"""Launcher tests with temporary profiles and mocked process creation only."""
from __future__ import annotations

import copy
from contextlib import chdir, nullcontext
import hashlib
import io
import json
import os
import sys
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest
import urllib.error
from unittest.mock import Mock, patch

import launch_mod as launcher


def config_bytes(value=None):
    return (json.dumps(launcher.make_config() if value is None else value, indent=2) + "\n").encode("utf-8")


class ConfigValidationTests(unittest.TestCase):
    def test_expected_config_and_extra_unrelated_key(self):
        value = dict(launcher.make_config(), test_extra="preserved")
        self.assertEqual(launcher.validate_config(config_bytes(value)), value)

    def test_generation_retains_all_pinned_fields_and_applies_only_mod_values(self):
        data = launcher.CONFIG_TEMPLATE_PATH.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), launcher.CONFIG_TEMPLATE_SHA256)
        original = json.loads(data)
        generated = launcher.make_config()
        self.assertEqual(len(original), 74)
        self.assertEqual(set(generated), set(original))
        for key, value in original.items():
            self.assertEqual(generated[key], launcher.CONFIG_DEFAULTS.get(key, value))
        self.assertEqual(launcher.validate_config(config_bytes(generated)), generated)

    def test_generation_returns_independent_lists(self):
        generated = launcher.make_config()
        generated["locale_replaces_with_ru"].append("fixture")
        self.assertNotIn("fixture", launcher.make_config()["locale_replaces_with_ru"])

    def test_sparse_override_document_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "required"):
            launcher.validate_config(config_bytes(launcher.CONFIG_DEFAULTS))

    def test_each_full_template_field_is_required(self):
        for key in launcher.make_config():
            value = launcher.make_config()
            del value[key]
            with self.subTest(missing=key), self.assertRaisesRegex(ValueError, "required"):
                launcher.validate_config(config_bytes(value))

    def test_each_full_template_field_type_is_checked(self):
        for key, default in launcher.make_config().items():
            value = launcher.make_config()
            value[key] = [123] if isinstance(default, list) else (True if type(default) is int else 42)
            with self.subTest(field=key), self.assertRaises(ValueError):
                launcher.validate_config(config_bytes(value))

    def test_each_required_value_is_checked_with_exact_type(self):
        for key, expected in launcher.CONFIG_DEFAULTS.items():
            candidates = [None, 0, 1, "ONEME", not expected if isinstance(expected, bool) else "other"]
            for candidate in candidates:
                with self.subTest(key=key, candidate=candidate):
                    value = launcher.make_config()
                    value[key] = candidate
                    with self.assertRaises(ValueError):
                        launcher.validate_config(config_bytes(value))
            value = launcher.make_config()
            del value[key]
            with self.subTest(key=key, missing=True), self.assertRaises(ValueError):
                launcher.validate_config(config_bytes(value))

    def test_invalid_json_types_constants_encoding_duplicates_and_size(self):
        valid = config_bytes().decode()
        cases = [b"not json", b"[]", b"null", b"\xff", b'{"x":NaN}',
                 (valid[:-2] + ', "app_folder_name": "ONEME"}\n').encode(),
                 b" " * (launcher.MAX_CONFIG_BYTES + 1)]
        for data in cases:
            with self.subTest(data=data[:100]), self.assertRaises(ValueError):
                launcher.validate_config(data)


