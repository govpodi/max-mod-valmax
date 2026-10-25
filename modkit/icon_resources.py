"""Clone the pinned native icon RCC and add ValMAX SVGs using official rcc.

Build-time helper only: reads immutable program bytes, writes an isolated build
directory, optionally registers the result only in this Python QA process.
It never modifies a native image or registers resources in MAX.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import struct
import subprocess
import xml.etree.ElementTree as ET
import zlib

import pefile
from valmax_icons import resource_payloads

BASELINE_SHA256 = "d79837f5090c63bce820818355d9cc05b73b8c9bf5da9dddb47cdaf11aea3182"
TREE_RAW = 0x33FFCA0
NAMES_RAW = 0x33FAAC0
DATA_RAW = 0x336D810
ORIGINAL_COUNT = 546
FIXED_MTIME = 1735689600
DEFAULT_RCC = Path("D:/CodexLabs/max-mod-26.35.1/venv/Lib/site-packages/PySide6/rcc.exe")
ANCHORS = {"register": {"data": 0x9E2B24, "names": 0x9E2B30, "tree": 0x9E2B37},
           "unregister": {"data": 0x9E2AEC, "names": 0x9E2AF6, "tree": 0x9E2AFD}}
ORIGINAL_RVAS = {"data": 0x336E810, "names": 0x33FBAC0, "tree": 0x3400CA0}
LEA_PREFIX = {"data": b"\x4c\x8d\x0d", "names": b"\x4c\x8d\x05", "tree": b"\x48\x8d\x15"}


def sha(data): return hashlib.sha256(data).hexdigest()


def native_payloads(binary):
    if sha(binary) != BASELINE_SHA256:
        raise ValueError("Exact immutable MAX baseline required for icon RCC")
    result = {}
    for index in range(2, ORIGINAL_COUNT + 2):
        node = TREE_RAW + index * 22
        name = NAMES_RAW + int.from_bytes(binary[node:node + 4], "big")
        length = int.from_bytes(binary[name:name + 2], "big")
        if not 1 <= length <= 256: raise ValueError("Invalid native icon name")
        label = binary[name + 6:name + 6 + length * 2].decode("utf-16-be")
        if PurePosixPath(label).name != label or "/" in label or "\\" in label or ":" in label or label in (".", ".."):
            raise ValueError("Unsafe native icon alias")
        flags = int.from_bytes(binary[node + 4:node + 6], "big")
        if flags not in (0, 1) or binary[node + 6:node + 10] != b"\0\0\0\1":
            raise ValueError("Unsupported icon compression/locale")
        record = DATA_RAW + int.from_bytes(binary[node + 10:node + 14], "big")
        size = int.from_bytes(binary[record:record + 4], "big")
        if not 0 < size <= 1024 * 1024 or record + 4 + size > len(binary):
            raise ValueError("Invalid native icon record")
        payload = binary[record + 4:record + 4 + size]
        if flags:
            expected = int.from_bytes(payload[:4], "big"); decoder = zlib.decompressobj()
            data = decoder.decompress(payload[4:], 1024 * 1024 + 1)
            if len(data) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise ValueError("Invalid compressed native icon")
            payload = data
        path = ":/icon_pack/" + label
        if path in result: raise ValueError("Duplicate native icon")
        result[path] = payload
    if len(result) != ORIGINAL_COUNT: raise ValueError("Incomplete native icon table")
    return result


def registration_anchors(binary):
    if sha(binary) != BASELINE_SHA256: raise ValueError("Exact immutable MAX baseline required")
    pe = pefile.PE(data=binary, fast_load=True); result = []
    for lifetime, fields in ANCHORS.items():
        for name, rva in fields.items():
            raw = pe.get_offset_from_rva(rva); before = binary[raw:raw + 7]
            if before[:3] != LEA_PREFIX[name] or rva + 7 + struct.unpack_from("<i", before, 3)[0] != ORIGINAL_RVAS[name]:
                raise ValueError("Native icon RCC pointer anchor changed")
            result.append({"lifetime": lifetime, "field": name, "rva": rva, "raw": raw, "expected": before.hex(), "original_target_rva": ORIGINAL_RVAS[name]})
    return result


def split_binary_resource(bundle):
    if len(bundle) < 24 or bundle[:4] != b"qres": raise ValueError("Invalid compiled Qt resource")
    version, tree, data, names = struct.unpack_from(">4I", bundle, 4)
    if version != 3 or len({tree, data, names}) != 3 or min(tree, data, names) < 24 or max(tree, data, names) >= len(bundle):
        raise ValueError("Expected RCC binary format version 3")
    return {"tree": tree, "data": data, "names": names, "version": version}


def prepare(binary, output, *, rcc=DEFAULT_RCC, assets_directory=None, extra_resources=None):
    """Return bundle bytes, section offsets, and six guarded LEA anchors.

    Caller appends the bundle to its existing read-only UI section, then patches
    both register and unregister RIP displacements to bundleRva+sectionOffset.
    Native resource registration calls/ABI/lifetimes remain stock.
    """
    originals = native_payloads(binary); anchors = registration_anchors(binary)
    additions = resource_payloads(assets_directory)
    extras = {} if extra_resources is None else extra_resources
    if type(extras) is not dict or set(extras) - {":/valmax/ChatExportPanel.qml", ":/valmax/MessageRetentionController.qml", ":/valmax/MessageRetentionArchive.qml", ":/valmax/MessageRetentionInline.qml", ":/valmax/MessageRetentionRange.qml", ":/valmax/MessageRetentionBubble.qml", ":/valmax/ChatLockController.qml", ":/valmax/ValMAXAsyncListRow.qml", ":/valmax/ValMAXListWorkQueue.qml", ":/valmax/ValMAXWheelAdmission.qml", ":/valmax/optimization/OptimizationConfig.qml", ":/valmax/optimization/qmldir"}:
        raise ValueError("Unsupported extra ValMAX resource")
    for path, payload in extras.items():
        if type(payload) is not bytes or not 1 <= len(payload) <= 1024 * 1024:
            raise ValueError("Invalid bounded ValMAX source resource")
        payload.decode("utf8")
    additions.update(extras)
    if set(originals) & set(additions): raise ValueError("New icon URL collision")
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()): raise ValueError("Icon RCC output must be a fresh empty build directory")
    resources = {**originals, **additions}; qrc_root = ET.Element("RCC", {"version": "1.0"}); group = ET.SubElement(qrc_root, "qresource", {"prefix": "/"})
    for path, payload in sorted(resources.items()):
        relative = path[2:]; target = output / "inputs" / relative
        target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(payload)
        # QQmlEngine validates standalone qrc disk cache by URL + resource date.
        # Icons keep their reproducible date; changed QML must get a new date.
        timestamp_ms = (1767225600000 + int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") % 31536000000
                        if path in extras else FIXED_MTIME * 1000)
        os.utime(target, ns=(timestamp_ms * 1000000, timestamp_ms * 1000000))
        element = ET.SubElement(group, "file", {"alias": relative}); element.text = target.relative_to(output).as_posix()
    qrc = output / "icon-pack-plus-valmax.qrc"; ET.indent(qrc_root, space="  "); ET.ElementTree(qrc_root).write(qrc, encoding="utf-8", xml_declaration=True)
    target = output / "icon-pack-plus-valmax.rcc"; rcc = Path(rcc).resolve(strict=True)
    # rcc replaces every input date with SOURCE_DATE_EPOCH when it is set.
    # All input dates above are already deterministic, including per-QML dates.
    env = dict(os.environ); env.pop("SOURCE_DATE_EPOCH", None)
    command = [str(rcc), "--binary", "--format-version", "3", "--no-zstd", "--compress-algo", "zlib", "--compress", "9", "--threshold", "0", "-o", str(target), str(qrc)]
    # Qt's Windows GUI-subsystem tools show QMessageBox for --help/errors.
    # Only compile a valid generated QRC, never invoke help/version/no-args.
    process = subprocess.run(command, cwd=output, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=30)
    if process.returncode or not target.is_file(): raise ValueError("Official rcc compilation failed")
    bundle = target.read_bytes(); sections = split_binary_resource(bundle)
    evidence = {"schema": 1, "baseline_sha256": BASELINE_SHA256, "resource_count": len(resources), "original_count": len(originals),
                "added_count": len(additions), "added_icon_count": 7, "added_source_count": len(extras), "bundle_sha256": sha(bundle), "bundle_bytes": len(bundle), "section_offsets": sections,
                "anchors": anchors, "compiler_sha256": sha(rcc.read_bytes()), "compiler_stdout_bytes": len(process.stdout), "compiler_stderr_bytes": len(process.stderr),
                "payloads": {path: sha(payload) for path, payload in sorted(resources.items())},
                "notes": ["546 original icon contents are preserved byte-for-byte after decompression; ValMAX icons and bounded standalone QML sources are added.",
                          "Patch six 7-byte LEA instructions (register and unregister) with guarded RIP displacements; no code shim or ABI changes.",
                          "This helper does not write or patch max.exe. The shared builder owns PE-section sizes, append/reversal and integration."]}
    (output / "icon-resource-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    return bundle, evidence


def verify_in_python(bundle_file, expected):
    """Read every path through Qt's real RCC decoder in this QA process only."""
    from PySide6.QtCore import QFile, QIODevice, QResource
    path = str(Path(bundle_file).resolve(strict=True))
    if not QResource.registerResource(path): raise ValueError("Qt rejected compiled icon RCC")
    try:
        for name, digest in expected.items():
            file = QFile(name)
            if not file.open(QIODevice.OpenModeFlag.ReadOnly): raise ValueError("Qt cannot resolve merged icon resource")
            value = bytes(file.readAll()); file.close()
            # QFile's resource engine retains a reference even after close.
            # Release it before verifying the unregister lifecycle.
            del file
            if sha(value) != digest: raise ValueError("Merged Qt resource content changed")
    finally:
        if not QResource.unregisterResource(path): raise ValueError("Qt icon resource unregister failed")
    return {"verified_resources": len(expected), "all_payload_hashes_match": True, "unregistered": True, "native_client_accessed": False}
