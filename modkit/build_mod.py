"""Build and verify a MAX mod in a new directory; never patch the source tree."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_relative(value: str) -> Path:
    # Manifest paths use POSIX separators even on Windows. PurePosixPath alone
    # would treat '..\\outside' or a UNC path as an innocent single component,
    # while constructing a native Path would reinterpret the backslashes.
    if not isinstance(value, str) or "\\" in value:
        raise ValueError(f"Unsafe payload path: {value}")
    path = PurePosixPath(value)
    if (path.is_absolute() or not path.parts or path.as_posix() != value
            or any(part in ("..", ".") or ":" in part or part.endswith((".", " ")) for part in path.parts)):
        raise ValueError(f"Unsafe payload path: {value}")
    return Path(*path.parts)


def payload_file(root: Path, value: str) -> Path:
    """Resolve reparse points before reading or replacing a payload file."""
    root = root.resolve(strict=True)
    target = (root / safe_relative(value)).resolve(strict=True)
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError(f"Payload file escapes its application directory or is not a file: {value}")
    return target


def atomic_replace_bytes(target: Path, data: bytes, suffix: str) -> None:
    """Write through an exclusively created temporary in the target directory."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=f".{target.name}.", suffix=suffix,
                                         dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def patch_bytes(data: bytes, entries: list[dict], reverse: bool = False) -> bytes:
    # A resource section can be appended without moving any existing PE data.
    # Allow one exact pinned tail only; ordinary patches may never address it.
    tails = [entry for entry in entries if entry.get("kind", "replace") == "append"]
    if len(tails) > 1 or any(entry.get("kind", "replace") not in ("replace", "append") for entry in entries):
        raise ValueError("Invalid patch kind or multiple appended tails")
    tail = bytes.fromhex(tails[0]["replacement"]) if tails else b""
    original_length = len(data) - len(tail) if reverse else len(data)
    if tails:
        entry = tails[0]
        if (entry["expected"] != "" or not tail or type(entry["offset"]) is not int
                or original_length < 0 or entry["offset"] != original_length):
            raise ValueError("Invalid appended-tail bounds")
        if reverse and data[original_length:] != tail:
            raise ValueError("Unexpected bytes in appended tail")
    result = bytearray(data[:original_length])
    occupied = set()
    for entry in entries:
        if entry.get("kind", "replace") == "append":
            continue
        expected = bytes.fromhex(entry["replacement" if reverse else "expected"])
        replacement = bytes.fromhex(entry["expected" if reverse else "replacement"])
        offset = entry["offset"]
        if (type(offset) is not int or not expected or len(expected) != len(replacement)
                or offset < 0 or offset + len(expected) > original_length):
            raise ValueError(f"Invalid patch bounds: {entry['id']}")
        span = set(range(offset, offset + len(expected)))
        if occupied.intersection(span):
            raise ValueError(f"Overlapping patches: {entry['id']}")
        occupied.update(span)
        if data[offset:offset+len(expected)] != expected:
            raise ValueError(f"Unexpected bytes at {offset:#x}: {entry['id']}")
        result[offset:offset+len(expected)] = replacement
    if tails and not reverse:
        result.extend(tail)
    return bytes(result)


def specifications(components: list[str]) -> list[dict]:
    merged = {}
    for component in components:
        if component not in ("accounts", "privacy", "isolation", "ui_privacy"):
            raise ValueError(f"Unknown component: {component}")
        module = importlib.import_module(component)
        for spec in module.PATCH_FILES:
            path = spec["relative_path"]
            key = os.path.normcase(str(safe_relative(path)))
            if key in merged and merged[key]["sha256"] != spec["sha256"]:
                raise ValueError(f"Conflicting source hashes: {path}")
            item = merged.setdefault(key, {"relative_path":path,"sha256":spec["sha256"],"patches":[]})
            item["patches"].extend(spec["patches"])
    return list(merged.values())


