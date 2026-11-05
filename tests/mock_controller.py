"""A fake Twinkly Pro (TWPROCTRLPLC21, fw 4.1.0) built from packet captures of the real
one: v2 REST endpoints on an HTTP port, v3 real-time datagrams on a UDP port. Run it
standalone to point the bridge at 127.0.0.1, or use it from the tests.

    python tests/mock_controller.py            # HTTP on 8080, UDP 7777 (set env MOCK_HTTP_PORT)

Note the bridge always talks to port 80, so for a manual test on a machine where you can
bind port 80, run with MOCK_HTTP_PORT=80 (Linux/macOS need sudo; Windows usually allows it).
"""
import base64
import hashlib
import json
import math
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

TOKEN = base64.b64encode(bytes.fromhex("19b9ff9b3d4e9bd2")).decode()
N_LED, PORTS = 1250, 5


def gestalt():
    return {"product_code": "TWPROCTRLPLC21", "device_name": "C10", "max_supported_led": 1500,
            "number_of_led": N_LED,
            "device_config": {"led_profile": "RGB",
                              "ports": [{"port_id": p, "strings": [{"start": 1, "len": N_LED // PORTS}]} for p in range(PORTS)]},
            "group": {"mode": "none", "uid": "", "size": 0, "offset": 0}, "frame_rate": 9,
            "osc_en": False, "artnet_en": True, "rest_locked": False, "cloud": True, "result": {"code": 1000}}


INFO = {"product_code": "TWPROCTRLPLC21", "product_name": "Twinkly Pro", "hardware_version": 7,
        "mac": "30:49:50:a0:63:09", "api": {"version": "2.11.0", "min_version": "2.10.0"},
        "protocols": [{"name": "discovery", "version": "1", "min_version": "1"},
                      {"name": "streaming", "version": "4", "min_version": "3"},
                      {"name": "osc", "version": "1.4", "min_version": "1.1"},
                      {"name": "artnet", "version": "4", "min_version": "4"}], "result": {"code": 1000}}


def layout():
    coords = []
    for p in range(PORTS):
        for n in range(N_LED // PORTS):
            coords.append({"x": round(n / (N_LED // PORTS - 1), 6), "y": round(0.1 + p * 0.2 + 0.05 * math.sin(n / 12.0), 6),
                           "z": round(0.02 * p, 6)})
    return {"source": "3d", "synthesized": False, "uuid": "00000000-0000-0000-0000-000000000000",
            "coordinates": coords, "result": {"code": 1000}}


class MockState:
    def __init__(self):
        self.mode = {"mode": "movie", "unique_id": "90A9D9A7-479E-49A1-AE2C-B3732E84D99A"}
        self.log = []
        self.frames = 0
        self.bad = 0
        self.parts = {}
        self.complete = b""                 # last fully received rt frame
        self.lock = threading.Lock()

    @property
    def last_frame(self):
        """The last complete frame (a frame still arriving fragment by fragment is not it)."""
        with self.lock:
            return self.complete or b"".join(self.parts[k] for k in sorted(self.parts))


def make_handler(state):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            state.log.append(("GET", self.path))
            if self.path == "/xled/info":
                return self._send(200, INFO)
            if self.path == "/xled/v2/gestalt":
                return self._send(200, gestalt())
            if self.path == "/xled/v2/fw/ct1/version":
                return self._send(200, {"version": "4.1.0", "desc": "Release build", "result": {"code": 1000}})
            if self.path == "/xled/v2/application/mode":
                return self._send(200, {**state.mode, "result": {"code": 1000}})
            if self.path == "/xled/v2/led/layout/full":
                return self._send(200, layout())
            self._send(404, {"result": {"code": 2404}})

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n)
            state.log.append(("POST", self.path, len(body)))
            if self.path == "/xled/v2/login":
                return self._send(200, {"authentication_token": TOKEN, "authentication_token_expires_in": 14400,
                                        "challenge-response": hashlib.sha1(body).hexdigest(), "result": {"code": 1000}})
            if self.headers.get("X-Auth-Token") != TOKEN:
                return self._send(401, {"result": {"code": 1401}})
            if self.path == "/xled/v2/verify":
                return self._send(200, {"result": {"code": 1000}})
            if self.path == "/xled/v2/application/mode":
                state.mode = json.loads(body or b"{}")
                return self._send(200, {"result": {"code": 1000}})
            if self.path == "/xled/v2/led/rt/frame":
                with state.lock:
                    state.parts = {0: body}
                    state.complete = body
                state.frames += 1
                return self._send(200, {"result": {"code": 1000}})
            self._send(404, {"result": {"code": 2404}})
    return H


class MockController:
    def __init__(self, http_port=8080, udp_port=7777, host="127.0.0.1"):
        self.state = MockState()
        self.host, self.http_port, self.udp_port = host, http_port, udp_port
        self.http = HTTPServer((host, http_port), make_handler(self.state))
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.udp.bind((host, udp_port))
        self.udp.settimeout(0.3)
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        threading.Thread(target=self._udp_loop, daemon=True).start()
        return self

    def stop(self):
        self._stop.set()
        self.http.shutdown()
        self.udp.close()

    def _udp_loop(self):
        raw = base64.b64decode(TOKEN)
        while not self._stop.is_set():
            try:
                d, _ = self.udp.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            if d[0] == 3 and d[1:9] == raw and d[9:11] == b"\x00\x00":
                with self.state.lock:
                    if d[11] == 0:
                        self.state.parts = {}
                    self.state.parts[d[11]] = d[12:]
                    if len(self.state.parts) * 900 >= N_LED * 3:
                        self.state.complete = b"".join(self.state.parts[k] for k in sorted(self.state.parts))
                        self.state.frames += 1
            else:
                self.state.bad += 1


if __name__ == "__main__":
    port = int(os.environ.get("MOCK_HTTP_PORT", "8080"))
    m = MockController(http_port=port).start()
    print(f"mock Twinkly Pro: HTTP on {port}, UDP 7777. Ctrl+C to stop.")
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        m.stop()
