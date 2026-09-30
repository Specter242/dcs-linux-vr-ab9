# VR benchmark — September 29, 2026

The August observations remain historical. The old 15-second capture script
printed a separately observed 75 FPS rather than recording frame timing.
Its multiplayer replay uses track version 427; DCS 2.9.29.27468 requires 428
and rejected it. No track metadata was rewritten to bypass that check.

The new comparison uses the installed AH-64D Caucasus Runway Start mission,
75 Hz Beyond, live eye tracking, and quad views. Each settings change is named
in the table; runtime comparisons retain the same graphics settings.
WayVR is stopped throughout the captures. The preserved GE10 runtime and
GE11 runtime both use the current shared DCS installation. These runs measure
runtime/settings differences, not the isolated effect of the DCS update.

A temporary simulator hook observes model time and pause state. Mission
loading and shader compilation precede measurement. Hardware counters are
sampled each second for 60 seconds; Monado records per-frame timestamps.
Head movement and gaze remain live, so small differences can reflect viewing
changes. A stationary ground mission does not reproduce a busy multiplayer
server or prove performance during all flight conditions.

## Measurements

All windows are 60 seconds. Fresh FPS measures DCS content at compositor
latches, rather than the compositor's 75 Hz cadence.

| Runtime / settings | Fresh FPS | Reused latches | Completion interval p99 | GPU busy | VRAM mean |
| --- | ---: | ---: | ---: | ---: | ---: |
| GE11, original visuals, cap 75 | 65.41 | 12.78% | 19.52 ms | 70.5% | 14.17 GiB |
| Same, repeat | 65.90 | 12.14% | 18.93 ms | 70.5% | 14.11 GiB |
| Same, Performance power profile | 66.01 | 11.98% | 19.73 ms | 70.6% | 14.13 GiB |
| GE10, same current DCS and original visuals | 68.10 | 9.20% | 19.12 ms | 70.3% | 14.33 GiB |
| GE11, original visuals, cap removed | 65.11 | 13.05% | 19.52 ms | 71.0% | 14.33 GiB |
| Same, repeat | 70.93 | 5.42% | 18.39 ms | 69.7% | 14.20 GiB |
| GE11, clear profile | 68.08 | 9.22% | 20.79 ms | 73.5% | 14.52 GiB |
| Same, repeat | 68.31 | 8.91% | 19.81 ms | 73.8% | 14.52 GiB |
| Clear profile, app margin 3 ms experiment | 69.68 | 7.09% | 19.80 ms | 72.0% | 14.38 GiB |
| Same, repeat | 71.63 | 4.49% | 19.62 ms | 73.2% | 14.39 GiB |

The clear profile is retained because the user confirmed clearer cockpit text
and a less distracting boundary, with similar measured throughput to the
original visuals. It is not a demonstrated universal speedup. The uncapped
results varied substantially; a single 70.93 FPS window does not establish a
consistent pacing improvement. Performance power mode added no meaningful
benefit in these windows, so the system remains Balanced. One GE10 capture
does not establish a runtime ranking. GE11 remains the current launcher default
with GE10 preserved as a rollback option.

The margin experiment's first window included a 924 ms application-completion
gap, 69 consecutive reused latches, and wake-to-latch age p99 of 577 ms. Its
repeat had no comparable stall: mean wake-to-latch age was 18.80 ms versus
18.41 ms in the clear-profile repeat, and p99 was 35.26 ms versus 33.05 ms.
The user was also checking physical headset fit during this part of the session;
the cause of the stall was not isolated. The higher fresh-latch rate is promising,
but the experiment does not yet establish a consistently better setting.
The launcher retains the runtime's original 2 ms app margin. No immediate-return,
turbo, or compositor display-offset override was added.

Fresh-frame FPS counts changes in the application frame latched by Monado.
The compositor itself continues at 75 Hz; reused-frame percentage reports
how often it latches the same application image again. Head-pose reprojection
can still change the final view, and a latch is upstream of actual presentation.
Application completion
intervals describe rendered-frame cadence. Application work spans return from
xrWaitFrame through GPU completion and includes overlapping pipeline stages;
it must not be interpreted as GPU execution time or a standalone CPU limit.
Predicted deadline lateness measures Monado's pacing target, not display loss.
GPU, VRAM, GTT, power, and RAM counters cover the whole machine where relevant.

The analyzer also joins application frame IDs to compositor latches. It reports
GPU-completion-to-first-latch delay, wake-to-latch age including reused frames,
completed frames never latched in the recorded stream, and the longest run of
reused latches. These are pipeline observations, not motion-to-photon or
eye-to-photon latency. Frames at the end of the entire stream may be censored;
record past the capture window before closing the runtime.

## Applied clarity settings

The clear profile disables DCS upscaling, removes its additional FPS cap,
raises peripheral density from 0.20 to 0.30, and sets the focus region to 0.46
with edge smoothing 0.20. Focus density remains 1.00; TAA and existing sharpening
are retained. The layer's log confirms 764×764 peripheral and 1170×1170 focus
view recommendations. These are per-view sizes, not headset panel resolution.

The larger peripheral density reduces the sampling difference across the
boundary. The unmixed focus core is approximately
`0.46 × (1 − 2 × 0.20) = 0.276` of view width, versus the old `0.2688`.
Increasing smoothing to 0.25 with the same focus size would shrink that core
to 0.23, so that stronger setting was not used. This is a texture-coordinate
estimate; projected angular coverage depends on the view geometry.

`dcs-vr-quality` backs up both DCS and QuadViews settings before applying a
profile, preserves other settings, and refuses edits while DCS is running.
Restoration refuses to overwrite settings changed after that application.

