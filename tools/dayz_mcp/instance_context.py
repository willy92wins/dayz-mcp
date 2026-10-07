"""State-root writer lease and positive evidence of the root-selection contract.

The selector itself lives in ``server_cli`` so packaged modules can import it
without pulling in an unpackaged helper. Names are re-exported here for the
rest of the process.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from dayz_mcp.server_cli import (
    InstanceSelectionError,
    bind_instance_context,
    current_game_path,
    current_instance_token,
    registration_name,
    reject_conflicting_environment,
    reset_instance_context_for_tests,
    state_root_name,
    open_lock_file,
    validate_game_path,
    validate_instance_token,
)

ROOT_SELECTION_CONTRACT_ID = "97de-v1"
_CONTRACT_LINE = 'ROOT_SELECTION_CONTRACT_ID = "97de-v1"'

__all__ = [
    "GLOBAL_SKILLS_OWNER",
    "InstanceSelectionError",
    "ROOT_SELECTION_CONTRACT_ID",
    "RootWriterLease",
    "accredited_package_dir",
    "bind_instance_context",
    "contract_marker_present",
    "current_game_path",
    "current_instance_token",
    "registration_name",
    "reject_conflicting_environment",
    "reset_instance_context_for_tests",
    "state_root_name",
    "validate_game_path",
    "validate_instance_token",
]


def contract_marker_present(package_dir: Path) -> bool:
    path = Path(package_dir) / "instance_context.py"
    try:
        if not path.is_file():
            return False
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return _CONTRACT_LINE in text.splitlines()


def _resolved_package_dir(
    executable: str | None,
    script: str | None,
    cwd: str | None,
    *,
    module_import_established: bool = True,
) -> Path | None:
    """One package: the script that was named, else ``-m`` import order.

    An explicit script is the target even when it has no contract marker.
    ``python -m`` resolves ``dayz_mcp`` from the process cwd first, then a
    venv ``Scripts`` layout, only when the interpreter flags establish that
    lookup. ``-I`` and ``-P`` leave cwd off ``sys.path``, and any flag this
    process did not recognize does too: the package is then not established.
    A ``PYTHONSAFEPATH`` value set in the other process is the same cwd
    exclusion and cannot be read portably from here, so a writer that depends
    on it without passing ``-P`` can still be matched to the cwd package.
    A later absolute argument is not a candidate.
    """
    if not isinstance(script, str) or not script:
        if not module_import_established:
            return None
    if isinstance(script, str) and script:
        script_path = Path(script)
        if not script_path.is_absolute() and isinstance(cwd, str) and cwd:
            script_path = Path(cwd) / script_path
        if script_path.suffix.casefold() == ".py":
            if (
                script_path.name.casefold() == "__main__.py"
                and script_path.parent.name.casefold() == "dayz_mcp"
            ):
                return script_path.parent
            return None
        return None
    candidates: list[Path] = []
    if isinstance(cwd, str) and cwd:
        candidates.append(Path(cwd) / "dayz_mcp")
    if isinstance(executable, str) and executable:
        exe = Path(executable)
        if exe.parent.name.casefold() == "scripts":
            try:
                candidate = exe.parents[2] / "dayz_mcp"
            except IndexError:
                candidate = None
            if candidate is not None and candidate not in candidates:
                candidates.append(candidate)
    for candidate in candidates:
        if (candidate / "__main__.py").is_file() or (candidate / "__init__.py").is_file():
            return candidate
    return candidates[0] if candidates else None


def accredited_package_dir(
    executable: str | None,
    script: str | None,
    cwd: str | None,
    *,
    module_import_established: bool = True,
) -> Path | None:
    """The package next to the python target must carry the contract marker.

    A token on the command line is not evidence. An unestablished ``-m``
    lookup is not evidence either.
    """
    package = _resolved_package_dir(
        executable,
        script,
        cwd,
        module_import_established=module_import_established,
    )
    if package is None or not contract_marker_present(package):
        return None
    return package


class _OwnerToken:
    """Identity of one holder. The lease keeps this object alive.

    A bare ``id()`` is reused after the owner is collected, so a later object
    can be mistaken for the holder that still owns the root.
    """


def _open_shared_lock(path: Path) -> int:
    try:
        import msvcrt
    except ImportError:
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        return os.open(path, flags, 0o600)
    del msvcrt
    try:
        return open_lock_file(path)
    except OSError as error:
        raise OSError("root_writer_lock_open_failed") from error


_lease_guard = threading.Lock()
_lease_holders: dict[str, int] = {}
_lease_fds: dict[str, int] = {}
_lease_owners: dict[str, object] = {}


class RootWriterLease:
    """One writer for a state root, held until this process releases it.

    Process death releases the OS byte-range lock. A second process fails
    before it activates stores, whatever its port or keyfile. A second lease
    in this process is admitted only for the same owner object.
    """

    def __init__(self, root: Path, owner: object | None = None) -> None:
        self.root = Path(root)
        self._key = ""
        self._held = False
        self._owner = owner if owner is not None else _OwnerToken()

    def try_acquire(self) -> bool:
        if self._held:
            return True
        key = os.path.normcase(str(self.root))
        with _lease_guard:
            if key in _lease_fds:
                if _lease_owners.get(key) is not self._owner:
                    return False
                _lease_holders[key] = _lease_holders.get(key, 0) + 1
                self._key = key
                self._held = True
                return True
        try:
            import msvcrt
        except ImportError:
            return False
        self.root.mkdir(parents=True, exist_ok=True)
        # Byte 1 of the startup lock. Byte 0 stays the startup election.
        # A second file would show up beside `.daemon-startup.lock`.
        # The open refuses FILE_SHARE_DELETE, so the held file cannot be
        # replaced out from under the locker. Release the lease before
        # removing a temporary root.
        lock_path = self.root / ".daemon-startup.lock"
        descriptor = _open_shared_lock(lock_path)
        try:
            if os.fstat(descriptor).st_size < 2:
                os.lseek(descriptor, 0, os.SEEK_SET)
                os.write(descriptor, b"\0\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 1, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            os.close(descriptor)
            return False
        with _lease_guard:
            if key in _lease_fds:
                os.lseek(descriptor, 1, os.SEEK_SET)
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
                os.close(descriptor)
                if _lease_owners.get(key) is not self._owner:
                    return False
                _lease_holders[key] = _lease_holders.get(key, 0) + 1
            else:
                _lease_fds[key] = descriptor
                _lease_holders[key] = 1
                _lease_owners[key] = self._owner
            self._key = key
            self._held = True
            return True

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        key = self._key
        descriptor: int | None = None
        with _lease_guard:
            count = _lease_holders.get(key, 0) - 1
            if count > 0:
                _lease_holders[key] = count
                return
            _lease_holders.pop(key, None)
            _lease_owners.pop(key, None)
            descriptor = _lease_fds.pop(key, None)
        if descriptor is None:
            return
        try:
            import msvcrt

            os.lseek(descriptor, 1, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            os.close(descriptor)


GLOBAL_SKILLS_OWNER = "default"
