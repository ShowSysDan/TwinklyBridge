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
import re
import shutil
import threading
import time
import zipfile
from collections import Counter

from flask import Flask, Response, abort, jsonify, make_response, redirect, render_template, request

from . import __version__, logs, mapping, security, state, tls
from .ddp import DdpReceiver
from .engine import ControlThread, OutputEngine
from .exports import eos_patch_csv, eos_pixelmap_csv, preview_svg
from .layout import auto_patch, parameter_summary
from .patterns import MAP_PATTERNS, PATTERNS, bounds
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
    # the HTTPS port is the phone's: its bare address goes straight to camera mapping
    if request.is_secure and not request.args.get("desk"):
        return redirect("/map")
    return render_template("index.html", version=__version__)


@app.get("/map")
def map_page():
    """The phone page for camera mapping (needs HTTPS for the camera)."""
    return render_template("map.html", version=__version__, https_port=app.config.get("HTTPS_PORT", 0))


@app.get("/api/mapping/info")
def api_mapping_info():
    """Where the phone should go: the HTTPS port (0 = no certificate, HTTPS off)."""
    host = request.host.rsplit(":", 1)[0] if not request.host.endswith("]") else request.host
    lan = tls.lan_addresses()
    names = tls.STATUS.get("names") or []
    port = app.config.get("HTTPS_PORT", 0)
    return jsonify({"https_port": port, "host": host, "https": dict(tls.STATUS), "lan_addresses": lan,
                    "urls": [f"https://{ip}:{port}/map" for ip in lan] if port else [],
                    "cert_missing_ips": [ip for ip in lan if names and ip not in names],
                    "session": mapping.SESSION.describe() if mapping.SESSION else None})


def _deliver(ctrls, frames, timeout=2.5):
    """Set each controller's mapping frame and wait until it has gone out at least twice.
    Returns the names of controllers that did not take it in time."""
    now = time.time()
    marks = {}
    for c in ctrls:
        marks[c.id] = c.frames_sent
        c.test = {"frame": frames[c.id], "until": now + 60, "pattern": "mapping"}
    end = time.time() + timeout
    late = list(ctrls)
    while late and time.time() < end:
        late = [c for c in late if not (c.frames_sent >= marks[c.id] + 2 and c.last_frame == frames[c.id])]
        if late:
            time.sleep(0.01)
    return [c.name for c in late]


@app.post("/api/mapping/start")
def api_mapping_start():
    """body: {"ids": [...] (omit = every enabled controller), "level": 128}"""
    body = request.get_json(force=True) or {}
    with lock:
        ids = body.get("ids") or [c.id for c in CONTROLLERS.values() if c.enabled]
        ctrls = [CONTROLLERS[i] for i in ids if i in CONTROLLERS]
    for c in ctrls:
        if not c.n_led and not c.connect():
            return jsonify(error=f"{c.name}: {c.error}"), 502
    if not ctrls:
        return jsonify(error="no controllers to map"), 400
    with mapping.lock:
        if mapping.SESSION:
            for c in mapping.SESSION.ctrls:
                c.test = None
        s = mapping.Session(ctrls, body.get("level", 128))
        mapping.SESSION = s
    late = _deliver(ctrls, {c.id: s.frame(c, "black") for c in ctrls}, timeout=8)   # first one logs in, enters rt
    if late:
        return jsonify(error=f"not taking frames: {', '.join(late)} (check it is online)"), 502
    log.info("camera mapping started by %s: %s, %d LEDs, %d steps", client_ip(),
             ", ".join(c.name for c in ctrls), s.total, len(s.steps))
    return jsonify(s.describe())


def _session(body):
    s = mapping.SESSION
    if not s or body.get("session") != s.id:
        abort(make_response(jsonify(ok=False, error="that mapping session is over (another phone may have "
                                                    "started one, or it was saved): start again"), 409))
    return s


@app.post("/api/mapping/step")
def api_mapping_step():
    """body: {"session": id, "step": "white"} -> returns once the frame is out on every controller."""
    body = request.get_json(force=True) or {}
    s = _session(body)
    step = body.get("step")
    if step not in s.steps:
        return jsonify(error=f"unknown step {step!r}"), 400
    t = time.time()
    late = _deliver(s.ctrls, {c.id: s.frame(c, step) for c in s.ctrls})
    if late:
        return jsonify(error=f"not taking frames: {', '.join(late)}"), 502
    return jsonify(ok=True, ms=round((time.time() - t) * 1000))


@app.post("/api/mapping/stop")
def api_mapping_stop():
    with mapping.lock:
        if mapping.SESSION:
            for c in mapping.SESSION.ctrls:
                c.test = None
            mapping.SESSION = None
    return jsonify(ok=True)


