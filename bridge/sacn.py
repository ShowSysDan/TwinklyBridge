"""sACN (ANSI E1.31) receiver: keeps the latest DMX slots per universe."""
import socket
import struct
import threading
import time

from .state import CONFIG, wanted_universes

SACN_PORT = 5568
ACN_ID = b"ASC-E1.17\x00\x00\x00"


def parse_e131(d):
    """Returns (universe, priority, options, source_name, dmx_bytes) or None for anything
    that isn't an E1.31 data packet with the DMX start code."""
    if len(d) < 126 or d[4:16] != ACN_ID:
        return None
    if struct.unpack("!I", d[18:22])[0] != 4 or struct.unpack("!I", d[40:44])[0] != 2:
        return None
    if d[117] != 0x02 or d[125] != 0x00:
        return None
    priority, options = d[108], d[112]
    universe = struct.unpack("!H", d[113:115])[0]
    count = struct.unpack("!H", d[123:125])[0]
    source = d[44:108].split(b"\x00", 1)[0].decode("utf-8", "replace")
    return universe, priority, options, source, bytes(d[126:126 + max(0, count - 1)])


class SacnReceiver(threading.Thread):
    def __init__(self, port=SACN_PORT):
        super().__init__(daemon=True, name="sacn")
        self.port = port
        self.universes = {}          # universe -> bytes
        self.last_seen = {}          # universe -> time
        self.priority = {}           # universe -> (priority, source)
        self.sources = {}
        self.packets = 0
        self.rate = 0.0
        self.error = ""
        self.joined = set()
        self.sock = None
        self._restart = threading.Event()
        self._stop = threading.Event()

    def open(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        except OSError:
            pass
        s.bind(("", self.port))
        s.settimeout(0.5)
        self.sock, self.joined = s, set()
        self.error = ""

    def join(self, universes):
        if not CONFIG["sacn"]["multicast"] or not self.sock:
            return
        iface = CONFIG["sacn"]["interface_ip"] or "0.0.0.0"
        for u in universes:
            if u in self.joined or not 1 <= u <= 63999:
                continue
            group = f"239.255.{u >> 8}.{u & 255}"
            try:
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                     socket.inet_aton(group) + socket.inet_aton(iface))
                self.joined.add(u)
            except OSError as e:
                self.error = f"multicast join {group} on {iface}: {e}"

    def restart(self):
        self._restart.set()

    def stop(self):
        self._stop.set()

    def run(self):
        last_tick, count = time.time(), 0
        while not self._stop.is_set():
            if self.sock is None or self._restart.is_set():
                self._restart.clear()
                try:
                    if self.sock:
                        self.sock.close()
                    self.open()
                    self.join(wanted_universes())
                except OSError as e:
                    self.error = f"cannot listen on UDP {self.port}: {e}"
                    self.sock = None
                    time.sleep(2)
                    continue
            try:
                data, _ = self.sock.recvfrom(2048)
            except socket.timeout:
                data = None
            except ConnectionResetError:          # Windows: ICMP unreachable surfaces here
                continue
            except OSError as e:
                self.error = f"receive: {e}"
                time.sleep(0.5)
                continue
            if data:
                if self.handle(data):
                    count += 1
            now = time.time()
            if now - last_tick >= 1.0:
                self.rate = count / (now - last_tick)
                count, last_tick = 0, now
        if self.sock:
            self.sock.close()

    def handle(self, d):
        p = parse_e131(d)
        if p is None:
            return False
        universe, priority, options, source, dmx = p
        now = time.time()
        if options & 0x40:                            # stream terminated
            self.last_seen[universe] = 0
            return True
        cur = self.priority.get(universe)
        if cur and cur[0] > priority and now - self.last_seen.get(universe, 0) < 2.5:
            return False                              # a higher-priority source owns this universe
        self.universes[universe] = dmx
        self.last_seen[universe] = now
        self.priority[universe] = (priority, source)
        self.sources[universe] = source
        self.packets += 1
        return True

    def status(self):
        now = time.time()
        idle = CONFIG["sacn"]["idle_timeout_s"]
        active = sorted(u for u, t in self.last_seen.items() if now - t < idle)
        return {"listening": self.sock is not None, "port": self.port, "error": self.error,
                "packets": self.packets, "rate": round(self.rate, 1), "active_universes": active,
                "sources": sorted({self.sources[u] for u in active}),
                "joined": sorted(self.joined), "multicast": CONFIG["sacn"]["multicast"],
                "interface_ip": CONFIG["sacn"]["interface_ip"]}
