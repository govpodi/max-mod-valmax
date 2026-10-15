"""Authenticated compact MData preview; no Windows credential/profile access.

Public results contain only a fixed projection of native auth metadata. The
container terminal and EOF are authenticated before that projection is made.
All decrypted data stays in bounded process memory and is never installed.
"""
from __future__ import annotations

import base64
import hashlib
from contextlib import contextmanager
import io
import os
import unicodedata

import launch_mod
import mdata_archive
import mdata_login
from mdata_service import MDataError, _error, _login_policy, selected_path
from quick_reply_store import _parents
from session_archive import _json

MAX_ARCHIVE_BYTES = 2 * 1024 * 1024
MAX_AUTH_TOTAL = mdata_login.MAX_ACCOUNTS * mdata_login.MAX_AUTH_BYTES


def _name(value, limit):
    if type(value) is not str:
        return ""
    # Unicode control/format characters cannot impersonate another row or
    # reverse a displayed account name. HTML remains plain text in the UI.
    text = "".join(" " if unicodedata.category(char) in ("Cc", "Cf", "Cs") else char
                   for char in value[:limit])
    return " ".join(text.split())


def _account_metadata(data):
    try:
        value = _json(data, "invalid_payload")
    except Exception:
        return "", ""
    if type(value) is not dict:
        return "", ""
    first = _name(value.get("first_name"), 128)
    last = _name(value.get("last_name"), 128)
    return (first + " " + last).strip()[:256], _name(value.get("short_name"), 32)


class _BoundedSink(io.BytesIO):
    def __init__(self, expected):
        super().__init__()
        self.expected = expected

    def write(self, value):
        if self.tell() + len(value) > self.expected:
            raise MDataError("too_large")
        return super().write(value)


class _BoundedSource:
    def __init__(self, stream):
        self.stream, self.used = stream, 0
        self.digest = hashlib.sha256()

    def read(self, size):
        if type(size) is not int or size < 0:
            raise MDataError("too_large")
        value = self.stream.read(min(size, MAX_ARCHIVE_BYTES - self.used + 1))
        self.used += len(value)
        self.digest.update(value)
        if self.used > MAX_ARCHIVE_BYTES:
            raise MDataError("too_large")
        return value


def inspect(selected_file, password):
    """Authenticate v2 completely, then return IDs and native display names.

    ``selected_file`` is the existing local file-URI contract. Legacy full
    profile archives remain importable through MDataService, but are excluded
    from this deliberately bounded preview API.
    """
    auth = {}
    manifest = None
    tokens = None
    try:
        path = selected_path(selected_file, "import")
        if path.suffix.casefold() != ".mdata":
            raise MDataError("invalid_archive")

        def preflight(total, count):
            if count > mdata_login.MAX_ACCOUNTS or total > MAX_AUTH_TOTAL:
                raise MDataError("too_large")

        @contextmanager
        def sink(relative, size):
            if (mdata_login._auth_id(relative) is None
                    or not 1 <= size <= mdata_login.MAX_AUTH_BYTES
                    or relative in auth or len(auth) >= mdata_login.MAX_ACCOUNTS):
                raise MDataError("invalid_payload")
            with _BoundedSink(size) as output:
                yield output
                value = output.getvalue()
                if len(value) != size:
                    raise MDataError("invalid_payload")
                auth[relative] = value

        with _parents(path, create=False) as parent:
            if parent is None:
                raise MDataError("unsafe_path")
            with launch_mod._config_stream(path, create=False) as source:
                if source.read(8) == mdata_archive.MAGIC:
                    # Recognizable legacy scope, never unverified account data.
                    # The existing explicit import still authenticates it.
                    raise MDataError("unsupported_version")
                source.seek(0)
                if not 8 <= os.fstat(source.fileno()).st_size <= MAX_ARCHIVE_BYTES:
                    raise MDataError("too_large")
                reader = _BoundedSource(source)
                manifest = mdata_archive.decrypt_stream(reader, password, sink, policy=_login_policy(),
                                                        preflight=preflight, expected_kind="login")
                fingerprint = reader.digest.hexdigest()
        # Nothing above is observable account metadata: even a valid first
        # frame can still fail terminal authentication or the exact EOF check.
        tokens = {entry["name"]: base64.b64decode(entry["blob"], validate=True)
                  for entry in manifest["credentials"]}
        mdata_login.validate(manifest, tokens)
        accounts = []
        for rid in sorted((mdata_login._token_id(name) for name in tokens), key=int):
            display, short = _account_metadata(auth.get("auth_" + rid, b""))
            accounts.append({"accountId": rid, "displayName": display, "shortName": short})
        return {"schema": 1, "kind": "login", "profiles": len(accounts), "accounts": accounts, "fingerprint": fingerprint}
    except MDataError:
        raise
    except Exception as error:
        raise _error(error) from None
    finally:
        # Python does not guarantee secret zeroization. Drop our references;
        # do not persist plaintext, passwords, tokens or partially decoded UI.
        auth.clear()
        manifest = tokens = password = None
