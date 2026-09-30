"""The daemon ingress accepts every payload a tool sends, and not more.

fb-20260822-191204-6ce4 gave the sixteen verbs that had no schema one each in
loopback._COMMAND_ARG_SCHEMAS. A schema stricter than its tool would turn a
valid tool call into bad_args at the daemon, and neither side's own tests would
see it: the tool tests replace call_bridge, and the table tests never run a
tool. This test runs each tool through build_app with a recording call_bridge
and checks every captured payload against validate_command_args. It covers each
shape a tool can emit, and the two internal senders: the telemetry probe of
wait_for(entity_state) and the clearance raycast of player_teleport.
"""
from __future__ import annotations

import ast
import copy
import unittest
from pathlib import Path
from typing import Any

from dayz_mcp import loopback, server
from dayz_mcp.server import ServerConfig, build_app
from tests._tiers import slow_test


_TOOLS_DIR = Path(__file__).resolve().parents[1]
_POS = [10.0, 0.0, 20.0]
_DATE = {"year": 2026, "month": 9, "day": 28, "hour": 9, "minute": 0}

# (tool, arguments, verb the tool must send)
_TOOL_CALLS: tuple[tuple[str, dict[str, Any], str], ...] = (
    ("query_player_state", {}, "query_player_state"),
    ("weapon_state", {}, "weapon_state"),
    ("weapon_state", {"uid": "76561198000000000"}, "weapon_state"),
    ("hands_take", {"object_id": 1}, "hands_take"),
    ("hands_take", {"object_id": 4, "uid": "76561198000000000"}, "hands_take"),
    ("query_all_players", {}, "query_all_players"),
    ("world_spawn", {"type": "CivilianSedan", "pos": _POS}, "world_spawn"),
    (
        "world_spawn",
        {"type": "CivilianSedan", "pos": [1, 2, 3], "flags": 0, "rotation": 5},
        "world_spawn",
    ),
    ("vehicle_enter", {"pos": _POS}, "vehicle_enter"),
    ("scene_raycast", {"from_pos": [0.0, 10.0, 0.0], "to": [0.0, -5.0, 0.0]}, "scene_raycast"),
    (
        "scene_raycast",
        {
            "from_pos": [0.0, 10.0, 0.0],
            "to": [0.0, -5.0, 0.0],
            "method": "bullet",
            "ignore": "player",
            "radius": 0.0,
            "intersect": "ifire",
        },
        "scene_raycast",
    ),
    (
        "telemetry_read",
        {"mode": "object_at", "type": "CarScript", "pos": _POS, "radius": 5.0},
        "telemetry_read",
    ),
    (
        "telemetry_read",
        {"mode": "fixture_jsonl", "path": "fixture.jsonl", "max_lines": 10},
        "telemetry_read",
    ),
    ("query_get_in_condition", {"pos": _POS}, "query_get_in_condition"),
    ("world_time_set", dict(_DATE), "world_time_set"),
    ("world_time_set", {**_DATE, "time_multiplier": -1.0}, "world_time_set"),
    ("world_time_set", {**_DATE, "time_multiplier": 64.0}, "world_time_set"),
    ("world_weather_set", {"rain": 0.5}, "world_weather_set"),
    (
        "world_weather_set",
        {"overcast": 1.0, "rain": 0.0, "fog": 0.2, "time": 30.0, "min_duration": 60.0},
        "world_weather_set",
    ),
    (
        "camera_set",
        {"cam_mode": "orient", "cam_pos": [0.0, 2.0, 0.0], "cam_orientation": [90.0, 0.0, 0.0]},
        "camera_set",
    ),
    (
        "camera_set",
        {"cam_mode": "lookat", "cam_pos": [0.0, 2.0, 0.0], "look_at": [5.0, 1.0, 5.0]},
        "camera_set",
    ),
    (
        "camera_set",
        {"cam_mode": "look_at", "cam_pos": [0.0, 2.0, 0.0], "look_at": [5.0, 1.0, 5.0]},
        "camera_set",
    ),
    (
        "camera_set",
        {"cam_mode": "matrix", "cam_matrix": [0.0] * 12, "fov": 0.9, "settle_ticks": 0},
        "camera_set",
    ),
    (
        "camera_set",
        {"cam_mode": "free", "cam_pos": [0.0, 2.0, 0.0], "look_at": [5.0, 1.0, 5.0]},
        "camera_set",
    ),
    (
        "camera_set",
        {"cam_mode": "free", "cam_pos": [0.0, 2.0, 0.0], "cam_orientation": [90.0, 0.0, 0.0]},
        "camera_set",
    ),
    ("camera_get", {}, "camera_get"),
    ("camera_get", {"cam_mode": ""}, "camera_get"),
    ("vehicle_get_in_client", {"pos": _POS}, "vehicle_get_in_client"),
    ("engine_set", {"mode": "start"}, "engine_set"),
    ("engine_set", {"mode": "stop"}, "engine_set"),
    ("vehicle_control", {}, "vehicle_control"),
    (
        "vehicle_control",
        {"throttle": 1.0, "steer": -1.0, "brake": 1.0, "handbrake": 1.0, "hold_ttl_s": 30.0},
        "vehicle_control",
    ),
    ("vehicle_telemetry", {}, "vehicle_telemetry"),
    ("vehicle_release", {}, "vehicle_release"),
    # Edges of what the tools accept. A schema that is narrower than its tool
    # anywhere inside these ranges fails here (review R1 of this change: a
    # hypothetical fov <= 0.9 cap passed the first 30 shapes).
    ("world_spawn", {"type": "ZmbM_CitizenASkinny", "pos": _POS, "flags": 3108}, "world_spawn"),
    ("world_spawn", {"type": "CivilianSedan", "pos": _POS, "flags": 1028}, "world_spawn"),
    (
        "camera_set",
        {
            "cam_mode": "orient",
            "cam_pos": [0.0, 2.0, 0.0],
            "cam_orientation": [360.0, -90.0, 180.0],
            "fov": 3.1,
            "settle_ticks": 600,
        },
        "camera_set",
    ),
    (
        "scene_raycast",
        {"from_pos": [0.0, 10.0, 0.0], "to": [0.0, -5.0, 0.0], "radius": 100.0, "intersect": "geom"},
        "scene_raycast",
    ),
    (
        "telemetry_read",
        {"mode": "object_at", "type": "CarScript", "pos": _POS, "radius": 5000.0},
        "telemetry_read",
    ),
    ("query_get_in_condition", {"pos": _POS, "component": 7}, "query_get_in_condition"),
    (
        "world_time_set",
        {"year": 1970, "month": 1, "day": 1, "hour": 0, "minute": 0, "time_multiplier": 0.0},
        "world_time_set",
    ),
    (
        "world_time_set",
        {"year": 2100, "month": 12, "day": 31, "hour": 23, "minute": 59},
        "world_time_set",
    ),
    ("world_weather_set", {"overcast": 0.0}, "world_weather_set"),
    ("world_weather_set", {"fog": 1.0, "time": 1e6, "min_duration": 1e6}, "world_weather_set"),
    (
        "vehicle_control",
        {"throttle": 0.5, "steer": 1.0, "brake": 0.25, "handbrake": 0.0, "hold_ttl_s": 0.5},
        "vehicle_control",
    ),
    (
        "object_doors",
        {"type": "Land_Garage_Row_Small", "pos": _POS},
        "object_doors",
    ),
    ("object_doors", {"object_id": 1}, "object_doors"),
    ("vehicle_door", {"object_id": 1, "source": "DoorsDriver", "mode": "read"}, "vehicle_door"),
    ("vehicle_door", {"object_id": 7, "source": "DoorsCargo1", "mode": "open"}, "vehicle_door"),
    (
        "vehicle_door",
        {"type": "CivilianSedan", "pos": _POS, "source": "DoorsTrunk", "mode": "close"},
        "vehicle_door",
    ),
    ("input_describe", {"name": "UAMoveForward"}, "input_describe"),
    ("input_describe", {"name": "a" * 128}, "input_describe"),
    ("weapon_raise", {"raised": True}, "weapon_raise"),
    ("weapon_raise", {"raised": False, "hold_ttl_s": 30.0}, "weapon_raise"),
    ("weapon_aim", {"dx": 0.0, "dy": 0.0}, "weapon_aim"),
    ("weapon_aim", {"dx": 3.141593, "dy": -3.141593}, "weapon_aim"),
    ("weapon_fire", {}, "weapon_fire"),
    ("weapon_sights", {"mode": "ironsights"}, "weapon_sights"),
    ("weapon_sights", {"mode": "optics"}, "weapon_sights"),
    ("weapon_sights", {"mode": "none"}, "weapon_sights"),
)

