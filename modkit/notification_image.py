"""One transparent program-owned PNG for notifications without profile photos.

The native Windows toast template expects a valid local image. This constant
contains no user data and avoids passing an empty/invalid image URI to WinRT.
"""
from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import stat
import struct
import zlib

from launch_mod import _hold_directory, _config_stream, _check_path


def _chunk(name, data):
    return struct.pack('>I', len(data)) + name + data + struct.pack('>I', zlib.crc32(name + data))


PNG = (b'\x89PNG\r\n\x1a\n'
       + _chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
       + _chunk(b'IDAT', zlib.compress(b'\0\0\0\0\0', 9))
       + _chunk(b'IEND', b''))
SHA256 = hashlib.sha256(PNG).hexdigest()
NAME = 'valmax-private-notification-' + SHA256[:16] + '.png'


def prepare(directory):
    """Create only this fixed asset in the caller's verified helper directory."""
    directory = Path(directory)
    if (not directory.is_absolute() or directory.resolve(strict=True) != directory
            or str(directory).startswith(('\\\\', '//'))):
        raise ValueError('Unsafe notification image directory')
    with ExitStack() as held:
        for ancestor in (*reversed(directory.parents), directory):
            held.enter_context(_hold_directory(ancestor))
        target = directory / NAME
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
        except FileExistsError:
            with _config_stream(target, create=False) as stream:
                if stream.read(len(PNG) + 1) != PNG:
                    raise ValueError('Notification image does not match its pinned content')
        else:
            with os.fdopen(descriptor, 'wb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('Unsafe notification image file')
                stream.write(PNG)
                stream.flush()
                os.fsync(stream.fileno())
            _check_path(target, directory=False)
            with _config_stream(target, create=False) as stream:
                if stream.read(len(PNG) + 1) != PNG:
                    raise ValueError('Notification image changed after creation')
    return target
