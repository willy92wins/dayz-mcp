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
- ``native_process_guard``, ``accredited_daemon_transport`` and
  ``native_process_snapshot.working_directory_of``: process images and working
  directories are absolute POSIX paths, not drive paths;
- ``server_cli.open_lock_file``: ``byte_lock.open_lock_file``;
- ``daemon_policy_contract.valid_absolute_path``: also ``valid_posix_absolute_path``
  (the Win32 rule demands a drive letter, so no Linux path was ever valid);
- ``dayz_test_request``, ``dayz_test_worker``, ``dayz_test_attestation`` and
  ``dayz_test_storage`` (``install_sealed_worker_paths``): ``DRIVE_PATH_AWARE`` for
  their ``ntpath`` (``posixpath`` on POSIX paths, ``ntpath`` on drive paths), and
  drive-letter validators that also accept a POSIX path;
- ``dayz_mcp.native_launcher_backend`` (``install_linux_launcher_backend``, called by
  ``linux_launcher.load_verified_bundle``, not by ``install()``): a stand-in
  whose ``launch_registered_native`` is ``linux_launcher.launch_registered_linux``.

On Windows ``install()`` does nothing and importing this module has no side effect.
Unsealed modules import ``win_dll`` and ``is_python_image`` from here directly.
"""

from __future__ import annotations

import ctypes
import ntpath
import os
import posixpath
import stat as _stat
import sys
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


def valid_posix_absolute_path(value: object) -> bool:
    """The drive-letter rule's POSIX counterpart: an absolute, normalized path.

    Same text limits as ``daemon_policy_contract.valid_text`` (1..520 characters,
    NFC, no NUL, no lone surrogates); ``//`` at the start is refused, like any other
    path that normalization would change.
    """
    from dayz_mcp.daemon_policy_contract import valid_text

    return (
        valid_text(value)
        and value.startswith("/")
        and not value.startswith("//")
        and posixpath.normpath(value) == value
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
    """ntpath for sealed modules, except that a POSIX path is absolute.

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
    native_process_snapshot.ntpath = _NtpathWithPosixIsabs()

    from dayz_mcp import pinned_keyfile

    pinned_keyfile.read_pinned_keyfile = read_pinned_keyfile_posix

    from dayz_mcp import native_process_guard

    native_process_guard.ntpath = _NtpathWithPosixIsabs()

    from dayz_mcp import accredited_daemon_transport

    accredited_daemon_transport.ntpath = _NtpathWithPosixIsabs()

    from dayz_mcp import byte_lock, server_cli

    server_cli.open_lock_file = byte_lock.open_lock_file

    from dayz_mcp import daemon_policy_contract

    drive_rule = daemon_policy_contract.valid_absolute_path
    if not getattr(drive_rule, "_accepts_posix", False):
        # A POSIX path, or what the drive-letter rule accepted before: a drive
        # path never matches a Linux process image, so it fails closed later.
        def absolute_rule(value: object) -> bool:
            return valid_posix_absolute_path(value) or drive_rule(value)

        absolute_rule._accepts_posix = True
        daemon_policy_contract.valid_absolute_path = absolute_rule

    install_sealed_worker_paths()


def install_linux_launcher_backend() -> None:
    """``dayz_mcp.native_launcher_backend`` off Windows: the Linux relay.

    The real module drives the signed PE through Win32 and cannot even be
    imported here. ``secure_launcher`` is byte-pinned by ``dependency-lock.json``
    (editing it costs a launcher rebuild), and it imports the backend by name
    when a lease is granted, so a module of that name with
    ``linux_launcher.launch_registered_linux`` as ``launch_registered_native``
    is where the Linux run takes over. ``security_runtime_audit`` audits
    ``linux_launcher`` explicitly for that reason. Installed by
    ``linux_launcher.load_verified_bundle``, i.e. only in a process that is about
    to launch, and with ``linux_launcher.py`` as its file, so the server-code
    freshness check can read its source.
    """
    if os.name == "nt" or "dayz_mcp.native_launcher_backend" in sys.modules:
        return
    import types

    from dayz_mcp import linux_launcher

    backend = types.ModuleType(
        "dayz_mcp.native_launcher_backend",
        "Off Windows: linux_launcher's relay under the native backend's name.",
    )
    backend.__file__ = linux_launcher.__file__
    backend.launch_registered_native = linux_launcher.launch_registered_linux
    backend.NativeLauncherBackendError = linux_launcher.LinuxLauncherError
    sys.modules[backend.__name__] = backend


# install_sealed_worker_paths covers the sealed modules a dayz_test_run request
# passes through: the tool's request projection, the worker that turns it into
# lifecycle starts, the attestation of the mod it names and the mission storage
# rotation the daemon does before a spawn.


