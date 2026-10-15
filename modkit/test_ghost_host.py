"""Independent ghost host regressions using synthetic processes and D: stores.

Never starts/attaches a native process, reads a real profile, or uses WinCred.
"""
from contextlib import ExitStack, nullcontext
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import launch_mod
from startup_status import StartupError
from ghost_runtime import GhostRuntime
from ghost_store import GhostSettingsError, GhostSettingsStore, defaults
from privacy_rpc import SnapshotTimeout, SnapshotUnavailable
from privacy_server import Monitor, PORT, handler_factory
import test_privacy_status as privacy_fixtures


def desired(enabled=True, revision="a" * 64, **flags):
    return {"schema": 1, "revision": revision,
            "settings": {**defaults(), "enabled": enabled, **flags}}


def native_ghost(policy, *, ready=True, revision=None):
    return {"ready": ready, "features": {"hideTyping": ready, "hideRead": ready},
            "revision": policy["revision"] if revision is None else revision,
            **policy["settings"], "blocked": {"typing": 0, "read": 0},
            "error": None if ready else "waiting_for_core"}


def native_snapshot(policy, **changes):
    return {"stats": {"tracerHooks": 1, "transportHooks": 1}, "tracerCounts": {},
            "connections": {}, "packets": [], "omittedPackets": 0, "omittedBytes": 0,
            "failures": 0, "ghost": native_ghost(policy, **changes)}


