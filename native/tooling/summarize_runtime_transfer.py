"""Record successful native transfer checks without claiming full migration."""
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
    log = Path("C:/ValMAXBuild/build/runtime-transfer-rust-final.log")
    raw = log.read_text("utf8")
    rows = re.findall(r"test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored", raw)
    assert len(rows) == 20 and "test result: FAILED" not in raw
    counts = [sum(int(row[index]) for row in rows) for index in range(3)]
    assert counts == [237, 0, 0]
    reports = {name: json.loads((REPORTS / name).read_text("utf8")) for name in (
        "qt-runtime-transfer-build.json", "qt-runtime-transfer-proof.json", "mdata-transport-proof.json", "qt-generator-proof.json")}
    build = reports["qt-runtime-transfer-build.json"]
    for path, digest in build["sourceHashes"].items():
        assert sha(ROOT / path) == digest, path
    for path, digest in build["artifacts"].items():
        assert sha(path) == digest, path
    qt = reports["qt-runtime-transfer-proof.json"]
    assert qt["verified"] and qt["buildProofSha256"] == sha(REPORTS / "qt-runtime-transfer-build.json")
    native_checks = sum(item["result"]["checks"] for item in qt["results"].values())
    native_checks += sum(item["checks"] for item in qt["lifecycle"]["result"]["cycles"])
    native_checks += qt["lifecycle"]["result"]["mediaEngineChecks"] + qt["lifecycle"]["result"]["mediaComposerChecks"]
    assert native_checks == 405
    children = list(qt["results"].values()) + [qt["lifecycle"]]
    assert all(v["process"]["exitCode"] == 0 and v["process"]["remainingProcesses"] == 0 and not v["process"]["timedOut"] for v in children)
    proof = {"schema": 1, "verified": True, "rust": {"suites": len(rows), "passed": counts[0], "failed": counts[1], "ignored": counts[2],
             "log": str(log), "logSha256": sha(log), "knownWarnings": "Win32 GetFileInformationByHandle declarations use separate local repr(C) struct types; linker import-library note"},
             "qtPluginChecks": native_checks, "qmlStubTransportChecks": 90,
             "proofHashes": {name: sha(REPORTS / name) for name in reports}, "corePin": build["corePin"],
             "newRoutes": ["mdata.inspect", "export.start", "export.page", "export.media-chunk", "export.media", "export.finish", "export.status", "export.history", "export.cancel"],
             "hostShutdownSignal": "valmax_runtime_cancel_pending", "ownedChildProcessesRemaining": 0,
             "syntheticOnly": True, "installedClientChanged": False, "realAccountsUsed": False, "networkUsed": False,
             "mdataImportRecoveryComplete": False, "exportNativeHistoryMediaHostConnected": False, "nativeInstallerReady": False,
             "limits": ["Selected-file preview and export coordinator only; not end-to-end native MAX acceptance",
                        "UI abort discards preview delivery; it does not interrupt active scrypt",
                        "Shutdown signals export cancellation, but cannot guarantee an OS filesystem operation latency",
                        "Native launcher/bootstrap and remaining feature backends still require integration"]}
    path = REPORTS / "runtime-transfer-native-proof.json"
    path.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf8")
    print(json.dumps({"verified": True, "rustTests": counts[0], "qtChecks": native_checks, "proof": str(path)}))


if __name__ == "__main__":
    main()
