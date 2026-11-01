"""loadout_get and loadout_apply: one loadout format for a read and a write.

fb-20261023-235342-fb99 (loadout_get) and fb-20261023-235342-220f
(loadout_apply). The tool checks the whole loadout and flattens it into rows
(dayz_mcp/loadout.py); the bridge creates the rows in order, reads the target
back into the same rows, and the tool nests them into the tree again.
clear_first is its own internal bridge verb, loadout_clear: it deletes in its
dispatch tick and its job only waits for the deleted objects to go, so no job
changes the world after the tick it was dispatched in (#261 review Y1). The
tool sends loadout_apply only once the clear has answered and, with neither
object_id nor uid given, by the uid the clear answered, so the creates go to
the player the clear acted on (#261 review Y11).

These tests pin the Python contract, the ingress and lease split, the
older-PBO admission, the reply shapes and the Enforce source the game will
compile. LoadoutBridgeRunTest runs the real Enforce bodies on fakes through
tests/enforce_subset_helpers.py. Nothing here launches DayZ; the in-game
criteria are listed in the PR.
"""
from __future__ import annotations

import asyncio
import copy
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
from tests._tiers import slow_test
from tests.fence_helpers import (
    INST_SERVER,
    PID_SERVER,
    bind_both_peers,
    bound_queue,
)
from tests.loadout_helpers import (
    APPEND_FN,
    APPLY,
    APPLY_FN,
    APPLY_LOADOUT_FN,
    BRIDGE_PATH,
    CLEAR,
    CLEAR_DONE_FN,
    CLEAR_FN,
    CLEAR_LOADOUT_FN,
    CREATE_FN,
    GET,
    GET_FN,
    JOB_READY_FN,
    JOB_SUCCESS_FN,
    JOB_TIMEOUT_FN,
    MESSAGES_PATH,
    POST_CLEAR_FN,
    READ_FN,
    RESOLVE_FN,
    ROLLBACK_FN,
    ROWS_FN,
    SLOT_NAME_FN,
    _Args,
    _Array,
    _Bridge,
    _Command,
    _ENFORCE_FUNCTIONS,
    _Entity,
    _Identity,
    _Job,
    _Player,
    _Result,
    _Row,
    _SLOT_IDS,
    _read_rows,
    _s,
    _wire_rows,
)
from tests.mcp_helpers import FakePeer, _content_json


CENSUS_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "bridge_capabilities_v1.json"
)
# The server arg contract before these verbs: an older PBO announces it.
_PRE_LOADOUT_ARG_CONTRACT_HASH = "4f90284258023d98"
# A PBO built from this PR's first round: loadout_apply took clear_first and
# there was no loadout_clear.
_FIRST_ROUND_ARG_CONTRACT_HASH = "6236c68dfa05bc83"
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
# Two containers with different cargo (#261 review Y9): a read that moved
# cargo from one to the other keeps the same classes and slots.
TWO_CONTAINERS: dict[str, Any] = {
    "attachments": [
        {
            "type": "TaloonBag_Blue",
            "slot": "Back",
            "cargo": [{"type": "Apple"}, {"type": "BandageDressing"}],
        },
        {"type": "SmershVest", "slot": "Vest", "cargo": [{"type": "Rag"}]},
    ],
    "hands": {
        "type": "M4A1",
        "attachments": [{"type": "Mag_STANAG_30Rnd", "slot": "magazine"}],
    },
}


def _rows(tree: object) -> list[dict[str, Any]]:
    return [row for _path, row in loadout.flatten_request(tree)]


def _flatten(tree: object) -> list[tuple[str, dict[str, Any]]]:
    return loadout.flatten_request(tree)


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


def _lineage(tree: dict[str, Any]) -> Counter[tuple[tuple[str, str, str], ...]]:
    """Each item as its chain of (dest, slot, type) from the target down to it.

    Free of list order, as the engine may list attachments in its own order,
    but an Apple in a bag's cargo and an Apple in a vest's cargo differ: the
    parent, slot and type of every item are compared.
    """
    found: Counter[tuple[tuple[str, str, str], ...]] = Counter()

    def walk(item: dict[str, Any], dest: str, chain: tuple[tuple[str, str, str], ...]) -> None:
        link = chain + ((dest, item.get("slot", ""), item["type"]),)
        found[link] += 1
        for child in item.get("attachments", []):
            walk(child, "attachment", link)
        for child in item.get("cargo", []):
            walk(child, "cargo", link)

    for item in tree.get("attachments", []):
        walk(item, "attachment", ())
    if tree.get("hands") is not None:
        walk(tree["hands"], "hands", ())
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


def _all_created(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return _flat_answer(rows, [{"ok": 1, "error": ""} for _ in rows])


_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')
_MEMBER_RE = re.compile(
    r"(?m)(?<![A-Za-z0-9_>])(?P<type>(?:ref\s+)?[A-Za-z_][A-Za-z0-9_]*(?:\s*<[^;{}]+>)?)"
    r"\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*;"
)
_LOCAL_DECLARATION = re.compile(
    r"^\s*(?:string|int|bool|Object|EntityAI|PlayerBase|PlayerIdentity|GameInventory|"
    r"CargoBase|InventoryLocation|MCPJob|MCPResult|MCPVehicleEject|MCPLoadout|MCPLoadoutItem|"
    r"MCPLoadoutApplied|array<EntityAI>|array<int>)\s+(\w+)\s*(?:=[^;]*)?;\s*$"
)
_SERVER_CAPS = re.compile(
    r'protected const string SERVER_CAPABILITIES = ((?:"[^"\n]*"(?: \+ )?)+);'
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
        "uid": "76561198000000000",
        "loadout_items": [{"type": "TShirt_White", "slot": "Body", "dest": "attachment", "parent": -1}],
    },
    CLEAR: {"uid": "76561198000000000"},
}


class LoadoutIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[dict[str, object]] = []
        self.state = loopback.ServerState("test-key")
        bind_both_peers(self.state)
        # The verbs are admitted only against a census that names them.
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

    def test_all_three_are_server_verbs_and_only_the_read_skips_the_lease(self) -> None:
        for command in (GET, APPLY, CLEAR):
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
        for command in (APPLY, CLEAR):
            with self.subTest(command=command):
                self.assertNotIn(command, READ_ONLY_COMMANDS)
                self.assertTrue(command_requires_lease(command))

    def test_a_session_without_a_lease_reads_and_cannot_apply_or_clear(self) -> None:
        self._coordinate()
        status, body = self.state.enqueue_command(
            GET, {"object_id": 3}, "server", identity_payload=IDENTITY
        )
        self.assertEqual((status, body["peer"], body["cmd"]), (200, "server", GET))
        for command in (APPLY, CLEAR):
            with self.subTest(command=command):
                refused = self.state.enqueue_command(
                    command, dict(_WIRE_ARGS[command]), "server", identity_payload=IDENTITY
                )
                self.assertEqual(refused, (423, {"error": "lease_required"}))
        decisions = [event.get("decision") for event in self.events]
        self.assertIn("read", decisions)
        self.assertIn("lease_required", decisions)

    def test_the_payloads_the_tool_builds_pass_the_ingress(self) -> None:
        for command, args in (
            (APPLY, {"uid": "76561198000000000", "loadout_items": _rows(PRESET_8)}),
            (APPLY, {"object_id": 9, "loadout_items": _rows(PRESET_8)}),
            (APPLY, {"uid": "76561198000000000", "loadout_items": []}),
            (CLEAR, {"uid": "76561198000000000"}),
            (CLEAR, {"object_id": 9}),
            (CLEAR, {"uid": "76561198000000000"}),
        ):
            with self.subTest(command=command, args=args):
                self.assertEqual(loopback.validate_command_args(command, args), (True, None))
                status, body = self.state.enqueue_command(command, args)
                self.assertEqual(status, 200, body)

    def test_the_ingress_refuses_bad_args_before_an_id(self) -> None:
        # The bridge refuses each of these again in its own tick
        # (ResolveLoadoutTarget, LoadoutRowsError). What only the ingress
        # refuses is in the test below.
        good = _rows(PRESET_8)
        refused: list[tuple[str, dict[str, Any]]] = [
            (GET, {"object_id": 3, "uid": "x"}),
            (CLEAR, {"object_id": 3, "uid": "x"}),
            (APPLY, {}),
            (APPLY, {"loadout_items": good, "object_id": 2, "uid": "x"}),
            (APPLY, {"loadout_items": good * 9}),
            (APPLY, {"loadout_items": [{"type": "Apple", "slot": "", "dest": "cargo", "parent": -1}]}),
            (APPLY, {"loadout_items": [{"type": "", "slot": "Body", "dest": "attachment", "parent": -1}]}),
        ]
        for command, args in refused:
            with self.subTest(command=command, args=args):
                self.assertEqual(
                    self.state.enqueue_command(command, args), (400, {"error": "bad_args"})
                )

    def test_only_the_ingress_refuses_object_id_0_and_names_with_spaces(self) -> None:
        # The bridge would take each of these (#261 review Y3): object_id 0
        # leaves the choice to uid (ResolveLoadoutTarget hands it to
        # ResolvePlayer), LoadoutRowsError checks a type only for being empty
        # and an attachment's slot likewise, and an unknown key is never read.
        # The ingress is what refuses them, before an id is spent;
        # LoadoutBridgeRunTest.test_the_bridge_takes_what_only_the_ingress_refuses
        # pins the bridge side. The tool never sends object_id 0 (target_args).
        body = {"type": "TShirt_White", "slot": "Body", "dest": "attachment", "parent": -1}
        only_here: list[tuple[str, dict[str, Any]]] = [
            (GET, {"object_id": 0}),
            (CLEAR, {"object_id": 0}),
            (APPLY, {"object_id": 0, "loadout_items": []}),
            (APPLY, {"loadout_items": [dict(body, type="TShirt White")]}),
            (APPLY, {"loadout_items": [dict(body, slot="Bo dy")]}),
            (GET, {"loadout_items": []}),
            (CLEAR, {"loadout_items": []}),
            (APPLY, {"clear_first": True, "loadout_items": []}),
        ]
        for command, args in only_here:
            with self.subTest(command=command, args=args):
                self.assertEqual(loopback.validate_command_args(command, args), (False, "bad_args"))
                self.assertEqual(
                    self.state.enqueue_command(command, args), (400, {"error": "bad_args"})
                )


