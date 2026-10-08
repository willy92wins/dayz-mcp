"""Shared physical-box admission for supported instances.

Instance-local session leases stay independent. This lock only serializes
destructive preparation and spawn across daemons. Process death releases the
OS lock. The same thread may re-enter (the Steam path calls start again).
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


_thread_depth = threading.local()
_process_lock = threading.Lock()
_descriptor: int | None = None
_holders = 0


def _lock_path() -> Path:
    from dayz_mcp.server_cli import shared_root

    root = shared_root()
    root.mkdir(parents=True, exist_ok=True)
    return root / "box-admission.lock"


def _acquire_os() -> bool:
    global _descriptor, _holders
    with _process_lock:
        # This process already admitted work. Another thread shares that
        # admission; a second process still blocks on the OS lock.
        if _holders > 0:
            _holders += 1
            return True
        try:
            import msvcrt
        except ImportError:
            return False
        path = _lock_path()
        from dayz_mcp.server_cli import open_lock_file

        descriptor = open_lock_file(path)
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            os.close(descriptor)
            return False
        _descriptor = descriptor
        _holders = 1
        return True


def _release_os() -> None:
    global _descriptor, _holders
    with _process_lock:
        if _holders <= 0:
            return
        _holders -= 1
        if _holders > 0:
            return
        descriptor = _descriptor
        _descriptor = None
        if descriptor is None:
            return
        try:
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            os.close(descriptor)


@contextmanager
def box_admission() -> Iterator[bool]:
    """Yield True when this thread holds the cross-instance admission lock."""
    depth = getattr(_thread_depth, "n", 0)
    if depth > 0:
        _thread_depth.n = depth + 1
        try:
            yield True
        finally:
            _thread_depth.n = depth
        return
    if not _acquire_os():
        yield False
        return
    _thread_depth.n = 1
    try:
        yield True
    finally:
        _thread_depth.n = 0
        _release_os()
