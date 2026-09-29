import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location("benchmark", Path(__file__).with_name("dcs-vr-benchmark.py"))
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class FrameMetricsTests(unittest.TestCase):
    def test_reused_frames_are_not_counted_as_fresh(self):
        times = [0, 10_000_000, 20_000_000, 30_000_000]
        frames = [SimpleNamespace(when_gpu_done_ns=t + 1, when_wait_woke_ns=t,
                                  discarded=False, predicted_gpu_done_time_ns=t + 2)
                  for t in times]
        used = [SimpleNamespace(system_frame_id=i, session_frame_id=f, when_ns=t)
                for i, (f, t) in enumerate(zip([1, 2, 2, 3], times))]
        result = benchmark.summarize(frames, used, 0, 40_000_000, 100)
        self.assertAlmostEqual(result["fresh_frame_fps"], 2 / .03)
        self.assertAlmostEqual(result["reused_frame_percent"], 100 / 3)

    def test_capture_window_excludes_loading_and_discarded_frames(self):
        frames = [SimpleNamespace(when_gpu_done_ns=t, when_wait_woke_ns=t - 100,
                                  discarded=t == 15, predicted_gpu_done_time_ns=t - 1)
                  for t in [0, 10, 15, 20, 30]]
        used = [SimpleNamespace(system_frame_id=i, session_frame_id=i, when_ns=t)
                for i, t in enumerate([0, 10, 20, 30])]
        result = benchmark.summarize(frames, used, 10, 30, 75)
        self.assertEqual(result["application_frames"], 2)
        self.assertEqual(result["compositor_latches"], 2)
        self.assertEqual(result["predicted_gpu_deadline_late_percent"], 100)

    def test_truncated_varint_is_rejected(self):
        with self.assertRaises(EOFError):
            benchmark.varint(bytes([128]), 0)
        self.assertEqual(benchmark.varint(bytes([129, 1]), 0), (129, 2))


if __name__ == "__main__":
    unittest.main()
