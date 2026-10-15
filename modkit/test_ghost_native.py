"""Ghost disk guards and contracts; no MAX process/profile is opened."""
from __future__ import annotations
import copy
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import ghost_native as native


class GhostSpecificationTests(unittest.TestCase):
    def image(self):
        data = bytearray(256)
        data[:2] = b'MZ'
        struct.pack_into('<I', data, 0x3C, 0x60)
        data[0x60:0x64] = b'PE\0\0'
        struct.pack_into('<H', data, 0x64, 0x8664)
        struct.pack_into('<H', data, 0x78, 0x20B)
        struct.pack_into('<I', data, 0x78 + 56, native.CORE_IMAGE_SIZE)
        return bytes(data)

    def validate(self, data):
        with patch.object(native, 'CORE_FILE_SIZE', len(data)), patch.object(
                native, 'CORE_SHA256', hashlib.sha256(data).hexdigest()):
            return native.specification_from_bytes(data, module_path=Path('D:/synthetic/core.dll'))

    def test_exact_pins_and_isolated_fresh_specification(self):
        first = self.validate(self.image())
        self.assertEqual(first['schema'], 1)
        self.assertEqual([v['feature'] for v in first['wrappers']], ['hideTyping', 'hideRead', 'hideRead', 'hideStories'])
        first['wrappers'][0]['arguments'][1] = 'int'
        self.assertEqual(self.validate(self.image())['wrappers'][0]['arguments'][1], 'int64')
        self.assertEqual(native.WRAPPERS[0]['rva'], 0x12A8B80)
        self.assertEqual(native.WRAPPERS[1]['rva'], 0x9CA190)
        self.assertEqual(len(bytes.fromhex(native.WRAPPERS[0]['expected'])), 25)
        self.assertEqual(len(bytes.fromhex(native.WRAPPERS[1]['expected'])), 42)
        self.assertEqual(native.WRAPPERS[1]['slot'], 8)
        self.assertEqual(native.WRAPPERS[2]['rva'], 0xA940C0)
        self.assertEqual(native.WRAPPERS[2]['selectorExcept'], 1)
        self.assertEqual(native.WRAPPERS[2]['slot'], 8)
        self.assertEqual(len(bytes.fromhex(native.WRAPPERS[2]['expected'])), 25)
        self.assertNotIn(0xA94050, [v['rva'] for v in native.WRAPPERS])
        self.assertNotIn(0x9CA4C0, [v['rva'] for v in native.WRAPPERS])
        self.assertEqual(native.WRAPPERS[3]['rva'], 0x1241F70)
        self.assertEqual(native.WRAPPERS[3]['arguments'], ['pointer', 'pointer', 'int64'])
        self.assertEqual(native.ONLINE['login']['rva'], 0x1531AB0)
        self.assertEqual(native.ONLINE['ping']['rva'], 0x15432AA)
        self.assertNotIn(0x123D4E0, [v['rva'] for v in native.WRAPPERS])
        self.assertNotIn(0x1241FA0, [v['rva'] for v in native.WRAPPERS])

    def test_modified_wrong_size_and_mutable_bytes_fail_closed(self):
        for data in (b'', b'X' * 256, bytearray(self.image())):
            with self.subTest(kind=type(data)), self.assertRaisesRegex(ValueError, 'Unsupported ghost core'):
                native.specification_from_bytes(data, module_path=Path('D:/synthetic/core.dll'))
        good = self.image()
        with patch.object(native, 'CORE_FILE_SIZE', len(good)), patch.object(
                native, 'CORE_SHA256', hashlib.sha256(good).hexdigest()):
            for data in (good[:-1], good[:-1] + b'X'):
                with self.assertRaisesRegex(ValueError, 'Unsupported ghost core'):
                    native.specification_from_bytes(data, module_path=Path('D:/synthetic/core.dll'))

    def test_pe_machine_optional_type_image_size_and_bounds_checked(self):
        for offset, replacement in ((0, b'XX'), (0x60, b'XX'), (0x64, b'\x4c\x01'),
                                    (0x78, b'\x0b\x01'), (0x78 + 56, b'\0\0\0\0'),
                                    (0x3C, b'\xff\xff\xff\x7f')):
            value = bytearray(self.image())
            value[offset:offset + len(replacement)] = replacement
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, 'PE layout'):
                self.validate(bytes(value))

    def test_actual_path_is_resolved_and_missing_core_rejected(self):
        with tempfile.TemporaryDirectory(prefix='ghost-spec-', dir=Path(__file__).resolve().parents[1] / 'analysis') as temporary:
            root = Path(temporary)
            with self.assertRaises(FileNotFoundError):
                native.specification(root)
            data = self.image()
            (root / 'core.dll').write_bytes(data)
            with patch.object(native, 'CORE_FILE_SIZE', len(data)), patch.object(
                    native, 'CORE_SHA256', hashlib.sha256(data).hexdigest()), patch(
                    'ghost_unread_native.specification', return_value={'synthetic': True}):
                spec = native.specification(root)
                self.assertEqual(Path(spec['modulePath']), (root / 'core.dll').resolve())


if __name__ == '__main__':
    unittest.main()
