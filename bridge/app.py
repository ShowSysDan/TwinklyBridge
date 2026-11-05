"""Web UI and REST API.

`python run.py` (any OS, Flask's own server) or gunicorn with `wsgi:app` (the systemd
service written by install.sh) both go through create_app(), which loads .env and the
config, installs the access controls and starts the receiver / output threads once.
"""
import argparse
import base64
import csv
import io
import os
import threading
import time

from flask import Flask, Response, abort, jsonify, render_template, request

from . import __version__, logs, security, state
from .ddp import DdpReceiver
from .engine import ControlThread, OutputEngine
from .exports import eos_patch_csv, eos_pixelmap_csv, preview_svg
from .layout import auto_patch, parameter_summary
from .sacn import SacnReceiver
from .state import CONFIG, CONTROLLERS, lock, save_config, wanted_universes
from .twinkly import Controller

DEFAULT_PORT = 5120

app = Flask(__name__, template_folder=os.path.join(state.PACKAGE_DIR, "templates"),
            static_folder=os.path.join(state.PACKAGE_DIR, "static"))
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024        # a 3D map CSV for a few thousand LEDs is < 1 MB
log = logs.get("app")
RX = SacnReceiver()
DDP = DdpReceiver()
OUT = OutputEngine(RX, DDP)
CTL = ControlThread()


def load_controllers():
    with lock:
        CONTROLLERS.clear()
        for cfg in CONFIG["controllers"]:
            c = Controller(cfg)
            CONTROLLERS[c.id] = c


def client_ip():
    return request.remote_addr or "?"


def get_ctrl(cid):
    c = CONTROLLERS.get(cid)
    if not c:
        abort(404, f"the bridge has no controller '{cid}' (reload the page; it may have been removed)")
    return c


@app.errorhandler(404)
@app.errorhandler(405)
def api_not_found(err):
    """JSON errors on /api/, so the UI can say what went wrong instead of failing silently."""
    if request.path.startswith("/api/"):
        detail = err.description if err.code == 404 and "controller" in (err.description or "") \
            else f"{request.method} {request.path} is not a bridge API endpoint"
        return jsonify(ok=False, error=f"bridge: {detail}"), err.code
    return err


def export_params():
    p = {k: request.args.get(k, "") for k in
         ("pixelmap", "flatten", "arrange", "width", "max_cells", "mode", "channel_base",
          "manufacturer", "fixture_type", "allow_straddle")}
    return p


def csv_response(text, filename):
    return Response(text, mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})


# ---------------------------------------------------------------- pages / status
@app.get("/")
def index():
    return render_template("index.html", version=__version__)


@app.get("/healthz")
def healthz():
    """For monitoring; needs no login. 200 while the web side is up."""
    with lock:
        ctrls = list(CONTROLLERS.values())
    return jsonify({"ok": True, "version": __version__, "sacn_listening": RX.sock is not None,
                    "controllers": len(ctrls),
                    "online": sum(1 for c in ctrls if c.status in ("online", "streaming")),
                    "streaming": sum(1 for c in ctrls if c.status == "streaming")})


@app.get("/api/version")
def api_version():
    return jsonify({"version": __version__})


@app.get("/api/parameters")
def api_parameters():
    return jsonify(parameter_summary())


@app.get("/api/status")
def api_status():
    with lock:
        ctrls = [c.to_status() for c in CONTROLLERS.values()]
    return jsonify({"version": __version__, "sacn": RX.status(), "ddp": DDP.status(),
                    "output": {"fps_target": CONFIG["output"]["fps"], "fps_actual": round(OUT.fps_actual, 1)},
                    "controllers": ctrls, "parameters": parameter_summary(),
                    "settings": {"sacn": CONFIG["sacn"], "ddp": CONFIG["ddp"], "output": CONFIG["output"]}})


