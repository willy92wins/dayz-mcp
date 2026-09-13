"""Caller-facing server responses must distinguish dispatch from confirmed state."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

from dayz_mcp import control_client, server
from dayz_mcp.server import ServerConfig, build_app
from tests._addon_paths import addon_root
from tests.test_mcp_tools import _content_json


RETAIL_QUARANTINE_RECIPE = (
    "retail_quarantine: a DayZ retail process is running on this machine; "
    "mutations are blocked until no DayZ retail process is running"
)


async def _call_tool_with_bridge_result(
    tool_name: str,
    arguments: dict[str, Any],
    bridge_result: dict[str, Any],
) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any], str, float]]]:
    app, runtime = build_app(ServerConfig(log_sink=lambda _message: None))
    calls: list[tuple[str, dict[str, Any], str, float]] = []

    async def call_bridge(
        command: str,
        args: dict[str, Any],
        peer: str,
        timeout_s: float,
    ) -> dict[str, Any]:
        calls.append((command, args, peer, timeout_s))
        return dict(bridge_result)

    runtime.call_bridge = call_bridge  # type: ignore[method-assign]
    result = _content_json(await app.call_tool(tool_name, arguments))
    return result, calls


class RetailQuarantineErrorTest(unittest.IsolatedAsyncioTestCase):
    def test_remote_error_code_preserves_retail_quarantine(self) -> None:
        self.assertEqual(
            server._remote_error_code({"error": "retail_quarantine"}),
            "retail_quarantine",
        )

    def test_enqueue_error_explains_how_retail_quarantine_clears(self) -> None:
        message = server._public_enqueue_error({"error": "retail_quarantine"})
        self.assertEqual(message, RETAIL_QUARANTINE_RECIPE)
        self.assertIn("retail_quarantine", message)
        self.assertIn("DayZ retail", message)
        self.assertIn("mutations are blocked", message)

    async def test_control_error_uses_the_retail_quarantine_recipe(self) -> None:
        runtime = object.__new__(server.ClientRuntime)

        async def rejected() -> dict[str, object]:
            raise control_client.ControlClientError(
                "retail_quarantine",
                request_stage="post_request",
                http_bytes_sent=1,
            )

        with self.assertRaises(server.ToolError) as raised:
            await runtime._control_with_lazy_spawn(rejected)

        self.assertEqual(str(raised.exception), RETAIL_QUARANTINE_RECIPE)


class VersionBlockedNoRunTest(unittest.TestCase):
    @staticmethod
    def _version_blocked() -> dict[str, object]:
        return {
            "error": "version_blocked",
            "got": None,
            "expected": "8",
        }

    def test_both_unpolled_peers_report_no_run(self) -> None:
        status = {
            "server_peer": {"last_poll_age_s": None, "version_state": "legacy"},
            "client_peer": {"last_poll_age_s": None, "version_state": "legacy"},
        }

        message = server._public_enqueue_error(
            self._version_blocked(),
            status_snapshot=status,
        )

        self.assertEqual(message, "game_not_ready:reason=no_run")

    def test_live_target_peer_keeps_version_mismatch_details(self) -> None:
        status = {
            "server_peer": {"last_poll_age_s": 0.1, "version_state": "version_mismatch"},
            "client_peer": {"last_poll_age_s": None, "version_state": "legacy"},
        }

        message = server._public_enqueue_error(
            self._version_blocked(),
            status_snapshot=status,
            peer="server",
        )

        self.assertEqual(message, "version_blocked:bridge None != '8'")


class EngineSetResponseTest(unittest.IsolatedAsyncioTestCase):
    async def test_matching_engine_readback_confirms_state_and_preserves_fields(self) -> None:
        result, calls = await _call_tool_with_bridge_result(
            "engine_set",
            {"mode": "start"},
            {"ok": 1, "engine_on_server": 1, "sent": 1},
        )

        self.assertIs(result.get("command_sent"), True)
        self.assertIs(result.get("state_confirmed"), True)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(calls[0][0:3], ("engine_set", {"mode": "start"}, "client"))

    async def test_mismatched_engine_readback_rejects_state_confirmation(self) -> None:
        result, _calls = await _call_tool_with_bridge_result(
            "engine_set",
            {"mode": "stop"},
            {"ok": 1, "engine_on_server": 1},
        )

        self.assertIs(result.get("command_sent"), True)
        self.assertIs(result.get("state_confirmed"), False)

    async def test_missing_engine_readback_is_accepted_but_not_confirmed(self) -> None:
        result, _calls = await _call_tool_with_bridge_result(
            "engine_set",
            {"mode": "start"},
            {"ok": 1},
        )

        self.assertIs(result.get("command_sent"), True)
        self.assertIn("state_confirmed", result)
        self.assertIsNone(result.get("state_confirmed"))


class WorldTimeSetResponseTest(unittest.IsolatedAsyncioTestCase):
    async def test_date_match_and_multiplier_mismatch_are_independent(self) -> None:
        result, calls = await _call_tool_with_bridge_result(
            "world_time_set",
            {
                "year": 2026,
                "month": 8,
                "day": 23,
                "hour": 14,
                "minute": 30,
                "time_multiplier": 4.0,
            },
            {
                "ok": 1,
                "applied": {
                    "year": 2026,
                    "month": 8,
                    "day": 23,
                    "hour": 14,
                    "minute": 30,
                    "time_multiplier": 3.0,
                },
                "sent": 1,
            },
        )

        self.assertIs(result.get("date_applied"), True)
        self.assertIs(result.get("multiplier_applied"), False)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(result.get("ok"), 0)
        self.assertEqual(result.get("warnings"), ["multiplier_mismatch"])
        self.assertEqual(calls[0][0], "world_time_set")

    async def test_matching_multiplier_is_confirmed_when_readback_exists(self) -> None:
        result, _calls = await _call_tool_with_bridge_result(
            "world_time_set",
            {
                "year": 2026,
                "month": 8,
                "day": 23,
                "hour": 14,
                "minute": 30,
                "time_multiplier": 4.0,
            },
            {
                "ok": 1,
                "applied": {
                    "year": 2026,
                    "month": 8,
                    "day": 23,
                    "hour": 14,
                    "minute": 30,
                    "time_multiplier": 4.0,
                },
            },
        )

        self.assertIs(result.get("date_applied"), True)
        self.assertIs(result.get("multiplier_applied"), True)

    async def test_requested_multiplier_without_readback_is_unconfirmed(self) -> None:
        requested, _calls = await _call_tool_with_bridge_result(
            "world_time_set",
            {
                "year": 2026,
                "month": 8,
                "day": 23,
                "hour": 14,
                "minute": 30,
                "time_multiplier": 4.0,
            },
            {
                "ok": 1,
                "applied": {
                    "year": 2026,
                    "month": 8,
                    "day": 23,
                    "hour": 14,
                    "minute": 30,
                },
            },
        )
        unrequested, _calls = await _call_tool_with_bridge_result(
            "world_time_set",
            {"year": 2026, "month": 8, "day": 23, "hour": 14, "minute": 30},
            {
                "ok": 1,
                "applied": {
                    "year": 2026,
                    "month": 8,
                    "day": 23,
                    "hour": 14,
                    "minute": 30,
                    "time_multiplier": 4.0,
                },
            },
        )

        self.assertIn("multiplier_applied", requested)
        self.assertIsNone(requested.get("multiplier_applied"))
        self.assertEqual(requested.get("ok"), 1)
        self.assertEqual(requested.get("warnings"), ["multiplier_unconfirmed"])
        self.assertIn("multiplier_applied", unrequested)
        self.assertIsNone(unrequested.get("multiplier_applied"))
        self.assertNotIn("warnings", unrequested)

    async def test_minute_sixty_echo_matches_the_requested_hour(self) -> None:
        result, _calls = await _call_tool_with_bridge_result(
            "world_time_set",
            {
                "year": 2026,
                "month": 9,
                "day": 12,
                "hour": 9,
                "minute": 0,
                "time_multiplier": 1.0,
            },
            {
                "ok": 1,
                "applied": {
                    "year": 2026,
                    "month": 9,
                    "day": 12,
                    "hour": 8,
                    "minute": 60,
                },
            },
        )

        self.assertIs(result.get("date_applied"), True)
        self.assertEqual(result["applied"]["hour"], 9)
        self.assertEqual(result["applied"]["minute"], 0)
        self.assertEqual(result.get("ok"), 1)
        self.assertIsNone(result.get("multiplier_applied"))
        self.assertEqual(result.get("warnings"), ["multiplier_unconfirmed"])
        self.assertEqual(result["applied_echo"]["hour"], 8)
        self.assertEqual(result["applied_echo"]["minute"], 60)
        self.assertIs(result.get("clock_normalized"), True)

    async def test_true_date_mismatch_clears_ok(self) -> None:
        result, _calls = await _call_tool_with_bridge_result(
            "world_time_set",
            {"year": 2026, "month": 9, "day": 12, "hour": 9, "minute": 0},
            {
                "ok": 1,
                "applied": {
                    "year": 2026,
                    "month": 9,
                    "day": 12,
                    "hour": 10,
                    "minute": 0,
                },
            },
        )

        self.assertIs(result.get("date_applied"), False)
        self.assertEqual(result.get("ok"), 0)
        self.assertEqual(result.get("warnings"), ["date_not_applied"])

    async def test_previous_day_minute_sixty_echo_matches_midnight_request(self) -> None:
        result, _calls = await _call_tool_with_bridge_result(
            "world_time_set",
            {
                "year": 2026,
                "month": 9,
                "day": 12,
                "hour": 0,
                "minute": 0,
            },
            {
                "ok": 1,
                "applied": {
                    "year": 2026,
                    "month": 9,
                    "day": 11,
                    "hour": 23,
                    "minute": 60,
                },
            },
        )

        self.assertNotEqual(result["applied"]["hour"], 24)
        self.assertEqual(result["applied"]["hour"], 0)
        self.assertEqual(result["applied"]["minute"], 0)
        self.assertEqual(result["applied"]["day"], 12)
        self.assertEqual(result["applied"]["year"], 2026)
        self.assertEqual(result["applied"]["month"], 9)
        self.assertIs(result.get("date_applied"), True)
        self.assertEqual(result.get("ok"), 1)
        self.assertNotIn("date_not_applied", result.get("warnings", []))

    async def test_same_day_minute_sixty_echo_carries_to_next_day(self) -> None:
        result, _calls = await _call_tool_with_bridge_result(
            "world_time_set",
            {
                "year": 2026,
                "month": 9,
                "day": 12,
                "hour": 0,
                "minute": 0,
            },
            {
                "ok": 1,
                "applied": {
                    "year": 2026,
                    "month": 9,
                    "day": 12,
                    "hour": 23,
                    "minute": 60,
                },
            },
        )

        # Today's lie wrapped this echo to day=12 hour=0 ok=1. Honest
        # calendar carry is 13 Sep 00:00, which does not match the request.
        self.assertNotEqual(result["applied"]["hour"], 24)
        self.assertEqual(result["applied"]["hour"], 0)
        self.assertEqual(result["applied"]["minute"], 0)
        self.assertEqual(result["applied"]["day"], 13)
        self.assertNotEqual(result["applied"]["day"], 12)
        self.assertIs(result.get("date_applied"), False)
        self.assertEqual(result.get("ok"), 0)
        self.assertEqual(result.get("warnings"), ["date_not_applied"])

    async def test_same_day_float_minute_sixty_echo_carries_to_next_day(self) -> None:
        result, _calls = await _call_tool_with_bridge_result(
            "world_time_set",
            {
                "year": 2026,
                "month": 9,
                "day": 12,
                "hour": 0,
                "minute": 0,
            },
            {
                "ok": 1,
                "applied": {
                    "year": 2026,
                    "month": 9,
                    "day": 12,
                    "hour": 23.0,
                    "minute": 60.0,
                },
            },
        )

        # Today's skip: _is_int_clock_part rejects 60.0, so 23.0:60.0
        # stays uncarried (day=12). Honest path matches int 23:60.
        self.assertNotEqual(result["applied"]["hour"], 24)
        self.assertNotEqual(result["applied"]["hour"], 24.0)
        self.assertNotEqual(result["applied"]["minute"], 60.0)
        self.assertEqual(result["applied"]["hour"], 0)
        self.assertEqual(result["applied"]["minute"], 0)
        self.assertEqual(result["applied"]["day"], 13)
        self.assertNotEqual(result["applied"]["day"], 12)
        self.assertIs(result.get("date_applied"), False)
        self.assertEqual(result.get("ok"), 0)
        self.assertEqual(result.get("warnings"), ["date_not_applied"])

    async def test_missing_applied_echo_does_not_rewrite_ok(self) -> None:
        result, _calls = await _call_tool_with_bridge_result(
            "world_time_set",
            {"year": 2026, "month": 9, "day": 12, "hour": 9, "minute": 0},
            {"ok": 1},
        )
        self.assertIs(result.get("date_applied"), False)
        self.assertEqual(result.get("ok"), 1)
        self.assertNotIn("warnings", result)

    async def test_raw_eight_sixty_echo_is_recoverable_and_marked_normalized(self) -> None:
        result, _calls = await _call_tool_with_bridge_result(
            "world_time_set",
            {"year": 2026, "month": 9, "day": 12, "hour": 9, "minute": 0},
            {
                "ok": 1,
                "applied": {
                    "year": 2026,
                    "month": 9,
                    "day": 12,
                    "hour": 8,
                    "minute": 60,
                },
            },
        )

        # Silent overwrite of applied would lose 8:60. Both the raw echo
        # and the 9:00 interpretation have to be present.
        self.assertEqual(result["applied_echo"]["hour"], 8)
        self.assertEqual(result["applied_echo"]["minute"], 60)
        self.assertEqual(result["applied"]["hour"], 9)
        self.assertEqual(result["applied"]["minute"], 0)
        self.assertIs(result.get("clock_normalized"), True)
        self.assertNotEqual(result["applied_echo"], result["applied"])

    async def test_partial_day_echo_does_not_publish_day_thirty_two_as_ok(self) -> None:
        result, _calls = await _call_tool_with_bridge_result(
            "world_time_set",
            {
                "year": 2026,
                "month": 9,
                "day": 12,
                "hour": 0,
                "minute": 0,
            },
            {
                "ok": 1,
                "applied": {"day": 31, "hour": 23, "minute": 60},
            },
        )

        self.assertNotEqual(result.get("ok"), 1)
        self.assertEqual(result.get("ok"), 0)
        self.assertNotEqual(result["applied"].get("day"), 32)
        self.assertEqual(result["applied"]["day"], 31)
        self.assertEqual(result["applied"]["hour"], 0)
        self.assertEqual(result["applied"]["minute"], 0)
        self.assertEqual(result["applied_echo"]["hour"], 23)
        self.assertEqual(result["applied_echo"]["minute"], 60)
        self.assertIs(result.get("date_applied"), False)
        self.assertEqual(result.get("warnings"), ["date_not_applied"])


class NormalizeAppliedClockTest(unittest.TestCase):
    def test_minute_sixty_carries_into_hour(self) -> None:
        out = server._normalize_applied_clock({"hour": 8, "minute": 60, "year": 2026})
        self.assertEqual(out["hour"], 9)
        self.assertEqual(out["minute"], 0)
        self.assertEqual(out["year"], 2026)

    def test_minute_overflow_carries_into_the_next_day(self) -> None:
        out = server._normalize_applied_clock(
            {"year": 2026, "month": 9, "day": 12, "hour": 23, "minute": 60}
        )
        self.assertNotEqual(out["hour"], 24)
        self.assertEqual(out["hour"], 0)
        self.assertEqual(out["minute"], 0)
        self.assertEqual(out["day"], 13)
        self.assertEqual(out["month"], 9)
        self.assertEqual(out["year"], 2026)
        self.assertGreaterEqual(out["hour"], 0)
        self.assertLess(out["hour"], 24)

    def test_float_sixty_and_twenty_four_carry_like_ints(self) -> None:
        # Pin: fails while _is_int_clock_part is type-is-int only, because
        # 60.0/24.0 never enter _overflow_clock_parts.
        minute_out = server._normalize_applied_clock(
            {"hour": 8, "minute": 60.0, "year": 2026}
        )
        self.assertEqual(minute_out["hour"], 9)
        self.assertEqual(minute_out["minute"], 0)
        self.assertNotEqual(minute_out["minute"], 60.0)

        hour_out = server._normalize_applied_clock(
            {"year": 2026, "month": 9, "day": 12, "hour": 24.0, "minute": 0}
        )
        self.assertNotEqual(hour_out["hour"], 24.0)
        self.assertEqual(hour_out["hour"], 0)
        self.assertEqual(hour_out["minute"], 0)
        self.assertEqual(hour_out["day"], 13)

        midnight = server._normalize_applied_clock(
            {"year": 2026, "month": 9, "day": 12, "hour": 23.0, "minute": 60.0}
        )
        self.assertEqual(midnight["hour"], 0)
        self.assertEqual(midnight["minute"], 0)
        self.assertEqual(midnight["day"], 13)

    def test_minute_one_twenty_carries_two_hours(self) -> None:
        out = server._normalize_applied_clock({"hour": 8, "minute": 120})
        self.assertEqual(out["hour"], 10)
        self.assertEqual(out["minute"], 0)
        float_out = server._normalize_applied_clock({"hour": 8, "minute": 120.0})
        self.assertEqual(float_out["hour"], 10)
        self.assertEqual(float_out["minute"], 0)

    def test_partial_calendar_does_not_invent_a_day(self) -> None:
        out = server._normalize_applied_clock(
            {"day": 31, "hour": 23, "minute": 60}
        )
        self.assertEqual(out["hour"], 0)
        self.assertEqual(out["minute"], 0)
        self.assertEqual(out["day"], 31)
        self.assertNotEqual(out["day"], 32)

    def test_leaves_in_range_clocks_alone(self) -> None:
        src = {"hour": 9, "minute": 0}
        self.assertEqual(server._normalize_applied_clock(src), src)


class ToolDescriptionTruthTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))
        self.tools = {tool.name: tool for tool in await app.list_tools()}

    async def test_vehicle_enter_distinguishes_accepted_order_from_final_state(self) -> None:
        description = self.tools["vehicle_enter"].description or ""
        self.assertIn("seated=1 confirms the command was accepted", description)
        self.assertIn("not the final seated state", description)
        self.assertIn("client-side ownership", description)
        self.assertIn("vehicle_get_in_client", description)

    async def test_capture_screenshot_warns_about_focus_and_two_clients(self) -> None:
        description = self.tools["capture_screenshot"].description or ""
        self.assertIn("Without window focus", description)
        self.assertIn("never steals OS focus", description)
        self.assertIn("no SetForegroundWindow", description)
        self.assertIn("frame can be frozen", description)
        self.assertIn("cmdline_match/client_pid", description)
        self.assertIn("live run's client", description)

    async def test_capture_screenshot_names_the_frame_stale_contract(self) -> None:
        # mcp_capture publishes frame_stale/frame_stale_detail in the meta block
        # (mcp_capture.py:1144-1145). The names are literal: a consumer greps the tool
        # surface for the key it will read, so the description has to carry them
        # verbatim rather than describe a hand-rolled frame_sha256 comparison.
        description = self.tools["capture_screenshot"].description or ""
        self.assertIn("frame_stale", description)
        self.assertIn("frame_stale_detail", description)
        # bool | null, and null is not false: a first capture has no baseline.
        self.assertIn("null", description)
        # A repeated frame is a fact about pixels, not a tool error.
        self.assertIn("not an error", description)
        self.assertIn("frames>=2", description)
        self.assertIn("frames=4", description)
        self.assertIn("distinct_frames=1", description)
        self.assertIn("max_adjacent_delta=0", description)
        self.assertIn("frozen-render", description)
        self.assertIn("frames=1", description)
        self.assertIn("non-discriminating", description)
        self.assertIn("not a freeze signal", description)

    async def test_engine_set_documents_ownership_and_confirmation_fields(self) -> None:
        description = self.tools["engine_set"].description or ""
        self.assertIn("client-side ownership", description)
        self.assertIn("vehicle_get_in_client", description)
        self.assertIn("command_sent", description)
        self.assertIn("state_confirmed", description)
        self.assertIn("accepted, not confirmed", description)

    async def test_vehicle_get_in_client_is_not_server_crew(self) -> None:
        description = self.tools["vehicle_get_in_client"].description or ""
        self.assertIn("does not place the player in the server crew", description)
        self.assertIn("ActionSwitchLights", description)
        self.assertIn("until vehicle_enter", description)

    async def test_world_time_set_description_matches_divmod_overflow(self) -> None:
        description = self.tools["world_time_set"].description or ""
        self.assertNotIn(
            "An applied minute of 60 is normalized to hour+1", description
        )
        self.assertIn("divmod", description)
        self.assertIn("120", description)
        self.assertIn("applied_echo", description)
        self.assertIn("clock_normalized", description)


class TimeMultiplierProducerContractTest(unittest.TestCase):
    def test_mcp_applied_does_not_echo_time_multiplier(self) -> None:
        messages = (
            addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
        ).read_text(encoding="utf-8")
        match = re.search(r"class MCPApplied\s*\{(.*?)\n\};", messages, re.S)
        self.assertIsNotNone(match)
        self.assertNotRegex(match.group(1), r"\btime_multiplier\b")

        bridge = (
            addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
        ).read_text(encoding="utf-8")
        start = bridge.index("protected bool DispatchWorldTimeSet")
        brace = bridge.index("{", start)
        depth = 0
        body = ""
        for index in range(brace, len(bridge)):
            if bridge[index] == "{":
                depth += 1
            elif bridge[index] == "}":
                depth -= 1
                if depth == 0:
                    body = bridge[brace + 1 : index]
                    break
        self.assertIn("SetTimeMultiplier", body)
        self.assertNotIn("GetTimeMultiplier", body)

        world = Path(r"P:\scripts\3_Game\global\world.c")
        if not world.is_file():
            self.skipTest("vanilla World.c not mounted")
        world_src = world.read_text(encoding="utf-8")
        self.assertIn("proto native void SetTimeMultiplier", world_src)
        self.assertNotIn("GetTimeMultiplier", world_src)


if __name__ == "__main__":
    unittest.main()
