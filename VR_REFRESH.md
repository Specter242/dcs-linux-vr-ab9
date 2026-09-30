# VR refresh — September 29, 2026

This refresh preserves the previous Proton runtime and prefix, the personalized
eye model, flight bindings, graphics settings, and original DCS textures.
The August performance numbers in the main guide are historical; they have
not been remeasured on this software combination.

## Software and architecture

| Component | Current setup |
| --- | --- |
| DCS World | 2.9.29.27468, updated from 2.9.28.26385 |
| Proton | GE-Proton11-7, isolated `prefix-ge11` |
| DXVK | Exact GE11-7 submodule revision `601930949d111edbbcf9dd463948426d9f8f6ddd`, with the Apache alpha repair |
| UMU | Private 1.4.4 installation; system package remains independently managed |
| Monado | 25.1.0.r863.g9950e2a5f; already matched upstream at inspection |
| WayVR | 26.8.0, native OpenXR overlay with KDE/PipeWire desktop capture |
| Eye cameras | go-bsb-cams 1.0.2 |
| Eye inference | Baballonia source `5dbd332bc7f94868ec9b11a1b9ebd3c28acb91be` with the accompanying patch |
| Windows eye layers | Existing OpenXR-Eye-Trackers 1.3.0 custom build and Quad-Views-Foveated 1.1.3 |

The gaze path now goes directly from Baballonia to OSC UDP 9020, then through
the Windows eye layer into quad views. The old Python OSC conversion bridge
is preserved locally for manual recovery but is not part of the default path.
Monado still uses SteamVR's Lighthouse driver without the full SteamVR compositor.

The launcher owns the Monado/game session, temporary runtime selection, and
display refresh workaround. User systemd units own camera capture, Baballonia,
and WayVR. The eye helper requires recent inferred gaze, not just live processes.
The launcher first checks a live headset pose through a native headless OpenXR
session. It waits up to 30 seconds for both position and orientation to become
valid, then reports missing Lighthouse tracking and cleans up if they do not.
Stopping the camera stops its dependent tracker; launcher shutdown cleans up
workers it started and restores the previous runtime/display mode.

Baballonia's patch fixes connected-UDP receiver startup/restart failures,
projects named temporal-model outputs onto the six values required by the eye
filter, handles both eyes closing without division by zero, rejects nonfinite
eye output, and writes an atomic gaze health record. It also fixes capture
module placement when publishing to a custom output directory. The existing
frame-disposal and diagnostic changes are included. Personal model files and
settings are excluded from this repository.
The desktop app also closes its UI lifetime on systemd's stop signal; live
start/stop verification now reports a clean exit instead of a stop timeout.

## Commands

```sh
dcs-linux monado                         # updated runtime, direct VR
dcs-eye-tracking status --json           # service and fresh-gaze status
dcs-wayvr                               # start or toggle desktop overlay
DCS_WAYVR=0 dcs-linux monado             # start without WayVR
DCS_RUNTIME=legacy dcs-linux monado      # previous GE10/prefix, current eye services
```

The usual DCS World VR application entry selects the updated runtime. Local
application entries also expose the previous Proton runtime and WayVR toggle.
WayVR uses the main monitor's KDE sharing permission; its restore token stays
in the user's private config. HMD pointer mode supports use without VR controllers.
The DCS launcher starts WayVR hidden so the desktop does not cover the game.
Use `dcs-wayvr` or the WayVR application entry to show or hide it when needed.

`DCS_PREFIX_PATH` and `DCS_PROTON_PATH` override the selected game environment.
Input synchronization follows that prefix and migrates saved bindings to the
current DirectInput GUIDs. GE11 manages `S:` itself, so the texture repair uses
the stable `W:` mapping to `/mnt`:

```sh
export DCS_MFD_ROOT='W:\dcs\Program Files\Eagle Dynamics\DCS World'
```

Automatic dismissal of the authorization notice is off by default; opt in with
`DCS_DISMISS_AUTHORIZATION_NOTICE=1` if wanted.

For diagnostic stereo VR without the Windows eye/quad layers, use:

```sh
DCS_REQUIRE_TRACKING=0 DCS_WAYVR=0 \
DISABLE_XR_APILAYER_MBUCCHIA_quad_views_foveated=1 \
DISABLE_XR_APILAYER_MBUCCHIA_eye_trackers=1 dcs-linux monado
```

This can display the menu without a valid pose, but full Lighthouse tracking
still needs to be restored before flying. Normal eye-tracked quad views keep
the readiness check enabled.

## Rebuild and deployment

Apply `dcs-dxvk-ge11.patch` to the DXVK revision above. Initialize its submodules,
then build both matched 64-bit DLLs:

