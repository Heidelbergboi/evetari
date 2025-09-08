import os
import mimetypes
import logging
from urllib.parse import urlparse
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

from flask import Flask, render_template, redirect, url_for, session, flash, request, jsonify, current_app
from config import Config
from flask_migrate import Migrate
from flask_dance.contrib.google import make_google_blueprint, google
from functools import wraps
from extensions import db
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import requests
# from flask_apscheduler import APScheduler  <-- Scheduler import not needed if not used

logging.basicConfig(level=logging.DEBUG)

migrate = Migrate()

# Background executor to run scraper jobs without blocking web requests
SCRAPER_WORKERS = int(os.getenv("SCRAPER_WORKERS", "2"))
executor = ThreadPoolExecutor(max_workers=SCRAPER_WORKERS)

def upload_media_to_wp(image_url, wp_site, wp_user, wp_pass):
    try:
        response = requests.get(image_url, stream=True, timeout=10)
        if response.status_code != 200:
            logging.error(f"Failed to download image: {image_url}. Status Code: {response.status_code}")
            return None
        parsed_url = urlparse(image_url)
        filename = os.path.basename(parsed_url.path)
        mime_type, _ = mimetypes.guess_type(filename)
        if mime_type is None:
            mime_type = 'application/octet-stream'
        files = {
            'file': (filename, response.content, mime_type)
        }
        auth = requests.auth.HTTPBasicAuth(wp_user, wp_pass)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json"
        }
        upload_url = f"{wp_site.rstrip('/')}/wp-json/wp/v2/media"
        upload_response = requests.post(upload_url, headers=headers, files=files, auth=auth, timeout=60)
        if upload_response.status_code in [200, 201]:
            media_id = upload_response.json().get('id')
            logging.debug(f"Uploaded image to WP: {image_url} with Media ID: {media_id}")
            return media_id
        else:
            logging.error(f"Failed to upload image to WP: {image_url}. Status: {upload_response.status_code}, Response: {upload_response.text}")
            return None
    except Exception as e:
        logging.error(f"Exception in upload_media_to_wp for {image_url}: {str(e)}")
        return None

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    migrate.init_app(app, db)

    from models import User

    google_bp = make_google_blueprint(
        client_id=app.config["GOOGLE_OAUTH_CLIENT_ID"],
        client_secret=app.config["GOOGLE_OAUTH_CLIENT_SECRET"],
        scope=["profile", "email"],
        offline=True,
        reprompt_consent=True,
        redirect_url="/login/authorized"
    )
    app.register_blueprint(google_bp, url_prefix="/login")

    def login_required(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if "user_id" not in session:
                flash("Please log in to access this page.", "warning")
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return decorated_function

    @app.context_processor
    def inject_user():
        user = None
        if "user_id" in session:
            user = db.session.get(User, session["user_id"])
        return dict(current_user=user)

    @app.route("/login")
    def login():
        if request.args.get("switch"):
            session.clear()
            try:
                del google.token
            except Exception:
                pass
            google.token = None
        if "user_id" in session:
            return redirect(url_for("dashboard"))
        if not google.authorized:
            return redirect(url_for("google.login"))
        return redirect(url_for("login_authorized"))

    @app.route("/login/authorized")
    def login_authorized():
        if not google.authorized:
            flash("Authorization failed.", "error")
            return redirect(url_for("index"))
        resp = google.get("/oauth2/v2/userinfo")
        if not resp.ok:
            flash("Failed to fetch user info from Google.", "error")
            return redirect(url_for("index"))
        user_info = resp.json()
        from models import User
        user = User.query.filter_by(email=user_info["email"]).first()
        if not user:
            user = User(
                email=user_info["email"],
                name=user_info.get("name", ""),
                picture=user_info.get("picture", "")
            )
            db.session.add(user)
            db.session.commit()
        session["user_id"] = user.id
        flash("Successfully logged in!", "success")
        return redirect(url_for("dashboard"))

    @app.route("/logout")
    def logout():
        session.clear()
        try:
            del google.token
        except Exception:
            pass
        google.token = None
        flash("Logged out successfully.", "info")
        return redirect(url_for("index"))

    @app.route("/wp_login", methods=["GET", "POST"])
    @login_required
    def wp_login():
        if request.method == "POST":
            wp_site = request.form.get("wp_site", "").strip()
            wp_user = request.form.get("wp_user", "").strip()
            wp_pass = request.form.get("wp_pass", "").strip()
            if not wp_site or not wp_user or not wp_pass:
                flash("All WordPress fields are required.", "error")
                return render_template("wp_login.html")
            if not (wp_site.startswith("http://") or wp_site.startswith("https://")):
                flash("WordPress site URL must start with http:// or https://", "error")
                return render_template("wp_login.html")
            headers = {
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            }
            test_url = f"{wp_site.rstrip('/')}/wp-json/wp/v2/posts?per_page=1"
            try:
                response = requests.get(
                    test_url,
                    auth=requests.auth.HTTPBasicAuth(wp_user, wp_pass),
                    headers=headers,
                    timeout=10
                )
                if response.status_code in [200, 201]:
                    session["wp_site"] = wp_site
                    session["wp_user"] = wp_user
                    session["wp_pass"] = wp_pass
                    flash("WordPress login successful!", "success")
                    return redirect(url_for("dashboard"))
                else:
                    flash(f"WordPress login failed: Status code {response.status_code}", "error")
                    return render_template("wp_login.html")
            except Exception as e:
                flash(f"Error connecting to WordPress site: {str(e)}", "error")
                return render_template("wp_login.html")
        return render_template("wp_login.html")

    @app.route("/wp_logout")
    @login_required
    def wp_logout():
        session.pop("wp_site", None)
        session.pop("wp_user", None)
        session.pop("wp_pass", None)
        flash("WordPress logout successful.", "info")
        return redirect(url_for("wp_login"))

    @app.route("/")
    @login_required
    def index():
        from models import ScrapedTweet, ScrapedFBPost
        articles = []
        if "user_id" in session:
            user_id = session["user_id"]
            tweets = ScrapedTweet.query.filter_by(user_id=user_id).order_by(ScrapedTweet.created_at.desc()).all()
            fb_posts = ScrapedFBPost.query.filter_by(user_id=user_id).order_by(ScrapedFBPost.time_of_posting.desc()).all()

            for t in tweets:
                articles.append({
                    "title": t.chatgpt_title or "Untitled Tweet",
                    "body": t.chatgpt_output or t.text,
                    "date": t.created_at.strftime("%Y-%m-%d %H:%M:%S") if t.created_at else "",
                    "category": "Twitter",
                    "image": t.photo_url or "",
                    "row_index": t.id,
                    "published": True,
                    "source": ""
                })
            for p in fb_posts:
                articles.append({
                    "title": p.posttitle or "Untitled Post",
                    "body": p.chatgpt_output or p.post_text,
                    "date": p.time_of_posting,
                    "category": "Facebook",
                    "image": p.first_post_picture or "",
                    "row_index": p.id,
                    "published": True,
                    "source": p.post_url or ""
                })

        wp_categories = []
        wp_site = session.get("wp_site")
        wp_user = session.get("wp_user")
        wp_pass = session.get("wp_pass")
        if wp_site and wp_user and wp_pass:
            wp_categories = fetch_wp_categories(wp_site, wp_user, wp_pass)

        return render_template("index.html", articles=articles, wp_categories=wp_categories)

    def fetch_wp_categories(wp_site, wp_user, wp_pass):
        try:
            url = f"{wp_site.rstrip('/')}/wp-json/wp/v2/categories?per_page=100"
            auth = requests.auth.HTTPBasicAuth(wp_user, wp_pass)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Accept": "application/json"
            }
            r = requests.get(url, auth=auth, headers=headers, timeout=20)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            logging.error(f"Error fetching WP categories: {str(e)}")
        return []

    @app.route("/dashboard", methods=["GET", "POST"])
    @login_required
    def dashboard():
        from models import User, TwitterProfile, FacebookPage
        from forms import TwitterProfileForm, FacebookProfileForm

        user = db.session.get(User, session["user_id"])

        # Ensure the user has 5 Twitter profile entries.
        if not user.twitter_profiles or len(user.twitter_profiles) < 5:
            needed = 5 - len(user.twitter_profiles)
            for _ in range(needed):
                user.twitter_profiles.append(TwitterProfile())
            db.session.commit()

        # Initialize forms with existing data.
        twitter_form = TwitterProfileForm(obj=user)
        facebook_form = FacebookProfileForm(obj=user)

        # Prepopulate Facebook pages on GET.
        if request.method == "GET":
            facebook_form.facebook_pages.entries = []
            for fb_page in user.facebook_pages:
                facebook_form.facebook_pages.append_entry(fb_page.page_url)

        if request.method == "POST":
            # Process Twitter form submission.
            if "submit_twitter" in request.form:
                twitter_form = TwitterProfileForm(request.form)
                if twitter_form.validate():
                    user.twitter_profiles = []
                    handles = [
                        twitter_form.twitter1.data,
                        twitter_form.twitter2.data,
                        twitter_form.twitter3.data,
                        twitter_form.twitter4.data,
                        twitter_form.twitter5.data
                    ]
                    for handle in handles:
                        if handle and handle.strip():
                            user.twitter_profiles.append(TwitterProfile(twitter_handle=handle.strip()))
                    user.preferred_language = twitter_form.twitter_language.data
                    user.scraper_interval = int(twitter_form.scraper_interval.data)
                    db.session.commit()
                    flash("Twitter Preferences updated.", "success")
                    return redirect(url_for("dashboard"))

            # Process Facebook form submission.
            elif "update_facebook" in request.form or "delete_all_facebook" in request.form:
                facebook_form = FacebookProfileForm(request.form)
                if facebook_form.validate():
                    if "delete_all_facebook" in request.form:
                        user.facebook_pages = []
                        db.session.commit()
                        flash("All Facebook Preferences have been deleted.", "info")
                        return redirect(url_for("dashboard"))
                    else:
                        user.facebook_pages = []
                        for page_url in facebook_form.facebook_pages.data:
                            if page_url and page_url.strip():
                                user.facebook_pages.append(FacebookPage(page_url=page_url.strip()))
                        user.preferred_language_facebook = facebook_form.facebook_language.data
                        user.scraper_interval = int(facebook_form.scraper_interval.data)
                        db.session.commit()
                        flash("Facebook Preferences updated.", "success")
                        return redirect(url_for("dashboard"))

        wp_categories = []
        wp_site = session.get("wp_site")
        wp_user = session.get("wp_user")
        wp_pass = session.get("wp_pass")
        if wp_site and wp_user and wp_pass:
            wp_categories = fetch_wp_categories(wp_site, wp_user, wp_pass)

        return render_template("dashboard.html", twitter_form=twitter_form, facebook_form=facebook_form, user=user, wp_categories=wp_categories)

    @app.route("/new_article", methods=["GET", "POST"])
    @login_required
    def new_article():
        if request.method == "POST":
            flash("New article created (placeholder).", "success")
            return redirect(url_for("dashboard"))
        return "Placeholder for new article form."

    # ---- NON-BLOCKING scraper trigger (queues a background job) ----
    def _run_scraper_job(app_obj, user_id):
        with app_obj.app_context():
            from models import User
            from scrape_twitter import scrape_and_store_tweets_for_user
            user = db.session.get(User, user_id)
            if not user:
                current_app.logger.warning("Scraper job: user %s not found", user_id)
                return
            try:
                scrape_and_store_tweets_for_user(user)
                user.last_scraped_at = datetime.now()
                db.session.commit()
                current_app.logger.info("Scraper job finished for user_id=%s", user_id)
            except Exception as e:
                current_app.logger.exception("Scraper job failed for user_id=%s: %s", user_id, e)

    @app.route("/run_scraper", methods=["POST"])
    @login_required
    def run_scraper():
        user_id = session["user_id"]
        app_obj = current_app._get_current_object()
        executor.submit(_run_scraper_job, app_obj, user_id)
        flash("Scraper queued. It will run in the background.", "info")
        return redirect(url_for("index"))

    # (Optional) make FB scraper non-blocking too
    def _run_fb_scraper_job(app_obj, user_id):
        with app_obj.app_context():
            from models import User
            from scrape_facebook import scrape_and_store_fb_posts_for_user
            user = db.session.get(User, user_id)
            if not user:
                current_app.logger.warning("FB scraper job: user %s not found", user_id)
                return
            try:
                scrape_and_store_fb_posts_for_user(user)
                user.last_scraped_at = datetime.now()
                db.session.commit()
                current_app.logger.info("FB scraper job finished for user_id=%s", user_id)
            except Exception as e:
                current_app.logger.exception("FB scraper job failed for user_id=%s: %s", user_id, e)

    @app.route("/run_fb_scraper", methods=["POST"])
    @login_required
    def run_fb_scraper():
        user_id = session["user_id"]
        app_obj = current_app._get_current_object()
        executor.submit(_run_fb_scraper_job, app_obj, user_id)
        flash("Facebook scraper queued. It will run in the background.", "info")
        return redirect(url_for("index"))

    @app.route("/publish_article", methods=["POST"])
    @login_required
    def publish_article():
        try:
            data = request.get_json()
            title = data.get('title', "").strip()
            body  = data.get('body', "").strip()
            image_field = data.get('image', "").strip()
            wp_category_id = data.get('wp_category_id')
            publish_status = data.get('publish_status', 'draft')

            if not title or not body:
                return jsonify({"error": "Title and body are required"}), 400

            wp_site = session.get('wp_site')
            wp_user = session.get('wp_user')
            wp_pass = session.get('wp_pass')
            if not wp_site or not wp_user or not wp_pass:
                return jsonify({"error": "WordPress credentials not found. Please log in again."}), 401

            image_urls = [url.strip() for url in image_field.split(",") if url.strip()]
            media_ids = []
            for url in image_urls:
                media_id = upload_media_to_wp(url, wp_site, wp_user, wp_pass)
                if media_id:
                    media_ids.append(media_id)
            featured_media_id = media_ids[0] if media_ids else 0

            wp_api_endpoint = f"{wp_site.rstrip('/')}/wp-json/wp/v2/posts"
            post_payload = {
                "title": title,
                "content": body,
                "status": publish_status,
                "featured_media": featured_media_id
            }
            if wp_category_id and str(wp_category_id).isdigit():
                post_payload["categories"] = [int(wp_category_id)]

            headers = {
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Content-Type": "application/json"
            }
            auth = requests.auth.HTTPBasicAuth(wp_user, wp_pass)
            response = requests.post(wp_api_endpoint, headers=headers, json=post_payload, auth=auth, timeout=60)
            logging.debug(f"Request URL: {wp_api_endpoint}")
            logging.debug(f"Request Payload: {post_payload}")
            logging.debug(f"Response Status Code: {response.status_code}")
            logging.debug(f"Response Body: {response.text}")

            if response.status_code == 406:
                return jsonify({"error": "ModSecurity is blocking the request. Please contact the server administrator."}), 406
            if response.status_code == 401:
                return jsonify({"error": "Unauthorized. Check WordPress credentials or permissions."}), 401
            if not response.ok:
                return jsonify({"error": f"Failed to create a post on WordPress: {response.text}"}), response.status_code

            wp_data = response.json()
            new_post_id   = wp_data.get("id")
            new_post_link = wp_data.get("link")

            return jsonify({
                "status": "success",
                "wp_post_id": new_post_id,
                "wp_post_link": new_post_link,
                "message": f"Article was saved with status '{publish_status}' (Post ID {new_post_id})."
            })

        except Exception as e:
            logging.error("Error in publish_article: %s", str(e))
            return jsonify({"error": str(e)}), 500

    @app.route("/update_article", methods=["POST"])
    @login_required
    def update_article():
        data = request.get_json()
        row_index = data.get("row_index")
        new_title = data.get("title")
        new_body = data.get("body")

        if not row_index or not new_title or not new_body:
            return jsonify({"error": "Missing required fields"}), 400

        from models import ScrapedTweet, ScrapedFBPost

        article = ScrapedTweet.query.filter_by(id=row_index, user_id=session.get("user_id")).first()
        if article:
            article.chatgpt_title = new_title
            article.chatgpt_output = new_body
        else:
            article = ScrapedFBPost.query.filter_by(id=row_index, user_id=session.get("user_id")).first()
            if article:
                article.posttitle = new_title
                article.chatgpt_output = new_body
            else:
                return jsonify({"error": "Article not found"}), 404

        db.session.commit()
        return jsonify({"status": "success"})

    @app.route("/delete_article", methods=["POST"])
    @login_required
    def delete_article():
        from models import ScrapedTweet, ScrapedFBPost
        data = request.get_json()
        article_id = data.get("article_id")
        category = data.get("category")
        if not article_id or not category:
            return jsonify({"error": "Missing article_id or category"}), 400
        if category == "Twitter":
            article = ScrapedTweet.query.get(article_id)
        elif category == "Facebook":
            article = ScrapedFBPost.query.get(article_id)
        else:
            article = None
        if not article:
            return jsonify({"error": "Article not found"}), 404
        db.session.delete(article)
        db.session.commit()
        return jsonify({"status": "success"})

    def scheduled_scrape():
        with app.app_context():
            from models import User
            from scrape_twitter import scrape_and_store_tweets_for_user
            from scrape_facebook import scrape_and_store_fb_posts_for_user
            now = datetime.now()
            users = User.query.all()
            for user in users:
                if (not user.last_scraped_at) or ((now - user.last_scraped_at).total_seconds() >= (user.scraper_interval * 60)):
                    scrape_and_store_tweets_for_user(user)
                    scrape_and_store_fb_posts_for_user(user)
                    user.last_scraped_at = now
            db.session.commit()
            app.logger.info("Scheduled scraping completed.")

    # Scheduler disabled - automatic scraping is turned off.

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, use_reloader=False)
