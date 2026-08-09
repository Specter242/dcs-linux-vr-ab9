#!/usr/bin/env python3
"""Synthesize bounded AB9 supplementary effects from DCS telemetry.

The DCS exporter sends MOZA-compatible field names to UDP localhost:34400.
Copied MOZA aircraft presets supply effect switches, strengths, and frequencies.
Core aerodynamic/centering forces remain DCS's responsibility through native
DirectInput; this daemon adds vibration and motion cues alongside them.

Hardware output requires both ``--apply`` and a separate explicit arm file.
Without both, the daemon is a motor-safe dry-run telemetry monitor.
"""

from __future__ import annotations

import argparse
import json
import math
import signal
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evdev import InputDevice, ecodes, ff, list_devices

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import ab9_profile
import ab9_profiled


HOST = "127.0.0.1"
PORT = 34400
ARM_FILE = Path.home() / ".config/ab9-ffb/telemetry-effects-enabled"
ARM_PHRASE = "I UNDERSTAND TELEMETRY EFFECTS MAY MOVE THE STICK"
OVERRIDES_FILE = Path.home() / ".config/ab9-ffb/telemetry-overrides.json"
TELEMETRY_TIMEOUT = 0.5
HELICOPTER_VEHICLES = {
    "AH-64D_BLK_II",
    "CH-47Fbl1",
    "Ka-50_3",
    "Mi-24P",
    "Mi-8MT",
    "OH58D",
    "UH-1H",
}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def as_float(data: dict[str, str], key: str, default: float = 0.0) -> float:
    try:
        value = float(data.get(key, default))
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def enabled(params: dict[str, Any], key: str) -> bool:
    return params.get(key) is True or params.get(key) == 1


