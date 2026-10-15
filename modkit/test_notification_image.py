"""Owned temporary image fixtures only; no messenger profile or notifications."""
import hashlib
from pathlib import Path
import tempfile
import unittest

import notification_image as target


class ImageTests(unittest.TestCase):
    def test_png_is_valid_transparent_rgba_and_reusable(self):
        from PySide6.QtGui import QImage
        image = QImage.fromData(target.PNG)
        self.assertFalse(image.isNull())
        self.assertEqual((1, 1, 0), (image.width(), image.height(), image.pixelColor(0, 0).alpha()))
        with tempfile.TemporaryDirectory(dir='D:/ValMAX/analysis/test-temp') as name:
            root = Path(name)
            path = target.prepare(root)
            self.assertEqual(target.SHA256, hashlib.sha256(path.read_bytes()).hexdigest())
            identity = path.stat().st_ino
            self.assertEqual(path, target.prepare(root))
            self.assertEqual(identity, path.stat().st_ino)

    def test_existing_foreign_or_corrupt_asset_is_never_overwritten(self):
        with tempfile.TemporaryDirectory(dir='D:/ValMAX/analysis/test-temp') as name:
            root = Path(name)
            path = root / target.NAME
            path.write_bytes(b'synthetic unrelated file')
            with self.assertRaises(ValueError):
                target.prepare(root)
            self.assertEqual(b'synthetic unrelated file', path.read_bytes())

    def test_relative_directory_rejected_before_writing(self):
        with self.assertRaises(ValueError):
            target.prepare(Path('.'))


if __name__ == '__main__':
    unittest.main()
