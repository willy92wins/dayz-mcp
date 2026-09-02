from __future__ import annotations

import json
import ntpath
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from dayz_mcp import dayz_test_request, dayz_test_worker, secure_launcher
from dayz_mcp.launcher_registry import open_approved_launcher


_MISSION_ALIASES = frozenset({"chernarus", "livonia", "sakhal", "lfheli"})
_BRIDGE_MOD_NAMES = frozenset({"dayz_mcp", "@dayz_mcp"})
_PUBLIC_MODES = frozenset({"server", "all", "client"})
_ACCEPTED_MODES = _PUBLIC_MODES | {"offline"}
_HELD_LEASE_RUN = (
    "session_transition_conflict: release your session lease first - "
    "dayz_test_run manages its own lease internally"
)
_HELD_LEASE_STOP = (
    "session_transition_conflict: release your session lease first - "
    "dayz_test_stop manages its own lease internally"
)
_TRANSITION_IN_FLIGHT = (
    "session_transition_conflict: a session transition is in flight"
)
_TERMINAL_KEYS = frozenset(
    {"cleanup_degraded", "error_code", "exit_code", "ok", "run_id"}
)


class DayzTestToolError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class WorkerTerminal:
    cleanup_degraded: bool
    error_code: str | None
    exit_code: int
    ok: bool
    run_id: str | None


class _Runtime(Protocol):
    active_lease_token: str | None
    active_ticket: str | None
    active_operation_id: str | None
    daemon_policy: object

    async def lifecycle_status(self) -> dict[str, object]: ...
    async def reconcile_idle_session(self) -> dict[str, object]: ...


_ProgressCallback = Callable[[str, str | None], Awaitable[None]]


def _fail(code: str) -> None:
    raise DayzTestToolError(code)


def _semantic_policies(
    sealed_policies: tuple[object, ...],
) -> tuple[dayz_test_request.RequestProjectPolicy, ...]:
    if type(sealed_policies) is not tuple:
        _fail("launcher_policy_invalid")
    policies = tuple(getattr(item, "policy", None) for item in sealed_policies)
    if not policies or any(
        type(item) is not dayz_test_request.RequestProjectPolicy for item in policies
    ):
        _fail("launcher_policy_invalid")
    return policies  # type: ignore[return-value]


def _selected_policy(
    sealed_policies: tuple[object, ...], project: object
) -> dayz_test_request.RequestProjectPolicy:
    policies = _semantic_policies(sealed_policies)
    matches = [item for item in policies if item.mod == project]
    if len(matches) != 1:
        _fail("bad_project")
    return matches[0]


def _valid_public_mod(value: object, roots: tuple[str, ...]) -> bool:
    # Same contract as dayz_test_request._valid_mod_entry: a relative folder
    # name, or an absolute path that falls inside the selected mod_roots.
    return dayz_test_request._valid_mod_entry(value, roots)


def _public_mod_list(
    value: list[str] | None, roots: tuple[str, ...]
) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or any(
        not _valid_public_mod(item, roots) for item in value
    ):
        _fail("bad_mod")
    return list(value)


def build_run_request(
    sealed_policies: tuple[object, ...],
    *,
    project: str,
    mode: str,
    mission: str = "chernarus",
    build: bool = False,
    clean: bool = False,
    pack_only: bool = False,
    preflight: bool = False,
    run_id: str | None = None,
    extra_mods: list[str] | None = None,
    base_mods: list[str] | None = None,
    server_mods: list[str] | None = None,
    no_base_mods: bool = False,
    no_file_patching: bool = False,
    port: int = 2302,
    width: int = 1920,
    height: int = 1080,
    player_name: str = "Dev",
    server_wait_s: int = 60,
    kill: bool = False,
) -> tuple[bytes, dayz_test_request.RequestProjectPolicy]:
    selected = _selected_policy(sealed_policies, project)
    if mode not in _ACCEPTED_MODES:
        _fail("bad_dayz_test_request:mode expected server|all|client")
    if mission not in _MISSION_ALIASES:
        _fail("bad_mission")
    public_extra = _public_mod_list(extra_mods, selected.mod_roots)
    public_base = _public_mod_list(base_mods, selected.mod_roots)
    public_server = _public_mod_list(server_mods, selected.mod_roots)
    document: dict[str, object] = {
        "build": build,
        "clean": clean,
        "dev_root": selected.dev_root,
        "height": height,
        "kill": kill,
        "mission": mission,
        "mod": selected.mod,
        "mode": mode,
        "no_base_mods": no_base_mods,
        "no_file_patching": no_file_patching,
        "pack_only": pack_only,
        "player_name": player_name,
        "port": port,
        "preflight": preflight,
        "run_id": run_id,
        "server_wait_s": server_wait_s,
        "version": 1,
        "width": width,
    }
    if public_extra is not None:
        document["extra_mods"] = public_extra
    if public_base is not None:
        document["base_mods"] = public_base
    if public_server is not None:
        document["server_mods"] = public_server
    raw = json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        parsed = dayz_test_request.parse_dayz_test_request(
            raw, policies=_semantic_policies(sealed_policies)
        )
    except (TypeError, ValueError):
        _fail("bad_dayz_test_request")
    effective_mods = [selected.mod, *(public_extra or [])]
    if not kill and not any(
        ntpath.basename(mod).casefold() in _BRIDGE_MOD_NAMES
        for mod in effective_mods
    ):
        _fail("bridge_mod_missing: add extra_mods=['@DayZ_MCP']")
    return parsed.canonical_bytes, selected


