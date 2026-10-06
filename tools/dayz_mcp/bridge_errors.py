"""Caller-facing error text: the enqueue whitelist, bridge error details, wire-safe tokens.

Maps a daemon's enqueue refusal or a bridge error result to the ToolError text
the MCP caller sees, and keeps host paths off the wire. Moved out of server.py
unchanged (backlog 71fc); server.py imports every name back, so
dayz_mcp.server.<name> is the same object.
"""

from __future__ import annotations

import math
import re
import traceback
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp.agent_loop import with_next_step
from dayz_mcp.bridge_readiness import (
    READY_REASONS,
    _game_not_ready_reason,
    _target_peer_down,
)
from dayz_mcp.session_coordination import public_audit_stage


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


_REMOTE_ERROR_CODES = frozenset({
    "audit_failed",
    "arg_contract_mismatch",
    "bad_args",
    "bridge_capability_missing",
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
    # d17c-a: client-peer admission refused because the exact destination's
    # registered client process is known dead. Process absence, not a crash.
    "client_process_gone",
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


# player_move fills its reply before refusing (MCPClientBridge.c
# DispatchPlayerMove). Only a known released_by crosses, and only on the two
# codes that are about a release: not_held (the last release, when there was
# one) and aborted (what ended the hold first).
_PLAYER_MOVE_RELEASED_BY = frozenset(
    {"hold", "phase", "ttl", "arrived", "restore", "player_changed", "shutdown"}
)
_PLAYER_MOVE_RELEASE_CODES = frozenset({"not_held", "aborted"})


def _player_move_detail(result: dict[str, Any]) -> str:
    if str(result.get("error") or "") not in _PLAYER_MOVE_RELEASE_CODES:
        return ""
    report = result.get("player_move")
    if not isinstance(report, dict):
        return ""
    released_by = report.get("released_by")
    if isinstance(released_by, str) and released_by in _PLAYER_MOVE_RELEASED_BY:
        return f"observed=released_by={released_by}"
    return ""


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

    player_move is the fifth: not_held and aborted carry observed=released_by=
    with a known release cause, and nothing else crosses.

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
    if cmd == "player_move":
        return _player_move_detail(result)
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
    # vehicle_door/door_missing, which carries the empty slot,
    # input_trigger, whose refusals carry reason= and observed=, and
    # player_move, whose not_held and aborted carry observed=released_by=.
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


def _audit_failed_message(message: str, audit_stage: object) -> str:
    """Name the coordinator step an audit_failed stopped at (00c4).

    The stage travels only when it is one of session_coordination.AUDIT_STAGES,
    together with next_step=session_status: its audit_fault, claimable and
    cleanup_degraded tell whether the failure is still latched. Without a known
    stage (the exec_enforce or lifecycle audit, or an older daemon) the message
    is returned unchanged.
    """
    stage = public_audit_stage(audit_stage)
    if stage is None:
        return message
    return with_next_step(f"{message}; audit_stage={stage}", "session_status")


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
    A coordinator audit_failed carries its audit_stage and
    next_step=session_status (_audit_failed_message).
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
    if code == "audit_failed":
        return _audit_failed_message(message, payload.get("audit_stage"))
    return message
