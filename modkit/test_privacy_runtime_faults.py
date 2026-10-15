"""Finite observer fault metadata; synthetic sessions and stores only."""
import json
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import frida
from frida_errors import FRIDA_ERRORS
from ghost_runtime import GhostRuntime
from ghost_store import GhostSettingsError
from message_retention_service import RetentionServiceError
from privacy_rpc import SnapshotTimeout, SnapshotUnavailable
from privacy_server import _observer_state, _runtime_fault_code
from privacy_status import ERRORS, Observation
import test_ghost_host as ghosts
import test_message_retention_lifecycle as retention
import test_privacy_status as fixtures


PRIVATE = "PRIVATE_EXCEPTION_VALUE"


def fault_cases():
    retained, ghost = RetentionServiceError("invalid_request"), GhostSettingsError("invalid_settings")
    retained.args = ghost.args = (PRIVATE,)
    return [
        (SnapshotTimeout(PRIVATE), "runtime_snapshot_timeout"),
        (SnapshotUnavailable(PRIVATE), "runtime_snapshot_unavailable"),
        (frida.InvalidOperationError(PRIVATE), "runtime_session_lost"),
        (frida.TransportError(PRIVATE), "runtime_transport_failed"),
        (retained, "runtime_retention_ack_failed"),
        (ghost, "runtime_ghost_ack_failed"),
        (ValueError(PRIVATE), "runtime_schema_failed"),
        (KeyError(PRIVATE), "runtime_schema_failed"),
        (TypeError(PRIVATE), "runtime_schema_failed"),
    ]


