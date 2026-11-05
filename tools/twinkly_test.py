#!/usr/bin/env python3
"""Twinkly / Twinkly Pro local-UDP test tool. Windows-friendly, stdlib only.

    py twinkly_test.py discover [broadcast-address]
    py twinkly_test.py info <device-ip>
    py twinkly_test.py rt <device-ip>
    py twinkly_test.py rt-http <device-ip> [R G B]

discover : broadcast the 0x01"discover" probe to UDP 5555 and list replies.
           With several adapters (Wi-Fi + Ethernet + VPN) pass your subnet's
           directed broadcast, e.g.  discover 10.201.115.255
info     : print product, firmware, LED layout and the protocol versions the
           controller advertises (no login needed).
rt       : log in, switch the controller to "rt" mode and stream
           red -> green -> blue frames to UDP 7777 (v3 datagram format,
           confirmed against a capture of the iOS app on Pro fw 4.1.0).
rt-http  : same login + mode switch, then push ONE solid frame with
           POST /xled/v2/led/rt/frame (no UDP involved) - handy for
           proving the mode works if UDP is being filtered.

Pro controllers (fw 4.x) expose the REST API under /xled/v2/; older
consumer firmware uses /xled/v1/. Every call below tries v2 first and falls
back to v1, so the same script works on both.
"""
import base64
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

# Never route LAN calls through a system-configured proxy (Windows reads the
# IE/registry proxy settings by default, which can swallow 10.x.x.x requests).
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))


