"""Explicit encrypted transfer of MAX Mod's own authorization, never ONEME.

The constructor and status perform no credential reads. Export/import are
called only by the separate native settings action. No secret, password,
account identifier or file content is included in public results/errors.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid
from urllib.parse import unquote, urlsplit

import launch_mod
from quick_reply_store import _lexical_path, _parents, _directory
from session_credentials import (ACCOUNT_KEYS, COMMON_NAMES, WindowsCredentials,
                                 CredentialError, account_id, allowed_name, MAX_BLOB)

MAX_ACCOUNT_FILE = 256 * 1024
MAX_ARCHIVE_FILE = 6 * 1024 * 1024 + 4096
_AUTH = re.compile(r"auth_([1-9][0-9]{0,18})\Z")
_ERRORS = {"no_accounts", "invalid_profile", "destination_not_empty", "credential_io",
           "unsafe_path", "file_exists", "io_error", "client_still_running", "busy",
           "unsupported_version", "invalid_payload", "policy_violation", "too_large",
           "authentication_failed", "invalid_password", "crypto_unavailable",
           "rollback_failed", "invalid_request", "invalid_archive"}


class TransferError(Exception):
    def __init__(self, code):
        self.code = code if code in _ERRORS else "io_error"
        super().__init__(self.code)


def _failure(error):
    if isinstance(error, FileExistsError):
        return TransferError("file_exists")
    if isinstance(error, (ValueError, TypeError)):
        return TransferError("unsafe_path")
    return TransferError(getattr(error, "code", "io_error"))


def file_url(value):
    if type(value) is not str or len(value) > 32768 or "\x00" in value:
        raise TransferError("unsafe_path")
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError()
        decoded = unquote(parsed.path, errors="strict")
        if os.name == "nt" and re.match(r"/[a-zA-Z]:/", decoded):
            decoded = decoded[1:]
        path = _lexical_path(decoded)
        if (path.suffix.lower() != ".maxsession"
                or any(part.casefold() == "oneme" for part in path.parts)):
            raise ValueError()
        return path
    except (ValueError, TypeError, UnicodeError):
        raise TransferError("unsafe_path") from None


@contextmanager
def transfer_lock():
    """Serialize imports and launcher startup. No filesystem lock or secrets."""
    if os.name != "nt":
        yield
        return
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    api.CreateMutexW.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    api.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = api.CreateMutexW(None, False, "Local\\MAXModSessionTransferV1")
    if not handle:
        raise TransferError("io_error")
    acquired = False
    try:
        result = api.WaitForSingleObject(handle, 0)
        if result == 0x102:
            raise TransferError("busy")
        if result not in (0, 0x80):
            raise TransferError("io_error")
        acquired = True
        yield
    finally:
        if acquired:
            api.ReleaseMutex(handle)
        api.CloseHandle(handle)


def policy():
    from session_archive import ArchivePolicy
    token = r"[1-9][0-9]{0,18}"
    return ArchivePolicy(
        credential_names=COMMON_NAMES,
        credential_name_patterns=tuple(key + "_" + token + "@MXMOD" for key in ACCOUNT_KEYS),
        file_path_patterns=("auth_" + token,))


def _identity(info):
    return info.st_dev, info.st_ino


def _remove_owned(path, identity):
    try:
        path.lstat()
    except FileNotFoundError:
        return
    launch_mod._check_path(path, directory=False)
    if _identity(path.lstat()) != identity:
        raise TransferError("rollback_failed")
    path.unlink()


def _validate_created(api, handle, path, stream):
    if api is not None:
        launch_mod._validate_handle(api, handle, path, directory=False)
    import stat
    info = os.fstat(stream.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise TransferError("unsafe_path")


@contextmanager
def _new_stream(path):
    """Exclusive creation with handle-based deletion on ANY failed write/open.

