"""camera_path_play (d026), camera_set focus (702f) and hud_set (f427).

Contract, tool, admission and census checks run here. Text asserts on Enforce
are wiring only: they do not credit in-game behavior, which the PR lists for
the final round.
"""
from __future__ import annotations

import json
import math
import re
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from dayz_mcp import camera_path, loopback, result_prune, server
from dayz_mcp.bridge_errors import _public_enqueue_error
from dayz_mcp.server import ServerConfig, ToolError, build_app
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease
from tests._addon_paths import addon_root
from tests.bridge_client_capabilities_helpers import (
    _method_body,
    announced_caps,
    dispatch_census,
)
from tests.fence_helpers import INST_CLIENT, PID_CLIENT, bind_both_peers, bound_queue
from tests.mcp_helpers import _assert_tool_error, _content_json


MISSION = addon_root() / "scripts" / "5_Mission"
BRIDGE = MISSION / "MCPClientBridge.c"
MESSAGES = MISSION / "MCPMessages.c"
CAMERA_PATH_C = MISSION / "MCP_CameraPath.c"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bridge_capabilities_v1.json"
NEW_COMMANDS = ("camera_path_play", "camera_set_focus", "hud_set")
VALID_WIRE_ARGS: dict[str, dict[str, Any]] = {
    "camera_path_play": {"mode": "state", "path_id": ""},
    "camera_set_focus": {
        "cam_mode": "orient",
        "cam_pos": [0.0, 2.0, 0.0],
        "cam_orientation": [90.0, 0.0, 0.0],
        "fov": 0.0,
        "settle_ticks": 3,
        "focus_distance": 2.0,
        "focus_blur": 1.0,
    },
    "hud_set": {"visible": False},
}


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code(text: str) -> str:
    """Enforce source without // comments, so a comment cannot satisfy an assert."""
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def _flat(text: str) -> str:
    return " ".join(_code(text).split())


def _class_body(source: str, name: str) -> str:
    return _method_body(source, f"class {name}")


def _members(body: str) -> list[tuple[str, str]]:
    found = []
    for line in _code(body).splitlines():
        match = re.fullmatch(r"\s*((?:ref )?[\w<> ]+?) (\w+);\s*", line)
        if match:
            found.append((match.group(1), match.group(2)))
    return found


def _tool_error_text(exc: BaseException, tool: str) -> str:
    message = str(exc)
    wrapper = f"Error executing tool {tool}: "
    return message[len(wrapper) :] if message.startswith(wrapper) else message


def _key(t: float, **overrides: object) -> dict[str, object]:
    key: dict[str, object] = {"t": t, "cam_pos": [t, 2.0, 0.0], "cam_orientation": [0.0, 0.0, 0.0]}
    key.update(overrides)
    return key


def _three_keys() -> list[dict[str, object]]:
    return [_key(0), _key(3.0), _key(6.0)]


