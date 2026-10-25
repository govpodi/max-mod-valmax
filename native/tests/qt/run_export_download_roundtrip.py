"""Real C++ HTTP worker -> frozen Rust sink -> selected synthetic archive."""
from __future__ import annotations
import argparse
import ctypes as c
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import uuid
import pefile

ROOT=Path(__file__).resolve().parents[3]
PAIR=Path("D:/ValMAXBuild/build/core-export-download-inputs-f6a16a78bf25110aaf1b41de122f6a299b901f7b9a005502084b80b6dc1e62de")

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build",type=Path,default=Path("D:/ValMAXBuild/build/export-download-roundtrip-real-core"))
    parser.add_argument("--qt-runtime",type=Path,default=Path("C:/ValMAXBuild/build/qt-support-0gb8v6n6"))
    parser.add_argument("--report",type=Path,default=ROOT/"analysis/native-migration-v1/export-download-roundtrip-proof.json")
    args=parser.parse_args()
    spec=importlib.util.spec_from_file_location("export_download_owned_job",Path(__file__).with_name("run_native_media_plugin.py"))
    job=importlib.util.module_from_spec(spec);spec.loader.exec_module(job);c.windll.kernel32.SetErrorMode(0x8003)
    pair=json.loads((PAIR/"core-pair-proof.json").read_text(encoding="utf8"))
    job.require(pair["verified"] and pair["hashes"]["dll"]==sha(PAIR/"valmax_core.dll"),"unverified Rust pair")
    fixture=Path("D:/ValMAXBuild/build")/("export-download-roundtrip-runtime-"+uuid.uuid4().hex);fixture.mkdir()
    exe=args.build/"valmax_export_download_roundtrip.exe";job.require(exe.is_file(),"missing compiled roundtrip executable")
    shutil.copy2(exe,fixture/exe.name)
    copied={}
    for path in args.qt_runtime.glob("*.dll"):
        if path.name.lower() in ("qt6core.dll","qt6network.dll") or path.name.lower().startswith(("msvcp","vcruntime","concrt","api-ms-","ucrtbase")):
            shutil.copy2(path,fixture/path.name);copied[path.name] = {"source":str(path),"sha256":sha(path)}
    shutil.copy2(PAIR/"valmax_core.dll",fixture/"valmax_core.dll")
    for name in ("home","local","roaming","temp","plugins"):(fixture/name).mkdir()
    base=Path("D:/ValMAXBuild/fixtures/export-download-roundtrip");base.mkdir(parents=True,exist_ok=True)
    selected=base/uuid.uuid4().hex;job.require(not selected.exists(),"synthetic export target already exists")
    paths=["native/qt/export_downloader.cpp","native/qt/export_downloader.h","native/core/valmax_core.h",
        "native/tests/qt/export_download_roundtrip/CMakeLists.txt","native/tests/qt/export_download_roundtrip_test.cpp",
        "native/tests/qt/run_export_download_roundtrip.py"]
    sources={name:sha(ROOT/name) for name in paths}
    env={key:os.environ[key] for key in ("SystemRoot","WINDIR","SystemDrive","COMSPEC","PATHEXT") if key in os.environ}
    env.update(PATH=str(fixture)+os.pathsep+str(Path(os.environ["SystemRoot"])/"System32"),
        USERPROFILE=str(fixture/"home"),LOCALAPPDATA=str(fixture/"local"),APPDATA=str(fixture/"roaming"),
        TEMP=str(fixture/"temp"),TMP=str(fixture/"temp"),QT_PLUGIN_PATH=str(fixture/"plugins"))
    result=job.run(fixture,fixture/exe.name,[selected],env,"roundtrip")
    value=result["result"];job.require(value["realRustSink"] and value["syntheticLoopbackOnly"] and value["requests"]==3,"unexpected test scope")
    job.require(sources=={name:sha(ROOT/name) for name in paths},"sources changed during test")
    binary=pefile.PE(str(fixture/exe.name));imports=[entry.dll.decode("ascii").lower() for entry in binary.DIRECTORY_ENTRY_IMPORT]
    job.require("valmax_core.dll" in imports and "qt6network.dll" in imports,"roundtrip must import the real native libraries")
    proof={"schema":1,"verified":True,"fixture":str(fixture),"syntheticExportRoot":str(selected),"result":result,
        "sourceHashes":sources,"executableSha256":sha(fixture/exe.name),"realCoreSha256":sha(fixture/"valmax_core.dll"),
        "corePairProof":str(PAIR/"core-pair-proof.json"),"corePairProofSha256":sha(PAIR/"core-pair-proof.json"),
        "qtRuntimeFiles":copied,"dependencyClosure":job.closure([fixture/exe.name],fixture),
        "realProfilesRead":False,"windowsCredentialsUsed":False,"installedClientStarted":False,
        "externalNetworkUsed":False,"localHttpFixtureOnly":True,"eofHookUsed":False,"fullChatExportVerified":False,
        "scope":"Actual C++ HTTP success/404/cancellation through the frozen real Rust opaque sink, closed writer before terminal ACK, verified archive bytes, prior archive preserved; synthetic selected chat only"}
    args.report.write_text(json.dumps(proof,indent=2)+"\n",encoding="utf8")
    print(json.dumps({"verified":True,"checks":value["checks"],"proof":str(args.report)}))

if __name__=="__main__":main()