@app.post("/api/settings")
def api_settings():
    body = request.get_json(force=True) or {}
    restart = False
    with lock:
        for section in ("sacn", "ddp", "output"):
            for k, v in body.get(section, {}).items():
                if k in CONFIG[section]:
                    if section == "sacn" and k in ("interface_ip", "multicast") and CONFIG[section][k] != v:
                        restart = True
                    CONFIG[section][k] = type(CONFIG[section][k])(v)
        for c in CONTROLLERS.values():
            c._slices = None
    save_config()
    log.info("settings saved by %s: %s", client_ip(), {k: body[k] for k in ("sacn", "ddp", "output") if k in body})
    if restart:
        RX.restart()
    else:
        RX.join(wanted_universes())
    return jsonify(ok=True)


# ---------------------------------------------------------------- controllers
@app.get("/api/controllers")
def api_controllers():
    with lock:
        return jsonify([c.to_status() for c in CONTROLLERS.values()])


@app.post("/api/controllers")
def api_add_controller():
    body = request.get_json(force=True) or {}
    ip = (body.get("ip") or "").strip()
    if not ip:
        return jsonify(error="ip is required"), 400
    c = Controller({"ip": ip, "universe": body.get("universe", 1), "channel": body.get("channel", 1),
                    "name": body.get("name", ""), "source": body.get("source", "sacn"),
                    "ddp_start": body.get("ddp_start", 1)})
    with lock:
        if c.id in CONTROLLERS:
            return jsonify(error=f"{ip} is already added"), 409
    ok = c.connect()
    with lock:
        CONTROLLERS[c.id] = c
    save_config()
    log.info("controller %s (%s) added by %s: %s", c.name, c.ip, client_ip(),
             f"{c.n_led} LEDs {c.profile}" if ok else f"not reachable yet ({c.error})")
    RX.join(wanted_universes())
    return jsonify(c.to_status()), (201 if ok else 202)


@app.put("/api/controllers/<cid>")
def api_edit_controller(cid):
    c = get_ctrl(cid)
    body = request.get_json(force=True) or {}
    with lock:
        if "name" in body:
            c.name = body["name"] or c.name
        if "source" in body and body["source"] in ("sacn", "ddp"):
            c.source = body["source"]
        if "universe" in body:
            c.universe = max(1, int(body["universe"]))
        if "channel" in body:
            c.channel = min(512, max(1, int(body["channel"])))
        if "ddp_start" in body:
            c.ddp_start = max(1, int(body["ddp_start"]))
        if "input_order" in body and sorted(body["input_order"]) == ["B", "G", "R", "W"]:
            c.input_order = body["input_order"]
        if "enabled" in body:
            c.enabled = bool(body["enabled"])
        c._slices = None
    save_config()
    log.info("controller %s edited by %s: %s", c.name, client_ip(), body)
    RX.join(wanted_universes())
    return jsonify(c.to_status())


@app.delete("/api/controllers/<cid>")
def api_delete_controller(cid):
    c = get_ctrl(cid)
    if c.active:
        c.restore_mode()
    with lock:
        del CONTROLLERS[cid]
    save_config()
    log.info("controller %s (%s) removed by %s", c.name, c.ip, client_ip())
    return jsonify(ok=True)


@app.post("/api/controllers/<cid>/connect")
def api_connect(cid):
    c = get_ctrl(cid)
    c.connect()
    save_config()
    RX.join(wanted_universes())
    return jsonify(c.to_status())


@app.post("/api/patch/auto")
def api_auto_patch():
    """body: {"mode": "aligned"|"packed", "start_universe": 1, "order": "group"|"added"}"""
    body = request.get_json(force=True) or {}
    result = auto_patch(body.get("mode", "aligned"), body.get("start_universe", 1), body.get("order", "group"))
    save_config()
    log.info("auto patch (%s) by %s: %s", body.get("mode", "aligned"), client_ip(),
             ", ".join(f"{r['name']} u{r['universe']}/{r['channel']}" for r in result))
    RX.join(wanted_universes())
    return jsonify(result)


