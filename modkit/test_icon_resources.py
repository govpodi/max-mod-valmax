"""Pinned icon resource extraction; no rcc process or native app in unit tests."""
from pathlib import Path
import struct
import unittest

from icon_resources import native_payloads, registration_anchors, split_binary_resource

NATIVE = Path("D:/CodexLabs/max-mod-26.35.1/runtime/baseline/max.exe")


class IconRccHeaderTests(unittest.TestCase):
    def test_binary_v3_offsets_and_rejections(self):
        good = b"qres" + struct.pack(">4I", 3, 24, 32, 40) + b"\0" * 40
        self.assertEqual({"tree": 24, "data": 32, "names": 40, "version": 3}, split_binary_resource(good))
        for data in (b"", good.replace(b"qres", b"bad!", 1), b"qres" + struct.pack(">4I", 2, 24, 32, 40) + b"\0" * 40,
                     b"qres" + struct.pack(">4I", 3, 24, 24, 40) + b"\0" * 40,
                     b"qres" + struct.pack(">4I", 3, 12, 32, 40) + b"\0" * 40):
            with self.assertRaises(ValueError): split_binary_resource(data)


@unittest.skipUnless(NATIVE.is_file(), "Pinned native baseline unavailable")
class NativeIconExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.data = NATIVE.read_bytes()

    def test_all_546_native_urls_and_register_unregister_anchors(self):
        payloads = native_payloads(self.data); self.assertEqual(546, len(payloads))
        self.assertIn(":/icon_pack/privacy.svg", payloads); self.assertIn(":/icon_pack/reply.svg", payloads)
        self.assertTrue(all(path.startswith(":/icon_pack/") for path in payloads))
        anchors = registration_anchors(self.data); self.assertEqual(6, len(anchors))
        self.assertEqual({"register", "unregister"}, {row["lifetime"] for row in anchors})
        self.assertTrue(all(len(bytes.fromhex(row["expected"])) == 7 for row in anchors))

    def test_changed_baseline_rejected_before_decoding(self):
        data = bytes([self.data[0] ^ 1]) + self.data[1:]
        with self.assertRaisesRegex(ValueError, "immutable"): native_payloads(data)
        with self.assertRaisesRegex(ValueError, "immutable"): registration_anchors(data)


if __name__ == "__main__": unittest.main()
