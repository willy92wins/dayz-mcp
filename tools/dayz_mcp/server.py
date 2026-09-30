from __future__ import annotations

import asyncio
import base64
import inspect
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, AsyncIterator, Awaitable, Callable, Iterator, Literal

import anyio
from mcp.server.fastmcp import Context, FastMCP, Image
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr

import mcp_capture
from dayz_mcp import (
    core,
    daemon,
    daemon_credential,
    dayz_test_modes,
    dayz_test_tool,
    host_config,
    inbox,
    orphan_guard,
    playbook_tool as playbook_tool_mod,
    registry_lock,
    tool_pack as tool_pack_mod,
    ui_dialog as ui_dialog_mod,
)
from dayz_mcp.control_client import (
    ControlClient,
    ControlClientError,
    ControlIdentity,
    VerifiedErrorBody,
)
from dayz_mcp.accredited_daemon_transport import AccreditedTransportError
from dayz_mcp.daemon_policy import load_normal_daemon_policy
from dayz_mcp.camera_restore import (
    RESTORE_CAMERA_PROBE_CMD,
    RESTORE_NOT_VERIFIED,
    restore_camera_verdict as _restore_camera_verdict,
)
from dayz_mcp.core import (
    ECE_CREATEPHYSICS,
    ECE_EQUIP_ATTACHMENTS,
    ECE_INITAI,
    ECE_KEEPHEIGHT,
    ECE_KEEPHEIGHT_NOLIFETIME,
    ECE_NOLIFETIME,
    ECE_NOPERSISTENCY_WORLD,
    ECE_PLACE_ON_SURFACE,
    ECE_TRACE,
    EXPECTED_BRIDGE_VERSION,
    WORLD_SPAWN_ALLOWED_EXTRA_FLAGS,
    is_allowed_spawn_flags,
)
from dayz_mcp.effective_schema_core import project_server_config_identity
from dayz_mcp.tool_registry_fingerprint import capture_registry_snapshot
from dayz_mcp.agent_loop import PUBLIC_NEXT_TOOLS, next_step, ok_next_step, with_next_step
from dayz_mcp.knowledge import register_knowledge_tools
from dayz_mcp.occupant_seat import occupant_client_seated
from dayz_mcp.peer_liveness import (
    PEER_STALE_S,
    client_peer_probeable as _client_peer_probeable,
    peer_is_live as _peer_is_live,
)
from dayz_mcp.server_freshness import (
    REMEDIATION as _TOOL_REGISTRY_REMEDIATION,
    ServerSourceWatch,
    install_result_freshness,
    loaded_source_files,
    schema_signal,
    source_stale,
)
from dayz_mcp import log_tail, result_prune
from dayz_mcp.loopback import (
    INPUT_NAME_MAX_CHARS,
    INPUT_TRIGGER_DIK_MAX,
    INPUT_TRIGGER_HOLD_MAX_S,
    INPUT_TRIGGER_PRESS_MAX_TTL_S,
    MAX_CLIENT_DUMP_RUN_IDS,
    LoopbackServer,
    is_printable_input_name,
    read_key,
)
from dayz_mcp.server_cli import CLIENT_PLATFORM_ALIASES, build_server_parser
from dayz_mcp.process_lifecycle import (
    ADOPTION_REVERT_PENDING,
    caller_launched_row,
    caller_may_adopt_ownerless,
    empty_box,
    occupancy_error_fields,
    protection_fields,
    takeover_target_run_id,
)
from dayz_mcp import session_handoff
from dayz_mcp.mcp_supervisor import Supervisor
from dayz_mcp.session_coordination import (
    READ_ONLY_COMMANDS,
    ClientIdentity,
    command_requires_lease,
)
from dayz_mcp.vehicle_trace import normalize_bridge_result, normalize_request
from dayz_mcp import anim_timeline as anim_timeline_contract

# Import the production lazy closures before freezing their source baseline.
# These imports bind definitions only; they do not launch or acquire anything.
if os.name == "nt":
    from dayz_mcp import native_bundle, native_launcher_backend
playbook_tool_mod.load_runner()
# Freeze at import, before build_app can be delayed or repeated.
_SERVER_SOURCES = ServerSourceWatch(loaded_source_files())

UiClickMode = Literal["direct", "complete"]
InventoryAttachDest = Literal["attachment", "cargo"]
UiReloadLayoutMode = Literal["reload", "close"]
TelemetryReadMode = Literal["object_at", "fixture_jsonl"]
WeaponSightsMode = Literal["ironsights", "optics", "none"]
VehicleDoorMode = Literal["read", "open", "close"]
InputTriggerKind = Literal["key", "input"]
InputTriggerEntry = Literal["game", "mission"]
InputTriggerPhase = Literal["click", "hold", "press", "release"]

_CLOSED_SCHEMA_TOOLS: tuple[str, ...] = (
    "pipeline_resolve",
    "capture_screenshot",
    "ui_click",
    "ui_reload_layout",
    "dayz_knowledge_status",
    "dayz_knowledge_prepare",
    "dayz_test_run",
)


DEFAULT_TOOL_TIMEOUT_S = 15.0
# Upper bound for per-tool bridge timeouts. 300 s, not 120 s, because
# dayz_test_run in mode=all measured 28.6 s and the operation pin already caps
# at MAX_OPERATION_PIN_S=300.0 (session_coordination).
MAX_TIMEOUT_S = 300.0
# The liveness probe runs AFTER the caller's budget is already spent, and inside
# the tool lock, so it gets its own short ceiling instead of the 5.0 s default of
# _request_once. A slow daemon degrades the message; it must not extend the call.
LIVENESS_STATUS_TIMEOUT_S = 1.0
# At most one lease-carrier rewrite per this many seconds
# (ClientRuntime._refresh_lease_carrier). Heartbeats and commands the daemon
# just renewed share it, so a burst does not fsync once per call.
_CARRIER_REFRESH_S = 5.0
POLL_INTERVAL_S = 0.05
WAIT_FOR_MAX_TIMEOUT_S = 600.0
BOX_WAIT_MAX_S = 600.0
BOX_WAIT_POLL_S = 1.0
BOX_WAIT_MIN_POLL_S = 0.05
BOX_CLAIM_HEARTBEAT_S = 30.0
WAIT_FOR_MIN_POLL_INTERVAL_S = 0.5
WAIT_FOR_CONDITIONS = frozenset({
    "players_at_least",
    "players_at_most",
    "log_matches",
    "entity_state",
})
TELEMETRY_READ_MODES = frozenset({"object_at", "fixture_jsonl"})
LEASE_REQUIRED_RECIPE = with_next_step(
    "lease_required: call session_acquire_wait(purpose=...)",
    "session_acquire_wait",
)
LEASE_EXPIRED_RECIPE = with_next_step(
    "lease_expired: the lease timed out. A run left RUNNING_IDLE can be "
    "re-adopted with session_acquire_wait while its grace lasts",
    "session_acquire_wait",
)
LEASE_INVALID_RECIPE = with_next_step(
    "lease_invalid: token was never valid for this client",
    "session_status",
)
TAKEOVER_REQUIRED = "takeover_required"
RETAIL_QUARANTINE_RECIPE = (
    "retail_quarantine: a DayZ retail process is running on this machine; "
    "mutations are blocked until no DayZ retail process is running"
)
_RETAIL_QUARANTINE_REASONS = frozenset({
    "no_probe",
    "probe_error",
    "probe_malformed",
    "probe_unknown",
    "retail_present",
})
LEASE_TOOL_LINE = "Requires a lease (session_acquire_wait)."

# flags=0 stays the surface default. This mask is the example to add when
# the caller wants the object left out of the world save.
_WORLD_SPAWN_NOPERSIST_FLAGS = ECE_PLACE_ON_SURFACE | ECE_NOPERSISTENCY_WORLD
WORLD_SPAWN_FLAGS_LINE = (
    "flags=0 uses ECE_PLACE_ON_SURFACE. Allowed non-zero values are the exact "
    "pair ECE_CREATEPHYSICS|ECE_TRACE, or any value that includes "
    "ECE_PLACE_ON_SURFACE plus extras from "
    "ECE_INITAI|ECE_EQUIP_ATTACHMENTS|ECE_NOPERSISTENCY_WORLD|ECE_CREATEPHYSICS. "
    "ECE_KEEPHEIGHT (524288) and ECE_NOLIFETIME (4194304), alone or together "
    "(flags=4718592), return bad_flags because IsAllowedSpawnFlags does not "
    "admit those bits (KEEPHEIGHT skips surface placement; NOLIFETIME is not "
    "in the extra allowlist). Unknown bits also return bad_flags. "
    "Without ECE_NOPERSISTENCY_WORLD the server can save the object and "
    "return it in a later run, where its object_id is no longer valid. "
    f"Add that flag to keep it out of the world save, for example "
    f"flags={_WORLD_SPAWN_NOPERSIST_FLAGS} "
    "(ECE_PLACE_ON_SURFACE|ECE_NOPERSISTENCY_WORLD)."
)
# world_spawn lifetime_s upper bound, in seconds. Mirrors SPAWN_LIFETIME_MAX_S in
# addon/scripts/5_Mission/MCPBridge.c: 3888000 (45 days) is the largest
# <lifetime> in the dayzOffline.chernarusplus db/types.xml.
WORLD_SPAWN_LIFETIME_MAX_S = 3888000.0
DAEMON_AUTOSPAWN_DISABLED = (
    "daemon_autospawn_disabled: start the daemon (--daemon) or omit "
    "--no-daemon-autospawn"
)
DAEMON_AUTOSPAWN_ALREADY = (
    "daemon_unavailable: autospawn already attempted (start the daemon "
    "or omit --no-daemon-autospawn)"
)
# Published ready.reason set. The bridge_status description derives its list
# from this set plus _FENCE_BLOCK_READY.values() at build time and declares it
# OPEN: consumers validate by shape, never against a copied whitelist.
# *_legacy_blocked / version_mismatch only after that peer has polled at least
# once (last_poll_age_s is not None).
READY_REASONS = frozenset({
    "ready",
    "no_run",
    "server_poll_stale",
    "client_not_polling",
    "client_legacy_blocked",
    "version_mismatch",
    "arg_contract_mismatch",
    "capabilities_unknown",
    "binding_ambiguous",
    "unbound_after_restart",
    "binding_not_ready",
    "binding_retired",
    "instance_unknown",
    "instance_unattributed",
    "instance_role_mismatch",
    "instance_malformed",
    "instance_peer_collision",
    "legacy_unbound",
    "creation_time_unreadable",
})
# Public tools named when ready is false. Never lifecycle_status.
# OK payloads do not get next_step here.
_READY_NEXT_TOOLS: dict[str, str] = {
    "no_run": "dayz_test_run",
    "binding_not_ready": "bridge_status",
    "server_poll_stale": "bridge_status",
    "client_not_polling": "bridge_status",
    "binding_retired": "session_status",
    "unbound_after_restart": "dayz_test_run",
    "legacy_unbound": "dayz_test_run",
    "instance_unknown": "dayz_test_run",
    "instance_malformed": "dayz_test_run",
    "instance_role_mismatch": "session_status",
    "instance_peer_collision": "session_status",
    "instance_unattributed": "bridge_status",
    "creation_time_unreadable": "bridge_status",
    "binding_ambiguous": "session_status",
    "version_mismatch": "bridge_status",
    "arg_contract_mismatch": "bridge_status",
    "capabilities_unknown": "bridge_status",
    "client_legacy_blocked": "dayz_test_run",
}
WAIT_FOR_LOOKBACK_MAX = 2000
# lookback_from="launch" scans each current-launch log from byte 0 instead of
# rewinding a line count. Measured 2026-08-21: the "[DayZ-MCP] config loaded"
# line a caller waits on after a launch sat at line 20 of a 132,632-line script
# log, and was absent from that launch's RPT entirely (0 hits in 165,669 lines,
# because the RPT only starts mirroring SCRIPT output ~16 s in). No value of
# lookback_lines reaches that, so waiting on a startup line could not work.
WAIT_FOR_LOOKBACK_FROM = frozenset({"lines", "launch"})
# Ceiling for one launch scan. It runs under tool_lock, so it is bounded rather
# than open-ended, and a file past the ceiling is reported as scan_truncated
# instead of being quietly half-read.
WAIT_FOR_LAUNCH_SCAN_MAX_BYTES = 64 * 1024 * 1024
_LAUNCH_SCAN_CHUNK_BYTES = 1024 * 1024
_REMOTE_ERROR_CODES = frozenset({
    "audit_failed",
    "bad_args",
    "bad_content_length",
    "bad_id",
    "bad_json",
    "bad_json_type",
    "bad_ms",
    "bad_ms_range",
    "bad_operation_timeout",
    "bad_operation_id",
    "bad_peer",
    "bad_purpose",
    "bad_wait_timeout",
    "exec_not_allowed",
    "identity_mismatch",
    "invalid_identity",
    "lease_expired",
    "lease_invalid",
    "lease_required",
    "not_found",
    "not_whitelisted",
    "queue_full",
    "retail_quarantine",
    "coordination_audit_fault",
    "coordination_repairing",
    "operation_cancelled",
    "operation_conflict",
    "operation_tombstones_saturated",
    "session_releasing",
    "ticket_expired",
    "ticket_invalid",
    "unauthorized",
    "version_blocked",
    "legacy_unbound",
    "instance_malformed",
    "instance_unknown",
    "unbound_after_restart",
    "instance_role_mismatch",
    "instance_ambiguous",
    "instance_unattributed",
    "binding_not_ready",
    "binding_retired",
    "instance_peer_collision",
    "instance_config_missing",
    "instance_config_mismatch",
    "creation_time_unreadable",
    # run-fence refusals emitted by loopback._enqueue_run_rejection and loopback._enqueue_command.
    "run_not_owned",
    "run_state_unavailable",
    "run_protected",
    "enqueue_cancelled",
    # lease-grant race surfaced by session_coordination._validate_token_locked on /enqueue.
    "session_granting",
})
_STALE_TICKET_ERRORS = frozenset({"ticket_expired", "ticket_invalid"})
_STALE_LEASE_ERRORS = frozenset({"lease_expired", "lease_invalid"})
_ENQUEUE_HINT_MAX_CHARS = 240
# ready.reason tokens that are not /enqueue whitelist codes. Treating them as
# unknown collapses the only identifier a caller can use to tell a loading
# client from a real enqueue failure -- if that token actually travelled on
# the enqueue payload. Global /status readiness is computed separately and
# does not by itself prove a given /enqueue was refused with this code.
_PUBLISHED_NOT_READY_CODES = frozenset(
    reason
    for reason in READY_REASONS
    if reason != "ready" and reason not in _REMOTE_ERROR_CODES
)
_WAIT_FOR_RETRYABLE_NOT_READY = frozenset({
    "game_not_ready:reason=server_poll_stale",
    "game_not_ready:reason=client_not_polling",
    "game_not_ready:reason=binding_not_ready",
    "server_poll_stale",
    "client_not_polling",
    "binding_not_ready",
})


def _carriable_hint(payload: object) -> str | None:
    """Accredited-daemon prose travels only beside a whitelist code, bounded; it
    never replaces the code."""
    if not isinstance(payload, dict):
        return None
    hint = payload.get("hint")
    if type(hint) is not str:
        return None
    if not 0 < len(hint) <= _ENQUEUE_HINT_MAX_CHARS:
        return None
    if hint != hint.strip():
        return None
    if not hint.isprintable():
        return None
    return hint


# Constant ValueError tokens raised along the dayz_test request path, mapped to
# caller-facing codes. The tokens are fixed strings that carry no host paths, so
# translating them keeps host paths off the wire while replacing a bare
# "dayz_test_failed:ValueError". The parse rejects before accreditation runs
# (native_launcher_transaction.py:108 vs :112), so both stages need an entry.
# Any ValueError not listed here keeps propagating untouched.
_DAYZ_TEST_VALUE_ERROR_CODES = {
    "invalid_dayz_test_path_authority": "bad_mod_authority",
    "invalid_dayz_test_policy": "launcher_policy_invalid",
    "invalid_dayz_test_request": "bad_dayz_test_request",
    "invalid_run_id": "bad_run_id",
    "client_requires_run_id": "client_requires_run_id",
    "server_all_forbid_run_id": "server_all_forbid_run_id",
    # The run-manifest side of the same path. None of these were mapped, so a
    # launch that got past the parse failed as a bare "dayz_test_failed:ValueError"
    # with nothing to search for. Reported 2026-08-21 by a session that spent the
    # diagnosis by hand on a cause `python -m dayz_mcp.doctor` names in one line
    # (RUN_PREPRUNE_BACKUP_SLOTS_EXHAUSTED). The codes stay constant strings: the
    # point is a searchable name on the wire, not the offending path.
    "invalid_native_launcher_transaction": "launcher_transaction_invalid",
    "invalid_process_record": "process_record_invalid",
    "invalid_run_manifest": "run_manifest_invalid",

    "invalid_run_record": "run_record_invalid",
    "run_exists": "run_exists",
    "run_not_found": "run_not_found",
}


def _log_opaque_failure(runtime: Any, tool: str, exc: BaseException) -> None:
    """Write the dropped cause to the LOCAL log, never to the wire.

    The wire carries the exception type alone because the message can hold host
    paths, and that protection stays. What was missing is the other half: nothing
    printed the cause anywhere, so `dayz_test_failed:ValueError` reached the caller
    with the answer one frame away in ``__cause__``. Two sessions spent an afternoon
    each on that silence on 2026-08-21. The client process's stderr is local, so the
    full chain belongs there.

    Never raises: a diagnostic that masks the failure it describes is worse than none.
    """
    sink = getattr(runtime, "_log", None)
    if not callable(sink):
        return
    try:
        detail = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        ).rstrip()
        sink(f"[{tool}] opaque failure; full cause (local only):\n{detail}")
    except Exception:
        try:
            sink(f"[{tool}] opaque failure: {type(exc).__name__}")
        except Exception:
            pass


def _wire_safe_error(runtime: Any, tool: str, detail: object) -> str:
    """Reduce a backend error to the token the wire may carry.

    A capture failure reports what actually broke, and what broke is described
    with a host path: `mcp_capture` names GRAB_SCRIPT when the grab script is
    missing, forwards the grab backend's stderr when it fails, and forwards the
    exception text otherwise. Any of those puts C:\\Users\\<name>\\... in front of
    whoever called the tool, on a wire that reaches other machines.

    The leading token before the first colon is our own constant
    (`capture_backend_failed`, `capture_timeout`), so that part travels and the
    detail goes to the local log -- the same split `_typed_dayz_test_value_errors`
    already applies, for the same reason. A token that is not identifier-shaped is
    replaced rather than trusted: the point is a searchable name, never the text.
    """
    text = str(detail)
    token = text.split(":", 1)[0].strip()
    if text != token:
        sink = getattr(runtime, "_log", None)
        if callable(sink):
            try:
                sink(f"[{tool}] error detail (local only): {text}")
            except Exception:
                pass
    return token if _is_safe_error_token(token) else f"{tool}_failed"


def _is_safe_error_token(value: str) -> bool:
    """True for a bare identifier-shaped token, which cannot hold a host path.

    Deliberately strict: no dot, colon, separator, space or quote survives, so a
    stdlib message (`invalid literal for int() with base 10: 'x'`) is rejected
    and stays mute, while a source constant (`invalid_session_lease`) passes.
    """
    return (
        3 <= len(value) <= 64
        and value[0].isascii()
        and value[0].isalpha()
        and all(char.isascii() and (char.isalnum() or char == "_") for char in value)
    )


def _opaque_dayz_test_failure(exc: BaseException) -> str:
    """`dayz_test_failed:<Type>`, plus `:<code>` when the launcher backend named one.

    NativeLauncherBackendError (native_launcher_backend.py) keeps a source
    constant in ``code`` -- invalid_native_launcher_environment,
    native_launcher_create_failed, ... -- and any host detail in ``detail``,
    which never travels. Only an identifier-shaped code crosses the wire.
    An identifier-shaped ``fine_code`` is appended as a fourth part.
    Ficha ae65 (2026-09-04): build=true died in that backend and the caller saw
    the class name alone, with the code one frame away in the local log.
    """
    name = type(exc).__name__
    code = getattr(exc, "code", None) if name == "NativeLauncherBackendError" else None
    if isinstance(code, str) and _is_safe_error_token(code):
        fine_code = getattr(exc, "fine_code", None)
        if isinstance(fine_code, str) and _is_safe_error_token(fine_code):
            return f"dayz_test_failed:{name}:{code}:{fine_code}"
        return f"dayz_test_failed:{name}:{code}"
    return f"dayz_test_failed:{name}"


@contextmanager
def _typed_dayz_test_value_errors() -> Iterator[None]:
    """Type the constant ValueError tokens of the dayz_test request path.

    Both dayz_test_run and dayz_test_stop reach the same escape point:
    _execute_request (dayz_test_tool.py) runs the parse and the path
    accreditation of native_launcher_transaction.py:108/:112 for either
    public mode, so a stop hits the identical tokens a run does.
    """
    try:
        yield
    except ValueError as error:
        token = str(error)
        code = _DAYZ_TEST_VALUE_ERROR_CODES.get(token)
        if code is not None:
            raise dayz_test_tool.DayzTestToolError(code) from None
        if _is_safe_error_token(token):
            # Not curated, but a bare identifier cannot carry a host path, and a
            # named failure beats `dayz_test_failed:ValueError` -- a string that
            # matches nothing and sends whoever hit it to read source. Measured
            # 2026-08-21: a session spent two hours on exactly that silence while
            # the map held 9 tokens and the path raised far more. The map above
            # now only exists to RENAME the few tokens whose own name is poor.
            raise dayz_test_tool.DayzTestToolError(token) from None
        raise


_CONTROL_CLIENT_ERROR_CODES = frozenset({
    "credential_source_untrusted",
    "client_policy_untrusted_open_new_session",
    "daemon_credential_desynchronized",
    "daemon_reaccreditation_failed_open_new_session",
    "daemon_bad_body",
    "daemon_bad_session_response",
    "daemon_identity_unverified",
    "daemon_response_ambiguous",
    "daemon_unavailable",
    "session_cleanup_failed",
    "session_transition_conflict",
    "session_wait_timeout",
    "stale_client_credential_refresh_failed",
    "stale_client_credential_retry_rejected",
    "stale_client_credential_retry_transport_failed",
})


def _published_not_ready_code(payload: object) -> str | None:
    """Return a published ready.reason carried on an enqueue-shaped payload.

    `_remote_error_code` only accepts `_REMOTE_ERROR_CODES`. These tokens live
    on bridge_status.ready.reason instead, so a payload that names them as
    `error` or `reason` used to become the bare token remote_error.

    Conditional: this recovers the token only when the enqueue body itself
    carries it. A status snapshot with ready.reason=client_not_polling does
    not imply that a given /enqueue was refused with that code.
    """
    if not isinstance(payload, dict):
        return None
    candidates: list[object] = [payload.get("error"), payload.get("reason")]
    ready = payload.get("ready")
    if isinstance(ready, dict):
        candidates.append(ready.get("reason"))
    for value in candidates:
        if isinstance(value, str) and value in _PUBLISHED_NOT_READY_CODES:
            return value
    return None


def _remote_error_code(payload: object) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, str) and error in _REMOTE_ERROR_CODES:
            return error
        published = _published_not_ready_code(payload)
        if published is not None:
            return published
    return "remote_error"


_FENCE_BLOCK_READY = {
    "AMBIGUOUS": "binding_ambiguous",
    "STARTING": "binding_not_ready",
    "RETIRED": "binding_retired",
    "binding_retired": "binding_retired",
    "instance_unknown": "instance_unknown",
    "unbound_after_restart": "unbound_after_restart",
    "instance_unattributed": "instance_unattributed",
    "instance_role_mismatch": "instance_role_mismatch",
    "instance_malformed": "instance_malformed",
    "instance_peer_collision": "instance_peer_collision",
    "creation_time_unreadable": "creation_time_unreadable",
}


async def _runtime_client_peer_probeable(runtime: Any) -> bool:
    try:
        status = await runtime.bridge_status_payload()
    except Exception:
        return False
    return _client_peer_probeable(status)


def _finite_poll_age(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def compute_bridge_ready(status: dict[str, Any]) -> dict[str, Any]:
    """Return {ready, reason} for a bridge_status snapshot. Additive field.

    Version reasons are used only when that peer has polled at least once.
    Server liveness is checked before client liveness.

    A BOUND peer with no accredited poll this generation is binding_not_ready,
    not server_poll_stale: leftover last_poll_age_s from a dead pre-launch
    peer must not look like a failed live server (fb-20260917-100411-5edf).
    """
    server = status.get("server_peer") if isinstance(status.get("server_peer"), dict) else {}
    client = status.get("client_peer") if isinstance(status.get("client_peer"), dict) else {}
    s_age = server.get("last_poll_age_s")
    c_age = client.get("last_poll_age_s")
    s_live = _peer_is_live(server)
    c_live = _peer_is_live(client)
    s_state = server.get("version_state")
    c_state = client.get("version_state")
    s_bind = server.get("binding_state")
    c_bind = client.get("binding_state")
    s_block = _FENCE_BLOCK_READY.get(s_bind)
    c_block = _FENCE_BLOCK_READY.get(c_bind)
    if s_block:
        return {"ready": False, "reason": s_block}
    if c_block:
        return {"ready": False, "reason": c_block}
    if s_bind == "LEGACY_UNBOUND" and s_age is not None:
        return {"ready": False, "reason": "legacy_unbound"}
    if c_bind == "LEGACY_UNBOUND" and c_age is not None:
        return {"ready": False, "reason": "legacy_unbound"}
    # 0878: name-only census can green-wash a stale PBO that rejects current
    # tool args (vehicle_prepare_fixture mode/radius). Server ready requires an
    # accredited caps census AND a matching ach. Capability comparison (B1)
    # must run before this so published status sees mismatch/unknown, not raw
    # announced. Raw announced (no comparison yet) still checks ach directly.
    # Unknown / missing / malformed caps: ready=false (B2).
    s_caps = server.get("capabilities") if isinstance(server.get("capabilities"), dict) else {}
    if s_live:
        caps_state = s_caps.get("state")
        if caps_state == "match":
            pass
        elif caps_state == "announced":
            ach = s_caps.get("announced_arg_contract_hash")
            if (
                not isinstance(ach, str)
                or ach == ""
                or ach != EXPECTED_SERVER_ARG_CONTRACT_HASH
            ):
                return {"ready": False, "reason": "arg_contract_mismatch"}
        elif caps_state == "mismatch":
            # Fail-closed on arg-contract hash independently of the primary
            # compare reason. Census disagreement used to hide absent/wrong ach
            # (B2 residual): missing census cmd + bad/absent ach must not
            # green-wash ready=true. Matching ach + census-only mismatch keeps
            # historical ready behavior.
            if s_caps.get("reason") == "arg_contract_mismatch":
                return {"ready": False, "reason": "arg_contract_mismatch"}
            ach = s_caps.get("announced_arg_contract_hash")
            if (
                not isinstance(ach, str)
                or ach == ""
                or ach != EXPECTED_SERVER_ARG_CONTRACT_HASH
            ):
                return {"ready": False, "reason": "arg_contract_mismatch"}
            # Name-census disagreement with matching ach: historical ready.
            pass
        else:
            return {"ready": False, "reason": "capabilities_unknown"}
    if s_live and c_live and s_state == "ok" and c_state == "ok":
        return {"ready": True, "reason": "ready"}
    if s_bind == "BOUND" and not _finite_poll_age(server.get("bound_last_poll_age_s")):
        return {"ready": False, "reason": "binding_not_ready"}
    if c_bind == "BOUND" and not _finite_poll_age(client.get("bound_last_poll_age_s")):
        return {"ready": False, "reason": "binding_not_ready"}
    if s_age is None and c_age is None:
        return {"ready": False, "reason": "no_run"}
    if not s_live:
        return {"ready": False, "reason": "server_poll_stale"}
    if not c_live:
        return {"ready": False, "reason": "client_not_polling"}
    if c_age is not None and c_state == "legacy_blocked":
        return {"ready": False, "reason": "client_legacy_blocked"}
    if (
        (s_age is not None and s_state in {"version_mismatch", "legacy_blocked"})
        or (c_age is not None and c_state == "version_mismatch")
    ):
        return {"ready": False, "reason": "version_mismatch"}
    if s_state != "ok" or c_state != "ok":
        return {"ready": False, "reason": "version_mismatch"}
    return {"ready": False, "reason": "no_run"}


_BRIDGE_WORLD_READ_COMMANDS = READ_ONLY_COMMANDS - {"logs_since"}

# Progressive disclosure (fb-20260917-092908-2ad1): the first tools/list a
# client-mode caller sees is a compact catalog: the session and lifecycle core
# plus the reads that need no lease. The rest of world_*/vehicle_*/ui_* stays
# off the catalog until a lease is held. Embedded mode keeps the full registry
# so in-process tests and the host-side catalog stay complete. The listing is
# not an access control: a tool it leaves out still runs when called by name,
# and the lease gate refuses a mutation without a lease (fb-20260925-233937-b753).
_LEASE_REVEAL_PREFIXES = ("world_", "vehicle_", "ui_")
_INITIAL_CORE_NAMES = frozenset(
    {
        "bridge_status",
        "dayz_knowledge_find",
        "dayz_knowledge_prepare",
        "dayz_knowledge_show",
        "dayz_knowledge_status",
        "dayz_test_close",
        "dayz_test_run",
        "dayz_test_stop",
        "lease_acquire",
        "pipeline_feedback",
        "pipeline_inbox",
        "pipeline_resolve",
        "session_acquire_wait",
        "session_heartbeat",
        "session_release",
        "session_status",
        "wait_for",
    }
)
# The public tools whose only bridge command is in READ_ONLY_COMMANDS (the tool
# _BRIDGE_COMMAND_TOOLS names for it), plus logs_since, which reads host logs
# and never reaches the game. They run without a lease, so hiding them until
# one is held only took the information away from weak callers (b753).
_INITIAL_READ_TOOL_NAMES = frozenset(
    {
        "camera_get",
        "entities_query",
        "input_describe",
        "logs_since",
        "object_doors",
        "object_inspect",
        "query_all_players",
        "query_get_in_condition",
        "query_player_state",
        "scene_raycast",
        "surface_query",
        "telemetry_read",
        "ui_tree",
        "vehicle_telemetry",
        "weapon_state",
    }
)
_INITIAL_CATALOG_NAMES = _INITIAL_CORE_NAMES | _INITIAL_READ_TOOL_NAMES
# Whole sentences are kept up to this many characters (b753); see
# _compact_description for what happens when the first sentence is longer.
_INITIAL_DESCRIPTION_LIMIT = 120
_COMPACT_DESCRIPTION_MARKER = "…"
_OPENER_FOR_CLOSER = {")": "(", "]": "[", "}": "{"}
# Claude Code never re-lists after tools/list_changed (#93, e7ef): a compact
# catalog there hides the game verbs for the whole session, so this platform
# lists the full catalog from the start, as --no-progressive-disclosure does.
_FULL_CATALOG_PLATFORMS = frozenset({"claude"})


def _runtime_holds_lease(runtime: Any) -> bool:
    token = getattr(runtime, "active_lease_token", None)
    if callable(token):
        try:
            token = token()
        except Exception:
            token = None
    return bool(token)


def _progressive_disclosure_enabled(config: Any) -> bool:
    """True when this process lists the compact catalog while it holds no lease."""
    return (
        getattr(config, "mode", None) == "client"
        and getattr(config, "progressive_disclosure", True) is not False
        and getattr(config, "client_platform", None) not in _FULL_CATALOG_PLATFORMS
    )


def _progressive_disclosure_active(runtime: Any) -> bool:
    return _progressive_disclosure_enabled(
        getattr(runtime, "config", None)
    ) and not _runtime_holds_lease(runtime)


def _is_lease_revealed_tool(name: str) -> bool:
    return name.startswith(_LEASE_REVEAL_PREFIXES)


def _compact_description(description: str) -> str:
    """Shorten a description for the compact catalog without a misleading cut.

    Keeps the longest run of whole sentences that fits in
    _INITIAL_DESCRIPTION_LIMIT. When even the first sentence is longer, it is
    cut at the last space outside brackets that fits, with the separator it
    leaves dangling removed. Either way the dropped text is marked with a
    trailing "…", and with no such space the marker stands alone. A cut inside a
    word read as a real value (b753: "kind must be bug | request | find…").
    """
    if len(description) <= _INITIAL_DESCRIPTION_LIMIT:
        return description
    room = _INITIAL_DESCRIPTION_LIMIT - len(" " + _COMPACT_DESCRIPTION_MARKER)
    sentence_end = word_end = 0
    open_brackets: list[str] = []
    for index, char in enumerate(description[: room + 1]):
        if char in "([{":
            open_brackets.append(char)
        elif char in _OPENER_FOR_CLOSER:
            if open_brackets and open_brackets[-1] == _OPENER_FOR_CLOSER[char]:
                open_brackets.pop()
        elif char.isspace() and index > 0 and not open_brackets:
            word_end = index
            if description[index - 1] in ".!?" and description[
                max(0, index - 4) : index
            ].lower() not in ("e.g.", "i.e."):
                sentence_end = index
    if sentence_end:
        kept = description[:sentence_end].rstrip()
    else:
        kept = description[:word_end].rstrip().rstrip(",;:|/-=>+&").rstrip()
    if not kept:
        return _COMPACT_DESCRIPTION_MARKER
    return f"{kept} {_COMPACT_DESCRIPTION_MARKER}"


def _compact_initial_catalog(tools: list[Any]) -> list[Any]:
    """The pre-lease tools/list for 8B clients: the core plus lease-free reads.

    About 17 KB with the reads (b753), most of it input schemas, which stay
    whole. Descriptions go through _compact_description; outputSchema is
    dropped. A world_*/vehicle_*/ui_* tool is listed only when it is a read.
    """
    compacted: list[Any] = []
    for tool in tools:
        name = getattr(tool, "name", "")
        if name not in _INITIAL_CATALOG_NAMES:
            continue
        if _is_lease_revealed_tool(name) and name not in _INITIAL_READ_TOOL_NAMES:
            continue
        description = getattr(tool, "description", None) or ""
        updates: dict[str, Any] = {}
        compact_description = _compact_description(description)
        if compact_description != description:
            updates["description"] = compact_description
        if getattr(tool, "outputSchema", None) is not None:
            updates["outputSchema"] = None
        compacted.append(tool.model_copy(update=updates) if updates else tool)
    return compacted


def _install_catalog_change_notice(app: Any, runtime: Any) -> None:
    """Send tools/list_changed after a call that flips the compact catalog.

    b753: a lease reveals the lease-gated tools, and session_release, or a call
    that finds the lease expired or lost, hides them again. The client runtime
    already tracks that state: ControlClient.active_lease_token, set by a grant
    and cleared by session_release, by _clear_matching_lease on a
    lease_expired / lease_invalid reply, and by a session_status reply that no
    longer shows the lease. So there is no timer: after every call the view is
    compared with the one last announced, and only a difference is sent. A lease
    that expires silently is seen by the next call that reaches the daemon.
    A failed send never fails the call; the next call retries it.

    The comparison, the send and the update run under one lock, and the view is
    read inside it. Calls run concurrently and release tool_lock before this
    point, so without the lock a call finishing while another's notice is still
    being sent compares against the value that notice is about to overwrite: it
    can stay silent about a new flip, and leave a stale view that makes a later
    call miss one or announce nothing new (b753 review, F1).

    Registered as the protocol tools/call handler before
    install_result_freshness wraps that handler, so the freshness check still
    finds its own wrapper outermost. In-process app.call_tool is not wrapped.
    """
    call_tool = app.call_tool
    announced_compact = _progressive_disclosure_active(runtime)
    announce_lock = asyncio.Lock()

    async def call_tool_then_announce(name: str, arguments: dict[str, Any]) -> Any:
        nonlocal announced_compact
        try:
            return await call_tool(name, arguments)
        finally:
            async with announce_lock:
                compact = _progressive_disclosure_active(runtime)
                if compact != announced_compact:
                    try:
                        session = app._mcp_server.request_context.session
                        await session.send_tool_list_changed()
                    except Exception:
                        pass
                    else:
                        announced_compact = compact

    app._mcp_server.call_tool(validate_input=False)(call_tool_then_announce)


def _visible_public_tools(runtime: Any) -> frozenset[str]:
    """Return the real registry when build_app published it, else the full public set."""
    names = getattr(runtime, "_registered_tool_names", None)
    if isinstance(names, (set, frozenset)):
        return frozenset(name for name in names if isinstance(name, str))
    return PUBLIC_NEXT_TOOLS


def _next_public_call(runtime: Any, *preferred: str) -> dict[str, Any]:
    visible = _visible_public_tools(runtime)
    for tool in preferred:
        if tool in PUBLIC_NEXT_TOOLS and tool in visible:
            return {"tool": tool, "args": {}}
    # Every supported registry pack includes bridge_status. The fallback also
    # keeps direct Runtime instances useful before build_app attaches its set.
    return {"tool": "bridge_status", "args": {}}


def _world_read_not_ready(
    runtime: Any, cmd: str, status: dict[str, Any]
) -> dict[str, Any] | None:
    """Return the short fail-fast envelope for bridge world reads, or None."""
    if cmd not in _BRIDGE_WORLD_READ_COMMANDS:
        return None
    verdict = compute_bridge_ready(status)
    if verdict["ready"]:
        return None
    reason = str(verdict["reason"])
    return {
        "ok": False,
        "error": f"game_not_ready:reason={reason}",
        "code": "not_ready",
        "reason": reason,
        "next_step": _next_public_call(runtime, "bridge_status"),
    }


def _has_ready_snapshot_shape(status: object) -> bool:
    """True when a client-fetched status has both readiness inputs."""
    if not isinstance(status, dict):
        return False
    for key in ("server_peer", "client_peer"):
        peer = status.get(key)
        if not isinstance(peer, dict):
            return False
        if "last_poll_age_s" not in peer or "version_state" not in peer:
            return False
    return True


def _bridge_success_candidates(
    cmd: str, result: dict[str, Any]
) -> list[tuple[str, dict[str, Any]]]:
    """Return ordered follow-up candidates derived only from wire-visible facts."""
    if cmd == "query_all_players":
        players = result.get("players")
        if isinstance(players, list) and players:
            return [("query_player_state", {}), ("bridge_status", {})]
        if isinstance(players, list):
            return [
                ("wait_for", {"condition": "players_at_least", "value": 1}),
                ("bridge_status", {}),
            ]
    if cmd == "entities_query":
        entities = result.get("entities")
        if isinstance(entities, list) and entities:
            row = next((item for item in entities if isinstance(item, dict)), None)
            if isinstance(row, dict):
                object_type = row.get("type") or row.get("classname")
                pos = row.get("pos")
                if (
                    isinstance(object_type, str)
                    and object_type
                    and isinstance(pos, list)
                    and len(pos) == 3
                ):
                    return [
                        (
                            "object_inspect",
                            {
                                "type": object_type,
                                "pos": list(pos),
                                "want": ["bounding_center"],
                            },
                        ),
                        ("bridge_status", {}),
                    ]
    return [("bridge_status", {}), ("session_status", {})]


def _with_bridge_success_hints(
    runtime: Any, cmd: str, result: dict[str, Any]
) -> dict[str, Any]:
    """Add at most two calls that exist in this client's exposed registry."""
    if not isinstance(result, dict) or not result.get("ok"):
        return result
    visible = _visible_public_tools(runtime)
    suggested: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tool, args in _bridge_success_candidates(cmd, result):
        if tool in seen or tool not in PUBLIC_NEXT_TOOLS or tool not in visible:
            continue
        suggested.append({"tool": tool, "args": dict(args)})
        seen.add(tool)
        if len(suggested) == 2:
            break
    if not suggested:
        return _with_ok_next_step(result, cmd)
    payload = dict(result)
    payload["suggested_calls"] = suggested
    return _with_ok_next_step(payload, cmd)


def _with_ok_next_step(result: dict[str, Any], cmd: str) -> dict[str, Any]:
    """Attach next_step from PUBLIC_NEXT_TOOLS on ok:true mutation/session results."""
    if not isinstance(result, dict):
        return result
    if result.get("ok") not in (True, 1) and "ok" in result:
        return result
    if result.get("error"):
        return result
    existing = result.get("next_step")
    if isinstance(existing, str) and existing in PUBLIC_NEXT_TOOLS:
        return result
    mutating = command_requires_lease(cmd)
    follow = ok_next_step(cmd, mutating=mutating)
    if follow is None:
        return result
    payload = dict(result)
    if payload.get("ok") not in (True, 1):
        payload["ok"] = True
    payload["next_step"] = follow
    return payload


# Arg-contract fingerprint (fb-20260924-235528-0878). Command-name census alone
# cannot see a PBO that still lists vehicle_prepare_fixture but rejects the
# tool's mode=/radius= shape. Both sides ship the same 16-hex SHA-256 prefix of
# the canonical form below; the PBO announces it as poll `ach=` and
# `_compare_bridge_capabilities` fails closed on absent/wrong values for the
# server peer. Client peer has no ach gate yet.
SERVER_ARG_CONTRACT: dict[str, tuple[str, ...]] = {
    "vehicle_prepare_fixture": ("mode", "pos", "radius", "type"),
}


def server_arg_contract_canonical() -> str:
    """Stable, newline-joined `cmd=k1,k2` lines (keys sorted, cmds sorted)."""

    return "\n".join(
        f"{cmd}={','.join(keys)}"
        for cmd, keys in sorted(SERVER_ARG_CONTRACT.items())
    )


def server_arg_contract_hash() -> str:
    """First 16 hex chars of sha256(canonical). Must match MCPBridge.c."""

    import hashlib

    return hashlib.sha256(server_arg_contract_canonical().encode("utf-8")).hexdigest()[:16]


EXPECTED_SERVER_ARG_CONTRACT_HASH = server_arg_contract_hash()


# peer + command -> the public tool that fronts it, or None when the command is
# deliberately not exposed. Hand written from the two Enforce dispatchers
# (MCPBridge.c SERVER_CAPABILITIES, MCPClientBridge.c CLIENT_POLL_CAPS).
#
# Explicitly NOT derived from app.list_tools(), from loopback's command lists or
# from the PBO. The census exists so it CAN disagree with what the daemon
# registers; a table derived from either side would agree by construction and
# detect nothing. A command the bridge announces and this table does not know is
# reported as unmapped rather than silently accepted -- that is the case a new
# command shipped in the PBO produces, and it should be visible on the first
# poll instead of on the first failed call.
_BRIDGE_COMMAND_TOOLS: dict[str, dict[str, str | None]] = {
    "server": {
        "entities_query": "entities_query",
        "exec_enforce": None,  # not a public tool by decision
        "hands_take": "hands_take",
        "infected_drive": "infected_drive",
        "inventory_attach": "inventory_attach",
        "inventory_give": "inventory_give",
        "notify_players": "notify_players",
        "object_anim": "object_anim",
        "object_delete": "object_delete",
        "object_doors": "object_doors",
        "object_inspect": "object_inspect",
        "player_teleport": "player_teleport",
        "query_all_players": "query_all_players",
        "query_get_in_condition": "query_get_in_condition",
        "query_player_state": "query_player_state",
        "scene_raycast": "scene_raycast",
        "surface_query": "surface_query",
        "telemetry_read": "telemetry_read",
        "vehicle_door": "vehicle_door",
        "vehicle_enter": "vehicle_enter",
        "vehicle_prepare_fixture": "vehicle_prepare_fixture",
        "weapon_state": "weapon_state",
        "world_spawn": "world_spawn",
        "world_time_set": "world_time_set",
        "world_weather_set": "world_weather_set",
    },
    "client": {
        "action_use": "action_use",
        "action_use_door": "action_use",
        "action_use_target": "action_use",
        "anim_timeline": "anim_timeline",
        "camera_get": "camera_get",
        "camera_set": "camera_set",
        "engine_set": "engine_set",
        "input_describe": "input_describe",
        "input_trigger": "input_trigger",
        "key_press": "key_press",
        "player_respawn": "player_respawn",
        "restore_gameplay": "restore_gameplay",
        "ui_click": "ui_click",
        "ui_dialog": "ui_dialog",
        "ui_focus": "ui_focus",
        "ui_reload_layout": "ui_reload_layout",
        "ui_set_text": "ui_set_text",
        "ui_tree": "ui_tree",
        "vehicle_control": "vehicle_control",
        "vehicle_get_in_client": "vehicle_get_in_client",
        "vehicle_release": "vehicle_release",
        "vehicle_telemetry": "vehicle_telemetry",
        "vehicle_trace": "vehicle_trace",
        "weapon_aim": "weapon_aim",
        "weapon_fire": "weapon_fire",
        "weapon_raise": "weapon_raise",
        "weapon_sights": "weapon_sights",
    },
}


def _compare_bridge_capabilities(
    peer: str,
    capabilities: object,
    registered_tools: frozenset[str],
    intended_tools: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Cross one peer's announced census against the registered tools.

    Three verdicts and never a fourth: ``match`` when every mapped command has
    its tool and every tool has its command, ``mismatch`` when they disagree --
    naming exactly which commands -- and ``unknown`` when there is no census to
    judge. ``unknown`` is not a mismatch: an absent, malformed or unaccredited
    announcement means we did not look, and saying otherwise would put a red on
    a bridge that may be perfectly fine.
    """

    block = capabilities if isinstance(capabilities, dict) else {}
    mapping = _BRIDGE_COMMAND_TOOLS.get(peer, {})
    expected_tools = {tool for tool in mapping.values() if tool}
    intended_bridge_tools = (
        expected_tools
        if intended_tools is None
        else expected_tools & set(intended_tools)
    )
    registered_bridge_tools = sorted(intended_bridge_tools & registered_tools)
    announced = block.get("announced_commands")
    if block.get("state") != "announced" or not isinstance(announced, list):
        return {
            "state": "unknown",
            "reason": str(block.get("reason") or "absent"),
            "announced_commands": [],
            "registered_bridge_tools": registered_bridge_tools,
            "announced_without_registered_tool": [],
            "registered_without_announced_command": [],
            "unmapped_announced_commands": [],
            "expected_arg_contract_hash": (
                EXPECTED_SERVER_ARG_CONTRACT_HASH if peer == "server" else None
            ),
            "announced_arg_contract_hash": None,
        }
    announced_set = {item for item in announced if isinstance(item, str)}
    unmapped = sorted(item for item in announced_set if item not in mapping)
    missing_tool = sorted(
        item
        for item in announced_set
        if mapping.get(item) in intended_bridge_tools
        and mapping[item] not in registered_tools
    )
    announced_tools = {mapping[item] for item in announced_set if mapping.get(item)}
    not_announced = sorted(
        tool for tool in registered_bridge_tools if tool not in announced_tools
    )
    agrees = not (unmapped or missing_tool or not_announced)
    announced_hash = block.get("announced_arg_contract_hash")
    expected_hash = (
        EXPECTED_SERVER_ARG_CONTRACT_HASH if peer == "server" else None
    )
    arg_contract_ok = True
    arg_reason = "ok"
    if expected_hash is not None:
        if not isinstance(announced_hash, str) or announced_hash == "":
            arg_contract_ok = False
            arg_reason = "arg_contract_mismatch"
            announced_hash = None
        elif announced_hash != expected_hash:
            arg_contract_ok = False
            arg_reason = "arg_contract_mismatch"
    # Prefer arg_contract_mismatch when both census and ach fail so the
    # fail-closed gate is not hidden behind census_disagrees (B2 residual).
    # Census-only disagreement keeps its historical reason; details remain in
    # announced_without_registered_tool / registered_without_announced_command.
    if not arg_contract_ok:
        state = "mismatch"
        reason = arg_reason
    elif not agrees:
        state = "mismatch"
        reason = "census_disagrees_with_registered_tools"
    else:
        state = "match"
        reason = "ok"
    return {
        "state": state,
        "reason": reason,
        "announced_commands": sorted(announced_set),
        "registered_bridge_tools": registered_bridge_tools,
        "announced_without_registered_tool": missing_tool,
        "registered_without_announced_command": not_announced,
        "unmapped_announced_commands": unmapped,
        "expected_arg_contract_hash": expected_hash,
        "announced_arg_contract_hash": announced_hash if isinstance(announced_hash, str) else None,
    }


def _with_capability_comparison(
    payload: dict[str, Any],
    registered_tools: frozenset[str],
    intended_tools: frozenset[str] | None = None,
) -> dict[str, Any]:
    enriched = dict(payload)
    for peer, key in (("server", "server_peer"), ("client", "client_peer")):
        block = enriched.get(key)
        if not isinstance(block, dict):
            continue
        block = dict(block)
        block["capabilities"] = _compare_bridge_capabilities(
            peer,
            block.get("capabilities"),
            registered_tools,
            intended_tools,
        )
        enriched[key] = block
    return enriched


def _client_peer_announces_command(status: object, command: str) -> bool:
    if not isinstance(status, dict):
        return False
    client_peer = status.get("client_peer")
    if not isinstance(client_peer, dict):
        return False
    capabilities = client_peer.get("capabilities")
    if not isinstance(capabilities, dict):
        return False
    if capabilities.get("state") != "announced":
        return False
    announced = capabilities.get("announced_commands")
    if not isinstance(announced, list):
        return False
    return command in announced


def _registry_tool_records(app: FastMCP) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for tool in app._tool_manager.list_tools():
        schema = tool.parameters if isinstance(getattr(tool, "parameters", None), dict) else {}
        records.append(
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": schema,
                "public_constraints": [],
                "effect_verification": "wire",
            }
        )
    return records


def _capture_process_registry(app: FastMCP, config: ServerConfig) -> Any:
    profile, role = project_server_config_identity(
        enable_exec_enforce=config.enable_exec_enforce,
        client_platform=config.client_platform,
    )
    if profile == "unknown" or role == "unknown":
        profile, role = "standard", "claude"
    return capture_registry_snapshot(
        session_id=str(uuid.uuid4()),
        profile=profile,
        role=role,
        captured_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        tools=_registry_tool_records(app),
    )


def _frozen_tool_registry_overlay(app: FastMCP, config: ServerConfig) -> dict[str, Any]:
    snapshot = _capture_process_registry(app, config)
    return {
        "tool_registry_fingerprint": snapshot.fingerprint,
        "tool_registry_captured_at": snapshot.captured_at_utc,
    }


def _front_key(payload: dict[str, Any], key: str) -> dict[str, Any]:
    """Put key first so an 8B scanner sees the verdict before the rest of the blob."""
    if key not in payload:
        return dict(payload)
    ordered: dict[str, Any] = {key: payload[key]}
    for name, value in payload.items():
        if name != key:
            ordered[name] = value
    return ordered


def _ready_next_tool(reason: str, *, is_ready: bool) -> str | None:
    if is_ready or reason == "ready":
        return None
    # Uniform ready=false envelope (fb-20260917-092908-1765 / baf9):
    # always name bridge_status, never a per-reason fork.
    return next_step("bridge_status")


def _with_ready(status: dict[str, Any]) -> dict[str, Any]:
    payload = dict(status)
    verdict = compute_bridge_ready(payload)
    server = payload.get("server_peer") if isinstance(payload.get("server_peer"), dict) else {}
    client = payload.get("client_peer") if isinstance(payload.get("client_peer"), dict) else {}
    is_ready = bool(verdict["ready"])
    reason = str(verdict["reason"])
    # ready/reason/next_step first so an 8B scanner sees the verdict before ages.
    ready: dict[str, Any] = {
        "ready": is_ready,
        "reason": reason,
    }
    follow = _ready_next_tool(reason, is_ready=is_ready)
    if follow is not None:
        ready["next_step"] = follow
    ready["stale_threshold_s"] = PEER_STALE_S
    ready["server_last_poll_age_s"] = server.get("last_poll_age_s")
    ready["client_last_poll_age_s"] = client.get("last_poll_age_s")
    ready["server_bound_last_poll_age_s"] = server.get("bound_last_poll_age_s")
    ready["client_bound_last_poll_age_s"] = client.get("bound_last_poll_age_s")
    payload["ready"] = ready
    return _front_key(payload, "ready")


def _game_not_ready_reason(
    status: dict[str, Any] | None,
    peer: str | None = None,
) -> str:
    if isinstance(status, dict) and peer in {"server", "client"}:
        key = "client_peer" if peer == "client" else "server_peer"
        if not _peer_is_live(status.get(key)):
            return "client_not_polling" if peer == "client" else "server_poll_stale"
    if not isinstance(status, dict):
        return "no_run"
    reason = compute_bridge_ready(status)["reason"]
    if reason == "ready":
        return "no_run"
    return str(reason)


def _target_peer_down(
    status_snapshot: dict[str, Any] | None,
    peer: str | None,
) -> bool:
    """True when the command's target peer is not live (same rule as embedded)."""
    if not isinstance(status_snapshot, dict):
        return False
    if peer in {"server", "client"}:
        key = "client_peer" if peer == "client" else "server_peer"
        return not _peer_is_live(status_snapshot.get(key))
    return not _peer_is_live(status_snapshot.get("server_peer")) and not _peer_is_live(
        status_snapshot.get("client_peer")
    )


_UI_ECHO_VERBS = frozenset({"ui_click", "ui_focus", "ui_set_text", "ui_tree"})
_UI_CLICK_DIAGNOSTIC_KEYS = ("handler", "user_id", "clicked")
_UI_ECHO_KEYS = ("requested_path", "requested_root", "matched_path")
# vehicle_prepare_fixture fills these before fixture_not_ready
# (MCPBridge.c PopulateTelemetryObject + vehicle_fixture_ready). The
# expected WheelCount() is not on the wire; do not invent it (fb-b1ff).
_FIXTURE_NOT_READY_TELEMETRY_KEYS = ("wheel_count", "fuel_fraction", "attachment_count")
_FIXTURE_SCALAR_STR_MAX = 32


def _format_fixture_scalar(value: object) -> str | None:
    """Unquoted [EXACT] form for allowlisted fixture scalars.

    int/float/bool match the published message (``wheel_count=2``). A JSON
    string ``'2'`` must not become Python ``!r`` quotes (``wheel_count='2'``).
    Non-numeric strings are omitted, not leaked.
    """
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return repr(value)
    if isinstance(value, str) and 0 < len(value) <= _FIXTURE_SCALAR_STR_MAX:
        negative = value.startswith("-") and len(value) > 1
        text = value[1:] if negative else value
        if text.isdigit():
            parsed_int = int(text, 10)
            return str(-parsed_int if negative else parsed_int)
        left, sep, right = text.partition(".")
        if sep and left.isdigit() and right.isdigit():
            parsed = float(f"{'-' if negative else ''}{left}.{right}")
            if math.isfinite(parsed):
                return repr(parsed)
    return None


def _fixture_not_ready_detail(result: dict[str, Any]) -> str:
    pairs: list[str] = []
    telemetry = result.get("telemetry")
    if isinstance(telemetry, dict):
        for key in _FIXTURE_NOT_READY_TELEMETRY_KEYS:
            if key not in telemetry:
                continue
            rendered = _format_fixture_scalar(telemetry[key])
            if rendered is None:
                continue
            pairs.append(f"{key}={rendered}")
    if "vehicle_fixture_ready" in result:
        rendered = _format_fixture_scalar(result["vehicle_fixture_ready"])
        if rendered is not None:
            pairs.append(f"vehicle_fixture_ready={rendered}")
    if not pairs:
        return ""
    # P34-P2-1: label the blob so a client does not read wheel_count= as the
    # cause. expected (WheelCount()) is not on the wire; do not invent it.
    return "observed=" + " ".join(pairs)


# vehicle_door fills vehicle_door.slot, state and phase before door_missing
# (MCPBridge.c DispatchVehicleDoor). slot is the car script's slot name for the
# crew door's seat, never caller input; a value that is not a plain slot token
# is omitted, not leaked. source is caller input and stays out.
_VEHICLE_DOOR_SLOT_RE = re.compile(r"[A-Za-z0-9_]{1,64}")


def _vehicle_door_missing_detail(result: dict[str, Any]) -> str:
    report = result.get("vehicle_door")
    if not isinstance(report, dict):
        return ""
    pairs: list[str] = []
    slot = report.get("slot")
    if isinstance(slot, str) and _VEHICLE_DOOR_SLOT_RE.fullmatch(slot):
        pairs.append(f"slot={slot}")
    if report.get("state") == "missing":
        pairs.append("state=missing")
    phase = report.get("phase")
    if (
        isinstance(phase, (int, float))
        and not isinstance(phase, bool)
        and math.isfinite(phase)
    ):
        pairs.append(f"phase={float(phase)!r}")
    return " ".join(pairs)


# input_trigger fills its reply before refusing (MCPClientBridge.c
# DispatchInputTrigger). Only a reason token, a known released_by and 0/1
# facts cross, and each code publishes only its own facts: the reply is one
# flat class, so an unread fact still arrives as 0. name is caller input and
# stays out.
_INPUT_TRIGGER_REASON_RE = re.compile(r"[a-z_]{1,64}")
_INPUT_TRIGGER_RELEASED_BY = frozenset(
    {"phase", "ttl", "restore", "player_changed", "shutdown"}
)
_INPUT_TRIGGER_OBSERVED_KEYS: dict[str, tuple[str, ...]] = {
    "input_not_drivable": ("exists", "locked", "in_active_inputs"),
    "not_held": ("released_by",),
    "aborted": ("released_by",),
}


def _input_trigger_detail(result: dict[str, Any]) -> str:
    report = result.get("input_trigger")
    if not isinstance(report, dict):
        return ""
    parts: list[str] = []
    reason = report.get("reason")
    if isinstance(reason, str) and _INPUT_TRIGGER_REASON_RE.fullmatch(reason):
        parts.append(f"reason={reason}")
    pairs: list[str] = []
    code = str(result.get("error") or "")
    for key in _INPUT_TRIGGER_OBSERVED_KEYS.get(code, ()):
        value = report.get(key)
        if key == "released_by":
            if isinstance(value, str) and value in _INPUT_TRIGGER_RELEASED_BY:
                pairs.append(f"released_by={value}")
        elif isinstance(value, int) and value in (0, 1):
            # Enforce sends a bool as 0 or 1; Python's bool is an int too.
            pairs.append(f"{key}={int(value)}")
    if pairs:
        parts.append("observed=" + " ".join(pairs))
    return "; ".join(parts)


def _bridge_error_detail(result: dict[str, Any], cmd: str | None) -> str:
    """Diagnostics the bridge filled BEFORE deciding the error, as message text.

    ui_click sets user_id, handler and clicked before it settles on not_handled
    (MCPClientBridge.c:1465-1480), and the four core UI verbs echo their request
    (ui_request, MCPClientBridge.c:2199-2218). Only the message of a ToolError
    crosses the MCP wire, so a bare code threw away the two fields that
    discriminate the cause (fb-20260829-221423-b2c4).

    vehicle_prepare_fixture is the same shape for fixture_not_ready
    (fb-20260911-230927-b1ff): telemetry.wheel_count was already on the
    result and the Python layer raised the bare code. Expected axle count
    is not in the echo, so the allowlist is observed scalars only, published
    under an ``observed=`` label (P34-P2-1). Do not invent ``expected``.

    vehicle_door's door_missing is the third: the bridge already filled the
    empty crew-door slot, and the caller needs it to fill that slot
    (inventory_attach takes slot=). Only slot, state and phase cross.

    input_trigger is the fourth: its refusals carry reason= (a token, e.g.
    no_local_setter) and observed= with the facts of that code only
    (exists/locked/in_active_inputs for input_not_drivable, released_by for
    not_held and aborted). The name it resolved is caller input and stays out.

    The decision is by VERB, never by key presence: MCPResult is one flat class
    (MCPMessages.c:423-479), so every result carries handler="", user_id=0 and
    clicked=false, and a world_spawn timeout has to stay "timeout". The click
    scalars are reported for ui_click only, an empty handler included -- no
    handler ran, which is a different diagnosis from one that ran and declined.
    The echo is whitelisted; requested_root and requested_path are echoed
    whenever the bridge sent the key (empty root included, ficha f4f2).
    matched_path is filled only after a unique match, so an empty value is
    omitted (it is not a request input). requested_text stays out on purpose,
    it would replay caller input (possibly sensitive, unbounded) into an
    error message.
    """
    if cmd == "vehicle_prepare_fixture" and str(result.get("error") or "") == "fixture_not_ready":
        return _fixture_not_ready_detail(result)
    if cmd == "vehicle_door" and str(result.get("error") or "") == "door_missing":
        return _vehicle_door_missing_detail(result)
    if cmd == "input_trigger":
        return _input_trigger_detail(result)
    if cmd not in _UI_ECHO_VERBS:
        return ""
    parts: list[str] = []
    if cmd == "ui_click":
        fields = [
            f"{key}={result[key]!r}" for key in _UI_CLICK_DIAGNOSTIC_KEYS if key in result
        ]
        if fields:
            parts.append(" ".join(fields))
    echo = result.get("ui_request")
    if isinstance(echo, dict):
        pairs = []
        for key in _UI_ECHO_KEYS:
            if key not in echo:
                continue
            value = echo[key]
            if value is None:
                continue
            # Empty matched_path is unset resolution, not an echoed input.
            if key == "matched_path" and value == "":
                continue
            pairs.append(f"{key}={value!r}")
        if pairs:
            parts.append(" ".join(pairs))
    return "; ".join(parts)


def _bridge_error(result: dict[str, Any], cmd: str | None = None) -> ToolError:
    # The message head stays the fixed code; the bridge's object_id (sent on a
    # spawn timeout, MCPBridge.c:3272) rides in a structured attribute so the
    # caller can clean up instead of duplicating, without the message carrying
    # host content across the MCP wire. For the core UI verbs the diagnostics
    # the bridge filled before the error follow the code after "; " -- see
    # _bridge_error_detail; other verbs keep the bare code except
    # vehicle_prepare_fixture/fixture_not_ready, which carries the
    # observed= telemetry allowlist the bridge already filled,
    # vehicle_door/door_missing, which carries the empty slot, and
    # input_trigger, whose refusals carry reason= and observed=.
    code = str(result.get("error") or "bridge_error")
    detail = _bridge_error_detail(result, cmd)
    if code in {"binding_retired", "run_not_owned"}:
        # The daemon also retires (binding_retired) or fences (run_not_owned)
        # already queued commands. Carry the same bounded hint and next step
        # through /await as through a refused /enqueue.
        error = ToolError(_public_enqueue_error(result))
    else:
        error = ToolError(f"{code}; {detail}" if detail else code)
    object_id = result.get("object_id")
    if isinstance(object_id, int) and not isinstance(object_id, bool) and object_id > 0:
        error.object_id = object_id
    return error


def _retail_quarantine_recipe(reason: object) -> str:
    if isinstance(reason, str) and reason in _RETAIL_QUARANTINE_REASONS:
        return f"{RETAIL_QUARANTINE_RECIPE}; reason: {reason}"
    return RETAIL_QUARANTINE_RECIPE


def _public_enqueue_error(
    payload: dict[str, Any],
    *,
    status_snapshot: dict[str, Any] | None = None,
    peer: str | None = None,
) -> str:
    """Map a remote enqueue payload to the caller-facing ToolError string.

    A known code with a valid hint travels as "<code>: <hint>"; a known code
    without a hint stays bare; an unknown code stays the bare token remote_error
    even when a hint is present. A published ready.reason that is not on the
    enqueue whitelist travels as game_not_ready:reason=<token> so the caller
    still sees client_not_polling (and the other startup reasons) instead of
    a stripped remote_error -- when that token is on the enqueue payload,
    not merely on a sibling /status snapshot. run_not_owned also carries
    next_step=session_acquire_wait: that grant adopts the ownerless run.
    """
    code = _remote_error_code(payload)
    if code == "retail_quarantine":
        return _retail_quarantine_recipe(payload.get("reason"))
    if code == "lease_required":
        if (
            isinstance(payload, dict)
            and payload.get("version_state") in {"legacy_blocked", "version_mismatch"}
        ):
            if _target_peer_down(status_snapshot, peer):
                return f"game_not_ready:reason={_game_not_ready_reason(status_snapshot, peer)}"
            expected = payload.get("expected")
            got = payload.get("got")
            return (
                f"{LEASE_REQUIRED_RECIPE}; "
                f"version_blocked:bridge {got!r} != {expected!r}"
            )
        return LEASE_REQUIRED_RECIPE
    if code == "lease_expired":
        return LEASE_EXPIRED_RECIPE
    if code == "lease_invalid":
        return LEASE_INVALID_RECIPE
    if code == "version_blocked":
        if _target_peer_down(status_snapshot, peer):
            return f"game_not_ready:reason={_game_not_ready_reason(status_snapshot, peer)}"
        expected = payload.get("expected") if isinstance(payload, dict) else None
        got = payload.get("got") if isinstance(payload, dict) else None
        if isinstance(expected, str):
            return f"version_blocked:bridge {got!r} != {expected!r}"
        return "version_blocked"
    if code in _PUBLISHED_NOT_READY_CODES:
        return f"game_not_ready:reason={code}"
    hint = _carriable_hint(payload)
    message = code
    if code != "remote_error" and hint is not None:
        message = f"{code}: {hint}"
    if code == "run_not_owned":
        # Named here, not only in the daemon's hint, so an older daemon's
        # prose still reaches the caller with the tool to call next.
        return with_next_step(message, "session_acquire_wait")
    return message


def _image_format_from_mime(mime: object) -> str:
    if mime == "image/jpeg":
        return "jpeg"
    if mime == "image/webp":
        return "webp"
    return "png"


@dataclass(frozen=True)
class ServerConfig:
    # mode: "embedded" (bare default — bind the loopback in-process, today's path),
    # "client" (proxy over HTTP to the daemon, lazily spawning it), or "daemon"
    # (standalone broker; handled by dayz_mcp.daemon.run_daemon, not build_app).
    mode: str = "embedded"
    port: int = 8765
    keyfile: str | None = None
    key: str | None = None
    expected_game_version: str | None = None
    require_version: bool = False
    idle_timeout_s: float = 1800.0
    enable_exec_enforce: bool = False
    exec_allowlist: str | None = None
    exec_audit_path: str | None = None
    log_sink: Callable[[str], None] | None = None
    client_platform: str = "unknown"
    client_platform_raw: str = ""
    task_label: str = ""
    session_ttl_s: float = 120.0
    runtime_dir: str | None = None
    # Own stdio and run the real server as a replaceable child. Orthogonal to mode:
    # the child inherits the mode flags this process was given.
    supervised: bool = False
    # CLI flag is the spawn authority for this process. It need not match
    # the registered host argv (registration-False / CLI-True is allowed).
    auto_spawn_daemon: bool = True
    # Opt-in small-model surface. The full public registry remains the default.
    tool_pack: str = "full"
    # Client mode lists a compact catalog until a lease is held and relies on the
    # host re-listing after tools/list_changed. Claude Code does not (#93, e7ef),
    # so client_platform claude lists everything from the start whatever this
    # says (_progressive_disclosure_enabled); --no-progressive-disclosure does
    # the same for any other platform.
    progressive_disclosure: bool = True


class Runtime:
    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self.loopback: LoopbackServer | None = None
        self.tool_lock = asyncio.Lock()
        self.exec_allowlist = self._load_exec_allowlist(config.exec_allowlist)
        # Idle bookkeeping for the inactivity self-shutdown watchdog (monotonic).
        self.start_monotonic = time.monotonic()
        self.last_tool_activity: float | None = None

    def touch(self) -> None:
        """Mark MCP-side activity (a tool call) so the idle watchdog stays disarmed."""
        self.last_tool_activity = time.monotonic()

    def idle_seconds(self) -> float:
        """Seconds since the last sign of life: tool call, game poll, or startup."""
        now = time.monotonic()
        last_server_poll = None
        last_client_poll = None
        if self.loopback is not None:
            snapshot = self.loopback.state.status_snapshot(now)
            last_server_poll = snapshot["peers"]["server"]["last_poll_at"]
            last_client_poll = snapshot["peers"]["client"]["last_poll_at"]
        return orphan_guard.compute_idle_seconds(
            now, self.start_monotonic, self.last_tool_activity, last_server_poll, last_client_poll
        )

    @property
    def state(self):
        if self.loopback is None:
            raise RuntimeError("loopback not started")
        return self.loopback.state

    def start_loopback(self) -> None:
        if self.loopback is not None:
            return
        key = self.config.key if self.config.key is not None else read_key(required_keyfile(self.config))
        self.loopback = LoopbackServer(
            self.config.port,
            key,
            log_sink=self.config.log_sink or (lambda message: print(message, file=sys.stderr, flush=True)),
            enable_exec_enforce=self.config.enable_exec_enforce,
            version_validator=lambda version: self._version_state_for(version)[0],
            exec_allowlist=self.exec_allowlist,
            exec_audit=self.audit_exec if self.config.enable_exec_enforce else None,
            status_provider=lambda: self.status(),
        )
        self.loopback.start()

    def stop_loopback(self) -> None:
        if self.loopback is None:
            return
        self.loopback.stop()
        self.loopback = None

    def status(self) -> dict[str, Any]:
        return core.build_status(
            self.state.status_snapshot(),
            require_version=self.config.require_version,
            expected_game_version=self.config.expected_game_version,
        )

    def _version_state_for(self, version: str | None) -> tuple[str, str]:
        return core.version_state_for(
            version,
            require_version=self.config.require_version,
            expected_game_version=self.config.expected_game_version,
        )

    async def bridge_status_payload(
        self,
        *,
        registered_tools: frozenset[str] | None = None,
        intended_tools: frozenset[str] | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Async status accessor used by the bridge_status tool (uniform with
        ClientRuntime, which fetches /status over HTTP).

        When ``registered_tools`` is provided, capability comparison (incl. ach)
        runs BEFORE ``compute_bridge_ready`` so ready=false on mismatch/unknown.
        ``timeout_s`` is accepted for duck-typing parity with ClientRuntime and
        ignored in embedded mode.
        """
        del timeout_s  # embedded status is local; no HTTP budget
        payload = self.status()
        if registered_tools is not None:
            payload = _with_capability_comparison(
                payload, registered_tools, intended_tools
            )
        return _with_ready(payload)

    def ensure_peer_allowed(self, peer: str) -> None:
        snapshot = self.status()
        peer_key = "client_peer" if peer == "client" else "server_peer"
        peer_status = snapshot[peer_key]
        state = peer_status["version_state"]
        if state == "never_polled_this_generation":
            if snapshot.get("require_version"):
                raise ToolError(
                    f"game_not_ready:reason={_game_not_ready_reason(snapshot, peer)}"
                )
            return
        if state in {"legacy_blocked", "version_mismatch"}:
            if peer_status.get("last_poll_age_s") is None or not _peer_is_live(peer_status):
                raise ToolError(
                    f"game_not_ready:reason={_game_not_ready_reason(snapshot, peer)}"
                )
            raise ToolError(f"{peer} peer version_state={state}: {peer_status['version_detail']}")

    def liveness_message(self, peer: str) -> str:
        snapshot = self.status()
        peer_key = "client_peer" if peer == "client" else "server_peer"
        peer_status = snapshot[peer_key]
        age = peer_status["last_poll_age_s"]
        if age is None:
            age_text = "has never polled"
        else:
            age_text = f"last poll {age:.1f}s ago"
        return (
            f"{peer} peer {age_text}; queue_depth={peer_status['queue_depth']}; "
            f"version_state={peer_status['version_state']}"
        )

    async def call_bridge(self, cmd: str, args: dict[str, Any], peer: str, timeout_s: float) -> dict[str, Any]:
        self.touch()
        early = _world_read_not_ready(self, cmd, self.status())
        if early is not None:
            return early
        self.ensure_peer_allowed(peer)
        status, payload = self.state.enqueue_command(
            cmd, args, peer=peer, operation_timeout_s=timeout_s
        )
        if status != 200:
            raise ToolError(
                _public_enqueue_error(payload, status_snapshot=self.status(), peer=peer)
                if isinstance(payload, dict)
                else payload.get("error", f"enqueue_failed_http_{status}")
            )

        command_id = int(payload["id"])
        result = await self.wait_for_result(cmd, command_id, peer, timeout_s)
        return _with_bridge_success_hints(self, cmd, result)

    async def call_exec_enforce(self, args: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        self.touch()
        self.ensure_peer_allowed("server")
        status, payload = await asyncio.to_thread(
            self.state.enqueue_command,
            "exec_enforce",
            args,
            peer="server",
            operation_timeout_s=timeout_s,
        )
        if status != 200:
            raise ToolError(
                _public_enqueue_error(
                    payload, status_snapshot=self.status(), peer="server"
                )
                if isinstance(payload, dict)
                else payload.get("error", f"enqueue_failed_http_{status}")
            )

        command_id = int(payload["id"])
        return await self.wait_for_result("exec_enforce", command_id, "server", timeout_s)

    async def wait_for_result(self, cmd: str, command_id: int, peer: str, timeout_s: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            result = self.state.take_result(command_id, remove=True)
            if result is not None:
                # Bridge serializes Enforce `ok` as int 0/1, so `is False` never
                # matched a business error (`0 is False` is False) and surfaced
                # bridge failures as success. Treat any falsy ok as a ToolError.
                if not result.get("ok"):
                    raise _bridge_error(result, cmd)
                return result_prune.prune_unfilled_fields(cmd, result)
            await asyncio.sleep(POLL_INTERVAL_S)

        self.state.abandon_command(command_id, "tool_timeout")
        raise ToolError(f"timeout waiting for {cmd} id={command_id}; {self.liveness_message(peer)}")

    async def enqueue_bridge(
        self, cmd: str, args: dict[str, Any], peer: str, timeout_s: float
    ) -> int:
        self.touch()
        self.ensure_peer_allowed(peer)
        status, payload = self.state.enqueue_command(
            cmd, args, peer=peer, operation_timeout_s=timeout_s
        )
        if status != 200:
            raise ToolError(
                _public_enqueue_error(payload, status_snapshot=self.status(), peer=peer)
                if isinstance(payload, dict)
                else payload.get("error", f"enqueue_failed_http_{status}")
            )
        return int(payload["id"])

    async def probe_bridge_result(
        self, cmd: str, command_id: int, peer: str
    ) -> dict[str, Any] | None:
        result = self.state.take_result(command_id, remove=True)
        if result is None:
            return None
        if not result.get("ok"):
            raise _bridge_error(result, cmd)
        return result_prune.prune_unfilled_fields(cmd, result)

    async def abandon_bridge(self, command_id: int, reason: str) -> None:
        self.state.abandon_command(command_id, reason)

    def audit_exec(self, expr: str, verdict: str, main_fn: str = "", command_id: int | None = None) -> None:
        audit_path = self.exec_audit_path()
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

    def exec_audit_path(self) -> Path:
        if self.config.exec_audit_path is not None:
            return Path(self.config.exec_audit_path)
        return Path(__file__).resolve().parents[1] / "_audit" / "exec_enforce.jsonl"

    def _load_exec_allowlist(self, path: str | None) -> set[str]:
        return core.load_exec_allowlist(path)


class _CallBudgetExpired(Exception):
    """``ClientRuntime._call`` had no time left to start this HTTP hop."""


def _status_disowns_lease(status: object, lease_id: object) -> bool:
    """Whether a /session/status reply shows this identity no longer holds a lease.

    The daemon honours a token only while its lease is this identity's active
    lease (SessionCoordinator._validate_token_locked), which status reports as
    self.state "active" with that lease_id. none, queued and releasing all say
    it is not: an expired lease reads releasing while its cleanup runs, then
    none. "active" under another lease_id says so too, when both ids are known.
    A reply without a readable self block decides nothing.
    """
    own = status.get("self") if isinstance(status, dict) else None
    if not isinstance(own, dict):
        return False
    state = own.get("state")
    if state in {"none", "queued", "releasing"}:
        return True
    if state != "active" or not isinstance(lease_id, str) or not lease_id:
        return False
    named = own.get("lease_id")
    return isinstance(named, str) and bool(named) and named != lease_id


class ClientRuntime:
    """Client-mode runtime: proxies bridge calls over HTTP to the broker daemon.

    Holds NO loopback bind, so no orphan-guard is needed here. The daemon owns the
    version gate, exec chokepoint, exclusive loopback bind and idle watchdog; this only forwards
    (POST /enqueue + GET /await) and reads /status. The daemon is discovered via
    GET /status and lazily spawned (detached) on first need; a connection failure
    triggers one re-spawn + retry, so an idle-reaped daemon self-heals.

    Exposes the same surface build_app's tools call on Runtime (tool_lock, touch,
    call_bridge, call_exec_enforce, bridge_status_payload). ``spawn_fn``/``probe_fn``
    are injectable so tests exercise discovery/spawn without a real subprocess.
    """

    _time_fn = staticmethod(time.monotonic)
    # Declared on the class, not only in __init__: a bare instance built with
    # object.__new__ (BUG-037 timeout cap, H14 stale-policy) still has to
    # answer for the closed default.
    _allow_stale_policy: bool = False
    _carrier_io_hook: Callable[[int, bool], None] | None = None

    def __init__(
        self,
        config: ServerConfig,
        *,
        spawn_fn: Callable[[], int | None] | None = None,
        probe_fn: Callable[..., bool] | None = None,
        time_fn: Callable[[], float] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        startup_budget_s: float | None = None,
    ) -> None:
        self.config = config
        self.tool_lock = asyncio.Lock()
        self._allow_stale_policy = False
        daemon_policy = load_normal_daemon_policy()
        provenance = host_config.resolve_daemon_provenance()
        if (
            type(provenance.port) is not int
            or type(config.port) is not int
            or not 1 <= provenance.port <= 65535
            or config.port != provenance.port
        ):
            raise host_config.HostConfigError("daemon_provenance_conflict")
        if (
            daemon_policy.port != provenance.port
            or daemon_policy.argv != tuple(provenance.argv)
            or os.path.normcase(os.path.normpath(daemon_policy.keyfile))
            != os.path.normcase(os.path.normpath(provenance.keyfile))
            or os.path.normcase(os.path.normpath(daemon_policy.native_executable))
            != os.path.normcase(os.path.normpath(provenance.native_executable))
            or os.path.normcase(os.path.normpath(daemon_policy.cwd))
            != os.path.normcase(os.path.normpath(provenance.cwd))
        ):
            raise host_config.HostConfigError("daemon_provenance_conflict")
        authority_paths = (
            provenance.launch_executable,
            provenance.native_executable,
            provenance.cwd,
        )
        if any(
            not isinstance(value, str) or not value or not os.path.isabs(value)
            for value in authority_paths
        ):
            raise host_config.HostConfigError("daemon_provenance_conflict")
        local_launch = host_config._local_launch_executable()
        local_native = host_config._local_native_executable()
        if (
            os.path.normcase(os.path.normpath(provenance.launch_executable))
            != os.path.normcase(os.path.normpath(local_launch))
            or os.path.normcase(os.path.normpath(provenance.native_executable))
            != os.path.normcase(os.path.normpath(local_native))
        ):
            raise host_config.HostConfigError("daemon_provenance_conflict")
        if (
            not isinstance(provenance.argv, (tuple, list))
            or not provenance.argv
            or any(not isinstance(value, str) or not value for value in provenance.argv)
            or os.path.normcase(os.path.normpath(provenance.argv[0]))
            != os.path.normcase(os.path.normpath(provenance.launch_executable))
        ):
            raise host_config.HostConfigError("daemon_provenance_conflict")
        if (
            type(provenance.auto_spawn_daemon) is not bool
            or type(config.auto_spawn_daemon) is not bool
        ):
            raise host_config.HostConfigError("daemon_provenance_conflict")
        # --no-daemon-autospawn is a local process flag. Requiring it to match
        # the registered host argv aborted the whole stdio client (pipeline_*
        # included). Honor the CLI: False disables spawn; True may spawn.
        authority_keyfile = host_config.require_matching_keyfile(
            provenance.keyfile, provenance.keyfile
        )
        keyfile = host_config.require_matching_keyfile(
            required_keyfile(config), authority_keyfile
        )
        self._credential_provider = (
            daemon_credential.RefreshingDaemonCredential(
                policy=daemon_policy,
                request_fn=lambda **kwargs: (
                    orphan_guard.verified_daemon_http_request(
                        time_fn=self._time_fn,
                        **kwargs,
                    )
                ),
            )
        )
        self.host = "127.0.0.1"
        self.port = provenance.port
        self.base = f"http://{self.host}:{self.port}"
        self._daemon_executable = provenance.native_executable
        self._daemon_argv = tuple(provenance.argv)
        self._daemon_cwd = provenance.cwd
        self._auto_spawn_daemon = bool(config.auto_spawn_daemon)
        self._spawn_attempted = False
        self._log = config.log_sink or (lambda message: print(message, file=sys.stderr, flush=True))
        self._spawn_lock = threading.Lock()
        self._probe = probe_fn
        self._probe_key = config.key or ""
        self._spawn_fn = spawn_fn or self._default_spawn
        self._time_fn = time_fn or time.monotonic
        self._sleep_fn = sleep_fn or time.sleep
        self._startup_budget_s = daemon.validated_startup_budget_s(startup_budget_s)
        # The coordinator compares all six ClientIdentity fields by value, so a
        # fresh identity cannot reach a live lease. A carrier crosses the whole
        # identity or nothing: carrying only the session id is worse than none
        # (identity_mismatch). Measured in REHEARSAL-LEASE.md, 34/34.
        # With no lease (the dayz_test_run path; session_release clears the
        # carrier) the same supervisor still reuses one full identity from a
        # file next to that carrier. Embedded and unsupervised workers have no
        # HANDOFF_ENV and mint a new id, as does a new supervisor directory.
        self._handoff_path = os.environ.get(session_handoff.HANDOFF_ENV) or None
        # Confirmed write time only. A reservation that has not landed yet lives
        # in _carrier_reserved_at / _carrier_reserve_gen, so a failed fsync does
        # not burn the next refresh (fb-20260928-021456-3272, review F3).
        self._carrier_written_at = float("-inf")
        self._carrier_reserved_at = float("-inf")
        self._carrier_reserve_gen = 0
        self._carrier_degraded = False
        # Writes and clears share the lock. The generation is bumped on the
        # caller before the file work is handed to a thread, and that thread
        # re-checks it under the lock, so an older write cannot land after a
        # newer clear once the fsync is off the event loop.
        self._carrier_lock = threading.Lock()
        self._carrier_gen = 0
        self._carrier_tasks: set[asyncio.Task[None]] = set()
        self._carrier_io_hook: Callable[[int, bool], None] | None = None
        carried = (
            session_handoff.consume_handoff(self._handoff_path)
            if self._handoff_path
            else None
        )
        self.identity = self._identity_for_worker(config, carried)
        if config.client_platform_raw:
            self._log(
                "CLIENT: platform alias normalized "
                f"raw={config.client_platform_raw} canonical={config.client_platform}"
            )
        self._control = ControlClient(
            policy=daemon_policy,
            identity=ControlIdentity(**self.identity.to_payload()),
            credential_provider=self._credential_provider,
        )
        self.daemon_policy = daemon_policy
        if self._handoff_path:
            self._control.on_lease_change = self._mirror_lease_to_carrier
        if carried is not None:
            # The token names the same lease the previous generation held. Whether it
            # still OWNS the box is not decided here and is not assumed: the daemon
            # re-validates on every authorize, and answers lease_invalid if a slow
            # recycle let the TTL lapse and the queue take it.
            self._control.active_lease_token = carried.lease_token
            self._control.active_lease_id = carried.lease_id
            self._log(
                f"SESSION: adopted lease {carried.lease_id} from worker generation "
                f"{carried.generation}; ownership re-checked by the daemon"
            )

    def _mint_identity(self, config: ServerConfig) -> ClientIdentity:
        return ClientIdentity(
            platform=config.client_platform,
            pid=os.getpid(),
            ppid=os.getppid(),
            started_at_utc=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            session_id=str(uuid.uuid4()),
            task_label=(config.task_label or os.environ.get("DAYZ_MCP_TASK_LABEL", ""))[:120],
        )

    def _supervisor_identity_file(self) -> Path | None:
        if not self._handoff_path:
            return None
        return session_handoff.supervisor_identity_path(self._handoff_path)

    def _remember_supervisor_identity(self, identity: ClientIdentity) -> None:
        path = self._supervisor_identity_file()
        if path is None:
            return
        try:
            session_handoff.store_supervisor_identity_if_absent(path, identity)
        except (OSError, TypeError, ValueError) as exc:
            # Losing the file costs the launcher right on a later reload with
            # no lease. It must not fail the worker that is already starting.
            self._log(f"SESSION: supervisor identity write failed: {exc}")

    def _identity_for_worker(
        self,
        config: ServerConfig,
        carried: session_handoff.SessionHandoff | None,
    ) -> ClientIdentity:
        """Carrier identity, else the supervisor file, else a new mint.

        A live carrier wins so the lease still matches all six fields. The
        file is accepted only when it still sits in the directory it names
        and this worker's supervisor is that process (pid and creation
        time; a venv launcher between them is not the supervisor). It is
        written only when absent, and never when it is unreadable or
        rejected: a doubtful file must not be replaced with a new session.
        """

        if carried is not None:
            self._remember_supervisor_identity(carried.identity)
            return carried.identity
        path = self._supervisor_identity_file()
        if path is None:
            return self._mint_identity(config)
        try:
            loaded = session_handoff.load_supervisor_identity(path)
        except Exception:
            loaded = None
        if loaded is not None:
            return loaded
        minted = self._mint_identity(config)
        if not path.exists():
            self._remember_supervisor_identity(minted)
        return minted

    def _mirror_lease_to_carrier(
        self, lease_token: str | None, lease_id: str | None
    ) -> None:
        """Keep the carrier in step with the lease, so any death hands it on.

        Written on every change rather than only when a recycle is requested: a worker
        that dies unplanned leaves the carrier behind for its replacement, and a worker
        that releases its lease leaves nothing to inherit. The file work runs off the
        event loop; this only reserves a generation.
        """
        if not getattr(self, "_handoff_path", None):
            return
        lock = getattr(self, "_carrier_lock", None)
        if lock is None:
            return
        token = lease_token if lease_token and lease_id else None
        named = lease_id if token else None
        with lock:
            self._carrier_gen += 1
            generation = self._carrier_gen
        self._submit_carrier_io(generation, token, named, refresh=False)

    def _submit_carrier_io(
        self,
        generation: int,
        lease_token: str | None,
        lease_id: str | None,
        *,
        refresh: bool,
    ) -> None:
        """Run one carrier write or clear off the loop when a loop is running.

        The generation was already bumped. The optional hook runs before the
        lock so a newer clear can be reserved while this write is still waiting.
        The thread calls named methods. A getattr result called with two
        arguments is an unaccredited HTTP path to the runtime audit.
        """

        def run() -> None:
            self._notify_carrier_io_hook(generation, refresh)
            self._apply_carrier_io(
                generation, lease_token, lease_id, refresh=refresh
            )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            run()
            return
        task = loop.create_task(asyncio.to_thread(run))
        tasks = self._carrier_tasks
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    def _notify_carrier_io_hook(self, generation: int, refresh: bool) -> None:
        """Pause point for tests. The attribute is read by name, not via getattr."""
        hook = self._carrier_io_hook
        if hook is None:
            return
        hook(generation, refresh)

    def _apply_carrier_io(
        self,
        generation: int,
        lease_token: str | None,
        lease_id: str | None,
        *,
        refresh: bool,
    ) -> None:
        lock = getattr(self, "_carrier_lock", None)
        if lock is None:
            return
        with lock:
            if generation != self._carrier_gen:
                self._release_carrier_reservation(generation)
                return
            if refresh:
                # The interval was reserved at submit time. Checking it again
                # here would skip the write that reservation just paid for.
                live_id = self._control.active_lease_id
                if (
                    self._control.active_lease_token != lease_token
                    or not isinstance(live_id, str)
                    or not live_id
                ):
                    self._release_carrier_reservation(generation)
                    return
                lease_id = live_id
            try:
                if lease_token and lease_id:
                    session_handoff.write_handoff(
                        self._handoff_path,
                        identity=self.identity,
                        lease_token=lease_token,
                        lease_id=lease_id,
                        generation=0,
                    )
                    self._carrier_written_at = self._time_fn()
                    self._carrier_degraded = False
                    self._release_carrier_reservation(generation)
                else:
                    session_handoff.clear_handoff(self._handoff_path)
                    self._carrier_degraded = False
            except (OSError, ValueError, TypeError) as exc:
                # Losing the carrier costs a lease across the next recycle; it
                # must never cost the call that happened to change the lease.
                # A rewrite the tombstone still rejects is not a persisted lease:
                # the same degraded flag a failed clear already publishes.
                self._log(f"SESSION: carrier write failed: {exc}")
                if lease_token and lease_id:
                    self._release_carrier_reservation(generation)
                    if isinstance(exc, session_handoff.TombstoneStillRejects):
                        self._carrier_degraded = True
                elif generation == self._carrier_gen:
                    self._carrier_degraded = True

    async def _finish_carrier_io(self) -> None:
        """Wait for carrier tasks already scheduled. A bare runtime has none."""
        pending = getattr(self, "_carrier_tasks", None)
        if not pending:
            return
        current = [task for task in list(pending) if not task.done()]
        if current:
            await asyncio.gather(*current)

    async def _refresh_lease_carrier(self, lease_token: str) -> None:
        """Rewrite the carrier after the daemon renewed this lease.

        Heartbeats do this on success. Commands do it only when the response
        names the lease this client holds (fb-20260928-021456-3272): a read
        with no valid lease is authorized, and a foreign id is not proof.
        At most one rewrite per _CARRIER_REFRESH_S. The anti-burst reservation
        is separate from the confirmed write time: it is taken here, under the
        lock, before the thread runs, and released if that generation fails or
        is discarded. A later generation's reservation and confirmed time stay.
        The thread re-reads the live lease and skips the write if a release
        has since cleared it.
        """
        # getattr: tests build a bare ClientRuntime (object.__new__) to check how
        # its session methods compose; such an instance has no carrier.
        if not getattr(self, "_handoff_path", None):
            return
        lock = getattr(self, "_carrier_lock", None)
        if lock is None:
            return
        with lock:
            now = self._time_fn()
            if now - self._carrier_written_at < _CARRIER_REFRESH_S:
                return
            if (
                self._carrier_reserve_gen != 0
                and now - self._carrier_reserved_at < _CARRIER_REFRESH_S
            ):
                return
            live_id = self._control.active_lease_id
            if (
                self._control.active_lease_token != lease_token
                or not isinstance(live_id, str)
                or not live_id
            ):
                return
            self._carrier_reserved_at = now
            self._carrier_gen += 1
            generation = self._carrier_gen
            self._carrier_reserve_gen = generation
        self._submit_carrier_io(generation, lease_token, live_id, refresh=True)
        await self._finish_carrier_io()

    def _release_carrier_reservation(self, generation: int) -> None:
        """Drop this generation's anti-burst hold. A newer hold stays."""
        if self._carrier_reserve_gen != generation:
            return
        self._carrier_reserve_gen = 0
        self._carrier_reserved_at = float("-inf")

    async def _accept_command_renewal(self, payload: object) -> None:
        """Refresh only if this response names the lease held right now.

        Absence, a non-string, an empty string, or any other id means the
        daemon did not just renew this client's lease. Fail closed: do not
        re-seal a token it did not name.
        """
        if not isinstance(payload, dict):
            return
        renewed = payload.get("lease_id")
        if not isinstance(renewed, str) or not renewed:
            return
        control = getattr(self, "_control", None)
        if control is None:
            return
        held = getattr(control, "active_lease_id", None)
        if not isinstance(held, str) or held != renewed:
            return
        token = getattr(control, "active_lease_token", None)
        if not isinstance(token, str) or not token:
            return
        await self._refresh_lease_carrier(token)

    def touch(self) -> None:
        # No local idle watchdog in client mode; the daemon tracks its own idle.
        return

    def _session_state_snapshot(self) -> tuple[str | None, str | None]:
        return self._control.active_lease_token, self._control.active_ticket

    @property
    def active_lease_token(self) -> str | None:
        return self._control.active_lease_token

    @active_lease_token.setter
    def active_lease_token(self, value: str | None) -> None:
        self._control.active_lease_token = value

    @property
    def active_ticket(self) -> str | None:
        return self._control.active_ticket

    @active_ticket.setter
    def active_ticket(self, value: str | None) -> None:
        self._control.active_ticket = value

    @property
    def active_operation_id(self) -> str | None:
        return self._control.active_operation_id

    @active_operation_id.setter
    def active_operation_id(self, value: str | None) -> None:
        self._control.active_operation_id = value

    async def _control_with_lazy_spawn(
        self,
        method: Callable[..., Awaitable[dict[str, object]]],
        *args: object,
        **kwargs: object,
    ) -> dict[str, Any]:
        async def invoke() -> dict[str, object]:
            return await method(*args, **kwargs)

        lifecycle, held_token = self._lifecycle_held_token(method)

        def public_error_code(error: ControlClientError) -> str:
            if error.code == "retail_quarantine":
                return _retail_quarantine_recipe(error.hint)
            if error.code == "lease_required":
                return LEASE_REQUIRED_RECIPE
            if error.code == "lease_expired":
                return LEASE_EXPIRED_RECIPE
            if error.code == "lease_invalid":
                return LEASE_INVALID_RECIPE
            if error.hint and (
                error.code in _REMOTE_ERROR_CODES
                or error.code in _CONTROL_CLIENT_ERROR_CODES
            ):
                return str(error)
            if (
                error.code in _REMOTE_ERROR_CODES
                or error.code in _CONTROL_CLIENT_ERROR_CODES
            ):
                return error.code
            return "remote_error"

        try:
            try:
                return await invoke()
            except ControlClientError as error:
                retryable = (
                    error.code == "daemon_unavailable"
                    and error.request_stage == "pre_request"
                    and error.http_bytes_sent == 0
                )
                if retryable:
                    spawned = await asyncio.to_thread(self._ensure_daemon)
                    if not spawned:
                        raise ToolError(self._daemon_missing_error()) from None
                    try:
                        return await invoke()
                    except ControlClientError as retry_error:
                        await self._observe_lifecycle_error(
                            lifecycle, retry_error, held_token
                        )
                        raise ToolError(public_error_code(retry_error)) from None
                await self._observe_lifecycle_error(lifecycle, error, held_token)
                raise ToolError(public_error_code(error)) from None
        finally:
            await self._finish_carrier_io()

    def _lifecycle_held_token(self, method: object) -> tuple[bool, str | None]:
        """Whether ``method`` is lifecycle close/reap, and the token it is about to use."""
        control = getattr(self, "_control", None)
        if control is None:
            return False, None
        # Each attribute access builds a new bound method, so identity does not work.
        wanted = getattr(method, "__func__", None)
        implemented = {
            getattr(getattr(control, "lifecycle_close", None), "__func__", None),
            getattr(getattr(control, "lifecycle_close_roles", None), "__func__", None),
            getattr(getattr(control, "lifecycle_reap", None), "__func__", None),
        }
        if wanted is None or wanted not in implemented:
            return False, None
        token = getattr(control, "active_lease_token", None)
        if not isinstance(token, str) or not token:
            return True, None
        return True, token

    async def _observe_lifecycle_error(
        self,
        lifecycle: bool,
        error: ControlClientError,
        held_token: str | None,
    ) -> None:
        """Same lease comparison as enqueue, for a close/reap that did not return.

        A verified ``lease_id`` refreshes only when it is the lease held now.
        ``lease_invalid`` / ``lease_expired`` then clear that token, so the
        clear is the newer generation.
        """
        if not lifecycle:
            return
        body = error.body
        if isinstance(body, VerifiedErrorBody) and isinstance(body.lease_id, str) and body.lease_id:
            await self._accept_command_renewal({"lease_id": body.lease_id})
        if error.code in _STALE_LEASE_ERRORS and held_token is not None:
            self._control._clear_matching_lease(held_token)
            await self._finish_carrier_io()

    async def session_acquire(self, purpose: str) -> dict[str, Any]:
        return await self._control_with_lazy_spawn(
            self._control.session_acquire, purpose
        )

    async def session_wait(
        self, ticket: str, timeout_s: float = 30.0
    ) -> dict[str, Any]:
        return await self._control_with_lazy_spawn(
            self._control.session_wait, ticket, timeout_s=timeout_s
        )

    async def session_cancel(self, ticket: str) -> dict[str, Any]:
        return await self._control_with_lazy_spawn(
            self._control.session_cancel, ticket
        )

    async def session_acquire_wait(
        self,
        purpose: str,
        max_wait_s: float | None = None,
        progress_cb: Callable[[float, float | None, str | None], Awaitable[None]]
        | None = None,
    ) -> dict[str, Any]:
        payload = await self._control_with_lazy_spawn(
            self._control.session_acquire_wait,
            purpose,
            max_wait_s=max_wait_s,
            progress_cb=progress_cb,
        )
        return await self._release_protected_grant(payload)

    async def _release_protected_grant(self, payload: object) -> object:
        """Drop a grant whose adopt came back run_protected.

        The daemon leaves the lease active. This client releases it and
        answers box_protected. A release that throws keeps the token so the
        caller can retry; it is not reported as an active grant.
        """

        if not isinstance(payload, dict):
            return payload
        adopted = payload.get("adopted_run")
        if not isinstance(adopted, dict) or adopted.get("error") != "run_protected":
            return payload
        token = payload.get("lease_token")
        release_error: str | None = None
        if isinstance(token, str) and token:
            try:
                await self.session_release(token)
            except Exception as exc:
                release_error = type(exc).__name__
        answer: dict[str, Any] = {
            "ok": False,
            "error": "box_protected",
            "status": "released" if release_error is None else "release_failed",
            "adopted_run": adopted,
            "use_state": adopted.get("use_state"),
            "hint": adopted.get("hint"),
        }
        if "use_reason" in adopted:
            answer["use_reason"] = adopted["use_reason"]
        if "retry_after_s" in adopted:
            answer["retry_after_s"] = adopted["retry_after_s"]
        if release_error is not None:
            answer["release_error"] = release_error
            answer["lease_token"] = token
        return answer

    async def session_heartbeat(self, lease_token: str) -> dict[str, Any]:
        result = await self._control_with_lazy_spawn(
            self._control.session_heartbeat, lease_token
        )
        await self._refresh_lease_carrier(lease_token)
        return result

    async def session_release(self, lease_token: str) -> dict[str, Any]:
        result = await self._control_with_lazy_spawn(
            self._control.session_release, lease_token
        )
        # The daemon has released. A clear that could not even seal a tombstone
        # must not look like the carrier was cleared (review F2).
        if getattr(self, "_carrier_degraded", False) and isinstance(result, dict):
            result = dict(result)
            result["carrier_clear"] = "degraded"
        return result

    async def session_status(self) -> dict[str, Any]:
        # Read before the request, so a token granted while it is in flight is
        # never the one forgotten below. getattr: tests compose a bare runtime
        # with a control fake that tracks no lease.
        held_token = getattr(self._control, "active_lease_token", None)
        held_lease_id = getattr(self._control, "active_lease_id", None)
        status = await self._control_with_lazy_spawn(self._control.session_status)
        if isinstance(held_token, str) and held_token and _status_disowns_lease(
            status, held_lease_id
        ):
            # b753 review F2: the daemon no longer holds the lease (it expired,
            # was released, or the daemon restarted), so forget the token as a
            # lease_expired reply does. ControlClient.session_status stays a pure
            # read; its own recovery is reconcile_idle_session, which proves the
            # identity idle and cancels the operation before clearing it.
            self._control._clear_matching_lease(held_token)
            await self._finish_carrier_io()
        return status

    # 296b r3: the daemon's client dump registry. Never spawns a daemon: a
    # daemon that is not running holds no record, and the caller maps any
    # error to a null diagnosis.
    async def client_dumps_open(self, baseline: dict[str, object]) -> dict[str, Any]:
        return await self._control.client_dumps_open(baseline)

    async def client_dumps_bind(self, token: str, run_id: str) -> dict[str, Any]:
        return await self._control.client_dumps_bind(token, run_id)

    async def client_dumps_get(self, run_ids: list[str]) -> dict[str, Any]:
        return await self._control.client_dumps_get(run_ids)

    async def session_box_status(
        self,
        *,
        wait: bool = False,
        ticket: str | None = None,
        done: bool = False,
        claim: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, object] = {}
        if wait:
            payload["box_wait"] = True
        if isinstance(ticket, str) and ticket:
            payload["box_ticket"] = ticket
        if done:
            payload["box_wait_done"] = True
        if claim:
            payload["box_wait_claim"] = True
        return await self._control_with_lazy_spawn(
            self._control._session_call, "/session/status", payload
        )

    async def reconcile_idle_session(self) -> dict[str, Any]:
        return await self._control_with_lazy_spawn(
            self._control.reconcile_idle_session
        )

    async def lifecycle_status(self) -> dict[str, Any]:
        return await self._control_with_lazy_spawn(self._control.lifecycle_status)

    async def lifecycle_close(self, run_id: str) -> dict[str, Any]:
        result = await self._control_with_lazy_spawn(
            self._control.lifecycle_close, run_id
        )
        await self._accept_command_renewal(result)
        if isinstance(result, dict):
            result.pop("lease_id", None)
        return result

    async def lifecycle_close_roles(
        self, run_id: str, roles: list[str]
    ) -> dict[str, Any]:
        result = await self._control_with_lazy_spawn(
            self._control.lifecycle_close_roles, run_id, roles
        )
        await self._accept_command_renewal(result)
        if isinstance(result, dict):
            result.pop("lease_id", None)
        return result

    async def lifecycle_reap(self, run_id: str) -> dict[str, Any]:
        result = await self._control_with_lazy_spawn(
            self._control.lifecycle_reap, run_id
        )
        await self._accept_command_renewal(result)
        if isinstance(result, dict):
            result.pop("lease_id", None)
        return result

    def _default_spawn(self) -> int | None:
        # outside_app: the daemon must not inherit the client app's registry
        # virtualization (daemon._spawn_outside_app).
        return daemon.spawn_detached(
            list(self._daemon_argv),
            log=self._log,
            cwd=self._daemon_cwd,
            outside_app=True,
        )

    def _daemon_healthy(self, deadline: float) -> bool:
        if self._probe is not None:
            return self._probe(
                self.port,
                self._probe_key,
                deadline=deadline,
                expected_executable=self._daemon_executable,
                expected_argv=list(self._daemon_argv),
                expected_cwd=self._daemon_cwd,
            )
        try:
            status, body = self._credential_provider.request_with_refresh(
                method="GET",
                path="/status",
                query={},
                body=None,
                headers={},
                deadline=deadline,
            )
        except AccreditedTransportError as error:
            if error.code == "daemon_identity_unverified":
                raise ToolError(error.code) from None
            if (
                error.request_stage == "pre_request"
                and error.http_bytes_sent == 0
            ):
                return False
            raise ToolError("daemon_response_ambiguous") from None
        except daemon_credential.CredentialRefreshError as error:
            raise ToolError(error.code) from None
        if not 200 <= status < 300:
            raise ToolError("daemon_health_unexpected_status")
        try:
            payload = orphan_guard._decode_status_json(body)
        except Exception:
            raise ToolError("daemon_health_invalid_response") from None
        if not orphan_guard._status_payload_is_exact_daemon(
            payload,
            expected_generation=None,
        ):
            raise ToolError("daemon_health_invalid_response")
        return True

    def _daemon_missing_error(self) -> str:
        if not self._auto_spawn_daemon:
            return DAEMON_AUTOSPAWN_DISABLED
        if getattr(self, "_spawn_attempted", False):
            return DAEMON_AUTOSPAWN_ALREADY
        return "daemon_unavailable"

    def _ensure_daemon(self, deadline: float | None = None) -> bool:
        """Discover the daemon; spawn at most once per unsuccessful streak.

        The first spawn can block up to ``_startup_budget_s`` (typically 5-12s)
        waiting for GET /status. A failed spawn leaves ``_spawn_attempted`` set
        so later misses fail fast. The flag clears when /status is healthy.
        Does not attach a parent-death watchdog: the daemon outlives this
        session.
        """
        startup_deadline = self._time_fn() + self._startup_budget_s
        if deadline is not None:
            startup_deadline = min(startup_deadline, float(deadline))
        deadline = startup_deadline
        if self._daemon_healthy(deadline):
            self._spawn_attempted = False
            return True
        if self._time_fn() >= deadline:
            return False
        if not self._auto_spawn_daemon:
            self._log(
                f"CLIENT: no daemon on 127.0.0.1:{self.port}; auto-spawn disabled"
            )
            return False
        with self._spawn_lock:
            if self._time_fn() >= deadline:
                return False
            if self._daemon_healthy(deadline):
                self._spawn_attempted = False
                return True
            if self._time_fn() >= deadline:
                return False
            if getattr(self, "_spawn_attempted", False):
                return False
            self._spawn_attempted = True
            self._log(f"CLIENT: no daemon on 127.0.0.1:{self.port}; spawning")
            self._spawn_fn()
            while True:
                remaining = deadline - self._time_fn()
                if remaining <= 0.0:
                    break
                if self._daemon_healthy(deadline):
                    self._spawn_attempted = False
                    return True
                remaining = deadline - self._time_fn()
                if remaining <= 0.0:
                    break
                self._sleep_fn(min(0.1, remaining))
        return False

    @staticmethod
    def _decode_body(body: str) -> dict[str, Any]:
        """Parse a daemon HTTP body into a dict, or raise ToolError. A misbehaving
        responder returning non-JSON or a non-object 200 must surface as a clean
        ToolError, not a raw JSONDecodeError/TypeError that crashes the tool."""
        try:
            parsed = json.loads(body or "{}")
        except ValueError as exc:
            raise ToolError("daemon_bad_body") from exc
        if not isinstance(parsed, dict):
            raise ToolError("daemon_bad_body")
        return parsed

    def _request_once(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
        timeout: float = 5.0,
    ) -> tuple[int, dict[str, Any]]:
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(float(timeout))
            or timeout <= 0.0
        ):
            raise ValueError("invalid_daemon_request_timeout")
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        deadline = self._time_fn() + float(timeout)
        refresh_kwargs: dict[str, object] = {}
        if self._allow_stale_policy:
            refresh_kwargs["allow_stale_policy"] = True
        status, response_body = self._credential_provider.request_with_refresh(
            method=method,
            path=path,
            query=dict(query or {}),
            body=data,
            headers=headers,
            deadline=deadline,
            **refresh_kwargs,
        )
        body = response_body.decode("utf-8") or "{}"
        return status, self._decode_body(body)

    def _call(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
        timeout: float = 5.0,
        deadline: float | None = None,
    ) -> tuple[int, dict[str, Any]]:
        # Replay only an accredited pre-request failure that sent zero HTTP bytes.
        # All ambiguous post-request failures fail closed without another attempt.
        timeout_value = float(timeout)
        call_deadline = self._time_fn() + timeout_value
        if deadline is not None:
            call_deadline = min(call_deadline, float(deadline))

        def remaining_timeout() -> float:
            remaining = call_deadline - self._time_fn()
            if remaining <= 0.0:
                # /await is waiting on an already-enqueued command: the
                # caller-imposed deadline is a timeout, not a missing daemon.
                if path == "/await":
                    raise _CallBudgetExpired()
                raise ToolError("daemon_unavailable")
            return remaining

        try:
            return self._request_once(
                method, path, payload, query, remaining_timeout()
            )
        except daemon_credential.CredentialRefreshError as error:
            raise ToolError(error.code) from None
        except AccreditedTransportError as error:
            if error.code == "daemon_identity_unverified":
                raise ToolError(error.code) from None
            retryable = (
                error.request_stage == "pre_request"
                and error.http_bytes_sent == 0
            )
            if not retryable:
                raise ToolError("daemon_response_ambiguous") from None
            if not self._ensure_daemon(call_deadline):
                if path == "/await":
                    raise _CallBudgetExpired() from None
                raise ToolError(self._daemon_missing_error()) from None
            try:
                return self._request_once(
                    method, path, payload, query, remaining_timeout()
                )
            except daemon_credential.CredentialRefreshError as retry_error:
                raise ToolError(retry_error.code) from None
            except AccreditedTransportError as retry_error:
                if retry_error.code == "daemon_identity_unverified":
                    raise ToolError(retry_error.code) from None
                retry_safe = (
                    retry_error.request_stage == "pre_request"
                    and retry_error.http_bytes_sent == 0
                )
                if not retry_safe:
                    raise ToolError("daemon_response_ambiguous") from None
                if path == "/await":
                    raise _CallBudgetExpired() from None
                raise ToolError("daemon_unavailable") from None
            except (ConnectionError, OSError):
                if path == "/await":
                    raise _CallBudgetExpired() from None
                raise ToolError("daemon_unavailable") from None
        except (ConnectionError, OSError):
            if path == "/await":
                raise _CallBudgetExpired() from None
            raise ToolError("daemon_unavailable") from None

    async def call_bridge(self, cmd: str, args: dict[str, Any], peer: str, timeout_s: float) -> dict[str, Any]:
        deadline = self._time_fn() + timeout_s
        if cmd in _BRIDGE_WORLD_READ_COMMANDS:
            # BUG-037: the liveness probe must not outlive the caller's deadline.
            probe_budget = min(
                LIVENESS_STATUS_TIMEOUT_S, max(0.0, deadline - self._time_fn())
            )
            snapshot = None
            if probe_budget > 0.0:
                try:
                    snapshot = await self.bridge_status_payload(
                        timeout_s=probe_budget
                    )
                except Exception:
                    snapshot = None
            if _has_ready_snapshot_shape(snapshot):
                early = _world_read_not_ready(self, cmd, snapshot)
                if early is not None:
                    return early
        lease_token, _ticket = self._session_state_snapshot()
        request_payload: dict[str, Any] = {
            "identity": self.identity.to_payload(),
            "cmd": cmd,
            "args": args,
            "peer": peer,
            "operation_timeout_s": timeout_s,
        }
        if lease_token is not None:
            request_payload["lease_token"] = lease_token
        previous = self._allow_stale_policy
        self._allow_stale_policy = not command_requires_lease(cmd)
        try:
            status, payload = await asyncio.to_thread(
                self._call,
                "POST",
                "/enqueue",
                request_payload,
                None,
                timeout_s,
                deadline,
            )
            await self._accept_command_renewal(payload)
            if status != 200:
                error = self._enqueue_error(payload)
                if _remote_error_code(payload) in _STALE_LEASE_ERRORS and lease_token is not None:
                    self._control._clear_matching_lease(lease_token)
                    await self._finish_carrier_io()
                if _remote_error_code(payload) in {"version_blocked", "lease_required"}:
                    try:
                        snapshot = await self.bridge_status_payload(
                            timeout_s=LIVENESS_STATUS_TIMEOUT_S
                        )
                    except Exception:
                        snapshot = None
                    error = _public_enqueue_error(
                        payload, status_snapshot=snapshot, peer=peer
                    )
                raise ToolError(error)
            if "id" not in payload:
                raise ToolError("daemon_bad_enqueue_response")
            command_id = int(payload["id"])
            result = await self._await_result(
                cmd, command_id, peer, timeout_s, deadline=deadline
            )
            return _with_bridge_success_hints(self, cmd, result)
        finally:
            self._allow_stale_policy = previous

    async def call_exec_enforce(self, args: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        return await self.call_bridge("exec_enforce", args, "server", timeout_s)

    def _enqueue_error(self, payload: dict[str, Any]) -> str:
        return _public_enqueue_error(payload)

    async def _await_result(
        self,
        cmd: str,
        command_id: int,
        peer: str,
        timeout_s: float,
        *,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        if deadline is None:
            deadline = self._time_fn() + timeout_s
        while True:
            remaining = deadline - self._time_fn()
            if remaining <= 0.0:
                break
            try:
                status, payload = await asyncio.to_thread(
                    self._call,
                    "GET",
                    "/await",
                    None,
                    {"id": str(command_id), "remove": "1"},
                    remaining,
                    deadline,
                )
            except _CallBudgetExpired:
                break
            if status == 200 and payload.get("status") == "done":
                result = payload.get("result") or {}
                # Bridge serializes ok as int 0/1; treat any falsy ok as an error.
                if not result.get("ok"):
                    raise _bridge_error(result, cmd)
                return result_prune.prune_unfilled_fields(cmd, result)
            remaining = deadline - self._time_fn()
            if remaining <= 0.0:
                break
            await asyncio.sleep(min(POLL_INTERVAL_S, remaining))
        raise ToolError(
            f"timeout waiting for {cmd} id={command_id}; "
            f"{await self._liveness_message(peer)}"
        )

    async def enqueue_bridge(
        self, cmd: str, args: dict[str, Any], peer: str, timeout_s: float
    ) -> int:
        self.touch()
        deadline = self._time_fn() + timeout_s
        lease_token, _ticket = self._session_state_snapshot()
        request_payload: dict[str, Any] = {
            "identity": self.identity.to_payload(),
            "cmd": cmd,
            "args": args,
            "peer": peer,
            "operation_timeout_s": timeout_s,
        }
        if lease_token is not None:
            request_payload["lease_token"] = lease_token
        previous = self._allow_stale_policy
        self._allow_stale_policy = not command_requires_lease(cmd)
        try:
            status, payload = await asyncio.to_thread(
                self._call,
                "POST",
                "/enqueue",
                request_payload,
                None,
                timeout_s,
                deadline,
            )
            await self._accept_command_renewal(payload)
            if status != 200:
                error = self._enqueue_error(payload)
                if _remote_error_code(payload) in _STALE_LEASE_ERRORS and lease_token is not None:
                    self._control._clear_matching_lease(lease_token)
                    await self._finish_carrier_io()
                if _remote_error_code(payload) in {"version_blocked", "lease_required"}:
                    try:
                        snapshot = await self.bridge_status_payload(
                            timeout_s=LIVENESS_STATUS_TIMEOUT_S
                        )
                    except Exception:
                        snapshot = None
                    error = _public_enqueue_error(
                        payload, status_snapshot=snapshot, peer=peer
                    )
                raise ToolError(error)
            if "id" not in payload:
                raise ToolError("daemon_bad_enqueue_response")
            return int(payload["id"])
        finally:
            self._allow_stale_policy = previous

    async def probe_bridge_result(
        self, cmd: str, command_id: int, peer: str
    ) -> dict[str, Any] | None:
        deadline = self._time_fn() + DEFAULT_TOOL_TIMEOUT_S
        try:
            status, payload = await asyncio.to_thread(
                self._call,
                "GET",
                "/await",
                None,
                {"id": str(command_id), "remove": "1"},
                DEFAULT_TOOL_TIMEOUT_S,
                deadline,
            )
        except _CallBudgetExpired:
            raise ToolError(
                f"timeout waiting for {cmd} id={command_id}; "
                f"{await self._liveness_message(peer)}"
            ) from None
        if status != 200:
            raise ToolError(str(payload.get("error") or payload))
        if payload.get("status") == "done":
            result = payload.get("result") or {}
            if not result.get("ok"):
                raise _bridge_error(result, cmd)
            return result_prune.prune_unfilled_fields(cmd, result)
        return None

    async def abandon_bridge(self, command_id: int, reason: str) -> None:
        # Client mode has no daemon /abandon route. An undelivered command
        # expires via COMMAND_TTL_S; a delivered one is reaped by its
        # operation deadline. This method is a documented no-op.
        return

    async def _liveness_message(self, peer: str) -> str:
        # The whole body is guarded, not just the fetch: this runs inside the
        # timeout handler, so anything raising here would REPLACE the timeout
        # ToolError with an unrelated one and hide what the caller asked about.
        try:
            status = await self.bridge_status_payload(
                timeout_s=LIVENESS_STATUS_TIMEOUT_S
            )
            peer_key = "client_peer" if peer == "client" else "server_peer"
            peer_status = status.get(peer_key) or {}
            age = peer_status.get("last_poll_age_s")
            age_text = (
                "has never polled" if age is None else f"last poll {float(age):.1f}s ago"
            )
            return (
                f"{peer} peer {age_text}; queue_depth={peer_status.get('queue_depth')}; "
                f"version_state={peer_status.get('version_state')}"
            )
        except Exception:
            return f"{peer} peer status unavailable"

    async def bridge_status_payload(
        self,
        *,
        timeout_s: float | None = None,
        registered_tools: frozenset[str] | None = None,
        intended_tools: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        args = () if timeout_s is None else (None, None, float(timeout_s))
        status, payload = await asyncio.to_thread(
            self._call, "GET", "/status", *args
        )
        if status != 200:
            raise ToolError(str(payload.get("error") or payload))
        # B1: compare capabilities (ach) before ready when tools are supplied.
        if registered_tools is not None:
            payload = _with_capability_comparison(
                payload, registered_tools, intended_tools
            )
        return _with_ready(payload)


def required_keyfile(config: ServerConfig) -> str:
    if config.keyfile is None:
        raise ValueError("--keyfile is required")
    return config.keyfile


def _bad_args(field: str, value: object, requirement: str) -> str:
    return f"bad_args: {field} {value!r} must {requirement}"


def _require_vec3(value: list[float] | None, name: str) -> list[float]:
    error = (
        "bad_pos"
        if name == "pos"
        else _bad_args(name, value, "be a list of 3 finite numbers")
    )
    if not isinstance(value, list) or len(value) != 3:
        raise ToolError(error)
    try:
        vec = [float(value[0]), float(value[1]), float(value[2])]
    except (TypeError, ValueError) as exc:
        raise ToolError(error) from exc
    if not all(math.isfinite(item) for item in vec):
        raise ToolError(error)
    return vec


def _require_float_list(
    value: list[float] | None, count: int, name: str = "value"
) -> list[float]:
    error = _bad_args(name, value, f"be a list of {count} finite numbers")
    if not isinstance(value, list) or len(value) != count:
        raise ToolError(error)
    try:
        items = [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise ToolError(error) from exc
    if not all(math.isfinite(item) for item in items):
        raise ToolError(error)
    return items


def _timeout(timeout_s: float) -> float:
    try:
        value = float(timeout_s)
    except (TypeError, ValueError) as exc:
        raise ToolError("bad_timeout") from exc
    if value <= 0.0 or not math.isfinite(value):
        raise ToolError("bad_timeout")
    if value > MAX_TIMEOUT_S:
        raise ToolError("bad_timeout")
    return value


# restore_gameplay closes its verdict blind in Enforce (ficha 4f83). The
# observer is dayz_mcp.camera_restore: view=player vs view=scripted vs ok=0.
# Re-exported below so existing tests keep importing from server.


# Mirrors VEHICLE_CONTROL_MAX_TTL_S in addon/scripts/5_Mission/MCPClientBridge.c:114.
# The bridge only honours hold_ttl_s <= this value; above it the control silently
# falls back to VEHICLE_CONTROL_DEFAULT_TTL_S (3.0 s). Keep in sync with the bridge.
VEHICLE_CONTROL_MAX_TTL_S = 30.0
# Mirrors MCPWeaponControl.RAISE_* and AIM_CHANGE_ABS_MAX
# (addon/scripts/4_World/MCP_Weapon.c:45-50). Out of range is rejected.
WEAPON_RAISE_DEFAULT_TTL_S = 3.0
WEAPON_RAISE_MAX_TTL_S = 30.0
WEAPON_AIM_ABS_MAX = 3.141593


def _finite_float(value: float, error: str = "bad_args") -> float:
    resolved_error = (
        _bad_args("value", value, "be a finite number")
        if error == "bad_args"
        else error
    )
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise ToolError(resolved_error) from exc
    if not math.isfinite(converted):
        raise ToolError(resolved_error)
    return converted


# A hold answers after its release, hold_s after the press: the tool waits at
# least hold_s plus this, the slack MCPClientBridge.c INPUT_TRIGGER_JOB_SLACK_S
# gives the job before it releases the key itself.
INPUT_TRIGGER_HOLD_SLACK_S = 5.0


def _input_trigger_seconds(field: str, value: object, maximum: float, when: str) -> float:
    requirement = f"be a finite number in (0, {maximum}] with {when}"
    if value is None or isinstance(value, bool):
        raise ToolError(_bad_args(field, value, requirement))
    seconds = _finite_float(value, _bad_args(field, value, requirement))
    if seconds <= 0.0 or seconds > maximum:
        raise ToolError(_bad_args(field, value, requirement))
    return seconds


def _input_trigger_request(
    kind: object,
    dik: object,
    name: object,
    entry: object,
    phase: object,
    hold_s: object,
    ttl_s: object,
    timeout_s: object,
) -> tuple[dict[str, Any], float]:
    """The input_trigger wire args and wait budget, or ToolError bad_args.

    Each kind and phase sends exactly the keys of its ingress variant
    (loopback._input_trigger_variant). No number travels without its phase,
    so an absent key never reaches Enforce as 0 (fb-20260930-065425-8779).
    """
    if kind not in ("key", "input"):
        raise ToolError(_bad_args("kind", kind, "be 'key' or 'input'"))
    if phase not in ("click", "hold", "press", "release"):
        raise ToolError(
            _bad_args("phase", phase, "be 'click', 'hold', 'press' or 'release'")
        )
    args: dict[str, Any] = {"trigger_kind": kind, "trigger_edge": phase}
    if kind == "key":
        if (
            isinstance(dik, bool)
            or not isinstance(dik, int)
            or dik < 0
            or dik > INPUT_TRIGGER_DIK_MAX
        ):
            raise ToolError(
                _bad_args(
                    "dik", dik, f"be an int from 0 to {INPUT_TRIGGER_DIK_MAX} with kind='key'"
                )
            )
        if name is not None:
            raise ToolError(_bad_args("name", name, "be omitted with kind='key'"))
        chosen_entry = "game" if entry is None else entry
        if chosen_entry not in ("game", "mission"):
            raise ToolError(_bad_args("entry", entry, "be 'game' or 'mission'"))
        args["trigger_entry"] = chosen_entry
        args["dik"] = dik
    else:
        if not is_printable_input_name(name):
            raise ToolError(
                _bad_args(
                    "name",
                    name,
                    f"be 1..{INPUT_NAME_MAX_CHARS} printable ASCII characters "
                    "(codes 32..126) with kind='input'",
                )
            )
        if dik is not None:
            raise ToolError(_bad_args("dik", dik, "be omitted with kind='input'"))
        if entry is not None:
            raise ToolError(_bad_args("entry", entry, "be omitted with kind='input'"))
        args["name"] = name
    if phase == "hold":
        args["hold_s"] = _input_trigger_seconds(
            "hold_s", hold_s, INPUT_TRIGGER_HOLD_MAX_S, "phase='hold'"
        )
    elif hold_s is not None:
        raise ToolError(_bad_args("hold_s", hold_s, "be omitted unless phase is 'hold'"))
    if phase == "press":
        args["hold_ttl_s"] = _input_trigger_seconds(
            "ttl_s", ttl_s, INPUT_TRIGGER_PRESS_MAX_TTL_S, "phase='press'"
        )
    elif ttl_s is not None:
        raise ToolError(_bad_args("ttl_s", ttl_s, "be omitted unless phase is 'press'"))
    timeout = _timeout(timeout_s)
    if phase == "hold":
        timeout = max(timeout, args["hold_s"] + INPUT_TRIGGER_HOLD_SLACK_S)
    return args, timeout


def _is_int_clock_part(value: object) -> bool:
    """True for int or finite integral float (60.0, 24.0). Never bool."""
    if isinstance(value, bool):
        return False
    if type(value) is int:
        return True
    if type(value) is float:
        return math.isfinite(value) and value == int(value)
    return False


def _add_applied_days(out: dict[str, Any], extra_days: int) -> None:
    if extra_days == 0:
        return
    year, month, day = out.get("year"), out.get("month"), out.get("day")
    if all(_is_int_clock_part(part) for part in (year, month, day)):
        try:
            shifted = datetime(int(year), int(month), int(day)) + timedelta(
                days=extra_days
            )
        except ValueError:
            if _is_int_clock_part(day):
                out["day"] = int(day) + extra_days
            return
        out["year"] = shifted.year
        out["month"] = shifted.month
        out["day"] = shifted.day
        return
    # Incomplete calendar (day without year/month) must not invent a date.


def _overflow_clock_parts(hour: int, minute: int) -> tuple[int, int, int]:
    extra_hours, minute = divmod(minute, 60)
    extra_days, hour = divmod(hour + extra_hours, 24)
    return extra_days, hour, minute


def _clock_was_normalized(raw: dict[str, Any], normalized: dict[str, Any]) -> bool:
    return any(
        raw.get(field) != normalized.get(field)
        for field in ("year", "month", "day", "hour", "minute")
    )


def _normalize_applied_clock(applied: dict[str, Any]) -> dict[str, Any]:
    """Carry minute>=60 into hour, then into the calendar day. Hour stays 0–23.

    GetDate can echo hour=8, minute=60 for a requested 9:00, or hour=23,
    minute=60 for midnight (fb-20260911-230929-311d). Integral floats
    60.0 and 24.0 take the same carry path as int 60/24. Overflow always
    carries into the next calendar day so applied.hour is never 24. The
    request is not consulted; a same-day 23:60 echo becomes 00:00 the
    next day even if the client asked for 00:00 on the echoed day.
    """
    out = dict(applied)
    hour = out.get("hour")
    minute = out.get("minute")
    if not _is_int_clock_part(hour) or not _is_int_clock_part(minute):
        return out
    hour = int(hour)
    minute = int(minute)
    if minute < 60 and 0 <= hour <= 23:
        return out
    extra_days, hour, minute = _overflow_clock_parts(hour, minute)
    out["hour"] = hour
    out["minute"] = minute
    _add_applied_days(out, extra_days)
    return out


def _optional_finite_float(
    value: float | None, error: str = "bad_args"
) -> float | None:
    if value is None:
        return None
    return _finite_float(value, error)


def _require_range(
    value: float,
    minimum: float,
    maximum: float,
    error: str = "bad_args",
) -> float:
    resolved_error = (
        _bad_args(
            "value", value, f"be a finite number from {minimum} to {maximum}"
        )
        if error == "bad_args"
        else error
    )
    converted = _finite_float(value, resolved_error)
    if converted < minimum or converted > maximum:
        raise ToolError(resolved_error)
    return converted


def _patch_public_argument_alias(app: FastMCP, tool_name: str, internal: str, public: str) -> None:
    tool = app._tool_manager.get_tool(tool_name)  # type: ignore[attr-defined]
    if tool is None:
        raise RuntimeError(f"missing tool {tool_name}")
    properties = tool.parameters.get("properties", {})
    if internal in properties:
        properties[public] = properties.pop(internal)
    required = tool.parameters.get("required")
    if isinstance(required, list):
        tool.parameters["required"] = [public if item == internal else item for item in required]
    original = tool.fn_metadata.call_fn_with_arg_validation

    async def patched(fn, fn_is_async, arguments_to_validate, arguments_to_pass_directly):
        arguments = dict(arguments_to_validate)
        if public in arguments and internal not in arguments:
            arguments[internal] = arguments.pop(public)
        return await original(fn, fn_is_async, arguments, arguments_to_pass_directly)

    object.__setattr__(tool.fn_metadata, "call_fn_with_arg_validation", patched)


_CLOSED_UNEXPECTED_ARGUMENT_CAP = 5


def _echo_unexpected_argument_key(key: str) -> str:
    """Render a caller-supplied argument name for a closed-schema error.

    Identifier-shaped keys are echoed. Anything else (path, space, punctuation)
    becomes ``<unsafe>`` so host text never crosses the MCP wire. Short names
    such as ``id`` fail ``_is_safe_error_token``'s 3-char floor but are still
    identifier-shaped and safe to name.
    """
    if not isinstance(key, str):
        return "<unsafe>"
    if _is_safe_error_token(key):
        return key
    if (
        1 <= len(key) <= 2
        and key[0].isascii()
        and key[0].isalpha()
        and all(char.isascii() and (char.isalnum() or char == "_") for char in key)
    ):
        return key
    return "<unsafe>"


def _closed_unexpected_arguments_message(
    unknown: set[str],
    allowed: set[str],
    required: list[str] | tuple[str, ...],
    provided: object,
) -> str:
    """``bad_args: unexpected arguments: ... (accepted: ...)`` plus ``; missing:``."""
    ranked = sorted(unknown)
    echoed = [
        _echo_unexpected_argument_key(name)
        for name in ranked[:_CLOSED_UNEXPECTED_ARGUMENT_CAP]
    ]
    unexpected = ", ".join(echoed)
    overflow = len(ranked) - _CLOSED_UNEXPECTED_ARGUMENT_CAP
    if overflow > 0:
        unexpected = f"{unexpected} +{overflow} more"
    accepted = ", ".join(sorted(allowed))
    message = f"bad_args: unexpected arguments: {unexpected} (accepted: {accepted})"
    missing = sorted(name for name in required if name not in provided)
    if missing:
        message = f"{message}; missing: {', '.join(missing)}"
    return message


def _patch_closed_tool_schema(app: FastMCP, tool_name: str) -> None:
    tool = app._tool_manager.get_tool(tool_name)  # type: ignore[attr-defined]
    if tool is None:
        raise RuntimeError(f"missing tool {tool_name}")
    tool.parameters["additionalProperties"] = False
    # A zero-argument tool publishes `required: []` explicitly: a consumer that generates
    # calls from the schema must see a closed, empty contract, not an absent key.
    tool.parameters.setdefault("required", [])
    allowed = set(tool.parameters.get("properties", {}))
    required = tuple(tool.parameters.get("required") or [])
    original = tool.fn_metadata.call_fn_with_arg_validation

    async def patched(fn, fn_is_async, arguments_to_validate, arguments_to_pass_directly):
        unknown = set(arguments_to_validate) - allowed
        if unknown:
            raise ToolError(
                _closed_unexpected_arguments_message(
                    unknown, allowed, required, arguments_to_validate
                )
            )
        return await original(fn, fn_is_async, arguments_to_validate, arguments_to_pass_directly)

    object.__setattr__(tool.fn_metadata, "call_fn_with_arg_validation", patched)


def _patch_mode_enum_from_authority(app: FastMCP, tool_name: str, field: str = "mode") -> None:
    """Publish and enforce a mode enum read from the M12 authority at build time and per call.

    ``dayz_test_modes.public_mode_names()`` is read when the app is BUILT (never when this module
    is imported) for the published schema, and again on EVERY call before validation, so a
    substituted record set is honoured both by a new ``build_app()`` and by the next call
    (Codex B-01, 2026-09-04). Schema and validation close together: the annotation stays ``str``
    and this wrapper is the gate.
    """
    tool = app._tool_manager.get_tool(tool_name)  # type: ignore[attr-defined]
    if tool is None:
        raise RuntimeError(f"missing tool {tool_name}")
    prop = tool.parameters.get("properties", {}).get(field)
    if not isinstance(prop, dict):
        raise RuntimeError(f"missing property {field} on {tool_name}")
    prop["enum"] = list(dayz_test_modes.public_mode_names())
    original = tool.fn_metadata.call_fn_with_arg_validation

    async def patched(fn, fn_is_async, arguments_to_validate, arguments_to_pass_directly):
        allowed = dayz_test_modes.public_mode_names()
        if arguments_to_validate.get(field) not in allowed:
            raise ToolError(f"bad_args: {field} must be one of " + "|".join(allowed))
        return await original(fn, fn_is_async, arguments_to_validate, arguments_to_pass_directly)

    object.__setattr__(tool.fn_metadata, "call_fn_with_arg_validation", patched)


RUN_ID_MATRIX_MODE_DESCRIPTION = (
    "Launch mode. client reattaches only the client to a live run and REQUIRES "
    "run_id (server and world state preserved); server and all launch fresh and "
    "must NOT pass run_id."
)
RUN_ID_MATRIX_RUN_ID_DESCRIPTION = (
    "Live run to reattach to. Required with mode=client; forbidden with "
    "mode=server|all (bad_dayz_test_request otherwise)."
)
NAVMESH_DATA_SERVER_DESCRIPTION = (
    "Opt-in. Default false. With mode=server, adds -startNavmeshDataServer "
    "for DayZ Tools NavMeshGenerator. Rejected with other modes or pack_only. "
    "Preflight validates the request without launching. A successful launch "
    "does not prove the generator connected or produced a usable navmesh."
)
AUTO_REMEDIATE_STEAM_DESCRIPTION = (
    "Opt-in. Default false. When the Steam gate refuses (steam_not_running or "
    "steam_session_stale), dayz-mcp prepares Steam once before it launches the "
    "client: it rewrites Steam's registered pid when exactly one steam.exe runs "
    "with a logged-in user, otherwise restarts Steam (only starts it when no "
    "steam.exe runs), then waits for Steam's startup. A running Steam with no "
    "logged-in user is left alone and the launch is refused. Off by default "
    "because a restart ends a Steam session the caller may be using. On success "
    "the result reports steam_pid_repair, steam_restarted and steam_startup."
)
TAKEOVER_DESCRIPTION = (
    "Opt-in. Default false. When another session's RUNNING run occupies "
    "the box, refuse with takeover_required unless this is true. An "
    "ownerless RUNNING_IDLE that this session did not launch is "
    "run_protected until use_state is abandoned; takeover=true does not "
    "evict it. True stops a run this session may take and then launches; "
    "the result names evicted_run_id. Does not kill PIDs that are not on "
    "the run manifest."
)
EXTRA_MODS_DESCRIPTION = (
    "Additional mods for this run. Each entry must be a single folder name "
    "(for example '@DayZ_MCP') or an absolute path inside the project's "
    "mod_roots; relative paths with '\\' or '/' are rejected as bad_mod. "
    "When the selected project is not DayZ_MCP, '@DayZ_MCP' is appended to "
    "extra_mods by default unless that folder name is already present in "
    "extra_mods, and the result reports extra_mods_defaulted. The same "
    "folder in the base_mods list this run will load (the caller's list, or "
    "the policy defaults when the caller omits it) is not copied into "
    "extra_mods. base_mods and server_mods do not satisfy that gate, so "
    "that call stays bridge_mod_missing. bridge_mod_missing also remains "
    "when the sealed request would reject the appended entry."
)
CLIENT_START_BUDGET_MAX_S = 3600.0
CLIENT_START_BUDGET_DESCRIPTION = (
    "Seconds a freshly launched client may take to reach its first poll before "
    "it counts as hung. Under it, relaunching is refused with "
    "client_still_starting, or with client_start_stalled when the client's RPT "
    f"has sat at its bare header without a poll for {PEER_STALE_S:g} s (0 then "
    "supersedes it). Must be a number in [0, 3600]; anything else is "
    "rejected, not ignored. It governs THIS call only, so a client reattach "
    "(mode=client) must pass it again -- it does not configure the launched "
    "client. Omit to fall back to "
    f"{dayz_test_tool._CLIENT_START_BUDGET_ENV} and then to the "
    f"default of {dayz_test_tool._CLIENT_START_BUDGET_S:g} seconds, which "
    "covers the slowest startup MEASURED - itself a lower bound, since the "
    "process starts before its log header and polls after the mission exists."
)


def _describe_run_parameters(app: FastMCP, tool_name: str) -> None:
    """Publish on the properties what the tool prose alone would not carry.

    ``dayz_test_request.py`` enforces client-requires-run_id and server|all-forbid-run_id
    with one bare ``bad_dayz_test_request``; the published schema said only "Mode" and
    "Run Id" (fb-20260829-104625-7c88). The property descriptions are the place a client
    reads before calling.

    The same argument is why the startup budget and the Steam opt-in are here:
    both were reachable only from the environment of a process the caller does
    not launch, so the capability existed with no path from the surface the
    caller has. A missing property raises rather than passing quietly - a
    description silently attached to nothing is the failure this guards.
    """
    tool = app._tool_manager.get_tool(tool_name)  # type: ignore[attr-defined]
    if tool is None:
        raise RuntimeError(f"missing tool {tool_name}")
    props = tool.parameters.get("properties", {})
    for field, text in (
        ("mode", RUN_ID_MATRIX_MODE_DESCRIPTION),
        ("run_id", RUN_ID_MATRIX_RUN_ID_DESCRIPTION),
        ("extra_mods", EXTRA_MODS_DESCRIPTION),
        ("auto_remediate_steam", AUTO_REMEDIATE_STEAM_DESCRIPTION),
        ("navmesh_data_server", NAVMESH_DATA_SERVER_DESCRIPTION),
        ("client_start_budget_s", CLIENT_START_BUDGET_DESCRIPTION),
        ("takeover", TAKEOVER_DESCRIPTION),
    ):
        prop = props.get(field)
        if not isinstance(prop, dict):
            raise RuntimeError(f"missing property {field} on {tool_name}")
        prop["description"] = text


def _player_count(result: dict[str, Any]) -> int:
    players = result.get("players")
    if not isinstance(players, list):
        raise ToolError("wait_for: players list missing")
    return len(players)


def _sibling_profile_dirs(profiles: list[str]) -> list[str]:
    """Add the _client/_server sibling when the run only recorded one side."""
    extra: list[str] = []
    for item in profiles:
        path = Path(item)
        parent = path.parent.name.casefold()
        if parent == "_server":
            sibling = str(path.parent.parent / "_client" / "profiles")
        elif parent == "_client":
            sibling = str(path.parent.parent / "_server" / "profiles")
        else:
            continue
        if log_tail.is_allowed_profiles_dir(sibling) and Path(sibling).is_dir():
            extra.append(sibling)
    return sorted(set(profiles + extra))


def _run_start_epoch(runs: list[dict[str, Any]]) -> float | None:
    """Start of the launch in progress: the newest run's earliest process.

    `min` inside a run is when that run started -- process_lifecycle._run_age_s
    aggregates the same way. `max` across runs keeps the floor on the current
    launch, so a second live run cannot pull it back and readmit the first
    one's logs as if they belonged to this one.

    Only a live run reaches here with a stamp at all: RunRecord.validate makes
    EXITED carry an empty `processes` and RUNNING/RUNNING_IDLE a non-empty one.
    With a single live run -- the only shape observed on this host across the
    store and its six pre-prune backups -- both aggregations return the same
    float, so this is a guard rather than a repair.
    """

    starts: list[float] = []
    for run in runs:
        times: list[float] = []
        for proc in run.get("processes") or []:
            if not isinstance(proc, dict):
                continue
            raw = proc.get("creation_time_utc")
            if not isinstance(raw, str) or not raw:
                continue
            try:
                times.append(
                    datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
                )
            except ValueError:
                continue
        if times:
            starts.append(min(times))
    return max(starts) if starts else None


def _newest_rpt_and_script(dated: list[tuple[float, str]]) -> list[str]:
    """Newest .rpt and newest .log by suffix, independent of mtime gap."""
    newest_rpt: str | None = None
    newest_script: str | None = None
    rpt_mtime = script_mtime = None
    for mtime, path in dated:
        suffix = Path(path).suffix.casefold()
        if suffix == ".rpt":
            if rpt_mtime is None or mtime >= rpt_mtime:
                newest_rpt, rpt_mtime = path, mtime
        elif suffix == ".log":
            if script_mtime is None or mtime >= script_mtime:
                newest_script, script_mtime = path, mtime
    return [path for path in (newest_rpt, newest_script) if path]


# Teleport clearance probe (fb-20260824-115220-1bc1): a surface landing must have
# an uncovered vertical column. Constants mirror the certified site gate probe
# (tools/g0_site_gate.py canopy_gate, calibrated 2026-08-19 at 0.05 m).
CLEARANCE_PROBE_UP_M = 30.0
CLEARANCE_PROBE_DOWN_M = 5.0
CLEARANCE_TOLERANCE_M = 0.05
CLEARANCE_LANDING_BAND_M = 0.5

# entities_query is trustworthy only with a player streaming the area: far from
# every player the engine answers 0 rows or exactly the cap with no error signal
# (fb-20260824-123204-638e, measured 2026-08-24). 300 m is conservative against
# the certified corridor probes (player within ~215 m of every sphere).
ENTITIES_QUERY_BUBBLE_M = 300.0


# entities_query rows carry has_cargo: MCPEntityHit declares the field
# (addon/scripts/5_Mission/MCPMessages.c:360) and DispatchEntitiesQuery fills it with
# HasCargoCapacity (MCPBridge.c:1422, defined :1441-1456 as EntityAI.Cast ->
# GetInventory() -> GetCargo() != null). Two wire facts keep a normalisation on this
# side:
#   - the bridge serialises an Enforce bool as int 0/1, the same way `ok` arrives
#     (wait_for_result above), so a consumer testing `row["has_cargo"] is True` would
#     read every container as false;
#   - a bridge that predates the field omits the key entirely, and absent means "the
#     bridge did not say" (null), never "no cargo" (false).
# A value in any other form is published as null rather than guessed: a truthiness test
# on an unknown shape would fabricate a verdict the bridge never gave.
def _cargo_flag(value: object) -> bool | None:
    """Read one has_cargo cell off the wire as a bool, or None when unstated."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return None


def _normalize_entities_cargo(result: dict[str, Any]) -> dict[str, Any]:
    """Publish has_cargo on every entities_query row as bool | None.

    Additive and total: no row is dropped, reordered or otherwise rewritten, and no
    shape raises. A result without rows, or rows that are not dicts, comes back as it
    arrived -- a missing field must never cost the caller the answer it did get.
    """
    if not isinstance(result, dict):
        return result
    rows = result.get("entities")
    if not isinstance(rows, list):
        return result
    for row in rows:
        if isinstance(row, dict):
            row["has_cargo"] = _cargo_flag(row.get("has_cargo"))
    return result


def _annotate_entities_reliability(
    result: dict[str, Any], players_result: object, pos: list[float]
) -> dict[str, Any]:
    """Stamp nearest_player_m and reliability on an entities_query result."""
    if not isinstance(result, dict) or not result.get("ok"):
        return result
    nearest: float | None = None
    players = []
    # The probe's raw list, kept apart from the iteration default: an ok reply
    # WITHOUT a players list is not evidence that nobody is connected.
    players_raw: object = None
    if isinstance(players_result, dict) and players_result.get("ok"):
        players_raw = players_result.get("players")
        players = players_raw if isinstance(players_raw, list) else []
    for player in players:
        ppos = player.get("pos") if isinstance(player, dict) else None
        if not (isinstance(ppos, list) and len(ppos) == 3):
            continue
        try:
            deltas = [float(a) - float(b) for a, b in zip(ppos, pos)]
        except (TypeError, ValueError):
            continue
        distance = (deltas[0] ** 2 + deltas[1] ** 2 + deltas[2] ** 2) ** 0.5
        if nearest is None or distance < nearest:
            nearest = distance
    if nearest is not None:
        result["nearest_player_m"] = round(nearest, 1)
    else:
        result["nearest_player_m"] = None
    if nearest is not None and nearest <= ENTITIES_QUERY_BUBBLE_M:
        result["reliability"] = "player_in_bubble"
    else:
        result["reliability"] = "remote_unverified"
    # Positive evidence only: the probe answered ok AND carried an empty list.
    # A missing or non-list field is reported as remote_unverified without a reason.
    if isinstance(players_raw, list) and not players_raw:
        result["reason"] = "no_player_connected"
    return result


def _object_target_args(
    type: str, pos: list[float] | None, object_id: int
) -> dict[str, Any]:
    """Validate the object_id-or-classname target contract shared by object verbs."""
    if not isinstance(object_id, int) or isinstance(object_id, bool) or object_id < 0:
        raise ToolError(_bad_args("object_id", object_id, "be a non-negative int"))
    if object_id > 0:
        return {"object_id": object_id}
    if not isinstance(type, str) or type == "":
        raise ToolError(
            _bad_args("type", type, "be a non-empty string when object_id is omitted")
        )
    if not _inspect_type_is_valid(type):
        raise ToolError(
            _bad_args("type", type, "be a DayZ classname without whitespace")
        )
    return {"type": type, "pos": _require_vec3(pos, "pos")}


def _inspect_type_is_valid(type: object) -> bool:
    """True for a classname token object_inspect can echo on rejection."""
    if not isinstance(type, str) or not type:
        return False
    if type.strip() != type or any(ch.isspace() for ch in type):
        return False
    if "/" in type or "\\" in type or type[0] in "{[":
        return False
    return True


def _current_launch_logs(profiles_dir: str, start_epoch: float | None) -> list[str]:
    """Return RPT/script files from the current launch, never historic dumps.

    Without a launch timestamp, keep the newest .rpt and the newest .log by
    name suffix (not a time cluster), so a quiet current file is not dropped.
    """
    paths = log_tail.resolve_log_files(profiles_dir)
    dated: list[tuple[float, str]] = []
    for path in paths:
        # crash_*.log dumps are excluded even when freshly touched: a crash dump
        # carries CE world-create noise by the hundred thousand lines and starves
        # the wait_for scan budget (fb-20260823-130809-413a).
        if Path(path).name.casefold().startswith("crash"):
            continue
        try:
            dated.append((Path(path).stat().st_mtime, path))
        except OSError:
            continue
    if not dated:
        return []
    if start_epoch is None:
        return _newest_rpt_and_script(dated)
    floor = start_epoch - 2.0
    return [path for mtime, path in dated if mtime >= floor]


def _coerce_logs_since_marker(marker: object) -> str:
    """Normalize a logs_since marker to the encoded JSON string.

    The tool returns an encoded JSON string. FastMCP pre-parses JSON-looking
    strings into dicts before the handler runs because the parameter type is a
    union, not bare ``str`` (``FuncMetadata.pre_parse_json``). Clients that
    JSON-decode the returned marker also pass a dict. Accept both; reject
    anything else as ``bad_marker``.
    """
    if isinstance(marker, str):
        return marker
    if isinstance(marker, dict):
        try:
            return json.dumps(marker, separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError) as error:
            raise ToolError("bad_marker") from error
    raise ToolError("bad_marker")


# Run states whose client window may still be on screen, so a capture should be
# aimed at it. Anything else (EXITED, FAILED) has no window to disambiguate.
_CAPTURE_LIVE_RUN_STATES = frozenset({"STARTING", "RUNNING", "RUNNING_IDLE", "STOPPING"})


def _profile_dirs_from_runs(runs: list[dict[str, Any]]) -> list[str]:
    candidates = sorted(
        {str(item.get("profiles")) for item in runs if item.get("profiles")}
    )
    allowed = [item for item in candidates if log_tail.is_allowed_profiles_dir(item)]
    return _sibling_profile_dirs(allowed)


async def _wait_for_script_log_paths(runtime: Any) -> list[str]:
    status_fn = getattr(runtime, "lifecycle_status", None)
    if status_fn is None:
        raise ToolError("no_active_run")
    status = status_fn()
    if asyncio.iscoroutine(status):
        status = await status
    if not isinstance(status, dict):
        raise ToolError("no_active_run")
    runs = [item for item in (status.get("runs") or []) if isinstance(item, dict)]
    profiles = _profile_dirs_from_runs(runs)
    if not profiles:
        raise ToolError("no_active_run")
    start_epoch = _run_start_epoch(runs)
    if start_epoch is None:
        # No live run: the newest-file fallback would scan a dead launch
        # and wait_for could report satisfied:true on a line hours old.
        raise ToolError("no_active_run")
    paths: list[str] = []
    for profiles_dir in profiles:
        paths.extend(_current_launch_logs(profiles_dir, start_epoch))
    return paths


def _offset_before_last_lines(data: bytes, lookback_lines: int) -> int:
    """Byte offset of the start of the last ``lookback_lines`` lines.

    The result is always ``0`` or the byte just after a ``\\n``: a reader
    resuming there sees whole lines, never a half-line.
    """
    if lookback_lines <= 0 or not data:
        return len(data)
    parts = data.split(b"\n")
    line_count = len(parts) - 1 if parts and parts[-1] == b"" else len(parts)
    skip = max(0, line_count - lookback_lines)
    if skip == 0:
        return 0
    offset = 0
    seen = 0
    for part in parts:
        if seen >= skip:
            break
        offset += len(part) + 1
        seen += 1
    # `offset` is measured from byte 0 of `data` (each skipped line contributes
    # its length plus its terminating newline), so it is already an absolute
    # file offset; no window base is added.
    return min(offset, len(data))


def _offset_before_last_lines_in_window(
    window: bytes, window_start: int, lookback_lines: int
) -> int:
    """Absolute offset of the start of the last ``lookback_lines`` lines.

    ``window`` is the tail of the file starting at ``window_start``, not the whole
    file, and that is what makes this fiddly in two places:

    * unless the window starts at byte 0 its first line is a fragment cut by the
      window boundary. It is not a line, so it is neither counted nor returned --
      but its bytes still have to be added to the offset, or the result lands
      mid-line and the reader gets half a line as though it were whole;
    * when the window holds fewer complete lines than were asked for, the honest
      answer is the first complete line IN THE WINDOW. Returning 0 would point at
      the start of a file that may be hundreds of MB, which is the read this
      function exists to avoid.
    """
    if lookback_lines <= 0 or not window:
        return window_start + len(window)
    parts = window.split(b"\n")
    base = window_start
    if window_start > 0:
        base += len(parts[0]) + 1      # skip the boundary fragment, bytes included
        parts = parts[1:]
    if not parts:
        return base
    line_count = len(parts) - 1 if parts[-1] == b"" else len(parts)
    skip = max(0, line_count - lookback_lines)
    offset = base
    for part in parts[:skip]:
        offset += len(part) + 1
    return min(offset, window_start + len(window))


def _marker_rewound(path: str, lookback_lines: int) -> log_tail.TailMarker:
    """Marker rewound by ``lookback_lines``, reading only the file's tail.

    D40: this used to read the file whole. DayZ RPTs reach hundreds of MB in a
    long session, and every ``wait_for(log_matches, lookback_lines>0)`` paid for
    it. ``log_tail`` already caps its own reads at ``MAX_TAIL_BYTES``; this now
    respects the same ceiling. Size comes from ``os.fstat`` on the open handle,
    not from ``stat(path)``: the game is appending to this file while we read it,
    so the size has to describe the bytes we actually took.
    """
    file_path = Path(path)
    with file_path.open("rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        identity = log_tail._file_identity(
            handle, min(log_tail.IDENTITY_PREFIX_BYTES, size)
        )
        read_size = min(size, log_tail.MAX_TAIL_BYTES)
        window_start = size - read_size
        handle.seek(window_start)
        window = handle.read(read_size)
    offset = _offset_before_last_lines_in_window(window, window_start, lookback_lines)
    return log_tail.TailMarker(
        path=str(file_path), offset=offset, size=size, identity=identity
    )


def _log_markers_at_end(paths: list[str]) -> dict[str, log_tail.TailMarker]:
    markers: dict[str, log_tail.TailMarker] = {}
    for path in paths:
        try:
            result = log_tail.read_since(path, None)
        except log_tail.LogTailError:
            continue
        markers[path] = result["marker"]
    return markers


def _log_markers_with_lookback(
    paths: list[str], lookback_lines: int
) -> dict[str, log_tail.TailMarker]:
    if lookback_lines <= 0:
        return _log_markers_at_end(paths)
    markers: dict[str, log_tail.TailMarker] = {}
    for path in paths:
        try:
            markers[path] = _marker_rewound(path, lookback_lines)
        except (OSError, log_tail.LogTailError):
            continue
    return markers


def _new_log_lines(
    paths: list[str], markers: dict[str, log_tail.TailMarker]
) -> tuple[list[str], dict[str, log_tail.TailMarker], dict[str, int]]:
    """New lines since ``markers``, plus how many each file contributed.

    A path missing from the returned counts could not be read at all. That is
    the difference between "the file had nothing new" and "the file was never
    opened", and wait_for used to collapse both into silence.
    """
    lines: list[str] = []
    updated = dict(markers)
    counts: dict[str, int] = {}
    for path in paths:
        try:
            result = log_tail.read_since(path, markers.get(path))
        except log_tail.LogTailError:
            continue
        updated[path] = result["marker"]
        lines.extend(result["lines"])
        counts[path] = len(result["lines"])
    return lines, updated, counts


def _scan_log_for_pattern(
    path: str, pattern: str, max_bytes: int
) -> tuple[str | None, int, bool]:
    """First line of ``path`` containing ``pattern``, scanning from byte 0.

    Streams in chunks and returns on the first hit, so a whole launch is
    reachable without holding the file in memory. ``read_since`` cannot serve
    this: it keeps the NEWEST ``max_bytes``, which is right for tailing and
    wrong for a line printed at mission start.

    Splits on LF and drops a trailing CR, matching what ``read_since`` hands the
    matcher, so one pattern behaves the same on both paths.
    """
    scanned = 0
    consumed = 0
    carry = b""
    truncated = False
    try:
        with open(path, "rb") as handle:
            while True:
                budget = max_bytes - consumed
                if budget <= 0:
                    # Measured, not assumed: only truncated if bytes remain.
                    truncated = bool(handle.read(1))
                    break
                chunk = handle.read(min(_LAUNCH_SCAN_CHUNK_BYTES, budget))
                if not chunk:
                    break
                consumed += len(chunk)
                carry += chunk
                start = 0
                while True:
                    end = carry.find(b"\n", start)
                    if end == -1:
                        break
                    line = carry[start:end].decode("utf-8", errors="replace")
                    line = line.rstrip("\r")
                    start = end + 1
                    scanned += 1
                    if pattern in line:
                        return line, scanned, False
                carry = carry[start:]
    except OSError as error:
        raise log_tail.LogTailError("log_unavailable") from error
    if carry:
        line = carry.decode("utf-8", errors="replace").rstrip("\r")
        scanned += 1
        if pattern in line:
            return line, scanned, truncated
    return None, scanned, truncated


def _log_label(path: str) -> str:
    """Side-qualified file name for the wire; no host path leaves the daemon."""
    item = Path(path)
    side = item.parent.parent.name
    return f"{side}/{item.name}" if side.startswith("_") else item.name


def _record_scan(
    paths: list[str],
    counts: dict[str, int],
    seen: list[str],
    totals: dict[str, int],
    unreadable: set[str],
) -> None:
    """Fold one probe's per-file counts into the cumulative scan report."""
    for path in paths:
        if path not in totals:
            seen.append(path)
            totals[path] = 0
        if path in counts:
            totals[path] += counts[path]
            unreadable.discard(path)
        else:
            unreadable.add(path)


def _scanned_report(
    paths: list[str],
    totals: dict[str, int],
    unreadable: set[str],
    lookback_from: str,
    scan_truncated: bool,
) -> dict[str, Any]:
    """What wait_for actually read, so a no-match is visible as a no-match.

    Reported by two sessions on 2026-08-21: ``observed`` carries only the last
    line of the newest file, so when the RPT sorts newest it looks like the
    script log was never opened. It was; nothing in it matched. Names are
    side-qualified file names, never host paths -- this crosses the MCP wire.
    """
    files = [
        {
            "name": _log_label(path),
            "lines": totals.get(path, 0),
            "readable": path not in unreadable,
        }
        for path in paths
    ]
    report: dict[str, Any] = {
        "pattern_kind": "substring",
        "lookback_from": lookback_from,
        "files": files,
        "lines_total": sum(int(item["lines"]) for item in files),
    }
    if lookback_from == "launch":
        report["scan_truncated"] = scan_truncated
    return report


def _wait_for_response(
    *,
    condition: str,
    started: float,
    probes: int,
    observed: Any,
    satisfied: bool,
    scanned: dict[str, Any] | None = None,
    not_ready_probes: int = 0,
    last_error: str | None = None,
) -> dict[str, Any]:
    response = {
        # Timeout is a normal result, not a tool error. Gate on satisfied.
        "ok": True,
        "satisfied": satisfied,
        "condition": condition,
        "elapsed_s": time.monotonic() - started,
        "probes": probes,
        "observed": observed,
        "timed_out": not satisfied,
        "tool": "wait_for",
        "not_ready_probes": not_ready_probes,
    }
    if last_error is not None:
        response["last_error"] = last_error
    if scanned is not None:
        response["scanned"] = scanned
    return response


def _entity_wait_request(entity: Any) -> dict[str, Any]:
    """Validate the closed predicate before any bridge call or lock."""
    if not isinstance(entity, dict) or set(entity) != {"type", "pos", "radius", "field", "equals"}:
        raise ToolError("bad_args: entity requires exactly type,pos,radius,field,equals")
    if not isinstance(entity["type"], str) or not entity["type"]:
        raise ToolError("bad_args: entity.type must be non-empty")
    position_value = entity["pos"]
    if not isinstance(position_value, list) or len(position_value) != 3 or any(type(v) not in (int, float) for v in position_value):
        raise ToolError("bad_args: entity.pos must be three finite numbers")
    if type(entity["radius"]) not in (int, float):
        raise ToolError("bad_args: entity.radius must be a number in (0,50]")
    if any(not (-1e9 <= v <= 1e9) for v in position_value):
        raise ToolError("bad_args: entity.pos must be finite world coordinates")
    if not (0.0 < entity["radius"] <= 50.0):
        raise ToolError("bad_args: entity.radius must be in (0,50]")
    position = _require_vec3(position_value, "entity.pos")
    radius = _finite_float(entity["radius"], "bad_args: entity.radius must be in (0,50]")
    if radius <= 0.0 or radius > 50.0:
        raise ToolError("bad_args: entity.radius must be in (0,50]")
    field = entity["field"]
    expected = entity["equals"]
    if field == "found":
        valid = type(expected) is bool
    elif field == "health01":
        valid = type(expected) in (int, float) and 0.0 <= expected <= 1.0 and math.isfinite(expected)
    elif field in ("attachment_count", "cargo_count", "items_total"):
        valid = type(expected) is int and expected >= 0
    else:
        raise ToolError("bad_args: entity.field is not an instrumented state field")
    if not valid:
        raise ToolError("bad_args: entity.equals has the wrong type or range for entity.field")
    return {"mode": "object_at", "type": entity["type"], "pos": position, "radius": radius}


def _entity_wait_observation(result: Any, entity: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Missing data is never interpreted as a false/zero entity state."""
    if not isinstance(result, dict) or type(result.get("ok")) not in (bool, int):
        raise ToolError("entity_state_unavailable: malformed result")
    if result["ok"] not in (True, 1):
        raise ToolError(str(result.get("error") or "entity_state_unavailable"))
    telemetry = result.get("telemetry")
    if not isinstance(telemetry, dict):
        raise ToolError("entity_state_unavailable: missing telemetry")
    found = telemetry.get("found")
    if type(found) not in (bool, int) or found not in (False, True):
        raise ToolError("entity_state_unavailable: invalid found")
    field = entity["field"]
    observed = {"found": bool(found), "field": field}
    if field != "found" and not found:
        return observed, False
    if field not in telemetry:
        raise ToolError("entity_state_unavailable: missing field " + field)
    actual = bool(found) if field == "found" else telemetry[field]
    if field == "health01":
        valid = type(actual) in (int, float) and 0.0 <= actual <= 1.0 and math.isfinite(actual)
    elif field == "found":
        valid = True
    else:
        valid = type(actual) is int and actual >= 0
    if not valid:
        raise ToolError("entity_state_unavailable: invalid field " + field)
    observed["value"] = actual
    return observed, actual == entity["equals"]


def _structured_not_ready_message(result: object) -> str | None:
    """Translate the F3 envelope into the existing wait_for retry token."""
    if not isinstance(result, dict):
        return None
    if result.get("ok") not in (False, 0) or result.get("code") != "not_ready":
        return None
    reason = result.get("reason")
    if not isinstance(reason, str) or not reason:
        return None
    return f"game_not_ready:reason={reason}"


_TOOL_LOCK_BUSY = "tool_lock_busy"


@asynccontextmanager
async def _tool_lock_until(runtime: Any, deadline: float) -> AsyncIterator[bool]:
    """Hold ``runtime.tool_lock`` if it can be had before ``deadline``.

    Yields whether it was acquired. wait_for fixes its deadline before it asks
    for the lock, so an unbounded wait lets a sibling tool that holds the lock
    stretch the call past timeout_s (fb-20260928-001643-f280). A lock that is
    free, with nobody queued for it, is taken even with no time left: acquire
    does not yield then, so the timeout cannot fire first. With waiters queued,
    the call waits its turn inside the deadline like any other. If the timeout
    fires while acquire waits, asyncio.Lock hands the wake-up to the next
    waiter, so nothing is left held.
    """
    acquired = False
    try:
        async with asyncio.timeout(max(deadline - time.monotonic(), 0.0)):
            await runtime.tool_lock.acquire()
        acquired = True
    except TimeoutError:
        pass
    try:
        yield acquired
    finally:
        if acquired:
            runtime.tool_lock.release()


async def execute_wait_for(
    runtime: Any,
    condition: str,
    value: int = 0,
    pattern: str = "",
    timeout_s: float = 180.0,
    poll_interval_s: float = 2.0,
    lookback_lines: int = 200,
    lookback_from: str = "lines",
    marker: str | dict[str, Any] | None = None,
    entity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Poll until a wait_for condition holds.

    ``pattern`` is a plain substring, never a regex -- see the matcher below.
    ``marker`` is a logs_since cursor. When present for ``log_matches``, it
    replaces the heuristic lookback and both lookback arguments are ignored.
    ``lookback_from="launch"`` scans this launch's logs from byte 0 once before
    polling, then tails from the end like the default.

    Any tool that waits on a human or a slow condition takes the lock
    per probe and sleeps outside it (``wait_for``, ``ui_dialog``,
    ``playbook_run``). The daemon is a multi-session broker: holding
    ``runtime.tool_lock`` across ``await asyncio.sleep`` would stall every
    other tool for the full wait. ``playbook_run`` is a compositor: it
    does not wrap its body in the lock; each step tool takes the lock as
    usual.
    """
    if condition not in WAIT_FOR_CONDITIONS:
        raise ToolError(
            "bad_args: condition must be one of "
            "players_at_least, players_at_most, log_matches, entity_state"
        )
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ToolError("bad_args: value must be a non-negative int")
    if not isinstance(pattern, str):
        raise ToolError("bad_args: pattern must be a string")
    if condition == "log_matches" and pattern == "":
        raise ToolError("bad_args: pattern must be non-empty when condition is log_matches")
    timeout_value = _finite_float(timeout_s, "bad_args: timeout_s must be > 0")
    if timeout_value <= 0.0:
        raise ToolError("bad_args: timeout_s must be > 0")
    if timeout_value > WAIT_FOR_MAX_TIMEOUT_S:
        raise ToolError(
            f"bad_args: timeout_s must be <= {int(WAIT_FOR_MAX_TIMEOUT_S)}"
        )
    timeout_s = timeout_value
    poll_value = _finite_float(poll_interval_s, "bad_args: poll_interval_s must be > 0")
    if poll_value <= 0.0:
        raise ToolError("bad_args: poll_interval_s must be > 0")
    poll_interval_s = max(poll_value, WAIT_FOR_MIN_POLL_INTERVAL_S)
    marker_state: dict[str, log_tail.TailMarker] | None = None
    if condition == "log_matches" and marker is not None:
        try:
            marker_state = log_tail.decode_marker(_coerce_logs_since_marker(marker))
        except log_tail.LogTailError:
            raise ToolError("bad_marker") from None
    else:
        if (
            not isinstance(lookback_lines, int)
            or isinstance(lookback_lines, bool)
            or lookback_lines < 0
            or lookback_lines > WAIT_FOR_LOOKBACK_MAX
        ):
            raise ToolError(
                f"bad_args: lookback_lines must be in 0..{WAIT_FOR_LOOKBACK_MAX}"
            )
        if lookback_from not in WAIT_FOR_LOOKBACK_FROM:
            raise ToolError('bad_args: lookback_from must be "lines" or "launch"')

    entity_args = _entity_wait_request(entity) if condition == "entity_state" else None
    started = time.monotonic()
    deadline = started + timeout_s
    probes = 0
    not_ready_probes = 0
    last_error: str | None = None
    observed: Any = None
    log_markers: dict[str, log_tail.TailMarker] = {}
    seen_paths: list[str] = []
    scanned_lines: dict[str, int] = {}
    unreadable: set[str] = set()
    scan_truncated = False
    scan_mode = "marker" if marker_state is not None else lookback_from

    def scan_summary() -> dict[str, Any] | None:
        if condition != "log_matches":
            return None
        return _scanned_report(
            seen_paths, scanned_lines, unreadable, scan_mode, scan_truncated
        )

    if condition == "log_matches":
        async with _tool_lock_until(runtime, deadline) as held:
            if not held:
                return _wait_for_response(
                    condition=condition,
                    started=started,
                    probes=0,
                    observed=observed,
                    satisfied=False,
                    scanned=scan_summary(),
                    last_error=_TOOL_LOCK_BUSY,
                )
            # A free lock is taken even with no time left. This block is
            # before the polling loop, so the loop's deadline check does not
            # cover it. Do not resolve paths or read a log once the deadline
            # has passed; the launch scan would otherwise accept a later line.
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return _wait_for_response(
                    condition=condition,
                    started=started,
                    probes=0,
                    observed=observed,
                    satisfied=False,
                    scanned=scan_summary(),
                )
            probe_paths = await _wait_for_script_log_paths(runtime)
            # Markers FIRST, then the launch scan. A line written between the
            # two is read twice, which costs nothing; the other order drops it.
            if marker_state is not None:
                log_markers = marker_state
            else:
                log_markers = (
                    _log_markers_at_end(probe_paths)
                    if lookback_from == "launch"
                    else _log_markers_with_lookback(probe_paths, lookback_lines)
                )
            if marker_state is None and lookback_from == "launch":
                for path in probe_paths:
                    try:
                        line, count, cut = _scan_log_for_pattern(
                            path, pattern, WAIT_FOR_LAUNCH_SCAN_MAX_BYTES
                        )
                    except log_tail.LogTailError:
                        _record_scan(
                            [path], {}, seen_paths, scanned_lines, unreadable
                        )
                        continue
                    _record_scan(
                        [path], {path: count}, seen_paths, scanned_lines, unreadable
                    )
                    scan_truncated = scan_truncated or cut
                    if line is not None:
                        return _wait_for_response(
                            condition=condition,
                            started=started,
                            probes=1,
                            observed=line,
                            satisfied=True,
                            scanned=scan_summary(),
                        )

    while time.monotonic() < deadline:
        async with _tool_lock_until(runtime, deadline) as held:
            if not held:
                last_error = _TOOL_LOCK_BUSY
                break
            # The while test can pass and this lock still be taken: a free
            # lock does not yield, so the timeout cannot fire first. A probe
            # must not start once the deadline has passed.
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                break
            probes += 1
            if condition == "entity_state":
                probe_timeout = min(DEFAULT_TOOL_TIMEOUT_S, remaining)
                result = await runtime.call_bridge("telemetry_read", entity_args, "server", probe_timeout)
                not_ready = _structured_not_ready_message(result)
                if not_ready is not None:
                    if not_ready not in _WAIT_FOR_RETRYABLE_NOT_READY:
                        raise ToolError(not_ready)
                    not_ready_probes += 1
                    last_error = not_ready
                    satisfied = False
                else:
                    observed, satisfied = _entity_wait_observation(result, entity)
            elif condition in {"players_at_least", "players_at_most"}:
                probe_timeout = min(DEFAULT_TOOL_TIMEOUT_S, max(remaining, POLL_INTERVAL_S))
                try:
                    result = await runtime.call_bridge(
                        "query_all_players", {}, "server", probe_timeout
                    )
                    not_ready = _structured_not_ready_message(result)
                    if not_ready is not None:
                        raise ToolError(not_ready)
                except ToolError as exc:
                    message = str(exc)
                    if message in _WAIT_FOR_RETRYABLE_NOT_READY:
                        not_ready_probes += 1
                        last_error = (
                            message
                            if message.startswith("game_not_ready:reason=")
                            else f"game_not_ready:reason={message}"
                        )
                        satisfied = False
                    elif message.startswith("timeout waiting for"):
                        # An accepted probe has its own (normally 15s) budget.
                        # Keep its abort semantics, but do not claim the whole
                        # wait expired when the caller still had time left.
                        now = time.monotonic()
                        outcome = "timed out" if now >= deadline else "aborted"
                        suffix = ""
                        if "; " in message:
                            # /status is peer-wide, not tied to this command or
                            # the caller's adopted run; old polls may survive.
                            suffix = "; station snapshot: " + message.split("; ", 1)[1]
                        raise ToolError(
                            f"wait_for {outcome} waiting for {condition}; "
                            f"reason=probe_timeout; elapsed_s={now - started:.3f}; "
                            f"timeout_s={timeout_s:g}; probe_timeout_s={probe_timeout:g}{suffix}"
                        ) from None
                    elif message.startswith("version_blocked") or message.startswith(
                        "game_not_ready"
                    ) or message in {
                        "daemon_unavailable",
                        "version_blocked",
                    }:
                        raise
                    elif "query_all_players" in message:
                        raise ToolError(
                            message.replace("query_all_players", "wait_for")
                        ) from None
                    else:
                        raise
                else:
                    observed = _player_count(result)
                    satisfied = (
                        observed >= value
                        if condition == "players_at_least"
                        else observed <= value
                    )
            else:
                probe_paths = await _wait_for_script_log_paths(runtime)
                lines, log_markers, counts = _new_log_lines(probe_paths, log_markers)
                _record_scan(
                    probe_paths, counts, seen_paths, scanned_lines, unreadable
                )
                # Substring, not regex. A caller who sends an escaped pattern
                # gets a silent no-match, which is why scanned exists and why
                # the tool description names the contract.
                matched = next((line for line in lines if pattern in line), None)
                satisfied = matched is not None
                observed = matched if matched is not None else (lines[-1] if lines else "")
        if satisfied:
            return _wait_for_response(
                condition=condition,
                started=started,
                probes=probes,
                observed=observed,
                satisfied=True,
                scanned=scan_summary(),
                not_ready_probes=not_ready_probes,
                last_error=last_error,
            )
        # Sleep outside the lock. Do not wrap this loop in tool_lock. The sleep
        # is bounded by the single deadline: a poll interval longer than the
        # remaining budget must not extend the call past timeout_s.
        remaining_sleep = deadline - time.monotonic()
        if remaining_sleep <= 0.0:
            break
        await asyncio.sleep(min(poll_interval_s, remaining_sleep))

    return _wait_for_response(
        condition=condition,
        started=started,
        probes=probes,
        observed=observed,
        satisfied=False,
        scanned=scan_summary(),
        not_ready_probes=not_ready_probes,
        last_error=last_error,
    )


async def _peer_liveness_suffix(runtime: Any, peer: str) -> str:
    try:
        message_fn = getattr(runtime, "liveness_message", None)
        if callable(message_fn):
            text = message_fn(peer)
            if inspect.isawaitable(text):
                text = await text
            return f"; {text}"
        alt = getattr(runtime, "_liveness_message", None)
        if callable(alt):
            return f"; {await alt(peer)}"
    except Exception:
        return ""
    return ""


async def execute_ui_dialog(
    runtime: Any,
    kind: str,
    title: str,
    message: str = "",
    fields: list[dict[str, Any]] | None = None,
    timeout_s: float = ui_dialog_mod.DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Show a client modal and wait for the local player.

    Any tool that waits on a human or a slow condition takes the lock
    per probe and sleeps outside it (``wait_for``, ``ui_dialog``,
    ``playbook_run``).
    """
    try:
        request = ui_dialog_mod.parse_request(kind, title, message, fields, timeout_s)
    except ui_dialog_mod.UiDialogError as exc:
        raise ToolError(str(exc)) from None

    args = ui_dialog_mod.bridge_args(request)
    budget = ui_dialog_mod.bridge_wait_budget_s(request.timeout_s)
    async with runtime.tool_lock:
        command_id = await runtime.enqueue_bridge(
            "ui_dialog", args, "client", budget
        )

    deadline = time.monotonic() + budget
    try:
        while time.monotonic() < deadline:
            async with runtime.tool_lock:
                result = await runtime.probe_bridge_result(
                    "ui_dialog", command_id, "client"
                )
            if result is not None:
                try:
                    return ui_dialog_mod.interpret_result(request, result)
                except ui_dialog_mod.UiDialogError as exc:
                    raise ToolError(str(exc)) from None
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                break
            # Sleep outside the lock. Do not wrap this loop in tool_lock.
            await asyncio.sleep(min(WAIT_FOR_MIN_POLL_INTERVAL_S, remaining))
    except asyncio.CancelledError:
        async with runtime.tool_lock:
            await runtime.abandon_bridge(command_id, "cancelled")
        raise

    async with runtime.tool_lock:
        await runtime.abandon_bridge(command_id, "tool_timeout")
    suffix = await _peer_liveness_suffix(runtime, "client")
    raise ToolError(f"timeout waiting for ui_dialog id={command_id}{suffix}")


def _parse_wait_for_box_s(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError("bad_args: wait_for_box_s must be a finite float >= 0")
    converted = float(value)
    if not math.isfinite(converted) or converted < 0.0:
        raise ToolError("bad_args: wait_for_box_s must be a finite float >= 0")
    if converted > BOX_WAIT_MAX_S:
        raise ToolError(
            f"bad_args: wait_for_box_s must be <= {BOX_WAIT_MAX_S:g}"
        )
    return converted


def _parse_on_busy(value: object) -> str:
    if value not in {"fail", "queue"}:
        raise ToolError("bad_args: on_busy must be 'fail' or 'queue'")
    return str(value)


def _parse_client_start_budget_s(value: object) -> float | None:
    """Validate the startup budget at the WIRE, which is where the frontier is.

    Rejecting it inside the executor was not enough, and the reason is worth
    keeping: pydantic coerces before any of our code runs, so ``false`` arrives
    as ``0.0`` and ``"5"`` as ``5.0``. A budget of zero passes every range check
    and disables the guard completely -- fail-open, reached by the one input
    that most looks like "no". The StrictFloat|StrictInt annotation on the tool
    stops the coercion; this function owns the range, next to
    ``_parse_wait_for_box_s`` and raising the same ``bad_args:`` token, because
    a bare ValueError from the executor reaches the caller as
    ``dayz_test_failed:ValueError`` and names neither the field nor the range.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError(
            "bad_args: client_start_budget_s must be a finite number in "
            f"[0, {CLIENT_START_BUDGET_MAX_S:g}]"
        )
    converted = float(value)
    if not math.isfinite(converted) or not 0.0 <= converted <= CLIENT_START_BUDGET_MAX_S:
        raise ToolError(
            "bad_args: client_start_budget_s must be a finite number in "
            f"[0, {CLIENT_START_BUDGET_MAX_S:g}]"
        )
    return converted


def _box_from_status(status: object) -> dict[str, Any]:
    if not isinstance(status, dict):
        return empty_box(occupied=True)
    box = status.get("box")
    if not isinstance(box, dict):
        return empty_box(occupied=True)
    payload = dict(box)
    if not isinstance(payload.get("runs"), list):
        payload["runs"] = []
    if not isinstance(payload.get("foreign"), list):
        payload["foreign"] = []
    if not isinstance(payload.get("ports_in_use"), list):
        payload["ports_in_use"] = []
    if not isinstance(payload.get("queue"), list):
        payload["queue"] = []
    if payload.get("occupied") is not False:
        payload["occupied"] = bool(payload.get("occupied", True))
    return payload


def _box_session_is(session: object, session_id: str) -> bool:
    if not isinstance(session, str) or not isinstance(session_id, str):
        return False
    return session in {session_id, session_id[:12]}


def _box_head_is(box: dict[str, Any], session_id: str) -> bool:
    queue = box.get("queue")
    if not isinstance(queue, list) or not queue:
        return False
    head = queue[0]
    if not isinstance(head, dict):
        return False
    return _box_session_is(head.get("session"), session_id)


def _box_ready_for(box: dict[str, Any], session_id: str) -> bool:
    if box.get("occupied") is True:
        return False
    return _box_head_is(box, session_id)


_BOX_OFFER_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_BOX_OFFER_MOD = re.compile(r"[A-Za-z0-9@ _.+-]{1,64}")
_BOX_OFFER_STATES = frozenset({
    "STARTING",
    "RUNNING",
    "RUNNING_IDLE",
    "STOPPING",
    "UNRECONCILED",
})
_BOX_OFFER_MOD_CAP = 16


def _offer_run_id(value: object) -> str | None:
    if isinstance(value, str) and _BOX_OFFER_RUN_ID.fullmatch(value):
        return value
    return None


def _offer_state(value: object) -> str | None:
    if isinstance(value, str) and value in _BOX_OFFER_STATES:
        return value
    return None


def _offer_port(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535:
        return value
    return None


def _offer_age_s(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _offer_mods(raw: object) -> list[str]:
    if isinstance(raw, str):
        items: list[object] = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    kept: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item:
            continue
        if item[0] == " " or item[-1] == " ":
            continue
        if _BOX_OFFER_MOD.fullmatch(item) is None:
            continue
        kept.append(item)
        if len(kept) >= _BOX_OFFER_MOD_CAP:
            break
    return kept


def _first_box_dict(items: object) -> dict[str, Any] | None:
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict):
            return item
    return None


def _box_run(box: object, run_id: object) -> dict[str, Any] | None:
    if not isinstance(box, dict) or not isinstance(run_id, str) or not run_id:
        return None
    runs = box.get("runs")
    if not isinstance(runs, list):
        return None
    for item in runs:
        if isinstance(item, dict) and item.get("run_id") == run_id:
            return item
    return None


def _row_is_protected(
    row: dict[str, Any] | None, caller_session: str | None
) -> bool:
    """Ownerless RUNNING_IDLE, or a revert that has not landed. Fail closed.

    A RUNNING row is not this refusal unless its adoption revert is still
    pending. The launcher is not refused. The daemon decides with the full
    session id; this layer only has the public prefix.
    """

    if not isinstance(row, dict):
        return False
    if row.get("use_reason") == ADOPTION_REVERT_PENDING:
        return not caller_launched_row(row, caller_session)
    if row.get("state") != "RUNNING_IDLE":
        return False
    owner = row.get("owner_session")
    if isinstance(owner, str) and owner:
        return False
    return not caller_may_adopt_ownerless(row, caller_session)


def _box_wait_cannot_help(
    box: object,
    caller_session: str | None = None,
    port: int | None = None,
) -> str | None:
    """Return a reason when waiting in the FIFO cannot free the box."""

    if not isinstance(box, dict):
        return None
    if box.get("port_scan_known") is False:
        return "port_scan_unknown"
    session_id = caller_session if isinstance(caller_session, str) else ""
    runs = box.get("runs")
    if isinstance(runs, list) and session_id:
        for occupier in runs:
            if (
                isinstance(occupier, dict)
                and occupier.get("state") in {"RUNNING", "RUNNING_IDLE"}
                and _box_session_is(occupier.get("owner_session"), session_id)
            ):
                return "own_run"
    if box_available_for(box, caller_session=session_id or None)["adopt"] is True:
        return "adopt"
    if isinstance(runs, list):
        for item in runs:
            if isinstance(item, dict) and item.get("state") == "UNRECONCILED":
                return "unreconciled"
    if _port_conflict_fields(box, port).get("reason") == "port_in_use_foreign":
        return "port_in_use_foreign"
    return None


def _occupant_from_box(box: dict[str, Any]) -> tuple[dict[str, Any], float | None]:
    run = _first_box_dict(box.get("runs"))
    if run is not None:
        mods_raw: object = run.get("mods")
        if not isinstance(mods_raw, list):
            mods_raw = run.get("mod")
        occupant = {
            "run_id": _offer_run_id(run.get("run_id")),
            "state": _offer_state(run.get("state")),
            "foreign": False,
            "port": _offer_port(run.get("port")),
            "mods": _offer_mods(mods_raw),
        }
        return occupant, _offer_age_s(run.get("age_s"))
    foreign = _first_box_dict(box.get("foreign"))
    if foreign is not None:
        occupant = {
            "run_id": _offer_run_id(foreign.get("run_id")),
            "state": _offer_state(foreign.get("state")),
            "foreign": True,
            "port": _offer_port(foreign.get("port")),
            "mods": _offer_mods(foreign.get("mods")),
        }
        return occupant, _offer_age_s(foreign.get("age_s"))
    occupant = {
        "run_id": None,
        "state": None,
        "foreign": False,
        "port": None,
        "mods": [],
    }
    return occupant, _offer_age_s(box.get("claimed_s"))


def _box_queue_position(box: dict[str, Any], session_id: str) -> int | None:
    queue = box.get("queue")
    if not isinstance(queue, list) or not session_id:
        return None
    for index, item in enumerate(queue):
        if isinstance(item, dict) and _box_session_is(item.get("session"), session_id):
            return index + 1
    return None


def _box_queue_offer(
    box: object,
    *,
    caller_session: str | None = None,
    port: int | None = None,
) -> dict[str, Any] | None:
    """Actionable FIFO offer for a busy-box rejection or session_status."""

    if not isinstance(box, dict):
        return None
    session_id = caller_session if isinstance(caller_session, str) else ""
    wait_reason = _box_wait_cannot_help(
        box, caller_session=session_id or None, port=port
    )
    if wait_reason is not None and wait_reason != "own_run":
        return None
    if _port_conflict_fields(box, port).get("reason") == "port_in_use_foreign":
        return None
    waiters = 0
    queue = box.get("queue")
    if isinstance(queue, list):
        for item in queue:
            session = item.get("session") if isinstance(item, dict) else None
            if _box_session_is(session, session_id):
                continue
            waiters += 1
    occupant, age_s = _occupant_from_box(box)
    retry: dict[str, Any] = {
        "tool": "dayz_test_run",
        "same_args": True,
    }
    if wait_reason != "own_run":
        retry["on_busy"] = "queue"
    return {
        "position_if_joined": waiters + 1,
        "waiters": waiters,
        "occupant": occupant,
        "occupant_age_s": age_s,
        "retry": retry,
    }


def _attach_queue_offer(
    payload: dict[str, Any],
    box: object,
    *,
    caller_session: str | None = None,
    port: int | None = None,
) -> dict[str, Any]:
    if payload.get("error_code") in {
        "active_run_exists",
        TAKEOVER_REQUIRED,
        "run_protected",
    }:
        payload["queue_offer"] = _box_queue_offer(
            box, caller_session=caller_session, port=port
        )
    return payload


_DAYZ_GAME_PORT_MIN = 2302
_DAYZ_GAME_PORT_MAX = 2999


def _port_is_dayz_relevant(port: object) -> bool:
    return (
        isinstance(port, int)
        and not isinstance(port, bool)
        and _DAYZ_GAME_PORT_MIN <= port <= _DAYZ_GAME_PORT_MAX
    )


def _foreign_port_number(item: object) -> int | None:
    if isinstance(item, int) and not isinstance(item, bool):
        return item
    if isinstance(item, dict):
        port = item.get("port")
        if isinstance(port, int) and not isinstance(port, bool):
            return port
    return None


def _foreign_ports_contain(foreign_ports: object, port: int) -> bool:
    if not isinstance(foreign_ports, list):
        return False
    return any(_foreign_port_number(item) == port for item in foreign_ports)


def _annotate_foreign_port_list(
    ports: object,
    *,
    relevant_ports: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Normalize a foreign_ports* list to [{port, dayz_relevant}, ...].

    When relevant_ports is set (membership of capture's DayZ-related
    foreign_ports), dayz_relevant follows that set so image-classified
    ports outside 2302-2999 stay coherent with foreign_ports_meta.dayz_related.
    Otherwise fall back to the UDP game-port range alone.
    """
    if not isinstance(ports, list):
        return []
    annotated: list[dict[str, Any]] = []
    for item in ports:
        port = _foreign_port_number(item)
        if port is None:
            continue
        extra = dict(item) if isinstance(item, dict) else {}
        extra.pop("port", None)
        extra["port"] = port
        if relevant_ports is not None:
            extra["dayz_relevant"] = port in relevant_ports
        else:
            extra["dayz_relevant"] = _port_is_dayz_relevant(port)
        annotated.append(extra)
    return annotated


def _annotate_box_foreign_ports(box: dict[str, Any]) -> dict[str, Any]:
    """Flag DayZ-relevant listeners; keep full table under foreign_ports_all.

    Capture already filters foreign_ports to image-or-range DayZ-related
    holders. Annotate both lists from that membership so a DayZ image on
    e.g. 1234 stays dayz_relevant:true and meta dayz_relevant matches
    dayz_related (fb-1432 B1). Range-only marking demoted those ports.
    """
    ports = box.get("foreign_ports")
    relevant_ports: set[int] | None = None
    if isinstance(ports, list):
        relevant_ports = {
            port
            for port in (_foreign_port_number(item) for item in ports)
            if port is not None
        }

    annotated: list[dict[str, Any]] = []
    if isinstance(ports, list):
        annotated = _annotate_foreign_port_list(
            ports, relevant_ports=relevant_ports
        )
        box["foreign_ports"] = annotated

    ports_all = box.get("foreign_ports_all")
    annotated_all: list[dict[str, Any]] | None = None
    if isinstance(ports_all, list):
        annotated_all = _annotate_foreign_port_list(
            ports_all, relevant_ports=relevant_ports
        )
        box["foreign_ports_all"] = annotated_all

    meta = dict(box.get("foreign_ports_meta") or {})
    if isinstance(ports, list):
        meta["count"] = len(annotated)
        meta["dayz_relevant"] = sum(
            1 for item in annotated if item.get("dayz_relevant")
        )
    if annotated_all is not None:
        meta["count_all"] = len(annotated_all)
    box["foreign_ports_meta"] = meta
    return box


def _port_conflict_fields(box: object, port: int | None) -> dict[str, Any]:
    """Diagnosis for an active_run_exists that the box alone cannot explain.

    fb-20260904-114520-6927: a launch refused because the requested port is
    held by a process that is not ours (any image) arrives with a box that
    reads free -- only DayZ images occupy the box -- so the generic hint told
    the caller to wait for a box that was never busy. The held port is in
    foreign_ports; say that, and say when the socket table itself could not be
    read (waiting does not repair that either).
    """
    if not isinstance(box, dict):
        return {}
    if box.get("port_scan_known") is False:
        reason = box.get("port_scan_reason")
        return {
            "reason": reason if isinstance(reason, str) and reason else "port_scan_unknown",
            "hint": (
                "the daemon could not read the host UDP socket table "
                "(psutil/netstat): waiting does not help, restore that first"
            ),
        }
    # Prefer the full table when present (option A); fall back to the default
    # DayZ-related list for older payloads that never emitted foreign_ports_all.
    foreign_ports_all = box.get("foreign_ports_all")
    foreign_ports = (
        foreign_ports_all
        if isinstance(foreign_ports_all, list)
        else box.get("foreign_ports")
    )
    runs = box.get("runs")
    if isinstance(port, int) and _foreign_ports_contain(foreign_ports, port):
        # Any requested port, any image: foreign_ports_all is the socket table
        # minus managed runs. When a run also occupies the box, both blockers
        # are named -- freeing the box does not free this port.
        if isinstance(runs, list) and runs:
            hint = (
                f"the box is busy (see occupied_by_run_id) AND port {port} is held "
                "by a process that is not a managed run: after the box frees, pass "
                "another port= or wait for that holder to exit; waiting for the box "
                "alone does not free the port"
            )
        else:
            hint = (
                f"port {port} is held by a process on this host that is not a "
                "managed run (see session_status.box.foreign_ports_all): pass another "
                "port= or wait for its holder to exit; wait_for_box_s does not "
                "help while the box reads free"
            )
        return {"reason": "port_in_use_foreign", "port": port, "hint": hint}
    return {}


def _apply_takeover_required(
    payload: dict[str, Any],
    box: object,
    *,
    caller_session: str | None = None,
) -> dict[str, Any]:
    if payload.get("reason") == "port_in_use_foreign":
        return payload
    target = takeover_target_run_id(box, caller_session=caller_session)
    if target is None:
        return payload
    row = _box_run(box, target)
    if _row_is_protected(row, caller_session):
        payload["error_code"] = "run_protected"
        notice = protection_fields(row)
        payload["hint"] = notice["hint"]
        payload["use_state"] = notice["use_state"]
        payload["use_reason"] = notice["use_reason"]
        if "retry_after_s" in notice:
            payload["retry_after_s"] = notice["retry_after_s"]
        return payload
    payload["error_code"] = TAKEOVER_REQUIRED
    extra = occupancy_error_fields(box, caller_session=caller_session)
    hint = extra.get("hint")
    if isinstance(hint, str) and hint:
        payload["hint"] = hint
    return payload


def _enrich_active_run_result(
    result: dict[str, Any],
    box: dict[str, Any],
    *,
    caller_session: str | None = None,
    port: int | None = None,
) -> dict[str, Any]:
    extra = occupancy_error_fields(box, caller_session=caller_session)
    payload = dict(result)
    payload.update(extra)
    payload.update(_port_conflict_fields(box, port))
    payload["run_id"] = None
    payload["status"] = "failed"
    payload["error_code"] = "active_run_exists"
    return _attach_queue_offer(
        _apply_takeover_required(payload, box, caller_session=caller_session),
        box,
        caller_session=caller_session,
        port=port,
    )


def _failed_active_run_result(
    *,
    project: str,
    mode: str,
    box: object,
    started: float,
    caller_session: str | None = None,
    port: int | None = None,
) -> dict[str, Any]:
    extra = occupancy_error_fields(box, caller_session=caller_session)
    extra.update(_port_conflict_fields(box, port))
    return _attach_queue_offer(
        _apply_takeover_required(
            {
                "status": "failed",
                "project": project,
                "mode": mode,
                "run_id": None,
                "phase": "executing",
                "elapsed_s": round(time.monotonic() - started, 3),
                "artifacts_paths": [],
                "error_code": "active_run_exists",
                "cleanup_degraded": False,
                "server_alive": None,
                "client_alive": None,
                **extra,
            },
            box,
            caller_session=caller_session,
        ),
        box,
        caller_session=caller_session,
        port=port,
    )


_TOOL_REGISTRY_STALE_WARNING = "tool_registry_stale_reopen_client"
_TOOL_REGISTRY_STALE_HINT_KEY = "tool_registry_stale_hint"
_TOOL_REGISTRY_STALE_HINT = (
    "The host must reconnect the dayz-mcp server to list the current tools. "
    "The agent cannot do that itself (Claude Code: /mcp, then reconnect "
    "dayz-mcp). Claude Code gets every tool from the start; another host "
    "that does not re-list after tools/list_changed should be registered "
    "with --no-progressive-disclosure. The list does not gate calls: a tool "
    "it leaves out still runs when called by name, and a mutation without "
    "a lease is refused with lease_required."
)


def _annotate_caller_tool_registry(
    payload: dict[str, Any], stale: bool
) -> dict[str, Any]:
    payload["caller_tool_registry_stale"] = bool(stale)
    if not stale:
        return payload
    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        warnings = list(warnings)
    else:
        warnings = []
    if _TOOL_REGISTRY_STALE_WARNING not in warnings:
        warnings.append(_TOOL_REGISTRY_STALE_WARNING)
    payload["warnings"] = warnings
    payload[_TOOL_REGISTRY_STALE_HINT_KEY] = _TOOL_REGISTRY_STALE_HINT
    return payload


async def _observe_caller_tool_registry_stale(
    observe: Callable[[], dict[str, Any]],
) -> bool:
    try:
        snapshot = await asyncio.to_thread(observe)
        return source_stale(snapshot)
    except Exception:
        return True


async def _heartbeat_box_claim(
    client: Any,
    ticket: str,
    *,
    sleep_fn: Callable[[float], Awaitable[None]] | None = None,
) -> None:
    """Refresh a claimed box ticket while the launch holds tool_lock.

    Must not take ``tool_lock``: the launch already holds it. The daemon
    call only needs to bump ``touched_at`` on the claim. Transient
    transport errors must not stop the heartbeat.
    """

    sleeper = sleep_fn or asyncio.sleep
    while True:
        await sleeper(BOX_CLAIM_HEARTBEAT_S)
        try:
            await client.session_box_status(wait=True, ticket=ticket)
        except Exception:
            continue


async def _release_box_wait_ticket(client: Any, ticket: str | None) -> None:
    """Leave the box FIFO even if the MCP request CancelScope is cancelled.

    Must not take ``tool_lock``: a sibling can hold that lock for a
    bridge timeout or a whole wait, and the leave only needs to reach
    the daemon, same as the claim heartbeat. ``ticket`` None is
    leave-session. A stuck daemon is bounded so shielded cleanup
    cannot hang the tool.
    """

    with anyio.CancelScope(shield=True):
        with anyio.move_on_after(5):
            try:
                await client.session_box_status(done=True, ticket=ticket)
            except Exception:
                pass


async def execute_wait_for_box(
    client: Any,
    wait_s: float,
    *,
    sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    time_fn: Callable[[], float] | None = None,
    poll_interval_s: float | None = None,
    abort_if: Callable[[dict[str, Any]], object] | None = None,
) -> dict[str, Any]:
    """Wait until the box is free and this waiter is FIFO head.

    Probes under ``tool_lock`` and sleeps outside it, same rule as
    ``wait_for`` / ``ui_dialog``. Does not take a lease and does not
    require the caller to heartbeat. ``abort_if`` is checked after
    every status read; a truthy result ends the wait like a timeout
    (the caller still holds the ticket to leave the FIFO).
    """

    sleeper = sleep_fn or asyncio.sleep
    clock = time_fn or time.monotonic
    interval = BOX_WAIT_POLL_S if poll_interval_s is None else poll_interval_s
    poll = max(float(interval), BOX_WAIT_MIN_POLL_S)
    deadline = clock() + float(wait_s)
    ticket: str | None = None
    box = empty_box(occupied=True)
    session_id = str(getattr(getattr(client, "identity", None), "session_id", "") or "")
    join_task: asyncio.Task[Any] | None = None
    try:
        while True:
            wait_error = None
            async with client.tool_lock:
                join_task = asyncio.create_task(
                    client.session_box_status(wait=True, ticket=ticket)
                )
                status = await asyncio.shield(join_task)
            if isinstance(status, dict):
                box = _box_from_status(status)
                next_ticket = status.get("box_ticket")
                wait_error = status.get("box_wait_error")
                if wait_error in {"box_ticket_invalid", "box_wait_cancelled"}:
                    ticket = None
                elif isinstance(next_ticket, str) and next_ticket:
                    ticket = next_ticket
            else:
                wait_error = "box_status_invalid"
            if box.get("port_scan_known") is False:
                # The box reads occupied because the daemon could not read the
                # UDP socket table; the FIFO does not repair that, so waiting
                # would only burn the timeout. Say so at once.
                return {
                    "ok": False,
                    "ticket": ticket,
                    "box": box,
                    "error": "port_scan_unknown",
                }
            if wait_error == "box_queue_saturated":
                return {
                    "ok": False,
                    "ticket": ticket,
                    "box": box,
                    "error": "box_queue_saturated",
                }
            if abort_if is not None:
                reason = abort_if(box)
                if reason:
                    payload: dict[str, Any] = {
                        "ok": False,
                        "ticket": ticket,
                        "box": box,
                    }
                    if isinstance(reason, str):
                        payload["error"] = reason
                    return payload
            ready = (
                isinstance(ticket, str)
                and ticket
                and wait_error is None
                and _box_ready_for(box, session_id)
            )
            if ready:
                async with client.tool_lock:
                    join_task = asyncio.create_task(
                        client.session_box_status(
                            wait=True, ticket=ticket, claim=True
                        )
                    )
                    claimed = await asyncio.shield(join_task)
                holds_claim = True
                if isinstance(claimed, dict):
                    box = _box_from_status(claimed)
                    holds_claim = claimed.get("box_claimed") is not False
                if holds_claim:
                    return {"ok": True, "ticket": ticket, "box": box}
            remaining = deadline - clock()
            if remaining <= 0.0:
                return {"ok": False, "ticket": ticket, "box": box}
            await sleeper(min(poll, remaining))
    except BaseException:
        with anyio.CancelScope(shield=True):
            started = time.monotonic()
            with anyio.move_on_after(5):
                if join_task is not None and (
                    ticket is None or join_task.done()
                ):
                    try:
                        status = await join_task
                    except (Exception, asyncio.CancelledError):
                        status = None
                    else:
                        if isinstance(status, dict):
                            next_ticket = status.get("box_ticket")
                            if isinstance(next_ticket, str) and next_ticket:
                                ticket = next_ticket
            leftover = 5.0 - (time.monotonic() - started)
            with anyio.move_on_after(leftover if leftover > 0.0 else 0.05):
                try:
                    await client.session_box_status(done=True, ticket=ticket)
                except Exception:
                    pass
            if join_task is not None and not join_task.done():
                join_task.cancel()
        raise


ADOPT_BLOCKED_ON = (
    "DayZ test box has an ownerless RUNNING_IDLE run; next: call "
    "session_acquire_wait(purpose=...) to adopt it. dayz_test_run wait_for_box_s "
    "is for a new launch, not this box."
)
_TOOLS_REMEDIATION_STALE = "tool_registry_schema_signal=stale_client"
_TOOLS_REMEDIATION_UNKNOWN = (
    "tool_registry_schema_signal=unknown; sources unverifiable, not a crash"
)
_DAEMON_REMEDIATION_WHEN = (
    "daemon_modules.stale or unreadable is non-empty; not a crash; do not kill; "
    "reopen_mcp_client does not refresh the daemon"
)
_MUTATION_REJECTS_META = {
    "kind": "historical",
    "window": "since_daemon_start",
    "origin": "loopback_enqueue_fence",
    "blocks_now": False,
}


def _ownerless_idle_runs(box: dict[str, Any]) -> list[dict[str, Any]]:
    runs = box.get("runs")
    if not isinstance(runs, list):
        return []
    idle: list[dict[str, Any]] = []
    for item in runs:
        if not isinstance(item, dict):
            continue
        if item.get("state") != "RUNNING_IDLE":
            continue
        owner = item.get("owner_session")
        if owner is None or owner == "":
            idle.append(item)
    return idle


def box_available_for(
    box: object, caller_session: str | None = None
) -> dict[str, bool]:
    """MCP-only: new launch, or adopt for the launcher or an abandoned run.

    caller_session None cannot prove the launcher, so adopt is only abandoned.
    """

    unavailable = {"new_launch": False, "adopt": False}
    if not isinstance(box, dict):
        return unavailable
    if box.get("port_scan_known") is False:
        return unavailable
    if box.get("occupied") is not True:
        return {"new_launch": True, "adopt": False}
    foreign = box.get("foreign")
    if not isinstance(foreign, list) or foreign:
        return unavailable
    idle = _ownerless_idle_runs(box)
    if len(idle) == 1 and caller_may_adopt_ownerless(idle[0], caller_session):
        return {"new_launch": False, "adopt": True}
    return unavailable


def _tool_registry_remediation_for(signal: str) -> dict[str, str] | None:
    if signal == "fresh":
        return None
    if signal == "stale_client":
        applies_when = _TOOLS_REMEDIATION_STALE
    else:
        applies_when = _TOOLS_REMEDIATION_UNKNOWN
    return {
        "code": _TOOL_REGISTRY_REMEDIATION,
        "scope": "tools",
        "applies_when": applies_when,
    }


def _daemon_source_remediation(daemon_modules: object) -> dict[str, str] | None:
    if not isinstance(daemon_modules, dict):
        return None
    stale = daemon_modules.get("stale")
    unreadable = daemon_modules.get("unreadable")
    stale_list = stale if isinstance(stale, list) else []
    unread_list = unreadable if isinstance(unreadable, list) else []
    if not stale_list and not unread_list:
        return None
    return {
        "code": "none",
        "scope": "daemon",
        "applies_when": _DAEMON_REMEDIATION_WHEN,
    }


def _annotate_mcp_fence(overlay: dict[str, Any]) -> None:
    fence = overlay.get("fence")
    if not isinstance(fence, dict):
        return
    annotated = dict(fence)
    annotated["mutation_rejects_meta"] = dict(_MUTATION_REJECTS_META)
    overlay["fence"] = annotated


def _lease_renewal_contract(ttl_s: float) -> str:
    """Lease TTL and how an interactive session keeps or loses it."""
    return (
        "Calls that reach the box with this session's lease (bridge verbs "
        "and probes such as players_* and entity_state, dayz_test_run, "
        "dayz_test_stop) and session_heartbeat renew the lease; "
        "session_status does not renew the lease. With no renewing call "
        f"for longer than {ttl_s:g} s the lease expires; an adopted run "
        "then becomes ownerless RUNNING_IDLE and the next client verb on "
        "that run returns run_not_owned. session_heartbeat keeps the lease "
        "across a longer pause."
    )


async def _client_dump_bindings(client: object, status: dict[str, Any]) -> object:
    """296b r3: the daemon's dump snapshot of each retired run, or None.

    An older daemon, a failed call or a malformed answer is None, and every
    row's client_death_diagnosis is then null.
    """
    return (await _client_dump_snapshot(client, status))[0]


async def _client_dump_snapshot(
    client: object, status: dict[str, Any]
) -> tuple[object, str | None]:
    """(bindings, registry revision they were read at); (None, None) on failure."""
    raw = status.get("retired_run_diagnostics")
    if not isinstance(raw, list):
        return None, None
    run_ids = [
        item["run_id"]
        for item in raw
        if isinstance(item, dict) and isinstance(item.get("run_id"), str)
    ][:MAX_CLIENT_DUMP_RUN_IDS]
    if not run_ids:
        return None, None
    try:
        # Called directly, not through getattr (security_runtime_audit); a
        # client without it raises AttributeError, which is null here too.
        response = await client.client_dumps_get(run_ids)  # type: ignore[attr-defined]
    except Exception:
        return None, None
    if not isinstance(response, dict):
        return None, None
    bindings = response.get("bindings")
    if not isinstance(bindings, dict):
        return None, None
    revision = response.get("revision")
    return bindings, revision if isinstance(revision, str) and revision else None


def _attach_runs_retired_recently(
    status: dict[str, Any], bindings: object = None
) -> None:
    if "retired_run_diagnostics" not in status:
        status["runs_retired_recently"] = None
        return
    status["runs_retired_recently"] = dayz_test_tool._runs_retired_recently(
        status.pop("retired_run_diagnostics"), bindings
    )


async def _attach_revalidated_runs_retired_recently(
    client: object, status: dict[str, Any]
) -> None:
    """296b r4: publish a named death only if its binding outlived the scan.

    The bindings travel to this process and the shared profile is scanned
    afterwards; another MCP process can launch in between and write the dump
    the scan then reads. A named death is published only if the daemon's
    registry revision is the same after the scan as when the bindings were
    read. Otherwise one retry with fresh bindings, then every row is null.
    A row that names nothing needs no proof.
    """
    if "retired_run_diagnostics" not in status:
        status["runs_retired_recently"] = None
        return
    raw = status.pop("retired_run_diagnostics")
    request = {"retired_run_diagnostics": raw}
    for _attempt in range(2):
        bindings, revision = await _client_dump_snapshot(client, request)
        rows = dayz_test_tool._runs_retired_recently(raw, bindings)
        if not any(
            row.get("client_death_diagnosis") is not None for row in rows or []
        ):
            status["runs_retired_recently"] = rows
            return
        if revision is None:
            break
        _unused, after = await _client_dump_snapshot(client, request)
        if after == revision:
            status["runs_retired_recently"] = rows
            return
    status["runs_retired_recently"] = dayz_test_tool._runs_retired_recently(raw, None)


def _protection_blocked_on(row: dict[str, Any]) -> str:
    """What protects the run, and until when, for a caller who cannot adopt."""

    notice = protection_fields(row)
    state = notice.get("use_state") or "unclassified"
    reason = notice.get("use_reason")
    text = f"DayZ test box is protected ({state}"
    if isinstance(reason, str) and reason:
        text += f", {reason}"
    text += ")"
    retry = notice.get("retry_after_s")
    if isinstance(retry, (int, float)) and not isinstance(retry, bool):
        text += f"; it can be abandoned in {retry}s"
    else:
        text += "; no time is given for when it can be abandoned"
    text += ". Waiting in the box FIFO does not adopt it."
    return text


def _session_status_blocked_on(
    status: dict[str, Any], caller_session: str | None = None
) -> str | None:
    """Return the next queue a caller should join, if a resource is busy."""

    if isinstance(status.get("owner"), dict):
        return (
            "session lease; next: call session_acquire_wait(purpose=...) "
            "to join the lease FIFO"
        )
    box = status.get("box")
    if isinstance(box, dict) and box.get("port_scan_known") is False:
        # The box reads occupied because the daemon could not read or
        # attribute the host UDP socket table; the FIFO does not repair that.
        reason = box.get("port_scan_reason")
        reason_text = reason if isinstance(reason, str) and reason else "port_scan_unknown"
        return (
            f"DayZ test box, {reason_text}; next: restore the daemon's view of the "
            "host UDP socket table (psutil/netstat, process attribution) -- "
            "wait_for_box_s does not help"
        )
    if isinstance(box, dict):
        runs = box.get("runs")
        if isinstance(runs, list):
            for item in runs:
                if (
                    isinstance(item, dict)
                    and item.get("use_reason") == ADOPTION_REVERT_PENDING
                    and _row_is_protected(item, caller_session)
                ):
                    return _protection_blocked_on(item)
    if isinstance(box, dict) and box_available_for(box, caller_session)["adopt"] is True:
        return ADOPT_BLOCKED_ON
    if isinstance(box, dict):
        idle = _ownerless_idle_runs(box)
        if len(idle) == 1 and _row_is_protected(idle[0], caller_session):
            return _protection_blocked_on(idle[0])
    if isinstance(box, dict) and box.get("occupied") is True:
        return (
            'DayZ test box; next: call dayz_test_run(..., on_busy="queue") '
            "to join the box FIFO"
        )
    return None


def _bridge_status_description() -> str:
    """Publish the open ready.reason set from module authority at build time."""
    reasons = sorted(READY_REASONS | set(_FENCE_BLOCK_READY.values()))
    reason_list = "|".join(reasons)
    return (
        "Inspect peer liveness, version_state, and ready "
        f"{{ready, reason is an OPEN set (today: {reason_list}): validate by shape "
        "(ready: bool first, reason: non-empty string, next_step: public tool "
        "when ready is false, then stale_threshold_s "
        "and per-peer last_poll_age_s / bound_last_poll_age_s), never against "
        "a whitelist}}. After a fresh launch, leftover ages from a dead "
        "pre-launch peer are forgotten; ready.reason is binding_not_ready "
        "until this generation's first accredited poll, not server_poll_stale. "
        "daemon_modules.stale = source newer than daemon, not a crash. "
        "server_modules watches this tools process's loaded Python sources; "
        "stale lists changed content, unreadable lists unverifiable sources. "
        "tool_registry_source_stale is always a boolean: true for stale OR "
        "unknown, false only for verified fresh. tool_registry_schema_signal "
        "is fresh | stale_client | unknown (stale_client means reopen the MCP "
        "client). server_modules.status "
        "distinguishes fresh/stale/unknown; unreadable_reasons and "
        "observation_errors explain unknown, including a detached result hook. "
        "Sources use stat(mtime_ns,size,file_id) then hash on change; edits/ACL "
        "denies preserving that triple can be missed. tool_registry_remediation "
        "is null when server_modules.status is fresh; otherwise "
        "{code, scope:tools, applies_when}. fence.mutation_rejects_meta marks "
        "historical counters that do not block ready. daemon_source_remediation "
        "is informational (code none) when daemon_modules are stale or unreadable; "
        "it is not reopen_mcp_client. A separate non-JSON "
        "SERVER_CODE_FRESHNESS text block and result _meta mark responses "
        "observed as stale/unknown. Read the original payload separately, "
        "not by concatenating text blocks. Reopen the MCP client to load new "
        "server code."
    )


def build_app(config: ServerConfig) -> tuple[FastMCP, Any]:
    # Numeric tool annotations are strict at FastMCP ingress: handler guards
    # cannot reject bool after Pydantic has already converted it to 0/1.
    # StrictFloat still accepts JSON integers; optional None stays read/omit.
    intended_tool_names = tool_pack_mod.tool_names(config.tool_pack)
    runtime: Any = ClientRuntime(config) if config.mode == "client" else Runtime(config)

    @asynccontextmanager
    async def lifespan(_app: FastMCP):
        if config.mode == "client":
            # Client mode holds no port; the daemon is ensured lazily on first
            # bridge call (and re-spawned on connection failure). Nothing to bind.
            yield {"runtime": runtime}
            return
        try:
            runtime.start_loopback()
        except OSError as exc:
            print(f"failed to bind loopback on 127.0.0.1:{config.port}: {exc}", file=sys.stderr, flush=True)
            raise SystemExit(2) from exc
        try:
            yield {"runtime": runtime}
        finally:
            runtime.stop_loopback()

    app = FastMCP(
        name="dayz-mcp",
        instructions=(
            "Expose DayZDiag through typed MCP tools. Flow: "
            "session_acquire_wait (or lease_acquire) -> check "
            "bridge_status.ready -> mutating verbs -> wait_for to wait -> "
            "session_release. Use dayz_test_run / dayz_test_stop for lifecycle; "
            "call dayz_test_run without a lease, then session_acquire_wait "
            "(its grant adopts the run) before bridge verbs or wait_for. "
            "Spawn: playbook_run(name=\"place_safely\", "
            "params={\"x\":..,\"z\":..}) before a new site; "
            "pos=[x, surface_query.y, z]; y=0 is ground; example "
            "type=CivilianSedan; living infected flags=3108 "
            "(ECE_PLACE_ON_SURFACE|ECE_INITAI|ECE_CREATEPHYSICS). "
            "wait_for/logs_since read script/RPT only — player chat is not "
            "there; with -adminlog the server .ADM has chat, no tool reads it. "
            "wait_for(log_matches) lookback_lines=200 includes the last N "
            "lines already on disk so a sequential action_use then wait_for "
            "does not miss a ~200ms response. action_use: held item is the "
            "ItemBase in the local player's hands (null if empty); world "
            "targets use componentIndex=-1 unless door_index targets one door "
            "of a Building (then that door's view-geometry component); "
            "classname is exact GetType()."
        ),
        lifespan=lifespan,
    )
    register_knowledge_tools(app)

    def _client_runtime() -> ClientRuntime:
        if config.mode != "client" or not isinstance(runtime, ClientRuntime):
            raise ToolError("session_tools_require_client_mode")
        return runtime

    # Observed after build_app has finished registering, which is the only
    # moment the set is complete, and cached because it cannot change afterwards.
    _registered_tool_names: set[str] = set()
    # Filled at the close of build_app, after every tool is registered. The
    # overlay is local to this FastMCP process; loopback /status does not
    # publish it.
    _tool_registry_overlay: dict[str, Any] = {}
    server_sources = _SERVER_SOURCES

    async def _with_tool_registry(payload: dict[str, Any]) -> dict[str, Any]:
        overlay = dict(payload)
        overlay.update(_tool_registry_overlay)
        modules = await asyncio.to_thread(observe_server_sources)
        overlay["server_modules"] = modules
        overlay["tool_registry_source_stale"] = source_stale(modules)
        signal = schema_signal(modules)
        overlay["tool_registry_schema_signal"] = signal
        overlay["tool_registry_remediation"] = _tool_registry_remediation_for(signal)
        overlay["daemon_source_remediation"] = _daemon_source_remediation(
            overlay.get("daemon_modules")
        )
        _annotate_mcp_fence(overlay)
        return _front_key(overlay, "ready")

    async def _bridge_tool_names() -> frozenset[str]:
        if not _registered_tool_names:
            _registered_tool_names.update(tool.name for tool in await app.list_tools())
        return frozenset(_registered_tool_names)

    @app.tool(
        description="LOW-LEVEL: prefer session_acquire_wait. Acquire or join the FIFO lease."
    )
    async def session_acquire(purpose: str) -> dict[str, Any]:
        if not isinstance(purpose, str) or not purpose.strip():
            raise ToolError("bad_purpose")
        client = _client_runtime()
        async with client.tool_lock:
            return _with_ok_next_step(
                await client.session_acquire(purpose.strip()), "session_acquire"
            )

    @app.tool(
        description="LOW-LEVEL: prefer session_acquire_wait. Wait up to 30s for this client's FIFO ticket."
    )
    async def session_wait(ticket: str, timeout_s: StrictFloat = 30.0) -> dict[str, Any]:
        if not isinstance(ticket, str) or not ticket:
            raise ToolError("bad_ticket")
        client = _client_runtime()
        async with client.tool_lock:
            return _with_ok_next_step(
                await client.session_wait(
                    ticket, _require_range(timeout_s, 0.0, 30.0, "bad_wait_timeout")
                ),
                "session_wait",
            )

    @app.tool(
        description=(
            "Preferred: use this, not session_acquire. Wait in the FIFO until "
            "this request acquires the lease or its maximum wait expires; "
            "never returns a queued result. "
            f"{_lease_renewal_contract(config.session_ttl_s)}"
        )
    )
    async def session_acquire_wait(
        purpose: str,
        max_wait_s: StrictFloat | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        if not isinstance(purpose, str) or not purpose.strip():
            raise ToolError("bad_purpose")
        if max_wait_s is None:
            validated_wait = None
        else:
            if isinstance(max_wait_s, bool):
                raise ToolError("bad_wait_timeout")
            validated_wait = _finite_float(max_wait_s, "bad_wait_timeout")
            if validated_wait <= 0.0:
                raise ToolError("bad_wait_timeout")

        async def report(
            progress: float, total: float | None, message: str | None
        ) -> None:
            if ctx is not None:
                await ctx.report_progress(progress, total, message)

        caller_stale = await _observe_caller_tool_registry_stale(
            observe_server_sources
        )
        client = _client_runtime()
        async with client.tool_lock:
            payload = await client.session_acquire_wait(
                purpose.strip(), validated_wait, report
            )
        # tools/list_changed for the revealed catalog is sent by
        # _install_catalog_change_notice once this call returns.
        return _with_ok_next_step(
            _annotate_caller_tool_registry(payload, caller_stale),
            "session_acquire_wait",
        )

    app.add_tool(
        session_acquire_wait,
        name="lease_acquire",
        description="alias of session_acquire_wait",
    )

    @app.tool(
        description="LOW-LEVEL: prefer session_acquire_wait. Cancel this client's exact queued FIFO ticket."
    )
    async def session_cancel(ticket: str) -> dict[str, Any]:
        if not isinstance(ticket, str) or not ticket:
            raise ToolError("bad_ticket")
        client = _client_runtime()
        async with client.tool_lock:
            return _with_ok_next_step(
                await client.session_cancel(ticket), "session_cancel"
            )

    @app.tool(
        description=(
            "LOW-LEVEL: prefer session_acquire_wait. "
            "Renew an active lease while exclusive work is in progress or "
            "across a pause with no other dayz-mcp calls. "
            f"{_lease_renewal_contract(config.session_ttl_s)}"
        )
    )
    async def session_heartbeat(lease_token: str) -> dict[str, Any]:
        if not isinstance(lease_token, str) or not lease_token:
            raise ToolError("bad_lease_token")
        client = _client_runtime()
        async with client.tool_lock:
            return _with_ok_next_step(
                await client.session_heartbeat(lease_token), "session_heartbeat"
            )

    @app.tool(
        description=(
            "Release this client's active lease and run bounded cleanup. "
            "Runs listed in cleanup.runs_released stay alive as ownerless RUNNING_IDLE; releasing never stops DayZ; use dayz_test_stop to stop a run."
        )
    )
    async def session_release(lease_token: str) -> dict[str, Any]:
        if not isinstance(lease_token, str) or not lease_token:
            raise ToolError("bad_lease_token")
        client = _client_runtime()
        async with client.tool_lock:
            return _with_ok_next_step(
                await client.session_release(lease_token), "session_release"
            )

    @app.tool(
        description=(
            "Read redacted daemon/queue/self coordination state, including "
            "box occupancy (managed runs; foreign DayZ processes seen by image "
            "or by a held UDP port, even without a run record; ports_in_use "
            "from the socket table; foreign_ports is DayZ-related listeners "
            "only (image or DayZ UDP range), each entry {port, dayz_relevant}; "
            "foreign_ports_all is the complete socket table minus managed runs; "
            "foreign_ports_meta summarizes count/count_all/dayz_relevant and "
            "neither list overrides occupied/available_for; "
            "and the box wait FIFO). box.available_for distinguishes new_launch "
            "from adopt of an ownerless RUNNING_IDLE this session launched or "
            "whose use_state is abandoned. A protected run names use_state and, "
            "when known, how long until it can be abandoned; blocked_on names "
            "session_acquire_wait, not the launch FIFO, only when this caller "
            "can adopt. blocked_on names the resource and next queue, or is "
            "null when neither lease nor box is busy. "
            f"{_lease_renewal_contract(config.session_ttl_s)}"
        )
    )
    async def session_status() -> dict[str, Any]:
        client = _client_runtime()
        async with client.tool_lock:
            status = await client.session_status()
            box = status.get("box")
            caller_session = str(
                getattr(getattr(client, "identity", None), "session_id", "") or ""
            )
            if isinstance(box, dict):
                box = dict(box)
                _annotate_box_foreign_ports(box)
                box["available_for"] = box_available_for(
                    box, caller_session or None
                )
                position = _box_queue_position(box, caller_session)
                box["queue_position"] = position
                if position is None and box.get("occupied") is True:
                    box["queue_offer"] = _box_queue_offer(
                        box, caller_session=caller_session
                    )
                else:
                    box["queue_offer"] = None
                status["box"] = box
            status["blocked_on"] = _session_status_blocked_on(
                status, caller_session or None
            )
            await _attach_revalidated_runs_retired_recently(client, status)
            return _with_ok_next_step(status, "session_status")

    async def report_dayz_progress(
        ctx: Context | None, stage: str, message: str | None
    ) -> None:
        if ctx is not None:
            try:
                ctx.request_context
            except ValueError:
                return
            progress = {
                "validating": 0.0,
                "queued": 1.0,
                "executing": 2.0,
                "finalizing": 3.0,
            }[stage]
            await ctx.report_progress(progress, 4.0, message or stage)

    @app.tool(
        description=(
            "Cycle: session_release (if holding) -> dayz_test_run -> "
            "session_acquire_wait. Queue and run an approved DayZ test "
            "project; lease ownership and heartbeat remain internal to the "
            "tool. Release any held session lease before calling. The run it "
            "leaves has no owner (RUNNING_IDLE). session_acquire_wait adopts "
            "it when this session launched it or use_state is abandoned, and "
            "is required before any bridge verb or wait_for "
            "players_*/entity_state, as for later mutating tools. Any other "
            "session gets box_protected and the grant is released. With several "
            "ownerless runs the grant adopts none (adopted_run error "
            "multiple_idle_runs). "
            "Reattach sequence: server -> run_id -> client(run_id). "
            "mode=client requires run_id: it reattaches only the client to a "
            "live run, preserving the server and the world state (no server "
            "reboot); mode=server|all must NOT pass run_id. "
            "mode=all plus wait_for(players_at_least, 1) after "
            "session_acquire_wait can complete without human intervention "
            "(viable night session). "
            "preflight does not relax that matrix. "
            "extra_mods entries must be a single folder name "
            "(for example '@DayZ_MCP') or an absolute path inside the "
            "project's mod_roots; relative paths with '\\' or '/' are "
            "rejected as bad_mod. A disposable probe need not be "
            "registered as a project. When the selected project is not "
            "DayZ_MCP, '@DayZ_MCP' is appended to extra_mods by default "
            "unless that folder name is already present in extra_mods, and "
            "the result reports extra_mods_defaulted. The same folder in "
            "the base_mods list this run will load (the caller's list, or "
            "the policy defaults when the caller omits it) is not copied "
            "into extra_mods. base_mods and server_mods do not satisfy "
            "that gate, so that call stays bridge_mod_missing. "
            "bridge_mod_missing also remains when the sealed request would "
            "reject the appended entry. "
            "wait_for_box_s>0 waits "
            "until session_status.box is free (FIFO, no tool_lock while "
            "sleeping). on_busy=\"queue\" waits in that FIFO (wait_for_box_s "
            "when >0, else the 600s cap) instead of failing at once; busy "
            "rejections include queue_offer, or null when waiting cannot help. "
            "A DayZ server holding a game port counts as an "
            "occupied box even without a run record, and a launch onto a "
            "port held by a process that is not ours is refused "
            "(active_run_exists; reason port_in_use_foreign names the port) "
            "by a socket-table read repeated right before the launch; the "
            "only window left is between that read and DayZ's own bind. "
            "A RUNNING run owned by another session is takeover_required "
            "unless takeover=true (evicts that registered run, then launches; "
            "names evicted_run_id). An ownerless RUNNING_IDLE that this "
            "session did not launch is run_protected until use_state is "
            "abandoned; takeover=true does not evict it. Abandoned, or the "
            "session that launched it, stays takeover_required unless "
            "takeover=true. "
            "port_scan_unknown means the daemon could not read the socket "
            "table: fix the host, waiting does not help. "
            f"0 is the immediate reject. wait_for_box_s must be <= "
            f"{BOX_WAIT_MAX_S:g}. Choose port= from "
            "session_status.box.ports_in_use. width/height are copied into "
            "the request and the worker passes -x/-y to the client exe, but "
            "DayZDiag does not honor them as the render viewport "
            "(profile/DPI win; 1280x720 measured as 846x461). Workaround: "
            "SetWindowPos host-side, then ui_reload_layout to re-measure "
            "without reboot. The call blocks for up to wait_for_box_s plus "
            "the launch, so a client whose own tool-call timeout is shorter "
            "(Antigravity CLI cuts MCP calls at 180 s) must pass a smaller "
            "wait_for_box_s and repeat the call."
        )
    )
    async def dayz_test_run(
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
        port: StrictInt = 2302,
        width: StrictInt = 1920,
        height: StrictInt = 1080,
        player_name: str = "Dev",
        server_wait_s: StrictInt = 60,
        wait_for_box_s: StrictFloat = 0.0,
        auto_remediate_steam: StrictBool = False,
        navmesh_data_server: StrictBool = False,
        takeover: StrictBool = False,
        client_start_budget_s: StrictFloat | StrictInt | None = None,
        on_busy: StrictStr = "fail",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        caller_stale = await _observe_caller_tool_registry_stale(
            observe_server_sources
        )

        def annotated(payload: dict[str, Any]) -> dict[str, Any]:
            return _annotate_caller_tool_registry(payload, caller_stale)

        # Both parses run BEFORE the box queue: a request that is already
        # invalid must not be able to take a queue slot, hold the tool lock or
        # come back as box_queue_saturated with its real defect never reported.
        budget_s = _parse_client_start_budget_s(client_start_budget_s)
        wait_s = _parse_wait_for_box_s(wait_for_box_s)
        on_busy = _parse_on_busy(on_busy)
        # Refuse before queue admission or takeover. The sealed request parser
        # also enforces this restriction for non-MCP callers and the worker.
        if navmesh_data_server and (mode != "server" or pack_only):
            raise ToolError(
                "bad_dayz_test_request:navmesh_data_server_requires_server_launch"
            )
        client = _client_runtime()
        started = time.monotonic()
        box_ticket: str | None = None
        claim_task: asyncio.Task[None] | None = None
        caller_session = str(getattr(client.identity, "session_id", "") or "") or None

        async def report(stage: str, message: str | None) -> None:
            await report_dayz_progress(ctx, stage, message)

        async def peek_box() -> dict[str, Any]:
            async with client.tool_lock:
                return _box_from_status(await client.session_status())

        try:
            if on_busy == "queue":
                peeked = await peek_box()
                cannot = _box_wait_cannot_help(
                    peeked, caller_session=caller_session, port=port
                )
                if cannot is not None:
                    failed = _failed_active_run_result(
                        project=project,
                        mode=mode,
                        box=peeked,
                        started=started,
                        caller_session=caller_session,
                        port=port,
                    )
                    if peeked.get("port_scan_known") is False:
                        failed["error_code"] = "port_scan_unknown"
                    if cannot in {"own_run", "adopt"}:
                        failed["reason"] = cannot
                    return annotated(failed)
            if on_busy == "queue" or wait_s > 0.0:
                await report("queued", "waiting for box")
                wait_budget = wait_s
                abort = None
                if on_busy == "queue":
                    wait_budget = wait_s if wait_s > 0.0 else BOX_WAIT_MAX_S
                    session_for_wait = caller_session

                    def abort(box: dict[str, Any]) -> object:
                        return _box_wait_cannot_help(
                            box, caller_session=session_for_wait, port=port
                        )

                waited = await execute_wait_for_box(
                    client, wait_budget, abort_if=abort
                )
                ticket = waited.get("ticket")
                box_ticket = ticket if isinstance(ticket, str) else None
                if not waited.get("ok"):
                    failed = _failed_active_run_result(
                        project=project,
                        mode=mode,
                        box=waited.get("box"),
                        started=started,
                        caller_session=caller_session,
                        port=port,
                    )
                    wait_error = waited.get("error")
                    if wait_error == "port_scan_unknown":
                        failed["error_code"] = "port_scan_unknown"
                    if wait_error == "box_queue_saturated":
                        failed["error_code"] = "box_queue_saturated"
                        failed["hint"] = "retry with wait_for_box_s=<n>"
                    if wait_error in {"own_run", "adopt"}:
                        failed["reason"] = wait_error
                    return annotated(failed)
                if box_ticket:
                    claim_task = asyncio.create_task(
                        _heartbeat_box_claim(client, box_ticket)
                    )

            execute_error: dayz_test_tool.DayzTestToolError | None = None
            result: dict[str, Any] | None = None
            evicted_run_id: str | None = None
            async with client.tool_lock:
                box: dict[str, Any] = {}
                try:
                    snapshot = await client.session_status()
                except Exception:
                    # Lifecycle still refuses a managed run; this peek
                    # only names takeover_required before execute.
                    snapshot = None
                if isinstance(snapshot, dict):
                    box = _box_from_status(snapshot)
                target = takeover_target_run_id(
                    box, caller_session=caller_session
                )
                if target is not None and _row_is_protected(
                    _box_run(box, target), caller_session
                ):
                    # takeover=true does not evict a protected ownerless run.
                    # The daemon would refuse the worker's adopt opaquely.
                    return annotated(
                        _failed_active_run_result(
                            project=project,
                            mode=mode,
                            box=box,
                            started=started,
                            caller_session=caller_session,
                            port=port,
                        )
                    )
                if target is not None and not takeover:
                    return annotated(
                        _failed_active_run_result(
                            project=project,
                            mode=mode,
                            box=box,
                            started=started,
                            caller_session=caller_session,
                            port=port,
                        )
                    )
                if target is not None and takeover:
                    try:
                        stop_result = await dayz_test_tool.execute_dayz_test_stop(
                            client,
                            target,
                            progress_cb=report,
                        )
                    except dayz_test_tool.DayzTestToolError as error:
                        raise ToolError(error.code) from None
                    if (
                        isinstance(stop_result, dict)
                        and stop_result.get("status") == "failed"
                    ):
                        return annotated(stop_result)
                    evicted_run_id = target
                try:
                    with _typed_dayz_test_value_errors():
                        result = await dayz_test_tool.execute_dayz_test_run(
                            client,
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
                            navmesh_data_server=navmesh_data_server,
                            port=port,
                            width=width,
                            height=height,
                            player_name=player_name,
                            server_wait_s=server_wait_s,
                            progress_cb=report,
                            auto_remediate_steam=auto_remediate_steam,
                            client_start_budget_s=budget_s,
                        )
                except dayz_test_tool.DayzTestToolError as error:
                    execute_error = error
                except ToolError:
                    raise
                except Exception as exc:
                    # The ToolError carries the exception TYPE, plus the launcher
                    # backend's bare code when it has one (ficha ae65). The message
                    # can hold host paths, so it must not cross the MCP wire; FastMCP
                    # serializes str(exc) alone. `from exc` keeps the cause in
                    # __cause__ for LOCAL diagnosis (needed to see why build:true failed), not for the wire.
                    _log_opaque_failure(client, "dayz_test_run", exc)
                    raise ToolError(_opaque_dayz_test_failure(exc)) from exc
            if execute_error is not None:
                if execute_error.code == "active_run_exists":
                    return annotated(
                        _failed_active_run_result(
                            project=project,
                            mode=mode,
                            box=await peek_box(),
                            started=started,
                            caller_session=caller_session,
                            port=port,
                        )
                    )
                raise ToolError(execute_error.code) from None
            if (
                isinstance(result, dict)
                and result.get("error_code") == "active_run_exists"
            ):
                return annotated(
                    _enrich_active_run_result(
                        result,
                        await peek_box(),
                        caller_session=caller_session,
                        port=port,
                    )
                )
            if result is None:
                raise ToolError("dayz_test_failed:RuntimeError")
            if evicted_run_id is not None:
                result["evicted_run_id"] = evicted_run_id
            return annotated(result)
        finally:
            if claim_task is not None:
                claim_task.cancel()
                try:
                    await claim_task
                except (asyncio.CancelledError, Exception):
                    pass
            if isinstance(box_ticket, str) and box_ticket:
                await _release_box_wait_ticket(client, box_ticket)

    @app.tool(
        description=(
            "Queue adoption and shutdown of one exact approved DayZ run. "
            "Stop uses forced process kill, not orderly mission teardown: "
            "RPT exit metrics (Leaked counts, Destroying game, Termination "
            "successfully completed) are not valid after this tool. status "
            "succeeded means processes are gone; exit_metrics_valid is false. "
            "For an orderly shutdown first, use dayz_test_close."
        )
    )
    async def dayz_test_stop(
        run_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        client = _client_runtime()

        async def report(stage: str, message: str | None) -> None:
            await report_dayz_progress(ctx, stage, message)

        caller_session = str(
            getattr(getattr(client, "identity", None), "session_id", "") or ""
        ) or None
        async with client.tool_lock:
            try:
                snapshot = await client.session_status()
            except Exception:
                # The daemon still refuses the adopt inside the worker.
                snapshot = None
            if isinstance(snapshot, dict):
                row = _box_run(_box_from_status(snapshot), run_id)
                if _row_is_protected(row, caller_session):
                    notice = protection_fields(row)
                    failed: dict[str, Any] = {
                        "status": "failed",
                        "run_id": run_id,
                        "error_code": "run_protected",
                        "hint": notice["hint"],
                        "use_state": notice["use_state"],
                        "use_reason": notice["use_reason"],
                    }
                    if "retry_after_s" in notice:
                        failed["retry_after_s"] = notice["retry_after_s"]
                    return failed
            try:
                with _typed_dayz_test_value_errors():
                    return await dayz_test_tool.execute_dayz_test_stop(
                        client, run_id, progress_cb=report
                    )
            except dayz_test_tool.DayzTestToolError as error:
                raise ToolError(error.code) from None
            except ToolError:
                raise
            except Exception as exc:
                # The ToolError carries the exception TYPE, plus the launcher
                # backend's bare code when it has one (ficha ae65). The message
                # can hold host paths, so it must not cross the MCP wire; FastMCP
                # serializes str(exc) alone. `from exc` keeps the cause in
                # __cause__ for LOCAL diagnosis (needed to see why build:true failed), not for the wire.
                _log_opaque_failure(client, "dayz_test_stop", exc)
                raise ToolError(_opaque_dayz_test_failure(exc)) from exc

    @app.tool(
        description=(
            f"{LEASE_TOOL_LINE} For the session that owns the run. When the "
            "run has a live client and a live server, close the client first, "
            "wait for each connected player's `[Logout]: Player … finished` "
            "line (at most 30s, also bounded by graceful_timeout_s), then "
            "close the server. If that server close fails, the result is not "
            "graceful, stop_required is true, and the original error is kept. "
            "A server-only run or a client-only reattach "
            "closes as before. Graceful only when every launched role wrote "
            "a new termination line and the run was retired as run_reaped. "
            "When stop_required is true, release the lease and call "
            "dayz_test_stop."
        )
    )
    async def dayz_test_close(
        run_id: StrictStr,
        graceful_timeout_s: StrictFloat = 45,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        client = _client_runtime()

        async def report(stage: str, message: str | None) -> None:
            await report_dayz_progress(ctx, stage, message)

        async with client.tool_lock:
            try:
                with _typed_dayz_test_value_errors():
                    return await dayz_test_tool.execute_dayz_test_close(
                        client,
                        run_id,
                        graceful_timeout_s=graceful_timeout_s,
                        progress_cb=report,
                    )
            except dayz_test_tool.DayzTestToolError as error:
                raise ToolError(error.code) from None
            except ToolError:
                raise
            except Exception as exc:
                _log_opaque_failure(client, "dayz_test_close", exc)
                raise ToolError(_opaque_dayz_test_failure(exc)) from exc

    @app.tool(description="Read the authoritative server-side player state.")
    async def query_player_state(timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        async with runtime.tool_lock:
            return await runtime.call_bridge("query_player_state", {}, "server", _timeout(timeout_s))

    @app.tool(description="Read the authoritative state of every connected player.")
    async def query_all_players(timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        async with runtime.tool_lock:
            return await runtime.call_bridge("query_all_players", {}, "server", _timeout(timeout_s))

    # F2.1: marker state lives per MCP session (this process), so a caller can
    # poll without threading the marker through every call. Passing `marker`
    # explicitly still wins, which is what makes the tool replayable.
    # Keyed by run_id (or "__all__" when run_id is None) so cursors do not
    # cross between runs.
    log_marker_state: dict[str, str] = {}

    @app.tool(
        description=(
            "Read RPT/script log lines appended since a marker, from the "
            "active run's _server and _client profiles. Without a marker, "
            "reads the tail of the current launch -- each file is capped at "
            "its last 256 KiB -- never a historic dump. No lease. "
            "Pass back the marker this tool returns unchanged: encoded JSON "
            "string or the decoded object {path:[offset,size,identity]}. The "
            "marker advances only to the end of the lines RETURNED, never to "
            "EOF: without a marker, max_lines=1 marks one line INTO the launch "
            "tail and a later read from it replays the whole boot. To mark "
            "'now', read with a large max_lines until a call returns 0 new "
            "lines and keep that marker, or use wait_for(log_matches), whose "
            "window is measured from EOF. The mod probe writes to "
            "script_<date>.log, not to the .RPT: a positive control searched "
            "only in the RPT reads as zero."
        )
    )
    async def logs_since(
        marker: str | dict[str, Any] | None = None,
        max_lines: StrictInt = 200,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Drain RPT/script logs since a previous marker.

        ``marker`` is the exact value returned by a previous call. Accepted
        input: ``None`` (session-stored marker, or current launch if none),
        the encoded JSON string, or the decoded dict
        ``{path: [offset, size, identity]}``. The response ``marker`` stays
        the encoded JSON string so existing consumers keep parsing it.
        """
        if (
            not isinstance(max_lines, int)
            or isinstance(max_lines, bool)
            or not 1 <= max_lines <= 2000
        ):
            raise ToolError(
                _bad_args("max_lines", max_lines, "be an int from 1 to 2000")
            )
        client = _client_runtime()
        marker_key = run_id if run_id is not None else "__all__"
        source = (
            log_marker_state.get(marker_key, "")
            if marker is None
            else _coerce_logs_since_marker(marker)
        )
        try:
            markers = log_tail.decode_marker(source)
        except log_tail.LogTailError:
            raise ToolError("bad_marker") from None

        status = await client.lifecycle_status()
        runs = [item for item in (status.get("runs") or []) if isinstance(item, dict)]
        if run_id is not None:
            runs = [item for item in runs if item.get("run_id") == run_id]
        candidates = sorted(
            {str(item.get("profiles")) for item in runs if item.get("profiles")}
        )
        if not candidates:
            raise ToolError("no_active_run")
        allowed = [
            item for item in candidates if log_tail.is_allowed_profiles_dir(item)
        ]
        if not allowed:
            raise ToolError("bad_profiles")
        profiles = _sibling_profile_dirs(allowed)
        start_epoch = _run_start_epoch(runs)

        files: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        remaining = max_lines
        updated = dict(markers)
        # Only logs from this launch: DayZ writes the RPT and the script log
        # CONCURRENTLY, and a profiles dir also holds months of older files.
        for profiles_dir in profiles:
            for log_path in _current_launch_logs(profiles_dir, start_epoch):
                if remaining <= 0:
                    break
                try:
                    result = log_tail.read_since(
                        log_path, markers.get(log_path), max_lines=remaining
                    )
                except log_tail.LogTailError as exc:
                    errors.append({"path": log_path, "error": str(exc)})
                    continue
                # The marker advances only over the lines handed over, so a cap
                # withholds lines for the next call instead of skipping them.
                updated[log_path] = result["marker"]
                remaining -= len(result["lines"])
                if not result["lines"] and not result["rotated"]:
                    continue
                files.append(
                    {
                        "path": result["path"],
                        "lines": result["lines"],
                        "rotated": result["rotated"],
                        "truncated": result["truncated"],
                    }
                )

        encoded = log_tail.encode_marker(updated)
        log_marker_state[marker_key] = encoded
        response: dict[str, Any] = {"ok": 1, "files": files, "marker": encoded}
        if errors:
            response["errors"] = errors
        return response

    @app.tool(
        description=(
            f"{LEASE_TOOL_LINE} Spawn a DayZ object through the existing "
            "world_spawn bridge command. rotation is an RF_* CreateObjectEx "
            "flag integer, not an angle; 0 uses the bridge default RF_DEFAULT. "
            f"{WORLD_SPAWN_FLAGS_LINE} "
            "Does not attach wheels, battery, or spark plug; for a usable "
            "vehicle follow with vehicle_prepare_fixture. "
            "Once no player is within the mission's CleanupAvoidance, a "
            "spawned object lives only as long as its economy lifetime: "
            "measured on 1.29, a CivilianSedan (types.xml lifetime 3 s, "
            "CleanupAvoidance 100 m) vanished 3-27 s after the player went "
            "~114 m away and survived at ~44 m. lifetime_s (seconds, above 0 "
            f"and at most {int(WORLD_SPAWN_LIFETIME_MAX_S)}) overrides the "
            "economy lifetime for fixtures: the bridge calls SetLifetimeMax "
            "then SetLifetime on the new entity, and the reply's lifetime "
            "gives remaining_s (GetLifetime) and max_s (GetLifetimeMax). A "
            "type that is not an EntityAI has no economy lifetime to set: "
            "with lifetime_s it is deleted and the call fails with "
            "lifetime_unsupported."
        )
    )
    async def world_spawn(
        type: str,
        pos: list[StrictFloat],
        flags: StrictInt = 0,
        rotation: StrictInt = 0,
        lifetime_s: StrictFloat | None = None,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        parsed_flags = int(flags)
        if not is_allowed_spawn_flags(parsed_flags):
            raise ToolError("bad_flags")
        args = {
            "type": type,
            "pos": _require_vec3(pos, "pos"),
            "flags": parsed_flags,
            "rotation": int(rotation),
        }
        # The flag travels with the value: the bridge reads an absent key as 0
        # or false, so only lifetime_s_set asks for the override
        # (fb-20260930-065425-8779). Without lifetime_s neither travels and the
        # economy lifetime is left alone (fb-20260930-080543-bd28).
        if lifetime_s is not None:
            lifetime_error = _bad_args(
                "lifetime_s",
                lifetime_s,
                "be a finite number of seconds greater than 0 and at most "
                f"{int(WORLD_SPAWN_LIFETIME_MAX_S)}",
            )
            lifetime_value = _finite_float(lifetime_s, lifetime_error)
            if lifetime_value <= 0.0 or lifetime_value > WORLD_SPAWN_LIFETIME_MAX_S:
                raise ToolError(lifetime_error)
            args["lifetime_s"] = lifetime_value
            args["lifetime_s_set"] = True
        async with runtime.tool_lock:
            return await runtime.call_bridge("world_spawn", args, "server", _timeout(timeout_s))

    @app.tool(
        description=(
            "Requires a lease (session_acquire_wait). Delete an object "
            "previously returned by world_spawn.object_id. object_id is "
            "session-scoped and does not survive the run — keep the spawn id "
            "in this session; there is no pos+type delete. Spawn with "
            "ECE_NOPERSISTENCY_WORLD so the object is not saved into a later "
            "run. Deleting a seated "
            "transport after vehicle_get_in_client needs care (sanctioned "
            "teardown; it ejects). Returns ok with "
            "deleted=0 (success, nothing removed) when the id is unknown or "
            "already gone — check the `deleted` field to know whether anything "
            "was actually removed."
        )
    )
    async def object_delete(object_id: StrictInt, timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        if not isinstance(object_id, int) or isinstance(object_id, bool):
            raise ToolError(
                _bad_args("object_id", object_id, "be a positive int")
            )
        parsed_id = object_id
        if parsed_id <= 0:
            raise ToolError(
                _bad_args("object_id", object_id, "be a positive int")
            )
        args = {"object_id": parsed_id}
        async with runtime.tool_lock:
            return await runtime.call_bridge("object_delete", args, "server", _timeout(timeout_s))

    @app.tool(
        description=(
            "Requires a lease (session_acquire_wait). Send a vanilla "
            "notification popup. show_time is the display duration in seconds. "
            "uid empty (default) broadcasts to every connected player; a "
            "non-empty uid targets that identity. Returns error=no_players "
            "when the server reports zero connected players. timeout_s is "
            "the total budget for the player check plus send; a failed "
            "player query still attempts the notification."
        )
    )
    async def notify_players(
        show_time: StrictFloat,
        title: str,
        detail: str = "",
        icon: str = "",
        uid: str = "",
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        show_time_error = _bad_args(
            "show_time", show_time, "be a finite number greater than 0"
        )
        show_time_value = _finite_float(show_time, show_time_error)
        if show_time_value <= 0.0:
            raise ToolError(show_time_error)
        if not isinstance(title, str) or title == "":
            raise ToolError(
                _bad_args("title", title, "be a non-empty string")
            )
        if not isinstance(detail, str):
            raise ToolError(_bad_args("detail", detail, "be a string"))
        if not isinstance(icon, str):
            raise ToolError(_bad_args("icon", icon, "be a string"))
        if not isinstance(uid, str):
            raise ToolError(_bad_args("uid", uid, "be a string"))
        args: dict[str, Any] = {
            "show_time": show_time_value,
            "title": title,
            "detail": detail,
            "icon": icon,
        }
        if uid != "":
            args["uid"] = uid
        budget = _timeout(timeout_s)
        deadline = time.monotonic() + budget

        def remaining() -> float:
            left = deadline - time.monotonic()
            if left <= 0.0:
                raise ToolError("timeout waiting for notify_players")
            return left

        async with runtime.tool_lock:
            # Preflight is advisory and may use at most half the budget so a
            # timeout/exception still leaves time to send. Only a confirmed
            # empty players list short-circuits; missing key or query failure
            # falls through to notify (the pre-check contract).
            players: object = None
            try:
                players_result = await runtime.call_bridge(
                    "query_all_players",
                    {},
                    "server",
                    min(remaining(), budget / 2.0),
                )
            except ToolError:
                players_result = None
            if isinstance(players_result, dict):
                players = players_result.get("players")
            if isinstance(players, list) and len(players) == 0:
                # Bridge SendNotification... still sets sent=true with nobody
                # listening (MCPBridge.c DispatchNotifyPlayers).
                return {"ok": False, "sent": 0, "error": "no_players"}
            return await runtime.call_bridge(
                "notify_players", args, "server", remaining()
            )

    @app.tool(
        description=(
            "Requires a lease (session_acquire_wait). Seat the first "
            "player in the driver seat of a vehicle near pos. seated=1 "
            "confirms the command was accepted, not the final seated state. "
            "engine_set and vehicle_control require client-side ownership; "
            "establish it with vehicle_get_in_client."
        )
    )
    async def vehicle_enter(pos: list[StrictFloat], timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        args = {"pos": _require_vec3(pos, "pos")}
        async with runtime.tool_lock:
            return await runtime.call_bridge("vehicle_enter", args, "server", _timeout(timeout_s))

    @app.tool(
        description=(
            "Raycast through the server bridge using from/to world positions. "
            "Public arg is from (alias of from_pos)."
        )
    )
    async def scene_raycast(
        from_pos: list[StrictFloat],
        to: list[StrictFloat],
        method: str = "rvproxy",
        ignore: str = "",
        radius: StrictFloat = 0.05,
        intersect: str = "view",
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        radius_error = _bad_args(
            "radius", radius, "be a non-negative finite number"
        )
        args = {
            "from": _require_vec3(from_pos, "from"),
            "to": _require_vec3(to, "to"),
            "method": method,
            "ignore": ignore,
            "radius": _finite_float(radius, radius_error),
            "intersect": intersect,
        }
        if args["radius"] < 0.0:
            raise ToolError(radius_error)
        if method not in {"rvproxy", "bullet"}:
            raise ToolError(
                _bad_args("method", method, "be one of 'rvproxy' or 'bullet'")
            )
        if ignore not in {"", "player"}:
            raise ToolError(
                _bad_args("ignore", ignore, "be empty or 'player'")
            )
        if intersect not in {"view", "fire", "geom", "ifire"}:
            raise ToolError(
                _bad_args(
                    "intersect",
                    intersect,
                    "be one of 'view', 'fire', 'geom', or 'ifire'",
                )
            )
        async with runtime.tool_lock:
            return await runtime.call_bridge("scene_raycast", args, "server", _timeout(timeout_s))

    @app.tool(description=(
        "Read server-side telemetry. Closed mode set object_at|fixture_jsonl "
        "(schema enum; other strings fail validation before the handler). "
        "object_at consumes type (exact GetType match), pos ([x,y,z] as given), "
        "radius (0 < radius <= 50 metres); path and max_lines are ignored. "
        "Returns {ok,telemetry:{mode,found,type,class_name,pos,orientation,"
        "direction,velocity,health01,declared_slots,attachment_count,"
        "attachment_items,cargo_count,cargo_items,items,items_total,"
        "items_truncated}}; item arrays are classnames with a 16-entry cap per "
        "array, counts uncapped, immediate inventory only; cars additionally "
        "populate engine_on_server,speedo,wheel_count,fuel_fraction (defaults on "
        "non-cars are not measurements); zero matches is a successful return with "
        "found:false. Multiple exact-type matches raise ToolError("
        "ambiguous_fixture); that is an MCP tool error, not a returned dict. "
        "object_at reads only those instrumented fields: "
        "it does not reach arbitrary script members, mod getters, or synchronized "
        "variables of a modded entity, and does not check client-side replication. "
        "fixture_jsonl consumes path and max_lines; type/pos/radius are ignored. "
        "path must be a direct child of $mission:dayz_mcp/ (no subdirectories or "
        "'..'); max_lines=0 means 64, positive values are capped at 64, negative "
        "is invalid. Reads from the BEGINNING, not the tail; each line must "
        "deserialize as {fixture_id:nonempty string,value:finite float,seq:int}; "
        "seq must differ from the unset sentinel. Returns {ok,telemetry:{mode,"
        "path,found,line_count_read,last_valid:{fixture_id,value,seq},"
        "parse_error}} on success; last_valid is the last valid row within that "
        "prefix. Missing file raises ToolError(fixture_not_found); an empty/"
        "invalid/over-4096-character line raises ToolError(parse_error). Both "
        "runtimes convert a falsy bridge ok into that ToolError before the tool "
        "returns. The shared telemetry object can include default "
        "fields from the other mode. timeout_s bounds the server bridge request."
    ))
    async def telemetry_read(
        mode: TelemetryReadMode,
        type: str = "",
        pos: list[StrictFloat] | None = None,
        radius: StrictFloat = 0.0,
        path: str = "",
        max_lines: StrictInt = 0,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        # Name the field that is wrong. A bare "bad_args" makes the caller guess
        # between mode, type and radius, which is the whole cost of the error;
        # the codes below follow the same shape the rest of the surface uses
        # (bad_throttle, bad_steer, bad_hold_ttl_s...). Field names are not host
        # content, so they may cross the wire; paths may not.
        if mode == "object_at":
            args = {"mode": mode, "type": type, "pos": _require_vec3(pos, "telemetry_pos"), "radius": float(radius)}
            if args["type"] == "":
                raise ToolError("bad_type")
            if args["radius"] <= 0.0 or not math.isfinite(args["radius"]):
                raise ToolError("bad_radius")
        elif mode == "fixture_jsonl":
            args = {"mode": mode, "path": path, "max_lines": int(max_lines)}
        else:
            raise ToolError("bad_mode")
        async with runtime.tool_lock:
            return await runtime.call_bridge("telemetry_read", args, "server", _timeout(timeout_s))

    @app.tool(description="Diagnose whether a normal get-in would be available on a vehicle and which gate blocks it. Pass a concrete `component` (a seat/action component index, not the default -1): with the default the bridge returns a partial diagnostic (`partial=true`, `available=false`, `first_block=\"no_component\"`) that only lists per-seat occupancy/through/area and never reports reachability or a usable `available`.")
    async def query_get_in_condition(
        pos: list[StrictFloat],
        component: StrictInt = -1,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        args = {"pos": _require_vec3(pos, "pos"), "component": int(component)}
        async with runtime.tool_lock:
            return await runtime.call_bridge("query_get_in_condition", args, "server", _timeout(timeout_s))

    # General fixture prep for any CarScript (no classname allowlist).
    @app.tool(
        description=(
            "Requires a lease (session_acquire_wait). Prepare a vehicle "
            "fixture near pos (OnDebugSpawn when needed). Any CarScript "
            "classname; non-vehicles return fixture_not_vehicle."
        )
    )
    async def vehicle_prepare_fixture(
        type: str,
        pos: list[StrictFloat],
        radius: StrictFloat = 100.0,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(type, str) or type == "":
            raise ToolError(_bad_args("type", type, "be a non-empty string"))
        radius_error = _bad_args(
            "radius", radius, "be a finite number greater than 0"
        )
        radius_value = _finite_float(radius, radius_error)
        if radius_value <= 0.0:
            raise ToolError(radius_error)
        args = {
            "mode": "object_at",
            "type": type,
            "pos": _require_vec3(pos, "pos"),
            "radius": radius_value,
        }
        async with runtime.tool_lock:
            return await runtime.call_bridge(
                "vehicle_prepare_fixture", args, "server", _timeout(timeout_s)
            )

    # Pure read of terrain under (x, z).
    @app.tool(description="Query terrain surface Y, type, and normal at world (x, z).")
    async def surface_query(
        x: StrictFloat,
        z: StrictFloat,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        args = {
            "x": _finite_float(x, _bad_args("x", x, "be a finite number")),
            "z": _finite_float(z, _bad_args("z", z, "be a finite number")),
        }
        async with runtime.tool_lock:
            return await runtime.call_bridge("surface_query", args, "server", _timeout(timeout_s))

    # Mutating teleport of a connected player.
    async def _surface_clearance(pos: list[float], timeout: float) -> dict[str, Any] | None:
        """Covered-column check for a surface landing; None means clear to land.

        Mirrors the certified site gate probe (g0_site_gate.canopy_gate): surface Y
        at (x, z), then a geom ray from y+30 down to y-5 whose first hit must sit
        within 0.05 m of the surface. A roof, canopy or water plane over the landing
        point refuses the teleport instead of burying the player
        (fb-20260824-115220-1bc1). Probe failures refuse too: unverified is not clear.
        """
        x = float(pos[0])
        y_req = float(pos[1])
        z = float(pos[2])
        surface = await runtime.call_bridge(
            "surface_query", {"x": x, "z": z}, "server", timeout
        )
        surface_y = surface.get("y") if isinstance(surface, dict) else None
        if not (
            isinstance(surface, dict)
            and surface.get("ok")
            and isinstance(surface_y, (int, float))
        ):
            return {
                "ok": False,
                "error": "clearance_unverified",
                "detail": "surface_query failed for the target column",
            }
        surface_y = float(surface_y)
        if y_req != 0.0 and y_req > surface_y + CLEARANCE_LANDING_BAND_M:
            # Explicit above-surface target (roof, platform): not a surface
            # landing, so the covered-column predicate does not apply.
            return None
        ray = await runtime.call_bridge(
            "scene_raycast",
            {
                "from": [x, surface_y + CLEARANCE_PROBE_UP_M, z],
                "to": [x, surface_y - CLEARANCE_PROBE_DOWN_M, z],
                "method": "rvproxy",
                # The probe must ignore players: the column often contains the
                # very player being teleported, and the ray hits their head at
                # dy ~1.67 m (measured, multi-agent run 2026-08-24).
                "ignore": "player",
                "radius": 0.05,
                "intersect": "geom",
            },
            "server",
            timeout,
        )
        raycast: dict[str, Any] = {}
        if isinstance(ray, dict) and isinstance(ray.get("raycast"), dict):
            raycast = ray["raycast"]
        hit_pos = raycast.get("pos")
        if not (raycast.get("hit") and isinstance(hit_pos, list) and len(hit_pos) == 3):
            return {
                "ok": False,
                "error": "clearance_unverified",
                "detail": "clearance ray found no ground at the target column",
                "surface_y": surface_y,
            }
        if abs(float(hit_pos[1]) - surface_y) > CLEARANCE_TOLERANCE_M:
            return {
                "ok": False,
                "error": "clearance_blocked",
                "surface_y": surface_y,
                "hit_y": float(hit_pos[1]),
                "hit_object_type": raycast.get("object_type") or "",
                "hint": (
                    "the vertical column is covered (roof/canopy/water); pass "
                    "skip_clearance_check=true only for intentional covered teleports"
                ),
            }
        return None

    @app.tool(
        description=(
            f"{LEASE_TOOL_LINE} Teleport a "
            "connected player to pos. y==0 snaps to SurfaceY (vanilla "
            "script-console contract). uid empty (default) targets the first "
            "human; a non-empty uid selects by PlayerIdentity.GetPlainId(). "
            "Surface landings are refused with clearance_blocked when the "
            "target column is covered (a roof, canopy or water plane would "
            "bury the player); skip_clearance_check=true bypasses the probe "
            "for intentional covered/indoor teleports. Refuses "
            "occupant_client_seated when the player is a client-owned seated "
            "occupant (vehicle_get_in_client): teleporting that transport "
            "desyncs client and server. One car per run — there is no get-out "
            "after vehicle_get_in_client; tear down with object_delete of the "
            "in-session world_spawn object_id (that id does not survive the "
            "run; deleting a seated transport needs care). The Python "
            "occupant_client_seated precheck calls client vehicle_telemetry "
            "only when that peer is probing; without a client peer it is "
            "skipped (fail open to on-foot teleport) and Enforce still refuses "
            "a seated occupant."
        )
    )
    async def player_teleport(
        pos: list[StrictFloat],
        uid: str = "",
        skip_clearance_check: bool = False,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(uid, str):
            raise ToolError(_bad_args("uid", uid, "be a string"))
        if not isinstance(skip_clearance_check, bool):
            raise ToolError(
                _bad_args("skip_clearance_check", skip_clearance_check, "be a bool")
            )
        args: dict[str, Any] = {"pos": _require_vec3(pos, "pos")}
        if uid != "":
            args["uid"] = uid
        async with runtime.tool_lock:
            if await _runtime_client_peer_probeable(runtime):
                telemetry = await runtime.call_bridge(
                    "vehicle_telemetry", {}, "client", _timeout(timeout_s)
                )
                if occupant_client_seated(telemetry):
                    return {
                        "ok": False,
                        "error": "occupant_client_seated",
                        "hint": (
                            "player_teleport of a client-owned seated occupant "
                            "desyncs client and server. One car per run: there is "
                            "no get-out after vehicle_get_in_client; tear down "
                            "with object_delete of the in-session fixture "
                            "(object_id does not survive the run)."
                        ),
                    }
            if not skip_clearance_check:
                refusal = await _surface_clearance(args["pos"], _timeout(timeout_s))
                if refusal is not None:
                    return refusal
            return await runtime.call_bridge("player_teleport", args, "server", _timeout(timeout_s))

    # Read or write entity animation phase.
    @app.tool(
        description=(
            f"{LEASE_TOOL_LINE} "
            "Read or set an entity animation phase. Target by object_id (as "
            "returned by world_spawn; position-independent, reaches a "
            "client-authoritative fixture whose server replica sits at spawn) "
            "or by classname near pos. phase is a unitless value; the write "
            "uses SetAnimationPhaseNow. The returned phase is the same-tick "
            "re-read and can still read the old value, so confirm a write "
            "with a later read. A read writes nothing. A written phase holds: "
            "measured on 1.29, a car door written with phase=1 read back 1.0 "
            "through vehicle_door at +0.6, +1.2 and +5 s. For a car door, use "
            "vehicle_door. For building doors, read object_doors, which gives "
            "each door's state by index. object_anim reads a door's animation "
            "source only under its exact name (measured on 1.29: Land_Shed_M1 "
            "door 0, source Doors1, read 1.0 open and 0.0 closed), and an "
            "unknown source name also reads 0, so a 0 alone does not show that "
            "a door is closed. Omit phase to read."
        )
    )
    async def object_anim(
        source: str,
        type: str = "",
        pos: list[StrictFloat] | None = None,
        phase: StrictFloat | None = None,
        object_id: StrictInt = 0,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(source, str) or source == "":
            raise ToolError(_bad_args("source", source, "be a non-empty string"))
        args: dict[str, Any] = {"source": source}
        args.update(_object_target_args(type, pos, object_id))
        # The flag travels with the value: the bridge reads an absent key as 0
        # or false, so only phase_set says a write was asked for
        # (fb-20260930-065425-8779). A read sends neither.
        if phase is not None:
            args["phase"] = _finite_float(
                phase, _bad_args("phase", phase, "be a finite unitless number")
            )
            args["phase_set"] = True
        async with runtime.tool_lock:
            return await runtime.call_bridge("object_anim", args, "server", _timeout(timeout_s))

    # One car door through the server half of the vanilla door action
    # (MCPBridge.c DispatchVehicleDoor). The lease follows the command name, so
    # mode=read is leased too, as object_anim's read is.
    @app.tool(
        description=(
            f"{LEASE_TOOL_LINE} Open, close or read one car door on the server "
            "the way the vanilla door action does. mode=open or close calls "
            "ForceUpdateLightsStart and SetAnimationPhase(source, 1 or 0), the "
            "server half of ActionCarDoorsOutside, and ForceUpdateLightsEnd one "
            "second later; it does not use SetAnimationPhaseNow, which "
            "object_anim uses. mode=read changes nothing. source is the car's "
            "door animation source, the name object_anim takes (CivilianSedan: "
            "DoorsDriver, DoorsCoDriver, DoorsCargo1, DoorsCargo2, DoorsHood, "
            "DoorsTrunk). Target by object_id (world_spawn) or by classname "
            "near pos. The door part must be attached: door_missing names the "
            "empty slot of a crew door (vehicle_prepare_fixture or "
            "inventory_attach fills it), and door_not_found means no attached "
            "door part maps to source. The reply gives phase "
            "(GetAnimationPhase) and state (GetCarDoorsState: open above 0.5, "
            "closed, or missing) read before the call, the door part's type, "
            "slot and world pos, is_authority_owner and the server "
            "tick_time_s. The door takes 0.5 s to move and phase_reply is read "
            "in the same tick, so it still shows the old value after a write: "
            "confirm with mode=read at +1 s or later. It checks neither the "
            "player's reach nor an obstructed door, unlike the player action. "
            "Errors: bad_args, object_not_found, ambiguous_object, "
            "object_id_unknown, object_id_stale, not_a_car, door_missing, "
            "door_not_found."
        )
    )
    async def vehicle_door(
        source: StrictStr,
        mode: VehicleDoorMode,
        type: StrictStr = "",
        pos: list[StrictFloat] | None = None,
        object_id: StrictInt = 0,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(source, str) or source == "":
            raise ToolError(_bad_args("source", source, "be a non-empty string"))
        if mode not in ("read", "open", "close"):
            raise ToolError(
                _bad_args("mode", mode, "be one of 'read', 'open' or 'close'")
            )
        args: dict[str, Any] = {"source": source, "mode": mode}
        args.update(_object_target_args(type, pos, object_id))
        async with runtime.tool_lock:
            return await runtime.call_bridge("vehicle_door", args, "server", _timeout(timeout_s))

    # Probe verb: drive an infected server-side via its AI input controller.
    @app.tool(
        description=(
            f"{LEASE_TOOL_LINE} "
            "Impose heading/speed on an infected by classname near pos, through its "
            "AI input controller. heading is DEGREES (0=north), speed 0-5 "
            "(0 idle, 1 walk, 2 run, 3 sprint). Pass mode='release' to hand control "
            "back to the vanilla AI. The override may need reapplying each tick; if "
            "the infected does not move, that is the finding."
        )
    )
    async def infected_drive(
        type: str,
        pos: list[StrictFloat],
        heading: StrictFloat | None = None,
        speed: StrictFloat | None = None,
        mode: str | None = None,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(type, str) or type == "":
            raise ToolError(_bad_args("type", type, "be a non-empty string"))
        args: dict[str, Any] = {"type": type, "pos": _require_vec3(pos, "pos")}
        if mode is not None:
            if mode != "release":
                raise ToolError(_bad_args("mode", mode, "be 'release' or omitted"))
            if heading is not None:
                raise ToolError(
                    _bad_args("heading", heading, "be omitted when mode is 'release'")
                )
            if speed is not None:
                raise ToolError(
                    _bad_args("speed", speed, "be omitted when mode is 'release'")
                )
            args["mode"] = mode
        else:
            if heading is None:
                raise ToolError(
                    _bad_args("heading", heading, "be provided when mode is omitted")
                )
            if speed is None:
                raise ToolError(
                    _bad_args("speed", speed, "be provided when mode is omitted")
                )
            # Each value travels with its presence flag (fb-20260930-065425-8779).
            args["heading"] = _finite_float(
                heading, _bad_args("heading", heading, "be a finite number of degrees")
            )
            args["heading_set"] = True
            args["speed"] = _finite_float(
                speed, _bad_args("speed", speed, "be a finite number")
            )
            args["speed_set"] = True
        async with runtime.tool_lock:
            return await runtime.call_bridge("infected_drive", args, "server", _timeout(timeout_s))

    # Spawn into a player's inventory.
    @app.tool(
        description=(
            "Requires a lease (session_acquire_wait). Spawn classname "
            "into a player's inventory via CreateInInventory. dest is 'hands' "
            "or 'inventory'. uid empty (default) targets the first human; a "
            "non-empty uid selects by PlayerIdentity.GetPlainId()."
        )
    )
    async def inventory_give(
        classname: str,
        dest: str = "hands",
        uid: str = "",
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(classname, str) or classname == "":
            raise ToolError(
                _bad_args("classname", classname, "be a non-empty string")
            )
        if dest not in {"hands", "inventory"}:
            raise ToolError(
                _bad_args("dest", dest, "be one of 'hands' or 'inventory'")
            )
        if not isinstance(uid, str):
            raise ToolError(_bad_args("uid", uid, "be a string"))
        args: dict[str, Any] = {"classname": classname, "dest": dest}
        if uid != "":
            args["uid"] = uid
        async with runtime.tool_lock:
            return await runtime.call_bridge("inventory_give", args, "server", _timeout(timeout_s))

    @app.tool(
        description=(
            f"{LEASE_TOOL_LINE} "
            "Put a reachable item into a player's hands via "
            "PredictiveTakeEntityToHands. object_id is the world_spawn id "
            "(inventory_give does not return one). The take is predictive and "
            "asynchronous: accepted=true with confirmed=false means the server "
            "accepted the request, not that the item is in hands yet. Confirm "
            "with weapon_state. uid empty (default) targets the first human. "
            "A confirmation is weapon_state.object_id equal to the requested "
            "object_id; the same type and visible state do not distinguish "
            "two items. No OS input and no client focus."
        )
    )
    async def hands_take(
        object_id: StrictInt,
        uid: str = "",
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(object_id, int) or isinstance(object_id, bool):
            raise ToolError(_bad_args("object_id", object_id, "be a positive int"))
        if object_id <= 0:
            raise ToolError(_bad_args("object_id", object_id, "be a positive int"))
        if not isinstance(uid, str):
            raise ToolError(_bad_args("uid", uid, "be a string"))
        args: dict[str, Any] = {"object_id": object_id}
        if uid != "":
            args["uid"] = uid
        async with runtime.tool_lock:
            return await runtime.call_bridge("hands_take", args, "server", _timeout(timeout_s))

    @app.tool(
        description=(
            "Read the weapon in a player's hands on the server: type, current "
            "muzzle, per-muzzle chamber and magazine, jam, fire mode, the "
            "server shot counter, and object_id. The counter increments in "
            "Weapon_Base.EEFired on the server only, after super, and is not "
            "a replicated variable. Empty hands or a non-weapon is a completed "
            "read: ok=true, found=false, error=no_weapon_in_hands, and type "
            "is set when a non-weapon is held. object_id is the world_spawn "
            "id of the object actually held, or 0 when that object was not "
            "spawned by world_spawn. uid empty (default) targets the first "
            "human. Confirm a hands_take by comparing object_id with the "
            "requested id."
        )
    )
    async def weapon_state(
        uid: str = "",
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(uid, str):
            raise ToolError(_bad_args("uid", uid, "be a string"))
        args: dict[str, Any] = {}
        if uid != "":
            args["uid"] = uid
        async with runtime.tool_lock:
            return await runtime.call_bridge("weapon_state", args, "server", _timeout(timeout_s))

    @app.tool(
        description=(
            f"{LEASE_TOOL_LINE} Raise or lower the local player's weapon with "
            "HumanInputController.OverrideRaise. No OS input and no focus. "
            "raised=true holds OverrideRaise(ENABLED, true) until hold_ttl_s "
            "elapses; the bridge then applies DISABLED itself. raised=false "
            "releases at once. In multiplayer the request is also sent to the "
            "server, which applies the same raise or release to its own copy "
            "of the player (a raise lasts the same hold_ttl_s and ends early "
            "on death or when the weapon leaves the hands) and prints its "
            "verdict in the script "
            "log as [DayZ_MCP] weapon_raise server accepted=0|1 raised=0|1 "
            "reason=...; a full input channel is error input_busy and changes "
            "neither side. The result is the client's read-back after one "
            "simulation tick: raised is PlayerBase.IsRaised(), input_raised is "
            "HumanInputController.IsWeaponRaised(), plus expires_at and "
            "hold_ttl_s. Nothing stays ENABLED without an expiry."
        )
    )
    async def weapon_raise(
        raised: StrictBool,
        hold_ttl_s: StrictFloat = WEAPON_RAISE_DEFAULT_TTL_S,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(raised, bool):
            raise ToolError(_bad_args("raised", raised, "be a bool"))
        requirement = (
            f"be a finite number in (0, {WEAPON_RAISE_MAX_TTL_S}]"
        )
        ttl = _finite_float(hold_ttl_s, _bad_args("hold_ttl_s", hold_ttl_s, requirement))
        if ttl <= 0.0 or ttl > WEAPON_RAISE_MAX_TTL_S:
            raise ToolError(_bad_args("hold_ttl_s", hold_ttl_s, requirement))
        args = {"raised": raised, "hold_ttl_s": ttl}
        async with runtime.tool_lock:
            return await runtime.call_bridge(
                "weapon_raise", args, "client", _timeout(timeout_s)
            )

    @app.tool(
        description=(
            f"{LEASE_TOOL_LINE} Apply one ONE_FRAME aim change on the local "
            "player through OverrideAimChangeX and OverrideAimChangeY. No OS "
            "input and no focus. The override protos name no unit. The result "
            "is the read-back after one simulation tick: aim_lr_before, "
            "aim_lr_after, aim_ud_before and aim_ud_after are "
            "GetBaseAimingAngleLR and GetBaseAimingAngleUD (those protos name "
            "no unit), and aim_change_0, aim_change_1 and aim_change_2 are the "
            "three components of GetAimChange (the proto calls that vector "
            "radians and does not name the components). The bridge forces "
            "DISABLED on both axes after that tick."
        )
    )
    async def weapon_aim(
        dx: StrictFloat,
        dy: StrictFloat,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        bound = f"be a finite number in [-{WEAPON_AIM_ABS_MAX}, {WEAPON_AIM_ABS_MAX}]"
        dx_value = _finite_float(dx, _bad_args("dx", dx, bound))
        dy_value = _finite_float(dy, _bad_args("dy", dy, bound))
        if dx_value < -WEAPON_AIM_ABS_MAX or dx_value > WEAPON_AIM_ABS_MAX:
            raise ToolError(_bad_args("dx", dx, bound))
        if dy_value < -WEAPON_AIM_ABS_MAX or dy_value > WEAPON_AIM_ABS_MAX:
            raise ToolError(_bad_args("dy", dy, bound))
        args = {"dx": dx_value, "dy": dy_value}
        async with runtime.tool_lock:
            return await runtime.call_bridge(
                "weapon_aim", args, "client", _timeout(timeout_s)
            )

    @app.tool(
        description=(
            f"{LEASE_TOOL_LINE} Fire the weapon in the local player's hands "
            "once, through WeaponManager.Fire, after vanilla's own checks. "
            "No OS input and no focus. In multiplayer the request is sent to "
            "the server, which runs the same checks on its own copy of the "
            "player (the weapon must be raised there too, so call weapon_raise "
            "first), fires from its CommandHandler and prints its verdict in "
            "the script log as [DayZ_MCP] weapon_fire server accepted=0|1 "
            "reason=...; a full input channel is the completed refusal "
            "input_busy, and then the client does not fire either. "
            "accepted=true means the client called Fire, not that the server "
            "counted a shot; confirm the shot with "
            "weapon_state. A completed refusal is ok=true, accepted=false, "
            "and error names the reason. A missing weapon, or a dead, "
            "unconscious, restrained or seated player at dispatch, is "
            "ok=false. Those four are checked again immediately before "
            "Fire; a failure there is a completed refusal."
        )
    )
    async def weapon_fire(
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        async with runtime.tool_lock:
            return await runtime.call_bridge(
                "weapon_fire", {}, "client", _timeout(timeout_s)
            )

    @app.tool(
        description=(
            f"{LEASE_TOOL_LINE} Enter or leave the local player's weapon sights. "
            "mode ironsights leaves the optic with SwitchOptics(optic, false) "
            "then calls SetIronsights(true). mode optics calls "
            "SetIronsights(false) then SwitchOptics(optic, true). mode none "
            "calls ExitSights(). No OS input and no "
            "focus. Optics is refused when the weapon has no attached optic; "
            "ironsights is refused when the weapon cannot enter them. The "
            "result reads back ironsights (IsInIronsights) and optics "
            "(IsInOptics) after one simulation tick. The engine can leave the "
            "sights on the next frame. This is not an input override."
        )
    )
    async def weapon_sights(
        mode: WeaponSightsMode,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if mode not in ("ironsights", "optics", "none"):
            raise ToolError(
                _bad_args("mode", mode, "be one of 'ironsights', 'optics' or 'none'")
            )
        async with runtime.tool_lock:
            return await runtime.call_bridge(
                "weapon_sights", {"mode": mode}, "client", _timeout(timeout_s)
            )

    @app.tool(
        description=(
            f"{LEASE_TOOL_LINE} "
            "Create classname in a world EntityAI selected by object_id or by "
            "unique type+pos. dest='attachment' requires a non-empty slot and "
            "uses CreateAttachmentEx; dest='cargo' requires slot to be omitted. "
            "Success returns the destination receipt plus an immediate inventory "
            "snapshot; object_inspect(want=['inventory']) can re-read it."
        )
    )
    async def inventory_attach(
        classname: StrictStr,
        dest: InventoryAttachDest,
        type: StrictStr = "",
        pos: list[StrictFloat] | None = None,
        object_id: StrictInt = 0,
        slot: StrictStr = "",
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(classname, str) or classname == "":
            raise ToolError(
                _bad_args("classname", classname, "be a non-empty string")
            )
        if not isinstance(dest, str) or dest not in {"attachment", "cargo"}:
            raise ToolError(
                _bad_args("dest", dest, "be one of 'attachment' or 'cargo'")
            )
        if not isinstance(slot, str):
            raise ToolError(_bad_args("slot", slot, "be a string"))
        if dest == "attachment" and slot == "":
            raise ToolError(
                _bad_args("slot", slot, "be non-empty when dest is 'attachment'")
            )
        if dest == "cargo" and slot != "":
            raise ToolError(
                _bad_args("slot", slot, "be omitted when dest is 'cargo'")
            )
        args: dict[str, Any] = {"classname": classname, "dest": dest}
        if dest == "attachment":
            args["slot"] = slot
        args.update(_object_target_args(type, pos, object_id))
        async with runtime.tool_lock:
            return await runtime.call_bridge(
                "inventory_attach", args, "server", _timeout(timeout_s)
            )

    # Memory points + bounding_center. Missing points are exists:false, ok:true.
    @app.tool(
        description=(
            "Inspect an object: memory points (exists+pos) and optional "
            "bounding_center. Target by object_id (from world_spawn) or by "
            "classname near pos. Absent memory points return exists:false "
            "with ok:true."
        )
    )
    async def object_inspect(
        want: list[str],
        type: str = "",
        pos: list[StrictFloat] | None = None,
        object_id: StrictInt = 0,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(want, list) or len(want) == 0:
            raise ToolError(
                _bad_args("want", want, "be a non-empty list of non-empty strings")
            )
        if any(not isinstance(item, str) or item == "" for item in want):
            raise ToolError(
                _bad_args("want", want, "be a non-empty list of non-empty strings")
            )
        if type == "" and object_id == 0:
            raise ToolError(
                "bad_args: missing target parameters type and object_id; "
                "use object_id+want or type+pos+want"
            )
        if type != "" and not _inspect_type_is_valid(type):
            raise ToolError(
                _bad_args("type", type, "be a DayZ classname without whitespace")
            )
        args: dict[str, Any] = {"want": list(want)}
        args.update(_object_target_args(type, pos, object_id))
        async with runtime.tool_lock:
            return await runtime.call_bridge("object_inspect", args, "server", _timeout(timeout_s))

    # Building door predicates. object_anim's phase read is a different API.
    @app.tool(
        description=(
            "Read Building door state. Target by object_id (from world_spawn) "
            "or by classname near pos, the same lookup object_inspect uses. "
            "Returns door_count and, per door index, open, opening, "
            "opening_ajar, opened, ajar, closing, closed and locked. These are "
            "the engine door predicates. Each door also carries pos, the world "
            "position Building.GetDoorSoundPos returns as a 3-float array, so "
            "a caller knows where to stand. object_anim is unchanged: it reads "
            "a door's animation source only under its exact name, and an "
            "unknown source name also reads 0, so a 0 from object_anim does not "
            "show that a door is closed. A target that is not a Building "
            "returns not_a_building. A door count outside 0..64 returns "
            "door_count_unsupported with that count and an empty doors list. "
            "Whether a raycast passes through an open door leaf is out of scope."
        )
    )
    async def object_doors(
        type: StrictStr = "",
        pos: list[StrictFloat] | None = None,
        object_id: StrictInt = 0,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if type == "" and object_id == 0:
            raise ToolError(
                "bad_args: missing target parameters type and object_id; "
                "use object_id or type+pos"
            )
        args = _object_target_args(type, pos, object_id)
        async with runtime.tool_lock:
            return await runtime.call_bridge("object_doors", args, "server", _timeout(timeout_s))

    @app.tool(
        description=(
            "Query world entities around pos within radius (0 < r <= 200). "
            "Returns the nearest entries up to limit (default 32, max 128) as "
            "{type, classname, has_cargo, pos, distance} sorted by distance "
            "ascending, plus count_total before the cut. No classname filter; raw "
            "nearby objects. has_cargo is cargo CAPACITY, not occupancy: the bridge "
            "reads GetInventory().GetCargo() != null on the row's own object, so an "
            "EMPTY container reads true, and an object that is not an EntityAI, or "
            "an EntityAI with no cargo grid, reads false. It answers 'could this "
            "hold items', which is the predicate a container check needs; it never "
            "says whether anything is inside. A row whose only cargo lives in a "
            "proxy reports false. has_cargo is null, never false, when the bridge "
            "did not state it: a client older than the has_cargo build omits the "
            "field, and null means the bridge did not say. "
            "Absent entities travel as []. Rows are trustworthy only with a "
            "player streaming the area: far from every player the engine "
            "answers 0-or-cap with no error signal, so the result carries "
            "nearest_player_m and reliability (player_in_bubble | "
            "remote_unverified). When the players probe succeeds with an empty "
            "list, reason is no_player_connected. pos.y is used as given: "
            "nothing snaps it to the surface (player_teleport y==0 does, this "
            "tool does not), so pass pos=[x, surface_query.y, z]. A query "
            "centred underground returns count_total 0 with reliability "
            "player_in_bubble and no error: the only tell is nearest_player_m "
            "reading as the vertical distance to the player standing on the "
            "spot."
        )
    )
    async def entities_query(
        pos: list[StrictFloat],
        radius: StrictFloat,
        limit: StrictInt = 32,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        radius_error = _bad_args(
            "radius", radius, "be a finite number greater than 0 and at most 200"
        )
        radius_value = _finite_float(radius, radius_error)
        if radius_value <= 0.0 or radius_value > 200.0:
            raise ToolError(radius_error)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 128:
            raise ToolError(_bad_args("limit", limit, "be an int from 1 to 128"))
        args = {
            "pos": _require_vec3(pos, "pos"),
            "radius": radius_value,
            "limit": int(limit),
        }
        async with runtime.tool_lock:
            result = await runtime.call_bridge(
                "entities_query", args, "server", _timeout(timeout_s)
            )
            players = await runtime.call_bridge(
                "query_all_players", {}, "server", _timeout(timeout_s)
            )
        return _annotate_entities_reliability(
            _normalize_entities_cargo(result), players, args["pos"]
        )

    @app.tool(
        description=(
            "Requires a lease (session_acquire_wait). Set server world "
            "date/time and optionally the time multiplier. This is a server "
            "world-effect: Python confirms the applied echo "
            "(date_applied / multiplier_applied). Clock overflow uses divmod: "
            "minute>=60 (60, 120, 60.0, 120.0) carries into hour, then into "
            "the calendar day (hour stays 0–23; minute=120 is hour+2). "
            "applied is the normalized clock; applied_echo keeps the raw "
            "GetDate echo; clock_normalized is true when they differ. ok is 0 "
            "when the date does not match, the echo is an incomplete calendar, "
            "or the multiplier echo mismatches; multiplier_applied=null "
            "means the echo had no numeric time_multiplier (World has "
            "SetTimeMultiplier and no GetTimeMultiplier; MCPApplied does "
            "not echo one) and warnings includes multiplier_unconfirmed — "
            "that is not confirmation it was applied. Visual/particle "
            "confirmation is in_game_required, not a wire guarantee."
        )
    )
    async def world_time_set(
        year: StrictInt,
        month: StrictInt,
        day: StrictInt,
        hour: StrictInt,
        minute: StrictInt,
        time_multiplier: StrictFloat | None = None,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        month_value = int(month)
        day_value = int(day)
        hour_value = int(hour)
        minute_value = int(minute)
        year_value = int(year)
        if year_value < 1970 or year_value > 2100:
            raise ToolError("bad_year")
        if month_value < 1 or month_value > 12:
            raise ToolError("bad_month")
        if day_value < 1 or day_value > 31:
            raise ToolError("bad_day")
        if hour_value < 0 or hour_value > 23:
            raise ToolError("bad_hour")
        if minute_value < 0 or minute_value > 59:
            raise ToolError("bad_minute")
        args: dict[str, Any] = {
            "year": year_value,
            "month": month_value,
            "day": day_value,
            "hour": hour_value,
            "minute": minute_value,
        }
        if time_multiplier is not None:
            multiplier = _finite_float(time_multiplier, "bad_time_multiplier")
            if multiplier != -1.0 and (multiplier < 0.0 or multiplier > 64.0):
                raise ToolError("bad_time_multiplier")
            # The flag travels with the value (fb-20260930-065425-8779).
            args["time_multiplier"] = multiplier
            args["time_multiplier_set"] = True
        async with runtime.tool_lock:
            result = await runtime.call_bridge(
                "world_time_set", args, "server", _timeout(timeout_s)
            )

        requested_date = {
            "year": year_value,
            "month": month_value,
            "day": day_value,
            "hour": hour_value,
            "minute": minute_value,
        }
        applied = result.get("applied")
        applied_echo = dict(applied) if isinstance(applied, dict) else None
        if isinstance(applied, dict):
            applied = _normalize_applied_clock(applied)
        date_applied = isinstance(applied, dict) and all(
            applied.get(field) == value for field, value in requested_date.items()
        )
        multiplier_applied: bool | None = None
        if time_multiplier is not None and isinstance(applied, dict):
            applied_multiplier = applied.get("time_multiplier")
            if isinstance(applied_multiplier, (int, float)) and not isinstance(
                applied_multiplier, bool
            ):
                multiplier_applied = float(applied_multiplier) == multiplier

        response = dict(result)
        if isinstance(applied, dict) and applied_echo is not None:
            response["applied"] = applied
            response["applied_echo"] = applied_echo
            response["clock_normalized"] = _clock_was_normalized(
                applied_echo, applied
            )
        response["date_applied"] = date_applied
        response["multiplier_applied"] = multiplier_applied
        warnings: list[str] = []
        present_clock = (
            [field for field in requested_date if field in applied]
            if isinstance(applied, dict)
            else []
        )
        incomplete_date = bool(present_clock) and len(present_clock) < len(
            requested_date
        )
        comparable_date = len(present_clock) == len(requested_date)
        if (incomplete_date or (comparable_date and not date_applied)):
            response["ok"] = 0
            warnings.append("date_not_applied")
        if time_multiplier is not None and multiplier_applied is False:
            response["ok"] = 0
            warnings.append("multiplier_mismatch")
        elif time_multiplier is not None and multiplier_applied is None:
            warnings.append("multiplier_unconfirmed")
        if warnings:
            response["warnings"] = warnings
        return response

    @app.tool(
        description=(
            "Requires a lease (session_acquire_wait). Set server weather "
            "overcast, rain, or fog forecast values. time is the transition "
            "duration in seconds; min_duration is the minimum hold duration "
            "in seconds passed to the weather phenomenon Set method. "
            "This is a server world-effect: the wire confirms the requested "
            "fields were accepted. Sky/particle look is in_game_required."
        )
    )
    async def world_weather_set(
        overcast: StrictFloat | None = None,
        rain: StrictFloat | None = None,
        fog: StrictFloat | None = None,
        time: StrictFloat = 0.0,
        min_duration: StrictFloat = 0.0,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {}
        overcast_value = _optional_finite_float(
            overcast,
            _bad_args("overcast", overcast, "be a finite number from 0 to 1"),
        )
        rain_value = _optional_finite_float(
            rain, _bad_args("rain", rain, "be a finite number from 0 to 1")
        )
        fog_value = _optional_finite_float(
            fog, _bad_args("fog", fog, "be a finite number from 0 to 1")
        )
        # Only the given levels travel, each with its presence flag. The bridge
        # sets a level only when its flag is true: an absent key arrives there
        # as 0, which used to zero the levels not given (fb-20260930-065425-8779).
        if overcast_value is not None:
            args["overcast"] = _require_range(overcast_value, 0.0, 1.0, "bad_overcast")
            args["overcast_set"] = True
        if rain_value is not None:
            args["rain"] = _require_range(rain_value, 0.0, 1.0, "bad_rain")
            args["rain_set"] = True
        if fog_value is not None:
            args["fog"] = _require_range(fog_value, 0.0, 1.0, "bad_fog")
            args["fog_set"] = True
        if not args:
            raise ToolError("no_weather_fields")
        args["time"] = _require_range(
            time,
            0.0,
            float("inf"),
            _bad_args("time", time, "be a non-negative finite number of seconds"),
        )
        args["min_duration"] = _require_range(
            min_duration,
            0.0,
            float("inf"),
            _bad_args(
                "min_duration",
                min_duration,
                "be a non-negative finite number of seconds",
            ),
        )
        async with runtime.tool_lock:
            return await runtime.call_bridge("world_weather_set", args, "server", _timeout(timeout_s))

    @app.tool(description=(
        "Requires a lease (session_acquire_wait). Set the client camera "
        "through the existing camera_set bridge command. "
        "cam_mode: orient (cam_pos + cam_orientation), lookat (cam_pos + look_at), "
        "matrix (cam_matrix of 12), free (cam_pos, then look_at or cam_orientation). "
        "cam_orientation is [yaw, pitch, roll] in degrees. fov is the FOV angle "
        "in radians; 0 leaves the current/default FOV unchanged. "
        "cam_mode look_at is accepted as an alias of lookat and is sent as lookat. "
        "Settle is wall-time only (no Camera.IsInterpolationComplete / GetCurrentFOV). "
        "Use restore_gameplay to leave the scripted camera; camera_get.view is the observer."
    ))
    async def camera_set(
        cam_mode: str = "orient",
        cam_pos: list[StrictFloat] | None = None,
        cam_orientation: list[StrictFloat] | None = None,
        look_at: list[StrictFloat] | None = None,
        cam_matrix: list[StrictFloat] | None = None,
        fov: StrictFloat = 0.0,
        settle_ticks: StrictInt = 3,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        # The wire value is `lookat`, but the vector argument sitting
        # right beside it is `look_at`, so a caller naturally spells the mode
        # with the underscore and gets a bare `bad_args`. Normalize rather than
        # widen the wire: the branches below forward cam_mode verbatim, so
        # accepting the alias without rewriting it would make this tool admit
        # exactly what MCPClientBridge.c:1741 then rejects in-game.
        if cam_mode == "look_at":
            cam_mode = "lookat"
        if cam_mode == "orient":
            args = {"cam_mode": cam_mode, "cam_pos": _require_vec3(cam_pos, "cam_pos"), "cam_orientation": _require_vec3(cam_orientation, "cam_orientation")}
        elif cam_mode == "lookat":
            args = {"cam_mode": cam_mode, "cam_pos": _require_vec3(cam_pos, "cam_pos"), "look_at": _require_vec3(look_at, "look_at")}
        elif cam_mode == "matrix":
            args = {
                "cam_mode": cam_mode,
                "cam_matrix": _require_float_list(cam_matrix, 12, "cam_matrix"),
            }
        elif cam_mode == "free":
            args = {"cam_mode": cam_mode, "cam_pos": _require_vec3(cam_pos, "cam_pos")}
            if look_at is not None:
                args["look_at"] = _require_vec3(look_at, "look_at")
            else:
                args["cam_orientation"] = _require_vec3(cam_orientation, "cam_orientation")
        else:
            raise ToolError(
                "bad_args: cam_mode must be one of orient, lookat, matrix, free "
                "(look_at is accepted as an alias of lookat)"
            )
        fov_error = _bad_args(
            "fov", fov, "be a non-negative finite number of radians"
        )
        fov_value = _finite_float(fov, fov_error)
        if fov_value < 0.0:
            raise ToolError(fov_error)
        args["fov"] = fov_value
        args["settle_ticks"] = int(settle_ticks)
        async with runtime.tool_lock:
            return await runtime.call_bridge("camera_set", args, "client", _timeout(timeout_s))

    @app.tool(description=(
        "Read the client camera through camera_get. Observable trichotomy "
        "(decision 6): camera.view='player' + ok + viewport_moved=0 is the "
        "liberated player camera (DayZPlayer.GetCurrentCameraTransform); "
        "camera.view='scripted' + ok + viewport_moved=1 is a mounted "
        "scripted camera; camera.view='vehicle' + ok is a readable seated "
        "cabin view (ResolveLiveSeatedTransport + GetCurrentCameraTransform, "
        "never GetCommand_Vehicle as presence); ok=0 plus a named "
        "camera.error is illegible "
        "(client_not_in_game, camera_unavailable_*, "
        "camera_illegible_player_transform). Absence of m_ActiveCam is not "
        "liberation. Live-client recipe that does not poll (L5/L6, ficha "
        "5cca): this tool is one client command; do not wait_for a poll "
        "heartbeat to decide the camera. A live process that is not polling "
        "is classified by the dayz_test_run replace-gate (pid alive + "
        "readable peer + record age past the start budget + not "
        "_peer_is_live → client_not_polling), not by camera_get."
    ))
    async def camera_get(cam_mode: str = "get", timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        args = {"cam_mode": cam_mode} if cam_mode else {}
        async with runtime.tool_lock:
            return await runtime.call_bridge("camera_get", args, "client", _timeout(timeout_s))

    @app.tool(description=(
        f"{LEASE_TOOL_LINE} Restore local player simulation, input, HUD, and "
        "release the camera. camera_set has no off mode. The bridge closes this "
        "verdict without checking anything, so the tool re-reads the camera with "
        "camera_get and fails closed: ok when camera.view='player' or a readable "
        "camera.view='vehicle' while still seated "
        "(camera_released: true), camera_still_active when view='scripted' or "
        "viewport_moved, restore_unverified when the probe is illegible "
        "(ok=0). Do not treat a missing scripted camera as liberation. "
        "Controls, HUD, simulation and render are NOT verified -- no reader for "
        "controls, HUD or simulation exists on the wire, and the render is not "
        "checked here -- and the ok names them in not_verified. Check the render "
        "with capture_screenshot with frames of at least 2: its warnings carry "
        "render_frozen_signal when max_adjacent_delta is below "
        f"{mcp_capture.RENDER_FROZEN_DELTA_EPS:g}, even with distinct_frames above 1, "
        "because a render frozen on the last scripted frame can still change in a "
        "small part of the frame. timeout_s bounds "
        "each of the two bridge calls."
    ))
    async def restore_gameplay(timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        async with runtime.tool_lock:
            timeout = _timeout(timeout_s)
            result = await runtime.call_bridge("restore_gameplay", {}, "client", timeout)
            try:
                probe = await runtime.call_bridge(
                    RESTORE_CAMERA_PROBE_CMD, {"cam_mode": "get"}, "client", timeout
                )
            except ToolError as exc:
                raise ToolError(
                    "restore_unverified: restore_gameplay ran, but the camera_get "
                    f"probe that confirms it failed ({exc}); the view may still be "
                    "on the debug camera. Check camera_get or capture_screenshot."
                ) from None
            verdict, detail = _restore_camera_verdict(probe)
            if verdict == "still_active":
                suffix = f" ({detail})" if detail else ""
                raise ToolError(
                    "camera_still_active: restore_gameplay ran, but camera_get "
                    f"still reports a scripted camera mounted{suffix}; the view "
                    "has not returned to the player. Check camera_get or capture_screenshot."
                )
            if verdict != "released":
                raise ToolError(
                    f"restore_unverified: restore_gameplay ran, but {detail}. "
                    "Check camera_get or capture_screenshot."
                )
            confirmed = dict(result)
            confirmed["camera_released"] = True
            confirmed["not_verified"] = list(RESTORE_NOT_VERIFIED)
            return confirmed

    @app.tool(description=(
        "Read GetInputByName for a UAInput name, and what the selected "
        "alternative has bound: binding_count, keys (index, key code, device), "
        "locked and conflict_count. exists is true only when GetInputByName "
        "returns non-null and input.ID() >= 0 (uainput.c:25, the input index). "
        "In 1.29 an unknown name returns a shared placeholder whose index is -1, "
        "so exists is false and binding_count, locked, conflict_count and keys stay at their defaults. probe "
        "is raw engine values published on the non-null path, including that "
        "placeholder, so a caller can see why exists is false: input_id, "
        "name_hash, name_string_hash, by_id_found, by_id_same_hash, "
        "in_active_inputs. probe is left unset when GetInputByName returns "
        "null, so none of its fields are present. "
        "binding_count, locked, conflict_count and keys are "
        "meaningful only when exists is true. The call does not change the "
        "selected alternative. Pressing the input is out of scope; use key_press "
        "for an OnKeyPress handler. The client must already be in game "
        "(client_not_in_game). input_api_unavailable means GetUApi returned null. "
        "input_bind_unreadable means a negative count or more than 16 keys on "
        "the selected alternative."
    ))
    async def input_describe(
        name: StrictStr,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not is_printable_input_name(name):
            raise ToolError(
                _bad_args(
                    "name",
                    name,
                    f"be 1..{INPUT_NAME_MAX_CHARS} printable ASCII characters (codes 32..126)",
                )
            )
        async with runtime.tool_lock:
            return await runtime.call_bridge(
                "input_describe", {"name": name}, "client", _timeout(timeout_s)
            )

    @app.tool(description=(
        f"{LEASE_TOOL_LINE} Deliver one non-negative DIK code to "
        "Mission.OnKeyPress on the client (ESC is dik=1). This is a mission "
        "callback, not OS input, key-up, hold, or respawn."
    ))
    async def key_press(
        dik: StrictInt,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(dik, int) or isinstance(dik, bool) or dik < 0:
            raise ToolError(_bad_args("dik", dik, "be a non-negative int"))
        async with runtime.tool_lock:
            return await runtime.call_bridge(
                "key_press", {"dik": dik}, "client", _timeout(timeout_s)
            )

    @app.tool(description=(
        f"{LEASE_TOOL_LINE} Deliver a DIK key code to the client's script key "
        "handlers, or report why a named UAInput cannot be driven. No OS input "
        "and no focus. kind=key sends dik (0..255, ESC is 1) through "
        "entry=game (default), DayZGame.OnKeyPress/OnKeyRelease, the CGame key "
        "callbacks, which set the Ctrl and Alt flags, feed the keyboard handler "
        "a menu registered, then call the mission; or through "
        "entry=mission, Mission.OnKeyPress/OnKeyRelease only, what key_press "
        "calls. phase=click (default) presses, releases on the next client "
        "tick and answers after the release. phase=hold presses, releases "
        f"after hold_s (required, 0 < hold_s <= {INPUT_TRIGGER_HOLD_MAX_S:g}) "
        "and answers after the release; the tool waits at least hold_s + "
        f"{INPUT_TRIGGER_HOLD_SLACK_S:g} s. phase=press presses and answers at "
        "once with release_due_s; the key stays pressed until phase=release "
        "with the same dik and entry, its ttl_s (required, 0 < ttl_s <= "
        f"{INPUT_TRIGGER_PRESS_MAX_TTL_S:g}), restore_gameplay, a change or "
        "death of the local player, or the bridge shutting down. One key at a "
        "time: a click, hold or press while one is held, or a release while a "
        "click or hold runs, is input_trigger_busy. "
        "The answer is input_trigger: kind, entry, phase, dik, delivered_press, "
        "delivered_release, press_tick and release_tick (client ticks), "
        "release_due_s and tick_time_s (client GetTickTime seconds), menu_open "
        "(a scripted menu was open) and released_by (phase, ttl, restore, "
        "player_changed, which also covers a death, or shutdown). "
        "delivered_press and delivered_release mean the bridge called the "
        "handler, not that anything consumed the key: the handlers return "
        "nothing, so check the effect you expect. entry=game never presses F4, "
        "LMENU or RMENU, whatever this tool or the physical keyboard holds "
        "(would_request_exit): in developer builds DayZGame.OnKeyPress requests "
        "exit on F4 while its left Alt flag is set, a physical Alt sets that "
        "flag and nothing can read it, and an Alt held through entry=game "
        "would let a physical F4 exit. entry=mission delivers these keys to the "
        "mission handlers only. phase=release of a key "
        "this tool does not hold is not_held; observed=released_by=... is "
        "added only when the most recent release this tool made was of that "
        "same entry and key (one release is remembered, not a history per "
        "key). A click or hold that something else released first is "
        "aborted, with observed=released_by=.... kind=input resolves name "
        f"(1..{INPUT_NAME_MAX_CHARS} printable ASCII) with GetInputByName and "
        "never drives it: in DayZ 1.29 no script setter feeds UAInput.Local*, "
        "so mods that poll UAInput (LocalPress, LocalValue and the like, "
        "Community Framework input bindings included) cannot be driven by "
        "this tool. It answers input_unknown (null, or ID() < 0), "
        "input_locked (IsLocked), input_denied (UAWalkRunForced and "
        "UATempRaiseWeapon, whose force vanilla manages) and otherwise always "
        "input_not_drivable; reason=no_local_setter; observed=exists=1 "
        "locked=0 in_active_inputs=0|1. Errors: bad_args, client_not_in_game, "
        "no_mission, input_trigger_busy, not_held, would_request_exit, aborted, "
        "input_api_unavailable, input_unknown, input_locked, input_denied, "
        "input_not_drivable."
    ))
    async def input_trigger(
        kind: InputTriggerKind,
        dik: StrictInt | None = None,
        name: StrictStr | None = None,
        entry: InputTriggerEntry | None = None,
        phase: InputTriggerPhase = "click",
        hold_s: StrictFloat | None = None,
        ttl_s: StrictFloat | None = None,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        args, timeout = _input_trigger_request(
            kind, dik, name, entry, phase, hold_s, ttl_s, timeout_s
        )
        async with runtime.tool_lock:
            return await runtime.call_bridge("input_trigger", args, "client", timeout)

    @app.tool(description=(
        f"{LEASE_TOOL_LINE} Request a random local-player respawn through the "
        "same character-selection, death-screen, menu, and mission teardown sequence as "
        "vanilla InGameMenu.GameRespawn. ok/requested means the request was "
        "issued; observe player state separately for completion."
    ))
    async def player_respawn(
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        async with runtime.tool_lock:
            return await runtime.call_bridge(
                "player_respawn", {}, "client", _timeout(timeout_s)
            )

    @app.tool(description=(
        "Capture a screenshot from the DayZDiag window. Returns inline JPEG ImageContent fit to the "
        "client's MAX_MCP_OUTPUT_TOKENS budget (default 25000 -> ~600px wide; raise that client env var for bigger inline frames: 50000 -> ~860px/2x px, 75000 -> ~1070px/3x, 100000 -> ~native; max_tokens spends LESS than the cap, above-cap is clamped). Use scale='full' to spend a raised inline budget on resolution (the default scale='small' is a hard 512px cap). crop ('center', 'center:0.4', or normalized 'l,t,r,b') zooms on the subject; "
        "for optical zoom set a narrow fov in radians via camera_set first. fmt='webp' is ~15% smaller (opt-in; Claude Code has known webp MIME bugs, JPEG stays default). "
        "The result is ALWAYS two blocks: the image, then a JSON text block with the surface map (crop_space, window_surface, client_surface, effective_surface, frame_sha256, frame_stale, frame_stale_detail, fullres_path). "
        "crop_space='client' (default) normalizes crop over the rendered viewport (the space ui_tree rects use) and fails closed with frame_client_rect_unverified; 'window' is the legacy whole-window bitmap. save_fullres=True also writes the "
        "native-resolution frame to disk and reports its path as fullres_path — read that file for "
        "fine detail, bypassing the inline token budget. Capture never steals OS focus "
        "(PrintWindow, then CopyFromScreen; no SetForegroundWindow) because focus theft "
        "has killed the live client (ficha 8f76). "
        "session_locked means the Windows session is locked: both window-grab backends need the interactive desktop, retrying does not help until the session is unlocked, so unattended runs must keep it unlocked. "
        "dayz_test_run waits up to 30 s for an unlocked non-black host desktop before launching a client (session_locked / desktop_all_black / desktop_probe_timeout / desktop_probe_failed) so a capture tandem does not burn runs only to return frame_client_all_black. Non-Windows desktop_probe_unsupported does not block. "
        "An unfocused DayZDiag client renders at about 20 fps, so client-side timing depends on which window owns the foreground. "
        "Without window focus, the frame can be frozen: frame_stale (bool | null) declares it. true means these "
        "pixels repeat the previous capture of the same window, false that the render advanced, and null that no comparison was possible (first capture, an "
        "unidentifiable window, a record over a different surface or geometry, or an unusable state store). frame_stale_detail carries the evidence: "
        "previous_sha256, age_s, repeat_count, key_kind and state_backend, plus the intra-call frames, distinct_frames and max_adjacent_delta, which need no "
        "stored state and are therefore there on the very first capture. A repeated frame is a fact about pixels, not an error: a paused sim, an open menu "
        "and a still scene all produce it legitimately. "
        "With a live simulation and a position that advances, frames>=2 (default frames=4) "
        f"with max_adjacent_delta below {mcp_capture.RENDER_FROZEN_DELTA_EPS:g} is a frozen-render signal, not a "
        "process hang, even when distinct_frames is above 1 (a render frozen on its last frame measured "
        "up to 4.7e-04, a live one from 0.004); warnings then carry render_frozen_signal. It is a warning, "
        "not a verdict: a genuinely still live view can fall under it too. With frames=1 those metrics are non-discriminating (always "
        "distinct_frames=1 and max_adjacent_delta=0; no adjacent pairs) and are not a freeze "
        "signal. "
        "With two DayZ clients, capture targets the live run's client through cmdline_match/client_pid. "
        "window_surface and client_surface rects are PHYSICAL pixels (DPI-aware): a host helper that never calls "
        "SetProcessDpiAwareness sees virtualized coordinates instead (at 150%: 1920 -> 1280), so a 'client_rect == "
        "requested' gate can pass in the wrong space without the window having moved; check it against this surface map."
    ))
    async def capture_screenshot(
        scale: str = "small",
        max_tokens: StrictInt = mcp_capture.DEFAULT_MAX_TOKENS,
        frames: StrictInt = mcp_capture.DEFAULT_FRAME_COUNT,
        process_name: str = "DayZDiag_x64",
        fmt: str = mcp_capture.DEFAULT_FORMAT,
        quality: StrictInt = mcp_capture.DEFAULT_QUALITY,
        crop: str = "",
        crop_space: str = mcp_capture.DEFAULT_CROP_SPACE,
        save_fullres: bool = False,
        save_dir: str = "",
    ):
        runtime.touch()
        # Fail-closed: never ask the encoder for more than the client's MAX_MCP_OUTPUT_TOKENS ceiling
        # (an over-budget result is rejected outright -> lost capture). resolve_request_budget clamps a
        # caller-requested max_tokens to the safe cap; <=0 means "use the safe cap".
        eff_max_tokens = mcp_capture.resolve_request_budget(max_tokens)
        # D05: with two DayZ windows open, picking by process name alone can
        # photograph the wrong world and certify a subject that was never there.
        # A run records only its _server profiles dir, so derive the _client
        # sibling: the client is launched with that path on its command line,
        # which is what makes cmdline_match identify the window. client_pid is
        # only a fallback -- the recorded pid comes from the launcher
        # (process_lifecycle.py:1365-1372) and a DayZDiag window can be owned by
        # a different process (mcp_capture.py:330, mcp-grab.ps1:28-30). With no
        # live run both stay empty and capture behaves exactly as before.
        cmdline_match = ""
        client_pid = 0
        status_fn = getattr(runtime, "lifecycle_status", None)
        if status_fn is not None:
            try:
                status = status_fn()
                if asyncio.iscoroutine(status):
                    status = await status
            except Exception:
                status = None      # fail-open: a capture beats no capture
            if isinstance(status, dict):
                runs = [
                    item
                    for item in (status.get("runs") or [])
                    if isinstance(item, dict)
                    and item.get("state") in _CAPTURE_LIVE_RUN_STATES
                ]
                for profiles_dir in _profile_dirs_from_runs(runs):
                    if Path(profiles_dir).parent.name.casefold() == "_client":
                        cmdline_match = profiles_dir
                        break
                for run in runs:
                    for proc in run.get("processes") or []:
                        if not isinstance(proc, dict):
                            continue
                        if str(proc.get("role", "")).casefold() != "client":
                            continue
                        pid = proc.get("pid")
                        if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
                            client_pid = pid
                            break
                    if client_pid:
                        break
        async with runtime.tool_lock:
            result = await asyncio.to_thread(
                mcp_capture.capture_dual,
                scale=scale,
                max_tokens=eff_max_tokens,
                frames=frames,
                process_name=process_name,
                cmdline_match=cmdline_match,
                client_pid=client_pid,
                fmt=fmt,
                quality=quality,
                crop=crop,
                crop_space=crop_space,
                save_fullres=save_fullres,
                save_dir=save_dir,
            )
        if result.get("isError"):
            raise ToolError(
                _wire_safe_error(
                    runtime, "capture_screenshot", result.get("error") or result
                )
            )
        inline = result.get("inline") or {}
        data = inline.get("data")
        if not isinstance(data, str):
            raise ToolError("missing image data")
        try:
            raw = base64.b64decode(data.encode("ascii"), validate=True)
        except ValueError as exc:
            raise ToolError("bad image data") from exc
        image_format = _image_format_from_mime(inline.get("mimeType"))
        image = Image(data=raw, format=image_format)
        meta = {"fullres_path": result.get("fullres_path"), **result.get("meta", {})}
        return [image, json.dumps(meta)]

    if config.enable_exec_enforce:

        @app.tool(description="Execute an exact allowlisted Enforce script expression through the server bridge.")
        async def exec_enforce(expr: str, main_fn: str = "", timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
            args = {"expr": expr, "main_fn": main_fn}
            async with runtime.tool_lock:
                return await runtime.call_exec_enforce(args, _timeout(timeout_s))

    @app.tool(description=_bridge_status_description())
    async def bridge_status() -> dict[str, Any]:
        runtime.touch()
        # B1: capability comparison (incl. ach) MUST run before ready.
        payload = await runtime.bridge_status_payload(
            registered_tools=await _bridge_tool_names(),
            intended_tools=intended_tool_names,
        )
        return await _with_tool_registry(payload)

    @app.tool(
        description=(
            "Requires a lease (session_acquire_wait). Seat the connected "
            "client in a nearby vehicle (client-side ownership get-in). "
            "Seats the client in the vehicle nearest to pos within the search radius. "
            "This is client ownership for engine_set, vehicle_control, and "
            "vehicle_trace; it does not place the player in the server crew. "
            "ActionCondition gates such as ActionSwitchLights still fail "
            "until vehicle_enter. One car per run: there is no get-out after "
            "this seat; player_teleport of the occupant returns "
            "occupant_client_seated. Tear down with object_delete of the "
            "in-session world_spawn object_id — that id does not survive the "
            "run. Deleting a seated transport needs care (it ejects; a stale "
            "or guessed object_id is not that fixture)."
        )
    )
    async def vehicle_get_in_client(pos: list[StrictFloat], timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        args = {"pos": _require_vec3(pos, "pos")}
        async with runtime.tool_lock:
            return await runtime.call_bridge("vehicle_get_in_client", args, "client", _timeout(timeout_s))

    @app.tool(
        description=(
            "Requires a lease (session_acquire_wait). Start or stop the "
            "owned vehicle's engine. This requires client-side ownership "
            "established with vehicle_get_in_client. command_sent reports "
            "bridge acceptance; state_confirmed reports matching engine "
            "readback when available, otherwise null (accepted, not confirmed). "
            "The local player must drive a car this client owns: otherwise the "
            "bridge returns not_seated (not seated in a car), not_driver "
            "(seated, but not in the driver seat) or not_owner (in the driver "
            "seat, but this client does not own the car)."
        )
    )
    async def engine_set(mode: str, timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        if mode not in ("start", "stop"):
            raise ToolError(
                f"bad_mode: mode {mode!r} must be one of 'start' or 'stop'"
            )
        async with runtime.tool_lock:
            result = await runtime.call_bridge(
                "engine_set", {"mode": mode}, "client", _timeout(timeout_s)
            )

        actual = result.get("engine_on_server")
        if isinstance(actual, bool):
            engine_on = actual
        elif type(actual) is int and actual in (0, 1):
            engine_on = bool(actual)
        else:
            engine_on = None

        response = dict(result)
        response["command_sent"] = True
        response["state_confirmed"] = (
            None if engine_on is None else engine_on == (mode == "start")
        )
        return response

    @app.tool(
        description=(
            "Requires a lease (session_acquire_wait). Set sustained "
            "owner-side driving control (held until released or deadman TTL). "
            "The local player must drive a car this client owns: otherwise the "
            "bridge returns not_seated (not seated in a car), not_driver "
            "(seated, but not in the driver seat) or not_owner (in the driver "
            "seat, but this client does not own the car)."
        )
    )
    async def vehicle_control(
        throttle: StrictFloat = 0.0,
        steer: StrictFloat = 0.0,
        brake: StrictFloat = 0.0,
        handbrake: StrictFloat = 0.0,
        hold_ttl_s: StrictFloat = 0.0,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        t = float(throttle)
        s = float(steer)
        b = float(brake)
        h = float(handbrake)
        ttl = float(hold_ttl_s)
        if not math.isfinite(t) or t < 0.0 or t > 1.0:
            raise ToolError("bad_throttle")
        if not math.isfinite(s) or s < -1.0 or s > 1.0:
            raise ToolError("bad_steer")
        if not math.isfinite(b) or b < 0.0 or b > 1.0:
            raise ToolError("bad_brake")
        if not math.isfinite(h) or (h != 0.0 and h != 1.0):
            raise ToolError("bad_handbrake")
        if not math.isfinite(ttl) or ttl < 0.0 or ttl > VEHICLE_CONTROL_MAX_TTL_S:
            raise ToolError(
                "bad_hold_ttl_s: hold_ttl_s "
                f"{ttl!r} must be in [0, {VEHICLE_CONTROL_MAX_TTL_S}]"
            )
        args = {"throttle": t, "steer": s, "brake": b, "handbrake": h, "hold_ttl_s": ttl}
        async with runtime.tool_lock:
            return await runtime.call_bridge("vehicle_control", args, "client", _timeout(timeout_s))

    @app.tool(description="Read owner-side vehicle telemetry (speed, gear, engine, pos, ownership).")
    async def vehicle_telemetry(timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        async with runtime.tool_lock:
            return await runtime.call_bridge("vehicle_telemetry", {}, "client", _timeout(timeout_s))

    @app.tool(
        description=(
            f"{LEASE_TOOL_LINE} Capture and read an atomic owner-client vehicle "
            "trace. mode=start requires the local player seated in the vehicle; "
            "otherwise the bridge returns not_seated. mode=start while a trace "
            "already exists returns trace_exists; call mode=clear before reuse. "
            "mode=dump writes JSONL to $profile:dayz_mcp_trace_<trace_id>.jsonl "
            "and stop autodumps the same file. Call mode=stop before "
            "vehicle_release; release Abort autodumps remaining samples then "
            "clears the trace."
        )
    )
    async def vehicle_trace(
        mode: str,
        trace_id: str = "",
        cursor: StrictInt = 0,
        limit: StrictInt = 64,
        sample_hz: StrictInt = 20,
        max_samples: StrictInt = 4096,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        try:
            args = normalize_request(
                mode,
                trace_id,
                cursor,
                limit,
                sample_hz,
                max_samples,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        async with runtime.tool_lock:
            raw_result = await runtime.call_bridge(
                "vehicle_trace",
                args,
                "client",
                _timeout(timeout_s),
            )
        try:
            return normalize_bridge_result(raw_result)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    @app.tool(
        description=(
            f"{LEASE_TOOL_LINE} Release sustained vehicle control "
            "(stop driving). Call vehicle_trace mode=stop before "
            "vehicle_release: an active trace is autodumped then cleared on "
            "Abort, so the stop result is what you read."
        )
    )
    async def vehicle_release(timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        async with runtime.tool_lock:
            return await runtime.call_bridge("vehicle_release", {}, "client", _timeout(timeout_s))

    @app.tool(
        description=(
            f"{LEASE_TOOL_LINE} Record a timeline of the local player's action "
            "and the item in hands, to time an object animation against the "
            "action. Client side only: the server is not sampled in this "
            "version. t_s is seconds since start in client tick time "
            "(GetTickTime), not server or wall time. Each sample has action "
            "(running action type or empty), action_state, callback (command, "
            "modifier or empty; command wins and callback_both=true when both "
            "run), state, state_name, hands and phases: one value per name in "
            "sources, read from the item in hands (all 0 and "
            "hands_present=false with empty hands). Samples come at sample_hz, "
            "plus one with edge=true on the frame the action type or the "
            "callback state changes; both share max_samples and a full "
            "timeline stops with overflow=true. mode=start while a timeline "
            "exists returns trace_exists; other modes need the trace_id that "
            "start returned. action_use started=true is not proof the action "
            "ran; this timeline is the evidence. Recipe: start with sources, "
            "then action_use, then read (stop first: eof needs a stopped "
            "timeline), then clear."
        )
    )
    async def anim_timeline(
        mode: str,
        trace_id: str = "",
        cursor: StrictInt = 0,
        limit: StrictInt = 64,
        sample_hz: StrictInt = 20,
        max_samples: StrictInt = 4096,
        sources: list[str] | None = None,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        try:
            args = anim_timeline_contract.normalize_request(
                mode,
                trace_id,
                cursor,
                limit,
                sample_hz,
                max_samples,
                sources,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        async with runtime.tool_lock:
            raw_result = await runtime.call_bridge(
                "anim_timeline",
                args,
                "client",
                _timeout(timeout_s),
            )
        try:
            return anim_timeline_contract.normalize_bridge_result(raw_result)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    @app.tool(description=(
        "Walk the client widget tree from an optional named root, or the active "
        "scripted menu when path is empty. Each node reports name, type, user_id, "
        "visible, visible_hierarchy, disabled, text, text_readable and screen box. "
        "TextWidget/RichTextWidget have no getter: text_readable is false and text "
        "is empty, never a fake label."
    ))
    async def ui_tree(
        path: str = "",
        limit: StrictInt = 256,
        # Not `str | None`: FastMCP would publish anyOf[string,null] and collapse
        # explicit root=null into omit (global scope). `str = None` publishes
        # type:string so Pydantic rejects the null before enqueue.
        root: str = None,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(path, str):
            raise ToolError(_bad_args("path", path, "be a string"))
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 512:
            raise ToolError(_bad_args("limit", limit, "be an int from 1 to 512"))
        if root is not None and (not isinstance(root, str) or root == ""):
            raise ToolError(_bad_args("root", root, "be a non-empty string"))
        args: dict[str, Any] = {"limit": int(limit)}
        if path != "":
            args["path"] = path
        if root is not None:
            args["root"] = root
        async with runtime.tool_lock:
            return await runtime.call_bridge("ui_tree", args, "client", _timeout(timeout_s))

    @app.tool(description=(
        f"{LEASE_TOOL_LINE} Write text on a client EditBox/MultilineEditBox/"
        "Button/Text widget by name. Other types return text_not_writable."
    ))
    async def ui_set_text(
        path: str,
        text: str,
        # Not `str | None`: FastMCP would publish anyOf[string,null] and collapse
        # explicit root=null into omit (global scope). `str = None` publishes
        # type:string so Pydantic rejects the null before enqueue.
        root: str = None,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(path, str) or path == "":
            raise ToolError(_bad_args("path", path, "be a non-empty string"))
        if not isinstance(text, str):
            raise ToolError(_bad_args("text", text, "be a string"))
        if root is not None and (not isinstance(root, str) or root == ""):
            raise ToolError(_bad_args("root", root, "be a non-empty string"))
        args: dict[str, Any] = {"path": path, "text": text}
        if root is not None:
            args["root"] = root
        async with runtime.tool_lock:
            return await runtime.call_bridge("ui_set_text", args, "client", _timeout(timeout_s))

    @app.tool(description=(
        f"{LEASE_TOOL_LINE} Click a client widget by name. button is "
        "0=left, 1=right, 2=middle. root scopes a search that would otherwise "
        "be global (homonyms without root return ambiguous_path). "
        "bubble=true is forwarded only with mode='complete'; direct mode "
        "ignores it. mode='direct' is the default; the bridge rejects "
        "mode='complete' with mode_not_implemented (complete would click the "
        "widget center via down→up→click with explicit bubbling). On failure "
        "the error text keeps the bridge diagnostics after the code, e.g. "
        "not_handled; handler='X' user_id=506 clicked=False; "
        "requested_path='BtnCloseX' requested_root='...' matched_path='...': "
        "an empty handler means no handler ran, a named one ran and declined."
    ))
    async def ui_click(
        path: str,
        button: StrictInt = 0,
        # Not `str | None`: FastMCP would publish anyOf[string,null] and collapse
        # explicit root=null into omit (global scope). `str = None` publishes
        # type:string so Pydantic rejects the null before enqueue.
        root: str = None,
        mode: UiClickMode = "direct",
        bubble: StrictBool = False,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(path, str) or path == "":
            raise ToolError(_bad_args("path", path, "be a non-empty string"))
        if not isinstance(button, int) or isinstance(button, bool) or button < 0 or button > 2:
            raise ToolError(_bad_args("button", button, "be an int from 0 to 2"))
        if not isinstance(mode, str) or mode not in {"direct", "complete"}:
            raise ToolError(_bad_args("mode", mode, "be one of 'direct' or 'complete'"))
        if not isinstance(bubble, bool):
            raise ToolError(_bad_args("bubble", bubble, "be a bool"))
        if root is not None and (not isinstance(root, str) or root == ""):
            raise ToolError(_bad_args("root", root, "be a non-empty string"))
        args: dict[str, Any] = {
            "path": path,
            "button": int(button),
            "mode": mode,
            "bubble": bubble,
        }
        if root is not None:
            args["root"] = root
        async with runtime.tool_lock:
            return await runtime.call_bridge("ui_click", args, "client", _timeout(timeout_s))

    @app.tool(description=(
        f"{LEASE_TOOL_LINE} Rebuild a client preview root from a .layout file on "
        "disk and return the engine rects of the resulting tree, so UI can be "
        "iterated without repacking the mod. Only $profile: paths are re-read from "
        "disk; an addon-prefixed path is served by the PBO. mode='close' unlinks "
        "the preview and loads nothing. A missing file returns layout_not_found "
        "instead of killing the client."
    ))
    async def ui_reload_layout(
        path: str = "",
        mode: UiReloadLayoutMode = "reload",
        limit: StrictInt = 256,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(mode, str) or mode not in {"reload", "close"}:
            raise ToolError(
                _bad_args("mode", mode, "be one of 'reload' or 'close'")
            )
        if not isinstance(path, str):
            raise ToolError(_bad_args("path", path, "be a string"))
        if mode == "reload" and path == "":
            raise ToolError(
                _bad_args("path", path, "be non-empty when mode is 'reload'")
            )
        if mode == "close" and path != "":
            raise ToolError(
                _bad_args("path", path, "be empty when mode is 'close'")
            )
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 512:
            raise ToolError(_bad_args("limit", limit, "be an int from 1 to 512"))
        args: dict[str, Any] = {"mode": mode, "limit": int(limit)}
        if path != "":
            args["path"] = path
        async with runtime.tool_lock:
            return await runtime.call_bridge(
                "ui_reload_layout", args, "client", _timeout(timeout_s)
            )

    @app.tool(description=(
        f"{LEASE_TOOL_LINE} Give keyboard focus to a client widget by name. "
        "Walks to the topmost ancestor, SetActiveWindow(..., false) so the "
        "engine does not steal focus onto the first focusable child, then "
        "SetFocus. ok is true only when GetFocus() equals the target; a "
        "widget that cannot take focus (NoFocus flag or disabled) returns "
        "found=true and error=focus_not_taken. A plain TextWidget does take "
        "focus (ok=1). ui_click does not focus: it calls OnClick directly."
    ))
    async def ui_focus(
        path: str,
        # Not `str | None`: FastMCP would publish anyOf[string,null] and collapse
        # explicit root=null into omit (global scope). `str = None` publishes
        # type:string so Pydantic rejects the null before enqueue.
        root: str = None,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(path, str) or path == "":
            raise ToolError(_bad_args("path", path, "be a non-empty string"))
        if root is not None and (not isinstance(root, str) or root == ""):
            raise ToolError(_bad_args("root", root, "be a non-empty string"))
        args: dict[str, Any] = {"path": path}
        if root is not None:
            args["root"] = root
        async with runtime.tool_lock:
            return await runtime.call_bridge(
                "ui_focus", args, "client", _timeout(timeout_s)
            )

    @app.tool(description=(
        f"{LEASE_TOOL_LINE} Client modal (acknowledge/confirm/form). "
        "Blocks up to timeout_s for the local player's answer; "
        "cancelled and timed_out are valid."
    ))
    async def ui_dialog(
        kind: Literal["acknowledge", "confirm", "form"],
        title: str,
        message: str = "",
        fields: list[dict[str, Any]] | None = None,
        timeout_s: StrictFloat = 60.0,
    ) -> dict[str, Any]:
        return await execute_ui_dialog(
            runtime,
            kind,
            title,
            message=message,
            fields=fields,
            timeout_s=timeout_s,
        )

    @app.tool(description=(
        f"{LEASE_TOOL_LINE} Start a DayZ user action on the local player "
        "without keyboard. Verify the EFFECT you expect, not the call: for a "
        "continuous action a longer wait cannot recover one the engine already "
        "cancelled, so poll the world state the action should have changed. "
        "action = the Enforce class name of the user action "
        "(candidate.Type().ToString(), e.g. ActionOpenDoors), NOT the "
        "visible/localized prompt text; classname = the target's GetType(). "
        "target selects the ActionTarget: world (default) is the nearest world "
        "object, hands is the item in the player's hands, self is a null target; "
        "the result echoes that same mode. hands and self need an addon that "
        "announces action_use_target and otherwise return target_not_supported "
        "without calling the bridge; they suit actions whose target condition "
        "does not read the cursor position (for example ActionDrink). A hands or "
        "self call whose result does not echo that mode is target_not_supported. "
        "door_index selects one door of a Building (0 <= door_index < 64) on "
        "the nearest world object of classname. It needs an addon that "
        "announces action_use_door and otherwise returns door_not_supported "
        "without calling the bridge. A result that does not echo the same "
        "door_index is door_not_supported. Door errors: door_not_supported, "
        "not_a_building, door_out_of_range (door_index >= that building's "
        "door count) and door_component_not_found. The player must stand within 2 m "
        "of that door: read its pos from object_doors, then player_teleport. "
        "started still does not prove the door moved; read object_doors after. "
        "Routes to MCPClientBridge on the CLIENT and calls "
        "ActionManagerClient.PerformActionStart with the held item. Without "
        "door_index the synthetic target uses component=-1. With door_index "
        "the target uses the view-geometry component of that door. This enters the normal client action "
        "lifecycle, including client callbacks such as OnStartClient and, for "
        "AnimatedActionBase when its execution animation event arrives, "
        "OnExecuteClient; client-only mod code compiled under #ifndef SERVER "
        "can therefore run. Non-local multiplayer actions are also sent to "
        "the server, which can reject them. No callbacks are invoked directly "
        "by this tool. started:true / started:1 only means the client manager "
        "retained a running/pending action immediately after the start call; "
        "it proves neither server acceptance, callback execution nor "
        "completion. The "
        "tool does not sustain continuous-action input or wait for progress "
        "completion. Verify the intended effect separately; client callback "
        "reachability by code is not an in-engine test of your mod."
    ))
    async def action_use(
        action: str,
        classname: str = "",
        pos: list[StrictFloat] | None = None,
        radius: StrictFloat = 5.0,
        target: StrictStr = "world",
        door_index: StrictInt | None = None,
        timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(action, str) or action == "":
            raise ToolError(_bad_args("action", action, "be a non-empty string"))
        if not isinstance(classname, str):
            raise ToolError(_bad_args("classname", classname, "be a string"))
        if not isinstance(target, str) or target not in {"world", "hands", "self"}:
            raise ToolError(
                _bad_args("target", target, "be 'world', 'hands' or 'self'")
            )
        if pos is not None and target != "world":
            raise ToolError(
                _bad_args("pos", pos, "be omitted unless target is world")
            )
        if classname != "" and target == "self":
            raise ToolError(
                _bad_args("classname", classname, "be omitted when target is self")
            )
        if door_index is not None:
            if (
                isinstance(door_index, bool)
                or not isinstance(door_index, int)
                or door_index < 0
                or door_index >= 64
            ):
                raise ToolError(
                    _bad_args("door_index", door_index, "be an int from 0 to 63")
                )
            if target != "world":
                raise ToolError(
                    _bad_args(
                        "door_index",
                        door_index,
                        "be omitted unless target is world",
                    )
                )
            if classname == "":
                raise ToolError(
                    _bad_args(
                        "classname",
                        classname,
                        "be a non-empty string when door_index is set",
                    )
                )
        radius_error = _bad_args(
            "radius",
            radius,
            "be a finite number greater than 0 and at most 200",
        )
        radius_value = _finite_float(radius, radius_error)
        if radius_value <= 0.0 or radius_value > 200.0:
            raise ToolError(radius_error)
        args: dict[str, Any] = {"action": action, "radius": radius_value}
        if classname != "":
            args["classname"] = classname
        if pos is not None:
            args["pos"] = _require_vec3(pos, "pos")
        if door_index is not None:
            announced = False
            try:
                status = await runtime.bridge_status_payload()
                announced = _client_peer_announces_command(status, "action_use_door")
            except Exception:
                announced = False
            if not announced:
                raise ToolError("door_not_supported")
            args["door_index"] = door_index
            async with runtime.tool_lock:
                result = await runtime.call_bridge(
                    "action_use_door", args, "client", _timeout(timeout_s)
                )
            echoed = result.get("door_index") if isinstance(result, dict) else None
            if echoed != door_index:
                raise ToolError("door_not_supported")
            return result
        if target == "world":
            async with runtime.tool_lock:
                return await runtime.call_bridge(
                    "action_use", args, "client", _timeout(timeout_s)
                )
        announced = False
        try:
            status = await runtime.bridge_status_payload()
            announced = _client_peer_announces_command(status, "action_use_target")
        except Exception:
            announced = False
        if not announced:
            raise ToolError("target_not_supported")
        args["target"] = target
        async with runtime.tool_lock:
            result = await runtime.call_bridge(
                "action_use_target", args, "client", _timeout(timeout_s)
            )
        echoed = result.get("target") if isinstance(result, dict) else None
        if echoed != target:
            raise ToolError("target_not_supported")
        return result

    @app.tool(
        description=(
            "Block until a condition holds. condition ENUM: players_at_least, "
            "players_at_most, log_matches, entity_state. entity_state requires "
            "entity={type,pos,radius,field,equals}; it polls server telemetry_read "
            "object_at with an exact type and radius in (0,50]. field is found "
            "(bool), health01 (0..1), attachment_count, cargo_count or items_total "
            "(non-negative int). Equality only, no float tolerance. Absence "
            "satisfies only found=false; missing state and bridge errors abort. "
            "Inventory counts are immediate, not recursive. Querying existence "
            "inherits the engine streaming limits of object_at. This does not "
            "read arbitrary mod members, sorter power, or client SyncVars. "
            "pattern is a plain SUBSTRING, not a "
            r"regex: pass '[MOD]', never '\[MOD\]'. For log_matches, marker "
            "is the exact cursor returned by logs_since; when present, "
            "lookback_lines and lookback_from are ignored. Without marker, "
            "lookback_lines (default 200, max "
            f"{WAIT_FOR_LOOKBACK_MAX}) rewinds N lines, or "
            "lookback_from='launch' scans from byte 0. That heuristic can match "
            "a line written before the caller's action and cause a false positive. "
            "On timeout still returns "
            "ok: true with satisfied: false -- gate on satisfied, not ok. "
            "timeout_s <= 600 (bad_args above; never clamped). "
            "players_* waits through startup: a probe answered "
            "game_not_ready:reason=server_poll_stale, "
            "game_not_ready:reason=client_not_polling, or "
            "binding_not_ready is retried until "
            "timeout_s (not_ready_probes, last_error in the response); any "
            "other not-ready reason aborts on the first probe. "
            "client_not_polling is the normal client-load window after launch; "
            "a dead client still stops at timeout_s with that last_error. "
            "A probe refused with run_not_owned (the run has no owner) aborts on "
            "the first probe with the daemon's hint: adopt the run first with "
            "session_acquire_wait, whose grant adopts the single ownerless "
            "RUNNING_IDLE run and reports it in adopted_run (with several idle "
            "runs it reports multiple_idle_runs and adopts none). "
            "scanned reports which log files were read and how many "
            "lines each gave, so a no-match is visible as a no-match. "
            "players_* and entity_state probes reach the box with this "
            "session's lease and renew it while this wait stays open; "
            "log_matches does not. "
            f"{_lease_renewal_contract(config.session_ttl_s)}"
        )
    )
    async def wait_for(
        condition: Literal["players_at_least", "players_at_most", "log_matches", "entity_state"],
        value: StrictInt = 0,
        pattern: str = "",
        timeout_s: StrictFloat = 180.0,
        poll_interval_s: StrictFloat = 2.0,
        lookback_lines: StrictInt = 200,
        lookback_from: Literal["lines", "launch"] = "lines",
        marker: str | dict[str, Any] | None = None,
        entity: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # wait_for, ui_dialog, and playbook_run: do not wrap the whole body
        # in tool_lock. Any tool that waits on a human or a slow condition
        # takes the lock per probe and sleeps outside it. A whole-body lock
        # here would freeze every other session that shares the daemon for
        # the full timeout_s.
        return await execute_wait_for(
            runtime,
            condition,
            value=value,
            pattern=pattern,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            lookback_lines=lookback_lines,
            lookback_from=lookback_from,
            marker=marker,
            entity=entity,
        )

    def _pipeline_platform() -> str:
        config = getattr(runtime, "config", None)
        value = getattr(config, "client_platform", "") if config is not None else ""
        return value if type(value) is str else ""

    @app.tool(
        description=(
            "List approved dayz_test_run project names from the sealed "
            "request policy (the `mod` field). No host paths. The policy is "
            "produced by build_native_launcher.py; a clone that has not "
            "built it fails with launcher_policy_missing."
        )
    )
    async def list_projects() -> dict[str, Any]:
        async with runtime.tool_lock:
            return dayz_test_tool.list_project_names()

    @app.tool(
        description=(
            "File pipeline feedback from any agent session. kind must be "
            "bug | request | finding | tool_contribution. Body template: "
            "tool, args, error, repro. For contributions, reference "
            "artifacts at DURABLE paths (never session scratchpads). "
            "Enforced limits, in characters: title 1..120, body 1..8000, "
            "project 0..64. An over-length value is rejected by the published "
            "inputSchema (Pydantic type=string_too_long) before inbox; trim "
            "to those caps without guessing. Published minLength counts raw "
            "characters, spaces included; inbox strips title, so a "
            "whitespace-only title is schema-legal then bad_args: title empty. "
            "Appends to a local shared inbox; ids cannot collide. Works "
            "even when the game and daemon are down."
        )
    )
    async def pipeline_feedback(
        kind: Literal["bug", "request", "finding", "tool_contribution"],
        title: Annotated[
            str,
            Field(min_length=inbox.TITLE_MIN_CHARS, max_length=inbox.TITLE_MAX_CHARS),
        ],
        body: Annotated[
            str,
            Field(min_length=inbox.BODY_MIN_CHARS, max_length=inbox.BODY_MAX_CHARS),
        ],
        project: Annotated[str, Field(max_length=inbox.PROJECT_MAX_CHARS)] = "",
    ) -> dict[str, Any]:
        """File pipeline feedback from any agent session: a bug you hit, a request for a missing capability, a finding worth recording, or a tool/playbook you built (kind=tool_contribution). For contributions, reference artifacts at DURABLE paths (never session scratchpads). Enforced limits, in characters: title 1..120, body 1..8000, project 0..64. An over-length value is rejected by the published inputSchema (Pydantic type=string_too_long) before inbox; trim to those caps without guessing. Published minLength counts raw characters, spaces included; inbox strips title, so a whitespace-only title is schema-legal then bad_args: title empty. Appends to a local shared inbox; ids cannot collide. Works even when the game and daemon are down."""
        # The lock here only preserves the one-tool-at-a-time client invariant;
        # these tools do not call the bridge.
        async with runtime.tool_lock:
            try:
                return inbox.append_feedback(
                    kind,
                    title,
                    body,
                    project=project,
                    platform=_pipeline_platform(),
                )
            except ValueError as exc:
                message = str(exc)
                if message == "bad_args" or message.startswith("bad_args"):
                    raise ToolError(message) from None
                raise

    @app.tool(
        description=(
            "Drain the pipeline inbox for owner triage or any-agent lookup."
        )
    )
    async def pipeline_inbox(
        limit: StrictInt = 20,
        kind: str = "",
        include_resolved: bool = False,
    ) -> dict[str, Any]:
        """Drain the pipeline inbox for owner triage or any-agent lookup."""
        # The lock here only preserves the one-tool-at-a-time client invariant;
        # these tools do not call the bridge.
        async with runtime.tool_lock:
            try:
                return inbox.read_inbox(
                    limit=limit, kind=kind, include_resolved=include_resolved
                )
            except ValueError as exc:
                message = str(exc)
                if message == "bad_args" or message.startswith("bad_args"):
                    raise ToolError(message) from None
                raise

    @app.tool(
        description=(
            "Triage a feedback item by appending a resolution; deletes "
            "nothing, history is append-only. A feedback_id that matches no "
            "filed entry is refused with feedback_not_found; nothing is "
            "appended. Enforced limits, in characters: "
            "resolution 1..2000, evidence_ref 1..240. evidence_ref is a path "
            "only -- a path relative to DayZ_MCP_dev starting at one of "
            "reviews | gates | reports | research, ASCII, segments of "
            "[A-Za-z0-9._-]. No repo prefix (not DayZ_MCP_dev/reviews/...), and "
            "nothing appended to it: a note, parentheses, a commit id or a #anchor "
            "make it an invalid path segment. An over-length resolution is "
            "rejected by the published inputSchema (Pydantic "
            "type=string_too_long) before inbox; trim to 2000 without guessing."
        )
    )
    async def pipeline_resolve(
        feedback_id: str,
        resolution: Annotated[str, Field(max_length=inbox.RESOLUTION_MAX_CHARS)],
        evidence_ref: Annotated[
            str | None, Field(max_length=inbox.EVIDENCE_REF_MAX_CHARS)
        ] = None,
    ) -> dict[str, Any]:
        """Triage a feedback item by appending a resolution; deletes nothing, history is append-only. A feedback_id that matches no filed entry is refused with feedback_not_found; nothing is appended. Enforced limits, in characters: resolution 1..2000, evidence_ref 1..240. evidence_ref is a path only -- relative to DayZ_MCP_dev, starting at reviews | gates | reports | research, ASCII, segments of [A-Za-z0-9._-], no repo prefix and nothing appended (note, parentheses, commit id, #anchor). An over-length resolution is rejected by the published inputSchema (Pydantic type=string_too_long) before inbox; trim to 2000 without guessing."""
        # The lock here only preserves the one-tool-at-a-time client invariant;
        # these tools do not call the bridge.
        async with runtime.tool_lock:
            try:
                return inbox.append_resolution(
                    feedback_id,
                    resolution,
                    platform=_pipeline_platform(),
                    evidence_ref=evidence_ref,
                )
            except ValueError as exc:
                message = str(exc)
                if (
                    message == "bad_args"
                    or message.startswith("bad_args")
                    or message.startswith("feedback_not_found")
                ):
                    raise ToolError(message) from None
                raise

    @app.tool(
        description=(
            f"{LEASE_TOOL_LINE} Run a named playbook checklist from the "
            "dictionary. Does not launch DayZ. certified is always false. "
            "Live place_safely requires params.x and params.z (0 ok)."
        )
    )
    async def playbook_run(
        name: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a named playbook checklist.

        Does not wrap the body in ``runtime.tool_lock``. Each step tool
        takes the lock as usual (same rule as ``wait_for`` and
        ``ui_dialog``). There is no envelope timeout: each sub-tool
        applies its own budget (``DEFAULT_TOOL_TIMEOUT_S`` 15s up to
        ``MAX_TIMEOUT_S`` 300s; ``wait_for`` <= 600s; ``ui_dialog``
        <= 250s). At most ``MAX_PLAYBOOK_STEPS`` steps. ``certified``
        is always false until a FROZEN sidecar registry exists. Does
        not launch DayZ. Live ``place_safely`` requires explicit
        ``params.x`` and ``params.z`` (the site); explicit ``0`` is
        valid. Omitting either is ``bad_args``.
        """
        return await playbook_tool_mod.execute_playbook_run(app, name, params)

    @app.tool(
        description=(
            "Explicitly reload only playbooks/runner.py in this MCP process: "
            "module must be dayz_playbook_runner. Refuses while any playbook is "
            "in flight or a load is in progress. Keeps the previous runner on "
            "failure. Does not reload the tool registry, daemon or other clients. "
            "The response may retain a freshness warning from call entry; check "
            "bridge_status.server_modules on the next call."
        )
    )
    async def playbook_reload(
        module: Literal["dayz_playbook_runner"],
    ) -> dict[str, Any]:
        return await asyncio.to_thread(playbook_tool_mod.reload_runner, module)

    _patch_mode_enum_from_authority(app, "dayz_test_run")
    _describe_run_parameters(app, "dayz_test_run")
    for _closed_tool in _CLOSED_SCHEMA_TOOLS:
        _patch_closed_tool_schema(app, _closed_tool)
    _patch_public_argument_alias(app, "scene_raycast", "from_pos", "from")
    tool_pack_mod.apply_tool_pack(app._tool_manager, config.tool_pack)
    registered_tool_names = frozenset(
        tool.name for tool in app._tool_manager.list_tools()
    )
    _registered_tool_names.update(registered_tool_names)
    runtime._registered_tool_names = registered_tool_names
    _tool_registry_overlay.update(_frozen_tool_registry_overlay(app, config))
    if _progressive_disclosure_enabled(config):
        # Before install_result_freshness: its wrapper has to stay outermost.
        _install_catalog_change_notice(app, runtime)
    observe_server_sources = install_result_freshness(app, server_sources)
    _original_list_tools = app.list_tools

    async def list_tools_progressive():
        tools = await _original_list_tools()
        if not _progressive_disclosure_active(runtime):
            return tools
        return _compact_initial_catalog(tools)

    # FastMCP binds tools/list at construction via
    # _mcp_server.list_tools()(self.list_tools). Replacing the Python
    # attribute alone leaves the transport handler pointing at the original
    # full catalog; re-register so MCP clients see the compact list.
    app.list_tools = list_tools_progressive  # type: ignore[method-assign]
    app._mcp_server.list_tools()(list_tools_progressive)
    return app, runtime


def parse_args(argv: list[str] | None = None) -> ServerConfig:
    parser = build_server_parser()
    parser.allow_abbrev = False
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw_argv)
    tool_pack = args.tool_pack
    tool_pack_was_explicit = "--tool-pack" in raw_argv or any(
        token.startswith("--tool-pack=") for token in raw_argv
    )
    if not tool_pack_was_explicit:
        environment_tool_pack = os.environ.get("DAYZ_MCP_TOOL_PACK", "").strip()
        if environment_tool_pack:
            tool_pack = environment_tool_pack
    try:
        tool_pack_mod.tool_names(tool_pack)
    except ValueError as exc:
        parser.error(str(exc))
    client_platform_raw = (
        args.client_platform if args.client_platform in CLIENT_PLATFORM_ALIASES else ""
    )
    return ServerConfig(
        mode=args.mode,
        port=args.port,
        keyfile=args.keyfile,
        expected_game_version=args.expected_game_version,
        require_version=bool(args.require_version),
        idle_timeout_s=float(args.idle_timeout),
        enable_exec_enforce=bool(args.enable_exec_enforce),
        exec_allowlist=args.exec_allowlist,
        exec_audit_path=args.exec_audit_path,
        client_platform=CLIENT_PLATFORM_ALIASES.get(
            args.client_platform, args.client_platform
        ),
        client_platform_raw=client_platform_raw,
        task_label=args.task_label,
        supervised=bool(args.supervised),
        auto_spawn_daemon=bool(args.auto_spawn_daemon),
        tool_pack=tool_pack,
        progressive_disclosure=bool(args.progressive_disclosure),
    )


def _release_and_exit(runtime: Runtime) -> None:
    # The MCP stdio peer is gone (parent exited, or the session hung/was abandoned):
    # release the loopback port best-effort, then hard-exit. A graceful FastMCP
    # shutdown is moot once no live peer remains. Shared by both watchdogs.
    try:
        runtime.stop_loopback()
    except Exception:
        pass
    os._exit(0)


def run_supervisor(argv: list[str]) -> int:
    """Own stdio and serve the host from a worker this process can replace.

    The worker is this same module with --supervised removed, so it keeps the mode and
    every other flag the host registered. The lease crosses each replacement through a
    carrier file whose PATH -- never the token -- travels in the child's environment.
    """
    import tempfile

    child_argv = [value for value in argv if value != "--supervised"]
    command = [sys.executable, "-u", "-m", "dayz_mcp", *child_argv]
    carrier = session_handoff.carrier_path(
        tempfile.mkdtemp(prefix="dayz-mcp-handoff-")
    )
    child_env = dict(os.environ)
    child_env[session_handoff.HANDOFF_ENV] = str(carrier)

    def log(message: str) -> None:
        print(f"[supervisor] {message}", file=sys.stderr, flush=True)

    def spawn() -> subprocess.Popen:
        return subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            env=child_env,
        )

    supervisor = Supervisor(spawn=spawn, out_stream=sys.stdout.buffer, log=log)
    try:
        supervisor.run(sys.stdin.buffer)
    finally:
        session_handoff.clear_handoff(carrier)
        session_handoff.clear_supervisor_identity(
            session_handoff.supervisor_identity_path(carrier)
        )
        try:
            os.rmdir(carrier.parent)
        except OSError:
            pass
    return 0


def run(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    if config.supervised:
        return run_supervisor(list(sys.argv[1:] if argv is None else argv))
    if config.mode == "daemon":
        return daemon.run_daemon(config)

    app, runtime = build_app(config)
    if config.mode == "embedded":
        try:
            runtime.start_loopback()
        except OSError as exc:
            print(
                f"failed to bind loopback on 127.0.0.1:{config.port}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            return 2

    log = lambda message: print(message, file=sys.stderr, flush=True)
    if config.mode == "embedded":
        # Embedded owns the port → arm both watchdogs (parent-death + idle), today's
        # behavior. Client mode holds nothing, so it needs neither; the daemon owns
        # its own idle watchdog and is intentionally NOT parent-bound (it outlives
        # the spawning session).
        orphan_guard.install_parent_death_watchdog(
            on_parent_death=lambda: _release_and_exit(runtime),
            log=log,
        )
        if config.idle_timeout_s and config.idle_timeout_s > 0:
            poll_interval = min(60.0, max(5.0, config.idle_timeout_s / 4.0))
            orphan_guard.install_idle_watchdog(
                idle_seconds=runtime.idle_seconds,
                timeout_s=config.idle_timeout_s,
                on_idle=lambda: _release_and_exit(runtime),
                log=log,
                poll_interval=poll_interval,
            )
    try:
        app.run(transport="stdio")
    finally:
        if config.mode == "embedded":
            runtime.stop_loopback()
    return 0
