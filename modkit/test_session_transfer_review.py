"""Independent regressions: own temporary files and fake credentials only."""
import base64
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import launch_mod
import session_credentials as credentials_module
import session_transfer as transfer


class FakeCredentials:
    def __init__(self):
        self.data, self.reads, self.writes, self.deletes = {}, [], [], []

    def read(self, name):
        assert credentials_module.allowed_name(name)
        self.reads.append(name)
        return self.data.get(name)

    def create(self, name, blob):
        assert credentials_module.allowed_name(name)
        if name in self.data:
            raise credentials_module.CredentialError("destination_not_empty")
        self.data[name] = blob
        self.writes.append(name)

    def delete_created(self, name, expected_blob):
        self.deletes.append(name)
        if name not in self.data:
            return
        if self.data[name] != expected_blob:
            raise credentials_module.CredentialError("credential_io")
        del self.data[name]


class SessionTransferReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="max-transfer-review-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "MXMOD"
        self.root.mkdir()
        self.credentials = FakeCredentials()
        self.service = transfer.SessionTransfer(self.root, lambda: (0, True),
                                                credentials=self.credentials)
        self.archive = self.base / "synthetic.maxsession"
        self.password = "Independent-Synthetic-Password-123"
        self.files = {"auth_42": b'{"first_name":"Synthetic 42"}',
                      "auth_43": b'{"first_name":"Synthetic 43"}'}
        self.blobs = {"access_token_42@MXMOD": b"SYNTHETIC-42-NOT-REAL",
                      "access_token_43@MXMOD": b"SYNTHETIC-43-NOT-REAL"}

    def seed_export(self):
        (self.root / "auth_42").write_bytes(self.files["auth_42"])
        self.credentials.data["access_token_42@MXMOD"] = self.blobs["access_token_42@MXMOD"]

    def error(self, code, action, *args):
        with self.assertRaises(transfer.TransferError) as raised:
            action(*args)
        self.assertEqual(code, raised.exception.code)
        self.assertEqual(code, str(raised.exception))

    def test_oneme_target_is_refused_before_credential_reads(self):
        self.seed_export()
        outside = self.base / "ONEME"
        outside.mkdir()
        self.error("unsafe_path", self.service.execute, "export",
                   (outside / "synthetic.maxsession").as_uri(), self.password)
        self.assertEqual([], self.credentials.reads)
        self.assertEqual([], list(outside.iterdir()))

    def test_export_fsync_failure_leaves_no_partial_archive_or_temporary(self):
        self.seed_export()
        with patch.object(transfer.os, "fsync", side_effect=OSError("synthetic failure")):
            self.error("io_error", self.service.execute, "export", self.archive.as_uri(), self.password)
        self.assertFalse(self.archive.exists())
        self.assertEqual([self.root], list(self.base.iterdir()))

    def test_export_destination_race_preserves_the_other_file(self):
        self.seed_export()
        def create_destination(_fd):
            self.archive.write_bytes(b"OTHER-SYNTHETIC-FILE")
        with patch.object(transfer.os, "fsync", side_effect=create_destination):
            self.error("file_exists", self.service.execute, "export", self.archive.as_uri(), self.password)
        self.assertEqual(b"OTHER-SYNTHETIC-FILE", self.archive.read_bytes())
        self.assertEqual({self.root, self.archive}, set(self.base.iterdir()))

    def test_restore_regular_replacement_survives_and_credentials_still_rollback(self):
        original_stream = transfer._new_stream
        foreign = self.base / "foreign-synthetic.json"
        foreign.write_bytes(b"FOREIGN-SYNTHETIC-REPLACEMENT")
        @contextmanager
        def replaced_stream(path):
            if path.name == "auth_43":
                self.assertTrue(foreign.is_relative_to(self.base))
                self.assertTrue((self.root / "auth_42").is_relative_to(self.base))
                os.replace(foreign, self.root / "auth_42")
                raise OSError("later synthetic write failure")
            with original_stream(path) as stream:
                yield stream
        with patch.object(transfer, "_new_stream", replaced_stream):
            self.error("rollback_failed", self.service._restore, self.files, self.blobs)
        self.assertEqual(b"FOREIGN-SYNTHETIC-REPLACEMENT", (self.root / "auth_42").read_bytes())
        self.assertEqual({}, self.credentials.data)
        self.assertCountEqual(list(self.blobs), self.credentials.deletes)

    def test_restore_hardlink_replacement_does_not_abort_credential_cleanup(self):
        original_stream = transfer._new_stream
        foreign = self.base / "foreign-synthetic.json"
        foreign.write_bytes(b"FOREIGN-SYNTHETIC-HARDLINK")
        @contextmanager
        def replaced_stream(path):
            if path.name == "auth_43":
                owned = self.root / "auth_42"
                self.assertTrue(owned.is_relative_to(self.base))
                owned.unlink()
                os.link(foreign, owned)
                raise OSError("later synthetic write failure")
            with original_stream(path) as stream:
                yield stream
        with patch.object(transfer, "_new_stream", replaced_stream):
            self.error("rollback_failed", self.service._restore, self.files, self.blobs)
        self.assertEqual(b"FOREIGN-SYNTHETIC-HARDLINK", foreign.read_bytes())
        self.assertEqual({}, self.credentials.data)
        self.assertCountEqual(list(self.blobs), self.credentials.deletes)

    def test_restore_replaced_credential_is_not_deleted(self):
        original_stream = transfer._new_stream
        @contextmanager
        def failed_stream(path):
            if path.name == "auth_42":
                self.credentials.data["access_token_42@MXMOD"] = b"FOREIGN-SYNTHETIC-CREDENTIAL"
                raise OSError("synthetic file failure")
            with original_stream(path) as stream:
                yield stream
        with patch.object(transfer, "_new_stream", failed_stream):
            self.error("rollback_failed", self.service._restore, self.files, self.blobs)
        self.assertEqual({"access_token_42@MXMOD": b"FOREIGN-SYNTHETIC-CREDENTIAL"},
                         self.credentials.data)
        self.assertEqual([], list(self.root.iterdir()))

    def test_creation_failure_before_stream_yield_has_no_orphan_auth_file(self):
        original_validator = transfer._validate_created
        def failed_validation(api, handle, path, stream):
            if path.name == "auth_42":
                raise OSError("synthetic post-create validation failure")
            return original_validator(api, handle, path, stream)
        with patch.object(transfer, "_validate_created", failed_validation):
            with self.assertRaises(Exception):
                self.service._restore(self.files, self.blobs)
        self.assertEqual({}, self.credentials.data)
        self.assertFalse((self.root / "auth_42").exists())
        self.assertEqual([], list(self.root.iterdir()))

    def test_restore_fsync_failure_deletes_owned_file_and_reports_original_error(self):
        with patch.object(transfer.os, "fsync", side_effect=OSError("synthetic fsync failure")):
            with self.assertRaises(OSError):
                self.service._restore(self.files, self.blobs)
        self.assertEqual({}, self.credentials.data)
        self.assertEqual([], list(self.root.iterdir()))

    def test_fdopen_failure_closes_and_deletes_the_new_object(self):
        path = self.root / "synthetic-new-file"
        captured_fds = []
        def failed_fdopen(fd, *args, **kwargs):
            captured_fds.append(fd)
            raise OSError("synthetic fdopen failure")
        try:
            with patch.object(transfer.os, "fdopen", failed_fdopen):
                with self.assertRaises(OSError):
                    with transfer._new_stream(path):
                        self.fail("The failed stream must never be yielded")
            self.assertFalse(path.exists())
            for fd in captured_fds:
                with self.assertRaises(OSError):
                    os.fstat(fd)
        finally:
            # Keep the regression safe even when run against the unfixed code:
            # only our captured descriptor and own synthetic path are cleaned.
            for fd in captured_fds:
                try:
                    os.close(fd)
                except OSError:
                    pass
            self.assertTrue(path.is_relative_to(self.base))
            path.unlink(missing_ok=True)

    def test_exclusive_new_stream_never_touches_an_existing_file(self):
        path = self.root / "synthetic-existing-file"
        path.write_bytes(b"FOREIGN-SYNTHETIC-FILE")
        with patch.object(transfer.os, "fdopen") as fdopen:
            with self.assertRaises(FileExistsError):
                with transfer._new_stream(path):
                    self.fail("An existing file must not be yielded")
            fdopen.assert_not_called()
        self.assertEqual(b"FOREIGN-SYNTHETIC-FILE", path.read_bytes())

    def test_persisted_status_cannot_overwrite_a_live_import_state(self):
        status = self.root / "session-transfer-result.json"
        status.write_text(json.dumps({"schema": 1, "state": "done", "error": None}), encoding="utf-8")
        entered, release = threading.Event(), threading.Event()
        original_stream = launch_mod._config_stream
        @contextmanager
        def delayed_stream(path, *, create):
            with original_stream(path, create=create) as stream:
                if path.name == status.name and not create:
                    entered.set()
                    if not release.wait(timeout=5):
                        raise AssertionError("synthetic status read timeout")
                yield stream
        results = []
        def snapshot():
            results.append(self.service.snapshot())
        with patch.object(launch_mod, "_config_stream", delayed_stream):
            worker = threading.Thread(target=snapshot, daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(timeout=3))
                self.service._set("waiting_for_exit")
            finally:
                release.set()
                worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual("waiting_for_exit", self.service.snapshot()["state"])
        self.assertTrue(self.service.pending())
        self.assertEqual("waiting_for_exit", results[0]["state"])

    @unittest.skipUnless(os.name == "nt", "Windows launcher/import mutex")
    def test_transfer_lock_is_busy_in_another_thread(self):
        outcomes = []
        def contender():
            try:
                with transfer.transfer_lock():
                    outcomes.append("acquired")
            except transfer.TransferError as error:
                outcomes.append(error.code)
        with transfer.transfer_lock():
            worker = threading.Thread(target=contender, daemon=True)
            worker.start()
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(["busy"], outcomes)

    def test_native_credential_rollback_does_not_delete_changed_blob(self):
        adapter = credentials_module.WindowsCredentials()
        with patch.object(adapter, "read", return_value=b"FOREIGN-SYNTHETIC"), \
                patch.object(credentials_module, "_api") as api:
            with self.assertRaises(credentials_module.CredentialError):
                adapter.delete_created("access_token_42@MXMOD", b"OURS-SYNTHETIC")
            api.assert_not_called()

    def test_native_credential_create_never_overwrites_an_existing_blob(self):
        adapter = credentials_module.WindowsCredentials()
        with patch.object(adapter, "read", return_value=b"EXISTING-SYNTHETIC"), \
                patch.object(credentials_module, "_api") as api:
            with self.assertRaises(credentials_module.CredentialError) as error:
                adapter.create("access_token_42@MXMOD", b"OURS-SYNTHETIC")
            self.assertEqual("destination_not_empty", error.exception.code)
            api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
