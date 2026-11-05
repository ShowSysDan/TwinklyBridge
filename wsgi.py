"""WSGI entry point for gunicorn (the systemd service install.sh writes):

    gunicorn --workers 1 --threads 8 --bind 0.0.0.0:5120 wsgi:app

Exactly one worker: the sACN / DDP listeners and the controller sessions live in the process.
"""
from bridge.app import create_app

app = create_app()
