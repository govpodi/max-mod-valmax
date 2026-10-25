"""Build and run a pinned loader-only host against immutable FINAL staging."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "native/tooling"))
from assemble_native_runtime import read_json, sha, require, relative, inventory

spec = importlib.util.spec_from_file_location("ordinary_smoke_job", Path(__file__).with_name("run_native_media_plugin.py"))
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)

TARGETS = [
    "mod/Qt6SerialPort.dll", "mod/Qt6PrintSupport.dll", "mod/Qt6QuickWidgets.dll",
    "mod/Qt6WebEngineCore.dll", "mod/Qt6WebEngineQuick.dll", "mod/Qt6WebEngineQuickDelegatesQml.dll", "mod/Qt6WebEngineWidgets.dll",
    "mod/plugins/position/qtposition_nmea.dll", "mod/qml/QtWebEngine/qtwebenginequickplugin.dll",
    "mod/qml/QtWebEngine/ControlsDelegates/qtwebenginequickdelegatesplugin.dll",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-proof", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    require(args.build.is_absolute() and args.build.resolve().is_relative_to(Path("D:/ValMAXBuild/build").resolve()) and not args.build.exists(), "new owned D fixture required")
    require(args.report.parent.resolve() == (ROOT / "analysis/native-migration-v1").resolve() and not args.report.exists(), "new proof required")
    staging = read_json(args.staging_proof)
    runtime = Path(staging["runtime"])
    require(staging.get("assembled") is True and staging["dependencyClosure"]["verified"] is True, "completed exact staging required")
    require(inventory(runtime) == staging["inventory"], "FINAL runtime changed")
    rows = {r["path"]: r for r in staging["inventory"]}
    folded_rows = {name.casefold(): row for name, row in rows.items()}
    allowed = [r for r in staging["inventory"] if r["path"] in TARGETS or
               (r["path"].startswith("mod/") and "/" not in r["path"][4:] and Path(r["path"]).suffix.lower() == ".dll"
                and Path(r["path"]).name.lower().startswith(("qt6", "msvcp", "vcruntime", "concrt", "ucrt", "api-ms-")))]
    require(set(TARGETS) <= set(rows), "target missing")
    args.build.mkdir(parents=True)
    source = Path(__file__).with_name("loader_smoke.cpp")
    source_digest = sha(source)
    header = args.build / "loader_smoke_pins.h"
    def literal(value): return "L" + json.dumps(str(value).replace("/", "\\"), ensure_ascii=True)
    lines = ["// Exact immutable FINAL input pins, build generated.", "#pragma once",
             "struct SmokePin { const wchar_t* relative; const char* sha256; };",
             "inline constexpr wchar_t kRuntime[]=" + literal(runtime) + ";"]
    for name, values in (("kTargets", [rows[path] for path in TARGETS]), ("kAllowed", allowed)):
        lines.append("inline constexpr SmokePin " + name + "[]={")
        lines.extend("{" + literal(row["path"]) + ',"' + row["sha256"] + '"},' for row in values)
        lines.append("};")
    header.write_text("\n".join(lines) + "\n", "utf8")
    executable = args.build / "valmax_qt_loader_smoke.exe"
    command = ["cl", "/nologo", "/std:c++20", "/EHsc", "/W4", "/WX", "/O2", "/MT", "/DUNICODE", "/D_UNICODE", "/I"+str(args.build),
               str(source), "/Fe:"+str(executable), "/Fo:"+str(args.build/"loader_smoke.obj"), "/link", "bcrypt.lib", "psapi.lib", "/INCREMENTAL:NO"]
    compiled = subprocess.run(command, cwd=args.build, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
    (args.build / "compile.log").write_text(compiled.stdout+compiled.stderr, "utf8")
    require(compiled.returncode == 0, "smoke compile failed: " + compiled.stdout[-6000:] + compiled.stderr[-6000:])
    for name in ("home", "local", "roaming", "temp"):
        (args.build/name).mkdir()
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SystemDrive", "COMSPEC", "PATHEXT") if key in os.environ}
    env.update(PATH=str(Path(os.environ["SystemRoot"])/"System32"), USERPROFILE=str(args.build/"home"),
               LOCALAPPDATA=str(args.build/"local"), APPDATA=str(args.build/"roaming"), TEMP=str(args.build/"temp"), TMP=str(args.build/"temp"))
    result = host.run(args.build, executable, [], env, "loader-only")
    require(len(result["result"]["targets"]) == len(TARGETS), "incomplete target loads")
    loaded = result["result"]["modules"]
    for row in loaded:
        path = Path(row["path"])
        require(sha(path) == row["sha256"], "loaded module file hash changed")
        if row["source"] == "FINAL":
            name = path.relative_to(runtime).as_posix()
            # Windows preserves dependency import spelling (e.g. MSVCP140.dll)
            # in GetModuleFileNameW; filesystem identity is case-insensitive.
            require(name.casefold() in {r["path"].casefold() for r in allowed}
                    and folded_rows[name.casefold()]["sha256"] == row["sha256"], "unexpected staged loaded module")
    require(inventory(runtime) == staging["inventory"], "FINAL runtime changed during smoke")
    require(sha(source) == source_digest, "probe source changed")
    proof = {"schema": 1, "verified": True, "stagingProof": str(args.staging_proof), "stagingProofSha256": sha(args.staging_proof),
             "runtimeInventorySha256": staging["runtimeInventorySha256"], "fixture": str(args.build),
             "sources": {str(source.relative_to(ROOT)): source_digest, str(Path(__file__).relative_to(ROOT)): sha(Path(__file__))},
             "generatedPinsSha256": sha(header), "executableSha256": sha(executable), "compileLogSha256": sha(args.build/"compile.log"),
             "compileCommand": command, "searchFlags": ["DLL_LOAD_DIR", "USER_DIRS:FINAL/mod only", "SYSTEM32"], "cleanPath": env["PATH"],
             "runtime": result, "targets": TARGETS, "allowedStageImages": allowed,
             "actualMaxStarted": False, "browserStarted": False, "helperStarted": False, "hooksLoaded": False,
             "instancesCreated": False, "exportsInvoked": False, "profilesRead": False, "finalRuntimeUnchanged": True,
             "scope": "Actual Windows LoadLibraryExW of exact official Qt images/dependencies only. No Qt plugin instances, engines, navigation or MAX feature/lifecycle proof."}
    with args.report.open("x", encoding="utf8") as output: output.write(json.dumps(proof, indent=2) + "\n")
    print(json.dumps({"verified": True, "targetsLoaded": len(TARGETS), "loadedModules": len(loaded), "checks": result["result"]["checks"], "report": str(args.report)}))


if __name__ == "__main__": main()
