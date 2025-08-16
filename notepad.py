# test_epctex_tweets.py
import os, sys, argparse
from datetime import date, timedelta, datetime, timezone

try:
    from apify_client import ApifyClient
except ImportError:
    print("Install dependency first: pip install apify-client", file=sys.stderr)
    sys.exit(2)

def parse_ts(it):
    raw = (it.get("createdAt")
           or it.get("created_at")
           or (it.get("legacy") or {}).get("created_at")
           or (it.get("tweet") or {}).get("created_at")
           or "")
    # try ISO/offset/Z
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        pass
    # try dateutil if available (covers 'Fri Nov 24 17:49:36 +0000 2023')
    try:
        from dateutil import parser
        dt = parser.parse(raw)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def main():
    ap = argparse.ArgumentParser(description="Test Twitter scraping via epctex/twitter-search-scraper (no DB).")
    ap.add_argument("--handle", default="elonmusk", help="Twitter handle (default: elonmusk)")
    ap.add_argument("--days", type=int, default=14, help="Days back from today (default: 14)")
    ap.add_argument("--max", type=int, default=50, help="Max items (default: 50)")
    args = ap.parse_args()

    token = os.getenv("APIFY_TOKEN")
    if not token:
        print("ERROR: set APIFY_TOKEN in your environment.", file=sys.stderr)
        sys.exit(2)

    end_d = date.today() + timedelta(days=1)   # until is exclusive
    start_d = end_d - timedelta(days=args.days)

    query = f"from:{args.handle} since:{start_d.isoformat()} until:{end_d.isoformat()}"
    print(f"[TEST] actor   : epctex/twitter-search-scraper")
    print(f"[TEST] query   : {query}")
    print(f"[TEST] max     : {args.max}")

    client = ApifyClient(token)
    run_input = {"searchTerms": [query], "sort": "Latest", "maxItems": args.max}

    try:
        run = client.actor("epctex/twitter-search-scraper").call(run_input=run_input)
    except Exception as e:
        print(f"ERROR calling actor: {e}", file=sys.stderr)
        sys.exit(1)

    dsid = run.get("defaultDatasetId")
    if not dsid:
        print("ERROR: no defaultDatasetId from actor.", file=sys.stderr)
        sys.exit(1)

    items = list(client.dataset(dsid).iterate_items())
    print(f"[TEST] fetched : {len(items)} items")

    printed = 0
    for it in items:
        dt = parse_ts(it)
        txt = (it.get("fullText") or it.get("text") or "").replace("\n", " ")
        url = it.get("url") or it.get("twitterUrl") or it.get("link") or ""
        if dt is None and not txt and not url:
            # Probably a demo/placeholder row
            continue
        print("—")
        print(f"id   : {it.get('id')}")
        print(f"time : {dt.isoformat() if dt else 'n/a'}")
        print(f"text : {txt[:240]}")
        if url:
            print(f"url  : {url}")
        printed += 1

    if printed == 0:
        print("No printable tweet items returned. If you used a different actor (apidojo), "
              "that may be in Demo mode on free plans.")

if __name__ == "__main__":
    main()
