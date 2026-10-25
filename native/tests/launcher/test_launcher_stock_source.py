"""Read-only stock-icon admission boundaries; no target image is executed."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "production_launcher_runner", Path(__file__).with_name("run_production_launcher.py"))
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class StockSourceTest(unittest.TestCase):
    HASH = "a" * 64

    def admit(self, path, *, links=1, attributes=0, mode=stat.S_IFREG, digest=None):
        info = SimpleNamespace(st_file_attributes=attributes, st_mode=mode, st_nlink=links)
        with patch.object(Path, "lstat", return_value=info), \
                patch.object(RUNNER, "digest", return_value=digest or self.HASH):
            return RUNNER.stock_icon_source(path, self.HASH)

    def test_read_only_c_build_root_is_admitted(self):
        path = Path("C:/ValMAXBuild/build/owned-stage/runtime/mod/max.exe")
        self.assertEqual(self.admit(path), path)

    def test_read_only_d_build_root_stays_admitted(self):
        path = Path("D:/ValMAXBuild/build/owned-stage/runtime/mod/max.exe")
        self.assertEqual(self.admit(path), path)

    def test_unowned_path_rejected_before_read(self):
        with patch.object(RUNNER, "digest") as digest, patch.object(Path, "lstat") as lstat:
            with self.assertRaisesRegex(ValueError, "explicit pinned owned"):
                RUNNER.stock_icon_source(Path("C:/Other/runtime/mod/max.exe"), self.HASH)
            digest.assert_not_called()
            lstat.assert_not_called()

    def test_exact_runtime_mod_max_path_required(self):
        for path in ["C:/ValMAXBuild/build/owned/runtime/max.exe",
                     "C:/ValMAXBuild/build/owned/runtime/mod/other.exe",
                     "C:/ValMAXBuild/build/owned/not-runtime/mod/max.exe"]:
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "explicit pinned owned"):
                self.admit(Path(path))

    def test_full_lowercase_sha_required(self):
        path = Path("C:/ValMAXBuild/build/owned/runtime/mod/max.exe")
        for value in ["a" * 63, "A" * 64, "g" * 64]:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "lowercase stock SHA"):
                RUNNER.stock_icon_source(path, value)

    def test_reparse_input_rejected(self):
        with self.assertRaisesRegex(ValueError, "reparse stock icon path"):
            self.admit(Path("C:/ValMAXBuild/build/owned/runtime/mod/max.exe"),
                       attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)

    def test_hardlinked_input_rejected(self):
        with self.assertRaisesRegex(ValueError, "pinned ordinary file"):
            self.admit(Path("C:/ValMAXBuild/build/owned/runtime/mod/max.exe"), links=2)

    def test_hash_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, "pinned ordinary file"):
            self.admit(Path("C:/ValMAXBuild/build/owned/runtime/mod/max.exe"), digest="b" * 64)

    def test_nonfile_input_rejected(self):
        with self.assertRaisesRegex(ValueError, "pinned ordinary file"):
            self.admit(Path("C:/ValMAXBuild/build/owned/runtime/mod/max.exe"), mode=stat.S_IFDIR)


if __name__ == "__main__":
    unittest.main()
