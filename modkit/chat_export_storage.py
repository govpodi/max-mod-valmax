"""Owned, atomic Windows export workspace; never reads a MAX profile."""
from contextlib import contextmanager, ExitStack
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import threading
import uuid

from launch_mod import _check_path, _config_stream, _hold_directory

CODES = frozenset({"invalid_path", "exists", "source_unavailable", "asset_too_large", "cancelled",
                   "source_changed", "io_error", "unsafe_path", "cleanup_failed", "unsupported_platform", "no_space"})


def _bad_component(value):
    return (not value or value in (".", "..") or value.endswith((".", " "))
            or any(char in '<>:"|?*' or ord(char) < 32 or ord(char) == 127 for char in value)
            or value.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]})


class ChatExportStorageError(Exception):
    def __init__(self, code):
        if code not in CODES: raise ValueError("Invalid export storage error")
        self.code = code
        super().__init__(code)


def relative_path(value):
    if (type(value) is not str or not value or len(value) > 512 or "\\" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ChatExportStorageError("invalid_path")
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or path.as_posix() != value or any(_bad_component(part) for part in path.parts):
        raise ChatExportStorageError("invalid_path")
    return Path(*path.parts)


def absolute_path(value):
    try: path = Path(value)
    except (TypeError, ValueError): raise ChatExportStorageError("invalid_path") from None
    if not path.is_absolute() or any(_bad_component(part) for part in path.parts[1:]):
        raise ChatExportStorageError("invalid_path")
    return path


def hold_ancestors(stack, directory):
    for path in (*reversed(directory.parents), directory):
        stack.enter_context(_hold_directory(path))


def _rename_in_parent(source, destination, identity):
    """Atomic non-replacing sibling rename with the parent still pinned.

    MoveFile/os.rename opens the destination parent for writing and conflicts
    with our directory guard. FileRenameInformation accepts a basename and a
    NULL RootDirectory for an in-place rename of the already opened source.
    https://learn.microsoft.com/windows-hardware/drivers/ddi/ntifs/ns-ntifs-_file_rename_information
    """
    import ctypes
    from ctypes import wintypes
    from launch_mod import _windows_api, _validate_handle
    if source.parent != destination.parent: raise ChatExportStorageError('unsafe_path')
    api = _windows_api()
    handle = api.CreateFileW(str(source), 0x10080, 7, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value: raise ctypes.WinError(ctypes.get_last_error())
    try:
        _validate_handle(api,handle,source,directory=True)
        class FileId(ctypes.Structure):
            _fields_ = [('volume',ctypes.c_ulonglong),('identifier',ctypes.c_ubyte*16)]
        fileid = FileId()
        if not api.GetFileInformationByHandleEx(handle,18,ctypes.byref(fileid),ctypes.sizeof(fileid)):
            raise ctypes.WinError(ctypes.get_last_error())
        if (fileid.volume,int.from_bytes(bytes(fileid.identifier),'little')) != identity:
            raise ChatExportStorageError('unsafe_path')
        class RenameInfo(ctypes.Structure):
            _fields_ = [('replace',wintypes.BOOL),('root',wintypes.HANDLE),('length',wintypes.DWORD),('name',wintypes.WCHAR*1)]
        class IOStatus(ctypes.Structure):
            _fields_ = [('status',ctypes.c_void_p),('information',ctypes.c_size_t)]
        encoded = destination.name.encode('utf-16le')
        size = max(ctypes.sizeof(RenameInfo),RenameInfo.name.offset+len(encoded))
        buffer = ctypes.create_string_buffer(size); info = RenameInfo.from_buffer(buffer)
        info.replace = False; info.root = None; info.length = len(encoded)
        ctypes.memmove(ctypes.addressof(buffer)+RenameInfo.name.offset,encoded,len(encoded))
        native = ctypes.WinDLL('ntdll',use_last_error=True)
        native.NtSetInformationFile.argtypes = (wintypes.HANDLE,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.c_int)
        native.NtSetInformationFile.restype = wintypes.LONG
        status = native.NtSetInformationFile(handle,ctypes.byref(IOStatus()),buffer,size,10)
        if status < 0:
            native.RtlNtStatusToDosError.argtypes = (wintypes.LONG,)
            native.RtlNtStatusToDosError.restype = wintypes.ULONG
            raise ctypes.WinError(native.RtlNtStatusToDosError(status))
    finally:
        api.CloseHandle(handle)


@contextmanager
def _append_stream(path):
    """Open an existing owned file exclusively, without following a link."""
    import ctypes
    import msvcrt
    from launch_mod import _windows_api, _validate_handle
    _check_path(path,directory=False)
    api = _windows_api()
    handle = api.CreateFileW(str(path),0xC0000000,0,None,3,0x00200000,None)
    if handle == ctypes.c_void_p(-1).value: raise ctypes.WinError(ctypes.get_last_error())
    try:
        _validate_handle(api,handle,path,directory=False)
        fd = msvcrt.open_osfhandle(handle,os.O_RDWR|os.O_BINARY)
    except BaseException:
        api.CloseHandle(handle); raise
    with os.fdopen(fd,'r+b') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise ValueError('Unsafe local export asset')
        yield stream


class ExportWorkspace:
    """Publish one new folder, or remove only this workspace's staging tree.

    The caller supplies a controlled adapter spool. URL fetching, native chat
    access and credential handling are deliberately outside this module.
    """
    def __init__(self, destination, *, cancel_event=None):
        if os.name != "nt": raise ChatExportStorageError("unsupported_platform")
        self.destination = absolute_path(destination)
        self.cancel_event = cancel_event or threading.Event()
        self._parents, self._directories = ExitStack(), ExitStack()
        self._held = set(); self._published = False; self._closed = False; self._stage = None
        self._identity = None; self._finishing = False
        try:
            self.check_cancelled()
            hold_ancestors(self._parents, self.destination.parent)
            try: self.destination.lstat()
            except FileNotFoundError: pass
            else: raise ChatExportStorageError("exists")
            self._stage = self.destination.parent / (".valmax-export-" + uuid.uuid4().hex + ".tmp")
            self._stage.mkdir(mode=0o700)
            info = self._stage.lstat(); self._identity = info.st_dev, info.st_ino
            self._hold(self._stage)
        except ChatExportStorageError:
            self.abort(); raise
        except (OSError, ValueError):
            self.abort()
            raise ChatExportStorageError("unsafe_path") from None

    @property
    def staging(self): return self._stage

    def check_cancelled(self):
        if self.cancel_event.is_set(): raise ChatExportStorageError("cancelled")

    def _hold(self, path):
        if path not in self._held:
            self._directories.enter_context(_hold_directory(path)); self._held.add(path)

    def _target(self, relative):
        self.check_cancelled()
        if self._closed or self._published or self._finishing or self._stage is None: raise ChatExportStorageError("unsafe_path")
        path = self._stage / relative_path(relative)
        current = self._stage
        for part in path.relative_to(self._stage).parts[:-1]:
            current /= part
            try: current.mkdir()
            except FileExistsError: pass
            self._hold(current)
        return path

    def reserve_asset(self, relative):
        """Return a new controlled path for the native download adapter."""
        if relative_path(relative).parts[0] != 'media' or len(relative_path(relative).parts) != 2:
            raise ChatExportStorageError('unsafe_path')
        path = self._target(relative)
        try: path.lstat()
        except FileNotFoundError: return path
        except OSError: raise ChatExportStorageError("io_error") from None
        raise ChatExportStorageError("exists")

    def discard_asset(self, relative):
        """Remove only a failed reserved file, never follow a link or directory."""
        part = relative_path(relative)
        if part.parts[0] != 'media' or len(part.parts) != 2: raise ChatExportStorageError('unsafe_path')
        path = self._target(relative)
        try:
            _check_path(path,directory=False)
            with _config_stream(path,create=False) as stream:
                before = os.fstat(stream.fileno())
            after = path.lstat()
            if (before.st_dev,before.st_ino) != (after.st_dev,after.st_ino): raise ChatExportStorageError('unsafe_path')
            path.unlink()
        except FileNotFoundError: return
        except ChatExportStorageError: raise
        except (OSError,ValueError): raise ChatExportStorageError('unsafe_path') from None

    def write_asset_chunk(self, relative, offset, data, *, max_bytes=64*1024**2):
        """Append at an exact acknowledged offset in a reserved media slot.

        The coordinator authenticates job/reservation and deduplicates retries.
        Each call is bounded to 48 KiB. It never truncates or replaces a file.
        """
        part = relative_path(relative)
        if part.parts[0] != 'media' or len(part.parts) != 2: raise ChatExportStorageError('unsafe_path')
        if (type(offset) is not int or offset < 0 or type(data) is not bytes or not 0 < len(data) <= 48*1024
                or type(max_bytes) is not int or not 0 < max_bytes <= 64*1024**2 or offset+len(data)>max_bytes):
            raise ChatExportStorageError('asset_too_large')
        path = self._target(relative)
        try:
            if shutil.disk_usage(self.destination.parent).free < len(data)+64*1024**2:
                raise ChatExportStorageError('no_space')
            opener = _config_stream(path,create=True) if offset == 0 else _append_stream(path)
            with opener as stream:
                before = os.fstat(stream.fileno())
                if before.st_size != offset: raise ChatExportStorageError('source_changed')
                self.check_cancelled(); stream.seek(offset); stream.write(data)
                stream.flush(); os.fsync(stream.fileno())
                if os.fstat(stream.fileno()).st_size != offset+len(data): raise ChatExportStorageError('source_changed')
            return offset+len(data)
        except ChatExportStorageError: raise
        except FileExistsError: raise ChatExportStorageError('exists') from None
        except FileNotFoundError: raise ChatExportStorageError('source_unavailable') from None
        except ValueError: raise ChatExportStorageError('unsafe_path') from None
        except OSError: raise ChatExportStorageError('io_error') from None

    def verify_asset(self, relative, *, max_bytes):
        """Read an acknowledged local asset with a non-writing, pinned handle."""
        if type(max_bytes) is not int or not 0 < max_bytes <= 16 * 1024**3:
            raise ChatExportStorageError("asset_too_large")
        path = self._target(relative)
        try:
            _check_path(path, directory=False)
            with _config_stream(path, create=False) as stream:
                before = os.fstat(stream.fileno())
                if before.st_nlink != 1 or not stat.S_ISREG(before.st_mode): raise ChatExportStorageError("unsafe_path")
                if before.st_size > max_bytes: raise ChatExportStorageError("asset_too_large")
                total = 0; digest = hashlib.sha256()
                while True:
                    self.check_cancelled()
                    data = stream.read(min(1024 * 1024, max_bytes - total + 1))
                    if not data: break
                    total += len(data)
                    if total > max_bytes: raise ChatExportStorageError("asset_too_large")
                    digest.update(data)
                after = os.fstat(stream.fileno())
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or total != before.st_size:
                    raise ChatExportStorageError("source_changed")
                return {"bytes": total, "sha256": digest.hexdigest()}
        except ChatExportStorageError: raise
        except FileNotFoundError: raise ChatExportStorageError("source_unavailable") from None
        except ValueError: raise ChatExportStorageError("unsafe_path") from None
        except OSError: raise ChatExportStorageError("io_error") from None

    @contextmanager
    def open_text(self, relative):
        try:
            path = self._target(relative)
            with _config_stream(path, create=True) as stream:
                wrapper = io.TextIOWrapper(stream, encoding="utf8", newline="\n")
                try:
                    yield wrapper
                    wrapper.flush(); os.fsync(stream.fileno())
                finally:
                    wrapper.detach()
        except ChatExportStorageError: raise
        except FileExistsError: raise ChatExportStorageError("exists") from None
        except (OSError, ValueError): raise ChatExportStorageError("io_error") from None

    def copy_asset(self, spool_root, source_relative, dest_relative, *, max_bytes):
        if type(max_bytes) is not int or not 0 < max_bytes <= 16 * 1024**3:
            raise ChatExportStorageError("asset_too_large")
        root = absolute_path(spool_root)
        source = root / relative_path(source_relative)
        if root == self.destination or root.is_relative_to(self.destination) or self.destination.is_relative_to(root):
            raise ChatExportStorageError("unsafe_path")
        target = None; target_identity = None; succeeded = False
        try:
            with ExitStack() as held:
                hold_ancestors(held, source.parent)
                _check_path(source, directory=False)
                with _config_stream(source, create=False) as incoming:
                    before = os.fstat(incoming.fileno())
                    if before.st_nlink != 1 or not stat.S_ISREG(before.st_mode): raise ChatExportStorageError("unsafe_path")
                    if before.st_size > max_bytes: raise ChatExportStorageError("asset_too_large")
                    if shutil.disk_usage(self.destination.parent).free < before.st_size + 64 * 1024**2:
                        raise ChatExportStorageError("no_space")
                    target = self._target(dest_relative)
                    total = 0; digest = hashlib.sha256()
                    with _config_stream(target, create=True) as outgoing:
                        info = os.fstat(outgoing.fileno()); target_identity = info.st_dev, info.st_ino
                        while True:
                            self.check_cancelled()
                            data = incoming.read(min(1024 * 1024, max_bytes - total + 1))
                            if not data: break
                            total += len(data)
                            if total > max_bytes: raise ChatExportStorageError("asset_too_large")
                            outgoing.write(data); digest.update(data)
                        after = os.fstat(incoming.fileno())
                        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or total != before.st_size:
                            raise ChatExportStorageError("source_changed")
                        outgoing.flush(); os.fsync(outgoing.fileno())
                    succeeded = True
                    return {"bytes": total, "sha256": digest.hexdigest()}
        except ChatExportStorageError: raise
        except FileNotFoundError: raise ChatExportStorageError("source_unavailable") from None
        except ValueError: raise ChatExportStorageError("unsafe_path") from None
        except OSError: raise ChatExportStorageError("io_error") from None
        finally:
            if not succeeded and target is not None and target_identity is not None:
                # A successful return preserves the asset; exceptions remove
                # only the file created by this copy, never a prior file.
                try:
                    _check_path(target, directory=False); info = target.lstat()
                    if (info.st_dev, info.st_ino) == target_identity: target.unlink()
                except (OSError, ValueError): pass

    def publish(self):
        self.check_cancelled()
        if self._closed or self._published or self._finishing: raise ChatExportStorageError("unsafe_path")
        self._finishing = True
        self._directories.close()
        try:
            _check_path(self._stage, directory=True)
            info = self._stage.lstat()
            if (info.st_dev, info.st_ino) != self._identity: raise ChatExportStorageError("unsafe_path")
            # Windows rename fails if ANY destination already exists, including
            # an empty directory. It does not replace a competing export.
            _rename_in_parent(self._stage, self.destination,self._identity)
        except FileExistsError: raise ChatExportStorageError("exists") from None
        except (OSError, ValueError): raise ChatExportStorageError("io_error") from None
        self._published = True; self._closed = True; self._parents.close()
        return self.destination

    def abort(self):
        if self._published or self._closed: return
        self._directories.close()
        try:
            if self._stage is not None and self._identity is not None:
                expected = self.destination.parent / self._stage.name
                if (self._stage.resolve(strict=True) != expected or not self._stage.resolve().is_relative_to(self.destination.parent.resolve())):
                    raise ChatExportStorageError("cleanup_failed")
                _check_path(self._stage, directory=True); info = self._stage.lstat()
                if (info.st_dev, info.st_ino) != self._identity: raise ChatExportStorageError("cleanup_failed")
                for directory, dirs, files in os.walk(self._stage, followlinks=False):
                    for name in dirs: _check_path(Path(directory) / name, directory=True)
                    for name in files: _check_path(Path(directory) / name, directory=False)
                shutil.rmtree(self._stage)
        except (OSError, ValueError): raise ChatExportStorageError("cleanup_failed") from None
        finally:
            self._closed = True; self._parents.close()

    def __enter__(self): return self
    def __exit__(self, *_): self.abort()
