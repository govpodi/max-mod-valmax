"""Explicit first login with MData. Import and opening the GUI read no profile.

Only Submit starts the closed-client scan and transfer. Existing accounts are
preserved; the launcher is called after a successful add and after unlocking.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import queue
import threading


MESSAGES = {
    "invalid_password": "Введи пароль MData: от 12 до 1024 байт UTF-8.",
    "authentication_failed": "Пароль не подошёл или MData повреждена.",
    "client_still_running": "Закрой все копии MAX Mod через меню в трее → Выход.",
    "process_scan_failed": "Не удалось проверить закрытие MAX Mod. Заверши его и повтори попытку.",
    "busy": "Другая операция MData ещё выполняется. Подожди её завершения.",
    "recovery_required": "Предыдущий перенос не завершён. Сначала восстанови резервную MData.",
    "unsupported_version": "Версия MData не поддерживается этой сборкой.",
    "unsupported_credential": "В MData есть неподдерживаемые данные входа Windows.",
    "invalid_archive": "Файл MData не прошёл проверку.",
    "invalid_profile": "Данные аккаунтов не прошли проверку.",
    "invalid_payload": "Содержимое MData не прошло проверку.",
    "policy_violation": "MData содержит неподдерживаемые данные.",
    "unsafe_path": "Выбери файл MData вне папок данных MAX и MAX Mod.",
    "unsupported_path": "Расположение файла MData не поддерживается.",
    "too_large": "Размер MData превышает допустимый предел.",
    "crypto_unavailable": "Модуль шифрования недоступен.",
    "credential_io": "Не удалось сохранить данные входа в хранилище Windows.",
    "insufficient_space": "Недостаточно свободного места для входа с MData.",
    "no_accounts": "В этой MData нет данных для входа.",
    "account_limit": "Можно сохранить не более трёх аккаунтов MAX Mod.",
    "profile_changed": "Данные MAX Mod изменились. Закрой его и повтори попытку.",
    "profile_collision": "Данные одного из аккаунтов уже существуют. Вход остановлен.",
    "source_changed": "Файл MData изменился. Вход остановлен.",
    "backup_failed": "Не удалось сохранить резервную копию текущих аккаунтов.",
    "rollback_failed": "Перенос не завершён. Сохрани резервную MData и выполни восстановление.",
    "restart_failed": "Аккаунты добавлены. Открой MAX Mod обычным ярлыком.",
    "io_error": "Не удалось загрузить MData. Проверь файл и доступ к нему.",
}


class MDataImportError(Exception):
    def __init__(self, code):
        self.code = code if code in MESSAGES else "io_error"
        super().__init__(self.code)


def error_code(error):
    try:
        if isinstance(error, OSError) and (error.errno == 28 or getattr(error, "winerror", 0) == 112):
            return "insufficient_space"
        code = getattr(error, "code", None)
        if code == "destination_not_empty":
            return "profile_changed"
    except Exception:
        return "io_error"
    return code if isinstance(code, str) and code in MESSAGES else "io_error"


def prepare_profile():
    import launch_mod
    from mdata_recovery_state import ensure_clear, root_candidate
    ensure_clear(root_candidate())
    config, _status = launch_mod.prepare_config()
    return config.parent


def make_service(root, activity):
    from mdata_service import MDataService
    return MDataService(root, activity)


class ImportJob:
    """Inert headless controller; injected dependencies permit isolated tests."""
    def __init__(self, *, activity=None, prepare=None, service_factory=None,
                 lock_factory=None, launch=None, guard=None):
        # Wrappers defer all optional/native imports until the explicit action.
        self._activity, self._prepare = activity, prepare or prepare_profile
        self._service_factory, self._transfer_lock = service_factory or make_service, lock_factory
        self._launch, self._guard = launch, guard
        self._lock, self.events = threading.Lock(), queue.Queue()
        self.thread, self.busy, self.committed = None, False, False

    def start(self, selected_file, password):
        from mdata_recovery import validate_password
        from mdata_service import selected_path
        try:
            validate_password(password)
            path = selected_path(selected_file, "import")
        except Exception as error:
            raise MDataImportError(error_code(error)) from None
        with self._lock:
            if self.busy or self.committed:
                raise MDataImportError("busy")
            self.busy = True
        try:
            self.thread = threading.Thread(target=self._work, args=(path, password),
                                           name="MData-import", daemon=True)
            self.thread.start()
        except Exception:
            with self._lock:
                self.busy = False
            self.thread = None
            raise MDataImportError("io_error") from None

    def _closed(self):
        from mdata_recovery import owned_client_activity
        try:
            count, scanned = (self._activity or owned_client_activity)()
        except Exception:
            raise MDataImportError("process_scan_failed") from None
        if type(count) is not int or count < 0 or scanned is not True:
            raise MDataImportError("process_scan_failed")
        if count:
            raise MDataImportError("client_still_running")
        return 0, True

    def _work(self, path, password):
        try:
            from mdata_recovery import launch_quietly
            from mdata_recovery_state import ensure_clear
            from session_transfer import transfer_lock
            with (self._transfer_lock or transfer_lock)():
                self._closed()
                root = self._prepare()
                (self._guard or ensure_clear)(root)
                if path.is_relative_to(root):
                    raise MDataImportError("unsafe_path")
                self._closed()
                service = self._service_factory(root, self._closed)
                result, profiles = service._import(path, password, "add")
                self.committed = True
                password = None
                # Status persistence is best effort and never retries an import.
                try:
                    service._action = "import"
                    service._set("done", result=result, profiles=profiles)
                    service._persist()
                except Exception:
                    pass
            try:
                (self._launch or launch_quietly)()
            except Exception:
                raise MDataImportError("restart_failed") from None
            event = {"state": "done", "error": None}
        except Exception as error:
            event = {"state": "error", "error": error_code(error)}
        finally:
            password = None
            with self._lock:
                self.busy = False
        self.events.put(event)


def run_gui(**dependencies):
    import tkinter as tk
    from tkinter import filedialog, ttk

    window = tk.Tk()
    window.title("Войти с MData — MAX Mod")
    window.resizable(False, False)
    window.option_add("*Font", "{Segoe UI} 10")
    body = ttk.Frame(window, padding=20)
    body.grid(sticky="nsew")
    ttk.Label(body, text="Войти с MData", font=("Segoe UI", 13, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
    ttk.Label(body, text="Выбери MData и введи пароль, заданный при её создании.\n"
              "Закрой MAX Mod через меню в трее → Выход.\n"
              "Текущие аккаунты сохранятся. После входа MAX Mod откроется сам.",
              wraplength=430, justify="left").grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 16))
    selected, secret, status = tk.StringVar(value=""), tk.StringVar(value=""), tk.StringVar(value="Выбери файл MData.")
    ttk.Entry(body, textvariable=selected, state="readonly", width=48).grid(
        row=2, column=0, columnspan=2, sticky="ew")
    choose = ttk.Button(body, text="Выбрать MData")
    choose.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 14))
    ttk.Label(body, text="Пароль MData").grid(row=4, column=0, columnspan=2, sticky="w")
    entry = ttk.Entry(body, textvariable=secret, show="●", width=48)
    entry.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(4, 0))
    ttk.Label(body, textvariable=status, wraplength=430, justify="left").grid(
        row=6, column=0, columnspan=2, sticky="w", pady=(12, 16))
    submit_button, close = ttk.Button(body, text="Войти"), ttk.Button(body, text="Закрыть")
    submit_button.grid(row=7, column=0, sticky="ew", padx=(0, 8))
    close.grid(row=7, column=1, sticky="ew")
    job = ImportJob(**dependencies)

    def set_busy():
        for control in (choose, entry, submit_button):
            control.configure(state="disabled" if job.busy or job.committed else "normal")
        close.configure(state="disabled" if job.busy else "normal")

    def choose_file():
        if job.busy or job.committed:
            return
        try:
            value = filedialog.askopenfilename(parent=window, title="Выбрать MData",
                                               filetypes=[("MData", "*.mdata *.maxsession")])
            if value:
                selected.set(value)
                secret.set("")
                entry.focus_set()
        except Exception:
            status.set(MESSAGES["io_error"])

    def submit():
        if job.busy or job.committed:
            return
        password = secret.get()
        secret.set("")
        try:
            if not selected.get():
                raise MDataImportError("unsafe_path")
            job.start(Path(selected.get()).as_uri(), password)
        except Exception as error:
            status.set(MESSAGES[error_code(error)])
        else:
            status.set("Выполняется вход и запуск MAX Mod… Не закрывай это окно.")
        finally:
            password = None
        set_busy()

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
        set_busy()
        window.after(100, poll)

    choose.configure(command=choose_file)
    submit_button.configure(command=submit)
    close.configure(command=dismiss)
    window.protocol("WM_DELETE_WINDOW", dismiss)
    window.after(100, poll)
    window.mainloop()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Вход в MAX Mod с локальной MData.")
    parser.add_argument("--import-mdata", action="store_true", help="открыть окно входа с MData")
    parser.parse_args(argv)
    import launch_mod
    return launch_mod.main(["--import-mdata"])


if __name__ == "__main__":
    raise SystemExit(main())