DELETE access lets Windows remove the exact newly created object even if
validation fails before yielding. Share=0 prevents replacement while held.
"""
    api = handle = None
    if os.name == "nt":
        import msvcrt
        from ctypes import wintypes
        api = launch_mod._windows_api()
        api.SetFileInformationByHandle.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                                   ctypes.c_void_p, wintypes.DWORD)
        api.SetFileInformationByHandle.restype = wintypes.BOOL
        handle = api.CreateFileW(str(path), 0xC0010000, 0, None, 1, 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            winerror = ctypes.get_last_error()
            if winerror in (80, 183):
                raise FileExistsError()
            raise ctypes.WinError(winerror)
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        except Exception:
            delete = ctypes.c_ubyte(1)
            api.SetFileInformationByHandle(handle, 4, ctypes.byref(delete), ctypes.sizeof(delete))
            api.CloseHandle(handle)
            raise
    else:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    identity = None
    try:
        stream = os.fdopen(fd, "w+b")
    except Exception:
        try:
            if api is not None:
                delete = ctypes.c_ubyte(1)
                if not api.SetFileInformationByHandle(handle, 4, ctypes.byref(delete), ctypes.sizeof(delete)):
                    raise TransferError("rollback_failed") from None
            else:
                _remove_owned(path, _identity(os.fstat(fd)))
        finally:
            os.close(fd)
        raise
    with stream:
        try:
            identity = _identity(os.fstat(stream.fileno()))
            _validate_created(api, handle, path, stream)
            yield stream
        except Exception:
            if api is not None:
                delete = ctypes.c_ubyte(1)
                if not api.SetFileInformationByHandle(handle, 4, ctypes.byref(delete), ctypes.sizeof(delete)):
                    raise TransferError("rollback_failed") from None
            elif identity is not None:
                _remove_owned(path, identity)
            raise


def _rename_new(source, destination):
    if os.name == "nt":
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.MoveFileExW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
        api.MoveFileExW.restype = wintypes.BOOL
        if not api.MoveFileExW(str(source), str(destination), 8):
            winerror = ctypes.get_last_error()
            if winerror in (80, 183):
                raise FileExistsError()
            raise ctypes.WinError(winerror)
    else:
        os.link(source, destination)
        source.unlink()


def _payload_parts(payload):
    """Require matching access tokens and native auth metadata, no extra IDs."""
    files, credentials, ids = {}, {}, set()
    for item in payload["files"]:
        match = _AUTH.fullmatch(item["path"])
        if not match or not account_id(match.group(1)):
            raise TransferError("invalid_payload")
        data = base64.b64decode(item["data"], validate=True)
        if not 1 <= len(data) <= MAX_ACCOUNT_FILE:
            raise TransferError("invalid_payload")
        ids.add(match.group(1))
        files[item["path"]] = data
    if not ids or len(ids) > 3:
        raise TransferError("invalid_payload")
    for item in payload["credentials"]:
        name = item["name"]
        if not allowed_name(name):
            raise TransferError("policy_violation")
        data = base64.b64decode(item["blob"], validate=True)
        if not 1 <= len(data) <= MAX_BLOB:
            raise TransferError("invalid_payload")
        if name not in COMMON_NAMES and name.rsplit("_", 1)[1].split("@", 1)[0] not in ids:
            raise TransferError("invalid_payload")
        credentials[name] = data
    if any("access_token_" + rid + "@MXMOD" not in credentials for rid in ids):
        raise TransferError("invalid_payload")
    return files, credentials


class SessionTransfer:
    def __init__(self, root, client_activity, *, credentials=None, wait_seconds=120):
        self.root = _lexical_path(Path(root) / "config.json").parent
        if self.root.name != "MXMOD":
            raise TransferError("unsafe_path")
        self.activity = client_activity
        self.credentials = credentials if credentials is not None else WindowsCredentials()
        self.wait_seconds = wait_seconds
        self._guard = threading.Lock()
        self._state_guard = threading.Lock()
        self._state = "idle"
        self._error = None
        self._worker = None
        self._last_loaded = False

    def snapshot(self):
        with self._state_guard:
            load_previous = not self._last_loaded
            self._last_loaded = True
        if load_previous:
            try:
                with self._profile() as root:
                    with launch_mod._config_stream(root / "session-transfer-result.json", create=False) as stream:
                        raw = stream.read(513)
                value = json.loads(raw)
                if (len(raw) <= 512 and type(value) is dict and set(value) == {"schema", "state", "error"}
                        and type(value["schema"]) is int and value["schema"] == 1
                        and ((value["state"] == "done" and value["error"] is None)
                             or (value["state"] == "error" and type(value["error"]) is str
                                 and value["error"] in _ERRORS))):
                    with self._state_guard:
                        if self._state == "idle":
                            self._state, self._error = value["state"], value["error"]
            except (OSError, ValueError, TransferError):
                pass  # An absent/bad non-secret status file never grants import.
        with self._state_guard:
            return {"schema": 1, "available": True, "version": "26.35.1",
                    "state": self._state, "error": self._error}

    def _set(self, state, error=None):
        with self._state_guard:
            self._state, self._error = state, error
            self._last_loaded = True

    def _persist_final(self):
        import uuid
        try:
            with self._profile() as root:
                destination = root / "session-transfer-result.json"
                temporary = root / (".session-result-" + uuid.uuid4().hex + ".tmp")
                data = json.dumps({"schema": 1, "state": self._state, "error": self._error},
                                  separators=(",", ":")).encode("ascii")
                try:
                    if destination.exists():
                        launch_mod._check_path(destination, directory=False)
                    with _new_stream(temporary) as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, destination)
                finally:
                    if temporary.exists():
                        temporary.unlink()
        except (OSError, ValueError, TransferError):
            pass  # This optional status never contains or changes authorization.

    def pending(self):
        return self.snapshot()["state"] in ("waiting_for_exit", "importing")

    @contextmanager
    def _profile(self):
        with _parents(self.root / "config.json", create=False) as parent:
            if parent is None:
                raise TransferError("invalid_profile")
            with _directory(self.root) as actual:
                # The validated physical path also covers Codex's package overlay.
                yield actual

    @staticmethod
    def _auth_names(root):
        names = []
        for entry in root.iterdir():
            if entry.name.startswith("auth_"):
                match = _AUTH.fullmatch(entry.name)
                if not match or not account_id(match.group(1)):
                    raise TransferError("invalid_profile")
                names.append(entry.name)
        if len(names) > 3:
            raise TransferError("invalid_profile")
        return sorted(names)

    def _capture(self):
        files, credentials = [], []
        with self._profile() as root:
            names = self._auth_names(root)
            if not names:
                raise TransferError("no_accounts")
            for name in names:
                with launch_mod._config_stream(root / name, create=False) as stream:
                    data = stream.read(MAX_ACCOUNT_FILE + 1)
                if not 1 <= len(data) <= MAX_ACCOUNT_FILE:
                    raise TransferError("invalid_profile")
                rid = name[5:]
                token = self.credentials.read("access_token_" + rid + "@MXMOD")
                if not token:
                    raise TransferError("invalid_profile")
                files.append({"path": name, "data": base64.b64encode(data).decode("ascii")})
                for key in ACCOUNT_KEYS:
                    target = key + "_" + rid + "@MXMOD"
                    blob = token if key == "access_token" else self.credentials.read(target)
                    if blob:
                        credentials.append({"name": target, "blob": base64.b64encode(blob).decode("ascii")})
            for target in COMMON_NAMES:
                blob = self.credentials.read(target)
                if blob:
                    credentials.append({"name": target, "blob": base64.b64encode(blob).decode("ascii")})
        payload = {"schema": 1, "sourceClient": "MAX Desktop", "sourceVersion": "26.35.1",
                   "credentials": credentials, "files": files}
        _payload_parts(payload)
        return payload

    @staticmethod
    def _read_archive(path):
        with _parents(path, create=False) as parent:
            if parent is None:
                raise TransferError("io_error")
            with launch_mod._config_stream(path, create=False) as stream:
                result = stream.read(MAX_ARCHIVE_FILE + 1)
        if len(result) > MAX_ARCHIVE_FILE:
            raise TransferError("too_large")
        return result

    def _empty(self, root, targets):
        if self._auth_names(root):
            raise TransferError("destination_not_empty")
        for name in targets:
            if self.credentials.read(name) is not None:
                raise TransferError("destination_not_empty")

    def execute(self, action, selected_file, password):
        if action not in ("export", "import"):
            raise TransferError("invalid_request")
        if not self._guard.acquire(blocking=False):
            raise TransferError("busy")
        asynchronous = False
        try:
            from session_archive import encrypt, decrypt
            path = file_url(selected_file)
            if action == "export":
                # Exclusive file creation; ciphertext only ever reaches disk.
                with _parents(path, create=False) as parent:
                    if parent is None:
                        raise TransferError("unsafe_path")
                    try:
                        path.lstat()
                    except FileNotFoundError:
                        pass
                    else:
                        raise FileExistsError()
                    payload = self._capture()
                    archive = encrypt(payload, password, policy=policy())
                    if payload != self._capture():
                        raise TransferError("invalid_profile")
                    temporary = path.with_name(".mxsession-" + uuid.uuid4().hex + ".tmp")
                    owned = None
                    try:
                        with _new_stream(temporary) as stream:
                            owned = _identity(os.fstat(stream.fileno()))
                            stream.write(archive)
                            stream.flush()
                            os.fsync(stream.fileno())
                        _rename_new(temporary, path)
                        owned = None
                    finally:
                        if owned is not None and temporary.exists():
                            _remove_owned(temporary, owned)
                self._set("done")
                return {"schema": 1, "state": "done", "action": "export",
                        "credentials": len(payload["credentials"])}
            payload = decrypt(self._read_archive(path), password, policy=policy())
            files, credentials = _payload_parts(payload)
            with self._profile() as root:
                self._empty(root, credentials)
            self._set("waiting_for_exit")
            self._worker = threading.Thread(target=self._import_after_exit,
                                            args=(files, credentials), daemon=False,
                                            name="MAX Mod explicit session import")
            self._worker.start()
            asynchronous = True
            return {"schema": 1, "state": "waiting_for_exit", "action": "import"}
        except Exception as error:
            public = _failure(error)
            self._set("error", public.code)
            raise public from None
        finally:
            password = None
            if not asynchronous:
                self._guard.release()

    def _restore(self, files, credentials):
        _payload_parts({"files": [{"path": name, "data": base64.b64encode(data).decode("ascii")}
                                  for name, data in files.items()],
                        "credentials": [{"name": name, "blob": base64.b64encode(data).decode("ascii")}
                                        for name, data in credentials.items()]})
        created_files, created_credentials = [], []
        with self._profile() as root:
            self._empty(root, credentials)
            count, scanned = self.activity()
            if count or not scanned:
                raise TransferError("client_still_running")
            try:
                for name, data in credentials.items():
                    self.credentials.create(name, data)
                    created_credentials.append(name)
                for name, data in files.items():
                    with _new_stream(root / name) as stream:
                        created_files.append((name, _identity(os.fstat(stream.fileno()))))
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
            except Exception:
                failed = False
                for name, identity in reversed(created_files):
                    try:
                        _remove_owned(root / name, identity)
                    except (OSError, ValueError, TransferError):
                        failed = True
                for name in reversed(created_credentials):
                    try:
                        self.credentials.delete_created(name, credentials[name])
                    except Exception:
                        failed = True
                if failed:
                    raise TransferError("rollback_failed") from None
                raise

    def _import_after_exit(self, files, credentials):
        try:
            with transfer_lock():
                deadline = time.monotonic() + self.wait_seconds
                empty_since = None
                while True:
                    count, scanned = self.activity()
                    if count or not scanned:
                        empty_since = None
                    elif empty_since is None:
                        empty_since = time.monotonic()
                    elif time.monotonic() - empty_since >= 2:
                        break
                    if time.monotonic() >= deadline:
                        raise TransferError("client_still_running")
                    time.sleep(.1)
                self._set("importing")
                self._restore(files, credentials)
                self._set("done")
        except Exception as error:
            self._set("error", _failure(error).code)
        finally:
            files.clear()
            credentials.clear()
            self._persist_final()
            self._guard.release()
