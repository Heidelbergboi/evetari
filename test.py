#!/usr/bin/env python3
"""
Quick Apify test for Twitter scraping (no DB, no OpenAI).

Usage (PowerShell):
  # 1) Set your Apify token for this shell
  $env:APIFY_TOKEN = "apify_api_XXXXXXXXXXXXXXXXXXXX"

  # 2) Run a dry test on two handles for the last 48 hours (prints a few items)
  python test_apify_twitter.py --handles heidelbergboi93 realDonaldTrump --since-hours 48 --max-items 100 --show 5

If you want to use profile URLs, that's fine too:
  python test_apify_twitter.py --handles https://twitter.com/realDonaldTrump

Install dependency if needed:
  pip install apify-client
"""
import os
import sys
import argparse
from typing import List, Optional
from datetime import datetime, timedelta, timezone

# Optional local timezone pretty-print (Python 3.9+)
try:
    from zoneinfo import ZoneInfo  # type: ignore
    LOCAL_TZ = ZoneInfo("Europe/Belgrade")
except Exception:
    ZoneInfo = None
    LOCAL_TZ = None

try:
    from apify_client import ApifyClient
except ImportError:
    print("ERROR: apify-client is not installed. Run: pip install apify-client", file=sys.stderr)
    sys.exit(2)

def parse_iso_utc(s: str) -> Optional[datetime]:
    """Parse ISO datetime like '2025-07-27T08:52:06.123Z' into an aware UTC datetime."""
    if not s:
        return None
    try:
        # handle trailing Z
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        # fallback: drop fractional seconds, assume UTC
        try:
            return datetime.strptime(s.split(".")[0], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            return None

def normalize_handle(h: str) -> Optional[str]:
    """Turn '@user' or 'https://twitter.com/user' into 'user'."""
    if not h:
        return None
    h = h.strip()
    if h.startswith("http"):
        # URL form
        try:
            # e.g., https://twitter.com/username or /username/status/...
            path = h.split("://", 1)[-1].split("/", 1)[-1]
            username = path.split("/", 1)[0]
            return username or None
        except Exception:
            return None
    # @username or username
    return h.lstrip("@")

def run_apify_test(handles: List[str], since_hours: int, max_items: int, lang: str, show: int, actor_id: str) -> int:
    token = os.getenv("APIFY_TOKEN")
    if not token:
        print("ERROR: Set APIFY_TOKEN in your environment.", file=sys.stderr)
        return 2

    client = ApifyClient(token)

    normalized = [normalize_handle(h) for h in handles]
    normalized = [n for n in normalized if n]
    if not normalized:
        print("ERROR: No valid handles provided.", file=sys.stderr)
        return 2

    search_terms = [f"from:{n}" for n in normalized]
    print(f"[TEST] APIFY_TOKEN OK")
    print(f"[TEST] actor     : {actor_id}")
    print(f"[TEST] handles   : {', '.join(normalized)}")
    print(f"[TEST] search    : {search_terms}")
    print(f"[TEST] max_items : {max_items}")
    print(f"[TEST] window    : last {since_hours}h")
    print(f"[TEST] language  : {lang}")

    run_input = {
        "searchTerms": search_terms,
        "sort": "Latest",
        "maxItems": max_items,
        "tweetLanguage": lang,
    }

    # Run the actor and wait for finish
    try:
        run = client.actor(actor_id).call(run_input=run_input)
    except Exception as e:
        print(f"ERROR: Failed to call actor: {e}", file=sys.stderr)
        return 1

    dataset_id = run.get("defaultDatasetId")
    if not dataset_id:
        print("ERROR: Actor did not return defaultDatasetId.", file=sys.stderr)
        return 1

    items = list(client.dataset(dataset_id).iterate_items())
    print(f"[TEST] fetched   : {len(items)} items (before filtering)")

    # Filter: last N hours (UTC)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    kept = []
    for it in items:
        dt = parse_iso_utc(it.get("createdAt") or it.get("created_at") or "")
        if not dt:
            continue
        if dt >= cutoff:
            kept.append((dt, it))

    print(f"[TEST] kept      : {len(kept)} items (createdAt >= {cutoff.isoformat()})")

    if not kept:
        print("[TEST] Nothing in the selected time window. Try a larger --since-hours (e.g., 72 or 168).")
        return 0

    # Show a few examples
    print(f"\n[TEST] Showing up to {show} items:\n")
    for i, (dt_utc, it) in enumerate(kept[:show], start=1):
        author = (it.get("author") or {}).get("username") or (it.get("user") or {}).get("screen_name")
        text = (it.get("fullText") or it.get("text") or "").replace("\n", " ")
        dt_local = dt_utc.astimezone(LOCAL_TZ) if LOCAL_TZ else dt_utc
        url = it.get("url") or it.get("link") or ""
        print(f"#{i}")
        print(f" author  : @{author}")
        print(f" created : UTC {dt_utc.isoformat()} | LOCAL {dt_local.isoformat()}")
        print(f" text    : {text[:240]}")
        if url:
            print(f" url     : {url}")
        print("—")

    return 0

def main():
    parser = argparse.ArgumentParser(description="Quick Apify Twitter scrape test (no DB/OpenAI).")
    parser.add_argument("--handles", nargs="*", required=True,
                        help="Twitter handles or profile URLs (e.g., heidelbergboi93 realDonaldTrump or https://twitter.com/realDonaldTrump)")
    parser.add_argument("--since-hours", type=int, default=24,
                        help="Keep tweets from the last N hours (default: 24)")
    parser.add_argument("--max-items", type=int, default=100,
                        help="Max items to request from actor (default: 100)")
    parser.add_argument("--lang", default="en",
                        help="Tweet language filter for actor (default: en)")
    parser.add_argument("--show", type=int, default=5,
                        help="Print up to this many items (default: 5)")
    parser.add_argument("--actor", default="apidojo/twitter-scraper-lite",
                        help="Actor ID to use (default: apidojo/twitter-scraper-lite)")
    args = parser.parse_args()

    rc = run_apify_test(
        handles=args.handles,
        since_hours=args.since_hours,
        max_items=args.max_items,
        lang=args.lang,
        show=args.show,
        actor_id=args.actor
    )
    sys.exit(rc)

if __name__ == "__main__":
    main()
