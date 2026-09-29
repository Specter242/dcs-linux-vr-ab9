#!/usr/bin/env python3
"""Capture hardware counters and summarize Monado's official metrics stream.

Metrics schema: https://gitlab.freedesktop.org/monado/utilities/metrics
Generate a descriptor with protoc --descriptor_set_out=metrics-schema.pb.
Frame work includes application CPU/draw and GPU completion waiting; it is
not a measurement of GPU execution alone. Used records describe compositor
latches, so repeated application frame IDs measure stale-frame reuse.
"""

import argparse
import json
import math
from pathlib import Path
import statistics
import time


def distribution(values):
    if not values:
        return None
    ordered = sorted(values)
    return {
        "mean": statistics.mean(ordered),
        "p50": ordered[math.ceil(len(ordered) * .5) - 1],
        "p95": ordered[math.ceil(len(ordered) * .95) - 1],
        "p99": ordered[math.ceil(len(ordered) * .99) - 1],
        "max": ordered[-1],
    }


def varint(data, position):
    result = 0
    for shift in range(0, 70, 7):
        if position >= len(data):
            raise EOFError("Incomplete metrics record")
        byte = data[position]
        position += 1
        result |= (byte & 127) << shift
        if byte < 128:
            return result, position
    raise ValueError("Invalid metrics record length")


def read_records(path, schema):
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
    descriptors = descriptor_pb2.FileDescriptorSet.FromString(schema.read_bytes())
    pool = descriptor_pool.DescriptorPool()
    for descriptor in descriptors.file:
        pool.Add(descriptor)
    descriptor = pool.FindMessageTypeByName("monado_metrics.Record")
    if hasattr(message_factory, "GetMessageClass"):
        record_class = message_factory.GetMessageClass(descriptor)
    else:
        record_class = message_factory.MessageFactory(pool).GetPrototype(descriptor)
    data = path.read_bytes()
    position = 0
    while position < len(data):
        size, position = varint(data, position)
        if position + size > len(data):
            raise EOFError("Metrics file ends inside a record; stop Monado to flush it")
        record = record_class.FromString(data[position:position + size])
        position += size
        if record.WhichOneof("record") == "version" and record.version.major != 1:
            raise ValueError("Unsupported Monado metrics version")
        yield record


def summarize(frames, used, begin, end, hz):
    frames = [f for f in frames if begin <= f.when_gpu_done_ns < end and not f.discarded]
    used = sorted((u for u in used if begin <= u.when_ns < end), key=lambda u: u.system_frame_id)
    if len(frames) < 2 or len(used) < 2:
        raise ValueError("Capture contains too few application frames or compositor latches")
    unique = sum(a.session_frame_id != b.session_frame_id for a, b in zip(used, used[1:]))
    repeats = len(used) - 1 - unique
    intervals = [(b.when_gpu_done_ns - a.when_gpu_done_ns) / 1e6
                 for a, b in zip(sorted(frames, key=lambda f: f.when_gpu_done_ns),
                                 sorted(frames, key=lambda f: f.when_gpu_done_ns)[1:])]
    elapsed = (used[-1].when_ns - used[0].when_ns) / 1e9
    work = [(f.when_gpu_done_ns - f.when_wait_woke_ns) / 1e6 for f in frames]
    return {
        "window_seconds": (end - begin) / 1e9,
        "application_frames": len(frames), "compositor_latches": len(used),
        "fresh_frame_fps": unique / elapsed,
        "reused_frame_percent": 100 * repeats / (len(used) - 1),
        "frame_interval_ms": distribution(intervals),
        "application_work_ms": distribution(work),
        "work_over_refresh_budget_percent": 100 * sum(v > 1000 / hz for v in work) / len(work),
        "predicted_gpu_deadline_late_percent": 100 * sum(f.when_gpu_done_ns > f.predicted_gpu_done_time_ns for f in frames) / len(frames),
    }


def read_number(path):
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def process_ticks(pid):
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return int(fields[11]) + int(fields[12])


