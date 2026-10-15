"""Verify the pinned mod and private config before launching MAX Mod.

The official ONEME profile and Windows credentials are never opened. Passing
--check-only performs reads only, including when the MXMOD config is absent.
UI builds normally use local diagnostics; --without-diagnostics launches the
verified client directly for manual recovery. Child output is discarded.
"""
from __future__ import annotations

import sys

# Also covers build_mod's lazily imported patch modules in --check-only mode.
sys.dont_write_bytecode = True

import argparse
from concurrent.futures import Future, InvalidStateError, TimeoutError as FutureTimeout
from contextlib import ExitStack, contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request

# Support direct execution by Start-MAX-Mod.cmd and module/test imports.
_MODULE_DIRECTORY = str(Path(__file__).resolve().parent)
if not sys.path or sys.path[0] != _MODULE_DIRECTORY:
    sys.path.insert(0, _MODULE_DIRECTORY)
import build_mod
import desktop_process
import startup_status
from startup_status import StartupError
from helper_command import command as helper_command, default_manifest


DEFAULT_MANIFEST = default_manifest()
DIAGNOSTICS_PORT = 18973
# Cold frozen startup verifies the payload and acknowledges several protection
# bridges. Their individual bounded calls must fit inside the outer deadline.
DIAGNOSTICS_START_TIMEOUT = 60.0
DIAGNOSTICS_IDLE_TIMEOUT = 25.0
DESKTOP_START_TIMEOUT = 100.0
MAX_IDENTITY_BYTES = 4096
REQUIRED_COMPONENTS = ("accounts", "privacy", "isolation")
# Full MAX 26.35.1.80662 defaults, extracted from config.dll's embedded JSON.
# The local file replaces the embedded config; a sparse override is insufficient.
CONFIG_TEMPLATE_PATH = Path(__file__).with_name("config.defaults.json")
CONFIG_TEMPLATE_SHA256 = "0a822dc4316554f9e2210091263337c41a3ba0b4888d519dcfcedf249330955a"
CONFIG_DEFAULTS = {
    "client_friendly_name": "MAX Mod",
    "app_folder_name": "MXMOD",
    "single_instance_id": "mxmod_instance",
    "local_server_pipe_name": "mxmod_local_pipe",
    "auto_update_enabled": False,
    "rooms_auto_update_enabled": False,
    "need_mask_logs": True,
    "send_error_logs_to_crash_server": False,
}
MAX_CONFIG_BYTES = 64 * 1024
_REPARSE_POINT = 0x400
_CODEX_PACKAGE_FAMILY = "OpenAI.Codex_2p2nqsd0c76g0"


