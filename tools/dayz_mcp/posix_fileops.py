"""POSIX counterparts of the Win32 file primitives the runs.json migration relies on.

``identity_migration`` publishes its transaction marker and receipt with a rename
that must not overwrite, and removes the marker only after proving, through a held
handle, that the file it is about to delete is the one it wrote. Win32 gives both
directly: ``MoveFileEx`` without ``REPLACE_EXISTING`` fails on an existing target,
and a handle opened with ``FILE_SHARE_READ`` only blocks renames and writes until
``FILE_DISPOSITION_INFO`` deletes the file on close. POSIX has neither a shared-mode
open nor a delete through a descriptor, so the guarantees are rebuilt differently:

- ``rename_noreplace``: ``renameat2(RENAME_NOREPLACE)``; where the filesystem refuses
  the flag, ``link()`` + ``unlink()``, which also fails with ``EEXIST`` and never
  replaces a target.
- ``unlink_owned``: open the leaf with ``O_NOFOLLOW`` relative to a held directory
  descriptor, check it is a regular single-link file with the expected bytes, then
  move that name to a private quarantine name with ``rename_noreplace`` and compare
  the quarantined inode with the descriptor's. Only an inode proven to be the one
  that was read is unlinked. A swap anywhere in between cannot be blocked as on
  Windows, but it is detected: the result is a conflict and nothing foreign is
  deleted (a foreign file caught in quarantine is moved back).

Callers hold the migration's exclusive byte lock around these calls, so the only
writers they race with are processes that ignore that lock.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import secrets
import stat
from pathlib import Path
from typing import Callable

_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_MAX_OWNED_BYTES = 16 * 1024 * 1024

_renameat2 = None
if os.name != "nt":
    try:
        _libc = ctypes.CDLL(ctypes.util.find_library("c") or None, use_errno=True)
        _renameat2 = _libc.renameat2
        _renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        _renameat2.restype = ctypes.c_int
    except (AttributeError, OSError):  # pragma: no cover - glibc < 2.28, musl, BSD
        _renameat2 = None


class OwnedUnlinkConflict(Exception):
    """The file at the path is not, or is no longer, the one the caller owns."""


class OwnedUnlinkFailed(Exception):
    """The owned file could not be removed for an I/O reason."""


def rename_noreplace(
    source: os.PathLike[str] | str,
    target: os.PathLike[str] | str,
    *,
    dir_fd: int | None = None,
) -> None:
    """Rename ``source`` to ``target``; ``FileExistsError`` if ``target`` exists.

    With ``dir_fd`` both names are relative to that directory descriptor.
    """
    src = os.fsencode(source)
    dst = os.fsencode(target)
    base = _AT_FDCWD if dir_fd is None else dir_fd
    if _renameat2 is not None:
        if _renameat2(base, src, base, dst, _RENAME_NOREPLACE) == 0:
            return
        code = ctypes.get_errno()
        if code not in {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP}:
            raise OSError(code, os.strerror(code), os.fsdecode(source), None, os.fsdecode(target))
    # No RENAME_NOREPLACE here: link() never replaces an existing name either.
    os.link(src, dst, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
    try:
        os.unlink(src, dir_fd=dir_fd)
    except OSError:
        try:
            os.unlink(dst, dir_fd=dir_fd)
        except OSError:
            pass
        raise


def _read_all(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    offset = 0
    while True:
        chunk = os.pread(descriptor, 64 * 1024, offset)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        offset += len(chunk)
        if offset > _MAX_OWNED_BYTES:
            raise OwnedUnlinkConflict("owned_file_too_large")


def _identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def unlink_owned(
    path: Path,
    matches: Callable[[bytes], bool],
    *,
    fault_injector: Callable[[str], None] | None = None,
    phase_name: str,
) -> None:
    """Delete ``path`` only if it is a regular single-link file whose bytes ``matches``.

    Returns quietly if the path is already gone when it is opened. Raises
    ``OwnedUnlinkConflict`` when the file is not the owned one or changes during the
    call, ``OwnedUnlinkFailed`` on an I/O error. Fault-injection phase names are the
    ones the Win32 implementation reports: ``<phase>_unlink_pinned`` runs after the
    file has been opened and verified, before anything is removed.
    """
    path = Path(os.path.abspath(path))
    name = path.name
    try:
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError as error:
        raise OwnedUnlinkFailed("owned_dir_unavailable") from error
    try:
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NOCTTY | os.O_NONBLOCK,
                dir_fd=directory,
            )
        except FileNotFoundError:
            return
        except OSError as error:
            # ELOOP: the name is a symbolic link, which the owner never writes.
            raise OwnedUnlinkConflict("owned_open_refused") from error
        try:
            pinned = os.fstat(descriptor)
            if not stat.S_ISREG(pinned.st_mode) or pinned.st_nlink != 1:
                raise OwnedUnlinkConflict("owned_not_single_regular_file")
            actual = _read_all(descriptor)
            if not matches(actual):
                raise OwnedUnlinkConflict("owned_bytes_mismatch")
            if fault_injector is not None:
                fault_injector(f"{phase_name}_unlink_pinned")
            if _identity(os.fstat(descriptor)) != _identity(pinned) or _read_all(descriptor) != actual:
                raise OwnedUnlinkConflict("owned_changed_while_pinned")

            quarantine = f".{name}.unlink-{secrets.token_hex(8)}"
            try:
                rename_noreplace(name, quarantine, dir_fd=directory)
            except FileNotFoundError as error:
                raise OwnedUnlinkConflict("owned_name_moved") from error
            except OSError as error:
                raise OwnedUnlinkFailed("owned_quarantine_failed") from error
            try:
                caught = os.stat(quarantine, dir_fd=directory, follow_symlinks=False)
            except OSError as error:
                raise OwnedUnlinkFailed("owned_quarantine_unreadable") from error
            current = os.fstat(descriptor)
            if (
                _identity(caught) != _identity(pinned)
                or _identity(current) != _identity(pinned)
                or current.st_nlink != 1
                or _read_all(descriptor) != actual
            ):
                # Someone else's file was caught (or ours gained a link): put it back.
                try:
                    rename_noreplace(quarantine, name, dir_fd=directory)
                except OSError:
                    pass
                raise OwnedUnlinkConflict("owned_swapped_before_unlink")
            try:
                os.unlink(quarantine, dir_fd=directory)
                os.fsync(directory)
            except OSError as error:
                raise OwnedUnlinkFailed("owned_unlink_failed") from error
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)
