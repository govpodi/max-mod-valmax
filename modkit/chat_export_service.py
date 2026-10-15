"""Explicit user-owned chat export coordinator; no profile or network access.

Native QML supplies only the selected chat's available messages and saves media
to assigned local paths. Pages are indexed on disk, not kept as one large JSON
document. The export is a new folder and never replaces an existing result.
"""
from __future__ import annotations

from contextlib import ExitStack
import base64
import binascii
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import unquote, urlsplit
import uuid

from chat_export_storage import ExportWorkspace, ChatExportStorageError, hold_ancestors
from launch_mod import _check_path, _config_stream, _hold_directory
from quick_reply_store import _lexical_path, QuickReplyStoreError

LIMITS = {"maxPages": 20000, "maxMessages": 1000000, "maxAttachments": 1000000,
          "maxPageMessages": 200, "maxPageBytes": 2097152,
          "maxAudioBytes": 64 * 1024**2, "maxChunkBytes": 49152}
STOP_REASONS = frozenset({"end_of_history", "cancelled", "timeout", "history_error", "no_progress",
    "page_limit", "message_limit", "attachment_limit", "message_too_large", "unsupported_history"})
MEDIA_REASONS = frozenset({"not_available", "permission_denied", "download_failed", "timeout", "unsupported", "cancelled"})
MEDIA_TYPES = frozenset({"image", "video", "voice", "audio", "file", "unsupported"})
ERRORS = frozenset({"invalid_request", "busy", "not_found", "sequence_conflict", "invalid_state",
    "unsafe_path", "io_error", "insufficient_space", "limit_reached", "cancelled", "cleanup_failed"})
TERMINAL = frozenset({"complete", "partial", "failed", "cancelled"})
MAX_ASSET_BYTES = 16 * 1024**3
KEYS = {
    "start": {"schema", "accountId", "chat", "directory"},
    "page": {"schema", "jobId", "jobToken", "sequence", "messages"},
    "media": {"schema", "jobId", "jobToken", "messageId", "attachmentId", "status", "reason"},
    "media-start": {"schema", "jobId", "jobToken", "messageId", "attachmentId"},
    "media-chunk": {"schema", "jobId", "jobToken", "messageId", "attachmentId", "offset", "data"},
    "finish": {"schema", "jobId", "jobToken", "completion"},
    "status": {"schema", "jobId", "jobToken"},
    "history": {"schema", "jobId", "jobToken"},
    "cancel": {"schema", "jobId", "jobToken"},
}


class ChatExportError(Exception):
    def __init__(self, code):
        self.code = code if code in ERRORS else "io_error"
        super().__init__(self.code)


def _fail(code="invalid_request"):
    raise ChatExportError(code)


def _string(value, maximum, *, empty=True):
    if type(value) is not str or len(value) > maximum or (not empty and not value) or "\x00" in value:
        _fail()
    # Reject lone surrogates before writing UTF-8; never echo a bad value.
    try: value.encode("utf8")
    except UnicodeError: _fail()
    return value


def _identifier(value):
    value = _string(value, 128, empty=False)
    if any(ord(c) < 33 or ord(c) > 126 for c in value): _fail()
    return value


def validate_chat(value):
    if type(value) is not dict or set(value) != {"id", "title", "timezoneOffsetMinutes"}: _fail()
    offset = value["timezoneOffsetMinutes"]
    if type(offset) is not int or not -840 <= offset <= 840: _fail()
    return {"id": _identifier(value["id"]), "title": _string(value["title"], 512), "timezoneOffsetMinutes": offset}


