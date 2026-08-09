#!/usr/bin/env python3
"""Read and apply the minimal MOZA AB9 native-FFB configuration.

This uses command definitions extracted from MOZA Cockpit 1.1.4.21's
DeviceCommon.dll.  Status mode is read-only.  --enable-directinput writes only:

* Main_CtrlMode = 1 (DirectInput FFB)
* Main_LimitToqueEnable = 0 (clear "FFB output disabled")
* Main_CtrlFfbEnable = 1 (enable the FFB controller)

It intentionally does not enumerate command IDs and never uses group 32.

--ensure-directinput is intended for launchers.  It reads only the APP state and
the three native-FFB settings, then writes only settings which are incorrect.

--ensure-ffb-ready is profile-aware: it accepts DirectInput (1) or Composite
(2), correcting only Telemetry-only mode (0), while still clearing the output
gate and enabling the FFB controller.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import time
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import moza


VENDOR = "346e"
PRODUCT = "1000"
MAIN_DEVICE = 0x12


@dataclass(frozen=True)
class Query:
    label: str
    command_ids: tuple[int, ...]
    request_size: int
    response_size: int
    values: dict[int, str] | None = None


QUERIES = (
    Query("App state", (0x86,), 2, 2, {0: "locked", 1: "free", 2: "error"}),
    Query(
        "Control mode",
        (0x85,),
        2,
        2,
        {0: "Telemetry", 1: "DirectInput", 2: "Composite"},
    ),
    Query("Working mode", (0x50,), 1, 1, {0: "normal", 1: "standby", 2: "debug"}),
    Query("USB mode", (0x51,), 1, 1, {0: "default", 1: "Xbox"}),
    Query("HID descriptor mode", (0x52,), 1, 1, {0: "default", 1: "n*32 buttons"}),
    Query(
        "Stick-connect protection",
        (0xCB,),
        2,
        2,
        {0: "protection enabled", 1: "protection disabled"},
    ),
    Query(
        "Force-output disable gate",
        (0xDE,),
        2,
        2,
        {0: "clear (force allowed)", 1: "SET (force forced to zero)"},
    ),
    Query("FFB controller", (0xE1, 0x17), 0, 1, {0: "disabled", 1: "enabled"}),
)


def find_port() -> str:
    for tty in sorted(glob.glob("/sys/class/tty/ttyACM*")):
        interface = os.path.realpath(os.path.join(tty, "device"))
        usb_device = os.path.realpath(os.path.join(interface, ".."))
        try:
            with open(os.path.join(usb_device, "idVendor"), encoding="ascii") as stream:
                vendor = stream.read().strip().lower()
            with open(os.path.join(usb_device, "idProduct"), encoding="ascii") as stream:
                product = stream.read().strip().lower()
        except OSError:
            continue
        if (vendor, product) == (VENDOR, PRODUCT):
            return "/dev/" + os.path.basename(tty)
    raise RuntimeError("MOZA AB9 serial interface was not found")


def parse_frames(raw: bytes) -> list[bytes]:
    frames: list[bytes] = []
    offset = 0
    while offset < len(raw):
        start = raw.find(bytes((moza.START,)), offset)
        if start < 0 or start + 5 > len(raw):
            break
        payload_length = raw[start + 1]
        end = start + 5 + payload_length
        if end > len(raw):
            break
        candidate = raw[start:end]
        expected_checksum = (moza.MAGIC + sum(candidate[:-1])) & 0xFF
        if candidate[-1] == expected_checksum:
            frames.append(candidate)
            offset = end
        else:
            offset = start + 1
    return frames


def transact(fd: int, group: int, command_ids: tuple[int, ...], payload: bytes) -> list[bytes]:
    request = moza.frame(group, MAIN_DEVICE, list(command_ids), payload)
    raw = moza.xact(fd, request, 0.45)
    expected_group = (group + 0x80) & 0xFF
    expected_device = ((MAIN_DEVICE & 0x0F) << 4) | ((MAIN_DEVICE & 0xF0) >> 4)
    return [
        frame
        for frame in parse_frames(raw)
        if frame[2] == expected_group and frame[3] == expected_device
    ]


def read_value(fd: int, query: Query) -> int | None:
    frames = transact(fd, 30, query.command_ids, bytes(query.request_size))
    for frame in frames:
        body = frame[4:-1]
        prefix = bytes(query.command_ids)
        if not body.startswith(prefix):
            continue
        data = body[len(prefix) :]
        if len(data) != query.response_size:
            continue
        return int.from_bytes(data, "big", signed=False)
    return None


def print_status(fd: int) -> dict[str, int | None]:
    result: dict[str, int | None] = {}
    for query in QUERIES:
        value = read_value(fd, query)
        result[query.label] = value
        if value is None:
            rendered = "no value (device may be in BOOT mode)"
        else:
            meaning = query.values.get(value) if query.values else None
            rendered = f"{value}" + (f" ({meaning})" if meaning else "")
        print(f"{query.label}: {rendered}")
    return result


def write_value(fd: int, label: str, command_ids: tuple[int, ...], value: int, size: int) -> None:
    payload = value.to_bytes(size, "big", signed=False)
    frames = transact(fd, 31, command_ids, payload)
    if not frames:
        raise RuntimeError(f"{label}: no acknowledgement")
    print(f"{label}: wrote {value}, acknowledged")


def enable_directinput(fd: int) -> None:
    write_value(fd, "Control mode", (0x85,), 1, 2)
    time.sleep(0.2)
    write_value(fd, "Force-output disable gate", (0xDE,), 0, 2)
    time.sleep(0.2)
    write_value(fd, "FFB controller", (0xE1, 0x17), 1, 1)


def ensure_directinput(fd: int) -> None:
    by_label = {query.label: query for query in QUERIES}
    expected = (
        ("Control mode", (0x85,), 1, 2),
        ("Force-output disable gate", (0xDE,), 0, 2),
        ("FFB controller", (0xE1, 0x17), 1, 1),
    )

    app_state = read_value(fd, by_label["App state"])
    print(f"App state: {app_state}")
    if app_state != 1:
        raise RuntimeError(
            "AB9 APP is not free; run the documented Base Restore and Reset recovery"
        )

    current: dict[str, int | None] = {}
    for label, _, wanted, _ in expected:
        current[label] = read_value(fd, by_label[label])
        print(f"{label}: {current[label]} (expected {wanted})")
        if current[label] is None:
            raise RuntimeError(f"could not read {label}")

    changed = False
    for label, command_ids, wanted, size in expected:
        if current[label] == wanted:
            continue
        write_value(fd, label, command_ids, wanted, size)
        changed = True
        time.sleep(0.2)

    for label, _, wanted, _ in expected:
        actual = read_value(fd, by_label[label])
        if actual != wanted:
            raise RuntimeError(
                f"{label}: verification failed (expected {wanted}, read {actual})"
            )
    print("AB9 native FFB ready" + (" (settings corrected)" if changed else ""))


def ensure_ffb_ready(fd: int) -> None:
    """Ensure game FFB works without overwriting a Composite aircraft preset."""
    by_label = {query.label: query for query in QUERIES}
    app_state = read_value(fd, by_label["App state"])
    print(f"App state: {app_state}")
    if app_state != 1:
        raise RuntimeError(
            "AB9 APP is not free; run the documented Base Restore and Reset recovery"
        )

    control_mode = read_value(fd, by_label["Control mode"])
    output_gate = read_value(fd, by_label["Force-output disable gate"])
    controller = read_value(fd, by_label["FFB controller"])
    print(f"Control mode: {control_mode} (accepted: 1 DirectInput or 2 Composite)")
    print(f"Force-output disable gate: {output_gate} (expected 0)")
    print(f"FFB controller: {controller} (expected 1)")
    if None in (control_mode, output_gate, controller):
        raise RuntimeError("could not read all AB9 FFB readiness settings")

    changed = False
    if control_mode not in (1, 2):
        write_value(fd, "Control mode", (0x85,), 1, 2)
        changed = True
        time.sleep(0.2)
    if output_gate != 0:
        write_value(fd, "Force-output disable gate", (0xDE,), 0, 2)
        changed = True
        time.sleep(0.2)
    if controller != 1:
        write_value(fd, "FFB controller", (0xE1, 0x17), 1, 1)
        changed = True
        time.sleep(0.2)

    final_mode = read_value(fd, by_label["Control mode"])
    final_gate = read_value(fd, by_label["Force-output disable gate"])
    final_controller = read_value(fd, by_label["FFB controller"])
    if final_mode not in (1, 2) or final_gate != 0 or final_controller != 1:
        raise RuntimeError(
            "profile-aware FFB verification failed "
            f"(mode={final_mode}, gate={final_gate}, controller={final_controller})"
        )
    print("AB9 game FFB ready" + (" (settings corrected)" if changed else ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument(
        "--enable-directinput",
        action="store_true",
        help="write the three documented values required for native DirectInput FFB",
    )
    action_group.add_argument(
        "--ensure-directinput",
        action="store_true",
        help="verify native FFB and write only incorrect values",
    )
    action_group.add_argument(
        "--ensure-ffb-ready",
        action="store_true",
        help="verify game FFB while preserving DirectInput or Composite profile mode",
    )
    args = parser.parse_args()

    try:
        moza.PORT = find_port()
        print(f"AB9 serial: {moza.PORT}")
        fd = moza.open_port()
        try:
            if args.ensure_directinput:
                ensure_directinput(fd)
                return 0
            if args.ensure_ffb_ready:
                ensure_ffb_ready(fd)
                return 0
            before = print_status(fd)
            if not args.enable_directinput:
                return 0
            if before["App state"] is None:
                raise RuntimeError("refusing writes because the AB9 is not responding in APP mode")
            print("Applying documented native-FFB values...")
            enable_directinput(fd)
            print("Verification:")
            after = print_status(fd)
            expected = {
                "Control mode": 1,
                "Force-output disable gate": 0,
                "FFB controller": 1,
            }
            failed = [name for name, value in expected.items() if after[name] != value]
            if failed:
                raise RuntimeError("verification failed for: " + ", ".join(failed))
        finally:
            os.close(fd)
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
