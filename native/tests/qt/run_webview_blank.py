"""Owned about:blank using MAX Qt and one Microsoft Fixed Runtime directory.

Development-only pre-resume Frida guards deny network, credentials, host
profile files and registry writes in the harness and every allowed child.
This is instrumentation, not an OS sandbox or a production observer.
"""
from __future__ import annotations
import ctypes
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import sys
import tempfile
import threading
import time

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT / "modkit"))
import frida
from privacy_monitor_probe import OwnedProcess, redacted_guard_source
from privacy_rpc import SnapshotChannel

SOURCE = Path("C:/ValMAXBuild/build/qt-probes/qt-max-runtime-2tq1c3sb")
BUILD = Path("C:/ValMAXBuild/build/qt-webview-bridge")
BUNDLE = Path("C:/ValMAXBuild/deps/webview2/verification.json")
REQUIRED = {"NtCreateFile", "NtOpenFile", "CredReadW", "CredWriteW", "CredDeleteW", "CredEnumerateW"}


def digest(path):
    return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()


def main():
    ctypes.windll.kernel32.SetErrorMode(0x8003)
    bundle = json.loads(BUNDLE.read_text())
    if bundle.get("verified") is not True: raise RuntimeError("unverified_bundle")
    browser_root = Path(bundle["runtime"]).resolve(strict=True)
    browser = browser_root / "msedgewebview2.exe"
    expected = next(item["sha256"] for item in bundle["signatures"] if item["name"] == browser.name)
    if digest(browser) != expected: raise RuntimeError("browser_hash_mismatch")
    fixture = Path(tempfile.mkdtemp(prefix="qt-webview-blank-", dir="C:/ValMAXBuild/build"))
    for path in SOURCE.glob("*.dll"):
        if not path.name.startswith("valmax_"): os.link(path, fixture / path.name)
    for name in ("qml", "plugins"):
        shutil.copytree(SOURCE / name, fixture / name, copy_function=os.link,
            ignore=lambda path, names: [name for name in names if name == "ValMAX"])
    shutil.copy2(SOURCE / "qt.conf", fixture / "qt.conf")
    exe = fixture / "valmax_webview_blank_test.exe"
    shutil.copy2(BUILD / exe.name, exe)
    qml = fixture / "test.qml"
    shutil.copy2(Path(__file__).with_name("webview_blank.qml"), qml)
    profile = fixture / "profile"
    for name in ("roaming", "local", "home", "temp", "MXMOD/business-webview2", *[f"qt/{i}" for i in range(32)]):
        (profile / name).mkdir(parents=True, exist_ok=True)
    user_data = profile / "MXMOD/business-webview2"
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("QT_", "QML", "WEBVIEW2_", "MAX_MOD", "MXMOD", "VALMAX_NATIVE", "PYTHON"))}
    env.update(PATH=str(fixture) + os.pathsep + os.path.join(os.environ["SystemRoot"], "System32"),
        QT_QPA_PLATFORM="windows", QT_DEBUG_PLUGINS="1", QT_QUICK_BACKEND="software", QML_DISABLE_DISK_CACHE="1",
        QT_WEBVIEW_PLUGIN="webview2", WEBVIEW2_BROWSER_EXECUTABLE_FOLDER=str(browser_root),
        WEBVIEW2_USER_DATA_FOLDER=str(user_data),
        WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS="--disable-background-networking --disable-component-update --disable-sync --no-first-run --no-default-browser-check --disable-gpu",
        APPDATA=str(profile / "roaming"), LOCALAPPDATA=str(profile / "local"), USERPROFILE=str(profile / "home"),
        TEMP=str(profile / "temp"), TMP=str(profile / "temp"))
    guard_source = redacted_guard_source({"profile": profile.as_posix(), "hostProfile": os.environ["USERPROFILE"], "offline": True})
    anchor = r"  const profile = GUARD.hostProfile.replace(/\//g, '\\').toLowerCase();"
    if guard_source.count(anchor) != 1: raise RuntimeError("guard_anchor_changed")
    guard_source = guard_source.replace(anchor,
        "  if (normal === '\\\\device\\\\afd' || normal.startsWith('\\\\device\\\\afd\\\\')) { count('afd_open_denied'); return true; }\n" + anchor)
    guard_source += "\nif (!protectedPath('\\\\Device\\\\Afd\\\\Endpoint')) throw Error('AFD guard unavailable');\n"
    device = frida.get_local_device()
    owned, sessions, hooks, detached = {}, {}, {}, set()
    messages = queue.Queue()
    stopped = threading.Event()
    outputs = {1: bytearray(), 2: bytearray()}
    failures, records, snapshots = [], [], []
    main_pid = None

    def child_added(child):
        if child.parent_pid not in owned: return
        try:
            owned[child.pid] = OwnedProcess(child.pid)
            if stopped.is_set() or not child.path or Path(child.path).resolve(strict=True) != browser:
                owned[child.pid].stop()
                failures.append("unexpected_child")
                stopped.set()
                return
            messages.put(child)
        except Exception:
            failures.append("child_guard_failed")
            stopped.set()
            device.kill(child.pid)

    def output(pid, fd, value):
        if pid == main_pid and fd in outputs and value and len(outputs[fd]) < 256 * 1024:
            outputs[fd].extend(value[:256 * 1024 - len(outputs[fd])])

    def instrument(pid):
        session = device.attach(pid)
        sessions[pid] = {"session": session}
        session.enable_child_gating()
        session.on("detached", lambda reason, crash=None: detached.add(pid))
        script = session.create_script(guard_source)
        def message(value, data):
            payload = value.get("payload")
            if value.get("type") == "error" or isinstance(payload, dict) and payload.get("type") == "guard-error":
                failures.append("guard_failed")
                stopped.set()
            elif isinstance(payload, dict) and payload.get("type") == "hook":
                hooks.setdefault(pid, set()).add(payload.get("name"))
        script.on("message", message)
        script.load()
        channel = SnapshotChannel(script)
        sessions[pid].update(script=script, channel=channel)
        result = channel.snapshot(timeout=3)
        if stopped.is_set() or not REQUIRED <= hooks.get(pid, set()) or result["selftest"].get("credentialReadDenied") is not True:
            raise RuntimeError("guard_selftest_failed")
        if result["summary"]["counters"].get("afd_open_denied", 0) < 1: raise RuntimeError("afd_selftest_failed")
        records.append({"pid": pid, "requiredGuardsBeforeResume": True, "afdDenied": True, "credentialDenied": True})

    device.on("child-added", child_added)
    device.on("output", output)
    try:
        main_pid = device.spawn([str(exe), str(qml)], cwd=str(fixture), envp=env, stdio="pipe")
        owned[main_pid] = OwnedProcess(main_pid)
        instrument(main_pid)
        device.resume(main_pid)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline and not stopped.is_set() and main_pid not in detached:
            try: child = messages.get(timeout=.1)
            except queue.Empty: continue
            instrument(child.pid)
            if stopped.is_set(): break
            device.resume(child.pid)
        if main_pid not in detached and not failures: failures.append("deadline")
        for pid, item in sessions.items():
            if pid not in detached:
                value = item["channel"].snapshot(timeout=2)
                snapshots.append({"pid": pid, "counters": value["summary"]["counters"]})
    except Exception as error:
        failures.append(type(error).__name__)
    finally:
        stopped.set()
        for item in tuple(owned.values()): item.stop()
        device.off("child-added", child_added)
        device.off("output", output)
        for item in sessions.values():
            if "channel" in item: item["channel"].close()
            try: item["session"].detach()
            except Exception: pass
        for item in tuple(owned.values()): item.close()
    (fixture / "stderr.log").write_bytes(outputs[2])
    (fixture / "stdout.log").write_bytes(outputs[1])
    try: result = json.loads(outputs[1])
    except (ValueError, UnicodeDecodeError): result = {}
    proof = {"schema": 1, "verified": not failures and result.get("ok") is True and result.get("checks") == 2
        and result.get("url") == "about:blank" and len(owned) > 1,
        "fixture": str(fixture), "fixedRuntime": str(browser_root), "runtimeVersion": bundle["version"],
        "runtimeCabSha256": bundle["cabSha256"], "runtimeCopiedForTest": False,
        "browserSha256": expected, "ownedProcesses": len(owned), "guardFailures": failures,
        "guards": records, "snapshots": snapshots, "result": result,
        "userDataOwned": str(user_data), "userDataFilesCreated": sum(1 for p in user_data.rglob("*") if p.is_file()),
        "actualBusinessUrlLoaded": False, "realProfileRead": False, "userInstallationModified": False,
        "devGuardNotOsSandbox": True, "networkDeniedBeforeEveryResume": len(records) == len(owned),
        "qtHashes": {name: digest(fixture / name) for name in ("Qt6Core.dll", "Qt6Qml.dll", "Qt6WebView.dll", "plugins/webview/qtwebview_webview2.dll")}}
    (PROJECT / "analysis/native-migration-v1/qt-webview2-blank-proof.json").write_text(json.dumps(proof, indent=2))
    print(json.dumps({key: proof[key] for key in ("verified", "fixture", "guardFailures", "ownedProcesses", "result")}))
    return 0 if proof["verified"] else 1


if __name__ == "__main__": raise SystemExit(main())
