"""Installed lifecycle regressions using synthetic identities and no MAX I/O."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import launch_mod as launcher
import startup_status


class InstalledStartupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.manifest = self.root / 'mod-manifest.json'
        self.manifest.write_bytes(b'{"synthetic":true}')
        self.executable = self.root / 'max.exe'
        self.runtime = launcher.runtime_identity(self.manifest)
        startup_status.reset()
        self.addCleanup(startup_status.reset)

    def identity(self, state='running', runtime=None, observer=None):
        return dict(schema=1, kind='max-mod-privacy-server', runtimeId=runtime or self.runtime,
                    serverPid=123, clientState=state, processes=0 if state == 'idle' else 1,
                    observerState=observer or ('starting' if state == 'starting' else 'running'))

    def test_closed_other_copy_releases_port_before_exactly_one_spawn(self):
        old = self.identity('idle', runtime='0'*64)
        values = [old, old, None, self.identity('starting'), self.identity()]
        process = SimpleNamespace(poll=lambda: None)
        with patch.object(launcher, '_diagnostics_identity', side_effect=values), \
                patch.object(launcher, '_quiet_process', return_value=process) as spawn, \
                patch.object(launcher.time, 'sleep'):
            self.assertEqual(launcher.launch_diagnostics(self.manifest, self.executable), 'started')
        spawn.assert_called_once()

    def test_live_other_copy_never_gets_killed_or_bypassed(self):
        with patch.object(launcher, '_diagnostics_identity', return_value=self.identity(runtime='0'*64)), \
                patch.object(launcher, '_quiet_process') as spawn:
            with self.assertRaises(startup_status.StartupError) as error:
                launcher.launch_diagnostics(self.manifest, self.executable)
        self.assertEqual(error.exception.code, 'port_other_runtime')
        spawn.assert_not_called()

    def test_idle_http_shutdown_race_still_requires_free_port(self):
        values = [self.identity('idle', runtime='0'*64),
                  startup_status.StartupError('port_unavailable'), None, self.identity()]
        with patch.object(launcher, '_diagnostics_identity', side_effect=values), \
                patch.object(launcher.time, 'sleep'), \
                patch.object(launcher, '_quiet_process', return_value=SimpleNamespace(poll=lambda: None)) as spawn:
            self.assertEqual(launcher.launch_diagnostics(self.manifest, self.executable), 'started')
        spawn.assert_called_once()

    def test_dead_observer_is_not_a_successful_launch(self):
        with patch.object(launcher, '_diagnostics_identity', return_value=self.identity(observer='stopped')), \
                patch.object(launcher, '_quiet_process') as spawn:
            with self.assertRaises(startup_status.StartupError) as error:
                launcher.launch_diagnostics(self.manifest, self.executable)
        self.assertEqual(error.exception.code, 'observer_worker_stopped')
        spawn.assert_not_called()

    def test_client_seen_before_worker_started_waits_for_worker(self):
        with patch.object(launcher, '_diagnostics_identity', side_effect=[
                self.identity(observer='starting'), self.identity()]) as query, \
                patch.object(launcher, '_quiet_process') as spawn:
            self.assertEqual(launcher.launch_diagnostics(self.manifest, self.executable), 'already running')
        self.assertEqual(query.call_count, 2)
        spawn.assert_not_called()

    def test_cold_start_over_fifteen_seconds_can_finish(self):
        clock = [0.0]
        def sleep(seconds):
            clock[0] += 8.0
        def identity(**_kwargs):
            return None if clock[0] == 0 else self.identity('starting' if clock[0] < 24 else 'running')
        with patch.object(launcher, '_diagnostics_identity', side_effect=identity), \
                patch.object(launcher, '_quiet_process', return_value=SimpleNamespace(poll=lambda: None)) as spawn, \
                patch.object(launcher.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(launcher.time, 'sleep', side_effect=sleep):
            self.assertEqual(launcher.launch_diagnostics(self.manifest, self.executable), 'started')
        self.assertEqual(clock[0], 24)
        spawn.assert_called_once()

    def test_inherited_finite_observer_failure_survives_exit(self):
        report = SimpleNamespace(try_read=lambda: dict(state='error', code='notification_ack_failed'),
                                 try_write=Mock(), stage='observer')
        with patch.object(launcher.startup_status, 'current', return_value=report), \
                patch.object(launcher, '_diagnostics_identity', return_value=None), \
                patch.object(launcher, '_quiet_process', return_value=SimpleNamespace(poll=lambda: 1)) as spawn:
            with self.assertRaises(startup_status.StartupError) as error:
                launcher.launch_diagnostics(self.manifest, self.executable)
        self.assertEqual(error.exception.code, 'notification_ack_failed')
        spawn.assert_called_once()

    def test_only_actual_recovery_guard_classifies_recovery(self):
        from mdata_recovery_state import RecoveryRequired
        with patch('mdata_recovery_state.ensure_clear', side_effect=RecoveryRequired()):
            with self.assertRaises(startup_status.StartupError) as error:
                launcher._ensure_no_recovery(self.root)
        self.assertEqual(error.exception.code, 'recovery_required')


if __name__ == '__main__':
    unittest.main()
