"""Check the opt-in QtWebEngine omission against frozen MAX/p16 inputs.

Static PE and source inspection only. This does not launch MAX or a WebView.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "native/tooling"))
from assemble_native_runtime import (  # noqa: E402
    Plan, QT_WEBENGINE_BASELINE_FILES, QT_WEBVIEW_RUNTIME_FILES,
    add_qt_completion, dependency_closure, is_qt_webengine_runtime_path,
    omit_orphan_qt_webengine, read_json, relative, require,
    verify_inventory, verify_qt_webengine_absent,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path,
                        default=Path("D:/CodexLabs/max-mod-26.35.1/runtime/baseline"))
    parser.add_argument("--layout", type=Path,
                        default=Path("D:/CodexLabs/max-mod-26.35.1/analysis/restore_layout_report.json"))
    parser.add_argument("--completion", type=Path,
                        default=Path("D:/ValMAX/analysis/native-migration-v1/qt-runtime-completion-inputs-20261021.json"))
    parser.add_argument("--materials", type=Path,
                        default=Path("D:/ValMAX/analysis/native-migration-v1/qt-runtime-completion-materials-20261021.json"))
    parser.add_argument("--manifest", type=Path,
                        default=Path("E:/ValMAXBuild/build/package-public-beta-p16-20261025-2043/payload/native-package.json"))
    parser.add_argument("--runtime", type=Path,
                        default=Path("E:/ValMAXBuild/build/package-public-beta-p16-20261025-2043/payload/runtime"))
    parser.add_argument("--report", type=Path, default=ROOT / "analysis/webengine-prune-static-proof.json")
    args = parser.parse_args()

    layout = read_json(args.layout)
    require(Path(layout["output"]).resolve() == args.baseline.resolve(), "baseline layout path mismatch")
    baseline_rows = verify_inventory(args.baseline, layout["layout"])
    plan = Plan()
    for row in baseline_rows:
        path = "mod/" + row["path"]
        plan.entries[path.casefold()] = {"path": path, "source": str(args.baseline / relative(row["path"])),
                                         "sha256": row["sha256"], "component": "frozen-baseline"}
    omission = omit_orphan_qt_webengine(plan, args.baseline, baseline_rows)
    require(set(omission["omittedBaselineHashes"]) == QT_WEBENGINE_BASELINE_FILES,
            "incorrect baseline QtWebEngine omission")
    require(not any(is_qt_webengine_runtime_path(row["path"]) for row in plan.entries.values()),
            "QtWebEngine baseline file survived")

    # A changed/missing stock plugin inventory must stop the mode before copying.
    missing_plugin_rows = [row for row in baseline_rows
                           if row["path"] != "qml/QtWebEngine/qtwebenginequickplugin.dll"]
    rejected_changed_baseline = False
    try:
        omit_orphan_qt_webengine(plan, args.baseline, missing_plugin_rows)
    except ValueError:
        rejected_changed_baseline = True
    require(rejected_changed_baseline, "changed baseline plugin inventory was accepted")

    completion = add_qt_completion(plan, args.completion, args.materials, include_webengine=False)
    require(completion["modules"] == ["qtserialport"] and completion["webEngineIncluded"] is False
            and all(not is_qt_webengine_runtime_path(path) for path in completion["newRuntimeFiles"]),
            "slim Qt completion admitted WebEngine")

    package = read_json(args.manifest)
    require(package["version"] == "8.9.0-native.preview.16" and len(package["runtimeFiles"]) == 4429,
            "different published runtime fixture")
    dropped = [row for row in package["runtimeFiles"] if is_qt_webengine_runtime_path(row["path"])]
    require(len(dropped) == 107 and sum(row["size"] for row in dropped) == 860819538,
            "QtWebEngine family differs from the audited p16 set")
    filtered = [{"path": row["path"], "bytes": row["size"], "sha256": row["sha256"]}
                for row in package["runtimeFiles"] if not is_qt_webengine_runtime_path(row["path"])]
    require(QT_WEBVIEW_RUNTIME_FILES <= {row["path"] for row in filtered},
            "QtWebView path missing from filtered runtime")
    source_paths = {row["path"]: args.runtime / relative(row["path"]) for row in filtered}
    closure = dependency_closure(args.runtime, filtered, source_paths)
    audit = verify_qt_webengine_absent({row["path"] for row in filtered}, closure)
    require(closure["verified"] and audit["verified"],
            "pruned p16 PE dependency graph did not close")

    result = {"schema": 1, "verified": True, "scope": "Static PE graph and frozen source inventory only; no client or WebView launched.",
              "baselineFiles": len(baseline_rows), "stockQmlScanned": omission["stockQmlFilesScanned"],
              "baselineOrphanFilesRemoved": len(omission["omittedBaselineHashes"]),
              "changedBaselineRejected": rejected_changed_baseline,
              "selectedCompletionModules": completion["modules"],
              "p16FamilyFilesOmitted": len(dropped), "p16FamilyLogicalBytesOmitted": sum(row["size"] for row in dropped),
              "filteredPeImages": closure["images"], "missingPeDependencies": len(closure["missing"]),
              "qtWebViewPathsPresent": audit["qtWebViewRuntimeFiles"],
              "installedClientTested": False, "businessWebViewTested": False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf8") as output:
        output.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"verified": True, "familyFilesOmitted": len(dropped),
                      "logicalBytesOmitted": result["p16FamilyLogicalBytesOmitted"],
                      "filteredPeImages": closure["images"], "missingPeDependencies": 0,
                      "report": str(args.report)}))


if __name__ == "__main__":
    main()
