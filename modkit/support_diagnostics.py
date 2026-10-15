"""Explicit local support report built from an exact, bounded allowlist.

No profile, credential, log, message, network, process inventory, clock, host
name or environment collection. Callers supply existing public snapshots.
Only the user-selected new output file is written; nothing is sent anywhere.
"""
from __future__ import annotations

import json
import os
import re
import sys
from urllib.parse import unquote, urlsplit
import uuid

from startup_status import CODES, ROLES, STAGES, STATES
from privacy_status import ERRORS as OBSERVER_ERRORS

MAX_BYTES = 16384
MAX_COUNT = 2**53 - 1
VERSION_KEYS = ("valmax", "max", "windows", "python")
GHOST_FLAGS = ("enabled", "hideTyping", "hideRead", "hideOnline", "hideStories")
OPTIMIZATION_FLAGS = ("enabled", "smoothWheel", "listScheduling", "listPrefetch",
    "readyCache", "historyCache", "historyBookkeeping", "photoDecodeResize",
    "windowPosition", "hiddenPageWork", "profileRows")
OBSERVATION_COUNTS = ("seconds", "blockedCalls", "tracerHooks", "transportHooks", "processes")
DASHBOARD_COUNTS = ("observedPackets", "observedBytes", "parsedPackets", "headerOnlyPackets",
    "excludedPackets", "excludedBytes", "requestTypes", "excludedRequestTypes")
ERRORS = frozenset(("invalid_request", "unsafe_path", "file_exists", "io_error", "invalid_report"))
_VERSION = re.compile(r"[0-9]{1,5}(?:\.[0-9]{1,5}){1,3}\Z", re.ASCII)
_TOP_KEYS = frozenset(("schema", "versions", "startup", "flags", "counters", "errors"))


class SupportDiagnosticsError(ValueError):
    def __init__(self, code):
        self.code = code if type(code) is str and code in ERRORS else "io_error"
        super().__init__(self.code)


def _mapping(value):
    # Do not execute subclass get/items/str methods on untrusted observations.
    return value if type(value) is dict else {}


def _boolean(value):
    return value if type(value) is bool else None


def _count(value):
    return value if type(value) is int and 0 <= value <= MAX_COUNT else None


def _version(value):
    return value if type(value) is str and _VERSION.fullmatch(value) else None


def _flags(value, keys):
    settings = _mapping(_mapping(value).get("settings"))
    return {key: _boolean(settings.get(key)) for key in keys}


def _startup(value):
    value = _mapping(value)
    if (type(value.get("role")) is not str or value["role"] not in ROLES
            or type(value.get("stage")) is not str or value["stage"] not in STAGES
            or type(value.get("state")) is not str or value["state"] not in STATES
            or type(value.get("code")) is not str
            or value["code"] not in (CODES if value["state"] == "error" else {"none"})):
        return None
    return {key: value[key] for key in ("role", "stage", "state", "code")}


def build_report(*, versions=None, startup=None, privacy=None, ghost=None, optimization=None):
    """Copy only named scalar fields; arbitrary snapshot extras are never visited.

    Unknown or malformed values become null. A current receipt loses its
    operation ID; privacy IPs, rows, frames and text are not part of the schema.
    """
    versions, privacy = _mapping(versions), _mapping(privacy)
    observation = _mapping(privacy.get("observation"))
    dashboard = _mapping(privacy.get("dashboard"))
    errors = _mapping(privacy.get("errors"))
    return {
        "schema": 1,
        "versions": {key: _version(versions.get(key)) for key in VERSION_KEYS},
        "startup": _startup(startup),
        "flags": {
            "verified": _boolean(privacy.get("verified")),
            "observationActive": _boolean(observation.get("active")),
            "requestTypesComplete": _boolean(dashboard.get("requestTypesComplete")),
            "ghost": _flags(ghost, GHOST_FLAGS),
            "optimization": _flags(optimization, OPTIMIZATION_FLAGS),
        },
        "counters": {
            "observation": {key: _count(observation.get(key)) for key in OBSERVATION_COUNTS},
            "dashboard": {key: _count(dashboard.get(key)) for key in DASHBOARD_COUNTS},
        },
        "errors": {key: _count(errors.get(key, 0)) if type(privacy.get("errors")) is dict else None
                   for key in sorted(OBSERVER_ERRORS)},
    }


