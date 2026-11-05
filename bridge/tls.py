"""HTTPS for the phone. Phone browsers only open the camera on an https:// page, so the
bridge also serves the same app (same access controls) over TLS on HTTPS_PORT (default
5121), from a thread inside the bridge process, under run.py and gunicorn alike. It binds
every interface by default (HTTPS_HOST overrides): it exists only for a phone on the LAN.

The certificate is instance/cert.pem + instance/key.pem (TLS_CERT / TLS_KEY override).
install.sh and install.bat make a self-signed one; a phone accepts its warning once.

Each connection is handled in its own thread: the first byte tells TLS (0x16, a
ClientHello) from plain HTTP. Plain http:// on this port - what Safari does when the address
is typed without https:// - gets a redirect to the https:// address instead of a dropped
connection. The TLS handshake also happens in that thread, so a phone sitting on the
certificate warning (or asleep) can't hold up the others.
"""
import os
import socket
import ssl
import subprocess
import threading

from werkzeug.serving import ThreadedWSGIServer

from . import state
from .logs import get

log = get("tls")
TIMEOUT = 15
STATUS = {"port": 0, "host": "", "listening": False, "cert": "", "names": [], "error": ""}


class _Server(ThreadedWSGIServer):
    def __init__(self, host, port, app, context):
        super().__init__(host, port, app)          # plain socket; TLS per connection below
        self.ssl_context = context                 # Werkzeug reads this for wsgi.url_scheme

    def finish_request(self, request, client_address):
        request.settimeout(TIMEOUT)
        try:
            first = request.recv(1, socket.MSG_PEEK)
        except OSError:
            return
        if not first:
            return
        if first != b"\x16":
            _redirect(request, self.server_address[1])
            return
        tls = self.ssl_context.wrap_socket(request, server_side=True)    # handshake here, in this thread
        try:
            self.RequestHandlerClass(tls, client_address, self)
        finally:
            try:
                tls.close()
            except OSError:
                pass

    def handle_error(self, request, client_address):
        pass                                        # refused certificate, timeouts: just drop

    def shutdown_request(self, request):
        try:
            super().shutdown_request(request)
        except (OSError, ValueError):
            pass


def _redirect(sock, port):
    """Plain HTTP arrived on the HTTPS port: send the browser to the same URL over https://."""
    try:
        head = b""
        while b"\r\n\r\n" not in head and len(head) < 8192:
            chunk = sock.recv(2048)
            if not chunk:
                break
            head += chunk
        lines = head.decode("latin1").split("\r\n")
        parts = lines[0].split(" ")
        path = parts[1] if len(parts) > 1 and parts[1].startswith("/") else "/map"
        host = next((ln.split(":", 1)[1].strip() for ln in lines[1:] if ln.lower().startswith("host:")), "")
        host = host or sock.getsockname()[0]
        if ":" not in host.rsplit("]", 1)[-1]:
            host = f"{host}:{port}"
        url = f"https://{host}{path}"
        body = (f"This port is HTTPS: {url}\n").encode()
        sock.sendall(b"HTTP/1.1 301 Moved Permanently\r\nLocation: " + url.encode() +
                     b"\r\nContent-Type: text/plain\r\nContent-Length: " + str(len(body)).encode() +
                     b"\r\nConnection: close\r\n\r\n" + body)
    except OSError:
        pass


def paths():
    inst = os.path.join(state.ROOT_DIR, "instance")
    return (os.environ.get("TLS_CERT") or os.path.join(inst, "cert.pem"),
            os.environ.get("TLS_KEY") or os.path.join(inst, "key.pem"))


def cert_names(cert):
    """The DNS names / IPs a certificate is for (from openssl if it is there; best effort)."""
    try:
        out = subprocess.run(["openssl", "x509", "-in", cert, "-noout", "-ext", "subjectAltName"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [n.split(":", 1)[1].strip() for n in out.replace("\n", ",").split(",")
            if n.strip().startswith(("DNS:", "IP Address:"))]


def lan_addresses():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))                   # no packet is sent
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def start(app, host, port):
    """Start the HTTPS listener if a certificate is there. Returns the port, or 0 if not started."""
    STATUS.update(port=port, host=host, listening=False, error="")
    if not port:
        STATUS["error"] = "HTTPS_PORT is 0 (off)"
        return 0
    cert, key = paths()
    STATUS["cert"] = cert
    if not (os.path.exists(cert) and os.path.exists(key)):
        STATUS["error"] = (f"no certificate at {cert}: re-run install.sh / install.bat "
                           f"(or python tools/make_cert.py), then restart the bridge")
        log.warning("HTTPS for phone camera mapping is off: %s", STATUS["error"])
        return 0
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cert, key)
        srv = _Server(host, port, app, ctx)
    except (OSError, ssl.SSLError) as e:
        STATUS["error"] = f"cannot serve HTTPS on {host}:{port}: {e}"
        log.error(STATUS["error"])
        return 0
    STATUS["names"] = cert_names(cert)
    STATUS["listening"] = True
    threading.Thread(target=srv.serve_forever, daemon=True, name="https").start()
    urls = [f"https://{ip}:{port}/map" for ip in lan_addresses()] or [f"https://<this machine's IP>:{port}/map"]
    log.info("HTTPS for phone camera mapping on %s:%d. On the phone open: %s", host, port, "  or  ".join(urls))
    return port