def _normalized(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def runtime_identity(manifest_path: Path) -> str:
    """Hash UTF-8 normalized resolved parent + NUL + exact manifest bytes.

    This public runtime identifier contains no profile, credential or account
    information. Both launcher and diagnostics server use this exact format.
    Call only after the normal version-pinned build verification.
    """
    path = manifest_path.resolve(strict=True)
    value = _normalized(path.parent).encode("utf-8") + b"\0" + path.read_bytes()
    return hashlib.sha256(value).hexdigest()


def _privacy_ui_enabled(manifest_path: Path) -> bool:
    # verified_executable already validated the complete manifest and patches.
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    return "ui_privacy" in value["components"]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def _read_diagnostics_identity(timeout: float) -> dict | None:
    """Read a bounded loopback identity; None means connection was refused.

    A busy, unrelated or malformed listener is an error, never permission to
    spawn another MAX process. Environment proxy settings are not consulted.
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(
        f"http://127.0.0.1:{DIAGNOSTICS_PORT}/identity",
        headers={"Host": f"127.0.0.1:{DIAGNOSTICS_PORT}", "Accept": "application/json"})
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise StartupError("observer_identity_invalid")
            data = response.read(MAX_IDENTITY_BYTES + 1)
    except urllib.error.URLError as error:
        reason = error.reason
        if isinstance(error, urllib.error.HTTPError):
            error.close()
        if isinstance(reason, ConnectionRefusedError) or getattr(reason, "errno", None) in (61, 111, 10061):
            return None
        raise StartupError("observer_unavailable") from None
    except (TimeoutError, OSError):
        raise StartupError("observer_unavailable") from None
    if len(data) > MAX_IDENTITY_BYTES:
        raise StartupError("observer_identity_invalid")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=_invalid_json_constant)
    except (UnicodeDecodeError, ValueError):
        raise StartupError("observer_identity_invalid") from None
    if (type(value) is not dict or type(value.get("schema")) is not int or value["schema"] != 1
            or value.get("kind") != "max-mod-privacy-server"
            or type(value.get("runtimeId")) is not str or len(value["runtimeId"]) != 64
            or any(character not in "0123456789abcdef" for character in value["runtimeId"])
            or type(value.get("serverPid")) is not int or value["serverPid"] <= 0
            or value.get("clientState") not in ("starting", "running", "idle")
            or type(value.get("processes")) is not int or value["processes"] < 0):
        raise StartupError("observer_identity_invalid")
    if "observerState" in value and value["observerState"] not in ("starting", "running", "stopped"):
        raise StartupError("observer_identity_invalid")
    keys = ("schema", "kind", "runtimeId", "serverPid", "clientState", "processes")
    return {key: value[key] for key in (*keys, *(('observerState',) if 'observerState' in value else ()))}


def _diagnostics_identity(timeout: float = 1.0) -> dict | None:
    # A refused Windows TCP connect can take longer than our one-second total
    # HTTP deadline. Probe exclusive ownership first, without starting a server.
    # The real server still arbitrates races with its own exclusive bind.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", DIAGNOSTICS_PORT))
            return None
    except OSError as error:
        # Windows may report WSAEACCES instead of WSAEADDRINUSE when the
        # existing owner also used SO_EXCLUSIVEADDRUSE. Only probe identity;
        # this condition by itself never permits a second server/client spawn.
        if getattr(error, "errno", None) not in (48, 98, 10013, 10048) and getattr(error, "winerror", None) not in (10013, 10048):
            raise StartupError("port_unavailable") from None
    # urllib's socket timeout is per read, not a total deadline. A daemon
    # request worker also bounds a listener that drips bytes indefinitely.
    future = Future()

    def query():
        try:
            result = _read_diagnostics_identity(timeout)
            future.set_result(result)
        except InvalidStateError:
            pass  # Deadline won; a late response must have no effect.
        except Exception as error:
            try:
                future.set_exception(error if isinstance(error, StartupError) else StartupError("observer_identity_invalid"))
            except InvalidStateError:
                pass

    threading.Thread(target=query, name="MAX diagnostics identity", daemon=True).start()
    try:
        identity = future.result(timeout=timeout)
        if identity is None:
            raise StartupError("port_unavailable")
        return identity
    except FutureTimeout:
        future.cancel()
        raise StartupError("observer_unavailable") from None


def _matching_diagnostics(identity: dict, expected: str) -> str:
    if identity["runtimeId"] != expected:
        raise StartupError("port_other_runtime")
    if identity["clientState"] == "idle":
        raise StartupError("observer_idle")
    if identity.get("observerState") == "stopped":
        raise StartupError("observer_worker_stopped")
    if identity.get("observerState") == "starting":
        return "starting"
    return identity["clientState"]


def _wait_idle_diagnostics(identity: dict | None) -> dict | None:
    """Let a closed client's old helper release its port without terminating it.

    This also handles switching from a development copy to the installed copy.
    A running foreign client remains an explicit conflict, never a fallback.
    """
    deadline = time.monotonic() + DIAGNOSTICS_IDLE_TIMEOUT
    while identity is not None and identity["clientState"] == "idle":
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise StartupError("observer_idle")
        time.sleep(min(0.2, remaining))
        try:
            identity = _diagnostics_identity(timeout=min(1.0, max(0.05, remaining)))
        except StartupError as error:
            # A known idle server can disappear between bind probing and HTTP.
            # Keep waiting for exclusive free ownership; never infer it from
            # a refused HTTP connection alone.
            if error.code not in ("port_unavailable", "observer_unavailable"):
                raise
    return identity


def _observer_failure(default: str) -> StartupError:
    """Use only this operation's finite receipt, never a previous error log."""
    report = startup_status.current()
    value = report.try_read() if report is not None else None
    if value is not None and value["state"] == "error":
        return StartupError(value["code"])
    return StartupError(default)


def _start_observer(manifest_path: Path, executable: Path):
    try:
        return _quiet_process(helper_command("observe", ["--manifest", str(manifest_path),
                              "--launch", "--idle-seconds", "20"]), executable.parent)
    except (OSError, ValueError):
        raise StartupError("observer_start_failed") from None


def _quiet_process(command: list[str], cwd: Path, *, parent_waits: bool = False):
    from external_environment import native_environment
    with native_environment() as environment:
        environment.pop("MAX_MOD_STARTUP_PARENT_WAITS", None)
        if os.name == "nt":
            options = {"cwd": cwd}
            if command and Path(command[0]).name.casefold() == "max.exe":
                from business_environment import child_environment
                from optimization_store import child_environment as optimization_environment
                config_path, _ = prepare_config(check_only=True)
                options["env"] = optimization_environment(child_environment(environment, config_path.parent))
            if parent_waits:
                options["env"] = {**options.get("env", environment), "MAX_MOD_STARTUP_PARENT_WAITS": "1"}
            if bool(getattr(sys, "frozen", False)):
                options["env"] = {**options.get("env", environment), "PYINSTALLER_RESET_ENVIRONMENT": "1"}
            return desktop_process.popen(command, **options)
        return subprocess.Popen(command, cwd=str(cwd), stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                close_fds=True)


def _prepare_helper_temp(runtime: Path):
    """Keep helper temporary assets on the verified application's volume.

    Only this process environment changes; no user/system setting changes.
    Frida is imported in a child after the environment has been inherited.
    """
    parent = runtime.parent
    with ExitStack() as held:
        for ancestor in (*reversed(parent.parents), parent):
            held.enter_context(_hold_directory(ancestor))
        target = parent / ".mxmod-helper-temp"
        try:
            target.mkdir()
        except FileExistsError:
            pass
        physical = held.enter_context(_hold_directory(target))
        os.environ["TMP"] = os.environ["TEMP"] = str(physical)
        return physical


def launch_diagnostics(manifest_path: Path, executable: Path) -> str:
    """Reuse the same observer or start one exclusive observer before MAX.

    The server must bind its exclusive HTTP port before spawning MAX. This
    prevents concurrent launchers from spawning duplicate clients. There is no
    automatic direct-launch fallback if observation cannot be confirmed.
    """
    manifest_path = manifest_path.resolve(strict=True)
    expected = runtime_identity(manifest_path)
    startup_status.stage("observer")
    existing = _wait_idle_diagnostics(_diagnostics_identity())
    if existing is not None:
        state = _matching_diagnostics(existing, expected)
        if state == "running":
            return "already running"
    process = None
    if existing is None:
        process = _start_observer(manifest_path, executable)
    deadline = time.monotonic() + DIAGNOSTICS_START_TIMEOUT
    while time.monotonic() < deadline:
        identity = _diagnostics_identity(timeout=min(1.0, max(0.05, deadline - time.monotonic())))
        if identity is not None:
            state = _matching_diagnostics(identity, expected)
            if state == "running":
                return "started" if process is not None else "already running"
        # A second launcher may have won the exclusive bind. Always inspect
        # identity before treating this child server's exit as failure. The
        # venv Python redirector can also exit while its base-Python child owns
        # the server; a matching starting identity must continue to running.
        if process is not None and process.poll() is not None and identity is None:
            raise _observer_failure("observer_start_failed")
        if process is None and identity is None:
            # A previous starting helper exited. Only an exclusive free-port
            # probe permits a replacement; no running client is terminated.
            process = _start_observer(manifest_path, executable)
        time.sleep(0.1)
    raise _observer_failure("observer_timeout")


def _patch_identity(patch: dict) -> tuple:
    return (patch.get("kind", "replace"),) + tuple(patch[key] for key in ("id", "offset", "expected", "replacement"))


def verified_executable(manifest_path: Path) -> Path:
    """Require every pinned patch, rather than trusting a components label."""
    manifest_path = manifest_path.resolve(strict=True)
    manifest = build_mod.verify(manifest_path)
    if manifest.get("format") != 1 or manifest.get("max_version") != "26.35.1.80662":
        raise ValueError("Unsupported mod manifest/version")
    components = manifest.get("components")
    if (not isinstance(components, list)
            or any(type(value) is not str for value in components)
            or len(components) != len(set(components))
            or not set(REQUIRED_COMPONENTS) <= set(components)
            or not set(components) <= set(REQUIRED_COMPONENTS) | {"ui_privacy"}):
        raise ValueError("The launcher requires accounts, privacy and isolation together")
    root = manifest_path.parent
    destination = manifest.get("destination")
    # The literal self-directory marker preserves all payload and patch checks.
    if (destination != "." and (not isinstance(destination, str) or not Path(destination).is_absolute()
            or _normalized(Path(destination)) != _normalized(root))):
        raise ValueError("Manifest destination does not match the actual mod directory")
    expected = build_mod.specifications(components)
    expected_by_path = {_normalized(build_mod.safe_relative(item["relative_path"])): item for item in expected}
    actual_by_path = {}
    for item in manifest["files"]:
        key = _normalized(build_mod.safe_relative(item["relative_path"]))
        if key in actual_by_path:
            raise ValueError("Duplicate mod manifest file")
        actual_by_path[key] = item
    if set(actual_by_path) != set(expected_by_path):
        raise ValueError("Manifest does not contain the complete pinned mod")
    for key, spec in expected_by_path.items():
        record = actual_by_path[key]
        if (record.get("sha256") != spec["sha256"]
                or sorted(map(_patch_identity, record["patches"])) != sorted(map(_patch_identity, spec["patches"]))):
            raise ValueError(f"Unexpected or missing pinned patches: {spec['relative_path']}")
    return build_mod.payload_file(root, "max.exe")


def _unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate config key: {key}")
        result[key] = value
    return result


def _invalid_json_constant(value: str):
    raise ValueError(f"Invalid JSON constant: {value}")


def _parse_config(data: bytes) -> dict:
    if len(data) > MAX_CONFIG_BYTES:
        raise ValueError("MXMOD config is too large")
    try:
        value = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                           parse_constant=_invalid_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid UTF-8 JSON in MXMOD config") from error
    if not isinstance(value, dict):
        raise ValueError("MXMOD config must be a JSON object")
    return value


def _config_template() -> dict:
    data = CONFIG_TEMPLATE_PATH.read_bytes()
    if hashlib.sha256(data).hexdigest() != CONFIG_TEMPLATE_SHA256:
        raise ValueError("The pinned MAX config template is missing or modified")
    value = _parse_config(data)
    if len(value) != 74 or not CONFIG_DEFAULTS.keys() <= value.keys():
        raise ValueError("The pinned MAX config template has an unexpected schema")
    return value


def make_config() -> dict:
    """Return a fresh full 74-field config with the required mod overrides.

    MAX consumes local config as a complete document. Preserve its transport,
    UI and timeout defaults instead of writing only the identity/privacy flags.
    Each call verifies the shipped template hash and returns independent lists.
    """
    value = _config_template()
    value.update(CONFIG_DEFAULTS)
    return value


def validate_config(data: bytes) -> dict:
    """Require the full version-pinned schema and exact critical mod values."""
    value = _parse_config(data)
    for key, default in _config_template().items():
        if key not in value or type(value[key]) is not type(default):
            raise ValueError(f"Missing or invalid required MXMOD config field: {key}")
        if isinstance(default, list) and any(type(item) is not str for item in value[key]):
            raise ValueError(f"Invalid MXMOD config list item type: {key}")
    for key, expected in CONFIG_DEFAULTS.items():
        actual = value.get(key)
        if type(actual) is not type(expected) or actual != expected:
            raise ValueError(f"Unsafe or missing MXMOD config value: {key}")
    return value


def _local_root(local_appdata: Path | None) -> Path:
    if local_appdata is None:
        value = os.environ.get("LOCALAPPDATA")
        if not value:
            raise ValueError("LOCALAPPDATA is missing; no profile location can be selected safely")
        local_appdata = Path(value)
    path = Path(local_appdata)
    if (not path.is_absolute() or str(path).startswith(("\\\\", "//"))
            or any(part in (".", "..") or part.endswith((".", " ")) for part in path.parts)
            or any(part.casefold() == "oneme" for part in path.parts)):
        raise ValueError("Unsafe LOCALAPPDATA path")
    if os.name == "nt" and any(":" in part for part in path.parts[1:]):
        raise ValueError("Alternate streams are not profile directories")
    return path


def _check_path(path: Path, *, directory: bool) -> None:
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT):
        raise ValueError(f"Reparse points and symlinks are forbidden: {path}")
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"Profile ancestor is not a directory: {path}")
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError(f"Config must be a regular file with one link: {path}")


