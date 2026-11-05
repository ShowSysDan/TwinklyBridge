#!/usr/bin/env python3
"""Pull the LED map (the coordinates the Twinkly app measured with the camera)
from one Twinkly / Twinkly Pro controller and export it. Windows-friendly, stdlib only.

    py twinkly_layout.py <ip> [name] [--universe U] [--flatten xy|xz|zy|unwrap] [--pixelmap N]

Writes four files next to the script (name defaults to the controller's device name):
    <name>_layout.json     raw reply from led/layout/full
    <name>_layout.csv      index, port, led, x, y, z   (one row per LED, index = chain order)
    <name>_layout.svg      preview, one dot per LED coloured by port - open in any browser
    <name>_eos_pixelmap.csv  Eos Pixel Map CSV for this one controller, patched from --universe
                           channel 1 (170 pixels per universe). For several controllers use the
                           bridge, which exports from its live patch.

Speaks the Pro's /xled/v2/ API and falls back to /xled/v1/ for consumer firmware.
"""
import base64
import csv
import json
import math
import os
import sys
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
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.load(r), p
        except urllib.error.HTTPError as e:
            last = e
            if e.code != 404:
                raise
    raise last


def fetch_layout(ip):
    challenge = base64.b64encode(os.urandom(32)).decode()
    login, _ = _call(ip, ["/xled/v2/login", "/xled/v1/login"], {"challenge": challenge})
    token = login["authentication_token"]
    _call(ip, ["/xled/v2/verify", "/xled/v1/verify"],
          {"challenge-response": login["challenge-response"]}, token)
    gestalt, _ = _call(ip, ["/xled/v2/gestalt", "/xled/v1/gestalt"], token=token)
    layout, path = _call(ip, ["/xled/v2/led/layout/full", "/xled/v1/led/layout/full"], token=token)
    code = layout.get("result", {}).get("code", layout.get("code"))
    if code != 1000:
        raise SystemExit(f"layout call {path} returned code {code}: {layout}")
    return gestalt, layout, path


def port_of(index, ports):
    for pid, (first, n) in ports.items():
        if first <= index < first + n:
            return pid, index - first + 1
    return -1, index + 1


def ports_from_gestalt(g):
    ports, idx = {}, 0
    for port in sorted(g.get("device_config", {}).get("ports", []), key=lambda p: p["port_id"]):
        n = sum(s["len"] for s in port["strings"])
        ports[port["port_id"]] = (idx, n)
        idx += n
    return ports


def write_csv(path, coords, ports):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["index", "port", "led", "x", "y", "z"])
        for i, c in enumerate(coords):
            pid, led = port_of(i, ports)
            w.writerow([i, pid, led, c.get("x", 0), c.get("y", 0), c.get("z", 0)])


def write_svg(path, coords, ports, title):
    palette = ["#e53935", "#43a047", "#1e88e5", "#fdd835", "#d81b60", "#00acc1", "#fb8c00", "#8e24aa"]
    xs = [c.get("x", 0) for c in coords]
    ys = [c.get("y", 0) for c in coords]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    W, H, pad = 1000, 1000 * (y1 - y0 or 1) / (x1 - x0 or 1), 20
    H = max(200, min(H, 3000))
    def sx(x): return pad + (x - x0) / ((x1 - x0) or 1) * (W - 2 * pad)
    def sy(y): return pad + (y - y0) / ((y1 - y0) or 1) * (H - 2 * pad)   # Twinkly y grows downward
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H + 30}" width="{W}" height="{H + 30}">',
           f'<rect width="100%" height="100%" fill="#111"/>',
           f'<text x="{pad}" y="{H + 20}" fill="#ccc" font-family="sans-serif" font-size="14">{title}</text>']
    for i, c in enumerate(coords):
        pid, led = port_of(i, ports)
        col = palette[pid % len(palette)] if pid >= 0 else "#fff"
        out.append(f'<circle cx="{sx(c.get("x", 0)):.1f}" cy="{sy(c.get("y", 0)):.1f}" r="2.5" fill="{col}">'
                   f'<title>index {i} / port {pid} led {led}</title></circle>')
        if i % 50 == 0:
            out.append(f'<text x="{sx(c.get("x", 0)) + 4:.1f}" y="{sy(c.get("y", 0)) - 4:.1f}" '
                       f'fill="#aaa" font-family="sans-serif" font-size="9">{i}</text>')
    out.append("</svg>")
    with open(path, "w") as f:
        f.write("\n".join(out))


def flatten_points(pts, mode):
    """3D -> 2D. xy: front. xz: top. zy: side. unwrap: angle around the vertical axis -> X."""
    if mode == "xz":
        return [(p[0], p[2]) for p in pts]
    if mode == "zy":
        return [(p[2], p[1]) for p in pts]
    if mode == "unwrap":
        cx = sum(p[0] for p in pts) / len(pts)
        cz = sum(p[2] for p in pts) / len(pts)
        return [((math.atan2(p[2] - cz, p[0] - cx) + math.pi) / (2 * math.pi), p[1]) for p in pts]
    return [(p[0], p[1]) for p in pts]