# A bridge answer per verb, enough for each tool to reach its bridge call and
# for player_teleport to reach its clearance raycast.
_BRIDGE_ANSWERS: dict[str, dict[str, Any]] = {
    "surface_query": {"ok": 1, "y": 5.0},
    "scene_raycast": {"ok": 1, "hit": 1, "pos": [10.0, 5.0, 20.0]},
}


async def _captured_calls(
    tool: str, arguments: dict[str, Any]
) -> list[tuple[str, dict[str, Any], str]]:
    app, runtime = build_app(ServerConfig(log_sink=lambda _message: None))
    calls: list[tuple[str, dict[str, Any], str]] = []

    async def call_bridge(
        command: str, args: dict[str, Any], peer: str, timeout_s: float
    ) -> dict[str, Any]:
        calls.append((command, copy.deepcopy(args), peer))
        return dict(_BRIDGE_ANSWERS.get(command, {"ok": 1}))

    async def no_status(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("no bridge in this test")

    runtime.call_bridge = call_bridge  # type: ignore[method-assign]
    runtime.bridge_status_payload = no_status  # type: ignore[method-assign]
    try:
        await app.call_tool(tool, arguments)
    except Exception:
        # Only the payloads matter here; post-processing of the fake answer
        # may refuse, and that is not what this test is about.
        pass
    return calls


class IngressSchemaCoherenceTest(unittest.IsolatedAsyncioTestCase):
    @slow_test
    async def test_every_tool_payload_passes_the_ingress(self) -> None:
        for tool, arguments, verb in _TOOL_CALLS:
            with self.subTest(tool=tool, arguments=arguments):
                calls = await _captured_calls(tool, arguments)
                sent = [command for command, _args, _peer in calls]
                self.assertIn(verb, sent, f"{tool} never reached its bridge call")
                for command, args, _peer in calls:
                    self.assertEqual(
                        loopback.validate_command_args(command, args),
                        (True, None),
                        f"{tool} sent {command} {args!r}, which the ingress refuses",
                    )

    async def test_player_teleport_clearance_raycast_passes_the_ingress(self) -> None:
        calls = await _captured_calls("player_teleport", {"pos": [10.0, 0.0, 20.0]})
        rays = [args for command, args, _peer in calls if command == "scene_raycast"]
        self.assertEqual(len(rays), 1, f"clearance raycast not sent: {calls!r}")
        for command, args, _peer in calls:
            self.assertEqual(loopback.validate_command_args(command, args), (True, None))

    def test_wait_for_entity_probe_passes_the_ingress(self) -> None:
        probe = server._entity_wait_request(
            {"type": "CarScript", "pos": [1.0, 2.0, 3.0], "radius": 50, "field": "found", "equals": True}
        )
        self.assertEqual(loopback.validate_command_args("telemetry_read", probe), (True, None))

    async def test_an_extra_key_on_a_tool_payload_is_refused(self) -> None:
        # Negative control: the check above has to be able to fail.
        calls = await _captured_calls("world_spawn", {"type": "CivilianSedan", "pos": _POS})
        command, args, _peer = next(call for call in calls if call[0] == "world_spawn")
        args["unexpected"] = True
        self.assertEqual(loopback.validate_command_args(command, args), (False, "bad_args"))

    def test_vehicle_control_ttl_limit_matches_the_tool(self) -> None:
        self.assertEqual(
            loopback._VEHICLE_CONTROL_MAX_TTL_S, server.VEHICLE_CONTROL_MAX_TTL_S
        )

    def test_weapon_action_bounds_match_the_tool(self) -> None:
        self.assertEqual(loopback.WEAPON_RAISE_DEFAULT_TTL_S, server.WEAPON_RAISE_DEFAULT_TTL_S)
        self.assertEqual(loopback.WEAPON_RAISE_MAX_TTL_S, server.WEAPON_RAISE_MAX_TTL_S)
        self.assertEqual(loopback.WEAPON_AIM_ABS_MAX, server.WEAPON_AIM_ABS_MAX)
        self.assertEqual(loopback.WEAPON_RAISE_DEFAULT_TTL_S, 3.0)
        self.assertEqual(loopback.WEAPON_RAISE_MAX_TTL_S, 30.0)
        self.assertEqual(loopback.WEAPON_AIM_ABS_MAX, 3.141593)

    def test_the_session_e2e_binary_sends_valid_payloads(self) -> None:
        # _session_coordination/e2e_agent_sessions.py calls call_bridge directly,
        # not through a tool, so the coherence cases above cannot see it. Review R1
        # found three of its payloads refused after this change. Every literal
        # payload it sends must pass the ingress.
        source = (_TOOLS_DIR / "_session_coordination" / "e2e_agent_sessions.py").read_text(
            encoding="utf-8"
        )
        checked = 0
        for node in ast.walk(ast.parse(source)):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "call_bridge"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                continue
            payload = ast.literal_eval(node.args[1])
            with self.subTest(line=node.lineno, command=node.args[0].value):
                self.assertEqual(
                    loopback.validate_command_args(node.args[0].value, payload),
                    (True, None),
                )
            checked += 1
        self.assertGreaterEqual(checked, 6, "the scan found too few call_bridge calls")


if __name__ == "__main__":
    unittest.main()
