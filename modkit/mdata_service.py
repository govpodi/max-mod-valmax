"""Explicit compact login export; offline import with helper restart.

No authorization is read by construction, GET/status or request validation.
Export returns its completed result without closing MAX. Only an accepted
import response authorizes the native page to closeMessenger.
Passwords and credential values never appear in public results or exceptions.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
from urllib.parse import unquote, urlsplit
import uuid

import launch_mod
import mdata_files as files
from mdata_credentials import MDataCredentials, allowed_name, owner_id, policy as credential_policy
from quick_reply_store import _parents, _directory, _lexical_path
from session_transfer import transfer_lock, _new_stream, _identity, _remove_owned

_CODES = frozenset({"invalid_request", "invalid_password", "authentication_failed", "invalid_archive",
    "unsupported_version", "invalid_payload", "policy_violation", "too_large", "crypto_unavailable",
    "io_error", "source_changed", "unsafe_path", "file_exists", "credential_io", "client_still_running",
    "busy", "no_accounts", "profile_changed", "invalid_profile", "account_limit", "insufficient_space",
    "unsupported_path", "unsupported_credential", "backup_failed", "rollback_failed", "legacy_add_only",
    "profile_collision", "recovery_required"})
SPACE_RESERVE = 64 * 1024 * 1024
EXPORT_SPACE_RESERVE = 256 * 1024 * 1024
EXPORT_SPACE_CHECK_BYTES = 1024 * 1024
STAGING_FILE_OVERHEAD = 4096


class MDataError(Exception):
    def __init__(self, code):
        self.code = code if code in _CODES else "io_error"
        super().__init__(self.code)


def _error(error):
    try:
        if isinstance(error, FileExistsError):
            return MDataError("file_exists")
        if isinstance(error, OSError) and (error.errno == 28 or getattr(error, "winerror", 0) == 112):
            return MDataError("insufficient_space")
        code = getattr(error, "code", "io_error")
        if code == "destination_not_empty":
            code = "profile_changed"
        return MDataError(code)
    except Exception:
        # Exception properties can themselves fail; never format the object.
        return MDataError("io_error")


def selected_path(value, action):
    if type(value) is not str or len(value) > 32768 or "\x00" in value:
        raise MDataError("unsafe_path")
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError()
        text = unquote(parsed.path, errors="strict")
        if os.name == "nt" and re.match(r"/[A-Za-z]:/", text):
            text = text[1:]
        path = _lexical_path(text)
        extensions = (".mdata",) if action == "export" else (".mdata", ".maxsession")
        if path.suffix.lower() not in extensions or any(p.casefold() == "oneme" for p in path.parts):
            raise ValueError()
        return path
    except (ValueError, TypeError, UnicodeError):
        raise MDataError("unsafe_path") from None


def _policy():
    from mdata_archive import MDataPolicy
    return MDataPolicy(credential_policy=credential_policy(), file_path_validator=files.included,
                       credential_name_validator=allowed_name)


def _login_policy():
    from mdata_archive import MDataPolicy
    from session_credentials import allowed_name as token_name, account_id
    from session_transfer import policy as login_policy
    def auth_path(relative):
        match = re.fullmatch(r"auth_([1-9][0-9]{0,18})", relative)
        return bool(match and account_id(match.group(1)))
    return MDataPolicy(credential_policy=login_policy(), file_path_validator=auth_path,
                       credential_name_validator=token_name)


def _active(credentials):
    return {owner_id(name) for name, blob in credentials.items()
            if blob and re.fullmatch(r"access_token_[1-9][0-9]{0,18}@MXMOD", name)}


def _validate_storage(manifest, credentials):
    known = files.profile_ids(manifest["files"])
    for name in credentials:
        if re.fullmatch(r"(?:access_token|history_storage_key|calls_token|calls_session)_[1-9][0-9]{0,18}@MXMOD", name):
            known.add(owner_id(name))
    for name in credentials:
        owner = owner_id(name)
        if owner is not None and owner not in known:
            raise MDataError("invalid_payload")
    for item in manifest["files"]:
        path = item["path"].casefold()
        history = re.fullmatch(r"user([1-9][0-9]{0,18})/history/.+\.db", path)
        if history and not credentials.get("history_storage_key_" + history.group(1) + "@MXMOD"):
            raise MDataError("invalid_payload")
        if path.startswith("app/") and path.endswith(".db") and not credentials.get("app_data_storage_key_Common@MXMOD"):
            raise MDataError("invalid_payload")


def _archive_space_bound(file_bytes):
    """Upper bound for the unchanged uncompressed encrypted container format."""
    from mdata_archive import CHUNK_BYTES, MAGIC, MAX_HEADER_BYTES, MAX_MANIFEST_BYTES
    plaintext = 4 + MAX_MANIFEST_BYTES + file_bytes
    chunks = (plaintext + CHUNK_BYTES - 1) // CHUNK_BYTES
    return len(MAGIC) + 2 + MAX_HEADER_BYTES + plaintext + 21 * (chunks + 1)


def _require_space(physical_directory, required, *, reserve=SPACE_RESERVE):
    """Read capacity only for a directory already held by a filesystem guard."""
    free = shutil.disk_usage(physical_directory).free
    if type(free) is not int or free < 0:
        raise MDataError("io_error")
    if free < required + reserve:
        raise MDataError("insufficient_space")


def _require_export_space(profile_directory, output_directory, file_bytes):
    # MAX needs room to reopen its profile even when the archive is on another
    # disk. These directories are held by their existing filesystem guards.
    _require_space(profile_directory, 0, reserve=EXPORT_SPACE_RESERVE)
    _require_space(output_directory, _archive_space_bound(file_bytes),
                   reserve=EXPORT_SPACE_RESERVE)


class _ExportOutput:
    """Bound unchecked output between capacity reads to about one MiB.

    A concurrent writer can consume previously checked space. Check again
    before the next batch, while keeping the partial archive unpublished.
    """
    def __init__(self, stream, profile_directory, output_directory):
        self.stream = stream
        self.profile_directory, self.output_directory = profile_directory, output_directory
        self._checked_bytes = 0

    def write(self, data):
        size = len(data)
        if size > self._checked_bytes:
            budget = size + EXPORT_SPACE_CHECK_BYTES
            _require_space(self.profile_directory, 0, reserve=EXPORT_SPACE_RESERVE)
            _require_space(self.output_directory, budget, reserve=EXPORT_SPACE_RESERVE)
            self._checked_bytes = budget
        written = self.stream.write(data)
        if type(written) is int and written > 0:
            self._checked_bytes -= written
        return written


class MDataService:
    def __init__(self, root, activity, *, credentials=None, wait_seconds=120, settle_seconds=2,
                 restart_activity=None):
        self.root = files.profile_path(root)
        self.activity = activity
        self.restart_activity = restart_activity
        self.credentials = credentials if credentials is not None else MDataCredentials()
        self.wait_seconds, self.settle_seconds = wait_seconds, settle_seconds
        self._job_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._loaded = False
        self._state, self._action, self._error, self._result = "idle", None, None, None
        self._profiles, self._backup = 0, False
        self._restart = False
        self._restart_after_wait_failure = False
        self._worker = None
        self._phase, self._operation_id, self._diagnostic = "lock", None, None
        self._cleanup_diagnostic = None

    def _set(self, state, *, error=None, result=None, profiles=None):
        with self._state_lock:
            self._loaded = True
            self._state, self._error, self._result = state, error, result
            if profiles is not None:
                self._profiles = profiles

    def snapshot(self):
        with self._state_lock:
            load = not self._loaded
            self._loaded = True
        if load:
            try:
                with files.hold_tree(self.root) as root:
                    with launch_mod._config_stream(root / "mdata-result.json", create=False) as stream:
                        raw = stream.read(2049)
                value = json.loads(raw)
                if (len(raw) <= 2048 and type(value) is dict and value.get("schema") == 1
                        and value.get("state") in ("done", "error")
                        and value.get("action") in ("export", "import")
                        and value.get("error") in (None, *_CODES)
                        and value.get("result") in (None, "exported", "restored", "added", "already_present")
                        and type(value.get("profiles")) is int and 0 <= value["profiles"] <= 3
                        and type(value.get("backupAvailable")) is bool):
                    with self._state_lock:
                        if self._state == "idle":
                            self._state, self._action = value["state"], value["action"]
                            self._error, self._result = value["error"], value["result"]
                            self._profiles, self._backup = value["profiles"], value["backupAvailable"]
            except (OSError, ValueError, TypeError, files.MDataFileError):
                pass
        with self._state_lock:
            return {"schema": 1, "available": True, "state": self._state, "action": self._action,
                    "error": self._error, "result": self._result,
                    "profiles": self._profiles, "backupAvailable": self._backup}

    def pending(self):
        with self._state_lock:
            # A terminal result precedes status persistence and restart
            # calculation. Keep the observer alive until that finalization
            # releases the job lock, even while state already says done/error.
            return self._job_lock.locked() or self._state in ("waiting_for_exit", "working")

    def restart_requested(self, *, recheck=False):
        with self._state_lock:
            requested, after_wait_failure = self._restart, self._restart_after_wait_failure
        # Recheck immediately before launching the replacement UI. A different
        # mod main may have appeared while HTTP and instrumentation closed.
        if requested and after_wait_failure and recheck:
            return self._safe_main_restart()
        return requested

    def _safe_main_restart(self):
        """A failed pre-transfer wait may return the UI, never change data."""
        if self.restart_activity is None:
            return False
        try:
            count, scanned = self.restart_activity()
            if type(count) is not int or count != 0 or scanned is not True:
                return False
            from mdata_recovery_state import ensure_clear
            ensure_clear(self.root)
            return True
        except Exception:
            # Unknown process state or an unreadable recovery guard fails closed.
            return False

    def _persist(self):
        try:
            from mdata_diagnostics import validate
            value = self.snapshot()
            if self._diagnostic is not None and validate(self._diagnostic):
                value["diagnostic"] = self._diagnostic
            if self._cleanup_diagnostic is not None and validate(self._cleanup_diagnostic):
                value["cleanupDiagnostic"] = self._cleanup_diagnostic
            data = json.dumps(value, separators=(",", ":")).encode("ascii")
            if len(data) > 2048:
                # Keep the primary result even if optional diagnostics cannot fit.
                value.pop("diagnostic", None)
                value.pop("cleanupDiagnostic", None)
                data = json.dumps(value, separators=(",", ":")).encode("ascii")
            with files.hold_tree(self.root) as root:
                temporary = root / (".mdata-result-" + uuid.uuid4().hex + ".tmp")
                try:
                    with _new_stream(temporary) as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    destination = root / "mdata-result.json"
                    if destination.exists():
                        launch_mod._check_path(destination, directory=False)
                    os.replace(temporary, destination)
                finally:
                    if temporary.exists():
                        launch_mod._check_path(temporary, directory=False)
                        temporary.unlink()
        except (OSError, ValueError, files.MDataFileError):
            pass

    def _failure_record(self, error, phase):
        try:
            from mdata_diagnostics import failure
            return failure(error, phase, self._operation_id)
        except Exception:
            # Diagnostics must not replace a job or cleanup outcome.
            return None

    def _cleanup_staging(self, staging, identity):
        pending_error = sys.exception()
        previous_phase, self._phase = self._phase, "cleanup"
        try:
            if staging.exists():
                files.remove_staging(staging, self.root.parent, expected=identity)
        except Exception as error:
            # A deletion error raised during finally inherits the unrelated
            # pending job exception as context. Exclude only that boundary
            # while collecting cleanup metadata, then restore the exception.
            slot = vars(BaseException)["__context__"]
            context = slot.__get__(error, BaseException)
            detached = pending_error is not None and context is pending_error
            if detached:
                slot.__set__(error, None)
            try:
                self._cleanup_diagnostic = self._failure_record(error, "cleanup")
            finally:
                if detached:
                    slot.__set__(error, context)
        finally:
            self._phase = previous_phase

    def _capture(self):
        self._phase = "profile_listing"
        entries = files.listing(self.root)
        self._phase = "credential_inventory"
        names = self.credentials.owned_names()
        credentials = {}
        for name in names:
            if not allowed_name(name):
                raise MDataError("unsupported_credential")
            self._phase = "credential_read"
            blob = self.credentials.read(name)
            if blob is None:
                raise MDataError("profile_changed")
            credentials[name] = blob
        if len(_active(credentials)) > 3:
            raise MDataError("account_limit")
        manifest = {"schema": 1, "sourceClient": "MAX Desktop", "sourceVersion": "26.35.1",
                    "files": entries, "credentials": [{"name": name, "blob": base64.b64encode(blob).decode("ascii")}
                                                        for name, blob in credentials.items()]}
        self._phase = "storage_validate"
        _validate_storage(manifest, credentials)
        return manifest, credentials

    @contextmanager
    def _archive_source(self, root, relative):
        self._phase = "source_open"
        with files.source(root, relative) as stream:
            self._phase = "source_read"
            yield stream

    def _write_archive(self, path, password, *, allow_empty=False, export_headroom=True,
                       login_only=True, account_ids=None):
        from mdata_archive import encrypt_stream
        selection = {"account_ids": account_ids} if account_ids is not None else {}
        if selection and not login_only:
            raise MDataError("invalid_request")
        if login_only:
            import mdata_login
            self._phase = "credential_read"
            manifest, credentials, auth_bytes = mdata_login.capture(self.root, self.credentials, **selection)
        else:
            manifest, credentials = self._capture()
        if not allow_empty and not _active(credentials):
            raise MDataError("no_accounts")
        self._phase = "archive_open"
        with files.hold_tree(self.root) as root:
            with _parents(path, create=False) as parent:
                if parent is None:
                    raise MDataError("unsafe_path")
                file_bytes = sum(item["size"] for item in manifest["files"])
                if export_headroom:
                    _require_export_space(root, parent, file_bytes)
                else:
                    _require_space(parent, _archive_space_bound(file_bytes))
                with files.atomic_output(path) as output:
                    self._phase = "archive_encrypt"
                    destination = _ExportOutput(output, root, parent) if export_headroom else output
                    if login_only:
                        encrypt_stream(manifest, password, lambda relative: io.BytesIO(auth_bytes[relative]),
                                       destination, policy=_login_policy(), archive_kind="login")
                    else:
                        encrypt_stream(manifest, password, lambda relative: self._archive_source(root, relative),
                                       destination, policy=_policy())
                    self._phase = "profile_verify"
                    if login_only:
                        again, token_values, auth_values = mdata_login.capture(root, self.credentials, **selection)
                        if again != manifest or token_values != credentials or auth_values != auth_bytes:
                            raise MDataError("profile_changed")
                    elif files.listing(root) != manifest["files"] or tuple(credentials) != self.credentials.owned_names():
                        raise MDataError("profile_changed")
                    self._phase = "credential_verify"
                    for name, blob in credentials.items():
                        if self.credentials.read(name) != blob:
                            raise MDataError("profile_changed")
                    if export_headroom:
                        _require_space(root, 0, reserve=EXPORT_SPACE_RESERVE)
                        _require_space(parent, 0, reserve=EXPORT_SPACE_RESERVE)
                    self._phase = "archive_publish"
        return manifest, credentials

    def execute(self, action, selected_file, password, mode):
        try:
            return self._execute(action, selected_file, password, mode)
        except MDataError:
            raise
        except Exception as error:
            raise _error(error) from None

    def execute_selected_export(self, selected_file, password, account_ids):
        """Explicit compact export selection; preserve the legacy request API."""
        from mdata_login import selected_account_ids
        try:
            selected = selected_account_ids(account_ids)
            return self._execute("export", selected_file, password, "add", account_ids=selected)
        except MDataError:
            raise
        except Exception as error:
            raise _error(error) from None

    def _export_login(self, path, password, *, account_ids=None):
        # The client may refresh an authorization while it is being read.
        # Retry only unpublished, bounded snapshots; unsafe paths and invalid
        # profiles still fail immediately. Never terminate or restart MAX.
        for attempt in range(3):
            try:
                selection = {"account_ids": account_ids} if account_ids is not None else {}
                return self._write_archive(path, password, **selection)
            except Exception as error:
                changed = _error(error).code in ("profile_changed", "source_changed")
                sharing = isinstance(error, OSError) and getattr(error, "winerror", 0) in (32, 33)
                vanished = isinstance(error, FileNotFoundError) and self._phase in (
                    "credential_read", "profile_verify", "credential_verify")
                if attempt == 2 or not (changed or sharing or vanished):
                    raise
                time.sleep(.05)

    def execute_confirmed_import(self, selected_file, password, fingerprint):
        """Import exactly the ciphertext whose account list was confirmed."""
        if (type(fingerprint) is not str or len(fingerprint) != 64
                or any(char not in "0123456789abcdef" for char in fingerprint)):
            raise MDataError("invalid_request")
        if selected_path(selected_file, "import").suffix.lower() != ".mdata":
            raise MDataError("invalid_request")
        try:
            return self._execute("import", selected_file, password, "add", fingerprint=fingerprint)
        except MDataError:
            raise
        except Exception as error:
            raise _error(error) from None

    def _execute(self, action, selected_file, password, mode, *, account_ids=None, fingerprint=None):
        from mdata_recovery_state import ensure_clear
        ensure_clear(self.root)
        if action not in ("export", "import") or mode not in ("restore", "add"):
            raise MDataError("invalid_request")
        if account_ids is not None and action != "export":
            raise MDataError("invalid_request")
        if type(password) is not str:
            raise MDataError("invalid_password")
        try:
            if not 12 <= len(password.encode("utf8")) <= 1024 or "\x00" in password:
                raise ValueError()
        except (ValueError, UnicodeError):
            raise MDataError("invalid_password") from None
        path = selected_path(selected_file, action)
        if path.is_relative_to(self.root):
            raise MDataError("unsafe_path")
        if self.pending():
            raise MDataError("busy")
        # Validate the chosen file/parent before reading any credentials.
        # Import alone requires native shutdown; export uses a small RAM copy.
        with _parents(path, create=False) as parent:
            if parent is None:
                raise MDataError("unsafe_path")
            if action == "import":
                launch_mod._check_path(path, directory=False)
                if path.suffix.lower() == ".maxsession" and mode != "add":
                    raise MDataError("legacy_add_only")
            else:
                try:
                    path.lstat()
                except FileNotFoundError:
                    pass
                else:
                    raise MDataError("file_exists")
        if not self._job_lock.acquire(False):
            raise MDataError("busy")
        try:
            if action == "export":
                # Reject low space before any credential read. This listing
                # reads sizes only; the live snapshot verifies its own data.
                with files.hold_tree(self.root) as root, _parents(path, create=False) as parent:
                    if parent is None:
                        raise MDataError("unsafe_path")
                    from mdata_login import listing
                    entries = listing(root)
                    if account_ids is not None:
                        from mdata_login import selected_account_ids
                        selected = selected_account_ids(account_ids)
                        chosen = {"auth_" + rid for rid in selected}
                        if not chosen.issubset({entry["path"] for entry in entries}):
                            raise MDataError("invalid_request")
                        entries = [entry for entry in entries if entry["path"] in chosen]
                    _require_export_space(root, parent, sum(item["size"] for item in entries))
        except Exception:
            self._job_lock.release()
            raise
        with self._state_lock:
            self._confirmed_fingerprint = fingerprint
            self._action, self._restart, self._backup = action, False, False
            # This is the outcome count for this job, not an inventory of
            # logged-in accounts. A failed import must not inherit the count
            # from a preceding export or import.
            self._profiles = 0
            self._restart_after_wait_failure = False
            self._phase, self._operation_id, self._diagnostic = "lock", uuid.uuid4().hex, None
            self._cleanup_diagnostic = None
        if action == "export":
            self._worker = None
            self._set("working")
            # HTTP already runs in its own thread. There is no native process
            # transition, and the response confirms the published archive.
            selection = {"account_ids": account_ids} if account_ids is not None else {}
            outcome = self._run(action, path, password, mode, None, **selection)
            return {**outcome, "accepted": True}
        self._set("waiting_for_exit")
        ready = threading.Event()
        try:
            self._worker = threading.Thread(target=self._run, args=(action, path, password, mode, ready),
                                            daemon=False, name="MAX Mod explicit MData job")
            self._worker.start()
        except Exception:
            self._worker = None
            self._confirmed_fingerprint = None
            self._set("error", error="io_error")
            self._job_lock.release()
            raise MDataError("io_error") from None
        if not ready.wait(2):
            # Do not authorize native shutdown until the worker owns its lock.
            raise MDataError("busy")
        value = self.snapshot()
        if value["state"] == "error":
            raise MDataError(value["error"])
        return {"schema": 1, "state": "waiting_for_exit", "accepted": True, "action": action}

    def _wait_closed(self):
        deadline, empty_since = time.monotonic() + self.wait_seconds, None
        while True:
            count, scanned = self.activity()
            if count or not scanned:
                empty_since = None
            elif empty_since is None:
                empty_since = time.monotonic()
            elif time.monotonic() - empty_since >= self.settle_seconds:
                return
            if time.monotonic() >= deadline:
                raise MDataError("client_still_running")
            time.sleep(.05)

    def _backup_current(self, password):
        self._phase = "backup"
        folder = self.root.parent / "MXMOD-MData-Backups"
        path = folder / ("MData-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8] + ".mdata")
        with _parents(path, create=True):
            pass
        snapshot, credentials = self._write_archive(path, password, allow_empty=True,
                                                     export_headroom=False, login_only=False)
        self._backup = True
        return snapshot, credentials, path

    def _decode(self, path, password, staging, *, additional_space=0, login_only=False):
        def preflight(total, count):
            if login_only and (total > 3 * 256 * 1024 or count > 3):
                raise MDataError("invalid_payload")
            with _directory(staging) as physical:
                _require_space(physical, total + count * STAGING_FILE_OVERHEAD + additional_space,
                               reserve=EXPORT_SPACE_RESERVE if login_only else SPACE_RESERVE)

        if path.suffix.lower() == ".maxsession":
            from session_archive import decrypt
            from session_transfer import policy as legacy_policy
            with _parents(path, create=False):
                with launch_mod._config_stream(path, create=False) as stream:
                    raw = stream.read(6 * 1024 * 1024 + 4097)
            old = decrypt(raw, password, policy=legacy_policy())
            decoded = [(item["path"], base64.b64decode(item["data"], validate=True)) for item in old["files"]]
            preflight(sum(len(data) for _, data in decoded), len(decoded))
            entries = []
            for relative, data in decoded:
                with files.sink(staging, relative, len(data)) as output:
                    output.write(data)
                entries.append({"path": relative, "size": len(data)})
            return {**old, "files": entries}
        from mdata_archive import decrypt_stream
        with _parents(path, create=False):
            with launch_mod._config_stream(path, create=False) as source:
                expected = getattr(self, "_confirmed_fingerprint", None) if login_only else None
                if expected is not None:
                    from mdata_manager import _BoundedSource
                    reader = _BoundedSource(source)
                else:
                    reader = source
                manifest = decrypt_stream(reader, password, lambda relative, size: files.sink(staging, relative, size),
                                      policy=_login_policy() if login_only else _policy(), preflight=preflight,
                                      expected_kind="login" if login_only else "profile")
                if expected is not None and reader.digest.hexdigest() != expected:
                    raise MDataError("source_changed")
                return manifest

    def _archive_kind(self, path):
        if path.suffix.lower() == ".maxsession":
            return "login"
        from mdata_archive import MAGIC, archive_kind
        with _parents(path, create=False):
            with launch_mod._config_stream(path, create=False) as source:
                return archive_kind(source.read(len(MAGIC)))

    def _import_login(self, path, password):
        """Add only authorizations; never snapshot or replace the current profile."""
        import mdata_login
        self._phase = "staging_create"
        staging = self.root.parent / (".MXMOD-MData-stage-" + uuid.uuid4().hex)
        with _parents(staging / "placeholder", create=True):
            pass
        identity = _identity(staging.lstat())
        try:
            self._phase = "archive_decrypt"
            manifest = self._decode(path, password, staging, login_only=True)
            incoming = {entry["name"]: base64.b64decode(entry["blob"], validate=True)
                        for entry in manifest["credentials"]}
            self._phase = "incoming_validate"
            mdata_login.validate(manifest, incoming)
            count, scanned = self.activity()
            if count or not scanned:
                raise MDataError("client_still_running")
            self._phase = "add"
            return mdata_login.import_snapshot(self.root, staging, manifest, incoming, self.credentials)
        finally:
            self._cleanup_staging(staging, identity)

    def _apply_credentials(self, before, after, changes):
        for name in sorted(set(before) | set(after)):
            old, new = before.get(name), after.get(name)
            if old == new:
                continue
            if new is None:
                self.credentials.delete_owned(name, old)
            else:
                self.credentials.set_owned(name, new, old)
            changes.append((name, old, new))

    def _rollback_credentials(self, changes):
        failed = False
        for name, old, new in reversed(changes):
            try:
                if old is None:
                    self.credentials.delete_owned(name, new)
                else:
                    self.credentials.set_owned(name, old, new)
            except Exception:
                failed = True
        return failed

    def _restore_all(self, staging, before, after):
        old_root = self.root.with_name("MXMOD-MData-before-" + uuid.uuid4().hex)
        changes = []
        old_moved = published = False
        stage_identity = _identity(staging.lstat())
        try:
            self._apply_credentials(before, after, changes)
            if self.root.exists():
                files.move_owned(self.root, old_root, self.root.parent)
                old_moved = True
            files.move_owned(staging, self.root, self.root.parent, expected=stage_identity)
            published = True
        except Exception:
            failed = self._rollback_credentials(changes)
            try:
                if published:
                    files.move_owned(self.root, staging, self.root.parent, expected=stage_identity)
                if old_moved:
                    files.move_owned(old_root, self.root, self.root.parent)
            except Exception:
                failed = True
            if failed:
                raise MDataError("rollback_failed") from None
            raise

    def _add_accounts(self, staging, before, after, manifest):
        incoming, current = _active(after), _active(before)
        new_ids = incoming - current
        if not new_ids:
            return "already_present", 0
        if len(current | incoming) > 3:
            raise MDataError("account_limit")
        additions = {name: blob for name, blob in after.items() if owner_id(name) in new_ids}
        result_credentials = {**before, **additions}
        # Only independently verified per-account storage is merged. The
        # shared app database and common key remain from this computer.
        prefixes = tuple("user" + rid + "/" + kind for rid in new_ids
                         for kind in ("history/", "files/", "video/", "webapps/"))
        exact = {"auth_" + rid for rid in new_ids} | {"user" + rid + "/settings" for rid in new_ids}
        selected = [item for item in manifest["files"] if item["path"].casefold() in exact or item["path"].casefold().startswith(prefixes)]
        rollback_dir = self.root.parent / (".MXMOD-MData-stage-rollback-" + uuid.uuid4().hex)
        with _parents(rollback_dir / "placeholder", create=True):
            pass
        replacements, changes = [], []
        try:
            self._apply_credentials(before, result_credentials, changes)
            for item in selected:
                relative = item["path"]
                destination = self.root.joinpath(*relative.split("/"))
                staged = staging.joinpath(*relative.split("/"))
                backup = rollback_dir.joinpath(*relative.split("/"))
                with _parents(destination, create=True), _parents(backup, create=True):
                    old_identity = None
                    if destination.exists():
                        launch_mod._check_path(destination, directory=False)
                        old_identity = _identity(destination.lstat())
                        destination.rename(backup)
                    new_identity = _identity(staged.lstat())
                    replacements.append((destination, staged, backup, old_identity, new_identity))
                    staged.rename(destination)
        except Exception:
            failed = self._rollback_credentials(changes)
            for destination, staged, backup, old_identity, new_identity in reversed(replacements):
                try:
                    if destination.exists():
                        if _identity(destination.lstat()) != new_identity:
                            raise MDataError("rollback_failed")
                        destination.rename(staged)
                    if old_identity is not None:
                        if _identity(backup.lstat()) != old_identity:
                            raise MDataError("rollback_failed")
                        backup.rename(destination)
                except Exception:
                    failed = True
            if failed:
                raise MDataError("rollback_failed") from None
            raise
        # Rollback files stay in their validated private sibling directory;
        # the encrypted complete backup is available independently.
        return "added", len(new_ids)

    def _import(self, path, password, mode):
        self._phase = "archive_decrypt"
        kind = self._archive_kind(path)
        if getattr(self, "_confirmed_fingerprint", None) is not None and kind != "login":
            raise MDataError("source_changed")
        if kind == "login":
            return self._import_login(path, password)
        self._phase = "staging_create"
        staging = self.root.parent / (".MXMOD-MData-stage-" + uuid.uuid4().hex)
        with _parents(staging / "placeholder", create=True):
            pass
        stage_identity = _identity(staging.lstat())
        preserve = False
        latch = None
        try:
            # Reserve the future complete encrypted backup as well as staging
            # before the first plaintext sink. This listing reads metadata only.
            self._phase = "profile_listing"
            current_entries = files.listing(self.root)
            backup_space = _archive_space_bound(sum(item["size"] for item in current_entries))
            self._phase = "archive_decrypt"
            manifest = self._decode(path, password, staging, additional_space=backup_space)
            self._phase = "incoming_validate"
            incoming = {entry["name"]: base64.b64decode(entry["blob"], validate=True) for entry in manifest["credentials"]}
            _validate_storage(manifest, incoming)
            if not _active(incoming):
                raise MDataError("no_accounts")
            if len(_active(incoming)) > 3:
                raise MDataError("account_limit")
            if mode == "restore":
                self._phase = "config_prepare"
                # Keep this mod's version-pinned privacy/isolation defaults.
                config = staging / "config.json"
                if config.exists():
                    launch_mod._check_path(config, directory=False)
                    config.unlink()
                data = json.dumps(launch_mod.make_config(), ensure_ascii=False, indent=2).encode("utf8")
                with _new_stream(config) as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            _, before = self._capture()
            if mode == "add" and not (_active(incoming) - _active(before)):
                return "already_present", 0
            if mode == "add" and len(_active(incoming) | _active(before)) > 3:
                raise MDataError("account_limit")
            if mode == "add":
                for rid in _active(incoming) - _active(before):
                    if (self.root / ("auth_" + rid)).exists() or (self.root / ("user" + rid)).exists():
                        raise MDataError("profile_collision")
            _, before, backup_path = self._backup_current(password)
            count, scanned = self.activity()
            if count or not scanned:
                raise MDataError("client_still_running")
            from mdata_recovery_state import write
            self._phase = "recovery_marker"
            latch = write(self.root, backup_path)
            if mode == "restore":
                self._phase = "restore"
                self._restore_all(staging, before, incoming)
                return "restored", len(_active(incoming))
            self._phase = "add"
            return self._add_accounts(staging, before, incoming, manifest)
        except Exception as error:
            preserve = _error(error).code in ("rollback_failed", "recovery_required")
            raise
        finally:
            if latch is not None and not preserve:
                from mdata_recovery_state import clear
                try:
                    clear(self.root, latch)
                except Exception:
                    raise MDataError("recovery_required") from None
            if not preserve:
                self._cleanup_staging(staging, stage_identity)

    def _run(self, action, path, password, mode, ready, *, account_ids=None):
        closed = False
        prewrite_wait_failure = False
        try:
            with transfer_lock():
                if ready is not None:
                    ready.set()
                if action == "import":
                    self._phase = "wait_closed"
                    self._wait_closed()
                    closed = True
                self._set("working")
                if action == "export":
                    selection = {"account_ids": account_ids} if account_ids is not None else {}
                    _, credentials = self._export_login(path, password, **selection)
                    result, profiles = "exported", len(_active(credentials))
                else:
                    result, profiles = self._import(path, password, mode)
                self._phase = "complete"
                self._set("done", result=result, profiles=profiles)
        except Exception as error:
            classified = _error(error)
            prewrite_wait_failure = (not closed and self._phase == "wait_closed"
                                     and classified.code == "client_still_running")
            self._diagnostic = self._failure_record(error, self._phase)
            self._set("error", error=classified.code)
        finally:
            if ready is not None:
                ready.set()
            password = None
            try:
                self._persist()
            except Exception:
                # Saving optional status cannot change the completed job.
                pass
            finally:
                try:
                    # No capture, archive or credential operation was entered
                    # in this branch. A surviving service must not strand the
                    # user after the acknowledged native main-window shutdown.
                    return_ui = action == "import" and prewrite_wait_failure and self._safe_main_restart()
                    with self._state_lock:
                        self._restart = action == "import" and ((closed and self._error not in ("rollback_failed", "recovery_required")) or return_ui)
                        self._restart_after_wait_failure = return_ui
                    # Freeze this job's response while the lock still belongs
                    # to it; another HTTP caller can immediately start a job
                    # after release and replace the shared status.
                    outcome = self.snapshot()
                finally:
                    self._confirmed_fingerprint = None
                    self._job_lock.release()
        return outcome


def owned_client_activity():
    """Verify closed clients across all roots; never attach or inspect profiles."""
    import frida
    from privacy_server import bounded_rpc, process_path
    from max_client_policy import scan_clients
    try:
        processes = bounded_rpc(lambda: frida.get_local_device().enumerate_processes())
        return scan_clients(processes, process_path)
    except Exception:
        return 0, False


def recover_backup(root, password, *, credentials=None, activity=None):
    """Explicit offline recovery, using the encrypted backup in the durable guard."""
    from mdata_recovery_state import read, clear
    root = files.profile_path(root)
    activity = activity or owned_client_activity
    with transfer_lock():
        count, scanned = activity()
        if count or not scanned:
            raise MDataError("client_still_running")
        latch = read(root)
        if latch is None:
            raise MDataError("invalid_request")
        service = MDataService(root, activity, credentials=credentials)
        service._action, service._backup = "import", True
        service._operation_id = uuid.uuid4().hex
        service._phase = "archive_decrypt"
        staging = root.parent / (".MXMOD-MData-stage-" + uuid.uuid4().hex)
        with _parents(staging / "placeholder", create=True):
            pass
        identity, preserve = _identity(staging.lstat()), False
        try:
            manifest = service._decode(Path(latch["backupFile"]), password, staging)
            incoming = {entry["name"]: base64.b64decode(entry["blob"], validate=True)
                        for entry in manifest["credentials"]}
            _validate_storage(manifest, incoming)
            config = staging / "config.json"
            if config.exists():
                launch_mod._check_path(config, directory=False)
                config.unlink()
            with _new_stream(config) as stream:
                stream.write(json.dumps(launch_mod.make_config(), ensure_ascii=False, indent=2).encode("utf8"))
                stream.flush()
                os.fsync(stream.fileno())
            before = {name: service.credentials.read(name) for name in service.credentials.owned_names()}
            count, scanned = activity()
            if count or not scanned:
                raise MDataError("client_still_running")
            service._restore_all(staging, before, incoming)
            clear(root, latch)
            service._action, service._backup = "import", True
            service._set("done", result="restored", profiles=len(_active(incoming)))
        except Exception as error:
            classified = _error(error)
            preserve = classified.code in ("rollback_failed", "recovery_required")
            service._diagnostic = service._failure_record(error, service._phase)
            service._set("error", error=classified.code)
            raise classified from None
        finally:
            if not preserve:
                service._cleanup_staging(staging, identity)
            try:
                service._persist()
            except Exception:
                pass