def _windows_api():
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    api.CloseHandle.restype = wintypes.BOOL
    api.GetFileInformationByHandleEx.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                                ctypes.c_void_p, wintypes.DWORD)
    api.GetFileInformationByHandleEx.restype = wintypes.BOOL
    api.GetFinalPathNameByHandleW.argtypes = (wintypes.HANDLE, wintypes.LPWSTR,
                                             wintypes.DWORD, wintypes.DWORD)
    api.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    api.GetCurrentPackageFamilyName.argtypes = (ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR)
    api.GetCurrentPackageFamilyName.restype = wintypes.LONG
    api.GetPackagesByPackageFamily.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.UINT),
                                              ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.UINT),
                                              wintypes.LPWSTR)
    api.GetPackagesByPackageFamily.restype = wintypes.LONG
    return api


def _current_package_family_name() -> str | None:
    """Query the process identity; never infer it from an environment string."""
    if os.name != "nt":
        return None
    from ctypes import wintypes

    api = _windows_api()
    length = wintypes.UINT()
    result = api.GetCurrentPackageFamilyName(ctypes.byref(length), None)
    if result == 15700:  # APPMODEL_ERROR_NO_PACKAGE
        return None
    if result != 122 or not 1 < length.value <= 256:  # ERROR_INSUFFICIENT_BUFFER
        raise ValueError(f"Cannot establish the current package family: {result}")
    buffer = ctypes.create_unicode_buffer(length.value)
    result = api.GetCurrentPackageFamilyName(ctypes.byref(length), buffer)
    if result != 0:
        raise ValueError(f"Cannot read the current package family: {result}")
    family = buffer.value
    if (not family or not family.isascii() or family in (".", "..")
            or family.endswith((".", " "))
            or any(not (character.isalnum() or character in ".-_") for character in family)):
        raise ValueError("Invalid current package family")
    return family


