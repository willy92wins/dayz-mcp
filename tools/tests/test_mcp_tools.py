from __future__ import annotations

import asyncio
import ctypes
import json
import os
import socket
import threading
import time
import unittest
import urllib.parse
import urllib.request
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable
from unittest.mock import AsyncMock, patch

from dayz_mcp import core
from dayz_mcp import server as server_module
from dayz_mcp.server import EXPECTED_BRIDGE_VERSION, ServerConfig, Runtime, build_app
from tests.fence_helpers import INST_CLIENT, INST_SERVER, bind_both_peers


def _content_json(content: Any) -> dict[str, Any]:
    if isinstance(content, tuple):
        _blocks, structured = content
        if isinstance(structured, dict):
            return structured
        content = _blocks
    text = content[0].text
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise AssertionError(f"expected dict content, got {parsed!r}")
    return parsed


def _assert_tool_error(testcase: unittest.TestCase, exc: BaseException) -> None:
    testcase.assertEqual(type(exc).__name__, "ToolError")


class FakePeer:
    def __init__(
        self,
        runtime: Runtime,
        key: str,
        peer: str,
        version: str | None = None,
        result_delay_s: float = 0.0,
        responder: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        if runtime.loopback is None or runtime.loopback.httpd is None:
            raise RuntimeError("runtime loopback not started")
        host, port = runtime.loopback.httpd.server_address
        self.base = f"http://{host}:{port}"
        self.key = key
        self.peer = peer
        self.version = version
        self.result_delay_s = result_delay_s
        self.responder = responder or self.default_responder
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.max_batch_size = 0
        self.commands_seen: list[dict[str, Any]] = []

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2.0)

    def default_responder(self, command: dict[str, Any]) -> dict[str, Any]:
        # Bridge serializes Enforce bool as int 0/1; mirror that here so the
        # ok-handling path is exercised against the real wire type, not Python bool.
        result: dict[str, Any] = {
            "id": command["id"],
            "ok": 1,
            "cmd": command["cmd"],
            "args": command.get("args", {}),
        }
        if command["cmd"] == "query_player_state":
            result["state"] = {"name": "fake", "pos": [1.0, 2.0, 3.0]}
        if command["cmd"] == "camera_get":
            result["camera"] = {"ok": 1, "viewport_moved": 1, "pos": [1.0, 2.0, 3.0]}
        if command["cmd"] == "camera_set":
            result["camera"] = {"ok": 1, "applied_mode": command.get("args", {}).get("cam_mode", "")}
        return result

    def request(self, method: str, path: str, payload: dict | None = None, query: dict | None = None) -> dict:
        params = dict(query or {})
        params["key"] = self.key
        url = self.base + path + "?" + urllib.parse.urlencode(params)
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=2.0) as response:
            return json.loads(response.read().decode("utf-8"))

    def run(self) -> None:
        while not self.stop_event.is_set():
            query = {"peer": self.peer}
            query["inst"] = INST_SERVER if self.peer == "server" else INST_CLIENT
            if self.version is not None:
                query["ver"] = self.version
            try:
                body = self.request("GET", "/poll", query=query)
                commands = body.get("commands", [])
                self.max_batch_size = max(self.max_batch_size, len(commands))
                for command in commands:
                    self.commands_seen.append(command)
                    if self.result_delay_s > 0.0:
                        time.sleep(self.result_delay_s)
                    result = self.responder(command)
                    self.request(
                        "POST",
                        "/result",
                        payload=result,
                        query={"inst": query["inst"]},
                    )
            except Exception:
                time.sleep(0.02)
            time.sleep(0.02)


class MCPToolsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.key = "test-key"
        self.peers: list[FakePeer] = []
        self.runtimes: list[Runtime] = []

    async def asyncTearDown(self) -> None:
        await asyncio.gather(*(asyncio.to_thread(peer.stop) for peer in self.peers))
        await asyncio.gather(*(asyncio.to_thread(runtime.stop_loopback) for runtime in self.runtimes))

    def build_started(self, **config_kwargs: Any):
        app, runtime = build_app(ServerConfig(key=self.key, port=0, log_sink=lambda _message: None, **config_kwargs))
        runtime.start_loopback()
        bind_both_peers(runtime.state)
        self.runtimes.append(runtime)
        return app, runtime

    def start_peer(self, runtime: Runtime, peer: str, version: str | None = None, result_delay_s: float = 0.0) -> FakePeer:
        fake = FakePeer(runtime, self.key, peer, version=version, result_delay_s=result_delay_s)
        fake.start()
        self.peers.append(fake)
        return fake

    async def wait_for(self, predicate: Callable[[], bool], timeout_s: float = 1.0) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.02)
        self.fail("condition not reached")

    async def test_happy_path_server_and_client_tools(self) -> None:
        app, runtime = self.build_started()
        self.start_peer(runtime, "server")
        self.start_peer(runtime, "client")

        state = _content_json(await app.call_tool("query_player_state", {"timeout_s": 1.0}))
        self.assertTrue(state["ok"])
        self.assertEqual(state["state"]["pos"], [1.0, 2.0, 3.0])

        spawned = _content_json(await app.call_tool("world_spawn", {"type": "Hatchback_02", "pos": [1, 2, 3], "timeout_s": 1.0}))
        self.assertTrue(spawned["ok"])
        self.assertEqual(spawned["args"]["type"], "Hatchback_02")

        deleted = _content_json(await app.call_tool("object_delete", {"object_id": 7, "timeout_s": 1.0}))
        self.assertTrue(deleted["ok"])
        self.assertEqual(deleted["args"]["object_id"], 7)

        notified = _content_json(
            await app.call_tool(
                "notify_players",
                {"show_time": 5.0, "title": "Game Master", "detail": "Evento iniciado", "timeout_s": 1.0},
            )
        )
        self.assertTrue(notified["ok"])
        self.assertEqual(notified["args"]["title"], "Game Master")

        camera = _content_json(await app.call_tool("camera_get", {"timeout_s": 1.0}))
        self.assertTrue(camera["ok"])
        self.assertEqual(camera["camera"]["pos"], [1.0, 2.0, 3.0])

    async def test_session_tools_require_client_mode(self) -> None:
        app, _runtime = self.build_started()

        with self.assertRaises(Exception) as err:
            await app.call_tool("session_acquire", {"purpose": "x"})

        _assert_tool_error(self, err.exception)
        self.assertIn("session_tools_require_client_mode", str(err.exception))

    async def test_session_tools_validate_locally_before_http(self) -> None:
        from tests.test_client_mode import _fixture_client_runtime

        config = ServerConfig(
            mode="client", key=self.key, port=12345,
            client_platform="codex", log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, built_runtime = build_app(config)
        self.assertIs(built_runtime, runtime)
        runtime._call = lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("unexpected HTTP")
        )
        cases = (
            ("session_acquire", {"purpose": " "}, "bad_purpose"),
            ("session_wait", {"ticket": ""}, "bad_ticket"),
            (
                "session_wait",
                {"ticket": "ticket", "timeout_s": 30.1},
                "bad_wait_timeout",
            ),
            (
                "session_acquire_wait",
                {"purpose": "build", "max_wait_s": 0.0},
                "bad_wait_timeout",
            ),
            ("session_heartbeat", {"lease_token": ""}, "bad_lease_token"),
            ("session_release", {"lease_token": ""}, "bad_lease_token"),
        )

        for tool_name, arguments, expected in cases:
            with self.subTest(tool=tool_name, arguments=arguments):
                with self.assertRaises(Exception) as err:
                    await app.call_tool(tool_name, arguments)
                _assert_tool_error(self, err.exception)
                self.assertIn(expected, str(err.exception))

    def test_exact_session_tool_surface_has_no_force_or_mutation_token_args(self) -> None:
        app, _runtime = build_app(
            ServerConfig(key=self.key, port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name: tool for tool in app._tool_manager.list_tools()}

        self.assertTrue(
            {
                "session_acquire", "session_wait", "session_heartbeat",
                "session_release", "session_status", "session_acquire_wait",
                "lease_acquire",
            }.issubset(tools)
        )
        self.assertNotIn("force_release", tools)
        self.assertIn("dayz_test_run", tools)
        self.assertIn("dayz_test_stop", tools)
        forbidden = {
            "dev_root", "source", "executable", "pid", "argv",
            "lease_token", "environment",
        }
        for name in ("dayz_test_run", "dayz_test_stop"):
            properties = set(tools[name].parameters.get("properties", {}))
            self.assertTrue(properties.isdisjoint(forbidden), (name, properties))
        self.assertEqual(
            set(tools["dayz_test_stop"].parameters.get("properties", {})),
            {"run_id"},
        )
        for name, tool in tools.items():
            if not name.startswith("session_"):
                self.assertNotIn(
                    "lease_token", tool.parameters.get("properties", {}), msg=name
                )

    async def test_dayz_test_operation_serializes_same_runtime_session_tools(self) -> None:
        from tests.test_client_mode import _fixture_client_runtime

        config = ServerConfig(
            mode="client",
            key=self.key,
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        started = asyncio.Event()
        finish = asyncio.Event()

        async def execute_run(*_args: object, **kwargs: object) -> dict[str, Any]:
            started.set()
            await kwargs["progress_cb"]("validating", None)
            await finish.wait()
            return {
                "status": "succeeded",
                "project": "ExampleMod",
                "mode": "offline",
                "run_id": "12345678-1234-4234-8234-1234567890ab",
                "phase": "completed",
                "elapsed_s": 0.1,
                "artifacts_paths": [],
                "error_code": None,
                "cleanup_degraded": False,
            }

        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, built_runtime = build_app(config)
        with patch.object(
            server_module.dayz_test_tool,
            "execute_dayz_test_run",
            side_effect=execute_run,
        ), patch.object(
            runtime, "session_status", new=AsyncMock(return_value={"self": {"state": "none"}})
        ) as session_status:
            self.assertIs(built_runtime, runtime)
            running = asyncio.create_task(
                app.call_tool(
                    "dayz_test_run",
                    {"project": "ExampleMod", "mode": "server"},
                )
            )
            await started.wait()
            peek_count = session_status.await_count
            self.assertGreaterEqual(peek_count, 1)
            status = asyncio.create_task(app.call_tool("session_status", {}))
            await asyncio.sleep(0)
            self.assertEqual(session_status.await_count, peek_count)
            finish.set()
            await running
            await status

        self.assertEqual(session_status.await_count, peek_count + 1)

    async def test_dayz_test_launcher_backend_code_travels_without_its_detail(self) -> None:
        # Ficha ae65: build=true died as dayz_test_failed:NativeLauncherBackendError
        # and the code, one frame away, never reached the caller. The backend
        # names its cause with a source constant: that token crosses, the local
        # detail (host paths) does not, and a code that is not a bare token
        # stays mute exactly as before.
        from dayz_mcp.native_launcher_backend import NativeLauncherBackendError
        from tests.test_client_mode import _fixture_client_runtime

        config = ServerConfig(
            mode="client",
            key=self.key,
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = build_app(config)

        cases = (
            (
                NativeLauncherBackendError(
                    "invalid_native_launcher_environment", r"secret C:\Users\host"
                ),
                "NativeLauncherBackendError:invalid_native_launcher_environment",
            ),
            (
                NativeLauncherBackendError(r"C:\Users\host\secret"),
                "NativeLauncherBackendError",
            ),
        )
        for error, expected in cases:

            async def boom(*_args: object, **_kwargs: object) -> dict[str, Any]:
                raise error

            with patch.object(
                server_module.dayz_test_tool, "execute_dayz_test_run", side_effect=boom
            ):
                with self.assertRaises(Exception) as err:
                    await app.call_tool(
                        "dayz_test_run", {"project": "ExampleMod", "mode": "server"}
                    )
            message = str(err.exception)
            _assert_tool_error(self, err.exception)
            self.assertIn("dayz_test_failed:", message, expected)
            tail = message.split("dayz_test_failed:", 1)[1].split()[0].rstrip(".,;)")
            self.assertEqual(tail, expected)
            self.assertNotIn("secret", message, expected)
            self.assertNotIn("host", message, expected)

    async def test_fb_c9ca_fine_code_crosses_the_wire_as_a_fourth_part(self) -> None:
        from dayz_mcp.native_launcher_backend import NativeLauncherBackendError
        from tests.test_client_mode import _fixture_client_runtime

        config = ServerConfig(
            mode="client",
            key=self.key,
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = build_app(config)

        cases = (
            (
                NativeLauncherBackendError(
                    "native_job_cleanup_incomplete",
                    r"secret C:\Users\host",
                    fine_code="active_zero_never_observed",
                ),
                "NativeLauncherBackendError:native_job_cleanup_incomplete:active_zero_never_observed",
            ),
            (
                NativeLauncherBackendError(
                    "native_job_cleanup_incomplete",
                    (
                        "drain_s=0.0"
                        " second_wait=True"
                        " open_handles=0"
                        " continues=4"
                    ),
                    fine_code="active_zero_wait_timed_out",
                ),
                "NativeLauncherBackendError:native_job_cleanup_incomplete:active_zero_wait_timed_out",
            ),
            (
                NativeLauncherBackendError(
                    "native_job_cleanup_incomplete",
                    r"secret C:\Users\host",
                    fine_code=r"C:\x",
                ),
                "NativeLauncherBackendError:native_job_cleanup_incomplete",
            ),
            (
                NativeLauncherBackendError(
                    "native_job_cleanup_incomplete",
                    r"secret C:\Users\host",
                    fine_code="has a space",
                ),
                "NativeLauncherBackendError:native_job_cleanup_incomplete",
            ),
            (
                NativeLauncherBackendError(
                    "native_job_cleanup_incomplete",
                    r"secret C:\Users\host",
                    fine_code=None,
                ),
                "NativeLauncherBackendError:native_job_cleanup_incomplete",
            ),
            (
                RuntimeError(r"boom C:\Users\host\secret"),
                "RuntimeError",
            ),
        )
        for error, expected in cases:

            async def boom(*_args: object, **_kwargs: object) -> dict[str, Any]:
                raise error

            with patch.object(
                server_module.dayz_test_tool, "execute_dayz_test_run", side_effect=boom
            ):
                with self.assertRaises(Exception) as err:
                    await app.call_tool(
                        "dayz_test_run", {"project": "ExampleMod", "mode": "server"}
                    )
            message = str(err.exception)
            _assert_tool_error(self, err.exception)
            self.assertIn("dayz_test_failed:", message, expected)
            tail = message.split("dayz_test_failed:", 1)[1].split()[0].rstrip(".,;)")
            self.assertEqual(tail, expected)
            self.assertNotIn("secret", message, expected)
            self.assertNotIn("host", message, expected)
            self.assertNotIn("drain_s", message, expected)
            self.assertNotIn("open_handles", message, expected)

    async def test_dayz_test_untyped_failure_carries_the_exception_type(self) -> None:
        # The bare `except Exception` swallowed the cause, which is exactly
        # what makes build:true undiagnosable. A non-existent `project` would NOT
        # exercise this branch -- it leaves through DayzTestToolError -- so the
        # gate needs an untyped error, and that typed error is the control below.
        from tests.test_client_mode import _fixture_client_runtime

        config = ServerConfig(
            mode="client",
            key=self.key,
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = build_app(config)

        async def boom(*_args: object, **_kwargs: object) -> dict[str, Any]:
            raise RuntimeError(r"boom C:\Users\host\secret")

        for tool, args, target in (
            (
                "dayz_test_run",
                {"project": "ExampleMod", "mode": "server"},
                "execute_dayz_test_run",
            ),
            (
                "dayz_test_stop",
                {"run_id": "12345678-1234-4234-8234-1234567890ab"},
                "execute_dayz_test_stop",
            ),
        ):
            with patch.object(server_module.dayz_test_tool, target, side_effect=boom):
                with self.assertRaises(Exception) as err:
                    await app.call_tool(tool, args)
            message = str(err.exception)
            _assert_tool_error(self, err.exception)
            self.assertIn("dayz_test_failed:RuntimeError", message, tool)
            # Type only: the message can carry host paths and must not travel.
            self.assertNotIn("boom", message, tool)
            self.assertNotIn("secret", message, tool)

        # Negative control: the typed branch still surfaces its bare code, with
        # no suffix. Raised directly so the outcome does not depend on whichever
        # code the real launcher registry would reject an unknown project with.
        async def typed(*_args: object, **_kwargs: object) -> dict[str, Any]:
            raise server_module.dayz_test_tool.DayzTestToolError("bad_project")

        with patch.object(
            server_module.dayz_test_tool, "execute_dayz_test_run", side_effect=typed
        ):
            with self.assertRaises(Exception) as typed_err:
                await app.call_tool(
                    "dayz_test_run", {"project": "ExampleMod", "mode": "server"}
                )
        typed_message = str(typed_err.exception)
        self.assertIn("bad_project", typed_message)
        self.assertNotIn("dayz_test_failed", typed_message)

    async def test_negative_args_are_tool_errors(self) -> None:
        app, runtime = self.build_started()
        self.start_peer(runtime, "server")
        self.start_peer(runtime, "client")

        with self.assertRaises(Exception) as missing:
            await app.call_tool("world_spawn", {"pos": [1, 2, 3], "timeout_s": 1.0})
        _assert_tool_error(self, missing.exception)
        self.assertIn("Field required", str(missing.exception))

        with self.assertRaises(Exception) as bad_matrix:
            await app.call_tool("camera_set", {"cam_mode": "matrix", "cam_matrix": [1, 2, 3], "timeout_s": 1.0})
        _assert_tool_error(self, bad_matrix.exception)
        self.assertIn("bad_args", str(bad_matrix.exception))

        with self.assertRaises(Exception) as bad_delete:
            await app.call_tool("object_delete", {"object_id": 0, "timeout_s": 1.0})
        _assert_tool_error(self, bad_delete.exception)
        self.assertIn("bad_args", str(bad_delete.exception))

        with self.assertRaises(Exception) as bad_notify:
            await app.call_tool("notify_players", {"show_time": 0.0, "title": "Game Master", "timeout_s": 1.0})
        _assert_tool_error(self, bad_notify.exception)
        self.assertIn("bad_args", str(bad_notify.exception))

    async def test_camera_set_look_at_alias_is_normalized_for_the_wire(self) -> None:
        # `lookat` is the value the game matches on
        # (MCPClientBridge.c:1741), but the vector argument beside it is spelled
        # `look_at`, so callers reach for the underscore and get a bare bad_args.
        # The alias must be NORMALIZED, not merely accepted: every branch forwards
        # cam_mode verbatim, so passing the caller's spelling through would trade a
        # clean local rejection for one that only shows up in-game.
        app, runtime = self.build_started()
        self.start_peer(runtime, "server")
        client = self.start_peer(runtime, "client")

        await app.call_tool(
            "camera_set",
            {"cam_mode": "look_at", "cam_pos": [1, 2, 3], "look_at": [4, 5, 6], "timeout_s": 5.0},
        )

        sent = [command for command in client.commands_seen if command["cmd"] == "camera_set"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["args"]["cam_mode"], "lookat")
        self.assertEqual(sent[0]["args"]["look_at"], [4.0, 5.0, 6.0])

        # Negative control: a near-miss that is NOT the documented alias must still
        # be rejected -- the fix adds one spelling, it does not relax the mode set.
        with self.assertRaises(Exception) as unknown:
            await app.call_tool(
                "camera_set", {"cam_mode": "lookAt", "cam_pos": [1, 2, 3], "timeout_s": 1.0}
            )
        _assert_tool_error(self, unknown.exception)
        message = str(unknown.exception)
        self.assertIn("bad_args", message)
        # The rejection has to name the way out, or the next caller guesses again.
        for mode in ("orient", "lookat", "matrix", "free"):
            self.assertIn(mode, message)

    async def test_timeout_has_an_upper_bound(self) -> None:
        # Only <=0 and non-finite were rejected, so a caller could
        # pin an operation far past MAX_OPERATION_PIN_S. The 299 s call below is
        # the negative control: it must still be accepted and complete.
        app, runtime = self.build_started()
        self.start_peer(runtime, "server")
        self.assertEqual(server_module.MAX_TIMEOUT_S, 300.0)

        for rejected in (1e6, server_module.MAX_TIMEOUT_S + 0.5):
            with self.assertRaises(Exception) as err:
                await app.call_tool("query_player_state", {"timeout_s": rejected})
            _assert_tool_error(self, err.exception)
            self.assertIn("bad_timeout", str(err.exception))

        accepted = _content_json(
            await app.call_tool("query_player_state", {"timeout_s": 299.0})
        )
        self.assertTrue(accepted["ok"])

    async def test_bridge_business_error_is_tool_error(self) -> None:
        # A bridge result with int ok=0 must surface as a ToolError,
        # not be returned as success (0 is not False, so `is False` missed it).
        app, runtime = self.build_started()

        def failing(command: dict[str, Any]) -> dict[str, Any]:
            return {"id": command["id"], "ok": 0, "error": "no_players", "cmd": command["cmd"]}

        peer = self.start_peer(runtime, "server")
        peer.responder = failing
        with self.assertRaises(Exception) as err:
            await app.call_tool("query_player_state", {"timeout_s": 1.0})
        _assert_tool_error(self, err.exception)
        self.assertIn("no_players", str(err.exception))

    async def test_timeout_includes_liveness(self) -> None:
        app, _runtime = self.build_started()
        with self.assertRaises(Exception) as err:
            await app.call_tool("query_player_state", {"timeout_s": 0.15})
        _assert_tool_error(self, err.exception)
        self.assertIn("server peer has never polled", str(err.exception))

    async def test_mutex_serializes_dayz_touching_tools(self) -> None:
        app, runtime = self.build_started()
        fake_client = self.start_peer(runtime, "client", result_delay_s=0.12)

        payload1 = {"cam_mode": "orient", "cam_pos": [1, 2, 3], "cam_orientation": [0, 0, 0], "timeout_s": 2.0}
        payload2 = {"cam_mode": "orient", "cam_pos": [4, 5, 6], "cam_orientation": [0, 0, 0], "timeout_s": 2.0}
        results = await asyncio.gather(app.call_tool("camera_set", payload1), app.call_tool("camera_set", payload2))
        parsed = [_content_json(result) for result in results]
        self.assertEqual([item["args"]["cam_pos"] for item in parsed], [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
        self.assertEqual(fake_client.max_batch_size, 1)

    async def test_version_state_legacy_and_legacy_blocked(self) -> None:
        app, runtime = self.build_started()
        self.start_peer(runtime, "server")
        ok = _content_json(await app.call_tool("query_player_state", {"timeout_s": 1.0}))
        self.assertTrue(ok["ok"])
        status = _content_json(await app.call_tool("bridge_status", {}))
        self.assertEqual(status["version_state"]["server"], "legacy")

        blocked_app, blocked_runtime = self.build_started(require_version=True)
        self.start_peer(blocked_runtime, "server")
        await self.wait_for(
            lambda: blocked_runtime.status()["server_peer"]["last_poll_age_s"] is not None
        )
        with self.assertRaises(Exception) as err:
            await blocked_app.call_tool("query_player_state", {"timeout_s": 1.0})
        _assert_tool_error(self, err.exception)
        self.assertIn("legacy_blocked", str(err.exception))
        status = _content_json(await blocked_app.call_tool("bridge_status", {}))
        self.assertEqual(status["version_state"]["server"], "legacy_blocked")

    async def test_never_polled_is_game_not_ready_before_enqueue(self) -> None:
        app, runtime = self.build_started(require_version=True)
        enqueue_calls: list[object] = []
        original = runtime.state.enqueue_command

        def wrapped(*args: object, **kwargs: object) -> object:
            enqueue_calls.append(1)
            return original(*args, **kwargs)

        runtime.state.enqueue_command = wrapped  # type: ignore[method-assign]
        with self.assertRaises(Exception) as err:
            await app.call_tool("query_player_state", {"timeout_s": 1.0})
        _assert_tool_error(self, err.exception)
        message = str(err.exception)
        self.assertIn("game_not_ready", message)
        self.assertNotIn("version_blocked:bridge", message)
        self.assertNotIn("poll did not include ver=", message)
        self.assertEqual(enqueue_calls, [])
        status = _content_json(await app.call_tool("bridge_status", {}))
        self.assertEqual(
            status["server_peer"]["version_state"], "never_polled_this_generation"
        )
        self.assertEqual(
            status["client_peer"]["version_state"], "never_polled_this_generation"
        )

    async def test_version_state_mismatch_and_ok(self) -> None:
        app, runtime = self.build_started(expected_game_version="1.29.0")
        self.start_peer(runtime, "server", version="wrong~1.29.0")
        await self.wait_for(lambda: runtime.status()["server_peer"]["version"] == "wrong~1.29.0")
        with self.assertRaises(Exception) as bridge_err:
            await app.call_tool("query_player_state", {"timeout_s": 1.0})
        _assert_tool_error(self, bridge_err.exception)
        self.assertIn("version_mismatch", str(bridge_err.exception))

        game_app, game_runtime = self.build_started(expected_game_version="1.29.0")
        self.start_peer(game_runtime, "server", version=f"{EXPECTED_BRIDGE_VERSION}~1.30.0")
        await self.wait_for(lambda: game_runtime.status()["server_peer"]["version"] == f"{EXPECTED_BRIDGE_VERSION}~1.30.0")
        with self.assertRaises(Exception) as game_err:
            await game_app.call_tool("query_player_state", {"timeout_s": 1.0})
        _assert_tool_error(self, game_err.exception)
        self.assertIn("version_mismatch", str(game_err.exception))

        ok_app, ok_runtime = self.build_started(expected_game_version="1.29.0")
        self.start_peer(ok_runtime, "server", version=f"{EXPECTED_BRIDGE_VERSION}~1.29.0")
        await self.wait_for(lambda: ok_runtime.status()["server_peer"]["version"] == f"{EXPECTED_BRIDGE_VERSION}~1.29.0")
        result = _content_json(await ok_app.call_tool("query_player_state", {"timeout_s": 1.0}))
        self.assertTrue(result["ok"])
        status = _content_json(await ok_app.call_tool("bridge_status", {}))
        self.assertEqual(status["version_state"]["server"], "ok")

    async def test_bridge_status_ready_true_with_fresh_versioned_peers(self) -> None:
        app, runtime = self.build_started()
        version = f"{EXPECTED_BRIDGE_VERSION}~1.29.0"
        self.start_peer(runtime, "server", version=version)
        self.start_peer(runtime, "client", version=version)
        await self.wait_for(
            lambda: (
                runtime.status()["server_peer"]["version_state"] == "ok"
                and runtime.status()["client_peer"]["version_state"] == "ok"
                and runtime.status()["server_peer"]["last_poll_age_s"] is not None
                and runtime.status()["client_peer"]["last_poll_age_s"] is not None
            )
        )
        status = _content_json(await app.call_tool("bridge_status", {}))
        ready = status["ready"]
        self.assertIs(ready["ready"], True)
        self.assertEqual(ready["reason"], "ready")
        self.assertIn(ready["reason"], server_module.READY_REASONS)

    async def test_bridge_status_ready_false_without_peers(self) -> None:
        app, _runtime = self.build_started()
        status = _content_json(await app.call_tool("bridge_status", {}))
        ready = status["ready"]
        self.assertIs(ready["ready"], False)
        self.assertIn(ready["reason"], server_module.READY_REASONS)

    async def test_bridge_status_publishes_frozen_tool_registry_overlay(self) -> None:
        app, _runtime = self.build_started()
        status = _content_json(await app.call_tool("bridge_status", {}))
        for key in (
            "tool_registry_fingerprint",
            "tool_registry_captured_at",
            "tool_registry_source_stale",
            "tool_registry_remediation",
            "tool_registry_schema_signal",
        ):
            self.assertIn(key, status)
        self.assertNotIsInstance(status["tool_registry_remediation"], str)
        if status["server_modules"]["status"] == "fresh":
            self.assertIsNone(status["tool_registry_remediation"])
        else:
            remediation = status["tool_registry_remediation"]
            self.assertIsInstance(remediation, dict)
            self.assertEqual(remediation["code"], "reopen_mcp_client")
            self.assertEqual(remediation["scope"], "tools")
        stale = status["tool_registry_source_stale"]
        self.assertIs(type(stale), bool)
        self.assertIs(stale, status["server_modules"]["status"] != "fresh")
        self.assertGreater(status["server_modules"]["watched_count"], 0)
        with patch.object(server_module, "capture_registry_snapshot") as capture:
            again = _content_json(await app.call_tool("bridge_status", {}))
        capture.assert_not_called()
        self.assertEqual(
            again["tool_registry_fingerprint"], status["tool_registry_fingerprint"]
        )
        self.assertEqual(
            again["tool_registry_captured_at"], status["tool_registry_captured_at"]
        )

    async def test_bridge_status_source_stale_is_boolean_for_all_three_states(self) -> None:
        app, _runtime = self.build_started()
        description = next(tool.description for tool in await app.list_tools() if tool.name == "bridge_status")
        self.assertIn("always a boolean: true for stale OR unknown", description)
        self.assertIn("false only for verified fresh", description)
        for state, expected in (("fresh", False), ("stale", True), ("unknown", True)):
            with self.subTest(state=state):
                snapshot = {
                    "server_started_at": 1.0, "server_pid": 123, "watched_count": 1,
                    "stale": ["fixture"] if state == "stale" else [],
                    "unreadable": ["fixture"] if state == "unknown" else [],
                    "unreadable_reasons": {"fixture": "source_unreadable_now"} if state == "unknown" else {},
                }
                with patch.object(server_module._SERVER_SOURCES, "snapshot", return_value=snapshot):
                    status = _content_json(await app.call_tool("bridge_status", {}))
                self.assertIs(status["tool_registry_source_stale"], expected)
                self.assertEqual(status["server_modules"]["status"], state)
                signal = {
                    "fresh": "fresh",
                    "stale": "stale_client",
                    "unknown": "unknown",
                }[state]
                self.assertEqual(status["tool_registry_schema_signal"], signal)

    async def test_loopback_status_omits_tool_registry_overlay(self) -> None:
        _app, runtime = self.build_started()
        assert runtime.loopback is not None and runtime.loopback.httpd is not None
        host, port = runtime.loopback.httpd.server_address
        url = f"http://{host}:{port}/status?key={self.key}"
        with urllib.request.urlopen(url, timeout=2.0) as response:
            raw = json.loads(response.read().decode("utf-8"))
        encoded = json.dumps(raw)
        for key in (
            "tool_registry_fingerprint",
            "tool_registry_captured_at",
            "tool_registry_source_stale",
            "tool_registry_remediation",
            "tool_registry_schema_signal",
            "daemon_source_remediation",
            "mutation_rejects_meta",
            "available_for",
        ):
            self.assertNotIn(key, raw)
            self.assertNotIn(key, encoded)

    async def test_shutdown_reuses_port(self) -> None:
        _app, runtime = self.build_started()
        self.assertIsNotNone(runtime.loopback)
        assert runtime.loopback is not None and runtime.loopback.httpd is not None
        port = int(runtime.loopback.httpd.server_address[1])
        await asyncio.to_thread(runtime.stop_loopback)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", port))

    async def test_dayz_test_value_errors_are_typed_for_run_and_stop(self) -> None:
        # dayz_test_stop shares _execute_request with dayz_test_run, so the parse
        # and the path accreditation raise it the SAME constant tokens; it used to
        # let them reach the wire as a bare "dayz_test_failed:ValueError" while run
        # translated them. Both tools go through _typed_dayz_test_value_errors now,
        # so this asserts the pair, not just the tool that happened to get the fix.
        from tests.test_client_mode import _fixture_client_runtime

        config = ServerConfig(
            mode="client",
            key=self.key,
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = build_app(config)

        for tool, args, target in (
            (
                "dayz_test_run",
                {"project": "ExampleMod", "mode": "server"},
                "execute_dayz_test_run",
            ),
            (
                "dayz_test_stop",
                {"run_id": "12345678-1234-4234-8234-1234567890ab"},
                "execute_dayz_test_stop",
            ),
        ):
            for token, code in server_module._DAYZ_TEST_VALUE_ERROR_CODES.items():

                async def raise_token(
                    *_args: object, _token: str = token, **_kwargs: object
                ) -> dict[str, Any]:
                    raise ValueError(_token)

                with patch.object(
                    server_module.dayz_test_tool, target, side_effect=raise_token
                ):
                    with self.assertRaises(Exception) as err:
                        await app.call_tool(tool, args)
                message = str(err.exception)
                _assert_tool_error(self, err.exception)
                self.assertIn(code, message, f"{tool}/{token}")
                self.assertNotIn("dayz_test_failed", message, f"{tool}/{token}")

            # Negative control: an unmapped ValueError keeps travelling as its
            # type alone, so the translation stays a whitelist and host paths stay off the wire.
            async def unmapped(*_args: object, **_kwargs: object) -> dict[str, Any]:
                raise ValueError(r"boom C:\Users\host\secret")

            with patch.object(
                server_module.dayz_test_tool, target, side_effect=unmapped
            ):
                with self.assertRaises(Exception) as untyped:
                    await app.call_tool(tool, args)
            untyped_message = str(untyped.exception)
            self.assertIn("dayz_test_failed:ValueError", untyped_message, tool)
            self.assertNotIn("secret", untyped_message, tool)

    def test_dayz_test_value_error_tokens_are_all_mapped(self) -> None:
        # Every constant ValueError token raised along the dayz_test request path
        # must translate to a caller-facing code. An unmapped token reaches the
        # wire as a bare "dayz_test_failed:ValueError" with nothing to act on.
        import importlib
        import inspect
        import re

        tokens: set[str] = set()
        for name in ("dayz_test_request", "request_path_authority"):
            source = inspect.getsource(importlib.import_module(f"dayz_mcp.{name}"))
            tokens |= set(
                re.findall(r'ValueError\(\s*"(invalid_dayz_test_[a-z_]+)"', source)
            )
        self.assertTrue(tokens, "no constant tokens found; the pattern went stale")
        mapping = server_module._DAYZ_TEST_VALUE_ERROR_CODES
        self.assertEqual(tokens - set(mapping), set(), "unmapped ValueError tokens")
        for token, code in mapping.items():
            # The code crosses the wire, so it must be a bare identifier
            # and never carry a host path.
            self.assertTrue(code.isidentifier(), token)

    def test_dayz_test_run_id_matrix_tokens_are_mapped(self) -> None:
        from dayz_mcp import dayz_test_request

        mapping = server_module._DAYZ_TEST_VALUE_ERROR_CODES
        self.assertEqual(mapping[dayz_test_request._INVALID_RUN_ID], "bad_run_id")
        for token in (
            dayz_test_request._INVALID_RUN_ID,
            dayz_test_request._CLIENT_REQUIRES_RUN_ID,
            dayz_test_request._SERVER_ALL_FORBID_RUN_ID,
        ):
            with self.subTest(token=token):
                self.assertIn(token, mapping)
                self.assertTrue(mapping[token].isidentifier(), token)

    async def test_dayz_test_run_names_run_id_matrix_causes_on_the_wire(self) -> None:
        from dayz_mcp import dayz_test_request, dayz_test_tool
        from tests.test_client_mode import _fixture_client_runtime
        from tests.test_dayz_test_tool import _Bundle, _Opened, _policy, _sealed

        config = ServerConfig(
            mode="client",
            key=self.key,
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = build_app(config)

        sealed = _sealed(_policy())
        bad_uuid = "not-a-uuid"
        rows = (
            (
                {"mode": "client", "run_id": bad_uuid},
                "bad_run_id",
            ),
            (
                {"mode": "client"},
                dayz_test_request._CLIENT_REQUIRES_RUN_ID,
            ),
            (
                {"mode": "server", "run_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
                dayz_test_request._SERVER_ALL_FORBID_RUN_ID,
            ),
            (
                {"mode": "all", "run_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
                dayz_test_request._SERVER_ALL_FORBID_RUN_ID,
            ),
        )
        for arguments, token in rows:
            for preflight in (False, True):
                args = {
                    "project": "ExampleMod",
                    "extra_mods": ["@DayZ_MCP"],
                    "preflight": preflight,
                    **arguments,
                }
                with self.subTest(args=args):
                    with patch.object(
                        dayz_test_tool, "_require_idle_session", new=AsyncMock()
                    ), patch.object(
                        dayz_test_tool,
                        "open_approved_launcher",
                        return_value=_Opened(),
                    ), patch.object(
                        dayz_test_tool.secure_launcher,
                        "load_verified_bundle",
                        return_value=_Bundle(sealed),
                    ):
                        with self.assertRaises(Exception) as err:
                            await app.call_tool("dayz_test_run", args)
                    _assert_tool_error(self, err.exception)
                    message = str(err.exception)
                    self.assertIn(token, message)
                    self.assertNotIn("dayz_test_failed", message)

    async def test_dayz_test_run_preflight_client_reattach_keeps_run_id(self) -> None:
        from dayz_mcp import dayz_test_tool
        from tests.test_client_mode import _fixture_client_runtime
        from tests.test_dayz_test_tool import _Bundle, _Opened, _policy, _sealed

        run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        config = ServerConfig(
            mode="client",
            key=self.key,
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = build_app(config)

        sealed = _sealed(_policy())

        async def launch(_raw: bytes, **kwargs: object) -> int:
            await kwargs["execution_started_cb"]()
            kwargs["output_sink"](
                "stdout",
                json.dumps(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": run_id,
                    },
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
            )
            return 0

        with patch.object(
            dayz_test_tool, "_require_idle_session", new=AsyncMock()
        ), patch.object(
            runtime, "lifecycle_status", new=AsyncMock(return_value={"runs": []})
        ), patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(sealed),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            raw = await app.call_tool(
                "dayz_test_run",
                {
                    "project": "ExampleMod",
                    "mode": "client",
                    "preflight": True,
                    "run_id": run_id,
                    "extra_mods": ["@DayZ_MCP"],
                },
            )
        payload = _content_json(raw)
        self.assertEqual(payload.get("status"), "succeeded")
        self.assertEqual(payload.get("run_id"), run_id)
        self.assertNotEqual(payload.get("error_code"), "terminal_invalid")

    async def test_dayz_test_run_description_documents_reattach_matrix(self) -> None:
        from tests.test_client_mode import _fixture_client_runtime

        config = ServerConfig(
            mode="client",
            key=self.key,
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = build_app(config)
        tools = {tool.name: tool for tool in await app.list_tools()}
        desc = (tools["dayz_test_run"].description or "").lower()
        self.assertIn("reattach", desc)
        self.assertIn("preflight", desc)
        self.assertIn("run_id", desc)
        self.assertIn("client", desc)


class UiPublicSurfaceTest(unittest.IsolatedAsyncioTestCase):
    """Public UI tools accept root; ui_click also accepts mode and bubble."""

    _UI_TOOLS = ("ui_tree", "ui_set_text", "ui_click", "ui_focus")

    def _app(self):
        return build_app(ServerConfig(key="k", port=0, log_sink=lambda _message: None))

    async def _call(self, tool: str, arguments: dict[str, Any]):
        app, runtime = self._app()
        seen: list[dict[str, Any]] = []

        async def recorder(cmd, bridge_args, role, timeout):
            seen.append(dict(bridge_args))
            return {"ok": 1}

        with patch.object(runtime, "call_bridge", side_effect=recorder):
            try:
                await app.call_tool(tool, arguments)
                return (seen[0] if seen else None), None
            except Exception as exc:
                return (seen[0] if seen else None), exc

    def _base(self, tool: str) -> dict[str, Any]:
        args: dict[str, Any] = {"path": "Btn"}
        if tool == "ui_set_text":
            args["text"] = "x"
        return args

    async def test_registered_schema_declares_root_on_all_four_ui_tools(self) -> None:
        app, _runtime = self._app()
        tools = {tool.name: tool for tool in await app.list_tools()}
        for name in self._UI_TOOLS:
            with self.subTest(name):
                props = (tools[name].inputSchema or {}).get("properties", {})
                self.assertIn("root", props, props)

    async def test_registered_schema_declares_mode_and_bubble_on_ui_click(self) -> None:
        app, _runtime = self._app()
        tools = {tool.name: tool for tool in await app.list_tools()}
        props = (tools["ui_click"].inputSchema or {}).get("properties", {})
        self.assertIn("mode", props, props)
        self.assertIn("bubble", props, props)

    async def test_root_reaches_the_bridge_when_the_caller_sends_it(self) -> None:
        for tool in self._UI_TOOLS:
            with self.subTest(tool):
                sent, err = await self._call(tool, {**self._base(tool), "root": "MiRoot"})
                self.assertIsNone(err, err)
                self.assertIsNotNone(sent)
                self.assertEqual(sent.get("root"), "MiRoot")

    async def test_root_does_not_travel_when_omitted(self) -> None:
        for tool in self._UI_TOOLS:
            with self.subTest(tool):
                sent, err = await self._call(tool, self._base(tool))
                self.assertIsNone(err, err)
                self.assertIsNotNone(sent)
                self.assertNotIn("root", sent)

    async def test_ui_click_always_sends_default_mode_and_bubble(self) -> None:
        sent, err = await self._call("ui_click", {"path": "Btn"})
        self.assertIsNone(err, err)
        self.assertIsNotNone(sent)
        self.assertEqual(sent.get("mode"), "direct")
        self.assertIs(sent.get("bubble"), False)

    async def test_ui_click_forwards_complete_mode_and_true_bubble(self) -> None:
        sent, err = await self._call(
            "ui_click", {"path": "Btn", "mode": "complete", "bubble": True}
        )
        self.assertIsNone(err, err)
        self.assertIsNotNone(sent)
        self.assertEqual(sent.get("mode"), "complete")
        self.assertIs(sent.get("bubble"), True)

    async def test_fail_closed_rejects_before_enqueue(self) -> None:
        cases = (
            ("ui_click", {"path": "Btn", "mode": "otro"}),
            ("ui_click", {"path": "Btn", "mode": 3}),
            ("ui_click", {"path": "Btn", "bubble": "true"}),
            ("ui_click", {"path": "Btn", "bubble": 1}),
            ("ui_click", {"path": "Btn", "root": ""}),
            ("ui_focus", {"path": "Btn", "root": 7}),
            ("ui_tree", {"root": ""}),
            ("ui_set_text", {"path": "Btn", "text": "x", "root": ""}),
        )
        for tool, arguments in cases:
            with self.subTest(tool=tool, arguments=arguments):
                sent, err = await self._call(tool, arguments)
                self.assertIsNone(sent)
                self.assertIsNotNone(err)
                _assert_tool_error(self, err)

    async def test_ui_tree_rejects_explicit_null_root_before_enqueue(self) -> None:
        sent, err = await self._call("ui_tree", {**self._base("ui_tree"), "root": None})
        self.assertIsNone(sent)
        self.assertIsNotNone(err)
        _assert_tool_error(self, err)

    async def test_ui_set_text_rejects_explicit_null_root_before_enqueue(self) -> None:
        sent, err = await self._call(
            "ui_set_text", {**self._base("ui_set_text"), "root": None}
        )
        self.assertIsNone(sent)
        self.assertIsNotNone(err)
        _assert_tool_error(self, err)

    async def test_ui_click_rejects_explicit_null_root_before_enqueue(self) -> None:
        sent, err = await self._call("ui_click", {**self._base("ui_click"), "root": None})
        self.assertIsNone(sent)
        self.assertIsNotNone(err)
        _assert_tool_error(self, err)

    async def test_ui_focus_rejects_explicit_null_root_before_enqueue(self) -> None:
        sent, err = await self._call("ui_focus", {**self._base("ui_focus"), "root": None})
        self.assertIsNone(sent)
        self.assertIsNotNone(err)
        _assert_tool_error(self, err)

    async def test_existing_path_and_button_guards_still_reject(self) -> None:
        cases = (
            ("ui_click", {"path": ""}),
            ("ui_click", {"path": "Btn", "button": 9}),
        )
        for tool, arguments in cases:
            with self.subTest(arguments=arguments):
                sent, err = await self._call(tool, arguments)
                self.assertIsNone(sent)
                self.assertIsNotNone(err)
                _assert_tool_error(self, err)


if __name__ == "__main__":
    unittest.main()



# --- M22: the announced census against the tools the app registers ----------
#
# The expected side is the fixture, a second hand-written copy of the map. It is
# never derived from app.list_tools(), from loopback's lists or from the PBO:
# the census exists precisely so it CAN disagree with the daemon, and an expected
# computed from either side would agree by construction and catch nothing.

_CENSUS_FIXTURE = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "bridge_capabilities_v1.json")
    .read_text(encoding="utf-8")
)


def _announced(commands: list[str]) -> dict[str, object]:
    return {"state": "announced", "reason": "ok", "announced_commands": commands}


class BridgeCapabilityComparisonTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        app, _runtime = build_app(
            ServerConfig(key="k", port=0, log_sink=lambda _message: None)
        )
        self.registered = frozenset(tool.name for tool in await app.list_tools())

    def _census(self, peer: str) -> list[str]:
        return sorted(_CENSUS_FIXTURE["peers"][peer])

    def test_the_shipped_map_and_the_fixture_still_agree(self) -> None:
        # Two copies on purpose. This is what turns "someone edited one side"
        # into a red instead of into silent agreement.
        for peer, expected in _CENSUS_FIXTURE["peers"].items():
            with self.subTest(peer):
                self.assertEqual(
                    server_module._BRIDGE_COMMAND_TOOLS[peer], expected
                )

    def test_a_complete_census_matches(self) -> None:
        for peer in ("server", "client"):
            with self.subTest(peer):
                result = server_module._compare_bridge_capabilities(
                    peer, _announced(self._census(peer)), self.registered
                )
                self.assertEqual(result["state"], "match")
                self.assertEqual(result["reason"], "ok")
                self.assertEqual(result["announced_without_registered_tool"], [])
                self.assertEqual(result["registered_without_announced_command"], [])
                self.assertEqual(result["unmapped_announced_commands"], [])

    def test_dropping_one_named_command_is_a_mismatch_that_says_which(self) -> None:
        for peer, commands in _CENSUS_FIXTURE["discriminating"].items():
            for dropped in commands:
                with self.subTest(f"{peer}:{dropped}"):
                    census = [c for c in self._census(peer) if c != dropped]
                    result = server_module._compare_bridge_capabilities(
                        peer, _announced(census), self.registered
                    )
                    self.assertEqual(result["state"], "mismatch")
                    self.assertEqual(
                        result["registered_without_announced_command"], [dropped]
                    )

    def test_a_census_announced_on_the_wrong_peer_is_a_mismatch(self) -> None:
        result = server_module._compare_bridge_capabilities(
            "client", _announced(self._census("server")), self.registered
        )
        self.assertEqual(result["state"], "mismatch")
        self.assertTrue(result["unmapped_announced_commands"])
        self.assertIn("entities_query", result["unmapped_announced_commands"])

    def test_a_command_the_map_does_not_know_is_reported_unmapped(self) -> None:
        # What a PBO that grew a verb looks like: visible on the first poll,
        # not on the first failed call.
        census = self._census("client") + ["brand_new_verb"]
        result = server_module._compare_bridge_capabilities(
            "client", _announced(census), self.registered
        )
        self.assertEqual(result["state"], "mismatch")
        self.assertEqual(result["unmapped_announced_commands"], ["brand_new_verb"])

    def test_no_usable_census_is_unknown_and_not_a_mismatch(self) -> None:
        # unknown is not a red: it says we did not look. Calling it mismatch
        # would put a fault on a bridge that may be perfectly fine.
        for label, block in (
            ("absent", {"state": "unknown", "reason": "absent", "announced_commands": []}),
            (
                "unaccredited",
                {"state": "unknown", "reason": "unaccredited", "announced_commands": []},
            ),
            ("malformed", {"state": "unknown", "reason": "malformed", "announced_commands": []}),
            ("not a mapping", "nope"),
            ("missing list", {"state": "announced", "reason": "ok"}),
        ):
            with self.subTest(label):
                result = server_module._compare_bridge_capabilities(
                    "client", block, self.registered
                )
                self.assertEqual(result["state"], "unknown")
                self.assertEqual(result["announced_commands"], [])

    def test_every_mapped_tool_is_actually_registered_by_the_app(self) -> None:
        # The seam in the other direction: the fixture claims a public tool for
        # each exposed command, and the app has to have it. A typo here would
        # otherwise show up as a permanent mismatch blamed on the bridge.
        for peer, mapping in _CENSUS_FIXTURE["peers"].items():
            for command, tool in mapping.items():
                if tool is None:
                    continue
                with self.subTest(f"{peer}:{command}"):
                    self.assertIn(tool, self.registered)


class ClientRenderSignalTest(unittest.TestCase):
    @staticmethod
    def _own_stamp() -> str:
        """This process's creation time in the record's own format.

        The fixtures below used to carry only pid and role, which is LESS than
        the producer publishes: _projected_run is dataclasses.asdict of a
        ProcessRecord and creation_time_utc is a required field. Once the
        sample started being checked against the registered identity (a
        recycled pid otherwise borrows a stranger's CPU), a fixture without it
        stopped being a shorter version of reality and became a different one.
        """
        from datetime import datetime, timezone

        sample = core.read_process_cpu_times(os.getpid())
        assert sample is not None, "este proceso tiene que ser muestreable"
        epoch_s = sample["created_100ns"] / 1e7 - 11644473600.0
        return datetime.fromtimestamp(epoch_s, tz=timezone.utc).isoformat()

    def _snapshot(self) -> dict[str, Any]:
        return {
            "peers": {
                "server": {
                    "last_poll_age_s": 0.1,
                    "queue_depth": 0,
                    "version": None,
                    "binding_state": "BOUND",
                    "instance_prefix": "ab",
                    "bound_last_poll_age_s": 0.1,
                },
                "client": {
                    "last_poll_age_s": 0.1,
                    "queue_depth": 0,
                    "version": None,
                    "binding_state": "BOUND",
                    "instance_prefix": "cd",
                    "bound_last_poll_age_s": 0.1,
                },
            },
            "results_pending": 0,
        }

    def test_the_client_process_publishes_a_cpu_signal(self) -> None:
        lifecycle = {
            "runs": [
                {
                    "processes": [
                        {
                            "pid": os.getpid(),
                            "role": "client",
                            "creation_time_utc": self._own_stamp(),
                        },
                    ]
                }
            ]
        }
        payload = core.build_status(
            self._snapshot(),
            require_version=False,
            expected_game_version=None,
        )
        enriched = core.attach_lifecycle_cpu_signals(lifecycle)
        payload["lifecycle"] = enriched
        process = enriched["runs"][0]["processes"][0]
        cpu = process.get("cpu")
        self.assertIsInstance(cpu, dict)
        self.assertIn("user_100ns", cpu)
        self.assertIn("kernel_100ns", cpu)
        self.assertIn("sampled_at_ns", cpu)
        self.assertIsInstance(cpu["user_100ns"], int)
        self.assertIsInstance(cpu["kernel_100ns"], int)
        self.assertIsInstance(cpu["sampled_at_ns"], int)
        self.assertGreaterEqual(int(cpu["user_100ns"]) + int(cpu["kernel_100ns"]), 0)
        self.assertNotIn("percent", cpu)
        self.assertNotIn("cpu_percent", cpu)
        self.assertNotIn("cpu", payload["client_peer"])
        self.assertNotIn("cpu_percent", payload["client_peer"])

    def test_a_peer_with_no_process_record_publishes_no_false_cpu_signal(self) -> None:
        payload = core.build_status(
            self._snapshot(),
            require_version=False,
            expected_game_version=None,
        )
        enriched = core.attach_lifecycle_cpu_signals({"runs": []})
        payload["lifecycle"] = enriched
        self.assertNotIn("cpu", payload["client_peer"])
        self.assertNotIn("cpu_percent", payload["client_peer"])
        self.assertIsNone(payload["client_peer"].get("cpu"))
        self.assertNotIn("cpu", enriched)
        self.assertNotIn("cpu_percent", enriched)
        self.assertEqual(enriched["runs"], [])

    def test_a_process_that_has_exited_publishes_no_cpu_signal(self) -> None:
        def _write_filetime(pointer: object, value_100ns: int) -> None:
            stamp = ctypes.cast(pointer, ctypes.POINTER(wintypes.FILETIME)).contents
            stamp.dwLowDateTime = value_100ns & 0xFFFFFFFF
            stamp.dwHighDateTime = (value_100ns >> 32) & 0xFFFFFFFF

        def fake_get_process_times(handle, created, exited, kernel, user):
            _write_filetime(created, 133000000000000136)
            _write_filetime(exited, 133000000000000185)
            _write_filetime(kernel, 156250)
            _write_filetime(user, 0)
            return True

        lifecycle = {
            "runs": [
                {
                    "processes": [
                        {"pid": os.getpid(), "role": "client"},
                    ]
                }
            ]
        }
        with patch.object(core._kernel32, "GetProcessTimes", side_effect=fake_get_process_times):
            enriched = core.attach_lifecycle_cpu_signals(lifecycle)
        process = enriched["runs"][0]["processes"][0]
        self.assertNotIn("cpu", process)

    def test_the_cpu_signal_carries_the_process_creation_time(self) -> None:
        lifecycle = {
            "runs": [
                {
                    "processes": [
                        {
                            "pid": os.getpid(),
                            "role": "client",
                            "creation_time_utc": self._own_stamp(),
                        },
                    ]
                }
            ]
        }
        enriched = core.attach_lifecycle_cpu_signals(lifecycle)
        cpu = enriched["runs"][0]["processes"][0].get("cpu")
        self.assertIsInstance(cpu, dict)
        self.assertIn("created_100ns", cpu)
        self.assertIsInstance(cpu["created_100ns"], int)
        self.assertGreater(int(cpu["created_100ns"]), 0)

    def test_a_process_that_cannot_be_sampled_publishes_no_cpu_signal(self) -> None:
        lifecycle = {
            "runs": [
                {
                    "processes": [
                        {"pid": -1, "role": "client"},
                    ]
                }
            ]
        }
        enriched = core.attach_lifecycle_cpu_signals(lifecycle)
        process = enriched["runs"][0]["processes"][0]
        self.assertNotIn("cpu", process)
        self.assertNotIn("cpu_percent", process)
