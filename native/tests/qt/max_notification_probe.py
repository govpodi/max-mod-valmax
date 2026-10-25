"""Actual MAX Qt host/Rust/notification lease in an owned offline fixture.

Only synthetic PIN scopes are exercised. Pre-resume guards deny network and
credentials in all owned processes; no notifications or messages are sent.
"""
import ctypes
import hashlib
import json
from pathlib import Path
import shutil
import uuid

import max_engine_probe as base
from max_ghost_probe import ExplicitEnvironment

MONITOR = r'''
const note={hooksObserved:false,refreshConfirmed:0,refreshRejected:0,verifiedInstallation:false,
  syntheticSet:false,syntheticLock:false,syntheticUnlock:false,syntheticRemove:false,
  policyRestored:false,shutdownOk:false,shutdownAttempts:0,operationFailed:false};
let runtimeContext=ptr(0),emptyRevision='',phase=0;
function nativeExport(module,name) {
  // All observed entry points are imported by the real Qt plugin. Read its
  // resolved IAT rather than calling GetModuleHandle under the loader lock.
  const plugin=Process.findModuleByName('valmaxnativeplugin.dll');
  if(!plugin)return ptr(0);
  for(const value of plugin.enumerateImports())
    if(value.name===name&&String(value.module).toLowerCase()===module.toLowerCase())return value.address;
  return ptr(0);
}
function observe() {
  if(note.hooksObserved)return;
  const address=nativeExport('valmax_core.dll','valmax_runtime_notifications_refresh');
  if(address.isNull())return;
  Interceptor.attach(address,{onEnter(args){runtimeContext=args[0];},onLeave(result){
    if(result.toInt32()===0)note.refreshConfirmed++;else note.refreshRejected++;
  }});note.hooksObserved=true;
}
Process.attachModuleObserver({onAdded(module){if(module.name.toLowerCase()==='valmaxnativeplugin.dll')setTimeout(observe,0);}});
function syntheticOperation(action) {
  if(runtimeContext.isNull())return false;
  const address=nativeExport('valmax_core.dll','valmax_runtime_dispatch');
  const free=nativeExport('valmax_core.dll','valmax_core_buffer_free');
  if(address.isNull()||free.isNull())return false;
  // Fixed synthetic fixture data only. No user payload is observed or logged.
  let value={schema:1,accountId:'101',chatId:'202'};
  if(['set','unlock','remove'].includes(action))value.pin='426819';
  if(action==='set')value.confirmPin='426819';
  let json=JSON.stringify({operation:'chat_lock.execute',action:action,value:value});
  const input=Memory.allocUtf8String(json),length=json.length;
  const output=Memory.alloc(24);output.writeByteArray(new Uint8Array(24));
  let ok=false;
  try {
    const code=new NativeFunction(address,'int',['pointer','pointer','uint64','pointer'])(runtimeContext,input,length,output);
    if(code===0){
      const count=output.add(8).readU64().toNumber(),bytes=output.readPointer();
      if(!bytes.isNull()&&count>0&&count<65536)ok=JSON.parse(bytes.readUtf8String(count)).ok===true;
    }
  }finally{
    input.writeByteArray(new Uint8Array(length+1));value=null;json='';
    new NativeFunction(free,'void',['pointer'])(output);
  }
  if(!ok)note.operationFailed=true;
  return ok;
}
function notificationSnapshot() {
  observe();
  const address=nativeExport('valmax_notifications.dll','valmax_notifications_status');
  if(address.isNull())return {...note,loaded:false};
  const value=Memory.alloc(144);value.writeByteArray(new Uint8Array(144));value.writeU32(144);value.add(4).writeU32(1);
  const result=new NativeFunction(address,'uint',['pointer'])(value);
  if(result!==0)return {...note,loaded:true,error:result};
  const current={loaded:true,ready:value.add(12).readU32()===1,applied:value.add(16).readU32()===1,
    error:value.add(8).readU32(),replacements:value.add(20).readU32(),listeners:value.add(24).readU32()};
  const bytes=new Uint8Array(value.add(40).readByteArray(64));
  const revisionValid=Array.from(bytes).every(c=>(c>=48&&c<=57)||(c>=97&&c<=102));
  const revision=revisionValid?Array.from(bytes).map(c=>String.fromCharCode(c)).join(''):'';
  const good=current.ready&&current.applied&&current.error===0&&current.replacements===6&&current.listeners===2&&revisionValid;
  if(phase===0&&good&&note.refreshConfirmed>=1&&!runtimeContext.isNull()){
    note.verifiedInstallation=true;emptyRevision=revision;
    note.syntheticSet=syntheticOperation('set');phase=1;
  }else if(phase===1&&good&&revision!==emptyRevision&&note.syntheticSet){
    note.syntheticLock=syntheticOperation('lock');note.syntheticUnlock=syntheticOperation('unlock');
    note.syntheticRemove=syntheticOperation('remove');phase=2;
  }else if(phase===2&&good&&revision===emptyRevision&&note.syntheticRemove){
    note.policyRestored=true;phase=3;
  }
  if(phase===3&&!note.shutdownOk&&note.shutdownAttempts<5){
    const shutdown=nativeExport('valmax_notifications.dll','valmax_notifications_shutdown');
    if(!shutdown.isNull()){note.shutdownAttempts++;note.shutdownOk=new NativeFunction(shutdown,'uint',[])()===0;}
  }
  return {...note,...current};
}
'''


