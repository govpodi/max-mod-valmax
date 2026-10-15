"""Conservative closed-client policy shared by MData observers and recovery.

Every MAX-named process may use MXMOD, wherever its executable is installed.
Only the version-pinned original executable *and* original sibling config DLL
are excluded. No manifest label, directory name, profile or credential is read.
An unreadable, changed or unfamiliar image is never treated as official.
"""
from collections import OrderedDict
from contextlib import contextmanager
import ctypes
from ctypes import wintypes as w
import hashlib
import ntpath
import os
import threading


NAMES = frozenset(("max.exe", "max-service.exe"))
ORIGINAL_IMAGES = {
    "max.exe": (79444816, "d79837f5090c63bce820818355d9cc05b73b8c9bf5da9dddb47cdaf11aea3182",
                875856, "8e7d9d53f77d0f2599ed2bb5b117b3c8a4256ee361434ebd3d977b01d4685035"),
    "max-service.exe": (123454288, "bd82b3fa0a6d24ee87bb9e0ec32ee311df7c40d8d233a71537685af00d50e0eb",
                        875856, "950531233749920b60fdcb05a0465eb84f8f2acf3fbb76fc4d1279cb67f714f3"),
}


class _FileInfo(ctypes.Structure):
    _fields_ = [("attributes", w.DWORD), ("creation", w.FILETIME),
                ("access", w.FILETIME), ("write", w.FILETIME),
                ("volume", w.DWORD), ("size_high", w.DWORD), ("size_low", w.DWORD),
                ("links", w.DWORD), ("index_high", w.DWORD), ("index_low", w.DWORD)]


class _BasicInfo(ctypes.Structure):
    _fields_ = [("creation", ctypes.c_longlong), ("access", ctypes.c_longlong),
                ("write", ctypes.c_longlong), ("change", ctypes.c_longlong),
                ("attributes", w.DWORD)]


class _HeldImage:
    def __init__(self, api, handle):
        self.api, self.handle = api, handle

    def identity(self):
        info, basic = _FileInfo(), _BasicInfo()
        if (not self.api.GetFileInformationByHandle(self.handle, ctypes.byref(info))
                or not self.api.GetFileInformationByHandleEx(self.handle, 0, ctypes.byref(basic), ctypes.sizeof(basic))
                or info.attributes & (0x10 | 0x400) or info.links != 1):
            raise ValueError("Unverifiable MAX image")
        return (info.volume, info.index_high, info.index_low,
                (info.size_high << 32) | info.size_low,
                basic.write, basic.change, info.attributes, info.links)

    def read(self, size):
        data, read = ctypes.create_string_buffer(size), w.DWORD()
        if not self.api.ReadFile(self.handle, data, size, ctypes.byref(read), None):
            raise ValueError("Unverifiable MAX image")
        return data.raw[:read.value]


@contextmanager
def _held_image(path):
    # FILE_SHARE_READ only pins both object identity and bytes while hashing or
    # accepting a cache hit. Reparse points, directories and hardlinks fail.
    if os.name != "nt":
        raise ValueError("Unverifiable MAX image")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = (w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE)
    api.CreateFileW.restype = w.HANDLE
    api.CloseHandle.argtypes, api.CloseHandle.restype = (w.HANDLE,), w.BOOL
    api.GetFileInformationByHandle.argtypes = (w.HANDLE, ctypes.POINTER(_FileInfo))
    api.GetFileInformationByHandle.restype = w.BOOL
    api.GetFileInformationByHandleEx.argtypes = (w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD)
    api.GetFileInformationByHandleEx.restype = w.BOOL
    api.ReadFile.argtypes = (w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p)
    api.ReadFile.restype = w.BOOL
    handle = api.CreateFileW(path, 0x80000000, 1, None, 3, 0x08200000, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ValueError("Unverifiable MAX image")
    try:
        yield _HeldImage(api, handle)
    finally:
        api.CloseHandle(handle)


class OriginalImagePolicy:
    """Hash once per held file identity/size/write/change metadata, bounded LRU."""
    def __init__(self, *, opener=None, pins=None, cache_limit=64):
        self.opener = opener or _held_image
        self.pins = ORIGINAL_IMAGES if pins is None else pins
        self.cache_limit = cache_limit
        self.cache, self.lock = OrderedDict(), threading.Lock()

    def original(self, path, name):
        try:
            if type(name) is not str or name.lower() not in self.pins:
                return False
            value = os.fspath(path)
            if (type(value) is not str or "\0" in value or not ntpath.isabs(value)
                    or value.startswith(("\\\\", "//"))
                    or ntpath.basename(value).lower() != name.lower()):
                return False
            size, expected, config_size, config_expected = self.pins[name.lower()]
            config = ntpath.join(ntpath.dirname(value), "config.dll")
            # Hold both at once: replacing the DLL between two independent
            # hashes must not turn a namespace-modified pair into "official".
            with self.opener(value) as executable, self.opener(config) as configuration:
                return (self._matches_held(executable, size, expected)
                        and self._matches_held(configuration, config_size, config_expected))
        except Exception:
            return False

    def _matches_held(self, image, size, expected):
        identity = image.identity()
        if identity[3] != size:
            return False
        key = (identity, expected)
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
        digest, remaining = hashlib.sha256(), size
        while remaining:
            data = image.read(min(1024 * 1024, remaining))
            if not data or len(data) > remaining:
                return False
            digest.update(data)
            remaining -= len(data)
        if image.read(1) or image.identity() != identity:
            return False
        matches = digest.hexdigest() == expected
        with self.lock:
            self.cache[key] = matches
            self.cache.move_to_end(key)
            while len(self.cache) > self.cache_limit:
                self.cache.popitem(last=False)
        return matches


_POLICY = OriginalImagePolicy()


def original_image(path, name):
    return _POLICY.original(path, name)


def scan_clients(processes, process_path):
    """Return (possibly MXMOD-using clients, complete path scan).

    A known path with unverified bytes counts as a client. An unavailable path
    also fails the scan closed. Callers never attach or terminate these PIDs.
    """
    count, complete = 0, True
    try:
        for process in processes:
            name = process.name.lower()
            if name not in NAMES:
                continue
            try:
                path = process_path(process.pid)
                if path is None or not ntpath.isabs(os.fspath(path)):
                    complete = False
                    continue
                if not original_image(path, name):
                    count += 1
            except Exception:
                complete = False
    except Exception:
        complete = False
    return count, complete
