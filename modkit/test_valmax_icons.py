"""Original SVG build assets only; no native client or user files."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from valmax_icons import ASSET_ROOT, ICON_IDS, resource_payloads


class ValMAXIconTests(unittest.TestCase):
    def test_complete_unique_monochrome_payloads(self):
        payloads = resource_payloads()
        self.assertEqual({":/valmax/icons/" + name + ".svg" for name in ICON_IDS}, set(payloads))
        self.assertEqual(7, len({hashlib.sha256(data).hexdigest() for data in payloads.values()}))
        self.assertLess(sum(map(len, payloads.values())), 20000)
        for data in payloads.values():
            self.assertIn(b'fill="currentColor"', data)
            self.assertNotIn(b"stroke=", data)
            self.assertNotIn(b"<rect", data)
            self.assertNotIn(b"data:", data)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="valmax-icon-assets-", dir="D:/ValMAX/analysis")
        self.addCleanup(self.temp.cleanup); self.root = Path(self.temp.name) / "assets"
        shutil.copytree(ASSET_ROOT, self.root)

    def test_modified_bytes_rejected_before_rcc(self):
        path = self.root / "icons/ghost.svg"; path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "hash/size"):
            resource_payloads(self.root)

    def test_path_change_cannot_read_arbitrary_file(self):
        path = self.root / "catalog.json"; value = json.loads(path.read_text(encoding="utf8")); value["icons"][0]["file"] = "../outside.svg"
        path.write_text(json.dumps(value), encoding="utf8")
        with self.assertRaisesRegex(ValueError, "path"):
            resource_payloads(self.root)

    def test_catalog_reorder_or_incomplete_fails(self):
        path = self.root / "catalog.json"; value = json.loads(path.read_text(encoding="utf8")); value["icons"].reverse(); path.write_text(json.dumps(value), encoding="utf8")
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            resource_payloads(self.root)

    def test_active_or_external_svg_rejected_even_with_matching_hash(self):
        path = self.root / "icons/privacy.svg"; data = path.read_bytes().replace(b"<path ", b'<path onload="bad()" ', 1); path.write_bytes(data)
        manifest = self.root / "catalog.json"; value = json.loads(manifest.read_text(encoding="utf8")); value["icons"][0]["sha256"] = hashlib.sha256(data).hexdigest(); manifest.write_text(json.dumps(value), encoding="utf8")
        with self.assertRaisesRegex(ValueError, "active/external"):
            resource_payloads(self.root)


if __name__ == "__main__": unittest.main()
