"""Test patterns: exact bytes for each pattern, and a pattern reaching the mock controller."""
import pytest

from bridge.patterns import PATTERNS, render
from bridge.twinkly import Controller

N = 1250


def ctrl(profile="RGB"):
    return Controller({"ip": "10.0.0.9", "n_led": N, "profile": profile,
                       "ports": [[p, p * 250, 250] for p in range(5)]})


def px(frame, i, bpp=3):
    return tuple(frame[i * bpp:i * bpp + bpp])


def test_every_pattern_fills_the_whole_frame():
    for profile, bpp in (("RGB", 3), ("RGBW", 4)):
        c = ctrl(profile)
        for name in PATTERNS:
            assert len(render(c, name, 0.7, {"rgb": [255, 0, 0], "speed": 25})) == N * bpp


def test_channels_steps_colour_order():
    c = ctrl()
    assert {px(render(c, "channels", 0.1, {}), i) for i in (0, 600, N - 1)} == {(255, 0, 0)}
    assert px(render(c, "channels", 1.6, {}), 7) == (0, 255, 0)
    assert px(render(c, "channels", 3.1, {}), 7) == (0, 0, 255)
    assert px(render(c, "channels", 4.6, {}), 7) == (255, 255, 255)
    w = ctrl("RGBW")                                    # white step is the W channel alone, sent first
    assert px(render(w, "channels", 4.6, {}), 7, 4) == (255, 0, 0, 0)
    assert px(render(w, "channels", 0.1, {}), 7, 4) == (0, 255, 0, 0)


def test_ports_marks_boundaries():
    f = render(ctrl(), "ports", 0, {})
    assert px(f, 0) == (255, 255, 255) and px(f, 1) == (255, 0, 0) and px(f, 249) == (40, 40, 40)
    assert px(f, 250) == (255, 255, 255) and px(f, 251) == (0, 255, 0) and px(f, 1001) == (255, 0, 255)


def test_markers_and_gradient():
    f = render(ctrl(), "markers", 0, {})
    assert px(f, 0) == (0, 255, 0) and px(f, 100) == (255, 0, 0) and px(f, 50) == (255, 200, 0)
    assert px(f, 10) == (40, 40, 40) and px(f, 11) == (0, 0, 0)
    g = render(ctrl(), "gradient", 0, {})
    assert px(g, 0) == (0, 0, 0) and px(g, 249) == (255, 255, 255) and px(g, 250) == (0, 0, 0)


def test_chase_moves_and_rainbow_differs():
    c = ctrl()
    f = render(c, "chase", 2.0, {"speed": 25, "rgb": [0, 0, 255]})
    assert px(f, 50) == (0, 0, 255) and px(f, 51) == (0, 0, 0) and px(f, 49) != (0, 0, 0)
    a, b = render(c, "rainbow", 0, {}), render(c, "rainbow", 1, {})
    assert a != b and len({px(a, i) for i in range(250)}) > 200


def test_unknown_pattern():
    with pytest.raises(ValueError):
        render(ctrl(), "nope", 0, {})


def test_pattern_reaches_controller(bridge, mock):
    import time
    client, appmod, state = bridge
    assert set(client.get("/api/patterns").get_json()) == set(PATTERNS)
    cid = client.get("/api/status").get_json()["controllers"][0]["id"]
    r = client.post("/api/test", json={"ids": [cid], "mode": "pattern", "pattern": "markers", "seconds": 2})
    assert r.get_json()["ok"]
    end = time.time() + 3
    while time.time() < end and mock.state.last_frame[300:303] != b"\xff\x00\x00":
        time.sleep(0.05)
    f = mock.state.last_frame
    assert f[0:3] == b"\x00\xff\x00" and f[300:303] == b"\xff\x00\x00" and f[33:36] == b"\x00\x00\x00"
    s = client.get("/api/status").get_json()["controllers"][0]
    assert s["testing"] and s["test_pattern"] == "markers"
    assert client.post("/api/test", json={"mode": "pattern", "pattern": "bogus"}).status_code == 400
    client.post("/api/test", json={"mode": "stop"})
    assert client.get("/api/status").get_json()["controllers"][0]["testing"] is False
