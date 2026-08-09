# DCS World on Linux with Bigscreen Beyond 2e VR and MOZA AB9 force feedback

Tested on 9 August 2026. This is an independent community field report, not an
officially supported Eagle Dynamics, Bigscreen, MOZA, Valve, or Collabora
configuration.

## Short version

Yes: this combination can work on Linux.

On one CachyOS system we have run the standalone DCS World build through Proton
in a Bigscreen Beyond 2e at 75 Hz, with Lighthouse tracking, four-view foveated
rendering, eye-driven focus, VIRPIL/MFG controls, native DCS force feedback on a
MOZA AB9, automatic AB9 aircraft profiles, and supplementary effects generated
from DCS telemetry.

The last fully validated performance baseline was 74-76 delivered frames per
second in the AH-64D on Syria at 75 Hz, with approximately 85% GPU utilization.
That is a single-system observation, not a general benchmark. A later visual
quality increase is described below but has not yet been re-benchmarked.

This is not turnkey yet because several community components still require
manual installation and configuration. Eye-tracked foveation works end to end;
the Beyond 2e eye-tracking hardware/software used here is still beta and, like
other camera-based trackers, calibration quality can vary with headset fit and
the individual user. The AB9 path works, but its profile/configuration
integration is community-built and must be treated as safety-critical software
around a 12 N-m motor.

## Tested hardware and software

- CachyOS, KDE Plasma on Wayland
- Linux `7.1.6-1-cachyos`
- Ryzen 7 9800X3D, 64 GB RAM
- Radeon RX 9070 XT 16 GB with Mesa RADV 26.1.6
- Bigscreen Beyond 2e, 75 Hz
- SteamVR Lighthouse tracking
- MOZA AB9 FFB base (`346e:1000`)
- VIRPIL CM3 throttle and Rotor TCS Plus collective
- MFG Crosswind pedals
- Standalone DCS World `2.9.28.26385`
- `umu-launcher` 1.4.3 with GE-Proton10-17
- Monado `25.1.0.r710.g735e29e4e`
- Quad-Views-Foveated 1.1.3
- A locally patched MinGW build of OpenXR-Eye-Trackers 1.3.0
- Baballonia 1.1.1 built from commit
  `63ea0c21effd5bd717d150c59d6fd7d0b548af2f`
- go-bsb-cams 1.0.2

The DCS installation lives on a dedicated ext4 NVMe volume. The Proton prefix,
settings, logs, and caches live on the Linux system drive. Moving the game from
the old Windows/NTFS setup to native ext4 materially improved asset loading and
removed one large variable from the investigation.

## What works, and how well

| Component | Status | Notes |
| --- | --- | --- |
| Standalone DCS under Proton | Working | Install, update, repair, 2D, VR, single-player, and multiplayer tested. |
| Beyond 2e display | Working | Monado direct mode at 5088x2544 combined, 75 Hz. |
| Lighthouse pose tracking | Working | Monado uses SteamVR's Lighthouse wrapper. |
| Quad views | Working | DCS reports four views and the layer reports large pixel savings. |
| Beyond 2e eye cameras | Working | go-bsb-cams supplies an 800x400 MJPEG stream. |
| Eye-driven focus | Working | End-to-end gaze reaches Quad-Views-Foveated. Calibration remains sensitive to headset fit on the tested beta hardware. |
| VIRPIL and MFG input | Working | Use Proton HIDRAW for these devices. |
| AB9 axes/buttons | Working | Exposed through Linux input/evdev. |
| Native DCS AB9 FFB | Working | Standard DirectInput effects travel through Wine/evdev and `hid-universal-pidff`. |
| Automatic AB9 profiles | Working | DCS aircraft ID selects a copied MOZA preset and applies its persistent base settings. |
| AB9 telemetry effects | Working prototype | Engine/runway/buffet/motion/weapon and helicopter effects are synthesized with a hard 15% cap. |

## The architecture

There are two independent AB9 force paths. Keeping them separate was the key
to getting sensible forces instead of replacing DCS's own trim/centering:

```text
DCS DirectInput FFB
  -> Wine evdev backend
  -> Linux FF_* API
  -> hid-universal-pidff
  -> AB9

DCS Export.lua telemetry (localhost UDP)
  -> bounded Linux effect mixer
  -> Linux FF_PERIODIC / FF_SPRING / FF_DAMPER
  -> hid-universal-pidff
  -> AB9

DCS aircraft identifier (localhost UDP)
  -> profile selector
  -> documented MOZA serial commands
  -> persistent AB9 base settings
```

