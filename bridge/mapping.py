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
import cmath
import json
import math
import os
import secrets
import threading
import time
from collections import Counter

from . import state
from .patterns import pack

lock = threading.Lock()
SESSION = None                     # the one mapping session in progress


def gray(v):
    return v ^ (v >> 1)


class Session:
    """One capture. Coding frames light LEDs in one channel (green: what phone sensors see
    best) at `code_level`: 255 by default, which isn't dimmed by PWM, so a phone's rolling
    shutter can't catch it half off. The phone may pick a lower level (less bloom and
    clipping) after measuring that it doesn't flicker on its camera. `level` only sets the
    coverage colours shown between captures."""

    def __init__(self, ctrls, level=128):
        self.id = secrets.token_hex(6)
        self.ctrls = list(ctrls)
        self.level = max(8, min(255, int(level)))
        self.code_level = 255
        self.offsets, g = {}, 0
        for c in self.ctrls:
            self.offsets[c.id] = g
            g += c.n_led
        self.total = g
        self.bits = max(1, self.total.bit_length())        # codes 1..total
        # b<k>/i<k>: bit k and its inverse. (No parity pair: in Gray code chain neighbours differ
        # in one bit, so their parity always differs and every pixel between two neighbouring
        # LEDs would fail it - tested: it lost 40% of LEDs on densely packed strings.)
        self.steps = (["black", "white", "red", "green", "blue"]
                      + [s for k in range(self.bits) for s in (f"b{k}", f"i{k}")] + ["white_end"])
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
        base = self.offsets[c.id]
        if step in ("red", "green", "blue"):
            k = ("red", "green", "blue").index(step)
            return pack(c, [tuple(self.code_level if j == k else 0 for j in range(3))] * c.n_led)
        on = [self.lit(base + i, step) for i in range(c.n_led)]
        return pack(c, [(0, self.code_level, 0) if x else (0, 0, 0) for x in on])

    def coverage(self, c, now, before, level=None):
        """What the installation shows between captures: green = found in the capture just
        taken, blue = found only in earlier views, red = not found yet. now / before are sets
        of global LED numbers."""
        v = max(24, (level or self.level) // 2)
        base, px = self.offsets[c.id], []
        for i in range(c.n_led):
            g = base + i
            px.append((0, v, 0) if g in now else (0, 0, v) if g in before else (v, 0, 0))
        return pack(c, px)

    def describe(self):
        return {"session": self.id, "total": self.total, "bits": self.bits, "steps": self.steps,
                "level": self.level, "code_level": self.code_level,
                "controllers": [{"id": c.id, "name": c.name, "n_led": c.n_led, "offset": self.offsets[c.id],
                                 "ports": c.ports} for c in self.ctrls]}


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def _median(v, default=0.0):
    v = sorted(v)
    return v[len(v) // 2] if v else default


def axis_centre(pts, bands=8):
    """Where the vertical axis is, in (x, z): least-squares circles through the LEDs seen from
    above, one per height band (a cone or tree gets narrower towards the top), all sharing one
    centre. Unlike the average position, it isn't pulled sideways when one side of the object
    is hidden. Falls back to the average if the fit is unreasonable."""
    n = len(pts)
    mx, mz = sum(p[0] for p in pts) / n, sum(p[2] for p in pts) / n
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    groups = {}
    for p in pts:                                     # equal height slices
        groups.setdefault(min(bands - 1, int((p[1] - y0) / ((y1 - y0) or 1) * bands)), []).append(p)
    # circle: x2 + z2 + D x + E z + F_band = 0. Removing each band's own F (its radius) leaves
    # a 2x2 least-squares problem for the shared D, E.
    sxx = sxz = szz = bx = bz = 0.0
    for band in groups.values():
        if len(band) < 3:
            continue
        xs = [p[0] - mx for p in band]
        zs = [p[2] - mz for p in band]
        ws = [x * x + z * z for x, z in zip(xs, zs)]
        ax, az, aw = sum(xs) / len(xs), sum(zs) / len(zs), sum(ws) / len(ws)
        for x, z, w in zip(xs, zs, ws):
            x, z, w = x - ax, z - az, w - aw
            sxx += x * x; sxz += x * z; szz += z * z; bx -= x * w; bz -= z * w
    det = sxx * szz - sxz * sxz
    if abs(det) < 1e-15:
        return mx, mz
    D = (bx * szz - bz * sxz) / det
    E = (sxx * bz - sxz * bx) / det
    cx, cz = -D / 2, -E / 2
    spread = max(max(p[0] for p in pts) - min(p[0] for p in pts), max(p[2] for p in pts) - min(p[2] for p in pts)) or 1
    if math.hypot(cx, cz) > spread:                  # not a ring-like object: use the average
        return mx, mz
    return mx + cx, mz + cz


SPLINE_MAX_RUN = 6        # LEDs: a longer hidden run goes straight, the curve has nothing to go on
WRAP_ASPECT = 0.7         # depth extent / width above which the piece is treated as round


def _dist(a, b):
    return math.sqrt(sum((a[k] - b[k]) ** 2 for k in range(3)))


def _spacing(out, ports, n, measured=None):
    """Median distance between chain-neighbours, from pairs whose positions were measured
    (not guessed) where there are enough of them. This is the string's own LED pitch."""
    strong, any_pair = [], []
    for _pid, first, length in (ports or [[0, 0, n]]):
        for i in range(max(0, first), min(n, first + length) - 1):
            a, b = out[i], out[i + 1]
            if a and b:
                d = _dist(a, b)
                any_pair.append(d)
                if measured is None or (measured[i] and measured[i + 1]):
                    strong.append(d)
    return _median(strong if len(strong) >= 20 else any_pair, default=0.0)


def _spline(xs, ys):
    """Second derivatives of the natural cubic spline through (xs, ys), xs increasing
    (Thomas algorithm). Natural ends mean it leaves each end in a straight line."""
    k = len(xs)
    if k < 3:
        return [0.0] * k
    a, b, c, d = [0.0] * k, [1.0] * k, [0.0] * k, [0.0] * k
    for i in range(1, k - 1):
        h0, h1 = xs[i] - xs[i - 1], xs[i + 1] - xs[i]
        a[i], b[i], c[i] = h0 / 6, (h0 + h1) / 3, h1 / 6
        d[i] = (ys[i + 1] - ys[i]) / h1 - (ys[i] - ys[i - 1]) / h0
    for i in range(1, k):
        m = a[i] / b[i - 1] if b[i - 1] else 0.0
        b[i] -= m * c[i - 1]; d[i] -= m * d[i - 1]
    m2 = [0.0] * k
    m2[-1] = d[-1] / b[-1] if b[-1] else 0.0
    for i in range(k - 2, -1, -1):
        m2[i] = (d[i] - c[i] * m2[i + 1]) / b[i] if b[i] else 0.0
    return m2


def _spline_at(xs, ys, m2, x):
    k = len(xs)
    if k == 1:
        return ys[0]
    if x <= xs[0] or x >= xs[-1]:                      # natural ends: carry on straight
        i, j = (0, 1) if x <= xs[0] else (-1, -2)
        h = abs(xs[j] - xs[i])
        slope = (ys[i] - ys[j]) / (xs[i] - xs[j]) + (h * (2 * m2[i] + m2[j]) / 6) * (1 if x >= xs[-1] else -1)
        return ys[i] + slope * (x - xs[i])
    lo, hi = 0, k - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        hi, lo = (mid, lo) if xs[mid] > x else (hi, mid)
    h = xs[hi] - xs[lo]
    A, B = (xs[hi] - x) / h, (x - xs[lo]) / h
    return A * ys[lo] + B * ys[hi] + ((A ** 3 - A) * m2[lo] + (B ** 3 - B) * m2[hi]) * h * h / 6


def _shorten(pa, pb, pts, longest):
    """A string can't stretch: if the filled path from pa through pts to pb is longer than the
    wire between them, slide it back towards the straight line until it fits."""
    def length(ps):
        seq = [pa] + ps + [pb]
        return sum(_dist(seq[k], seq[k + 1]) for k in range(len(seq) - 1))
    if longest <= 0 or length(pts) <= longest * 1.02:
        return pts
    m = len(pts) + 1
    line = [[pa[d] + (j / m) * (pb[d] - pa[d]) for d in range(3)] for j in range(1, m)]
    if length(line) > longest * 1.02:
        return line                                    # even straight is too long: nothing to give
    lo, hi = 0.0, 1.0
    for _ in range(20):
        f = (lo + hi) / 2
        trial = [[line[j][d] + f * (pts[j][d] - line[j][d]) for d in range(3)] for j in range(len(pts))]
        lo, hi = (lo, f) if length(trial) > longest else (f, hi)
    return [[line[j][d] + lo * (pts[j][d] - line[j][d]) for d in range(3)] for j in range(len(pts))]


def fill_gaps(points, ports, n, measured=None, route="auto"):
    """Fill the LEDs the camera didn't see (hidden behind something, out of frame) from the
    ones it did, along each string (port). points: [[x, y(, z)] or None] * n for one
    controller. measured: per LED, was its position worked out from two or more angles.
    Returns (points, number filled). A port with nothing seen is left None.

    Which route the hidden wire took is a guess, and the wrong guess is worse than no guess,
    so it is chosen from the shape the camera actually measured (route="auto"):

    round pieces (a tree, a column: as deep as they are wide) are filled in cylinder
    coordinates around the vertical axis (angle, distance from the axis, height), so a run
    hidden behind the tree goes round the far side rather than through the middle; which way
    round, and how many turns, comes from the string's own spiral rate either side of the gap.

    everything else - stars, signs, panels, anything with a front and a back - is filled
    along a curve through the LEDs that were seen, which assumes only that the string carries
    on smoothly. Assuming a cylinder there would wrap LEDs round a shape that isn't there.
    Short gaps between two measured LEDs get the curve; longer ones, or ones whose ends were
    themselves guessed, go straight, and no filled path is ever longer than the wire it stands
    for. Flat maps: straight between the two ends of the gap.

    Ends of a string (nothing seen after / before): continue the string's local trend - its
    spacing and direction - from the last LEDs seen, no further than the wire allows."""
    out = [list(p) if p else None for p in points[:n]] + [None] * max(0, n - len(points))
    known = [p for p in out if p]
    if not known:
        return out, 0
    three = any(len(p) > 2 and abs(p[2]) > 1e-9 for p in known) and \
        max(p[2] for p in known) - min(p[2] for p in known) > 1e-6
    if not three:
        route = "flat"
    elif route == "auto":
        # Is this piece round, or does it have a front and a back? Judge it on the LEDs whose
        # depth was actually measured: how deep they reach compared with how wide they spread.
        # A tree or column comes out near 1, a star or panel well under half.
        firm = [p for i, p in enumerate(out) if p and (measured is None or measured[i])] or known
        width = (max(p[0] for p in firm) - min(p[0] for p in firm)) or 1e-9
        depth = max(p[2] for p in firm) - min(p[2] for p in firm)
        route = "cylinder" if depth / width >= WRAP_ASPECT else "spline"
    round_ = route == "cylinder"
    cx, cz = axis_centre(known) if round_ else (sum(p[0] for p in known) / len(known), 0.0)
    spacing = _spacing([p + [0.0] * (3 - len(p)) if p else None for p in out], ports, n, measured) \
        if route == "spline" else 0.0

    def to_c(p):                                   # (angle, radius, height) or (x, y)
        return [math.atan2(p[0] - cx, p[2] - cz), math.hypot(p[0] - cx, p[2] - cz), p[1]] if round_ else [p[0], p[1]]

    def from_c(q):
        return [cx + q[1] * math.sin(q[0]), q[2], cz + q[1] * math.cos(q[0])] if round_ else [q[0], q[1], 0.0]

    filled = 0
    for _pid, first, length in (ports or [[0, 0, n]]):
        rng = range(first, min(n, first + length))
        idx = [i for i in rng if out[i]]
        if not idx:
            continue
        C = {i: to_c(out[i]) for i in idx}
        curve = None
        if route == "spline":
            xs = [float(i) for i in idx]
            ys = [[out[i][d] for i in idx] for d in range(3)]
            m2 = [_spline(xs, ys[d]) for d in range(3)]
            curve = lambda i: [_spline_at(xs, ys[d], m2[d], float(i)) for d in range(3)]

        def local_step(around, k=6):
            """Median per-LED change between neighbouring seen LEDs near `around`."""
            pairs = [(i, i + 1) for i in idx if i + 1 in C and abs(i - around) <= 3 * k]
            pairs = sorted(pairs, key=lambda p: abs(p[0] - around))[:k]
            if not pairs:
                return None
            dims = len(C[idx[0]])
            step = []
            for d in range(dims):
                vals = [(_wrap(C[b][d] - C[a][d]) if (round_ and d == 0) else C[b][d] - C[a][d]) for a, b in pairs]
                step.append(_median(vals))
            return step

        # gaps between two seen LEDs
        for a, b in zip(idx, idx[1:]):
            m = b - a
            if m < 2:
                continue
            if route == "spline":
                # the curve only where it has something to go on: a short gap whose two ends
                # were themselves measured. Otherwise straight, which invents nothing.
                ends_ok = measured is None or (measured[a] and measured[b])
                here = [curve(a + j) for j in range(1, m)] if (m - 1 <= SPLINE_MAX_RUN and ends_ok) \
                    else [[out[a][d] + (j / m) * (out[b][d] - out[a][d]) for d in range(3)] for j in range(1, m)]
                for j, q in enumerate(_shorten(out[a], out[b], here, m * spacing), 1):
                    out[a + j] = q
                    filled += 1
                continue
            qa, qb = C[a], C[b]
            delta = [qb[d] - qa[d] for d in range(len(qa))]
            if round_:
                sa, sb = local_step(a), local_step(b)
                rate = _median([s[0] for s in (sa, sb) if s], default=_wrap(qb[0] - qa[0]) / m)
                expect = rate * m
                base = _wrap(qb[0] - qa[0])
                delta[0] = min((base + 2 * math.pi * k for k in range(-3, 4)), key=lambda d: abs(d - expect))
            for j in range(1, m):
                t = j / m
                out[a + j] = from_c([qa[d] + t * delta[d] for d in range(len(qa))])
                filled += 1
        # the ends of the string: carry on the local trend, no further than the wire reaches
        for end, sign, missing in ((idx[0], -1, range(idx[0] - 1, first - 1, -1)),
                                   (idx[-1], 1, range(idx[-1] + 1, rng.stop))):
            step = local_step(end)
            anchor = out[end]
            for k, i in enumerate(missing, 1):
                if step is None:
                    out[i] = from_c(C[end])
                else:
                    q = [C[end][d] + sign * k * step[d] for d in range(len(step))]
                    if round_:
                        q[1] = max(0.0, q[1])
                    out[i] = from_c(q)
                if route == "spline" and spacing > 0:
                    far = _dist(anchor, out[i])
                    if far > k * spacing:
                        f = k * spacing / far
                        out[i] = [anchor[d] + f * (out[i][d] - anchor[d]) for d in range(3)]
                filled += 1
    dims = max(len(p) for p in known)               # hand back as many coordinates as came in
    return [p[:dims] if p else None for p in out], filled


def report(points, colours, ports, n, partial=False, measured=None):
    """Per port: what the camera saw. points: [[x, y] or None] per LED (before gap filling);
    colours: per LED the camera's answer to the red / green / blue frames as a 3-letter
    string, e.g. "RGB" (as sent) or "GRB" (red looked green, green looked red), or None.
    measured: per LED, was its position worked out from two or more angles (else its depth
    was guessed from the string). Returns one dict per port with configured length, LEDs
    found, the last LED seen (the string's real end, 1-based), dark LEDs before it, how many
    LEDs were measured / estimated / never seen, the longest unmeasured run, and the colour
    orders seen. partial: more sides are still to be captured, so unseen LEDs are "not seen
    yet", not a short string."""
    out = []
    for pid, first, length in (ports or [[0, 0, n]]):
        rng = range(first, min(n, first + length))
        seen = [i - first + 1 for i in rng if i < len(points) and points[i]]
        last = max(seen, default=0)
        n_meas = sum(1 for i in rng if i < len(points) and points[i]
                     and (measured[i] if measured and i < len(measured) else True))
        run = longest = 0
        for i in rng:
            ok = i < len(points) and points[i] and (measured[i] if measured and i < len(measured) else True)
            run = 0 if ok else run + 1
            longest = max(longest, run)
        orders = Counter(colours[i] for i in rng if i < len(colours) and colours[i])
        top, top_n = (orders.most_common(1)[0] if orders else (None, 0))
        verdict = []
        if not seen:
            verdict.append("nothing seen on this port")
        else:
            seen_set = set(seen)
            dark = [k for k in range(1, last + 1) if k not in seen_set]
            if partial:
                if len(seen) < length:
                    verdict.append(f"{length - len(seen)} not seen yet")
            else:
                if last < length - 2:
                    verdict.append(f"string ends at LED {last} of {length} configured")
                if len(dark) > max(2, last // 20):
                    verdict.append(f"{len(dark)} LEDs not seen before the end")
        if top and top != "RGB" and top_n >= max(3, sum(orders.values()) // 2):
            verdict.append(f"colours look {top}: red sent, {dict(R='red', G='green', B='blue')[top[0]]} seen")
        out.append({"port": pid, "configured": length, "found": len(seen), "last_seen": last,
                    "dark_before_end": max(0, last - len(seen)), "colour_orders": dict(orders),
                    "measured": n_meas, "estimated": len(seen) - n_meas,
                    "never_seen": len(rng) - len(seen), "longest_unmeasured_run": longest,
                    "colour_order": top, "ok": not verdict, "notes": verdict})
    return out


# ---------------------------------------------------------------- several captures (views)
#
# Each capture is a "view": one (u, v) per LED in that photo (normalised by the photo's
# width, v down) and the angle the phone stood at, walking around the object to the right
# (0 = the first position). Views are kept in instance/mapping_views.json for one set of
# controllers until combined into a map (and kept after, to add more views later).

def views_path():
    return os.path.join(os.path.dirname(os.path.abspath(state.CONFIG_PATH)), "mapping_views.json")


def load_views():
    try:
        with open(views_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_views(data):
    path = views_path()
    if data is None:
        if os.path.exists(path):
            os.remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(data, f)
    os.replace(path + ".tmp", path)


def _seen(view):
    return {g for g, p in enumerate(view["points"]) if p}


def similarity(pairs):
    """Least-squares scale + rotation + shift taking a -> b, as complex (c, t): b ~ c*a + t.
    pairs: [(a, b)] of complex numbers."""
    n = len(pairs)
    ma = sum(a for a, _ in pairs) / n
    mb = sum(b for _, b in pairs) / n
    num = sum((a - ma).conjugate() * (b - mb) for a, b in pairs)
    den = sum(abs(a - ma) ** 2 for a, _ in pairs) or 1e-12
    c = num / den
    return c, mb - c * ma


def combine_flat(views):
    """All views from roughly the same side: fit each onto the view with the most LEDs
    (scale, rotation, shift), then average. Returns (points [[x, y, 0] | None], per-view info)."""
    ref = max(range(len(views)), key=lambda i: len(_seen(views[i])))
    total = len(views[ref]["points"])
    acc = [[] for _ in range(total)]
    info = []
    for i, v in enumerate(views):
        if i == ref:
            c, t = 1, 0
        else:
            shared = [(complex(*v["points"][g][:2]), complex(*views[ref]["points"][g][:2]))
                      for g in _seen(v) & _seen(views[ref])]
            if len(shared) < 3:
                info.append({"angle": v["angle"], "found": len(_seen(v)), "used": False,
                             "note": "fewer than 3 LEDs in common with the main view"})
                continue
            c, t = similarity(shared)
        err = []
        for g, p in enumerate(v["points"]):
            if p:
                z = c * complex(*p[:2]) + t
                acc[g].append(z)
                if i != ref and views[ref]["points"][g]:
                    err.append(abs(z - complex(*views[ref]["points"][g][:2])))
        info.append({"angle": v["angle"], "found": len(_seen(v)), "used": True,
                     "rms": round(math.sqrt(sum(e * e for e in err) / len(err)), 5) if err else 0})
    pts = [None if not a else [(sum(a) / len(a)).real, (sum(a) / len(a)).imag, 0.0] for a in acc]
    return pts, info


BRACKET_MAX = 10          # how far along a string a bracketing neighbour may be, in LEDs
REFINE_LIMIT = 15.0       # degrees a typed angle may be moved by
REFINE_GRID = 1.0         # degrees: the coarse sweep before the parabolic polish
REFINE_GATE = 0.7         # only move an angle if that cuts the view's residual to this share


def combine_around(views, mirror=False, iterations=40, refine=True, ports=None):
    """Views from around the object: work out each LED's 3D position.

    Model (orthographic: stand back and zoom in for best results): in a view at angle t, an
    LED at (x, y, z) appears at u = s*(x*cos t - z*sin t) + cu, v = s*y + cv, with the view's
    own scale s and offsets cu, cv. The first view is the reference (s = 1). LEDs seen from
    two angles get x and z from the two u equations - those are the measured ones, and on the
    3D test rig they are placed on the wrong side of the object essentially never. LEDs seen
    once have their sideways position measured but their depth guessed from where the same
    string was just before and just after them, so they are flagged not measured.
    View scales / offsets are re-fitted each round, and the angles you typed are refined by up
    to REFINE_LIMIT degrees. ports: [(id, first, length)] in global LED order, so a depth is
    only ever borrowed from the same string. Returns (points [[x, y, z] | None], per-view
    info, stats); stats["measured"] is one bool per LED."""
    nv = len(views)
    total = len(views[0]["points"])
    sign = -1 if mirror else 1
    th = [math.radians(sign * float(v["angle"])) for v in views]
    typed = list(th)
    seen = [_seen(v) for v in views]
    U = [[p[0] if p else None for p in v["points"]] for v in views]
    V = [[p[1] if p else None for p in v["points"]] for v in views]
    ref = 0
    s, cu, cv = [1.0] * nv, [0.0] * nv, [0.0] * nv
    cu[ref] = sum(U[ref][g] for g in seen[ref]) / max(1, len(seen[ref]))
    # initial scale / vertical offset of each view from the heights it shares with the reference
    order = sorted(range(nv), key=lambda i: -len(seen[i] & seen[ref]))
    done = {ref}
    for i in order:
        if i in done:
            continue
        best = max(done, key=lambda j: len(seen[i] & seen[j]))
        sh = list(seen[i] & seen[best])
        if len(sh) >= 3:
            ys = [(V[best][g] - cv[best]) / s[best] for g in sh]
            vs = [V[i][g] for g in sh]
            my, mv = sum(ys) / len(ys), sum(vs) / len(vs)
            var = sum((y - my) ** 2 for y in ys) or 1e-12
            s[i] = sum((y - my) * (v - mv) for y, v in zip(ys, vs)) / var or 1.0
            cv[i] = mv - s[i] * my
        cu[i] = sum(U[i][g] for g in seen[i]) / max(1, len(seen[i]))
        done.add(i)

    port_of = [0] * total
    for pid, first, length in (ports or [[0, 0, total]]):
        for g in range(max(0, first), min(total, first + length)):
            port_of[g] = pid

    obs = [[i for i in range(nv) if U[i][g] is not None] for g in range(total)]
    X = [None] * total

    def fit_xz(g, o):
        """Least squares for (x, z) from the views in `o`: (u - cu)/s = x cos t - z sin t."""
        a11 = a12 = a22 = b1 = b2 = 0.0
        for i in o:
            c_, s_ = math.cos(th[i]), -math.sin(th[i])
            r = (U[i][g] - cu[i]) / s[i]
            a11 += c_ * c_; a12 += c_ * s_; a22 += s_ * s_; b1 += c_ * r; b2 += s_ * r
        det = a11 * a22 - a12 * a12
        if len(o) >= 2 and det > 0.05 * len(o) ** 2 * 0.1:
            return (a22 * b1 - a12 * b2) / det, (a11 * b2 - a12 * b1) / det
        return None

    def solve_points():
        for g in range(total):
            o = obs[g]
            if not o:
                X[g] = None
                continue
            y = sum((V[i][g] - cv[i]) / s[i] for i in o) / len(o)
            xz = fit_xz(g, o)
            if xz:
                X[g] = [xz[0], y, xz[1], True]
            else:                                   # one direction only: lateral known, depth not yet
                i = o[0]
                lat = (U[i][g] - cu[i]) / s[i]
                X[g] = [lat * math.cos(th[i]), y, -lat * math.sin(th[i]), False, i]
        # Depth for the LEDs seen from one direction only. The string is continuous, so take
        # the depth from where it was just before this LED and just after, on the same port,
        # and only within BRACKET_MAX LEDs. "The string runs between two points we measured"
        # is a far weaker assumption than a shape: it can't invent a wrap or a second face.
        # With no such pair (a long hidden run) fall back to the nearest measured neighbours.
        good = [g for g in range(total) if X[g] and X[g][3]]
        byport = {}
        for g in good:
            byport.setdefault(port_of[g], []).append(g)
        for g in range(total):
            if not X[g] or X[g][3]:
                continue
            i = X[g][4]
            ax = (math.sin(th[i]), math.cos(th[i]))        # view axis in (x, z): towards the camera
            pool = byport.get(port_of[g]) or good
            if not pool:
                X[g] = [X[g][0], X[g][1], X[g][2], False]
                continue
            depth_of = lambda k: X[k][0] * ax[0] + X[k][2] * ax[1]
            before = [k for k in pool if k < g and g - k <= BRACKET_MAX]
            after = [k for k in pool if k > g and k - g <= BRACKET_MAX]
            if before and after:
                a_, b_ = max(before), min(after)
                t = (g - a_) / (b_ - a_)
                depth = depth_of(a_) + t * (depth_of(b_) - depth_of(a_))
            elif before or after:
                depth = depth_of(max(before) if before else min(after))
            else:
                near = sorted(pool, key=lambda k: abs(k - g))[:6]
                depth = sum(depth_of(k) for k in near) / len(near)
            X[g] = [X[g][0] + depth * ax[0], X[g][1], X[g][2] + depth * ax[1], False]

    def fit_view(i, t, pts, gs):
        """View i's own scale and offsets at angle t, and the mean square error that leaves."""
        c_, s_ = math.cos(t), math.sin(t)
        rows = [(pts[g][0] * c_ - pts[g][2] * s_, pts[g][1], U[i][g], V[i][g]) for g in gs]
        n = len(rows)
        mp = sum(r[0] for r in rows) / n; my = sum(r[1] for r in rows) / n
        mu = sum(r[2] for r in rows) / n; mv = sum(r[3] for r in rows) / n
        num = sum((p - mp) * (u - mu) + (y - my) * (v - mv) for p, y, u, v in rows)
        den = sum((p - mp) ** 2 + (y - my) ** 2 for p, y, u, v in rows) or 1e-12
        sc = num / den
        err = 0.0
        for p, y, u, v in rows:
            du = sc * (p - mp) - (u - mu); dv = sc * (y - my) - (v - mv)
            err += du * du + dv * dv
        return sc, mu - sc * mp, mv - sc * my, err / n

    def refine_angles():
        """Judge each view's angle against a map built WITHOUT that view, re-fitting the view's
        own scale and offsets at every candidate angle. Judged against a map it helped build, a
        view's current angle always looks right, which is why a small local probe stalls."""
        for i in range(nv):
            if i == ref:
                continue
            out = [None] * total                     # leave-one-out map
            for g in seen[i]:
                o = [j for j in obs[g] if j != i]
                if len(o) < 2:
                    continue
                xz = fit_xz(g, o)
                if xz:
                    out[g] = [xz[0], sum((V[j][g] - cv[j]) / s[j] for j in o) / len(o), xz[1]]
            gs = [g for g in seen[i] if out[g]]
            if len(gs) < 8:                          # only two views: nothing to leave out
                out, gs = X, [g for g in seen[i] if X[g]]
            if len(gs) < 8:
                continue
            base = fit_view(i, typed[i], out, gs)[3]
            best_t, best_e = typed[i], base
            k = 1
            while k * REFINE_GRID <= REFINE_LIMIT:
                for d in (k * REFINE_GRID, -k * REFINE_GRID):
                    t = typed[i] + math.radians(d)
                    e = fit_view(i, t, out, gs)[3]
                    if e < best_e:
                        best_t, best_e = t, e
                k += 1
            h = math.radians(REFINE_GRID)            # parabolic polish on the grid minimum
            e0 = fit_view(i, best_t - h, out, gs)[3]
            e2 = fit_view(i, best_t + h, out, gs)[3]
            den = e0 - 2 * best_e + e2
            if den > 0:
                best_t += -0.5 * h * (e2 - e0) / den
            if best_e > REFINE_GATE * base:
                best_t = typed[i]                    # not a clear win: leave the typed angle be
            lim = math.radians(REFINE_LIMIT)
            th[i] = max(typed[i] - lim, min(typed[i] + lim, best_t))

    for it in range(iterations):
        solve_points()
        for i in range(nv):
            if i != ref:
                gs = [g for g in seen[i] if X[g]]
                if len(gs) >= 3:
                    s[i], cu[i], cv[i], _ = fit_view(i, th[i], X, gs)
        if refine and nv >= 2 and it % 5 == 4:
            refine_angles()
    solve_points()
    measured = [bool(p and p[3]) for p in X]
    multi = sum(1 for m in measured if m)
    single = sum(1 for g in range(total) if X[g] and not X[g][3])
    pts = [p[:3] if p else None for p in X]
    info = []
    for i, v in enumerate(views):
        gs = [g for g in seen[i] if X[g]]
        rms = math.sqrt(fit_view(i, th[i], X, gs)[3]) if len(gs) >= 3 else 0.0
        refined = round(sign * math.degrees(th[i]), 1)
        info.append({"angle": v["angle"], "refined_angle": refined, "found": len(seen[i]),
                     "clamped": abs(math.degrees(th[i] - typed[i])) >= REFINE_LIMIT - 0.05,
                     "rms": round(rms, 5)})
    return pts, info, {"multi_view": multi, "single_view": single,
                       "unseen": total - multi - single, "measured": measured}


def normalise(points):
    """Scale so the largest extent is 1: x and z centred on 0.5, y from 0 (top) down."""
    have = [p for p in points if p]
    if not have:
        return points
    lo = [min(p[k] for p in have) for k in range(3)]
    hi = [max(p[k] for p in have) for k in range(3)]
    ext = max(hi[k] - lo[k] for k in range(3)) or 1.0
    mid = [(lo[k] + hi[k]) / 2 for k in range(3)]
    return [None if not p else [round(0.5 + (p[0] - mid[0]) / ext, 5), round((p[1] - lo[1]) / ext, 5),
                                round(0.5 + (p[2] - mid[2]) / ext, 5) if hi[2] > lo[2] else 0.0] for p in points]


def combine(views, mode="auto", mirror=False, ports=None):
    """auto: 'around' when the views' angles differ by 20 degrees or more, else 'flat'.
    ports: [(id, first, length)] in global LED order, used to keep a guessed depth on the
    same string. stats["measured"] is one bool per LED: was its position measured, or
    partly guessed."""
    angles = [float(v["angle"]) % 360 for v in views]
    spread = max((min(abs(a - b), 360 - abs(a - b)) for a in angles for b in angles), default=0)
    if mode == "auto":
        mode = "around" if spread >= 20 else "flat"
    if mode == "around":
        if len(views) < 2:
            raise ValueError("3D needs at least two views from different angles")
        pts, info, stats = combine_around(views, mirror, ports=ports)
    else:
        pts, info = combine_flat(views)
        found = sum(1 for p in pts if p)
        # flat: nothing is guessed, but an LED two cameras agree on is the better measured
        measured = [bool(pts[g]) and sum(1 for v in views if v["points"][g]) > 1 for g in range(len(pts))]
        stats = {"multi_view": sum(1 for m in measured if m), "single_view": 0,
                 "unseen": len(pts) - found, "measured": measured}
        stats["single_view"] = found - stats["multi_view"]
    return mode, normalise(pts), info, stats
