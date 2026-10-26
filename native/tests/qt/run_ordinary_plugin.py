"""Real ordinary plugin/core pair on copied Qt; no MAX, EOF host or user profile."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import uuid
import pefile

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location("ordinary_owned_job",Path(__file__).with_name("run_native_media_plugin.py"))
host=importlib.util.module_from_spec(spec);spec.loader.exec_module(host)
sha,require=host.sha,host.require
QT=Path("C:/ValMAXBuild/build/qt-probes/qt-max-runtime-2tq1c3sb")
FIXTURE_ROOTS=(Path("C:/ValMAXBuild/build"),Path("D:/ValMAXBuild/build"),Path("E:/ValMAXBuild/build"))


def owned_fixture_root(path):
    require(path.is_absolute() and path in FIXTURE_ROOTS, "explicit C/D/E fixture build root required")
    for candidate in (path, *path.parents):
        if candidate.exists():
            require(not candidate.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT,
                    "reparse fixture path")
    require(path.is_dir(), "existing fixture root required")
    return path.resolve()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-proof",type=Path,required=True)
    parser.add_argument("--generator-proof",type=Path,required=True)
    parser.add_argument("--tls-build",type=Path,required=True)
    parser.add_argument("--report",type=Path,required=True)
    parser.add_argument("--qt-runtime",type=Path,default=QT,
                        help="Existing copied Qt runtime; defaults to the original owned probe runtime")
    parser.add_argument("--fixture-root",type=Path,default=Path("D:/ValMAXBuild/build"),
                        help="Existing owned C:/ValMAXBuild/build, D:/ValMAXBuild/build or E:/ValMAXBuild/build; default preserves the D fixture location")
    args=parser.parse_args()
    fixture_root=owned_fixture_root(args.fixture_root)
    qt_runtime=args.qt_runtime
    require(qt_runtime.is_absolute() and qt_runtime.is_dir()
            and all((qt_runtime/name).is_dir() for name in ("plugins","qml"))
            and (qt_runtime/"qt.conf").is_file(),"existing copied Qt runtime required")
    require(args.report.parent.resolve()==(ROOT/"analysis/native-migration-v1").resolve() and not args.report.exists(),"new proof required")
    build=json.loads(args.build_proof.read_text("utf8")); directory=Path(build["qtBuild"]); hooks=Path(build["hookBuild"])
    require(build.get("compiled") is True and build.get("exportHistoryHookBuilt") is False
            and build.get("exportSelectionHostBuilt") is True,"ordinary selected-view/no-EOF build required")
    retention_built = build.get("retentionBuilt") is True
    if retention_built:
        pin_path = Path(build["retentionPinProof"])
        pins = json.loads(pin_path.read_text("utf8"))
        require(build["artifacts"].get(str(pin_path)) == sha(pin_path)
                and pins.get("schema") == 1 and pins.get("historySyncVerified") is False
                and pins.get("realProfilesAccessed") is False,
                "exact passive retention pins required")
    for name,digest in build["sourceHashes"].items(): require(sha(ROOT/name)==digest,"build source changed: "+name)
    for name,digest in build["artifacts"].items(): require(sha(name)==digest,"build artifact changed: "+name)
    generator=json.loads(args.generator_proof.read_text("utf8")); generated=Path(generator["fixture"])/"native/BaseSendPanel.qml"
    declaration=(ROOT/"native/qt/InlineMediaComposer.qml").read_text("utf8").replace("\r\n","\n")
    require(generated.read_text("utf8").count(declaration)==1,"generated composer declaration differs")
    fixture=fixture_root/("qt-ordinary-plugin-"+uuid.uuid4().hex); fixture.mkdir()
    for source in qt_runtime.glob("*.dll"):
        if source.name.lower().startswith(("qt6","msvcp","vcruntime","concrt","api-ms-","ucrt")): shutil.copy2(source,fixture/source.name)
    for name in ("plugins","qml"):
        shutil.copytree(qt_runtime/name,fixture/name,copy_function=shutil.copy2,ignore=lambda path,names:[name for name in names if name=="ValMAX"])
    shutil.copy2(qt_runtime/"qt.conf",fixture/"qt.conf")
    module=fixture/"qml/ValMAX/Native"
    shutil.copytree(directory/"qml/ValMAX/Native",module,copy_function=shutil.copy2)
    core=Path(build["coreInputs"])/"valmax_core.dll";shutil.copy2(core,fixture/core.name)
    for name in ("valmax_privacy.dll","valmax_ghost.dll","valmax_notifications.dll"): shutil.copy2(hooks/name,fixture/name)
    for source in hooks.glob("valmax-private-notification-*.png"): shutil.copy2(source,fixture/source.name)
    executables=["valmax_qml_bridge_test","valmax_all_features_test","valmax_privacy_host_test","valmax_privacy_delivery_test",
                 "valmax_protection_delivery_test","valmax_ghost_host_test","valmax_notification_host_test",
                 "valmax_export_selection_host_test"]
    if retention_built: executables.append("valmax_retention_host_bridge_test")
    for name in executables: shutil.copy2(directory/(name+".exe"),fixture/(name+".exe"))
    shutil.copy2(args.tls_build/"valmax_tls_probe.exe",fixture/"valmax_tls_probe.exe")
    wrapper='''import QtQuick
import ValMAX.Native 1.0 as ValMAXNative
Item {
 id: root
 property bool textInputVisible: true
 property var modQuickReplySelected: null
 function setText(value, entities) { chatInput.text=value; }
 Item { id: chatInput; objectName: "synthetic-chat-input"; property string text: "@pic fixture"; function moveCursorToEnd() {} }
'''+declaration+'\nComponent.onDestruction: modInlineReset(false)\n}\n'
    (fixture/"inline_native_fragment.qml").write_text(wrapper,"utf8")
    for name in ("home","local","roaming","temp","synthetic-profile","synthetic-runtime"): (fixture/name).mkdir()
    env={key:os.environ[key] for key in ("SystemRoot","WINDIR","SystemDrive","COMSPEC","PATHEXT") if key in os.environ}
    env.update(PATH=str(fixture)+os.pathsep+str(Path(os.environ["SystemRoot"])/"System32"),QT_QPA_PLATFORM="windows",QT_QUICK_BACKEND="software",QT_DEBUG_PLUGINS="1",
        QML_DISABLE_DISK_CACHE="1",QML_IMPORT_PATH=str(fixture/"qml"),QML2_IMPORT_PATH=str(fixture/"qml"),
        USERPROFILE=str(fixture/"home"),LOCALAPPDATA=str(fixture/"local"),APPDATA=str(fixture/"roaming"),TEMP=str(fixture/"temp"),TMP=str(fixture/"temp"))
    host.c.windll.kernel32.SetErrorMode(0x8003)
    dependencies=host.closure([module/"valmaxnativeplugin.dll",fixture/"plugins/platforms/qwindows.dll",fixture/"qml/QtQuick/qtquick2plugin.dll"],fixture)
    pe=pefile.PE(str(module/"valmaxnativeplugin.dll"))
    imports={entry.dll.decode("ascii").lower() for entry in pe.DIRECTORY_ENTRY_IMPORT}
    require({"valmax_core.dll","valmax_ghost.dll","valmax_notifications.dll","valmax_privacy.dll","qt6network.dll"}<=imports,"ordinary component import missing")
    requested={item.name.decode("ascii") for entry in pe.DIRECTORY_ENTRY_IMPORT if entry.dll.lower()==b"valmax_core.dll" for item in entry.imports if item.name}
    require({"valmax_runtime_export_history_bind","valmax_runtime_export_history_invalidate"}<=requested
            and "valmax_runtime_export_history_observe" not in requested,
            "partial selected-view host must bind and invalidate without EOF observation")
    core_pe=pefile.PE(str(fixture/"valmax_core.dll")); exported={entry.name.decode("ascii") for entry in core_pe.DIRECTORY_ENTRY_EXPORT.symbols if entry.name}
    require(requested<=exported,"unresolved Rust symbols")
    require({"valmax_export_download_open","valmax_export_download_write","valmax_export_download_close"}<=requested,"production network sink branch not linked")
    checks={}
    tls=host.run(fixture,fixture/"valmax_tls_probe.exe",[],env,"tls")
    tls_path=fixture/"plugins/tls/qschannelbackend.dll"
    require(tls_path.is_file() and Path(tls["result"]["loadedSchannel"]).resolve()==tls_path.resolve(),"TLS backend outside bundled fixture")
    require(sha(tls_path)==sha(qt_runtime/"plugins/tls/qschannelbackend.dll"),"TLS backend differs from copied runtime")
    for name in executables[2:]:
        arguments=[fixture/(name+"-synthetic")] if name in ("valmax_ghost_host_test","valmax_notification_host_test") else []
        checks[name]=host.run(fixture,fixture/(name+".exe"),arguments,env,name)
    source=Path(__file__).with_name("all_features.qml").read_text("utf8")
    marker='            test.check(Native.Bridge.servicesAvailable, "real core ABI loaded");'
    require(source.count(marker)==1,"ordinary lifecycle anchor changed")
    source=source.replace(marker,marker+'''
            test.check(typeof Native.TransferLauncher.confirmAndStartImport === "function" && !Native.TransferLauncher.available, "MData handoff registered but synthetic executable cannot launch a client");
            test.check(Native.MDataTransport.statusValue().exportAvailable === true, "native MData export transport retained");
            test.check(Native.Bridge.exportDownloader && Native.Bridge.exportDownloader.available && !Native.Bridge.exportDownloader.busy, "production export downloader bound to this synthetic runtime");
''',1)
    qml=fixture/"ordinary_lifecycle.qml"; qml.write_text(source,"utf8")
    config=json.dumps({"profileDirectory":str(fixture/"synthetic-profile"),"runtimeDirectory":str(fixture/"synthetic-runtime"),"existingClient":False})
    lifecycle=host.run(fixture,fixture/"valmax_all_features_test.exe",[fixture/"qml",qml],{**env,"VALMAX_NATIVE_RUNTIME_CONFIG":config},"ordinary_lifecycle")
    require(len(lifecycle["result"]["cycles"])==3 and lifecycle["result"]["mediaEngineChecks"]==44 and lifecycle["result"]["mediaComposerChecks"]==51,"missing engine lifecycle coverage")
    for name,path in lifecycle["result"]["loadedModules"].items():
        expected=module/name if name=="valmaxnativeplugin.dll" else fixture/name
        require(Path(path).resolve()==expected.resolve() and sha(path)==sha(expected),"unexpected loaded module")
    negatives={}
    for label,value,state in (("unconfigured",None,"unconfigured"),("malformed","{","error")):
        qml=fixture/(label+".qml"); qml.write_text(Path(__file__).with_name("settings_unavailable.qml").read_text().replace("@STATE@",state),"utf8")
        case_env=dict(env)
        if value is not None: case_env["VALMAX_NATIVE_RUNTIME_CONFIG"]=value
        negatives[label]=host.run(fixture,fixture/"valmax_qml_bridge_test.exe",[fixture/"qml",qml],case_env,label)
    bridge_qml=fixture/"bridge.qml"; shutil.copy2(Path(__file__).with_name("bridge.qml"),bridge_qml)
    bridge=host.run(fixture,fixture/"valmax_qml_bridge_test.exe",[fixture/"qml",bridge_qml],env,"bridge")
    for name,digest in build["sourceHashes"].items(): require(sha(ROOT/name)==digest,"source changed during runtime proof")
    for name,digest in build["artifacts"].items(): require(sha(name)==digest,"artifact changed during runtime proof")
    total=sum(value["result"]["checks"] for value in checks.values())+sum(value["checks"] for value in lifecycle["result"]["cycles"])
    total+=sum(value["result"]["checks"] for value in negatives.values())+bridge["result"]["checks"]+tls["result"]["checks"]+44+51
    proof={"schema":1,"verified":True,"checks":total,"fixture":str(fixture),"buildProof":str(args.build_proof),"buildProofSha256":sha(args.build_proof),
        "generatorProof":str(args.generator_proof),"generatorProofSha256":sha(args.generator_proof),"pluginSha256":sha(module/"valmaxnativeplugin.dll"),
        "coreSha256":sha(fixture/"valmax_core.dll"),"corePairProof":build["corePairProof"],"corePairProofSha256":build["corePairProofSha256"],
        "imports":sorted(imports),"coreImportSymbols":sorted(requested),"coreSymbolsResolved":True,"dependencyClosure":dependencies,
        "hostChecks":checks,"lifecycle":lifecycle,"negativeConfiguration":negatives,"bridge":bridge,"tls":tls,
        "tlsProbeSources":{str(path.relative_to(ROOT)):sha(path) for path in (ROOT/"native/tests/qt/tls_probe").iterdir() if path.is_file()},
        "tlsProbeExecutableSha256":sha(args.tls_build/"valmax_tls_probe.exe"),"bundledSchannelSha256":sha(tls_path),
        "runtimeFiles":{path.relative_to(fixture).as_posix():sha(path) for path in fixture.rglob("*") if path.is_file() and path.suffix in (".dll",".exe",".qml",".conf")},
        "runnerSha256":sha(__file__),"exportHistoryHookBuilt":False,"exportHistoryHookTested":False,
        "exportSelectionHostBuilt":True,"exportSelectionHostTested":True,"retentionBuilt":retention_built,
        "retentionHostBridgeTested":retention_built,
        "actualMaxStarted":False,"installedClientChanged":False,"realCredentialsUsed":False,"networkRequestsSent":False,
        "scope":"Exact ordinary plugin/core linkage and copied Qt dependency closure; typed selected-view BETA host without EOF observations, repeated QQmlEngine lifetime, real MediaComposer registration, MData handoff registration/export status, real network-sink imports. No production MAX, private profiles, history EOF hook, server or full-installer proof."}
    with args.report.open("x",encoding="utf8") as stream: stream.write(json.dumps(proof,indent=2)+"\n")
    print(json.dumps({"verified":True,"checks":total,"pluginSha256":proof["pluginSha256"],"coreSha256":proof["coreSha256"],"report":str(args.report)}))


if __name__=="__main__": main()
