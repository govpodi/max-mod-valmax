"""Password-encrypted bytes envelope, not a MAX credential/profile reader.

Opaque records must come from a separately authorized adapter and must match an
explicit policy on both encryption and decryption. This module has no file,
registry, WinCred, DPAPI, process or network access. It does not establish that
MAX will accept the supplied records on another computer.

Format v1: MAGIC | uint16-BE(header length) | canonical UTF-8 JSON header |
AES-256-GCM ciphertext with its 16-byte tag. The entire prefix/header is AAD.
Scrypt is fixed at n=32768, r=8, p=1, key length=32, salt length=16. Header
parameters are validated before the KDF. Dependencies: cryptography==50.0.2.
Passwords are exact UTF-8, never normalized; no password/plaintext is logged.
Python cannot guarantee zeroization of strings or immutable bytes in memory.

Primary API references:
https://cryptography.io/en/50.0.2/hazmat/primitives/aead/
https://cryptography.io/en/50.0.2/hazmat/primitives/key-derivation-functions/
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
import os
import re
import struct

try:
    from cryptography.exceptions import InvalidTag, UnsupportedAlgorithm
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
except ImportError:
    AESGCM = Scrypt = None

SOURCE_CLIENT = "MAX Desktop"
SOURCE_VERSION = "26.35.1"
MAGIC = b"MXMODSA\x01"
MAX_SECRET_BYTES = 4 * 1024 * 1024
MAX_PLAINTEXT_BYTES = 6 * 1024 * 1024
MAX_HEADER_BYTES = 1024
MAX_ARCHIVE_BYTES = len(MAGIC) + 2 + MAX_HEADER_BYTES + MAX_PLAINTEXT_BYTES + 16
MAX_CREDENTIALS = 256
MAX_FILES = 128
SCRYPT_N, SCRYPT_R, SCRYPT_P = 32768, 8, 1
_PREFIX = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{2,126}[:/_-]\Z")
_ID_TOKENS = (r"[1-9][0-9]{0,18}", r"[1-9][0-9]{0,19}")
_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL",
                       *("COM" + str(n) for n in range(1, 10)),
                       *("LPT" + str(n) for n in range(1, 10))})
_CODES = frozenset({"invalid_password", "authentication_failed", "invalid_archive",
                    "unsupported_version", "invalid_payload", "policy_violation",
                    "too_large", "crypto_unavailable"})


class SessionArchiveError(Exception):
    def __init__(self, code: str):
        if code not in _CODES:
            raise ValueError("Unknown session archive error code")
        self.code = code
        super().__init__(code)


def _fail(code: str):
    raise SessionArchiveError(code) from None


def _name(value) -> bool:
    return (type(value) is str and 1 <= len(value) <= 256 and value.isascii()
            and all(32 <= ord(character) <= 126 for character in value))


def _path(value) -> bool:
    if (type(value) is not str or not 1 <= len(value) <= 240 or not value.isascii()
            or any(ord(character) < 32 or ord(character) > 126 for character in value)
            or any(character in value for character in '\\:%<>|?*"')):
        return False
    for part in value.split("/"):
        if (not part or part in (".", "..") or part.startswith(" ")
                or part.endswith((".", " ")) or part.split(".")[0].upper() in _RESERVED):
            return False
    return True


def _pattern_parts(pattern: str) -> tuple[str, str]:
    """Only a literal namespace and one bounded positive decimal ID token."""
    if type(pattern) is not str or not 1 <= len(pattern) <= 512:
        _fail("policy_violation")
    tokens = [token for token in _ID_TOKENS if pattern.count(token) == 1]
    if len(tokens) != 1:
        _fail("policy_violation")
    before, after = pattern.split(tokens[0])
    def literal(value):
        # A dot in a static name must be escaped, never a regex wildcard.
        if not re.fullmatch(r"(?:[A-Za-z0-9_@/ -]|\\\.)*", value):
            _fail("policy_violation")
        return value.replace(r"\.", ".")
    before, after = literal(before), literal(after)
    if not before and not after:
        _fail("policy_violation")
    return before, after


def _matches_pattern(value: str, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        before, after = _pattern_parts(pattern)
        if re.fullmatch(pattern, value) is not None:
            decimal = value[len(before):len(value) - len(after) if after else len(value)]
            if 1 <= int(decimal) <= (1 << 64) - 1:
                return True
    return False


@dataclass(frozen=True)
class ArchivePolicy:
    """Deny by default; never infer allowed records from archive contents."""

    credential_names: tuple[str, ...] = ()
    credential_prefixes: tuple[str, ...] = ()
    allowed_file_paths: tuple[str, ...] = ()
    credential_name_patterns: tuple[str, ...] = ()
    file_path_patterns: tuple[str, ...] = ()

    def __post_init__(self):
        fields = ((self.credential_names, MAX_CREDENTIALS, _name),
                  (self.credential_prefixes, 32,
                   lambda value: type(value) is str and _PREFIX.fullmatch(value) is not None),
                  (self.allowed_file_paths, MAX_FILES, _path))
        for values, limit, validator in fields:
            if type(values) is not tuple or len(values) > limit:
                _fail("policy_violation")
            if not all(validator(value) for value in values):
                _fail("policy_violation")
            if len({value.casefold() for value in values}) != len(values):
                _fail("policy_violation")
        for patterns, validator in ((self.credential_name_patterns, _name),
                                    (self.file_path_patterns, _path)):
            if type(patterns) is not tuple or len(patterns) > 32:
                _fail("policy_violation")
            for pattern in patterns:
                before, after = _pattern_parts(pattern)
                if not validator(before + "1" + after):
                    _fail("policy_violation")
            if len(set(patterns)) != len(patterns):
                _fail("policy_violation")


def _policy(policy) -> ArchivePolicy:
    if type(policy) is not ArchivePolicy:
        _fail("policy_violation")
    return policy


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _json(data: bytes, code: str):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail(code)
            result[key] = value
        return result

    def invalid_constant(_value):
        _fail(code)

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=invalid_constant)
    except (UnicodeError, ValueError, RecursionError):
        _fail(code)


def _base64(value, maximum: int, code: str) -> bytes:
    if type(value) is not str:
        _fail(code)
    if len(value) > 4 * ((maximum + 2) // 3):
        _fail("too_large")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeError, ValueError, binascii.Error):
        _fail(code)
    if len(decoded) > maximum:
        _fail("too_large")
    if base64.b64encode(decoded).decode("ascii") != value:
        _fail(code)
    return decoded


def _password(password) -> bytes:
    if type(password) is not str or not 1 <= len(password) <= 1024 or "\x00" in password:
        _fail("invalid_password")
    try:
        encoded = password.encode("utf-8")
    except UnicodeError:
        _fail("invalid_password")
    if not 12 <= len(encoded) <= 1024:
        _fail("invalid_password")
    return encoded


def validate_payload(payload, *, policy: ArchivePolicy) -> dict:
    """Validate supplied in-memory records; never collect or apply them."""
    policy = _policy(policy)
    if (type(payload) is not dict
            or set(payload) != {"schema", "sourceClient", "sourceVersion", "credentials", "files"}
            or type(payload["schema"]) is not int or payload["schema"] != 1
            or type(payload["sourceClient"]) is not str
            or type(payload["sourceVersion"]) is not str):
        _fail("invalid_payload")
    if payload["sourceClient"] != SOURCE_CLIENT or payload["sourceVersion"] != SOURCE_VERSION:
        _fail("unsupported_version")
    credentials, files = payload["credentials"], payload["files"]
    if type(credentials) is not list or type(files) is not list:
        _fail("invalid_payload")
    if len(credentials) > MAX_CREDENTIALS or len(files) > MAX_FILES:
        _fail("too_large")
    result = {"schema": 1, "sourceClient": SOURCE_CLIENT, "sourceVersion": SOURCE_VERSION,
              "credentials": [], "files": []}
    names, paths, total = set(), set(), 0
    for record in credentials:
        if type(record) is not dict or set(record) != {"name", "blob"} or not _name(record["name"]):
            _fail("invalid_payload")
        name = record["name"]
        if name.casefold() in names:
            _fail("invalid_payload")
        if (name not in policy.credential_names
                and not any(name.startswith(prefix) and len(name) > len(prefix)
                            for prefix in policy.credential_prefixes)
                and not _matches_pattern(name, policy.credential_name_patterns)):
            _fail("policy_violation")
        total += len(_base64(record["blob"], MAX_SECRET_BYTES - total, "invalid_payload"))
        names.add(name.casefold())
        result["credentials"].append({"name": name, "blob": record["blob"]})
    for record in files:
        if type(record) is not dict or set(record) != {"path", "data"} or not _path(record["path"]):
            _fail("invalid_payload")
        path = record["path"]
        if path.casefold() in paths:
            _fail("invalid_payload")
        if path not in policy.allowed_file_paths and not _matches_pattern(path, policy.file_path_patterns):
            _fail("policy_violation")
        total += len(_base64(record["data"], MAX_SECRET_BYTES - total, "invalid_payload"))
        paths.add(path.casefold())
        result["files"].append({"path": path, "data": record["data"]})
    if len(_canonical(result)) > MAX_PLAINTEXT_BYTES:
        _fail("too_large")
    return result


def _derive(password: bytes, salt: bytes) -> bytes:
    if Scrypt is None or AESGCM is None:
        _fail("crypto_unavailable")
    try:
        return Scrypt(salt=salt, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(password)
    except (UnsupportedAlgorithm, MemoryError, ValueError):
        _fail("crypto_unavailable")


def encrypt(payload, password, *, policy: ArchivePolicy) -> bytes:
    password_bytes = _password(password)
    clean = validate_payload(payload, policy=policy)
    plaintext = _canonical(clean)
    try:
        salt, nonce = os.urandom(16), os.urandom(12)
    except OSError:
        _fail("crypto_unavailable")
    header = {"schema": 1, "sourceClient": SOURCE_CLIENT, "sourceVersion": SOURCE_VERSION,
              "cipher": "AES-256-GCM", "plaintextBytes": len(plaintext),
              "kdf": {"name": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
                      "salt": base64.b64encode(salt).decode("ascii")},
              "nonce": base64.b64encode(nonce).decode("ascii")}
    header_bytes = _canonical(header)
    aad = MAGIC + struct.pack(">H", len(header_bytes)) + header_bytes
    key = _derive(password_bytes, salt)
    try:
        return aad + AESGCM(key).encrypt(nonce, plaintext, aad)
    except (UnsupportedAlgorithm, MemoryError, ValueError):
        _fail("crypto_unavailable")


def _header(archive: bytes) -> tuple[dict, bytes, bytes, bytes, bytes]:
    if type(archive) is not bytes:
        _fail("invalid_archive")
    if len(archive) > MAX_ARCHIVE_BYTES:
        _fail("too_large")
    prefix = len(MAGIC) + 2
    if len(archive) < prefix + 2 + 16 or not archive.startswith(MAGIC):
        _fail("invalid_archive")
    size = struct.unpack_from(">H", archive, len(MAGIC))[0]
    if not 2 <= size <= MAX_HEADER_BYTES or prefix + size + 16 > len(archive):
        _fail("invalid_archive")
    header_bytes = archive[prefix:prefix + size]
    header = _json(header_bytes, "invalid_archive")
    if (type(header) is not dict
            or set(header) != {"schema", "sourceClient", "sourceVersion", "cipher", "plaintextBytes", "kdf", "nonce"}
            or type(header["schema"]) is not int or header["schema"] != 1
            or type(header["sourceClient"]) is not str or type(header["sourceVersion"]) is not str
            or header["cipher"] != "AES-256-GCM"
            or type(header["plaintextBytes"]) is not int
            or not 1 <= header["plaintextBytes"] <= MAX_PLAINTEXT_BYTES):
        _fail("invalid_archive")
    if header["sourceClient"] != SOURCE_CLIENT or header["sourceVersion"] != SOURCE_VERSION:
        _fail("unsupported_version")
    kdf = header["kdf"]
    if (type(kdf) is not dict or set(kdf) != {"name", "n", "r", "p", "salt"}
            or kdf["name"] != "scrypt"
            or any(type(kdf[key]) is not int or kdf[key] != expected
                   for key, expected in (("n", SCRYPT_N), ("r", SCRYPT_R), ("p", SCRYPT_P)))):
        _fail("invalid_archive")
    salt = _base64(kdf["salt"], 16, "invalid_archive")
    nonce = _base64(header["nonce"], 12, "invalid_archive")
    if (len(salt) != 16 or len(nonce) != 12 or _canonical(header) != header_bytes
            or len(archive) - prefix - size != header["plaintextBytes"] + 16):
        _fail("invalid_archive")
    return header, salt, nonce, archive[:prefix + size], archive[prefix + size:]


def decrypt(archive, password, *, policy: ArchivePolicy) -> dict:
    policy = _policy(policy)
    password_bytes = _password(password)
    _header_value, salt, nonce, aad, ciphertext = _header(archive)
    key = _derive(password_bytes, salt)
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
    except InvalidTag:
        # A wrong password and authenticated-data corruption are indistinguishable.
        _fail("authentication_failed")
    except (UnsupportedAlgorithm, MemoryError, ValueError):
        _fail("crypto_unavailable")
    payload = _json(plaintext, "invalid_payload")
    return validate_payload(payload, policy=policy)
