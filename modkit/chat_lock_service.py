"""Local per-chat opening PINs. No messenger database or network access.

PIN verifiers are salted, slow-hashed, and protected with this Windows user's
DPAPI. This is an application UI lock, not encryption of the MAX history.
Tests inject a protector and never access Windows credentials or real profiles.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager, ExitStack
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
import uuid

from launch_mod import _check_path, _config_stream
from quick_reply_store import QuickReplyStore, QuickReplyStoreError, _writer_lock

MAX_BYTES = 512 * 1024
MAX_LOCKS = 256
ITERATIONS = 210000
UNLOCK_SECONDS = 120
ERRORS = frozenset({"invalid_request", "invalid_pin", "wrong_pin", "rate_limited", "not_protected",
                    "already_protected", "limit_reached", "unsafe_path", "store_busy", "io_error", "invalid_store", "protector_unavailable", "notification_unavailable"})
KEYS = {
    "status": {"schema", "accountId", "chatId"},
    "set": {"schema", "accountId", "chatId", "pin", "confirmPin"},
    "unlock": {"schema", "accountId", "chatId", "pin"},
    "remove": {"schema", "accountId", "chatId", "pin"},
    "lock": {"schema", "accountId", "chatId"},
    "lock-all": {"schema"},
}


class ChatLockError(Exception):
    def __init__(self, code):
        self.code = code if code in ERRORS else "io_error"
        super().__init__(self.code)


def fail(code):
    raise ChatLockError(code)


def scope(account, chat, code="invalid_request"):
    if (type(account) is not str or not re.fullmatch(r"[1-9][0-9]{0,18}", account)
            or int(account) > 0x7FFFFFFFFFFFFFFF or type(chat) is not str
            or not re.fullmatch(r"(?:0|-?[1-9][0-9]{0,18})", chat)
            or not -(2**63) <= int(chat) <= 0x7FFFFFFFFFFFFFFF):
        fail(code)
    return account + ":" + chat


def pin(value):
    if type(value) is not str or not re.fullmatch(r"[0-9]{6,12}", value):
        fail("invalid_pin")
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail("invalid_store")
        result[key] = value
    return result


def _bad_constant(_value):
    fail("invalid_store")


def _validated(value):
    if (type(value) is not dict or set(value) != {"schema", "locks"}
            or type(value["schema"]) is not int or value["schema"] != 1
            or type(value["locks"]) is not dict or len(value["locks"]) > MAX_LOCKS):
        fail("invalid_store")
    for key, record in value["locks"].items():
        if type(key) is not str or key.count(":") != 1:
            fail("invalid_store")
        scope(*key.split(":"), "invalid_store")
        if (type(record) is not dict or set(record) != {"salt", "protected", "failures", "blockedUntil"}
                or type(record["salt"]) is not str or not re.fullmatch(r"[0-9a-f]{32}", record["salt"])
                or type(record["protected"]) is not str or not 1 <= len(record["protected"]) <= 16384
                or type(record["failures"]) is not int or not 0 <= record["failures"] <= 1000000
                or type(record["blockedUntil"]) is not int or not 0 <= record["blockedUntil"] < 2**53):
            fail("invalid_store")
        try:
            if not 16 <= len(base64.b64decode(record["protected"], validate=True)) <= 12288:
                fail("invalid_store")
        except (ValueError, TypeError):
            fail("invalid_store")
    return value


class WindowsProtector:
    """Only protects/unprotects this feature's verifier, never enumerates WinCred."""
    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    def _run(self, data, protect):
        if os.name != "nt" or type(data) is not bytes or not 1 <= len(data) <= 12288:
            fail("protector_unavailable")
        source = ctypes.create_string_buffer(data)
        source_blob = self.Blob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
        entropy = ctypes.create_string_buffer(b"ValMAX.ChatOpeningPIN.v1")
        entropy_blob = self.Blob(len(entropy.raw) - 1, ctypes.cast(entropy, ctypes.POINTER(ctypes.c_ubyte)))
        output = self.Blob()
        api = ctypes.WinDLL("crypt32", use_last_error=True)
        function = api.CryptProtectData if protect else api.CryptUnprotectData
        function.argtypes = (ctypes.POINTER(self.Blob), ctypes.c_void_p, ctypes.POINTER(self.Blob),
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(self.Blob))
        function.restype = wintypes.BOOL
        if not function(ctypes.byref(source_blob), None, ctypes.byref(entropy_blob), None, None, 1, ctypes.byref(output)):
            fail("protector_unavailable")
        local_free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
        local_free.argtypes = (ctypes.c_void_p,)
        local_free.restype = ctypes.c_void_p
        try:
            if not 1 <= output.cbData <= 12288:
                fail("protector_unavailable")
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            local_free(output.pbData)

    def protect(self, data):
        return self._run(data, True)

    def unprotect(self, data):
        return self._run(data, False)