class LoadoutAdmissionTest(unittest.TestCase):
    """An older PBO is never handed these verbs, and no id is spent on it.

    Each fails on a tree that leaves a verb out of _CAPABILITY_ADMISSION_COMMANDS
    or out of SERVER_ARG_CONTRACT: enqueue then answers 200 and allocates an id.
    """

    def _bound(self) -> loopback.ServerState:
        state = loopback.ServerState(key="k")
        state.install_bound_peer(
            instance=INST_SERVER, role="server", pid=PID_SERVER, run_id="loadout-run"
        )
        return state

    def test_a_census_without_the_verb_refuses_before_an_id(self) -> None:
        for command in (GET, APPLY, CLEAR):
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

    def test_an_older_contract_hash_refuses_before_an_id(self) -> None:
        # Before these verbs, and the first round of this PR, whose
        # loadout_apply still read clear_first.
        for ach in (_PRE_LOADOUT_ARG_CONTRACT_HASH, _FIRST_ROUND_ARG_CONTRACT_HASH):
            for command in (GET, APPLY, CLEAR):
                with self.subTest(ach=ach, command=command):
                    state = self._bound()
                    _announce_server(state, _server_commands(), ach)
                    before = state._next_id
                    status, body = state.enqueue_command(
                        command, dict(_WIRE_ARGS[command]), peer="server"
                    )
                    self.assertEqual(status, 409)
                    self.assertEqual(body["error"], "arg_contract_mismatch")
                    self.assertEqual(state._next_id, before)
                    self.assertEqual(bound_queue(state, "server"), [])
        self.assertNotIn(
            server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
            (_PRE_LOADOUT_ARG_CONTRACT_HASH, _FIRST_ROUND_ARG_CONTRACT_HASH),
        )

    def test_the_current_census_admits_all_three(self) -> None:
        for command in (GET, APPLY, CLEAR):
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
            server.SERVER_ARG_CONTRACT[APPLY], ("loadout_items", "object_id", "uid")
        )
        self.assertEqual(server.SERVER_ARG_CONTRACT[CLEAR], ("object_id", "uid"))
        self.assertEqual(_BRIDGE_COMMAND_TOOLS["server"][GET], GET)
        self.assertEqual(_BRIDGE_COMMAND_TOOLS["server"][APPLY], APPLY)
        # loadout_clear has no tool of its own: loadout_apply sends it.
        self.assertIn(CLEAR, _BRIDGE_COMMAND_TOOLS["server"])
        self.assertIsNone(_BRIDGE_COMMAND_TOOLS["server"][CLEAR])
        fixture = json.loads(CENSUS_FIXTURE.read_text(encoding="utf-8"))
        for command, tool in ((GET, GET), (APPLY, APPLY), (CLEAR, None)):
            with self.subTest(command=command):
                self.assertIn(command, fixture["peers"]["server"])
                self.assertEqual(fixture["peers"]["server"][command], tool)
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


def _null_list(branch: str, level: int, key: str) -> tuple[dict[str, Any], str]:
    """A loadout whose item `level` levels below the target, worn or in hands,
    has `key` null, and that list's path. Bags in cargo lead down to it."""
    item: dict[str, Any] = {"type": "SmershBag", key: None}
    for _ in range(level - 1):
        item = {"type": "SmershBag", "cargo": [item]}
    tail = ".cargo[0]" * (level - 1) + f".{key}"
    if branch == "hands":
        return {"hands": item}, "loadout.hands" + tail
    item["slot"] = "Back"
    return {"attachments": [item]}, "loadout.attachments[0]" + tail


