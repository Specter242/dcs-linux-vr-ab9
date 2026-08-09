#!/usr/bin/env python3
"""Bridge Baballonia's generic eye OSC output into OpenXR Eye Trackers OSC."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import socket
import struct
import time


EYE_PATHS = {
    "/LeftEyeX": "left_x",
    "/LeftEyeY": "left_y",
    "/RightEyeX": "right_x",
    "/RightEyeY": "right_y",
}


def padded_osc_string(value: str) -> bytes:
    encoded = value.encode("ascii") + b"\0"
    return encoded + b"\0" * (-len(encoded) % 4)


def gaze_packet(left_pitch: float, left_yaw: float, right_pitch: float, right_yaw: float) -> bytes:
    return (
        padded_osc_string("/tracking/eye/LeftRightPitchYaw")
        + padded_osc_string(",ffff")
        + struct.pack(">ffff", left_pitch, left_yaw, right_pitch, right_yaw)
    )


def parse_single_float(packet: bytes) -> tuple[str, float] | None:
    try:
        address_end = packet.index(0)
        address = packet[:address_end].decode("ascii")
        type_offset = (address_end + 4) & ~3
        type_end = packet.index(0, type_offset)
        type_tag = packet[type_offset:type_end]
        value_offset = (type_end + 4) & ~3
        if type_tag != b",f" or value_offset + 4 > len(packet):
            return None
        value = struct.unpack_from(">f", packet, value_offset)[0]
        if not math.isfinite(value):
            return None
        return address, value
    except (UnicodeDecodeError, ValueError, struct.error):
        return None


def read_yaw_offset(path: Path, previous: float) -> float:
    """Read a live global yaw correction in degrees, keeping the last good value."""
    try:
        value = float(path.read_text(encoding="utf-8").strip())
        if not math.isfinite(value):
            raise ValueError("not finite")
        return max(-15.0, min(15.0, value))
    except (OSError, ValueError):
        return previous


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-port", type=int, default=8888)
    parser.add_argument("--target-port", type=int, default=9020)
    parser.add_argument("--max-hz", type=float, default=60.0)
    parser.add_argument(
        "--yaw-offset-file",
        type=Path,
        default=Path.home() / ".config/dcs-linux/eye-yaw-offset-degrees",
        help="live global horizontal gaze correction in degrees",
    )
    args = parser.parse_args()

    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    receiver.bind(("127.0.0.1", args.listen_port))

    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = ("127.0.0.1", args.target_port)
    values: dict[str, float] = {}
    minimum_interval = 1.0 / max(args.max_hz, 1.0)
    last_sent = 0.0
    sent = 0
    last_report = time.monotonic()
    last_offset_read = 0.0
    yaw_offset = 0.0
    report_count = 0
    report_sums = {key: 0.0 for key in EYE_PATHS.values()}

    print(
        f"Eye OSC bridge listening on 127.0.0.1:{args.listen_port}, "
        f"sending gaze to 127.0.0.1:{args.target_port}",
        flush=True,
    )

    while True:
        parsed = parse_single_float(receiver.recv(4096))
        if parsed is None:
            continue

        address, value = parsed
        key = EYE_PATHS.get(address)
        if key is None:
            continue
        values[key] = value

        now = time.monotonic()
        if len(values) < 4 or now - last_sent < minimum_interval:
            continue

        if now - last_offset_read >= 1.0:
            new_yaw_offset = read_yaw_offset(args.yaw_offset_file, yaw_offset)
            if new_yaw_offset != yaw_offset:
                print(
                    f"Eye yaw offset changed: {yaw_offset:+.2f} -> "
                    f"{new_yaw_offset:+.2f} degrees",
                    flush=True,
                )
            yaw_offset = new_yaw_offset
            last_offset_read = now

        # Match Baballonia's DFR conversion and the OpenXR layer's expected
        # argument order: left pitch/yaw followed by right pitch/yaw, degrees.
        # The X crossover was correct for the tested Beyond 2e camera ordering;
        # verify it on other hardware rather than assuming it is universal.
        packet = gaze_packet(
            values["left_y"] * -45.0,
            values["right_x"] * 45.0 + yaw_offset,
            values["right_y"] * -45.0,
            values["left_x"] * 45.0 + yaw_offset,
        )
        sender.sendto(packet, target)
        sent += 1
        report_count += 1
        for report_key in report_sums:
            report_sums[report_key] += values[report_key]
        last_sent = now

        if now - last_report >= 5.0:
            averages = {
                report_key: report_sums[report_key] / max(report_count, 1)
                for report_key in report_sums
            }
            print(
                f"Eye OSC bridge sent {sent} gaze packets; raw mean "
                f"LX={averages['left_x']:+.4f} LY={averages['left_y']:+.4f} "
                f"RX={averages['right_x']:+.4f} RY={averages['right_y']:+.4f}; "
                f"yaw offset={yaw_offset:+.2f} deg",
                flush=True,
            )
            report_count = 0
            for report_key in report_sums:
                report_sums[report_key] = 0.0
            last_report = now


if __name__ == "__main__":
    raise SystemExit(main())
