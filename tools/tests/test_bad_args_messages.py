"""Caller-facing argument errors and numeric tool descriptions stay actionable."""

from __future__ import annotations

import ast
import inspect
import unittest
from typing import Any

from dayz_mcp import control_client, server
from dayz_mcp.server import ServerConfig, build_app


RETAIL_QUARANTINE_RECIPE = (
    "retail_quarantine: a DayZ retail process is running on this machine; "
    "mutations are blocked until no DayZ retail process is running"
)


class BadArgsMessagesTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))

    async def _bad_args_message(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> str:
        with self.assertRaises(server.ToolError) as raised:
            await self.app.call_tool(tool_name, arguments)
        message = str(raised.exception)
        wrapper = f"Error executing tool {tool_name}: "
        self.assertTrue(message.startswith(wrapper), message)
        return message[len(wrapper):]

    async def test_bad_args_name_field_and_expectation_across_tools(self) -> None:
        cases = (
            ("logs_since", {"max_lines": 0}, "max_lines", "int from 1 to 2000"),
            ("object_delete", {"object_id": 0}, "object_id", "positive int"),
            (
                "notify_players",
                {"show_time": 0.0, "title": "Notice"},
                "show_time",
                "finite number greater than 0",
            ),
            (
                "scene_raycast",
                {"from_pos": [0, 0, 0], "to": [1, 1, 1], "method": "laser"},
                "method",
                "one of 'rvproxy' or 'bullet'",
            ),
            (
                "vehicle_prepare_fixture",
                {"type": "", "pos": [0, 0, 0]},
                "type",
                "non-empty string",
            ),
            (
                "object_anim",
                {"type": "House", "pos": [0, 0, 0], "source": ""},
                "source",
                "non-empty string",
            ),
            (
                "infected_drive",
                {"type": "ZmbM_CitizenASkinny_Blue", "pos": [0, 0, 0]},
                "heading",
                "provided when mode is omitted",
            ),
            (
                "inventory_give",
                {"classname": "Apple", "dest": "backpack"},
                "dest",
                "one of 'hands' or 'inventory'",
            ),
            (
                "object_inspect",
                {"type": "House", "pos": [0, 0, 0], "want": []},
                "want",
                "non-empty list of non-empty strings",
            ),
            (
                "entities_query",
                {"pos": [0, 0, 0], "radius": 5.0, "limit": 0},
                "limit",
                "int from 1 to 128",
            ),
            (
                "world_weather_set",
                {"overcast": 0.5, "time": -1.0},
                "time",
                "non-negative finite number of seconds",
            ),
            (
                "camera_set",
                {
                    "cam_mode": "orient",
                    "cam_pos": [0, 0, 0],
                    "cam_orientation": [0, 0, 0],
                    "fov": -1.0,
                },
                "fov",
                "non-negative finite number of radians",
            ),
            ("ui_click", {"path": "Root/Button", "button": 3}, "button", "int from 0 to 2"),
            (
                "action_use",
                {"action": "ActionOpen", "radius": 0.0},
                "radius",
                "finite number greater than 0 and at most 200",
            ),
        )

        for tool_name, arguments, field, expectation in cases:
            with self.subTest(tool=tool_name, field=field):
                message = await self._bad_args_message(tool_name, arguments)
                self.assertTrue(message.startswith("bad_args: "), message)
                self.assertIn(field, message)
                self.assertIn(" must ", message)
                self.assertIn(expectation, message)

    async def test_world_position_keeps_public_bad_pos_code(self) -> None:
        message = await self._bad_args_message(
            "world_spawn", {"type": "CivilianSedan", "pos": []}
        )
        self.assertEqual(message, "bad_pos")

    def test_server_has_no_bare_bad_args_tool_error(self) -> None:
        tree = ast.parse(inspect.getsource(server))
        bare_lines = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ToolError"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "bad_args"
        ]
        self.assertEqual(bare_lines, [], f"bare ToolError('bad_args') at {bare_lines}")


class ToolDescriptionUnitsTest(unittest.IsolatedAsyncioTestCase):
    async def test_numeric_parameters_name_units_and_flag_semantics(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))
        tools = {tool.name: tool for tool in await app.list_tools()}
        expected_fragments = {
            "world_spawn": ("rotation", "RF_*", "not an angle"),
            "notify_players": ("show_time", "display duration in seconds"),
            "world_weather_set": (
                "time",
                "transition duration in seconds",
                "min_duration",
                "minimum hold duration in seconds",
            ),
            "camera_set": (
                "cam_orientation",
                "[yaw, pitch, roll] in degrees",
                "fov",
                "FOV angle in radians",
            ),
            "object_anim": ("phase", "unitless", "SetAnimationPhase"),
        }

        for tool_name, fragments in expected_fragments.items():
            description = tools[tool_name].description or ""
            with self.subTest(tool=tool_name):
                for fragment in fragments:
                    self.assertIn(fragment, description)


class RetailQuarantineReasonTest(unittest.IsolatedAsyncioTestCase):
    def test_payload_appends_each_valid_reason(self) -> None:
        for reason in (
            "no_probe",
            "probe_error",
            "probe_malformed",
            "probe_unknown",
            "retail_present",
        ):
            with self.subTest(reason=reason):
                self.assertEqual(
                    server._public_enqueue_error(
                        {"error": "retail_quarantine", "reason": reason}
                    ),
                    f"{RETAIL_QUARANTINE_RECIPE}; reason: {reason}",
                )

    def test_payload_without_valid_reason_keeps_recipe_exact(self) -> None:
        for payload in (
            {"error": "retail_quarantine"},
            {"error": "retail_quarantine", "reason": "unexpected"},
            {"error": "retail_quarantine", "reason": 7},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(
                    server._public_enqueue_error(payload), RETAIL_QUARANTINE_RECIPE
                )

    async def test_control_error_appends_valid_reason_already_in_hint(self) -> None:
        runtime = object.__new__(server.ClientRuntime)

        async def rejected() -> dict[str, object]:
            raise control_client.ControlClientError(
                "retail_quarantine",
                request_stage="post_request",
                http_bytes_sent=1,
                hint="probe_error",
            )

        with self.assertRaises(server.ToolError) as raised:
            await runtime._control_with_lazy_spawn(rejected)

        self.assertEqual(
            str(raised.exception),
            f"{RETAIL_QUARANTINE_RECIPE}; reason: probe_error",
        )


if __name__ == "__main__":
    unittest.main()
