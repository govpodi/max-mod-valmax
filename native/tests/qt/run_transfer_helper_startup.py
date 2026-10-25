"""Launch only our packaged production helper, inspect its own idle window, close it."""
from __future__ import annotations

import argparse
import ctypes as c
from ctypes import wintypes as w
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("helper_startup_job", Path(__file__).with_name("run_webview_blank_native.py"))
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def sha(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


user = c.WinDLL("user32", use_last_error=True)
callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
user.EnumWindows.argtypes = [callback_type, w.LPARAM]
user.EnumWindows.restype = w.BOOL
user.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
user.GetWindowThreadProcessId.restype = w.DWORD
user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
user.GetWindowTextW.restype = c.c_int
user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user.PostMessageW.restype = w.BOOL


def own_window(pid):
    found = []

    @callback_type
    def visit(window, _):
        owner = w.DWORD()
        user.GetWindowThreadProcessId(window, c.byref(owner))
        # No other application's title or UI is read.
        if owner.value == pid:
            title = c.create_unicode_buffer(256)
            user.GetWindowTextW(window, title, len(title))
            if title.value == "ValMAX · Перенос аккаунтов":
                found.append(window)
        return True

    require(user.EnumWindows(visit, 0), "window enumeration failed")
    require(len(found) <= 1, "duplicate helper window")
    return found[0] if found else None


class Module(c.Structure):
    _fields_ = [("size", w.DWORD), ("moduleId", w.DWORD), ("pid", w.DWORD), ("globalCount", w.DWORD),
                ("processCount", w.DWORD), ("base", c.c_void_p), ("bytes", w.DWORD), ("handle", w.HMODULE),
                ("name", w.WCHAR * 256), ("path", w.WCHAR * 260)]


snapshot = job.bind("CreateToolhelp32Snapshot", w.HANDLE, [w.DWORD, w.DWORD])
module_first = job.bind("Module32FirstW", w.BOOL, [w.HANDLE, c.POINTER(Module)])
module_next = job.bind("Module32NextW", w.BOOL, [w.HANDLE, c.POINTER(Module)])
VC_MODULES = frozenset(("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
                        "vcruntime140.dll", "vcruntime140_1.dll"))


def owned_modules(pid, package, file_hashes):
    wanted = {"valmax_core.dll", "qt6core.dll", "qt6qml.dll", "qt6quick.dll", "qwindows.dll"}
    expected_hashes = {name.lower(): digest for name, digest in file_hashes.items()}
    result = {}
    handle = snapshot(0x18, pid)
    require(handle and handle != c.c_void_p(-1).value, "module snapshot failed")
    try:
        entry = Module()
        entry.size = c.sizeof(entry)
        require(module_first(handle, c.byref(entry)), "module enumeration failed")
        count = 0
        while True:
            count += 1
            require(count <= 1024, "module bound exceeded")
            name = entry.name.lower()
            require(not name.startswith(("python", "frida")), "unexpected runtime module")
            if name in wanted or name in VC_MODULES:
                relative = "plugins/platforms/qwindows.dll" if name == "qwindows.dll" else name
                expected = package / relative
                path = Path(entry.path)
                require(path.resolve() == expected.resolve(), "dependency outside package")
                digest = sha(path)
                require(expected_hashes.get(relative) == digest, "loaded dependency differs from package manifest")
                result[name] = {"path": str(path), "sha256": digest}
            if not module_next(handle, c.byref(entry)):
                require(c.get_last_error() == 18, "incomplete module enumeration")
                break
        require(wanted <= set(result), "required modules missing")
    finally:
        job.close(handle)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-proof", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--work-root", type=Path, choices=(Path("C:/ValMAXBuild"), Path("D:/ValMAXBuild")),
                        default=Path("C:/ValMAXBuild"))
    parser.add_argument("--render-backend", choices=("default", "software"), default="software",
                        help="Software preserves the historical scenario; default does not override Qt graphics.")
    args = parser.parse_args()
    require(args.report.resolve().parent == (ROOT / "analysis/native-migration-v1").resolve()
            and not args.report.exists(), "new proof required under analysis directory")
    package_proof = json.loads(args.package_proof.read_text("utf8"))
    package_proof_hash = sha(args.package_proof)
    source_hashes = {str(path.relative_to(ROOT)): sha(path) for path in
                     (Path(__file__), Path(__file__).with_name("run_webview_blank_native.py"))}
    package = Path(package_proof["package"])
    require(any(package.resolve().is_relative_to((root / "packages").resolve())
                for root in (Path("C:/ValMAXBuild"), Path("D:/ValMAXBuild"))), "not an owned package")
    for name, digest in package_proof["fileHashes"].items():
        require(sha(package / name) == digest, "package changed")
    exe = package / "valmax-transfer.exe"
    fixture = args.work_root / "build" / ("transfer-helper-startup-" + uuid.uuid4().hex)
    for name in ("home", "local", "roaming", "temp"):
        (fixture / name).mkdir(parents=True)
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SystemDrive", "COMSPEC", "PATHEXT") if key in os.environ}
    env.update(PATH=str(package) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32"),
               QT_QPA_PLATFORM="windows", QML_DISABLE_DISK_CACHE="1",
               USERPROFILE=str(fixture / "home"), LOCALAPPDATA=str(fixture / "local"),
               APPDATA=str(fixture / "roaming"), TEMP=str(fixture / "temp"), TMP=str(fixture / "temp"))
    if args.render_backend == "software":
        env["QT_QUICK_BACKEND"] = "software"
    block = c.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(env.items())) + "\0\0")
    command = c.create_unicode_buffer(subprocess.list2cmdline([str(exe)]))
    process = job.PROCESS()
    handle = None
    outputs = []
    assigned = False
    remaining = None
    code = w.DWORD(259)
    failure = None
    modules = {}
    window_ready = False
    close_owner_verified = False
    pre_resume_cleanup = None
    started = time.monotonic()
    c.windll.kernel32.SetErrorMode(0x8003)
    try:
        handle = job.create_job(None, None)
        require(handle, "job creation failed")
        limits = job.LIMITS()
        limits.basic.flags = 0x2008
        limits.basic.processes = 1
        require(job.set_job(handle, 9, c.byref(limits), c.sizeof(limits)), "job limit failed")
        security = job.SA(c.sizeof(job.SA), None, True)
        for path in ("NUL", str(fixture / "stdout.log"), str(fixture / "stderr.log")):
            output = job.create_file(path, 0x80000000 if path == "NUL" else 0x40000000,
                                     1, c.byref(security), 3 if path == "NUL" else 2, 0x80, None)
            require(output and output != c.c_void_p(-1).value, "log handle failed")
            outputs.append(output)
        startup = job.STARTUP()
        startup.cb = c.sizeof(startup)
        startup.flags = 0x101
        startup.show = 0
        startup.stdin, startup.stdout, startup.stderr = outputs
        # Working directory is deliberately unrelated to the installed bundle.
        require(job.create_process(str(exe), command, None, None, True, 0x08000404, block,
                                   str(fixture), c.byref(startup), c.byref(process)), "create failed")
        actual, length = c.create_unicode_buffer(32768), w.DWORD(32768)
        require(job.query_path(process.process, 0, actual, c.byref(length)), "owned path query failed")
        require(Path(actual.value).resolve() == exe.resolve(), "unexpected executable")
        require(job.assign(handle, process.process), "assign before resume failed")
        assigned = True
        require(job.resume(process.thread) != 0xffffffff, "resume failed")
        deadline = time.monotonic() + 12
        window = None
        while time.monotonic() < deadline and job.wait(process.process, 0) == 258:
            window = own_window(process.pid)
            if window:
                break
            time.sleep(.02)
        require(window, "production QML window did not become ready")
        window_ready = True
        modules = owned_modules(process.pid, package, package_proof["fileHashes"])
        owner = w.DWORD()
        user.GetWindowThreadProcessId(window, c.byref(owner))
        require(job.wait(process.process, 0) == 258 and owner.value == process.pid,
                "owned window changed before close")
        close_owner_verified = True
        require(user.PostMessageW(window, 0x10, 0, 0), "owned window close failed")
        require(job.wait(process.process, 10000) == 0, "idle helper did not close")
        require(job.exit_code(process.process, c.byref(code)) and code.value == 0, "helper exit failed")
    except Exception as error:
        failure = str(error)
    finally:
        if handle:
            job.terminate_job(handle, 91)
            for _ in range(50):
                pids = job.PIDS()
                if job.query_job(handle, 3, c.byref(pids), c.sizeof(pids), None):
                    remaining = pids.listed
                    if not remaining:
                        break
                time.sleep(.02)
        if process.process and not assigned:
            terminated = bool(job.bind("TerminateProcess", w.BOOL, [w.HANDLE, w.UINT])(process.process, 91))
            waited = job.wait(process.process, 3000)
            pre_resume_cleanup = {"terminateSucceeded": terminated, "processExited": waited == 0}
            if waited != 0:
                failure = (failure + "; " if failure else "") + "pre-resume cleanup failed"
        for item in (process.thread, process.process, *outputs, handle):
            if item:
                job.close(item)
    process_proof = {"exitCode": code.value, "failure": failure, "assignedBeforeResume": assigned,
                     "remainingProcesses": remaining, "windowReady": window_ready,
                     "closeOwnerVerified": close_owner_verified, "preResumeCleanup": pre_resume_cleanup,
                     "elapsedSeconds": round(time.monotonic() - started, 3)}
    (fixture / "process.json").write_text(json.dumps(process_proof, indent=2), encoding="utf8")
    require(failure is None and remaining == 0, "startup failed; see " + str(fixture / "process.json"))
    for name, digest in package_proof["fileHashes"].items():
        require(sha(package / name) == digest, "package changed during startup")
    require(sha(args.package_proof) == package_proof_hash, "package receipt changed during startup")
    for name, digest in source_hashes.items():
        require(sha(ROOT / name) == digest, "runner changed during startup")
    proof = {"schema": 1, "verified": True, "fixture": str(fixture), "process": process_proof,
             "packageProof": str(args.package_proof), "packageProofSha256": package_proof_hash,
             "sourceHashes": source_hashes,
             "loadedModules": modules, "noSdkOrPythonInPath": True, "unrelatedWorkingDirectory": True,
             "renderBackendRequested": args.render_backend,
             "qtQuickBackendOverride": env.get("QT_QUICK_BACKEND"), "renderedFrameVerified": False,
             "vcModulesLoaded": sorted(VC_MODULES & modules.keys()),
             "vcModulesNotObserved": sorted(VC_MODULES - modules.keys()),
             "vcLoadedPathsAndHashesVerified": True,
             "fileSelected": False, "importRequested": False, "installedClientChanged": False,
             "scope": "Unmodified production EXE idle startup and graceful close in its own Job; no arguments, selected archive or import."}
    with args.report.open("x", encoding="utf8") as output:
        output.write(json.dumps(proof, indent=2) + "\n")
    print(json.dumps({"verified": True, "report": str(args.report), "exitCode": code.value}))


if __name__ == "__main__":
    main()
