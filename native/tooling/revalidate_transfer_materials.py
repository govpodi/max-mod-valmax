"""Revalidate cached Qt/VC materials against a new byte-identical runtime set.

Offline build tooling. Original receipts/materials remain immutable; fresh receipts
record the new package binding and the exact earlier evidence being reused.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

import collect_qt_notices as qt_collect
import collect_vc_notices as vc_collect
from enrich_transfer_package import material_boundary, read_proof, relative_name, validated_materials
from package_transfer_helper import ROOT, REPORT_ROOT, WORK_ROOTS, below, files, ordinary, require, sha


def write_new(path, value):
    with path.open("x", encoding="utf8") as output:
        output.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def package_inputs(proof_path):
    proof, ref = read_proof(proof_path)
    root = Path(proof["package"])
    require(any(root.resolve().is_relative_to((work / "packages").resolve()) for work in WORK_ROOTS),
            "package outside owned roots")
    root = below(root, root.parent, "package")
    ordinary(root, True)
    manifest_hash = proof.get("manifestSha256", proof.get("packageManifestSha256"))
    manifest_path = root / "package-manifest.json"
    require(sha(manifest_path) == manifest_hash, "package manifest changed")
    manifest = json.loads(manifest_path.read_text("utf8"))
    require(manifest["qtVersion"] == qt_collect.VERSION, "unexpected Qt version")
    records = {relative_name(row["path"]): row for row in manifest["files"]}
    require(len(records) == len(manifest["files"]), "duplicate package entries")
    expected = {**{name: row["sha256"] for name, row in records.items()}, "package-manifest.json": manifest_hash}
    require({path.relative_to(root).as_posix(): sha(path) for path in files(root)} == expected,
            "package inventory changed")
    return proof, ref, root, manifest_hash, records


def component_rows(records, components):
    return {name: {"sha256": row["sha256"], "origin": row["origin"]}
            for name, row in records.items() if row["origin"]["component"] in components}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("package-proof", "cached-qt-proof", "cached-vc-proof", "qt-report", "vc-report"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    reports = [below(path, REPORT_ROOT, "new materials receipt") for path in (args.qt_report, args.vc_report)]
    require(reports[0] != reports[1] and not any(path.exists() for path in reports), "new distinct receipts required")
    new, new_ref, package, binding, records = package_inputs(args.package_proof)
    qt, qt_ref = read_proof(args.cached_qt_proof)
    vc, vc_ref = read_proof(args.cached_vc_proof)
    require(qt.get("kind") == "valmax-qt-source-materials" and qt.get("materialsComplete") is True
            and qt.get("gaps") == [] and all(qt["checks"].get(name) is True for name in
                ("officialArchiveChecksums", "packageSdkByteIdentity", "sourceCommitOrTreeIdentity",
                 "allSourceLicenseReferences", "sdkAndPackageUnchanged")), "incomplete cached Qt evidence")
    require(vc.get("kind") == "valmax-vc-materials" and vc.get("verified") is True and vc.get("gaps") == [],
            "incomplete cached VC evidence")
    require(qt["sourcePackageManifestSha256"] == vc["sourcePackageManifestSha256"],
            "cached materials refer to different packages")
    old, old_ref, _, old_hash, old_records = package_inputs(Path(vc["packageProof"]["path"]))
    require(old_ref["sha256"] == vc["packageProof"]["sha256"] and old_hash == vc["sourcePackageManifestSha256"],
            "cached package proof changed")
    old_qt_helper, old_qt_ref = read_proof(Path(qt["helperProof"]))
    require(old_qt_ref["sha256"] == qt["helperProofSha256"]
            and old_qt_helper["packageManifestSha256"] == old_hash, "cached Qt helper proof changed")
    qt_rows, vc_rows = component_rows(records, {"qt", "qt-bin"}), component_rows(records, {"vc-redist"})
    require(qt_rows and qt_rows == component_rows(old_records, {"qt", "qt-bin"}),
            "Qt runtime set differs; collect applicable materials afresh")
    require(vc_rows and vc_rows == component_rows(old_records, {"vc-redist"}),
            "VC runtime set differs; collect applicable materials afresh")
    qt_root = below(Path(qt["output"]), material_boundary(qt, "output", "deps"), "cached Qt materials")
    vc_root = below(Path(vc["materials"]), material_boundary(vc, "materials", "package-notices"), "cached VC materials")
    # Full inventory/hash checks include archives, license references, original
    # provenance and the old materials manifest. Its old binding is preserved.
    validated_materials(qt, "output", "materialsFiles", qt_root.parent, "", old_hash)
    validated_materials(vc, "materials", "files", vc_root.parent, "", old_hash)
    _, loaded_hash, rows, modules, sboms, checksums = qt_collect.load_inputs(args.package_proof)
    require(loaded_hash == new_ref["sha256"] and rows == qt["shippedQtFiles"] and modules == qt["modules"],
            "new SDK mapping differs from cached materials coverage")
    require(len(qt["archives"]) == len(modules) == len(qt["moduleSources"]), "module source coverage differs")
    for archive, identity in zip(qt["archives"], qt["moduleSources"]):
        module = archive["module"]
        require(module in modules and identity["module"] == module, "module order mismatch")
        source = below(qt_root / relative_name(archive["path"]), qt_root, "source archive")
        ordinary(source)
        require(source.stat().st_size == archive["bytes"] and sha(source) == archive["sha256"]
                == archive["upstreamSha256"], "cached archive changed")
        checksum = source.with_name(source.name + ".sha256").read_bytes()
        match = re.fullmatch(rb"([a-f0-9]{64})\s+\*?" + re.escape(source.name.encode()) + rb"\s*", checksum)
        require(match is not None and match[1].decode() == archive["sha256"]
                and hashlib.sha256(checksum).hexdigest() == archive["upstreamChecksumSha256"],
                "cached official archive checksum differs")
        metadata = qt_root / relative_name(identity["metadataPath"])
        require(sha(metadata) == identity["metadataSha256"], "source identity metadata changed")
        actual = qt_collect.cgit_identity(metadata.read_bytes())
        require(actual == {"commit": identity["sdkCommit"], "tree": identity["upstreamTree"]},
                "source commit/tree differs")
        source_tag = (qt_root / "notices" / module / ".tag").read_text("ascii").strip()
        require(source_tag == identity["archiveTag"] and source_tag in actual.values(), "source archive tag mismatch")
        roots = [row["relatedSpdxElement"] for row in sboms[module]["relationships"] if row["relationshipType"] == "DESCRIBES"]
        packages = [row for row in sboms[module]["packages"] if row["SPDXID"] in roots]
        require(packages == [identity["sdkRootPackage"]], "SDK source package identity changed")
    attributions = json.loads((qt_root / "source-attributions.json").read_text("utf8"))
    require({row["module"] for row in attributions["modules"]} == set(modules), "attribution module coverage differs")
    references = 0
    for module in attributions["modules"]:
        for row in module["attributions"]:
            given = row["data"].get("LicenseFile", [])
            given = [given] if isinstance(given, str) else given
            resolved = [qt_collect.reference_path(row["source"], name) for name in given]
            require(resolved == row["resolvedLicenseFiles"], "attribution reference mapping differs")
            for name in resolved:
                ordinary(below(qt_root / "notices" / module["module"] / name, qt_root, "license reference"))
                references += 1
    ordinary(vc_collect.ARCHIVE)
    require(sha(vc_collect.ARCHIVE) == vc_collect.ARCHIVE_SHA, "pinned VC archive changed")
    runtime = []
    with zipfile.ZipFile(vc_collect.ARCHIVE) as archive:
        for name, row in vc_rows.items():
            require(Path(name).name == name, "unexpected VC path")
            member = vc_collect.CRT_PREFIX + name
            raw = archive.read(member)
            require(len(raw) <= 16 << 20 and hashlib.sha256(raw).hexdigest() == row["sha256"] == sha(package / name),
                    "VC package bytes differ from original archive")
            runtime.append({"path": name, "bytes": len(raw), "sha256": row["sha256"], "archiveMember": member})
    require(sorted(runtime, key=lambda row: row["path"]) == sorted(vc["runtimeFiles"], key=lambda row: row["path"]),
            "VC provenance coverage differs")
    require(vc_collect.license_text(vc_root / "Visual-C-Runtime-2015-2022-License.docx")
            == (vc_root / "Visual-C-Runtime-2015-2022-License.txt").read_text("utf8"), "VC license text differs")
    common = {"mode": "offline-byte-identical-runtime-revalidation", "packageProof": new_ref,
              "cachedPackageProof": old_ref, "cachedPackageManifestSha256": old_hash,
              "revalidator": {"path": str(Path(__file__).resolve()), "sha256": sha(__file__)},
              "originalMaterialsUnchanged": True, "networkUsed": False,
              "note": "Original materials manifests keep their original package bindings. This fresh receipt records independently rechecked applicability to the new package."}
    qt_new = {**qt, "sourcePackageManifestSha256": binding, "helperProof": new_ref["path"],
              "helperProofSha256": new_ref["sha256"], "shippedQtFiles": rows,
              "revalidation": {**common, "cachedMaterialsProof": qt_ref, "runtimeFilesChecked": len(qt_rows),
                               "resolvedLicenseReferencesChecked": references}}
    vc_new = {**vc, "sourcePackageManifestSha256": binding, "packageProof": new_ref,
              "runtimeFiles": runtime, "revalidation": {**common, "cachedMaterialsProof": vc_ref,
                                                         "runtimeFilesChecked": len(vc_rows)}}
    for ref in (new_ref, old_ref, old_qt_ref, qt_ref, vc_ref):
        require(sha(ref["path"]) == ref["sha256"], "input receipt changed during revalidation")
    # Recheck payload/SDK mappings after material verification, before writing.
    require(package_inputs(args.package_proof)[3] == binding and qt_collect.load_inputs(args.package_proof)[2:] == (rows, modules, sboms, checksums),
            "package or SDK changed during revalidation")
    write_new(reports[0], qt_new)
    write_new(reports[1], vc_new)
    print(json.dumps({"verified": True, "qtFiles": len(qt_rows), "vcFiles": len(vc_rows),
                      "licenseReferences": references, "reports": list(map(str, reports)), "networkUsed": False}))


if __name__ == "__main__":
    main()