class PackageOverlayTests(unittest.TestCase):
    def setUp(self):
        self.local = Path(r"C:\Users\fixture\AppData\Local")
        self.expected = self.local / "MXMOD" / "config.json"
        self.family = "Current.App_123456789abcd"
        self.actual = self.local / "Packages" / self.family / "LocalCache" / "Local" / "MXMOD" / "config.json"

    def allowed(self, actual, *, current=None, installed=False, expected=None):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.local)}), \
                patch.object(launcher, "_current_package_family_name", return_value=current), \
                patch.object(launcher, "_installed_codex_package", return_value=installed):
            return launcher._allowed_package_overlay(self.expected if expected is None else expected, actual)

    def test_current_package_exact_overlay_is_accepted(self):
        self.assertTrue(self.allowed(self.actual, current=self.family))

    def test_nested_oneme_directory_in_mxmod_is_valid_in_current_package(self):
        suffix = Path("MXMOD") / "cache" / "synthetic" / "ONEME"
        expected = self.local / suffix
        actual = self.local / "Packages" / self.family / "LocalCache" / "Local" / suffix
        self.assertTrue(self.allowed(actual, current=self.family, expected=expected))

    def test_nested_oneme_directory_requires_verified_codex_fallback(self):
        suffix = Path("MXMOD") / "cache" / "synthetic" / "ONEME"
        expected = self.local / suffix
        actual = self.local / "Packages" / launcher._CODEX_PACKAGE_FAMILY / "LocalCache" / "Local" / suffix
        self.assertTrue(self.allowed(actual, current=None, installed=True, expected=expected))
        self.assertFalse(self.allowed(actual, current=None, installed=False, expected=expected))

    def test_nested_oneme_does_not_authorize_foreign_root_suffix_or_family(self):
        suffix = Path("MXMOD") / "cache" / "synthetic" / "ONEME"
        expected = self.local / suffix
        package_root = self.local / "Packages" / self.family / "LocalCache" / "Local"
        actual = package_root / suffix
        cases = [
            (self.local / "ONEME" / "cache" / "synthetic" / "ONEME", actual),
            (self.local / "ONEME" / suffix, package_root / "ONEME" / suffix),
            (expected, package_root / "ONEME" / "cache" / "synthetic" / "ONEME"),
            (expected, actual.with_name("OTHER")),
            (expected, self.local / "Packages" / "Other.App_123456789abcd" / "LocalCache" / "Local" / suffix),
        ]
        for requested, final in cases:
            with self.subTest(requested=requested, final=final):
                self.assertFalse(self.allowed(final, current=self.family, installed=True, expected=requested))
        fallback = self.local / "Packages" / launcher._CODEX_PACKAGE_FAMILY / "LocalCache" / "Local"
        for final in (fallback / "ONEME" / "cache" / "synthetic" / "ONEME",
                      fallback / suffix / "unexpected",
                      self.local / "Packages" / "Other.App_123456789abcd" / "LocalCache" / "Local" / suffix):
            with self.subTest(fallback=final):
                self.assertFalse(self.allowed(final, current=None, installed=True, expected=expected))

    def test_foreign_family_wrong_tail_or_nonprofile_path_is_rejected(self):
        cases = [self.local / "Packages" / "Other.App_123456789abcd" / "LocalCache" / "Local" / "MXMOD" / "config.json",
                 self.actual.parent / "other.json", self.local / "elsewhere" / "MXMOD" / "config.json",
                 self.local / "Packages" / self.family / "LocalCache" / "Roaming" / "MXMOD" / "config.json"]
        for actual in cases:
            with self.subTest(actual=actual):
                self.assertFalse(self.allowed(actual, current=self.family, installed=True))
        self.assertFalse(self.allowed(self.actual, current=self.family, expected=self.local / "ONEME" / "config.json"))
        self.assertFalse(self.allowed(self.actual, current=self.family, expected=self.local / "MXMOD2" / "config.json"))

    def test_unpacked_codex_child_requires_exact_installed_codex_family(self):
        actual = self.local / "Packages" / launcher._CODEX_PACKAGE_FAMILY / "LocalCache" / "Local" / "MXMOD" / "config.json"
        self.assertTrue(self.allowed(actual, current=None, installed=True))
        self.assertFalse(self.allowed(actual, current=None, installed=False))
        self.assertFalse(self.allowed(self.actual, current=None, installed=True))
        self.assertFalse(self.allowed(actual, current=self.family, installed=True))

    def test_foreign_redirect_does_not_even_query_other_installed_packages(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.local)}), \
                patch.object(launcher, "_current_package_family_name", return_value=None), \
                patch.object(launcher, "_installed_codex_package", side_effect=AssertionError("unrelated package queried")):
            self.assertFalse(launcher._allowed_package_overlay(self.expected, self.actual))


class TemporaryProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mxmod-launcher-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.local = Path(self.temporary.name).resolve()
        self.root = self.local / "MXMOD"
        self.config = self.root / "config.json"

    def write_config(self, data=None):
        self.root.mkdir()
        self.config.write_bytes(config_bytes() if data is None else data)

    def test_check_only_missing_profile_performs_no_writes(self):
        with patch.object(launcher.Path, "mkdir", side_effect=AssertionError("unexpected mkdir")), \
                patch.object(launcher.os, "fsync", side_effect=AssertionError("unexpected write")):
            path, status = launcher.prepare_config(self.local, check_only=True)
        self.assertEqual(path, self.config)
        self.assertIn("absent", status)
        self.assertEqual(list(self.local.iterdir()), [])

    def test_check_only_existing_root_does_not_create_config(self):
        self.root.mkdir()
        path, status = launcher.prepare_config(self.local, check_only=True)
        self.assertEqual(path, self.config)
        self.assertIn("absent", status)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_first_prepare_creates_only_private_config(self):
        path, status = launcher.prepare_config(self.local)
        self.assertEqual(path, self.config)
        self.assertEqual(status, "created")
        self.assertEqual(json.loads(self.config.read_bytes()), launcher.make_config())
        self.assertEqual([item.name for item in self.local.iterdir()], ["MXMOD"])
        self.assertEqual([item.name for item in self.root.iterdir()], ["config.json"])

    def test_existing_valid_config_bytes_and_mtime_are_preserved(self):
        data = b"\xef\xbb\xbf" + config_bytes(dict(launcher.make_config(), extra={"custom": 3}))
        self.write_config(data)
        before = self.config.stat().st_mtime_ns
        for check_only in (True, False):
            _, status = launcher.prepare_config(self.local, check_only=check_only)
            self.assertEqual(status, "existing and validated")
            self.assertEqual(self.config.read_bytes(), data)
            self.assertEqual(self.config.stat().st_mtime_ns, before)

    def test_existing_unsafe_config_is_not_replaced(self):
        for key in ("app_folder_name", "single_instance_id", "local_server_pipe_name",
                    "auto_update_enabled", "rooms_auto_update_enabled", "need_mask_logs",
                    "send_error_logs_to_crash_server"):
            value = launcher.make_config()
            value[key] = True if key.endswith("enabled") or key.startswith("send_") else "unsafe"
            if not self.root.exists():
                self.root.mkdir()
            data = config_bytes(value)
            self.config.write_bytes(data)
            for check_only in (False, True):
                with self.subTest(key=key, check_only=check_only), self.assertRaises(ValueError):
                    launcher.prepare_config(self.local, check_only=check_only)
                self.assertEqual(self.config.read_bytes(), data)

    def test_existing_invalid_json_is_not_replaced(self):
        data = b"{broken JSON"
        self.write_config(data)
        with self.assertRaises(ValueError):
            launcher.prepare_config(self.local)
        self.assertEqual(self.config.read_bytes(), data)

    def test_existing_sparse_config_is_rejected_and_preserved(self):
        data = config_bytes(launcher.CONFIG_DEFAULTS)
        self.write_config(data)
        for check_only in (False, True):
            with self.subTest(check_only=check_only), self.assertRaisesRegex(ValueError, "required"):
                launcher.prepare_config(self.local, check_only=check_only)
            self.assertEqual(self.config.read_bytes(), data)

    def test_missing_or_modified_template_stops_before_profile_creation(self):
        template = self.local / "template-fixture.json"
        for data in (None, b"{}\n"):
            if data is not None:
                template.write_bytes(data)
            with patch.object(launcher, "CONFIG_TEMPLATE_PATH", template):
                for check_only in (False, True):
                    with self.subTest(data=data, check_only=check_only), self.assertRaises((OSError, ValueError)):
                        launcher.prepare_config(self.local, check_only=check_only)
            self.assertFalse(self.root.exists())

    def test_config_hard_link_is_rejected_without_reading_content(self):
        self.root.mkdir()
        external = self.local / "fixture-data.json"
        external.write_bytes(config_bytes())
        os.link(external, self.config)
        with patch.object(launcher, "_read_config", side_effect=AssertionError("hardlink content opened")):
            with self.assertRaisesRegex(ValueError, "one link"):
                launcher.prepare_config(self.local)
        self.assertEqual(external.read_bytes(), config_bytes())

    def test_profile_root_as_file_is_rejected(self):
        self.root.write_bytes(b"not a directory")
        with self.assertRaisesRegex(ValueError, "directory"):
            launcher.prepare_config(self.local)
        self.assertEqual(self.root.read_bytes(), b"not a directory")

    def test_config_as_directory_is_rejected(self):
        self.root.mkdir()
        self.config.mkdir()
        with self.assertRaisesRegex(ValueError, "regular file"):
            launcher.prepare_config(self.local)

    def test_reparse_root_is_rejected_before_config_read_or_write(self):
        self.root.mkdir()
        actual_lstat = Path.lstat

        def fake_lstat(path, *args, **kwargs):
            info = actual_lstat(path, *args, **kwargs)
            if path == self.root:
                return types.SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
            return info

        with patch.object(Path, "lstat", fake_lstat), \
                patch.object(launcher, "_config_stream", side_effect=AssertionError("config opened")):
            with self.assertRaisesRegex(ValueError, "Reparse"):
                launcher.prepare_config(self.local)
        self.assertEqual(list(self.root.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows overlay handle semantics")
    def test_overlay_physical_ancestors_are_checked_before_config_open(self):
        self.root.mkdir()
        physical = self.local / "Packages" / launcher._CODEX_PACKAGE_FAMILY / "LocalCache" / "Local" / "MXMOD"
        physical.mkdir(parents=True)
        real_validate = launcher._validate_handle
        real_check = launcher._check_path
        checked = []

        def validate(api, handle, path, *, directory):
            result = real_validate(api, handle, path, directory=directory)
            return physical if path == self.root else result

        def check(path, *, directory):
            checked.append(path)
            if path == physical.parent:
                raise ValueError("fixture physical reparse ancestor")
            return real_check(path, directory=directory)

        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.local)}), \
                patch.object(launcher, "_validate_handle", side_effect=validate), \
                patch.object(launcher, "_check_path", side_effect=check), \
                patch.object(launcher, "_config_stream", side_effect=AssertionError("config opened before physical checks")):
            with self.assertRaisesRegex(ValueError, "physical reparse"):
                launcher.prepare_config(self.local)
        self.assertIn(self.local / "Packages", checked)
        self.assertIn(physical.parent, checked)

    def test_symlink_root_is_rejected_without_following_it(self):
        target = self.local / "fixture-other"
        target.mkdir()
        try:
            self.root.symlink_to(target, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"OS cannot create fixture symlink: {error}")
        with self.assertRaisesRegex(ValueError, "Reparse|symlink"):
            launcher.prepare_config(self.local)
        self.assertEqual(list(target.iterdir()), [])

    def test_missing_relative_or_official_named_local_root_is_rejected(self):
        for value in (Path("relative"), self.local / "missing", self.local / "ONEME"):
            with self.subTest(path=value), self.assertRaises((ValueError, FileNotFoundError)):
                launcher.prepare_config(value)
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, "LOCALAPPDATA"):
            launcher.prepare_config()

    @unittest.skipUnless(os.name == "nt", "Windows directory handle semantics")
    def test_directory_handle_blocks_rename_while_held(self):
        directory = self.local / "held"
        directory.mkdir()
        with launcher._hold_directory(directory):
            # Child creation is permitted, parent rename and write handles are not.
            (directory / "child").mkdir()
            with self.assertRaises(OSError):
                directory.rename(self.local / "renamed")
        directory.rename(self.local / "renamed")

    @unittest.skipUnless(os.name == "nt", "Windows directory handle semantics")
    def test_directory_handle_blocks_reparse_write_access(self):
        directory = self.local / "held"
        directory.mkdir()
        api = launcher._windows_api()
        with launcher._hold_directory(directory):
            handle = api.CreateFileW(str(directory), 0x40000000, 7, None, 3, 0x02000000, None)
            if handle != launcher.ctypes.c_void_p(-1).value:
                api.CloseHandle(handle)
                self.fail("Directory could be opened for junction-changing write access")
            self.assertEqual(launcher.ctypes.get_last_error(), 32)  # ERROR_SHARING_VIOLATION


