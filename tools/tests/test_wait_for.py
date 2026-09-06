from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, server
from dayz_mcp.server import ServerConfig, build_app
from tests.test_client_mode import _fixture_client_runtime
from tests.test_mcp_tools import _content_json

def _live_run(profiles: Path) -> dict:
    """A RUNNING run: a process stamped just now, so its logs clear the floor.

    `wait_for` derives the launch floor from the earliest process of the newest
    run and refuses a snapshot with no live run at all, so a fixture without a
    process no longer describes anything the lifecycle can produce.
    """

    stamp = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    return {
        "run_id": "run-a",
        "profiles": str(profiles),
        "processes": [{"pid": 4242, "creation_time_utc": stamp}],
    }


class _FakeRuntime:
    def __init__(self, player_counts: list[int] | None = None, fallback: int = 0) -> None:
        self.tool_lock = asyncio.Lock()
        self._counts = list(player_counts or [])
        self._fallback = fallback
        self.lifecycle_status = None
        self.lock_acquisitions = 0
        self.bridge_calls = 0

    async def call_bridge(
        self, cmd: str, args: dict, peer: str, timeout_s: float
    ) -> dict:
        self.bridge_calls += 1
        if cmd != "query_all_players":
            raise server.ToolError(f"unexpected:{cmd}")
        count = self._counts.pop(0) if self._counts else self._fallback
        if isinstance(count, str):
            raise server.ToolError(count)
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
        self.assertEqual(result["tool"], "wait_for")
        self.assertEqual(result["observed"], 0)
        self.assertGreaterEqual(result["probes"], 1)

    async def test_unknown_condition_is_bad_args(self) -> None:
        runtime = _FakeRuntime()
        with self.assertRaises(server.ToolError) as ctx:
            await server.execute_wait_for(runtime, "not_a_condition")
        self.assertIn("bad_args", str(ctx.exception))
        self.assertIn("players_at_least", str(ctx.exception))

    async def test_version_blocked_aborts_first_probe(self) -> None:
        class _Blocked(_FakeRuntime):
            async def call_bridge(self, cmd, args, peer, timeout_s):
                raise server.ToolError("version_blocked")

        runtime = _Blocked()
        with self.assertRaises(server.ToolError) as ctx:
            await server.execute_wait_for(
                runtime,
                "players_at_least",
                value=1,
                timeout_s=10.0,
                poll_interval_s=2.0,
            )
        self.assertIn("version_blocked", str(ctx.exception))

    async def test_rejects_timeout_above_600_before_lock(self) -> None:
        runtime = _FakeRuntime(player_counts=[1])
        with self.assertRaises(server.ToolError) as ctx:
            await server.execute_wait_for(
                runtime, "players_at_least", value=1, timeout_s=601.0, poll_interval_s=0.5
            )
        self.assertIn("bad_args: timeout_s must be <= 600", str(ctx.exception))
        self.assertEqual(runtime.bridge_calls, 0)

    async def test_accepts_exactly_600(self) -> None:
        runtime = _FakeRuntime(player_counts=[1])
        result = await server.execute_wait_for(
            runtime, "players_at_least", value=1, timeout_s=600.0, poll_interval_s=0.5
        )
        self.assertTrue(result["satisfied"])
        self.assertEqual(runtime.bridge_calls, 1)

    async def test_waits_through_server_poll_stale(self) -> None:
        stale = "game_not_ready:reason=server_poll_stale"
        runtime = _FakeRuntime(player_counts=[stale, stale, 1])
        result = await server.execute_wait_for(
            runtime, "players_at_least", value=1, timeout_s=10.0, poll_interval_s=0.2
        )
        self.assertTrue(result["satisfied"])
        self.assertEqual(result["probes"], 3)
        self.assertEqual(result["not_ready_probes"], 2)
        self.assertEqual(runtime.bridge_calls, 3)

    async def test_server_poll_stale_timeout_reports_last_error(self) -> None:
        stale = "game_not_ready:reason=server_poll_stale"
        runtime = _FakeRuntime(fallback=stale)
        result = await server.execute_wait_for(
            runtime, "players_at_least", value=1, timeout_s=1.2, poll_interval_s=0.3
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["last_error"], stale)
        self.assertGreaterEqual(result["not_ready_probes"], 2)

    async def test_other_game_not_ready_aborts_first_probe(self) -> None:
        runtime = _FakeRuntime(player_counts=["game_not_ready:reason=no_run", 1])
        with self.assertRaises(server.ToolError) as ctx:
            await server.execute_wait_for(
                runtime, "players_at_least", value=1, timeout_s=5.0, poll_interval_s=0.2
            )
        self.assertIn("game_not_ready:reason=no_run", str(ctx.exception))
        self.assertEqual(runtime.bridge_calls, 1)

    async def test_an_ownership_refusal_aborts_the_first_probe_with_its_hint(self) -> None:
        message = "run_not_owned: " + loopback._RUN_NOT_OWNED_HINT
        runtime = _FakeRuntime(player_counts=[message, 1])
        with self.assertRaises(server.ToolError) as ctx:
            await server.execute_wait_for(
                runtime, "players_at_least", value=1, timeout_s=5.0, poll_interval_s=0.2
            )
        self.assertEqual(str(ctx.exception), message)
        self.assertEqual(runtime.bridge_calls, 1)

    async def test_deadline_bounds_the_sleep(self) -> None:
        # A poll interval longer than the remaining budget must not extend the
        # call past timeout_s: the single deadline governs the sleep too (Codex B-02).
        import time

        runtime = _FakeRuntime(fallback="game_not_ready:reason=server_poll_stale")
        t0 = time.monotonic()
        result = await server.execute_wait_for(
            runtime, "players_at_least", value=1, timeout_s=0.1, poll_interval_s=0.5
        )
        wall = time.monotonic() - t0
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])
        self.assertEqual(runtime.bridge_calls, 1)
        self.assertLess(wall, 0.3, f"deadline exceeded: {wall:.3f}s for timeout_s=0.1")

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
                return {"runs": [_live_run(profiles)]}

            runtime = _FakeRuntime()
            runtime.lifecycle_status = lifecycle_status
            result = await server.execute_wait_for(
                runtime,
                "log_matches",
                pattern="MATCH",
                timeout_s=0.6,
                poll_interval_s=0.5,
                lookback_lines=0,
            )

        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["tool"], "wait_for")
        self.assertGreaterEqual(result["probes"], 1)

    async def test_log_matches_lookback_sees_recent_preexisting_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles = Path(directory) / "_server" / "profiles"
            profiles.mkdir(parents=True)
            log = profiles / "script.log"
            lines = [f"line-{i}\n" for i in range(10)]
            lines.append("BTCOpenResponse\n")
            log.write_text("".join(lines), encoding="utf-8")

            async def lifecycle_status() -> dict:
                return {"runs": [_live_run(profiles)]}

            runtime = _FakeRuntime()
            runtime.lifecycle_status = lifecycle_status
            result = await server.execute_wait_for(
                runtime,
                "log_matches",
                pattern="BTCOpenResponse",
                timeout_s=0.6,
                poll_interval_s=0.5,
                lookback_lines=5,
            )

        self.assertTrue(result["satisfied"])
        self.assertTrue(result["ok"])
        self.assertIn("BTCOpenResponse", str(result["observed"]))

    async def test_log_matches_lookback_misses_line_outside_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles = Path(directory) / "_server" / "profiles"
            profiles.mkdir(parents=True)
            log = profiles / "script.log"
            lines = ["BTCOpenResponse\n"] + [f"later-{i}\n" for i in range(20)]
            log.write_text("".join(lines), encoding="utf-8")

            async def lifecycle_status() -> dict:
                return {"runs": [_live_run(profiles)]}

            runtime = _FakeRuntime()
            runtime.lifecycle_status = lifecycle_status
            result = await server.execute_wait_for(
                runtime,
                "log_matches",
                pattern="BTCOpenResponse",
                timeout_s=0.6,
                poll_interval_s=0.5,
                lookback_lines=5,
            )

        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])

    async def test_log_matches_lookback_zero_misses_preexisting_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles = Path(directory) / "_server" / "profiles"
            profiles.mkdir(parents=True)
            log = profiles / "script.log"
            log.write_text("BTCOpenResponse\n", encoding="utf-8")

            async def lifecycle_status() -> dict:
                return {"runs": [_live_run(profiles)]}

            runtime = _FakeRuntime()
            runtime.lifecycle_status = lifecycle_status
            result = await server.execute_wait_for(
                runtime,
                "log_matches",
                pattern="BTCOpenResponse",
                timeout_s=0.6,
                poll_interval_s=0.5,
                lookback_lines=0,
            )

        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])

    async def test_lookback_lines_out_of_range_is_bad_args(self) -> None:
        runtime = _FakeRuntime()
        for bad in (-1, 2001, True):
            with self.subTest(bad=bad):
                with self.assertRaises(server.ToolError) as ctx:
                    await server.execute_wait_for(
                        runtime,
                        "players_at_least",
                        value=1,
                        lookback_lines=bad,
                    )
                self.assertIn("lookback_lines", str(ctx.exception))


