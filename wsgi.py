# Entry point for Gunicorn on Render
# If you later switch to a factory pattern, change this file only.
from app import app  # app.py defines: app = Flask(__name__)