def capture(args):
    import os
    cards = [p for p in Path("/sys/class/drm").glob("card[0-9]*/device")
             if (p / "gpu_busy_percent").exists()]
    if len(cards) != 1:
        raise ValueError("Specify an unambiguous single AMD GPU before capturing")
    gpu = cards[0]
    hw = next(p for p in (gpu / "hwmon").glob("hwmon*") if (p / "name").read_text().strip() == "amdgpu")
    fields = {"gpu_busy_percent": gpu / "gpu_busy_percent",
              "vram_bytes": gpu / "mem_info_vram_used", "vram_total_bytes": gpu / "mem_info_vram_total",
              "gtt_bytes": gpu / "mem_info_gtt_used", "power_microwatts": hw / "power1_average",
              "junction_millicelsius": hw / "temp2_input"}
    mission_before = json.loads(args.mission_state.read_text()) if args.mission_state else None
    if mission_before and mission_before.get("paused"):
        raise ValueError("Unpause the mission before capturing flight performance")
    start = time.monotonic_ns()
    previous_time, previous_ticks = start, process_ticks(args.pid)
    samples = []
    for number in range(args.seconds):
        time.sleep(max(0, (start + (number + 1) * 10**9 - time.monotonic_ns()) / 1e9))
        now, ticks = time.monotonic_ns(), process_ticks(args.pid)
        sample = {"monotonic_ns": now, **{key: read_number(path) for key, path in fields.items()}}
        sample["dcs_cpu_one_core_percent"] = 100 * (ticks - previous_ticks) / os.sysconf("SC_CLK_TCK") / ((now - previous_time) / 1e9)
        for line in Path(f"/proc/{args.pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                sample["dcs_rss_bytes"] = int(line.split()[1]) * 1024
        samples.append(sample)
        previous_time, previous_ticks = now, ticks
    result = {"label": args.label, "pid": args.pid, "start_monotonic_ns": start,
              "end_monotonic_ns": now, "samples": samples,
              "mission_before": mission_before,
              "mission_after": json.loads(args.mission_state.read_text()) if args.mission_state else None,
              "hardware": {key: distribution([s[key] for s in samples if s[key] is not None])
                           for key in samples[0] if key != "monotonic_ns"}}
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"label": args.label, "hardware": result["hardware"]}, indent=2))


def analyze(args):
    capture_data = json.loads(args.capture.read_text())
    records = list(read_records(args.metrics, args.schema))
    frames = [r.session_frame for r in records if r.WhichOneof("record") == "session_frame"]
    counts = {sid: sum(f.session_id == sid and not f.discarded for f in frames)
              for sid in {f.session_id for f in frames}}
    # The launcher creates readiness, overlay, and then game sessions in order.
    sid = args.session if args.session is not None else max(counts)
    result = summarize([f for f in frames if f.session_id == sid],
                       [r.used for r in records if r.WhichOneof("record") == "used" and r.used.session_id == sid],
                       capture_data["start_monotonic_ns"], capture_data["end_monotonic_ns"], args.hz)
    result.update(label=capture_data["label"], session_id=sid, session_frame_counts=counts,
                  hardware=capture_data["hardware"])
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture_parser = sub.add_parser("capture")
    capture_parser.add_argument("--pid", type=int, required=True)
    capture_parser.add_argument("--seconds", type=int, default=60)
    capture_parser.add_argument("--label", required=True)
    capture_parser.add_argument("--mission-state", type=Path)
    capture_parser.add_argument("--output", type=Path, required=True)
    analyze_parser = sub.add_parser("analyze")
    analyze_parser.add_argument("--metrics", type=Path, required=True)
    analyze_parser.add_argument("--schema", type=Path, required=True)
    analyze_parser.add_argument("--capture", type=Path, required=True)
    analyze_parser.add_argument("--session", type=int)
    analyze_parser.add_argument("--hz", type=float, default=75)
    analyze_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "capture" and args.seconds < 2:
        parser.error("Capture must last at least two seconds")
    if args.command == "analyze" and args.hz <= 0:
        parser.error("Refresh rate must be positive")
    (capture if args.command == "capture" else analyze)(args)


if __name__ == "__main__":
    main()
