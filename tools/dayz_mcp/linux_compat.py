"""Linux support kept outside the sealed native launcher.

The modules embedded in the dayz-test-v1 native launcher (``PACKAGED_MODULES`` in
``build_native_launcher.py``, hashed in ``tools/packaged-modules.lock.json``) stay
byte-identical, so a Linux change never forces a launcher rebuild. Instead
``install()`` runs first in ``python -m dayz_mcp`` and, outside Windows only, puts
Linux implementations in place before any other module binds them:

- ``LOCALAPPDATA`` defaults to ``$XDG_STATE_HOME`` (``~/.local/state``);
- ``win32_fileinfo.bind_common_kernel32`` returns an ``UnavailableLibrary``, so the
  kernel32 prototypes bound at import succeed and any call raises ``OSError``;
- ``stat.IO_REPARSE_TAG_MOUNT_POINT`` / ``_SYMLINK`` exist (Win32 values, only read by
  Windows-only checks);
- ``pinned_keyfile.read_pinned_keyfile``: a POSIX reader with the Win32 reader's refusals;
- ``native_process_snapshot.full_image_path_of``: ``/proc/<pid>/exe``;
- ``native_process_guard``: process images are absolute POSIX paths, not drive paths;
- ``server_cli.open_lock_file``: ``byte_lock.open_lock_file``.

On Windows ``install()`` does nothing and importing this module has no side effect.
Unsealed modules import ``win_dll`` and ``is_python_image`` from here directly.
"""

from __future__ import annotations

import ctypes
import ntpath
import os
import stat as _stat
from pathlib import Path

_MAX_RAW_BYTES = 4096
_MAX_KEY_CHARS = 1024
_installed = False


class _UnavailableFunction:
    """A Win32 prototype outside Windows: prototypes can be assigned, calls raise."""

    def __init__(self, name: str) -> None:
        self.__name__ = name
        self.argtypes = None
        self.restype = None
        self.errcheck = None

    def __call__(self, *args, **kwargs):
        raise OSError(f"win32_unavailable:{self.__name__}")


class UnavailableLibrary:
    """Stands in for ``ctypes.WinDLL`` outside Windows."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._functions: dict[str, _UnavailableFunction] = {}

    def __getattr__(self, name: str) -> _UnavailableFunction:
        if name.startswith("__"):
            raise AttributeError(name)
        function = self._functions.get(name)
        if function is None:
            function = self._functions[name] = _UnavailableFunction(f"{self._name}.{name}")
        return function


def win_dll(name: str):
    """``ctypes.WinDLL(name, use_last_error=True)`` on Windows, ``UnavailableLibrary`` elsewhere."""
    if os.name == "nt":
        return ctypes.WinDLL(name, use_last_error=True)
    return UnavailableLibrary(name)


def is_python_image(value: object) -> bool:
    """True for a Python interpreter image path or name.

    Windows: python*.exe (the rule the callers used before). Elsewhere the
    interpreter has no extension: python, python3, python3.14.
    """
    if not isinstance(value, str) or not value:
        return False
    if os.name == "nt":
        basename = ntpath.basename(value).casefold()
        return basename.startswith("python") and basename.endswith(".exe")
    basename = os.path.basename(value)
    return basename.startswith("python") and all(
        character.isdigit() or character == "." for character in basename[6:]
    )


def _decode_key(raw: bytes) -> str:
    # Same rules as the tail of pinned_keyfile.read_pinned_keyfile.
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("invalid_daemon_keyfile")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("invalid_daemon_keyfile") from None
    key = text.strip()
    if not key or len(key) > _MAX_KEY_CHARS or "\0" in key or "\r" in key or "\n" in key:
        raise ValueError("invalid_daemon_keyfile")
    return key


def read_pinned_keyfile_posix(path: object) -> str:
    """The Win32 reader's refusals on POSIX; every failure is invalid_daemon_keyfile.

    An absolute, normalized path; no symbolic link in it, the leaf included
    (O_NOFOLLOW, lstat on every parent); a regular file with one link, owned by this
    user, not writable by group or others, at most 4096 bytes.
    """
    if (
        not isinstance(path, str)
        or not path
        or "\0" in path
        or not os.path.isabs(path)
        or os.path.normpath(path) != path
    ):
        raise ValueError("invalid_daemon_keyfile")
    try:
        for parent in reversed(Path(path).parents):
            if _stat.S_ISLNK(os.lstat(parent).st_mode):
                raise ValueError("invalid_daemon_keyfile")
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NOCTTY | os.O_NONBLOCK
        descriptor = os.open(path, flags)
    except OSError:
        raise ValueError("invalid_daemon_keyfile") from None
    chunks: list[bytes] = []
    try:
        info = os.fstat(descriptor)
        if (
            not _stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o022
            or info.st_size > _MAX_RAW_BYTES
        ):
            raise ValueError("invalid_daemon_keyfile")
        remaining = _MAX_RAW_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError:
        raise ValueError("invalid_daemon_keyfile") from None
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    if not 1 <= len(raw) <= _MAX_RAW_BYTES:
        raise ValueError("invalid_daemon_keyfile")
    return _decode_key(raw)


def full_image_path_of_posix(pid: int) -> str | None:
    """The process image from the kernel's link; psutil on other POSIX systems."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        path: str | None = os.readlink(f"/proc/{pid}/exe")
    except OSError:
        path = None
        try:
            import psutil

            path = psutil.Process(pid).exe() or None
        except Exception:
            path = None
    if not path or path.endswith(" (deleted)") or not os.path.isabs(path):
        return None
    return path


class _NtpathWithPosixIsabs:
    """ntpath for native_process_guard, except that a POSIX image path is absolute.

    The identity hash keeps ntpath.normpath, so its value is computed the same way
    on every host.
    """

    isabs = staticmethod(os.path.isabs)

    def __getattr__(self, name: str):
        return getattr(ntpath, name)


def install() -> None:
    """Put the Linux implementations in place. A no-op on Windows; idempotent."""
    global _installed
    if os.name == "nt" or _installed:
        return
    _installed = True

    if not os.environ.get("LOCALAPPDATA", "").strip():
        os.environ["LOCALAPPDATA"] = os.environ.get("XDG_STATE_HOME", "").strip() or os.path.join(
            os.path.expanduser("~"), ".local", "state"
        )
    if not hasattr(_stat, "IO_REPARSE_TAG_MOUNT_POINT"):
        _stat.IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
    if not hasattr(_stat, "IO_REPARSE_TAG_SYMLINK"):
        _stat.IO_REPARSE_TAG_SYMLINK = 0xA000000C

    # Order matters: later modules bind these names at import ("from x import y").
    from dayz_mcp import win32_fileinfo

    win32_fileinfo.bind_common_kernel32 = lambda: UnavailableLibrary("kernel32")

    from dayz_mcp import native_process_snapshot

    native_process_snapshot.full_image_path_of = full_image_path_of_posix

    from dayz_mcp import pinned_keyfile

    pinned_keyfile.read_pinned_keyfile = read_pinned_keyfile_posix

    from dayz_mcp import native_process_guard

    native_process_guard.ntpath = _NtpathWithPosixIsabs()

    from dayz_mcp import byte_lock, server_cli

    server_cli.open_lock_file = byte_lock.open_lock_file
