"""Quiet child creation from the verified ordinary desktop user's namespace.

No elevation, Explorer injection, shell commands or global virtualization changes.
The desktop process is queried only for its image/token/session and used as the
documented CreateProcess parent. No profile, credentials or archive is accessed.
Python children bootstrap their own NUL streams; caller handles are not inherited
from a different specified parent. Frozen helpers provide their own early NUL setup.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
from functools import lru_cache
import math
import ntpath
import os
from pathlib import Path
import runpy
import subprocess
import sys
import threading

DESKTOP_CHILD_MARKER = "MAX_MOD_DESKTOP_CHILD"


def is_desktop_child():
    """Loop-control marker only; this is not an authentication or path-policy gate."""
    return os.environ.get(DESKTOP_CHILD_MARKER) == "1"


class DesktopProcessError(ValueError):
    """Fixed messages only: no command, environment, path or token is formatted."""

    def __init__(self, code="native_error"):
        codes = {"invalid_command", "invalid_environment", "invalid_directory", "unsupported_platform",
                 "desktop_unavailable", "untrusted_desktop", "user_mismatch", "session_mismatch",
                 "elevated_desktop", "integrity_mismatch", "logon_mismatch", "native_error", "closed_process"}
        self.code = code if code in codes else "native_error"
        super().__init__("Desktop helper refused: " + self.code)


class _StartupInfo(ctypes.Structure):
    _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR), ("lpTitle", w.LPWSTR),
                ("dwX", w.DWORD), ("dwY", w.DWORD), ("dwXSize", w.DWORD), ("dwYSize", w.DWORD),
                ("dwXCountChars", w.DWORD), ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD),
                ("dwFlags", w.DWORD), ("wShowWindow", w.WORD), ("cbReserved2", w.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", w.HANDLE),
                ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE)]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", ctypes.c_void_p)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE), ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]


@lru_cache(maxsize=1)
def _api():
    if os.name != "nt":
        raise DesktopProcessError("unsupported_platform")
    class Api:
        pass
    api = Api()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    declarations = (
        (user, "GetShellWindow", (), w.HWND),
        (user, "GetWindowThreadProcessId", (w.HWND, ctypes.POINTER(w.DWORD)), w.DWORD),
        (kernel, "GetWindowsDirectoryW", (w.LPWSTR, w.UINT), w.UINT),
        (kernel, "OpenProcess", (w.DWORD, w.BOOL, w.DWORD), w.HANDLE),
        (kernel, "CloseHandle", (w.HANDLE,), w.BOOL),
        (kernel, "GetCurrentProcess", (), w.HANDLE),
        (kernel, "GetCurrentProcessId", (), w.DWORD),
        (kernel, "QueryFullProcessImageNameW", (w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)), w.BOOL),
        (kernel, "ProcessIdToSessionId", (w.DWORD, ctypes.POINTER(w.DWORD)), w.BOOL),
        (kernel, "InitializeProcThreadAttributeList", (ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t)), w.BOOL),
        (kernel, "UpdateProcThreadAttribute", (ctypes.c_void_p, w.DWORD, ctypes.c_size_t, ctypes.c_void_p,
                                              ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p), w.BOOL),
        (kernel, "DeleteProcThreadAttributeList", (ctypes.c_void_p,), None),
        (kernel, "CreateProcessW", (w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL,
                                   w.DWORD, ctypes.c_void_p, w.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(_ProcessInfo)), w.BOOL),
        (kernel, "WaitForSingleObject", (w.HANDLE, w.DWORD), w.DWORD),
        (kernel, "GetExitCodeProcess", (w.HANDLE, ctypes.POINTER(w.DWORD)), w.BOOL),
        (security, "OpenProcessToken", (w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)), w.BOOL),
        (security, "GetTokenInformation", (w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)), w.BOOL),
        (security, "EqualSid", (ctypes.c_void_p, ctypes.c_void_p), w.BOOL),
        (security, "GetSidSubAuthorityCount", (ctypes.c_void_p,), ctypes.POINTER(ctypes.c_ubyte)),
        (security, "GetSidSubAuthority", (ctypes.c_void_p, w.DWORD), ctypes.POINTER(w.DWORD)),
    )
    for library, name, args, result in declarations:
        method = getattr(library, name)
        method.argtypes, method.restype = args, result
        setattr(api, name, method)
    return api


def _norm(value):
    return ntpath.normcase(ntpath.normpath(value))


def _absolute(value):
    return (type(value) is str and bool(value) and "\0" not in value and ntpath.isabs(value)
            and not value.startswith(("\\\\", "//")) and ntpath.splitdrive(value)[0].endswith(":"))


def _command(command):
    if type(command) not in (list, tuple) or not command or len(command) > 128:
        raise DesktopProcessError("invalid_command")
    if any(type(part) is not str or "\0" in part or len(part) > 32767 for part in command) or not _absolute(command[0]):
        raise DesktopProcessError("invalid_command")
    if len(subprocess.list2cmdline(command)) > 32766:
        raise DesktopProcessError("invalid_command")
    return list(command)


def _child_command(command):
    # Python helper source runs in this worker before its target imports, so
    # even native/CRT writes use NUL. No secret is passed to this bootstrap.
    if ntpath.basename(command[0]).lower() in ("python.exe", "pythonw.exe"):
        if len(command) < 3 or command[1] != "-B" or not _absolute(command[2]) or not command[2].lower().endswith(".py"):
            raise DesktopProcessError("invalid_command")
        return [command[0], "-B", str(Path(__file__).resolve()), "--worker", *command[1:]]
    # GUI MAX / frozen helper: no caller handle inheritance. Frozen entrypoints
    # establish their own streams before any code which might print.
    return command


def _environment(value):
    if value is None:
        return None
    if type(value) is not dict or len(value) > 8192:
        raise DesktopProcessError("invalid_environment")
    seen, entries = set(), []
    for key, item in value.items():
        if (type(key) is not str or type(item) is not str or not key or "=" in key
                or "\0" in key or "\0" in item or key.casefold() in seen):
            raise DesktopProcessError("invalid_environment")
        seen.add(key.casefold())
        entries.append((key, item))
    text = "\0".join(key + "=" + item for key, item in sorted(entries, key=lambda pair: pair[0].casefold())) + "\0\0"
    if len(text) > 1024 * 1024:
        raise DesktopProcessError("invalid_environment")
    return ctypes.create_unicode_buffer(text)


def _token_data(api, token, kind):
    size = w.DWORD()
    api.GetTokenInformation(token, kind, None, 0, ctypes.byref(size))
    if not 0 < size.value <= 65536:
        raise DesktopProcessError()
    data = ctypes.create_string_buffer(size.value)
    if not api.GetTokenInformation(token, kind, data, len(data), ctypes.byref(size)) or not 0 < size.value <= len(data):
        raise DesktopProcessError()
    return data


def _sid(data):
    if len(data) < ctypes.sizeof(ctypes.c_void_p):
        raise DesktopProcessError()
    pointer = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p)).contents.value
    if not pointer:
        raise DesktopProcessError()
    return pointer


def _integrity(api, data):
    sid = _sid(data)
    count = api.GetSidSubAuthorityCount(sid)
    if not count or not 1 <= count[0] <= 15:
        raise DesktopProcessError()
    value = api.GetSidSubAuthority(sid, count[0] - 1)
    if not value:
        raise DesktopProcessError()
    return value[0]


def _verify_tokens(api, desktop):
    handles = []
    try:
        for process in (api.GetCurrentProcess(), desktop):
            token = w.HANDLE()
            if not api.OpenProcessToken(process, 8, ctypes.byref(token)) or not token.value:
                raise DesktopProcessError()
            handles.append(token.value)
        ours, theirs = handles
        user_ours, user_theirs = _token_data(api, ours, 1), _token_data(api, theirs, 1)
        if not api.EqualSid(_sid(user_ours), _sid(user_theirs)):
            raise DesktopProcessError("user_mismatch")
        elevated = _token_data(api, theirs, 20)
        if len(elevated) < 4:
            raise DesktopProcessError()
        if ctypes.cast(elevated, ctypes.POINTER(w.DWORD)).contents.value:
            raise DesktopProcessError("elevated_desktop")
        integrity_ours = _integrity(api, _token_data(api, ours, 25))
        integrity_theirs = _integrity(api, _token_data(api, theirs, 25))
        if integrity_theirs < 0x2000 or integrity_theirs > integrity_ours:
            raise DesktopProcessError("integrity_mismatch")
        statistics_ours, statistics_theirs = _token_data(api, ours, 10), _token_data(api, theirs, 10)
        if len(statistics_ours) < 16 or len(statistics_theirs) < 16:
            raise DesktopProcessError()
        if statistics_ours.raw[8:16] != statistics_theirs.raw[8:16]:
            raise DesktopProcessError("logon_mismatch")
    finally:
        for token in handles:
            api.CloseHandle(token)


def _verified_parent(api):
    hwnd, pid = api.GetShellWindow(), w.DWORD()
    if not hwnd or not api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
        raise DesktopProcessError("desktop_unavailable")
    desktop = api.OpenProcess(0x1080, False, pid.value)
    if not desktop:
        raise DesktopProcessError("desktop_unavailable")
    try:
        image, length = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
        windows = ctypes.create_unicode_buffer(32768)
        windows_length = api.GetWindowsDirectoryW(windows, len(windows))
        if (not api.QueryFullProcessImageNameW(desktop, 0, image, ctypes.byref(length))
                or not 0 < length.value < 32768 or not 0 < windows_length < len(windows)
                or _norm(image.value) != _norm(ntpath.join(windows.value, "explorer.exe"))):
            raise DesktopProcessError("untrusted_desktop")
        ours, theirs = w.DWORD(), w.DWORD()
        if (not api.ProcessIdToSessionId(api.GetCurrentProcessId(), ctypes.byref(ours))
                or not api.ProcessIdToSessionId(pid.value, ctypes.byref(theirs))):
            raise DesktopProcessError()
        if ours.value != theirs.value:
            raise DesktopProcessError("session_mismatch")
        _verify_tokens(api, desktop)
        return desktop
    except BaseException:
        api.CloseHandle(desktop)
        raise


class DesktopProcess:
    def __init__(self, api, handle, pid, args):
        self._api, self._handle, self.pid = api, handle, pid
        self.args, self.returncode = list(args), None
        self._lock = threading.RLock()

    def _finish(self):
        value = w.DWORD()
        if not self._api.GetExitCodeProcess(self._handle, ctypes.byref(value)):
            raise DesktopProcessError()
        self.returncode = value.value
        self._api.CloseHandle(self._handle)
        self._handle = None
        return self.returncode

    def poll(self):
        with self._lock:
            if self.returncode is not None:
                return self.returncode
            if self._handle is None:
                raise DesktopProcessError("closed_process")
            result = self._api.WaitForSingleObject(self._handle, 0)
            if result == 258:
                return None
            if result != 0:
                raise DesktopProcessError()
            return self._finish()

    def wait(self, timeout=None):
        if timeout is not None and (type(timeout) not in (int, float) or not math.isfinite(timeout)):
            raise ValueError("Invalid desktop helper timeout")
        milliseconds = 0xFFFFFFFF if timeout is None else min(0xFFFFFFFE, max(0, math.ceil(timeout * 1000)))
        with self._lock:
            if self.returncode is not None:
                return self.returncode
            if self._handle is None:
                raise DesktopProcessError("closed_process")
            result = self._api.WaitForSingleObject(self._handle, milliseconds)
            if result == 258:
                raise subprocess.TimeoutExpired(self.args, timeout)
            if result != 0:
                raise DesktopProcessError()
            return self._finish()

    def close(self):
        """Release only our handle; never terminates the child or desktop process."""
        with self._lock:
            if self._handle is not None:
                self._api.CloseHandle(self._handle)
                self._handle = None

    def __del__(self):
        try:
            self.close()
        except BaseException:
            pass


def popen(command, *, cwd=None, env=None):
    command = _command(command)
    child = _command(_child_command(command))
    if cwd is not None:
        try:
            cwd = os.fspath(cwd)
        except TypeError:
            raise DesktopProcessError("invalid_directory") from None
        if not _absolute(cwd):
            raise DesktopProcessError("invalid_directory")
    if env is not None and type(env) is not dict:
        raise DesktopProcessError("invalid_environment")
    child_env = dict(os.environ) if env is None else dict(env)
    # The marker is public loop-control. It never relaxes any filesystem guard.
    child_env[DESKTOP_CHILD_MARKER] = "1"
    environment = _environment(child_env)
    api = _api()
    parent, initialized, process = None, False, _ProcessInfo()
    try:
        parent = _verified_parent(api)
        size = ctypes.c_size_t()
        api.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        if not 0 < size.value <= 65536:
            raise DesktopProcessError()
        storage = ctypes.create_string_buffer(size.value)
        if not api.InitializeProcThreadAttributeList(storage, 1, 0, ctypes.byref(size)):
            raise DesktopProcessError()
        initialized = True
        parent_handle = w.HANDLE(parent)
        if not api.UpdateProcThreadAttribute(storage, 0, 0x00020000, ctypes.byref(parent_handle), ctypes.sizeof(parent_handle), None, None):
            raise DesktopProcessError()
        startup = _StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.lpAttributeList = ctypes.addressof(storage)
        arguments = ctypes.create_unicode_buffer(subprocess.list2cmdline(child))
        if not api.CreateProcessW(child[0], arguments, None, None, False, 0x08080400,
                                  environment, cwd, ctypes.byref(startup), ctypes.byref(process)):
            raise DesktopProcessError()
        if not process.hProcess or not process.hThread or not process.dwProcessId:
            raise DesktopProcessError()
        result = DesktopProcess(api, process.hProcess, process.dwProcessId, command)
        process.hProcess = None
        return result
    except DesktopProcessError:
        raise
    except Exception:
        raise DesktopProcessError() from None
    finally:
        for handle in (process.hThread, process.hProcess, parent):
            if handle:
                api.CloseHandle(handle)
        if initialized:
            api.DeleteProcThreadAttributeList(storage)


def redirect_stdio_to_null():
    descriptor = os.open(os.devnull, os.O_RDWR | getattr(os, "O_BINARY", 0))
    try:
        for target in (0, 1, 2):
            os.dup2(descriptor, target, inheritable=False)
    finally:
        if descriptor > 2:
            os.close(descriptor)
    sys.stdin = open(0, "r", encoding="utf-8", errors="replace", closefd=False)
    sys.stdout = open(1, "w", encoding="utf-8", errors="replace", closefd=False)
    sys.stderr = open(2, "w", encoding="utf-8", errors="replace", closefd=False)


def _worker(arguments):
    redirect_stdio_to_null()
    if len(arguments) < 2 or arguments[0] != "-B" or not _absolute(arguments[1]) or not arguments[1].lower().endswith(".py"):
        return 2
    script = arguments[1]
    os.environ[DESKTOP_CHILD_MARKER] = "1"
    sys.dont_write_bytecode = True
    sys.argv = arguments[1:]
    sys.path[0] = str(Path(script).parent)
    runpy.run_path(script, run_name="__main__")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] != "--worker":
        raise SystemExit(2)
    raise SystemExit(_worker(sys.argv[2:]))
