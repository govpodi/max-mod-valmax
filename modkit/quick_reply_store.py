"""Small local store for user-authored templates; never reads MAX data.

The constructor performs lexical validation only. Reads do not create files or
directories. On Windows, existing launcher guards pin all parent directories
and reject reparse points; writers serialize through a named Windows mutex.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import time
import uuid
from urllib.parse import unquote, urlsplit

from launch_mod import (_check_path, _config_stream, _hold_directory,
                        _validate_handle, _windows_api)

MAX_STORE_BYTES = 256 * 1024
MAX_REPLIES = 100
MAX_ATTACHMENTS = 10
MAX_PARTS = 10
MAX_FILE_URL = 4096
_COMMAND = re.compile(r"[a-zа-яё][a-zа-яё0-9_]{0,31}\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")
_ACCOUNT_ID = re.compile(r"[1-9][0-9]{0,18}\Z")
_REVISION = re.compile(r"[0-9a-f]{64}\Z")
_LOCK_SECONDS = 2.0
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_CODES = frozenset({"invalid_replies", "invalid_revision", "revision_conflict",
                    "invalid_store", "unsafe_path", "store_busy", "io_error"})


class QuickReplyStoreError(Exception):
    """A fixed public code: exception messages never contain file/content data."""

    def __init__(self, code: str):
        if code not in _CODES:
            raise ValueError("Unknown quick reply store error code")
        self.code = code
        super().__init__(code)


def _fail(code: str):
    raise QuickReplyStoreError(code)


def _string(value, minimum: int, maximum: int) -> bool:
    return (type(value) is str and minimum <= len(value) <= maximum
            and "\x00" not in value
            and not any(0xD800 <= ord(character) <= 0xDFFF for character in value))


def _account(value, code):
    if (type(value) is not str or value != "" and
            (not _ACCOUNT_ID.fullmatch(value) or int(value) > 0x7FFFFFFFFFFFFFFF)):
        _fail(code)
    return value


def _local_file_url(value, code):
    """Validate only a local Windows URL's spelling; never open/stat its target."""
    if (not _string(value, 1, MAX_FILE_URL) or not value.startswith("file:///")
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or re.search(r"%(?![0-9a-fA-F]{2})", value)):
        _fail(code)
    try:
        parsed = urlsplit(value)
        decoded = unquote(parsed.path, encoding="utf-8", errors="strict")
    except (ValueError, UnicodeError):
        _fail(code)
    if (parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment
            or not re.fullmatch(r"/[A-Za-z]:/[^\\]+", decoded)
            or not _string(decoded, 5, MAX_FILE_URL)
            or any(ord(c) < 32 or ord(c) == 127 for c in decoded)):
        _fail(code)
    parts = decoded[4:].split("/")
    for part in parts:
        if (not part or part in (".", "..") or part.endswith((".", " "))
                or any(c in part for c in ':<>"|?*')
                or part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL",
                   *("COM" + str(n) for n in range(1, 10)),
                   *("LPT" + str(n) for n in range(1, 10))}):
            _fail(code)
    return value, decoded.casefold()


def _attachments(value, code):
    if type(value) is not list or not 1 <= len(value) <= MAX_ATTACHMENTS:
        _fail(code)
    result, seen = [], set()
    for item in value:
        if (type(item) is not dict or set(item) != {"url", "kind"}
                or type(item["kind"]) is not str or item["kind"] not in ("file", "media")):
            _fail(code)
        url, identity = _local_file_url(item["url"], code)
        if identity in seen:
            _fail(code)
        seen.add(identity)
        result.append({"url": url, "kind": item["kind"]})
    return result


def _part(value, code):
    if (type(value) is not dict or not {"text"} <= set(value)
            or not set(value) <= {"text", "attachments"}
            or not _string(value["text"], 0, 5000)):
        _fail(code)
    result = {"text": value["text"]}
    if "attachments" in value:
        result["attachments"] = _attachments(value["attachments"], code)
    if not result["text"] and "attachments" not in result:
        _fail(code)
    return result


