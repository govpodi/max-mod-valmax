"""Settings/ACK and marker bridge tests; generated metadata only, all files on D:."""
import errno
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import message_retention_service as target
from message_retention_store import RetentionStoreError


def native(**changes):
    return {"ready": True, "enabled": False, "retainedEvents": 0,
            "retainedMessages": 0, "error": None, "historySyncVerified": False, **changes}


def event(**changes):
    return {"kind": "retained-delete", "schema": 1, "accountId": "1", "chatId": "-1",
            "messageIds": ["1", "2"], **changes}


class RetentionServiceTests(unittest.TestCase):
    def setUp(self):
        base = Path("D:/ValMAX/analysis/message-retention-v1")
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="service-test-", dir=base)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.service = target.MessageRetentionService(self.root)

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(target.RetentionServiceError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code)

    def configure(self, enabled):
        return self.service.configure({"schema": 1, "revision": self.service.desired()["revision"], "enabled": enabled})

    def lookup(self, **changes):
        return self.service.lookup({"schema": 1, "accountId": "1", "chatId": "-1",
                                    "messageIds": ["1", "2"], **changes})

    def acknowledge(self, pid=123, **changes):
        self.service.register(pid)
        self.service.update(pid, native(**changes))

    def test_default_is_off_and_missing_lookup_is_ready_without_hook_ack_or_files(self):
        desired = self.service.desired()
        self.assertFalse(desired["enabled"])
        self.assertEqual(len(desired["revision"]), 64)
        self.assertEqual(desired, self.service.desired())
        self.assertFalse(self.service.snapshot()["runtime"]["ready"])
        self.assertFalse(self.service.snapshot()["runtime"]["applied"])
        result = self.lookup()
        self.assertEqual(result["deletedIds"], [])
        self.assertTrue(result["ready"])
        self.assertFalse(result["enabled"])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_enabling_requires_ready_runtime_before_cas_write(self):
        self.error("runtime_unavailable", self.configure, True)
        self.service.register(123)
        self.error("runtime_unavailable", self.configure, True)
        self.service.update(123, native(ready=False, error="waiting_for_modules"))
        self.error("runtime_unavailable", self.configure, True)
        self.assertEqual(list(self.root.iterdir()), [])
        self.service.update(123, native())
        saved = self.configure(True)
        self.assertTrue(saved["enabled"])
        self.assertFalse(saved["runtime"]["applied"])
        self.assertFalse(saved["runtime"]["enabled"])
        self.service.update(123, native(enabled=True))
        self.assertTrue(self.service.snapshot()["runtime"]["applied"])
        self.assertTrue(self.service.snapshot()["runtime"]["enabled"])

    def test_disable_remains_available_when_runtime_or_storage_is_unavailable(self):
        self.acknowledge()
        self.configure(True)
        self.service.invalidate(123)
        self.service._storage_failed = True
        result = self.configure(False)
        self.assertFalse(result["enabled"])
        self.assertFalse(result["runtime"]["ready"])
        self.assertFalse(result["runtime"]["applied"])

    def test_all_registered_pids_need_ack_and_stale_pid_is_not_silently_forgotten(self):
        self.acknowledge(123, historySyncVerified=True, retainedEvents=3, retainedMessages=5)
        self.acknowledge(456, historySyncVerified=True, retainedEvents=7, retainedMessages=11)
        result = self.service.snapshot()["runtime"]
        self.assertTrue(result["ready"])
        self.assertTrue(result["applied"])
        self.assertTrue(result["historySyncVerified"])
        self.assertEqual((result["retainedEvents"], result["retainedMessages"]), (10, 16))
        self.service.invalidate(456)
        self.assertFalse(self.service.snapshot()["runtime"]["ready"])
        self.assertFalse(self.service.snapshot()["runtime"]["historySyncVerified"])
        self.service.remove(456)
        self.assertTrue(self.service.snapshot()["runtime"]["ready"])

    def test_native_metadata_rejects_unhashable_or_arbitrary_errors_and_payload_values(self):
        for error in ([], {}, set(), 123, False, "PRIVATE_SYNTHETIC_VALUE"):
            with self.subTest(error=type(error).__name__):
                self.error("invalid_request", target.native_snapshot, native(error=error))
        for value in (native(ready=True, error="hook_failed"), native(retainedEvents=True),
                      native(retainedMessages=1000001), native(historySyncVerified=1),
                      {**native(), "text": "PRIVATE_SYNTHETIC_VALUE"}):
            self.error("invalid_request", target.native_snapshot, value)
        for error in target._NATIVE_ERRORS:
            value = target.native_snapshot(native(ready=False, error=error))
            self.assertEqual(value["error"], error)

    def test_pid_validation_and_unknown_pid_update_are_fixed_errors(self):
        for pid in (None, [], {}, True, "123", -1, 0, 1 << 32):
            for method, args in ((self.service.register, ()), (self.service.remove, ()),
                                 (self.service.invalidate, ()), (self.service.update, (native(),)),
                                 (self.service.record, (event(),))):
                self.error("invalid_request", method, pid, *args)
        self.error("invalid_request", self.service.update, 123, native())
        self.error("runtime_unavailable", self.service.record, 123, event())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_only_finite_marker_events_enter_the_private_store(self):
        self.acknowledge(enabled=True)
        self.service.record(123, event())
        self.assertEqual(self.lookup()["deletedIds"], ["1", "2"])
        self.assertEqual(self.lookup(accountId="2")["deletedIds"], [])
        for payload in (event(text="PRIVATE_SYNTHETIC_VALUE"), event(messageIds=[]),
                        event(messageIds=["1", "1"]), event(accountId="0"),
                        event(messageIds=["PRIVATE_SYNTHETIC_VALUE"]), event(schema=True),
                        event(kind="packet")):
            self.error("invalid_request", self.service.record, 123, payload)
        self.assertFalse(self.service._storage_failed)
        self.assertNotIn("PRIVATE_SYNTHETIC_VALUE", json.dumps(self.lookup()))
        self.service.remove(123)
        self.error("runtime_unavailable", self.service.record, 123, event(messageIds=["3"]))

    def test_capture_off_or_new_service_does_not_hide_persisted_deletion_markers(self):
        self.acknowledge(enabled=True)
        self.service.record(123, event())
        restarted = target.MessageRetentionService(self.root)
        result = restarted.lookup({"schema": 1, "accountId": "1", "chatId": "-1", "messageIds": ["2", "1"]})
        self.assertEqual(result["deletedIds"], ["2", "1"])
        self.assertTrue(result["ready"])
        self.assertFalse(result["enabled"])
        self.assertFalse(restarted.snapshot()["runtime"]["ready"])

    def test_storage_failure_closes_capture_without_exposing_error_text(self):
        self.acknowledge(enabled=True)
        with patch.object(self.service.markers, "mark", side_effect=RetentionStoreError("no_space")):
            self.error("no_space", self.service.record, 123, event())
        result = self.service.snapshot()["runtime"]
        self.assertFalse(result["ready"])
        self.assertFalse(result["applied"])
        self.assertEqual(result["error"], "storage_unavailable")
        self.error("runtime_unavailable", self.configure, True)
        self.assertFalse(self.configure(False)["enabled"])

    def test_preflight_proves_marker_storage_and_failed_enable_does_not_save_settings(self):
        self.acknowledge()
        initial = self.service.desired()
        with patch.object(self.service.markers, "ensure_ready", side_effect=RetentionStoreError("invalid_store")):
            self.error("invalid_store", self.configure, True)
        self.assertEqual(initial, self.service.desired())
        self.assertEqual(self.service.snapshot()["runtime"]["error"], "storage_unavailable")
        self.assertTrue(self.service.preflight_marker_store())
        self.assertFalse(self.service._storage_failed)
        self.assertEqual(self.lookup()["deletedIds"], [])
        self.assertTrue(self.configure(True)["enabled"])

    def test_configure_and_lookup_require_strict_public_key_sets(self):
        for body in ({}, {"schema": True, "revision": "a" * 64, "enabled": False},
                     {"schema": 1, "revision": "a" * 64, "enabled": False, "raw": "PRIVATE_SYNTHETIC_VALUE"}):
            self.error("invalid_request", self.service.configure, body)
        self.error("invalid_request", self.service.configure, {"schema": 1, "revision": "a" * 64, "enabled": 0})
        self.error("invalid_revision", self.service.configure, {"schema": 1, "revision": [], "enabled": False})
        self.error("invalid_request", self.service.lookup, {"schema": 1, "accountId": "1", "chatId": "-1", "messageIds": [], "text": "PRIVATE_SYNTHETIC_VALUE"})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_settings_cas_and_json_validation_preserve_existing_bytes(self):
        store = self.service.settings
        first = store.snapshot()
        saved = store.replace(first["revision"], True)
        path = self.root / "message-retention-settings.json"
        before = path.read_bytes()
        self.error("revision_conflict", store.replace, first["revision"], False)
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(saved, target.RetentionSettingsStore(path).snapshot())
        for data in (b'{"schema":1,"enabled":true,"enabled":false}',
                     b'{"schema":true,"enabled":false}', b'{"schema":1,"enabled":NaN}',
                     b'{"schema":1,"enabled":false,"extra":"PRIVATE_SYNTHETIC_VALUE"}'):
            path.write_bytes(data)
            self.error("invalid_store", store.snapshot)
            self.assertEqual(path.read_bytes(), data)

    def test_failed_write_cleans_only_owned_temporary_and_reports_disk_full(self):
        store = self.service.settings
        first = store.snapshot()
        with patch.object(target.os, "fsync", side_effect=OSError(errno.ENOSPC, "PRIVATE_SYNTHETIC_VALUE")):
            self.error("no_space", store.replace, first["revision"], True)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_replaced_temporary_is_not_installed_or_deleted_by_cleanup(self):
        store = self.service.settings
        first = store.snapshot()
        original_read, calls = target._read, [0]
        replaced = []
        def read(path):
            calls[0] += 1
            if calls[0] == 2:
                temporary = next(self.root.glob(".*.tmp"))
                # Hold the removed original inode under a different name to
                # prevent immediate file-ID reuse in this synthetic race.
                displaced = temporary.with_suffix(".displaced")
                temporary.rename(displaced)
                temporary.write_bytes(b"SYNTHETIC_FOREIGN_REPLACEMENT")
                replaced.append((temporary, displaced))
            return original_read(path)
        with patch.object(target, "_read", side_effect=read):
            self.error("unsafe_path", store.replace, first["revision"], True)
        self.assertFalse((self.root / "message-retention-settings.json").exists())
        self.assertEqual(replaced[0][0].read_bytes(), b"SYNTHETIC_FOREIGN_REPLACEMENT")
        self.assertTrue(replaced[0][1].exists())


if __name__ == "__main__":
    unittest.main()
