from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
import unittest
import urllib.error
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import control_client, core, host_config, server
from dayz_mcp.server import ServerConfig
from tests.test_daemon import DaemonHttpServer, _config, _free_port, _http
from tests.test_mcp_tools import _content_json
from tests.fence_helpers import INST_CLIENT, INST_SERVER


def _fixture_client_runtime(
    config: ServerConfig,
    **kwargs: object,
) -> server.ClientRuntime:
    """Construct a client without consulting live host registrations."""
    with tempfile.TemporaryDirectory() as directory:
        keyfile = Path(directory) / "daemon.key"
        keyfile.write_text(config.key or "fixture-key", encoding="utf-8")
        fixture_config = replace(config, keyfile=str(keyfile.resolve()))
        launcher = str(Path(sys.executable).resolve())
        native = launcher
        provenance = host_config.DaemonProvenance(
            launch_executable=launcher,
            native_executable=native,
            argv=tuple(server.daemon.build_daemon_argv(fixture_config, python=launcher)),
            cwd=server.daemon.daemon_runtime_cwd(),
            port=fixture_config.port,
            keyfile=str(keyfile.resolve()),
            auto_spawn_daemon=fixture_config.auto_spawn_daemon,
        )
        with (
            patch.object(
                host_config,
                "resolve_daemon_provenance",
                return_value=provenance,
            ),
            patch.object(
                host_config, "_local_launch_executable", return_value=launcher
            ),
            patch.object(
                host_config, "_local_native_executable", return_value=native
            ),
        ):
            return server.ClientRuntime(fixture_config, **kwargs)


class GamePeer:
    """A fake DayZ poller: GET /poll on the daemon, respond via POST /result.
    Mirrors the real bridge's int-ok wire type so the ok-handling path is real."""

    def __init__(self, base: str, key: str, peer: str, version: str | None = None, responder=None) -> None:
        self.base = base
        self.key = key
        self.peer = peer
        self.version = version
        self.responder = responder or self._default
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _default(self, command: dict) -> dict:
        result = {"id": command["id"], "ok": 1, "cmd": command["cmd"], "args": command.get("args", {})}
        if command["cmd"] == "query_player_state":
            result["state"] = {"pos": [1.0, 2.0, 3.0]}
        return result

    def _run(self) -> None:
        while not self._stop.is_set():
            query = {"peer": self.peer}
            query["inst"] = INST_SERVER if self.peer == "server" else INST_CLIENT
            if self.version is not None:
                query["ver"] = self.version
            try:
                _status, body = _http(self.base, "GET", "/poll", self.key, query=query)
                for command in body.get("commands", []):
                    _http(
                        self.base,
                        "POST",
                        "/result",
                        self.key,
                        payload=self.responder(command),
                        query={"inst": query["inst"]},
                    )
            except Exception:
                pass
            time.sleep(0.02)


class ClientModeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.servers: list[DaemonHttpServer] = []
        self.peers: list[GamePeer] = []

    def tearDown(self) -> None:
        for peer in self.peers:
            peer.shutdown()
        for srv in self.servers:
            srv.stop()

    def _daemon(self, port: int = 0, **kw) -> DaemonHttpServer:
        srv = DaemonHttpServer(_config(key="ckey", **kw), port=port)
        srv.start()
        self.servers.append(srv)
        return srv

    def _peer(self, srv: DaemonHttpServer, peer: str, version: str | None = None) -> GamePeer:
        game = GamePeer(srv.base, srv.key, peer, version=version)
        game.start()
        self.peers.append(game)
        return game

    def _client(self, srv: DaemonHttpServer, **kw) -> server.ClientRuntime:
        config = ServerConfig(mode="client", key=srv.key, port=srv.port, log_sink=lambda _m: None, **kw)
        runtime = _fixture_client_runtime(config)
        self._attach_fixture_transport(runtime, srv)
        return runtime

    @staticmethod
    def _attach_fixture_transport(
        runtime: server.ClientRuntime, srv: DaemonHttpServer
    ) -> None:
        request = lambda method, path, payload=None, query=None, timeout=5.0: _http(
            srv.base,
            method,
            path,
            srv.key,
            payload=payload,
            query=query,
            timeout=timeout,
        )
        runtime._request_once = request
        ClientModeTest._attach_control_transport(runtime, request)

    @staticmethod
    def _attach_control_transport(runtime: server.ClientRuntime, request) -> None:
        def control_request(path, payload, timeout_s):
            status, response = request(
                "POST", path, payload, None, timeout_s
            )
            if status not in (200, 202):
                raise control_client.ControlClientError(
                    server._remote_error_code(response),
                    request_stage="post_request",
                    http_bytes_sent=1,
                )
            return response

        runtime._control._request_once = control_request

    @staticmethod
    def _attach_scripted_transports(runtime: server.ClientRuntime, request) -> None:
        runtime._request_once = request
        ClientModeTest._attach_control_transport(runtime, request)

    def _standalone_client(self) -> server.ClientRuntime:
        return _fixture_client_runtime(
            ServerConfig(
                mode="client", key="k", port=12345, client_platform="codex",
                log_sink=lambda _m: None,
            )
        )

    async def test_client_call_bridge_round_trip(self) -> None:
        srv = self._daemon()
        self._peer(srv, "server")
        runtime = self._client(srv)
        result = await runtime.call_bridge("query_player_state", {}, "server", 2.0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"]["pos"], [1.0, 2.0, 3.0])

    async def test_client_acquire_stores_token_and_mutation_transports_it(self) -> None:
        srv = self._daemon()
        self._peer(srv, "server")
        runtime = self._client(srv, client_platform="codex")

        acquired = await runtime.session_acquire("spawn fixture")

        self.assertEqual(acquired["status"], "active")
        self.assertEqual(runtime.active_lease_token, acquired["lease_token"])
        own_identity = json.loads(acquired["client_identity_json"])
        self.assertEqual(own_identity["session_id"], runtime.identity.session_id)
        self.assertEqual(own_identity["pid"], runtime.identity.pid)
        result = await runtime.call_bridge(
            "world_spawn", {"type": "X", "pos": [1, 2, 3]}, "server", 2.0
        )
        self.assertTrue(result["ok"])

    async def test_wait_stores_granted_token_and_release_clears_local_state(self) -> None:
        srv = self._daemon()
        owner = self._client(srv, client_platform="codex")
        waiter = self._client(srv, client_platform="claude")
        owner_acquired = await owner.session_acquire("owner")
        queued = await waiter.session_acquire("waiter")
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(waiter.active_ticket, queued["ticket"])

        await owner.session_release(owner_acquired["lease_token"])
        await asyncio.sleep(0.05)
        granted = await waiter.session_wait(queued["ticket"], 0.0)

        self.assertEqual(granted["status"], "active")
        self.assertEqual(waiter.active_lease_token, granted["lease_token"])
        self.assertIsNone(waiter.active_ticket)
        await waiter.session_release(granted["lease_token"])
        await asyncio.sleep(0.1)
        self.assertIsNone(waiter.active_lease_token)
        self.assertIsNone(waiter.active_ticket)

    def test_two_client_runtimes_have_distinct_stable_session_ids(self) -> None:
        srv = self._daemon()
        first = self._client(srv, client_platform="codex")
        second = self._client(srv, client_platform="codex")

        self.assertNotEqual(first.identity.session_id, second.identity.session_id)

    def test_task_label_uses_environment_fallback_and_is_capped_at_120(self) -> None:
        srv = self._daemon()
        with patch.dict("os.environ", {"DAYZ_MCP_TASK_LABEL": "e" * 140}):
            from_environment = self._client(srv, client_platform="codex")
        explicit = self._client(
            srv, client_platform="claude", task_label="x" * 140
        )

        self.assertEqual(from_environment.identity.task_label, "e" * 120)
        self.assertEqual(explicit.identity.task_label, "x" * 120)

    async def test_call_bridge_always_transports_identity_and_operation_timeout(self) -> None:
        config = ServerConfig(
            mode="client", key="k", port=12345, client_platform="codex",
            log_sink=lambda _m: None,
        )
        runtime = _fixture_client_runtime(config)
        calls: list[dict] = []

        def request_once(method, path, payload=None, query=None, timeout=5.0):
            calls.append({"method": method, "path": path, "payload": payload})
            if path == "/enqueue":
                return 200, {"id": 7}
            return 200, {"status": "done", "result": {"id": 7, "ok": 1}}

        runtime._request_once = request_once
        await runtime.call_bridge("query_player_state", {}, "server", 1.25)

        enqueue = calls[0]["payload"]
        self.assertEqual(enqueue["identity"], runtime.identity.to_payload())
        self.assertEqual(enqueue["operation_timeout_s"], 1.25)
        self.assertNotIn("lease_token", enqueue)

    async def test_daemon_unreachable_never_switches_to_embedded(self) -> None:
        config = ServerConfig(
            mode="client", key="k", port=12345, client_platform="codex",
            log_sink=lambda _m: None,
        )
        runtime = _fixture_client_runtime(config)
        runtime._request_once = lambda *_a, **_k: (_ for _ in ()).throw(
            urllib.error.URLError("offline")
        )
        runtime._ensure_daemon = lambda *args: False

        with self.assertRaises(Exception) as err:
            await runtime.call_bridge("query_player_state", {}, "server", 0.1)

        self.assertIn("daemon_unavailable", str(err.exception))
        self.assertEqual(runtime.config.mode, "client")
        self.assertFalse(hasattr(runtime, "loopback"))

    async def test_no_daemon_autospawn_fails_without_calling_spawn(self) -> None:
        spawn_calls: list[bool] = []
        runtime = _fixture_client_runtime(
            ServerConfig(
                mode="client",
                key="k",
                port=12345,
                auto_spawn_daemon=False,
                log_sink=lambda _m: None,
            ),
            spawn_fn=lambda: spawn_calls.append(True),
            probe_fn=lambda *_a, **_k: False,
        )
        runtime._request_once = lambda *_a, **_k: (_ for _ in ()).throw(
            urllib.error.URLError("offline")
        )

        started = time.monotonic()
        with self.assertRaises(Exception) as err:
            await runtime.call_bridge("query_player_state", {}, "server", 0.1)

        self.assertIn("daemon_unavailable", str(err.exception))
        self.assertEqual(spawn_calls, [])
        self.assertLess(time.monotonic() - started, 1.0)

    async def test_session_http_error_never_echoes_unredacted_payload(self) -> None:
        runtime = self._standalone_client()
        runtime.active_lease_token = "active-token-shaped-test-value"
        unsafe_payloads = (
            {"error": {"lease_token": "sensitive-test-value"}},
            {"error": ["sensitive-test-value"]},
            {"error": "active-token-shaped-test-value"},
            {"error": "arbitrary_remote_text"},
        )

        for payload in unsafe_payloads:
            with self.subTest(payload=payload):
                self._attach_control_transport(
                    runtime,
                    lambda *_a, _payload=payload, **_k: (
                        500, _payload
                    ),
                )
                with self.assertRaises(Exception) as err:
                    await runtime.session_status()
                self.assertIn("remote_error", str(err.exception))
                self.assertNotIn("sensitive-test-value", str(err.exception))
                self.assertNotIn("active-token-shaped-test-value", str(err.exception))

                enqueue_error = runtime._enqueue_error(payload)
                self.assertEqual(enqueue_error, "remote_error")
                self.assertNotIn("sensitive-test-value", enqueue_error)
                self.assertNotIn("active-token-shaped-test-value", enqueue_error)

        self.assertEqual(
            runtime._enqueue_error({"error": "lease_required"}),
            server.LEASE_REQUIRED_RECIPE,
        )
        self.assertIn("session_acquire_wait", runtime._enqueue_error({"error": "lease_required"}))
        self.assertEqual(
            runtime._enqueue_error(
                {"error": "version_blocked", "state": "active-token-shaped-test-value"}
            ),
            "version_blocked",
        )

    async def test_ticket_errors_clear_only_the_matching_local_ticket(self) -> None:
        for error in ("ticket_expired", "ticket_invalid"):
            with self.subTest(error=error, matching=True):
                runtime = self._standalone_client()
                runtime.active_ticket = "old-ticket"
                runtime.active_operation_id = "operation-old"
                runtime._control.state = "QUEUED"
                self._attach_control_transport(
                    runtime,
                    lambda *_a, _error=error, **_k: (
                        410 if _error == "ticket_expired" else 403,
                        {"error": _error},
                    ),
                )
                with self.assertRaises(Exception) as err:
                    await runtime.session_wait("old-ticket", 0.0)
                self.assertIn(error, str(err.exception))
                self.assertIsNone(runtime.active_ticket)
                self.assertIsNone(runtime.active_operation_id)
                self.assertEqual(runtime._control.state, "CLOSED")

            with self.subTest(error=error, matching=False):
                runtime = self._standalone_client()
                runtime.active_ticket = "new-ticket"
                runtime.active_operation_id = "operation-new"
                runtime._control.state = "QUEUED"
                self._attach_control_transport(
                    runtime,
                    lambda *_a, _error=error, **_k: (
                        410 if _error == "ticket_expired" else 403,
                        {"error": _error},
                    ),
                )
                with self.assertRaises(Exception):
                    await runtime.session_wait("old-ticket", 0.0)
                self.assertEqual(runtime.active_ticket, "new-ticket")

    async def test_lease_errors_clear_only_matching_token_for_session_and_enqueue(self) -> None:
        for method_name in ("session_heartbeat", "session_release"):
            for error in ("lease_expired", "lease_invalid"):
                with self.subTest(method=method_name, error=error, matching=True):
                    runtime = self._standalone_client()
                    runtime.active_lease_token = "old-token"
                    runtime.active_ticket = "ticket-stays"
                    self._attach_control_transport(
                        runtime,
                        lambda *_a, _error=error, **_k: (
                            410 if _error == "lease_expired" else 403,
                            {"error": _error},
                        ),
                    )
                    with self.assertRaises(Exception) as err:
                        await getattr(runtime, method_name)("old-token")
                    self.assertIn(error, str(err.exception))
                    self.assertIsNone(runtime.active_lease_token)
                    self.assertEqual(runtime.active_ticket, "ticket-stays")

                with self.subTest(method=method_name, error=error, matching=False):
                    runtime = self._standalone_client()
                    runtime.active_lease_token = "new-token"
                    self._attach_control_transport(
                        runtime,
                        lambda *_a, _error=error, **_k: (
                            410 if _error == "lease_expired" else 403,
                            {"error": _error},
                        ),
                    )
                    with self.assertRaises(Exception):
                        await getattr(runtime, method_name)("old-token")
                    self.assertEqual(runtime.active_lease_token, "new-token")

        for error in ("lease_expired", "lease_invalid"):
            with self.subTest(endpoint="enqueue", error=error):
                runtime = self._standalone_client()
                runtime.active_lease_token = "old-token"
                runtime.active_ticket = "ticket-stays"
                runtime._request_once = lambda *_a, _error=error, **_k: (
                    410 if _error == "lease_expired" else 403,
                    {"error": _error},
                )
                with self.assertRaises(Exception) as err:
                    await runtime.call_bridge("world_spawn", {}, "server", 1.0)
                self.assertIn(error, str(err.exception))
                self.assertIsNone(runtime.active_lease_token)
                self.assertEqual(runtime.active_ticket, "ticket-stays")

    async def test_release_serializes_acquire_and_new_token_reaches_bridge(self) -> None:
        runtime = self._standalone_client()
        release_started = threading.Event()
        release_can_finish = threading.Event()
        acquire_started = threading.Event()
        acquire_tokens = iter(("old-token", "new-token"))
        sent_tokens: list[str] = []

        def request_once(_method, path, payload=None, *_args, **_kwargs):
            if path == "/session/acquire":
                token = next(acquire_tokens)
                if token == "new-token":
                    acquire_started.set()
                return 200, {"status": "active", "lease_token": token}
            if path == "/session/release":
                release_started.set()
                release_can_finish.wait(2.0)
                return 200, {"released": True}
            if path == "/enqueue":
                sent_tokens.append(payload["lease_token"])
                return 200, {"id": 17}
            if path == "/await":
                return 200, {"status": "done", "result": {"ok": 1}}
            raise AssertionError(path)

        self._attach_scripted_transports(runtime, request_once)
        await runtime.session_acquire("old")
        old_release = asyncio.create_task(runtime.session_release("old-token"))
        self.assertTrue(await asyncio.to_thread(release_started.wait, 1.0))
        new_acquire = asyncio.create_task(runtime.session_acquire("new"))
        acquire_started_while_release_blocked = await asyncio.to_thread(
            acquire_started.wait, 0.2
        )
        release_can_finish.set()
        _release_result, acquire_result = await asyncio.gather(
            old_release, new_acquire
        )

        self.assertFalse(acquire_started_while_release_blocked)
        self.assertEqual(acquire_result["lease_token"], runtime.active_lease_token)
        self.assertEqual(runtime.active_lease_token, "new-token")
        self.assertIsNone(runtime.active_ticket)
        await runtime.call_bridge("query_player_state", {}, "server", 1.0)
        self.assertEqual(sent_tokens, ["new-token"])

    async def test_acquire_serializes_release_and_success_matches_local_state(self) -> None:
        runtime = self._standalone_client()
        acquire_started = threading.Event()
        acquire_can_finish = threading.Event()
        release_started = threading.Event()
        release_can_finish = threading.Event()

        def acquire_request(_method, path, *_args, **_kwargs):
            if path == "/session/acquire":
                acquire_started.set()
                acquire_can_finish.wait(2.0)
                return 200, {"status": "active", "lease_token": "new-token"}
            if path == "/session/release":
                release_started.set()
                release_can_finish.wait(2.0)
                return 200, {"released": True}
            raise AssertionError(path)

        self._attach_control_transport(runtime, acquire_request)
        acquire = asyncio.create_task(runtime.session_acquire("new"))
        self.assertTrue(await asyncio.to_thread(acquire_started.wait, 1.0))
        release = asyncio.create_task(runtime.session_release("new-token"))
        release_started_while_acquire_blocked = await asyncio.to_thread(
            release_started.wait, 0.2
        )
        acquire_can_finish.set()
        acquire_result = await acquire
        self.assertTrue(await asyncio.to_thread(release_started.wait, 1.0))
        success_matches_local_state = (
            acquire_result["lease_token"] == runtime.active_lease_token
        )
        release_can_finish.set()
        await release

        self.assertFalse(release_started_while_acquire_blocked)
        self.assertTrue(success_matches_local_state)
        self.assertIsNone(runtime.active_lease_token)
        self.assertIsNone(runtime.active_ticket)

    async def test_wait_serializes_release_and_success_matches_local_state(self) -> None:
        runtime = self._standalone_client()
        wait_started = threading.Event()
        wait_can_finish = threading.Event()
        release_started = threading.Event()
        release_can_finish = threading.Event()

        def wait_request(_method, path, *_args, **_kwargs):
            if path == "/session/acquire":
                return 202, {"status": "queued", "ticket": "old-ticket"}
            if path == "/session/wait":
                wait_started.set()
                wait_can_finish.wait(2.0)
                return 200, {"status": "active", "lease_token": "wait-token"}
            if path == "/session/release":
                release_started.set()
                release_can_finish.wait(2.0)
                return 200, {"released": True}
            raise AssertionError(path)

        self._attach_control_transport(runtime, wait_request)
        await runtime.session_acquire("queued")
        wait = asyncio.create_task(runtime.session_wait("old-ticket", 0.0))
        self.assertTrue(await asyncio.to_thread(wait_started.wait, 1.0))
        release = asyncio.create_task(runtime.session_release("wait-token"))
        release_started_while_wait_blocked = await asyncio.to_thread(
            release_started.wait, 0.2
        )
        wait_can_finish.set()
        wait_result = await wait
        self.assertTrue(await asyncio.to_thread(release_started.wait, 1.0))
        success_matches_local_state = (
            wait_result["lease_token"] == runtime.active_lease_token
        )
        release_can_finish.set()
        await release

        self.assertFalse(release_started_while_wait_blocked)
        self.assertTrue(success_matches_local_state)
        self.assertIsNone(runtime.active_lease_token)
        self.assertIsNone(runtime.active_ticket)

    async def test_heartbeat_serializes_acquire(self) -> None:
        runtime = self._standalone_client()
        heartbeat_started = threading.Event()
        heartbeat_can_finish = threading.Event()
        acquire_started = threading.Event()
        acquire_calls = 0

        def request_once(_method, path, *_args, **_kwargs):
            nonlocal acquire_calls
            if path == "/session/acquire":
                acquire_calls += 1
                if acquire_calls == 2:
                    acquire_started.set()
                return 200, {"status": "active", "lease_token": "old-token"}
            if path == "/session/heartbeat":
                heartbeat_started.set()
                heartbeat_can_finish.wait(2.0)
                return 200, {"ok": True}
            if path == "/session/status":
                return 200, {
                    "self": {
                        "state": "active",
                        "lease_id": "old-lease-id",
                        "position": 0,
                    }
                }
            raise AssertionError(path)

        self._attach_control_transport(runtime, request_once)
        await runtime.session_acquire("old")
        heartbeat = asyncio.create_task(runtime.session_heartbeat("old-token"))
        self.assertTrue(await asyncio.to_thread(heartbeat_started.wait, 1.0))
        acquire = asyncio.create_task(runtime.session_acquire("new"))
        acquire_started_while_heartbeat_blocked = await asyncio.to_thread(
            acquire_started.wait, 0.2
        )
        heartbeat_can_finish.set()
        _heartbeat_result, acquire_result = await asyncio.gather(heartbeat, acquire)

        self.assertFalse(acquire_started_while_heartbeat_blocked)
        self.assertEqual(acquire_result["lease_token"], runtime.active_lease_token)
        self.assertEqual(runtime.active_lease_token, "old-token")

    async def test_old_enqueue_error_cannot_clear_new_acquire(self) -> None:
        runtime = self._standalone_client()
        enqueue_started = threading.Event()
        enqueue_can_finish = threading.Event()
        acquire_tokens = iter(("old-token", "new-token"))
        sent_tokens: list[str] = []

        def request_once(_method, path, payload=None, *_args, **_kwargs):
            if path == "/session/acquire":
                return 200, {"status": "active", "lease_token": next(acquire_tokens)}
            if path == "/session/release":
                return 200, {"released": True}
            if path == "/enqueue":
                sent_tokens.append(payload["lease_token"])
                enqueue_started.set()
                enqueue_can_finish.wait(2.0)
                return 403, {"error": "lease_invalid"}
            raise AssertionError(path)

        self._attach_scripted_transports(runtime, request_once)
        await runtime.session_acquire("old")
        old_enqueue = asyncio.create_task(
            runtime.call_bridge("world_spawn", {}, "server", 1.0)
        )
        self.assertTrue(await asyncio.to_thread(enqueue_started.wait, 1.0))
        await runtime.session_release("old-token")
        await runtime.session_acquire("new")
        enqueue_can_finish.set()
        with self.assertRaises(Exception) as err:
            await old_enqueue
        self.assertIn("lease_invalid", str(err.exception))
        self.assertEqual(sent_tokens, ["old-token"])
        self.assertEqual(runtime.active_lease_token, "new-token")

    async def test_two_clients_one_daemon_both_get_results(self) -> None:
        # F1: multiple sessions driving one game (offline proxy form).
        srv = self._daemon()
        self._peer(srv, "server")
        client_a = self._client(srv)
        client_b = self._client(srv)
        result_a, result_b = await asyncio.gather(
            client_a.call_bridge("query_player_state", {}, "server", 2.0),
            client_b.call_bridge("query_player_state", {}, "server", 2.0),
        )
        self.assertTrue(result_a["ok"])
        self.assertTrue(result_b["ok"])

    async def test_version_blocked_surfaces_tool_error(self) -> None:
        srv = self._daemon(require_version=True)
        self._peer(srv, "server")  # polls with no version → legacy_blocked
        runtime = self._client(srv)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            status = await runtime.bridge_status_payload()
            if (status.get("server_peer") or {}).get("last_poll_age_s") is not None:
                break
            await asyncio.sleep(0.02)
        with self.assertRaises(Exception) as err:
            await runtime.call_bridge("query_player_state", {}, "server", 1.0)
        self.assertEqual(type(err.exception).__name__, "ToolError")
        self.assertIn("version_blocked", str(err.exception))

    async def test_bridge_status_is_proxied(self) -> None:
        srv = self._daemon()
        self._peer(srv, "server")
        runtime = self._client(srv)
        status = await runtime.bridge_status_payload()
        self.assertIn("server_peer", status)
        self.assertEqual(status["server_version"], core.EXPECTED_BRIDGE_VERSION)

    async def test_business_error_is_tool_error(self) -> None:
        srv = self._daemon()
        peer = GamePeer(srv.base, srv.key, "server",
                        responder=lambda command: {"id": command["id"], "ok": 0, "error": "no_players"})
        peer.start()
        self.peers.append(peer)
        runtime = self._client(srv)
        with self.assertRaises(Exception) as err:
            await runtime.call_bridge("query_player_state", {}, "server", 1.5)
        self.assertEqual(type(err.exception).__name__, "ToolError")
        self.assertIn("no_players", str(err.exception))

    async def test_timeout_includes_liveness(self) -> None:
        # The timeout must carry the peer's measured liveness instead of a
        # fixed string. Daemon alive with no game peer polling -> never polled.
        srv = self._daemon()  # no game peer → command never resolves
        runtime = self._client(srv)
        with self.assertRaises(Exception) as err:
            await runtime.call_bridge("query_player_state", {}, "server", 0.4)
        self.assertEqual(type(err.exception).__name__, "ToolError")
        message = str(err.exception)
        self.assertIn("timeout waiting for", message)
        self.assertTrue(
            "has never polled" in message or "last poll" in message, message
        )
        self.assertIn("queue_depth", message)

    async def test_timeout_degrades_when_peer_status_is_unavailable(self) -> None:
        # Negative control: inject a bridge_status_payload failure. The happy
        # path alone would stay green even if the degradation path were broken.
        srv = self._daemon()
        runtime = self._client(srv)
        with patch.object(
            runtime,
            "bridge_status_payload",
            new=AsyncMock(side_effect=RuntimeError("status down")),
        ):
            with self.assertRaises(Exception) as err:
                await runtime.call_bridge("query_player_state", {}, "server", 0.4)
        message = str(err.exception)
        self.assertIn("timeout waiting for", message)
        self.assertIn("server peer status unavailable", message)
        self.assertNotIn("queue_depth", message)

    async def test_malformed_status_never_replaces_the_timeout_error(self) -> None:
        # The guard covered only the fetch, so a /status
        # answering 200 with an odd shape raised INSIDE the timeout handler and
        # substituted the timeout ToolError with an unrelated AttributeError or
        # TypeError. The invariant: the caller always learns it timed out.
        srv = self._daemon()
        runtime = self._client(srv)
        for malformed in (
            {"server_peer": None},                        # .get on None
            {"server_peer": {"last_poll_age_s": "n/a"}},  # format str as float
            {},                                           # key absent entirely
            {"server_peer": []},                          # wrong container type
        ):
            with self.subTest(malformed=malformed):
                with patch.object(
                    runtime,
                    "bridge_status_payload",
                    new=AsyncMock(return_value=malformed),
                ):
                    with self.assertRaises(Exception) as err:
                        await runtime.call_bridge(
                            "query_player_state", {}, "server", 0.3
                        )
                self.assertEqual(type(err.exception).__name__, "ToolError")
                self.assertIn("timeout waiting for", str(err.exception))

    async def test_build_app_client_mode_routes_tools_through_daemon(self) -> None:
        # Exercises the FastMCP wiring (build_app branch + bridge_status tool using
        # the async accessor), not just ClientRuntime in isolation.
        srv = self._daemon()
        self._peer(srv, "server")
        config = ServerConfig(mode="client", key=srv.key, port=srv.port, log_sink=lambda _m: None)
        runtime = _fixture_client_runtime(config)
        self.assertIsInstance(runtime, server.ClientRuntime)
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, built_runtime = server.build_app(config)
        self.assertIs(built_runtime, runtime)
        self._attach_fixture_transport(runtime, srv)
        result = _content_json(await app.call_tool("query_player_state", {"timeout_s": 2.0}))
        self.assertTrue(result["ok"])
        status = _content_json(await app.call_tool("bridge_status", {}))
        self.assertIn("server_peer", status)
        self.assertEqual(status["server_version"], core.EXPECTED_BRIDGE_VERSION)

    async def test_client_mode_session_tool_enables_existing_mutation(self) -> None:
        srv = self._daemon()
        self._peer(srv, "server")
        config = ServerConfig(
            mode="client", key=srv.key, port=srv.port,
            client_platform="codex", log_sink=lambda _m: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, built_runtime = server.build_app(config)
        self.assertIs(built_runtime, runtime)
        self._attach_fixture_transport(runtime, srv)

        with self.assertRaises(Exception) as denied:
            await app.call_tool(
                "world_spawn",
                {"type": "X", "pos": [1, 2, 3], "timeout_s": 2.0},
            )
        self.assertIn("lease_required", str(denied.exception))

        acquired = _content_json(
            await app.call_tool("session_acquire", {"purpose": "spawn fixture"})
        )
        heartbeat = _content_json(
            await app.call_tool(
                "session_heartbeat", {"lease_token": acquired["lease_token"]}
            )
        )
        self.assertEqual(heartbeat["status"], "active")
        spawned = _content_json(
            await app.call_tool(
                "world_spawn",
                {"type": "X", "pos": [1, 2, 3], "timeout_s": 2.0},
            )
        )
        self.assertTrue(spawned["ok"])
        released = _content_json(
            await app.call_tool(
                "session_release", {"lease_token": acquired["lease_token"]}
            )
        )
        self.assertTrue(released["released"])

    async def test_pure_read_of_all_players_needs_no_lease_while_mutations_do(self) -> None:
        # The observable is the MCP contract, not the HTTP layer. With no
        # lease held, query_all_players completes; the mutating verbs below are
        # the negative control -- without them a fully broken gate still passes.
        srv = self._daemon()
        self._peer(srv, "server")
        config = ServerConfig(
            mode="client", key=srv.key, port=srv.port,
            client_platform="codex", log_sink=lambda _m: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, built_runtime = server.build_app(config)
        self.assertIs(built_runtime, runtime)
        self._attach_fixture_transport(runtime, srv)
        self.assertIsNone(runtime.active_lease_token)

        read = _content_json(
            await app.call_tool("query_all_players", {"timeout_s": 2.0})
        )
        self.assertTrue(read["ok"])

        for name, args in (
            ("world_spawn", {"type": "X", "pos": [1, 2, 3], "timeout_s": 2.0}),
            ("object_delete", {"object_id": 1, "timeout_s": 2.0}),
        ):
            with self.assertRaises(Exception) as denied:
                await app.call_tool(name, args)
            self.assertIn("lease_required", str(denied.exception), name)

    async def test_all_players_read_is_not_blocked_by_another_sessions_lease(self) -> None:
        # Second acceptance clause: with ANOTHER session holding
        # the lease, the pure read still completes. Negative control: the foreign
        # lease does not let this session mutate either.
        srv = self._daemon()
        self._peer(srv, "server")
        holder = self._client(srv, client_platform="codex")
        acquired = await holder.session_acquire("hold the lease")
        self.assertEqual(acquired["status"], "active")

        config = ServerConfig(
            mode="client", key=srv.key, port=srv.port,
            client_platform="claude", log_sink=lambda _m: None,
        )
        reader = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=reader):
            app, _built = server.build_app(config)
        self._attach_fixture_transport(reader, srv)

        read = _content_json(
            await app.call_tool("query_all_players", {"timeout_s": 2.0})
        )
        self.assertTrue(read["ok"])

        with self.assertRaises(Exception) as denied:
            await app.call_tool(
                "world_spawn", {"type": "X", "pos": [1, 2, 3], "timeout_s": 2.0}
            )
        self.assertIn("lease_required", str(denied.exception))

        await holder.session_release(acquired["lease_token"])

    async def test_logs_since_streams_only_new_lines_and_needs_no_lease(self) -> None:
        # F2.1 through the MCP contract: the profile comes from the active run,
        # the second call returns only what was appended, and no lease is held.
        srv = self._daemon()
        config = ServerConfig(
            mode="client", key=srv.key, port=srv.port,
            client_platform="codex", log_sink=lambda _m: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, _built = server.build_app(config)
        self._attach_fixture_transport(runtime, srv)
        self.assertIsNone(runtime.active_lease_token)

        with tempfile.TemporaryDirectory() as directory:
            # Shape it as the worker builds it (dayz_test_worker.py:208-211), or
            # the profiles allowlist refuses it.
            profiles = Path(directory) / "_server" / "profiles"
            profiles.mkdir(parents=True)
            log = profiles / "script_2026-08-07.log"
            log.write_text("boot line\n", encoding="utf-8")
            rpt = profiles / "DayZDiag_x64_2026-08-07.rpt"
            rpt.write_text("engine warning\n", encoding="utf-8")
            lifecycle = AsyncMock(
                return_value={
                    "runs": [
                        {"run_id": "run-a", "profiles": str(profiles)},
                        {"run_id": "run-a", "profiles": str(profiles)},  # deduped
                    ]
                }
            )
            with patch.object(runtime, "lifecycle_status", new=lifecycle):
                first = _content_json(await app.call_tool("logs_since", {}))
                # H2: BOTH streams, not just the newest file -- engine errors
                # live in the RPT while the script log is the one being written.
                emitted = {
                    Path(item["path"]).name: item["lines"] for item in first["files"]
                }
                self.assertEqual(emitted[log.name], ["boot line"])
                self.assertEqual(emitted[rpt.name], ["engine warning"])

                # Negative control: nothing appended -> no lines, same marker.
                idle = _content_json(await app.call_tool("logs_since", {}))
                self.assertEqual(idle["files"], [])
                self.assertEqual(idle["marker"], first["marker"])

                with log.open("a", encoding="utf-8") as handle:
                    handle.write("second line\n")
                second = _content_json(await app.call_tool("logs_since", {}))

        self.assertEqual(len(second["files"]), 1)
        self.assertEqual(second["files"][0]["lines"], ["second line"])
        self.assertNotEqual(second["marker"], first["marker"])
        self.assertFalse(second["files"][0]["rotated"])

    async def test_logs_since_rejects_bad_input_and_reports_no_active_run(self) -> None:
        srv = self._daemon()
        config = ServerConfig(
            mode="client", key=srv.key, port=srv.port,
            client_platform="codex", log_sink=lambda _m: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, _built = server.build_app(config)
        self._attach_fixture_transport(runtime, srv)

        with patch.object(
            runtime, "lifecycle_status", new=AsyncMock(return_value={"runs": []})
        ):
            with self.assertRaises(Exception) as no_run:
                await app.call_tool("logs_since", {})
            self.assertIn("no_active_run", str(no_run.exception))

            with self.assertRaises(Exception) as bad_marker:
                await app.call_tool("logs_since", {"marker": "{not json"})
            self.assertIn("bad_marker", str(bad_marker.exception))

            for bad in (0, 5000):
                with self.assertRaises(Exception) as bad_max:
                    await app.call_tool("logs_since", {"max_lines": bad})
                self.assertIn("bad_args", str(bad_max.exception))

        # H4: a run whose `profiles` is not a run profile directory is refused
        # fail-closed instead of reading whatever host directory it names.
        with patch.object(
            runtime,
            "lifecycle_status",
            new=AsyncMock(
                return_value={
                    "runs": [{"run_id": "r", "profiles": r"C:\Users\example\Documents"}]
                }
            ),
        ):
            with self.assertRaises(Exception) as refused:
                await app.call_tool("logs_since", {})
            self.assertIn("bad_profiles", str(refused.exception))

    async def test_connection_refused_triggers_spawn_and_retry(self) -> None:
        # No daemon initially: the first call must spawn one (self-healing) and retry.
        port = _free_port()
        started: dict[str, DaemonHttpServer] = {}

        def spawn() -> int:
            srv = DaemonHttpServer(_config(key="ckey"), port=port)
            srv.start()
            self.servers.append(srv)
            game = GamePeer(srv.base, srv.key, "server")
            game.start()
            self.peers.append(game)
            started["srv"] = srv
            return 4321  # fake pid

        config = ServerConfig(mode="client", key="ckey", port=port, log_sink=lambda _m: None)
        runtime = _fixture_client_runtime(
            config,
            spawn_fn=spawn,
            probe_fn=lambda *_args, **_kwargs: "srv" in started,
            startup_budget_s=1.0,
        )
        runtime._request_once = lambda method, path, payload=None, query=None, timeout=5.0: (
            (_ for _ in ()).throw(
                control_client.transport.AccreditedTransportError(
                    "daemon_transport_failure",
                    request_stage="pre_request",
                    http_bytes_sent=0,
                )
            )
            if "srv" not in started
            else _http(
                started["srv"].base,
                method,
                path,
                started["srv"].key,
                payload=payload,
                query=query,
                timeout=timeout,
            )
        )
        result = await runtime.call_bridge("query_player_state", {}, "server", 3.0)
        self.assertTrue(result["ok"])
        self.assertIn("srv", started)

    async def test_malformed_enqueue_response_is_tool_error(self) -> None:
        # C-2: a 200 enqueue response missing "id" surfaces as ToolError, not a raw
        # KeyError that crashes the tool.
        config = ServerConfig(mode="client", key="k", port=12345, log_sink=lambda _m: None)
        runtime = _fixture_client_runtime(config)
        runtime._request_once = lambda *_a, **_k: (200, {"unexpected": True})
        with self.assertRaises(Exception) as err:
            await runtime.call_bridge("query_player_state", {}, "server", 1.0)
        self.assertEqual(type(err.exception).__name__, "ToolError")
        self.assertIn("daemon_bad_enqueue_response", str(err.exception))

    def test_decode_body_rejects_non_json_and_non_object(self) -> None:
        # C-2: a non-JSON or non-object 200 body is a clean ToolError, not a crash.
        with self.assertRaises(Exception) as err:
            server.ClientRuntime._decode_body("not json{")
        self.assertEqual(type(err.exception).__name__, "ToolError")
        with self.assertRaises(Exception):
            server.ClientRuntime._decode_body("[1,2,3]")

    def test_client_identity_cli_flags_are_proxy_only(self) -> None:
        config = server.parse_args(
            [
                "--keyfile", "dummy.key", "--client", "--client-platform", "codex",
                "--task-label", "task-four",
            ]
        )

        self.assertEqual(config.mode, "client")
        self.assertEqual(config.client_platform, "codex")
        self.assertEqual(config.task_label, "task-four")
        daemon_argv = server.daemon.build_daemon_argv(config, python="python")
        self.assertNotIn("--client-platform", daemon_argv)
        self.assertNotIn("--task-label", daemon_argv)

    def test_no_daemon_autospawn_cli_is_client_local_and_defaults_on(self) -> None:
        default = server.parse_args(["--keyfile", "dummy.key", "--client"])
        disabled = server.parse_args(
            ["--keyfile", "dummy.key", "--client", "--no-daemon-autospawn"]
        )

        self.assertTrue(default.auto_spawn_daemon)
        self.assertFalse(disabled.auto_spawn_daemon)
        self.assertNotIn(
            "--no-daemon-autospawn", server.daemon.build_daemon_argv(disabled)
        )

    def test_parser_default_remains_embedded_and_config_has_coordination_fields(self) -> None:
        config = server.parse_args(["--keyfile", "dummy.key"])

        self.assertEqual(config.mode, "embedded")
        self.assertEqual(config.client_platform, "unknown")
        self.assertEqual(config.task_label, "")
        self.assertEqual(config.session_ttl_s, 120.0)
        self.assertIsNone(config.runtime_dir)


class ClientRuntimeProvenanceGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.keyfile = self.root / "daemon.key"
        self.keyfile.write_text("fixture-key", encoding="utf-8")
        self.foreign_keyfile = self.root / "foreign.key"
        self.foreign_keyfile.write_text("foreign-key", encoding="utf-8")
        self.launcher = str(Path(sys.executable).resolve())
        native = self.root / "native-python.exe"
        native.write_bytes(b"native-fixture")
        self.native = str(native.resolve())
        self.launch_patch = patch.object(
            host_config, "_local_launch_executable", return_value=self.launcher
        )
        self.native_patch = patch.object(
            host_config, "_local_native_executable", return_value=self.native
        )
        self.launch_patch.start()
        self.native_patch.start()
        self.addCleanup(self.launch_patch.stop)
        self.addCleanup(self.native_patch.stop)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _provenance(self, **updates: object) -> host_config.DaemonProvenance:
        values: dict[str, object] = {
            "launch_executable": self.launcher,
            "native_executable": self.native,
            "argv": (
                self.launcher,
                "-m",
                "dayz_mcp",
                "--daemon",
                "--port",
                "18765",
                "--keyfile",
                str(self.keyfile.resolve()),
            ),
            "cwd": str(_TOOLS_DIR.resolve()),
            "port": 18765,
            "keyfile": str(self.keyfile.resolve()),
            "auto_spawn_daemon": True,
        }
        values.update(updates)
        return host_config.DaemonProvenance(**values)

    def _config(
        self,
        *,
        port: int = 18765,
        keyfile: Path | None = None,
        key: str | None = None,
        auto_spawn_daemon: bool = True,
    ) -> ServerConfig:
        return ServerConfig(
            mode="client",
            key=key,
            keyfile=str((keyfile or self.keyfile).resolve()),
            port=port,
            auto_spawn_daemon=auto_spawn_daemon,
            log_sink=lambda _message: None,
        )

    def test_consensus_precedes_key_read_and_supplies_all_transport_authority(self) -> None:
        events: list[str] = []
        requests: list[dict[str, object]] = []

        def resolve() -> host_config.DaemonProvenance:
            events.append("consensus")
            return self._provenance()

        def read_shared(path: str) -> str:
            events.append("shared_key_read")
            self.assertEqual(Path(path), self.keyfile.resolve())
            return "fixture-key"

        def request(**kwargs: object) -> tuple[int, bytes]:
            requests.append(kwargs)
            return 200, b"{}"

        with (
            patch.object(host_config, "resolve_daemon_provenance", side_effect=resolve),
            patch.object(
                server,
                "read_key",
                side_effect=AssertionError("client must not keep a second key"),
            ),
            patch.object(
                server.daemon_credential.pinned_keyfile,
                "read_pinned_keyfile",
                side_effect=read_shared,
            ),
            patch.object(
                server.orphan_guard,
                "verified_daemon_http_request",
                side_effect=request,
            ),
        ):
            runtime = server.ClientRuntime(self._config())
            runtime._request_once("GET", "/status")

        self.assertEqual(events.count("shared_key_read"), 1)
        key_read_index = events.index("shared_key_read")
        self.assertGreaterEqual(key_read_index, 2)
        self.assertTrue(
            all(event == "consensus" for event in events[:key_read_index])
        )
        self.assertEqual(runtime.port, 18765)
        self.assertEqual(runtime._daemon_executable, self.native)
        self.assertEqual(tuple(runtime._daemon_argv), self._provenance().argv)
        self.assertEqual(runtime._daemon_cwd, str(_TOOLS_DIR.resolve()))
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["expected_executable"], self.native)
        self.assertEqual(tuple(requests[0]["expected_argv"]), self._provenance().argv)
        self.assertEqual(requests[0]["expected_cwd"], str(_TOOLS_DIR.resolve()))

    def test_unavailable_incomplete_or_conflicting_consensus_has_zero_key_or_request(self) -> None:
        for error in (
            "daemon_provenance_unavailable",
            "daemon_provenance_incomplete",
            "daemon_provenance_conflict",
        ):
            key_reads: list[str] = []
            requests: list[dict[str, object]] = []
            with (
                self.subTest(error=error),
                patch.object(
                    host_config,
                    "resolve_daemon_provenance",
                    side_effect=host_config.HostConfigError(error),
                ),
                patch.object(server, "read_key", side_effect=key_reads.append),
                patch.object(
                    server.orphan_guard,
                    "verified_daemon_http_request",
                    side_effect=lambda **kwargs: requests.append(kwargs),
                ),
                self.assertRaisesRegex(host_config.HostConfigError, f"^{error}$"),
            ):
                server.ClientRuntime(self._config())
            self.assertEqual(key_reads, [])
            self.assertEqual(requests, [])

    def test_port_keyfile_or_swapped_provenance_conflict_before_key_or_request(self) -> None:
        cases = (
            (self._config(port=18766), self._provenance()),
            (self._config(keyfile=self.foreign_keyfile), self._provenance()),
            (
                self._config(),
                self._provenance(
                    argv=(self.native, "-m", "dayz_mcp", "--daemon")
                ),
            ),
        )
        for config, provenance in cases:
            key_reads: list[str] = []
            requests: list[dict[str, object]] = []
            with (
                self.subTest(config=config, provenance=provenance),
                patch.object(
                    host_config,
                    "resolve_daemon_provenance",
                    return_value=provenance,
                ),
                patch.object(server, "read_key", side_effect=key_reads.append),
                patch.object(
                    server.orphan_guard,
                    "verified_daemon_http_request",
                    side_effect=lambda **kwargs: requests.append(kwargs),
                ),
                self.assertRaisesRegex(
                    host_config.HostConfigError, "^daemon_provenance_conflict$"
                ),
            ):
                server.ClientRuntime(config)
            self.assertEqual(key_reads, [])
            self.assertEqual(requests, [])

    def test_injected_key_cannot_bypass_consensus(self) -> None:
        with (
            patch.object(
                host_config,
                "resolve_daemon_provenance",
                side_effect=host_config.HostConfigError(
                    "daemon_provenance_unavailable"
                ),
            ),
            patch.object(server, "read_key") as read,
            self.assertRaisesRegex(
                host_config.HostConfigError, "^daemon_provenance_unavailable$"
            ),
        ):
            server.ClientRuntime(self._config(key="already-loaded"))
        read.assert_not_called()

    def test_coherent_launch_native_swap_fails_before_key_connect_or_request(self) -> None:
        swapped = self._provenance(
            launch_executable=self.native,
            native_executable=self.launcher,
            argv=(self.native, "-m", "dayz_mcp", "--daemon"),
        )
        key_reads: list[str] = []
        requests: list[dict[str, object]] = []
        with (
            patch.object(
                host_config, "resolve_daemon_provenance", return_value=swapped
            ),
            patch.object(server, "read_key", side_effect=key_reads.append),
            patch.object(
                server.orphan_guard,
                "verified_daemon_http_request",
                side_effect=lambda **kwargs: requests.append(kwargs),
            ),
            patch.object(
                server.orphan_guard.http.client, "HTTPConnection"
            ) as connection,
            self.assertRaisesRegex(
                host_config.HostConfigError, "^daemon_provenance_conflict$"
            ),
        ):
            server.ClientRuntime(self._config())

        self.assertEqual(key_reads, [])
        connection.assert_not_called()
        self.assertEqual(requests, [])

    def test_auto_spawn_cli_override_does_not_crash_construction(self) -> None:
        # Host registration may have auto_spawn=True while this process passed
        # --no-daemon-autospawn. Construction must succeed; spawn stays off.
        spawns: list[bool] = []
        with patch.object(
            host_config,
            "resolve_daemon_provenance",
            return_value=self._provenance(auto_spawn_daemon=True),
        ):
            runtime = server.ClientRuntime(
                self._config(auto_spawn_daemon=False),
                spawn_fn=lambda: spawns.append(True) or 4321,
                probe_fn=lambda *_args, **_kwargs: False,
                startup_budget_s=0.01,
            )

        self.assertFalse(runtime._auto_spawn_daemon)
        self.assertFalse(runtime._ensure_daemon())
        self.assertEqual(spawns, [])

    def test_registration_false_cli_true_does_not_conflict(self) -> None:
        # CLI is the spawn authority: host registration may disable autospawn
        # while this process did not pass --no-daemon-autospawn.
        spawns: list[bool] = []
        with patch.object(
            host_config,
            "resolve_daemon_provenance",
            return_value=self._provenance(auto_spawn_daemon=False),
        ):
            runtime = server.ClientRuntime(
                self._config(auto_spawn_daemon=True),
                spawn_fn=lambda: spawns.append(True) or 4321,
                probe_fn=lambda *_args, **_kwargs: False,
                startup_budget_s=0.01,
            )

        self.assertTrue(runtime._auto_spawn_daemon)
        self.assertFalse(runtime._ensure_daemon())
        self.assertEqual(spawns, [True])

    def test_consensual_no_autospawn_is_the_runtime_authority(self) -> None:
        spawns: list[bool] = []
        with patch.object(
            host_config,
            "resolve_daemon_provenance",
            return_value=self._provenance(auto_spawn_daemon=False),
        ):
            runtime = server.ClientRuntime(
                self._config(auto_spawn_daemon=False),
                spawn_fn=lambda: spawns.append(True) or 4321,
                probe_fn=lambda *_args, **_kwargs: False,
                startup_budget_s=0.01,
            )

        self.assertFalse(getattr(runtime, "_auto_spawn_daemon", True))
        self.assertFalse(runtime._ensure_daemon())
        self.assertEqual(spawns, [])


class ClientDaemonStartupBudgetTest(unittest.TestCase):
    class Clock:
        def __init__(self) -> None:
            self.value = 100.0
            self.sleeps: list[float] = []

        def now(self) -> float:
            return self.value

        def sleep(self, duration: float) -> None:
            self.sleeps.append(duration)
            self.value += duration

    def test_single_budget_exceeds_drain_activation_and_margin(self) -> None:
        self.assertGreater(
            server.daemon.DAEMON_STARTUP_BUDGET_S,
            server.daemon.MIGRATION_CANDIDATE_DRAIN_S
            + server.daemon.STATUS_ACTIVATION_TIMEOUT_S
            + server.daemon.STARTUP_BUDGET_MARGIN_S,
        )
        self.assertLessEqual(
            server.daemon.DAEMON_STARTUP_BUDGET_S,
            server.daemon.MAX_DAEMON_STARTUP_BUDGET_S,
        )

    def test_launch_argv_and_native_listener_image_are_distinct(self) -> None:
        launcher = r"C:\venv\Scripts\python.exe"
        native_image = r"C:\Python314\python.exe"
        with tempfile.TemporaryDirectory() as directory:
            keyfile = Path(directory) / "daemon.key"
            keyfile.write_text("fixture-key", encoding="utf-8")
            config = ServerConfig(
                mode="client",
                key="fixture-key",
                keyfile=str(keyfile.resolve()),
                port=12345,
                log_sink=lambda _message: None,
            )
            provenance = host_config.DaemonProvenance(
                launch_executable=launcher,
                native_executable=native_image,
                argv=(launcher, "-m", "dayz_mcp", "--daemon"),
                cwd=str(_TOOLS_DIR.resolve()),
                port=12345,
                keyfile=str(keyfile.resolve()),
                auto_spawn_daemon=True,
            )
            with (
                patch.object(
                    host_config,
                    "resolve_daemon_provenance",
                    return_value=provenance,
                ),
                patch.object(
                    host_config,
                    "_local_launch_executable",
                    return_value=launcher,
                ),
                patch.object(
                    host_config,
                    "_local_native_executable",
                    return_value=native_image,
                ),
            ):
                runtime = server.ClientRuntime(config)

        self.assertEqual(runtime._daemon_executable, native_image)
        self.assertEqual(runtime._daemon_argv[0], launcher)

    def test_client_wait_uses_injectable_finite_shared_budget(self) -> None:
        clock = self.Clock()
        spawns: list[bool] = []
        runtime = _fixture_client_runtime(
            ServerConfig(
                mode="client", key="fixture-key", port=12345,
                log_sink=lambda _message: None,
            ),
            spawn_fn=lambda: spawns.append(True) or 4321,
            probe_fn=lambda *_args, **_kwargs: False,
            time_fn=clock.now,
            sleep_fn=clock.sleep,
            startup_budget_s=1.0,
        )

        self.assertFalse(runtime._ensure_daemon())
        self.assertEqual(spawns, [True])
        self.assertGreaterEqual(clock.value, 101.0)
        self.assertTrue(clock.sleeps)

    def test_non_finite_or_out_of_range_budget_fails_closed(self) -> None:
        config = ServerConfig(
            mode="client", key="fixture-key", port=12345,
            log_sink=lambda _message: None,
        )
        for value in (float("nan"), float("inf"), 0.0, -1.0, 3601.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _fixture_client_runtime(config, startup_budget_s=value)

    def test_one_absolute_deadline_is_shared_from_first_probe(self) -> None:
        clock = self.Clock()
        deadlines: list[float] = []

        def probe(*_args: object, **kwargs: object) -> bool:
            deadlines.append(float(kwargs["deadline"]))
            clock.value += 0.2
            return False

        runtime = _fixture_client_runtime(
            ServerConfig(
                mode="client", key="fixture-key", port=12345,
                keyfile=r"C:\runtime\key.txt", log_sink=lambda _message: None,
            ),
            spawn_fn=lambda: 4321,
            probe_fn=probe,
            time_fn=clock.now,
            sleep_fn=clock.sleep,
            startup_budget_s=1.0,
        )

        self.assertFalse(runtime._ensure_daemon())
        self.assertGreaterEqual(len(deadlines), 2)
        self.assertEqual(set(deadlines), {101.0})

    def test_budget_exhausted_by_first_probe_never_spawns(self) -> None:
        clock = self.Clock()
        spawns: list[bool] = []

        def probe(*_args: object, **_kwargs: object) -> bool:
            clock.value += 1.0
            return False

        runtime = _fixture_client_runtime(
            ServerConfig(
                mode="client", key="fixture-key", port=12345,
                keyfile=r"C:\runtime\key.txt", log_sink=lambda _message: None,
            ),
            spawn_fn=lambda: spawns.append(True) or 4321,
            probe_fn=probe,
            time_fn=clock.now,
            sleep_fn=clock.sleep,
            startup_budget_s=1.0,
        )

        self.assertFalse(runtime._ensure_daemon())
        self.assertEqual(spawns, [])

    def test_direct_client_request_uses_connected_verified_transport(self) -> None:
        config = ServerConfig(
            mode="client", key="fixture-key", port=12345,
            keyfile=r"C:\runtime\key.txt", log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        expected = (200, b'{"ok":true}')
        with patch.object(
            server.orphan_guard,
            "verified_daemon_http_request",
            return_value=expected,
            create=True,
        ) as verified:
            status, payload = runtime._request_once("GET", "/status", timeout=2.0)

        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True})
        kwargs = verified.call_args.kwargs
        self.assertIsInstance(kwargs["expected_argv"], list)
        self.assertTrue(kwargs["expected_argv"])
        self.assertEqual(kwargs["expected_executable"], runtime._daemon_executable)
        self.assertEqual(kwargs["expected_argv"][0], runtime._daemon_argv[0])
        self.assertIsInstance(kwargs["expected_cwd"], str)
        self.assertTrue(kwargs["expected_cwd"])
        self.assertIn("deadline", kwargs)

    def test_direct_client_request_preserves_http_contract(self) -> None:
        runtime = _fixture_client_runtime(
            ServerConfig(
                mode="client", key="fixture-key", port=12345,
                keyfile=r"C:\runtime\key.txt", log_sink=lambda _message: None,
            )
        )
        with patch.object(
            server.orphan_guard,
            "verified_daemon_http_request",
            return_value=(202, b'{"queued":true}'),
        ) as verified:
            status, payload = runtime._request_once(
                "POST", "/enqueue", {"cmd": "fixture"}, {"peer": "client"}, 2.0
            )

        self.assertEqual((status, payload), (202, {"queued": True}))
        kwargs = verified.call_args.kwargs
        self.assertEqual(kwargs["method"], "POST")
        self.assertEqual(kwargs["path"], "/enqueue")
        self.assertEqual(kwargs["query"], {"peer": "client"})
        self.assertEqual(kwargs["body"], b'{"cmd":"fixture"}')
        self.assertEqual(kwargs["headers"], {"Content-Type": "application/json"})
        self.assertEqual(kwargs["key"], "fixture-key")


class StrictParserTest(unittest.TestCase):
    def test_abbreviated_options_are_rejected_before_runtime(self) -> None:
        with self.assertRaises(SystemExit):
            server.parse_args(["--keyf", "K", "--client"])

    def test_silent_parser_matches_cli_without_writing_output(self) -> None:
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        from dayz_mcp.server_cli import parse_server_tail_silent

        cases = (
            (["--client", "--keyfile", "K", "--idle-timeout", "-1"], "parsed", "client"),
            (["--client", "--keyfile=K", "--task-label=-nightly"], "parsed", "client"),
            (["--client", "--client", "--keyfile", "K"], "parsed", "client"),
            (["--daemon", "--keyfile", "K"], "parsed", "daemon"),
            (["--client", "--keyfile", "K", "--task-label", "-nightly"], "invalid", None),
            (["--client", "--keyfile", "K", "--task-label", "--daemon"], "invalid", None),
            (["--client", "--keyf", "K"], "invalid", None),
            (["--help"], "terminal", None),
        )
        for argv, expected_status, expected_mode in cases:
            with self.subTest(argv=argv):
                stdout = StringIO()
                stderr = StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    result = parse_server_tail_silent(list(argv))
                self.assertEqual(result.status, expected_status)
                self.assertEqual(
                    None if result.namespace is None else result.namespace.mode,
                    expected_mode,
                )
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
