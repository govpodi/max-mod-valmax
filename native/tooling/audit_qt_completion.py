"""Statically resolve Qt import symbols of the newly completed runtime branch.

No target DLL/executable is loaded. This narrow check complements the complete
module-availability graph; it is not a clean-machine or dynamic-load test.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import pefile

from assemble_native_runtime import read_json, require, relative, sha, ordinary, is_qt_webengine_runtime_path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-proof", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    require(args.report.parent.resolve() == (ROOT / "analysis/native-migration-v1").resolve() and not args.report.exists(), "new report required")
    staging = read_json(args.staging_proof)
    completion = staging.get("qtRuntimeCompletion")
    require(staging.get("assembled") is True and completion and completion.get("binaryVersions"), "completed runtime proof required")
    runtime = Path(staging["runtime"])
    inventory = {r["path"].lower(): r for r in staging["inventory"]}
    sources = [r["path"] for r in completion["binaryVersions"]]
    slim = bool((staging.get("qtWebEngineOmission") or {}).get("enabled"))
    if slim:
        require(completion["modules"] == ["qtserialport"]
                and not any(is_qt_webengine_runtime_path(row["path"]) for row in staging["inventory"]),
                "slim QtWebEngine omission did not survive staging")
        sources += ["mod/Qt6WebView.dll", "mod/Qt6WebViewQuick.dll",
                    "mod/plugins/webview/qtwebview_webview2.dll",
                    "mod/qml/QtWebView/qtwebviewquickplugin.dll"]
    else:
        sources += ["mod/qml/QtWebEngine/qtwebenginequickplugin.dll",
                    "mod/qml/QtWebEngine/ControlsDelegates/qtwebenginequickdelegatesplugin.dll"]
    exports, inputs, edges, failures = {}, {}, [], []
    def image(path):
        ordinary(path)
        relative_path = path.relative_to(runtime).as_posix()
        row = inventory[relative_path.lower()]
        require(sha(path) == row["sha256"], "stage image changed")
        inputs[relative_path] = row["sha256"]
        return pefile.PE(str(path), fast_load=True, max_symbol_exports=65536)
    def symbols(path):
        if path not in exports:
            pe = image(path)
            try:
                pe.parse_data_directories(directories=[0])
                table = pe.DIRECTORY_ENTRY_EXPORT
                require(table.struct.NumberOfNames <= 65536 and table.struct.NumberOfFunctions <= 65536, "Qt export bound")
                require(sum(bool(s.name) for s in table.symbols) == table.struct.NumberOfNames, "Qt exports incompletely parsed")
                result = {}
                for s in table.symbols:
                    forwarder = s.forwarder.decode("ascii") if s.forwarder else None
                    result[s.ordinal] = forwarder
                    if s.name: result[s.name.decode("ascii")] = forwarder
                exports[path] = result
            finally:
                pe.close()
        return exports[path]
    for source in sources:
        pe = image(runtime / relative(source))
        try:
            pe.parse_data_directories(directories=[1, 13])
            for kind, attr in (("direct", "DIRECTORY_ENTRY_IMPORT"), ("delay", "DIRECTORY_ENTRY_DELAY_IMPORT")):
                for descriptor in getattr(pe, attr, []):
                    name = descriptor.dll.decode("ascii").lower()
                    if not name.startswith("qt6"): continue
                    row = inventory.get("mod/" + name)
                    require(row is not None, "Qt dependency missing in completed runtime")
                    target = runtime / relative(row["path"])
                    exported = symbols(target)
                    requested = [s.name.decode("ascii") if s.name else s.ordinal for s in descriptor.imports]
                    missing = [symbol for symbol in requested if symbol not in exported]
                    forwarders = [symbol for symbol in requested if exported.get(symbol) is not None]
                    edge = {"image": source, "kind": kind, "module": row["path"], "requestedSymbols": len(requested),
                            "missingSymbols": missing, "unresolvedForwarders": forwarders}
                    edges.append(edge)
                    if missing or forwarders: failures.append(edge)
        finally:
            pe.close()
    proof = {"schema": 1, "verified": not failures, "qtWebEngineOmitted": slim,
             "stagingProof": str(args.staging_proof),
             "stagingProofSha256": sha(args.staging_proof), "runtimeInventorySha256": staging["runtimeInventorySha256"],
             "inputs": inputs, "edges": edges, "symbolsChecked": sum(e["requestedSymbols"] for e in edges),
             "failures": failures, "targetsExecuted": False, "sourceSha256": sha(Path(__file__)),
             "scope": "Qt import symbols for added release modules and either the baseline WebEngine plugins or the retained QtWebView path. No OS symbols, dynamic-load or whole-client execution claim."}
    with args.report.open("x", encoding="utf8") as output: output.write(json.dumps(proof, indent=2) + "\n")
    print(json.dumps({"verified": proof["verified"], "symbolsChecked": proof["symbolsChecked"], "edges": len(edges), "failures": len(failures)}))
    raise SystemExit(0 if proof["verified"] else 1)


if __name__ == "__main__": main()
