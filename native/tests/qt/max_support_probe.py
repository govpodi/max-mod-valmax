"""Support runtime lifecycle inside a pinned anonymous, guarded offline MAX.

Reuse an identified owned fixture without changing MAX/core/resources. A fixed
synthetic request sequence executes on the plugin's own runtime worker before
its current native call returns, while its shared RuntimeState is still held.
No context pointer survives that callback. This is not a SaveDialog visual test.
"""
import ctypes
import hashlib
import json
from pathlib import Path
import shutil
import uuid

import max_engine_probe as base
from max_ghost_probe import ExplicitEnvironment

DIRECTORY = base.BUILD_ROOT / "probe-800fc79010034de8bf9269b1fd0679fe"
BUILD = Path("C:/ValMAXBuild/build/qt-support-bridge")
RUST = Path("C:/ValMAXBuild/target/release/deps/valmax_core.dll")

MONITOR = r'''
const support={observed:false,executed:false,preview:false,exported:false,duplicateRejected:false,
 sourceExtrasRejected:false,borrowedWithinHostCall:false,failure:'',calls:0,openCalls:0,dispatchCalls:0,contextOpened:false,openError:''};
let active=false;
function supportExport(name) {
 const plugin=Process.findModuleByName('valmaxnativeplugin.dll');if(!plugin)return ptr(0);
 for(const value of plugin.enumerateImports())
  if(value.name===name&&String(value.module).toLowerCase()==='valmax_core.dll')return value.address;
 return ptr(0);
}
function exerciseSupport(context,dispatch,free) {
 function call(value) {
  const text=JSON.stringify(value),input=Memory.allocUtf8String(text),output=Memory.alloc(24);
  output.writeByteArray(new Uint8Array(24));support.calls++;
  try {
   const code=new NativeFunction(dispatch,'int',['pointer','pointer','uint64','pointer'])(context,input,text.length,output);
   if(code!==0)throw new Error('dispatch_failed');
   const length=output.add(8).readU64().toNumber(),data=output.readPointer();
   if(data.isNull()||length<1||length>16384)throw new Error('invalid_output');
   return JSON.parse(data.readUtf8String(length));
  }finally {
   input.writeByteArray(new Uint8Array(text.length+1));
   new NativeFunction(free,'void',['pointer'])(output);
  }
 }
 const source={versions:{valmax:null,max:'26.35.1.80662',windows:null,qt:null,secret:'SYNTHETIC-ONLY'},
  startup:null,privacy:{verified:false,secret:'SYNTHETIC-ONLY',accountId:'SYNTHETIC-ONLY'}};
 const preview=call({operation:'support.preview',context:source});
 support.preview=preview.schema===1&&preview.ok===true&&preview.result.schema===1
  &&preview.result.report.schema===2&&preview.result.report.runtime.kind==='native';
 const serialized=JSON.stringify(preview);
 support.sourceExtrasRejected=serialized.indexOf('SYNTHETIC-ONLY')<0&&serialized.indexOf('accountId')<0
  &&serialized.indexOf('file:///')<0;
 const exported=call({operation:'support.export',context:source,file:SUPPORT_OUTPUT_URL});
 support.exported=exported.schema===1&&exported.ok===true&&exported.result.saved===true;
 const duplicate=call({operation:'support.export',context:source,file:SUPPORT_OUTPUT_URL});
 support.duplicateRejected=duplicate.schema===1&&duplicate.ok===false&&duplicate.error==='file_exists';
}
function observeSupport() {
 if(support.observed)return;
 const dispatch=supportExport('valmax_runtime_dispatch'),free=supportExport('valmax_core_buffer_free'),open=supportExport('valmax_runtime_open');
 if(dispatch.isNull()||free.isNull()||open.isNull())return;
 function host(address) {const plugin=Process.findModuleByName('valmaxnativeplugin.dll');return plugin
  &&address.compare(plugin.base)>=0&&address.compare(plugin.base.add(plugin.size))<0;}
 function once(context) {
  if(active||support.executed||context.isNull())return;
  active=true;support.executed=true;support.borrowedWithinHostCall=true;
  try {exerciseSupport(context,dispatch,free);}
  catch(_){support.failure='synthetic_operation_failed';}
  finally {active=false;}
 }
 Interceptor.attach(open,{
  onEnter(args){this.host=host(this.returnAddress);if(this.host){support.openCalls++;this.out=args[2];this.result=args[3];}},
  onLeave(code){
   if(!this.host)return;
   const context=this.out.readPointer();support.contextOpened=!context.isNull();
   if(code.toInt32()===0&&support.contextOpened)once(context);
   else if(code.toInt32()===0){
    try {const size=this.result.add(8).readU64().toNumber();if(size>0&&size<512){
     const value=JSON.parse(this.result.readPointer().readUtf8String(size));
     support.openError=['invalid_request','unsafe_path','io_error','invalid_store','store_busy'].includes(value.error)?value.error:'other_error';
    }}catch(_){support.openError='invalid_output';}
   }
   this.out=ptr(0);this.result=ptr(0);
  }
 });
 Interceptor.attach(dispatch,{
  onEnter(args) {
   this.borrow=!active&&!support.executed&&host(this.returnAddress);
   if(host(this.returnAddress))support.dispatchCalls++;
   if(this.borrow)this.context=args[0];
  },
  onLeave(result) {
   if(!this.borrow||result.toInt32()!==0||this.context.isNull())return;
   once(this.context);this.context=ptr(0);
  }
 });support.observed=true;
}
Process.attachModuleObserver({onAdded(module){if(module.name.toLowerCase()==='valmaxnativeplugin.dll'){observeSupport();setTimeout(observeSupport,0);}}});
'''


