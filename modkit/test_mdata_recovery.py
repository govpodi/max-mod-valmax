"""Synthetic recovery GUI/controller tests. No MAX, profile or WinCred access."""
from contextlib import contextmanager, redirect_stderr
import importlib.util
import io
import queue
from pathlib import Path
import subprocess
import sys
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import mdata_recovery as recovery


PASSWORD = "SYNTHETIC-password-12"
ROOT = Path("D:/Synthetic-Test/MXMOD")
RUNTIME = "D:\\CodexLabs\\max-mod-26.35.1\\runtime"


def process(pid, name="max.exe"):
    return SimpleNamespace(pid=pid, name=name)


class RecoveryControllerTests(unittest.TestCase):
    def job(self, activity=None, recover=None, launch=None):
        activity = activity or Mock(return_value=(0, True))
        recover = recover or Mock()
        launch = launch or Mock()
        job = recovery.RecoveryJob(ROOT, activity=activity, recover=recover, launch=launch)
        return job, activity, recover, launch

    def finish(self, job):
        job.thread.join(timeout=2)
        self.assertFalse(job.thread.is_alive(), "Synthetic worker did not finish")
        self.assertFalse(job.busy)
        return job.events.get_nowait()

    def test_import_and_controller_construction_are_inert(self):
        job, activity, restore, launch = self.job()
        self.assertIsNone(job.thread)
        self.assertTrue(job.events.empty())
        for call in (activity, restore, launch):
            call.assert_not_called()
        # Re-importing the standalone helper must not import the native backend.
        spec = importlib.util.spec_from_file_location("synthetic_recovery_inert", recovery.__file__)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"frida": None, "mdata_service": None,
                                      "mdata_recovery_state": None, "tkinter": None}):
            spec.loader.exec_module(module)

    def test_explicit_flag_required_before_any_gui_or_marker_access(self):
        with patch.object(recovery, "run_gui") as gui, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                recovery.main([])
            self.assertEqual(raised.exception.code, 2)
            gui.assert_not_called()
        with patch("launch_mod.main", return_value=0) as launcher, \
                patch.object(recovery, "run_gui", side_effect=AssertionError("GUI bypassed desktop launcher")):
            self.assertEqual(recovery.main(["--recover-mdata"]), 0)
            launcher.assert_called_once_with(["--recover-mdata"])

    def test_password_utf8_bounds_and_nul(self):
        for value in ("a" * 12, "я" * 6, "я" * 512):
            recovery.validate_password(value)
        for value in ("", "a" * 11, "я" * 513, "a" * 1025, "ab\0" * 5,
                      "\ud800" * 12, None, b"a" * 12):
            with self.subTest(kind=type(value).__name__):
                with self.assertRaises(recovery.RecoveryError) as raised:
                    recovery.validate_password(value)
                self.assertEqual(raised.exception.code, "invalid_password")

    def test_invalid_password_never_starts_worker(self):
        job, activity, restore, launch = self.job()
        with self.assertRaises(recovery.RecoveryError):
            job.start("short")
        self.assertIsNone(job.thread)
        for call in (activity, restore, launch):
            call.assert_not_called()

    def test_closed_check_precedes_restore_then_launch(self):
        calls = []
        job, *_ = self.job(
            activity=Mock(side_effect=lambda: (calls.append("scan") or (0, True))),
            recover=Mock(side_effect=lambda root, password: calls.append(("restore", root, password))),
            launch=Mock(side_effect=lambda: calls.append("launch")))
        job.start(PASSWORD)
        self.assertEqual(self.finish(job), {"state": "done", "error": None})
        self.assertEqual(calls, ["scan", ("restore", ROOT, PASSWORD), "launch"])
        self.assertTrue(job.recovered)
        self.assertFalse(any("password" in key for key in vars(job)))
        with self.assertRaises(recovery.RecoveryError):
            job.start(PASSWORD)

    def test_running_or_unverified_scan_refuses_backend(self):
        for value, code in (((1, True), "client_still_running"),
                            ((0, False), "process_scan_failed"),
                            ((0, "true"), "process_scan_failed"),
                            ((-1, True), "process_scan_failed"),
                            ((False, True), "process_scan_failed")):
            with self.subTest(value=value):
                job, _, restore, launch = self.job(activity=Mock(return_value=value))
                job.start(PASSWORD)
                self.assertEqual(self.finish(job), {"state": "error", "error": code})
                restore.assert_not_called()
                launch.assert_not_called()

    def test_scan_exception_is_fixed_and_never_reads_archive(self):
        job, _, restore, launch = self.job(activity=Mock(side_effect=RuntimeError("SYNTHETIC-SECRET-PATH")))
        job.start(PASSWORD)
        self.assertEqual(self.finish(job), {"state": "error", "error": "process_scan_failed"})
        restore.assert_not_called()
        launch.assert_not_called()

    def test_restore_exception_has_only_allowlisted_code_no_launch(self):
        for error, code in ((recovery.RecoveryError("authentication_failed"), "authentication_failed"),
                            (RuntimeError("SYNTHETIC-SECRET-PATH"), "io_error")):
            job, _, _, launch = self.job(recover=Mock(side_effect=error))
            job.start(PASSWORD)
            self.assertEqual(self.finish(job), {"state": "error", "error": code})
            self.assertFalse(job.recovered)
            launch.assert_not_called()

    def test_busy_job_cannot_start_second_restore(self):
        release = threading.Event()
        job, _, restore, launch = self.job(recover=Mock(side_effect=lambda *_args: release.wait(2)))
        job.start(PASSWORD)
        try:
            with self.assertRaises(recovery.RecoveryError) as raised:
                job.start(PASSWORD)
            self.assertEqual(raised.exception.code, "busy")
        finally:
            release.set()
            self.finish(job)
        restore.assert_called_once()
        launch.assert_called_once()

    def test_launch_failure_cannot_repeat_completed_restore(self):
        job, _, restore, _ = self.job(launch=Mock(side_effect=OSError("SYNTHETIC-PATH")))
        job.start(PASSWORD)
        self.assertEqual(self.finish(job), {"state": "error", "error": "restart_failed"})
        self.assertTrue(job.recovered)
        with self.assertRaises(recovery.RecoveryError):
            job.start(PASSWORD)
        restore.assert_called_once()

    def test_thread_start_failure_resets_busy_without_backend(self):
        job, activity, restore, launch = self.job()
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("SYNTHETIC-PATH")):
            with self.assertRaises(recovery.RecoveryError) as raised:
                job.start(PASSWORD)
        self.assertEqual(raised.exception.code, "io_error")
        self.assertFalse(job.busy)
        for call in (activity, restore, launch):
            call.assert_not_called()

    def test_quiet_launcher_uses_no_secret_arguments_or_stdio(self):
        with patch.object(recovery, "wait_for_observer_exit") as wait, patch("launch_mod._quiet_process") as popen:
            popen.return_value.wait.return_value = 0
            recovery.launch_quietly()
        wait.assert_called_once_with()
        popen.return_value.wait.assert_called_once_with(timeout=20)
        args, kwargs = popen.call_args
        self.assertEqual(args[0], [sys.executable, "-B", str(Path(recovery.__file__).with_name("launch_mod.py"))])
        self.assertEqual(args[1].name, "valmax-live-v8")
        self.assertEqual(kwargs, {})

    def test_quiet_launcher_wait_is_bounded_and_does_not_terminate_on_timeout(self):
        for result in (1, subprocess.TimeoutExpired("SYNTHETIC-launcher", 20)):
            with patch.object(recovery, "wait_for_observer_exit"), patch("launch_mod._quiet_process") as popen:
                if isinstance(result, Exception):
                    popen.return_value.wait.side_effect = result
                else:
                    popen.return_value.wait.return_value = result
                with self.assertRaises(recovery.RecoveryError) as raised:
                    recovery.launch_quietly()
                self.assertEqual(raised.exception.code, "restart_failed")
                popen.return_value.wait.assert_called_once_with(timeout=20)
                popen.return_value.terminate.assert_not_called()
                popen.return_value.kill.assert_not_called()

    def test_explicit_import_restart_preserves_the_verified_manifest(self):
        manifest = Path("D:/Synthetic Relocated/Клиент/runtime/mod/mod-manifest.json")
        with patch.object(recovery, "wait_for_observer_exit"), patch("launch_mod._quiet_process") as popen:
            popen.return_value.wait.return_value = 0
            recovery.launch_quietly(manifest=manifest)
        self.assertEqual(popen.call_args.args[0][-2:], ["--manifest", str(manifest)])
        self.assertEqual(popen.call_args.args[1], manifest.parent)

    def test_port_wait_failure_prevents_quiet_launch_after_completed_restore(self):
        with patch.object(recovery, "wait_for_observer_exit", side_effect=recovery.RecoveryError("restart_failed")), patch.object(recovery.subprocess, "Popen") as popen:
            job, _, restore, _ = self.job(launch=recovery.launch_quietly)
            job.start(PASSWORD)
            self.assertEqual(self.finish(job), {"state": "error", "error": "restart_failed"})
            self.assertTrue(job.recovered)
            restore.assert_called_once()
            popen.assert_not_called()