def validate_message(value):
    required = {"id", "author", "timestampMs", "text", "outgoing", "reply", "attachments"}
    if type(value) is not dict or not required.issubset(value) or set(value) - required - {"kind"}: _fail()
    kind = value.get("kind", "message")
    if type(kind) is not str or kind not in ("message", "service", "unsupported"): _fail()
    author = value["author"]
    if type(author) is not dict or set(author) != {"id", "name"}: _fail()
    stamp = value["timestampMs"]
    if stamp is not None and (type(stamp) is not int or not -62135546400000 <= stamp <= 253402250399999): _fail()
    if type(value["outgoing"]) is not bool: _fail()
    reply = value["reply"]
    if reply is not None:
        if type(reply) is not dict or set(reply) != {"id", "authorName", "text"}: _fail()
        reply = {"id": _identifier(reply["id"]), "authorName": _string(reply["authorName"], 512), "text": _string(reply["text"], 65536)}
    attachments = value["attachments"]
    if type(attachments) is not list or len(attachments) > 256: _fail()
    result = []
    ids = set()
    for item in attachments:
        if type(item) is not dict or set(item) != {"id", "name", "mime", "type", "status", "reason"}: _fail()
        identifier = _identifier(item["id"])
        if identifier in ids: _fail()
        ids.add(identifier)
        if type(item["type"]) is not str or item["type"] not in MEDIA_TYPES or item["status"] not in ("pending", "unavailable"): _fail()
        if item["reason"] is not None and (type(item["reason"]) is not str or item["reason"] not in MEDIA_REASONS): _fail()
        if item["status"] == "pending" and (item["reason"] is not None or item["type"] == "unsupported"): _fail()
        if item["status"] == "unavailable" and item["reason"] is None: _fail()
        mime = _string(item["mime"], 128)
        if mime and not re.fullmatch(r"[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+", mime): _fail()
        result.append({"id": identifier, "name": _string(item["name"], 512), "mime": mime,
                       "type": item["type"], "status": item["status"], "reason": item["reason"]})
    return {"id": _identifier(value["id"]), "author": {"id": _identifier(author["id"]), "name": _string(author["name"], 512)},
            "timestampMs": stamp, "text": _string(value["text"], 65536), "outgoing": value["outgoing"], "reply": reply, "attachments": result, "kind": kind}


def validate_completion(value):
    if type(value) is not dict or set(value) != {"historyComplete", "stopReason", "pagesFetched"}: _fail()
    if type(value["historyComplete"]) is not bool or type(value["stopReason"]) is not str or value["stopReason"] not in STOP_REASONS: _fail()
    if type(value["pagesFetched"]) is not int or not 0 <= value["pagesFetched"] <= LIMITS["maxPages"]: _fail()
    if value["historyComplete"] != (value["stopReason"] == "end_of_history"): _fail()
    return dict(value)


def selected_directory(value):
    value = _string(value, 32768, empty=False)
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment: _fail("unsafe_path")
        text = unquote(parsed.path, errors="strict")
        if os.name == "nt" and re.match(r"/[A-Za-z]:/", text): text = text[1:]
        path = _lexical_path(text)
        _check_path(path, directory=True)
        return path
    except (ValueError, OSError, UnicodeError, QuickReplyStoreError):
        _fail("unsafe_path")


def _error(error):
    if isinstance(error, OSError) and (error.errno == 28 or getattr(error, "winerror", None) == 112):
        return "insufficient_space"
    code = getattr(error, "code", "io_error")
    if code == "no_space": return "insufficient_space"
    return code if code in ERRORS else "io_error"


def _extension(mime, kind):
    # The original name is display text only, never part of a file-system path.
    return {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
            "video/mp4": ".mp4", "video/webm": ".webm", "audio/ogg": ".ogg", "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a", "audio/wav": ".wav", "application/pdf": ".pdf"}.get(mime.lower(),
              {"image": ".jpg", "video": ".mp4", "voice": ".ogg", "audio": ".bin"}.get(kind, ".bin"))


