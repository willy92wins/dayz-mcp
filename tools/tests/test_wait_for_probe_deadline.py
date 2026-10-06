"""Recognised probe timeouts at the wait_for global deadline (inbox 9983)."""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from unittest.mock import patch

from dayz_mcp import server
from tests.wait_for_helpers import _ExactWaitClock

_ENTITY = {
    "type": "CarScript",
    "pos": [1.0, 2.0, 3.0],
    "radius": 5.0,
    "field": "found",
    "equals": True,
}

_PRESERVED = (
    "lease_required",
    "version_blocked",
    "daemon_identity_unverified",
    "daemon_response_ambiguous",
)


class _ClockRuntime:
    """One unsatisfied probe, then a probe whose clock jump is the caller's."""

    def __init__(self, clock: _ExactWaitClock, late_error: BaseException, jump_s: float) -> None:
        self.tool_lock = asyncio.Lock()
        self.clock = clock
        self.late_error = late_error
        self.jump_s = jump_s
        self.calls = 0
        self.probe_timeouts: list[float] = []

    async def call_bridge(self, cmd: str, args: dict, peer: str, timeout_s: float) -> dict:
        self.calls += 1
        self.probe_timeouts.append(timeout_s)
        if self.calls == 1:
            if cmd == "query_all_players":
                return {"ok": 1, "players": [{}]}
            if cmd == "telemetry_read":
                return {"ok": 1, "telemetry": {"found": False}}
            raise AssertionError(cmd)
        self.clock.now += self.jump_s
        raise self.late_error


class ProbeDeadlineTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self, runtime: _ClockRuntime, condition: str, **kwargs):
        clock = runtime.clock
        if condition == "entity_state":
            extra = {"entity": _ENTITY}
        elif condition == "players_at_most":
            extra = {"value": 0}
        else:
            extra = {"value": 2}
        extra.update(kwargs)
        with (
            patch("dayz_mcp.server.time.monotonic", clock),
            patch("dayz_mcp.server.asyncio.sleep", clock.sleep),
        ):
            return await server.execute_wait_for(
                runtime,
                condition,
                timeout_s=20.0,
                poll_interval_s=5.0,
                **extra,
            )

    async def test_last_probe_at_global_deadline_returns_json(self) -> None:
        # players_at_least(value=2, timeout_s=20, poll_interval_s=5): one player,
        # then the last probe budget ends on the global deadline.
        for condition, cmd in (
            ("players_at_least", "query_all_players"),
            ("players_at_most", "query_all_players"),
            ("entity_state", "telemetry_read"),
        ):
            with self.subTest(condition=condition):
                clock = _ExactWaitClock()
                message = f"timeout waiting for {cmd} id=7; server peer idle"
                runtime = _ClockRuntime(clock, server.ToolError(message), jump_s=15.0)
                result = await self._run(runtime, condition)
                self.assertEqual(runtime.calls, 2)
                self.assertEqual(clock.now, 20.0)
                self.assertTrue(result["ok"])
                self.assertFalse(result["satisfied"])
                self.assertTrue(result["timed_out"])
                self.assertEqual(result["last_error"], "probe_timeout")
                self.assertEqual(result["tool"], "wait_for")
                self.assertEqual(result["condition"], condition)
                self.assertEqual(result["probes"], 2)
                if condition != "entity_state":
                    self.assertEqual(result["observed"], 1)

    async def test_probe_timeout_before_deadline_stays_an_error(self) -> None:
        for condition, cmd in (
            ("players_at_least", "query_all_players"),
            ("entity_state", "telemetry_read"),
        ):
            with self.subTest(condition=condition):
                clock = _ExactWaitClock()
                message = f"timeout waiting for {cmd} id=9; server peer idle"
                runtime = _ClockRuntime(clock, server.ToolError(message), jump_s=5.0)
                with self.assertRaises(server.ToolError) as caught:
                    await self._run(runtime, condition)
                text = str(caught.exception)
                self.assertLess(clock.now, 20.0)
                if condition.startswith("players_"):
                    self.assertIn("wait_for aborted", text)
                    self.assertIn("reason=probe_timeout", text)
                    self.assertNotIn("wait_for timed out", text)
                else:
                    self.assertEqual(text, message)

    async def test_non_probe_errors_stay_errors_after_the_deadline(self) -> None:
        for condition in ("players_at_least", "entity_state"):
            for preserved in _PRESERVED:
                with self.subTest(condition=condition, error=preserved):
                    clock = _ExactWaitClock()
                    runtime = _ClockRuntime(
                        clock, server.ToolError(preserved), jump_s=15.0
                    )
                    with self.assertRaises(server.ToolError) as caught:
                        await self._run(runtime, condition)
                    self.assertGreaterEqual(clock.now, 20.0)
                    self.assertEqual(str(caught.exception), preserved)

    async def test_unrelated_exception_after_deadline_is_not_swallowed(self) -> None:
        for condition in ("players_at_least", "entity_state"):
            with self.subTest(condition=condition):
                clock = _ExactWaitClock()
                runtime = _ClockRuntime(clock, RuntimeError("bridge exploded"), jump_s=15.0)
                with self.assertRaises(RuntimeError) as caught:
                    await self._run(runtime, condition)
                self.assertGreaterEqual(clock.now, 20.0)
                self.assertEqual(str(caught.exception), "bridge exploded")


if __name__ == "__main__":
    unittest.main()
