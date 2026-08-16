from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import server


class _FakeRuntime:
    def __init__(self, player_counts: list[int] | None = None, fallback: int = 0) -> None:
        self.tool_lock = asyncio.Lock()
        self._counts = list(player_counts or [])
        self._fallback = fallback
        self.lifecycle_status = None

    async def call_bridge(
        self, cmd: str, args: dict, peer: str, timeout_s: float
    ) -> dict:
        if cmd != "query_all_players":
            raise server.ToolError(f"unexpected:{cmd}")
        count = self._counts.pop(0) if self._counts else self._fallback
        return {"ok": 1, "players": [{} for _ in range(count)]}


class WaitForTest(unittest.IsolatedAsyncioTestCase):
    async def test_satisfied_on_first_probe(self) -> None:
        runtime = _FakeRuntime(player_counts=[2])
        result = await server.execute_wait_for(
            runtime, "players_at_least", value=2, timeout_s=2.0, poll_interval_s=0.5
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["satisfied"])
        self.assertEqual(result["probes"], 1)
        self.assertEqual(result["observed"], 2)
        self.assertFalse(result["timed_out"])

    async def test_satisfied_on_third_probe(self) -> None:
        runtime = _FakeRuntime(player_counts=[0, 1, 3])
        result = await server.execute_wait_for(
            runtime, "players_at_least", value=2, timeout_s=5.0, poll_interval_s=0.5
        )
        self.assertTrue(result["satisfied"])
        self.assertEqual(result["probes"], 3)
        self.assertEqual(result["observed"], 3)
        self.assertFalse(result["timed_out"])

    async def test_timeout_returns_unsatisfied_without_raising(self) -> None:
        runtime = _FakeRuntime(fallback=0)
        result = await server.execute_wait_for(
            runtime, "players_at_least", value=1, timeout_s=0.6, poll_interval_s=0.5
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["observed"], 0)
        self.assertGreaterEqual(result["probes"], 1)

    async def test_unknown_condition_is_bad_args(self) -> None:
        runtime = _FakeRuntime()
        with self.assertRaises(server.ToolError) as ctx:
            await server.execute_wait_for(runtime, "not_a_condition")
        self.assertIn("bad_args", str(ctx.exception))

    async def test_sleep_does_not_hold_tool_lock(self) -> None:
        # Fails if wait_for wraps its whole body in `async with runtime.tool_lock`.
        # Holding the lock across the inter-probe sleep would starve every other
        # session that shares the daemon. Another caller must be able to acquire
        # the lock while execute_wait_for is sleeping between unsatisfied probes.
        runtime = _FakeRuntime(fallback=0)
        acquired = asyncio.Event()

        async def contender() -> None:
            await asyncio.sleep(0.05)
            async with runtime.tool_lock:
                acquired.set()

        waiter = asyncio.create_task(
            server.execute_wait_for(
                runtime,
                "players_at_least",
                value=99,
                timeout_s=3.0,
                poll_interval_s=0.5,
            )
        )
        rival = asyncio.create_task(contender())
        try:
            await asyncio.wait_for(acquired.wait(), timeout=1.0)
        finally:
            waiter.cancel()
            rival.cancel()
            await asyncio.gather(waiter, rival, return_exceptions=True)
        self.assertTrue(acquired.is_set())

    async def test_log_matches_ignores_preexisting_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles = Path(directory) / "_server" / "profiles"
            profiles.mkdir(parents=True)
            log = profiles / "script.log"
            log.write_text("MATCH already here\n", encoding="utf-8")

            async def lifecycle_status() -> dict:
                return {"runs": [{"run_id": "run-a", "profiles": str(profiles)}]}

            runtime = _FakeRuntime()
            runtime.lifecycle_status = lifecycle_status
            result = await server.execute_wait_for(
                runtime,
                "log_matches",
                pattern="MATCH",
                timeout_s=0.6,
                poll_interval_s=0.5,
            )

        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])
        self.assertGreaterEqual(result["probes"], 1)
