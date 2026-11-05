#!/usr/bin/env bash
# Production install for twinkly-bridge on Linux (Debian, Ubuntu, Raspberry Pi OS).
#
# Creates the virtualenv if needed, installs the pinned requirements, writes a hardened
# systemd unit with the right user and paths, then enables and (re)starts it.
#
#   cd ~/TwinklyBridge && sudo bash install.sh
#
# Safe to re-run after a git pull: it updates requirements and restarts the service.
# Options, as environment variables:
#
#   PORT=8080 sudo bash install.sh        web UI port (default 5120)
#   HOST=10.1.1.5 sudo bash install.sh    bind the web UI to one interface (default 0.0.0.0)
#
# Other actions:
#
#   sudo bash install.sh uninstall        stop and remove the service (keeps the checkout,
#                                         .env and instance/bridge_config.json)

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="${SUDO_USER:-$(whoami)}"
NAME=twinkly-bridge
SERVICE=/etc/systemd/system/$NAME.service
PORT="${PORT:-5120}"
HOST="${HOST:-0.0.0.0}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo bash install.sh" >&2
  exit 1
fi

if [ "${1:-}" = "uninstall" ]; then
  systemctl disable --now "$NAME" 2>/dev/null || true
  rm -f "$SERVICE"
  systemctl daemon-reload
  echo "Removed the $NAME service. The checkout in $APP_DIR (with .env and instance/) is untouched;"
  echo "delete it by hand if you no longer need it."
  exit 0
fi

if [ "$APP_USER" = "root" ]; then
  echo "Run as the account the service should use, with sudo (not from a root shell):" >&2
  echo "  sudo bash install.sh" >&2
  exit 1
fi
if ! command -v python3 >/dev/null; then
  echo "python3 is not installed. On Debian / Ubuntu / Raspberry Pi OS:" >&2
  echo "  sudo apt install python3 python3-venv python3-pip" >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "twinkly-bridge needs Python 3.10 or newer; found $(python3 --version)" >&2
  exit 1
fi
if ! python3 -c 'import venv, ensurepip' 2>/dev/null; then
  echo "The python3-venv package is missing:  sudo apt install python3-venv" >&2
  exit 1
fi
case "$PORT" in ''|*[!0-9]*) echo "PORT must be a number" >&2; exit 1;; esac

echo "Installing twinkly-bridge from $APP_DIR as user $APP_USER, web UI on $HOST:$PORT"

if [ ! -d "$APP_DIR/venv" ]; then
  sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
fi
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
  sudo -u "$APP_USER" cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "Created $APP_DIR/.env from .env.example"
fi
# .env may hold PANEL_PASSWORD; the bridge config is written by the service into instance/
chmod 600 "$APP_DIR/.env"
sudo -u "$APP_USER" mkdir -p "$APP_DIR/instance"
chmod 700 "$APP_DIR/instance"
# A 0.1.0 checkout kept its config next to run.py; move it where the sandboxed service can write it.
if [ -f "$APP_DIR/bridge_config.json" ] && [ ! -f "$APP_DIR/instance/bridge_config.json" ]; then
  sudo -u "$APP_USER" mv "$APP_DIR/bridge_config.json" "$APP_DIR/instance/bridge_config.json"
  echo "Moved bridge_config.json into instance/"
fi

# Binding below 1024 needs a capability; everything else runs with none.
if [ "$PORT" -lt 1024 ]; then
  CAPS="CapabilityBoundingSet=CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_BIND_SERVICE"
else
  CAPS="CapabilityBoundingSet="
fi

cat > "$SERVICE" <<UNIT
[Unit]
Description=twinkly-bridge - sACN / DDP to Twinkly Pro real-time bridge
Documentation=file://$APP_DIR/README.md
After=network-online.target
Wants=network-online.target
# Never give up restarting: this has to come back on its own during a show.
StartLimitIntervalSec=0

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
# One worker only: the sACN / DDP listeners and the controller sessions live in the process.
# No control socket: gunicorn would put it in ~/.gunicorn, which the sandbox makes read-only.
ExecStart=$APP_DIR/venv/bin/gunicorn --workers 1 --threads 8 --timeout 60 --graceful-timeout 10 --no-control-socket \\
    --bind $HOST:$PORT wsgi:app
Restart=always
RestartSec=3
TimeoutStopSec=15
UMask=0077
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=TWINKLY_BRIDGE_CONFIG=$APP_DIR/instance/bridge_config.json

# --- sandboxing: the process only needs the network, its own instance/ folder and /dev/log
NoNewPrivileges=true
$CAPS
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$APP_DIR/instance
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
ProtectHostname=true
ProtectProc=invisible
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictNamespaces=true
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallFilter=~@privileged

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "$NAME"
systemctl restart "$NAME"          # starts it, or picks up new code on an update
sleep 2
if ! systemctl is-active --quiet "$NAME"; then
  echo "twinkly-bridge did not start. Last log lines:" >&2
  journalctl -u "$NAME" --no-pager --lines=20 >&2 || true
  exit 1
fi
systemctl --no-pager --lines=3 status "$NAME" || true
echo
if [ "$HOST" = "0.0.0.0" ]; then
  echo "twinkly-bridge is running on http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT"
else
  echo "twinkly-bridge is running on http://$HOST:$PORT"
fi
echo "Logs: sudo journalctl -u $NAME -f      Health: curl http://localhost:$PORT/healthz"
echo "Open on the host firewall: TCP $PORT (web UI), UDP 5568 (sACN), UDP 4048 (DDP)."
if ! grep -qE '^(PANEL_PASSWORD|ALLOWED_NETWORKS)=' "$APP_DIR/.env"; then
  echo
  echo "Note: $APP_DIR/.env sets no PANEL_PASSWORD or ALLOWED_NETWORKS, so anyone who can"
  echo "reach port $PORT can repatch the bridge. See README 'Security'."
fi
