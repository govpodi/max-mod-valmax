"""Finite, non-secret startup receipts shared by the installed helper roles.

The caller supplies the program's ``.mxmod-helper-temp`` directory, never a
profile directory. One small receipt is atomically replaced. A new launcher
must explicitly claim an operation; delayed children cannot overwrite another
operation. Import performs no filesystem, process or credential operation.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import uuid


FILENAME = "startup-status.json"
DIRECTORY = ".mxmod-helper-temp"
ENVIRONMENT_KEY = "MAX_MOD_STARTUP_OPERATION"
MAX_BYTES = 1024
ROLES = frozenset(("launcher", "observe", "recover", "mdata"))
STAGES = frozenset(("bootstrap", "import", "verify", "desktop", "profile",
                    "observer", "optimization", "spawn", "protection",
                    "recovery", "complete", "frida_initialize", "setup_stores",
                    "bind", "native_spawn", "protection_attach", "ready"))
STATES = frozenset(("starting", "ready", "error"))
_OPERATION = re.compile(r"[0-9a-f]{32}\Z")
_FIELDS = frozenset(("schema", "operationId", "role", "stage", "state", "code"))

# These strings are application-owned constants. Never derive a code or a
# message by formatting an exception, command, environment or profile value.
_MESSAGES = {
    "startup_failed": "Не удалось запустить ValMAX. Повтори запуск; если ошибка повторится, переустанови приложение.",
    "invalid_command": "Некорректная команда запуска ValMAX. Используй обычный ярлык приложения.",
    "unsupported_platform": "Эта версия ValMAX предназначена для Windows.",
    "import_failed": "Не удалось загрузить компоненты ValMAX. Повтори установку приложения.",
    "payload_invalid": "Файлы ValMAX отсутствуют, повреждены или принадлежат другой версии. Повтори установку приложения.",
    "desktop_unavailable": "Рабочий стол Windows недоступен. Дождись его загрузки и повтори запуск ValMAX.",
    "desktop_bootstrap_failed": "Не удалось запустить помощник ValMAX в текущем сеансе Windows. Повтори запуск обычным ярлыком.",
    "desktop_bootstrap_timeout": "Помощник ValMAX запускается дольше обычного. Подожди немного и повтори запуск.",
    "profile_guard_failed": "Не удалось безопасно открыть настройки ValMAX. Закрой другие копии приложения и повтори запуск.",
    "recovery_required": "Перенос MData не завершён. Открой «Восстановление MData» через меню «Пуск».",
    "transfer_busy": "Перенос аккаунтов ещё выполняется. Дождись его завершения и повтори запуск ValMAX.",
    "port_unavailable": "Локальный порт помощника ValMAX недоступен. Повтори запуск приложения.",
    "port_busy": "Локальный порт помощника ValMAX занят. Закрой другие копии ValMAX и повтори запуск.",
    "port_other_runtime": "Ещё работает помощник другой копии ValMAX. Полностью закрой её через значок в трее, подожди немного и повтори запуск.",
    "observer_idle": "Предыдущий помощник ValMAX завершает работу. Подожди немного и повтори запуск.",
    "observer_start_failed": "Не удалось запустить помощник ValMAX. Повтори запуск приложения.",
    "observer_timeout": "Помощник ValMAX не успел подтвердить запуск. Подожди немного и повтори запуск.",
    "observer_worker_stopped": "Помощник ValMAX остановился. Полностью закрой приложение и запусти его снова.",
    "observer_identity_invalid": "Не удалось проверить локальный помощник ValMAX. Закрой другие копии приложения и повтори запуск.",
    "observer_unavailable": "Локальный помощник ValMAX не отвечает. Подожди немного и повтори запуск.",
    "optimization_prepare_failed": "Не удалось подготовить настройки оптимизации ValMAX. Повтори запуск приложения.",
    "native_spawn_failed": "Не удалось открыть клиент ValMAX. Проверь установку приложения и повтори запуск.",
    "window_activation_failed": "ValMAX уже работает, но не удалось открыть её окно. Открой приложение через значок в трее.",
    "protection_ack_failed": "Не удалось подтвердить подключение защиты ValMAX. Приложение остановлено до входа в аккаунт.",
    "ghost_ack_failed": "Не удалось подтвердить режим призрака. ValMAX остановлена до входа в аккаунт; повтори запуск.",
    "retention_ack_failed": "Не удалось подтвердить сохранение удалённых сообщений. ValMAX остановлена до входа в аккаунт; повтори запуск.",
    "notification_ack_failed": "Не удалось подтвердить защиту уведомлений PIN-чатов. ValMAX остановлена до входа в аккаунт; повтори запуск.",
}
CODES = frozenset(_MESSAGES)
_STATUS_CODES = frozenset(("invalid_receipt", "unsafe_path", "store_busy", "io_error"))
_current = None
_role = "launcher"
_failure = None


class StartupError(ValueError):
    """A launch failure whose representation contains only a fixed code."""

    def __init__(self, code):
        if type(code) is not str or code not in CODES:
            raise ValueError("Invalid startup code")
        self.code = code
        super().__init__(code)


class StartupStatusError(ValueError):
    """Receipt I/O failure, separate from the primary launch failure."""

    def __init__(self, code):
        if type(code) is not str or code not in _STATUS_CODES:
            raise ValueError("Invalid startup status code")
        self.code = code
        super().__init__(code)


def message_for(code):
    """Return a fixed Russian message; only recovery_required mentions MData."""
    if type(code) is not str or code not in CODES:
        raise ValueError("Invalid startup code")
    return _MESSAGES[code]


def new_operation_id():
    return uuid.uuid4().hex


def _operation_id(value):
    if type(value) is not str or _OPERATION.fullmatch(value) is None:
        raise ValueError("Invalid startup operation")
    return value


def operation_from_environment(environment=None):
    """Missing means a new root launch; invalid values are rejected, not echoed."""
    source = os.environ if environment is None else environment
    value = source.get(ENVIRONMENT_KEY)
    return None if value is None else _operation_id(value)


def child_environment(environment, operation_id):
    """Return a copy containing a validated public correlation ID."""
    return {**environment, ENVIRONMENT_KEY: _operation_id(operation_id)}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StartupStatusError("invalid_receipt")
        result[key] = value
    return result


def _validate(value):
    if (type(value) is not dict or set(value) != _FIELDS
            or type(value["schema"]) is not int or value["schema"] != 1
            or type(value["operationId"]) is not str
            or _OPERATION.fullmatch(value["operationId"]) is None
            or type(value["role"]) is not str or value["role"] not in ROLES
            or type(value["stage"]) is not str or value["stage"] not in STAGES
            or type(value["state"]) is not str or value["state"] not in STATES
            or type(value["code"]) is not str
            or (value["state"] == "error" and value["code"] not in CODES)
            or (value["state"] != "error" and value["code"] != "none")):
        raise StartupStatusError("invalid_receipt")
    return dict(value)


def parse(data):
    if type(data) is not bytes or len(data) > MAX_BYTES:
        raise StartupStatusError("invalid_receipt")
    try:
        value = json.loads(data.decode("utf8"), object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, RecursionError):
        raise StartupStatusError("invalid_receipt") from None
    return _validate(value)


def _read(path):
    # Lazy imports avoid the launch_mod -> startup_status -> store cycle.
    from launch_mod import _check_path, _config_stream
    try:
        _check_path(path, directory=False)
        with _config_stream(path, create=False) as stream:
            return parse(stream.read(MAX_BYTES + 1))
    except FileNotFoundError:
        return None


def _atomic_write(path, data):
    from launch_mod import _check_path, _config_stream
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    identity = None
    try:
        with _config_stream(temporary, create=True) as stream:
            info = os.fstat(stream.fileno())
            identity = (info.st_dev, info.st_ino)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _check_path(temporary, directory=False)
        info = temporary.lstat()
        if (info.st_dev, info.st_ino) != identity:
            raise StartupStatusError("unsafe_path")
        # Validate the destination again just before replacement. In
        # particular, a hard link or malformed receipt is never overwritten.
        _read(path)
        os.replace(temporary, path)
        identity = None
    finally:
        if identity is not None:
            try:
                _check_path(temporary, directory=False)
                info = temporary.lstat()
                if (info.st_dev, info.st_ino) == identity:
                    temporary.unlink()
            except FileNotFoundError:
                pass


class StartupStatus:
    def __init__(self, helper_temp, role="launcher", operation_id=None):
        from quick_reply_store import QuickReplyStore, QuickReplyStoreError
        if type(role) is not str or role not in ROLES:
            raise ValueError("Invalid startup role")
        self.role = role
        self.operation_id = new_operation_id() if operation_id is None else _operation_id(operation_id)
        try:
            self._guard = QuickReplyStore(Path(helper_temp) / FILENAME)
            if self._guard.path.parent.name != DIRECTORY:
                raise StartupStatusError("unsafe_path")
        except (QuickReplyStoreError, TypeError, ValueError):
            raise StartupStatusError("unsafe_path") from None
        self.path = self._guard.path
        self.stage = "bootstrap"

    @contextmanager
    def _operation(self, *, create):
        from quick_reply_store import QuickReplyStoreError
        try:
            with self._guard._operation(create=create) as path:
                yield path
        except StartupStatusError:
            raise
        except QuickReplyStoreError as error:
            raise StartupStatusError(error.code if error.code in _STATUS_CODES else "io_error") from None
        except OSError:
            raise StartupStatusError("io_error") from None
        except ValueError:
            raise StartupStatusError("unsafe_path") from None

    def read(self, expected_operation_id=None):
        expected = self.operation_id if expected_operation_id is None else _operation_id(expected_operation_id)
        with self._operation(create=False) as path:
            value = None if path is None else _read(path)
            return value if value is not None and value["operationId"] == expected else None

    def write(self, stage, state="starting", code="none", *, claim=False):
        """Return False for an unclaimed/stale child; claim is explicit and root-only.

        All roles may update an already claimed ID. An interactive root
        launcher/recovery/import may claim ownership of the one fixed receipt.
        """
        if type(claim) is not bool or claim and (self.role == "observe" or state != "starting"):
            raise ValueError("Invalid startup claim")
        value = _validate({"schema": 1, "operationId": self.operation_id,
                           "role": self.role, "stage": stage, "state": state, "code": code})
        data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf8")
        from quick_reply_store import _writer_lock
        with self._operation(create=claim) as path:
            if path is None:
                return False
            with _writer_lock(path):
                current = _read(path)
                if not claim and (current is None or current["operationId"] != self.operation_id):
                    return False
                # Preserve the first precise child failure rather than
                # replacing it with a later parent timeout or ready signal.
                if not claim and current["state"] == "error":
                    return current == value
                _atomic_write(path, data)
                self.stage = stage
        return True

    def begin(self, stage="bootstrap", *, claim=False):
        return self.write(stage, claim=claim)

    def ready(self):
        return self.write("complete", "ready")

    def fail(self, code, *, stage=None):
        return self.write(self.stage if stage is None else stage, "error", code)

    def try_write(self, stage, state="starting", code="none", *, claim=False):
        """Receipt failure must never replace the primary startup failure."""
        try:
            return self.write(stage, state, code, claim=claim)
        except StartupStatusError:
            return False

    def try_read(self, expected_operation_id=None):
        try:
            return self.read(expected_operation_id)
        except StartupStatusError:
            return None


def set_role(role):
    """The dispatcher sets a fixed role before importing application modules."""
    global _role
    if type(role) is not str or role not in ROLES:
        raise ValueError("Invalid startup role")
    _role = role


def current():
    return _current


def reset():
    """Clear process-local state only; preserve the fixed role and inherited ID."""
    global _current, _failure
    _current, _failure = None, None


def begin(manifest, role=None, *, claim=False, operation_id=None):
    """Configure receipt after program verification, then begin bootstrap.

    Root launchers claim a fresh ID. Children inherit that public ID. Source
    launcher invocations without an ID can start their own operation even when
    already bootstrapped; an observer never claims ownership by itself.
    Receipt failures are optional and cannot prevent launching the client.
    """
    global _current, _failure
    _failure = None
    chosen_role = _role if role is None else role
    if type(chosen_role) is not str or chosen_role not in ROLES or type(claim) is not bool:
        raise ValueError("Invalid startup role or claim")
    try:
        inherited = operation_from_environment() if operation_id is None else _operation_id(operation_id)
    except ValueError:
        inherited = None
    if claim:
        chosen_id = new_operation_id() if operation_id is None else _operation_id(operation_id)
    else:
        chosen_id = inherited
        if chosen_id is None:
            if chosen_role == "observe":
                _current = None
                return None
            chosen_id, claim = new_operation_id(), True
    try:
        path = Path(manifest)
        if not path.is_absolute() or path.name != "mod-manifest.json":
            raise StartupStatusError("unsafe_path")
        report = StartupStatus(path.parent.parent / DIRECTORY, chosen_role, chosen_id)
    except (StartupStatusError, TypeError, ValueError):
        _current = None
        return None
    _current = report
    os.environ[ENVIRONMENT_KEY] = chosen_id
    report.try_write("bootstrap", claim=claim)
    return report


def stage(name):
    """Set a finite stage; this is a setter, not a context manager."""
    if type(name) is not str or name not in STAGES:
        raise ValueError("Invalid startup stage")
    report = current()
    if report is None:
        return False
    report.stage = name
    return report.try_write(name)


def fail(code):
    global _failure
    if type(code) is not str or code not in CODES:
        raise ValueError("Invalid startup code")
    if _failure is None:
        _failure = code
    report = current()
    return False if report is None else report.try_write(report.stage, "error", code)


def ready():
    report = current()
    return False if report is None else report.try_write("complete", "ready")


def failure_code(default="startup_failed"):
    """Read only this operation's finite failure; stale receipts are ignored."""
    if type(default) is not str or default not in CODES:
        raise ValueError("Invalid startup code")
    report = current()
    value = None if report is None else report.try_read()
    return value["code"] if value is not None and value["state"] == "error" else _failure or default
