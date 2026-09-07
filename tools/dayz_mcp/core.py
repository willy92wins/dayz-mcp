"""Shared bridge policy helpers: version gate, status assembly, exec config.

Extracted from ``server.Runtime`` so the standalone daemon and the embedded
runtime share ONE source of truth for the version gate (R6 fail-closed), the
``/status`` payload shape, and the exec allowlist/audit. The broker split (daemon
owns the loopback, sessions proxy as clients) requires this policy to live where
both processes can import it, not buried in the embedded Runtime.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.GetProcessTimes.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    )
    _kernel32.GetProcessTimes.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL
else:
    _kernel32 = None


EXPECTED_BRIDGE_VERSION = "10"

# Peer version_state values that must block command delivery / enqueue.
BLOCKED_VERSION_STATES = {"legacy_blocked", "version_mismatch"}


def version_state_for(
    version: str | None,
    *,
    require_version: bool,
    expected_game_version: str | None,
    expected_bridge_version: str = EXPECTED_BRIDGE_VERSION,
) -> tuple[str, str]:
    """Classify a peer poll ``ver=`` string into (state, detail).

    Verbatim from the former ``Runtime._version_state_for`` so the daemon and the
    embedded runtime classify identically.
    """
    if version is None:
        if require_version:
            return "legacy_blocked", "poll did not include ver="
        return "legacy", "poll did not include ver="

    bridge_version, sep, game_version = version.partition("~")
    if not sep:
        return "version_mismatch", "ver= must be <bridge_version>~<game_version>"
    if bridge_version != expected_bridge_version:
        return "version_mismatch", f"bridge_version {bridge_version!r} != {expected_bridge_version!r}"
    if expected_game_version is not None and game_version != expected_game_version:
        return "version_mismatch", f"game_version {game_version!r} != {expected_game_version!r}"
    return "ok", "version accepted"


def _peer_status(
    snapshot: dict[str, Any],
    peer: str,
    *,
    require_version: bool,
    expected_game_version: str | None,
    expected_bridge_version: str,
) -> dict[str, Any]:
    peer_snapshot = snapshot["peers"][peer]
    version = peer_snapshot.get("version")
    last_poll_age_s = peer_snapshot.get("last_poll_age_s")
    observed_this_generation = last_poll_age_s is not None
    state, detail = version_state_for(
        version,
        require_version=require_version,
        expected_game_version=expected_game_version,
        expected_bridge_version=expected_bridge_version,
    )
    if not observed_this_generation:
        # Do not serve the computed/persisted verdict (legacy_blocked,
        # version_mismatch, "poll did not include ver=") as if this
        # generation observed a poll. Label the payload instead.
        state = "never_polled_this_generation"
        detail = "never_polled_this_generation"
    # M22 carrier (addendum enmienda 2026-09-03). M06 publishes the capability
    # census on the raw snapshot and M22 compares it against the registered
    # tools; this dict is the only thing between them, and it is built from a
    # fixed key set, so anything not named here dies here. The shape is kept
    # constant -- an older loopback with no census still yields a block that
    # says unknown, rather than a missing key each consumer has to guess about.
    capabilities = peer_snapshot.get("capabilities")
    if not isinstance(capabilities, dict):
        capabilities = {"state": "unknown", "reason": "absent", "announced_commands": []}
    return {
        "last_poll_age_s": last_poll_age_s,
        "queue_depth": peer_snapshot.get("queue_depth", 0),
        "capabilities": capabilities,
        "version": version,
        "version_state": state,
        "version_detail": detail,
        "observed_this_generation": observed_this_generation,
        "binding_state": peer_snapshot.get("binding_state"),
        "instance_prefix": peer_snapshot.get("instance_prefix"),
        "bound_last_poll_age_s": peer_snapshot.get("bound_last_poll_age_s"),
    }


def build_status(
    snapshot: dict[str, Any],
    *,
    require_version: bool,
    expected_game_version: str | None,
    expected_bridge_version: str = EXPECTED_BRIDGE_VERSION,
) -> dict[str, Any]:
    """Assemble the rich status dict (per-peer version_state + metadata) from a raw
    ``ServerState.status_snapshot()``. Shared by ``Runtime.status()`` and the
    daemon ``/status`` endpoint so embedded and client ``bridge_status`` agree.
    """
    server_peer = _peer_status(
        snapshot,
        "server",
        require_version=require_version,
        expected_game_version=expected_game_version,
        expected_bridge_version=expected_bridge_version,
    )
    client_peer = _peer_status(
        snapshot,
        "client",
        require_version=require_version,
        expected_game_version=expected_game_version,
        expected_bridge_version=expected_bridge_version,
    )
    payload = {
        "server_peer": server_peer,
        "client_peer": client_peer,
        "results_pending": snapshot["results_pending"],
        "version_state": {
            "server": server_peer["version_state"],
            "client": client_peer["version_state"],
        },
        "server_version": expected_bridge_version,
        "expected_game_version": expected_game_version,
        "require_version": require_version,
    }
    fence = snapshot.get("fence")
    if isinstance(fence, dict):
        payload["fence"] = fence
    return payload


def _filetime_100ns(stamp: Any) -> int:
    return (int(stamp.dwHighDateTime) << 32) + int(stamp.dwLowDateTime)


def read_process_cpu_times(pid: object) -> dict[str, int] | None:
    """Cumulative CPU times for one pid, or None when the pid cannot be sampled.

    One GetProcessTimes reading is not a utilization rate: the consumer has to
    subtract two samples. This function never invents a percentage.

    A process that has already exited still answers OpenProcess and
    GetProcessTimes while a handle remains open; its totals are frozen. A
    fresh sampled_at_ns on those totals would look like a live idle process,
    so a non-zero exit time is not a sample. created_100ns is the pid-recycle
    discriminator: two samples with the same pid and a different creation
    time are not the same process.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    if os.name != "nt" or _kernel32 is None:
        return None
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not _kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        if _filetime_100ns(exited) != 0:
            return None
        return {
            "user_100ns": _filetime_100ns(user),
            "kernel_100ns": _filetime_100ns(kernel),
            "created_100ns": _filetime_100ns(created),
            "sampled_at_ns": time.time_ns(),
        }
    finally:
        _kernel32.CloseHandle(handle)