class RuntimeFaultTests(unittest.TestCase):
    def test_classification_uses_types_without_exception_text_or_args(self):
        class PoisonValueError(ValueError):
            def __str__(self):
                raise AssertionError("Exception text must never be read")

        for error, expected in fault_cases() + [(PoisonValueError(PRIVATE), "runtime_schema_failed")]:
            with self.subTest(code=expected):
                self.assertEqual(_runtime_fault_code(error), expected)
                self.assertIn(expected, ERRORS)
        for kind in FRIDA_ERRORS:
            expected = "runtime_session_lost" if kind is frida.InvalidOperationError else "runtime_transport_failed"
            self.assertEqual(_runtime_fault_code(kind(PRIVATE)), expected)

    def test_observation_accepts_only_finite_error_codes(self):
        observation = Observation()
        for code in ERRORS:
            observation.error(code)
        observation.error(PRIVATE)
        observation.error({"private": PRIVATE})
        errors = observation.snapshot()["errors"]
        self.assertEqual(set(errors), ERRORS)
        self.assertEqual(errors["hook_failed"], 3)
        self.assertNotIn(PRIVATE, json.dumps(observation.snapshot()))
        observation.errors["runtime_worker_failed"] = 1000000
        observation.error("runtime_worker_failed")
        self.assertEqual(observation.snapshot()["errors"]["runtime_worker_failed"], 1000000)

    def protected_monitor(self):
        monitor, path = fixtures.LifecycleTests.fixture_monitor(self)
        monitor.message_retention = retention.fake_service(enabled=True)
        monitor.message_retention.preflight_marker_store()
        monitor.message_retention.register(123)
        monitor.message_retention.update(123, retention.retention_native(True))
        monitor.ghost_store = None
        monitor.ghost_runtime = None
        session, script, channel = Mock(), Mock(), Mock()
        channel.snapshot.return_value = retention.snapshot(True)
        monitor.sessions[123] = session, script, channel
        monitor.roles[123] = "main"
        monitor.observation.attach(123, tracer_hooks=1, transport_hooks=1, role="main")
        return monitor, path, session, script, channel

    def test_attach_snapshot_timeout_cleans_only_new_session_and_retries(self):
        monitor, path, existing, existing_script, existing_channel = self.protected_monitor()
        monitor.root, monitor.specs, monitor.source = path.parent, {False: {}}, "// synthetic"
        monitor.device.enumerate_processes.return_value = [
            SimpleNamespace(pid=123, name="max.exe"), SimpleNamespace(pid=456, name="max.exe")]
        failed, succeeded, failed_script, succeeded_script = Mock(), Mock(), Mock(), Mock()
        failed.create_script.return_value = failed_script
        succeeded.create_script.return_value = succeeded_script
        monitor.device.attach.side_effect = [failed, succeeded]
        failed_channel, succeeded_channel = Mock(), Mock()
        failed_channel.snapshot.side_effect = SnapshotTimeout(PRIVATE)
        succeeded_channel.snapshot.return_value = retention.snapshot(True)
        with patch("privacy_server.process_path", return_value=path), \
                patch("privacy_server.current_tcp_endpoints", return_value=[]), \
                patch("privacy_server.bounded_rpc", side_effect=lambda call: call()), \
                patch("message_retention_native.source_for", return_value="// synthetic retention"), \
                patch("privacy_server.SnapshotChannel", side_effect=lambda script:
                      failed_channel if script is failed_script else succeeded_channel), \
                patch("privacy_server.time.monotonic", return_value=100) as clock:
            monitor.tick()
            self.assertEqual(monitor.observation.snapshot()["errors"],
                             {"attach_failed": 1, "runtime_snapshot_timeout": 1})
            self.assertEqual(monitor.message_retention._allowed, {123})
            self.assertTrue(monitor.message_retention.snapshot()["runtime"]["applied"])
            self.assertIs(monitor.sessions[123][0], existing)
            existing.detach.assert_not_called()
            existing_script.unload.assert_not_called()
            existing_channel.close.assert_not_called()
            failed.detach.assert_called_once()
            failed_channel.close.assert_called_once()
            clock.return_value = 104
            monitor.tick()
            self.assertEqual(monitor.device.attach.call_count, 1)
            clock.return_value = 106
            monitor.tick()
        self.assertEqual(monitor.device.attach.call_count, 2)
        self.assertIn(456, monitor.sessions)
        self.assertNotIn(456, monitor.failed)
        self.assertTrue(monitor.message_retention.snapshot()["runtime"]["applied"])
        existing.detach.assert_not_called()
        existing_script.unload.assert_not_called()
        self.assertNotIn(PRIVATE, json.dumps(monitor.observation.snapshot()))

    def test_worker_retries_timeout_and_unexpected_error_before_explicit_shutdown(self):
        for fault, expected in [(SnapshotTimeout(PRIVATE), "runtime_snapshot_timeout"),
                                (RuntimeError(PRIVATE), "runtime_worker_failed")]:
            with self.subTest(code=expected):
                monitor, _path, session, script, channel = self.protected_monitor()
                monitor.stopping = threading.Event()
                recovered = []
                def healthy_iteration():
                    # The worker reached another iteration without unloading
                    # the protective session, and invalidated its stale ACK.
                    session.detach.assert_not_called()
                    script.unload.assert_not_called()
                    channel.close.assert_not_called()
                    self.assertFalse(monitor.message_retention.snapshot()["runtime"]["applied"])
                    self.assertFalse(monitor.observation.snapshot()["observation"]["active"])
                    monitor.message_retention.update(123, retention.retention_native(True))
                    recovered.append(monitor.message_retention.snapshot()["runtime"]["applied"])
                    monitor.stopping.set()
                turns = [0]
                def iteration():
                    turns[0] += 1
                    if turns[0] == 1:
                        raise fault
                    healthy_iteration()
                monitor.tick = Mock(side_effect=iteration)
                with patch.object(monitor.stopping, "wait", return_value=False), \
                        patch("privacy_server.bounded_rpc", side_effect=lambda call: call()):
                    monitor.run()
                self.assertEqual(recovered, [True])
                self.assertEqual(monitor.tick.call_count, 2)
                self.assertEqual(monitor.observation.snapshot()["errors"][expected], 1)
                session.detach.assert_called_once()
                channel.close.assert_called_once()
                script.unload.assert_not_called()
                self.assertEqual(monitor.message_retention._allowed, set())
                self.assertNotIn(PRIVATE, json.dumps(monitor.observation.snapshot()))

    def test_non_shutdown_worker_abort_does_not_detach_protective_session(self):
        monitor, _path, session, script, channel = self.protected_monitor()
        monitor.stopping = threading.Event()
        monitor.tick = Mock(side_effect=KeyboardInterrupt(PRIVATE))
        with self.assertRaises(KeyboardInterrupt):
            monitor.run()
        session.detach.assert_not_called()
        script.unload.assert_not_called()
        channel.close.assert_not_called()
        self.assertEqual(monitor.message_retention._allowed, {123})

    def test_observer_health_is_independent_from_live_client_metadata(self):
        worker = Mock()
        worker.ident = None
        self.assertEqual(_observer_state(worker), "starting")
        worker.ident = 123
        worker.is_alive.return_value = True
        self.assertEqual(_observer_state(worker), "running")
        worker.is_alive.return_value = False
        self.assertEqual(_observer_state(worker), "stopped")

    def test_tick_reports_class_and_preserves_filters_then_recovers_ack(self):
        for error, expected in fault_cases():
            with self.subTest(code=expected):
                monitor, path = fixtures.LifecycleTests.fixture_monitor(self)
                monitor.message_retention = retention.fake_service(enabled=True)
                monitor.message_retention.preflight_marker_store()
                monitor.message_retention.register(123)
                monitor.message_retention.update(123, retention.retention_native(True))
                policy = ghosts.desired()
                monitor.ghost_store = Mock()
                monitor.ghost_store.snapshot.return_value = policy
                monitor.ghost_runtime = GhostRuntime(clock=lambda: 100.0)
                monitor.ghost_runtime.update(123, ghosts.native_ghost(policy))
                session, script, stale, replacement = Mock(), Mock(), Mock(), Mock()
                stale.snapshot.side_effect = error
                replacement.snapshot.return_value = retention.snapshot(True, ghost_policy=policy)
                monitor.sessions[123] = session, script, stale
                monitor.roles[123] = "main"
                with patch("privacy_server.process_path", return_value=path), \
                        patch("privacy_server.bounded_rpc", side_effect=lambda call: call()), \
                        patch("privacy_server.SnapshotChannel", return_value=replacement) as factory:
                    monitor.tick()
                    self.assertEqual(monitor.observation.snapshot()["errors"], {"hook_failed": 1, expected: 1})
                    self.assertFalse(monitor.message_retention.snapshot()["runtime"]["applied"])
                    self.assertFalse(monitor.ghost_runtime.status(policy)["applied"])
                    self.assertIs(monitor.sessions[123][0], session)
                    self.assertIs(monitor.sessions[123][1], script)
                    self.assertIs(monitor.sessions[123][2], replacement)
                    stale.close.assert_called_once()
                    factory.assert_called_once_with(script)
                    session.detach.assert_not_called()
                    script.unload.assert_not_called()
                    monitor.device.attach.assert_not_called()
                    monitor.tick()
                self.assertTrue(monitor.message_retention.snapshot()["runtime"]["applied"])
                self.assertTrue(monitor.ghost_runtime.status(policy)["applied"])
                session.detach.assert_not_called()
                script.unload.assert_not_called()
                self.assertNotIn(PRIVATE, json.dumps(monitor.observation.snapshot()))


if __name__ == "__main__":
    unittest.main()
