"""Helpers so existing tests can enqueue mutations under instance fencing."""
from __future__ import annotations

import os

INST_SERVER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
INST_CLIENT = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
PID_SERVER = 41001
PID_CLIENT = 41002
_FIXTURE_CTIME = "2026-08-18T00:00:00.000000Z"
_FIXTURE_HASH_EXE = "a" * 64
_FIXTURE_HASH_CMD = "b" * 64


def bind_both_peers(
    state: object,
    run_id: str = "test-run",
) -> tuple[str, str]:
    """Bind both fenced peers and register the run without a holder.

    P-I10: this helper never writes an owner. A real manifest records the
    run RUNNING_IDLE with processes the lifecycle guard classifies ``owned``.
    Ownership is obtained only through product operations (``adopt_run`` with
    a real coordinator lease, or ``start_run``).
    """
    installer = getattr(state, "install_bound_peer", None)
    if not callable(installer):
        raise AssertionError("ServerState.install_bound_peer is required")
    installer(
        instance=INST_SERVER,
        role="server",
        pid=PID_SERVER,
        run_id=run_id,
    )
    installer(
        instance=INST_CLIENT,
        role="client",
        pid=PID_CLIENT,
        run_id=run_id,
    )
    _register_manifest_run(state, run_id)
    return INST_SERVER, INST_CLIENT


def _register_manifest_run(state: object, run_id: str) -> None:
    """C1: a real ProcessLifecycle manifest must know the bound run.

    Production mints the run through start_run before any binding. Tests that
    call bind_both_peers against a daemon-wired lifecycle skip that path, so
    the helper records the run when ``manifest.add`` exists. A
    ``_BoundPeerDispatchable`` (no ``add``) is left alone — C5.

    P-I10: the owner is never written here. The run is always RUNNING_IDLE
    with owned processes. A fixture that needs a titular acquires a real
    lifecycle lease and calls ``adopt_run``.
    """
    lifecycle = getattr(state, "lifecycle", None)
    if lifecycle is None:
        return
    manifest = getattr(lifecycle, "manifest", None)
    adder = getattr(manifest, "add", None)
    if not callable(adder):
        return
    getter = getattr(manifest, "get", None)
    if callable(getter):
        try:
            if getter(run_id) is not None:
                return
        except Exception:
            return
    from dayz_mcp.process_lifecycle import RunRecord

    record = RunRecord(
        run_id,
        None,
        None,
        "RUNNING_IDLE",
        "fence-fixture",
        "",
        "",
        "",
        _owned_process_records(lifecycle),
    )
    try:
        adder(record)
    except ValueError:
        return


def _owned_process_records(lifecycle: object) -> list[object]:
    """Processes the lifecycle guard will classify ``owned``.

    Fake guards expose a ``snapshots`` dict: seed the complete identity of
    the synthetic peer pids. A real ``NativeProcessGuard`` has no such
    dict — register the live interpreter with the identity ``snapshot``
    returns.
    """
    from dayz_mcp.process_lifecycle import ProcessRecord

    guard = getattr(lifecycle, "guard", None)
    snapshots = getattr(guard, "snapshots", None)
    if isinstance(snapshots, dict):
        processes = [
            ProcessRecord(
                PID_SERVER,
                _FIXTURE_CTIME,
                _FIXTURE_HASH_EXE,
                _FIXTURE_HASH_CMD,
                "server",
                identity_scheme="psutil-argv-v2",
            ),
            ProcessRecord(
                PID_CLIENT,
                _FIXTURE_CTIME,
                _FIXTURE_HASH_EXE,
                _FIXTURE_HASH_CMD,
                "client",
                identity_scheme="psutil-argv-v2",
            ),
        ]
        _seed_guard_snapshots(lifecycle, processes)
        return processes
    if guard is None or not callable(getattr(guard, "snapshot", None)):
        raise AssertionError(
            "real lifecycle guard must expose snapshot() to register a live process"
        )
    pid = os.getpid()
    snap = guard.snapshot(pid)
    if not isinstance(snap, dict) or snap.get("identity_complete") is not True:
        raise AssertionError(
            f"guard.snapshot({pid}) is not a complete identity: {snap!r}"
        )
    return [
        ProcessRecord(
            int(snap["pid"]),
            str(snap["creation_time_utc"]),
            str(snap["executable_sha256"]),
            str(snap["command_line_sha256"]),
            "server",
            identity_scheme=str(snap["identity_scheme"]),
        )
    ]


def _seed_guard_snapshots(lifecycle: object, processes: list[object]) -> None:
    guard = getattr(lifecycle, "guard", None)
    snapshots = getattr(guard, "snapshots", None)
    if not isinstance(snapshots, dict):
        return
    for record in processes:
        pid = getattr(record, "pid", None)
        if not isinstance(pid, int):
            continue
        snapshots.setdefault(
            pid,
            {
                "pid": pid,
                "creation_time_utc": getattr(record, "creation_time_utc", _FIXTURE_CTIME),
                "executable_sha256": getattr(record, "executable_sha256", _FIXTURE_HASH_EXE),
                "command_line_sha256": getattr(
                    record, "command_line_sha256", _FIXTURE_HASH_CMD
                ),
                "identity_scheme": getattr(
                    record, "identity_scheme", "psutil-argv-v2"
                ),
                "identity_complete": True,
            },
        )


def accredited_poll(
    state: object,
    peer: str,
    version: str | None = None,
):
    instance = INST_SERVER if peer == "server" else INST_CLIENT
    pid = PID_SERVER if peer == "server" else PID_CLIENT
    return state.record_poll(peer, version, instance=instance, source_pid=pid)


def bound_queue(state: object, peer: str) -> list[dict]:
    instance = INST_SERVER if peer == "server" else INST_CLIENT
    queues = getattr(state, "_bound_queues", {})
    return list(queues.get(instance, []))
