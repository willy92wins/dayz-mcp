"""object_set_health: set one object's Health and read it back (inbox c703).

These tests pin the wire contract, the lease partition, the admission of an
older PBO, the prune contract and the Enforce source the game will compile.
They do not launch DayZ or compile the addon.

Engine calls the handler decides with, checked read-only against the DayZ
1.30 vanilla scripts (P:\\scripts); the EntityAI cast and Object.GetType,
which only names the class in the reply, are left out:

- Object.SetHealth01: 3_game/entities/object.c:1100
  void SetHealth01(string zoneName, string healthType, float coef)
- Object.GetHealth01: 3_game/entities/object.c:1001
  proto native float GetHealth01(string zoneName, string healthType)
- Object.GetMaxHealth: 3_game/entities/object.c:1008
  proto native float GetMaxHealth(string zoneName, string healthType)
- Object.GetHealthLevel: 3_game/entities/object.c:1171
  proto native int GetHealthLevel(string zone = "")
- EntityAI.GetInventory: 3_game/entities/entityai.c:1884
  proto native GameInventory GetInventory()
- InventorySlots.GetSlotIdFromString: 3_game/systems/inventory/inventoryslots.c:42
  static proto native int GetSlotIdFromString(string slot_name)
- InventorySlots.IsSlotIdValid: 3_game/systems/inventory/inventoryslots.c:59
  static proto native bool IsSlotIdValid(int slotId)
- GameInventory.HasAttachmentSlot: 3_game/systems/inventory/inventory.c:188
  bool HasAttachmentSlot(int slotId)
- GameInventory.FindAttachment: 3_game/systems/inventory/inventory.c:224
  proto native EntityAI FindAttachment(int slot)
- Object.IsMan: 3_game/entities/object.c:523
  bool IsMan()
- CarWheel.EEHealthLevelChanged: 4_world/entities/core/inherited/inventoryitem.c:220
  override void EEHealthLevelChanged(int oldLevel, int newLevel, string zone)

Added after the 9/11 in-game round, where GetMaxHealth threw a Virtual
Machine Exception for an object with no damage system and for a zone the
object does not have (cites read on the maintainer's PC):

- Object.ConfigIsExisting: 3_game/entities/object.c:929
  proto native bool ConfigIsExisting(string entryName)
  The handler asks for the DamageSystem class, where the damage system's data
  comes from.
- Object.GetDamageZones: 3_game/entities/object.c:1153, the names vanilla's
  TransferEntityHealth looks a zone up in (miscgameplayfunctions.c:398-410)

Object.HasDamageSystem (object.c:976) is not the guard: its comment at
:972-976 says it only tells whether the damage system is initialized, and in
the 10/11 round it read false on the 1.30 server for barrels, wheels and other
items whose health GetMaxHealth reads, so a guard on it refused them all.
"""
from __future__ import annotations

import json
import math
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dayz_mcp import bridge_errors, loopback, result_prune, server
from dayz_mcp.bridge_readiness import EXPECTED_SERVER_ARG_CONTRACT_HASH
from dayz_mcp.core import EXPECTED_BRIDGE_VERSION
from dayz_mcp.server import (
    DEFAULT_TOOL_TIMEOUT_S,
    LEASE_TOOL_LINE,
    ServerConfig,
    ToolError,
    _BRIDGE_COMMAND_TOOLS,
    build_app,
)
from dayz_mcp.session_coordination import (
    READ_ONLY_COMMANDS,
    SessionCoordinator,
    command_requires_lease,
)
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import EnforceArray, EnforceString, fake_class, translate
from tests.fence_helpers import INST_SERVER, PID_SERVER, bind_both_peers, bound_queue
from tests.mcp_helpers import _content_json
from tests.player_move_helpers import _LogString


COMMAND = "object_set_health"
DISPATCHER = "DispatchObjectSetHealth"
D1_DESCRIPTION = (
    "set one object's health on the server and read it back in the same "
    "tick. Target by object_id (world_spawn, object_resolve, "
    "inventory_give.object_id, inventory_attach.item_object_id) or by "
    "exact classname within 25 m of pos. With slot, the target is the item "
    "attached to that object in that inventory slot (telemetry_read "
    "object_at lists the slot names in declared_slots). health01 is a "
    "fraction of the zone's maximum Health; an empty zone is the global "
    "health. The reply, object_health, gives health01 and the health level "
    "the object reports (vanilla's usual scale: 0 pristine to 4 ruined), "
    "read before and after the call. A player is refused: use player_heal "
    "or player_kill (target_is_player). An item the game replaces when it "
    "is ruined (a 1.30 car wheel becomes its _Ruined class) is replaced by "
    "the game, not by this tool: read the vehicle again with "
    "telemetry_read object_at to see what sits in the slot."
)
WIRE_OBJECT_ID = {"object_id": 7, "health01": 0.0}
WIRE_SLOT_ZONE = {
    "object_id": 7,
    "health01": 0.0,
    "slot": "Wheel_1_1",
    "zone": "Engine",
}
WIRE_TYPE = {
    "type": "LFQuad_Wheel_Front",
    "pos": [10.0, 5.0, 20.0],
    "health01": 0.5,
}
OBJECT_HEALTH = {
    "type": "LFQuad_Wheel_Front",
    "zone": "",
    "slot": "",
    "health01_requested": 0.0,
    "health01_before": 1.0,
    "health01_after": 0.0,
    "health_level_before": 0,
    "health_level_after": 4,
}
SCRIPTS = addon_root() / "scripts"
BRIDGE_PATH = SCRIPTS / "5_Mission" / "MCPBridge.c"
MESSAGES_PATH = SCRIPTS / "5_Mission" / "MCPMessages.c"
CENSUS_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "bridge_capabilities_v1.json"
)
DISPATCH_CHAIN = "protected void Dispatch(MCPCommand command)"
PREVIOUS_ARG_CONTRACT_HASH = "d0b2a66a36c7078f"
IDENTITY = {
    "platform": "codex",
    "pid": 12,
    "ppid": 1,
    "started_at_utc": "2026-10-24T00:00:00Z",
    "session_id": "R",
    "task_label": "health",
}

