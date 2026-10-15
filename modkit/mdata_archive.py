"""Streaming encrypted MData container. No filesystem/WinCred/profile access.

Trusted factories supply context-managed binary streams. Decryption sinks MUST
be private staging outputs: discard them on any exception, and do not install
files/credentials until decrypt_stream returns after terminal authentication.
The returned manifest is secret internal data, never a public status/log value.

Each frame has a unique nonce (random 8-byte archive prefix + uint32 counter).
Its AAD includes the complete header, counter, length and terminal flag. There
is no compression; one authenticated empty terminal frame and EOF are required.
Parameters: AES-256-GCM, Scrypt n=32768/r=8/p=1, 16-byte salt, 1 MiB chunks.
Dependency: cryptography==50.0.2. Python cannot guarantee secret zeroization.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
import struct
import unicodedata

import session_archive as _session
from session_archive import ArchivePolicy, SessionArchiveError

MAGIC = b"MXMODMD\x01"
LOGIN_MAGIC = b"MXMODMD\x02"
_ARCHIVE_MAGICS = {"profile": MAGIC, "login": LOGIN_MAGIC}
CHUNK_BYTES = 1024 * 1024
MAX_FILES = 10000
MAX_FILE_BYTES = 4 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_HEADER_BYTES = 1024
MAX_PLAINTEXT_BYTES = 4 + MAX_MANIFEST_BYTES + MAX_FILE_BYTES
_CODES = frozenset({"invalid_password", "authentication_failed", "invalid_archive",
                    "unsupported_version", "invalid_payload", "policy_violation",
                    "too_large", "crypto_unavailable", "io_error", "source_changed",
                    "unsafe_path", "unsupported_credential", "insufficient_space"})
_DEVICES = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$",
                      *("COM" + str(n) for n in range(1, 10)),
                      *("LPT" + str(n) for n in range(1, 10)),
                      *("COM" + n for n in "¹²³"), *("LPT" + n for n in "¹²³")})


class MDataArchiveError(Exception):
    def __init__(self, code):
        if code not in _CODES:
            raise ValueError("Unknown MData archive error code")
        self.code = code
        super().__init__(code)


def _fail(code):
    raise MDataArchiveError(code) from None


def archive_kind(prefixbytes):
    """Classify an exact magic prefix; authentication is still required."""
    if type(prefixbytes) is not bytes or len(prefixbytes) != len(MAGIC):
        _fail("invalid_archive")
    for kind, magic in _ARCHIVE_MAGICS.items():
        if prefixbytes == magic:
            return kind
    _fail("invalid_archive")


def _translate(error):
    if isinstance(error, OSError) and (error.errno == 28 or getattr(error, "winerror", None) == 112):
        _fail("insufficient_space")
    code = getattr(error, "code", "io_error")
    _fail(code if type(code) is str and code in _CODES else "io_error")


def valid_path(value) -> bool:
    """Relative Unicode path; literal percent is preserved, never URL-decoded."""
    if (type(value) is not str or not 1 <= len(value) <= 1024
            or any(unicodedata.category(char) in ("Cc", "Cs") for char in value)
            or any(char in value for char in '\\:<>|?*"')):
        return False
    try:
        if len(value.encode("utf-8")) > 4096:
            return False
        for part in value.split("/"):
            if (not 1 <= len(part) <= 255 or part in (".", "..")
                    or part.endswith((".", " "))
                    or len(part.encode("utf-16le")) // 2 > 255
                    or part.split(".")[0].rstrip(" .").upper() in _DEVICES):
                return False
    except UnicodeError:
        return False
    return True


valid_file_path = valid_path


@dataclass(frozen=True, repr=False)
class MDataPolicy:
    credential_policy: ArchivePolicy
    file_path_validator: object
    credential_name_validator: object = None

    def __post_init__(self):
        if (type(self.credential_policy) is not ArchivePolicy or not callable(self.file_path_validator)
                or (self.credential_name_validator is not None
                    and not callable(self.credential_name_validator))):
            _fail("policy_violation")

    def __repr__(self):
        return "<MData explicit credential/file policy>"


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _json(data, code):
    try:
        return _session._json(data, code)
    except SessionArchiveError as error:
        _translate(error)


def _password(password):
    try:
        return _session._password(password)
    except SessionArchiveError as error:
        _translate(error)


def _derive(password, salt):
    try:
        return _session._derive(password, salt)
    except SessionArchiveError as error:
        _translate(error)


def validate_manifest(manifest, *, policy: MDataPolicy):
    if type(policy) is not MDataPolicy:
        _fail("policy_violation")
    if (type(manifest) is not dict
            or set(manifest) != {"schema", "sourceClient", "sourceVersion", "credentials", "files"}
            or type(manifest["files"]) is not list):
        _fail("invalid_payload")
    if len(manifest["files"]) > MAX_FILES:
        _fail("too_large")
    credential_payload = dict(manifest, files=[])
    credential_policy = policy.credential_policy
    if policy.credential_name_validator is not None:
        records = manifest["credentials"]
        if type(records) is not list:
            _fail("invalid_payload")
        if len(records) > _session.MAX_CREDENTIALS:
            _fail("too_large")
        names, seen = [], set()
        for item in records:
            if type(item) is not dict or set(item) != {"name", "blob"}:
                _fail("invalid_payload")
            name = item["name"]
            if (type(name) is not str or not 1 <= len(name) <= 256
                    or any(unicodedata.category(char) in ("Cc", "Cs") for char in name)):
                _fail("invalid_payload")
            if not name.isascii():
                _fail("unsupported_credential")
            if name.casefold() in seen:
                _fail("invalid_payload")
            try:
                accepted = policy.credential_name_validator(name)
            except Exception:
                _fail("policy_violation")
            if accepted is not True:
                _fail("policy_violation")
            names.append(name)
            seen.add(name.casefold())
        try:
            credential_policy = ArchivePolicy(credential_names=tuple(names))
        except SessionArchiveError as error:
            _translate(error)
    try:
        clean = _session.validate_payload(credential_payload, policy=credential_policy)
    except SessionArchiveError as error:
        _translate(error)
    paths, total = set(), 0
    for item in manifest["files"]:
        if (type(item) is not dict or set(item) != {"path", "size"}
                or not valid_path(item["path"]) or type(item["size"]) is not int
                or item["size"] < 0):
            _fail("invalid_payload")
        path = item["path"]
        if path.casefold() in paths:
            _fail("invalid_payload")
        try:
            accepted = policy.file_path_validator(path)
        except Exception:
            _fail("policy_violation")
        if accepted is not True:
            _fail("policy_violation")
        total += item["size"]
        if total > MAX_FILE_BYTES:
            _fail("too_large")
        paths.add(path.casefold())
        clean["files"].append({"path": path, "size": item["size"]})
    if len(_canonical(clean)) > MAX_MANIFEST_BYTES:
        _fail("too_large")
    return clean


def _read(stream, size):
    try:
        data = stream.read(size)
    except Exception as error:
        _translate(error)
    if type(data) is not bytes or len(data) > size:
        _fail("io_error")
    return data


def _read_exact(stream, size):
    result = bytearray()
    while len(result) < size:
        data = _read(stream, min(CHUNK_BYTES, size - len(result)))
        if not data:
            _fail("invalid_archive")
        result.extend(data)
    return bytes(result)


def _write(stream, data):
    view, written = memoryview(data), 0
    while written < len(view):
        try:
            count = stream.write(view[written:])
        except Exception as error:
            _translate(error)
        if type(count) is not int or not 1 <= count <= len(view) - written:
            _fail("io_error")
        written += count


def _cipher(key):
    try:
        return _session.AESGCM(key)
    except Exception:
        _fail("crypto_unavailable")


def _crypt(cipher, operation, nonce, data, aad):
    try:
        return getattr(cipher, operation)(nonce, data, aad)
    except _session.InvalidTag:
        _fail("authentication_failed")
    except Exception:
        _fail("crypto_unavailable")


class _ChunkWriter:
    def __init__(self, output, cipher, prefix, aad, expected):
        self.output, self.cipher, self.prefix, self.aad = output, cipher, prefix, aad
        self.expected, self.total, self.counter = expected, 0, 0
        self.buffer = bytearray()
        self.archive_bytes = len(aad)
        _write(output, aad)

    def _emit(self, data, terminal=False):
        if self.counter >= 1 << 32:
            _fail("too_large")
        counter = struct.pack(">I", self.counter)
        frame = struct.pack(">IB", len(data), int(terminal))
        ciphertext = _crypt(self.cipher, "encrypt", self.prefix + counter,
                            data, self.aad + counter + frame)
        _write(self.output, frame)
        _write(self.output, ciphertext)
        self.archive_bytes += len(frame) + len(ciphertext)
        self.counter += 1

    def feed(self, data):
        self.total += len(data)
        if self.total > self.expected:
            _fail("source_changed")
        view, position = memoryview(data), 0
        while position < len(view):
            count = min(CHUNK_BYTES - len(self.buffer), len(view) - position)
            self.buffer.extend(view[position:position + count])
            position += count
            if len(self.buffer) == CHUNK_BYTES:
                self._emit(bytes(self.buffer))
                self.buffer.clear()

    def finish(self):
        if self.total != self.expected:
            _fail("source_changed")
        if self.buffer:
            self._emit(bytes(self.buffer))
            self.buffer.clear()
        self._emit(b"", terminal=True)


def encrypt_stream(manifest, password, source_factory, output, *, policy: MDataPolicy,
                   archive_kind="profile"):
    """Return safe counts; output is only complete after successful return."""
    if type(archive_kind) is not str or archive_kind not in _ARCHIVE_MAGICS:
        _fail("policy_violation")
    magic = _ARCHIVE_MAGICS[archive_kind]
    password_bytes = _password(password)
    clean = validate_manifest(manifest, policy=policy)
    if not callable(source_factory):
        _fail("io_error")
    encoded = _canonical(clean)
    expected = 4 + len(encoded) + sum(item["size"] for item in clean["files"])
    try:
        salt, prefix = os.urandom(16), os.urandom(8)
    except OSError:
        _fail("crypto_unavailable")
    header = {"schema": 1, "sourceClient": _session.SOURCE_CLIENT,
              "sourceVersion": _session.SOURCE_VERSION, "cipher": "AES-256-GCM",
              "chunkBytes": CHUNK_BYTES, "plaintextBytes": expected,
              "noncePrefix": base64.b64encode(prefix).decode("ascii"),
              "kdf": {"name": "scrypt", "n": 32768, "r": 8, "p": 1,
                      "salt": base64.b64encode(salt).decode("ascii")}}
    header_bytes = _canonical(header)
    aad = magic + struct.pack(">H", len(header_bytes)) + header_bytes
    writer = _ChunkWriter(output, _cipher(_derive(password_bytes, salt)), prefix, aad, expected)
    writer.feed(struct.pack(">I", len(encoded)))
    writer.feed(encoded)
    for item in clean["files"]:
        try:
            with source_factory(item["path"]) as source:
                remaining = item["size"]
                while remaining:
                    data = _read(source, min(CHUNK_BYTES, remaining))
                    if not data:
                        _fail("source_changed")
                    writer.feed(data)
                    remaining -= len(data)
                if _read(source, 1):
                    _fail("source_changed")
        except MDataArchiveError:
            raise
        except Exception as error:
            _translate(error)
    writer.finish()
    return {"schema": 1, "files": len(clean["files"]),
            "fileBytes": expected - 4 - len(encoded),
            "dataChunks": writer.counter - 1, "archiveBytes": writer.archive_bytes}


def _header(source):
    prefix = _read_exact(source, len(MAGIC) + 2)
    archive_kind(prefix[:len(MAGIC)])
    length = struct.unpack_from(">H", prefix, len(MAGIC))[0]
    if not 2 <= length <= MAX_HEADER_BYTES:
        _fail("invalid_archive")
    raw = _read_exact(source, length)
    header = _json(raw, "invalid_archive")
    if (type(header) is not dict
            or set(header) != {"schema", "sourceClient", "sourceVersion", "cipher", "chunkBytes",
                               "plaintextBytes", "noncePrefix", "kdf"}
            or type(header["schema"]) is not int or header["schema"] != 1
            or type(header["sourceClient"]) is not str or type(header["sourceVersion"]) is not str
            or header["cipher"] != "AES-256-GCM"
            or type(header["chunkBytes"]) is not int or header["chunkBytes"] != CHUNK_BYTES
            or type(header["plaintextBytes"]) is not int
            or not 6 <= header["plaintextBytes"] <= MAX_PLAINTEXT_BYTES):
        _fail("invalid_archive")
    if header["sourceClient"] != _session.SOURCE_CLIENT or header["sourceVersion"] != _session.SOURCE_VERSION:
        _fail("unsupported_version")
    kdf = header["kdf"]
    if (type(kdf) is not dict or set(kdf) != {"name", "n", "r", "p", "salt"}
            or kdf["name"] != "scrypt"
            or any(type(kdf[key]) is not int or kdf[key] != expected
                   for key, expected in (("n", 32768), ("r", 8), ("p", 1)))):
        _fail("invalid_archive")
    try:
        salt = _session._base64(kdf["salt"], 16, "invalid_archive")
        nonce_prefix = _session._base64(header["noncePrefix"], 8, "invalid_archive")
    except SessionArchiveError as error:
        _translate(error)
    if len(salt) != 16 or len(nonce_prefix) != 8 or _canonical(header) != raw:
        _fail("invalid_archive")
    return header, salt, nonce_prefix, prefix + raw


class _PlainReader:
    def __init__(self, source, cipher, prefix, aad, expected):
        self.source, self.cipher, self.prefix, self.aad = source, cipher, prefix, aad
        self.expected, self.total, self.counter = expected, 0, 0
        self.buffer, self.ended = b"", False

    def _next(self):
        if self.ended or self.counter >= 1 << 32:
            _fail("invalid_archive")
        frame = _read_exact(self.source, 5)
        length, flag = struct.unpack(">IB", frame)
        if (length > CHUNK_BYTES or flag not in (0, 1)
                or (flag == 1 and length != 0) or (flag == 0 and length == 0)):
            _fail("invalid_archive")
        ciphertext = _read_exact(self.source, length + 16)
        counter = struct.pack(">I", self.counter)
        plaintext = _crypt(self.cipher, "decrypt", self.prefix + counter, ciphertext,
                           self.aad + counter + frame)
        self.counter += 1
        self.total += len(plaintext)
        if self.total > self.expected:
            _fail("invalid_archive")
        if flag:
            if self.total != self.expected or _read(self.source, 1):
                _fail("invalid_archive")
            self.ended = True
        return plaintext

    def read_exact(self, size):
        result = bytearray()
        while len(result) < size:
            if not self.buffer:
                self.buffer = self._next()
                if not self.buffer:
                    _fail("invalid_archive")
            count = min(size - len(result), len(self.buffer))
            result.extend(self.buffer[:count])
            self.buffer = self.buffer[count:]
        return bytes(result)

    def finish(self):
        if self.buffer or self._next() or not self.ended:
            _fail("invalid_archive")


def decrypt_stream(source, password, sink_factory, *, policy: MDataPolicy, preflight=None,
                   expected_kind=None):
    """Write authenticated bytes ONLY to staging; return manifest after EOF/tag."""
    if (expected_kind is not None
            and (type(expected_kind) is not str or expected_kind not in _ARCHIVE_MAGICS)):
        _fail("policy_violation")
    if type(policy) is not MDataPolicy:
        _fail("policy_violation")
    if not callable(sink_factory):
        _fail("io_error")
    if preflight is not None and not callable(preflight):
        _fail("io_error")
    password_bytes = _password(password)
    header, salt, prefix, aad = _header(source)
    if expected_kind is not None and archive_kind(aad[:len(MAGIC)]) != expected_kind:
        _fail("invalid_archive")
    reader = _PlainReader(source, _cipher(_derive(password_bytes, salt)), prefix, aad,
                          header["plaintextBytes"])
    length = struct.unpack(">I", reader.read_exact(4))[0]
    if not 2 <= length <= MAX_MANIFEST_BYTES:
        _fail("too_large" if length > MAX_MANIFEST_BYTES else "invalid_payload")
    raw = reader.read_exact(length)
    clean = validate_manifest(_json(raw, "invalid_payload"), policy=policy)
    if (_canonical(clean) != raw
            or 4 + length + sum(item["size"] for item in clean["files"]) != header["plaintextBytes"]):
        _fail("invalid_archive")
    # Manifest bytes passed GCM authentication and policy/size checks. The
    # archive still requires terminal authentication before any installation.
    # Run the trusted count-only callback outside sink exception translation.
    if preflight is not None:
        preflight(sum(item["size"] for item in clean["files"]), len(clean["files"]))
    for item in clean["files"]:
        try:
            with sink_factory(item["path"], item["size"]) as sink:
                remaining = item["size"]
                while remaining:
                    data = reader.read_exact(min(CHUNK_BYTES, remaining))
                    _write(sink, data)
                    remaining -= len(data)
        except MDataArchiveError:
            raise
        except Exception as error:
            _translate(error)
    reader.finish()
    return clean
