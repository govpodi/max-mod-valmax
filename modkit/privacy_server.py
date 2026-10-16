"""Loopback-only privacy diagnostics and explicit MAX Mod quick-reply settings.

No TLS interception or certificates. Only verified mod processes are attached;
no official profile files are opened. Diagnostic endpoints expose
sanitized metadata, never raw packets. The separate quick-reply endpoint stores
only templates explicitly saved by the user. The separate transfer endpoint
reads exact MAX Mod authorization records only after an explicit export/import
action from settings; its status and diagnostics never include those records.
Close via Ctrl+C; clients are left
running and instrumentation is detached.
"""
from __future__ import annotations

import argparse
from concurrent.futures import Future, InvalidStateError, TimeoutError as FutureTimeout
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import socket
import threading
import time
from types import SimpleNamespace

import frida
import startup_status
import build_mod
import launch_mod
from privacy_monitor_specs import specifications
from privacy_status import NOTES, Observation, rules_from_manifest
from privacy_rpc import SnapshotChannel, SnapshotTimeout, SnapshotUnavailable
from frida_errors import FRIDA_ERRORS
from max_client_policy import original_image, scan_clients
from ghost_store import GhostSettingsError
from message_retention_service import RetentionServiceError
from notification_policy import (PolicyChannel, NotificationRuntime, NotificationPolicyError,
                                 from_catalog as notification_policy_from_catalog,
                                 unavailable_policy as notification_unavailable_policy)


PORT = 18973


@contextmanager
def _startup_step(name, code):
    """Publish constants only; a failed protection stage still stops startup."""
    startup_status.stage(name)
    try:
        yield
    except startup_status.StartupError as error:
        startup_status.fail(error.code)
        raise
    except Exception as error:
        if code == "protection_ack_failed":
            if isinstance(error, GhostSettingsError):
                code = "ghost_ack_failed"
            elif isinstance(error, RetentionServiceError):
                code = "retention_ack_failed"
            elif isinstance(error, NotificationPolicyError):
                code = "notification_ack_failed"
        startup_status.fail(code)
        raise startup_status.StartupError(code) from None


class LoopbackServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR does not give exclusive ownership. Acquire the port
    # exclusively before spawning MAX so concurrent launchers cannot both win.
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def bounded_rpc(call):
    cancellation = frida.Cancellable()
    timer = threading.Timer(5, cancellation.cancel)
    timer.start()
    try:
        with cancellation:
            return call()
    finally:
        timer.cancel()


def _runtime_fault_code(error):
    """Classify a caught observer fault without reading its text or values."""
    if isinstance(error, SnapshotTimeout):
        return "runtime_snapshot_timeout"
    if isinstance(error, SnapshotUnavailable):
        return "runtime_snapshot_unavailable"
    if isinstance(error, RetentionServiceError):
        return "runtime_retention_ack_failed"
    if isinstance(error, NotificationPolicyError):
        return "runtime_notification_ack_failed"
    if isinstance(error, GhostSettingsError):
        return "runtime_ghost_ack_failed"
    if isinstance(error, frida.InvalidOperationError):
        return "runtime_session_lost"
    if isinstance(error, FRIDA_ERRORS):
        return "runtime_transport_failed"
    if isinstance(error, (KeyError, ValueError, TypeError)):
        return "runtime_schema_failed"
    return "runtime_worker_failed"


def _observer_state(worker):
    """Additive helper health; a surviving HTTP listener is not a live worker."""
    if worker.ident is None:
        return "starting"
    return "running" if worker.is_alive() else "stopped"


