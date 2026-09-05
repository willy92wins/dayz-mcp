from __future__ import annotations

import json
import ntpath
import os
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from dayz_mcp import (
    dayz_test_modes,
    dayz_test_request,
    dayz_test_worker,
    secure_launcher,
)
from dayz_mcp.launcher_registry import open_approved_launcher
from dayz_mcp.native_launcher_transaction import preflight_vpp_request
from dayz_mcp.steam_preflight import (
    REMEDIATION,
    STEAM_SESSION_STALE,
    SteamSessionResult,
    evaluate_steam_session,
)
_BRIDGE_MOD_NAMES = frozenset({"dayz_mcp", "@dayz_mcp"})
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
    async def bridge_status_payload(self) -> dict[str, object]: ...


_ProgressCallback = Callable[[str, str | None], Awaitable[None]]


def _fail(code: str) -> None:
    raise DayzTestToolError(code)


def _mode_records() -> tuple[dayz_test_modes.ModeRecord, ...]:
    """The M12 record view, read on every call. A broken view is a bad request.

    dayz_test_request._request_mode_view does the same at the parse layer. A
    ModeAuthorityError that escaped here would reach the caller as a mute
    dayz_test_failed:ValueError: its message is prose, not a token.
    """
    try:
        records = dayz_test_modes.mode_records()
    except dayz_test_modes.ModeAuthorityError:
        _fail("bad_dayz_test_request")
    return records


def _public_modes() -> frozenset[str]:
    """The modes dayz_test_run publishes. offline is a teardown role, not one.

    What "public" means belongs to M12 too, so the predicate is its accessor
    and not a comprehension repeated here.
    """
    return frozenset(dayz_test_modes.public_mode_names(_mode_records()))


def _accepted_modes() -> frozenset[str]:
    """The modes the request layer accepts: the public ones plus the roles.

    dayz_test_request gates on the same accessor (dayz_test_request.py:71,
    :297); this pre-check only names the rejection earlier, and with the
    public enum.
    """
    return frozenset(dayz_test_modes.request_mode_names(_mode_records()))


def _mode_expected_error() -> str:
    """The rejection names the public modes only, in the order they are declared.

    Naming a role here would publish a mode dayz_test_run rejects one layer
    later, the regression ficha 9d46 warned about.
    """
    return "bad_dayz_test_request:mode expected " + "|".join(
        dayz_test_modes.public_mode_names(_mode_records())
    )


def _mode_record(mode: str) -> dayz_test_modes.ModeRecord | None:
    """The record of an exact name, or None. Never a fallback record.

    execute_dayz_test_stop projects the label "stop" as its public_mode and
    that label is not a record, so callers that only need a projection get
    None instead of the exception resolve_mode would raise in the stop happy
    path. Callers that need the record itself fail closed on the None.
    """
    matches = [record for record in _mode_records() if record.name == mode]
    return matches[0] if len(matches) == 1 else None


def _mode_starts_client(mode: str) -> bool:
    """Whether this call started a client, per the record of its mode.

    A name outside the authority starts no client.
    """
    record = _mode_record(mode)
    return record is not None and record.starts_client


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
    if mode not in _accepted_modes():
        _fail(_mode_expected_error())
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
    except (TypeError, ValueError) as exc:
        token = str(exc)
        if token == dayz_test_request._INVALID_RUN_ID:
            _fail("bad_run_id")
        if token in {
            dayz_test_request._CLIENT_REQUIRES_RUN_ID,
            dayz_test_request._SERVER_ALL_FORBID_RUN_ID,
        }:
            _fail(f"bad_dayz_test_request:{token}")
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


# fb-20260904-025733-d60f: a stop whose cleanup degraded leaves the run
# UNRECONCILED with no owner, and the second dayz_test_stop was refused here,
# one layer above the lifecycle, so the only remaining route was a human
# killing processes by hand. The worker adopts before it stops
# (dayz_test_worker: adopt then stop on the kill path), and adopt now accepts
# that state, so the call has somewhere to go. EXITED and the two transient
# states stay closed.
_STOPPABLE_STATES = frozenset({"RUNNING", "RUNNING_IDLE", "UNRECONCILED"})


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
    if not never_started and run.get("state") not in _STOPPABLE_STATES:
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
    """The profile roots of an exact mode record, in the order it declares.

    This was a literal table whose else branch answered _client for every
    name it did not know: a mode whose roots the authority moved kept
    reporting the old ones, and an unknown mode reported a root it never
    wrote. An exact lookup that fails closed says so instead.
    """
    record = _mode_record(mode)
    if record is None:
        _fail(_mode_expected_error())
    return [
        ntpath.join(policy.dev_root, root, "profiles")
        for root in record.artifact_roots
    ]


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


