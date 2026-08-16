"""Standalone broker daemon: the single owner of 127.0.0.1:<port>.

The daemon decouples the loopback from any one Cowork session so that MANY
sessions (each a ``--client`` MCP server) can drive ONE DayZ game at once. It
owns the ServerState, the version gate, the exec chokepoint, the E4 exclusive
bind, and the idle self-shutdown. It is spawned DETACHED by the first session and
is meant to OUTLIVE that session.

Lifecycle (deliberately different from the embedded server.py):
- NO parent-death watchdog — the daemon must survive its spawner. Reaping is by
  idle self-shutdown only (no game polling AND no client requests for T seconds).
- Discovery is health-probe based: if a healthy daemon already owns the port, a
  freshly spawned daemon exits 0 (lost the race); the client connects to the winner.
- Reclaim is health-gated (orphan_guard.try_reclaim_unresponsive_listener): a
  healthy holder is never killed; only an unresponsive dayz_mcp orphan is.
"""
from __future__ import annotations

import math
import hashlib
import dataclasses
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from dayz_mcp import core, orphan_guard
from dayz_mcp.daemon_contract import build_daemon_argv, daemon_runtime_cwd
from dayz_mcp.identity_migration import (
    RunsBackupGateError,
    capture_launch_ancestor_identity,
    daemon_startup_election,
    ensure_runs_v1_backup,
)
from dayz_mcp.loopback import (
    ServerState,
    _is_address_in_use,
    create_http_server,
    read_key,
)
from dayz_mcp.runtime_state import (
    CoordinationFaultStore,
    CoordinationSnapshotStore,
    JsonlAuditWriter,
    LifecycleRecoveryFaultStore,
    RuntimePaths,
    recover_coordination_startup,
)
from dayz_mcp.process_lifecycle import (
    ProcessLifecycle,
    RunManifestStore,
)
from dayz_mcp.native_process_guard import NativeProcessGuard
from dayz_mcp.session_coordination import CleanupDisposition, SessionCoordinator

# Windows process-creation flags for a detached, session-surviving child.
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000

# How often the daemon retires runs whose processes are all confirmed dead, so a
# crashed DayZ never permanently blocks the box (latency of auto-heal, not correctness).
REAP_INTERVAL_S = 30.0
DAEMON_STARTUP_CONTENDED = 75
MIGRATION_CANDIDATE_DRAIN_S = 30.0
STATUS_ACTIVATION_TIMEOUT_S = 5.0
STARTUP_BUDGET_MARGIN_S = 2.0
DAEMON_STARTUP_BUDGET_S = 40.0
MAX_DAEMON_STARTUP_BUDGET_S = 3600.0


def cleanup_begin(
    state: ServerState,
    lifecycle: ProcessLifecycle | None,
    session_id: str,
    lease_id: str,
    reason: str,
    vehicle_active: bool,
) -> dict[str, object] | CleanupDisposition:
    result = state.cleanup_owner(session_id, lease_id, reason, vehicle_active)
    if lifecycle is None:
        return result
    try:
        disposition = lifecycle.begin_release_owner(session_id, lease_id)
    except Exception:
        degraded = result.get("cleanup_degraded", [])
        if not isinstance(degraded, list):
            degraded = []
        result["cleanup_degraded"] = list(
            dict.fromkeys([*degraded, "run_manifest_failed"])
        )
        result.update(
            {
                "terminal_safe": False,
                "error": "run_manifest_failed",
            }
        )
        terminal = threading.Event()
        terminal.set()
        return CleanupDisposition(True, terminal, result)
    for key, value in result.items():
        disposition.terminal_result.setdefault(key, value)
    return disposition


