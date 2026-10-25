"""One combined production plugin in standalone MAX Qt; no MAX/profile launch."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import pefile

ROOT=Path(__file__).resolve().parents[3]
BUILD=Path("C:/ValMAXBuild/build/qt-all-features")
HOOKS=Path("C:/ValMAXBuild/build/hooks-all-features")
INPUTS=Path("C:/ValMAXBuild/build/all-feature-inputs")
SOURCE=Path("C:/ValMAXBuild/build/qt-probes/qt-max-runtime-2tq1c3sb")
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def require(value,message):
    if not value:raise RuntimeError(message)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--build-proof",type=Path,default=ROOT/"analysis/native-migration-v1/qt-all-features-build.json")
    parser.add_argument("--core-inputs",type=Path)
    parser.add_argument("--output-proof",type=Path,default=ROOT/"analysis/native-migration-v1/qt-all-features-lifecycle-proof.json")
    parser.add_argument("--fixture-label",default="qt-all-features-")
    args=parser.parse_args()
    ctypes.windll.kernel32.SetErrorMode(0x8003)
    build=json.loads(args.build_proof.read_text(encoding="utf-8-sig"))
    build_directory=Path(build["qtBuild"])
    hooks_directory=Path(build["hookBuild"])
    require(not args.output_proof.exists(),"output_proof_already_exists")
    for path,digest in build["artifacts"].items():require(sha(Path(path))==digest,"build_artifact_changed:"+Path(path).name)
    for path,digest in build.get("sourceHashes",{}).items():require(sha(ROOT/path)==digest,"build_source_changed:"+path)
    built_core=[Path(path) for path in build["artifacts"] if Path(path).name=="valmax_core.dll"]
    require(len(built_core)==1,"ambiguous_build_core")
    if args.core_inputs is None:args.core_inputs=built_core[0].parent
    replacement=args.core_inputs.resolve()!=built_core[0].parent.resolve()
    if replacement:
        require(args.output_proof.resolve()!=(ROOT/"analysis/native-migration-v1/qt-all-features-lifecycle-proof.json").resolve(),"replacement_core_requires_separate_proof")
        require(not args.output_proof.exists(),"replacement_proof_already_exists")
    require(args.fixture_label.startswith("qt-all-features-") and all(c.isalnum() or c=="-" for c in args.fixture_label),"invalid_fixture_label")
    pair=json.loads((args.core_inputs/"core-pair-proof.json").read_text(encoding="utf8"))
    require(pair.get("verified") is True and pair.get("sourceSnapshotMatches") is True and pair.get("importLibrarySymbolsMatch") is True,"unverified_core_pair")
    require(sha(Path(pair["sourceProof"]))==pair["sourceProofSha256"],"source_core_proof_changed")
    require(sha(args.core_inputs/"valmax_core.dll")==pair["hashes"]["dll"] and sha(args.core_inputs/"valmax_core.dll.lib")==pair["hashes"]["importLibrary"],"core_pair_changed")
    fixture=Path(tempfile.mkdtemp(prefix=args.fixture_label,dir="C:/ValMAXBuild/build"))
    for file in SOURCE.glob("*.dll"):
        if file.name.lower().startswith(("qt6","msvcp","vcruntime","concrt","api-ms-","ucrt")):
            os.link(file,fixture/file.name)
    for directory in ("plugins","qml"):
        shutil.copytree(SOURCE/directory,fixture/directory,copy_function=os.link,
            ignore=lambda path,names:[name for name in names if name=="ValMAX"])
    shutil.copy2(SOURCE/"qt.conf",fixture/"qt.conf")
    module=fixture/"qml/ValMAX/Native";module.mkdir(parents=True)
    for name in ("qmldir","SettingsTransport.qml","ChatLockTransport.qml","SupportTransport.qml","valmaxnativeplugin.dll"):
        shutil.copy2(build_directory/"qml/ValMAX/Native"/name,module/name)
    shutil.copy2(args.core_inputs/"valmax_core.dll",fixture/"valmax_core.dll")
    for name in ("valmax_ghost.dll","valmax_notifications.dll","valmax_privacy.dll"):
        shutil.copy2(hooks_directory/name,fixture/name)
    for asset in hooks_directory.glob("valmax-private-notification-*.png"):shutil.copy2(asset,fixture/asset.name)
    pe=pefile.PE(str(module/"valmaxnativeplugin.dll"))
    imports={entry.dll.decode().lower() for entry in pe.DIRECTORY_ENTRY_IMPORT}
    require({"valmax_core.dll","valmax_ghost.dll","valmax_notifications.dll","valmax_privacy.dll"}<=imports,"partial_feature_imports")
    require(not any(name.startswith(("python","frida-agent")) for name in imports),"unexpected_runtime_dependency")
    core=pefile.PE(str(fixture/"valmax_core.dll"))
    core_exports={entry.name.decode("ascii") for entry in core.DIRECTORY_ENTRY_EXPORT.symbols if entry.name}
    core_imports={item.name.decode("ascii") for entry in pe.DIRECTORY_ENTRY_IMPORT if entry.dll.lower()==b"valmax_core.dll" for item in entry.imports if item.name}
    require(core_imports<=core_exports,"core_symbols_missing")
    env={key:value for key,value in os.environ.items() if not key.upper().startswith(("QT_","QML","WEBVIEW2_","PYTHON","MAX_MOD","MXMOD","VALMAX_NATIVE"))}
    for name in ("home","local","roaming","temp","synthetic-profile","synthetic-runtime"):(fixture/name).mkdir()
    env.update(PATH=str(fixture)+os.pathsep+os.path.join(os.environ["SystemRoot"],"System32"),
        QT_QPA_PLATFORM="windows",QT_QUICK_BACKEND="software",QT_DEBUG_PLUGINS="1",QML_DISABLE_DISK_CACHE="1",
        QML_IMPORT_PATH=str(fixture/"qml"),QML2_IMPORT_PATH=str(fixture/"qml"),
        USERPROFILE=str(fixture/"home"),LOCALAPPDATA=str(fixture/"local"),APPDATA=str(fixture/"roaming"),TEMP=str(fixture/"temp"),TMP=str(fixture/"temp"))
    def run(name,args=(),environment=None):
        exe=fixture/(name+".exe");shutil.copy2(build_directory/exe.name,exe)
        result=subprocess.run([str(exe),*map(str,args)],cwd=fixture,env=env if environment is None else environment,
            capture_output=True,text=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
        (fixture/(name+".stderr.log")).write_text(result.stderr,encoding="utf8")
        if result.returncode:print(result.stdout);print(result.stderr[-6000:]);raise RuntimeError("all_feature_test_failed:"+name)
        value=json.loads(result.stdout);require(value.get("ok") is True,"missing_test_success")
        return value
    hosts={}
    for name in ("valmax_privacy_host_test","valmax_privacy_delivery_test","valmax_protection_delivery_test","valmax_ghost_host_test","valmax_notification_host_test"):
        case_args=[fixture/(name+"-synthetic")] if name in ("valmax_ghost_host_test","valmax_notification_host_test") else []
        hosts[name]=run(name,case_args)
    config=json.dumps({"profileDirectory":str(fixture/"synthetic-profile"),"runtimeDirectory":str(fixture/"synthetic-runtime"),"existingClient":False})
    qml=fixture/"all_features.qml";shutil.copy2(Path(__file__).with_name("all_features.qml"),qml)
    lifecycle=run("valmax_all_features_test",[fixture/"qml",qml],{**env,"VALMAX_NATIVE_RUNTIME_CONFIG":config})
    require(len(lifecycle["cycles"])==3 and all(item["checks"]>=20 for item in lifecycle["cycles"]),"lifecycle_cycles_missing")
    if build.get("mediaEngineContextLinked"):
        require(lifecycle.get("mediaEngineChecks")==44,"media_engine_lifecycle_missing")
    for name,path in lifecycle["loadedModules"].items():
        expected=module/name if name=="valmaxnativeplugin.dll" else fixture/name
        require(Path(path).resolve()==expected.resolve(),"unexpected_loaded_native_path")
    negative=[]
    for label,config_value,state in (("unconfigured",None,"unconfigured"),("malformed","{","error")):
        qml=fixture/(label+".qml")
        qml.write_text(Path(__file__).with_name("settings_unavailable.qml").read_text().replace("@STATE@",state))
        case_env=dict(env)
        if config_value is not None:case_env["VALMAX_NATIVE_RUNTIME_CONFIG"]=config_value
        result=run("valmax_qml_bridge_test",[fixture/"qml",qml],case_env)
        negative.append({"case":label,"checks":result["checks"],"ok":True})
    proof={"schema":1,"verified":True,"fixture":str(fixture),"allFeatureImports":sorted(imports),
        "hostChecks":hosts,"lifecycle":lifecycle,"negativeConfiguration":negative,
        "hashes":{name:sha(fixture/name) for name in ("Qt6Core.dll","Qt6Qml.dll","valmax_core.dll","valmax_ghost.dll","valmax_notifications.dll","valmax_privacy.dll")},
        "pluginSha256":sha(module/"valmaxnativeplugin.dll"),"buildProof":str(args.build_proof.resolve()),"buildProofSha256":sha(args.build_proof),
        "corePairProof":str((args.core_inputs/"core-pair-proof.json").resolve()),"corePairProofSha256":sha(args.core_inputs/"core-pair-proof.json"),
        "coreReplacementValidation":replacement,"coreImportSymbols":sorted(core_imports),"coreSymbolsResolved":True,
        "mediaEngineContextLinked":build.get("mediaEngineContextLinked",False),
        "earlyBootstrapGateVerified":False,"actualMaxStarted":False,"installedClientChanged":False,"retentionBuilt":False,
        "scope":"Standalone combined DLL load, fail-closed unsupported hook host, bounded worker teardown and repeated QQmlEngine lifetime; no positive MAX install/early-bootstrap claim."}
    for path,digest in build.get("sourceHashes",{}).items():require(sha(ROOT/path)==digest,"source_changed_during_test:"+path)
    args.output_proof.write_text(json.dumps(proof,indent=2))
    total=sum(item["checks"] for item in hosts.values())+sum(item["checks"] for item in lifecycle["cycles"])+sum(item["checks"] for item in negative)+lifecycle.get("mediaEngineChecks",0)
    print(json.dumps({"verified":True,"checks":total,"proof":str(args.output_proof),"fixture":str(fixture),"pluginSha256":proof["pluginSha256"]}))
if __name__=="__main__":main()
