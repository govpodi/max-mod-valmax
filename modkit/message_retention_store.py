"""Private feature indexes; never open the native message database.

Only account/chat/message IDs and deletion times enter the marker index.
The separate received-ID ledger contains routing IDs only. Original
message bodies and media remain in the native profile, outside this module.
Construction and a missing lookup have no filesystem side effects.
"""
from contextlib import contextmanager, ExitStack
import ctypes
import errno
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

from launch_mod import _check_path, _config_stream, _windows_api, _validate_handle
from quick_reply_store import QuickReplyStore, QuickReplyStoreError, _writer_lock

MAX_LOOKUP_IDS = 256
MAX_EVENT_IDS = 4096
MAX_KNOWN_IDS = 65536
APPLICATION_ID = 0x56414C52
RECEIVED_APPLICATION_ID = 0x56414C4B
MAX_STORE_BYTES = 128 * 1024 * 1024
MAX_JOURNAL_BYTES = MAX_STORE_BYTES + 1024 * 1024
_JOURNAL_MAGIC = b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7"
_ID = re.compile(r"-?[1-9][0-9]{0,18}\Z")
_CODES = frozenset({"invalid_request", "invalid_store", "unsafe_path", "store_busy", "io_error", "no_space", "known_capacity"})
_TABLES = {
    "retention_meta": "CREATE TABLE retention_meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL CHECK(revision>=0))",
    "deleted_messages": "CREATE TABLE deleted_messages (account_id TEXT NOT NULL, chat_id TEXT NOT NULL, message_id TEXT NOT NULL, deleted_at_ms INTEGER NOT NULL CHECK(deleted_at_ms>=0), PRIMARY KEY(account_id,chat_id,message_id)) WITHOUT ROWID",
}
_RECEIVED_TABLES = {
    "retention_meta": _TABLES["retention_meta"],
    "received_messages": "CREATE TABLE received_messages (account_id TEXT NOT NULL, chat_id TEXT NOT NULL, message_id TEXT NOT NULL, PRIMARY KEY(account_id,chat_id,message_id)) WITHOUT ROWID",
}


class RetentionStoreError(Exception):
    def __init__(self, code):
        if code not in _CODES:
            raise ValueError("Unknown retention-store error code")
        self.code = code
        super().__init__(code)


def _fail(code):
    raise RetentionStoreError(code)


def canonical_id(value, *, signed=False):
    if type(value) is not str or not _ID.fullmatch(value):
        _fail("invalid_request")
    number = int(value)
    if not -(1 << 63) <= number <= (1 << 63) - 1 or not signed and number <= 0:
        _fail("invalid_request")
    return value


def validate_ids(values, *, limit=MAX_LOOKUP_IDS):
    if type(values) is not list or len(values) > limit:
        _fail("invalid_request")
    result = [canonical_id(value) for value in values]
    if len(set(result)) != len(result):
        _fail("invalid_request")
    return result


def _revision(number):
    return hashlib.sha256(("valmax-retention-v1:" + str(number)).encode("ascii")).hexdigest()


