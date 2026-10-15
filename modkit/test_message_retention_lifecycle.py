"""Retention attach/lifecycle regressions: mocked Frida and stores, no native I/O."""
from contextlib import ExitStack
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

import frida
from ghost_runtime import GhostRuntime
from message_retention_service import MessageRetentionService, RetentionServiceError
from message_retention_store import RetentionStoreError
from privacy_rpc import SnapshotTimeout
import test_ghost_host as ghosts
import test_privacy_status as fixtures


def retention_native(enabled=False, *, ready=True, error=None):
    return {"ready": ready, "enabled": enabled, "retainedEvents": 0,
            "retainedMessages": 0, "error": error, "historySyncVerified": False}


def snapshot(enabled=False, *, ready=True, error=None, ghost_policy=None):
    value = {"stats": {"tracerHooks": 1, "transportHooks": 1}, "tracerCounts": {},
             "connections": {}, "packets": [], "omittedPackets": 0, "omittedBytes": 0,
             "failures": 0, "retention": retention_native(enabled, ready=ready, error=error)}
    if ghost_policy is not None:
        value["ghost"] = ghosts.native_ghost(ghost_policy)
    return value


def fake_service(enabled=False):
    service = MessageRetentionService.__new__(MessageRetentionService)
    service.settings, service.markers, service.received = Mock(), Mock(), Mock()
    service.settings.snapshot.return_value = {"schema": 1, "revision": "a" * 64, "enabled": enabled}
    service.markers.ensure_ready.return_value = True
    service.received.ensure_ready.return_value = True
    service.received.seed.return_value = []
    service._lock = threading.RLock()
    service._allowed, service._clients = set(), {}
    service._storage_failed = False
    service._known, service._known_updates, service._known_cursors = set(), [], {}
    service._known_loaded = False
    return service


