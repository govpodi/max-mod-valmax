"""Retention preferences and marker-only bridge; no native message contents."""
from contextlib import contextmanager
import hashlib
import errno
import json
import os
import re
import stat
import threading
import uuid

from launch_mod import _check_path, _config_stream
from quick_reply_store import QuickReplyStore, QuickReplyStoreError, _writer_lock
from message_retention_store import RetentionMarkerStore, ReceivedMessageStore, RetentionStoreError, canonical_id, validate_ids, MAX_EVENT_IDS, MAX_LOOKUP_IDS, MAX_KNOWN_IDS

_REVISION = re.compile(r"[0-9a-f]{64}\Z")
_ERRORS = frozenset({"invalid_request", "invalid_store", "invalid_revision", "revision_conflict", "unsafe_path", "store_busy", "io_error", "no_space", "runtime_unavailable", "known_capacity"})
_NATIVE_ERRORS = frozenset({"waiting_for_modules", "unsupported_platform", "invalid_specification",
                            "module_mismatch", "hook_failed", "module_unloaded", "storage_unavailable",
                            "scope_guard", "memory_guard", "emit_failed", "known_capacity"})


class RetentionServiceError(Exception):
    def __init__(self, code):
        if type(code) is not str or code not in _ERRORS:
            raise ValueError("Unknown retention-service error code")
        self.code = code
        super().__init__(code)


def _fail(code):
    raise RetentionServiceError(code)