@contextmanager
def _pin_file(path):
    """Keep the SQLite file identity pinned while allowing SQLite to write.

    The parent guard comes from QuickReplyStore. No delete sharing means the
    main file and persistent rollback journal cannot be replaced during use.
    """
    _check_path(path, directory=False)
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                _fail("unsafe_path")
            yield info.st_size
        finally:
            os.close(descriptor)
        return
    api = _windows_api()
    handle = api.CreateFileW(str(path), 0x81, 3, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        _validate_handle(api, handle, path, directory=False)
        class StandardInfo(ctypes.Structure):
            _fields_ = [("allocation", ctypes.c_longlong), ("size", ctypes.c_longlong),
                        ("links", ctypes.c_ulong), ("delete_pending", ctypes.c_ubyte),
                        ("directory", ctypes.c_ubyte)]
        info = StandardInfo()
        if not api.GetFileInformationByHandleEx(handle, 1, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.links != 1 or info.directory or info.delete_pending:
            _fail("unsafe_path")
        yield info.size
    finally:
        api.CloseHandle(handle)


def _create_empty(path):
    try:
        with _config_stream(path, create=True) as stream:
            stream.flush()
            os.fsync(stream.fileno())
        return True
    except FileExistsError:
        _check_path(path, directory=False)
        return False


def _present(path):
    """Unlike exists(), lstat distinguishes a missing file from a dangling link."""
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    _check_path(path, directory=False)
    return True


def _validate_database(connection, *, application_id=APPLICATION_ID, tables=None):
    """Read-only ownership check, before any file-changing PRAGMA or DDL."""
    application = connection.execute("PRAGMA application_id").fetchone()[0]
    objects = list(connection.execute("SELECT type,name,sql FROM sqlite_master"))
    tables = _TABLES if tables is None else tables
    expected = sorted(("table", name, statement) for name, statement in tables.items())
    if application != application_id or sorted(objects) != expected:
        _fail("invalid_store")
    if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
        _fail("invalid_store")
    rows = list(connection.execute("SELECT singleton,revision FROM retention_meta LIMIT 2"))
    if (len(rows) != 1 or rows[0][0] != 1 or type(rows[0][1]) is not int
            or not 0 <= rows[0][1] < (1 << 63)):
        _fail("invalid_store")
    return rows[0][1]


def _validate_journal(path, size):
    if not 0 <= size <= MAX_JOURNAL_BYTES:
        _fail("invalid_store")
    with _config_stream(path, create=False) as stream:
        header = stream.read(8)
        if header and header not in (b"\0" * 8, _JOURNAL_MAGIC):
            _fail("invalid_store")
        if size >= 16:
            stream.seek(-8, os.SEEK_END)
            if stream.read(8) == _JOURNAL_MAGIC:
                # This index never ATTACHes another database. A super-journal
                # footer could make recovery open a path outside this store.
                _fail("invalid_store")


@contextmanager
def _transaction(connection):
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    finally:
        # A bad batch or failed commit must never leave a partially applied
        # marker event, including when the caller raises between statements.
        if connection.in_transaction:
            connection.rollback()


class _IndexStore:
    _application_id = APPLICATION_ID
    _tables = _TABLES

    def __init__(self, path):
        try:
            self._guard = QuickReplyStore(path)
        except QuickReplyStoreError as error:
            _fail(error.code if error.code in _CODES else "io_error")
        self.path = self._guard.path

    @contextmanager
    def _database(self, *, create):
        try:
            with self._guard._operation(create=create) as path:
                if path is None:
                    yield None
                    return
                if not create and not _present(path):
                    for suffix in ("-journal", "-wal", "-shm"):
                        if _present(Path(str(path) + suffix)):
                            _fail("invalid_store")
                    yield None
                    return
                with _writer_lock(path), ExitStack() as held:
                    exists = _present(path)
                    journal = Path(str(path) + "-journal")
                    journal_exists = _present(journal)
                    for suffix in ("-wal", "-shm"):
                        if _present(Path(str(path) + suffix)):
                            _fail("invalid_store")
                    if not exists:
                        if journal_exists:
                            _fail("invalid_store")
                        if not create:
                            yield None
                            return
                    created = _create_empty(path) if create and not exists else False
                    size = held.enter_context(_pin_file(path))
                    if not 0 <= size <= MAX_STORE_BYTES:
                        _fail("invalid_store")
                    if journal_exists:
                        journal_size = held.enter_context(_pin_file(journal))
                        _validate_journal(journal, journal_size)
                    if not created:
                        with _config_stream(path, create=False) as stream:
                            header = stream.read(100)
                        if (len(header) != 100 or header[:16] != b"SQLite format 3\0"
                                or header[18:20] != b"\x01\x01"):
                            _fail("invalid_store")
                        # immutable avoids both journal recovery and schema
                        # writes on an unrelated SQLite file. It is used only
                        # for ownership validation, never to serve marker data.
                        probe = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=2)
                        try:
                            probe.enable_load_extension(False)
                            probe.execute("PRAGMA trusted_schema=OFF")
                            _validate_database(probe, application_id=self._application_id, tables=self._tables)
                        finally:
                            probe.close()
                    # PERSIST keeps rollback journaling crash-safe without
                    # deleting/replacing a sidecar file behind our pinned handle.
                    if not journal_exists:
                        _create_empty(journal)
                        journal_size = held.enter_context(_pin_file(journal))
                        _validate_journal(journal, journal_size)
                    connection = sqlite3.connect(str(path), timeout=2, isolation_level=None)
                    try:
                        connection.enable_load_extension(False)
                        connection.execute("PRAGMA trusted_schema=OFF")
                        # Set pager locking before schema access/recovery. In
                        # exclusive mode SQLite zeroes a recovered journal
                        # instead of unlinking it behind our pinned handle.
                        connection.execute("PRAGMA locking_mode=EXCLUSIVE")
                        connection.execute("PRAGMA journal_mode=PERSIST")
                        connection.execute("PRAGMA synchronous=FULL")
                        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
                        if type(page_size) is not int or not 512 <= page_size <= 65536:
                            _fail("invalid_store")
                        connection.execute("PRAGMA max_page_count=" + str(MAX_STORE_BYTES // page_size))
                        if created:
                            with _transaction(connection):
                                for statement in self._tables.values():
                                    connection.execute(statement)
                                connection.execute("INSERT INTO retention_meta VALUES(1,0)")
                                connection.execute("PRAGMA application_id=" + str(self._application_id))
                                connection.execute("PRAGMA user_version=1")
                        _validate_database(connection, application_id=self._application_id, tables=self._tables)
                        yield connection
                    finally:
                        if connection.in_transaction:
                            connection.rollback()
                        connection.close()
        except RetentionStoreError:
            raise
        except QuickReplyStoreError as error:
            original = error.__context__
            if (error.code == "io_error" and isinstance(original, OSError)
                    and (original.errno == errno.ENOSPC or getattr(original, "winerror", None) == 112)):
                _fail("no_space")
            _fail(error.code if error.code in _CODES else "io_error")
        except sqlite3.Error as error:
            code = getattr(error, "sqlite_errorcode", 0) & 255
            _fail("no_space" if code == sqlite3.SQLITE_FULL else
                  "store_busy" if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) else "invalid_store")
        except ValueError:
            _fail("unsafe_path")
        except OSError as error:
            _fail("no_space" if error.errno == errno.ENOSPC or getattr(error, "winerror", None) == 112 else "io_error")

    def ensure_ready(self):
        """Explicitly create/validate this private index, without marker rows."""
        with self._database(create=True):
            return True


class RetentionMarkerStore(_IndexStore):
    def mark(self, account_id, chat_id, message_ids, *, deleted_at_ms=None):
        account_id, chat_id = canonical_id(account_id), canonical_id(chat_id, signed=True)
        message_ids = validate_ids(message_ids, limit=MAX_EVENT_IDS)
        stamp = int(time.time() * 1000) if deleted_at_ms is None else deleted_at_ms
        if type(stamp) is not int or not 0 <= stamp < (1 << 63):
            _fail("invalid_request")
        if not message_ids:
            return 0
        with self._database(create=True) as connection:
            with _transaction(connection):
                before = connection.total_changes
                connection.executemany("INSERT OR IGNORE INTO deleted_messages VALUES(?,?,?,?)",
                                       [(account_id, chat_id, identifier, stamp) for identifier in message_ids])
                added = connection.total_changes - before
                if added:
                    revision = connection.execute("SELECT revision FROM retention_meta WHERE singleton=1").fetchone()[0]
                    if revision >= (1 << 63) - 1:
                        _fail("invalid_store")
                    connection.execute("UPDATE retention_meta SET revision=revision+1 WHERE singleton=1")
                return added

    def lookup(self, account_id, chat_id, message_ids):
        account_id, chat_id = canonical_id(account_id), canonical_id(chat_id, signed=True)
        message_ids = validate_ids(message_ids)
        with self._database(create=False) as connection:
            if connection is None:
                return {"deletedIds": [], "revision": _revision(0)}
            revision = connection.execute("SELECT revision FROM retention_meta WHERE singleton=1").fetchone()[0]
            if not message_ids:
                return {"deletedIds": [], "revision": _revision(revision)}
            placeholders = ",".join("?" for _ in message_ids)
            found = {row[0] for row in connection.execute(
                "SELECT message_id FROM deleted_messages WHERE account_id=? AND chat_id=? AND message_id IN (" + placeholders + ")",
                [account_id, chat_id, *message_ids])}
            return {"deletedIds": [identifier for identifier in message_ids if identifier in found],
                    "revision": _revision(revision)}


class ReceivedMessageStore(_IndexStore):
    """Bounded received-ID ledger; contains no message bodies or media."""
    _application_id = RECEIVED_APPLICATION_ID
    _tables = _RECEIVED_TABLES

    def remember(self, account_id, chat_id, message_ids):
        account_id, chat_id = canonical_id(account_id), canonical_id(chat_id, signed=True)
        message_ids = validate_ids(message_ids, limit=MAX_EVENT_IDS)
        if not message_ids:
            return 0
        with self._database(create=True) as connection:
            with _transaction(connection):
                before = connection.total_changes
                connection.executemany("INSERT OR IGNORE INTO received_messages VALUES(?,?,?)",
                                       [(account_id, chat_id, identifier) for identifier in message_ids])
                added = connection.total_changes - before
                if connection.execute("SELECT count(*) FROM received_messages").fetchone()[0] > MAX_KNOWN_IDS:
                    _fail("known_capacity")
                return added

    def seed(self):
        """Validate every stored routing ID; return bounded native batches."""
        records = []
        with self._database(create=False) as connection:
            if connection is None:
                return records
            current = None
            count = 0
            for account, chat, message in connection.execute(
                    "SELECT account_id,chat_id,message_id FROM received_messages ORDER BY account_id,chat_id,message_id LIMIT ?",
                    (MAX_KNOWN_IDS + 1,)):
                count += 1
                if count > MAX_KNOWN_IDS:
                    _fail("known_capacity")
                try:
                    canonical_id(account)
                    canonical_id(chat, signed=True)
                    canonical_id(message)
                except RetentionStoreError:
                    _fail("invalid_store")
                if current is None or current["accountId"] != account or current["chatId"] != chat or len(current["messageIds"]) == MAX_LOOKUP_IDS:
                    current = {"accountId": account, "chatId": chat, "messageIds": []}
                    records.append(current)
                current["messageIds"].append(message)
        return records
