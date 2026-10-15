"""Synthetic session transfer tests. No real MAX profile or WinCred access."""
import base64
from contextlib import contextmanager
import ctypes
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

import session_credentials as creds
from session_archive import decrypt
from session_transfer import SessionTransfer, TransferError, file_url, policy, _payload_parts


class MemoryCredentials:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.reads, self.writes = [], []
        self.fail_at = None

    def read(self, name):
        self.reads.append(name)
        assert creds.allowed_name(name)
        return self.data.get(name)

    def create(self, name, blob):
        if self.fail_at == len(self.writes):
            raise creds.CredentialError("credential_io")
        if name in self.data:
            raise creds.CredentialError("destination_not_empty")
        self.data[name] = blob
        self.writes.append(name)

    def delete_created(self, name, expected_blob):
        if self.data[name] != expected_blob:
            raise creds.CredentialError("credential_io")
        del self.data[name]


class SessionTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "MXMOD"
        self.root.mkdir()
        self.creds = MemoryCredentials()
        self.service = SessionTransfer(self.root, lambda: (0, True), credentials=self.creds)
        self.url = (self.base / "synthetic.maxsession").as_uri()
        self.password = "synthetic-password-only"

    def seed(self):
        (self.root / "auth_42").write_bytes(b'{"first_name":"Synthetic"}')
        self.creds.data["access_token_42@MXMOD"] = b"SYNTHETIC-TOKEN"

    def test_status_and_constructor_do_not_open_credentials(self):
        self.assertEqual(self.service.snapshot()["state"], "idle")
        self.assertEqual(self.creds.reads, [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_only_explicit_export_reads_exact_own_names_and_encrypts(self):
        self.seed()
        result = self.service.execute("export", self.url, self.password)
        self.assertEqual(result["state"], "done")
        archive = file_url(self.url).read_bytes()
        self.assertNotIn(b"SYNTHETIC-TOKEN", archive)
        self.assertNotIn(b"Synthetic", archive)
        self.assertTrue(all(creds.allowed_name(name) for name in self.creds.reads))
        value = decrypt(archive, self.password, policy=policy())
        files, credentials = _payload_parts(value)
        self.assertEqual(credentials, {"access_token_42@MXMOD": b"SYNTHETIC-TOKEN"})
        self.assertIn("auth_42", files)

    def test_existing_archive_never_overwritten(self):
        self.seed()
        path = file_url(self.url)
        path.write_bytes(b"OLD-SYNTHETIC")
        with self.assertRaises(TransferError) as failure:
            self.service.execute("export", self.url, self.password)
        self.assertEqual(failure.exception.code, "file_exists")
        self.assertEqual(path.read_bytes(), b"OLD-SYNTHETIC")

    def test_changed_snapshot_never_publishes_archive(self):
        self.seed()
        original_read = self.creds.read
        calls = [0]
        def changed(name):
            calls[0] += 1
            value = original_read(name)
            return value if calls[0] == 1 else b"REFRESHED-SYNTHETIC-TOKEN"
        self.creds.read = changed
        with self.assertRaises(TransferError) as failure:
            self.service.execute("export", self.url, self.password)
        self.assertEqual(failure.exception.code, "invalid_profile")
        self.assertFalse(file_url(self.url).exists())

    def test_empty_export_does_not_create_archive(self):
        with self.assertRaises(TransferError) as failure:
            self.service.execute("export", self.url, self.password)
        self.assertEqual(failure.exception.code, "no_accounts")
        self.assertFalse(file_url(self.url).exists())

    def test_bad_password_import_never_mutates(self):
        self.seed()
        self.service.execute("export", self.url, self.password)
        (self.root / "auth_42").unlink()
        self.creds.data.clear()
        with self.assertRaises(TransferError) as failure:
            self.service.execute("import", self.url, "wrong-password-long")
        self.assertEqual(failure.exception.code, "authentication_failed")
        self.assertEqual(self.creds.writes, [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_import_refuses_existing_profile_and_does_not_overwrite(self):
        self.seed()
        self.service.execute("export", self.url, self.password)
        before = dict(self.creds.data)
        with self.assertRaises(TransferError) as failure:
            self.service.execute("import", self.url, self.password)
        self.assertEqual(failure.exception.code, "destination_not_empty")
        self.assertEqual(self.creds.data, before)
        self.assertEqual(self.creds.writes, [])

    def test_background_import_waits_for_closed_mod_then_restores(self):
        self.seed()
        self.service.execute("export", self.url, self.password)
        original = (self.root / "auth_42").read_bytes()
        (self.root / "auth_42").unlink()
        self.creds.data.clear()
        active = [1]
        self.service.activity = lambda: (active[0], True)
        result = self.service.execute("import", self.url, self.password)
        self.assertEqual(result["state"], "waiting_for_exit")
        self.assertEqual(self.creds.writes, [])
        self.assertFalse((self.root / "auth_42").exists())
        active[0] = 0
        self.service._worker.join(timeout=5)
        self.assertFalse(self.service._worker.is_alive())
        self.assertEqual(self.service.snapshot()["state"], "done")
        self.assertEqual((self.root / "auth_42").read_bytes(), original)
        self.assertEqual(self.creds.data["access_token_42@MXMOD"], b"SYNTHETIC-TOKEN")

    def test_restore_checks_running_process_before_first_write(self):
        self.service.activity = lambda: (1, True)
        with self.assertRaises(TransferError) as failure:
            self.service._restore({"auth_42": b"{}"}, {"access_token_42@MXMOD": b"fake"})
        self.assertEqual(failure.exception.code, "client_still_running")
        self.assertEqual(self.creds.writes, [])

    def test_restore_preserves_fresh_profile_common_keys(self):
        self.creds.data["app_data_storage_key_Common@MXMOD"] = b"FRESH-LOCAL-KEY"
        self.service._restore({"auth_42": b"{}"}, {"access_token_42@MXMOD": b"fake"})
        self.assertEqual(self.creds.data["app_data_storage_key_Common@MXMOD"], b"FRESH-LOCAL-KEY")
        self.assertNotIn("app_data_storage_key_Common@MXMOD", self.creds.reads)

    def test_credential_failure_rolls_back_only_created_entries(self):
        self.creds.fail_at = 1
        with self.assertRaises(creds.CredentialError):
            self.service._restore({"auth_42": b"{}", "auth_43": b"{}"}, {
                "access_token_42@MXMOD": b"fake", "access_token_43@MXMOD": b"fake-call"})
        self.assertEqual(self.creds.data, {})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_token_or_extra_account_is_rejected(self):
        payload = {"files": [{"path": "auth_42", "data": "e30="}], "credentials": []}
        with self.assertRaises(TransferError):
            _payload_parts(payload)
        payload["credentials"] = [{"name": "access_token_43@MXMOD", "blob": "eA=="}]
        with self.assertRaises(TransferError):
            _payload_parts(payload)

    def test_invalid_paths_never_reach_credentials(self):
        self.seed()
        for value in ("http://invalid/x.maxsession", "file://server/x.maxsession",
                      "file:///C:/x.txt", "file:///C:/x.maxsession?secret=x",
                      "file:///C:/x.maxsession#x", "file:///C:/../x.maxsession",
                      "file:///C:/x.maxsession:stream", "file:///C:/ONEME/x.maxsession"):
            with self.subTest(value=value), self.assertRaises(TransferError) as failure:
                self.service.execute("export", value, self.password)
            self.assertEqual(failure.exception.code, "unsafe_path")
        self.assertEqual(self.creds.reads, [])


class ExactCredentialTests(unittest.TestCase):
    def test_unrelated_namespace_and_invalid_ids_rejected_before_windows(self):
        adapter = creds.WindowsCredentials()
        with patch.object(creds, "_api") as api:
            for name in ("access_token_42@ONEME", "MXMOD_access_token@42", "access_token_0@MXMOD",
                         "access_token_01@MXMOD", "unrelated_Common@MXMOD",
                         "access_token_9223372036854775808@MXMOD", "access_token_42@MXMOD-extra"):
                with self.subTest(name=name), self.assertRaises(creds.CredentialError):
                    adapter.read(name)
            api.assert_not_called()

    def test_native_blob_copy_freed_and_api_copy_wiped(self):
        buffer = (ctypes.c_ubyte * 4)(1, 2, 3, 4)
        entry = creds.CREDENTIALW()
        entry.Type, entry.TargetName = 1, "access_token_42@MXMOD"
        entry.CredentialBlobSize, entry.CredentialBlob = 4, buffer
        pointer = ctypes.pointer(entry)
        api = Mock()
        def read(name, kind, flags, output):
            ctypes.cast(output, ctypes.POINTER(ctypes.POINTER(creds.CREDENTIALW)))[0] = pointer
            return 1
        api.CredReadW.side_effect = read
        with patch.object(creds, "_api", return_value=api):
            self.assertEqual(creds.WindowsCredentials().read(entry.TargetName), b"\x01\x02\x03\x04")
        api.CredFree.assert_called_once()
        self.assertEqual(bytes(buffer), b"\x00" * 4)

    def test_create_uses_generic_local_persistence_and_clears_temporary_copy(self):
        adapter, api = creds.WindowsCredentials(), Mock()
        captured = {}
        def write(pointer, flags):
            entry = ctypes.cast(pointer, ctypes.POINTER(creds.CREDENTIALW)).contents
            captured.update(kind=entry.Type, persist=entry.Persist, name=entry.TargetName,
                            buffer=entry.CredentialBlob, size=entry.CredentialBlobSize)
            self.assertEqual(ctypes.string_at(entry.CredentialBlob, entry.CredentialBlobSize), b"fake")
            return 1
        api.CredWriteW.side_effect = write
        with patch.object(creds, "_api", return_value=api), patch.object(adapter, "read", return_value=None):
            adapter.create("access_token_42@MXMOD", b"fake")
        self.assertEqual((captured["kind"], captured["persist"]), (1, 2))
        # The native buffer is out of scope now; no dereference after lifetime.


if __name__ == "__main__":
    unittest.main()
