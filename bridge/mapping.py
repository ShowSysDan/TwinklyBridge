"""Camera mapping: the bridge lights Gray-coded patterns, a phone photographs each one and
decodes which LED every camera pixel saw (bridge/static/js/mapdecode.js), then sends back
one (x, y) per LED.

Every LED across the controllers in a session gets a global number g (controllers in the
order given, chain order within each). Code value v = g + 1 (0 would be "no LED"), in Gray
code, so LEDs next to each other in the chain - usually next to each other on the wall -
differ in one bit, and a pixel blurred between two of them decodes to one or the other
rather than to a stranger. For each bit k there are two frames: LEDs whose bit k is 1, then
the inverse. Each pair lights the same number of LEDs on average, which keeps phone camera
auto-exposure steady, and the difference A - B gives the bit with its own confidence.

Steps: black, white, red, green, blue, b0, i0, b1, i1, ..., then white_end (to see whether
the camera moved). red / green / blue light every LED in one wire channel, so the phone can
check at each located LED which colour the camera actually saw: a port whose "red" looks
green has its strings in another colour order. Together with which LEDs were found at all,
that gives each port's real string length (report()).
"""
from collections import Counter
import secrets
import threading
import time

from .patterns import pack

lock = threading.Lock()
SESSION = None                     # the one mapping session in progress


def gray(v):
    return v ^ (v >> 1)


class Session:
    def __init__(self, ctrls, level=128):
        self.id = secrets.token_hex(6)
        self.ctrls = list(ctrls)
        self.level = max(8, min(255, int(level)))
        self.offsets, g = {}, 0
        for c in self.ctrls:
            self.offsets[c.id] = g
            g += c.n_led
        self.total = g
        self.bits = max(1, self.total.bit_length())        # codes 1..total
        self.steps = ["black", "white", "red", "green", "blue"] + [s for k in range(self.bits) for s in (f"b{k}", f"i{k}")] + ["white_end"]
        self.started = time.time()

    def lit(self, g, step):
        if step in ("white", "white_end"):
            return True
        if step == "black":
            return False
        bit = (gray(g + 1) >> int(step[1:])) & 1
        return bool(bit) if step[0] == "b" else not bit

    def frame(self, c, step):
        if step not in self.steps:
            raise ValueError(f"unknown step {step!r}")
        base, v = self.offsets[c.id], self.level
        if step in ("red", "green", "blue"):
            k = ("red", "green", "blue").index(step)
            return pack(c, [tuple(v if j == k else 0 for j in range(3))] * c.n_led)
        on = [self.lit(base + i, step) for i in range(c.n_led)]
        return pack(c, [(v, v, v) if x else (0, 0, 0) for x in on],
                    [v if x else 0 for x in on] if c.bpp == 4 else None)

    def describe(self):
        return {"session": self.id, "total": self.total, "bits": self.bits, "steps": self.steps,
                "level": self.level,
                "controllers": [{"id": c.id, "name": c.name, "n_led": c.n_led, "offset": self.offsets[c.id],
                                 "ports": c.ports} for c in self.ctrls]}


def fill_gaps(points, ports, n):
    """points: [[x, y] or None] * n for one controller. Fills each None by linear interpolation
    between the nearest found LEDs on the same port (copies the nearest at a port's ends).
    Returns (filled points, number filled). A port with nothing found is left None."""
    out = [list(p) if p else None for p in points[:n]] + [None] * max(0, n - len(points))
    filled = 0
    for _pid, first, length in (ports or [[0, 0, n]]):
        idx = [i for i in range(first, min(n, first + length)) if out[i]]
        if not idx:
            continue
        for i in range(first, min(n, first + length)):
            if out[i]:
                continue
            before = max((j for j in idx if j < i), default=None)
            after = min((j for j in idx if j > i), default=None)
            if before is None or after is None:
                out[i] = list(out[after if before is None else before])
            else:
                t = (i - before) / (after - before)
                out[i] = [out[before][0] + t * (out[after][0] - out[before][0]),
                          out[before][1] + t * (out[after][1] - out[before][1])]
            filled += 1
    return out, filled


def report(points, colours, ports, n):
    """Per port: what the camera saw. points: [[x, y] or None] per LED (before gap filling);
    colours: per LED the camera's answer to the red / green / blue frames as a 3-letter
    string, e.g. "RGB" (as sent) or "GRB" (red looked green, green looked red), or None.
    Returns one dict per port with configured length, LEDs found, the last LED seen (the
    string's real end, 1-based), dark LEDs before it, and the colour orders seen."""
    out = []
    for pid, first, length in (ports or [[0, 0, n]]):
        rng = range(first, min(n, first + length))
        seen = [i - first + 1 for i in rng if i < len(points) and points[i]]
        last = max(seen, default=0)
        orders = Counter(colours[i] for i in rng if i < len(colours) and colours[i])
        top, top_n = (orders.most_common(1)[0] if orders else (None, 0))
        verdict = []
        if not seen:
            verdict.append("nothing seen on this port")
        else:
            if last < length - 2:
                verdict.append(f"string ends at LED {last} of {length} configured")
            seen_set = set(seen)
            dark = [k for k in range(1, last + 1) if k not in seen_set]
            if len(dark) > max(2, last // 20):
                verdict.append(f"{len(dark)} LEDs not seen before the end")
        if top and top != "RGB" and top_n >= max(3, sum(orders.values()) // 2):
            verdict.append(f"colours look {top}: red sent, {dict(R='red', G='green', B='blue')[top[0]]} seen")
        out.append({"port": pid, "configured": length, "found": len(seen), "last_seen": last,
                    "dark_before_end": max(0, last - len(seen)), "colour_orders": dict(orders),
                    "colour_order": top, "ok": not verdict, "notes": verdict})
    return out