class CameraPathContractTest(unittest.TestCase):
    def test_play_builds_the_exact_wire_args(self) -> None:
        keys = [
            {"t": 0, "cam_pos": [0, 2, 0], "look_at": [0, 1, 9], "fov": 1},
            {"t": 2.5, "cam_pos": [5.0, 2.0, 0.0], "look_at": [0.0, 1.0, 9.0], "fov": 0.5},
        ]
        args = camera_path.normalize_request("play", keys, "smooth", "orbit-2")
        self.assertEqual(
            args,
            {
                "mode": "play",
                "keys": [
                    {"t": 0.0, "cam_pos": [0.0, 2.0, 0.0], "look_at": [0.0, 1.0, 9.0], "fov": 1.0},
                    {"t": 2.5, "cam_pos": [5.0, 2.0, 0.0], "look_at": [0.0, 1.0, 9.0], "fov": 0.5},
                ],
                "easing": "smooth",
                "path_id": "orbit-2",
            },
        )
        # Numbers travel as floats: the Enforce key reads t and fov as float.
        self.assertIsInstance(args["keys"][0]["t"], float)
        self.assertEqual(loopback.validate_command_args("camera_path_play", args), (True, None))

    def test_state_and_stop_carry_mode_and_path_id_only(self) -> None:
        for mode in ("state", "stop"):
            with self.subTest(mode=mode):
                args = camera_path.normalize_request(mode, None, "smooth", "dolly_1")
                self.assertEqual(args, {"mode": mode, "path_id": "dolly_1"})
                self.assertEqual(loopback.validate_command_args("camera_path_play", args), (True, None))

    def test_refusals_name_what_is_wrong(self) -> None:
        cases = (
            (("pause", None, "smooth", ""), "mode must be one of play, state, stop"),
            (("play", [_key(0)], "smooth", ""), "keys must be a list of 2 to 64 keys"),
            (("play", [_key(float(i)) for i in range(65)], "smooth", ""), "2 to 64 keys"),
            (("play", None, "smooth", ""), "keys must be a list"),
            (("play", [_key(0.5), _key(1.0)], "smooth", ""), "keys[0].t must be 0"),
            (("play", [_key(0), _key(2.0), _key(2.0)], "smooth", ""), "keys[2].t must be greater than keys[1].t"),
            (("play", [_key(0), _key(600.5)], "smooth", ""), "keys[1].t must be at most 600 seconds"),
            (("play", [_key(0), _key(True)], "smooth", ""), "keys[1].t must be a finite number"),
            (("play", [_key(0), _key(float("nan"))], "smooth", ""), "keys[1].t must be a finite number"),
            (("play", [_key(0), _key(1.0, cam_pos=[1.0, 2.0])], "smooth", ""), "keys[1].cam_pos must be a list of 3 finite numbers"),
            (("play", [_key(0), _key(1.0, look_at=[0.0, 0.0, 1.0])], "smooth", ""), "keys[1] must carry exactly one of cam_orientation or look_at"),
            (
                ("play", [_key(0), {"t": 1.0, "cam_pos": [0.0, 0.0, 0.0], "look_at": [0.0, 0.0, 1.0]}], "smooth", ""),
                "keys[1] carries look_at and keys[0] carries cam_orientation",
            ),
            (("play", [_key(0, fov=0.9), _key(1.0)], "smooth", ""), "fov goes on every key or on none"),
            (("play", [_key(0, fov=0.0), _key(1.0, fov=0.0)], "smooth", ""), "keys[0].fov must be a finite number of radians above 0"),
            (("play", [_key(0), _key(1.0, focus_distance=2.0)], "smooth", ""), "keys[1] has unknown fields ['focus_distance']"),
            (("play", [_key(0), [1.0]], "smooth", ""), "keys[1] must be an object"),
            (("play", _three_keys(), "ease", ""), "easing must be linear or smooth"),
            (("play", _three_keys(), "smooth", "a" * 65), "path_id must be up to 64 characters"),
            (("play", _three_keys(), "smooth", "dolly 1"), "path_id must be up to 64 characters"),
            (("state", _three_keys(), "smooth", ""), "keys travel with mode=play only"),
        )
        for args, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(ValueError) as caught:
                    camera_path.normalize_request(*args)
                self.assertTrue(str(caught.exception).startswith("bad_args: "), caught.exception)
                self.assertIn(expected, str(caught.exception))

    def test_ingress_answers_the_fixed_token_only(self) -> None:
        refused = dict(VALID_WIRE_ARGS["camera_path_play"], mode="play", keys=[_key(0)], easing="linear")
        self.assertEqual(camera_path.validate_command_args(refused), (False, "bad_args"))
        self.assertEqual(camera_path.validate_command_args(["play"]), (False, "bad_args"))

    def test_the_bounds_mirror_the_bridge(self) -> None:
        code = _code(_source(CAMERA_PATH_C))

        def constant(name: str) -> str:
            match = re.search(rf"static const \w+ {name} = ([^;]+);", code)
            self.assertIsNotNone(match, name)
            return match.group(1)

        self.assertEqual(int(constant("KEYS_MIN")), camera_path.KEYS_MIN)
        self.assertEqual(int(constant("KEYS_MAX")), camera_path.KEYS_MAX)
        self.assertEqual(float(constant("DURATION_MAX_S")), camera_path.DURATION_MAX_S)
        self.assertEqual(int(constant("PATH_ID_MAX")), camera_path.PATH_ID_MAX_CHARS)
        chars = json.loads(constant("PATH_ID_CHARS"))
        self.assertEqual(len(set(chars)), len(chars))
        for code_point in range(128):
            character = chr(code_point)
            with self.subTest(character=character):
                self.assertEqual(camera_path.is_path_id(character), character in chars)
        self.assertEqual((camera_path.KEYS_MIN, camera_path.KEYS_MAX), (2, 64))
        self.assertEqual(camera_path.DURATION_MAX_S, 600.0)
        self.assertEqual(camera_path.PATH_STATES, ("idle", "playing", "finished", "aborted"))


