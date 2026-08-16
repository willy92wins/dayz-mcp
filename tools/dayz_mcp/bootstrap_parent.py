"""Accredit the sealed native PE that is allowed to spawn bootstrap callers."""

from __future__ import annotations

import os

from dayz_mcp import launcher_registry, native_process_snapshot
from dayz_mcp.native_process_guard import NativeProcessGuard


_BOOTSTRAP_LAUNCHER_ID = "dayz-test-v1"
_IDENTITY_FIELDS = (
    "pid",
    "creation_time_utc",
    "executable_sha256",
    "command_line_sha256",
    "identity_scheme",
)


def _snapshot_matches(
    snapshot: object,
    *,
    pid: int,
    expected_executable_sha256: str,
) -> bool:
    return (
        isinstance(snapshot, dict)
        and snapshot.get("identity_complete") is True
        and snapshot.get("identity_scheme") == "psutil-argv-v2"
        and snapshot.get("pid") == pid
        and isinstance(snapshot.get("creation_time_utc"), str)
        and bool(snapshot.get("creation_time_utc"))
        and str(snapshot.get("executable_sha256", "")).casefold()
        == expected_executable_sha256.casefold()
        and isinstance(snapshot.get("command_line_sha256"), str)
        and len(str(snapshot.get("command_line_sha256"))) == 64
    )


def accredit_registered_bootstrap_parent(*, guard: object | None = None) -> None:
    try:
        parent_pid = os.getppid()
        if (
            not isinstance(parent_pid, int)
            or isinstance(parent_pid, bool)
            or parent_pid <= 0
        ):
            raise ValueError("unaccredited_bootstrap_parent")
        identity_guard = guard or NativeProcessGuard()
        with launcher_registry.open_approved_launcher(
            _BOOTSTRAP_LAUNCHER_ID
        ) as opened:
            opened.revalidate()
            path_a = native_process_snapshot.full_image_path_of(parent_pid)
            snapshot_a = identity_guard.snapshot(parent_pid)  # type: ignore[attr-defined]
            opened.revalidate()
            path_b = native_process_snapshot.full_image_path_of(parent_pid)
            snapshot_b = identity_guard.snapshot(parent_pid)  # type: ignore[attr-defined]
            opened.revalidate()
            if (
                not native_process_snapshot.same_path(path_a, str(opened.path))
                or not native_process_snapshot.same_path(path_b, str(opened.path))
                or not _snapshot_matches(
                    snapshot_a,
                    pid=parent_pid,
                    expected_executable_sha256=opened.sha256,
                )
                or not _snapshot_matches(
                    snapshot_b,
                    pid=parent_pid,
                    expected_executable_sha256=opened.sha256,
                )
                or any(
                    snapshot_b.get(field) != snapshot_a.get(field)
                    for field in _IDENTITY_FIELDS
                )
            ):
                raise ValueError("unaccredited_bootstrap_parent")
    except Exception:
        raise ValueError("unaccredited_bootstrap_parent") from None
