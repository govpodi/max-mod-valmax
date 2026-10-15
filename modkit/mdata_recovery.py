"""Explicit local MData recovery UI. Never restores on import or ordinary launch.

Only the sanitized recovery marker is read when the explicit GUI starts. Process
enumeration and the recovery backend run after the user's Restore click. The
backend performs its own closed-client check under the transfer lock.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import time


MESSAGES = {
    "invalid_password": "Введи пароль резервной MData: от 12 до 1024 байт UTF-8.",
    "authentication_failed": "Пароль не подошёл или резервная MData повреждена.",
    "client_still_running": "Сначала закрой все копии MAX Mod через меню в трее → Выход.",
    "process_scan_failed": "Не удалось проверить закрытие MAX Mod. Повтори попытку после его завершения.",
    "no_backup": "Резервная копия для восстановления не найдена.",
    "recovery_required": "Не удалось проверить запись восстановления. Сохрани резервные файлы и проверь MAX Mod.",
    "invalid_request": "Запись восстановления отсутствует или уже обработана.",
    "unsupported_version": "Версия резервной MData не поддерживается этой сборкой.",
    "unsupported_credential": "В резервной MData есть неподдерживаемые данные входа Windows.",
    "invalid_archive": "Резервная MData не прошла проверку.",
    "invalid_profile": "Данные резервной копии не прошли проверку.",
    "invalid_payload": "Содержимое резервной MData не прошло проверку.",
    "policy_violation": "Резервная MData не соответствует правилам восстановления.",
    "unsafe_path": "Расположение резервной копии не прошло проверку.",
    "unsupported_path": "Расположение резервной копии не поддерживается.",
    "too_large": "Размер резервной MData превышает допустимый предел.",
    "crypto_unavailable": "Модуль шифрования недоступен.",
    "credential_io": "Не удалось восстановить данные входа в хранилище Windows.",
    "insufficient_space": "Недостаточно свободного места для восстановления.",
    "busy": "Другая операция MData ещё выполняется. Подожди её завершения.",
    "profile_changed": "Данные MAX Mod изменились. Закрой его и повтори попытку.",
    "source_changed": "Резервная копия изменилась. Восстановление остановлено.",
    "rollback_failed": "Восстановление не завершено. Резервная копия сохранена; не запускай MAX Mod до восстановления.",
    "restart_failed": "Данные восстановлены. Запусти MAX Mod позже обычным ярлыком.",
    "io_error": "Не удалось восстановить резервную MData. Проверь доступ к файлам и повтори попытку.",
}


class RecoveryError(Exception):
    def __init__(self, code):
        self.code = code if code in MESSAGES else "io_error"
        super().__init__(self.code)


def error_code(error):
    """Do not propagate exception text, paths, passwords or credential values."""
    try:
        code = getattr(error, "code", None)
    except Exception:
        return "io_error"
    return code if isinstance(code, str) and code in MESSAGES else "io_error"


def validate_password(value):
    if not isinstance(value, str) or "\0" in value:
        raise RecoveryError("invalid_password")
    try:
        length = len(value.encode("utf-8", errors="strict"))
    except UnicodeError:
        raise RecoveryError("invalid_password") from None
    if not 12 <= length <= 1024:
        raise RecoveryError("invalid_password")


def scan_owned_clients(processes, process_path, runtime_parent):
    """Conservative shared policy; runtime_parent remains an API compatibility arg.

    MAX clients at every root may share MXMOD. Only pinned original executable
    and config bytes are excluded, without attach, profile or credential access.
    """
    from max_client_policy import scan_clients
    return scan_clients(processes, process_path)


def owned_client_activity():
    # Lazy: neither Frida nor any MAX process is touched when this module imports.
    import frida
    import launch_mod
    from privacy_server import bounded_rpc, process_path
    try:
        processes = bounded_rpc(lambda: frida.get_local_device().enumerate_processes())
        return scan_owned_clients(processes, process_path, launch_mod.DEFAULT_MANIFEST.parent.parent)
    except Exception:
        return 0, False


def load_recovery_marker():
    from mdata_recovery_state import root_candidate, read
    root = root_candidate()
    return root, read(root)


def recover_selected_backup(root, password):
    # This is the first backend call which may read the encrypted archive,
    # existing profile or Windows credentials. It only runs after Restore.
    from mdata_service import recover_backup
    return recover_backup(root, password, activity=owned_client_activity)


def observer_port_is_free(*, port=18973, socket_factory=None):
    """Confirm freedom with exclusive bind; port override is for synthetic tests."""
    socket_factory = socket_factory or socket.socket
    try:
        with socket_factory(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", port))
            return True
    except OSError as error:
        if getattr(error, "errno", None) in (48, 98, 10013, 10048) or getattr(error, "winerror", None) in (10013, 10048):
            return False
        raise RecoveryError("restart_failed") from None


def wait_for_observer_exit(*, timeout=25.0, monotonic=None, sleep=None, is_free=None):
    """Bounded exclusive loopback binds only; never connects or kills a listener."""
    monotonic = monotonic or time.monotonic
    sleep = sleep or time.sleep
    is_free = is_free or observer_port_is_free
    deadline = monotonic() + timeout
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise RecoveryError("restart_failed")
        if is_free():
            return
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise RecoveryError("restart_failed")
        sleep(min(0.1, remaining))


def launch_quietly(*, manifest=None):
    # RecoveryJob invokes this on its background thread, after successful restore.
    import launch_mod
    wait_for_observer_exit()
    selected = launch_mod.DEFAULT_MANIFEST if manifest is None else Path(manifest)
    options = [] if manifest is None else ["--manifest", str(selected)]
    process = launch_mod._quiet_process(launch_mod.helper_command("launcher", options), selected.parent)
    try:
        if process.wait(timeout=20) != 0:
            raise RecoveryError("restart_failed")
    except subprocess.TimeoutExpired:
        raise RecoveryError("restart_failed") from None
    return process


class RecoveryJob:
    """Headless controller with injected dependencies for synthetic tests."""
    def __init__(self, root, *, activity, recover, launch):
        self.root = root
        self._activity, self._recover, self._launch = activity, recover, launch
        self._lock = threading.Lock()
        self.events = queue.Queue()
        self.thread = None
        self.busy = False
        self.recovered = False

    def start(self, password):
        validate_password(password)
        with self._lock:
            if self.busy or self.recovered:
                raise RecoveryError("busy")
            self.busy = True
        self.thread = threading.Thread(target=self._work, args=(password,),
                                       name="MData-recovery", daemon=True)
        try:
            self.thread.start()
        except Exception:
            with self._lock:
                self.busy = False
            raise RecoveryError("io_error") from None

    def _work(self, password):
        try:
            try:
                count, scanned = self._activity()
            except Exception:
                raise RecoveryError("process_scan_failed") from None
            if type(scanned) is not bool or not scanned or type(count) is not int or count < 0:
                raise RecoveryError("process_scan_failed")
            if count:
                raise RecoveryError("client_still_running")
            self._recover(self.root, password)
            self.recovered = True
            password = None
            try:
                self._launch()
            except Exception:
                raise RecoveryError("restart_failed") from None
            self.events.put({"state": "done", "error": None})
        except Exception as error:
            self.events.put({"state": "error", "error": error_code(error)})
        finally:
            password = None
            with self._lock:
                self.busy = False


def run_gui(*, load_marker=load_recovery_marker, activity=owned_client_activity,
            recover=recover_selected_backup, launch=launch_quietly):
    import tkinter as tk
    from tkinter import ttk

    metadata_error = None
    try:
        root, record = load_marker()
        if record is None:
            metadata_error = "no_backup"
    except Exception as error:
        root, record = None, None
        metadata_error = error_code(error)

    window = tk.Tk()
    window.title("Восстановление MData — MAX Mod")
    window.resizable(False, False)
    window.option_add("*Font", "{Segoe UI} 10")
    body = ttk.Frame(window, padding=20)
    body.grid(sticky="nsew")
    ttk.Label(body, text="Восстановить резервную копию", font=("Segoe UI", 13, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
    ttk.Label(body, text="Закрой все копии MAX Mod через меню в трее → Выход.\n"
              "Введи пароль MData, использованный при неудачной загрузке.\n"
              "После восстановления MAX Mod откроется автоматически.",
              wraplength=410, justify="left").grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 16))
    secret = tk.StringVar(value="")
    entry = ttk.Entry(body, textvariable=secret, show="●", width=48)
    entry.grid(row=2, column=0, columnspan=2, sticky="ew")
    status = tk.StringVar(value=MESSAGES[metadata_error] if metadata_error else "Резервная копия готова к восстановлению.")
    ttk.Label(body, textvariable=status, wraplength=410, justify="left").grid(
        row=3, column=0, columnspan=2, sticky="w", pady=(12, 16))
    job = RecoveryJob(root, activity=activity, recover=recover, launch=launch)
    restore = ttk.Button(body, text="Восстановить")
    restore.grid(row=4, column=0, sticky="ew", padx=(0, 8))
    close = ttk.Button(body, text="Закрыть")
    close.grid(row=4, column=1, sticky="ew")

    def set_busy(value):
        entry.configure(state="disabled" if value or metadata_error or job.recovered else "normal")
        restore.configure(state="disabled" if value or metadata_error or job.recovered else "normal")
        close.configure(state="disabled" if value else "normal")

    def submit():
        if job.busy or metadata_error or job.recovered:
            return
        password = secret.get()
        secret.set("")
        try:
            job.start(password)
        except Exception as error:
            status.set(MESSAGES[error_code(error)])
        else:
            status.set("Восстановление и запуск выполняются… Не закрывай это окно.")
            set_busy(True)
        finally:
            password = None

    def dismiss():
        if not job.busy:
            secret.set("")
            window.destroy()

    def poll():
        try:
            result = job.events.get_nowait()
        except queue.Empty:
            window.after(100, poll)
            return
        secret.set("")
        if result["state"] == "done":
            window.destroy()
            return
        status.set(MESSAGES[result["error"]])
        set_busy(False)
        window.after(100, poll)

    restore.configure(command=submit)
    close.configure(command=dismiss)
    window.protocol("WM_DELETE_WINDOW", dismiss)
    set_busy(False)
    if not metadata_error:
        entry.focus_set()
    window.after(100, poll)
    window.mainloop()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Явное локальное восстановление резервной MData MAX Mod.")
    parser.add_argument("--recover-mdata", action="store_true", help="открыть окно восстановления")
    args = parser.parse_args(argv)
    if not args.recover_mdata:
        parser.error("для открытия окна восстановления нужен явный --recover-mdata")
    # Both source and frozen recovery must enter the verified desktop context
    # before opening even the sanitized recovery marker. The launcher calls
    # run_gui directly once there, so this route cannot recurse.
    import launch_mod
    return launch_mod.main(["--recover-mdata"])


if __name__ == "__main__":
    raise SystemExit(main())
