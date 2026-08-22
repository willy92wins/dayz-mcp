"""wait_for(log_matches): the substring contract, the launch scan, the scan report.

Three defects measured against real DayZ logs on 2026-08-21, each of them silent:

1. `pattern` is `pattern in line`. Nothing said so, so two sessions independently
   passed a regex-escaped pattern and read the resulting no-match as "the mod
   printed nothing". Replayed against the real file: r"\\[DayZ-MCP\\]" -> 0 hits,
   "[DayZ-MCP]" -> 1 hit, same file, same line.

2. `lookback_lines` tops out at 2000 and the line that call was waiting for sat
   at line 20 of a 132,632-line script log -- and was absent from that launch's
   RPT (0 hits in 165,669 lines, because the RPT starts mirroring SCRIPT output
   about 16 s into the launch). Waiting on a startup line could not work at any
   legal value, and `README-mcp.md` recommends exactly such a needle.

3. `observed` carries `lines[-1]`, the last line of the newest file. When the RPT
   sorts newest, a caller sees only RPT lines and concludes the script log is
   never opened. It is opened; nothing in it matched.

Each test below fails if its fix is reverted, which is the only reason to keep
them: a test that stays green either way pins nothing.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import log_tail, server


def _live_process(pid: int = 4242) -> dict:
    """A process stamp recent enough that files written in the test pass the floor."""

    stamp = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    return {"pid": pid, "creation_time_utc": stamp}


class _FakeRuntime:
    """Only what execute_wait_for touches: a lock and a lifecycle snapshot."""

    def __init__(
        self, profiles: Path, runs: list[dict] | None = None
    ) -> None:
        self.tool_lock = asyncio.Lock()
        snapshot = (
            list(runs)
            if runs is not None
            else [
                {
                    "run_id": "run-a",
                    "profiles": str(profiles),
                    "processes": [_live_process()],
                }
            ]
        )

        async def lifecycle_status() -> dict:
            return {"runs": snapshot}

        self.lifecycle_status = lifecycle_status

    async def call_bridge(self, cmd: str, args: dict, peer: str, timeout_s: float) -> dict:
        raise AssertionError(f"log_matches must not call the bridge (got {cmd})")


def _profiles(directory: str) -> Path:
    profiles = Path(directory) / "_server" / "profiles"
    profiles.mkdir(parents=True)
    return profiles


class PatternIsASubstringTest(unittest.IsolatedAsyncioTestCase):
    """The contract is substring matching, and it has to be stated somewhere."""

    async def test_regex_escaped_pattern_does_not_match_the_plain_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles = _profiles(directory)
            (profiles / "script.log").write_text(
                "SCRIPT : [DayZ-MCP] config loaded poll_hz=5\n", encoding="utf-8"
            )
            runtime = _FakeRuntime(profiles)
            escaped = await server.execute_wait_for(
                runtime,
                "log_matches",
                pattern=r"\[DayZ-MCP\]",
                timeout_s=0.6,
                poll_interval_s=0.5,
                lookback_from="launch",
            )
            plain = await server.execute_wait_for(
                runtime,
                "log_matches",
                pattern="[DayZ-MCP]",
                timeout_s=0.6,
                poll_interval_s=0.5,
                lookback_from="launch",
            )

        self.assertFalse(
            escaped["satisfied"],
            "a regex-escaped pattern must NOT match -- if this starts passing, the "
            "matcher became a regex and every caller's literal '[' now means a "
            "character class",
        )
        self.assertTrue(plain["satisfied"])
        self.assertIn("[DayZ-MCP]", str(plain["observed"]))
        self.assertEqual(escaped["scanned"]["pattern_kind"], "substring")

    def test_the_tool_description_names_the_contract(self) -> None:
        """A caller who reads only the description must not guess wrong."""
        app, _runtime = server.build_app(
            server.ServerConfig(log_sink=lambda _message: None)
        )
        description = app._tool_manager.get_tool("wait_for").description or ""
        self.assertIn("SUBSTRING", description)
        self.assertIn(r"\[MOD\]", description)
        self.assertIn("lookback_from", description)


class LaunchScanReachesTheStartOfTheLaunchTest(unittest.IsolatedAsyncioTestCase):
    """The pairing that matters: unreachable by lines, reachable from the launch."""

    NEEDLE = "MCP-BOOT-NEEDLE"

    def _write_log(self, profiles: Path, filler_lines: int) -> None:
        body = [f"SCRIPT : {self.NEEDLE} config loaded\n"]
        body.extend(f"SCRIPT : filler {index}\n" for index in range(filler_lines))
        (profiles / "script.log").write_text("".join(body), encoding="utf-8")

    async def _wait(self, profiles: Path, **kwargs: object) -> dict:
        return await server.execute_wait_for(
            _FakeRuntime(profiles),
            "log_matches",
            pattern=self.NEEDLE,
            timeout_s=0.6,
            poll_interval_s=0.5,
            **kwargs,
        )

    async def test_max_lookback_cannot_reach_it_but_launch_can(self) -> None:
        beyond = server.WAIT_FOR_LOOKBACK_MAX + 500
        with tempfile.TemporaryDirectory() as directory:
            profiles = _profiles(directory)
            self._write_log(profiles, filler_lines=beyond)
            by_lines = await self._wait(
                profiles, lookback_lines=server.WAIT_FOR_LOOKBACK_MAX
            )
            by_launch = await self._wait(profiles, lookback_from="launch")

        self.assertFalse(
            by_lines["satisfied"],
            "the largest legal lookback must still miss a line this far back -- "
            "that is the defect lookback_from=launch exists to answer",
        )
        self.assertTrue(by_launch["satisfied"])
        self.assertIn(self.NEEDLE, str(by_launch["observed"]))
        self.assertEqual(by_launch["scanned"]["lookback_from"], "launch")
        self.assertFalse(by_launch["scanned"]["scan_truncated"])

    async def test_crlf_lines_match_the_same_as_lf(self) -> None:
        """DayZ writes CRLF; a needle at end of line must not carry the CR."""
        with tempfile.TemporaryDirectory() as directory:
            profiles = _profiles(directory)
            (profiles / "script.log").write_bytes(
                b"SCRIPT : noise\r\nSCRIPT : ends with " + self.NEEDLE.encode() + b"\r\n"
            )
            result = await self._wait(profiles, lookback_from="launch")

        self.assertTrue(result["satisfied"])
        self.assertNotIn("\r", str(result["observed"]))

    async def test_a_line_written_after_the_scan_is_still_caught(self) -> None:
        """Markers are taken before the scan, so the tail resumes at the end."""
        with tempfile.TemporaryDirectory() as directory:
            profiles = _profiles(directory)
            log = profiles / "script.log"
            log.write_text("SCRIPT : nothing yet\n", encoding="utf-8")

            async def append_late() -> None:
                await asyncio.sleep(0.3)
                with log.open("a", encoding="utf-8") as handle:
                    handle.write(f"SCRIPT : {self.NEEDLE} arrived late\n")

            task = asyncio.create_task(append_late())
            result = await server.execute_wait_for(
                _FakeRuntime(profiles),
                "log_matches",
                pattern=self.NEEDLE,
                timeout_s=4.0,
                poll_interval_s=0.5,
                lookback_from="launch",
            )
            await task

        self.assertTrue(result["satisfied"])
        self.assertIn(self.NEEDLE, str(result["observed"]))


class LaunchScanIsBoundedTest(unittest.IsolatedAsyncioTestCase):
    """The scan runs under tool_lock, so it is capped -- and says when it capped."""

    async def test_a_file_past_the_ceiling_reports_scan_truncated(self) -> None:
        original = server.WAIT_FOR_LAUNCH_SCAN_MAX_BYTES
        server.WAIT_FOR_LAUNCH_SCAN_MAX_BYTES = 64
        try:
            with tempfile.TemporaryDirectory() as directory:
                profiles = _profiles(directory)
                body = "SCRIPT : filler line that is comfortably wide\n" * 20
                (profiles / "script.log").write_text(
                    body + "SCRIPT : LATE-NEEDLE\n", encoding="utf-8"
                )
                result = await server.execute_wait_for(
                    _FakeRuntime(profiles),
                    "log_matches",
                    pattern="LATE-NEEDLE",
                    timeout_s=0.6,
                    poll_interval_s=0.5,
                    lookback_from="launch",
                )
        finally:
            server.WAIT_FOR_LAUNCH_SCAN_MAX_BYTES = original

        self.assertFalse(result["satisfied"])
        self.assertTrue(
            result["scanned"]["scan_truncated"],
            "a capped scan that reports nothing looks identical to a complete scan "
            "that found nothing; the flag is the whole difference",
        )

    async def test_a_file_inside_the_ceiling_is_not_called_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles = _profiles(directory)
            (profiles / "script.log").write_text("SCRIPT : HIT\n", encoding="utf-8")
            result = await server.execute_wait_for(
                _FakeRuntime(profiles),
                "log_matches",
                pattern="HIT",
                timeout_s=0.6,
                poll_interval_s=0.5,
                lookback_from="launch",
            )

        self.assertTrue(result["satisfied"])
        self.assertFalse(result["scanned"]["scan_truncated"])


class ScannedReportTest(unittest.IsolatedAsyncioTestCase):
    """A no-match has to be visible as a no-match, per file."""

    async def test_every_file_read_is_named_with_its_line_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles = _profiles(directory)
            (profiles / "script.log").write_text(
                "SCRIPT : one\nSCRIPT : two\n", encoding="utf-8"
            )
            (profiles / "DayZDiag_x64.RPT").write_text(
                "00:00:01 NETWORK : hello\n", encoding="utf-8"
            )
            result = await server.execute_wait_for(
                _FakeRuntime(profiles),
                "log_matches",
                pattern="ABSENT-ON-PURPOSE",
                timeout_s=0.6,
                poll_interval_s=0.5,
                lookback_from="launch",
            )

        scanned = result["scanned"]
        names = {entry["name"] for entry in scanned["files"]}
        self.assertEqual(names, {"_server/script.log", "_server/DayZDiag_x64.RPT"})
        self.assertTrue(all(entry["readable"] for entry in scanned["files"]))
        self.assertEqual(
            scanned["lines_total"],
            sum(entry["lines"] for entry in scanned["files"]),
        )
        self.assertGreater(scanned["lines_total"], 0)

    async def test_names_carry_no_host_path(self) -> None:
        """This crosses the MCP wire; a profiles path names the machine's user."""
        with tempfile.TemporaryDirectory() as directory:
            profiles = _profiles(directory)
            (profiles / "script.log").write_text("SCRIPT : x\n", encoding="utf-8")
            result = await server.execute_wait_for(
                _FakeRuntime(profiles),
                "log_matches",
                pattern="ABSENT-ON-PURPOSE",
                timeout_s=0.6,
                poll_interval_s=0.5,
            )

        for entry in result["scanned"]["files"]:
            self.assertNotIn(":", entry["name"], entry["name"])
            self.assertNotIn("\\", entry["name"], entry["name"])
            self.assertNotIn(str(Path(directory).name), entry["name"], entry["name"])

    async def test_players_conditions_carry_no_scan_report(self) -> None:
        """scanned describes log reading; a player probe reads no logs."""

        class _PlayerRuntime:
            def __init__(self) -> None:
                self.tool_lock = asyncio.Lock()
                self.lifecycle_status = None

            async def call_bridge(self, cmd, args, peer, timeout_s):  # noqa: ANN001
                return {"ok": 1, "players": [{}]}

        result = await server.execute_wait_for(
            _PlayerRuntime(), "players_at_least", value=1, timeout_s=1.0
        )
        self.assertTrue(result["satisfied"])
        self.assertNotIn("scanned", result)

    def test_an_unreadable_file_is_reported_as_unreadable(self) -> None:
        """`_new_log_lines` omits a file it could not open; the report says so.

        Driven through the helpers rather than a live wait: making a real file
        unopenable mid-probe is platform-specific, and the behaviour under test
        is the bookkeeping, not the OS error.
        """
        good, bad = r"X:\_server\profiles\script.log", r"X:\_server\profiles\dead.RPT"
        seen: list[str] = []
        totals: dict[str, int] = {}
        unreadable: set[str] = set()

        server._record_scan([good, bad], {good: 7}, seen, totals, unreadable)
        report = server._scanned_report(seen, totals, unreadable, "lines", False)

        by_name = {entry["name"]: entry for entry in report["files"]}
        self.assertTrue(by_name["_server/script.log"]["readable"])
        self.assertEqual(by_name["_server/script.log"]["lines"], 7)
        self.assertFalse(by_name["_server/dead.RPT"]["readable"])
        self.assertEqual(by_name["_server/dead.RPT"]["lines"], 0)

    def test_counts_accumulate_across_probes(self) -> None:
        path = r"X:\_server\profiles\script.log"
        seen: list[str] = []
        totals: dict[str, int] = {}
        unreadable: set[str] = set()

        server._record_scan([path], {path: 3}, seen, totals, unreadable)
        server._record_scan([path], {path: 4}, seen, totals, unreadable)
        report = server._scanned_report(seen, totals, unreadable, "lines", False)

        self.assertEqual(report["files"][0]["lines"], 7)
        self.assertEqual(len(report["files"]), 1, "a path must be listed once")

    def test_a_file_readable_again_stops_being_reported_unreadable(self) -> None:
        path = r"X:\_server\profiles\script.log"
        seen: list[str] = []
        totals: dict[str, int] = {}
        unreadable: set[str] = set()

        server._record_scan([path], {}, seen, totals, unreadable)
        self.assertIn(path, unreadable)
        server._record_scan([path], {path: 2}, seen, totals, unreadable)
        self.assertNotIn(path, unreadable)


