from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import daemon, host_config, loopback, server
from dayz_mcp.accredited_daemon_transport import AccreditedTransportError


_TOOLS_DIR = Path(__file__).resolve().parents[1]


class _FakeClock:
    def __init__(self) -> None:
        self.value = 100.0
        self.sleeps: list[float] = []
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self.value

    def sleep(self, duration: float) -> None:
        with self._lock:
            self.sleeps.append(duration)
            self.value += duration


def _unreachable_client_runtime(clock: _FakeClock) -> server.ClientRuntime:
    runtime = object.__new__(server.ClientRuntime)
    runtime.config = server.ServerConfig(mode="client", key="fixture-key")
    runtime._control = SimpleNamespace(
        active_lease_token=None,
        active_ticket=None,
    )
    runtime.identity = SimpleNamespace(
        to_payload=lambda: {
            "platform": "codex",
            "pid": 1,
            "ppid": 0,
            "started_at_utc": "2026-07-28T00:00:00Z",
            "session_id": "bug037-fixture",
            "task_label": "",
        }
    )
    runtime._time_fn = clock.now
    runtime._sleep_fn = clock.sleep
    runtime._startup_budget_s = 10.0
    runtime._auto_spawn_daemon = True
    runtime._spawn_lock = threading.Lock()
    runtime._probe = lambda *_args, **_kwargs: False
    runtime._probe_key = "fixture-key"
    runtime._spawn_fn = lambda: 4321
    runtime._daemon_executable = str(Path(sys.executable).resolve())
    runtime._daemon_argv = (runtime._daemon_executable, "-m", "dayz_mcp", "--daemon")
    runtime._daemon_cwd = str(_TOOLS_DIR)
    runtime.port = 65530
    runtime._log = lambda _message: None

    def unavailable(*_args: object, **_kwargs: object):
        raise AccreditedTransportError(
            "daemon_transport_failure",
            request_stage="pre_request",
            http_bytes_sent=0,
        )

    runtime._request_once = unavailable
    return runtime


def _pre_request_zero_bytes() -> AccreditedTransportError:
    return AccreditedTransportError(
        "daemon_transport_failure",
        request_stage="pre_request",
        http_bytes_sent=0,
    )


def _connection_aborted() -> ConnectionAbortedError:
    return ConnectionAbortedError(
        10053, "An established connection was aborted"
    )


def _scripted_await_runtime(
    clock: _FakeClock,
    await_errors: list[BaseException],
    *,
    ensure_daemon: bool | None = None,
) -> server.ClientRuntime:
    runtime = _unreachable_client_runtime(clock)
    pending = list(await_errors)

    def request_once(
        method: str,
        path: str,
        payload: object = None,
        query: object = None,
        timeout: float = 5.0,
    ) -> tuple[int, dict[str, object]]:
        if path == "/enqueue":
            return 200, {"id": 7}
        if path == "/await":
            if not pending:
                raise AssertionError("unexpected extra /await hop")
            raise pending.pop(0)
        return 200, {}

    runtime._request_once = request_once
    if ensure_daemon is not None:
        runtime._ensure_daemon = lambda *_args, **_kwargs: ensure_daemon
    return runtime


