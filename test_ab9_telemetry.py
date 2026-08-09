#!/usr/bin/env python3
import unittest
from pathlib import Path

import ab9_profile
from ab9_telemetryd import ConditionSpec, EffectMixer, EvdevSink, PeriodicSpec, parse_packet


BASELINE = {
    "aircraft_name": "test",
    "engine_rpm_left": "0",
    "engine_rpm_right": "0",
    "ias": "0",
    "tas": "0",
    "mach": "0",
    "aoa": "0",
    "h_above_ground_level": "1000",
    "left_gear": "0",
    "nose_gear": "0",
    "right_gear": "0",
    "gear_value": "0",
    "flap_pos": "0",
    "speedbrake_value": "0",
    "canopy_pos": "0",
    "cannon_shells": "500",
    "payload_count": "8",
    "flare": "30",
    "chaff": "30",
    "wind_x": "0",
    "wind_y": "0",
    "wind_z": "0",
}


def synthetic_profile(vehicle="P-51D-30-NA", name="Synthetic fixed wing"):
    """Build a redistributable fixture instead of loading MOZA preset files."""
    return ab9_profile.Profile(
        "test",
        Path("synthetic-fixed-wing.preset"),
        {
            "name": name,
            "vehicles": vehicle,
            "uuid": "synthetic-fixed-wing",
            "device_params": {"bacic_damper": 20},
            "telemetry_params": {
                "propeller_vibration_switch": True,
                "engine_rumble_low_intensity": 5,
                "engine_rumble_high_intensity": 12,
                "runway_rumble_switch": True,
                "runway_rumble": 10,
                "gear_buffet_switch": True,
                "gear_buffet": 12,
                "gear_buffet_frequency": 10,
                "stall_buffeting_switch": True,
                "stall_aoa": 12,
                "stall_buffeting": 25,
                "buffeting_frequency": 11,
                "gear_motion_switch": True,
                "gear_motion": 12,
                "flaps_motion_switch": True,
                "flaps_motion": 10,
                "speedbrake_motion_switch": True,
                "speedbrake_motion": 10,
                "canopy_motion_switch": True,
                "canopy_motion": 8,
                "weapon_switch": True,
                "gun_vibration": 18,
                "weapon_release": 20,
                "countermeasure_release": 10,
                "DrivingControl_switch": True,
                "speed_thresh": 70,
                "max_intensity_speed": 400,
                "dyn_pressure_scale": 100,
            },
        },
    )


def synthetic_helicopter(vehicle="AH-64D_BLK_II"):
    return ab9_profile.Profile(
        "test",
        Path("synthetic-helicopter.preset"),
        {
            "name": "Synthetic helicopter",
            "vehicles": vehicle,
            "uuid": f"synthetic-{vehicle}",
            "device_params": {"bacic_damper": 20},
            "telemetry_params": {
                "jet_engine_rumble_switch": True,
                "jet_engine_rumble": 8,
                "jet_engine_rumble_freq": 20,
                "rotor_rumble_switch": True,
                "rotor_rumble": 6,
                "heli_blade_slap_switch": True,
                "heli_blade_slap_intensity": 12,
                "heli_blade_slap_airspeed_min": 5,
                "heli_blade_slap_airspeed_max": 30,
                "heli_blade_slap_aoa_min": 5,
                "heli_blade_slap_aoa_max": 20,
                "etl_effeet_switch": True,
                "etl_start_speed": 5,
                "etl_stop_speed": 30,
                "etl_effeet": 8,
                "vrs_max_intensity_switch": True,
                "vrs_airspeed_threshold": 5,
                "vrs_onset_verticsl_speed": 2,
                "vrs_full_vertical_speed": 12,
                "vrs_max_intensity": 10,
            },
        },
    )