def _keys(value, expected):
    return type(value) is dict and set(value) == set(expected) and all(type(key) is str for key in value)


def _nullable_mapping(value, keys, check):
    return _keys(value, keys) and all(item is None or check(item) for item in value.values())


def serialize(value):
    """Reject extra fields instead of serializing a broader source snapshot."""
    if not _keys(value, _TOP_KEYS) or type(value["schema"]) is not int or value["schema"] != 1:
        raise SupportDiagnosticsError("invalid_report")
    flags, counters = value["flags"], value["counters"]
    if (not _nullable_mapping(value["versions"], VERSION_KEYS, lambda item: _version(item) is not None)
            or (value["startup"] is not None and (not _keys(value["startup"], ("role", "stage", "state", "code"))
                                                  or _startup(value["startup"]) != value["startup"]))
            or not _keys(flags, ("verified", "observationActive", "requestTypesComplete", "ghost", "optimization"))
            or any(flags[key] is not None and type(flags[key]) is not bool for key in ("verified", "observationActive", "requestTypesComplete"))
            or not _nullable_mapping(flags["ghost"], GHOST_FLAGS, lambda item: type(item) is bool)
            or not _nullable_mapping(flags["optimization"], OPTIMIZATION_FLAGS, lambda item: type(item) is bool)
            or not _keys(counters, ("observation", "dashboard"))
            or not _nullable_mapping(counters["observation"], OBSERVATION_COUNTS, lambda item: _count(item) is not None)
            or not _nullable_mapping(counters["dashboard"], DASHBOARD_COUNTS, lambda item: _count(item) is not None)
            or not _nullable_mapping(value["errors"], OBSERVER_ERRORS, lambda item: _count(item) is not None)):
        raise SupportDiagnosticsError("invalid_report")
    raw = (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("ascii")
    if len(raw) > MAX_BYTES:
        raise SupportDiagnosticsError("invalid_report")
    return raw


def runtime_versions(valmax_version, max_version):
    """Only numeric software versions; no platform strings or host identifiers."""
    windows = None
    if sys.platform == "win32":
        value = sys.getwindowsversion()
        windows = f"{value.major}.{value.minor}.{value.build}"
    return {"valmax": _version(valmax_version), "max": _version(max_version), "windows": windows,
            "python": ".".join(str(item) for item in sys.version_info[:3])}


def selected_file(value):
    """Resolve a QML SaveFile URL without ever echoing its content in errors."""
    from quick_reply_store import _lexical_path, QuickReplyStoreError
    try:
        if type(value) is not str or not 1 <= len(value) <= 32768 or "\x00" in value:
            raise ValueError()
        parsed = urlsplit(value)
        if parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError()
        decoded = unquote(parsed.path, errors="strict")
        if os.name == "nt" and re.match(r"/[A-Za-z]:/", decoded):
            decoded = decoded[1:]
        path = _lexical_path(decoded)
        if path.suffix.lower() != ".json":
            raise ValueError()
        return path
    except (ValueError, OSError, UnicodeError, QuickReplyStoreError):
        raise SupportDiagnosticsError("unsafe_path") from None


def _publish_stream(stream, source, destination, data):
    """Publish the open Windows file by handle, with no destination replacement.

    The POSIX test fallback exclusively creates the final file from allowlisted
    bytes and cleans it on failure; visibility during its write is not atomic.
    It never publishes a temporary pathname another process could replace.
    """
    from session_transfer import _new_stream, _identity, _remove_owned
    if source.parent != destination.parent:
        raise SupportDiagnosticsError("unsafe_path")
    identity = _identity(os.fstat(stream.fileno()))
    if os.name != "nt":
        # Reject a changed temporary entry, then use only the serialized bytes.
        # A later pathname swap cannot substitute private content in the report.
        if _identity(source.lstat()) != identity:
            raise SupportDiagnosticsError("unsafe_path")
        with _new_stream(destination) as output:
            if output.write(data) != len(data):
                raise OSError()
            output.flush()
            os.fsync(output.fileno())
            _remove_owned(source, identity)
        return

    import ctypes
    import msvcrt
    from ctypes import wintypes
    class RenameInfo(ctypes.Structure):
        _fields_ = [("replace", wintypes.BOOL), ("root", wintypes.HANDLE),
                    ("length", wintypes.DWORD), ("name", wintypes.WCHAR * 1)]
    class IOStatus(ctypes.Structure):
        _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_size_t)]
    encoded = destination.name.encode("utf-16le")
    size = max(ctypes.sizeof(RenameInfo), RenameInfo.name.offset + len(encoded))
    buffer = ctypes.create_string_buffer(size)
    info = RenameInfo.from_buffer(buffer)
    info.replace, info.root, info.length = False, None, len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.name.offset, encoded, len(encoded))
    native = ctypes.WinDLL("ntdll", use_last_error=True)
    native.NtSetInformationFile.argtypes = (wintypes.HANDLE, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_int)
    native.NtSetInformationFile.restype = wintypes.LONG
    # A basename with NULL RootDirectory renames within the pinned parent.
    # _new_stream retains DELETE access and share=0 until publication finishes.
    status = native.NtSetInformationFile(msvcrt.get_osfhandle(stream.fileno()),
        ctypes.byref(IOStatus()), buffer, size, 10)
    if status < 0:
        native.RtlNtStatusToDosError.argtypes = (wintypes.LONG,)
        native.RtlNtStatusToDosError.restype = wintypes.ULONG
        code = native.RtlNtStatusToDosError(status)
        if code in (80, 183):
            raise FileExistsError()
        raise ctypes.WinError(code)


