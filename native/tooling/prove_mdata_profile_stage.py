"""Developer-only synthetic proof; no installed client/profile/WinCred access."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
BUILD = Path("C:/ValMAXBuild")
PATTERN = re.compile(r"test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored;")


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def sources() -> dict[str, str]:
    paths = list((ROOT / "native/core/src").rglob("*.rs"))
    paths += [ROOT / relative for relative in (
        "native/Cargo.toml", "native/Cargo.lock", "native/core/Cargo.toml",
        "native/core/tests/mdata_archive_contract.rs",
        "native/core/tests/mdata_recovery_state_native.rs",
        "native/core/tests/launcher_config_contract.rs",
        "modkit/config.defaults.json",
        "native/core/tests/mdata_profile_oracle.py",
        "native/tooling/prove_mdata_profile_stage.py",
    )]
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(paths)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    if not out.is_relative_to(BUILD.resolve()) or out == BUILD.resolve():
        raise SystemExit("output must be inside C:/ValMAXBuild")
    if args.report.exists():
        raise SystemExit("report already exists")
    out.mkdir(parents=True, exist_ok=False)
    before = sources()
    env = os.environ.copy()
    env.update(CARGO_TARGET_DIR=str(BUILD / "target-mdata-recovery"),
               CARGO_PROFILE_TEST_DEBUG="0", CARGO_PROFILE_TEST_OPT_LEVEL="2")
    runs = []
    for name, selection, expected in (
        ("profile-stage", ["--lib", "mdata_profile"], [43]),
        ("recovery-prepare", ["--lib", "mdata_recovery_"], [20]),
        ("recovery-credentials", ["--lib", "mdata_credential"], [26]),
        ("archive-regression", ["--test", "mdata_archive_contract"], [16]),
        ("recovery-marker-regression", ["--test", "mdata_recovery_state_native"], [7]),
        ("launcher-config-regression", ["--test", "launcher_config_contract"], [10]),
    ):
        cmd = ["cargo", "+1.99.1", "test", "--locked", "--manifest-path",
               str(ROOT / "native/Cargo.toml"), "-p", "valmax-core", *selection,
               "--", "--test-threads=2"]
        start = time.monotonic()
        run = subprocess.run(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
                             timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
        log = out / f"{name}.log"
        log.write_text(run.stdout, encoding="utf-8")
        results = [(int(p), int(f), int(i)) for p, f, i in PATTERN.findall(run.stdout)]
        verified = run.returncode == 0 and results == [(n, 0, 0) for n in expected]
        runs.append({"name": name, "command": cmd, "exitCode": run.returncode,
                     "seconds": round(time.monotonic() - start, 3), "results": results,
                     "verified": verified, "log": str(log), "logSha256": digest(log)})
        print(json.dumps({"run": name, "verified": verified, "results": results}), flush=True)
        if not verified:
            raise SystemExit("native tests failed; inspect bounded developer log")
    if before != sources():
        raise SystemExit("source changed during proof")
    fixture = Path(env.get("VALMAX_MDATA_PROFILE_FIXTURE",
                           str(BUILD / "fixtures/mdata-profile/contract.json")))
    cases = json.loads(fixture.read_bytes())
    groups = {key: len(cases[key]) for key in ("names", "paths", "profileIds", "targets", "storage")}
    # Cargo's executable suffix is not a protocol. Resolve the actual tested
    # path from the completed command's log.
    log_text = Path(runs[0]["log"]).read_text(encoding="utf-8")
    match = re.search(r"Running unittests [^\r\n]+ \(([^\r\n]+\.exe)\)", log_text)
    if match:
        binary = Path(match.group(1))
    else:
        raise SystemExit("missing executed test binary")
    report = {"schema": 1, "verified": True,
              "scope": "synthetic full-profile recovery with native Windows root publication/rollback and memory-only credentials",
              "rustTestsPassed": sum(p for run in runs for p, _, _ in run["results"]),
              "runs": runs, "sourceHashes": before,
              "testBinary": {"path": str(binary), "sha256": digest(binary)},
              "pythonDifferential": {"fixture": str(fixture), "sha256": digest(fixture),
                                     "groups": groups, "cases": sum(groups.values())},
              "boundaries": {"realProfilesAccessed": False, "realCredentialCalls": False,
                             "installedClientChanged": False, "wholeTreeInventoryVerified": True,
                             "inventoryAtomicSnapshot": False, "currentConfigPrepared": True,
                             "exactMarkerBackupPinned": True,
                             "credentialTransactionVerifiedWithMemoryStore": True,
                             "credentialAdapterVerifiedWithFakeWinApi": True,
                             "credentialExpectedValueCheckAtomic": False,
                             "credentialMutationsOnDrop": False,
                             "restorePublicationImplemented": True,
                             "nativeRecoveryCoordinatorImplemented": True,
                             "nativeRootMovesVerifiedOnOwnedFixtures": True,
                             "wholeProfileTransactionCrashAtomic": False,
                             "recoveryUiIntegrated": False,
                             "legacyMaxsessionSupportedByStage": False,
                             "fullNativeInstallerReady": False},
              "cleanup": "exact retained handles only; failures are sticky; unknown objects preserved",
              "knownWarnings": "existing Win32 extern declaration type-name clashes; no ABI edits"}
    with args.report.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(json.dumps({"verified": True, "tests": report["rustTestsPassed"],
                      "differentialCases": sum(groups.values()), "report": str(args.report)}))


if __name__ == "__main__":
    main()
