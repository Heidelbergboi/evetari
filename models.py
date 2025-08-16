from extensions import db
from datetime import datetime

class User(db.Model):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(255))
    picture = db.Column(db.String(500))
    # Twitter language preference (already existed)
    preferred_language = db.Column(db.String(10), default="en")
    # NEW: Facebook language preference
    preferred_language_facebook = db.Column(db.String(10), default="en")
    scraper_interval = db.Column(db.Integer, default=60)  # in minutes
    last_scraped_at = db.Column(db.DateTime, nullable=True)
    twitter_profiles = db.relationship("TwitterProfile", backref="user", cascade="all, delete-orphan", lazy=True)
    facebook_pages = db.relationship("FacebookPage", backref="user", cascade="all, delete-orphan", lazy=True)
    scraped_tweets = db.relationship("ScrapedTweet", backref="user", cascade="all, delete-orphan", lazy=True)
    scraped_fb_posts = db.relationship("ScrapedFBPost", backref="user", cascade="all, delete-orphan", lazy=True)

    def __repr__(self):
        return f"<User {self.email}>"

class TwitterProfile(db.Model):
    __tablename__ = "twitter_profile"
    id = db.Column(db.Integer, primary_key=True)
    twitter_handle = db.Column(db.String(255))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    def __repr__(self):
        return f"<TwitterProfile {self.twitter_handle}>"

class FacebookPage(db.Model):
    __tablename__ = "facebook_page"
    id = db.Column(db.Integer, primary_key=True)
    page_url = db.Column(db.String(255))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    def __repr__(self):
        return f"<FacebookPage {self.page_url}>"

class ScrapedTweet(db.Model):
    __tablename__ = "scraped_tweet"
    id = db.Column(db.Integer, primary_key=True)
    tweet_id = db.Column(db.String(255), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    text = db.Column(db.Text)
    full_text = db.Column(db.Text)
    lang = db.Column(db.String(10))
    retweet_count = db.Column(db.Integer, default=0)
    reply_count = db.Column(db.Integer, default=0)
    like_count = db.Column(db.Integer, default=0)
    quote_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime)
    author_name = db.Column(db.String(255))
    author_username = db.Column(db.String(255))
    photo_url = db.Column(db.String(500))
    chatgpt_title = db.Column(db.String(500))
    chatgpt_output = db.Column(db.Text)

    def __repr__(self):
        return f"<ScrapedTweet tweet_id={self.tweet_id} user_id={self.user_id}>"

class ScrapedFBPost(db.Model):
    __tablename__ = "scraped_fb_post"
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.String(255), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    page_name = db.Column(db.String(255))
    post_url = db.Column(db.String(500))
    post_text = db.Column(db.Text)
    time_of_posting = db.Column(db.String(100))
    number_of_likes = db.Column(db.Integer, default=0)
    number_of_comments = db.Column(db.Integer, default=0)
    number_of_shares = db.Column(db.Integer, default=0)
    first_post_picture = db.Column(db.String(500))
    profile_picture = db.Column(db.String(500))
    posttitle = db.Column(db.String(500))
    chatgpt_output = db.Column(db.Text)

    def __repr__(self):
        return f"<ScrapedFBPost post_id={self.post_id} user_id={self.user_id}>"
