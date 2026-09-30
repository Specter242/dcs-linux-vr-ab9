# Continuous QuadViews feather experiment

The installed QVF 1.1.3 projection shader gives the focus image at least 50%
weight inside its rectangle, followed by zero outside. The user authorized
trying a continuous transition after the independent source reviews.

The experiment changes only that alpha floor from 0.5 to zero. It preserves
the original product of smoothstep ramps, sharp core, corners, zero-smoothing
branch, sampling, color handling, texture allocation, and shader instruction
count. The source equivalent is [qvf-continuous-feather.patch](qvf-continuous-feather.patch).
The rounded/minimum-axis mask suggested during research is a separate change
and is not included. Current focus size 0.46 and smoothing 0.20 are retained
for the first headset comparison.

## Exact supported layer

The patcher accepts only DLL SHA-256
`20673164cfd789d70b3cd6f7e2b49a1a0618e7fbb606e5e6787e2f66518ad482`.
It verifies the projection shader's original DXBC checksum and MAX instruction,
changes the immediate, and recomputes the DXBC-specific checksum. Ordinary
MD5 finalization cannot substitute for DXBC's final blocks. The DLL and
instruction sizes stay unchanged; no game texture or eye calibration is edited.
The altered DLL no longer verifies against its original publisher signature;
it is a local Wine experiment, and the signed original is preserved for restore.

Patched DLL SHA-256:
`975940770525d22cda2336fcc10dc8d4ea975e3960cfd3187de95f235794f82a`.
Other layer builds are rejected. The local source tree requires an unprepared
MSVC/Windows SDK build, so this experiment modifies the fingerprinted embedded
shader rather than claiming a full layer rebuild.

## GPU verification

`test_qvf_feather.cpp` loads the exact original and patched shader bytecode,
creates D3D11 pixel shaders, and renders 1024×64 offscreen images on the Radeon
through the same GE11/DXVK stack as DCS. It tests both shaders at smoothing
0, 0.08, and 0.20, with floating-point output and sRGB input/output: 12 cases.
Every output pixel and channel is compared with the analytic blend, including
outside the rectangle, borders, corners, and the center. The original shader
is a control which must reproduce the 50% floor.

All 12 cases passed. Maximum floating-point error was 0.00000024; maximum sRGB
normalized error was 0.00227, below one 8-bit code value. The probe emits the
SHA-256 of both tested shader inputs in its success receipt. Installation
requires that receipt to match the staged DLL's shader bytes.

Tests cover the opaque DCS composition path. They do not repair the upstream
transparent-layer flag issue or characterize gaze latency. GPU correctness
does not establish in-headset comfort or eliminate possible TAA registration
artifacts. DCS startup and user comparison follow installation.

The patched DLL was then verified in the live DCS process by its mapped file,
inode, and installed hash. The Apache cockpit rendered, the layer reported
quad views with smoothing 0.20 and sharpening 0.70, and direct gaze remained
fresh. WayVR was disabled for the comparison. These establish startup and
rendering compatibility. The user then compared the instruments and outside
view in the headset and reported a smoother boundary with text still sharp.
The experiment is retained at smoothing 0.20. No new throughput benchmark was
performed for this shader change; the earlier clarity-profile numbers should
not be attributed to it.

## Reproduce and restore

```sh
dcs-vr-feather prepare /absolute/path/to/original-layer.dll /absolute/path/stage
x86_64-w64-mingw32-g++ -std=c++17 -O2 -static test_qvf_feather.cpp \
  -o /absolute/path/stage/test_qvf_feather.exe \
  -ld3dcompiler_47 -ld3d11 -ldxgi -ladvapi32
```

Run the executable under the DCS Proton environment, with Windows paths to
`original.dxbc`, `continuous.dxbc`, and a writable `gpu-readback.log` as its
three arguments. The explicit report file is required because the GUI
executable launcher does not necessarily forward the child console.
Use the `FEATHER_PROBE_PASS` report rather than the wrapper's exit code alone.

Close DCS before installation or restoration:

```sh
dcs-vr-feather apply /absolute/path/stage /absolute/path/stage/gpu-readback.log
dcs-vr-feather restore /absolute/path/to/printed/backup
```

The tool backs up the original DLL, probe receipt, target path, and before/after
hashes under `~/.local/state/dcs-linux/feather/`. Restore refuses to overwrite
a newer or different layer. The legacy Proton prefix remains unchanged.

The focused Python checks cover MD5 compression across padding boundaries,
unknown-fingerprint rejection, the precise changed byte range, DXBC checksum,
probe receipt matching, backup, and restoration's newer-layer protection.

```sh
QVF_TEST_DLL=/absolute/path/to/original-layer.dll \
  python3 -m unittest test_vr_feather.py
```

Private DLL copies, bytecode, compiled probe, and raw reports remain in
`updates/20260929/feather-test/`. The repository contains source and results.
