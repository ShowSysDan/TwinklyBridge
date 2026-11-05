"""Turn controller maps into a 2D pixel grid, with each LED's Eos address attached.

Used by the Eos pixel-map export, the patch sheet, the preview and the auto-patcher.
Coordinates: Twinkly's y grows downward, which matches both Eos (1,1 = top-left) and the
way the grid is built here (row 0 at the top).
"""
import math

from .state import CONFIG, CONTROLLERS, lock

EOS_UNIVERSE = 512                 # Eos absolute address = (universe - 1) * 512 + offset


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


def grid_place(fpts, max_cells=200, width=0):
    """Snap 2D points to integer cells, one LED per cell. Returns (W, H, cells, nudged) with
    cells[i] = (col, row), 0-based. Grows the grid until fewer than 1% of LEDs collide, then
    moves the stragglers to the nearest free cell."""
    xs = [p[0] for p in fpts]
    ys = [p[1] for p in fpts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
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
