#!/usr/bin/env python3
"""Inspect and safely apply MOZA Cockpit AB9 aircraft presets on Linux.

The source ``.preset`` files are copied verbatim from the user's own MOZA
Cockpit installation. This tool applies the persistent AB9 base-tuning portion
of a preset. The companion ``ab9_telemetryd.py`` implements a bounded subset of
the host-side telemetry effects. ``--mode native`` keeps the base in
DirectInput mode and ensures DCS native FFB is available; this is the Linux
default. ``--mode exact-base`` preserves MOZA's Composite-mode request and is
retained only for protocol diagnostics because it requires a compatible
host-side telemetry engine.

Applying a profile can create spring, damping, inertia, and friction forces.
The tool first closes the firmware's force-output gate, writes and verifies the
profile values, and only then reopens the gate after an interactive READY
confirmation (unless --yes is explicitly supplied by an already-authorized
controller).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import ab9_control
import moza


PROFILE_ROOT = Path(
    os.environ.get(
        "AB9_PROFILE_ROOT",
        str(Path.home() / ".local/share/ab9-ffb/profiles"),
    )
)


@dataclass(frozen=True)
class Profile:
    source: str
    path: Path
    data: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.data.get("name", self.path.stem))

    @property
    def vehicle(self) -> str:
        value = self.data.get("vehicles", "")
        if isinstance(value, list):
            return ",".join(str(item) for item in value)
        return str(value)

    @property
    def uuid(self) -> str:
        return str(self.data.get("uuid", self.path.stem))

    @property
    def profile_id(self) -> str:
        return f"{self.source}:{self.uuid}"


Decode = Callable[[bytes], int]
Encode = Callable[[int], bytes]


def decode_uint(data: bytes) -> int:
    return int.from_bytes(data, "big", signed=False)


def decode_low_byte(data: bytes) -> int:
    if not data:
        raise ValueError("empty response")
    return data[-1]


def encode_u8(value: int) -> bytes:
    return value.to_bytes(1, "big", signed=False)


def encode_u16(value: int) -> bytes:
    return value.to_bytes(2, "big", signed=False)


def encode_game_ffb(value: int) -> bytes:
    return bytes((0, value))


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    command_ids: tuple[int, ...]
    read_request_size: int
    response_size: int
    minimum: int
    maximum: int
    encode: Encode
    decode: Decode = decode_uint


SETTINGS = (
    Setting("bacic_force_model", "Force-feedback mode", (133,), 2, 2, 0, 2, encode_u16),
    Setting(
        "bacic_gameForceFeedback",
        "Game FFB gain",
        (153,),
        2,
        2,
        0,
        100,
        encode_game_ffb,
        decode_low_byte,
    ),
    Setting("axis_range_x_del_center", "X center deadzone", (151, 1), 1, 1, 0, 25, encode_u8),
    Setting("axis_range_x_del_border", "X border", (151, 2), 1, 1, 75, 100, encode_u8),
    Setting("axis_range_y_del_center", "Y center deadzone", (152, 1), 1, 1, 0, 25, encode_u8),
    Setting("axis_range_y_del_border", "Y border", (152, 2), 1, 1, 75, 100, encode_u8),
    Setting("axis_range_x_reversal", "Reverse X", (158,), 2, 2, 0, 1, encode_u16),
    Setting("axis_range_y_reversal", "Reverse Y", (162,), 2, 2, 0, 1, encode_u16),
    Setting("bacic_max_torque", "Maximum torque", (169,), 2, 2, 0, 100, encode_u16),
    Setting("axis_range_x_move", "X positive travel", (171, 1), 1, 1, 1, 100, encode_u8),
    Setting("axis_range_x_negative_move", "X negative travel", (171, 2), 1, 1, 1, 100, encode_u8),
    Setting("axis_range_y_move", "Y positive travel", (172, 1), 1, 1, 1, 100, encode_u8),
    Setting("axis_range_y_negative_move", "Y negative travel", (172, 2), 1, 1, 1, 100, encode_u8),
    Setting("bacic_overall_strength", "Overall strength", (174,), 2, 2, 0, 100, encode_u16),
    Setting("bacic_spring", "Mechanical spring", (175,), 2, 2, 0, 100, encode_u16),
    Setting("bacic_damper", "Mechanical damping", (176,), 2, 2, 0, 100, encode_u16),
    Setting("bacic_inertia", "Mechanical inertia", (177,), 2, 2, 0, 100, encode_u16),
    Setting("bacic_friction", "Mechanical friction", (178,), 2, 2, 0, 100, encode_u16),
    Setting("hardware_balancing_enable", "Hardware trim", (195,), 2, 2, 0, 1, encode_u16),
    Setting("hardware_balancing_speed", "Hardware trim speed", (196, 0), 1, 1, 0, 20, encode_u8),
    Setting("hardware_balancing_noseDown", "Trim button nose down", (197, 0), 1, 1, 0, 203, encode_u8),
    Setting("hardware_balancing_rwd", "Trim button right", (197, 1), 1, 1, 0, 203, encode_u8),
    Setting("hardware_balancing_noseUp", "Trim button nose up", (197, 2), 1, 1, 0, 203, encode_u8),
    Setting("hardware_balancing_lwd", "Trim button left", (197, 3), 1, 1, 0, 203, encode_u8),
    Setting("hardware_balancing_trim", "Trim button", (197, 4), 1, 1, 0, 203, encode_u8),
    Setting("hardware_balancing_recenter", "Recenter button", (197, 5), 1, 1, 0, 203, encode_u8),
    Setting("balancing_limit_noseUp", "Trim limit up", (198, 0), 1, 1, 0, 100, encode_u8),
    Setting("balancing_limit_rwd", "Trim limit right", (198, 1), 1, 1, 0, 100, encode_u8),
    Setting("balancing_limit_noseDown", "Trim limit down", (198, 2), 1, 1, 0, 100, encode_u8),
    Setting("balancing_limit_lwd", "Trim limit left", (198, 3), 1, 1, 0, 100, encode_u8),
    Setting("bacic_hardware_blance_auto", "Follow game force trim", (201,), 2, 2, 0, 1, encode_u16),
    Setting("base_total_advanced_set_X", "Overall strength X", (205, 0), 1, 1, 0, 100, encode_u8),
    Setting("base_total_advanced_set_Y", "Overall strength Y", (205, 2), 1, 1, 0, 100, encode_u8),
    Setting("base_total_limit_advanced_set_X", "Torque limit X", (206, 0), 1, 1, 0, 100, encode_u8),
    Setting("base_total_limit_advanced_set_Y", "Torque limit Y", (206, 2), 1, 1, 0, 100, encode_u8),
    Setting("Steer_StartForceHandsOffEnable", "Breakout hands-off detection", (207,), 2, 2, 0, 1, encode_u16),
    Setting("bacic_start_force_enble", "Adaptive centering", (208,), 2, 2, 0, 1, encode_u16),
    Setting("bacic_start_force_coefficient", "Breakout force", (209, 0), 0, 1, 0, 100, encode_u8),
    Setting("bacic_start_force_transition_zone", "Breakout transition", (210, 0), 0, 1, 0, 100, encode_u8),
    Setting("base_advanced_enable", "Professional settings", (217,), 2, 2, 0, 1, encode_u16),
    Setting("base_spring_x_pos_advanced", "Spring X+", (218, 0), 1, 1, 0, 100, encode_u8),
    Setting("base_spring_x_neg_advanced", "Spring X-", (218, 1), 1, 1, 0, 100, encode_u8),
    Setting("base_spring_y_pos_advanced", "Spring Y+", (218, 2), 1, 1, 0, 100, encode_u8),
    Setting("base_spring_y_neg_advanced", "Spring Y-", (218, 3), 1, 1, 0, 100, encode_u8),
    Setting("base_damp_x_pos_advanced", "Damping X+", (219, 0), 1, 1, 0, 100, encode_u8),
    Setting("base_damp_x_neg_advanced", "Damping X-", (219, 1), 1, 1, 0, 100, encode_u8),
    Setting("base_damp_y_pos_advanced", "Damping Y+", (219, 2), 1, 1, 0, 100, encode_u8),
    Setting("base_damp_y_neg_advanced", "Damping Y-", (219, 3), 1, 1, 0, 100, encode_u8),
    Setting("base_inertia_x_pos_advanced", "Inertia X+", (220, 0), 1, 1, 0, 100, encode_u8),
    Setting("base_inertia_x_neg_advanced", "Inertia X-", (220, 1), 1, 1, 0, 100, encode_u8),
    Setting("base_inertia_y_pos_advanced", "Inertia Y+", (220, 2), 1, 1, 0, 100, encode_u8),
    Setting("base_inertia_y_neg_advanced", "Inertia Y-", (220, 3), 1, 1, 0, 100, encode_u8),
    Setting("base_fric_x_pos_advanced", "Friction X+", (221, 0), 1, 1, 0, 100, encode_u8),
    Setting("base_fric_x_neg_advanced", "Friction X-", (221, 1), 1, 1, 0, 100, encode_u8),
    Setting("base_fric_y_pos_advanced", "Friction Y+", (221, 2), 1, 1, 0, 100, encode_u8),
    Setting("base_fric_y_neg_advanced", "Friction Y-", (221, 3), 1, 1, 0, 100, encode_u8),
)


SETTING_BY_KEY = {setting.key: setting for setting in SETTINGS}


def load_profiles() -> list[Profile]:
    profiles: list[Profile] = []
    for source in ("official", "custom"):
        directory = PROFILE_ROOT / source
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.preset")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"WARNING: cannot load {path}: {exc}", file=sys.stderr)
                continue
            if "AB9" not in data.get("devices", []):
                continue
            if "dcs_world" not in data.get("games", []):
                continue
            profiles.append(Profile(source, path, data))
    return profiles


def matches(profile: Profile, selector: str) -> bool:
    wanted = selector.casefold().strip()
    candidates = {
        profile.profile_id.casefold(),
        profile.uuid.casefold(),
        profile.uuid.strip("{}").casefold(),
        profile.vehicle.casefold(),
        profile.name.casefold(),
    }
    return wanted in candidates


def resolve_profile(profiles: list[Profile], selector: str, source: str | None) -> Profile:
    found = [profile for profile in profiles if matches(profile, selector)]
    if source:
        found = [profile for profile in found if profile.source == source]
    if not found:
        raise RuntimeError(f"no AB9 DCS profile matches {selector!r}")
    if len(found) > 1:
        choices = "\n".join(
            f"  {profile.profile_id}  {profile.vehicle}  {profile.name}" for profile in found
        )
        raise RuntimeError(f"profile selector is ambiguous; use one of:\n{choices}")
    return found[0]


def profile_values(profile: Profile, mode: str) -> dict[str, int]:
    params = profile.data.get("device_params", {})
    result: dict[str, int] = {}
    for key, setting in SETTING_BY_KEY.items():
        value = params.get(key)
        if isinstance(value, bool):
            value = int(value)
        if not isinstance(value, int):
            continue
        if not setting.minimum <= value <= setting.maximum:
            raise RuntimeError(
                f"{profile.name}: {key}={value} is outside documented range "
                f"{setting.minimum}..{setting.maximum}"
            )
        result[key] = value
    if mode == "native":
        result["bacic_force_model"] = 1
        result["bacic_gameForceFeedback"] = 100
    return result


def response_data(frame: bytes, command_ids: tuple[int, ...]) -> bytes | None:
    body = frame[4:-1]
    prefix = bytes(command_ids)
    if not body.startswith(prefix):
        return None
    return body[len(prefix) :]


def read_setting(fd: int, setting: Setting) -> int:
    frames = ab9_control.transact(
        fd,
        30,
        setting.command_ids,
        bytes(setting.read_request_size),
    )
    for frame in frames:
        data = response_data(frame, setting.command_ids)
        if data is None or len(data) != setting.response_size:
            continue
        return setting.decode(data)
    raise RuntimeError(f"{setting.label}: no readable value")


def write_setting(fd: int, setting: Setting, value: int) -> None:
    frames = ab9_control.transact(fd, 31, setting.command_ids, setting.encode(value))
    if not frames:
        raise RuntimeError(f"{setting.label}: no acknowledgement")


def core_summary(params: dict[str, Any]) -> str:
    fields = (
        ("mode", "bacic_force_model"),
        ("torque", "bacic_max_torque"),
        ("overall", "bacic_overall_strength"),
        ("game", "bacic_gameForceFeedback"),
        ("spring", "bacic_spring"),
        ("damping", "bacic_damper"),
        ("inertia", "bacic_inertia"),
        ("friction", "bacic_friction"),
    )
    return " ".join(f"{label}={params.get(key, '-')}" for label, key in fields)


def list_profiles(profiles: list[Profile], vehicle_filter: str | None) -> None:
    filtered = profiles
    if vehicle_filter:
        wanted = vehicle_filter.casefold()
        filtered = [p for p in profiles if wanted in p.vehicle.casefold() or wanted in p.name.casefold()]
    for profile in sorted(filtered, key=lambda p: (p.vehicle.casefold(), p.source, p.name.casefold())):
        params = profile.data.get("device_params", {})
        print(f"{profile.profile_id}")
        print(f"  {profile.vehicle}: {profile.name} [{profile.source}]")
        print(f"  {core_summary(params)}")
    print(f"{len(filtered)} profile(s)")


def show_profile(profile: Profile, mode: str) -> None:
    params = profile.data.get("device_params", {})
    effective = profile_values(profile, mode)
    print(f"ID: {profile.profile_id}")
    print(f"Aircraft: {profile.vehicle}")
    print(f"Name: {profile.name}")
    print(f"Source: {profile.source}")
    print(f"File: {profile.path}")
    print(f"Mode: {mode}")
    print(f"Original core: {core_summary(params)}")
    print(f"Effective core: {core_summary(effective)}")
    mapped = sum(1 for key in params if key in SETTING_BY_KEY)
    print(f"Mapped persistent base settings: {mapped}/{len(params)}")
    print(f"Telemetry-effect parameters retained in file: {len(profile.data.get('telemetry_params', {}))}")
    if mode == "native" and params.get("bacic_gameForceFeedback") != 100:
        print(
            "Native adaptation: game FFB gain is raised to 100 because the Linux "
            "telemetry-effect engine is not active."
        )


def read_app_state(fd: int) -> int | None:
    query = next(q for q in ab9_control.QUERIES if q.label == "App state")
    return ab9_control.read_value(fd, query)


def print_device_status(fd: int) -> None:
    for key in (
        "bacic_force_model",
        "bacic_max_torque",
        "bacic_overall_strength",
        "bacic_gameForceFeedback",
        "bacic_spring",
        "bacic_damper",
        "bacic_inertia",
        "bacic_friction",
        "base_advanced_enable",
    ):
        setting = SETTING_BY_KEY[key]
        try:
            value = read_setting(fd, setting)
        except RuntimeError as exc:
            print(f"{setting.label}: unavailable ({exc})")
        else:
            print(f"{setting.label}: {value}")


def confirm_ready(profile: Profile) -> None:
    phrase = f"READY {profile.vehicle}"
    print()
    print("Applying this profile can center or load the stick when force is restored.")
    print("Clear the mechanism, rest a hand lightly on the grip, and do not fight it.")
    entered = input(f"Type {phrase!r} immediately before force restoration: ").strip()
    if entered != phrase:
        raise RuntimeError("readiness confirmation did not match; force remains muted")


def apply_profile(profile: Profile, mode: str, assume_ready: bool) -> None:
    values = profile_values(profile, mode)
    moza.PORT = ab9_control.find_port()
    fd = moza.open_port()
    output_muted = False
    try:
        if read_app_state(fd) != 1:
            raise RuntimeError("AB9 APP is not free; refusing profile writes")

        torque_setting = SETTING_BY_KEY["bacic_max_torque"]
        current_torque = read_setting(fd, torque_setting)
        requested_torque = values.get("bacic_max_torque", current_torque)
        values["bacic_max_torque"] = min(requested_torque, current_torque)
        if requested_torque > current_torque:
            print(
                f"Torque safety cap: preset requests {requested_torque}, retaining current {current_torque}."
            )

        current: dict[str, int] = {}
        readable: dict[str, Setting] = {}
        for key in values:
            setting = SETTING_BY_KEY[key]
            try:
                current[key] = read_setting(fd, setting)
            except RuntimeError as exc:
                print(f"Skipping unreadable {setting.label}: {exc}")
                continue
            readable[key] = setting

        changes = [key for key in values if key in current and current[key] != values[key]]
        print(f"Profile: {profile.vehicle} / {profile.name} ({mode})")
        print(f"Verified settings available: {len(readable)}; changes needed: {len(changes)}")
        for key in changes:
            print(f"  {readable[key].label}: {current[key]} -> {values[key]}")
        if not changes:
            print("AB9 base settings already match this profile.")
            return

        if not assume_ready:
            confirm_ready(profile)

        # Close the documented force-output gate before altering force layers.
        ab9_control.write_value(fd, "Force-output disable gate", (0xDE,), 1, 2)
        output_muted = True
        time.sleep(0.2)

        for key in changes:
            setting = readable[key]
            write_setting(fd, setting, values[key])
            time.sleep(0.04)

        failed: list[str] = []
        for key in changes:
            actual = read_setting(fd, readable[key])
            if actual != values[key]:
                failed.append(f"{readable[key].label} expected {values[key]}, read {actual}")
        if failed:
            raise RuntimeError("verification failed; force remains muted: " + "; ".join(failed))

        # Keep the preset's requested FFB mode. Exact MOZA DCS presets use
        # Composite (2); native mode explicitly requests DirectInput (1).
        ab9_control.write_value(fd, "FFB controller", (0xE1, 0x17), 1, 1)
        ab9_control.write_value(fd, "Force-output disable gate", (0xDE,), 0, 2)
        output_muted = False
        print(f"Applied and verified {len(changes)} AB9 settings; force output restored.")
    finally:
        os.close(fd)
        if output_muted:
            print(
                "NOTICE: profile application did not restore force output. "
                "The DCS launcher readiness check can reopen it after the cause is fixed.",
                file=sys.stderr,
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="list copied AB9 DCS presets")
    list_parser.add_argument("filter", nargs="?", help="aircraft/name substring")

    show_parser = subparsers.add_parser("show", help="show one preset without touching hardware")
    show_parser.add_argument("profile")
    show_parser.add_argument("--source", choices=("official", "custom"))
    show_parser.add_argument("--mode", choices=("exact-base", "native"), default="native")

    subparsers.add_parser("status", help="read current AB9 base tuning without actuation")

    apply_parser = subparsers.add_parser("apply", help="apply one preset to the AB9 base")
    apply_parser.add_argument("profile")
    apply_parser.add_argument("--source", choices=("official", "custom"))
    apply_parser.add_argument("--mode", choices=("exact-base", "native"), default="native")
    apply_parser.add_argument(
        "--yes",
        action="store_true",
        help="skip interactive readiness prompt (only for an already-authorized controller)",
    )

    args = parser.parse_args()
    profiles = load_profiles()
    try:
        if args.command == "list":
            list_profiles(profiles, args.filter)
        elif args.command == "status":
            moza.PORT = ab9_control.find_port()
            fd = moza.open_port()
            try:
                print_device_status(fd)
            finally:
                os.close(fd)
        else:
            profile = resolve_profile(profiles, args.profile, args.source)
            if args.command == "show":
                show_profile(profile, args.mode)
            else:
                apply_profile(profile, args.mode, args.yes)
    except (EOFError, OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
