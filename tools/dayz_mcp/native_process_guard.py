from __future__ import annotations

import hashlib
import json
import math
import ntpath
from collections import deque
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

try:
    import psutil
except ImportError:  # pragma: no cover - exercised through the fail-closed runtime path.
    psutil = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from dayz_mcp.process_lifecycle import ProcessRecord


IDENTITY_SCHEME = "psutil-argv-v2"
_HEX = frozenset("0123456789abcdef")


def _identity_failure(pid: object, error: str, exit_code: int) -> dict[str, object]:
    return {
        "pid": pid,
        "identity_scheme": IDENTITY_SCHEME,
        "identity_complete": False,
        "error": error,
        "exit_code": exit_code,
    }


def _termination_failure(
    pid: object,
    error: str,
    exit_code: int,
    *,
    identity_complete: bool = False,
) -> dict[str, object]:
    return {
        "pid": pid,
        "identity_scheme": IDENTITY_SCHEME,
        "identity_complete": identity_complete,
        "terminated": False,
        "error": error,
        "exit_code": exit_code,
    }


def _is_psutil_error(error: BaseException, name: str) -> bool:
    if psutil is None:
        return False
    error_type = getattr(psutil, name, None)
    return isinstance(error_type, type) and isinstance(error, error_type)


def _valid_pid(pid: object) -> bool:
    return isinstance(pid, int) and not isinstance(pid, bool) and pid > 0


def _valid_hash(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX for character in value.casefold())
    )


def identity_hashes(executable: str, argv: list[str]) -> dict[str, str]:
    if (
        not isinstance(executable, str)
        or not executable
        or not ntpath.isabs(executable)
        or not isinstance(argv, list)
        or not argv
        or any(not isinstance(argument, str) or not argument for argument in argv)
    ):
        raise ValueError("invalid_process_identity_input")
    normalized_executable = ntpath.normpath(executable).casefold().encode("utf-8")
    encoded_argv = json.dumps(
        argv,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "executable_sha256": hashlib.sha256(
            b"psutil-exe-v2\0" + normalized_executable
        ).hexdigest(),
        "command_line_sha256": hashlib.sha256(
            b"psutil-argv-v2\0" + encoded_argv
        ).hexdigest(),
    }