```sh
dcs-vr-quality show
dcs-vr-quality apply clear
dcs-vr-quality apply balanced  # unbenchmarked FSR 0.85 fallback
dcs-vr-quality apply previous  # original FSR 0.72 visuals and cap 75
dcs-vr-quality restore /absolute/path/to/printed/backup
```

Backups stay in `~/.local/state/dcs-linux/quality/`. The previous preset restores
the named visual settings; restoring a backup restores both complete files.
The balanced fallback is available for heavier missions but has not been
benchmarked or judged in-headset in this pass.

## Research findings and next experiments

Independent GPT Sol 6.1 and Grok research passes examined the Linux runtime and
foveation path. Settings are accepted on local measurements and user feedback,
not provider agreement alone.

Grok 4.7 independently verified the alpha floor against local, tagged, and
current upstream shader text. It also recommended checking Bloom and Lens
Effects; both were already disabled in this setup. This source review supports
the feathering experiment, rather than another graphics-driver or runtime
replacement. Both research passes were read-only.

The installed [QVF projection shader](https://github.com/mbucchia/Quad-Views-Foveated/blob/79855a001302472a0f6c0703567dfe8ebfa6f988/openxr-api-layer/ProjectionPS.hlsl)
sets focus alpha to `isInside * max(0.5, s.x * s.y)`. Focus contribution therefore
jumps to at least one half at the rectangle boundary. More edge smoothing
cannot eliminate this floor. A continuous zero-to-one feather is a concrete
software experiment, but has not been deployed: it needs shader compilation,
GPU readback checks, and headset comparison. Preserve zero-smoothing behavior,
the sharp core, alpha handling, and format-aware sRGB decode/encode.

Pimax's transferable guidance is to adjust focus size, focus resolution, and
peripheral resolution independently and verify gaze calibration. Its vendor
runtime features do not establish availability in this Beyond/AMD/Monado stack.
[Pimax quad-view guide](https://store.pimax.com/blogs/blogs/quad-views-foveated-rendering-for-pimax-crystal),
[QVF configuration](https://github.com/mbucchia/Quad-Views-Foveated/wiki/Advanced-Configuration).

If readable text degrades specifically at extreme gaze angles, inspect QVF's
automatic focus-FOV widening: texture dimensions remain fixed while the view
angle grows. Disabling widening is an isolated experiment with possible
eye-overlap loss near the edges, not an applied fix. Gaze prediction should
follow capture/inference/transport/consumption timestamp measurements and
fixation checks. The [original latency study](https://research.nvidia.com/publication/2017-09_latency-requirements-foveated-rendering-virtual-reality)
does not provide a universal latency allowance for cockpit text.

Monado's app wake margin defaults to 2 ms. A 3 ms margin is tested separately
with the clear profile; more wake lead can trade queue age for delivery
reliability. Avoid immediate-return pacing overrides or compositor display
offset changes without a measured benefit and latency evidence.
[Installed app pacer source](https://gitlab.freedesktop.org/monado/monado/-/blob/9950e2a5f5751eb7960ce64f72545b189da6beb5/src/xrt/auxiliary/util/u_pacing_app.c).

Baballonia's active One Euro filter uses minimum cutoff 0.5 and speed coefficient
3. Raising the cutoff or disabling filtering is a future fixation/jitter A/B,
not an applied improvement. Grok estimated packet rate from an older log;
fresh packets, new inference samples, and timestamped end-to-end gaze latency
must be measured separately before treating that estimate as current tracking
performance. Simple field-of-view fraction calculations also do not establish
angular sharpness for nonlinear projections.

Eyelash contact and optical residue were reported after the clarity tuning.
Cleaning and face clearance are separate physical variables. Fit changes should
be followed by a gaze-alignment check before collecting another comparison.

## Repeating a capture

Monado supports metrics capture through `XRT_METRICS_FILE`:
[official documentation](https://monado.pages.freedesktop.org/monado/metrics.html).
Start a fresh launcher session with a unique metrics path:

```sh
XRT_METRICS_FILE=/absolute/path/run.protobuf DCS_WAYVR=0 dcs-linux monado
```

Load the same mission, unpause, warm the scene, and keep the view consistent.
Find the actual DCS process PID, then run:

```sh
python3 dcs-vr-benchmark.py capture --pid PID --seconds 60 \
  --label run-name --output capture.json
```

Close the game/runtime normally to flush the metrics stream. Obtain the
[official schema](https://gitlab.freedesktop.org/monado/utilities/metrics/-/blob/main/proto/monado_metrics.proto)
and compile a descriptor with `protoc`:

```sh
protoc --proto_path=/path/to/schema --descriptor_set_out=metrics-schema.pb \
  /path/to/schema/monado_metrics.proto
python3 dcs-vr-benchmark.py analyze --metrics run.protobuf \
  --schema metrics-schema.pb --capture capture.json --output result.json
```

Analysis requires Python's protobuf package. The tool selects the last
rendering session by default; use `--session` when launching another OpenXR
application after DCS. Headless readiness sessions do not emit render frames.
The decoder rejects truncated records and unsupported major schema versions.
Focused tests cover reused-frame counting, frame-ID age joins across capture
boundaries, discarded frames, truncated record lengths, and quality-profile
backup/restoration and protection of newer changes.

Private mission copies, original settings, raw metrics, samples, and runtime
logs stay under `~/Games/dcs-linux/benchmarks/20260929/`. The public repository
contains tooling and aggregate results only.
The temporary simulator hooks were removed from both Proton prefixes after
capture, so normal play no longer receives benchmark auto-unpause behavior.
