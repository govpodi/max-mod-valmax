"""Independent confirmed-import lifecycle review using synthetic accounts."""
from __future__ import annotations

import hashlib
import threading
import unittest
from unittest.mock import patch

import mdata_service
import test_mdata_manager as fixtures
from test_mdata_service import PASSWORD


class ConfirmedImportReviewTests(unittest.TestCase):
    setUp = fixtures.MDataManagerTests.setUp
    profile = fixtures.MDataManagerTests.profile
    service = fixtures.MDataManagerTests.service
    archive = fixtures.MDataManagerTests.archive
    error = fixtures.MDataManagerTests.error

    def prepare(self):
        path = self.archive()
        root, memory = self.profile("destination", ["7"])
        service = self.service(root, memory)
        return path, root, memory, service, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_thread_constructor_failure_releases_job_and_drops_fingerprint(self):
        path, _, memory, service, fingerprint = self.prepare()
        with patch("mdata_service.threading.Thread", side_effect=RuntimeError(PASSWORD)):
            self.error("io_error", service.execute_confirmed_import, path.as_uri(), PASSWORD, fingerprint)
        self.assertFalse(service.pending())
        self.assertIsNone(service._confirmed_fingerprint)
        self.assertIsNone(service._worker)
        self.assertEqual(service.snapshot()["state"], "error")
        self.assertEqual((memory.reads, memory.writes, memory.inventories), ([], [], 0))

    def test_start_failure_cleans_state_and_password_args_before_unlock(self):
        path, _, memory, service, fingerprint = self.prepare()
        released = []
        original = service._job_lock

        class ObservedLock:
            def acquire(self, blocking): return original.acquire(blocking)
            def locked(self): return original.locked()
            def release(self):
                # A competing caller can enter immediately after release.
                released.append((service.snapshot()["state"], service._confirmed_fingerprint, service._worker))
                original.release()

        service._job_lock = ObservedLock()
        with patch.object(threading.Thread, "start", side_effect=RuntimeError(PASSWORD)):
            self.error("io_error", service.execute_confirmed_import, path.as_uri(), PASSWORD, fingerprint)
        self.assertEqual(released, [("error", None, None)])
        self.assertFalse(service.pending())
        self.assertEqual((memory.reads, memory.writes, memory.inventories), ([], [], 0))

    def test_busy_request_cannot_replace_running_job_fingerprint(self):
        path, _, _, service, fingerprint = self.prepare()
        active = [True]
        service.activity = lambda: (int(active[0]), True)
        result = service.execute_confirmed_import(path.as_uri(), PASSWORD, fingerprint)
        self.assertEqual(result["state"], "waiting_for_exit")
        try:
            self.error("busy", service.execute_confirmed_import, path.as_uri(), PASSWORD, "0" * 64)
            self.assertEqual(service._confirmed_fingerprint, fingerprint)
        finally:
            active[0] = False
            service._worker.join(5)
        self.assertFalse(service._worker.is_alive())
        self.assertIsNone(service._confirmed_fingerprint)
        self.assertFalse(service.pending())
        self.assertEqual(service.snapshot()["state"], "done")

    def test_replaced_authenticated_archive_fails_before_destination_credentials(self):
        path, root, memory, service, fingerprint = self.prepare()
        other = self.archive(("11",))
        path.write_bytes(other.read_bytes())
        before = dict(memory.data)
        active = [True]
        service.activity = lambda: (int(active[0]), True)
        result = service.execute_confirmed_import(path.as_uri(), PASSWORD, fingerprint)
        self.assertEqual(result["state"], "waiting_for_exit")
        active[0] = False; service._worker.join(5)
        self.assertEqual(service.snapshot()["error"], "source_changed")
        self.assertEqual(memory.data, before)
        self.assertEqual((memory.reads, memory.writes, memory.inventories), ([], [], 0))
        self.assertFalse((root / "auth_42").exists())
        self.assertFalse((root / "auth_11").exists())
        self.assertFalse(any(root.parent.glob(".MXMOD-MData-stage-*")))
        self.assertIsNone(service._confirmed_fingerprint)

    def test_legacy_scope_replacement_cannot_enter_full_profile_import(self):
        path, _, memory, service, fingerprint = self.prepare()
        other = self.archive(kind="profile")
        path.write_bytes(other.read_bytes())
        active = [True]; service.activity = lambda: (int(active[0]), True)
        with patch.object(service, "_capture", side_effect=AssertionError("Full profile capture entered")), \
                patch.object(service, "_backup_current", side_effect=AssertionError("Full backup entered")):
            service.execute_confirmed_import(path.as_uri(), PASSWORD, fingerprint)
            active[0] = False; service._worker.join(5)
        self.assertEqual(service.snapshot()["error"], "source_changed")
        self.assertEqual((memory.reads, memory.writes, memory.inventories), ([], [], 0))
        self.assertIsNone(service._confirmed_fingerprint)

    def test_completed_guard_does_not_contaminate_following_explicit_legacy_import(self):
        path, _, _, service, fingerprint = self.prepare()
        active = [True]; service.activity = lambda: (int(active[0]), True)
        service.execute_confirmed_import(path.as_uri(), PASSWORD, fingerprint)
        active[0] = False; service._worker.join(5)
        self.assertEqual(service.snapshot()["result"], "added")
        self.assertIsNone(service._confirmed_fingerprint)
        legacy = self.archive(kind="profile")
        active[0] = True
        service.execute("import", legacy.as_uri(), PASSWORD, "add")
        self.assertIsNone(service._confirmed_fingerprint)
        active[0] = False; service._worker.join(5)
        self.assertEqual(service.snapshot()["result"], "already_present")
        self.assertIsNone(service._confirmed_fingerprint)
        self.assertFalse(service.pending())

    def test_invalid_guard_is_rejected_without_job_or_auth_access(self):
        path, _, memory, service, _ = self.prepare()
        for value in (None, "", "A" * 64, "a" * 63, "a" * 65, 7, ["a" * 64]):
            self.error("invalid_request", service.execute_confirmed_import, path.as_uri(), PASSWORD, value)
        self.assertFalse(service.pending())
        self.assertEqual((memory.reads, memory.writes, memory.inventories), ([], [], 0))


if __name__ == "__main__": unittest.main()
