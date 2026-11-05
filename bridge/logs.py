"""Logging: the console (journald under systemd), local syslog, and optionally a remote
syslog server, all from the standard `logging` module.

Everything the bridge logs goes under the `twinkly_bridge` logger. Operator actions
(controllers added, repatched, maps pulled, settings saved) are INFO; a controller that
stops answering is WARNING. Set in .env:

    LOG_LEVEL=INFO
    SYSLOG_HOST=10.1.1.20      remote syslog server (unset = local syslog only)
    SYSLOG_PORT=514
    SYSLOG_PROTO=udp           udp or tcp
    SYSLOG_FACILITY=local0
"""
import logging
import logging.handlers
import os
import socket

TAG = "twinkly-bridge"
_configured = False


class _Tagged(logging.Formatter):
    """RFC 3164 style: 'twinkly-bridge[pid]: LEVEL logger message'."""

    def format(self, record):
        return f"{TAG}[{os.getpid()}]: {record.levelname} {record.name} {record.getMessage()}"


def _local_syslog_address():
    for path in ("/dev/log", "/var/run/syslog"):
        if os.path.exists(path):
            return path
    return None


def setup(level=None):
    """Idempotent. Called by run.py and the WSGI entry point before anything is started."""
    global _configured
    if _configured:
        return
    _configured = True
    root = logging.getLogger("twinkly_bridge")
    root.setLevel((level or os.environ.get("LOG_LEVEL") or "INFO").upper())
    root.propagate = False

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(console)

    facility_name = (os.environ.get("SYSLOG_FACILITY") or "local0").lower()
    facility = logging.handlers.SysLogHandler.facility_names.get(
        facility_name, logging.handlers.SysLogHandler.LOG_LOCAL0)

    local = _local_syslog_address()
    if local:
        try:
            h = logging.handlers.SysLogHandler(address=local, facility=facility)
            h.setFormatter(_Tagged())
            root.addHandler(h)
        except OSError:
            pass

    host = (os.environ.get("SYSLOG_HOST") or "").strip()
    if host:
        port = int(os.environ.get("SYSLOG_PORT") or 514)
        tcp = (os.environ.get("SYSLOG_PROTO") or "udp").lower() == "tcp"
        try:
            h = logging.handlers.SysLogHandler(address=(host, port), facility=facility,
                                               socktype=socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM)
            h.setFormatter(_Tagged())
            root.addHandler(h)
        except OSError as e:
            root.warning("remote syslog %s:%s unavailable: %s", host, port, e)


def get(name):
    return logging.getLogger(f"twinkly_bridge.{name}")
