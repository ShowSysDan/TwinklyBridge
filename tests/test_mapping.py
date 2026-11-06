"""Camera mapping: the bridge's Gray-coded frames, the phone decoder (run in Node against a
synthetic camera), and the save / report path through the API."""
import json
import os
import shutil
import subprocess

import pytest

from bridge import mapping
from bridge.twinkly import Controller

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
N = 1250


def ctrl(i=0, n=N, profile="RGB"):
    return Controller({"ip": f"10.0.0.{20 + i}", "n_led": n, "profile": profile,
                       "ports": [[p, p * 250, 250] for p in range(n // 250)]})


def test_gray_frames_identify_every_led():
    a, b = ctrl(0), ctrl(1, 500)
    s = mapping.Session([a, b], level=100)
    assert s.total == 1750 and s.bits == 11 and s.offsets == {a.id: 0, b.id: 1250}
    assert s.steps[:5] == ["black", "white", "red", "green", "blue"] and s.steps[-1] == "white_end"
    # reading each LED's bits back from the frames gives its global number, uniquely
    for c in (a, b):
        frames = {k: s.frame(c, f"b{k}") for k in range(s.bits)}
        inverse = {k: s.frame(c, f"i{k}") for k in range(s.bits)}
        for i in range(0, c.n_led, 7):
            code = sum(1 << k for k in range(s.bits) if frames[k][i * 3 + 1])
            assert all(bool(frames[k][i * 3 + 1]) != bool(inverse[k][i * 3 + 1]) for k in range(s.bits))
            v, g = code, 0
            while v:
                g ^= v
                v >>= 1
            assert g - 1 == s.offsets[c.id] + i
    # full-on channels (255, so no PWM flicker), whatever the level: coding frames in green
    assert s.frame(a, "red")[:3] == bytes([255, 0, 0]) and s.frame(a, "blue")[3:6] == bytes([0, 0, 255])
    assert set(s.frame(a, "black")) == {0} and s.frame(a, "white")[:6] == bytes([0, 255, 0, 0, 255, 0])
    w = ctrl(2, 250, "RGBW")
    assert mapping.Session([w], 90).frame(w, "white")[:4] == bytes([0, 0, 255, 0])          # W R G B
    with pytest.raises(ValueError):
        s.frame(a, "b99")


def test_fill_gaps_and_report():
    pts = [None] * 10
    pts[1], pts[4], pts[5] = [0, 0], [3, 3], [4, 4]
    out, n = mapping.fill_gaps(pts, [[0, 0, 6], [1, 6, 4]], 10)
    # the gap is filled along the line; LED 0 continues the string's step backwards (not a copy)
    assert out[0] == [-1, -1] and out[2] == [1, 1] and out[3] == [2, 2] and n == 3  # port 1 all unseen: left None
    assert out[6] is None
    cols = ["RGB"] * 3 + ["GRB"] * 3 + [None] * 4
    rep = mapping.report([[0, 0]] * 6 + [None] * 4, cols, [[0, 0, 3], [1, 3, 3], [2, 6, 4]], 10)
    assert rep[0]["ok"] and rep[1]["colour_order"] == "GRB" and not rep[1]["ok"]
    assert rep[2]["notes"] == ["nothing seen on this port"]
    short = mapping.report([[0, 0]] * 200 + [None] * 50, ["RGB"] * 250, [[0, 0, 250]], 250)[0]
    assert short["last_seen"] == 200 and "string ends at LED 200 of 250 configured" in short["notes"]
    sofar = mapping.report([[0, 0]] * 200 + [None] * 50, ["RGB"] * 250, [[0, 0, 250]], 250, partial=True)[0]
    assert sofar["notes"] == ["50 not seen yet"]


def run_sim(mode="normal", stabilise=True):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    env = dict(os.environ, STAB="1" if stabilise else "0")
    out = subprocess.run([node, os.path.join(ROOT, "tests", "js", "mapdecode_sim.js"),
                          os.path.join(ROOT, "bridge", "static", "js", "mapdecode.js"), mode],
                         capture_output=True, text=True, timeout=120, check=True, env=env)
    return json.loads(out.stdout)


def test_decoder_on_a_harsh_synthetic_camera():
    """Bloom and clipping, stripes across the picture, a floor reflection of every LED, more
    noise and exposure drift between photos: fewer LEDs, but none put in the wrong place."""
    d = run_sim("hard")
    assert d["stats"]["found"] > 950 and d["wrong"] == 0 and d["meanErr"] < 1.0


def test_green_ness_in_a_lit_warehouse():
    """A lit warehouse: grey textured room around 140, copper tinsel that glints, the camera
    exposed for the room, and enough scattered glow that an LED supplies only ~30% of the raw
    brightness at its own position. Reading the LEDs by green-ness finds clearly more than by
    brightness, and the better one is used."""
    d = run_sim("bright")
    assert d["signal"] == "green-ness" and d["tried"]["green-ness"] > 1.15 * d["tried"]["brightness"]
    assert d["stats"]["found"] > 900 and d["wrong"] == 0


def test_a_hand_held_phone_in_a_lit_room():
    """The real-world case: a lit warehouse photographed hand-held, so the picture wanders about
    20 px over the capture and rolls a few tenths of a degree. The drift is measured against the
    room's own texture and taken out with an affine fit, so every LED still lands in the right
    place and no 'the camera moved' warning is raised. Without lining the photos up, the same
    photos decode to almost nothing: 20 px of drift against a 3 px dot is fatal."""
    d = run_sim("brightshake")
    assert d["stats"]["found"] > 950 and d["wrong"] == 0 and d["meanErr"] < 1.0
    assert d["stab"]["drift"] > 10 and not d["stab"]["unaligned"] and not d["stats"]["cameraMoved"]
    assert d["stab"]["affine"] >= 20                       # roll is fitted, not just translation
    raw = run_sim("brightshake", stabilise=False)
    assert raw["stats"]["found"] < 100


def test_a_hand_held_phone_in_the_dark():
    """The same hand-held drift in a dark room, where the LEDs bloom (any real LED bright enough
    to photograph does). The drift is measured from the LEDs themselves and still comes out."""
    d = run_sim("hardshake")
    assert d["stats"]["found"] > 950 and d["wrong"] == 0 and d["meanErr"] < 1.0
    assert d["stab"]["drift"] > 10 and not d["stab"]["unaligned"]


def test_a_hand_held_phone_in_a_blacked_out_room():
    """The hardest case: a blacked-out room, hand-held, and LEDs that are bare dots with no
    bloom - so the only thing in the picture to line the photos up on is the LEDs themselves.
    This used to fail, because the coarse search stepped four pixels at a time: an LED is about
    a pixel and a half across, so a drift landing two pixels off that step was invisible to it
    and it answered with something nine pixels wrong. It steps one pixel now."""
    d = run_sim("shake")
    assert d["stats"]["found"] > 950 and d["wrong"] == 0 and d["meanErr"] < 1.0
    assert d["stab"]["drift"] > 10 and not d["stab"]["unaligned"]


def test_drift_is_found_whatever_fraction_of_a_pixel_it_lands_on():
    """The bug above, directly: slide one photo by a known 0 to 8 px and check every one comes
    back. It failed at exactly 2 and 6 px before - a mod-4 signature, not noise."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    out = subprocess.run([node, os.path.join(ROOT, "tests", "js", "shift_probe.js"),
                          os.path.join(ROOT, "bridge", "static", "js", "mapdecode.js")],
                         capture_output=True, text=True, timeout=300, check=True)
    got = json.loads(out.stdout)
    # the old coarse search gave 22.9 px here, failing at offsets 2, 6 and 10 and nowhere else
    assert got["worst_error_px"] < 1.0, got
    assert got["z_evenness"] > 0.5, got      # and it was no less sure of the wrong answers


def test_decoder_on_synthetic_camera():
    """The baseline: a still phone, a dark room. LEDs ~20 px apart along the wire and ~6 px from
    the nearest LED on the next fold of the string, as measured on a real capture."""
    d = run_sim()
    st = d["stats"]
    assert st["found"] > 1050 and d["wrong"] == 0 and d["meanErr"] < 0.8 and not st["cameraMoved"]
    assert st["inBand"] > 0.95                              # neighbours really are neighbours
    assert all(n > 180 for n in d["foundPerPort"]) and d["lastPort4"] == 200
    # colour order is read per port, and never read wrongly
    assert d["colours"]["2:GRB"] > 180 and d["colours"]["0:RGB"] > 180
    assert not any(k.endswith(":RGB") for k in d["colours"] if k.startswith("2:"))


def test_mapping_session_through_the_api(bridge, mock):
    client, appmod, state = bridge
    cid = client.get("/api/status").get_json()["controllers"][0]["id"]
    saved = state.CONTROLLERS[cid].map, state.CONTROLLERS[cid].map_source
    try:
        s = client.post("/api/mapping/start", json={"ids": [cid], "level": 120}).get_json()
        assert s["total"] == N and s["bits"] == 11 and len(s["steps"]) == 5 + 22 + 1
        for step in ("white", "b3", "i3", "red"):
            r = client.post("/api/mapping/step", json={"session": s["session"], "step": step})
            assert r.status_code == 200, r.data
            sess = mapping.SESSION
            assert mock.state.last_frame == sess.frame(state.CONTROLLERS[cid], step)   # it reached the "controller"
        assert client.get("/api/status").get_json()["controllers"][0]["test_pattern"] == "mapping"
        assert client.post("/api/mapping/step", json={"session": "old", "step": "white"}).status_code == 409
        assert client.post("/api/mapping/save", json={"session": s["session"], "points": [None] * N}).status_code == 422

        d = run_sim()                                   # the phone's result for this 1250-LED layout
        body = {"session": s["session"], "points": d["points"], "colours": d["colourList"]}
        rep = client.post("/api/mapping/save", json={**body, "save": False}).get_json()
        ports = rep["controllers"][0]["ports"]
        assert ports[2]["colour_order"] == "GRB" and not ports[2]["ok"]
        assert ports[4]["last_seen"] == 200 and "string ends at LED 200 of 250 configured" in ports[4]["notes"]
        # the report says what was really seen: one capture from one side does not see every
        # LED, and the port says so rather than claiming to be fine
        assert ports[0]["colour_order"] == "RGB" and ports[0]["found"] > 180
        assert any("not seen before the end" in note for note in ports[0]["notes"])
        assert mapping.SESSION is not None                             # preview only: nothing saved yet
        rep = client.post("/api/mapping/save", json=body).get_json()
        c = rep["controllers"][0]
        found = sum(1 for p in d["points"] if p)
        assert c["found"] == found and c["filled"] == N - found and c["parameters"] == 3 * N
        st = client.get("/api/status").get_json()["controllers"][0]
        assert st["map_source"] == f"camera, {found} seen, {N - found} filled"
        assert not st["testing"] and mapping.SESSION is None
    finally:
        client.post("/api/mapping/stop")
        state.CONTROLLERS[cid].map, state.CONTROLLERS[cid].map_source = saved


def test_capture_photos_upload_and_zip(bridge, tmp_path, monkeypatch):
    import io
    import zipfile
    client, appmod, state = bridge
    monkeypatch.setattr(state, "CONFIG_PATH", str(tmp_path / "bridge_config.json"))
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 100
    assert client.post("/api/mapping/captures/2026-11-05T10-00-00_90deg/white.png", data=png,
                       content_type="image/png").status_code == 200
    assert client.post("/api/mapping/captures/2026-11-05T10-00-00_90deg/capture.json", data=b'{"a": 1}',
                       content_type="application/json").status_code == 200
    lst = client.get("/api/mapping/captures").get_json()
    assert lst[0]["name"] == "2026-11-05T10-00-00_90deg" and lst[0]["files"] == 2 and lst[0]["complete"]
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/mapping/captures/2026-11-05T10-00-00_90deg.zip").data))
    assert sorted(z.namelist()) == ["2026-11-05T10-00-00_90deg/capture.json", "2026-11-05T10-00-00_90deg/white.png"]
    # names are checked: no paths out of instance/captures, only png / json, right content type
    for url in ("/api/mapping/captures/..%2F..%2Fetc/x.png", "/api/mapping/captures/ok/..png",
                "/api/mapping/captures/ok/a.exe", "/api/mapping/captures/bad name/a.png"):
        assert client.post(url, data=png, content_type="image/png").status_code in (400, 404, 405)
    assert client.post("/api/mapping/captures/ok/a.png", data=png, content_type="text/plain").status_code == 415
    assert client.post("/api/mapping/captures/ok/a.png", data=b"", content_type="image/png").status_code == 400
    assert client.post("/api/mapping/captures/2026-11-05T10-00-00_90deg/b0.jpg", data=b"\xff\xd8jpeg",
                       content_type="image/jpeg").status_code == 200
    assert client.delete("/api/mapping/captures/2026-11-05T10-00-00_90deg").get_json()["ok"]
    assert client.get("/api/mapping/captures").get_json() == []


def test_step_sets_the_coding_level(bridge, mock):
    client, appmod, state = bridge
    cid = client.get("/api/status").get_json()["controllers"][0]["id"]
    try:
        s = client.post("/api/mapping/start", json={"ids": [cid]}).get_json()
        assert s["code_level"] == 255
        assert client.post("/api/mapping/step", json={"session": s["session"], "step": "white", "level": 56}).status_code == 200
        assert mock.state.last_frame[:6] == bytes([0, 56, 0, 0, 56, 0])            # all-on, green at 56
        client.post("/api/mapping/step", json={"session": s["session"], "step": "b0"})
        assert set(mock.state.last_frame[1::3]) == {0, 56}                         # and it sticks for the patterns
        client.post("/api/mapping/step", json={"session": s["session"], "step": "white", "level": 999})
        assert mock.state.last_frame[1] == 255                                     # clamped
    finally:
        client.post("/api/mapping/stop")


def test_the_guided_walk_plans_over_the_arc_you_can_reach():
    """The walk used to take 8 views 45 degrees apart whatever the piece or the room allowed,
    so someone who could only get three quarters of the way round stopped half way through the
    plan. It now spreads its views over the arc the user says they can walk, never asking for
    more than that, and keeps them off the angles where a flat piece is edge-on to the camera
    and gives up about half its LEDs (90 and 270 degrees)."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    out = subprocess.run([node, os.path.join(ROOT, "tests", "js", "guide_plan.js"),
                          os.path.join(ROOT, "bridge", "static", "js", "mapping.js")],
                         capture_output=True, text=True, timeout=60, check=True)
    plans = json.loads(out.stdout)
    assert set(plans) == {"315", "250", "180", "120"}
    for arc, p in plans.items():
        assert p["span"] <= int(arc)                   # never further than they said they can go
        assert p["span"] >= int(arc) - 2 * p["step"]   # and most of the way round it
        assert p["edgeOn"] == 0 and len(p["angles"]) == 8
    assert plans["315"]["step"] != 45                  # the old plan put two of eight views edge-on