class HelperTemporaryDirectoryTests(unittest.TestCase):
    def test_runtime_volume_is_used_without_changing_user_or_system_settings(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"TMP": "synthetic-before", "TEMP": "synthetic-before"}):
            runtime = Path(temporary).resolve() / "runtime" / "mod"
            runtime.mkdir(parents=True)
            target = launcher._prepare_helper_temp(runtime)
            self.assertEqual(target, runtime.parent / ".mxmod-helper-temp")
            self.assertEqual(os.environ["TMP"], str(target))
            self.assertEqual(os.environ["TEMP"], str(target))
            self.assertEqual(launcher._prepare_helper_temp(runtime), target)

    def test_refused_directory_does_not_change_helper_environment(self):
        with patch.dict(os.environ, {"TMP": "synthetic-before", "TEMP": "synthetic-before"}), \
                patch.object(launcher, "_hold_directory", side_effect=ValueError("unsafe")):
            with self.assertRaises(ValueError):
                launcher._prepare_helper_temp(Path(r"D:\synthetic\runtime\mod"))
            self.assertEqual(os.environ["TMP"], "synthetic-before")
            self.assertEqual(os.environ["TEMP"], "synthetic-before")


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mxmod-manifest-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.executable = self.root / "max.exe"
        self.executable.write_bytes(b"synthetic; never executed")
        self.path = self.root / "mod-manifest.json"
        self.path.write_text("{}", encoding="utf-8")
        self.specs = [{"relative_path": "max.exe", "sha256": "synthetic-source-hash", "patches": [
            {"id": "fixture", "offset": 3, "expected": "ab", "replacement": "cd"}]}]
        self.manifest = {"format": 1, "max_version": "26.35.1.80662", "destination": str(self.root),
                         "components": list(launcher.REQUIRED_COMPONENTS), "files": copy.deepcopy(self.specs)}

    def verify_with(self, value):
        with patch.object(launcher.build_mod, "verify", return_value=value) as verify, \
                patch.object(launcher.build_mod, "specifications", return_value=self.specs):
            result = launcher.verified_executable(self.path)
            verify.assert_called_once_with(self.path)
            return result

    def test_full_specs_and_components_are_required(self):
        self.assertEqual(self.verify_with(self.manifest), self.executable)

    def test_portable_manifest_is_anchored_to_its_actual_parent(self):
        value = copy.deepcopy(self.manifest)
        value["destination"] = "."
        self.assertEqual(self.verify_with(value), self.executable)
        for destination in ("..", "./", "runtime", "./runtime", "", None):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                value["destination"] = destination
                self.verify_with(value)
        value["destination"] = "."
        value["files"][0]["patches"] = []
        with self.assertRaises(ValueError):
            self.verify_with(value)

    def test_false_components_label_or_missing_patch_cannot_enable_launch(self):
        cases = []
        for components in (["accounts"], ["accounts", "privacy", "privacy"], ["accounts", "privacy", "unknown"]):
            value = copy.deepcopy(self.manifest)
            value["components"] = components
            cases.append(value)
        for field, replacement in (("files", []), ("destination", str(self.root.parent)), ("max_version", "other")):
            value = copy.deepcopy(self.manifest)
            value[field] = replacement
            cases.append(value)
        value = copy.deepcopy(self.manifest)
        value["files"][0]["patches"] = []
        cases.append(value)
        value = copy.deepcopy(self.manifest)
        value["files"][0]["patches"][0]["replacement"] = "ef"
        cases.append(value)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.verify_with(value)


