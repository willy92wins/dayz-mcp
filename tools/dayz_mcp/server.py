from __future__ import annotations

import asyncio
import base64
import inspect
import json
import math
import os
import sys
import threading
import time
import traceback
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Awaitable, Callable, Iterator, Literal

from mcp.server.fastmcp import Context, FastMCP, Image
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

import mcp_capture
from dayz_mcp import (
    core,
    daemon,
    daemon_credential,
    dayz_test_tool,
    host_config,
    inbox,
    orphan_guard,
    playbook_tool as playbook_tool_mod,
    ui_dialog as ui_dialog_mod,
)
from dayz_mcp.control_client import ControlClient, ControlClientError, ControlIdentity
from dayz_mcp.accredited_daemon_transport import AccreditedTransportError
from dayz_mcp.daemon_policy import load_normal_daemon_policy
from dayz_mcp.core import EXPECTED_BRIDGE_VERSION
from dayz_mcp import log_tail, result_prune
from dayz_mcp.loopback import LoopbackServer, read_key
from dayz_mcp.server_cli import CLIENT_PLATFORM_ALIASES, build_server_parser
from dayz_mcp.process_lifecycle import empty_box, occupancy_error_fields
from dayz_mcp.session_coordination import ClientIdentity
from dayz_mcp.vehicle_trace import normalize_bridge_result, normalize_request


DEFAULT_TOOL_TIMEOUT_S = 15.0
# Upper bound for per-tool bridge timeouts. 300 s, not 120 s, because
# dayz_test_run in mode=all measured 28.6 s and the operation pin already caps
# at MAX_OPERATION_PIN_S=300.0 (session_coordination).
MAX_TIMEOUT_S = 300.0
# The liveness probe runs AFTER the caller's budget is already spent, and inside
# the tool lock, so it gets its own short ceiling instead of the 5.0 s default of
# _request_once. A slow daemon degrades the message; it must not extend the call.
LIVENESS_STATUS_TIMEOUT_S = 1.0
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
})
LEASE_REQUIRED_RECIPE = "lease_required: call session_acquire_wait(purpose=...)"
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
DAEMON_AUTOSPAWN_DISABLED = (
    "daemon_autospawn_disabled: start the daemon (--daemon) or omit "
    "--no-daemon-autospawn"
)
DAEMON_AUTOSPAWN_ALREADY = (
    "daemon_unavailable: autospawn already attempted (start the daemon "
    "or omit --no-daemon-autospawn)"
)
# A peer with last_poll_age_s >= this value is not live (game polls ~0.2s).
PEER_STALE_S = 15.0
# Closed ready.reason set. *_legacy_blocked / version_mismatch only after
# that peer has polled at least once (last_poll_age_s is not None).
READY_REASONS = frozenset({
    "ready",
    "no_run",
    "server_poll_stale",
    "client_not_polling",
    "client_legacy_blocked",
    "version_mismatch",
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
})
_STALE_TICKET_ERRORS = frozenset({"ticket_expired", "ticket_invalid"})
_STALE_LEASE_ERRORS = frozenset({"lease_expired", "lease_invalid"})
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


def _remote_error_code(payload: object) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, str) and error in _REMOTE_ERROR_CODES:
            return error
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


def _peer_is_live(peer: object) -> bool:
    if not isinstance(peer, dict):
        return False
    bind = peer.get("binding_state")
    if bind == "LEGACY_UNBOUND":
        return False
    if bind in {None, ""}:
        age = peer.get("last_poll_age_s")
    elif bind != "BOUND":
        return False
    else:
        age = peer.get("bound_last_poll_age_s")
    return isinstance(age, (int, float)) and not isinstance(age, bool) and age < PEER_STALE_S


