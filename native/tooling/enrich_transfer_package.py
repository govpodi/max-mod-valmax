"""Create a NEW helper package with verified Qt/VC redistribution materials.

The original helper bundle, installed client and user profiles are untouched.
This is build tooling; no runtime or installer is executed here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import uuid

from package_transfer_helper import (
    ROOT, BUILD_ROOT, WORK_ROOTS, PACKAGE_ROOT, REPORT_ROOT, below, canonical, copy_new,
    files, ordinary, require, sha,
)


def relative_name(value):
    require(isinstance(value, str) and 0 < len(value) <= 1024 and "\\" not in value, "invalid relative name")
    path = PurePosixPath(value)
    require(not path.is_absolute() and path.as_posix() == value and all(
        part not in (".", "..") and not any(c in part for c in '<>:"|?*\0')
        and not part.endswith((".", " ")) and not re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
        for part in path.parts), "unsafe relative name")
    return value


def read_proof(path):
    path = below(path, REPORT_ROOT, "proof")
    require(ordinary(path).st_size <= 8 << 20, "proof bound")
    return json.loads(path.read_text("utf-8")), {"path": str(path), "sha256": sha(path)}


def validated_materials(proof, root_key, files_key, root_boundary, destination, source_hash):
    verified = proof.get("verified") is True
    if root_key == "output":
        # The frozen Qt collector records five explicit checks, not a generic
        # 'verified' flag. Consume that actual schema without editing its receipt.
        checks = ("officialArchiveChecksums", "packageSdkByteIdentity", "sourceCommitOrTreeIdentity",
                  "allSourceLicenseReferences", "sdkAndPackageUnchanged")
        verified = (proof.get("kind") == "valmax-qt-source-materials"
                    and all(proof.get("checks", {}).get(key) is True for key in checks)
                    and proof.get("materialsComplete") is True)
    require(verified and proof.get("sourcePackageManifestSha256") == source_hash,
            "unverified or unrelated materials")
    root = below(Path(proof[root_key]), root_boundary, "materials folder")
    records = proof[files_key]
    require(isinstance(records, list) and 0 < len(records) <= 4096, "materials count bound")
    result, seen, size = [], set(), 0
    for record in records:
        name = relative_name(record["path"])
        require(name.casefold() not in seen, "duplicate material")
        seen.add(name.casefold())
        source = below(root / name, root, "material")
        info = ordinary(source)
        require(info.st_nlink == 1 and info.st_size == record["bytes"] and sha(source) == record["sha256"],
                "materials changed")
        size += info.st_size
        require(size <= 256 << 20, "materials size bound")
        require(source.suffix.lower() not in (".exe", ".dll", ".pyd", ".py", ".pyc", ".mdata", ".dmp"),
                "unexpected runtime/private material")
        result.append((source, {"path": destination + name, "bytes": info.st_size, "sha256": record["sha256"]}))
    return result


def material_boundary(proof, key, category):
    value = Path(proof[key]).resolve()
    for root in WORK_ROOTS:
        boundary = (root / category).resolve()
        if value.is_relative_to(boundary):
            return boundary
    raise ValueError("materials outside owned work roots")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("source-package-proof", "qt-proof", "vc-proof", "report", "materials-report", "distribution-report"):
        parser.add_argument("--" + option, type=Path, required=True)
    parser.add_argument("--work-root", type=Path, choices=WORK_ROOTS, default=BUILD_ROOT)
    args = parser.parse_args()
    package_root = args.work_root / "packages"
    report = below(args.report, REPORT_ROOT, "new package report")
    materials_report = below(args.materials_report, REPORT_ROOT, "new materials report")
    distribution_report = below(args.distribution_report, REPORT_ROOT, "new distribution report")
    require(len({report, materials_report, distribution_report}) == 3 and
            not any(path.exists() for path in (report, materials_report, distribution_report)), "reports must be distinct and new")
    package, package_ref = read_proof(args.source_package_proof)
    qt, qt_ref = read_proof(args.qt_proof)
    vc, vc_ref = read_proof(args.vc_proof)
    source_root = below(Path(package["package"]), package_root, "original package")
    source_hash = package["manifestSha256"]
    require(sha(source_root / "package-manifest.json") == source_hash, "source manifest changed")
    manifest = json.loads((source_root / "package-manifest.json").read_text("utf-8"))
    records = {relative_name(row["path"]): row for row in manifest["files"]}
    require(len(records) == len(manifest["files"]), "duplicate original manifest record")
    expected = {**{name: row["sha256"] for name, row in records.items()}, "package-manifest.json": source_hash}
    require(package["verifiedBundle"] is True and expected == package["fileHashes"], "original package proof mismatch")
    actual = {path.relative_to(source_root).as_posix(): sha(path) for path in files(source_root)}
    require(actual == expected, "original package inventory changed")
    material_files = validated_materials(qt, "output", "materialsFiles", material_boundary(qt, "output", "deps"),
                                         "licenses/Qt/source-materials/", source_hash)
    material_files += validated_materials(vc, "materials", "files", material_boundary(vc, "materials", "package-notices"),
                                          "licenses/Microsoft/", source_hash)
    gaps = qt["gaps"] + vc["gaps"]
    require(isinstance(gaps, list), "invalid materials gaps")
    # Preserve unresolved evidence as a staging package; never silently clear it.
    ready = not gaps
    stage = package_root / ("valmax-transfer-materials-" + uuid.uuid4().hex)
    below(stage, package_root, "new package stage")
    stage.mkdir()
    for name in records:
        if name != "THIRD-PARTY-NOTICES.txt":
            copy_new(source_root / name, stage / name)
    for source, record in material_files:
        require(record["path"].casefold() not in {name.casefold() for name in records}, "material collides with runtime")
        copy_new(source, stage / record["path"])
        records[record["path"]] = {**record, "origin": {"component": "notice", "source": record["path"]}}
    notice = (
        "ValMAX native MData transfer helper\n\n"
        "Original ValMAX code: MIT. See licenses/ValMAX-MIT.txt.\n"
        "Rust dependency notices: licenses/Rust.\n\n"
        "Qt 6.11.2 shared libraries: QtBase and QtDeclarative.\n"
        "Copyright The Qt Company Ltd. and other contributors.\n"
        "Applicable Qt license texts, third-party notices and complete original source archives\n"
        "are supplied in licenses/Qt/source-materials. The receipt records upstream versions,\n"
        "source trees and binary SDK provenance. The source archives are not required at runtime.\n"
        "QtShaderTools SPDX retained from the original SDK inventory does not mean its DLL is supplied.\n"
        "These are dynamically linked Qt libraries. You may replace/rebuild them under their\n"
        "applicable license terms; this helper imposes no runtime hash lock on those libraries.\n\n"
        "Microsoft VC143 release runtime DLLs are unmodified files from the official redistribution\n"
        "directory. See licenses/Microsoft for original terms, redistribution list and origin records.\n"
        "Windows system DLLs/API-set contracts are not bundled.\n\n"
        "No Python runtime, MAX executable, user profiles, passwords or MData archives are included.\n"
        "Materials describe this standalone helper; they do not approve a complete ValMAX release.\n"
    ).encode("utf-8")
    (stage / "THIRD-PARTY-NOTICES.txt").write_bytes(notice)
    records["THIRD-PARTY-NOTICES.txt"] = {
        "path": "THIRD-PARTY-NOTICES.txt", "bytes": len(notice), "sha256": sha(stage / "THIRD-PARTY-NOTICES.txt"),
        "origin": {"component": "generated", "source": "enrich_transfer_package.py"},
    }
    for name, record in records.items():
        require((stage / name).stat().st_size == record["bytes"] and sha(stage / name) == record["sha256"], "copy mismatch")
    require(len(files(stage)) == len(records), "unexpected staged file")
    manifest["files"] = [records[name] for name in sorted(records)]
    manifest["sourcePackageManifestSha256"] = source_hash
    manifest["distributionBlockers"] = gaps
    manifest["redistributionMaterialsComplete"] = ready
    with (stage / "package-manifest.json").open("xb") as stream:
        stream.write(canonical(manifest) + b"\n")
    new_hash = sha(stage / "package-manifest.json")
    final = below(package_root / ("valmax-transfer-" + new_hash), package_root, "new package")
    require(not final.exists(), "final package already exists")
    # Both absolute targets are checked children of the owned package root.
    stage.rename(final)
    for ref in (package_ref, qt_ref, vc_ref):
        require(sha(ref["path"]) == ref["sha256"], "input receipt changed")
    packaged = [row for _, row in material_files]
    materials = {"schema": 1, "kind": "valmax-transfer-materials", "sourcePackageManifestSha256": source_hash,
                 "components": ["qt", "msvc"], "gaps": gaps, "packagedMaterials": packaged,
                 "componentProofs": {"qt": qt_ref, "msvc": vc_ref}, "sourcePackageProof": package_ref,
                 "scope": "Completeness and identities of collected materials; not a legal eligibility attestation."}
    with materials_report.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(materials, ensure_ascii=False, indent=2) + "\n")
    distribution = {"schema": 1, "kind": "valmax-transfer-distribution", "distributionReady": ready,
                    "sourcePackageManifestSha256": source_hash, "packageManifestSha256": new_hash,
                    "materialsProof": {"path": str(materials_report), "sha256": sha(materials_report)},
                    "packagedMaterials": packaged, "gaps": gaps,
                    "scope": "Material-complete standalone helper package; fresh runtime verification is still required before installer staging."}
    with distribution_report.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(distribution, ensure_ascii=False, indent=2) + "\n")
    result = {**package, "package": str(final), "manifestSha256": new_hash,
              "fileHashes": {**{name: row["sha256"] for name, row in records.items()}, "package-manifest.json": new_hash},
              "files": len(records), "bytes": sum(row["bytes"] for row in records.values()),
              "distributionReady": ready, "distributable": ready, "distributionBlockers": gaps,
              "sourcePackageProof": package_ref, "materialsProof": {"path": str(materials_report), "sha256": sha(materials_report)},
              "runtimeExecuted": False,
              "inputHashes": {**package["inputHashes"], str(Path(__file__).resolve()): sha(__file__),
                              **{ref["path"]: ref["sha256"] for ref in (package_ref, qt_ref, vc_ref)}}}
    with report.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"package": str(final), "report": str(report), "files": len(records), "materialFiles": len(packaged),
                      "distributionReady": ready}))


if __name__ == "__main__":
    main()