class ExportJob:
    def __init__(self, directory, chat, account_id, *, renderer=None):
        self.id, self.token = uuid.uuid4().hex, secrets.token_hex(32)
        self.chat, self.account_id = chat, account_id
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.state, self.error = "exporting", None
        self.sequence, self.last_page_hash, self.last_response = 0, None, None
        self.message_count = self.attachment_count = self.ready_count = self.unavailable_count = self.media_bytes = 0
        self.updated = time.monotonic()
        self.renderer, self.worker = renderer, None
        self.active_native = None
        self.history_pid = None
        self.history_proof = {"ready": False, "backward": False, "forward": False}
        self.workspace = self.db = None
        self.index_hold, self.index_parents = ExitStack(), ExitStack()
        self.index_path = directory / (".valmax-index-" + self.id)
        self.index_identity = None
        self.destination = directory / ("ValMAX-Chat-" + time.strftime("%Y-%m-%d_%H-%M-%S") + "-" + self.id[:8])
        try:
            hold_ancestors(self.index_parents, directory)
            self.workspace = ExportWorkspace(self.destination, cancel_event=self.cancel_event)
            self.index_path.mkdir(mode=0o700)
            info = self.index_path.lstat(); self.index_identity = info.st_dev, info.st_ino
            self.index_hold.enter_context(_hold_directory(self.index_path))
            db_path = self.index_path / "index.sqlite"
            with _config_stream(db_path, create=True): pass
            self.db = sqlite3.connect(db_path, check_same_thread=False)
            self.db.executescript("""PRAGMA journal_mode=DELETE;
                CREATE TABLE messages (id TEXT PRIMARY KEY, stamp INTEGER, data TEXT NOT NULL);
                CREATE TABLE assets (message_id TEXT, attachment_id TEXT, source TEXT, state TEXT NOT NULL,
                    reason TEXT, bytes INTEGER, digest TEXT, chunk_bytes INTEGER NOT NULL DEFAULT 0,
                    chunk_offset INTEGER, chunk_digest TEXT, PRIMARY KEY(message_id, attachment_id));""")
        except Exception as error:
            self._cleanup()
            raise ChatExportError(_error(error)) from None

    def snapshot(self):
        with self.lock:
            result = {"schema": 1, "jobId": self.id, "state": self.state,
                      "counts": {"messages": self.message_count, "attachments": self.attachment_count,
                                 "ready": self.ready_count, "unavailable": self.unavailable_count, "mediaBytes": self.media_bytes}}
            if self.error: result["error"] = self.error
            if self.active_native is not None and self.state in TERMINAL:
                result["cleanupPending"] = True
            if self.state in ("complete", "partial"):
                result["htmlPath"] = str(self.destination / "index.html")
                result["htmlFileURL"] = (self.destination / "index.html").as_uri()
            return result

    def observe_history(self, pid, value):
        """Consume only value-free observations of this marked native view."""
        with self.lock:
            if self.state != "exporting": return
            if value["event"] == "bound":
                if self.history_pid is None: self.history_pid = pid
                if self.history_pid == pid: self.history_proof["ready"] = True
                return
            if self.history_pid != pid or not self.history_proof["ready"]: return
            for direction, field in (("backward", "moreBackward"), ("forward", "moreForward")):
                if value[field]: self.history_proof[direction] = False
                elif (value["count"] > 0 and value["pendingDone"]
                      and value["direction"] in (direction, "center")):
                    self.history_proof[direction] = True

    def history(self):
        with self.lock:
            return {"schema": 1, "jobId": self.id, "history": dict(self.history_proof)}

    def page(self, sequence, values):
        if type(sequence) is not int or sequence < 0 or sequence >= LIMITS["maxPages"]: _fail()
        if type(values) is not list or not 1 <= len(values) <= LIMITS["maxPageMessages"]: _fail()
        rows = [validate_message(value) for value in values]
        encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf8")
        if len(encoded) > LIMITS["maxPageBytes"]: _fail("limit_reached")
        digest = hashlib.sha256(encoded).hexdigest()
        with self.lock:
            if self.state != "exporting": _fail("invalid_state")
            if sequence == self.sequence - 1 and digest == self.last_page_hash:
                return self.last_response
            if sequence != self.sequence: _fail("sequence_conflict")
            media = []
            new_rows = [row for row in rows if not self.db.execute("SELECT 1 FROM messages WHERE id=?", (row["id"],)).fetchone()]
            if len({r["id"] for r in new_rows}) != len(new_rows): _fail()
            attachments = sum(len(row["attachments"]) for row in new_rows)
            if self.message_count + len(new_rows) > LIMITS["maxMessages"] or self.attachment_count + attachments > LIMITS["maxAttachments"]:
                _fail("limit_reached")
            try:
                with self.db:
                    for row in new_rows:
                        self.db.execute("INSERT INTO messages VALUES(?,?,?)", (row["id"], row["timestampMs"], json.dumps(row, ensure_ascii=False)))
                        for item in row["attachments"]:
                            source = None
                            if item["status"] == "pending":
                                source = "media/" + uuid.uuid4().hex + _extension(item["mime"], item["type"])
                                path = self.workspace.reserve_asset(source)
                                media.append({"messageId": row["id"], "attachmentId": item["id"], "source": source, "nativePathFileURL": path.as_uri()})
                            self.db.execute("INSERT INTO assets(message_id,attachment_id,source,state,reason,bytes,digest) VALUES(?,?,?,?,?,?,?)", (row["id"], item["id"], source, item["status"], item["reason"], None, None))
                self.message_count += len(new_rows); self.attachment_count += attachments
                self.unavailable_count += attachments - len(media)
                self.sequence += 1; self.last_page_hash = digest
                self.last_response = {"schema": 1, "jobId": self.id, "sequence": sequence, "media": media}
                return self.last_response
            except Exception as error:
                self._failed(error)
                raise ChatExportError(_error(error)) from None

    def media_chunk(self, message_id, attachment_id, offset, encoded):
        message_id, attachment_id = _identifier(message_id), _identifier(attachment_id)
        if type(offset) is not int or not 0 <= offset < LIMITS["maxAudioBytes"]: _fail()
        if type(encoded) is not str or not 4 <= len(encoded) <= 65536: _fail()
        try: data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error): _fail()
        if not 1 <= len(data) <= LIMITS["maxChunkBytes"] or offset + len(data) > LIMITS["maxAudioBytes"]: _fail("limit_reached")
        digest = hashlib.sha256(data).hexdigest()
        with self.lock:
            if self.state != "exporting": _fail("invalid_state")
            row = self.db.execute("SELECT source,state,chunk_bytes,chunk_offset,chunk_digest FROM assets WHERE message_id=? AND attachment_id=?", (message_id, attachment_id)).fetchone()
            if row is None or row[0] is None or row[1] != "pending": _fail("invalid_state")
            if offset == row[3] and digest == row[4]:
                return {"schema": 1, "jobId": self.id, "offset": row[2]}
            if offset != row[2]: _fail("sequence_conflict")
            try:
                next_offset = self.workspace.write_asset_chunk(row[0], offset, data, max_bytes=LIMITS["maxAudioBytes"])
                if next_offset != offset + len(data): _fail("io_error")
                with self.db:
                    self.db.execute("UPDATE assets SET chunk_bytes=?,chunk_offset=?,chunk_digest=? WHERE message_id=? AND attachment_id=?", (next_offset, offset, digest, message_id, attachment_id))
                return {"schema": 1, "jobId": self.id, "offset": next_offset}
            except Exception as error:
                self._failed(error)
                raise ChatExportError(_error(error)) from None

    def media_start(self, message_id, attachment_id):
        pair = _identifier(message_id), _identifier(attachment_id)
        with self.lock:
            if self.state != "exporting": _fail("invalid_state")
            if self.active_native is not None and self.active_native != pair: _fail("invalid_state")
            row = self.db.execute("SELECT source,state FROM assets WHERE message_id=? AND attachment_id=?", pair).fetchone()
            if row is None or row[0] is None or row[1] != "pending": _fail("invalid_state")
            self.active_native = pair
            return self.snapshot()

    def media(self, message_id, attachment_id, state, reason):
        message_id, attachment_id = _identifier(message_id), _identifier(attachment_id)
        if state not in ("ready", "unavailable", "error", "cancelled"): _fail()
        if state == "ready" and reason is not None: _fail()
        if state != "ready" and (type(reason) is not str or reason not in MEDIA_REASONS): _fail()
        with self.lock:
            pair = message_id, attachment_id
            if self.state in ("cancelled", "failed") and self.active_native == pair:
                # The callback acknowledges that native file I/O has ended.
                # Do not verify/hash a cancelled file; now its staging is safe
                # to remove. A timer alone never makes this assertion.
                self.active_native = None
                self._cleanup()
                return self.snapshot()
            if self.state != "exporting": _fail("invalid_state")
            if self.active_native is not None and self.active_native != pair: _fail("invalid_state")
            row = self.db.execute("SELECT source,state,reason FROM assets WHERE message_id=? AND attachment_id=?", (message_id, attachment_id)).fetchone()
            if row is None or row[0] is None: _fail()
            if row[1] != "pending":
                if (row[1], row[2]) == (state, reason): return self.snapshot()
                _fail("invalid_state")
            self.active_native = None
            try:
                details = self.workspace.verify_asset(row[0], max_bytes=MAX_ASSET_BYTES) if state == "ready" else None
                if state != "ready": self.workspace.discard_asset(row[0])
                with self.db:
                    self.db.execute("UPDATE assets SET state=?,reason=?,bytes=?,digest=? WHERE message_id=? AND attachment_id=?", (state, reason, details["bytes"] if details else None, details["sha256"] if details else None, message_id, attachment_id))
                if details:
                    self.ready_count += 1; self.media_bytes += details["bytes"]
                else: self.unavailable_count += 1
                return self.snapshot()
            except Exception as error:
                self._failed(error)
                raise ChatExportError(_error(error)) from None

    def finish(self, completion):
        completion = validate_completion(completion)
        with self.lock:
            if self.state != "exporting":
                if self.state == "rendering" or self.state in TERMINAL: return self.snapshot()
                _fail("invalid_state")
            # Unacknowledged media never becomes a silently successful archive.
            count = self.db.execute("SELECT count(*) FROM assets WHERE state='pending'").fetchone()[0]
            if count: _fail("invalid_state")
            if completion["historyComplete"] and not all(self.history_proof.values()):
                completion = {**completion, "historyComplete": False, "stopReason": "unsupported_history"}
            self.state = "rendering"
            self.worker = threading.Thread(target=self._render, args=(completion,), name="ValMAX chat export", daemon=True)
            try: self.worker.start()
            except Exception as error:
                self.worker = None
                self._failed(error)
                raise ChatExportError(_error(error)) from None
            return self.snapshot()

    def _messages(self):
        cursor = self.db.execute("SELECT id,data FROM messages ORDER BY stamp IS NULL,stamp,length(id),id")
        for identifier, data in cursor:
            if self.cancel_event.is_set(): _fail("cancelled")
            row = json.loads(data)
            assets = {item[0]: item[1:] for item in self.db.execute("SELECT attachment_id,source,state,reason,bytes,digest FROM assets WHERE message_id=?", (identifier,))}
            for item in row["attachments"]:
                source, state, reason, count, digest = assets[item["id"]]
                item.update(source=source if state == "ready" else None, status=state, reason=reason)
                if state == "ready": item.update(bytes=count, sha256=digest)
            yield row

    def _render(self, completion):
        try:
            if self.renderer is None:
                from chat_export_render import render_into
                renderer = render_into
            else: renderer = self.renderer
            summary = renderer(self.workspace, self.chat, self._messages(), completion, deduplicated=True) if self.renderer is None else renderer(self.workspace, self.chat, self._messages(), completion)
            counts = summary["counts"]
            ready, missing, media_bytes = counts["mediaReady"], counts["mediaMissing"], counts["bytes"]
            if any(type(v) is not int or v < 0 for v in (ready, missing, media_bytes)): _fail("io_error")
            with self.lock:
                if self.cancel_event.is_set(): _fail("cancelled")
                self.workspace.publish()
                self.ready_count = ready
                self.unavailable_count = missing
                self.media_bytes = media_bytes
                self.state = "complete" if completion["historyComplete"] and not missing else "partial"
                self.updated = time.monotonic()
        except Exception as error:
            with self.lock: self._failed(error)
        finally:
            with self.lock: self._cleanup()

    def _failed(self, error):
        self.error = _error(error)
        self.state = "cancelled" if self.cancel_event.is_set() or self.error == "cancelled" else "failed"
        if self.worker is None: self._cleanup()

    def cancel(self):
        # Verification hashes large files. Set the cancellation flag before
        # waiting for the job mutex so its chunk loop can stop promptly.
        self.cancel_event.set()
        with self.lock:
            if self.state not in TERMINAL:
                if self.worker is None:
                    self.state = "cancelled"; self._cleanup()
            return self.snapshot()

    def _cleanup(self):
        if self.active_native is not None:
            return  # A late native writer must retain its assigned directory.
        if self.db is not None:
            try: self.db.close()
            except sqlite3.Error: pass
            self.db = None
        if self.workspace is not None:
            try: self.workspace.abort()
            except (ChatExportStorageError, OSError): self.error = "cleanup_failed"
        self.index_hold.close()
        try:
            if self.index_identity is not None:
                _check_path(self.index_path, directory=True)
                info = self.index_path.lstat()
                if (info.st_dev, info.st_ino) != self.index_identity: _fail("unsafe_path")
                # No recursive deletion and no removal of unexpected entries.
                for name in ("index.sqlite", "index.sqlite-journal", "index.sqlite-shm", "index.sqlite-wal"):
                    path = self.index_path / name
                    try: _check_path(path, directory=False)
                    except FileNotFoundError: continue
                    path.unlink()
                self.index_path.rmdir()
                self.index_identity = None
        except (OSError, ValueError, ChatExportError):
            self.error = "cleanup_failed"
        finally:
            self.index_parents.close()


