"""Turn controller maps into a 2D pixel grid, with each LED's Eos address attached.

Used by the Eos pixel-map export, the patch sheet, the preview and the auto-patcher.
Coordinates: Twinkly's y grows downward, which matches both Eos (1,1 = top-left) and the
way the grid is built here (row 0 at the top).
"""
import math

from .state import CONFIG, CONTROLLERS, lock

EOS_UNIVERSE = 512                 # Eos absolute address = (universe - 1) * 512 + offset


def _axis(pts):
    """Centre of the vertical axis (x, z) - the same fit gap filling uses - and the median
    distance from it."""
    from .mapping import axis_centre
    cx, cz = axis_centre(pts)
    r = sorted(math.hypot(p[0] - cx, p[2] - cz) for p in pts)[len(pts) // 2] or 1.0
    return cx, cz, r


def flatten_points(pts, mode):
    """3D -> 2D for the Eos pixel map (Eos maps are flat; the bridge keeps the 3D map).
    xy: front. xz: top. zy: side.
    faces: a flat-ish piece (a star, a sign, a panel) laid out in its OWN plane, so its shape
        survives; whatever is on the back goes beside it as a second region rather than folded
        on top of the front. Use this for anything plate-shaped.
    unwrap: a cylinder or cone (tree, column) cut down the back and opened out: across = the
        way round the object as seen from the front (front in the middle, left on the left),
        scaled by the median radius so the grid has the object's proportions; down = height.
        It unrolls around the VERTICAL AXIS, so it is wrong for a flat piece standing upright -
        every LED is at much the same angle and the shape collapses into two edge strips.
    cone: a cone unrolled exactly into a fan, apex at the tip: distances along the surface
        stay true, so content drawn in Eos isn't stretched near the top. Uses more cells.
    sphere: longitude across, latitude down (balls).
    auto: `faces` for a plate-shaped piece, `unwrap` for a round one - see shape_of()."""
    if mode == "auto":
        mode = shape_of(pts)
    if mode == "xz":
        return [(p[0], p[2]) for p in pts]
    if mode == "zy":
        return [(p[2], p[1]) for p in pts]
    if mode == "faces":
        cu, cv, ax, az = _plane(pts)
        u = [(p[0] - cu) * ax + (p[2] - cv) * az for p in pts]        # across the piece
        d = [-(p[0] - cu) * az + (p[2] - cv) * ax for p in pts]       # depth, + is one side
        span = (max(u) - min(u)) or 1.0
        cut = _two_layers(d)
        if cut is None:                             # one sheet (tinsel wobbles in depth, but
            return list(zip(u, [p[1] for p in pts]))    # there is no second face): don't split it
        # two layers: the bigger one is the front, the other goes beside it. Left and right stay
        # as they are in the room - the back is drawn as if seen THROUGH the piece - so a sweep
        # across the map crosses both faces the same way round.
        back = 1 if sum(1 for x in d if x > cut) < sum(1 for x in d if x <= cut) else -1
        gap = 0.12 * span
        return [(x + (span + gap if ((dd - cut) * back > 0) else 0), p[1])
                for x, dd, p in zip(u, d, pts)]
    if mode in ("unwrap", "cone", "sphere"):
        cx, cz, r = _axis(pts)
        ang = [math.atan2(p[0] - cx, p[2] - cz) for p in pts]          # 0 = front, + = to the right
        if mode == "unwrap":
            return [(a * r, p[1]) for a, p in zip(ang, pts)]
        if mode == "sphere":
            cy = sum(p[1] for p in pts) / len(pts)
            return [(a / (2 * math.pi), math.atan2(p[1] - cy, math.hypot(p[0] - cx, p[2] - cz)) / math.pi + 0.5)
                    for a, p in zip(ang, pts)]
        # cone: apex above the top LED on the axis; slant distance s from the apex, and the
        # fan's opening = 2*pi * sin(half angle) with sin(half angle) = radius / slant
        rad = [math.hypot(p[0] - cx, p[2] - cz) for p in pts]
        # apex: where the radius, fitted as a straight line against height, reaches 0 (a tree's
        # tip is usually above its top LED); no taper (a cylinder) -> the top LED
        top = min(p[1] for p in pts)
        my, mr = sum(p[1] for p in pts) / len(pts), sum(rad) / len(rad)
        vy = sum((p[1] - my) ** 2 for p in pts)
        slope = sum((p[1] - my) * (r - mr) for p, r in zip(pts, rad)) / vy if vy else 0.0
        if slope > 1e-6:                            # wider further down (y grows downward)
            top = min(top, my - mr / slope)
        slant = [math.hypot(p[1] - top, rr) for p, rr in zip(pts, rad)]
        ratios = sorted(rr / sl for rr, sl in zip(rad, slant) if sl > 1e-9)
        k = ratios[len(ratios) // 2] if ratios else 1.0
        return [(sl * math.sin(a * k), sl * math.cos(a * k)) for a, sl in zip(ang, slant)]
    return [(p[0], p[1]) for p in pts]


def _plane(pts):
    """The vertical plane the piece mostly lies in: its centre in x / z and the horizontal
    direction along it. Up stays up, so only the x-z spread matters and it is a 2x2 problem."""
    n = len(pts)
    cx = sum(p[0] for p in pts) / n
    cz = sum(p[2] for p in pts) / n
    sxx = sum((p[0] - cx) ** 2 for p in pts)
    szz = sum((p[2] - cz) ** 2 for p in pts)
    sxz = sum((p[0] - cx) * (p[2] - cz) for p in pts)
    # larger eigenvalue of [[sxx, sxz], [sxz, szz]] and its direction
    tr, det = sxx + szz, sxx * szz - sxz * sxz
    lam = tr / 2 + math.sqrt(max(0.0, tr * tr / 4 - det))
    ax, az = (lam - szz, sxz) if abs(sxz) > 1e-12 else ((1.0, 0.0) if sxx >= szz else (0.0, 1.0))
    L = math.hypot(ax, az) or 1.0
    return cx, cz, ax / L, az / L


def _two_layers(d, min_share=0.2, min_gap=0.35):
    """Is this piece two separated faces, or one sheet that happens to wobble in depth?
    Looks for a clear empty band across the depths with a decent share of LEDs either side.
    Returns the depth to split at, or None for a single sheet."""
    s = sorted(d)
    n = len(s)
    if n < 20:
        return None
    span = (s[int(0.98 * (n - 1))] - s[int(0.02 * (n - 1))]) or 1e-12
    lo, hi = int(min_share * n), int((1 - min_share) * n)
    best, at = 0.0, None
    for i in range(max(1, lo), min(n - 1, hi)):
        g = s[i + 1] - s[i]
        if g > best:
            best, at = g, (s[i] + s[i + 1]) / 2
    return at if best > min_gap * span else None


def shape_of(pts):
    """"faces" for a plate-shaped piece, "unwrap" for a round one. A flat piece has almost all
    its horizontal spread along one direction; a cylinder or cone has it in both equally."""
    if len(pts) < 8:
        return "faces"
    cx, cz, ax, az = _plane(pts)
    along = [(p[0] - cx) * ax + (p[2] - cz) * az for p in pts]
    across = [-(p[0] - cx) * az + (p[2] - cz) * ax for p in pts]
    a = _spread(along)
    b = _spread(across)
    return "unwrap" if a > 1e-9 and b / a > 0.45 else "faces"


def _spread(v):
    """Robust width: the middle 90% of the values."""
    s = sorted(v)
    if not s:
        return 0.0
    return s[int(0.95 * (len(s) - 1))] - s[int(0.05 * (len(s) - 1))]


def grid_place(fpts, max_cells=200, width=0):
    """Snap 2D points to integer cells, one LED per cell. Returns (W, H, cells, nudged) with
    cells[i] = (col, row), 0-based. Grows the grid until fewer than 1% of LEDs collide, then
    moves the stragglers to the nearest free cell."""
    xs = [p[0] for p in fpts]
    ys = [p[1] for p in fpts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    # Already on a whole-number grid with one LED per cell (Map from ports, or a CSV in cell
    # units): place it exactly instead of rescaling.
    if not width and all(float(v).is_integer() for v in xs + ys):
        W, H = int(x1 - x0) + 1, int(y1 - y0) + 1
        cells = {i: (int(x - x0), int(y - y0)) for i, (x, y) in enumerate(fpts)}
        if W <= 2000 and H <= 2000 and len(set(cells.values())) == len(cells):
            return W, H, cells, 0
    aspect = ((x1 - x0) or 1e-9) / ((y1 - y0) or 1e-9)
    n = len(fpts)
    size = width if width else max(8, int(math.sqrt(n) * 1.5))
    while True:
        if width:
            W = max(2, width)
            H = max(2, min(max_cells, int(round(W / aspect))))
        else:
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
        if width or nudged * 100 <= n or W >= max_cells or H >= max_cells:
            return W, H, cells, nudged
        size = int(size * 1.3)


def mapped_controllers():
    cpu = CONFIG["sacn"]["channels_per_universe"]
    with lock:
        ctrls = [c for c in CONTROLLERS.values() if c.map and c.n_led]
    ctrls.sort(key=lambda c: (0, c.abs_start(cpu)) if c.source == "sacn" else (1, c.ddp_start))
    return ctrls


def parameter_summary():
    """What the mapped controllers cost on the desk, the figures the export will produce.
    A pixel is one parameter per colour (R, G, B = 3; R, G, B, W = 4), and each parameter
    takes one DMX address, so `parameters` is also the number of Eos outputs the pixel map
    patches. Only controllers with a map count, and only as many LEDs as the map covers."""
    cpu = CONFIG["sacn"]["channels_per_universe"]
    per, universes = [], set()
    for c in mapped_controllers():
        pixels = min(c.n_led, len(c.map))
        per.append({"id": c.id, "name": c.name, "pixels": pixels, "bpp": c.bpp,
                    "parameters": pixels * c.bpp, "universes": c.universes(cpu)})
        universes.update(c.universes(cpu))
    return {"controllers": len(per), "pixels": sum(p["pixels"] for p in per),
            "parameters": sum(p["parameters"] for p in per), "universes": len(universes),
            "per_controller": per}


def pixel_rows(params):
    """One row per LED across all mapped controllers.
    params: pixelmap, flatten (xy|xz|zy|unwrap), arrange (shared|tiled|separate), width, max_cells
    Returns (rows, size, straddle). Each row: pixelmap, x, y (1-based), controller, ip, index,
    global_index, port, led, universe, offset, address (Eos absolute), ddp (1-based byte),
    bpp, profile."""
    cpu = CONFIG["sacn"]["channels_per_universe"]
    ctrls = mapped_controllers()
    if not ctrls:
        raise ValueError("no controller has a map yet")
    flatten, arrange = params.get("flatten") or "xy", params.get("arrange") or "shared"
    width, max_cells = int(params.get("width") or 0), int(params.get("max_cells") or 200)
    pm0 = int(params.get("pixelmap") or 1)
    grids = []
    if arrange == "shared":
        allpts = [p for c in ctrls for p in c.map[:c.n_led]]
        W, H, cells, nudged = grid_place(flatten_points(allpts, flatten), max_cells, width)
        k = 0
        for c in ctrls:
            n = min(c.n_led, len(c.map))
            grids.append((c, pm0, {i: cells[k + i] for i in range(n)}))
            k += n
        size = {"W": W, "H": H, "nudged": nudged}
    else:
        xoff, maxh, nud, W, H = 0, 0, 0, 0, 0
        for j, c in enumerate(ctrls):
            W, H, cells, nudged = grid_place(flatten_points(c.map[:c.n_led], flatten), max_cells, width)
            nud += nudged
            if arrange == "separate":
                grids.append((c, pm0 + j, cells))
            else:
                grids.append((c, pm0, {i: (col + xoff, row) for i, (col, row) in cells.items()}))
                xoff += W + 2
                maxh = max(maxh, H)
        size = ({"W": xoff - 2, "H": maxh, "nudged": nud} if arrange == "tiled"
                else {"W": W, "H": H, "nudged": nud})
    rows, straddle, gi = [], 0, 0
    for c, pm, cells in grids:
        start = c.abs_start(cpu)
        for i in range(len(cells)):
            a = start + i * c.bpp
            u, s = a // cpu + 1, a % cpu
            if s + c.bpp > EOS_UNIVERSE:
                straddle += 1
            col, row = cells[i]
            port, led = c.port_of(i)
            rows.append({"pixelmap": pm, "x": col + 1, "y": row + 1, "controller": c.name, "ip": c.ip,
                         "index": i, "global_index": gi, "port": port, "led": led,
                         "universe": u, "offset": s + 1, "address": (u - 1) * EOS_UNIVERSE + s + 1,
                         "ddp": c.ddp_start + i * c.bpp, "bpp": c.bpp, "profile": c.profile})
            gi += 1
    return rows, size, straddle


def auto_patch(mode="aligned", start_universe=1, order="group"):
    """Patch every controller contiguously.
    aligned: each controller starts on a fresh universe (the normal choice for Eos).
    packed:  controller B starts right after A's last channel, no gaps.
    order:   'group' sorts by the controllers' group offset when they report one, else by
             the order they were added. Returns the new patch list."""
    cpu = CONFIG["sacn"]["channels_per_universe"]
    with lock:
        ctrls = [c for c in CONTROLLERS.values() if c.n_led]
        if order == "group":
            ctrls.sort(key=lambda c: (0, int(c.group.get("offset", 0))) if c.group.get("offset") not in (None, "", 0)
                       else (1, 0))
        abs_pos = (max(1, int(start_universe)) - 1) * cpu
        ddp_pos = 1
        result = []
        for c in ctrls:
            if mode == "aligned" and abs_pos % cpu:
                abs_pos += cpu - abs_pos % cpu
            c.universe, c.channel = abs_pos // cpu + 1, abs_pos % cpu + 1
            c.ddp_start = ddp_pos
            c._slices = None
            abs_pos += c.n_led * c.bpp
            ddp_pos += c.n_led * c.bpp
            result.append({"id": c.id, "name": c.name, "universe": c.universe, "channel": c.channel,
                           "universes": c.universes(cpu), "ddp_start": c.ddp_start})
    return result
