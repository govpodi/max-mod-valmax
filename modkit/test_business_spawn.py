"""Synthetic native/observer spawn integration; no MAX process or real profile.

Only temporary MXMOD directories on the source workspace volume are used.
Native process creation, Frida attachment and prepared-config lookup are mocks.
The real environment/directory adapters still validate the copied child state.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import business_environment
import external_environment
import helper_command
import launch_mod
from privacy_server import Monitor


_NATIVE_ENVIRONMENT = external_environment.native_environment


def environment_fingerprint():
    # A failed assertion must never print the caller's actual environment.
    return hashlib.sha256(json.dumps(dict(os.environ), sort_keys=True,
                                     ensure_ascii=False).encode("utf8")).hexdigest()


class FakeDllDirectory:
    def __init__(self):
        self.value = "synthetic previous DLL directory"
        self.calls = []

    def get(self):
        self.calls.append(("get", self.value))
        return self.value

    def set(self, value):
        self.calls.append(("set", value))
        self.value = value


class BusinessSpawnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="business-spawn-test-",
                                                dir=Path(__file__).resolve().parent.parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / "MXMOD"
        self.profile.mkdir()
        self.config = self.profile / "config.json"
        self.config.write_bytes(b"synthetic prepared config; never parsed")
        self.exe = self.root / "application with spaces" / "max.exe"
        self.bundle = self.root / "frozen bundle"
        self.external = self.root / "external tools"
        self.source_env = {
            "PATH": os.pathsep.join(map(str, (self.bundle, self.bundle / "plugins", self.external))),
            "SYNTHETIC": "preserved",
            "WEBVIEW2_USER_DATA_FOLDER": "synthetic foreign profile",
            "webview2_user_data_folder": "synthetic alternate casing",
            "WEBVIEW2_BROWSER_EXECUTABLE_FOLDER": "synthetic selected WebView2 runtime",
        }
        self.dll = FakeDllDirectory()
        self.original_env = environment_fingerprint()
        self.original_source = dict(self.source_env)
        # Any accidental use of a real device is a test error before a spawn.
        deny_device = patch("frida.get_local_device", side_effect=AssertionError("Real Frida device forbidden"))
        deny_device.start()
        self.addCleanup(deny_device.stop)

    @contextmanager
    def native_environment(self, frozen):
        with _NATIVE_ENVIRONMENT(frozen=frozen, bundle_directory=self.bundle,
                                 environment=self.source_env, dll_api=self.dll) as environment:
            yield environment

    def assert_child_environment(self, environment, *, frozen, direct):
        self.assertIsNot(environment, self.source_env)
        self.assertEqual(environment[business_environment.VARIABLE],
                         str(self.profile / business_environment.DIRECTORY))
        self.assertEqual([business_environment.VARIABLE],
                         [key for key in environment if key.casefold() == business_environment.VARIABLE.casefold()])
        expected_path = str(self.external) if frozen else self.source_env["PATH"]
        self.assertEqual(environment["PATH"], expected_path)
        self.assertEqual(environment["SYNTHETIC"], "preserved")
        self.assertEqual(environment["WEBVIEW2_BROWSER_EXECUTABLE_FOLDER"],
                         "synthetic selected WebView2 runtime")
        self.assertEqual(environment.get("PYINSTALLER_RESET_ENVIRONMENT"), "1" if frozen and direct else None)
        self.assertEqual(self.source_env, self.original_source)
        self.assertEqual(environment_fingerprint(), self.original_env)
        self.assertEqual(self.config.read_bytes(), b"synthetic prepared config; never parsed")
        self.assertTrue((self.profile / business_environment.DIRECTORY).is_dir())
        self.assertEqual(self.dll.value, "synthetic previous DLL directory")
        self.assertEqual(self.dll.calls, [("get", "synthetic previous DLL directory"),
                                         ("set", None), ("set", "synthetic previous DLL directory")] if frozen else [])

    def monitor(self):
        monitor = Monitor.__new__(Monitor)
        monitor.device = Mock()
        monitor.device.spawn.return_value = 4242
        monitor.attach = Mock()
        monitor.activity_lock = threading.Lock()
        monitor.client_pids = {99}
        monitor.main_client_pids = {99}
        monitor.profile_pids = {99}
        return monitor

    def test_direct_native_source_and_frozen_children_use_prepared_config_parent_only(self):
        for frozen in (False, True):
            with self.subTest(frozen=frozen):
                self.dll = FakeDllDirectory()
                command = [str(self.exe), "synthetic argument with spaces"]
                original_command = list(command)
                child = SimpleNamespace(pid=4242)
                with patch.object(sys, "frozen", frozen, create=True), \
                        patch.object(external_environment, "native_environment", side_effect=lambda: self.native_environment(frozen)), \
                        patch.object(launch_mod, "prepare_config", return_value=(self.config, "synthetic verified")) as prepare, \
                        patch.object(launch_mod.desktop_process, "popen", return_value=child) as spawn:
                    self.assertIs(launch_mod._quiet_process(command, self.exe.parent), child)
                prepare.assert_called_once_with(check_only=True)
                spawn.assert_called_once()
                self.assertEqual(spawn.call_args.args, (original_command,))
                self.assertEqual(command, original_command)
                self.assertEqual(spawn.call_args.kwargs["cwd"], self.exe.parent)
                self.assertEqual(set(spawn.call_args.kwargs), {"cwd", "env"})
                self.assert_child_environment(spawn.call_args.kwargs["env"], frozen=frozen, direct=True)

    def test_monitor_source_and_frozen_spawn_then_attach_resume_without_changing_parent(self):
        for frozen in (False, True):
            with self.subTest(frozen=frozen):
                self.dll = FakeDllDirectory()
                monitor = self.monitor()
                order = []
                monitor.device.spawn.side_effect = lambda *args, **kwargs: order.append("spawn") or 4242
                monitor.attach.side_effect = lambda *args, **kwargs: order.append("attach")
                monitor.device.resume.side_effect = lambda *args: order.append("resume")
                with patch.object(sys, "frozen", frozen, create=True), \
                        patch.object(external_environment, "native_environment", side_effect=lambda: self.native_environment(frozen)), \
                        patch.object(launch_mod, "prepare_config", return_value=(self.config, "synthetic verified")) as prepare:
                    self.assertEqual(monitor.launch(self.exe), 4242)
                prepare.assert_called_once_with(check_only=True)
                self.assertEqual(order, ["spawn", "attach", "resume"])
                self.assertEqual(monitor.device.spawn.call_args.args, ([str(self.exe)],))
                options = monitor.device.spawn.call_args.kwargs
                self.assertEqual(set(options), {"cwd", "stdio", "env"})
                self.assertEqual(options["cwd"], str(self.exe.parent))
                self.assertEqual(options["stdio"], "pipe")
                self.assert_child_environment(options["env"], frozen=frozen, direct=False)
                monitor.attach.assert_called_once_with(4242, service=False)
                monitor.device.resume.assert_called_once_with(4242)
                monitor.device.kill.assert_not_called()
                self.assertEqual(monitor.client_pids, {99, 4242})
                self.assertEqual(monitor.main_client_pids, {99, 4242})
                self.assertEqual(monitor.profile_pids, {99, 4242})

    def test_unsafe_browser_directory_fails_before_either_native_spawn(self):
        target = self.profile / business_environment.DIRECTORY
        target.write_bytes(b"synthetic unrelated file; must survive")
        for direct in (False, True):
            with self.subTest(direct=direct):
                monitor = self.monitor()
                with patch.object(sys, "frozen", False, create=True), \
                        patch.object(external_environment, "native_environment", side_effect=lambda: self.native_environment(False)), \
                        patch.object(launch_mod, "prepare_config", return_value=(self.config, "synthetic verified")), \
                        patch.object(launch_mod.desktop_process, "popen") as spawn:
                    with self.assertRaises((ValueError, OSError)):
                        launch_mod._quiet_process([str(self.exe)], self.exe.parent) if direct else monitor.launch(self.exe)
                spawn.assert_not_called()
                monitor.device.spawn.assert_not_called()
                monitor.attach.assert_not_called()
                monitor.device.resume.assert_not_called()
                monitor.device.kill.assert_not_called()
                self.assertEqual(target.read_bytes(), b"synthetic unrelated file; must survive")
                self.assertEqual(environment_fingerprint(), self.original_env)

    def test_wrong_prepared_profile_parent_is_rejected_before_spawn(self):
        wrong = self.root / "ONEME"
        wrong.mkdir()
        for direct in (False, True):
            with self.subTest(direct=direct):
                monitor = self.monitor()
                with patch.object(external_environment, "native_environment", side_effect=lambda: self.native_environment(False)), \
                        patch.object(launch_mod, "prepare_config", return_value=(wrong / "config.json", "synthetic")), \
                        patch.object(launch_mod.desktop_process, "popen") as spawn:
                    with self.assertRaises((ValueError, OSError)):
                        launch_mod._quiet_process([str(self.exe)], self.exe.parent) if direct else monitor.launch(self.exe)
                spawn.assert_not_called()
                monitor.device.spawn.assert_not_called()
                self.assertEqual([], list(wrong.iterdir()))

    def test_helper_roles_preserve_argv_and_never_prepare_native_browser_profile(self):
        for frozen in (False, True):
            for role in ("launcher", "observe", "recover", "mdata"):
                with self.subTest(frozen=frozen, role=role):
                    self.dll = FakeDllDirectory()
                    executable = self.root / ("ValMAXHelper.exe" if frozen else "python.exe")
                    command = helper_command.command(role, executable=str(executable), frozen=frozen,
                                                     module_directory=self.root / "modkit")
                    original = list(command)
                    with patch.object(sys, "frozen", frozen, create=True), \
                            patch.object(external_environment, "native_environment", side_effect=lambda: self.native_environment(frozen)), \
                            patch.object(launch_mod, "prepare_config", side_effect=AssertionError("Helper read profile")), \
                            patch.object(business_environment, "child_environment", side_effect=AssertionError("Helper created browser profile")), \
                            patch.object(launch_mod.desktop_process, "popen") as spawn:
                        launch_mod._quiet_process(command, self.root)
                    self.assertEqual(spawn.call_args.args, (original,))
                    self.assertEqual(command, original)
                    self.assertEqual(spawn.call_args.kwargs["cwd"], self.root)
                    if frozen:
                        self.assertEqual(spawn.call_args.kwargs["env"]["WEBVIEW2_USER_DATA_FOLDER"],
                                         "synthetic foreign profile")
                        self.assertEqual(spawn.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
                    else:
                        self.assertEqual(set(spawn.call_args.kwargs), {"cwd"})
                    self.assertEqual(self.source_env, self.original_source)
                    self.assertEqual(environment_fingerprint(), self.original_env)
        self.assertFalse((self.profile / business_environment.DIRECTORY).exists())


if __name__ == "__main__":
    unittest.main()
