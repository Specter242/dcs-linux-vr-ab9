#!/usr/bin/env python3
"""Select MOZA AB9 presets from the current DCS aircraft.

The DCS export script sends an aircraft identifier to UDP localhost:34399.
Automatic hardware writes require both ``--apply`` and an explicit arm file.
The supplied systemd user service requests ``--apply``, but no write can occur
until the user creates the separate arm file with the exact warning phrase.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import ab9_profile


HOST = "127.0.0.1"
PORT = 34399
ARM_FILE = Path.home() / ".config/ab9-ffb/auto-switch-enabled"
ARM_PHRASE = "I UNDERSTAND THE STICK MAY MOVE"


# DCS has used more than one identifier for some variants.  Values are MOZA
# Cockpit preset vehicle identifiers, not DCS input-binding folder names.
AIRCRAFT_ALIASES = {
    "A-10C II": "A-10C_2",
    "A-10C_II": "A-10C_2",
    "AH-64D_BLK_II_PLT": "AH-64D_BLK_II",
    "CH-47F": "CH-47Fbl1",
    "F-14A-135-GR": "F-14B",
    "F-5E-3_FC": "F-5E-3",
    "Ka-50": "Ka-50_3",
    "P-51D": "P-51D-30-NA",
    "P-51D-25-NA": "P-51D-30-NA",
    "TF-51D": "P-51D-30-NA",
}


def normalized(value: str) -> str:
    return value.strip().casefold()


def preset_date(profile: ab9_profile.Profile) -> int:
    try:
        return int(profile.data.get("date", 0))
    except (TypeError, ValueError):
        return 0


def select_profile(
    profiles: list[ab9_profile.Profile], aircraft: str
) -> ab9_profile.Profile | None:
    target = AIRCRAFT_ALIASES.get(aircraft, aircraft)
    candidates = [p for p in profiles if normalized(p.vehicle) == normalized(target)]
    if not candidates:
        return None
    # A user's own profile takes precedence over the bundled one.  If Cockpit
    # contains multiple custom versions for one aircraft, use its newest date.
    return max(
        candidates,
        key=lambda profile: (profile.source == "custom", preset_date(profile)),
    )


def armed() -> bool:
    try:
        return ARM_FILE.read_text(encoding="utf-8").strip() == ARM_PHRASE
    except OSError:
        return False


def handle_aircraft(
    aircraft: str,
    profiles: list[ab9_profile.Profile],
    do_apply: bool,
    mode: str,
    last_profile_id: str | None,
) -> str | None:
    profile = select_profile(profiles, aircraft)
    if profile is None:
        print(f"DCS aircraft {aircraft!r}: no copied MOZA AB9 preset", flush=True)
        return None
    if profile.profile_id == last_profile_id:
        return last_profile_id
    print(
        f"DCS aircraft {aircraft!r}: selected {profile.profile_id} "
        f"({profile.name}, {mode})",
        flush=True,
    )
    if not do_apply:
        print("Dry selection only; automatic AB9 writes are disabled.", flush=True)
        return profile.profile_id
    if not armed():
        print(
            f"Refusing automatic AB9 write: {ARM_FILE} is absent or not armed.",
            flush=True,
        )
        return profile.profile_id
    ab9_profile.apply_profile(profile, mode, assume_ready=True)
    return profile.profile_id


def listen(do_apply: bool, mode: str) -> None:
    profiles = ab9_profile.load_profiles()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, PORT))
    print(
        f"AB9 profile selector listening on udp://{HOST}:{PORT}; "
        f"hardware writes={'requested' if do_apply else 'disabled'}; armed={armed()}",
        flush=True,
    )
    last_aircraft: str | None = None
    last_profile_id: str | None = None
    while True:
        packet, _ = sock.recvfrom(512)
        text = packet.decode("utf-8", errors="replace").strip()
        if not text.startswith("AB9_AIRCRAFT\t"):
            continue
        aircraft = text.split("\t", 1)[1].strip()
        if not aircraft or aircraft == last_aircraft:
            continue
        last_aircraft = aircraft
        try:
            last_profile_id = handle_aircraft(
                aircraft, profiles, do_apply, mode, last_profile_id
            )
        except Exception as exc:
            print(f"ERROR applying profile for {aircraft!r}: {exc}", file=sys.stderr, flush=True)
            # Permit a retry when DCS reports another aircraft and later returns.
            last_profile_id = None
        time.sleep(0.05)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", metavar="AIRCRAFT", help="resolve one aircraft and exit")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="request base writes (also requires the explicit arm file)",
    )
    parser.add_argument("--mode", choices=("exact-base", "native"), default="native")
    args = parser.parse_args()

    profiles = ab9_profile.load_profiles()
    if args.once:
        try:
            handle_aircraft(args.once, profiles, args.apply, args.mode, None)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        return 0
    try:
        listen(args.apply, args.mode)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
