"""Real MAX, pinned native ghost + Qt host + Rust ACK, disabled policy only.

Development-only Frida guard denies network/credentials before main and child
resume. Copies an already reviewed anonymous namespace; no installed MAX use.
"""
from __future__ import annotations
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

import max_engine_probe as base

GHOST_MONITOR = r'''
const ghostStats={ackAccepted:0,ackRevoked:0,ackReady:false,ackDisabled:false,ackHook:false,
  verifiedInstallation:false,shutdownAttempts:0,shutdownOk:false};
function nativeExport(module,name) {
  const k=Process.getModuleByName('kernelbase.dll');
  const gm=new NativeFunction(k.getExportByName('GetModuleHandleW'),'pointer',['pointer']);
  const gp=new NativeFunction(k.getExportByName('GetProcAddress'),'pointer',['pointer','pointer']);
  const handle=gm(Memory.allocUtf16String(module));
  return handle.isNull()?ptr(0):gp(handle,Memory.allocUtf8String(name));
}
function installAckObservation() {
  if(ghostStats.ackHook)return;
  const address=nativeExport('valmax_core.dll','valmax_runtime_ghost_ack');
  if(address.isNull())return;
  Interceptor.attach(address,{
    onEnter(args){
      const value=args[1];this.revoked=value.isNull();this.valid=false;
      if(!this.revoked){try{
        this.valid=value.readU32()===144 && value.add(4).readU32()===1 && value.add(12).readU32()===1
          && value.add(136).readU32()===5 && value.add(140).readU32()===7;
        this.disabled=value.add(88).readU8()===0;
      }catch(_){}}
    },
    onLeave(result){
      if(result.toInt32()!==0)return;
      if(this.revoked)ghostStats.ackRevoked++;
      else {ghostStats.ackAccepted++;ghostStats.ackReady=this.valid;ghostStats.ackDisabled=this.disabled;}
    }
  });ghostStats.ackHook=true;
}
Process.attachModuleObserver({onAdded(module){if(module.name.toLowerCase()==='valmaxnativeplugin.dll')setTimeout(installAckObservation,0);}});
function ghostSnapshot() {
  installAckObservation();
  const address=nativeExport('valmax_ghost.dll','valmax_ghost_status');
  if(address.isNull())return {...ghostStats,loaded:false};
  const value=Memory.alloc(144);value.writeU32(144);value.add(4).writeU32(1);
  const code=new NativeFunction(address,'uint',['pointer'])(value);
  if(code!==0)return {...ghostStats,loaded:true,error:code};
  const current={loaded:true,error:value.add(8).readU32(),ready:value.add(12).readU32()===1,
    disabled:value.add(88).readU8()===0,replacements:value.add(136).readU32(),listeners:value.add(140).readU32()};
  if(current.ready && current.error===0 && current.disabled && current.replacements===5 && current.listeners===7
     && ghostStats.ackReady && ghostStats.ackDisabled && ghostStats.ackAccepted>=2)ghostStats.verifiedInstallation=true;
  // Owned fixture lifecycle test only: detach the actual DLL, then observe the
  // C++ host revoking its Rust ACK. No saved policy is enabled or changed.
  if(ghostStats.verifiedInstallation && !ghostStats.shutdownOk && ghostStats.shutdownAttempts<5) {
    const shutdown=nativeExport('valmax_ghost.dll','valmax_ghost_shutdown');
    if(!shutdown.isNull()){ghostStats.shutdownAttempts++;ghostStats.shutdownOk=new NativeFunction(shutdown,'uint',[])()===0;}
  }
  return {...ghostStats,...current};
}
'''


class ExplicitEnvironment:
    def __init__(self, device, config):
        self.device, self.config = device, config
    def __getattr__(self, name):
        return getattr(self.device, name)
    def spawn(self, argv, **kwargs):
        kwargs["env"] = {**kwargs.get("env", {}), "VALMAX_NATIVE_RUNTIME_CONFIG": json.dumps(self.config),
                         "QT_QPA_PLATFORM": "windows", "QT_DEBUG_PLUGINS": "1", "QML_XHR_ALLOW_FILE_READ": "1"}
        return self.device.spawn(argv, **kwargs)