class MainTests(unittest.TestCase):
    def setUp(self):
        # These tests mock every profile/process operation. They must not
        # contend with a real GUI transfer's cross-process mutex.
        transfer = patch("session_transfer.transfer_lock", side_effect=lambda: nullcontext())
        transfer.start()
        self.addCleanup(transfer.stop)
        marker = patch.object(launcher.desktop_process, "is_desktop_child", return_value=True)
        marker.start()
        self.addCleanup(marker.stop)
        temporary = patch.object(launcher, "_prepare_helper_temp")
        self.temporary = temporary.start()
        self.addCleanup(temporary.stop)
        # The adapter's verified_executable is synthetic. Never let a mocked
        # main call claim the real default manifest's startup receipt.
        receipts = patch.object(launcher.startup_status, "begin")
        receipts.start()
        self.addCleanup(receipts.stop)
        # Optimization activation and OS identity are also mocked: these main
        # adapter tests must never create their literal synthetic directories.
        optimization_store = patch("optimization_store.OptimizationSettingsStore")
        optimization_store.start()
        self.addCleanup(optimization_store.stop)
        optimization_process = patch("optimization_store.native_client_running", return_value=False)
        optimization_process.start()
        self.addCleanup(optimization_process.stop)

    def test_check_only_never_calls_popen_or_prepare_in_write_mode(self):
        executable = Path(r"D:\synthetic\max.exe")
        with patch.object(launcher, "verified_executable", return_value=executable), \
                patch.object(launcher, "prepare_config", return_value=(Path(r"D:\synthetic\MXMOD\config.json"), "absent")) as prepare, \
                patch.object(launcher, "_privacy_ui_enabled", side_effect=AssertionError("manifest reread")), \
                patch.object(launcher, "_diagnostics_identity", side_effect=AssertionError("HTTP requested")), \
                patch.object(launcher, "_quiet_process", side_effect=AssertionError("process started")), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(launcher.main(["--check-only"]), 0)
            prepare.assert_called_once_with(check_only=True)
            self.temporary.assert_not_called()
            self.assertIn(str(executable), output.getvalue())

    def test_launch_uses_argument_list_and_executable_directory(self):
        executable = Path(r"D:\synthetic path\max.exe")
        native_env = {"SYNTHETIC": "1", "WEBVIEW2_USER_DATA_FOLDER": r"D:\synthetic\MXMOD\business-webview2"}
        with patch.object(launcher, "verified_executable", return_value=executable), \
                patch.object(launcher, "prepare_config", return_value=(Path(r"D:\synthetic\MXMOD\config.json"), "created")) as prepare, \
                patch.object(launcher, "_privacy_ui_enabled", return_value=False), \
                patch("external_environment.native_environment", side_effect=lambda: nullcontext({"SYNTHETIC": "1"})), \
                patch("business_environment.child_environment", return_value=native_env), \
                patch.object(launcher.desktop_process, "popen", return_value=types.SimpleNamespace(pid=123)) as popen, \
                patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(launcher.main([]), 0)
            self.assertEqual(prepare.call_args_list, [unittest.mock.call(check_only=False), unittest.mock.call(check_only=True)])
            popen.assert_called_once_with([str(executable)], cwd=executable.parent,
                                         env={**native_env, "QML_XHR_ALLOW_FILE_READ": "1"})

    def test_ui_build_starts_diagnostics_only_after_config_validation(self):
        executable = Path(r"D:\synthetic path\max.exe")
        order = []
        with patch.object(launcher, "verified_executable", return_value=executable), \
                patch.object(launcher, "prepare_config", side_effect=lambda **kwargs: (order.append("config") or (Path(r"D:\synthetic\MXMOD\config.json"), "valid"))), \
                patch.object(launcher, "_privacy_ui_enabled", return_value=True), \
                patch.object(launcher, "launch_diagnostics", side_effect=lambda *args: order.append("diagnostics") or "started") as diagnostics, \
                patch.object(launcher, "_quiet_process", side_effect=AssertionError("direct MAX spawn")), \
                patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(launcher.main([]), 0)
            diagnostics.assert_called_once_with(launcher.DEFAULT_MANIFEST, executable)
            self.assertEqual(order, ["config", "diagnostics"])
        self.assertEqual(launcher.DEFAULT_MANIFEST.parent.name, "valmax-live-v8")

    def test_without_diagnostics_uses_quiet_direct_launch_and_no_http(self):
        executable = Path(r"D:\synthetic path\max.exe")
        native_env = {"SYNTHETIC": "1", "WEBVIEW2_USER_DATA_FOLDER": r"D:\synthetic\MXMOD\business-webview2"}
        with patch.object(launcher, "verified_executable", return_value=executable), \
                patch.object(launcher, "prepare_config", return_value=(Path(r"D:\synthetic\MXMOD\config.json"), "valid")), \
                patch.object(launcher, "_privacy_ui_enabled", side_effect=AssertionError("unneeded manifest read")), \
                patch.object(launcher, "launch_diagnostics", side_effect=AssertionError("diagnostics launched")), \
                patch("external_environment.native_environment", side_effect=lambda: nullcontext({"SYNTHETIC": "1"})), \
                patch("business_environment.child_environment", return_value=native_env), \
                patch.object(launcher.desktop_process, "popen", return_value=types.SimpleNamespace(pid=123)) as popen, \
                patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(launcher.main(["--without-diagnostics"]), 0)
            self.assertEqual(popen.call_args.args[0], [str(executable)])
            self.assertEqual(popen.call_args.kwargs, {"cwd": executable.parent,
                                                    "env": {**native_env, "QML_XHR_ALLOW_FILE_READ": "1"}})

    def test_repeat_shortcut_activates_existing_client_without_spawn(self):
        executable = Path(r"D:\synthetic path\max.exe")
        for activated in (True, False):
            with self.subTest(activated=activated), \
                    patch.object(launcher, "verified_executable", return_value=executable), \
                    patch.object(launcher, "prepare_config", return_value=(Path(r"D:\synthetic\MXMOD\config.json"), "valid")), \
                    patch.object(launcher, "_privacy_ui_enabled", return_value=True), \
                    patch.object(launcher, "launch_diagnostics", return_value="already running"), \
                    patch("client_activation.activate", return_value=activated) as activate, \
                    patch.object(launcher, "_quiet_process", side_effect=AssertionError("duplicate native spawn")), \
                    patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(launcher.main([]), 0 if activated else 1)
                activate.assert_called_once_with(executable)
                if not activated:
                    self.assertEqual(launcher.startup_status.failure_code(), "window_activation_failed")

    def test_without_helper_cannot_silently_disable_enabled_retention(self):
        args = types.SimpleNamespace(without_diagnostics=True)
        root = Path(r"D:\synthetic\MXMOD")
        with patch("ghost_store.GhostSettingsStore") as ghost, \
                patch("message_retention_service.RetentionSettingsStore") as retention, \
                patch.object(launcher, "_quiet_process") as spawn, \
                patch.object(launcher, "launch_diagnostics") as diagnostics:
            ghost.return_value.snapshot.return_value = {"settings": {"enabled": False}}
            retention.return_value.snapshot.return_value = {"enabled": True}
            with self.assertRaisesRegex(ValueError, "Сохранение удалённых сообщений требует помощника"):
                launcher._start_verified(args, root / "max.exe", root)
            spawn.assert_not_called()
            diagnostics.assert_not_called()

    def test_desktop_relaunch_happens_before_config_or_recovery_access(self):
        executable = Path(r"D:\synthetic\max.exe")
        child = types.SimpleNamespace(wait=lambda timeout: 0)
        with patch.object(launcher.desktop_process, "is_desktop_child", return_value=False), \
                patch.object(launcher, "verified_executable", return_value=executable), \
                patch.object(launcher, "prepare_config", side_effect=AssertionError("profile touched")), \
                patch.object(launcher, "_quiet_process", return_value=child) as quiet, \
                patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(launcher.main([]), 0)
            self.assertEqual(quiet.call_args.args[0], launcher.helper_command("launcher", ["--manifest", str(launcher.DEFAULT_MANIFEST)]))
            self.assertTrue(quiet.call_args.kwargs["parent_waits"])
            self.assertEqual(launcher.main(["--recover-mdata"]), 0)
            self.assertFalse(quiet.call_args.kwargs["parent_waits"])

    def test_desktop_relaunch_resolves_manifest_before_changing_child_directory(self):
        with tempfile.TemporaryDirectory() as temporary, chdir(temporary):
            manifest = Path(temporary) / "mod-manifest.json"
            manifest.write_text("{}", encoding="utf-8")
            relative = os.path.relpath(manifest)
            child = types.SimpleNamespace(wait=lambda timeout: 0)
            with patch.object(launcher.desktop_process, "is_desktop_child", return_value=False), \
                    patch.object(launcher, "verified_executable", return_value=Path(r"D:\different\max.exe")), \
                    patch.object(launcher, "prepare_config", side_effect=AssertionError("profile touched")), \
                    patch.object(launcher, "_quiet_process", return_value=child) as quiet, \
                    patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(launcher.main(["--manifest", relative]), 0)
                command = quiet.call_args.args[0]
                self.assertEqual(command[command.index("--manifest") + 1], str(manifest.resolve()))

    def test_import_mdata_opens_explicit_gui_before_any_profile_or_client_start(self):
        executable = Path(r"D:\synthetic\max.exe")
        manifest = Path(r"D:\synthetic\mod-manifest.json")
        gui = Mock(return_value=0)
        with patch.dict(sys.modules, {"mdata_import": types.SimpleNamespace(run_gui=gui)}), \
                patch.object(launcher, "verified_executable", return_value=executable), \
                patch.object(launcher, "prepare_config", side_effect=AssertionError("profile touched before click")), \
                patch.object(launcher, "launch_diagnostics", side_effect=AssertionError("client started before click")), \
                patch("mdata_recovery.launch_quietly") as restart, \
                patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(launcher.main(["--manifest", str(manifest), "--import-mdata"]), 0)
            gui.assert_called_once()
            restart.assert_not_called()
            gui.call_args.kwargs["launch"]()
            restart.assert_called_once_with(manifest=manifest.resolve())

    def test_import_mdata_desktop_bootstrap_does_not_wait_for_gui_completion(self):
        child = types.SimpleNamespace(wait=Mock(side_effect=AssertionError("GUI wait")))
        with patch.object(launcher.desktop_process, "is_desktop_child", return_value=False), \
                patch.object(launcher, "verified_executable", return_value=Path(r"D:\synthetic\max.exe")), \
                patch.object(launcher, "prepare_config", side_effect=AssertionError("profile touched")), \
                patch.object(launcher, "_quiet_process", return_value=child) as quiet, \
                patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(launcher.main(["--import-mdata"]), 0)
            self.assertIn("--import-mdata", quiet.call_args.args[0])
            self.assertFalse(quiet.call_args.kwargs["parent_waits"])
            child.wait.assert_not_called()

    def test_bad_config_stops_before_http_or_process_start(self):
        with patch.object(launcher, "verified_executable", return_value=Path(r"D:\synthetic\max.exe")), \
                patch.object(launcher, "prepare_config", side_effect=ValueError("unsafe fixture")), \
                patch.object(launcher, "launch_diagnostics", side_effect=AssertionError("diagnostics called")), \
                patch.object(launcher, "_quiet_process", side_effect=AssertionError("process started")), \
                patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(launcher.main([]), 1)

    def test_diagnostics_failure_has_no_automatic_direct_launch_fallback(self):
        with patch.object(launcher, "verified_executable", return_value=Path(r"D:\synthetic\max.exe")), \
                patch.object(launcher, "prepare_config", return_value=(Path(r"D:\synthetic\MXMOD\config.json"), "valid")), \
                patch.object(launcher, "_privacy_ui_enabled", return_value=True), \
                patch.object(launcher, "launch_diagnostics", side_effect=ValueError("unavailable fixture")), \
                patch.object(launcher, "_quiet_process", side_effect=AssertionError("fallback MAX spawn")), \
                patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(launcher.main([]), 1)

    def test_verification_failure_never_prepares_config_or_starts_process(self):
        with patch.object(launcher, "verified_executable", side_effect=ValueError("fixture mismatch")), \
                patch.object(launcher, "prepare_config", side_effect=AssertionError("config touched")), \
                patch.object(launcher, "_quiet_process", side_effect=AssertionError("process started")), \
                patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(launcher.main([]), 1)


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mxmod-diagnostics-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.manifest = self.root / "mod-manifest.json"
        self.manifest.write_bytes(b'{"components":["ui_privacy"]}\n')
        self.executable = self.root / "max.exe"
        self.expected = launcher.runtime_identity(self.manifest)

    def identity(self, state="running", **extra):
        return dict({"schema": 1, "kind": "max-mod-privacy-server", "runtimeId": self.expected,
                     "serverPid": 123, "clientState": state, "processes": 1 if state == "running" else 0}, **extra)

    def test_runtime_identity_uses_exact_bytes_and_resolved_normalized_parent(self):
        expected = hashlib.sha256(launcher._normalized(self.root).encode("utf-8") + b"\0" + self.manifest.read_bytes()).hexdigest()
        self.assertEqual(self.expected, expected)
        self.manifest.write_bytes(self.manifest.read_bytes() + b" ")
        self.assertNotEqual(launcher.runtime_identity(self.manifest), expected)
        other = self.root / "other"
        other.mkdir()
        moved = other / self.manifest.name
        moved.write_bytes(b'{"components":["ui_privacy"]}\n')
        self.assertNotEqual(launcher.runtime_identity(moved), expected)

    def test_existing_same_runtime_running_or_starting_never_spawns(self):
        for state in ("running", "starting"):
            with self.subTest(state=state), \
                    patch.object(launcher, "_diagnostics_identity", side_effect=[self.identity(state), self.identity()]), \
                    patch.object(launcher.time, "sleep"), \
                    patch.object(launcher, "_quiet_process", side_effect=AssertionError("duplicate MAX/server spawn")):
                self.assertEqual(launcher.launch_diagnostics(self.manifest, self.executable), "already running")

    def test_idle_or_other_runtime_refuses_without_spawn(self):
        for value in (self.identity("idle"), self.identity(runtimeId="0" * 64)):
            with self.subTest(value=value), \
                    patch.object(launcher, "_diagnostics_identity", return_value=value), \
                    patch.object(launcher, "DIAGNOSTICS_IDLE_TIMEOUT", .01), \
                    patch.object(launcher.time, "sleep"), \
                    patch.object(launcher, "_quiet_process", side_effect=AssertionError("duplicate MAX/server spawn")):
                with self.assertRaises(ValueError):
                    launcher.launch_diagnostics(self.manifest, self.executable)

    def test_server_command_is_separate_python_hidden_with_no_inherited_stdio(self):
        process = types.SimpleNamespace(pid=123, poll=lambda: None)
        with patch.object(launcher, "_diagnostics_identity", side_effect=[None, self.identity("starting"), self.identity()]), \
                patch.object(launcher, "_quiet_process", return_value=process) as popen, \
                patch.object(launcher.time, "sleep"):
            self.assertEqual(launcher.launch_diagnostics(self.manifest, self.executable), "started")
        self.assertEqual(popen.call_count, 1)
        command = popen.call_args.args[0]
        self.assertEqual(command, [launcher.sys.executable, "-B", str(Path(launcher.__file__).resolve().with_name("privacy_server.py")),
                                  "--manifest", str(self.manifest), "--launch", "--idle-seconds", "20"])
        self.assertNotIn(str(self.executable), command)
        self.assertEqual(popen.call_args.args[1], self.root)
        self.assertEqual(popen.call_args.kwargs, {})

    def test_server_failure_or_deadline_does_not_spawn_direct_max(self):
        for stopped in (True, False):
            process = types.SimpleNamespace(pid=123, poll=lambda: 1 if stopped else None)
            with self.subTest(stopped=stopped), \
                    patch.object(launcher, "_diagnostics_identity", return_value=None), \
                    patch.object(launcher, "_quiet_process", return_value=process) as popen, \
                    patch.object(launcher, "DIAGNOSTICS_START_TIMEOUT", .01), \
                    patch.object(launcher.time, "sleep"):
                with self.assertRaises(ValueError):
                    launcher.launch_diagnostics(self.manifest, self.executable)
                self.assertEqual(popen.call_count, 1)

    def test_python_redirector_pid_differs_but_waits_until_server_running(self):
        process = types.SimpleNamespace(pid=456, poll=lambda: None)
        with patch.object(launcher, "_diagnostics_identity", side_effect=[None, self.identity("starting"), self.identity()]) as identity, \
                patch.object(launcher, "_quiet_process", return_value=process) as popen, \
                patch.object(launcher.time, "sleep"):
            self.assertEqual(launcher.launch_diagnostics(self.manifest, self.executable), "started")
            self.assertEqual(identity.call_count, 3)
            self.assertEqual(popen.call_count, 1)

    def test_concurrent_launch_loser_waits_for_other_server_running_without_another_spawn(self):
        process = types.SimpleNamespace(pid=456, poll=lambda: 1)
        with patch.object(launcher, "_diagnostics_identity", side_effect=[None, self.identity("starting"), self.identity()]) as identity, \
                patch.object(launcher, "_quiet_process", return_value=process) as popen, \
                patch.object(launcher.time, "sleep"):
            self.assertEqual(launcher.launch_diagnostics(self.manifest, self.executable), "started")
            self.assertEqual(identity.call_count, 3)
            self.assertEqual(popen.call_count, 1)

    def test_exited_child_with_matching_identity_starting_times_out_without_fallback(self):
        process = types.SimpleNamespace(pid=123, poll=lambda: 1)
        with patch.object(launcher, "_diagnostics_identity", side_effect=lambda **kwargs: None if not kwargs else self.identity("starting")), \
                patch.object(launcher, "_quiet_process", return_value=process) as popen, \
                patch.object(launcher, "DIAGNOSTICS_START_TIMEOUT", .01), \
                patch.object(launcher.time, "sleep"):
            with self.assertRaises(ValueError):
                launcher.launch_diagnostics(self.manifest, self.executable)
            self.assertEqual(popen.call_count, 1)

    def read(self, raw=None, *, error=None):
        # Magic context methods need a class rather than instance attributes.
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read(self, size): return raw[:size]
        opener = types.SimpleNamespace(open=lambda *args, **kwargs: Response())
        if error is not None:
            def fail(*args, **kwargs): raise error
            opener.open = fail
        with patch.object(launcher.urllib.request, "build_opener", return_value=opener) as builder:
            result = launcher._read_diagnostics_identity(1.0)
            self.assertEqual(builder.call_args.args[0].proxies, {})
            self.assertIsNone(builder.call_args.args[1].redirect_request(None, None, None, None, None, None))
            return result

    def test_http_identity_is_bounded_strict_and_cannot_redirect_or_use_proxies(self):
        self.assertEqual(self.read(json.dumps(self.identity()).encode()), self.identity())
        for raw in (b"[]", b"{}", b"x" * (launcher.MAX_IDENTITY_BYTES + 1),
                    json.dumps(self.identity(schema=True)).encode(),
                    json.dumps(self.identity(serverPid="secret")).encode(),
                    json.dumps(self.identity(runtimeId="secret")).encode(),
                    json.dumps(self.identity(processes=-1)).encode(),
                    json.dumps(self.identity(clientState="unknown")).encode()):
            with self.subTest(raw=raw[:40]), self.assertRaises(ValueError):
                self.read(raw)
        self.assertIsNone(self.read(error=urllib.error.URLError(ConnectionRefusedError(10061, "fixture"))))
        with self.assertRaises(ValueError):
            self.read(error=urllib.error.HTTPError("http://127.0.0.1", 302, "fixture", {}, None))

    def test_total_http_deadline_does_not_wait_for_slow_body_or_late_completion(self):
        release = threading.Event()
        finished = threading.Event()
        def blocked(timeout):
            release.wait(2)
            finished.set()
            return self.identity()
        with patch.object(launcher.socket, "socket") as socket_factory, \
                patch.object(launcher, "_read_diagnostics_identity", side_effect=blocked):
            socket_factory.return_value.__enter__.return_value.bind.side_effect = OSError(10048, "fixture in-use")
            started = time.monotonic()
            with self.assertRaises(ValueError):
                launcher._diagnostics_identity(.03)
            self.assertLess(time.monotonic() - started, .5)
            release.set()
            self.assertTrue(finished.wait(1))

    def test_free_port_returns_none_without_http_and_closes_exclusive_probe(self):
        with patch.object(launcher.socket, "socket") as factory, \
                patch.object(launcher, "_read_diagnostics_identity", side_effect=AssertionError("HTTP attempted on free port")):
            self.assertIsNone(launcher._diagnostics_identity())
            factory.assert_called_once_with(launcher.socket.AF_INET, launcher.socket.SOCK_STREAM)
            probe = factory.return_value.__enter__.return_value
            probe.setsockopt.assert_called_once_with(launcher.socket.SOL_SOCKET, launcher.socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind.assert_called_once_with(("127.0.0.1", launcher.DIAGNOSTICS_PORT))
            factory.return_value.__exit__.assert_called_once()

    def test_occupied_port_uses_bounded_http_and_other_bind_errors_refuse(self):
        for error, accepted in ((OSError(10048, "fixture in-use"), True),
                                (PermissionError(10013, "fixture exclusive in-use"), True),
                                (OSError(10022, "fixture invalid"), False)):
            with self.subTest(accepted=accepted), patch.object(launcher.socket, "socket") as factory, \
                    patch.object(launcher, "_read_diagnostics_identity", return_value=self.identity()) as read:
                factory.return_value.__enter__.return_value.bind.side_effect = error
                if accepted:
                    self.assertEqual(launcher._diagnostics_identity(), self.identity())
                    read.assert_called_once_with(1.0)
                else:
                    with self.assertRaises(ValueError):
                        launcher._diagnostics_identity()
                    read.assert_not_called()
                factory.return_value.__exit__.assert_called_once()

    def test_exclusive_or_access_denied_port_without_identity_never_permits_spawn(self):
        for code in (10013, 10048):
            with self.subTest(code=code), patch.object(launcher.socket, "socket") as factory, \
                    patch.object(launcher, "_read_diagnostics_identity", return_value=None), \
                    patch.object(launcher, "_quiet_process", side_effect=AssertionError("server/MAX spawn after occupied probe")):
                factory.return_value.__enter__.return_value.bind.side_effect = OSError(code, "fixture occupied")
                with self.assertRaises(ValueError):
                    launcher.launch_diagnostics(self.manifest, self.executable)


if __name__ == "__main__":
    unittest.main()
