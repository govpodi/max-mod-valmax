"""Normal sandboxed Fixed WebView2, owned job + empty synthetic browser profile.

No Frida injection and no sandbox-disable flags. Chromium may perform its own
background requests; this is a dependency/ABI check, not network-absence proof.
"""
import ctypes as c
import argparse
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
kernel = c.WinDLL("kernel32", use_last_error=True)
class SA(c.Structure): _fields_ = [("length", w.DWORD), ("descriptor", w.LPVOID), ("inherit", w.BOOL)]
class STARTUP(c.Structure):
    _fields_ = [("cb", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR), ("title", w.LPWSTR),
        ("x", w.DWORD), ("y", w.DWORD), ("xs", w.DWORD), ("ys", w.DWORD), ("xc", w.DWORD), ("yc", w.DWORD),
        ("fill", w.DWORD), ("flags", w.DWORD), ("show", w.WORD), ("reserved2", w.WORD), ("bytes", c.POINTER(c.c_byte)),
        ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE)]
class PROCESS(c.Structure): _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]
class BASIC(c.Structure):
    _fields_ = [("process_time", c.c_int64), ("job_time", c.c_int64), ("flags", w.DWORD),
        ("min_ws", c.c_size_t), ("max_ws", c.c_size_t), ("processes", w.DWORD), ("affinity", c.c_size_t),
        ("priority", w.DWORD), ("scheduling", w.DWORD)]