class ScanHelperTest(unittest.TestCase):
    """`_scan_log_for_pattern` is the piece `read_since` structurally cannot be."""

    def test_it_finds_the_first_line_not_the_last(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "script.log"
            log.write_text("first HIT a\nnoise\nsecond HIT b\n", encoding="utf-8")
            line, scanned, truncated = server._scan_log_for_pattern(
                str(log), "HIT", server.WAIT_FOR_LAUNCH_SCAN_MAX_BYTES
            )
        self.assertEqual(line, "first HIT a")
        self.assertEqual(scanned, 1, "it must stop at the first hit")
        self.assertFalse(truncated)

    def test_it_counts_every_line_when_nothing_matches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "script.log"
            log.write_text("a\nb\nc\n", encoding="utf-8")
            line, scanned, truncated = server._scan_log_for_pattern(
                str(log), "ABSENT", server.WAIT_FOR_LAUNCH_SCAN_MAX_BYTES
            )
        self.assertIsNone(line)
        self.assertEqual(scanned, 3)
        self.assertFalse(truncated)

    def test_a_final_line_without_a_newline_still_matches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "script.log"
            log.write_text("a\nTAIL-HIT", encoding="utf-8")
            line, scanned, _ = server._scan_log_for_pattern(
                str(log), "TAIL-HIT", server.WAIT_FOR_LAUNCH_SCAN_MAX_BYTES
            )
        self.assertEqual(line, "TAIL-HIT")
        self.assertEqual(scanned, 2)

    def test_a_hit_spanning_a_chunk_boundary_is_found(self) -> None:
        """The carry buffer exists for this; a small chunk size proves it works."""
        original = server._LAUNCH_SCAN_CHUNK_BYTES
        server._LAUNCH_SCAN_CHUNK_BYTES = 8
        try:
            with tempfile.TemporaryDirectory() as directory:
                log = Path(directory) / "script.log"
                log.write_text("x" * 40 + " SPANNING-HIT here\n", encoding="utf-8")
                line, _, _ = server._scan_log_for_pattern(
                    str(log), "SPANNING-HIT", server.WAIT_FOR_LAUNCH_SCAN_MAX_BYTES
                )
        finally:
            server._LAUNCH_SCAN_CHUNK_BYTES = original
        self.assertIsNotNone(line)
        self.assertIn("SPANNING-HIT", str(line))

    def test_a_missing_file_raises_the_same_error_read_since_raises(self) -> None:
        with self.assertRaises(log_tail.LogTailError):
            server._scan_log_for_pattern(
                r"X:\nope\_server\profiles\gone.log",
                "x",
                server.WAIT_FOR_LAUNCH_SCAN_MAX_BYTES,
            )

    def test_exact_ceiling_is_not_called_truncated(self) -> None:
        """Truncated means bytes were left, measured -- not 'we hit the budget'."""
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "script.log"
            payload = b"abcdefgh\n"
            log.write_bytes(payload)  # not write_text: it would translate to CRLF
            _, _, truncated = server._scan_log_for_pattern(
                str(log), "ABSENT", len(payload)
            )
        self.assertFalse(truncated)


class LookbackFromArgumentTest(unittest.IsolatedAsyncioTestCase):
    async def test_an_unknown_lookback_from_is_bad_args_naming_the_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles = _profiles(directory)
            (profiles / "script.log").write_text("x\n", encoding="utf-8")
            with self.assertRaises(server.ToolError) as caught:
                await server.execute_wait_for(
                    _FakeRuntime(profiles),
                    "log_matches",
                    pattern="x",
                    timeout_s=0.6,
                    lookback_from="whole-file",
                )
        message = str(caught.exception)
        self.assertIn("bad_args", message)
        self.assertIn("lookback_from", message)

    async def test_the_default_still_tails_from_the_end(self) -> None:
        """Default behaviour is unchanged: a new parameter must not move it."""
        with tempfile.TemporaryDirectory() as directory:
            profiles = _profiles(directory)
            (profiles / "script.log").write_text("OLD-LINE\n", encoding="utf-8")
            result = await server.execute_wait_for(
                _FakeRuntime(profiles),
                "log_matches",
                pattern="OLD-LINE",
                timeout_s=0.6,
                poll_interval_s=0.5,
                lookback_lines=0,
            )
        self.assertFalse(result["satisfied"])
        self.assertEqual(result["scanned"]["lookback_from"], "lines")
        self.assertNotIn("scan_truncated", result["scanned"])


if __name__ == "__main__":
    unittest.main()
