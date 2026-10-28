"""Scenes (inbox 165a, 5a21, 7f5d): scene_id on world_spawn, scene_entities,
scene_clear and world_spawn_batch.

The bridge keeps, per scene_id, the objects world_spawn and world_spawn_batch
created under it. These tests hold what can be checked offline: the tool and
daemon refusals, the older-PBO admission, read-only versus lease, the reply
shapes, the tool's groups of per_tick, and the Enforce bodies themselves: the
batch dispatch, scene_clear and its job run translated against fakes
(enforce_subset_helpers), and every message member they use is checked against
its declaration in MCPMessages.c.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
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

from dayz_mcp import loopback, server, tool_catalog, world_results
from dayz_mcp.bridge_errors import _bridge_error
from dayz_mcp.bridge_readiness import (
    EXPECTED_SERVER_ARG_CONTRACT_HASH,
    SERVER_ARG_CONTRACT,
)
from dayz_mcp.core import ECE_INITAI, EXPECTED_BRIDGE_VERSION
from dayz_mcp.result_prune import prune_unfilled_fields
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import (
    EnforceArray,
    EnforceMap,
    EnforceString,
    EnforceVector,
    SignedAsciiString,
    class_members,
    clean,
    fake_class,
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
        return {"items": [dict(WIRE_ITEM)], "scene_id": "a"}
    return {"scene_id": "a"}


def _created_answer(args: dict[str, Any], first_id: int) -> dict[str, Any]:
    """The bridge's reply to one world_spawn_batch command that created every item."""
    rows = [
        {"index": index, "ok": 1, "object_id": first_id + index, "type": item["type"], "error": ""}
        for index, item in enumerate(args["items"])
    ]
    return {
        "ok": 1,
        "world_spawn_batch": {"status": "ok", "created": len(rows), "failed": 0, "items": rows},
    }


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
        # per_tick never reaches the bridge: the tool sends groups of that size.
        self.assertEqual(SERVER_ARG_CONTRACT["world_spawn_batch"], ("items", "scene_id"))
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
        self.assertEqual(loopback.SPAWN_ROTATION_MIN, -(2**31))
        self.assertEqual(loopback.SPAWN_ROTATION_MAX, 2**31 - 1)
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

    async def _batch(self, arguments: dict, answer: Any = None):
        """world_spawn_batch against a fake bridge. answer(group, args) answers each
        command (or raises); by default every item is created, ids counting on from
        1000000000. Returns the (cmd, args, peer, timeout_s) of every command and the
        tool's reply."""
        calls: list[tuple[str, dict[str, Any], str, float]] = []

        async def call_bridge(cmd: str, args: dict, peer: str, timeout_s: float) -> dict:
            first_id = 1000000000 + sum(len(call[1]["items"]) for call in calls)
            calls.append((cmd, copy.deepcopy(args), peer, timeout_s))
            if answer is None:
                return _created_answer(args, first_id)
            return answer(len(calls) - 1, args, first_id)

        with patch.object(self.runtime, "call_bridge", new=call_bridge):
            result = await self.app.call_tool("world_spawn_batch", arguments)
        return calls, _content_json(result)

    def test_signatures(self) -> None:
        self.assertEqual(
            set(self.tools["world_spawn_batch"].parameters["properties"]),
            {"items", "per_tick", "scene_id", "timeout_s"},
        )
        self.assertEqual(self.tools["world_spawn_batch"].parameters["required"], ["items"])
        self.assertEqual(self.tools["world_spawn_batch"].parameters["properties"]["per_tick"]["default"], 10)
        self.assertIs(self.tools["world_spawn_batch"].parameters.get("additionalProperties"), False)
        # items is list[Any]: every element reaches the tool's own check, which
        # names it, instead of a frontier error.
        items = self.tools["world_spawn_batch"].parameters["properties"]["items"]
        self.assertEqual(items["type"], "array")
        self.assertNotIn("type", items.get("items", {}))
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
        for claim in (
            "groups of per_tick",
            "one command per group",
            "which is not measured yet",
            "raise it for many groups",
            "not_sent",
            "If the first group fails, the call fails with its error",
            "-2147483648 to 2147483647",
        ):
            with self.subTest(claim=claim):
                self.assertIn(claim, batch)
        self.assertNotIn("does not stall", batch)
        clear = self.tools["scene_clear"].description
        self.assertIn("never touches the server's storage", clear)
        self.assertIn("scene_shared", clear)
        self.assertIn("deleted out of it", clear)
        self.assertIn("deleting a vehicle ejects its crew", clear)
        self.assertIn("one seated in it is ejected", clear)
        self.assertNotIn("Nothing else is touched", clear)
        # The cap: entries are never dropped, so a deleted one still counts.
        for name in ("world_spawn", "world_spawn_batch"):
            with self.subTest(cap=name):
                self.assertIn(server.SCENE_CAP_LINE, self.tools[name].description)
        self.assertIn("never drops one", server.SCENE_CAP_LINE)
        self.assertIn("scene_clear frees no room", server.SCENE_CAP_LINE)
        self.assertIn("keep counting toward the 2048 a scene holds", clear)

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
        calls, _result = await self._batch(
            {
                "items": [
                    {"type": "Barrel_Green", "pos": [1, None, 3]},
                    {"type": "Barrel_Green", "pos": [1, 2, 3], "rotation": 2, "flags": 1060},
                    {"type": "ZmbM_CitizenASkinny", "pos": [1.5, 0, 3.5], "ypr": [90, -5, 0]},
                    {"type": "Barrel_Green", "pos": [1, 2, 3], "rotation": -2147483648},
                    {"type": "Barrel_Green", "pos": [1, 2, 3], "rotation": 2147483647},
                ],
                "per_tick": 5,
                "scene_id": "a",
                "timeout_s": 4,
            },
        )
        self.assertEqual(len(calls), 1)
        cmd, args, peer, timeout_s = calls[0]
        self.assertEqual((cmd, peer, timeout_s), ("world_spawn_batch", "server", 4.0))
        self.assertEqual(
            args,
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
                    {"type": "Barrel_Green", "pos": [1.0, 2.0, 3.0], "flags": 0, "rotation": -2147483648},
                    {"type": "Barrel_Green", "pos": [1.0, 2.0, 3.0], "flags": 0, "rotation": 2147483647},
                ],
                "scene_id": "a",
            },
        )
        self.assertEqual(loopback.validate_command_args("world_spawn_batch", args), (True, None))
        calls, _result = await self._batch({"items": [ITEM] * 200, "per_tick": 50})
        self.assertEqual([len(call[1]["items"]) for call in calls], [50, 50, 50, 50])
        for _cmd, args, _peer, _timeout_s in calls:
            self.assertNotIn("scene_id", args)
            self.assertEqual(loopback.validate_command_args("world_spawn_batch", args), (True, None))

    async def test_the_items_go_in_groups_of_per_tick_in_input_order(self) -> None:
        items = [{"type": f"Type{index}", "pos": [1.0, 2.0, 3.0]} for index in range(5)]
        calls, result = await self._batch({"items": items, "per_tick": 2, "scene_id": "take_1"})
        self.assertEqual(
            [[item["type"] for item in call[1]["items"]] for call in calls],
            [["Type0", "Type1"], ["Type2", "Type3"], ["Type4"]],
        )
        for cmd, args, peer, _timeout_s in calls:
            self.assertEqual((cmd, peer, args["scene_id"]), ("world_spawn_batch", "server", "take_1"))
            self.assertLessEqual(len(args["items"]), 2)
        batch = result["world_spawn_batch"]
        self.assertEqual((batch["status"], batch["created"], batch["failed"]), ("ok", 5, 0))
        self.assertEqual(
            batch["items"],
            [{"index": index, "ok": True, "object_id": 1000000000 + index} for index in range(5)],
        )
        self.assertEqual(result["ok"], 1)
        # per_tick 1 and the default 10.
        calls, _result = await self._batch({"items": items, "per_tick": 1})
        self.assertEqual([len(call[1]["items"]) for call in calls], [1, 1, 1, 1, 1])
        calls, _result = await self._batch({"items": [ITEM] * 25})
        self.assertEqual([len(call[1]["items"]) for call in calls], [10, 10, 5])

    async def test_a_later_group_that_fails_ends_the_call_partial(self) -> None:
        items = [ITEM, {"type": "NoSuchClass", "pos": [1.0, 2.0, 3.0]}, ITEM, ITEM, ITEM]
        for error in ("scene_full", "timeout waiting for world_spawn_batch id=12; server peer polled 0.2 s ago"):
            with self.subTest(error=error):

                def answer(group: int, args: dict, first_id: int, error: str = error) -> dict:
                    if group == 1:
                        raise ToolError(error)
                    reply = _created_answer(args, first_id)
                    reply["world_spawn_batch"]["items"][1] = {
                        "index": 1, "ok": 0, "object_id": 0, "type": "NoSuchClass", "error": "unknown_type"
                    }
                    return reply

                calls, result = await self._batch({"items": items, "per_tick": 2}, answer)
                self.assertEqual(len(calls), 2)
                batch = result["world_spawn_batch"]
                self.assertEqual((batch["status"], batch["created"], batch["failed"]), ("partial", 1, 4))
                self.assertEqual(
                    batch["items"],
                    [
                        {"index": 0, "ok": True, "object_id": 1000000000},
                        {"index": 1, "ok": False, "error": "unknown_type", "type": "NoSuchClass"},
                        {"index": 2, "ok": False, "error": error, "type": "Barrel_Green"},
                        {"index": 3, "ok": False, "error": error, "type": "Barrel_Green"},
                        {"index": 4, "ok": False, "error": "not_sent", "type": "Barrel_Green"},
                    ],
                )

    async def test_a_first_group_that_fails_fails_the_call(self) -> None:
        for error in ("scene_full", "bad_args", "timeout waiting for world_spawn_batch id=3"):
            with self.subTest(error=error):
                sent: list[int] = []

                async def call_bridge(
                    cmd: str, args: dict, peer: str, timeout_s: float, error: str = error
                ) -> dict:
                    sent.append(len(args["items"]))
                    raise ToolError(error)

                with patch.object(self.runtime, "call_bridge", new=call_bridge):
                    with self.assertRaises(Exception) as caught:
                        await self.app.call_tool("world_spawn_batch", {"items": [ITEM] * 3, "per_tick": 1})
                self.assertIn(error, _tool_error_text(caught.exception))
                self.assertEqual(sent, [1])

    async def test_every_group_shares_one_deadline(self) -> None:
        # The deadline is taken once; the first group gets the whole budget, a
        # later one what is left, and none is sent once nothing is left.
        readings = iter([100.0, 104.0, 115.5])
        clock = SimpleNamespace(monotonic=lambda: next(readings))
        with patch.object(server, "time", clock):
            calls, result = await self._batch({"items": [ITEM] * 3, "per_tick": 1, "timeout_s": 15})
        self.assertEqual([call[3] for call in calls], [15.0, 11.0])
        batch = result["world_spawn_batch"]
        self.assertEqual((batch["status"], batch["created"], batch["failed"]), ("partial", 2, 1))
        self.assertEqual(batch["items"][2], {"index": 2, "ok": False, "error": "not_sent", "type": "Barrel_Green"})

    async def test_a_cancelled_call_sends_no_further_group(self) -> None:
        def answer(group: int, args: dict, first_id: int) -> dict:
            if group == 1:
                raise asyncio.CancelledError()
            return _created_answer(args, first_id)

        calls: list[Any] = []

        async def call_bridge(cmd: str, args: dict, peer: str, timeout_s: float) -> dict:
            calls.append(args)
            return answer(len(calls) - 1, args, 1000000000)

        with patch.object(self.runtime, "call_bridge", new=call_bridge):
            with self.assertRaises(asyncio.CancelledError):
                await self.app.call_tool("world_spawn_batch", {"items": [ITEM] * 3, "per_tick": 1})
        self.assertEqual(len(calls), 2)

    async def test_two_calls_reach_the_bridge_one_at_a_time(self) -> None:
        # The tool lock holds for every group of a call: the second call's
        # groups go once the first call has answered.
        order: list[str] = []
        inside = 0
        peak = 0

        async def call_bridge(cmd: str, args: dict, peer: str, timeout_s: float) -> dict:
            nonlocal inside, peak
            inside += 1
            peak = max(peak, inside)
            order.append(args["scene_id"])
            try:
                await asyncio.sleep(0)
                return _created_answer(args, 1000000000)
            finally:
                inside -= 1

        with patch.object(self.runtime, "call_bridge", new=call_bridge):
            replies = await asyncio.gather(
                *(
                    self.app.call_tool(
                        "world_spawn_batch", {"items": [ITEM] * 2, "per_tick": 1, "scene_id": scene_id}
                    )
                    for scene_id in ("A", "B")
                )
            )
        self.assertEqual((peak, order), (1, ["A", "A", "B", "B"]))
        for reply in replies:
            self.assertEqual(_content_json(reply)["world_spawn_batch"]["status"], "ok")

    async def test_item_count_and_per_tick_bounds_refuse_before_anything_is_sent(self) -> None:
        for arguments, part in (
            ({"items": []}, "bad_args: items holds 0 items"),
            ({"items": [ITEM] * 201}, "bad_args: items holds 201 items"),
            ({"items": [ITEM], "per_tick": 0}, "bad_args: per_tick"),
            ({"items": [ITEM], "per_tick": 51}, "bad_args: per_tick"),
            ({"items": [ITEM], "per_tick": True}, "per_tick"),
            ({"items": [ITEM], "scene": "a"}, "unexpected arguments: scene"),
            ({"items": [ITEM], "timeout_s": 0}, "bad_timeout"),
            ({"items": [ITEM], "timeout_s": 301}, "bad_timeout"),
        ):
            with self.subTest(arguments={k: v for k, v in arguments.items() if k != "items"}):
                self.assertIn(part, await self._refused("world_spawn_batch", arguments))

    async def test_a_malformed_item_refuses_the_call_naming_its_index(self) -> None:
        cases = (
            # items is list[Any]: an item that is not an object reaches the
            # tool's check, which names it, like any other malformed item.
            ("Barrel_Green", "bad_args: items[3] 'Barrel_Green' must be an object"),
            (None, "bad_args: items[3] None must be an object"),
            (["Barrel_Green", [1.0, 2.0, 3.0]], "bad_args: items[3] ["),
            (7, "bad_args: items[3] 7 must be an object"),
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
            ({**ITEM, "rotation": True}, "bad_args: items[3].rotation"),
            ({**ITEM, "rotation": 2147483648}, "bad_args: items[3].rotation"),
            ({**ITEM, "rotation": -2147483649}, "bad_args: items[3].rotation"),
            ({**ITEM, "rotation": 0, "ypr": [0.0, 0.0, 0.0]}, "bad_args: items[3] takes rotation or ypr"),
            ({**ITEM, "ypr": [0.0, 0.0]}, "bad_args: items[3].ypr"),
            ({**ITEM, "ypr": [0.0, float("inf"), 0.0]}, "bad_args: items[3].ypr"),
            ({**ITEM, "ypr": [0.0, True, 0.0]}, "bad_args: items[3].ypr"),
            ({**ITEM, "lifetime_s": 60.0}, "bad_args: items[3] has unexpected keys ['lifetime_s']"),
        )
        for bad, part in cases:
            with self.subTest(item=repr(bad)):
                # per_tick 1: the bad item is in the fourth group, and still
                # nothing is sent.
                items = [ITEM, ITEM, ITEM, bad, ITEM]
                self.assertIn(part, await self._refused("world_spawn_batch", {"items": items, "per_tick": 1}))

    async def test_batch_reply_rows_carry_only_what_is_true_of_them(self) -> None:
        def answer(group: int, args: dict, first_id: int) -> dict:
            return {
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

        _calls, result = await self._batch(
            {"items": [ITEM, {"type": "NoSuchClass", "pos": [1.0, 2.0, 3.0]}]}, answer
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

    def test_a_group_row_the_bridge_did_not_send_is_not_created(self) -> None:
        items = [dict(WIRE_ITEM), {**WIRE_ITEM, "type": "Other"}]
        rows = world_results._spawn_batch_group_rows(
            {"ok": 1, "world_spawn_batch": {"items": [{"index": 0, "ok": 1, "object_id": 9}]}}, items, 4
        )
        self.assertEqual(
            rows,
            [
                {"index": 4, "ok": True, "object_id": 9},
                {"index": 5, "ok": False, "error": "missing_row", "type": "Other"},
            ],
        )
        reply = world_results._spawn_batch_reply({"ok": 1}, rows, items * 3)
        self.assertEqual(reply["world_spawn_batch"]["created"], 1)
        self.assertEqual(reply["world_spawn_batch"]["failed"], 5)
        self.assertEqual(
            [row["error"] for row in reply["world_spawn_batch"]["items"][2:]],
            ["not_sent"] * 4,
        )

    async def test_scene_clear_publishes_its_counts_without_the_shared_pair(self) -> None:
        answer = {
            "ok": 1,
            "scene_clear": {
                "deleted": 2,
                "already_deleted": 1,
                "errors": 0,
                "error_ids": [],
                "carrier_id": 0,
                "carrier_scene_id": "",
                "carried_id": 0,
                "carried_scene_id": "",
            },
        }
        _args, result = await self._forwarded("scene_clear", {"scene_id": "a"}, answer)
        self.assertEqual(
            result["scene_clear"], {"deleted": 2, "already_deleted": 1, "errors": 0, "error_ids": []}
        )

    def test_scene_shared_names_the_pair_that_blocked_it(self) -> None:
        wire = {
            "ok": 0,
            "error": "scene_shared",
            "scene_clear": {
                "deleted": 0,
                "carrier_id": 1000000000,
                "carrier_scene_id": "a",
                "carried_id": 1000000001,
                "carried_scene_id": "b",
            },
        }
        self.assertEqual(
            str(_bridge_error(wire, "scene_clear")),
            "scene_shared; carrier_id=1000000000 carrier_scene_id=a carried_id=1000000001 carried_scene_id=b",
        )
        # Only a positive int and a scene id cross; other verbs carry nothing.
        odd = {**wire, "scene_clear": {**wire["scene_clear"], "carrier_id": True, "carried_scene_id": "x y"}}
        self.assertEqual(str(_bridge_error(odd, "scene_clear")), "scene_shared")
        self.assertEqual(str(_bridge_error(wire, "world_spawn")), "scene_shared")
        self.assertEqual(str(_bridge_error({**wire, "error": "bad_args"}, "scene_clear")), "bad_args")

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
            ("SPAWN_BATCH_PER_TICK_MAX", loopback.SPAWN_BATCH_PER_TICK_MAX),
            ("BATCH_OBJECT_ID_BASE", 1000000000),
        ):
            with self.subTest(constant=name):
                self.assertIn(f"protected const int {name} = {value};", self.bridge)
        # The 200-item bound is the tool's alone: one command holds at most
        # SPAWN_BATCH_PER_TICK_MAX items, and per_tick never reaches the bridge.
        self.assertNotIn("SPAWN_BATCH_ITEMS_MAX", self.bridge)
        self.assertNotIn("per_tick", self.bridge)
        self.assertNotIn("per_tick", self.messages)
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
            "{ MCPBotControl.OnObjectGone(this, entry.object_id); MCPSurvivorDrive.OnObjectGone(this, entry.object_id); "
            "m_RuntimeObjects.Remove(entry.object_id); }",
            clear,
        )
        self.assertIn("GetGame().ObjectDelete(deleteNow.Get(index));", clear)
        self.assertEqual(clear.count("ObjectDelete("), 1)
        # Every refusal is decided before the first delete.
        self.assertLess(clear.rindex("return RefuseSceneShared("), clear.index("ObjectDelete("))
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
        # One level up: the inventory parent of an EntityAI, else the entity-tree
        # parent every Object has.
        carrier = self._body("protected Object SceneCarrier(Object obj)")
        self.assertLess(carrier.index("holder = entity.GetHierarchyParent();"), carrier.index("obj.GetParent()"))
        self.assertTrue(carrier.endswith("return Object.Cast(obj.GetParent());"))
        index = self._body("protected int SceneCarrierIndex(Object obj, array<Object> carriers)")
        self.assertIn("Object parent = SceneCarrier(obj);", index)
        self.assertIn("parent = SceneCarrier(parent);", index)
        # The refusal walks every carrier; the skip of a delete, holders only.
        self.assertIn("if (SceneHolderIndex(entry.obj, mine) < 0) { deleteNow.Insert(entry.obj); }", clear)
        holder = self._body("protected int SceneHolderIndex(Object obj, array<Object> holders)")
        self.assertIn("holder = entity.GetHierarchyParent();", holder)
        self.assertIn("holder = holder.GetHierarchyParent();", holder)
        self.assertNotIn("GetParent()", holder)

    def test_a_batch_command_answers_in_its_dispatch_tick_and_leaves_no_job(self) -> None:
        dispatch = self._body("protected bool DispatchWorldSpawnBatch(MCPCommand command, MCPResult result)")
        self.assertIn("if (count < 1 || count > SPAWN_BATCH_PER_TICK_MAX)", dispatch)
        self.assertLess(
            dispatch.index("result.error = SceneSpawnRefusal(args, count);"),
            dispatch.index("row = SpawnBatchItem(args, index, itemArgs);"),
        )
        self.assertNotIn("m_Jobs", dispatch)
        self.assertTrue(dispatch.rstrip().endswith("return true;"))
        for gone in ('"spawn_batch"', "StepSpawnBatch", "PostSpawnBatch"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, self.bridge)
        self.assertNotIn("spawn_batch", [name for _type, name in class_members(self.messages, "MCPJob")])

    def test_a_batch_item_goes_through_world_spawn_checks_ground_and_initai(self) -> None:
        item = self._body("protected MCPSpawnBatchRow SpawnBatchItem(MCPArgs args, int index, MCPArgs itemArgs)")
        order = [
            "validation = ValidateSpawnArgs(itemArgs);",
            "pos[1] = GetGame().SurfaceGetType(pos[0], pos[2], surfaceType);",
            'if (GetGame().IsKindOf(item.type, "DayZInfected")) { flags = flags | ECE_INITAI; }',
            "spawned = GetGame().CreateObjectEx(item.type, pos, flags, validation.rotation);",
            "spawned.SetOrientation(Vector(item.ypr.Get(0), item.ypr.Get(1), item.ypr.Get(2)));",
            "m_RuntimeObjects.Insert(row.object_id, spawned);",
            "AddSceneEntry(args.scene_id, row.object_id, spawned);",
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


# --- The Enforce bodies, run translated (enforce_subset_helpers) ---------------

_FLOAT_MAX = 3.4028234663852886e38
_BRIDGE_CLASS_TYPES = frozenset(
    {
        "array",
        "map",
        "MCPArgs",
        "MCPSpawnBatchItem",
        "MCPSpawnBatch",
        "MCPSpawnBatchRow",
        "MCPSpawnValidation",
        "Object",
        "MCPSceneEntry",
        "MCPSceneClear",
        "MCPJob",
        "MCPResult",
        "MCPSceneEntity",
        "EntityAI",
    }
)
_BRIDGE_MEMBERS = frozenset({"m_Scenes", "m_RuntimeObjects", "m_NextBatchObjectId", "m_Jobs", "m_ElapsedS"})
# The batch and scene methods. Every message member they use is checked against
# MCPMessages.c (SceneMessageContractTest), and each one runs below.
_SCENE_METHODS = (
    "protected bool DispatchWorldSpawnBatch(MCPCommand command, MCPResult result)",
    "protected MCPSpawnBatchRow SpawnBatchItem(MCPArgs args, int index, MCPArgs itemArgs)",
    "protected string SceneSpawnRefusal(MCPArgs args, int count)",
    "protected void RegisterSpawnScene(MCPCommand command)",
    "protected void AddSceneEntry(string sceneId, int objectId, Object obj)",
    "protected bool DispatchSceneEntities(MCPCommand command, MCPResult result)",
    "protected bool DispatchSceneClear(MCPCommand command, MCPResult result)",
    "protected Object SceneCarrier(Object obj)",
    "protected int SceneCarrierIndex(Object obj, array<Object> carriers)",
    "protected int SceneHolderIndex(Object obj, array<Object> holders)",
    "protected bool RefuseSceneShared(MCPResult result, int carrierId, string carrierScene, int carriedId, string carriedScene)",
    "protected bool IsSceneClearDone(MCPJob job)",
    "protected void PostSceneClear(MCPJob job)",
)
# What they call or follow, run as well: world_spawn's dispatch and checks, and
# the job runner.
_SUPPORT_METHODS = (
    "protected bool IsValidSceneId(string sceneId)",
    "protected bool DispatchWorldSpawn(MCPCommand command, MCPResult result)",
    "protected bool ApplySpawnLifetime(MCPCommand command, MCPResult result)",
    "protected MCPSpawnValidation ValidateSpawnArgs(MCPArgs args)",
    "protected bool IsFiniteFloat(float value)",
    "protected bool IsFiniteWorldCoord(float value, float worldSize)",
    "protected bool IsAllowedSpawnFlags(int flags)",
    "protected void VectorToArray(vector v, array<float> a)",
    "protected void ProcessJobs()",
    "protected bool IsJobReady(MCPJob job)",
    "protected void PostJobTimeout(MCPJob job)",
    "protected void PostJobSuccess(MCPJob job)",
)
# Dispatch's world_spawn branch, cut from the real Dispatch: from its
# `if (command.cmd == "world_spawn")` to the next `else if`. It runs inside a
# method that declares postNow as Dispatch does and returns it.
_WORLD_SPAWN_BRANCH = "protected bool WorldSpawnBranch(MCPCommand command, MCPResult result)"


def _world_spawn_branch(bridge: str) -> str:
    dispatch = method_body(bridge, "protected void Dispatch(MCPCommand command)")
    head = 'if (command.cmd == "world_spawn")'
    if dispatch.count(head) != 1 or dispatch.count("bool postNow = true;") != 1:
        raise AssertionError("Dispatch's world_spawn branch or its postNow is not where the test cuts it")
    start = dispatch.index(head)
    branch = dispatch[start : dispatch.index("else if", start)]
    return f"{_WORLD_SPAWN_BRANCH}\n{{\n\tbool postNow = true;\n\t{branch.strip()}\n\treturn postNow;\n}}\n"


_MESSAGE_FAKES = (
    "MCPArgs",
    "MCPCommand",
    "MCPSpawnBatchItem",
    "MCPSpawnBatchRow",
    "MCPSpawnBatch",
    "MCPSpawnValidation",
    "MCPSpawnLifetime",
    "MCPSceneEntry",
    "MCPSceneEntity",
    "MCPSceneClear",
    "MCPJob",
    "MCPResult",
)


class _LogString(EnforceString):
    """A string literal of the bodies: `+` appends a string, an int or a float,
    as the Log lines do. A float is written the way Python writes it, which is
    not Enforce's form: no test reads a line that holds one."""

    __slots__ = ()

    def __add__(self, other: object) -> "_LogString":
        if isinstance(other, EnforceString):
            return _LogString(self.data + other.data)
        if isinstance(other, (int, float)) and not isinstance(other, bool):
            return _LogString(self.data + str(other).encode("ascii"))
        raise AssertionError(f"string + {other!r}")


class _Entity:
    """A world object: what the bodies call on one. holder is its inventory
    parent, what EntityAI.GetHierarchyParent answers (attached to, in the cargo
    or the hands of); parent is its entity-tree parent, what IEntity.GetParent
    answers (a seat, or what AddChild linked). entity False is an Object that is
    not an EntityAI: it has no inventory."""

    def __init__(
        self,
        type_name: str,
        pos: tuple[float, float, float] = (1.0, 2.0, 3.0),
        holder: Any = None,
        parent: Any = None,
        entity: bool = True,
    ) -> None:
        self.type_name = type_name
        self.pos = list(pos)
        self.holder = holder
        self.parent = parent
        self.entity = entity
        self.orientation: list[float] | None = None

    def GetType(self) -> EnforceString:
        return EnforceString.of(self.type_name)

    def GetPosition(self) -> EnforceVector:
        return EnforceVector(self.pos)

    def SetOrientation(self, ypr: EnforceVector) -> None:
        self.orientation = [float(value) for value in ypr]

    def GetHierarchyParent(self) -> Any:
        if not self.entity:
            raise AssertionError("GetHierarchyParent on an Object that is not an EntityAI")
        return self.holder

    def GetParent(self) -> Any:
        return self.parent

    def __repr__(self) -> str:
        return f"<{self.type_name} {id(self):#x}>"


class _Game:
    """GetGame(): the config, the ground, the infected class, and a record of
    every create and delete."""

    def __init__(self) -> None:
        self.known = {"Barrel_Green", "ZmbM_CitizenASkinny", "CarBattery", "CivilianSedan"}
        self.infected = {"ZmbM_CitizenASkinny"}
        self.created: list[tuple[str, list[float], int, int, _Entity]] = []
        self.deleted: list[Any] = []

    def ConfigIsExisting(self, path: EnforceString) -> bool:
        text = path.text
        return text.startswith("CfgVehicles ") and text[len("CfgVehicles ") :] in self.known

    def GetWorld(self) -> SimpleNamespace:
        return SimpleNamespace(GetWorldSize=lambda: 15360.0)

    def SurfaceY(self, x: float, z: float) -> float:
        return 41.0

    def SurfaceGetType(self, x: float, z: float, surface_type: EnforceString) -> float:
        # surface_query's read, kept apart from SurfaceY to tell them apart.
        return 42.5

    def IsKindOf(self, type_name: EnforceString, parent: EnforceString) -> bool:
        return parent.text == "DayZInfected" and type_name.text in self.infected

    def CreateObjectEx(self, type_name: EnforceString, pos: EnforceVector, flags: int, rotation: int) -> _Entity:
        created = _Entity(type_name.text, tuple(pos))
        self.created.append((type_name.text, list(pos), flags, rotation, created))
        return created

    def ObjectDelete(self, obj: Any) -> None:
        self.deleted.append(obj)


def _bridge_const(source: str, name: str) -> int | float:
    match = re.search(rf"protected const (?:int|float) {name} = (-?[0-9.]+);", source)
    if match is None:
        raise AssertionError(f"{name} not declared")
    text = match.group(1)
    return float(text) if "." in text else int(text)


class SceneBridgeExecutionTest(unittest.TestCase):
    """The batch dispatch, scene_clear and its job, executed: their branching
    and arithmetic are the .c source's, against a fake engine."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = BRIDGE.read_text(encoding="utf-8")
        cls.messages = MESSAGES.read_text(encoding="utf-8")

    def setUp(self) -> None:
        self.game = _Game()
        self.logs: list[str] = []
        self.posted: list[Any] = []
        self.gone: list[int] = []
        self.drive_gone: list[int] = []
        ns: dict[str, Any] = {"_S": _LogString, "array": EnforceArray, "map": EnforceMap}
        for name in _MESSAGE_FAKES:
            fake_class(self.messages, name, ns, constants={"MCP_ARG_FLOAT_UNSET": _FLOAT_MAX})

        def unexpected(*_args: Any) -> None:
            raise AssertionError("not a scene path")

        ns.update(
            {
                "GetGame": lambda: self.game,
                "Vector": lambda x, y, z: EnforceVector((x, y, z)),
                "Log": lambda line: self.logs.append(line.text),
                "PostResult": self.posted.append,
                "MCPBotControl": SimpleNamespace(OnObjectGone=lambda _bridge, object_id: self.gone.append(object_id)),
                "MCPSurvivorDrive": SimpleNamespace(
                    OnObjectGone=lambda _bridge, object_id: self.drive_gone.append(object_id)
                ),
                "this": object(),
                "EntityAI": SimpleNamespace(
                    Cast=lambda obj: obj if isinstance(obj, _Entity) and obj.entity else None
                ),
                "Object": SimpleNamespace(Cast=lambda obj: obj if isinstance(obj, _Entity) else None),
                "float": SimpleNamespace(MAX=_FLOAT_MAX),
                "IsSpawnReady": unexpected,
                "IsSeatReady": unexpected,
                "PostSeatSuccess": unexpected,
                # vanilla values (core.py mirrors them); RF_DEFAULT is a stand-in.
                "ECE_PLACE_ON_SURFACE": 1060,
                "ECE_INITAI": ECE_INITAI,
                "ECE_CREATEPHYSICS": 1024,
                "ECE_TRACE": 4,
                "ECE_EQUIP_ATTACHMENTS": 8192,
                "ECE_NOPERSISTENCY_WORLD": 8388608,
                "RF_DEFAULT": 512,
                "m_Scenes": EnforceMap(),
                "m_RuntimeObjects": EnforceMap(),
                "m_Jobs": EnforceMap(),
                "m_NextBatchObjectId": _bridge_const(self.bridge, "BATCH_OBJECT_ID_BASE"),
                "m_ElapsedS": 0.0,
            }
        )
        for name in (
            "SCENE_ID_MAX_CHARS",
            "SCENE_ENTRIES_MAX",
            "SPAWN_BATCH_PER_TICK_MAX",
            "JOB_TIMEOUT_S",
            "SPAWN_LIFETIME_MAX_S",
        ):
            ns[name] = _bridge_const(self.bridge, name)
        for signature in _SCENE_METHODS + _SUPPORT_METHODS:
            translate(self.bridge, signature, ns, class_types=_BRIDGE_CLASS_TYPES, members=_BRIDGE_MEMBERS)
        translate(
            _world_spawn_branch(self.bridge),
            _WORLD_SPAWN_BRANCH,
            ns,
            class_types=_BRIDGE_CLASS_TYPES,
            members=_BRIDGE_MEMBERS,
        )
        self.ns = ns

    # Building commands and the world.
    def _item(self, type_name: str = "Barrel_Green", pos=(10.0, 2.0, 20.0), **fields: Any) -> Any:
        item = self.ns["MCPSpawnBatchItem"]()
        item.type = EnforceString.of(type_name)
        item.pos = EnforceArray(float(value) for value in pos)
        item.flags = fields.get("flags", 0)
        item.rotation = fields.get("rotation", 0)
        item.ground = fields.get("ground", False)
        if "ypr" in fields:
            item.ypr = EnforceArray(float(value) for value in fields["ypr"])
            item.ypr_set = True
        return item

    def _command(self, command_id: int, scene_id: str = "", items: list[Any] | None = None) -> Any:
        args = self.ns["MCPArgs"]()
        args.scene_id = EnforceString.of(scene_id)
        if items is not None:
            args.items = EnforceArray(items)
        command = self.ns["MCPCommand"]()
        command.id = command_id
        command.args = args
        return command

    def _batch(self, items: list[Any], scene_id: str = "", command_id: int = 7) -> tuple[bool, Any]:
        result = self.ns["MCPResult"]()
        post_now = self.ns["DispatchWorldSpawnBatch"](self._command(command_id, scene_id, items), result)
        return post_now, result

    def _world_spawn(self, command_id: int, scene_id: str, type_name: str = "Barrel_Green") -> tuple[bool, Any]:
        """A world_spawn command through Dispatch's own world_spawn branch
        (_world_spawn_branch): (postNow, result)."""
        command = self._command(command_id, scene_id)
        command.cmd = EnforceString.of("world_spawn")
        command.args.type = EnforceString.of(type_name)
        command.args.pos = EnforceArray([10.0, 2.0, 20.0])
        result = self.ns["MCPResult"]()
        post_now = self.ns["WorldSpawnBranch"](command, result)
        return post_now, result

    def _clear(self, scene_id: str, command_id: int = 50) -> tuple[bool, Any]:
        result = self.ns["MCPResult"]()
        post_now = self.ns["DispatchSceneClear"](self._command(command_id, scene_id), result)
        return post_now, result

    def _register(self, scene_id: str, object_id: int, obj: _Entity) -> None:
        self.ns["m_RuntimeObjects"].Insert(object_id, obj)
        self.ns["AddSceneEntry"](EnforceString.of(scene_id), object_id, obj)

    def _entries(self, scene_id: str) -> Any:
        return self.ns["m_Scenes"].Get(EnforceString.of(scene_id))

    def _engine_removes(self, *objects: _Entity) -> None:
        # Enforce nulls every reference to a deleted object.
        targets = {id(obj) for obj in objects}
        for index in range(self.ns["m_Scenes"].Count()):
            for entry in self.ns["m_Scenes"].GetElement(index):
                if entry.obj is not None and id(entry.obj) in targets:
                    entry.obj = None

    def _run_jobs_at(self, elapsed_s: float) -> None:
        self.ns["m_ElapsedS"] = elapsed_s
        self.ns["ProcessJobs"]()

    # world_spawn_batch
    def test_a_command_creates_every_item_in_its_dispatch_and_answers_at_once(self) -> None:
        items = [
            self._item("Barrel_Green", (10.0, 2.0, 20.0)),
            self._item("ZmbM_CitizenASkinny", (11.0, 0.0, 21.0), flags=1060, ground=True, ypr=(90.0, -5.0, 0.0)),
            self._item("NoSuchClass", (1.0, 2.0, 3.0)),
            self._item("CarBattery", (12.0, 3.0, 22.0), rotation=4),
        ]
        post_now, result = self._batch(items, scene_id="a")
        self.assertIs(post_now, True)
        self.assertIs(result.ok, True)
        batch = result.world_spawn_batch
        self.assertEqual((batch.status.text, batch.created, batch.failed), ("partial", 3, 1))
        rows = [(row.index, row.ok, row.object_id, row.type.text, row.error.text) for row in batch.items]
        self.assertEqual(
            rows,
            [
                (0, True, 1000000000, "Barrel_Green", ""),
                (1, True, 1000000001, "ZmbM_CitizenASkinny", ""),
                (2, False, 0, "NoSuchClass", "unknown_type"),
                (3, True, 1000000002, "CarBattery", ""),
            ],
        )
        # flags 0 is world_spawn's ECE_PLACE_ON_SURFACE; the infected gets
        # ECE_INITAI added; ground is surface_query's y; rotation 0 is RF_DEFAULT.
        made = [(type_name, pos, flags, rotation) for type_name, pos, flags, rotation, _obj in self.game.created]
        self.assertEqual(
            made,
            [
                ("Barrel_Green", [10.0, 2.0, 20.0], 1060, 512),
                ("ZmbM_CitizenASkinny", [11.0, 42.5, 21.0], 1060 | ECE_INITAI, 512),
                ("CarBattery", [12.0, 3.0, 22.0], 1060, 4),
            ],
        )
        objects = [obj for *_rest, obj in self.game.created]
        self.assertEqual([obj.orientation for obj in objects], [None, [90.0, -5.0, 0.0], None])
        # Each created object has its own id, in the registry and in the scene.
        runtime = self.ns["m_RuntimeObjects"]
        self.assertEqual([runtime.Get(1000000000 + index) for index in range(3)], objects)
        self.assertEqual(
            [(entry.object_id, entry.type.text, entry.obj) for entry in self._entries("a")],
            [(1000000000 + index, obj.type_name, obj) for index, obj in enumerate(objects)],
        )
        self.assertEqual(self.ns["m_NextBatchObjectId"], 1000000003)
        self.assertEqual(self.ns["m_Jobs"].Count(), 0)
        self.assertEqual(self.posted, [])
        self.assertEqual(self.logs, ["spawn batch id=7 items=4 created=3 failed=1"])
        # The next command counts on.
        _post_now, result = self._batch([self._item(), self._item()], command_id=8)
        self.assertEqual([row.object_id for row in result.world_spawn_batch.items], [1000000003, 1000000004])
        self.assertIsNone(self._entries(""))

    def test_a_refused_command_creates_nothing(self) -> None:
        limit = self.ns["SPAWN_BATCH_PER_TICK_MAX"]
        bad_ypr = self._item("Barrel_Green", ypr=(0.0, float("nan"), 0.0))
        both = self._item("Barrel_Green", rotation=2, ypr=(0.0, 0.0, 0.0))
        for name, items in (
            ("no_items", []),
            ("one_over_the_limit", [self._item() for _ in range(limit + 1)]),
            ("bad_ypr_last", [self._item(), self._item(), bad_ypr]),
            ("rotation_and_ypr_last", [self._item(), both]),
            ("null_item", [self._item(), None]),
        ):
            with self.subTest(case=name):
                post_now, result = self._batch(items)
                self.assertIs(post_now, True)
                self.assertIs(result.ok, False)
                self.assertEqual(result.error.text, "bad_args")
                self.assertIsNone(result.world_spawn_batch)
        post_now, result = self._batch(None)  # type: ignore[arg-type]
        self.assertEqual(result.error.text, "bad_args")
        self.assertEqual(self.game.created, [])
        self.assertEqual(self.ns["m_RuntimeObjects"].Count(), 0)
        # The limit itself is one command.
        _post_now, result = self._batch([self._item() for _ in range(limit)])
        self.assertEqual(result.world_spawn_batch.created, limit)

    def test_the_scene_cap_is_checked_where_the_group_is_created(self) -> None:
        cap = self.ns["SCENE_ENTRIES_MAX"]
        for index in range(cap - 3):
            self._register("full", 10 + index, _Entity("Barrel_Green"))
        # A group that would pass the cap is refused whole.
        _post_now, result = self._batch([self._item() for _ in range(4)], scene_id="full")
        self.assertEqual((result.ok, result.error.text), (False, "scene_full"))
        self.assertEqual(self.game.created, [])
        self.assertEqual(self._entries("full").Count(), cap - 3)
        # Three more fill it; a deleted entry still counts, so one more is refused.
        _post_now, result = self._batch([self._item() for _ in range(3)], scene_id="full")
        self.assertEqual(result.world_spawn_batch.created, 3)
        self.assertEqual(self._entries("full").Count(), cap)
        self._engine_removes(*[entry.obj for entry in self._entries("full")])
        _post_now, result = self._batch([self._item()], scene_id="full")
        self.assertEqual(result.error.text, "scene_full")
        self.assertEqual(len(self.game.created), 3)

    def test_a_group_registers_every_object_it_creates(self) -> None:
        # More than four valid items: each created object is registered under
        # its row's id and type.
        names = ["Barrel_Green", "CarBattery", "ZmbM_CitizenASkinny", "CivilianSedan", "Barrel_Green", "CarBattery"]
        _post_now, result = self._batch(
            [self._item(name, (10.0 + index, 2.0, 20.0)) for index, name in enumerate(names)], scene_id="a"
        )
        rows = list(result.world_spawn_batch.items)
        self.assertEqual([(row.index, row.ok) for row in rows], [(index, True) for index in range(len(names))])
        objects = [obj for *_rest, obj in self.game.created]
        self.assertEqual([obj.type_name for obj in objects], names)
        self.assertEqual(
            [(entry.object_id, entry.type.text, entry.obj) for entry in self._entries("a")],
            [(row.object_id, row.type.text, obj) for row, obj in zip(rows, objects)],
        )
        runtime = self.ns["m_RuntimeObjects"]
        self.assertEqual([runtime.Get(row.object_id) for row in rows], objects)

    # world_spawn with a scene, through Dispatch's own branch
    def test_world_spawn_registers_its_own_object_under_its_scene(self) -> None:
        # Each object is registered once, under the scene its command named;
        # one with no scene_id is registered nowhere.
        spawns = ((5, "a", "Barrel_Green"), (6, "b", "CarBattery"), (7, "a", "CarBattery"), (8, "", "Barrel_Green"))
        for command_id, scene_id, type_name in spawns:
            with self.subTest(command_id=command_id):
                post_now, result = self._world_spawn(command_id, scene_id, type_name)
                self.assertEqual((post_now, result.error.text), (False, ""))
        self.assertEqual(len(self.game.created), 4)
        spawned = {command_id: created[4] for (command_id, *_rest), created in zip(spawns, self.game.created)}
        self.assertEqual(
            [(entry.object_id, entry.type.text, entry.obj) for entry in self._entries("a")],
            [(5, "Barrel_Green", spawned[5]), (7, "CarBattery", spawned[7])],
        )
        self.assertEqual(
            [(entry.object_id, entry.type.text, entry.obj) for entry in self._entries("b")],
            [(6, "CarBattery", spawned[6])],
        )
        self.assertEqual(self.ns["m_Scenes"].Count(), 2)
        self.assertIs(self.ns["m_Jobs"].Get(5).subject, spawned[5])

    def test_world_spawn_registers_nothing_without_its_own_spawn_job(self) -> None:
        # No job for the id: the class is unknown, so nothing was created.
        post_now, result = self._world_spawn(8, "a", "NoSuchClass")
        self.assertEqual((post_now, result.error.text), (True, "unknown_type"))
        self.assertEqual(self.ns["m_Jobs"].Count(), 0)
        self.assertIsNone(self._entries("a"))
        # A job for other args: an older command's, under an id the daemon
        # reused. Its object is not this command's.
        self._world_spawn(9, "")
        older = self.ns["m_Jobs"].Get(9)
        post_now, result = self._world_spawn(9, "a", "NoSuchClass")
        self.assertEqual((post_now, result.error.text), (True, "unknown_type"))
        self.assertIs(self.ns["m_Jobs"].Get(9), older)
        self.assertIsNone(self._entries("a"))
        self.assertEqual(len(self.game.created), 1)

    # scene_clear
    def test_scene_clear_refuses_a_hierarchy_shared_with_another_scene(self) -> None:
        car = _Entity("CivilianSedan")
        battery = _Entity("CarBattery", holder=car)
        # Through an unregistered carrier at any depth: a backpack in the car.
        backpack = _Entity("AliceBag_Green", holder=car)
        can = _Entity("Barrel_Green", holder=backpack)
        # Through the entity tree: a survivor in a seat of the car.
        seated = _Entity("SurvivorM_Mirek", parent=car)
        self._register("a", 1000000000, car)
        self._register("b", 1000000001, battery)
        self._register("c", 1000000002, can)
        self._register("d", 1000000003, seated)
        for scene, carried, carried_scene in (
            ("a", 1000000001, "b"),
            ("b", 1000000001, "b"),
            ("c", 1000000002, "c"),
            ("d", 1000000003, "d"),
        ):
            with self.subTest(scene=scene):
                post_now, result = self._clear(scene)
                self.assertIs(post_now, True)
                self.assertEqual((result.ok, result.error.text), (False, "scene_shared"))
                report = result.scene_clear
                self.assertEqual(
                    (report.carrier_id, report.carrier_scene_id.text, report.carried_id, report.carried_scene_id.text),
                    (1000000000, "a", carried, carried_scene),
                )
        # Nothing was deleted, released or queued.
        self.assertEqual(self.game.deleted, [])
        self.assertEqual(self.gone, [])
        self.assertEqual(self.drive_gone, [])
        self.assertEqual(self.ns["m_RuntimeObjects"].Count(), 4)
        self.assertEqual(self.ns["m_Jobs"].Count(), 0)
        self.assertIn("scene clear refused scene_shared carrier_id=1000000000 carried_id=1000000001", self.logs)

    def test_scene_clear_deletes_a_holder_and_reads_back_what_it_holds(self) -> None:
        car = _Entity("CivilianSedan")
        battery = _Entity("CarBattery", holder=car)
        # Held through an unregistered holder at any depth: a can in a bag in the car.
        bag = _Entity("AliceBag_Green", holder=car)
        can = _Entity("Barrel_Green", holder=bag)
        # A child in the entity tree that is no EntityAI is not held: it gets
        # its own delete.
        sign = _Entity("Land_Sign", parent=car, entity=False)
        self._register("a", 1000000000, car)
        self._register("a", 1000000001, battery)
        self._register("a", 1000000002, sign)
        self._register("a", 1000000003, can)
        post_now, _result = self._clear("a", command_id=51)
        self.assertIs(post_now, False)
        # What the car holds goes with it: the call deletes the car and the sign.
        self.assertEqual(self.game.deleted, [car, sign])
        self.assertEqual(self.gone, [1000000000, 1000000001, 1000000002, 1000000003])
        self.assertEqual(self.drive_gone, self.gone)
        self.assertEqual(self.ns["m_RuntimeObjects"].Count(), 0)
        self.assertEqual(self.logs[-1], "job queued id=51 kind=scene_clear pending=4 delete=2")
        self._engine_removes(car, battery, sign, can)
        self._run_jobs_at(0.1)
        self.assertEqual(len(self.posted), 1)
        posted = self.posted[0]
        self.assertEqual((posted.id, posted.ok), (51, True))
        report = posted.scene_clear
        self.assertEqual((report.deleted, report.already_deleted, report.errors), (4, 0, 0))
        self.assertEqual(self.ns["m_Jobs"].Count(), 0)
        self.assertEqual([entry.obj for entry in self._entries("a")], [None, None, None, None])

    def test_a_seated_object_is_deleted_through_its_own_entry(self) -> None:
        # Deleting a vehicle ejects its crew (docs/VEHICLE_TESTING.md), so a
        # seat is not a hold: what sits in the car gets its own delete.
        car = _Entity("CivilianSedan")
        seated = _Entity("SurvivorM_Mirek", parent=car)
        # An unregistered player seated in the car, with a scene object in its
        # hands: the player is ejected and the object is deleted out of them.
        player = _Entity("SurvivorM_Boris", parent=car)
        held = _Entity("Barrel_Green", holder=player)
        # What the seated survivor holds still goes with it.
        bag = _Entity("AliceBag_Green", holder=seated)
        self._register("a", 1000000000, car)
        self._register("a", 1000000001, seated)
        self._register("a", 1000000002, held)
        self._register("a", 1000000003, bag)
        post_now, _result = self._clear("a", command_id=53)
        self.assertIs(post_now, False)
        self.assertEqual(self.game.deleted, [car, seated, held])
        self.assertNotIn(player, self.game.deleted)
        self.assertEqual(self.logs[-1], "job queued id=53 kind=scene_clear pending=4 delete=3")
        self._engine_removes(car, seated, held, bag)
        self._run_jobs_at(0.1)
        report = self.posted[0].scene_clear
        self.assertEqual((report.deleted, report.already_deleted, report.errors), (4, 0, 0))

    def test_unregistered_objects_follow_their_carrier(self) -> None:
        hands = _Entity("SurvivorM_Mirek")
        held = _Entity("Barrel_Green", holder=hands)
        car = _Entity("CivilianSedan")
        _wheel = _Entity("HatchbackWheel", holder=car)
        static = _Entity("Land_Wall", entity=False)
        self._register("a", 1000000000, held)
        self._register("a", 1000000001, car)
        self._register("a", 1000000002, static)
        _post_now, _result = self._clear("a")
        # The held object is deleted out of the hands; the wheel is left to go
        # with the car, as object_delete leaves it.
        self.assertEqual(self.game.deleted, [held, car, static])

    def test_a_live_reference_keeps_the_clear_job_waiting_until_its_deadline(self) -> None:
        stays = _Entity("Barrel_Green")
        goes = _Entity("Barrel_Green")
        before = _Entity("Barrel_Green")
        self._register("a", 1000000000, stays)
        self._register("a", 1000000001, goes)
        self._register("a", 1000000002, before)
        self._engine_removes(before)
        post_now, _result = self._clear("a", command_id=52)
        self.assertIs(post_now, False)
        self._engine_removes(goes)
        deadline = self.ns["JOB_TIMEOUT_S"]
        for elapsed in (0.1, deadline - 0.1, deadline):
            with self.subTest(elapsed=elapsed):
                self._run_jobs_at(elapsed)
                self.assertEqual(self.posted, [])
                self.assertEqual(self.ns["m_Jobs"].Count(), 1)
        self._run_jobs_at(deadline + 0.1)
        self.assertEqual(len(self.posted), 1)
        report = self.posted[0].scene_clear
        self.assertEqual((self.posted[0].id, self.posted[0].ok), (52, True))
        self.assertEqual((report.deleted, report.already_deleted, report.errors), (1, 1, 1))
        self.assertEqual(list(report.error_ids), [1000000000])
        self.assertEqual(self.ns["m_Jobs"].Count(), 0)

    def test_an_unknown_or_emptied_scene_answers_at_once(self) -> None:
        post_now, result = self._clear("nobody")
        self.assertEqual((post_now, result.ok), (True, True))
        report = result.scene_clear
        self.assertEqual((report.deleted, report.already_deleted, report.errors), (0, 0, 0))
        gone = _Entity("Barrel_Green")
        self._register("a", 1000000000, gone)
        self._engine_removes(gone)
        post_now, result = self._clear("a")
        self.assertEqual((post_now, result.ok, result.scene_clear.already_deleted), (True, True, 1))
        self.assertEqual(self.game.deleted, [])
        _post_now, result = self._clear("a b")
        self.assertEqual((result.ok, result.error.text), (False, "bad_args"))

    def test_scene_entities_reads_live_and_deleted_rows(self) -> None:
        live = _Entity("Barrel_Green", (5.0, 6.0, 7.0))
        gone = _Entity("CarBattery")
        self._register("a", 1000000000, live)
        self._register("a", 1000000001, gone)
        self._engine_removes(gone)
        result = self.ns["MCPResult"]()
        self.assertIs(self.ns["DispatchSceneEntities"](self._command(9, "a"), result), True)
        self.assertEqual(
            [(row.object_id, row.type.text, row.deleted, list(row.pos)) for row in result.scene_entities],
            [(1000000000, "Barrel_Green", False, [5.0, 6.0, 7.0]), (1000000001, "CarBattery", True, [])],
        )


# --- Every message member those bodies use, against MCPMessages.c --------------

# (class, member) -> the declared type the scene code relies on. The test below
# collects every member access of _SCENE_METHODS and requires this table to
# name exactly those, so a new access needs its row here.
_SCENE_MEMBER_TYPES: dict[tuple[str, str], str] = {
    ("MCPArgs", "flags"): "int",
    ("MCPArgs", "items"): "ref array<ref MCPSpawnBatchItem>",
    ("MCPArgs", "pos"): "ref array<float>",
    ("MCPArgs", "rotation"): "int",
    ("MCPArgs", "scene_id"): "string",
    ("MCPArgs", "type"): "string",
    ("MCPCommand", "args"): "ref MCPArgs",
    ("MCPCommand", "id"): "int",
    ("MCPJob", "args"): "ref MCPArgs",
    ("MCPJob", "deadline_s"): "float",
    ("MCPJob", "id"): "int",
    ("MCPJob", "kind"): "string",
    ("MCPJob", "scene_clear"): "ref MCPSceneClear",
    ("MCPJob", "scene_pending"): "ref array<ref MCPSceneEntry>",
    ("MCPJob", "subject"): "Object",
    ("MCPJob", "tick_dispatch"): "int",
    ("MCPJob", "tick_poll_callback"): "int",
    ("MCPJob", "tick_poll_sent"): "int",
    ("MCPResult", "error"): "string",
    ("MCPResult", "id"): "int",
    ("MCPResult", "ok"): "bool",
    ("MCPResult", "scene_clear"): "ref MCPSceneClear",
    ("MCPResult", "scene_entities"): "ref array<ref MCPSceneEntity>",
    ("MCPResult", "tick_dispatch"): "int",
    ("MCPResult", "tick_poll_callback"): "int",
    ("MCPResult", "tick_poll_sent"): "int",
    ("MCPResult", "world_spawn_batch"): "ref MCPSpawnBatch",
    ("MCPSceneClear", "already_deleted"): "int",
    ("MCPSceneClear", "carried_id"): "int",
    ("MCPSceneClear", "carried_scene_id"): "string",
    ("MCPSceneClear", "carrier_id"): "int",
    ("MCPSceneClear", "carrier_scene_id"): "string",
    ("MCPSceneClear", "deleted"): "int",
    ("MCPSceneClear", "error_ids"): "ref array<int>",
    ("MCPSceneClear", "errors"): "int",
    ("MCPSceneEntity", "deleted"): "bool",
    ("MCPSceneEntity", "object_id"): "int",
    ("MCPSceneEntity", "pos"): "ref array<float>",
    ("MCPSceneEntity", "type"): "string",
    ("MCPSceneEntry", "obj"): "Object",
    ("MCPSceneEntry", "object_id"): "int",
    ("MCPSceneEntry", "type"): "string",
    ("MCPSpawnBatch", "created"): "int",
    ("MCPSpawnBatch", "failed"): "int",
    ("MCPSpawnBatch", "items"): "ref array<ref MCPSpawnBatchRow>",
    ("MCPSpawnBatch", "status"): "string",
    ("MCPSpawnBatchItem", "flags"): "int",
    ("MCPSpawnBatchItem", "ground"): "bool",
    ("MCPSpawnBatchItem", "pos"): "ref array<float>",
    ("MCPSpawnBatchItem", "rotation"): "int",
    ("MCPSpawnBatchItem", "type"): "string",
    ("MCPSpawnBatchItem", "ypr"): "ref array<float>",
    ("MCPSpawnBatchItem", "ypr_set"): "bool",
    ("MCPSpawnBatchRow", "error"): "string",
    ("MCPSpawnBatchRow", "index"): "int",
    ("MCPSpawnBatchRow", "object_id"): "int",
    ("MCPSpawnBatchRow", "ok"): "bool",
    ("MCPSpawnBatchRow", "type"): "string",
    ("MCPSpawnValidation", "error"): "string",
    ("MCPSpawnValidation", "flags"): "int",
    ("MCPSpawnValidation", "ok"): "bool",
    ("MCPSpawnValidation", "pos"): "vector",
    ("MCPSpawnValidation", "rotation"): "int",
}

_MEMBER_CHAIN = re.compile(r"(?<![\w.])([A-Za-z_]\w*)((?:\.[A-Za-z_]\w*)+)(\s*\()?")
_LOCAL_DECLARATION = re.compile(
    r"^(?P<type>[A-Za-z_]\w*(?:\s*<[^=;(){}]*>)?)\s+(?P<name>[A-Za-z_]\w*)\s*(?:=|$)"
)
_NOT_TYPES = frozenset({"return", "else", "new", "if", "while", "delete"})


def _message_classes(messages: str) -> set[str]:
    return set(re.findall(r"^class\s+([A-Za-z_]\w*)", clean(messages), re.M))


def _message_class_of(type_name: str, classes: set[str]) -> str | None:
    base = type_name.replace("ref ", "").strip()
    return base if base in classes else None


def _scene_member_uses(bridge: str, messages: str) -> dict[tuple[str, str], str]:
    """(class, member) -> declared type, for every message member the scene
    methods read or write; an undeclared one fails here."""
    classes = _message_classes(messages)
    declared = {name: dict((member, kind) for kind, member in class_members(messages, name)) for name in classes}
    used: dict[tuple[str, str], str] = {}
    for signature in _SCENE_METHODS:
        params = signature[signature.index("(") + 1 : signature.rindex(")")]
        variables: dict[str, str] = {}
        for part in [piece.strip() for piece in params.split(",") if piece.strip()]:
            kind, name = part.rsplit(None, 1)
            variables[name] = kind
        body = re.sub(r'"(?:\\.|[^"\\])*"', '""', method_body(bridge, signature))
        for chunk in re.split(r"[;{}]", body):
            match = _LOCAL_DECLARATION.match(chunk.strip())
            if match and match.group("type") not in _NOT_TYPES:
                variables[match.group("name")] = match.group("type")
        for match in _MEMBER_CHAIN.finditer(body):
            kind = variables.get(match.group(1))
            if kind is None:
                continue
            segments = match.group(2)[1:].split(".")
            for position, member in enumerate(segments):
                owner = _message_class_of(kind, classes)
                if owner is None:
                    break
                called = position == len(segments) - 1 and match.group(3) is not None
                if called or member not in declared[owner]:
                    raise AssertionError(f"{signature}: {owner}.{member} is not a declared member")
                kind = declared[owner][member]
                used[(owner, member)] = kind
    return used


class SceneMessageContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = BRIDGE.read_text(encoding="utf-8")
        cls.messages = MESSAGES.read_text(encoding="utf-8")

    def test_every_member_the_scene_code_uses_is_declared_with_the_type_it_needs(self) -> None:
        used = _scene_member_uses(self.bridge, self.messages)
        self.assertEqual(sorted(used), sorted(_SCENE_MEMBER_TYPES))
        for key, kind in sorted(_SCENE_MEMBER_TYPES.items()):
            with self.subTest(member=".".join(key)):
                self.assertEqual(used[key], kind)

    def test_the_collector_sees_a_missing_member(self) -> None:
        # Negative control: a body that reads a member MCPSpawnBatchRow lacks.
        broken = self.bridge.replace("row.object_id = m_NextBatchObjectId;", "row.object_ref = m_NextBatchObjectId;")
        with self.assertRaisesRegex(AssertionError, "MCPSpawnBatchRow.object_ref"):
            _scene_member_uses(broken, self.messages)

    def test_the_daemon_reads_only_keys_the_bridge_declares(self) -> None:
        def members(name: str) -> set[str]:
            return {member for _kind, member in class_members(self.messages, name)}

        self.assertLessEqual(set(world_results._SCENE_SHARED_KEYS), members("MCPSceneClear"))
        self.assertLessEqual({"deleted", "already_deleted", "errors", "error_ids"}, members("MCPSceneClear"))
        self.assertLessEqual({"index", "ok", "object_id", "error", "type"}, members("MCPSpawnBatchRow"))
        self.assertLessEqual({"status", "created", "failed", "items"}, members("MCPSpawnBatch"))
        self.assertLessEqual({"object_id", "type", "deleted", "pos"}, members("MCPSceneEntity"))
        self.assertLessEqual({"world_spawn_batch", "scene_entities", "scene_clear"}, members("MCPResult"))
        self.assertLessEqual(
            {"type", "pos", "flags", "rotation", "ground", "ypr", "ypr_set"}, members("MCPSpawnBatchItem")
        )
        self.assertEqual(
            {member for _kind, member in class_members(self.messages, "MCPSpawnBatchItem")},
            set(loopback._SPAWN_BATCH_ITEM_REQUIRED | loopback._SPAWN_BATCH_ITEM_OPTIONAL),
        )


if __name__ == "__main__":
    unittest.main()
