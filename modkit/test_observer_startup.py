"""Finite startup stages with synthetic profiles, devices and HTTP workers.

No native process, real profile, credentials, UI or local listener is accessed.
"""
from contextlib import ExitStack, nullcontext
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import frida
from ghost_store import GhostSettingsError
from message_retention_service import RetentionServiceError
from notification_policy import NotificationPolicyError
import privacy_server as target
import startup_status


PRIVATE = "PRIVATE_EXCEPTION_VALUE_MUST_NEVER_APPEAR"


class PoisonError(RuntimeError):
    def __str__(self):
        raise AssertionError("Exception text must never be read")


class StartupStagesTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory(
            prefix="observer-startup-test-", dir=Path(__file__).resolve().parent.parent))
        self.root = Path(self.temp)
        self.manifest = self.root / "runtime" / "mod" / "mod-manifest.json"
        self.exe = self.manifest.parent / "max.exe"
        self.profile = self.root / "MXMOD"
        self.config = self.profile / "config.json"
        self.profile.mkdir()
        self.args = SimpleNamespace(manifest=self.manifest, launch=True, seconds=0,
                                    idle_seconds=20, summary=None)
        self.monitor, self.server, self.worker, self.http = Mock(), Mock(), Mock(), Mock()
        self.worker.ident = 1
        self.worker.is_alive.return_value = True
        self.monitor.activity.return_value = (1, True)
        self.monitor.main_activity.return_value = (1, True)
        self.optimization = Mock()
        self.begin = self.mock(startup_status, "begin", return_value=None)
        self.stage = self.mock(startup_status, "stage", return_value=False)
        self.fail = self.mock(startup_status, "fail", return_value=False)
        self.ready = self.mock(startup_status, "ready", return_value=False)
        self.mock(target.launch_mod.desktop_process, "is_desktop_child", return_value=True)
        self.verify = self.mock(target.launch_mod, "verified_executable", return_value=self.exe)
        self.mock(target.launch_mod, "runtime_identity", return_value="a" * 64)
        self.prepare = self.mock(target.launch_mod, "prepare_config", return_value=(self.config, "synthetic"))
        self.mock(target.build_mod, "verify", return_value={"files": []})
        self.constructor = self.mock(target, "Monitor", return_value=self.monitor)
        self.bind = self.mock(target, "LoopbackServer", return_value=self.server)
        self.mock(target.threading, "Thread", side_effect=[self.worker, self.http])
        self.mock(target.frida, "shutdown")
        self.mock(target, "bounded_rpc", side_effect=lambda call: call())
        self.mock(target.time, "sleep", side_effect=KeyboardInterrupt)
        self.stack.enter_context(patch("builtins.print"))
        self.stack.enter_context(patch("session_credentials._api", side_effect=AssertionError("WinCred forbidden")))
        self.stack.enter_context(patch("frida.get_local_device", side_effect=AssertionError("Real device forbidden")))
        self.chat_locks = self.stack.enter_context(patch("chat_lock_service.ChatLockService"))
        self.stack.enter_context(patch("inline_media.InlineMediaService"))
        self.stack.enter_context(patch("optimization_store.OptimizationSettingsStore", return_value=self.optimization))
        self.stack.enter_context(patch("optimization_store.native_client_running", return_value=False))

    def mock(self, owner, name, **kwargs):
        return self.stack.enter_context(patch.object(owner, name, **kwargs))

    def run_main(self):
        with patch("sys.argv", ["privacy_server.py", "--manifest", str(self.manifest), "--launch"]):
            target.main()

    def assert_failure(self, failure, code, stage):
        self.assertEqual(failure.exception.code, code)
        self.assertEqual(str(failure.exception), code)
        self.assertNotIn(PRIVATE, str(failure.exception))
        self.assertIn(unittest.mock.call(stage), self.stage.call_args_list)
        self.fail.assert_any_call(code)
        self.ready.assert_not_called()

    def test_verification_failure_stops_before_frida_stores_bind_or_spawn(self):
        self.verify.side_effect = PoisonError(PRIVATE)
        with self.assertRaises(startup_status.StartupError) as failure:
            self.run_main()
        self.assert_failure(failure, "payload_invalid", "verify")
        self.constructor.assert_not_called()
        self.prepare.assert_not_called()
        self.bind.assert_not_called()
        self.monitor.launch.assert_not_called()

    def test_frida_failure_stops_before_profile_or_bind(self):
        self.constructor.side_effect = frida.TransportError(PRIVATE)
        with self.assertRaises(startup_status.StartupError) as failure:
            self.run_main()
        self.assert_failure(failure, "observer_start_failed", "frida_initialize")
        self.prepare.assert_not_called()
        self.bind.assert_not_called()
        self.monitor.launch.assert_not_called()

    def test_store_failure_stops_before_binding(self):
        self.chat_locks.side_effect = PoisonError(PRIVATE)
        with self.assertRaises(startup_status.StartupError) as failure:
            self.run_main()
        self.assert_failure(failure, "observer_start_failed", "setup_stores")
        self.bind.assert_not_called()
        self.monitor.launch.assert_not_called()

    def test_exclusive_port_failure_never_activates_or_spawns(self):
        self.bind.side_effect = OSError(PRIVATE)
        with self.assertRaises(startup_status.StartupError) as failure:
            self.run_main()
        self.assert_failure(failure, "port_busy", "bind")
        self.optimization.activate.assert_not_called()
        self.monitor.launch.assert_not_called()

    def test_optimization_failure_closes_owned_server_before_spawn(self):
        self.optimization.activate.side_effect = PoisonError(PRIVATE)
        with self.assertRaises(startup_status.StartupError) as failure:
            self.run_main()
        self.assert_failure(failure, "optimization_prepare_failed", "optimization")
        self.server.server_close.assert_called_once()
        self.http.start.assert_not_called()
        self.monitor.launch.assert_not_called()

    def test_unexpected_spawn_failure_closes_http_and_never_starts_worker(self):
        self.worker.ident = None
        self.monitor.launch.side_effect = frida.ProcessNotFoundError(PRIVATE)
        with self.assertRaises(startup_status.StartupError) as failure:
            self.run_main()
        self.assert_failure(failure, "native_spawn_failed", "native_spawn")
        self.worker.start.assert_not_called()
        self.worker.join.assert_not_called()
        self.server.shutdown.assert_called_once()
        self.server.server_close.assert_called_once()

    def test_ready_waits_for_scan_and_is_published_once(self):
        self.monitor.activity.side_effect = [(0, False), (1, True), (1, True)]
        self.monitor.main_activity.side_effect = [(0, False), (1, True), (1, True)]
        target.time.sleep.side_effect = [None, None, KeyboardInterrupt]
        self.run_main()
        self.ready.assert_called_once_with()
        self.fail.assert_not_called()
        self.begin.assert_called_once_with(self.manifest, role="observe")
        self.monitor.launch.assert_called_once_with(self.exe)
        self.assertEqual([call.args[0] for call in self.stage.call_args_list],
                         ["verify", "frida_initialize", "setup_stores", "bind",
                          "optimization", "observer", "native_spawn", "observer"])
        self.assertEqual(list(self.profile.iterdir()), [])

    def test_worker_abort_before_ready_is_finite_and_cleans_server(self):
        self.worker.is_alive.return_value = False
        with self.assertRaises(startup_status.StartupError) as failure:
            self.run_main()
        self.assert_failure(failure, "observer_worker_stopped", "observer")
        self.server.shutdown.assert_called_once()
        self.server.server_close.assert_called_once()


class NativeProtectionStartupTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("frida.get_local_device", side_effect=AssertionError("Real device forbidden")))
        self.stack.enter_context(patch("session_credentials._api", side_effect=AssertionError("WinCred forbidden")))
        self.stage = self.stack.enter_context(patch.object(startup_status, "stage", return_value=False))
        self.fail = self.stack.enter_context(patch.object(startup_status, "fail", return_value=False))
        self.stack.enter_context(patch("external_environment.native_environment", return_value=nullcontext({"SYNTHETIC": "1"})))
        self.stack.enter_context(patch("business_environment.child_environment", return_value={"SYNTHETIC": "1"}))
        self.stack.enter_context(patch("optimization_store.child_environment", return_value={"SYNTHETIC": "1"}))
        self.stack.enter_context(patch.object(target.launch_mod, "prepare_config", return_value=(Path("D:/synthetic/MXMOD/config.json"), "synthetic")))
        self.monitor = target.Monitor.__new__(target.Monitor)
        self.monitor.device = Mock()
        self.monitor.device.spawn.return_value = 4242
        self.monitor.attach = Mock()
        self.monitor.activity_lock = threading.Lock()
        self.monitor.client_pids = {99}
        self.monitor.main_client_pids = {99}
        self.monitor.profile_pids = {99}

    def test_spawn_failure_has_no_pid_to_kill_and_never_attaches(self):
        self.monitor.device.spawn.side_effect = PoisonError(PRIVATE)
        with self.assertRaises(startup_status.StartupError) as failure:
            self.monitor.launch(Path("D:/synthetic/max.exe"))
        self.assertEqual(failure.exception.code, "native_spawn_failed")
        self.monitor.attach.assert_not_called()
        self.monitor.device.resume.assert_not_called()
        self.monitor.device.kill.assert_not_called()

    def test_failed_protection_never_resumes_and_kills_only_its_new_pid(self):
        cases = [(PoisonError(PRIVATE), "protection_ack_failed"),
                 (GhostSettingsError("invalid_store"), "ghost_ack_failed"),
                 (RetentionServiceError("invalid_request"), "retention_ack_failed"),
                 (NotificationPolicyError(), "notification_ack_failed"),
                 (startup_status.StartupError("ghost_ack_failed"), "ghost_ack_failed")]
        for error, code in cases:
            with self.subTest(code=code):
                self.monitor.device.reset_mock()
                self.monitor.attach.side_effect = error
                with self.assertRaises(startup_status.StartupError) as failure:
                    self.monitor.launch(Path("D:/synthetic/max.exe"))
                self.assertEqual(failure.exception.code, code)
                self.assertNotIn(PRIVATE, str(failure.exception))
                self.monitor.device.resume.assert_not_called()
                self.monitor.device.kill.assert_called_once_with(4242)
                self.assertEqual(self.monitor.client_pids, {99})
                self.fail.assert_any_call(code)

    def test_kill_error_cannot_replace_primary_protection_failure(self):
        self.monitor.attach.side_effect = startup_status.StartupError("retention_ack_failed")
        self.monitor.device.kill.side_effect = PoisonError(PRIVATE)
        with self.assertRaises(startup_status.StartupError) as failure:
            self.monitor.launch(Path("D:/synthetic/max.exe"))
        self.assertEqual(failure.exception.code, "retention_ack_failed")
        self.monitor.device.resume.assert_not_called()


if __name__ == "__main__":
    unittest.main()
