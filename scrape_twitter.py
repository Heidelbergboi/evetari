# scrape_twitter.py
import os
import logging
from datetime import datetime, date, timedelta, timezone
from dateutil import parser as dtparser
from urllib.parse import urlparse

import requests
from apify_client import ApifyClient

from app import create_app
from models import User, ScrapedTweet
from extensions import db

# ------------------ Logging ------------------
logger = logging.getLogger(__name__)
logging.basicConfig(
    filename="scrape_twitter.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)

# Quiet down super-verbose libraries if you want:
logging.getLogger("flask_dance.consumer.oauth2").setLevel(logging.WARNING)
logging.getLogger("requests_oauthlib.oauth2_session").setLevel(logging.WARNING)

# ------------------ Config ------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CHATGPT_MODEL = os.getenv("CHATGPT_MODEL", "gpt-4-turbo")

APIFY_TOKEN = os.getenv("APIFY_TOKEN")
if not APIFY_TOKEN:
    raise ValueError("Please set APIFY_TOKEN in your environment.")

# Default to apidojo actor as requested
ACTOR_ID = os.getenv("APIFY_TWITTER_ACTOR", "apidojo/tweet-scraper")

SINCE_DAYS = int(os.getenv("APIFY_SINCE_DAYS", "7"))
DEFAULT_MAX_ITEMS = int(os.getenv("APIFY_MAX_ITEMS", "250"))
EXTRA_QUERY = os.getenv("APIFY_EXTRA_QUERY", "").strip()

# If set to "1", use searchTerms instead of twitterHandles
USE_SEARCH_TERMS = os.getenv("APIFY_USE_SEARCH_TERMS", "0") == "1"

# Init Apify client
client = ApifyClient(APIFY_TOKEN)

# ------------------ Helpers ------------------
def _normalize_handle(h: str) -> str:
    if not h:
        return ""
    h = h.strip()
    if h.startswith("http"):
        path = urlparse(h).path.strip("/")
        return (path.split("/", 1)[0] if path else "").lstrip("@")
    return h.lstrip("@")

def _build_search_terms(handles, start_d: date, until_d: date):
    terms = []
    for h in handles:
        u = _normalize_handle(h)
        if not u:
            continue
        q = f"from:{u} since:{start_d.isoformat()} until:{until_d.isoformat()}"
        if EXTRA_QUERY:
            q = f"{q} {EXTRA_QUERY}"
        terms.append(q)
    return terms

def _pick_timestamp_raw(item: dict) -> str:
    return (
        item.get("createdAt")
        or item.get("created_at")
        or (item.get("legacy") or {}).get("created_at")
        or (item.get("tweet") or {}).get("createdAt")
        or (item.get("tweet") or {}).get("created_at")
        or ""
    )

def _parse_timestamp(s: str):
    if not s:
        return None
    try:
        # ISO with 'Z'
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        pass
    try:
        dt = dtparser.parse(s)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def _extract_author(item: dict):
    author = item.get("author") or {}
    name = author.get("name") or ""
    username = author.get("username") or author.get("userName") or ""
    if not username and item.get("user"):
        username = item["user"].get("screen_name") or ""
        name = name or item["user"].get("name") or ""
    return name, username

def _extract_text(item: dict):
    return (item.get("fullText") or item.get("text") or "").strip()

def _extract_photo_url(item: dict):
    ent = item.get("entities") or {}
    media = ent.get("media") or []
    if isinstance(media, list) and media:
        m = media[0]
        return m.get("media_url_https") or m.get("media_url") or ""

    ext = item.get("extendedEntities") or item.get("extended_entities") or {}
    media2 = ext.get("media") or []
    if isinstance(media2, list) and media2:
        m = media2[0]
        return m.get("media_url_https") or m.get("media_url") or ""

    return ""

def _is_demo_item(item: dict) -> bool:
    keys = set(item.keys())
    return keys == {"demo"} or (keys == {"demo", "type"} and not any(k in item for k in ["id", "text", "fullText"]))

# ------------------ ChatGPT helpers ------------------
def call_chatgpt(prompt: str) -> str:
    if not OPENAI_API_KEY:
        return "OpenAI API key not set."

    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": CHATGPT_MODEL,
        "messages": [
            {"role": "system", "content": "You are ChatGPT-4. You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        data = resp.json()
        logger.info("OpenAI response: %s", data)
        if "choices" in data and data["choices"]:
            return data["choices"][0]["message"]["content"].strip()
        return "No response from ChatGPT."
    except Exception as e:
        logger.error("Error calling ChatGPT: %s", e)
        return f"Error calling ChatGPT: {e}"

def parse_chatgpt_response(response_text):
    if "Post Title:" in response_text:
        parts = response_text.split("Post Title:")
        summary = parts[0].strip()
        title = parts[1].strip()
        return title, summary
    else:
        return "Untitled", response_text

# ------------------ Main scraper ------------------
def scrape_and_store_tweets_for_user(user):
    logger.info("Scraping tweets for user %s with email %s", user.id, user.email)

    handles = [p.twitter_handle for p in user.twitter_profiles if p.twitter_handle]
    handles = [h for h in handles if _normalize_handle(h)]
    if not handles:
        logger.info("No Twitter handles for user %s, skipping.", user.id)
        return

    until_d = date.today() + timedelta(days=1)    # exclusive
    start_d = until_d - timedelta(days=SINCE_DAYS)

    # Build actor input
    if USE_SEARCH_TERMS:
        search_terms = _build_search_terms(handles, start_d, until_d)
        run_input = {
            "searchTerms": search_terms,
            "sort": "Latest",
            "maxItems": DEFAULT_MAX_ITEMS,
        }
    else:
        run_input = {
            "twitterHandles": [_normalize_handle(h) for h in handles],
            "start": start_d.isoformat(),
            "end": until_d.isoformat(),
            "maxItems": DEFAULT_MAX_ITEMS,
        }

    logger.info("Actor=%s input=%s", ACTOR_ID, run_input)

    try:
        run = client.actor(ACTOR_ID).call(run_input=run_input)
        logger.info("Apify run initiated, run id: %s", run.get("id"))
    except Exception as e:
        logger.error("Error calling Apify: %s", e)
        return

    dataset_id = run.get("defaultDatasetId")
    if not dataset_id:
        logger.error("No defaultDatasetId returned from Apify run")
        return

    items = list(client.dataset(dataset_id).iterate_items())
    logger.info("Fetched %d items from Apify for user %s.", len(items), user.id)

    if not items:
        logger.info("No items returned for user %s.", user.id)
        return

    start_utc = datetime.combine(start_d, datetime.min.time(), tzinfo=timezone.utc)
    until_utc = datetime.combine(until_d, datetime.min.time(), tzinfo=timezone.utc)

    # Stats
    demo = missing = out_of_window = non_tweet = duplicates = 0

    new_items = []
    for t in items:
        if _is_demo_item(t):
            demo += 1
            continue

        if t.get("type") and t.get("type") != "tweet":
            non_tweet += 1
            continue

        tweet_id = str(t.get("id", "") or "")
        raw_ts = _pick_timestamp_raw(t)
        dt_aware = _parse_timestamp(raw_ts) if raw_ts else None

        if not tweet_id or not dt_aware:
            missing += 1
            continue

        if not (start_utc <= dt_aware < until_utc):
            out_of_window += 1
            continue

        exists = ScrapedTweet.query.filter_by(user_id=user.id, tweet_id=tweet_id).first()
        if exists:
            duplicates += 1
            continue

        author_name, author_username = _extract_author(t)
        text = _extract_text(t)
        photo_url = _extract_photo_url(t)
        lang = t.get("lang", "")

        st = ScrapedTweet(
            tweet_id=tweet_id,
            user_id=user.id,
            text=text,
            full_text=t.get("fullText") or t.get("text") or "",
            lang=lang,
            retweet_count=t.get("retweetCount", 0),
            reply_count=t.get("replyCount", 0),
            like_count=t.get("likeCount", 0),
            quote_count=t.get("quoteCount", 0),
            created_at=dt_aware,
            author_name=author_name,
            author_username=author_username,
            photo_url=photo_url,
        )
        db.session.add(st)
        new_items.append(st)

    if not new_items:
        logger.info(
            "No new tweets to insert for user %s in window [%s .. %s). (demo=%d, missing=%d, out_of_window=%d, non_tweet=%d)",
            user.id, start_d, until_d, demo, missing, out_of_window, non_tweet,
        )
        return

    db.session.commit()
    logger.info("Inserted %d new tweets for user %s into DB.", len(new_items), user.id)

    # -------- Optional summaries via ChatGPT --------
    if not OPENAI_API_KEY:
        logger.info("OPENAI_API_KEY not set; skipping ChatGPT summaries.")
        return

    language_map = {
        "af": "Afrikaans","sq":"Albanian","am":"Amharic","ar":"Arabic","hy":"Armenian","az":"Azerbaijani",
        "eu":"Basque","be":"Belarusian","bn":"Bengali","bs":"Bosnian","bg":"Bulgarian","ca":"Catalan",
        "ceb":"Cebuano","ny":"Chichewa","zh-CN":"Chinese (Simplified)","zh-TW":"Chinese (Traditional)",
        "co":"Corsican","hr":"Croatian","cs":"Czech","da":"Danish","nl":"Dutch","en":"English","eo":"Esperanto",
        "et":"Estonian","tl":"Filipino","fi":"Finnish","fr":"French","fy":"Frisian","gl":"Galician","ka":"Georgian",
        "de":"German","el":"Greek","gu":"Gujarati","ht":"Haitian Creole","ha":"Hausa","haw":"Hawaiian","he":"Hebrew",
        "hi":"Hindi","hmn":"Hmong","hu":"Hungarian","is":"Icelandic","ig":"Igbo","id":"Indonesian","ga":"Irish",
        "it":"Italian","ja":"Japanese","jw":"Javanese","kn":"Kannada","kk":"Kazakh","km":"Khmer","rw":"Kinyarwanda",
        "ko":"Korean","ku":"Kurdish (Kurmanji)","ky":"Kyrgyz","lo":"Lao","la":"Latin","lv":"Latvian",
        "lt":"Lithuanian","lb":"Luxembourgish","mk":"Macedonian","mg":"Malagasy","ms":"Malay","ml":"Malayalam",
        "mt":"Maltese","mi":"Maori","mr":"Marathi","mn":"Mongolian","my":"Myanmar (Burmese)","ne":"Nepali",
        "no":"Norwegian","ps":"Pashto","fa":"Persian","pl":"Polish","pt":"Portuguese","pa":"Punjabi","ro":"Romanian",
        "ru":"Russian","sm":"Samoan","gd":"Scots Gaelic","sr":"Serbian","st":"Sesotho","sn":"Shona","sd":"Sindhi",
        "si":"Sinhala","sk":"Slovak","sl":"Slovenian","so":"Somali","es":"Spanish","su":"Sundanese","sw":"Swahili",
        "sv":"Swedish","tg":"Tajik","ta":"Tamil","te":"Telugu","th":"Thai","tr":"Turkish","uk":"Ukrainian",
        "ur":"Urdu","uz":"Uzbek","vi":"Vietnamese","cy":"Welsh","xh":"Xhosa","yi":"Yiddish","yo":"Yoruba","zu":"Zulu",
    }

    user_lang_code = user.preferred_language or "en"
    lang_name = language_map.get(user_lang_code, "English")

    summarized = 0
    for st in new_items:
        author_name = st.author_name or "Unknown"
        tweet_text = st.text or st.full_text or ""
        prompt = (
            "You are ChatGPT-4. Below is a tweet in its original language. "
            f"Please translate and adjust it so that it is readable in {lang_name}. "
            f"Start by saying: 'In the latest tweet from ({author_name})...'. "
            "Then provide a summary in two paragraphs or less, explaining the context or importance of the tweet, "
            f"and finally repeat the original tweet as is. Please do it in {lang_name}. "
            "At the end, on a new line, output the short title in the format: 'Post Title: [Title]'.\n\n"
            f"Original Tweet: {tweet_text}"
        )
        response = call_chatgpt(prompt)
        title, summary = parse_chatgpt_response(response)
        if title and summary:
            st.chatgpt_output = summary
            st.chatgpt_title = title
            summarized += 1

    db.session.commit()
    logger.info("ChatGPT summarized %d/%d tweets for user %s.", summarized, len(new_items), user.id)

def main():
    logger.info(
        "Starting Twitter scrape with actor=%s, since_days=%s, max_items=%s",
        ACTOR_ID, SINCE_DAYS, DEFAULT_MAX_ITEMS
    )
    app = create_app()
    with app.app_context():
        users = User.query.all()
        logger.info("Found %d users in DB.", len(users))
        for user in users:
            scrape_and_store_tweets_for_user(user)

if __name__ == "__main__":
    main()