def _validated_replies(replies, code: str) -> list[dict]:
    if type(replies) is not list or len(replies) > MAX_REPLIES:
        _fail(code)
    result, ids, commands = [], set(), set()
    for reply in replies:
        if (type(reply) is not dict or not {"id", "command", "title", "text"} <= set(reply)
                or not set(reply) <= {"id", "command", "title", "text", "accountId", "attachments", "parts"}):
            _fail(code)
        rid, command = reply["id"], reply["command"]
        account = _account(reply.get("accountId", ""), code)
        if (type(rid) is not str or not _ID.fullmatch(rid)
                or type(command) is not str or not _COMMAND.fullmatch(command)
                or not _string(reply["title"], 0, 80)
                or rid in ids or (account, command) in commands):
            _fail(code)
        primary = _part({key: reply[key] for key in ("text", "attachments") if key in reply}, code)
        ids.add(rid)
        commands.add((account, command))
        clean = {"id": rid, "command": command, "title": reply["title"], **primary}
        if "accountId" in reply:
            clean["accountId"] = account
        if "parts" in reply:
            if type(reply["parts"]) is not list or not 1 <= len(reply["parts"]) < MAX_PARTS:
                _fail(code)
            clean["parts"] = [_part(part, code) for part in reply["parts"]]
        result.append(clean)
    return result


def _canonical(replies: list[dict], code: str) -> bytes:
    data = json.dumps({"schema": 1, "replies": replies}, ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(data) > MAX_STORE_BYTES:
        _fail(code)
    return data


def _snapshot(replies: list[dict], code: str = "invalid_store") -> dict:
    return {"schema": 1, "revision": hashlib.sha256(_canonical(replies, code)).hexdigest(),
            "replies": replies}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("invalid_store")
        result[key] = value
    return result


def _invalid_constant(_value):
    _fail("invalid_store")


def _parse(data: bytes) -> list[dict]:
    if len(data) > MAX_STORE_BYTES:
        _fail("invalid_store")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=_invalid_constant)
    except (UnicodeError, ValueError, RecursionError):
        _fail("invalid_store")
    if (type(value) is not dict or set(value) != {"schema", "replies"}
            or type(value["schema"]) is not int or value["schema"] != 1):
        _fail("invalid_store")
    replies = _validated_replies(value["replies"], "invalid_store")
    _canonical(replies, "invalid_store")
    return replies


def _lexical_path(value) -> Path:
    try:
        text = os.fspath(value)
        path = Path(text)
    except (TypeError, ValueError):
        _fail("unsafe_path")
    if (type(text) is not str or not text or "\x00" in text
            or not path.is_absolute() or text.startswith(("\\\\", "//"))
            or not path.name or path.name in (".", "..")
            or any(part in (".", "..") for part in re.split(r"[\\/]", text))
            or any(part in (".", "..") or part.endswith((".", " ")) for part in path.parts)
            or (os.name == "nt" and (any(":" in part for part in path.parts[1:])
                or any(part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL",
                       *("COM" + str(n) for n in range(1, 10)),
                       *("LPT" + str(n) for n in range(1, 10))} for part in path.parts[1:])))):
        _fail("unsafe_path")
    return path


