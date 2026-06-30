"""Production WSGI entry point.

Run with a real WSGI server (NOT the Flask dev server):
  Linux:        gunicorn -w 2 --threads 8 -b 0.0.0.0:8000 wsgi:app
  Windows/any:  waitress-serve --listen=0.0.0.0:8000 wsgi:app

Background searches run as in-process threads, so use a low worker count with
several threads each (gunicorn --threads / waitress --threads).
"""
from app import create_app

app = create_app()
