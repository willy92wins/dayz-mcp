"""fb-20260910-031716-f5a7 pts 1/2/4 + fb-20260910-102449-f47b pt 4.

Tool descriptions and Python ToolError domains only. No Enforce/PBO, no
camera/restore, no new verbs.
"""

from __future__ import annotations

import unittest

from dayz_mcp.server import VEHICLE_CONTROL_MAX_TTL_S, ServerConfig, ToolError, build_app


class LoteMsgsF5a7DescriptionsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, _runtime = build_app(
            ServerConfig(key="k", port=0, log_sink=lambda _m: None)
        )
        self.tools = {tool.name: tool for tool in await self.app.list_tools()}

    def test_vehicle_get_in_client_says_what_it_is_not(self) -> None:
        description = self.tools["vehicle_get_in_client"].description or ""
        self.assertIn("engine_set", description)
        self.assertIn("vehicle_control", description)
        self.assertIn("vehicle_trace", description)
        self.assertIn("does not place the player in the server crew", description)
        self.assertIn("ActionSwitchLights", description)
        self.assertIn("vehicle_enter", description)

    def test_action_use_started_one_is_not_server_acceptance(self) -> None:
        description = self.tools["action_use"].description or ""
        self.assertIn("started:1", description)
        self.assertIn("neither server acceptance", description)

    def test_dayz_test_run_names_unattended_night_path(self) -> None:
        description = self.tools["dayz_test_run"].description or ""
        self.assertIn("mode=all plus wait_for(players_at_least, 1)", description)
        self.assertIn("without human intervention", description)
        self.assertIn("viable night session", description)

    def test_capture_screenshot_names_frozen_render_signal(self) -> None:
        description = self.tools["capture_screenshot"].description or ""
        self.assertIn("frames>=2", description)
        self.assertIn("frames=4", description)
        self.assertIn("distinct_frames=1", description)
        self.assertIn("max_adjacent_delta=0", description)
        self.assertIn("frozen-render", description)
        self.assertIn("not a process hang", description)
        self.assertIn("frames=1", description)
        self.assertIn("non-discriminating", description)
        self.assertIn("not a freeze signal", description)


class LoteMsgsF5a7ErrorDomainsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, _runtime = build_app(
            ServerConfig(key="k", port=0, log_sink=lambda _m: None)
        )

    async def _error(self, tool_name: str, arguments: dict) -> str:
        with self.assertRaises(ToolError) as raised:
            await self.app.call_tool(tool_name, arguments)
        message = str(raised.exception)
        wrapper = f"Error executing tool {tool_name}: "
        self.assertTrue(message.startswith(wrapper), message)
        return message[len(wrapper) :]

    async def test_engine_set_bad_mode_names_start_stop(self) -> None:
        message = await self._error("engine_set", {"mode": "idle"})
        self.assertTrue(message.startswith("bad_mode"), message)
        self.assertIn("start", message)
        self.assertIn("stop", message)
        self.assertIn("idle", message)

    async def test_vehicle_control_bad_hold_ttl_s_names_range(self) -> None:
        too_high = VEHICLE_CONTROL_MAX_TTL_S + 0.1
        message = await self._error("vehicle_control", {"hold_ttl_s": too_high})
        self.assertTrue(message.startswith("bad_hold_ttl_s"), message)
        self.assertIn("[0, ", message)
        self.assertIn(str(VEHICLE_CONTROL_MAX_TTL_S), message)
        self.assertEqual(VEHICLE_CONTROL_MAX_TTL_S, 30.0)


if __name__ == "__main__":
    unittest.main()
