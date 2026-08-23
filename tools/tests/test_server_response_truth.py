"""Caller-facing server responses must distinguish dispatch from confirmed state."""

from __future__ import annotations

import unittest
from typing import Any

from dayz_mcp import control_client, server
from dayz_mcp.server import ServerConfig, build_app
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
        self.assertIn("multiplier_applied", unrequested)
        self.assertIsNone(unrequested.get("multiplier_applied"))


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
        self.assertIn("frame can be frozen", description)
        self.assertIn("cmdline_match/client_pid", description)
        self.assertIn("live run's client", description)

    async def test_engine_set_documents_ownership_and_confirmation_fields(self) -> None:
        description = self.tools["engine_set"].description or ""
        self.assertIn("client-side ownership", description)
        self.assertIn("vehicle_get_in_client", description)
        self.assertIn("command_sent", description)
        self.assertIn("state_confirmed", description)
        self.assertIn("accepted, not confirmed", description)


if __name__ == "__main__":
    unittest.main()