# ---------------------------------------------------------------- discovery
def discover(bcast="255.255.255.255", wait=3.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(wait)
    s.sendto(b"\x01discover", (bcast, 5555))
    print(f"probe sent to {bcast}:5555, waiting {wait:.0f}s ...")
    found, deadline = 0, time.time() + wait
    while time.time() < deadline:
        try:
            data, (addr, _) = s.recvfrom(1024)
        except socket.timeout:
            break
        except ConnectionResetError:
            # Windows quirk: some host replied with ICMP port-unreachable to the
            # broadcast; Windows surfaces that here. Ignore it and keep listening.
            continue
        # reply = 4 bytes IP | 2-byte marker | device id | 0x00
        # consumer firmware: IP reversed, marker "OK"
        # Pro firmware:      IP in normal order, marker "TP"
        ip_fwd = ".".join(str(b) for b in data[0:4])
        ip_rev = ".".join(str(b) for b in data[3::-1])
        reported = ip_fwd if ip_fwd == addr else ip_rev
        marker = data[4:6].decode(errors="replace")
        dev_id = data[6:].split(b"\x00", 1)[0].decode(errors="replace")
        print(f"  {addr:15}  id={dev_id}  marker={marker}  (reports {reported})")
        found += 1
    print(f"{found} device(s) replied")
    if not found:
        print("hint: check Windows Firewall allowed python.exe, and that this PC "
              "is on the same subnet as the controller")


# ---------------------------------------------------------------- HTTP helpers
def _request(url, body=None, token=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("X-Auth-Token", token)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def call(ip, paths, body=None, token=None):
    """Try each path in order (v2 first, then v1); return first non-404 reply."""
    last = None
    for p in paths:
        try:
            result = _request(f"http://{ip}{p}", body, token)
            call.last_path = p
            return result
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:
                continue
            raise
    raise last


# ---------------------------------------------------------------- info
def info(ip):
    try:
        i = call(ip, ["/xled/info"])
        print(f"product : {i.get('product_name')} ({i.get('product_code')})  "
              f"hw v{i.get('hardware_version')}  api {i.get('api', {}).get('version')}")
        for p in i.get("protocols", []):
            print(f"  protocol {p['name']:10} version {p['version']:5} (min {p['min_version']})")
    except urllib.error.HTTPError:
        print("/xled/info not present (consumer firmware)")
    g = call(ip, ["/xled/v2/gestalt", "/xled/v1/gestalt"])
    print(f"name    : {g.get('device_name')}   leds={g.get('number_of_led')}  "
          f"profile={g.get('led_profile') or g.get('device_config', {}).get('led_profile')}")
    for port in g.get("device_config", {}).get("ports", []):
        print(f"  port {port['port_id']}: " + ", ".join(
            f"start {s['start']} len {s['len']}" for s in port["strings"]))
    for k in ("osc_en", "artnet_en", "rest_locked", "frame_rate", "group"):
        if k in g:
            print(f"  {k} = {g[k]}")
    try:
        fw = call(ip, ["/xled/v2/fw/ct1/version", "/xled/v1/fw/version"])
        print(f"firmware: {fw.get('version')}  {fw.get('desc', '')}")
    except urllib.error.HTTPError as e:
        print(f"firmware: (needs auth, HTTP {e.code})")


# ---------------------------------------------------------------- real-time
def _login_and_rt(ip):
    """Login + verify, read LED layout, switch to rt. Returns (token, n_led, bpp)."""
    challenge = base64.b64encode(os.urandom(32)).decode()
    login = call(ip, ["/xled/v2/login", "/xled/v1/login"], {"challenge": challenge})
    token = login["authentication_token"]
    login_path = call.last_path
    call(ip, ["/xled/v2/verify", "/xled/v1/verify"],
         {"challenge-response": login["challenge-response"]}, token)
    print(f"logged in via {login_path}, token ok")

    g = call(ip, ["/xled/v2/gestalt", "/xled/v1/gestalt"], token=token)
    n_led = g["number_of_led"]
    profile = g.get("led_profile") or g.get("device_config", {}).get("led_profile", "RGB")
    bpp = 4 if profile == "RGBW" else 3
    print(f"{g.get('product_code', g.get('product_name'))}  leds={n_led}  profile={profile}  "
          f"frame={n_led * bpp} bytes")

    r = call(ip, ["/xled/v2/application/mode", "/xled/v1/led/mode"], {"mode": "rt"}, token)
    print("set mode rt ->", r)
    return token, n_led, bpp


def realtime_http(ip, rgb=(255, 0, 0)):
    token, n_led, bpp = _login_and_rt(ip)
    led = (bytes([0]) + bytes(rgb)) if bpp == 4 else bytes(rgb)
    frame = led * n_led
    req = urllib.request.Request(f"http://{ip}/xled/v2/led/rt/frame", data=frame, method="POST",
                                 headers={"Content-Type": "application/octet-stream",
                                          "X-Auth-Token": token})
    with urllib.request.urlopen(req, timeout=5) as r:
        print(f"POST rt/frame ({len(frame)} bytes, colour {rgb}) ->", json.load(r))


def realtime(ip):
    token, n_led, bpp = _login_and_rt(ip)

    # 4. v3 datagram: 0x03 | 8 raw token bytes | 0x00 0x00 | fragment# | <=900 B
    raw_token = base64.b64decode(token)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send_frame(frame: bytes):
        for i in range(0, len(frame), 900):
            hdr = b"\x03" + raw_token + b"\x00\x00" + bytes([i // 900])
            sock.sendto(hdr + frame[i:i + 900], (ip, 7777))
            time.sleep(0.002)          # the app paces fragments ~6 ms apart

    for rgb in [(255, 0, 0), (0, 255, 0), (0, 0, 255)]:
        led = (bytes([0]) + bytes(rgb)) if bpp == 4 else bytes(rgb)  # RGBW = W,R,G,B
        print("sending", rgb)
        for _ in range(40):            # ~2 s at 20 fps
            send_frame(led * n_led)
            time.sleep(0.05)

    print("done - controller falls back to its previous mode after a few seconds of silence")


# ---------------------------------------------------------------- main
if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["discover"]:
        discover(*args[1:2])
    elif args[:1] == ["info"] and len(args) == 2:
        info(args[1])
    elif args[:1] == ["rt"] and len(args) == 2:
        realtime(args[1])
    elif args[:1] == ["rt-http"] and len(args) in (2, 5):
        realtime_http(args[1], tuple(int(v) for v in args[2:5]) or (255, 0, 0))
    else:
        print(__doc__)
        sys.exit(1)
