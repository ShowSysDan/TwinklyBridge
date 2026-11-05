# Changelog

Every change the user gets is its own version: bump `bridge/__init__.py` and
`pyproject.toml` together and add a section here (`test_version_matches_everywhere`
fails if they disagree). Semver: patch = fix, minor = feature, major = breaking.

## [0.3.1] - 2026-11-05

- docs/protocol.md: recorded the Diagnose result from the TWPROCTRLPLC21 fw 4.1.0 that
  shows a map in the Twinkly app but answers 2404 on every layout / map URL tried, v2 and
  v1. The controller does not always hold the app's map.

## [0.3.0] - 2026-11-05

- **Diagnose** button on each controller: logs in and GETs every likely map URL
  (`led/layout/full`, `led/layout`, `layout`, `led/map`, `mapping`, ... under v2 and v1,
  plus `gestalt`, `summary`, `movies`), read-only, and shows status, Twinkly result code,
  reply keys and whether coordinates came back, in a box you can copy. For controllers
  that have a map in the Twinkly app but answer 2404 on `led/layout/full`
  (seen on a TWPROCTRLPLC21 fw 4.1.0). Also `POST /api/controllers/<id>/probe`.
- **Map from ports** button: builds a map from the controller's own port layout, one row
  per port, LEDs in chain order (`POST /api/controllers/<id>/map/ports`). Exact pixel order
  and Eos addressing, not physical positions; gives a working pixel map and parameter count
  until a camera map can be pulled.
- Eos export places maps that are already on a whole-number grid (Map from ports, or a CSV
  in cell units) exactly, e.g. 250 x 5 cells, instead of rescaling and nudging them.
- The Pull map 404 message points to Diagnose / Map from ports instead of assuming the
  controller was never mapped.
- Fix: a controller's card did not refresh after pressing one of its buttons (the page
  skipped redraws while any element in the list had focus, not just an input).

## [0.2.1] - 2026-11-05

- **Pull map 404s now say what happened.** When the controller answers 404 to the layout
  request on both `/xled/v2/led/layout/full` and `/xled/v1/led/layout/full`, the error
  names both URLs, the controller's reply, its model / firmware and group mode, and what to
  do (camera-map it in the Twinkly app, pull from the group master, or upload a CSV),
  instead of a bare `HTTP Error 404: Not Found`. The error stays on the controller's card
  until the next successful pull (`map_error` in `/api/status`), and long messages stay on
  screen longer.
- Pull map retries once with a fresh login if the controller says the token expired (401).
- The bridge's own 404s on `/api/` are JSON (`bridge: ...`), and the UI reports any failed
  API call instead of carrying on as if it had worked.

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