class _ToolCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        self.tools = {tool.name: tool for tool in self.app._tool_manager.list_tools()}

    async def _forwarded(
        self, tool: str, arguments: dict[str, Any], answer: dict[str, Any] | None = None
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        bridge = AsyncMock(return_value=dict(answer or {"ok": 1}))
        with patch.object(self.runtime, "call_bridge", new=bridge):
            result = _content_json(await self.app.call_tool(tool, arguments))
        bridge.assert_awaited_once()
        return bridge.await_args.args, result

    async def _refused(self, tool: str, arguments: dict[str, Any]) -> str:
        bridge = AsyncMock(return_value={"ok": 1})
        with patch.object(self.runtime, "call_bridge", new=bridge):
            with self.assertRaises(Exception) as caught:
                await self.app.call_tool(tool, arguments)
        bridge.assert_not_awaited()
        _assert_tool_error(self, caught.exception)
        return _tool_error_text(caught.exception, tool)


class CameraPathToolTest(_ToolCase):
    async def test_every_mode_forwards_its_wire_args_to_the_client(self) -> None:
        args, _result = await self._forwarded(
            "camera_path_play",
            {"mode": "play", "keys": _three_keys(), "easing": "linear", "path_id": "dolly_1", "timeout_s": 2.0},
        )
        self.assertEqual(
            args,
            (
                "camera_path_play",
                {
                    "mode": "play",
                    "keys": [
                        {"t": 0.0, "cam_pos": [0.0, 2.0, 0.0], "cam_orientation": [0.0, 0.0, 0.0]},
                        {"t": 3.0, "cam_pos": [3.0, 2.0, 0.0], "cam_orientation": [0.0, 0.0, 0.0]},
                        {"t": 6.0, "cam_pos": [6.0, 2.0, 0.0], "cam_orientation": [0.0, 0.0, 0.0]},
                    ],
                    "easing": "linear",
                    "path_id": "dolly_1",
                },
                "client",
                2.0,
            ),
        )
        args, _result = await self._forwarded("camera_path_play", {"mode": "play", "keys": _three_keys()})
        self.assertEqual(args[1]["easing"], "smooth")
        self.assertEqual(args[1]["path_id"], "")
        self.assertEqual(args[3], server.DEFAULT_TOOL_TIMEOUT_S)
        args, _result = await self._forwarded("camera_path_play", {"mode": "state"})
        self.assertEqual(args[:3], ("camera_path_play", {"mode": "state", "path_id": ""}, "client"))
        args, _result = await self._forwarded("camera_path_play", {"mode": "stop", "path_id": "dolly_1"})
        self.assertEqual(args[:3], ("camera_path_play", {"mode": "stop", "path_id": "dolly_1"}, "client"))

    async def test_the_path_state_comes_back_as_the_bridge_read_it(self) -> None:
        answer = {
            "id": 7,
            "ok": 1,
            "camera_path": {"state": "playing", "t_s": 1.25, "duration_s": 6.0, "path_id": "dolly_1"},
        }
        _args, result = await self._forwarded("camera_path_play", {"mode": "state"}, answer)
        self.assertEqual(result["camera_path"], answer["camera_path"])

    async def test_bad_requests_never_reach_the_bridge(self) -> None:
        for arguments, expected in (
            ({"mode": "play", "keys": [_key(0)]}, "bad_args: keys must be a list of 2 to 64 keys"),
            ({"mode": "play", "keys": [_key(0), _key(3.0), _key(2.0)]}, "bad_args: keys[2].t must be greater"),
            ({"mode": "play", "keys": [_key(1.0), _key(2.0)]}, "bad_args: keys[0].t must be 0"),
            ({"mode": "play"}, "bad_args: keys must be a list"),
            ({"mode": "play", "keys": _three_keys(), "easing": "ease_in"}, "bad_args: easing must be linear or smooth"),
            ({"mode": "state", "keys": _three_keys()}, "bad_args: keys travel with mode=play only"),
            ({"mode": "stop", "path_id": "x" * 65}, "bad_args: path_id must be up to 64 characters"),
            ({"mode": "rewind"}, "bad_args: mode must be one of play, state, stop"),
            ({"mode": 1}, "valid string"),
            ({"mode": "play", "keys": "dolly"}, "valid list"),
            ({"mode": "state", "path_id": 5}, "valid string"),
            ({"mode": "state", "timeout_s": 0.0}, "bad_timeout"),
        ):
            with self.subTest(arguments=arguments):
                self.assertIn(expected, await self._refused("camera_path_play", arguments))

    def test_the_description_states_the_contract(self) -> None:
        description = self.tools["camera_path_play"].description or ""
        self.assertTrue(description.startswith(server.LEASE_TOOL_LINE))
        for phrase in (
            "2 to 64",
            "at most 600",
            "there is no Camera.InterpolateTo",
            "Catmull-Rom",
            "the short way round",
            "camera_path {state, t_s, duration_s, path_id}",
            "idle (no play yet), playing, finished or aborted",
            "Read the pose with camera_get",
            "path_busy",
            "path_not_found",
            "client_not_in_game",
            "camera_create_failed",
            "bridge_capability_missing",
            "left with restore_gameplay",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, description)


def _camera_answer(**overrides: object) -> dict[str, Any]:
    answer: dict[str, Any] = {
        "id": 1,
        "ok": 1,
        "camera": {"ok": 1, "view": "scripted", "applied_mode": "orient", "viewport_moved": 1, "fov": 0.0},
    }
    answer.update(overrides)
    return answer


_ORIENT = {"cam_mode": "orient", "cam_pos": [1.0, 2.0, 3.0], "cam_orientation": [10.0, 0.0, 0.0]}


class CameraSetFocusTest(_ToolCase):
    async def test_focus_goes_out_on_camera_set_focus_and_is_echoed(self) -> None:
        for label, arguments in (
            ("orient", dict(_ORIENT)),
            ("lookat", {"cam_mode": "lookat", "cam_pos": [1.0, 2.0, 3.0], "look_at": [4.0, 5.0, 6.0]}),
            ("matrix", {"cam_mode": "matrix", "cam_matrix": [1.0] * 12}),
            ("free", {"cam_mode": "free", "cam_pos": [1.0, 2.0, 3.0], "cam_orientation": [0.0, 0.0, 0.0]}),
        ):
            with self.subTest(cam_mode=label):
                args, result = await self._forwarded(
                    "camera_set",
                    dict(arguments, focus_distance=2.0, focus_blur=0.0, timeout_s=1.0),
                    _camera_answer(),
                )
                self.assertEqual(args[0], "camera_set_focus")
                self.assertEqual(args[1]["focus_distance"], 2.0)
                self.assertEqual(args[1]["focus_blur"], 0.0)
                self.assertEqual(loopback.validate_command_args(args[0], args[1]), (True, None))
                self.assertEqual(result["focus_applied"], {"distance": 2.0, "blur": 0.0})
                self.assertNotIn("focus_applied", result["camera"])

    async def test_omitted_focus_stays_camera_set_and_echoes_null(self) -> None:
        args, result = await self._forwarded("camera_set", dict(_ORIENT, fov=1.1), _camera_answer())
        self.assertEqual(args[0], "camera_set")
        self.assertNotIn("focus_distance", args[1])
        self.assertNotIn("focus_blur", args[1])
        self.assertIn("focus_applied", result)
        self.assertIsNone(result["focus_applied"])
        self.assertEqual(result["fov_applied"], 1.1)

    async def test_bad_focus_never_reaches_the_bridge(self) -> None:
        for extra, expected in (
            ({"focus_distance": 2.0}, "bad_args: focus_distance and focus_blur go together"),
            ({"focus_blur": 1.0}, "bad_args: focus_distance and focus_blur go together"),
            ({"focus_distance": 0.0, "focus_blur": 1.0}, "bad_args: focus_distance 0.0 must be a finite number of metres above 0"),
            ({"focus_distance": -2.0, "focus_blur": 1.0}, "bad_args: focus_distance -2.0"),
            ({"focus_distance": math.inf, "focus_blur": 1.0}, "bad_args: focus_distance inf"),
            ({"focus_distance": math.nan, "focus_blur": 1.0}, "bad_args: focus_distance nan"),
            ({"focus_distance": 2.0, "focus_blur": -0.5}, "bad_args: focus_blur -0.5 must be a finite number of 0 or more"),
            ({"focus_distance": 2.0, "focus_blur": math.inf}, "bad_args: focus_blur inf"),
            ({"focus_distance": True, "focus_blur": 1.0}, "valid number"),
            ({"focus_distance": 2.0, "focus_blur": "1"}, "valid number"),
        ):
            with self.subTest(extra=extra):
                self.assertIn(expected, await self._refused("camera_set", dict(_ORIENT, **extra)))

    async def test_a_failed_or_illegible_apply_claims_no_focus(self) -> None:
        bridge = AsyncMock(side_effect=ToolError("camera_create_failed"))
        with patch.object(self.runtime, "call_bridge", new=bridge):
            with self.assertRaises(ToolError) as caught:
                await self.app.call_tool("camera_set", dict(_ORIENT, focus_distance=2.0, focus_blur=1.0))
        self.assertIn("camera_create_failed", str(caught.exception))
        self.assertNotIn("focus_applied", str(caught.exception))
        illegible = _camera_answer(camera={"ok": 0, "view": "", "error": "camera_unavailable_no_scripted_camera"})
        _args, result = await self._forwarded(
            "camera_set", dict(_ORIENT, focus_distance=2.0, focus_blur=1.0), illegible
        )
        self.assertNotIn("focus_applied", result)

    def test_the_description_names_the_echo(self) -> None:
        description = self.tools["camera_set"].description or ""
        for phrase in (
            "focus_distance (metres, finite, above 0) and focus_blur (finite, 0 or more) go together",
            "Camera.SetFocus(focus_distance, focus_blur)",
            "focus_applied {distance, blur}",
            "focus_applied is null",
            "an echo of the values sent, not a read-back",
            "depth-of-field video option, which is not measured",
            "bridge_capability_missing",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, description)


class HudSetToolTest(_ToolCase):
    async def test_visible_is_forwarded_as_a_bool(self) -> None:
        for visible in (False, True):
            with self.subTest(visible=visible):
                args, _result = await self._forwarded("hud_set", {"visible": visible})
                self.assertEqual(args, ("hud_set", {"visible": visible}, "client", server.DEFAULT_TOOL_TIMEOUT_S))
        args, _result = await self._forwarded("hud_set", {"visible": True, "timeout_s": 2.0})
        self.assertEqual(args[3], 2.0)

    async def test_the_flags_come_back_as_the_bridge_read_them(self) -> None:
        answer = {"id": 3, "ok": 1, "hud": {"visible": 0, "hud_hide": 1, "quickbar_hide": 1}}
        _args, result = await self._forwarded("hud_set", {"visible": False}, answer)
        self.assertEqual(result["hud"], answer["hud"])

    async def test_a_value_that_is_not_a_bool_never_reaches_the_bridge(self) -> None:
        for arguments, expected in (
            ({}, ("\nvisible\n", "Field required")),
            ({"visible": 1}, ("\nvisible\n", "valid boolean")),
            ({"visible": "false"}, ("\nvisible\n", "valid boolean")),
            ({"visible": None}, ("\nvisible\n", "valid boolean")),
            ({"visible": True, "timeout_s": 0.0}, ("bad_timeout",)),
        ):
            with self.subTest(arguments=arguments):
                text = await self._refused("hud_set", arguments)
                for part in expected:
                    self.assertIn(part, text)

    def test_the_description_states_the_contract(self) -> None:
        description = self.tools["hud_set"].description or ""
        self.assertTrue(description.startswith(server.LEASE_TOOL_LINE))
        for phrase in (
            "without touching the controls",
            "HUD_HIDE",
            "QUICKBAR_HIDE",
            "HUD_DISABLE, the options menu's switch, is not touched",
            "neither is the HUD hiding of camera_set's scripted camera",
            "hud {visible, hud_hide, quickbar_hide}",
            "read back with IsContextFlagActive",
            "not an echo",
            "restore_gameplay calls neither setter, so hud_set(visible=true) is what brings the HUD back",
            "client_not_in_game",
            "hud_unavailable",
            "bridge_capability_missing",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, description)


class OlderBridgeAdmissionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        bind_both_peers(self.state)
        self.full = sorted(server._BRIDGE_COMMAND_TOOLS["client"])
        self.older = [name for name in self.full if name not in NEW_COMMANDS]

    def _announce(self, names: list[str]) -> tuple[int, dict]:
        return self.state.record_poll(
            "client", "12~1.29", instance=INST_CLIENT, source_pid=PID_CLIENT, caps=",".join(names)
        )

    def _enqueue(self, command: str) -> tuple[int, dict]:
        return self.state.enqueue_command(
            command, dict(VALID_WIRE_ARGS[command]), peer="client", operation_timeout_s=30.0
        )

    def test_an_older_census_refuses_each_verb_before_anything_is_queued(self) -> None:
        self._announce(self.older)
        for command in NEW_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(self._enqueue(command), (409, {"error": "bridge_capability_missing"}))
        self.assertEqual(bound_queue(self.state, "client"), [])
        # The tool sees the code itself, not remote_error.
        self.assertEqual(
            _public_enqueue_error({"error": "bridge_capability_missing"}), "bridge_capability_missing"
        )

    def test_the_current_census_admits_them(self) -> None:
        self._announce(self.full)
        for command in NEW_COMMANDS:
            with self.subTest(command=command):
                status, body = self._enqueue(command)
                self.assertEqual(status, 200, body)
        self.assertEqual([row["cmd"] for row in bound_queue(self.state, "client")], list(NEW_COMMANDS))

    def test_delivery_checks_the_census_again(self) -> None:
        self._announce(self.full)
        status, body = self._enqueue("camera_path_play")
        self.assertEqual(status, 200, body)
        status, poll = self._announce(self.older)
        self.assertEqual(status, 200)
        self.assertEqual(poll["commands"], [])
        self.assertEqual(
            self.state._results[body["id"]],
            {"id": body["id"], "ok": False, "error": "bridge_capability_missing"},
        )

    def test_action_hold_keeps_its_own_code(self) -> None:
        self._announce([name for name in self.full if name != "action_hold"])
        status, body = self.state.enqueue_command(
            "action_hold",
            {"action": "ActionDeployObject", "selector": "world", "hold_timeout_s": 1.0},
            peer="client",
            operation_timeout_s=30.0,
        )
        self.assertEqual((status, body), (409, {"error": "hold_not_supported"}))

    def test_every_new_verb_needs_a_lease(self) -> None:
        for command in NEW_COMMANDS:
            with self.subTest(command=command):
                self.assertTrue(command_requires_lease(command))
                self.assertNotIn(command, READ_ONLY_COMMANDS)
                self.assertIn(command, loopback.CLIENT_COMMANDS)
                self.assertNotIn(command, loopback.SERVER_COMMANDS)


class CensusTest(unittest.TestCase):
    def test_the_client_announces_and_dispatches_each_command(self) -> None:
        source = _source(BRIDGE)
        announced = announced_caps(source)
        census = dispatch_census(source)
        for command in NEW_COMMANDS:
            with self.subTest(command=command):
                self.assertIn(command, announced)
                self.assertIn(command, census)

    def test_the_fixture_and_the_readiness_map_name_their_tools(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))["peers"]["client"]
        expected = {"camera_path_play": "camera_path_play", "camera_set_focus": "camera_set", "hud_set": "hud_set"}
        for command, tool in expected.items():
            with self.subTest(command=command):
                self.assertEqual(fixture[command], tool)
                self.assertEqual(server._BRIDGE_COMMAND_TOOLS["client"][command], tool)

    def test_the_version_and_the_server_arg_contract_hash_stay(self) -> None:
        for command in NEW_COMMANDS:
            self.assertNotIn(command, server.SERVER_ARG_CONTRACT)
        self.assertEqual(server.EXPECTED_SERVER_ARG_CONTRACT_HASH, "4f90284258023d98")
        self.assertIn('const string MCP_BRIDGE_VERSION = "12";', _source(MESSAGES))


class EnforceWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = _source(BRIDGE)
        cls.messages = _source(MESSAGES)
        cls.path = _source(CAMERA_PATH_C)

    def test_the_wire_classes(self) -> None:
        self.assertEqual(
            _members(_class_body(self.messages, "MCPPathKey")),
            [
                ("float", "t"),
                ("ref array<float>", "cam_pos"),
                ("ref array<float>", "cam_orientation"),
                ("ref array<float>", "look_at"),
                ("float", "fov"),
            ],
        )
        self.assertEqual(
            _members(_class_body(self.messages, "MCPCameraPathState")),
            [("string", "state"), ("float", "t_s"), ("float", "duration_s"), ("string", "path_id")],
        )
        self.assertEqual(
            _members(_class_body(self.messages, "MCPHudState")),
            [("bool", "visible"), ("bool", "hud_hide"), ("bool", "quickbar_hide")],
        )
        args = _members(_class_body(self.messages, "MCPArgs"))
        for member in (
            ("ref array<ref MCPPathKey>", "keys"),
            ("string", "easing"),
            ("string", "path_id"),
            ("float", "focus_distance"),
            ("float", "focus_blur"),
            ("bool", "visible"),
        ):
            self.assertIn(member, args)
        result = _members(_class_body(self.messages, "MCPResult"))
        self.assertEqual(result[-2:], [("ref MCPCameraPathState", "camera_path"), ("ref MCPHudState", "hud")])
        self.assertIn(("bool", "camera_focus"), _members(_class_body(self.messages, "MCPJob")))

    def test_the_blocks_are_prunable_and_kept_when_filled(self) -> None:
        self.assertEqual(result_prune.PRUNABLE_FIELDS[-2:], ("camera_path", "hud"))
        path = {"state": "idle", "t_s": 0.0, "duration_s": 0.0, "path_id": ""}
        kept = result_prune.prune_unfilled_fields("camera_path_play", {"ok": 1, "camera_path": dict(path), "hud": {}})
        self.assertEqual(kept["camera_path"], path)
        self.assertNotIn("hud", kept)
        hud = {"visible": 1, "hud_hide": 0, "quickbar_hide": 0}
        kept = result_prune.prune_unfilled_fields("hud_set", {"ok": 1, "hud": dict(hud), "camera_path": {}})
        self.assertEqual(kept["hud"], hud)
        self.assertNotIn("camera_path", kept)

    def test_a_play_is_checked_before_anything_moves(self) -> None:
        body = _flat(_method_body(self.bridge, "protected bool DispatchCameraPathPlay("))
        order = [
            "error = candidate.Load(command.args);",
            'result.error = "path_busy";',
            'result.error = "busy";',
            "m_JobRunner.AddJob(job);",
        ]
        positions = [body.index(step) for step in order]
        self.assertEqual(positions, sorted(positions))
        query = [
            "if (!MCPCameraPath.IsValidPathId(command.args.path_id))",
            'result.error = "path_not_found";',
            "StopCameraPath();",
            "m_CameraPath.Fill(result.camera_path);",
        ]
        positions = [body.index(step) for step in query]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('if (mode != "state" && mode != "stop")', body)

    def test_the_path_moves_per_frame_on_the_scripted_camera(self) -> None:
        tick = _flat(_method_body(self.bridge, "void OnTick(float timeslice)"))
        self.assertLess(tick.index("TickCameraPath(timeslice);"), tick.index("m_JobRunner.Tick(timeslice, this);"))
        frame = _flat(_method_body(self.bridge, "protected void TickCameraPath(float timeslice)"))
        self.assertIn("if (!m_CameraPathCam || m_ActiveCam != m_CameraPathCam)", frame)
        self.assertLess(frame.index("m_CameraPath.Advance(timeslice);"), frame.index("m_CameraPath.Apply(m_CameraPathCam);"))
        play = _flat(_method_body(self.bridge, "protected bool ProcessCameraPathPlayJob(MCPJob job)"))
        for step in (
            "FinishCameraHandoff();",
            "SuppressGameplay();",
            "DeleteOwnedCamera();",
            'g_Game.CreateObject("staticcamera", path.StartPosition(), true);',
            "path.Begin();",
            "path.Apply(cam);",
            "cam.SetActive(true);",
        ):
            self.assertIn(step, play)
        self.assertIn(
            'if (cameraObject) { g_Game.ObjectDelete(cameraObject); } '
            'job.error = "camera_create_failed"; RestoreGameplay(); return true;',
            play,
        )
        for text in (_code(self.path), play, frame):
            self.assertNotIn("InterpolateTo", text)
            self.assertNotIn("_now(", text)
        self.assertNotIn("FreeDebugCamera", _code(self.path) + play + frame)

    def test_camera_set_and_restore_end_a_playing_path(self) -> None:
        apply = _flat(_method_body(self.bridge, "protected bool ApplyCameraSet(MCPJob job)"))
        self.assertLess(apply.index("StopCameraPath();"), apply.index("SuppressGameplay();"))
        restore = _flat(_method_body(self.bridge, "protected void RestoreGameplay()"))
        self.assertLess(restore.index("StopCameraPath();"), restore.index("if (!GetGame())"))
        stop = _flat(_method_body(self.bridge, "protected void StopCameraPath()"))
        self.assertEqual(stop, "if (m_CameraPath) { m_CameraPath.Abort(); } m_CameraPathCam = null;")
        abort = _flat(_method_body(self.path, "void Abort()"))
        self.assertEqual(abort, 'if (m_State == "playing") { m_State = "aborted"; }')

    def test_focus_is_set_only_for_camera_set_focus(self) -> None:
        dispatch = _flat(_method_body(self.bridge, "protected bool DispatchCameraSet("))
        self.assertIn('bool focus = command.cmd == "camera_set_focus";', dispatch)
        self.assertLess(dispatch.index("!IsValidCameraFocus(command.args)"), dispatch.index("HasExclusiveJob()"))
        self.assertIn("job.camera_focus = focus;", dispatch)
        for signature, camera in (
            ("protected bool ApplyCameraSet(MCPJob job)", "cam"),
            ("protected bool ApplyFreeCamera(MCPJob job, MCPCameraValidation validation)", "freeCam"),
        ):
            with self.subTest(apply=signature):
                body = _flat(_method_body(self.bridge, signature))
                self.assertIn(
                    f"if (job.camera_focus) {{ {camera}.SetFocus(job.args.focus_distance, job.args.focus_blur); }}",
                    body,
                )
                self.assertEqual(body.count("SetFocus("), 1)
        check = _flat(_method_body(self.bridge, "protected bool IsValidCameraFocus(MCPArgs args)"))
        self.assertIn("if (distance != distance || blur != blur)", check)
        self.assertIn("if (distance <= 0.0 || distance >= float.MAX)", check)
        self.assertIn("if (blur < 0.0 || blur >= float.MAX)", check)

    def test_hud_set_uses_the_hide_key_flags_and_reads_them_back(self) -> None:
        body = _flat(_method_body(self.bridge, "protected bool DispatchHudSet(MCPCommand command, MCPResult result)"))
        self.assertLess(body.index("hud.ShowHudPlayer(command.args.visible);"), body.index("state.hud_hide ="))
        self.assertIn("hud.ShowQuickbarPlayer(command.args.visible);", body)
        self.assertIn("state.hud_hide = visibility.IsContextFlagActive(EHudContextFlags.HUD_HIDE);", body)
        self.assertIn(
            "state.quickbar_hide = visibility.IsContextFlagActive(EHudContextFlags.QUICKBAR_HIDE);", body
        )
        self.assertIn("state.visible = !state.hud_hide && !state.quickbar_hide;", body)
        self.assertIn('result.error = "hud_unavailable";', body)
        for absent in ("HUD_DISABLE", ".Show(", "PlayerControl", "SetContextFlag"):
            self.assertNotIn(absent, body)

    def test_a_client_out_of_game_is_refused_before_any_handler(self) -> None:
        dispatch = _method_body(self.bridge, "protected void Dispatch(MCPCommand command)")
        gate = dispatch.index('result.error = "client_not_in_game";')
        for command in NEW_COMMANDS:
            with self.subTest(command=command):
                self.assertLess(gate, dispatch.index(f'if (command.cmd == "{command}")'))


if __name__ == "__main__":
    unittest.main()