@dataclass(frozen=True)
class LaunchReadinessProjection:
    """The single readiness contract: two independent axes and a reason.

    The incident behind this is a launch reported ready because a PID existed.
    So the axes never feed each other: ``process_alive`` comes only from
    ``_liveness_from_status``, and ``bridge_ready``/``reason`` only from the
    validated ``ready`` object of a bridge snapshot. A process that is alive but
    not polling is alive and not ready, and that has to be sayable.
    """

    process_alive: bool | None
    bridge_ready: bool | None
    reason: str | None


# Deliberately NOT an allowlist. compute_bridge_ready returns the value of
# _FENCE_BLOCK_READY for a blocked binding (server.py:373-376) on top of its
# seven literals, so the reason space is open and any closed set here would turn
# a real, informative reason into "unknown" -- the exact silence this contract
# exists to prevent. What is checked is the SHAPE: a non-empty string.
_NULL_READINESS = LaunchReadinessProjection(None, None, None)


def _project_launch_readiness(
    client_alive: bool | None, bridge_status_payload: object
) -> LaunchReadinessProjection:
    """Project a snapshot already read elsewhere. Pure, and diagnostic only.

    It takes no runtime and no broker on purpose: nothing here -- not a live
    PID, not a UDP-ready gate -- authorises ownership, adopt, stop or reap.

    A snapshot that is missing, malformed, or whose ``ready`` object does not
    typecheck leaves the bridge axis null. Promoting it from the PID would be
    the very defect this contract exists to make visible.
    """

    if not isinstance(bridge_status_payload, dict):
        return LaunchReadinessProjection(client_alive, None, None)
    ready = bridge_status_payload.get("ready")
    if not isinstance(ready, dict):
        return LaunchReadinessProjection(client_alive, None, None)
    flag = ready.get("ready")
    reason = ready.get("reason")
    if not isinstance(flag, bool) or not isinstance(reason, str) or not reason:
        return LaunchReadinessProjection(client_alive, None, None)
    return LaunchReadinessProjection(client_alive, flag, reason)


# fb-20260904-025027-8f76 (part c) asks the client to be relaunched "when the
# bridge sees client_not_polling". These are the answers the extension gate can
# give, and every one of them is published on the result. Ronda 2 added three:
# a client that is still starting is not a hung one (A4-H1), a client whose pid
# is confirmed dead is replaceable whatever its peer row says (A2-H1), and a
# peer that polls without being accredited is not a peer that does not poll
# (A3-F5).
_CLIENT_POLLING = "client_polling"
_CLIENT_NOT_POLLING = "client_not_polling"
_CLIENT_NOT_ACCREDITED = "client_not_accredited"
_CLIENT_STILL_STARTING = "client_still_starting"
_CLIENT_PROCESS_DEAD = "client_process_dead"
_CLIENT_RECORD_AGE_UNKNOWN = "client_record_age_unknown"
_BRIDGE_STATUS_UNKNOWN = "bridge_status_unknown"
_NO_CLIENT_TO_REPLACE = "no_client_to_replace"
_LIFECYCLE_STATUS_INVALID = "lifecycle_status_invalid"
CLIENT_ALREADY_POLLING = "client_already_polling"
BRIDGE_STATUS_UNKNOWN = "bridge_status_unknown"
CLIENT_STILL_STARTING = "client_still_starting"
CLIENT_RECORD_AGE_UNKNOWN = "client_record_age_unknown"
LIFECYCLE_STATUS_INVALID = "lifecycle_status_invalid"