def _installed_codex_package() -> bool:
    """Verify only the known Codex family in the current user's package DB.

    Codex's child Python reports no package identity on this host while the
    inherited filesystem VFS still redirects new LocalAppData files. This
    narrowly scoped fallback is used only for the exact Codex overlay path.
    """
    if os.name != "nt":
        return False
    from ctypes import wintypes

    api = _windows_api()
    count, length = wintypes.UINT(), wintypes.UINT()
    result = api.GetPackagesByPackageFamily(_CODEX_PACKAGE_FAMILY, ctypes.byref(count), None,
                                           ctypes.byref(length), None)
    if result == 0 and count.value == 0:
        return False
    if result != 122 or not 0 < count.value <= 32 or not 0 < length.value <= 16384:
        raise ValueError(f"Cannot verify the installed Codex package: {result}")
    names = (wintypes.LPWSTR * count.value)()
    buffer = ctypes.create_unicode_buffer(length.value)
    result = api.GetPackagesByPackageFamily(_CODEX_PACKAGE_FAMILY, ctypes.byref(count), names,
                                           ctypes.byref(length), buffer)
    if result != 0:
        raise ValueError(f"Cannot read the installed Codex package: {result}")
    return count.value > 0 and all(name and name.startswith("OpenAI.Codex_")
                                    and name.endswith("_2p2nqsd0c76g0") for name in names[:count.value])


