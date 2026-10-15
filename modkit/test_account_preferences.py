"""Synthetic local preferences only; no native profiles or WinCred calls."""
from __future__ import annotations

from datetime import datetime
import copy
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import account_preferences as module
from account_preferences import AccountPreferencesError, AccountPreferencesStore, defaults, is_work_time


def account(rid="42", **changes):
    result = defaults(rid)
    result.update(displayName="Работа · тест", color="#8b5cf6")
    result.update(changes)
    return result


def _process_writer(path, revision, number, barrier, queue):
    try:
        barrier.wait(timeout=8)
        AccountPreferencesStore(path).replace(revision, [account(str(number))])
        queue.put("saved")
    except AccountPreferencesError as error:
        queue.put(error.code)
    except Exception:
        queue.put("test_process_failed")


class AccountPreferencesTests(unittest.TestCase):
    def setUp(self):
        # Keep these fixtures on the project volume rather than the system TEMP.
        self.temp = tempfile.TemporaryDirectory(prefix="account-prefs-test-", dir=Path(__file__).resolve().parent.parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "settings" / "account-settings.json"
        self.store = AccountPreferencesStore(self.path)

    def error(self, code, function, *args):
        with self.assertRaises(AccountPreferencesError) as raised:
            function(*args)
        self.assertEqual(code, raised.exception.code)
        self.assertEqual(code, str(raised.exception))

    def save(self, values=None):
        return self.store.replace(self.store.snapshot()["revision"], [account()] if values is None else values)

    def test_constructor_and_absent_read_create_no_files(self):
        with mock.patch.object(module, "_read", side_effect=AssertionError("read")):
            self.assertEqual([], self.store.snapshot()["accounts"])
            AccountPreferencesStore(self.path)
        expected = hashlib.sha256(b'{"accounts":[],"schema":1}').hexdigest()
        self.assertEqual(expected, self.store.snapshot()["revision"])
        self.assertEqual([], list(self.root.iterdir()))

    def test_round_trip_preserves_unicode_and_defaults(self):
        saved = self.save([account(displayName="ИП 👩‍💻")])
        self.assertEqual(saved, self.store.snapshot())
        raw = self.path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), saved["revision"])
        self.assertIn("ИП".encode(), raw)
        self.assertEqual({"schema": 1, "accounts": saved["accounts"]}, json.loads(raw))
        self.assertEqual({key: False for key in module.GHOST_FIELDS}, saved["accounts"][0]["ghost"])

    def test_caller_and_result_mutations_do_not_affect_stored_values(self):
        rows = [account()]
        result = self.save(rows)
        rows[0]["workHours"]["days"].clear()
        result["accounts"][0]["ghost"]["hideRead"] = True
        self.assertEqual([account()], self.store.snapshot()["accounts"])

    def test_canonical_revision_ignores_whitespace_account_and_day_order(self):
        rows = [account("9"), account("2", workHours={"enabled": True, "days": [5, 1, 3], "start": "09:00", "end": "18:00"})]
        saved = self.save(rows)
        self.path.write_text(json.dumps({"schema": 1, "accounts": list(reversed(rows))}, ensure_ascii=False, indent=2), encoding="utf8")
        self.assertEqual(saved, self.store.snapshot())
        self.assertEqual(["2", "9"], [row["accountId"] for row in saved["accounts"]])
        self.assertEqual([1, 3, 5], saved["accounts"][0]["workHours"]["days"])

    def test_stale_revision_cannot_replace_newer_preference(self):
        revision = self.store.snapshot()["revision"]
        saved = self.save()
        before = self.path.read_bytes()
        self.error("revision_conflict", self.store.replace, revision, [account("7")])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(saved, self.store.snapshot())

    def test_invalid_revision_and_rows_do_not_create_directory(self):
        for value in (None, True, 7, "", "a" * 63, "A" * 64):
            self.error("invalid_revision", self.store.replace, value, [])
        for rid in (None, True, 7, "0", "01", "-1", "+1", "1\n", "9223372036854775808", "9" * 20):
            row = account(); row["accountId"] = rid
            self.error("invalid_accounts", self.store.replace, "0" * 64, [row])
        self.assertFalse(self.path.parent.exists())

    def test_name_palette_and_reserved_ghost_schema_are_bounded(self):
        cases = [account(displayName="x" * 65), account(displayName="hello\nworld"), account(displayName="\x00"),
                 account(displayName="\ud800"), account(displayName="\u202eevil"), account(color="#0088FF"),
                 account(color="#123456"), account(color=None), account(ghost={}), account(ghost=dict(defaults("1")["ghost"], hideRead=1)),
                 dict(account(), credential="must not be accepted")]
        for row in cases:
            self.error("invalid_accounts", self.store.replace, "0" * 64, [row])
        rows = [account(displayName="я" * 64, color="")]
        rows[0]["ghost"]["hideRead"] = True  # Stored preference only; network behaviour is independent.
        self.assertEqual(rows, self.save(rows)["accounts"])

    def test_empty_alias_and_signed64_maximum_id_are_supported(self):
        values = [account("9223372036854775807", displayName="", color="")]
        self.assertEqual(values, self.save(values)["accounts"])

    def test_duplicate_accounts_and_more_than_thirty_two_rows_fail(self):
        for rows in ([account(), account()], [account(str(i)) for i in range(1, 34)]):
            self.error("invalid_accounts", self.store.replace, "0" * 64, rows)
        self.assertEqual(32, len(self.save([account(str(i)) for i in range(1, 33)])["accounts"]))

    def test_workhours_reject_invalid_days_times_and_ambiguous_full_day(self):
        base = {"enabled": True, "days": [1, 2], "start": "09:00", "end": "18:00"}
        cases = [{"days": []}, {"days": [0]}, {"days": [8]}, {"days": [True]}, {"days": [1, 1]}, {"days": [1.0]},
                 {"days": tuple([1])}, {"start": "9:00"}, {"start": "24:00"}, {"end": "18:60"}, {"end": "18:00\n"},
                 {"start": None}, {"enabled": 1}, {"end": "09:00"}, {"timezone": "UTC"}]
        for change in cases:
            self.error("invalid_accounts", self.store.replace, "0" * 64, [account(workHours=dict(base, **change))])
        self.assertFalse(self.path.parent.exists())

    def test_disabled_schedule_can_have_no_workdays(self):
        row = account(); row["workHours"]["days"] = []
        self.assertEqual([row], self.save([row])["accounts"])

    def test_malformed_existing_json_is_never_overwritten(self):
        self.path.parent.mkdir()
        valid = json.dumps({"schema": 1, "accounts": [account()]}).encode()
        cases = [b"", b"{", b"\xff", b'{"schema":NaN,"accounts":[]}', b'{"schema":1,"schema":1,"accounts":[]}',
                 b'{"schema":true,"accounts":[]}', b'{"schema":2,"accounts":[]}', b'{"schema":1,"accounts":[],"secret":0}',
                 valid.replace(b'"displayName":', b'"displayName":"duplicate","displayName":'),
                 b"[" * 3000 + b"]" * 3000, b" " * (module.MAX_STORE_BYTES + 1)]
        for raw in cases:
            self.path.write_bytes(raw)
            self.error("invalid_store", self.store.snapshot)
            self.error("invalid_store", self.store.replace, "0" * 64, [])
            self.assertEqual(raw, self.path.read_bytes())

    def test_links_and_directory_destination_are_rejected(self):
        self.path.parent.mkdir()
        target = self.root / "target.json"; target.write_bytes(b"synthetic target")
        os.link(target, self.path)
        self.error("unsafe_path", self.store.snapshot)
        self.error("unsafe_path", self.store.replace, "0" * 64, [])
        self.assertEqual(b"synthetic target", target.read_bytes())
        self.path.unlink(); self.path.mkdir()
        self.error("unsafe_path", self.store.snapshot)

    def test_unsafe_constructor_paths_fail(self):
        values = ["relative.json", self.root / ".." / "outside.json", str(self.root / "x.json") + " ", str(self.root / "x.json") + "\x00"]
        if os.name == "nt": values.extend((str(self.root / "x.json:stream"), str(self.root / "NUL"), "\\\\localhost\\share\\x.json"))
        for value in values:
            self.error("unsafe_path", AccountPreferencesStore, value)

    def test_replace_and_fsync_failures_keep_original_and_clean_owned_temp(self):
        saved = self.save(); before = self.path.read_bytes()
        for function in ("replace", "fsync"):
            with mock.patch.object(module.os, function, side_effect=OSError("synthetic private value")):
                self.error("io_error", self.store.replace, saved["revision"], [account("7")])
            self.assertEqual(before, self.path.read_bytes())
            self.assertEqual([], list(self.path.parent.glob("*.tmp")))

    def test_exclusive_temporary_creation_preserves_existing_object(self):
        saved = self.save(); before = self.path.read_bytes()
        temporary = self.path.with_name("." + self.path.name + "." + "a" * 32 + ".tmp")
        temporary.write_bytes(b"already exists")
        with mock.patch.object(module.uuid, "uuid4", return_value=mock.Mock(hex="a" * 32)):
            self.error("io_error", self.store.replace, saved["revision"], [account("7")])
        self.assertEqual(b"already exists", temporary.read_bytes())
        self.assertEqual(before, self.path.read_bytes())

    def test_stream_creation_failure_closes_descriptor_and_cleans_own_file(self):
        saved = self.save(); before = self.path.read_bytes()
        original_fdopen = module.os.fdopen
        def fail_write_stream(fd, mode, *args, **kwargs):
            if mode == "wb": raise OSError("synthetic fdopen failure")
            return original_fdopen(fd, mode, *args, **kwargs)
        with mock.patch.object(module.os, "fdopen", side_effect=fail_write_stream):
            self.error("io_error", self.store.replace, saved["revision"], [account("7")])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual([], list(self.path.parent.glob("*.tmp")))

    def test_replaced_temporary_is_not_published_or_deleted(self):
        saved = self.save(); before = self.path.read_bytes()
        temporary = self.path.with_name("." + self.path.name + "." + "a" * 32 + ".tmp")
        displaced = self.root / "own-displaced.tmp"
        original_read = module._read
        reads = []
        def replace_temporary(path):
            value = original_read(path); reads.append(1)
            if len(reads) == 2:
                temporary.rename(displaced)
                temporary.write_bytes(b"another writer owns this")
            return value
        with mock.patch.object(module.uuid, "uuid4", return_value=mock.Mock(hex="a" * 32)), mock.patch.object(module, "_read", side_effect=replace_temporary):
            self.error("unsafe_path", self.store.replace, saved["revision"], [account("7")])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(b"another writer owns this", temporary.read_bytes())

    def test_changed_destination_is_checked_before_publication(self):
        saved = self.save()
        with mock.patch.object(module.os, "fsync", side_effect=lambda _fd: self.path.write_bytes(b"invalid synthetic replacement")):
            self.error("invalid_store", self.store.replace, saved["revision"], [account("7")])
        self.assertEqual(b"invalid synthetic replacement", self.path.read_bytes())
        self.assertEqual([], list(self.path.parent.glob("*.tmp")))

    def test_cross_process_writer_lock_prevents_lost_update(self):
        revision = self.save([])["revision"]
        ctx = multiprocessing.get_context("spawn"); barrier, queue = ctx.Barrier(2), ctx.Queue()
        processes = [ctx.Process(target=_process_writer, args=(str(self.path), revision, i, barrier, queue)) for i in (1, 2)]
        try:
            for process in processes: process.start()
            self.assertCountEqual(["saved", "revision_conflict"], [queue.get(timeout=15), queue.get(timeout=15)])
            for process in processes:
                process.join(timeout=5); self.assertEqual(0, process.exitcode)
            self.assertEqual(1, len(self.store.snapshot()["accounts"]))
        finally:
            for process in processes:
                if process.is_alive(): process.terminate(); process.join(timeout=5)
            queue.close()

    def test_errors_never_include_preference_values(self):
        with self.assertRaises(ValueError): AccountPreferencesError("synthetic alias")
        self.error("invalid_accounts", self.store.replace, "0" * 64, [account(displayName="synthetic alias\n")])


