"""Exact-name Windows Generic Credential adapter for explicit MAX Mod transfer.

No enumeration API is used. Construction does no I/O. Names are validated
before calling Windows, and neither errors nor repr include credential data.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import re
import hmac

# SafeStorage entry points, verified in the pinned storage.dll. Session
# transfer preserves opaque blobs; it never parses or logs access tokens.
ACCOUNT_KEYS = ("access_token",)
COMMON_KEYS = ()
_ACCOUNT_NAME = re.compile(r"(" + "|".join(ACCOUNT_KEYS) + r")_([1-9][0-9]{0,18})@MXMOD\Z")
COMMON_NAMES = tuple(key + "_Common@MXMOD" for key in COMMON_KEYS)
MAX_BLOB = 2560  # CRED_MAX_CREDENTIAL_BLOB_SIZE


class CredentialError(Exception):
    def __init__(self, code):
        self.code = code if code in {"credential_io", "invalid_payload", "destination_not_empty"} else "credential_io"
        super().__init__(self.code)


def account_id(value):
    return (type(value) is str and re.fullmatch(r"[1-9][0-9]{0,18}", value) is not None
            and int(value) <= 0x7FFFFFFFFFFFFFFF)


def allowed_name(name):
    if type(name) is not str:
        return False
    if name in COMMON_NAMES:
        return True
    match = _ACCOUNT_NAME.fullmatch(name)
    return bool(match and account_id(match.group(2)))


class CREDENTIALW(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


def _api():
    if os.name != "nt":
        raise CredentialError("credential_io")
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.CredReadW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                             ctypes.POINTER(ctypes.POINTER(CREDENTIALW)))
    api.CredReadW.restype = wintypes.BOOL
    api.CredWriteW.argtypes = (ctypes.POINTER(CREDENTIALW), wintypes.DWORD)
    api.CredWriteW.restype = wintypes.BOOL
    api.CredDeleteW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD)
    api.CredDeleteW.restype = wintypes.BOOL
    api.CredFree.argtypes = (ctypes.c_void_p,)
    api.CredFree.restype = None
    return api


class WindowsCredentials:
    def __repr__(self):
        return "<MAX Mod exact-name credential adapter>"

    @staticmethod
    def _name(name):
        if not allowed_name(name):
            raise CredentialError("invalid_payload")

    def read(self, name):
        self._name(name)
        api = _api()
        pointer = ctypes.POINTER(CREDENTIALW)()
        if not api.CredReadW(name, 1, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == 1168:
                return None
            raise CredentialError("credential_io")
        try:
            entry = pointer.contents
            if (entry.Type != 1 or entry.TargetName != name
                    or entry.CredentialBlobSize > MAX_BLOB
                    or (entry.CredentialBlobSize and not entry.CredentialBlob)):
                raise CredentialError("credential_io")
            return ctypes.string_at(entry.CredentialBlob, entry.CredentialBlobSize)
        finally:
            # Best effort wipe of the API's allocated copy before CredFree.
            if pointer and pointer.contents.CredentialBlob and pointer.contents.CredentialBlobSize <= MAX_BLOB:
                ctypes.memset(pointer.contents.CredentialBlob, 0, pointer.contents.CredentialBlobSize)
            api.CredFree(pointer)

    def create(self, name, blob):
        self._name(name)
        if type(blob) is not bytes or not 1 <= len(blob) <= MAX_BLOB:
            raise CredentialError("invalid_payload")
        if self.read(name) is not None:
            raise CredentialError("destination_not_empty")
        self._write(name, blob)

    def _write(self, name, blob):
        self._name(name)
        if type(blob) is not bytes or not 0 <= len(blob) <= MAX_BLOB:
            raise CredentialError("invalid_payload")
        api = _api()
        buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        entry = CREDENTIALW()
        entry.Type, entry.TargetName = 1, name
        entry.CredentialBlobSize, entry.CredentialBlob = len(blob), buffer
        entry.Persist = 2  # This Windows user on this computer; no new roaming.
        entry.UserName = ""
        try:
            if not api.CredWriteW(ctypes.byref(entry), 0):
                raise CredentialError("credential_io")
        finally:
            ctypes.memset(buffer, 0, len(blob))

    def delete_created(self, name, expected_blob):
        """Rollback only names this import successfully created, never a scan."""
        self._name(name)
        current = self.read(name)
        if current is None:
            return
        if not hmac.compare_digest(current, expected_blob):
            raise CredentialError("credential_io")
        api = _api()
        if not api.CredDeleteW(name, 1, 0) and ctypes.get_last_error() != 1168:
            raise CredentialError("credential_io")