# What a refusal publishes as error_code, and what it tells the caller to do.
# A refusal is never silent: the call did nothing, so the response has to say
# what would make it work. Every reason maps to an error_code of its OWN name:
# A5-N2 measured this table publishing error_code="bridge_status_unknown" over
# a reason of client_record_age_unknown with the bridge PERFECTLY readable,
# which is the same self-contradiction the client_not_accredited branch exists
# to remove. Reason and code now come out of the same decision.
_REFUSAL_ERROR_CODE = {
    _CLIENT_POLLING: CLIENT_ALREADY_POLLING,
    _BRIDGE_STATUS_UNKNOWN: BRIDGE_STATUS_UNKNOWN,
    _CLIENT_RECORD_AGE_UNKNOWN: CLIENT_RECORD_AGE_UNKNOWN,
    _CLIENT_STILL_STARTING: CLIENT_STILL_STARTING,
    _LIFECYCLE_STATUS_INVALID: LIFECYCLE_STATUS_INVALID,
}
_REFUSAL_REMEDIATION = {
    _CLIENT_POLLING: (
        "the registered client is polling the bridge, so there is nothing to "
        "recover and nothing was touched. Use dayz_test_stop(run_id=...) and a "
        "fresh dayz_test_run if you want to start over anyway."
    ),
    _BRIDGE_STATUS_UNKNOWN: (
        "the bridge snapshot could not be read, so a healthy client cannot be "
        "told from a hung one; nothing was touched. Retry the same call, and if "
        "it keeps failing use dayz_test_stop(run_id=...) then dayz_test_run."
    ),
    _CLIENT_RECORD_AGE_UNKNOWN: (
        "the client record does not carry a readable creation_time_utc, so its "
        "startup budget cannot be checked; nothing was touched. Retry, and if it "
        "persists use dayz_test_stop(run_id=...) then dayz_test_run."
    ),
    _LIFECYCLE_STATUS_INVALID: (
        "the lifecycle status row could not be read as a run with process "
        "records, so this call cannot tell whether there is a client to "
        "supersede; nothing was touched. Retry, and if it persists use "
        "dayz_test_stop(run_id=...) then dayz_test_run."
    ),
    _CLIENT_STILL_STARTING: (
        "the registered client is younger than the startup budget and has not "
        "polled yet - a DayZ client takes tens of seconds to reach its first "
        "poll. Wait and retry, or dayz_test_stop(run_id=...) then dayz_test_run "
        "to start over. Override the budget with "
        "DAYZ_MCP_CLIENT_START_BUDGET_S when a shorter one is known to be safe."
    ),
}

# A4-H1: a client that has not polled YET reads exactly like a client that has
# stopped polling, and the response of the call that launched it says
# client_not_polling, so the next call had a licence to kill what the previous
# one started. The record's own age closes it.
#
# SUPUESTO, a calibrar en el gate in-game L6. Measured 2026-09-04 over the 120
# client RPTs under DayZ_MCP_dev\_client\profiles: of the 102 that reach
# CreateMission(), the window from the RPT header to the mission client is
# p50 39.3 s, p90 58.7 s, p95 81.4 s, p99 187.7 s, max 344.6 s. That is a LOWER
# bound twice over - the process starts before the header is written, and the
# first poll comes after the mission exists - so the default covers the observed
# maximum with margin instead of the median. Refusing for too long costs a wait;
# refusing for too little costs a live DayZ session.
_CLIENT_START_BUDGET_S = 360.0
_CLIENT_START_BUDGET_ENV = "DAYZ_MCP_CLIENT_START_BUDGET_S"


def _client_start_budget_s() -> float:
    """The startup budget in seconds, overridable for an operator in a hurry.

    A value that is not a finite number in [0, 3600] is ignored rather than
    obeyed: an unreadable override must not silently disable the guard.
    """
    raw = os.environ.get(_CLIENT_START_BUDGET_ENV)
    if raw is None:
        return _CLIENT_START_BUDGET_S
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _CLIENT_START_BUDGET_S
    if value != value or not 0.0 <= value <= 3600.0:
        return _CLIENT_START_BUDGET_S
    return value


@dataclass(frozen=True)
class ClientRecordProjection:
    """The client role of one run, as /lifecycle/status publishes it.

    ``valid`` is the third answer next to present/absent, and it is the one the
    gate reads first: a payload whose ``processes`` cannot be vouched for tells
    us NOTHING about the client role, and Codex C-01 measured that reading it as
    "there is no client" hands the destructive branch a licence -- the lifecycle
    on the other side of the wire sees its own manifest intact, finds the client
    ``owned``, and ends it.
    """

    present: bool
    alive: bool | None
    age_s: float | None
    pid: int | None
    valid: bool = True