def _runs(status: object) -> list[dict[str, object]]:
    if not isinstance(status, dict) or not isinstance(status.get("runs"), list):
        _fail("lifecycle_status_invalid")
    values = status["runs"]
    if any(not isinstance(item, dict) for item in values):
        _fail("lifecycle_status_invalid")
    return values  # type: ignore[return-value]


def _exact_run(status: object, run_id: object) -> dict[str, object]:
    if not _valid_uuid4(run_id):
        _fail("bad_run_id")
    matches = [item for item in _runs(status) if item.get("run_id") == run_id]
    if len(matches) != 1:
        _fail("run_not_found")
    return matches[0]


def require_extension_run(
    status: object,
    selected_policy: dayz_test_request.RequestProjectPolicy,
    run_id: str,
) -> dict[str, object]:
    run = _exact_run(status, run_id)
    if run.get("state") != "RUNNING_IDLE":
        _fail("run_not_extensible")
    if run.get("mod") != "@" + selected_policy.mod:
        _fail("run_project_mismatch")
    return run


def resolve_stop_run(
    status: object,
    sealed_policies: tuple[object, ...],
    run_id: str,
) -> tuple[dayz_test_request.RequestProjectPolicy, dict[str, object]]:
    run = _exact_run(status, run_id)
    never_started = (
        run.get("state") == "EXITED" and run.get("launch_acknowledged") is False
    )
    if not never_started and run.get("state") not in {"RUNNING", "RUNNING_IDLE"}:
        _fail("run_not_active")
    policies = _semantic_policies(sealed_policies)
    matches = [item for item in policies if run.get("mod") == "@" + item.mod]
    if len(matches) != 1:
        _fail("run_project_unapproved")
    return matches[0], run


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail("terminal_invalid")
        result[key] = value
    return result


def _constant(_value: str) -> object:
    _fail("terminal_invalid")


def _valid_uuid4(value: object) -> bool:
    if not isinstance(value, str) or value != value.casefold():
        return False
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError):
        return False
    return parsed.version == 4 and str(parsed) == value


def parse_worker_terminal(
    stdout: bytes, stderr: bytes, process_exit_code: int
) -> WorkerTerminal:
    if (
        type(stdout) is not bytes
        or not 1 <= len(stdout) <= 4096
        or type(stderr) is not bytes
        or stderr
        or type(process_exit_code) is not int
        or not 0 <= process_exit_code <= 255
    ):
        _fail("terminal_invalid")
    try:
        value = json.loads(
            stdout.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_constant,
        )
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (UnicodeError, ValueError, TypeError):
        _fail("terminal_invalid")
    if (
        not isinstance(value, dict)
        or set(value) != _TERMINAL_KEYS
        or canonical != stdout
        or type(value.get("cleanup_degraded")) is not bool
        or type(value.get("ok")) is not bool
        or type(value.get("exit_code")) is not int
        or value.get("exit_code") != process_exit_code
    ):
        _fail("terminal_invalid")
    cleanup_degraded = value["cleanup_degraded"]
    error_code = value["error_code"]
    exit_code = value["exit_code"]
    ok = value["ok"]
    run_id = value["run_id"]
    valid_run = run_id is None or _valid_uuid4(run_id)
    if not valid_run:
        _fail("terminal_invalid")
    if ok:
        if exit_code != 0 or error_code is not None or cleanup_degraded:
            _fail("terminal_invalid")
    elif (
        not 1 <= exit_code <= 255
        or error_code not in dayz_test_worker.WORKER_ERROR_CODES
        or cleanup_degraded
        and run_id is None
        or not cleanup_degraded
        and run_id is not None
    ):
        _fail("terminal_invalid")
    return WorkerTerminal(
        cleanup_degraded=cleanup_degraded,
        error_code=error_code,
        exit_code=exit_code,
        ok=ok,
        run_id=run_id,
    )


