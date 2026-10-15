"""Validate real pinned RCC structure without loading or running MAX."""
from pathlib import Path
import struct
import unittest
import zlib

import build_mod
import ui_multi_resource_patch as target


class MultiResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        baseline = Path(r"D:\CodexLabs\max-mod-26.35.1\runtime\baseline\max.exe")
        if not baseline.is_file():
            raise unittest.SkipTest("Pinned read-only baseline fixture absent")
        cls.binary = baseline.read_bytes()

    def test_independent_resource_records_cache_keys_and_exact_reverse(self):
        sources = {path: target.read_source(self.binary, path) + b"\n// synthetic resource guard\n"
                   for path in target.RESOURCES}
        spec, evidence = target.make_resource_spec(self.binary, sources)
        patched = build_mod.patch_bytes(self.binary, spec["patches"])
        self.assertEqual(build_mod.patch_bytes(patched, spec["patches"], reverse=True), self.binary)
        self.assertEqual(len(spec["patches"]), 6 + 3 * len(sources))
        self.assertEqual(len(evidence["resources"]), len(sources))
        for path, source in sources.items():
            node, key_raw, _ = target._node(path)
            self.assertEqual(patched[node + 4:node + 10], self.binary[node + 4:node + 10])
            relative = int.from_bytes(patched[node + 10:node + 14], "big") + target.RESOURCES[path][4] - target.SECTION_RVA
            raw = target.SECTION_RAW + relative
            length = int.from_bytes(patched[raw:raw + 4], "big")
            if target.RESOURCES[path][5] == 1:
                original_length = int.from_bytes(patched[raw + 4:raw + 8], "big")
                self.assertEqual(original_length, len(source))
                self.assertEqual(zlib.decompress(patched[raw + 8:raw + 4 + length]), source)
            else:
                self.assertEqual(patched[raw + 4:raw + 4 + length], source)
            self.assertNotEqual(patched[node + 14:node + 22], self.binary[node + 14:node + 22])
            self.assertEqual(patched[key_raw:key_raw + len(path[1:]) * 2],
                             (path[1:-1] + "x").encode("utf-16-le"))
        flags = struct.unpack_from("<I", patched, target.SECTION_HEADER_RAW + 36)[0]
        self.assertEqual(flags, 0x40000040)
        self.assertFalse(flags & (0x20000000 | 0x80000000))
        ranges = [(p["offset"], p["offset"] + len(bytes.fromhex(p["replacement"])))
                  for p in spec["patches"] if p.get("kind") != "append"]
        # Every existing byte outside the selected PE/RCC/cache guards stays
        # identical; independent resources and all original certificate bytes
        # are left alone.
        cursor = 0
        for start, end in sorted(ranges):
            self.assertEqual(patched[cursor:start], self.binary[cursor:start])
            cursor = end
        self.assertEqual(patched[cursor:len(self.binary)], self.binary[cursor:])

    def test_invalid_source_or_unknown_url_is_rejected_before_spec(self):
        path = next(iter(target.RESOURCES))
        for sources in ({}, {":/untrusted.qml": b"Item {}"}, {path: b"\xff"},
                        {path: b""}, {path: b"x" * (1024 * 1024 + 1)}):
            with self.subTest(keys=list(sources)):
                with self.assertRaises((ValueError, UnicodeDecodeError)):
                    target.make_resource_spec(self.binary, sources)
        with self.assertRaises(ValueError):
            target.make_resource_spec(b"wrong baseline", {path: b"Item {}"})

    def test_icon_tables_share_readonly_section_and_both_lifetimes_reverse(self):
        from icon_resources import registration_anchors
        bundle = b"qres" + struct.pack(">4I", 3, 24, 32, 40) + b"\0" * 40
        path = next(iter(target.RESOURCES))
        spec, evidence = target.make_resource_spec(self.binary, {path: target.read_source(self.binary, path)}, icon_bundle=bundle)
        patched = build_mod.patch_bytes(self.binary, spec["patches"])
        self.assertEqual(build_mod.patch_bytes(patched, spec["patches"], reverse=True), self.binary)
        info = evidence["icons"]
        self.assertEqual(6, info["pointerPatches"])
        self.assertEqual(bundle, patched[target.SECTION_RAW + info["recordOffset"]:target.SECTION_RAW + info["recordOffset"] + len(bundle)])
        for anchor in registration_anchors(self.binary):
            displacement = struct.unpack_from("<i", patched, anchor["raw"] + 3)[0]
            self.assertEqual(target.SECTION_RVA + info["recordOffset"] + info["sectionOffsets"][anchor["field"]], anchor["rva"] + 7 + displacement)
            self.assertEqual(patched[anchor["raw"]:anchor["raw"] + 3], bytes.fromhex(anchor["expected"])[:3])
        self.assertEqual(0x40000040, struct.unpack_from("<I", patched, target.SECTION_HEADER_RAW + 36)[0])


if __name__ == "__main__":
    unittest.main()
