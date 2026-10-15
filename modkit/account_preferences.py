"""Local account labels and schedules; never reads native account data.

The caller supplies account IDs obtained from the native account model. This
store only saves preferences explicitly submitted by the user. Time windows
use the computer's current local clock; an overnight window belongs to its
start day. Ghost preferences are reserved data: this module makes no claim
that a corresponding network filter is enabled.

Reuse the launcher's checked handles/parent guards and the quick-reply store's
bounded cross-process writer lock, without sharing either store's contents.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from launch_mod import _check_path, _config_stream
from quick_reply_store import QuickReplyStore, QuickReplyStoreError, _writer_lock

MAX_STORE_BYTES = 64 * 1024
MAX_ACCOUNTS = 32  # Retain labels for signed-out accounts; active limit is separate.
COLORS = ("", "#0088ff", "#8b5cf6", "#22a06b", "#e58b17", "#dc5260", "#d35ca5", "#149ca1")
GHOST_FIELDS = ("hideRead", "hideStoryRead", "hideTyping", "hideOnline", "autoOffline")
_ACCOUNT_ID = re.compile(r"[1-9][0-9]{0,18}\Z")
_REVISION = re.compile(r"[0-9a-f]{64}\Z")
_TIME = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")
_CODES = frozenset({"invalid_accounts", "invalid_revision", "revision_conflict", "invalid_store",
                    "unsafe_path", "store_busy", "io_error"})


class AccountPreferencesError(Exception):
    """Finite public code; never contains an account ID or preference value."""
    def __init__(self, code):
        if code not in _CODES:
            raise ValueError("Unknown account-preference error code")
        self.code = code
        super().__init__(code)


def _fail(code):
    raise AccountPreferencesError(code)


def valid_account_id(value):
    return (type(value) is str and bool(_ACCOUNT_ID.fullmatch(value))
            and int(value) <= (1 << 63) - 1)


def defaults(account_id):
    if not valid_account_id(account_id):
        _fail("invalid_accounts")
    return {"accountId": account_id, "displayName": "", "color": "",
            "workHours": {"enabled": False, "days": [1, 2, 3, 4, 5], "start": "09:00", "end": "18:00"},
            "ghost": {field: False for field in GHOST_FIELDS}}


def _display_name(value):
    return (type(value) is str and len(value) <= 64
            and not any(ord(c) < 32 or 127 <= ord(c) < 160 or 0xD800 <= ord(c) <= 0xDFFF
                        or ord(c) in (0x2028, 0x2029, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
                                      0x2066, 0x2067, 0x2068, 0x2069) for c in value))


def _hours(value, code):
    if (type(value) is not dict or set(value) != {"enabled", "days", "start", "end"}
            or type(value["enabled"]) is not bool or type(value["days"]) is not list
            or len(value["days"]) > 7 or any(type(d) is not int or not 1 <= d <= 7 for d in value["days"])
            or len(set(value["days"])) != len(value["days"])
            or value["enabled"] and not value["days"]
            or type(value["start"]) is not str or not _TIME.fullmatch(value["start"])
            or type(value["end"]) is not str or not _TIME.fullmatch(value["end"])
            or value["start"] == value["end"]):
        _fail(code)
    return {"enabled": value["enabled"], "days": sorted(value["days"]),
            "start": value["start"], "end": value["end"]}


def _validated_accounts(values, code):
    if type(values) is not list or len(values) > MAX_ACCOUNTS:
        _fail(code)
    result, seen = [], set()
    for row in values:
        if (type(row) is not dict or set(row) != {"accountId", "displayName", "color", "workHours", "ghost"}
                or not valid_account_id(row["accountId"]) or row["accountId"] in seen
                or not _display_name(row["displayName"])
                or type(row["color"]) is not str or row["color"] not in COLORS
                or type(row["ghost"]) is not dict or set(row["ghost"]) != set(GHOST_FIELDS)
                or any(type(row["ghost"][field]) is not bool for field in GHOST_FIELDS)):
            _fail(code)
        seen.add(row["accountId"])
        result.append({"accountId": row["accountId"], "displayName": row["displayName"], "color": row["color"],
                       "workHours": _hours(row["workHours"], code),
                       "ghost": {field: row["ghost"][field] for field in GHOST_FIELDS}})
    # Stable ordering makes revision independent of which account was edited.
    return sorted(result, key=lambda row: int(row["accountId"]))


def _canonical(values, code="invalid_store"):
    data = json.dumps({"schema": 1, "accounts": values}, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf8")
    if len(data) > MAX_STORE_BYTES:
        _fail(code)
    return data


def _snapshot(values, code="invalid_store"):
    return {"schema": 1, "revision": hashlib.sha256(_canonical(values, code)).hexdigest(), "accounts": values}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("invalid_store")
        result[key] = value
    return result


def _constant(_value):
    _fail("invalid_store")


def _parse(data):
    if len(data) > MAX_STORE_BYTES:
        _fail("invalid_store")
    try:
        value = json.loads(data.decode("utf8"), object_pairs_hook=_unique, parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError):
        _fail("invalid_store")
    if (type(value) is not dict or set(value) != {"schema", "accounts"}
            or type(value["schema"]) is not int or value["schema"] != 1):
        _fail("invalid_store")
    values = _validated_accounts(value["accounts"], "invalid_store")
    _canonical(values)
    return values


def _read(path):
    try:
        _check_path(path, directory=False)
        with _config_stream(path, create=False) as stream:
            return _parse(stream.read(MAX_STORE_BYTES + 1))
    except FileNotFoundError:
        return []


class AccountPreferencesStore:
    def __init__(self, path):
        try:
            self._guard = QuickReplyStore(path)
        except QuickReplyStoreError as error:
            _fail(error.code)
        self.path = self._guard.path

    @contextmanager
    def _operation(self, *, create):
        try:
            with self._guard._operation(create=create) as path:
                yield path
        except AccountPreferencesError:
            raise
        except QuickReplyStoreError as error:
            _fail(error.code if error.code in _CODES else "io_error")

    def snapshot(self):
        with self._operation(create=False) as path:
            return _snapshot([] if path is None else _read(path))

    def replace(self, expected_revision, accounts):
        if type(expected_revision) is not str or not _REVISION.fullmatch(expected_revision):
            _fail("invalid_revision")
        clean = _validated_accounts(accounts, "invalid_accounts")
        payload = _canonical(clean, "invalid_accounts")
        with self._operation(create=True) as path:
            with _writer_lock(path):
                if _snapshot(_read(path))["revision"] != expected_revision:
                    _fail("revision_conflict")
                temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
                identity = None
                fd = None
                try:
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                    fd = os.open(temporary, flags, 0o600)
                    info = os.fstat(fd)
                    identity = info.st_dev, info.st_ino
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        _fail("unsafe_path")
                    stream = os.fdopen(fd, "wb")
                    fd = None  # Stream now owns the descriptor, including close errors.
                    with stream:
                        stream.write(payload)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if _snapshot(_read(path))["revision"] != expected_revision:
                        _fail("revision_conflict")
                    _check_path(temporary, directory=False)
                    info = temporary.lstat()
                    if (info.st_dev, info.st_ino) != identity:
                        _fail("unsafe_path")
                    os.replace(temporary, path)
                    identity = None
                finally:
                    if fd is not None:
                        os.close(fd)
                    if identity is not None:
                        try:
                            _check_path(temporary, directory=False)
                            info = temporary.lstat()
                            if (info.st_dev, info.st_ino) == identity:
                                temporary.unlink()
                        except FileNotFoundError:
                            pass
                return _snapshot(clean, "invalid_accounts")


def is_work_time(hours, now: datetime):
    """Pure schedule reference used by tests; now represents the local clock."""
    hours = _hours(hours, "invalid_accounts")
    if not hours["enabled"]:
        return True
    day, minute = now.isoweekday(), now.hour * 60 + now.minute
    start = int(hours["start"][:2]) * 60 + int(hours["start"][3:])
    end = int(hours["end"][:2]) * 60 + int(hours["end"][3:])
    if start < end:
        return day in hours["days"] and start <= minute < end
    previous_day = 7 if day == 1 else day - 1
    return (day in hours["days"] and minute >= start
            or previous_day in hours["days"] and minute < end)