def number(params: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        value = float(params.get(key, default))
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def armed() -> bool:
    try:
        return ARM_FILE.read_text(encoding="utf-8").strip() == ARM_PHRASE
    except OSError:
        return False


def load_overrides() -> dict[str, dict[str, Any]]:
    try:
        document = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read {OVERRIDES_FILE}: {exc}") from exc
    profiles = document.get("profiles", {})
    if not isinstance(profiles, dict):
        raise RuntimeError(f"{OVERRIDES_FILE}: 'profiles' must be an object")
    return {key: value for key, value in profiles.items() if isinstance(value, dict)}


def parse_packet(packet: bytes) -> tuple[str, dict[str, str]]:
    text = packet.decode("utf-8", errors="replace").strip()
    if text.startswith("AB9_STOP\t"):
        return "stop", {}
    if not text.startswith("AB9_TELEMETRY\t"):
        return "ignore", {}
    fields: dict[str, str] = {}
    for item in text.split("\t", 1)[1].split(";"):
        if not item or "," not in item:
            continue
        key, value = item.split(",", 1)
        if key:
            fields[key] = value
    return "telemetry", fields


@dataclass(frozen=True)
class PeriodicSpec:
    percent: float
    frequency_hz: float
    direction: int

    def bounded(self, cap_percent: float, gain: float = 1.0) -> "PeriodicSpec | None":
        percent = clamp(self.percent * gain, 0.0, cap_percent)
        if percent < 0.25:
            return None
        return PeriodicSpec(
            round(percent * 2.0) / 2.0,
            round(clamp(self.frequency_hz, 5.0, 50.0)),
            self.direction & 0xFFFF,
        )


@dataclass(frozen=True)
class ConditionSpec:
    effect_type: int
    x_percent: float
    y_percent: float
    deadband: int = 250

    def bounded(self, cap_percent: float) -> "ConditionSpec | None":
        x_percent = round(clamp(self.x_percent, 0.0, cap_percent) * 2.0) / 2.0
        y_percent = round(clamp(self.y_percent, 0.0, cap_percent) * 2.0) / 2.0
        if max(x_percent, y_percent) < 0.5:
            return None
        return ConditionSpec(
            self.effect_type,
            x_percent,
            y_percent,
            max(0, min(0xFFFF, self.deadband)),
        )


EffectSpec = PeriodicSpec | ConditionSpec


class EffectMixer:
    """Translate one aircraft's telemetry and copied preset into effect slots."""

    def __init__(
        self,
        profile: ab9_profile.Profile,
        cap_percent: float,
        gain: float = 1.0,
        overrides: dict[str, Any] | None = None,
    ):
        self.profile = profile
        self.params: dict[str, Any] = dict(profile.data.get("telemetry_params", {}))
        if overrides:
            self.params.update(overrides)
        self.device_params: dict[str, Any] = profile.data.get("device_params", {})
        self.cap_percent = cap_percent
        self.gain = gain
        self.previous: dict[str, float] = {}
        self.motion_until: dict[str, tuple[float, float, float]] = {}
        self.weapon_until: dict[str, tuple[float, float, float]] = {}

    def _rpm_fraction(self, telemetry: dict[str, str]) -> float:
        raw = max(
            as_float(telemetry, "engine_rpm_left"),
            as_float(telemetry, "engine_rpm_right"),
        )
        if raw <= 0:
            return 0.0
        # DCS normally exports engine RPM as percent.  Preserve compatibility
        # with exporters that provide actual RPM as well.
        if raw <= 150:
            return clamp(raw / 100.0, 0.0, 1.0)
        high = max(number(self.params, "engine_rumble_high_rpm", 3000), 1.0)
        return clamp(raw / high, 0.0, 1.0)

    def _engine(self, telemetry: dict[str, str]) -> PeriodicSpec | None:
        rpm = self._rpm_fraction(telemetry)
        if rpm < 0.01:
            return None
        intensity = 0.0
        frequency = 18.0
        if enabled(self.params, "propeller_vibration_switch"):
            low = number(self.params, "engine_rumble_low_intensity", 0)
            high = number(self.params, "engine_rumble_high_intensity", low)
            intensity += (low + (high - low) * rpm) * math.sqrt(rpm)
            frequency = 10.0 + 32.0 * rpm
        if enabled(self.params, "jet_engine_rumble_switch"):
            intensity += number(self.params, "jet_engine_rumble", 0) * rpm
            frequency = number(self.params, "jet_engine_rumble_freq", 18)
        afterburner = max(
            as_float(telemetry, "afterburner_1"),
            as_float(telemetry, "afterburner_2"),
        )
        if enabled(self.params, "afterburner_rumble_switch") and afterburner > 0.05:
            intensity += number(self.params, "afterburner_rumble", 0) * afterburner
            frequency = max(frequency, 28.0)
        return PeriodicSpec(intensity, frequency, 0x4000).bounded(
            self.cap_percent, self.gain
        )

    def _runway(self, telemetry: dict[str, str]) -> PeriodicSpec | None:
        if not enabled(self.params, "runway_rumble_switch"):
            return None
        agl = as_float(telemetry, "h_above_ground_level", 9999)
        gear = max(
            as_float(telemetry, "left_gear"),
            as_float(telemetry, "nose_gear"),
            as_float(telemetry, "right_gear"),
            as_float(telemetry, "gear_value"),
        )
        speed = max(as_float(telemetry, "ias"), as_float(telemetry, "tas"))
        if agl > 4.0 or gear < 0.75 or speed < 2.0:
            return None
        scale = clamp((speed - 2.0) / 28.0, 0.0, 1.0)
        intensity = number(self.params, "runway_rumble", 0) * scale
        frequency = 12.0 + clamp(speed * 0.7, 0.0, 28.0)
        return PeriodicSpec(intensity, frequency, 0x6000).bounded(
            self.cap_percent, self.gain
        )

    def _buffet(self, telemetry: dict[str, str]) -> PeriodicSpec | None:
        ias = as_float(telemetry, "ias")
        ias_kph = ias * 3.6
        aoa = as_float(telemetry, "aoa")
        mach = as_float(telemetry, "mach")
        gear = max(
            as_float(telemetry, "left_gear"),
            as_float(telemetry, "nose_gear"),
            as_float(telemetry, "right_gear"),
            as_float(telemetry, "gear_value"),
        )
        speedbrake = as_float(telemetry, "speedbrake_value")
        intensity = 0.0
        weighted_frequency = 0.0

        def add(component: float, frequency: float) -> None:
            nonlocal intensity, weighted_frequency
            if component <= 0:
                return
            weighted_frequency += component * frequency
            intensity += component

        if enabled(self.params, "gear_buffet_switch") and gear > 0.5 and ias > 15:
            scale = clamp((ias - 15.0) / 70.0, 0.0, 1.0)
            add(
                number(self.params, "gear_buffet", 0) * scale,
                number(self.params, "gear_buffet_frequency", 10),
            )
        if enabled(self.params, "speedbrake_buffet_switch") and speedbrake > 0.05:
            add(number(self.params, "speedbrake_buffet", 0) * speedbrake, 16.0)
        if enabled(self.params, "stall_buffeting_switch"):
            onset = number(
                self.params,
                "buffet_onset_aoa",
                number(self.params, "stall_aoa", 15),
            )
            if aoa > onset:
                scale = clamp((aoa - onset) / 6.0, 0.0, 1.0)
                add(
                    number(self.params, "stall_buffeting", 0) * scale,
                    number(
                        self.params,
                        "buffeting_frequency",
                        number(self.params, "buffeting_frequeney", 11),
                    ),
                )
        if enabled(self.params, "aoa_buffeting_switch"):
            onset = number(self.params, "design_buffeting_aoa", 12)
            if aoa > onset:
                scale = clamp((aoa - onset) / 6.0, 0.0, 1.0)
                add(number(self.params, "buffeting_intensity", 0) * 100 * scale, 11.0)
        if enabled(self.params, "transonic_buffet_enabled"):
            lower = number(self.params, "transonic_buffet_mach_lower_limit", 0.7)
            upper = number(self.params, "transonic_buffet_mach_upper_limit", 1.0)
            if lower <= mach <= upper and upper > lower:
                middle = (lower + upper) / 2.0
                half = (upper - lower) / 2.0
                scale = 1.0 - abs(mach - middle) / half
                add(
                    number(self.params, "transonic_buffet_intensity", 0) * scale,
                    number(self.params, "transonic_buffet_base_freq", 17),
                )
        if enabled(self.params, "overpeed_shake_switch"):
            start = number(self.params, "overpeed_shake_start_speed", 99999)
            if ias_kph > start:
                scale = clamp((ias_kph - start) / max(start * 0.25, 30), 0.0, 1.0)
                add(number(self.params, "overpeed_shake", 0) * scale, 15.0)
        if enabled(self.params, "wind_effect_switch") and ias > 8:
            wind = math.sqrt(
                as_float(telemetry, "wind_x") ** 2
                + as_float(telemetry, "wind_y") ** 2
                + as_float(telemetry, "wind_z") ** 2
            )
            divisor = max(number(self.params, "wind_effect_scaling", 20), 1)
            add(number(self.params, "wind_effect", 0) * clamp(wind / divisor, 0, 1), 9.0)

        if intensity <= 0:
            return None
        frequency = weighted_frequency / intensity
        return PeriodicSpec(intensity, frequency, 0x2000).bounded(
            self.cap_percent, self.gain
        )

    def _changed_motion(
        self,
        telemetry: dict[str, str],
        now: float,
        field: str,
        switch: str,
        strength: str,
        frequency: float,
    ) -> None:
        value = as_float(telemetry, field)
        old = self.previous.get(field)
        self.previous[field] = value
        if old is None or abs(value - old) < 0.002 or not enabled(self.params, switch):
            return
        self.motion_until[field] = (
            now + 0.22,
            number(self.params, strength, 0),
            frequency,
        )

    def _motion(self, telemetry: dict[str, str], now: float) -> PeriodicSpec | None:
        self._changed_motion(
            telemetry,
            now,
            "gear_value",
            "gear_motion_switch",
            "gear_motion",
            number(self.params, "gear_motion_frequency", 10),
        )
        self._changed_motion(
            telemetry, now, "flap_pos", "flaps_motion_switch", "flaps_motion", 14.0
        )
        self._changed_motion(
            telemetry,
            now,
            "speedbrake_value",
            "speedbrake_motion_switch",
            "speedbrake_motion",
            16.0,
        )
        self._changed_motion(
            telemetry, now, "canopy_pos", "canopy_motion_switch", "canopy_motion", 12.0
        )
        active = [value for value in self.motion_until.values() if value[0] > now]
        self.motion_until = {
            key: value for key, value in self.motion_until.items() if value[0] > now
        }
        if not active:
            return None
        _, intensity, frequency = max(active, key=lambda item: item[1])
        return PeriodicSpec(intensity, frequency, 0x6000).bounded(
            self.cap_percent, self.gain
        )

    def _counter_decreased(
        self,
        telemetry: dict[str, str],
        now: float,
        field: str,
        event: str,
        intensity: float,
        frequency: float,
        duration: float,
    ) -> None:
        if field not in telemetry:
            return
        value = as_float(telemetry, field)
        old = self.previous.get(field)
        self.previous[field] = value
        if old is not None and value < old:
            self.weapon_until[event] = (now + duration, intensity, frequency)

    def _weapon(self, telemetry: dict[str, str], now: float) -> PeriodicSpec | None:
        if not enabled(self.params, "weapon_switch"):
            return None
        self._counter_decreased(
            telemetry,
            now,
            "cannon_shells",
            "gun",
            number(self.params, "gun_vibration", 0),
            28.0,
            0.14,
        )
        self._counter_decreased(
            telemetry,
            now,
            "payload_count",
            "release",
            number(self.params, "weapon_release", 0),
            18.0,
            0.24,
        )
        countermeasure = number(self.params, "countermeasure_release", 0)
        self._counter_decreased(
            telemetry, now, "flare", "flare", countermeasure, 24.0, 0.16
        )
        self._counter_decreased(
            telemetry, now, "chaff", "chaff", countermeasure, 24.0, 0.16
        )
        active = [value for value in self.weapon_until.values() if value[0] > now]
        self.weapon_until = {
            key: value for key, value in self.weapon_until.items() if value[0] > now
        }
        if not active:
            return None
        _, intensity, frequency = max(active, key=lambda item: item[1])
        direction_code = int(number(self.params, "weapon_effects_direction", 0))
        direction = (0x0000, 0x4000, 0x2000)[direction_code % 3]
        return PeriodicSpec(intensity, frequency, direction).bounded(
            self.cap_percent, self.gain
        )

    def _core(self, telemetry: dict[str, str]) -> dict[str, ConditionSpec]:
        """Add MOZA-style dynamic pressure without replacing DCS's trim center."""
        if self.profile.vehicle in HELICOPTER_VEHICLES:
            return {}
        if not enabled(self.params, "DrivingControl_switch"):
            return {}
        ias_kph = as_float(telemetry, "ias") * 3.6
        start_speed = number(self.params, "speed_thresh", 70)
        max_speed = max(number(self.params, "max_intensity_speed", 400), start_speed + 1)
        speed_scale = clamp((ias_kph - start_speed) / (max_speed - start_speed), 0, 1)
        if speed_scale <= 0:
            return {}

        dynamic_scale = clamp(number(self.params, "dyn_pressure_scale", 100) / 100.0, 0, 2)
        # Roll is deliberately lighter than pitch, matching the copied
        # Mustang's K_td_x/K_td_y ratio (20/30).  Standard FF_SPRING performs
        # the actual axis-position feedback in firmware at full USB rate.
        spring = ConditionSpec(
            ecodes.FF_SPRING,
            20.0 * dynamic_scale * speed_scale,
            30.0 * dynamic_scale * speed_scale,
        ).bounded(self.cap_percent)

        copied_damper = float(self.device_params.get("bacic_damper", 0) or 0)
        damper_max = min(15.0, copied_damper * 0.5)
        damper = ConditionSpec(
            ecodes.FF_DAMPER,
            damper_max * speed_scale,
            damper_max * speed_scale,
            0,
        ).bounded(self.cap_percent)
        result: dict[str, ConditionSpec] = {}
        if spring is not None:
            result["dynamic-spring"] = spring
        if damper is not None:
            result["dynamic-damper"] = damper
        return result

    def _helicopter(self, telemetry: dict[str, str]) -> dict[str, PeriodicSpec]:
        if self.profile.vehicle not in HELICOPTER_VEHICLES:
            return {}
        effects: dict[str, PeriodicSpec] = {}
        speed = max(as_float(telemetry, "ias"), as_float(telemetry, "tas"))
        aoa = abs(as_float(telemetry, "aoa"))

        engine_fraction = self._rpm_fraction(telemetry)
        rotor_rpm = as_float(telemetry, "helicopter_rotor_rpm")
        if rotor_rpm > 150:
            rotor_fraction = clamp(rotor_rpm / 400.0, 0, 1)
        elif rotor_rpm > 0:
            rotor_fraction = clamp(rotor_rpm / 100.0, 0, 1)
        else:
            rotor_fraction = engine_fraction
        if enabled(self.params, "rotor_rumble_switch") and rotor_fraction > 0.02:
            rotor = PeriodicSpec(
                number(self.params, "rotor_rumble", 0) * rotor_fraction,
                8.0 + 14.0 * rotor_fraction,
                0x4000,
            ).bounded(self.cap_percent, self.gain)
            if rotor is not None:
                effects["rotor"] = rotor

        if enabled(self.params, "heli_blade_slap_switch"):
            speed_min = number(self.params, "heli_blade_slap_airspeed_min", 0)
            speed_max = max(
                number(self.params, "heli_blade_slap_airspeed_max", speed_min + 1),
                speed_min + 1,
            )
            aoa_min = number(self.params, "heli_blade_slap_aoa_min", 0)
            aoa_max = max(
                number(self.params, "heli_blade_slap_aoa_max", aoa_min + 1),
                aoa_min + 1,
            )
            speed_scale = clamp((speed - speed_min) / (speed_max - speed_min), 0, 1)
            aoa_scale = clamp((aoa - aoa_min) / (aoa_max - aoa_min), 0, 1)
            slap = PeriodicSpec(
                number(self.params, "heli_blade_slap_intensity", 0)
                * speed_scale
                * aoa_scale,
                12.0,
                0x2000,
            ).bounded(self.cap_percent, self.gain)
            if slap is not None:
                effects["blade-slap"] = slap

        if enabled(self.params, "etl_effeet_switch"):
            start = number(self.params, "etl_start_speed", 5)
            stop = max(number(self.params, "etl_stop_speed", start + 1), start + 1)
            if start < speed < stop:
                midpoint = (start + stop) / 2.0
                half_width = (stop - start) / 2.0
                scale = 1.0 - abs(speed - midpoint) / half_width
                etl = PeriodicSpec(
                    number(self.params, "etl_effeet", 0) * scale,
                    10.0,
                    0x6000,
                ).bounded(self.cap_percent, self.gain)
                if etl is not None:
                    effects["etl"] = etl

        if enabled(self.params, "vrs_max_intensity_switch"):
            speed_threshold = max(number(self.params, "vrs_airspeed_threshold", 5), 0.1)
            descent = max(0.0, -as_float(telemetry, "vertical_velocity_speed"))
            onset = number(self.params, "vrs_onset_verticsl_speed", 0)
            full = max(number(self.params, "vrs_full_vertical_speed", onset + 1), onset + 1)
            if speed < speed_threshold and descent > onset:
                speed_scale = 1.0 - clamp(speed / speed_threshold, 0, 1)
                descent_scale = clamp((descent - onset) / (full - onset), 0, 1)
                vrs = PeriodicSpec(
                    number(self.params, "vrs_max_intensity", 0)
                    * speed_scale
                    * descent_scale,
                    9.0,
                    0x0000,
                ).bounded(self.cap_percent, self.gain)
                if vrs is not None:
                    effects["vrs"] = vrs
        return effects

    def update(self, telemetry: dict[str, str], now: float) -> dict[str, EffectSpec]:
        effects: dict[str, EffectSpec | None] = {
            "engine": self._engine(telemetry),
            "runway": self._runway(telemetry),
            "buffet": self._buffet(telemetry),
            "motion": self._motion(telemetry, now),
            "weapon": self._weapon(telemetry, now),
        }
        effects.update(self._core(telemetry))
        effects.update(self._helicopter(telemetry))
        return {name: spec for name, spec in effects.items() if spec is not None}


class DryRunSink:
    def __init__(self) -> None:
        self.current: dict[str, EffectSpec] = {}

    def apply(self, effects: dict[str, EffectSpec]) -> None:
        self.current = dict(effects)

    def clear(self) -> None:
        self.current.clear()

    def close(self) -> None:
        self.clear()


@dataclass
class UploadedEffect:
    effect_id: int
    spec: EffectSpec
    started_at: float


class EvdevSink:
    def __init__(self) -> None:
        self.device = self._find_ab9()
        self.uploaded: dict[str, UploadedEffect] = {}
        print(f"AB9 effect device: {self.device.path} ({self.device.name})", flush=True)

    @staticmethod
    def _find_ab9() -> InputDevice:
        for path in list_devices():
            device = InputDevice(path)
            if "AB9" not in (device.name or ""):
                device.close()
                continue
            capabilities = device.capabilities().get(ecodes.EV_FF, [])
            if ecodes.FF_PERIODIC not in capabilities and ecodes.FF_SINE not in capabilities:
                device.close()
                continue
            return device
        raise RuntimeError("MOZA AB9 FF_PERIODIC event device was not found")

    @staticmethod
    def _effect(spec: EffectSpec, effect_id: int) -> ff.Effect:
        if isinstance(spec, PeriodicSpec):
            magnitude = int(round(0x7FFF * spec.percent / 100.0))
            period_ms = int(round(1000.0 / spec.frequency_hz))
            periodic = ff.Periodic(
                waveform=ecodes.FF_SINE,
                period=period_ms,
                magnitude=magnitude,
                offset=0,
                phase=0,
                envelope=ff.Envelope(0, 0, 0, 0),
                custom_len=0,
                custom_data=None,
            )
            effect_type = ecodes.FF_PERIODIC
            direction = spec.direction
            union = ff.EffectType(ff_periodic_effect=periodic)
        else:
            def condition(percent: float) -> ff.Condition:
                strength = int(round(0x7FFF * percent / 100.0))
                return ff.Condition(
                    strength,
                    strength,
                    strength,
                    strength,
                    spec.deadband,
                    0,
                )

            conditions = (ff.Condition * 2)(
                condition(spec.x_percent), condition(spec.y_percent)
            )
            effect_type = spec.effect_type
            direction = 0
            union = ff.EffectType(ff_condition_effect=conditions)
        return ff.Effect(
            type=effect_type,
            id=effect_id,
            direction=direction,
            ff_trigger=ff.Trigger(0, 0),
            ff_replay=ff.Replay(60000, 0),
            u=union,
        )

    def _stop(self, name: str) -> None:
        uploaded = self.uploaded.pop(name, None)
        if uploaded is None:
            return
        try:
            self.device.write(ecodes.EV_FF, uploaded.effect_id, 0)
            self.device.syn()
        finally:
            self.device.erase_effect(uploaded.effect_id)

    def apply(self, effects: dict[str, EffectSpec]) -> None:
        for name in set(self.uploaded) - set(effects):
            self._stop(name)
        now = time.monotonic()
        for name, spec in effects.items():
            current = self.uploaded.get(name)
            if current is not None and current.spec == spec and now - current.started_at < 30:
                continue
            effect_id = current.effect_id if current is not None else -1
            uploaded_id = self.device.upload_effect(self._effect(spec, effect_id))
            should_start = current is None or now - current.started_at >= 30
            started_at = current.started_at if current is not None else now
            if should_start:
                self.device.write(ecodes.EV_FF, uploaded_id, 1)
                self.device.syn()
                started_at = now
            self.uploaded[name] = UploadedEffect(uploaded_id, spec, started_at)

    def clear(self) -> None:
        for name in list(self.uploaded):
            self._stop(name)

    def close(self) -> None:
        try:
            self.clear()
        finally:
            self.device.close()


def format_effects(effects: dict[str, EffectSpec]) -> str:
    if not effects:
        return "none"
    rendered: list[str] = []
    for name, spec in sorted(effects.items()):
        if isinstance(spec, PeriodicSpec):
            rendered.append(f"{name}={spec.percent:.1f}%@{spec.frequency_hz:.0f}Hz")
        else:
            rendered.append(f"{name}=x{spec.x_percent:.1f}%/y{spec.y_percent:.1f}%")
    return ", ".join(rendered)


def simulate(cap_percent: float, gain: float) -> int:
    profiles = ab9_profile.load_profiles()
    profile = ab9_profiled.select_profile(profiles, "P-51D")
    if profile is None:
        raise RuntimeError("copied P-51D profile not found")
    overrides = load_overrides().get(profile.vehicle, {})
    mixer = EffectMixer(profile, cap_percent, gain, overrides)
    now = 1000.0
    samples = [
        {
            "aircraft_name": "P-51D",
            "engine_rpm_left": "0",
            "ias": "0",
            "h_above_ground_level": "0",
            "gear_value": "1",
            "flap_pos": "0",
            "speedbrake_value": "0",
            "canopy_pos": "1",
            "cannon_shells": "250",
            "payload_count": "2",
            "flare": "0",
            "chaff": "0",
        },
        {
            "aircraft_name": "P-51D",
            "engine_rpm_left": "45",
            "ias": "12",
            "h_above_ground_level": "0",
            "gear_value": "1",
            "flap_pos": "0.2",
            "speedbrake_value": "0",
            "canopy_pos": "1",
            "cannon_shells": "250",
            "payload_count": "2",
            "flare": "0",
            "chaff": "0",
        },
        {
            "aircraft_name": "P-51D",
            "engine_rpm_left": "90",
            "ias": "85",
            "h_above_ground_level": "500",
            "gear_value": "0",
            "flap_pos": "0",
            "speedbrake_value": "0",
            "canopy_pos": "0",
            "cannon_shells": "247",
            "payload_count": "1",
            "flare": "0",
            "chaff": "0",
            "aoa": "8",
            "mach": "0.45",
        },
    ]
    print(f"Simulation profile: {profile.vehicle} / {profile.name}")
    for index, sample in enumerate(samples, 1):
        effects = mixer.update(sample, now)
        print(f"sample {index}: {format_effects(effects)}")
        now += 0.05
    now += 0.3
    print(f"after event expiry: {format_effects(mixer.update(samples[-1], now))}")
    return 0


def serve(do_apply: bool, cap_percent: float, gain: float) -> int:
    if do_apply and not armed():
        raise RuntimeError(
            f"hardware output requested but {ARM_FILE} is absent or not armed"
        )
    sink: DryRunSink | EvdevSink = EvdevSink() if do_apply else DryRunSink()
    profiles = ab9_profile.load_profiles()
    overrides = load_overrides()
    mixer: EffectMixer | None = None
    current_profile_id: str | None = None
    last_packet = 0.0
    last_report = 0.0
    last_rendered = ""
    running = True

    def stop(_signum: int, _frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, PORT))
    sock.settimeout(0.1)
    print(
        f"AB9 telemetry listener udp://{HOST}:{PORT}; "
        f"motor_output={'enabled' if do_apply else 'DRY-RUN'}; "
        f"gain={gain:.2f}x; cap={cap_percent:.0f}%",
        flush=True,
    )
    try:
        while running:
            try:
                packet, _ = sock.recvfrom(8192)
            except socket.timeout:
                packet = b""
            now = time.monotonic()
            if packet:
                kind, telemetry = parse_packet(packet)
                if kind == "stop":
                    sink.clear()
                    mixer = None
                    current_profile_id = None
                elif kind == "telemetry":
                    last_packet = now
                    aircraft = telemetry.get("aircraft_name", "")
                    profile = ab9_profiled.select_profile(profiles, aircraft)
                    if profile is None:
                        if current_profile_id is not None:
                            print(f"No copied telemetry profile for {aircraft!r}", flush=True)
                        sink.clear()
                        mixer = None
                        current_profile_id = None
                    else:
                        if profile.profile_id != current_profile_id:
                            sink.clear()
                            mixer = EffectMixer(
                                profile,
                                cap_percent,
                                gain,
                                overrides.get(profile.vehicle, {}),
                            )
                            current_profile_id = profile.profile_id
                            print(
                                f"Telemetry profile: {aircraft!r} -> "
                                f"{profile.name} ({profile.source})",
                                flush=True,
                            )
                        assert mixer is not None
                        effects = mixer.update(telemetry, now)
                        sink.apply(effects)
                        rendered = format_effects(effects)
                        if rendered != last_rendered or now - last_report >= 5.0:
                            print(
                                f"{'LIVE' if do_apply else 'DRY'} effects: {rendered}",
                                flush=True,
                            )
                            last_report = now
                            last_rendered = rendered
            if last_packet and now - last_packet > TELEMETRY_TIMEOUT:
                sink.clear()
                last_packet = 0.0
                if not do_apply:
                    print("Telemetry timeout: effects cleared", flush=True)
    finally:
        sink.close()
        sock.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="enable motor output (also requires the separate telemetry arm file)",
    )
    parser.add_argument(
        "--cap-percent",
        type=float,
        default=35.0,
        help="hard maximum magnitude for any synthesized effect (5..50, default 35)",
    )
    parser.add_argument(
        "--gain",
        type=float,
        default=1.0,
        help="multiply copied profile effect strengths before the hard cap",
    )
    parser.add_argument("--simulate", action="store_true", help="run motor-safe synthetic data")
    args = parser.parse_args()
    if not 5.0 <= args.cap_percent <= 50.0:
        parser.error("--cap-percent must be between 5 and 50")
    if not 0.25 <= args.gain <= 4.0:
        parser.error("--gain must be between 0.25 and 4.0")
    if args.apply and args.simulate:
        parser.error("--apply and --simulate are mutually exclusive")
    try:
        return (
            simulate(args.cap_percent, args.gain)
            if args.simulate
            else serve(args.apply, args.cap_percent, args.gain)
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
