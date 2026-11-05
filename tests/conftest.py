import os
import socket
import sys
import tempfile
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from mock_controller import MockController  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="session")
def mock():
    m = MockController(http_port=free_port()).start()
    yield m
    m.stop()


@pytest.fixture(scope="session")
def bridge(mock):
    """The Flask app with its threads running, config in a temp file, mock controller added."""
    os.environ["TWINKLY_BRIDGE_CONFIG"] = os.path.join(tempfile.mkdtemp(), "bridge_config.json")
    from bridge import app as appmod, state
    state.CONFIG_PATH = os.environ["TWINKLY_BRIDGE_CONFIG"]
    state.load_config(state.CONFIG_PATH)
    state.CONFIG["sacn"]["multicast"] = False
    state.CONFIG["sacn"]["idle_timeout_s"] = 1.0
    appmod.load_controllers()
    appmod.start_threads()
    client = appmod.app.test_client()
    r = client.post("/api/controllers", json={"ip": f"127.0.0.1:{mock.http_port}", "universe": 1, "channel": 1})
    assert r.status_code == 201, r.data
    time.sleep(0.3)
    yield client, appmod, state
