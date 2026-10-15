"""Owned MAX Mod profile filesystem adapter; no Credential Manager access."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import uuid

import launch_mod
from quick_reply_store import _lexical_path, _parents, _directory
from session_transfer import _new_stream, _rename_new, _identity, _remove_owned
from session_credentials import account_id

MAX_FILES = 10000
MAX_BYTES = 4 * 1024 * 1024 * 1024
_HELPER_FILES = {"mdata-result.json", "session-transfer-result.json"}
_HELPER_PREFIXES = (".mxsession-", ".session-result-", ".mdata-")


class MDataFileError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def included(path):
    parts = path.split("/")
    if any(part.casefold() in {"logs", "crashdumps"} for part in parts):
        return False
    return (parts[0] not in _HELPER_FILES and not any(parts[0].startswith(p) for p in _HELPER_PREFIXES)
            and not parts[0].lower().endswith((".mdata", ".maxsession")))


def safe_relative(path):
    from mdata_archive import valid_file_path
    return valid_file_path(path) and included(path)


def profile_path(value):
    root = _lexical_path(Path(value) / "config.json").parent
    if root.name != "MXMOD" or any(part.casefold() == "oneme" for part in root.parts):
        raise MDataFileError("unsafe_path")
    return root


@contextmanager
def hold_tree(root):
    with _parents(root / "config.json", create=False) as parent:
        if parent is None:
            raise MDataFileError("invalid_profile")
        with _directory(root) as actual:
            yield actual


def listing(root):
    """Reject links/special objects; include persistent data and ordinary cache.

    Crash/log directories and this helper's transient files are not needed for
    authorization. No file content is opened by this listing.
    """
    result = []
    total = 0
    with hold_tree(root) as actual:
        def visit(directory, relative=""):
            nonlocal total
            with _directory(directory) as physical:
                for entry in sorted(physical.iterdir(), key=lambda p: p.name.casefold()):
                    name = relative + entry.name
                    if not included(name):
                        continue
                    if not safe_relative(name):
                        raise MDataFileError("unsupported_path")
                    info = entry.lstat()
                    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                        raise MDataFileError("unsupported_path")
                    if stat.S_ISDIR(info.st_mode):
                        visit(entry, name + "/")
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        total += info.st_size
                        if total > MAX_BYTES or len(result) >= MAX_FILES:
                            raise MDataFileError("too_large")
                        result.append({"path": name, "size": info.st_size})
                    else:
                        raise MDataFileError("unsupported_path")
        visit(actual)
    return result


def profile_ids(files):
    ids = set()
    for item in files:
        first = item["path"].split("/", 1)[0].casefold()
        match = re.fullmatch(r"(?:auth_|user)([1-9][0-9]{0,18})", first)
        if match and account_id(match.group(1)):
            ids.add(match.group(1))
    if len(ids) > 63:
        raise MDataFileError("too_large")
    return ids


@contextmanager
def source(root, relative):
    if not safe_relative(relative):
        raise MDataFileError("unsafe_path")
    path = root.joinpath(*relative.split("/"))
    with _parents(path, create=False) as parent:
        if parent is None:
            raise MDataFileError("profile_changed")
        # The guard may resolve a verified MSIX alias to its physical parent.
        # Open the same pinned directory that listing enumerated.
        with launch_mod._config_stream(parent / path.name, create=False) as stream:
            yield stream


@contextmanager
def sink(root, relative, size):
    if not safe_relative(relative) or type(size) is not int or not 0 <= size <= MAX_BYTES:
        raise MDataFileError("unsafe_path")
    path = root.joinpath(*relative.split("/"))
    with _parents(path, create=True):
        with _new_stream(path) as stream:
            yield stream


@contextmanager
def atomic_output(path):
    with _parents(path, create=False) as parent:
        if parent is None:
            raise MDataFileError("unsafe_path")
        try:
            path.lstat()
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError()
        temporary = path.with_name(".mdata-" + uuid.uuid4().hex + ".tmp")
        identity = None
        try:
            with _new_stream(temporary) as stream:
                identity = _identity(os.fstat(stream.fileno()))
                yield stream
                stream.flush()
                os.fsync(stream.fileno())
            _rename_new(temporary, path)
            identity = None
        finally:
            if identity is not None and temporary.exists():
                _remove_owned(temporary, identity)


def move_owned(source_path, destination, base, *, expected=None):
    """Move one checked directory only within the explicitly selected parent."""
    source_path, destination, base = map(Path, (source_path, destination, base))
    if (source_path.parent != base or destination.parent != base or source_path == destination
            or not source_path.is_absolute() or not destination.is_absolute()):
        raise MDataFileError("unsafe_path")
    with _parents(source_path, create=False):
        launch_mod._check_path(source_path, directory=True)
        if source_path.resolve(strict=True).parent != base.resolve(strict=True):
            raise MDataFileError("unsafe_path")
        if expected is not None and _identity(source_path.lstat()) != expected:
            raise MDataFileError("rollback_failed")
        try:
            destination.lstat()
        except FileNotFoundError:
            pass
        else:
            raise MDataFileError("profile_changed")
        source_path.rename(destination)


def remove_staging(root, base, *, expected=None):
    """Delete only a newly created, verified staging tree; never active data."""
    root, base = Path(root), Path(base)
    if (root.parent != base or not root.name.startswith(".MXMOD-MData-stage-")
            or not root.is_absolute() or root.resolve(strict=True).parent != base.resolve(strict=True)):
        raise MDataFileError("unsafe_path")
    # Validate the complete tree before any recursive removal, without links.
    with _parents(root, create=False):
        if expected is not None and _identity(root.lstat()) != expected:
            raise MDataFileError("rollback_failed")
        def checked(path):
            launch_mod._check_path(path, directory=True)
            for item in path.iterdir():
                info = item.lstat()
                if stat.S_ISDIR(info.st_mode):
                    checked(item)
                else:
                    launch_mod._check_path(item, directory=False)
        checked(root)
        import shutil
        shutil.rmtree(root)
