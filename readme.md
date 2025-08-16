# Twitter Scraper (Apify + Flask)

## What it does
- Scrapes tweets via **apidojo/tweet-scraper** on Apify
- Stores to the app DB
- Optional ChatGPT summaries

## Quick start (Windows / PowerShell)
```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt

Copy-Item .env.example .env
# edit .env and set APIFY_TOKEN at minimum

python scrape_twitter.py
# optional: run the web app
python app.py
