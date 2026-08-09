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

Start with the [complete field guide](PUBLIC_GUIDE.md). The
[AB9 protocol and safety notes](PUBLIC_AB9_PROTOCOL.md) cover the force-feedback
work in detail.

## Repository status

This is an alpha research release intended to make the result auditable and to
invite testing and contributions. It is not a one-click installer. In
particular:

- the Bigscreen 2e eye pipeline still needs better fit/calibration diagnostics;
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
python -m unittest -v test_ab9_telemetry.py
python -m py_compile \
  moza.py ab9_control.py ab9_profile.py ab9_profiled.py ab9_telemetryd.py

cc -std=c11 -Wall -Wextra -Werror -O2 -o ab9_test ab9_test.c
cc -std=c11 -Wall -Wextra -Werror -O2 -o ab9_spring_test ab9_spring_test.c
cc -std=c11 -Wall -Wextra -Werror -O2 -o ab9_state ab9_state.c
```

The Python tests use synthetic fixtures and do not touch the motor. The C motor
probes refuse to run unless their `--move-stick` danger acknowledgment is
present.

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

Run both daemons in dry-run mode first. Hardware writes remain disabled unless
both `--apply` and the corresponding explicit arm file are present. The tested
telemetry service clamps every synthesized effect to 15 percent.

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
