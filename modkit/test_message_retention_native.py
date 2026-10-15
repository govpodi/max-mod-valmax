"""Byte-only guard tests; no client profiles, databases or native processes."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

import message_retention_native as native


def image(size: int) -> bytes:
    data = bytearray(256)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3c, 64)
    data[64:68] = b'PE\0\0'
    struct.pack_into('<H', data, 68, 0x8664)
    struct.pack_into('<H', data, 88, 0x20b)
    struct.pack_into('<I', data, 144, size)
    return bytes(data)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.modules = copy.deepcopy(native.MODULES)
        self.images = {}
        self.paths = {}
        for item in self.modules:
            data = image(item['imageSize'])
            item['moduleSize'] = len(data)
            item['moduleSha256'] = hashlib.sha256(data).hexdigest()
            self.images[item['name']] = data
            self.paths[item['name']] = Path('D:/synthetic') / item['name']
        self.replace = patch.object(native, 'MODULES', self.modules)
        self.replace.start()
        self.addCleanup(self.replace.stop)

    def spec(self):
        return native.specification_from_bytes(self.images, module_paths=self.paths)

    def test_spec_is_fresh_and_resync_is_unproven(self):
        result = self.spec()
        self.assertEqual(result['schema'], 1)
        self.assertFalse(result['historySyncVerified'])
        self.assertEqual(result['limits']['maxMessageIds'], 4096)
        result['modules'][0]['pins'][0]['rva'] = 0
        result['limits']['maxMessageIds'] = 0
        self.assertNotEqual(self.spec()['modules'][0]['pins'][0]['rva'], 0)
        self.assertEqual(self.spec()['limits']['maxMessageIds'], 4096)

    def test_each_file_identity_is_required(self):
        for name in self.images:
            with self.subTest(name=name), self.assertRaises(ValueError):
                wrong = dict(self.images)
                wrong[name] = wrong[name][:-1] + b'\x01'
                native.specification_from_bytes(wrong, module_paths=self.paths)
        with self.assertRaises(ValueError):
            native.specification_from_bytes({'core.dll': self.images['core.dll']}, module_paths=self.paths)
        with self.assertRaises(ValueError):
            native.specification_from_bytes(self.images, module_paths={})

    def test_pe_checks_even_if_test_hash_matches(self):
        for at, value in [(0, b'NZ'), (64, b'XP\0\0'), (68, b'\x64\xaa'),
                          (88, b'\x0b\x01'), (144, b'\0\0\0\0'), (60, b'\xff' * 4)]:
            with self.subTest(offset=at):
                data = bytearray(self.images['core.dll'])
                data[at:at + len(value)] = value
                data = bytes(data)
                self.modules[0]['moduleSha256'] = hashlib.sha256(data).hexdigest()
                with self.assertRaises(ValueError):
                    native.specification_from_bytes({**self.images, 'core.dll': data}, module_paths=self.paths)

    def test_source_has_strict_enable_and_no_rpc_override(self):
        with patch.object(native, 'specification', return_value=self.spec()):
            source = native.source_for(Path('D:/synthetic'))
            self.assertIn('"retentionEnabled":false', source)
            self.assertIn('"storageAvailable":true', source)
            self.assertNotIn('rpc.exports', source)
            self.assertNotIn('new NativeFunction', source)
            self.assertIn("new NativeCallback(firstRowGate,'int'", source)
            self.assertIn('Interceptor.replaceFast(gate,memory)', source)
            self.assertNotIn('recv.wait', source)
            with self.assertRaises(ValueError):
                native.source_for(Path('D:/synthetic'), enabled=1)

    def test_paths_resolve_only_payload_files(self):
        images, paths = self.images, self.paths
        class ImageFile:
            def __init__(self, name): self.name = name
            def read_bytes(self): return images[self.name]
            def __str__(self): return str(paths[self.name])
        with patch.object(native, 'payload_file') as resolve:
            resolve.side_effect = lambda root, name: ImageFile(name)
            result = native.specification(Path('D:/synthetic'))
            self.assertEqual(resolve.call_count, 2)
            self.assertEqual([item['modulePath'] for item in result['modules']], list(map(str, self.paths.values())))

    def test_known_routing_seed_is_canonical_bounded_and_deduplicated(self):
        seed = [{"accountId": "9223372036854775807", "chatId": "-9223372036854775808",
                 "messageIds": ["9007199254740993", "9007199254740993", "9223372036854775807"]}]
        result = native.known_records(seed)
        self.assertEqual(result[0]['messageIds'], ['9007199254740993', '9223372036854775807'])
        self.assertEqual(seed[0]['messageIds'][1], '9007199254740993')
        for bad in [None, {}, [{**seed[0], 'extra': 1}], [{**seed[0], 'messageIds': []}],
                    [{**seed[0], 'messageIds': ['01']}], [{**seed[0], 'messageIds': ['9223372036854775808']}],
                    [{**seed[0], 'accountId': '-1'}], [{**seed[0], 'chatId': '0'}],
                    [{**seed[0], 'messageIds': ['123'] * 257}]]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                native.known_records(bad)
        with patch.object(native, 'specification', return_value=self.spec()):
            self.assertIn('"knownMessages":[]', native.source_for(Path('D:/synthetic')))
            self.assertIn('"knownMessages":[{', native.source_for(Path('D:/synthetic'), known_messages=seed))


if __name__ == '__main__':
    unittest.main()
