"""Test patterns for proving per-pixel control, rendered per output frame.

render(controller, name, t, params) -> bytes in the controller's wire format
(R G B per LED, or W R G B for RGBW). t is seconds since the pattern started.
"""
import colorsys
import math

PATTERNS = {
    "channels": "Whole chain red, green, blue, then white, 1.5 s each: checks colour order",
    "ports": "Each port its own colour, its first LED white and last LED dim: checks port order and lengths",
    "markers": "LED 0 green, every 100th red, every 50th yellow, every 10th dim white: count along the chain",
    "chase": "One white dot with a tail running down the chain (speed = LEDs per second)",
    "gradient": "White ramp from off to full along each port: checks per-pixel brightness",
    "rainbow": "Scrolling rainbow along the chain: every LED a different colour each frame",
    "solid": "Every LED the chosen colour",
    "sweep_x": "On the map: a bar moving left to right (Sweep time = one pass)",
    "sweep_y": "On the map: a bar moving top to bottom",
    "sweep_z": "On the map: a bar moving front to back (3D maps)",
    "ring": "On the map: a ring growing out from the centre",
    "spin": "On the map: a plane spinning around the vertical axis (3D maps: trees, spheres)",
    "map_rainbow": "On the map: a rainbow across the width, scrolling",
}
# Patterns that place light by position, so they need a map (and say where it is wrong).
MAP_PATTERNS = {"sweep_x", "sweep_y", "sweep_z", "ring", "spin", "map_rainbow"}


def bounds(ctrls):
    """Shared box around the maps of the given controllers, so a sweep crosses them all as one."""
    pts = [p for c in ctrls if c.map for p in c.map[:c.n_led]]
    if not pts:
        return None
    lo = [min(p[k] if len(p) > k else 0 for p in pts) for k in range(3)]
    hi = [max(p[k] if len(p) > k else 0 for p in pts) for k in range(3)]
    return {"lo": lo, "hi": hi}


def _map_pattern(c, name, t, params):
    b = params.get("bounds") or bounds([c])
    if not c.map or not b:
        return bytes(c.n_led * c.bpp)
    lo, hi = b["lo"], b["hi"]
    span = [(hi[k] - lo[k]) or 1e-9 for k in range(3)]
    mid = [(hi[k] + lo[k]) / 2 for k in range(3)]
    period = max(0.2, float(params.get("period") or 3))
    phase = (t / period) % 1.0
    rgb = tuple(max(0, min(255, int(v))) for v in (params.get("rgb") or (255, 255, 255))[:3])
    width = 0.08
    px = []
    for i in range(c.n_led):
        p = c.map[i] if i < len(c.map) else (0, 0, 0)
        x, y, z = p[0], p[1], p[2] if len(p) > 2 else 0
        if name in ("sweep_x", "sweep_y", "sweep_z"):
            k = "xyz".index(name[-1])
            pos = ((x, y, z)[k] - lo[k]) / span[k]
            d = abs(pos - (phase * (1 + 2 * width) - width))
        elif name == "ring":
            r = math.sqrt(sum((((x, y, z)[k] - mid[k]) / (max(span) or 1)) ** 2 for k in range(3))) * 2
            d = abs(r - phase * 1.8)
        elif name == "spin":
            a = (math.atan2(z - mid[2], x - mid[0]) / (2 * math.pi)) % 1.0
            d = min(abs(a - phase), 1 - abs(a - phase)) * 2
        else:                                                   # map_rainbow
            h = ((x - lo[0]) / span[0] + phase) % 1.0
            r, g, bl = colorsys.hsv_to_rgb(h, 1.0, 1.0)
            px.append((int(r * 255), int(g * 255), int(bl * 255)))
            continue
        f = max(0.0, 1 - d / width)
        px.append(tuple(int(v * f) for v in rgb))
    return pack(c, px)


PORT_COLOURS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 160, 0), (255, 0, 255),
                (0, 255, 255), (255, 255, 0), (128, 0, 255)]


def pack(c, pixels, white=None):
    """[(r, g, b), ...] -> wire bytes. RGBW controllers get W first; `white` is an optional
    per-LED W value (else 0)."""
    out = bytearray(c.n_led * c.bpp)
    for i, (r, g, b) in enumerate(pixels[:c.n_led]):
        o = i * c.bpp
        if c.bpp == 4:
            out[o:o + 4] = bytes((white[i] if white else 0, r, g, b))
        else:
            out[o:o + 3] = bytes((r, g, b))
    return bytes(out)


def _ports(c):
    return c.ports or [[0, 0, c.n_led]]


def render(c, name, t, params):
    if name in MAP_PATTERNS:
        return _map_pattern(c, name, t, params)
    n = c.n_led
    rgb = tuple(max(0, min(255, int(v))) for v in (params.get("rgb") or (255, 255, 255))[:3])
    if name == "solid":
        return pack(c, [rgb] * n)
    if name == "channels":
        step = int(t / 1.5) % 4
        if step == 3:                                   # white: the W channel alone on RGBW
            return pack(c, [(0, 0, 0)] * n, [255] * n) if c.bpp == 4 else pack(c, [(255, 255, 255)] * n)
        return pack(c, [tuple(255 if k == step else 0 for k in range(3))] * n)
    if name == "ports":
        px = [(0, 0, 0)] * n
        for j, (_pid, first, length) in enumerate(_ports(c)):
            col = PORT_COLOURS[j % len(PORT_COLOURS)]
            for k in range(length):
                if first + k < n:
                    px[first + k] = col
            if first < n:
                px[first] = (255, 255, 255)
            if 0 < length and first + length - 1 < n:
                px[first + length - 1] = (40, 40, 40)
        return pack(c, px)
    if name == "markers":
        px = [(0, 0, 0)] * n
        for i in range(0, n, 10):
            px[i] = (255, 0, 0) if i % 100 == 0 else (255, 200, 0) if i % 50 == 0 else (40, 40, 40)
        if n:
            px[0] = (0, 255, 0)
        return pack(c, px)
    if name == "chase":
        speed = float(params.get("speed") or 25)
        head = int(t * speed) % max(1, n)
        px = [(0, 0, 0)] * n
        for k in range(12):                             # tail fades over 12 LEDs
            i = head - k
            if 0 <= i < n:
                v = int(255 * (1 - k / 12) ** 2)
                px[i] = (v, v, v) if k else rgb
        return pack(c, px)
    if name == "gradient":
        px = [(0, 0, 0)] * n
        for _pid, first, length in _ports(c):
            for k in range(length):
                if first + k < n:
                    v = round(255 * k / max(1, length - 1))
                    px[first + k] = (v, v, v)
        return pack(c, px)
    if name == "rainbow":
        speed = float(params.get("speed") or 25)
        span = max(1, min(n, 250))
        shift = t * speed
        px = []
        for i in range(n):
            r, g, b = colorsys.hsv_to_rgb(((i + shift) % span) / span, 1.0, 1.0)
            px.append((int(r * 255), int(g * 255), int(b * 255)))
        return pack(c, px)
    raise ValueError(f"unknown pattern {name!r}; one of {', '.join(PATTERNS)}")
