"""Non-secret durable guard against starting a half-restored MAX Mod."""
import json
from pathlib import Path
import os
import uuid

import launch_mod
from quick_reply_store import _parents, _directory
from session_transfer import _new_stream


class RecoveryRequired(Exception):
    code = "recovery_required"


def root_candidate(local_appdata=None):
    root = launch_mod._local_root(local_appdata) / "MXMOD"
    with _parents(root, create=False) as parent:
        if parent is None:
            return root
        if root.exists():
            with launch_mod._hold_directory(root) as actual:
                return actual
        return parent / "MXMOD"


def _path(root):
    root = Path(root)
    if root.name != "MXMOD" or not root.is_absolute():
        raise RecoveryRequired()
    return root.parent / "MXMOD-MData-Recovery.json"


def read(root):
    path = _path(root)
    try:
        with _parents(path, create=False) as parent:
            if parent is None:
                return None
            try:
                path.lstat()
                with launch_mod._config_stream(path, create=False) as stream:
                    data = stream.read(16385)
            except FileNotFoundError:
                return None
        value = json.loads(data, object_pairs_hook=launch_mod._unique_object,
                           parse_constant=launch_mod._invalid_json_constant)
        if (len(data) > 16384 or type(value) is not dict
                or set(value) != {"schema", "operationId", "profileRoot", "backupFile"}
                or type(value["schema"]) is not int or value["schema"] != 1
                or type(value["operationId"]) is not str or len(value["operationId"]) != 32
                or any(c not in "0123456789abcdef" for c in value["operationId"])
                or value["profileRoot"] != str(root) or type(value["backupFile"]) is not str):
            raise RecoveryRequired()
        backup = Path(value["backupFile"])
        if (backup.parent != Path(root).parent / "MXMOD-MData-Backups"
                or not backup.is_absolute() or backup.suffix.lower() != ".mdata"):
            raise RecoveryRequired()
        return value
    except (ValueError, OSError, TypeError):
        raise RecoveryRequired() from None


def write(root, backup):
    path = _path(root)
    backup = Path(backup)
    if backup.parent != Path(root).parent / "MXMOD-MData-Backups" or backup.suffix != ".mdata":
        raise RecoveryRequired()
    if read(root) is not None:
        raise RecoveryRequired()
    value = {"schema": 1, "operationId": uuid.uuid4().hex,
             "profileRoot": str(root), "backupFile": str(backup)}
    data = json.dumps(value, separators=(",", ":")).encode("utf8")
    with _parents(path, create=False):
        with _new_stream(path) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    return value


def clear(root, expected):
    path = _path(root)
    with _parents(path, create=False):
        current = read(root)
        if current is None:
            return
        if current != expected:
            raise RecoveryRequired()
        launch_mod._check_path(path, directory=False)
        path.unlink()


def ensure_clear(root):
    if read(root) is not None:
        raise RecoveryRequired()