class GhostHostTests(unittest.TestCase):
    def monitor(self, policy=None):
        monitor, path = privacy_fixtures.LifecycleTests.fixture_monitor(self)
        monitor.root = path.parent
        monitor.specs, monitor.source = {False: {}, True: {}}, "// synthetic observer"
        monitor.ghost_store = Mock()
        monitor.ghost_store.snapshot.return_value = policy or desired()
        monitor.ghost_runtime = GhostRuntime(clock=lambda: 100.0)
        return monitor, path

    def attach_mocks(self, monitor, channel):
        session, script = Mock(), Mock()
        monitor.device.attach.return_value = session
        session.create_script.return_value = script
        stack = ExitStack()
        stack.enter_context(patch("privacy_server.SnapshotChannel", return_value=channel))
        stack.enter_context(patch("privacy_server.bounded_rpc", side_effect=lambda call: call()))
        stack.enter_context(patch("ghost_native.specification", return_value={"schema": 1, "synthetic": True}))
        self.addCleanup(stack.close)
        return session, script

    def launch_environment(self):
        stack = ExitStack()
        stack.enter_context(patch("external_environment.native_environment", side_effect=lambda: nullcontext({"SYNTHETIC": "1"})))
        stack.enter_context(patch("launch_mod.prepare_config", return_value=(Path("D:/synthetic/MXMOD/config.json"), "synthetic")))
        stack.enter_context(patch("business_environment.child_environment", return_value={"SYNTHETIC": "1"}))
        self.addCleanup(stack.close)

    def tick_environment(self, path):
        stack = ExitStack()
        stack.enter_context(patch("privacy_server.process_path", return_value=path))
        stack.enter_context(patch("privacy_server.current_tcp_endpoints", return_value=[]))
        stack.enter_context(patch("privacy_server.bounded_rpc", side_effect=lambda call: call()))
        self.addCleanup(stack.close)

    def test_initial_enabled_policy_requires_ack_before_resume(self):
        policy = desired()
        monitor, path = self.monitor(policy)
        channel = Mock(); channel.snapshot.return_value = native_snapshot(policy)
        session, script = self.attach_mocks(monitor, channel)
        self.launch_environment(); monitor.device.spawn.return_value = 123
        order = []
        script.load.side_effect = lambda: order.append("load")
        channel.snapshot.side_effect = lambda **_: order.append("ack") or native_snapshot(policy)
        monitor.device.resume.side_effect = lambda _pid: order.append("resume")
        self.assertEqual(monitor.launch(path), 123)
        self.assertEqual(order, ["load", "ack", "resume"])
        self.assertEqual(monitor.ghost_runtime.status(policy)["state"], "active")
        source = session.create_script.call_args.args[0]
        prefix = source.split(";\n", 1)[0].removeprefix("const MONITOR=")
        config = json.loads(prefix)
        self.assertEqual(config["ghostSettings"], {key: policy[key] for key in ("revision", "settings")})
        self.assertIn("function ghostSnapshot()", source)
        monitor.device.kill.assert_not_called(); session.detach.assert_not_called()

    def test_unready_wrong_revision_or_timeout_never_resumes_and_cleans_new_pid(self):
        for failure in ("unready", "wrong_revision", "timeout"):
            with self.subTest(failure=failure):
                policy = desired(); monitor, path = self.monitor(policy)
                channel = Mock()
                if failure == "timeout": channel.snapshot.side_effect = SnapshotTimeout("PRIVATE_VALUE")
                else: channel.snapshot.return_value = native_snapshot(policy, ready=failure != "unready",
                    revision="b" * 64 if failure == "wrong_revision" else None)
                session, _script = self.attach_mocks(monitor, channel)
                self.launch_environment(); monitor.device.spawn.return_value = 123
                monitor.client_pids.add(987)
                with self.assertRaises((ValueError, SnapshotTimeout)):
                    monitor.launch(path)
                monitor.device.resume.assert_not_called()
                monitor.device.kill.assert_called_once_with(123)
                channel.close.assert_called_once(); session.detach.assert_called_once()
                self.assertEqual(monitor.sessions, {}); self.assertEqual(monitor.roles, {})
                self.assertEqual(monitor.client_pids, {987})
                self.assertEqual(monitor.ghost_runtime.status(policy)["state"], "unavailable")

    def test_initial_store_error_detaches_and_kills_new_pid_without_running_it(self):
        monitor, path = self.monitor(); channel = Mock()
        session, _script = self.attach_mocks(monitor, channel)
        monitor.ghost_store.snapshot.side_effect = GhostSettingsError("invalid_store")
        self.launch_environment(); monitor.device.spawn.return_value = 123
        with self.assertRaises(StartupError) as failure: monitor.launch(path)
        self.assertEqual(failure.exception.code, "ghost_ack_failed")
        monitor.device.resume.assert_not_called(); monitor.device.kill.assert_called_once_with(123)
        session.detach.assert_called_once(); channel.snapshot.assert_not_called()
        self.assertEqual(monitor.sessions, {})

    def test_disabled_policy_may_start_but_unready_runtime_does_not_claim_off_confirmed(self):
        policy = desired(False); monitor, path = self.monitor(policy)
        channel = Mock(); channel.snapshot.return_value = native_snapshot(policy, ready=False)
        self.attach_mocks(monitor, channel); self.launch_environment()
        monitor.device.spawn.return_value = 123
        self.assertEqual(monitor.launch(path), 123)
        monitor.device.resume.assert_called_once_with(123)
        self.assertEqual(monitor.ghost_runtime.status(policy)["state"], "unavailable")

    def test_service_never_receives_or_reads_ghost_policy(self):
        monitor, _path = self.monitor(); channel = Mock()
        session, _script = self.attach_mocks(monitor, channel)
        monitor.attach(456, service=True)
        monitor.ghost_store.snapshot.assert_not_called(); channel.snapshot.assert_not_called()
        source = session.create_script.call_args.args[0]
        config = json.loads(source.split(";\n", 1)[0].removeprefix("const MONITOR="))
        self.assertNotIn("ghost", config); self.assertNotIn("function ghostSnapshot()", source)

    def test_snapshot_timeout_keeps_native_script_and_recovers_channel_with_new_ack(self):
        first = desired(); second = desired(revision="b" * 64, hideTyping=False)
        monitor, path = self.monitor(second); self.tick_environment(path)
        session, script, stale, replacement = Mock(), Mock(), Mock(), Mock()
        stale.snapshot.side_effect = SnapshotTimeout("PRIVATE_VALUE")
        replacement.snapshot.return_value = native_snapshot(second)
        monitor.sessions[123] = session, script, stale; monitor.roles[123] = "main"
        monitor.ghost_runtime.update(123, native_ghost(first))
        order = []
        script.post.side_effect = lambda _value: order.append("policy")
        stale.snapshot.side_effect = lambda **_: (order.append("snapshot"), (_ for _ in ()).throw(SnapshotTimeout("PRIVATE_VALUE")))[1]
        with patch("privacy_server.SnapshotChannel", return_value=replacement):
            monitor.tick()
        self.assertEqual(order, ["policy", "snapshot"])
        self.assertEqual(script.post.call_args.args[0], {"type": "valmax-ghost-settings", "revision": second["revision"], "settings": second["settings"]})
        stale.close.assert_called_once(); session.detach.assert_not_called(); script.unload.assert_not_called()
        self.assertIs(monitor.sessions[123][1], script)
        self.assertIs(monitor.sessions[123][2], replacement)
        self.assertEqual(monitor.ghost_runtime.status(second)["state"], "unavailable")
        monitor.tick()
        self.assertEqual(monitor.ghost_runtime.status(second)["state"], "active")
        session.detach.assert_not_called(); script.unload.assert_not_called()
        self.assertEqual(monitor.failed, {})

    def test_failed_channel_recreation_keeps_existing_native_replacements(self):
        policy = desired(); monitor, path = self.monitor(policy); self.tick_environment(path)
        session, script, channel = Mock(), Mock(), Mock()
        channel.snapshot.side_effect = SnapshotUnavailable("PRIVATE_VALUE")
        monitor.sessions[123] = session, script, channel; monitor.roles[123] = "main"
        monitor.ghost_runtime.update(123, native_ghost(policy))
        with patch("privacy_server.SnapshotChannel", side_effect=SnapshotUnavailable("PRIVATE_VALUE")):
            monitor.tick()
        session.detach.assert_not_called(); script.unload.assert_not_called()
        self.assertIn(123, monitor.sessions)
        self.assertFalse(monitor.ghost_runtime.status(policy)["applied"])

    def test_live_store_error_never_detaches_active_rules(self):
        policy = desired(); monitor, path = self.monitor(policy); self.tick_environment(path)
        session, script, channel, replacement = Mock(), Mock(), Mock(), Mock()
        monitor.sessions[123] = session, script, channel; monitor.roles[123] = "main"
        monitor.ghost_runtime.update(123, native_ghost(policy))
        monitor.ghost_store.snapshot.side_effect = GhostSettingsError("invalid_store")
        with patch("privacy_server.SnapshotChannel", return_value=replacement): monitor.tick()
        script.post.assert_not_called(); channel.snapshot.assert_not_called()
        session.detach.assert_not_called(); script.unload.assert_not_called()
        self.assertEqual(monitor.ghost_runtime.status(policy)["state"], "unavailable")

    def test_discovery_store_failure_does_not_escape_and_unhook_existing_client(self):
        policy = desired(); monitor, path = self.monitor(policy); self.tick_environment(path)
        session, script, channel = Mock(), Mock(), Mock()
        channel.snapshot.return_value = native_snapshot(policy)
        monitor.sessions[123] = session, script, channel; monitor.roles[123] = "main"
        monitor.ghost_runtime.update(123, native_ghost(policy))
        monitor.device.enumerate_processes.return_value = [SimpleNamespace(pid=x, name="max.exe") for x in (123, 456)]
        monitor.attach = Mock(side_effect=GhostSettingsError("invalid_store"))
        monitor.tick()
        session.detach.assert_not_called(); script.unload.assert_not_called()
        self.assertIn(123, monitor.sessions); self.assertIn(456, monitor.failed)
        self.assertEqual(monitor.ghost_runtime.status(policy)["state"], "unavailable")

    def test_two_live_clients_require_both_acks_and_a_failed_one_is_not_forgotten(self):
        policy = desired(); monitor, path = self.monitor(policy); self.tick_environment(path)
        sessions, scripts, channels = {pid: Mock() for pid in (123, 456)}, {pid: Mock() for pid in (123, 456)}, {pid: Mock() for pid in (123, 456)}
        for pid in (123, 456):
            monitor.sessions[pid] = sessions[pid], scripts[pid], channels[pid]
            monitor.roles[pid] = "main"
            monitor.ghost_runtime.update(pid, native_ghost(policy))
            channels[pid].snapshot.return_value = native_snapshot(policy)
        monitor.device.enumerate_processes.return_value = [SimpleNamespace(pid=x, name="max.exe") for x in (123, 456)]
        channels[456].snapshot.side_effect = SnapshotTimeout("PRIVATE_VALUE")
        replacement = Mock(); replacement.snapshot.return_value = native_snapshot(policy)
        with patch("privacy_server.SnapshotChannel", return_value=replacement): monitor.tick()
        self.assertEqual(monitor.ghost_runtime.status(policy)["state"], "unavailable")
        sessions[456].detach.assert_not_called(); scripts[456].unload.assert_not_called()
        monitor.tick()
        self.assertEqual(monitor.ghost_runtime.status(policy)["state"], "active")
        # Disappearing native PID may be removed; the remaining ACK suffices.
        monitor.device.enumerate_processes.return_value = [SimpleNamespace(pid=123, name="max.exe")]
        monitor.tick()
        sessions[456].detach.assert_called_once(); replacement.close.assert_called_once()
        sessions[123].detach.assert_not_called()
        self.assertEqual(monitor.ghost_runtime.status(policy)["state"], "active")

    def test_stale_or_new_live_pid_does_not_inherit_another_process_confirmation(self):
        now = [100.0]; runtime = GhostRuntime(clock=lambda: now[0]); policy = desired()
        runtime.sync({123, 456}); runtime.update(123, native_ghost(policy))
        self.assertEqual(runtime.status(policy)["state"], "unavailable")
        runtime.update(456, native_ghost(policy)); self.assertTrue(runtime.status(policy)["applied"])
        now[0] = 111.0; runtime.update(123, native_ghost(policy))
        self.assertFalse(runtime.status(policy)["ready"])
        runtime.invalidate(456); now[0] = 125.0; runtime.update(123, native_ghost(policy))
        self.assertEqual(runtime.status(policy)["state"], "unavailable")
        runtime.sync({123}); self.assertEqual(runtime.status(policy)["state"], "active")

    def test_malformed_native_ghost_snapshot_is_not_confirmation_or_log_content(self):
        policy = desired(); monitor, path = self.monitor(policy); self.tick_environment(path)
        session, script, channel, replacement = Mock(), Mock(), Mock(), Mock()
        dirty = native_snapshot(policy); dirty["ghost"]["token"] = "PRIVATE_VALUE"
        channel.snapshot.return_value = dirty
        monitor.sessions[123] = session, script, channel; monitor.roles[123] = "main"
        monitor.ghost_runtime.update(123, native_ghost(policy))
        with patch("privacy_server.SnapshotChannel", return_value=replacement): monitor.tick()
        self.assertFalse(monitor.ghost_runtime.status(policy)["applied"])
        self.assertNotIn("PRIVATE_VALUE", json.dumps(monitor.observation.snapshot()))
        session.detach.assert_not_called(); script.unload.assert_not_called()

    def test_without_diagnostics_cannot_bypass_a_persisted_enabled_policy(self):
        store = Mock(); store.snapshot.return_value = desired()
        args = SimpleNamespace(without_diagnostics=True, manifest=Path("D:/synthetic/manifest.json"))
        with patch("ghost_store.GhostSettingsStore", return_value=store), \
                patch("launch_mod._quiet_process") as start, \
                patch("launch_mod.launch_diagnostics") as diagnostics:
            with self.assertRaises(ValueError):
                launch_mod._start_verified(args, Path("D:/synthetic/max.exe"), Path("D:/synthetic/MXMOD"))
        start.assert_not_called(); diagnostics.assert_not_called()


