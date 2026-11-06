"""Several captures (views): flat and 3D combining, coverage, the API flow, and the map-placed
check patterns."""
import math
import random

import pytest

from bridge import mapping
from bridge.patterns import render
from bridge.twinkly import Controller

N = 1250


def sphere(n=N):
    """LEDs on a spiral over a unit sphere (y down), like strings wrapped round a ball."""
    pts = []
    for i in range(n):
        lat = math.acos(1 - 2 * i / n)
        lon = i * 0.35
        pts.append([math.sin(lat) * math.cos(lon), -math.cos(lat), math.sin(lat) * math.sin(lon)])
    return pts


def photograph(truth, angle, typed=None, scale=0.3, cu=0.5, cv=0.4, noise=0.003, seed=1):
    """An (orthographic) photo from `angle` degrees round to the right; the far side is hidden."""
    rnd = random.Random(seed)
    th = math.radians(angle)
    pts = []
    for x, y, z in truth:
        if x * math.sin(th) + z * math.cos(th) < -0.15:
            pts.append(None)
            continue
        pts.append([scale * (x * math.cos(th) - z * math.sin(th)) + cu + rnd.gauss(0, noise),
                    scale * y + cv + rnd.gauss(0, noise)])
    return {"angle": angle if typed is None else typed, "points": pts, "colours": []}


