"""One Twinkly / Twinkly Pro controller: login, facts, rt mode, frame building, map.

Everything here was confirmed against packet captures of the iOS app talking to a
TWPROCTRLPLC21 on firmware 4.1.0 (see docs/protocol.md). The real-time datagram format was
first documented by the xled project. Pro firmware serves the REST API under /xled/v2/;
consumer firmware under /xled/v1/. Every call tries v2 then v1.
"""
import base64
import csv
import io
import json
import os
import socket
import time
import urllib.error
import urllib.request

from .logs import get
from .state import CONFIG

log = get("controller")

# Never route LAN calls through a system proxy (Windows reads the IE/registry proxy by default).
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

RT_PORT = 7777
FRAGMENT = 900
# While streaming, check this often (s) that the controller is still in rt mode under our login.
# Another client - the Twinkly app is the usual one - logging in makes our token stale (the
# controller then silently drops our UDP frames) or switches it to another mode; 30 s of
# re-asserting is too slow to notice, so look every few seconds and take it back.
RT_CHECK_S = 5

# Read-only URLs Diagnose tries, to find where a firmware keeps its map. led/layout/full is
# the documented one; Pro fw 4.1.0 keeps the app's map in the Twinkly cloud instead (see
# docs/protocol.md, capture 3), so on those this list mostly confirms there is none.
PROBE_PATHS = [f"/xled/{v}/{p}" for v in ("v2", "v1") for p in (
    "led/layout/full", "led/layout", "led/layout/2d", "led/layout/3d", "layout/full", "layout",
    "led/map", "led/mapping", "mapping", "map", "led/layout/full?aspectXY=0", "led/config",
    "summary", "gestalt", "device/config", "led/out/brightness", "movies", "movies/current", "playlist",
)] + ["/xled/info"]


class NotFound(urllib.error.HTTPError):
    """Every path tried answered 404. `attempts` is [(url, reply body snippet), ...]."""

    def __init__(self, attempts):
        self.attempts = attempts
        super().__init__(attempts[-1][0], 404, "Not Found", None, None)

    def __str__(self):
        return "; ".join(f"GET {url} -> 404{f' {body}' if body else ''}" for url, body in self.attempts)


def _snippet(err):
    try:
        return err.read(300).decode("utf-8", "replace").strip()
    except Exception:                                        # noqa: BLE001
        return ""


