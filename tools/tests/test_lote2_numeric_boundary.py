"""Numeric ingress regression matrix. All probes enter through FastMCP.call_tool.

Only the final registered function is replaced, never validation or metadata.
This keeps all probes offline and proves invalid input cannot reach a handler.
"""
from __future__ import annotations

import inspect
import unittest
from typing import Annotated, Union, get_args, get_origin, get_type_hints
from types import UnionType
from unittest.mock import AsyncMock, patch

from dayz_mcp import server
from tests._tiers import slow_test


def numeric_shape(annotation):
    origin = get_origin(annotation)
    if origin is Annotated:
        return numeric_shape(get_args(annotation)[0])
    if origin in (Union, UnionType):
        shapes = [numeric_shape(a) for a in get_args(annotation) if a is not type(None)]
        return next((s for s in shapes if s), None)
    if origin is list:
        inner = numeric_shape(get_args(annotation)[0])
        return "vector" if inner else None
    return "int" if annotation is int else "float" if annotation is float else None


def census(app):
    rows = []
    for tool in sorted(app._tool_manager.list_tools(), key=lambda t: t.name):
        for name, annotation in get_type_hints(tool.fn, include_extras=True).items():
            if name != "return" and (shape := numeric_shape(annotation)):
                rows.append((tool.name, name, shape))
    return rows


def sample(schema):
    if "default" in schema:
        return schema["default"]
    if "enum" in schema:
        return schema["enum"][0]
    if "anyOf" in schema:
        return sample(next(s for s in schema["anyOf"] if s.get("type") != "null"))
    if schema.get("type") == "array":
        return [sample(schema["items"])] * 3
    return {"string": "fixture", "integer": 1, "number": 1.0,
            "boolean": False, "array": [1.0, 2.0, 3.0], "object": {}}.get(schema.get("type"))


class NumericBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app, self.runtime = server.build_app(server.ServerConfig(
            key="fixture", port=0, enable_exec_enforce=True, log_sink=lambda _: None))
        self.rows = census(self.app)

    def arguments(self, name):
        tool = self.app._tool_manager.get_tool(name)
        schema = tool.parameters
        args = {p: sample(schema["properties"][p]) for p in schema.get("required", [])}
        if name == "dayz_test_run":
            args["mode"] = "all"
        return args

    async def probe(self, name, arguments, seen):
        tool = self.app._tool_manager.get_tool(name)
        original = tool.fn
        def sync_spy(*args, **kwargs):
            seen.update(inspect.signature(original).bind_partial(*args, **kwargs).arguments)
            seen["__called__"] = True
            return {"ok": True}
        async def async_spy(*args, **kwargs):
            return sync_spy(*args, **kwargs)
        with patch.object(tool, "fn", async_spy if tool.is_async else sync_spy):
            return await self.app.call_tool(name, arguments)

    def test_census_includes_alias_optional_vectors_and_conditional_tool(self):
        rows = set(self.rows)
        # 123 baseline + 4 from main (object_doors pos/object_id/timeout_s, input_describe timeout_s)
        # + 3 from fb-0063 part 1 (hands_take object_id/timeout_s, weapon_state timeout_s)
        # + 7 from fb-0063 part 2 (weapon_raise hold_ttl_s/timeout_s, weapon_aim dx/dy/timeout_s,
        # weapon_fire timeout_s, weapon_sights timeout_s). raised is a bool, not in this census.
        # + 1 from action_use door_index (StrictInt | None).
        # + 5 from ficha 5535 (anim_timeline cursor/limit/sample_hz/max_samples/timeout_s;
        # sources is a string list, not in this census).
        # + 3 from fb-29c1 (vehicle_door pos/object_id/timeout_s; source, mode and
        # type are strings, not in this census).
        # + 1 from fb-bd28 (world_spawn lifetime_s, StrictFloat | None).
        # + 4 from fb-e1ae part 2 (input_trigger dik/hold_s/ttl_s/timeout_s; kind,
        # entry, phase and name are strings, not in this census).
        # + 1 from world_time_get (timeout_s, its only parameter).
        # + 5 from 4ae5 (player_trace cursor/limit/sample_hz/max_samples/timeout_s;
        # mode and trace_id are strings, not in this census).
        # + 7 from c1cb (player_move hold_s/ttl_s/angle_deg/heading_deg/to/
        # arrive_radius_m/timeout_s; speed and phase are strings, not in this census).
        # + 2 from bef7 and 3136 (player_heal timeout_s, player_godmode timeout_s;
        # uid is a string and full and on are bools, not in this census).
        self.assertEqual(len(rows), 166)
        self.assertIn(("player_heal", "timeout_s", "float"), rows)
        self.assertIn(("player_godmode", "timeout_s", "float"), rows)
        for name in ("cursor", "limit", "sample_hz", "max_samples"):
            self.assertIn(("player_trace", name, "int"), rows)
        self.assertIn(("player_trace", "timeout_s", "float"), rows)
        for name in ("hold_s", "ttl_s", "angle_deg", "heading_deg", "arrive_radius_m", "timeout_s"):
            self.assertIn(("player_move", name, "float"), rows)
        self.assertIn(("player_move", "to", "vector"), rows)
        self.assertIn(("world_time_get", "timeout_s", "float"), rows)
        self.assertIn(("input_trigger", "dik", "int"), rows)
        self.assertIn(("input_trigger", "hold_s", "float"), rows)
        self.assertIn(("input_trigger", "ttl_s", "float"), rows)
        self.assertIn(("input_trigger", "timeout_s", "float"), rows)
        self.assertIn(("world_spawn", "lifetime_s", "float"), rows)
        self.assertIn(("vehicle_door", "pos", "vector"), rows)
        self.assertIn(("vehicle_door", "object_id", "int"), rows)
        self.assertIn(("vehicle_door", "timeout_s", "float"), rows)
        self.assertIn(("anim_timeline", "sample_hz", "int"), rows)
        self.assertIn(("anim_timeline", "timeout_s", "float"), rows)
        self.assertIn(("lease_acquire", "max_wait_s", "float"), rows)
        self.assertIn(("exec_enforce", "timeout_s", "float"), rows)
        self.assertIn(("object_anim", "phase", "float"), rows)
        self.assertIn(("scene_raycast", "from_pos", "vector"), rows)
        self.assertIn(("dayz_test_run", "client_start_budget_s", "float"), rows)
        self.assertIn(("dayz_test_close", "graceful_timeout_s", "float"), rows)
        self.assertIn(("inventory_attach", "pos", "vector"), rows)
        self.assertIn(("inventory_attach", "object_id", "int"), rows)
        self.assertIn(("inventory_attach", "timeout_s", "float"), rows)
        self.assertIn(("hands_take", "object_id", "int"), rows)
        self.assertIn(("hands_take", "timeout_s", "float"), rows)
        self.assertIn(("weapon_state", "timeout_s", "float"), rows)
        self.assertIn(("object_doors", "pos", "vector"), rows)
        self.assertIn(("object_doors", "object_id", "int"), rows)
        self.assertIn(("object_doors", "timeout_s", "float"), rows)
        self.assertIn(("input_describe", "timeout_s", "float"), rows)
        self.assertIn(("weapon_raise", "hold_ttl_s", "float"), rows)
        self.assertIn(("weapon_raise", "timeout_s", "float"), rows)
        self.assertIn(("weapon_aim", "dx", "float"), rows)
        self.assertIn(("weapon_aim", "dy", "float"), rows)
        self.assertIn(("weapon_aim", "timeout_s", "float"), rows)
        self.assertIn(("weapon_fire", "timeout_s", "float"), rows)
        self.assertIn(("weapon_sights", "timeout_s", "float"), rows)
        self.assertIn(("action_use", "door_index", "int"), rows)

    async def test_every_numeric_parameter_rejects_both_booleans_before_handler(self):
        for name, param, shape in self.rows:
            wire_param = "from" if (name, param) == ("scene_raycast", "from_pos") else param
            for value in (False, True):
                positions = range(3) if shape == "vector" else (None,)
                for position in positions:
                    with self.subTest(tool=name, param=param, value=value, position=position):
                        args = self.arguments(name)
                        payload = [1.0, 2.0, 3.0] if shape == "vector" else value
                        if position is not None:
                            payload[position] = value
                        args[wire_param] = payload
                        seen = {}
                        with self.assertRaises(server.ToolError) as caught:
                            await self.probe(name, args, seen)
                        self.assertIn(param, str(caught.exception))
                        self.assertEqual(seen, {}, "invalid argument reached handler")

    @slow_test
    async def test_every_numeric_parameter_accepts_real_numbers(self):
        for name, param, shape in self.rows:
            wire_param = "from" if (name, param) == ("scene_raycast", "from_pos") else param
            values = ([0, 1, 2], [0.0, 1.25, 2.5]) if shape == "vector" else (0, 1) if shape == "int" else (0, 1, 1.25)
            for value in values:
                with self.subTest(tool=name, param=param, value=value):
                    args = self.arguments(name)
                    args[wire_param] = value
                    seen = {}
                    await self.probe(name, args, seen)
                    self.assertTrue(seen.pop("__called__"))
                    self.assertEqual(seen[param], value)
                    self.assertIsNot(type(seen[param]), bool)

    async def test_optional_null_and_omitted_defaults_still_reach_handler(self):
        for name, param, _shape in self.rows:
            tool = self.app._tool_manager.get_tool(name)
            props = tool.parameters["properties"]
            wire_param = "from" if (name, param) == ("scene_raycast", "from_pos") else param
            prop = props[wire_param]
            if not any(s.get("type") == "null" for s in prop.get("anyOf", [])):
                continue
            for explicit in (False, True):
                with self.subTest(tool=name, param=param, explicit_null=explicit):
                    args = self.arguments(name)
                    if explicit:
                        args[wire_param] = None
                    seen = {}
                    await self.probe(name, args, seen)
                    self.assertIsNone(seen[param])

    async def test_real_bridge_handlers_preserve_zero_and_integer_float_values(self):
        cases = (
            ("object_anim", {"source": "door", "object_id": 1, "phase": 0}, "phase", 0.0),
            ("world_weather_set", {"rain": 0}, "rain", 0.0),
            ("vehicle_control", {"throttle": 1, "steer": 0}, "throttle", 1.0),
            ("world_spawn", {"type": "Barrel_Green", "pos": [1, 2, 3], "rotation": 0}, "rotation", 0),
        )
        for name, args, key, expected in cases:
            with self.subTest(tool=name):
                with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})) as bridge:
                    await self.app.call_tool(name, args)
                bridge.assert_awaited_once()
                self.assertEqual(bridge.await_args.args[1][key], expected)


if __name__ == "__main__":
    unittest.main()
