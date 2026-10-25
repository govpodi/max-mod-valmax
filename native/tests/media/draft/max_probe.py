"""One finite, anonymous MAX type/void-dispatch probe; never draft acceptance."""
import base64
import ctypes
import hashlib
import json
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "native/tests/qt"))
import max_engine_probe as base
from max_ghost_probe import ExplicitEnvironment

DIRECTORY = base.BUILD_ROOT / "probe-800fc79010034de8bf9269b1fd0679fe"
DLL = Path("C:/ValMAXBuild/build/media-draft-probe/valmax_draft_contract_probe.dll")
MONITOR = r'''
let draftStarted=false,draftSnapshotCall=null,draftFailure='';
function draftSnapshot() {
 if(!draftStarted && state.bridge && state.pluginOwned) {
  draftStarted=true;
  const k=Process.getModuleByName('kernelbase.dll');
  const load=new NativeFunction(k.getExportByName('LoadLibraryExW'),'pointer',['pointer','pointer','uint']);
  const proc=new NativeFunction(k.getExportByName('GetProcAddress'),'pointer',['pointer','pointer']);
  const module=load(Memory.allocUtf16String(DRAFT_DLL),ptr(0),0x100|0x1000);
  if(module.isNull()){draftFailure='helper_load_failed';return {failure:draftFailure};}
  const start=proc(module,Memory.allocUtf8String('valmax_draft_probe_start'));
  const snapshot=proc(module,Memory.allocUtf8String('valmax_draft_probe_snapshot'));
  if(start.isNull()||snapshot.isNull()){draftFailure='helper_export_missing';return {failure:draftFailure};}
  draftSnapshotCall=new NativeFunction(snapshot,'int',['pointer','uint']);
  const code=new NativeFunction(start,'int',['pointer'])(Memory.allocUtf16String(DRAFT_FILE));
  if(code!==0)draftFailure='helper_start_rejected';
 }
 if(draftFailure)return {failure:draftFailure};
 if(!draftSnapshotCall)return {done:false,started:draftStarted};
 const output=Memory.alloc(8192),size=draftSnapshotCall(output,8192);
 if(size<=0||size>=8192)return {failure:'invalid_snapshot'};
 return JSON.parse(output.readUtf8String(size));
}
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DraftProbe(base.OwnedProbe):
    def __init__(self, directory, output, namespace, helper):
        super().__init__(directory, output, namespace)
        inherited = self.monitor_source.replace(
            "  const kernel=Process.getModuleByName('kernelbase.dll');", "  if(state.plugin) {\n  const kernel=Process.getModuleByName('kernelbase.dll');", 1).replace(
            "  for(const module of Process.enumerateModules())", "  }\n  for(const module of Process.enumerateModules())", 1)
        self.monitor_source = ("const DRAFT_DLL=" + json.dumps(helper.as_posix())
            + ";const DRAFT_FILE=" + json.dumps((output / "synthetic-draft.png").as_posix()) + ";\n"
            + MONITOR + inherited.replace("return {stats:{tracerHooks:0,transportHooks:0}",
                "return {draft:draftSnapshot(),stats:{tracerHooks:0,transportHooks:0}", 1))
        stores, runtime = output / "profile/native-stores", output / "runtime-state"
        stores.mkdir(); runtime.mkdir()
        self.device = ExplicitEnvironment(self.device, {"profileDirectory": str(stores),
            "runtimeDirectory": str(runtime), "existingClient": True})
        self.draft = {}
        self.wait_handles = {}

    def retain(self, pid):
        super().retain(pid)
        if pid not in self.wait_handles:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
            kernel.OpenProcess.restype = ctypes.c_void_p
            handle = kernel.OpenProcess(0x100000, False, pid)
            if not handle:
                raise RuntimeError("owned_wait_handle_failed")
            self.wait_handles[pid] = handle

    def consume_snapshot(self, pid, snapshot):
        super().consume_snapshot(pid, snapshot)
        if not self.sessions[pid]["service"]:
            self.draft = snapshot.get("draft", {})

    def cleanup_proof(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
        kernel.WaitForSingleObject.restype = ctypes.c_ulong
        kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
        results = []
        for pid, handle in self.wait_handles.items():
            results.append({"pid": pid, "terminated": kernel.WaitForSingleObject(handle, 5000) == 0})
            kernel.CloseHandle(handle)
        self.wait_handles.clear()
        return results


def main():
    ctypes.windll.kernel32.SetErrorMode(0x8003)
    directory = DIRECTORY.resolve(strict=True)
    if directory.parent != base.BUILD_ROOT.resolve() or directory.is_symlink():
        raise RuntimeError("invalid_owned_fixture")
    runtime = directory / "runtime"
    build = json.loads((directory / "build-proof.json").read_text())
    static_path = ROOT / "analysis/native-migration-v1/media-draft-static-proof.json"
    static = json.loads(static_path.read_text())
    if not static["staticEvidenceVerified"] or sha(runtime / "max.exe") != static["fixtureSha256"]:
        raise RuntimeError("static_evidence_pin_mismatch")
    base.build_mod.verify(runtime / "mod-manifest.json")
    pins = {name: sha(runtime / name) for name in ("max.exe", "core.dll", "valmax_core.dll", "Qt6Core.dll", "Qt6Qml.dll", "Qt6Quick.dll")}
    for name, expected in build["qtDllHashes"].items():
        if pins[name] != expected:
            raise RuntimeError("runtime_qt_pin_mismatch")
    output = directory / ("draft-run-" + uuid.uuid4().hex)
    base.prepare_profile(output)
    original = output / "profile/qt/11/MXMOD/config.json"
    config = json.loads(original.read_text())
    namespace = build["namespace"]
    if namespace != "N0B5F":
        raise RuntimeError("unexpected_namespace")
    config.update(app_folder_name=namespace, single_instance_id=namespace.lower()+"_instance",
        local_server_pipe_name=namespace.lower()+"_local_pipe",client_friendly_name="ValMAX Native OFFLINE QA",is_qrcode_login_first=False)
    target = original.parent.with_name(namespace)
    target.mkdir(); (target / "config.json").write_text(json.dumps(config))
    original.unlink(); original.parent.rmdir()
    (output / "synthetic-draft.png").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
    helper = runtime / ("valmax_draft_contract_probe_" + sha(DLL)[:16] + ".dll")
    if helper.exists() and sha(helper) != sha(DLL):
        raise RuntimeError("helper_collision")
    if not helper.exists():
        shutil.copy2(DLL, helper)
    probe = DraftProbe(directory, output, namespace, helper)
    result = probe.run(15)
    cleanup = probe.cleanup_proof()
    unchanged = all(sha(runtime / name) == expected for name, expected in pins.items())
    verified = (not result["guardFailed"] and probe.draft.get("verified") is True and unchanged
        and all(item["terminated"] for item in cleanup)
        and any(item["namespaceVerified"] for item in probe.guard_checks))
    proof = {"schema": 1, "typeAndDispatchVerified": verified, "fixture": str(directory), "run": str(output),
        "guardFailed": result["guardFailed"], "guards": probe.guard_checks, "draft": probe.draft, "cleanup": cleanup,
        "hashes": pins, "helperSha256": sha(helper), "sourceSha256": sha(Path(__file__)),
        "helperSourceSha256": sha(Path(__file__).with_name("draft_probe.cpp")), "staticProofSha256": sha(static_path),
        "runtimeBinariesUnchanged": unchanged, "installedCopyChanged": False, "realProfilesAccessed": False,
        "sendInvoked": False, "presenterInitialized": False, "acceptanceProven": False, "fileOwnershipProven": False,
        "temporaryGuardsAreDevToolOnly": True,
        "scope": "Actual native type/model metadata and void dispatch with a verified null presenter; not draft acceptance"}
    path = ROOT / "analysis/native-migration-v1/media-draft-max-proof.json"
    path.write_text(json.dumps(proof, indent=2))
    print(json.dumps({key: proof[key] for key in ("typeAndDispatchVerified", "guardFailed", "draft", "cleanup")}))
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
