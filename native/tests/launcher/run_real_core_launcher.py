"""Exercise ordinary Win32LaunchDriver with a frozen real core and own fake MAX.

Only explicit D build/fixture paths are used. No KnownFolder GUI entrypoint,
installed MAX, account sign-in, credential operation or hook is executed.
Python remains a build/test tool, never an installed runtime dependency.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import uuid
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[3]
# Full receipt identities select one reviewed frozen core pair. CLI input does
# not permit loading a caller-supplied DLL or accepting a caller-supplied hash.
FROZEN_CORE_PROOFS = {
    "2ca40ae490cbb2804878ceda2c110e0731c23608dbfbe259e50ecd76f9f335ef": {
        "corePairProofSha256": "6230c05deeaac58481b39250a24d2cab9fba91102f1153478b950dd67d42fdae",
        "sourceProofSha256": "deac640722c9621582e4a823d7594c4d840078369ac68cca27246fb31bd34fac",
        "coreSha256": "cf54fba9a05e668762789e7e422faee2facd37ac78b7fde50be9adf5391c65ca",
        "importLibrarySha256": "0fe021aabd8103c9c636c2e4322de9fc2080f09e35d27a7ed656ba099a6acc6d",
    },
}
BUILD_ROOT = Path("D:/ValMAXBuild/build").resolve()
FIXTURE_ROOT = Path("D:/ValMAXBuild/fixtures/native-launcher").resolve()
REPORT_ROOT = ROOT / "analysis/native-migration-v1"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ordinary(path, directory=False):
    info = path.lstat()
    if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("reparse input/output")
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError("unexpected input/output type")
    if not directory and info.st_nlink != 1:
        raise ValueError("hardlinked input/output")


def owned(path, root):
    if not path.is_absolute():
        raise ValueError("absolute explicit path required")
    value = path.resolve()
    if not value.is_relative_to(root) or value == root:
        raise ValueError("path outside owned root")
    for ancestor in (path, *path.parents):
        if ancestor.exists():
            ordinary(ancestor, ancestor.is_dir())
    return value


def require(value, reason):
    if not value:
        raise ValueError(reason)


def verified_core_inputs(runtime_proof_path, pair_proof_path):
    """Validate the reviewed receipt chain before building or loading a core."""
    runtime_proof_path = owned(runtime_proof_path, REPORT_ROOT)
    ordinary(runtime_proof_path)
    runtime_sha = sha(runtime_proof_path)
    require(runtime_sha in FROZEN_CORE_PROOFS, "unreviewed runtime proof")
    expected = FROZEN_CORE_PROOFS[runtime_sha]
    runtime = json.loads(runtime_proof_path.read_text("utf8"))
    require(runtime["verified"] is True and runtime["actualMaxStarted"] is False,
            "runtime proof is not an ordinary verified fixture")
    require(runtime["coreSha256"] == expected["coreSha256"], "runtime core identity differs")
    pair_path = owned(pair_proof_path, BUILD_ROOT)
    ordinary(pair_path)
    require(pair_path == owned(Path(runtime["corePairProof"]), BUILD_ROOT),
            "explicit pair differs from runtime proof")
    pair_sha = sha(pair_path)
    require(pair_sha == runtime["corePairProofSha256"] == expected["corePairProofSha256"],
            "core-pair proof identity differs")
    pair = json.loads(pair_path.read_text("utf8"))
    require(pair["verified"] is True and pair["sourceSnapshotMatches"] is True
            and pair["importLibrarySymbolsMatch"] is True, "core pair is not verified")
    require(owned(Path(pair["snapshot"]), BUILD_ROOT) == pair_path.parent,
            "core-pair snapshot differs")
    source_proof = owned(Path(pair["sourceProof"]), REPORT_ROOT)
    ordinary(source_proof)
    require(sha(source_proof) == pair["sourceProofSha256"] == expected["sourceProofSha256"],
            "core source proof identity differs")
    source = json.loads(source_proof.read_text("utf8"))
    require(source["ok"] is True and source["sourceSnapshotMatches"] is True,
            "core build proof is not verified")
    core = owned(pair_path.parent / "valmax_core.dll", BUILD_ROOT)
    library = owned(pair_path.parent / "valmax_core.dll.lib", BUILD_ROOT)
    ordinary(core)
    ordinary(library)
    require(sha(core) == pair["hashes"]["dll"] == source["dll"]["sha256"]
            == expected["coreSha256"], "frozen core DLL identity differs")
    require(sha(library) == pair["hashes"]["importLibrary"] == source["importLibrary"]["sha256"]
            == expected["importLibrarySha256"], "frozen core import-library identity differs")
    crt_rows = [row for row in runtime["dependencyClosure"]
                if Path(row["path"]).name.lower() == "vcruntime140.dll"]
    require(len(crt_rows) == 1, "unique recorded VCRuntime required")
    dependency = owned(Path(crt_rows[0]["path"]), BUILD_ROOT)
    ordinary(dependency)
    require(sha(dependency) == crt_rows[0]["sha256"], "recorded VCRuntime identity differs")
    return {"runtimeProof": runtime_proof_path, "runtimeProofSha256": runtime_sha,
            "corePairProof": pair_path, "corePairProofSha256": pair_sha,
            "sourceProof": source_proof, "sourceProofSha256": sha(source_proof),
            "core": core, "coreSha256": expected["coreSha256"],
            "importLibrary": library, "importLibrarySha256": expected["importLibrarySha256"],
            "dependency": dependency, "dependencySha256": crt_rows[0]["sha256"]}


def run(command, log_path, environment):
    result = subprocess.run([str(arg) for arg in command], cwd=ROOT, env=environment,
                            capture_output=True, timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
    with log_path.open("ab") as log:
        log.write(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("Owned launcher check failed; inspect " + str(log_path))
    return result.stdout.decode("utf8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--runtime-proof", type=Path, required=True)
    parser.add_argument("--core-pair-proof", type=Path, required=True)
    parser.add_argument("--proof", type=Path, required=True)
    args = parser.parse_args()
    build = owned(args.build, BUILD_ROOT)
    args.proof = args.proof.resolve()
    if args.proof.exists():
        raise ValueError("new proof required")
    inputs = verified_core_inputs(args.runtime_proof, args.core_pair_proof)
    core, dependency = inputs["core"], inputs["dependency"]
    frozen_core_sha = inputs["coreSha256"]
    build.mkdir(parents=True, exist_ok=True)
    log_path = build / ("real-core-launcher-" + uuid.uuid4().hex + ".log")
    paths = sorted(set([*(ROOT / "native/launcher").glob("*.cpp"), *(ROOT / "native/launcher").glob("*.h"),
                        *(ROOT / "native/launcher").glob("*.cmake"), ROOT / "native/launcher/CMakeLists.txt",
                        ROOT / "native/core/valmax_core.h", ROOT / "native/tooling/msvc_env.ps1",
                        ROOT / "native/transfer/closed_clients.cpp", ROOT / "native/transfer/closed_clients.h",
                        ROOT / "native/transfer/closed_clients.cmake",
                        ROOT / "native/common/startup_context.cpp", ROOT / "native/common/startup_context.h",
                        ROOT / "native/common/startup_context.cmake",
                        ROOT / "native/tests/launcher/launcher_child.cpp", ROOT / "native/tests/launcher/launcher_owned_test.cpp",
                        ROOT / "native/tests/launcher/launcher_test_core.cpp", ROOT / "native/tests/launcher/launch_session_test.cpp",
                        Path(__file__)]))
    source_hashes = {path.relative_to(ROOT).as_posix(): sha(path) for path in paths}
    environment = os.environ.copy()
    environment.update(TEMP="D:/ValMAXBuild/tmp", TMP="D:/ValMAXBuild/tmp")
    run(["cmake", "-S", ROOT / "native/launcher", "-B", build, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release"], log_path, environment)
    run(["cmake", "--build", build, "--target", "valmax_launch_session_test", "valmax_launcher_owned_test",
         "valmax_launcher_child", "valmax_launcher_test_core", "--parallel", "1"], log_path, environment)
    policy = json.loads(run([build / "valmax_launch_session_test.exe"], log_path, environment))
    fake_fixture, real_fixture = [FIXTURE_ROOT / uuid.uuid4().hex for _ in range(2)]
    fake = json.loads(run([build / "valmax_launcher_owned_test.exe", fake_fixture, build / "max.exe", build / "valmax_core.dll"], log_path, environment))
    real = json.loads(run([build / "valmax_launcher_owned_test.exe", real_fixture, build / "max.exe", core, dependency], log_path, environment))
    assert policy["ok"] and fake["ok"] and real["ok"]
    assert fake["realCore"] is False and real["realCore"] is True and real["opaqueAccountBytesPreserved"] is True
    for result in (fake, real):
        assert result["ownChildOnly"] is True and result["realProfilesTouched"] is False and result["realCredentialsTouched"] is False
        assert result["protectionReadinessTested"] is False
    assert source_hashes == {path.relative_to(ROOT).as_posix(): sha(path) for path in paths}
    require(verified_core_inputs(inputs["runtimeProof"], inputs["corePairProof"]) == inputs,
            "reviewed core inputs changed during checks")
    actual_core = real_fixture / "runtime/mod/valmax_core.dll"
    actual_crt = real_fixture / "runtime/mod/vcruntime140.dll"
    assert sha(actual_core) == frozen_core_sha and sha(actual_crt) == inputs["dependencySha256"]
    profile = real_fixture / "local/MXMOD"
    config = json.loads((profile / "config.json").read_text("utf8"))
    assert config["app_folder_name"] == "MXMOD" and config["auto_update_enabled"] is False
    assert config["ownedSyntheticExtra"] == {"preserve": "fixture", "count": 2}
    active = json.loads((real_fixture / "runtime/mod/optimization-active.json").read_text("utf8"))
    assert active["schema"] == 1 and len(active["settings"]) == 11 and all(v is True for v in active["settings"].values())
    proof = {"schema": 1, "checkedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "ok": True, "buildDirectory": str(build), "policy": policy, "fakeCoreRegression": fake,
             "realCoreRoundtrip": real, "fakeFixture": str(fake_fixture), "realFixture": str(real_fixture),
             "coreSha256": sha(core), "bundledVCRuntimeSha256": sha(dependency),
             "runtimeProof": str(inputs["runtimeProof"]), "runtimeProofSha256": inputs["runtimeProofSha256"],
             "corePairProof": str(inputs["corePairProof"]), "corePairProofSha256": inputs["corePairProofSha256"],
             "coreSourceProof": str(inputs["sourceProof"]), "coreSourceProofSha256": inputs["sourceProofSha256"],
             "coreImportLibrarySha256": inputs["importLibrarySha256"],
             "log": str(log_path), "logSha256": sha(log_path),
             "profileArtifacts": {name: sha(profile / name) for name in ("config.json", "opaque-owned-account-state.bin")},
             "sources": source_hashes, "sourceUnchanged": True,
             "artifacts": {name: sha(build / name) for name in ("valmax_launch_session_test.exe", "valmax_launcher_owned_test.exe", "max.exe", "valmax_core.dll")},
             "guiEntrypointExecuted": False, "actualMaxStarted": False, "realProfilesOrWinCredAccessed": False,
             "accountSignInTested": False, "mdataImportExecuted": False, "protectionReadinessTested": False,
             "distributionReady": False,
             "scope": "Actual C++ Win32LaunchDriver to frozen Rust config/runtime preparation to an owned fake child; hidden-window activation and MData command routing only. No complete-client/sign-in/readiness claim."}
    args.proof.parent.mkdir(parents=True, exist_ok=True)
    args.proof.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps({"ok": True, "policyChecks": policy["checks"], "fakeCoreChecks": fake["checks"], "realCoreChecks": real["checks"],
                      "proof": str(args.proof), "distributionReady": False}))


if __name__ == "__main__":
    main()
