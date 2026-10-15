"""Non-secret recovery latch tests using temporary files and mocked startup."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from contextlib import contextmanager
from unittest.mock import Mock, patch

import launch_mod
import mdata_recovery_state as state
import startup_status


class RecoveryStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "MXMOD"
        self.root.mkdir()
        self.backup = self.base / "MXMOD-MData-Backups" / "synthetic.mdata"
        self.backup.parent.mkdir()
        self.backup.write_bytes(b"SYNTHETIC-ENCRYPTED-BACKUP")

    def test_absent_guard_is_read_only(self):
        self.assertIsNone(state.read(self.root))
        self.assertFalse((self.base / "MXMOD-MData-Recovery.json").exists())

    def test_durable_guard_survives_new_reader_and_blocks_start(self):
        value = state.write(self.root, self.backup)
        self.assertEqual(state.read(self.root), value)
        with self.assertRaises(state.RecoveryRequired):
            state.ensure_clear(self.root)
        state.clear(self.root, value)
        self.assertIsNone(state.read(self.root))

    def test_cannot_clear_another_operations_guard(self):
        value = state.write(self.root, self.backup)
        with self.assertRaises(state.RecoveryRequired):
            state.clear(self.root, {**value, "operationId": "0" * 32})
        self.assertEqual(state.read(self.root), value)

    def test_cannot_overwrite_pending_recovery(self):
        first = state.write(self.root, self.backup)
        with self.assertRaises(state.RecoveryRequired):
            state.write(self.root, self.backup)
        self.assertEqual(state.read(self.root), first)

    def test_foreign_backup_path_and_invalid_json_fail_closed(self):
        value = state.write(self.root, self.backup)
        target = self.base / "MXMOD-MData-Recovery.json"
        target.write_text(json.dumps({**value, "backupFile": str(self.base / "foreign.mdata")}), encoding="utf8")
        with self.assertRaises(state.RecoveryRequired):
            state.read(self.root)
        target.write_bytes(b'{"schema":1,"schema":1}')
        with self.assertRaises(state.RecoveryRequired):
            state.read(self.root)

    def test_launcher_refuses_before_any_process_start(self):
        state.write(self.root, self.backup)
        with patch.object(launch_mod, "verified_executable", return_value=Path("D:/synthetic/max.exe")), \
                patch.object(launch_mod.desktop_process, "is_desktop_child", return_value=True), \
                patch.object(launch_mod, "_prepare_helper_temp"), \
                patch.object(startup_status, "begin"), \
                patch.object(launch_mod, "prepare_config", return_value=(self.root / "config.json", "synthetic")), \
                patch.object(launch_mod, "_quiet_process") as start, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(launch_mod.main([]), 1)
            start.assert_not_called()
        self.assertIn("recovery_required", error.getvalue())
        self.assertIn("Восстановление MData", startup_status.message_for(startup_status.failure_code()))
        self.assertNotIn(str(self.backup), error.getvalue())

    def test_guard_is_checked_after_startup_mutex_is_acquired(self):
        @contextmanager
        def lock_with_new_crash_marker():
            state.write(self.root, self.backup)
            yield
        args = SimpleNamespace(without_diagnostics=True, manifest=Path("D:/synthetic/manifest.json"))
        with patch("session_transfer.transfer_lock", lock_with_new_crash_marker), \
                patch.object(launch_mod, "_quiet_process") as start:
            with self.assertRaises(startup_status.StartupError) as error:
                launch_mod._start_verified(args, Path("D:/synthetic/max.exe"), self.root)
            self.assertEqual(error.exception.code, "recovery_required")
            start.assert_not_called()

    def test_pending_recovery_prevents_creation_of_an_absent_profile(self):
        state.write(self.root, self.backup)
        self.root.rmdir()
        with self.assertRaises(startup_status.StartupError) as error:
            launch_mod.prepare_config(self.base)
        self.assertEqual(error.exception.code, "recovery_required")
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    unittest.main()
