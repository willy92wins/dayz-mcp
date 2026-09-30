"""anim_timeline (ficha 5535): request validation, bridge result, tool, ingress.

The Enforce side is pinned in tests/test_anim_timeline_contract.py.
"""
from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp import anim_timeline, loopback, result_prune, server
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease


TRACE_ID = "0123456789abcdef0123456789abcdef"
WIRE_KEYS = {"mode", "trace_id", "cursor", "limit", "sample_hz", "max_samples", "sources"}


def _request(**overrides: object) -> dict[str, object]:
    """normalize_request with a valid non-start call and `overrides` applied."""
    kwargs: dict[str, object] = {
        "mode": "status",
        "trace_id": TRACE_ID,
        "cursor": 0,
        "limit": 64,
        "sample_hz": 20,
        "max_samples": 4096,
        "sources": None,
    }
    kwargs.update(overrides)
    return anim_timeline.normalize_request(**kwargs)  # type: ignore[arg-type]


def _sample(**overrides: object) -> dict[str, object]:
    sample: dict[str, object] = {
        "t_s": 0.87,
        "edge": 1,
        "player_present": 1,
        "action": "ActionPourLiquid",
        "action_state": 2,
        "callback": "command",
        "callback_both": 0,
        "state": 2,
        "state_name": "LOOP_LOOP",
        "hands": "CocaLab_Tray",
        "hands_present": 1,
        "phases": [0.25, 1.0],
    }
    sample.update(overrides)
    return sample


def _bridge_result(*samples: dict[str, object]) -> dict[str, object]:
    return {
        "ok": 1,
        "timeline": {
            "schema": anim_timeline.TIMELINE_SCHEMA,
            "mode": "read",
            "trace_id": TRACE_ID,
            "active": 0,
            "complete": 1,
            "overflow": 0,
            "stop_reason": "requested",
            "sample_hz": 20,
            "capacity": 4096,
            "count": len(samples),
            "elapsed_s": 3.47,
            "cursor": 0,
            "next_cursor": len(samples),
            "eof": 1,
            "sources": ["lid", "tray"],
            "samples": list(samples),
        },
    }


def _content_json(content: object) -> dict:
    if isinstance(content, tuple):
        blocks, structured = content
        if isinstance(structured, dict):
            return structured
        content = blocks
    parsed = json.loads(content[0].text)  # type: ignore[index,union-attr]
    if not isinstance(parsed, dict):
        raise AssertionError("expected dict")
    return parsed


class AnimTimelineRequestTest(unittest.TestCase):
    def test_start_generates_an_id_and_forwards_every_wire_key(self) -> None:
        args = _request(mode="start", trace_id="", sources=["lid", "tray"])
        self.assertEqual(set(args), WIRE_KEYS)
        self.assertRegex(str(args["trace_id"]), r"^[0-9a-f]{32}$")
        self.assertEqual(args["sources"], ["lid", "tray"])
        other = _request(mode="start", trace_id="")
        self.assertNotEqual(args["trace_id"], other["trace_id"])

    def test_start_refuses_a_caller_id_and_other_modes_require_one(self) -> None:
        with self.assertRaisesRegex(ValueError, "^bad_trace_id$"):
            _request(mode="start", trace_id=TRACE_ID)
        for mode in ("status", "stop", "read", "clear"):
            with self.subTest(mode=mode):
                self.assertEqual(_request(mode=mode)["trace_id"], TRACE_ID)
                for bad in ("", "A" * 32, "a" * 31, "a" * 33, "g" * 32, None, 7):
                    with self.assertRaisesRegex(ValueError, "^bad_trace_id$"):
                        _request(mode=mode, trace_id=bad)

    def test_modes_are_the_five_and_nothing_else(self) -> None:
        self.assertEqual(
            anim_timeline.TIMELINE_MODES,
            frozenset({"start", "status", "stop", "read", "clear"}),
        )
        for bad in ("dump", "START", "", " read", None, ["read"], 1):
            with self.subTest(mode=bad):
                with self.assertRaisesRegex(ValueError, "^bad_mode$"):
                    _request(mode=bad)

    def test_every_numeric_bound_is_inclusive_and_typed(self) -> None:
        cases = (
            ("cursor", "bad_cursor", (0, 10_000), (-1, True, 1.0, "0")),
            ("limit", "bad_limit", (1, 64), (0, 65, True, 1.0)),
            ("sample_hz", "bad_sample_hz", (10, 60), (9, 61, True, 20.0)),
            ("max_samples", "bad_max_samples", (2, 8192), (1, 8193, False, 2.0)),
        )
        for field, error, accepted, refused in cases:
            for value in accepted:
                with self.subTest(field=field, value=value):
                    self.assertEqual(_request(**{field: value})[field], value)
            for value in refused:
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(ValueError, f"^{error}$"):
                        _request(**{field: value})

    def test_sources_shape(self) -> None:
        accepted = (
            None,
            [],
            ["lid"],
            [" ~"],
            ["a" * 64],
            [chr(ord("a") + index) * 64 for index in range(8)],
            ["lid", "lid"],
        )
        for value in accepted:
            with self.subTest(sources=value):
                self.assertEqual(_request(sources=value)["sources"], value or [])
        refused = (
            "lid",
            ("lid",),
            {"lid": 1},
            ["a"] * 9,
            [""],
            ["a" * 65],
            [1],
            [None],
            ["lid\n"],
            ["lid\t"],
            ["lid\x7f"],
            ["tapañ"],
            [["lid"]],
        )
        for value in refused:
            with self.subTest(sources=value):
                with self.assertRaisesRegex(ValueError, "^bad_sources$"):
                    _request(sources=value)

    def test_forwarded_sources_are_a_copy(self) -> None:
        names = ["lid"]
        args = _request(sources=names)
        names.append("tray")
        self.assertEqual(args["sources"], ["lid"])


