#!/usr/bin/env python3
"""Back up and apply DCS/QuadViews quality settings while the game is closed."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid

PROFILES = {
    "previous": {"graphics": {"Upscaling": "FSR", "Scaling": .72, "maxFPS": 75},
                 "quad": {"peripheral_multiplier": .20, "focus_multiplier": 1.00,
                          "horizontal_focus_section": .42, "vertical_focus_section": .42,
                          "smoothen_focus_view_edges": .18}},
    "clear": {"graphics": {"Upscaling": "OFF", "maxFPS": 0},
              "quad": {"peripheral_multiplier": .30, "focus_multiplier": 1.00,
                       "horizontal_focus_section": .46, "vertical_focus_section": .46,
                       "smoothen_focus_view_edges": .20}},
    "balanced": {"graphics": {"Upscaling": "FSR", "Scaling": .85, "maxFPS": 0},
                 "quad": {"peripheral_multiplier": .30, "focus_multiplier": 1.00,
                          "horizontal_focus_section": .46, "vertical_focus_section": .46,
                          "smoothen_focus_view_edges": .20}},
}
# The .46 focus region with .20 feathering retains a .276-wide unmixed core,
# slightly larger than the previous .42 * (1 - 2 * .18) = .2688.


def update_graphics(text, values):
    begin = text.index('["graphics"]')
    end = text.index('["miscellaneous"]', begin)
    section = text[begin:end]
    for key, value in values.items():
        replacement = json.dumps(value)
        pattern = rf'(\["{re.escape(key)}"\]\s*=\s*)("[^"\n]*"|[-+0-9.eE]+)'
        section, count = re.subn(pattern, lambda m: m[1] + replacement, section)
        if count != 1:
            raise ValueError(f"Expected exactly one graphics setting: {key}")
    return text[:begin] + section + text[end:]


def update_quad(text, values):
    for key, value in values.items():
        pattern = rf'(?m)^({re.escape(key)}\s*=)[^\n]*$'
        matches = len(re.findall(pattern, text))
        if matches > 1:
            raise ValueError(f"Multiple sections define {key}; review the configuration first")
        if matches:
            match = re.search(pattern, text)
            if re.search(r'(?m)^\s*\[', text[:match.start()]):
                raise ValueError(f"An application override defines {key}; review the configuration first")
            text = re.sub(pattern, lambda m: m[1] + str(value), text)
        else:
            # Missing settings belong to the global section, before any app
            # override, rather than being appended to the last app section.
            text = f"{key}={value}\n" + text
    return text


def atomic_write(path, text):
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def apply_profile(prefix, name, state_root):
    base = prefix / "pfx/drive_c/users/steamuser"
    paths = [base / "Saved Games/DCS/Config/options.lua",
             base / "AppData/Local/Quad-Views-Foveated/settings.cfg"]
    before = [p.read_text() for p in paths]
    profile = PROFILES[name]
    after = [update_graphics(before[0], profile["graphics"]), update_quad(before[1], profile["quad"])]
    backup = state_root / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
    backup.mkdir(parents=True, mode=0o700)
    for index, path in enumerate(paths):
        shutil.copy2(path, backup / f"{index}-before")
        (backup / f"{index}-after").write_text(after[index])
    (backup / "manifest.json").write_text(json.dumps({"profile": name, "paths": [str(p.resolve()) for p in paths]}, indent=2) + "\n")
    try:
        for path, text in zip(paths, after):
            atomic_write(path, text)
    except Exception:
        for path, text in zip(paths, before):
            atomic_write(path, text)
        raise
    return backup


def restore(backup):
    manifest = json.loads((backup / "manifest.json").read_text())
    paths = [Path(p) for p in manifest["paths"]]
    for index, path in enumerate(paths):
        if path.read_text() != (backup / f"{index}-after").read_text():
            raise ValueError("Settings changed since this preset was applied; refusing to overwrite newer changes")
    for index, path in enumerate(paths):
        atomic_write(path, (backup / f"{index}-before").read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=Path(os.getenv("DCS_PREFIX_PATH", str(Path.home() / "Games/dcs-linux/prefix-ge11"))))
    parser.add_argument("--state-root", type=Path, default=Path.home() / ".local/state/dcs-linux/quality")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show")
    apply = sub.add_parser("apply")
    apply.add_argument("profile", choices=PROFILES)
    undo = sub.add_parser("restore")
    undo.add_argument("backup", type=Path)
    args = parser.parse_args()
    if args.command == "show":
        print(json.dumps(PROFILES, indent=2))
        return
    if subprocess.run(["pgrep", "-f", r"^S:.*DCS\.exe"], stdout=subprocess.DEVNULL).returncode == 0:
        parser.error("Close DCS before changing quality settings")
    if args.command == "apply":
        backup = apply_profile(args.prefix.expanduser().resolve(), args.profile, args.state_root.expanduser())
        print(f"Applied {args.profile}. Restart DCS to use it. Backup: {backup}")
    else:
        restore(args.backup)
        print("Restored the backed-up settings. Restart DCS to use them.")


if __name__ == "__main__":
    main()
