"""Fail closed if a candidate runtime still needs or bundles Qt Lottie.

This is a distribution gate for a future native build. It does not claim that
microphone or camera capture works in the installed application.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import zlib

import pefile

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT / "modkit"))
from recording_indicator_ui import RESOURCE_PATHS, MARKER  # noqa: E402
from ui_resource_index import index_resources  # noqa: E402

IMPORT = re.compile(r"(?m)^\s*import\s+Qt\.labs\.lottieqt(?:\s|$)")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def decode_qml(binary: bytes, pe: pefile.PE, record: dict) -> str:
    offset = pe.get_offset_from_rva(record["payload_rva"] - 4)
    size = int.from_bytes(binary[offset:offset + 4], "big")
    if not 0 < size <= 8 * 1024 * 1024:
        raise RuntimeError("invalid QML resource size")
    payload = binary[offset + 4:offset + 4 + size]
    if record["flags"] & 1:
        expected = int.from_bytes(payload[:4], "big")
        decompressor = zlib.decompressobj()
        payload = decompressor.decompress(payload[4:], 8 * 1024 * 1024)
        if (len(payload) != expected or not decompressor.eof
                or decompressor.unused_data or decompressor.unconsumed_tail):
            raise RuntimeError("invalid compressed QML resource")
    elif record["flags"] & 4:
        import zstandard
        payload = zstandard.ZstdDecompressor().decompress(payload, max_output_size=8 * 1024 * 1024)
    return payload.decode("utf8")


def pe_inventory_from_assembly(root: Path, proof: Path) -> dict[str, str]:
    record = json.loads(proof.read_text(encoding="utf8"))
    if (record.get("schema") != 1 or record.get("assembled") is not True
            or Path(record.get("runtime", "")).resolve(strict=True) != root.parent):
        raise RuntimeError("wrong assembly inventory for candidate runtime")
    rows = record.get("inventory")
    if not isinstance(rows, list):
        raise RuntimeError("assembly inventory missing")
    expected: dict[str, str] = {}
    for row in rows:
        name = row.get("path") if isinstance(row, dict) else None
        if not isinstance(name, str) or not name.startswith("mod/"):
            continue
        short = name[4:]
        if Path(short).suffix.lower() not in {".exe", ".dll"}:
            continue
        if short in expected:
            raise RuntimeError("duplicate assembly PE path")
        expected[short] = row.get("sha256")
    return expected


def verify_runtime(root: Path, *, synthetic_fixture: bool = False,
                   expected_pe_inventory: dict[str, str] | None = None) -> dict:
    """Verify packed QML and the exact full p15 mod PE inventory."""
    root = root.resolve(strict=True)
    binary_path = root / "max.exe"
    if not root.is_dir() or not binary_path.is_file():
        raise RuntimeError("candidate native runtime/mod is missing")
    qt_lottie_files = [path.relative_to(root).as_posix() for path in root.rglob("*")
                       if path.is_file() and (path.name.lower().startswith("qt6lottie")
                                              or path.name.lower() in {"qlottievectorimage.dll", "lottieplugin.dll"})]
    if qt_lottie_files or (root / "qml/Qt/labs/lottieqt").exists():
        raise RuntimeError("Qt Lottie module binaries remain in candidate runtime")

    if not synthetic_fixture:
        if (not isinstance(expected_pe_inventory, dict) or len(expected_pe_inventory) != 318
                or any(not isinstance(name, str) or PurePosixPath(name).as_posix() != name
                       or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
                       or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                       for name, digest in expected_pe_inventory.items())):
            raise RuntimeError("exact p15 mod PE inventory required")
    imports = []
    actual_pe_inventory: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.suffix.lower() not in {".exe", ".dll"}:
            continue
        if os.lstat(path).st_file_attributes & 0x400:
            raise RuntimeError("reparse PE in candidate runtime")
        relative = path.relative_to(root).as_posix()
        if relative in actual_pe_inventory:
            raise RuntimeError("duplicate candidate PE path")
        actual_pe_inventory[relative] = sha256_file(path)
        pe = pefile.PE(str(path), fast_load=True)
        try:
            pe.parse_data_directories(directories=[
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"],
            ])
            for kind in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
                imports.extend((path.relative_to(root).as_posix(), item.dll.decode("ascii", "replace"))
                               for item in getattr(pe, kind, []) if b"lottie" in item.dll.lower())
        finally:
            pe.close()
    if imports:
        raise RuntimeError("Qt Lottie PE imports remain in candidate runtime")
    if not synthetic_fixture and actual_pe_inventory != expected_pe_inventory:
        raise RuntimeError("candidate runtime PE path/hash inventory differs from pinned plan")
    scanned_pe = len(actual_pe_inventory)
    inventory_sha = hashlib.sha256(json.dumps(
        sorted(actual_pe_inventory.items()), separators=(",", ":")
    ).encode("utf8")).hexdigest()

    data = binary_path.read_bytes()
    pe = pefile.PE(data=data, fast_load=True)
    qml_count = 0
    selected: dict[str, str] = {}
    for record in index_resources(binary_path)["files"]:
        if not record["path"].endswith((".qml", ".js", "/qmldir")):
            continue
        text = decode_qml(data, pe, record)
        qml_count += 1
        if IMPORT.search(text):
            raise RuntimeError("Qt.labs.lottieqt import remains in packed QML")
        if record["path"] in RESOURCE_PATHS:
            if (text.count(MARKER) != 1 or "LottieAnimation" in text
                    or "qrc:/animations/recording.json" in text):
                raise RuntimeError("recording panel has no Qt Quick pulse replacement")
            selected[record["path"]] = hashlib.sha256(text.encode("utf8")).hexdigest()
    pe.close()
    if set(selected) != set(RESOURCE_PATHS) or qml_count < 1000:
        raise RuntimeError("packed recorder panel or QML inventory is incomplete")
    result = {"schema": 1, "verified": True, "runtime": str(root),
              "scannerSha256": sha256_file(Path(__file__)),
              "maxExeSha256": hashlib.sha256(data).hexdigest(),
              "qmlDecoded": qml_count, "recordingResources": selected,
              "peScanned": scanned_pe, "peInventorySha256": inventory_sha,
              "exactPeInventoryMatched": not synthetic_fixture,
              "lottieFiles": 0, "lottieImports": 0,
              "syntheticOnly": synthetic_fixture,
              "installedRecordingSmoke": False, "releaseEligible": False}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True, help="Candidate runtime/mod directory")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--inventory", type=Path,
                        help="Exact assembled runtime inventory; required outside synthetic fixture")
    parser.add_argument("--synthetic-fixture", action="store_true",
                        help="Allow an isolated max.exe-only positive control; never counts as full runtime evidence")
    args = parser.parse_args()
    if args.report and (args.report.exists() or not args.report.parent.is_dir()):
        raise RuntimeError("fresh report parent required")
    if bool(args.inventory) == bool(args.synthetic_fixture):
        raise RuntimeError("full runtime requires exactly one assembly inventory")
    expected = pe_inventory_from_assembly(args.runtime.resolve(strict=True), args.inventory) if args.inventory else None
    result = verify_runtime(args.runtime, synthetic_fixture=args.synthetic_fixture,
                            expected_pe_inventory=expected)
    if args.report:
        with args.report.open("x", encoding="utf8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
