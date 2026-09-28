"""wait_for keeps timeout_s while another tool holds tool_lock.

execute_wait_for fixes its deadline before it asks for runtime.tool_lock. While
that wait had no bound, a sibling tool of the same client holding the lock for
2 s stretched a wait_for(timeout_s=0.5) to 1.998 s (fb-20260928-001643-f280).
The time spent waiting for the lock now counts against timeout_s.
"""
from __future__ import annotations

import asyncio
import time
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from dayz_mcp import server

HOLD_S = 2.0
TIMEOUT_S = 0.5
# Generous on purpose: the claim is "not HOLD_S", not a precise latency.
SLACK_S = 0.6


class _FakeRuntime:
    def __init__(self) -> None:
        self.tool_lock = asyncio.Lock()
        self.bridge_calls = 0

    async def call_bridge(
        self, cmd: str, args: dict[str, Any], peer: str, timeout_s: float
    ) -> dict[str, Any]:
        self.bridge_calls += 1
        return {"ok": 1, "players": [], "count": 0}


async def _hold(runtime: _FakeRuntime, held: asyncio.Event, seconds: float) -> None:
    async with runtime.tool_lock:
        held.set()
        await asyncio.sleep(seconds)


class WaitForLockDeadlineTest(unittest.IsolatedAsyncioTestCase):
    async def _timed(self, runtime: _FakeRuntime, **kwargs: Any) -> tuple[float, dict[str, Any]]:
        started = time.monotonic()
        result = await server.execute_wait_for(runtime, **kwargs)
        return time.monotonic() - started, result

    async def _held_for(self, runtime: _FakeRuntime, seconds: float) -> asyncio.Task:
        held = asyncio.Event()
        holder = asyncio.create_task(_hold(runtime, held, seconds))
        await held.wait()
        return holder

    async def test_players_wait_keeps_its_deadline_while_the_lock_is_held(self) -> None:
        runtime = _FakeRuntime()
        holder = await self._held_for(runtime, HOLD_S)
        elapsed, result = await self._timed(
            runtime,
            condition="players_at_least",
            value=5,
            timeout_s=TIMEOUT_S,
            poll_interval_s=0.1,
        )
        self.assertLess(elapsed, TIMEOUT_S + SLACK_S)
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["last_error"], "tool_lock_busy")
        self.assertEqual(runtime.bridge_calls, 0)
        await holder
        self.assertFalse(runtime.tool_lock.locked())

    async def test_log_wait_keeps_its_deadline_while_the_lock_is_held(self) -> None:
        runtime = _FakeRuntime()
        holder = await self._held_for(runtime, HOLD_S)
        # No run, no log files: the wait can only time out, and how long that
        # takes is the whole question.
        with patch.object(server, "_wait_for_script_log_paths", AsyncMock(return_value=[])):
            elapsed, result = await self._timed(
                runtime, condition="log_matches", pattern="never", timeout_s=TIMEOUT_S
            )
        self.assertLess(elapsed, TIMEOUT_S + SLACK_S)
        self.assertFalse(result["satisfied"])
        self.assertEqual(result["last_error"], "tool_lock_busy")
        await holder
        self.assertFalse(runtime.tool_lock.locked())

    async def test_lock_freed_before_the_deadline_is_taken(self) -> None:
        runtime = _FakeRuntime()
        holder = await self._held_for(runtime, 0.2)
        elapsed, result = await self._timed(
            runtime, condition="players_at_least", value=0, timeout_s=2.0
        )
        self.assertTrue(result["satisfied"])
        self.assertLess(elapsed, 1.0)
        self.assertEqual(runtime.bridge_calls, 1)
        await holder
        self.assertFalse(runtime.tool_lock.locked())

    async def test_free_lock_behaves_as_before(self) -> None:
        # Control: with nobody on the lock the wait probes until its own
        # deadline and names no lock error.
        runtime = _FakeRuntime()
        elapsed, result = await self._timed(
            runtime,
            condition="players_at_least",
            value=5,
            timeout_s=TIMEOUT_S,
            poll_interval_s=0.1,
        )
        self.assertGreaterEqual(elapsed, TIMEOUT_S * 0.9)
        self.assertLess(elapsed, TIMEOUT_S + SLACK_S)
        self.assertFalse(result["satisfied"])
        # WAIT_FOR_MIN_POLL_INTERVAL_S (0.5 s) leaves room for one probe here.
        self.assertGreaterEqual(runtime.bridge_calls, 1)
        self.assertNotIn("last_error", result)
        self.assertFalse(runtime.tool_lock.locked())


if __name__ == "__main__":
    unittest.main()
