"""The client side of session_acquire_wait.

Moved verbatim from test_session_acquire_wait.py
(review 2026-09-25, W4d step 3). The session_wait_quota tests came later
(#317): a virtual clock checks each pause, and real-time tests are the smoke.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from typing import Callable
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import control_client, server
from dayz_mcp.server import ServerConfig
from dayz_mcp.session_coordination import MAX_OPERATION_TOMBSTONES
from tests._tiers import slow_test
from tests.catalog_helpers import list_tools_after_lease
from tests.client_helpers import _fixture_client_runtime
from tests.session_wait_quota_cases import QUOTA_PAUSE_CASES

_REAL_SLEEP = asyncio.sleep
_ACTIVE = {"status": "active", "lease_token": "token-a", "lease_id": "lease-a"}


class _VirtualClock:
    """A monotonic clock that only the fake transport and the pause move.

    Dyadic values (1024.0, 0.375, 0.25) keep the arithmetic exact, so a
    pause compares with ==.
    """

    def __init__(self) -> None:
        self.now = 1024.0
        self.pauses: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.pauses.append(seconds)
        self.now += seconds
        await _REAL_SLEEP(0)


def _asyncio_with_sleep(sleep: Callable) -> types.ModuleType:
    """control_client's view of asyncio, with only sleep replaced."""
    view = types.ModuleType("asyncio")
    view.__dict__.update(vars(asyncio))
    view.sleep = sleep
    return view


# --- from test_session_acquire_wait.py ---


class ClientAcquireWaitTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self) -> server.ClientRuntime:
        return _fixture_client_runtime(
            ServerConfig(
                mode="client",
                key="test-key",
                port=12345,
                client_platform="codex",
                auto_spawn_daemon=False,
                log_sink=lambda _message: None,
            )
        )

    async def test_queued_progress_then_active_never_returns_queued(self) -> None:
        runtime = self.runtime()
        waits = iter(
            (
                {"status": "queued", "ticket": "ticket-a", "position": 2},
                {"status": "queued", "ticket": "ticket-a", "position": 1},
                {
                    "status": "active",
                    "lease_token": "token-a",
                    "lease_id": "lease-a",
                },
            )
        )
        operations: list[str] = []
        progress: list[tuple[float, float | None, str | None]] = []

        def request(path, payload, _timeout_s):
            if path == "/session/enqueue":
                operations.append(payload["operation_id"])
                return {
                    "status": "queued",
                    "ticket": "ticket-a",
                    "position": 3,
                    "operation_id": payload["operation_id"],
                }
            if path == "/session/wait":
                response = dict(next(waits))
                response["ticket"] = "ticket-a"
                response["operation_id"] = operations[0]
                return response
            raise AssertionError(path)

        async def report(
            current: float, total: float | None, message: str | None
        ) -> None:
            progress.append((current, total, message))

        runtime._control._request_once = request
        result = await runtime.session_acquire_wait("build", 2.0, report)

        self.assertEqual(result["status"], "active")
        self.assertEqual(result["lease_token"], "token-a")
        self.assertEqual(len(operations), 1)
        self.assertEqual(runtime.active_operation_id, operations[0])
        self.assertEqual([item[2] for item in progress], [
            "En cola (posición 3) para build",
            "En cola (posición 2) para build",
            "En cola (posición 1) para build",
        ])

    @slow_test
    async def test_duplicate_quota_pauses_before_the_next_wait(self) -> None:
        runtime = self.runtime()
        calls = {"n": 0}
        operation_ids: list[str] = []

        def request(path, payload, _timeout_s):
            if path == "/session/enqueue":
                operation_ids.append(payload["operation_id"])
                return {
                    "status": "queued",
                    "ticket": "ticket-a",
                    "position": 1,
                    "operation_id": payload["operation_id"],
                }
            if path == "/session/wait":
                calls["n"] += 1
                operation_id = operation_ids[0]
                if calls["n"] == 1:
                    return {
                        "status": "queued",
                        "ticket": "ticket-a",
                        "position": 1,
                        "operation_id": operation_id,
                        "session_wait_quota": "duplicate",
                        "retry_after_s": 30,
                    }
                return {
                    "status": "active",
                    "ticket": "ticket-a",
                    "operation_id": operation_id,
                    "lease_token": "token-a",
                    "lease_id": "lease-a",
                }
            raise AssertionError(path)

        runtime._control._request_once = request
        started = time.monotonic()
        result = await runtime.session_acquire_wait("build", 5.0)
        elapsed = time.monotonic() - started
        self.assertEqual(result["status"], "active")
        self.assertEqual(calls["n"], 2)
        # The cap is 1s. 0.9s still fails if the pause is removed.
        # 2.5s leaves room when the machine is busy.
        self.assertGreaterEqual(elapsed, 0.9)
        self.assertLess(elapsed, 2.5)

    @staticmethod
    def _quota_requests(
        waits: list[dict[str, object]],
    ) -> tuple[Callable[[str, dict, float], dict], dict[str, int]]:
        """A fake _request_once for ticket-a.

        Each /session/wait answers the next item of ``waits``, and the last
        one repeats. ``calls`` counts the waits.
        """
        calls = {"wait": 0}
        operation: list[str] = []

        def request(path, payload, _timeout_s):
            if path == "/session/enqueue":
                operation.append(payload["operation_id"])
                return {
                    "status": "queued",
                    "ticket": "ticket-a",
                    "position": 1,
                    "operation_id": operation[0],
                }
            if path == "/session/wait":
                answer = dict(waits[min(calls["wait"], len(waits) - 1)])
                calls["wait"] += 1
                answer.update(ticket="ticket-a", operation_id=operation[0])
                return answer
            if path == "/session/cancel-operation":
                return {"cancelled": True, "operation_id": payload["operation_id"]}
            raise AssertionError(path)

        return request, calls

    async def _virtual_acquire(
        self,
        waits: list[dict[str, object]],
        max_wait_s: float,
        transport_s: float = 0.0,
    ) -> tuple[object, _VirtualClock, dict[str, int]]:
        """session_acquire_wait on a virtual clock.

        Each /session/wait takes transport_s of virtual time to answer.
        Returns the result, or the exception raised, with the clock and
        the call counts.
        """
        runtime = self.runtime()
        request, calls = self._quota_requests(waits)
        clock = _VirtualClock()

        def timed_request(path, payload, timeout_s):
            if path == "/session/wait":
                clock.now += transport_s
            return request(path, payload, timeout_s)

        runtime._control._request_once = timed_request
        with patch.object(control_client, "_monotonic", clock.monotonic), patch.object(
            control_client, "asyncio", _asyncio_with_sleep(clock.sleep)
        ):
            try:
                outcome: object = await runtime.session_acquire_wait("build", max_wait_s)
            except Exception as error:
                outcome = error
        return outcome, clock, calls

    async def test_the_deadline_cut_reads_the_clock_after_the_answer(self) -> None:
        # The answer takes 0.25s of a 0.375s budget. The pause asks for the
        # 1s cap and is cut to the 0.125s left after the answer: a cut to
        # what was left before the request would sleep 0.375s and return
        # 0.25s late.
        outcome, clock, calls = await self._virtual_acquire(
            [
                {
                    "status": "queued",
                    "position": 1,
                    "session_wait_quota": "duplicate",
                    "retry_after_s": 30,
                }
            ],
            max_wait_s=0.375,
            transport_s=0.25,
        )
        self.assertIsInstance(outcome, Exception)
        self.assertIn("session_wait_timeout", str(outcome))
        self.assertEqual(calls["wait"], 1)
        self.assertEqual(clock.pauses, [0.125])
        self.assertEqual(clock.now, 1024.375)

    async def test_the_quota_pause_follows_the_retry_rule(self) -> None:
        for label, quota, retry_after, pauses in QUOTA_PAUSE_CASES:
            with self.subTest(label):
                outcome, clock, calls = await self._virtual_acquire(
                    [
                        {
                            "status": "queued",
                            "position": 1,
                            "session_wait_quota": quota,
                            "retry_after_s": retry_after,
                        },
                        _ACTIVE,
                    ],
                    max_wait_s=64.0,
                )
                self.assertIsInstance(outcome, dict)
                self.assertEqual(outcome["status"], "active")
                self.assertEqual(calls["wait"], 2)
                self.assertEqual(clock.pauses, pauses)

    @slow_test
    async def test_duplicate_pause_stays_inside_the_caller_deadline(self) -> None:
        runtime = self.runtime()
        request, calls = self._quota_requests(
            [
                {
                    "status": "queued",
                    "position": 1,
                    "session_wait_quota": "duplicate",
                    "retry_after_s": 30,
                }
            ]
        )
        runtime._control._request_once = request
        started = time.monotonic()
        with self.assertRaisesRegex(Exception, "session_wait_timeout"):
            await runtime.session_acquire_wait("build", 0.3)
        elapsed = time.monotonic() - started
        # retry_after_s=30 asks for the whole 1s cap. Cut to the 0.3s the
        # caller has left, the wait times out near 0.3s. Without the cut it
        # sleeps the 1s first (measured 1.002s).
        self.assertGreaterEqual(calls["wait"], 1)
        self.assertLess(elapsed, 0.8)

    @slow_test
    async def test_duplicate_with_a_huge_retry_after_does_not_overflow(
        self,
    ) -> None:
        runtime = self.runtime()
        request, calls = self._quota_requests(
            [
                {
                    "status": "queued",
                    "position": 1,
                    "session_wait_quota": "duplicate",
                    # json.loads keeps an integer too large for a float
                    # (401 digits is under Python's 4300-digit limit), and
                    # float() of it raises OverflowError.
                    "retry_after_s": 10**400,
                },
                {
                    "status": "active",
                    "lease_token": "token-a",
                    "lease_id": "lease-a",
                },
            ]
        )
        runtime._control._request_once = request
        result = await runtime.session_acquire_wait("build", 5.0)
        self.assertEqual(result["status"], "active")
        self.assertEqual(calls["wait"], 2)

    @slow_test
    async def test_a_list_quota_is_not_a_pause(self) -> None:
        runtime = self.runtime()
        request, _calls = self._quota_requests(
            [
                {
                    "status": "queued",
                    "position": 1,
                    "session_wait_quota": ["duplicate"],
                    "retry_after_s": 10**400,
                }
            ]
        )
        runtime._control._request_once = request
        started = time.monotonic()
        with self.assertRaisesRegex(Exception, "session_wait_timeout"):
            await runtime.session_acquire_wait("build", 0.3)
        elapsed = time.monotonic() - started
        # Only a string is a quota: testing a list against the set would
        # raise TypeError. With no quota there is no pause, so retry_after_s
        # is never read and the wait times out at the caller's 0.3s.
        self.assertLess(elapsed, 0.8)

    async def test_timeout_tombstones_exact_operation_and_clears_state(self) -> None:
        runtime = self.runtime()
        seen: dict[str, str] = {}

        def request(path, payload, _timeout_s):
            if path == "/session/enqueue":
                seen["enqueue"] = payload["operation_id"]
                return {"status": "queued", "ticket": "ticket-a", "position": 1, "operation_id": seen["enqueue"]}
            if path == "/session/wait":
                return {"status": "queued", "ticket": "ticket-a", "position": 1, "operation_id": seen["enqueue"]}
            if path == "/session/cancel-operation":
                seen["cancel"] = payload["operation_id"]
                return {
                    "cancelled": True,
                    "operation_id": payload["operation_id"],
                }
            raise AssertionError(path)

        runtime._control._request_once = request
        with self.assertRaisesRegex(Exception, "session_wait_timeout"):
            await runtime.session_acquire_wait("build", 0.01)

        self.assertEqual(seen["cancel"], seen["enqueue"])
        self.assertIsNone(runtime.active_ticket)
        self.assertIsNone(runtime.active_lease_token)
        self.assertIsNone(runtime.active_operation_id)

    async def test_session_acquire_wait_toolerror_includes_tombstone_recipe(self) -> None:
        runtime = self.runtime()
        saturated = {
            "error": "operation_tombstones_saturated",
            "count": MAX_OPERATION_TOMBSTONES,
            "capacity": MAX_OPERATION_TOMBSTONES,
            "retry_after_s": 120.0,
            "hint": (
                "wait 120s then retry session_acquire_wait; "
                "do not spin"
            ),
        }

        def request_with_refresh(
            *,
            method: str,
            path: str,
            query: dict[str, str],
            body: bytes | None,
            headers: dict[str, str],
            deadline: float,
        ) -> tuple[int, bytes]:
            if path == "/session/enqueue":
                return 503, json.dumps(saturated).encode("utf-8")
            payload = json.loads((body or b"{}").decode("utf-8"))
            if path == "/session/cancel-operation":
                return 200, json.dumps(
                    {
                        "cancelled": True,
                        "operation_id": payload.get("operation_id"),
                    }
                ).encode("utf-8")
            raise AssertionError(path)

        runtime._control._credential_provider.request_with_refresh = (
            request_with_refresh
        )
        with patch.object(
            type(runtime._control.policy), "revalidate", return_value=None
        ):
            with self.assertRaises(server.ToolError) as raised:
                await runtime.session_acquire_wait("build", 1.0)
        message = str(raised.exception)
        self.assertIn("operation_tombstones_saturated", message)
        self.assertIn("session_acquire_wait", message)
        self.assertIn("do not spin", message)
        self.assertNotEqual(message, "operation_tombstones_saturated")

    async def test_unknown_remote_code_with_hint_is_still_masked(self) -> None:
        leaked_code = "leaked_internal_code"
        leaked_hint = "secret-hint-do-not-surface"

        def _attach_http(runtime: server.ClientRuntime, payload: dict[str, object]) -> None:
            def request_with_refresh(
                *,
                method: str,
                path: str,
                query: dict[str, str],
                body: bytes | None,
                headers: dict[str, str],
                deadline: float,
            ) -> tuple[int, bytes]:
                if path == "/session/enqueue":
                    return 503, json.dumps(payload).encode("utf-8")
                request_payload = json.loads((body or b"{}").decode("utf-8"))
                if path == "/session/cancel-operation":
                    return 200, json.dumps(
                        {
                            "cancelled": True,
                            "operation_id": request_payload.get("operation_id"),
                        }
                    ).encode("utf-8")
                raise AssertionError(path)

            runtime._control._credential_provider.request_with_refresh = (
                request_with_refresh
            )

        unknown_runtime = self.runtime()
        _attach_http(
            unknown_runtime,
            {"error": leaked_code, "hint": leaked_hint},
        )
        with patch.object(
            type(unknown_runtime._control.policy), "revalidate", return_value=None
        ):
            with self.assertRaises(server.ToolError) as unknown_raised:
                await unknown_runtime.session_acquire_wait("build", 1.0)
        unknown_message = str(unknown_raised.exception)
        self.assertEqual(unknown_message, "remote_error")
        self.assertNotIn(leaked_code, unknown_message)
        self.assertNotIn(leaked_hint, unknown_message)

        recipe_runtime = self.runtime()
        _attach_http(
            recipe_runtime,
            {
                "error": "operation_tombstones_saturated",
                "count": MAX_OPERATION_TOMBSTONES,
                "capacity": MAX_OPERATION_TOMBSTONES,
                "retry_after_s": 120.0,
                "hint": (
                    "wait 120s then retry session_acquire_wait; "
                    "do not spin"
                ),
            },
        )
        with patch.object(
            type(recipe_runtime._control.policy), "revalidate", return_value=None
        ):
            with self.assertRaises(server.ToolError) as recipe_raised:
                await recipe_runtime.session_acquire_wait("build", 1.0)
        recipe = str(recipe_raised.exception)
        self.assertIn("operation_tombstones_saturated", recipe)
        self.assertIn("session_acquire_wait", recipe)
        self.assertIn("do not spin", recipe)
        self.assertNotEqual(recipe, "operation_tombstones_saturated")

    async def test_progress_failure_preserves_primary_exception_after_cleanup(self) -> None:
        runtime = self.runtime()
        primary = ValueError("progress exploded")
        cancelled: list[str] = []
        operation: list[str] = []

        def request(path, payload, _timeout_s):
            if path == "/session/enqueue":
                operation.append(payload["operation_id"])
                return {"status": "queued", "ticket": "ticket-a", "position": 1, "operation_id": operation[0]}
            if path == "/session/wait":
                return {"status": "queued", "ticket": "ticket-a", "position": 1, "operation_id": operation[0]}
            if path == "/session/cancel-operation":
                cancelled.append(payload["operation_id"])
                return {
                    "cancelled": True,
                    "operation_id": payload["operation_id"],
                }
            raise AssertionError(path)

        async def fail_progress(*_args) -> None:
            raise primary

        runtime._control._request_once = request
        with self.assertRaises(ValueError) as raised:
            await runtime.session_acquire_wait("build", 1.0, fail_progress)

        self.assertIs(raised.exception, primary)
        self.assertEqual(str(raised.exception), "progress exploded")
        self.assertEqual(len(cancelled), 1)

    async def test_cancelled_await_tombstones_before_late_enqueue_returns(self) -> None:
        runtime = self.runtime()
        enqueue_started = threading.Event()
        release_enqueue = threading.Event()
        cancel_seen = threading.Event()
        operations: dict[str, str] = {}

        def request(path, payload, _timeout_s):
            if path == "/session/enqueue":
                operations["enqueue"] = payload["operation_id"]
                enqueue_started.set()
                release_enqueue.wait(2.0)
                return {"status": "queued", "ticket": "late", "position": 1, "operation_id": operations["enqueue"]}
            if path == "/session/cancel-operation":
                operations["cancel"] = payload["operation_id"]
                cancel_seen.set()
                return {
                    "cancelled": True,
                    "operation_id": payload["operation_id"],
                }
            raise AssertionError(path)

        runtime._control._request_once = request
        task = asyncio.create_task(runtime.session_acquire_wait("build", 1.0))
        self.assertTrue(await asyncio.to_thread(enqueue_started.wait, 1.0))
        task.cancel()
        self.assertTrue(await asyncio.to_thread(cancel_seen.wait, 1.0))
        release_enqueue.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(operations["cancel"], operations["enqueue"])

    async def test_fastmcp_schema_hides_context_and_registers_cancel(self) -> None:
        config = ServerConfig(
            mode="client", key="test-key", port=12345, auto_spawn_daemon=False
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, built_runtime = server.build_app(config)
        self.assertIs(built_runtime, runtime)
        tools = {tool.name: tool for tool in await list_tools_after_lease(app, runtime)}

        acquire_schema = tools["session_acquire_wait"].inputSchema
        self.assertEqual(
            set(acquire_schema["properties"]), {"purpose", "max_wait_s"}
        )
        wait_schema = acquire_schema["properties"]["max_wait_s"]
        self.assertIsNone(wait_schema["default"])
        self.assertIn("null", str(wait_schema).lower())
        self.assertNotIn("maximum", str(wait_schema).lower())
        self.assertNotIn("1800", str(wait_schema))
        self.assertNotIn("ctx", acquire_schema["properties"])
        self.assertEqual(
            set(tools["session_cancel"].inputSchema["properties"]), {"ticket"}
        )

    async def test_public_acquire_wait_forwards_none_and_any_positive_finite_value(
        self,
    ) -> None:
        config = ServerConfig(
            mode="client", key="test-key", port=12345, auto_spawn_daemon=False
        )
        runtime = _fixture_client_runtime(config)
        acquire_wait = AsyncMock(return_value={"status": "active"})
        runtime.session_acquire_wait = acquire_wait
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, _built_runtime = server.build_app(config)
        tool = app._tool_manager.get_tool("session_acquire_wait")
        self.assertIsNotNone(tool)

        outcomes: list[object] = []
        for arguments in (
            {"purpose": "omitted"},
            {"purpose": "explicit-none", "max_wait_s": None},
            {"purpose": "beyond-old-limit", "max_wait_s": 7200.25},
        ):
            try:
                outcomes.append(await tool.fn(**arguments))
            except BaseException as error:
                outcomes.append(error)

        self.assertTrue(all(isinstance(outcome, dict) for outcome in outcomes))
        forwarded = [call.args[1] for call in acquire_wait.await_args_list]
        self.assertEqual(forwarded, [None, None, 7200.25])

    async def test_public_acquire_wait_rejects_invalid_timeout_before_transport(
        self,
    ) -> None:
        config = ServerConfig(
            mode="client", key="test-key", port=12345, auto_spawn_daemon=False
        )
        runtime = _fixture_client_runtime(config)
        acquire_wait = AsyncMock(return_value={"status": "active"})
        runtime.session_acquire_wait = acquire_wait
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, _built_runtime = server.build_app(config)
        tool = app._tool_manager.get_tool("session_acquire_wait")
        self.assertIsNotNone(tool)

        for value in (True, float("nan"), float("inf"), float("-inf"), 0.0, -1.0):
            with self.subTest(value=value):
                with self.assertRaises(server.ToolError) as raised:
                    await tool.fn(purpose="invalid", max_wait_s=value)
                self.assertEqual(str(raised.exception), "bad_wait_timeout")
        acquire_wait.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