def _store_maps(ctrls, offsets, pts, cols, save, source, partial=False):
    """Split global points (one per LED: [x, y] or [x, y, z] or None) into each controller,
    report per port, fill gaps along the strings and (if save) store the maps."""
    out = []
    for c in ctrls:
        o = offsets[c.id]
        mine = [p if p and len(p) >= 2 else None for p in pts[o:o + c.n_led]]
        rep = mapping.report(mine, cols[o:o + c.n_led], c.ports, c.n_led, partial)
        filled, n_filled = mapping.fill_gaps(mine, c.ports, c.n_led)
        n_found = sum(1 for p in mine if p)
        if save:
            c.map = [[round(p[0], 5), round(p[1], 5), round(p[2], 5) if len(p) > 2 else 0] if p else [0, 0, 0]
                     for p in filled]
            c.map_source = f"{source}, {n_found} seen, {n_filled} filled"
            c.map_filled = [i for i, p in enumerate(mine) if not p]
            c.map_error = ""
        out.append({"id": c.id, "name": c.name, "found": n_found, "filled": n_filled,
                    "parameters": c.parameters, "ports": rep})
    if save:
        save_config()
        with mapping.lock:
            for c in ctrls:
                c.test = None
            mapping.SESSION = None
        log.info("camera map (%s) saved by %s: %s", source, client_ip(),
                 "; ".join(f"{r['name']} {r['found']} seen {r['filled']} filled" for r in out))
    return out


