"""Owned suspended-child ACK experiment; no MAX process, hooks or profile.

The test demonstrates two different contracts. A suspended child's load-time
DLL and entrypoint cannot acknowledge before its first thread is resumed.
After resume, a source-owned gate *inside* the child can hold a simulated
producer until an explicit parent signal. MAX has no such known source gate.
"""

from __future__ import annotations

import argparse
import ctypes as c
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import time
import uuid


ROOT = Path(__file__).resolve().parents[4]
JOB_SOURCE = ROOT / "native/tests/qt/run_webview_blank_native.py"
spec = importlib.util.spec_from_file_location("valmax_owned_job_api", JOB_SOURCE)
assert spec and spec.loader
jobapi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jobapi)
w = jobapi.w

create_event = jobapi.bind("CreateEventW", w.HANDLE, [w.LPVOID, w.BOOL, w.BOOL, w.LPCWSTR])
set_event = jobapi.bind("SetEvent", w.BOOL, [w.HANDLE])


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def private_environment(case: Path, names: dict[str, str]) -> c.Array:
    for folder in ("home", "local", "roaming", "temp"):
        (case / folder).mkdir()
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SystemDrive", "COMSPEC", "PATHEXT")
           if key in os.environ}
    env.update(
        PATH=str(case.parent) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32"),
        APPDATA=str(case / "roaming"), LOCALAPPDATA=str(case / "local"),
        USERPROFILE=str(case / "home"), TEMP=str(case / "temp"), TMP=str(case / "temp"),
        VALMAX_SYNTHETIC_DLL_EVENT=names["dll"],
        VALMAX_SYNTHETIC_MAIN_EVENT=names["main"],
        VALMAX_SYNTHETIC_PROCEED_EVENT=names["proceed"],
        VALMAX_SYNTHETIC_PRODUCER_EVENT=names["producer"],
    )
    return c.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in
                                      sorted(env.items(), key=lambda item: item[0].upper())) + "\0\0")


