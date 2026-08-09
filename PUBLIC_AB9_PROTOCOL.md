# MOZA AB9 Linux interoperability notes

These notes document the subset of the MOZA AB9 protocol used to make native
Linux force feedback and aircraft-profile selection work. They are independent
interoperability research, not official MOZA documentation.

## Safety warning

The AB9 is a 12 N-m motor. Serial writes can change torque, spring, damping,
control mode, calibration, or motor state. Do not run write commands without
knowing exactly what they do and ensuring the mechanism has unobstructed travel.

Do not enumerate or fuzz write command groups. Do not send group 32 subcommand
15 with experimental payloads; it redirected/silenced the serial output channel
until a physical power cycle on the tested base.

## Device interfaces

The tested AB9 enumerates as USB `346e:1000` and exposes:

- a HID joystick/PID force-feedback interface;
- an evdev event device used for Linux `FF_*` effects;
- a proprietary 115200-baud serial command channel on a `ttyACM` device.

Find the serial path by walking `/sys/class/tty/ttyACM*` to the parent USB
device and matching `idVendor=346e`, `idProduct=1000`. Never hard-code
`/dev/ttyACM2`; its number can change.

## Serial framing

```text
request:
  7e | payload_length | group | device | command_id... | data... | checksum

checksum:
  (13 + sum(all preceding frame bytes)) modulo 256

response:
  request group + 0x80
  device ID with high/low nibbles swapped
```

The main AB9 device ID used here is `0x12`. Multi-byte integer values are
big-endian.

## Native-FFB state

The following command definitions were identified in MOZA Cockpit 1.1.4.21's
`DeviceCommon.dll` JSON resources and verified against the tested AB9:

| Setting | Get | Set | Width | Values |
| --- | --- | --- | --- | --- |
| APP state | group 30, command `0x86` | n/a | 2 | 0 locked, 1 free, 2 error |
| Control mode | group 30, command `0x85` | group 31, command `0x85` | 2 | 0 Telemetry, 1 DirectInput, 2 Composite |
| Working mode | group 30, command `0x50` | group 31, command `0x50` | 1 | 0 normal, 1 standby, 2 debug |
| Force-output disable gate | group 30, command `0xde` | group 31, command `0xde` | 2 | 0 allow, 1 force output to zero |
| FFB controller | group 30, commands `0xe1 0x17` | group 31, commands `0xe1 0x17` | 1 | 0 disabled, 1 enabled |
| Auto calibration | group 30, command `0x82` | group 31, command `0x82` | varies | status 0 never, 1 executing, 2 complete, 3 failed |
| Base restore/retest | group 30, command `0xc1` | group 31, command `0xc1` | 2 | progress 0-100; 200 failure |

The minimum verified native-FFB configuration is:

```text
APP state = 1
Control mode = 1
Force-output disable gate = 0
FFB controller = 1
```

Read all values first, write only incorrect values, pause briefly between
writes, and read them back afterward.

## Recovery finding

The base initially acknowledged complete Linux HID PID effect uploads and start
requests but produced no motor output. Its firmware state was locked/error with
the output gate suppressing force. This was not a Linux driver failure.

The recovery sequence observed from MOZA Cockpit was:

1. Toggle Base Restore and Reset through group 31 command `0xc1`.
2. Poll group 30 command `0xc1` until progress reaches 100.
3. Complete the operation with `ComSetDevControl(0)`, group 1, device `0x12`,
   payload byte `00`.
4. Verify APP free, calibration complete, motor normal, DirectInput mode,
   output gate clear, and FFB controller enabled.

The first reset failed at 98% with status 200 because the seat/rig obstructed
stick travel. Clearing the obstruction allowed the second reset to finish.
Prefer the official Cockpit UI for recovery rather than replaying raw frames.

## Linux PID driver binding

The working kernel driver is `hid-universal-pidff`. The AB9 dynamic ID is:

```text
0003 0000346E 00001000
```

A udev hot-plug rule can register the ID before the HID child enumerates:

```udev
ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="346e", ATTR{idProduct}=="1000", RUN+="/usr/local/libexec/ab9-ffb-setup --new-id-only"
```

A boot oneshot should also inspect `/sys/bus/hid/devices/0003:346E:1000.*`
and rebind any instance that enumeration attached to `hid-generic`.

## Why copied MOZA presets cannot be applied literally

MOZA aircraft `.preset` files mix persistent base settings with host-side
telemetry effects. Many official profiles select Composite mode and set game
FFB to zero because Windows MOZA Cockpit expects to generate the dynamic force
itself.

On Linux, literal Composite-mode application suppressed standard PID effects.
The working `native` interpretation therefore:

- copies the persistent base tuning;
- retains the current torque ceiling rather than raising it;
- forces control mode 1 (DirectInput);
- sets game FFB gain to 100;
- verifies all writes before reopening the output gate.

The tested mapper covers 56 persistent settings. Official and custom preset
files should be copied from each user's own MOZA Cockpit installation rather
than redistributed.

## Automatic aircraft selection

DCS sends `LoGetSelfData().Name` to `127.0.0.1:34399`. The selector maps known
DCS aliases to MOZA vehicle identifiers, prefers the newest matching custom
profile, and falls back to the official profile.

Motor writes require both an explicit `--apply` option and an arm file whose
contents exactly acknowledge that profile changes may move the stick.

## Supplementary telemetry effects

DCS exports at most 50 frames per second to `127.0.0.1:34400`. The current
fields include aircraft name, attitude, engine/rotor RPM, airspeed, vertical
speed, angle of attack/sideslip, acceleration, velocity, angular velocity,
gear/flap/speedbrake/canopy state, altitude, payload counts, and
countermeasures.

The Linux mixer uses copied telemetry parameters to create standard evdev
effects. Native DCS DirectInput forces remain active at the same time. The
current prototype supports:

- periodic engine and rotor rumble;
- runway/gear buffet;
- angle-of-attack buffet;
- acceleration and control-motion cues;
- weapon/countermeasure events;
- helicopter blade-slap, ETL, and VRS cues;
- optional fixed-wing spring and damper augmentation.

Safety properties:

- hardware output is independently armed;
- each effect is clamped to a configured magnitude cap (15% in the tested
  service);
- no telemetry for 0.5 seconds erases every synthesized effect;
- process exit and signals stop and erase uploaded effects;
- a dry-run simulation path exercises profile selection/mixing without opening
  the motor device.

This is a working prototype, not a byte-for-byte reimplementation of MOZA
Cockpit's proprietary effect engine. The flight model, effect curves, and
aircraft-specific tuning need broader community validation.
