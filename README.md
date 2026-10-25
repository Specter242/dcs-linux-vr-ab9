# DCS Linux VR + MOZA AB9

This repository is a tested field report and an early community implementation
for running standalone DCS World on Linux with:

- a Bigscreen Beyond 2e through Monado;
- Lighthouse tracking;
- eye-tracked quad-view foveated rendering;
- VIRPIL and MFG controllers through Proton HIDRAW;
- native DCS force feedback on a MOZA AB9 through Wine evdev and
  `hid-universal-pidff`;
- automatic per-aircraft AB9 base profiles; and
- bounded supplementary effects generated from DCS telemetry.

The tested machine reached a fully validated 74-76 delivered FPS in an AH-64D
on Syria at the Beyond's 75 Hz mode. This is one CachyOS/Radeon system, not a
universal benchmark or an officially supported configuration.

## Tested system

| Component | Tested configuration |
| --- | --- |
| CPU | AMD Ryzen 7 9800X3D |
| GPU | AMD Radeon RX 9070 XT, 16 GB VRAM |
| Memory | 64 GB RAM |
| Operating system | CachyOS, KDE Plasma on Wayland |
| Kernel | Linux `7.1.6-1-cachyos` |
| Graphics driver | Mesa RADV 26.1.6 |
| Headset | Bigscreen Beyond 2e at 75 Hz |
| DCS | Standalone DCS World `2.9.28.26385` |
| Compatibility layer | `umu-launcher` 1.4.3 with GE-Proton10-17 |
| OpenXR runtime | Monado `25.1.0.r710.g735e29e4e` |
| Storage | DCS on a dedicated ext4 NVMe volume |