class TelemetryTests(unittest.TestCase):
    def test_packet_parser(self):
        kind, fields = parse_packet(
            b"AB9_TELEMETRY\taircraft_name,P-51D;ias,82.5;cannon_shells,240;"
        )
        self.assertEqual(kind, "telemetry")
        self.assertEqual(fields["aircraft_name"], "P-51D")
        self.assertEqual(fields["ias"], "82.5")

    def test_representative_profiles_stay_bounded(self):
        profiles = [
            synthetic_profile(),
            synthetic_helicopter(),
            synthetic_helicopter("Ka-50_3"),
        ]
        for profile in profiles:
            with self.subTest(profile=profile.profile_id):
                mixer = EffectMixer(profile, 35.0)
                first = dict(BASELINE, aircraft_name=profile.vehicle)
                mixer.update(first, 100.0)
                active = dict(
                    first,
                    engine_rpm_left="100",
                    ias="180",
                    tas="190",
                    mach="0.85",
                    aoa="30",
                    h_above_ground_level="0",
                    left_gear="1",
                    nose_gear="1",
                    right_gear="1",
                    gear_value="1",
                    flap_pos="1",
                    speedbrake_value="1",
                    canopy_pos="1",
                    cannon_shells="490",
                    payload_count="7",
                    flare="29",
                    chaff="29",
                    wind_x="20",
                )
                effects = mixer.update(active, 100.05)
                for spec in effects.values():
                    if isinstance(spec, PeriodicSpec):
                        self.assertGreaterEqual(spec.percent, 0.25)
                        self.assertLessEqual(spec.percent, 35.0)
                        self.assertGreaterEqual(spec.frequency_hz, 5.0)
                        self.assertLessEqual(spec.frequency_hz, 50.0)
                    else:
                        self.assertIsInstance(spec, ConditionSpec)
                        self.assertGreaterEqual(spec.x_percent, 0.0)
                        self.assertLessEqual(spec.x_percent, 35.0)
                        self.assertGreaterEqual(spec.y_percent, 0.0)
                        self.assertLessEqual(spec.y_percent, 35.0)

    def test_event_effects_expire(self):
        profile = synthetic_profile()
        mixer = EffectMixer(profile, 35.0)
        mixer.update(dict(BASELINE, aircraft_name=profile.vehicle), 200.0)
        fired = dict(
            BASELINE,
            aircraft_name=profile.vehicle,
            engine_rpm_left="0",
            cannon_shells="499",
            payload_count="7",
            flap_pos="0.5",
        )
        effects = mixer.update(fired, 200.05)
        self.assertIn("weapon", effects)
        self.assertIn("motion", effects)
        effects = mixer.update(fired, 200.50)
        self.assertNotIn("weapon", effects)
        self.assertNotIn("motion", effects)

    def test_effect_structure_is_standard_periodic(self):
        profile = synthetic_profile()
        mixer = EffectMixer(profile, 35.0)
        sample = dict(BASELINE, aircraft_name=profile.vehicle, engine_rpm_left="75")
        spec = mixer.update(sample, 300.0)["engine"]
        effect = EvdevSink._effect(spec, -1)
        self.assertEqual(effect.id, -1)
        self.assertGreater(effect.u.ff_periodic_effect.magnitude, 0)
        self.assertEqual(len(memoryview(effect).tobytes()), 48)

    def test_gain_multiplies_then_caps(self):
        profile = synthetic_profile()
        mixer = EffectMixer(profile, 35.0, 2.0)
        sample = dict(BASELINE, aircraft_name=profile.vehicle, engine_rpm_left="65")
        effects = mixer.update(sample, 400.0)
        self.assertEqual(effects["engine"].percent, 15.5)
        strong = effects["engine"].__class__(47.0, 18.0, 0).bounded(35.0, 2.0)
        self.assertIsNotNone(strong)
        self.assertEqual(strong.percent, 35.0)

    def test_dynamic_pressure_condition_effects(self):
        profile = synthetic_profile()
        mixer = EffectMixer(profile, 35.0, 2.0)
        sample = dict(BASELINE, aircraft_name=profile.vehicle, ias="100")
        effects = mixer.update(sample, 500.0)
        self.assertIn("dynamic-spring", effects)
        self.assertIn("dynamic-damper", effects)
        effect = EvdevSink._effect(effects["dynamic-spring"], -1)
        self.assertEqual(effect.type, 83)  # FF_SPRING
        self.assertGreater(effect.u.ff_condition_effect[0].right_coeff, 0)
        self.assertGreater(effect.u.ff_condition_effect[1].right_coeff, 0)

    def test_helicopter_uses_rotor_model_not_fixed_wing_spring(self):
        profile = synthetic_helicopter("OH58D")
        mixer = EffectMixer(profile, 35.0, 2.0)
        sample = dict(
            BASELINE,
            aircraft_name=profile.vehicle,
            engine_rpm_left="100",
            helicopter_rotor_rpm="100",
            ias="18",
            tas="18",
            aoa="14",
            vertical_velocity_speed="0",
        )
        effects = mixer.update(sample, 600.0)
        self.assertNotIn("dynamic-spring", effects)
        self.assertNotIn("dynamic-damper", effects)
        self.assertIn("rotor", effects)
        self.assertIn("blade-slap", effects)

    def test_helicopter_vrs_uses_profile_thresholds(self):
        profile = synthetic_helicopter("Ka-50_3")
        mixer = EffectMixer(profile, 35.0, 2.0)
        sample = dict(
            BASELINE,
            aircraft_name=profile.vehicle,
            engine_rpm_left="100",
            ias="1",
            tas="1",
            vertical_velocity_speed="-15",
        )
        effects = mixer.update(sample, 700.0)
        self.assertIn("vrs", effects)
        self.assertLessEqual(effects["vrs"].percent, 35.0)

    def test_apache_comfort_override_keeps_idle_vibration_gentle(self):
        profile = synthetic_helicopter()
        overrides = {
            "jet_engine_rumble_switch": False,
            "rotor_rumble": 2.5,
            "runway_rumble": 4,
            "heli_blade_slap_intensity": 12,
            "etl_effeet": 6,
            "vrs_max_intensity": 8,
        }
        mixer = EffectMixer(profile, 15.0, 1.0, overrides)
        idle = dict(
            BASELINE,
            aircraft_name=profile.vehicle,
            engine_rpm_left="100",
            helicopter_rotor_rpm="100",
        )
        effects = mixer.update(idle, 800.0)
        self.assertNotIn("engine", effects)
        self.assertIn("rotor", effects)
        self.assertLessEqual(effects["rotor"].percent, 2.5)


if __name__ == "__main__":
    unittest.main()
