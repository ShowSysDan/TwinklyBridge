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
            code = sum(1 << k for k in range(s.bits) if frames[k][i * 3])
            assert all(bool(frames[k][i * 3]) != bool(inverse[k][i * 3]) for k in range(s.bits))
            v, g = code, 0
            while v:
                g ^= v
                v >>= 1
            assert g - 1 == s.offsets[c.id] + i
    assert s.frame(a, "red")[:3] == bytes([100, 0, 0]) and s.frame(a, "blue")[3:6] == bytes([0, 0, 100])
    assert set(s.frame(a, "black")) == {0} and set(s.frame(a, "white")) == {100}
    w = ctrl(2, 250, "RGBW")
    assert mapping.Session([w], 90).frame(w, "white")[:4] == bytes([90, 90, 90, 90])
    with pytest.raises(ValueError):
        s.frame(a, "b99")


def test_fill_gaps_and_report():
    pts = [None] * 10
    pts[1], pts[4], pts[5] = [0, 0], [3, 3], [4, 4]
    out, n = mapping.fill_gaps(pts, [[0, 0, 6], [1, 6, 4]], 10)
    assert out[0] == [0, 0] and out[2] == [1, 1] and out[3] == [2, 2] and n == 3   # port 1 all unseen: left None
    assert out[6] is None
    cols = ["RGB"] * 3 + ["GRB"] * 3 + [None] * 4
    rep = mapping.report([[0, 0]] * 6 + [None] * 4, cols, [[0, 0, 3], [1, 3, 3], [2, 6, 4]], 10)
    assert rep[0]["ok"] and rep[1]["colour_order"] == "GRB" and not rep[1]["ok"]
    assert rep[2]["notes"] == ["nothing seen on this port"]
    short = mapping.report([[0, 0]] * 200 + [None] * 50, ["RGB"] * 250, [[0, 0, 250]], 250)[0]
    assert short["last_seen"] == 200 and "string ends at LED 200 of 250 configured" in short["notes"]


def run_sim():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    out = subprocess.run([node, os.path.join(ROOT, "tests", "js", "mapdecode_sim.js"),
                          os.path.join(ROOT, "bridge", "static", "js", "mapdecode.js")],
                         capture_output=True, text=True, timeout=120, check=True)
    return json.loads(out.stdout)


def test_decoder_on_synthetic_camera():
    d = run_sim()
    st = d["stats"]
    assert st["found"] == 1200 and d["wrong"] == 0 and d["meanErr"] < 0.5 and not st["cameraMoved"]
    assert d["foundPerPort"] == [250, 250, 250, 250, 200] and d["lastPort4"] == 200
    assert d["colours"]["2:GRB"] == 250 and d["colours"]["0:RGB"] == 250


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
        assert ports[0]["ok"] and mapping.SESSION is not None          # preview only: nothing saved yet
        rep = client.post("/api/mapping/save", json=body).get_json()
        c = rep["controllers"][0]
        assert (c["found"], c["filled"], c["parameters"]) == (1200, 50, 3 * N)
        st = client.get("/api/status").get_json()["controllers"][0]
        assert st["map_source"] == "camera, 1200 seen, 50 filled" and not st["testing"] and mapping.SESSION is None
    finally:
        client.post("/api/mapping/stop")
        state.CONTROLLERS[cid].map, state.CONTROLLERS[cid].map_source = saved
