from __future__ import annotations

import asyncio
import json
import math
import ntpath
import os
import re
import time
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Awaitable, Callable, Iterator, Protocol

from dayz_mcp import (
    dayz_test_attestation,
    dayz_test_modes,
    dayz_test_request,
    dayz_test_storage,
    dayz_test_worker,
    process_lifecycle,
    request_path_authority,
    secure_launcher,
)
from dayz_mcp.launcher_registry import open_approved_launcher
from dayz_mcp.native_launcher_transaction import preflight_vpp_request
from dayz_mcp.peer_liveness import PEER_STALE_S
from dayz_mcp.client_steam_bootstrap import (
    ClientDumpBaseline,
    baseline_to_wire,
    diagnose_client_steam_bootstrap,
    diagnose_retired_client_death,
    snapshot_client_dumps,
)
from dayz_mcp.steam_preflight import (
    REMEDIATION,
    STEAM_SESSION_STALE,
    SteamSessionResult,
    evaluate_steam_session,
)


def evaluate_prerun_desktop(timeout_s: float | None = None):
    """Host desktop unlock + brightness probe before a client-starting run.

    fb-20260918-134756-05a0 / c0e5: refuse a capture tandem when the
    interactive desktop is locked, the framebuffer is all-black, or (on
    Windows) the brightness probe fails or times out.
    """
    import mcp_capture

    if timeout_s is None:
        return mcp_capture.run_prerun_desktop_gate()
    return mcp_capture.run_prerun_desktop_gate(timeout_s=timeout_s)


_BRIDGE_MOD_NAMES = frozenset({"dayz_mcp", "@dayz_mcp"})
# fb-20260909-213257-49a9: the token stays the prefix so existing matchers
# keep working; the suffix names the accepted extra_mods form.
_BAD_MOD = (
    "bad_mod: extra_mods/base_mods/server_mods entries must be a single "
    "folder name such as '@DayZ_MCP', or an absolute path inside the "
    "project's mod_roots; relative paths with '\\' or '/' are rejected"
)
# fb-20260925-233943-9ccc. The folder name the default writes. Presence uses
# the same basename comparison as the bridge check below (_BRIDGE_MOD_NAMES).
_DEFAULT_BRIDGE_EXTRA = "@DayZ_MCP"
# A candidate the sealed parser would refuse is not committed. The call keeps
# bridge_mod_missing instead of turning into bad_dayz_test_request.
_SEALED_REQUEST_REJECTS_BRIDGE_DEFAULT = frozenset(
    {
        "bad_dayz_test_request:mod_list_invalid",
        "bad_dayz_test_request:payload_too_large",
        "bad_dayz_test_request:raw_envelope_invalid",
    }
)
_BRIDGE_MOD_MISSING = (
    "bridge_mod_missing: add extra_mods=['@DayZ_MCP'] "
    "(the folder name '@DayZ_MCP' must be explicit in extra_mods or as "
    "the project mod; base_mods and server_mods do not count). "
    "dayz_test_run appends '@DayZ_MCP' to extra_mods by default; "
    "this call kept bridge_mod_missing because the sealed request "
    "would reject that appended entry"
)
# The bridge folder is already on the effective base_mods list, so the
# default is not appended (a second -mod= entry). The sealed parser
# rejected nothing on this path.
_BRIDGE_MOD_MISSING_IN_BASE_MODS = (
    "bridge_mod_missing: the bridge is already in base_mods, which does not "
    "count (base_mods and server_mods do not satisfy the bridge check). "
    "Move '@DayZ_MCP' from the effective base_mods to extra_mods "
    "(extra_mods=['@DayZ_MCP']). When that base list is the policy default, "
    "pass an explicit base_mods without the bridge. dayz_test_run does not "
    "copy a base_mods entry into extra_mods, because that would list the "
    "folder twice on -mod="
)
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
_OPTIONAL_TERMINAL_KEYS = frozenset(
    {
        "attempt_run_id",
        "launch_operation_id",
        "storage_recovery_reason",
        "storage_recovery_hint",
    }
)


