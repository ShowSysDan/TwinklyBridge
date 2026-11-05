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
