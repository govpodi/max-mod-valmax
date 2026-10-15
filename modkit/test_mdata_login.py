"""Compact login transfer: owned temporary trees and fake credentials only."""
import base64
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import launch_mod
import mdata_files
import mdata_login as login
from mdata_service import MDataError
from session_credentials import CredentialError
from test_mdata_service import synthetic_disk_capacity


class MemoryCredentials:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.reads, self.writes, self.deletes = [], [], []
        self.inventories = 0
        self.fail_target = None
        self.fail_after_commit = False

    def owned_names(self):
        self.inventories += 1
        return tuple(sorted(self.data))

    def read(self, name):
        self.reads.append(name)
        return self.data.get(name)

    def set_owned(self, name, blob, expected):
        if self.data.get(name) != expected:
            raise CredentialError("destination_not_empty")
        self.writes.append(name)
        if name == self.fail_target and not self.fail_after_commit:
            raise CredentialError("credential_io")
        self.data[name] = blob
        if name == self.fail_target and self.fail_after_commit:
            raise CredentialError("credential_io")

    def delete_owned(self, name, expected):
        self.deletes.append(name)
        current = self.data.get(name)
        if current is None:
            return
        if current != expected:
            raise CredentialError("destination_not_empty")
        del self.data[name]


class CompactLoginTests(unittest.TestCase):
    def setUp(self):
        # Tests never read the actual MXMOD root or call the Windows API.
        temporary_parent = Path(r"D:\CodexLabs\max-mod-26.35.1\analysis\test-temp") if os.name == "nt" else None
        if temporary_parent is not None:
            temporary_parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="mdata-login-synthetic-", dir=temporary_parent)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "MXMOD"
        self.root.mkdir()
        self.stage = self.base / "stage"
        self.stage.mkdir()
        self.memory = MemoryCredentials()
        self.addCleanup(patch.stopall)
        patch("session_credentials._api", side_effect=AssertionError("Real WinCred forbidden")).start()
        self.enterContext(synthetic_disk_capacity(self.base))

    def seed(self, rid="42", *, token=True, data=b'{"first_name":"SYNTHETIC"}'):
        (self.root / ("auth_" + rid)).write_bytes(data)
        if token:
            self.memory.data["access_token_" + rid + "@MXMOD"] = ("SYNTHETIC-TOKEN-" + rid).encode("ascii")

    def incoming(self, ids=("42",), *, metadata=True):
        tokens, entries = {}, []
        for rid in ids:
            name = "access_token_" + rid + "@MXMOD"
            tokens[name] = ("SYNTHETIC-INCOMING-" + rid).encode("ascii")
            if metadata:
                data = ("SYNTHETIC-METADATA-" + rid).encode("ascii")
                (self.stage / ("auth_" + rid)).write_bytes(data)
                entries.append({"path": "auth_" + rid, "size": len(data)})
        manifest = {"schema": 1, "sourceClient": "MAX Desktop", "sourceVersion": "26.35.1",
                    "files": entries, "credentials": [{"name": name, "blob": base64.b64encode(value).decode("ascii")}
                                                      for name, value in tokens.items()]}
        return manifest, tokens

    def error(self, code, function, *args):
        with self.assertRaises(MDataError) as raised:
            function(*args)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code)

    def values(self):
        return {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def test_capture_ignores_gigabyte_history_and_does_not_inventory_credentials(self):
        self.seed()
        history = self.root / "user42" / "history" / "synthetic.db"
        history.parent.mkdir(parents=True)
        # Sparse fixture: logical 1.2 GiB, almost no physical storage.
        with history.open("wb") as output:
            # Seeking alone creates a fully allocated file on Windows/NTFS.
            # Mark only this owned synthetic file sparse before extending it.
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes
                import msvcrt
                control = ctypes.WinDLL("kernel32", use_last_error=True).DeviceIoControl
                control.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
                control.restype = wintypes.BOOL
                returned = wintypes.DWORD()
                self.assertTrue(control(msvcrt.get_osfhandle(output.fileno()), 0x900C4,
                                        None, 0, None, 0, ctypes.byref(returned), None),
                                "Cannot mark owned synthetic history file sparse")
            output.seek(1200 * 1024 * 1024)
            output.write(b"X")
        cache = self.root / "cache"
        cache.mkdir()
        (cache / "ordinary.bin").write_bytes(b"SYNTHETIC-CACHE")
        self.memory.data["app_data_storage_key_Common@MXMOD"] = b"SYNTHETIC-COMMON"
        opened = []
        original = launch_mod._config_stream
        @contextmanager
        def inspect(path, *, create):
            opened.append(Path(path).name)
            self.assertTrue(Path(path).name.startswith("auth_"))
            with original(path, create=create) as stream:
                yield stream
        with patch.object(launch_mod, "_config_stream", inspect), \
                patch.object(mdata_files, "listing", side_effect=AssertionError("Full profile listing forbidden")), \
                patch.object(self.memory, "owned_names", side_effect=AssertionError("Export inventory forbidden")):
            manifest, tokens, auth = login.capture(self.root, self.memory)
        self.assertEqual(manifest["files"], [{"path": "auth_42", "size": len(auth["auth_42"])}])
        self.assertEqual(set(tokens), {"access_token_42@MXMOD"})
        self.assertEqual(opened, ["auth_42", "auth_42"])
        self.assertEqual(self.memory.reads, ["access_token_42@MXMOD"] * 2)
        self.assertEqual((self.memory.inventories, self.memory.writes), (0, []))
        self.assertLess(sum(len(value) for value in auth.values()), 1024)

    def test_capture_skips_stale_auth_without_token(self):
        self.seed("42")
        self.seed("43", token=False)
        self.seed("44", token=False)
        self.memory.data["access_token_44@MXMOD"] = b""
        manifest, tokens, auth = login.capture(self.root, self.memory)
        self.assertEqual(set(auth), {"auth_42"})
        self.assertEqual(set(tokens), {"access_token_42@MXMOD"})
        self.assertEqual(len(manifest["credentials"]), 1)

    def test_capture_no_accounts_for_only_stale_metadata(self):
        self.seed(token=False)
        self.error("no_accounts", login.capture, self.root, self.memory)

    def test_listing_is_metadata_only_and_bounded(self):
        self.seed()
        self.memory.data.clear()
        with patch.object(launch_mod, "_config_stream", side_effect=AssertionError("Listing read contents")):
            self.assertEqual(login.listing(self.root), [{"path": "auth_42", "size": 26}])
        self.assertEqual(self.memory.reads, [])
        for number in range(1, login.MAX_CANDIDATES + 1):
            (self.root / ("auth_" + str(number + 100))).write_bytes(b"{}")
        self.error("invalid_profile", login.listing, self.root)

    def test_listing_refuses_noncanonical_auth_filename(self):
        for name in ("auth_0", "auth_01", "auth_bad", "Auth_42", "auth_9223372036854775808"):
            with self.subTest(name=name):
                path = self.root / name
                path.write_bytes(b"{}")
                self.error("invalid_profile", login.listing, self.root)
                path.unlink()

    def test_listing_refuses_oversize_directory_and_hardlink_auth(self):
        auth = self.root / "auth_42"
        auth.write_bytes(b"X" * (login.MAX_AUTH_BYTES + 1))
        self.error("invalid_profile", login.listing, self.root)
        auth.unlink()
        auth.mkdir()
        self.error("unsafe_path", login.listing, self.root)
        auth.rmdir()
        target = self.base / "foreign-synthetic"
        target.write_bytes(b"{}")
        os.link(target, auth)
        self.error("unsafe_path", login.listing, self.root)
        self.assertEqual(target.read_bytes(), b"{}")

    def test_capture_enforces_three_active_accounts(self):
        for rid in ("42", "43", "44", "45"):
            self.seed(rid)
        self.error("account_limit", login.capture, self.root, self.memory)

    def test_capture_rejects_changed_token(self):
        self.seed()
        original = self.memory.read
        def changed(name):
            value = original(name)
            return value if len(self.memory.reads) == 1 else b"CHANGED-SYNTHETIC"
        with patch.object(self.memory, "read", side_effect=changed):
            self.error("profile_changed", login.capture, self.root, self.memory)

    def test_capture_rejects_same_size_metadata_change(self):
        self.seed()
        original = login._read_auth
        calls = [0]
        def changed(*args, **kwargs):
            calls[0] += 1
            value = original(*args, **kwargs)
            return value if calls[0] == 1 else b"Y" * len(value)
        with patch.object(login, "_read_auth", side_effect=changed):
            self.error("profile_changed", login.capture, self.root, self.memory)

    def test_capture_rejects_a_stale_account_becoming_active_during_snapshot(self):
        self.seed("42")
        self.seed("43", token=False)
        original = self.memory.read
        def activated(name):
            if name == "access_token_43@MXMOD" and self.memory.reads.count(name):
                self.memory.data[name] = b"SYNTHETIC-NEWLY-ACTIVE"
            return original(name)
        with patch.object(self.memory, "read", side_effect=activated):
            self.error("profile_changed", login.capture, self.root, self.memory)

    def test_validation_allows_token_only_startup(self):
        manifest, tokens = self.incoming(metadata=False)
        self.assertTrue(login.validate(manifest, tokens))

    def test_validation_refuses_extra_files_keys_namespace_or_ids(self):
        for item in ({"path": "user42/history/file.db", "size": 1}, {"path": "auth_7", "size": 1},
                     {"path": "auth_042", "size": 1}, {"path": "auth_42", "size": True},
                     {"path": "auth_42", "size": login.MAX_AUTH_BYTES + 1}):
            manifest, tokens = self.incoming()
            manifest["files"] = [item]
            self.error("invalid_payload", login.validate, manifest, tokens)
        manifest, tokens = self.incoming()
        manifest["credentials"].append({"name": "app_data_storage_key_Common@MXMOD", "blob": "WA=="})
        self.error("invalid_payload", login.validate, manifest, tokens)
        manifest, tokens = self.incoming()
        tokens["access_token_7@ONEME"] = b"FOREIGN-SYNTHETIC"
        self.error("invalid_payload", login.validate, manifest, tokens)

    def test_validation_rejects_duplicate_records_mismatched_and_oversize_tokens(self):
        manifest, tokens = self.incoming()
        manifest["credentials"].append(dict(manifest["credentials"][0]))
        self.error("invalid_payload", login.validate, manifest, tokens)
        manifest, tokens = self.incoming()
        manifest["files"].append(dict(manifest["files"][0]))
        self.error("invalid_payload", login.validate, manifest, tokens)
        manifest, tokens = self.incoming()
        tokens["access_token_42@MXMOD"] = b"CHANGED"
        self.error("invalid_payload", login.validate, manifest, tokens)
        manifest, tokens = self.incoming()
        tokens["access_token_42@MXMOD"] = b"X" * 2561
        self.error("invalid_payload", login.validate, manifest, tokens)

    def test_import_preserves_every_existing_file_and_nonaccess_credential(self):
        self.seed("7")
        for name in ("settings/application", "app/synthetic.db", "user7/history/synthetic.db", "user7/files/image.bin"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("OLD-SYNTHETIC-" + name).encode("ascii"))
        self.memory.data.update({"app_data_storage_key_Common@MXMOD": b"COMMON", "history_storage_key_7@MXMOD": b"HISTORY",
                                 "calls_token_7@MXMOD": b"CALLS", "synthetic-key-5_2@MXMOD": b"WEB", "access_token_8@ONEME": b"FOREIGN"})
        old_files, old_keys = self.values(), dict(self.memory.data)
        manifest, tokens = self.incoming()
        with patch.object(mdata_files, "listing", side_effect=AssertionError("Full profile listing forbidden")):
            self.assertEqual(login.import_snapshot(self.root, self.stage, manifest, tokens, self.memory), ("added", 1))
        for name, data in old_files.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        for name, data in old_keys.items():
            self.assertEqual(self.memory.data[name], data)
        self.assertEqual(self.memory.reads, ["access_token_7@MXMOD"])
        self.assertEqual(self.memory.writes, ["access_token_42@MXMOD"])
        self.assertEqual(set(self.values()) - set(old_files), {"auth_42"})

    def test_import_token_is_committed_only_after_auth_fsync(self):
        manifest, tokens = self.incoming()
        events = []
        original_set = self.memory.set_owned
        def token(*args):
            self.assertEqual(events, ["fsync"])
            self.assertEqual((self.root / "auth_42").read_bytes(), (self.stage / "auth_42").read_bytes())
            events.append("token")
            original_set(*args)
        with patch.object(login.os, "fsync", side_effect=lambda fd: events.append("fsync")), \
                patch.object(self.memory, "set_owned", side_effect=token):
            self.assertEqual(login.import_snapshot(self.root, self.stage, manifest, tokens, self.memory), ("added", 1))
        self.assertEqual(events, ["fsync", "token"])

    def test_import_is_idempotent_and_does_not_overwrite_existing_token(self):
        self.seed("42")
        before = self.values(), dict(self.memory.data)
        manifest, tokens = self.incoming()
        self.assertEqual(login.import_snapshot(self.root, self.stage, manifest, tokens, self.memory), ("already_present", 0))
        self.assertEqual((self.values(), self.memory.data), before)
        self.assertEqual(self.memory.writes, [])

    def test_import_accepts_token_only_account(self):
        manifest, tokens = self.incoming(metadata=False)
        self.assertEqual(login.import_snapshot(self.root, self.stage, manifest, tokens, self.memory), ("added", 1))
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.memory.data, tokens)

    def test_import_completes_matching_auth_from_interrupted_previous_attempt(self):
        manifest, tokens = self.incoming()
        data = (self.stage / "auth_42").read_bytes()
        (self.root / "auth_42").write_bytes(data)
        old_identity = login._identity((self.root / "auth_42").stat())
        with patch.object(login, "_new_stream", side_effect=AssertionError("Matching auth must not be rewritten")):
            self.assertEqual(login.import_snapshot(self.root, self.stage, manifest, tokens, self.memory), ("added", 1))
        self.assertEqual(login._identity((self.root / "auth_42").stat()), old_identity)

    def test_import_preserves_stale_user_directory_and_unmatched_optional_auth(self):
        manifest, tokens = self.incoming()
        user = self.root / "user42"
        user.mkdir()
        (self.root / "auth_42").write_bytes(b"SYNTHETIC-OLD-AUTH")
        before = self.values()
        self.assertEqual(login.import_snapshot(self.root, self.stage, manifest, tokens, self.memory), ("added", 1))
        self.assertTrue(user.is_dir())
        self.assertEqual(self.values(), before)
        self.assertEqual(self.memory.data, tokens)

    def test_import_refuses_wrong_object_types_before_writes(self):
        manifest, tokens = self.incoming()
        user = self.root / "user42"
        user.write_bytes(b"SYNTHETIC-WRONG-TYPE")
        self.error("profile_collision", login.import_snapshot, self.root, self.stage, manifest, tokens, self.memory)
        user.unlink()
        (self.root / "auth_42").mkdir()
        self.error("profile_collision", login.import_snapshot, self.root, self.stage, manifest, tokens, self.memory)
        self.assertEqual(self.memory.writes, [])

    def test_import_limit_is_checked_before_any_writes(self):
        for rid in ("7", "8", "9"):
            self.seed(rid)
        manifest, tokens = self.incoming()
        self.error("account_limit", login.import_snapshot, self.root, self.stage, manifest, tokens, self.memory)
        self.assertEqual(self.memory.writes, [])
        self.assertFalse((self.root / "auth_42").exists())

    def test_invalid_manifest_rejected_before_inventory_or_native_reads(self):
        manifest, tokens = self.incoming()
        manifest["files"][0]["path"] = "../auth_42"
        self.error("invalid_payload", login.import_snapshot, self.root, self.stage, manifest, tokens, self.memory)
        self.assertEqual((self.memory.inventories, self.memory.reads, self.memory.writes), (0, [], []))

    def test_import_staged_size_mismatch_rejected_before_native_io(self):
        manifest, tokens = self.incoming()
        (self.stage / "auth_42").write_bytes(b"X")
        self.error("invalid_payload", login.import_snapshot, self.root, self.stage, manifest, tokens, self.memory)
        self.assertEqual((self.memory.inventories, self.memory.reads, self.memory.writes), (0, [], []))

    def test_import_credential_failure_rolls_back_only_created_auth_and_tokens(self):
        self.seed("7")
        before = self.values(), dict(self.memory.data)
        manifest, tokens = self.incoming(("42", "43"))
        self.memory.fail_target = "access_token_43@MXMOD"
        with self.assertRaises(CredentialError):
            login.import_snapshot(self.root, self.stage, manifest, tokens, self.memory)
        self.assertEqual((self.values(), self.memory.data), before)
        self.assertEqual(set(self.memory.deletes), set(tokens))

    def test_import_failure_after_native_commit_also_rolls_back_created_token(self):
        manifest, tokens = self.incoming()
        self.memory.fail_target = "access_token_42@MXMOD"
        self.memory.fail_after_commit = True
        with self.assertRaises(CredentialError):
            login.import_snapshot(self.root, self.stage, manifest, tokens, self.memory)
        self.assertEqual(self.memory.data, {})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_import_auth_fsync_failure_leaves_no_files_or_tokens(self):
        manifest, tokens = self.incoming()
        with patch.object(login.os, "fsync", side_effect=OSError("SYNTHETIC-IO-FAILURE")):
            with self.assertRaises(OSError):
                login.import_snapshot(self.root, self.stage, manifest, tokens, self.memory)
        self.assertEqual(self.memory.writes, [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_rollback_preserves_replaced_token_and_reports_fixed_error(self):
        manifest, tokens = self.incoming()
        def changed_token(name, blob, expected):
            self.memory.data[name] = b"FOREIGN-SYNTHETIC-REPLACEMENT"
            raise OSError("SYNTHETIC-IO-FAILURE")
        with patch.object(self.memory, "set_owned", side_effect=changed_token):
            self.error("rollback_failed", login.import_snapshot, self.root, self.stage, manifest, tokens, self.memory)
        self.assertEqual(self.memory.data, {"access_token_42@MXMOD": b"FOREIGN-SYNTHETIC-REPLACEMENT"})

    def test_rollback_preserves_auth_modified_in_place_and_reports_fixed_error(self):
        manifest, tokens = self.incoming()
        def changed_auth(name, blob, expected):
            (self.root / "auth_42").write_bytes(b"FOREIGN-SYNTHETIC-REPLACEMENT")
            raise OSError("SYNTHETIC-IO-FAILURE")
        with patch.object(self.memory, "set_owned", side_effect=changed_auth):
            self.error("rollback_failed", login.import_snapshot, self.root, self.stage, manifest, tokens, self.memory)
        self.assertEqual((self.root / "auth_42").read_bytes(), b"FOREIGN-SYNTHETIC-REPLACEMENT")
        self.assertEqual(self.memory.data, {})

    def test_prewrite_credential_conflict_never_deletes_the_other_writer_token(self):
        manifest, tokens = self.incoming()
        def conflict(name, blob, expected):
            self.memory.data[name] = blob  # Another importer committed equal bytes.
            raise CredentialError("destination_not_empty")
        with patch.object(self.memory, "set_owned", side_effect=conflict):
            with self.assertRaises(CredentialError):
                login.import_snapshot(self.root, self.stage, manifest, tokens, self.memory)
        self.assertEqual(self.memory.data, tokens)
        self.assertEqual(self.memory.deletes, [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_auth_replacement_before_token_commit_is_preserved_without_committing_token(self):
        manifest, tokens = self.incoming()
        original_present = login._present
        calls = [0]
        def replaced(path):
            if path.name == "user42":
                calls[0] += 1
                if calls[0] == 2:
                    replacement = self.base / "synthetic-replacement"
                    replacement.write_bytes((self.stage / "auth_42").read_bytes())
                    os.replace(replacement, self.root / "auth_42")
            return original_present(path)
        with patch.object(login, "_present", side_effect=replaced):
            self.error("rollback_failed", login.import_snapshot, self.root, self.stage, manifest, tokens, self.memory)
        self.assertEqual(self.memory.writes, [])
        self.assertEqual((self.root / "auth_42").read_bytes(), (self.stage / "auth_42").read_bytes())

    def test_user_directory_appearing_after_preflight_prevents_token_commit(self):
        manifest, tokens = self.incoming()
        def appeared(_fd):
            (self.root / "user42").mkdir()
        with patch.object(login.os, "fsync", side_effect=appeared):
            self.error("profile_changed", login.import_snapshot, self.root, self.stage, manifest, tokens, self.memory)
        self.assertEqual(self.memory.writes, [])
        self.assertTrue((self.root / "user42").is_dir())
        self.assertFalse((self.root / "auth_42").exists())


if __name__ == "__main__":
    unittest.main()
