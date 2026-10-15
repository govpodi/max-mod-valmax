"""Bounded, non-secret MData failure metadata; no storage or native API access.

Exception messages, args, repr, locals, source lines and full paths are never
read. Only builtin exception slots and immutable traceback/code attributes are
examined. validate() permits only this exact bounded ASCII JSON structure.
"""
from datetime import datetime, timezone
import json
import re


PHASES = frozenset((
    "lock", "wait_closed", "profile_listing", "credential_inventory",
    "credential_read", "storage_validate", "archive_open", "archive_encrypt",
    "source_open", "source_read", "profile_verify", "credential_verify",
    "archive_publish", "staging_create", "archive_decrypt", "incoming_validate",
    "config_prepare", "backup", "recovery_marker", "restore", "add", "cleanup", "complete",
))
TRUSTED_MODULES = frozenset((
    "mdata_service.py", "mdata_files.py", "mdata_credentials.py", "mdata_archive.py",
    "mdata_recovery.py", "mdata_recovery_state.py", "session_transfer.py",
    "session_archive.py", "session_credentials.py", "quick_reply_store.py",
    "launch_mod.py", "privacy_server.py",
))
_BUILTIN_ERRORS = (
    OSError, PermissionError, FileNotFoundError, FileExistsError, TimeoutError,
    IsADirectoryError, NotADirectoryError, BlockingIOError, ValueError, TypeError,
    RuntimeError, KeyError, IndexError, EOFError, MemoryError, UnicodeError,
    UnicodeDecodeError, UnicodeEncodeError, OverflowError, ZeroDivisionError,
)
_PROJECT_ERRORS = {
    "MDataError": "mdata_service", "MDataFileError": "mdata_files",
    "MDataArchiveError": "mdata_archive", "CredentialError": "session_credentials",
    "SessionArchiveError": "session_archive", "TransferError": "session_transfer",
    "RecoveryRequired": "mdata_recovery_state", "QuickReplyStoreError": "quick_reply_store",
}
ROOT_ERRORS = frozenset(error.__name__ for error in _BUILTIN_ERRORS) | frozenset(_PROJECT_ERRORS) | {"other"}
MAX_BYTES = 1200
MAX_CHAIN = 6
_MAX_TRACEBACK = 256
_KEYS = frozenset(("schema", "timestamp", "phase", "operationId", "rootError", "errno", "winerror", "frames"))
_FRAME_KEYS = frozenset(("module", "function", "line"))
_OPERATION = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z", re.ASCII)
_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z", re.ASCII)
_EXCEPTION_SLOTS = {name: vars(BaseException)[name] for name in
                    ("__cause__", "__context__", "__traceback__", "__dict__")}
_TYPE_NAME = vars(type)["__name__"]
_TYPE_NAMESPACE = vars(type)["__dict__"]
_TYPE_MRO = vars(type)["__mro__"]


def _inherits(value, base):
    # isinstance() may consult a hostile object's __class__ when a check fails.
    try:
        return any(ancestor is base for ancestor in _TYPE_MRO.__get__(type(value), type))
    except Exception:
        return False


def _exception_slot(error, name):
    # Calling the builtin descriptor avoids hostile subclass __getattribute__
    # overrides and properties with these names.
    try:
        return _EXCEPTION_SLOTS[name].__get__(error, BaseException)
    except Exception:
        return None


def _root(error):
    if not _inherits(error, BaseException):
        return None
    seen = {id(error)}
    for _index in range(MAX_CHAIN - 1):
        cause = _exception_slot(error, "__cause__")
        context = _exception_slot(error, "__context__")
        following = cause if _inherits(cause, BaseException) else context
        if not _inherits(following, BaseException) or id(following) in seen:
            break
        seen.add(id(following))
        error = following
    return error


def _root_class(error):
    error_type = type(error)
    for known in _BUILTIN_ERRORS:
        if error_type is known:
            return known.__name__
    try:
        name = _TYPE_NAME.__get__(error_type, type)
        module = _TYPE_NAMESPACE.__get__(error_type, type).get("__module__")
        if type(name) is str and type(module) is str and _PROJECT_ERRORS.get(name) == module:
            return name
    except Exception:
        pass
    return "other"


def _numeric_slot(error, name):
    value = None
    if _inherits(error, OSError):
        descriptor = vars(OSError).get(name)
        if descriptor is not None:
            try:
                value = descriptor.__get__(error, OSError)
            except Exception:
                pass
    else:
        namespace = _exception_slot(error, "__dict__")
        if type(namespace) is dict:
            value = dict.get(namespace, name)
    return value if type(value) is int and 0 <= value <= 65535 else None


def _frames(error):
    result, seen = [], set()
    traceback = _exception_slot(error, "__traceback__")
    for _index in range(_MAX_TRACEBACK):
        if traceback is None or id(traceback) in seen:
            break
        seen.add(id(traceback))
        try:
            code = traceback.tb_frame.f_code
            filename, function, line = code.co_filename, code.co_name, traceback.tb_lineno
            module = filename.replace("\\", "/").rsplit("/", 1)[-1]
            if (module in TRUSTED_MODULES and type(function) is str
                    and _IDENTIFIER.fullmatch(function) and type(line) is int and 1 <= line <= 1000000):
                result.append({"module": module, "function": function, "line": line})
                if len(result) > 4:
                    del result[0]
            traceback = traceback.tb_next
        except Exception:
            break
    return result


def _exact_keys(value, expected):
    return type(value) is dict and len(value) == len(expected) and all(type(key) is str for key in value) and set(value) == expected


def _number(value):
    return value is None or type(value) is int and 0 <= value <= 65535


def validate(value):
    """Validate untrusted saved metadata before printing; never formats its input."""
    try:
        if not _exact_keys(value, _KEYS):
            return False
        if type(value["schema"]) is not int or value["schema"] != 1:
            return False
        if type(value["timestamp"]) is not str or not _UTC.fullmatch(value["timestamp"]):
            return False
        datetime.fromisoformat(value["timestamp"][:-1])
        if type(value["phase"]) is not str or value["phase"] not in PHASES:
            return False
        if type(value["operationId"]) is not str or not _OPERATION.fullmatch(value["operationId"]):
            return False
        if type(value["rootError"]) is not str or value["rootError"] not in ROOT_ERRORS:
            return False
        if not _number(value["errno"]) or not _number(value["winerror"]):
            return False
        if type(value["frames"]) is not list or len(value["frames"]) > 4:
            return False
        for frame in value["frames"]:
            if not _exact_keys(frame, _FRAME_KEYS):
                return False
            if type(frame["module"]) is not str or frame["module"] not in TRUSTED_MODULES:
                return False
            if type(frame["function"]) is not str or not _IDENTIFIER.fullmatch(frame["function"]):
                return False
            if type(frame["line"]) is not int or not 1 <= frame["line"] <= 1000000:
                return False
        return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")) <= MAX_BYTES
    except Exception:
        return False


def failure(error, phase, operation_id):
    """Return bounded diagnostic metadata; invalid caller phase/id raises fixed ValueError."""
    if type(phase) is not str or phase not in PHASES:
        raise ValueError("Invalid MData diagnostic phase")
    if type(operation_id) is not str or not _OPERATION.fullmatch(operation_id):
        raise ValueError("Invalid MData diagnostic operation ID")
    root = _root(error)
    return {
        "schema": 1,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "phase": phase,
        "operationId": operation_id,
        "rootError": _root_class(root),
        "errno": _numeric_slot(root, "errno"),
        "winerror": _numeric_slot(root, "winerror"),
        "frames": _frames(root),
    }