def grid_place(fpts, max_cells=200):
    """Snap 2D points to integer cells, one LED per cell; grows until <1% collide, then
    moves stragglers to the nearest free cell. Returns (W, H, cells{i: (col, row)}, nudged)."""
    xs = [p[0] for p in fpts]
    ys = [p[1] for p in fpts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    aspect = ((x1 - x0) or 1e-9) / ((y1 - y0) or 1e-9)
    n = len(fpts)
    size = max(8, int(math.sqrt(n) * 1.5))
    while True:
        W = max(2, min(max_cells, int(round(size * math.sqrt(aspect)))))
        H = max(2, min(max_cells, int(round(size / math.sqrt(aspect)))))
        taken, cells, nudged = {}, {}, 0
        for i, (x, y) in enumerate(fpts):
            col = int(round((x - x0) / ((x1 - x0) or 1) * (W - 1)))
            row = int(round((y - y0) / ((y1 - y0) or 1) * (H - 1)))
            if (col, row) in taken:
                nudged += 1
                placed = False
                for rad in range(1, max(W, H)):
                    for dr in range(-rad, rad + 1):
                        for dc in range(-rad, rad + 1):
                            rr, cc = row + dr, col + dc
                            if 0 <= rr < H and 0 <= cc < W and (cc, rr) not in taken:
                                col, row, placed = cc, rr, True
                                break
                        if placed:
                            break
                    if placed:
                        break
            taken[(col, row)] = i
            cells[i] = (col, row)
        if nudged * 100 <= n or W >= max_cells or H >= max_cells:
            return W, H, cells, nudged
        size = int(size * 1.3)


def write_eos_csv(path, coords, bpp, universe, flatten, pixelmap, manufacturer, fixture_type):
    """Eos Pixel Map CSV for one controller patched at universe/1, 170 pixels per universe
    (510 channels), addresses absolute at 512 per universe as Eos expects."""
    W, H, cells, nudged = grid_place(flatten_points(coords, flatten))
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["START_PIXELMAP"])
        w.writerow(["PIXELMAP", "LOCATION_X", "LOCATION_Y", "ADDRESS", "MANUFACTURER", "FIXTURE_TYPE"])
        for i in range(len(coords)):
            a = i * bpp
            u, s = a // 510 + universe, a % 510
            col, row = cells[i]
            w.writerow([pixelmap, col + 1, row + 1, (u - 1) * 512 + s + 1, manufacturer, fixture_type])
        w.writerow(["END_PIXELMAP"])
    return W, H, nudged


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opts = {a.lstrip("-").split("=")[0]: (a.split("=", 1)[1] if "=" in a else sys.argv[sys.argv.index(a) + 1])
            for a in sys.argv[1:] if a.startswith("--")}
    args = [a for a in args if a not in opts.values()]
    if not args:
        print(__doc__)
        sys.exit(1)
    ip = args[0]
    gestalt, layout, path = fetch_layout(ip)
    coords = layout.get("coordinates", [])
    source = layout.get("source", "?")
    ports = ports_from_gestalt(gestalt)
    name = args[1] if len(args) > 1 else (gestalt.get("device_name") or "twinkly")
    name = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in name) or "twinkly"
    print(f"{gestalt.get('product_code', gestalt.get('product_name'))}  "
          f"leds={gestalt.get('number_of_led')}  layout source={source}  "
          f"coordinates={len(coords)}  synthesized={layout.get('synthesized')}  via {path}")
    if not coords:
        raise SystemExit("no coordinates - run the camera mapping in the Twinkly app first")
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, name)
    with open(out + "_layout.json", "w") as f:
        json.dump(layout, f, indent=1)
    write_csv(out + "_layout.csv", coords, ports)
    write_svg(out + "_layout.svg", coords, ports, f"{name}: {len(coords)} LEDs, source {source}")
    profile = gestalt.get("led_profile") or gestalt.get("device_config", {}).get("led_profile", "RGB")
    pts = [[c.get("x", 0), c.get("y", 0), c.get("z", 0)] for c in coords]
    W, H, collisions = write_eos_csv(out + "_eos_pixelmap.csv", pts, 4 if profile == "RGBW" else 3,
                                     int(opts.get("universe", 1)), opts.get("flatten", "xy"),
                                     int(opts.get("pixelmap", 1)), opts.get("manufacturer", "Generic"),
                                     opts.get("fixture_type", profile))
    xs = [c.get("x", 0) for c in coords]; ys = [c.get("y", 0) for c in coords]; zs = [c.get("z", 0) for c in coords]
    print(f"x {min(xs):.3f}..{max(xs):.3f}   y {min(ys):.3f}..{max(ys):.3f}   z {min(zs):.3f}..{max(zs):.3f}")
    print(f"wrote {out}_layout.json / .csv / .svg and {out}_eos_pixelmap.csv "
          f"({W}x{H} grid{', ' + str(collisions) + ' LEDs nudged to a free cell' if collisions else ''})")
