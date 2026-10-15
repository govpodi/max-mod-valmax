"""Deletion marker SQLite tests use only generated IDs and D: test directories."""
from contextlib import closing, contextmanager
import os
from pathlib import Path
import sqlite3
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import message_retention_store as target


class RetentionStoreTests(unittest.TestCase):
    def setUp(self):
        base = Path("D:/ValMAX/analysis/message-retention-v1")
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="store-test-", dir=base)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "settings" / "deletion-markers.sqlite"
        self.store = target.RetentionMarkerStore(self.path)

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(target.RetentionStoreError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code)

    @contextmanager
    def fixture_connection(self):
        self.path.parent.mkdir(exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            yield connection

    def test_constructor_and_missing_lookup_create_no_files_or_parents(self):
        self.assertEqual(self.store.lookup("1", "-1", []), self.store.lookup("1", "-1", ["1"]))
        self.assertEqual(list(self.root.iterdir()), [])
        self.path.parent.mkdir()
        self.assertEqual(self.store.lookup("1", "-1", ["1"])["deletedIds"], [])
        self.assertEqual(list(self.path.parent.iterdir()), [])
        self.assertEqual(self.store.mark("1", "-1", [], deleted_at_ms=1), 0)
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_explicit_preflight_initializes_an_empty_owned_index_without_markers(self):
        self.assertTrue(self.store.ensure_ready())
        self.assertEqual(self.store.lookup("1", "-1", ["1"])["deletedIds"], [])
        with self.fixture_connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM deleted_messages").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT revision FROM retention_meta").fetchone()[0], 0)
        self.assertTrue(self.store.ensure_ready())

    def test_ids_remain_exact_int64_strings_and_accounts_chats_are_isolated(self):
        account, chat, message = "9223372036854775807", "-9223372036854775808", "9223372036854775807"
        self.assertEqual(self.store.mark(account, chat, [message], deleted_at_ms=1), 1)
        self.assertEqual(self.store.lookup(account, chat, [message])["deletedIds"], [message])
        self.assertEqual(self.store.lookup("1", chat, [message])["deletedIds"], [])
        self.assertEqual(self.store.lookup(account, "1", [message])["deletedIds"], [])
        self.assertEqual(target.RetentionMarkerStore(self.path).lookup(account, chat, [message])["deletedIds"], [message])

    def test_bad_batch_rejected_before_filesystem_changes(self):
        for value in ("0", "-0", "01", "+1", " 1", "1\n", "9223372036854775808",
                      "-9223372036854775809", "１２", None, 1, True):
            with self.subTest(value=value):
                self.error("invalid_request", self.store.mark, value, "-1", ["1"])
                self.error("invalid_request", self.store.mark, "1", "-1", ["1", value])
        for chat in ("0", "-0", "01", "-01", "9223372036854775808"):
            self.error("invalid_request", self.store.lookup, "1", chat, ["1"])
        for values in (("1",), ["1", "1"], ["1"] * 4097):
            self.error("invalid_request", self.store.mark, "1", "-1", values)
        for stamp in (True, -1, 1.0, "1", 1 << 63):
            self.error("invalid_request", self.store.mark, "1", "-1", ["1"], deleted_at_ms=stamp)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_event_and_lookup_limits_and_input_order(self):
        values = [str(number) for number in range(1, 4097)]
        self.assertEqual(self.store.mark("1", "-1", values, deleted_at_ms=0), 4096)
        requested = list(reversed(values[:256]))
        self.assertEqual(self.store.lookup("1", "-1", requested)["deletedIds"], requested)
        self.error("invalid_request", self.store.lookup, "1", "-1", values[:257])
        self.error("invalid_request", self.store.mark, "1", "-1", values + ["4097"])

    def test_duplicate_events_do_not_change_time_or_revision(self):
        self.store.mark("1", "-1", ["1", "2"], deleted_at_ms=10)
        before = self.store.lookup("1", "-1", ["1", "2"])
        self.assertEqual(self.store.mark("1", "-1", ["2", "1"], deleted_at_ms=20), 0)
        self.assertEqual(before, self.store.lookup("1", "-1", ["1", "2"]))
        with self.fixture_connection() as connection:
            self.assertEqual(connection.execute("SELECT DISTINCT deleted_at_ms FROM deleted_messages").fetchall(), [(10,)])

    def test_unrelated_database_and_journal_are_unchanged_on_rejection(self):
        with self.fixture_connection() as connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
            connection.execute("INSERT INTO unrelated VALUES('synthetic')")
        journal = Path(str(self.path) + "-journal")
        journal.write_bytes(b"\0" * 64 + b"SYNTHETIC_UNRELATED_JOURNAL")
        before = self.path.read_bytes(), journal.read_bytes()
        original_connect, statements = sqlite3.connect, []
        class Tracked(sqlite3.Connection):
            def execute(self, sql, *args):
                statements.append(sql)
                return super().execute(sql, *args)
        def connect(*args, **kwargs):
            return original_connect(*args, **kwargs, factory=Tracked)
        with patch.object(target.sqlite3, "connect", side_effect=connect):
            self.error("invalid_store", self.store.lookup, "1", "-1", ["1"])
            self.error("invalid_store", self.store.mark, "1", "-1", ["1"])
        self.assertEqual(before, (self.path.read_bytes(), journal.read_bytes()))
        self.assertFalse(any(sql.startswith(("PRAGMA journal_mode=", "PRAGMA synchronous=", "PRAGMA max_page_count=")) for sql in statements))

    def test_unknown_zero_file_is_not_adopted_and_no_journal_is_created(self):
        self.path.parent.mkdir()
        self.path.write_bytes(b"")
        self.error("invalid_store", self.store.mark, "1", "-1", ["1"])
        self.assertEqual(self.path.read_bytes(), b"")
        self.assertFalse(Path(str(self.path) + "-journal").exists())

    def test_tampered_schema_extra_objects_and_metadata_fail_before_changes(self):
        changes = ("CREATE VIEW extra AS SELECT 1", "CREATE INDEX extra ON deleted_messages(deleted_at_ms)",
                   "UPDATE retention_meta SET revision=1.5", "DELETE FROM retention_meta")
        for index, statement in enumerate(changes):
            with self.subTest(statement=statement):
                path = self.root / f"tampered-{index}.sqlite"
                store = target.RetentionMarkerStore(path)
                store.mark("1", "-1", ["1"], deleted_at_ms=0)
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute(statement)
                before = path.read_bytes()
                self.error("invalid_store", store.mark, "1", "-1", ["2"])
                self.assertEqual(before, path.read_bytes())

    def test_failed_batch_rolls_back_insert_and_revision_together(self):
        self.store.mark("1", "-1", ["1"], deleted_at_ms=1)
        before = self.store.lookup("1", "-1", ["1", "2", "3"])
        original_connect = sqlite3.connect
        class FailedBatch(sqlite3.Connection):
            def executemany(self, sql, parameters):
                super().execute(sql, next(iter(parameters)))
                raise sqlite3.OperationalError("PRIVATE_SYNTHETIC_ERROR")
        def connect(*args, **kwargs):
            return original_connect(*args, **kwargs, **({"factory": FailedBatch} if not kwargs.get("uri") else {}))
        with patch.object(target.sqlite3, "connect", side_effect=connect):
            self.error("invalid_store", self.store.mark, "1", "-1", ["2", "3"], deleted_at_ms=2)
        self.assertEqual(before, self.store.lookup("1", "-1", ["1", "2", "3"]))

    def test_initialization_ddl_is_atomic_on_second_table_failure(self):
        original_connect = sqlite3.connect
        class FailedInit(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql.startswith("CREATE TABLE deleted_messages"):
                    raise sqlite3.OperationalError("PRIVATE_SYNTHETIC_ERROR")
                return super().execute(sql, *args)
        with patch.object(target.sqlite3, "connect", side_effect=lambda *args, **kwargs: original_connect(*args, **kwargs, factory=FailedInit)):
            self.error("invalid_store", self.store.mark, "1", "-1", ["1"])
        with closing(original_connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master").fetchall(), [])
            self.assertEqual(connection.execute("PRAGMA application_id").fetchone()[0], 0)

    def test_revision_overflow_rolls_back_new_markers(self):
        self.store.mark("1", "-1", ["1"], deleted_at_ms=1)
        with self.fixture_connection() as connection:
            connection.execute("UPDATE retention_meta SET revision=?", ((1 << 63) - 1,))
        before = self.store.lookup("1", "-1", ["1", "2"])
        self.error("invalid_store", self.store.mark, "1", "-1", ["2"])
        self.assertEqual(before, self.store.lookup("1", "-1", ["1", "2"]))
        self.assertEqual(self.store.mark("1", "-1", ["1"]), 0)

    def test_abrupt_process_exit_recovers_only_committed_markers(self):
        self.store.mark("1", "-1", ["1"], deleted_at_ms=1)
        before = self.store.lookup("1", "-1", ["1", "10000"])
        code = """
import os, sqlite3, sys
c = sqlite3.connect(sys.argv[1], isolation_level=None)
c.execute('PRAGMA journal_mode=PERSIST')
c.execute('PRAGMA synchronous=FULL')
c.execute('PRAGMA cache_size=2')
c.execute('BEGIN IMMEDIATE')
c.execute('UPDATE retention_meta SET revision=777')
c.executemany('INSERT INTO deleted_messages VALUES(?,?,?,?)', [('1','-1',str(n),2) for n in range(10000,16000)])
os._exit(73)
"""
        environment = dict(os.environ, TEMP=str(self.root), TMP=str(self.root))
        result = subprocess.run([sys.executable, "-c", code, str(self.path)], env=environment,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 73)
        self.assertGreater(Path(str(self.path) + "-journal").stat().st_size, 512)
        self.assertEqual(before, self.store.lookup("1", "-1", ["1", "10000"]))
        self.assertEqual(target.RetentionMarkerStore(self.path).mark("1", "-1", ["2"], deleted_at_ms=3), 1)

    def test_hardlinked_main_and_sidecars_cannot_modify_another_file(self):
        self.path.parent.mkdir()
        other = self.root / "other-file"
        other.write_bytes(b"SYNTHETIC_UNRELATED_FILE")
        os.link(other, self.path)
        self.error("unsafe_path", self.store.lookup, "1", "-1", ["1"])
        self.path.unlink()
        for suffix in ("-journal", "-wal", "-shm"):
            with self.subTest(suffix=suffix):
                sidecar = Path(str(self.path) + suffix)
                os.link(other, sidecar)
                self.error("unsafe_path", self.store.mark, "1", "-1", ["1"])
                self.assertFalse(self.path.exists())
                sidecar.unlink()
        self.assertEqual(other.read_bytes(), b"SYNTHETIC_UNRELATED_FILE")

    def test_mock_dangling_main_link_is_not_an_absent_lookup(self):
        self.path.parent.mkdir()
        original = Path.lstat
        def lstat(path, *args, **kwargs):
            if path == self.path:
                return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_file_attributes=0x400)
            return original(path, *args, **kwargs)
        with patch.object(Path, "lstat", lstat):
            self.error("unsafe_path", self.store.lookup, "1", "-1", ["1"])
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_unexpected_wal_mode_and_sidecars_are_rejected_unchanged(self):
        self.store.mark("1", "-1", ["1"], deleted_at_ms=1)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(self.path) + suffix)
            sidecar.write_bytes(b"SYNTHETIC_SIDECAR")
            before = self.path.read_bytes(), sidecar.read_bytes()
            self.error("invalid_store", self.store.lookup, "1", "-1", ["1"])
            self.assertEqual(before, (self.path.read_bytes(), sidecar.read_bytes()))
            sidecar.unlink()
        with self.fixture_connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
        before = self.path.read_bytes()
        self.assertEqual(before[18:20], b"\x02\x02")
        self.error("invalid_store", self.store.mark, "1", "-1", ["2"])
        self.assertEqual(before, self.path.read_bytes())

    def test_journal_with_super_journal_footer_cannot_follow_external_paths(self):
        self.store.mark("1", "-1", ["1"], deleted_at_ms=1)
        journal = Path(str(self.path) + "-journal")
        name = b"synthetic-outside-super-journal"
        data = target._JOURNAL_MAGIC + b"\0" * 504 + name + struct.pack(">II", len(name), sum(name)) + target._JOURNAL_MAGIC
        journal.write_bytes(data)
        before = self.path.read_bytes(), journal.read_bytes()
        with patch.object(target.sqlite3, "connect", side_effect=AssertionError("SQLite must not inspect an external journal")):
            self.error("invalid_store", self.store.lookup, "1", "-1", ["1"])
        self.assertEqual(before, (self.path.read_bytes(), journal.read_bytes()))

    def test_dangling_symlinks_are_rejected_instead_of_treated_as_missing(self):
        self.path.parent.mkdir()
        try:
            self.path.symlink_to(self.root / "missing-target")
        except OSError as error:
            self.skipTest("Synthetic symlink creation unavailable: " + type(error).__name__)
        self.error("unsafe_path", self.store.lookup, "1", "-1", ["1"])
        self.error("unsafe_path", self.store.mark, "1", "-1", ["1"])
        self.path.unlink()
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = Path(str(self.path) + suffix)
            sidecar.symlink_to(self.root / "missing-target")
            self.error("unsafe_path", self.store.lookup, "1", "-1", ["1"])
            self.assertFalse(self.path.exists())
            sidecar.unlink()


if __name__ == "__main__":
    unittest.main()
