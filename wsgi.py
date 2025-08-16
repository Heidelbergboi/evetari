# Gunicorn entrypoint for Render (Flask app factory pattern)
from app import create_app

app = create_app()
