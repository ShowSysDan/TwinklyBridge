"""End-to-end tests against the mock Twinkly Pro. Run: pytest -q"""
import base64
import csv
import io
import math
import time
import xml.etree.ElementTree as ET

from senders import ddp_packet, e131_packet, send_ddp, send_sacn

N = 1250


def pattern():
    ch = bytearray(bytes([8, 8, 8]) * N)
    ch[0:3] = b"\xff\x00\x00"
    ch[501 * 3:501 * 3 + 3] = b"\x00\xff\x00"
    ch[-3:] = b"\x00\x00\xff"
    return ch


def wait_for(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------- unit: parsers
def test_e131_parser_roundtrip():
    from bridge.sacn import parse_e131
    p = e131_packet(7, bytes(range(256)) * 2, seq=9, priority=120)
    universe, priority, options, source, dmx = parse_e131(p)
    assert (universe, priority, options) == (7, 120, 0)
    assert source == "twinkly-bridge-test"
    assert dmx == bytes(range(256)) * 2
    assert parse_e131(b"not a packet" * 20) is None


def test_ddp_parser_roundtrip():
    from bridge.ddp import parse_ddp
    off, payload = parse_ddp(ddp_packet(3000, b"\x01\x02\x03"))
    assert (off, payload) == (3000, b"\x01\x02\x03")
    assert parse_ddp(b"\x42\x00\x01\x01\x00\x00\x00\x00\x00\x00") is None   # query flag -> not data


# ---------------------------------------------------------------- controller facts
def test_controller_added_and_read(bridge, mock):
    client, appmod, state = bridge
    s = client.get("/api/status").get_json()
    c = s["controllers"][0]
    assert c["n_led"] == N and c["profile"] == "RGB" and c["api"] == "v2"
    assert c["universes"] == list(range(1, 9))          # 3750 channels at 510/universe
    assert c["prev_mode"] == {"mode": "movie", "unique_id": "90A9D9A7-479E-49A1-AE2C-B3732E84D99A"}
    assert len(c["ports"]) == 5 and c["ports"][2] == [2, 500, 250]


# ---------------------------------------------------------------- sACN -> rt
def test_sacn_streams_and_restores(bridge, mock):
    client, appmod, state = bridge
    cid = client.get("/api/status").get_json()["controllers"][0]["id"]
    frames_before = mock.state.frames
    send_sacn(pattern(), seconds=1.5)
    assert wait_for(lambda: mock.state.frames > frames_before + 5, 3)
    f = base64.b64decode(client.get("/api/frames").get_json()[cid])
    assert len(f) == N * 3 and f[:3] == b"\xff\x00\x00" and f[1503:1506] == b"\x00\xff\x00" and f[-3:] == b"\x00\x00\xff"
    assert f[21:24] == b"\x08\x08\x08"
    last = mock.state.last_frame
    assert last[:3] == b"\xff\x00\x00" and last[-3:] == b"\x00\x00\xff" and mock.state.bad == 0
    assert mock.state.mode == {"mode": "rt"}
    # idle: controller returns to the mode it was in
    assert wait_for(lambda: mock.state.mode.get("mode") == "movie", 5)
    c = client.get("/api/status").get_json()["controllers"][0]
    assert c["active"] is False and c["status"] == "online"


# ---------------------------------------------------------------- DDP -> rt
def test_ddp_streams(bridge, mock):
    client, appmod, state = bridge
    cid = client.get("/api/status").get_json()["controllers"][0]["id"]
    client.put(f"/api/controllers/{cid}", json={"source": "ddp", "ddp_start": 1})
    frames_before = mock.state.frames
    send_ddp(pattern(), seconds=1.5)
    assert wait_for(lambda: mock.state.frames > frames_before + 5, 3)
    last = mock.state.last_frame
    assert last[:3] == b"\xff\x00\x00" and last[1503:1506] == b"\x00\xff\x00" and last[-3:] == b"\x00\x00\xff"
    client.put(f"/api/controllers/{cid}", json={"source": "sacn"})
    wait_for(lambda: mock.state.mode.get("mode") == "movie", 5)


# ---------------------------------------------------------------- identify / test patterns
def test_identify_pixel(bridge, mock):
    client, appmod, state = bridge
    cid = client.get("/api/status").get_json()["controllers"][0]["id"]
    client.post(f"/api/controllers/{cid}/test", json={"mode": "pixel", "index": 10, "rgb": [0, 0, 255], "seconds": 1})
    assert wait_for(lambda: mock.state.last_frame[30:33] == b"\x00\x00\xff", 3)
    assert sum(1 for b in mock.state.last_frame if b) == 1
    wait_for(lambda: mock.state.mode.get("mode") == "movie", 5)


# ---------------------------------------------------------------- map + exports
def test_map_pull_and_exports(bridge, mock):
    client, appmod, state = bridge
    cid = client.get("/api/status").get_json()["controllers"][0]["id"]
    r = client.post(f"/api/controllers/{cid}/map/pull").get_json()
    assert r["ok"] and r["points"] == N and r["source"] == "3d"
    # parameter count: one per colour per mapped pixel
    assert r["pixels"] == N and r["bpp"] == 3 and r["parameters"] == 3 * N
    assert r["totals"]["parameters"] == 3 * N and r["totals"]["controllers"] == 1
    c = client.get("/api/status").get_json()["controllers"][0]
    assert c["parameters"] == 3 * N
    # add a second, synthetic controller patched after the first (a helix, to exercise unwrap)
    from bridge.twinkly import Controller
    b = Controller({"ip": "10.0.0.2", "name": "C11", "universe": 9, "channel": 1, "n_led": N, "profile": "RGB",
                    "ports": [[p, p * 250, 250] for p in range(5)]})
    b.map = [[0.5 + 0.5 * math.cos(i / 50.0), i / N, 0.5 + 0.5 * math.sin(i / 50.0)] for i in range(N)]
    state.CONTROLLERS[b.id] = b
    try:
        p = client.get("/api/parameters").get_json()
        assert (p["controllers"], p["pixels"], p["parameters"], p["universes"]) == (2, 2 * N, 6 * N, 16)
        assert client.get("/api/status").get_json()["parameters"] == p
        # an RGBW controller counts four per pixel, and only the LEDs its map covers
        w = Controller({"ip": "10.0.0.3", "name": "W1", "universe": 20, "n_led": 100, "profile": "RGBW"})
        w.map = [[i, 0, 0] for i in range(60)]
        assert w.parameters == 240 and w.to_status()["parameters"] == 240
        assert Controller({"ip": "10.0.0.4", "n_led": 100}).parameters == 0           # no map yet
        for arrange in ("shared", "tiled", "separate"):
            r = client.get(f"/api/export/eos_pixelmap.csv?arrange={arrange}&flatten=unwrap&pixelmap=3")
            assert r.status_code == 200, r.data
            lines = r.data.decode().splitlines()
            assert lines[0] == "START_PIXELMAP" and lines[-1] == "END_PIXELMAP"
            assert lines[1] == "PIXELMAP,LOCATION_X,LOCATION_Y,ADDRESS,MANUFACTURER,FIXTURE_TYPE"
            rows = list(csv.DictReader(io.StringIO("\n".join(lines[1:-1]))))
            assert len(rows) == 2 * N
            assert len({(x["PIXELMAP"], x["LOCATION_X"], x["LOCATION_Y"]) for x in rows}) == 2 * N
            assert {x["PIXELMAP"] for x in rows} == ({"3"} if arrange != "separate" else {"3", "4"})
            assert all(x["FIXTURE_TYPE"] == "RGB" for x in rows)
        # address math: Eos absolute = (universe-1)*512 + offset, 170 pixels per universe
        rows = list(csv.DictReader(io.StringIO(client.get("/api/export/eos_patch.csv?mode=channel&channel_base=101").data.decode())))
        by = {(x["CONTROLLER"], int(x["LED_INDEX"])): x for x in rows}
        assert by[("C10", 0)]["ADDRESS"] == "1" and by[("C10", 0)]["PORT_OFFSET"] == "1/1"
        assert by[("C10", 170)]["ADDRESS"] == "513" and by[("C10", 170)]["PORT_OFFSET"] == "2/1"
        assert by[("C11", 0)]["ADDRESS"] == "4097" and by[("C11", 0)]["PORT_OFFSET"] == "9/1"
        assert rows[0]["CHANNEL"] == "101" and rows[-1]["CHANNEL"] == str(100 + 2 * N)
        assert by[("C11", 501)]["LABEL"] == "C11 p2 #2" and by[("C11", 501)]["DDP_BYTE"] == str(1 + 501 * 3)
        # channel-mode pixel map
        lines = client.get("/api/export/eos_pixelmap.csv?mode=channel&channel_base=101").data.decode().splitlines()
        assert lines[1] == "PIXELMAP,LOCATION_X,LOCATION_Y,CHANNEL" and lines[2].endswith(",101")
        # straddle guard
        state.CONFIG["sacn"]["channels_per_universe"] = 512
        for c in state.CONTROLLERS.values():
            c._slices = None
        r = client.get("/api/export/eos_pixelmap.csv")
        assert r.status_code == 400 and b"straddle" in r.data
        state.CONFIG["sacn"]["channels_per_universe"] = 510
        for c in state.CONTROLLERS.values():
            c._slices = None
        # preview
        r = client.get("/api/export/eos_preview.svg?flatten=unwrap")
        assert r.status_code == 200 and ET.fromstring(r.data) is not None and r.data.count(b"<rect") == 2 * N + 1
        # per-controller CSV
        r = client.get(f"/api/controllers/{cid}/map.csv")
        assert r.data.decode().splitlines()[0] == "index,port,led,x,y,z" and len(r.data.decode().splitlines()) == N + 1
    finally:
        del state.CONTROLLERS[b.id]


def test_map_pull_404_explains(bridge, mock):
    client, appmod, state = bridge
    cid = client.get("/api/status").get_json()["controllers"][0]["id"]
    mock.state.no_layout = True
    try:
        r = client.post(f"/api/controllers/{cid}/map/pull")
        assert r.status_code == 502
        err = r.get_json()["error"]
        assert "/xled/v2/led/layout/full -> 404" in err and "/xled/v1/led/layout/full -> 404" in err
        assert "camera-mapped" in err
        assert client.get("/api/status").get_json()["controllers"][0]["map_error"] == err.split("Pull map failed: ")[1]
    finally:
        mock.state.no_layout = False
    assert client.post(f"/api/controllers/{cid}/map/pull").get_json()["ok"]
    assert client.get("/api/status").get_json()["controllers"][0]["map_error"] == ""


def test_bridge_404_is_json(bridge):
    client, appmod, state = bridge
    r = client.post("/api/controllers/nope/map/pull")
    assert r.status_code == 404 and "no controller 'nope'" in r.get_json()["error"]
    r = client.get("/api/nothing")
    assert r.status_code == 404 and r.get_json()["error"].startswith("bridge:")


# ---------------------------------------------------------------- auto patch
def test_auto_patch(bridge, mock):
    client, appmod, state = bridge
    from bridge.twinkly import Controller
    extra = []
    for i in range(3):
        c = Controller({"ip": f"10.0.0.{10 + i}", "name": f"X{i}", "n_led": 1000, "profile": "RGB",
                        "group": {"mode": "slave", "offset": (3 - i) * 1000}})
        state.CONTROLLERS[c.id] = c
        extra.append(c)
    try:
        r = client.post("/api/patch/auto", json={"mode": "aligned", "start_universe": 1, "order": "group"}).get_json()
        # group offsets 1000, 2000, 3000 come first (ascending), the mock (no group) last
        assert [x["name"] for x in r] == ["X2", "X1", "X0", "C10"]
        assert r[0]["universe"] == 1 and r[0]["channel"] == 1 and r[0]["universes"] == list(range(1, 7))   # 3000 ch -> 6 universes
        assert r[1]["universe"] == 7 and r[1]["channel"] == 1                                              # fresh universe
        assert r[1]["ddp_start"] == 3001
        r = client.post("/api/patch/auto", json={"mode": "packed"}).get_json()
        assert r[1]["universe"] == 6 and r[1]["channel"] == 451                                            # 3000 = 5*510 + 450
    finally:
        for c in extra:
            del state.CONTROLLERS[c.id]
        client.put(f"/api/controllers/{client.get('/api/status').get_json()['controllers'][0]['id']}",
                   json={"universe": 1, "channel": 1})


def test_settings_persist(bridge, mock):
    client, appmod, state = bridge
    r = client.post("/api/settings", json={"output": {"fps": 25}, "sacn": {"channels_per_universe": 510}})
    assert r.get_json()["ok"]
    import json
    saved = json.load(open(state.CONFIG_PATH))
    assert saved["output"]["fps"] == 25 and saved["controllers"][0]["n_led"] == N
    client.post("/api/settings", json={"output": {"fps": 30}})