class NotificationProbe(base.OwnedProbe):
    def __init__(self, directory, output, namespace):
        super().__init__(directory, output, namespace)
        inherited = self.monitor_source.replace(
            "  const kernel=Process.getModuleByName('kernelbase.dll');", "  if(state.plugin) {\n  const kernel=Process.getModuleByName('kernelbase.dll');", 1).replace(
            "  for(const module of Process.enumerateModules())", "  }\n  for(const module of Process.enumerateModules())", 1)
        self.monitor_source = MONITOR + inherited.replace(
            "return {stats:{tracerHooks:0,transportHooks:0}", "return {notifications:notificationSnapshot(),stats:{tracerHooks:0,transportHooks:0}", 1)
        store = output / "profile/native-stores"; store.mkdir()
        self.device = ExplicitEnvironment(self.device, {"profileDirectory": str(store),
            "runtimeDirectory": str(directory / "runtime"), "existingClient": True})
        self.notifications = {}
    def consume_snapshot(self, pid, snapshot):
        super().consume_snapshot(pid, snapshot)
        if not self.sessions[pid]["service"]:
            self.notifications = snapshot.get("notifications", {})
    def monitor_message(self, pid, message, data):
        if message.get("type") == "error":
            category = str(message.get("description", "")).split(":", 1)[0]
            if category not in {"TypeError", "ReferenceError", "SyntaxError", "RangeError", "Error"}:
                category = "ScriptError"
            line = message.get("lineNumber", 0)
            self.record("dev_monitor_error", pid, category=category, line=line if type(line) is int else 0)
        super().monitor_message(pid, message, data)


def main():
    ctypes.windll.kernel32.SetErrorMode(0x8003)
    owner = json.loads((base.PROJECT / "analysis/native-migration-v1/notification-native-proof.json").read_text())
    source = Path(owner["pins"]["runtimeRoot"])
    directory = base.BUILD_ROOT / ("probe-" + uuid.uuid4().hex); directory.mkdir()
    runtime = directory / "runtime"; shutil.copytree(source, runtime)
    for name, pin in owner["pins"]["images"].items():
        if hashlib.sha256((runtime / name).read_bytes()).hexdigest() != pin["sha256"]:
            raise RuntimeError("fixture_image_mismatch")
    module = runtime / "qml/ValMAX/Native"
    for name in ("qmldir", "SettingsTransport.qml", "ChatLockTransport.qml", "valmaxnativeplugin.dll"):
        shutil.copy2(Path("C:/ValMAXBuild/build/qt-notification-bridge/qml/ValMAX/Native") / name, module / name)
    shutil.copy2("C:/ValMAXBuild/target/release/valmax_core.dll", runtime / "valmax_core.dll")
    shutil.copy2("C:/ValMAXBuild/build/hooks/valmax_notifications.dll", runtime / "valmax_notifications.dll")
    asset = owner["pins"]["asset"]["name"]
    shutil.copy2(Path("C:/ValMAXBuild/build/hooks") / asset, runtime / asset)
    if hashlib.sha256((runtime / "valmax_notifications.dll").read_bytes()).hexdigest() != owner["dllSha256"]:
        raise RuntimeError("fixture_hook_mismatch")
    build = json.loads((source.parent / "build-proof.json").read_text()); build.update(directory=str(directory), nativeStarted=False)
    (directory / "build-proof.json").write_text(json.dumps(build, indent=2))
    base.build_mod.verify(runtime / "mod-manifest.json")
    output = directory / ("run-" + uuid.uuid4().hex); base.prepare_profile(output)
    original = output / "profile/qt/11/MXMOD/config.json"
    config = json.loads(original.read_text()); namespace = build["namespace"]
    config.update(app_folder_name=namespace,single_instance_id=namespace.lower()+"_instance",
        local_server_pipe_name=namespace.lower()+"_local_pipe",client_friendly_name="ValMAX Native OFFLINE QA",is_qrcode_login_first=False)
    target = original.parent.with_name(namespace); target.mkdir(); (target / "config.json").write_text(json.dumps(config))
    original.unlink(); original.parent.rmdir()
    probe = NotificationProbe(directory,output,namespace); result = probe.run(20)
    keys = ("loaded","hooksObserved","verifiedInstallation","syntheticSet","syntheticLock","syntheticUnlock","syntheticRemove","policyRestored","shutdownOk")
    verified = not result["guardFailed"] and all(probe.notifications.get(key) is True for key in keys)
    verified = verified and probe.notifications.get("ready") is False and probe.notifications.get("refreshRejected",0)>0 and not probe.notifications.get("operationFailed")
    verified = verified and probe.notifications.get("replacements")==0 and probe.notifications.get("listeners")==0 and probe.native.get("bridge") is True
    proof = {"schema":1,"verified":verified,"fixture":str(directory),"guardFailed":result["guardFailed"],
        "native":probe.native,"notifications":probe.notifications,"guards":probe.guard_checks,"namespace":namespace,
        "offline":True,"anonymous":True,"syntheticPinOnly":True,"osNotificationDeliveryTested":False,
        "installedCopyChanged":False,"realProfilesAccessed":False,"temporaryGuardIsDevToolOnly":True,
        "hashes":{name:hashlib.sha256((runtime/name).read_bytes()).hexdigest() for name in ("max.exe","notifications.dll","valmax_notifications.dll","valmax_core.dll",asset)},
        "pluginSha256":hashlib.sha256((module/"valmaxnativeplugin.dll").read_bytes()).hexdigest()}
    (base.PROJECT/"analysis/native-migration-v1/qt-notification-max-lifecycle-proof.json").write_text(json.dumps(proof,indent=2))
    print(json.dumps({key:proof[key] for key in ("verified","fixture","guardFailed","notifications","native")}))
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