def _is_session_transition_conflict(error: BaseException) -> bool:
    return getattr(error, "code", None) == "session_transition_conflict" or str(
        error
    ) == "session_transition_conflict"


async def _require_idle_session(runtime: _Runtime, *, tool: str) -> None:
    local_busy = any(
        getattr(runtime, name, None) is not None
        for name in ("active_lease_token", "active_ticket", "active_operation_id")
    )
    held_lease = getattr(runtime, "active_lease_token", None)
    has_held_lease = isinstance(held_lease, str) and bool(held_lease)
    if local_busy:
        try:
            await runtime.reconcile_idle_session()
        except Exception as error:
            if _is_session_transition_conflict(error):
                if has_held_lease:
                    _fail(
                        _HELD_LEASE_STOP
                        if tool == "dayz_test_stop"
                        else _HELD_LEASE_RUN
                    )
                _fail(_TRANSITION_IN_FLIGHT)
            raise
    if any(
        getattr(runtime, name, None) is not None
        for name in ("active_lease_token", "active_ticket", "active_operation_id")
    ):
        _fail("session_busy")


def _artifact_paths(
    policy: dayz_test_request.RequestProjectPolicy, mode: str
) -> list[str]:
    if mode == "server":
        roots = ("_server",)
    elif mode == "all":
        roots = ("_server", "_client")
    else:
        roots = ("_client",)
    return [ntpath.join(policy.dev_root, root, "profiles") for root in roots]


def list_project_names() -> dict[str, object]:
    """Approved `dayz_test_run` project names. No host paths or file ids.

    The sealed policy is a build artifact of build_native_launcher.py and is
    gitignored, so a clone does not have it until that build runs.
    FileNotFoundError is that absence (launcher_policy_missing). A present
    file that cannot be read or parsed, or a document that is not an object,
    stays launcher_policy_invalid.
    """
    path = (
        Path(__file__).resolve().parents[1]
        / "native-launchers"
        / "dayz-test-v1"
        / "request-policy.json"
    )
    try:
        data = json.loads(path.read_bytes().decode("utf-8-sig"))
    except FileNotFoundError:
        # FileNotFoundError is an OSError; it must be caught first so a
        # clone that has not built the launcher is not reported as invalid.
        _fail("launcher_policy_missing")
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("launcher_policy_invalid")
    projects: list[dict[str, str]] = []
    if not isinstance(data, dict):
        _fail("launcher_policy_invalid")
    for item in data.get("projects") or []:
        if isinstance(item, dict) and isinstance(item.get("mod"), str) and item["mod"]:
            projects.append({"name": item["mod"]})
    return {"projects": projects}


def _pid_alive(pid: object) -> bool | None:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        import psutil

        return bool(psutil.pid_exists(pid))
    except Exception:
        return None


def _liveness_from_status(
    status: object, run_id: str | None
) -> tuple[bool | None, bool | None]:
    if not isinstance(status, dict) or not run_id:
        return None, None
    runs = [item for item in (status.get("runs") or []) if isinstance(item, dict)]
    match = next((item for item in runs if item.get("run_id") == run_id), None)
    if match is None:
        return None, None
    server_alive: bool | None = None
    client_alive: bool | None = None
    for proc in match.get("processes") or []:
        if not isinstance(proc, dict):
            continue
        role = proc.get("role")
        alive = _pid_alive(proc.get("pid"))
        if role == "server":
            server_alive = alive
        elif role == "client":
            client_alive = alive
    return server_alive, client_alive


