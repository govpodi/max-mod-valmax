"""Run about:blank with QtWebView from a pruned runtime in an owned job.

The fixture and WebView2 data directory are under E:; the installed MAX and
its profiles are never started or opened. Browser children stay in a job that
is terminated before this script returns.
"""
from __future__ import annotations

import argparse
import ctypes as c
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "native/tooling"))
from assemble_native_runtime import (  # noqa: E402
    QT_WEBVIEW_RUNTIME_FILES, is_qt_webengine_runtime_path, read_json, require, sha,
)
import run_webview_blank_native as winjob  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-proof", required=True, type=Path)
    parser.add_argument("--import-proof", required=True, type=Path)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--harness", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    build_root = Path("E:/ValMAXBuild/build").resolve(strict=True)
    fixture = args.fixture.resolve(strict=True)
    require(fixture != build_root and fixture.is_relative_to(build_root), "owned E: fixture required")
    require(args.report.parent.resolve() == (ROOT / "analysis/native-migration-v1").resolve()
            and not args.report.exists(), "fresh local report required")
    assembly, imported = read_json(args.staging_proof), read_json(args.import_proof)
    require(assembly["assembled"] is True and assembly["qtWebEngineOmission"]["enabled"] is True
            and imported["stock"]["loaded"] is True and Path(imported["fixture"]).resolve() == fixture,
            "verified stock QtWebView import fixture required")
    webview2_mode = assembly.get("webview2Mode")
    require(webview2_mode in ("fixed", "evergreen"), "verified WebView2 mode required")
    runtime = Path(assembly["runtime"])
    rows = {row["path"]: row for row in assembly["inventory"]}
    require(not any(is_qt_webengine_runtime_path(path) for path in rows),
            "QtWebEngine family remains")
    for path in QT_WEBVIEW_RUNTIME_FILES:
        source = runtime / path
        destination = fixture / path.removeprefix("mod/")
        require(sha(source) == rows[path]["sha256"], "stage QtWebView changed: " + path)
        if path == "mod/qml/QtWebView/qmldir":
            # The earlier diagnostic also tried an altered qmldir. Restore the
            # byte-identical stock file for this functional run.
            shutil.copy2(source, destination)
        require(sha(destination) == rows[path]["sha256"], "fixture QtWebView changed: " + path)
    for name in ("Qt6Core.dll", "Qt6Qml.dll", "Qt6Quick.dll", "qt.conf"):
        require(sha(fixture / name) == rows["mod/" + name]["sha256"],
                "fixture Qt root changed: " + name)

    browser_root = runtime / "webview2" if webview2_mode == "fixed" else None
    browser = browser_root / "msedgewebview2.exe" if browser_root else None
    if browser is not None:
        require(sha(browser) == rows["webview2/msedgewebview2.exe"]["sha256"],
                "pinned fixed browser changed")
    else:
        require(not any(path.startswith("webview2/") for path in rows)
                and not (runtime / "webview2").exists(),
                "Evergreen staging must not contain a fixed browser")
    require(args.harness.is_file(), "WebView harness missing")
    harness = fixture / "valmax_webview_blank_test.exe"
    shutil.copy2(args.harness, harness)
    require(sha(harness) == sha(args.harness), "copied harness changed")
    qml_source = Path(__file__).with_name("webview_blank.qml")
    qml = fixture / "webview-blank-slim.qml"
    shutil.copy2(qml_source, qml)

    profile = fixture / "webview-blank-slim-profile"
    require(not profile.exists(), "fresh synthetic WebView profile required")
    for name in ("home", "local", "roaming", "temp", "MXMOD/business-webview2"):
        (profile / name).mkdir(parents=True)
    userdata = profile / "MXMOD/business-webview2"
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SystemDrive", "COMSPEC", "PATHEXT")
           if key in os.environ}
    env.update(PATH=str(fixture) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32"),
               QT_QPA_PLATFORM="windows", QT_DEBUG_PLUGINS="1", QT_QUICK_BACKEND="software",
               QML_DISABLE_DISK_CACHE="1", QML_IMPORT_PATH=str(fixture / "qml"),
               QML2_IMPORT_PATH=str(fixture / "qml"), QT_PLUGIN_PATH=str(fixture / "plugins"),
               QT_WEBVIEW_PLUGIN="webview2", VALMAX_WEBVIEW2_MODE=webview2_mode,
               WEBVIEW2_USER_DATA_FOLDER=str(userdata),
               WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS="--disable-background-networking --disable-component-update --disable-sync --no-first-run --no-default-browser-check --disable-gpu",
               APPDATA=str(profile / "roaming"), LOCALAPPDATA=str(profile / "local"),
               USERPROFILE=str(profile / "home"), TEMP=str(profile / "temp"), TMP=str(profile / "temp"))
    if browser_root is not None:
        env["WEBVIEW2_BROWSER_EXECUTABLE_FOLDER"] = str(browser_root)
    block = c.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(env.items())) + "\0\0")

    owned_job, pi, handles = None, winjob.PROCESS(), []
    owned_paths, owned_pids, failures = set(), set(), []
    remaining, assigned, code, started = None, False, winjob.w.DWORD(259), time.monotonic()
    try:
        owned_job = winjob.create_job(None, None)
        winjob.require(owned_job, "create_job")
        limits = winjob.LIMITS()
        limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE; browser children allowed.
        winjob.require(winjob.set_job(owned_job, 9, c.byref(limits), c.sizeof(limits)), "set_job")
        security = winjob.SA(c.sizeof(winjob.SA), None, True)
        for path in ("NUL", str(profile / "stdout.log"), str(profile / "stderr.log")):
            handle = winjob.create_file(path, 0x80000000 if path == "NUL" else 0x40000000,
                                        1, c.byref(security), 3 if path == "NUL" else 2, 0x80, None)
            winjob.require(handle and handle != c.c_void_p(-1).value, "create_output")
            handles.append(handle)
        startup = winjob.STARTUP()
        startup.cb = c.sizeof(startup)
        startup.flags = 0x101
        startup.show = 0
        startup.stdin, startup.stdout, startup.stderr = handles
        command = c.create_unicode_buffer(f'"{harness}" "{qml}"')
        winjob.require(winjob.create_process(str(harness), command, None, None, True,
                                             0x08000404, block, str(fixture),
                                             c.byref(startup), c.byref(pi)), "create_process")
        winjob.require(winjob.assign(owned_job, pi.process), "assign_before_resume")
        assigned = True
        winjob.require(winjob.resume(pi.thread) != 0xFFFFFFFF, "resume")
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            pids = winjob.PIDS()
            winjob.require(winjob.query_job(owned_job, 3, c.byref(pids), c.sizeof(pids), None),
                           "query_owned_job")
            for pid in pids.pids[:pids.listed]:
                owned_pids.add(pid)
                process = winjob.open_process(0x1000, False, pid)
                if process:
                    try:
                        output, size = c.create_unicode_buffer(32768), winjob.w.DWORD(32768)
                        if winjob.query_path(process, 0, output, c.byref(size)):
                            owned_paths.add(output.value)
                    finally:
                        winjob.close(process)
            if winjob.wait(pi.process, 100) == 0:
                break
        winjob.require(winjob.exit_code(pi.process, c.byref(code)), "exit_code")
        if code.value == 259:
            failures.append("deadline")
    except Exception as error:
        failures.append(str(error))
    finally:
        if owned_job:
            winjob.terminate_job(owned_job, 1)
            for _ in range(40):
                pids = winjob.PIDS()
                if winjob.query_job(owned_job, 3, c.byref(pids), c.sizeof(pids), None):
                    remaining = pids.listed
                    if remaining == 0:
                        break
                time.sleep(0.05)
        if pi.process and not assigned:
            winjob.bind("TerminateProcess", winjob.w.BOOL, [winjob.w.HANDLE, winjob.w.UINT])(pi.process, 1)
        for handle in (pi.thread, pi.process, *handles, owned_job):
            if handle:
                winjob.close(handle)

    try:
        result = json.loads((profile / "stdout.log").read_text("utf8"))
    except (OSError, ValueError):
        result = {}
    stderr = (profile / "stderr.log").read_text("utf8", errors="replace")
    expected = {os.path.normcase(str(harness))}
    if browser is not None:
        expected.add(os.path.normcase(str(browser)))
    actual = {os.path.normcase(path) for path in owned_paths}
    installed_roots = [Path(os.environ[key]) / "Microsoft/EdgeWebView/Application"
                       for key in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")
                       if key in os.environ]
    evergreen_browsers = {path for path in actual if Path(path).name.casefold() == "msedgewebview2.exe"}
    installed_browsers = {path for path in evergreen_browsers
                          if Path(path).is_file() and any(Path(path).resolve().is_relative_to(root.resolve())
                                                          for root in installed_roots if root.is_dir())}
    allowed = expected | {os.path.normcase(str(Path(os.environ["SystemRoot"]) / "System32/conhost.exe"))}
    if webview2_mode == "evergreen":
        allowed |= installed_browsers
    verified = (not failures and assigned and remaining == 0 and code.value == 0
                and result.get("ok") is True and result.get("checks") == 2
                and result.get("url") == "about:blank" and expected <= actual <= allowed
                and (browser is not None or bool(installed_browsers)))
    proof = {"schema": 1, "verified": verified, "stagingProof": str(args.staging_proof),
             "stagingProofSha256": sha(args.staging_proof), "importProof": str(args.import_proof),
             "importProofSha256": sha(args.import_proof), "fixture": str(fixture),
             "harnessSource": str(args.harness), "harnessSha256": sha(harness),
             "qmlSourceSha256": sha(qml_source), "qmlFixtureSha256": sha(qml),
             "stockQtWebViewQmldirSha256": sha(fixture / "qml/QtWebView/qmldir"),
             "webview2Mode": webview2_mode,
             "fixedBrowser": str(browser_root) if browser_root else None,
             "fixedBrowserExeSha256": sha(browser) if browser else None,
             "installedEvergreenBrowserExecutables": sorted(installed_browsers),
             "installedEvergreenBrowserSha256": {path: sha(Path(path)) for path in sorted(installed_browsers)},
             "syntheticUserDataDirectory": str(userdata), "syntheticUserDataFiles": sum(1 for p in userdata.rglob("*") if p.is_file()),
             "ownedPids": sorted(owned_pids), "ownedExecutablePaths": sorted(owned_paths),
             "jobAssignedBeforeResume": assigned, "jobProcessesAfterTermination": remaining,
             "exitCode": code.value, "result": result, "failures": failures,
             "stderrSha256": sha(profile / "stderr.log"), "stderrTail": stderr[-3000:] if not verified else "",
             "elapsedSeconds": round(time.monotonic() - started, 3),
             "actualMaxStarted": False, "installedClientChanged": False,
             "privateProfilesRead": False, "realLoginUsed": False,
             "networkAbsenceProven": False, "backgroundWebViewRequestsPossible": True,
             "scope": "Stock QtWebView QML object navigates to about:blank in bounded synthetic process and profile; no MAX UI/business flow."}
    with args.report.open("x", encoding="utf8") as output:
        output.write(json.dumps(proof, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"verified": verified, "exitCode": code.value,
                      "checks": result.get("checks"), "url": result.get("url"),
                      "browserProcessSeen": (os.path.normcase(str(browser)) in actual
                                             if browser else bool(installed_browsers)),
                      "report": str(args.report)}))
    require(verified, "stock QtWebView about:blank failed in the pruned runtime")


if __name__ == "__main__":
    main()
