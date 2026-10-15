"""Real encrypted login containers with synthetic profiles and memory tokens."""
import base64
import io
from pathlib import Path
import unittest
from unittest.mock import patch

import mdata_archive
import mdata_files
import mdata_service
import test_mdata_service as fixtures


class LoginServiceTests(unittest.TestCase):
    setUp = fixtures.MDataServiceTests.setUp
    profile = fixtures.MDataServiceTests.profile
    service = fixtures.MDataServiceTests.service
    job = fixtures.MDataServiceTests.job
    decode = staticmethod(fixtures.MDataServiceTests.decode)
    file_values = staticmethod(fixtures.MDataServiceTests.file_values)

    def login_archive(self, ids=("42",)):
        root, memory = self.profile("incoming", ids)
        path = self.base / "login.mdata"
        with patch("mdata_files.listing", side_effect=AssertionError("Full profile listed")), \
                patch.object(memory, "owned_names", side_effect=AssertionError("Credential inventory used for export")):
            self.job(self.service(root, memory), "export", path)
        return path, root, memory

    def test_confirmed_import_rechecks_the_ciphertext_before_destination_access(self):
        import hashlib
        path, _, _ = self.login_archive()
        fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
        root, current = self.profile("confirmed-destination", ["7"])
        service = self.service(root, current)
        service._confirmed_fingerprint = "0" * 64
        before = self.file_values(root), dict(current.data)
        with self.assertRaises(mdata_service.MDataError) as raised:
            service._import(path, fixtures.PASSWORD, "add")
        self.assertEqual(raised.exception.code, "source_changed")
        self.assertEqual((self.file_values(root), current.data), before)
        self.assertEqual((current.reads, current.writes, current.inventories), ([], [], 0))
        service._confirmed_fingerprint = fingerprint
        result, count = service._import(path, fixtures.PASSWORD, "add")
        self.assertEqual((result, count), ("added", 1))

    def test_public_export_ignores_history_media_and_cache_contents(self):
        root, memory = self.profile("large-source", ["42"])
        (root / "cache" / "ignored.bin").write_bytes(b"SYNTHETIC-CACHE" * 600000)
        path = self.base / "small.mdata"
        service = self.service(root, memory)
        with patch("mdata_files.listing", side_effect=AssertionError("Full profile listed")), \
                patch.object(memory, "owned_names", side_effect=AssertionError("Credential inventory used")), \
                patch.object(service, "_capture", side_effect=AssertionError("Full capture used")):
            self.job(service, "export", path)
        self.assertLess(path.stat().st_size, 16384)
        self.assertEqual(path.read_bytes()[:8], mdata_archive.LOGIN_MAGIC)
        manifest, values = self.decode(path)
        self.assertEqual(set(values), {"auth_42"})
        self.assertEqual({item["name"] for item in manifest["credentials"]}, {"access_token_42@MXMOD"})
        self.assertTrue(all(name == "access_token_42@MXMOD" for name in memory.reads))

    def test_login_import_adds_to_existing_account_without_full_backup_or_capture(self):
        path, source, incoming = self.login_archive()
        root, current = self.profile("destination", ["7"])
        before_files, before_credentials = self.file_values(root), dict(current.data)
        current.reads.clear()
        service = self.service(root, current)
        with patch("mdata_files.listing", side_effect=AssertionError("Full profile listed")), \
                patch.object(service, "_capture", side_effect=AssertionError("Full capture used")), \
                patch.object(service, "_backup_current", side_effect=AssertionError("Full backup used")):
            # Old callers saying restore must still only add a v2 login archive.
            result = self.job(service, "import", path, mode="restore")
        self.assertEqual((result["result"], result["profiles"], result["backupAvailable"]), ("added", 1, False))
        actual = self.file_values(root)
        for name, value in before_files.items():
            self.assertEqual(actual[name], value)
        for name, value in before_credentials.items():
            self.assertEqual(current.data[name], value)
        self.assertEqual(actual["auth_42"], (source / "auth_42").read_bytes())
        self.assertEqual(current.data["access_token_42@MXMOD"], incoming.data["access_token_42@MXMOD"])
        self.assertFalse((root / "user42").exists())
        self.assertFalse((root.parent / "MXMOD-MData-Backups").exists())
        self.assertTrue(all(name.startswith("access_token_") for name in current.reads))

    def test_duplicate_login_import_does_not_refresh_token_or_touch_files(self):
        path, _, _ = self.login_archive()
        root, current = self.profile("same-account", ["42"])
        before = self.file_values(root), dict(current.data)
        result = self.job(self.service(root, current), "import", path, mode="add")
        self.assertEqual((result["result"], result["profiles"]), ("already_present", 0))
        self.assertEqual((self.file_values(root), current.data), before)
        self.assertFalse(current.writes)

    def test_wrong_password_and_terminal_tamper_never_read_destination_credentials(self):
        path, _, _ = self.login_archive()
        root, current = self.profile("invalid-input", ["7"])
        service = self.service(root, current)
        self.job(service, "import", path, password="Wrong-Synthetic-Password-123", error="authentication_failed")
        damaged = bytearray(path.read_bytes())
        damaged[-1] ^= 1
        other = self.base / "damaged.mdata"
        other.write_bytes(damaged)
        self.job(service, "import", other, error="authentication_failed")
        self.assertEqual((current.reads, current.writes, current.inventories), ([], [], 0))

    def test_profile_payload_in_login_container_is_rejected_before_any_plaintext_sink(self):
        values = {"auth_42": b"synthetic", "cache/forbidden.bin": b"not login state"}
        token = b"synthetic-token"
        manifest = {"schema": 1, "sourceClient": "MAX Desktop", "sourceVersion": "26.35.1",
                    "files": [{"path": name, "size": len(value)} for name, value in values.items()],
                    "credentials": [{"name": "access_token_42@MXMOD", "blob": base64.b64encode(token).decode("ascii")}]}
        path = self.base / "wrong-scope.mdata"
        with path.open("wb") as output:
            mdata_archive.encrypt_stream(manifest, fixtures.PASSWORD, lambda name: io.BytesIO(values[name]),
                                         output, policy=mdata_service._policy(), archive_kind="login")
        root, current = self.profile("scope-destination", ["7"])
        with patch("mdata_files.sink", side_effect=AssertionError("Plaintext sink opened")):
            self.job(self.service(root, current), "import", path, error="policy_violation")
        self.assertEqual((current.reads, current.writes, current.inventories), ([], [], 0))

    def test_classification_swap_cannot_restore_login_archive_as_full_profile(self):
        path, _, _ = self.login_archive()
        root, current = self.profile("swap-destination", ["7"])
        before = self.file_values(root), dict(current.data)
        service = self.service(root, current)
        with patch.object(service, "_archive_kind", return_value="profile"), \
                patch("mdata_files.sink", side_effect=AssertionError("Plaintext sink opened")):
            self.job(service, "import", path, mode="restore", error="invalid_archive")
        self.assertEqual((self.file_values(root), current.data), before)
        self.assertEqual((current.reads, current.writes, current.inventories), ([], [], 0))


if __name__ == "__main__":
    unittest.main()
