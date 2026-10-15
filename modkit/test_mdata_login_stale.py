"""Logged-out account recovery: own temp profiles, no real WinCred or MAX."""
from contextlib import contextmanager, nullcontext
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import launch_mod
import mdata_login as candidate
import mdata_service
from mdata_service import MDataError, MDataService
from session_credentials import CredentialError
from test_mdata_login import MemoryCredentials
import test_mdata_login as login_fixtures
import test_mdata_service as fixtures

TEMP_PARENT = Path(r"D:\CodexLabs\max-mod-26.35.1\analysis\test-temp") if os.name == "nt" else Path(tempfile.gettempdir())


class StaleLoginTests(unittest.TestCase):
    incoming = login_fixtures.CompactLoginTests.incoming
    seed = login_fixtures.CompactLoginTests.seed
    error = login_fixtures.CompactLoginTests.error
    values = login_fixtures.CompactLoginTests.values

    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="owned-stale-", dir=TEMP_PARENT)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root, self.stage = self.base / "MXMOD", self.base / "stage"
        self.root.mkdir()
        self.stage.mkdir()
        self.memory = MemoryCredentials()
        self.enterContext(patch("session_credentials._api", side_effect=AssertionError("Real WinCred forbidden")))

    def stale(self, rid="42", *, auth=b"SYNTHETIC-OLD-METADATA", user=True):
        if auth is not None:
            (self.root / ("auth_" + rid)).write_bytes(auth)
        if user:
            path = self.root / ("user" + rid) / "history" / "synthetic.db"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"SYNTHETIC-OLD-ENCRYPTED-HISTORY-NOT-READ-BY-IMPORT")

    def import_now(self, manifest, tokens):
        return candidate.import_snapshot(self.root, self.stage, manifest, tokens, self.memory)


    def test_candidate_reauthenticates_stale_owner_preserving_auth_and_user_data(self):
        self.stale()
        manifest, tokens = self.incoming()
        before = self.values()
        identities = {path.name: candidate._identity(path.stat()) for path in self.root.iterdir()}
        opened = []
        original = launch_mod._config_stream
        @contextmanager
        def bounded(path, *, create):
            self.assertTrue(Path(path).name.startswith("auth_"), "History/config must never be opened")
            opened.append(Path(path).name)
            with original(path, create=create) as stream:
                yield stream
        with patch.object(launch_mod, "_config_stream", side_effect=bounded):
            self.assertEqual(self.import_now(manifest, tokens), ("added", 1))
        self.assertEqual(self.values(), before)
        self.assertEqual(self.memory.data, tokens)
        self.assertEqual({path.name: candidate._identity(path.stat()) for path in self.root.iterdir()}, identities)
        self.assertEqual(opened, ["auth_42", "auth_42"])

    def test_stale_metadata_is_preserved_even_with_token_only_incoming(self):
        self.stale(user=False)
        manifest, tokens = self.incoming(metadata=False)
        before = self.values()
        self.assertEqual(self.import_now(manifest, tokens), ("added", 1))
        self.assertEqual(self.values(), before)
        self.assertEqual(self.memory.data, tokens)

    def test_empty_existing_optional_metadata_is_preserved(self):
        self.stale(auth=b"", user=False)
        manifest, tokens = self.incoming()
        self.assertEqual(self.import_now(manifest, tokens), ("added", 1))
        self.assertEqual((self.root / "auth_42").read_bytes(), b"")

    def test_existing_metadata_larger_than_bound_rejected_before_write(self):
        self.stale(auth=b"X" * (candidate.MAX_AUTH_BYTES + 1), user=False)
        manifest, tokens = self.incoming()
        self.error("invalid_profile", self.import_now, manifest, tokens)
        self.assertFalse(self.memory.writes)

    def test_directory_instead_of_auth_file_remains_collision(self):
        (self.root / "auth_42").mkdir()
        manifest, tokens = self.incoming()
        self.error("profile_collision", self.import_now, manifest, tokens)
        self.assertFalse(self.memory.writes)

    def test_file_instead_of_user_directory_remains_collision(self):
        (self.root / "user42").write_bytes(b"SYNTHETIC-WRONG-TYPE")
        manifest, tokens = self.incoming()
        self.error("profile_collision", self.import_now, manifest, tokens)
        self.assertFalse(self.memory.writes)

    def test_hardlinked_auth_file_remains_collision(self):
        (self.base / "other").write_bytes(b"SYNTHETIC-LINKED")
        os.link(self.base / "other", self.root / "auth_42")
        manifest, tokens = self.incoming()
        self.error("profile_collision", self.import_now, manifest, tokens)
        self.assertFalse(self.memory.writes)

    def test_reparse_or_unsafe_user_directory_rejected_before_write(self):
        self.stale(auth=None)
        manifest, tokens = self.incoming()
        original = launch_mod._check_path
        def rejected(path, *, directory):
            if Path(path) == self.root / "user42":
                raise ValueError("SYNTHETIC-reparse")
            return original(path, directory=directory)
        with patch.object(launch_mod, "_check_path", side_effect=rejected):
            self.error("profile_collision", self.import_now, manifest, tokens)
        self.assertFalse(self.memory.writes)

    def test_active_account_is_unchanged_and_never_replaced(self):
        self.seed("42", data=b"SYNTHETIC-ACTIVE-METADATA")
        self.stale(auth=None)
        manifest, tokens = self.incoming()
        before = self.values(), dict(self.memory.data)
        self.assertEqual(self.import_now(manifest, tokens), ("already_present", 0))
        self.assertEqual((self.values(), self.memory.data), before)
        self.assertFalse(self.memory.writes)

    def test_old_common_and_history_keys_are_neither_read_nor_changed(self):
        self.stale()
        self.memory.data.update({"app_data_storage_key_Common@MXMOD": b"SYNTHETIC-COMMON",
                                 "history_storage_key_42@MXMOD": b"SYNTHETIC-HISTORY-KEY"})
        before = dict(self.memory.data)
        manifest, tokens = self.incoming()
        self.assertEqual(self.import_now(manifest, tokens), ("added", 1))
        for name, value in before.items():
            self.assertEqual(self.memory.data[name], value)
        self.assertFalse(self.memory.reads)

    def test_second_token_failure_rolls_back_new_data_and_keeps_stale_owner_data(self):
        self.stale()
        manifest, tokens = self.incoming(("42", "43"))
        before = self.values(), dict(self.memory.data)
        self.memory.fail_target = "access_token_43@MXMOD"
        with self.assertRaises(CredentialError) as raised:
            self.import_now(manifest, tokens)
        self.assertEqual(raised.exception.code, "credential_io")
        self.assertEqual((self.values(), self.memory.data), before)

    def test_new_user_directory_appearance_is_not_overwritten(self):
        manifest, tokens = self.incoming()
        original = candidate._verify_preserved_user_directory
        def appeared(path, identity):
            if path.name == "user42":
                path.mkdir()
                (path / "foreign-synthetic").write_bytes(b"SYNTHETIC-OTHER-WRITER")
            return original(path, identity)
        with patch.object(candidate, "_verify_preserved_user_directory", side_effect=appeared):
            self.error("profile_changed", self.import_now, manifest, tokens)
        self.assertFalse(self.memory.writes)
        self.assertFalse((self.root / "auth_42").exists())
        self.assertEqual((self.root / "user42" / "foreign-synthetic").read_bytes(), b"SYNTHETIC-OTHER-WRITER")

    def test_auth_in_place_change_is_detected_without_deleting_other_writer_data(self):
        self.stale(user=False)
        manifest, tokens = self.incoming()
        original = candidate._verify_preserved_user_directory
        @contextmanager
        def permissive_read(path, *, create):
            self.assertFalse(create)
            with open(path, "rb") as stream:
                yield stream
        def changed(path, identity):
            (self.root / "auth_42").write_bytes(b"SYNTHETIC-CHANGED-IN-PLACE")
            return original(path, identity)
        # A deliberately permissive owned test handle lets us model an outside
        # writer; the production Win32 handle itself denies write/delete sharing.
        with patch.object(launch_mod, "_config_stream", side_effect=permissive_read), \
                patch.object(candidate, "_verify_preserved_user_directory", side_effect=changed):
            self.error("profile_changed", self.import_now, manifest, tokens)
        self.assertFalse(self.memory.writes)
        self.assertEqual((self.root / "auth_42").read_bytes(), b"SYNTHETIC-CHANGED-IN-PLACE")



    def test_empty_token_records_are_inactive_and_replaced_with_exact_expectation(self):
        self.stale()
        manifest, tokens = self.incoming(("42", "43"))
        self.memory.data.update({name: b"" for name in tokens})
        observed = []
        original = self.memory.set_owned
        def checked(name, value, expected):
            observed.append((name, expected))
            return original(name, value, expected)
        with patch.object(self.memory, "set_owned", side_effect=checked):
            self.assertEqual(self.import_now(manifest, tokens), ("added", 2))
        self.assertEqual(self.memory.data, tokens)
        self.assertEqual(observed, [(name, b"") for name in sorted(tokens)])

    def test_second_empty_token_failure_restores_both_empty_records_and_stale_files(self):
        self.stale()
        manifest, tokens = self.incoming(("42", "43"))
        self.memory.data.update({name: b"" for name in tokens})
        before = self.values(), dict(self.memory.data)
        self.memory.fail_target = "access_token_43@MXMOD"
        with self.assertRaises(CredentialError) as raised:
            self.import_now(manifest, tokens)
        self.assertEqual(raised.exception.code, "credential_io")
        self.assertEqual((self.values(), self.memory.data), before)

    def test_failure_after_empty_record_commit_restores_the_empty_record(self):
        self.stale()
        manifest, tokens = self.incoming()
        self.memory.data["access_token_42@MXMOD"] = b""
        before = self.values(), dict(self.memory.data)
        self.memory.fail_target = "access_token_42@MXMOD"
        self.memory.fail_after_commit = True
        original = self.memory.set_owned
        def one_shot(name, value, expected):
            try:
                return original(name, value, expected)
            except CredentialError:
                self.memory.fail_target = None
                raise
        with patch.object(self.memory, "set_owned", side_effect=one_shot):
            with self.assertRaises(CredentialError) as raised:
                self.import_now(manifest, tokens)
        self.assertEqual(raised.exception.code, "credential_io")
        self.assertEqual((self.values(), self.memory.data), before)

    def test_racing_active_token_never_replaced_or_rolled_back_as_an_empty_record(self):
        self.stale()
        manifest, tokens = self.incoming()
        target = "access_token_42@MXMOD"
        self.memory.data[target] = b""
        original = self.memory.set_owned
        def other_writer(name, value, expected):
            self.memory.data[name] = b"SYNTHETIC-OTHER-ACTIVE-TOKEN"
            return original(name, value, expected)
        with patch.object(self.memory, "set_owned", side_effect=other_writer):
            with self.assertRaises(CredentialError) as raised:
                self.import_now(manifest, tokens)
        self.assertEqual(raised.exception.code, "destination_not_empty")
        self.assertEqual(self.memory.data[target], b"SYNTHETIC-OTHER-ACTIVE-TOKEN")
        self.assertFalse(self.memory.deletes)


