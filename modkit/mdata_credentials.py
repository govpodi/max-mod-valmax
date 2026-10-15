"""Verified MAX Mod key kinds; owned-name inventory and exact value reads."""
import hmac
import re
import ctypes
from ctypes import wintypes

from session_credentials import WindowsCredentials, CredentialError, account_id
import session_credentials as native

ACCOUNT_KEYS = ("access_token", "history_storage_key", "calls_token", "calls_session")
COMMON_KEYS = ("app_data_storage_key", "multi_state_enabled")
COMMON_NAMES = tuple(key + "_Common@MXMOD" for key in COMMON_KEYS)
_NAME = re.compile(r"(" + "|".join(ACCOUNT_KEYS) + r")_([1-9][0-9]{0,18})@MXMOD\Z")
_WEB = re.compile(r"(.{1,192})-([1-9][0-9]{0,18})_(0|[1-9][0-9]{0,18})@MXMOD\Z")


def allowed_name(name):
    if type(name) is not str:
        return False
    if name in COMMON_NAMES:
        return True
    match = _NAME.fullmatch(name)
    if match and account_id(match.group(2)):
        return True
    match = _WEB.fullmatch(name)
    if not match or not account_id(match.group(2)) or not name.isascii():
        return False
    if any(ord(char) < 32 or ord(char) > 126 for char in match.group(1)):
        return False
    owner = int(match.group(2)) ^ int(match.group(3))
    return account_id(str(owner))


def owner_id(name):
    if not allowed_name(name):
        raise CredentialError("invalid_payload")
    if name in COMMON_NAMES:
        return None
    match = _NAME.fullmatch(name)
    if match:
        return match.group(2)
    match = _WEB.fullmatch(name)
    return str(int(match.group(2)) ^ int(match.group(3)))


def targets(ids):
    if any(not account_id(rid) for rid in ids) or len(ids) > 63:
        raise CredentialError("invalid_payload")
    return tuple(COMMON_NAMES) + tuple(key + "_" + rid + "@MXMOD"
                                       for rid in sorted(ids) for key in ACCOUNT_KEYS)


class MDataCredentials(WindowsCredentials):
    @staticmethod
    def _name(name):
        if not allowed_name(name):
            raise CredentialError("invalid_payload")

    def set_owned(self, name, blob, expected):
        self._name(name)
        current = self.read(name)
        if (current is None) != (expected is None) or (current is not None and not hmac.compare_digest(current, expected)):
            raise CredentialError("destination_not_empty")
        self._write(name, blob)

    def delete_owned(self, name, expected):
        self.delete_created(name, expected)

    def owned_names(self):
        """Inventory names only, then callers use exact CredRead for own values.

        WinCred supports prefix filters, while this app puts MXMOD at the end.
        Its API allocation includes other records; their blob fields are never
        dereferenced, copied, printed or saved. The allocation is freed here.
        This method is called only by an explicit MData job, never by status.
        """
        api = native._api()
        pointer_type = ctypes.POINTER(native.CREDENTIALW)
        values = ctypes.POINTER(pointer_type)()
        count = wintypes.DWORD()
        api.CredEnumerateW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(ctypes.POINTER(pointer_type)))
        api.CredEnumerateW.restype = wintypes.BOOL
        if not api.CredEnumerateW(None, 0, ctypes.byref(count), ctypes.byref(values)):
            if ctypes.get_last_error() == 1168:
                return ()
            raise CredentialError("credential_io")
        names = []
        try:
            if count.value > 100000:
                raise CredentialError("credential_io")
            for index in range(count.value):
                entry = values[index].contents
                name = entry.TargetName
                if entry.Type == 1 and name and name.endswith("@MXMOD"):
                    if not allowed_name(name):
                        raise CredentialError("invalid_payload")
                    names.append(name)
            if len(names) > 256 or len({name.casefold() for name in names}) != len(names):
                raise CredentialError("invalid_payload")
            return tuple(sorted(names))
        finally:
            api.CredFree(values)


def policy():
    from session_archive import ArchivePolicy
    return ArchivePolicy(credential_names=COMMON_NAMES,
                         credential_name_patterns=tuple(key + r"_[1-9][0-9]{0,18}@MXMOD" for key in ACCOUNT_KEYS))
