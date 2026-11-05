"""The phone's HTTPS listener: serves the app over TLS, redirects plain http:// on the same
port, and a stalled connection doesn't block others. Needs openssl for the test certificate."""
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request

import pytest
from flask import Flask

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def https():
    if not shutil.which("openssl"):
        pytest.skip("openssl not installed")
    d = tempfile.mkdtemp()
    cert, key = os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "make_cert.py"), cert, key], check=True,
                   capture_output=True)
    os.environ["TLS_CERT"], os.environ["TLS_KEY"] = cert, key
    from bridge import tls
    app = Flask("t")

    @app.get("/map")
    def page():
        return "phone page"

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    try:
        assert tls.start(app, "127.0.0.1", port) == port
        yield port, tls
    finally:
        os.environ.pop("TLS_CERT")
        os.environ.pop("TLS_KEY")


def get(url, **kw):
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))
    return opener.open(url, timeout=5, **kw)


def test_serves_https(https):
    port, tls = https
    with get(f"https://127.0.0.1:{port}/map") as r:
        assert r.status == 200 and r.read() == b"phone page"
    assert tls.STATUS["listening"] and "127.0.0.1" in tls.STATUS["names"]


def test_plain_http_is_redirected(https):
    port, _ = https
    s = socket.create_connection(("127.0.0.1", port))
    s.sendall(f"GET /map HTTP/1.1\r\nHost: 10.1.2.3:{port}\r\n\r\n".encode())
    reply = s.recv(1000).decode()
    assert reply.startswith("HTTP/1.1 301") and f"Location: https://10.1.2.3:{port}/map" in reply
    with get(f"http://127.0.0.1:{port}/map") as r:          # urllib follows it to https
        assert r.status == 200 and r.geturl() == f"https://127.0.0.1:{port}/map"


def test_stalled_connections_dont_block(https):
    port, _ = https
    silent = socket.create_connection(("127.0.0.1", port))
    half = socket.create_connection(("127.0.0.1", port))
    half.sendall(b"\x16\x03\x01")                           # a TLS hello that never finishes
    t = time.time()
    with get(f"https://127.0.0.1:{port}/map") as r:
        assert r.status == 200
    assert time.time() - t < 2
    silent.close()
    half.close()


def test_no_certificate_says_why(tmp_path, monkeypatch):
    from bridge import tls
    monkeypatch.setenv("TLS_CERT", str(tmp_path / "nope.pem"))
    monkeypatch.setenv("TLS_KEY", str(tmp_path / "nope.key"))
    status = dict(tls.STATUS)
    try:
        assert tls.start(Flask("x"), "127.0.0.1", 5999) == 0
        assert "no certificate" in tls.STATUS["error"] and not tls.STATUS["listening"]
    finally:
        tls.STATUS.update(status)
