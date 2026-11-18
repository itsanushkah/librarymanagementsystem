"""Web entry point. Local: python app.py   Production: gunicorn app:app"""
import os

from lms.web import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", "5000")))