class SupportProbe(base.OwnedProbe):
    def __init__(self, directory, output, namespace):
        super().__init__(directory, output, namespace)
        inherited = self.monitor_source.replace(
            "  const kernel=Process.getModuleByName('kernelbase.dll');", "  if(state.plugin) {\n  const kernel=Process.getModuleByName('kernelbase.dll');", 1).replace(
            "  for(const module of Process.enumerateModules())", "  }\n  for(const module of Process.enumerateModules())", 1)
        target = output / "reports/synthetic-report.json"
        target.parent.mkdir()
        self.monitor_source = ("const SUPPORT_OUTPUT_URL=" + json.dumps(target.as_uri()) + ";\n" + MONITOR
            + inherited.replace("return {stats:{tracerHooks:0,transportHooks:0}",
                                "return {support:{...support},stats:{tracerHooks:0,transportHooks:0}", 1))
        store = output / "profile/native-stores"
        runtime = output / "runtime-state"
        store.mkdir(); runtime.mkdir()
        self.device = ExplicitEnvironment(self.device, {"profileDirectory": str(store),
            "runtimeDirectory": str(runtime), "existingClient": True})
        self.support = {}

    def consume_snapshot(self, pid, snapshot):
        super().consume_snapshot(pid, snapshot)
        if not self.sessions[pid]["service"]:
            self.support = snapshot.get("support", {})

    def monitor_message(self, pid, message, data):
        if message.get("type") == "error":
            self.record("dev_monitor_error", pid, code="script_error")
        super().monitor_message(pid, message, data)