def attach_lifecycle_cpu_signals(lifecycle: dict[str, Any]) -> dict[str, Any]:
    """Copy lifecycle.public_status() and hang raw CPU times on each process.

    The pid already lives on the ProcessRecord; this does not scan the process
    table. A process that cannot be sampled is left without a ``cpu`` key
    rather than given a zero that would look like idle rendering.
    """
    if not isinstance(lifecycle, dict):
        return lifecycle
    runs = lifecycle.get("runs")
    if not isinstance(runs, list):
        return lifecycle
    attached_runs: list[Any] = []
    for run in runs:
        if not isinstance(run, dict):
            attached_runs.append(run)
            continue
        processes = run.get("processes")
        if not isinstance(processes, list):
            attached_runs.append(run)
            continue
        attached_processes: list[Any] = []
        for process in processes:
            if not isinstance(process, dict):
                attached_processes.append(process)
                continue
            row = dict(process)
            sample = read_process_cpu_times(process.get("pid"))
            if sample is not None:
                row["cpu"] = sample
            attached_processes.append(row)
        attached_run = dict(run)
        attached_run["processes"] = attached_processes
        attached_runs.append(attached_run)
    attached = dict(lifecycle)
    attached["runs"] = attached_runs
    return attached


def load_exec_allowlist(path: str | None) -> set[str]:
    """Load the exec allowlist JSON. ``utf-8-sig`` tolerates a BOM that Windows
    editors prepend, which plain utf-8 choked on at startup."""
    if path is None:
        return set()
    with open(path, "r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise ValueError("--exec-allowlist must be a JSON list of strings")
    return set(payload)


def make_exec_auditor(audit_path: Path) -> Callable[[str, str, str, int | None], None]:
    """Return an ExecAudit callable that appends a JSONL audit entry. Matches the
    loopback ``ExecAudit`` signature ``(expr, verdict, main_fn, command_id)``."""

    def audit(expr: str, verdict: str, main_fn: str = "", command_id: int | None = None) -> None:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        entry: dict[str, Any] = {
            "ts_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "expr": expr,
            "main_fn": main_fn,
            "verdict": verdict,
        }
        if command_id is not None:
            entry["command_id"] = command_id
        with audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, separators=(",", ":")) + "\n")

    return audit
