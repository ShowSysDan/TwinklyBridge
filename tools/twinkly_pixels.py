#!/usr/bin/env python3
"""Per-pixel real-time control for Twinkly / Twinkly Pro. Windows-friendly, stdlib only.

    py twinkly_pixels.py <ip> ports                 # each port a different colour (confirms index -> port mapping)
    py twinkly_pixels.py <ip> pixel <index> R G B   # light ONE led, hold until Ctrl+C
    py twinkly_pixels.py <ip> chase                 # a bright dot with a tail running down the strand
    py twinkly_pixels.py <ip> rainbow               # scrolling rainbow
    py twinkly_pixels.py <ip> fps [rate]            # moving bar at a fixed rate (default 30) to judge smoothness

Use it from your own code:

    from twinkly_pixels import TwinklyRT
    t = TwinklyRT("10.201.115.204")          # logs in, reads layout, switches to rt mode
    f = t.new_frame()                        # all-black frame, one slot per LED
    f[10] = (255, 0, 0)                      # LED index 10 red  (index = position in the chain, 0-based)
    f[t.port_index(2, 1)] = (0, 0, 255)      # first LED on port 2 blue
    t.send(f)                                # one frame; call again within ~2 s or the
                                             # controller drops back to its previous mode

Frame layout on the wire: LED i -> bytes [3i, 3i+1, 3i+2] = R, G, B (RGBW: W, R, G, B).
Ports are concatenated in port_id order, so with 5 x 250 LEDs, port p LED n (1-based)
is index p*250 + n - 1.
"""
import base64
import colorsys
import json
import math
import os
import socket
import sys
import time
import urllib.error
import urllib.request

urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))