def _windows_shaped(value: object) -> bool:
    """A drive path (``C:...``) or a UNC/device path (``\\\\...``): Windows semantics."""
    if isinstance(value, (list, tuple)):
        return any(_windows_shaped(item) for item in value)
    if isinstance(value, os.PathLike):
        value = os.fspath(value)
    return isinstance(value, str) and (
        (len(value) >= 2 and value[1] == ":" and value[0].isascii() and value[0].isalpha())
        or value.startswith("\\\\")
    )


class DrivePathAwarePath:
    """``ntpath`` for Windows-shaped paths, ``posixpath`` for everything else.

    Policy paths are drive paths on Windows and POSIX paths on Linux, and the
    same code handles both. Each call is dispatched on its first argument: a
    drive or UNC path keeps exactly the ``ntpath`` behaviour it always had, so a
    Windows-shaped value is judged the same way on every host; anything else
    (``/home/...``, a bare name) gets ``posixpath``. Only the functions those
    modules call exist here; any other name is an AttributeError.
    """

    @staticmethod
    def basename(path):
        return ntpath.basename(path) if _windows_shaped(path) else posixpath.basename(path)

    @staticmethod
    def dirname(path):
        return ntpath.dirname(path) if _windows_shaped(path) else posixpath.dirname(path)

    @staticmethod
    def isabs(path):
        return ntpath.isabs(path) if _windows_shaped(path) else posixpath.isabs(path)

    @staticmethod
    def isjunction(path):
        return ntpath.isjunction(path) if _windows_shaped(path) else posixpath.isjunction(path)

    @staticmethod
    def join(path, *paths):
        return ntpath.join(path, *paths) if _windows_shaped(path) else posixpath.join(path, *paths)

    @staticmethod
    def normcase(path):
        return ntpath.normcase(path) if _windows_shaped(path) else posixpath.normcase(path)

    @staticmethod
    def normpath(path):
        return ntpath.normpath(path) if _windows_shaped(path) else posixpath.normpath(path)

    @staticmethod
    def splitdrive(path):
        return ntpath.splitdrive(path) if _windows_shaped(path) else posixpath.splitdrive(path)

    @staticmethod
    def splitext(path):
        return ntpath.splitext(path) if _windows_shaped(path) else posixpath.splitext(path)

    @staticmethod
    def relpath(path, start=None):
        if _windows_shaped(path):
            return ntpath.relpath(path, start)
        return posixpath.relpath(path, start)

    @staticmethod
    def commonpath(paths):
        return ntpath.commonpath(paths) if _windows_shaped(paths) else posixpath.commonpath(paths)


DRIVE_PATH_AWARE = DrivePathAwarePath()


def valid_posix_local_path(value: object) -> bool:
    """The POSIX counterpart of the sealed drive-letter rule.

    Same bounds as theirs (3..520 characters, valid NFC text) and an absolute,
    normalized POSIX path.
    """
    return isinstance(value, str) and 3 <= len(value) <= 520 and valid_posix_absolute_path(value)


def install_sealed_worker_paths() -> None:
    """Give the sealed request/worker modules POSIX path semantics for POSIX paths.

    They join, normalize and compare policy paths with ``ntpath`` and accept
    only drive-letter paths. Outside Windows the policy paths are POSIX paths, so
    their module-level ``ntpath`` becomes ``DRIVE_PATH_AWARE`` (``posixpath`` for a
    POSIX path, unchanged ``ntpath`` for a drive path; ``isjunction`` is False for
    POSIX paths) and the two drive-letter validators accept a valid POSIX path as
    well as what they accepted before. Called by ``install()`` and by the Linux
    worker host before the sealed worker runs.
    """
    if os.name == "nt":
        return
    from dayz_mcp import (
        dayz_test_attestation,
        dayz_test_request,
        dayz_test_storage,
        dayz_test_worker,
    )

    for module in (dayz_test_request, dayz_test_worker, dayz_test_attestation, dayz_test_storage):
        module.ntpath = DRIVE_PATH_AWARE
    drive_request = dayz_test_request._valid_local_absolute_path
    drive_worker = dayz_test_worker._local_path
    if not getattr(drive_request, "_accepts_posix", False):
        def request_rule(value: object) -> bool:
            return valid_posix_local_path(value) or drive_request(value)

        request_rule._accepts_posix = True
        dayz_test_request._valid_local_absolute_path = request_rule
    if not getattr(drive_worker, "_accepts_posix", False):
        def worker_rule(value: object) -> bool:
            return valid_posix_local_path(value) or drive_worker(value)

        worker_rule._accepts_posix = True
        dayz_test_worker._local_path = worker_rule
