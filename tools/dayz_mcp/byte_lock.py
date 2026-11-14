"""One-byte, non-blocking, exclusive locks with Windows semantics on every host.

Windows: ``msvcrt.locking(LK_NBLCK)`` at the offset, exactly what the callers did
before this module existed.

Linux: open-file-description locks (``F_OFD_SETLK``). Classic POSIX record locks
(``lockf``/``F_SETLK``) belong to the process and vanish when the process closes
*any* descriptor of the file, and several callers close a second descriptor of a
lock file while the first one holds a byte. OFD locks belong to the open file, like
Windows byte-range locks: closing another descriptor does not release them, and two
descriptors in one process do conflict. Other POSIX systems fall back to
``F_SETLK`` with the process-wide caveat above.
"""

from __future__ import annotations

import ctypes
import os
import sys

if os.name == "nt":
    import msvcrt
else:
    import fcntl

    class _Flock(ctypes.Structure):
        # struct flock on 64-bit Linux: short, short, off_t, off_t, pid_t.
        _fields_ = [
            ("l_type", ctypes.c_short),
            ("l_whence", ctypes.c_short),
            ("l_start", ctypes.c_int64),
            ("l_len", ctypes.c_int64),
            ("l_pid", ctypes.c_int32),
        ]

    # Python 3.11's fcntl does not name F_OFD_SETLK; the kernel's value on Linux is 37.
    _SETLK = getattr(
        fcntl, "F_OFD_SETLK", 37 if sys.platform.startswith("linux") else fcntl.F_SETLK
    )

    def _fcntl_lock(descriptor: int, offset: int, kind: int) -> None:
        request = _Flock(kind, os.SEEK_SET, offset, 1, 0)
        fcntl.fcntl(descriptor, _SETLK, bytes(request))


def try_lock(descriptor: int, offset: int = 0) -> None:
    """Lock byte ``offset``. Raises OSError when someone else holds it."""
    if os.name == "nt":
        os.lseek(descriptor, offset, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
    else:
        _fcntl_lock(descriptor, offset, fcntl.F_WRLCK)


def lock(descriptor: int, offset: int = 0) -> None:
    """Lock byte ``offset``, waiting like ``msvcrt.locking(LK_LOCK)``.

    LK_LOCK tries once a second, ten times, then raises OSError. Elsewhere the
    same: ten non-blocking attempts a second apart, never an unbounded wait.
    """
    if os.name == "nt":
        os.lseek(descriptor, offset, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        return
    import time

    for attempt in range(10):
        try:
            _fcntl_lock(descriptor, offset, fcntl.F_WRLCK)
            return
        except OSError:
            if attempt == 9:
                raise
            time.sleep(1.0)


def unlock(descriptor: int, offset: int = 0) -> None:
    """Release byte ``offset``. Raises OSError like ``msvcrt.locking`` does."""
    if os.name == "nt":
        os.lseek(descriptor, offset, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        _fcntl_lock(descriptor, offset, fcntl.F_UNLCK)


def open_lock_file(path: os.PathLike[str] | str) -> int:
    """POSIX counterpart of ``server_cli.open_lock_file``: create or open, never via a symlink."""
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
    return os.open(path, flags, 0o600)