_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')
_MEMBER_RE = re.compile(
    r"(?m)(?<![A-Za-z0-9_>])(?P<type>(?:ref\s+)?[A-Za-z_][A-Za-z0-9_]*(?:\s*<[^;{}]+>)?)"
    r"\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*;"
)
_SERVER_CAPS = re.compile(
    r'protected const string SERVER_CAPABILITIES = ((?:"[^"\n]*"(?: \+ )?)+);'
)
_LOCAL_DECLARATION = re.compile(
    r"^\s*(?:int|bool|float|string|vector|Object|EntityAI|GameInventory|"
    r"MCPObjectHealth)\s+(\w+)\s*;\s*$"
)
_DOT_SPACES = re.compile(r"\s*\.\s*")
_NAME_BEFORE_PAREN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
_RECEIVER_BEFORE_NAME = re.compile(r"(?<![\w.])([A-Za-z_][A-Za-z0-9_]*)\.$")
# Each call a read local may sit in, with its receiver: the Object target, or
# vanilla's InventorySlots, whose methods take these arguments by value (the
# docstring's cites). A bare name would reach an MCPBridge helper instead.
_HEALTH_VALUE_APIS = {
    "GetMaxHealth": "target",
    "GetHealth01": "target",
    "GetHealthLevel": "target",
    "SetHealth01": "target",
    "GetSlotIdFromString": "InventorySlots",
}
_DISPATCH_CMD_LITERAL = r'command\s*\.\s*cmd\s*==\s*("(?:\\.|[^"\\\n])*")'
_DISPATCH_CMD_IF = re.compile(r"if\s*\(\s*" + _DISPATCH_CMD_LITERAL + r"\s*\)")
_DISPATCH_CMD_ELSE_IF = re.compile(
    r"else\s+if\s*\(\s*" + _DISPATCH_CMD_LITERAL + r"\s*\)"
)
_DISPATCH_PREAMBLE = (
    "if (!command) { return; } MCPResult result = new MCPResult(); "
    "result.id = command.id; result.tick_poll_sent = m_TickPollSent; "
    "result.tick_poll_callback = m_TickPollCallback; "
    "result.tick_dispatch = m_Tick; bool postNow = true;"
)
_DISPATCH_UNKNOWN = 'result.ok = false; result.error = "unknown_command";'
_DISPATCH_POST_CALL = "if (postNow) { PostResult(result); }"


def _blank_match(match: re.Match[str], *, strings: bool) -> str:
    text = match.group(0)
    if text.startswith('"'):
        if not strings:
            return text
        return '"' + re.sub(r"[^\n]", " ", text[1:-1]) + '"'
    return re.sub(r"[^\n]", " ", text)


def _without_comments(source: str) -> str:
    return _COMMENT_OR_STRING.sub(lambda m: _blank_match(m, strings=False), source)


def _without_comments_and_strings(source: str) -> str:
    # Same length as source: comments and string interiors become spaces;
    # quotes and newlines stay, so brace offsets match the comment-free view.
    return _COMMENT_OR_STRING.sub(lambda m: _blank_match(m, strings=True), source)


def _block_span(source: str, start: int) -> tuple[int, int]:
    view = _without_comments_and_strings(source)
    brace = view.find("{", start)
    if brace < 0:
        raise AssertionError(f"no opening brace at offset {start}")
    depth = 0
    for index in range(brace, len(view)):
        if view[index] == "{":
            depth += 1
        elif view[index] == "}":
            depth -= 1
            if depth == 0:
                return brace + 1, index
    raise AssertionError(f"unterminated block at offset {start}")


def _block_after(source: str, start: int) -> str:
    inner_start, inner_end = _block_span(source, start)
    return source[inner_start:inner_end]


def _method_views(source: str, name: str) -> tuple[str, str]:
    clean = _without_comments(source)
    bare = _without_comments_and_strings(source)
    found = list(re.finditer(rf"\bprotected\s+[\w<>]+\s+{re.escape(name)}\(", bare))
    if len(found) != 1:
        raise AssertionError(f"{name} is declared {len(found)} times")
    inner_start, inner_end = _block_span(bare, found[0].end())
    return clean[inner_start:inner_end], bare[inner_start:inner_end]


def _method_code(source: str, name: str) -> str:
    return _method_views(source, name)[0]


def _if_body(code: str, condition: str) -> str:
    needle = f"if ({condition})"
    if code.count(needle) != 1:
        raise AssertionError(f"{needle!r} occurs {code.count(needle)} times")
    return _block_after(code, code.index(needle))


def _collapse_dots(source: str) -> str:
    return _DOT_SPACES.sub(".", source)


def _skip_ws(source: str, pos: int) -> int:
    while pos < len(source) and source[pos].isspace():
        pos += 1
    return pos


def _ws_one_line(source: str) -> str:
    return re.sub(r"\s+", " ", source).strip()


def _braced_block(source: str, start: int, what: str) -> tuple[int, int]:
    # The block must open right after start, with only blanks between, so a
    # statement without braces cannot borrow a later branch's block.
    view = _without_comments_and_strings(source)
    brace = _skip_ws(view, start)
    if not view.startswith("{", brace):
        raise AssertionError(f"{what} is not followed by its block")
    return _block_span(source, brace)


def _dispatch_body(source: str) -> str:
    # Dispatch is the one declaration of its signature outside comments and
    # strings, so a copy of the signature cannot hand over another block.
    bare = _without_comments_and_strings(source)
    count = bare.count(DISPATCH_CHAIN)
    if count != 1:
        raise AssertionError(f"{DISPATCH_CHAIN!r} is declared {count} times")
    start = bare.index(DISPATCH_CHAIN) + len(DISPATCH_CHAIN)
    inner_start, inner_end = _braced_block(source, start, "Dispatch")
    return _without_comments(source)[inner_start:inner_end]


def _paren_pairs(source: str) -> list[tuple[int, int]]:
    stack: list[int] = []
    pairs: list[tuple[int, int]] = []
    for index, char in enumerate(source):
        if char == "(":
            stack.append(index)
        elif char == ")" and stack:
            pairs.append((stack.pop(), index))
    return pairs


def _local_writes(source: str, name: str) -> list[str]:
    pattern = (
        rf"(?:(?<![\w.])(?:\+\+|--)\s*{name}\b|"
        rf"(?<![\w.]){name}\b\s*(?:\+\+|--)|"
        rf"(?<![\w.]){name}\b\s*[-+*/]?=(?!=))"
    )
    return re.findall(pattern, source)


