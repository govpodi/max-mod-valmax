"""Native handoff with owned fake launchers and generated QML logic; no MAX/profile."""
from __future__ import annotations
import argparse
import ctypes as c
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("mdata_owned_job", Path(__file__).with_name("run_native_media_plugin.py"))
host = importlib.util.module_from_spec(spec); spec.loader.exec_module(host)
job, require, sha = host.job, host.require, host.sha


def run_child_test(fixture, executable, arguments, env):
    """Two-process job: our test plus its one exact synthetic launcher."""
    block = c.create_unicode_buffer("\0".join(f"{key}={value}" for key,value in sorted(env.items())) + "\0\0")
    command = c.create_unicode_buffer(subprocess.list2cmdline([str(executable), *map(str,arguments)]))
    process=job.PROCESS(); group=None; outputs=[]; assigned=False; remaining=None; code=job.w.DWORD(259); failure=None
    try:
        group=job.create_job(None,None); require(group,"job creation failed")
        limits=job.LIMITS(); limits.basic.flags=0x2008; limits.basic.processes=2
        require(job.set_job(group,9,c.byref(limits),c.sizeof(limits)),"job limit failed")
        security=job.SA(c.sizeof(job.SA),None,True)
        for path in ("NUL",str(fixture/"native.stdout.log"),str(fixture/"native.stderr.log")):
            handle=job.create_file(path,0x80000000 if path=="NUL" else 0x40000000,1,c.byref(security),3 if path=="NUL" else 2,0x80,None)
            require(handle and handle!=c.c_void_p(-1).value,"log creation failed"); outputs.append(handle)
        startup=job.STARTUP(); startup.cb=c.sizeof(startup); startup.flags=0x101; startup.show=0
        startup.stdin,startup.stdout,startup.stderr=outputs
        require(job.create_process(str(executable),command,None,None,True,0x08000404,block,str(fixture),c.byref(startup),c.byref(process)),"test creation failed")
        actual=c.create_unicode_buffer(32768); length=job.w.DWORD(32768)
        require(job.query_path(process.process,0,actual,c.byref(length)) and Path(actual.value).resolve()==executable.resolve(),"unexpected executable")
        require(job.assign(group,process.process),"job assignment failed"); assigned=True
        require(job.resume(process.thread)!=0xffffffff,"resume failed")
        require(job.wait(process.process,10000)==0,"test timeout")
        require(job.exit_code(process.process,c.byref(code)),"exit code failed")
    except Exception as error: failure=str(error)
    finally:
        if group:
            job.terminate_job(group,91)
            for _ in range(50):
                pids=job.PIDS()
                if job.query_job(group,3,c.byref(pids),c.sizeof(pids),None):
                    remaining=pids.listed
                    if remaining==0: break
                time.sleep(.02)
        if process.process and not assigned: job.bind("TerminateProcess",job.w.BOOL,[job.w.HANDLE,job.w.UINT])(process.process,91)
        for handle in (process.thread,process.process,*outputs,group):
            if handle: job.close(handle)
    require(failure is None and code.value==0 and remaining==0,"native test failed; inspect owned logs: "+str(fixture))
    result=json.loads((fixture/"native.stdout.log").read_text("utf8")); require(result.get("ok") is True,"missing native result")
    return {"result":result,"exitCode":code.value,"remainingProcesses":remaining,"activeProcessLimit":2}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build",type=Path,required=True)
    parser.add_argument("--runtime",type=Path,default=Path("C:/ValMAXBuild/build/qt-support-0gb8v6n6"))
    parser.add_argument("--report",type=Path,required=True)
    args=parser.parse_args()
    require(args.report.parent.resolve()==(ROOT/"analysis/native-migration-v1").resolve() and not args.report.exists(),"new proof required")
    require(args.build.resolve().is_relative_to(Path("D:/ValMAXBuild/build").resolve()),"build outside owned root")
    fixture=Path("D:/ValMAXBuild/build")/("qt-mdata-launch-"+uuid.uuid4().hex); fixture.mkdir()
    bundle=Path("D:/ValMAXBuild/packages/valmax-transfer-7e25ee08429655dfd26211eafaef976ca8fff471fab3e0bd8168a99c7c41ed80")
    for name in ("Qt6Core.dll","msvcp140.dll","msvcp140_1.dll","msvcp140_2.dll","vcruntime140.dll","vcruntime140_1.dll"):
        shutil.copy2(bundle/name,fixture/name)
    for name in ("valmax_transfer_launch_test.exe","valmax_transfer_fake_launcher.exe"):
        shutil.copy2(args.build/name,fixture/name)
    for name in ("home","local","roaming","temp"): (fixture/name).mkdir()
    env={key:os.environ[key] for key in ("SystemRoot","WINDIR","SystemDrive","COMSPEC","PATHEXT") if key in os.environ}
    env.update(PATH=str(fixture)+os.pathsep+str(Path(os.environ["SystemRoot"])/"System32"),USERPROFILE=str(fixture/"home"),LOCALAPPDATA=str(fixture/"local"),APPDATA=str(fixture/"roaming"),TEMP=str(fixture/"temp"),TMP=str(fixture/"temp"))
    paths=["native/qt/transfer_launch.cpp","native/qt/transfer_launch.h","native/qt/MDataTransferEntry.qml","native/qt/MDataTransport.qml",
           "native/tests/qt/transfer_launch_test.cpp","native/tests/qt/transfer_fake_launcher.cpp","native/tests/qt/mdata_handoff.qml","native/tests/qt/run_mdata_handoff.py"]
    sources={name:sha(ROOT/name) for name in paths}
    native=run_child_test(fixture,fixture/"valmax_transfer_launch_test.exe",[fixture,fixture/"valmax_transfer_fake_launcher.exe"],env)
    module=fixture/"qml/ValMAX/Native"; module.mkdir(parents=True)
    (module/"qmldir").write_text("module ValMAX.Native\nsingleton TransferLauncher 1.0 TransferLauncher.qml\nMDataTransferEntry 1.0 MDataTransferEntry.qml\n")
    shutil.copy2(ROOT/"native/qt/MDataTransferEntry.qml",module/"MDataTransferEntry.qml")
    (module/"TransferLauncher.qml").write_text('''pragma Singleton
import QtQml
QtObject {
 property bool available: true
 property bool busy: false
 property string state: "idle"
 property int starts: 0
 property int quits: 0
 signal handoffFinished(bool helperLaunched, string error)
 function confirmAndStartImport() { ++starts; busy=true; state="launching"; return true; }
 function completeHandoff() { if(state!=="helper_launched") return false; ++quits; return true; }
 function finish(ok,error) { busy=false; state=ok?"helper_launched":"error"; handoffFinished(ok,error); if(ok) completeHandoff(); }
}
''')
    ui=fixture/"qml/UiKitModule"; ui.mkdir()
    (ui/"qmldir").write_text("module UiKitModule\nsingleton DesignSystem 1.0 DesignSystem.qml\nBaseModalDialog 1.0 BaseModalDialog.qml\n")
    (ui/"DesignSystem.qml").write_text('pragma Singleton\nimport QtQml\nQtObject { enum SpacingCommon { _L = 16 } }\n')
    (ui/"BaseModalDialog.qml").write_text('''import QtQuick
Item {
 property var chatAppearance
 property real minimumWidth
 property real margins
 property string title
 property string subtitle
 property string confirmText
 property string cancelText
 property bool confirmButtonEnabled
 property bool confirmButtonLoading
 property int closePolicy
 property Item dialogContentItem
 signal confirmClicked()
 signal cancelClicked()
 signal closeClicked()
 signal closed()
 function open() {}
 function close() { closed(); }
}
''')
    appearance=fixture/"qml/ClientAppearanceModule"; appearance.mkdir()
    (appearance/"qmldir").write_text("module ClientAppearanceModule\nsingleton AppearanceTokens 1.0 AppearanceTokens.qml\n")
    (appearance/"AppearanceTokens.qml").write_text('pragma Singleton\nimport QtQuick\nQtObject { property color colorTextSecondary: "#777777" }\n')
    qml=fixture/"mdata_handoff.qml"; shutil.copy2(Path(__file__).with_name(qml.name),qml)
    env.update(PATH=str(args.runtime)+os.pathsep+str(Path(os.environ["SystemRoot"])/"System32"),QT_QPA_PLATFORM="windows",QT_PLUGIN_PATH=str(args.runtime/"plugins"),QT_QUICK_BACKEND="software",QML_DISABLE_DISK_CACHE="1",QML_IMPORT_PATH=str(fixture/"qml"),QML2_IMPORT_PATH=str(fixture/"qml"))
    qml_result=host.run(fixture,args.runtime/"valmax_qml_bridge_test.exe",[fixture/"qml",qml],env,"qml")
    require(sources=={name:sha(ROOT/name) for name in paths},"handoff sources changed")
    result={"schema":1,"verified":True,"fixture":str(fixture),"sourceHashes":sources,"native":native,"qml":qml_result,
            "artifacts":{str(args.build/name):sha(args.build/name) for name in ("valmax_transfer_launch_test.exe","valmax_transfer_fake_launcher.exe")},
            "realClientStarted":False,"realProfileUsed":False,"importSuccessTested":False,"networkUsed":False,
            "scope":"Native exact mdata argv/launcher exit/error/timeout/duplicate and graceful quit gating with owned fake binary; QML explicit confirmation and singleton handoff using style/backend stubs."}
    args.report.write_text(json.dumps(result,indent=2)+"\n",encoding="utf8")
    print(json.dumps({"verified":True,"checks":native["result"]["checks"]+qml_result["result"]["checks"],"report":str(args.report)}))


if __name__=="__main__": main()