class ChatLockService:
    def __init__(self, directory: Path, *, protector=None, clock=time.time, monotonic=time.monotonic,
                 before_protect=None):
        self.guard = QuickReplyStore(Path(directory) / "chat-locks.json")
        self.protector = protector if protector is not None else WindowsProtector()
        self.clock, self.monotonic = clock, monotonic
        self.mutex = threading.RLock()
        self.kdf_slots = threading.BoundedSemaphore(2)
        self.grants = {}
        self.before_protect = before_protect

    @contextmanager
    def _operation(self, create=False):
        try:
            with self.mutex, self.guard._operation(create=create) as path:
                if path is None:
                    yield None, {"schema": 1, "locks": {}}
                else:
                    with _writer_lock(path):
                        yield path, self._read(path)
        except ChatLockError:
            raise
        except QuickReplyStoreError as error:
            fail(error.code)
        except (OSError, ValueError, TypeError, RecursionError):
            fail("io_error")

    def _read(self, path):
        try:
            with _config_stream(path, create=False) as stream:
                data = stream.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return {"schema": 1, "locks": {}}
        if len(data) > MAX_BYTES:
            fail("invalid_store")
        try:
            return _validated(json.loads(data.decode("utf8"), object_pairs_hook=_unique, parse_constant=_bad_constant))
        except (UnicodeError, ValueError, TypeError, RecursionError):
            fail("invalid_store")

    def _write(self, path, value):
        raw = json.dumps(_validated(value), sort_keys=True, separators=(",", ":")).encode("utf8")
        if path is None or len(raw) > MAX_BYTES:
            fail("limit_reached")
        temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
        identity = None
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                info = os.fstat(stream.fileno())
                identity = info.st_dev, info.st_ino
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    fail("unsafe_path")
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            _check_path(temporary, directory=False)
            info = temporary.lstat()
            if (info.st_dev, info.st_ino) != identity:
                fail("unsafe_path")
            os.replace(temporary, path)
            identity = None
        finally:
            if identity is not None:
                try:
                    _check_path(temporary, directory=False)
                    info = temporary.lstat()
                    if (info.st_dev, info.st_ino) == identity:
                        temporary.unlink()
                except FileNotFoundError:
                    pass

    def _derive(self, value, salt):
        if not self.kdf_slots.acquire(blocking=False):
            fail("store_busy")
        try:
            return hashlib.pbkdf2_hmac("sha256", pin(value).encode("ascii"), bytes.fromhex(salt), ITERATIONS, 32)
        finally:
            self.kdf_slots.release()

    def _verify(self, path, state, key, value):
        record = state["locks"].get(key)
        if record is None:
            fail("not_protected")
        if record["blockedUntil"] > int(self.clock() * 1000):
            fail("rate_limited")
        expected = self.protector.unprotect(base64.b64decode(record["protected"], validate=True))
        if len(expected) != 32:
            fail("invalid_store")
        actual = self._derive(value, record["salt"])
        if not hmac.compare_digest(actual, expected):
            self.grants.pop(key, None)
            record["failures"] = min(1000000, record["failures"] + 1)
            delay = min(300, 30 * 2 ** min(4, record["failures"] - 5)) if record["failures"] >= 5 else 0
            record["blockedUntil"] = int((self.clock() + delay) * 1000) if delay else 0
            self._write(path, state)
            fail("wrong_pin")
        record["failures"] = 0
        record["blockedUntil"] = 0

    def _locked(self, key):
        until = self.grants.get(key, 0)
        if until <= self.monotonic():
            self.grants.pop(key, None)
            return True
        return False

    def catalog(self):
        with self._operation() as (_path, state):
            entries = []
            for key in sorted(state["locks"]):
                account, chat = key.split(":")
                entries.append({"accountId": account, "chatId": chat, "locked": self._locked(key)})
            return {"schema": 1, "ready": True, "protected": entries}

    def execute(self, action, value):
        if (action not in KEYS or type(value) is not dict or set(value) != KEYS[action]
                or type(value.get("schema")) is not int or value["schema"] != 1):
            fail("invalid_request")
        if action == "lock-all":
            with self.mutex:
                self.grants.clear()
            return self.catalog()
        key = scope(value["accountId"], value["chatId"])
        new_pin = None
        if action == "set":
            new_pin = pin(value["pin"])
            if not hmac.compare_digest(new_pin, pin(value["confirmPin"])):
                fail("invalid_pin")
        elif action in ("unlock", "remove"):
            pin(value["pin"])
        with self._operation(create=action == "set") as (path, state):
            exists = key in state["locks"]
            if action == "set":
                if exists:
                    fail("already_protected")
                if len(state["locks"]) >= MAX_LOCKS:
                    fail("limit_reached")
                salt = secrets.token_hex(16)
                protected = self.protector.protect(self._derive(new_pin, salt))
                state["locks"][key] = {"salt": salt, "protected": base64.b64encode(protected).decode("ascii"),
                                       "failures": 0, "blockedUntil": 0}
                # Confirm native notification masking before a new PIN can
                # become persistent. Only public scope IDs enter this bridge.
                # The mutex also serializes periodic policy refreshes, so an
                # old catalogue cannot undo this staged protection.
                with ExitStack() as confirmation:
                    if self.before_protect is not None:
                        entries = []
                        for protected_key in sorted(state["locks"]):
                            owner, chat = protected_key.split(":")
                            entries.append({"accountId": owner, "chatId": chat, "locked": True})
                        try:
                            lease = self.before_protect({"schema": 1, "ready": True, "protected": entries})
                            if lease is not None:
                                confirmation.enter_context(lease)
                        except Exception:
                            fail("notification_unavailable")
                    self._write(path, state)
                    self.grants[key] = self.monotonic() + UNLOCK_SECONDS
                exists = True
            elif action in ("unlock", "remove"):
                pin(value["pin"])
                self._verify(path, state, key, value["pin"])
                if action == "remove":
                    del state["locks"][key]
                    self.grants.pop(key, None)
                    exists = False
                else:
                    self.grants[key] = self.monotonic() + UNLOCK_SECONDS
                self._write(path, state)
            elif action == "lock":
                self.grants.pop(key, None)
            return {"schema": 1, "ready": True, "accountId": value["accountId"], "chatId": value["chatId"],
                    "protected": exists, "locked": exists and self._locked(key)}