def _health_locals_unmutated(testcase: unittest.TestCase, bare: str) -> None:
    source = _collapse_dots(bare)
    pairs = _paren_pairs(source)
    for name in ("health01", "zone", "slot"):
        writes = _local_writes(source, name)
        testcase.assertEqual(len(writes), 1, name)
        for match in re.finditer(rf"(?<![\w.]){name}\b", source):
            rest = source[match.end() :]
            testcase.assertFalse(rest.startswith("."), name)
            testcase.assertIsNone(re.match(r"\s*\[", rest), name)
            enclosing = [
                (open_at, close_at)
                for open_at, close_at in pairs
                if open_at < match.start() and close_at >= match.end()
            ]
            enclosing.sort(key=lambda span: span[0], reverse=True)
            for open_at, _close_at in enclosing:
                prefix = source[:open_at].rstrip()
                if re.search(r"\bif$", prefix):
                    continue
                api = _NAME_BEFORE_PAREN.search(prefix)
                if api is None:
                    continue
                testcase.assertIn(api.group(0), _HEALTH_VALUE_APIS, name)
                receiver = _RECEIVER_BEFORE_NAME.search(prefix[: api.start()])
                testcase.assertIsNotNone(receiver, f"{name} in {api.group(0)}")
                assert receiver is not None
                testcase.assertEqual(
                    receiver.group(1), _HEALTH_VALUE_APIS[api.group(0)], name
                )


def _class_members(source: str, class_name: str) -> list[tuple[str, str]]:
    clean = _without_comments(source)
    match = re.search(rf"\bclass\s+{re.escape(class_name)}\b", clean)
    if match is None:
        raise AssertionError(f"class {class_name} is absent")
    body = _block_after(clean, match.end())
    return [(m.group("type"), m.group("name")) for m in _MEMBER_RE.finditer(body)]


def _server_capabilities(source: str) -> list[str]:
    declaration = _SERVER_CAPS.search(source)
    if declaration is None:
        raise AssertionError("SERVER_CAPABILITIES declaration not found")
    return "".join(re.findall(r'"([^"\n]*)"', declaration.group(1))).split(",")


def _in_order(testcase: unittest.TestCase, code: str, tokens: tuple[str, ...]) -> None:
    positions = []
    for token in tokens:
        testcase.assertEqual(code.count(token), 1, token)
        positions.append(code.index(token))
    testcase.assertEqual(positions, sorted(positions))


def _locals_on_top(body: str) -> list[str]:
    names: list[str] = []
    statement_seen = False
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line in ("{", "}"):
            continue
        match = _LOCAL_DECLARATION.match(raw)
        if match is None:
            statement_seen = True
            continue
        if statement_seen:
            raise AssertionError(f"declaration after a statement: {line}")
        names.append(match.group(1))
    return names


def _announce_server(
    state: loopback.ServerState, commands: list[str], ach: str | None
) -> None:
    state.daemon_generation = "gen-health"
    state._peer_caps["server"] = {
        "generation": state.daemon_generation,
        "commands": list(commands),
        "reason": "ok",
        "arg_contract_hash": ach,
        "announced_at": state._now(),
        "instance": INST_SERVER,
    }


def _server_commands() -> list[str]:
    return sorted(_BRIDGE_COMMAND_TOOLS["server"])


def _bound_state(**kwargs: object) -> loopback.ServerState:
    state = loopback.ServerState(key="k", **kwargs)  # type: ignore[arg-type]
    state.install_bound_peer(
        instance=INST_SERVER, role="server", pid=PID_SERVER, run_id="health-run"
    )
    return state


class ObjectSetHealthToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def _call(self, bridge_answer: dict[str, object], **arguments: object):
        call = AsyncMock(return_value=bridge_answer)
        with patch.object(self.runtime, "call_bridge", new=call):
            result = _content_json(await self.app.call_tool(COMMAND, dict(arguments)))
        return result, call

    async def _refused(self, arguments: dict[str, object]) -> str:
        with patch.object(self.runtime, "call_bridge", new=AsyncMock()) as call:
            try:
                await self.app.call_tool(COMMAND, dict(arguments))
            except ToolError as error:
                self.assertFalse(call.await_count)
                return str(error)
        self.fail("expected ToolError")

    async def test_p1_object_id_path_sends_only_the_named_keys(self) -> None:
        # P1
        _result, call = await self._call({"ok": 1, "object_health": OBJECT_HEALTH}, object_id=7, health01=0.0)
        call.assert_awaited_once_with(COMMAND, WIRE_OBJECT_ID, "server", DEFAULT_TOOL_TIMEOUT_S)
        self.assertEqual(call.await_args.args[1], {"object_id": 7, "health01": 0.0})
        _result, call = await self._call(
            {"ok": 1, "object_health": OBJECT_HEALTH},
            object_id=7,
            health01=0.0,
            slot="Wheel_1_1",
            zone="Engine",
        )
        call.assert_awaited_once_with(COMMAND, WIRE_SLOT_ZONE, "server", DEFAULT_TOOL_TIMEOUT_S)
        self.assertEqual(set(call.await_args.args[1]), {"object_id", "health01", "slot", "zone"})

    async def test_p1_empty_slot_is_not_sent(self) -> None:
        # P1 (mutant: the tool sending an empty slot)
        _result, call = await self._call(
            {"ok": 1, "object_health": OBJECT_HEALTH},
            object_id=7,
            health01=0.0,
            slot="",
            zone="",
        )
        self.assertEqual(call.await_args.args[1], WIRE_OBJECT_ID)
        self.assertNotIn("slot", call.await_args.args[1])
        self.assertNotIn("zone", call.await_args.args[1])

    async def test_p2_type_path_sends_exactly_type_pos_health01(self) -> None:
        # P2
        _result, call = await self._call(
            {"ok": 1, "object_health": OBJECT_HEALTH},
            type="LFQuad_Wheel_Front",
            pos=[10.0, 5.0, 20.0],
            health01=0.5,
        )
        call.assert_awaited_once_with(COMMAND, WIRE_TYPE, "server", DEFAULT_TOOL_TIMEOUT_S)
        self.assertEqual(call.await_args.args[1], WIRE_TYPE)

    async def test_n1_health01_is_a_finite_unit_real(self) -> None:
        # N1. The handler: a number out of [0, 1] or not finite is bad_args.
        for value in (-0.01, 1.01, math.nan, math.inf):
            with self.subTest(health01=value):
                text = await self._refused({"object_id": 7, "health01": value})
                self.assertIn("bad_args", text)
                self.assertIn("health01", text)
        # The typed schema (StrictFloat): a bool or a string fails FastMCP's
        # validation before the handler runs, without bad_args, as server_perf's
        # window_s does.
        for value in (True, "0"):
            with self.subTest(health01=value):
                text = await self._refused({"object_id": 7, "health01": value})
                self.assertIn("validation error for object_set_healthArguments", text)
                self.assertIn("health01", text)
                self.assertNotIn("bad_args", text)

    async def test_n2_target_shape_is_object_id_or_type_and_pos(self) -> None:
        # N2. The target contract object_inspect and object_anim use
        # (_object_target_args): bad_args naming the field.
        cases = (
            ({"health01": 0.0}, "type"),
            ({"health01": 0.0, "object_id": -1}, "object_id"),
            ({"health01": 0.0, "type": " LFQuad ", "pos": [1.0, 2.0, 3.0]}, "type"),
        )
        for arguments, field in cases:
            with self.subTest(arguments=arguments):
                text = await self._refused(arguments)
                self.assertIn("bad_args", text)
                self.assertIn(field, text)
        # A pos that is not three numbers is the helper's public bad_pos.
        text = await self._refused(
            {"health01": 0.0, "type": "LFQuad_Wheel_Front", "pos": [1.0, 2.0]}
        )
        self.assertTrue(text.endswith(": bad_pos"), text)
        # StrictInt: a bool object_id fails FastMCP's validation first.
        text = await self._refused({"health01": 0.0, "object_id": True})
        self.assertIn("validation error for object_set_healthArguments", text)
        self.assertIn("object_id", text)
        self.assertNotIn("bad_args", text)


