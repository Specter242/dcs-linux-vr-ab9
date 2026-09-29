"""Verify freshness boundaries and corrupt status handling without hardware."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

loader = importlib.machinery.SourceFileLoader("eye", str(Path(__file__).with_name("dcs-eye-tracking")))
spec = importlib.util.spec_from_loader(loader.name, loader)
eye = importlib.util.module_from_spec(spec)
loader.exec_module(eye)


class EyeHealthTests(unittest.TestCase):
    def test_only_recent_nonempty_output_is_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gaze.json"
            for timestamp, samples, messages, expected in [(99, 60, 120, True), (96, 60, 120, False), (101, 60, 120, False), (99, 0, 120, False), (99, 60, 0, False)]:
                with self.subTest(timestamp=timestamp, samples=samples, messages=messages):
                    path.write_text(json.dumps(dict(timestamp=timestamp, eyeUpdates=samples, dfrMessagesSent=messages)))
                    self.assertEqual(eye.gaze_status(path, now=100)["fresh"], expected)

    def test_missing_and_corrupt_status_are_not_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gaze.json"
            self.assertFalse(eye.gaze_status(path)["fresh"])
            for value in ["{", "null", "{}", '{"timestamp":"bad"}']:
                path.write_text(value)
                self.assertFalse(eye.gaze_status(path)["fresh"])


if __name__ == "__main__":
    unittest.main()
