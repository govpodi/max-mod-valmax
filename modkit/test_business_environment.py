"""Synthetic directory guards only; no browser, registry or real profile."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import business_environment as module
from business_environment import DIRECTORY, VARIABLE, child_environment


class BusinessEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="business-env-test-", dir=Path(__file__).resolve().parent.parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / "MXMOD"
        self.profile.mkdir()

    def test_native_override_is_fixed_child_copy_and_global_environment_is_unchanged(self):
        source = {"PATH": "synthetic path", "LOCALAPPDATA": "synthetic untouched", "WEBVIEW2_USER_DATA_FOLDER": "foreign-profile", "ValMAX_TEST": "retained"}
        before = dict(os.environ)
        result = child_environment(source, self.profile)
        self.assertEqual(str(self.profile / DIRECTORY), result[VARIABLE])
        self.assertEqual("foreign-profile", source[VARIABLE])
        self.assertEqual("synthetic untouched", result["LOCALAPPDATA"])
        self.assertEqual("retained", result["ValMAX_TEST"])
        self.assertEqual(before, dict(os.environ))
        self.assertTrue((self.profile / DIRECTORY).is_dir())
        self.assertEqual([], list((self.profile / DIRECTORY).iterdir()))

    def test_every_case_variant_is_removed_before_one_canonical_override(self):
        source = {"webview2_user_data_folder": "one", "WebView2_User_Data_Folder": "two", "WEBVIEW2_USER_DATA_FOLDER": "three", "WEBVIEW2_BROWSER_EXECUTABLE_FOLDER": "retained runtime selection"}
        result = child_environment(source, self.profile)
        self.assertEqual([VARIABLE], [key for key in result if key.casefold() == VARIABLE.casefold()])
        self.assertEqual("retained runtime selection", result["WEBVIEW2_BROWSER_EXECUTABLE_FOLDER"])

    def test_existing_browser_contents_are_not_read_or_modified(self):
        target = self.profile / DIRECTORY; target.mkdir()
        (target / "synthetic-cookie-file").write_bytes(b"synthetic opaque existing state")
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("No web data read")), mock.patch.object(Path, "read_text", side_effect=AssertionError("No web data read")):
            result = child_environment({}, self.profile)
        self.assertEqual(str(target), result[VARIABLE])
        self.assertEqual(b"synthetic opaque existing state", (target / "synthetic-cookie-file").read_bytes())

    def test_missing_profile_is_not_created(self):
        missing = self.root / "absent" / "MXMOD"
        with self.assertRaises((ValueError, OSError)): child_environment({}, missing)
        self.assertFalse(missing.parent.exists())

    def test_non_mod_relative_unc_and_traversal_roots_fail(self):
        official = self.root / "ONEME"; official.mkdir()
        paths = [official, self.root, Path("relative") / "MXMOD", self.root / "outside" / ".." / "MXMOD"]
        if os.name == "nt": paths.append(Path("\\\\localhost\\share\\MXMOD"))
        for path in paths:
            with self.subTest(path=str(path)), self.assertRaises((ValueError, OSError)):
                child_environment({}, path)
        self.assertEqual([], list(official.iterdir()))

    def test_target_regular_file_or_hardlink_is_never_overwritten(self):
        target = self.profile / DIRECTORY
        original = self.root / "synthetic-target"; original.write_bytes(b"synthetic unrelated contents")
        os.link(original, target)
        with self.assertRaises((ValueError, OSError)): child_environment({}, self.profile)
        self.assertEqual(b"synthetic unrelated contents", target.read_bytes())
        self.assertEqual(b"synthetic unrelated contents", original.read_bytes())

    def test_target_symlink_is_rejected(self):
        outside = self.root / "outside"; outside.mkdir()
        try: (self.profile / DIRECTORY).symlink_to(outside, target_is_directory=True)
        except OSError: self.skipTest("Creating test symlink is unavailable")
        with self.assertRaises((ValueError, OSError)): child_environment({}, self.profile)
        self.assertEqual([], list(outside.iterdir()))

    @unittest.skipUnless(os.name == "nt", "Windows junction guard")
    def test_target_windows_junction_is_rejected_without_writing_target(self):
        outside = self.root / "outside"; outside.mkdir(); link = self.profile / DIRECTORY
        result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode: self.skipTest("Creating synthetic junction unavailable")
        try:
            with self.assertRaises((ValueError, OSError)): child_environment({}, self.profile)
            self.assertEqual([], list(outside.iterdir()))
        finally:
            self.assertTrue(link.is_relative_to(self.root)); os.rmdir(link)

    @unittest.skipUnless(os.name == "nt", "Windows directory sharing")
    def test_prepared_parent_is_pinned_during_child_validation(self):
        original_check = module._check_path
        attempts = []
        def check(path, *, directory):
            if Path(path).name == DIRECTORY:
                with self.assertRaises(PermissionError): self.profile.rename(self.root / "moved-profile")
                attempts.append(True)
            return original_check(path, directory=directory)
        with mock.patch.object(module, "_check_path", side_effect=check):
            child_environment({}, self.profile)
        self.assertTrue(attempts)
        self.assertFalse((self.root / "moved-profile").exists())

    @unittest.skipUnless(os.name == "nt", "Windows junction ancestor guard")
    def test_profile_root_junction_cannot_redirect_new_browser_directory(self):
        outside = self.root / "outside"; outside.mkdir()
        self.assertTrue(self.profile.is_relative_to(self.root))
        self.profile.rmdir()  # Our empty synthetic directory only.
        result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(self.profile), str(outside)], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode: self.skipTest("Creating synthetic junction unavailable")
        try:
            with self.assertRaises((ValueError, OSError)): child_environment({}, self.profile)
            self.assertEqual([], list(outside.iterdir()))
        finally:
            self.assertTrue(self.profile.is_relative_to(self.root)); os.rmdir(self.profile)

    def test_malformed_environment_fails_before_filesystem_write(self):
        for source in (None, [], {7: "value"}, {"key": None}, {"key": "bad\x00value"}, {"bad\x00key": "value"}):
            with self.assertRaises(ValueError): child_environment(source, self.profile)
        self.assertEqual([], list(self.profile.iterdir()))


if __name__ == "__main__":
    unittest.main()