class StaleLoginServiceTests(unittest.TestCase):
    profile = fixtures.MDataServiceTests.profile
    service = fixtures.MDataServiceTests.service
    job = fixtures.MDataServiceTests.job
    file_values = staticmethod(fixtures.MDataServiceTests.file_values)

    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="owned-stale-service-", dir=TEMP_PARENT)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.enterContext(patch("mdata_service.transfer_lock", side_effect=lambda: nullcontext()))
        self.enterContext(patch("session_credentials._api", side_effect=AssertionError("Real WinCred forbidden")))
        self.enterContext(fixtures.synthetic_disk_capacity(self.base))

    def scenario(self):
        source, source_memory = self.profile("source", ["42", "43"])
        archive = self.base / "two-accounts.mdata"
        self.job(self.service(source, source_memory), "export", archive)
        self.assertLess(archive.stat().st_size, 16384)
        destination, memory = self.profile("destination", ["7", "42"])
        # An empty token is logged-out state even though local account files and
        # independent history/calls keys from that owner remain on this PC.
        memory.data["access_token_42@MXMOD"] = b""
        return archive, source, source_memory, destination, MemoryCredentials(memory.data)

    def test_encrypted_two_account_import_preserves_logged_out_data_and_active_third_account(self):
        archive, source, incoming, root, current = self.scenario()
        before = self.file_values(root), dict(current.data)
        service = self.service(root, current)
        result = self.job(service, "import", archive, mode="add")
        self.assertEqual((result["state"], result["result"], result["profiles"]), ("done", "added", 2))
        self.assertTrue(service.restart_requested())
        after = self.file_values(root)
        for name, value in before[0].items():
            self.assertEqual(after[name], value)
        for name, value in before[1].items():
            if name != "access_token_42@MXMOD":
                self.assertEqual(current.data[name], value)
        for rid in ("42", "43"):
            name = "access_token_" + rid + "@MXMOD"
            self.assertEqual(current.data[name], incoming.data[name])
        self.assertEqual(after["auth_43"], (source / "auth_43").read_bytes())
        self.assertFalse((root / "user43").exists())
        reloaded = self.service(root, current).snapshot()
        self.assertEqual((reloaded["state"], reloaded["result"], reloaded["profiles"]), ("done", "added", 2))

    def test_encrypted_second_token_failure_restores_empty_token_and_all_existing_data(self):
        archive, _source, _incoming, root, current = self.scenario()
        before = self.file_values(root), dict(current.data)
        current.fail_target = "access_token_43@MXMOD"
        service = self.service(root, current)
        result = self.job(service, "import", archive, mode="add", error="credential_io")
        self.assertEqual(result["profiles"], 0)
        self.assertEqual((self.file_values(root), current.data), before)
        self.assertTrue(service.restart_requested())
        reloaded = self.service(root, current).snapshot()
        self.assertEqual((reloaded["state"], reloaded["error"], reloaded["profiles"]), ("error", "credential_io", 0))


if __name__ == "__main__":
    unittest.main()
