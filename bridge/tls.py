"""HTTPS for the phone. Phone browsers only open the camera on an https:// page, so the
bridge also serves the same app (same access controls) over TLS on HTTPS_PORT (default
5121), from a thread inside the bridge process, under run.py and gunicorn alike.

The certificate is instance/cert.pem + instance/key.pem (TLS_CERT / TLS_KEY override).
install.sh and install.bat make a self-signed one; a phone accepts its warning once.

Each connection's TLS handshake runs in that connection's own thread, not in the accept
loop, so a phone sitting on the certificate warning (or asleep) can't hold up the others.
"""
import os
import ssl
import threading

from werkzeug.serving import ThreadedWSGIServer

from . import state
from .logs import get

log = get("tls")
HANDSHAKE_TIMEOUT = 15


class _Server(ThreadedWSGIServer):
    def __init__(self, host, port, app, context):
        super().__init__(host, port, app)          # plain socket; TLS per connection below
        self.ssl_context = context                 # Werkzeug reads this for wsgi.url_scheme

    def get_request(self):
        sock, addr = self.socket.accept()
        sock.settimeout(HANDSHAKE_TIMEOUT)
        return self.ssl_context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr

    def handle_error(self, request, client_address):
        pass                                        # refused certificate, plain http, timeouts: just drop


def paths():
    inst = os.path.join(state.ROOT_DIR, "instance")
    return (os.environ.get("TLS_CERT") or os.path.join(inst, "cert.pem"),
            os.environ.get("TLS_KEY") or os.path.join(inst, "key.pem"))


def start(app, host, port):
    """Start the HTTPS listener if a certificate is there. Returns the port, or 0 if not started."""
    if not port:
        return 0
    cert, key = paths()
    if not (os.path.exists(cert) and os.path.exists(key)):
        log.warning("no certificate (%s): HTTPS for phone camera mapping is off. "
                    "Re-run install.sh / install.bat, or see README 'Camera mapping'.", cert)
        return 0
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    try:
        srv = _Server(host, port, app, ctx)
    except OSError as e:
        log.error("cannot listen for HTTPS on %s:%d: %s", host, port, e)
        return 0
    threading.Thread(target=srv.serve_forever, daemon=True, name="https").start()
    log.info("HTTPS (phone camera mapping) on https://%s:%d/map", host, port)
    return port