class IO(c.Structure): _fields_ = [(name, c.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read", "write", "other")]
class LIMITS(c.Structure): _fields_ = [("basic", BASIC), ("io", IO), ("process_mem", c.c_size_t), ("job_mem", c.c_size_t), ("peak_process", c.c_size_t), ("peak_job", c.c_size_t)]
class PIDS(c.Structure): _fields_ = [("assigned", w.DWORD), ("listed", w.DWORD), ("pids", c.c_size_t * 128)]


def bind(name, result, args):
    f = getattr(kernel, name); f.restype = result; f.argtypes = args; return f


create_job = bind("CreateJobObjectW", w.HANDLE, [w.LPVOID, w.LPCWSTR])
set_job = bind("SetInformationJobObject", w.BOOL, [w.HANDLE, c.c_int, w.LPVOID, w.DWORD])
query_job = bind("QueryInformationJobObject", w.BOOL, [w.HANDLE, c.c_int, w.LPVOID, w.DWORD, w.LPVOID])
assign = bind("AssignProcessToJobObject", w.BOOL, [w.HANDLE, w.HANDLE])
terminate_job = bind("TerminateJobObject", w.BOOL, [w.HANDLE, w.UINT])
close = bind("CloseHandle", w.BOOL, [w.HANDLE])
create_file = bind("CreateFileW", w.HANDLE, [w.LPCWSTR, w.DWORD, w.DWORD, c.POINTER(SA), w.DWORD, w.DWORD, w.HANDLE])
create_process = bind("CreateProcessW", w.BOOL, [w.LPCWSTR, w.LPWSTR, w.LPVOID, w.LPVOID, w.BOOL, w.DWORD, w.LPVOID, w.LPCWSTR, c.POINTER(STARTUP), c.POINTER(PROCESS)])
resume = bind("ResumeThread", w.DWORD, [w.HANDLE])
wait = bind("WaitForSingleObject", w.DWORD, [w.HANDLE, w.DWORD])
exit_code = bind("GetExitCodeProcess", w.BOOL, [w.HANDLE, c.POINTER(w.DWORD)])
open_process = bind("OpenProcess", w.HANDLE, [w.DWORD, w.BOOL, w.DWORD])
query_path = bind("QueryFullProcessImageNameW", w.BOOL, [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)])


def require(value, label):
    if not value: raise RuntimeError(f"{label}:{c.get_last_error()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("good", "missing-runtime", "blocked-data", "blocked-child", "unset-data", "timeout", "delete-on-signal", "failed-controller", "failed-controller-delete"), default="good")
    parser.add_argument("--guarded-backend", action="store_true")
    args = parser.parse_args()
    if args.mode != "good" and not args.guarded_backend: raise RuntimeError("negative_requires_guarded_backend")
    bundle = json.loads(Path("C:/ValMAXBuild/deps/webview2/verification.json").read_text())
    browser_root = Path(bundle["runtime"]).resolve(strict=True)
    # Reuse only our completed failed-probe Qt files; fresh profile per run.
    fixture = Path("C:/ValMAXBuild/build/qt-webview-blank-eifyoipa").resolve(strict=True)
    exe = fixture / "valmax_webview_blank_test.exe"
    if args.mode in ("delete-on-signal", "failed-controller-delete"):
        import shutil
        exe = fixture / "valmax_webview_delete_test.exe"
        shutil.copy2("C:/ValMAXBuild/build/qt-webview-guard/valmax_webview_delete_test.exe",exe)
    if args.guarded_backend:
        import shutil
        target = fixture / "plugins/webview/qtwebview_webview2.dll"
        # The previous fixture file was hard-linked to immutable MAX Qt files.
        # Unlink this owned directory entry before writing a replacement.
        target.unlink()
        guard_source = Path("C:/ValMAXBuild/build/qt-webview-guard")
        if args.mode != "good": guard_source /= "timeout-test"
        shutil.copy2(guard_source / "qtwebview_webview2.dll", target)
    profile = fixture / ("native-profile-" + uuid.uuid4().hex)
    for name in ("home", "local", "roaming", "temp", "MXMOD/business-webview2"): (profile / name).mkdir(parents=True)
    userdata = profile / "MXMOD/business-webview2"
    qml = fixture / "test.qml"
    if args.mode == "timeout":
        qml = Path(__file__).with_name("webview_timeout.qml")
    runtime_input = browser_root
    if args.mode == "missing-runtime": runtime_input = profile / "missing-browser"
    if args.mode == "blocked-data":
        userdata = profile / "blocked-data-file"
        userdata.write_text("synthetic file, not a directory")
    if args.mode == "blocked-child": (userdata/"EBWebView").write_text("synthetic blocked browser subdirectory")
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith(("QT_", "QML", "WEBVIEW2_", "MAX_MOD", "MXMOD", "VALMAX_NATIVE", "PYTHON"))}
    env.update(PATH=str(fixture) + os.pathsep + os.path.join(os.environ["SystemRoot"], "System32"),
        QT_QPA_PLATFORM="windows", QT_DEBUG_PLUGINS="1", QT_QUICK_BACKEND="software", QML_DISABLE_DISK_CACHE="1",
        QT_WEBVIEW_PLUGIN="webview2", WEBVIEW2_BROWSER_EXECUTABLE_FOLDER=str(runtime_input),
        WEBVIEW2_USER_DATA_FOLDER=str(userdata),
        WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS="--disable-background-networking --disable-component-update --disable-sync --no-first-run --no-default-browser-check --disable-gpu",
        APPDATA=str(profile / "roaming"), LOCALAPPDATA=str(profile / "local"), USERPROFILE=str(profile / "home"),
        TEMP=str(profile / "temp"), TMP=str(profile / "temp"))
    if args.mode == "unset-data": del env["WEBVIEW2_USER_DATA_FOLDER"]
    if args.mode in ("failed-controller", "failed-controller-delete"):
        env["VALMAX_WEBVIEW_FAULT"] = "failed-controller"
    environment = c.create_unicode_buffer("\0".join(f"{k}={v}" for k, v in sorted(env.items(), key=lambda x: x[0].upper())) + "\0\0")
    job, pi, handles, paths, failures = None, PROCESS(), [], set(), []
    owned_pids, remaining = set(), None
    job_assigned = False
    code = w.DWORD(259)
    started = time.monotonic()
    try:
        job = create_job(None, None); require(job, "create_job")
        limits = LIMITS(); limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE; no BREAKAWAY.
        require(set_job(job, 9, c.byref(limits), c.sizeof(limits)), "set_job")
        sa = SA(c.sizeof(SA), None, True)
        for path in ("NUL", str(profile / "stdout.log"), str(profile / "stderr.log")):
            handle = create_file(path, 0x80000000 if path == "NUL" else 0x40000000, 1, c.byref(sa), 3 if path == "NUL" else 2, 0x80, None)
            require(handle and handle != c.c_void_p(-1).value, "create_output")
            handles.append(handle)
        startup = STARTUP(); startup.cb = c.sizeof(startup); startup.flags = 0x101; startup.show = 0
        startup.stdin, startup.stdout, startup.stderr = handles
        command = c.create_unicode_buffer(f'"{exe}" "{qml}"')
        require(create_process(str(exe), command, None, None, True, 0x08000404, environment, str(fixture), c.byref(startup), c.byref(pi)), "create_process")
        require(assign(job, pi.process), "assign_job_before_resume")
        job_assigned = True
        require(resume(pi.thread) != 0xFFFFFFFF, "resume")
        started = time.monotonic()
        deadline = started + 25
        while time.monotonic() < deadline:
            pids = PIDS()
            require(query_job(job, 3, c.byref(pids), c.sizeof(pids), None), "query_owned_job")
            for pid in pids.pids[:pids.listed]:
                owned_pids.add(pid)
                handle = open_process(0x1000, False, pid)
                if handle:
                    try:
                        output, size = c.create_unicode_buffer(32768), w.DWORD(32768)
                        if query_path(handle, 0, output, c.byref(size)): paths.add(output.value)
                    finally: close(handle)
            if wait(pi.process, 100) == 0: break
        require(exit_code(pi.process, c.byref(code)), "exit_code")
        if code.value == 259: failures.append("deadline")
    except Exception as error:
        failures.append(str(error))  # Only API label/code; no credential inputs.
    finally:
        if job:
            terminate_job(job, 1)
            for attempt in range(40):
                pids=PIDS()
                if query_job(job,3,c.byref(pids),c.sizeof(pids),None):
                    remaining=pids.listed
                    if remaining==0:break
                time.sleep(.05)
        # If assignment failed, terminate only the still-suspended owned handle.
        if pi.process and code.value == 259:
            terminate = bind("TerminateProcess", w.BOOL, [w.HANDLE, w.UINT]); terminate(pi.process, 1)
        for handle in (pi.thread, pi.process, *handles, job):
            if handle: close(handle)
    try: result = json.loads((profile / "stdout.log").read_text())
    except (OSError, ValueError): result = {}
    expected = {os.path.normcase(str(exe)), os.path.normcase(str(browser_root / "msedgewebview2.exe"))}
    allowed = expected | {os.path.normcase(os.path.join(os.environ["SystemRoot"], "System32", "conhost.exe"))}
    actual = {os.path.normcase(path) for path in paths}
    good = code.value == 0 and result.get("ok") is True and result.get("checks") == 2 and result.get("url") == "about:blank" and expected <= actual <= allowed
    negative = args.mode != "good" and code.value == 9 and result.get("failure") == "blank_load_failed" and actual <= allowed
    no_browser = os.path.normcase(str(browser_root / "msedgewebview2.exe")) not in actual
    fake_calls=(profile/"stderr.log").read_bytes().count(b"VALMAX_FAKE_ENV_CALLED")
    late_callbacks=(profile/"stderr.log").read_bytes().count(b"VALMAX_FAKE_ENV_CALLBACK")
    controller_closes=(profile/"stderr.log").read_bytes().count(b"VALMAX_FAKE_CONTROLLER_CLOSE")
    if args.mode == "timeout": negative = negative and result.get("checks") == 1 and no_browser and fake_calls==1 and late_callbacks==1
    if args.mode in ("blocked-data", "blocked-child", "unset-data", "missing-runtime"): negative = negative and no_browser and fake_calls==0
    if args.mode == "delete-on-signal": negative = code.value == 0 and result.get("checks") == 1 and result.get("ok") is True and no_browser
    if args.mode == "failed-controller": negative = negative and no_browser and fake_calls == 1 and controller_closes == 1
    if args.mode == "failed-controller-delete":
        negative = code.value == 0 and result.get("checks") == 1 and result.get("ok") is True and no_browser and fake_calls == 1 and controller_closes == 1
    proof = {"schema": 1, "verified": not failures and remaining==0 and job_assigned and (good if args.mode == "good" else negative),
        "mode": args.mode, "guardedBackend": args.guarded_backend,
        "negativeUsesSyntheticLoader":args.mode!="good","productionNoModalGuarantee":False,
        "ownedPids":sorted(owned_pids),"jobProcessesAfterTermination":remaining,"fakeLoaderCalls":fake_calls,
        "fakeLateCallbacks":late_callbacks,"fakeControllerCloses":controller_closes,"elapsedSeconds":round(time.monotonic()-started,3),
        "fixture": str(fixture), "profile": str(profile), "ownedExecutablePaths": sorted(paths),
        "failures": failures, "exitCode": code.value, "result": result, "jobAssignedBeforeResume": job_assigned,
        "fixedRuntime": str(browser_root), "version": bundle["version"], "cabSha256": bundle["cabSha256"],
        "runtimeCopiedForTest": False, "sandboxDisabled": False, "fridaUsed": False,
        "networkAbsenceProven": False, "backgroundWebViewRequestsPossible": True,
        "businessUrlLoaded": False, "userInstallationChanged": False, "realLoginUsed": False,
        "userDataDirectory": str(userdata), "userDataFiles": sum(1 for p in userdata.rglob("*") if p.is_file()),
        "qtHashes": {name: hashlib.sha256((fixture / name).read_bytes()).hexdigest() for name in
            ("Qt6Core.dll", "Qt6Qml.dll", "Qt6WebView.dll", "plugins/webview/qtwebview_webview2.dll")}}
    proof_name = ("qt-webview2-guard-" + args.mode + "-proof.json") if args.guarded_backend else "qt-webview2-blank-native-proof.json"
    (ROOT / "analysis/native-migration-v1" / proof_name).write_text(json.dumps(proof, indent=2))
    print(json.dumps({k: proof[k] for k in ("verified", "exitCode", "failures", "result", "ownedExecutablePaths")}))
    return 0 if proof["verified"] else 1

if __name__ == "__main__": raise SystemExit(main())
