"""DDP receiver (http://www.3waylabs.com/ddp/). A second input for media servers and
players that prefer it over sACN: one flat byte stream, no universes. Each packet carries a byte offset and
a payload; we keep a flat buffer and controllers read their range from it.

Header (10 bytes, 14 with timecode):
  0 flags   0x40 = version 1, 0x01 push, 0x02 query, 0x04 reply, 0x08 storage, 0x10 timecode
  1 sequence (low 4 bits)
  2 data type (ignored, we assume packed 8-bit RGB/RGBW as patched)
  3 destination id (1 = default output)
  4-7 offset (big-endian)   8-9 length (big-endian)   [10-13 timecode]
"""
import socket
import struct
import threading
import time

from .state import CONFIG

DDP_PORT = 4048
CHUNK = 1024                     # granularity for "data seen recently at this offset"
MAX_BUFFER = 4 * 1024 * 1024


def parse_ddp(d):
    """Returns (offset, payload) or None."""
    if len(d) < 10 or (d[0] & 0xC0) != 0x40:
        return None
    flags = d[0]
    if flags & 0x06:                               # query / reply: not data
        return None
    hdr = 14 if flags & 0x10 else 10
    offset, length = struct.unpack("!IH", d[4:10])
    payload = d[hdr:hdr + length]
    if not payload:
        return None
    return offset, payload


class DdpReceiver(threading.Thread):
    def __init__(self, port=DDP_PORT):
        super().__init__(daemon=True, name="ddp")
        self.port = port
        self.buffer = bytearray()
        self.seen = {}               # chunk index -> time
        self.packets = 0
        self.rate = 0.0
        self.error = ""
        self.sock = None
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        last_tick, count = time.time(), 0
        while not self._stop.is_set():
            if not CONFIG["ddp"]["enabled"]:
                if self.sock:
                    self.sock.close()
                    self.sock = None
                time.sleep(0.5)
                continue
            if self.sock is None:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(("", self.port))
                    s.settimeout(0.5)
                    self.sock, self.error = s, ""
                except OSError as e:
                    self.error = f"cannot listen on UDP {self.port}: {e}"
                    time.sleep(2)
                    continue
            try:
                data, _ = self.sock.recvfrom(2048)
            except socket.timeout:
                data = None
            except ConnectionResetError:
                continue
            except OSError as e:
                self.error = f"receive: {e}"
                time.sleep(0.5)
                continue
            if data and self.handle(data):
                count += 1
            now = time.time()
            if now - last_tick >= 1.0:
                self.rate = count / (now - last_tick)
                count, last_tick = 0, now
        if self.sock:
            self.sock.close()

    def handle(self, d):
        p = parse_ddp(d)
        if p is None:
            return False
        offset, payload = p
        end = offset + len(payload)
        if end > MAX_BUFFER:
            return False
        if end > len(self.buffer):
            self.buffer.extend(bytes(end - len(self.buffer)))
        self.buffer[offset:end] = payload
        now = time.time()
        for ch in range(offset // CHUNK, (end - 1) // CHUNK + 1):
            self.seen[ch] = now
        self.packets += 1
        return True

    def live(self, start, length, idle):
        """True if any part of [start, start+length) received data within idle seconds."""
        now = time.time()
        return any(now - self.seen.get(ch, 0) < idle
                   for ch in range(start // CHUNK, max(start, start + length - 1) // CHUNK + 1))

    def status(self):
        now = time.time()
        idle = CONFIG["sacn"]["idle_timeout_s"]
        return {"enabled": CONFIG["ddp"]["enabled"], "listening": self.sock is not None, "port": self.port,
                "error": self.error, "packets": self.packets, "rate": round(self.rate, 1),
                "active": any(now - t < idle for t in self.seen.values()),
                "bytes": len(self.buffer)}
