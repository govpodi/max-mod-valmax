"""Compact, explicit native login transfer; never copy history or caches.

Export discovers only direct auth_<ID> filenames and reads exact access-token
targets. Import inventories names only, then reads native access tokens only.
All existing accounts, local databases, common keys and settings are preserved.
Logged-out per-account metadata and directories remain in place when their
access token is absent; they do not constitute an active-account collision.
The caller holds the transfer mutex. Export verifies a bounded live snapshot;
import requires that owned clients are closed before changing authorization.

Ordinary exceptions roll back only objects this import created. A process or
power failure can leave a valid subset of newly added accounts: tokens are
committed after their optional metadata, and retry completes matching metadata
left before a token commit. Server acceptance on another PC is not established
by this filesystem adapter.
"""
from __future__ import annotations

import base64
import binascii
from contextlib import ExitStack
import hmac
import os
from pathlib import Path
import re
import stat

import launch_mod
import mdata_files
from quick_reply_store import _parents, _directory, _lexical_path
from session_credentials import account_id, MAX_BLOB
from session_transfer import _new_stream, _identity, _remove_owned

MAX_AUTH_BYTES = 256 * 1024
MAX_CANDIDATES = 63
MAX_ACCOUNTS = 3
_AUTH = re.compile(r"auth_([1-9][0-9]{0,18})\Z")
_TOKEN = re.compile(r"access_token_([1-9][0-9]{0,18})@MXMOD\Z")


def _fail(code):
    # Lazy import keeps the service free to import this adapter.
    from mdata_service import MDataError
    raise MDataError(code) from None


def _auth_id(name):
    match = _AUTH.fullmatch(name) if type(name) is str else None
    return match.group(1) if match and account_id(match.group(1)) else None


def _token_id(name):
    match = _TOKEN.fullmatch(name) if type(name) is str else None
    return match.group(1) if match and account_id(match.group(1)) else None


def listing(root):
    """Direct auth-file metadata only; no recursion or credential access."""
    root = mdata_files.profile_path(root)
    entries = []
    with mdata_files.hold_tree(root) as actual:
        for path in actual.iterdir():
            if not path.name.casefold().startswith("auth_"):
                continue
            if not _auth_id(path.name):
                _fail("invalid_profile")
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or getattr(info, "st_file_attributes", 0) & 0x400):
                _fail("unsafe_path")
            if not 1 <= info.st_size <= MAX_AUTH_BYTES:
                _fail("invalid_profile")
            entries.append({"path": path.name, "size": info.st_size})
            if len(entries) > MAX_CANDIDATES:
                _fail("invalid_profile")
    return sorted(entries, key=lambda item: item["path"])


def _read_auth(root, name, expected_size=None, *, code="profile_changed"):
    if not _auth_id(name):
        _fail("invalid_payload")
    with launch_mod._config_stream(root / name, create=False) as stream:
        data = stream.read(MAX_AUTH_BYTES + 1)
    if (not 1 <= len(data) <= MAX_AUTH_BYTES
            or expected_size is not None and len(data) != expected_size):
        _fail(code)
    return data


def _blob(blob, *, code="invalid_payload"):
    if type(blob) is not bytes or not 1 <= len(blob) <= MAX_BLOB:
        _fail(code)
    return blob


def selected_account_ids(value):
    """Canonical nonempty explicit selection, validated before native reads."""
    if (type(value) not in (list, tuple) or not 1 <= len(value) <= MAX_ACCOUNTS
            or any(not account_id(item) for item in value)
            or len(set(value)) != len(value)):
        _fail("invalid_request")
    return tuple(sorted(value, key=int))


def capture(root, credentials, *, account_ids=None):
    """Return manifest, native tokens and small auth bytes; no key inventory."""
    selected = selected_account_ids(account_ids) if account_ids is not None else None
    root = mdata_files.profile_path(root)
    candidates = listing(root)
    if selected is not None and not set(selected).issubset({_auth_id(entry["path"]) for entry in candidates}):
        _fail("invalid_request")
    tokens, auth, observed_tokens = {}, {}, {}
    with mdata_files.hold_tree(root) as actual:
        for entry in candidates:
            rid = _auth_id(entry["path"])
            if selected is not None and rid not in selected:
                continue
            name = "access_token_" + rid + "@MXMOD"
            blob = credentials.read(name)
            observed_tokens[name] = blob
            # Native logout can leave metadata after deleting its token.
            if blob is None or blob == b"":
                if selected is not None:
                    _fail("no_accounts")
                continue
            tokens[name] = _blob(blob, code="invalid_profile")
            if len(tokens) > MAX_ACCOUNTS:
                _fail("account_limit")
            auth[entry["path"]] = _read_auth(actual, entry["path"], entry["size"])
        if not tokens:
            _fail("no_accounts")
        if listing(actual) != candidates:
            _fail("profile_changed")
        for name, value in observed_tokens.items():
            if credentials.read(name) != value:
                _fail("profile_changed")
        for name, data in auth.items():
            if _read_auth(actual, name, len(data)) != data:
                _fail("profile_changed")
    manifest = {"schema": 1, "sourceClient": "MAX Desktop", "sourceVersion": "26.35.1",
                "files": [{"path": name, "size": len(data)} for name, data in sorted(auth.items())],
                "credentials": [{"name": name, "blob": base64.b64encode(data).decode("ascii")}
                                for name, data in sorted(tokens.items())]}
    validate(manifest, tokens)
    return manifest, tokens, auth


