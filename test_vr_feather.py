import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("feather", Path(__file__).with_name("dcs-vr-feather.py"))
feather = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feather)


class ChecksumTests(unittest.TestCase):
    def test_compression_matches_standard_md5_with_standard_final_blocks(self):
        for size in [0, 1, 55, 56, 63, 64, 65, 119, 120, 128, 1000]:
            data = bytes(i % 256 for i in range(size))
            padded = data + b'\x80' + bytes((55 - size) % 64) + struct.pack('<Q', size * 8)
            self.assertEqual(feather.md5_blocks(padded), hashlib.md5(data).digest())

    def test_unknown_fingerprint_is_rejected(self):
        with self.assertRaises(ValueError):
            feather.patch_dll(b'unknown layer')


@unittest.skipUnless(os.getenv('QVF_TEST_DLL'), 'Set QVF_TEST_DLL to test the installed layer')
class InstalledLayerTests(unittest.TestCase):
    def setUp(self):
        self.original = Path(os.environ['QVF_TEST_DLL']).read_bytes()
        self.patched = feather.patch_dll(self.original)

    def test_only_alpha_floor_and_checksum_change(self):
        self.assertEqual(len(self.original), len(self.patched))
        allowed = set(range(feather.SHADER_OFFSET + 4, feather.SHADER_OFFSET + 20))
        allowed.update(range(feather.IMMEDIATE_OFFSET, feather.IMMEDIATE_OFFSET + 4))
        changes = {i for i, (a, b) in enumerate(zip(self.original, self.patched)) if a != b}
        self.assertTrue(changes)
        self.assertLessEqual(changes, allowed)
        shader = self.patched[feather.SHADER_OFFSET:feather.SHADER_OFFSET + feather.SHADER_SIZE]
        self.assertEqual(feather.dxbc_checksum(shader), shader[4:20])
        self.assertEqual(struct.unpack_from('<f', self.patched, feather.IMMEDIATE_OFFSET)[0], 0)

    def test_receipt_guard_backup_and_restore_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / 'prefix' / feather.DLL_RELATIVE
            target.parent.mkdir(parents=True)
            target.write_bytes(self.original)
            stage = root / 'stage'
            feather.prepare(target, stage)
            receipt = root / 'probe.log'
            receipt.write_text('FEATHER_PROBE_PASS {"cases":12,"shader_sha256":[]}')
            with self.assertRaises(ValueError):
                feather.apply(root / 'prefix', stage, receipt, root / 'backups')
            self.assertEqual(target.read_bytes(), self.original)
            hashes = [feather.sha256((stage / name).read_bytes()) for name in ['original.dxbc', 'continuous.dxbc']]
            receipt.write_text('FEATHER_PROBE_PASS ' + json.dumps({'cases': 12, 'shader_sha256': hashes}))
            feather.apply(root / 'prefix', stage, receipt, root / 'backups')
            backup, = (root / 'backups').iterdir()
            self.assertEqual(target.read_bytes(), self.patched)
            target.write_bytes(b'newer layer')
            with self.assertRaises(ValueError):
                feather.restore(backup)
            target.write_bytes(self.patched)
            feather.restore(backup)
            self.assertEqual(target.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()
