"""Shared state: configuration, the controller registry, and the lock that guards them."""
import json
import os
import threading

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(PACKAGE_DIR)
CONFIG_PATH = os.environ.get("TWINKLY_BRIDGE_CONFIG") or os.path.join(ROOT_DIR, "bridge_config.json")

DEFAULT_CONFIG = {
    "sacn": {"interface_ip": "0.0.0.0", "multicast": True, "channels_per_universe": 510,
             "idle_timeout_s": 5.0},
    "ddp": {"enabled": True},
    "output": {"fps": 30, "fragment_gap_ms": 2, "rt_reassert_s": 30, "restore_mode_on_idle": True},
    "controllers": [],
}

lock = threading.RLock()
CONFIG = json.loads(json.dumps(DEFAULT_CONFIG))
CONTROLLERS = {}          # id -> Controller (filled by app.load_controllers)


def load_config(path=None):
    """Load bridge_config.json over the defaults, in place (modules hold references to CONFIG)."""
    path = path or CONFIG_PATH
    fresh = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(path):
        try:
            with open(path) as f:
                saved = json.load(f)
            for k in ("sacn", "ddp", "output"):
                fresh[k].update(saved.get(k, {}))
            fresh["controllers"] = saved.get("controllers", [])
        except Exception as e:                       # noqa: BLE001
            print("config unreadable, starting fresh:", e)
    with lock:
        CONFIG.clear()
        CONFIG.update(fresh)
    return CONFIG


def save_config(path=None):
    path = path or CONFIG_PATH
    with lock:
        data = {"sacn": CONFIG["sacn"], "ddp": CONFIG["ddp"], "output": CONFIG["output"],
                "controllers": [c.to_config() for c in CONTROLLERS.values()]}
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def wanted_universes():
    cpu = CONFIG["sacn"]["channels_per_universe"]
    us = set()
    with lock:
        for c in CONTROLLERS.values():
            if c.source == "sacn":
                us.update(c.universes(cpu))
    return sorted(us)
