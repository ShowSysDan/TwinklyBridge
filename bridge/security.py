"""HTTP hardening for the web UI and the REST API, the same layers as the other ShowSysDan
apps. The bridge normally lives on a closed show network, so every layer is optional:

* ALLOWED_NETWORKS  IP / CIDR allowlist for every HTTP request.
* PANEL_PASSWORD    HTTP Basic auth on the UI and /api/ (user PANEL_USER, default "twinkly").
* Cross-site request blocking: a request a browser marks `Sec-Fetch-Site: cross-site` is
  refused, so a web page open in an operator's browser cannot repatch the bridge or flash
  a controller. Scripts and curl never send that header, so they are unaffected.
* JSON-only mutating endpoints (an HTML form cannot send JSON). The map CSV upload is the
  one multipart endpoint, and the cross-site check covers it.
* Response headers: CSP with no inline script, no framing, no sniffing, API never cached.
"""
import hmac
import ipaddress

from flask import jsonify, request

from .logs import get

log = get("security")

CSP = ("default-src 'self'; script-src 'self'; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
       "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
       "form-action 'self'; object-src 'none'")

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

# Monitoring may poll these without credentials; they reveal only "alive" and counts.
UNAUTHENTICATED = ("/healthz",)
# The one mutating endpoint that takes a file upload instead of JSON.
MULTIPART_OK = ("/map/csv",)


def parse_networks(spec):
    """'10.1.1.0/24, 192.168.10.5' -> [ip_network, ...]. Raises ValueError."""
    nets = []
    for part in (spec or "").replace(";", ",").split(","):
        part = part.strip()
        if part:
            nets.append(ipaddress.ip_network(part, strict=False))
    return nets


def address_allowed(addr, networks):
    if not networks:
        return True
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in net for net in networks)


def constant_time_equal(a, b):
    if a is None or b is None:
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _json_error(status, message):
    return jsonify({"ok": False, "error": message}), status


def init_app(app, allowed_networks="", panel_user="twinkly", panel_password=None):
    """Install the request gate and response headers. Returns warnings about what is still open."""
    try:
        networks = parse_networks(allowed_networks)
    except ValueError as exc:
        raise RuntimeError(f"ALLOWED_NETWORKS is not valid: {exc}") from exc
    panel_user = panel_user or "twinkly"

    @app.before_request
    def gate():
        if not address_allowed(request.remote_addr or "", networks):
            log.warning("refused HTTP request from %s (not in ALLOWED_NETWORKS)", request.remote_addr)
            return _json_error(403, "address not allowed")
        if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
            log.warning("refused cross-site request from %s to %s", request.remote_addr, request.path)
            return _json_error(403, "cross-site requests are not accepted")
        if request.path in UNAUTHENTICATED:
            return None
        if panel_password:
            auth = request.authorization
            if (auth is None or auth.type != "basic"
                    or not constant_time_equal(auth.username, panel_user)
                    or not constant_time_equal(auth.password, panel_password)):
                resp = jsonify({"ok": False, "error": "authentication required"})
                resp.status_code = 401
                resp.headers["WWW-Authenticate"] = 'Basic realm="Twinkly bridge"'
                return resp
        if (request.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE")
                and request.content_length and not request.is_json
                and not request.path.endswith(MULTIPART_OK)):
            return _json_error(415, "send a JSON body")
        return None

    @app.after_request
    def headers(resp):
        for name, value in SECURITY_HEADERS.items():
            resp.headers.setdefault(name, value)
        if request.path.startswith("/api/") or request.path in UNAUTHENTICATED:
            resp.headers["Cache-Control"] = "no-store"
        return resp

    warnings = []
    if not networks:
        warnings.append("ALLOWED_NETWORKS is not set: any address that can reach the web port can use the UI")
    if not panel_password:
        warnings.append("PANEL_PASSWORD is not set: the web UI and /api/ need no login")
    return warnings
