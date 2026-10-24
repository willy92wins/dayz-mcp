"""Scenes (inbox 165a, 5a21, 7f5d): scene_id on world_spawn, scene_entities,
scene_clear and world_spawn_batch.

The bridge keeps, per scene_id, the objects world_spawn and world_spawn_batch
created under it. These tests hold what can be checked offline: the tool and
daemon refusals, the older-PBO admission, read-only versus lease, the reply
shapes, and the Enforce source contract (IsValidSceneId runs translated).
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import loopback, server, tool_catalog
from dayz_mcp.bridge_readiness import (
    EXPECTED_SERVER_ARG_CONTRACT_HASH,
    SERVER_ARG_CONTRACT,
)
from dayz_mcp.core import ECE_INITAI, EXPECTED_BRIDGE_VERSION
from dayz_mcp.result_prune import prune_unfilled_fields
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import (
    EnforceString,
    SignedAsciiString,
    method_body,
    translate,
)
from tests.fence_helpers import INST_SERVER, PID_SERVER, bound_queue


BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
MESSAGES = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
SCENE_VERBS = ("world_spawn_batch", "scene_entities", "scene_clear")
# The arg-contract hash of the bridge before these verbs (main at f8cf3b4): what
# an older PBO of this line announces.
OLDER_PBO_ARG_CONTRACT_HASH = "4f90284258023d98"
ITEM = {"type": "Barrel_Green", "pos": [10.0, 2.0, 20.0]}
WIRE_ITEM = {"type": "Barrel_Green", "pos": [10.0, 2.0, 20.0], "flags": 0, "rotation": 0}
BAD_SCENE_IDS = ("a b", "a" * 65, "take.1", "a/b", "tomañ", "a\n", "-​")


def _content_json(content: Any) -> dict[str, Any]:
    if isinstance(content, tuple):
        blocks, structured = content
        if isinstance(structured, dict):
            return structured
        content = blocks
    parsed = json.loads(content[0].text)
    if not isinstance(parsed, dict):
        raise AssertionError(f"expected dict content, got {parsed!r}")
    return parsed


def _tool_error_text(exc: BaseException) -> str:
    text = str(exc)
    for attr in ("exceptions", "error"):
        nested = getattr(exc, attr, None)
        if nested:
            text = text + " " + str(nested)
    return text


def _bound_state(**kwargs: object) -> loopback.ServerState:
    state = loopback.ServerState(key="k", **kwargs)  # type: ignore[arg-type]
    state.daemon_generation = "gen-scenes"
    state.install_bound_peer(instance=INST_SERVER, role="server", pid=PID_SERVER, run_id="scenes-run")
    return state


def _announce(state: loopback.ServerState, commands: list[str], ach: str | None) -> None:
    state._peer_caps["server"] = {
        "generation": state.daemon_generation,
        "commands": list(commands),
        "reason": "ok",
        "arg_contract_hash": ach,
        "announced_at": state._now(),
        "instance": INST_SERVER,
    }


def _full_commands() -> list[str]:
    return sorted(server._BRIDGE_COMMAND_TOOLS["server"])


def _poll(state: loopback.ServerState, commands: list[str], ach: str):
    return state.record_poll(
        "server",
        f"{EXPECTED_BRIDGE_VERSION}~1.30.0",
        instance=INST_SERVER,
        source_pid=PID_SERVER,
        caps=",".join(commands),
        ach=ach,
    )


def _valid_args(verb: str) -> dict[str, Any]:
    if verb == "world_spawn_batch":
        return {"items": [dict(WIRE_ITEM)], "per_tick": 10, "scene_id": "a"}
    return {"scene_id": "a"}


class SceneAdmissionTest(unittest.TestCase):
    """An older PBO is never handed a scene verb, nor a world_spawn with scene_id."""

    def _refused(self, state: loopback.ServerState, cmd: str, args: dict[str, Any]) -> dict:
        before = len(bound_queue(state, "server"))
        status, body = state.enqueue_command(cmd, args, peer="server")
        self.assertEqual(status, 409, body)
        self.assertEqual(len(bound_queue(state, "server")), before)
        return body

    def test_the_verbs_are_server_commands_gated_like_new_verbs(self) -> None:
        for verb in SCENE_VERBS:
            with self.subTest(verb=verb):
                self.assertIn(verb, loopback.SERVER_COMMANDS)
                self.assertIn(verb, loopback._CAPABILITY_ADMISSION_COMMANDS)
                self.assertEqual(server._BRIDGE_COMMAND_TOOLS["server"][verb], verb)
        self.assertNotIn("world_spawn", loopback._CAPABILITY_ADMISSION_COMMANDS)
        self.assertEqual(SERVER_ARG_CONTRACT["scene_entities"], ("scene_id",))
        self.assertEqual(SERVER_ARG_CONTRACT["scene_clear"], ("scene_id",))
        self.assertEqual(SERVER_ARG_CONTRACT["world_spawn_batch"], ("items", "per_tick", "scene_id"))
        self.assertIn("scene_id", SERVER_ARG_CONTRACT["world_spawn"])
        self.assertNotEqual(EXPECTED_SERVER_ARG_CONTRACT_HASH, OLDER_PBO_ARG_CONTRACT_HASH)

    def test_an_older_census_refuses_every_scene_verb_before_a_queue_id(self) -> None:
        older = [name for name in _full_commands() if name not in SCENE_VERBS]
        for verb in SCENE_VERBS:
            for name, commands, ach, error in (
                ("verb_not_announced", older, EXPECTED_SERVER_ARG_CONTRACT_HASH, "bridge_capability_missing"),
                ("older_hash", _full_commands(), OLDER_PBO_ARG_CONTRACT_HASH, "arg_contract_mismatch"),
                ("no_hash", _full_commands(), None, "arg_contract_mismatch"),
            ):
                with self.subTest(verb=verb, census=name):
                    state = _bound_state()
                    _announce(state, commands, ach)
                    body = self._refused(state, verb, _valid_args(verb))
                    self.assertEqual(body["error"], error)
                    self.assertEqual(state._next_id, 1)

    def test_the_current_census_queues_each_verb(self) -> None:
        for verb in SCENE_VERBS:
            with self.subTest(verb=verb):
                state = _bound_state()
                _announce(state, _full_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
                status, body = state.enqueue_command(verb, _valid_args(verb), peer="server")
                self.assertEqual(status, 200, body)
                self.assertEqual([command["cmd"] for command in bound_queue(state, "server")], [verb])

    def test_world_spawn_with_scene_id_needs_the_current_hash_and_without_it_does_not(self) -> None:
        plain = dict(WIRE_ITEM)
        scened = {**WIRE_ITEM, "scene_id": "a"}
        state = _bound_state()
        _announce(state, _full_commands(), OLDER_PBO_ARG_CONTRACT_HASH)
        self.assertEqual(self._refused(state, "world_spawn", scened)["error"], "arg_contract_mismatch")
        status, body = state.enqueue_command("world_spawn", plain, peer="server")
        self.assertEqual(status, 200, body)
        state = _bound_state()
        _announce(state, _full_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        status, body = state.enqueue_command("world_spawn", scened, peer="server")
        self.assertEqual(status, 200, body)

    def test_delivery_to_an_older_pbo_drops_a_scened_spawn_but_not_a_plain_one(self) -> None:
        state = _bound_state()
        _announce(state, _full_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        status, scened = state.enqueue_command(
            "world_spawn", {**WIRE_ITEM, "scene_id": "a"}, peer="server"
        )
        self.assertEqual(status, 200, scened)
        status, plain = state.enqueue_command("world_spawn", dict(WIRE_ITEM), peer="server")
        self.assertEqual(status, 200, plain)
        status, polled = _poll(state, _full_commands(), OLDER_PBO_ARG_CONTRACT_HASH)
        self.assertEqual(status, 200)
        self.assertEqual([command["id"] for command in polled["commands"]], [plain["id"]])
        self.assertEqual(state.take_result(scened["id"])["error"], "arg_contract_mismatch")

    def test_delivery_drops_a_scene_verb_the_bound_peer_no_longer_announces(self) -> None:
        state = _bound_state()
        _announce(state, _full_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        status, body = state.enqueue_command("scene_clear", {"scene_id": "a"}, peer="server")
        self.assertEqual(status, 200, body)
        reduced = [name for name in _full_commands() if name != "scene_clear"]
        status, polled = _poll(state, reduced, EXPECTED_SERVER_ARG_CONTRACT_HASH)
        self.assertEqual(status, 200)
        self.assertEqual(polled["commands"], [])
        self.assertEqual(state.take_result(body["id"])["error"], "bridge_capability_missing")


class SceneLeaseTest(unittest.TestCase):
    def test_scene_entities_is_a_read_and_the_others_need_a_lease(self) -> None:
        self.assertIn("scene_entities", READ_ONLY_COMMANDS)
        self.assertFalse(command_requires_lease("scene_entities"))
        self.assertTrue(command_requires_lease("scene_clear"))
        self.assertTrue(command_requires_lease("world_spawn_batch"))
        self.assertIn("scene_entities", tool_catalog._INITIAL_READ_TOOL_NAMES)
        self.assertNotIn("scene_clear", tool_catalog._INITIAL_READ_TOOL_NAMES)
        self.assertNotIn("world_spawn_batch", tool_catalog._INITIAL_READ_TOOL_NAMES)

    def test_scene_id_rule_and_bounds(self) -> None:
        self.assertEqual(loopback.SCENE_ID_MAX_CHARS, 64)
        self.assertEqual(loopback.SPAWN_BATCH_ITEMS_MAX, 200)
        self.assertEqual(loopback.SPAWN_BATCH_PER_TICK_MAX, 50)
        for good in ("a", "A-z_0-9", "Z" * 64, "-", "_"):
            with self.subTest(good=good):
                self.assertTrue(loopback.is_scene_id(good))
        for bad in ("", *BAD_SCENE_IDS, None, 1, ["a"]):
            with self.subTest(bad=bad):
                self.assertFalse(loopback.is_scene_id(bad))


class SceneToolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        self.tools = {tool.name: tool for tool in self.app._tool_manager.list_tools()}

    async def _forwarded(self, tool: str, arguments: dict, answer: dict | None = None):
        call = AsyncMock(return_value={"ok": 1} if answer is None else answer)
        with patch.object(self.runtime, "call_bridge", new=call):
            result = await self.app.call_tool(tool, arguments)
        call.assert_awaited_once()
        return call.await_args.args, _content_json(result)

    async def _refused(self, tool: str, arguments: dict) -> str:
        call = AsyncMock(return_value={"ok": 1})
        with patch.object(self.runtime, "call_bridge", new=call):
            with self.assertRaises(Exception) as caught:
                await self.app.call_tool(tool, arguments)
        call.assert_not_awaited()
        return _tool_error_text(caught.exception)

    def test_signatures(self) -> None:
        self.assertEqual(
            set(self.tools["world_spawn_batch"].parameters["properties"]),
            {"items", "per_tick", "scene_id", "timeout_s"},
        )
        self.assertEqual(self.tools["world_spawn_batch"].parameters["required"], ["items"])
        self.assertEqual(self.tools["world_spawn_batch"].parameters["properties"]["per_tick"]["default"], 10)
        self.assertIs(self.tools["world_spawn_batch"].parameters.get("additionalProperties"), False)
        for name in ("scene_entities", "scene_clear"):
            with self.subTest(tool=name):
                self.assertEqual(set(self.tools[name].parameters["properties"]), {"scene_id", "timeout_s"})
                self.assertEqual(self.tools[name].parameters["required"], ["scene_id"])
        self.assertIn("scene_id", self.tools["world_spawn"].parameters["properties"])
        self.assertNotIn("scene_id", self.tools["world_spawn"].parameters.get("required", []))

    def test_descriptions_state_the_lease_and_the_session_scope(self) -> None:
        for name in ("world_spawn_batch", "scene_clear"):
            self.assertTrue(self.tools[name].description.startswith(server.LEASE_TOOL_LINE), name)
        self.assertFalse(self.tools["scene_entities"].description.startswith(server.LEASE_TOOL_LINE))
        self.assertIn("No lease", self.tools["scene_entities"].description)
        for name in ("world_spawn", "world_spawn_batch", "scene_entities", "scene_clear"):
            with self.subTest(tool=name):
                self.assertIn("does not survive a restart", self.tools[name].description)
        batch = self.tools["world_spawn_batch"].description
        self.assertIn("DayZInfected gets ECE_INITAI", batch)
        self.assertIn("partial", batch)
        self.assertIn("y null", batch)
        self.assertIn("never touches the server's storage", self.tools["scene_clear"].description)

    async def test_world_spawn_sends_scene_id_only_when_one_is_named(self) -> None:
        args, _result = await self._forwarded(
            "world_spawn", {"type": "Barrel_Green", "pos": [1.0, 2.0, 3.0], "scene_id": "take_1"}
        )
        self.assertEqual(args[1]["scene_id"], "take_1")
        args, _result = await self._forwarded("world_spawn", {"type": "Barrel_Green", "pos": [1.0, 2.0, 3.0]})
        self.assertNotIn("scene_id", args[1])
        args, _result = await self._forwarded(
            "world_spawn", {"type": "Barrel_Green", "pos": [1.0, 2.0, 3.0], "scene_id": ""}
        )
        self.assertNotIn("scene_id", args[1])

    async def test_a_bad_scene_id_is_bad_args_naming_it_on_every_tool(self) -> None:
        calls = (
            ("world_spawn", {"type": "Barrel_Green", "pos": [1.0, 2.0, 3.0]}),
            ("world_spawn_batch", {"items": [ITEM]}),
            ("scene_entities", {}),
            ("scene_clear", {}),
        )
        for tool, base in calls:
            for bad in BAD_SCENE_IDS:
                with self.subTest(tool=tool, scene_id=bad):
                    text = await self._refused(tool, {**base, "scene_id": bad})
                    self.assertIn("bad_args: scene_id", text)
        for tool in ("scene_entities", "scene_clear"):
            with self.subTest(tool=tool, scene_id=""):
                self.assertIn("bad_args: scene_id", await self._refused(tool, {"scene_id": ""}))

    async def test_scene_entities_and_scene_clear_send_the_scene_alone(self) -> None:
        for tool in ("scene_entities", "scene_clear"):
            with self.subTest(tool=tool):
                args, _result = await self._forwarded(tool, {"scene_id": "Take-01_b", "timeout_s": 4})
                self.assertEqual(args, (tool, {"scene_id": "Take-01_b"}, "server", 4.0))

    async def test_batch_wire_items(self) -> None:
        args, _result = await self._forwarded(
            "world_spawn_batch",
            {
                "items": [
                    {"type": "Barrel_Green", "pos": [1, None, 3]},
                    {"type": "Barrel_Green", "pos": [1, 2, 3], "rotation": 2, "flags": 1060},
                    {"type": "ZmbM_CitizenASkinny", "pos": [1.5, 0, 3.5], "ypr": [90, -5, 0]},
                ],
                "per_tick": 5,
                "scene_id": "a",
            },
        )
        self.assertEqual(args[0], "world_spawn_batch")
        self.assertEqual(
            args[1],
            {
                "items": [
                    {"type": "Barrel_Green", "pos": [1.0, 0.0, 3.0], "flags": 0, "rotation": 0, "ground": True},
                    {"type": "Barrel_Green", "pos": [1.0, 2.0, 3.0], "flags": 1060, "rotation": 2},
                    {
                        "type": "ZmbM_CitizenASkinny",
                        "pos": [1.5, 0.0, 3.5],
                        "flags": 0,
                        "rotation": 0,
                        "ypr": [90.0, -5.0, 0.0],
                        "ypr_set": True,
                    },
                ],
                "per_tick": 5,
                "scene_id": "a",
            },
        )
        self.assertEqual(loopback.validate_command_args("world_spawn_batch", args[1]), (True, None))
        args, _result = await self._forwarded("world_spawn_batch", {"items": [ITEM] * 200, "per_tick": 50})
        self.assertEqual(len(args[1]["items"]), 200)
        self.assertNotIn("scene_id", args[1])

    async def test_item_count_and_per_tick_bounds_refuse_before_anything_is_sent(self) -> None:
        for arguments, part in (
            ({"items": []}, "bad_args: items holds 0 items"),
            ({"items": [ITEM] * 201}, "bad_args: items holds 201 items"),
            ({"items": [ITEM], "per_tick": 0}, "bad_args: per_tick"),
            ({"items": [ITEM], "per_tick": 51}, "bad_args: per_tick"),
            ({"items": [ITEM], "per_tick": True}, "per_tick"),
            ({"items": [ITEM], "scene": "a"}, "unexpected arguments: scene"),
        ):
            with self.subTest(arguments={k: v for k, v in arguments.items() if k != "items"}):
                self.assertIn(part, await self._refused("world_spawn_batch", arguments))

    async def test_a_malformed_item_refuses_the_call_naming_its_index(self) -> None:
        cases = (
            # An item that is not an object is refused at the FastMCP frontier
            # (items is a list of objects), which names it items.3.
            ("Barrel_Green", "items.3"),
            ({"pos": [1.0, 2.0, 3.0]}, "bad_args: items[3].type"),
            ({"type": "", "pos": [1.0, 2.0, 3.0]}, "bad_args: items[3].type"),
            ({"type": "Barrel_Green"}, "bad_args: items[3].pos"),
            ({"type": "Barrel_Green", "pos": [1.0, 2.0]}, "bad_args: items[3].pos"),
            ({"type": "Barrel_Green", "pos": [None, 2.0, 3.0]}, "bad_args: items[3].pos"),
            ({"type": "Barrel_Green", "pos": [1.0, float("nan"), 3.0]}, "bad_args: items[3].pos"),
            ({"type": "Barrel_Green", "pos": [1.0, True, 3.0]}, "bad_args: items[3].pos"),
            ({"type": "Barrel_Green", "pos": [10**400, 2.0, 3.0]}, "bad_args: items[3].pos"),
            ({**ITEM, "flags": 1}, "bad_args: items[3].flags"),
            ({**ITEM, "flags": 524288}, "bad_args: items[3].flags"),
            ({**ITEM, "flags": True}, "bad_args: items[3].flags"),
            ({**ITEM, "rotation": 1.5}, "bad_args: items[3].rotation"),
            ({**ITEM, "rotation": 0, "ypr": [0.0, 0.0, 0.0]}, "bad_args: items[3] takes rotation or ypr"),
            ({**ITEM, "ypr": [0.0, 0.0]}, "bad_args: items[3].ypr"),
            ({**ITEM, "ypr": [0.0, float("inf"), 0.0]}, "bad_args: items[3].ypr"),
            ({**ITEM, "lifetime_s": 60.0}, "bad_args: items[3] has unexpected keys ['lifetime_s']"),
        )
        for bad, part in cases:
            with self.subTest(item=repr(bad)):
                items = [ITEM, ITEM, ITEM, bad, ITEM]
                self.assertIn(part, await self._refused("world_spawn_batch", {"items": items}))

    async def test_batch_reply_rows_carry_only_what_is_true_of_them(self) -> None:
        answer = {
            "ok": 1,
            "world_spawn_batch": {
                "status": "partial",
                "created": 1,
                "failed": 1,
                "items": [
                    {"index": 0, "ok": 1, "object_id": 1000000000, "type": "Barrel_Green", "error": ""},
                    {"index": 1, "ok": 0, "object_id": 0, "type": "NoSuchClass", "error": "unknown_type"},
                ],
            },
        }
        _args, result = await self._forwarded(
            "world_spawn_batch",
            {"items": [ITEM, {"type": "NoSuchClass", "pos": [1.0, 2.0, 3.0]}]},
            answer,
        )
        batch = result["world_spawn_batch"]
        self.assertEqual((batch["status"], batch["created"], batch["failed"]), ("partial", 1, 1))
        self.assertEqual(
            batch["items"],
            [
                {"index": 0, "ok": True, "object_id": 1000000000},
                {"index": 1, "ok": False, "error": "unknown_type", "type": "NoSuchClass"},
            ],
        )

    async def test_scene_entities_rows_publish_deleted_as_a_bool(self) -> None:
        answer = {
            "ok": 1,
            "scene_entities": [
                {"object_id": 7, "type": "Barrel_Green", "deleted": 0, "pos": [1.0, 2.0, 3.0]},
                {"object_id": 8, "type": "Barrel_Green", "deleted": 1, "pos": []},
            ],
        }
        _args, result = await self._forwarded("scene_entities", {"scene_id": "a"}, answer)
        self.assertEqual(
            result["scene_entities"],
            [
                {"object_id": 7, "type": "Barrel_Green", "deleted": False, "pos": [1.0, 2.0, 3.0]},
                {"object_id": 8, "type": "Barrel_Green", "deleted": True},
            ],
        )

    async def test_an_unknown_scene_answers_an_empty_list_through_call_bridge(self) -> None:
        # The runtime's own call_bridge, prune included; only the loopback and
        # the readiness snapshot are faked. scene_entities [] is the answer.
        ready = {
            "server_peer": {
                "last_poll_age_s": 0.1,
                "version_state": "ok",
                "capabilities": {"state": "match", "reason": "ok"},
            },
            "client_peer": {"last_poll_age_s": 0.1, "version_state": "ok"},
        }
        wire = {"ok": 1, "scene_entities": [], "scene_clear": {}, "world_spawn_batch": {}, "players": []}
        state = SimpleNamespace(
            enqueue_command=lambda *_args, **_kwargs: (200, {"id": 3}),
            daemon_generation="generation-test",
            take_result=lambda *_args, **_kwargs: dict(wire),
        )
        with patch.object(self.runtime, "status", return_value=ready), patch.object(
            self.runtime, "loopback", SimpleNamespace(state=state)
        ):
            result = _content_json(await self.app.call_tool("scene_entities", {"scene_id": "nobody"}))
        self.assertEqual(result["ok"], 1)
        self.assertEqual(result["scene_entities"], [])
        self.assertNotIn("scene_clear", result)
        self.assertNotIn("world_spawn_batch", result)

    def test_the_empty_scene_list_is_kept_for_its_own_verb_only(self) -> None:
        wire = {"ok": 1, "scene_entities": [], "scene_clear": {}, "world_spawn_batch": {}}
        self.assertEqual(prune_unfilled_fields("scene_entities", dict(wire)), {"ok": 1, "scene_entities": []})
        self.assertEqual(prune_unfilled_fields("scene_clear", dict(wire)), {"ok": 1})
        self.assertEqual(prune_unfilled_fields("world_spawn", dict(wire)), {"ok": 1})


class SceneBridgeSourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = BRIDGE.read_text(encoding="utf-8")
        cls.messages = MESSAGES.read_text(encoding="utf-8")

    def _body(self, signature: str) -> str:
        return " ".join(method_body(self.bridge, signature).split())

    def test_bridge_version_and_constants_match_the_python_mirrors(self) -> None:
        self.assertIn('const string MCP_BRIDGE_VERSION = "12";', self.messages)
        for name, value in (
            ("SCENE_ID_MAX_CHARS", loopback.SCENE_ID_MAX_CHARS),
            ("SCENE_ENTRIES_MAX", server.SCENE_ENTRIES_MAX),
            ("SPAWN_BATCH_ITEMS_MAX", loopback.SPAWN_BATCH_ITEMS_MAX),
            ("SPAWN_BATCH_PER_TICK_MAX", loopback.SPAWN_BATCH_PER_TICK_MAX),
        ):
            with self.subTest(constant=name):
                self.assertIn(f"protected const int {name} = {value};", self.bridge)
        self.assertEqual(server.SPAWN_BATCH_ITEMS_MAX, loopback.SPAWN_BATCH_ITEMS_MAX)
        self.assertEqual(server.SPAWN_BATCH_PER_TICK_MAX, loopback.SPAWN_BATCH_PER_TICK_MAX)

    def test_dispatch_routes_each_verb_to_its_handler(self) -> None:
        dispatch = self._body("protected void Dispatch(MCPCommand command)")
        for verb, handler in (
            ("world_spawn_batch", "DispatchWorldSpawnBatch"),
            ("scene_entities", "DispatchSceneEntities"),
            ("scene_clear", "DispatchSceneClear"),
        ):
            with self.subTest(verb=verb):
                self.assertIn(
                    f'else if (command.cmd == "{verb}") {{ postNow = {handler}(command, result); }}',
                    dispatch,
                )

    def test_is_valid_scene_id_is_the_daemon_rule(self) -> None:
        namespace: dict[str, object] = {"SCENE_ID_MAX_CHARS": 64}
        rule = translate(self.bridge, "protected bool IsValidSceneId(string sceneId)", namespace)
        corpus = ["", "a", "Z" * 64, "a" * 65, "A-z_0-9", "ñ", "a" * 63 + "ñ", "a\n"]
        corpus += [chr(code) for code in range(1, 128)]
        corpus += ["ok" + chr(code) for code in range(1, 128)]
        for value in corpus:
            with self.subTest(value=value):
                expected = loopback.is_scene_id(value)
                self.assertEqual(rule(EnforceString.of(value)), expected)
                self.assertEqual(rule(SignedAsciiString.of(value)), expected)

    def test_a_spawn_is_refused_before_create_and_registered_after(self) -> None:
        dispatch = self._body("protected void Dispatch(MCPCommand command)")
        branch = dispatch[dispatch.index('if (command.cmd == "world_spawn")'):]
        branch = branch[: branch.index('else if (command.cmd == "object_delete")')]
        self.assertLess(
            branch.index("result.error = SceneSpawnRefusal(command.args, 1);"),
            branch.index("postNow = DispatchWorldSpawn(command, result);"),
        )
        self.assertLess(
            branch.index("postNow = ApplySpawnLifetime(command, result);"),
            branch.index("RegisterSpawnScene(command);"),
        )
        refusal = self._body("protected string SceneSpawnRefusal(MCPArgs args, int count)")
        self.assertIn('if (!args || args.scene_id == "") { return ""; }', refusal)
        self.assertIn('if (!IsValidSceneId(args.scene_id)) { return "bad_args"; }', refusal)
        self.assertIn('entries.Count() + count > SCENE_ENTRIES_MAX) { return "scene_full"; }', refusal)
        register = self._body("protected void RegisterSpawnScene(MCPCommand command)")
        self.assertIn("job = m_Jobs.Get(command.id);", register)
        self.assertIn("if (!job || job.args != command.args || !job.subject) { return; }", register)
        self.assertIn("AddSceneEntry(command.args.scene_id, command.id, job.subject);", register)

    def test_scene_entries_are_never_dropped(self) -> None:
        self.assertNotIn("m_Scenes.Remove", self.bridge)
        self.assertNotIn("m_Scenes.Clear", self.bridge)
        self.assertNotIn("entries.Remove", self.bridge)
        self.assertIn("Object obj;", self.messages)
        entities = self._body("protected bool DispatchSceneEntities(MCPCommand command, MCPResult result)")
        self.assertIn('if (!command.args || !IsValidSceneId(command.args.scene_id)) { result.ok = false; result.error = "bad_args"; return true; }', entities)
        self.assertLess(
            entities.index("result.scene_entities = new array<ref MCPSceneEntity>();"),
            entities.index("entries = m_Scenes.Get(command.args.scene_id);"),
        )
        self.assertIn(
            "if (entry.obj) { VectorToArray(entry.obj.GetPosition(), row.pos); } else { row.deleted = true; }",
            entities,
        )

    def test_scene_clear_deletes_its_own_entries_the_way_object_delete_does(self) -> None:
        clear = self._body("protected bool DispatchSceneClear(MCPCommand command, MCPResult result)")
        self.assertIn('if (!command.args || !IsValidSceneId(command.args.scene_id)) { result.ok = false; result.error = "bad_args"; return true; }', clear)
        self.assertIn("entries = m_Scenes.Get(command.args.scene_id);", clear)
        self.assertIn(
            "if (m_RuntimeObjects.Get(entry.object_id) == entry.obj) "
            "{ MCPBotControl.OnObjectGone(this, entry.object_id); m_RuntimeObjects.Remove(entry.object_id); }",
            clear,
        )
        self.assertIn("GetGame().ObjectDelete(entry.obj);", clear)
        self.assertEqual(clear.count("ObjectDelete("), 1)
        for absent in ("GetObjectsAtPosition", "CreateObjectEx", "GetType()", "IsKindOf", "m_Scenes.Get(entry"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, clear)
        self.assertIn("report.already_deleted = report.already_deleted + 1;", clear)
        self.assertIn("if (job.scene_pending.Count() == 0) { result.ok = true; result.scene_clear = report; return true; }", clear)
        post = self._body("protected void PostSceneClear(MCPJob job)")
        self.assertIn(
            "if (entry.obj) { report.errors = report.errors + 1; report.error_ids.Insert(entry.object_id); } "
            "else { report.deleted = report.deleted + 1; }",
            post,
        )
        timeout = self._body("protected void PostJobTimeout(MCPJob job)")
        self.assertTrue(timeout.startswith('if (job.kind == "scene_clear") { PostSceneClear(job); return; }'))
        ready = self._body("protected bool IsJobReady(MCPJob job)")
        self.assertIn('else if (job.kind == "scene_clear") { return IsSceneClearDone(job); }', ready)

    def test_a_batch_is_checked_whole_and_then_created_per_tick(self) -> None:
        dispatch = self._body("protected bool DispatchWorldSpawnBatch(MCPCommand command, MCPResult result)")
        self.assertNotIn("CreateObjectEx", dispatch)
        self.assertIn(
            "if (count < 1 || count > SPAWN_BATCH_ITEMS_MAX || args.per_tick < 1 || args.per_tick > SPAWN_BATCH_PER_TICK_MAX)",
            dispatch,
        )
        self.assertLess(dispatch.index("SPAWN_BATCH_ITEMS_MAX"), dispatch.index("m_Jobs.Insert(job.id, job);"))
        self.assertLess(
            dispatch.index("result.error = SceneSpawnRefusal(args, count);"), dispatch.index("m_Jobs.Insert(job.id, job);")
        )
        self.assertIn('job.kind = "spawn_batch";', dispatch)
        self.assertTrue(dispatch.rstrip().endswith("return false;"))
        step = self._body("protected bool StepSpawnBatch(MCPJob job)")
        self.assertIn("int stop = done + job.args.per_tick;", step)
        self.assertIn("return done >= total;", step)
        ready = self._body("protected bool IsJobReady(MCPJob job)")
        self.assertIn('else if (job.kind == "spawn_batch") { return StepSpawnBatch(job); }', ready)
        success = self._body("protected void PostJobSuccess(MCPJob job)")
        self.assertIn('if (job.kind == "spawn_batch") { PostSpawnBatch(job); return; }', success)
        self.assertEqual(self.bridge.count("PostSpawnBatch(job)"), 1)
        post = self._body("protected void PostSpawnBatch(MCPJob job)")
        self.assertIn('batch.status = "ok"; if (batch.failed > 0) { batch.status = "partial"; }', post)

    def test_a_batch_item_goes_through_world_spawn_checks_ground_and_initai(self) -> None:
        item = self._body("protected MCPSpawnBatchRow SpawnBatchItem(MCPJob job, int index, MCPArgs itemArgs)")
        order = [
            "validation = ValidateSpawnArgs(itemArgs);",
            "pos[1] = GetGame().SurfaceGetType(pos[0], pos[2], surfaceType);",
            'if (GetGame().IsKindOf(item.type, "DayZInfected")) { flags = flags | ECE_INITAI; }',
            "spawned = GetGame().CreateObjectEx(item.type, pos, flags, validation.rotation);",
            "spawned.SetOrientation(Vector(item.ypr.Get(0), item.ypr.Get(1), item.ypr.Get(2)));",
            "m_RuntimeObjects.Insert(row.object_id, spawned);",
            "AddSceneEntry(job.args.scene_id, row.object_id, spawned);",
        ]
        for earlier, later in zip(order, order[1:]):
            with self.subTest(earlier=earlier):
                self.assertLess(item.index(earlier), item.index(later))
        self.assertIn("if (!validation.ok) { row.error = validation.error; return row; }", item)
        self.assertIn('if (!spawned) { row.error = "spawn_failed"; return row; }', item)
        self.assertIn("if (item.ground)", item)
        self.assertIn("if (item.ypr_set)", item)
        # surface_query reads the ground the same way.
        self.assertIn(
            "GetGame().SurfaceGetType(command.args.x, command.args.z, surfaceType);",
            self._body("protected bool DispatchSurfaceQuery(MCPCommand command, MCPResult result)"),
        )
        self.assertEqual(ECE_INITAI, 2048)


if __name__ == "__main__":
    unittest.main()
