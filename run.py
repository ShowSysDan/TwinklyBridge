#!/usr/bin/env python3
"""Start the bridge with Flask's own server (Windows, macOS, or Linux by hand):

    python run.py [--host 0.0.0.0] [--port 5120] [--config path]

HOST / PORT in .env set the defaults. On Linux the systemd service (install.sh) runs the
same app under gunicorn instead.
"""
from bridge.app import main

if __name__ == "__main__":
    main()