The VR/eye path is:

```text
DCS (DX11/OpenXR)
  -> Proton / wineopenxr
  -> Monado direct compositor
  -> Beyond 2e display

SteamVR Lighthouse driver
  -> Monado steamvr_lh wrapper
  -> headset pose

Beyond 2e eye cameras
  -> go-bsb-cams MJPEG stream
  -> Baballonia + trained ONNX model
  -> small OSC conversion bridge
  -> locally built OpenXR-Eye-Trackers API layer
  -> XR_EXT_eye_gaze_interaction
  -> Quad-Views-Foveated
  -> DCS focus views
```

## 1. DCS and Proton

The standalone Windows installation is launched with `umu-run`. The essential
shape is:

```bash
export GAMEID="umu-223750"
export STORE="none"
export WINEPREFIX="$HOME/Games/dcs-linux/prefix"
export PROTONPATH="$HOME/.local/share/Steam/compatibilitytools.d/GE-Proton10-17"
export PROTON_USE_XALIA="0"
export WINE_SIMULATE_WRITECOPY="1"
export WINEDLLOVERRIDES="wbemprox=n"
export PRESSURE_VESSEL_IMPORT_OPENXR_1_RUNTIMES="1"

cd "/path/to/DCS World/bin"
umu-run ./DCS.exe --no-launcher --force_enable_VR --force_OpenXR
```

The same prefix must be used for the installer, updater, and game. For a
standalone installation on another volume, map that mount into the prefix as a
Wine drive and keep the volume on a Linux-native filesystem.

Useful cache settings on this machine are:

```bash
export MESA_SHADER_CACHE_DIR="$HOME/Games/dcs-linux/cache/mesa"
export MESA_SHADER_CACHE_MAX_SIZE="10G"
export DXVK_STATE_CACHE_PATH="$HOME/Games/dcs-linux/cache/dxvk"
```

Relevant upstream references:

