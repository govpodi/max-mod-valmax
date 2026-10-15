"""Pure pinned guards for export-owned, value-free native history observation.

The launcher separately verifies the complete mod manifest/reversal. max.exe
may contain the declared ValMAX patches, so this module pins relevant native
code and type slots rather than requiring its unmodified whole-file hash.
Only program binaries are read; no profile, credentials, network or MAX process.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import struct

import pefile


CONTRACT = "valmax-chat-end-v1"
QT_SHA256 = "22c113a5644b875b2c85482723cd5d2e6a3a4029169778fcc78adde08ef3b6aa"
QT_FILE_SIZE, QT_IMAGE_SIZE = 10363704, 10395648
BASELINE_MAX_IMAGE_SIZE = 79654912
MARKER_PREFIX, MARKER_LENGTH = "valmax-export-", 46
MAX_NATIVE_COUNT = 100000
MAX_ANCHORS = (
    (0xE27A00, "48895424105355565741544155415641574883ec584d8be1458be8488bf2488bd9"),
    (0xE29380, "488d8170060000488902488d056786490248894208488bc2c3"),
    (0xE293A0, "488d8180060000488902488d054786490248894208488bc2c3"),
    (0x11E168C, "488b471848394710410f94c1448b473c8b5738488bcee856f9e9fe4c8d47108b573c488bcee8879eebfe"),
    (0x11E1804, "4c8b57404533ff458bcf4d390e410f94c1448b473c488b57204c897f20488b4f184c897f18488b47104c897f10488945e048894de8488955f04c8955d044894dd88b45dc8945dc4c8d4dd0488d55e0488bce41ff5328"),
    (0x4D4B4, "e947a5dd00"),
    # Entire Presenter entry through the virtual IView call (618 bytes).
    # RDI receives the lambda at 11e160c and is not overwritten before that
    # call; nested callees preserve it under Win64's nonvolatile-register ABI.
    (0x11E15F0,
     "48895c241848895424105556574154415541564157488bec4883ec70488bf90f57c0f30f7f45c04c8b41084d85c07423418b400885c0741b0f1f8400000000008d4801f0410fb148080f842501000085c075ed488b75c0488b5dc84885f60f846c0200004c8db690010000498b0e4885c90f84f5010000488b47404839010f85e801000048ff49080f1047280f2945d0488d55d0488bcee800a5ebfe488b471848394710410f94c1448b473c8b5738488bcee856f9e9fe4c8d47108b573c488bcee8879eebfe4c8b67104c8b7f184d3be70f841f010000837f38000f8515010000ff1551b291034c8bc04d2bfc48b8d504e3adec73484d49f7ef48c1fa07488bca48c1e93f4803d1493bd00f83e50000008b4f3c85c90f84c700000083e901746783f9010f85cc000000488d4e68488d5540e8db32e2fe488b4f18488b5710483bd17418488b004839020f84a60000004881c2a8010000483bd175eb498b0648ff4008488b5740488bcee85272e9fee982000000488b37488975c0488b5f0848895dc8e9d3feffff498b0648ff4008488b5740488bcee8f50ee7fe488d4e68488d5540e86a32e2fe488b5718488b4f10483bca74a7488b00483901740e4881c1a8010000483bca75efeb9141b101ba0100000041b802000000488bcee82cf8e9feeb13498b0648ff4008488b5740488bcee89a0ee7fe498b064885c0740f48837808007508498bcee8e83be6fe488b75484c8b1e4c8b57404533ff458bcf4d390e410f94c1448b473c488b57204c897f20488b4f184c897f18488b47104c897f10488945e048894de8488955f04c8955d044894dd88b45dc8945dc4c8d4dd0488d55e0488bce41ff5328"),
)
MAX_VTABLES = ((0x376B768, 0x2C575), (0x376B808, 0x4D4B4))
QT_ANCHORS = (
    (0xF1C00, "405341564883ec48488b59084c8bf248896c24684889742470488b43504885"),
    (0xEABD0, "48894c24085355565741544155415641574883ec684c8bf1488d0519063100"),
    (0x190F0, "e90b8b0d00"),
    (0xE8053, "83fa02751b4585c00f85a8000000488b13488b5c24604883c4405fe98d9b0000"),
)
QT_EXPORTS = (
    ("?setObjectName@QObject@@QEAAXAEBVQString@@@Z", 0x190F0),
    ("?qt_static_metacall@QObject@@CAXPEAV1@W4Call@QMetaObject@@HPEAPEAX@Z", 0xE7E00),
    ("??1QObject@@UEAA@XZ", 0xEABD0),
)
BOUND_FIELDS = {"kind", "jobId", "event"}
PAGE_FIELDS = BOUND_FIELDS | {"direction", "count", "pendingDone", "moreBackward", "moreForward"}


def checked_event(value):
    """Reject unknown/raw fields; return only the exact metadata-only contract."""
    if (type(value) is not dict or value.get("kind") != "chat-export-history"
            or type(value.get("jobId")) is not str
            or re.fullmatch(r"[0-9a-f]{32}", value["jobId"]) is None):
        raise ValueError("Invalid export history event")
    if value.get("event") == "bound":
        if set(value) != BOUND_FIELDS: raise ValueError("Invalid export history event")
    elif value.get("event") == "page":
        if (set(value) != PAGE_FIELDS or value["direction"] not in ("backward", "center", "forward")
                or type(value["count"]) is not int or not 0 <= value["count"] <= MAX_NATIVE_COUNT
                or any(type(value[key]) is not bool for key in ("pendingDone", "moreBackward", "moreForward"))):
            raise ValueError("Invalid export history event")
    else:
        raise ValueError("Invalid export history event")
    return dict(value)


def proven_directions(value):
    """Empty/unfinished callbacks never prove EOF, even with native flags false."""
    event = checked_event(value)
    if event["event"] != "page" or not event["count"] or not event["pendingDone"]: return ()
    return tuple(direction for direction, field in (("backward", "moreBackward"), ("forward", "moreForward"))
                 if event["direction"] in (direction, "center") and not event[field])


def _image(path):
    data = path.read_bytes()
    pe = pefile.PE(data=data, fast_load=True)
    if pe.FILE_HEADER.Machine != 0x8664 or pe.OPTIONAL_HEADER.Magic != 0x20B:
        raise ValueError("Unsupported export history binary")
    return data, pe


def _guard(pe, anchors, slots=()):
    for rva, expected in anchors:
        section = pe.get_section_by_rva(rva)
        if (section is None or not section.Characteristics & 0x20000000
                or pe.get_data(rva, len(expected) // 2).hex() != expected):
            raise ValueError("Unsupported export history code")
    for rva, target in slots:
        if struct.unpack("<Q", pe.get_data(rva, 8))[0] != pe.OPTIONAL_HEADER.ImageBase + target:
            raise ValueError("Unsupported export history type")


def specification(root):
    root = Path(root)
    if not root.is_absolute(): raise ValueError("Export observer requires an absolute program directory")
    maximum, qt = root / "max.exe", root / "Qt6Core.dll"
    max_data, max_pe = _image(maximum)
    qt_data, qt_pe = _image(qt)
    try:
        _guard(max_pe, MAX_ANCHORS, MAX_VTABLES)
        if max_pe.OPTIONAL_HEADER.SizeOfImage < BASELINE_MAX_IMAGE_SIZE:
            raise ValueError("Unsupported export history image")
        if (len(qt_data) != QT_FILE_SIZE or hashlib.sha256(qt_data).hexdigest() != QT_SHA256
                or qt_pe.OPTIONAL_HEADER.SizeOfImage != QT_IMAGE_SIZE):
            raise ValueError("Unsupported export history Qt binary")
        _guard(qt_pe, QT_ANCHORS)
        qt_pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
        exports = {item.name.decode("ascii"): item.address for item in qt_pe.DIRECTORY_ENTRY_EXPORT.symbols if item.name}
        if any(exports.get(name) != rva for name, rva in QT_EXPORTS):
            raise ValueError("Unsupported export history Qt exports")
        return {"schema": 1, "contract": CONTRACT,
                "max": {"modulePath": str(maximum), "imageSize": max_pe.OPTIONAL_HEADER.SizeOfImage,
                        "anchors": [{"rva": rva, "expected": expected} for rva, expected in MAX_ANCHORS],
                        "vtables": [{"rva": rva, "target": target} for rva, target in MAX_VTABLES]},
                "qt": {"modulePath": str(qt), "sha256": QT_SHA256, "imageSize": QT_IMAGE_SIZE,
                       "anchors": [{"rva": rva, "expected": expected} for rva, expected in QT_ANCHORS],
                       "exports": [{"name": name, "rva": rva} for name, rva in QT_EXPORTS]}}
    except (KeyError, IndexError, struct.error, UnicodeError, pefile.PEFormatError):
        raise ValueError("Unsupported export history binary") from None
    finally:
        max_pe.close(); qt_pe.close()


def source_for(root):
    """Append to the verified main-process observer; it never replaces RPC exports."""
    config = specification(root)
    source = Path(__file__).with_name("chat_export_end_monitor.js").read_text("utf8")
    return "const VALMAX_CHAT_END_SPEC=" + json.dumps(config, separators=(",", ":")) + ";\n" + source