def save_report(file_url, report):
    """Create a chosen new file; Windows publication is atomic and by handle."""
    from quick_reply_store import _parents, QuickReplyStoreError
    from session_transfer import _new_stream, _identity, _remove_owned
    data, path = serialize(report), selected_file(file_url)
    temporary, identity = None, None
    try:
        with _parents(path, create=False) as parent:
            if parent is None:
                raise SupportDiagnosticsError("unsafe_path")
            try:
                path.lstat()
            except FileNotFoundError:
                pass
            else:
                raise SupportDiagnosticsError("file_exists")
            temporary = path.with_name(".valmax-support-" + uuid.uuid4().hex + ".tmp")
            with _new_stream(temporary) as stream:
                identity = _identity(os.fstat(stream.fileno()))
                if stream.write(data) != len(data):
                    raise OSError()
                stream.flush()
                os.fsync(stream.fileno())
                _publish_stream(stream, temporary, path, data)
                identity = None
    except SupportDiagnosticsError:
        raise
    except FileExistsError:
        raise SupportDiagnosticsError("file_exists") from None
    except (QuickReplyStoreError, ValueError):
        raise SupportDiagnosticsError("unsafe_path") from None
    except Exception:
        raise SupportDiagnosticsError("io_error") from None
    finally:
        if temporary is not None and identity is not None:
            try:
                _remove_owned(temporary, identity)
            except Exception:
                pass
    return {"schema": 1, "saved": True}


def _snapshot(callback):
    if callback is None:
        return None
    try:
        return callback()
    except Exception:
        # Do not inspect the exception or turn a failed optional field into a
        # fake successful status. The unavailable values remain null.
        return None


class SupportDiagnosticsService:
    def __init__(self, *, valmax_version, max_version, privacy, startup=None, ghost=None, optimization=None):
        self.versions = runtime_versions(valmax_version, max_version)
        self.privacy, self.startup, self.ghost, self.optimization = privacy, startup, ghost, optimization

    def preview(self):
        report = build_report(versions=self.versions, startup=_snapshot(self.startup),
            privacy=_snapshot(self.privacy), ghost=_snapshot(self.ghost), optimization=_snapshot(self.optimization))
        serialize(report)
        return {"schema": 1, "report": report}

    def export(self, file_url):
        return save_report(file_url, self.preview()["report"])
