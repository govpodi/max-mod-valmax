"""Global optimization preferences and a bool-only snapshot for the next launch.

The requested file belongs to MXMOD. The active file beside max.exe contains
only public flags, is written before launch, and is never copied into packages.
"""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import uuid

from launch_mod import _check_path, _config_stream
from quick_reply_store import QuickReplyStore, QuickReplyStoreError, _writer_lock

MAX_STORE_BYTES = 4096
ACTIVE_FILENAME = "optimization-active.json"
KEYS = ("enabled", "smoothWheel", "listScheduling", "listPrefetch", "readyCache",
        "historyCache", "historyBookkeeping", "photoDecodeResize", "windowPosition",
        "hiddenPageWork", "profileRows")
_REVISION = re.compile(r"[0-9a-f]{64}\Z")
_CODES = frozenset(("invalid_settings", "invalid_revision", "revision_conflict",
                    "invalid_store", "unsafe_path", "store_busy", "io_error"))


class OptimizationSettingsError(Exception):
    def __init__(self, code):
        if code not in _CODES:
            raise ValueError("Unknown optimization error")
        self.code = code
        super().__init__(code)


def defaults():
    return {key: True for key in KEYS}


def validate_settings(value, code="invalid_settings"):
    if (type(value) is not dict or set(value) != set(KEYS)
            or any(type(item) is not bool for item in value.values())):
        raise OptimizationSettingsError(code)
    return {key: value[key] for key in KEYS}


def canonical(settings):
    return json.dumps({"schema": 1, "settings": validate_settings(settings)},
                      sort_keys=True, separators=(",", ":")).encode("utf8")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise OptimizationSettingsError("invalid_store")
        result[key] = value
    return result


def parse(data):
    if len(data) > MAX_STORE_BYTES:
        raise OptimizationSettingsError("invalid_store")
    try:
        value = json.loads(data.decode("utf8"), object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, RecursionError):
        raise OptimizationSettingsError("invalid_store") from None
    if (type(value) is not dict or set(value) != {"schema", "settings"}
            or type(value["schema"]) is not int or value["schema"] != 1):
        raise OptimizationSettingsError("invalid_store")
    return validate_settings(value["settings"], "invalid_store")


def _read(path, *, missing_defaults=True):
    try:
        _check_path(path, directory=False)
        with _config_stream(path, create=False) as stream:
            return parse(stream.read(MAX_STORE_BYTES + 1))
    except FileNotFoundError:
        return defaults() if missing_defaults else None


def _atomic_write(path, payload, *, create_only=False):
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    identity = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(temporary, flags, 0o600)
        with os.fdopen(fd, "wb") as stream:
            info = os.fstat(stream.fileno())
            identity = info.st_dev, info.st_ino
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OptimizationSettingsError("unsafe_path")
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _check_path(temporary, directory=False)
        info = temporary.lstat()
        if (info.st_dev, info.st_ino) != identity:
            raise OptimizationSettingsError("unsafe_path")
        # Preserve the same no-links/path validation as the existing stores.
        _read(path)
        if create_only:
            if os.name == "nt":
                # Unlike os.replace, Windows os.rename refuses an existing file.
                os.rename(temporary, path)
            else:
                # A hard link publishes exclusively on POSIX; drop the staging name.
                os.link(temporary, path, follow_symlinks=False)
                temporary.unlink()
        else:
            os.replace(temporary, path)
        identity = None
    finally:
        if identity is not None:
            try:
                if not (create_only and os.name != "nt"):
                    _check_path(temporary, directory=False)
                info = temporary.lstat()
                if (info.st_dev, info.st_ino) == identity and stat.S_ISREG(info.st_mode):
                    # A POSIX create-only publication briefly gives this inode
                    # two links. Removing our known staging name is still safe.
                    temporary.unlink()
            except FileNotFoundError:
                pass


