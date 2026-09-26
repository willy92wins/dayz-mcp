from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Awaitable, Callable

from mcp.server.fastmcp import Context, FastMCP, Image
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

import mcp_capture
from dayz_mcp import core, daemon, dayz_test_tool, host_config, orphan_guard
from dayz_mcp.control_client import ControlClient, ControlClientError, ControlIdentity
from dayz_mcp.accredited_daemon_transport import AccreditedTransportError
from dayz_mcp.daemon_credential import (
    DaemonCredentialError,
    RefreshingDaemonCredential,
)
from dayz_mcp.daemon_policy import load_normal_daemon_policy
from dayz_mcp.core import EXPECTED_BRIDGE_VERSION
from dayz_mcp.loopback import LoopbackServer, read_key
from dayz_mcp.server_cli import build_server_parser
from dayz_mcp.session_coordination import ClientIdentity
from dayz_mcp.vehicle_trace import normalize_request


DEFAULT_TOOL_TIMEOUT_S = 15.0
POLL_INTERVAL_S = 0.05
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
})
_STALE_TICKET_ERRORS = frozenset({"ticket_expired", "ticket_invalid"})
_STALE_LEASE_ERRORS = frozenset({"lease_expired", "lease_invalid"})
_CONTROL_CLIENT_ERROR_CODES = frozenset({
    "credential_source_untrusted",
    "daemon_bad_body",
    "daemon_bad_session_response",
    "daemon_credential_desynchronized",
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
    task_label: str = ""
    session_ttl_s: float = 120.0
    runtime_dir: str | None = None
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
        return self.status()

    def ensure_peer_allowed(self, peer: str) -> None:
        snapshot = self.status()
        peer_key = "client_peer" if peer == "client" else "server_peer"
        peer_status = snapshot[peer_key]
        state = peer_status["version_state"]
        if state in {"legacy_blocked", "version_mismatch"}:
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
        status, payload = self.state.enqueue_command(cmd, args, peer=peer)
        if status != 200:
            raise ToolError(payload.get("error", f"enqueue_failed_http_{status}"))

        command_id = int(payload["id"])
        return await self.wait_for_result(cmd, command_id, peer, timeout_s)

    async def call_exec_enforce(self, args: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        self.touch()
        self.ensure_peer_allowed("server")
        status, payload = self.state.enqueue_command("exec_enforce", args, peer="server")
        if status != 200:
            raise ToolError(payload.get("error", f"enqueue_failed_http_{status}"))

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
                    raise ToolError(str(result.get("error") or result))
                return result
            await asyncio.sleep(POLL_INTERVAL_S)

        raise ToolError(f"timeout waiting for {cmd} id={command_id}; {self.liveness_message(peer)}")

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
    version gate, exec chokepoint, E4 lock and idle watchdog; this only forwards
    (POST /enqueue + GET /await) and reads /status. The daemon is discovered via
    GET /status and lazily spawned (detached) on first need; a connection failure
    triggers one re-spawn + retry, so an idle-reaped daemon self-heals.

    Exposes the same surface build_app's tools call on Runtime (tool_lock, touch,
    call_bridge, call_exec_enforce, bridge_status_payload). ``spawn_fn``/``probe_fn``
    are injectable so tests exercise discovery/spawn without a real subprocess.
    """

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
            or config.auto_spawn_daemon != provenance.auto_spawn_daemon
        ):
            raise host_config.HostConfigError("daemon_provenance_conflict")
        authority_keyfile = host_config.require_matching_keyfile(
            provenance.keyfile, provenance.keyfile
        )
        keyfile = host_config.require_matching_keyfile(
            required_keyfile(config), authority_keyfile
        )
        self._credential = RefreshingDaemonCredential(
            policy=daemon_policy,
            initial_key=config.key,
        )
        self.host = "127.0.0.1"
        self.port = provenance.port
        self.base = f"http://{self.host}:{self.port}"
        self._daemon_executable = provenance.native_executable
        self._daemon_argv = tuple(provenance.argv)
        self._daemon_cwd = provenance.cwd
        self._auto_spawn_daemon = provenance.auto_spawn_daemon
        self._log = config.log_sink or (lambda message: print(message, file=sys.stderr, flush=True))
        self._spawn_lock = threading.Lock()
        self._probe = probe_fn
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
        self._control = ControlClient(
            policy=daemon_policy,
            identity=ControlIdentity(**self.identity.to_payload()),
            credential=self._credential,
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
            try:
                daemon_ready = (
                    retryable and await asyncio.to_thread(self._ensure_daemon)
                )
            except DaemonCredentialError as credential_error:
                raise ToolError(credential_error.code) from None
            except AccreditedTransportError as transport_error:
                raise ToolError(
                    "daemon_identity_unverified"
                    if transport_error.code == "daemon_identity_unverified"
                    else "daemon_unavailable"
                ) from None
            if daemon_ready:
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
                deadline=deadline,
                expected_executable=self._daemon_executable,
                expected_argv=list(self._daemon_argv),
                expected_cwd=self._daemon_cwd,
            )
        try:
            status, body = self._credential.exchange(
                request_fn=orphan_guard.verified_daemon_http_request,
                method="GET",
                path="/status",
                query={},
                body=None,
                headers={},
                deadline=deadline,
                max_response_bytes=orphan_guard.MAX_STATUS_BODY_BYTES,
                time_fn=self._time_fn,
            )
        except DaemonCredentialError:
            raise
        except AccreditedTransportError as error:
            if error.code == "daemon_identity_unverified":
                raise
            if error.request_stage == "pre_request" and error.http_bytes_sent == 0:
                return False
            raise
        return orphan_guard.status_response_is_healthy(status, body)

    def _ensure_daemon(self) -> bool:
        deadline = self._time_fn() + self._startup_budget_s
        if self._daemon_healthy(deadline):
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
                return True
            if self._time_fn() >= deadline:
                return False
            self._log(f"CLIENT: no daemon on 127.0.0.1:{self.port}; spawning")
            self._spawn_fn()
            while True:
                remaining = deadline - self._time_fn()
                if remaining <= 0.0:
                    break
                if self._daemon_healthy(deadline):
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
        status, response_body = self._credential.exchange(
            request_fn=orphan_guard.verified_daemon_http_request,
            method=method,
            path=path,
            query=dict(query or {}),
            body=data,
            headers=headers,
            deadline=self._time_fn() + float(timeout),
            time_fn=self._time_fn,
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
    ) -> tuple[int, dict[str, Any]]:
        # Replay only an accredited pre-request failure that sent zero HTTP bytes.
        # All ambiguous post-request failures fail closed without another attempt.
        try:
            return self._request_once(method, path, payload, query, timeout)
        except DaemonCredentialError as error:
            raise ToolError(error.code) from None
        except AccreditedTransportError as error:
            if error.code == "daemon_identity_unverified":
                raise ToolError("daemon_identity_unverified") from None
            retryable = (
                error.request_stage == "pre_request"
                and error.http_bytes_sent == 0
            )
            if not retryable:
                raise ToolError("daemon_response_ambiguous") from None
            try:
                daemon_ready = self._ensure_daemon()
            except DaemonCredentialError as credential_error:
                raise ToolError(credential_error.code) from None
            except AccreditedTransportError as transport_error:
                raise ToolError(
                    "daemon_identity_unverified"
                    if transport_error.code == "daemon_identity_unverified"
                    else "daemon_unavailable"
                ) from None
            if not daemon_ready:
                raise ToolError("daemon_unavailable") from None
            try:
                return self._request_once(method, path, payload, query, timeout)
            except DaemonCredentialError as credential_error:
                raise ToolError(credential_error.code) from None
            except AccreditedTransportError as retry_error:
                if retry_error.code == "daemon_identity_unverified":
                    raise ToolError("daemon_identity_unverified") from None
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
            self._call, "POST", "/enqueue", request_payload
        )
        if status != 200:
            error = self._enqueue_error(payload)
            if error in _STALE_LEASE_ERRORS and lease_token is not None:
                self._control._clear_matching_lease(lease_token)
            raise ToolError(error)
        if "id" not in payload:
            raise ToolError("daemon_bad_enqueue_response")
        command_id = int(payload["id"])
        return await self._await_result(cmd, command_id, peer, timeout_s)

    async def call_exec_enforce(self, args: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        return await self.call_bridge("exec_enforce", args, "server", timeout_s)

    def _enqueue_error(self, payload: dict[str, Any]) -> str:
        return _remote_error_code(payload)

    async def _await_result(self, cmd: str, command_id: int, peer: str, timeout_s: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            status, payload = await asyncio.to_thread(
                self._call, "GET", "/await", None, {"id": str(command_id), "remove": "1"}
            )
            if status == 200 and payload.get("status") == "done":
                result = payload.get("result") or {}
                # Bridge serializes ok as int 0/1; treat any falsy ok as an error.
                if not result.get("ok"):
                    raise ToolError(str(result.get("error") or result))
                return result
            await asyncio.sleep(POLL_INTERVAL_S)
        raise ToolError(f"timeout waiting for {cmd} id={command_id}; {await self._liveness_message(peer)}")

    async def _liveness_message(self, peer: str) -> str:
        try:
            status = await self.bridge_status_payload()
        except Exception:
            return f"{peer} peer status unavailable"
        peer_key = "client_peer" if peer == "client" else "server_peer"
        peer_status = status.get(peer_key, {})
        age = peer_status.get("last_poll_age_s")
        age_text = "has never polled" if age is None else f"last poll {age:.1f}s ago"
        return (
            f"{peer} peer {age_text}; queue_depth={peer_status.get('queue_depth')}; "
            f"version_state={peer_status.get('version_state')}"
        )

    async def bridge_status_payload(self) -> dict[str, Any]:
        status, payload = await asyncio.to_thread(self._call, "GET", "/status")
        if status != 200:
            raise ToolError(str(payload.get("error") or payload))
        return payload


def required_keyfile(config: ServerConfig) -> str:
    if config.keyfile is None:
        raise ValueError("--keyfile is required")
    return config.keyfile


def _require_vec3(value: list[float] | None, name: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ToolError("bad_args" if name != "pos" else "bad_pos")
    try:
        vec = [float(value[0]), float(value[1]), float(value[2])]
    except (TypeError, ValueError) as exc:
        raise ToolError("bad_args" if name != "pos" else "bad_pos") from exc
    if not all(math.isfinite(item) for item in vec):
        raise ToolError("bad_args" if name != "pos" else "bad_pos")
    return vec


def _require_float_list(value: list[float] | None, count: int) -> list[float]:
    if not isinstance(value, list) or len(value) != count:
        raise ToolError("bad_args")
    try:
        items = [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise ToolError("bad_args") from exc
    if not all(math.isfinite(item) for item in items):
        raise ToolError("bad_args")
    return items


def _timeout(timeout_s: float) -> float:
    try:
        value = float(timeout_s)
    except (TypeError, ValueError) as exc:
        raise ToolError("bad_timeout") from exc
    if value <= 0.0 or not math.isfinite(value):
        raise ToolError("bad_timeout")
    return value


def _finite_float(value: float, error: str = "bad_args") -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise ToolError(error) from exc
    if not math.isfinite(converted):
        raise ToolError(error)
    return converted


def _optional_finite_float(value: float | None, error: str = "bad_args") -> float | None:
    if value is None:
        return None
    return _finite_float(value, error)


def _require_range(value: float, minimum: float, maximum: float, error: str = "bad_args") -> float:
    converted = _finite_float(value, error)
    if converted < minimum or converted > maximum:
        raise ToolError(error)
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
            "Expose DayZDiag through typed MCP tools. Use dayz_test_run and "
            "dayz_test_stop for queued lifecycle operations; use bridge_status "
            "to inspect server/client peer liveness."
        ),
        lifespan=lifespan,
    )

    def _client_runtime() -> ClientRuntime:
        if config.mode != "client" or not isinstance(runtime, ClientRuntime):
            raise ToolError("session_tools_require_client_mode")
        return runtime

    @app.tool(description="Acquire or join the FIFO lease for exclusive DayZ mutations.")
    async def session_acquire(purpose: str) -> dict[str, Any]:
        if not isinstance(purpose, str) or not purpose.strip():
            raise ToolError("bad_purpose")
        client = _client_runtime()
        async with client.tool_lock:
            return await client.session_acquire(purpose.strip())

    @app.tool(description="Wait up to 30 seconds for this client's FIFO ticket.")
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
            "Wait in the FIFO queue until this request acquires the lease or its "
            "maximum wait expires; never returns a queued result."
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

    @app.tool(description="Cancel this client's exact queued FIFO ticket.")
    async def session_cancel(ticket: str) -> dict[str, Any]:
        if not isinstance(ticket, str) or not ticket:
            raise ToolError("bad_ticket")
        client = _client_runtime()
        async with client.tool_lock:
            return await client.session_cancel(ticket)

    @app.tool(description="Renew an active lease while exclusive work is in progress.")
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

    @app.tool(description="Read redacted daemon/queue/self coordination state.")
    async def session_status() -> dict[str, Any]:
        client = _client_runtime()
        async with client.tool_lock:
            return await client.session_status()

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
            "heartbeat remain internal to the tool."
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
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        client = _client_runtime()

        async def report(stage: str, message: str | None) -> None:
            await report_dayz_progress(ctx, stage, message)

        async with client.tool_lock:
            try:
                return await dayz_test_tool.execute_dayz_test_run(
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
                raise ToolError(error.code) from None
            except ToolError:
                raise
            except Exception:
                raise ToolError("dayz_test_failed") from None

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
                return await dayz_test_tool.execute_dayz_test_stop(
                    client, run_id, progress_cb=report
                )
            except dayz_test_tool.DayzTestToolError as error:
                raise ToolError(error.code) from None
            except ToolError:
                raise
            except Exception:
                raise ToolError("dayz_test_failed") from None

    @app.tool(description="Read the authoritative server-side player state.")
    async def query_player_state(timeout_s: float = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        async with runtime.tool_lock:
            return await runtime.call_bridge("query_player_state", {}, "server", _timeout(timeout_s))

    @app.tool(description="Spawn a DayZ object through the existing world_spawn bridge command.")
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

    @app.tool(description="Delete an object previously returned by world_spawn.object_id.")
    async def object_delete(object_id: int, timeout_s: float = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        if not isinstance(object_id, int) or isinstance(object_id, bool):
            raise ToolError("bad_args")
        parsed_id = object_id
        if parsed_id <= 0:
            raise ToolError("bad_args")
        args = {"object_id": parsed_id}
        async with runtime.tool_lock:
            return await runtime.call_bridge("object_delete", args, "server", _timeout(timeout_s))

    @app.tool(description="Send a vanilla notification popup to all connected players.")
    async def notify_players(
        show_time: float,
        title: str,
        detail: str = "",
        icon: str = "",
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        show_time_value = _finite_float(show_time)
        if show_time_value <= 0.0 or not isinstance(title, str) or title == "":
            raise ToolError("bad_args")
        if not isinstance(detail, str) or not isinstance(icon, str):
            raise ToolError("bad_args")
        args = {"show_time": show_time_value, "title": title, "detail": detail, "icon": icon}
        async with runtime.tool_lock:
            return await runtime.call_bridge("notify_players", args, "server", _timeout(timeout_s))

    @app.tool(description="Seat the first player in the driver seat of a vehicle near pos.")
    async def vehicle_enter(pos: list[float], timeout_s: float = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        args = {"pos": _require_vec3(pos, "pos")}
        async with runtime.tool_lock:
            return await runtime.call_bridge("vehicle_enter", args, "server", _timeout(timeout_s))

    @app.tool(description="Raycast through the server bridge using from/to world positions.")
    async def scene_raycast(
        from_pos: list[float],
        to: list[float],
        method: str = "rvproxy",
        ignore: str = "",
        radius: float = 0.05,
        intersect: str = "view",
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        args = {
            "from": _require_vec3(from_pos, "from"),
            "to": _require_vec3(to, "to"),
            "method": method,
            "ignore": ignore,
            "radius": float(radius),
            "intersect": intersect,
        }
        if args["radius"] < 0.0 or not math.isfinite(args["radius"]) or method not in {"rvproxy", "bullet"}:
            raise ToolError("bad_args")
        if ignore not in {"", "player"} or intersect not in {"view", "fire", "geom", "ifire"}:
            raise ToolError("bad_args")
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
        if mode == "object_at":
            args = {"mode": mode, "type": type, "pos": _require_vec3(pos, "telemetry_pos"), "radius": float(radius)}
            if args["type"] == "" or args["radius"] <= 0.0 or not math.isfinite(args["radius"]):
                raise ToolError("bad_args")
        elif mode == "fixture_jsonl":
            args = {"mode": mode, "path": path, "max_lines": int(max_lines)}
        else:
            raise ToolError("bad_args")
        async with runtime.tool_lock:
            return await runtime.call_bridge("telemetry_read", args, "server", _timeout(timeout_s))

    @app.tool(description="Diagnose whether a normal get-in would be available on a vehicle and which gate blocks it.")
    async def query_get_in_condition(
        pos: list[float],
        component: int = -1,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        args = {"pos": _require_vec3(pos, "pos"), "component": int(component)}
        async with runtime.tool_lock:
            return await runtime.call_bridge("query_get_in_condition", args, "server", _timeout(timeout_s))

    @app.tool(description="Set server world date/time and optionally the time multiplier.")
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
            return await runtime.call_bridge("world_time_set", args, "server", _timeout(timeout_s))

    @app.tool(description="Set server weather overcast, rain, or fog forecast values.")
    async def world_weather_set(
        overcast: float | None = None,
        rain: float | None = None,
        fog: float | None = None,
        time: float = 0.0,
        min_duration: float = 0.0,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {}
        overcast_value = _optional_finite_float(overcast)
        rain_value = _optional_finite_float(rain)
        fog_value = _optional_finite_float(fog)
        if overcast_value is not None:
            args["overcast"] = _require_range(overcast_value, 0.0, 1.0, "bad_overcast")
        if rain_value is not None:
            args["rain"] = _require_range(rain_value, 0.0, 1.0, "bad_rain")
        if fog_value is not None:
            args["fog"] = _require_range(fog_value, 0.0, 1.0, "bad_fog")
        if not args:
            raise ToolError("no_weather_fields")
        args["time"] = _require_range(time, 0.0, float("inf"))
        args["min_duration"] = _require_range(min_duration, 0.0, float("inf"))
        async with runtime.tool_lock:
            return await runtime.call_bridge("world_weather_set", args, "server", _timeout(timeout_s))

    @app.tool(description="Set the client camera through the existing camera_set bridge command.")
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
        if cam_mode == "orient":
            args = {"cam_mode": cam_mode, "cam_pos": _require_vec3(cam_pos, "cam_pos"), "cam_orientation": _require_vec3(cam_orientation, "cam_orientation")}
        elif cam_mode == "lookat":
            args = {"cam_mode": cam_mode, "cam_pos": _require_vec3(cam_pos, "cam_pos"), "look_at": _require_vec3(look_at, "look_at")}
        elif cam_mode == "matrix":
            args = {"cam_mode": cam_mode, "cam_matrix": _require_float_list(cam_matrix, 12)}
        elif cam_mode == "free":
            args = {"cam_mode": cam_mode, "cam_pos": _require_vec3(cam_pos, "cam_pos")}
            if look_at is not None:
                args["look_at"] = _require_vec3(look_at, "look_at")
            else:
                args["cam_orientation"] = _require_vec3(cam_orientation, "cam_orientation")
        else:
            raise ToolError("bad_args")
        fov_value = float(fov)
        if fov_value < 0.0 or not math.isfinite(fov_value):
            raise ToolError("bad_args")
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
        "Capture a screenshot from the DayZDiag window. Returns inline JPEG ImageContent fit to the "
        "client's MAX_MCP_OUTPUT_TOKENS budget (default 25000 -> ~600px wide; raise that client env var for bigger inline frames: 50000 -> ~860px/2x px, 75000 -> ~1070px/3x, 100000 -> ~native; max_tokens spends LESS than the cap, above-cap is clamped). Use scale='full' to spend a raised inline budget on resolution (the default scale='small' is a hard 512px cap). crop ('center', 'center:0.4', or normalized 'l,t,r,b') zooms on the subject; "
        "for optical zoom set a narrow fov via camera_set first. fmt='webp' is ~15% smaller (opt-in; Claude Code has known webp MIME bugs, JPEG stays default). save_fullres=True also writes the "
        "native-resolution frame to disk and returns its path in a JSON text block — read that file for "
        "fine detail, bypassing the inline token budget."
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
        async with runtime.tool_lock:
            result = await asyncio.to_thread(
                mcp_capture.capture_dual,
                scale=scale,
                max_tokens=eff_max_tokens,
                frames=frames,
                process_name=process_name,
                fmt=fmt,
                quality=quality,
                crop=crop,
                save_fullres=save_fullres,
                save_dir=save_dir,
            )
        if result.get("isError"):
            raise ToolError(str(result.get("error") or result))
        inline = result.get("inline") or {}
        data = inline.get("data")
        if not isinstance(data, str):
            raise ToolError("missing image data")
        try:
            raw = base64.b64decode(data.encode("ascii"), validate=True)
        except ValueError as exc:
            raise ToolError("bad image data") from exc
        image_format = "jpeg" if inline.get("mimeType") == "image/jpeg" else "png"
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

    @app.tool(description="Inspect loopback queues, peer liveness, and version_state without touching DayZ.")
    async def bridge_status() -> dict[str, Any]:
        runtime.touch()
        return await runtime.bridge_status_payload()

    @app.tool(description="Seat the connected client in a nearby vehicle (client-side ownership get-in).")
    async def vehicle_get_in_client(pos: list[float], timeout_s: float = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        args = {"pos": _require_vec3(pos, "pos")}
        async with runtime.tool_lock:
            return await runtime.call_bridge("vehicle_get_in_client", args, "client", _timeout(timeout_s))

    @app.tool(description="Start or stop the owned vehicle's engine.")
    async def engine_set(mode: str, timeout_s: float = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        if mode not in ("start", "stop"):
            raise ToolError("bad_mode")
        async with runtime.tool_lock:
            return await runtime.call_bridge("engine_set", {"mode": mode}, "client", _timeout(timeout_s))

    @app.tool(description="Set sustained owner-side driving control (held until released or deadman TTL).")
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
        if not math.isfinite(ttl) or ttl < 0.0:
            raise ToolError("bad_hold_ttl_s")
        args = {"throttle": t, "steer": s, "brake": b, "handbrake": h, "hold_ttl_s": ttl}
        async with runtime.tool_lock:
            return await runtime.call_bridge("vehicle_control", args, "client", _timeout(timeout_s))

    @app.tool(description="Read owner-side vehicle telemetry (speed, gear, engine, pos, ownership).")
    async def vehicle_telemetry(timeout_s: float = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        async with runtime.tool_lock:
            return await runtime.call_bridge("vehicle_telemetry", {}, "client", _timeout(timeout_s))

    @app.tool(description="Capture and read an atomic owner-client vehicle trace.")
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
            return await runtime.call_bridge(
                "vehicle_trace",
                args,
                "client",
                _timeout(timeout_s),
            )

    @app.tool(description="Release sustained vehicle control (stop driving).")
    async def vehicle_release(timeout_s: float = DEFAULT_TOOL_TIMEOUT_S) -> dict[str, Any]:
        async with runtime.tool_lock:
            return await runtime.call_bridge("vehicle_release", {}, "client", _timeout(timeout_s))

    _patch_public_argument_alias(app, "scene_raycast", "from_pos", "from")
    return app, runtime


def parse_args(argv: list[str] | None = None) -> ServerConfig:
    parser = build_server_parser()
    parser.allow_abbrev = False
    args = parser.parse_args(argv)
    return ServerConfig(
        mode=args.mode,
        port=args.port,
        keyfile=args.keyfile,
        expected_game_version=args.expected_game_version,
        require_version=bool(args.require_version),
        idle_timeout_s=float(args.idle_timeout),
        enable_exec_enforce=bool(args.enable_exec_enforce),
        exec_allowlist=args.exec_allowlist,
        client_platform=args.client_platform,
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
    app.run(transport="stdio")
    return 0