def _allowed_package_overlay(expected: Path, actual: Path) -> bool:
    """Accept only an exact MXMOD suffix in a proven package's LocalCache."""
    local = _local_root(None)
    try:
        relative = expected.relative_to(local)
    except ValueError:
        return False
    if not relative.parts or relative.parts[0].casefold() != "mxmod":
        return False
    family = _current_package_family_name()
    if family is not None:
        mapped = local / "Packages" / family / "LocalCache" / "Local" / relative
        return _normalized(actual) == _normalized(mapped)
    mapped = local / "Packages" / _CODEX_PACKAGE_FAMILY / "LocalCache" / "Local" / relative
    # Query the package DB only after exact path matching, with no wildcard or
    # fallback to other installed apps. Explorer's ordinary path needs neither.
    return _normalized(actual) == _normalized(mapped) and _installed_codex_package()


def _validate_handle(api, handle, path: Path, *, directory: bool) -> Path:
    class AttributeTagInfo(ctypes.Structure):
        _fields_ = [("attributes", ctypes.c_uint32), ("tag", ctypes.c_uint32)]

    info = AttributeTagInfo()
    if not api.GetFileInformationByHandleEx(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    if info.attributes & _REPARSE_POINT or bool(info.attributes & 0x10) != directory:
        raise ValueError(f"Unsafe profile handle: {path}")
    buffer = ctypes.create_unicode_buffer(32768)
    length = api.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    if not length or length >= len(buffer):
        raise ValueError(f"Cannot resolve profile handle safely: {path}")
    final = buffer.value
    if final.startswith("\\\\?\\"):
        final = final[4:]
    actual = Path(final)
    if _normalized(actual) != _normalized(path) and not _allowed_package_overlay(path, actual):
        raise ValueError(f"Profile handle was redirected: {path}")
    return actual


@contextmanager
def _hold_directory(path: Path):
    """Prevent junction edits and directory renames while using the profile."""
    _check_path(path, directory=True)
    if os.name != "nt":
        if _normalized(path.resolve(strict=True)) != _normalized(path):
            raise ValueError(f"Profile ancestor was redirected: {path}")
        yield path
        return
    api = _windows_api()
    # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES; share read only;
    # OPEN_EXISTING; open reparse itself. READ_ATTRIBUTES alone is only a
    # metadata handle on Windows and does not reliably block directory rename.
    # Directory children may still be created; the parent cannot be retargeted.
    handle = api.CreateFileW(str(path), 0x81, 1, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        actual = _validate_handle(api, handle, path, directory=True)
        with ExitStack() as physical_parents:
            if _normalized(actual) != _normalized(path):
                local = _local_root(None)
                # Overlay acceptance never permits a filesystem reparse point.
                # Hold every physical package ancestor as well as the alias so
                # it cannot be retargeted before the config is opened/written.
                for ancestor in reversed(actual.parents):
                    if ancestor != local and ancestor.is_relative_to(local):
                        physical_parents.enter_context(_hold_directory(ancestor))
            yield actual
    finally:
        api.CloseHandle(handle)


@contextmanager
def _config_stream(path: Path, *, create: bool):
    """Open the config without following links; creation is always exclusive."""
    if os.name != "nt":
        if not create:
            _check_path(path, directory=False)
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL if create else os.O_RDONLY
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
    else:
        import msvcrt

        api = _windows_api()
        access = 0xC0000000 if create else 0x80000000
        handle = api.CreateFileW(str(path), access, 0 if create else 1, None,
                                 1 if create else 3, 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            _validate_handle(api, handle, path, directory=False)
            fd = msvcrt.open_osfhandle(handle, (os.O_RDWR if create else os.O_RDONLY) | os.O_BINARY)
        except BaseException:
            api.CloseHandle(handle)
            raise
    with os.fdopen(fd, "w+b" if create else "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("MXMOD config cannot have hard links or special file types")
        yield stream


def _read_config(path: Path) -> None:
    with _config_stream(path, create=False) as stream:
        validate_config(stream.read(MAX_CONFIG_BYTES + 1))


def prepare_config(local_appdata: Path | None = None, *, check_only: bool = False) -> tuple[Path, str]:
    """Return (config path, status), touching only a safe MXMOD directory.

    Existing config bytes are never replaced. Ambiguous ancestors, reparse
    points, hard links, conflicting identity or unsafe flags stop the launch.
    Explicit local_appdata is intended for isolated temporary test fixtures.
    """
    # Check the template even in read-only mode with an absent profile, before
    # creating any directory. Failure cannot leave a new partial configuration.
    fresh_config = make_config()
    local = _local_root(local_appdata)
    root = local / "MXMOD"
    if not check_only:
        from mdata_recovery_state import root_candidate
        _ensure_no_recovery(root_candidate(local))
    config = root / "config.json"
    with ExitStack() as held:
        # Acquire from the drive root down so a checked parent cannot be
        # renamed or changed to a junction before its child is opened.
        for ancestor in (*reversed(local.parents), local):
            held.enter_context(_hold_directory(ancestor))
        try:
            root.lstat()
        except FileNotFoundError:
            if check_only:
                return config, "absent (will be created on manual launch)"
            root.mkdir()
        actual_root = held.enter_context(_hold_directory(root))
        reported_config = actual_root / "config.json"
        try:
            config.lstat()
        except FileNotFoundError:
            if check_only:
                return reported_config, "absent (will be created on manual launch)"
            data = (json.dumps(fresh_config, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            try:
                with _config_stream(config, create=True) as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                return reported_config, "created"
            except FileExistsError:
                # Another instance won exclusive creation; validate it instead.
                _read_config(config)
                return reported_config, "existing and validated"
        _check_path(config, directory=False)
        _read_config(config)
        return reported_config, "existing and validated"


def _ensure_no_recovery(root):
    from mdata_recovery_state import ensure_clear, RecoveryRequired
    try:
        ensure_clear(root)
    except RecoveryRequired:
        raise StartupError("recovery_required") from None


def _start_verified(args, executable, config_root):
    from session_transfer import transfer_lock, TransferError
    if args.without_diagnostics:
        from ghost_store import GhostSettingsStore
        if GhostSettingsStore(config_root / "ghost-settings.json").snapshot()["settings"]["enabled"]:
            raise ValueError("Режим призрака требует помощника ValMAX. Запускай обычным ярлыком без --without-diagnostics.")
        from message_retention_service import RetentionSettingsStore, RetentionServiceError
        try:
            retention_enabled = RetentionSettingsStore(config_root / "message-retention-settings.json").snapshot()["enabled"]
        except RetentionServiceError:
            raise ValueError("Не удалось проверить настройки сохранения удалённых сообщений. Запускай ValMAX обычным ярлыком.") from None
        if retention_enabled:
            raise ValueError("Сохранение удалённых сообщений требует помощника ValMAX. Запускай обычным ярлыком без --without-diagnostics.")
    try:
        with transfer_lock():
            _ensure_no_recovery(config_root)
            if not args.without_diagnostics and _privacy_ui_enabled(args.manifest):
                state = launch_diagnostics(args.manifest, executable)
                if state == "already running":
                    from client_activation import activate
                    if not activate(executable):
                        raise StartupError("window_activation_failed")
                if state == "already starting":
                    print("MAX Mod: диагностика уже запускается; повторный MAX не запущен.", flush=True)
                else:
                    print("MAX Mod: локальная диагностика подключена.", flush=True)
            else:
                from optimization_store import (OptimizationSettingsStore, OptimizationSettingsError,
                                                native_client_running)
                try:
                    OptimizationSettingsStore(config_root / "optimization-settings.json").activate(
                        executable.parent, existing_client=native_client_running(executable))
                except OptimizationSettingsError:
                    raise StartupError("optimization_prepare_failed") from None
                startup_status.stage("spawn")
                try:
                    process = _quiet_process([str(executable)], executable.parent)
                except (OSError, ValueError):
                    raise StartupError("native_spawn_failed") from None
                print(f"MAX Mod started for manual login (PID {process.pid}).", flush=True)
    except TransferError:
        raise StartupError("transfer_busy") from None


def _prepare_and_start(args, executable):
    from session_transfer import transfer_lock, TransferError
    try:
        with transfer_lock():
            config, status = prepare_config(check_only=args.check_only)
            print(f"Private MXMOD config: {config} [{status}]", flush=True)
            if args.check_only:
                print("Check completed. No files written; MAX was not started.", flush=True)
                return 0
            _start_verified(args, executable, config.parent)
            return 0
    except TransferError:
        raise StartupError("transfer_busy") from None


def main(argv: list[str] | None = None) -> int:
    # A source child has no bundle dispatcher to consume this one-shot hint.
    # It must never reach observer/MData background restart descendants.
    os.environ.pop("MAX_MOD_STARTUP_PARENT_WAITS", None)
    startup_status.reset()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--check-only", action="store_true", help="verify without writing files or launching MAX")
    parser.add_argument("--without-diagnostics", action="store_true", help="manual recovery: launch MAX directly without local diagnostics")
    parser.add_argument("--recover-mdata", action="store_true", help="Open local MData recovery for an interrupted restore")
    parser.add_argument("--import-mdata", action="store_true", help="Open MData login before signing in to MAX")
    args = parser.parse_args(argv)
    if sum((args.check_only, args.recover_mdata, args.import_mdata)) > 1:
        parser.error("--check-only, --recover-mdata and --import-mdata are mutually exclusive")
    failure_code = "payload_invalid"
    try:
        if os.name != "nt":
            raise StartupError("unsupported_platform")
        executable = verified_executable(args.manifest)
        args.manifest = args.manifest.resolve()
        print(f"Verified MAX Mod executable: {executable}", flush=True)
        print("Verified all pinned component patches.", flush=True)
        desktop_child = desktop_process.is_desktop_child()
        if not args.check_only:
            failure_code = "profile_guard_failed"
            _prepare_helper_temp(executable.parent)
            startup_status.begin(args.manifest, claim=not desktop_child)
        if not args.check_only and not desktop_child:
            failure_code = "desktop_bootstrap_failed"
            startup_status.stage("desktop")
            options = ["--manifest", str(args.manifest)]
            if args.without_diagnostics:
                options.append("--without-diagnostics")
            if args.recover_mdata:
                options.append("--recover-mdata")
            if args.import_mdata:
                options.append("--import-mdata")
            child = _quiet_process(helper_command("launcher", options), executable.parent,
                                   parent_waits=not (args.recover_mdata or args.import_mdata))
            if args.recover_mdata or args.import_mdata:
                return 0
            try:
                status = child.wait(timeout=DESKTOP_START_TIMEOUT)
                if status != 0:
                    startup_status.fail(startup_status.failure_code())
                return status
            except subprocess.TimeoutExpired:
                raise _observer_failure("desktop_bootstrap_timeout") from None
        if args.recover_mdata:
            from mdata_recovery import run_gui
            return run_gui()
        if args.import_mdata:
            from mdata_import import run_gui
            from mdata_recovery import launch_quietly
            return run_gui(launch=lambda: launch_quietly(manifest=args.manifest))
        failure_code = "profile_guard_failed"
        startup_status.stage("profile")
        status = _prepare_and_start(args, executable)
        if not args.check_only:
            startup_status.ready()
        return status
    except (OSError, ValueError, KeyError, TypeError) as error:
        code = error.code if isinstance(error, StartupError) else failure_code
        startup_status.fail(code)
        # Frozen stdout/stderr are NUL. Source diagnostics also use finite
        # application-owned codes, never arbitrary exception/config text.
        print("ValMAX launch refused: " + code, file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