def main():
    ctypes.windll.kernel32.SetErrorMode(0x8003)
    directory = DIRECTORY.resolve(strict=True)
    if directory.parent != base.BUILD_ROOT.resolve() or directory.is_symlink():
        raise RuntimeError("invalid_fixture")
    build = json.loads((directory / "build-proof.json").read_text())
    if build["namespace"] != "N0B5F":
        raise RuntimeError("unexpected_owned_namespace")
    runtime = directory / "runtime"
    owner = json.loads((base.PROJECT / "analysis/native-migration-v1/privacy-native-proof.json").read_text())
    for pin in owner["pins"]["files"]:
        if hashlib.sha256((runtime / pin["relative"]).read_bytes()).hexdigest() != pin["sha256"]:
            raise RuntimeError("fixture_pin_mismatch")
    core_proof = json.loads((base.PROJECT / "analysis/native-migration-v1/support-qt-proof.json").read_text())
    if hashlib.sha256(RUST.read_bytes()).hexdigest() != core_proof["rustSha256"]:
        raise RuntimeError("rust_tested_bytes_mismatch")
    module = runtime / "qml/ValMAX/Native"
    for name in ("qmldir", "SettingsTransport.qml", "ChatLockTransport.qml", "SupportTransport.qml", "valmaxnativeplugin.dll"):
        shutil.copy2(BUILD / "qml/ValMAX/Native" / name, module / name)
    shutil.copy2(RUST, runtime / "valmax_core.dll")
    if hashlib.sha256((module / "valmaxnativeplugin.dll").read_bytes()).hexdigest() != core_proof["pluginSha256"]:
        raise RuntimeError("plugin_tested_bytes_mismatch")
    base.build_mod.verify(runtime / "mod-manifest.json")
    output = directory / ("support-run-" + uuid.uuid4().hex)
    base.prepare_profile(output)
    original = output / "profile/qt/11/MXMOD/config.json"
    config = json.loads(original.read_text())
    namespace = build["namespace"]
    config.update(app_folder_name=namespace, single_instance_id=namespace.lower()+"_instance",
        local_server_pipe_name=namespace.lower()+"_local_pipe",client_friendly_name="ValMAX Native OFFLINE QA",is_qrcode_login_first=False)
    target = original.parent.with_name(namespace)
    target.mkdir(); (target / "config.json").write_text(json.dumps(config))
    original.unlink(); original.parent.rmdir()
    probe = SupportProbe(directory, output, namespace)
    result = probe.run(15)
    keys = ("observed", "executed", "preview", "exported", "duplicateRejected", "sourceExtrasRejected", "borrowedWithinHostCall")
    verified = not result["guardFailed"] and all(probe.support.get(key) is True for key in keys)
    verified = verified and not probe.support.get("failure") and probe.support.get("calls") == 3
    verified = verified and all(probe.native.get(key) is True for key in ("bridge", "pluginOwned", "rust"))
    report_path = output / "reports/synthetic-report.json"
    if verified:
        report = json.loads(report_path.read_text())
        verified = report["schema"] == 2 and report["runtime"]["kind"] == "native" and report["flags"]["verified"] is False
        verified = verified and "SYNTHETIC-ONLY" not in report_path.read_text() and len(list(report_path.parent.iterdir())) == 1
    proof = {"schema": 1, "verified": bool(verified), "fixture": str(directory), "run": str(output),
        "guardFailed": result["guardFailed"], "support": probe.support, "native": probe.native,
        "guards": probe.guard_checks, "namespace": namespace, "offline": True, "anonymous": True,
        "installedCopyChanged": False, "realProfilesAccessed": False, "syntheticReportsOnly": True,
        "maxBinaryUnchanged": True, "temporaryGuardIsDevToolOnly": True,
        "actualSaveDialogTested": False, "actualSupportPageRendered": False,
        "scope": "Runtime lifecycle on actual MAX Qt worker, plus separate exact-controller proof; not full visual integration",
        "noNetworkAbsenceGuarantee": True,
        "hashes": {name: hashlib.sha256((runtime / name).read_bytes()).hexdigest() for name in
            ("max.exe", "core.dll", "valmax_core.dll", "Qt6Core.dll", "Qt6Qml.dll", "Qt6Quick.dll")},
        "pluginSha256": hashlib.sha256((module / "valmaxnativeplugin.dll").read_bytes()).hexdigest(),
        "sourceSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (base.PROJECT / "analysis/native-migration-v1/support-max-proof.json").write_text(json.dumps(proof, indent=2))
    print(json.dumps({key: proof[key] for key in ("verified", "fixture", "guardFailed", "support", "native")}))
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
