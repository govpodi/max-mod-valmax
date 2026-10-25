"""Freeze dependency evidence only after every case matches current binaries."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
evidence = ROOT / "analysis/native-migration-v1"
build = Path("C:/ValMAXBuild/build/qt-webview-guard")
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def read(name): return json.loads((evidence/name).read_text(encoding="utf8"))
def require(value, message):
    if not value: raise RuntimeError(message)

production = build / "qtwebview_webview2.dll"
fake = build / "timeout-test/qtwebview_webview2.dll"
production_hash, fake_hash = sha(production), sha(fake)
require(production_hash != fake_hash, "synthetic_backend_must_be_distinct")
source = read("qt-webview2-guard-source.json")
require(source["patchSha256"] == sha(ROOT/"native/qt/webview/qt-6.11.2-init.patch"), "source_patch_changed")
for name, expected in source["outputs"].items():
    require(sha(Path(source["output"])/name) == expected, "generated_source_changed")
cases = {}
for mode in ("good", "missing-runtime", "blocked-data", "blocked-child", "unset-data", "timeout", "delete-on-signal", "failed-controller", "failed-controller-delete"):
    proof_name = "qt-webview2-guard-" + mode + "-proof.json"
    proof = read(proof_name)
    require(proof["verified"] and proof["jobProcessesAfterTermination"] == 0, "incomplete_case:" + mode)
    require(not proof["sandboxDisabled"] and not proof["businessUrlLoaded"] and not proof["realLoginUsed"], "invalid_case_scope")
    require(proof["qtHashes"]["plugins/webview/qtwebview_webview2.dll"] == (production_hash if mode == "good" else fake_hash), "case_binary_changed:"+mode)
    if mode != "good":
        require(proof["negativeUsesSyntheticLoader"] and not any(Path(path).name == "msedgewebview2.exe" for path in proof["ownedExecutablePaths"]), "negative_browser_child")
    cases[mode] = {"proof": proof_name, "sha256": sha(evidence/proof_name), "verified": True,
        "jobProcessesAfterTermination": 0}
controller = read("qt-business-controller-proof.json")
require(controller["verified"] and controller["productionPanelSha256"] == sha(ROOT/"native/qt/BusinessPanel.qml"), "controller_source_changed")
generator = read("qt-generator-proof.json")
require(generator["verified"] and generator["legacySourcesAndSpecIdentical"], "legacy_generator_regression")
bundle = read("qt-webview2-bundle-proof.json")
require(bundle["verified"], "bundle_not_verified")
source_build = read("qt-webview2-source-build-proof.json")
require(source_build["verified"] and source_build["sourceArchiveSha256"] == sha(Path(source_build["sourceArchive"])), "source_archive_changed")
result = {"schema": 1, "verified": True, "stage": "G15 dependency and controller proof",
    "runtimeVersion": "155.0.4283.60", "qtVersion": "6.11.2", "webView2SdkVersion": "1.0.4258.31",
    "productionBackend": {"path": str(production), "sha256": production_hash},
    "syntheticBackendExcludedFromRelease": {"path": str(fake), "sha256": fake_hash},
    "patchSha256": source["patchSha256"], "cases": cases,
    "businessControllerChecks": controller["checks"], "legacyGeneratorEquivalent": True,
    "sourceDistributionProof": "qt-webview2-source-distribution.json",
    "sourceArchiveRebuildProof": "qt-webview2-source-build-proof.json",
    "runtimeBundleProof": "qt-webview2-bundle-proof.json",
    "historicalPopupRetained": "qt-webview2-guard-blocked-data-historical.json",
    "userInstallationChanged": False, "installerIntegrationVerified": False,
    "limitations": [
        "about:blank confirms native Qt/WebView2 compatibility, not real business login or page rendering.",
        "Normal Microsoft WebView2 sandbox remains enabled; background browser network requests are possible.",
        "Negative COM/data-path tests use a separate synthetic loader and create no browser child.",
        "Directory precheck rejects known file/path/write failures before browser creation; later ACL/disk/runtime faults may still show an internal Edge dialog.",
        "Bundle, notices, corresponding source and launcher environment still require final installer integration and clean-machine validation.",
        "Windows 10 unpackaged Win32 deployment needs Microsoft's documented AppContainer RX permissions on the fixed runtime directory; Windows 10 was not tested here."]}
(evidence/"qt-webview2-final-proof.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
print(json.dumps(result, ensure_ascii=False))