# --- BUG-086: evidence that stands on its own -------------------------------
#
# The bug is fixed and was confirmed in-game; the window cases above already
# cover "inside" and "outside". What none of them proves is the ordering the
# incident was actually about. They write the needle before wait_for exists, so
# a build that took its marker at EOF would pass them too -- and taking the
# marker at EOF is precisely the defect. These cases close that gap without
# borrowing a verdict from the sibling ticket:
#
#   * the causal case publishes through an action that flushes, fsyncs and
#     closes BEFORE returning, so the response is durable while the marker is
#     still unborn. That is the sequence action_use -> wait_for;
#   * the lookback_lines=0 control has to prove it looked. From outside, "read
#     the file and nothing matched" and "never opened the file" are the same
#     verdict, so the control asserts a probe and a non-matching line read from
#     that same file;
#   * the two cardinality cases pin the inclusive edge of the window at 200
#     from the literal table below, not from the offset helpers they exercise.
#
# Load-bearing mutants, both one line of server.py and both required to go red:
#   1. drop the dedicated lookback_lines<=0 branch (:1866-1867) AND treat 0 as
#      the positive default 200 -- the control must fail. Deleting that branch
#      alone is an equivalent mutant: _offset_before_last_lines_in_window also
#      returns EOF for lookback_lines<=0 (:1809-1810), so the behaviour does not
#      change and a red would not mean anything;
#   2. widen the rewind to _marker_rewound(path, lookback_lines + 1) (:1871) --
#      OUTSIDE-201 must fail, and the tests stay byte-identical.


