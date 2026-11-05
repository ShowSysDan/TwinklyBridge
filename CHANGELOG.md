# Changelog

Every change the user gets is its own version: bump `bridge/__init__.py` and
`pyproject.toml` together and add a section here (`test_version_matches_everywhere`
fails if they disagree). Semver: patch = fix, minor = feature, major = breaking.

## [0.2.0] - 2026-11-05

Imported into GitHub (ShowSysDan/TwinklyBridge) and brought in line with the other
ShowSysDan show-control apps.

- **Parameter count.** Once a controller's map is pulled (or a CSV loaded), the bridge
  shows how many Eos parameters the pixel map will use: one per colour per mapped pixel
  (3 for RGB, 4 for RGBW). Per controller on its card, a total in the header, a
  per-controller table above *Export for Eos*, in the *Pull map* toast, in the preview SVG
  header for each pixel map, in `/api/status` (`parameters`) and at `/api/parameters`.
- **Linux service.** `install.sh` creates `venv/`, installs the pinned requirements,
  creates `.env` and writes a sandboxed systemd unit (`twinkly-bridge.service`, gunicorn
  with one worker, restart forever). `sudo bash install.sh uninstall` removes it.
- **Windows.** `install.bat` creates `venv\` and installs the requirements; `run.bat`
  uses that venv.
- **Default web port is now 5120** (was 5000, which macOS AirPlay also uses). `HOST` /
  `PORT` in `.env` or `--host` / `--port` change it.
- **Config moved to `instance/bridge_config.json`** (git-ignored, the only folder the
  service may write). An existing `bridge_config.json` next to `run.py` is still used
  until it is moved; `install.sh` / `install.bat` move it.
- **Access control** (all optional, off by default): `ALLOWED_NETWORKS` address
  allowlist, `PANEL_PASSWORD` HTTP Basic auth, cross-site request blocking, JSON-only
  mutating API, Content-Security-Policy and other security headers, 8 MB body limit.
- **Logging** through Python `logging`: console / journald, local syslog (tag
  `twinkly-bridge`, LOCAL0) and an optional remote syslog server (`SYSLOG_HOST`). Every
  operator action (controller added / edited / removed, auto patch, map pulled, settings
  saved) is logged with the client's IP; a controller going into error is logged once.
- `/healthz` for monitoring (no login), `/api/version`, version shown in the UI header.
- `.env.example`; dependencies pinned to exact tested versions (Flask 3.1.3,
  python-dotenv 1.2.4, gunicorn 26.2.0 on Linux).
- The UI's script and styles moved to `bridge/static/` so the CSP can forbid inline
  script. Controller names are escaped in the preview SVG.
- Werkzeug debugger explicitly off in `run.py`.
- Tests: the mock controller reports the last *complete* real-time frame, fixing an
  intermittent failure when a test read a frame that was still arriving.

## [0.1.0] - 2026-11-05

First release, as uploaded: sACN and DDP input, Twinkly Pro real-time output, camera map
pull, live map, auto patch, Eos pixel map / patch sheet export, standalone tools and the
mock controller test suite.
