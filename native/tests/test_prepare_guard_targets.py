"""Target selection boundaries for the baseline-derived guard input copier."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "native/tooling"))
from prepare_guard_targets import selected_targets


class SelectedGuardTargets(unittest.TestCase):
    def test_union_preserves_nested_service_and_required_stock_images(self):
        self.assertEqual(selected_targets([{"relative_path": "core.dll"},
                                           {"relative_path": "max-service/config.dll"}]),
                         ["core.dll", "max-service/config.dll", "max.exe", "notifications.dll"])

    def test_unsafe_non_pe_or_case_collision_rejects(self):
        for names in (("../core.dll",), ("C:/core.dll",), ("config.dll", "CONFIG.dll"),
                      ("notes.txt",), ("MXMOD/core.dll",), ("mod\\core.dll",), ("x.dll:stream",)):
            with self.subTest(names=names):
                with self.assertRaises(ValueError):
                    selected_targets([{"relative_path": name} for name in names])

    def test_missing_or_malformed_manifest_is_not_a_valid_subset(self):
        for value in ([], None, {}, ["core.dll"]):
            with self.subTest(value=value):
                with self.assertRaises((ValueError, KeyError)):
                    selected_targets(value)


if __name__ == "__main__":
    unittest.main()