@contextmanager
def _directory(path: Path):
    """Pin the directory against rename while permitting child replacement."""
    if os.name != "nt":
        with _hold_directory(path) as actual:
            yield actual
        return
    _check_path(path, directory=True)
    api = _windows_api()
    # READ|WRITE sharing permits MoveFileEx on a child. Omitting DELETE sharing
    # keeps this directory/ancestor from being renamed or removed in use.
    handle = api.CreateFileW(str(path), 0x81, 3, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        actual = _validate_handle(api, handle, path, directory=True)
        with ExitStack() as physical:
            if os.path.normcase(str(actual)) != os.path.normcase(str(path)):
                for ancestor in reversed(actual.parents):
                    physical.enter_context(_directory(ancestor))
            yield actual
    finally:
        api.CloseHandle(handle)


@contextmanager
def _parents(path: Path, *, create: bool):
    """Hold every existing ancestor; a missing read never creates its parent."""
    with ExitStack() as stack:
        actual = None
        for parent in reversed(path.parents):
            try:
                parent.lstat()
            except FileNotFoundError:
                if not create:
                    yield None
                    return
                parent.mkdir(mode=0o700)
            actual = stack.enter_context(_directory(parent))
        yield actual


@contextmanager
def _writer_lock(path: Path):
    if os.name == "nt":
        from ctypes import wintypes

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        api.CreateMutexW.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        api.ReleaseMutex.restype = wintypes.BOOL
        api.CloseHandle.argtypes = (wintypes.HANDLE,)
        key = hashlib.sha256(os.path.normcase(str(path)).encode("utf-8")).hexdigest()
        handle = api.CreateMutexW(None, False, "Local\\MAXModQuickReplies-" + key)
        if not handle:
            _fail("io_error")
        acquired = False
        try:
            status = api.WaitForSingleObject(handle, int(_LOCK_SECONDS * 1000))
            if status == 0x102:
                _fail("store_busy")
            if status not in (0, 0x80):  # ordinary or abandoned owner; revalidate file
                _fail("io_error")
            acquired = True
            yield
        finally:
            if acquired:
                api.ReleaseMutex(handle)
            api.CloseHandle(handle)
        return
    # A persistent lock inode is needed for flock: unlinking it after a write
    # would let waiters lock a different inode. Reads never create this file.
    import fcntl

    lock_path = path.with_name("." + path.name + ".lock")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            _fail("unsafe_path")
        deadline = time.monotonic() + _LOCK_SECONDS
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    _fail("store_busy")
                time.sleep(0.01)
        yield
    finally:
        os.close(fd)


def _read(path: Path) -> list[dict]:
    try:
        _check_path(path, directory=False)
        with _config_stream(path, create=False) as stream:
            return _parse(stream.read(MAX_STORE_BYTES + 1))
    except FileNotFoundError:
        return []


class QuickReplyStore:
    def __init__(self, path):
        self.path = _lexical_path(path)
        key = os.path.normcase(str(self.path))
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(key, threading.RLock())

    @contextmanager
    def _operation(self, *, create: bool):
        if not self._lock.acquire(timeout=_LOCK_SECONDS):
            _fail("store_busy")
        try:
            with _parents(self.path, create=create) as parent:
                yield parent / self.path.name if parent is not None else None
        except QuickReplyStoreError:
            raise
        except ValueError:
            _fail("unsafe_path")
        except OSError:
            _fail("io_error")
        finally:
            self._lock.release()

    def snapshot(self) -> dict:
        with self._operation(create=False) as path:
            return _snapshot([] if path is None else _read(path))

    def replace(self, expected_revision, replies) -> dict:
        if type(expected_revision) is not str or not _REVISION.fullmatch(expected_revision):
            _fail("invalid_revision")
        clean = _validated_replies(replies, "invalid_replies")
        payload = _canonical(clean, "invalid_replies")
        with self._operation(create=True) as path:
            with _writer_lock(path):
                current = _snapshot(_read(path))
                if current["revision"] != expected_revision:
                    _fail("revision_conflict")
                temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
                created = False
                try:
                    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
                    fd = os.open(temporary, flags, 0o600)
                    created = True
                    with os.fdopen(fd, "wb") as stream:
                        info = os.fstat(stream.fileno())
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            _fail("unsafe_path")
                        stream.write(payload)
                        stream.flush()
                        os.fsync(stream.fileno())
                    # Reject a destination changed into a link/special file
                    # during preparation, and never overwrite malformed JSON.
                    if _snapshot(_read(path))["revision"] != expected_revision:
                        _fail("revision_conflict")
                    os.replace(temporary, path)
                    created = False
                finally:
                    if created:
                        temporary.unlink(missing_ok=True)
                return _snapshot(clean, "invalid_replies")