def recover_unacknowledged_before_listen(
    lifecycle: ProcessLifecycle,
    manifest: RunManifestStore,
    *,
    deadline: float,
    on_pending: Callable[[object, str], object] | None = None,
) -> list[str]:
    recovered: list[str] = []
    for run in manifest.list_runs():
        if (
            getattr(run, "launch_operation_id", None) is None
            or getattr(run, "launch_acknowledged", True) is not False
            or getattr(run, "state", "EXITED") == "EXITED"
        ):
            continue
        session_id = getattr(run, "owner_session_id", None)
        lease_id = getattr(run, "owner_lease_id", None)
        if not isinstance(session_id, str) or not isinstance(lease_id, str):
            if on_pending is not None:
                on_pending(run, "identity_ambiguous")
            continue
        disposition = lifecycle.begin_release_owner(session_id, lease_id)
        remaining = max(0.0, float(deadline) - time.monotonic())
        if not disposition.terminal_event.wait(remaining):
            if on_pending is not None:
                on_pending(run, "cleanup_failed")
            continue
        if disposition.terminal_result.get("terminal_safe") is True:
            released = disposition.terminal_result.get("runs_released", [])
            if isinstance(released, list):
                recovered.extend(str(item) for item in released)
        elif on_pending is not None:
            on_pending(
                run,
                str(disposition.terminal_result.get("error", "cleanup_failed")),
            )
    return sorted(set(recovered))


def validated_startup_budget_s(value: float | None = None) -> float:
    budget = DAEMON_STARTUP_BUDGET_S if value is None else float(value)
    if (
        not math.isfinite(budget)
        or budget <= 0.0
        or budget > MAX_DAEMON_STARTUP_BUDGET_S
    ):
        raise ValueError("invalid_daemon_startup_budget")
    required = (
        MIGRATION_CANDIDATE_DRAIN_S
        + STATUS_ACTIVATION_TIMEOUT_S
        + STARTUP_BUDGET_MARGIN_S
    )
    if value is None and budget <= required:
        raise RuntimeError("daemon_startup_budget_too_small")
    return budget


def _noop(_message: str) -> None:
    return


def _log_sink(config: Any) -> Callable[[str], None]:
    sink = getattr(config, "log_sink", None)
    if sink is not None:
        return sink
    return lambda message: print(message, file=sys.stderr, flush=True)


def _required_keyfile(config: Any) -> str:
    keyfile = getattr(config, "keyfile", None)
    if not keyfile:
        raise ValueError("--keyfile is required for the daemon")
    return keyfile


def _ensure_identity_migration(config: Any) -> None:
    approved_python = (
        Path(__file__).resolve().parents[1]
        / ".venv-mcp"
        / "Scripts"
        / "python.exe"
    )
    try:
        approved = approved_python.resolve(strict=True)
        current = Path(sys.executable).resolve(strict=True)
    except OSError as error:
        raise RuntimeError("daemon_python_not_approved") from error
    if os.path.normcase(str(current)) != os.path.normcase(str(approved)):
        raise RuntimeError("daemon_python_not_approved")
    guard = NativeProcessGuard()
    identity = guard.snapshot(os.getpid())
    if (
        not isinstance(identity, dict)
        or identity.get("identity_complete") is not True
        or identity.get("identity_scheme") != "psutil-argv-v2"
    ):
        raise RuntimeError("daemon_identity_unavailable")
    launch_ancestor_identity = capture_launch_ancestor_identity(
        identity,
        approved,
        guard=guard,
    )
    migration_kwargs: dict[str, object] = {}
    test_migration_dir = getattr(config, "_identity_migration_dir", None)
    test_fault_injector = getattr(config, "_identity_migration_fault_injector", None)
    if test_migration_dir is not None:
        migration_kwargs["migration_dir"] = Path(test_migration_dir)
    if test_fault_injector is not None:
        if not callable(test_fault_injector):
            raise RuntimeError("invalid_identity_migration_fault_injector")
        migration_kwargs["fault_injector"] = test_fault_injector
    ensure_runs_v1_backup(
        RuntimePaths.from_env(),
        int(getattr(config, "port", 8765)),
        allowed_current_identity=identity,
        allowed_launch_ancestor_identity=launch_ancestor_identity,
        **migration_kwargs,
    )