class ChatExportService:
    """At most one live job, with bounded idle and completed-job retention."""
    def __init__(self, *, renderer=None, activity=None):
        self.lock, self.jobs, self.renderer = threading.RLock(), {}, renderer
        self.activity = activity

    def execute(self, action, value):
        if (type(action) is not str or action not in KEYS or type(value) is not dict or set(value) != KEYS[action]
                or type(value.get("schema")) is not int or value["schema"] != 1): _fail()
        with self.lock:
            self.reap()
            if action == "start":
                if any(job.state not in TERMINAL for job in self.jobs.values()): _fail("busy")
                if sum(job.active_native is not None for job in self.jobs.values()) >= 4: _fail("busy")
                account = _string(value["accountId"], 19, empty=False)
                if not re.fullmatch(r"[1-9][0-9]{0,18}", account) or int(account) > 9223372036854775807: _fail()
                chat = validate_chat(value["chat"])
                directory = selected_directory(value["directory"])
                job = ExportJob(directory, chat, account, renderer=self.renderer)
                self.jobs[job.id] = job
                return {**job.snapshot(), "jobToken": job.token, "limits": dict(LIMITS)}
            identifier, token = value.get("jobId"), value.get("jobToken")
            if type(identifier) is not str or not re.fullmatch(r"[0-9a-f]{32}", identifier): _fail("not_found")
            if type(token) is not str or not re.fullmatch(r"[0-9a-f]{64}", token): _fail("not_found")
            job = self.jobs.get(identifier)
            if job is None or not hmac.compare_digest(job.token, token): _fail("not_found")
            job.updated = time.monotonic()
        # Keep request dispatch and cancellation independent of disk hashing.
        if action == "page": return job.page(value["sequence"], value["messages"])
        if action == "media": return job.media(value["messageId"], value["attachmentId"], value["status"], value["reason"])
        if action == "media-start": return job.media_start(value["messageId"], value["attachmentId"])
        if action == "media-chunk": return job.media_chunk(value["messageId"], value["attachmentId"], value["offset"], value["data"])
        if action == "finish": return job.finish(value["completion"])
        if action == "cancel": return job.cancel()
        if action == "history": return job.history()
        return job.snapshot()

    def observe_history(self, pid, value):
        # No message IDs, text, account data or native addresses are accepted.
        if type(pid) is not int or not 0 < pid <= 0xffffffff or type(value) is not dict: return
        if value.get("kind") != "chat-export-history": return
        identifier = value.get("jobId")
        if type(identifier) is not str or re.fullmatch(r"[0-9a-f]{32}", identifier) is None: return
        event = value.get("event")
        if event == "bound":
            if set(value) != {"kind", "jobId", "event"}: return
        elif event == "page":
            if set(value) != {"kind", "jobId", "event", "direction", "count", "pendingDone", "moreBackward", "moreForward"}: return
            if value["direction"] not in ("backward", "center", "forward"): return
            if type(value["count"]) is not int or not 0 <= value["count"] <= 100000: return
            if any(type(value[key]) is not bool for key in ("pendingDone", "moreBackward", "moreForward")): return
        else: return
        with self.lock:
            job = self.jobs.get(identifier)
        if job is not None: job.observe_history(pid, value)

    def reap(self):
        now = time.monotonic()
        with self.lock:
            native_gone = False
            if self.activity is not None:
                try:
                    count, scanned = self.activity()
                    native_gone = scanned is True and type(count) is int and count == 0
                except Exception: pass  # Unknown process state cannot authorize cleanup.
            for identifier, job in tuple(self.jobs.items()):
                if now - job.updated > 900 and job.state == "exporting": job.cancel()
                if native_gone and job.state in TERMINAL and job.active_native is not None:
                    with job.lock:
                        job.active_native = None
                        job._cleanup()
                if job.state in TERMINAL and job.active_native is None and (now - job.updated > 900 or len(self.jobs) > 16):
                    self.jobs.pop(identifier)

    def pending(self):
        self.reap()
        with self.lock: return any(job.state not in TERMINAL or job.active_native is not None for job in self.jobs.values())

    def close(self):
        with self.lock:
            jobs = tuple(self.jobs.values())
            for job in jobs: job.cancel()
            self.reap()
        for job in jobs:
            if job.worker is not None and job.worker.ident is not None: job.worker.join(timeout=10)