class ObserverPortWaitTests(unittest.TestCase):
    def test_free_port_returns_without_sleep_or_http(self):
        is_free, sleep = Mock(return_value=True), Mock()
        with patch.object(recovery.socket, "create_connection") as connect:
            recovery.wait_for_observer_exit(is_free=is_free, monotonic=lambda: 0.0, sleep=sleep)
            connect.assert_not_called()
        is_free.assert_called_once_with()
        sleep.assert_not_called()

    def test_busy_then_closed_port_uses_virtual_time_only(self):
        clock = [0.0]
        is_free = Mock(side_effect=[False, False, False, True])
        recovery.wait_for_observer_exit(is_free=is_free, monotonic=lambda: clock[0],
                                        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
        self.assertEqual(is_free.call_count, 4)
        self.assertAlmostEqual(clock[0], 0.3)

    def test_persistent_busy_port_has_bounded_25_second_virtual_deadline(self):
        clock = [0.0]
        is_free = Mock(return_value=False)
        with self.assertRaises(recovery.RecoveryError) as raised:
            recovery.wait_for_observer_exit(is_free=is_free, monotonic=lambda: clock[0],
                                            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
        self.assertEqual(raised.exception.code, "restart_failed")
        self.assertAlmostEqual(clock[0], 25.0)
        self.assertLessEqual(clock[0], 25.0)
        self.assertGreater(is_free.call_count, 1)

    def test_exclusive_bind_checks_free_port_without_listening_or_http(self):
        socket_factory = MagicMock()
        probe = socket_factory.return_value.__enter__.return_value
        self.assertTrue(recovery.observer_port_is_free(socket_factory=socket_factory))
        probe.bind.assert_called_once_with(("127.0.0.1", 18973))
        if sys.platform == "win32":
            probe.setsockopt.assert_called_once_with(recovery.socket.SOL_SOCKET, recovery.socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.listen.assert_not_called()
        probe.accept.assert_not_called()
        probe.send.assert_not_called()
        probe.sendall.assert_not_called()
        socket_factory.return_value.__exit__.assert_called_once()

    def test_exclusive_bind_busy_or_access_denied_never_claims_freedom(self):
        for error_number in (48, 98, 10013, 10048):
            socket_factory = MagicMock()
            socket_factory.return_value.__enter__.return_value.bind.side_effect = OSError(error_number, "SYNTHETIC")
            self.assertFalse(recovery.observer_port_is_free(socket_factory=socket_factory))

    def test_unexpected_bind_error_is_fixed_restart_failure(self):
        socket_factory = MagicMock()
        socket_factory.return_value.__enter__.return_value.bind.side_effect = OSError(9, "SYNTHETIC-SECRET")
        with self.assertRaises(recovery.RecoveryError) as raised:
            recovery.observer_port_is_free(socket_factory=socket_factory)
        self.assertEqual(raised.exception.code, "restart_failed")

    def test_other_socket_errors_fail_with_fixed_code(self):
        with self.assertRaises(recovery.RecoveryError) as raised:
            recovery.wait_for_observer_exit(is_free=Mock(side_effect=recovery.RecoveryError("restart_failed")), monotonic=lambda: 0.0, sleep=Mock())
        self.assertEqual(raised.exception.code, "restart_failed")


class OwnedProcessTests(unittest.TestCase):
    def setUp(self):
        # Synthetic executable paths must never cause real image reads.
        classifier = patch("max_client_policy.original_image", return_value=False)
        classifier.start()
        self.addCleanup(classifier.stop)

    def scan(self, paths, names=None):
        names = names or {}
        lookup = Mock(side_effect=lambda pid: paths[pid])
        return recovery.scan_owned_clients([process(pid, names.get(pid, "max.exe")) for pid in paths], lookup, RUNTIME), lookup

    def test_exact_main_service_and_multiple_lab_versions_count(self):
        value, lookup = self.scan({1: RUNTIME + "\\mod\\max.exe",
                                   2: RUNTIME + "\\mod-mdata-v6\\max-service\\max-service.exe",
                                   3: RUNTIME.upper() + "\\MOD-OLD\\MAX.EXE"}, {2: "MAX-SERVICE.EXE"})
        self.assertEqual(value, (3, True))
        self.assertEqual(lookup.call_count, 3)

    def test_unverified_images_outside_lab_also_block(self):
        value, _ = self.scan({1: "C:\\Program Files\\MAX\\max.exe",
                              2: "D:\\Other\\max.exe",
                              3: RUNTIME + "-other\\mod\\max.exe",
                              4: RUNTIME + "\\baseline\\max.exe",
                              5: RUNTIME + "\\mod-v1\\nested\\max.exe",
                              6: RUNTIME + "\\mod-v1\\helper.exe"})
        self.assertEqual(value, (6, True))

    def test_only_candidate_names_are_queried(self):
        value, lookup = self.scan({1: RUNTIME + "\\mod\\notepad.exe"}, {1: "notepad.exe"})
        self.assertEqual(value, (0, True))
        lookup.assert_not_called()

    def test_unknown_path_fails_closed_including_possible_official_process(self):
        value, _ = self.scan({1: None, 2: "relative\\max.exe"})
        self.assertEqual(value, (0, False))

    def test_lookup_and_enumeration_fail_closed(self):
        value = recovery.scan_owned_clients([process(1)], Mock(side_effect=OSError("SYNTHETIC")), RUNTIME)
        self.assertEqual(value, (0, False))
        def broken():
            yield process(1)
            raise RuntimeError("SYNTHETIC")
        value = recovery.scan_owned_clients(broken(), lambda _pid: RUNTIME + "\\mod\\max.exe", RUNTIME)
        self.assertEqual(value, (1, False))


class FakeVariable:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeWidget:
    def __init__(self, fixture, *args, **kwargs):
        self.fixture, self.options = fixture, dict(kwargs)
        self.bindings = {}
        fixture.widgets.append(self)

    def grid(self, **_kwargs):
        pass

    def configure(self, **kwargs):
        self.options.update(kwargs)

    def bind(self, event, callback):
        self.bindings[event] = callback

    def focus_set(self):
        pass


class TkFixture:
    """No Tk display or native window is ever created in these tests."""
    def __init__(self, script):
        self.widgets, self.callbacks, self.variables = [], [], []
        self.destroyed = False
        self.script = script
        self.protocols = {}
        self.tk = ModuleType("tkinter")
        self.ttk = ModuleType("tkinter.ttk")
        self.tk.Tk = lambda: self
        self.tk.StringVar = self.variable
        for name in ("Frame", "Label", "Entry", "Button"):
            setattr(self.ttk, name, lambda *args, **kwargs: FakeWidget(self, *args, **kwargs))
        self.tk.ttk = self.ttk

    def variable(self, value=""):
        var = FakeVariable(value)
        self.variables.append(var)
        return var

    @property
    def password(self):
        return self.variables[0]

    @property
    def status(self):
        return self.variables[1]

    def button(self, text):
        return next(widget for widget in self.widgets if widget.options.get("text") == text)

    def title(self, _value):
        pass

    def resizable(self, *_args):
        pass

    def option_add(self, *_args):
        pass

    def protocol(self, name, callback):
        self.protocols[name] = callback

    def after(self, _milliseconds, callback):
        self.callbacks.append(callback)

    def mainloop(self):
        self.script(self)

    def destroy(self):
        self.destroyed = True

    @contextmanager
    def installed(self):
        with patch.dict(sys.modules, {"tkinter": self.tk, "tkinter.ttk": self.ttk}):
            yield


class RecoveryGuiTests(unittest.TestCase):
    def run_gui(self, script, *, marker=None, activity=None, recover=None, launch=None):
        marker = marker or Mock(return_value=(ROOT, {"schema": 1, "backupFile": "SYNTHETIC-NOT-OPENED"}))
        activity = activity or Mock(return_value=(0, True))
        recover = recover or Mock()
        launch = launch or Mock()
        fixture = TkFixture(script)
        with fixture.installed():
            self.assertEqual(recovery.run_gui(load_marker=marker, activity=activity, recover=recover, launch=launch), 0)
        return fixture, marker, activity, recover, launch

    def test_show_and_cancel_do_not_enumerate_or_recover(self):
        def script(gui):
            entry = next(widget for widget in gui.widgets if "show" in widget.options)
            self.assertEqual(entry.options["show"], "●")
            gui.password.set(PASSWORD)
            gui.button("Закрыть").options["command"]()
            self.assertEqual(gui.password.get(), "")
        fixture, marker, activity, restore, launch = self.run_gui(script)
        self.assertTrue(fixture.destroyed)
        marker.assert_called_once_with()
        for call in (activity, restore, launch):
            call.assert_not_called()

    def test_missing_marker_disables_restore_and_never_reads_storage(self):
        def script(gui):
            self.assertEqual(gui.button("Восстановить").options["state"], "disabled")
            gui.password.set(PASSWORD)
            gui.button("Восстановить").options["command"]()
            self.assertEqual(gui.status.get(), recovery.MESSAGES["no_backup"])
            gui.protocols["WM_DELETE_WINDOW"]()
        fixture, _, activity, restore, launch = self.run_gui(script, marker=Mock(return_value=(ROOT, None)))
        self.assertTrue(fixture.destroyed)
        for call in (activity, restore, launch):
            call.assert_not_called()

    def test_metadata_exception_uses_fixed_error_without_exception_text(self):
        def script(gui):
            self.assertEqual(gui.status.get(), recovery.MESSAGES["io_error"])
            self.assertNotIn("SYNTHETIC-SECRET", gui.status.get())
            gui.button("Закрыть").options["command"]()
        _, _, activity, restore, launch = self.run_gui(script, marker=Mock(side_effect=OSError("SYNTHETIC-SECRET")))
        for call in (activity, restore, launch):
            call.assert_not_called()

    def test_explicit_restore_clears_masked_field_and_locks_close_until_success(self):
        entered, release, launched = threading.Event(), threading.Event(), threading.Event()
        def restore(_root, password):
            self.assertEqual(password, PASSWORD)
            entered.set()
            self.assertTrue(release.wait(2))
        def script(gui):
            gui.password.set(PASSWORD)
            gui.button("Восстановить").options["command"]()
            self.assertEqual(gui.password.get(), "")
            self.assertTrue(entered.wait(1))
            self.assertEqual(gui.button("Закрыть").options["state"], "disabled")
            gui.protocols["WM_DELETE_WINDOW"]()
            self.assertFalse(gui.destroyed)
            release.set()
            self.assertTrue(launched.wait(1))
            # Wait for the synthetic result event, not a real application.
            for thread in threading.enumerate():
                if thread.name == "MData-recovery":
                    thread.join(1)
            gui.callbacks.pop(0)()
            self.assertTrue(gui.destroyed)
        fixture, _, activity, _, launch = self.run_gui(script, recover=Mock(side_effect=restore), launch=Mock(side_effect=launched.set))
        self.assertTrue(fixture.destroyed)
        activity.assert_called_once()
        launch.assert_called_once()

    def test_invalid_password_gui_failure_does_not_dispatch_or_persist(self):
        def script(gui):
            gui.password.set("short")
            gui.button("Восстановить").options["command"]()
            self.assertEqual(gui.password.get(), "")
            self.assertEqual(gui.status.get(), recovery.MESSAGES["invalid_password"])
            gui.button("Закрыть").options["command"]()
        _, _, activity, restore, launch = self.run_gui(script)
        for call in (activity, restore, launch):
            call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
