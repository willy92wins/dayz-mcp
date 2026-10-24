"""loadout_get and loadout_apply: one loadout format for a read and a write.

fb-20261023-235342-fb99 (loadout_get) and fb-20261023-235342-220f
(loadout_apply). The tool checks the whole loadout and flattens it into rows
(dayz_mcp/loadout.py); the bridge creates the rows in order, reads the target
back into the same rows, and the tool nests them into the tree again.

These tests pin the Python contract, the ingress and lease split, the
older-PBO admission, the reply shapes and the Enforce source the game will
compile. They do not launch DayZ; the in-game criteria are listed in the PR.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dayz_mcp import loadout, loopback, result_prune, server
from dayz_mcp.server import (
    DEFAULT_TOOL_TIMEOUT_S,
    EXPECTED_BRIDGE_VERSION,
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
from tests._tiers import slow_test
from tests.fence_helpers import (
    INST_SERVER,
    PID_SERVER,
    bind_both_peers,
    bound_queue,
)
from tests.mcp_helpers import FakePeer, _content_json


GET = "loadout_get"
APPLY = "loadout_apply"
SCRIPTS = addon_root() / "scripts"
BRIDGE_PATH = SCRIPTS / "5_Mission" / "MCPBridge.c"
MESSAGES_PATH = SCRIPTS / "5_Mission" / "MCPMessages.c"
CENSUS_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "bridge_capabilities_v1.json"
)
# The server arg contract before these verbs: an older PBO announces it.
_PRE_LOADOUT_ARG_CONTRACT_HASH = "4f90284258023d98"
IDENTITY = {
    "platform": "codex",
    "pid": 11,
    "ppid": 1,
    "started_at_utc": "2026-07-15T00:00:00Z",
    "session_id": "A",
    "task_label": "wardrobe",
}

# Eight items: four worn, two in a backpack's cargo, a weapon in hands with
# its magazine.
PRESET_8: dict[str, Any] = {
    "attachments": [
        {"type": "TShirt_White", "slot": "Body"},
        {"type": "Jeans_Blue", "slot": "Legs"},
        {"type": "AthleticShoes_Black", "slot": "Feet"},
        {
            "type": "TaloonBag_Blue",
            "slot": "Back",
            "cargo": [{"type": "Apple"}, {"type": "BandageDressing"}],
        },
    ],
    "hands": {
        "type": "M4A1",
        "attachments": [{"type": "Mag_STANAG_30Rnd", "slot": "magazine"}],
    },
}
PRESET_8_PATHS = [
    "loadout.attachments[0]",
    "loadout.attachments[1]",
    "loadout.attachments[2]",
    "loadout.attachments[3]",
    "loadout.attachments[3].cargo[0]",
    "loadout.attachments[3].cargo[1]",
    "loadout.hands",
    "loadout.hands.attachments[0]",
]


def _rows(tree: object) -> list[dict[str, Any]]:
    return [row for _path, row in loadout.flatten_request(tree)]


def _classes(tree: dict[str, Any]) -> Counter[str]:
    """The multiset of classes a loadout tree holds."""
    found: Counter[str] = Counter()

    def walk(item: dict[str, Any]) -> None:
        found[item["type"]] += 1
        for child in item.get("attachments", []):
            walk(child)
        for child in item.get("cargo", []):
            walk(child)

    for item in tree.get("attachments", []):
        walk(item)
    if tree.get("hands") is not None:
        walk(tree["hands"])
    return found


def _chain(levels: int) -> dict[str, Any]:
    """A loadout whose deepest item is `levels` levels below the target.

    A worn vest holds a bag in its cargo, which holds a bag, and so on down
    to a rag.
    """
    if levels == 1:
        return {"attachments": [{"type": "Rag", "slot": "Body"}]}
    item: dict[str, Any] = {"type": "Rag"}
    for _ in range(levels - 2):
        item = {"type": "SmershBag", "cargo": [item]}
    return {"attachments": [{"type": "SmershVest", "slot": "Vest", "cargo": [item]}]}


def _flat_answer(
    rows: list[dict[str, Any]], applied: list[dict[str, Any]] | None = None, **extra: Any
) -> dict[str, Any]:
    wire: dict[str, Any] = {
        "items": [dict(row) for row in rows],
        "applied": list(applied or []),
        "too_large": 0,
    }
    wire.update(extra)
    return {"ok": 1, "type": "SurvivorM_Mirek", "loadout": wire}


_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')
_MEMBER_RE = re.compile(
    r"(?m)(?<![A-Za-z0-9_>])(?P<type>(?:ref\s+)?[A-Za-z_][A-Za-z0-9_]*(?:\s*<[^;{}]+>)?)"
    r"\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*;"
)
_LOCAL_DECLARATION = re.compile(
    r"^\s*(?:string|int|bool|Object|EntityAI|PlayerBase|GameInventory|CargoBase|"
    r"InventoryLocation|MCPJob|MCPResult|MCPLoadout|MCPLoadoutItem|MCPLoadoutApplied|"
    r"array<EntityAI>|array<int>)\s+(\w+)\s*(?:=[^;]*)?;\s*$"
)
_SERVER_CAPS = re.compile(
    r'protected const string SERVER_CAPABILITIES = ((?:"[^"\n]*"(?: \+ )?)+);'
)
_ENFORCE_FUNCTIONS = (
    "protected bool DispatchLoadoutGet(MCPCommand command, MCPResult result)",
    "protected bool DispatchLoadoutApply(MCPCommand command, MCPResult result)",
    "protected PlayerBase ResolveLoadoutTarget(MCPArgs args, out string error)",
    "protected string LoadoutRowsError(array<ref MCPLoadoutItem> rows)",
    "protected void ClearLoadout(PlayerBase target, array<EntityAI> cleared)",
    "protected void ApplyLoadout(PlayerBase target, MCPArgs args, MCPResult result)",
    "protected EntityAI CreateLoadoutItem(PlayerBase target, EntityAI owner, "
    "MCPLoadoutItem row, out string error)",
    "protected void ReadLoadout(PlayerBase target, MCPLoadout loadoutRead)",
    "protected void AppendLoadoutRow(MCPLoadout loadoutRead, EntityAI item, "
    "string dest, int parent, int level)",
    "protected string LoadoutSlotName(EntityAI item)",
    "protected bool IsLoadoutClearDone(MCPJob job)",
    "protected void PostLoadoutJob(MCPJob job)",
)


def _block_after(source: str, start: int) -> str:
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated block at offset {start}")


def _method_body(source: str, signature: str) -> str:
    if source.count(signature) != 1:
        raise AssertionError(f"{signature!r} occurs {source.count(signature)} times")
    return _block_after(source, source.index(signature))


def _if_body(source: str, condition: str) -> str:
    needle = f"if ({condition})"
    if source.count(needle) != 1:
        raise AssertionError(f"{needle!r} occurs {source.count(needle)} times")
    return _block_after(source, source.index(needle))


def _without_comments(source: str) -> str:
    def replace(match: re.Match[str]) -> str:
        text = match.group(0)
        return text if text.startswith('"') else re.sub(r"[^\n]", " ", text)

    return _COMMENT_OR_STRING.sub(replace, source)


def _class_members(source: str, class_name: str) -> list[tuple[str, str]]:
    clean = _without_comments(source)
    match = re.search(rf"\bclass\s+{re.escape(class_name)}\b", clean)
    if match is None:
        raise AssertionError(f"class {class_name} is absent")
    body = _block_after(clean, match.end())
    return [(m.group("type"), m.group("name")) for m in _MEMBER_RE.finditer(body)]


def _locals_declared_at_top(body: str) -> list[str]:
    """Local names, failing when one follows a statement or is declared twice."""
    names: list[str] = []
    statement_seen = False
    for raw in _without_comments(body).splitlines():
        line = raw.strip()
        if not line or line in ("{", "}"):
            continue
        match = _LOCAL_DECLARATION.match(raw)
        if match is None:
            statement_seen = True
            continue
        if statement_seen:
            raise AssertionError(f"declaration after a statement: {line}")
        if match.group(1) in names:
            raise AssertionError(f"local declared twice: {match.group(1)}")
        names.append(match.group(1))
    return names


def _errors(code: str) -> set[str]:
    """The error literals a body assigns (error, itemError, result.error, ...)."""
    return set(re.findall(r'[eE]rror = "(\w+)"', code))


def _server_capabilities(source: str) -> list[str]:
    declaration = _SERVER_CAPS.search(source)
    if declaration is None:
        raise AssertionError("SERVER_CAPABILITIES declaration not found")
    return "".join(re.findall(r'"([^"\n]*)"', declaration.group(1))).split(",")


def _server_commands() -> list[str]:
    return sorted(_BRIDGE_COMMAND_TOOLS["server"])


def _announce_server(
    state: loopback.ServerState, commands: list[str], ach: str | None
) -> None:
    state.daemon_generation = "gen-loadout"
    state._peer_caps["server"] = {
        "generation": state.daemon_generation,
        "commands": list(commands),
        "reason": "ok",
        "arg_contract_hash": ach,
        "announced_at": state._now(),
        "instance": INST_SERVER,
    }


_WIRE_ARGS = {
    GET: {"uid": ""},
    APPLY: {
        "clear_first": False,
        "loadout_items": [{"type": "TShirt_White", "slot": "Body", "dest": "attachment", "parent": -1}],
    },
}


class LoadoutIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[dict[str, object]] = []
        self.state = loopback.ServerState("test-key")
        bind_both_peers(self.state)
        # Both verbs are admitted only against a census that names them.
        _announce_server(self.state, _server_commands(), server.EXPECTED_SERVER_ARG_CONTRACT_HASH)

    def _coordinate(self) -> None:
        self.state.coordination = SessionCoordinator(
            token_fn=lambda: "token",
            id_fn=lambda: "lease",
            audit=lambda event: self.events.append(event) or True,
            cleanup=lambda session, lease, reason, active: self.state.cleanup_owner(
                session, lease, reason, active
            ),
        )

    def test_both_are_server_verbs_and_only_the_read_skips_the_lease(self) -> None:
        for command in (GET, APPLY):
            with self.subTest(command=command):
                self.assertIn(command, loopback.SERVER_COMMANDS)
                self.assertNotIn(command, loopback.CLIENT_COMMANDS)
                self.assertEqual(loopback.peer_for_command(command), "server")
                self.assertIn(command, loopback._CAPABILITY_ADMISSION_COMMANDS)
                self.assertEqual(
                    self.state.enqueue_command(command, dict(_WIRE_ARGS[command]), peer="client"),
                    (400, {"error": "bad_peer"}),
                )
        self.assertIn(GET, READ_ONLY_COMMANDS)
        self.assertFalse(command_requires_lease(GET))
        self.assertNotIn(APPLY, READ_ONLY_COMMANDS)
        self.assertTrue(command_requires_lease(APPLY))

    def test_a_session_without_a_lease_reads_and_cannot_apply(self) -> None:
        self._coordinate()
        status, body = self.state.enqueue_command(
            GET, {"object_id": 3}, "server", identity_payload=IDENTITY
        )
        self.assertEqual((status, body["peer"], body["cmd"]), (200, "server", GET))
        refused = self.state.enqueue_command(
            APPLY, dict(_WIRE_ARGS[APPLY]), "server", identity_payload=IDENTITY
        )
        self.assertEqual(refused, (423, {"error": "lease_required"}))
        decisions = [event.get("decision") for event in self.events]
        self.assertIn("read", decisions)
        self.assertIn("lease_required", decisions)

    def test_the_rows_the_tool_builds_pass_the_ingress(self) -> None:
        for args in (
            {"clear_first": True, "loadout_items": _rows(PRESET_8)},
            {"object_id": 9, "clear_first": False, "loadout_items": _rows(PRESET_8)},
            {"uid": "76561198000000000", "clear_first": False, "loadout_items": []},
        ):
            with self.subTest(args=args):
                self.assertEqual(loopback.validate_command_args(APPLY, args), (True, None))
                status, body = self.state.enqueue_command(APPLY, args)
                self.assertEqual(status, 200, body)

    def test_the_ingress_refuses_what_the_bridge_would_refuse(self) -> None:
        good = _rows(PRESET_8)
        refused: list[tuple[str, dict[str, Any]]] = [
            (GET, {"object_id": 3, "uid": "x"}),
            (GET, {"object_id": 0}),
            (GET, {"loadout_items": []}),
            (APPLY, {"loadout_items": good}),
            (APPLY, {"clear_first": 1, "loadout_items": good}),
            (APPLY, {"clear_first": False, "loadout_items": good, "object_id": 2, "uid": "x"}),
            (APPLY, {"clear_first": False, "loadout_items": good * 9}),
            (
                APPLY,
                {
                    "clear_first": False,
                    "loadout_items": [{"type": "Apple", "slot": "", "dest": "cargo", "parent": -1}],
                },
            ),
        ]
        for command, args in refused:
            with self.subTest(command=command, args=args):
                self.assertEqual(
                    self.state.enqueue_command(command, args), (400, {"error": "bad_args"})
                )


class LoadoutAdmissionTest(unittest.TestCase):
    """An older PBO is never handed either verb, and no id is spent on it.

    Both fail on a tree that leaves the verbs out of _CAPABILITY_ADMISSION_COMMANDS
    or out of SERVER_ARG_CONTRACT: enqueue then answers 200 and allocates an id.
    """

    def _bound(self) -> loopback.ServerState:
        state = loopback.ServerState(key="k")
        state.install_bound_peer(
            instance=INST_SERVER, role="server", pid=PID_SERVER, run_id="loadout-run"
        )
        return state

    def test_a_census_without_the_verb_refuses_before_an_id(self) -> None:
        for command in (GET, APPLY):
            with self.subTest(command=command):
                state = self._bound()
                reduced = [name for name in _server_commands() if name != command]
                _announce_server(state, reduced, server.EXPECTED_SERVER_ARG_CONTRACT_HASH)
                before = state._next_id
                status, body = state.enqueue_command(
                    command, dict(_WIRE_ARGS[command]), peer="server"
                )
                self.assertEqual(status, 409)
                self.assertEqual(body["error"], "bridge_capability_missing")
                self.assertEqual(state._next_id, before)
                self.assertEqual(bound_queue(state, "server"), [])

    def test_the_pre_loadout_contract_hash_refuses_before_an_id(self) -> None:
        for command in (GET, APPLY):
            with self.subTest(command=command):
                state = self._bound()
                _announce_server(state, _server_commands(), _PRE_LOADOUT_ARG_CONTRACT_HASH)
                before = state._next_id
                status, body = state.enqueue_command(
                    command, dict(_WIRE_ARGS[command]), peer="server"
                )
                self.assertEqual(status, 409)
                self.assertEqual(body["error"], "arg_contract_mismatch")
                self.assertEqual(state._next_id, before)
                self.assertEqual(bound_queue(state, "server"), [])

    def test_the_current_census_admits_both(self) -> None:
        for command in (GET, APPLY):
            with self.subTest(command=command):
                state = self._bound()
                _announce_server(state, _server_commands(), server.EXPECTED_SERVER_ARG_CONTRACT_HASH)
                status, body = state.enqueue_command(
                    command, dict(_WIRE_ARGS[command]), peer="server"
                )
                self.assertEqual(status, 200, body)

    def test_the_contract_names_every_key_each_verb_sends(self) -> None:
        self.assertEqual(server.SERVER_ARG_CONTRACT[GET], ("object_id", "uid"))
        self.assertEqual(
            server.SERVER_ARG_CONTRACT[APPLY],
            ("clear_first", "loadout_items", "object_id", "uid"),
        )
        self.assertEqual(_BRIDGE_COMMAND_TOOLS["server"][GET], GET)
        self.assertEqual(_BRIDGE_COMMAND_TOOLS["server"][APPLY], APPLY)
        fixture = json.loads(CENSUS_FIXTURE.read_text(encoding="utf-8"))
        for command in (GET, APPLY):
            with self.subTest(command=command):
                self.assertEqual(fixture["peers"]["server"].get(command), command)
                self.assertNotIn(command, fixture["peers"]["client"])


class LoadoutTargetTest(unittest.TestCase):
    def test_the_target_is_a_uid_an_object_id_or_neither(self) -> None:
        self.assertEqual(loadout.target_args(0, ""), {})
        self.assertEqual(loadout.target_args(12, ""), {"object_id": 12})
        self.assertEqual(loadout.target_args(0, "7656"), {"uid": "7656"})

    def test_both_or_a_bad_value_is_bad_args(self) -> None:
        for object_id, uid in ((4, "7656"), (-1, ""), (True, ""), (1.0, ""), ("4", ""), (0, 7)):
            with self.subTest(object_id=object_id, uid=uid):
                with self.assertRaises(ValueError) as raised:
                    loadout.target_args(object_id, uid)
                self.assertTrue(str(raised.exception).startswith("bad_args: "), raised.exception)
        with self.assertRaises(ValueError) as both:
            loadout.target_args(4, "7656")
        self.assertIn("must not both be given", str(both.exception))


class LoadoutFlattenTest(unittest.TestCase):
    def test_rows_come_in_apply_order_with_their_parents(self) -> None:
        flat = loadout.flatten_request(PRESET_8)
        self.assertEqual([path for path, _row in flat], PRESET_8_PATHS)
        self.assertEqual(
            [row for _path, row in flat],
            [
                {"type": "TShirt_White", "slot": "Body", "dest": "attachment", "parent": -1},
                {"type": "Jeans_Blue", "slot": "Legs", "dest": "attachment", "parent": -1},
                {"type": "AthleticShoes_Black", "slot": "Feet", "dest": "attachment", "parent": -1},
                {"type": "TaloonBag_Blue", "slot": "Back", "dest": "attachment", "parent": -1},
                {"type": "Apple", "slot": "", "dest": "cargo", "parent": 3},
                {"type": "BandageDressing", "slot": "", "dest": "cargo", "parent": 3},
                {"type": "M4A1", "slot": "", "dest": "hands", "parent": -1},
                {"type": "Mag_STANAG_30Rnd", "slot": "magazine", "dest": "attachment", "parent": 6},
            ],
        )

    def test_an_item_s_attachments_come_before_its_cargo(self) -> None:
        tree = {
            "attachments": [
                {
                    "type": "PlateCarrierVest",
                    "slot": "Vest",
                    "cargo": [{"type": "Apple"}],
                    "attachments": [
                        {"type": "PlateCarrierHolster", "slot": "VestHolster",
                         "cargo": [{"type": "Rag"}]},
                    ],
                }
            ]
        }
        self.assertEqual(
            [(path, row["parent"]) for path, row in loadout.flatten_request(tree)],
            [
                ("loadout.attachments[0]", -1),
                ("loadout.attachments[0].attachments[0]", 0),
                ("loadout.attachments[0].attachments[0].cargo[0]", 1),
                ("loadout.attachments[0].cargo[0]", 0),
            ],
        )

    def test_empty_loadouts_have_no_rows(self) -> None:
        for tree in ({}, {"attachments": []}, {"hands": None}, {"attachments": [], "hands": None}):
            with self.subTest(tree=tree):
                self.assertEqual(loadout.flatten_request(tree), [])

    def test_bad_args_names_the_first_bad_path(self) -> None:
        cases: list[tuple[object, str]] = [
            (
                {"attachments": [
                    {"type": "TShirt_White", "slot": "Body"},
                    {"type": "Jeans_Blue", "slot": "Legs"},
                    {"type": "AthleticShoes_Black"},
                ]},
                "loadout.attachments[2].slot",
            ),
            (
                {"attachments": [{"type": "TaloonBag_Blue", "slot": "Back",
                                  "cargo": [{"type": "Apple"}, {"type": "Rag", "slot": "Back"}]}]},
                "loadout.attachments[0].cargo[1].slot",
            ),
            ({"hands": {"type": "M4A1", "slot": "Hands"}}, "loadout.hands.slot"),
            ({"attachments": [{"type": "", "slot": "Body"}]}, "loadout.attachments[0].type"),
            ({"attachments": [{"type": "TShirt White", "slot": "Body"}]}, "loadout.attachments[0].type"),
            ({"attachments": [{"type": 7, "slot": "Body"}]}, "loadout.attachments[0].type"),
            ({"attachments": [{"type": "TShirt_White", "slot": " "}]}, "loadout.attachments[0].slot"),
            ({"attachments": [{"type": "Apple", "slot": "Body", "quantity": 3}]},
             "loadout.attachments[0].quantity"),
            ({"attachments": [{"type": "Apple", "slot": "Body", "health": 1.0}]},
             "loadout.attachments[0].health"),
            ({"attachments": {"type": "Apple", "slot": "Body"}}, "loadout.attachments"),
            ({"attachments": [{"type": "TaloonBag_Blue", "slot": "Back", "cargo": "Apple"}]},
             "loadout.attachments[0].cargo"),
            ({"attachments": ["TShirt_White"]}, "loadout.attachments[0]"),
            ({"hands": "M4A1"}, "loadout.hands"),
            ({"cargo": []}, "loadout.cargo"),
            (["TShirt_White"], "loadout"),
        ]
        for tree, path in cases:
            with self.subTest(path=path, tree=tree):
                with self.assertRaises(ValueError) as raised:
                    loadout.flatten_request(tree)
                self.assertTrue(
                    str(raised.exception).startswith(f"bad_args: {path} "), raised.exception
                )

    def test_sixty_four_items_pass_and_the_sixty_fifth_is_bad_args(self) -> None:
        full = {"attachments": [{"type": "Rag", "slot": f"Slot{i}"} for i in range(64)]}
        self.assertEqual(len(loadout.flatten_request(full)), loadout.MAX_ITEMS)
        over = {"attachments": [{"type": "Rag", "slot": f"Slot{i}"} for i in range(64)],
                "hands": {"type": "Apple"}}
        with self.assertRaises(ValueError) as raised:
            loadout.flatten_request(over)
        self.assertEqual(
            str(raised.exception),
            "bad_args: loadout.hands is item 65; a loadout holds at most 64 items",
        )
        self.assertEqual(loadout.MAX_ITEMS, 64)

    def test_four_levels_pass_and_a_fifth_is_bad_args(self) -> None:
        self.assertEqual(loadout.MAX_DEPTH, 4)
        deep = _chain(4)
        rows = loadout.flatten_request(deep)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[-1][0], "loadout.attachments[0].cargo[0].cargo[0].cargo[0]")
        with self.assertRaises(ValueError) as raised:
            loadout.flatten_request(_chain(5))
        self.assertEqual(
            str(raised.exception),
            "bad_args: loadout.attachments[0].cargo[0].cargo[0].cargo[0].cargo[0] is 5 "
            "levels below the target; a loadout nests at most 4",
        )
        nested_hands = {"hands": {"type": "M4A1", "attachments": [
            {"type": "Mag_STANAG_30Rnd", "slot": "magazine", "cargo": [
                {"type": "Ammo_556x45", "cargo": [{"type": "Rag", "cargo": [{"type": "Apple"}]}]}
            ]}
        ]}}
        with self.assertRaises(ValueError) as hands_raised:
            loadout.flatten_request(nested_hands)
        self.assertIn("is 5 levels below the target", str(hands_raised.exception))


class LoadoutWireRowsTest(unittest.TestCase):
    def test_every_request_the_tool_can_build_is_a_valid_row_list(self) -> None:
        for tree in (PRESET_8, _chain(4), {}, {"hands": {"type": "Apple"}}):
            with self.subTest(tree=tree):
                self.assertTrue(loadout.is_wire_items(_rows(tree)))

    def test_rows_the_bridge_refuses_are_refused_here(self) -> None:
        body = {"type": "TShirt_White", "slot": "Body", "dest": "attachment", "parent": -1}
        cargo = {"type": "Apple", "slot": "", "dest": "cargo", "parent": 0}
        hands = {"type": "M4A1", "slot": "", "dest": "hands", "parent": -1}
        chain = [dict(body)] + [
            {"type": "Rag", "slot": "", "dest": "cargo", "parent": i} for i in range(4)
        ]
        cases = {
            "not_a_list": {"rows": body},
            "65_rows": [dict(body) for _ in range(65)],
            "missing_key": [{"type": "TShirt_White", "slot": "Body", "dest": "attachment"}],
            "extra_key": [dict(body, quantity=1)],
            "empty_type": [dict(body, type="")],
            "spaced_type": [dict(body, type="T Shirt")],
            "attachment_without_slot": [dict(body, slot="")],
            "forward_parent": [dict(body, parent=1), dict(body)],
            "own_parent": [dict(body, parent=0)],
            "parent_below_minus_one": [dict(body, parent=-2)],
            "bool_parent": [dict(body), dict(cargo, parent=True)],
            "cargo_on_the_target": [dict(cargo, parent=-1)],
            "cargo_with_slot": [dict(body), dict(cargo, slot="Body")],
            "two_hands": [dict(hands), dict(hands)],
            "hands_inside_an_item": [dict(body), dict(hands, parent=0)],
            "hands_with_slot": [dict(hands, slot="Hands")],
            "unknown_dest": [dict(body, dest="ground")],
            "fifth_level": chain,
        }
        for label, rows in cases.items():
            with self.subTest(label):
                self.assertFalse(loadout.is_wire_items(rows))
        self.assertTrue(loadout.is_wire_items(chain[:4]))


class LoadoutRoundTripTest(unittest.TestCase):
    """fb99 POS, offline half: what loadout_get answers is what loadout_apply takes."""

    def test_a_read_nests_into_a_tree_that_flattens_back_to_the_same_rows(self) -> None:
        rows = _rows(PRESET_8)
        tree = loadout.nest_rows(rows)
        self.assertIsNotNone(tree)
        assert tree is not None
        self.assertEqual(_rows(tree), rows)
        self.assertEqual(_classes(tree), _classes(PRESET_8))
        self.assertEqual(sum(_classes(tree).values()), 8)
        # The read is a complete apply input: every item lists what it holds,
        # and only an attachment carries a slot.
        self.assertEqual(tree["hands"]["attachments"][0]["slot"], "magazine")
        self.assertNotIn("slot", tree["hands"])
        self.assertNotIn("slot", tree["attachments"][3]["cargo"][0])
        self.assertEqual(tree["attachments"][0]["cargo"], [])
        self.assertEqual(tree["attachments"][0]["attachments"], [])

    def test_rows_that_form_no_tree_are_refused(self) -> None:
        body = {"type": "TShirt_White", "slot": "Body", "dest": "attachment", "parent": -1}
        for rows in (
            None,
            {"items": []},
            [None],
            [dict(body, parent=0)],
            [dict(body, dest="cargo")],
            [dict(body, dest="hands"), dict(body, dest="hands")],
            [dict(body), dict(body, dest="hands", parent=0)],
            [dict(body, type="")],
        ):
            with self.subTest(rows=rows):
                self.assertIsNone(loadout.nest_rows(rows))
        self.assertEqual(loadout.nest_rows([]), {"attachments": [], "hands": None})


class LoadoutReplyTest(unittest.TestCase):
    def test_get_answers_the_tree(self) -> None:
        answer = _flat_answer(_rows(PRESET_8))
        shaped = loadout.get_reply(answer)
        self.assertEqual(shaped["ok"], 1)
        self.assertEqual(shaped["type"], "SurvivorM_Mirek")
        self.assertEqual(_classes(shaped["loadout"]), _classes(PRESET_8))
        self.assertNotIn("warnings", shaped)
        # The answer the tool was handed is not rewritten.
        self.assertIn("items", answer["loadout"])
        empty = loadout.get_reply(_flat_answer([]))
        self.assertEqual(empty["loadout"], {"attachments": [], "hands": None})

    def test_an_answer_that_is_not_ok_passes_through(self) -> None:
        envelope = {
            "ok": False,
            "error": "game_not_ready:reason=no_run",
            "code": "not_ready",
            "reason": "no_run",
        }
        self.assertEqual(loadout.get_reply(dict(envelope)), envelope)
        self.assertEqual(loadout.apply_reply(_flatten(PRESET_8), dict(envelope)), envelope)

    def test_a_get_answer_that_cannot_be_read_is_not_ok(self) -> None:
        rows = _rows(PRESET_8)
        cases = {
            "member_absent": {"ok": 1},
            "member_not_an_object": {"ok": 1, "loadout": []},
            "too_large": _flat_answer([], too_large=1),
            "too_large_not_a_bool": _flat_answer(rows, too_large="no"),
            "items_absent": {"ok": 1, "loadout": {"too_large": 0}},
            "orphan_row": _flat_answer([dict(rows[4], parent=9)]),
        }
        for label, answer in cases.items():
            with self.subTest(label):
                shaped = loadout.get_reply(answer)
                self.assertEqual(shaped["ok"], 0)
                self.assertEqual(shaped["warnings"], [loadout.INCOMPLETE])

    def test_eight_items_created_answer_ok(self) -> None:
        flat = _flatten(PRESET_8)
        applied = [{"ok": 1, "error": ""} for _ in flat]
        shaped = loadout.apply_reply(flat, _flat_answer(_rows(PRESET_8), applied))
        self.assertEqual(shaped["status"], "ok")
        self.assertEqual(
            shaped["items"],
            [
                {"path": path, "type": row["type"], "ok": True, "error": None}
                for path, row in flat
            ],
        )
        self.assertEqual(_classes(shaped["loadout"]), _classes(PRESET_8))
        self.assertIs(shaped["loadout_too_large"], False)

    def test_one_unknown_class_among_eight_is_partial_and_names_the_class(self) -> None:
        tree = json.loads(json.dumps(PRESET_8))
        tree["attachments"][1]["type"] = "Jeans_NoSuchColour"
        flat = _flatten(tree)
        applied = [{"ok": 1, "error": ""} for _ in flat]
        applied[1] = {"ok": 0, "error": "unknown_type"}
        read = [row for index, row in enumerate(_rows(tree)) if index != 1]
        # Removing row 1 shifts the parents that point past it.
        for row in read:
            if row["parent"] > 1:
                row["parent"] -= 1
        shaped = loadout.apply_reply(flat, _flat_answer(read, applied))
        self.assertEqual(shaped["status"], "partial")
        self.assertEqual(sum(1 for item in shaped["items"] if item["ok"]), 7)
        failed = [item for item in shaped["items"] if not item["ok"]]
        self.assertEqual(
            failed,
            [{"path": "loadout.attachments[1]", "type": "Jeans_NoSuchColour",
              "ok": False, "error": "unknown_type"}],
        )
        self.assertEqual(sum(_classes(shaped["loadout"]).values()), 7)

    def test_a_second_item_for_one_slot_is_slot_occupied_and_reported(self) -> None:
        tree = {"attachments": [
            {"type": "TShirt_White", "slot": "Body"},
            {"type": "Hoodie_Blue", "slot": "Body"},
        ]}
        flat = _flatten(tree)
        applied = [{"ok": 1, "error": ""}, {"ok": 0, "error": "slot_occupied"}]
        shaped = loadout.apply_reply(flat, _flat_answer(_rows(tree)[:1], applied))
        self.assertEqual(shaped["status"], "partial")
        self.assertEqual(len(shaped["items"]), 2)
        self.assertEqual(
            shaped["items"][1],
            {"path": "loadout.attachments[1]", "type": "Hoodie_Blue",
             "ok": False, "error": "slot_occupied"},
        )
        self.assertEqual(
            shaped["loadout"]["attachments"],
            [{"type": "TShirt_White", "slot": "Body", "attachments": [], "cargo": []}],
        )

    def test_children_of_a_failed_item_are_parent_failed(self) -> None:
        flat = _flatten(PRESET_8)
        applied = [{"ok": 1, "error": ""} for _ in flat]
        applied[3] = {"ok": 0, "error": "slot_not_found"}
        applied[4] = {"ok": 0, "error": "parent_failed"}
        applied[5] = {"ok": 0, "error": "parent_failed"}
        rows = _rows(PRESET_8)
        # The read after: three worn items and the weapon with its magazine.
        read = rows[:3] + [dict(rows[6]), dict(rows[7], parent=3)]
        shaped = loadout.apply_reply(flat, _flat_answer(read, applied))
        self.assertEqual(
            [(item["path"], item["error"]) for item in shaped["items"] if not item["ok"]],
            [
                ("loadout.attachments[3]", "slot_not_found"),
                ("loadout.attachments[3].cargo[0]", "parent_failed"),
                ("loadout.attachments[3].cargo[1]", "parent_failed"),
            ],
        )
        self.assertEqual(shaped["status"], "partial")

    def test_a_read_after_past_the_caps_is_null_and_flagged(self) -> None:
        flat = _flatten(PRESET_8)
        applied = [{"ok": 1, "error": ""} for _ in flat]
        shaped = loadout.apply_reply(flat, _flat_answer([], applied, too_large=1))
        self.assertEqual(shaped["ok"], 1)
        self.assertIsNone(shaped["loadout"])
        self.assertIs(shaped["loadout_too_large"], True)
        self.assertEqual(shaped["status"], "ok")
        self.assertEqual(len(shaped["items"]), 8)

    def test_an_apply_answer_that_cannot_be_read_is_not_ok_and_does_not_raise(self) -> None:
        flat = _flatten(PRESET_8)
        ok_rows = [{"ok": 1, "error": ""} for _ in flat]
        cases = {
            "member_absent": {"ok": 1},
            "one_outcome_short": _flat_answer(_rows(PRESET_8), ok_rows[:-1]),
            "outcome_not_an_object": _flat_answer(_rows(PRESET_8), ok_rows[:-1] + [1]),
            "ok_with_an_error": _flat_answer(
                _rows(PRESET_8), ok_rows[:-1] + [{"ok": 1, "error": "no_room"}]
            ),
            "failed_without_an_error": _flat_answer(
                _rows(PRESET_8), ok_rows[:-1] + [{"ok": 0, "error": ""}]
            ),
            "ok_not_a_bool": _flat_answer(
                _rows(PRESET_8), ok_rows[:-1] + [{"ok": "yes", "error": ""}]
            ),
            "too_large_not_a_bool": _flat_answer(_rows(PRESET_8), ok_rows, too_large=2),
            "read_not_a_tree": _flat_answer([{"type": "Apple"}], ok_rows),
        }
        for label, answer in cases.items():
            with self.subTest(label):
                shaped = loadout.apply_reply(flat, answer)
                self.assertEqual(shaped["ok"], 0)
                self.assertEqual(shaped["warnings"], [loadout.INCOMPLETE])
                self.assertNotIn("status", shaped)


def _flatten(tree: object) -> list[tuple[str, dict[str, Any]]]:
    return loadout.flatten_request(tree)


class LoadoutToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def _call(self, name: str, bridge_answer: dict[str, Any], **arguments: Any):
        call = AsyncMock(return_value=bridge_answer)
        with patch.object(self.runtime, "call_bridge", new=call):
            result = _content_json(await self.app.call_tool(name, dict(arguments)))
        return result, call

    async def _refused(self, name: str, **arguments: Any) -> str:
        call = AsyncMock(return_value={"ok": 1})
        with patch.object(self.runtime, "call_bridge", new=call):
            with self.assertRaises(Exception) as raised:
                await self.app.call_tool(name, dict(arguments))
        call.assert_not_awaited()
        return str(raised.exception)

    async def _description(self, name: str) -> str:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        return tools[name].description or ""

    async def test_get_sends_the_target_and_answers_the_tree(self) -> None:
        answer = _flat_answer(_rows(PRESET_8))
        cases = (
            ({}, {}, DEFAULT_TOOL_TIMEOUT_S),
            ({"object_id": 7, "timeout_s": 2.0}, {"object_id": 7}, 2.0),
            ({"uid": "76561198000000000", "timeout_s": 2.0}, {"uid": "76561198000000000"}, 2.0),
        )
        for arguments, wire, timeout in cases:
            with self.subTest(arguments=arguments):
                result, call = await self._call(GET, dict(answer), **arguments)
                call.assert_awaited_once_with(GET, wire, "server", timeout)
                self.assertEqual(loopback.validate_command_args(GET, wire), (True, None))
                self.assertEqual(_classes(result["loadout"]), _classes(PRESET_8))
                self.assertEqual(result["type"], "SurvivorM_Mirek")

    async def test_get_refuses_both_targets_before_the_bridge(self) -> None:
        text = await self._refused(GET, object_id=3, uid="76561198000000000")
        self.assertIn("bad_args", text)
        self.assertIn("must not both be given", text)
        self.assertIn("bad_args", await self._refused(GET, object_id=-2))

    async def test_apply_sends_the_rows_in_apply_order(self) -> None:
        flat = _flatten(PRESET_8)
        applied = [{"ok": 1, "error": ""} for _ in flat]
        result, call = await self._call(
            APPLY,
            _flat_answer(_rows(PRESET_8), applied),
            loadout=PRESET_8,
            object_id=5,
            clear_first=True,
            timeout_s=3.0,
        )
        wire = {"object_id": 5, "clear_first": True, "loadout_items": _rows(PRESET_8)}
        call.assert_awaited_once_with(APPLY, wire, "server", 3.0)
        self.assertEqual(loopback.validate_command_args(APPLY, wire), (True, None))
        self.assertEqual(result["status"], "ok")
        self.assertEqual([item["path"] for item in result["items"]], PRESET_8_PATHS)
        self.assertEqual(_classes(result["loadout"]), _classes(PRESET_8))

        _default, default_call = await self._call(APPLY, _flat_answer([]), loadout={})
        default_call.assert_awaited_once_with(
            APPLY, {"clear_first": False, "loadout_items": []}, "server", DEFAULT_TOOL_TIMEOUT_S
        )

    async def test_apply_refuses_a_bad_loadout_before_the_bridge(self) -> None:
        missing_slot = {"attachments": [
            {"type": "TShirt_White", "slot": "Body"},
            {"type": "Jeans_Blue", "slot": "Legs"},
            {"type": "AthleticShoes_Black"},
        ]}
        text = await self._refused(APPLY, loadout=missing_slot)
        self.assertIn("bad_args: loadout.attachments[2].slot", text)
        too_many = {"attachments": [{"type": "Rag", "slot": f"Slot{i}"} for i in range(65)]}
        self.assertIn("at most 64 items", await self._refused(APPLY, loadout=too_many))
        self.assertIn("nests at most 4", await self._refused(APPLY, loadout=_chain(5)))
        self.assertIn(
            "must not both be given",
            await self._refused(APPLY, loadout={}, object_id=2, uid="7656"),
        )
        for clear_first in (1, "true"):
            with self.subTest(clear_first=clear_first):
                await self._refused(APPLY, loadout={}, clear_first=clear_first)

    async def test_a_bridge_refusal_reaches_the_caller(self) -> None:
        # The embedded runtime's own call_bridge and wait_for_result, with only
        # the loopback state and the readiness snapshot faked (fb99 NEG).
        ready = {
            "server_peer": {
                "last_poll_age_s": 0.1,
                "version_state": "ok",
                "capabilities": {"state": "match", "reason": "ok"},
            },
            "client_peer": {"last_poll_age_s": 0.1, "version_state": "ok"},
        }
        for name, arguments, error in (
            (GET, {"object_id": 404}, "not_found"),
            (GET, {"object_id": 12}, "bad_target"),
            (GET, {}, "loadout_too_large"),
            (APPLY, {"loadout": {}, "object_id": 404}, "not_found"),
            (APPLY, {"loadout": {}, "object_id": 12}, "bad_target"),
            (APPLY, {"loadout": {}, "object_id": 5, "clear_first": True}, "busy"),
        ):
            with self.subTest(name=name, error=error):
                enqueued: list[tuple[str, dict[str, object], object]] = []

                def enqueue(cmd: str, args: dict[str, object], peer: object = None, **_kwargs: object):
                    enqueued.append((cmd, dict(args), peer))
                    return 200, {"id": 7}

                state = SimpleNamespace(
                    enqueue_command=enqueue,
                    daemon_generation="generation-test",
                    take_result=lambda *_args, _error=error, **_kwargs: {"ok": 0, "error": _error},
                )
                with patch.object(self.runtime, "status", return_value=ready), patch.object(
                    self.runtime, "loopback", SimpleNamespace(state=state)
                ):
                    with self.assertRaises(ToolError) as raised:
                        await self.app.call_tool(name, dict(arguments, timeout_s=1.0))
                self.assertTrue(
                    str(raised.exception).endswith(f": {error}"), str(raised.exception)
                )
                self.assertEqual([entry[0] for entry in enqueued], [name])

    async def test_descriptions_state_the_lease_the_format_and_the_errors(self) -> None:
        read = await self._description(GET)
        write = await self._description(APPLY)
        self.assertFalse(read.startswith(LEASE_TOOL_LINE), read)
        self.assertIn("needs no lease", read)
        self.assertTrue(write.startswith(LEASE_TOOL_LINE), write)
        for fragment in (
            "{attachments: [item, ...], hands: item or null}",
            "{type, slot, attachments, cargo}",
            "InventorySlots.GetSlotName",
            "not_found",
            "bad_target",
            "loadout_too_large",
            "loadout_incomplete",
            "arg_contract_mismatch or bridge_capability_missing, with nothing sent",
            f"{loadout.MAX_ITEMS} items",
            f"{loadout.MAX_DEPTH} levels",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, read)
        for fragment in (
            "{attachments: [item, ...], hands: item or null}",
            "bad_args names the path (loadout.attachments[2].slot)",
            "clear_first=true first deletes",
            "slot_occupied and keeps what is there",
            "unknown_type",
            "slot_not_found",
            "cannot_attach",
            "no_room",
            "parent_failed",
            "status is ok when every item was created and partial otherwise",
            "loadout_too_large true",
            "Created items get no registry ids",
            "Quantity, health and other item state are not set",
            "busy",
            "arg_contract_mismatch or bridge_capability_missing, with nothing sent",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, write)

    async def test_numeric_params_are_strict(self) -> None:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        for name in (GET, APPLY):
            with self.subTest(name=name):
                properties = tools[name].inputSchema["properties"]
                self.assertEqual(properties["object_id"]["type"], "integer")
                self.assertEqual(properties["uid"]["type"], "string")
                self.assertEqual(properties["timeout_s"]["type"], "number")
        apply_schema = tools[APPLY].inputSchema
        self.assertEqual(apply_schema["required"], ["loadout"])
        self.assertEqual(apply_schema["properties"]["clear_first"]["type"], "boolean")
        self.assertEqual(apply_schema["properties"]["loadout"]["type"], "object")


class LoadoutCatalogTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_compact_catalog_lists_the_read_before_a_lease(self) -> None:
        self.assertIn(GET, server._INITIAL_READ_TOOL_NAMES)
        self.assertNotIn(APPLY, server._INITIAL_READ_TOOL_NAMES)

        class _ListingRuntime:
            def __init__(self, config: ServerConfig, **_kwargs: object) -> None:
                self.config = config
                self.active_lease_token = None
                self._registered_tool_names = None

        with patch("dayz_mcp.server.ClientRuntime", _ListingRuntime):
            app, runtime = build_app(
                ServerConfig(
                    mode="client",
                    key="k",
                    port=12345,
                    log_sink=lambda _message: None,
                    client_platform="codex",
                )
            )
        self.assertIsNone(runtime.active_lease_token)
        listed = {tool.name: tool for tool in await app.list_tools()}
        self.assertIn(GET, listed)
        self.assertNotIn(APPLY, listed)
        self.assertEqual(
            listed[GET].description,
            "Read what one target wears and holds, in the format loadout_apply "
            "takes. It changes nothing and needs no lease. …",
        )

    async def test_the_embedded_registry_has_both(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))
        names = {tool.name for tool in app._tool_manager.list_tools()}
        self.assertIn(GET, names)
        self.assertIn(APPLY, names)


class LoadoutPruneTest(unittest.TestCase):
    def test_the_reply_member_is_prunable_and_an_empty_read_survives(self) -> None:
        self.assertIn("loadout", result_prune.PRUNABLE_FIELDS)
        self.assertNotIn("loadout", result_prune.OWNED_SCALAR_FIELDS)
        # A target that wears nothing still answers its (empty) read.
        empty = {"items": [], "applied": [], "too_large": 0}
        for command in (GET, APPLY):
            with self.subTest(command=command):
                kept = result_prune.prune_unfilled_fields(command, {"ok": 1, "loadout": dict(empty)})
                self.assertEqual(kept["loadout"], empty)
        for unfilled in ({}, None):
            with self.subTest(unfilled=unfilled):
                dropped = result_prune.prune_unfilled_fields(
                    "world_time_get", {"ok": 1, "loadout": unfilled}
                )
                self.assertNotIn("loadout", dropped)


class LoadoutEnforceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = BRIDGE_PATH.read_text(encoding="utf-8")
        self.messages = MESSAGES_PATH.read_text(encoding="utf-8")

    def _code(self, signature: str) -> str:
        return _without_comments(_method_body(self.source, signature))

    def test_dispatch_routes_both_and_the_census_names_them(self) -> None:
        dispatch = _method_body(self.source, "protected void Dispatch(MCPCommand command)")
        for command, handler in ((GET, "DispatchLoadoutGet"), (APPLY, "DispatchLoadoutApply")):
            with self.subTest(command=command):
                branch = _if_body(dispatch, f'command.cmd == "{command}"')
                self.assertEqual(branch.strip(), f"postNow = {handler}(command, result);")
        caps = _server_capabilities(self.source)
        self.assertIn(GET, caps)
        self.assertIn(APPLY, caps)
        self.assertEqual(caps, sorted(caps))
        self.assertIn(
            f'SERVER_ARG_CONTRACT_HASH = "{server.EXPECTED_SERVER_ARG_CONTRACT_HASH}"',
            self.source,
        )
        self.assertIn('const string MCP_BRIDGE_VERSION = "12";', self.messages)
        self.assertEqual(EXPECTED_BRIDGE_VERSION, "12")

    def test_the_caps_mirror_the_tool_s_caps(self) -> None:
        items = re.search(r"protected const int LOADOUT_MAX_ITEMS = (\d+);", self.source)
        depth = re.search(r"protected const int LOADOUT_MAX_DEPTH = (\d+);", self.source)
        self.assertIsNotNone(items)
        self.assertIsNotNone(depth)
        assert items is not None and depth is not None
        self.assertEqual(int(items.group(1)), loadout.MAX_ITEMS)
        self.assertEqual(int(depth.group(1)), loadout.MAX_DEPTH)

    def test_the_target_is_refused_before_anything_is_read(self) -> None:
        code = self._code(_ENFORCE_FUNCTIONS[2])
        self.assertEqual(_errors(code), {"bad_args", "not_found", "bad_target"})
        for token in (
            "return ResolvePlayer(args, error);",
            "registered = RuntimeObjectById(args.object_id);",
            "survivor = PlayerBase.Cast(registered);",
            "if (survivor.GetIdentity())",
        ):
            with self.subTest(token=token):
                self.assertEqual(code.count(token), 1)
        not_found = _if_body(code, "!registered")
        self.assertIn('error = "not_found";', not_found)
        not_a_survivor = _if_body(code, "!survivor")
        self.assertIn('error = "bad_target";', not_a_survivor)
        both = _if_body(code, 'args.uid != ""')
        self.assertIn('error = "bad_args";', both)
        self.assertLess(code.index("return ResolvePlayer"), code.index('args.uid != ""'))

    def test_the_read_writes_nothing_and_answers_nothing_past_the_caps(self) -> None:
        get = self._code(_ENFORCE_FUNCTIONS[0])
        read_path = "".join(
            self._code(signature) for signature in (
                _ENFORCE_FUNCTIONS[0], _ENFORCE_FUNCTIONS[7], _ENFORCE_FUNCTIONS[8],
                _ENFORCE_FUNCTIONS[9],
            )
        )
        for forbidden in ("ObjectDelete", "Create", "m_RuntimeObjects", "m_Jobs", "ApplyLoadout", "ClearLoadout"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, read_path)
        too_large = _if_body(get, "loadoutRead.too_large")
        self.assertIn('result.error = "loadout_too_large";', too_large)
        self.assertNotIn("result.loadout", too_large)
        self.assertLess(get.index("ResolveLoadoutTarget("), get.index("ReadLoadout("))
        self.assertLess(get.index('"loadout_too_large"'), get.index("result.loadout = loadoutRead;"))
        read = self._code(_ENFORCE_FUNCTIONS[7])
        self.assertIn("loadoutRead.items.Clear();", _if_body(read, "loadoutRead.too_large"))
        append = self._code(_ENFORCE_FUNCTIONS[8])
        self.assertIn(
            "if (level > LOADOUT_MAX_DEPTH || loadoutRead.items.Count() >= LOADOUT_MAX_ITEMS)",
            append,
        )
        # Attachments, then cargo, depth first: the order flatten_request uses.
        self.assertLess(
            append.index('AppendLoadoutRow(loadoutRead, child, "attachment", rowIndex, level + 1);'),
            append.index('AppendLoadoutRow(loadoutRead, child, "cargo", rowIndex, level + 1);'),
        )
        self.assertLess(
            read.index('AppendLoadoutRow(loadoutRead, attachment, "attachment", -1, 1);'),
            read.index('AppendLoadoutRow(loadoutRead, hands, "hands", -1, 1);'),
        )
        slot_name = self._code(_ENFORCE_FUNCTIONS[9])
        self.assertIn("return InventorySlots.GetSlotName(location.GetSlot());", slot_name)

    def test_apply_checks_rows_and_target_before_it_changes_anything(self) -> None:
        code = self._code(_ENFORCE_FUNCTIONS[1])
        order = [
            "rowsError = LoadoutRowsError(command.args.loadout_items);",
            "target = ResolveLoadoutTarget(command.args, targetError);",
            "if (HasActiveJobFor(null, target))",
            "ClearLoadout(target, cleared);",
            'job.kind = "loadout";',
            "m_Jobs.Insert(job.id, job);",
            "ApplyLoadout(target, command.args, result);",
        ]
        positions = []
        for token in order:
            with self.subTest(token=token):
                self.assertEqual(code.count(token), 1)
            positions.append(code.index(token))
        self.assertEqual(positions, sorted(positions))
        self.assertIn('result.error = "busy";', _if_body(code, "HasActiveJobFor(null, target)"))
        queued = _if_body(code, "cleared.Count() > 0")
        self.assertIn("job.loadout_cleared = cleared;", queued)
        self.assertIn("job.subject = target;", queued)
        self.assertIn("return false;", queued)

        rows = self._code(_ENFORCE_FUNCTIONS[3])
        self.assertEqual(set(re.findall(r'return "(\w*)";', rows)), {"bad_args", ""})
        for token in (
            "if (rows.Count() > LOADOUT_MAX_ITEMS)",
            "if (row.parent < -1 || row.parent >= i)",
            "if (handsRows > 1)",
            "if (level > LOADOUT_MAX_DEPTH)",
            "level = levels.Get(row.parent) + 1;",
        ):
            with self.subTest(token=token):
                self.assertIn(token, rows)

    def test_each_row_error_and_an_occupied_slot_keeps_its_item(self) -> None:
        create = self._code(_ENFORCE_FUNCTIONS[6])
        apply = self._code(_ENFORCE_FUNCTIONS[5])
        self.assertEqual(
            _errors(create),
            {"unknown_type", "slot_not_found", "slot_occupied", "cannot_attach", "no_room"},
        )
        self.assertEqual(_errors(apply), {"parent_failed"})
        self.assertEqual(_errors(create) | _errors(apply), set(loadout.ITEM_ERRORS))
        self.assertLess(create.index("IsKnownInventoryClassname(row.type)"), create.index("CreateInHands"))
        # The occupied checks come before the creates, and delete nothing.
        occupied_slot = _if_body(create, "inventory.FindAttachment(slotId)")
        self.assertIn('error = "slot_occupied";', occupied_slot)
        self.assertNotIn("ObjectDelete", occupied_slot)
        self.assertLess(
            create.index("if (inventory.FindAttachment(slotId))"),
            create.index("item = inventory.CreateAttachmentEx(row.type, slotId);"),
        )
        occupied_hands = _if_body(create, "target.GetItemInHands()")
        self.assertIn('error = "slot_occupied";', occupied_hands)
        self.assertNotIn("ObjectDelete", occupied_hands)
        self.assertLess(
            create.index("if (target.GetItemInHands())"),
            create.index("item = target.GetHumanInventory().CreateInHands(row.type);"),
        )
        # An item created somewhere else than asked is removed again.
        for postcondition in (
            "target.GetItemInHands() != item",
            "inventory.FindAttachment(slotId) != item",
            "!inventory.HasEntityInCargo(item)",
        ):
            with self.subTest(postcondition=postcondition):
                self.assertIn("GetGame().ObjectDelete(item);", _if_body(create, postcondition))

    def test_every_row_gets_one_outcome_and_a_failed_parent_stops_its_children(self) -> None:
        apply = self._code(_ENFORCE_FUNCTIONS[5])
        loop = _block_after(apply, apply.index("while (i < args.loadout_items.Count())"))
        for token in (
            "created.Insert(item);",
            "loadoutReply.applied.Insert(outcome);",
            "owner = created.Get(row.parent);",
            'itemError = "parent_failed";',
            "item = CreateLoadoutItem(target, owner, row, itemError);",
        ):
            with self.subTest(token=token):
                self.assertEqual(loop.count(token), 1)
        self.assertLess(apply.index("while (i < args.loadout_items.Count())"), apply.index("ReadLoadout(target, loadoutReply);"))
        self.assertIn("result.loadout = loadoutReply;", apply)
        self.assertNotIn("m_RuntimeObjects", apply)

    def test_clear_first_deletes_then_waits_for_the_objects_to_go(self) -> None:
        clear = self._code(_ENFORCE_FUNCTIONS[4])
        self.assertIn("GetGame().ObjectDelete(cleared.Get(i));", clear)
        self.assertLess(clear.index("cleared.Insert(hands);"), clear.index("GetGame().ObjectDelete"))
        ready = _method_body(self.source, "protected bool IsJobReady(MCPJob job)")
        self.assertEqual(
            _if_body(ready, 'job.kind == "loadout"').strip(), "return IsLoadoutClearDone(job);"
        )
        for hook in ("protected void PostJobSuccess(MCPJob job)", "protected void PostJobTimeout(MCPJob job)"):
            with self.subTest(hook=hook):
                body = _without_comments(_method_body(self.source, hook))
                branch = _if_body(body, 'job.kind == "loadout"')
                self.assertEqual(
                    [line.strip() for line in branch.strip().splitlines()],
                    ["PostLoadoutJob(job);", "return;"],
                )
        done = self._code(_ENFORCE_FUNCTIONS[10])
        self.assertIn("if (job.loadout_cleared.Get(i))", done)
        post = self._code(_ENFORCE_FUNCTIONS[11])
        self.assertIn("ApplyLoadout(target, job.args, result);", post)
        self.assertIn("PostResult(result);", post)
        self.assertEqual(_errors(post), {"not_found"})

    def test_compile_hazards_locals_on_top_and_no_ternary(self) -> None:
        for signature in _ENFORCE_FUNCTIONS:
            with self.subTest(signature=signature):
                body = _method_body(self.source, signature)
                _locals_declared_at_top(body)
                self.assertIsNone(re.search(r"\?[^;]*:", _without_comments(body)))
        with self.assertRaises(AssertionError):
            _locals_declared_at_top("\t\tint a = 0;\n\t\ta = 1;\n\t\tint b = 0;\n")
        with self.assertRaises(AssertionError):
            _locals_declared_at_top("\t\tint a;\n\t\tint a;\n")

    def test_message_classes_carry_the_rows_and_the_reply(self) -> None:
        self.assertEqual(
            _class_members(self.messages, "MCPLoadoutItem"),
            [("string", "type"), ("string", "slot"), ("string", "dest"), ("int", "parent")],
        )
        self.assertEqual(
            {name for _type, name in _class_members(self.messages, "MCPLoadoutItem")},
            set(loadout._ROW_KEYS),
        )
        self.assertEqual(
            _class_members(self.messages, "MCPLoadoutApplied"),
            [("bool", "ok"), ("string", "error")],
        )
        self.assertEqual(
            _class_members(self.messages, "MCPLoadout"),
            [
                ("ref array<ref MCPLoadoutItem>", "items"),
                ("ref array<ref MCPLoadoutApplied>", "applied"),
                ("bool", "too_large"),
            ],
        )
        args = _class_members(self.messages, "MCPArgs")
        self.assertEqual(args.count(("ref array<ref MCPLoadoutItem>", "loadout_items")), 1)
        self.assertEqual(args.count(("bool", "clear_first")), 1)
        self.assertEqual(
            _class_members(self.messages, "MCPResult").count(("ref MCPLoadout", "loadout")), 1
        )
        self.assertEqual(
            _class_members(self.messages, "MCPJob").count(("ref array<EntityAI>", "loadout_cleared")), 1
        )
        for name in ("class MCPLoadoutItem", "class MCPLoadoutApplied", "class MCPLoadout\n"):
            with self.subTest(name=name):
                self.assertLess(self.messages.index(name), self.messages.index("class MCPResult"))
        self.assertLess(self.messages.index("class MCPLoadoutItem"), self.messages.index("class MCPArgs"))


class LoadoutRoundTripThroughThePollerTest(unittest.IsolatedAsyncioTestCase):
    """The tool through the embedded runtime, the ingress and a polling fake."""

    async def asyncSetUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        self.runtime.start_loopback()
        bind_both_peers(self.runtime.state)
        self.peers: list[FakePeer] = []

    async def asyncTearDown(self) -> None:
        await asyncio.gather(*(asyncio.to_thread(peer.stop) for peer in self.peers))
        await asyncio.to_thread(self.runtime.stop_loopback)

    def _peer(self, peer: str, responder=None) -> FakePeer:
        fake = FakePeer(
            self.runtime,
            "test-key",
            peer,
            version=f"{EXPECTED_BRIDGE_VERSION}~1.29.0",
            responder=responder,
        )
        fake.start()
        self.peers.append(fake)
        return fake

    @slow_test
    async def test_an_apply_arrives_shaped_with_the_other_refs_pruned(self) -> None:
        def answer(command: dict[str, Any]) -> dict[str, Any]:
            result: dict[str, Any] = {"id": command["id"], "ok": 1, "cmd": command["cmd"]}
            if command["cmd"] == APPLY:
                rows = command["args"]["loadout_items"]
                # The flat MCPResult: the verb's own member plus empty others.
                result["type"] = "SurvivorM_Mirek"
                result["loadout"] = {
                    "items": rows,
                    "applied": [{"ok": 1, "error": ""} for _ in rows],
                    "too_large": 0,
                }
                result["applied"] = {}
                result["world_time"] = {}
            return result

        server_peer = self._peer("server", answer)
        self._peer("client")
        deadline = time.monotonic() + 2.0
        while not server.compute_bridge_ready(self.runtime.status())["ready"]:
            if time.monotonic() > deadline:
                self.fail("bridge never became ready")
            await asyncio.sleep(0.02)

        result = _content_json(
            await self.app.call_tool(APPLY, {"loadout": PRESET_8, "timeout_s": 2.0})
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["items"]), 8)
        self.assertEqual(_classes(result["loadout"]), _classes(PRESET_8))
        self.assertNotIn("applied", result)
        self.assertNotIn("world_time", result)
        sent = [command for command in server_peer.commands_seen if command["cmd"] == APPLY]
        self.assertEqual(len(sent), 1)
        self.assertEqual(
            sent[0]["args"], {"clear_first": False, "loadout_items": _rows(PRESET_8)}
        )


if __name__ == "__main__":
    unittest.main()
