import os
import logging
from datetime import datetime
from dateutil import parser
from urllib.parse import urlparse

import pandas as pd
import requests
from apify_client import ApifyClient

from app import create_app
from models import User, TwitterProfile, ScrapedTweet
from extensions import db

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CHATGPT_MODEL = "gpt-4-turbo"

APIFY_TOKEN = os.getenv("APIFY_TOKEN")
if not APIFY_TOKEN:
    raise ValueError("Please set APIFY_TOKEN in your environment.")

# Initialize the Apify client with your token.
client = ApifyClient(APIFY_TOKEN)
DEFAULT_MAX_ITEMS = 250

logging.basicConfig(
    filename='scrape_twitter.log',
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)

def call_chatgpt(prompt):
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": CHATGPT_MODEL,
        "messages": [
            {"role": "system", "content": "You are ChatGPT-4. You are a helpful assistant."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        data = response.json()
        logging.info("OpenAI response: %s", data)
        if "choices" in data and len(data["choices"]) > 0:
            return data["choices"][0]["message"]["content"].strip()
        else:
            return "No response from ChatGPT."
    except Exception as e:
        logging.error(f"Error calling ChatGPT: {e}")
        return f"Error calling ChatGPT: {e}"

def parse_chatgpt_response(response_text):
    if "Post Title:" in response_text:
        parts = response_text.split("Post Title:")
        summary = parts[0].strip()
        title = parts[1].strip()
        return title, summary
    else:
        return "Untitled", response_text

def scrape_and_store_tweets_for_user(user):
    logging.info(f"Scraping tweets for user {user.id} with email {user.email}")

    # Build a list of Twitter handles from the user's profiles.
    handles = [p.twitter_handle for p in user.twitter_profiles if p.twitter_handle]
    if not handles:
        logging.info(f"No Twitter handles for user {user.id}, skipping.")
        return

    # Build search queries using the "from:" operator.
    search_terms = []
    for h in handles:
        if h.startswith("http"):
            parsed = urlparse(h)
            username = parsed.path.strip("/")
            if username:
                search_terms.append(f"from:{username}")
        else:
            username = h.lstrip("@")
            if username:
                search_terms.append(f"from:{username}")
    logging.info(f"Using search terms: {search_terms}")

    # Build the run input for the actor.
    run_input = {
        "searchTerms": search_terms,
        "sort": "Latest",
        "maxItems": DEFAULT_MAX_ITEMS,
        "tweetLanguage": user.preferred_language or "en"
    }
    try:
        # Call the new actor.
        run = client.actor("apidojo/twitter-scraper-lite").call(run_input=run_input)
        logging.info(f"Apify run initiated, run id: {run.get('id')}")
    except Exception as e:
        logging.error(f"Error calling Apify: {e}")
        return

    dataset_id = run.get("defaultDatasetId")
    if not dataset_id:
        logging.error("No defaultDatasetId returned from Apify run")
        return

    dataset_client = client.dataset(dataset_id)
    items = list(dataset_client.iterate_items())
    logging.info(f"Scraped {len(items)} tweets for user {user.id}.")

    if not items:
        logging.info(f"No items returned for user {user.id}.")
        return

    # Get today's date.
    today_date = datetime.now().date()

    new_items = []
    for t in items:
        tweet_id = str(t.get('id', ''))
        created_at = t.get('createdAt')
        if tweet_id and created_at:
            try:
                dt = parser.parse(created_at).date()
            except Exception as e:
                logging.error(f"Error parsing date {created_at}: {e}")
                continue
            # Only process tweets from today.
            if dt != today_date:
                continue
            # Only add tweets that are not already in the database.
            existing = ScrapedTweet.query.filter_by(user_id=user.id, tweet_id=tweet_id).first()
            if not existing:
                new_items.append(t)
    logging.info(f"Found {len(new_items)} new items for user {user.id} from today.")

    if not new_items:
        logging.info(f"No new tweets for user {user.id} from today.")
        return

    df = pd.DataFrame(new_items)
    if 'author' in df.columns:
        df['author_name'] = df['author'].apply(lambda x: x.get('name') if isinstance(x, dict) else None)
        df['author_username'] = df['author'].apply(lambda x: x.get('username') if isinstance(x, dict) else None)
    if 'entities' in df.columns:
        def extract_photo(entities_obj):
            if isinstance(entities_obj, dict) and 'media' in entities_obj and len(entities_obj['media']) > 0:
                return entities_obj['media'][0].get('media_url_https', "")
            return ""
        df['photo_url'] = df['entities'].apply(extract_photo)
    else:
        df['photo_url'] = ""

    for _, row in df.iterrows():
        st = ScrapedTweet(
            tweet_id=str(row.get('id', '')),
            user_id=user.id,
            text=row.get('text', ''),
            full_text=row.get('fullText', ''),
            lang=row.get('lang', ''),
            retweet_count=row.get('retweetCount', 0),
            reply_count=row.get('replyCount', 0),
            like_count=row.get('likeCount', 0),
            quote_count=row.get('quoteCount', 0),
            created_at=parser.parse(row['createdAt']) if row.get('createdAt') else None,
            author_name=row.get('author_name'),
            author_username=row.get('author_username'),
            photo_url=row.get('photo_url')
        )
        db.session.add(st)
    db.session.commit()
    logging.info(f"Inserted {len(new_items)} new tweets for user {user.id} into DB.")

    language_map = {
        "af": "Afrikaans",
        "sq": "Albanian",
        "am": "Amharic",
        "ar": "Arabic",
        "hy": "Armenian",
        "az": "Azerbaijani",
        "eu": "Basque",
        "be": "Belarusian",
        "bn": "Bengali",
        "bs": "Bosnian",
        "bg": "Bulgarian",
        "ca": "Catalan",
        "ceb": "Cebuano",
        "ny": "Chichewa",
        "zh-CN": "Chinese (Simplified)",
        "zh-TW": "Chinese (Traditional)",
        "co": "Corsican",
        "hr": "Croatian",
        "cs": "Czech",
        "da": "Danish",
        "nl": "Dutch",
        "en": "English",
        "eo": "Esperanto",
        "et": "Estonian",
        "tl": "Filipino",
        "fi": "Finnish",
        "fr": "French",
        "fy": "Frisian",
        "gl": "Galician",
        "ka": "Georgian",
        "de": "German",
        "el": "Greek",
        "gu": "Gujarati",
        "ht": "Haitian Creole",
        "ha": "Hausa",
        "haw": "Hawaiian",
        "he": "Hebrew",
        "hi": "Hindi",
        "hmn": "Hmong",
        "hu": "Hungarian",
        "is": "Icelandic",
        "ig": "Igbo",
        "id": "Indonesian",
        "ga": "Irish",
        "it": "Italian",
        "ja": "Japanese",
        "jw": "Javanese",
        "kn": "Kannada",
        "kk": "Kazakh",
        "km": "Khmer",
        "rw": "Kinyarwanda",
        "ko": "Korean",
        "ku": "Kurdish (Kurmanji)",
        "ky": "Kyrgyz",
        "lo": "Lao",
        "la": "Latin",
        "lv": "Latvian",
        "lt": "Lithuanian",
        "lb": "Luxembourgish",
        "mk": "Macedonian",
        "mg": "Malagasy",
        "ms": "Malay",
        "ml": "Malayalam",
        "mt": "Maltese",
        "mi": "Maori",
        "mr": "Marathi",
        "mn": "Mongolian",
        "my": "Myanmar (Burmese)",
        "ne": "Nepali",
        "no": "Norwegian",
        "ps": "Pashto",
        "fa": "Persian",
        "pl": "Polish",
        "pt": "Portuguese",
        "pa": "Punjabi",
        "ro": "Romanian",
        "ru": "Russian",
        "sm": "Samoan",
        "gd": "Scots Gaelic",
        "sr": "Serbian",
        "st": "Sesotho",
        "sn": "Shona",
        "sd": "Sindhi",
        "si": "Sinhala",
        "sk": "Slovak",
        "sl": "Slovenian",
        "so": "Somali",
        "es": "Spanish",
        "su": "Sundanese",
        "sw": "Swahili",
        "sv": "Swedish",
        "tg": "Tajik",
        "ta": "Tamil",
        "te": "Telugu",
        "th": "Thai",
        "tr": "Turkish",
        "uk": "Ukrainian",
        "ur": "Urdu",
        "uz": "Uzbek",
        "vi": "Vietnamese",
        "cy": "Welsh",
        "xh": "Xhosa",
        "yi": "Yiddish",
        "yo": "Yoruba",
        "zu": "Zulu"
    }
    
    user_lang_code = user.preferred_language or "en"
    lang_name = language_map.get(user_lang_code, "English")
    
    for item in new_items:
        st = ScrapedTweet.query.filter_by(user_id=user.id, tweet_id=item['id']).first()
        if st:
            author_name = st.author_name or "Unknown"
            tweet_text = st.text or ""
            prompt = (
                "You are ChatGPT-4. Below is a tweet in its original language. "
                "Please translate and adjust it so that it is readable in {language}. "
                "Start by saying: 'In the latest tweet from ({author_name})...'. "
                "Then provide a summary in two paragraphs or less, explaining the context or importance of the tweet, and finally repeat the original tweet as is. Please do it in the {language} selected. "
                "At the end, on a new line, output the short title in the format: 'Post Title: [Title]'.\n\n"
                "Original Tweet: {tweet_text}"
            ).format(language=lang_name, author_name=author_name, tweet_text=tweet_text)
            response = call_chatgpt(prompt)
            title, summary = parse_chatgpt_response(response)
            st.chatgpt_output = summary
            st.chatgpt_title = title
    db.session.commit()
    logging.info(f"ChatGPT summarized new tweets for user {user.id}.")

def main():
    app = create_app()
    with app.app_context():
        from models import User
        users = User.query.all()
        logging.info(f"Found {len(users)} users in DB.")
        for user in users:
            scrape_and_store_tweets_for_user(user)

if __name__ == "__main__":
    main()
