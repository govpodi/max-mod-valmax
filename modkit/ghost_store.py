"""Explicit global ghost preferences; no account, credential or message access.

The store describes requested settings. Runtime confirmation is separate and
must never be inferred from a successful file write.
"""
from contextlib import contextmanager
import hashlib
import json
import os
import re
import stat
import uuid

from launch_mod import _check_path, _config_stream
from quick_reply_store import QuickReplyStore, QuickReplyStoreError, _writer_lock

MAX_STORE_BYTES = 4096
_REVISION = re.compile(r"[0-9a-f]{64}\Z")
_CODES = frozenset({"invalid_settings", "invalid_revision", "revision_conflict", "invalid_store", "unsafe_path", "store_busy", "io_error"})

class GhostSettingsError(Exception):
    def __init__(self, code):
        if code not in _CODES:
            raise ValueError("Unknown ghost settings error")
        self.code = code
        super().__init__(code)

def _fail(code):
    raise GhostSettingsError(code)

def defaults():
    return {"enabled": False, "hideTyping": True, "hideRead": True,
            "hideOnline": False, "hideStories": False}

def validate_settings(value, code="invalid_settings"):
    # V1 preferences migrate in memory without enabling any newly added filter.
    # Accept the old exact shape from a still-open V1 settings page as well.
    legacy = {"enabled", "hideTyping", "hideRead"}
    if (type(value) is not dict or set(value) not in (legacy, set(defaults()))
            or any(type(x) is not bool for x in value.values())):
        _fail(code)
    return {**defaults(), **value}

def _canonical(value, code="invalid_store"):
    value = validate_settings(value, code)
    return json.dumps({"schema": 1, "settings": value}, sort_keys=True, separators=(",", ":")).encode("utf8")

def _snapshot(value, code="invalid_store"):
    clean = validate_settings(value, code)
    return {"schema": 1, "revision": hashlib.sha256(_canonical(clean, code)).hexdigest(), "settings": clean}

def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("invalid_store")
        result[key] = value
    return result

def _constant(value):
    _fail("invalid_store")

def _parse(data):
    if len(data) > MAX_STORE_BYTES:
        _fail("invalid_store")
    try:
        value = json.loads(data.decode("utf8"), object_pairs_hook=_unique, parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError):
        _fail("invalid_store")
    if type(value) is not dict or set(value) != {"schema", "settings"} or type(value["schema"]) is not int or value["schema"] != 1:
        _fail("invalid_store")
    return validate_settings(value["settings"], "invalid_store")

def _read(path):
    try:
        _check_path(path, directory=False)
        with _config_stream(path, create=False) as stream:
            return _parse(stream.read(MAX_STORE_BYTES + 1))
    except FileNotFoundError:
        return defaults()

class GhostSettingsStore:
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
        except GhostSettingsError:
            raise
        except QuickReplyStoreError as error:
            _fail(error.code if error.code in _CODES else "io_error")

    def snapshot(self):
        with self._operation(create=False) as path:
            return _snapshot(defaults() if path is None else _read(path))

    def replace(self, expected_revision, settings):
        if type(expected_revision) is not str or not _REVISION.fullmatch(expected_revision):
            _fail("invalid_revision")
        clean = validate_settings(settings, "invalid_settings")
        payload = _canonical(clean, "invalid_settings")
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
                return _snapshot(clean, "invalid_settings")