class AnimTimelineBridgeResultTest(unittest.TestCase):
    def test_bools_are_typed_and_nothing_else_changes(self) -> None:
        raw = _bridge_result(_sample(), _sample(t_s=0.9, edge=0, hands_present=0, phases=[0.0, 0.0]))
        expected = copy.deepcopy(raw)
        timeline = expected["timeline"]
        assert isinstance(timeline, dict)
        timeline.update(active=False, complete=True, overflow=False, eof=True)
        timeline["samples"][0].update(edge=True, player_present=True, callback_both=False, hands_present=True)
        timeline["samples"][1].update(edge=False, player_present=True, callback_both=False, hands_present=False)
        self.assertEqual(anim_timeline.normalize_bridge_result(raw), expected)

    def test_json_bools_pass_and_no_field_is_added(self) -> None:
        raw = _bridge_result(_sample(edge=True, player_present=False, callback_both=True, hands_present=False))
        timeline = raw["timeline"]
        assert isinstance(timeline, dict)
        timeline.update(active=True, complete=False, overflow=True, eof=False)
        before = copy.deepcopy(raw)
        self.assertEqual(anim_timeline.normalize_bridge_result(raw), before)

    def test_bad_shapes_and_non_binary_bools_fail_closed(self) -> None:
        mutations = (
            (lambda r: r.pop("timeline"), "bad_bridge_timeline_result"),
            (lambda r: r.update(timeline=[]), "bad_bridge_timeline_result"),
            (lambda r: r["timeline"].update(samples={}), "bad_bridge_timeline_result"),
            (lambda r: r["timeline"]["samples"].append("x"), "bad_bridge_timeline_result"),
            (lambda r: r["timeline"].pop("overflow"), "bad_bridge_timeline_boolean"),
            (lambda r: r["timeline"].update(active=2), "bad_bridge_timeline_boolean"),
            (lambda r: r["timeline"].update(eof="true"), "bad_bridge_timeline_boolean"),
            (lambda r: r["timeline"]["samples"][0].pop("edge"), "bad_bridge_timeline_boolean"),
            (lambda r: r["timeline"]["samples"][0].update(hands_present=1.0), "bad_bridge_timeline_boolean"),
        )
        for index, (mutate, error) in enumerate(mutations):
            with self.subTest(case=index):
                raw = _bridge_result(_sample())
                mutate(raw)
                with self.assertRaisesRegex(ValueError, f"^{error}$"):
                    anim_timeline.normalize_bridge_result(raw)
        with self.assertRaisesRegex(ValueError, "^bad_bridge_timeline_result$"):
            anim_timeline.normalize_bridge_result([])


class AnimTimelineIngressTest(unittest.TestCase):
    def test_command_is_client_only_mutating_and_validated(self) -> None:
        self.assertIn("anim_timeline", loopback.CLIENT_COMMANDS)
        self.assertNotIn("anim_timeline", loopback.SERVER_COMMANDS)
        self.assertNotIn("anim_timeline", READ_ONLY_COMMANDS)
        self.assertTrue(command_requires_lease("anim_timeline"))
        self.assertEqual(loopback.peer_for_command("anim_timeline"), "client")

        state = loopback.ServerState("test-key")
        from tests.fence_helpers import bind_both_peers

        bind_both_peers(state)
        valid = _request(mode="start", trace_id="", sources=["lid"])
        status, body = state.enqueue_command("anim_timeline", valid)
        self.assertEqual((status, body["peer"]), (200, "client"))
        for bad in (
            valid | {"mode": "dump"},
            valid | {"sample_hz": 9},
            valid | {"max_samples": 8193},
            valid | {"sources": ["a"] * 9},
            valid | {"sources": ["a" * 65]},
            valid | {"extra": 1},
        ):
            with self.subTest(bad=bad):
                self.assertEqual(
                    state.enqueue_command("anim_timeline", bad),
                    (400, {"error": "bad_args"}),
                )

    def test_every_request_the_tool_builds_passes_the_ingress(self) -> None:
        for mode in sorted(anim_timeline.TIMELINE_MODES):
            trace_id = "" if mode == "start" else TRACE_ID
            for sources in (None, [], ["a" * 64] * 8):
                with self.subTest(mode=mode, sources=sources):
                    args = _request(mode=mode, trace_id=trace_id, sources=sources, sample_hz=10)
                    self.assertEqual(loopback.validate_command_args("anim_timeline", args), (True, None))

    def test_the_timeline_survives_prune_only_on_its_own_verb(self) -> None:
        self.assertIn("timeline", result_prune.PRUNABLE_FIELDS)
        filled = _bridge_result()
        self.assertIn("timeline", result_prune.prune_unfilled_fields("anim_timeline", filled))
        self.assertNotIn(
            "timeline",
            result_prune.prune_unfilled_fields("camera_get", {"ok": 1, "timeline": None}),
        )


class AnimTimelineToolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def test_tool_is_registered_with_the_brief_signature(self) -> None:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        self.assertIn("anim_timeline", tools)
        schema = tools["anim_timeline"].inputSchema
        self.assertEqual(schema.get("required"), ["mode"])
        properties = schema["properties"]
        for name, default in (
            ("trace_id", ""),
            ("cursor", 0),
            ("limit", 64),
            ("sample_hz", 20),
            ("max_samples", 4096),
            ("sources", None),
        ):
            with self.subTest(param=name):
                self.assertEqual(properties[name].get("default"), default)

    async def test_description_names_the_limits_and_the_recipe(self) -> None:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        description = tools["anim_timeline"].description or ""
        for phrase in (
            server.LEASE_TOOL_LINE,
            "Client side only: the server is not sampled in this version.",
            "client tick time",
            "read from the item in hands",
            "action_use started=true is not proof the action ran",
            "Recipe: start with sources, then action_use, then read",
            "then clear.",
            "trace_exists",
            "overflow=true",
            "edge=true",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, description)

    async def test_start_forwards_exact_args_to_the_client_peer(self) -> None:
        raw = _bridge_result(_sample())
        with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value=raw)) as call:
            result = _content_json(
                await self.app.call_tool(
                    "anim_timeline",
                    {
                        "mode": "start",
                        "sample_hz": 30,
                        "max_samples": 128,
                        "sources": ["lid", "tray"],
                        "timeout_s": 1.0,
                    },
                )
            )
        args = call.await_args.args
        self.assertEqual(args[0], "anim_timeline")
        self.assertEqual(args[2], "client")
        self.assertEqual(set(args[1]), WIRE_KEYS)
        self.assertRegex(args[1]["trace_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(
            {key: value for key, value in args[1].items() if key != "trace_id"},
            {
                "mode": "start",
                "cursor": 0,
                "limit": 64,
                "sample_hz": 30,
                "max_samples": 128,
                "sources": ["lid", "tray"],
            },
        )
        # The samples come back as the bridge wrote them, bools typed.
        sample = result["timeline"]["samples"][0]
        self.assertEqual(sample["action"], "ActionPourLiquid")
        self.assertEqual(sample["state_name"], "LOOP_LOOP")
        self.assertEqual(sample["phases"], [0.25, 1.0])
        self.assertIs(sample["edge"], True)
        self.assertEqual(result["timeline"]["stop_reason"], "requested")
        self.assertEqual(set(result), {"ok", "timeline"})

    async def test_omitted_sources_travel_as_an_empty_list(self) -> None:
        raw = _bridge_result()
        with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value=raw)) as call:
            await self.app.call_tool(
                "anim_timeline",
                {"mode": "read", "trace_id": TRACE_ID, "cursor": 64, "limit": 8},
            )
        self.assertEqual(call.await_args.args[1]["sources"], [])
        self.assertEqual(call.await_args.args[1]["cursor"], 64)

    async def test_invalid_requests_never_reach_the_bridge(self) -> None:
        cases = (
            ({"mode": "dump", "trace_id": TRACE_ID}, "bad_mode"),
            ({"mode": "start", "trace_id": TRACE_ID}, "bad_trace_id"),
            ({"mode": "start", "sample_hz": 9}, "bad_sample_hz"),
            ({"mode": "start", "sources": ["a"] * 9}, "bad_sources"),
            ({"mode": "start", "sources": ["lid\n"]}, "bad_sources"),
        )
        for arguments, error in cases:
            with self.subTest(arguments=arguments):
                with patch.object(self.runtime, "call_bridge", new=AsyncMock()) as call:
                    with self.assertRaisesRegex(Exception, error):
                        await self.app.call_tool("anim_timeline", arguments)
                call.assert_not_awaited()

    async def test_a_malformed_bridge_answer_is_an_error_not_a_guess(self) -> None:
        raw = _bridge_result(_sample(edge=2))
        with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value=raw)):
            with self.assertRaisesRegex(Exception, "bad_bridge_timeline_boolean"):
                await self.app.call_tool(
                    "anim_timeline", {"mode": "read", "trace_id": TRACE_ID}
                )


if __name__ == "__main__":
    unittest.main()