def error(pts, truth):
    tn = mapping.normalise(truth)
    e = sorted(math.dist(p, q) for p, q in zip(pts, tn) if p)
    return e[len(e) // 2], e[int(len(e) * 0.9)]


def test_3d_sphere_from_four_sides():
    truth = sphere()
    views = [photograph(truth, a, scale=s, cu=c, seed=k)
             for k, (a, s, c) in enumerate(((0, .3, .5), (90, .27, .46), (180, .33, .52), (270, .29, .49)))]
    mode, pts, info, stats = mapping.combine(views)
    assert mode == "around" and stats["unseen"] == 0 and stats["multi_view"] == N
    med, p90 = error(pts, truth)
    assert med < 0.03 and p90 < 0.05                      # a few % of the ball's size
    mirrored = mapping.normalise(mapping.combine_around(views, mirror=True)[0])
    assert error(mirrored, truth)[0] > 0.2                # the wrong handedness is clearly wrong


def test_3d_eight_views_correct_rough_angles():
    truth = sphere()
    views = [photograph(truth, a, typed=a + d, seed=a) for a, d in
             ((0, 0), (45, 6), (90, -8), (135, 5), (180, 8), (225, -6), (270, -9), (315, 7))]
    mode, pts, info, stats = mapping.combine(views)
    assert error(pts, truth)[0] < 0.03
    assert abs(info[2]["refined_angle"] - 90) < 4 and abs(info[6]["refined_angle"] - 270) < 6


def test_flat_combine_fills_gaps():
    truth = [[i % 50 / 50, i // 50 / 50, 0] for i in range(N)]
    a = {"angle": 0, "points": [[p[0], p[1]] if i % 3 else None for i, p in enumerate(truth)]}
    rot, sc = math.radians(7), 1.3                       # the second photo: zoomed, turned, shifted
    b = {"angle": 5, "points": [[sc * (p[0] * math.cos(rot) - p[1] * math.sin(rot)) + .1,
                                 sc * (p[0] * math.sin(rot) + p[1] * math.cos(rot)) - .05] if i % 3 != 1 else None
                                for i, p in enumerate(truth)]}
    mode, pts, info, stats = mapping.combine([a, b])
    assert mode == "flat" and stats["unseen"] == 0 and all(p for p in pts)
    assert info[1]["rms"] < 1e-6 and error(pts, truth)[1] < 1e-6


def test_coverage_colours():
    c = Controller({"ip": "10.0.0.9", "n_led": 6, "ports": [[0, 0, 6]]})
    s = mapping.Session([c], level=100)
    f = s.coverage(c, now={0, 1}, before={1, 2})
    assert f[0:3] == bytes([0, 50, 0]) and f[3:6] == bytes([0, 50, 0])      # this view: green
    assert f[6:9] == bytes([0, 0, 50]) and f[9:12] == bytes([50, 0, 0])     # before: blue; never: red


def test_map_patterns():
    c = Controller({"ip": "10.0.0.9", "n_led": 3, "ports": [[0, 0, 3]]})
    c.map = [[0, 0, 0], [0.5, 0.5, 0.5], [1, 1, 1]]
    lit = lambda f: [i for i in range(3) if any(f[3 * i:3 * i + 3])]
    assert lit(render(c, "sweep_x", 0.08 * 3 / 1.16, {"period": 3})) == [0]         # bar at the left edge
    assert lit(render(c, "sweep_x", 3 * 0.58 / 1.16, {"period": 3})) == [1]           # half way
    assert lit(render(c, "sweep_y", 3 * (1.08 / 1.16), {"period": 3})) == [2]       # bottom
    assert lit(render(c, "sweep_z", 0.08 * 3 / 1.16, {"period": 3})) == [0]
    assert len(render(c, "spin", 1, {})) == 9 and len(render(c, "map_rainbow", 1, {})) == 9
    c.map = None
    assert render(c, "sweep_x", 0, {}) == bytes(9)


def test_views_through_the_api(bridge, mock, tmp_path, monkeypatch):
    client, appmod, state = bridge
    monkeypatch.setattr(state, "CONFIG_PATH", str(tmp_path / "bridge_config.json"))
    client.delete("/api/mapping/views")
    cid = client.get("/api/status").get_json()["controllers"][0]["id"]
    c = state.CONTROLLERS[cid]
    saved = c.map, c.map_source
    truth = sphere()
    try:
        assert client.get("/api/mapping/views").get_json() is None
        assert client.post("/api/mapping/combine", json={}).status_code == 400
        for k, angle in enumerate((0, 90, 180, 270)):
            s = client.post("/api/mapping/start", json={"ids": [cid]}).get_json()
            v = photograph(truth, angle, seed=k)
            r = client.post("/api/mapping/view", json={"session": s["session"], "angle": angle, "points": v["points"],
                                                       "colours": ["RGB"] * N, "heading": 200 - angle, "pitch": 2})
            assert r.status_code == 200, r.data
            r = r.get_json()
            assert r["views"]["views"][-1]["angle"] == angle and (k > 0 or r["new"] > 600)
            assert client.get("/api/status").get_json()["controllers"][0]["test_pattern"] == "coverage"
        assert r["views"]["seen_any"] == N and (tmp_path / "mapping_views.json").exists()
        prev = client.post("/api/mapping/combine", json={}).get_json()
        assert prev["mode"] == "around" and not prev["saved"] and len(prev["points"]) == N and c.map == saved[0]
        out = client.post("/api/mapping/combine", json={"save": True}).get_json()
        assert out["saved"] and out["controllers"][0]["found"] == N
        st = client.get("/api/status").get_json()["controllers"][0]
        assert st["map_source"].startswith("camera 3D, 4 views") and not st["testing"]
        assert len({round(p[2], 3) for p in c.map}) > 100                       # real depth
        # sweeps over the saved map
        assert client.post("/api/test", json={"ids": [cid], "mode": "pattern", "pattern": "spin",
                                              "seconds": 1}).get_json()["ok"]
        client.post("/api/test", json={"mode": "stop"})
        # views from another controller set are not mixed in
        other = Controller({"ip": "10.0.0.77", "n_led": 10})
        state.CONTROLLERS[other.id] = other
        try:
            s = client.post("/api/mapping/start", json={"ids": [other.id]})
            assert s.status_code in (200, 502)
        finally:
            del state.CONTROLLERS[other.id]
    finally:
        client.post("/api/mapping/stop")
        client.delete("/api/mapping/views")
        c.map, c.map_source = saved


def test_map_pattern_needs_a_map(bridge):
    client, appmod, state = bridge
    cid = client.get("/api/status").get_json()["controllers"][0]["id"]
    c = state.CONTROLLERS[cid]
    saved = c.map
    c.map = None
    try:
        r = client.post(f"/api/controllers/{cid}/test", json={"mode": "pattern", "pattern": "sweep_x"})
        assert r.status_code == 502 and "no map" in r.get_json()["error"]
    finally:
        c.map = saved


def test_https_root_goes_to_the_phone_page(bridge):
    client, appmod, state = bridge
    r = client.get("/", base_url="https://localhost")
    assert r.status_code == 302 and r.headers["Location"].endswith("/map")
    assert client.get("/?desk=1", base_url="https://localhost").status_code == 200
    assert client.get("/").status_code == 200


def helix(n=250, turns=4, r=0.3, pitch=0.002):
    """One string spiralling up a cylinder (y down: starts at the bottom)."""
    return [[0.5 + r * math.sin(2 * math.pi * turns * i / n), 1 - pitch * i * 2, 0.5 + r * math.cos(2 * math.pi * turns * i / n)]
            for i in range(n)]


def test_fill_hidden_run_goes_round_the_back():
    truth = helix()
    seen = [p if not 100 <= i < 125 else None for i, p in enumerate(truth)]   # 25 LEDs hidden: over 1/3 turn
    filled, n = mapping.fill_gaps(seen, [[0, 0, 250]], 250)
    assert n == 25
    err = max(math.dist(filled[i], truth[i]) for i in range(100, 125))
    chord = [[seen[99][k] + (seen[125][k] - seen[99][k]) * (i - 99) / 26 for k in range(3)] for i in range(100, 125)]
    straight = max(math.dist(chord[i - 100], truth[i]) for i in range(100, 125))
    assert err < 0.01 < straight / 5                    # on the surface, not through the middle


def test_fill_hidden_end_continues_the_spiral():
    truth = helix()
    seen = truth[:230] + [None] * 20                  # the last 20 LEDs out of frame
    filled, n = mapping.fill_gaps(seen, [[0, 0, 250]], 250)
    assert n == 20 and max(math.dist(filled[i], truth[i]) for i in range(230, 250)) < 0.02
    flat = [[i * 0.01, 0.5] if i < 240 else None for i in range(250)]        # flat: keep going in a line
    out, _ = mapping.fill_gaps(flat, [[0, 0, 250]], 250)
    assert out[249] == pytest.approx([2.49, 0.5]) and len(out[249]) == 2


def test_fill_cone_hidden_runs():
    cone = [[0.5 + (0.4 - 0.35 * i / 250) * math.sin(2 * math.pi * 6 * i / 250), 1 - i / 250,
             0.5 + (0.4 - 0.35 * i / 250) * math.cos(2 * math.pi * 6 * i / 250)] for i in range(250)]
    for lo, hi in ((60, 80), (0, 15), (235, 250)):                       # middle, bottom start, top end
        seen = [p if not lo <= i < hi else None for i, p in enumerate(cone)]
        filled, _ = mapping.fill_gaps(seen, [[0, 0, 250]], 250)
        assert max(math.dist(filled[i], cone[i]) for i in range(lo, hi)) < 0.02
    ax = mapping.axis_centre([p for i, p in enumerate(cone) if not 60 <= i < 80])
    assert math.dist(ax, (0.5, 0.5)) < 0.01


def test_unwrap_for_eos_front_in_the_middle():
    from bridge.layout import flatten_points, grid_place
    # a cylinder: front (+z, towards the first camera) must land mid-width, right side to the right
    pts = [[0.5 + 0.3 * math.sin(a), y, 0.5 + 0.3 * math.cos(a)] for y in (0.0, 0.5, 1.0)
           for a in (0.0, math.pi / 2, -math.pi / 2, math.pi * 0.99)]
    f = flatten_points(pts, "unwrap")
    assert f[0][0] == pytest.approx(0) and f[1][0] > 0 > f[2][0] and f[3][0] > f[1][0]
    assert f[0][0] * 1 == pytest.approx(0) and abs(f[1][0]) == pytest.approx(0.3 * math.pi / 2)   # arc length
    # cone unrolled exactly: every LED keeps its slant distance from the apex
    cone = [[0.5 + 0.4 * h * math.sin(a / 2), h, 0.5 + 0.4 * h * math.cos(a / 2)] for h in (0.25, 0.5, 1.0)
            for a in range(12)]                          # rings round a cone, tip above the top ring
    fan = flatten_points(cone, "cone")
    for (x, y), p in zip(fan, cone):
        assert math.hypot(x, y) == pytest.approx(math.hypot(p[1], 0.4 * p[1]), rel=1e-6)
    W, H, cells, nudged = grid_place(flatten_points(helix(), "unwrap"))
    assert W > H and nudged <= 3                         # 4 turns round a cylinder: wider than tall


def test_a_flat_piece_keeps_its_shape_and_its_back_face():
    """A star is a flat piece standing upright. Unwrapping it as if it were a cylinder measures
    the angle round a vertical axis, which every LED shares, so the shape collapses into two
    edge strips. `faces` lays it out in its own plane instead, and puts a second face beside the
    front rather than on top of it."""
    import math
    from bridge.layout import flatten_points, shape_of, _plane, _two_layers

    def star(n, depth, rev=False):
        pts = []
        for k in range(n):
            t = 2 * math.pi * k / n
            r = 0.45 * (0.5 + 0.5 * abs(math.cos(2.5 * t)))
            pts.append([r * math.cos(t), r * math.sin(t), depth])
        return pts[::-1] if rev else pts

    two_faced = star(250, -0.04) + star(250, 0.04, True)
    wobbly = [[p[0], p[1], p[2] + 0.15 * math.sin(7 * k / 250.0)] for k, p in enumerate(star(500, 0.0))]
    tree = [[0.35 * (1 - k / 500.0) * math.cos(k / 8.0), -0.9 * k / 500.0,
             0.35 * (1 - k / 500.0) * math.sin(k / 8.0)] for k in range(500)]

    assert shape_of(two_faced) == "faces" and shape_of(wobbly) == "faces"
    assert shape_of(tree) == "unwrap"                      # a round piece still unwraps

    # the front view folds the two faces exactly on top of each other; `faces` does not
    xy = flatten_points(two_faced, "xy")
    assert all(math.dist(xy[g], xy[499 - g]) < 1e-9 for g in range(250))
    fa = flatten_points(two_faced, "auto")
    span = max(p[0] for p in fa) - min(p[0] for p in fa)
    assert min(math.dist(fa[g], fa[499 - g]) for g in range(250)) > 0.3 * span

    # a single sheet that merely wobbles in depth is NOT split in two: the star stays a star
    w = flatten_points(wobbly, "auto")
    wide = max(p[0] for p in w) - min(p[0] for p in w)
    tall = max(p[1] for p in w) - min(p[1] for p in w)
    assert 0.8 < wide / tall < 1.25                        # still roughly square, not two stars
    cx, cz, ax, az = _plane(wobbly)
    assert _two_layers([-(p[0] - cx) * az + (p[2] - cz) * ax for p in wobbly]) is None


def two_faced_panel(n=250, w=0.6, h=0.6, d=0.05):
    """One string across the front of a flat panel in rows, over the rim, back across the rear:
    the routing a star or a sign most often has, and the hardest front/back case there is,
    because each back LED sits almost exactly behind a front one."""
    pts, half, cols = [], n // 2, 25
    rows = max(1, half // cols)
    for k in range(n):
        j = k if k < half else n - 1 - k
        row, col = divmod(j, cols)
        x = 0.5 - w / 2 + w * (col if row % 2 == 0 else cols - 1 - col) / (cols - 1)
        pts.append([x, 0.2 + h * row / max(1, rows - 1), -d if k < half else d])
    return pts


def panel_views(truth, angles, keep=0.55, seed=7, through=0.25):
    """Orthographic views of a flat piece: an LED is mostly seen when its own face is turned
    towards the camera, and now and then through a gap from the other side (tinsel is sparse)."""
    rnd = random.Random(seed)
    out = []
    for a in angles:
        t = math.radians(a)
        pts = []
        for p in truth:
            facing = -math.cos(t) if p[2] < 0 else math.cos(t)
            ok = rnd.random() < (keep if facing > -0.1 else keep * through)
            pts.append([p[0] * math.cos(t) - p[2] * math.sin(t) + 0.5, p[1]] if ok else None)
        out.append({"angle": a, "points": pts})
    return out


def test_a_flat_piece_is_not_filled_round_an_axis_that_isnt_there():
    """fill_gaps' cylinder route is right for a tree and wrong for a star: a star's LEDs all
    share the vertical axis, so going "round the back" swings a hidden run out into space.
    The route is chosen from how deep the measured LEDs reach compared with how wide they
    spread - near 1 for a tree or column, well under half for anything with a front and a back."""
    truth = two_faced_panel()
    views = panel_views(truth, [0, 50, 100, 150, 200, 250])
    _, pts, _, stats = mapping.combine(views, "around", ports=[(0, 0, 250)])
    seen = [p for p in pts if p]
    depth = max(p[2] for p in seen) - min(p[2] for p in seen)
    hide = range(40, 46)                                   # a 6-LED run hidden mid-front
    part = [None if i in hide else p for i, p in enumerate(pts)]
    meas = [m and i not in hide for i, m in enumerate(stats["measured"])]

    auto, _ = mapping.fill_gaps(part, [[0, 0, 250]], 250, meas)
    round_, _ = mapping.fill_gaps(part, [[0, 0, 250]], 250, meas, route="cylinder")
    straight = max(math.dist(auto[i], pts[i]) for i in hide if pts[i])
    swung = max(math.dist(round_[i], pts[i]) for i in hide if pts[i])
    assert straight < 0.25 * swung                         # the cylinder route throws them away
    # and nothing filled leaves the slab of space the camera actually measured
    lo, hi = min(p[2] for p in seen), max(p[2] for p in seen)
    assert all(lo - 0.3 * depth <= auto[i][2] <= hi + 0.3 * depth for i in hide)


def test_a_guessed_depth_comes_from_the_same_string():
    """An LED seen from one angle only has its depth taken from where its own string was just
    before and just after it. The LED numbers either side of a port boundary are neighbours in
    the chain but can be metres apart on the installation, so the string matters, not the number."""
    front = [[0.3 + 0.03 * i, 0.4, -0.25] for i in range(10)]      # port 0: well in front
    back = [[0.3 + 0.03 * i, 0.6, 0.25] for i in range(10)]        # port 1: well behind
    truth = front + back
    views = []
    for a in (0, 60):
        t = math.radians(a)
        views.append({"angle": a, "points": [[p[0] * math.cos(t) - p[2] * math.sin(t) + 0.5, p[1]]
                                             for p in truth]})
    views[1]["points"][9] = None                           # the last LED of port 0: seen once
    _, pts, _, stats = mapping.combine(views, "around", ports=[(0, 0, 10), (1, 10, 10)])
    assert not stats["measured"][9] and stats["measured"][8] and stats["measured"][10]
    near = [p for i, p in enumerate(pts) if i < 9]
    assert abs(pts[9][2] - sum(p[2] for p in near) / len(near)) < 0.2 * abs(pts[10][2] - pts[0][2])


def test_the_map_says_what_was_measured_and_what_was_guessed():
    """Every front/back mistake on the 3D test rig is in an LED whose depth was guessed, so the
    bridge has to hand that apart rather than report one number for the whole map."""
    truth = two_faced_panel()
    views = panel_views(truth, [0, 50, 100, 150, 200, 250])
    _, pts, info, stats = mapping.combine(views, "around", ports=[(0, 0, 250)])
    m = stats["measured"]
    assert len(m) == 250 and sum(m) == stats["multi_view"]
    assert all(not m[g] for g, p in enumerate(pts) if not p)
    assert "clamped" in info[0] and info[0]["clamped"] is False

    rep = mapping.report(pts, [None] * 250, [[0, 0, 250]], 250, measured=m)[0]
    assert rep["measured"] == stats["multi_view"] and rep["estimated"] == stats["single_view"]
    assert rep["never_seen"] == stats["unseen"]
    assert 0 < rep["longest_unmeasured_run"] < 60
    # with no provenance to hand (one capture, flat map) everything seen counts as measured
    plain = mapping.report(pts, [None] * 250, [[0, 0, 250]], 250)[0]
    assert plain["measured"] == stats["multi_view"] + stats["single_view"] and plain["estimated"] == 0


def test_the_faces_are_told_apart_from_the_measured_leds_only():
    """A piece's front and back are separated by looking at how deep its LEDs sit. The LEDs
    whose depth was guessed land between the faces - the string has to cross over somewhere,
    and a guess lands on the way - so a handful of them is enough to hide the gap and fold the
    back onto the front in the Eos map. Fit the split on the measured LEDs, then put every LED
    on whichever side its own depth falls: that assumes nothing about the order the string
    runs in, which is the thing the mapping is there to find out."""
    import random
    from bridge.layout import flatten_points, _faces_cut, _two_layers, _plane

    rnd = random.Random(3)
    front = [[math.cos(2 * math.pi * k / 200) * 0.4, math.sin(2 * math.pi * k / 200) * 0.4, -0.05]
             for k in range(200)]
    back = [[p[0], p[1], 0.05] for p in reversed(front)]
    pts = front + back
    measured = [True] * 400
    for k in range(400):               # the camera is not exact, and 45% of the depths are guesses
        z = pts[k][2] + rnd.gauss(0, 0.008)
        if rnd.random() < 0.45:
            z, measured[k] = rnd.uniform(-0.05, 0.05), False
        pts[k] = [pts[k][0], pts[k][1], z]

    assert _faces_cut(pts, measured) is not None    # measured LEDs: two clear layers
    assert _faces_cut(pts, None) is None            # everything: the gap is filled in by guesses

    pairs = [(g, 399 - g) for g in range(200)]    # each back LED sits right behind a front one

    def folded(flat):
        span = max(p[0] for p in flat) - min(p[0] for p in flat)
        return [(g, h) for g, h in pairs if math.dist(flat[g], flat[h]) < 0.02 * span]

    blind = folded(flatten_points(pts, "auto"))             # no provenance: no split, all folded
    told = folded(flatten_points(pts, "auto", measured))
    assert len(blind) == len(pairs) and len(told) < 0.4 * len(blind)
    # and no pair of LEDs the camera actually measured is folded: every one left is a guess
    assert all(not (measured[g] and measured[h]) for g, h in told)


def test_a_flat_piece_with_things_in_front_of_it_is_not_unwrapped():
    """A star with a tree standing in front of it and a frame behind reaches about half as far
    back as it is wide. That is not a cylinder, and unwrapping it round the vertical axis
    collapses it - but it is deep enough that a tighter test called it round."""
    from bridge.layout import shape_of, flatten_points

    star = [[0.45 * (0.5 + 0.5 * abs(math.cos(2.5 * 2 * math.pi * k / 400))) * math.cos(2 * math.pi * k / 400),
             0.45 * (0.5 + 0.5 * abs(math.cos(2.5 * 2 * math.pi * k / 400))) * math.sin(2 * math.pi * k / 400),
             0.0] for k in range(400)]
    tree = [[0.1 * math.cos(k / 4.0) - 0.2, -0.3 + 0.4 * k / 80, 0.1 * math.sin(k / 4.0) - 0.25] for k in range(80)]
    frame = [[-0.4 + 0.8 * k / 60, 0.4, 0.22] for k in range(60)]
    pts = star + tree + frame
    assert shape_of(pts) == "faces"
    f = flatten_points(pts, "auto")
    wide = max(p[0] for p in f) - min(p[0] for p in f)
    tall = max(p[1] for p in f) - min(p[1] for p in f)
    assert 0.8 < wide / tall < 1.3                  # still star-shaped, not two strips