The 74-76 FPS result used the performance-oriented settings documented in the
[field guide](PUBLIC_GUIDE.md#8-performance-settings-and-results), including
FSR 0.66 and the 0.90/38% quad-view configuration. Later visual-quality changes
have not yet been benchmarked against the same mission.

Start with the [complete field guide](PUBLIC_GUIDE.md). The
[AB9 protocol and safety notes](PUBLIC_AB9_PROTOCOL.md) cover the force-feedback
work in detail.

## Repository status

This is an alpha research release intended to make the result auditable and to
invite testing and contributions. It is not a one-click installer. In
particular:

- the archived OpenXR-Eye-Trackers MinGW patch needs reproducible packaging;
- telemetry curves need validation across more DCS aircraft and firmware;
- MOZA aircraft preset files are intentionally not redistributed.

## Source layout

| File | Purpose |
| --- | --- |
| `ab9_control.py` | Read AB9 status and safely enforce native DirectInput FFB state. |
| `ab9_profile.py` | Read and apply the persistent subset of a user's MOZA aircraft presets. |
| `ab9_profiled.py` | Select a preset automatically from the DCS aircraft identifier. |
| `ab9_telemetryd.py` | Mix bounded supplementary effects from DCS telemetry. |
| `AB9ProfileExport.lua` | Export aircraft identity and telemetry from DCS over localhost UDP. |
| `dcs-eye-osc-bridge.py` | Convert Baballonia eye values to OpenXR-Eye-Trackers OSC. |
| `ab9-ffb-setup` | Bind the AB9 to `hid-universal-pidff`. |
| `ab9_test.c` / `ab9_spring_test.c` | Explicitly armed physical FFB probes. |
| `ab9_state.c` | Read HID PID state and pool reports. |

## Quick verification

Install Python's `evdev` package using your distribution or a virtual
environment, then run the motor-safe tests:

```bash
python -m unittest -v test_ab9_telemetry.py test_ab9_profiles.py
python -m py_compile \
  moza.py ab9_control.py ab9_profile.py ab9_profiled.py ab9_telemetryd.py

cc -std=c11 -Wall -Wextra -Werror -O2 -o ab9_test ab9_test.c
cc -std=c11 -Wall -Wextra -Werror -O2 -o ab9_spring_test ab9_spring_test.c
cc -std=c11 -Wall -Wextra -Werror -O2 -o ab9_state ab9_state.c
```

The Python tests use synthetic presets, scripted UDP input, and mocked serial
calls; they do not open the AB9. The C motor probes refuse to run unless their
`--move-stick` danger acknowledgment is present.

## Minimal AB9 install outline

These commands are examples; inspect every file first and adapt paths and input
device permissions for your distribution:

```bash
sudo install -Dm755 ab9-ffb-setup /usr/local/libexec/ab9-ffb-setup
sudo install -Dm644 99-moza-ab9-ffb.rules /etc/udev/rules.d/99-moza-ab9-ffb.rules
sudo install -Dm644 ab9-ffb-setup.service /etc/systemd/system/ab9-ffb-setup.service
sudo systemctl daemon-reload
sudo systemctl enable --now ab9-ffb-setup.service

install -d "$HOME/.local/libexec/ab9-ffb" "$HOME/.config/systemd/user"
install -m755 moza.py ab9_control.py ab9_profile.py ab9_profiled.py \
  ab9_telemetryd.py "$HOME/.local/libexec/ab9-ffb/"
install -m644 ab9-profiled.service ab9-telemetryd.service \
  "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
```

Users normally need membership in their distribution's input and serial-device
groups. Log out and back in after changing group membership.

Copy AB9 DCS profiles from your own MOZA Cockpit installation into:

```text
~/.local/share/ab9-ffb/profiles/official/
~/.local/share/ab9-ffb/profiles/custom/
```

Install `AB9ProfileExport.lua` below DCS's Saved Games `Scripts` directory and
load it from the existing `Export.lua` without replacing other exporters:

```lua
dofile(lfs.writedir() .. [[Scripts\AB9ProfileExport.lua]])
```

### Check profiles and telemetry first

These commands do not write to the base. The aircraft identifier below is an
example; choose one for which you have copied a preset:

```bash
python3 "$HOME/.local/libexec/ab9-ffb/ab9_profile.py" list
python3 "$HOME/.local/libexec/ab9-ffb/ab9_profiled.py" --once 'Mi-8MT'
python3 "$HOME/.local/libexec/ab9-ffb/ab9_telemetryd.py" --cap-percent 15
```

Do not run the foreground listener and the telemetry service at the same time.
Enter an unpaused mission and look for `motor_output=DRY-RUN`, a matching
`Telemetry profile:` line, and `DRY effects:` output. Stop the listener with
Ctrl+C before starting its service.

### Enable output deliberately

**The following enables motor output. Clear the stick's full travel first.**
The install outline does not create arm files or start these user services.
Hardware writes require both `--apply` and the matching arm-file contents:

```bash
install -d "$HOME/.config/ab9-ffb"
printf '%s\n' 'I UNDERSTAND TELEMETRY EFFECTS MAY MOVE THE STICK' \
  > "$HOME/.config/ab9-ffb/telemetry-effects-enabled"
systemctl --user start ab9-telemetryd.service
```

Telemetry selects its own copied aircraft preset; automatic base-profile writes
are optional. To enable those as well, with the aircraft parked and travel clear:

```bash
printf '%s\n' 'I UNDERSTAND THE STICK MAY MOVE' \
  > "$HOME/.config/ab9-ffb/auto-switch-enabled"
systemctl --user start ab9-profiled.service
journalctl --user -u ab9-profiled.service -u ab9-telemetryd.service -n 60 --no-pager
```

Wait for `Applied and verified ... force output restored` or `AB9 base settings
already match this profile`, and check for errors before taking off. Reading,
writing, and verifying many settings can take tens of seconds. Logs report the
application's total duration and time spent with force output muted. A fixed
three-second sleep is not a readiness check.

The supplied telemetry service caps each synthesized effect at 15 percent. This
is not a combined native-plus-telemetry force or calibrated torque limit. After
any profile application has finished, stop the services with:

```bash
systemctl --user stop ab9-telemetryd.service ab9-profiled.service
```

This stops supplementary effects and automatic switching; native DCS FFB and
previously applied base settings remain.

### Preset versions and edits

The telemetry mixer accepts the four reported weapon-effect renames between
the 1.0.1 and 1.0.4 preset formats:

- `weapon_switch` / `weapon_release_effect_enabled`
- `weapon_release` / `weapon_release_intensity`
- `countermeasure_release` / `cm_vibration_intensity`
- `gun_vibration` / `gun_vibration_intensity`

Within a preset, the newer name wins if both exist, including `false` or zero.
Local overrides in `~/.config/ab9-ffb/telemetry-overrides.json` take precedence
over the preset in either naming scheme. This covers these fields; newer curve
formats and other renamed effects still require separate validation.

Both daemons reload copied presets when DCS reports a different aircraft. The
telemetry daemon also reloads overrides at that point. To pick up an edit,
switch to another aircraft and back while parked, or restart the relevant
service with travel clear and no profile write in progress. Editing a file
alone does not trigger an automatic base write. After a telemetry timeout, the
listener clears effects and resets its event counters before processing new
data, preventing a stale ammunition count from creating a new weapon pulse.

The reported loss of stick-axis input during aircraft changes is still
unresolved. The profile timing logs can help identify a force-output pause;
they do not establish the cause of an input dropout. Hardware testing should
compare automatic profiles off, telemetry off, and native DCS FFB alone, and
capture the corresponding user-service and USB/HID kernel logs.

## Critical Proton rule

Use `PROTON_ENABLE_HIDRAW` for high-button-count VIRPIL/MFG devices, but do not
include the AB9 (`346e:1000`). The AB9 must use Wine's evdev joystick path or
DCS inputs may work while native DirectInput force feedback does not.

## Safety

The AB9 can move suddenly and produce 12 N-m. Clear people, the chair, cables,
and the rig from its full travel before any reset, profile write, or motor test.
Do not blindly scan MOZA serial commands. Group 32 subcommand 15 silenced the
serial channel on the tested base until a physical power cycle.

## Contributing

Reports from other kernels, GPUs, AB9 firmware, DCS modules, and MOZA flight
bases are welcome. Include software versions, the exact aircraft/mission, and
whether a result was observed in 2D or VR. Do not upload MOZA firmware, copied
MOZA preset files, serial numbers, or raw captures containing private device
identifiers.

This project is independent community interoperability work and is not
affiliated with Eagle Dynamics, Bigscreen, MOZA, Valve, or Collabora.
