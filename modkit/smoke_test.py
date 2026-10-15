"""Fresh-profile Frida smoke test; never use for real account login.

Instrumentation limits network/credential access and redirects Qt paths. It is
not a security sandbox. Tests do not prove authenticated account switching.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import threading
import time

import frida


def bounded_rpc(call, seconds=5):
    cancellation = frida.Cancellable()
    timer = threading.Timer(seconds, cancellation.cancel)
    timer.start()
    try:
        with cancellation:
            return call()
    finally:
        timer.cancel()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exe", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=25)
    parser.add_argument("--online", action="store_true", help="Allow network for anonymous startup checks; credential and host-profile guards remain")
    parser.add_argument("--mod-config", action="store_true", help="Use the launcher's config in this temporary profile only")
    parser.add_argument("--arg", action="append", default=[], help="Argument passed to the test executable")
    args = parser.parse_args()
    exe = args.exe.resolve(strict=True)
    output = args.output.resolve()
    if output.exists():
        raise SystemExit("Use a new output directory for every smoke test")
    output.mkdir(parents=True)
    profile = output / "profile"
    for location in range(23):
        (profile / "qt" / str(location)).mkdir(parents=True)
    for name in ("temp", "roaming", "local", "home"):
        (profile / name).mkdir()
    if args.mod_config:
        from launch_mod import make_config
        config_path = profile / "qt" / "11" / "MXMOD" / "config.json"
        config_path.parent.mkdir()
        test_config = make_config()
        test_config["client_friendly_name"] = "MAX Mod TEST"
        config_path.write_text(json.dumps(test_config, ensure_ascii=False), encoding="utf-8")
    config = {"profile": str(profile).replace("\\", "/"), "hostProfile": os.environ["USERPROFILE"], "offline": not args.online}
    source = "const GUARD=" + json.dumps(config) + ";\n" + Path(__file__).with_name("offline_guard.js").read_text(encoding="utf-8")
    device = frida.get_local_device()
    sessions: dict[int, tuple] = {}
    seen_children: set[int] = set()
    events = queue.Queue()
    lock = threading.RLock()
    stopping = threading.Event()
    guard_errors = []
    logfile = output / "events.jsonl"

    def log(event):
        with lock:
            with logfile.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"time": time.time(), **event}, ensure_ascii=False) + "\n")

    def instrument(pid):
        session = device.attach(pid)
        session.enable_child_gating()
        script = session.create_script(source)
        def on_message(message, data):
            log({"pid": pid, "message": message})
            if message.get("type") == "error" or message.get("payload", {}).get("type") == "guard-error":
                guard_errors.append({"pid": pid, "message": message})
                stopping.set()
        script.on("message", on_message)
        session.on("detached", lambda reason, crash=None: log({"pid": pid, "detached": reason, "crash": str(crash) if crash else None}))
        script.load()
        if guard_errors:
            raise RuntimeError("Guard instrumentation failed; process will not be resumed")
        # Exports exist only after the script has installed all current hooks.
        check = bounded_rpc(script.exports_sync.selftest)
        sessions[pid] = (session, script)
        log({"pid": pid, "selftest": check})

    def child_added(child):
        with lock:
            seen_children.add(child.pid)
        if stopping.is_set():
            try:
                device.kill(child.pid)
            except frida.ProcessNotFoundError:
                pass
            return
        log({"child_added": child.pid, "identifier": child.identifier, "path": child.path})
        # MAX can spawn taskkill as service cleanup. Never resume external
        # utilities: they could act on an already installed client by name.
        child_path = Path(child.path or "").resolve()
        if not child.path or not child_path.is_relative_to(exe.parent) or child_path.suffix.lower() != ".exe":
            log({"child_blocked": child.pid, "reason": "outside test runtime"})
            try:
                device.kill(child.pid)
            except frida.ProcessNotFoundError:
                pass
            return
        events.put(child.pid)

    device.on("child-added", child_added)
    def on_output(target, fd, data):
        if data:
            if args.online:
                log({"pid": target, "fd": fd, "output_bytes": len(data)})
            else:
                log({"pid": target, "fd": fd, "output": data[:16384].decode("utf-8", errors="replace")})
    device.on("output", on_output)
    env = {
        "APPDATA": str(profile / "roaming"), "LOCALAPPDATA": str(profile / "local"),
        "USERPROFILE": str(profile / "home"), "TEMP": str(profile / "temp"), "TMP": str(profile / "temp"),
        "QT_LOGGING_TO_CONSOLE": "1", "QT_DEBUG_PLUGINS": "1", "QML_DISABLE_DISK_CACHE": "1",
    }
    pid = device.spawn([str(exe), *args.arg], cwd=str(exe.parent), env=env, stdio="pipe")
    log({"spawned": pid, "exe": str(exe), "guard": "instrumented temporary profile; not OS sandbox", "network_allowed": args.online})
    try:
        instrument(pid)
        device.resume(pid)
        log({"resumed": pid})
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline and not stopping.is_set():
            try:
                child_pid = events.get(timeout=min(0.5, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                continue
            try:
                instrument(child_pid)
                device.resume(child_pid)
                log({"child_resumed": child_pid})
            except Exception as error:
                log({"child_guard_failed": child_pid, "error": str(error)})
                device.kill(child_pid)
        summaries = []
        for target, (_session, script) in tuple(sessions.items()):
            try:
                summaries.append(bounded_rpc(script.exports_sync.summary))
            except frida.InvalidOperationError:
                summaries.append({"pid": target, "exited": True})
            except frida.OperationCancelledError:
                summaries.append({"pid": target, "summary_timeout": True})
                guard_errors.append({"pid": target, "error": "summary RPC timed out"})
        (output / "summary.json").write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(output), "pids": list(sessions), "summary": summaries}))
        if guard_errors:
            raise RuntimeError("Guard instrumentation reported an error; inspect events.jsonl")
    finally:
        # Only processes spawned/gated by this run are terminated.
        stopping.set()
        with lock:
            targets = set(sessions) | seen_children | {pid}
        for target in targets:
            try:
                device.kill(target)
            except (frida.ProcessNotFoundError, frida.InvalidOperationError):
                pass
        device.off("child-added", child_added)
        device.off("output", on_output)


if __name__ == "__main__":
    main()
