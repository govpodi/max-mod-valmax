"""Fresh anonymous offline probe of the privacy observer, never a real profile.

Both guard and observer load before the owned process resumes. Credential and
host-profile access, registry writes and network calls remain denied. Frida is
instrumentation, not an OS security sandbox. Raw Frida data and Qt messages are
never printed or stored. Existing smoke_test.py and offline_guard.js are read
only. The asynchronous privacy_rpc helper is required before a run can begin.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import queue
import threading
import time

import frida
import build_mod
import launch_mod
from frida_errors import FRIDA_ERRORS
from network_metadata import packet_metadata
from privacy_monitor_specs import specifications
from privacy_status import Observation


MODULE_DIR = Path(__file__).resolve().parent
ALLOWED_COUNTERS = frozenset((
    "unresolved_relative_file_denied", "oneme_path_observations", "host_profile_file_denied",
    "credential_read_denied", "credential_enumerate_denied", "credential_write_denied", "credential_delete_denied",
    "registry_write_denied", "dns_denied", "dnsapi_denied", "network_extension_denied",
    "qt_path_redirected", "qt_messages_omitted", "network_ConnectEx_denied", "network_winhttp_denied", "network_wininet_denied",
)) | frozenset("network_"+name+"_denied" for name in ("connect", "WSAConnect", "send", "sendto", "WSASend", "WSASendTo")) \
   | frozenset(kind+"_namespace_"+marker for kind in ("file", "credential") for marker in ("ONEME", "MXMOD")) \
   | frozenset(prefix+name for prefix in ("sdk_", "sdk_stub_") for name in ("tracer_init", "tracer_upload_async", "tracer_install_crash_handler", "tracer_collect_user_sample"))
GUARD_QT_BRANCH = "if(!GUARD.offline){count('qt_messages_omitted');return;}"
GUARD_CHANNEL = """
// Public message channel: no synchronous Frida RPC can outlive its deadline.
function receiveGuardSnapshot() {
  recv('privacy-snapshot', message => {
    try {
      send({kind:'privacy-snapshot',requestId:message.requestId,
            snapshot:{selftest:rpc.exports.selftest(),summary:rpc.exports.summary()}});
    } catch (_) {
      send({kind:'privacy-snapshot',requestId:message.requestId,snapshot:{failed:true}});
    }
    receiveGuardSnapshot();
  });
}
receiveGuardSnapshot();
"""


def redacted_guard_source(config: dict) -> str:
    """Disable Qt text reading in this in-memory copy; retain offline guards."""
    source = (MODULE_DIR / "offline_guard.js").read_text(encoding="utf8")
    if source.count(GUARD_QT_BRANCH) != 1:
        raise ValueError("Guard Qt redaction anchor changed")
    source = source.replace(GUARD_QT_BRANCH, "count('qt_messages_omitted');return;")
    return "const GUARD=" + json.dumps(config) + ";\n" + source + "\n" + GUARD_CHANNEL


def bounded_counter(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 10_000_000 else None


def safe_guard_summary(value: object) -> dict | None:
    if type(value) is not dict or type(value.get("counters")) is not dict:
        return None
    counters = {key: count for key, count in value["counters"].items()
                if type(key) is str and key in ALLOWED_COUNTERS
                and bounded_counter(count) is not None}
    namespaces = {}
    expected = {"appFolder": "MXMOD", "instance": "mxmod_instance", "pipe": "mxmod_local_pipe"}
    if type(value.get("namespaceValues")) is dict:
        namespaces = {key: value["namespaceValues"][key] == known for key, known in expected.items()
                      if key in value["namespaceValues"]}
    config = {}
    if type(value.get("configValues")) is dict:
        config = {key: value["configValues"][key] for key in ("maskLogs", "sendErrorLogs", "autoUpdate", "roomsAutoUpdate")
                  if type(value["configValues"].get(key)) is int and value["configValues"][key] in (0, 1)}
    return {"counters": counters, "hooks": bounded_counter(value.get("hooks")),
            "namespaceMatches": namespaces, "configValues": config,
            "sdkObjectPresent": value.get("sdkObjectPresent") if type(value.get("sdkObjectPresent")) is bool else None}


class OwnedProcess:
    """Keep a native process handle, so cleanup cannot kill a reused PID."""
    def __init__(self, pid: int):
        self.pid = pid
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
        self.kernel.TerminateProcess.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.handle = self.kernel.OpenProcess(0x100001, False, pid)
        if not self.handle:
            raise OSError("Cannot retain owned process handle")

    def stop(self):
        if self.handle:
            self.kernel.TerminateProcess(self.handle, 1)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class Probe:
    def __init__(self, executable: Path, output: Path, channel_factory):
        self.executable, self.output, self.channel_factory = executable, output, channel_factory
        self.profile = output / "profile"
        self.device = frida.get_local_device()
        self.guard_source = redacted_guard_source({"profile": str(self.profile).replace("\\", "/"),
                                                  "hostProfile": os.environ["USERPROFILE"], "offline": True})
        self.monitor_source = (MODULE_DIR / "privacy_monitor.js").read_text(encoding="utf8")
        self.allowed = {executable.resolve(): False,
                        (executable.parent / "max-service" / "max-service.exe").resolve(): True}
        self.specs = {service: specifications(executable.parent, service=service) for service in (False, True)}
        self.sessions = {}
        self.owned = {}
        self.events = queue.Queue()
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.guard_failed = False
        self.observation = Observation()
        self.started = time.monotonic()
        self.records = []
        self.output_bytes = 0
        self.detached = {}

    def record(self, kind: str, pid: int, **safe):
        with self.lock:
            self.records.append({"seconds": round(time.monotonic()-self.started, 3),
                                 "kind": kind, "pid": pid, **safe})

    def retain(self, pid: int):
        with self.lock:
            if pid not in self.owned:
                self.owned[pid] = OwnedProcess(pid)

    def guard_message(self, pid: int, message: dict, _data):
        payload = message.get("payload")
        if message.get("type") == "error" or (type(payload) is dict and payload.get("type") == "guard-error"):
            self.guard_failed = True
            self.stopping.set()
            self.record("guard_failed", pid)
        elif type(payload) is dict and payload.get("type") in {"guard-ready", "sdk-stub", "sdk-call", "account-latch"}:
            # No paths, strings, message text, errors, credential names or data.
            self.record(payload["type"], pid)

    def monitor_message(self, pid: int, message: dict, data):
        if message.get("type") == "error":
            self.observation.error("hook_failed")
            return
        payload = message.get("payload")
        if type(payload) is not dict:
            return
        kind = payload.get("kind")
        if kind == "session-init" and type(data) is bytes:
            metadata = packet_metadata(data)
            if metadata is None:
                self.observation.error("metadata_rejected")
                from network_metadata import session_init_header_metadata
                self.observation.packet(session_init_header_metadata(data))
            else:
                self.observation.packet(metadata)
        elif kind == "packet":
            self.observation.packet(payload.get("metadata"))
        elif kind == "fatal" and type(payload.get("frames")) is list:
            self.observation.fatal(payload["frames"])
            self.record("qt_fatal", pid)
        # Never log message/data themselves, including unexpected/error fields.

    def on_child(self, child):
        try:
            self.retain(child.pid)
            path = Path(child.path).resolve(strict=True) if child.path else None
            if self.stopping.is_set() or path not in self.allowed:
                self.record("child_blocked", child.pid)
                self.owned[child.pid].stop()
                return
            self.record("child_gated", child.pid, service=self.allowed[path])
            self.events.put((child.pid, self.allowed[path]))
        except (OSError, ValueError):
            self.guard_failed = True
            self.stopping.set()
            self.record("child_guard_failed", child.pid)
            # This event is delivered only for a child of our gated sessions.
            try:
                self.device.kill(child.pid)
            except FRIDA_ERRORS:
                pass

    def on_output(self, _pid, _fd, data):
        # Count only; plugin/fatal/QR/auth text is never decoded.
        with self.lock:
            self.output_bytes += len(data) if data else 0

    def instrument(self, pid: int, service: bool):
        session = self.device.attach(pid)
        self.sessions[pid] = {"session": session, "service": service}
        session.enable_child_gating()
        def detached(reason, crash=None):
            del crash
            self.detached[pid] = reason if reason in {"application-requested", "process-replaced", "process-terminated", "connection-terminated", "device-lost"} else "unknown"
            self.observation.detach(pid)
        session.on("detached", detached)
        guard = session.create_script(self.guard_source)
        guard.on("message", lambda message, data: self.guard_message(pid, message, data))
        guard.load()
        self.sessions[pid]["guard"] = guard
        channel = self.channel_factory(guard)
        self.sessions[pid]["guardChannel"] = channel
        guard_snapshot = channel.snapshot(timeout=5.0)
        check = guard_snapshot.get("selftest") if type(guard_snapshot) is dict else None
        if self.guard_failed or type(check) is not dict or check.get("credentialReadDenied") is not True:
            raise RuntimeError("Guard selftest failed")
        self.record("selftest", pid, credentialReadDenied=True)
        spec = self.specs[service]
        monitor = session.create_script("const MONITOR="+json.dumps(spec)+";\n"+self.monitor_source)
        monitor.on("message", lambda message, data: self.monitor_message(pid, message, data))
        monitor.load()
        self.sessions[pid]["monitor"] = monitor
        # Readiness before resume also tests the async RPC deadline mechanism.
        channel = self.channel_factory(monitor)
        self.sessions[pid]["monitorChannel"] = channel
        snapshot = channel.snapshot(timeout=5.0)
        self.consume_snapshot(pid, snapshot)

    def consume_snapshot(self, pid: int, snapshot: object):
        if type(snapshot) is not dict or type(snapshot.get("stats")) is not dict:
            raise ValueError("Invalid observer snapshot")
        stats = snapshot["stats"]
        tracer_hooks, transport_hooks = bounded_counter(stats.get("tracerHooks")), bounded_counter(stats.get("transportHooks"))
        if tracer_hooks is None or transport_hooks is None:
            raise ValueError("Invalid observer hook counts")
        self.observation.attach(pid, tracer_hooks=tracer_hooks, transport_hooks=transport_hooks,
                                role="service" if self.sessions[pid]["service"] else "main")
        self.sessions[pid]["lastStats"] = {"tracerHooks":tracer_hooks, "transportHooks":transport_hooks}
        counts = snapshot.get("tracerCounts")
        if type(counts) is dict:
            for symbol, count in counts.items():
                if bounded_counter(count) is not None:
                    for _ in range(min(count, 100000)):
                        self.observation.tracer_call(symbol)
        connections = snapshot.get("connections")
        if type(connections) is dict:
            for endpoint, count in connections.items():
                if type(endpoint) is str and len(endpoint) <= 64 and bounded_counter(count) is not None:
                    try:
                        host, port = endpoint.rsplit("|", 1)
                        for _ in range(min(count, 1000)):
                            self.observation.connection(host, int(port))
                    except ValueError:
                        self.observation.error("hook_failed")
        packets = snapshot.get("packets")
        if type(packets) is list:
            for item in packets[:256]:
                if type(item) is dict:
                    self.observation.packet_group(item)
        self.observation.omitted_headers(snapshot.get("omittedPackets", 0), snapshot.get("omittedBytes", 0))
        if snapshot.get("failures"):
            self.observation.error("hook_failed")

    def run(self, seconds: float) -> dict:
        self.device.on("child-added", self.on_child)
        self.device.on("output", self.on_output)
        pid = None
        guard_summaries = []
        before_cleanup = None
        try:
            env = {"APPDATA":str(self.profile / "roaming"), "LOCALAPPDATA":str(self.profile / "local"),
                   "USERPROFILE":str(self.profile / "home"), "TEMP":str(self.profile / "temp"), "TMP":str(self.profile / "temp"),
                   "QT_LOGGING_TO_CONSOLE":"1", "QML_DISABLE_DISK_CACHE":"1"}
            pid = self.device.spawn([str(self.executable)], cwd=str(self.executable.parent), env=env, stdio="pipe")
            self.retain(pid)
            self.record("spawned", pid)
            self.instrument(pid, service=False)
            if self.guard_failed:
                raise RuntimeError("Guard failed before resume")
            self.device.resume(pid)
            self.record("resumed", pid)
            deadline = time.monotonic()+seconds
            next_snapshot = time.monotonic()+1
            while time.monotonic() < deadline and not self.stopping.is_set():
                try:
                    child_pid, service = self.events.get(timeout=min(.5, max(.01, deadline-time.monotonic())))
                except queue.Empty:
                    child_pid = None
                if child_pid is not None:
                    self.instrument(child_pid, service)
                    if self.guard_failed:
                        break
                    self.device.resume(child_pid)
                    self.record("child_resumed", child_pid, service=service)
                if time.monotonic() >= next_snapshot:
                    for target, item in tuple(self.sessions.items()):
                        if target not in self.detached and "monitorChannel" in item:
                            self.consume_snapshot(target, item["monitorChannel"].snapshot(timeout=5.0))
                    next_snapshot = time.monotonic()+1
            for target, item in tuple(self.sessions.items()):
                entry = {"pid":target, "service":item["service"], "detached":self.detached.get(target)}
                if target not in self.detached:
                    if "monitor" in item:
                        self.consume_snapshot(target, item["monitorChannel"].snapshot(timeout=5.0))
                    if "guard" in item:
                        value = item["guardChannel"].snapshot(timeout=5.0)
                        entry["summary"] = safe_guard_summary(value.get("summary")) if type(value) is dict else None
                entry["observer"] = item.get("lastStats")
                expected = len(self.specs[item["service"]]["tracerCallsites"])
                entry["expectedTracerCallsites"] = expected
                entry["tracerCoverage"] = ("unknown" if expected == 0 else
                    "complete" if item.get("lastStats",{}).get("tracerHooks") == expected else "partial")
                guard_summaries.append(entry)
            before_cleanup = self.observation.snapshot()
        except Exception as error:
            # Exception text/args may include arbitrary data: type label only.
            self.record("probe_failed", pid or 0, exception=type(error).__name__)
            self.guard_failed = True
        finally:
            self.stopping.set()
            if before_cleanup is None:
                before_cleanup = self.observation.snapshot()
            for owned in tuple(self.owned.values()):
                owned.stop()
            self.device.off("child-added", self.on_child)
            self.device.off("output", self.on_output)
            for item in tuple(self.sessions.values()):
                for key in ("guardChannel", "monitorChannel"):
                    if key in item:
                        item[key].close()
                try:
                    item["session"].detach()
                except FRIDA_ERRORS:
                    pass
            for owned in tuple(self.owned.values()):
                owned.close()
        result = {"schema":1, "offline":True, "anonymous":True,
                  "guard":"Frida temporary profile; not an OS sandbox", "guardFailed":self.guard_failed,
                  "expectedTracerCallsites":{str(service):len(spec["tracerCallsites"]) for service,spec in self.specs.items()},
                  "guardSummaries":guard_summaries, "detached":self.detached, "stdoutBytes":self.output_bytes,
                  "statusBeforeCleanup":before_cleanup}
        (self.output / "events.json").write_text(json.dumps(self.records, indent=2)+"\n", encoding="utf8")
        (self.output / "summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False)+"\n", encoding="utf8")
        return result


def prepare_profile(output: Path):
    output.mkdir(parents=True, exist_ok=False)
    profile = output / "profile"
    for location in range(23):
        (profile / "qt" / str(location)).mkdir(parents=True)
    for name in ("temp", "roaming", "local", "home"):
        (profile / name).mkdir()
    config = profile / "qt" / "11" / "MXMOD" / "config.json"
    config.parent.mkdir()
    value = launch_mod.make_config()
    value["client_friendly_name"] = "MAX Mod OFFLINE TEST"
    config.write_text(json.dumps(value, ensure_ascii=False), encoding="utf8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=15)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 30:
        raise ValueError("Probe duration must be between1 and30 seconds")
    # Helper import deliberately precedes output creation and process spawn.
    from privacy_rpc import SnapshotChannel
    executable = launch_mod.verified_executable(args.manifest)
    manifest = build_mod.verify(args.manifest)
    if "ui_privacy" not in manifest["components"]:
        raise ValueError("This probe requires the separate UI candidate")
    output = args.output.resolve()
    if output.drive.upper() != "D:":
        raise ValueError("Use a fresh D: output directory outside the host profile")
    prepare_profile(output)
    result = Probe(executable, output, SnapshotChannel).run(args.seconds)
    print(json.dumps({"output":str(output), "guardFailed":result["guardFailed"],
                      "expectedTracerCallsites":result["expectedTracerCallsites"], "files":["events.json", "summary.json"]}))
    if result["guardFailed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