def _compact_result(
    *,
    terminal: WorkerTerminal,
    project: str,
    mode: str,
    started_at: float,
    artifacts_paths: list[str],
    server_alive: bool | None = None,
    client_alive: bool | None = None,
) -> dict[str, object]:
    return {
        "status": "succeeded" if terminal.ok else "failed",
        "project": project,
        "mode": mode,
        "run_id": terminal.run_id,
        "phase": "completed" if terminal.ok else "executing",
        "elapsed_s": round(time.monotonic() - started_at, 3),
        "artifacts_paths": artifacts_paths,
        "error_code": terminal.error_code,
        "cleanup_degraded": terminal.cleanup_degraded,
        "server_alive": server_alive,
        "client_alive": client_alive,
    }


def _validate_terminal_context(
    terminal: WorkerTerminal,
    *,
    preflight: bool,
    expected_run_id: str | None,
) -> None:
    if not terminal.ok:
        return
    if preflight != (terminal.run_id is None):
        _fail("terminal_invalid")
    if expected_run_id is not None and terminal.run_id != expected_run_id:
        _fail("terminal_invalid")


def _stop_artifacts(
    policy: dayz_test_request.RequestProjectPolicy,
    run: dict[str, object],
) -> list[str]:
    profiles = run.get("profiles")
    if profiles is None:
        return []
    if not isinstance(profiles, str) or not profiles:
        _fail("lifecycle_status_invalid")
    normalized = ntpath.normcase(ntpath.normpath(profiles))
    matches = [
        candidate
        for candidate in _artifact_paths(policy, "all")
        if ntpath.normcase(ntpath.normpath(candidate)) == normalized
    ]
    if len(matches) != 1:
        _fail("lifecycle_status_invalid")
    return matches


async def _execute_request(
    runtime: _Runtime,
    *,
    opened_launcher: object,
    verified_bundle: object,
    raw_request: bytes,
    policy: dayz_test_request.RequestProjectPolicy,
    public_mode: str,
    artifacts_paths: list[str],
    started_at: float,
    preflight: bool,
    expected_run_id: str | None,
    progress_cb: _ProgressCallback | None,
) -> dict[str, object]:
    stdout = bytearray()
    stderr = bytearray()
    queued_reported = False

    async def report(stage: str, message: str | None = None) -> None:
        if progress_cb is not None:
            await progress_cb(stage, message)

    async def report_queued(
        _progress: float, _total: float | None, message: str | None
    ) -> None:
        nonlocal queued_reported
        queued_reported = True
        await report("queued", message)

    async def report_executing() -> None:
        nonlocal queued_reported
        if not queued_reported:
            queued_reported = True
            await report("queued", "En cola")
        await report("executing", None)

    def capture(channel: str, chunk: bytes) -> None:
        if channel not in {"stdout", "stderr"} or type(chunk) is not bytes:
            _fail("terminal_invalid")
        target = stdout if channel == "stdout" else stderr
        # parse_worker_terminal accepts 1..4096 bytes. One extra byte is
        # the overflow sentinel that trips that length check. Truncating
        # at 4096 would hide overflow from it. Valid terminals are far
        # smaller than the cap, so 4097 is never acceptance.
        if len(target) <= 4096:
            target.extend(chunk[: 4097 - len(target)])

    exit_code = await secure_launcher.execute_secure_launcher_request(
        raw_request,
        opened_launcher=opened_launcher,
        verified_bundle=verified_bundle,
        control_client=runtime,
        daemon_policy=runtime.daemon_policy,
        output_sink=capture,
        max_wait_s=None,
        queue_progress_cb=report_queued,
        execution_started_cb=report_executing,
    )
    await report("finalizing", None)
    terminal = parse_worker_terminal(bytes(stdout), bytes(stderr), exit_code)
    _validate_terminal_context(
        terminal, preflight=preflight, expected_run_id=expected_run_id
    )
    if not terminal.ok and terminal.error_code == "worker_failed":
        try:
            failed_status = await runtime.lifecycle_status()
        except Exception:
            failed_status = None
        if isinstance(failed_status, dict):
            reason = failed_status.get("last_start_error")
            if type(reason) is str and reason:
                terminal = replace(terminal, error_code=reason)
    server_alive: bool | None = None
    client_alive: bool | None = None
    if terminal.ok and not preflight and terminal.run_id:
        try:
            status = await runtime.lifecycle_status()
        except Exception:
            status = None
        server_alive, client_alive = _liveness_from_status(status, terminal.run_id)
        if public_mode in {"all", "offline"} and client_alive is False:
            terminal = replace(
                terminal,
                ok=False,
                error_code="client_dead_after_ack",
                exit_code=1,
            )
    return _compact_result(
        terminal=terminal,
        project=policy.mod,
        mode=public_mode,
        started_at=started_at,
        artifacts_paths=artifacts_paths,
        server_alive=server_alive,
        client_alive=client_alive,
    )


