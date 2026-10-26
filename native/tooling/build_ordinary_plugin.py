"""Compile the ordinary Qt plugin with a frozen Rust pair and partial export selection."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[2]
HOOKS = Path("C:/ValMAXBuild/build/hooks-all-features")
BUILD_ROOTS = (Path("C:/ValMAXBuild/build"), Path("D:/ValMAXBuild/build"), Path("E:/ValMAXBuild/build"))
TARGETS = ("valmaxnativeplugin", "valmax_qml_bridge_test", "valmax_all_features_test",
           "valmax_privacy_host_test", "valmax_privacy_delivery_test", "valmax_protection_delivery_test",
           "valmax_ghost_host_test", "valmax_notification_host_test", "valmax_export_selection_host_test")


def require(value, message):
    if not value: raise RuntimeError(message)


def sha(path):
    with Path(path).open("rb") as stream: return hashlib.file_digest(stream, "sha256").hexdigest()


def owned_build_path(path):
    require(path.is_absolute()
            and all(part not in (".", "..") and not part.endswith((".", " ")) for part in path.parts[1:])
            and any(path != root and path.is_relative_to(root) for root in BUILD_ROOTS),
            "explicit owned C/D/E build path required")
    for candidate in (path, *path.parents):
        if candidate.exists():
            require(not candidate.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT,
                    "reparse build path")
    return path.resolve()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build",type=Path,required=True)
    parser.add_argument("--core-inputs",type=Path,required=True)
    parser.add_argument("--hooks", type=Path, default=HOOKS,
                        help="Existing guard DLL/lib build; production assembly separately verifies exact target pins")
    parser.add_argument("--retention-runtime-root", type=Path,
                        help="Exact owned MAX core/storage image pair for the passive retention listener")
    parser.add_argument("--report",type=Path,required=True)
    args=parser.parse_args()
    hooks = owned_build_path(args.hooks)
    require(hooks.is_dir(), "existing owned guard build required")
    owned_build_path(args.build)
    require(not args.build.exists(),"new owned build required")
    require(args.report.parent.resolve()==(ROOT/"analysis/native-migration-v1").resolve() and not args.report.exists(),"new proof required")
    retention_root = owned_build_path(args.retention_runtime_root) if args.retention_runtime_root else None
    if retention_root:
        require(retention_root.is_dir() and all((retention_root/name).is_file()
                for name in ("core.dll", "storage.dll")), "exact retention image pair required")
    pair_path=args.core_inputs/"core-pair-proof.json"
    pair=json.loads(pair_path.read_text("utf8"))
    require(pair.get("verified") is True and pair.get("sourceSnapshotMatches") is True
            and pair.get("importLibrarySymbolsMatch") is True,"unverified core pair")
    dll,args_lib=args.core_inputs/"valmax_core.dll",args.core_inputs/"valmax_core.dll.lib"
    require(sha(dll)==pair["hashes"]["dll"] and sha(args_lib)==pair["hashes"]["importLibrary"],"core pair changed")
    require(sha(pair["sourceProof"])==pair["sourceProofSha256"],"core source proof changed")
    sources={path for folder in (ROOT/"native/qt",ROOT/"native/media") for path in folder.rglob("*")
             if path.is_file() and path.suffix in (".cpp",".h",".qml",".cmake")
             and not path.name.startswith("export_history")}
    sources.update(ROOT/name for name in ("native/qt/CMakeLists.txt","native/media/CMakeLists.txt","native/qt/export_history.cmake",
                                        "native/qt/qmldir","native/core/valmax_core.h",
                                        "native/common/window_activation.h"))
    sources.update(ROOT/"native/tests/qt"/(name+".cpp") for name in
                   ("bridge_harness","all_features_harness","privacy_host_test","privacy_delivery_test","protection_delivery_test","ghost_host_test","notification_host_test"))
    sources.add(ROOT/"native/tests/qt/export_selection_host_test.cpp")
    if retention_root:
        sources.update(ROOT/name for name in (
            "native/hooks/retention_passive_host.cpp", "native/hooks/retention_passive_host.h",
            "native/hooks/retention_async_worker.cpp", "native/hooks/retention_async_worker.h",
            "native/hooks/retention_row_codec.cpp", "native/hooks/retention_row_codec.h",
            "native/hooks/retention_generate_pins.py", "native/hooks/ghost_generate_pins.py",
            "native/hooks/ghost_module.cpp", "native/hooks/ghost_internal.h",
            "native/hooks/hook_set.cpp", "native/hooks/hook_set.h",
            "modkit/message_retention_native.py",
            "modkit/message_retention_monitor.js"))
        sources.add(ROOT/"native/tests/qt/retention_host_bridge_test.cpp")
    sources.add(Path(__file__).resolve())
    source_hashes={str(path.relative_to(ROOT)).replace("\\","/"):sha(path) for path in sorted(sources)}
    inputs={str(path):sha(path) for path in (dll,args_lib,pair_path,hooks/"valmax_privacy.lib",hooks/"valmax_ghost.lib",hooks/"valmax_notifications.lib",
                                          hooks/"valmax_privacy.dll",hooks/"valmax_ghost.dll",hooks/"valmax_notifications.dll",
                                          hooks/"privacy-pins.json", hooks/"privacy_pins.generated.h",
                                          hooks/"ghost-pins.json", hooks/"ghost_pins.generated.h",
                                          hooks/"notification-pins.json", hooks/"notification_pins.generated.h")}
    if retention_root:
        inputs.update({str(retention_root/name):sha(retention_root/name)
                       for name in ("core.dll", "storage.dll")})
    targets = TARGETS + (("valmax_retention_host_bridge_test",) if retention_root else ())
    commands={"configure":["cmake","-S",str(ROOT/"native/qt"),"-B",str(args.build),"-G","Ninja","-DCMAKE_BUILD_TYPE=Release",
        "-DCMAKE_PREFIX_PATH=C:/ValMAXBuild/Qt","-DVALMAX_REQUIRE_ALL_FEATURES=ON","-DVALMAX_EXPORT_HISTORY_RUNTIME_ROOT=",
        "-DVALMAX_RETENTION_RUNTIME_ROOT="+(str(retention_root) if retention_root else ""),
        "-DVALMAX_CORE_LIBRARY="+str(args_lib),"-DVALMAX_CORE_PIN_SHA256="+pair["hashes"]["dll"],"-DVALMAX_CORE_PIN_BYTES="+str(dll.stat().st_size),
        "-DVALMAX_PRIVACY_LIBRARY="+str(hooks/"valmax_privacy.lib"),"-DVALMAX_GHOST_LIBRARY="+str(hooks/"valmax_ghost.lib"),
        "-DVALMAX_NOTIFICATION_LIBRARY="+str(hooks/"valmax_notifications.lib")],
        "build":["cmake","--build",str(args.build),"--target",*targets,"-j","1"]}
    args.build.mkdir(parents=True)
    for label,command in commands.items():
        run=subprocess.run(command,capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=1200)
        (args.build/(label+".log")).write_text(run.stdout+run.stderr,encoding="utf8")
        if run.returncode: print((run.stdout+run.stderr)[-14000:]); raise RuntimeError(label+" failed")
    require(all(sha(ROOT/name)==digest for name,digest in source_hashes.items()),"sources changed during ordinary plugin build")
    require(all(sha(path)==digest for path,digest in inputs.items()),"frozen build input changed")
    cache=(args.build/"CMakeCache.txt").read_text("utf8")
    require("VALMAX_EXPORT_HISTORY_RUNTIME_ROOT:PATH=\n" in cache,"export EOF option was enabled")
    configured_retention = "VALMAX_RETENTION_RUNTIME_ROOT:PATH=" + (str(retention_root) if retention_root else "")
    require(configured_retention.replace("\\", "/").lower() in cache.replace("\\", "/").lower(),
            "retention runtime root mismatch")
    artifacts={str(args.build/(name+".exe")):sha(args.build/(name+".exe")) for name in targets if name!="valmaxnativeplugin"}
    plugin=args.build/"qml/ValMAX/Native/valmaxnativeplugin.dll"; artifacts[str(plugin)]=sha(plugin)
    artifacts.update({path:digest for path,digest in inputs.items() if Path(path).suffix==".dll"})
    retention_pins = None
    if retention_root:
        retention_pins = args.build/"retention-pins.json"
        header = args.build/"retention_pins.generated.h"
        pins = json.loads(retention_pins.read_text("utf8"))
        require(pins.get("schema") == 1 and pins.get("historySyncVerified") is False
                and pins.get("realProfilesAccessed") is False
                and pins.get("runtimeRoot") == str(retention_root)
                and pins.get("headerSha256") == sha(header)
                and all(pins["images"][name]["sha256"] == sha(retention_root/name)
                        for name in ("core.dll", "storage.dll")), "retention pin proof mismatch")
        artifacts[str(retention_pins)] = sha(retention_pins)
        artifacts[str(header)] = sha(header)
    proof={"schema":1,"compiled":True,"qtBuild":str(args.build),"hookBuild":str(hooks),"coreInputs":str(args.core_inputs),
           "corePairProof":str(pair_path),"corePairProofSha256":sha(pair_path),"coreSha256":sha(dll),"coreBytes":dll.stat().st_size,
           "sourceHashes":source_hashes,"inputHashes":inputs,"artifacts":artifacts,"commands":commands,
           "buildGlue":{str(args.build/name):sha(args.build/name) for name in ("CMakeCache.txt","build.ninja","configure.log","build.log")},
           "mediaEngineContextLinked":True,"exportHistoryHookBuilt":False,
           "exportSelectionHostBuilt":True,"retentionBuilt":bool(retention_root),
           "retentionPinProof":str(retention_pins) if retention_pins else None,
           "installedClientChanged":False,"actualMaxStarted":False,
           "scope":"Ordinary Qt/C++ plugin with core/privacy/ghost/notifications/media/MData handoff/network export. A typed selected-view host binds BETA API2 jobs without EOF observations. Export EOF hook remains disabled."}
    with args.report.open("x",encoding="utf8") as stream: stream.write(json.dumps(proof,indent=2)+"\n")
    print(json.dumps({"compiled":True,"pluginSha256":sha(plugin),"coreSha256":sha(dll),"report":str(args.report),"exportHistoryHookBuilt":False,"exportSelectionHostBuilt":True,"retentionBuilt":bool(retention_root)}))


if __name__=="__main__": main()
