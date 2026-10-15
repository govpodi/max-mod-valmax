"""Restore the normal Windows DLL search path around an external child spawn.

PyInstaller's private DLL directory must not be inherited by the native MAX
client. This module never reads or writes a MAX profile or credential.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import sys
import threading

_spawn_lock = threading.RLock()


def clean_environment(environment, bundle_directory):
    """Return a copy without PATH entries anchored in the frozen bundle."""
    result = dict(environment)
    anchor = os.path.normcase(os.path.abspath(str(bundle_directory)))
    for key in result:
        if key.upper() != "PATH":
            continue
        kept = []
        for entry in result[key].split(os.pathsep):
            if entry:
                candidate = os.path.normcase(os.path.abspath(entry.strip('"')))
                try:
                    inside = os.path.commonpath((anchor, candidate)) == anchor
                except ValueError:
                    inside = False
                if inside:
                    continue
            kept.append(entry)
        result[key] = os.pathsep.join(kept)
    return result


class _DllDirectory:
    def __init__(self):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.GetDllDirectoryW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
        self.api.GetDllDirectoryW.restype = ctypes.c_uint32
        self.api.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
        self.api.SetDllDirectoryW.restype = ctypes.c_int

    def get(self):
        ctypes.set_last_error(0)
        size = self.api.GetDllDirectoryW(0, None)
        if not size:
            error = ctypes.get_last_error()
            if error:
                raise ctypes.WinError(error)
            return None
        if size > 32767:
            raise OSError("Invalid DLL search directory")
        buffer = ctypes.create_unicode_buffer(size + 1)
        ctypes.set_last_error(0)
        length = self.api.GetDllDirectoryW(len(buffer), buffer)
        if not length:
            error = ctypes.get_last_error()
            if error:
                raise ctypes.WinError(error)
            # An empty/default Windows DLL path has no copied characters.
            # GetDllDirectoryW(0, None) may still have returned one for NUL.
            return None
        if length >= len(buffer):
            raise OSError("Invalid DLL search directory")
        return buffer.value

    def set(self, directory):
        if not self.api.SetDllDirectoryW(directory):
            raise ctypes.WinError(ctypes.get_last_error())


@contextmanager
def native_environment(*, frozen=None, bundle_directory=None, environment=None, dll_api=None):
    """Yield a sanitized environment, restoring the caller even if spawn fails."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    environment = os.environ if environment is None else environment
    if not frozen:
        yield dict(environment)
        return
    bundle_directory = getattr(sys, "_MEIPASS", Path(sys.executable).parent) if bundle_directory is None else bundle_directory
    clean = clean_environment(environment, bundle_directory)
    if os.name != "nt" and dll_api is None:
        yield clean
        return
    with _spawn_lock:
        api = _DllDirectory() if dll_api is None else dll_api
        previous = api.get()
        api.set(None)
        try:
            yield clean
        finally:
            api.set(previous)