class GhostHTTPTests(unittest.TestCase):
    def setUp(self):
        scratch = Path("D:/ValMAX/audit"); scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="ghost-host-", dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "profile" / "ghost-settings.json"
        self.store = GhostSettingsStore(self.path)
        self.runtime = GhostRuntime(clock=lambda: 100.0)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(
            lambda: {"schema": 1}, ghost_settings=self.store, ghost_runtime=self.runtime))
        self.server.daemon_threads = True
        self.server.handle_error = Mock()
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start(); self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)

    def request(self, method="GET", path="/ghost-settings", body=None, headers=None, duplicate=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        actual = {"Host": f"127.0.0.1:{PORT}", **(headers or {})}
        if duplicate:
            connection.putrequest(method, path, skip_host=True)
            for key, value in actual.items(): connection.putheader(key, value)
            connection.putheader(*duplicate)
            connection.putheader("Content-Length", str(len(body or b"")))
            connection.endheaders(body)
        else:
            connection.request(method, path, body=body, headers=actual)
        response = connection.getresponse()
        result = response.status, dict(response.headers), response.read()
        connection.close(); return result

    def post(self, value=None, *, body=None, headers=None, duplicate=None):
        if body is None: body = json.dumps(value or self.store.snapshot()).encode("utf8")
        return self.request("POST", body=body, headers={"Content-Type": "application/json;charset=UTF-8",
            "X-Max-Mod-Local-Write": "ghost-settings-v1", **(headers or {})}, duplicate=duplicate)

    def test_save_and_ack_are_distinct_and_get_never_writes(self):
        before = self.store.snapshot()
        status, headers, body = self.request()
        value = json.loads(body)
        self.assertEqual(status, 200); self.assertFalse(self.path.exists())
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(value["runtime"]["state"], "unavailable")
        self.runtime.update(123, native_ghost(before))
        requested = {**before, "settings": {**before["settings"], "enabled": True}}
        status, _, body = self.post(requested)
        saved = json.loads(body)
        self.assertEqual(status, 200); self.assertTrue(self.path.exists())
        self.assertEqual(saved["runtime"]["state"], "pending")
        self.assertFalse(saved["runtime"]["applied"])
        self.runtime.update(123, native_ghost(saved))
        confirmed = json.loads(self.request()[2])
        self.assertEqual(confirmed["runtime"]["state"], "active")
        self.assertTrue(confirmed["runtime"]["applied"])
        self.assertEqual(set(confirmed), {"schema", "revision", "settings", "runtime"})

    def test_web_origins_wrong_host_duplicate_headers_and_simple_forms_cannot_write(self):
        before = self.store.snapshot()
        for headers in ({"Origin": "https://untrusted.invalid"}, {"Origin": "null"},
                        {"Host": "untrusted.invalid"}, {"X-Max-Mod-Local-Write": ""},
                        {"Content-Type": "text/plain"}, {"Transfer-Encoding": "chunked"}):
            with self.subTest(headers=headers): self.assertEqual(self.post(headers=headers)[0], 403)
        for duplicate in (("Host", f"127.0.0.1:{PORT}"), ("X-Max-Mod-Local-Write", "ghost-settings-v1")):
            with self.subTest(duplicate=duplicate): self.assertEqual(self.post(duplicate=duplicate)[0], 403)
        self.assertEqual(self.request("OPTIONS")[0], 405)
        self.assertEqual(self.store.snapshot(), before); self.assertFalse(self.path.exists())

    def test_schema_strict_flags_extra_keys_duplicates_and_stale_revision_fail_closed(self):
        before = self.store.snapshot()
        for value in ({**before, "schema": True}, {**before, "extra": "PRIVATE_VALUE"},
                      {**before, "settings": {**before["settings"], "enabled": 1}},
                      {**before, "settings": {**before["settings"], "hideLocation": True}},
                      {**before, "revision": "invalid"}):
            with self.subTest(value=value):
                status, _, body = self.post(value)
                self.assertEqual(status, 400); self.assertNotIn(b"PRIVATE_VALUE", body)
        for body in (b"not-json", b"\xff", b'{"schema":1,"schema":1}', b'{"schema":NaN}'):
            with self.subTest(body=body): self.assertEqual(self.post(body=body)[0], 400)
        self.assertFalse(self.path.exists())
        good = {**before, "settings": {**before["settings"], "enabled": True}}
        self.assertEqual(self.post(good)[0], 200)
        stale, _, body = self.post(before)
        self.assertEqual(stale, 409)
        self.assertEqual(json.loads(body), {"schema": 1, "error": "revision_conflict"})
        self.assertTrue(self.store.snapshot()["settings"]["enabled"])

    def test_corrupt_settings_have_only_fixed_public_error_and_remain_untouched(self):
        self.path.parent.mkdir()
        raw = b'{"schema":1,"settings":{"PRIVATE_VALUE":"bad"}}'
        self.path.write_bytes(raw)
        for method in ("GET", "POST"):
            if method == "GET": status, _, body = self.request()
            else: status, _, body = self.post(desired())
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(body), {"schema": 1, "error": "invalid_store"})
        self.assertEqual(self.path.read_bytes(), raw)

    def test_deep_untrusted_json_is_a_fixed_400_not_a_disconnected_handler(self):
        body = b'{"schema":1,"revision":"' + b'a' * 64 + b'","settings":' + b'[' * 20000 + b'0' + b']' * 20000 + b'}'
        status, _, response = self.post(body=body)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(response), {"schema": 1, "error": "invalid_request"})
        self.server.handle_error.assert_not_called()
        self.assertFalse(self.path.exists())


if __name__ == "__main__": unittest.main()
