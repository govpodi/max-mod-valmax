"""Pinned native source guards and real Qt history cache behavior."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

import history_cache_ui as target

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path("D:/CodexLabs/max-mod-26.35.1/runtime/baseline/max.exe")


class HistoryCacheUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BASELINE.is_file():
            raise unittest.SkipTest("Pinned native baseline unavailable")
        import ui_multi_resource_patch
        cls.source = ui_multi_resource_patch.read_source(BASELINE.read_bytes(), target.RESOURCE).decode("utf8")

    def test_changes_only_prefetch_budget_and_preserves_native_model_and_geometry(self):
        changed = target.transform(self.source)
        restored = changed.replace(target.CACHE_DECLARATION, target.CACHE_ANCHOR, 1)
        restored = restored.replace(target.GROWTH_HANDLER, target.GROWTH_ANCHOR, 1)
        self.assertEqual(restored, self.source.replace("\r\n", "\n"))
        self.assertEqual(changed.count(target.MARKER), 1)

    def test_changed_source_duplicate_and_wrong_types_fail_closed(self):
        for value in (None, b"", "", self.source + "\n", self.source.replace("cacheBuffer: 500", "cacheBuffer: 501"), target.transform(self.source)):
            with self.subTest(kind=type(value).__name__):
                with self.assertRaises(ValueError):
                    target.transform(value)
        self.assertEqual(target.transform(self.source.replace("\r\n", "\n")), target.transform(self.source))
        with self.assertRaises(ValueError):
            target.transform_sources({})

    def test_real_qt_bounds_hidden_delegates_and_preserves_history_navigation(self):
        environment = dict(os.environ)
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(ROOT / "analysis/performance-v4/list-cache/validate.py")],
                                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:] + result.stdout[-1000:])
        self.assertIn('"navigation": "passed"', result.stdout)
        for error in ("ReferenceError", "TypeError", "Binding loop", "Cannot assign"):
            self.assertNotIn(error, result.stderr)


if __name__ == "__main__":
    unittest.main()
