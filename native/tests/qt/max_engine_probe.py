"""Actual MAX QQmlEngine probe in a fresh guarded offline namespace.

Frida is a development-only isolation guard here. All guards load before the
owned suspended process runs. This is not a production runtime or OS sandbox.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

PROJECT = Path(__file__).resolve().parents[3]
BASELINE = Path("D:/CodexLabs/max-mod-26.35.1/runtime/baseline")
BUILD_ROOT = Path("C:/ValMAXBuild/build/max-engine-probes")
HOST_TEMP = Path("C:/ValMAXBuild/tmp/frida-native-qt")
HOST_TEMP.mkdir(parents=True, exist_ok=True)
os.environ["TEMP"] = os.environ["TMP"] = str(HOST_TEMP)
tempfile.tempdir = str(HOST_TEMP)
sys.path[:0] = [str(PROJECT / "modkit"), str(PROJECT / "native/tooling")]
import build_mod
import build_native_qml
from ui_multi_resource_patch import make_resource_spec
from privacy_monitor_probe import Probe, prepare_profile
import privacy_monitor_probe as probe_module
from privacy_rpc import SnapshotChannel

REQUIRED = {"NtCreateFile", "NtOpenFile", "CredReadW", "CredEnumerateW", "CredWriteW", "CredDeleteW",
            "QStandardPaths::writableLocation", "RegCreateKeyExW", "RegSetValueExW", "connect", "WSAConnect",
            "send", "sendto", "WSASend", "WSASendTo", "getaddrinfo", "GetAddrInfoW"}
QA_QML = r'''
    // Synthetic data only. No account model, network call or user interaction.
    property bool nativeQaBridge: false
    property bool nativeQaRows: false
    property bool nativeQaScroll: false
    property bool nativeQaPool: false
    property string nativeQaRequest: ""
    readonly property string nativeQaTitle: "VALMAX_NATIVE_QA|" + (nativeQaBridge ? "1" : "0") + "|" + (nativeQaRows ? "1" : "0") + "|" + (nativeQaScroll ? "1" : "0") + "|" + (nativeQaPool ? "1" : "0")
    Connections {
        target: root.valmaxNativeBridge
        function onOperationFinished(id, responseJson) {
            if (id !== root.nativeQaRequest) return;
            const response = JSON.parse(responseJson);
            root.nativeQaBridge = response.ok && response.result.runtime === "rust" && response.result.abi === 1;
        }
    }
    Item {
        width: 320; height: 200; opacity: 0; enabled: false
        Flickable {
            id: nativeQaView
            anchors.fill: parent; contentHeight: 800
            Repeater {
                id: nativeQaRepeater
                model: 20
                delegate: ValMAXQa.ValMAXAsyncListRow {
                    required property int index
                    view: nativeQaView; workQueue: nativeQaQueue
                    width: 320; rowHeight: 40; y: index * 40
                    identityKey: "native-qa-synthetic-" + index
                    rowComponent: Component { Rectangle { color: "purple"; objectName: "nativeQaBody" } }
                }
            }
        }
        ValMAXQa.ValMAXListWorkQueue { id: nativeQaQueue; objectName: "nativeQaCppQueue"; view: nativeQaView }
        Timer {
            interval: 1000; running: true; repeat: true
            property int phase: 0
            onTriggered: {
                if (phase === 0) {
                    const op = root.valmaxNativeBridge.invoke('{"operation":"version"}');
                    if (op.accepted) root.nativeQaRequest = op.requestId;
                    let ready = true;
                    for (let i = 0; i < 5; ++i) ready = ready && nativeQaRepeater.itemAt(i).ready;
                    if (!ready || nativeQaQueue.grants < 5) return;
                    root.nativeQaRows = true; nativeQaView.contentY = 400; phase = 1;
                } else if (phase === 1) {
                    let ready = true;
                    for (let i = 10; i < 15; ++i) ready = ready && nativeQaRepeater.itemAt(i).ready;
                    if (!ready || nativeQaRepeater.itemAt(0).item !== null) return;
                    root.nativeQaScroll = true; nativeQaRepeater.itemAt(12).pooled = true; phase = 2;
                } else if (phase === 2) {
                    root.nativeQaPool = nativeQaRepeater.itemAt(12).item === null && nativeQaQueue.peakInflight > 0;
                    stop();
                }
            }
        }
    }
'''
MONITOR = r'''
const state={markers:0,bridge:false,rows:false,scroll:false,pool:false,plugin:false,rust:false,pluginOwned:false};
Process.attachModuleObserver({onAdded(module){
  const name=module.name.toLowerCase();
  if(name==='valmaxnativeplugin.dll') {state.plugin=true;state.pluginOwned=module.path.replace(/\\/g,'/').toLowerCase()===MONITOR.plugin.toLowerCase();}
  if(name==='valmax_core.dll') state.rust=module.path.replace(/\\/g,'/').toLowerCase()===MONITOR.rust.toLowerCase();
  if(name!=='user32.dll')return;
  Interceptor.attach(module.getExportByName('SetWindowTextW'),{onEnter(args){
    const prefix='VALMAX_NATIVE_QA|';const p=args[1];if(p.isNull())return;
    try {
      for(let i=0;i<prefix.length;i++)if(p.add(i*2).readU16()!==prefix.charCodeAt(i))return;
      const digits=[];for(let i=0;i<7;i++){
        const code=p.add((prefix.length+i)*2).readU16();
        if(i%2){if(code!==124)return;}else {if(code!==48&&code!==49)return;digits.push(code===49);}
      }
      if(p.add((prefix.length+7)*2).readU16()!==0)return;
      state.markers=Math.min(10000,state.markers+1);
      [state.bridge,state.rows,state.scroll,state.pool]=digits;
    }catch(_){}
  }});
}});
function snapshot(){
  const kernel=Process.getModuleByName('kernelbase.dll');
  const getModule=new NativeFunction(kernel.getExportByName('GetModuleHandleW'),'pointer',['pointer']);
  const getName=new NativeFunction(kernel.getExportByName('GetModuleFileNameW'),'uint',['pointer','pointer','uint']);
  const rust=getModule(Memory.allocUtf16String('valmax_core.dll'));
  if(!rust.isNull()) {
    const out=Memory.alloc(65536),used=getName(rust,out,32768);
    if(used>0&&used<32768){const path=out.readUtf16String(used).replace(/\\/g,'/').toLowerCase();state.rust=path===MONITOR.rust.toLowerCase();state.rustModulePath=path;}
  }
  for(const module of Process.enumerateModules()) if(module.name.toLowerCase()==='valmax_core.dll') {
    const path=module.path.replace(/\\/g,'/').toLowerCase();
    state.rust=path===MONITOR.rust.toLowerCase();
    state.rustModulePath=path;
  }
  return {stats:{tracerHooks:0,transportHooks:0},tracerCounts:{},connections:{},packets:[],omittedPackets:0,omittedBytes:0,failures:0,native:state};
}
function wait(){recv('privacy-snapshot',m=>{send({kind:'privacy-snapshot',requestId:m.requestId,snapshot:snapshot()});wait();});}wait();
rpc.exports={snapshot};
'''


def merged(specs):
    result = {}
    for spec in specs:
        current = result.setdefault(spec["relative_path"], {"relative_path": spec["relative_path"], "sha256": spec["sha256"], "patches": []})
        assert current["sha256"] == spec["sha256"]
        current["patches"].extend(spec["patches"])
    return list(result.values())


def prepare(build: Path, rust: Path):
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    directory = BUILD_ROOT / ("probe-" + uuid.uuid4().hex)
    directory.mkdir()
    namespace = "N" + uuid.uuid4().hex[:4].upper()
    binary = (BASELINE / "max.exe").read_bytes()
    sources, _, bundle, proof = build_native_qml.prepare(binary, directory / "generated", native_runtime=True,
                                                        rcc=Path("C:/ValMAXBuild/Qt/bin/rcc.exe"))
    key = ":/qt/qml/OneMeUi/MeWindow.qml"
    window = sources[key].decode()
    old = '    title: "ValMAX" + (QmlUtils.showFpsCounter ? "; fps: " + Math.round(fpsCounter.fps) : "")'
    assert window.count(old) == 1
    window = window.replace(old, "    title: nativeQaTitle", 1)
    window = window.replace("import QtQuick\n", 'import QtQuick\nimport "qrc:/valmax" as ValMAXQa\n', 1)
    window = window.replace("    id: root\n", "    id: root\n" + QA_QML, 1)
    sources[key] = window.encode()
    spec, _ = make_resource_spec(binary, sources, icon_bundle=bundle)
    base = deepcopy(build_mod.specifications(["accounts", "privacy", "isolation"]))
    for entry in base:
        for patch in entry["patches"]:
            if patch["id"].startswith("isolation."):
                data = bytes.fromhex(patch["replacement"])
                replacement = data.replace(b"MXMOD", namespace.encode()).replace(b"mxmod", namespace.lower().encode()).replace("MXMOD".encode("utf-16le"), namespace.encode("utf-16le"))
                assert len(replacement) == len(data)
                patch["replacement"] = replacement.hex()
    prepared = build_mod.prepare(BASELINE, merged(base + [spec]))
    runtime = directory / "runtime"
    shutil.copytree(BASELINE, runtime)
    for record, data in prepared:
        assert build_mod.sha(build_mod.patch_bytes(data, record["patches"], reverse=True)) == record["sha256"]
        build_mod.atomic_replace_bytes(runtime / record["relative_path"], data, ".native-probe")
    plugin = runtime / "qml/ValMAX/Native"
    plugin.mkdir(parents=True)
    for name in ("qmldir", "valmaxnativeplugin.dll", "SettingsTransport.qml", "ChatLockTransport.qml"):
        shutil.copy2(build / "qml/ValMAX/Native" / name, plugin / name)
    shutil.copy2(rust, runtime / "valmax_core.dll")
    (runtime / "optimization-active.json").write_text(json.dumps({"schema": 1, "settings": {name: True for name in __import__("optimization_store").KEYS}}))
    manifest = {"format": 1, "max_version": "26.35.1.80662", "baseline": str(BASELINE), "destination": str(runtime),
                "components": ["accounts", "privacy", "isolation", "owned_native_qa"], "files": [record for record, _ in prepared]}
    (runtime / "mod-manifest.json").write_text(json.dumps(manifest, indent=2))
    build_mod.verify(runtime / "mod-manifest.json")
    report = {"schema": 1, "directory": str(directory), "namespace": namespace,
              "pluginSha256": build_mod.sha((plugin / "valmaxnativeplugin.dll").read_bytes()),
              "rustSha256": build_mod.sha(rust.read_bytes()), "fullyReversedNativeFiles": len(prepared),
              "qtDllHashes": {name: build_mod.sha((runtime / name).read_bytes()) for name in ("Qt6Core.dll", "Qt6Qml.dll", "Qt6Quick.dll")},
              "nativeStarted": False, "nativeQml": proof["extraResourceHashes"]}
    (directory / "build-proof.json").write_text(json.dumps(report, indent=2))
    return directory


class OwnedProbe(Probe):
    def __init__(self, directory, output, namespace):
        runtime = directory / "runtime"
        saved = probe_module.specifications
        try:
            probe_module.specifications = lambda _root, service: {"tracerCallsites": [], "plugin": (runtime / "qml/ValMAX/Native/valmaxnativeplugin.dll").as_posix(), "rust": (runtime / "valmax_core.dll").as_posix()}
            super().__init__(runtime / "max.exe", output, SnapshotChannel)
        finally:
            probe_module.specifications = saved
        self.monitor_source = MONITOR
        self.hooks = {}
        self.ready = set()
        self.native = {}
        self.namespace = namespace
        self.guard_checks = []
        anchor = r"  const profile = GUARD.hostProfile.replace(/\//g, '\\').toLowerCase();"
        assert self.guard_source.count(anchor) == 1
        self.guard_source = self.guard_source.replace(anchor, "  if(normal.split('\\\\').some(part=>['oneme','mxmod'].includes(part))) return true;\n" + anchor)

    def on_child(self, child):
        if getattr(child, "parent_pid", None) in self.owned:
            super().on_child(child)

    def guard_message(self, pid, message, data):
        value = message.get("payload")
        if type(value) is dict:
            if value.get("type") == "hook" and value.get("name") in REQUIRED:
                self.hooks.setdefault(pid, set()).add(value["name"])
            if value.get("type") == "guard-ready":
                self.ready.add(pid)
        super().guard_message(pid, message, data)

    def instrument(self, pid, service):
        super().instrument(pid, service)
        if pid not in self.ready or not REQUIRED <= self.hooks.get(pid, set()):
            raise RuntimeError("Required guards unavailable before resume")
        self.record("all_required_guards_before_resume", pid, passed=True)

    def consume_snapshot(self, pid, snapshot):
        super().consume_snapshot(pid, snapshot)
        if not self.sessions[pid]["service"]:
            self.native = snapshot.get("native", {})
        value = self.sessions[pid]["guardChannel"].snapshot(timeout=5)["summary"]
        expected = {"appFolder": self.namespace, "instance": self.namespace.lower() + "_instance", "pipe": self.namespace.lower() + "_local_pipe"}
        names = value.get("namespaceValues", {})
        if any(name in names and names[name] != text for name, text in expected.items()):
            raise RuntimeError("Unexpected namespace")
        self.guard_checks.append({"namespaceVerified": all(names.get(k) == v for k, v in expected.items()),
                                  "qtPathsRedirected": value.get("counters", {}).get("qt_path_redirected", 0),
                                  "requiredHookCount": len(self.hooks.get(pid, set()))})


def run(directory):
    directory = directory.resolve(strict=True)
    assert directory.is_relative_to(BUILD_ROOT.resolve()) and directory.name.startswith("probe-")
    build = json.loads((directory / "build-proof.json").read_text())
    build_mod.verify(directory / "runtime/mod-manifest.json")
    output = directory / ("run-" + uuid.uuid4().hex)
    prepare_profile(output)
    original = output / "profile/qt/11/MXMOD/config.json"
    config = json.loads(original.read_text())
    namespace = build["namespace"]
    config.update(app_folder_name=namespace, single_instance_id=namespace.lower() + "_instance",
                  local_server_pipe_name=namespace.lower() + "_local_pipe", client_friendly_name="ValMAX Native OFFLINE QA", is_qrcode_login_first=False)
    target = original.parent.with_name(namespace)
    target.mkdir()
    (target / "config.json").write_text(json.dumps(config))
    original.unlink()
    original.parent.rmdir()
    ctypes.windll.kernel32.SetErrorMode(0x8003)
    os.environ.update(QT_QPA_PLATFORM="windows", QT_DEBUG_PLUGINS="1", QML_XHR_ALLOW_FILE_READ="1")
    probe = OwnedProbe(directory, output, namespace)
    result = probe.run(15)
    proof = {"schema": 1, "guardFailed": result["guardFailed"], "namespace": namespace,
             "offline": True, "anonymous": True, "realProfileRead": False, "userInputSent": False,
             "native": probe.native, "guards": probe.guard_checks, "build": build,
             "temporaryGuardIsDevToolOnly": True, "installedCopyChanged": False}
    proof["verified"] = not proof["guardFailed"] and all(probe.native.get(key) is True for key in ("bridge", "rows", "scroll", "pool", "plugin", "rust", "pluginOwned")) and any(item["namespaceVerified"] and item["qtPathsRedirected"] > 0 for item in probe.guard_checks)
    (PROJECT / "analysis/native-migration-v1/qt-max-engine-proof.json").write_text(json.dumps(proof, indent=2))
    print(json.dumps({k: proof[k] for k in ("verified", "guardFailed", "native", "namespace")}))
    return 0 if proof["verified"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "run"))
    parser.add_argument("--build", type=Path, default=Path("C:/ValMAXBuild/build/qt-bridge"))
    parser.add_argument("--rust-dll", type=Path, default=Path("C:/ValMAXBuild/target/release/valmax_core.dll"))
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    if args.mode == "prepare":
        print(json.dumps({"directory": str(prepare(args.build, args.rust_dll)), "nativeStarted": False}))
    else:
        raise SystemExit(run(args.directory))