def _settings(enabled):
    if type(enabled) is not bool:
        _fail("invalid_request")
    return (json.dumps({"schema": 1, "enabled": enabled}, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def _snapshot(enabled):
    return {"schema": 1, "revision": hashlib.sha256(_settings(enabled)).hexdigest(), "enabled": enabled}


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _fail("invalid_store")
        value[key] = item
    return value


def _read(path):
    try:
        _check_path(path, directory=False)
        with _config_stream(path, create=False) as stream:
            data = stream.read(4097)
    except FileNotFoundError:
        return False
    if len(data) > 4096:
        _fail("invalid_store")
    try:
        value = json.loads(data.decode("utf8"), object_pairs_hook=_unique)
    except (ValueError, UnicodeError, RecursionError):
        _fail("invalid_store")
    if type(value) is not dict or set(value) != {"schema", "enabled"} or type(value["schema"]) is not int or value["schema"] != 1 or type(value["enabled"]) is not bool:
        _fail("invalid_store")
    return value["enabled"]


class RetentionSettingsStore:
    def __init__(self, path):
        try:
            self._guard = QuickReplyStore(path)
        except QuickReplyStoreError as error:
            _fail(error.code if error.code in _ERRORS else "io_error")

    @contextmanager
    def _operation(self, create):
        try:
            with self._guard._operation(create=create) as path:
                yield path
        except RetentionServiceError:
            raise
        except QuickReplyStoreError as error:
            original = error.__context__
            if (error.code == "io_error" and isinstance(original, OSError)
                    and (original.errno == errno.ENOSPC or getattr(original, "winerror", None) == 112)):
                _fail("no_space")
            _fail(error.code if error.code in _ERRORS else "io_error")
        except ValueError:
            _fail("unsafe_path")
        except OSError as error:
            _fail("no_space" if error.errno == errno.ENOSPC or getattr(error, "winerror", None) == 112 else "io_error")

    def snapshot(self):
        with self._operation(False) as path:
            return _snapshot(False if path is None else _read(path))

    def replace(self, revision, enabled):
        if type(revision) is not str or not _REVISION.fullmatch(revision):
            _fail("invalid_revision")
        payload = _settings(enabled)
        with self._operation(True) as path, _writer_lock(path):
            if _snapshot(_read(path))["revision"] != revision:
                _fail("revision_conflict")
            temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
            identity = None
            try:
                with _config_stream(temporary, create=True) as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        _fail("unsafe_path")
                    identity = info.st_dev, info.st_ino
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                if _snapshot(_read(path))["revision"] != revision:
                    _fail("revision_conflict")
                _check_path(temporary, directory=False)
                info = temporary.lstat()
                if (info.st_dev, info.st_ino) != identity:
                    _fail("unsafe_path")
                os.replace(temporary, path)
                identity = None
            finally:
                if identity is not None:
                    try:
                        _check_path(temporary, directory=False)
                        info = temporary.lstat()
                        if (info.st_dev, info.st_ino) == identity:
                            temporary.unlink()
                    except (FileNotFoundError, ValueError):
                        pass
            return _snapshot(enabled)


def native_snapshot(value):
    fields = {"ready", "enabled", "retainedEvents", "retainedMessages", "error", "historySyncVerified"}
    if (type(value) is not dict or set(value) != fields
            or any(type(value[key]) is not bool for key in ("ready", "enabled", "historySyncVerified"))
            or any(type(value[key]) is not int or not 0 <= value[key] <= 1000000 for key in ("retainedEvents", "retainedMessages"))
            or value["error"] is not None and (type(value["error"]) is not str or value["error"] not in _NATIVE_ERRORS)
            or value["ready"] and value["error"] is not None):
        _fail("invalid_request")
    return dict(value)


def _pid(value):
    if type(value) is not int or not 0 < value < (1 << 32):
        _fail("invalid_request")
    return value


class MessageRetentionService:
    def __init__(self, profile_root):
        self.settings = RetentionSettingsStore(profile_root / "message-retention-settings.json")
        self.markers = RetentionMarkerStore(profile_root / "retained-messages.sqlite3")
        self.received = ReceivedMessageStore(profile_root / "received-message-ids.sqlite3")
        self._lock = threading.RLock()
        self._allowed = set()
        self._clients = {}
        self._storage_failed = False
        self._known = set()
        self._known_updates = []
        self._known_cursors = {}
        self._known_loaded = False

    def desired(self):
        return self.settings.snapshot()

    def storage_available(self):
        with self._lock:
            return not self._storage_failed

    def preflight_marker_store(self):
        """Host must call before telling a native hook storage is available."""
        try:
            self.markers.ensure_ready()
            self.received.ensure_ready()
            seed = self.received.seed()
        except RetentionStoreError as error:
            with self._lock:
                self._storage_failed = True
            _fail(error.code)
        with self._lock:
            self._storage_failed = False
            if not self._known_loaded:
                self._known = {(row["accountId"], row["chatId"], identifier)
                               for row in seed for identifier in row["messageIds"]}
                self._known_loaded = True
        return True

    def register(self, pid):
        pid = _pid(pid)
        with self._lock:
            self._allowed.add(pid)
            self._known_cursors.setdefault(pid, len(self._known_updates))

    def remove(self, pid):
        pid = _pid(pid)
        with self._lock:
            self._allowed.discard(pid)
            self._clients.pop(pid, None)
            self._known_cursors.pop(pid, None)

    @staticmethod
    def _records(tuples, *, max_ids=None):
        records, current, total = [], None, 0
        for account, chat, identifier in tuples:
            if max_ids is not None and total >= max_ids:
                break
            if current is None or current["accountId"] != account or current["chatId"] != chat or len(current["messageIds"]) == MAX_LOOKUP_IDS:
                if max_ids is not None and len(records) == 256:
                    break
                current = {"accountId": account, "chatId": chat, "messageIds": []}
                records.append(current)
            current["messageIds"].append(identifier)
            total += 1
        return records, total

    def known_messages(self):
        with self._lock:
            if not self._known_loaded or self._storage_failed:
                _fail("runtime_unavailable")
            return self._records(sorted(self._known))[0]

    def pending_known(self, pid):
        pid = _pid(pid)
        with self._lock:
            if pid not in self._allowed or self._storage_failed:
                _fail("runtime_unavailable")
            offset = self._known_cursors[pid]
            records, count = self._records(self._known_updates[offset:offset + 4096], max_ids=4096)
            return {"type": "valmax-retention-known", "schema": 1, "records": records}, offset + count

    def acknowledge_known(self, pid, cursor):
        pid = _pid(pid)
        with self._lock:
            if (pid not in self._allowed or type(cursor) is not int
                    or not self._known_cursors[pid] <= cursor <= len(self._known_updates)):
                _fail("invalid_request")
            self._known_cursors[pid] = cursor

    def _remember(self, account, chat, identifiers):
        try:
            canonical_id(account)
            canonical_id(chat, signed=True)
            validate_ids(identifiers, limit=MAX_LOOKUP_IDS)
        except RetentionStoreError as error:
            _fail(error.code)
        with self._lock:
            if self._storage_failed or not self._known_loaded:
                _fail("runtime_unavailable")
            missing = [(account, chat, identifier) for identifier in identifiers
                       if (account, chat, identifier) not in self._known]
            if not missing:
                return
            if len(self._known) + len(missing) > MAX_KNOWN_IDS:
                self._storage_failed = True
                _fail("known_capacity")
            try:
                self.received.remember(account, chat, [row[2] for row in missing])
            except RetentionStoreError as error:
                self._storage_failed = True
                _fail(error.code)
            self._known.update(missing)
            self._known_updates.extend(missing)

    def record_received(self, pid, payload):
        pid = _pid(pid)
        if (type(payload) is not dict or set(payload) != {"kind", "schema", "accountId", "chatId", "messageIds"}
                or payload["kind"] != "received-messages" or type(payload["schema"]) is not int or payload["schema"] != 1):
            _fail("invalid_request")
        with self._lock:
            if pid not in self._allowed:
                _fail("invalid_request")
        try:
            canonical_id(payload["accountId"])
            canonical_id(payload["chatId"], signed=True)
            validate_ids(payload["messageIds"], limit=MAX_LOOKUP_IDS)
        except RetentionStoreError as error:
            _fail(error.code)
        if self.desired()["enabled"]:
            self._remember(payload["accountId"], payload["chatId"], payload["messageIds"])

    def invalidate(self, pid):
        pid = _pid(pid)
        with self._lock:
            self._clients.pop(pid, None)

    def update(self, pid, value):
        pid = _pid(pid)
        value = native_snapshot(value)
        with self._lock:
            if pid not in self._allowed:
                _fail("invalid_request")
            self._clients[pid] = value

    def snapshot(self):
        desired = self.desired()
        with self._lock:
            clients = [self._clients.get(pid) for pid in self._allowed]
            available = bool(clients) and not self._storage_failed and all(item is not None and item["ready"] for item in clients)
            enabled = available and all(item["enabled"] for item in clients)
            applied = available and all(item["enabled"] == desired["enabled"] for item in clients)
            values = [item for item in clients if item is not None]
            error = "storage_unavailable" if self._storage_failed else next((item["error"] for item in values if item["error"] is not None), None)
            runtime = {"ready": available, "applied": applied, "enabled": enabled,
                       "retainedEvents": min(1000000, sum(item["retainedEvents"] for item in values)),
                       "retainedMessages": min(1000000, sum(item["retainedMessages"] for item in values)),
                       "error": error,
                       "historySyncVerified": available and all(item["historySyncVerified"] for item in values)}
        return {**desired, "runtime": runtime}

    def configure(self, body):
        if (type(body) is not dict or set(body) != {"schema", "revision", "enabled"}
                or type(body["schema"]) is not int or body["schema"] != 1):
            _fail("invalid_request")
        if type(body["revision"]) is not str or not _REVISION.fullmatch(body["revision"]):
            _fail("invalid_revision")
        if type(body["enabled"]) is not bool:
            _fail("invalid_request")
        if body["enabled"] and not self.snapshot()["runtime"]["ready"]:
            _fail("runtime_unavailable")
        if body["enabled"]:
            self.preflight_marker_store()
        self.settings.replace(body["revision"], body["enabled"])
        return self.snapshot()

    def lookup(self, body):
        if (type(body) is not dict or set(body) != {"schema", "accountId", "chatId", "messageIds"}
                or type(body["schema"]) is not int or body["schema"] != 1):
            _fail("invalid_request")
        try:
            result = self.markers.lookup(body["accountId"], body["chatId"], body["messageIds"])
            if self.desired()["enabled"]:
                self._remember(body["accountId"], body["chatId"], body["messageIds"])
            return {"schema": 1, "accountId": body["accountId"], "chatId": body["chatId"],
                    **result, "enabled": self.desired()["enabled"], "ready": True}
        except RetentionStoreError as error:
            _fail(error.code)

    def record(self, pid, payload):
        pid = _pid(pid)
        if (type(payload) is not dict or set(payload) != {"kind", "schema", "accountId", "chatId", "messageIds"}
                or payload["kind"] != "retained-delete" or type(payload["schema"]) is not int or payload["schema"] != 1):
            _fail("invalid_request")
        try:
            canonical_id(payload["accountId"])
            canonical_id(payload["chatId"], signed=True)
            validate_ids(payload["messageIds"], limit=MAX_EVENT_IDS)
            if not payload["messageIds"]:
                _fail("invalid_request")
        except RetentionStoreError as error:
            _fail(error.code)
        with self._lock:
            if pid not in self._allowed or self._storage_failed:
                _fail("runtime_unavailable")
        try:
            self.markers.mark(payload["accountId"], payload["chatId"], payload["messageIds"])
        except RetentionStoreError as error:
            with self._lock:
                self._storage_failed = True
            _fail(error.code)