```sh
meson setup build.w64 --cross-file build-win64.txt --buildtype release \
  -Denable_d3d8=false -Denable_d3d9=false -Denable_d3d10=false
ninja -C build.w64 src/d3d11/d3d11.dll src/dxgi/dxgi.dll
```

Install both DLLs in the dedicated runtime's
`files/lib/wine/dxvk/x86_64-windows/`, preserving the stock originals.
Apply `baballonia-dcs.patch` to the Baballonia revision above, initialize the
HyperText.Avalonia submodule, and publish using .NET 10:

```sh
dotnet publish src/Baballonia.Desktop/Baballonia.Desktop.csproj \
  -c Release -r linux-x64 --self-contained true \
  -o "$HOME/.local/share/dcs-linux/eye-tracking/baballonia-20260929"
```

The existing calibration/trainer and firmware assets were copied alongside
the publish output. Launch scripts belong in `~/.local/bin`; the four session
units belong in `~/.config/systemd/user`. Run `systemctl --user daemon-reload`.
These units are started on demand, not enabled as login services. The UMU
wrapper expects the official 1.4.4 zipapp under
`~/.local/share/dcs-linux/umu-1.4.4/umu/` and the WayVR unit expects the extracted
26.8.0 AppImage under `~/.local/share/wayvr/squashfs-root/`.
Build the native readiness helper against the system OpenXR loader and headers:

```sh
cc -std=c11 -D_POSIX_C_SOURCE=200809L -O2 -Wall -Wextra \
  dcs-xr-readiness.c -lopenxr_loader -o "$HOME/.local/bin/dcs-xr-readiness"
```

## Verification and remaining checks

- Release downloads were checked against their official GitHub SHA-256 asset digests.
- DCS updater completed successfully and reported the new version.
- All ten relevant Apache atlas files retained their pre-update SHA-256 hashes;
  the opaque-upload fingerprints still match the repair table.
- A D3D11 GPU readback verified repaired alpha across four mip levels and an
  unchanged unknown texture. The stock DLL control failed as expected.
- Baballonia built and produced 210 finite gaze packets in 12 seconds.
  Stopping camera capture stopped the dependent tracker and invalidated readiness.
- DCS accepted direct gaze, detected all four flight controls including native
  AB9 force-feedback capability, and created four OpenXR view swapchains.
- DCS reached its desktop main menu on the updated game and Proton runtime.
- Basic stereo VR also reached the menu with the Windows eye/quad layers disabled.
- The native readiness check detected invalid headset pose flags and exited
  before starting DCS, restoring the previous runtime and display mode.
- Monado acquired the Beyond's DP-2 display lease at combined 5088×2544.
- WayVR connected to Monado and captured DP-3 through PipeWire.
- Gaze freshness boundary and corrupt-record tests, shell syntax checks, and
  systemd unit validation passed.

After the base stations were powered on and the Beyond was placed in view,
the native readiness check passed with valid position and orientation. Full
eye-tracked quad views reached the VR main menu on the updated runtime;
DCS entered the focused OpenXR session state and the Windows eye layer
accepted fresh direct gaze. WayVR continued capturing the desktop. After the
user power-cycled the AB9 and let calibration finish, it reported APP state 1
(free), normal mode, DirectInput, and enabled force output. The launcher's
native force-feedback readiness check passed.

The user confirmed head tracking and gaze-following clarity in the headset.
The Apache runway mission rendered with full quad views; the subsequent native
clarity profile improved cockpit text and the boundary according to the user.
[Benchmark results and tuning](VR_BENCHMARK.md) distinguish fresh compositor
content from application FPS. Busy flight and physical force feedback remain
untested in this refresh. Initial quad-view startups paused
without a valid headset pose. Source inspection identified the layer's
indefinite wait for valid views in
[cacheStereoView](https://github.com/mbucchia/Quad-Views-Foveated/blob/79855a001302472a0f6c0703567dfe8ebfa6f988/openxr-api-layer/layer.cpp#L2750).
The native readiness check addresses that startup hang without changing the
quad-view DLL; both its missing-pose cleanup and successful-pose paths have
now been verified.
One diagnostic query for the session state of a non-session libmonado client
triggered a Monado crash; session queries for control clients were discontinued.
The launcher stopped DCS, restored the global runtime symlink and monitor
refresh, and left no camera/overlay workers running after that failure.

Private backups, downloaded assets, verification reports, and build/startup
logs are under `~/Games/dcs-linux/updates/20260929/`. The original GE10 runtime
and original prefix remain available; the prefix snapshot and original eye
settings are in that directory's `backups/`. Restoring those settings should
be done with both eye stacks stopped. A runtime rollback does not roll back
the shared DCS installation; its updater can select a specific game version.
