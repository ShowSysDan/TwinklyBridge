# TwinklyBridge: notes for Claude

sACN / DDP to Twinkly Pro real-time bridge with a Flask web UI and Eos pixel-map export.
README.md has the design, install steps and API; docs/protocol.md the Twinkly wire format.

## Every change
- Every change the user gets is its own version: bump `bridge/__init__.py` and
  `pyproject.toml` together and add a top section `## [X.Y.Z] - YYYY-MM-DD` to
  CHANGELOG.md (semver: patch = fix, minor = feature, major = breaking).
  `tests/test_app.py::test_version_matches_everywhere` fails if they disagree.
- Document it in the same commit: README.md wherever the change shows (features, install,
  API, .env table), plus `.env.example` for new environment variables.
- Tell the user what to redeploy: Linux `git pull && sudo bash install.sh`; Windows
  `git pull`, `install.bat`, restart `run.bat`.
- Run `python -m pytest -q` (uses the mock controller; no hardware needed).

## Conventions (shared with the other ShowSysDan apps, e.g. Setter)
- Dependencies pinned to exact versions in requirements.txt.
- One process, one gunicorn worker: the UDP listeners and controller sessions live in it.
- Runtime state the service writes goes in `instance/` only (the systemd sandbox makes
  everything else read-only). Secrets and deployment settings go in `.env`.
- Access control lives in `bridge/security.py`; no inline `<script>` or event-handler
  attributes in templates (the CSP forbids them): JS in `bridge/static/js/app.js`.
- Log through `bridge.logs.get(name)`; operator actions at INFO with the client IP,
  controller faults at WARNING once per new error (`Controller._fail`).
- The output loop and the control thread must never block on each other: HTTP to
  controllers only on the control thread or in request handlers.
- Parameter counts come from `Controller.parameters` / `layout.parameter_summary()`;
  don't recompute them elsewhere.