def compute_bridge_ready(status: dict[str, Any]) -> dict[str, Any]:
    """Return {ready, reason} for a bridge_status snapshot. Additive field.

    Version reasons are used only when that peer has polled at least once.
    Server liveness is checked before client liveness.
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
    if s_live and c_live and s_state == "ok" and c_state == "ok":
        return {"ready": True, "reason": "ready"}
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


def _with_ready(status: dict[str, Any]) -> dict[str, Any]:
    payload = dict(status)
    payload["ready"] = compute_bridge_ready(payload)
    return payload


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


def _bridge_error(result: dict[str, Any]) -> ToolError:
    # The message stays a fixed code; the bridge's object_id (sent on a
    # spawn timeout, MCPBridge.c:3272) rides in a structured attribute so the
    # caller can clean up instead of duplicating, without the message carrying
    # host content across the MCP wire.
    error = ToolError(str(result.get("error") or "bridge_error"))
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
    """Map a remote enqueue payload to the caller-facing ToolError string."""
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
    if code == "version_blocked":
        if _target_peer_down(status_snapshot, peer):
            return f"game_not_ready:reason={_game_not_ready_reason(status_snapshot, peer)}"
        expected = payload.get("expected") if isinstance(payload, dict) else None
        got = payload.get("got") if isinstance(payload, dict) else None
        if isinstance(expected, str):
            return f"version_blocked:bridge {got!r} != {expected!r}"
        return "version_blocked"
    return code


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
    # CLI flag is the spawn authority for this process. It need not match
    # the registered host argv (registration-False / CLI-True is allowed).
    auto_spawn_daemon: bool = True


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

    async def bridge_status_payload(self) -> dict[str, Any]:
        """Async status accessor used by the bridge_status tool (uniform with
        ClientRuntime, which fetches /status over HTTP)."""
        return _with_ready(self.status())

    def ensure_peer_allowed(self, peer: str) -> None:
        snapshot = self.status()
        peer_key = "client_peer" if peer == "client" else "server_peer"
        peer_status = snapshot[peer_key]
        state = peer_status["version_state"]
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
        return await self.wait_for_result(cmd, command_id, peer, timeout_s)

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
                    raise _bridge_error(result)
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
            raise _bridge_error(result)
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
        self.identity = ClientIdentity(
            platform=config.client_platform,
            pid=os.getpid(),
            ppid=os.getppid(),
            started_at_utc=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            session_id=str(uuid.uuid4()),
            task_label=(config.task_label or os.environ.get("DAYZ_MCP_TASK_LABEL", ""))[:120],
        )
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

        def public_error_code(error: ControlClientError) -> str:
            if error.code == "retail_quarantine":
                return _retail_quarantine_recipe(error.hint)
            if error.code == "lease_required":
                return LEASE_REQUIRED_RECIPE
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
                    raise ToolError(public_error_code(retry_error)) from None
            raise ToolError(public_error_code(error)) from None

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
        return await self._control_with_lazy_spawn(
            self._control.session_acquire_wait,
            purpose,
            max_wait_s=max_wait_s,
            progress_cb=progress_cb,
        )

    async def session_heartbeat(self, lease_token: str) -> dict[str, Any]:
        return await self._control_with_lazy_spawn(
            self._control.session_heartbeat, lease_token
        )

    async def session_release(self, lease_token: str) -> dict[str, Any]:
        return await self._control_with_lazy_spawn(
            self._control.session_release, lease_token
        )

    async def session_status(self) -> dict[str, Any]:
        return await self._control_with_lazy_spawn(self._control.session_status)

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

    def _default_spawn(self) -> int | None:
        return daemon.spawn_detached(
            list(self._daemon_argv), log=self._log, cwd=self._daemon_cwd
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
        status, response_body = self._credential_provider.request_with_refresh(
            method=method,
            path=path,
            query=dict(query or {}),
            body=data,
            headers=headers,
            deadline=deadline,
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
                raise ToolError(
                    "daemon_unavailable"
                    if retry_safe
                    else "daemon_response_ambiguous"
                ) from None
            except (ConnectionError, OSError):
                raise ToolError("daemon_unavailable") from None
        except (ConnectionError, OSError):
            raise ToolError("daemon_unavailable") from None

    async def call_bridge(self, cmd: str, args: dict[str, Any], peer: str, timeout_s: float) -> dict[str, Any]:
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
        status, payload = await asyncio.to_thread(
            self._call,
            "POST",
            "/enqueue",
            request_payload,
            None,
            timeout_s,
            deadline,
        )
        if status != 200:
            error = self._enqueue_error(payload)
            if error in _STALE_LEASE_ERRORS and lease_token is not None:
                self._control._clear_matching_lease(lease_token)
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
        return await self._await_result(
            cmd, command_id, peer, timeout_s, deadline=deadline
        )

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
            status, payload = await asyncio.to_thread(
                self._call,
                "GET",
                "/await",
                None,
                {"id": str(command_id), "remove": "1"},
                remaining,
                deadline,
            )
            if status == 200 and payload.get("status") == "done":
                result = payload.get("result") or {}
                # Bridge serializes ok as int 0/1; treat any falsy ok as an error.
                if not result.get("ok"):
                    raise _bridge_error(result)
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
        status, payload = await asyncio.to_thread(
            self._call,
            "POST",
            "/enqueue",
            request_payload,
            None,
            timeout_s,
            deadline,
        )
        if status != 200:
            error = self._enqueue_error(payload)
            if error in _STALE_LEASE_ERRORS and lease_token is not None:
                self._control._clear_matching_lease(lease_token)
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

    async def probe_bridge_result(
        self, cmd: str, command_id: int, peer: str
    ) -> dict[str, Any] | None:
        deadline = self._time_fn() + DEFAULT_TOOL_TIMEOUT_S
        status, payload = await asyncio.to_thread(
            self._call,
            "GET",
            "/await",
            None,
            {"id": str(command_id), "remove": "1"},
            DEFAULT_TOOL_TIMEOUT_S,
            deadline,
        )
        if status != 200:
            raise ToolError(str(payload.get("error") or payload))
        if payload.get("status") == "done":
            result = payload.get("result") or {}
            if not result.get("ok"):
                raise _bridge_error(result)
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
        self, *, timeout_s: float | None = None
    ) -> dict[str, Any]:
        args = () if timeout_s is None else (None, None, float(timeout_s))
        status, payload = await asyncio.to_thread(
            self._call, "GET", "/status", *args
        )
        if status != 200:
            raise ToolError(str(payload.get("error") or payload))
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


# Mirrors VEHICLE_CONTROL_MAX_TTL_S in addon/scripts/5_Mission/MCPClientBridge.c:114.
# The bridge only honours hold_ttl_s <= this value; above it the control silently
# falls back to VEHICLE_CONTROL_DEFAULT_TTL_S (3.0 s). Keep in sync with the bridge.
VEHICLE_CONTROL_MAX_TTL_S = 30.0


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


def _current_launch_logs(profiles_dir: str, start_epoch: float | None) -> list[str]:
    """Return RPT/script files from the current launch, never historic dumps.

    Without a launch timestamp, keep the newest .rpt and the newest .log by
    name suffix (not a time cluster), so a quiet current file is not dropped.
    """
    paths = log_tail.resolve_log_files(profiles_dir)
    dated: list[tuple[float, str]] = []
    for path in paths:
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
    }
    if scanned is not None:
        response["scanned"] = scanned
    return response


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
            "players_at_least, players_at_most, log_matches"
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
    timeout_s = min(timeout_value, WAIT_FOR_MAX_TIMEOUT_S)
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
            raise ToolError("bad_args: lookback_lines must be in 0..2000")
        if lookback_from not in WAIT_FOR_LOOKBACK_FROM:
            raise ToolError('bad_args: lookback_from must be "lines" or "launch"')

    started = time.monotonic()
    deadline = started + timeout_s
    probes = 0
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
        async with runtime.tool_lock:
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
        async with runtime.tool_lock:
            probes += 1
            remaining = deadline - time.monotonic()
            if condition in {"players_at_least", "players_at_most"}:
                probe_timeout = min(DEFAULT_TOOL_TIMEOUT_S, max(remaining, POLL_INTERVAL_S))
                try:
                    result = await runtime.call_bridge(
                        "query_all_players", {}, "server", probe_timeout
                    )
                except ToolError as exc:
                    message = str(exc)
                    if message.startswith("timeout waiting for"):
                        suffix = ""
                        if "; " in message:
                            suffix = "; " + message.split("; ", 1)[1]
                        raise ToolError(
                            f"wait_for timed out waiting for {condition}{suffix}"
                        ) from None
                    if message.startswith("version_blocked") or message.startswith(
                        "game_not_ready"
                    ) or message in {
                        "daemon_unavailable",
                        "version_blocked",
                    }:
                        raise
                    if "query_all_players" in message:
                        raise ToolError(
                            message.replace("query_all_players", "wait_for")
                        ) from None
                    raise
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
            )
        # Sleep outside the lock. Do not wrap this loop in tool_lock.
        await asyncio.sleep(poll_interval_s)

    return _wait_for_response(
        condition=condition,
        started=started,
        probes=probes,
        observed=observed,
        satisfied=False,
        scanned=scan_summary(),
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


def _box_head_is(box: dict[str, Any], session_id: str) -> bool:
    queue = box.get("queue")
    if not isinstance(queue, list) or not queue:
        return False
    head = queue[0]
    if not isinstance(head, dict):
        return False
    session = head.get("session")
    return session in {session_id, session_id[:12]}


def _box_ready_for(box: dict[str, Any], session_id: str) -> bool:
    if box.get("occupied") is True:
        return False
    return _box_head_is(box, session_id)


def _enrich_active_run_result(
    result: dict[str, Any],
    box: dict[str, Any],
    *,
    caller_session: str | None = None,
) -> dict[str, Any]:
    extra = occupancy_error_fields(box, caller_session=caller_session)
    payload = dict(result)
    payload.update(extra)
    payload["run_id"] = None
    payload["status"] = "failed"
    payload["error_code"] = "active_run_exists"
    return payload


def _failed_active_run_result(
    *,
    project: str,
    mode: str,
    box: object,
    started: float,
    caller_session: str | None = None,
) -> dict[str, Any]:
    extra = occupancy_error_fields(box, caller_session=caller_session)
    return {
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
    }


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


async def execute_wait_for_box(
    client: Any,
    wait_s: float,
    *,
    sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    time_fn: Callable[[], float] | None = None,
    poll_interval_s: float = BOX_WAIT_POLL_S,
) -> dict[str, Any]:
    """Wait until the box is free and this waiter is FIFO head.

    Probes under ``tool_lock`` and sleeps outside it, same rule as
    ``wait_for`` / ``ui_dialog``. Does not take a lease and does not
    require the caller to heartbeat.
    """

    sleeper = sleep_fn or asyncio.sleep
    clock = time_fn or time.monotonic
    poll = max(float(poll_interval_s), BOX_WAIT_MIN_POLL_S)
    deadline = clock() + float(wait_s)
    ticket: str | None = None
    box = empty_box(occupied=True)
    session_id = str(getattr(getattr(client, "identity", None), "session_id", "") or "")
    try:
        while True:
            async with client.tool_lock:
                status = await client.session_box_status(wait=True, ticket=ticket)
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
            if wait_error == "box_queue_saturated":
                return {
                    "ok": False,
                    "ticket": ticket,
                    "box": box,
                    "error": "box_queue_saturated",
                }
            ready = (
                isinstance(ticket, str)
                and ticket
                and wait_error is None
                and _box_ready_for(box, session_id)
            )
            if ready:
                async with client.tool_lock:
                    claimed = await client.session_box_status(
                        wait=True, ticket=ticket, claim=True
                    )
                if isinstance(claimed, dict):
                    box = _box_from_status(claimed)
                return {"ok": True, "ticket": ticket, "box": box}
            remaining = deadline - clock()
            if remaining <= 0.0:
                return {"ok": False, "ticket": ticket, "box": box}
            await sleeper(min(poll, remaining))
    except BaseException:
        if ticket:
            try:
                async with client.tool_lock:
                    await client.session_box_status(done=True, ticket=ticket)
            except Exception:
                pass
        raise


def _session_status_blocked_on(status: dict[str, Any]) -> str | None:
    """Return the next queue a caller should join, if a resource is busy."""

    if isinstance(status.get("owner"), dict):
        return (
            "session lease; next: call session_acquire_wait(purpose=...) "
            "to join the lease FIFO"
        )
    box = status.get("box")
    if isinstance(box, dict) and box.get("occupied") is True:
        return (
            "DayZ test box; next: call dayz_test_run(..., wait_for_box_s=<n>) "
            "to join the box FIFO"
        )
    return None


def build_app(config: ServerConfig) -> tuple[FastMCP, Any]:
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
            "session_release. Use dayz_test_run / dayz_test_stop for lifecycle. "
            "Spawn: playbook_run(name=\"place_safely\", "
            "params={\"x\":..,\"z\":..}) before a new site; "
            "pos=[x, surface_query.y, z]; y=0 is ground; example "
            "type=CivilianSedan; living infected flags=3108 "
            "(ECE_PLACE_ON_SURFACE|ECE_INITAI|ECE_CREATEPHYSICS). "
            "wait_for/logs_since read script/RPT only — player chat is not "
            "there; with -adminlog the server .ADM has chat, no tool reads it. "
            "wait_for(log_matches) lookback_lines=200 includes the last N "
            "lines already on disk so a sequential action_use then wait_for "
            "does not miss a ~200ms response. action_use v1: held item is "
            "null; componentIndex=-1; classname is exact GetType()."
        ),
        lifespan=lifespan,
    )

    def _client_runtime() -> ClientRuntime:
        if config.mode != "client" or not isinstance(runtime, ClientRuntime):
            raise ToolError("session_tools_require_client_mode")
        return runtime

    @app.tool(
        description="LOW-LEVEL: prefer session_acquire_wait. Acquire or join the FIFO lease."
    )
    async def session_acquire(purpose: str) -> dict[str, Any]:
        if not isinstance(purpose, str) or not purpose.strip():
            raise ToolError("bad_purpose")
        client = _client_runtime()
        async with client.tool_lock:
            return await client.session_acquire(purpose.strip())

    @app.tool(
        description="LOW-LEVEL: prefer session_acquire_wait. Wait up to 30s for this client's FIFO ticket."
    )
    async def session_wait(ticket: str, timeout_s: float = 30.0) -> dict[str, Any]:
        if not isinstance(ticket, str) or not ticket:
            raise ToolError("bad_ticket")
        client = _client_runtime()
        async with client.tool_lock:
            return await client.session_wait(
                ticket, _require_range(timeout_s, 0.0, 30.0, "bad_wait_timeout")
            )

    @app.tool(
        description=(
            "Preferred: use this, not session_acquire. Wait in the FIFO until "
            "this request acquires the lease or its maximum wait expires; "
            "never returns a queued result. Lease TTL is "
            f"{config.session_ttl_s:g} s; renewal is internal."
        )
    )
    async def session_acquire_wait(
        purpose: str,
        max_wait_s: float | None = None,
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

        client = _client_runtime()
        async with client.tool_lock:
            return await client.session_acquire_wait(
                purpose.strip(), validated_wait, report
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
            return await client.session_cancel(ticket)

    @app.tool(
        description="LOW-LEVEL: prefer session_acquire_wait. Renew an active lease while exclusive work is in progress."
    )
    async def session_heartbeat(lease_token: str) -> dict[str, Any]:
        if not isinstance(lease_token, str) or not lease_token:
            raise ToolError("bad_lease_token")
        client = _client_runtime()
        async with client.tool_lock:
            return await client.session_heartbeat(lease_token)

    @app.tool(description="Release this client's active lease and run bounded cleanup.")
    async def session_release(lease_token: str) -> dict[str, Any]:
        if not isinstance(lease_token, str) or not lease_token:
            raise ToolError("bad_lease_token")
        client = _client_runtime()
        async with client.tool_lock:
            return await client.session_release(lease_token)

    @app.tool(
        description=(
            "Read redacted daemon/queue/self coordination state, including "
            "box occupancy (managed runs, foreign DayZDiag, ports_in_use, "
            "and the box wait FIFO). blocked_on names the resource and next "
            "queue, or is null when neither lease nor box is busy. Lease TTL "
            f"is {config.session_ttl_s:g} s; renewal is internal."
        )
    )
    async def session_status() -> dict[str, Any]:
        client = _client_runtime()
        async with client.tool_lock:
            status = await client.session_status()
            status["blocked_on"] = _session_status_blocked_on(status)
            return status

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
            "Queue and run an approved DayZ test project; lease ownership and "
            "heartbeat remain internal to the tool. wait_for_box_s>0 waits "
            "until session_status.box is free (FIFO, no tool_lock while "
            f"sleeping). 0 is the immediate reject. wait_for_box_s must be <= "
            f"{BOX_WAIT_MAX_S:g}. Choose port= from "
            "session_status.box.ports_in_use."
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
        port: int = 2302,
        width: int = 1920,
        height: int = 1080,
        player_name: str = "Dev",
        server_wait_s: int = 60,
        wait_for_box_s: float = 0.0,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        wait_s = _parse_wait_for_box_s(wait_for_box_s)
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
            if wait_s > 0.0:
                await report("queued", "waiting for box")
                waited = await execute_wait_for_box(client, wait_s)
                ticket = waited.get("ticket")
                box_ticket = ticket if isinstance(ticket, str) else None
                if not waited.get("ok"):
                    failed = _failed_active_run_result(
                        project=project,
                        mode=mode,
                        box=waited.get("box"),
                        started=started,
                        caller_session=caller_session,
                    )
                    if waited.get("error") == "box_queue_saturated":
                        failed["error_code"] = "box_queue_saturated"
                        failed["hint"] = "retry with wait_for_box_s=<n>"
                    return failed
                if box_ticket:
                    claim_task = asyncio.create_task(
                        _heartbeat_box_claim(client, box_ticket)
                    )

            execute_error: dayz_test_tool.DayzTestToolError | None = None
            result: dict[str, Any] | None = None
            async with client.tool_lock:
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
                            port=port,
                            width=width,
                            height=height,
                            player_name=player_name,
                            server_wait_s=server_wait_s,
                            progress_cb=report,
                        )
                except dayz_test_tool.DayzTestToolError as error:
                    execute_error = error
                except ToolError:
                    raise
                except Exception as exc:
                    # The ToolError carries the exception TYPE only. The message
                    # can hold host paths, so it must not cross the MCP wire; FastMCP
                    # serializes str(exc) alone. `from exc` keeps the cause in
                    # __cause__ for LOCAL diagnosis (needed to see why build:true failed), not for the wire.
                    _log_opaque_failure(client, "dayz_test_run", exc)
                    raise ToolError(f"dayz_test_failed:{type(exc).__name__}") from exc
            if execute_error is not None:
                if execute_error.code == "active_run_exists":
                    return _failed_active_run_result(
                        project=project,
                        mode=mode,
                        box=await peek_box(),
                        started=started,
                        caller_session=caller_session,
                    )
                raise ToolError(execute_error.code) from None
            if (
                isinstance(result, dict)
                and result.get("error_code") == "active_run_exists"
            ):
                return _enrich_active_run_result(
                    result,
                    await peek_box(),
                    caller_session=caller_session,
                )
            if result is None:
                raise ToolError("dayz_test_failed:RuntimeError")
            return result
        finally:
            if claim_task is not None:
                claim_task.cancel()
                try:
                    await claim_task
                except (asyncio.CancelledError, Exception):
                    pass
            if box_ticket:
                try:
                    async with client.tool_lock:
                        await client.session_box_status(done=True, ticket=box_ticket)
                except Exception:
                    pass

    @app.tool(
        description=(
            "Queue adoption and shutdown of one exact approved DayZ run."
        )
    )
    async def dayz_test_stop(
        run_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        client = _client_runtime()

        async def report(stage: str, message: str | None) -> None:
            await report_dayz_progress(ctx, stage, message)

        async with client.tool_lock:
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
                # The ToolError carries the exception TYPE only. The message
                # can hold host paths, so it must not cross the MCP wire; FastMCP
                # serializes str(exc) alone. `from exc` keeps the cause in
                # __cause__ for LOCAL diagnosis (needed to see why build:true failed), not for the wire.
                _log_opaque_failure(client, "dayz_test_stop", exc)
                raise ToolError(f"dayz_test_failed:{type(exc).__name__}") from exc

    @app.tool(description="Read the authoritative server-side player state.")
    async def query_player_state(timeout_s: float = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        async with runtime.tool_lock:
            return await runtime.call_bridge("query_player_state", {}, "server", _timeout(timeout_s))

    @app.tool(description="Read the authoritative state of every connected player.")
    async def query_all_players(timeout_s: float = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
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
            "string or the decoded object {path:[offset,size,identity]}."
        )
    )
    async def logs_since(
        marker: str | dict[str, Any] | None = None,
        max_lines: int = 200,
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
            "flag integer, not an angle; 0 uses the bridge default RF_DEFAULT."
        )
    )
    async def world_spawn(
        type: str,
        pos: list[float],
        flags: int = 0,
        rotation: int = 0,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        args = {"type": type, "pos": _require_vec3(pos, "pos"), "flags": int(flags), "rotation": int(rotation)}
        async with runtime.tool_lock:
            return await runtime.call_bridge("world_spawn", args, "server", _timeout(timeout_s))

    @app.tool(
        description=(
            "Requires a lease (session_acquire_wait). Delete an object "
            "previously returned by world_spawn.object_id. Returns ok with "
            "deleted=0 (success, nothing removed) when the id is unknown or "
            "already gone — check the `deleted` field to know whether anything "
            "was actually removed."
        )
    )
    async def object_delete(object_id: int, timeout_s: float = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
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
            "non-empty uid targets that identity."
        )
    )
    async def notify_players(
        show_time: float,
        title: str,
        detail: str = "",
        icon: str = "",
        uid: str = "",
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
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
        async with runtime.tool_lock:
            return await runtime.call_bridge("notify_players", args, "server", _timeout(timeout_s))

    @app.tool(
        description=(
            "Requires a lease (session_acquire_wait). Seat the first "
            "player in the driver seat of a vehicle near pos. seated=1 "
            "confirms the command was accepted, not the final seated state. "
            "engine_set and vehicle_control require client-side ownership; "
            "establish it with vehicle_get_in_client."
        )
    )
    async def vehicle_enter(pos: list[float], timeout_s: float = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
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
        from_pos: list[float],
        to: list[float],
        method: str = "rvproxy",
        ignore: str = "",
        radius: float = 0.05,
        intersect: str = "view",
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
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

    @app.tool(description="Read telemetry through object_at or fixture_jsonl bridge modes.")
    async def telemetry_read(
        mode: str,
        type: str = "",
        pos: list[float] | None = None,
        radius: float = 0.0,
        path: str = "",
        max_lines: int = 0,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
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
        pos: list[float],
        component: int = -1,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
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
        pos: list[float],
        radius: float = 100.0,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
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
        x: float,
        z: float,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        args = {
            "x": _finite_float(x, _bad_args("x", x, "be a finite number")),
            "z": _finite_float(z, _bad_args("z", z, "be a finite number")),
        }
        async with runtime.tool_lock:
            return await runtime.call_bridge("surface_query", args, "server", _timeout(timeout_s))

    # Mutating teleport of a connected player.
    @app.tool(
        description=(
            "Requires a lease (session_acquire_wait). Teleport a "
            "connected player to pos. y==0 snaps to SurfaceY (vanilla "
            "script-console contract). uid empty (default) targets the first "
            "human; a non-empty uid selects by PlayerIdentity.GetPlainId()."
        )
    )
    async def player_teleport(
        pos: list[float],
        uid: str = "",
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(uid, str):
            raise ToolError(_bad_args("uid", uid, "be a string"))
        args: dict[str, Any] = {"pos": _require_vec3(pos, "pos")}
        if uid != "":
            args["uid"] = uid
        async with runtime.tool_lock:
            return await runtime.call_bridge("player_teleport", args, "server", _timeout(timeout_s))

    # Read or write entity animation phase.
    @app.tool(
        description=(
            f"{LEASE_TOOL_LINE} "
            "Read or set an entity animation phase by classname near pos. "
            "phase is a unitless value passed unchanged to Entity.SetAnimationPhase; "
            "omit phase to read with GetAnimationPhase."
        )
    )
    async def object_anim(
        type: str,
        pos: list[float],
        source: str,
        phase: float | None = None,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(type, str) or type == "":
            raise ToolError(_bad_args("type", type, "be a non-empty string"))
        if not isinstance(source, str) or source == "":
            raise ToolError(_bad_args("source", source, "be a non-empty string"))
        args: dict[str, Any] = {
            "type": type,
            "pos": _require_vec3(pos, "pos"),
            "source": source,
        }
        if phase is not None:
            args["phase"] = _finite_float(
                phase, _bad_args("phase", phase, "be a finite unitless number")
            )
        async with runtime.tool_lock:
            return await runtime.call_bridge("object_anim", args, "server", _timeout(timeout_s))

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
        pos: list[float],
        heading: float | None = None,
        speed: float | None = None,
        mode: str | None = None,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
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
            args["heading"] = _finite_float(
                heading, _bad_args("heading", heading, "be a finite number of degrees")
            )
            args["speed"] = _finite_float(
                speed, _bad_args("speed", speed, "be a finite number")
            )
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
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
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

    # Memory points + bounding_center. Missing points are exists:false, ok:true.
    @app.tool(
        description=(
            "Inspect an object near pos: memory points (exists+pos) and optional "
            "bounding_center. Absent memory points return exists:false with ok:true."
        )
    )
    async def object_inspect(
        type: str,
        pos: list[float],
        want: list[str],
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(type, str) or type == "":
            raise ToolError(_bad_args("type", type, "be a non-empty string"))
        if not isinstance(want, list) or len(want) == 0:
            raise ToolError(
                _bad_args("want", want, "be a non-empty list of non-empty strings")
            )
        if any(not isinstance(item, str) or item == "" for item in want):
            raise ToolError(
                _bad_args("want", want, "be a non-empty list of non-empty strings")
            )
        args = {"type": type, "pos": _require_vec3(pos, "pos"), "want": list(want)}
        async with runtime.tool_lock:
            return await runtime.call_bridge("object_inspect", args, "server", _timeout(timeout_s))

    @app.tool(
        description=(
            "Query world entities around pos within radius (0 < r <= 200). "
            "Returns the nearest entries up to limit (default 32, max 128) as "
            "{type, classname, pos, distance} sorted by distance ascending, plus "
            "count_total before the cut. No classname filter; raw nearby objects. "
            "Absent entities travel as []."
        )
    )
    async def entities_query(
        pos: list[float],
        radius: float,
        limit: int = 32,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
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
            return await runtime.call_bridge("entities_query", args, "server", _timeout(timeout_s))

    @app.tool(
        description=(
            "Requires a lease (session_acquire_wait). Set server world "
            "date/time and optionally the time multiplier."
        )
    )
    async def world_time_set(
        year: int,
        month: int,
        day: int,
        hour: int,
        minute: int,
        time_multiplier: float | None = None,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
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
            args["time_multiplier"] = multiplier
        async with runtime.tool_lock:
            result = await runtime.call_bridge(
                "world_time_set", args, "server", _timeout(timeout_s)
            )

        applied = result.get("applied")
        requested_date = {
            "year": year_value,
            "month": month_value,
            "day": day_value,
            "hour": hour_value,
            "minute": minute_value,
        }
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
        response["date_applied"] = date_applied
        response["multiplier_applied"] = multiplier_applied
        return response

    @app.tool(
        description=(
            "Requires a lease (session_acquire_wait). Set server weather "
            "overcast, rain, or fog forecast values. time is the transition "
            "duration in seconds; min_duration is the minimum hold duration "
            "in seconds passed to the weather phenomenon Set method."
        )
    )
    async def world_weather_set(
        overcast: float | None = None,
        rain: float | None = None,
        fog: float | None = None,
        time: float = 0.0,
        min_duration: float = 0.0,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
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
        if overcast_value is not None:
            args["overcast"] = _require_range(overcast_value, 0.0, 1.0, "bad_overcast")
        if rain_value is not None:
            args["rain"] = _require_range(rain_value, 0.0, 1.0, "bad_rain")
        if fog_value is not None:
            args["fog"] = _require_range(fog_value, 0.0, 1.0, "bad_fog")
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
        "cam_mode look_at is accepted as an alias of lookat and is sent as lookat."
    ))
    async def camera_set(
        cam_mode: str = "orient",
        cam_pos: list[float] | None = None,
        cam_orientation: list[float] | None = None,
        look_at: list[float] | None = None,
        cam_matrix: list[float] | None = None,
        fov: float = 0.0,
        settle_ticks: int = 3,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
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

    @app.tool(description="Read the active client camera state through the existing camera_get bridge command.")
    async def camera_get(cam_mode: str = "get", timeout_s: float = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        args = {"cam_mode": cam_mode} if cam_mode else {}
        async with runtime.tool_lock:
            return await runtime.call_bridge("camera_get", args, "client", _timeout(timeout_s))

    @app.tool(description=(
        f"{LEASE_TOOL_LINE} Restore local player simulation, input, HUD, and "
        "release the camera. camera_set has no off mode."
    ))
    async def restore_gameplay(timeout_s: float = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        async with runtime.tool_lock:
            return await runtime.call_bridge("restore_gameplay", {}, "client", _timeout(timeout_s))

    @app.tool(description=(
        "Capture a screenshot from the DayZDiag window. Returns inline JPEG ImageContent fit to the "
        "client's MAX_MCP_OUTPUT_TOKENS budget (default 25000 -> ~600px wide; raise that client env var for bigger inline frames: 50000 -> ~860px/2x px, 75000 -> ~1070px/3x, 100000 -> ~native; max_tokens spends LESS than the cap, above-cap is clamped). Use scale='full' to spend a raised inline budget on resolution (the default scale='small' is a hard 512px cap). crop ('center', 'center:0.4', or normalized 'l,t,r,b') zooms on the subject; "
        "for optical zoom set a narrow fov in radians via camera_set first. fmt='webp' is ~15% smaller (opt-in; Claude Code has known webp MIME bugs, JPEG stays default). save_fullres=True also writes the "
        "native-resolution frame to disk and returns its path in a JSON text block — read that file for "
        "fine detail, bypassing the inline token budget. Without window focus, the frame can be frozen. "
        "With two DayZ clients, capture targets the live run's client through cmdline_match/client_pid."
    ))
    async def capture_screenshot(
        scale: str = "small",
        max_tokens: int = mcp_capture.DEFAULT_MAX_TOKENS,
        frames: int = mcp_capture.DEFAULT_FRAME_COUNT,
        process_name: str = "DayZDiag_x64",
        fmt: str = mcp_capture.DEFAULT_FORMAT,
        quality: int = mcp_capture.DEFAULT_QUALITY,
        crop: str = "",
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
        if not save_fullres:
            # Backward-compatible single-Image return (now JPEG instead of PNG).
            return image
        meta = {"fullres_path": result.get("fullres_path"), **result.get("meta", {})}
        return [image, json.dumps(meta)]

    if config.enable_exec_enforce:

        @app.tool(description="Execute an exact allowlisted Enforce script expression through the server bridge.")
        async def exec_enforce(expr: str, main_fn: str = "", timeout_s: float = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
            args = {"expr": expr, "main_fn": main_fn}
            async with runtime.tool_lock:
                return await runtime.call_exec_enforce(args, _timeout(timeout_s))

    @app.tool(
        description=(
            "Inspect peer liveness, version_state, and ready "
            "{ready, reason=ready|no_run|server_poll_stale|client_not_polling|"
            "client_legacy_blocked|version_mismatch}. "
            "daemon_modules.stale = source newer than daemon, not a crash."
        )
    )
    async def bridge_status() -> dict[str, Any]:
        runtime.touch()
        return await runtime.bridge_status_payload()

    @app.tool(
        description=(
            "Requires a lease (session_acquire_wait). Seat the connected "
            "client in a nearby vehicle (client-side ownership get-in)."
        )
    )
    async def vehicle_get_in_client(pos: list[float], timeout_s: float = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        args = {"pos": _require_vec3(pos, "pos")}
        async with runtime.tool_lock:
            return await runtime.call_bridge("vehicle_get_in_client", args, "client", _timeout(timeout_s))

    @app.tool(
        description=(
            "Requires a lease (session_acquire_wait). Start or stop the "
            "owned vehicle's engine. This requires client-side ownership "
            "established with vehicle_get_in_client. command_sent reports "
            "bridge acceptance; state_confirmed reports matching engine "
            "readback when available, otherwise null (accepted, not confirmed)."
        )
    )
    async def engine_set(mode: str, timeout_s: float = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        if mode not in ("start", "stop"):
            raise ToolError("bad_mode")
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
            "owner-side driving control (held until released or deadman TTL)."
        )
    )
    async def vehicle_control(
        throttle: float = 0.0,
        steer: float = 0.0,
        brake: float = 0.0,
        handbrake: float = 0.0,
        hold_ttl_s: float = 0.0,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
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
            raise ToolError("bad_hold_ttl_s")
        args = {"throttle": t, "steer": s, "brake": b, "handbrake": h, "hold_ttl_s": ttl}
        async with runtime.tool_lock:
            return await runtime.call_bridge("vehicle_control", args, "client", _timeout(timeout_s))

    @app.tool(description="Read owner-side vehicle telemetry (speed, gear, engine, pos, ownership).")
    async def vehicle_telemetry(timeout_s: float = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        async with runtime.tool_lock:
            return await runtime.call_bridge("vehicle_telemetry", {}, "client", _timeout(timeout_s))

    @app.tool(
        description=f"{LEASE_TOOL_LINE} Capture and read an atomic owner-client vehicle trace."
    )
    async def vehicle_trace(
        mode: str,
        trace_id: str = "",
        cursor: int = 0,
        limit: int = 64,
        sample_hz: int = 20,
        max_samples: int = 4096,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
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
            "Requires a lease (session_acquire_wait). Release sustained "
            "vehicle control (stop driving)."
        )
    )
    async def vehicle_release(timeout_s: float = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        async with runtime.tool_lock:
            return await runtime.call_bridge("vehicle_release", {}, "client", _timeout(timeout_s))

    @app.tool(description=(
        "Walk the client widget tree from an optional named root, or the active "
        "scripted menu when path is empty. Each node reports name, type, user_id, "
        "visible, visible_hierarchy, disabled, text, text_readable and screen box. "
        "TextWidget/RichTextWidget have no getter: text_readable is false and text "
        "is empty, never a fake label."
    ))
    async def ui_tree(
        path: str = "",
        limit: int = 256,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(path, str):
            raise ToolError(_bad_args("path", path, "be a string"))
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 512:
            raise ToolError(_bad_args("limit", limit, "be an int from 1 to 512"))
        args: dict[str, Any] = {"limit": int(limit)}
        if path != "":
            args["path"] = path
        async with runtime.tool_lock:
            return await runtime.call_bridge("ui_tree", args, "client", _timeout(timeout_s))

    @app.tool(description=(
        f"{LEASE_TOOL_LINE} Write text on a client EditBox/MultilineEditBox/"
        "Button/Text widget by name. Other types return text_not_writable."
    ))
    async def ui_set_text(
        path: str,
        text: str,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(path, str) or path == "":
            raise ToolError(_bad_args("path", path, "be a non-empty string"))
        if not isinstance(text, str):
            raise ToolError(_bad_args("text", text, "be a string"))
        args = {"path": path, "text": text}
        async with runtime.tool_lock:
            return await runtime.call_bridge("ui_set_text", args, "client", _timeout(timeout_s))

    @app.tool(description=(
        f"{LEASE_TOOL_LINE} Click a client widget by name. button is "
        "0=left, 1=right, 2=middle."
    ))
    async def ui_click(
        path: str,
        button: int = 0,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(path, str) or path == "":
            raise ToolError(_bad_args("path", path, "be a non-empty string"))
        if not isinstance(button, int) or isinstance(button, bool) or button < 0 or button > 2:
            raise ToolError(_bad_args("button", button, "be an int from 0 to 2"))
        args = {"path": path, "button": int(button)}
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
        mode: str = "reload",
        limit: int = 256,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
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
        "widget that cannot take focus (plain TextWidget, NoFocus, disabled) "
        "returns found=true and error=focus_not_taken. ui_click does not "
        "focus: it calls OnClick directly."
    ))
    async def ui_focus(
        path: str,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(path, str) or path == "":
            raise ToolError(_bad_args("path", path, "be a non-empty string"))
        args = {"path": path}
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
        timeout_s: float = 60.0,
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
        "without keyboard. Confirm with wait_for(condition=log_matches)."
    ))
    async def action_use(
        action: str,
        classname: str = "",
        pos: list[float] | None = None,
        radius: float = 5.0,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(action, str) or action == "":
            raise ToolError(_bad_args("action", action, "be a non-empty string"))
        if not isinstance(classname, str):
            raise ToolError(_bad_args("classname", classname, "be a string"))
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
        async with runtime.tool_lock:
            return await runtime.call_bridge("action_use", args, "client", _timeout(timeout_s))

    @app.tool(
        description=(
            "Block until a condition holds. condition ENUM: players_at_least, "
            "players_at_most, log_matches. pattern is a plain SUBSTRING, not a "
            r"regex: pass '[MOD]', never '\[MOD\]'. For log_matches, marker "
            "is the exact cursor returned by logs_since; when present, "
            "lookback_lines and lookback_from are ignored. Without marker, "
            "lookback_lines (default 200) rewinds N lines, or "
            "lookback_from='launch' scans from byte 0. That heuristic can match "
            "a line written before the caller's action and cause a false positive. "
            "On timeout still returns "
            "ok: true with satisfied: false -- gate on satisfied, not ok. "
            "scanned reports which log files were read and how many "
            "lines each gave, so a no-match is visible as a no-match."
        )
    )
    async def wait_for(
        condition: Literal["players_at_least", "players_at_most", "log_matches"],
        value: int = 0,
        pattern: str = "",
        timeout_s: float = 180.0,
        poll_interval_s: float = 2.0,
        lookback_lines: int = 200,
        lookback_from: Literal["lines", "launch"] = "lines",
        marker: str | dict[str, Any] | None = None,
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
            "Appends to a local shared inbox; ids cannot collide. Works "
            "even when the game and daemon are down."
        )
    )
    async def pipeline_feedback(
        kind: Literal["bug", "request", "finding", "tool_contribution"],
        title: str,
        body: str,
        project: str = "",
    ) -> dict[str, Any]:
        """File pipeline feedback from any agent session: a bug you hit, a request for a missing capability, a finding worth recording, or a tool/playbook you built (kind=tool_contribution). For contributions, reference artifacts at DURABLE paths (never session scratchpads). Appends to a local shared inbox; ids cannot collide. Works even when the game and daemon are down."""
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
        limit: int = 20,
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
            "nothing, history is append-only."
        )
    )
    async def pipeline_resolve(
        feedback_id: str,
        resolution: str,
    ) -> dict[str, Any]:
        """Triage a feedback item by appending a resolution; deletes nothing, history is append-only."""
        # The lock here only preserves the one-tool-at-a-time client invariant;
        # these tools do not call the bridge.
        async with runtime.tool_lock:
            try:
                return inbox.append_resolution(
                    feedback_id, resolution, platform=_pipeline_platform()
                )
            except ValueError as exc:
                message = str(exc)
                if message == "bad_args" or message.startswith("bad_args"):
                    raise ToolError(message) from None
                raise

    @app.tool(
        description=(
            f"{LEASE_TOOL_LINE} Run a named playbook checklist from the "
            "dictionary. Does not launch DayZ. certified is always false."
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
        not launch DayZ.
        """
        return await playbook_tool_mod.execute_playbook_run(app, name, params)

    _patch_public_argument_alias(app, "scene_raycast", "from_pos", "from")
    return app, runtime


def parse_args(argv: list[str] | None = None) -> ServerConfig:
    parser = build_server_parser()
    parser.allow_abbrev = False
    args = parser.parse_args(argv)
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
        auto_spawn_daemon=bool(args.auto_spawn_daemon),
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


def run(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
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