class _ProcessEntry32(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


def _main_processes():
    """Fresh names/PIDs only; usable after the Frida manager has shut down."""
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.Process32FirstW.argtypes = api.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32))
    api.Process32FirstW.restype = api.Process32NextW.restype = wintypes.BOOL
    api.CloseHandle.argtypes, api.CloseHandle.restype = (wintypes.HANDLE,), wintypes.BOOL
    handle = api.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    if handle in (None, 0, ctypes.c_void_p(-1).value):
        raise ValueError("Cannot verify main clients")
    try:
        entry = _ProcessEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        result = []
        ctypes.set_last_error(0)
        success = api.Process32FirstW(handle, ctypes.byref(entry))
        while success:
            if entry.szExeFile.casefold() == "max.exe":
                result.append(SimpleNamespace(pid=int(entry.th32ProcessID), name="max.exe"))
            ctypes.set_last_error(0)
            success = api.Process32NextW(handle, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise ValueError("Cannot verify main clients")
        return result
    finally:
        api.CloseHandle(handle)


def process_path(pid: int) -> Path | None:
    """Query executable identity without reading command line or memory."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                               wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return Path(buffer.value)
        return None
    finally:
        kernel.CloseHandle(handle)


def current_tcp_endpoints(pid: int) -> list[tuple[str, int]]:
    """Remote endpoints only, including connections opened before attach."""
    api = ctypes.WinDLL("iphlpapi").GetExtendedTcpTable
    api.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG), wintypes.BOOL,
                    wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    endpoints = []
    for family, row_size in ((2, 24), (23, 56)):
        size = wintypes.ULONG()
        api(None, ctypes.byref(size), False, family, 5, 0)
        if size.value < 4 or size.value > 16 * 1024 * 1024:
            continue
        buffer = ctypes.create_string_buffer(size.value)
        if api(buffer, ctypes.byref(size), False, family, 5, 0) != 0:
            continue
        data = buffer.raw[:size.value]
        count = int.from_bytes(data[:4], "little")
        if 4 + count * row_size > len(data):
            continue
        for offset in range(4, 4 + count * row_size, row_size):
            row = data[offset:offset + row_size]
            if family == 2:
                owner = int.from_bytes(row[20:24], "little")
                address = row[12:16]
                port = int.from_bytes(row[16:18], "big")
            else:
                owner = int.from_bytes(row[52:56], "little")
                address = row[24:40]
                port = int.from_bytes(row[44:46], "big")
            if owner == pid and port:
                endpoints.append((str(ipaddress.ip_address(address)), port))
    return endpoints


class Monitor:
    def __init__(self, root: Path, observation: Observation):
        self.root = root
        self.observation = observation
        self.device = frida.get_local_device()
        # Native MAX stdout is unrelated to the sanitized journal. Do not
        # forward its bytes to the launcher, terminal, status or any file.
        self.device.on("output", lambda *_args: None)
        self.sessions = {}
        self.failed = {}
        self.roles = {}
        self.stopping = threading.Event()
        self.activity_lock = threading.Lock()
        self.client_pids = set()
        self.main_client_pids = set()
        self.profile_pids = set()
        self.scan_completed = False
        self.source = Path(__file__).with_name("privacy_monitor.js").read_text(encoding="utf-8")
        self.ghost_store = None
        self.ghost_runtime = None
        self.chat_export = None
        self.message_retention = None
        self.chat_locks = None
        self.notification_sessions = {}
        self.notification_runtime = NotificationRuntime()
        self.notification_policy = notification_unavailable_policy()
        self.notification_lock = threading.RLock()
        self.notification_image_path = None
        # Keep only bounded fatal module/RVA metadata on the helper volume;
        # the profile disk may be full when a native startup fails.
        try:
            from native_crash_diagnostics import NativeCrashDiagnostics
            self.native_crashes = NativeCrashDiagnostics(root.parent / ".mxmod-helper-temp")
        except Exception:
            self.native_crashes = None
        self.specs = {False: specifications(root, service=False), True: specifications(root, service=True)}
        self.allowed = {os.path.normcase(str(root / "max.exe")): False,
                        os.path.normcase(str(root / "max-service" / "max-service.exe")): True}

    def _publish_notifications_locked(self, policy):
        """Caller holds notification_lock, and PIN mutations hold store mutex."""
        entries = tuple(self.notification_sessions.items())
        if not entries:
            raise NotificationPolicyError()
        self.notification_policy = policy
        for pid, (guard, updater, channel) in entries:
            if channel.closed or channel.stale:
                channel.close()
                channel = PolicyChannel(updater)
                self.notification_sessions[pid] = (guard, updater, channel)
            try:
                value = channel.publish(policy)
                if self.notification_sessions.get(pid) != (guard, updater, channel):
                    raise NotificationPolicyError()
                self.notification_runtime.update(pid, value)
            except NotificationPolicyError:
                self.notification_runtime.invalidate(pid)
                raise

    @contextmanager
    def prepare_protected_notifications(self, catalog):
        # ChatLockService holds its mutex through this entire context, including
        # the eventual disk commit. Refreshes use the same lock ordering.
        policy = notification_policy_from_catalog(catalog)
        with self.notification_lock:
            self._publish_notifications_locked(policy)
            yield

    def _refresh_notifications(self):
        if self.chat_locks is None:
            return
        from chat_lock_service import ChatLockError
        with self.chat_locks.mutex:
            try:
                policy = notification_policy_from_catalog(self.chat_locks.catalog())
            except (ChatLockError, NotificationPolicyError):
                policy = notification_unavailable_policy()
            with self.notification_lock:
                self.notification_policy = policy
                if self.notification_sessions:
                    self._publish_notifications_locked(policy)

    def notification_status(self):
        with self.notification_lock:
            return self.notification_runtime.status(self.notification_policy)

    def _forget_notifications(self, pid):
        with self.notification_lock:
            entry = self.notification_sessions.pop(pid, None)
            if entry is not None:
                entry[2].close()
            self.notification_runtime.remove(pid)

    def _attach_notifications(self, pid, session):
        from notification_native import source_for, updater_source_for
        from notification_image import prepare as prepare_image
        # Initial publication cannot race a PIN commit in an existing client.
        # This is the same store -> notification lock order as refresh/mutation.
        with self.chat_locks.mutex:
            policy = notification_policy_from_catalog(self.chat_locks.catalog())
            if self.notification_image_path is None:
                self.notification_image_path = prepare_image(launch_mod._prepare_helper_temp(self.root))
            guard = bounded_rpc(lambda: session.create_script(source_for(
                self.root, policy, generic_image_path=self.notification_image_path)))
            ready, channel = Future(), None

            def bootstrap(message, data=None):
                if data is not None or type(message) is not dict:
                    return
                try:
                    payload = message.get('payload')
                    if (message.get('type') == 'send' and type(payload) is dict
                            and payload.get('kind') == 'valmax-notification-bridge-ready'):
                        # The factory strictly validates all bridge metadata.
                        ready.set_result(updater_source_for(payload))
                    elif message.get('type') == 'error':
                        ready.set_exception(NotificationPolicyError())
                except (ValueError, TypeError, KeyError, OSError):
                    try:
                        ready.set_exception(NotificationPolicyError())
                    except InvalidStateError:
                        pass
                except InvalidStateError:
                    pass

            guard.on('message', bootstrap)
            try:
                bounded_rpc(guard.load)
                try:
                    updater_source = ready.result(timeout=5)
                except FutureTimeout:
                    raise NotificationPolicyError() from None
                # Eternal guards retain hooks/owned memory after a helper crash.
                # Their message channel stops, so a separate disposable updater
                # publishes policy through the bounded shared-memory bridge.
                bounded_rpc(guard.eternalize)
                updater = bounded_rpc(lambda: session.create_script(updater_source))
                channel = PolicyChannel(updater)
                bounded_rpc(updater.load)
                with self.notification_lock:
                    value = channel.publish(policy)
                    self.notification_runtime.register(pid)
                    self.notification_sessions[pid] = (guard, updater, channel)
                    self.notification_policy = policy
                    self.notification_runtime.update(pid, value)
            except BaseException:
                if channel is not None:
                    channel.close()
                raise
            finally:
                ready.cancel()
                try:
                    guard.off('message', bootstrap)
                except Exception:
                    pass

    def attach(self, pid: int, *, service: bool):
        session = bounded_rpc(lambda: self.device.attach(pid))
        channel = None
        try:
            config = dict(self.specs[service])
            source = self.source
            ghost_desired = None
            if not service and getattr(self, "ghost_store", None) is not None:
                from ghost_native import specification
                config["ghost"] = specification(self.root)
                ghost_desired = self.ghost_store.snapshot()
                config["ghostSettings"] = {key: ghost_desired[key] for key in ("revision", "settings")}
                source += "\n" + Path(__file__).with_name("ghost_monitor.js").read_text(encoding="utf8")
            if not service and getattr(self, "chat_export", None) is not None:
                from chat_export_end_specs import source_for
                try:
                    source += "\n" + source_for(self.root)
                except (ValueError, OSError):
                    # Missing provenance makes an export partial. Optional
                    # observation must never prevent the messenger starting.
                    pass
            retention_desired = None
            if not service and getattr(self, "message_retention", None) is not None:
                from message_retention_native import source_for
                retention_desired = self.message_retention.desired()
                self.message_retention.preflight_marker_store()
                self.message_retention.register(pid)
                source += "\n" + source_for(self.root, enabled=retention_desired["enabled"],
                                           known_messages=self.message_retention.known_messages())
            script = bounded_rpc(lambda: session.create_script("const MONITOR=" + json.dumps(config) + ";\n" + source))
            script.on("message", lambda message, data: self._message(
                message, data, role="service" if service else "main", pid=pid))
            channel = SnapshotChannel(script)
            bounded_rpc(script.load)
            if ghost_desired is not None:
                first = channel.snapshot(timeout=5)
                self.ghost_runtime.update(pid, first.get("ghost"))
                # A persisted opt-in cannot silently start unprotected.
                if ghost_desired["settings"]["enabled"] and not self.ghost_runtime.status(ghost_desired)["applied"]:
                    raise startup_status.StartupError("ghost_ack_failed")
            if retention_desired is not None:
                first = channel.snapshot(timeout=5)
                self.message_retention.update(pid, first.get("retention"))
                if retention_desired["enabled"] and not self.message_retention.snapshot()["runtime"]["applied"]:
                    raise startup_status.StartupError("retention_ack_failed")
            if not service and getattr(self, "chat_locks", None) is not None:
                self._attach_notifications(pid, session)
            self.sessions[pid] = (session, script, channel)
            self.roles[pid] = "service" if service else "main"
        except BaseException:
            if getattr(self, "chat_locks", None) is not None:
                self._forget_notifications(pid)
            if not service and getattr(self, "message_retention", None) is not None:
                self.message_retention.remove(pid)
            if getattr(self, "ghost_runtime", None) is not None:
                self.ghost_runtime.invalidate(pid)
            if channel is not None:
                channel.close()
            try:
                bounded_rpc(session.detach)
            except FRIDA_ERRORS:
                pass
            raise

    def launch(self, executable: Path):
        # Never inherit the tool/launcher console: MAX's own logs can contain
        # account or chat identifiers even though our observer never logs them.
        with _startup_step("native_spawn", "native_spawn_failed"):
            from external_environment import native_environment
            with native_environment() as environment:
                from business_environment import child_environment
                from optimization_store import child_environment as optimization_environment
                config_path, _ = launch_mod.prepare_config(check_only=True)
                options = {"cwd": str(executable.parent), "stdio": "pipe",
                           "env": optimization_environment(child_environment(environment, config_path.parent))}
                pid = self.device.spawn([str(executable)], **options)
        try:
            with _startup_step("protection_attach", "protection_ack_failed"):
                self.attach(pid, service=False)
                self.device.resume(pid)
        except BaseException:
            # Only this newly spawned process; existing clients are untouched.
            try:
                self.device.kill(pid)
            except Exception:
                pass  # A failed kill must not conceal the primary stage/code.
            raise
        with self.activity_lock:
            self.client_pids.add(pid)
            self.main_client_pids.add(pid)
            self.profile_pids.add(pid)
        return pid

    def activity(self):
        with self.activity_lock:
            return len(self.client_pids), self.scan_completed

    def profile_activity(self):
        with self.activity_lock:
            return len(self.profile_pids), self.scan_completed

    def main_activity(self):
        """A surviving calls service does not mean the messenger is open."""
        with self.activity_lock:
            return len(self.main_client_pids), self.scan_completed

    def restart_activity(self):
        """Conservative fresh all-root main scan, without attaching any PID."""
        try:
            return scan_clients(_main_processes(), process_path)
        except Exception:
            return 0, False

    def retry_later(self, pid: int):
        attempts = min(self.failed.get(pid, (0, 0))[0] + 1, 3)
        self.failed[pid] = (attempts, time.monotonic() + (5, 15, 30)[attempts - 1])

    def _message(self, message, data, *, role=None, pid=None):
        # Only the bounded SessionInit parser may consume a raw packet in RAM.
        # The raw buffer is never printed, included in an exception or stored.
        if message.get("type") == "error":
            self.observation.error("hook_failed")
            return
        payload = message.get("payload")
        if not isinstance(payload, dict):
            return
        if payload.get("kind") == "chat-export-history":
            if role == "main" and getattr(self, "chat_export", None) is not None:
                self.chat_export.observe_history(pid, payload)
            return
        if payload.get("kind") in ("retained-delete", "received-messages"):
            # This dedicated feature store contains IDs only. Never forward
            # the event to privacy diagnostics or a terminal/log sink.
            if role == "main" and getattr(self, "message_retention", None) is not None:
                from message_retention_service import RetentionServiceError
                try:
                    if payload["kind"] == "retained-delete":
                        self.message_retention.record(pid, payload)
                    else:
                        self.message_retention.record_received(pid, payload)
                except RetentionServiceError:
                    self.message_retention.invalidate(pid)
                    attached = self.sessions.get(pid)
                    if attached is not None:
                        try:
                            bounded_rpc(lambda: attached[1].post({"type": "valmax-retention-storage", "available": False}))
                        except FRIDA_ERRORS:
                            pass
            return
        if payload.get("kind") == "fatal" and type(payload.get("frames")) is list:
            self.observation.fatal(payload["frames"])
            if role in ("main", "service"):
                try:
                    recorder = getattr(self, "native_crashes", None)
                    if recorder is not None:
                        recorder.record(payload["frames"], role=role, pid=pid)
                except Exception:
                    pass  # Optional metadata cannot change native fatal handling.
            return
        if payload.get("kind") == "session-init" and isinstance(data, bytes):
            try:
                from network_metadata import packet_metadata_diagnostic, session_init_header_metadata
                value, reason = packet_metadata_diagnostic(data)
                if value is not None:
                    self.observation.packet(value)
                else:
                    self.observation.metadata_rejection(reason)
                    # Rejected body schema still has an observed frame header.
                    self.observation.packet(session_init_header_metadata(data))
            except (ValueError, TypeError, ImportError):
                self.observation.error("metadata_rejected")
        elif payload.get("kind") == "packet" and isinstance(payload.get("metadata"), dict):
            self.observation.packet(payload["metadata"])

    def tick(self):
        live = set()
        main_live = set()
        profile_live = set()
        for process in bounded_rpc(self.device.enumerate_processes):
            if process.name.lower() not in ("max.exe", "max-service.exe"):
                continue
            try:
                path = process_path(process.pid)
            except Exception:
                path = None
            identity = os.path.normcase(str(path)) if path is not None else ""
            # Every mod copy shares MXMOD, including an installed copy or a
            # previous lab build under another parent. Only pinned original
            # executable/config pairs are exempt; no other PID is attached.
            if path is None or identity in self.allowed or not original_image(path, process.name.lower()):
                profile_live.add(process.pid)
            if identity not in self.allowed:
                continue
            live.add(process.pid)
            if not self.allowed[identity]:
                main_live.add(process.pid)
            if (process.pid in self.sessions
                    or time.monotonic() < self.failed.get(process.pid, (0, 0))[1]):
                continue
            try:
                if getattr(self, "ghost_runtime", None) is not None and not self.allowed[identity]:
                    self.ghost_runtime.invalidate(process.pid)
                self.attach(process.pid, service=self.allowed[identity])
                self.failed.pop(process.pid, None)
                for host, port in current_tcp_endpoints(process.pid):
                    self.observation.connection(host, port)
            except (*FRIDA_ERRORS, GhostSettingsError, RetentionServiceError, NotificationPolicyError, OSError, ValueError, SnapshotTimeout, SnapshotUnavailable) as error:
                self.retry_later(process.pid)
                self.observation.error("attach_failed")
                self.observation.error(_runtime_fault_code(error))
        self.failed = {pid: value for pid, value in self.failed.items() if pid in live}
        with self.activity_lock:
            self.client_pids = live
            self.main_client_pids = main_live
            self.profile_pids = profile_live
            self.scan_completed = True
        if getattr(self, "ghost_runtime", None) is not None:
            self.ghost_runtime.sync(main_live)
        if getattr(self, "chat_locks", None) is not None:
            with self.notification_lock:
                for stale_pid in set(self.notification_sessions) - main_live:
                    self._forget_notifications(stale_pid)
                self.notification_runtime.sync(main_live)
            try:
                self._refresh_notifications()
            except NotificationPolicyError:
                self.observation.error("hook_failed")
        for pid, (session, script, channel) in tuple(self.sessions.items()):
            if pid not in live:
                if getattr(self, "message_retention", None) is not None:
                    self.message_retention.remove(pid)
                if getattr(self, "ghost_runtime", None) is not None:
                    self.ghost_runtime.remove(pid)
                self.observation.detach(pid)
                self.sessions.pop(pid, None)
                self.roles.pop(pid, None)
                channel.close()
                try:
                    bounded_rpc(session.detach)
                except FRIDA_ERRORS:
                    pass
                continue
            try:
                if getattr(self, "ghost_store", None) is not None and self.roles[pid] == "main":
                    desired = self.ghost_store.snapshot()
                    bounded_rpc(lambda: script.post({"type": "valmax-ghost-settings", "revision": desired["revision"], "settings": desired["settings"]}))
                if getattr(self, "message_retention", None) is not None and self.roles[pid] == "main":
                    retention_desired = self.message_retention.desired()
                    retention_available = self.message_retention.storage_available()
                    # Retry failure propagation even if the immediate event
                    # callback could not deliver it. Keep other filters loaded.
                    bounded_rpc(lambda: script.post({"type": "valmax-retention-storage", "available": retention_available}))
                    bounded_rpc(lambda: script.post({"type": "valmax-retention-settings", "enabled": retention_desired["enabled"] and retention_available}))
                    if retention_available:
                        known_update, known_cursor = self.message_retention.pending_known(pid)
                        if known_update["records"]:
                            bounded_rpc(lambda: script.post(known_update))
                            self.message_retention.acknowledge_known(pid, known_cursor)
                # No values besides public SDK labels, endpoints and counts.
                snapshot = channel.snapshot(timeout=5)
                if getattr(self, "ghost_runtime", None) is not None and self.roles[pid] == "main":
                    self.ghost_runtime.update(pid, snapshot.get("ghost"))
                if getattr(self, "message_retention", None) is not None and self.roles[pid] == "main":
                    self.message_retention.update(pid, snapshot.get("retention"))
                self.observation.attach(pid, tracer_hooks=snapshot["stats"]["tracerHooks"],
                                        transport_hooks=snapshot["stats"]["transportHooks"],
                                        role=self.roles[pid])
                for symbol, count in snapshot["tracerCounts"].items():
                    if type(count) is int and count > 0:
                        self.observation.tracer_call(symbol, count=min(count, 100000))
                for endpoint, count in snapshot["connections"].items():
                    host, port = endpoint.rsplit("|", 1)
                    if type(count) is int and count > 0:
                        self.observation.connection(host, int(port), count=min(count, 1000))
                for item in snapshot.get("packets", [])[:256]:
                    if type(item) is dict:
                        self.observation.packet_group(item)
                self.observation.omitted_headers(snapshot.get("omittedPackets", 0), snapshot.get("omittedBytes", 0))
                if snapshot["failures"]:
                    self.observation.error("hook_failed")
            except (*FRIDA_ERRORS, GhostSettingsError, RetentionServiceError, KeyError, ValueError, TypeError, SnapshotTimeout, SnapshotUnavailable) as error:
                if getattr(self, "message_retention", None) is not None:
                    self.message_retention.invalidate(pid)
                self.observation.error("hook_failed")
                self.observation.error(_runtime_fault_code(error))
                if (getattr(self, "ghost_store", None) is not None or getattr(self, "message_retention", None) is not None or getattr(self, "chat_locks", None) is not None) and self.roles.get(pid) == "main":
                    # Keep the existing ghost replacements installed when the
                    # optional observation channel fails. Losing telemetry is
                    # not permission to restore read/typing calls silently.
                    if getattr(self, "ghost_runtime", None) is not None:
                        self.ghost_runtime.invalidate(pid)
                    channel.close()
                    try:
                        self.sessions[pid] = (session, script, SnapshotChannel(script))
                    except SnapshotUnavailable:
                        pass
                    continue
                self.observation.detach(pid)
                self.sessions.pop(pid, None)
                self.roles.pop(pid, None)
                self.retry_later(pid)
                channel.close()
                try:
                    bounded_rpc(session.detach)
                except FRIDA_ERRORS:
                    pass

    def _invalidate_runtime_metadata(self):
        # Keep native filters and their owning scripts alive. A failed scan
        # or collector iteration cannot confirm an ACK or current counters.
        for pid in tuple(self.sessions):
            self.observation.detach(pid)
            if self.roles.get(pid) == "main":
                if getattr(self, "message_retention", None) is not None:
                    self.message_retention.invalidate(pid)
                if getattr(self, "ghost_runtime", None) is not None:
                    self.ghost_runtime.invalidate(pid)
                if getattr(self, "chat_locks", None) is not None:
                    self.notification_runtime.invalidate(pid)

    def run(self):
        try:
            while not self.stopping.is_set():
                try:
                    self.tick()
                except (*FRIDA_ERRORS, GhostSettingsError, RetentionServiceError, SnapshotTimeout, SnapshotUnavailable) as error:
                    self.observation.error("attach_failed")
                    self.observation.error(_runtime_fault_code(error))
                    self._invalidate_runtime_metadata()
                except Exception as error:
                    # Unexpected optional collector failures must not unload
                    # read/typing/retention filters from the running messenger.
                    self.observation.error("hook_failed")
                    self.observation.error("runtime_worker_failed")
                    code = _runtime_fault_code(error)
                    if code != "runtime_worker_failed":
                        self.observation.error(code)
                    self._invalidate_runtime_metadata()
                self.stopping.wait(2)
        finally:
            # Only the explicit helper shutdown owns filter cleanup. An
            # unexpected worker exit must not silently restore native calls.
            if self.stopping.is_set():
                for pid, (session, _script, channel) in tuple(self.sessions.items()):
                    if getattr(self, "chat_locks", None) is not None:
                        self._forget_notifications(pid)
                    if getattr(self, "message_retention", None) is not None:
                        self.message_retention.remove(pid)
                    if getattr(self, "ghost_runtime", None) is not None:
                        self.ghost_runtime.remove(pid)
                    channel.close()
                    try:
                        bounded_rpc(session.detach)
                    except FRIDA_ERRORS:
                        pass
                    self.observation.detach(pid)


def handler_factory(status, identity=None, quick_replies=None, session_transfer=None, mdata=None, account_settings=None, ghost_settings=None, ghost_runtime=None, chat_export=None, message_retention=None, chat_locks=None, inline_media=None, notification_privacy=None, optimization_settings=None, support_diagnostics=None):
    preview_lock = threading.BoundedSemaphore(1)
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def log_message(self, *_args):
            pass  # Do not log request URLs or arbitrary HTTP input.

        def local_headers(self):
            hosts = self.headers.get_all("Host", [])
            origins = self.headers.get_all("Origin", [])
            return (len(hosts) == 1 and hosts[0] in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")
                    and (not origins or origins == ["null"]))

        def json_content_type(self):
            types = self.headers.get_all("Content-Type", [])
            return (len(types) == 1 and types[0].split(";", 1)[0].strip().lower() == "application/json"
                    and self.headers.get_content_charset("utf-8") == "utf-8")

        def discard_rejected_body(self, max_body):
            """Avoid a Windows reset for a bounded, unambiguous rejected POST.

            Bytes are discarded without parsing, logging or storage. Ambiguous
            lengths and transfer encodings are never consumed; slow/incomplete
            bodies get one short total deadline, not a fresh timeout per chunk.
            """
            lengths = self.headers.get_all("Content-Length", [])
            if (self.headers.get_all("Transfer-Encoding", []) or len(lengths) != 1
                    or not 1 <= len(lengths[0]) <= 7 or not lengths[0].isascii()
                    or not lengths[0].isdigit()):
                return
            remaining = int(lengths[0])
            if not 0 < remaining <= max_body:
                return
            previous = self.connection.gettimeout()
            deadline = time.monotonic() + 0.25
            try:
                while remaining:
                    timeout = deadline - time.monotonic()
                    if timeout <= 0:
                        break
                    self.connection.settimeout(timeout)
                    chunk = self.rfile.read1(min(remaining, 65536))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    chunk = None
            except (TimeoutError, OSError):
                pass
            finally:
                try:
                    self.connection.settimeout(previous)
                except OSError:
                    pass

        def reply(self, code, value):
            data = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
                pass

        def store_failure(self, error):
            # Never put exception text, paths, user templates or request bodies
            # in an error. Only the store's finite public code is returned.
            code = getattr(error, "code", "io_error")
            allowed = {"invalid_replies", "invalid_accounts", "invalid_settings", "invalid_revision", "revision_conflict",
                       "invalid_store", "unsafe_path", "store_busy", "io_error"}
            if code not in allowed:
                code = "io_error"
            status_code = 409 if code == "revision_conflict" else (
                400 if code in ("invalid_replies", "invalid_accounts", "invalid_settings", "invalid_revision") else 503)
            self.reply(status_code, {"schema": 1, "error": code})

        def do_GET(self):
            if not self.local_headers():
                self.send_error(404)
                return
            if self.path == "/status":
                self.reply(200, status())
            elif self.path == "/support-diagnostics" and support_diagnostics is not None:
                self.reply(200, support_diagnostics.preview())
            elif self.path == "/identity" and identity is not None:
                self.reply(200, identity())
            elif self.path == "/quick-replies" and quick_replies is not None:
                from quick_reply_store import QuickReplyStoreError
                try:
                    self.reply(200, quick_replies.snapshot())
                except QuickReplyStoreError as error:
                    self.store_failure(error)
            elif self.path == "/session-transfer" and session_transfer is not None:
                self.reply(200, session_transfer.snapshot())
            elif self.path == "/account-settings" and account_settings is not None:
                from account_preferences import AccountPreferencesError
                try:
                    self.reply(200, account_settings.snapshot())
                except AccountPreferencesError as error:
                    self.store_failure(error)
            elif self.path == "/optimization-settings" and optimization_settings is not None:
                from optimization_store import OptimizationSettingsError
                try:
                    self.reply(200, optimization_settings.snapshot())
                except OptimizationSettingsError as error:
                    self.store_failure(error)
            elif self.path == "/mdata" and mdata is not None:
                self.reply(200, mdata.snapshot())
            elif self.path == "/ghost-settings" and ghost_settings is not None and ghost_runtime is not None:
                from ghost_store import GhostSettingsError
                try:
                    desired = ghost_settings.snapshot()
                    self.reply(200, {**desired, "runtime": ghost_runtime.status(desired)})
                except GhostSettingsError as error:
                    self.store_failure(error)
            elif self.path == "/message-retention/status" and message_retention is not None:
                try:
                    self.reply(200, message_retention.snapshot())
                except RetentionServiceError as error:
                    self.store_failure(error)
            elif self.path == "/chat-locks" and chat_locks is not None:
                from chat_lock_service import ChatLockError
                try:
                    self.reply(200, chat_locks.catalog())
                except ChatLockError as error:
                    self.reply(503, {"schema": 1, "error": error.code})
            elif self.path == "/notification-privacy" and notification_privacy is not None:
                self.reply(200, {"schema": 1, **notification_privacy()})
            elif self.path.startswith("/inline-media/preview?id=") and inline_media is not None:
                from inline_media import InlineMediaError
                result_id = self.path[len("/inline-media/preview?id="):]
                try:
                    data, mime = inline_media.preview(result_id)
                    if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif") or len(data) > 2 * 1024 * 1024:
                        raise InlineMediaError("invalid_response", 503)
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    try:
                        self.wfile.write(data)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
                        pass
                except InlineMediaError as error:
                    self.reply(error.status, {"schema": 1, "error": error.code})
            else:
                self.send_error(404)

        def do_POST(self):
            is_mdata = self.path == "/mdata" and mdata is not None
            is_preview = self.path == "/mdata/inspect" and mdata is not None
            is_selected = self.path == "/mdata/export-selected" and mdata is not None
            is_confirmed = self.path == "/mdata/import-confirmed" and mdata is not None
            is_mdata_request = is_mdata or is_preview or is_selected or is_confirmed
            is_transfer = self.path == "/session-transfer" and session_transfer is not None
            is_accounts = self.path == "/account-settings" and account_settings is not None
            is_optimization = self.path == "/optimization-settings" and optimization_settings is not None
            is_support = self.path == "/support-diagnostics" and support_diagnostics is not None
            is_ghost = self.path == "/ghost-settings" and ghost_settings is not None and ghost_runtime is not None
            is_retention = self.path in ("/message-retention/lookup", "/message-retention/settings") and message_retention is not None
            from chat_lock_service import KEYS as CHAT_LOCK_KEYS
            lock_action = self.path[len("/chat-locks/"):] if self.path.startswith("/chat-locks/") else None
            is_chat_lock = chat_locks is not None and lock_action in CHAT_LOCK_KEYS
            media_action = self.path[len("/inline-media/"):] if self.path.startswith("/inline-media/") else None
            is_inline_media = inline_media is not None and media_action in ("search", "select")
            export_action = self.path[len("/chat-export/"):] if self.path.startswith("/chat-export/") else None
            is_chat_export = chat_export is not None and export_action in ("start", "page", "media", "media-start", "media-chunk", "finish", "status", "history", "cancel")
            max_body = 2 * 1024 * 1024 if is_chat_export else 32768 if is_retention else 256 * 1024
            if not is_support and not is_inline_media and not is_chat_lock and not is_retention and not is_chat_export and not is_mdata_request and not is_transfer and not is_accounts and not is_optimization and not is_ghost and (self.path != "/quick-replies" or quick_replies is None):
                self.send_error(405)
                return
            # Native QML XHR sends no Origin. Web pages cannot send this custom
            # header + JSON without a CORS preflight; OPTIONS never grants it.
            if (not self.local_headers() or self.headers.get_all("Origin", [])
                    or self.headers.get_all("X-Max-Mod-Local-Write", []) != ["support-diagnostics-v1" if is_support else "optimization-settings-v1" if is_optimization else "inline-media-v1" if is_inline_media else "chat-locks-v1" if is_chat_lock else "message-retention-v1" if is_retention else "chat-export-v1" if is_chat_export else "mdata-v1" if is_mdata_request else "session-transfer-v1" if is_transfer else "account-settings-v1" if is_accounts else "ghost-settings-v1" if is_ghost else "quick-replies-v1"]
                    or not self.json_content_type()
                    or self.headers.get_all("Transfer-Encoding", [])
                    or self.headers.get_all("Expect", [])):
                self.discard_rejected_body(max_body)
                self.reply(403, {"schema": 1, "error": "local_write_required"})
                return
            lengths = self.headers.get_all("Content-Length", [])
            if (len(lengths) != 1 or len(lengths[0]) > (7 if is_chat_export else 6)
                    or not lengths[0].isascii() or not lengths[0].isdigit()):
                self.reply(400, {"schema": 1, "error": "invalid_request"})
                return
            length = int(lengths[0])
            if not 1 <= length <= max_body:
                self.reply(413, {"schema": 1, "error": "request_too_large"})
                return
            try:
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("length")
                value = json.loads(raw.decode("utf8"), object_pairs_hook=launch_mod._unique_object,
                                   parse_constant=launch_mod._invalid_json_constant)
                expected_keys = {"schema", "action", "file", "password", "mode"} if is_mdata else {"schema", "action", "file", "password"} if is_transfer else {"schema", "revision", "accounts"} if is_accounts else {"schema", "revision", "replies"}
                if is_preview:
                    expected_keys = {"schema", "file", "password"}
                elif is_selected:
                    expected_keys = {"schema", "file", "password", "accountIds"}
                elif is_confirmed:
                    expected_keys = {"schema", "file", "password", "fingerprint"}
                elif is_ghost or is_optimization:
                    expected_keys = {"schema", "revision", "settings"}
                elif is_support:
                    expected_keys = {"schema", "file"}
                elif is_inline_media:
                    expected_keys = {"mode", "query"} if media_action == "search" else {"id"}
                elif is_chat_lock:
                    expected_keys = CHAT_LOCK_KEYS[lock_action]
                elif is_retention:
                    expected_keys = {"schema", "accountId", "chatId", "messageIds"} if self.path.endswith("/lookup") else {"schema", "revision", "enabled"}
                elif is_chat_export:
                    from chat_export_service import KEYS
                    expected_keys = KEYS[export_action]
                if (type(value) is not dict or set(value) != expected_keys
                        or not is_inline_media and (type(value["schema"]) is not int or value["schema"] != 1)):
                    raise ValueError("schema")
            except (UnicodeDecodeError, ValueError, RecursionError, TimeoutError, OSError):
                self.reply(400, {"schema": 1, "error": "invalid_request"})
                return
            if is_support:
                from support_diagnostics import SupportDiagnosticsError
                try:
                    self.reply(200, support_diagnostics.export(value["file"]))
                except SupportDiagnosticsError as error:
                    self.reply(409 if error.code == "file_exists" else 400 if error.code == "unsafe_path" else 503,
                               {"schema": 1, "error": error.code})
                finally:
                    value.clear()
                    raw = None
                return
            if is_optimization:
                from optimization_store import OptimizationSettingsError
                try:
                    self.reply(200, optimization_settings.replace(value["revision"], value["settings"]))
                except OptimizationSettingsError as error:
                    self.store_failure(error)
                return
            if is_inline_media:
                from inline_media import InlineMediaError
                try:
                    self.reply(200, inline_media.search(value) if media_action == "search" else inline_media.select(value))
                except InlineMediaError as error:
                    self.reply(error.status, {"schema": 1, "error": error.code})
                finally:
                    value.clear()
                    raw = None
                return
            if is_chat_lock:
                from chat_lock_service import ChatLockError
                try:
                    self.reply(200, chat_locks.execute(lock_action, value))
                except ChatLockError as error:
                    code = 429 if error.code == "rate_limited" else 403 if error.code == "wrong_pin" else 400 if error.code in ("invalid_request", "invalid_pin") else 409 if error.code in ("not_protected", "already_protected", "limit_reached") else 503
                    self.reply(code, {"schema": 1, "error": error.code})
                finally:
                    value.clear()
                    raw = None
                return
            if is_retention:
                try:
                    self.reply(200, message_retention.lookup(value) if self.path.endswith("/lookup") else message_retention.configure(value))
                except RetentionServiceError as error:
                    self.store_failure(error)
                finally:
                    value.clear()
                    raw = None
                return
            if is_chat_export:
                from chat_export_service import ChatExportError
                try:
                    self.reply(200, chat_export.execute(export_action, value))
                except ChatExportError as error:
                    code = 409 if error.code in ("busy", "invalid_state", "sequence_conflict") else (404 if error.code == "not_found" else 400 if error.code in ("invalid_request", "limit_reached", "unsafe_path") else 503)
                    self.reply(code, {"schema": 1, "state": "error", "error": error.code})
                finally:
                    # Message contents belong to the explicit export only.
                    value.clear()
                    raw = None
                return
            if is_mdata_request:
                from mdata_service import MDataError
                acquired_preview = False
                try:
                    if is_preview:
                        acquired_preview = preview_lock.acquire(blocking=False)
                        if not acquired_preview:
                            raise MDataError("busy")
                        from mdata_manager import inspect
                        self.reply(200, inspect(value["file"], value["password"]))
                    elif is_selected:
                        self.reply(200, mdata.execute_selected_export(value["file"], value["password"], value["accountIds"]))
                    elif is_confirmed:
                        self.reply(200, mdata.execute_confirmed_import(value["file"], value["password"], value["fingerprint"]))
                    else:
                        self.reply(200, mdata.execute(value["action"], value["file"], value["password"], value["mode"]))
                except MDataError as error:
                    status_code = 409 if error.code in ("busy", "file_exists", "profile_collision", "account_limit", "recovery_required") else 400
                    self.reply(status_code, {"schema": 1, "state": "error", "error": error.code})
                finally:
                    if acquired_preview:
                        preview_lock.release()
                    value.clear()
                    raw = None
                return
            if is_transfer:
                from session_transfer import TransferError
                try:
                    self.reply(200, session_transfer.execute(value["action"], value["file"], value["password"]))
                except TransferError as error:
                    status_code = 409 if error.code in ("busy", "destination_not_empty", "file_exists") else 400
                    self.reply(status_code, {"schema": 1, "state": "error", "error": error.code})
                finally:
                    # No body, password, selected path or credential data is logged.
                    value.clear()
                    raw = None
                return
            if is_accounts:
                from account_preferences import AccountPreferencesError
                try:
                    self.reply(200, account_settings.replace(value["revision"], value["accounts"]))
                except AccountPreferencesError as error:
                    self.store_failure(error)
                return
            if is_ghost:
                from ghost_store import GhostSettingsError
                try:
                    desired = ghost_settings.replace(value["revision"], value["settings"])
                    self.reply(200, {**desired, "runtime": ghost_runtime.status(desired)})
                except GhostSettingsError as error:
                    self.store_failure(error)
                return
            from quick_reply_store import QuickReplyStoreError
            try:
                self.reply(200, quick_replies.replace(value["revision"], value["replies"]))
            except QuickReplyStoreError as error:
                self.store_failure(error)

        def do_OPTIONS(self):
            self.send_error(405)

    return Handler


class IdleDeadline:
    """Exit after the verified clients disappear; never stop the clients."""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self.empty_since = None

    def expired(self, count: int, scanned: bool, now: float) -> bool:
        if count or not scanned:
            self.empty_since = None
            return False
        if self.empty_since is None:
            self.empty_since = now
        return bool(self.seconds and now - self.empty_since >= self.seconds)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=launch_mod.DEFAULT_MANIFEST)
    parser.add_argument("--seconds", type=float, default=0, help="0 runs until stopped")
    parser.add_argument("--summary", type=Path, help="New file for sanitized final status only")
    parser.add_argument("--launch", action="store_true", help="Launch the verified mod with observer attached before startup")
    parser.add_argument("--idle-seconds", type=float, default=0,
                        help="Stop diagnostics after this long without a verified client; 0 disables")
    args = parser.parse_args()
    if not launch_mod.desktop_process.is_desktop_child():
        raise ValueError("Запускай диагностику через Start-MAX-Mod.cmd или установленный ярлык MAX Mod.")
    if not 0 <= args.idle_seconds <= 3600 or not 0 <= args.seconds <= 86400:
        parser.error("Invalid diagnostic lifetime")
    startup_status.begin(args.manifest, role="observe")
    try:
        _run_observer(args)
    except startup_status.StartupError as error:
        startup_status.fail(error.code)
        raise
    except Exception:
        startup_status.fail("observer_start_failed")
        raise startup_status.StartupError("observer_start_failed") from None


def _run_observer(args):
    with _startup_step("verify", "payload_invalid"):
        executable = launch_mod.verified_executable(args.manifest)
        manifest = build_mod.verify(args.manifest)
        observation = Observation()
        rules = rules_from_manifest(manifest)
        runtime_id = launch_mod.runtime_identity(args.manifest)
    with _startup_step("frida_initialize", "observer_start_failed"):
        monitor = Monitor(executable.parent, observation)
    launched = threading.Event()

    def status():
        return {"schema": 1, "verified": True, "rules": rules,
                **observation.snapshot(), "notes": NOTES}

    def identity():
        count, scanned = monitor.activity()
        main_count, main_scanned = monitor.main_activity()
        state = "starting" if not launched.is_set() or not scanned or not main_scanned else "running" if main_count else "idle"
        return {"schema": 1, "kind": "max-mod-privacy-server", "runtimeId": runtime_id,
                "serverPid": os.getpid(), "clientState": state, "processes": count,
                "observerState": _observer_state(worker)}

    # Own the port before any MAX spawn. A simultaneous second launcher cannot
    # start a second instrumented client when this bind is rejected.
    # Old privacy-only builds and synthetic startup tests never open a settings
    # file. This feature-specific store is lazy; absent files remain absent until
    # an explicit Save from the native quick-reply editor.
    with _startup_step("setup_stores", "observer_start_failed"):
        quick_replies = None
        session_transfer = None
        mdata = None
        account_settings = None
        ghost_settings = None
        ghost_runtime = None
        chat_export = None
        message_retention = None
        if any(patch.get("id", "").startswith("ui_privacy.retention_history")
               for file in manifest.get("files", []) for patch in file.get("patches", [])):
            from message_retention_service import MessageRetentionService
            config_path, _ = launch_mod.prepare_config(check_only=True)
            message_retention = MessageRetentionService(config_path.parent)
            monitor.message_retention = message_retention
        if any(patch.get("id", "").startswith("ui_privacy.chat_export_menu")
               for file in manifest.get("files", []) for patch in file.get("patches", [])):
            from chat_export_service import ChatExportService
            chat_export = ChatExportService(activity=monitor.activity)
            monitor.chat_export = chat_export
        if any(patch.get("id", "").startswith("ui_privacy.mdata_ghost_")
               for file in manifest.get("files", []) for patch in file.get("patches", [])):
            from ghost_store import GhostSettingsStore
            from ghost_runtime import GhostRuntime
            config_path, _ = launch_mod.prepare_config(check_only=True)
            ghost_settings = GhostSettingsStore(config_path.parent / "ghost-settings.json")
            ghost_runtime = GhostRuntime()
            monitor.ghost_store, monitor.ghost_runtime = ghost_settings, ghost_runtime
        if any(patch.get("id", "").startswith("ui_privacy.account_")
               for file in manifest.get("files", []) for patch in file.get("patches", [])):
            from account_preferences import AccountPreferencesStore
            config_path, _ = launch_mod.prepare_config(check_only=True)
            account_settings = AccountPreferencesStore(config_path.parent / "account-settings.json")
        if any(patch.get("id", "").startswith("ui_privacy.quick_replies_")
               for file in manifest.get("files", []) for patch in file.get("patches", [])):
            from quick_reply_store import QuickReplyStore
            config_path, _ = launch_mod.prepare_config(check_only=True)
            quick_replies = QuickReplyStore(config_path.parent / "quick-replies.json")
        if any(patch.get("id", "").startswith("ui_privacy.session_transfer_")
               for file in manifest.get("files", []) for patch in file.get("patches", [])):
            from session_transfer import SessionTransfer
            config_path, _ = launch_mod.prepare_config(check_only=True)
            session_transfer = SessionTransfer(config_path.parent, monitor.profile_activity)
        if any(patch.get("id", "").startswith("ui_privacy.mdata_")
               for file in manifest.get("files", []) for patch in file.get("patches", [])):
            from mdata_service import MDataService
            config_path, _ = launch_mod.prepare_config(check_only=True)
            mdata = MDataService(config_path.parent, monitor.profile_activity,
                                 restart_activity=monitor.restart_activity)
        from chat_lock_service import ChatLockService
        config_path, _ = launch_mod.prepare_config(check_only=True)
        chat_locks = ChatLockService(config_path.parent, before_protect=monitor.prepare_protected_notifications)
        monitor.chat_locks = chat_locks
        from inline_media import InlineMediaService
        inline_media = InlineMediaService(args.manifest.resolve().parent.parent / ".mxmod-helper-temp" / "inline-media")
        from optimization_store import OptimizationSettingsStore, native_client_running
        optimization_settings = OptimizationSettingsStore(config_path.parent / "optimization-settings.json")
        from support_diagnostics import SupportDiagnosticsService
        def current_startup():
            receipt = startup_status.current()
            return None if receipt is None else receipt.try_read()
        support_diagnostics = SupportDiagnosticsService(valmax_version=manifest.get("valmax_version"), max_version=manifest.get("max_version"),
            privacy=status, startup=current_startup,
            ghost=None if ghost_settings is None else ghost_settings.snapshot,
            optimization=optimization_settings.snapshot)
    with _startup_step("bind", "port_busy"):
        server = LoopbackServer(("127.0.0.1", PORT), handler_factory(status, identity, quick_replies, session_transfer, mdata, account_settings, ghost_settings, ghost_runtime, chat_export, message_retention, chat_locks, inline_media, monitor.notification_status, optimization_settings, support_diagnostics))
    try:
        with _startup_step("optimization", "optimization_prepare_failed"):
            # The exclusive port is owned before writing the launch snapshot. A
            # helper attaching to a surviving client adopts its existing snapshot.
            existing_client = not args.launch or native_client_running(executable)
            optimization_settings.activate(executable.parent, existing_client=existing_client)
    except Exception:
        server.server_close()
        raise
    try:
        with _startup_step("observer", "observer_start_failed"):
            worker = threading.Thread(target=monitor.run, name="MAX privacy observer", daemon=True)
            http = threading.Thread(target=server.serve_forever, name="MAX privacy status", daemon=True)
            http.start()
    except Exception:
        server.server_close()
        raise
    print(f"MAX Mod privacy status: http://127.0.0.1:{PORT}/status", flush=True)
    deadline = time.monotonic() + args.seconds if args.seconds else None
    idle = IdleDeadline(args.idle_seconds)
    startup_ready = False
    try:
        if args.launch:
            # Monitor.launch records spawn and protective ACKs separately. The
            # outer step also classifies a replacement/testing monitor's failure.
            with _startup_step("native_spawn", "native_spawn_failed"):
                monitor.launch(executable)
        launched.set()
        # Finish spawn/attach before polling discovers the same PID.
        with _startup_step("observer", "observer_start_failed"):
            worker.start()
        while deadline is None or time.monotonic() < deadline:
            count, scanned = monitor.activity()
            if mdata is not None and mdata.restart_requested():
                break
            main_count, main_scanned = monitor.main_activity()
            if not startup_ready and _observer_state(worker) == "stopped":
                raise startup_status.StartupError("observer_worker_stopped")
            if not startup_ready and scanned and main_scanned and (main_count or not args.launch):
                startup_status.ready()
                startup_ready = True
            if idle.expired(main_count, main_scanned, time.monotonic()) and not ((session_transfer and session_transfer.pending()) or (mdata and mdata.pending()) or (chat_export and chat_export.pending())):
                break
            time.sleep(.25)
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stopping.set()
        server.shutdown()
        server.server_close()
        if chat_export is not None:
            chat_export.close()
        if worker.ident is not None:
            worker.join(timeout=10)
        if worker.ident is None or not worker.is_alive():
            try:
                # The Python module caches its manager. Explicit close releases
                # the helper's temporary native assets before this process exits.
                bounded_rpc(frida.shutdown)
            except Exception:
                pass  # Optional SDK cleanup cannot replace a completed job.
        if args.summary is not None:
            # Exclusive creation; neither a profile nor an existing log is overwritten.
            with args.summary.open("x", encoding="utf-8") as stream:
                json.dump(status(), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
    if mdata is not None and mdata.restart_requested(recheck=True):
        # The old HTTP port is released and the explicit worker has completed
        # before a new launcher is started. Native stdout remains discarded.
        launch_mod._quiet_process(launch_mod.helper_command("launcher", [
                                   "--manifest", str(args.manifest)]), executable.parent)


if __name__ == "__main__":
    main()