def api_call(ip, paths, body=None, token=None, raw=None, timeout=5):
    """Try each path in order (v2 first, then v1). Returns (json, path). A 404 falls through to
    the next path; if every path is 404, raises NotFound naming each URL and the reply."""
    attempts = []
    for p in paths:
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(
            f"http://{ip}{p}", data=data, method="POST" if data is not None else "GET",
            headers={"Content-Type": "application/octet-stream" if raw is not None else "application/json"})
        if token:
            req.add_header("X-Auth-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r), p
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            attempts.append((f"http://{ip}{p}", _snippet(e)))
    raise NotFound(attempts)


def result_code(reply):
    return reply.get("result", {}).get("code", reply.get("code"))


class Controller:
    def _fail(self, message):
        """Mark the controller in error; log it once per distinct message, not every retry."""
        if message != self.error or self.status != "error":
            log.warning("%s (%s): %s", self.name, self.ip, message)
        self.status, self.error = "error", message

    def _ok(self, status):
        if self.status == "error":
            log.info("%s (%s) is back: %s", self.name, self.ip, status)
        self.status, self.error = status, ""

    def __init__(self, cfg):
        self.ip = cfg["ip"]                                  # may carry :port for the HTTP side (tests)
        self.host = self.ip.split(":")[0]
        self.id = cfg.get("id") or self.ip.replace(".", "-").replace(":", "_")
        self.name = cfg.get("name") or self.ip
        self.enabled = cfg.get("enabled", True)
        self.source = cfg.get("source", "sacn")             # "sacn" or "ddp"
        self.universe = int(cfg.get("universe", 1))
        self.channel = int(cfg.get("channel", 1))
        self.ddp_start = int(cfg.get("ddp_start", 1))        # 1-based byte offset in the DDP stream
        self.input_order = cfg.get("input_order", "RGBW")   # how W arrives (RGBW controllers only)
        self.n_led = int(cfg.get("n_led", 0))
        self.profile = cfg.get("profile", "RGB")
        self.ports = cfg.get("ports", [])                   # [[port_id, first_index, len], ...]
        self.group = cfg.get("group", {})
        self.product = cfg.get("product", "")
        self.fw = cfg.get("fw", "")
        self.mac = cfg.get("mac", "")
        self.map = cfg.get("map")                            # [[x, y, z], ...] or None
        self.map_source = cfg.get("map_source", "")
        self.map_filled = cfg.get("map_filled", [])         # LEDs never seen: position guessed from the string
        self.map_estimated = cfg.get("map_estimated", [])   # seen from one angle only: depth guessed
        # runtime
        self.map_error = ""                                  # why the last Pull map failed
        self.token = None
        self.raw_token = b""
        self.api = "v2"
        self.status = "idle"
        self.error = ""
        self.prev_mode = None
        self.rt_asserted_at = 0.0
        self.rt_checked_at = 0.0
        self.taken = ""                                      # last time another app took the controller
        self.last_attempt = 0.0
        self.active = False
        self.want_live = False
        self.last_frame = b""
        self.frames_sent = 0
        self.test = None          # {"frame": bytes, "until": t} or {"pattern", "params", "start", "until"}
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._slices = None

    # ---------------------------------------------------------------- presentation
    def to_config(self):
        return {"id": self.id, "ip": self.ip, "name": self.name, "enabled": self.enabled,
                "source": self.source, "universe": self.universe, "channel": self.channel,
                "ddp_start": self.ddp_start, "input_order": self.input_order,
                "n_led": self.n_led, "profile": self.profile, "ports": self.ports, "group": self.group,
                "product": self.product, "fw": self.fw, "mac": self.mac,
                "map": self.map, "map_source": self.map_source, "map_filled": self.map_filled,
                "map_estimated": self.map_estimated}

    def to_status(self):
        cpu = CONFIG["sacn"]["channels_per_universe"]
        return {**{k: v for k, v in self.to_config().items()
                   if k not in ("map", "map_filled", "map_estimated")},
                "map_filled_count": len(self.map_filled or []),
                "map_estimated_count": len(self.map_estimated or []),
                "bpp": self.bpp, "has_map": bool(self.map), "map_points": len(self.map) if self.map else 0,
                "parameters": self.parameters,
                "universes": self.universes(cpu) if self.source == "sacn" else [],
                "ddp_range": [self.ddp_start, self.ddp_start + self.n_led * self.bpp - 1] if self.source == "ddp" and self.n_led else [],
                "status": self.status, "error": self.error, "active": self.active, "map_error": self.map_error,
                "taken": self.taken,
                "frames_sent": self.frames_sent, "api": self.api, "prev_mode": self.prev_mode,
                "testing": self.testing(time.time()),
                "test_pattern": (self.test or {}).get("pattern") or ("frame" if self.test else "")}

    @property
    def bpp(self):
        return 4 if self.profile == "RGBW" else 3

    @property
    def parameters(self):
        """Desk parameters this controller's map adds (one per colour per mapped pixel); 0 without a map."""
        return min(self.n_led, len(self.map)) * self.bpp if self.map and self.n_led else 0

    def abs_start(self, cpu):
        return (self.universe - 1) * cpu + (self.channel - 1)

    def universes(self, cpu):
        if not self.n_led:
            return []
        start = self.abs_start(cpu)
        end = start + self.n_led * self.bpp - 1
        return list(range(start // cpu + 1, end // cpu + 2))

    def port_of(self, index):
        for pid, first, n in self.ports:
            if first <= index < first + n:
                return pid, index - first + 1
        return -1, index + 1

    # ---------------------------------------------------------------- device facts
    def refresh_gestalt(self):
        g, path = api_call(self.ip, ["/xled/v2/gestalt", "/xled/v1/gestalt"], token=self.token)
        self.api = "v2" if "/v2/" in path else "v1"
        self.n_led = int(g.get("number_of_led", self.n_led))
        cfg = g.get("device_config", {})
        self.profile = g.get("led_profile") or cfg.get("led_profile", self.profile)
        self.product = g.get("product_code") or g.get("product_name", "")
        self.group = g.get("group", {}) or {}
        self.mac = g.get("mac", self.mac)
        if g.get("device_name"):
            self.name = g["device_name"]
        ports, idx = [], 0
        for port in sorted(cfg.get("ports", []), key=lambda p: p["port_id"]):
            n = sum(s["len"] for s in port["strings"])
            ports.append([port["port_id"], idx, n])
            idx += n
        self.ports = ports
        try:
            fw, _ = api_call(self.ip, ["/xled/v2/fw/ct1/version", "/xled/v1/fw/version"], token=self.token)
            self.fw = fw.get("version", "")
        except Exception:                                    # noqa: BLE001
            pass
        try:
            info, _ = api_call(self.ip, ["/xled/info"])
            self.mac = info.get("mac", self.mac)
        except Exception:                                    # noqa: BLE001
            pass
        self._slices = None
        return g

    def login(self):
        challenge = base64.b64encode(os.urandom(32)).decode()
        login, path = api_call(self.ip, ["/xled/v2/login", "/xled/v1/login"], {"challenge": challenge})
        self.token = login["authentication_token"]
        self.raw_token = base64.b64decode(self.token)
        api_call(self.ip, ["/xled/v2/verify", "/xled/v1/verify"],
                 {"challenge-response": login["challenge-response"]}, self.token)
        self.api = "v2" if "/v2/" in path else "v1"

    def connect(self):
        """Login, read facts, remember the mode the controller was in."""
        self.last_attempt = time.time()
        try:
            self.login()
            self.refresh_gestalt()
            try:
                m, _ = api_call(self.ip, ["/xled/v2/application/mode", "/xled/v1/led/mode"], token=self.token)
                if m.get("mode") and m["mode"] != "rt":
                    self.prev_mode = {k: m[k] for k in ("mode", "unique_id") if k in m}
            except Exception:                                # noqa: BLE001
                pass
            self._ok("online")
            return True
        except Exception as e:                               # noqa: BLE001
            self.token = None
            self._fail(f"{type(e).__name__}: {e}")
            return False

    def set_mode(self, body):
        r, _ = api_call(self.ip, ["/xled/v2/application/mode", "/xled/v1/led/mode"], body, self.token)
        return result_code(r)

    def ensure_rt(self):
        """Control-thread only (does HTTP). Logs in if needed and (re)asserts rt mode."""
        now = time.time()
        if not self.token:
            if now - self.last_attempt < 5:
                return False
            if not self.connect():
                return False
        if self.rt_asserted_at and now - self.rt_asserted_at <= CONFIG["output"]["rt_reassert_s"] \
                and now - self.rt_checked_at > RT_CHECK_S:
            self.rt_checked_at = now
            try:
                m, _ = api_call(self.ip, ["/xled/v2/application/mode", "/xled/v1/led/mode"], token=self.token, timeout=2)
                if m.get("mode") != "rt":
                    log.warning("%s (%s): switched to %s by something else (the Twinkly app?): taking it back",
                                self.name, self.ip, m.get("mode"))
                    self.taken = f"switched to {m.get('mode')} by another app at {time.strftime('%H:%M:%S')}"
                    self.rt_asserted_at = 0                      # re-assert below, now
            except urllib.error.HTTPError as e:
                if e.code == 401:                                # our login was replaced by another client's
                    log.warning("%s (%s): our login was replaced (the Twinkly app?): logging in again",
                                self.name, self.ip)
                    self.taken = f"login replaced by another app at {time.strftime('%H:%M:%S')}"
                    self.token = None
                    if not self.connect():
                        return False
                    self.rt_asserted_at = 0
            except Exception:                                    # noqa: BLE001 - a missed check is fine
                pass
        if now - self.rt_asserted_at > CONFIG["output"]["rt_reassert_s"]:
            try:
                self.set_mode({"mode": "rt"})
                self.rt_asserted_at = now
                self._ok("streaming")
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    self.token = None
                self._fail(f"set mode rt: HTTP {e.code}")
                return False
            except Exception as e:                           # noqa: BLE001
                self.token = None
                self._fail(f"set mode rt: {e}")
                return False
        return True

    def rt_ready(self, now):
        return bool(self.token) and (now - self.rt_asserted_at) < CONFIG["output"]["rt_reassert_s"] + 15

    def testing(self, now):
        return bool(self.test) and now < self.test["until"]

    def test_frame(self, now):
        """The frame the running test shows now: a fixed frame, or a pattern rendered for this tick."""
        test = self.test
        if not test:
            return None
        if "frame" in test:
            return test["frame"]
        from .patterns import render
        return render(self, test["pattern"], now - test["start"], test["params"])

    def restore_mode(self):
        self.rt_asserted_at = 0.0
        if not self.token:
            return
        try:
            if CONFIG["output"]["restore_mode_on_idle"] and self.prev_mode:
                self.set_mode(self.prev_mode)
            self.status = "online"
        except Exception as e:                               # noqa: BLE001
            self._fail(f"restore mode: {e}")

    # ---------------------------------------------------------------- map
    def pull_map(self):
        if not self.token and not self.connect():
            raise RuntimeError(f"cannot log in to {self.ip}: {self.error}")
        try:
            layout, _ = api_call(self.ip, ["/xled/v2/led/layout/full", "/xled/v1/led/layout/full"],
                                 token=self.token, timeout=15)
        except urllib.error.HTTPError as e:
            if e.code == 401:                                # token expired: log in again, once
                self.token = None
                if not self.connect():
                    raise RuntimeError(f"cannot log in to {self.ip}: {self.error}") from e
                layout, _ = api_call(self.ip, ["/xled/v2/led/layout/full", "/xled/v1/led/layout/full"],
                                     token=self.token, timeout=15)
            elif isinstance(e, NotFound):
                grp = self.group or {}
                in_group = grp.get("mode") not in (None, "", "none")
                raise RuntimeError(
                    f"{self.name} ({self.ip}, {self.product or 'unknown model'} fw {self.fw or '?'}) returned "
                    f"no layout: {e}. Twinkly Pro firmware 4.x keeps the map you see in the Twinkly app in "
                    f"the Twinkly cloud, not on the controller, so there is nothing to pull. Use Map from "
                    f"ports (exact addressing, one row per port) or Upload CSV."
                    + (f" It is in a group (mode {grp.get('mode')}); try Pull map on the group's master."
                       if in_group else "")) from e
            else:
                raise
        if result_code(layout) != 1000:
            raise RuntimeError(f"layout returned code {result_code(layout)}")
        coords = layout.get("coordinates", [])
        self.map = [[c.get("x", 0), c.get("y", 0), c.get("z", 0)] for c in coords]
        self.map_filled = []
        self.map_estimated = []
        self.map_source = f"{layout.get('source', '?')}{' synthesized' if layout.get('synthesized') else ''}"
        return len(self.map)

    def map_from_ports(self):
        """A map from the controller's own port layout: one row per port (in port order, top
        to bottom), its LEDs left to right in chain order, on an integer grid. Not where the
        LEDs physically are, but the pixel order and Eos addressing are exact."""
        if not self.n_led and not self.connect():
            raise RuntimeError(f"cannot log in to {self.ip}: {self.error}")
        ports = self.ports or [[0, 0, self.n_led]]
        pts = [None] * self.n_led
        for row, (_pid, first, n) in enumerate(ports):
            for k in range(n):
                if first + k < self.n_led:
                    pts[first + k] = [k, row, 0]
        self.map = [p or [0, len(ports), 0] for p in pts]
        self.map_source = "ports"
        self.map_filled = []
        self.map_estimated = []
        return len(self.map)

    def probe(self):
        """GET each PROBE_PATHS URL (logged in) and report what answered. Read-only."""
        if not self.token and not self.connect():
            raise RuntimeError(f"cannot log in to {self.ip}: {self.error}")
        out = []
        for path in PROBE_PATHS:
            req = urllib.request.Request(f"http://{self.ip}{path}", headers={"X-Auth-Token": self.token})
            row = {"path": path}
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    body = r.read(2_000_000)
                    row["http"] = r.status
            except urllib.error.HTTPError as e:
                body, row["http"] = e.read(2000), e.code
            except Exception as e:                           # noqa: BLE001
                row["http"], body = None, b""
                row["error"] = f"{type(e).__name__}: {e}"
            row["bytes"] = len(body)
            try:
                j = json.loads(body)
                row["code"] = result_code(j) if isinstance(j, dict) else None
                row["keys"] = sorted(j)[:30] if isinstance(j, dict) else f"list[{len(j)}]"
                if isinstance(j, dict) and isinstance(j.get("coordinates"), list):
                    row["coordinates"] = len(j["coordinates"])
            except ValueError:
                pass
            row["start"] = body[:160].decode("utf-8", "replace")
            out.append(row)
        return {"controller": self.name, "ip": self.ip, "product": self.product, "fw": self.fw,
                "api": self.api, "n_led": self.n_led, "group": self.group, "ports": self.ports,
                "results": out}

    def set_map_from_csv(self, text):
        rows = list(csv.DictReader(io.StringIO(text)))
        pts = {}
        for r in rows:
            try:
                pts[int(r["index"])] = [float(r.get("x", 0)), float(r.get("y", 0)), float(r.get("z", 0) or 0)]
            except (KeyError, ValueError):
                continue
        if not pts:
            raise ValueError("CSV needs columns index, x, y (z optional)")
        n = max(pts) + 1
        self.map = [pts.get(i, [0, 0, 0]) for i in range(n)]
        self.map_source = "csv"
        self.map_filled = []
        self.map_estimated = []
        return n

    # ---------------------------------------------------------------- frames
    def slices(self, cpu):
        """[(universe, slot_start, slot_end), ...] covering this controller's sACN channels."""
        if self._slices is None or self._slices[0] != cpu:
            runs, a = [], self.abs_start(cpu)
            end = a + self.n_led * self.bpp
            while a < end:
                u, s = a // cpu + 1, a % cpu
                take = min(cpu - s, end - a)
                runs.append((u, s, s + take))
                a += take
            self._slices = (cpu, runs)
        return self._slices[1]

    def build_frame_sacn(self, universes, cpu):
        parts = []
        for u, s, e in self.slices(cpu):
            buf = universes.get(u) or b""
            if len(buf) < e:
                buf = buf + bytes(e - len(buf))
            parts.append(buf[s:e])
        return self._reorder(b"".join(parts))

    def build_frame_ddp(self, buffer):
        start = self.ddp_start - 1
        end = start + self.n_led * self.bpp
        chunk = bytes(buffer[start:end])
        if len(chunk) < end - start:
            chunk += bytes(end - start - len(chunk))
        return self._reorder(chunk)

    def _reorder(self, frame):
        """RGBW controllers want W,R,G,B on the wire; reorder from the incoming order."""
        if self.bpp != 4 or self.input_order == "WRGB":
            return frame
        pos = {ch: i for i, ch in enumerate(self.input_order)}
        perm = [pos["W"], pos["R"], pos["G"], pos["B"]]
        out = bytearray(len(frame))
        for i in range(0, len(frame) - 3, 4):
            out[i], out[i + 1], out[i + 2], out[i + 3] = (frame[i + perm[0]], frame[i + perm[1]],
                                                          frame[i + perm[2]], frame[i + perm[3]])
        return bytes(out)

    def fragments(self, frame):
        """v3 real-time datagrams: 0x03 | 8 raw token bytes | 00 00 | fragment# | <=900 bytes."""
        return [b"\x03" + self.raw_token + b"\x00\x00" + bytes([i // FRAGMENT]) + frame[i:i + FRAGMENT]
                for i in range(0, len(frame), FRAGMENT)]

    def send_frame(self, frame):
        gap = CONFIG["output"]["fragment_gap_ms"] / 1000.0
        for d in self.fragments(frame):
            self.sock.sendto(d, (self.host, RT_PORT))
            if gap:
                time.sleep(gap)
        self.last_frame = frame
        self.frames_sent += 1

    def solid_frame(self, rgb, w=0):
        px = bytes([w, *rgb]) if self.bpp == 4 else bytes(rgb)
        return px * self.n_led

    def pixel_frame(self, index, rgb):
        f = bytearray(self.n_led * self.bpp)
        if 0 <= index < self.n_led:
            f[index * self.bpp:index * self.bpp + self.bpp] = bytes([0, *rgb]) if self.bpp == 4 else bytes(rgb)
        return bytes(f)
