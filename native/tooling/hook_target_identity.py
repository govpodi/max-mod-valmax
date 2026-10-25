"""Read-only packaging identity checks; never loads DLLs or proves activation.

The build receipt must independently freeze these sidecars and DLLs.  A matching
SHA literal is a necessary consistency check, not proof that a guard executes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import struct
from typing import Callable

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_FAMILIES = {
    "privacy": ("valmax_privacy.dll", "privacy-pins.json", "privacy_pins.generated.h",
                "generatedHeaderSha256", ("max-service/max-service.exe", "config.dll",
                "max-service/config.dll", "core.dll", "tracernative.dll", "max-service/tracernative.dll")),
    "ghost": ("valmax_ghost.dll", "ghost-pins.json", "ghost_pins.generated.h", "headerSha256",
              ("core.dll", "max.exe")),
    "notification": ("valmax_notifications.dll", "notification-pins.json",
                     "notification_pins.generated.h", "generatedSha256", ("max.exe", "notifications.dll")),
}
_TOP_KEYS = {
    "privacy": {"schema", "runtimeRoot", "namespace", "files", "manifestSha256", "sourceSha256",
                "generatedHeaderSha256", "rules", "staticPatches", "callbacks", "networkObserved", "realProfilesAccessed"},
    "ghost": {"schema", "runtimeRoot", "isolatedNamespace", "canonicalCoreSha256", "sourceSha256",
              "headerSha256", "images", "codeGuards", "vtableGuards", "realProfilesAccessed"},
    "notification": {"schema", "runtimeRoot", "images", "specSha256", "asset", "generatedSha256", "realProfilesAccessed"},
}


class HookTargetIdentityError(RuntimeError):
    def __init__(self, reason: str, details=None):
        super().__init__(reason)
        self.reason, self.details = reason, details


def _require(value, reason: str, details=None):
    if not value:
        raise HookTargetIdentityError(reason, details)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _relative(value) -> str:
    _require(isinstance(value, str) and value and "\\" not in value and "\x00" not in value,
             "unsafe_target_path")
    path = PurePosixPath(value)
    _require(bool(path.parts) and not path.is_absolute() and not PureWindowsPath(value).drive
             and value == path.as_posix() and all(part not in (".", "..") for part in path.parts)
             and all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in path.parts), "unsafe_target_path")
    return value


def _plain_path(path: Path):
    # Check lexical ancestors before resolve() can hide a junction/symlink.
    for candidate in reversed((path, *path.parents)):
        try:
            info = candidate.lstat()
        except OSError as error:
            raise HookTargetIdentityError("hook_input_missing", str(candidate)) from error
        _require(not stat.S_ISLNK(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & 0x400),
                 "unsafe_hook_input", str(candidate))


def _read(path: Path, limit: int) -> bytes:
    _plain_path(path)
    try:
        _require(path.is_file() and 0 < path.stat().st_size <= limit, "invalid_hook_input", path.name)
        data = path.read_bytes()
    except OSError as error:
        raise HookTargetIdentityError("hook_input_unreadable", path.name) from error
    _require(0 < len(data) <= limit, "invalid_hook_input", path.name)
    return data


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate_metadata_key", key)
        result[key] = value
    return result


def _metadata(data: bytes, family: str):
    try:
        value = json.loads(data.decode("utf8"), object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
    except (UnicodeError, ValueError, RecursionError) as error:
        raise HookTargetIdentityError("invalid_hook_metadata", family) from error
    _require(isinstance(value, dict) and set(value) == _TOP_KEYS[family], "invalid_hook_metadata_keys", family)
    _require(type(value["schema"]) is int and value["schema"] == 1, "invalid_hook_metadata_schema", family)
    _require(value["realProfilesAccessed"] is False, "invalid_hook_metadata_scope", family)
    return value


def _integer(value, *, positive=True):
    return type(value) is int and (value > 0 if positive else value >= 0) and value < 2**32


def _identity(relative, row):
    relative = _relative(relative)
    _require(isinstance(row, dict) and _HASH.fullmatch(str(row.get("sha256", "")))
             and _integer(row.get("size")) and ("imageSize" not in row or _integer(row["imageSize"])),
             "invalid_target_identity", relative)
    return {"relative": relative, "sha256": row["sha256"], "size": row["size"],
            **({"imageSize": row["imageSize"]} if "imageSize" in row else {})}


def _header_images(header: bytes, family: str):
    try:
        text = header.decode("ascii")
    except UnicodeError as error:
        raise HookTargetIdentityError("invalid_pin_header", family) from error
    if family == "privacy":
        block = re.search(r"inline\s+constexpr\s+FilePin\s+kFiles\[\]\s*=\s*\{(.*?)\};", text, re.S)
        _require(block is not None, "invalid_pin_header", family)
        pattern = re.compile(r'\{L"([^"\\]+)","([0-9a-f]{64})",(\d+)ull,(\d+),([12]),kPatches(\d+)\}')
        matches = list(pattern.finditer(block[1]))
        _require(len(matches) == 6 and re.fullmatch(r"[\s,]*", pattern.sub("", block[1])) is not None,
                 "invalid_pin_header", family)
        rows = []
        for index, match in enumerate(matches):
            relative, digest, size, image_size, role, patch_index = match.groups()
            _require(int(patch_index) == index, "invalid_pin_header", family)
            rows.append({"relative": _relative(relative), "sha256": digest, "size": int(size),
                         "imageSize": int(image_size), "role": int(role)})
        return rows
    names = ("Core", "Main") if family == "ghost" else ("Main", "Backend")
    rows = []
    _require(len(re.findall(r"inline\s+constexpr\s+ImagePin\s+", text)) == 2, "invalid_pin_header", family)
    for name, relative in zip(names, _FAMILIES[family][4]):
        pattern = (r'inline\s+constexpr\s+ImagePin\s+k' + name
                   + r'\s*=\s*\{"([0-9a-f]{64})",(\d+)ull,(\d+)u?,k' + name + r'Guards,k' + name + r'Slots\};')
        matches = re.findall(pattern, text)
        _require(len(matches) == 1, "invalid_pin_header", family)
        digest, size, image_size = matches[0]
        rows.append({"relative": relative, "sha256": digest, "size": int(size), "imageSize": int(image_size)})
    return rows


def _pe_image_size(data: bytes) -> int:
    # Only the ordinary PE identity fields needed here; no code/RVA inspection.
    try:
        _require(len(data) >= 64 and data[:2] == b"MZ", "invalid_target_pe")
        pe = struct.unpack_from("<I", data, 0x3c)[0]
        _require(pe >= 64 and pe + 24 <= len(data) and data[pe:pe + 4] == b"PE\0\0", "invalid_target_pe")
        machine, optional_bytes = struct.unpack_from("<H", data, pe + 4)[0], struct.unpack_from("<H", data, pe + 20)[0]
        optional = pe + 24
        _require(machine == 0x8664 and optional_bytes >= 60 and optional + optional_bytes <= len(data)
                 and struct.unpack_from("<H", data, optional)[0] == 0x20b, "invalid_target_pe")
        image_size = struct.unpack_from("<I", data, optional + 56)[0]
        _require(image_size > 0, "invalid_target_pe")
        return image_size
    except struct.error as error:
        raise HookTargetIdentityError("invalid_target_pe") from error


def verify_hook_targets(hook_root: Path, read_target: Callable[[str], bytes]) -> dict:
    """Return frozen static identities or raise; never returns partial success.

    read_target receives a validated relative path below the staged ``mod`` root.
    Its filesystem boundary and the independent build receipt remain the caller's
    responsibility. runtimeRoot in legacy metadata is provenance, never read.
    """
    hook_root = Path(hook_root)
    _require(hook_root.is_absolute(), "absolute_hook_root_required")
    _plain_path(hook_root)
    _require(hook_root.is_dir(), "invalid_hook_root")
    hooks, targets, frozen = {}, [], {}
    for family, (dll, pins_file, header_file, header_key, expected_paths) in _FAMILIES.items():
        inputs = {pins_file: _read(hook_root / pins_file, 128 * 1024),
                  header_file: _read(hook_root / header_file, 256 * 1024),
                  dll: _read(hook_root / dll, 16 * 1024 * 1024)}
        frozen.update(inputs)
        metadata = _metadata(inputs[pins_file], family)
        _require(metadata[header_key] == _sha(inputs[header_file]), "pin_header_sha_mismatch", family)
        header_rows = _header_images(inputs[header_file], family)
        if family == "privacy":
            _require(isinstance(metadata["files"], list) and len(metadata["files"]) == 6, "invalid_target_count", family)
            rows = [_identity(row.get("relative"), row) for row in metadata["files"] if isinstance(row, dict)]
            _require(len(rows) == 6, "invalid_target_identity", family)
            for row, original, header in zip(rows, metadata["files"], header_rows):
                _require(type(original.get("role")) is int and original["role"] == header["role"],
                         "pin_header_identity_mismatch", family)
        else:
            _require(isinstance(metadata["images"], dict), "invalid_target_identity", family)
            rows = [_identity(relative, row) for relative, row in metadata["images"].items()]
            rows.sort(key=lambda row: expected_paths.index(row["relative"]) if row["relative"] in expected_paths else len(expected_paths))
        _require(len(rows) == len(expected_paths) and len({row["relative"] for row in rows}) == len(rows)
                 and tuple(row["relative"] for row in rows) == expected_paths, "unexpected_or_duplicate_targets", family)
        _pe_image_size(inputs[dll])
        for row, header in zip(rows, header_rows):
            _require(all(row.get(key, header[key]) == header[key] for key in ("relative", "sha256", "size", "imageSize")),
                     "pin_header_identity_mismatch", {"hook": family, "relative": row["relative"]})
            row["imageSize"] = header["imageSize"]
            _require(row["sha256"].encode("ascii") + b"\0" in inputs[dll], "compiled_pin_literal_missing",
                     {"hook": family, "relative": row["relative"]})
            targets.append({"hook": family, **row})
        hooks[family] = {"dll": dll, "dllSha256": _sha(inputs[dll]), "dllBytes": len(inputs[dll]),
                         "pinsFile": pins_file, "pinsSha256": _sha(inputs[pins_file]),
                         "headerFile": header_file, "headerSha256": _sha(inputs[header_file])}
    identities, mismatches = {}, []
    for row in targets:
        relative = row["relative"]
        expected = {key: row[key] for key in ("sha256", "size", "imageSize")}
        if relative not in identities:
            try:
                data = read_target(relative)
            except (OSError, KeyError) as error:
                raise HookTargetIdentityError("target_missing", relative) from error
            _require(isinstance(data, bytes), "target_reader_must_return_bytes", relative)
            identities[relative] = {"sha256": _sha(data), "size": len(data), "imageSize": _pe_image_size(data)}
        else:
            previous = next(item for item in targets if item["relative"] == relative)
            _require(all(previous[key] == expected[key] for key in expected), "conflicting_target_identity", relative)
        actual = identities[relative]
        if expected != actual:
            mismatches.append({"hook": row["hook"], "relative": relative, "expected": expected, "actual": actual})
    for name, before in frozen.items():
        _require(_read(hook_root / name, max(len(before), 1)) == before, "hook_input_changed", name)
    _require(not mismatches, "target_identity_mismatch", mismatches)
    _require(len(targets) == 10 and len(identities) == 8, "invalid_total_target_count")
    return {"schema": 1, "verified": True, "activationVerified": False,
            "verifierSha256": _sha(Path(__file__).read_bytes()),
            "targetCount": len(targets), "uniqueTargetCount": len(identities), "hooks": hooks, "targets": targets}
