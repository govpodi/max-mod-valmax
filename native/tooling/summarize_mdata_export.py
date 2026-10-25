"""Record native compact export checks with explicit acceptance boundaries."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "analysis/native-migration-v1"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    log = Path("C:/ValMAXBuild/build/mdata-export-rust-final.log")
    raw = log.read_text("utf8")
    rows = re.findall(r"test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored", raw)
    counts = [sum(int(row[index]) for row in rows) for index in range(3)]
    assert len(rows) == 21 and counts == [264, 0, 0] and "test result: FAILED" not in raw
    reports = {name: json.loads((REPORTS / name).read_text("utf8")) for name in (
        "qt-mdata-export-build.json", "qt-mdata-export-proof.json",
        "mdata-export-transport-proof.json", "qt-mdata-export-generator-proof.json")}
    build = reports["qt-mdata-export-build.json"]
    for path, digest in build["sourceHashes"].items():
        assert sha(ROOT / path) == digest, path
    for path, digest in build["artifacts"].items():
        assert sha(path) == digest, path
    qt = reports["qt-mdata-export-proof.json"]
    assert qt["verified"] and qt["buildProofSha256"] == sha(REPORTS / "qt-mdata-export-build.json")
    native_checks = sum(item["result"]["checks"] for item in qt["results"].values())
    native_checks += sum(item["checks"] for item in qt["lifecycle"]["result"]["cycles"])
    native_checks += qt["lifecycle"]["result"]["mediaEngineChecks"] + qt["lifecycle"]["result"]["mediaComposerChecks"]
    assert native_checks > 405
    transport = reports["mdata-export-transport-proof.json"]
    assert transport["verified"]
    stub_checks = transport["result"]["result"]["checks"]
    assert stub_checks > 90
    for path, digest in transport["sourceHashes"].items():
        assert sha(ROOT / path) == digest, path
    children = list(qt["results"].values()) + [qt["lifecycle"], transport["result"]]
    assert all(v["process"]["exitCode"] == 0 and v["process"]["remainingProcesses"] == 0
               and not v["process"]["timedOut"] for v in children)
    proof = {
        "schema": 1, "verified": True,
        "rust": {"suites": len(rows), "passed": counts[0], "failed": 0, "ignored": 0,
                 "log": str(log), "logSha256": sha(log),
                 "knownWarnings": "Three Win32 extern declaration type warnings and linker import-library note"},
        "qtPluginChecks": native_checks, "qmlStubTransportChecks": stub_checks,
        "proofHashes": {name: sha(REPORTS / name) for name in reports}, "corePin": build["corePin"],
        "newRoutes": ["mdata.export", "mdata.export-selected"],
        "successfulExportWithFakeCredentials": True, "actualQtExportValidationBeforeCredentials": True,
        "realWinCredCalled": False, "ownedChildProcessesRemaining": 0,
        "syntheticOnly": True, "installedClientChanged": False, "networkUsed": False,
        "mdataImportRecoveryComplete": False, "nativeInstallerReady": False,
        "limits": [
            "Positive encrypted-file export uses synthetic credentials; actual account acceptance is pending",
            "Repeated live snapshot checks are not an atomic transaction with a running client",
            "Cancellation is checked between stages; it cannot interrupt scrypt or guarantee filesystem latency",
            "Cancellation after the last check may lose to atomic publication",
            "An error after kernel rename cannot prove the selected file is absent",
            "Import needs a one-shot native transfer host that survives closing MAX",
            "Legacy full-profile recovery, remaining feature integration and the installer remain unfinished"]}
    path = REPORTS / "mdata-export-native-proof.json"
    with path.open("x", encoding="utf8") as stream:
        stream.write(json.dumps(proof, indent=2) + "\n")
    print(json.dumps({"verified": True, "rustTests": counts[0], "qtChecks": native_checks,
                      "transportChecks": stub_checks, "proof": str(path)}))


if __name__ == "__main__":
    main()
