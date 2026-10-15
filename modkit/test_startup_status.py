"""Synthetic startup metadata only; no native client/profile/credential access."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import startup_status as status


ID_A = "a" * 32
ID_B = "b" * 32
SECRET = "SYNTHETIC_AUTH_OR_EXCEPTION_TEXT"


def receipt(**changes):
    return {"schema": 1, "operationId": ID_A, "role": "launcher",
            "stage": "bootstrap", "state": "starting", "code": "none", **changes}


class FiniteMetadataTests(unittest.TestCase):
    def tearDown(self):
        status.reset()
        status.set_role("launcher")

    def test_valid_receipt_has_exact_metadata_fields(self):
        value = receipt()
        self.assertEqual(status.parse(json.dumps(value).encode()), value)
        self.assertEqual(set(value), {"schema", "operationId", "role", "stage", "state", "code"})

    def test_unknown_and_wrong_type_fields_are_rejected_without_echo(self):
        bad = [receipt(schema=True), receipt(schema=2), receipt(operationId=SECRET),
               receipt(role=SECRET), receipt(stage=SECRET), receipt(state=SECRET),
               receipt(code=SECRET), receipt(state="error", code="none"),
               receipt(state="ready", code="payload_invalid"), receipt(path=SECRET),
               receipt(operationId=1), receipt(role=[]), receipt(stage={}),
               receipt(state=1), receipt(code=[])]
        for value in bad:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(status.StartupStatusError) as caught:
                    status.parse(json.dumps(value).encode())
                self.assertEqual(str(caught.exception), "invalid_receipt")
                self.assertNotIn(SECRET, repr(caught.exception))

    def test_bounded_strict_json(self):
        for data in (b"[]", b"null", b"NaN", b"\xff", b"x" * (status.MAX_BYTES + 1),
                     b'{"schema":1,"schema":1}', "not-bytes"):
            with self.subTest(kind=type(data).__name__):
                with self.assertRaises(status.StartupStatusError):
                    status.parse(data)

    def test_failure_errors_and_messages_are_fixed(self):
        for code in status.CODES:
            error = status.StartupError(code)
            self.assertIsInstance(error, ValueError)
            self.assertEqual(str(error), code)
            self.assertEqual(error.code, code)
            self.assertNotIn(SECRET, status.message_for(code))
        for value in (SECRET, None, [], 1):
            with self.assertRaises(ValueError):
                status.StartupError(value)
            with self.assertRaises(ValueError):
                status.message_for(value)

    def test_only_actual_recovery_failure_recommends_mdata_recovery(self):
        mentioned = [code for code in status.CODES if "MData" in status.message_for(code)]
        self.assertEqual(mentioned, ["recovery_required"])
        self.assertIn("Восстановление MData", status.message_for("recovery_required"))

    def test_public_environment_id_is_copied_and_validated(self):
        original = {"UNCHANGED": "value"}
        clean = status.child_environment(original, ID_A)
        self.assertEqual(original, {"UNCHANGED": "value"})
        self.assertEqual(clean, {**original, status.ENVIRONMENT_KEY: ID_A})
        self.assertEqual(status.operation_from_environment(clean), ID_A)
        self.assertIsNone(status.operation_from_environment(original))
        for value in (SECRET, "A" * 32, "a" * 33, 1, [], "a" * 31 + "\0"):
            with self.assertRaises(ValueError) as caught:
                status.operation_from_environment({status.ENVIRONMENT_KEY: value})
            self.assertNotIn(SECRET, str(caught.exception))

    def test_ids_are_fresh_and_exactly_bounded(self):
        values = {status.new_operation_id() for _ in range(30)}
        self.assertEqual(len(values), 30)
        self.assertTrue(all(len(value) == 32 and set(value) <= set("0123456789abcdef") for value in values))

    def test_local_failure_survives_absent_or_broken_receipt(self):
        status.reset()
        self.assertFalse(status.fail("payload_invalid"))
        self.assertEqual(status.failure_code(), "payload_invalid")
        status.fail("observer_timeout")
        self.assertEqual(status.failure_code(), "payload_invalid")
        status.reset()
        self.assertEqual(status.failure_code(), "startup_failed")

    def test_reset_does_no_io_and_preserves_role_and_inherited_id(self):
        with patch.dict(os.environ, {status.ENVIRONMENT_KEY: ID_A}), \
             patch.object(status, "_role", "mdata"), \
             patch.object(status, "_current", object()), \
             patch.object(status, "_failure", "payload_invalid"), \
             patch("builtins.open", side_effect=AssertionError("filesystem access")):
            status.reset()
            self.assertIsNone(status.current())
            self.assertEqual(status.failure_code(), "startup_failed")
            self.assertEqual(status._role, "mdata")
            self.assertEqual(status.operation_from_environment(), ID_A)


class StartupReceiptTests(unittest.TestCase):
    def setUp(self):
        # All synthetic files stay on D, including temporary cleanup artifacts.
        parent = Path(__file__).resolve().parent.parent / "analysis" / "installed-startup-v1"
        self.temporary = tempfile.TemporaryDirectory(prefix="startup-fixture-", dir=parent)
        self.root = Path(self.temporary.name)
        self.helper = self.root / status.DIRECTORY
        self.report = status.StartupStatus(self.helper, "launcher", ID_A)
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        status.reset()

    def tearDown(self):
        status.reset()
        status.set_role("launcher")
        self.environment.stop()
        self.temporary.cleanup()

    def test_constructor_and_missing_read_create_nothing(self):
        self.assertFalse(self.helper.exists())
        self.assertIsNone(self.report.read())
        self.assertFalse(self.helper.exists())

    def test_unclaimed_child_does_not_create_a_receipt(self):
        child = status.StartupStatus(self.helper, "observe", ID_A)
        self.assertFalse(child.begin("observer"))
        self.assertFalse(self.helper.exists())

    def test_root_claim_and_success_round_trip(self):
        self.assertTrue(self.report.begin("verify", claim=True))
        self.assertEqual(self.report.read(), receipt(stage="verify"))
        self.assertLess(self.report.path.stat().st_size, status.MAX_BYTES)
        self.assertTrue(self.report.ready())
        self.assertEqual(self.report.read(), receipt(stage="complete", state="ready"))

    def test_child_updates_claimed_same_operation(self):
        self.report.begin(claim=True)
        child = status.StartupStatus(self.helper, "observe", ID_A)
        self.assertTrue(child.begin("frida_initialize"))
        self.assertEqual(self.report.read(), receipt(role="observe", stage="frida_initialize"))

    def test_old_child_cannot_clobber_later_root_attempt(self):
        self.report.begin(claim=True)
        old_child = status.StartupStatus(self.helper, "observe", ID_A)
        newer = status.StartupStatus(self.helper, "launcher", ID_B)
        newer.begin("verify", claim=True)
        before = newer.path.read_bytes()
        self.assertFalse(old_child.fail("native_spawn_failed", stage="native_spawn"))
        self.assertEqual(newer.path.read_bytes(), before)
        self.assertIsNone(self.report.read())
        self.assertIsNone(newer.read(ID_A))

    def test_first_precise_child_error_is_sticky(self):
        self.report.begin(claim=True)
        child = status.StartupStatus(self.helper, "observe", ID_A)
        self.assertTrue(child.fail("native_spawn_failed", stage="native_spawn"))
        before = child.read()
        self.assertFalse(self.report.fail("observer_timeout", stage="observer"))
        self.assertFalse(self.report.ready())
        self.assertFalse(child.begin("observer"))
        self.assertEqual(self.report.read(), before)

    def test_new_root_claim_replaces_previous_terminal_error(self):
        self.report.begin(claim=True)
        self.report.fail("payload_invalid")
        newer = status.StartupStatus(self.helper, "launcher", ID_B)
        self.assertTrue(newer.begin("verify", claim=True))
        self.assertEqual(newer.read()["code"], "none")

    def test_observer_cannot_claim_and_dedicated_root_can(self):
        observer = status.StartupStatus(self.helper, "observe", ID_A)
        with self.assertRaises(ValueError):
            observer.begin(claim=True)
        for role in ("recover", "mdata"):
            root = status.StartupStatus(self.helper, role, ID_B)
            self.assertTrue(root.begin(claim=True))
            self.assertEqual(root.read()["role"], role)

    def test_malformed_existing_receipt_is_preserved_and_optional(self):
        self.helper.mkdir()
        self.report.path.write_bytes(b"not-json")
        self.assertFalse(self.report.try_write("verify", claim=True))
        self.assertIsNone(self.report.try_read())
        self.assertEqual(self.report.path.read_bytes(), b"not-json")

    def test_hardlinked_destination_is_rejected_without_touching_source(self):
        self.helper.mkdir()
        other = self.root / "must-stay.json"
        data = json.dumps(receipt()).encode()
        other.write_bytes(data)
        os.link(other, self.report.path)
        self.assertFalse(self.report.try_write("verify", claim=True))
        self.assertEqual(other.read_bytes(), data)
        self.assertEqual(self.report.path.read_bytes(), data)

    def test_symlinked_helper_is_rejected(self):
        other = self.root / "must-stay"
        other.mkdir()
        try:
            self.helper.symlink_to(other, target_is_directory=True)
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                self.skipTest("Windows symlink privilege unavailable")
            raise
        self.assertFalse(self.report.try_write("verify", claim=True))
        self.assertEqual(list(other.iterdir()), [])

    def test_helper_directory_name_is_fixed(self):
        for path in (self.root / "MXMOD", self.root / "other", Path("relative") / status.DIRECTORY):
            with self.assertRaises(status.StartupStatusError):
                status.StartupStatus(path, "launcher", ID_A)

    def test_failure_to_replace_preserves_old_receipt_and_removes_owned_temp(self):
        self.report.begin(claim=True)
        before = self.report.path.read_bytes()
        with patch.object(status.os, "replace", side_effect=PermissionError(SECRET)):
            self.assertFalse(self.report.try_write("verify"))
        self.assertEqual(self.report.path.read_bytes(), before)
        self.assertEqual([path for path in self.helper.iterdir() if path.suffix == ".tmp"], [])

    def test_module_root_begin_claims_fresh_and_child_inherits(self):
        manifest = self.root / "runtime" / "mod" / "mod-manifest.json"
        manifest.parent.mkdir(parents=True)
        root = status.begin(manifest, "launcher", claim=True)
        self.assertIsNotNone(root)
        root_id = root.operation_id
        self.assertEqual(os.environ[status.ENVIRONMENT_KEY], root_id)
        self.assertTrue(status.stage("desktop"))
        status.reset()
        child = status.begin(manifest, "observe")
        self.assertEqual(child.operation_id, root_id)
        self.assertTrue(status.stage("native_spawn"))
        self.assertTrue(status.fail("native_spawn_failed"))
        status._current = root
        self.assertEqual(status.failure_code(), "native_spawn_failed")

    def test_observer_without_inherited_id_creates_nothing(self):
        manifest = self.root / "runtime" / "mod" / "mod-manifest.json"
        self.assertIsNone(status.begin(manifest, "observe"))
        self.assertFalse((manifest.parent.parent / status.DIRECTORY).exists())

    def test_stale_receipt_does_not_override_local_failure(self):
        self.report.begin(claim=True)
        self.report.fail("recovery_required")
        status._current = status.StartupStatus(self.helper, "launcher", ID_B)
        status.fail("observer_timeout")
        self.assertEqual(status.failure_code(), "observer_timeout")

    def test_module_invalid_manifest_or_failed_store_never_blocks_startup(self):
        for manifest in (Path("relative/mod-manifest.json"), self.root / "other.json"):
            self.assertIsNone(status.begin(manifest, claim=True))
        manifest = self.root / "runtime" / "mod" / "mod-manifest.json"
        with patch.object(status.StartupStatus, "try_write", return_value=False):
            self.assertIsNotNone(status.begin(manifest, claim=True))


if __name__ == "__main__":
    unittest.main()
