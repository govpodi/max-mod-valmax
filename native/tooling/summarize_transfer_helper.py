"""Verify and join the completed standalone helper evidence, without running it."""
from __future__ import annotations

import hashlib
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ANALYSIS = ROOT / "analysis/native-migration-v1"
REPORT = ANALYSIS / "native-transfer-helper-proof.json"
PREFIX = "qt-transfer-helper-v5-"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default=PREFIX)
    parser.add_argument("--build-proof", type=Path)
    parser.add_argument("--runtime-proof", type=Path)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--distribution-proof", type=Path)
    args = parser.parse_args()
    report_path = args.report.resolve()
    require(report_path.parent == ANALYSIS.resolve() and not report_path.exists(), "summary must be new under analysis")
    require(args.prefix and all(c.isalnum() or c in "-_" for c in args.prefix), "invalid proof prefix")
    paths = {key: ANALYSIS / (args.prefix + suffix + ".json") for key, suffix in
             (("build", "build"), ("package", "package"), ("runtime", "proof"), ("startup", "startup"))}
    if args.build_proof:
        require(args.build_proof.resolve().parent == ANALYSIS.resolve(), "build proof outside analysis")
        paths["build"] = args.build_proof
    if args.runtime_proof:
        require(args.runtime_proof.resolve().parent == ANALYSIS.resolve(), "runtime proof outside analysis")
        paths["runtime"] = args.runtime_proof
    reports = {key: json.loads(path.read_text("utf8")) for key, path in paths.items()}
    build, package, runtime, startup = (reports[key] for key in ("build", "package", "runtime", "startup"))
    require(build["compiled"] and package["verifiedBundle"] and runtime["verified"] and startup["verified"],
            "incomplete successful evidence")
    for report in (build, runtime, startup):
        for relative, digest in report["sourceHashes"].items():
            require(sha(ROOT / relative) == digest, "evidence source changed: " + relative)
    for relative, digest in package["appSourceHashes"].items():
        require(sha(ROOT / relative) == digest, "packaged source changed")
    for absolute, digest in build["artifacts"].items():
        require(sha(absolute) == digest, "build artifact changed")
    for absolute, digest in package["inputHashes"].items():
        require(sha(absolute) == digest, "packaging input changed")
    folder = Path(package["package"])
    for relative, digest in package["fileHashes"].items():
        require(sha(folder / relative) == digest, "package changed")
    require(runtime["buildProofSha256"] == sha(paths["build"]), "runtime/build mismatch")
    require(runtime["packageProofSha256"] == startup["packageProofSha256"] == sha(paths["package"]),
            "package evidence mismatch")
    test = Path(build["build"]) / "valmax_transfer_app_test.exe"
    require(sha(Path(runtime["fixture"]) / test.name) == sha(test), "tested binary changed")
    checks = sum(entry["result"]["checks"] for entry in runtime["results"].values())
    require(checks == 172, "unexpected completed check count")
    for entry in runtime["results"].values():
        require(entry["result"]["ok"] and entry["process"]["exitCode"] == 0
                and entry["process"]["remainingProcesses"] == 0, "test cleanup incomplete")
    require(startup["process"]["exitCode"] == 0 and startup["process"]["remainingProcesses"] == 0
            and startup["process"]["closeOwnerVerified"], "production startup incomplete")
    for path, digest in runtime["screenshots"].items():
        require(sha(path) == digest, "render evidence changed")
    distribution_ready = False
    if args.distribution_proof:
        require(args.distribution_proof.resolve().parent == ANALYSIS.resolve(), "distribution proof outside analysis")
        distribution = json.loads(args.distribution_proof.read_text("utf8"))
        require(distribution.get("kind") == "valmax-transfer-distribution" and distribution.get("gaps") == []
                and distribution.get("distributionReady") is True and package.get("distributionReady") is True
                and distribution.get("packageManifestSha256") == package["manifestSha256"], "distribution evidence incomplete")
        materials_ref = distribution["materialsProof"]
        materials_path = Path(materials_ref["path"])
        require(materials_path.resolve().parent == ANALYSIS.resolve() and sha(materials_path) == materials_ref["sha256"],
                "materials receipt changed")
        materials = json.loads(materials_path.read_text("utf8"))
        require(materials.get("kind") == "valmax-transfer-materials" and materials.get("gaps") == []
                and materials.get("components") == ["qt", "msvc"]
                and materials.get("packagedMaterials") == distribution.get("packagedMaterials"), "materials evidence incomplete")
        for item in materials["packagedMaterials"]:
            name = item["path"]
            require(name.startswith("licenses/") and package["fileHashes"].get(name) == item["sha256"], "unpackaged material")
        paths["distribution"] = args.distribution_proof
        distribution_ready = True
    proof = {"schema": 1, "verified": True, "scope": "Standalone compact MData helper only",
             "checks": checks, "productionIdleStartupVerified": True,
             "coreSha256": build["coreSha256"], "helperSha256": sha(folder / "valmax-transfer.exe"),
             "package": str(folder), "packageManifestSha256": package["manifestSha256"],
             "packagedFilesExcludingManifest": package["files"], "packageBytesExcludingManifest": package["bytes"],
             "dependencyEdges": package["dependencyEdges"],
             "proofs": {key: {"path": str(path), "sha256": sha(path)} for key, path in paths.items()},
             "screenshots": runtime["screenshots"], "summarySourceSha256": sha(__file__),
             "nativeCompactImportUiImplemented": True, "noPythonRuntimeInHelper": True,
             "persistentHttpServerInHelper": False, "installerIntegrated": False,
             "fullProfileRestoreImplemented": False, "realAccountTransferVerified": False,
             "installedClientChanged": False, "distributionReady": distribution_ready,
             "remaining": ([] if distribution_ready else ["Complete Qt notices and corresponding source distribution materials"]) +
                          ["Installer and native launcher integration with existing entry points",
                           "Full-profile recovery and remaining native client integration",
                           "End-to-end login verification on an explicitly selected test account"]}
    with report_path.open("x", encoding="utf8") as output:
        output.write(json.dumps(proof, indent=2) + "\n")
    print(json.dumps({"verified": True, "checks": checks, "report": str(report_path),
                      "helperSha256": proof["helperSha256"], "installerIntegrated": False}))


if __name__ == "__main__":
    main()