class GhostProbe(base.OwnedProbe):
    def __init__(self, directory, output, namespace):
        super().__init__(directory, output, namespace)
        # Only boolean/status counters are observed; no payload bytes or IDs.
        self.monitor_source = GHOST_MONITOR + self.monitor_source.replace(
            "return {stats:{tracerHooks:0,transportHooks:0}", "return {ghost:ghostSnapshot(),stats:{tracerHooks:0,transportHooks:0}", 1)
        store = output / "profile/native-stores"
        store.mkdir()
        self.device = ExplicitEnvironment(self.device, {"profileDirectory": str(store),
            "runtimeDirectory": str(directory / "runtime"), "existingClient": True})
        self.ghost = {}
    def consume_snapshot(self, pid, snapshot):
        super().consume_snapshot(pid, snapshot)
        if not self.sessions[pid]["service"]:
            self.ghost = snapshot.get("ghost", {})


def main():
    ctypes.windll.kernel32.SetErrorMode(0x8003)
    project = base.PROJECT
    owner_proof = json.loads((project / "analysis/native-migration-v1/ghost-native-proof.json").read_text())
    original_runtime = Path(owner_proof["pins"]["runtimeRoot"])
    directory = base.BUILD_ROOT / ("probe-" + uuid.uuid4().hex)
    directory.mkdir()
    runtime = directory / "runtime"
    shutil.copytree(original_runtime, runtime)
    for name in ("core.dll", "max.exe"):
        assert hashlib.sha256((runtime / name).read_bytes()).hexdigest() == owner_proof["pins"]["images"][name]["sha256"]
    module = runtime / "qml/ValMAX/Native"
    for name in ("qmldir", "SettingsTransport.qml", "ChatLockTransport.qml", "valmaxnativeplugin.dll"):
        shutil.copy2(Path("C:/ValMAXBuild/build/qt-ghost-bridge/qml/ValMAX/Native") / name, module / name)
    shutil.copy2("C:/ValMAXBuild/target/release/valmax_core.dll", runtime / "valmax_core.dll")
    shutil.copy2("C:/ValMAXBuild/build/hooks/valmax_ghost.dll", runtime / "valmax_ghost.dll")
    assert hashlib.sha256((runtime / "valmax_ghost.dll").read_bytes()).hexdigest() == owner_proof["dllSha256"]
    build = json.loads((original_runtime.parent / "build-proof.json").read_text())
    build.update(directory=str(directory), nativeStarted=False)
    (directory / "build-proof.json").write_text(json.dumps(build, indent=2))
    base.build_mod.verify(runtime / "mod-manifest.json")
    output = directory / ("run-" + uuid.uuid4().hex)
    base.prepare_profile(output)
    original = output / "profile/qt/11/MXMOD/config.json"
    config = json.loads(original.read_text())
    namespace = build["namespace"]
    config.update(app_folder_name=namespace, single_instance_id=namespace.lower() + "_instance",
                  local_server_pipe_name=namespace.lower() + "_local_pipe", client_friendly_name="ValMAX Native OFFLINE QA", is_qrcode_login_first=False)
    target = original.parent.with_name(namespace); target.mkdir()
    (target / "config.json").write_text(json.dumps(config))
    original.unlink(); original.parent.rmdir()
    probe = GhostProbe(directory, output, namespace)
    result = probe.run(15)
    healthy = all(probe.ghost.get(k) is True for k in ("loaded", "disabled", "ackHook", "ackReady", "ackDisabled", "verifiedInstallation", "shutdownOk"))
    verified = not result["guardFailed"] and healthy and probe.ghost.get("ready") is False and probe.ghost.get("ackRevoked", 0) >= 1
    verified = verified and probe.ghost.get("replacements") == 0 and probe.ghost.get("listeners") == 0 and probe.native.get("bridge") is True
    proof = {"schema": 1, "verified": verified, "fixture": str(directory), "guardFailed": result["guardFailed"],
             "namespace": namespace, "offline": True, "anonymous": True, "disabledPolicyOnly": True,
             "native": probe.native, "ghost": probe.ghost, "guards": probe.guard_checks,
             "installedCopyChanged": False, "realProfilesAccessed": False, "serverBehaviorTested": False,
             "nativeDllHashes": {name: hashlib.sha256((runtime / name).read_bytes()).hexdigest() for name in ("max.exe", "core.dll", "valmax_ghost.dll", "valmax_core.dll")},
             "pluginSha256": hashlib.sha256((module / "valmaxnativeplugin.dll").read_bytes()).hexdigest(),
             "temporaryGuardIsDevToolOnly": True}
    (project / "analysis/native-migration-v1/qt-ghost-max-lifecycle-proof.json").write_text(json.dumps(proof, indent=2))
    print(json.dumps({k: proof[k] for k in ("verified", "guardFailed", "fixture", "native", "ghost")}))
    raise SystemExit(0 if verified else 1)


if __name__ == "__main__":
    main()
