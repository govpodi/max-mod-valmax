"""Synthetic first-login UI/controller tests; no real MAX or Windows tokens."""
from contextlib import contextmanager, nullcontext, redirect_stderr
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import MagicMock, Mock, patch

import mdata_import as first_login
from mdata_service import MDataError


PASSWORD = "SYNTHETIC-password-only-123"
ROOT = Path("D:/Synthetic-MData/MXMOD")
ARCHIVE = Path("D:/Synthetic-MData/account.mdata")


class ImportControllerTests(unittest.TestCase):
    def job(self, **overrides):
        dependencies = dict(activity=Mock(return_value=(0, True)), prepare=Mock(return_value=ROOT),
                            service_factory=Mock(), lock_factory=lambda: nullcontext(), launch=Mock(), guard=Mock())
        dependencies.update(overrides)
        service = dependencies["service_factory"].return_value
        service._import.return_value = "added", 1
        return first_login.ImportJob(**dependencies), SimpleNamespace(**dependencies), service

    def finish(self, job):
        job.thread.join(5)
        self.assertFalse(job.thread.is_alive())
        self.assertFalse(job.busy)
        return job.events.get_nowait()

    def test_import_and_constructor_do_not_load_profile_or_native_modules(self):
        job, deps, _ = self.job()
        for call in (deps.activity, deps.prepare, deps.guard, deps.service_factory, deps.launch):
            call.assert_not_called()
        self.assertIsNone(job.thread)
        self.assertFalse(job.busy or job.committed)
        spec = importlib.util.spec_from_file_location("synthetic_import_inert", first_login.__file__)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {name: None for name in (
                "launch_mod", "mdata_service", "mdata_recovery", "mdata_recovery_state", "frida", "tkinter")}):
            spec.loader.exec_module(module)
            module.ImportJob()

    def test_main_delegates_explicit_route_and_rejects_secrets_as_arguments(self):
        with patch("launch_mod.main", return_value=0) as launcher:
            for argv in ([], ["--import-mdata"]):
                self.assertEqual(first_login.main(argv), 0)
                launcher.assert_called_with(["--import-mdata"])
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                first_login.main(["--password", PASSWORD])

    def test_invalid_password_or_path_cannot_start_worker_or_access_backend(self):
        for path, password, code in ((ARCHIVE.as_uri(), "short", "invalid_password"),
                (ARCHIVE.as_uri(), "\ud800" * 12, "invalid_password"),
                (ARCHIVE.as_uri(), "a" * 1025, "invalid_password"),
                ("https://example.test/account.mdata", PASSWORD, "unsafe_path"),
                ("file:///D:/Synthetic-MData/ONEME/account.mdata", PASSWORD, "unsafe_path")):
            job, deps, _ = self.job()
            with self.assertRaises(first_login.MDataImportError) as raised:
                job.start(path, password)
            self.assertEqual(raised.exception.code, code)
            self.assertIsNone(job.thread)
            for call in (deps.activity, deps.prepare, deps.service_factory, deps.launch):
                call.assert_not_called()

    def test_success_checks_twice_imports_add_under_lock_and_launches_after_release(self):
        calls, locked = [], [False]

        @contextmanager
        def transfer():
            locked[0] = True
            calls.append("lock")
            try:
                yield
            finally:
                locked[0] = False
                calls.append("unlock")

        def checked_scan():
            self.assertTrue(locked[0])
            calls.append("scan")
            return 0, True

        job, deps, service = self.job(activity=Mock(side_effect=checked_scan), lock_factory=transfer,
                                     launch=Mock(side_effect=lambda: calls.append(("launch", locked[0]))))
        service._import.side_effect = lambda *args: (calls.append(("import", args, locked[0])) or ("added", 1))
        job.start(ARCHIVE.as_uri(), PASSWORD)
        self.assertEqual(self.finish(job), {"state": "done", "error": None})
        self.assertEqual(calls, ["lock", "scan", "scan", ("import", (ARCHIVE, PASSWORD, "add"), True),
                                 "unlock", ("launch", False)])
        deps.guard.assert_called_once_with(ROOT)
        self.assertTrue(job.committed)
        service._set.assert_called_once_with("done", result="added", profiles=1)
        self.assertEqual(service._action, "import")
        with self.assertRaises(first_login.MDataImportError):
            job.start(ARCHIVE.as_uri(), PASSWORD)

    def test_running_or_unverified_client_refuses_even_profile_preparation(self):
        for state, code in (((1, True), "client_still_running"), ((0, False), "process_scan_failed"),
                ((0, 1), "process_scan_failed"), ((False, True), "process_scan_failed"),
                ((-1, True), "process_scan_failed"), (None, "process_scan_failed")):
            job, deps, service = self.job(activity=Mock(return_value=state))
            job.start(ARCHIVE.as_uri(), PASSWORD)
            self.assertEqual(self.finish(job), {"state": "error", "error": code})
            for call in (deps.prepare, deps.service_factory, service._import, deps.launch):
                call.assert_not_called()

    def test_second_scan_and_recovery_guard_fail_before_import(self):
        for changes, code in ((dict(activity=Mock(side_effect=[(0, True), (1, True)])), "client_still_running"),
                (dict(guard=Mock(side_effect=SimpleError("recovery_required"))), "recovery_required"),
                (dict(activity=Mock(side_effect=RuntimeError(PASSWORD))), "process_scan_failed")):
            job, deps, _ = self.job(**changes)
            job.start(ARCHIVE.as_uri(), PASSWORD)
            self.assertEqual(self.finish(job), {"state": "error", "error": code})
            deps.service_factory.assert_not_called()
            deps.launch.assert_not_called()

    def test_default_prepare_checks_recovery_before_creating_config(self):
        with patch("mdata_recovery_state.root_candidate", return_value=ROOT), \
                patch("mdata_recovery_state.ensure_clear", side_effect=SimpleError("recovery_required")), \
                patch("launch_mod.prepare_config") as prepare:
            with self.assertRaises(SimpleError):
                first_login.prepare_profile()
            prepare.assert_not_called()

    def test_source_inside_profile_is_rejected_before_service_or_credentials(self):
        job, deps, _ = self.job()
        job.start((ROOT / "account.mdata").as_uri(), PASSWORD)
        self.assertEqual(self.finish(job), {"state": "error", "error": "unsafe_path"})
        deps.service_factory.assert_not_called()
        deps.launch.assert_not_called()

    def test_wrong_password_or_callback_exception_is_sanitized_and_never_launches(self):
        for error, code in ((MDataError("authentication_failed"), "authentication_failed"),
                            (OSError(28, PASSWORD), "insufficient_space"),
                            (SimpleError("destination_not_empty"), "profile_changed"),
                            (RuntimeError(PASSWORD + str(ARCHIVE)), "io_error")):
            job, deps, service = self.job()
            service._import.side_effect = error
            job.start(ARCHIVE.as_uri(), PASSWORD)
            result = self.finish(job)
            self.assertEqual(result, {"state": "error", "error": code})
            self.assertNotIn(PASSWORD, str(result))
            self.assertFalse(job.committed)
            deps.launch.assert_not_called()

    def test_launch_failure_cannot_repeat_committed_import_and_persist_is_optional(self):
        job, _, service = self.job(launch=Mock(side_effect=OSError(PASSWORD)))
        service._persist.side_effect = RuntimeError(PASSWORD)
        job.start(ARCHIVE.as_uri(), PASSWORD)
        self.assertEqual(self.finish(job), {"state": "error", "error": "restart_failed"})
        self.assertTrue(job.committed)
        with self.assertRaises(first_login.MDataImportError):
            job.start(ARCHIVE.as_uri(), PASSWORD)
        service._import.assert_called_once()

    def test_busy_job_refuses_duplicate_and_thread_start_failure_releases_busy(self):
        release = threading.Event()
        job, _, service = self.job()
        service._import.side_effect = lambda *_args: (release.wait(3) and ("added", 1))
        job.start(ARCHIVE.as_uri(), PASSWORD)
        try:
            with self.assertRaises(first_login.MDataImportError) as raised:
                job.start(ARCHIVE.as_uri(), PASSWORD)
            self.assertEqual(raised.exception.code, "busy")
        finally:
            release.set()
            self.finish(job)
        job, deps, _ = self.job()
        with patch.object(threading.Thread, "start", side_effect=RuntimeError(PASSWORD)):
            with self.assertRaises(first_login.MDataImportError) as raised:
                job.start(ARCHIVE.as_uri(), PASSWORD)
        self.assertEqual(raised.exception.code, "io_error")
        self.assertIsNone(job.thread)
        self.assertFalse(job.busy)
        deps.prepare.assert_not_called()

    def test_busy_transfer_mutex_refuses_all_reads(self):
        @contextmanager
        def busy():
            raise SimpleError("busy")
            yield
        job, deps, _ = self.job(lock_factory=busy)
        job.start(ARCHIVE.as_uri(), PASSWORD)
        self.assertEqual(self.finish(job), {"state": "error", "error": "busy"})
        deps.activity.assert_not_called()
        deps.prepare.assert_not_called()