class PythonBacklogFixesTest(unittest.IsolatedAsyncioTestCase):
    async def test_bug037_tool_timeout_caps_daemon_startup_poll(self) -> None:
        for tool_timeout in (0.4, 2.0):
            with self.subTest(tool_timeout=tool_timeout):
                clock = _FakeClock()
                runtime = _unreachable_client_runtime(clock)
                started_at = clock.now()

                with self.assertRaisesRegex(ToolError, "daemon_unavailable"):
                    await runtime.call_bridge(
                        "query_player_state",
                        {},
                        "server",
                        tool_timeout,
                    )

                self.assertLessEqual(
                    clock.now() - started_at,
                    tool_timeout + 1e-9,
                    "the daemon startup poll consumed time beyond the tool deadline",
                )

    async def test_inflight_await_connection_abort_is_timeout_not_daemon_unavailable(
        self,
    ) -> None:
        # Names _CallBudgetExpired: an /await hop that starts with remaining>0
        # and dies in-flight (ConnectionAbortedError) must not become
        # daemon_unavailable. The sentinel is what _await_result turns into
        # "timeout waiting for".
        self.assertEqual(server._CallBudgetExpired.__name__, "_CallBudgetExpired")
        clock = _FakeClock()
        runtime = _unreachable_client_runtime(clock)

        def request_once(
            method: str,
            path: str,
            payload: object = None,
            query: object = None,
            timeout: float = 5.0,
        ) -> tuple[int, dict[str, object]]:
            if path == "/enqueue":
                return 200, {"id": 7}
            if path == "/await":
                raise ConnectionAbortedError(
                    10053, "An established connection was aborted"
                )
            return 200, {}

        runtime._request_once = request_once

        with self.assertRaises(server._CallBudgetExpired):
            runtime._call(
                "GET",
                "/await",
                None,
                {"id": "7", "remove": "1"},
                0.4,
                clock.now() + 0.4,
            )

        with self.assertRaises(ToolError) as err:
            await runtime.call_bridge(
                "query_player_state",
                {},
                "server",
                0.4,
            )
        message = str(err.exception)
        self.assertIn("timeout waiting for", message)
        self.assertNotIn("daemon_unavailable", message)

    async def test_inflight_await_ensure_daemon_false_is_timeout_not_daemon_unavailable(
        self,
    ) -> None:
        # First /await is retryable AccreditedTransportError; _ensure_daemon
        # False must raise _CallBudgetExpired (:1762-1763), not daemon_unavailable.
        clock = _FakeClock()
        runtime = _scripted_await_runtime(
            clock, [_pre_request_zero_bytes()], ensure_daemon=False
        )
        with self.assertRaises(server._CallBudgetExpired):
            runtime._call(
                "GET",
                "/await",
                None,
                {"id": "7", "remove": "1"},
                0.4,
                clock.now() + 0.4,
            )
        runtime = _scripted_await_runtime(
            _FakeClock(), [_pre_request_zero_bytes()], ensure_daemon=False
        )
        with self.assertRaises(ToolError) as err:
            await runtime.call_bridge("query_player_state", {}, "server", 0.4)
        message = str(err.exception)
        self.assertIn("timeout waiting for", message)
        self.assertNotIn("daemon_unavailable", message)

    async def test_inflight_await_retry_safe_is_timeout_not_daemon_unavailable(
        self,
    ) -> None:
        # Retryable miss, spawn succeeds, second hop is again retryable
        # pre_request/0 bytes. retry_safe /await must raise _CallBudgetExpired
        # (:1780-1781), not daemon_unavailable.
        clock = _FakeClock()
        runtime = _scripted_await_runtime(
            clock,
            [_pre_request_zero_bytes(), _pre_request_zero_bytes()],
            ensure_daemon=True,
        )
        with self.assertRaises(server._CallBudgetExpired):
            runtime._call(
                "GET",
                "/await",
                None,
                {"id": "7", "remove": "1"},
                0.4,
                clock.now() + 0.4,
            )
        runtime = _scripted_await_runtime(
            _FakeClock(),
            [_pre_request_zero_bytes(), _pre_request_zero_bytes()],
            ensure_daemon=True,
        )
        with self.assertRaises(ToolError) as err:
            await runtime.call_bridge("query_player_state", {}, "server", 0.4)
        message = str(err.exception)
        self.assertIn("timeout waiting for", message)
        self.assertNotIn("daemon_unavailable", message)

    async def test_inflight_await_inner_oserror_is_timeout_not_daemon_unavailable(
        self,
    ) -> None:
        # Retryable miss, spawn succeeds, second hop ConnectionAbortedError.
        # Inner except (ConnectionError, OSError) /await must raise
        # _CallBudgetExpired (:1784-1785). A first-hop abort only hits outer.
        clock = _FakeClock()
        runtime = _scripted_await_runtime(
            clock,
            [_pre_request_zero_bytes(), _connection_aborted()],
            ensure_daemon=True,
        )
        with self.assertRaises(server._CallBudgetExpired):
            runtime._call(
                "GET",
                "/await",
                None,
                {"id": "7", "remove": "1"},
                0.4,
                clock.now() + 0.4,
            )
        runtime = _scripted_await_runtime(
            _FakeClock(),
            [_pre_request_zero_bytes(), _connection_aborted()],
            ensure_daemon=True,
        )
        with self.assertRaises(ToolError) as err:
            await runtime.call_bridge("query_player_state", {}, "server", 0.4)
        message = str(err.exception)
        self.assertIn("timeout waiting for", message)
        self.assertNotIn("daemon_unavailable", message)

    async def test_bug024_timeout_reaps_state_and_never_delivers_zombie(self) -> None:
        state = loopback.ServerState("fixture-key")
        runtime = server.Runtime(
            server.ServerConfig(key="fixture-key", log_sink=lambda _message: None)
        )
        runtime.loopback = SimpleNamespace(state=state)

        with self.assertRaisesRegex(ToolError, "timeout waiting"):
            await runtime.call_bridge(
                "query_player_state",
                {},
                "server",
                0.01,
            )

        command_id = 1
        _status, reconnect_poll = state.record_poll("server")
        late_status, late_payload = state.store_result(
            {"id": command_id, "ok": 1}
        )
        snapshot = state.status_snapshot()
        self.assertEqual(late_status, 200)
        self.assertEqual(
            {
                "delivered_ids": [
                    command["id"] for command in reconnect_poll["commands"]
                ],
                "results_pending": snapshot["results_pending"],
                "enqueued_ids": sorted(state._enqueued_at),
                "late_result_discarded": late_payload.get("discarded", False),
            },
            {
                "delivered_ids": [],
                "results_pending": 0,
                "enqueued_ids": [],
                "late_result_discarded": True,
            },
        )

        queued_clock = _FakeClock()
        queued_state = loopback.ServerState(
            "fixture-key", time_fn=queued_clock.now
        )
        _status, queued = queued_state.enqueue_command(
            "camera_get",
            {},
            peer="client",
            operation_timeout_s=0.4,
        )
        queued_clock.sleep(0.5)
        _status, first_reconnect = queued_state.record_poll("client")
        self.assertEqual(first_reconnect["commands"], [])
        self.assertNotIn(queued["id"], queued_state._enqueued_at)
        self.assertEqual(queued_state.status_snapshot()["results_pending"], 0)

        inflight_clock = _FakeClock()
        inflight_state = loopback.ServerState(
            "fixture-key", time_fn=inflight_clock.now
        )
        _status, inflight = inflight_state.enqueue_command(
            "camera_get",
            {},
            peer="client",
            operation_timeout_s=0.4,
        )
        _status, delivered = inflight_state.record_poll("client")
        self.assertEqual([item["id"] for item in delivered["commands"]], [inflight["id"]])
        inflight_clock.sleep(0.5)
        late_status, late_payload = inflight_state.store_result(
            {"id": inflight["id"], "ok": 1}
        )
        self.assertEqual(late_status, 200)
        self.assertTrue(late_payload.get("discarded", False))
        self.assertEqual(inflight_state.status_snapshot()["results_pending"], 0)
        self.assertNotIn(inflight["id"], inflight_state._enqueued_at)

    async def test_bug025_exec_audit_write_does_not_block_event_loop(self) -> None:
        release_audit = threading.Event()
        audit_started = threading.Event()

        def blocking_audit(
            _expr: str,
            _verdict: str,
            _main_fn: str,
            _command_id: int | None,
        ) -> None:
            audit_started.set()
            release_audit.wait(0.75)

        state = loopback.ServerState(
            "fixture-key",
            enable_exec_enforce=True,
            exec_allowlist={"Probe()"},
            exec_audit=blocking_audit,
        )
        from tests.fence_helpers import bind_both_peers

        bind_both_peers(state)
        runtime = server.Runtime(
            server.ServerConfig(
                key="fixture-key",
                enable_exec_enforce=True,
                log_sink=lambda _message: None,
            )
        )
        runtime.loopback = SimpleNamespace(state=state)

        async def immediate_result(
            _cmd: str,
            _command_id: int,
            _peer: str,
            _timeout_s: float,
        ) -> dict[str, int]:
            return {"ok": 1}

        runtime.wait_for_result = immediate_result

        async def heartbeat() -> None:
            await asyncio.sleep(0.02)
            release_audit.set()

        started_at = time.monotonic()
        await asyncio.gather(
            runtime.call_exec_enforce(
                {"expr": "Probe()", "main_fn": "Main"},
                1.0,
            ),
            heartbeat(),
        )
        elapsed = time.monotonic() - started_at

        self.assertTrue(audit_started.is_set())
        self.assertLess(
            elapsed,
            0.30,
            "synchronous audit I/O blocked the event loop heartbeat",
        )

    def test_bug026_e4_bind_failure_exits_cleanly_with_code_2(self) -> None:
        script = "\n".join(
            (
                "from unittest.mock import patch",
                "from dayz_mcp import server",
                "with patch.object(server.Runtime, 'start_loopback', "
                "side_effect=OSError('fixture E4 bind failure')):",
                "    raise SystemExit(server.run(["
                "'--embedded', '--keyfile', 'unused', '--port', '8765']))",
            )
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=_TOOLS_DIR,
            capture_output=True,
            text=True,
            timeout=10.0,
            env=dict(os.environ),
        )

        self.assertEqual(
            completed.returncode,
            2,
            f"stderr was:\n{completed.stderr}",
        )
        self.assertNotIn("Traceback", completed.stderr)
        self.assertNotIn("BaseExceptionGroup", completed.stderr)

    def test_bug039_exec_audit_path_makes_install_and_daemon_argv_equal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            keyfile = (root / "daemon.key").resolve()
            keyfile.write_text("fixture-key", encoding="utf-8")
            audit_path = str((root / "_audit" / "custom-exec.jsonl").resolve())
            executable = str(Path(sys.executable).resolve())

            def install_args(platform: str) -> list[str]:
                return [
                    "-m",
                    "dayz_mcp",
                    "--client",
                    "--keyfile",
                    str(keyfile),
                    "--port",
                    "18765",
                    "--require-version",
                    "--idle-timeout",
                    "12.5",
                    "--exec-audit-path",
                    audit_path,
                    "--client-platform",
                    platform,
                ]

            claude_path = root / ".claude.json"
            claude_path.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "dayz-mcp": {
                                "type": "stdio",
                                "command": executable,
                                "args": install_args("claude"),
                                "timeout": host_config.CLAUDE_TIMEOUT_MS,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            codex_path = root / "config.toml"
            codex_path.write_text(
                "\n".join(
                    (
                        "[mcp_servers.dayz-mcp]",
                        f"command = {json.dumps(executable)}",
                        f"args = {json.dumps(install_args('codex'))}",
                        f"tool_timeout_sec = {host_config.CODEX_TIMEOUT_SECONDS}",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            stderr = StringIO()
            with redirect_stderr(stderr):
                try:
                    config = server.parse_args(
                        [
                            "--daemon",
                            "--keyfile",
                            str(keyfile),
                            "--port",
                            "18765",
                            "--require-version",
                            "--idle-timeout",
                            "12.5",
                            "--exec-audit-path",
                            audit_path,
                        ]
                    )
                except SystemExit as error:
                    self.fail(
                        "--exec-audit-path was rejected by parse_args "
                        f"(exit={error.code}, stderr={stderr.getvalue()!r})"
                    )

            daemon_argv = daemon.build_daemon_argv(config, python=executable)
            install_argv = list(
                host_config.resolve_daemon_provenance(
                    claude_path=claude_path,
                    codex_path=codex_path,
                ).argv
            )
            self.assertEqual(config.exec_audit_path, audit_path)
            self.assertEqual(install_argv, daemon_argv)
            self.assertEqual(daemon_argv.count("--exec-audit-path"), 1)
            flag_index = daemon_argv.index("--exec-audit-path")
            self.assertEqual(daemon_argv[flag_index + 1], audit_path)


if __name__ == "__main__":
    unittest.main()