def _call(ip, paths, body=None, token=None):
    last = None
    for p in paths:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://{ip}{p}", data=data,
                                     method="POST" if data else "GET",
                                     headers={"Content-Type": "application/json"})
        if token:
            req.add_header("X-Auth-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last = e
            if e.code != 404:
                raise
    raise last


class TwinklyRT:
    def __init__(self, ip, fragment=900, fragment_gap=0.002):
        """fragment_gap: pause between the UDP fragments of one frame. The official
        app leaves ~6 ms; 2 ms worked on the Pro. Set 0 to burst."""
        self.ip, self.fragment, self.fragment_gap = ip, fragment, fragment_gap
        challenge = base64.b64encode(os.urandom(32)).decode()
        login = _call(ip, ["/xled/v2/login", "/xled/v1/login"], {"challenge": challenge})
        self.token = login["authentication_token"]
        _call(ip, ["/xled/v2/verify", "/xled/v1/verify"],
              {"challenge-response": login["challenge-response"]}, self.token)
        g = _call(ip, ["/xled/v2/gestalt", "/xled/v1/gestalt"], token=self.token)
        self.n_led = g["number_of_led"]
        cfg = g.get("device_config", {})
        self.profile = g.get("led_profile") or cfg.get("led_profile", "RGB")
        self.bpp = 4 if self.profile == "RGBW" else 3
        # port_id -> (first index, length)
        self.ports, idx = {}, 0
        for port in sorted(cfg.get("ports", []), key=lambda p: p["port_id"]):
            n = sum(s["len"] for s in port["strings"])
            self.ports[port["port_id"]] = (idx, n)
            idx += n
        self._raw_token = base64.b64decode(self.token)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.set_rt()

    def set_rt(self):
        return _call(self.ip, ["/xled/v2/application/mode", "/xled/v1/led/mode"],
                     {"mode": "rt"}, self.token)

    def new_frame(self, colour=(0, 0, 0)):
        return [colour] * self.n_led

    def port_index(self, port_id, led_no):
        """Index of LED number led_no (1-based) on a given port."""
        first, n = self.ports[port_id]
        if not 1 <= led_no <= n:
            raise IndexError(f"port {port_id} has {n} LEDs")
        return first + led_no - 1

    def send(self, frame):
        """frame: list of (r,g,b) or (r,g,b,w) tuples, one per LED, or raw bytes."""
        if isinstance(frame, (bytes, bytearray)):
            buf = bytes(frame)
        else:
            if len(frame) != self.n_led:
                raise ValueError(f"frame has {len(frame)} entries, device has {self.n_led} LEDs")
            out = bytearray(self.n_led * self.bpp)
            for i, px in enumerate(frame):
                r, g, b = px[0], px[1], px[2]
                if self.bpp == 4:
                    out[i * 4:i * 4 + 4] = (px[3] if len(px) > 3 else 0, r, g, b)
                else:
                    out[i * 3:i * 3 + 3] = (r, g, b)
            buf = bytes(out)
        for i in range(0, len(buf), self.fragment):
            hdr = b"\x03" + self._raw_token + b"\x00\x00" + bytes([i // self.fragment])
            self._sock.sendto(hdr + buf[i:i + self.fragment], (self.ip, 7777))
            if self.fragment_gap:
                time.sleep(self.fragment_gap)

    def hold(self, frame, interval=0.5):
        """Keep re-sending a static frame so the controller stays in rt mode."""
        print("holding frame, Ctrl+C to stop")
        try:
            while True:
                self.send(frame)
                time.sleep(interval)
        except KeyboardInterrupt:
            print("stopped")


# ---------------------------------------------------------------- demos
def demo_ports(t):
    palette = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (255, 0, 255),
               (0, 255, 255), (255, 255, 255)]
    f = t.new_frame()
    for port_id, (first, n) in t.ports.items():
        c = palette[port_id % len(palette)]
        print(f"port {port_id}: index {first}..{first + n - 1} -> {c}")
        for i in range(first, first + n):
            f[i] = c
    t.hold(f)


def demo_pixel(t, index, r, g, b):
    f = t.new_frame()
    f[index] = (r, g, b)
    for port_id, (first, n) in t.ports.items():
        if first <= index < first + n:
            print(f"index {index} = port {port_id}, LED {index - first + 1}")
    t.hold(f)


def demo_chase(t, tail=12, fps=20):
    print("chase, Ctrl+C to stop")
    pos = 0
    try:
        while True:
            f = t.new_frame()
            for k in range(tail):
                i = (pos - k) % t.n_led
                v = int(255 * (1 - k / tail) ** 2)
                f[i] = (v, v, v)
            t.send(f)
            pos = (pos + 1) % t.n_led
            time.sleep(1 / fps)
    except KeyboardInterrupt:
        print("stopped")


def demo_rainbow(t, fps=20, cycles=3):
    print("rainbow, Ctrl+C to stop")
    phase = 0.0
    try:
        while True:
            f = [tuple(int(255 * c) for c in colorsys.hsv_to_rgb((i / t.n_led * cycles + phase) % 1, 1, 1))
                 for i in range(t.n_led)]
            t.send(f)
            phase = (phase + 0.01) % 1
            time.sleep(1 / fps)
    except KeyboardInterrupt:
        print("stopped")


def demo_fps(t, rate=30, seconds=8):
    """A 20-LED bar sweeping the strand at a fixed frame rate: if it stutters or
    stalls, the controller (or your network) can't keep up at that rate."""
    print(f"sweeping bar at {rate} fps for {seconds}s - watch for stutter")
    period, sent, late = 1 / rate, 0, 0
    next_t, end = time.perf_counter(), time.perf_counter() + seconds
    pos = 0
    while time.perf_counter() < end:
        f = t.new_frame()
        for k in range(20):
            f[(pos + k) % t.n_led] = (0, 120, 255)
        t.send(f)
        sent += 1
        pos = (pos + max(1, t.n_led // (rate * 4))) % t.n_led   # one lap every ~4 s
        next_t += period
        delay = next_t - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        else:
            late += 1
    print(f"sent {sent} frames; {late} were late (PC couldn't keep the pace)" if late
          else f"sent {sent} frames, PC kept pace; judge smoothness by eye")


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 2:
        print(__doc__)
        sys.exit(1)
    try:
        t = TwinklyRT(a[0])
    except Exception as e:
        print("could not connect:", e)
        sys.exit(1)
    print(f"{t.n_led} LEDs, {t.profile}, ports {t.ports}")
    cmd = a[1]
    if cmd == "ports":
        demo_ports(t)
    elif cmd == "pixel" and len(a) == 6:
        demo_pixel(t, int(a[2]), int(a[3]), int(a[4]), int(a[5]))
    elif cmd == "chase":
        demo_chase(t)
    elif cmd == "rainbow":
        demo_rainbow(t)
    elif cmd == "fps":
        demo_fps(t, int(a[2]) if len(a) > 2 else 30)
    else:
        print(__doc__)
        sys.exit(1)