def _check_points(pts, total):
    if len(pts) != total:
        return f"expected {total} points, got {len(pts)}"
    found = sum(1 for p in pts if p)
    if found < max(3, total // 10):
        return (f"only {found} of {total} LEDs were found: nothing kept. Darken the room, get the "
                f"installation in frame, hold the phone still, and try again")
    return None


@app.post("/api/mapping/save")
def api_mapping_save():
    """One capture straight to a map. body: {"session": id, "points": [[x, y] | null, ...]
    (one per LED, global order), "colours": ["RGB" | "GRB" | ... | null, ...], "save": true}.
    Fills gaps along each string, saves each controller's map (unless save is false) and
    returns a report per port."""
    body = request.get_json(force=True) or {}
    s = _session(body)
    pts, cols = body.get("points") or [], body.get("colours") or []
    err = _check_points(pts, s.total)
    if err:
        return jsonify(error=err), 400 if err.startswith("expected") else 422
    out = _store_maps(s.ctrls, s.offsets, pts, cols, body.get("save", True), "camera")
    return jsonify(ok=True, controllers=out, totals=parameter_summary())


def _views_summary(data):
    if not data:
        return None
    seen = set()
    for v in data["views"]:
        seen |= {g for g, p in enumerate(v["points"]) if p}
    return {"controllers": data["ids"], "total": data["total"], "seen_any": len(seen),
            "views": [{"angle": v["angle"], "found": sum(1 for p in v["points"] if p), "heading": v.get("heading"),
                       "pitch": v.get("pitch"), "moved": v.get("moved", False)} for v in data["views"]]}


def _union(data):
    """Per LED: seen in any view (for the port report) and the colour order most views agree on."""
    total = data["total"]
    pts, cols = [None] * total, [None] * total
    for g in range(total):
        votes = Counter()
        for v in data["views"]:
            if v["points"][g]:
                pts[g] = pts[g] or v["points"][g]
                if v["colours"] and g < len(v["colours"]) and v["colours"][g]:
                    votes[v["colours"][g]] += 1
        cols[g] = votes.most_common(1)[0][0] if votes else None
    return pts, cols


@app.post("/api/mapping/view")
def api_mapping_view():
    """Keep a capture as one view for combining. body: {"session", "angle" (degrees, walking
    to the right around the object), "points", "colours", "heading", "pitch", "moved"}.
    Shows coverage on the LEDs: green = this view, blue = earlier views only, red = not yet."""
    body = request.get_json(force=True) or {}
    s = _session(body)
    pts = body.get("points") or []
    err = _check_points(pts, s.total)
    if err:
        return jsonify(error=err), 400 if err.startswith("expected") else 422
    ids = [c.id for c in s.ctrls]
    data = mapping.load_views()
    replaced = bool(data) and (data["ids"] != ids or data["total"] != s.total)
    if not data or replaced:
        data = {"ids": ids, "total": s.total, "offsets": s.offsets, "views": []}
    before = set()
    for v in data["views"]:
        before |= {g for g, p in enumerate(v["points"]) if p}
    now = {g for g, p in enumerate(pts) if p}
    data["views"].append({"angle": float(body.get("angle") or 0), "points": [p[:2] if p else None for p in pts],
                          "colours": body.get("colours") or [], "heading": body.get("heading"),
                          "pitch": body.get("pitch"), "moved": bool(body.get("moved")), "time": time.time()})
    mapping.save_views(data)
    for c in s.ctrls:
        c.test = {"frame": s.coverage(c, now, before), "until": time.time() + 600, "pattern": "coverage"}
    union, cols = _union(data)
    rep = _store_maps(s.ctrls, s.offsets, union, cols, False, "", partial=True)
    log.info("mapping view %d at %s deg kept by %s: %d LEDs (%d new)", len(data["views"]), body.get("angle"),
             client_ip(), len(now), len(now - before))
    return jsonify(ok=True, replaced=replaced, new=len(now - before), views=_views_summary(data), controllers=rep)


@app.get("/api/mapping/views")
def api_mapping_views():
    return jsonify(_views_summary(mapping.load_views()))


@app.delete("/api/mapping/views")
def api_mapping_views_clear():
    mapping.save_views(None)
    log.info("mapping views discarded by %s", client_ip())
    return jsonify(ok=True)


@app.post("/api/mapping/combine")
def api_mapping_combine():
    """Combine the kept views into one map. body: {"mode": "auto"|"flat"|"around",
    "mirror": false, "save": false}. Returns the preview points (global, x/y/z 0..1), per
    view fit, how many LEDs were seen from several views, and the port report."""
    body = request.get_json(force=True) or {}
    data = mapping.load_views()
    if not data or not data["views"]:
        return jsonify(error="no views kept yet: capture one first"), 400
    with lock:
        ctrls = [CONTROLLERS.get(i) for i in data["ids"]]
    if None in ctrls or sum(c.n_led for c in ctrls) != data["total"]:
        return jsonify(error="the controllers changed since these views were taken: discard them and capture again"), 409
    try:
        mode, pts, info, stats = mapping.combine(data["views"], body.get("mode", "auto"), bool(body.get("mirror")))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    _, cols = _union(data)
    out = _store_maps(ctrls, data["offsets"], pts, cols, bool(body.get("save")),
                      f"camera {'3D' if mode == 'around' else '2D'}, {len(data['views'])} views")
    return jsonify(ok=True, mode=mode, views=info, stats=stats, points=pts, controllers=out,
                   saved=bool(body.get("save")), totals=parameter_summary())


# ---------------------------------------------------------------- capture photos (diagnosis)
CAPTURE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
CAPTURE_FILE = re.compile(r"^[A-Za-z0-9_]{1,32}\.(png|json)$")
KEEP_CAPTURES = 20


def captures_dir():
    return os.path.join(os.path.dirname(os.path.abspath(state.CONFIG_PATH)), "captures")


def _capture_path(name, fname=None):
    if not CAPTURE_NAME.match(name or "") or (fname is not None and not CAPTURE_FILE.match(fname)):
        abort(make_response(jsonify(ok=False, error="bad capture or file name"), 400))
    d = os.path.join(captures_dir(), name)
    return d if fname is None else os.path.join(d, fname)


@app.post("/api/mapping/captures/<name>/<fname>")
def api_capture_upload(name, fname):
    """The phone sends a capture's photos (PNG) and its notes (capture.json), one file per call."""
    path = _capture_path(name, fname)
    want = "image/png" if fname.endswith(".png") else "application/json"
    if (request.mimetype or "") != want:
        return jsonify(error=f"send {fname} as {want}"), 415
    d = os.path.dirname(path)
    if os.path.isdir(d) and len(os.listdir(d)) >= 60:
        return jsonify(error="too many files in this capture"), 413
    os.makedirs(d, exist_ok=True)
    with open(path, "wb") as f:
        f.write(request.get_data())
    caps = sorted((e for e in os.scandir(captures_dir()) if e.is_dir()), key=lambda e: e.stat().st_mtime)
    for old in caps[:-KEEP_CAPTURES]:                 # keep the latest captures only
        shutil.rmtree(old.path, ignore_errors=True)
    if fname == "capture.json":
        log.info("capture photos %s received from %s", name, client_ip())
    return jsonify(ok=True)


@app.get("/api/mapping/captures")
def api_captures():
    out = []
    if os.path.isdir(captures_dir()):
        for e in sorted(os.scandir(captures_dir()), key=lambda e: e.name, reverse=True):
            if e.is_dir():
                files = os.listdir(e.path)
                out.append({"name": e.name, "files": len(files), "complete": "capture.json" in files,
                            "bytes": sum(os.path.getsize(os.path.join(e.path, f)) for f in files)})
    return jsonify(out)


@app.get("/api/mapping/captures/<name>.zip")
def api_capture_zip(name):
    d = _capture_path(name)
    if not os.path.isdir(d):
        abort(404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(os.listdir(d)):
            z.write(os.path.join(d, f), f"{name}/{f}")
    return Response(buf.getvalue(), mimetype="application/zip",
                    headers={"Content-Disposition": f"attachment; filename=twinkly-capture-{name}.zip"})


@app.delete("/api/mapping/captures/<name>")
def api_capture_delete(name):
    shutil.rmtree(_capture_path(name), ignore_errors=True)
    return jsonify(ok=True)


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
    c.map, c.map_source, c.map_filled = None, "", []
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
                        "group": c.group,
                        "points": [[p[0], p[1], p[2] if len(p) > 2 else 0] for p in (c.map or [])],
                        "filled": c.map_filled or []})
    return jsonify(out)


