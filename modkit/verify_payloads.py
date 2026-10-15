"""Verify every original MSI payload and every file in a reconstructed/mod copy."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--mod", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    layout = json.loads(args.layout.read_text(encoding="utf-8"))
    patched = {}
    if args.mod:
        manifest = json.loads((args.mod / "mod-manifest.json").read_text(encoding="utf-8"))
        patched = {item["relative_path"]: item["patched_sha256"] for item in manifest["files"]}
    failures = []
    counts = {"original_unchanged": 0, "baseline_exact": 0, "mod_expected": 0}
    for item in layout["layout"]:
        checks = [
            ("original_unchanged", args.source / item["source_file"], item["sha256"]),
            ("baseline_exact", args.baseline / item["path"], item["sha256"]),
        ]
        if args.mod:
            checks.append(("mod_expected", args.mod / item["path"], patched.get(item["path"], item["sha256"])))
        for group, path, expected in checks:
            if not path.is_file():
                failures.append({"group": group, "file": item["path"], "error": "missing"})
            elif digest(path) != expected:
                failures.append({"group": group, "file": item["path"], "error": "hash mismatch"})
            else:
                counts[group] += 1
    report = {"official_msi_sha256": layout["msi_sha256"], "payload_count": len(layout["layout"]), "counts": counts, "failures": failures}
    args.output.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
