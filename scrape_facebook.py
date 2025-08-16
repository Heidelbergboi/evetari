# scrape_facebook.py
import os
import logging
from datetime import datetime
from dateutil import parser

import pandas as pd
import requests
from apify_client import ApifyClient

from app import create_app
from models import User, FacebookPage, ScrapedFBPost
from extensions import db

FACEBOOK_ACTOR_NAME = "apify/facebook-posts-scraper"
RESULTS_LIMIT = 3

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CHATGPT_MODEL = "gpt-4-turbo"

APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN")
if not APIFY_API_TOKEN:
    raise ValueError("Please set APIFY_API_TOKEN in your environment.")

client = ApifyClient(APIFY_API_TOKEN)

logging.basicConfig(
    filename='scrape_facebook.log',
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
        "temperature": 0.2
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
    try:
        parts = response_text.split("Title:")
        if len(parts) < 2:
            return "Untitled", response_text
        after_title = parts[1]
        title_line, rest = after_title.split("\n", 1)
        article_parts = rest.split("Original Post:")
        article_summary = article_parts[0].replace("Article:", "").strip()
        return title_line.strip(), article_summary
    except Exception as e:
        logging.error(f"Error parsing ChatGPT response: {e}")
        return "Untitled", response_text

def scrape_and_store_fb_posts_for_user(user):
    logging.info(f"Scraping Facebook posts for user {user.id} with email {user.email}")

    pages = [p.page_url for p in user.facebook_pages if p.page_url]
    if not pages:
        logging.info(f"No Facebook pages for user {user.id}, skipping Facebook scraping.")
        return

    start_urls = [{"url": page} for page in pages]
    logging.info(f"Using Facebook start URLs: {start_urls}")

    run_input = {
        "startUrls": start_urls,
        "resultsLimit": RESULTS_LIMIT,
        "proxy": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"]
        }
    }
    try:
        run = client.actor(FACEBOOK_ACTOR_NAME).call(run_input=run_input)
        logging.info(f"Facebook Apify run initiated, run id: {run.get('id')}")
    except Exception as e:
        logging.error(f"Error calling Facebook Apify: {e}")
        return

    dataset_id = run.get("defaultDatasetId")
    if not dataset_id:
        logging.error("No defaultDatasetId returned from Facebook Apify run")
        return

    dataset_client = client.dataset(dataset_id)
    items = list(dataset_client.iterate_items())
    logging.info(f"Scraped {len(items)} Facebook posts for user {user.id}.")

    if not items:
        logging.info(f"No Facebook posts returned for user {user.id}.")
        return

    today_date = datetime.utcnow().date()
    new_items = []
    for t in items:
        post_id = str(t.get('postId', t.get('id', '')))
        post_time_str = t.get("time", "")
        if post_id and post_time_str:
            try:
                dt = parser.parse(post_time_str).date()
            except Exception as e:
                logging.error(f"Error parsing Facebook post time '{post_time_str}': {e}")
                continue
            # Only store posts from today
            if dt != today_date:
                continue
            existing = ScrapedFBPost.query.filter_by(user_id=user.id, post_id=post_id).first()
            if not existing:
                new_items.append(t)
    logging.info(f"Found {len(new_items)} new Facebook posts for user {user.id} from today.")

    if not new_items:
        logging.info(f"No new Facebook posts from today for user {user.id}.")
        return

    processed_data = []
    for t in new_items:
        thumbnail = ""
        media = t.get("media", [])
        if isinstance(media, list) and len(media) > 0:
            thumbnail = media[0].get("thumbnail", "")
        page_name_val = t.get("pageName", "")
        if isinstance(page_name_val, dict):
            page_name_val = page_name_val.get("name", "")
        processed_data.append({
            "post_id": t.get("postId", t.get("id", "")),
            "page_name": page_name_val,
            "post_url": t.get("url", ""),
            "post_text": t.get("text", ""),
            "time_of_posting": t.get("time", ""),
            "number_of_likes": t.get("likes", 0),
            "number_of_comments": t.get("comments", 0),
            "number_of_shares": t.get("shares", 0),
            "first_post_picture": thumbnail,
            "profile_picture": t.get("user", {}).get("profilePic", ""),
            "posttitle": "",
            "chatgpt_output": ""
        })
    for data in processed_data:
        fb_post = ScrapedFBPost(
            post_id=data["post_id"],
            user_id=user.id,
            page_name=data["page_name"],
            post_url=data["post_url"],
            post_text=data["post_text"],
            time_of_posting=data["time_of_posting"],
            number_of_likes=data["number_of_likes"],
            number_of_comments=data["number_of_comments"],
            number_of_shares=data["number_of_shares"],
            first_post_picture=data["first_post_picture"],
            profile_picture=data["profile_picture"],
            posttitle=data["posttitle"],
            chatgpt_output=data["chatgpt_output"]
        )
        db.session.add(fb_post)
    db.session.commit()
    logging.info(f"Inserted {len(new_items)} new Facebook posts for user {user.id} into DB.")

    # Expanded language map based on the full list in your form
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

    # Convert user's code to a readable name using the updated map
    user_lang_code = user.preferred_language_facebook or "en"
    fb_lang = language_map.get(user_lang_code, "English")

    # Call ChatGPT for each new FB post using the selected language in the prompt
    for data in processed_data:
        fb_post = ScrapedFBPost.query.filter_by(user_id=user.id, post_id=data["post_id"]).first()
        if fb_post:
            page_name = fb_post.page_name if fb_post.page_name else (user.name if user.name else user.email)
            prompt = (
                f'You are ChatGPT-4. Below is a Facebook post in its original language.\n\n'
                f'Requirements:\n'
                f'1) Begin the response with: "Latest Facebook post from \\"{page_name}\\""\n'
                f'2) Create an expanded article in {fb_lang} with a short title and a summary consisting of 3-5 sentences.\n'
                f'3) The title must include the Facebook page name (e.g., "{page_name}: [topic]").\n'
                f'4) Under the header "Article:", summarize the main content of the post including key details.\n'
                f'5) Use a formal and informative tone that emphasizes the significance or context of the post.\n'
                f'6) Finally, add a section "Original Post:" and include the full original post enclosed in quotes.\n\n'
                f'Format your response exactly as follows:\n\n'
                f'Latest Facebook post from "{page_name}"\n\n'
                f'Title: [Your generated title]\n\n'
                f'Article:\n[Your 3-5 sentence summary]\n\n'
                f'Original Post:\n"{fb_post.post_text or ""}"'
            )
            response = call_chatgpt(prompt)
            title, summary = parse_chatgpt_response(response)
            fb_post.chatgpt_output = summary
            fb_post.posttitle = title
    db.session.commit()
    logging.info(f"ChatGPT summarized new Facebook posts for user {user.id}.")

def main():
    app = create_app()
    with app.app_context():
        from models import User
        users = User.query.all()
        logging.info(f"Found {len(users)} users in DB.")
        for user in users:
            scrape_and_store_fb_posts_for_user(user)

if __name__ == "__main__":
    main()
