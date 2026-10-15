"""Received-ID ledger and helper delivery tests; synthetic IDs and D: temp only."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import message_retention_service as service_module
import message_retention_store as store_module


def received(**changes):
    return {"kind": "received-messages", "schema": 1, "accountId": "11",
            "chatId": "-21", "messageIds": ["31", "32"], **changes}


def lookup(**changes):
    return {"schema": 1, "accountId": "11", "chatId": "-21",
            "messageIds": ["31", "32"], **changes}


def tuples(records):
    return {(row["accountId"], row["chatId"], message)
            for row in records for message in row["messageIds"]}


class OwnedTemporaryTest(unittest.TestCase):
    def setUp(self):
        base = Path("D:/ValMAX/analysis/message-retention-v1/received-tests")
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="synthetic-", dir=base)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def store_error(self, code, function, *args):
        with self.assertRaises(store_module.RetentionStoreError) as raised:
            function(*args)
        self.assertEqual((raised.exception.code, str(raised.exception)), (code, code))

    def service_error(self, code, function, *args):
        with self.assertRaises(service_module.RetentionServiceError) as raised:
            function(*args)
        self.assertEqual((raised.exception.code, str(raised.exception)), (code, code))


class ReceivedLedgerTests(OwnedTemporaryTest):
    def setUp(self):
        super().setUp()
        self.path = self.root / "private" / "received.sqlite3"
        self.store = store_module.ReceivedMessageStore(self.path)

    def test_missing_seed_and_empty_remember_have_no_file_side_effects(self):
        self.assertEqual(self.store.seed(), [])
        self.assertEqual(self.store.remember("11", "-21", []), 0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_exact_int64_restart_and_account_chat_isolation(self):
        account, chat = "9223372036854775807", "-9223372036854775808"
        messages = ["9007199254740993", "9223372036854775807"]
        self.assertEqual(self.store.remember(account, chat, messages), 2)
        self.assertEqual(self.store.remember("11", chat, [messages[0]]), 1)
        self.assertEqual(self.store.remember(account, "21", [messages[0]]), 1)
        expected = {(account, chat, value) for value in messages}
        expected |= {("11", chat, messages[0]), (account, "21", messages[0])}
        self.assertEqual(tuples(store_module.ReceivedMessageStore(self.path).seed()), expected)
        self.assertTrue(all(type(part) is str for row in expected for part in row))

    def test_duplicates_are_idempotent_without_collapsing_other_routes(self):
        self.assertEqual(self.store.remember("11", "-21", ["31", "32"]), 2)
        before = self.store.seed()
        self.assertEqual(self.store.remember("11", "-21", ["32", "31"]), 0)
        self.assertEqual(self.store.seed(), before)
        self.assertEqual(self.store.remember("12", "-21", ["31"]), 1)
        self.assertEqual(self.store.remember("11", "22", ["31"]), 1)
        self.assertEqual(len(tuples(self.store.seed())), 4)

    def test_invalid_receipts_fail_before_creating_files(self):
        invalid = ("0", "-1", "01", "+1", " 1", "1\n", "9223372036854775808",
                   "１２", None, 1, True, [], {})
        for value in invalid:
            with self.subTest(value=type(value).__name__):
                self.store_error("invalid_request", self.store.remember, value, "-21", ["31"])
                self.store_error("invalid_request", self.store.remember, "11", "-21", [value])
        for chat in ("0", "-0", "-01", "-9223372036854775809", True):
            self.store_error("invalid_request", self.store.remember, "11", chat, ["31"])
        for identifiers in (["31", "31"], ("31",), [str(i) for i in range(1, 4098)]):
            self.store_error("invalid_request", self.store.remember, "11", "-21", identifiers)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_capacity_overflow_rolls_back_the_entire_batch(self):
        with patch.object(store_module, "MAX_KNOWN_IDS", 3):
            self.store.remember("11", "-21", ["31", "32"])
            self.store_error("known_capacity", self.store.remember, "11", "-21", ["32", "33", "34"])
            self.assertEqual(tuples(self.store.seed()), {("11", "-21", "31"), ("11", "-21", "32")})
            self.assertEqual(self.store.remember("11", "-21", ["33"]), 1)
            self.assertEqual(len(tuples(self.store.seed())), 3)

    def test_seed_rejects_a_persisted_capacity_overflow(self):
        self.store.remember("11", "-21", ["31", "32", "33", "34"])
        with patch.object(store_module, "MAX_KNOWN_IDS", 3):
            self.store_error("known_capacity", self.store.seed)

    def test_seed_batches_have_at_most_256_ids_and_never_mix_routes(self):
        ids = [str(i) for i in range(1, 258)]
        self.store.remember("11", "-21", ids)
        self.store.remember("12", "21", ["1"])
        records = self.store.seed()
        self.assertEqual(sorted(len(row["messageIds"]) for row in records), [1, 1, 256])
        self.assertEqual(tuples(records), {("11", "-21", i) for i in ids} | {("12", "21", "1")})

    def test_received_and_marker_databases_are_not_mixed_or_migrated(self):
        marker_path = self.root / "markers.sqlite3"
        marker = store_module.RetentionMarkerStore(marker_path)
        marker.mark("11", "-21", ["31"], deleted_at_ms=1)
        self.store.remember("11", "-21", ["32"])
        for path, wrong in ((marker_path, store_module.ReceivedMessageStore(marker_path)),
                            (self.path, store_module.RetentionMarkerStore(self.path))):
            before = {p: p.read_bytes() for p in (path, Path(str(path) + "-journal"))}
            method, args = (wrong.seed, ()) if hasattr(wrong, "seed") else (wrong.lookup, ("11", "-21", ["32"]))
            self.store_error("invalid_store", method, *args)
            self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(marker.lookup("11", "-21", ["31", "32"])["deletedIds"], ["31"])
        self.assertEqual(tuples(self.store.seed()), {("11", "-21", "32")})

    def test_unrelated_sqlite_file_is_rejected_unchanged(self):
        self.path.parent.mkdir()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TABLE unrelated (id TEXT)")
            connection.execute("INSERT INTO unrelated VALUES('SYNTHETIC')")
        before = self.path.read_bytes()
        self.store_error("invalid_store", self.store.seed)
        self.store_error("invalid_store", self.store.remember, "11", "-21", ["31"])
        self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(Path(str(self.path) + "-journal").exists())

    def test_tampered_stored_ids_are_invalid_store(self):
        invalid_rows = [("01", "-21", "31"), ("11", "0", "31"),
                        ("11", "-21", "9223372036854775808"), ("11", "-21", b"31")]
        for index, row in enumerate(invalid_rows):
            path = self.root / ("tampered-" + str(index) + ".sqlite3")
            store = store_module.ReceivedMessageStore(path)
            store.ensure_ready()
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("INSERT INTO received_messages VALUES(?,?,?)", row)
            with self.subTest(index=index):
                self.store_error("invalid_store", store.seed)

    def test_receipt_schema_has_its_own_application_identity_and_no_content_columns(self):
        self.store.ensure_ready()
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA application_id").fetchone()[0], 0x56414C4B)
            self.assertNotEqual(store_module.APPLICATION_ID, store_module.RECEIVED_APPLICATION_ID)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual([row[1] for row in connection.execute("PRAGMA table_info(received_messages)")],
                             ["account_id", "chat_id", "message_id"])


class ReceivedServiceTests(OwnedTemporaryTest):
    def setUp(self):
        super().setUp()
        self.service = service_module.MessageRetentionService(self.root)

    def enabled_queue(self, *pids):
        self.service.settings.snapshot = Mock(return_value=service_module._snapshot(True))
        self.service.markers.ensure_ready = Mock(return_value=True)
        self.service.received.ensure_ready = Mock(return_value=True)
        self.service.received.seed = Mock(return_value=[])
        self.service.received.remember = Mock(return_value=1)
        self.service.preflight_marker_store()
        for pid in pids:
            self.service.register(pid)

    def test_seed_is_restored_on_restart_with_exact_routes(self):
        self.service.received.remember("9007199254740993", "-9223372036854775808", ["9223372036854775807"])
        self.service.received.remember("12", "21", ["31"])
        restarted = service_module.MessageRetentionService(self.root)
        self.service_error("runtime_unavailable", restarted.known_messages)
        restarted.preflight_marker_store()
        self.assertEqual(tuples(restarted.known_messages()),
                         {("9007199254740993", "-9223372036854775808", "9223372036854775807"), ("12", "21", "31")})
        restarted.register(123)
        self.assertEqual(restarted.pending_known(123)[0]["records"], [])

    def test_off_lookup_and_native_receipts_do_not_create_or_remember_any_ids(self):
        self.service.register(123)
        self.service.received.remember = Mock(side_effect=AssertionError("Receipt write while capture is off"))
        self.assertFalse(self.service.lookup(lookup())["enabled"])
        self.service.record_received(123, received())
        self.assertEqual(self.service._known, set())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_malformed_native_events_are_rejected_even_when_capture_is_off(self):
        self.service.register(123)
        malformed = [received(accountId=[]), received(accountId="01"), received(chatId="0"),
                     received(messageIds=["31", "31"]), received(messageIds=[True]),
                     received(messageIds=["9223372036854775808"]), received(messageIds=("31",)),
                     received(schema=True), received(kind="retained-delete"), received(text="SYNTHETIC")]
        for payload in malformed:
            self.service_error("invalid_request", self.service.record_received, 123, payload)
        self.assertTrue(self.service.storage_available())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_only_registered_integer_pids_can_publish_receipts(self):
        self.enabled_queue(123)
        for pid in (None, [], {}, True, "123", -1, 0, 1 << 32, 456):
            self.service_error("invalid_request", self.service.record_received, pid, received())
        self.service.received.remember.assert_not_called()
        self.assertTrue(self.service.storage_available())

    def test_enabled_lookup_seeds_receipts_without_creating_deletion_markers(self):
        self.service.preflight_marker_store()
        self.service.settings.replace(self.service.desired()["revision"], True)
        result = self.service.lookup(lookup())
        self.assertEqual(result["deletedIds"], [])
        self.assertEqual(tuples(self.service.known_messages()), {("11", "-21", "31"), ("11", "-21", "32")})
        self.assertEqual(self.service.markers.lookup("11", "-21", ["31", "32"])["deletedIds"], [])
        restarted = service_module.MessageRetentionService(self.root)
        restarted.preflight_marker_store()
        self.assertEqual(restarted.known_messages(), self.service.known_messages())

    def test_duplicate_receipts_are_persisted_and_queued_once(self):
        self.enabled_queue(123)
        self.service.record_received(123, received())
        first = self.service.pending_known(123)
        self.service.record_received(123, received(messageIds=["32", "31"]))
        self.assertEqual(self.service.pending_known(123), first)
        self.service.received.remember.assert_called_once_with("11", "-21", ["31", "32"])

    def test_delivery_retries_and_acknowledgements_are_independent_per_pid(self):
        self.enabled_queue(123, 456)
        self.service.record_received(123, received())
        first, cursor = self.service.pending_known(123)
        self.assertEqual(self.service.pending_known(123), (first, cursor))
        self.assertEqual(self.service.pending_known(456), (first, cursor))
        self.service.acknowledge_known(123, cursor)
        self.assertEqual(self.service.pending_known(123)[0]["records"], [])
        self.assertEqual(self.service.pending_known(456), (first, cursor))
        self.service.acknowledge_known(456, cursor)
        self.assertEqual(self.service.pending_known(456)[0]["records"], [])

    def test_pending_delivery_is_bounded_to_4096_ids(self):
        self.enabled_queue(123)
        for start in range(1, 4353, 256):
            self.service.record_received(123, received(messageIds=[str(i) for i in range(start, start + 256)]))
        body, cursor = self.service.pending_known(123)
        self.assertEqual((len(tuples(body["records"])), cursor), (4096, 4096))
        self.assertTrue(all(len(row["messageIds"]) <= 256 for row in body["records"]))
        self.service.acknowledge_known(123, cursor)
        second, second_cursor = self.service.pending_known(123)
        self.assertEqual((len(tuples(second["records"])), second_cursor), (256, 4352))
        self.assertFalse(tuples(body["records"]) & tuples(second["records"]))

    def test_pending_delivery_is_bounded_to_256_distinct_route_records(self):
        self.enabled_queue(123)
        for chat in range(1, 301):
            self.service.record_received(123, received(chatId=str(chat), messageIds=["31"]))
        body, cursor = self.service.pending_known(123)
        self.assertEqual((len(body["records"]), cursor), (256, 256))
        self.service.acknowledge_known(123, cursor)
        remaining, end = self.service.pending_known(123)
        self.assertEqual((len(remaining["records"]), end), (44, 300))
        self.assertEqual(len(tuples(body["records"]) | tuples(remaining["records"])), 300)

    def test_new_receipts_during_delivery_are_not_lost_after_the_old_ack(self):
        self.enabled_queue(123)
        self.service.record_received(123, received(messageIds=["31"]))
        _, old_cursor = self.service.pending_known(123)
        self.service.record_received(123, received(messageIds=["32"]))
        self.service.acknowledge_known(123, old_cursor)
        self.assertEqual(tuples(self.service.pending_known(123)[0]["records"]), {("11", "-21", "32")})

    def test_returned_delivery_objects_cannot_mutate_host_ledger(self):
        self.enabled_queue(123)
        self.service.record_received(123, received())
        body, cursor = self.service.pending_known(123)
        body["records"][0]["messageIds"].append("999")
        body["records"][0]["accountId"] = "999"
        self.assertEqual(tuples(self.service.pending_known(123)[0]["records"]),
                         {("11", "-21", "31"), ("11", "-21", "32")})
        known = self.service.known_messages()
        known.clear()
        self.assertEqual(len(tuples(self.service.known_messages())), cursor)

    def test_invalid_acknowledgements_do_not_advance_other_clients(self):
        self.enabled_queue(123)
        self.service.record_received(123, received())
        before = self.service.pending_known(123)
        for cursor in (None, True, 1.0, "1", -1, 3):
            self.service_error("invalid_request", self.service.acknowledge_known, 123, cursor)
        self.service_error("invalid_request", self.service.acknowledge_known, 456, 1)
        self.assertEqual(self.service.pending_known(123), before)
        self.service.remove(123)
        self.service_error("runtime_unavailable", self.service.pending_known, 123)

    def test_receipt_write_failure_latches_capture_closed_without_partial_memory_updates(self):
        self.enabled_queue(123)
        self.service.received.remember.side_effect = store_module.RetentionStoreError("no_space")
        self.service_error("no_space", self.service.record_received, 123, received())
        self.assertFalse(self.service.storage_available())
        self.assertEqual(self.service._known, set())
        self.assertEqual(self.service._known_updates, [])
        self.service_error("runtime_unavailable", self.service.pending_known, 123)
        self.assertEqual(self.service.snapshot()["runtime"]["error"], "storage_unavailable")

    def test_memory_capacity_failure_is_latched_before_any_extra_persistence(self):
        self.enabled_queue(123)
        with patch.object(service_module, "MAX_KNOWN_IDS", 3):
            self.service.record_received(123, received())
            self.service_error("known_capacity", self.service.record_received, 123, received(messageIds=["33", "34"]))
        self.service.received.remember.assert_called_once()
        self.assertEqual(self.service._known, {("11", "-21", "31"), ("11", "-21", "32")})
        self.assertFalse(self.service.storage_available())

    def test_corrupt_persisted_receipt_seed_latches_preflight_closed(self):
        self.service.received.ensure_ready()
        with closing(sqlite3.connect(self.service.received.path)) as connection, connection:
            connection.execute("INSERT INTO received_messages VALUES('01','-21','31')")
        self.service_error("invalid_store", self.service.preflight_marker_store)
        self.assertFalse(self.service.storage_available())
        self.service_error("runtime_unavailable", self.service.known_messages)
        self.assertFalse(self.service._known_loaded)


if __name__ == "__main__":
    unittest.main()
