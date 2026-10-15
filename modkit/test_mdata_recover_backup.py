"""End-to-end recovery using synthetic backup archives and memory credentials."""
from pathlib import Path
import json
import unittest
from unittest.mock import patch

import launch_mod
import mdata_recovery_state as recovery
import mdata_service
from session_credentials import CredentialError
import test_mdata_service as fixture_module

PASSWORD = fixture_module.PASSWORD


class MDataRecoverBackupTests(unittest.TestCase):
    # Reuse only fixture helpers, without inheriting/rerunning the service suite.
    setUp = fixture_module.MDataServiceTests.setUp
    profile = fixture_module.MDataServiceTests.profile
    service = fixture_module.MDataServiceTests.service
    job = fixture_module.MDataServiceTests.job
    archive = fixture_module.MDataServiceTests.archive
    file_values = staticmethod(fixture_module.MDataServiceTests.file_values)
    decode = staticmethod(fixture_module.MDataServiceTests.decode)

    def broken_restore(self):
        path, _, _ = self.archive()
        root, credentials = self.profile("destination", ["7", "8"])
        old_files, old_keys = self.file_values(root), dict(credentials.data)
        credentials.fail_at = 2
        original = credentials.set_owned
        def break_rollback(name, blob, expected):
            if name == "access_token_7@MXMOD" and expected is None:
                raise CredentialError("credential_io")
            return original(name, blob, expected)
        credentials.set_owned = break_rollback
        service = self.service(root, credentials)
        self.job(service, "import", path, error="rollback_failed")
        self.assertFalse(service.restart_requested())
        latch = recovery.read(root)
        self.assertIsNotNone(latch)
        backup = Path(latch["backupFile"])
        self.assertTrue(backup.is_file())
        backup_manifest, backup_files = self.decode(backup)
        self.assertEqual(old_files, backup_files)
        self.assertTrue(backup_manifest["credentials"])
        for blob in old_keys.values():
            self.assertNotIn(blob, backup.read_bytes())
        credentials.set_owned = original
        return root, credentials, old_files, old_keys, latch

    def recover(self, root, credentials, password=PASSWORD, activity=None):
        return mdata_service.recover_backup(root, password, credentials=credentials,
                                           activity=activity or (lambda: (0, True)))

    def test_wrong_password_keeps_latch_staging_and_makes_zero_native_writes(self):
        root, credentials, _, _, latch = self.broken_restore()
        before_files, before_keys = self.file_values(root), dict(credentials.data)
        writes, reads, inventories = len(credentials.writes), len(credentials.reads), credentials.inventories
        stages = {p.name for p in root.parent.glob(".MXMOD-MData-stage-*")}
        with self.assertRaises(mdata_service.MDataError) as raised:
            self.recover(root, credentials, password="Wrong-Synthetic-Password-123")
        self.assertEqual(raised.exception.code, "authentication_failed")
        self.assertEqual((len(credentials.writes), len(credentials.reads), credentials.inventories),
                         (writes, reads, inventories))
        self.assertEqual(credentials.data, before_keys)
        self.assertEqual(self.file_values(root), before_files)
        self.assertEqual(recovery.read(root), latch)
        self.assertEqual({p.name for p in root.parent.glob(".MXMOD-MData-stage-*")}, stages)

    def test_correct_password_restores_full_old_profile_all_keys_and_clears_guard(self):
        root, credentials, old_files, old_keys, latch = self.broken_restore()
        self.recover(root, credentials)
        actual = self.file_values(root)
        # Recovery keeps the current pinned runtime configuration, like restore.
        actual.pop("config.json")
        old_files.pop("config.json")
        self.assertEqual(actual, old_files)
        self.assertEqual(credentials.data, old_keys)
        launch_mod.validate_config((root / "config.json").read_bytes())
        self.assertIsNone(recovery.read(root))
        recovery.ensure_clear(root)
        self.assertTrue(Path(latch["backupFile"]).is_file())
        status = self.service(root, credentials).snapshot()
        self.assertEqual((status["state"], status["result"], status["profiles"]), ("done", "restored", 2))

    def test_correct_password_recovers_when_active_root_is_absent(self):
        root, credentials, old_files, old_keys, _ = self.broken_restore()
        # Simulate a checked sibling rename interrupted before publication.
        absent_root_backup = root.with_name("MXMOD-MData-before-synthetic-absence")
        root.rename(absent_root_backup)
        self.assertFalse(root.exists())
        self.recover(root, credentials)
        self.assertTrue(root.is_dir())
        actual = self.file_values(root)
        actual.pop("config.json")
        old_files.pop("config.json")
        self.assertEqual(actual, old_files)
        self.assertEqual(credentials.data, old_keys)
        recovery.ensure_clear(root)
        self.assertTrue(absent_root_backup.is_dir())

    def test_running_or_unverified_client_refuses_before_snapshot_or_credentials(self):
        root, credentials, _, _, latch = self.broken_restore()
        before_files, before_keys = self.file_values(root), dict(credentials.data)
        for activity in (lambda: (1, True), lambda: (0, False)):
            with self.subTest(activity=activity):
                writes, reads, inventories = len(credentials.writes), len(credentials.reads), credentials.inventories
                with self.assertRaises(mdata_service.MDataError) as raised:
                    self.recover(root, credentials, activity=activity)
                self.assertEqual(raised.exception.code, "client_still_running")
                self.assertEqual((len(credentials.writes), len(credentials.reads), credentials.inventories),
                                 (writes, reads, inventories))
                self.assertEqual(credentials.data, before_keys)
                self.assertEqual(self.file_values(root), before_files)
                self.assertEqual(recovery.read(root), latch)

    def test_client_appearing_after_decode_refuses_without_credentials_writes(self):
        root, credentials, _, _, latch = self.broken_restore()
        calls = [0]
        def activity():
            calls[0] += 1
            return (0 if calls[0] == 1 else 1, True)
        before_files, before_keys = self.file_values(root), dict(credentials.data)
        writes = len(credentials.writes)
        with self.assertRaises(mdata_service.MDataError) as raised:
            self.recover(root, credentials, activity=activity)
        self.assertEqual(raised.exception.code, "client_still_running")
        self.assertEqual(len(credentials.writes), writes)
        self.assertEqual(credentials.data, before_keys)
        self.assertEqual(self.file_values(root), before_files)
        self.assertEqual(recovery.read(root), latch)

    def test_recovery_cleanup_error_preserves_primary_authentication_failure(self):
        from mdata_diagnostics import validate
        root, credentials, _, _, latch = self.broken_restore()
        before = dict(credentials.data)
        with patch.object(mdata_service.files, "remove_staging", side_effect=PermissionError(13, "SYNTHETIC-PRIVATE-CLEANUP")):
            with self.assertRaises(mdata_service.MDataError) as raised:
                self.recover(root, credentials, password="Wrong-Synthetic-Password-123")
        self.assertEqual(raised.exception.code, "authentication_failed")
        self.assertEqual(credentials.data, before)
        self.assertEqual(recovery.read(root), latch)
        raw = (root / "mdata-result.json").read_bytes()
        saved = json.loads(raw)
        self.assertEqual(saved["error"], "authentication_failed")
        self.assertTrue(validate(saved["cleanupDiagnostic"]))
        self.assertEqual(saved["cleanupDiagnostic"]["phase"], "cleanup")
        self.assertEqual((saved["cleanupDiagnostic"]["rootError"], saved["cleanupDiagnostic"]["errno"]),
                         ("PermissionError", 13))
        self.assertNotIn(b"SYNTHETIC-PRIVATE-CLEANUP", raw)

    def test_recovery_persistence_failure_cannot_replace_successful_restore(self):
        from session_transfer import TransferError
        root, credentials, old_files, old_keys, _ = self.broken_restore()
        with patch.object(mdata_service.MDataService, "_persist", side_effect=TransferError("rollback_failed")):
            self.recover(root, credentials)
        self.assertEqual(credentials.data, old_keys)
        actual = self.file_values(root)
        actual.pop("config.json")
        old_files.pop("config.json")
        self.assertEqual(actual, old_files)
        self.assertIsNone(recovery.read(root))


if __name__ == "__main__":
    unittest.main()
