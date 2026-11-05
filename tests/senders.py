"""Minimal sACN (E1.31) and DDP packet builders/senders for tests and manual checks."""
import socket
import struct
import time
import uuid

CID = uuid.uuid4().bytes
NAME = b"twinkly-bridge-test".ljust(64, b"\0")


def e131_packet(universe, data, seq=0, priority=100, options=0):
    dmp = struct.pack("!HBBHHH", 0x7000 | (10 + 1 + len(data)), 0x02, 0xA1, 0, 1, len(data) + 1) + b"\x00" + data
    framing = (struct.pack("!HI", 0x7000 | (77 + 1 + len(data) - 38), 2) + NAME
               + struct.pack("!BHBBH", priority, 0, seq & 255, options, universe))
    root = struct.pack("!HH12sHI16s", 0x10, 0, b"ASC-E1.17\0\0\0", 0x7000 | (126 + len(data) - 16), 4, CID)
    return root + framing + dmp


def ddp_packet(offset, data, push=True):
    return struct.pack("!BBBBIH", 0x40 | (0x01 if push else 0), 0, 1, 1, offset, len(data)) + data


def send_sacn(channels, cpu=510, seconds=2.0, fps=40, host="127.0.0.1", port=5568):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    seq, end, sent = 0, time.time() + seconds, 0
    n_uni = (len(channels) + cpu - 1) // cpu
    while time.time() < end:
        for u in range(1, n_uni + 1):
            chunk = bytes(channels[(u - 1) * cpu:u * cpu]).ljust(cpu, b"\0")
            s.sendto(e131_packet(u, chunk, seq), (host, port))
            sent += 1
        seq += 1
        time.sleep(1 / fps)
    return sent


def send_ddp(channels, seconds=2.0, fps=40, host="127.0.0.1", port=4048, chunk=1440):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    end, sent = time.time() + seconds, 0
    while time.time() < end:
        for off in range(0, len(channels), chunk):
            s.sendto(ddp_packet(off, bytes(channels[off:off + chunk]), push=off + chunk >= len(channels)), (host, port))
            sent += 1
        time.sleep(1 / fps)
    return sent


if __name__ == "__main__":
    import sys
    n = 1250
    ch = bytearray(bytes([8, 8, 8]) * n)
    ch[0:3] = b"\xff\x00\x00"; ch[501 * 3:501 * 3 + 3] = b"\x00\xff\x00"; ch[-3:] = b"\x00\x00\xff"
    if len(sys.argv) > 1 and sys.argv[1] == "ddp":
        print("sent", send_ddp(ch, seconds=3), "DDP packets")
    else:
        print("sent", send_sacn(ch, seconds=3), "sACN packets")
