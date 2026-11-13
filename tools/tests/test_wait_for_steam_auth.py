"""wait_for(players_at_least): a Steam auth kick in the server RPT aborts.

fb-20261112-163422-f09e: the PC's Steam client lost its session
("Session Replaced") and never reconnected, so on every launch the server
logged the player entering WaitAuthPlayerLoginState and, 10 s later, kicked
it there. players_at_least could only report binding_not_ready at timeout.

The fixtures are excerpts of the two real server RPTs, copied as-is: the
bad run 71bdeb16 and the good run f5095a1e (its Steam ID and DayZ player
uid already read <steamid>/<uid> there). Lines a test appends are taken
from those same fixtures, never invented with a real identifier.
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

from unittest.mock import patch

from dayz_mcp import server
from tests.wait_for_helpers import PinnedClosePolicy, _ExactWaitClock

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_BAD_RPT = _FIXTURES / "wait_for_steam_auth_bad_71bdeb16.rpt"
_GOOD_RPT = _FIXTURES / "wait_for_steam_auth_good_f5095a1e.rpt"


def _fixture_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _line_with(lines: list[str], needle: str) -> str:
    return next(line for line in lines if needle in line)


_BAD_LINES = _fixture_lines(_BAD_RPT)
_GOOD_LINES = _fixture_lines(_GOOD_RPT)
_KICK_LINE = _line_with(_BAD_LINES, "kicked from server")
_WAIT_AUTH_LINE = _line_with(_BAD_LINES, "Entering WaitAuthPlayerLoginState")
_WAIT_ASSIGNED_LINE = _line_with(
    _GOOD_LINES, "Entering WaitPlayerAssignedLoginState"
)


def _live_run(profiles: Path) -> dict:
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    return {
        "run_id": "run-a",
        "mod": "@ExampleMod",
        "profiles": str(profiles),
        "processes": [{"pid": 4242, "creation_time_utc": stamp}],
    }


class _Runtime:
    """A live run (or none) plus a bridge that always reports one count."""

    def __init__(
        self,
        profiles: Path | None,
        *,
        fallback: int = 0,
        live: bool = True,
        on_probe=None,
    ) -> None:
        self.tool_lock = asyncio.Lock()
        self.bridge_calls = 0
        self._fallback = fallback
        self._on_probe = on_probe

        async def lifecycle_status() -> dict:
            if not live or profiles is None:
                return {"runs": []}
            return {"runs": [_live_run(profiles)]}

        self.lifecycle_status = lifecycle_status

    async def call_bridge(
        self, cmd: str, args: dict, peer: str, timeout_s: float
    ) -> dict:
        self.bridge_calls += 1
        if self._on_probe is not None:
            self._on_probe(self.bridge_calls)
        return {"ok": 1, "players": [{} for _ in range(self._fallback)]}


def _server_rpt(
    directory: str,
    *,
    fixture: Path | None = None,
    extra_lines: tuple[str, ...] = (),
    text: str | None = None,
) -> tuple[Path, Path]:
    profiles = Path(directory) / "_server" / "profiles"
    profiles.mkdir(parents=True)
    rpt = profiles / "DayZDiag_x64_2026-11-12_17-30-51.RPT"
    if text is not None:
        rpt.write_text(text, encoding="utf-8")
    elif fixture is not None:
        rpt.write_bytes(fixture.read_bytes())
    if extra_lines:
        with rpt.open("a", encoding="utf-8") as handle:
            for line in extra_lines:
                handle.write(line + "\n")
    return profiles, rpt


class WaitForSteamAuthTest(PinnedClosePolicy, unittest.IsolatedAsyncioTestCase):
    async def _wait(self, runtime: _Runtime, condition: str, **kwargs) -> dict:
        clock = _ExactWaitClock()
        self.clock = clock
        with (
            patch("dayz_mcp.server.time.monotonic", clock),
            patch("dayz_mcp.server.asyncio.sleep", clock.sleep),
        ):
            return await server.execute_wait_for(
                runtime, condition, poll_interval_s=0.5, **kwargs
            )

    async def test_kick_in_wait_auth_as_last_login_line_aborts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles, _rpt = _server_rpt(directory, fixture=_BAD_RPT)
            runtime = _Runtime(profiles, fallback=0)
            with self.assertRaises(server.ToolError) as caught:
                await self._wait(
                    runtime, "players_at_least", value=1, timeout_s=5.0
                )
        message = str(caught.exception)
        self.assertTrue(
            message.startswith(
                "wait_for aborted waiting for players_at_least; "
                "reason=steam_auth_timeout; kicked="
            ),
            message,
        )
        self.assertIn(_KICK_LINE, message)
        self.assertIn("elapsed_s=", message)
        self.assertIn("timeout_s=5", message)
        self.assertEqual(
            runtime.bridge_calls,
            1,
            "the abort must end the wait, not probe again",
        )

    async def test_kick_then_wait_player_assigned_does_not_abort(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles, _rpt = _server_rpt(
                directory, fixture=_BAD_RPT, extra_lines=(_WAIT_ASSIGNED_LINE,)
            )
            runtime = _Runtime(profiles, fallback=0)
            result = await self._wait(
                runtime, "players_at_least", value=1, timeout_s=0.1
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])

    async def test_kick_then_another_wait_auth_entering_does_not_abort(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles, _rpt = _server_rpt(
                directory, fixture=_BAD_RPT, extra_lines=(_WAIT_AUTH_LINE,)
            )
            runtime = _Runtime(profiles, fallback=0)
            result = await self._wait(
                runtime, "players_at_least", value=1, timeout_s=0.1
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])

    async def test_kick_arriving_while_waiting_aborts_on_the_next_probe(
        self,
    ) -> None:
        wait_auth_at = _BAD_LINES.index(_WAIT_AUTH_LINE)
        pending = "\n".join(_BAD_LINES[: wait_auth_at + 1]) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            profiles, rpt = _server_rpt(directory, text=pending)

            def append_kick(call: int) -> None:
                if call == 2:
                    with rpt.open("a", encoding="utf-8") as handle:
                        handle.write(_KICK_LINE + "\n")

            runtime = _Runtime(profiles, fallback=0, on_probe=append_kick)
            with self.assertRaises(server.ToolError) as caught:
                await self._wait(
                    runtime, "players_at_least", value=1, timeout_s=5.0
                )
        self.assertIn("reason=steam_auth_timeout", str(caught.exception))
        self.assertIn(_KICK_LINE, str(caught.exception))
        self.assertEqual(runtime.bridge_calls, 2)

    async def test_players_at_most_with_the_kick_does_not_abort(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles, _rpt = _server_rpt(directory, fixture=_BAD_RPT)
            runtime = _Runtime(profiles, fallback=1)
            result = await self._wait(
                runtime, "players_at_most", value=0, timeout_s=0.1
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])

    async def test_no_active_run_behaves_as_before(self) -> None:
        runtime = _Runtime(None, fallback=0, live=False)
        result = await self._wait(
            runtime, "players_at_least", value=1, timeout_s=0.1
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["observed"], 0)

    async def test_the_good_runs_rpt_does_not_abort(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles, _rpt = _server_rpt(directory, fixture=_GOOD_RPT)
            runtime = _Runtime(profiles, fallback=0)
            result = await self._wait(
                runtime, "players_at_least", value=1, timeout_s=0.1
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])

    async def test_kick_of_one_dpnid_while_another_waits_does_not_abort(
        self,
    ) -> None:
        other_wait = _WAIT_AUTH_LINE.replace("14933939", "22222222")
        text = "\n".join([_WAIT_AUTH_LINE, other_wait, _KICK_LINE]) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            profiles, _rpt = _server_rpt(directory, text=text)
            runtime = _Runtime(profiles, fallback=0)
            result = await self._wait(
                runtime, "players_at_least", value=1, timeout_s=0.1
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])

    async def test_abort_when_the_other_dpnid_is_also_kicked(self) -> None:
        other_wait = _WAIT_AUTH_LINE.replace("14933939", "22222222")
        other_kick = _KICK_LINE.replace("14933939", "22222222")
        text = (
            "\n".join([_WAIT_AUTH_LINE, other_wait, _KICK_LINE, other_kick])
            + "\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            profiles, _rpt = _server_rpt(directory, text=text)
            runtime = _Runtime(profiles, fallback=0)
            with self.assertRaises(server.ToolError) as caught:
                await self._wait(
                    runtime, "players_at_least", value=1, timeout_s=5.0
                )
        self.assertIn("reason=steam_auth_timeout", str(caught.exception))
        self.assertIn(other_kick, str(caught.exception))
        self.assertEqual(runtime.bridge_calls, 1)

    async def test_wait_player_assigned_of_one_blocks_kick_of_another(
        self,
    ) -> None:
        other_wait = _WAIT_AUTH_LINE.replace("14933939", "22222222")
        other_kick = _KICK_LINE.replace("14933939", "22222222")
        assigned = _WAIT_ASSIGNED_LINE.replace("18127079", "14933939")
        text = (
            "\n".join([_WAIT_AUTH_LINE, other_wait, other_kick, assigned])
            + "\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            profiles, _rpt = _server_rpt(directory, text=text)
            runtime = _Runtime(profiles, fallback=0)
            result = await self._wait(
                runtime, "players_at_least", value=1, timeout_s=0.1
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])

    async def test_a_cut_poll_read_does_not_abort(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles, rpt = _server_rpt(directory, fixture=_BAD_RPT)

            def bury_entering(call: int) -> None:
                if call == 1:
                    with rpt.open("a", encoding="utf-8") as handle:
                        handle.write(_WAIT_AUTH_LINE + "\n")
                        handle.write("noise\n" * 60000)

            runtime = _Runtime(profiles, fallback=0, on_probe=bury_entering)
            result = await self._wait(
                runtime, "players_at_least", value=1, timeout_s=0.1
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])

    async def test_login_line_without_dpnid_does_not_abort(self) -> None:
        anonymous_entering = _WAIT_AUTH_LINE.replace("dpnid 14933939 ", "")
        text = (
            "\n".join([_WAIT_AUTH_LINE, _KICK_LINE, anonymous_entering]) + "\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            profiles, _rpt = _server_rpt(directory, text=text)
            runtime = _Runtime(profiles, fallback=0)
            result = await self._wait(
                runtime, "players_at_least", value=1, timeout_s=0.1
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])

    async def test_entering_line_without_parenthesised_fields_blocks_the_abort(
        self,
    ) -> None:
        bare_entering = (
            "[StateMachine]: Player B Entering WaitAuthPlayerLoginState"
        )
        self.assertEqual(
            server._steam_auth_login(bare_entering), ("entering", None)
        )
        self.assertEqual(
            server._steam_auth_login(_WAIT_AUTH_LINE),
            ("entering", "14933939"),
        )
        with tempfile.TemporaryDirectory() as directory:
            profiles, _rpt = _server_rpt(
                directory, fixture=_BAD_RPT, extra_lines=(bare_entering,)
            )
            runtime = _Runtime(profiles, fallback=0)
            result = await self._wait(
                runtime, "players_at_least", value=1, timeout_s=0.1
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])

    async def test_slow_log_resolution_still_probes_and_does_not_abort(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles, _rpt = _server_rpt(directory, fixture=_BAD_RPT)
            runtime = _Runtime(profiles, fallback=0)
            resolved = runtime.lifecycle_status

            async def slow_status() -> dict:
                await asyncio.sleep(2)
                return await resolved()

            runtime.lifecycle_status = slow_status
            result = await self._wait(
                runtime, "players_at_least", value=1, timeout_s=0.5
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])
        self.assertGreaterEqual(runtime.bridge_calls, 1)

    async def test_slow_initial_scan_still_probes_and_does_not_abort(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles, _rpt = _server_rpt(directory, fixture=_BAD_RPT)
            runtime = _Runtime(profiles, fallback=0)
            original_scan = server._scan_log_for_steam_auth

            def slow_scan(path: str, max_bytes: int):
                result = original_scan(path, max_bytes)
                self.clock.now += 2.0
                return result

            with patch.object(
                server, "_scan_log_for_steam_auth", side_effect=slow_scan
            ):
                result = await self._wait(
                    runtime, "players_at_least", value=1, timeout_s=0.5
                )
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])
        self.assertEqual(runtime.bridge_calls, 1)

    async def test_cut_initial_scan_past_the_deadline_still_probes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles, _rpt = _server_rpt(directory, fixture=_BAD_RPT)
            runtime = _Runtime(profiles, fallback=0)
            original_scan = server._scan_log_for_steam_auth

            def cut_slow_scan(path: str, max_bytes: int):
                result = original_scan(path, 10)
                self.clock.now += 2.0
                return result

            with patch.object(
                server, "_scan_log_for_steam_auth", side_effect=cut_slow_scan
            ):
                result = await self._wait(
                    runtime, "players_at_least", value=1, timeout_s=0.5
                )
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])
        self.assertEqual(runtime.bridge_calls, 1)

    async def test_player_named_dpnid_aborts_on_its_kick(self) -> None:
        entering = _WAIT_AUTH_LINE.replace("Player Dev ", "Player dpnid ")
        kick = _KICK_LINE.replace("Player Dev ", "Player dpnid ")
        text = "\n".join([entering, kick]) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            profiles, _rpt = _server_rpt(directory, text=text)
            runtime = _Runtime(profiles, fallback=0)
            with self.assertRaises(server.ToolError) as caught:
                await self._wait(
                    runtime, "players_at_least", value=1, timeout_s=5.0
                )
        self.assertIn("reason=steam_auth_timeout", str(caught.exception))
        self.assertIn(kick, str(caught.exception))
        self.assertEqual(runtime.bridge_calls, 1)

    async def test_player_name_with_digits_and_parentheses_aborts(
        self,
    ) -> None:
        entering = _WAIT_AUTH_LINE.replace(
            "Player Dev ", "Player Unit 7 (42) dpnid "
        )
        kick = _KICK_LINE.replace(
            "Player Dev ", "Player Unit 7 (42) dpnid "
        )
        text = "\n".join([entering, kick]) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            profiles, _rpt = _server_rpt(directory, text=text)
            runtime = _Runtime(profiles, fallback=0)
            with self.assertRaises(server.ToolError) as caught:
                await self._wait(
                    runtime, "players_at_least", value=1, timeout_s=5.0
                )
        self.assertIn("reason=steam_auth_timeout", str(caught.exception))
        self.assertIn(kick, str(caught.exception))
        self.assertEqual(runtime.bridge_calls, 1)

    async def test_player_named_like_the_entering_field_aborts(
        self,
    ) -> None:
        entering = _WAIT_AUTH_LINE.replace(
            "Player Dev ", "Player (dpnid 2 uid ) Entering "
        )
        text = "\n".join([entering, _KICK_LINE]) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            profiles, _rpt = _server_rpt(directory, text=text)
            runtime = _Runtime(profiles, fallback=0)
            with self.assertRaises(server.ToolError) as caught:
                await self._wait(
                    runtime, "players_at_least", value=1, timeout_s=5.0
                )
        self.assertIn("reason=steam_auth_timeout", str(caught.exception))
        self.assertIn(_KICK_LINE, str(caught.exception))
        self.assertEqual(runtime.bridge_calls, 1)

    async def test_player_named_like_the_kick_field_aborts(self) -> None:
        kick = _KICK_LINE.replace(
            "Player Dev ", "Player (2) kicked from server "
        )
        text = "\n".join([_WAIT_AUTH_LINE, kick]) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            profiles, _rpt = _server_rpt(directory, text=text)
            runtime = _Runtime(profiles, fallback=0)
            with self.assertRaises(server.ToolError) as caught:
                await self._wait(
                    runtime, "players_at_least", value=1, timeout_s=5.0
                )
        self.assertIn("reason=steam_auth_timeout", str(caught.exception))
        self.assertIn(kick, str(caught.exception))
        self.assertEqual(runtime.bridge_calls, 1)

    async def test_player_named_kicked_from_server_aborts_on_real_kick(
        self,
    ) -> None:
        lines = [
            line.replace("Player Dev ", "Player kicked from server ")
            if (" Entering " in line or "kicked from server" in line)
            else line
            for line in _BAD_LINES
        ]
        kick = _KICK_LINE.replace(
            "Player Dev ", "Player kicked from server "
        )
        text = "\n".join(lines) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            profiles, _rpt = _server_rpt(directory, text=text)
            runtime = _Runtime(profiles, fallback=0)
            with self.assertRaises(server.ToolError) as caught:
                await self._wait(
                    runtime, "players_at_least", value=1, timeout_s=5.0
                )
        self.assertIn("reason=steam_auth_timeout", str(caught.exception))
        self.assertIn(kick, str(caught.exception))
        self.assertEqual(runtime.bridge_calls, 1)

    async def test_player_name_with_dpnid_group_cannot_hide_real_dpnid(
        self,
    ) -> None:
        lines = [
            line.replace("Player Dev ", "Player (dpnid 2 uid ")
            if (" Entering " in line or "kicked from server" in line)
            else line
            for line in _BAD_LINES
        ]
        kick = _KICK_LINE.replace("Player Dev ", "Player (dpnid 2 uid ")
        text = "\n".join(lines) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            profiles, _rpt = _server_rpt(directory, text=text)
            runtime = _Runtime(profiles, fallback=0)
            with self.assertRaises(server.ToolError) as caught:
                await self._wait(
                    runtime, "players_at_least", value=1, timeout_s=5.0
                )
        self.assertIn("reason=steam_auth_timeout", str(caught.exception))
        self.assertIn(kick, str(caught.exception))
        self.assertEqual(runtime.bridge_calls, 1)

    async def test_kick_with_empty_dpnid_blocks_abort(self) -> None:
        empty_kick = (
            "Player B () kicked from server: 49 "
            "(Timeout (WaitAuthPlayerLoginState))"
        )
        self.assertEqual(
            server._steam_auth_login(_KICK_LINE), ("kick", "14933939")
        )
        self.assertEqual(server._steam_auth_login(empty_kick), ("kick", None))
        with tempfile.TemporaryDirectory() as directory:
            profiles, _rpt = _server_rpt(
                directory, fixture=_BAD_RPT, extra_lines=(empty_kick,)
            )
            runtime = _Runtime(profiles, fallback=0)
            result = await self._wait(
                runtime, "players_at_least", value=1, timeout_s=0.1
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])

    async def test_unreadable_initial_scan_switches_the_check_off(
        self,
    ) -> None:
        other_wait = _WAIT_AUTH_LINE.replace("14933939", "22222222")
        text = "\n".join([_WAIT_AUTH_LINE, other_wait]) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            profiles, rpt = _server_rpt(directory, text=text)

            def append_kick(call: int) -> None:
                if call == 1:
                    with rpt.open("a", encoding="utf-8") as handle:
                        handle.write(_KICK_LINE + "\n")

            runtime = _Runtime(profiles, fallback=0, on_probe=append_kick)
            with patch.object(
                server,
                "_scan_log_for_steam_auth",
                side_effect=server.log_tail.LogTailError("log_unavailable"),
            ):
                result = await self._wait(
                    runtime, "players_at_least", value=1, timeout_s=0.1
                )
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])

    async def test_unreadable_incremental_read_switches_the_check_off(
        self,
    ) -> None:
        other_wait = _WAIT_AUTH_LINE.replace("14933939", "22222222")
        text = "\n".join([_WAIT_AUTH_LINE, _KICK_LINE]) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            profiles, rpt = _server_rpt(directory, text=text)

            def append_entering(call: int) -> None:
                if call == 1:
                    with rpt.open("a", encoding="utf-8") as handle:
                        handle.write(other_wait + "\n")

            runtime = _Runtime(profiles, fallback=0, on_probe=append_entering)
            original_read_since = server.log_tail.read_since

            def failing_incremental(path, marker, *args, **kwargs):
                # Only the steam-auth incremental read (the one carrying
                # a marker) fails; the marker read at the start, which
                # carries none, and every other reader keep working.
                if marker is not None:
                    raise server.log_tail.LogTailError("log_unavailable")
                return original_read_since(path, marker, *args, **kwargs)

            with patch.object(
                server.log_tail, "read_since", side_effect=failing_incremental
            ):
                result = await self._wait(
                    runtime, "players_at_least", value=1, timeout_s=0.1
                )
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])

    async def test_missing_steam_auth_marker_switches_the_check_off(
        self,
    ) -> None:
        other_wait = _WAIT_AUTH_LINE.replace("14933939", "22222222")
        text = "\n".join([_WAIT_AUTH_LINE, _KICK_LINE]) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            profiles, rpt = _server_rpt(directory, text=text)

            def append_entering(call: int) -> None:
                # B's Entering reaches the file during the first probe
                # but its newline only on the second, so a re-read from
                # byte 0 -- the fallback a missing marker would leave --
                # cannot return it as a line on the first probe. Only
                # switching the check off at the missing marker keeps
                # that probe from aborting on A's kick alone.
                if call == 1:
                    with rpt.open("a", encoding="utf-8") as handle:
                        handle.write(other_wait)
                elif call == 2:
                    with rpt.open("a", encoding="utf-8") as handle:
                        handle.write("\n")

            runtime = _Runtime(profiles, fallback=0, on_probe=append_entering)
            with patch.object(server, "_log_markers_at_end", return_value={}):
                result = await self._wait(
                    runtime, "players_at_least", value=1, timeout_s=5.0
                )
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])
        self.assertGreaterEqual(runtime.bridge_calls, 2)

    async def test_unterminated_tail_switches_the_check_off(self) -> None:
        other_wait = _WAIT_AUTH_LINE.replace("14933939", "22222222")
        text = "\n".join([_WAIT_AUTH_LINE, _KICK_LINE]) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            profiles, rpt = _server_rpt(directory, text=text)

            def append_unterminated(call: int) -> None:
                # B's Entering reaches the file during the first probe
                # but its line feed only on the second, with the real
                # markers and readers: log_tail withholds the partial
                # line, so that probe must not abort on A's kick alone,
                # and completing the line later must not abort either.
                if call == 1:
                    with rpt.open("a", encoding="utf-8") as handle:
                        handle.write(other_wait)
                elif call == 2:
                    with rpt.open("a", encoding="utf-8") as handle:
                        handle.write("\n")

            runtime = _Runtime(
                profiles, fallback=0, on_probe=append_unterminated
            )
            result = await self._wait(
                runtime, "players_at_least", value=1, timeout_s=5.0
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])
        self.assertGreaterEqual(runtime.bridge_calls, 2)

    async def test_append_between_size_stat_and_read_switches_the_check_off(
        self,
    ) -> None:
        other_wait = _WAIT_AUTH_LINE.replace("14933939", "22222222")
        text = "\n".join([_WAIT_AUTH_LINE, _KICK_LINE]) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            profiles, rpt = _server_rpt(directory, text=text)
            original_read_since = server.log_tail.read_since
            appended = False

            def interleaved_read_since(path, marker, *args, **kwargs):
                # Only the steam-auth incremental read carries a marker;
                # every other reader (the start markers, with none) is
                # left alone. The real read runs first, so its marker
                # keeps the size from before the append, and B's Entering
                # without its line feed lands after that size was taken
                # and the bytes were read, before the result returns.
                nonlocal appended
                result = original_read_since(path, marker, *args, **kwargs)
                if marker is not None and not appended:
                    appended = True
                    with rpt.open("a", encoding="utf-8") as handle:
                        handle.write(other_wait)
                return result

            runtime = _Runtime(profiles, fallback=0)
            with patch.object(
                server.log_tail, "read_since", side_effect=interleaved_read_since
            ):
                result = await self._wait(
                    runtime, "players_at_least", value=1, timeout_s=5.0
                )
        self.assertTrue(appended)
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])
        self.assertGreaterEqual(runtime.bridge_calls, 2)

    def test_scan_reports_an_unterminated_final_fragment_as_incomplete(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rpt = Path(directory) / "DayZDiag_x64_2026-11-12_17-30-51.RPT"
            body = (_WAIT_AUTH_LINE + "\n" + _KICK_LINE).encode("utf-8")
            rpt.write_bytes(body)
            _state, _kick, _unknown, incomplete = (
                server._scan_log_for_steam_auth(
                    str(rpt), server.WAIT_FOR_LAUNCH_SCAN_MAX_BYTES
                )
            )
            self.assertTrue(incomplete)
            rpt.write_bytes(body + b"\n")
            _state, _kick, _unknown, incomplete = (
                server._scan_log_for_steam_auth(
                    str(rpt), server.WAIT_FOR_LAUNCH_SCAN_MAX_BYTES
                )
            )
            self.assertFalse(incomplete)
            rpt.write_bytes(b"")
            _state, _kick, _unknown, incomplete = (
                server._scan_log_for_steam_auth(
                    str(rpt), server.WAIT_FOR_LAUNCH_SCAN_MAX_BYTES
                )
            )
            self.assertFalse(incomplete)

    async def test_player_name_spelling_an_entering_field_cannot_hide_an_unidentified_login(
        self,
    ) -> None:
        self.assertEqual(
            server._steam_auth_login(_WAIT_AUTH_LINE),
            ("entering", "14933939"),
        )
        self.assertEqual(
            server._steam_auth_login(_KICK_LINE), ("kick", "14933939")
        )
        entering = (
            "[StateMachine]: Player (dpnid 1 uid ) Entering B (uid ) "
            "Entering WaitAuthPlayerLoginState"
        )
        kick = (
            "Player A (1) kicked from server: 49 "
            "(Timeout (WaitAuthPlayerLoginState))"
        )
        self.assertEqual(
            server._steam_auth_login(entering), ("entering", None)
        )
        text = "\n".join([entering, kick]) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            profiles, _rpt = _server_rpt(directory, text=text)
            runtime = _Runtime(profiles, fallback=0)
            result = await self._wait(
                runtime, "players_at_least", value=1, timeout_s=0.1
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])

    def test_the_tool_description_names_the_new_abort(self) -> None:
        app, _runtime = server.build_app(
            server.ServerConfig(log_sink=lambda _message: None)
        )
        description = app._tool_manager.get_tool("wait_for").description or ""
        self.assertIn("steam_auth_timeout", description)
        self.assertIn("WaitAuthPlayerLoginState", description)
        self.assertIn("Session Replaced", description)
        self.assertIn("Steam client is signed in", description)


if __name__ == "__main__":
    unittest.main()
