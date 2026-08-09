#!/usr/bin/env python3
"""Minimal MOZA serial protocol primitives for the AB9 base.

Frame: 7e | len(id+payload) | group | device_id | cmd_id... | payload... | checksum
checksum = (13 + sum(frame bytes so far)) % 256
Response: group has 0x80 added, device id has nibbles swapped.

The caller must discover the AB9's current ttyACM path and assign ``PORT``.
This module intentionally has no generic command-line write interface: blind
serial writes are unsafe on a 12 N-m motor and once silenced the serial channel
on the tested base until a physical power cycle.
"""
import os, termios, time

PORT = None
MAGIC = 13
START = 0x7E

def open_port():
    if PORT is None:
        raise RuntimeError("AB9 serial path was not discovered")
    fd = os.open(PORT, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    # raw mode
    attrs[0] = 0; attrs[1] = 0; attrs[3] = 0
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    attrs[4] = termios.B115200; attrs[5] = termios.B115200
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd

def frame(group, dev, cmd_ids, payload):
    f = bytearray([START, len(cmd_ids) + len(payload), group, dev])
    f.extend(cmd_ids); f.extend(payload)
    f.append((MAGIC + sum(f)) % 256)
    return bytes(f)

def xact(fd, buf, wait=0.35):
    os.write(fd, buf)
    time.sleep(wait)
    out = b""
    try:
        while True:
            chunk = os.read(fd, 256)
            if not chunk: break
            out += chunk
    except BlockingIOError:
        pass
    return out

def hexs(b): return " ".join(f"{x:02x}" for x in b)
