"""Best-effort durable native fatal metadata; never reads MAX profile data.

Called by the Python helper, never by a native JS callback. Only a fixed report
in an existing .mxmod-helper-temp directory can be written. It is diagnostic
data, not a command or startup/recovery instruction.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import uuid

import launch_mod
from quick_reply_store import _lexical_path, _parents, _writer_lock
from session_transfer import _identity, _new_stream, _remove_owned, _rename_new

MAX_EVENTS = 4
MAX_FRAMES = 24
MAX_BYTES = 16384
_MODULE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\Z", re.ASCII)
_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z", re.ASCII)


def _keys(value, keys):
    return (type(value) is dict and len(value) == len(keys)
            and all(type(key) is str for key in value) and set(value) == set(keys))


def _frames(value):
    if type(value) is not list or len(value) > MAX_FRAMES:
        raise ValueError("invalid_native_crash_metadata")
    result = []
    for frame in value:
        if (not _keys(frame, ("module", "rva"))
                or type(frame["module"]) is not str or not _MODULE.fullmatch(frame["module"])
                or type(frame["rva"]) is not int or not 0 <= frame["rva"] < 2**32):
            raise ValueError("invalid_native_crash_metadata")
        result.append({"module": frame["module"], "rva": frame["rva"]})
    return result


def serialize(value):
    """Pure strict schema validation and bounded canonical ASCII JSON bytes."""
    if (not _keys(value, ("schema", "events")) or type(value["schema"]) is not int
            or value["schema"] != 1 or type(value["events"]) is not list
            or not 1 <= len(value["events"]) <= MAX_EVENTS):
        raise ValueError("invalid_native_crash_metadata")
    events = []
    for event in value["events"]:
        if (not _keys(event, ("timestampUTC", "role", "pid", "frames"))
                or type(event["timestampUTC"]) is not str or not _UTC.fullmatch(event["timestampUTC"])
                or type(event["role"]) is not str or event["role"] not in ("main", "service")
                or not (event["pid"] is None or type(event["pid"]) is int and 1 <= event["pid"] < 2**32)):
            raise ValueError("invalid_native_crash_metadata")
        try:
            datetime.fromisoformat(event["timestampUTC"][:-1])
        except ValueError:
            raise ValueError("invalid_native_crash_metadata") from None
        events.append({"timestampUTC": event["timestampUTC"], "role": event["role"],
                       "pid": event["pid"], "frames": _frames(event["frames"])})
    raw = json.dumps({"schema": 1, "events": events}, ensure_ascii=True,
                     sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    if len(raw) > MAX_BYTES:
        raise ValueError("invalid_native_crash_metadata")
    return raw


def validate(value):
    try:
        serialize(value)
        return True
    except Exception:
        return False


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid_native_crash_metadata")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("invalid_native_crash_metadata")


def _existing(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None, []
    launch_mod._check_path(path, directory=False)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
        raise ValueError("unsafe_native_crash_path")
    identity = _identity(info)
    with launch_mod._config_stream(path, create=False) as stream:
        opened = os.fstat(stream.fileno())
        if _identity(opened) != identity or opened.st_nlink != 1:
            raise ValueError("unsafe_native_crash_path")
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("invalid_native_crash_metadata")
    value = json.loads(raw.decode("utf8"), object_pairs_hook=_object, parse_constant=_constant)
    serialize(value)
    return identity, value["events"]


def _publish(temporary, path, previous):
    # Cooperative writers share a named lock. Directory guards prevent ancestor
    # replacement. Recheck the fixed target immediately before atomic publish.
    current, _events = _existing(path)
    if current != previous:
        raise ValueError("native_crash_report_changed")
    if previous is None:
        _rename_new(temporary, path)  # refuses a destination appearing meanwhile
    else:
        os.replace(temporary, path)


class NativeCrashDiagnostics:
    def __init__(self, helper_directory):
        try:
            path = _lexical_path(Path(helper_directory) / "native-crash.json")
        except Exception:
            raise ValueError("unsafe_native_crash_path") from None
        if (path.parent.name != ".mxmod-helper-temp"
                or any(part.casefold() in ("mxmod", "oneme") for part in path.parent.parts)):
            raise ValueError("unsafe_native_crash_path")
        self.path = path

    def record(self, frames, *, role, pid=None):
        """Append at most four sanitized events; all ordinary failures return False.

        No directory is created. An absent, unsafe, malformed or unwritable
        destination cannot affect the original fatal flow or public job result.
        """
        temporary = identity = None
        try:
            event = {"timestampUTC": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "role": role, "pid": pid, "frames": frames}
            serialize({"schema": 1, "events": [event]})
            with _parents(self.path, create=False) as parent:
                if parent is None or os.path.normcase(str(parent)) != os.path.normcase(str(self.path.parent)):
                    return False
                with _writer_lock(self.path):
                    previous, events = _existing(self.path)
                    raw = serialize({"schema": 1, "events": (events + [event])[-MAX_EVENTS:]})
                    temporary = self.path.with_name(".native-crash-" + uuid.uuid4().hex + ".tmp")
                    try:
                        with _new_stream(temporary) as stream:
                            identity = _identity(os.fstat(stream.fileno()))
                            if stream.write(raw) != len(raw):
                                raise OSError("native_crash_short_write")
                            stream.flush()
                            os.fsync(stream.fileno())
                        _publish(temporary, self.path, previous)
                        temporary = identity = None
                    finally:
                        if temporary is not None and identity is not None:
                            _remove_owned(temporary, identity)
            return True
        except Exception:
            return False
