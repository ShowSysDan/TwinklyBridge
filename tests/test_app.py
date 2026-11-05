"""Access control, health, versioning and the static UI. No controller needed."""
import os
import re

from flask import Flask

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make(**kw):
    from bridge import security
    app = Flask("t")
    warnings = security.init_app(app, **kw)

    @app.get("/api/x")
    def x():
        return {"ok": True}

    @app.post("/api/x")
    def post_x():
        return {"ok": True}

    @app.post("/api/controllers/a/map/csv")
    def csv_upload():
        return {"ok": True}

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app.test_client(), warnings


def test_open_by_default_with_warnings():
    c, warnings = make()
    assert c.get("/api/x").status_code == 200
    assert len(warnings) == 2


def test_allowed_networks():
    c, warnings = make(allowed_networks="10.1.1.0/24")
    assert c.get("/api/x", environ_base={"REMOTE_ADDR": "10.1.1.7"}).status_code == 200
    assert c.get("/api/x", environ_base={"REMOTE_ADDR": "10.2.1.7"}).status_code == 403
    assert c.get("/healthz", environ_base={"REMOTE_ADDR": "10.2.1.7"}).status_code == 403


def test_panel_password():
    c, warnings = make(panel_password="s3cret")
    assert c.get("/api/x").status_code == 401
    assert c.get("/api/x", auth=("twinkly", "wrong")).status_code == 401
    assert c.get("/api/x", auth=("twinkly", "s3cret")).status_code == 200
    assert c.get("/healthz").status_code == 200                       # monitoring needs no login


def test_cross_site_and_json_only():
    c, _ = make()
    assert c.post("/api/x", json={}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.post("/api/x", data="a=1", content_type="application/x-www-form-urlencoded").status_code == 415
    assert c.post("/api/x", json={"a": 1}).status_code == 200
    assert c.post("/api/controllers/a/map/csv", data="index,x,y\n0,1,2",
                  content_type="text/csv").status_code == 200          # the one upload endpoint


def test_security_headers():
    c, _ = make()
    r = c.get("/api/x")
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["Cache-Control"] == "no-store"


def test_template_has_no_inline_script():
    html = open(os.path.join(ROOT, "bridge", "templates", "index.html")).read()
    assert "<script>" not in html and "<style>" not in html
    assert not re.search(r"\son[a-z]+\s*=", html)                      # no inline event handlers


def test_version_matches_everywhere():
    from bridge import __version__
    pyproject = open(os.path.join(ROOT, "pyproject.toml")).read()
    assert re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1) == __version__
    changelog = open(os.path.join(ROOT, "CHANGELOG.md")).read()
    assert re.search(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.M).group(1) == __version__


def test_healthz_and_static(bridge):
    client, appmod, state = bridge
    h = client.get("/healthz").get_json()
    assert h["ok"] and h["controllers"] >= 1 and "version" in h
    assert client.get("/api/version").get_json()["version"] == h["version"]
    assert client.get("/static/js/app.js").status_code == 200
    assert client.get("/static/css/style.css").status_code == 200
    page = client.get("/").data.decode()
    assert "/static/js/app.js" in page and h["version"] in page