# A list that is null where a loadout or an item has one (#261 review Y8): the
# root's attachments, and both lists of an item at every level the caps admit,
# worn and in hands (#261 review Y14). An item four levels down holds no
# items, but its lists may be there, empty.
_NULL_LISTS_GIVEN: tuple[tuple[dict[str, Any], str], ...] = (
    ({"attachments": None}, "loadout.attachments"),
    (
        {"attachments": [{"type": "TaloonBag_Blue", "slot": "Back", "cargo": None}]},
        "loadout.attachments[0].cargo",
    ),
    (
        {"attachments": [{"type": "PlateCarrierVest", "slot": "Vest", "attachments": None}]},
        "loadout.attachments[0].attachments",
    ),
    ({"hands": {"type": "M4A1", "attachments": None}}, "loadout.hands.attachments"),
    (
        {"hands": {"type": "M4A1", "attachments": [
            {"type": "Mag_STANAG_30Rnd", "slot": "magazine", "cargo": None}
        ]}},
        "loadout.hands.attachments[0].cargo",
    ),
)
_NULL_LISTS: tuple[tuple[dict[str, Any], str], ...] = _NULL_LISTS_GIVEN + tuple(
    case
    for case in (
        _null_list(branch, level, key)
        for branch in ("attachments", "hands")
        for level in range(1, loadout.MAX_DEPTH + 1)
        for key in ("attachments", "cargo")
    )
    if case[1] not in {path for _tree, path in _NULL_LISTS_GIVEN}
)


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

    def test_a_null_list_is_bad_args_at_the_root_and_inside_an_item(self) -> None:
        for tree, path in _NULL_LISTS:
            with self.subTest(path=path):
                with self.assertRaises(ValueError) as raised:
                    loadout.flatten_request(tree)
                self.assertEqual(
                    str(raised.exception), f"bad_args: {path} None must be a list of items"
                )
        paths = {path for _tree, path in _NULL_LISTS}
        self.assertEqual(len(paths), len(_NULL_LISTS))
        # #261 review Y14: the case a check that ignored null from the fourth
        # level down accepted, and the deepest lists of both branches.
        for path in (
            "loadout.hands.cargo[0].cargo[0].attachments",
            "loadout.attachments[0].cargo[0].cargo[0].cargo[0].attachments",
            "loadout.attachments[0].cargo[0].cargo[0].cargo[0].cargo",
            "loadout.hands.cargo[0].cargo[0].cargo[0].attachments",
            "loadout.hands.cargo[0].cargo[0].cargo[0].cargo",
        ):
            with self.subTest(listed=path):
                self.assertIn(path, paths)
        # The same lists empty are a loadout: null is what is refused.
        for tree, path in _NULL_LISTS:
            with self.subTest(empty=path):
                emptied = json.loads(json.dumps(tree).replace("null", "[]"))
                loadout.flatten_request(emptied)

    def test_a_loadout_that_is_not_an_object_is_bad_args(self) -> None:
        for value in ([], None, "TShirt_White", 3):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as raised:
                    loadout.flatten_request(value)
                self.assertEqual(
                    str(raised.exception),
                    f"bad_args: loadout {value!r} must be an object with attachments and hands",
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
        for tree in (PRESET_8, TWO_CONTAINERS, _chain(4), {}, {"hands": {"type": "Apple"}}):
            with self.subTest(tree=tree):
                self.assertTrue(loadout.is_wire_items(_rows(tree)))

    def test_rows_the_ingress_refuses(self) -> None:
        # is_wire_items is the ingress's check of loadout_items. LoadoutRowsError
        # refuses most of these again in the bridge, but not all (#261 review
        # Y3): a type or slot with whitespace (the bridge only checks that a
        # type is not empty and that an attachment has a slot), a missing key
        # (the bridge reads it as "" or 0) and an extra key (never read) only
        # the ingress refuses.
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
            "spaced_slot": [dict(body, slot="Bo dy")],
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

    def test_two_containers_keep_their_own_cargo(self) -> None:
        rows = _rows(TWO_CONTAINERS)
        tree = loadout.nest_rows(rows)
        assert tree is not None
        self.assertEqual(_rows(tree), rows)
        self.assertEqual(_lineage(tree), _lineage(TWO_CONTAINERS))
        bag, vest = tree["attachments"]
        self.assertEqual((bag["slot"], bag["type"]), ("Back", "TaloonBag_Blue"))
        self.assertEqual([item["type"] for item in bag["cargo"]], ["Apple", "BandageDressing"])
        self.assertEqual((vest["slot"], vest["type"]), ("Vest", "SmershVest"))
        self.assertEqual([item["type"] for item in vest["cargo"]], ["Rag"])
        # The class multiset cannot tell the cargo moved; the lineage can.
        swapped = copy.deepcopy(TWO_CONTAINERS)
        worn = swapped["attachments"]
        worn[0]["cargo"], worn[1]["cargo"] = worn[1]["cargo"], worn[0]["cargo"]
        self.assertEqual(_classes(swapped), _classes(TWO_CONTAINERS))
        self.assertNotEqual(_lineage(swapped), _lineage(TWO_CONTAINERS))

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

    def test_a_get_answer_loadout_apply_would_refuse_is_not_ok(self) -> None:
        # #261 review Y5: rows that nest into a tree, but a tree that
        # loadout_apply refuses, are not answered as a loadout.
        body = {"type": "TShirt_White", "slot": "Body", "dest": "attachment", "parent": -1}
        deep = [dict(body, type="SmershVest", slot="Vest")] + [
            {"type": "SmershBag", "slot": "", "dest": "cargo", "parent": index}
            for index in range(4)
        ]
        cases = {
            "attachment_without_a_slot_name": [dict(body, slot="")],
            "spaced_type": [dict(body, type="TShirt White")],
            "spaced_slot": [dict(body, slot="Bo dy")],
            "five_levels": deep,
            "sixty_five_items": [dict(body, slot=f"Slot{index}") for index in range(65)],
        }
        for label, rows in cases.items():
            with self.subTest(label):
                self.assertIsNotNone(loadout.nest_rows(rows))
                shaped = loadout.get_reply(_flat_answer(rows))
                self.assertEqual(shaped["ok"], 0)
                self.assertEqual(shaped["warnings"], [loadout.INCOMPLETE])
        self.assertEqual(loadout.get_reply(_flat_answer(deep[:4]))["ok"], 1)

    def test_an_apply_read_after_loadout_apply_would_refuse_is_not_ok(self) -> None:
        # #261 review Y16: the read after an apply is checked as loadout_get's
        # read is. The apply has run, so this answers and raises nothing.
        body = {"type": "TShirt_White", "slot": "Body", "dest": "attachment", "parent": -1}
        deep = [dict(body, type="SmershVest", slot="Vest")] + [
            {"type": "SmershBag", "slot": "", "dest": "cargo", "parent": index}
            for index in range(4)
        ]
        cases = {
            "attachment_without_a_slot_name": [dict(body, slot="")],
            "spaced_type": [dict(body, type="TShirt White")],
            "spaced_slot": [dict(body, slot="Bo dy")],
            "five_levels": deep,
            "sixty_five_items": [dict(body, slot=f"Slot{index}") for index in range(65)],
        }
        flat = _flatten(PRESET_8)
        created = [{"ok": 1, "error": ""} for _ in flat]
        for label, read in cases.items():
            with self.subTest(label):
                self.assertIsNotNone(loadout.nest_rows(read))
                for request, applied in ((flat, created), ([], [])):
                    shaped = loadout.apply_reply(request, _flat_answer(read, applied))
                    self.assertEqual(shaped["ok"], 0)
                    self.assertEqual(shaped["warnings"], [loadout.INCOMPLETE])
                    self.assertNotIn("status", shaped)
        self.assertEqual(loadout.apply_reply(flat, _flat_answer(deep[:4], created))["ok"], 1)
        self.assertEqual(loadout.apply_reply([], _flat_answer([body]))["ok"], 1)

    def test_eight_items_created_answer_ok(self) -> None:
        flat = _flatten(PRESET_8)
        shaped = loadout.apply_reply(flat, _all_created(_rows(PRESET_8)))
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

    def test_the_clear_answers_what_it_deleted_and_whether_it_all_went(self) -> None:
        for answer, expected in (
            ({"ok": 1, "deleted": 4, "cleanup_complete": 1}, {"deleted": 4, "cleanup_complete": True}),
            ({"ok": 1, "deleted": 4, "cleanup_complete": 0}, {"deleted": 4, "cleanup_complete": False}),
            ({"ok": 1, "deleted": 0, "cleanup_complete": True}, {"deleted": 0, "cleanup_complete": True}),
        ):
            with self.subTest(answer=answer):
                self.assertEqual(loadout.clear_reply(answer), expected)

    def test_a_clear_answer_without_its_counts_is_loadout_incomplete(self) -> None:
        for answer in (
            {"ok": 1},
            {"ok": 1, "cleanup_complete": 1},
            {"ok": 1, "deleted": 2},
            {"ok": 1, "deleted": -1, "cleanup_complete": 1},
            {"ok": 1, "deleted": True, "cleanup_complete": 1},
            {"ok": 1, "deleted": 2.0, "cleanup_complete": 1},
            {"ok": 1, "deleted": 2, "cleanup_complete": 2},
            {"ok": 1, "deleted": 2, "cleanup_complete": "1"},
            None,
            [],
        ):
            with self.subTest(answer=answer):
                with self.assertRaises(ValueError) as raised:
                    loadout.clear_reply(answer)
                self.assertTrue(
                    str(raised.exception).startswith(f"{loadout.INCOMPLETE}: "), raised.exception
                )

    def test_the_clear_answers_the_uid_of_the_player_it_acted_on(self) -> None:
        # #261 review Y11: with neither target the creates go to this uid.
        self.assertEqual(
            loadout.cleared_uid({"ok": 1, "deleted": 0, "cleanup_complete": 1, "uid": "7656"}),
            "7656",
        )
        for answer in (
            {"ok": 1, "deleted": 2, "cleanup_complete": 1},
            {"ok": 1, "uid": ""},
            {"ok": 1, "uid": 7656},
            {"ok": 1, "uid": None},
            None,
            [],
        ):
            with self.subTest(answer=answer):
                with self.assertRaises(ValueError) as raised:
                    loadout.cleared_uid(answer)
                self.assertTrue(
                    str(raised.exception).startswith(
                        f"{loadout.INCOMPLETE}: loadout_clear answered without the uid"
                    ),
                    raised.exception,
                )

    def test_a_refusal_after_the_clear_says_what_the_clear_did(self) -> None:
        self.assertEqual(
            loadout.refusal_after_clear("busy", {"deleted": 3, "cleanup_complete": False}),
            "busy; clear=deleted=3 cleanup_complete=0",
        )
        self.assertEqual(
            loadout.refusal_after_clear("timeout", {"deleted": 0, "cleanup_complete": True}),
            "timeout; clear=deleted=0 cleanup_complete=1",
        )


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

    def _bridge(self, answers: dict[str, object]):
        """A call_bridge answering per verb: a dict, or an exception it raises."""
        sent: list[tuple[str, dict[str, Any], str, float]] = []

        async def call_bridge(
            command: str, args: dict[str, Any], peer: str, timeout_s: float
        ) -> dict[str, Any]:
            sent.append((command, copy.deepcopy(args), peer, timeout_s))
            answer = answers[command]
            if isinstance(answer, BaseException):
                raise answer
            return copy.deepcopy(answer)  # type: ignore[arg-type]

        return call_bridge, sent

    async def _run(self, answers: dict[str, object], **arguments: Any):
        call_bridge, sent = self._bridge(answers)
        with patch.object(self.runtime, "call_bridge", new=call_bridge):
            result = _content_json(await self.app.call_tool(APPLY, dict(arguments)))
        return result, sent

    async def _run_refused(self, answers: dict[str, object], **arguments: Any):
        call_bridge, sent = self._bridge(answers)
        with patch.object(self.runtime, "call_bridge", new=call_bridge):
            with self.assertRaises(ToolError) as raised:
                await self.app.call_tool(APPLY, dict(arguments))
        return str(raised.exception), sent

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
        result, call = await self._call(
            APPLY, _all_created(_rows(PRESET_8)), loadout=PRESET_8, object_id=5, timeout_s=3.0
        )
        wire = {"object_id": 5, "loadout_items": _rows(PRESET_8)}
        call.assert_awaited_once_with(APPLY, wire, "server", 3.0)
        self.assertEqual(loopback.validate_command_args(APPLY, wire), (True, None))
        self.assertEqual(result["status"], "ok")
        self.assertEqual([item["path"] for item in result["items"]], PRESET_8_PATHS)
        self.assertEqual(_classes(result["loadout"]), _classes(PRESET_8))
        # Without clear_first nothing was cleared, and the answer says so.
        self.assertIn("clear", result)
        self.assertIsNone(result["clear"])

        _default, default_call = await self._call(APPLY, _flat_answer([]), loadout={}, uid="76561198000000000")
        default_call.assert_awaited_once_with(
            APPLY, {"uid": "76561198000000000", "loadout_items": []}, "server", DEFAULT_TOOL_TIMEOUT_S
        )

    async def test_clear_first_sends_the_clear_and_then_the_rows(self) -> None:
        # B2: a uid or an object_id already names one target and goes out
        # again as it is; neither target is refused before the bridge.
        for target, cleared_uid, apply_target in (
            ({"object_id": 5}, "", {"object_id": 5}),
            ({"uid": "76561198000000000"}, "76561198000000000", {"uid": "76561198000000000"}),
        ):
            with self.subTest(target=target):
                result, sent = await self._run(
                    {
                        CLEAR: {"ok": 1, "deleted": 4, "cleanup_complete": 1, "uid": cleared_uid},
                        APPLY: _all_created(_rows(PRESET_8)),
                    },
                    loadout=PRESET_8,
                    clear_first=True,
                    timeout_s=3.0,
                    **target,
                )
                self.assertEqual(
                    sent,
                    [
                        (CLEAR, target, "server", 3.0),
                        (APPLY, dict(apply_target, loadout_items=_rows(PRESET_8)), "server", 3.0),
                    ],
                )
                for command, args, _peer, _timeout in sent:
                    self.assertEqual(loopback.validate_command_args(command, args), (True, None))
                self.assertEqual(result["clear"], {"deleted": 4, "cleanup_complete": True})
                self.assertEqual(result["status"], "ok")
                self.assertEqual(len(result["items"]), 8)

    async def test_a_clear_past_its_deadline_still_applies_and_says_so(self) -> None:
        tree = {"attachments": [
            {"type": "TShirt_White", "slot": "Body"},
            {"type": "Jeans_Blue", "slot": "Legs"},
        ]}
        rows = _rows(tree)
        # What the clear deleted still held Body when the creates ran.
        applied = [{"ok": 0, "error": "slot_occupied"}, {"ok": 1, "error": ""}]
        result, sent = await self._run(
            {
                CLEAR: {"ok": 1, "deleted": 2, "cleanup_complete": 0, "uid": "76561198000000000"},
                APPLY: _flat_answer(rows[1:], applied),
            },
            loadout=tree,
            clear_first=True,
            uid="76561198000000000",
        )
        self.assertEqual([command for command, *_rest in sent], [CLEAR, APPLY])
        self.assertEqual(result["clear"], {"deleted": 2, "cleanup_complete": False})
        self.assertEqual(result["status"], "partial")
        self.assertEqual(
            [(item["path"], item["error"]) for item in result["items"]],
            [("loadout.attachments[0]", "slot_occupied"), ("loadout.attachments[1]", None)],
        )

    async def test_a_clear_that_fails_sends_nothing_more(self) -> None:
        for label, answer, fragment in (
            ("refused", ToolError("busy"), ": busy"),
            ("timed_out", ToolError("timeout waiting for loadout_clear id=7"), "timeout waiting"),
            ("unreadable", {"ok": 1}, f"{loadout.INCOMPLETE}: loadout_clear answered without"),
        ):
            with self.subTest(label):
                text, sent = await self._run_refused(
                    {CLEAR: answer, APPLY: _all_created(_rows(PRESET_8))},
                    loadout=PRESET_8,
                    object_id=5,
                    clear_first=True,
                )
                self.assertIn(fragment, text)
                self.assertNotIn("clear=deleted", text)
                self.assertEqual([command for command, *_rest in sent], [CLEAR])

    async def test_a_clear_without_the_uid_it_acted_on_sends_nothing_more(self) -> None:
        # B2: with neither target (or an empty uid and no object_id) the
        # apply is refused before the bridge, so nothing is sent at all.
        counts = {"ok": 1, "deleted": 2, "cleanup_complete": 1}
        for label, target in (
            ("neither", {}),
            ("empty_uid", {"uid": ""}),
        ):
            with self.subTest(label):
                text, sent = await self._run_refused(
                    {CLEAR: dict(counts), APPLY: _all_created(_rows(PRESET_8))},
                    loadout=PRESET_8,
                    clear_first=True,
                    **target,
                )
                self.assertIn("bad_args", text)
                self.assertIn("uid", text)
                self.assertEqual(sent, [])
        for target in ({"uid": "76561198000000000"}, {"object_id": 5}):
            with self.subTest(target=target):
                result, sent = await self._run(
                    {CLEAR: dict(counts), APPLY: _all_created(_rows(PRESET_8))},
                    loadout=PRESET_8,
                    clear_first=True,
                    **target,
                )
                self.assertEqual(sent[1][1], dict(target, loadout_items=_rows(PRESET_8)))
                self.assertEqual(result["status"], "ok")

    async def test_a_cancelled_clear_sends_nothing_more(self) -> None:
        sent: list[str] = []
        waiting = asyncio.Event()

        async def call_bridge(
            command: str, args: dict[str, Any], peer: str, timeout_s: float
        ) -> dict[str, Any]:
            sent.append(command)
            if command == CLEAR:
                waiting.set()
                await asyncio.Event().wait()
            return _all_created(_rows(PRESET_8))

        with patch.object(self.runtime, "call_bridge", new=call_bridge):
            task = asyncio.ensure_future(
                self.app.call_tool(APPLY, {"loadout": PRESET_8, "clear_first": True, "uid": "76561198000000000"})
            )
            await asyncio.wait_for(waiting.wait(), 2.0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(sent, [CLEAR])

    async def test_a_refused_apply_after_its_clear_keeps_what_the_clear_did(self) -> None:
        for complete, suffix in ((1, "cleanup_complete=1"), (0, "cleanup_complete=0")):
            with self.subTest(complete=complete):
                text, sent = await self._run_refused(
                    {
                        CLEAR: {
                            "ok": 1,
                            "deleted": 2,
                            "cleanup_complete": complete,
                            "uid": "76561198000000000",
                        },
                        APPLY: ToolError("lease_required: call session_acquire_wait(purpose=...)"),
                    },
                    loadout=PRESET_8,
                    clear_first=True,
                    uid="76561198000000000",
                )
                self.assertTrue(
                    text.endswith(
                        "lease_required: call session_acquire_wait(purpose=...); "
                        f"clear=deleted=2 {suffix}"
                    ),
                    text,
                )
                self.assertEqual([command for command, *_rest in sent], [CLEAR, APPLY])
        # Without clear_first a refusal carries no clear.
        text, sent = await self._run_refused({APPLY: ToolError("busy")}, loadout=PRESET_8, uid="76561198000000000")
        self.assertTrue(text.endswith(": busy"), text)
        self.assertEqual([command for command, *_rest in sent], [APPLY])

    async def test_apply_refuses_a_bad_loadout_before_the_bridge(self) -> None:
        missing_slot = {"attachments": [
            {"type": "TShirt_White", "slot": "Body"},
            {"type": "Jeans_Blue", "slot": "Legs"},
            {"type": "AthleticShoes_Black"},
        ]}
        text = await self._refused(APPLY, loadout=missing_slot, uid="76561198000000000")
        self.assertIn("bad_args: loadout.attachments[2].slot", text)
        too_many = {"attachments": [{"type": "Rag", "slot": f"Slot{i}"} for i in range(65)]}
        self.assertIn("at most 64 items", await self._refused(APPLY, loadout=too_many, uid="76561198000000000"))
        self.assertIn("nests at most 4", await self._refused(APPLY, loadout=_chain(5), uid="76561198000000000"))
        self.assertIn(
            "must not both be given",
            await self._refused(APPLY, loadout={}, object_id=2, uid="7656"),
        )
        for clear_first in (1, "true"):
            with self.subTest(clear_first=clear_first):
                await self._refused(APPLY, loadout={}, clear_first=clear_first, uid="76561198000000000")
        # A bad loadout is refused before a clear is sent too.
        self.assertIn(
            "bad_args: loadout.attachments[2].slot",
            await self._refused(APPLY, loadout=missing_slot, clear_first=True, uid="76561198000000000"),
        )

    async def test_apply_refuses_a_loadout_that_is_not_an_object_with_its_path(self) -> None:
        # #261 review Y2: the public tool answers bad_args for these, as for any
        # other bad loadout, and never calls the bridge.
        for value in ([], None, "TShirt_White"):
            with self.subTest(value=value):
                for clear_first in (False, True):
                    text = await self._refused(APPLY, loadout=value, clear_first=clear_first, uid="76561198000000000")
                    self.assertIn(
                        f"bad_args: loadout {value!r} must be an object with attachments and hands",
                        text,
                    )

    async def test_apply_refuses_a_null_list_with_its_path_before_the_bridge(self) -> None:
        for tree, path in _NULL_LISTS:
            for clear_first in (False, True):
                with self.subTest(path=path, clear_first=clear_first):
                    text = await self._refused(APPLY, loadout=tree, clear_first=clear_first, uid="76561198000000000")
                    self.assertIn(f"bad_args: {path} None must be a list of items", text)

    async def test_an_unreadable_read_after_the_apply_answers_and_does_not_raise(self) -> None:
        # #261 review Y16, through the tool: an attachment with no slot name.
        read = [{"type": "TShirt_White", "slot": "", "dest": "attachment", "parent": -1}]
        result, call = await self._call(APPLY, _flat_answer(read), loadout={}, uid="76561198000000000")
        call.assert_awaited_once_with(APPLY, {"uid": "76561198000000000", "loadout_items": []}, "server", DEFAULT_TOOL_TIMEOUT_S)
        self.assertEqual(result["ok"], 0)
        self.assertEqual(result["warnings"], [loadout.INCOMPLETE])
        self.assertNotIn("status", result)
        self.assertIsNone(result["clear"])

    def _ready_runtime(self, enqueue, take_result):
        ready = {
            "server_peer": {
                "last_poll_age_s": 0.1,
                "version_state": "ok",
                "capabilities": {"state": "match", "reason": "ok"},
            },
            "client_peer": {"last_poll_age_s": 0.1, "version_state": "ok"},
        }
        state = SimpleNamespace(
            enqueue_command=enqueue,
            daemon_generation="generation-test",
            take_result=take_result,
        )
        return (
            patch.object(self.runtime, "status", return_value=ready),
            patch.object(self.runtime, "loopback", SimpleNamespace(state=state)),
        )

    async def test_a_bridge_refusal_reaches_the_caller(self) -> None:
        # The embedded runtime's own call_bridge and wait_for_result, with only
        # the loopback state and the readiness snapshot faked (fb99 NEG).
        for name, arguments, error, verbs in (
            (GET, {"object_id": 404}, "not_found", [GET]),
            (GET, {"object_id": 12}, "bad_target", [GET]),
            (GET, {}, "loadout_too_large", [GET]),
            (APPLY, {"loadout": {}, "object_id": 404}, "not_found", [APPLY]),
            (APPLY, {"loadout": {}, "object_id": 12}, "bad_target", [APPLY]),
            (APPLY, {"loadout": {}, "object_id": 5}, "busy", [APPLY]),
            # The clear is refused, so the rows are never sent.
            (APPLY, {"loadout": {}, "object_id": 5, "clear_first": True}, "busy", [CLEAR]),
            (APPLY, {"loadout": {}, "object_id": 404, "clear_first": True}, "not_found", [CLEAR]),
        ):
            with self.subTest(name=name, arguments=arguments):
                enqueued: list[tuple[str, dict[str, object], object]] = []

                def enqueue(cmd: str, args: dict[str, object], peer: object = None, **_kwargs: object):
                    enqueued.append((cmd, dict(args), peer))
                    return 200, {"id": 7}

                status, state = self._ready_runtime(
                    enqueue, lambda *_args, _error=error, **_kwargs: {"ok": 0, "error": _error}
                )
                with status, state:
                    with self.assertRaises(ToolError) as raised:
                        await self.app.call_tool(name, dict(arguments, timeout_s=1.0))
                self.assertTrue(
                    str(raised.exception).endswith(f": {error}"), str(raised.exception)
                )
                self.assertEqual([entry[0] for entry in enqueued], verbs)

    async def test_an_apply_refused_after_its_clear_reports_the_clear(self) -> None:
        # The runtime's own call_bridge: the clear answers, then the session
        # has lost its lease before the rows go out.
        enqueued: list[str] = []

        def enqueue(cmd: str, args: dict[str, object], peer: object = None, **_kwargs: object):
            enqueued.append(cmd)
            if cmd == APPLY:
                return 423, {"error": "lease_required"}
            return 200, {"id": 7}

        status, state = self._ready_runtime(
            enqueue, lambda *_args, **_kwargs: {"ok": 1, "deleted": 2, "cleanup_complete": 1}
        )
        with status, state:
            with self.assertRaises(ToolError) as raised:
                await self.app.call_tool(
                    APPLY,
                    {"loadout": PRESET_8, "object_id": 5, "clear_first": True, "timeout_s": 1.0},
                )
        text = str(raised.exception)
        self.assertIn("lease_required", text)
        self.assertTrue(text.endswith("; clear=deleted=2 cleanup_complete=1"), text)
        self.assertEqual(enqueued, [CLEAR, APPLY])

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
            "the game checks once per tick until all of it is gone or 5 s have passed",
            "slot_occupied and keeps what is there",
            "unknown_type",
            "slot_not_found",
            "cannot_attach",
            "no_room",
            "parent_failed",
            "status is ok when every item was created and partial otherwise",
            "loadout_too_large true",
            "clear is null without clear_first, else {deleted, cleanup_complete}",
            "whether all were gone at its last check, which can come one tick after the 5 s",
            "A clear that is refused, times out or is cancelled sends nothing more",
            "clear=deleted=N cleanup_complete=0 or 1",
            "A command the game was already handed still runs after the tool gave up on it",
            "uid is required and never falls back to the first human",
            "give a non-empty uid or an object_id above 0, not both",
            "timeout_s is the wait for each of these game calls",
            "Created items get no registry ids",
            "Quantity, health and other item state are not set",
            "busy (a clear_first on this target is still waiting for what it deleted to go)",
            "arg_contract_mismatch or bridge_capability_missing, with nothing sent",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, write)
        # #261 review Y12: true can come one tick after the 5 s.
        for stale in ("waits up to 5 s", "gone within the 5 s"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, write)

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
        # loadout takes any JSON value and the tool checks it (#261 review Y2).
        self.assertNotIn("type", apply_schema["properties"]["loadout"])


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

    async def test_the_embedded_registry_has_both_tools_and_no_clear_tool(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))
        names = {tool.name for tool in app._tool_manager.list_tools()}
        self.assertIn(GET, names)
        self.assertIn(APPLY, names)
        self.assertNotIn(CLEAR, names)


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

    def test_the_clear_keeps_its_counts_and_the_other_verbs_drop_them(self) -> None:
        # The flat MCPResult carries deleted and cleanup_complete on every
        # answer; 0 and false are loadout_clear's answers, and nobody else's.
        flat = {"ok": 1, "deleted": 0, "cleanup_complete": 0, "loadout": {}}
        kept = result_prune.prune_unfilled_fields(CLEAR, dict(flat))
        self.assertEqual((kept["deleted"], kept["cleanup_complete"]), (0, 0))
        self.assertNotIn("loadout", kept)
        for command in (GET, APPLY):
            with self.subTest(command=command):
                dropped = result_prune.prune_unfilled_fields(command, dict(flat))
                self.assertNotIn("deleted", dropped)
                self.assertNotIn("cleanup_complete", dropped)
        self.assertIn(CLEAR, result_prune.OWNED_SCALAR_FIELDS["deleted"])
        self.assertIn(CLEAR, result_prune.OWNED_SCALAR_FIELDS["cleanup_complete"])


class LoadoutEnforceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = BRIDGE_PATH.read_text(encoding="utf-8")
        self.messages = MESSAGES_PATH.read_text(encoding="utf-8")

    def _code(self, signature: str) -> str:
        return _without_comments(_method_body(self.source, signature))

    def test_dispatch_routes_all_three_and_the_census_names_them(self) -> None:
        dispatch = _method_body(self.source, "protected void Dispatch(MCPCommand command)")
        for command, handler in (
            (GET, "DispatchLoadoutGet"),
            (APPLY, "DispatchLoadoutApply"),
            (CLEAR, "DispatchLoadoutClear"),
        ):
            with self.subTest(command=command):
                branch = _if_body(dispatch, f'command.cmd == "{command}"')
                self.assertEqual(branch.strip(), f"postNow = {handler}(command, result);")
        caps = _server_capabilities(self.source)
        for command in (GET, APPLY, CLEAR):
            with self.subTest(command=command):
                self.assertIn(command, caps)
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
        code = self._code(RESOLVE_FN)
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
        for signature in (GET_FN, APPLY_FN, CLEAR_FN):
            with self.subTest(signature=signature):
                self.assertEqual(
                    self._code(signature).count(
                        "target = ResolveLoadoutTarget(command.args, targetError);"
                    ),
                    1,
                )

    def test_the_read_writes_nothing_and_answers_nothing_past_the_caps(self) -> None:
        get = self._code(GET_FN)
        read_path = "".join(
            self._code(signature) for signature in (GET_FN, READ_FN, APPEND_FN, SLOT_NAME_FN)
        )
        for forbidden in ("ObjectDelete", "Create", "m_RuntimeObjects", "m_Jobs", "ApplyLoadout", "ClearLoadout"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, read_path)
        too_large = _if_body(get, "loadoutRead.too_large")
        self.assertIn('result.error = "loadout_too_large";', too_large)
        self.assertNotIn("result.loadout", too_large)
        self.assertLess(get.index("ResolveLoadoutTarget("), get.index("ReadLoadout("))
        self.assertLess(get.index('"loadout_too_large"'), get.index("result.loadout = loadoutRead;"))
        read = self._code(READ_FN)
        self.assertIn("loadoutRead.items.Clear();", _if_body(read, "loadoutRead.too_large"))
        append = self._code(APPEND_FN)
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
        slot_name = self._code(SLOT_NAME_FN)
        self.assertIn("return InventorySlots.GetSlotName(location.GetSlot());", slot_name)

    def test_apply_checks_rows_and_target_then_creates_in_its_tick(self) -> None:
        code = self._code(APPLY_FN)
        order = [
            "rowsError = LoadoutRowsError(command.args.loadout_items);",
            "target = ResolveLoadoutTarget(command.args, targetError);",
            "if (HasActiveJobFor(null, target))",
            "ApplyLoadout(target, command.args, result);",
        ]
        positions = []
        for token in order:
            with self.subTest(token=token):
                self.assertEqual(code.count(token), 1)
            positions.append(code.index(token))
        self.assertEqual(positions, sorted(positions))
        self.assertIn('result.error = "busy";', _if_body(code, "HasActiveJobFor(null, target)"))
        # The apply queues no job and deletes nothing: that is loadout_clear's.
        for forbidden in ("ClearLoadout", "ObjectDelete", "m_Jobs", "MCPJob", "clear_first"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, code)

        rows = self._code(ROWS_FN)
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

    def test_the_clear_deletes_in_its_tick_and_its_job_only_watches(self) -> None:
        code = self._code(CLEAR_FN)
        order = [
            "target = ResolveLoadoutTarget(command.args, targetError);",
            "if (HasActiveJobFor(null, target))",
            "if (!ClearLoadout(target, cleared))",
            "if (cleared.Count() == 0)",
            'job.kind = "loadout_clear";',
            "m_Jobs.Insert(job.id, job);",
        ]
        positions = []
        for token in order:
            with self.subTest(token=token):
                self.assertEqual(code.count(token), 1)
            positions.append(code.index(token))
        self.assertEqual(positions, sorted(positions))
        self.assertIn('result.error = "busy";', _if_body(code, "HasActiveJobFor(null, target)"))
        self.assertNotIn("ApplyLoadout", code)
        clear = self._code(CLEAR_LOADOUT_FN)
        self.assertIn("GetGame().ObjectDelete(cleared.Get(i));", clear)
        self.assertLess(clear.index("cleared.Insert(hands);"), clear.index("GetGame().ObjectDelete"))
        # Nothing the job runs changes the world.
        job_code = self._code(CLEAR_DONE_FN) + self._code(POST_CLEAR_FN)
        for forbidden in ("ObjectDelete", "Create", "ApplyLoadout", "ClearLoadout", "m_RuntimeObjects"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, job_code)
        ready = _method_body(self.source, JOB_READY_FN)
        self.assertEqual(
            _if_body(ready, 'job.kind == "loadout_clear"').strip(), "return IsLoadoutClearDone(job);"
        )
        for hook, complete in ((JOB_SUCCESS_FN, "true"), (JOB_TIMEOUT_FN, "false")):
            with self.subTest(hook=hook):
                body = _without_comments(_method_body(self.source, hook))
                branch = _if_body(body, 'job.kind == "loadout_clear"')
                self.assertEqual(
                    [line.strip() for line in branch.strip().splitlines()],
                    [f"PostLoadoutClearJob(job, {complete});", "return;"],
                )
        # The first round's "loadout" job kind is gone.
        self.assertNotIn('job.kind == "loadout"', _without_comments(self.source))
        self.assertNotIn('job.kind = "loadout";', _without_comments(self.source))

    def test_each_row_error_and_an_occupied_slot_keeps_its_item(self) -> None:
        create = self._code(CREATE_FN)
        apply = self._code(APPLY_LOADOUT_FN)
        self.assertEqual(
            _errors(create),
            {"unknown_type", "slot_not_found", "slot_occupied", "cannot_attach", "no_room", "vehicle_eject_failed"},
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
                rollback = _if_body(create, postcondition)
                self.assertIn("if (!DeleteLoadoutRollback(item))", rollback)
                self.assertIn('error = "vehicle_eject_failed";', rollback)
        guarded_delete = self._code(ROLLBACK_FN)
        self.assertLess(guarded_delete.index("eject.Begin(m_Tick)"), guarded_delete.index("GetGame().ObjectDelete(item)"))
        self.assertIn("eject.HasOccupants()", guarded_delete)

    def test_every_row_gets_one_outcome_and_a_failed_parent_stops_its_children(self) -> None:
        apply = self._code(APPLY_LOADOUT_FN)
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
        # clear_first is the tool's: on the wire it is the loadout_clear verb.
        self.assertNotIn("clear_first", {name for _type, name in args})
        result = _class_members(self.messages, "MCPResult")
        self.assertEqual(result.count(("ref MCPLoadout", "loadout")), 1)
        self.assertEqual(result.count(("int", "deleted")), 1)
        self.assertEqual(result.count(("bool", "cleanup_complete")), 1)
        self.assertEqual(result.count(("string", "uid")), 1)
        job = _class_members(self.messages, "MCPJob")
        self.assertEqual(job.count(("ref array<EntityAI>", "loadout_cleared")), 1)
        self.assertEqual(job.count(("string", "loadout_uid")), 1)
        for name in ("class MCPLoadoutItem", "class MCPLoadoutApplied", "class MCPLoadout\n"):
            with self.subTest(name=name):
                self.assertLess(self.messages.index(name), self.messages.index("class MCPResult"))
        self.assertLess(self.messages.index("class MCPLoadoutItem"), self.messages.index("class MCPArgs"))


class LoadoutBridgeRunTest(unittest.TestCase):
    """The real Enforce bodies on fakes: branches and effects, not tokens."""

    def _dressed(self, bridge: _Bridge) -> tuple[_Player, list[_Entity]]:
        survivor = bridge.survivor(3)
        shirt = bridge.put(survivor, "attachment", "TShirt_White", "Body")
        bag = bridge.put(survivor, "attachment", "TaloonBag_Blue", "Back")
        bridge.put(bag, "cargo", "Apple")
        weapon = bridge.put(survivor, "hands", "M4A1")
        return survivor, [shirt, bag, weapon]

    def test_the_target_is_resolved_or_refused_before_anything_is_read(self) -> None:
        bridge = _Bridge()
        survivor = bridge.survivor(3)
        bridge.survivor(4, identity=object())
        bridge.registry[5] = _Entity(bridge.world, "Apple")

        def resolve(args: _Args | None) -> tuple[object, str]:
            target, error = bridge.call("ResolveLoadoutTarget", args)
            return target, error.text

        self.assertEqual(resolve(_Args(object_id=3)), (survivor, ""))
        # An id that names a connected player is bad_target: uid selects those (U5).
        self.assertEqual(resolve(_Args(object_id=4)), (None, "bad_target"))
        self.assertEqual(resolve(_Args(object_id=5)), (None, "bad_target"))
        self.assertEqual(resolve(_Args(object_id=9)), (None, "not_found"))
        self.assertEqual(resolve(_Args(object_id=3, uid="76561198000000000")), (None, "bad_args"))
        self.assertEqual(resolve(_Args(object_id=-1)), (None, "bad_args"))
        self.assertEqual(resolve(None), (None, "bad_args"))
        self.assertEqual(bridge.resolve_player_calls, [])
        self.assertEqual(bridge.world.changes(), (0, 0))

    def test_the_bridge_takes_what_only_the_ingress_refuses(self) -> None:
        # The other half of LoadoutIngressTest.test_only_the_ingress_refuses_object_id_0_and_names_with_spaces.
        bridge = _Bridge()
        bridge.player = _Player(bridge.world)
        for uid in ("", "76561198000000000"):
            with self.subTest(uid=uid):
                args = _Args(object_id=0, uid=uid)
                target, error = bridge.call("ResolveLoadoutTarget", args)
                self.assertIs(target, bridge.player)
                self.assertEqual(error.text, "")
                self.assertIs(bridge.resolve_player_calls[-1], args)
        body = {"type": "TShirt_White", "slot": "Body", "dest": "attachment", "parent": -1}
        for row in (dict(body, type="TShirt White"), dict(body, slot="Bo dy")):
            with self.subTest(row=row):
                self.assertEqual(bridge.call("LoadoutRowsError", _wire_rows([row])).text, "")
        # What both refuse: an empty type, or rows past the caps.
        self.assertEqual(
            bridge.call("LoadoutRowsError", _wire_rows([dict(body, type="")])).text, "bad_args"
        )
        self.assertEqual(
            bridge.call("LoadoutRowsError", _wire_rows([dict(body)] * 65)).text, "bad_args"
        )

    def test_the_clear_job_waits_while_any_deleted_object_is_still_there(self) -> None:
        bridge = _Bridge()
        still_there = _Entity(bridge.world, "TShirt_White")
        job = _Job()
        for references, done in (
            ([None, still_there, None], False),
            ([still_there], False),
            ([None, None, None], True),
        ):
            with self.subTest(references=references):
                job.loadout_cleared = _Array(references)
                self.assertIs(bridge.call("IsLoadoutClearDone", job), done)

    def test_the_clear_deletes_in_its_tick_and_its_job_changes_nothing_after(self) -> None:
        bridge = _Bridge()
        survivor, worn = self._dressed(bridge)
        bridge.namespace["m_ElapsedS"] = 2.0
        result = _Result()
        posted_now = bridge.call("DispatchLoadoutClear", _Command(CLEAR, _Args(object_id=3), 21), result)
        # The dispatch tick deletes what the target wears and holds (the apple
        # goes with its bag) and leaves the answer to the job.
        self.assertIs(posted_now, False)
        self.assertEqual(bridge.world.deleted, worn)
        self.assertEqual(bridge.world.created, [])
        job = bridge.jobs.jobs[21]
        self.assertEqual(job.kind.text, "loadout_clear")
        self.assertIs(job.subject, survivor)
        self.assertEqual(job.loadout_cleared.items, worn)
        self.assertEqual(job.deadline_s, 2.0 + bridge.job_timeout_s)
        self.assertIn("kind=loadout_clear deleted=3", bridge.logged[-1])
        after_dispatch = bridge.world.changes()

        # While the deleted objects are still there: no answer, and both verbs
        # are busy on this target.
        bridge.tick(3.0)
        self.assertEqual(bridge.posted, [])
        for verb, dispatch, args in (
            (APPLY, "DispatchLoadoutApply", _Args(object_id=3, rows=_rows(PRESET_8))),
            (CLEAR, "DispatchLoadoutClear", _Args(object_id=3)),
        ):
            with self.subTest(verb=verb):
                refused = _Result()
                self.assertIs(bridge.call(dispatch, _Command(verb, args, 22), refused), True)
                self.assertEqual((refused.ok, refused.error.text), (False, "busy"))
        self.assertEqual(bridge.world.changes(), after_dispatch)
        self.assertEqual(list(bridge.jobs.jobs), [21])

        # The engine removes them: the next pass posts what the clear saw.
        bridge.world.finish_deletes(job.loadout_cleared)
        bridge.tick(3.5)
        self.assertEqual(len(bridge.posted), 1)
        posted = bridge.posted[0]
        self.assertEqual(
            (posted.id, posted.ok, posted.deleted, posted.cleanup_complete), (21, True, 3, True)
        )
        self.assertIsNone(posted.loadout)
        self.assertEqual(bridge.jobs.Count(), 0)
        self.assertEqual(bridge.world.changes(), after_dispatch)

        # With the job gone, an apply runs in its own tick and queues nothing.
        applied = _Result()
        rows = _rows(PRESET_8)
        self.assertIs(
            bridge.call("DispatchLoadoutApply", _Command(APPLY, _Args(object_id=3, rows=rows), 23), applied),
            True,
        )
        self.assertEqual([outcome.ok for outcome in applied.loadout.applied.items], [True] * 8)
        self.assertEqual(_read_rows(applied.loadout), rows)
        self.assertEqual(bridge.jobs.Count(), 0)
        self.assertEqual(len(bridge.posted), 1)

    def test_a_clear_job_at_its_deadline_says_so_and_still_changes_nothing(self) -> None:
        bridge = _Bridge()
        _survivor, worn = self._dressed(bridge)
        bridge.namespace["m_ElapsedS"] = 2.0
        self.assertIs(
            bridge.call("DispatchLoadoutClear", _Command(CLEAR, _Args(object_id=3), 21), _Result()),
            False,
        )
        after_dispatch = bridge.world.changes()
        bridge.tick(2.0 + bridge.job_timeout_s)
        self.assertEqual(bridge.posted, [])
        # The deleted objects never read null; past the deadline the job reports.
        bridge.tick(2.25 + bridge.job_timeout_s)
        self.assertEqual(len(bridge.posted), 1)
        posted = bridge.posted[0]
        self.assertEqual(
            (posted.id, posted.ok, posted.deleted, posted.cleanup_complete),
            (21, True, len(worn), False),
        )
        self.assertIsNone(posted.loadout)
        self.assertEqual(posted.error.text, "")
        self.assertEqual(bridge.jobs.Count(), 0)
        self.assertEqual(bridge.world.changes(), after_dispatch)

    def test_objects_still_there_at_the_deadline_and_gone_by_the_next_tick_are_complete(self) -> None:
        # #261 review Y12: the runner checks readiness before the deadline, so
        # cleanup_complete true means "all gone at the job's last check", and
        # that check can come one tick after the 5 s.
        bridge = _Bridge()
        _survivor, worn = self._dressed(bridge)
        bridge.namespace["m_ElapsedS"] = 2.0
        bridge.call("DispatchLoadoutClear", _Command(CLEAR, _Args(object_id=3), 21), _Result())
        job = bridge.jobs.jobs[21]
        deadline = 2.0 + bridge.job_timeout_s
        self.assertEqual(job.deadline_s, deadline)
        bridge.tick(deadline)
        self.assertEqual(bridge.posted, [])
        self.assertEqual(job.loadout_cleared.items, worn)
        bridge.world.finish_deletes(job.loadout_cleared)
        bridge.tick(deadline + 0.25)
        self.assertEqual(len(bridge.posted), 1)
        posted = bridge.posted[0]
        self.assertEqual(
            (posted.id, posted.ok, posted.deleted, posted.cleanup_complete),
            (21, True, len(worn), True),
        )
        self.assertEqual(bridge.jobs.Count(), 0)

    def test_a_clear_with_nothing_to_delete_answers_at_once(self) -> None:
        bridge = _Bridge()
        bridge.survivor(3)
        result = _Result()
        self.assertIs(
            bridge.call("DispatchLoadoutClear", _Command(CLEAR, _Args(object_id=3)), result), True
        )
        self.assertEqual((result.ok, result.deleted, result.cleanup_complete), (True, 0, True))
        self.assertEqual(bridge.jobs.Count(), 0)
        self.assertEqual(bridge.world.changes(), (0, 0))

    def test_a_refused_target_clears_nothing(self) -> None:
        bridge = _Bridge()
        self._dressed(bridge)
        bridge.survivor(4, identity=object())
        for args, error in ((_Args(object_id=9), "not_found"), (_Args(object_id=4), "bad_target")):
            with self.subTest(error=error):
                result = _Result()
                self.assertIs(bridge.call("DispatchLoadoutClear", _Command(CLEAR, args), result), True)
                self.assertEqual((result.ok, result.error.text), (False, error))
        self.assertEqual(bridge.world.changes(), (0, 0))
        self.assertEqual(bridge.jobs.Count(), 0)

    def test_the_clear_answers_the_uid_of_the_player_it_acted_on(self) -> None:
        # #261 review Y11, with main's ResolvePlayer, GetFirstHuman and
        # FindHumanByUid: the first human, or the player uid names, and "" for
        # a survivor by object_id; at once and from the job alike.
        bridge = _Bridge(real_players=True)
        refused = _Result()
        self.assertIs(bridge.call("DispatchLoadoutClear", _Command(CLEAR, _Args()), refused), True)
        self.assertEqual((refused.ok, refused.error.text), (False, "no_players"))
        first = _Player(bridge.world, _Identity("76561198000000001"))
        second = _Player(bridge.world, _Identity("76561198000000002"))
        bridge.put(second, "attachment", "TShirt_White", "Body")
        bridge.game.players = [first, second]
        bridge.survivor(3)

        now = _Result()
        self.assertIs(bridge.call("DispatchLoadoutClear", _Command(CLEAR, _Args(), 31), now), True)
        self.assertEqual(
            (now.ok, now.deleted, now.cleanup_complete, now.uid.text),
            (True, 0, True, "76561198000000001"),
        )
        bridge.namespace["m_ElapsedS"] = 1.0
        self.assertIs(
            bridge.call(
                "DispatchLoadoutClear", _Command(CLEAR, _Args(uid="76561198000000002"), 32), _Result()
            ),
            False,
        )
        job = bridge.jobs.jobs[32]
        self.assertEqual(job.loadout_uid.text, "76561198000000002")
        bridge.world.finish_deletes(job.loadout_cleared)
        bridge.tick(1.25)
        posted = bridge.posted[-1]
        self.assertEqual(
            (posted.id, posted.deleted, posted.cleanup_complete, posted.uid.text),
            (32, 1, True, "76561198000000002"),
        )
        survivor = _Result()
        self.assertIs(
            bridge.call("DispatchLoadoutClear", _Command(CLEAR, _Args(object_id=3), 33), survivor), True
        )
        self.assertEqual((survivor.ok, survivor.uid.text), (True, ""))
        gone = _Result()
        self.assertIs(
            bridge.call("DispatchLoadoutClear", _Command(CLEAR, _Args(uid="76561198000000009")), gone),
            True,
        )
        self.assertEqual((gone.ok, gone.error.text), (False, "player_not_found"))
        self.assertEqual(len(bridge.world.deleted), 1)

    def test_a_first_human_with_no_identity_is_refused_before_the_clear(self) -> None:
        # No uid would pin the creates to that player (#261 review Y11).
        bridge = _Bridge(real_players=True)
        nameless = _Player(bridge.world)
        bridge.put(nameless, "attachment", "TShirt_White", "Body")
        bridge.game.players = [nameless, _Player(bridge.world, _Identity("76561198000000002"))]
        result = _Result()
        self.assertIs(bridge.call("DispatchLoadoutClear", _Command(CLEAR, _Args()), result), True)
        self.assertEqual((result.ok, result.error.text), (False, "player_not_found"))
        self.assertEqual(bridge.world.changes(), (0, 0))
        self.assertEqual(bridge.jobs.Count(), 0)

    def test_rows_five_levels_down_are_bad_args_and_four_are_created(self) -> None:
        # #261 review Y13: the bridge's own depth refusal, worn and in hands,
        # reached on its own and through the dispatch.
        bridge = _Bridge()
        bridge.survivor(3)
        worn = [{"type": "SmershVest", "slot": "Vest", "dest": "attachment", "parent": -1}]
        held = [{"type": "SmershBag", "slot": "", "dest": "hands", "parent": -1}]
        for chain in (worn, held):
            chain.extend(
                {"type": "SmershBag", "slot": "", "dest": "cargo", "parent": index}
                for index in range(4)
            )
        for label, rows in (("worn", worn), ("hands", held)):
            with self.subTest(label):
                self.assertEqual(bridge.call("LoadoutRowsError", _wire_rows(rows)).text, "bad_args")
                self.assertEqual(bridge.call("LoadoutRowsError", _wire_rows(rows[:4])).text, "")
                self.assertIs(loadout.is_wire_items(rows), False)
                self.assertIs(loadout.is_wire_items(rows[:4]), True)
        refused = _Result()
        self.assertIs(
            bridge.call("DispatchLoadoutApply", _Command(APPLY, _Args(object_id=3, rows=worn)), refused),
            True,
        )
        self.assertEqual((refused.ok, refused.error.text), (False, "bad_args"))
        self.assertEqual(bridge.world.changes(), (0, 0))
        applied = _Result()
        self.assertIs(
            bridge.call(
                "DispatchLoadoutApply", _Command(APPLY, _Args(object_id=3, rows=worn[:4])), applied
            ),
            True,
        )
        self.assertEqual([outcome.ok for outcome in applied.loadout.applied.items], [True] * 4)
        self.assertEqual(_read_rows(applied.loadout), worn[:4])

    def test_a_pending_job_on_the_target_makes_both_verbs_busy_and_nothing_else(self) -> None:
        # U14: the busy branch has to return, for the clear and for the apply.
        bridge = _Bridge()
        survivor, _worn = self._dressed(bridge)
        other = bridge.survivor(4)
        pending = _Job()
        pending.id = 40
        pending.kind = _s("loadout_clear")
        pending.subject = survivor
        pending.loadout_cleared = _Array([object()])
        pending.deadline_s = 99.0
        bridge.jobs.Insert(40, pending)
        for verb, dispatch, args in (
            (APPLY, "DispatchLoadoutApply", _Args(object_id=3, rows=_rows({"hands": {"type": "Apple"}}))),
            (CLEAR, "DispatchLoadoutClear", _Args(object_id=3)),
        ):
            with self.subTest(verb=verb):
                result = _Result()
                self.assertIs(bridge.call(dispatch, _Command(verb, args), result), True)
                self.assertEqual((result.ok, result.error.text), (False, "busy"))
                self.assertIsNone(result.loadout)
        self.assertEqual(bridge.world.changes(), (0, 0))
        self.assertEqual(list(bridge.jobs.jobs), [40])
        # A job on another target does not hold this one.
        applied = _Result()
        rows = _rows({"attachments": [{"type": "TShirt_White", "slot": "Body"}]})
        self.assertIs(
            bridge.call("DispatchLoadoutApply", _Command(APPLY, _Args(object_id=4, rows=rows)), applied),
            True,
        )
        self.assertTrue(applied.ok)
        self.assertIs(other.inventory.FindAttachment(_SLOT_IDS["Body"]), bridge.world.created[0])

    def test_rows_and_target_are_checked_before_the_apply_creates(self) -> None:
        bridge = _Bridge()
        bridge.survivor(3)
        bad_rows = [{"type": "Apple", "slot": "", "dest": "cargo", "parent": -1}]
        for args, error in (
            (_Args(object_id=3, rows=bad_rows), "bad_args"),
            (_Args(object_id=9, rows=_rows(PRESET_8)), "not_found"),
        ):
            with self.subTest(error=error):
                result = _Result()
                self.assertIs(bridge.call("DispatchLoadoutApply", _Command(APPLY, args), result), True)
                self.assertEqual((result.ok, result.error.text), (False, error))
        null_args = _Result()
        self.assertIs(bridge.call("DispatchLoadoutApply", _Command(APPLY, None), null_args), True)
        self.assertEqual(null_args.error.text, "bad_args")
        self.assertEqual(bridge.world.changes(), (0, 0))

    def test_two_containers_read_back_with_their_own_cargo(self) -> None:
        # #261 review Y9 at the bridge: ApplyLoadout, then ReadLoadout,
        # AppendLoadoutRow and LoadoutSlotName give each item its parent row.
        bridge = _Bridge()
        bridge.survivor(3)
        rows = _rows(TWO_CONTAINERS)
        result = _Result()
        self.assertIs(
            bridge.call("DispatchLoadoutApply", _Command(APPLY, _Args(object_id=3, rows=rows)), result),
            True,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.type.text, "SurvivorM_Mirek")
        self.assertEqual(result.object_id, 3)
        self.assertEqual(
            [(outcome.ok, outcome.error.text) for outcome in result.loadout.applied.items],
            [(True, "")] * len(rows),
        )
        read = _read_rows(result.loadout)
        self.assertEqual(read, rows)
        tree = loadout.nest_rows(read)
        assert tree is not None
        self.assertEqual(_lineage(tree), _lineage(TWO_CONTAINERS))

    def test_an_occupied_slot_or_hands_keeps_what_is_there_and_creates_nothing(self) -> None:
        bridge = _Bridge()
        survivor = bridge.survivor(3)
        shirt = bridge.put(survivor, "attachment", "TShirt_White", "Body")
        weapon = bridge.put(survivor, "hands", "M4A1")
        for row, error in (
            (_Row("TShirt_White", "Body", "attachment", -1), "slot_occupied"),
            (_Row("Apple", "", "hands", -1), "slot_occupied"),
            (_Row("NoSuchClass", "Legs", "attachment", -1), "unknown_type"),
            (_Row("Jeans_Blue", "Hips", "attachment", -1), "slot_not_found"),
            (_Row("Jeans_Blue", "magazine", "attachment", -1), "slot_not_found"),
            (_Row("Apple", "", "cargo", 0), "no_room"),
        ):
            with self.subTest(row=row.type.text, slot=row.slot.text, dest=row.dest.text):
                item, out_error = bridge.call("CreateLoadoutItem", survivor, survivor, row)
                self.assertIsNone(item)
                self.assertEqual(out_error.text, error)
        self.assertEqual(bridge.world.changes(), (0, 0))
        self.assertIs(survivor.inventory.FindAttachment(_SLOT_IDS["Body"]), shirt)
        self.assertIs(survivor.hands, weapon)

    def test_an_item_made_elsewhere_than_asked_is_deleted_again(self) -> None:
        # U13: each postcondition has to delete what it caught.
        for dest, error in (("cargo", "no_room"), ("attachment", "cannot_attach"), ("hands", "cannot_attach")):
            with self.subTest(dest=dest):
                bridge = _Bridge()
                survivor = bridge.survivor(3)
                bag = bridge.put(survivor, "attachment", "TaloonBag_Blue", "Back")
                owner: _Entity = bag if dest == "cargo" else survivor
                row = {
                    "cargo": _Row("Apple", "", "cargo", 0),
                    "attachment": _Row("TShirt_White", "Body", "attachment", -1),
                    "hands": _Row("Apple", "", "hands", -1),
                }[dest]
                bridge.world.misplace = True
                item, out_error = bridge.call("CreateLoadoutItem", survivor, owner, row)
                self.assertIsNone(item)
                self.assertEqual(out_error.text, error)
                self.assertEqual(len(bridge.world.created), 1)
                self.assertEqual(bridge.world.deleted, bridge.world.created)

    def test_a_failed_parent_stops_its_children_and_every_row_answers(self) -> None:
        bridge = _Bridge()
        bridge.survivor(3)
        tree = json.loads(json.dumps(PRESET_8))
        tree["attachments"][3]["slot"] = "Hips"
        result = _Result()
        bridge.call(
            "DispatchLoadoutApply", _Command(APPLY, _Args(object_id=3, rows=_rows(tree))), result
        )
        self.assertEqual(
            [(outcome.ok, outcome.error.text) for outcome in result.loadout.applied.items],
            [(True, "")] * 3
            + [(False, "slot_not_found"), (False, "parent_failed"), (False, "parent_failed")]
            + [(True, "")] * 2,
        )
        self.assertEqual(
            Counter(item.type.text for item in bridge.world.created),
            Counter(["TShirt_White", "Jeans_Blue", "AthleticShoes_Black", "M4A1", "Mag_STANAG_30Rnd"]),
        )

    def test_a_new_mcploadout_starts_with_empty_lists(self) -> None:
        # U6: the constructor has to give items and applied an array.
        made = _Bridge().call("NewMCPLoadout")
        for member in ("items", "applied"):
            with self.subTest(member=member):
                self.assertIsInstance(getattr(made, member), _Array)
                self.assertEqual(getattr(made, member).Count(), 0)
        self.assertIs(made.too_large, False)

    def test_a_read_answers_rows_and_empty_lists_never_null(self) -> None:
        bridge = _Bridge()
        bare = bridge.survivor(4)
        result = _Result()
        self.assertIs(bridge.call("DispatchLoadoutGet", _Command(GET, _Args(object_id=4)), result), True)
        self.assertTrue(result.ok)
        self.assertEqual((result.loadout.items.Count(), result.loadout.applied.Count()), (0, 0))
        self.assertEqual(result.type.text, bare.type.text)

        self._dressed(bridge)
        dressed = _Result()
        self.assertIs(bridge.call("DispatchLoadoutGet", _Command(GET, _Args(object_id=3)), dressed), True)
        self.assertEqual(
            _read_rows(dressed.loadout),
            [
                {"type": "TShirt_White", "slot": "Body", "dest": "attachment", "parent": -1},
                {"type": "TaloonBag_Blue", "slot": "Back", "dest": "attachment", "parent": -1},
                {"type": "Apple", "slot": "", "dest": "cargo", "parent": 1},
                {"type": "M4A1", "slot": "", "dest": "hands", "parent": -1},
            ],
        )
        self.assertEqual(dressed.loadout.applied.Count(), 0)
        self.assertEqual(bridge.world.changes(), (0, 0))

    def test_a_read_past_the_caps_is_loadout_too_large_with_no_read(self) -> None:
        # Five levels down, or 65 items (a bag and 64 apples); a bag and 63
        # apples, 64 items, still read.
        for cap, apples in (("depth", 0), ("items", 64), ("at_the_item_cap", 63)):
            with self.subTest(cap=cap):
                bridge = _Bridge()
                survivor = bridge.survivor(3)
                if cap == "depth":
                    holder = bridge.put(survivor, "attachment", "SmershVest", "Vest")
                    for _ in range(3):
                        holder = bridge.put(holder, "cargo", "SmershBag")
                    bridge.put(holder, "cargo", "Rag")
                else:
                    bag = bridge.put(survivor, "attachment", "TaloonBag_Blue", "Back")
                    for _ in range(apples):
                        bridge.put(bag, "cargo", "Apple")
                result = _Result()
                self.assertIs(
                    bridge.call("DispatchLoadoutGet", _Command(GET, _Args(object_id=3)), result), True
                )
                if cap == "at_the_item_cap":
                    self.assertIs(result.ok, True)
                    self.assertEqual(len(_read_rows(result.loadout)), 64)
                    continue
                self.assertEqual((result.ok, result.error.text), (False, "loadout_too_large"))
                self.assertIsNone(result.loadout)


def _wire_answer(result: _Result) -> dict[str, Any]:
    """The JSON a translated dispatch's MCPResult goes out as: bools as 0/1."""
    answer: dict[str, Any] = {
        "id": result.id,
        "ok": int(result.ok),
        "error": result.error.text,
        "type": result.type.text,
        "object_id": result.object_id,
        "deleted": result.deleted,
        "cleanup_complete": int(result.cleanup_complete),
        "uid": result.uid.text,
        "loadout": {},
    }
    if result.loadout is not None:
        answer["loadout"] = {
            "items": _read_rows(result.loadout),
            "applied": [
                {"ok": int(outcome.ok), "error": outcome.error.text}
                for outcome in result.loadout.applied.items
            ],
            "too_large": int(result.loadout.too_large),
        }
    return answer


class LoadoutClearFirstTargetTest(unittest.IsolatedAsyncioTestCase):
    """B2: clear_first with an explicit uid, from the tool down to the
    translated dispatches and main's player resolvers.

    Player A is named by uid and wears a shirt; B is connected too. Between
    the clear and the creates, A leaves, or B becomes the first human. The
    creates go to A or are refused; they never dress B. With neither target
    the tool refuses before anything is sent."""

    A = "76561198000000001"
    B = "76561198000000002"
    TREE: dict[str, Any] = {
        "attachments": [{"type": "Jeans_Blue", "slot": "Legs"}],
        "hands": {"type": "Apple"},
    }

    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        self.bridge = _Bridge(real_players=True)
        self.a = _Player(self.bridge.world, _Identity(self.A))
        self.b = _Player(self.bridge.world, _Identity(self.B))
        self.shirt = self.bridge.put(self.a, "attachment", "TShirt_White", "Body")
        self.bridge.game.players = [self.a, self.b]
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.between = lambda: None

    async def _call_bridge(
        self, command: str, args: dict[str, Any], peer: str, timeout_s: float
    ) -> dict[str, Any]:
        """The game for the tool: the dispatch, its job if it queued one, and
        the answer as Runtime.wait_for_result hands it on."""
        self.sent.append((command, copy.deepcopy(args)))
        command_id = 40 + len(self.sent)
        wire_args = _Args(
            object_id=args.get("object_id", 0),
            uid=args.get("uid", ""),
            rows=args.get("loadout_items"),
        )
        dispatch = {CLEAR: "DispatchLoadoutClear", APPLY: "DispatchLoadoutApply"}[command]
        result = _Result()
        result.id = command_id
        if not self.bridge.call(dispatch, _Command(command, wire_args, command_id), result):
            job = self.bridge.jobs.jobs[command_id]
            self.bridge.world.finish_deletes(job.loadout_cleared)
            self.bridge.tick(self.bridge.namespace["m_ElapsedS"] + 0.25)
            result = self.bridge.posted.pop()
        if command == CLEAR:
            self.between()
        answer = _wire_answer(result)
        if not answer["ok"]:
            raise server._bridge_error(answer, command)
        return result_prune.prune_unfilled_fields(command, answer)

    def _b_untouched(self) -> None:
        self.assertEqual(self.b.inventory.attachments, [])
        self.assertIsNone(self.b.hands)

    async def test_a_first_human_who_left_after_the_clear_is_refused_and_no_one_is_dressed(self) -> None:
        self.between = lambda: self.bridge.game.players.remove(self.a)
        with patch.object(self.runtime, "call_bridge", new=self._call_bridge):
            with self.assertRaises(ToolError) as raised:
                await self.app.call_tool(APPLY, {"loadout": self.TREE, "clear_first": True, "uid": self.A})
        self.assertTrue(
            str(raised.exception).endswith(
                "player_not_found; clear=deleted=1 cleanup_complete=1"
            ),
            str(raised.exception),
        )
        self.assertEqual(
            self.sent,
            [(CLEAR, {"uid": self.A}), (APPLY, {"uid": self.A, "loadout_items": _rows(self.TREE)})],
        )
        self.assertEqual(self.bridge.world.deleted, [self.shirt])
        self.assertEqual(self.bridge.world.created, [])
        self._b_untouched()

    async def test_a_first_human_who_is_second_by_the_creates_is_still_the_one_dressed(self) -> None:
        self.between = lambda: self.bridge.game.players.reverse()
        with patch.object(self.runtime, "call_bridge", new=self._call_bridge):
            result = _content_json(
                await self.app.call_tool(APPLY, {"loadout": self.TREE, "clear_first": True, "uid": self.A})
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["clear"], {"deleted": 1, "cleanup_complete": True})
        self.assertEqual(_classes(result["loadout"]), _classes(self.TREE))
        self.assertEqual(self.sent[0], (CLEAR, {"uid": self.A}))
        self.assertEqual(self.sent[1], (APPLY, {"uid": self.A, "loadout_items": _rows(self.TREE)}))
        self.assertEqual(
            [item.type.text for _slot, item in self.a.inventory.attachments], ["Jeans_Blue"]
        )
        self.assertEqual(self.a.hands.type.text, "Apple")
        self._b_untouched()

    async def test_clear_first_with_neither_target_is_refused_and_sends_nothing(self) -> None:
        with patch.object(self.runtime, "call_bridge", new=self._call_bridge):
            with self.assertRaises(ToolError) as raised:
                await self.app.call_tool(APPLY, {"loadout": self.TREE, "clear_first": True})
        self.assertIn("bad_args", str(raised.exception))
        self.assertIn("uid", str(raised.exception))
        self.assertEqual(self.sent, [])
        self.assertEqual(self.bridge.world.deleted, [])
        self.assertEqual(self.bridge.world.created, [])
        self._b_untouched()


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

    async def _ready(self) -> None:
        deadline = time.monotonic() + 2.0
        while not server.compute_bridge_ready(self.runtime.status())["ready"]:
            if time.monotonic() > deadline:
                self.fail("bridge never became ready")
            await asyncio.sleep(0.02)

    # The first human's uid in these games.
    FIRST_HUMAN = "76561198000000001"

    @classmethod
    def _answer(cls, command: dict[str, Any]) -> dict[str, Any]:
        # The flat MCPResult: the verb's own members plus the others' defaults.
        result: dict[str, Any] = {
            "id": command["id"],
            "ok": 1,
            "cmd": command["cmd"],
            "deleted": 0,
            "cleanup_complete": 0,
            "uid": "",
            "loadout": {},
            "applied": {},
            "world_time": {},
        }
        if command["cmd"] == CLEAR:
            result["deleted"] = 3
            result["cleanup_complete"] = 1
            # The player it acted on: the uid asked for, else the first human.
            if "object_id" not in command["args"]:
                result["uid"] = command["args"].get("uid") or cls.FIRST_HUMAN
        if command["cmd"] == APPLY:
            rows = command["args"]["loadout_items"]
            result["type"] = "SurvivorM_Mirek"
            result["loadout"] = {
                "items": rows,
                "applied": [{"ok": 1, "error": ""} for _ in rows],
                "too_large": 0,
            }
        return result

    @slow_test
    async def test_an_apply_arrives_shaped_with_the_other_refs_pruned(self) -> None:
        server_peer = self._peer("server", self._answer)
        self._peer("client")
        await self._ready()
        result = _content_json(
            await self.app.call_tool(APPLY, {"loadout": PRESET_8, "timeout_s": 2.0, "uid": "76561198000000000"})
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["items"]), 8)
        self.assertEqual(_classes(result["loadout"]), _classes(PRESET_8))
        self.assertIsNone(result["clear"])
        for pruned in ("applied", "world_time", "deleted", "cleanup_complete", "uid"):
            with self.subTest(pruned=pruned):
                self.assertNotIn(pruned, result)
        sent = [command for command in server_peer.commands_seen if command["cmd"] in (CLEAR, APPLY)]
        self.assertEqual([command["cmd"] for command in sent], [APPLY])
        self.assertEqual(sent[0]["args"], {"uid": "76561198000000000", "loadout_items": _rows(PRESET_8)})

    @slow_test
    async def test_clear_first_goes_out_as_the_clear_then_the_rows(self) -> None:
        server_peer = self._peer("server", self._answer)
        self._peer("client")
        await self._ready()
        result = _content_json(
            await self.app.call_tool(
                APPLY, {"loadout": PRESET_8, "clear_first": True, "object_id": 6, "timeout_s": 2.0}
            )
        )
        sent = [command for command in server_peer.commands_seen if command["cmd"] in (CLEAR, APPLY)]
        self.assertEqual([command["cmd"] for command in sent], [CLEAR, APPLY])
        self.assertEqual(sent[0]["args"], {"object_id": 6})
        self.assertEqual(sent[1]["args"], {"object_id": 6, "loadout_items": _rows(PRESET_8)})
        self.assertEqual(result["clear"], {"deleted": 3, "cleanup_complete": True})
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("deleted", result)
        self.assertNotIn("cleanup_complete", result)

    @slow_test
    async def test_clear_first_with_neither_target_is_refused_and_sends_nothing(self) -> None:
        # B2 through the ingress: neither target is refused before the
        # bridge, so no CLEAR or APPLY is sent.
        server_peer = self._peer("server", self._answer)
        self._peer("client")
        await self._ready()
        with self.assertRaises(Exception) as raised:
            await self.app.call_tool(
                APPLY, {"loadout": PRESET_8, "clear_first": True, "timeout_s": 2.0}
            )
        self.assertIn("bad_args", str(raised.exception))
        self.assertIn("uid", str(raised.exception))
        sent = [command for command in server_peer.commands_seen if command["cmd"] in (CLEAR, APPLY)]
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
