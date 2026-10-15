"""Pinned resource and real Qt image decode/geometry regressions."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import media_resize_ui as target

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path("D:/CodexLabs/max-mod-26.35.1/runtime/baseline/max.exe")


class MediaResizeUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BASELINE.is_file():
            raise unittest.SkipTest("Immutable native baseline unavailable")
        import ui_multi_resource_patch
        cls.source = ui_multi_resource_patch.read_source(BASELINE.read_bytes(), target.RESOURCE).decode("utf8")

    def test_exact_photo_resource_and_only_intended_changes(self):
        self.assertEqual(hashlib.sha256(self.source.encode()).hexdigest(), target.SOURCE_SHA256)
        changed = target.transform(self.source)
        self.assertEqual(changed.count(target.MARKER), 1)
        restored = changed.replace(target.PHOTO_DECODE_PROPERTIES, "", 1)
        restored = restored.replace("        source: root.previewSource\n        fillMode: Image.PreserveAspectCrop\n        clip: true\n", "        source: root.previewSource\n", 1)
        restored = restored.replace("            sourceSize: valmaxDecodeSize\n", "            sourceSize: root.sourceSize\n", 1)
        self.assertEqual(restored, self.source.replace("\r\n", "\n"))
        self.assertIn("interval: " + str(target.DECODE_INTERVAL_MS), changed)
        self.assertIn("retainWhileLoading: source.toString() === valmaxDecodedSource.toString()", changed)
        self.assertIn("onSourceChanged: if (valmaxDecodeInitialized) valmaxCommitDecodeSize()", changed)

    def test_duplicate_tampered_and_wrong_types_fail_closed(self):
        for source in (None, b"qml", "", self.source + "\n", self.source.replace("sourceSize: root.sourceSize", "sourceSize: root.otherSize"), target.transform(self.source)):
            with self.subTest(kind=type(source).__name__):
                with self.assertRaises(ValueError):
                    target.transform(source)
        self.assertEqual(target.transform(self.source.replace("\r\n", "\n")), target.transform(self.source))

    def test_generator_contract_preserves_other_resources(self):
        originals = {"HistoryPreviewCell.qml": self.source, "Other.qml": "unrelated content"}
        changed = target.transform_sources(originals)
        self.assertEqual(originals["HistoryPreviewCell.qml"], self.source)
        self.assertEqual(changed["Other.qml"], originals["Other.qml"])
        self.assertEqual(set(changed), set(originals))
        for sources in (None, {}, {"Other.qml": "qml"}):
            with self.assertRaises(ValueError):
                target.transform_sources(sources)

    def test_real_qt_decode_burst_crop_pixels_source_reuse_and_final_quality(self):
        temporary_root = ROOT / "analysis/test-temp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="media-resize-", dir=temporary_root) as directory:
            environment = dict(os.environ)
            for key in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "TEMP", "TMP", "TMPDIR", "QML_DISK_CACHE_PATH"):
                path = Path(directory) / key
                path.mkdir()
                environment[key] = str(path)
            environment.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", QML_DISABLE_DISK_CACHE="1")
            result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(ROOT / "analysis/performance-v2/fixture_media_resize.py")],
                                    env=environment, cwd=ROOT, capture_output=True, text=True, timeout=25)
            self.assertEqual(result.returncode, 0, result.stderr[-3500:])
            value = json.loads(result.stdout)
            baseline, changed = value["modes"]["baseline"], value["modes"]["coalesced"]
            self.assertEqual(baseline["decodesAfter100Resizes"], 101)
            self.assertEqual(changed["decodesAfter100Resizes"], 2)
            self.assertEqual(changed["liveGeometryChecks"], 200)
            self.assertEqual(changed["finalDecodeSize"], [300, 200])
            for key in ("mainAndFallbackCrop", "sourceChangeNeverShowsPreviousPhoto", "retainedPixelsWhileLoading", "lateOldDecodeDiscardedAfterSourceChange"):
                self.assertEqual(changed[key], "passed")
            for error in ("ReferenceError", "TypeError", "Binding loop", "Cannot assign"):
                self.assertNotIn(error, result.stderr)


if __name__ == "__main__":
    unittest.main()
