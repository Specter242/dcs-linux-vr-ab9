#!/usr/bin/env python3
"""Stage, install, or restore a fingerprinted QuadViews continuous-feather experiment.

Only the 1.1.3 DLL fingerprint below is supported. The embedded projection
shader's MAX immediate changes from .5 to 0; instruction and PE sizes stay
unchanged. The equivalent source patch is qvf-continuous-feather.patch.
DXBC finalization follows DXVK's dxbc-spirv hashDxbcBinary (MIT licensed;
see THIRD_PARTY_NOTICES.md).
The MD5 compression function follows RFC 1321; ordinary hashlib.md5 cannot
implement DXBC's custom final blocks.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import uuid

ORIGINAL_SHA256 = "20673164cfd789d70b3cd6f7e2b49a1a0618e7fbb606e5e6787e2f66518ad482"
SHADER_OFFSET, SHADER_SIZE = 0x8A050, 2592
INSTRUCTION_OFFSET, IMMEDIATE_OFFSET = 0x8A70C, 0x8A724
INSTRUCTION = bytes.fromhex("3400000712001000000000000a00100000000000014000000000003f")
DLL_RELATIVE = "pfx/drive_c/Program Files/OpenXR-Quad-Views-Foveated/XR_APILAYER_MBUCCHIA_quad_views_foveated.dll"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def md5_blocks(data):
    """Compress already finalized, complete 64-byte MD5 blocks."""
    if len(data) % 64:
        raise ValueError("Incomplete MD5 block")
    state = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476]
    shifts = [7, 12, 17, 22] * 4 + [5, 9, 14, 20] * 4 + [4, 11, 16, 23] * 4 + [6, 10, 15, 21] * 4
    constants = [int(abs(math.sin(i + 1)) * 2**32) for i in range(64)]
    for offset in range(0, len(data), 64):
        words = struct.unpack_from("<16I", data, offset)
        a, b, c, d = state
        for i in range(64):
            if i < 16:
                f, g = (b & c) | (~b & d), i
            elif i < 32:
                f, g = (d & b) | (~d & c), (5 * i + 1) % 16
            elif i < 48:
                f, g = b ^ c ^ d, (3 * i + 5) % 16
            else:
                f, g = c ^ (b | ~d), (7 * i) % 16
            value = (a + f + constants[i] + words[g]) & 0xFFFFFFFF
            shift = shifts[i]
            rotated = ((value << shift) | (value >> (32 - shift))) & 0xFFFFFFFF
            a, d, c, b = d, c, b, (b + rotated) & 0xFFFFFFFF
        state = [(old + new) & 0xFFFFFFFF for old, new in zip(state, [a, b, c, d])]
    return struct.pack("<4I", *state)


def dxbc_checksum(blob):
    if blob[:4] != b"DXBC" or len(blob) < 32:
        raise ValueError("Not a DXBC container")
    version, length, count = struct.unpack_from("<III", blob, 20)
    if version != 1 or length != len(blob) or 32 + count * 4 > length:
        raise ValueError("Invalid DXBC header")
    body = blob[20:]
    a = struct.pack("<I", (len(body) * 8) & 0xFFFFFFFF)
    b = struct.pack("<I", ((len(body) * 2) | 1) & 0xFFFFFFFF)
    remainder = len(body) % 64
    whole, tail = body[:len(body) - remainder], body[len(body) - remainder:]
    if remainder >= 56:
        final = tail + b"\x80" + bytes(63 - remainder) + a + bytes(56) + b
    else:
        final = a + tail + b"\x80" + bytes(55 - remainder) + b
    return md5_blocks(whole + final)


def patch_dll(original):
    if sha256(original) != ORIGINAL_SHA256:
        raise ValueError("Unsupported DLL fingerprint; no patch applied")
    if original[INSTRUCTION_OFFSET:INSTRUCTION_OFFSET + len(INSTRUCTION)] != INSTRUCTION:
        raise ValueError("Expected projection MAX instruction not found")
    shader = original[SHADER_OFFSET:SHADER_OFFSET + SHADER_SIZE]
    if dxbc_checksum(shader) != shader[4:20]:
        raise ValueError("Original projection shader checksum is invalid")
    patched = bytearray(original)
    struct.pack_into("<f", patched, IMMEDIATE_OFFSET, 0.0)
    new_shader = bytes(patched[SHADER_OFFSET:SHADER_OFFSET + SHADER_SIZE])
    patched[SHADER_OFFSET + 4:SHADER_OFFSET + 20] = dxbc_checksum(new_shader)
    return bytes(patched)


def atomic_write(path, data):
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def prepare(dll, output):
    original = dll.read_bytes()
    patched = patch_dll(original)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    for name, data in [("original.dll", original), ("continuous.dll", patched),
                       ("original.dxbc", original[SHADER_OFFSET:SHADER_OFFSET + SHADER_SIZE]),
                       ("continuous.dxbc", patched[SHADER_OFFSET:SHADER_OFFSET + SHADER_SIZE])]:
        (output / name).write_bytes(data)
    print(f"Staged experiment: {output}. Run the GPU probe before applying.")


def apply(prefix, stage, probe_log, state_root):
    original, patched = (stage / "original.dll").read_bytes(), (stage / "continuous.dll").read_bytes()
    if patch_dll(original) != patched:
        raise ValueError("Staged DLL does not match the verified patch operation")
    # The probe emits a receipt only after all original/new shader readbacks pass.
    receipts = [line for line in probe_log.read_text().splitlines() if line.startswith("FEATHER_PROBE_PASS ")]
    if len(receipts) != 1:
        raise ValueError("Expected exactly one successful GPU probe receipt")
    receipt = json.loads(receipts[0].split(" ", 1)[1])
    shader_hashes = [sha256((stage / name).read_bytes()) for name in ["original.dxbc", "continuous.dxbc"]]
    if not isinstance(receipt, dict) or receipt.get("shader_sha256") != shader_hashes or receipt.get("cases") != 12:
        raise ValueError("GPU probe receipt does not match these staged shaders")
    for name, dll in [("original.dxbc", original), ("continuous.dxbc", patched)]:
        if (stage / name).read_bytes() != dll[SHADER_OFFSET:SHADER_OFFSET + SHADER_SIZE]:
            raise ValueError("Staged shader does not match the DLL")
    target = prefix / DLL_RELATIVE
    if target.read_bytes() != original:
        raise ValueError("Installed DLL changed since staging; no replacement applied")
    backup = state_root / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
    backup.mkdir(parents=True, mode=0o700)
    shutil.copy2(target, backup / "original.dll")
    (backup / "probe.log").write_text(probe_log.read_text())
    (backup / "manifest.json").write_text(json.dumps({"path": str(target.resolve()),
        "before_sha256": sha256(original), "after_sha256": sha256(patched)}, indent=2) + "\n")
    atomic_write(target, patched)
    print(f"Installed continuous feather. Restart DCS. Backup: {backup}")


def restore(backup):
    manifest = json.loads((backup / "manifest.json").read_text())
    target, original = Path(manifest["path"]), (backup / "original.dll").read_bytes()
    if sha256(target.read_bytes()) != manifest["after_sha256"]:
        raise ValueError("Installed DLL changed; refusing to overwrite a newer layer")
    if sha256(original) != manifest["before_sha256"] or sha256(original) != ORIGINAL_SHA256:
        raise ValueError("Backup fingerprint is invalid")
    atomic_write(target, original)
    print("Restored the original QuadViews layer. Restart DCS.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("prepare")
    stage.add_argument("dll", type=Path)
    stage.add_argument("output", type=Path)
    install = sub.add_parser("apply")
    install.add_argument("stage", type=Path)
    install.add_argument("probe_log", type=Path)
    install.add_argument("--prefix", type=Path, default=Path.home() / "Games/dcs-linux/prefix-ge11")
    install.add_argument("--state-root", type=Path, default=Path.home() / ".local/state/dcs-linux/feather")
    undo = sub.add_parser("restore")
    undo.add_argument("backup", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare(args.dll, args.output)
        else:
            if subprocess.run(["pgrep", "-f", r"^S:.*DCS\.exe"], stdout=subprocess.DEVNULL).returncode == 0:
                parser.error("Close DCS before replacing its layer")
            if args.command == "apply":
                apply(args.prefix.resolve(), args.stage, args.probe_log, args.state_root)
            else:
                restore(args.backup)
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