class NativeProcessGuard:
    @staticmethod
    def _process(pid: int) -> object:
        if psutil is None:
            raise RuntimeError("psutil_unavailable")
        return psutil.Process(pid)

    @staticmethod
    def _snapshot_process(process: object, expected_pid: int) -> dict[str, object]:
        observed_pid = getattr(process, "pid")
        if observed_pid != expected_pid or not _valid_pid(observed_pid):
            raise ValueError("pid_mismatch")

        with process.oneshot():  # type: ignore[attr-defined]
            create_time = process.create_time()  # type: ignore[attr-defined]
            executable = process.exe()  # type: ignore[attr-defined]
            argv = process.cmdline()  # type: ignore[attr-defined]

        if (
            not isinstance(create_time, (int, float))
            or isinstance(create_time, bool)
            or not math.isfinite(create_time)
            or create_time <= 0
        ):
            raise ValueError("invalid_create_time")
        if (
            not isinstance(executable, str)
            or not executable
            or not ntpath.isabs(executable)
        ):
            raise ValueError("invalid_executable")
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(argument, str) or not argument for argument in argv)
        ):
            raise ValueError("invalid_argv")

        creation_time = datetime.fromtimestamp(create_time, UTC)
        creation_time_utc = creation_time.isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
        hashes = identity_hashes(executable, argv)

        return {
            "pid": observed_pid,
            "creation_time_utc": creation_time_utc,
            "executable_sha256": hashes["executable_sha256"],
            "command_line_sha256": hashes["command_line_sha256"],
            "identity_scheme": IDENTITY_SCHEME,
            "identity_complete": True,
            "exit_code": 0,
        }

    def snapshot(self, pid: int) -> dict[str, object]:
        if not _valid_pid(pid):
            return _identity_failure(pid, "identity_unavailable", 3)
        try:
            process = self._process(pid)
            return self._snapshot_process(process, pid)
        except Exception as error:
            if _is_psutil_error(error, "NoSuchProcess"):
                return _identity_failure(pid, "process_not_found", 4)
            return _identity_failure(pid, "identity_unavailable", 3)

    @staticmethod
    def _expected_is_valid(expected: object) -> bool:
        try:
            pid = expected.pid  # type: ignore[attr-defined]
            creation_time_utc = expected.creation_time_utc  # type: ignore[attr-defined]
            executable_sha256 = expected.executable_sha256  # type: ignore[attr-defined]
            command_line_sha256 = expected.command_line_sha256  # type: ignore[attr-defined]
            role = expected.role  # type: ignore[attr-defined]
            identity_scheme = expected.identity_scheme  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            return False
        return (
            _valid_pid(pid)
            and isinstance(creation_time_utc, str)
            and bool(creation_time_utc)
            and _valid_hash(executable_sha256)
            and _valid_hash(command_line_sha256)
            and isinstance(role, str)
            and bool(role)
            and identity_scheme == IDENTITY_SCHEME
        )

    @staticmethod
    def _identity_matches(expected: object, actual: dict[str, object]) -> bool:
        return (
            actual.get("identity_complete") is True
            and actual.get("identity_scheme") == expected.identity_scheme  # type: ignore[attr-defined]
            and actual.get("pid") == expected.pid  # type: ignore[attr-defined]
            and actual.get("creation_time_utc") == expected.creation_time_utc  # type: ignore[attr-defined]
            and actual.get("executable_sha256") == expected.executable_sha256  # type: ignore[attr-defined]
            and actual.get("command_line_sha256") == expected.command_line_sha256  # type: ignore[attr-defined]
        )

    def terminate(self, expected: ProcessRecord) -> dict[str, object]:
        if not self._expected_is_valid(expected):
            return _termination_failure(None, "invalid_expected_identity", 3)

        pid = expected.pid
        try:
            process = self._process(pid)
            actual = self._snapshot_process(process, pid)
        except Exception as error:
            if _is_psutil_error(error, "NoSuchProcess"):
                return _termination_failure(pid, "process_not_found", 4)
            return _termination_failure(pid, "identity_unavailable", 3)

        if not self._identity_matches(expected, actual):
            return _termination_failure(
                pid,
                "process_identity_mismatch",
                4,
                identity_complete=True,
            )

        try:
            process.kill()  # type: ignore[attr-defined]
            process.wait(timeout=5.0)  # type: ignore[attr-defined]
        except Exception as error:
            if _is_psutil_error(error, "NoSuchProcess"):
                return _termination_failure(pid, "process_not_found", 4)
            if _is_psutil_error(error, "TimeoutExpired"):
                return _termination_failure(pid, "termination_unconfirmed", 3)
            return _termination_failure(pid, "termination_unavailable", 3)

        result = dict(actual)
        result["terminated"] = True
        return result

    def discover(
        self,
        root_pid: int,
        allowlisted_names: set[str],
    ) -> dict[str, object]:
        if (
            not _valid_pid(root_pid)
            or not isinstance(allowlisted_names, set)
            or any(not isinstance(name, str) or not name for name in allowlisted_names)
        ):
            return {
                "processes": [],
                **_identity_failure(root_pid, "identity_unavailable", 3),
            }

        allowed = {name.casefold() for name in allowlisted_names}
        try:
            root = self._process(root_pid)
        except Exception as error:
            failure = (
                _identity_failure(root_pid, "process_not_found", 4)
                if _is_psutil_error(error, "NoSuchProcess")
                else _identity_failure(root_pid, "identity_unavailable", 3)
            )
            return {"processes": [], **failure}

        queue: deque[tuple[object, bool]] = deque(((root, True),))
        observed: list[dict[str, object]] = []
        while queue:
            process, is_root = queue.popleft()
            try:
                expected_pid = root_pid if is_root else getattr(process, "pid")
                snapshot = self._snapshot_process(process, expected_pid)
            except Exception as error:
                if not is_root and _is_psutil_error(error, "NoSuchProcess"):
                    continue
                failure = (
                    _identity_failure(root_pid, "process_not_found", 4)
                    if is_root and _is_psutil_error(error, "NoSuchProcess")
                    else _identity_failure(root_pid, "identity_unavailable", 3)
                )
                return {"processes": [], **failure}
            observed.append(snapshot)

            try:
                children = process.children(recursive=False)  # type: ignore[attr-defined]
                if not isinstance(children, list):
                    raise ValueError("invalid_children")
                for child in children:
                    try:
                        name = child.name()
                    except Exception as error:
                        if _is_psutil_error(error, "NoSuchProcess"):
                            continue
                        raise
                    if not isinstance(name, str) or not name:
                        raise ValueError("invalid_process_name")
                    if name.casefold() in allowed:
                        queue.append((child, False))
            except Exception as error:
                if not is_root and _is_psutil_error(error, "NoSuchProcess"):
                    continue
                return {
                    "processes": [],
                    **_identity_failure(root_pid, "identity_unavailable", 3),
                }

        return {
            "processes": observed,
            "identity_scheme": IDENTITY_SCHEME,
            "identity_complete": True,
            "exit_code": 0,
        }
