"""MData previews and selected exports with synthetic data and memory tokens."""
from __future__ import annotations

import base64
from contextlib import nullcontext
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import mdata_archive
import mdata_login
import mdata_manager as manager
import mdata_service
import test_mdata_service as fixtures


class MDataManagerTests(unittest.TestCase):
    profile = fixtures.MDataServiceTests.profile
    service = fixtures.MDataServiceTests.service

    def setUp(self):
        parent = Path(os.environ.get("VALMAX_TEST_TEMP_ROOT") or r"D:\ValMAX\audit\mdata-manager") if os.name == "nt" else None
        if parent is not None: parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="synthetic-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.addCleanup(patch.stopall)
        patch("session_credentials._api", side_effect=AssertionError("Real WinCred forbidden")).start()
        patch("mdata_service.transfer_lock", side_effect=lambda: nullcontext()).start()
        self.enterContext(fixtures.synthetic_disk_capacity(self.base))

    def archive(self, ids=("42",), metadata=None, *, kind="login", values=None):
        blobs = values if values is not None else {
            "auth_" + rid: json.dumps((metadata or {}).get(rid, {
                "first_name":"Synthetic", "last_name":"Account " + rid, "short_name":"SA",
                "bio":"PRIVATE-BIO", "phone":"PRIVATE-PHONE", "photo_url":"https://example.invalid/PRIVATE",
                "access_token":"DO-NOT-RETURN"})).encode("utf8") for rid in ids}
        tokens = [{"name":"access_token_" + rid + "@MXMOD",
                   "blob":base64.b64encode(("SYNTHETIC-TOKEN-" + rid).encode()).decode()} for rid in ids]
        manifest = {"schema":1,"sourceClient":"MAX Desktop","sourceVersion":"26.35.1",
                    "files":[{"path":name,"size":len(value)} for name,value in blobs.items()],"credentials":tokens}
        path = self.base / ("archive-" + str(len(tuple(self.base.glob("*.mdata")))) + ".mdata")
        with path.open("wb") as stream:
            mdata_archive.encrypt_stream(manifest, fixtures.PASSWORD, lambda name:io.BytesIO(blobs[name]), stream,
                                         policy=mdata_service._policy(), archive_kind=kind)
        return path

    def error(self, code, function, *args):
        with self.assertRaises(mdata_service.MDataError) as raised: function(*args)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code)

    def test_preview_returns_only_ids_and_native_names_after_complete_authentication(self):
        path = self.archive(("42", "7", "11"))
        before = path.read_bytes()
        with patch("mdata_login.capture", side_effect=AssertionError("Profile snapshot forbidden")), \
                patch("mdata_login.import_snapshot", side_effect=AssertionError("Import forbidden")), \
                patch("mdata_service.MDataCredentials", side_effect=AssertionError("WinCred construction forbidden")):
            value = manager.inspect(path.as_uri(), fixtures.PASSWORD)
        self.assertEqual(set(value), {"schema","kind","profiles","accounts","fingerprint"})
        self.assertEqual([row["accountId"] for row in value["accounts"]], ["7","11","42"])
        self.assertEqual(value["fingerprint"], __import__("hashlib").sha256(path.read_bytes()).hexdigest())
        self.assertEqual(value["profiles"], 3)
        self.assertEqual(value["accounts"][0], {"accountId":"7","displayName":"Synthetic Account 7","shortName":"SA"})
        for secret in ("PRIVATE", "TOKEN", "photo_url", "bio", "credentials", fixtures.PASSWORD):
            self.assertNotIn(secret, json.dumps(value))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(tuple(self.base.iterdir()), (path,))

    def test_wrong_password_terminal_tamper_truncation_and_trailing_bytes_never_project_metadata(self):
        path = self.archive()
        original = path.read_bytes()
        variants = [(original, "Wrong-Synthetic-Password-Only-123"),
                    (original[:-1] + bytes([original[-1] ^ 1]), fixtures.PASSWORD),
                    (original[:-16], fixtures.PASSWORD), (original + b"x", fixtures.PASSWORD)]
        for data,password in variants:
            path.write_bytes(data)
            with patch.object(manager, "_account_metadata", side_effect=AssertionError("Unauthenticated projection")) as project:
                with self.assertRaises(mdata_service.MDataError): manager.inspect(path.as_uri(), password)
                project.assert_not_called()

    def test_preview_has_fixed_size_limit_before_key_derivation(self):
        path = self.base / "oversized.mdata"
        with path.open("wb") as stream:
            stream.seek(manager.MAX_ARCHIVE_BYTES);stream.write(b"x")
        with patch("mdata_archive._derive", side_effect=AssertionError("KDF entered")):
            self.error("too_large", manager.inspect, path.as_uri(), fixtures.PASSWORD)

    def test_source_growth_and_sink_overflow_are_bounded(self):
        source = manager._BoundedSource(io.BytesIO(b"x" * (manager.MAX_ARCHIVE_BYTES + 1)))
        self.error("too_large", source.read, manager.MAX_ARCHIVE_BYTES + 200)
        self.assertEqual(source.used, manager.MAX_ARCHIVE_BYTES + 1)
        with manager._BoundedSink(3) as sink:
            sink.write(b"12")
            self.error("too_large", sink.write, b"34")
            self.assertEqual(sink.getvalue(), b"12")

    def test_legacy_full_profile_container_is_not_previewed(self):
        path = self.archive(kind="profile")
        with patch("mdata_archive._derive", side_effect=AssertionError("Legacy KDF entered")):
            self.error("unsupported_version", manager.inspect, path.as_uri(), fixtures.PASSWORD)

    def test_legacy_maxsession_is_not_accepted_by_compact_preview(self):
        path = self.base / "old.maxsession";path.write_bytes(b"synthetic")
        self.error("invalid_archive", manager.inspect, path.as_uri(), fixtures.PASSWORD)

    def test_forbidden_files_or_credentials_cannot_be_projected(self):
        path = self.archive(values={"cache/forbidden.bin":b"PRIVATE"})
        with patch.object(manager, "_account_metadata", side_effect=AssertionError("Projection entered")):
            self.error("policy_violation", manager.inspect, path.as_uri(), fixtures.PASSWORD)

    def test_missing_opaque_and_duplicate_key_auth_metadata_yield_unknown_names(self):
        for content in (None, b"SYNTHETIC-OPAQUE", b'{"first_name":"A","first_name":"B"}'):
            path = self.archive(values={} if content is None else {"auth_42":content})
            result = manager.inspect(path.as_uri(), fixtures.PASSWORD)
            self.assertEqual(result["accounts"], [{"accountId":"42","displayName":"","shortName":""}])

    def test_metadata_is_bounded_and_control_characters_cannot_spoof_rows(self):
        path = self.archive(metadata={"42":{"first_name":"<b>" + "x" * 500 + "</b>",
                                            "last_name":"A\u202eB\nC", "short_name":"Q\x00R\u2066S"}})
        row = manager.inspect(path.as_uri(), fixtures.PASSWORD)["accounts"][0]
        self.assertLessEqual(len(row["displayName"]), 256)
        self.assertTrue(row["displayName"].startswith("<b>"))
        self.assertTrue(row["displayName"].endswith("A B C"))
        self.assertEqual(row["shortName"], "Q R S")

    def test_selected_export_reads_only_selected_owned_token_and_keeps_client_running(self):
        root,memory = self.profile("source", ["42","7","11"])
        service = self.service(root, memory, activity=lambda: (_ for _ in ()).throw(AssertionError("Client activity queried")))
        path = self.base / "selected.mdata"
        with patch.object(memory, "owned_names", side_effect=AssertionError("Credential inventory used")), \
                patch.object(service, "_capture", side_effect=AssertionError("Full capture used")), \
                patch.object(service, "_wait_closed", side_effect=AssertionError("Native shutdown waited")):
            result = service.execute_selected_export(path.as_uri(), fixtures.PASSWORD, ["7"])
        self.assertEqual((result["state"],result["result"],result["profiles"]), ("done","exported",1))
        self.assertIsNone(service._worker)
        self.assertFalse(service.restart_requested())
        self.assertTrue(memory.reads)
        self.assertEqual(set(memory.reads), {"access_token_7@MXMOD"})
        self.assertFalse(memory.writes)
        self.assertEqual(manager.inspect(path.as_uri(), fixtures.PASSWORD)["accounts"][0]["accountId"], "7")
        self.assertLess(path.stat().st_size, 16384)

    def test_default_export_still_exports_all_active_logins(self):
        root,memory = self.profile("default-source", ["42","7"])
        path = self.base / "all.mdata"
        result = self.service(root,memory).execute("export", path.as_uri(), fixtures.PASSWORD, "restore")
        self.assertEqual(result["profiles"], 2)
        self.assertEqual([row["accountId"] for row in manager.inspect(path.as_uri(), fixtures.PASSWORD)["accounts"]], ["7","42"])

    def test_invalid_selection_is_rejected_before_any_profile_or_credential_access(self):
        root,memory = self.profile("invalid-selection", ["42"])
        service = self.service(root,memory)
        for selected in (None, [], ["42","42"], ["0"], ["042"], [42], [True], ["9223372036854775808"],
                         ["1","2","3","4"], "42", [{"id":"42"}]):
            with self.subTest(selection_type=type(selected).__name__), \
                    patch("mdata_login.listing", side_effect=AssertionError("Profile listed")):
                self.error("invalid_request", service.execute_selected_export,
                           (self.base / "invalid.mdata").as_uri(), fixtures.PASSWORD, selected)
        self.assertEqual((memory.reads,memory.writes,memory.inventories), ([],[],0))

    def test_unknown_selection_is_not_exported_and_reads_no_token(self):
        root,memory = self.profile("unknown-selection", ["42"])
        service = self.service(root,memory)
        self.error("invalid_request", service.execute_selected_export,
                   (self.base / "unknown.mdata").as_uri(), fixtures.PASSWORD, ["7"])
        self.assertEqual(memory.reads, [])
        self.assertFalse(service.pending())

    def test_missing_selected_token_fails_without_silently_exporting_another_account(self):
        root,memory = self.profile("stale-selection", ["42","7"])
        memory.data.pop("access_token_7@MXMOD")
        path = self.base / "stale.mdata"
        result = self.service(root,memory).execute_selected_export(path.as_uri(), fixtures.PASSWORD, ["7"])
        self.assertEqual((result["state"],result["error"]), ("error","no_accounts"))
        self.assertEqual(set(memory.reads), {"access_token_7@MXMOD"})
        self.assertFalse(path.exists())

    def test_three_canonical_selected_ids_are_sorted_without_changing_the_input(self):
        selected = ["9223372036854775807","42","7"]
        self.assertEqual(mdata_login.selected_account_ids(selected), ("7","42","9223372036854775807"))
        self.assertEqual(selected, ["9223372036854775807","42","7"])


if __name__ == "__main__": unittest.main()