def validate(manifest, tokens):
    """Require an authenticated login-only manifest before native I/O."""
    if (type(manifest) is not dict
            or set(manifest) != {"schema", "sourceClient", "sourceVersion", "files", "credentials"}
            or type(manifest["schema"]) is not int or manifest["schema"] != 1
            or manifest["sourceClient"] != "MAX Desktop" or manifest["sourceVersion"] != "26.35.1"
            or type(manifest["files"]) is not list or type(manifest["credentials"]) is not list
            or type(tokens) is not dict):
        _fail("invalid_payload")
    if len(tokens) > MAX_ACCOUNTS or len(manifest["credentials"]) > MAX_ACCOUNTS:
        _fail("account_limit")
    if not tokens:
        _fail("no_accounts")
    ids = set()
    for name, value in tokens.items():
        rid = _token_id(name)
        if rid is None:
            _fail("invalid_payload")
        ids.add(rid)
        _blob(value)
    encoded = {}
    for record in manifest["credentials"]:
        if (type(record) is not dict or set(record) != {"name", "blob"}
                or not _token_id(record["name"]) or record["name"] in encoded
                or type(record["blob"]) is not str or len(record["blob"]) > 4 * ((MAX_BLOB + 2) // 3)):
            _fail("invalid_payload")
        try:
            value = base64.b64decode(record["blob"], validate=True)
        except (ValueError, UnicodeError, binascii.Error):
            _fail("invalid_payload")
        _blob(value)
        if base64.b64encode(value).decode("ascii") != record["blob"]:
            _fail("invalid_payload")
        encoded[record["name"]] = value
    if encoded != tokens:
        _fail("invalid_payload")
    if len(manifest["files"]) > MAX_ACCOUNTS:
        _fail("invalid_payload")
    seen = set()
    for record in manifest["files"]:
        if (type(record) is not dict or set(record) != {"path", "size"}
                or _auth_id(record["path"]) not in ids or record["path"] in seen
                or type(record["size"]) is not int or not 1 <= record["size"] <= MAX_AUTH_BYTES):
            _fail("invalid_payload")
        seen.add(record["path"])
    return True


def _current_tokens(credentials):
    result, empty, seen = {}, set(), set()
    for name in credentials.owned_names():
        # Enumeration supplies names only. Never read common/history/calls/web
        # values, or a foreign namespace, even if the inventory includes them.
        rid = _token_id(name)
        if rid is None:
            if type(name) is str and name.startswith("access_token_") and name.endswith("@MXMOD"):
                _fail("invalid_profile")
            continue
        if name in seen:
            _fail("invalid_profile")
        seen.add(name)
        value = credentials.read(name)
        if value is None or value == b"":
            if value == b"":
                empty.add(name)
            continue
        result[name] = _blob(value, code="invalid_profile")
    if len(result) > MAX_ACCOUNTS:
        _fail("account_limit")
    return result, empty


def _present(path):
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def _remove_created_auth(path, identity, data):
    if not _present(path):
        return
    with launch_mod._config_stream(path, create=False) as stream:
        if _identity(os.fstat(stream.fileno())) != identity:
            _fail("rollback_failed")
        if not hmac.compare_digest(stream.read(MAX_AUTH_BYTES + 1), data):
            _fail("rollback_failed")
    _remove_owned(path, identity)


def _preserve_auth(path, held):
    """Pin existing optional metadata; never parse or overwrite its bytes."""
    try:
        stream = held.enter_context(launch_mod._config_stream(path, create=False))
        info = os.fstat(stream.fileno())
        data = stream.read(MAX_AUTH_BYTES + 1)
        if len(data) > MAX_AUTH_BYTES:
            _fail("invalid_profile")
        return stream, _identity(info), data
    except (OSError, ValueError):
        _fail("profile_collision")


def _verify_preserved_auth(path, preserved):
    stream, identity, data = preserved
    try:
        launch_mod._check_path(path, directory=False)
        if _identity(path.lstat()) != identity or _identity(os.fstat(stream.fileno())) != identity:
            _fail("profile_changed")
        stream.seek(0)
        if not hmac.compare_digest(stream.read(MAX_AUTH_BYTES + 1), data):
            _fail("profile_changed")
    except (OSError, ValueError):
        _fail("profile_changed")


def _preserve_user_directory(path, held):
    """Hold only the direct normal directory; do not inspect any child data."""
    try:
        launch_mod._check_path(path, directory=True)
        actual = held.enter_context(_directory(path))
        if actual != path:
            _fail("profile_collision")
        return _identity(actual.lstat())
    except (OSError, ValueError):
        _fail("profile_collision")


def _verify_preserved_user_directory(path, identity):
    if identity is None:
        if _present(path):
            _fail("profile_changed")
        return
    try:
        launch_mod._check_path(path, directory=True)
        if _identity(path.lstat()) != identity:
            _fail("profile_changed")
    except (OSError, ValueError):
        _fail("profile_changed")


def import_snapshot(root, staging, manifest, tokens, credentials):
    """Add missing logins, with tokens last and no existing-profile writes."""
    validate(manifest, tokens)
    root = mdata_files.profile_path(root)
    staging = _lexical_path(Path(staging) / "placeholder").parent
    auth = {}
    with _parents(staging / "placeholder", create=False) as parent:
        if parent is None:
            _fail("invalid_payload")
        with _directory(staging) as actual:
            for entry in manifest["files"]:
                auth[entry["path"]] = _read_auth(actual, entry["path"], entry["size"], code="invalid_payload")
    current, empty = _current_tokens(credentials)
    incoming = {_token_id(name): name for name in tokens}
    current_ids = {_token_id(name) for name in current}
    new_ids = set(incoming) - current_ids
    if not new_ids:
        return "already_present", 0
    if len(current_ids | set(incoming)) > MAX_ACCOUNTS:
        _fail("account_limit")
    created_files, created_tokens = [], []
    with ExitStack() as held:
        actual = held.enter_context(mdata_files.hold_tree(root))
        preserved_auth, preserved_users = {}, {}
        # Complete validation precedes any writes to the destination.
        for rid in sorted(new_ids):
            user_path = actual / ("user" + rid)
            preserved_users[rid] = (_preserve_user_directory(user_path, held)
                                    if _present(user_path) else None)
            name = "auth_" + rid
            if _present(actual / name):
                # Token absence means no active local authorization. Its own
                # optional metadata/history is not an active authorization and
                # need not match the source PC's current name or photo.
                preserved_auth[name] = _preserve_auth(actual / name, held)
        try:
            for rid in sorted(new_ids):
                name = "auth_" + rid
                if name in auth and name not in preserved_auth:
                    with _new_stream(actual / name) as stream:
                        created_files.append((actual / name, _identity(os.fstat(stream.fileno())), auth[name]))
                        stream.write(auth[name])
                        stream.flush()
                        os.fsync(stream.fileno())
                _verify_preserved_user_directory(actual / ("user" + rid), preserved_users[rid])
                if name in preserved_auth:
                    _verify_preserved_auth(actual / name, preserved_auth[name])
                elif name in auth:
                    with launch_mod._config_stream(actual / name, create=False) as stream:
                        if _identity(os.fstat(stream.fileno())) != created_files[-1][1]:
                            _fail("profile_changed")
                        if not hmac.compare_digest(stream.read(MAX_AUTH_BYTES + 1), auth[name]):
                            _fail("profile_changed")
                elif _present(actual / name):
                    _fail("profile_collision")
                target = incoming[rid]
                previous = b"" if target in empty else None
                created_tokens.append((target, tokens[target], previous))
                try:
                    credentials.set_owned(target, tokens[target], previous)
                except Exception as error:
                    # The adapter explicitly reports an expected-value conflict
                    # before it writes anything. Never roll back that other
                    # writer's token, even when its bytes match this archive.
                    if getattr(error, "code", None) == "destination_not_empty":
                        created_tokens.pop()
                    raise
        except Exception:
            failed = False
            for name, value, previous in reversed(created_tokens):
                try:
                    if previous is None:
                        credentials.delete_owned(name, value)
                    elif credentials.read(name) != previous:
                        credentials.set_owned(name, previous, value)
                except Exception:
                    failed = True
            for path, identity, data in reversed(created_files):
                try:
                    _remove_created_auth(path, identity, data)
                except Exception:
                    failed = True
            if failed:
                _fail("rollback_failed")
            raise
    return "added", len(new_ids)
