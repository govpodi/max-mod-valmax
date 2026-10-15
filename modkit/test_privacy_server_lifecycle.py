"""Observer startup cleanup uses mocks; never launches MAX or a server."""
from contextlib import ExitStack
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import frida
import mdata_service
import privacy_server as target
from startup_status import StartupError


class ServerLifecycleTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        # These tests invoke main. Keep every profile adapter synthetic even
        # when a production build adds a new lazy settings store.
        stack.enter_context(patch.object(target.launch_mod, "prepare_config",
                                        return_value=(Path("D:/synthetic/MXMOD/config.json"), "synthetic")))
        stack.enter_context(patch("chat_lock_service.ChatLockService"))
        stack.enter_context(patch("inline_media.InlineMediaService"))
        stack.enter_context(patch("optimization_store.OptimizationSettingsStore"))
        stack.enter_context(patch("optimization_store.native_client_running", return_value=False))
        stack.enter_context(patch("session_credentials._api", side_effect=AssertionError("WinCred forbidden")))
        for name in ("begin", "stage", "fail", "ready"):
            stack.enter_context(patch.object(target.startup_status, name, return_value=None))

    def test_launch_failure_closes_http_and_does_not_join_unstarted_worker(self):
        monitor, server, worker, http = Mock(), Mock(), Mock(), Mock()
        worker.ident = None
        monitor.launch.side_effect = frida.ProcessNotFoundError("synthetic")
        with patch("sys.argv", ["privacy_server.py", "--manifest", "D:/synthetic/manifest.json", "--launch"]), \
                patch.object(target.launch_mod.desktop_process, "is_desktop_child", return_value=True), \
                patch.object(target.launch_mod, "verified_executable", return_value=Path("D:/synthetic/max.exe")), \
                patch.object(target.launch_mod, "runtime_identity", return_value="a" * 64), \
                patch.object(target.build_mod, "verify", return_value={"files": []}), \
                patch.object(target, "Monitor", return_value=monitor), \
                patch.object(target, "LoopbackServer", return_value=server), \
                patch.object(target.frida, "shutdown") as shutdown, \
                patch.object(target, "bounded_rpc", side_effect=lambda call: call()), \
                patch.object(target.threading, "Thread", side_effect=[worker, http]), \
                patch("builtins.print"):
            with self.assertRaises(StartupError) as failure:
                target.main()
            self.assertEqual(failure.exception.code, "native_spawn_failed")
        http.start.assert_called_once()
        worker.start.assert_not_called()
        worker.join.assert_not_called()
        monitor.stopping.set.assert_called_once()
        server.shutdown.assert_called_once()
        server.server_close.assert_called_once()
        shutdown.assert_called_once_with()

    def test_sdk_shutdown_invoked_once_after_worker_stops_before_mdata_restart(self):
        monitor, server, worker, http, mdata = Mock(), Mock(), Mock(), Mock(), Mock()
        worker.ident = 1
        worker.is_alive.return_value = False
        monitor.activity.return_value = (0, True)
        mdata.restart_requested.return_value = True
        events = []
        server.shutdown.side_effect = lambda: events.append("http_shutdown")
        server.server_close.side_effect = lambda: events.append("http_closed")
        worker.join.side_effect = lambda **kwargs: events.append("worker_joined")
        worker.is_alive.side_effect = lambda: events.append("worker_stopped") or False

        def invoke(call):
            # Preserve the real one-argument API; an unexpected keyword must
            # fail before invocation instead of being hidden by a permissive mock.
            return call()

        with patch("sys.argv", ["privacy_server.py", "--manifest", "D:/synthetic/manifest.json"]), \
                patch("session_credentials._api", side_effect=AssertionError("WinCred forbidden")), \
                patch.object(target.launch_mod.desktop_process, "is_desktop_child", return_value=True), \
                patch.object(target.launch_mod, "verified_executable", return_value=Path("D:/synthetic/max.exe")), \
                patch.object(target.launch_mod, "runtime_identity", return_value="a" * 64), \
                patch.object(target.launch_mod, "prepare_config", return_value=(Path("D:/synthetic/MXMOD/config.json"), "synthetic")), \
                patch.object(target.launch_mod, "_quiet_process", side_effect=lambda *a: events.append("restart")) as restart, \
                patch.object(target.build_mod, "verify", return_value={"files": [{"relative_path": "max.exe", "patches": [{"id": "ui_privacy.mdata_synthetic"}]}]}), \
                patch.object(mdata_service, "MDataService", return_value=mdata), \
                patch.object(target, "Monitor", return_value=monitor), \
                patch.object(target, "LoopbackServer", return_value=server), \
                patch.object(target.frida, "shutdown", side_effect=lambda: events.append("sdk_shutdown")) as shutdown, \
                patch.object(target, "bounded_rpc", side_effect=invoke), \
                patch.object(target.threading, "Thread", side_effect=[worker, http]), \
                patch("builtins.print"):
            target.main()
        self.assertEqual(events, ["http_shutdown", "http_closed", "worker_joined", "worker_stopped", "sdk_shutdown", "restart"])
        shutdown.assert_called_once_with()
        restart.assert_called_once()
        monitor.launch.assert_not_called()
        self.assertEqual(mdata.restart_requested.call_args_list, [unittest.mock.call(), unittest.mock.call(recheck=True)])

    def test_mdata_error_return_rechecks_after_cleanup_and_suppresses_duplicate_spawn(self):
        monitor, server, worker, http, mdata = Mock(), Mock(), Mock(), Mock(), Mock()
        worker.ident = 1
        worker.is_alive.return_value = False
        monitor.activity.return_value = (0, True)
        events = []
        def requested(*, recheck=False):
            events.append("final_main_recheck" if recheck else "restart_requested")
            return not recheck  # A different main appeared during cleanup.
        mdata.restart_requested.side_effect = requested
        server.shutdown.side_effect = lambda: events.append("http_shutdown")
        server.server_close.side_effect = lambda: events.append("http_closed")
        worker.join.side_effect = lambda **kwargs: events.append("worker_joined")
        with patch("sys.argv", ["privacy_server.py", "--manifest", "D:/synthetic/manifest.json"]), \
                patch("session_credentials._api", side_effect=AssertionError("WinCred forbidden")), \
                patch.object(target.launch_mod.desktop_process, "is_desktop_child", return_value=True), \
                patch.object(target.launch_mod, "verified_executable", return_value=Path("D:/synthetic/max.exe")), \
                patch.object(target.launch_mod, "runtime_identity", return_value="a" * 64), \
                patch.object(target.launch_mod, "prepare_config", return_value=(Path("D:/synthetic/MXMOD/config.json"), "synthetic")), \
                patch.object(target.launch_mod, "_quiet_process") as restart, \
                patch.object(target.build_mod, "verify", return_value={"files": [{"relative_path": "max.exe", "patches": [{"id": "ui_privacy.mdata_synthetic"}]}]}), \
                patch.object(mdata_service, "MDataService", return_value=mdata) as service, \
                patch.object(target, "Monitor", return_value=monitor), \
                patch.object(target, "LoopbackServer", return_value=server), \
                patch.object(target.frida, "shutdown", side_effect=lambda: events.append("sdk_shutdown")), \
                patch.object(target, "bounded_rpc", side_effect=lambda call: call()), \
                patch.object(target.threading, "Thread", side_effect=[worker, http]), \
                patch("builtins.print"):
            target.main()
        self.assertEqual(events, ["restart_requested", "http_shutdown", "http_closed", "worker_joined",
                                  "sdk_shutdown", "final_main_recheck"])
        service.assert_called_once_with(Path("D:/synthetic/MXMOD"), monitor.profile_activity,
                                        restart_activity=monitor.restart_activity)
        restart.assert_not_called()
        monitor.launch.assert_not_called()

    def test_direct_unmarked_observer_refuses_before_profile_or_native_access(self):
        with patch("sys.argv", ["privacy_server.py", "--launch"]), \
                patch.object(target.launch_mod.desktop_process, "is_desktop_child", return_value=False), \
                patch.object(target.launch_mod, "verified_executable", side_effect=AssertionError("native verification accessed")), \
                patch.object(target, "Monitor", side_effect=AssertionError("native client accessed")), \
                patch.object(target.launch_mod, "prepare_config", side_effect=AssertionError("profile accessed")):
            with self.assertRaises(ValueError):
                target.main()


if __name__ == "__main__":
    unittest.main()