class OptimizationSettingsStore:
    def __init__(self, path):
        try:
            self._guard = QuickReplyStore(path)
        except QuickReplyStoreError as error:
            raise OptimizationSettingsError(error.code) from None
        self.path = self._guard.path
        self._active = defaults()
        self._activated = False
        self._activation_lock = threading.RLock()

    @contextmanager
    def _operation(self, *, create):
        try:
            with self._guard._operation(create=create) as path:
                yield path
        except QuickReplyStoreError as error:
            raise OptimizationSettingsError(error.code if error.code in _CODES else "io_error") from None

    def _value(self, settings):
        settings = validate_settings(settings, "invalid_store")
        with self._activation_lock:
            active = dict(self._active)
        return {"schema": 1, "available": True,
                "revision": hashlib.sha256(canonical(settings)).hexdigest(),
                "settings": settings, "appliedSettings": active,
                "restartRequired": settings != active}

    def snapshot(self):
        with self._operation(create=False) as path:
            return self._value(defaults() if path is None else _read(path))

    def replace(self, expected_revision, settings):
        if type(expected_revision) is not str or not _REVISION.fullmatch(expected_revision):
            raise OptimizationSettingsError("invalid_revision")
        clean = validate_settings(settings)
        with self._operation(create=True) as path:
            try:
                with _writer_lock(path):
                    if self._value(_read(path))["revision"] != expected_revision:
                        raise OptimizationSettingsError("revision_conflict")
                    _atomic_write(path, canonical(clean))
            except QuickReplyStoreError as error:
                raise OptimizationSettingsError(error.code if error.code in _CODES else "io_error") from None
            return self._value(clean)

    def activate(self, runtime_directory, *, existing_client=False):
        """Fix this helper's active settings once; never reconfigure a live UI."""
        if type(existing_client) is not bool:
            raise OptimizationSettingsError("invalid_settings")
        with self._activation_lock:
            if self._activated:
                return dict(self._active)
            try:
                active_guard = QuickReplyStore(Path(runtime_directory) / ACTIVE_FILENAME)
                if existing_client:
                    with active_guard._operation(create=False) as active_path:
                        clean = None if active_path is None else _read(active_path, missing_defaults=False)
                    if clean is None:
                        with active_guard._operation(create=True) as active_path:
                            with _writer_lock(active_path):
                                # Recheck after locking: another helper may have published it.
                                clean = _read(active_path, missing_defaults=False)
                                if clean is None:
                                    clean = self.snapshot()["settings"]
                                    try:
                                        _atomic_write(active_path, canonical(clean), create_only=True)
                                    except FileExistsError:
                                        clean = _read(active_path, missing_defaults=False)
                                        if clean is None:
                                            raise OptimizationSettingsError("io_error") from None
                else:
                    with active_guard._operation(create=True) as active_path:
                        clean = self.snapshot()["settings"]
                        with _writer_lock(active_path):
                            _atomic_write(active_path, canonical(clean))
                self._active, self._activated = dict(clean), True
                return dict(clean)
            except QuickReplyStoreError as error:
                raise OptimizationSettingsError(error.code if error.code in _CODES else "io_error") from None


def child_environment(environment):
    """Permit the fixed local JSON read in this native child, not globally."""
    return {**environment, "QML_XHR_ALLOW_FILE_READ": "1"}


def native_client_running(executable):
    """Fresh names/path metadata only; never attach or read process memory."""
    if os.name != "nt":
        raise OptimizationSettingsError("io_error")
    class ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes, api.CreateToolhelp32Snapshot.restype = (wintypes.DWORD, wintypes.DWORD), wintypes.HANDLE
    api.Process32FirstW.argtypes = api.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
    api.Process32FirstW.restype = api.Process32NextW.restype = wintypes.BOOL
    api.OpenProcess.argtypes, api.OpenProcess.restype = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD), wintypes.HANDLE
    api.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    api.QueryFullProcessImageNameW.restype = wintypes.BOOL
    api.CloseHandle.argtypes, api.CloseHandle.restype = (wintypes.HANDLE,), wintypes.BOOL
    snapshot = api.CreateToolhelp32Snapshot(2, 0)
    if snapshot in (None, 0, ctypes.c_void_p(-1).value):
        raise OptimizationSettingsError("io_error")
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        success = api.Process32FirstW(snapshot, ctypes.byref(entry))
        while success:
            if entry.szExeFile.casefold() == "max.exe":
                process = api.OpenProcess(0x1000, False, entry.th32ProcessID)
                if not process:
                    if ctypes.get_last_error() != 87:  # The candidate may have exited.
                        raise OptimizationSettingsError("io_error")
                else:
                    try:
                        path, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
                        if not api.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
                            raise OptimizationSettingsError("io_error")
                        if Path(path.value).resolve() == Path(executable).resolve():
                            return True
                    finally:
                        api.CloseHandle(process)
            ctypes.set_last_error(0)
            success = api.Process32NextW(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:
            raise OptimizationSettingsError("io_error")
        return False
    finally:
        api.CloseHandle(snapshot)