# ---------------------------------------------------------------- maps
@app.post("/api/controllers/<cid>/map/pull")
def api_pull_map(cid):
    c = get_ctrl(cid)
    try:
        n = c.pull_map()
        c.map_error = ""
    except Exception as e:                                   # noqa: BLE001
        msg = str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
        c.map_error = msg
        log.warning("map pull from %s (%s) failed: %s", c.name, c.ip, msg)
        return jsonify(error=f"Pull map failed: {msg}"), 502
    save_config()
    log.info("map pulled from %s (%s) by %s: %d points (%s), %d parameters",
             c.name, c.ip, client_ip(), n, c.map_source, c.parameters)
    return jsonify(ok=True, points=n, source=c.map_source, pixels=min(n, c.n_led), bpp=c.bpp,
                   parameters=c.parameters, totals=parameter_summary())


@app.post("/api/controllers/<cid>/map/ports")
def api_ports_map(cid):
    c = get_ctrl(cid)
    try:
        n = c.map_from_ports()
    except Exception as e:                                   # noqa: BLE001
        return jsonify(error=f"Map from ports failed: {e}"), 502
    c.map_error = ""
    save_config()
    log.info("map built from ports for %s by %s: %d points in %d rows, %d parameters",
             c.name, client_ip(), n, len(c.ports) or 1, c.parameters)
    return jsonify(ok=True, points=n, source=c.map_source, pixels=n, bpp=c.bpp,
                   parameters=c.parameters, totals=parameter_summary())


@app.post("/api/controllers/<cid>/probe")
def api_probe(cid):
    c = get_ctrl(cid)
    try:
        result = c.probe()
    except Exception as e:                                   # noqa: BLE001
        return jsonify(error=f"Diagnose failed: {e}"), 502
    found = [r["path"] for r in result["results"] if r.get("coordinates")]
    log.info("diagnose %s (%s) by %s: %s", c.name, c.ip, client_ip(),
             f"coordinates at {', '.join(found)}" if found else "no URL returned coordinates")
    return jsonify(result)


@app.post("/api/controllers/<cid>/map/csv")
def api_csv_map(cid):
    c = get_ctrl(cid)
    f = request.files.get("file")
    text = f.read().decode("utf-8", "replace") if f else (request.get_data(as_text=True) or "")
    try:
        n = c.set_map_from_csv(text)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    save_config()
    log.info("map CSV loaded for %s by %s: %d points, %d parameters", c.name, client_ip(), n, c.parameters)
    return jsonify(ok=True, points=n, pixels=min(n, c.n_led) if c.n_led else n, bpp=c.bpp,
                   parameters=c.parameters, totals=parameter_summary())


@app.delete("/api/controllers/<cid>/map")
def api_clear_map(cid):
    c = get_ctrl(cid)
    c.map, c.map_source = None, ""
    save_config()
    log.info("map cleared for %s by %s", c.name, client_ip())
    return jsonify(ok=True)


@app.get("/api/controllers/<cid>/map.csv")
def api_map_csv(cid):
    c = get_ctrl(cid)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["index", "port", "led", "x", "y", "z"])
    for i, p in enumerate(c.map or []):
        port, led = c.port_of(i)
        w.writerow([i, port, led, *p])
    return csv_response(buf.getvalue(), f"{c.name}_layout.csv")


@app.get("/api/map")
def api_map():
    out = []
    with lock:
        for c in CONTROLLERS.values():
            out.append({"id": c.id, "name": c.name, "n_led": c.n_led, "bpp": c.bpp, "ports": c.ports,
                        "group": c.group, "points": [[p[0], p[1]] for p in (c.map or [])]})
    return jsonify(out)


@app.get("/api/frames")
def api_frames():
    with lock:
        out = {c.id: base64.b64encode(c.last_frame).decode() for c in CONTROLLERS.values() if c.last_frame}
    return jsonify(out)


# ---------------------------------------------------------------- tests
@app.post("/api/controllers/<cid>/test")
def api_test(cid):
    """body: {"mode": "pixel"|"solid"|"off"|"stop", "index": 0, "rgb": [r,g,b], "seconds": 3}"""
    c = get_ctrl(cid)
    body = request.get_json(force=True) or {}
    mode = body.get("mode", "stop")
    secs = float(body.get("seconds", 3))
    rgb = [max(0, min(255, int(v))) for v in (body.get("rgb") or [255, 255, 255])[:3]]
    if not c.n_led and not c.connect():
        return jsonify(error=c.error), 502
    if mode == "stop":
        c.test = None
    elif mode == "pixel":
        c.test = {"frame": c.pixel_frame(int(body.get("index", 0)), rgb), "until": time.time() + secs}
    elif mode == "solid":
        c.test = {"frame": c.solid_frame(rgb), "until": time.time() + secs}
    elif mode == "off":
        c.test = {"frame": bytes(c.n_led * c.bpp), "until": time.time() + secs}
    else:
        return jsonify(error="mode must be pixel, solid, off or stop"), 400
    return jsonify(ok=True)