@app.get("/api/frames")
def api_frames():
    with lock:
        out = {c.id: base64.b64encode(c.last_frame).decode() for c in CONTROLLERS.values() if c.last_frame}
    return jsonify(out)


# ---------------------------------------------------------------- tests
@app.get("/api/patterns")
def api_patterns():
    return jsonify(PATTERNS)


def start_test(c, body):
    """Apply a test body to one controller. Returns an error string or None."""
    mode = body.get("mode", "stop")
    secs = max(0.5, min(3600.0, float(body.get("seconds", 3))))
    rgb = [max(0, min(255, int(v))) for v in (body.get("rgb") or [255, 255, 255])[:3]]
    if mode != "stop" and not c.n_led and not c.connect():
        return c.error
    if mode == "stop":
        c.test = None
    elif mode == "pattern":
        name = body.get("pattern")
        if name not in PATTERNS:
            return f"pattern must be one of {', '.join(PATTERNS)}"
        if name in MAP_PATTERNS and not c.map:
            return f"{c.name} has no map yet (pull one, map it with the camera, or Map from ports)"
        c.test = {"pattern": name, "params": {"rgb": rgb, "speed": float(body.get("speed") or 25),
                                              "period": float(body.get("period") or 3),
                                              "bounds": body.get("_bounds")},
                  "start": body.get("_start") or time.time(), "until": time.time() + secs}
    elif mode == "pixel":
        c.test = {"frame": c.pixel_frame(int(body.get("index", 0)), rgb), "until": time.time() + secs}
    elif mode == "solid":
        c.test = {"frame": c.solid_frame(rgb), "until": time.time() + secs}
    elif mode == "off":
        c.test = {"frame": bytes(c.n_led * c.bpp), "until": time.time() + secs}
    else:
        return "mode must be pattern, pixel, solid, off or stop"
    return None


@app.post("/api/test")
def api_test_all():
    """body: {"ids": [...] (omit = every enabled controller), plus the per-controller test body}"""
    body = request.get_json(force=True) or {}
    with lock:
        ids = body.get("ids") or [c.id for c in CONTROLLERS.values() if c.enabled]
        ctrls = [CONTROLLERS[i] for i in ids if i in CONTROLLERS]
    # map patterns: one box and one clock for every controller, so a sweep crosses them as one
    body = {**{k: v for k, v in body.items() if not k.startswith("_")}, "_bounds": bounds(ctrls), "_start": time.time()}
    errors = {c.id: e for c in ctrls if (e := start_test(c, body))}
    if body.get("mode") in ("pattern", "solid"):
        log.info("test %s %s on %s by %s", body.get("mode"), body.get("pattern", ""),
                 ", ".join(c.name for c in ctrls) or "nothing", client_ip())
    status = 200 if not errors else 400 if any("must be" in e for e in errors.values()) else 502
    return jsonify(ok=not errors, controllers=len(ctrls), errors=errors), status


@app.post("/api/controllers/<cid>/test")
def api_test(cid):
    """body: {"mode": "pattern"|"pixel"|"solid"|"off"|"stop", "pattern": "chase", "index": 0,
    "rgb": [r,g,b], "speed": 25, "seconds": 3}"""
    c = get_ctrl(cid)
    body = {k: v for k, v in (request.get_json(force=True) or {}).items() if not k.startswith("_")}
    err = start_test(c, body)
    if err:
        return jsonify(error=err), (400 if "must be" in err else 502)
    if body.get("mode") == "pattern":
        log.info("test pattern %s on %s by %s", body.get("pattern"), c.name, client_ip())
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
            # every interface unless HTTPS_HOST says otherwise: it only exists for a phone on the LAN,
            # so following HOST=127.0.0.1 (the run.py default) would make it unreachable
            app.config["HTTPS_PORT"] = tls.start(app, os.environ.get("HTTPS_HOST") or "0.0.0.0",
                                                  int(os.environ.get("HTTPS_PORT", "5121") or 0))
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
