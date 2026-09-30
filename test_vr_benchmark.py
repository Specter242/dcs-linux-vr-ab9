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
        frames = [SimpleNamespace(frame_id=i + 1, when_gpu_done_ns=t + 1, when_wait_woke_ns=t,
                                  discarded=False, predicted_gpu_done_time_ns=t + 2)
                  for i, t in enumerate(times)]
        used = [SimpleNamespace(system_frame_id=i, session_frame_id=f, when_ns=t)
                for i, (f, t) in enumerate(zip([1, 2, 2, 3], times))]
        result = benchmark.summarize(frames, used, 0, 40_000_000, 100)
        self.assertAlmostEqual(result["fresh_frame_fps"], 2 / .03)
        self.assertAlmostEqual(result["reused_frame_percent"], 100 / 3)

    def test_capture_window_excludes_loading_and_discarded_frames(self):
        frames = [SimpleNamespace(frame_id=i, when_gpu_done_ns=t, when_wait_woke_ns=t - 100,
                                  discarded=t == 15, predicted_gpu_done_time_ns=t - 1)
                  for i, t in enumerate([0, 10, 15, 20, 30])]
        used = [SimpleNamespace(system_frame_id=i, session_frame_id=i, when_ns=t)
                for i, t in enumerate([0, 10, 20, 30])]
        result = benchmark.summarize(frames, used, 10, 30, 75)
        self.assertEqual(result["application_frames"], 2)
        self.assertEqual(result["compositor_latches"], 2)
        self.assertEqual(result["predicted_gpu_deadline_late_percent"], 100)

    def test_latch_age_joins_frame_ids_and_retains_boundary_frame(self):
        frames = [SimpleNamespace(frame_id=i, when_gpu_done_ns=done,
                                  when_wait_woke_ns=done - 5_000_000, discarded=False,
                                  predicted_gpu_done_time_ns=done)
                  for i, done in [(1, 0), (2, 12_000_000), (3, 22_000_000), (4, 25_000_000)]]
        used = [SimpleNamespace(system_frame_id=i, session_frame_id=f, when_ns=t)
                for i, (f, t) in enumerate([(1, 10_000_000), (1, 20_000_000),
                                            (2, 30_000_000), (3, 40_000_000)])]
        result = benchmark.summarize(frames, used, 10_000_000, 35_000_000, 100)
        self.assertEqual(result["longest_consecutive_reused_latches"], 1)
        self.assertAlmostEqual(result["completed_frames_never_latched_percent"], 100 / 3)
        self.assertEqual(result["completion_to_first_latch_ms"]["mean"], 18)
        self.assertEqual(result["wake_to_latch_ms"]["max"], 25)

    def test_truncated_varint_is_rejected(self):
        with self.assertRaises(EOFError):
            benchmark.varint(bytes([128]), 0)
        self.assertEqual(benchmark.varint(bytes([129, 1]), 0), (129, 2))


if __name__ == "__main__":
    unittest.main()
