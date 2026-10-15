"""Restore an existing verified client's native main window from the tray.

Call only after the launcher has verified the executable and confirmed its
matching, ready helper. No client is spawned and no title, command line,
profile, credential or process memory is read. Windows foreground restrictions
are respected: True means the window is shown/restored; focus is best effort.

Qt's own Windows activation uses SW_SHOW for hidden windows and SW_RESTORE for
iconic ones. ShowWindowAsync keeps a hung client's GUI from blocking a launcher.
Sources: Microsoft ShowWindowAsync/SetForegroundWindow documentation; Qt 6.11.2
qwindowswindow.cpp requestActivateWindow and qwindowswindowclassregistry.cpp.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
import ntpath
import os
from pathlib import Path
import re
import time


_MAX_WINDOWS = 4096
_ACTIVATION_SECONDS = 2.5
_SCAN_ATTEMPTS = 51
_SHOW_SECONDS = 0.75
_SHOW_ATTEMPTS = 16
_WS_CHILD = 0x40000000
_WS_EX_TOOLWINDOW = 0x80
_WS_EX_NOACTIVATE = 0x08000000
_QT_MAIN_CLASS = re.compile(
    r"Qt6[0-9]{0,5}QWindow(?:OwnDC)?Icon"
    r"(?:\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\})?\Z")


def _normalized(value):
    if type(value) is not str or not value or "\0" in value:
        return None
    if value.startswith("\\\\?\\"):
        value = value[4:]
    drive, tail = ntpath.splitdrive(value)
    if (re.fullmatch(r"[A-Za-z]:", drive) is None or ":" in tail
            or not tail.startswith(("\\", "/"))
            or any(part in (".", "..") or part.endswith((".", " "))
                   for part in re.split(r"[\\/]", tail)[1:])
            or ntpath.basename(value).casefold() != "max.exe"):
        return None
    return ntpath.normcase(ntpath.normpath(value))


class _Windows:
    def __init__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        declarations = (
            (self.user, "EnumWindows", (self.callback_type, w.LPARAM), w.BOOL),
            (self.user, "GetWindowThreadProcessId", (w.HWND, ctypes.POINTER(w.DWORD)), w.DWORD),
            (self.user, "GetWindow", (w.HWND, w.UINT), w.HWND),
            (self.user, "GetWindowLongPtrW", (w.HWND, ctypes.c_int), ctypes.c_ssize_t),
            (self.user, "GetClassNameW", (w.HWND, w.LPWSTR, ctypes.c_int), ctypes.c_int),
            (self.user, "GetWindowRect", (w.HWND, ctypes.POINTER(w.RECT)), w.BOOL),
            (self.user, "IsWindowVisible", (w.HWND,), w.BOOL),
            (self.user, "IsIconic", (w.HWND,), w.BOOL),
            (self.user, "ShowWindowAsync", (w.HWND, ctypes.c_int), w.BOOL),
            (self.user, "SetForegroundWindow", (w.HWND,), w.BOOL),
            (self.kernel, "OpenProcess", (w.DWORD, w.BOOL, w.DWORD), w.HANDLE),
            (self.kernel, "QueryFullProcessImageNameW", (w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)), w.BOOL),
            (self.kernel, "WaitForSingleObject", (w.HANDLE, w.DWORD), w.DWORD),
            (self.kernel, "CloseHandle", (w.HANDLE,), w.BOOL),
        )
        for library, name, arguments, result in declarations:
            method = getattr(library, name)
            method.argtypes, method.restype = arguments, result

    def windows(self):
        result = []
        overflow = False

        def collect(hwnd, _parameter):
            nonlocal overflow
            if len(result) >= _MAX_WINDOWS:
                overflow = True
                return False
            result.append(int(hwnd))
            return True

        callback = self.callback_type(collect)
        if not self.user.EnumWindows(callback, 0) or overflow:
            return None
        return result

    def info(self, hwnd):
        pid = w.DWORD()
        if not self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
            return None
        name = ctypes.create_unicode_buffer(256)
        length = self.user.GetClassNameW(hwnd, name, len(name))
        if not 0 < length < len(name) - 1:
            return None
        rectangle = w.RECT()
        if not self.user.GetWindowRect(hwnd, ctypes.byref(rectangle)):
            return None
        values = []
        for index in (-16, -20):  # GWL_STYLE and GWL_EXSTYLE.
            ctypes.set_last_error(0)
            value = self.user.GetWindowLongPtrW(hwnd, index)
            if value == 0 and ctypes.get_last_error():
                return None
            values.append(value & 0xFFFFFFFF)
        return {"pid": int(pid.value), "owner": int(self.user.GetWindow(hwnd, 4) or 0),
                "style": values[0], "exstyle": values[1], "class": name.value,
                "width": rectangle.right - rectangle.left,
                "height": rectangle.bottom - rectangle.top,
                "visible": bool(self.user.IsWindowVisible(hwnd)),
                "iconic": bool(self.user.IsIconic(hwnd))}

    def open_process(self, pid):
        # Limited executable-path metadata plus liveness; never VM_READ.
        return self.kernel.OpenProcess(0x101000, False, pid)

    def image(self, process):
        name, length = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
        if (not self.kernel.QueryFullProcessImageNameW(process, 0, name, ctypes.byref(length))
                or not 0 < length.value < len(name)):
            return None
        return name.value

    def alive(self, process):
        return self.kernel.WaitForSingleObject(process, 0) == 258

    def close(self, process):
        self.kernel.CloseHandle(process)

    def show(self, hwnd, command):
        return bool(self.user.ShowWindowAsync(hwnd, command))

    def foreground(self, hwnd):
        return bool(self.user.SetForegroundWindow(hwnd))


def _eligible(info):
    return (type(info) is dict
            and type(info.get("pid")) is int and info["pid"] > 0
            and info.get("owner") == 0
            and type(info.get("style")) is int and not info["style"] & _WS_CHILD
            and type(info.get("exstyle")) is int
            and not info["exstyle"] & (_WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE)
            and type(info.get("class")) is str and _QT_MAIN_CLASS.fullmatch(info["class"]) is not None
            and type(info.get("width")) is int and info["width"] > 0
            and type(info.get("height")) is int and info["height"] > 0
            and type(info.get("visible")) is bool and type(info.get("iconic")) is bool)


def _activate(expected, api, *, clock=time.monotonic, sleep=time.sleep, deadline=None):
    handles = {}
    try:
        windows = api.windows()
        if windows is None:
            return False
        candidates = []
        for hwnd in windows:
            if deadline is not None and clock() >= deadline:
                return False
            info = api.info(hwnd)
            if not _eligible(info):
                continue
            pid = info["pid"]
            if pid not in handles:
                process = api.open_process(pid)
                handles[pid] = process or None
                if not (process and api.alive(process) and _normalized(api.image(process)) == expected):
                    if process:
                        api.close(process)
                    handles[pid] = None
            if handles[pid] is not None:
                candidates.append((hwnd, pid))
        # Never guess between independent main/media windows. Owned dialogs,
        # menus, tool windows and service windows have already been excluded.
        if len(candidates) != 1:
            return False
        hwnd, pid = candidates[0]
        process = handles[pid]

        def checked():
            if not api.alive(process) or _normalized(api.image(process)) != expected:
                return None
            fresh = api.info(hwnd)
            return fresh if _eligible(fresh) and fresh["pid"] == pid else None

        info = checked()
        if info is None:
            return False
        if info["iconic"] or not info["visible"]:
            if not api.show(hwnd, 9 if info["iconic"] else 5):
                return False
            show_deadline = clock() + _SHOW_SECONDS
            if deadline is not None:
                show_deadline = min(show_deadline, deadline)
            for _ in range(_SHOW_ATTEMPTS):
                info = checked()
                if info is None:
                    return False
                if info["visible"] and not info["iconic"]:
                    break
                remaining = show_deadline - clock()
                if remaining <= 0:
                    return False
                sleep(min(0.05, remaining))
            else:
                return False
        # Windows may deny focus even to a user-launched shortcut. The shown
        # native window is still usable; no thread-input tricks are attempted.
        api.foreground(hwnd)
        return True
    finally:
        for process in handles.values():
            if process:
                api.close(process)


def activate(executable):
    """Show/restore the exact existing client; False never permits an unsafe spawn."""
    try:
        value = os.fspath(executable) if isinstance(executable, (str, Path)) else None
        expected = _normalized(value)
        if expected is None or os.name != "nt":
            return False
        api = _Windows()
        deadline = time.monotonic() + _ACTIVATION_SECONDS
        for _ in range(_SCAN_ATTEMPTS):
            if _activate(expected, api, clock=time.monotonic, sleep=time.sleep, deadline=deadline):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.05, remaining))
        return False
    except (OSError, ValueError, TypeError, AttributeError):
        return False