# ---------------------------------------------------------------- exports
@app.get("/api/export/eos_pixelmap.csv")
def api_eos_pixelmap():
    try:
        text, size, straddle = eos_pixelmap_csv(export_params())
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return csv_response(text, "eos_pixelmap.csv")


@app.get("/api/export/eos_patch.csv")
def api_eos_patch():
    try:
        return csv_response(eos_patch_csv(export_params()), "eos_patch.csv")
    except ValueError as e:
        return jsonify(error=str(e)), 400


@app.get("/api/export/eos_preview.svg")
def api_eos_preview():
    try:
        return Response(preview_svg(export_params()), mimetype="image/svg+xml")
    except ValueError as e:
        return Response(f"<svg xmlns='http://www.w3.org/2000/svg' width='400' height='40'>"
                        f"<text y='20'>{e}</text></svg>", mimetype="image/svg+xml")


# ---------------------------------------------------------------- start-up
def start_threads():
    for t in (RX, DDP, OUT, CTL):
        if not t.is_alive():
            t.start()


def load_env():
    """Read .env next to run.py, if python-dotenv is installed (it is in requirements.txt)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(os.path.join(state.ROOT_DIR, ".env"))


_init_lock = threading.Lock()
_initialised = False


def create_app(config_path=None, start=True):
    """Load .env and the config, install access control, start the threads. Safe to call twice.
    gunicorn must run ONE worker: the sACN / DDP sockets and the controller sessions live in
    the process, and a second worker would fight over UDP 5568 and the rt tokens."""
    global _initialised
    with _init_lock:
        if _initialised:
            return app
        load_env()
        logs.setup()
        if config_path or os.environ.get("TWINKLY_BRIDGE_CONFIG"):
            state.CONFIG_PATH = config_path or os.environ["TWINKLY_BRIDGE_CONFIG"]
        state.load_config(state.CONFIG_PATH)
        load_controllers()
        warnings = security.init_app(app, os.environ.get("ALLOWED_NETWORKS", ""),
                                     os.environ.get("PANEL_USER") or "twinkly",
                                     os.environ.get("PANEL_PASSWORD") or None)
        log.info("twinkly-bridge %s starting: config %s, %d controllers, sACN UDP %d, DDP UDP %d",
                 __version__, state.CONFIG_PATH, len(CONTROLLERS), RX.port, DDP.port)
        for w in warnings:
            log.warning(w)
        if start:
            start_threads()
        _initialised = True
    return app


def main(argv=None):
    ap = argparse.ArgumentParser(description="sACN / DDP (Eos) to Twinkly Pro real-time bridge")
    ap.add_argument("--host", default=None,
                    help="web UI bind address (default HOST from .env, else 127.0.0.1; 0.0.0.0 for LAN access)")
    ap.add_argument("--port", type=int, default=None, help=f"web UI port (default PORT from .env, else {DEFAULT_PORT})")
    ap.add_argument("--config", default=None, help="path to bridge_config.json (default instance/bridge_config.json)")
    args = ap.parse_args(argv)
    load_env()
    host = args.host or os.environ.get("HOST") or "127.0.0.1"
    port = args.port or int(os.environ.get("PORT") or DEFAULT_PORT)
    create_app(args.config)
    log.info("web UI on http://%s:%d", host, port)
    # No reloader (it would start every thread twice) and no Werkzeug debugger (it offers a
    # Python console to anyone who can reach the port).
    app.run(host=host, port=port, threaded=True, use_reloader=False, use_debugger=False)


if __name__ == "__main__":
    main()
