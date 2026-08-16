from __future__ import annotations

import ast
import hmac
import importlib
import json
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock
from pathlib import Path
from unittest.mock import patch


class ClientRuntimeControlCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_runtime_has_one_control_state_machine(self) -> None:
        server = importlib.import_module("dayz_mcp.server")
        source_path = Path(server.__file__ or "")
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        runtime = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "ClientRuntime"
        )
        constructor = next(
            node
            for node in runtime.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        constructor_calls = {
            node.func.id
            for node in ast.walk(constructor)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("load_normal_daemon_policy", constructor_calls)
        self.assertIn("ControlClient", constructor_calls)

        session_methods = {
            node.name: node
            for node in runtime.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name
            in {
                "session_acquire",
                "session_wait",
                "session_cancel",
                "session_acquire_wait",
                "session_heartbeat",
                "session_release",
                "session_status",
                "reconcile_idle_session",
            }
        }
        self.assertEqual(len(session_methods), 8)
        for name, method in session_methods.items():
            attributes = {
                node.attr
                for node in ast.walk(method)
                if isinstance(node, ast.Attribute)
            }
            self.assertIn("_control_with_lazy_spawn", attributes, name)
            self.assertIn(name, attributes, name)

        adapter = next(
            node
            for node in runtime.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_control_with_lazy_spawn"
        )
        self.assertNotIn(
            "method_name", {argument.arg for argument in adapter.args.args}
        )
        dynamic_calls = {
            node.func.id
            for node in ast.walk(adapter)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue(dynamic_calls.isdisjoint({"getattr", "eval"}))
        self.assertFalse(
            any(
                isinstance(node, ast.Constant) and isinstance(node.value, str)
                and node.value.startswith("session_")
                for node in ast.walk(adapter)
            )
        )

        class_source = ast.get_source_segment(
            source_path.read_text(encoding="utf-8"), runtime
        ) or ""
        for endpoint in (
            "/session/enqueue",
            "/session/wait",
            "/session/cancel-operation",
            "/session/heartbeat",
            "/session/release",
        ):
            self.assertNotIn(endpoint, class_source)
        self.assertNotIn("_session_state_lock", class_source)
        self.assertNotIn("_session_transition_lock", class_source)

    async def test_each_public_session_method_calls_exact_control_method(self) -> None:
        server = importlib.import_module("dayz_mcp.server")

        class FakeControl:
            def __init__(self) -> None:
                self.calls: list[tuple[str, tuple[object, ...]]] = []

            async def session_acquire(self, purpose: str) -> dict[str, object]:
                self.calls.append(("session_acquire", (purpose,)))
                return {"method": "session_acquire"}

            async def session_wait(
                self, ticket: str, timeout_s: float = 30.0
            ) -> dict[str, object]:
                self.calls.append(("session_wait", (ticket, timeout_s)))
                return {"method": "session_wait"}

            async def session_cancel(self, ticket: str) -> dict[str, object]:
                self.calls.append(("session_cancel", (ticket,)))
                return {"method": "session_cancel"}

            async def session_acquire_wait(
                self, purpose: str, max_wait_s=None, progress_cb=None
            ) -> dict[str, object]:
                self.calls.append(
                    ("session_acquire_wait", (purpose, max_wait_s, progress_cb))
                )
                return {"method": "session_acquire_wait"}

            async def session_heartbeat(self, lease_token: str) -> dict[str, object]:
                self.calls.append(("session_heartbeat", (lease_token,)))
                return {"method": "session_heartbeat"}

            async def session_release(self, lease_token: str) -> dict[str, object]:
                self.calls.append(("session_release", (lease_token,)))
                return {"method": "session_release"}

            async def session_status(self) -> dict[str, object]:
                self.calls.append(("session_status", ()))
                return {"method": "session_status"}

            async def reconcile_idle_session(self) -> dict[str, object]:
                self.calls.append(("reconcile_idle_session", ()))
                return {"method": "reconcile_idle_session"}

        runtime = object.__new__(server.ClientRuntime)
        runtime._control = FakeControl()
        progress = object()
        results = (
            await runtime.session_acquire("purpose"),
            await runtime.session_wait("ticket", 2.5),
            await runtime.session_cancel("ticket"),
            await runtime.session_acquire_wait("purpose", None, progress),
            await runtime.session_heartbeat("lease"),
            await runtime.session_release("lease"),
            await runtime.session_status(),
            await runtime.reconcile_idle_session(),
        )

        expected_names = (
            "session_acquire",
            "session_wait",
            "session_cancel",
            "session_acquire_wait",
            "session_heartbeat",
            "session_release",
            "session_status",
            "reconcile_idle_session",
        )
        self.assertEqual(tuple(result["method"] for result in results), expected_names)
        self.assertEqual(
            tuple(name for name, _arguments in runtime._control.calls), expected_names
        )

    async def test_lazy_spawn_requires_exact_pre_request_zero_byte_tuple(self) -> None:
        server = importlib.import_module("dayz_mcp.server")
        control_module = importlib.import_module("dayz_mcp.control_client")

        class FakeControl:
            def __init__(self, failures: list[BaseException]) -> None:
                self.failures = failures
                self.calls = 0

            async def session_status(self) -> dict[str, object]:
                self.calls += 1
                if self.failures:
                    raise self.failures.pop(0)
                return {"status": "ok"}

        retryable = control_module.ControlClientError(
            "daemon_unavailable",
            request_stage="pre_request",
            http_bytes_sent=0,
        )
        runtime = object.__new__(server.ClientRuntime)
        runtime._control = FakeControl([retryable])
        spawns: list[str] = []
        runtime._ensure_daemon = lambda *args: spawns.append("spawn") or True
        result = await runtime.session_status()
        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(spawns, ["spawn"])
        self.assertEqual(runtime._control.calls, 2)

        non_retryable = (
            control_module.ControlClientError(
                "daemon_unavailable",
                request_stage="pre_request",
                http_bytes_sent=1,
            ),
            control_module.ControlClientError(
                "daemon_response_ambiguous",
                request_stage="post_request",
                http_bytes_sent=1,
            ),
        )
        for failure in non_retryable:
            runtime = object.__new__(server.ClientRuntime)
            runtime._control = FakeControl([failure])
            spawns = []
            runtime._ensure_daemon = lambda *args: spawns.append("spawn") or True
            with self.subTest(code=failure.code, stage=failure.request_stage):
                with self.assertRaises(server.ToolError):
                    await runtime.session_status()
                self.assertEqual(spawns, [])
                self.assertEqual(runtime._control.calls, 1)

        wrong_code = control_module.ControlClientError(
            "different_code", request_stage="pre_request", http_bytes_sent=0
        )
        runtime = object.__new__(server.ClientRuntime)
        runtime._control = FakeControl([wrong_code])
        spawns = []
        runtime._ensure_daemon = lambda *args: spawns.append("spawn") or True
        with self.assertRaises(server.ToolError):
            await runtime.session_status()
        self.assertEqual(spawns, [])
        self.assertEqual(runtime._control.calls, 1)

        first = control_module.ControlClientError(
            "daemon_unavailable", request_stage="pre_request", http_bytes_sent=0
        )
        second = control_module.ControlClientError(
            "daemon_unavailable", request_stage="pre_request", http_bytes_sent=0
        )
        runtime = object.__new__(server.ClientRuntime)
        runtime._control = FakeControl([first, second])
        spawns = []
        runtime._ensure_daemon = lambda *args: spawns.append("spawn") or True
        with self.assertRaises(server.ToolError):
            await runtime.session_status()
        self.assertEqual(spawns, ["spawn"])
        self.assertEqual(runtime._control.calls, 2)

    async def test_credential_and_identity_errors_are_public_without_spawn(
        self,
    ) -> None:
        server = importlib.import_module("dayz_mcp.server")
        control_module = importlib.import_module("dayz_mcp.control_client")

        class FakeControl:
            def __init__(self, failure: BaseException) -> None:
                self.failure = failure

            async def session_status(self) -> dict[str, object]:
                raise self.failure

        cases = (
            (
                "stale_client_credential_retry_rejected",
                "post_request",
                1,
            ),
            (
                "daemon_credential_desynchronized",
                "post_request",
                1,
            ),
            (
                "stale_client_credential_refresh_failed",
                "post_request",
                1,
            ),
            (
                "stale_client_credential_retry_transport_failed",
                "post_request",
                1,
            ),
            ("credential_source_untrusted", "pre_request", 0),
            ("daemon_identity_unverified", "pre_request", 0),
            (
                "client_policy_untrusted_open_new_session",
                "pre_request",
                0,
            ),
            (
                "daemon_reaccreditation_failed_open_new_session",
                "pre_request",
                0,
            ),
        )
        for code, stage, sent in cases:
            runtime = object.__new__(server.ClientRuntime)
            runtime._control = FakeControl(
                control_module.ControlClientError(
                    code,
                    request_stage=stage,
                    http_bytes_sent=sent,
                )
            )
            spawns: list[str] = []
            runtime._ensure_daemon = lambda *args: spawns.append("spawn") or True
            with self.subTest(code=code):
                with self.assertRaises(server.ToolError) as raised:
                    await runtime.session_status()
                self.assertEqual(str(raised.exception), code)
                self.assertEqual(spawns, [])

    async def test_bridge_replay_requires_accredited_pre_request_zero_bytes(self) -> None:
        server = importlib.import_module("dayz_mcp.server")
        transport = importlib.import_module(
            "dayz_mcp.accredited_daemon_transport"
        )

        safe = transport.AccreditedTransportError(
            "daemon_transport_failure",
            request_stage="pre_request",
            http_bytes_sent=0,
        )
        runtime = object.__new__(server.ClientRuntime)
        requests: list[str] = []
        responses = iter((safe, (200, {"ok": True})))

        def request(*_args, **_kwargs):
            requests.append("request")
            result = next(responses)
            if isinstance(result, BaseException):
                raise result
            return result

        spawns: list[str] = []
        runtime._request_once = request
        runtime._ensure_daemon = lambda *args: spawns.append("spawn") or True
        self.assertEqual(runtime._call("GET", "/status"), (200, {"ok": True}))
        self.assertEqual(requests, ["request", "request"])
        self.assertEqual(spawns, ["spawn"])

        retry_failures = (
            (
                transport.AccreditedTransportError(
                    "daemon_identity_unverified",
                    request_stage="pre_request",
                    http_bytes_sent=0,
                ),
                "daemon_identity_unverified",
            ),
            (
                server.daemon_credential.CredentialRefreshError(
                    "stale_client_credential_retry_rejected"
                ),
                "stale_client_credential_retry_rejected",
            ),
        )
        for retry_failure, expected_code in retry_failures:
            with self.subTest(retry_failure=expected_code):
                runtime = object.__new__(server.ClientRuntime)
                requests = []
                responses = iter((safe, retry_failure))

                def fail_after_spawn(*_args, **_kwargs):
                    requests.append("request")
                    result = next(responses)
                    if isinstance(result, BaseException):
                        raise result
                    return result

                spawns = []
                runtime._request_once = fail_after_spawn
                runtime._ensure_daemon = (
                    lambda *args: spawns.append("spawn") or True
                )
                with self.assertRaises(server.ToolError) as raised:
                    runtime._call("GET", "/status")
                self.assertEqual(str(raised.exception), expected_code)
                self.assertEqual(requests, ["request", "request"])
                self.assertEqual(spawns, ["spawn"])

        unsafe = (
            transport.AccreditedTransportError(
                "daemon_transport_failure",
                request_stage="post_request",
                http_bytes_sent=0,
            ),
            transport.AccreditedTransportError(
                "daemon_transport_failure",
                request_stage="pre_request",
                http_bytes_sent=1,
            ),
            ConnectionError("secret fixture text"),
        )
        for failure in unsafe:
            with self.subTest(failure=type(failure).__name__):
                runtime = object.__new__(server.ClientRuntime)
                requests = []

                def fail(*_args, **_kwargs):
                    requests.append("request")
                    raise failure

                spawns = []
                runtime._request_once = fail
                runtime._ensure_daemon = lambda *args: spawns.append("spawn") or True
                with self.assertRaises(server.ToolError) as raised:
                    runtime._call("GET", "/status")
                self.assertNotIn("secret fixture text", str(raised.exception))
                self.assertEqual(requests, ["request"])
                self.assertEqual(spawns, [])

    async def test_safe_retry_uses_fresh_operation_without_local_conflict(self) -> None:
        server = importlib.import_module("dayz_mcp.server")
        control_module = importlib.import_module("dayz_mcp.control_client")
        from tests.test_control_client import _policy

        identity = control_module.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="",
        )
        safe = control_module.ControlClientError(
            "daemon_unavailable",
            request_stage="pre_request",
            http_bytes_sent=0,
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            for public_method, side_effect, arguments in (
                (
                    "session_acquire",
                    (safe, {"status": "active", "lease_token": "lease-one"}),
                    ("purpose",),
                ),
                (
                    "session_acquire_wait",
                    (
                        safe,
                        {
                            "status": "queued",
                            "ticket": "ticket-one",
                            "operation_id": "operation-two",
                        },
                        {
                            "status": "active",
                            "lease_token": "lease-one",
                            "ticket": "ticket-one",
                            "operation_id": "operation-two",
                        },
                    ),
                    ("purpose", None),
                ),
            ):
                client = control_module.ControlClient(
                    policy=_policy(keyfile), identity=identity
                )
                session_call = AsyncMock(side_effect=side_effect)
                runtime = object.__new__(server.ClientRuntime)
                runtime._control = client
                spawns: list[str] = []
                runtime._ensure_daemon = lambda *args: spawns.append("spawn") or True
                with self.subTest(method=public_method), patch.object(
                    client, "_session_call", session_call
                ), patch.object(
                    control_module.uuid,
                    "uuid4",
                    side_effect=("operation-one", "operation-two"),
                ):
                    try:
                        response = await getattr(runtime, public_method)(*arguments)
                    except BaseException as error:
                        response = error

                mutation_calls = [
                    call
                    for call in session_call.await_args_list
                    if call.args[0] in {"/session/acquire", "/session/enqueue"}
                ]
                self.assertIsInstance(response, dict)
                if not isinstance(response, dict):
                    continue
                self.assertEqual(response["status"], "active")
                self.assertEqual(spawns, ["spawn"])
                self.assertEqual(len(mutation_calls), 2)
                self.assertNotEqual(
                    mutation_calls[0].args[1]["operation_id"],
                    mutation_calls[1].args[1]["operation_id"],
                )

    async def test_bridge_and_control_share_one_refreshing_credential(
        self,
    ) -> None:
        server = importlib.import_module("dayz_mcp.server")
        host_config = importlib.import_module("dayz_mcp.host_config")
        first_key = "fixture-credential-a"
        second_key = "fixture-credential-b"
        calls: list[dict[str, object]] = []

        def request(**kwargs: object) -> tuple[int, bytes]:
            calls.append(dict(kwargs))
            if hmac.compare_digest(str(kwargs["key"]), first_key):
                return 401, b'{"error":"unauthorized"}'
            return 200, b'{"status":"ok"}'

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            keyfile = root / "daemon.key"
            keyfile.write_text(first_key + "\n", encoding="utf-8")
            launcher = str(Path(sys.executable).resolve())
            config = server.ServerConfig(
                mode="client",
                port=18765,
                keyfile=str(keyfile.resolve()),
                auto_spawn_daemon=False,
                log_sink=lambda _message: None,
            )
            provenance = host_config.DaemonProvenance(
                launch_executable=launcher,
                native_executable=launcher,
                argv=tuple(
                    server.daemon.build_daemon_argv(
                        config,
                        python=launcher,
                    )
                ),
                cwd=server.daemon.daemon_runtime_cwd(),
                port=config.port,
                keyfile=str(keyfile.resolve()),
                auto_spawn_daemon=False,
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
                    return_value=launcher,
                ),
                patch.object(
                    server.orphan_guard,
                    "verified_daemon_http_request",
                    side_effect=request,
                ),
            ):
                runtime = server.ClientRuntime(config)
                provider = getattr(runtime, "_credential_provider", None)
                self.assertIsNotNone(
                    provider,
                    "ClientRuntime has no shared credential provider",
                )
                self.assertIs(
                    provider,
                    runtime._control._credential_provider,
                )
                keyfile.write_text(second_key + "\n", encoding="utf-8")

                bridge_response = runtime._call("GET", "/status")
                control_response = await runtime.session_status()

        self.assertEqual(bridge_response, (200, {"status": "ok"}))
        self.assertEqual(control_response, {"status": "ok"})
        self.assertEqual(len(calls), 3)
        self.assertTrue(hmac.compare_digest(str(calls[0]["key"]), first_key))
        self.assertTrue(hmac.compare_digest(str(calls[1]["key"]), second_key))
        self.assertTrue(hmac.compare_digest(str(calls[2]["key"]), second_key))
        self.assertEqual(calls[0]["path"], "/status")
        self.assertEqual(calls[1]["path"], "/status")
        self.assertEqual(calls[2]["path"], "/session/status")

    async def test_health_401_refreshes_instead_of_reporting_absent(
        self,
    ) -> None:
        server = importlib.import_module("dayz_mcp.server")
        host_config = importlib.import_module("dayz_mcp.host_config")
        first_key = "fixture-credential-a"
        second_key = "fixture-credential-b"
        calls: list[dict[str, object]] = []
        generation = "generation-after-rotation"
        healthy_body = json.dumps(
            {
                "daemon_status": {
                    "schema": server.orphan_guard.DAEMON_STATUS_SCHEMA,
                    "product": "dayz_mcp",
                    "mode": "daemon",
                    "daemon_generation": generation,
                    "coordination_revision": 0,
                },
                "daemon_generation": generation,
                "coordination": {"revision": 0},
            },
            separators=(",", ":"),
        ).encode("utf-8")

        def request(**kwargs: object) -> tuple[int, bytes]:
            calls.append(dict(kwargs))
            if hmac.compare_digest(str(kwargs["key"]), first_key):
                return 401, b'{"error":"unauthorized"}'
            return 200, healthy_body

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            keyfile = root / "daemon.key"
            keyfile.write_text(first_key + "\n", encoding="utf-8")
            launcher = str(Path(sys.executable).resolve())
            config = server.ServerConfig(
                mode="client",
                port=18765,
                keyfile=str(keyfile.resolve()),
                auto_spawn_daemon=False,
                log_sink=lambda _message: None,
            )
            provenance = host_config.DaemonProvenance(
                launch_executable=launcher,
                native_executable=launcher,
                argv=tuple(
                    server.daemon.build_daemon_argv(
                        config,
                        python=launcher,
                    )
                ),
                cwd=server.daemon.daemon_runtime_cwd(),
                port=config.port,
                keyfile=str(keyfile.resolve()),
                auto_spawn_daemon=False,
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
                    return_value=launcher,
                ),
                patch.object(
                    server.orphan_guard,
                    "verified_daemon_http_request",
                    side_effect=request,
                ),
            ):
                runtime = server.ClientRuntime(config)
                keyfile.write_text(second_key + "\n", encoding="utf-8")
                healthy = runtime._daemon_healthy(1234.5)

        self.assertTrue(healthy)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["deadline"], 1234.5)
        self.assertEqual(calls[1]["deadline"], 1234.5)
        self.assertEqual(
            calls[1]["headers"],
            {"X-DayZ-MCP-Credential-Retry": "1"},
        )

    async def test_second_consensus_read_cannot_drift_from_control_policy(self) -> None:
        server = importlib.import_module("dayz_mcp.server")
        host_config = importlib.import_module("dayz_mcp.host_config")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            keyfile = root / "daemon.key"
            keyfile.write_text("fixture-key\n", encoding="utf-8")
            native = root / "native-python.exe"
            native.write_bytes(b"fixture")
            launcher = str(Path(sys.executable).resolve())
            base_argv = (
                launcher,
                "-m",
                "dayz_mcp",
                "--daemon",
                "--port",
                "18765",
                "--keyfile",
                str(keyfile.resolve()),
                "--idle-timeout",
                "1800.0",
            )
            original = host_config.DaemonProvenance(
                launch_executable=launcher,
                native_executable=str(native.resolve()),
                argv=base_argv,
                cwd=str(root.resolve()),
                port=18765,
                keyfile=str(keyfile.resolve()),
                auto_spawn_daemon=True,
            )
            drifted = host_config.DaemonProvenance(
                launch_executable=launcher,
                native_executable=str(native.resolve()),
                argv=base_argv[:-1] + ("900.0",),
                cwd=str(root.resolve()),
                port=18765,
                keyfile=str(keyfile.resolve()),
                auto_spawn_daemon=True,
            )
            config = server.ServerConfig(
                mode="client",
                port=18765,
                keyfile=str(keyfile.resolve()),
                auto_spawn_daemon=True,
                log_sink=lambda _message: None,
            )
            with (
                patch.object(
                    host_config,
                    "resolve_daemon_provenance",
                    side_effect=(original, drifted, drifted),
                ),
                patch.object(
                    host_config, "_local_launch_executable", return_value=launcher
                ),
                patch.object(
                    host_config,
                    "_local_native_executable",
                    return_value=str(native.resolve()),
                ),
            ):
                with self.assertRaisesRegex(
                    host_config.HostConfigError, "daemon_provenance_conflict"
                ):
                    server.ClientRuntime(config)


if __name__ == "__main__":
    unittest.main()