async def execute_dayz_test_run(
    runtime: _Runtime,
    *,
    project: str,
    mode: str,
    mission: str = "chernarus",
    build: bool = False,
    clean: bool = False,
    pack_only: bool = False,
    preflight: bool = False,
    run_id: str | None = None,
    extra_mods: list[str] | None = None,
    base_mods: list[str] | None = None,
    server_mods: list[str] | None = None,
    no_base_mods: bool = False,
    no_file_patching: bool = False,
    port: int = 2302,
    width: int = 1920,
    height: int = 1080,
    player_name: str = "Dev",
    server_wait_s: int = 60,
    progress_cb: _ProgressCallback | None = None,
) -> dict[str, object]:
    started_at = time.monotonic()
    if progress_cb is not None:
        await progress_cb("validating", None)
    if mode not in _PUBLIC_MODES:
        _fail("bad_dayz_test_request:mode expected server|all|client")
    await _require_idle_session(runtime, tool="dayz_test_run")
    with open_approved_launcher("dayz-test-v1") as opened:
        opened.validate_native_pe()
        with secure_launcher.load_verified_bundle(opened) as bundle:
            raw_request, policy = build_run_request(
                bundle.sealed_policies,
                project=project,
                mode=mode,
                mission=mission,
                build=build,
                clean=clean,
                pack_only=pack_only,
                preflight=preflight,
                run_id=run_id,
                extra_mods=extra_mods,
                base_mods=base_mods,
                server_mods=server_mods,
                no_base_mods=no_base_mods,
                no_file_patching=no_file_patching,
                port=port,
                width=width,
                height=height,
                player_name=player_name,
                server_wait_s=server_wait_s,
            )
            if run_id is not None:
                require_extension_run(await runtime.lifecycle_status(), policy, run_id)
            return await _execute_request(
                runtime,
                opened_launcher=opened,
                verified_bundle=bundle,
                raw_request=raw_request,
                policy=policy,
                public_mode=mode,
                artifacts_paths=_artifact_paths(policy, mode),
                started_at=started_at,
                preflight=preflight,
                expected_run_id=run_id,
                progress_cb=progress_cb,
            )


async def execute_dayz_test_stop(
    runtime: _Runtime,
    run_id: str,
    *,
    progress_cb: _ProgressCallback | None = None,
) -> dict[str, object]:
    started_at = time.monotonic()
    if progress_cb is not None:
        await progress_cb("validating", None)
    await _require_idle_session(runtime, tool="dayz_test_stop")
    with open_approved_launcher("dayz-test-v1") as opened:
        opened.validate_native_pe()
        with secure_launcher.load_verified_bundle(opened) as bundle:
            policy, run = resolve_stop_run(
                await runtime.lifecycle_status(), bundle.sealed_policies, run_id
            )
            raw_request, _selected = build_run_request(
                bundle.sealed_policies,
                project=policy.mod,
                mode="offline",
                run_id=run_id,
                kill=True,
            )
            result = await _execute_request(
                runtime,
                opened_launcher=opened,
                verified_bundle=bundle,
                raw_request=raw_request,
                policy=policy,
                public_mode="stop",
                artifacts_paths=_stop_artifacts(policy, run),
                started_at=started_at,
                preflight=False,
                expected_run_id=run_id,
                progress_cb=progress_cb,
            )
            if (
                result.get("status") == "failed"
                and result.get("error_code") == "run_stop_failed"
                and result.get("run_id") == run_id
            ):
                try:
                    stopped = _exact_run(await runtime.lifecycle_status(), run_id)
                except Exception:
                    return result
                if stopped.get("state") == "EXITED":
                    result.update(
                        status="succeeded",
                        phase="completed",
                        error_code=None,
                        cleanup_degraded=False,
                    )
            return result
