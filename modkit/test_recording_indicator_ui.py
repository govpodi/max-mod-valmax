"""Pinned native-only recorder indicator replacement and drift guards."""
from __future__ import annotations

from pathlib import Path
import unittest

import build_mod
import recording_indicator_ui as target
import ui_multi_resource_patch
import ui_prepare_features


BASELINE = Path(r"D:\CodexLabs\max-mod-26.35.1\runtime\baseline\max.exe")


class RecordingIndicatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BASELINE.is_file():
            raise unittest.SkipTest("Pinned MAX baseline is not installed")
        cls.baseline = BASELINE.read_bytes()

    def test_only_recording_indicator_changes_in_exact_packed_sources(self):
        for path in target.RESOURCE_PATHS:
            with self.subTest(path=path):
                original = ui_multi_resource_patch.read_source(self.baseline, path)
                result = target.transform(path, original)
                new = result.decode("utf8")
                self.assertEqual(result.count(target.MARKER.encode()), 1)
                self.assertNotIn("Qt.labs.lottieqt", new)
                self.assertNotIn("LottieAnimation", new)
                self.assertNotIn("qrc:/animations/recording.json", new)
                self.assertIn('objectName: "valmaxRecordingPulseDot"', new)
                self.assertIn('objectName: "valmaxRecordingPulseHalo"', new)
                self.assertEqual(new.count("width: UiKit.DesignSystem.SizeBase.px16"), 1)
                self.assertEqual(new.count("height: UiKit.DesignSystem.SizeBase.px16"), 1)
                self.assertIn("duration: 1506", new)
                self.assertIn("duration: 644", new)
                if path == target.VIDEO_RESOURCE:
                    self.assertIn("sourceComponent: Item {", new)
                    self.assertIn("root.recordingContext.stopRecord()", new)
                    self.assertIn("root.recordingContext.previewUrl", new)
                else:
                    self.assertIn("id: recordAnimation", new)
                    self.assertIn("audioContext.pauseRecord()", new)
                    self.assertIn("audioContext.resumeRecord()", new)

    def test_mutations_unknown_path_and_double_application_fail_closed(self):
        for path in target.RESOURCE_PATHS:
            original = ui_multi_resource_patch.read_source(self.baseline, path)
            for payload in (original + b"\n", target.transform(path, original)):
                with self.subTest(path=path, payload_size=len(payload)):
                    with self.assertRaises(ValueError):
                        target.transform(path, payload)
        with self.assertRaises(ValueError):
            target.transform(":/unrelated.qml", b"Item {}")

    def test_actual_recording_resources_reverse_to_exact_baseline(self):
        sources = {path: target.transform(path, ui_multi_resource_patch.read_source(self.baseline, path))
                   for path in target.RESOURCE_PATHS}
        spec, evidence = ui_multi_resource_patch.make_resource_spec(self.baseline, sources)
        patched = build_mod.patch_bytes(self.baseline, spec["patches"])
        self.assertEqual({item["path"] for item in evidence["resources"]}, set(sources))
        self.assertEqual(build_mod.patch_bytes(patched, spec["patches"], reverse=True),
                         self.baseline)

    def test_legacy_feature_selection_omits_native_only_recorders(self):
        self.assertEqual(len(ui_prepare_features.FEATURE_RESOURCES), 32)
        self.assertEqual(len(ui_multi_resource_patch.RESOURCES), 34)
        self.assertEqual(set(ui_prepare_features.FEATURE_RESOURCES),
                         set(ui_multi_resource_patch.RESOURCES) - set(target.RESOURCE_PATHS))


if __name__ == "__main__":
    unittest.main()
