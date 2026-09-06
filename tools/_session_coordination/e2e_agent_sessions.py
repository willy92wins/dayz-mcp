"""Binary Phase-A gate for DayZ MCP multi-agent session coordination.

Runs a real daemon binary, real client runtimes and one disposable MCP stdio
proxy.  It never stops DayZ and never force-terminates a daemon: daemon restarts
are produced by its existing idle self-shutdown contract.
"""
from __future__ import annotations

import asyncio
import json
import os
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

_TOOLS = Path(__file__).resolve().parents[1]
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from dayz_mcp.server import ClientRuntime, ServerConfig, ToolError  # noqa: E402
from mcp.types import LATEST_PROTOCOL_VERSION  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULT_PATH = HERE / "e2e_result.json"
OWNER_TTL_S = 120.0
WAIT_SLICE_S = 30.0
OWNER_EXIT_HARD_TIMEOUT_S = 150.0
GATE_NAMES = (
    "P1_one_daemon",
    "P2_parallel_reads",
    "P3_mutation_rejected_before_poll",
    "P4_fifo_a_b_c",
    "P5_owner_exit_expiry",
    "P6_restart_invalidates",
    "P7_no_secrets_and_clean_status",
)


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _wait_listener(port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _listener_pids(port: int) -> set[int]:
    completed = subprocess.run(
        ["netstat", "-ano", "-p", "TCP"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    suffix = f"127.0.0.1:{port}"
    pids: set[int] = set()
    for line in completed.stdout.splitlines():
        fields = line.split()
        if (
            "LISTENING" in line.upper()
            and len(fields) >= 5
            and fields[1] == suffix
        ):
            try:
                pids.add(int(fields[-1]))
            except ValueError:
                continue
    return pids


def _listener_count(port: int) -> int:
    return len(_listener_pids(port))


def _wait_port_free(port: int, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            time.sleep(0.1)
        finally:
            sock.close()
    return False


def _same_nonempty_generation(*values: object) -> bool:
    return bool(
        values
        and all(isinstance(value, str) and bool(value) for value in values)
        and len(set(values)) == 1
    )


def _changed_nonempty_generation(old: object, new: object) -> bool:
    return (
        isinstance(old, str)
        and bool(old)
        and isinstance(new, str)
        and bool(new)
        and old != new
    )


def _cleanup_pass(
    daemon_exit_code: int | None,
    listener_free: bool,
    peers_stopped: bool,
    proxies_stopped: bool,
) -> bool:
    return (
        daemon_exit_code == 0
        and listener_free
        and peers_stopped
        and proxies_stopped
    )


def _overall_pass(gates: dict[str, object], *, cleanup_ok: bool) -> bool:
    return cleanup_ok and all(gates.get(name) is True for name in GATE_NAMES)


def _next_wait_timeout(
    started_at: float, now: float, hard_timeout_s: float = OWNER_EXIT_HARD_TIMEOUT_S
) -> float | None:
    remaining = max(0.0, hard_timeout_s - (now - started_at))
    if remaining <= 0.0:
        return None
    return min(WAIT_SLICE_S, remaining)


def _p5_deadline_pass(granted: bool, elapsed_s: float) -> bool:
    return granted and 0.0 <= elapsed_s <= OWNER_EXIT_HARD_TIMEOUT_S


def _http(
    base: str,
    method: str,
    path: str,
    secret: str,
    *,
    payload: dict | None = None,
    query: dict[str, object] | None = None,
    timeout: float = 3.0,
) -> tuple[int, dict]:
    params = dict(query or {})
    params["key"] = secret
    url = base + path + "?" + urllib.parse.urlencode(params)
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return int(response.status), json.loads(
            response.read().decode("utf-8") or "{}"
        )


class GamePeer:
    """Fake binary-side DayZ peer; retained evidence omits request arguments."""

    def __init__(self, base: str, secret: str, peer: str) -> None:
        self.base = base
        self.secret = secret
        self.peer = peer
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._seen: list[dict[str, object]] = []
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> bool:
        self._stop.set()
        self._thread.join(timeout=2.0)
        return not self._thread.is_alive()

    def names(self) -> list[str]:
        with self._lock:
            return [str(item["cmd"]) for item in self._seen]

    def first_seen(self, command: str) -> float | None:
        with self._lock:
            for item in self._seen:
                if item["cmd"] == command:
                    return float(item["seen_at"])
        return None

    def count(self, command: str) -> int:
        return self.names().count(command)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                _status, body = _http(
                    self.base,
                    "GET",
                    "/poll",
                    self.secret,
                    query={"peer": self.peer},
                )
                for command in body.get("commands", []):
                    seen_at = time.monotonic()
                    with self._lock:
                        self._seen.append(
                            {"cmd": str(command["cmd"]), "seen_at": seen_at}
                        )
                    if command["cmd"] in {"query_player_state", "camera_get"}:
                        time.sleep(0.25)
                    _http(
                        self.base,
                        "POST",
                        "/result",
                        self.secret,
                        payload={
                            "id": int(command["id"]),
                            "ok": 1,
                            "cmd": str(command["cmd"]),
                        },
                    )
            except Exception:
                pass
            time.sleep(0.01)


class StdioProxy:
    """Small JSON-RPC driver around one real `dayz_mcp --client` process."""

    def __init__(self, argv: list[str], cwd: Path, stderr_path: Path) -> None:
        self._messages: queue.Queue[dict] = queue.Queue()
        with stderr_path.open("w", encoding="utf-8") as errlog:
            self.process = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=errlog,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        self._next_id = 1

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                self._messages.put(value)

    def request(self, method: str, params: dict, timeout: float = 15.0) -> dict:
        request_id = self._next_id
        self._next_id += 1
        self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                message = self._messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(f"stdio response timeout: {method}") from exc
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError("stdio_rpc_error")
            result = message.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("stdio_bad_result")
            return result
        raise TimeoutError(f"stdio response timeout: {method}")

    def notify(self, method: str, params: dict | None = None) -> None:
        message: dict[str, object] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._write(message)

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "session-e2e", "version": "1"},
            },
        )
        self.notify("notifications/initialized")

    def call_tool(self, name: str, arguments: dict) -> dict:
        result = self.request(
            "tools/call", {"name": name, "arguments": arguments}, timeout=20.0
        )
        if result.get("isError"):
            raise RuntimeError("stdio_tool_error")
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            value = structured.get("result", structured)
            if isinstance(value, dict):
                return value
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    try:
                        value = json.loads(item["text"])
                    except ValueError:
                        continue
                    if isinstance(value, dict):
                        return value
        raise RuntimeError("stdio_tool_bad_payload")

    def terminate_proxy_only(self) -> int | None:
        self.process.terminate()
        try:
            return self.process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            self.process.kill()
            return self.process.wait(timeout=5.0)

    def _write(self, message: dict[str, object]) -> None:
        if self.process.stdin is None:
            raise RuntimeError("stdio_closed")
        self.process.stdin.write(
            json.dumps(message, separators=(",", ":"), ensure_ascii=False) + "\n"
        )
        self.process.stdin.flush()


def _daemon_argv(port: int, keyfile: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "dayz_mcp",
        "--daemon",
        "--port",
        str(port),
        "--keyfile",
        str(keyfile),
        "--idle-timeout",
        "2",
    ]


def _proxy_argv(port: int, keyfile: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "dayz_mcp",
        "--client",
        "--no-daemon-autospawn",
        "--port",
        str(port),
        "--keyfile",
        str(keyfile),
        "--client-platform",
        "codex",
        "--task-label",
        "e2e-owner-exit",
    ]


def _launch_daemon(
    port: int, keyfile: Path, env: dict[str, str], stderr_path: Path
) -> subprocess.Popen:
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    with stderr_path.open("w", encoding="utf-8") as errlog:
        return subprocess.Popen(
            _daemon_argv(port, keyfile),
            cwd=str(_TOOLS),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=errlog,
            close_fds=True,
            creationflags=creationflags,
        )


def _client(
    port: int,
    secret: str,
    platform: str,
    label: str,
) -> ClientRuntime:
    return ClientRuntime(
        ServerConfig(
            mode="client",
            key=secret,
            port=port,
            client_platform=platform,
            task_label=label,
            auto_spawn_daemon=False,
            log_sink=lambda _message: None,
        )
    )


def _error_code(exc: BaseException) -> str:
    text = str(exc)
    for code in ("lease_required", "lease_invalid", "ticket_invalid"):
        if code in text:
            return code
    return "unexpected_error"


def _forbidden_field_present(value: object) -> bool:
    forbidden = {
        "key",
        "api_key",
        "keyfile",
        "lease_token",
        "token",
        "pid",
        "ppid",
        "args",
        "request_args",
        "client_identity_json",
    }
    if isinstance(value, dict):
        if any(str(key).casefold() in forbidden for key in value):
            return True
        return any(_forbidden_field_present(item) for item in value.values())
    if isinstance(value, list):
        return any(_forbidden_field_present(item) for item in value)
    return False


def _parse_json_documents(texts: list[str]) -> list[object]:
    documents: list[object] = []
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            documents.append(json.loads(text))
            continue
        except ValueError:
            pass
        for line in text.splitlines():
            if not line.strip():
                continue
            documents.append(json.loads(line))
    return documents


def _contains_secret_value(value: object, secret_values: list[str]) -> bool:
    if isinstance(value, dict):
        return any(
            _contains_secret_value(key, secret_values)
            or _contains_secret_value(item, secret_values)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_secret_value(item, secret_values) for item in value)
    if isinstance(value, str):
        return any(secret and secret in value for secret in secret_values)
    return False


def _scan_documents(
    documents: list[object], secret_values: list[str]
) -> tuple[bool, bool]:
    return (
        any(_contains_secret_value(document, secret_values) for document in documents),
        any(_forbidden_field_present(document) for document in documents),
    )


async def _run() -> dict[str, object]:
    result: dict[str, object] = {
        "P1_one_daemon": False,
        "P2_parallel_reads": False,
        "P3_mutation_rejected_before_poll": False,
        "P4_fifo_a_b_c": False,
        "P5_owner_exit_expiry": False,
        "P6_restart_invalidates": False,
        "P7_no_secrets_and_clean_status": False,
        "overall_pass": False,
        "evidence": {},
    }
    evidence: dict[str, object] = result["evidence"]  # type: ignore[assignment]
    transient_secrets: list[str] = []
    daemon_process: subprocess.Popen | None = None
    proxy: StdioProxy | None = None
    proxy_started = False
    proxy_exit_code: int | None = None
    proxy_autospawn_disabled = False
    proxy_listener_stable = False
    proxy_generation_stable = False
    peers: list[GamePeer] = []
    peers_stopped = True
    clean_status = False
    final_pending_commands: object = None
    cleanup_ok = False

    with TemporaryDirectory(prefix="dayz-mcp-session-e2e-") as temp_root:
        temp = Path(temp_root)
        runtime_root = temp / "runtime"
        runtime_root.mkdir()
        secret = secrets.token_urlsafe(32)
        transient_secrets.append(secret)
        keyfile = temp / "auth.secret"
        keyfile.write_text(secret, encoding="utf-8")
        env = dict(os.environ)
        env["LOCALAPPDATA"] = str(runtime_root)
        port = _free_port()
        base = f"http://127.0.0.1:{port}"

        try:
            daemon_process = _launch_daemon(
                port, keyfile, env, temp / "daemon-first.err.log"
            )
            if not _wait_listener(port):
                raise RuntimeError("daemon_not_listening")

            runtime_a = _client(port, secret, "codex", "A")
            runtime_b = _client(port, secret, "claude", "B")
            runtime_c = _client(port, secret, "codex", "C")
            initial_a, initial_b = await asyncio.gather(
                runtime_a.bridge_status_payload(), runtime_b.bridge_status_payload()
            )
            _listener_status_code, listener_status = _http(
                base, "GET", "/status", secret
            )
            contender = subprocess.run(
                _daemon_argv(port, keyfile),
                cwd=str(_TOOLS),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
            listener_pids = _listener_pids(port)
            listener_count = len(listener_pids)
            same_generation = _same_nonempty_generation(
                initial_a.get("daemon_generation"),
                initial_b.get("daemon_generation"),
                listener_status.get("daemon_generation"),
            )
            p1 = (
                daemon_process.poll() is None
                and contender.returncode == 0
                and listener_count == 1
                and same_generation
            )
            result["P1_one_daemon"] = p1
            evidence["P1"] = {
                "listener_count": listener_count,
                "contender_exit_code": contender.returncode,
                "generation_nonempty_and_shared": same_generation,
            }

            server_peer = GamePeer(base, secret, "server")
            client_peer = GamePeer(base, secret, "client")
            peers.extend((server_peer, client_peer))
            server_peer.start()
            client_peer.start()

            read_started = time.monotonic()
            server_read, client_read = await asyncio.gather(
                runtime_b.call_bridge("query_player_state", {}, "server", 3.0),
                runtime_c.call_bridge("camera_get", {}, "client", 3.0),
            )
            read_duration = time.monotonic() - read_started
            server_seen = server_peer.first_seen("query_player_state")
            client_seen = client_peer.first_seen("camera_get")
            start_delta = (
                abs(server_seen - client_seen)
                if server_seen is not None and client_seen is not None
                else 999.0
            )
            p2 = (
                bool(server_read.get("ok"))
                and bool(client_read.get("ok"))
                and start_delta < 0.20
            )
            result["P2_parallel_reads"] = p2
            evidence["P2"] = {
                "completed_reads": 2 if p2 else 0,
                "start_delta_s": round(start_delta, 3),
                "duration_s": round(read_duration, 3),
            }

            before_reject = server_peer.count("world_spawn")
            rejection = ""
            try:
                await runtime_b.call_bridge("world_spawn", {}, "server", 2.0)
            except ToolError as exc:
                rejection = _error_code(exc)
            await asyncio.sleep(0.1)
            after_reject = server_peer.count("world_spawn")
            p3 = rejection == "lease_required" and before_reject == after_reject
            result["P3_mutation_rejected_before_poll"] = p3
            evidence["P3"] = {
                "decision": rejection,
                "poll_count_before": before_reject,
                "poll_count_after": after_reject,
            }

            active_a = await runtime_a.session_acquire("A")
            queued_b = await runtime_b.session_acquire("B")
            queued_c = await runtime_c.session_acquire("C")
            transient_secrets.append(str(active_a["lease_token"]))
            await runtime_a.call_bridge("world_spawn", {}, "server", 3.0)
            await runtime_a.session_release(active_a["lease_token"])
            active_b = await runtime_b.session_wait(queued_b["ticket"], 0.0)
            transient_secrets.append(str(active_b["lease_token"]))
            await runtime_b.call_bridge("world_time_set", {}, "server", 3.0)
            await runtime_b.session_release(active_b["lease_token"])
            active_c = await runtime_c.session_wait(queued_c["ticket"], 0.0)
            transient_secrets.append(str(active_c["lease_token"]))
            await runtime_c.call_bridge("world_weather_set", {}, "server", 3.0)
            await runtime_c.session_release(active_c["lease_token"])
            expected_order = ["world_spawn", "world_time_set", "world_weather_set"]
            observed_order = [
                name for name in server_peer.names() if name in expected_order
            ]
            p4 = (
                queued_b.get("position") == 1
                and queued_c.get("position") == 2
                and observed_order == expected_order
            )
            result["P4_fifo_a_b_c"] = p4
            evidence["P4"] = {
                "queue_positions": [queued_b.get("position"), queued_c.get("position")],
                "lease_ids": [
                    active_a.get("lease_id"),
                    active_b.get("lease_id"),
                    active_c.get("lease_id"),
                ],
                "mutation_order": observed_order,
            }

            proxy_argv = _proxy_argv(port, keyfile)
            proxy_autospawn_disabled = "--no-daemon-autospawn" in proxy_argv
            p5_listener_before = _listener_pids(port)
            p5_status_before = await runtime_c.bridge_status_payload()
            proxy = StdioProxy(
                proxy_argv,
                _TOOLS,
                temp / "stdio-proxy.err.log",
            )
            proxy_started = True
            proxy.initialize()
            proxy_owner = proxy.call_tool(
                "session_acquire", {"purpose": "owner-exit-expiry"}
            )
            transient_secrets.append(str(proxy_owner["lease_token"]))
            waiter = _client(port, secret, "claude", "expiry-waiter")
            queued_waiter = await waiter.session_acquire("expiry waiter")
            owner_exit_started = time.monotonic()
            proxy_exit_code = proxy.terminate_proxy_only()
            proxy = None
            p5_listener_after = _listener_pids(port)
            p5_status_after = await runtime_c.bridge_status_payload()
            proxy_listener_stable = (
                len(p5_listener_before) == 1
                and p5_listener_before == p5_listener_after
            )
            proxy_generation_stable = _same_nonempty_generation(
                p5_status_before.get("daemon_generation"),
                p5_status_after.get("daemon_generation"),
            )

            slice_durations: list[float] = []
            wait_requests: list[float] = []
            granted_waiter: dict | None = None
            while True:
                wait_timeout = _next_wait_timeout(
                    owner_exit_started, time.monotonic()
                )
                if wait_timeout is None:
                    break
                wait_requests.append(wait_timeout)
                slice_started = time.monotonic()
                waited = await waiter.session_wait(
                    queued_waiter["ticket"], wait_timeout
                )
                slice_durations.append(time.monotonic() - slice_started)
                if waited.get("status") == "active":
                    granted_waiter = waited
                    break
            owner_exit_duration = time.monotonic() - owner_exit_started
            if granted_waiter is not None:
                transient_secrets.append(str(granted_waiter["lease_token"]))
                await waiter.session_release(granted_waiter["lease_token"])
            p5 = (
                proxy_exit_code is not None
                and _p5_deadline_pass(granted_waiter is not None, owner_exit_duration)
                and owner_exit_duration >= OWNER_TTL_S - 2.0
                and all(timeout <= WAIT_SLICE_S for timeout in wait_requests)
                and proxy_autospawn_disabled
                and proxy_listener_stable
                and proxy_generation_stable
            )
            result["P5_owner_exit_expiry"] = p5
            evidence["P5"] = {
                "owner_lease_id": proxy_owner.get("lease_id"),
                "waiter_initial_position": queued_waiter.get("position"),
                "waiter_lease_id": (
                    granted_waiter.get("lease_id") if granted_waiter else None
                ),
                "duration_s": round(owner_exit_duration, 3),
                "wait_slices": len(slice_durations),
                "max_slice_duration_s": round(max(slice_durations, default=0.0), 3),
                "max_wait_request_s": round(max(wait_requests, default=0.0), 3),
                "proxy_exit_observed": proxy_exit_code is not None,
                "proxy_autospawn_disabled": proxy_autospawn_disabled,
                "listener_pid_stable": proxy_listener_stable,
                "generation_stable": proxy_generation_stable,
            }

            old_active = await runtime_a.session_acquire("restart-active")
            old_queued = await runtime_b.session_acquire("restart-queued")
            old_token = str(old_active["lease_token"])
            old_ticket = str(old_queued["ticket"])
            transient_secrets.append(old_token)
            peer_stop_results = [peer.stop() for peer in peers]
            peers_stopped = all(peer_stop_results)

            daemon_process.wait(timeout=20.0)
            first_exit_code = daemon_process.returncode
            daemon_process = _launch_daemon(
                port, keyfile, env, temp / "daemon-second.err.log"
            )
            if not _wait_listener(port):
                raise RuntimeError("restarted_daemon_not_listening")
            restarted_status = await runtime_c.bridge_status_payload()

            lease_error = ""
            ticket_error = ""
            try:
                await runtime_a.session_heartbeat(old_token)
            except ToolError as exc:
                lease_error = _error_code(exc)
            try:
                await runtime_b.session_wait(old_ticket, 0.0)
            except ToolError as exc:
                ticket_error = _error_code(exc)

            audit_path = runtime_root / "DayZ_MCP" / "audit" / "events.jsonl"
            audit_text = audit_path.read_text(encoding="utf-8")
            audit_events = [json.loads(line) for line in audit_text.splitlines()]
            restart_event_seen = any(
                event.get("event") == "daemon_restart_invalidated"
                for event in audit_events
            )
            generation_changed = _changed_nonempty_generation(
                initial_a.get("daemon_generation"),
                restarted_status.get("daemon_generation"),
            )
            p6 = (
                first_exit_code == 0
                and generation_changed
                and lease_error == "lease_invalid"
                and ticket_error == "ticket_invalid"
                and restart_event_seen
            )
            result["P6_restart_invalidates"] = p6
            evidence["P6"] = {
                "first_daemon_exit_code": first_exit_code,
                "generation_nonempty_and_changed": generation_changed,
                "old_lease_decision": lease_error,
                "old_ticket_decision": ticket_error,
                "restart_event_seen": restart_event_seen,
            }

            final_a, final_b = await asyncio.gather(
                runtime_a.session_status(), runtime_b.session_status()
            )
            clean_status = (
                final_a.get("owner") is None
                and final_b.get("owner") is None
                and final_a.get("queue") == []
                and final_b.get("queue") == []
                and final_a.get("self", {}).get("state") == "none"
                and final_b.get("self", {}).get("state") == "none"
                and final_a.get("pending_commands") == 0
                and final_b.get("pending_commands") == 0
            )
            final_pending_commands = final_a.get("pending_commands")
        finally:
            if proxy is not None:
                if proxy.process.poll() is None:
                    proxy_exit_code = proxy.terminate_proxy_only()
                else:
                    proxy_exit_code = proxy.process.returncode
            proxies_stopped = not proxy_started or proxy_exit_code is not None
            peer_stop_results = [peer.stop() for peer in peers]
            peers_stopped = peers_stopped and all(peer_stop_results)
            # Daemons are never killed here.  With peers stopped and idle_timeout=2,
            # the owned test daemon releases its listener through normal watchdog exit.
            final_daemon_exit_code: int | None = None
            if daemon_process is not None and daemon_process.poll() is None:
                try:
                    daemon_process.wait(timeout=20.0)
                except subprocess.TimeoutExpired:
                    pass
            if daemon_process is not None:
                final_daemon_exit_code = daemon_process.poll()
            listener_free = _wait_port_free(port, timeout=10.0)
            cleanup_ok = _cleanup_pass(
                final_daemon_exit_code,
                listener_free,
                peers_stopped,
                proxies_stopped,
            )

            evidence["P7"] = {
                "own_lease": "none" if clean_status else "not_clean",
                "own_ticket": "none" if clean_status else "not_clean",
                "pending_commands": final_pending_commands,
                "secret_values_found": 0,
                "forbidden_fields_found": 0,
                "proxy_autospawn_disabled": proxy_autospawn_disabled,
                "listener_pid_stable": proxy_listener_stable,
                "generation_stable": proxy_generation_stable,
                "final_daemon_exit_code": final_daemon_exit_code,
                "listener_free": listener_free,
                "peers_stopped": peers_stopped,
                "proxies_stopped": proxies_stopped,
            }

            runtime_dir = runtime_root / "DayZ_MCP"
            json_texts = [json.dumps(result, separators=(",", ":"))]
            coordination_path = runtime_dir / "coordination.json"
            if coordination_path.exists():
                json_texts.append(coordination_path.read_text(encoding="utf-8"))
            audit_dir = runtime_dir / "audit"
            if audit_dir.exists():
                for audit_file in sorted(audit_dir.glob("events.jsonl*")):
                    json_texts.append(audit_file.read_text(encoding="utf-8"))
            documents = _parse_json_documents(json_texts)
            secret_value_found, forbidden_field_found = _scan_documents(
                documents, transient_secrets
            )
            evidence["P7"]["secret_values_found"] = (  # type: ignore[index]
                1 if secret_value_found else 0
            )
            evidence["P7"]["forbidden_fields_found"] = (  # type: ignore[index]
                1 if forbidden_field_found else 0
            )
            result["P7_no_secrets_and_clean_status"] = (
                clean_status
                and cleanup_ok
                and proxy_autospawn_disabled
                and proxy_listener_stable
                and proxy_generation_stable
                and not secret_value_found
                and not forbidden_field_found
            )

    result["overall_pass"] = _overall_pass(result, cleanup_ok=cleanup_ok)
    return result


def main() -> int:
    result: dict[str, object]
    try:
        result = asyncio.run(_run())
    except Exception as exc:
        result = {
            "P1_one_daemon": False,
            "P2_parallel_reads": False,
            "P3_mutation_rejected_before_poll": False,
            "P4_fifo_a_b_c": False,
            "P5_owner_exit_expiry": False,
            "P6_restart_invalidates": False,
            "P7_no_secrets_and_clean_status": False,
            "overall_pass": False,
            "evidence": {"harness_error": type(exc).__name__},
        }
    RESULT_PATH.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("overall_pass") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