class ObjectSetHealthIngressTest(unittest.TestCase):
    def test_n3_ingress_refuses_range_zero_id_unknown_key_empty_strings_and_client(self) -> None:
        # N3
        self.assertEqual(
            loopback.validate_command_args(COMMAND, {"object_id": 7, "health01": -0.01}),
            (False, "bad_args"),
        )
        self.assertEqual(
            loopback.validate_command_args(COMMAND, {"object_id": 7, "health01": 1.01}),
            (False, "bad_args"),
        )
        self.assertEqual(
            loopback.validate_command_args(COMMAND, {"object_id": 0, "health01": 0.0}),
            (False, "bad_args"),
        )
        self.assertEqual(
            loopback.validate_command_args(
                COMMAND, {"object_id": 7, "health01": 0.0, "extra": 1}
            ),
            (False, "bad_args"),
        )
        self.assertEqual(
            loopback.validate_command_args(
                COMMAND, {"object_id": 7, "health01": 0.0, "slot": ""}
            ),
            (False, "bad_args"),
        )
        self.assertEqual(
            loopback.validate_command_args(
                COMMAND, {"object_id": 7, "health01": 0.0, "zone": ""}
            ),
            (False, "bad_args"),
        )
        self.assertEqual(loopback.peer_for_command(COMMAND), "server")
        self.assertNotIn(COMMAND, loopback.CLIENT_COMMANDS)
        state = loopback.ServerState("test-key")
        bind_both_peers(state)
        self.assertEqual(
            state.enqueue_command(COMMAND, dict(WIRE_OBJECT_ID), peer="client"),
            (400, {"error": "bad_peer"}),
        )

    def test_n4_without_a_lease_is_lease_required(self) -> None:
        # N4
        self.assertNotIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertTrue(command_requires_lease(COMMAND))
        state = loopback.ServerState("test-key")
        bind_both_peers(state)
        _announce_server(state, _server_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        state.coordination = SessionCoordinator(
            token_fn=lambda: "token",
            id_fn=lambda: "lease",
            audit=lambda event: True,
            cleanup=lambda session, lease, reason, active: state.cleanup_owner(
                session, lease, reason, active
            ),
        )
        self.assertEqual(
            state.enqueue_command(COMMAND, dict(WIRE_OBJECT_ID), "server", identity_payload=IDENTITY),
            (423, {"error": "lease_required"}),
        )


class ObjectSetHealthAdmissionTest(unittest.TestCase):
    def test_p3_a_full_census_queues_the_wire_args_on_the_server_peer(self) -> None:
        # P3
        state = _bound_state()
        _announce_server(state, _server_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        status, body = state.enqueue_command(COMMAND, dict(WIRE_OBJECT_ID), peer="server")
        self.assertEqual(status, 200, body)
        queued = [
            {key: value for key, value in item.items() if key != "_broker_pin"}
            for item in bound_queue(state, "server")
        ]
        self.assertEqual(
            queued, [{"id": body["id"], "cmd": COMMAND, "args": WIRE_OBJECT_ID}]
        )
        self.assertEqual(bound_queue(state, "client"), [])

    def test_n5_missing_capability_and_previous_hash_refuse_before_an_id(self) -> None:
        # N5
        state = _bound_state()
        reduced = [name for name in _server_commands() if name != COMMAND]
        _announce_server(state, reduced, EXPECTED_SERVER_ARG_CONTRACT_HASH)
        before = state._next_id
        status, body = state.enqueue_command(COMMAND, dict(WIRE_OBJECT_ID), peer="server")
        self.assertEqual((status, body.get("error")), (409, "bridge_capability_missing"))
        self.assertEqual(state._next_id, before)
        self.assertEqual(bound_queue(state, "server"), [])

        state = _bound_state()
        _announce_server(state, _server_commands(), PREVIOUS_ARG_CONTRACT_HASH)
        before = state._next_id
        status, body = state.enqueue_command(COMMAND, dict(WIRE_OBJECT_ID), peer="server")
        self.assertEqual((status, body.get("error")), (409, "arg_contract_mismatch"))
        self.assertEqual(state._next_id, before)


class ObjectSetHealthPruneTest(unittest.TestCase):
    def test_p4_ok_and_health_not_applied_keep_object_health(self) -> None:
        # P4
        filled = {"ok": 1, "object_health": dict(OBJECT_HEALTH), "inspect": {}}
        kept = result_prune.prune_unfilled_fields(COMMAND, dict(filled))
        self.assertEqual(kept["object_health"], OBJECT_HEALTH)
        self.assertNotIn("inspect", kept)
        refused = result_prune.prune_unfilled_fields(
            COMMAND,
            {"ok": 0, "error": "health_not_applied", "object_health": dict(OBJECT_HEALTH)},
        )
        self.assertEqual(refused["object_health"], OBJECT_HEALTH)
        self.assertEqual(refused["error"], "health_not_applied")

    def test_n6_health_not_applied_carries_observed_read_back_only(self) -> None:
        # N6
        wire = {
            "ok": 0,
            "error": "health_not_applied",
            "object_health": {
                "type": "LFQuad_Wheel_Front",
                "zone": "Engine",
                "slot": "Wheel_1_1",
                "health01_requested": 0.0,
                "health01_before": 1.0,
                "health01_after": 0.25,
                "health_level_before": 0,
                "health_level_after": 3,
            },
        }
        message = str(server._bridge_error(dict(wire), COMMAND))
        self.assertEqual(
            message,
            "health_not_applied; observed=health01_after=0.25 health_level_after=3",
        )
        self.assertIn("observed=health01_after=", message)
        self.assertIn("health_level_after=", message)
        self.assertNotIn("health01_requested", message)
        self.assertNotIn("Wheel_1_1", message)
        self.assertNotIn("Engine", message)
        self.assertNotIn("LFQuad", message)
        self.assertEqual(
            str(server._bridge_error(dict(wire), "object_anim")),
            "health_not_applied",
        )


class ObjectSetHealthCensusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = BRIDGE_PATH.read_text(encoding="utf-8")

    def test_c1_caps_dispatch_daemon_map_and_fixture_name_the_verb_once(self) -> None:
        # C1
        caps = _server_capabilities(self.source)
        self.assertEqual(caps.count(COMMAND), 1)
        self.assertEqual(caps, sorted(caps))
        dispatch = _dispatch_body(self.source)
        fixture = json.loads(CENSUS_FIXTURE.read_text(encoding="utf-8"))
        branch = _if_body(dispatch, f'command.cmd == "{COMMAND}"')
        self.assertEqual(branch.strip(), f"postNow = {DISPATCHER}(command, result);")
        self.assertEqual(_BRIDGE_COMMAND_TOOLS["server"].get(COMMAND), COMMAND)
        self.assertNotIn(COMMAND, _BRIDGE_COMMAND_TOOLS["client"])
        self.assertEqual(fixture["peers"]["server"].get(COMMAND), COMMAND)
        self.assertEqual(list(caps).count(COMMAND), 1)
        self.assertEqual(
            list(_BRIDGE_COMMAND_TOOLS["server"]).count(COMMAND), 1
        )

    def test_c2_the_arg_contract_gains_the_verb_and_the_version_stays(self) -> None:
        # C2
        self.assertEqual(
            server.SERVER_ARG_CONTRACT.get(COMMAND),
            ("health01", "object_id", "pos", "slot", "type", "zone"),
        )
        canonical = server.server_arg_contract_canonical()
        self.assertIn(
            "object_set_health=health01,object_id,pos,slot,type,zone", canonical
        )
        self.assertLess(
            canonical.index("object_resolve=pos,radius,type"),
            canonical.index("object_set_health=health01,object_id,pos,slot,type,zone"),
        )
        self.assertLess(
            canonical.index("object_set_health=health01,object_id,pos,slot,type,zone"),
            canonical.index("player_kill=uid"),
        )
        self.assertEqual(EXPECTED_SERVER_ARG_CONTRACT_HASH, "3b800b7c7ee0674c")
        self.assertNotEqual(EXPECTED_SERVER_ARG_CONTRACT_HASH, PREVIOUS_ARG_CONTRACT_HASH)
        self.assertIn('SERVER_ARG_CONTRACT_HASH = "3b800b7c7ee0674c"', self.source)
        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        self.assertIn('const string MCP_BRIDGE_VERSION = "12";', messages)


class ObjectSetHealthEnforceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = BRIDGE_PATH.read_text(encoding="utf-8")
        self.messages = MESSAGES_PATH.read_text(encoding="utf-8")
        self.code, self.bare = _method_views(self.source, DISPATCHER)

    def test_e1_dispatch_posts_in_the_same_tick_with_no_job(self) -> None:
        # E1. Every return in the method is the literal true (return (true);
        # is rejected on purpose). The final return true; is its own outer
        # statement, so no path falls off the end. Dispatch, the one
        # declaration of its signature outside comments and strings, has a
        # preamble that is the null-command return and the MCPResult setup;
        # for object_set_health it takes only that verb's branch (each branch
        # is command.cmd == a distinct string literal, chained with else, and
        # each condition and the final else open their block at once) and
        # calls PostResult in that tick. PostResult serializes and leaves the
        # send asynchronous (MCPBridge.c:8234).
        dispatch = _dispatch_body(self.source)
        head = _DISPATCH_CMD_IF.search(dispatch)
        self.assertIsNotNone(head, "Dispatch has no command.cmd chain")
        assert head is not None
        preamble = dispatch[: head.start()]
        self.assertEqual(_ws_one_line(preamble), _DISPATCH_PREAMBLE)
        preamble_returns = re.findall(r"\breturn\b([^;]*);", preamble)
        self.assertEqual([value.strip() for value in preamble_returns], [""])
        literals: list[str] = []
        pos = head.start()
        first = _DISPATCH_CMD_IF.match(dispatch, pos)
        self.assertIsNotNone(first, "first Dispatch branch is not command.cmd == literal")
        assert first is not None
        while True:
            if first is not None:
                matched = first
                first = None
            else:
                pos = _skip_ws(dispatch, pos)
                self.assertLess(pos, len(dispatch), "Dispatch chain ended before else")
                self.assertTrue(
                    dispatch.startswith("else", pos),
                    "Dispatch branch is not followed by else",
                )
                after_else = _skip_ws(dispatch, pos + 4)
                self.assertLess(after_else, len(dispatch), "else has no continuation")
                if dispatch.startswith("if", after_else):
                    matched = _DISPATCH_CMD_ELSE_IF.match(dispatch, pos)
                    self.assertIsNotNone(
                        matched,
                        "else if is not command.cmd == a string literal",
                    )
                    assert matched is not None
                else:
                    inner_start, inner_end = _braced_block(dispatch, after_else, "else")
                    self.assertEqual(
                        _ws_one_line(dispatch[inner_start:inner_end]),
                        _DISPATCH_UNKNOWN,
                    )
                    self.assertEqual(
                        _ws_one_line(dispatch[inner_end + 1 :]),
                        _DISPATCH_POST_CALL,
                    )
                    break
            literal = matched.group(1)[1:-1]
            self.assertNotIn(literal, literals, literal)
            literals.append(literal)
            inner_start, inner_end = _braced_block(dispatch, matched.end(), literal)
            if literal == COMMAND:
                self.assertEqual(
                    dispatch[inner_start:inner_end].strip(),
                    f"postNow = {DISPATCHER}(command, result);",
                )
            pos = inner_end + 1
            self.assertGreater(pos, matched.end())
        self.assertIn(COMMAND, literals)
        self.assertNotIn("job", self.code.lower())
        self.assertNotIn("CallLater", self.code)
        self.assertNotIn("AddJob", self.code)
        returns = re.findall(r"\breturn\b([^;]*);", self.bare)
        self.assertGreater(len(returns), 0)
        self.assertEqual([value.strip() for value in returns], ["true"] * len(returns))
        stripped = self.bare.rstrip()
        self.assertTrue(stripped.endswith("return true;"))
        head_stmt = stripped[: -len("return true;")].rstrip()
        self.assertTrue(
            head_stmt == "" or head_stmt.endswith(";") or head_stmt.endswith("}"),
            head_stmt,
        )

    def test_e2_the_steps_run_in_d2_order(self) -> None:
        # E2. Each of health01, zone and slot is assigned only at its read,
        # is never followed by a member access or an index, and sits inside
        # no call but GetMaxHealth, GetHealth01, GetHealthLevel, SetHealth01
        # and GetSlotIdFromString, each on its receiver (target, or
        # InventorySlots for GetSlotIdFromString). An if condition and plain
        # parentheses are not calls; any other name before a parenthesis is,
        # while and switch included, and so is a call around an allowed one.
        _in_order(
            self,
            self.code,
            (
                "!IsFiniteFloat(command.args.health01)",
                "health01 = command.args.health01;",
                "zone = command.args.zone;",
                "slot = command.args.slot;",
                "ResolveCommandObject(command.args, error)",
                "slotId = InventorySlots.GetSlotIdFromString(slot);",
                'result.error = "slot_empty"',
                'result.error = "target_is_player"',
                'GetMaxHealth(zone, "Health")',
                "report.health01_before = target.GetHealth01(zone, \"Health\")",
                "report.health_level_before = target.GetHealthLevel(zone)",
                'SetHealth01(zone, "Health", health01)',
                'result.error = "target_gone"',
                "report.health01_after = target.GetHealth01(zone, \"Health\")",
                "report.health_level_after = target.GetHealthLevel(zone)",
                'result.error = "health_not_applied"',
            ),
        )
        _health_locals_unmutated(self, self.bare)
        self.assertIn('result.error = "slot_not_found"', self.code)
        self.assertLess(
            self.code.index("ResolveCommandObject(command.args, error)"),
            self.code.index('result.error = "slot_not_found"'),
        )
        self.assertLess(
            self.code.index('result.error = "slot_not_found"'),
            self.code.index('result.error = "target_is_player"'),
        )
        self.assertLess(
            self.code.index("health01_before"),
            self.code.index('SetHealth01(zone, "Health", health01)'),
        )
        self.assertLess(
            self.code.index('SetHealth01(zone, "Health", health01)'),
            self.code.index("health01_after"),
        )
        self.assertLess(
            self.code.index('GetMaxHealth(zone, "Health")'),
            self.code.index('SetHealth01(zone, "Health", health01)'),
        )

    def test_e3_one_sethealth01_with_the_health_literal(self) -> None:
        # E3
        self.assertEqual(self.code.count("SetHealth01("), 1)
        self.assertIn('SetHealth01(zone, "Health", health01)', self.code)
        self.assertEqual(re.findall(r"\bSetHealth\(", self.code), [])
        self.assertNotIn("ObjectDelete", self.code)
        self.assertNotIn('"Blood"', self.code)

    def test_e4_message_classes_hold_d3_primitives(self) -> None:
        # E4
        self.assertEqual(
            _class_members(self.messages, "MCPObjectHealth"),
            [
                ("string", "type"),
                ("string", "zone"),
                ("string", "slot"),
                ("float", "health01_requested"),
                ("float", "health01_before"),
                ("float", "health01_after"),
                ("int", "health_level_before"),
                ("int", "health_level_after"),
            ],
        )
        for member_type, _name in _class_members(self.messages, "MCPObjectHealth"):
            self.assertFalse(member_type.startswith("ref"), member_type)
        args = _class_members(self.messages, "MCPArgs")
        self.assertIn(("float", "health01"), args)
        self.assertIn(("string", "zone"), args)
        result = _class_members(self.messages, "MCPResult")
        self.assertEqual(result.count(("ref MCPObjectHealth", "object_health")), 1)

    def test_e5_locals_are_declared_on_top(self) -> None:
        # E5
        names = _locals_on_top(self.code)
        self.assertIn("error", names)
        self.assertIn("target", names)
        self.assertIn("report", names)
        self.assertGreater(len(names), 0)


DISPATCH_SIGNATURE = (
    f"protected bool {DISPATCHER}(MCPCommand command, MCPResult result)"
)
POPULATE_SIGNATURE = (
    "protected void PopulateTelemetryObject(Object found, MCPTelemetry telemetry)"
)


class _HealthTarget:
    """A world object as object_set_health and telemetry_read read it.

    zones=None is an object with no damage system (StaticObj_Misc_SupplyBox1_DE
    in the 9/11 round). A health call on it, or on a named zone it does not
    list, fails the test the way GetMaxHealth threw a Virtual Machine
    Exception in that round. initialized is what HasDamageSystem answers:
    false by default, as it read on the 1.30 server in the 10/11 round for
    items whose health the engine reads and writes.
    """

    def __init__(
        self,
        zones: list[str] | None,
        health01: float = 1.0,
        initialized: bool = False,
        global_health: bool = True,
        entity_ready: bool | None = None,
        kind: str = "vehicle",
    ) -> None:
        self.zones = zones
        self.health01 = health01
        self.initialized = initialized
        # S1/C2 (#363 follow-up): whether the config class has a
        # DamageSystem GlobalHealth class, and, for an EntityAI, what
        # IsInitialized answers (None: not an EntityAI, a map object).
        self.global_health = global_health
        self.entity_ready = entity_ready
        self.kind = kind
        self.calls: list[str] = []
        self.config_paths: list[str] = []

    def _health_call(self, name: str, zone: EnforceString) -> None:
        self.calls.append(f"{name}({zone.text})")
        if self.zones is None:
            raise AssertionError(f"{name} on an object with no damage system")
        if zone.text and zone.text not in self.zones:
            raise AssertionError(f"{name} on zone {zone.text!r} the object does not have")

    def IsMan(self) -> bool:
        return False

    def IsWeapon(self) -> bool:
        return self.kind == "weapon"

    def IsMagazine(self) -> bool:
        return self.kind == "magazine"

    def IsInitialized(self) -> bool:
        self.calls.append("IsInitialized")
        return bool(self.entity_ready)

    def GetType(self) -> EnforceString:
        return EnforceString.of("Barrel_Blue")

    def ClassName(self) -> EnforceString:
        return EnforceString.of("Barrel_Blue")

    def GetPosition(self) -> tuple[float, float, float]:
        return (0.0, 0.0, 0.0)

    def GetOrientation(self) -> tuple[float, float, float]:
        return (0.0, 0.0, 0.0)

    def GetDirection(self) -> tuple[float, float, float]:
        return (0.0, 0.0, 1.0)

    def ConfigIsExisting(self, entry: EnforceString) -> bool:
        self.calls.append(f"ConfigIsExisting({entry.text})")
        return entry.text == "DamageSystem" and self.zones is not None

    def HasDamageSystem(self) -> bool:
        self.calls.append("HasDamageSystem")
        return self.initialized

    def GetDamageZones(self, names: EnforceArray) -> None:
        self.calls.append("GetDamageZones")
        if self.zones is None:
            raise AssertionError("GetDamageZones on an object with no damage system")
        for zone in self.zones:
            names.Insert(EnforceString.of(zone))

    def GetMaxHealth(self, zone: EnforceString, _type: EnforceString) -> float:
        self._health_call("GetMaxHealth", zone)
        return 100.0

    def GetHealth01(self, zone: EnforceString, _type: EnforceString) -> float:
        self._health_call("GetHealth01", zone)
        return self.health01

    def GetHealthLevel(self, zone: EnforceString) -> int:
        self._health_call("GetHealthLevel", zone)
        return 0 if self.health01 > 0.7 else 4

    def SetHealth01(
        self, zone: EnforceString, _type: EnforceString, value: float
    ) -> None:
        self._health_call("SetHealth01", zone)
        self.health01 = value


HEALTH_READABLE_SIGNATURE = "protected bool HealthReadable(Object target)"
CFG_ROOTS = {"vehicle": "CfgVehicles", "weapon": "CfgWeapons", "magazine": "CfgMagazines"}


def _health_readable_into(source: str, namespace: dict[str, object]) -> None:
    """Translate the bridge's HealthReadable (#363 follow-up, C2/S1) into its
    own namespace, with GetGame().ConfigIsExisting answering from the target
    the test runs, and put it in `namespace` for the handler that calls it.
    Before the fix the bridge has no HealthReadable and nothing is added."""
    if HEALTH_READABLE_SIGNATURE not in source:
        return
    current: dict[str, _HealthTarget] = {}

    def config_is_existing(path: EnforceString) -> bool:
        target = current["target"]
        target.config_paths.append(path.text)
        expected = f"{CFG_ROOTS[target.kind]} {target.GetType().text} DamageSystem GlobalHealth"
        return target.zones is not None and target.global_health and path.text == expected

    helper: dict[str, object] = {
        # Enforce strings with +, for the config path the guard builds.
        "_S": _LogString,
        "GetGame": lambda: SimpleNamespace(ConfigIsExisting=config_is_existing),
        "CFG_VEHICLESPATH": _LogString.of("CfgVehicles"),
        "CFG_WEAPONSPATH": _LogString.of("CfgWeapons"),
        "CFG_MAGAZINESPATH": _LogString.of("CfgMagazines"),
        "EntityAI": SimpleNamespace(
            Cast=lambda value: value if getattr(value, "entity_ready", None) is not None else None
        ),
    }
    readable = translate(source, HEALTH_READABLE_SIGNATURE, helper, class_types={"EntityAI", "string"})

    def bound(target: _HealthTarget) -> bool:
        current["target"] = target
        return readable(target)

    namespace["HealthReadable"] = bound


class ObjectSetHealthDamageSystemGuardTest(unittest.TestCase):
    """c703 after the 9/11 round: the real DispatchObjectSetHealth body runs on
    fakes, so these fail if either guard before GetMaxHealth is dropped."""

    def setUp(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        self.namespace: dict[str, object] = {
            "IsFiniteFloat": math.isfinite,
            "RuntimeObjectId": lambda _target: 7,
            "TStringArray": EnforceArray,
        }
        _health_readable_into(source, self.namespace)
        fake_class(messages, "MCPObjectHealth", self.namespace)
        self.result_class = fake_class(messages, "MCPResult", self.namespace)
        self.dispatch = translate(
            source,
            DISPATCH_SIGNATURE,
            self.namespace,
            class_types={"Object", "EntityAI", "MCPObjectHealth", "TStringArray"},
        )

    def _run(self, target: _HealthTarget, zone: str) -> object:
        self.namespace["ResolveCommandObject"] = lambda _args, _error: target
        command = SimpleNamespace(
            args=SimpleNamespace(
                health01=0.5,
                zone=EnforceString.of(zone),
                slot=EnforceString.of(""),
            )
        )
        result = self.result_class()
        self.assertIs(self.dispatch(command, result), True)
        return result

    def _assert_no_health(self, result: object) -> None:
        self.assertIs(result.ok, False)
        self.assertEqual(result.error, EnforceString.of("no_health"))
        self.assertEqual(result.type, EnforceString.of("Barrel_Blue"))
        self.assertIsNone(result.object_health)

    def test_g1_no_damage_system_is_no_health_before_any_health_call(self) -> None:
        for zone in ("", "Engine"):
            with self.subTest(zone=zone):
                target = _HealthTarget(zones=None)
                result = self._run(target, zone)
                self._assert_no_health(result)
                self.assertEqual(target.calls, ["ConfigIsExisting(DamageSystem)"])

    def test_g2_a_zone_the_object_does_not_list_is_no_health(self) -> None:
        target = _HealthTarget(zones=["Engine", "Body"])
        result = self._run(target, "Bogus")
        self._assert_no_health(result)
        self.assertEqual(target.calls, ["ConfigIsExisting(DamageSystem)", "GetDamageZones"])
        self.assertEqual(target.health01, 1.0)

    def test_g3_a_listed_zone_is_set_and_read_back(self) -> None:
        target = _HealthTarget(zones=["Body", "Engine"])
        result = self._run(target, "Engine")
        self.assertIs(result.ok, True)
        self.assertEqual(result.error, EnforceString.of(""))
        self.assertEqual(
            target.calls[:3],
            ["ConfigIsExisting(DamageSystem)", "GetDamageZones", "GetMaxHealth(Engine)"],
        )
        self.assertIn("SetHealth01(Engine)", target.calls)
        self.assertEqual(result.object_health.health01_after, 0.5)

    def test_g4_the_empty_zone_is_the_global_one_and_skips_the_lookup(self) -> None:
        target = _HealthTarget(zones=["Engine"])
        result = self._run(target, "")
        self.assertIs(result.ok, True)
        self.assertNotIn("GetDamageZones", target.calls)
        self.assertEqual(target.calls[:2], ["ConfigIsExisting(DamageSystem)", "GetMaxHealth()"])
        self.assertEqual(result.object_health.health01_after, 0.5)

    def test_g5_an_item_whose_damage_system_reads_uninitialized_is_still_set(self) -> None:
        # The 10/11 round: Barrel_Blue to 0.5 answered no_health because
        # HasDamageSystem read false, though GetMaxHealth reads its health.
        target = _HealthTarget(zones=[], initialized=False)
        result = self._run(target, "")
        self.assertIs(result.ok, True)
        self.assertNotIn("HasDamageSystem", target.calls)
        self.assertIn("SetHealth01()", target.calls)
        self.assertEqual(result.object_health.health01_after, 0.5)


    def test_g6_a_damage_system_without_global_health_is_no_health_before_zones(self) -> None:
        # C2 (#363 follow-up): a DamageSystem class with no GlobalHealth
        # class has no global health data for GetMaxHealth to read; it
        # answers no_health before GetDamageZones or any health call.
        for zone in ("", "Engine"):
            with self.subTest(zone=zone):
                target = _HealthTarget(zones=["Engine"], global_health=False)
                target._health_call = _refuse_health  # type: ignore[method-assign]
                target.GetDamageZones = _refuse_zones  # type: ignore[method-assign]
                result = self._run(target, zone)
                self._assert_no_health(result)
                self.assertEqual(
                    target.config_paths, ["CfgVehicles Barrel_Blue DamageSystem GlobalHealth"]
                )

    def test_g7_an_entity_not_yet_initialized_is_no_health_before_any_health_call(self) -> None:
        # S1 (#363 follow-up): a freshly spawned EntityAI before its
        # DeferredInit (vanilla entityai.c:240, :277-285) answers no_health
        # before GetDamageZones or GetMaxHealth.
        for zone in ("", "Engine"):
            with self.subTest(zone=zone):
                target = _HealthTarget(zones=["Engine"], entity_ready=False)
                target._health_call = _refuse_health  # type: ignore[method-assign]
                target.GetDamageZones = _refuse_zones  # type: ignore[method-assign]
                result = self._run(target, zone)
                self._assert_no_health(result)
                self.assertIn("IsInitialized", target.calls)

    def test_g8_an_initialized_entity_weapon_or_magazine_reads_its_own_config_root(self) -> None:
        for kind in ("vehicle", "weapon", "magazine"):
            with self.subTest(kind=kind):
                target = _HealthTarget(zones=["Engine"], entity_ready=True, kind=kind)
                result = self._run(target, "Engine")
                self.assertIs(result.ok, True)
                self.assertEqual(
                    target.config_paths, [f"{CFG_ROOTS[kind]} Barrel_Blue DamageSystem GlobalHealth"]
                )
                self.assertEqual(result.object_health.health01_after, 0.5)

    def test_g9_both_consumers_call_the_one_guard(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        self.assertIn(HEALTH_READABLE_SIGNATURE, source)
        dispatch = _method_views(source, DISPATCHER)[0]
        populate = _without_comments(source[source.index(POPULATE_SIGNATURE):])
        populate = populate[: populate.index("EntityAI entity = EntityAI.Cast(found);")]
        self.assertIn("if (!HealthReadable(target))", dispatch)
        self.assertNotIn('target.ConfigIsExisting("DamageSystem")', dispatch)
        self.assertIn("if (HealthReadable(found))", populate)
        self.assertNotIn('found.ConfigIsExisting("DamageSystem")', populate)
        self.assertLess(dispatch.index("HealthReadable(target)"), dispatch.index("GetDamageZones("))


def _refuse_health(name: str, zone: EnforceString) -> None:
    raise AssertionError(f"{name}({zone.text}) reached past the guard")


def _refuse_zones(_names: EnforceArray) -> None:
    raise AssertionError("GetDamageZones reached past the guard")


class TelemetryHealthGuardTest(unittest.TestCase):
    """telemetry_read object_at reads GetHealth01 only on an object with a
    damage system (c703); without one, health01 keeps its default 0."""

    def setUp(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        no_cast = SimpleNamespace(Cast=lambda _value: None)
        self.namespace: dict[str, object] = {
            "EntityAI": no_cast,
            "Car": no_cast,
            "GetVelocity": lambda _found: (0.0, 0.0, 0.0),
            "VectorToArray": lambda _vec, _arr: None,
        }
        _health_readable_into(source, self.namespace)
        self.populate = translate(
            source,
            POPULATE_SIGNATURE,
            self.namespace,
            class_types={"EntityAI", "Car", "string"},
        )

    def _run(self, found: _HealthTarget) -> SimpleNamespace:
        telemetry = SimpleNamespace(
            found=False,
            type=EnforceString.of(""),
            class_name=EnforceString.of(""),
            pos=[],
            orientation=[],
            direction=[],
            velocity=[],
            health01=0.0,
        )
        self.populate(found, telemetry)
        return telemetry

    def test_t1_no_damage_system_skips_the_read_and_keeps_zero(self) -> None:
        found = _HealthTarget(zones=None)
        telemetry = self._run(found)
        self.assertIs(telemetry.found, True)
        self.assertEqual(telemetry.health01, 0.0)
        self.assertEqual(found.calls, ["ConfigIsExisting(DamageSystem)"])

    def test_t2_a_damage_system_reads_the_global_health(self) -> None:
        found = _HealthTarget(zones=[], health01=0.25, initialized=False)
        telemetry = self._run(found)
        self.assertEqual(telemetry.health01, 0.25)
        self.assertEqual(found.calls, ["ConfigIsExisting(DamageSystem)", "GetHealth01()"])


    def test_t3_no_global_health_or_an_entity_not_initialized_skips_the_read(self) -> None:
        for found in (
            _HealthTarget(zones=[], health01=0.25, global_health=False),
            _HealthTarget(zones=[], health01=0.25, entity_ready=False),
        ):
            with self.subTest(global_health=found.global_health, entity_ready=found.entity_ready):
                found._health_call = _refuse_health  # type: ignore[method-assign]
                telemetry = self._run(found)
                self.assertEqual(telemetry.health01, 0.0)
                self.assertNotIn("GetHealth01()", found.calls)


class ObjectSetHealthDescriptionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def test_the_description_is_the_d1_text(self) -> None:
        tool = self.app._tool_manager.get_tool(COMMAND)
        self.assertEqual(tool.description, f"{LEASE_TOOL_LINE} {D1_DESCRIPTION}")