def _process_rows_from_status(
    status: object, run_id: str | None
) -> list[dict[str, object]] | None:
    """The process rows of one run, or None when the payload cannot be vouched for.

    Production always satisfies this: _projected_run is dataclasses.asdict over a
    RunRecord, so ``processes`` is a list of dicts and every row carries a
    non-empty ``role`` and a positive int ``pid`` (ProcessRecord.validate). A
    payload that does not is not "a run with no client": it is a payload this
    layer cannot read, and the two callers below turn that into a refusal
    instead of into an absence (Codex C-01).
    """
    if not isinstance(status, dict) or not run_id:
        return None
    runs = status.get("runs")
    if not isinstance(runs, list):
        return None
    match = next(
        (
            item
            for item in runs
            if isinstance(item, dict) and item.get("run_id") == run_id
        ),
        None,
    )
    if match is None:
        return None
    rows = match.get("processes")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            return None
        role = row.get("role")
        pid = row.get("pid")
        if not isinstance(role, str) or not role:
            return None
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return None
    return [row for row in rows]


def _client_pids_from_status(
    status: object, run_id: str | None
) -> tuple[int, ...] | None:
    """Client pids of that run in row order, or None when the payload is unreadable.

    None is NOT the empty tuple: an empty tuple says "this run has no client",
    which is a measurement, and None says "this payload cannot be read", which is
    the absence of one. _measure_replacement keeps them apart so a malformed
    snapshot never publishes client_terminated=0 over a client that did die
    (Codex C-01 measured exactly that pair).
    """
    rows = _process_rows_from_status(status, run_id)
    if rows is None:
        return None
    return tuple(
        int(row["pid"]) for row in rows if row.get("role") == "client"
    )


def _client_record_from_status(
    status: object, run_id: str | None
) -> ClientRecordProjection:
    """Project the client role: presence, liveness and record age, apart.

    A4-H2: presence is decided by the RECORD, never by its liveness. Deriving it
    from _liveness_from_status made a _pid_alive that could not answer read as
    "there is no client at all", which opened the gate on a client that was
    polling and published no_client_to_replace while superseding it. The three
    axes stay separate here, and only a liveness of exactly False shortcuts the
    gate - unknown never authorises anything.

    The age comes from creation_time_utc, which /lifecycle/status already
    publishes for every process row, parsed with the lifecycle's own parser
    instead of a second one. The import is function-local: process_lifecycle is
    a heavy module and this projection is the only thing here that needs it.
    """
    rows = _process_rows_from_status(status, run_id)
    if rows is None:
        return ClientRecordProjection(False, None, None, None, valid=False)
    record: dict[str, object] | None = None
    for proc in rows:
        if proc.get("role") == "client":
            record = proc
    if record is None:
        return ClientRecordProjection(False, None, None, None)
    from dayz_mcp.process_lifecycle import _utc_epoch

    born = _utc_epoch(record.get("creation_time_utc"))
    age = None if born is None else max(0.0, time.time() - born)
    pid = record.get("pid")
    return ClientRecordProjection(
        True,
        _pid_alive(pid),
        age,
        pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
    )


@dataclass(frozen=True)
class ClientReplacementDecision:
    """Whether this call may supersede the client already on the run, and why."""

    replace: bool
    reason: str
    last_poll_age_s: float | None
    record_age_s: float | None = None