class DayzTestToolError(RuntimeError):
    def __init__(self, code: str, *, cause: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.cause = cause


@dataclass(frozen=True, slots=True)
class WorkerTerminal:
    cleanup_degraded: bool
    error_code: str | None
    exit_code: int
    ok: bool
    run_id: str | None
    attempt_run_id: str | None = None
    launch_operation_id: str | None = None
    attestation: dict[str, object] | None = None
    storage_recovery_reason: str | None = None
    storage_recovery_hint: str | None = None


class _Runtime(Protocol):
    active_lease_token: str | None
    active_ticket: str | None
    active_operation_id: str | None
    daemon_policy: object

    async def lifecycle_status(self) -> dict[str, object]: ...
    async def reconcile_idle_session(self) -> dict[str, object]: ...
    async def bridge_status_payload(self) -> dict[str, object]: ...
    async def lifecycle_close(self, run_id: str) -> dict[str, object]: ...
    async def lifecycle_reap(self, run_id: str) -> dict[str, object]: ...


_ProgressCallback = Callable[[str, str | None], Awaitable[None]]


async def _open_client_dumps(
    runtime: object, baseline: ClientDumpBaseline | None
) -> str | None:
    """Record the pre-launch snapshot in the daemon (296b r3); its token or None.

    Opening caps every earlier run on the same client roots, whichever MCP
    process launched it. Any failure only costs the late-death diagnosis of
    this run: it is never bound and publishes null. The runtime method is
    called directly, not through getattr: security_runtime_audit flags a
    dynamically resolved call as dynamic_http. A runtime without it raises
    AttributeError, caught below.
    """
    if baseline is None:
        return None
    wire = baseline_to_wire(baseline)
    if wire is None:
        return None
    try:
        response = await runtime.client_dumps_open(wire)  # type: ignore[attr-defined]
    except Exception:
        return None
    token = response.get("token") if isinstance(response, dict) else None
    return token if isinstance(token, str) and token else None


async def _bind_client_dumps(runtime: object, token: str | None, run_id: object) -> None:
    if token is None or not isinstance(run_id, str):
        return
    try:
        await runtime.client_dumps_bind(token, run_id)  # type: ignore[attr-defined]
    except Exception:
        return


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
        _fail(_BAD_MOD)
    return list(value)


def _names_bridge(value: object) -> bool:
    """Same comparison as the bridge check: basename, casefolded."""
    return (
        isinstance(value, str)
        and ntpath.basename(value).casefold() in _BRIDGE_MOD_NAMES
    )


def _effective_base_mods(
    public_base: list[str] | None,
    *,
    policy_defaults: tuple[str, ...],
    no_base_mods: bool,
) -> list[str]:
    """The base_mods list the sealed payload, and then -mod=, will carry.

    The caller's list wins. Omitting the field uses the policy defaults.
    no_base_mods clears both, matching dayz_test_request's canonical payload.
    """
    if no_base_mods:
        return []
    if public_base is not None:
        return list(public_base)
    return list(policy_defaults)


def _extra_mods_with_bridge_default(
    extra_mods: list[str] | None,
    *,
    project_mod: str,
    kill: bool,
    base_mods: list[str],
    project_counts_as_bridge: bool = True,
) -> tuple[list[str] | None, tuple[str, ...]]:
    """Append @DayZ_MCP on extra_mods when this launch would miss the bridge.

    kill requests are not launches. The DayZ_MCP project already is the
    bridge. An entry whose basename casefolds into _BRIDGE_MOD_NAMES is
    already present, including '@dayz_mcp' and an absolute path, so it is
    not duplicated. The same comparison on the effective base_mods keeps
    the original document: copying the folder into extra_mods would put it
    twice on -mod=, and the bridge check below still reports
    bridge_mod_missing because base_mods do not satisfy it. A list the
    sealed parser would reject after the append (longer than 64, or a
    casefold duplicate) is left unchanged.
    """
    if kill or _names_bridge(project_mod):
        # The project folder is the bridge. A normal launch already counts it.
        # An override excludes that folder and must not copy it back into
        # extra_mods; presence is then decided on the effective list.
        del project_counts_as_bridge
        return extra_mods, ()
    if extra_mods is not None and any(_names_bridge(item) for item in extra_mods):
        return extra_mods, ()
    if any(_names_bridge(item) for item in base_mods):
        return extra_mods, ()
    candidate = [*(extra_mods or ()), _DEFAULT_BRIDGE_EXTRA]
    if not dayz_test_request._valid_string_list(candidate):
        return extra_mods, ()
    return candidate, (_DEFAULT_BRIDGE_EXTRA,)


def _bridge_default_report(
    requested: list[str] | None, canonical: bytes
) -> list[str]:
    """The default this canonical request actually carried, if it did."""
    try:
        payload = json.loads(canonical)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    got = payload.get("extra_mods")
    if type(got) is not list:
        return []
    prefix = list(requested) if type(requested) is list else []
    if got == [*prefix, _DEFAULT_BRIDGE_EXTRA]:
        return [_DEFAULT_BRIDGE_EXTRA]
    return []


def _annotate_bridge_default(
    result: dict[str, object],
    defaulted: list[str],
    *,
    project_mod_replaced: bool = False,
) -> dict[str, object]:
    if defaulted:
        result["extra_mods_defaulted"] = list(defaulted)
    if project_mod_replaced:
        result["project_mod_replaced"] = True
    return result


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
    auto_remediate_steam: bool = False,
    navmesh_data_server: bool = False,
    port: int = 2302,
    width: int = 1920,
    height: int = 1080,
    player_name: str = "Dev",
    server_wait_s: int = 60,
    kill: bool = False,
    replace_if_not_polling_since: int | None = None,
    project_mod_override: bool = False,
) -> tuple[bytes, dayz_test_request.RequestProjectPolicy]:
    selected = _selected_policy(sealed_policies, project)
    if mode not in _accepted_modes():
        _fail(_mode_expected_error())
    public_extra = _public_mod_list(extra_mods, selected.mod_roots)
    public_base = _public_mod_list(base_mods, selected.mod_roots)
    public_server = _public_mod_list(server_mods, selected.mod_roots)
    # Caller's entries are already validated. The default is one more folder
    # name on extra_mods, then the same parser. A size or list rejection that
    # appears only with that name is dropped and the bridge check below
    # reports bridge_mod_missing; any other rejection is the caller's.
    requested_extra = public_extra
    effective_base = _effective_base_mods(
        public_base,
        policy_defaults=selected.default_base_mods,
        no_base_mods=no_base_mods,
    )
    public_extra, defaulted_bridge = _extra_mods_with_bridge_default(
        public_extra,
        project_mod=selected.mod,
        kill=kill,
        base_mods=effective_base,
        project_counts_as_bridge=not project_mod_override,
    )

    def _compose(
        mods: list[str] | None,
    ) -> dayz_test_request.ParsedDayzTestRequest:
        document: dict[str, object] = {
            "auto_remediate_steam": auto_remediate_steam,
            "build": build,
            "clean": clean,
            "dev_root": selected.dev_root,
            "height": height,
            "kill": kill,
            "mission": mission,
            "mod": selected.mod,
            "mode": mode,
            "navmesh_data_server": navmesh_data_server,
            "no_base_mods": no_base_mods,
            "no_file_patching": no_file_patching,
            "pack_only": pack_only,
            "player_name": player_name,
            "port": port,
            "preflight": preflight,
            "run_id": run_id,
            "server_wait_s": server_wait_s,
            "version": 2 if project_mod_override else 1,
            "width": width,
        }
        if project_mod_override:
            document["project_mod_override"] = True
        if mods is not None:
            document["extra_mods"] = mods
        if public_base is not None:
            document["base_mods"] = public_base
        if public_server is not None:
            document["server_mods"] = public_server
        if replace_if_not_polling_since is not None:
            # 79e2. Present only on the call that supersedes a live client, and only
            # once the gate has actually read the bridge: the value is the instant
            # of that reading, which start_run revalidates before killing anything.
            document["replace_if_not_polling_since"] = replace_if_not_polling_since
        from dayz_mcp import server_cli

        document["shared_lock_root"] = os.path.normpath(str(server_cli.shared_root()))
        # The private worker environment is built from a fixed list and does
        # not inherit DAYZ_MCP_BUILD_LOCK_WAIT_S. The sealed request is the
        # accredited copy of the daemon's validated limit.
        document["build_lock_wait_s"] = server_cli.build_lock_wait_s()
        with server_cli._instance_bound_lock:
            bound = server_cli._instance_bound
        if bound is not None and bound[0]:
            document["instance_token"] = bound[0]
        raw = json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            return dayz_test_request.parse_dayz_test_request(
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
            # 8f8c point 3. The 25 conditions of the parser used to arrive here as
            # one token. A declared reason is republished with the same shape the
            # three named causes above already use, so every consumer that matches
            # on the bad_dayz_test_request prefix keeps working; anything else --
            # an undeclared suffix, a TypeError, a ValueError from elsewhere --
            # keeps EXACTLY the bare legacy code.
            prefix = "invalid_dayz_test_request:"
            if token.startswith(prefix):
                reason = token[len(prefix) :]
                if reason in dayz_test_request.REQUEST_REJECTION_REASONS:
                    _fail(f"bad_dayz_test_request:{reason}")
            _fail("bad_dayz_test_request")

    try:
        parsed = _compose(public_extra)
    except DayzTestToolError as exc:
        if (
            defaulted_bridge
            and exc.code in _SEALED_REQUEST_REJECTS_BRIDGE_DEFAULT
        ):
            try:
                parsed = _compose(requested_extra)
            except DayzTestToolError:
                raise exc
            public_extra = requested_extra
        else:
            raise
    effective_mods = [
        *( [] if project_mod_override else [selected.mod] ),
        *(public_extra or []),
    ]
    if not kill and not any(
        ntpath.basename(mod).casefold() in _BRIDGE_MOD_NAMES
        for mod in effective_mods
    ):
        if any(_names_bridge(item) for item in effective_base):
            _fail(_BRIDGE_MOD_MISSING_IN_BASE_MODS)
        _fail(_BRIDGE_MOD_MISSING)
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


def _run_unknown_to_store(status: object, run_id: str) -> bool:
    """True only when a readable /lifecycle/status lists no row for run_id.

    The same rows _exact_run resolves dayz_test_stop against, so an id this
    returns True for is one dayz_test_stop answers run_not_found. A status that
    cannot be read, or does not read as a list of rows, proves nothing.
    """
    if not isinstance(status, dict):
        return False
    runs = status.get("runs")
    if not isinstance(runs, list) or any(not isinstance(item, dict) for item in runs):
        return False
    return not any(item.get("run_id") == run_id for item in runs)


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
    state = run.get("state")
    if state != "RUNNING_IDLE":
        raise DayzTestToolError(
            "run_not_extensible",
            cause=state if isinstance(state, str) and state else "unknown",
        )
    if run.get("mod") != "@" + selected_policy.mod:
        _fail("run_project_mismatch")
    if _validated_recorded_leaf(selected_policy, run) is None:
        _fail("lifecycle_status_invalid")
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


def _terminal_storage_pair(
    value: dict[str, object], ok: bool, error_code: object
) -> tuple[str, str] | None:
    """The declared storage refusal, when the terminal carries a whole pair.

    Both keys or neither, only on a failed storage-rejection terminal, the
    token from the closed vocabulary and its canonical guidance. Anything
    else is a malformed diagnostic, not a half truth to publish.
    """
    present_reason = "storage_recovery_reason" in value
    present_hint = "storage_recovery_hint" in value
    if not present_reason and not present_hint:
        return None
    if not present_hint or ok or error_code != "storage_recovery_required":
        _fail("terminal_invalid")
    reason = value.get("storage_recovery_reason")
    hint = value.get("storage_recovery_hint")
    if (
        not isinstance(reason, str)
        or not isinstance(hint, str)
        or reason not in dayz_test_storage.STORAGE_RECOVERY_REASONS
        or hint != dayz_test_storage.storage_recovery_hint(reason)
    ):
        _fail("terminal_invalid")
    return reason, hint


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
    if not isinstance(value, dict) or canonical != stdout:
        _fail("terminal_invalid")
    keys = set(value)
    optional = keys - _TERMINAL_KEYS
    attestation_report = None
    if "attestation" in keys:
        try:
            attestation_report = dayz_test_attestation.validate_report(
                value.get("attestation")
            )
        except (TypeError, ValueError):
            _fail("terminal_invalid")
    allowed_optional = set(_OPTIONAL_TERMINAL_KEYS)
    if attestation_report is not None:
        allowed_optional.add("attestation")
    if (
        not _TERMINAL_KEYS <= keys
        or optional - allowed_optional
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
        if (
            exit_code != 0
            or error_code is not None
            or cleanup_degraded
            or optional - {"attestation"}
        ):
            _fail("terminal_invalid")
        attempt_run_id = None
        launch_operation_id = None
        # A success carries no storage diagnostic pair. The ok branch above
        # already refused any optional key beyond the attestation.
        storage_recovery = None
    else:
        if (
            not 1 <= exit_code <= 255
            or error_code not in dayz_test_worker.WORKER_ERROR_CODES
            or cleanup_degraded
            and run_id is None
            or not cleanup_degraded
            and run_id is not None
        ):
            _fail("terminal_invalid")
        attempt_run_id = value.get("attempt_run_id")
        launch_operation_id = value.get("launch_operation_id")
        if "attempt_run_id" in value and not _valid_uuid4(attempt_run_id):
            _fail("terminal_invalid")
        if "launch_operation_id" in value and (
            not _valid_uuid4(launch_operation_id) or "attempt_run_id" not in value
        ):
            _fail("terminal_invalid")
        if "attempt_run_id" not in value:
            attempt_run_id = None
        if "launch_operation_id" not in value:
            launch_operation_id = None
        storage_recovery = _terminal_storage_pair(value, ok, error_code)
    return WorkerTerminal(
        cleanup_degraded=cleanup_degraded,
        error_code=error_code,
        exit_code=exit_code,
        ok=ok,
        run_id=run_id,
        attempt_run_id=attempt_run_id if isinstance(attempt_run_id, str) else None,
        launch_operation_id=(
            launch_operation_id if isinstance(launch_operation_id, str) else None
        ),
        attestation=attestation_report,
        storage_recovery_reason=(
            None if storage_recovery is None else storage_recovery[0]
        ),
        storage_recovery_hint=(
            None if storage_recovery is None else storage_recovery[1]
        ),
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


def _planned_profile_leaf() -> str:
    """Leaf of the bound instance. Unbound is the default ``profiles`` leaf."""
    from dayz_mcp.server_cli import (
        InstanceSelectionError,
        bound_instance_token,
        profile_leaf_name,
    )

    try:
        return profile_leaf_name(bound_instance_token())
    except InstanceSelectionError:
        _fail("invalid_instance_token")


def _paths_for_leaf(
    policy: dayz_test_request.RequestProjectPolicy, mode: str, leaf: str
) -> list[str]:
    record = _mode_record(mode)
    if record is None:
        _fail(_mode_expected_error())
    return [ntpath.join(policy.dev_root, root, leaf) for root in record.artifact_roots]


def _validated_recorded_leaf(
    policy: dayz_test_request.RequestProjectPolicy, run: dict[str, object]
) -> str | None:
    """The recorded profile leaf, after the anchor matches this instance.

    The check runs before any requested role is chosen. A relative path, a
    traversal, another project, another token, or a legacy leaf under a named
    instance is rejected.
    """
    profiles = run.get("profiles")
    if not isinstance(profiles, str) or not profiles or not ntpath.isabs(profiles):
        return None
    # Reject traversal in the recorded text. normpath would erase `..` and
    # turn a different role's anchor into the approved leaf.
    raw_parts = profiles.replace("/", "\\").split("\\")
    if any(part == ".." for part in raw_parts):
        return None
    normalized = ntpath.normpath(profiles)
    try:
        leaf = _planned_profile_leaf()
    except DayzTestToolError:
        return None
    if ntpath.basename(normalized).casefold() != leaf.casefold():
        return None
    parent = ntpath.basename(ntpath.dirname(normalized))
    if parent.casefold() not in {"_server", "_client"}:
        return None
    dev_root = policy.dev_root
    if not isinstance(dev_root, str) or not ntpath.isabs(dev_root):
        return None
    expected = ntpath.normcase(ntpath.normpath(ntpath.join(dev_root, parent, leaf)))
    if ntpath.normcase(normalized) != expected:
        return None
    return leaf


def _artifact_paths(
    policy: dayz_test_request.RequestProjectPolicy, mode: str
) -> list[str]:
    """The profile roots of an exact mode record, in the order it declares.

    This was a literal table whose else branch answered _client for every
    name it did not know: a mode whose roots the authority moved kept
    reporting the old ones, and an unknown mode reported a root it never
    wrote. An exact lookup that fails closed says so instead.

    Before a run exists the leaf is the bound instance. The default instance
    stays ``profiles``.
    """
    return _paths_for_leaf(policy, mode, _planned_profile_leaf())


def _client_profile_roots(
    policy: dayz_test_request.RequestProjectPolicy,
    mode: str,
    run: dict[str, object] | None = None,
) -> list[str]:
    """The profile roots this mode starts a client in; never a server root.

    offline is the client-side process that also hosts the mission. An
    existing run keeps the leaf of its validated recorded anchor. An anchor
    that does not belong to this instance yields no root.
    """
    record = _mode_record(mode)
    if record is None:
        return []
    if run is not None:
        leaf = _validated_recorded_leaf(policy, run)
        if leaf is None:
            return []
    else:
        leaf = _planned_profile_leaf()
    return [
        ntpath.join(policy.dev_root, step.root, leaf)
        for step in record.steps
        if step.kind == "start" and step.role in {"client", "offline"} and step.root
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
_CLIENT_START_STALLED = "client_start_stalled"
_CLIENT_PROCESS_DEAD = "client_process_dead"
_CLIENT_RECORD_AGE_UNKNOWN = "client_record_age_unknown"
_BRIDGE_STATUS_UNKNOWN = "bridge_status_unknown"
_NO_CLIENT_TO_REPLACE = "no_client_to_replace"
_LIFECYCLE_STATUS_INVALID = "lifecycle_status_invalid"
CLIENT_ALREADY_POLLING = "client_already_polling"
BRIDGE_STATUS_UNKNOWN = "bridge_status_unknown"
CLIENT_STILL_STARTING = "client_still_starting"
CLIENT_START_STALLED = "client_start_stalled"
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
    _CLIENT_START_STALLED: CLIENT_START_STALLED,
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
    _CLIENT_START_STALLED: (
        "the registered client wrote the header of its RPT and nothing after "
        f"it for {PEER_STALE_S:g} s or more, and has not polled for as long: "
        "it is stalled at engine start, not loading (a healthy client writes "
        "its first line within a second of the header). It is younger than "
        "the startup budget, so nothing was touched. Repeat this call with "
        "client_start_budget_s=0 to supersede it now, or "
        "dayz_test_stop(run_id=...) then dayz_test_run to start over."
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


def _client_start_budget_s(override: float | None = None) -> float:
    """The startup budget in seconds: call parameter, then environment, default.

    The two doors are deliberately ASYMMETRIC about a value they cannot read.

    An env var is ignored: whoever exported it is not reading this response, and
    an unreadable override must not silently disable the guard.

    A call parameter is REJECTED. The caller does read the response, and a
    budget quietly discarded would answer client_still_starting for six minutes
    to someone who asked for five seconds - a mute failure, and one the caller
    has no way to see. Bool is refused with the rest: ``True`` would otherwise
    become a one-second budget by accident.
    """
    if override is not None:
        if isinstance(override, bool) or not isinstance(override, (int, float)):
            raise ValueError(
                "client_start_budget_s must be a number in [0, 3600], got "
                f"{override!r}"
            )
        value = float(override)
        if value != value or not 0.0 <= value <= 3600.0:
            raise ValueError(
                "client_start_budget_s must be a finite number in [0, 3600], got "
                f"{override!r}"
            )
        return value
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


# fb-20260819-123453-9359 (residue). A client that hangs at engine start keeps
# its process and its RPT stops at the header: three rules of '=' around the
# exe, the command line, the times and the version. Every later line is the
# engine at work. Measured 2026-09-30 over the 443 client RPTs of
# DayZ_MCP_dev\_client\profiles: the 400 that got past the header all wrote
# their first line within 0.99 s of the header's "Current time" (p50 0.57 s);
# the other 43 never wrote a line after it. The stall threshold is
# PEER_STALE_S, the same 15 s after which a silent peer stops counting as
# polling: about fifteen times the slowest header-to-first-line gap. It is
# measured from the header's own write (the RPT mtime), not from the process
# start: two healthy clients wrote their first line 16.8 and 27.2 s after the
# time in their RPT name, because their header itself came that late.
_RPT_HEADER_RULE = re.compile(rb"^={20,}\s*$")
_RPT_HEADER_RULES = 3
_RPT_HEADER_SCAN_BYTES = 64 * 1024
_CLIENT_START_STALL_S = PEER_STALE_S


def _rpt_header_only(path: Path) -> bool | None:
    """True for an RPT that holds its complete header and nothing after it.

    False when anything but blank lines follows the header, or when the file
    is larger than the scan window, which no header is. None when the file
    cannot be read or its header is not complete: no evidence either way.
    """
    try:
        with path.open("rb") as handle:
            data = handle.read(_RPT_HEADER_SCAN_BYTES + 1)
    except OSError:
        return None
    if len(data) > _RPT_HEADER_SCAN_BYTES:
        return False
    lines = data.splitlines()
    rules = [index for index, line in enumerate(lines) if _RPT_HEADER_RULE.match(line)]
    if len(rules) < _RPT_HEADER_RULES:
        return None
    return all(not line.strip() for line in lines[rules[_RPT_HEADER_RULES - 1] + 1 :])


def _client_start_stalled(
    profiles_dirs: list[str], born: float, now: float
) -> bool | None:
    """Does the RPT of the client started at ``born`` sit at its bare header?

    True only when the newest RPT written since that start holds the complete
    header and nothing else, and was last written _CLIENT_START_STALL_S or
    more before ``now``. None when no such RPT can be found or read.
    """
    newest: tuple[str, int, float] | None = None
    for directory in profiles_dirs:
        found = process_lifecycle._launch_rpt(directory, born)
        if found is not None and (newest is None or found[2] > newest[2]):
            newest = found
    if newest is None:
        return None
    path, _size, mtime = newest
    header_only = _rpt_header_only(Path(path))
    if header_only is not True:
        return header_only
    return now - mtime >= _CLIENT_START_STALL_S


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


def _client_start_stall_evidence(
    record: ClientRecordProjection, profiles_dirs: list[str]
) -> bool | None:
    """The RPT evidence the gate folds in; None whenever it has none.

    Read only for a client record that is present with a readable age: the
    RPT of that client is the newest one written since its process started.
    """
    if not record.valid or not record.present or record.age_s is None:
        return None
    if not profiles_dirs:
        return None
    now = time.time()
    try:
        return _client_start_stalled(profiles_dirs, now - record.age_s, now)
    except Exception:
        return None


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
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
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


def _http_status_of(exc: BaseException) -> int | None:
    for attr in ("status", "code"):
        value = getattr(exc, attr, None)
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 100 <= value <= 599
        ):
            return value
    return None


def _bridge_status_cause(exc: BaseException) -> str:
    """Name the unreadability without a traceback.

    The replacement gate has to tell a timeout from a 503 from a
    communication failure (refused or reset). A refused connection does
    not prove the daemon crashed: it proves the call did not complete.
    Messages and tracebacks carry host paths; type, HTTP status and
    errno do not. error_code stays the stable token so existing equality
    checks keep matching. The published token is not a retry policy.
    """
    name = type(exc).__name__
    http_status = _http_status_of(exc)
    if http_status is not None:
        return f"{name}:{http_status}"
    transport = getattr(exc, "errno", None)
    if not isinstance(transport, int) or isinstance(transport, bool):
        transport = getattr(exc, "winerror", None)
    if isinstance(transport, int) and not isinstance(transport, bool):
        return f"{name}:{transport}"
    token = str(exc).strip()
    if (
        3 <= len(token) <= 64
        and token[0].isascii()
        and token[0].isalpha()
        and all(ch.isascii() and (ch.isalnum() or ch == "_") for ch in token)
    ):
        return f"{name}:{token}"
    return name


def _decide_client_replacement(
    record: ClientRecordProjection,
    bridge_status_payload: object,
    *,
    budget_s: float | None = None,
    start_stalled: bool | None = None,
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

    ``start_stalled`` is the RPT evidence of _client_start_stalled (9359): True
    names a client stuck at its bare header. It only renames what the startup
    budget already decides -- client_still_starting under it (still refused),
    client_not_polling past it (still replaced) -- and anything but True
    leaves the decision exactly as it was.
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
    # 9359: its RPT sat at the bare header for PEER_STALE_S and no poll came
    # for as long. A poll younger than that is someone polling, not a stall.
    stalled = start_stalled is True and (age is None or age >= server.PEER_STALE_S)
    if record.age_s < (
        budget_s if budget_s is not None else _client_start_budget_s()
    ):
        if stalled:
            # Still a refusal: the budget is the only kill line that was ever
            # calibrated. The name says it will not come up by waiting, and
            # the remediation names the explicit override.
            return ClientReplacementDecision(
                False, _CLIENT_START_STALLED, age, record.age_s
            )
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
    if stalled:
        return ClientReplacementDecision(
            True, _CLIENT_START_STALLED, age, record.age_s
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


_STEAM_PREP_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _steam_prep_token(value: object) -> str | None:
    return value if type(value) is str and _STEAM_PREP_TOKEN.fullmatch(value) else None


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
    steam_startup: object = None,
    steam_pid_repair: object = None,
    steam_restarted: object = None,
    client_dump_baseline: ClientDumpBaseline | None = None,
    storage_rotated: bool | None = None,
    storage_backup: str | None = None,
    storage_reset_notice: str | None = None,
    project_mod_replaced: bool = False,
) -> dict[str, object]:
    projection = readiness or _NULL_READINESS
    startup = _steam_prep_token(steam_startup)
    repair = _steam_prep_token(steam_pid_repair)
    restarted = steam_restarted if type(steam_restarted) is bool else None
    recovery_reason = terminal.storage_recovery_reason
    recovery_hint = terminal.storage_recovery_hint
    if (
        terminal.ok
        or terminal.error_code != "storage_recovery_required"
        or not isinstance(recovery_reason, str)
        or recovery_reason not in dayz_test_storage.STORAGE_RECOVERY_REASONS
        or recovery_hint != dayz_test_storage.storage_recovery_hint(recovery_reason)
    ):
        recovery_reason = None
        recovery_hint = None
    if remediation is None and recovery_hint is not None:
        remediation = recovery_hint
    result: dict[str, object] = {
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
        # The admin-tools gate (ficha df93). An empty list is a MEASUREMENT:
        # the gate ran here and found nothing. null means this layer never
        # consulted it -- a stop, whose request is offline and therefore starts
        # no server, and every envelope built before the gate. A request that
        # asks for no admin tools is not refused: vpp_mod_not_requested travels
        # in vpp_warnings. The gate itself is not optional: it runs inside
        # native_launcher_transaction for every launch, named or not.
        "vpp_missing": vpp_missing,
        "vpp_warnings": vpp_warnings,
        # ficha 47c4: name the Steam-bootstrap death, never repair Steam here.
        # Always present, null included. Tokens fullmatch [a-z][a-z0-9_]{0,63};
        # restarted is a strict bool. Anything else, including identity, is null.
        "steam_startup": startup,
        "steam_pid_repair": repair,
        "steam_restarted": restarted,
        "client_death_diagnosis": diagnose_client_steam_bootstrap(
            error_code=terminal.error_code,
            client_alive=client_alive,
            steam_startup=startup,
            dump_baseline=client_dump_baseline,
        ),
        # null: this call did not observe a rotation (no status, another run,
        # a launch that does not create storage, a call on an existing run,
        # a legacy row). false: the run measured that it did not rotate. true
        # is only that measurement. It is not evidence the economy restored
        # the world.
        "storage_rotated": storage_rotated if type(storage_rotated) is bool else None,
        "storage_backup": storage_backup if storage_rotated is True else None,
        "storage_reset_notice": (
            storage_reset_notice if storage_rotated is True else None
        ),
        # null when this failure is not a declared storage refusal. The hint
        # is the remediation above; the token is the decision, not daemon text.
        "storage_recovery_reason": recovery_reason,
    }
    if terminal.attestation is not None:
        result["attestation"] = terminal.attestation
    if project_mod_replaced:
        result["project_mod_replaced"] = True
    return result


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
    leaf = _validated_recorded_leaf(policy, run)
    if leaf is None:
        _fail("lifecycle_status_invalid")
    candidates = _paths_for_leaf(policy, "all", leaf)
    normalized = ntpath.normcase(ntpath.normpath(profiles))
    matches = [
        candidate
        for candidate in candidates
        if ntpath.normcase(ntpath.normpath(candidate)) == normalized
    ]
    if len(matches) != 1:
        _fail("lifecycle_status_invalid")
    processes = run.get("processes")
    roles: set[str] = set()
    if isinstance(processes, list):
        for item in processes:
            if isinstance(item, dict):
                role = item.get("role")
                if isinstance(role, str) and role:
                    roles.add(role)
    if len(roles) > 1:
        return list(candidates)
    # fb-20260904-200821-dae1 (dae1-3 / A4 MEDIO): a server-anchored run
    # used to return only the server profiles root when the client role had
    # already left the row (hung client, 8f76). Hung-client RPTs under
    # _client\profiles were then never collected on stop. Keep client-only
    # anchors as a single root; expand server anchors to the full "all" set.
    server_roots = _paths_for_leaf(policy, "server", leaf)
    if matches == server_roots:
        return list(candidates)
    return matches


# fb-20260818-232129-1233. The sealed worker answers readiness_timeout both for
# a server still loading and for one that hung while loading. The daemon
# watched the RPT of that server launch, and the stop that followed the
# timeout recorded whether the RPT had been silent for more than
# SERVER_START_HUNG_AFTER_S; that is published on /lifecycle/status as
# server_start_verdict and cleared by every server launch.
SERVER_START_HUNG = process_lifecycle.SERVER_START_HUNG
_SERVER_START_HUNG_REMEDIATION = (
    "the server stopped writing its RPT for more than "
    f"{process_lifecycle.SERVER_START_HUNG_AFTER_S:g} s before it bound its "
    "UDP port: it hung while loading, it was not slow. The run was stopped "
    "like any readiness timeout. Relaunch with dayz_test_run; in "
    "fb-20260818-232129-1233 one relaunch was enough both times."
)

# fb-20260930-171015-4fdf. The sealed worker (native-launchers/dayz-test-v1/src/
# app_main.py, main) answers internal_failure for any exception it does not
# classify, and its DZW1 terminal carries only that code: the exception and its
# class stay in the sealed process, whose stderr the launcher sends to NUL.
# _execute_request is where that result leaves the sealed code, so the stage is
# named there and the class is published as unknown (null), not left out.
WORKER_INTERNAL_FAILURE = "internal_failure"
WORKER_INTERNAL_FAILURE_REASON = "sealed_worker_exception"
_WORKER_INTERNAL_FAILURE_REMEDIATION = (
    "the sealed dayz-test-v1 worker ended on an exception it does not "
    "classify, and it reports no exception class. Retry once; if it fails "
    "the same way, report it with pipeline_feedback (tool, args, error)."
)


def _server_start_hung(status: object, run_id: str | None) -> bool:
    """Whether the daemon's hung verdict is about the start this call made.

    A server launch clears the verdict, and a readiness_timeout means this
    call launched a server, so a verdict present now was reached after it.
    It must still name a run that is not alive any more (the worker stopped
    it), or the very run the terminal names when that stop degraded.
    Anything that does not read exactly like that keeps the generic timeout.
    """
    if not isinstance(status, dict):
        return False
    verdict = status.get("server_start_verdict")
    if not isinstance(verdict, dict) or verdict.get("code") != SERVER_START_HUNG:
        return False
    hung_run = verdict.get("run_id")
    if not _valid_uuid4(hung_run):
        return False
    stalled = verdict.get("rpt_stalled_s")
    if (
        isinstance(stalled, bool)
        or not isinstance(stalled, (int, float))
        or not math.isfinite(stalled)
        or stalled <= process_lifecycle.SERVER_START_HUNG_AFTER_S
    ):
        return False
    if run_id is not None:
        return hung_run == run_id
    runs = status.get("runs")
    if not isinstance(runs, list):
        return False
    return not any(
        isinstance(item, dict)
        and item.get("run_id") == hung_run
        and item.get("state") != "EXITED"
        for item in runs
    )


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
    client_dump_roots: list[str] | None = None,
) -> dict[str, object]:
    stdout = bytearray()
    stderr = bytearray()
    queued_reported = False
    client_dump_baseline: ClientDumpBaseline | None = None
    client_dump_token: str | None = None

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
        nonlocal queued_reported, client_dump_baseline, client_dump_token
        if not queued_reported:
            queued_reported = True
            await report("queued", "En cola")
        if client_dump_roots is not None and client_dump_baseline is None:
            # 296b: the dumps already in the CLIENT profile roots when this
            # launch leaves the queue; only a dump absent from here can name
            # its death. Taken after the wait, not before it, so a dump another
            # client's run writes while this call is queued is in the baseline.
            client_dump_baseline = snapshot_client_dumps(client_dump_roots)
            client_dump_token = await _open_client_dumps(
                runtime, client_dump_baseline
            )
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
    # 296b: the client can die after this call returns; the retired-run view
    # reads the snapshot the daemon now holds for this run_id.
    await _bind_client_dumps(runtime, client_dump_token, terminal.run_id)
    if not terminal.ok and terminal.error_code == "worker_failed":
        try:
            failed_status = await runtime.lifecycle_status()
        except Exception:
            failed_status = None
        if isinstance(failed_status, dict):
            reason = failed_status.get("last_start_error")
            if type(reason) is str and reason:
                terminal = replace(terminal, error_code=reason)
    if not terminal.ok and terminal.error_code == "readiness_timeout":
        try:
            readiness_status = await runtime.lifecycle_status()
        except Exception:
            readiness_status = None
        if _server_start_hung(readiness_status, terminal.run_id):
            terminal = replace(terminal, error_code=SERVER_START_HUNG)
    # inbox 3997. When the worker cannot confirm the stop of the run it minted
    # it names that run (cleanup_degraded), even when the daemon refused its
    # start before registering anything: dayz_test_stop then answered
    # run_not_found for the id this call returned. A call that creates its run
    # drops an id the store does not list; a status it cannot read keeps it.
    # The cleanup claim goes with the id (parse_worker_terminal ties
    # cleanup_degraded to a run_id): the store has no row for it, a launch
    # writes its row before it spawns, and the transaction released and
    # verified its lease before this terminal was read, so no start of that id
    # can still be admitted. The error code is kept.
    registered: object = None
    if (
        not terminal.ok
        and terminal.run_id is not None
        and expected_run_id is None
        and not preflight
    ):
        try:
            registered = await runtime.lifecycle_status()
        except Exception:
            registered = None
        if _run_unknown_to_store(registered, terminal.run_id):
            terminal = replace(terminal, run_id=None, cleanup_degraded=False)
    # Storage is this attempt only, decided on the worker's terminal before any
    # projection below turns a launch into a failure (client_dead_after_ack keeps
    # the run it names). attempt_run_id is the worker's own id for the run it
    # created or targeted; a failure terminal without it (an older worker, or a
    # failure before any run) is unknown, not a license to use whatever run_id
    # cleanup left behind. A call that names an existing run (reattach, stop)
    # never rotates: the rotation that run recorded at its creation is not this
    # call's observation, so the fields stay null on every terminal.
    if expected_run_id is not None:
        storage_run_id: str | None = None
        storage_operation = None
    elif terminal.attempt_run_id is not None:
        storage_run_id = terminal.attempt_run_id
        storage_operation = terminal.launch_operation_id
    elif terminal.ok:
        storage_run_id = terminal.run_id
        storage_operation = None
    else:
        storage_run_id = None
        storage_operation = None
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
    steam_startup, steam_pid_repair, steam_restarted = _steam_fields_from_status(
        status, terminal.run_id
    )
    observed_status = status if isinstance(status, dict) else registered
    if not preflight and storage_run_id is not None and not isinstance(observed_status, dict):
        try:
            observed_status = await runtime.lifecycle_status()
        except Exception:
            observed_status = None
    storage_rotated, storage_backup, storage_reset_notice = (
        _storage_observation_from_status(
            observed_status, storage_run_id, storage_operation
        )
    )
    result = _compact_result(
        terminal=terminal,
        project=policy.mod,
        mode=public_mode,
        started_at=started_at,
        artifacts_paths=artifacts_paths,
        server_alive=server_alive,
        client_alive=client_alive,
        readiness=readiness,
        remediation=(
            _SERVER_START_HUNG_REMEDIATION
            if terminal.error_code == SERVER_START_HUNG
            else None
        ),
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
        steam_startup=steam_startup,
        steam_pid_repair=steam_pid_repair,
        steam_restarted=steam_restarted,
        client_dump_baseline=client_dump_baseline,
        storage_rotated=storage_rotated,
        storage_backup=storage_backup,
        storage_reset_notice=storage_reset_notice,
    )
    if not terminal.ok and terminal.error_code == WORKER_INTERNAL_FAILURE:
        # 4fdf: the stage is known here, the exception class is not (see above).
        result["reason"] = WORKER_INTERNAL_FAILURE_REASON
        result["exception_class"] = None
        result["remediation"] = _WORKER_INTERNAL_FAILURE_REMEDIATION
    return result


def _operation_matches(row: dict[str, object], operation_id: str | None) -> bool:
    """When both sides name an operation, they must be the same attempt."""
    if operation_id is None:
        return True
    if "launch_operation_id" not in row or row.get("launch_operation_id") is None:
        return True
    return row.get("launch_operation_id") == operation_id


def _decode_storage_observation(
    row: dict[str, object],
) -> tuple[bool | None, str | None, str | None]:
    if "storage_rotated" not in row:
        return None, None, None
    rotated = row.get("storage_rotated")
    if rotated is None:
        return None, None, None
    if rotated is False:
        return False, None, None
    if rotated is not True:
        return None, None, None
    backup = row.get("storage_backup")
    notice = row.get("storage_reset_notice")
    if (
        not isinstance(backup, str)
        or not backup
        or any(separator in backup for separator in ("/", "\\", ":"))
        or notice != dayz_test_storage.RESET_NOTICE
    ):
        return None, None, None
    return True, backup, notice


def _storage_observation_from_status(
    status: object,
    run_id: object,
    operation_id: str | None = None,
) -> tuple[bool | None, str | None, str | None]:
    """The rotation stored for this attempt's run id, or unknown.

    Exactly one live row naming the run id is the only authority, including
    when its measurement is null or the keys are absent: a log entry must not
    fill in a row that measured nothing. A row that names a different launch
    operation is unknown for this attempt. The durable observation log is
    consulted only when no live row names the run (an EXITED row already
    pruned). No run id is unknown, not a guess about whichever run happens
    to be in status.
    """
    if not isinstance(status, dict) or not isinstance(run_id, str) or not run_id:
        return None, None, None
    runs = status.get("runs")
    if isinstance(runs, list):
        matches = [
            item
            for item in runs
            if isinstance(item, dict) and item.get("run_id") == run_id
        ]
        if len(matches) > 1:
            return None, None, None
        if len(matches) == 1:
            if not _operation_matches(matches[0], operation_id):
                return None, None, None
            return _decode_storage_observation(matches[0])
    observations = status.get("storage_observations")
    if not isinstance(observations, list):
        return None, None, None
    found = [
        item
        for item in observations
        if isinstance(item, dict) and item.get("run_id") == run_id
    ]
    if len(found) != 1 or not _operation_matches(found[0], operation_id):
        return None, None, None
    return _decode_storage_observation(found[0])


def _steam_fields_from_status(
    status: object, run_id: object
) -> tuple[str | None, str | None, bool | None]:
    if not isinstance(status, dict):
        return None, None, None
    prep = status.get("steam_preparation")
    if not isinstance(prep, dict) or prep.get("run_id") != run_id:
        return None, None, None
    restarted = prep.get("restarted")
    return (
        _steam_prep_token(prep.get("startup")),
        _steam_prep_token(prep.get("pid_repair")),
        restarted if type(restarted) is bool else None,
    )


_MANAGED_BOX_STATES = frozenset(
    {"STARTING", "RUNNING", "RUNNING_IDLE", "STOPPING", "UNRECONCILED"}
)


_PREFLIGHT_LIVE_OMISSIONS = (
    "process_launch",
    "readiness",
    "initialization_evidence",
)


def _preflight_skipped_checks(mode: str, run_id: str | None) -> list[str]:
    skipped: list[str] = []
    if run_id is not None:
        skipped.append("extension_run")
        if _mode_starts_client(mode):
            skipped.append("client_replacement")
    skipped.extend(_PREFLIGHT_LIVE_OMISSIONS)
    return skipped


def _occupancy_from_box(box: object) -> tuple[bool | None, str | None]:
    """Known occupancy from the read-only box snapshot.

    ``scan_known`` / ``port_scan_known`` must both be true. Anything else,
    including a snapshot that sets ``occupied`` because the scan did not run,
    stays unknown (``null``), never a free or busy guess.
    """
    if not isinstance(box, dict):
        return None, None
    if box.get("scan_known") is not True or box.get("port_scan_known") is not True:
        return None, None
    runs = box.get("runs")
    foreign = box.get("foreign")
    occupied = box.get("occupied")
    if type(occupied) is not bool or not isinstance(runs, list) or not isinstance(foreign, list):
        return None, None
    occupants = [
        str(item.get("run_id"))
        for item in runs
        if isinstance(item, dict)
        and item.get("state") in _MANAGED_BOX_STATES
        and isinstance(item.get("run_id"), str)
    ]
    foreign_busy = any(isinstance(item, dict) for item in foreign)
    occupant = occupants[0] if len(occupants) == 1 else None
    if occupants or foreign_busy or occupied:
        return True, occupant
    return False, None


async def _sample_box_occupancy(
    runtime: _Runtime,
) -> tuple[bool | None, str | None]:
    """Advisory read of the box observer, not ``/lifecycle/status``.

    Lifecycle status lists managed runs only. Foreign DayZ processes and the
    scan flags live on the session box. A missing or failed observation is
    null, not a guess that the box is free.
    """
    observe = getattr(runtime, "session_status", None)
    if not callable(observe):
        return None, None
    try:
        status = await observe()
    except Exception:
        return None, None
    if not isinstance(status, dict) or "box" not in status:
        return None, None
    return _occupancy_from_box(status.get("box"))


def _stamp_preflight(
    result: dict[str, object],
    *,
    run_id: str | None,
    box_busy: bool | None,
    occupied_by_run_id: str | None,
    skipped: list[str],
    project_mod_replaced: bool,
) -> dict[str, object]:
    result["preflight"] = True
    result["box_busy"] = box_busy
    result["occupied_by_run_id"] = occupied_by_run_id
    result["preflight_skipped_checks"] = list(skipped)
    if run_id is not None:
        result["run_id"] = run_id
    if project_mod_replaced:
        result["project_mod_replaced"] = True
    return result


async def _execute_preflight(
    runtime: _Runtime,
    *,
    project: str,
    mode: str,
    mission: str,
    build: bool,
    clean: bool,
    pack_only: bool,
    run_id: str | None,
    extra_mods: list[str] | None,
    base_mods: list[str] | None,
    server_mods: list[str] | None,
    no_base_mods: bool,
    no_file_patching: bool,
    port: int,
    width: int,
    height: int,
    player_name: str,
    server_wait_s: int,
    auto_remediate_steam: bool,
    navmesh_data_server: bool,
    project_mod_override: bool,
    started_at: float,
) -> dict[str, object]:
    """Validate without a lease, a queue slot, a repair, or a process start."""
    skipped = _preflight_skipped_checks(mode, run_id)
    box_busy, occupied = await _sample_box_occupancy(runtime)

    def finish(
        result: dict[str, object], defaulted: list[str]
    ) -> dict[str, object]:
        stamped = _stamp_preflight(
            result,
            run_id=run_id,
            box_busy=box_busy,
            occupied_by_run_id=occupied,
            skipped=skipped,
            project_mod_replaced=project_mod_override,
        )
        return _annotate_bridge_default(
            stamped, defaulted, project_mod_replaced=project_mod_override
        )

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
                preflight=True,
                run_id=run_id,
                extra_mods=extra_mods,
                base_mods=base_mods,
                server_mods=server_mods,
                no_base_mods=no_base_mods,
                no_file_patching=no_file_patching,
                navmesh_data_server=navmesh_data_server,
                auto_remediate_steam=auto_remediate_steam,
                port=port,
                width=width,
                height=height,
                player_name=player_name,
                server_wait_s=server_wait_s,
                project_mod_override=project_mod_override,
            )
            bridge_default = _bridge_default_report(extra_mods, raw_request)
            vpp = preflight_vpp_request(
                raw_request, sealed_policies=bundle.sealed_policies
            )
            if vpp.error_code is not None:
                refused = _compact_result(
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
                return finish(refused, bridge_default)
            if _mode_starts_client(mode):
                desktop = await asyncio.to_thread(evaluate_prerun_desktop)
                if desktop.error_code is not None:
                    refused = _compact_result(
                        terminal=WorkerTerminal(
                            cleanup_degraded=False,
                            error_code=desktop.error_code,
                            exit_code=1,
                            ok=False,
                            run_id=None,
                        ),
                        project=policy.mod,
                        mode=mode,
                        started_at=started_at,
                        artifacts_paths=[],
                        phase="validating",
                        remediation=desktop.remediation,
                        vpp_missing=list(vpp.missing),
                        vpp_warnings=list(vpp.warnings),
                    )
                    return finish(refused, bridge_default)
                try:
                    steam = evaluate_steam_session()
                except Exception:
                    steam = SteamSessionResult(
                        error_code=STEAM_SESSION_STALE,
                        steam_registered_pid=None,
                        steam_live_pids=(),
                        remediation=REMEDIATION,
                    )
                # Client reattach does not start a new client during preflight.
                # A stopped or stale Steam session must not turn that preflight
                # into a failure: the supplied run id is the result, and a
                # later real launch still hits this same gate. Modes that
                # start a client without a run id (all, offline) keep the
                # refusal. No repair runs on either path.
                if steam.error_code is not None and run_id is None:
                    failed = _compact_result(
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
                    return finish(failed, bridge_default)
            parsed = dayz_test_request.parse_dayz_test_request(
                raw_request, policies=_semantic_policies(bundle.sealed_policies)
            )
            try:
                # Direct attribute, not getattr: assigning getattr's result
                # and calling it with two arguments is the shape the runtime
                # HTTP audit classifies as a dynamic HTTP callable.
                runtime_policy = bundle.validated_worker_runtime(
                    policy.mod, policy.dev_root
                )
            except Exception:
                failed = _compact_result(
                    terminal=WorkerTerminal(
                        cleanup_degraded=False,
                        error_code="runtime_policy_invalid",
                        exit_code=1,
                        ok=False,
                        run_id=None,
                    ),
                    project=policy.mod,
                    mode=mode,
                    started_at=started_at,
                    artifacts_paths=[],
                    phase="validating",
                    vpp_missing=list(vpp.missing),
                    vpp_warnings=list(vpp.warnings),
                )
                return finish(failed, bridge_default)
            try:
                mission_path = dayz_test_worker._mission(parsed.payload, runtime_policy)
                effective_directories = dayz_test_worker._effective_directories(
                    parsed.payload, runtime_policy
                )
            except dayz_test_worker.DayzTestWorkerError as error:
                failed = _compact_result(
                    terminal=WorkerTerminal(
                        cleanup_degraded=False,
                        error_code=error.code,
                        exit_code=1,
                        ok=False,
                        run_id=None,
                    ),
                    project=policy.mod,
                    mode=mode,
                    started_at=started_at,
                    artifacts_paths=[],
                    phase="validating",
                    vpp_missing=list(vpp.missing),
                    vpp_warnings=list(vpp.warnings),
                )
                return finish(failed, bridge_default)
            sealed = bundle.sealed_policies
            if (
                type(sealed) is tuple
                and sealed
                and all(
                    type(item) is request_path_authority.SealedRequestProjectPolicy
                    for item in sealed
                )
            ):
                selected = next(
                    (
                        item
                        for item in sealed
                        if item.policy.mod == policy.mod
                        and item.policy.dev_root == policy.dev_root
                    ),
                    None,
                )
                try:
                    if selected is None:
                        raise ValueError("invalid_dayz_test_path_authority")
                    with request_path_authority.accredit_runtime_resolved_paths(
                        selected,
                        directories=effective_directories,
                        mission=mission_path,
                    ):
                        pass
                except ValueError:
                    failed = _compact_result(
                        terminal=WorkerTerminal(
                            cleanup_degraded=False,
                            error_code="invalid_dayz_test_path_authority",
                            exit_code=1,
                            ok=False,
                            run_id=None,
                        ),
                        project=policy.mod,
                        mode=mode,
                        started_at=started_at,
                        artifacts_paths=[],
                        phase="validating",
                        vpp_missing=list(vpp.missing),
                        vpp_warnings=list(vpp.warnings),
                    )
                    return finish(failed, bridge_default)
            attestation_document = None
            try:
                attestation_document = dayz_test_worker.assess_preflight(
                    parsed.payload, runtime_policy, policy.attestation
                )
            except dayz_test_worker.DayzTestWorkerError as error:
                failed = _compact_result(
                    terminal=WorkerTerminal(
                        cleanup_degraded=False,
                        error_code=error.code,
                        exit_code=1,
                        ok=False,
                        run_id=None,
                        attestation=error.attestation,
                    ),
                    project=policy.mod,
                    mode=mode,
                    started_at=started_at,
                    artifacts_paths=[],
                    phase="validating",
                    vpp_missing=list(vpp.missing),
                    vpp_warnings=list(vpp.warnings),
                )
                return finish(failed, bridge_default)
            except ValueError as error:
                token = str(error)
                if not token.startswith("invalid_dayz_test_request:"):
                    raise
                failed = _compact_result(
                    terminal=WorkerTerminal(
                        cleanup_degraded=False,
                        error_code="bad_" + token[len("invalid_") :],
                        exit_code=1,
                        ok=False,
                        run_id=None,
                    ),
                    project=policy.mod,
                    mode=mode,
                    started_at=started_at,
                    artifacts_paths=[],
                    phase="validating",
                    vpp_missing=list(vpp.missing),
                    vpp_warnings=list(vpp.warnings),
                )
                return finish(failed, bridge_default)
            succeeded = _compact_result(
                terminal=WorkerTerminal(
                    cleanup_degraded=False,
                    error_code=None,
                    exit_code=0,
                    ok=True,
                    run_id=run_id,
                    attestation=attestation_document,
                ),
                project=policy.mod,
                mode=mode,
                started_at=started_at,
                artifacts_paths=[],
                phase="validating",
                vpp_missing=list(vpp.missing),
                vpp_warnings=list(vpp.warnings),
            )
            return finish(succeeded, bridge_default)


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
    auto_remediate_steam: bool = False,
    navmesh_data_server: bool = False,
    client_start_budget_s: float | None = None,
    project_mod_override: bool = False,
) -> dict[str, object]:
    """Run or preflight a request, with explicit omissions in this adapter.

    Every preflight envelope includes preflight_skipped_checks, even when empty.
    It lists checks that need a live run: extension_run, client_replacement,
    process_launch, readiness, and initialization_evidence. Steam is checked.
    Preflight success is not a free box and not a future launch.
    """
    started_at = time.monotonic()
    if progress_cb is not None:
        await progress_cb("validating", None)
    if mode not in _public_modes():
        _fail(_mode_expected_error())
    # Resolved HERE, before the session lock and before a single process is
    # touched: a budget the caller got wrong must cost them an error message,
    # not a launched client that then gets refused.
    budget_s = _client_start_budget_s(client_start_budget_s)
    if preflight:
        return await _execute_preflight(
            runtime,
            project=project,
            mode=mode,
            mission=mission,
            build=build,
            clean=clean,
            pack_only=pack_only,
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
            auto_remediate_steam=auto_remediate_steam,
            navmesh_data_server=navmesh_data_server,
            project_mod_override=project_mod_override,
            started_at=started_at,
        )
    preflight_skipped_checks: list[str] = []
    await _require_idle_session(runtime, tool="dayz_test_run")
    with open_approved_launcher("dayz-test-v1") as opened:
        opened.validate_native_pe()
        with secure_launcher.load_verified_bundle(opened) as bundle:
            request_arguments: dict[str, object] = {
                "project": project,
                "mode": mode,
                "mission": mission,
                "build": build,
                "clean": clean,
                "pack_only": pack_only,
                "preflight": preflight,
                "run_id": run_id,
                "extra_mods": extra_mods,
                "base_mods": base_mods,
                "server_mods": server_mods,
                "no_base_mods": no_base_mods,
                "no_file_patching": no_file_patching,
                "navmesh_data_server": navmesh_data_server,
                "auto_remediate_steam": auto_remediate_steam,
                "port": port,
                "width": width,
                "height": height,
                "player_name": player_name,
                "server_wait_s": server_wait_s,
                "project_mod_override": project_mod_override,
            }
            raw_request, policy = build_run_request(
                bundle.sealed_policies, **request_arguments
            )
            bridge_default = _bridge_default_report(extra_mods, raw_request)
            # The admin-tools gate runs before the host gate below: it is a
            # property of the request just composed, and a refusal here has
            # consulted neither Steam nor the lifecycle. A request that asks
            # for no admin tools passes it with a warning. Like the worker's
            # mission-resolution gate, this request gate applies to preflight
            # too. Host admission checks omitted by preflight are reported
            # separately. native_launcher_transaction enforces the same admin
            # decision below; this call only names it for the caller.
            vpp = preflight_vpp_request(
                raw_request, sealed_policies=bundle.sealed_policies
            )
            if vpp.error_code is not None:
                refused = _compact_result(
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
                if preflight:
                    refused["preflight_skipped_checks"] = preflight_skipped_checks
                return _annotate_bridge_default(refused, bridge_default, project_mod_replaced=project_mod_override)
            if _mode_starts_client(mode):
                # Gate body uses time.sleep / ImageGrab join. Run it off the
                # broker event loop so other MCP sessions keep heartbeating.
                desktop = await asyncio.to_thread(evaluate_prerun_desktop)
                if desktop.error_code is not None:
                    refused = _compact_result(
                        terminal=WorkerTerminal(
                            cleanup_degraded=False,
                            error_code=desktop.error_code,
                            exit_code=1,
                            ok=False,
                            run_id=None,
                        ),
                        project=policy.mod,
                        mode=mode,
                        started_at=started_at,
                        artifacts_paths=[],
                        phase="validating",
                        remediation=desktop.remediation,
                        vpp_missing=list(vpp.missing),
                        vpp_warnings=list(vpp.warnings),
                    )
                    if preflight:
                        refused["preflight_skipped_checks"] = preflight_skipped_checks
                    return _annotate_bridge_default(refused, bridge_default, project_mod_replaced=project_mod_override)
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
                # Remediation belongs to admitted daemon authority, never stdio.
                if steam.error_code is not None and not auto_remediate_steam:
                    failed = _compact_result(
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
                    return _annotate_bridge_default(failed, bridge_default, project_mod_replaced=project_mod_override)
            replacement: ClientReplacementDecision | None = None
            client_pids_before: tuple[int, ...] | None = None
            bridge_cause: str | None = None
            if run_id is not None and not preflight:
                extension_status = await runtime.lifecycle_status()
                try:
                    require_extension_run(extension_status, policy, run_id)
                except DayzTestToolError as exc:
                    if exc.code != "run_not_extensible":
                        raise
                    # The server serializes only .code on exceptions. Publish the
                    # known state in an envelope so it survives that boundary.
                    refused = _compact_result(
                        terminal=WorkerTerminal(
                            cleanup_degraded=False,
                            error_code=exc.code,
                            exit_code=1,
                            ok=False,
                            run_id=run_id,
                        ),
                        project=policy.mod,
                        mode=mode,
                        started_at=started_at,
                        artifacts_paths=[],
                        phase="validating",
                        vpp_missing=list(vpp.missing),
                        vpp_warnings=list(vpp.warnings),
                    )
                    refused["run_not_extensible_cause"] = exc.cause
                    return _annotate_bridge_default(refused, bridge_default, project_mod_replaced=project_mod_override)
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
                    # The witness is stamped immediately BEFORE the read, so
                    # it can only be earlier than the evidence, never later: an
                    # error in this direction refuses a replacement, and the
                    # opposite one would authorise a kill with a stale fact.
                    decided_at_ms = int(time.time() * 1000)
                    try:
                        bridge = await runtime.bridge_status_payload()
                    except Exception as exc:
                        # A snapshot we could not read is no answer. It used to
                        # mean "replace"; ronda 2 made it a refusal, because a
                        # transport hiccup is not evidence that a client is hung.
                        # The cause is published next to the stable error_code
                        # so a mute timeout can be told from a 503 without
                        # composing the token that callers already match.
                        bridge = None
                        bridge_cause = _bridge_status_cause(exc)
                    replacement = _decide_client_replacement(
                        record,
                        bridge,
                        budget_s=budget_s,
                        start_stalled=_client_start_stall_evidence(
                            record,
                            _client_profile_roots(
                                policy,
                                mode,
                                _run_row(extension_status, run_id),
                            ),
                        ),
                    )
                    if not replacement.replace:
                        refused = _compact_result(
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
                        # Sibling, not a composed error_code: the token stays
                        # comparable. Absent when the snapshot was read, even
                        # if the row itself was unusable.
                        if (
                            bridge_cause is not None
                            and replacement.reason == _BRIDGE_STATUS_UNKNOWN
                        ):
                            refused["bridge_status_cause"] = bridge_cause
                        return _annotate_bridge_default(refused, bridge_default, project_mod_replaced=project_mod_override)
            if replacement is not None and replacement.replace:
                # H-A2-2 / 79e2: the verdict reached here is two broker hops and
                # one process start away from the kill, so it does not travel as
                # a decision but as a WITNESS that start_run revalidates against
                # the bridge state the daemon owns. Recomposed, not patched: the
                # request is sealed and parsed as a whole.
                raw_request, _witnessed = build_run_request(
                    bundle.sealed_policies,
                    **request_arguments,
                    replace_if_not_polling_since=decided_at_ms,
                )
                bridge_default = _bridge_default_report(extra_mods, raw_request)
            # 296b: the CLIENT profile roots whose dumps can name this
            # launch's death; the snapshot is taken when the launch executes.
            recorded_run = (
                _run_row(extension_status, run_id)
                if run_id is not None and not preflight
                else None
            )
            if recorded_run is not None and _validated_recorded_leaf(
                policy, recorded_run
            ) is None:
                _fail("lifecycle_status_invalid")
            client_dump_roots = (
                _client_profile_roots(policy, mode, recorded_run)
                if not preflight and _mode_starts_client(mode)
                else None
            )
            result = await _execute_request(
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
                client_dump_roots=client_dump_roots,
            )
            if preflight:
                result["preflight_skipped_checks"] = preflight_skipped_checks
            return _annotate_bridge_default(result, bridge_default, project_mod_replaced=project_mod_override)


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


def _wire_safe_generation(value: object) -> bool:
    """A generation reaches the MCP wire in stop envelopes: empty or the minted token only."""
    return isinstance(value, str) and (
        value == ""
        or process_lifecycle._GENERATION_TOKEN.fullmatch(value) is not None
    )


_DIAGNOSTIC_TOKEN = process_lifecycle._GENERATION_TOKEN


def _wire_safe_diagnostic_token(value: object) -> bool:
    """run_id/event/reason/decision on session_status retired-run rows."""
    return (
        isinstance(value, str)
        and _DIAGNOSTIC_TOKEN.fullmatch(value) is not None
    )


def _validated_generation(source: object) -> dict[str, object] | None:
    if not isinstance(source, dict):
        return None
    if any(field not in source for field in _GENERATION_FIELDS):
        return None
    launch = source["daemon_generation_at_launch"]
    current = source["daemon_generation_current"]
    changed = source["generation_changed"]
    if launch is not None and not _wire_safe_generation(launch):
        return None
    if not _wire_safe_generation(current):
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
    if item.get("run_id") != run_id or not _wire_safe_diagnostic_token(run_id):
        return None
    if item.get("state") != "EXITED":
        return None
    for key in ("event", "reason", "decision"):
        if not _wire_safe_diagnostic_token(item.get(key)):
            return None
    generations = _validated_generation(item)
    if generations is None:
        return None
    return {field: item[field] for field in _DIAGNOSTIC_FIELDS}


_RUNS_RETIRED_RECENTLY_LIMIT = 16


def _runs_retired_recently(
    raw: object, client_dump_bindings: object = None
) -> list[dict[str, object]] | None:
    """Wire-safe retired-run list for session_status; None if unreadable.

    ``client_dump_bindings`` is the daemon's map from run_id to the client
    dump snapshot of that launch and of the next one on the same roots.
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    out: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        run_id = item.get("run_id")
        if not isinstance(run_id, str):
            continue
        validated = _validated_diagnostic(item, run_id)
        if validated is None:
            continue
        # 296b: a client that died after dayz_test_run returned succeeded is
        # only named here. Always present, null included.
        validated["client_death_diagnosis"] = diagnose_retired_client_death(
            run_id, client_dump_bindings
        )
        out.append(validated)
        if len(out) >= _RUNS_RETIRED_RECENTLY_LIMIT:
            break
    return out


def _copy_generation(source: dict[str, object]) -> dict[str, object]:
    copied = _validated_generation(source)
    if copied is None:
        raise KeyError("generation")
    return copied


_STOP_ENVELOPE_REASON = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_STOP_ENVELOPE_STATE = re.compile(r"^[A-Z][A-Z_]{0,31}$")


def _failed_stop_envelope(
    run_id: str, error_code: str, source: dict[str, object]
) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "failed",
        "run_id": run_id,
        "error_code": error_code,
        **_copy_generation(source),
    }
    # Persisted free text must not cross the MCP wire.
    state = source.get("state")
    if isinstance(state, str) and _STOP_ENVELOPE_STATE.fullmatch(state):
        payload["state"] = state
    reason = source.get("reason")
    if isinstance(reason, str) and _STOP_ENVELOPE_REASON.fullmatch(reason):
        payload["reason"] = reason
    return payload


def _h14_caller_session(runtime: _Runtime) -> str | None:
    identity = getattr(runtime, "identity", None)
    if identity is None:
        control = getattr(runtime, "_control", None)
        identity = getattr(control, "identity", None) if control is not None else None
    session = getattr(identity, "session_id", None)
    return session if isinstance(session, str) else None


def _h14_owned_stop(runtime: _Runtime, status: object, run_id: str) -> bool:
    row = _run_row(status, run_id)
    if row is None:
        return False
    owner = row.get("owner_session")
    if not isinstance(owner, str) or not owner:
        owner = row.get("owner_session_id")
    if not isinstance(owner, str) or not owner:
        owner = None
    item = {"owner_session": owner}
    if process_lifecycle._caller_owns_run(item, _h14_caller_session(runtime)):
        return True
    # Live D4: after dayz_test_run the row is RUNNING_IDLE with
    # owner_session_id=None (RunRecord invariant). H11 stop adopts that
    # unique ownerless row by lease, then kills it. A different owner
    # stays fail-closed; F0 is not a lifecycle row.
    if owner is not None:
        return False
    return row.get("state") in {"RUNNING_IDLE", "UNRECONCILED"}


@contextmanager
def _h14_exemption(runtime: _Runtime, name: str) -> Iterator[None]:
    control = getattr(runtime, "_control", None)
    exemption = getattr(control, name, None) if control is not None else None
    if not callable(exemption):
        yield
        return
    with exemption():
        yield


@contextmanager
def _h14_stale_policy(runtime: _Runtime) -> Iterator[None]:
    with _h14_exemption(runtime, "stale_policy_exemption"):
        yield


@contextmanager
def _h14_owned_stop_kill(runtime: _Runtime) -> Iterator[None]:
    # Internal lease for the owned-stop kill path only. The MCP tool
    # session_acquire_wait never enters this manager.
    with _h14_exemption(runtime, "owned_stop_kill_exemption"):
        yield


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
            with _h14_stale_policy(runtime):
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
            rest = (
                _h14_owned_stop_kill(runtime)
                if _h14_owned_stop(runtime, status_snapshot, run_id)
                else nullcontext()
            )
            with rest:
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
                        snapshot = await runtime.lifecycle_status()
                    except Exception:
                        return result
                    try:
                        stopped = _exact_run(snapshot, run_id)
                    except DayzTestToolError as exc:
                        # fb-20260918-165857-744a: a reaped/missing row after
                        # the worker already tried to stop is success, not a
                        # leftover DayZ process.
                        if exc.code == "run_not_found":
                            result.update(
                                status="succeeded",
                                phase="completed",
                                error_code=None,
                                cleanup_degraded=False,
                            )
                        return result
                    except Exception:
                        return result
                    processes = stopped.get("processes")
                    processes_gone = isinstance(processes, list) and not processes
                    if stopped.get("state") == "EXITED" or processes_gone:
                        result.update(
                            status="succeeded",
                            phase="completed",
                            error_code=None,
                            cleanup_degraded=False,
                        )
            # 2edd-2: dayz_test_stop always drives the kill path today. Surface
            # that on the MCP envelope so "succeeded" is not read as "exit
            # metrics were collected" (Leaked lines, Destroying game, etc.).
            if result.get("status") == "succeeded":
                result.setdefault("stop_method", "forced_kill")
                result["exit_metrics_valid"] = False
            return result


_TERMINATION_LINE = "--- Termination successfully completed ---"
_CLOSE_TOOL_KEYS = (
    "run_id",
    "graceful",
    "stop_method",
    "graceful_wait_s",
    "exit_metrics_valid",
    "run_retired",
    "stop_required",
    "reason",
    "roles",
)
_CLOSE_ROLE_KEYS = ("role", "termination_line", "rpt_rotated")
_CLOSE_REASON_TOKENS = frozenset(
    {
        "timeout",
        "process_alive",
        "rpt_rotated",
        "role_without_rpt",
        "no_window",
        "run_retired_elsewhere",
        "status_unavailable",
        "close_failed",
    }
)
_CLOSE_ERROR_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_SPLIT_CLOSE_TOKENS = frozenset({"posted", "not_posted", "failed", "unknown"})
_SPLIT_PROCESS_TOKENS = frozenset({"running", "absent", "unknown"})
_CLOSE_POLL_S = 0.05
_CLOSE_REAP_INTERVAL_S = 1.0
_RPT_READ_CHUNK = 1024 * 1024
_RPT_READ_POLL_CAP = 8 * 1024 * 1024
# The logout warning walks each chosen log in full, one 1 MiB block at a time.
# The carry is longer than a connect or logout line (the cited server lines are
# under 200 bytes) so a marker split across a block is still matched. A marker
# that itself is longer than this carry and is also split across a block is
# outside that promise; one that sits inside a single block is still seen.
_PLAYER_LOG_OVERLAP = 8192
_PLAYER_CONNECTED_RE = re.compile(
    br'Player "[^"\n]+" \([^)\n]*\) is connected'
)
_LOGOUT_FINISHED_RE = re.compile(br"\[Logout\]: Player \S+ finished\b")
_PLAYER_CONNECTED_NAME_RE = re.compile(
    br'Player "([^"\n]+)" \([^)\n]*\) is connected'
)
_LOGOUT_FINISHED_ID_RE = re.compile(br"\[Logout\]: Player (\S+) finished\b")
# The connect line names the player. The logout line carries the uid. The
# server RPT state line is what ties those two cited formats together.
_PLAYER_UID_RE = re.compile(
    br"\[StateMachine\]: Player (.+?) \([^)\n]*\buid (\S+?)\)"
)
# fb-20260928-032049-62c5. The logout phase is min(this, graceful_timeout_s)
# and does not consume the termination deadline.
_LOGOUT_WAIT_S = 30.0
_PLAYER_TOKEN_MAX = 256
_PLAYER_STATE_NOT_SAVED = (
    "player_state_not_saved: wait for the periodic players.db save, "
    "or disconnect the client first"
)
_TERMINATION_LINE_BYTES = _TERMINATION_LINE.encode("ascii")
_RPT_OVERLAP = max(0, len(_TERMINATION_LINE_BYTES) - 1)
_ROLE_CLOSE_ORDER = {"client": 0, "server": 1}


@dataclass(frozen=True, slots=True)
class _RptWatch:
    role: str
    path: str
    file_id: int
    offset: int
    overlap: bytes


def _held_lease_token(runtime: _Runtime) -> str | None:
    token = getattr(runtime, "active_lease_token", None)
    if isinstance(token, str) and token:
        return token
    return None


def _parse_graceful_timeout_s(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("bad_args")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0.0 or timeout > 120.0:
        _fail("bad_args")
    return timeout


def _launched_roles(run: dict[str, object]) -> list[str]:
    processes = run.get("processes")
    roles: list[str] = []
    seen: set[str] = set()
    if isinstance(processes, list):
        for item in processes:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            if isinstance(role, str) and role and role not in seen:
                seen.add(role)
                roles.append(role)
    roles.sort(key=lambda role: (_ROLE_CLOSE_ORDER.get(role, 2), role))
    return roles


def _start_role_roots() -> dict[str, str] | None:
    """Role to artifact root from every mode start step. Not a literal table."""
    try:
        records = _mode_records()
    except DayzTestToolError:
        return None
    mapping: dict[str, str] = {}
    for record in records:
        for step in record.steps:
            if step.kind != "start":
                continue
            role = step.role
            root = step.root
            if not isinstance(role, str) or not role:
                continue
            if not isinstance(root, str) or not root:
                continue
            mapping[role] = root
    return mapping


def _close_project_policy(
    run: dict[str, object],
) -> dayz_test_request.RequestProjectPolicy | None:
    """The sealed project policy, loaded the way stop does before artifacts."""
    try:
        with open_approved_launcher("dayz-test-v1") as opened:
            opened.validate_native_pe()
            with secure_launcher.load_verified_bundle(opened) as bundle:
                policies = _semantic_policies(bundle.sealed_policies)
    except Exception:
        return None
    matches = [item for item in policies if run.get("mod") == "@" + item.mod]
    if len(matches) != 1:
        return None
    return matches[0]


def _artifact_candidates(
    policy: dayz_test_request.RequestProjectPolicy,
    leaf: str | None = None,
) -> list[str] | None:
    try:
        chosen = _planned_profile_leaf() if leaf is None else leaf
        return _paths_for_leaf(policy, "all", chosen)
    except DayzTestToolError:
        return None


def _profiles_match_policy(
    policy: dayz_test_request.RequestProjectPolicy, run: dict[str, object]
) -> bool:
    profiles = run.get("profiles")
    if not isinstance(profiles, str) or not profiles:
        return False
    leaf = _validated_recorded_leaf(policy, run)
    if leaf is None:
        return False
    candidates = _artifact_candidates(policy, leaf)
    if candidates is None:
        return False
    normalized = ntpath.normcase(ntpath.normpath(profiles))
    matches = [
        candidate
        for candidate in candidates
        if ntpath.normcase(ntpath.normpath(candidate)) == normalized
    ]
    return len(matches) == 1


def _close_role_folder(
    policy: dayz_test_request.RequestProjectPolicy,
    role: str,
    start_roots: dict[str, str],
    run: dict[str, object] | None = None,
) -> str | None:
    """One role folder. The recorded anchor is validated before this role.

    ``run`` is optional so a caller that already proved the anchor can pass
    it. Without a run there is no recorded leaf to keep, and the folder is
    not invented from the legacy name.
    """
    if run is None:
        return None
    leaf = _validated_recorded_leaf(policy, run)
    if leaf is None:
        return None
    root = start_roots.get(role)
    if not isinstance(root, str) or not root:
        return None
    candidates = _artifact_candidates(policy, leaf)
    if candidates is None:
        return None
    folder = ntpath.join(policy.dev_root, root, leaf)
    folder_norm = ntpath.normcase(ntpath.normpath(folder))
    if not any(
        ntpath.normcase(ntpath.normpath(candidate)) == folder_norm
        for candidate in candidates
    ):
        return None
    return folder


def _list_rpt_files(profiles_dir: Path) -> list[Path] | None:
    try:
        return [
            item
            for item in profiles_dir.iterdir()
            if item.is_file() and item.suffix.casefold() == ".rpt"
        ]
    except OSError:
        return None


def _newest_rpt(profiles_dir: Path) -> Path | None:
    files = _list_rpt_files(profiles_dir)
    if not files:
        return None

    def _mtime(item: Path) -> float:
        try:
            return item.stat().st_mtime
        except OSError:
            return -1.0

    return max(files, key=_mtime)


def _rpt_watch(role: str, path: Path) -> _RptWatch | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return _RptWatch(
        role=role,
        path=str(path),
        file_id=int(stat.st_ino),
        offset=int(stat.st_size),
        overlap=b"",
    )


def _poll_rpt(watch: _RptWatch, path: Path) -> tuple[str, _RptWatch]:
    """Return (termination|rotated|transient|none, updated watch)."""
    try:
        stat = path.stat()
    except OSError:
        return "transient", watch
    file_id = int(stat.st_ino)
    size = int(stat.st_size)
    if str(path) != watch.path or file_id != watch.file_id or size < watch.offset:
        return "rotated", watch
    if size == watch.offset:
        return "none", watch
    found = False
    overlap = watch.overlap
    offset = watch.offset
    remaining = _RPT_READ_POLL_CAP
    try:
        with path.open("rb") as handle:
            while offset < size and remaining > 0:
                chunk_n = min(_RPT_READ_CHUNK, remaining, size - offset)
                handle.seek(offset)
                added = handle.read(chunk_n)
                if not added:
                    break
                remaining -= len(added)
                window = overlap + added
                if _TERMINATION_LINE_BYTES in window:
                    found = True
                overlap = window[-_RPT_OVERLAP:] if _RPT_OVERLAP else b""
                offset += len(added)
                if found:
                    break
    except OSError:
        return "transient", watch
    updated = replace(watch, offset=offset, overlap=overlap)
    return ("termination" if found else "none"), updated


def _close_role_row(
    close_result: dict[str, object], role: str
) -> dict[str, object] | None:
    raw = close_result.get(role)
    if isinstance(raw, dict):
        return raw
    roles = close_result.get("roles")
    if isinstance(roles, list):
        for item in roles:
            if isinstance(item, dict) and item.get("role") == role:
                return item
    return None


def _windows_posted(close_result: dict[str, object], role: str) -> int:
    row = _close_role_row(close_result, role)
    if row is None:
        return 0
    value = row.get("windows_posted")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _retired_event(status: object, run_id: str) -> str | None:
    hits = _retired_for(status, run_id)
    if not hits:
        return None
    events = [
        item.get("event")
        for item in hits
        if isinstance(item.get("event"), str) and item.get("event")
    ]
    if "run_reaped" in events:
        return "run_reaped"
    if events:
        return str(events[0])
    return None


class _PlayerScan:
    __slots__ = ("connected", "finished")

    def __init__(self) -> None:
        self.connected = False
        self.finished = False


def _scan_player_markers(path: Path, found: _PlayerScan) -> bool:
    """Stream one log. True if it was read, or a finished line already decided it.

    Memory stays one block plus `_PLAYER_LOG_OVERLAP` bytes. A finished line
    ends the walk: the warning is then impossible, whether or not a player
    was connected. A connection by itself does not stop the walk, because a
    later finished line would clear it.
    """
    if found.finished:
        return True
    try:
        handle = path.open("rb")
    except OSError:
        return False
    try:
        carry = b""
        while not found.finished:
            try:
                chunk = handle.read(_RPT_READ_CHUNK)
            except OSError:
                return False
            if not chunk:
                break
            if not isinstance(chunk, bytes):
                return False
            window = carry + chunk
            if (
                not found.connected
                and _PLAYER_CONNECTED_RE.search(window) is not None
            ):
                found.connected = True
            if _LOGOUT_FINISHED_RE.search(window) is not None:
                found.finished = True
                return True
            if len(window) > _PLAYER_LOG_OVERLAP:
                carry = window[-_PLAYER_LOG_OVERLAP:]
            else:
                carry = window
        return True
    finally:
        try:
            handle.close()
        except OSError:
            pass


def _script_log_for_server_rpt(rpt: Path) -> Path | None:
    """Newest current-launch script log beside this server RPT.

    Same floor as `_current_launch_logs`: a log touched before the RPT was
    created belongs to an older run. crash_*.log is not a script log.
    Prefer script_*.log; otherwise the newest other non-crash .log.
    """
    try:
        floor = rpt.stat().st_ctime - 2.0
    except OSError:
        return None
    try:
        files = [item for item in rpt.parent.iterdir() if item.is_file()]
    except OSError:
        return None
    current: list[tuple[float, Path]] = []
    for item in files:
        if item.suffix.casefold() != ".log":
            continue
        if item.name.casefold().startswith("crash"):
            continue
        try:
            mtime = item.stat().st_mtime
        except OSError:
            continue
        if mtime < floor:
            continue
        current.append((mtime, item))
    if not current:
        return None
    scripts = [
        pair for pair in current if pair[1].name.casefold().startswith("script")
    ]
    pool = scripts or current
    return max(pool, key=lambda pair: pair[0])[1]


def _player_state_warnings(server_rpt: Path) -> list[str]:
    """Warn when a connected player never reached `[Logout]: Player … finished`.

    The connected line is the server RPT (`Player "…" (…) is connected`).
    The finished line is the server script log. Both files are streamed in
    full. Boolean: one finished line clears the warning and stops the walk.
    An unreadable script log fails toward the warning once the RPT shows a
    connected player. An unreadable RPT that never showed one does not warn.
    `Player connect enabled` is a server flag, not a player.
    """
    found = _PlayerScan()
    rpt_ok = _scan_player_markers(server_rpt, found)
    if found.finished:
        return []
    if not rpt_ok and not found.connected:
        return []
    script = _script_log_for_server_rpt(server_rpt)
    if script is not None:
        _scan_player_markers(script, found)
        if found.finished:
            return []
    if found.connected:
        return [_PLAYER_STATE_NOT_SAVED]
    return []


def _player_token(raw: bytes) -> str | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        return None
    if not text or len(text) > _PLAYER_TOKEN_MAX:
        return None
    if any(ord(char) < 32 for char in text):
        return None
    return text


@dataclass(frozen=True, slots=True)
class _LogoutBoundary:
    """Byte sizes taken before the client close. A later line is verifiable
    only at or past that size on the same file, or in a file the directory
    listing did not contain yet.
    """

    files: dict[str, tuple[int, int]]
    listed: bool


def _logout_boundary(server_rpt: Path | None) -> _LogoutBoundary:
    files: dict[str, tuple[int, int]] = {}
    if server_rpt is None:
        return _LogoutBoundary(files, False)
    listed = False
    try:
        entries = list(server_rpt.parent.iterdir())
        listed = True
    except OSError:
        entries = []
    for item in entries:
        try:
            if not item.is_file():
                continue
            stat = item.stat()
        except OSError:
            continue
        files[str(item)] = (int(stat.st_ino), int(stat.st_size))
    key = str(server_rpt)
    if key not in files:
        try:
            stat = server_rpt.stat()
        except OSError:
            pass
        else:
            files[key] = (int(stat.st_ino), int(stat.st_size))
    return _LogoutBoundary(files, listed)


@dataclass(frozen=True, slots=True)
class _LogEvent:
    path: str
    inode: int
    offset: int
    after_boundary: bool
    seq: int


class _LogoutWatch:
    """Active connections and the logout lines that can save them.

    A player is saved only when the uid linked to that name logs out after
    that player's last connect line and after the pre-close boundary. A line
    from before the boundary, or a uid that was never linked, does not count.
    """

    __slots__ = (
        "boundary",
        "order",
        "connects",
        "uid_links",
        "logouts",
        "files",
        "seq",
        "incomplete",
        "unverified",
    )

    def __init__(self, boundary: _LogoutBoundary) -> None:
        self.boundary = boundary
        self.order: list[str] = []
        self.connects: list[tuple[_LogEvent, str]] = []
        self.uid_links: list[tuple[_LogEvent, str, str]] = []
        self.logouts: list[tuple[_LogEvent, str]] = []
        self.files: dict[str, tuple[int, int, bytes]] = {}
        self.seq = 0
        self.incomplete = False
        self.unverified = False

    def _after_boundary(self, path: str, inode: int, offset: int) -> bool:
        snap = self.boundary.files.get(path)
        if snap is None:
            return self.boundary.listed
        snap_inode, snap_size = snap
        if inode != snap_inode:
            return False
        return offset >= snap_size

    def _event(self, path: str, inode: int, offset: int) -> _LogEvent:
        self.seq += 1
        return _LogEvent(
            path,
            inode,
            offset,
            self._after_boundary(path, inode, offset),
            self.seq,
        )

    def _fold(
        self,
        path: str,
        inode: int,
        window: bytes,
        window_start: int,
        carry_len: int,
    ) -> None:
        for match in _PLAYER_UID_RE.finditer(window):
            if match.end() <= carry_len:
                continue
            name = _player_token(match.group(1))
            uid = _player_token(match.group(2))
            if name and uid:
                event = self._event(path, inode, window_start + match.start())
                self.uid_links.append((event, name, uid))
        for match in _PLAYER_CONNECTED_NAME_RE.finditer(window):
            if match.end() <= carry_len:
                continue
            name = _player_token(match.group(1))
            if not name:
                continue
            event = self._event(path, inode, window_start + match.start())
            self.connects.append((event, name))
            if name not in self.order:
                self.order.append(name)
        for match in _LOGOUT_FINISHED_ID_RE.finditer(window):
            if match.end() <= carry_len:
                continue
            token = _player_token(match.group(1))
            if not token:
                continue
            event = self._event(path, inode, window_start + match.start())
            self.logouts.append((event, token))

    def _remember(
        self, key: str, inode: int, offset: int, carry: bytes
    ) -> None:
        self.files[key] = (inode, offset, carry)

    def consume_file(self, path: Path, deadline: float) -> bool:
        """Read one file up to ``deadline``. False means the budget expired."""
        if time.monotonic() >= deadline:
            self.incomplete = True
            return False
        try:
            stat = path.stat()
        except OSError:
            return True
        key = str(path)
        inode = int(stat.st_ino)
        size = int(stat.st_size)
        snap = self.boundary.files.get(key)
        if snap is not None and snap[0] != inode:
            self.unverified = True
            return True
        prev = self.files.get(key)
        if prev is not None and (prev[0] != inode or size < prev[1]):
            self.unverified = True
            return True
        offset = 0 if prev is None else prev[1]
        carry = b"" if prev is None else prev[2]
        if offset >= size:
            return True
        try:
            handle = path.open("rb")
        except OSError:
            return True
        try:
            while offset < size:
                if time.monotonic() >= deadline:
                    self.incomplete = True
                    self._remember(key, inode, offset, carry)
                    return False
                try:
                    handle.seek(offset)
                    chunk = handle.read(min(_RPT_READ_CHUNK, size - offset))
                except OSError:
                    self._remember(key, inode, offset, carry)
                    return True
                if not chunk or not isinstance(chunk, bytes):
                    self._remember(key, inode, offset, carry)
                    return True
                window_start = offset - len(carry)
                window = carry + chunk
                self._fold(key, inode, window, window_start, len(carry))
                if len(window) > _PLAYER_LOG_OVERLAP:
                    carry = window[-_PLAYER_LOG_OVERLAP:]
                else:
                    carry = window
                offset += len(chunk)
        finally:
            try:
                handle.close()
            except OSError:
                pass
        self._remember(key, inode, offset, carry)
        return True

    def consume_tree(self, server_rpt: Path, deadline: float) -> bool:
        if not self.consume_file(server_rpt, deadline):
            return False
        script = _script_log_for_server_rpt(server_rpt)
        if script is None:
            return not self.incomplete
        return self.consume_file(script, deadline)

    def _later(self, logout: _LogEvent, connect: _LogEvent) -> bool:
        if (
            logout.path == connect.path
            and logout.inode == connect.inode
            and logout.offset > connect.offset
        ):
            return True
        return logout.after_boundary and not connect.after_boundary

    def _active_uids(self, name: str, last: _LogEvent) -> set[str]:
        """The uid of this connection, not an earlier session of the same name."""
        same = [
            (event, uid)
            for event, linked, uid in self.uid_links
            if linked == name and event.path == last.path and event.inode == last.inode
        ]
        before = [
            (event, uid) for event, uid in same if event.offset <= last.offset
        ]
        if before:
            _event, uid = max(before, key=lambda item: (item[0].offset, item[0].seq))
            return {uid}
        after = [(event, uid) for event, uid in same if event.offset > last.offset]
        if after:
            _event, uid = min(after, key=lambda item: (item[0].offset, item[0].seq))
            return {uid}
        return set()

    def _logout_finished(self, name: str) -> bool:
        connects = [event for event, linked in self.connects if linked == name]
        if not connects:
            return False
        last = max(
            connects,
            key=lambda event: (event.after_boundary, event.offset, event.seq),
        )
        uids = self._active_uids(name, last)
        if not uids:
            return False
        for event, token in self.logouts:
            if token not in uids or not event.after_boundary:
                continue
            if self._later(event, last):
                return True
        return False

    def rows(self) -> list[dict[str, object]]:
        return [
            {"player": name, "logout_finished": self._logout_finished(name)}
            for name in self.order
        ]


async def _wait_for_connected_logouts(
    server_rpt: Path | None,
    timeout_s: float,
    boundary: _LogoutBoundary,
    *,
    wait: bool,
) -> tuple[list[dict[str, object]], float, bool]:
    """Return (players, wait_s, timed_out).

    The deadline is armed before any log byte is read and checked between
    blocks. No connected player skips the wait. A connected player is saved
    only by their linked uid's logout after the pre-close boundary. Without
    that link the wait runs to the cap and the caller warns.
    """
    if server_rpt is None:
        return [], 0.0, False
    budget = min(_LOGOUT_WAIT_S, timeout_s)
    started = time.monotonic()
    deadline = started + budget
    watch = _LogoutWatch(boundary)

    def _elapsed() -> float:
        return max(0.0, time.monotonic() - started)

    if not watch.consume_tree(server_rpt, deadline) or watch.unverified:
        return watch.rows(), _elapsed(), True
    rows = watch.rows()
    if not rows or not wait:
        return rows, 0.0, False
    while True:
        rows = watch.rows()
        if rows and all(item["logout_finished"] is True for item in rows):
            return rows, _elapsed(), False
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            return rows, _elapsed(), True
        await asyncio.sleep(min(_CLOSE_POLL_S, remaining))
        if not watch.consume_tree(server_rpt, deadline) or watch.unverified:
            return watch.rows(), _elapsed(), True


def _split_client_then_server(roles: list[str]) -> bool:
    return set(roles) == {"client", "server"}


def _close_error_token(value: object) -> str:
    if isinstance(value, str) and _CLOSE_ERROR_TOKEN.fullmatch(value):
        return value
    return "lifecycle_close_unavailable"


@dataclass(frozen=True, slots=True)
class _RoleCloseFailure:
    code: str
    query_state: bool


async def _checked_role_close(fn: object, *args: object) -> dict[str, object]:
    if not callable(fn):
        _fail("lifecycle_close_unavailable")
    close_result = await fn(*args)
    if not isinstance(close_result, dict):
        _fail("lifecycle_close_unavailable")
    if close_result.get("error"):
        code = close_result.get("error")
        _fail(
            str(code) if isinstance(code, str) and code else "lifecycle_close_unavailable"
        )
    return close_result


async def _attempt_role_close(
    fn: object, *args: object
) -> tuple[dict[str, object] | None, _RoleCloseFailure | None]:
    """Close one role. A failure is data, not an exception for the caller.

    A coded rejection can be followed by a status query. A transport
    exception cannot: another call may be the outage itself.
    """
    if not callable(fn):
        return None, _RoleCloseFailure("lifecycle_close_unavailable", False)
    try:
        close_result = await fn(*args)
    except DayzTestToolError as exc:
        return None, _RoleCloseFailure(_close_error_token(exc.code), True)
    except Exception:
        return None, _RoleCloseFailure("lifecycle_close_unavailable", False)
    if not isinstance(close_result, dict):
        return None, _RoleCloseFailure("lifecycle_close_unavailable", False)
    if close_result.get("error"):
        return None, _RoleCloseFailure(
            _close_error_token(close_result.get("error")), True
        )
    return close_result, None


def _role_process_state(status: dict[str, object], run_id: str, role: str) -> str:
    """``running`` or ``absent`` when the snapshot says so, else ``unknown``."""
    run = _run_row(status, run_id)
    if run is not None:
        processes = run.get("processes")
        if not isinstance(processes, list):
            return "unknown"
        for item in processes:
            if isinstance(item, dict) and item.get("role") == role:
                return "running"
        return "absent"
    if _retired_event(status, run_id) is not None:
        return "absent"
    return "unknown"


def _merge_role_close(
    run_id: str, parts: list[dict[str, object]]
) -> dict[str, object]:
    merged: dict[str, object] = {"run_id": run_id}
    for part in parts:
        for key, value in part.items():
            if key == "run_id" or not isinstance(value, dict):
                continue
            merged[key] = value
    return merged


def _whitelist_close_result(payload: dict[str, object]) -> dict[str, object]:
    roles_raw = payload.get("roles")
    roles: list[dict[str, object]] = []
    if isinstance(roles_raw, list):
        for item in roles_raw:
            if not isinstance(item, dict):
                continue
            roles.append({key: item.get(key) for key in _CLOSE_ROLE_KEYS})
    result = {key: payload.get(key) for key in _CLOSE_TOOL_KEYS}
    result["roles"] = roles
    result["stop_method"] = "orderly_close"
    warnings = payload.get("warnings")
    if (
        isinstance(warnings, list)
        and warnings
        and all(isinstance(item, str) and item for item in warnings)
    ):
        result["warnings"] = list(warnings)
    order = payload.get("close_order")
    if (
        isinstance(order, list)
        and order
        and all(isinstance(item, str) and item in {"client", "server"} for item in order)
    ):
        result["close_order"] = list(order)
    wait = payload.get("logout_wait_s")
    if isinstance(wait, float) and math.isfinite(wait) and wait >= 0.0:
        result["logout_wait_s"] = wait
    players = payload.get("logout_players")
    if isinstance(players, list):
        clean: list[dict[str, object]] = []
        reportable = True
        for item in players:
            if not isinstance(item, dict):
                reportable = False
                break
            name = item.get("player")
            seen = item.get("logout_finished")
            if not isinstance(name, str) or not name or not isinstance(seen, bool):
                reportable = False
                break
            clean.append({"player": name, "logout_finished": seen})
        if reportable:
            result["logout_players"] = clean
    error = payload.get("error")
    if isinstance(error, str) and _CLOSE_ERROR_TOKEN.fullmatch(error):
        result["error"] = error
    split_roles = payload.get("close_roles")
    if isinstance(split_roles, list):
        clean_roles: list[dict[str, object]] = []
        reportable = True
        for item in split_roles:
            if not isinstance(item, dict):
                reportable = False
                break
            role = item.get("role")
            close = item.get("close")
            process = item.get("process")
            if (
                role not in {"client", "server"}
                or close not in _SPLIT_CLOSE_TOKENS
                or process not in _SPLIT_PROCESS_TOKENS
            ):
                reportable = False
                break
            clean_roles.append(
                {"role": role, "close": close, "process": process}
            )
        if reportable and clean_roles:
            result["close_roles"] = clean_roles
    return result


def _reported_close_state(close_result: object, role: str) -> str:
    """``posted`` only when that role's ``windows_posted`` is a positive int.

    A confirmed zero is ``not_posted``. No row, a bool, or any other count
    is ``unknown``: the answer must not claim a WM_CLOSE was sent.
    """
    if not isinstance(close_result, dict):
        return "unknown"
    row = _close_role_row(close_result, role)
    if row is None:
        return "unknown"
    value = row.get("windows_posted")
    if isinstance(value, bool) or not isinstance(value, int):
        return "unknown"
    if value > 0:
        return "posted"
    if value == 0:
        return "not_posted"
    return "unknown"


async def _partial_split_close(
    status_fn: object,
    run_id: str,
    published: list[str],
    players: list[dict[str, object]],
    logout_wait_s: float,
    timed_out: bool,
    failure: _RoleCloseFailure,
    client_close: object,
) -> dict[str, object]:
    """The server close did not land. The client state comes from its reply.

    The answer keeps the original error, says the stop is still required,
    and reports each role's process as running, absent, or unknown.
    """
    client_process = "unknown"
    server_process = "unknown"
    run_retired = False
    if failure.query_state and callable(status_fn):
        status: object = None
        try:
            status = await status_fn()
        except Exception:
            status = None
        if isinstance(status, dict) and not status.get("error"):
            client_process = _role_process_state(status, run_id, "client")
            server_process = _role_process_state(status, run_id, "server")
            run_retired = _retired_event(status, run_id) is not None
    payload: dict[str, object] = {
        "run_id": run_id,
        "graceful": False,
        "stop_method": "orderly_close",
        "graceful_wait_s": 0.0,
        "exit_metrics_valid": False,
        "run_retired": run_retired,
        "stop_required": True,
        "reason": "close_failed",
        "roles": [
            {"role": role, "termination_line": False, "rpt_rotated": False}
            for role in published
        ],
        "close_order": ["client", "server"],
        "logout_wait_s": float(logout_wait_s),
        "logout_players": players,
        "error": failure.code,
        "close_roles": [
            {
                "role": "client",
                "close": _reported_close_state(client_close, "client"),
                "process": client_process,
            },
            {"role": "server", "close": "failed", "process": server_process},
        ],
    }
    if timed_out:
        payload["warnings"] = [_PLAYER_STATE_NOT_SAVED]
    return _whitelist_close_result(payload)


async def execute_dayz_test_close(
    runtime: _Runtime,
    run_id: str,
    *,
    graceful_timeout_s: object = 45,
    progress_cb: _ProgressCallback | None = None,
) -> dict[str, object]:
    timeout_s = _parse_graceful_timeout_s(graceful_timeout_s)
    if _held_lease_token(runtime) is None:
        _fail("lease_required")
    if progress_cb is not None:
        await progress_cb("validating", None)
    status_fn = getattr(runtime, "lifecycle_status", None)
    if not callable(status_fn):
        _fail("lifecycle_status_invalid")
    status_snapshot = await status_fn()
    run = _run_row(status_snapshot, run_id)
    if run is None:
        _fail("run_not_found")
    process_roles = _launched_roles(run)
    start_roots = _start_role_roots()
    policy = _close_project_policy(run)
    published = [
        role
        for role in process_roles
        if start_roots is not None and role in start_roots
    ]
    watches: dict[str, _RptWatch] = {}
    missing_rpt: list[str] = []
    if (
        policy is None
        or start_roots is None
        or not _profiles_match_policy(policy, run)
    ):
        missing_rpt.extend(process_roles)
    else:
        for role in process_roles:
            if role not in start_roots:
                missing_rpt.append(role)
                continue
            folder = _close_role_folder(policy, role, start_roots, run)
            if folder is None:
                missing_rpt.append(role)
                continue
            listed = _list_rpt_files(Path(folder))
            newest = None if listed is None else _newest_rpt(Path(folder))
            if newest is None:
                missing_rpt.append(role)
                continue
            watch = _rpt_watch(role, newest)
            if watch is None:
                missing_rpt.append(role)
                continue
            watches[role] = watch
    close_fn = getattr(runtime, "lifecycle_close", None)
    if not callable(close_fn):
        _fail("lifecycle_close_unavailable")
    logout_report: dict[str, object] | None = None
    if _split_client_then_server(process_roles):
        close_roles = getattr(runtime, "lifecycle_close_roles", None)
        server_watch = watches.get("server")
        server_rpt = None if server_watch is None else Path(server_watch.path)
        boundary = _logout_boundary(server_rpt)
        client_close = await _checked_role_close(close_roles, run_id, ["client"])
        client_row = _close_role_row(client_close, "client")
        players, logout_wait_s, timed_out = await _wait_for_connected_logouts(
            server_rpt,
            timeout_s,
            boundary,
            wait=client_row is None or _windows_posted(client_close, "client") > 0,
        )
        server_close, server_failure = await _attempt_role_close(
            close_roles, run_id, ["server"]
        )
        if server_failure is not None or server_close is None:
            return await _partial_split_close(
                status_fn,
                run_id,
                published,
                players,
                logout_wait_s,
                timed_out,
                server_failure
                or _RoleCloseFailure("lifecycle_close_unavailable", False),
                client_close,
            )
        close_result = _merge_role_close(run_id, [client_close, server_close])
        logout_report = {
            "close_order": ["client", "server"],
            "logout_wait_s": logout_wait_s,
            "logout_players": players,
            "timed_out": timed_out,
        }
    else:
        close_result = await _checked_role_close(close_fn, run_id)
    wait_started = time.monotonic()
    deadline = wait_started + timeout_s
    reap_fn = getattr(runtime, "lifecycle_reap", None)
    retired_event: str | None = None
    status_unavailable = False
    last_reap_at: float | None = None
    role_state: dict[str, dict[str, bool]] = {
        role: {"termination_line": False, "rpt_rotated": False}
        for role in published
    }
    for role in missing_rpt:
        if start_roots is not None and role in start_roots:
            role_state.setdefault(
                role, {"termination_line": False, "rpt_rotated": False}
            )

    no_window = False
    for role in published:
        if _close_role_row(close_result, role) is None:
            continue
        if _windows_posted(close_result, role) == 0:
            no_window = True
            break

    def _past_deadline() -> bool:
        return time.monotonic() >= deadline

    def _read_rpts() -> None:
        for role, watch in list(watches.items()):
            state = role_state.setdefault(
                role, {"termination_line": False, "rpt_rotated": False}
            )
            if state["termination_line"] or state["rpt_rotated"]:
                continue
            profiles_dir = Path(watch.path).parent
            listed = _list_rpt_files(profiles_dir)
            if listed is None:
                continue
            newest = _newest_rpt(profiles_dir) if listed else None
            target = newest if newest is not None else Path(watch.path)
            outcome, updated = _poll_rpt(watch, target)
            watches[role] = updated
            if outcome == "termination":
                state["termination_line"] = True
            elif outcome == "rotated":
                state["rpt_rotated"] = True

    async def _observe() -> None:
        nonlocal retired_event, status_unavailable, last_reap_at
        if _past_deadline():
            return
        try:
            current = await status_fn()
        except Exception:
            status_unavailable = True
            return
        event = _retired_event(current, run_id)
        if event is not None:
            retired_event = event
        _read_rpts()
        if retired_event == "run_reaped":
            _read_rpts()
            return
        if retired_event is not None:
            return
        if not callable(reap_fn) or _past_deadline():
            return
        now = time.monotonic()
        if last_reap_at is not None and (now - last_reap_at) < _CLOSE_REAP_INTERVAL_S:
            return
        try:
            await reap_fn(run_id)
        except Exception:
            pass
        last_reap_at = time.monotonic()
        if _past_deadline():
            return
        try:
            after = await status_fn()
        except Exception:
            status_unavailable = True
            return
        event = _retired_event(after, run_id)
        if event is not None:
            retired_event = event
        if retired_event == "run_reaped":
            _read_rpts()

    if not no_window:
        await _observe()
        while (
            not status_unavailable
            and retired_event is None
            and time.monotonic() < deadline
        ):
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                break
            await asyncio.sleep(min(_CLOSE_POLL_S, remaining))
            await _observe()

    graceful_wait_s = max(0.0, time.monotonic() - wait_started)
    run_reaped = retired_event == "run_reaped"
    run_retired_elsewhere = (
        retired_event is not None and retired_event != "run_reaped"
    )
    run_retired = retired_event is not None
    all_new_lines = bool(published) and all(
        role_state.get(role, {}).get("termination_line") for role in published
    )
    any_rotated = any(
        role_state.get(role, {}).get("rpt_rotated") for role in published
    )
    any_missing = bool(missing_rpt)
    graceful = bool(
        published
        and all_new_lines
        and run_reaped
        and not any_missing
        and not any_rotated
        and not status_unavailable
    )
    reason: str | None = None
    if not graceful:
        if status_unavailable:
            reason = "status_unavailable"
        elif run_retired_elsewhere:
            reason = "run_retired_elsewhere"
        elif any_missing:
            reason = "role_without_rpt"
        elif any_rotated:
            reason = "rpt_rotated"
        elif no_window:
            reason = "no_window"
        elif all_new_lines and not run_reaped:
            reason = "process_alive"
        else:
            reason = "timeout"
    if reason is not None and reason not in _CLOSE_REASON_TOKENS:
        reason = "timeout"
    roles_out = [
        {
            "role": role,
            "termination_line": bool(
                role_state.get(role, {}).get("termination_line")
            ),
            "rpt_rotated": bool(role_state.get(role, {}).get("rpt_rotated")),
        }
        for role in published
    ]
    payload: dict[str, object] = {
        "run_id": run_id,
        "graceful": graceful,
        "stop_method": "orderly_close",
        "graceful_wait_s": graceful_wait_s,
        "exit_metrics_valid": graceful,
        "run_retired": run_retired,
        "stop_required": (not run_retired) or status_unavailable,
        "reason": reason,
        "roles": roles_out,
    }
    # fb-20260928-032049-62c5 option 1: client, then the logout line, then
    # the server. The warning is that wait timing out. One-role closes keep
    # the boolean warning from #136.
    if logout_report is not None:
        payload["close_order"] = logout_report["close_order"]
        payload["logout_wait_s"] = logout_report["logout_wait_s"]
        payload["logout_players"] = logout_report["logout_players"]
        if graceful and logout_report["timed_out"] is True:
            payload["warnings"] = [_PLAYER_STATE_NOT_SAVED]
    elif graceful:
        server_watch = watches.get("server")
        if server_watch is not None:
            warnings = _player_state_warnings(Path(server_watch.path))
            if warnings:
                payload["warnings"] = warnings
    return _whitelist_close_result(payload)

