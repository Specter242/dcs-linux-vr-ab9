import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("quality", Path(__file__).with_name("dcs-vr-quality.py"))
quality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality)


class QualityTests(unittest.TestCase):
    def test_graphics_edit_preserves_other_sections(self):
        text = '["VR"] = { ["maxFPS"] = 50 },\n["graphics"] = { ["maxFPS"] = 75 },\n["miscellaneous"] = {}'
        result = quality.update_graphics(text, {"maxFPS": 0})
        self.assertIn('["VR"] = { ["maxFPS"] = 50 }', result)
        self.assertIn('["graphics"] = { ["maxFPS"] = 0 }', result)

    def test_duplicate_quad_overrides_are_rejected(self):
        with self.assertRaises(ValueError):
            quality.update_quad('focus_multiplier=1\n[DCS]\nfocus_multiplier=2\n', {"focus_multiplier": 1.1})

    def test_missing_quad_setting_is_added_before_app_sections(self):
        result = quality.update_quad('[other-app]\nturbo_mode=1\n', {"focus_multiplier": 1.1})
        self.assertTrue(result.startswith('focus_multiplier=1.1\n[other-app]'))

    def test_backup_restore_and_newer_change_protection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = root / 'prefix/pfx/drive_c/users/steamuser'
            options = base / 'Saved Games/DCS/Config/options.lua'
            quad = base / 'AppData/Local/Quad-Views-Foveated/settings.cfg'
            options.parent.mkdir(parents=True)
            quad.parent.mkdir(parents=True)
            original = '["graphics"] = { ["Upscaling"] = "FSR", ["maxFPS"] = 75 },\n["miscellaneous"] = {}'
            options.write_text(original)
            quad.write_text('focus_multiplier=1\n')
            backup = quality.apply_profile(root / 'prefix', 'clear', root / 'state')
            options.write_text(options.read_text() + '\n-- newer edit')
            with self.assertRaises(ValueError):
                quality.restore(backup)
            options.write_text((backup / '0-after').read_text())
            quality.restore(backup)
            self.assertEqual(options.read_text(), original)
            self.assertEqual(quad.read_text(), 'focus_multiplier=1\n')


if __name__ == '__main__':
    unittest.main()