class RetentionLifecycleTests(unittest.TestCase):
    def monitor(self, *, enabled=False, ghost=False):
        monitor, path = fixtures.LifecycleTests.fixture_monitor(self)
        monitor.root = path.parent
        monitor.specs, monitor.source = {False: {}, True: {}}, "// synthetic observer"
        monitor.ghost_store = None
        monitor.ghost_runtime = None
        monitor.message_retention = fake_service(enabled)
        if ghost:
            monitor.ghost_store = Mock()
            monitor.ghost_store.snapshot.return_value = ghosts.desired()
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
        source = stack.enter_context(patch("message_retention_native.source_for", return_value="// RETENTION SYNTHETIC"))
        self.addCleanup(stack.close)
        return session, script, source

    def tick_mocks(self, path):
        stack = ExitStack()
        stack.enter_context(patch("privacy_server.process_path", return_value=path))
        stack.enter_context(patch("privacy_server.current_tcp_endpoints", return_value=[]))
        stack.enter_context(patch("privacy_server.bounded_rpc", side_effect=lambda call: call()))
        self.addCleanup(stack.close)

    def test_preflight_source_load_and_enabled_ack_precede_new_process_resume(self):
        monitor, path = self.monitor(enabled=True)
        channel = Mock(); channel.snapshot.return_value = snapshot(True)
        session, script, source = self.attach_mocks(monitor, channel)
        ghosts.GhostHostTests.launch_environment(self)
        monitor.device.spawn.return_value = 123
        order, original_register = [], monitor.message_retention.register
        monitor.message_retention.markers.ensure_ready.side_effect = lambda: order.append("storage") or True
        def register(pid):
            order.append("register")
            original_register(pid)
        source.side_effect = lambda *args, **kwargs: order.append("source") or "// RETENTION SYNTHETIC"
        script.load.side_effect = lambda: order.append("load")
        channel.snapshot.side_effect = lambda **kwargs: order.append("ack") or snapshot(True)
        monitor.device.resume.side_effect = lambda pid: order.append("resume")
        with patch.object(monitor.message_retention, "register", side_effect=register):
            self.assertEqual(monitor.launch(path), 123)
        self.assertEqual(order, ["storage", "register", "source", "load", "ack", "resume"])
        source.assert_called_once_with(monitor.root, enabled=True, known_messages=[])
        self.assertIn("RETENTION SYNTHETIC", session.create_script.call_args.args[0])
        self.assertTrue(monitor.message_retention.snapshot()["runtime"]["applied"])
        monitor.device.kill.assert_not_called()

    def test_failed_preflight_or_source_generation_leaves_no_registered_pid(self):
        for phase in ("preflight", "source"):
            with self.subTest(phase=phase):
                monitor, path = self.monitor(enabled=True)
                channel = Mock()
                session, script, source = self.attach_mocks(monitor, channel)
                ghosts.GhostHostTests.launch_environment(self)
                monitor.device.spawn.return_value = 123
                monitor.client_pids.add(987)
                if phase == "preflight":
                    monitor.message_retention.markers.ensure_ready.side_effect = RetentionStoreError("invalid_store")
                else:
                    source.side_effect = ValueError("synthetic source failure")
                with self.assertRaises((RetentionServiceError, ValueError)):
                    monitor.launch(path)
                self.assertEqual(monitor.message_retention._allowed, set())
                self.assertEqual(monitor.message_retention._clients, {})
                self.assertEqual(monitor.sessions, {})
                self.assertEqual(monitor.client_pids, {987})
                monitor.device.resume.assert_not_called()
                monitor.device.kill.assert_called_once_with(123)
                session.detach.assert_called_once()
                session.create_script.assert_not_called()

    def test_calls_service_never_registers_preflights_or_loads_retention(self):
        monitor, _path = self.monitor(enabled=True)
        channel = Mock()
        session, script, source = self.attach_mocks(monitor, channel)
        monitor.attach(456, service=True)
        monitor.message_retention.settings.snapshot.assert_not_called()
        monitor.message_retention.markers.ensure_ready.assert_not_called()
        source.assert_not_called()
        channel.snapshot.assert_not_called()
        self.assertEqual(monitor.message_retention._allowed, set())
        self.assertNotIn("RETENTION SYNTHETIC", session.create_script.call_args.args[0])

    def test_default_off_may_resume_but_unready_native_guard_never_claims_applied(self):
        monitor, path = self.monitor(enabled=False)
        channel = Mock(); channel.snapshot.return_value = snapshot(False, ready=False, error="waiting_for_modules")
        self.attach_mocks(monitor, channel)
        ghosts.GhostHostTests.launch_environment(self)
        monitor.device.spawn.return_value = 123
        self.assertEqual(monitor.launch(path), 123)
        monitor.device.resume.assert_called_once_with(123)
        runtime = monitor.message_retention.snapshot()["runtime"]
        self.assertFalse(runtime["ready"])
        self.assertFalse(runtime["enabled"])
        self.assertFalse(runtime["applied"])
        self.assertEqual(runtime["error"], "waiting_for_modules")

    def test_enabled_without_matching_ack_never_resumes_and_cleans_only_new_pid(self):
        for failure in ("unready", "off", "timeout"):
            with self.subTest(failure=failure):
                monitor, path = self.monitor(enabled=True)
                channel = Mock()
                if failure == "timeout":
                    channel.snapshot.side_effect = SnapshotTimeout("synthetic")
                else:
                    channel.snapshot.return_value = snapshot(failure != "off", ready=failure != "unready",
                        error="waiting_for_modules" if failure == "unready" else None)
                session, script, source = self.attach_mocks(monitor, channel)
                ghosts.GhostHostTests.launch_environment(self)
                monitor.device.spawn.return_value = 123
                monitor.client_pids.add(987)
                with self.assertRaises((ValueError, SnapshotTimeout)):
                    monitor.launch(path)
                monitor.device.resume.assert_not_called()
                monitor.device.kill.assert_called_once_with(123)
                session.detach.assert_called_once()
                channel.close.assert_called_once()
                self.assertEqual(monitor.message_retention._allowed, set())
                self.assertEqual(monitor.sessions, {})
                self.assertEqual(monitor.client_pids, {987})

    def test_snapshot_timeout_preserves_retention_and_optional_ghost_then_recovers_ack(self):
        for ghost in (False, True):
            with self.subTest(ghost=ghost):
                monitor, path = self.monitor(enabled=True, ghost=ghost)
                self.tick_mocks(path)
                policy = monitor.ghost_store.snapshot() if ghost else None
                session, script, stale, replacement = Mock(), Mock(), Mock(), Mock()
                stale.snapshot.side_effect = SnapshotTimeout("synthetic")
                replacement.snapshot.return_value = snapshot(True, ghost_policy=policy)
                monitor.sessions[123] = session, script, stale
                monitor.roles[123] = "main"
                monitor.message_retention.register(123)
                monitor.message_retention.update(123, retention_native(True))
                if ghost:
                    monitor.ghost_runtime.update(123, ghosts.native_ghost(policy))
                with patch("privacy_server.SnapshotChannel", return_value=replacement):
                    monitor.tick()
                session.detach.assert_not_called()
                script.unload.assert_not_called()
                stale.close.assert_called_once()
                self.assertIs(monitor.sessions[123][1], script)
                self.assertIs(monitor.sessions[123][2], replacement)
                self.assertEqual(monitor.message_retention._allowed, {123})
                self.assertFalse(monitor.message_retention.snapshot()["runtime"]["ready"])
                if ghost:
                    self.assertFalse(monitor.ghost_runtime.status(policy)["applied"])
                monitor.tick()
                self.assertTrue(monitor.message_retention.snapshot()["runtime"]["applied"])
                if ghost:
                    self.assertTrue(monitor.ghost_runtime.status(policy)["applied"])
                session.detach.assert_not_called()

    def test_dead_pid_is_removed_from_both_runtime_and_event_authorization(self):
        monitor, path = self.monitor(enabled=False)
        channel = Mock(); channel.snapshot.return_value = snapshot()
        session, script, source = self.attach_mocks(monitor, channel)
        monitor.attach(123, service=False)
        monitor.device.enumerate_processes.return_value = []
        monitor.tick()
        self.assertEqual(monitor.message_retention._allowed, set())
        self.assertEqual(monitor.message_retention._clients, {})
        self.assertEqual(monitor.sessions, {})
        channel.close.assert_called_once()
        session.detach.assert_called_once()
        payload = {"kind": "retained-delete", "schema": 1, "accountId": "1", "chatId": "-1", "messageIds": ["1"]}
        with self.assertRaises(RetentionServiceError):
            monitor.message_retention.record(123, payload)
        monitor.message_retention.markers.mark.assert_not_called()

    def test_datastore_failure_notifies_native_and_rejects_future_marker_writes(self):
        monitor, path = self.monitor(enabled=True)
        self.tick_mocks(path)
        session, script, channel = Mock(), Mock(), Mock()
        monitor.sessions[123] = session, script, channel
        monitor.roles[123] = "main"
        monitor.message_retention.register(123)
        monitor.message_retention.update(123, retention_native(True))
        monitor.message_retention.markers.mark.side_effect = RetentionStoreError("no_space")
        available = [True]
        def post(value):
            if value["type"] == "valmax-retention-storage":
                available[0] = value["available"]
        script.post.side_effect = post
        channel.snapshot.side_effect = lambda **kwargs: snapshot(True, ready=available[0],
            error=None if available[0] else "storage_unavailable")
        event = {"kind": "retained-delete", "schema": 1, "accountId": "1", "chatId": "-1", "messageIds": ["1"]}
        before = monitor.observation.snapshot()
        monitor._message({"type": "send", "payload": event}, None, role="main", pid=123)
        self.assertFalse(available[0])
        self.assertEqual(before, monitor.observation.snapshot())
        self.assertTrue(monitor.message_retention._storage_failed)
        monitor.tick()
        self.assertFalse(monitor.message_retention.snapshot()["runtime"]["ready"])
        monitor._message({"type": "send", "payload": dict(event, messageIds=["2"])}, None, role="main", pid=123)
        self.assertEqual(monitor.message_retention.markers.mark.call_count, 1)
        session.detach.assert_not_called()
        script.unload.assert_not_called()

    def test_failed_immediate_storage_notice_is_retried_before_next_snapshot(self):
        monitor, path = self.monitor(enabled=True, ghost=True)
        self.tick_mocks(path)
        policy = monitor.ghost_store.snapshot()
        monitor.ghost_runtime.update(123, ghosts.native_ghost(policy))
        monitor.message_retention.register(123)
        monitor.message_retention.update(123, retention_native(True))
        monitor.message_retention.markers.mark.side_effect = RetentionStoreError("no_space")
        session, script, channel = Mock(), Mock(), Mock()
        monitor.sessions[123] = session, script, channel
        monitor.roles[123] = "main"
        available, enabled, failures, order = [True], [True], [1], []
        def post(value):
            if value["type"] == "valmax-retention-storage":
                order.append("storage")
                if not value["available"] and failures[0]:
                    failures[0] -= 1
                    raise frida.InvalidOperationError("synthetic transient channel error")
                available[0] = value["available"]
            elif value["type"] == "valmax-retention-settings":
                order.append("settings")
                enabled[0] = value["enabled"]
        def read_snapshot(**kwargs):
            order.append("snapshot")
            return snapshot(enabled[0], ready=available[0],
                error=None if available[0] else "storage_unavailable", ghost_policy=policy)
        script.post.side_effect = post
        channel.snapshot.side_effect = read_snapshot
        event = {"kind": "retained-delete", "schema": 1, "accountId": "1", "chatId": "-1", "messageIds": ["1"]}
        monitor._message({"type": "send", "payload": event}, None, role="main", pid=123)
        self.assertTrue(available[0], "synthetic first notice must have failed")
        self.assertFalse(monitor.message_retention.storage_available())
        monitor.tick()
        self.assertEqual(order, ["storage", "storage", "settings", "snapshot"])
        self.assertFalse(available[0])
        self.assertFalse(enabled[0])
        self.assertTrue(monitor.ghost_runtime.status(policy)["applied"])
        self.assertEqual(monitor.message_retention.snapshot()["runtime"]["error"], "storage_unavailable")
        self.assertIn(123, monitor.sessions)
        session.detach.assert_not_called()
        script.unload.assert_not_called()

    def test_failed_known_post_keeps_delta_until_delivery_after_channel_recovery(self):
        monitor, path = self.monitor(enabled=True)
        self.tick_mocks(path)
        service = monitor.message_retention
        service.preflight_marker_store()
        service.register(123)
        service.update(123, retention_native(True))
        service.record_received(123, {"kind": "received-messages", "schema": 1,
            "accountId": "1", "chatId": "-2", "messageIds": ["9007199254740993"]})
        session, script, stale, replacement = Mock(), Mock(), Mock(), Mock()
        monitor.sessions[123] = session, script, stale
        monitor.roles[123] = "main"
        failures, known_posts = [1], []
        def post(value):
            if value["type"] == "valmax-retention-known":
                known_posts.append(value)
                if failures[0]:
                    failures[0] -= 1
                    raise frida.InvalidOperationError("synthetic delivery failure")
        script.post.side_effect = post
        replacement.snapshot.return_value = snapshot(True)
        with patch("privacy_server.SnapshotChannel", return_value=replacement):
            monitor.tick()
        self.assertEqual(service._known_cursors[123], 0)
        stale.snapshot.assert_not_called()
        monitor.tick()
        self.assertEqual(len(known_posts), 2)
        self.assertEqual(known_posts[0], known_posts[1])
        self.assertEqual(service._known_cursors[123], 1)
        self.assertTrue(service.snapshot()["runtime"]["applied"])
        session.detach.assert_not_called()
        script.unload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