- [umu-launcher](https://github.com/Open-Wine-Components/umu-launcher)
- [GE-Proton](https://github.com/GloriousEggroll/proton-ge-custom)
- [VR on Linux DCS guide](https://wiki.vronlinux.org/docs/games/dcs-world/)

## 2. Monado and the Beyond 2e

Monado is selected only for the DCS process and restored afterward. Do not
leave a stale global runtime selection after a crash or hard reset.

The working Monado environment is:

```bash
export STEAMVR_LH_ENABLE=true
export LH_DRIVER=steamvr
export XRT_COMPOSITOR_SCALE_PERCENTAGE=100
export XRT_COMPOSITOR_COMPUTE=1
export U_PACING_APP_USE_MIN_FRAME_PERIOD=1
export XRT_DEBUG_GUI=0
export XRT_CURATED_GUI=0
monado-service
```

Wait for both the Monado IPC socket and the compositor/direct-display
initialization before launching DCS. The IPC socket appears before Vulkan
initialization has necessarily completed; a three-second stability wait fixed
a race on this system.

This desktop also has two 3440x1440 monitors, one normally at 175 Hz. Starting
Monado while that display was at full refresh occasionally produced zero
Vulkan surface formats, killed Monado, and made DCS fall back to 2D. The local
launcher temporarily sets that monitor to 120 Hz, waits three seconds for the
DisplayPort link to retrain, starts Monado, and restores the old mode when DCS
exits. This is a hardware-specific workaround, not a universal requirement.

The active host OpenXR runtime is a symlink to
`/usr/share/openxr/1/openxr_monado.json` for the duration of the launch.

References:

- [Monado project](https://monado.freedesktop.org/)
- [Monado OpenXR runtime overview](https://monado.freedesktop.org/getting-started.html)
- [Monado's SteamVR Lighthouse wrapper](https://monado.freedesktop.org/)

## 3. Quad-view foveated rendering

[Quad-Views-Foveated](https://github.com/mbucchia/Quad-Views-Foveated) is
installed inside the Wine prefix as an implicit 64-bit OpenXR API layer.

The fully tested performance-oriented baseline was:

```ini
peripheral_multiplier=0.22
focus_multiplier=0.90
horizontal_focus_section=0.38
vertical_focus_section=0.38
horizontal_focus_offset=0.0
vertical_focus_offset=0.0
smoothen_focus_view_edges=0.18
sharpen_focus_view=0.7
turbo_mode=0
horizontal_fixed_section=0.5
vertical_fixed_section=0.45
```

At a 2544x2544 stereo recommendation, the layer reported 560x560 peripheral
views and 870x870 focus views: 2.14 million quad-view pixels versus 12.94
million stereo pixels, or an 83.5% reduction before the application's own
upscaling.

The current quality candidate is `focus_multiplier=0.95` with 40% horizontal
and vertical focus sections, producing approximately 967x967 focus views. It
has not yet been re-benchmarked in the same mission, so do not treat it as the
validated baseline.

Do not use `horizontal_focus_offset` to correct a whole-image gaze bias. Quad
Views applies that value in opposite directions for the two eyes; it changes
vergence rather than supplying a global yaw correction.

## 4. Beyond 2e eye tracking on Linux

The upstream pieces are:

- [go-bsb-cams](https://github.com/LilliaElaine/go-bsb-cams), which exposes the
  headset cameras at `http://127.0.0.1:8080/stream`
- [Baballonia](https://github.com/Project-Babble/Baballonia), whose own support
  table lists the Beyond 2e on Linux through go-bsb-cams
- [OpenXR-Eye-Trackers](https://github.com/mbucchia/_ARCHIVE_OpenXR-Eye-Trackers),
  which is archived/deprecated upstream but contains the VRChat OSC tracker
  path used for this Monado configuration

Baballonia was configured with two 399x399 regions from the combined 800x400
MJPEG stream. It sends four generic OSC floats to localhost port 8888:

```text
/LeftEyeX
/LeftEyeY
/RightEyeX
/RightEyeY
```

A small bridge combines those into the message expected by the OpenXR layer on
UDP port 9020:

```text
/tracking/eye/LeftRightPitchYaw ,ffff
```

The bridge converts normalized Baballonia output to degrees with a 45-degree
scale and a vertical sign inversion. The left/right X sources had to be crossed
for this camera ordering. Treat that crossover as headset/configuration
specific and verify it rather than copying blindly.

The archived OpenXR-Eye-Trackers project did not build cleanly for this Wine
case. We produced a lean MinGW 64-bit build that:

- compiles out ETW/TraceLogging and unused vendor SDKs;
- uses portable `snprintf`/`vsnprintf` calls;
- selects the VRChat OSC tracker for the Monado/Beyond runtime;
- adds packet/accepted-gaze logging for diagnosis.

The resulting layer successfully advertises `XR_EXT_eye_gaze_interaction` to
Quad-Views-Foveated under Wine. This local build needs to be published as a
source patch or upstreamed; distributing only the DLL would make the result
hard to audit and reproduce.

### Fit and calibration note

The tested Beyond 2e eye-tracking implementation is beta hardware/software.
Tracking worked, but calibration could shift when headset fit changed. On this
particular user, eyelashes sometimes obscured the pupils in the raw IR views;
that is a personal fit/physiology observation rather than a Linux pipeline
failure. Calibrate with the headset in its normal playing position and reserve
the bridge's global yaw offset for small, stable corrections.

## 5. VIRPIL/MFG input versus AB9 force feedback

Proton HIDRAW works well for the high-button-count VIRPIL devices and MFG
pedals. The AB9 is different: it must remain on Wine's evdev joystick path for
Linux force-feedback effects to reach it.

The working launcher enables HIDRAW only for the non-FFB controllers:

```bash
# Example only: substitute the VID/PID pairs reported by your own devices.
export PROTON_ENABLE_HIDRAW="0x3344/0x8194,0x3344/0x832E,0x16D0/0x0A38"

# Intentionally DO NOT include the AB9 (0x346e/0x1000).
```

When the AB9 was put on Proton's HIDRAW backend, its inputs worked but DCS did
not open the Linux event device and produced no native flight forces.

DCS can also regenerate DirectInput instance GUIDs under Wine. Because those
GUIDs are embedded in input-profile filenames, keep canonical copies of
important `.diff.lua` files and re-associate them with the device GUIDs logged
by each launch. This solved apparently lost Apache pilot/CPG bindings without
changing the bindings themselves.

## 6. Native AB9 force feedback

Modern kernels contain `hid-universal-pidff`, which extends the generic HID PID
force-feedback driver and includes compatibility work needed by many MOZA-class
devices. The AB9 did not bind automatically on this system, so we registered
its dynamic HID ID and added a boot/hot-plug fallback.

The important identifiers are:

```text
USB VID:PID       346e:1000
HID dynamic ID    0003 0000346E 00001000
driver            hid-universal-pidff
```

A minimal registration sequence is:

```bash
sudo modprobe hid-universal-pidff
printf '%s\n' '0003 0000346E 00001000' |
  sudo tee /sys/bus/hid/drivers/hid-universal-pidff/new_id
```

For persistence, a udev rule registers the ID when USB device `346e:1000`
appears, and a oneshot systemd service rebinds an already-enumerated AB9 from
`hid-generic` if boot ordering loses the race.

Verify the result with:

```bash
for device in /sys/bus/hid/devices/0003:346E:1000.*; do
  printf '%s -> %s\n' "$device" "$(readlink -f "$device/driver")"
done
```

The driver and Wine path were not the original reason this base produced no
force. USB monitoring showed correct PID effect creation/start requests and
successful device acknowledgements. The base's own PID state reported that
actuators were suppressed. MOZA Cockpit logs from the previous Windows install
also showed an APP error and zero motor torque, proving that fault predated the
Linux work.

The actual recovery was completing MOZA Cockpit's official **Base Restore and
Reset** process. The first reset failed at 98% because the seat/rig obstructed
full travel. After clearing the mechanical interference, the reset reached
100%, APP became free, calibration completed, motor state became normal, and
standard Linux PID effects physically moved the stick.

Known-good persistent state:

```text
APP state                    1 (free)
Control mode                 1 (DirectInput)
Working mode                 0 (normal)
Force-output disable gate    0 (force allowed)
FFB controller               1 (enabled)
Automatic calibration        2 (complete)
Motor state                  2 (normal)
```

This distinction is worth emphasizing: if the kernel successfully uploads and
starts effects but the stick remains completely limp, inspect firmware state
before endlessly replacing Linux drivers.

See [PUBLIC_AB9_PROTOCOL.md](PUBLIC_AB9_PROTOCOL.md) for the protocol findings,
profile system, telemetry mixer, and safety rules.

## 7. MOZA aircraft profiles and telemetry effects

MOZA Cockpit stores aircraft profiles as JSON `.preset` files. They contain
two different categories:

1. persistent device/base settings;
2. host-side telemetry-effect parameters.

We copied 39 official DCS/AB9 profiles and five user-created profiles from the
owner's existing MOZA Cockpit installation. Those files are not included in a
public bundle; users should import profiles from their own licensed Cockpit
installation.

The Linux tooling maps and verifies 56 persistent AB9 settings. In `native`
mode it deliberately forces DirectInput control and game FFB gain 100 while
retaining the preset's other base tuning. Exact MOZA profiles often request
Composite mode and may set game FFB to zero because Cockpit expects its own
Windows telemetry host to create the forces. Copying those values literally
suppressed native Linux PID effects.

DCS `Export.lua` sends the aircraft name to UDP 34399. A user service selects
the newest matching custom profile or the official fallback and safely applies
only changed values over the AB9 serial interface.

The same exporter sends flight data to UDP 34400 at no more than 50 Hz. A
second daemon converts copied profile parameters into bounded Linux effects:

- engine/rotor rumble;
- runway/gear buffet;
- angle-of-attack buffet;
- acceleration/motion cues;
- gun, weapon-release, flare, and chaff pulses;
- helicopter rotor, blade-slap, ETL, and VRS cues;
- optional fixed-wing dynamic spring/damper augmentation.

Native DCS force feedback remains responsible for core control loading and trim.
The supplementary daemon has a 0.5-second telemetry timeout and erases all
effects when data stops. On this system it runs with a hard 15% per-effect
magnitude cap.

Both automatic hardware writers require explicit arm files containing exact
warning phrases. Dry-run is the default behavior without those files.

## 8. Performance settings and results

The smooth tested baseline used:

- 75 Hz headset refresh;
- DCS frame cap 75;
- DCS pixel density 1.0;
- FSR at 0.66;
- TAA;
- medium object/cockpit textures;
- low terrain textures;
- 2x anisotropic filtering;
- medium visibility range;
- LOD multiplier 0.6;
- conservative forest, shadow, cloud, SSAO, SSLR, and cockpit-GI settings;
- the 0.90/38% quad-view configuration above.

The next quality step, applied but not yet matched against the same benchmark,
is:

- high cockpit/object textures;
- high terrain textures;
- 16x anisotropic filtering;
- 100 km preload radius;
- 0.95/40% quad-view focus configuration.

The texture, anisotropy, and preload changes primarily spend VRAM/RAM rather
than DCS simulation-thread time. Visibility range, object LOD, forests,
scenery detail, shadows, and clouds were intentionally left conservative until
per-thread CPU and GPU frame times can be captured in a busy multiplayer Syria
mission.

Use the desktop's performance power profile for a dedicated gaming machine.
Do not interpret low aggregate CPU utilization as CPU headroom: DCS can be
limited by one render/simulation thread while most of a 16-thread processor is
idle.

## 9. Troubleshooting findings

### DCS starts in 2D after a VR reboot or crash

Check that Monado survived startup, its IPC socket exists, and the active
OpenXR runtime points where expected. A stale Monado runtime symlink does not
prove Monado is actually running. On this multi-monitor system, waiting for a
DisplayPort mode change before starting Monado was essential.

### The AB9 has inputs but no force

Check all three layers:

1. Is the HID device bound to `hid-universal-pidff`?
2. Did Wine open the evdev event device rather than forcing the AB9 through
   HIDRAW?
3. Is the base firmware in APP-free/DirectInput/force-enabled state?

If effect upload and start packets are acknowledged but there is still no
movement, the third layer is the likely culprit.

### Apache is limp while cold and dark

That can be correct helicopter behavior. Judge force feedback after the
aircraft systems and hydraulic/control logic are in an appropriate state, not
only while the aircraft is powered off.

### Quad views works but the sharp region is misplaced

Confirm live packets in both the eye bridge log and OpenXR-Eye-Trackers log.
If the data path is healthy, recalibrate with the headset in its normal playing
position. A stable numerical bias can be corrected with the bridge offset;
changing bias is more likely fit or beta-tracker calibration behavior.

### Apache MPD text/symbols render as solid colored blocks

In this investigation that was self-inflicted by an experimental modification
to seven Apache MPD font/indication texture atlases. It was not a Mesa VRAM
driver failure. Restoring the exact original files for the installed DCS build
fixed the integrity-check failures and is the correct remedy. Use DCS repair or
a known-matching backup; do not transplant files from another DCS version.

### DCS assets load extremely slowly or incompletely

Avoid running a Proton prefix or large mutable DCS installation from NTFS when
a native filesystem is available. A dedicated ext4 NVMe volume, larger shader
cache, and 100 km preload radius are the current setup.

### Kernel reports a split/bus-lock storm

This CachyOS machine suffered a hard hang accompanied by split/bus-lock
handling. `split_lock_detect=off` stopped the repeated events here. This is not
a generic DCS requirement: it disables kernel detection/mitigation behavior and
should be considered only after confirming the same messages in the journal.
Read the [Linux kernel bus-lock documentation](https://docs.kernel.org/arch/x86/buslock.html)
before changing it.

## Safety

The AB9 can move suddenly and produce 12 N-m. Before any test, reset, profile
write, or telemetry-effect enablement:

- keep people, cables, the chair, and the rig out of the stick's full travel;
- remove hands unless a test explicitly requires a light resting contact;
- use bounded effects first;
- make motor output opt-in rather than automatic after installation;
- clear effects on telemetry timeout and process termination;
- never interrupt power or USB during a firmware/reset operation.

Never blindly scan write-capable MOZA serial commands. In particular, group 32
subcommand 15 redirected/silenced the serial channel until a physical power
cycle during this investigation.

## What should be upstreamed or packaged next

1. A reproducible source branch/patch for the MinGW OpenXR-Eye-Trackers build.
2. A generic installer for the AB9 udev rule, setup service, safe CLI, profile
   daemon, telemetry daemon, and DCS exporter.
3. Automated tests for packet parsing, profile mapping, timeouts, and effect
   caps that do not require a motor.
4. Broader Beyond 2e beta eye-tracking testing across different users and
   headset fits.
5. More community testing across kernels, GPUs, DCS modules, AB9 firmware
   versions, and alternate MOZA flight bases.

## Upstream projects and prior work

- [DCS World Linux VR guide](https://wiki.vronlinux.org/docs/games/dcs-world/)
- [umu-launcher](https://github.com/Open-Wine-Components/umu-launcher)
- [GE-Proton](https://github.com/GloriousEggroll/proton-ge-custom)
- [Monado](https://gitlab.freedesktop.org/monado/monado)
- [Quad-Views-Foveated](https://github.com/mbucchia/Quad-Views-Foveated)
- [OpenXR-Eye-Trackers archive](https://github.com/mbucchia/_ARCHIVE_OpenXR-Eye-Trackers)
- [Baballonia](https://github.com/Project-Babble/Baballonia)
- [go-bsb-cams](https://github.com/LilliaElaine/go-bsb-cams)
- [universal-pidff](https://github.com/JacKeTUs/universal-pidff)
- [Linux steering-wheel compatibility notes](https://github.com/JacKeTUs/linux-steering-wheels)

Thanks to those projects and their maintainers. This work connects and debugs
their components; it does not replace them.