def prepare(baseline: Path, specs: list[dict]) -> list[tuple[dict, bytes]]:
    prepared = []
    seen = set()
    for spec in specs:
        target = payload_file(baseline, spec["relative_path"])
        if target in seen:
            raise ValueError(f"Duplicate payload file: {spec['relative_path']}")
        seen.add(target)
        data = target.read_bytes()
        if sha(data) != spec["sha256"]:
            raise ValueError(f"Unsupported/modified source binary: {spec['relative_path']}")
        result = patch_bytes(data, spec["patches"])
        if patch_bytes(result, spec["patches"], reverse=True) != data:
            raise ValueError("Patch failed reverse validation")
        prepared.append((dict(spec, patched_sha256=sha(result)), result))
    return prepared


def build(baseline: Path, destination: Path, components: list[str]) -> dict:
    baseline = baseline.resolve(strict=True)
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("Destination must be new; existing application/data directories are never replaced")
    if destination.is_relative_to(baseline) or baseline.is_relative_to(destination):
        raise ValueError("Source and destination directories must be separate")
    prepared = prepare(baseline, specifications(components))
    shutil.copytree(baseline, destination)
    for spec, data in prepared:
        target = payload_file(destination, spec["relative_path"])
        atomic_replace_bytes(target, data, ".modkit-tmp")
    manifest = {
        "format":1,"max_version":"26.35.1.80662","baseline":str(baseline),
        "destination":str(destination),"components":components,
        "signature_note":"Modified binaries no longer match their original Authenticode signatures.",
        "files":[spec for spec, _ in prepared],
    }
    manifest_path = destination / "mod-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    verify(manifest_path)
    return manifest


def verify(manifest_path: Path) -> dict:
    manifest_path = manifest_path.resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = manifest_path.parent
    seen = set()
    for record in manifest["files"]:
        target = payload_file(root, record["relative_path"])
        if target in seen:
            raise ValueError(f"Duplicate payload file: {record['relative_path']}")
        seen.add(target)
        data = target.read_bytes()
        if sha(data) != record["patched_sha256"]:
            raise ValueError(f"Modified build does not match manifest: {record['relative_path']}")
        restored = patch_bytes(data, record["patches"], reverse=True)
        if sha(restored) != record["sha256"]:
            raise ValueError(f"Cannot reproduce original: {record['relative_path']}")
    return manifest


def restore(manifest_path: Path) -> dict:
    manifest = verify(manifest_path)
    root = manifest_path.resolve().parent
    # Retain all validated restored bytes before the first mutation. Checking
    # the current complete hash again also rejects changes since verify().
    prepared = []
    for record in manifest["files"]:
        target = payload_file(root, record["relative_path"])
        data = target.read_bytes()
        if sha(data) != record["patched_sha256"]:
            raise ValueError(f"Modified build changed before restore: {record['relative_path']}")
        restored = patch_bytes(data, record["patches"], reverse=True)
        if sha(restored) != record["sha256"]:
            raise ValueError(f"Cannot reproduce original: {record['relative_path']}")
        prepared.append((target, restored))
    # Data directories are untouched; each validated binary is replaced atomically.
    for target, restored in prepared:
        atomic_replace_bytes(target, restored, ".restore-tmp")
    manifest["restored"] = True
    (root / "restore-receipt.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    builder = actions.add_parser("build")
    builder.add_argument("--baseline", type=Path, required=True)
    builder.add_argument("--output", type=Path, required=True)
    builder.add_argument("--components", nargs="+", choices=("accounts","privacy","isolation","ui_privacy"), default=["accounts","privacy","isolation"])
    for name in ("verify","restore"):
        actions.add_parser(name).add_argument("manifest", type=Path)
    args = parser.parse_args()
    if args.action == "build":
        manifest = build(args.baseline,args.output,args.components)
    elif args.action == "verify":
        manifest = verify(args.manifest)
    else:
        manifest = restore(args.manifest)
    print(json.dumps({"action":args.action,"destination":manifest["destination"],"files":len(manifest["files"]),"patches":sum(len(f["patches"]) for f in manifest["files"])}))


if __name__ == "__main__":
    main()
