"""Generate version-pinned, reversible Qt-resource patches; never write a binary.

The matching MAX 26.35.1 max.exe SHA256 is mandatory in the caller's file plan.
An append patch must be applied after the equal-length patches and reversed
before them. Keep every generated patch together. No runtime injection is used.
"""
from __future__ import annotations

import hashlib
import struct
import zlib

BASELINE_SHA256 = "d79837f5090c63bce820818355d9cc05b73b8c9bf5da9dddb47cdaf11aea3182"
BASELINE_LENGTH = 0x4BC3B50
RESOURCE_PATH = ":/qt/qml/OneMeUi/LeadingSettingsBar.qml"
RESOURCE_DATA_RVA = 0x2F333A0
RESOURCE_NODE_RAW = 0x300A542
CACHE_KEY_RAW = 0x267CA00
SECTION_RVA = 0x4BF7000
SECTION_RAW = 0x4BC3C00
SECTION_HEADER_RAW = 0x3D8


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def _encode(qml_source: bytes):
    if not isinstance(qml_source, bytes) or not 1 <= len(qml_source) <= 1024 * 1024:
        raise ValueError("Expected 1..1048576 bytes of UTF-8 QML source")
    qml_source.decode("utf-8")
    packed = struct.pack(">I", len(qml_source)) + zlib.compress(qml_source, 9)
    record = struct.pack(">I", len(packed)) + packed
    raw_size = _align(len(record), 0x200)
    image_size = _align(SECTION_RVA + len(record), 0x1000)
    digest = hashlib.sha256(qml_source).digest()
    # A deterministic resource timestamp changes with content, invalidating any
    # older disk QML cache for this URL while staying within a normal date range.
    timestamp = 1767225600000 + int.from_bytes(digest[:8], "big") % 31536000000
    if timestamp == 0x1A129D99920:
        timestamp += 1
    return packed, record, raw_size, image_size, timestamp


def describe_resource_extension(qml_source: bytes) -> dict:
    packed, record, raw_size, image_size, timestamp = _encode(qml_source)
    return {
        "resource_path": RESOURCE_PATH,
        "source_sha256": hashlib.sha256(qml_source).hexdigest(),
        "source_length": len(qml_source),
        "zlib_resource_length": len(packed),
        "section_name": ".mxmui",
        "section_rva": SECTION_RVA,
        "section_raw": SECTION_RAW,
        "section_virtual_size": len(record),
        "section_raw_size": raw_size,
        "new_size_of_image": image_size,
        "resource_timestamp_ms": timestamp,
        "append_length": SECTION_RAW - BASELINE_LENGTH + raw_size,
        "cache_key_before": RESOURCE_PATH[1:],
        "cache_key_after": RESOURCE_PATH[1:-1] + "x",
    }