def _ensure_identity_migration_after_candidate_drain(
    config: Any,
    *,
    deadline: float,
    time_fn: Callable[[], float] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> None:
    now = time_fn or time.monotonic
    sleep = sleep_fn or time.sleep
    if (
        not isinstance(deadline, (int, float))
        or isinstance(deadline, bool)
        or not math.isfinite(float(deadline))
    ):
        raise ValueError("invalid_daemon_startup_deadline")
    drain_deadline = min(float(deadline), now() + MIGRATION_CANDIDATE_DRAIN_S)
    while True:
        if now() >= drain_deadline:
            raise TimeoutError("daemon_migration_drain_deadline_exceeded")
        try:
            _ensure_identity_migration(config)
            if now() >= float(deadline):
                raise TimeoutError("daemon_startup_deadline_exceeded")
            return
        except RunsBackupGateError as error:
            if (
                str(error) != "dayz_mcp_process_present"
                or now() >= drain_deadline
            ):
                raise
            sleep(min(0.05, max(0.0, drain_deadline - now())))


def _status_accredits_generation(
    port: int,
    key: str,
    daemon_generation: str,
    *,
    deadline: float,
    expected_executable: str,
    expected_argv: list[str],
    expected_cwd: str,
    probe_fn: Callable[..., bool] | None = None,
    time_fn: Callable[[], float] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> bool:
    if (
        not isinstance(deadline, (int, float))
        or isinstance(deadline, bool)
        or not math.isfinite(float(deadline))
    ):
        return False
    probe = probe_fn or orphan_guard.probe_status_healthy
    now = time_fn or time.monotonic
    sleep = sleep_fn or time.sleep
    while True:
        remaining = float(deadline) - now()
        if remaining <= 0.0:
            break
        if probe(
            port,
            key,
            deadline=float(deadline),
            expected_generation=daemon_generation,
            expected_executable=expected_executable,
            expected_argv=expected_argv,
            expected_cwd=expected_cwd,
        ):
            return True
        remaining = float(deadline) - now()
        if remaining <= 0.0:
            break
        sleep(min(0.05, remaining))
    return False


def _audit_path(config: Any) -> Path:
    configured = getattr(config, "exec_audit_path", None)
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "_audit" / "exec_enforce.jsonl"


def build_server_state(
    config: Any,
    key: str,
    *,
    daemon_generation: str | None = None,
    activate_coordination: bool = False,
) -> ServerState:
    """Build a ServerState wired with the daemon's version gate + exec chokepoint
    (the policy that loopback.enqueue_command/record_poll enforce)."""
    enable_exec = bool(getattr(config, "enable_exec_enforce", False))
    exec_allowlist = core.load_exec_allowlist(getattr(config, "exec_allowlist", None)) if enable_exec else None
    exec_audit = core.make_exec_auditor(_audit_path(config)) if enable_exec else None
    validator = lambda version: core.version_state_for(
        version,
        require_version=bool(getattr(config, "require_version", False)),
        expected_game_version=getattr(config, "expected_game_version", None),
    )[0]
    generation = daemon_generation or uuid.uuid4().hex
    state = ServerState(
        key,
        enable_exec_enforce=enable_exec,
        version_validator=validator,
        exec_allowlist=exec_allowlist,
        exec_audit=exec_audit,
        coordination=None,
    )
    state.daemon_generation = generation
    if activate_coordination:
        activation_deadline = time.monotonic() + validated_startup_budget_s()
        _ensure_identity_migration(config)
        _activate_server_coordination(
            state, generation, deadline=activation_deadline
        )
    return state


def _activate_server_coordination(
    state: ServerState,
    daemon_generation: str,
    *,
    deadline: float | None = None,
    time_fn: Callable[[], float] | None = None,
) -> None:
    now = time_fn or time.monotonic
    if deadline is None:
        deadline = now() + validated_startup_budget_s()
    if (
        not isinstance(deadline, (int, float))
        or isinstance(deadline, bool)
        or not math.isfinite(float(deadline))
    ):
        raise ValueError("invalid_daemon_startup_deadline")

    def require_remaining() -> None:
        if now() >= float(deadline):
            raise TimeoutError("daemon_startup_deadline_exceeded")

    def bounded_io(operation: Callable[..., Any], *args: object, **kwargs: object):
        require_remaining()
        result = operation(*args, **kwargs)
        require_remaining()
        return result

    paths = bounded_io(RuntimePaths.from_env)
    audit_writer = bounded_io(JsonlAuditWriter, paths, daemon_generation)
    coordination_store = bounded_io(
        CoordinationSnapshotStore, paths, daemon_generation
    )
    coordination_fault_store = bounded_io(CoordinationFaultStore, paths)
    startup_recovery = bounded_io(
        recover_coordination_startup,
        paths,
        coordination_fault_store,
        daemon_generation,
    )
    recovered_audit_fault = startup_recovery.audit_fault
    lifecycle_holder: dict[str, ProcessLifecycle] = {}
    lifecycle_recovery_store = bounded_io(LifecycleRecoveryFaultStore, paths)
    recovered_lifecycle = bounded_io(lifecycle_recovery_store.load_active)
    recovered_lifecycle_fault = (
        recovered_lifecycle
        if isinstance(recovered_lifecycle, dict)
        and isinstance(recovered_lifecycle.get("event"), dict)
        and recovered_lifecycle["event"].get("state") != "repaired"
        else None
    )
    try:
        manifest = bounded_io(
            RunManifestStore,
            paths,
            checkpoint=lifecycle_recovery_store.checkpoint_manifest,
        )
    except ValueError as error:
        if str(error) != "invalid_run_manifest":
            raise
        try:
            corrupt_raw = bounded_io(paths.runs_path.read_bytes)
        except OSError:
            corrupt_raw = b""
        if recovered_lifecycle_fault is None:
            checkpoint = bounded_io(
                lifecycle_recovery_store.load_manifest_checkpoint
            )
            if checkpoint is None:
                raise RuntimeError("lifecycle_manifest_checkpoint_missing") from error
            _backup_raw, backup_receipt_sha256 = checkpoint
            recovered_lifecycle_fault = bounded_io(
                lifecycle_recovery_store.arm,
                scope="manifest",
                reason="manifest_corrupt",
                manifest_sha256=hashlib.sha256(corrupt_raw).hexdigest(),
                backup_receipt_sha256=backup_receipt_sha256,
            )
        elif recovered_lifecycle_fault.get("fault", {}).get("scope") != "manifest":
            raise RuntimeError("lifecycle_recovery_fault_conflict") from error
        manifest = RunManifestStore.empty_for_recovery(
            paths,
            checkpoint=lifecycle_recovery_store.checkpoint_manifest,
        )
    if recovered_lifecycle_fault is None:
        bounded_io(
            manifest.quarantine_legacy_active,
            lambda event: bounded_io(audit_writer.write, event),
        )

    def cleanup(
        session_id: str, lease_id: str, reason: str, vehicle_active: bool
    ) -> dict[str, object] | CleanupDisposition:
        lifecycle = lifecycle_holder.get("service")
        return cleanup_begin(
            state,
            lifecycle,
            session_id,
            lease_id,
            reason,
            vehicle_active,
        )

    coordinator = bounded_io(
        SessionCoordinator,
        audit=audit_writer.write,
        cleanup=cleanup,
        recovered_audit_fault=recovered_audit_fault,
        recovered_lifecycle_fault=recovered_lifecycle_fault,
        daemon_generation=daemon_generation,
        fault_arm=coordination_fault_store.arm,
        fault_transition=coordination_fault_store.transition,
        fault_clear=coordination_fault_store.clear,
        persist_snapshot=coordination_store.ensure_coordination,
    )
    guard = bounded_io(NativeProcessGuard)

    def arm_lifecycle_recovery_fault(run: object, reason: str) -> None:
        if not hasattr(run, "run_id") or reason not in {
            "identity_ambiguous",
            "cleanup_failed",
        }:
            raise ValueError("invalid_lifecycle_recovery_fault")
        existing = lifecycle_recovery_store.load_active()
        if (
            isinstance(existing, dict)
            and isinstance(existing.get("fault"), dict)
            and existing["fault"].get("run_id") == str(run.run_id)
            and existing["fault"].get("launch_operation_id")
            == str(run.launch_operation_id)
            and isinstance(existing.get("event"), dict)
            and existing["event"].get("state") != "repaired"
        ):
            coordinator.arm_lifecycle_recovery_fault(existing)
            return
        raw = paths.runs_path.read_bytes()
        backup_receipt_sha256 = lifecycle_recovery_store.create_manifest_backup(
            raw
        )
        run_payload = dataclasses.asdict(run)
        run_record_sha256 = hashlib.sha256(
            json.dumps(
                run_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        active_fault = lifecycle_recovery_store.arm(
            scope="run",
            reason=reason,
            manifest_sha256=hashlib.sha256(raw).hexdigest(),
            backup_receipt_sha256=backup_receipt_sha256,
            run_id=str(run.run_id),
            launch_operation_id=str(run.launch_operation_id),
            run_record_sha256=run_record_sha256,
        )
        coordinator.arm_lifecycle_recovery_fault(active_fault)

    lifecycle = bounded_io(
        ProcessLifecycle,
        coordinator=coordinator,
        manifest=manifest,
        audit=audit_writer.write,
        guard=guard,
        retail_probe=orphan_guard.snapshot_retail_processes,
        diag_probe=lambda: orphan_guard.snapshot_processes_by_name(
            ["DayZDiag_x64.exe"]
        ),
        game_path=Path(
            os.environ.get(
                "DAYZ_GAME_PATH",
                r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
            )
        ),
        recovery_fault_arm=arm_lifecycle_recovery_fault,
    )
    if recovered_lifecycle_fault is None:
        bounded_io(
            recover_unacknowledged_before_listen,
            lifecycle,
            manifest,
            deadline=float(deadline),
            on_pending=arm_lifecycle_recovery_fault,
        )
        runs = bounded_io(manifest.list_runs)
    else:
        runs = []
    restart_owned = [
        run.run_id
        for run in runs
        if run.state == "RUNNING" and run.owner_session_id is not None
    ]
    restart_transient = [
        run.run_id
        for run in runs
        if run.state in {"STARTING", "STOPPING"}
    ]
    if restart_owned or restart_transient:
        if bounded_io(
            audit_writer.write,
            {
                "event": "lifecycle_restart_recovery",
                "reason": "daemon_restart",
                "duration_s": 0.0,
                "decision": "recover_interrupted_lifecycle",
                "released_run_ids": sorted(restart_owned),
                "unreconciled_run_ids": sorted(restart_transient),
            },
        ) is not True:
            raise RuntimeError("restart_run_recovery_audit_failed")
        bounded_io(manifest.recover_after_restart)
    lifecycle_holder["service"] = lifecycle
    if startup_recovery.can_consume_snapshot:
        restart_event = bounded_io(
            coordination_store.consume_previous_generation,
            daemon_generation,
            previous_snapshot=startup_recovery.snapshot,
        )
        if restart_event:
            bounded_io(audit_writer.write, restart_event)
    require_remaining()
    state.coordination = coordinator
    state.coordination_store = coordination_store
    state.coordination_fault_store = coordination_fault_store
    state.lifecycle_recovery_fault_store = lifecycle_recovery_store
    state.audit_writer = audit_writer
    state.retail_probe = orphan_guard.snapshot_retail_processes
    state.lifecycle = lifecycle


# F2.3: this process caches its modules IN MEMORY at import time, so editing one
# on disk changes nothing until it restarts -- a failure that reads as "the verb
# does not exist" and has already cost a session. Report it; do NOT hot-reload
# (more fragile than telling).
#
# The watch set is the process's OWN import closure rather than a hand-written
# list: a fixed list both lies (naming a module this process never imported, so
# editing it warns for nothing) and misses (omitting one it did import, so a
# genuinely stale module stays silent).


def _loaded_module_files() -> dict[str, str]:
    files: dict[str, str] = {}
    for name, module in list(sys.modules.items()):
        if not name.startswith("dayz_mcp."):
            continue
        path = getattr(module, "__file__", None)
        if isinstance(path, str) and path:
            files[Path(path).name] = path
    return files


def _module_mtimes(files: dict[str, str]) -> dict[str, float | None]:
    mtimes: dict[str, float | None] = {}
    for name, path in files.items():
        try:
            mtimes[name] = Path(path).stat().st_mtime
        except OSError:
            mtimes[name] = None
    return mtimes


def make_status_provider(config: Any, state: ServerState) -> Callable[[], dict]:
    # Snapshot at construction (daemon boot). Comparing against the snapshot, not
    # against a wall-clock started_at, answers the exact question -- "did this
    # file change after I loaded it?" -- and survives clock adjustments.
    daemon_started_at = time.time()
    watched_files = _loaded_module_files()
    mtimes_at_start = _module_mtimes(watched_files)

    def status_provider() -> dict:
        payload = core.build_status(
            state.status_snapshot(),
            require_version=bool(getattr(config, "require_version", False)),
            expected_game_version=getattr(config, "expected_game_version", None),
        )
        generation = state.daemon_generation
        coordination = (
            state.coordination.snapshot_payload()
            if state.coordination is not None
            else {"active": None, "releasing": None, "queue": []}
        )
        payload["daemon_generation"] = generation
        payload["coordination"] = coordination
        payload["daemon_status"] = {
            "schema": orphan_guard.DAEMON_STATUS_SCHEMA,
            "product": "dayz_mcp",
            "mode": "daemon",
            "daemon_generation": generation,
            "coordination_revision": coordination.get("revision"),
        }
        if state.lifecycle is not None:
            payload["lifecycle"] = state.lifecycle.public_status()
        mtimes_now = _module_mtimes(watched_files)
        stale = sorted(
            name
            for name, mtime in mtimes_now.items()
            if mtime is not None and mtime != mtimes_at_start.get(name)
        )
        payload["daemon_modules"] = {
            "daemon_started_at": daemon_started_at,
            "watched_count": len(watched_files),
            "stale": stale,
        }
        if stale:
            payload["warnings"] = sorted(
                set(payload.get("warnings", [])) | {"daemon_module_stale"}
            )
        return payload

    return status_provider


def _bind_with_reclaim(
    port: int,
    key: str,
    state: ServerState,
    log: Callable[[str], None],
    status_provider: object,
    *,
    deadline: float,
    expected_executable: str,
    expected_argv: list[str],
    expected_cwd: str,
):
    """Exclusive-bind the loopback; on EADDRINUSE, exit-vs-reclaim by health.

    Returns the bound httpd, or None when a healthy daemon already owns the port
    (race lost → caller exits 0). Re-raises on any non-EADDRINUSE bind error.
    """
    if (
        not isinstance(deadline, (int, float))
        or isinstance(deadline, bool)
        or not math.isfinite(float(deadline))
        or time.monotonic() >= float(deadline)
    ):
        raise TimeoutError("daemon_startup_deadline_exceeded")

    def create_bounded_http_server():
        if time.monotonic() >= float(deadline):
            raise TimeoutError("daemon_startup_deadline_exceeded")
        httpd = create_http_server(
            port,
            state,
            log_sink=log,
            reclaim_orphans=False,
            status_provider=status_provider,
        )
        if time.monotonic() >= float(deadline):
            httpd.server_close()
            raise TimeoutError("daemon_startup_deadline_exceeded")
        return httpd

    try:
        return create_bounded_http_server()
    except OSError as exc:
        if not _is_address_in_use(exc):
            raise
        if orphan_guard.probe_status_healthy(
            port,
            key,
            deadline=deadline,
            expected_executable=expected_executable,
            expected_argv=expected_argv,
            expected_cwd=expected_cwd,
        ):
            return None  # Another healthy daemon won the race.
        if not orphan_guard.try_reclaim_unresponsive_listener(
            port,
            deadline=deadline,
            is_healthy=lambda: orphan_guard.probe_status_healthy(
                port,
                key,
                deadline=deadline,
                expected_executable=expected_executable,
                expected_argv=expected_argv,
                expected_cwd=expected_cwd,
            ),
            is_responsive=lambda: orphan_guard.probe_listener_responsive(
                port,
                timeout=max(0.001, min(1.0, deadline - time.monotonic())),
            ),
            log=log,
            expected_executable=expected_executable,
            expected_argv=expected_argv,
        ):
            # The holder is alive (healthy, a foreign-key daemon answering 401, or a
            # winner mid-startup) or unidentifiable — we did NOT reclaim it (fail-closed).
            # Lose the race gracefully (exit 0) instead of crashing; the client re-probes
            # and a key mismatch surfaces as daemon_unavailable rather than a dead winner.
            log(f"DAEMON: 127.0.0.1:{port} held by a live/unreclaimable listener; exiting")
            return None
        # Health-gated reclaim freed the port; retry the exclusive bind exactly once.
        if time.monotonic() >= float(deadline):
            raise TimeoutError("daemon_startup_deadline_exceeded")
        return create_bounded_http_server()


def _make_idle_seconds(state: ServerState, start: float) -> Callable[[], float]:
    def idle_seconds() -> float:
        now = time.monotonic()
        snapshot = state.status_snapshot(now)
        return orphan_guard.compute_idle_seconds(
            now,
            start,
            snapshot.get("last_client_request_at"),
            snapshot["peers"]["server"]["last_poll_at"],
            snapshot["peers"]["client"]["last_poll_at"],
        )

    return idle_seconds


def install_run_reaper(
    lifecycle: Any,
    *,
    interval_s: float = REAP_INTERVAL_S,
    log: Callable[[str], None] = _noop,
    stop: "threading.Event | None" = None,
) -> threading.Thread:
    """Arm a daemon thread that periodically retires runs whose processes are all
    confirmed dead (ProcessLifecycle.reap_dead_runs). Reaps first, then waits, so the
    first pass runs immediately — a fresh/restarted daemon clears a crashed run's stale
    RUNNING_IDLE record within one tick instead of blocking every start until a manual
    admin reconcile. The reaper never terminates a process; it only retires runs with
    zero live processes. ``stop`` and ``interval_s`` are injectable for deterministic tests."""

    def _loop() -> None:
        while stop is None or not stop.is_set():
            try:
                reaped = lifecycle.reap_dead_runs()
            except Exception as exc:  # never let a bad pass kill the reaper thread
                log(f"REAPER: pass failed: {exc}")
                reaped = []
            if reaped:
                log(f"REAPER: retired dead runs {reaped}")
            if stop is not None:
                if stop.wait(interval_s):
                    return
            else:
                time.sleep(interval_s)

    thread = threading.Thread(target=_loop, name="dayz-mcp-run-reaper", daemon=True)
    thread.start()
    return thread


def run_daemon(config: Any) -> int:
    startup_deadline = time.monotonic() + validated_startup_budget_s()
    log = _log_sink(config)
    key = config.key if getattr(config, "key", None) is not None else read_key(_required_keyfile(config))
    port = int(getattr(config, "port", 8765))
    daemon_generation = uuid.uuid4().hex
    expected_executable = orphan_guard.full_image_path_of(os.getpid())
    if not expected_executable or not os.path.isabs(expected_executable):
        raise RuntimeError("daemon_process_image_unavailable")
    expected_argv = build_daemon_argv(config, python=sys.executable)
    expected_cwd = daemon_runtime_cwd()

    def healthy(generation: str | None = None) -> bool:
        return orphan_guard.probe_status_healthy(
            port,
            key,
            deadline=startup_deadline,
            expected_generation=generation,
            expected_executable=expected_executable,
            expected_argv=expected_argv,
            expected_cwd=expected_cwd,
        )

    # Discovery: if a healthy daemon already owns the port, do not spawn a rival.
    if healthy():
        log(f"DAEMON: a healthy daemon already owns 127.0.0.1:{port}; exiting")
        return 0
    if time.monotonic() >= startup_deadline:
        log("DAEMON: startup deadline expired during discovery")
        return DAEMON_STARTUP_CONTENDED

    paths = RuntimePaths.from_env()
    with daemon_startup_election(paths) as elected:
        if not elected:
            if healthy():
                log(f"DAEMON: elected winner owns 127.0.0.1:{port}; exiting")
                return 0
            log("DAEMON: startup election is owned but no healthy daemon is available")
            return DAEMON_STARTUP_CONTENDED

        if time.monotonic() >= startup_deadline:
            log("DAEMON: startup deadline expired during election")
            return DAEMON_STARTUP_CONTENDED

        # Re-probe while holding the global runtime-root election.  This covers a
        # winner that became healthy between discovery and lock acquisition.
        if healthy():
            log(f"DAEMON: a healthy daemon won 127.0.0.1:{port}; exiting")
            return 0

        try:
            _ensure_identity_migration_after_candidate_drain(
                config, deadline=startup_deadline
            )
        except TimeoutError:
            log("DAEMON: startup deadline expired during identity migration")
            return DAEMON_STARTUP_CONTENDED

        state = build_server_state(
            config,
            key,
            daemon_generation=daemon_generation,
            activate_coordination=False,
        )
        status_provider = make_status_provider(config, state)

        try:
            httpd = _bind_with_reclaim(
                port,
                key,
                state,
                log,
                status_provider,
                deadline=startup_deadline,
                expected_executable=expected_executable,
                expected_argv=expected_argv,
                expected_cwd=expected_cwd,
            )
        except TimeoutError:
            log("DAEMON: startup deadline expired during bind/reclaim")
            return DAEMON_STARTUP_CONTENDED
        if httpd is None:
            if healthy():
                log(
                    f"DAEMON: another healthy daemon won 127.0.0.1:{port}; exiting"
                )
                return 0
            log(
                f"DAEMON: 127.0.0.1:{port} remains live but unavailable after bind"
            )
            return DAEMON_STARTUP_CONTENDED

        try:
            _activate_server_coordination(
                state, daemon_generation, deadline=startup_deadline
            )
        except TimeoutError:
            httpd.server_close()
            log("DAEMON: startup deadline expired during coordination activation")
            return DAEMON_STARTUP_CONTENDED
        except Exception:
            httpd.server_close()
            raise

        thread = threading.Thread(
            target=httpd.serve_forever,
            kwargs={"poll_interval": 0.1},
            daemon=True,
        )
        thread.start()
        if not _status_accredits_generation(
            port,
            key,
            daemon_generation,
            deadline=startup_deadline,
            expected_executable=expected_executable,
            expected_argv=expected_argv,
            expected_cwd=expected_cwd,
        ):
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=1.0)
            log("DAEMON: status did not accredit the elected generation")
            return DAEMON_STARTUP_CONTENDED
        host, bound_port = httpd.server_address

    idle_timeout_s = float(getattr(config, "idle_timeout_s", 1800.0))
    log(f"DAEMON LISTEN host={host} port={bound_port} idle_timeout={idle_timeout_s:.0f}s")

    stop = threading.Event()

    def on_idle() -> None:
        log("DAEMON: idle (no game polls, no client requests); releasing port and exiting")
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            pass
        stop.set()

    if idle_timeout_s and idle_timeout_s > 0:
        poll_interval = min(60.0, max(5.0, idle_timeout_s / 4.0))
        orphan_guard.install_idle_watchdog(
            idle_seconds=_make_idle_seconds(state, time.monotonic()),
            timeout_s=idle_timeout_s,
            on_idle=on_idle,
            log=log,
            poll_interval=poll_interval,
            stop=stop,
        )

    if state.lifecycle is not None:
        install_run_reaper(state.lifecycle, log=log, stop=stop)

    # Block until idle shutdown or external termination. No parent-death watchdog:
    # the daemon is meant to outlive the session that spawned it.
    try:
        stop.wait()
    except KeyboardInterrupt:
        on_idle()
        return 130
    return 0


def spawn_detached(argv: list[str], *, log: Callable[[str], None] = _noop, cwd: str | None = None) -> int | None:
    """Launch ``argv`` as a detached background process that aims to survive this session.

    Returns the child pid, or None on failure. On Windows the child is detached from
    the console/process-group and tries to break away from the parent Job so Cowork/node
    closing does not kill it. If the job forbids breakaway the child stays job-bound and
    will die when the session's Job Object closes (KILL_ON_JOB_CLOSE); multi-session then
    degrades to "one owner at a time" — the next session's client re-spawns the daemon
    into its own job. Whether breakaway succeeds under Cowork is the in-vivo gate; each
    branch is logged so that gate is diagnosable rather than a black box.
    """
    base_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "cwd": cwd,
    }
    if sys.platform != "win32":
        try:
            return subprocess.Popen(argv, start_new_session=True, **base_kwargs).pid
        except OSError as exc:
            log(f"SPAWN: failed to launch daemon: {exc}")
            return None

    detached = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP
    try:
        pid = subprocess.Popen(argv, creationflags=detached | _CREATE_BREAKAWAY_FROM_JOB, **base_kwargs).pid
        log(f"SPAWN: daemon pid={pid} broke away from the job (will outlive this session)")
        return pid
    except OSError:
        # Breakaway denied (the job forbids it): the child stays IN this session's Job
        # Object. Under KILL_ON_JOB_CLOSE it dies with the session and the next client
        # re-spawns it — degraded multi-session, not the decoupled-daemon ideal. Logged
        # so the in-vivo gate can attribute a "daemon died with the session" outcome.
        log("SPAWN: CREATE_BREAKAWAY_FROM_JOB denied; daemon is job-bound and may die with this session")
    try:
        pid = subprocess.Popen(argv, creationflags=detached, **base_kwargs).pid
        log(f"SPAWN: daemon pid={pid} launched job-bound (no breakaway)")
        return pid
    except OSError as exc:
        log(f"SPAWN: failed to launch daemon: {exc}")
        return None
