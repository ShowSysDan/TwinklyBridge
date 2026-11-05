#!/usr/bin/env python3
"""Make the self-signed HTTPS certificate the phone page needs (browsers only open the
camera on https://). Called by install.sh and install.bat; stdlib only.

    python tools/make_cert.py [--force] [cert.pem key.pem]

Defaults to instance/cert.pem and instance/key.pem. Does nothing if the certificate is
already there (pass --force for a new one, e.g. after the machine's IP changed). Uses the
openssl command: on Linux / macOS the system one, on Windows the one Git for Windows ships.

iOS only accepts a server certificate valid for at most 825 days, with the names / IPs the
phone connects to in subjectAltName and extendedKeyUsage serverAuth, so this makes exactly
that; the phone still warns once (self-signed), and after "visit website" it works.
"""
import os
import shutil
import socket
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_openssl():
    found = shutil.which("openssl")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", ""),
                 os.environ.get("LOCALAPPDATA", "") + r"\Programs"):
        for sub in (r"Git\usr\bin\openssl.exe", r"Git\mingw64\bin\openssl.exe"):
            p = os.path.join(base, sub)
            if base and os.path.exists(p):
                return p
    return None


def local_ipv4():
    ips = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:                                    # the address on the default route (no packet is sent)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:                                    # Linux: every configured address
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5).stdout
        ips.update(ip for ip in out.split() if "." in ip)
    except (OSError, subprocess.SubprocessError):
        pass
    return sorted(ips)


def main(argv):
    force = "--force" in argv
    args = [a for a in argv if a != "--force"]
    cert = args[0] if args else os.path.join(ROOT, "instance", "cert.pem")
    key = args[1] if len(args) > 1 else os.path.join(ROOT, "instance", "key.pem")
    if os.path.exists(cert) and os.path.exists(key) and not force:
        print(f"HTTPS certificate already there: {cert}")
        return 0
    openssl = find_openssl()
    if not openssl:
        print("openssl not found, so no HTTPS certificate was made and phone camera mapping is off.\n"
              "  Linux: sudo apt install openssl     Windows: install Git for Windows (it includes openssl)",
              file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(os.path.abspath(cert)), exist_ok=True)
    host = socket.gethostname()
    san = ",".join([f"DNS:{host}", f"DNS:{host}.local", "DNS:localhost"] + [f"IP:{ip}" for ip in local_ipv4()])
    cmd = [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825", "-sha256",
           "-keyout", key, "-out", cert, "-subj", f"/CN=twinkly-bridge {host}",
           "-addext", f"subjectAltName={san}", "-addext", "extendedKeyUsage=serverAuth",
           "-addext", "keyUsage=digitalSignature,keyEncipherment"]
    env = dict(os.environ, MSYS_NO_PATHCONV="1")          # Git's openssl would mangle "/CN=..." otherwise
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.returncode:
        print(f"openssl failed: {r.stderr.strip()}", file=sys.stderr)
        return 1
    try:
        os.chmod(key, 0o600)
    except OSError:
        pass
    print(f"Made a self-signed HTTPS certificate for {san} (valid 825 days)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
