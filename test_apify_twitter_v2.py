#!/usr/bin/env python3
"""
Test runner for Apify actor 61RPP7dywgiy0JPD0 (Tweet Scraper V2 / X).
No DB/OpenAI; prints what the actor returns so you can verify results.

Examples (PowerShell):
  $env:APIFY_TOKEN = "apify_api_XXXXXXXXXXXXXXXXXXXX"
  python test_apify_twitter_v2.py --mode search --handles elonmusk --days 14 --max-items 50 --show 5
  python test_apify_twitter_v2.py --mode profile --handles elonmusk --max-items 50 --show 5
  python test_apify_twitter_v2.py --mode search --handles heidelbergboi93 --start 2025-07-01 --until 2025-07-28 --show 5
"""

import os
import sys
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone, date
from typing import List, Optional, Tuple, Dict, Any

# ----- optional timezone pretty-print -----
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
    LOCAL_TZ = ZoneInfo("Europe/Belgrade")
except Exception:
    ZoneInfo = None
    LOCAL_TZ = None

# ----- parsing helpers -----
def parse_dt_any_to_utc(s: str) -> Optional[datetime]:
    """Parse many common timestamp shapes to an aware UTC datetime."""
    if not s:
        return None
    # 1) ISO with Z/offset
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        pass
    # 2) dateutil covers strings like "Fri Nov 24 17:49:36 +0000 2023"
    try:
        from dateutil import parser as duparser  # pip install python-dateutil
        dt = duparser.parse(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        pass
    # 3) fallback formats
    for fmt in (
        "%a %b %d %H:%M:%S %z %Y",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt
        except Exception:
            continue
    return None

def normalize_handle(h: str) -> Optional[str]:
    """Turn '@user' or 'https://twitter.com/user' into 'user'."""
    if not h:
        return None
    h = h.strip()
    if h.startswith("http"):
        try:
            path = h.split("://", 1)[-1].split("/", 1)[-1]
            username = path.split("/", 1)[0]
            return username or None
        except Exception:
            return None
    return h.lstrip("@")

def choose_dates(days: int, start: Optional[str], until: Optional[str]) -> Tuple[date, date]:
    """Resolve [start, until) where 'until' is exclusive."""
    if start and until:
        s = date.fromisoformat(start)
        u = date.fromisoformat(until)
        if not (s < u):
            raise ValueError("require start < until")
        return s, u
    if days <= 0:
        raise ValueError("--days must be positive if --start/--until not provided")
    today = date.today()
    u = today + timedelta(days=1)
    s = today - timedelta(days=days - 1)
    return s, u

def slice_range(start: date, until: date, step_days: int) -> List[Tuple[date, date]]:
    out: List[Tuple[date, date]] = []
    cur = start
    while cur < until:
        nxt = min(cur + timedelta(days=step_days), until)
        out.append((cur, nxt))
        cur = nxt
    return out

def build_queries(handles: List[str], start_d: date, until_d: date, extra_q: str, slice_days: int) -> List[str]:
    """Build query terms per handle and per slice: 'from:user since:YYYY-MM-DD until:YYYY-MM-DD [extra_q]'."""
    qs: List[str] = []
    for s, u in slice_range(start_d, until_d, slice_days):
        for h in handles:
            u_clean = h.lstrip("@").split("/")[-1]
            q = f"from:{u_clean} since:{s.isoformat()} until:{u.isoformat()}"
            if extra_q:
                q = f"{q} {extra_q}"
            qs.append(q)
    return qs

def pick_timestamp_raw(item: Dict[str, Any]) -> str:
    """Try multiple fields where a timestamp might live."""
    return (
        item.get("createdAt")
        or item.get("created_at")
        or (item.get("legacy") or {}).get("created_at")
        or (item.get("tweet") or {}).get("createdAt")
        or (item.get("tweet") or {}).get("created_at")
        or ""
    )

def is_demo_item(item: Dict[str, Any]) -> bool:
    """Detect classic demo rows that actors return on free/demo plans."""
    keys = set(item.keys())
    return keys == {"demo"} or (keys == {"demo", "type"} and not any(k in item for k in ["id", "text", "createdAt", "created_at"]))

# ----- main -----
def main():
    try:
        from apify_client import ApifyClient
    except ImportError:
        print("ERROR: apify-client not installed. Run: pip install apify-client", file=sys.stderr)
        sys.exit(2)

    ap = argparse.ArgumentParser(description="Apify Twitter test (actor 61RPP7dywgiy0JPD0).")
    ap.add_argument("--mode", choices=["search", "profile"], default="search",
                    help="search: build queries via searchTerms; profile: use startUrls")
    ap.add_argument("--handles", nargs="*", required=True,
                    help="Twitter handles or profile URLs (e.g., elonmusk or https://twitter.com/elonmusk)")
    ap.add_argument("--days", type=int, default=7, help="If no --start/--until, last N days (default 7)")
    ap.add_argument("--start", help="YYYY-MM-DD inclusive start")
    ap.add_argument("--until", help="YYYY-MM-DD exclusive end")
    ap.add_argument("--slice-days", type=int, default=14, help="Slice size for search queries (default 14)")
    ap.add_argument("--q", default="", help="Extra tokens appended to search queries (e.g., 'filter:media')")
    ap.add_argument("--actor", default="61RPP7dywgiy0JPD0", help="Actor id (default: 61RPP7dywgiy0JPD0)")
    ap.add_argument("--max-items", type=int, default=100, help="Max items requested from actor (default 100)")
    ap.add_argument("--show", type=int, default=5, help="How many sample items to display")
    ap.add_argument("--inspect", action="store_true", help="Print raw timestamps & key stats")
    args = ap.parse_args()

    token = os.getenv("APIFY_TOKEN")
    if not token:
        print("ERROR: Set APIFY_TOKEN in your environment.", file=sys.stderr)
        sys.exit(2)

    # Normalize handles
    handles = [normalize_handle(h) for h in args.handles]
    handles = [h for h in handles if h]
    if not handles:
        print("ERROR: No valid handles.", file=sys.stderr)
        sys.exit(2)

    client = ApifyClient(token)

    run_input: Dict[str, Any] = {
        "sort": "Latest",
        "maxItems": args.max_items,
    }

    if args.mode == "search":
        start_d, until_d = choose_dates(args.days, args.start, args.until)
        search_terms = build_queries(handles, start_d, until_d, args.q.strip(), args.slice_days)
        run_input["searchTerms"] = search_terms
        print(f"[TEST] mode      : search")
        print(f"[TEST] handles   : {', '.join(handles)}")
        print(f"[TEST] range     : {start_d} -> {until_d} (exclusive)")
        print(f"[TEST] slices    : {args.slice_days} days")
        print(f"[TEST] queries   : {len(search_terms)}")
    else:
        start_urls = [f"https://twitter.com/{h}" for h in handles]
        run_input["startUrls"] = start_urls
        if args.start and args.until:
            run_input["start"] = args.start
            run_input["end"] = args.until
        print(f"[TEST] mode      : profile")
        print(f"[TEST] startUrls : {', '.join(start_urls)}")
        if "start" in run_input:
            print(f"[TEST] actor-dates: {run_input['start']} -> {run_input['end']}")

    print(f"[TEST] actor     : {args.actor}")
    print(f"[TEST] max_items : {args.max_items}")
    if args.q:
        print(f"[TEST] extra q   : {args.q}")

    # Call actor
    try:
        run = client.actor(args.actor).call(run_input=run_input)
    except Exception as e:
        print(f"ERROR: Failed to call actor: {e}", file=sys.stderr)
        sys.exit(1)

    dsid = run.get("defaultDatasetId")
    if not dsid:
        print("ERROR: Actor did not return defaultDatasetId.", file=sys.stderr)
        sys.exit(1)

    items = list(client.dataset(dsid).iterate_items())
    print(f"[TEST] fetched   : {len(items)} items (raw)")

    # Stats & demo detection
    types = Counter([it.get("type", "UNKNOWN") for it in items])
    has_created = sum(1 for it in items if pick_timestamp_raw(it))
    print(f"[TEST] types     : {dict(types)}")
    print(f"[TEST] has any timestamp field : {has_created}/{len(items)}")
    demo_count = sum(1 for it in items if is_demo_item(it))
    if demo_count:
        print(f"[TEST] demo rows : {demo_count}/{len(items)}  --> DEMO output (plan restriction).")

    # Inspect (first few)
    if args.inspect and items:
        print("\n[TEST] Inspecting first 3 raw items:")
        for idx, it in enumerate(items[:3], start=1):
            raw = pick_timestamp_raw(it)
            parsed = parse_dt_any_to_utc(raw) if raw else None
            keys_list = sorted(list(it.keys()))
            print(f"  Item #{idx}: type={it.get('type')} id={it.get('id')}")
            print(f"   keys: {keys_list}")
            # Avoid backslash-in-fstring by precomputing strings
            created_line = "   createdAt_raw=" + repr(raw) + " parsed_utc=" + (parsed.isoformat() if parsed else "None")
            print(created_line)

    # Show samples with safe printing (no backslash in f-string expressions)
    print("\n[TEST] Sample items:")
    shown = 0
    for it in items:
        if shown >= args.show:
            break

        raw = pick_timestamp_raw(it)
        dt_utc = parse_dt_any_to_utc(raw) if raw else None
        dt_local = dt_utc.astimezone(LOCAL_TZ) if (dt_utc and LOCAL_TZ) else dt_utc
        author = (
            (it.get("author") or {}).get("username")
            or (it.get("user") or {}).get("screen_name")
            or (it.get("author") or {}).get("userName")
        )
        text_raw = (it.get("fullText") or it.get("text") or "")
        text_one_line = text_raw.replace("\n", " ")
        text_preview = text_one_line[:240]
        url = it.get("url") or it.get("twitterUrl") or it.get("link") or ""

        if is_demo_item(it):
            print("— DEMO ROW — " + repr(it))
            shown += 1
            continue

        print("—")
        print(" type    : " + str(it.get("type")))
        print(" id      : " + str(it.get("id")))
        print(" created : raw=" + str(raw) +
              " | UTC=" + (dt_utc.isoformat() if dt_utc else "None") +
              " | LOCAL=" + (dt_local.isoformat() if dt_local else "None"))
        print(" author  : @" + str(author))
        print(" text    : " + text_preview)
        if url:
            print(" url     : " + url)

        shown += 1

    if shown == 0:
        print("(No printable items)")

if __name__ == "__main__":
    main()