def _peer_age(value: object) -> float | None:
    """A poll age only counts when it is a finite, non-negative number."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")) or number < 0.0:
        return None
    return number


def _peer_row_is_usable(peer: dict[str, object]) -> bool:
    """Does this peer row carry evidence at all about whether the client polls?

    Codex C-02: being a dict is not being readable. server._peer_is_live answers
    False for an EMPTY row exactly as it answers False for a peer that stopped
    polling, so with a record older than the startup budget the empty row fell
    through to client_not_polling and ended a live client. A row with no usable
    age is no answer, and no answer must not authorise a kill.

    One usable age is enough, and which one _peer_is_live consults stays its
    business: this only decides whether there is anything for it to consult.
    """
    return (
        _peer_age(peer.get("last_poll_age_s")) is not None
        or _peer_age(peer.get("bound_last_poll_age_s")) is not None
    )


def _decide_client_replacement(
    record: ClientRecordProjection, bridge_status_payload: object
) -> ClientReplacementDecision:
    """Decide from the run row and the bridge snapshot, before anything is sent.

    The witness for "this peer polls" is the CLIENT PEER ROW, never
    ``ready.reason`` on its own: compute_bridge_ready tests the server before
    the client (server.py:413 answers server_poll_stale, server.py:415 answers
    client_not_polling), so a stale server hides a client that is polling
    perfectly well, and a gate built on that reason would end a healthy process.
    ``server._peer_is_live`` (server.py:367-379, threshold ``PEER_STALE_S`` at
    server.py:112) is the authority: imported, never mirrored, and the import is
    function-local because server.py:25-31 imports this module.

    Ronda 2 changed the direction of the unknown branch. It used to replace, on
    the argument that refusing without evidence would restore the dead end of
    the ficha. It does not: that dead end is a HUNG client, and its recovery
    goes through client_not_polling, which needs a READABLE snapshot. With no
    snapshot a healthy client cannot be told from a hung one, and the asymmetry
    decides - refusing costs one repeated call, replacing costs a live DayZ
    session. So the only branches that authorise superseding a live client are
    the ones with positive evidence that it is not serving the bridge.

    Pure, like _project_launch_readiness above it: it reads snapshots fetched
    elsewhere, takes no runtime and no broker, and authorises nothing by itself.
    """
    if not record.valid:
        # Codex C-01. Nothing is known about the client role, so nothing may be
        # done to it; this is the only branch that precedes the dead-pid
        # shortcut, because even "the pid is dead" was read from this payload.
        return ClientReplacementDecision(
            False, _LIFECYCLE_STATUS_INVALID, None, None
        )
    if not record.present:
        return ClientReplacementDecision(
            True, _NO_CLIENT_TO_REPLACE, None, record.age_s
        )
    if record.alive is False:
        # A2-H1: the pid is confirmed dead, so the guard will classify the
        # record `gone` and the replacement will retire it WITHOUT terminating
        # anything. There is no live process to protect, whatever the peer row
        # still says for the next few seconds.
        return ClientReplacementDecision(
            True, _CLIENT_PROCESS_DEAD, None, record.age_s
        )
    peer = None
    if isinstance(bridge_status_payload, dict):
        candidate = bridge_status_payload.get("client_peer")
        if isinstance(candidate, dict) and _peer_row_is_usable(candidate):
            peer = candidate
    if peer is None:
        return ClientReplacementDecision(
            False, _BRIDGE_STATUS_UNKNOWN, None, record.age_s
        )
    from dayz_mcp import server

    # Context, not the verdict: _peer_is_live reads bound_last_poll_age_s
    # instead when the peer is BOUND. The verdict is always its answer, and both
    # numbers come from this one snapshot so the response cannot disagree with
    # itself.
    age = _peer_age(peer.get("last_poll_age_s"))
    if server._peer_is_live(peer):
        return ClientReplacementDecision(False, _CLIENT_POLLING, age, record.age_s)
    if record.age_s is None:
        # Fail-closed: without a readable record age the startup budget cannot
        # be checked, and a client that has never polled is indistinguishable
        # from one that stopped.
        return ClientReplacementDecision(
            False, _CLIENT_RECORD_AGE_UNKNOWN, age, None
        )
    if record.age_s < _client_start_budget_s():
        # A4-H1: it has not polled because it has not finished starting. The
        # response of the call that launched it says client_not_polling too;
        # without this branch that response is a licence to kill what it started.
        return ClientReplacementDecision(
            False, _CLIENT_STILL_STARTING, age, record.age_s
        )
    if age is not None and age < server.PEER_STALE_S:
        # A3-F5: it polled 0.2 s ago but the poll is not accredited (BOUND with
        # no accredited poll yet, AMBIGUOUS...). Publishing "does not poll" next
        # to an age of 0.2 s is a response that contradicts itself.
        return ClientReplacementDecision(
            True, _CLIENT_NOT_ACCREDITED, age, record.age_s
        )
    return ClientReplacementDecision(True, _CLIENT_NOT_POLLING, age, record.age_s)


def _measure_replacement(
    before: tuple[int, ...] | None, status: object, run_id: str
) -> tuple[int | None, bool | None]:
    """What the run row says happened to the client role, before against after.

    A3-F2 / A3-F3: the key this replaces copied ``terminal.ok``, the worker's
    global verdict, so it published true over a call that terminated nothing and
    left two client rows, and false over a call whose client was already dead.
    These two numbers come from the rows themselves.

    ``client_terminated`` counts the client records that left the row and whose
    pid no longer answers. The replacement only ever retires a record it
    terminated (`owned`) or found already dead (`gone`), so both count as "that
    client is gone"; which of the two it was is not observable from this layer,
    and the operator's question is the same either way. A pid psutil cannot
    answer for is not counted, so this is a floor, never an overcount.
    """
    if before is None or not isinstance(status, dict):
        return None, None
    after = _client_pids_from_status(status, run_id)
    if after is None:
        # An unreadable row after the call is not "nothing happened": it is no
        # measurement, and publishing 0/False over it would be a claim.
        return None, None
    retired = [pid for pid in before if pid not in after]
    terminated = sum(1 for pid in retired if _pid_alive(pid) is False)
    return terminated, any(pid not in before for pid in after)


def _compact_result(
    *,
    terminal: WorkerTerminal,
    project: str,
    mode: str,
    started_at: float,
    artifacts_paths: list[str],
    server_alive: bool | None = None,
    client_alive: bool | None = None,
    readiness: LaunchReadinessProjection | None = None,
    phase: str | None = None,
    steam_registered_pid: int | None = None,
    steam_live_pids: list[int] | None = None,
    remediation: str | None = None,
    client_terminated: int | None = None,
    client_relaunched: bool | None = None,
    client_replace_reason: str | None = None,
    client_last_poll_age_s: float | None = None,
    client_record_age_s: float | None = None,
    vpp_missing: list[str] | None = None,
    vpp_warnings: list[str] | None = None,
) -> dict[str, object]:
    projection = readiness or _NULL_READINESS
    return {
        "status": "succeeded" if terminal.ok else "failed",
        "project": project,
        "mode": mode,
        "run_id": terminal.run_id,
        "phase": phase if phase is not None else ("completed" if terminal.ok else "executing"),
        "elapsed_s": round(time.monotonic() - started_at, 3),
        "artifacts_paths": artifacts_paths,
        "error_code": terminal.error_code,
        "cleanup_degraded": terminal.cleanup_degraded,
        "server_alive": server_alive,
        "client_alive": client_alive,
        "process_alive": projection.process_alive,
        "bridge_ready": projection.bridge_ready,
        "reason": projection.reason,
        "steam_registered_pid": steam_registered_pid,
        "steam_live_pids": steam_live_pids,
        "remediation": remediation,
        # Always present, null included: a key that shows up only sometimes is a
        # key a consumer cannot rely on. null means this call never looked at the
        # client role of a run. The pair (client_terminated, client_relaunched)
        # is measured from the run rows before and after, not inferred from the
        # worker's verdict, so "the client died and was not relaunched" is
        # sayable even when the sealed worker collapses the reason.
        "client_terminated": client_terminated,
        "client_relaunched": client_relaunched,
        "client_replace_reason": client_replace_reason,
        "client_last_poll_age_s": client_last_poll_age_s,
        "client_record_age_s": client_record_age_s,
        # The VPP admin-tools gate (ficha df93). An empty list is a
        # MEASUREMENT: the gate ran here and found nothing. null means this
        # layer never consulted it -- a stop, whose request is offline and
        # therefore starts no server, and every envelope built before the
        # gate. The enforcement itself is not optional: it runs inside
        # native_launcher_transaction for every launch, named or not.
        "vpp_missing": vpp_missing,
        "vpp_warnings": vpp_warnings,
    }


def _validate_terminal_context(
    terminal: WorkerTerminal,
    *,
    preflight: bool,
    expected_run_id: str | None,
) -> None:
    if not terminal.ok:
        return
    if expected_run_id is not None:
        if terminal.run_id != expected_run_id:
            _fail("terminal_invalid")
        return
    if preflight != (terminal.run_id is None):
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
    replacement: ClientReplacementDecision | None = None,
    client_pids_before: tuple[int, ...] | None = None,
    vpp: object | None = None,
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
    readiness: LaunchReadinessProjection | None = None
    client_terminated: int | None = None
    client_relaunched: bool | None = None
    status: object = None
    # The row is read back on a FAILED call too when this was a replacement:
    # that is the port_still_held case, where the client is already dead and the
    # sealed worker collapses the reason to worker_failed. Without this read the
    # response could not say the client is gone (A3-F3).
    if not preflight and terminal.run_id and (terminal.ok or replacement is not None):
        try:
            status = await runtime.lifecycle_status()
        except Exception:
            status = None
    if replacement is not None and terminal.run_id:
        client_terminated, client_relaunched = _measure_replacement(
            client_pids_before, status, terminal.run_id
        )
    if terminal.ok and not preflight and terminal.run_id:
        server_alive, client_alive = _liveness_from_status(status, terminal.run_id)
        if _mode_starts_client(public_mode):
            try:
                snapshot = await runtime.bridge_status_payload()
            except Exception:
                # A snapshot we could not read is not a bridge that is not
                # ready; it is no answer, and it says so.
                snapshot = None
            readiness = _project_launch_readiness(client_alive, snapshot)
        # Same projection as the readiness branch above: a call that started
        # a client and lost it failed, whatever the mode is called. The
        # literal set it replaces omitted client -- the one mode whose whole
        # job is the client -- and named offline, a role that never reaches
        # this function. preflight returns before this block; a stop does
        # reach it and starts no client, so it is never accused of one.
        if _mode_starts_client(public_mode) and client_alive is False:
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
        readiness=readiness,
        client_terminated=client_terminated,
        client_relaunched=client_relaunched,
        client_replace_reason=None if replacement is None else replacement.reason,
        client_last_poll_age_s=(
            None if replacement is None else replacement.last_poll_age_s
        ),
        client_record_age_s=(
            None if replacement is None else replacement.record_age_s
        ),
        vpp_missing=None if vpp is None else list(vpp.missing),
        vpp_warnings=None if vpp is None else list(vpp.warnings),
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
    if mode not in _public_modes():
        _fail(_mode_expected_error())
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
            # The admin-tools gate runs before the host gate below: it is a
            # property of the request just composed, and a refusal here has
            # consulted neither Steam nor the lifecycle. It applies to
            # preflight:true too -- dayz_test_worker.py:547-550 states the rule
            # this route must keep: a preflight fails exactly where a real
            # launch would. native_launcher_transaction enforces the same
            # decision below; this call only names it for the caller.
            vpp = preflight_vpp_request(
                raw_request, sealed_policies=bundle.sealed_policies
            )
            if vpp.error_code is not None:
                return _compact_result(
                    terminal=WorkerTerminal(
                        cleanup_degraded=False,
                        error_code=vpp.error_code,
                        exit_code=1,
                        ok=False,
                        run_id=None,
                    ),
                    project=policy.mod,
                    mode=mode,
                    started_at=started_at,
                    artifacts_paths=[],
                    phase="validating",
                    remediation=vpp.hint,
                    vpp_missing=list(vpp.missing),
                    vpp_warnings=list(vpp.warnings),
                )
            if not preflight and _mode_starts_client(mode):
                try:
                    steam = evaluate_steam_session()
                except Exception:
                    steam = SteamSessionResult(
                        error_code=STEAM_SESSION_STALE,
                        steam_registered_pid=None,
                        steam_live_pids=(),
                        remediation=REMEDIATION,
                    )
                if steam.error_code is not None:
                    return _compact_result(
                        terminal=WorkerTerminal(
                            cleanup_degraded=False,
                            error_code=steam.error_code,
                            exit_code=1,
                            ok=False,
                            run_id=None,
                        ),
                        project=policy.mod,
                        mode=mode,
                        started_at=started_at,
                        artifacts_paths=[],
                        phase="validating",
                        steam_registered_pid=steam.steam_registered_pid,
                        steam_live_pids=list(steam.steam_live_pids[:8]),
                        remediation=steam.remediation,
                        vpp_missing=list(vpp.missing),
                        vpp_warnings=list(vpp.warnings),
                    )
            replacement: ClientReplacementDecision | None = None
            client_pids_before: tuple[int, ...] | None = None
            if run_id is not None and not preflight:
                extension_status = await runtime.lifecycle_status()
                require_extension_run(extension_status, policy, run_id)
                if _mode_starts_client(mode):
                    # Relaunching this role supersedes the client already on the
                    # run (the role replacement inside start_run). The caller
                    # does not get to end a healthy client: this gate decides,
                    # here, before the sealed request is composed and before any
                    # process is touched.
                    server_alive, _liveness = _liveness_from_status(
                        extension_status, run_id
                    )
                    record = _client_record_from_status(extension_status, run_id)
                    client_pids_before = _client_pids_from_status(
                        extension_status, run_id
                    )
                    try:
                        bridge = await runtime.bridge_status_payload()
                    except Exception:
                        # A snapshot we could not read is no answer. It used to
                        # mean "replace"; ronda 2 made it a refusal, because a
                        # transport hiccup is not evidence that a client is hung.
                        bridge = None
                    replacement = _decide_client_replacement(record, bridge)
                    if not replacement.replace:
                        return _compact_result(
                            terminal=WorkerTerminal(
                                cleanup_degraded=False,
                                error_code=_REFUSAL_ERROR_CODE.get(
                                    replacement.reason, CLIENT_ALREADY_POLLING
                                ),
                                exit_code=1,
                                ok=False,
                                run_id=run_id,
                            ),
                            project=policy.mod,
                            mode=mode,
                            started_at=started_at,
                            artifacts_paths=_artifact_paths(policy, mode),
                            phase="validating",
                            server_alive=server_alive,
                            client_alive=record.alive,
                            remediation=_REFUSAL_REMEDIATION.get(replacement.reason),
                            # 0/False is a MEASUREMENT (the row was read and
                            # nothing happened to it). When the row could not be
                            # read at all there is no measurement to publish.
                            client_terminated=(
                                None
                                if replacement.reason == _LIFECYCLE_STATUS_INVALID
                                else 0
                            ),
                            client_relaunched=(
                                None
                                if replacement.reason == _LIFECYCLE_STATUS_INVALID
                                else False
                            ),
                            client_replace_reason=replacement.reason,
                            client_last_poll_age_s=replacement.last_poll_age_s,
                            client_record_age_s=replacement.record_age_s,
                            vpp_missing=list(vpp.missing),
                            vpp_warnings=list(vpp.warnings),
                        )
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
                replacement=replacement,
                client_pids_before=client_pids_before,
                vpp=vpp,
            )


def _run_row(status: object, run_id: str) -> dict[str, object] | None:
    if not isinstance(status, dict) or not isinstance(status.get("runs"), list):
        return None
    matches = [
        item
        for item in status["runs"]
        if isinstance(item, dict) and item.get("run_id") == run_id
    ]
    return matches[0] if len(matches) == 1 else None


def _retired_for(status: object, run_id: str) -> list[dict[str, object]]:
    if not isinstance(status, dict):
        return []
    raw = status.get("retired_run_diagnostics")
    if not isinstance(raw, list):
        return []
    return [
        item
        for item in raw
        if isinstance(item, dict) and item.get("run_id") == run_id
    ]


_GENERATION_FIELDS = (
    "daemon_generation_at_launch",
    "daemon_generation_current",
    "generation_changed",
)
_DIAGNOSTIC_FIELDS = (
    "run_id",
    *_GENERATION_FIELDS,
    "event",
    "reason",
    "decision",
    "state",
)


def _validated_generation(source: object) -> dict[str, object] | None:
    if not isinstance(source, dict):
        return None
    if any(field not in source for field in _GENERATION_FIELDS):
        return None
    launch = source["daemon_generation_at_launch"]
    current = source["daemon_generation_current"]
    changed = source["generation_changed"]
    if launch is not None and not isinstance(launch, str):
        return None
    if not isinstance(current, str):
        return None
    if changed is not None and not isinstance(changed, bool):
        return None
    return {
        "daemon_generation_at_launch": launch,
        "daemon_generation_current": current,
        "generation_changed": changed,
    }


def _validated_diagnostic(item: object, run_id: str) -> dict[str, object] | None:
    if not isinstance(item, dict) or set(item) != set(_DIAGNOSTIC_FIELDS):
        return None
    if item.get("run_id") != run_id or not isinstance(item.get("run_id"), str):
        return None
    if item.get("state") != "EXITED":
        return None
    for key in ("event", "reason", "decision"):
        value = item.get(key)
        if not isinstance(value, str) or not value:
            return None
    generations = _validated_generation(item)
    if generations is None:
        return None
    return {field: item[field] for field in _DIAGNOSTIC_FIELDS}


def _copy_generation(source: dict[str, object]) -> dict[str, object]:
    copied = _validated_generation(source)
    if copied is None:
        raise KeyError("generation")
    return copied


def _failed_stop_envelope(
    run_id: str, error_code: str, source: dict[str, object]
) -> dict[str, object]:
    return {
        "status": "failed",
        "run_id": run_id,
        "error_code": error_code,
        **_copy_generation(source),
    }


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
            status_snapshot = await runtime.lifecycle_status()
            try:
                policy, run = resolve_stop_run(
                    status_snapshot, bundle.sealed_policies, run_id
                )
            except DayzTestToolError as exc:
                if exc.code == "run_not_active":
                    row = _run_row(status_snapshot, run_id)
                    if _validated_generation(row) is None:
                        raise
                    return _failed_stop_envelope(run_id, "run_not_active", row)
                if exc.code == "run_not_found":
                    hits = _retired_for(status_snapshot, run_id)
                    if len(hits) != 1:
                        raise
                    diagnostic = _validated_diagnostic(hits[0], run_id)
                    if diagnostic is None:
                        raise
                    return _failed_stop_envelope(run_id, "run_not_found", diagnostic)
                raise
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
