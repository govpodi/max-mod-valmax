"""Pure argv/dispatcher tests; every application module and stream is mocked."""
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import bundle_entry
import helper_command as module


class HelperCommandTests(unittest.TestCase):
    def setUp(self):
        self.exe = str(Path("C:/Synthetic Folder/Клиент/MAXModHelper.exe"))
        self.python = str(Path("C:/Synthetic Python/python.exe"))
        self.scripts = Path("C:/Synthetic Source/Мод/modkit")

    def test_frozen_roles_preserve_argv_tokens_without_script_or_shell(self):
        args = ["--manifest", "C:/Проверка с пробелами/runtime/mod/mod-manifest.json"]
        for role in module.ROLES:
            with self.subTest(role=role):
                role_args = [] if role in ("recover", "mdata") else args
                self.assertEqual(module.command(role, role_args, executable=self.exe, frozen=True), [self.exe, role, *role_args])
        self.assertEqual(args[0], "--manifest")

    def test_source_roles_use_only_fixed_script_names_and_recovery_is_explicit(self):
        scripts = {"launcher": "launch_mod.py", "observe": "privacy_server.py", "recover": "mdata_recovery.py", "mdata": "mdata_import.py"}
        for role, script in scripts.items():
            expected = [self.python, "-B", str(self.scripts / script)]
            if role == "recover":
                expected.append("--recover-mdata")
            if role == "mdata":
                expected.append("--import-mdata")
            self.assertEqual(module.command(role, executable=self.python, frozen=False, module_directory=self.scripts), expected)
        command = module.command("recover", ["--recover-mdata"], executable=self.python, frozen=False, module_directory=self.scripts)
        self.assertEqual(command.count("--recover-mdata"), 1)
        command = module.command("mdata", ["--import-mdata"], executable=self.python, frozen=False, module_directory=self.scripts)
        self.assertEqual(command.count("--import-mdata"), 1)

    def test_default_manifest_is_relative_to_frozen_exe_and_source_stays_pinned(self):
        self.assertEqual(module.default_manifest(executable=self.exe, frozen=True), Path(self.exe).parent / "runtime/mod/mod-manifest.json")
        self.assertEqual(module.default_manifest(executable=self.python, frozen=False), module.SOURCE_DEFAULT_MANIFEST)
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", self.exe):
            self.assertEqual(module.command(), [self.exe, "launcher"])
            self.assertEqual(module.default_manifest(), Path(self.exe).parent / "runtime/mod/mod-manifest.json")

    def test_roles_password_flags_and_malformed_values_fail_with_fixed_error(self):
        calls = [
            lambda role=role: module.command(role, executable=self.exe, frozen=True)
            for role in (None, True, "-c", "export", "secret.py", "../../launch_mod.py")
        ]
        calls += [lambda args=args: module.command(args=args, executable=self.exe, frozen=True)
                  for args in ("--check-only", [None], [True], ["a\0b"], ["--password", "SYNTHETIC_SECRET"], ["--password=SYNTHETIC_SECRET"])]
        calls += [lambda: module.command(executable="relative.exe", frozen=True),
                  lambda: module.command(executable=self.exe, frozen=1),
                  lambda: module.command("recover", ["--manifest", "SYNTHETIC_SECRET"], executable=self.exe, frozen=True),
                  lambda: module.command("mdata", ["--manifest", "SYNTHETIC_SECRET"], executable=self.exe, frozen=True),
                  lambda: module.command(executable=self.python, frozen=False, module_directory="relative")]
        messages = []
        for call in calls:
            with self.assertRaises(ValueError) as caught:
                call()
            messages.append(caught.exception.args)
        self.assertEqual(len(set(messages)), 1)
        self.assertNotIn("SYNTHETIC_SECRET", str(messages))


class BundleEntryTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.launcher = SimpleNamespace(DEFAULT_MANIFEST=Path("D:/Synthetic-Source/mod-manifest.json"), main=Mock(side_effect=self.capture))
        self.observer = SimpleNamespace(main=Mock(side_effect=self.capture))
        self.recovery = SimpleNamespace(main=Mock(side_effect=self.capture))
        self.modules = {"launch_mod": self.launcher, "privacy_server": self.observer, "mdata_recovery": self.recovery}
        self.imports = []
        self.notice = Mock()
        self.environment = patch.dict(bundle_entry.os.environ, {}, clear=True)
        self.environment.start()

    def tearDown(self):
        bundle_entry.startup_status.reset()
        bundle_entry.startup_status.set_role("launcher")
        self.environment.stop()

    def capture(self):
        self.calls.append(list(sys.argv))
        return 0

    def importer(self, name):
        self.imports.append(name)
        return self.modules[name]

    def run_entry(self, args, *, frozen=False):
        with patch.object(bundle_entry.importlib, "import_module", side_effect=self.importer), \
             patch.object(sys, "frozen", frozen, create=True), \
             patch.object(sys, "executable", str(Path("C:/Synthetic App/MAXModHelper.exe"))), \
             patch.object(bundle_entry, "_show_startup_error", self.notice):
            return bundle_entry.main(args)

    def test_default_explicit_launcher_and_options_only_restore_original_argv(self):
        original = sys.argv
        for args, expected in (([], []), (["launcher", "--check-only"], ["--check-only"]), (["--without-diagnostics"], ["--without-diagnostics"])):
            self.assertEqual(self.run_entry(args), 0)
            self.assertEqual(self.calls[-1][1:], expected)
            self.assertIs(sys.argv, original)
        self.assertEqual(self.launcher.DEFAULT_MANIFEST, Path("D:/Synthetic-Source/mod-manifest.json"))

    def test_frozen_default_is_injected_before_observer_import(self):
        expected = Path("C:/Synthetic App/runtime/mod/mod-manifest.json")
        def import_checked(name):
            if name == "privacy_server":
                self.assertEqual(self.launcher.DEFAULT_MANIFEST, expected)
            return self.importer(name)
        with patch.object(bundle_entry.importlib, "import_module", side_effect=import_checked), \
             patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "executable", str(Path("C:/Synthetic App/MAXModHelper.exe"))):
            self.assertEqual(bundle_entry.main(["observe", "--launch", "--idle-seconds", "20"]), 0)
        self.assertEqual(self.imports, ["launch_mod", "privacy_server"])
        self.assertEqual(self.calls[-1][1:], ["--launch", "--idle-seconds", "20"])
        self.observer.main.assert_called_once()

    def test_recovery_requires_explicit_role_or_existing_launcher_flag(self):
        self.assertEqual(self.run_entry(["recover"]), 0)
        self.recovery.main.assert_not_called()
        self.launcher.main.assert_called_once()
        self.assertEqual(self.imports, ["launch_mod"])
        self.assertEqual(self.calls[-1][1:], ["--recover-mdata"])
        self.launcher.main.reset_mock()
        self.assertEqual(self.run_entry(["--recover-mdata"]), 0)
        self.recovery.main.assert_not_called()
        self.launcher.main.assert_called_once()
        self.assertEqual(self.calls[-1][1:], ["--recover-mdata"])

    def test_unknown_roles_and_bad_recovery_arguments_do_not_import_application(self):
        for args in (["SYNTHETIC_SECRET"], ["../launch_mod.py"], ["recover", "SYNTHETIC_SECRET"], ["mdata", "SYNTHETIC_SECRET"], ["launcher", "--password=SYNTHETIC_SECRET"], ["--password=SYNTHETIC_SECRET"], [None], "launcher"):
            with self.subTest(kind=type(args).__name__):
                self.assertEqual(self.run_entry(args), 2)
                self.assertEqual(self.imports, [])
                self.assertEqual(self.calls, [])

    def test_mdata_role_uses_verified_launcher_without_argument_secret_or_direct_gui(self):
        self.assertEqual(self.run_entry(["mdata"], frozen=True), 0)
        self.assertEqual(self.imports, ["launch_mod"])
        self.assertEqual(self.calls[-1][1:], ["--import-mdata"])
        self.assertEqual(self.launcher.DEFAULT_MANIFEST, Path("C:/Synthetic App/runtime/mod/mod-manifest.json"))

    def test_application_exceptions_are_fixed_exit_status_without_formatting(self):
        class HostileError(Exception):
            def __str__(self):
                raise AssertionError("Exception formatted")
            def __repr__(self):
                raise AssertionError("Exception represented")
        original = sys.argv
        self.launcher.main.side_effect = HostileError("SYNTHETIC_SECRET")
        self.assertEqual(self.run_entry([]), 1)
        self.assertIs(sys.argv, original)
        self.launcher.main.side_effect = SystemExit("SYNTHETIC_SECRET")
        self.assertEqual(self.run_entry([]), 1)
        self.launcher.main.side_effect = SystemExit(2)
        self.assertEqual(self.run_entry([]), 2)
        self.launcher.main.side_effect = SystemExit(None)
        self.assertEqual(self.run_entry([]), 0)

    def test_fixed_startup_notice_only_for_interactive_frozen_launcher_failure(self):
        for args, frozen, target, result, expected in (
            ([], True, self.launcher, 1, 1),
            (["launcher"], True, self.launcher, 2, 1),
            (["recover"], True, self.launcher, 1, 1),
            (["--check-only"], True, self.launcher, 1, 0),
            (["observe"], True, self.observer, 1, 0),
            ([], False, self.launcher, 1, 0),
            ([], True, self.launcher, 0, 0),
        ):
            with self.subTest(args=args, frozen=frozen, status=result):
                self.launcher.main.side_effect = None
                self.observer.main.side_effect = None
                self.launcher.main.return_value = self.observer.main.return_value = 0
                target.main.return_value = result
                self.notice.reset_mock()
                self.assertEqual(self.run_entry(args, frozen=frozen), result)
                self.assertEqual(self.notice.call_count, expected)
                if expected:
                    self.notice.assert_called_once_with()
        self.launcher.main.side_effect = RuntimeError("SYNTHETIC_SECRET")
        self.notice.reset_mock()
        self.assertEqual(self.run_entry([], frozen=True), 1)
        self.notice.assert_called_once_with()

    def test_native_notice_contains_only_fixed_strings_and_is_mocked(self):
        api = SimpleNamespace(MessageBoxW=Mock())
        with patch("ctypes.WinDLL", return_value=api) as load, patch.object(bundle_entry.os, "name", "nt"):
            bundle_entry._show_startup_error()
        load.assert_called_once_with("user32", use_last_error=True)
        api.MessageBoxW.assert_called_once_with(None, bundle_entry._STARTUP_ERROR, "ValMAX", 0x10)
        self.assertNotIn("SYNTHETIC_SECRET", bundle_entry._STARTUP_ERROR)

    def test_generic_notice_does_not_recommend_mdata_recovery(self):
        self.assertNotIn("MData", bundle_entry._STARTUP_ERROR)
        self.assertIn("ValMAX", bundle_entry._STARTUP_ERROR)

    def test_fixed_failure_reason_reaches_notice_without_raw_exception(self):
        status = bundle_entry.startup_status
        self.launcher.main.side_effect = status.StartupError("port_other_runtime")
        self.assertEqual(self.run_entry([], frozen=True), 1)
        self.assertEqual(status.failure_code(), "port_other_runtime")
        self.notice.assert_called_once_with()
        self.assertNotIn("MData", status.message_for(status.failure_code()))

    def test_main_return_code_uses_its_finite_failure(self):
        status = bundle_entry.startup_status
        def refused():
            status.fail("recovery_required")
            return 1
        self.launcher.main.side_effect = refused
        self.assertEqual(self.run_entry([], frozen=True), 1)
        self.assertEqual(status.failure_code(), "recovery_required")
        self.assertIn("Восстановление MData", status.message_for(status.failure_code()))

    def test_import_failure_gets_fixed_import_message(self):
        with patch.object(bundle_entry.importlib, "import_module", side_effect=ModuleNotFoundError("SYNTHETIC_SECRET")), \
             patch.object(sys, "frozen", True, create=True), \
             patch.object(bundle_entry, "_show_startup_error", self.notice):
            self.assertEqual(bundle_entry.main([]), 1)
        self.assertEqual(bundle_entry.startup_status.failure_code(), "import_failed")
        self.assertNotIn("SYNTHETIC_SECRET", bundle_entry.startup_status.message_for("import_failed"))

    def test_desktop_child_normal_failure_does_not_block_parent_with_popup(self):
        key = bundle_entry.startup_status.ENVIRONMENT_KEY
        self.launcher.main.side_effect = None
        self.launcher.main.return_value = 1
        for args, operation, expected in (([], "a" * 32, 0),
                                          ([], "invalid", 1),
                                          (["recover"], "a" * 32, 1),
                                          (["mdata"], "a" * 32, 1),
                                          (["--recover-mdata"], "a" * 32, 1),
                                          (["--import-mdata"], "a" * 32, 1),
                                          (["--check-only"], "a" * 32, 0)):
            with self.subTest(args=args, valid_id=operation == "a" * 32), \
                 patch.dict(bundle_entry.os.environ, {"MAX_MOD_DESKTOP_CHILD": "1", key: operation,
                                                    "MAX_MOD_STARTUP_PARENT_WAITS": "1"}):
                self.notice.reset_mock()
                self.assertEqual(self.run_entry(args, frozen=True), 1)
                self.assertEqual(self.notice.call_count, expected)

    def test_background_restart_has_notice_and_parent_hint_is_one_shot(self):
        key = bundle_entry.startup_status.ENVIRONMENT_KEY
        self.launcher.main.side_effect = None
        self.launcher.main.return_value = 1
        with patch.dict(bundle_entry.os.environ, {"MAX_MOD_DESKTOP_CHILD": "1", key: 'a'*32,
                                                "MAX_MOD_STARTUP_PARENT_WAITS": "1"}):
            self.assertEqual(self.run_entry([], frozen=True), 1)
            self.notice.assert_not_called()
            self.assertNotIn("MAX_MOD_STARTUP_PARENT_WAITS", bundle_entry.os.environ)
            # A long-lived observer's MData restart inherits the operation ID,
            # but its original root no longer waits and cannot show a popup.
            self.assertEqual(self.run_entry([], frozen=True), 1)
            self.notice.assert_called_once_with()

    def test_native_notice_uses_fixed_finite_message_only(self):
        status = bundle_entry.startup_status
        api = SimpleNamespace(MessageBoxW=Mock())
        status.fail("port_other_runtime")
        with patch("ctypes.WinDLL", return_value=api), patch.object(bundle_entry.os, "name", "nt"):
            bundle_entry._show_startup_error()
        api.MessageBoxW.assert_called_once_with(None, status.message_for("port_other_runtime"), "ValMAX", 0x10)

    def test_null_standard_streams_are_replaced_before_first_application_import(self):
        streams = []
        def null_open(path, mode, **kwargs):
            self.assertEqual(path, bundle_entry.os.devnull)
            self.assertEqual(kwargs, {"encoding": "utf-8"})
            stream = io.StringIO()
            streams.append((mode, stream))
            return stream
        def import_checked(name):
            for field in ("stdin", "stdout", "stderr"):
                self.assertIsNotNone(getattr(sys, field))
                getattr(sys, field).flush()
            return self.importer(name)
        with patch.object(sys, "stdin", None), patch.object(sys, "stdout", None), patch.object(sys, "stderr", None), \
             patch("builtins.open", side_effect=null_open), \
             patch.object(bundle_entry.importlib, "import_module", side_effect=import_checked), \
             patch.object(sys, "frozen", False, create=True):
            self.assertEqual(bundle_entry.main([]), 0)
        self.assertEqual([mode for mode, _stream in streams], ["r", "w", "w"])


if __name__ == "__main__":
    unittest.main()