class ImportGuiTests(unittest.TestCase):
    def gui(self, scenario, **dependencies):
        widgets, variables = [], []

        class Variable:
            def __init__(self, value=""):
                self.value = value
                variables.append(self)
            def get(self):
                return self.value
            def set(self, value):
                self.value = value

        def control(*args, **kwargs):
            widget = MagicMock()
            widget.options = dict(kwargs)
            widget.configure.side_effect = lambda **changes: widget.options.update(changes)
            widgets.append(widget)
            return widget

        window = MagicMock()
        callbacks = []
        window.after.side_effect = lambda delay, callback: callbacks.append(callback)
        window.mainloop.side_effect = lambda: scenario(window, widgets, variables, callbacks)
        tk = ModuleType("tkinter")
        tk.Tk, tk.StringVar = Mock(return_value=window), Variable
        tk.ttk = SimpleNamespace(Frame=control, Label=control, Entry=control, Button=control)
        tk.filedialog = SimpleNamespace(askopenfilename=Mock(return_value=str(ARCHIVE)))
        with patch.dict(sys.modules, {"tkinter": tk}):
            self.assertEqual(first_login.run_gui(**dependencies), 0)
        return window, tk

    def test_opening_and_dismissing_gui_does_not_read_profile_or_scan(self):
        deps = {key: Mock() for key in ("activity", "prepare", "service_factory", "launch", "guard")}

        def scenario(window, widgets, variables, callbacks):
            window.protocol.call_args.args[1]()
            self.assertEqual(variables[1].get(), "")
            self.assertEqual(next(w.options for w in widgets if w.options.get("show")),
                             {"textvariable": variables[1], "show": "●", "width": 48})
        window, tk = self.gui(scenario, **deps)
        window.destroy.assert_called_once()
        tk.filedialog.askopenfilename.assert_not_called()
        for call in deps.values():
            call.assert_not_called()

    def test_submit_clears_password_disables_busy_ui_and_preserves_committed_state(self):
        import queue
        job = SimpleNamespace(busy=False, committed=False, events=queue.Queue(), start=Mock())

        def scenario(window, widgets, variables, callbacks):
            button = lambda text: next(w for w in widgets if w.options.get("text") == text)
            button("Войти").options["command"]()
            self.assertEqual(variables[2].get(), first_login.MESSAGES["unsafe_path"])
            job.start.assert_not_called()
            button("Выбрать MData").options["command"]()
            variables[1].set(PASSWORD)

            def start(path, password):
                self.assertEqual(variables[1].get(), "")
                self.assertEqual((path, password), (ARCHIVE.as_uri(), PASSWORD))
                job.busy = True
            job.start.side_effect = start
            button("Войти").options["command"]()
            self.assertEqual(variables[1].get(), "")
            for label in ("Выбрать MData", "Войти", "Закрыть"):
                self.assertEqual(button(label).options["state"], "disabled")
            window.protocol.call_args.args[1]()
            window.destroy.assert_not_called()
            job.busy, job.committed = False, True
            job.events.put({"state": "error", "error": "restart_failed"})
            callbacks[0]()
            self.assertEqual(variables[2].get(), first_login.MESSAGES["restart_failed"])
            self.assertEqual(button("Войти").options["state"], "disabled")
            self.assertEqual(button("Закрыть").options["state"], "normal")
        with patch.object(first_login, "ImportJob", return_value=job):
            self.gui(scenario)
        job.start.assert_called_once()