class WorkHoursTests(unittest.TestCase):
    def setUp(self):
        self.hours = {"enabled": True, "days": [1, 2, 3, 4, 5], "start": "09:00", "end": "18:00"}

    def test_start_inclusive_end_exclusive_and_weekend(self):
        for stamp, active in (("2026-10-12T08:59", False), ("2026-10-12T09:00", True), ("2026-10-12T17:59", True),
                              ("2026-10-12T18:00", False), ("2026-10-17T12:00", False)):
            self.assertEqual(active, is_work_time(self.hours, datetime.fromisoformat(stamp)))

    def test_overnight_window_belongs_to_start_day_including_week_wrap(self):
        hours = dict(self.hours, days=[7], start="22:00", end="06:00")
        for stamp, active in (("2026-10-18T21:59", False), ("2026-10-18T22:00", True), ("2026-10-19T05:59", True),
                              ("2026-10-19T06:00", False), ("2026-10-18T05:00", False), ("2026-10-19T23:00", False)):
            self.assertEqual(active, is_work_time(hours, datetime.fromisoformat(stamp)))

    def test_disabled_schedule_never_mutes(self):
        self.assertTrue(is_work_time(dict(self.hours, enabled=False, days=[]), datetime(2026, 10, 18, 23, 59)))


if __name__ == "__main__":
    unittest.main()