def _append_durably(path: Path, text: str) -> tuple[int, int]:
    """Append ``text``, then return ``(size_before, size_after)``.

    The fsync is here for the ordering, not for durability: the bytes have to be
    on disk and the handle closed before this returns, or the caller cannot
    claim the response preceded the marker.
    """

    before = path.stat().st_size
    with open(path, "a", encoding="utf-8", newline="") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    return before, path.stat().st_size


# label, filler lines written after the needle, satisfied, lines_total.
# The window is inclusive from EOF, so a needle with 199 complete lines behind
# it occupies slot 200 and survives; with 200 behind it, slot 201, it does not.
# Both scan exactly 200 lines: what changes is what is in them.
_WINDOW_CASES = (
    ("EDGE-200", 199, True, 200),
    ("OUTSIDE-201", 200, False, 200),
)


class WaitForBug086EvidenceTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _profiles(directory: str) -> Path:
        profiles = Path(directory) / "_server" / "profiles"
        profiles.mkdir(parents=True)
        return profiles

    async def test_response_durable_before_the_marker_is_still_seen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles = self._profiles(directory)
            log = profiles / "script.log"
            log.write_text("".join(f"boot-{i}\n" for i in range(5)), encoding="utf-8")

            needle = f"BUG086-response-{uuid.uuid4().hex}"
            before, after = _append_durably(log, needle + "\n")

            async def lifecycle_status() -> dict:
                return {"runs": [_live_run(profiles)]}

            runtime = _FakeRuntime()
            runtime.lifecycle_status = lifecycle_status
            result = await server.execute_wait_for(
                runtime,
                "log_matches",
                pattern=needle,
                timeout_s=1.1,
                poll_interval_s=0.5,
            )

        # The offsets, not the clock, are what say the response came first.
        self.assertGreater(after, before)
        self.assertTrue(result["ok"])
        self.assertTrue(result["satisfied"])
        self.assertIn(needle, str(result["observed"]))

    async def test_the_public_tool_default_also_sees_the_earlier_response(self) -> None:
        """Same sequence, but through the tool an agent actually calls.

        The case above accredits the default of execute_wait_for. The registered
        tool declares its own, and a change there would put BUG-086 back with
        every case in this class still green -- which is why the ficha asks for
        the public tool with no lookback_lines argument at all.
        """

        with tempfile.TemporaryDirectory() as directory:
            profiles = self._profiles(directory)
            log = profiles / "script.log"
            log.write_text(
                "".join(f"boot-{index}\n" for index in range(5)), encoding="utf-8"
            )
            needle = f"BUG086-public-{uuid.uuid4().hex}"

            config = ServerConfig(
                mode="client",
                key="k",
                port=12345,
                client_platform="codex",
                log_sink=lambda _message: None,
            )
            runtime = _fixture_client_runtime(config)
            with patch.object(server, "ClientRuntime", return_value=runtime):
                app, _built = build_app(config)
            lifecycle = AsyncMock(return_value={"runs": [_live_run(profiles)]})
            with patch.object(runtime, "lifecycle_status", new=lifecycle):
                before, after = _append_durably(log, needle + "\n")
                result = _content_json(
                    await app.call_tool(
                        "wait_for",
                        {
                            "condition": "log_matches",
                            "pattern": needle,
                            "timeout_s": 1.1,
                            "poll_interval_s": 0.5,
                        },
                    )
                )

        self.assertGreater(after, before)
        self.assertTrue(result["satisfied"])
        self.assertIn(needle, str(result["observed"]))

    async def test_lookback_zero_reads_the_file_and_does_not_match(self) -> None:
        seen = {"calls": 0}
        with tempfile.TemporaryDirectory() as directory:
            profiles = self._profiles(directory)
            log = profiles / "script.log"
            needle = f"BUG086-control-{uuid.uuid4().hex}"
            other = f"BUG086-unrelated-{uuid.uuid4().hex}"
            log.write_text(needle + "\n", encoding="utf-8")

            async def lifecycle_status() -> dict:
                seen["calls"] += 1
                if seen["calls"] == 2:
                    # After the marker was taken at EOF and before the first
                    # probe: a durable line that does NOT carry the needle, so
                    # the probe has something to read and still cannot match.
                    _append_durably(log, other + "\n")
                return {"runs": [_live_run(profiles)]}

            runtime = _FakeRuntime()
            runtime.lifecycle_status = lifecycle_status
            result = await server.execute_wait_for(
                runtime,
                "log_matches",
                pattern=needle,
                timeout_s=1.1,
                poll_interval_s=0.5,
                lookback_from="lines",
                lookback_lines=0,
            )

        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertGreaterEqual(result["probes"], 1)
        scanned = result["scanned"]
        self.assertGreaterEqual(scanned["lines_total"], 1)
        entries = [item for item in scanned["files"] if item["lines"] >= 1]
        self.assertTrue(entries, scanned)
        self.assertTrue(all(item["readable"] for item in entries), scanned)

    async def test_same_bytes_default_sees_and_zero_misses(self) -> None:
        """Both arms of the contract on ONE file, byte-identical between calls.

        The default (200) must see a response that was durable before the
        marker; lookback_lines=0 on the very same bytes must not. Pinning the two
        verdicts on identical input attributes the flip to the parameter alone
        (fb-20260829-025502-251d: a default of 0 turns action_use -> wait_for
        into a false timeout, and a test per arm on different files cannot say
        which of the two inputs changed the verdict).
        """
        with tempfile.TemporaryDirectory() as directory:
            profiles = self._profiles(directory)
            log = profiles / "script.log"
            log.write_text("".join(f"boot-{i}\n" for i in range(5)), encoding="utf-8")
            needle = f"BUG086-arms-{uuid.uuid4().hex}"
            _append_durably(log, needle + "\n")
            digest_before = hashlib.sha256(log.read_bytes()).hexdigest()

            async def lifecycle_status() -> dict:
                return {"runs": [_live_run(profiles)]}

            def runtime() -> _FakeRuntime:
                fake = _FakeRuntime()
                fake.lifecycle_status = lifecycle_status
                return fake

            by_default = await server.execute_wait_for(
                runtime(),
                "log_matches",
                pattern=needle,
                timeout_s=1.1,
                poll_interval_s=0.5,
            )
            by_zero = await server.execute_wait_for(
                runtime(),
                "log_matches",
                pattern=needle,
                timeout_s=1.1,
                poll_interval_s=0.5,
                lookback_from="lines",
                lookback_lines=0,
            )
            digest_after = hashlib.sha256(log.read_bytes()).hexdigest()

        # Same bytes for both calls: the only thing that moved is lookback_lines.
        self.assertEqual(digest_before, digest_after)
        self.assertTrue(by_default["ok"])
        self.assertTrue(by_default["satisfied"])
        self.assertIn(needle, str(by_default["observed"]))
        self.assertTrue(by_zero["ok"])
        self.assertFalse(by_zero["satisfied"])
        self.assertTrue(by_zero["timed_out"])
        self.assertGreaterEqual(by_zero["probes"], 1)

    async def test_window_edge_is_inclusive_at_two_hundred(self) -> None:
        for label, fillers, satisfied, lines_total in _WINDOW_CASES:
            with self.subTest(label):
                with tempfile.TemporaryDirectory() as directory:
                    profiles = self._profiles(directory)
                    log = profiles / "script.log"
                    needle = f"BUG086-{label}-{uuid.uuid4().hex}"
                    body = ["guard-line\n", needle + "\n"]
                    body += [f"filler-{i:04d}\n" for i in range(fillers)]
                    log.write_text("".join(body), encoding="utf-8")
                    self.assertLess(
                        log.stat().st_size, server.log_tail.MAX_TAIL_BYTES
                    )

                    async def lifecycle_status() -> dict:
                        return {"runs": [_live_run(profiles)]}

                    runtime = _FakeRuntime()
                    runtime.lifecycle_status = lifecycle_status
                    result = await server.execute_wait_for(
                        runtime,
                        "log_matches",
                        pattern=needle,
                        timeout_s=1.1,
                        poll_interval_s=0.5,
                        lookback_from="lines",
                        lookback_lines=200,
                    )

                self.assertTrue(result["ok"])
                self.assertIs(result["satisfied"], satisfied)
                # timed_out is `not satisfied` by construction, so this is a
                # restatement, not a second measurement. Asserted because the
                # public contract promises the field, not as corroboration.
                self.assertIs(result["timed_out"], not satisfied)
                self.assertEqual(result["scanned"]["lines_total"], lines_total)
                if satisfied:
                    self.assertIn(needle, str(result["observed"]))
                else:
                    self.assertNotIn(needle, str(result["observed"]))
