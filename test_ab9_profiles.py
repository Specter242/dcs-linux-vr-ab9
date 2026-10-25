#!/usr/bin/env python3
"""Synthetic file/UDP and mocked serial tests; never open an AB9 device."""

import contextlib
import io
import json
import signal
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import ab9_profile
import ab9_profiled
import ab9_telemetryd


def write_profile(root, aircraft, strength, name="Synthetic"):
    path = root / "custom" / f"{aircraft}.preset"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({
        "name": name,
        "uuid": aircraft,
        "devices": ["AB9"],
        "games": ["dcs_world"],
        "vehicles": aircraft,
        "telemetry_params": {"weapon_switch": 1, "gun_vibration": strength},
    }), encoding="utf-8")


def telemetry_packet(aircraft, shells):
    return f"AB9_TELEMETRY\taircraft_name,{aircraft};cannon_shells,{shells};".encode()


class ProfileReloadTests(unittest.TestCase):
    def run_telemetry(self, root, packets, times=None):
        sink = Mock()
        sock = Mock()
        handlers = {}
        stream = iter(packets)
        output = io.StringIO()
        errors = io.StringIO()

        def receive(size):
            try:
                packet = next(stream)
            except StopIteration:
                handlers[signal.SIGTERM](None, None)
                packet = None
            if packet is None:
                raise socket.timeout
            return packet, None

        sock.recvfrom.side_effect = receive
        with (
            patch.object(ab9_profile, "PROFILE_ROOT", root),
            patch.object(ab9_telemetryd, "OVERRIDES_FILE", root / "overrides.json"),
            patch.object(ab9_telemetryd.socket, "socket", return_value=sock),
            patch.object(ab9_telemetryd.signal, "signal", side_effect=handlers.__setitem__),
            patch.object(ab9_telemetryd.time, "monotonic", side_effect=times, return_value=100.0),
            patch.object(ab9_telemetryd, "DryRunSink", return_value=sink),
            patch.object(ab9_telemetryd, "EvdevSink") as hardware,
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            self.assertEqual(ab9_telemetryd.serve(False, 15, 1), 0)
        hardware.assert_not_called()
        sink.close.assert_called_once()
        sock.close.assert_called_once()
        return sink, output.getvalue(), errors.getvalue()

    def test_bad_override_reload_disables_effects_without_stopping_listener(self):
        for bad_data in (b'{"profiles":', b'\xff', b'[]', b'{"profiles": []}', b'{"profiles": {"Mi-8MT": []}}'):
            with self.subTest(data=bad_data), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_profile(root, "JF-17", 4)
                write_profile(root, "Mi-8MT", 2)

                def packets():
                    yield telemetry_packet("JF-17", 100)
                    yield telemetry_packet("JF-17", 99)
                    (root / "overrides.json").write_bytes(bad_data)
                    yield telemetry_packet("Mi-8MT", 100)
                    yield telemetry_packet("Mi-8MT", 99)
                    (root / "overrides.json").write_text("{}", encoding="utf-8")
                    yield telemetry_packet("JF-17", 100)
                    yield telemetry_packet("JF-17", 99)

                sink, output, errors = self.run_telemetry(root, packets())
                strengths = [
                    args.args[0]["weapon"].percent
                    for args in sink.apply.call_args_list if "weapon" in args.args[0]
                ]
                self.assertEqual(strengths, [4, 4])
                self.assertEqual(errors.count("ERROR loading telemetry profile"), 1)
                self.assertIn("overrides.json", errors)

    def test_bad_preset_reload_does_not_fall_back_to_official(self):
        for bad_data in (b'{"devices":', b'\xff', b'[]', b'{}'):
            with self.subTest(data=bad_data), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_profile(root, "JF-17", 4)
                write_profile(root, "Mi-8MT", 2)
                custom = root / "custom" / "Mi-8MT.preset"
                (root / "official").mkdir()
                (root / "official" / custom.name).write_bytes(custom.read_bytes())

                def packets():
                    yield telemetry_packet("JF-17", 100)
                    yield telemetry_packet("JF-17", 99)
                    custom.write_bytes(bad_data)
                    yield telemetry_packet("Mi-8MT", 100)
                    yield telemetry_packet("Mi-8MT", 99)
                    write_profile(root, "Mi-8MT", 6)
                    yield telemetry_packet("JF-17", 100)
                    yield telemetry_packet("JF-17", 99)
                    yield telemetry_packet("Mi-8MT", 100)
                    yield telemetry_packet("Mi-8MT", 99)

                sink, output, errors = self.run_telemetry(root, packets())
                strengths = [
                    args.args[0]["weapon"].percent
                    for args in sink.apply.call_args_list if "weapon" in args.args[0]
                ]
                self.assertEqual(strengths, [4, 4, 6])
                self.assertEqual(errors.count("ERROR loading telemetry profile"), 1)
                self.assertIn("Mi-8MT.preset", errors)

    def test_telemetry_without_matching_preset_stays_running(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_profile(root, "JF-17", 4)
            packets = [
                telemetry_packet("JF-17", 100),
                telemetry_packet("JF-17", 99),
                telemetry_packet("Unknown", 100),
                telemetry_packet("Unknown", 99),
                telemetry_packet("JF-17", 100),
                telemetry_packet("JF-17", 99),
            ]
            sink, output, errors = self.run_telemetry(root, packets)
            self.assertEqual(output.count("No copied telemetry profile"), 1)
            self.assertEqual(len(sink.apply.call_args_list), 4)
            self.assertEqual(errors, "")

    def test_profile_selector_refuses_broken_custom_preset_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_profile(root, "JF-17", 4)
            write_profile(root, "Mi-8MT", 2, "Official fallback")
            custom = root / "custom" / "Mi-8MT.preset"
            (root / "official").mkdir()
            (root / "official" / custom.name).write_bytes(custom.read_bytes())

            def packets():
                yield b"AB9_AIRCRAFT\tJF-17", None
                custom.write_text('{"devices":', encoding="utf-8")
                yield b"AB9_AIRCRAFT\tMi-8MT", None
                yield b"AB9_AIRCRAFT\tMi-8MT", None
                write_profile(root, "Mi-8MT", 6, "Repaired custom")
                yield b"AB9_AIRCRAFT\tJF-17", None
                yield b"AB9_AIRCRAFT\tMi-8MT", None

            stream = packets()
            sock = Mock()
            sock.recvfrom.side_effect = lambda size: next(stream)
            errors = io.StringIO()
            with (
                patch.object(ab9_profile, "PROFILE_ROOT", root),
                patch.object(ab9_profiled, "armed", return_value=True),
                patch.object(ab9_profiled.socket, "socket", return_value=sock),
                patch.object(ab9_profiled.time, "sleep"),
                patch.object(ab9_profile, "apply_profile") as hardware,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(errors),
                self.assertRaises(StopIteration),
            ):
                ab9_profiled.listen(True, "native")
            self.assertEqual(
                [args.args[0].name for args in hardware.call_args_list],
                ["Synthetic", "Synthetic", "Repaired custom"],
            )
            self.assertEqual(errors.getvalue().count("ERROR applying profile"), 1)
            self.assertIn("Mi-8MT.preset", errors.getvalue())

    def test_aircraft_alias_does_not_hide_an_edited_profile(self):
        profile = ab9_profile.Profile("test", Path("synthetic.preset"), {
            "uuid": "shared",
            "vehicles": "P-51D-30-NA",
            "name": "Before edit",
        })
        edited = ab9_profile.Profile(profile.source, profile.path, {
            **profile.data, "name": "After edit",
        })
        with (
            patch.object(ab9_profiled, "armed", return_value=True),
            patch.object(ab9_profile, "apply_profile") as hardware,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            last = ab9_profiled.handle_aircraft("P-51D", [profile], True, "native", None)
            last = ab9_profiled.handle_aircraft("TF-51D", [profile], True, "native", last)
            self.assertEqual(hardware.call_count, 1)
            ab9_profiled.handle_aircraft("P-51D", [edited], True, "native", last)
            self.assertEqual(hardware.call_count, 2)

    def test_telemetry_reloads_presets_and_overrides_on_aircraft_change(self):
        for use_override in (False, True):
            with self.subTest(overrides=use_override), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                overrides = root / "overrides.json"
                write_profile(root, "JF-17", 4)
                write_profile(root, "Mi-8MT", 2)
                sink = Mock()
                sock = Mock()
                handlers = {}

                def packets():
                    yield telemetry_packet("JF-17", 100), None
                    yield telemetry_packet("JF-17", 99), None
                    write_profile(root, "JF-17", 6)
                    if use_override:
                        overrides.write_text(json.dumps({
                            "profiles": {"JF-17": {"gun_vibration_intensity": 9}},
                        }), encoding="utf-8")
                    # A file edit alone must not change a running effect.
                    yield telemetry_packet("JF-17", 98), None
                    yield telemetry_packet("Mi-8MT", 100), None
                    yield telemetry_packet("JF-17", 100), None
                    yield telemetry_packet("JF-17", 99), None
                    handlers[signal.SIGTERM](None, None)
                    raise socket.timeout

                stream = packets()
                sock.recvfrom.side_effect = lambda size: next(stream)
                with (
                    patch.object(ab9_profile, "PROFILE_ROOT", root),
                    patch.object(ab9_profiled, "armed", return_value=False),
                    patch.object(ab9_telemetryd, "OVERRIDES_FILE", overrides),
                    patch.object(ab9_telemetryd.socket, "socket", return_value=sock),
                    patch.object(ab9_telemetryd.signal, "signal", side_effect=handlers.__setitem__),
                    patch.object(ab9_telemetryd.time, "monotonic", return_value=100.0),
                    patch.object(ab9_telemetryd, "DryRunSink", return_value=sink),
                    patch.object(ab9_telemetryd, "EvdevSink") as hardware,
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(ab9_telemetryd.serve(False, 15, 1), 0)
                strengths = [
                    args.args[0]["weapon"].percent
                    for args in sink.apply.call_args_list
                    if "weapon" in args.args[0]
                ]
                self.assertEqual(strengths, [4, 4, 9 if use_override else 6])
                hardware.assert_not_called()
                sink.close.assert_called_once()

    def test_profile_selector_reloads_presets_only_on_aircraft_change(self):
        for apply in (False, True):
            with self.subTest(apply=apply), tempfile.TemporaryDirectory() as directory:
                self.check_profile_selector(Path(directory), apply)

    def check_profile_selector(self, root, do_apply):
        write_profile(root, "JF-17", 4, "Before edit")
        write_profile(root, "Mi-8MT", 2)

        def packets():
            yield b"AB9_AIRCRAFT\tJF-17", None
            write_profile(root, "JF-17", 6, "After edit")
            yield b"AB9_AIRCRAFT\tJF-17", None
            yield b"AB9_AIRCRAFT\tMi-8MT", None
            yield b"AB9_AIRCRAFT\tJF-17", None

        stream = packets()
        sock = Mock()
        sock.recvfrom.side_effect = lambda size: next(stream)
        output = io.StringIO()
        with (
            patch.object(ab9_profile, "PROFILE_ROOT", root),
            patch.object(ab9_profiled, "armed", return_value=True),
            patch.object(ab9_profiled.socket, "socket", return_value=sock),
            patch.object(ab9_profiled.time, "sleep"),
            patch.object(ab9_profile, "load_profiles", wraps=ab9_profile.load_profiles) as load,
            patch.object(ab9_profile, "apply_profile") as hardware,
            contextlib.redirect_stdout(output),
            self.assertRaises(StopIteration),
        ):
            ab9_profiled.listen(do_apply, "native")
        self.assertIn("Before edit", output.getvalue())
        self.assertIn("After edit", output.getvalue())
        self.assertEqual(load.call_count, 3)
        if do_apply:
            self.assertEqual(
                [args.args[0].name for args in hardware.call_args_list],
                ["Before edit", "Synthetic", "After edit"],
            )
        else:
            hardware.assert_not_called()

    def test_telemetry_timeout_reloads_files_and_resets_weapon_counter_baseline(self):
        for use_override in (False, True):
            with self.subTest(overrides=use_override), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_profile(root, "JF-17", 4)

                def packets():
                    yield telemetry_packet("JF-17", 100)
                    yield telemetry_packet("JF-17", 99)
                    write_profile(root, "JF-17", 6)
                    if use_override:
                        (root / "overrides.json").write_text(json.dumps({
                            "profiles": {"JF-17": {"gun_vibration_intensity": 9}},
                        }), encoding="utf-8")
                    yield None
                    yield telemetry_packet("JF-17", 98)
                    yield telemetry_packet("JF-17", 97)

                sink, output, errors = self.run_telemetry(
                    root, packets(), times=[100, 100.05, 101, 101.01, 101.02, 101.03],
                )
                effects = [args.args[0] for args in sink.apply.call_args_list]
                self.assertEqual(effects[0], {})
                self.assertEqual(effects[1]["weapon"].percent, 4)
                self.assertEqual(effects[2], {})
                self.assertEqual(effects[3]["weapon"].percent, 9 if use_override else 6)
                self.assertEqual(errors, "")


class ProfileApplicationTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.profile = ab9_profile.Profile("test", Path("synthetic.preset"), {
            "vehicles": "JF-17",
            "device_params": {"bacic_max_torque": 80, "bacic_spring": 20},
        })
        self.values = {
            "bacic_max_torque": 25,
            "bacic_spring": 5,
            "bacic_force_model": 1,
            "bacic_gameForceFeedback": 100,
        }
        self.stack.enter_context(patch.object(ab9_profile.ab9_control, "find_port", return_value="unused"))
        self.stack.enter_context(patch.object(ab9_profile.moza, "open_port", return_value=42))
        self.close = self.stack.enter_context(patch.object(ab9_profile.os, "close"))
        self.stack.enter_context(patch.object(ab9_profile, "read_app_state", return_value=1))
        self.stack.enter_context(patch.object(ab9_profile.time, "sleep"))
        self.stack.enter_context(patch.object(ab9_profile, "read_setting", side_effect=lambda fd, s: self.values[s.key]))
        self.write = self.stack.enter_context(patch.object(
            ab9_profile, "write_setting",
            side_effect=lambda fd, s, value: self.values.__setitem__(s.key, value),
        ))
        self.gate = self.stack.enter_context(patch.object(ab9_profile.ab9_control, "write_value"))
        self.output = io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.output))
        self.stack.enter_context(contextlib.redirect_stderr(self.output))

    def test_profile_logs_progress_and_retains_torque_limit(self):
        ab9_profile.apply_profile(self.profile, "native", assume_ready=True)
        self.assertEqual(self.values["bacic_max_torque"], 25)
        self.assertEqual(self.values["bacic_spring"], 20)
        self.assertIn("Reading AB9 base settings", self.output.getvalue())
        self.assertIn("Force output muted", self.output.getvalue())
        self.assertIn("muted for", self.output.getvalue())
        self.assertIn("force output restored", self.output.getvalue())
        self.assertEqual(self.gate.call_args_list, [
            call(42, "Force-output disable gate", (0xDE,), 1, 2),
            call(42, "FFB controller", (0xE1, 0x17), 1, 1),
            call(42, "Force-output disable gate", (0xDE,), 0, 2),
        ])
        self.close.assert_called_once_with(42)

    def test_failed_verification_keeps_force_output_muted(self):
        self.write.side_effect = None
        with self.assertRaisesRegex(RuntimeError, "verification failed"):
            ab9_profile.apply_profile(self.profile, "native", assume_ready=True)
        self.gate.assert_called_once_with(42, "Force-output disable gate", (0xDE,), 1, 2)
        self.assertIn("did not restore force output", self.output.getvalue())
        self.close.assert_called_once_with(42)


if __name__ == "__main__":
    unittest.main()
