"""Prepare only immutable target PE bytes for existing native guard metadata.

Build-time tooling. Reuses the assembler's exact reviewed static patch and QML
preparation; does not compile/load guards, execute MAX or use user profiles.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

import pefile

import assemble_native_runtime as assembly

ROOT = assembly.ROOT
REQUIRED = {"max.exe", "core.dll", "notifications.dll"}


def owned_output(path):
    assembly.require(path.is_absolute()
                     and all(part not in (".", "..") and not part.endswith((".", " ")) for part in path.parts[1:])
                     and any(path != root and path.is_relative_to(root) for root in assembly.BUILD_ROOTS),
                     "new owned C/D/E build output required")
    assembly.ancestors(path)
    assembly.require(not path.exists(), "new owned C/D/E build output required")
    return path


def selected_targets(specifications):
    assembly.require(isinstance(specifications, list) and specifications,
                     "nonempty literal privacy specifications required")
    names = set(REQUIRED)
    aliases = {name.casefold(): name for name in REQUIRED}
    seen = set()
    for item in specifications:
        assembly.require(isinstance(item, dict), "invalid privacy specification")
        name = assembly.relative(item["relative_path"]).as_posix()
        assembly.require(name.casefold() not in seen and Path(name).suffix.lower() in {".exe", ".dll"},
                         "duplicate/non-PE privacy target")
        assembly.require(name.casefold() not in aliases or aliases[name.casefold()] == name,
                         "case-colliding required target")
        seen.add(name.casefold())
        aliases[name.casefold()] = name
        names.add(name)
    return sorted(names)


def prepare(args):
    output, report = args.output, args.report
    owned_output(output)
    assembly.ancestors(report)
    assembly.require(report.is_absolute() and report.parent.resolve() == assembly.REPORTS.resolve()
                     and not report.exists(), "new analysis report required")
    source_paths = [ROOT / "native/tooling/prepare_guard_targets.py",
                    ROOT / "native/tooling/assemble_native_runtime.py"]
    source_paths += [ROOT / "modkit" / (name + ".py")
                     for name in ("accounts", "privacy", "isolation", "build_mod")]
    source_hashes = {path.relative_to(ROOT).as_posix(): assembly.sha(path) for path in source_paths}
    layout_hash = assembly.sha(args.layout_proof)
    layout = assembly.read_json(args.layout_proof)
    assembly.require(layout.get("applications_executed") is False
                     and Path(layout["output"]).resolve() == args.baseline.resolve()
                     and layout["file_count"] == len(layout["layout"]), "frozen baseline proof required")
    baseline_rows = assembly.verify_inventory(args.baseline, layout["layout"])
    baseline_map = {row["path"]: row for row in baseline_rows}
    native_qml, unresolved = assembly.generated_sources(args.qml)
    assembly.require(not unresolved, "guard target preparation requires completed native QML transport migration")
    qml_proof = args.qml / "native-qml-proof.json"
    qml_proof_hash = assembly.sha(qml_proof)
    privacy = assembly.literal_specs(ROOT / "modkit/privacy.py")
    names = selected_targets(privacy)
    assembly.require(set(names) <= set(baseline_map), "target missing from pinned baseline")
    prepared = {name: (data, row) for name, data, row in assembly.patched_files(
        args.baseline, assembly.literal_specs(args.qml / "ui_native.py"))}
    selected = []
    for name in names:
        source = args.baseline / assembly.relative(name)
        baseline_row = baseline_map[name]
        if name in prepared:
            data, patch = prepared[name]
            assembly.require(patch["baselineSha256"] == baseline_row["sha256"]
                             and patch["reverseVerified"] is True, "reviewed reversible target patch required")
        else:
            data = source.read_bytes()
            patch = {"baselineSha256": baseline_row["sha256"], "reverseVerified": True, "patchIds": []}
            assembly.require(hashlib.sha256(data).hexdigest() == baseline_row["sha256"], "unpatched baseline target changed")
        pe = pefile.PE(data=data, fast_load=True)
        try:
            assembly.require(pe.FILE_HEADER.Machine == 0x8664 and pe.OPTIONAL_HEADER.Magic == 0x20b,
                             "Windows x64 target PE required")
            row = {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                   "baselineSha256": patch["baselineSha256"], "reverseVerified": True,
                   "patchIds": patch["patchIds"], "imageSize": pe.OPTIONAL_HEADER.SizeOfImage}
        finally:
            pe.close()
        selected.append((name, data, row))
    total = sum(len(data) for _, data, _ in selected)
    assembly.require(shutil.disk_usage(output.anchor).free > total + (256 << 20), "insufficient output-drive space for target-only copy")
    runtime = output / "runtime"
    runtime.mkdir(parents=True)
    for name, data, row in selected:
        target = runtime / assembly.relative(name)
        assembly.write_new(target, data)
        assembly.require(assembly.ordinary(target).st_nlink == 1
                         and assembly.sha(target) == row["sha256"], "target output changed/not independent")
    rows = [row for _, _, row in selected]
    output_rows = assembly.inventory(runtime)
    assembly.require(output_rows == [{key: row[key] for key in ("path", "bytes", "sha256")} for row in rows],
                     "target output inventory differs")
    assembly.require(assembly.verify_inventory(args.baseline, baseline_rows) == baseline_rows,
                     "baseline changed during target preparation")
    assembly.require(assembly.generated_sources(args.qml) == (native_qml, unresolved)
                     and assembly.sha(qml_proof) == qml_proof_hash, "native QML inputs changed")
    assembly.require(all(assembly.sha(ROOT / name) == value for name, value in source_hashes.items())
                     and assembly.sha(args.layout_proof) == layout_hash, "source/receipt changed")
    result = {"schema": 1, "prepared": True, "runtimeRoot": str(runtime),
        "files": len(rows), "bytes": total, "targets": rows,
        "runtimeInventorySha256": hashlib.sha256(assembly.canonical(output_rows)).hexdigest(),
        "sourceHashes": source_hashes, "sourcesUnchanged": True,
        "baselineLayout": {"path": str(args.layout_proof), "sha256": layout_hash},
        "nativeQmlProof": {"path": str(qml_proof), "sha256": qml_proof_hash},
        "baselineUnchanged": True, "nativeQmlUnchanged": True,
        "independentCopies": True, "allPatchedTargetsReverseVerified": True,
        "targetOnly": True, "guardMetadataGenerated": False, "anythingCompiled": False,
        "targetsExecuted": False, "privateProfilesRead": False, "installedClientChanged": False,
        "releaseEligible": False,
        "scope": "Only reviewed baseline-derived target PE identities for existing guard generation. No callbacks, capture, EOF, bootstrap, DLL execution or before-first-use guarantee."}
    encoded = json.dumps(result, indent=2).encode("utf-8") + b"\n"
    assembly.write_new(output / "target-manifest.json", encoded)
    assembly.write_new(report, encoded)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=assembly.BASELINE)
    parser.add_argument("--layout-proof", type=Path, default=assembly.LAYOUT)
    parser.add_argument("--qml", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    result = prepare(parser.parse_args())
    print(json.dumps({key: result[key] for key in ("prepared", "runtimeRoot", "files", "bytes", "runtimeInventorySha256")}))


if __name__ == "__main__":
    main()