class SimpleError(Exception):
    def __init__(self, code):
        self.code = code


class FreshProfileTests(unittest.TestCase):
    def test_actual_login_archive_import_creates_fresh_private_config_and_only_login_data(self):
        import launch_mod
        from mdata_service import MDataService
        from test_mdata_login import MemoryCredentials
        from test_mdata_service import synthetic_disk_capacity
        parent = Path(os.environ.get("VALMAX_TEST_TEMP_ROOT") or r"D:\CodexLabs\max-mod-26.35.1\analysis\test-temp") if os.name == "nt" else None
        if parent is not None:
            parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="mdata-first-login-", dir=parent) as temporary, \
                patch("session_credentials._api", side_effect=AssertionError("Real tokens forbidden")), \
                synthetic_disk_capacity(temporary):
            base = Path(temporary)
            source = base / "source" / "MXMOD"
            source.mkdir(parents=True)
            (source / "auth_42").write_bytes(b'{"first_name":"SYNTHETIC"}')
            incoming = MemoryCredentials({"access_token_42@MXMOD": b"SYNTHETIC-TOKEN"})
            archive = base / "account.mdata"
            MDataService(source, lambda: (0, True), credentials=incoming)._write_archive(archive, PASSWORD)
            destination = base / "fresh"
            destination.mkdir()
            current, launch = MemoryCredentials(), Mock()
            job = first_login.ImportJob(activity=lambda: (0, True),
                prepare=lambda: launch_mod.prepare_config(destination)[0].parent,
                service_factory=lambda root, activity: MDataService(root, activity, credentials=current),
                lock_factory=lambda: nullcontext(), launch=launch)
            self.assertFalse((destination / "MXMOD").exists())
            job.start(archive.as_uri(), PASSWORD)
            job.thread.join(10)
            self.assertFalse(job.thread.is_alive())
            self.assertEqual(job.events.get_nowait(), {"state": "done", "error": None})
            launch.assert_called_once_with()
            self.assertEqual(current.data, incoming.data)
            self.assertEqual({item.name for item in (destination / "MXMOD").iterdir()},
                             {"config.json", "auth_42", "mdata-result.json"})
            self.assertFalse((destination / "MXMOD-MData-Backups").exists())


if __name__ == "__main__":
    unittest.main()