def make_resource_patches(qml_source: bytes) -> list[dict]:
    """Return complete guarded patches for one existing QML resource URL."""
    packed, record, raw_size, image_size, timestamp = _encode(qml_source)
    header = struct.pack(
        "<8sIIIIIIHHI", b".mxmui\0\0", len(record), SECTION_RVA, raw_size,
        SECTION_RAW, 0, 0, 0, 0, 0x40000040,
    )
    tail = (
        b"\0" * (SECTION_RAW - BASELINE_LENGTH)
        + record + b"\0" * (raw_size - len(record))
    )
    before_key = RESOURCE_PATH[1:].encode("utf-16-le")
    after_key = (RESOURCE_PATH[1:-1] + "x").encode("utf-16-le")
    def patch(identifier, offset, expected, replacement, reason, kind=None):
        if kind != "append" and len(expected) != len(replacement):
            raise ValueError("Only the final append patch may change file length")
        result = {
            "id": "ui_privacy." + identifier,
            "offset": offset,
            "expected": expected.hex(),
            "replacement": replacement.hex(),
            "reason": reason,
        }
        if kind:
            result["kind"] = kind
        return result
    return [
        patch("section_count", 0x146, struct.pack("<H", 10), struct.pack("<H", 11),
              "Add one PE section using the final 40 bytes of existing header padding."),
        patch("initialized_data_size", 0x160, struct.pack("<I", 0x281D800),
              struct.pack("<I", 0x281D800 + raw_size),
              "Account for the appended read-only initialized-data section."),
        patch("image_size", 0x190, struct.pack("<I", 0x4BF7000),
              struct.pack("<I", image_size),
              "Map the appended section with the original 4096-byte section alignment."),
        patch("clear_obsolete_checksum", 0x198, bytes.fromhex("a21dbd04"), b"\0" * 4,
              "Clear the original PE checksum after modifying the executable."),
        patch("section_header", SECTION_HEADER_RAW, b"\0" * 40, header,
              "Create .mxmui as read-only initialized data, without executable or writable flags; preserve all existing sections and certificate bytes."),
        patch("resource_data_offset", RESOURCE_NODE_RAW + 10,
              struct.pack(">I", 0x7AC4A), struct.pack(">I", SECTION_RVA - RESOURCE_DATA_RVA),
              "Redirect only RCC v3 node 147 LeadingSettingsBar.qml to the new length-prefixed zlib record; preserve its path, locale and compression flag."),
        patch("resource_timestamp", RESOURCE_NODE_RAW + 14,
              bytes.fromhex("000001a129d99920"), struct.pack(">Q", timestamp),
              "Change the QML resource timestamp with its source content so old disk-cached bytecode for this URL is rejected."),
        patch("aot_cache_key", CACHE_KEY_RAW, before_key, after_key,
              "Rename only this QHash cached-unit key from .qml to .qmx; its unique constructor reference is RVA0x1b0877. The original resource URL now misses the AOT lookup and Qt loads the changed source, while every other AOT unit remains available."),
        patch("append_resource_section", BASELINE_LENGTH, b"", tail,
              "Append alignment padding and the complete .mxmui RCC data record after the untouched original file; reverse by verifying and removing this exact tail.",
              kind="append"),
    ]


def make_resource_spec(binary: bytes, resource_path: str, replacement_source: bytes) -> dict:
    """Validate the exact source executable and return a builder file spec."""
    if resource_path != RESOURCE_PATH:
        raise ValueError("This pinned helper supports only " + RESOURCE_PATH)
    if len(binary) != BASELINE_LENGTH or hashlib.sha256(binary).hexdigest() != BASELINE_SHA256:
        raise ValueError("Resource extension requires the exact MAX 26.35.1 baseline max.exe")
    patches = make_resource_patches(replacement_source)
    for item in patches:
        expected = bytes.fromhex(item["expected"])
        offset = item["offset"]
        if item.get("kind") == "append":
            if offset != len(binary) or expected:
                raise ValueError("Invalid resource append guard")
        elif binary[offset:offset + len(expected)] != expected:
            raise ValueError("Resource byte guard failed: " + item["id"])
    return {"relative_path": "max.exe", "sha256": BASELINE_SHA256, "patches": patches}


EVIDENCE = {
    "max_exe_sha256": BASELINE_SHA256,
    "baseline_length": BASELINE_LENGTH,
    "resource_registration_call_rva": 0x45DD1E,
    "resource_version": 3,
    "resource_tree_rva": 0x300A8A0,
    "resource_names_rva": 0x3002F60,
    "resource_data_rva": RESOURCE_DATA_RVA,
    "resource_node": 147,
    "resource_node_raw": RESOURCE_NODE_RAW,
    "old_resource_payload_raw": 0x2FACFEE,
    "old_resource_payload_length": 2683,
    "resource_compression": "zlib with big-endian decompressed-length prefix",
    "aot_key_raw": CACHE_KEY_RAW,
    "aot_key_reference_rva": 0x1B0877,
    "aot_cached_unit_rva": 0x26739A0,
    "aot_compiled_data_rva": 0x2B95540,
    "qt_version": "6.11.2",
    "qt_source_urls": [
        "https://raw.githubusercontent.com/qt/qtdeclarative/v6.11.2/src/qml/qml/qqmlmetatype.cpp",
        "https://raw.githubusercontent.com/qt/qtdeclarative/v6.11.2/src/qml/qml/qqmltypeloader.cpp",
        "https://raw.githubusercontent.com/qt/qtdeclarative/v6.11.2/src/qml/qml/qqmltypedata.cpp",
        "https://raw.githubusercontent.com/qt/qtdeclarative/v6.11.2/src/qml/common/qv4compileddata.cpp",
    ],
    "limitations": [
        "Static generation does not confirm QML syntax, imports or runtime layout; guarded UI smoke testing is required.",
        "Changing max.exe invalidates its Authenticode signature; existing certificate bytes are retained for reversibility.",
    ],
}