def run_case(fixture: Path, mode: str) -> dict:
    case = fixture / mode
    case.mkdir()
    prefix = "Local\\ValMAXSyntheticAck-" + uuid.uuid4().hex
    names = {key: prefix + "-" + key for key in ("dll", "main", "proceed", "producer")}
    environment = private_environment(case, names)
    events: dict[str, int] = {}
    job = None
    process = jobapi.PROCESS()
    assigned = False
    resumed = False
    remaining = None
    failure = None
    checks: list[bool] = []
    exit_code = w.DWORD(259)
    observations: dict[str, bool] = {}
    started = time.monotonic()
    error_mode = jobapi.bind("SetErrorMode", w.UINT, [w.UINT])
    old_error_mode = error_mode(0x8003)
    try:
        for key, name in names.items():
            handle = create_event(None, True, False, name)
            jobapi.require(handle, "create_owned_event_" + key)
            events[key] = handle
        job = jobapi.create_job(None, None)
        jobapi.require(job, "create_owned_job")
        limits = jobapi.LIMITS()
        limits.basic.flags = 0x2008  # KILL_ON_JOB_CLOSE and one process.
        limits.basic.processes = 1
        jobapi.require(jobapi.set_job(job, 9, c.byref(limits), c.sizeof(limits)), "bound_owned_job")
        executable = fixture / "valmax_synthetic_ack_child.exe"
        command = c.create_unicode_buffer('"' + str(executable) + '"')
        startup = jobapi.STARTUP()
        startup.cb = c.sizeof(startup)
        jobapi.require(jobapi.create_process(str(executable), command, None, None, False,
                                             0x0C000404, environment, str(fixture),
                                             c.byref(startup), c.byref(process)), "create_owned_suspended")
        actual = c.create_unicode_buffer(32768)
        length = w.DWORD(32768)
        jobapi.require(jobapi.query_path(process.process, 0, actual, c.byref(length)), "query_owned_image")
        checks.append(os.path.normcase(actual.value) == os.path.normcase(str(executable)))
        jobapi.require(jobapi.assign(job, process.process), "assign_job_before_resume")
        assigned = True

        # The only thread that can run synthetic DLL_PROCESS_ATTACH and wmain
        # is still suspended. A parent-side wait cannot manufacture their ACK.
        for key in ("dll", "main", "producer"):
            observations["preResume" + key.title() + "Timeout"] = jobapi.wait(events[key], 100) == 258
        checks.extend(observations.values())
        observations["preResumeProcessStillActive"] = jobapi.wait(process.process, 0) == 258
        checks.append(observations["preResumeProcessStillActive"])

        if mode in ("source_owned_gate", "source_owned_gate_timeout"):
            jobapi.require(jobapi.resume(process.thread) != 0xFFFFFFFF, "resume_owned_child")
            resumed = True
            observations["dllSignalAfterResume"] = jobapi.wait(events["dll"], 3000) == 0
            observations["entrypointAfterResume"] = jobapi.wait(events["main"], 3000) == 0
            observations["producerHeldBeforeProceed"] = jobapi.wait(events["producer"], 150) == 258
            checks.extend((observations["dllSignalAfterResume"], observations["entrypointAfterResume"],
                           observations["producerHeldBeforeProceed"]))
            if mode == "source_owned_gate":
                jobapi.require(set_event(events["proceed"]), "signal_source_owned_gate")
                observations["producerAfterProceed"] = jobapi.wait(events["producer"], 3000) == 0
                observations["childExited"] = jobapi.wait(process.process, 3000) == 0
                jobapi.require(jobapi.exit_code(process.process, c.byref(exit_code)), "read_child_exit")
                checks.extend((observations["producerAfterProceed"], observations["childExited"],
                               exit_code.value == 0))
            else:
                observations["childExitedOnGateTimeout"] = jobapi.wait(process.process, 5000) == 0
                jobapi.require(jobapi.exit_code(process.process, c.byref(exit_code)), "read_child_timeout_exit")
                observations["producerStillHeldAfterTimeout"] = jobapi.wait(events["producer"], 0) == 258
                checks.extend((observations["childExitedOnGateTimeout"],
                               observations["producerStillHeldAfterTimeout"], exit_code.value == 4))
        elif mode != "pre_resume_ack_wait":
            raise ValueError("unknown fixture mode")
    except Exception as error:
        failure = str(error)
    finally:
        if job:
            jobapi.terminate_job(job, 91)
            if process.process:
                jobapi.wait(process.process, 3000)
            for _ in range(50):
                pids = jobapi.PIDS()
                if jobapi.query_job(job, 3, c.byref(pids), c.sizeof(pids), None):
                    remaining = pids.listed
                    if remaining == 0:
                        break
                time.sleep(0.02)
        if process.process and not assigned:
            terminate = jobapi.bind("TerminateProcess", w.BOOL, [w.HANDLE, w.UINT])
            terminate(process.process, 91)
        for handle in (process.thread, process.process, *events.values(), job):
            if handle:
                jobapi.close(handle)
        error_mode(old_error_mode)
    checks.extend((assigned, remaining == 0, failure is None))
    return {
        "mode": mode, "ok": all(checks), "checks": len(checks),
        "jobAssignedBeforeResume": assigned, "resumed": resumed,
        "observations": observations, "childExitCodeBeforeCleanup": exit_code.value,
        "jobProcessesAfterCleanup": remaining, "failure": failure,
        "elapsedSeconds": round(time.monotonic() - started, 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    build = Path(args.build).resolve(strict=True)
    fixture = Path(args.fixture)
    report_path = Path(args.report)
    allowed = Path(r"E:\ValMAXBuild").resolve(strict=True)
    if (not fixture.is_absolute() or not report_path.is_absolute() or
            not build.is_relative_to(allowed) or not fixture.parent.resolve(strict=True).is_relative_to(allowed)
            or report_path.exists() or fixture.exists()):
        raise RuntimeError("fresh E: build/fixture and fresh report required")
    executable = build / "valmax_synthetic_ack_child.exe"
    target = build / "valmax_synthetic_target.dll"
    if not executable.is_file() or not target.is_file():
        raise RuntimeError("owned synthetic fixture binaries missing")
    fixture.mkdir()
    shutil.copy2(executable, fixture / executable.name)
    shutil.copy2(target, fixture / target.name)
    results = [run_case(fixture, mode) for mode in
               ("pre_resume_ack_wait", "source_owned_gate", "source_owned_gate_timeout")]
    report = {
        "schema": 1, "verified": all(result["ok"] for result in results),
        "scope": "Owned synthetic Win32 child and load-time DLL only; no MAX, hooks, profile or remote code",
        "build": str(build), "fixture": str(fixture),
        "sourceSha256": {str(path.relative_to(ROOT)).replace("\\", "/"): sha256(path)
                         for path in (Path(__file__), Path(__file__).with_name("child.cpp"),
                                      Path(__file__).with_name("target.cpp"),
                                      Path(__file__).with_name("CMakeLists.txt"))},
        "binarySha256": {name: sha256(fixture / name)
                         for name in ("valmax_synthetic_ack_child.exe", "valmax_synthetic_target.dll")},
        "cases": results, "checks": sum(result["checks"] for result in results),
        "conclusion": "A child-generated hook ACK cannot precede ResumeThread without another execution path; a source-owned in-child gate can hold a synthetic producer after resume.",
        "limits": ["No MAX executable, loader closure or background producer order examined",
                   "No remote thread, injection, IAT/entrypoint patch, real account or network request",
                   "Parent-side timeout stops its owned fixture, but cannot prove a protected first event in MAX"],
        "productionChanged": False, "installedClientChanged": False, "releaseEligible": False,
    }
    with report_path.open("x", encoding="utf8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"verified": report["verified"], "checks": report["checks"],
                      "report": str(report_path), "sha256": sha256(report_path),
                      "cases": [{"mode": item["mode"], "ok": item["ok"]} for item in results]}))
    if not report["verified"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
