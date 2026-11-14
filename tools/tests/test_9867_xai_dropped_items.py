"""9867: releasing an Expansion AI group also deletes its dropped items.

Offline proof by translating the real adapter methods
(AIIsMemberEntity, AILastHeldByGroup, AIDroppedFromGroup,
AISweepDroppedItems, AIGroupRequestRelease, DispatchAIGroupRelease,
LeaseCloseExtensionStop) with tests/enforce_subset_helpers.py and
running them on fakes, plus source pins where translation cannot
reach. No Expansion or vanilla scripts are needed: the fakes reproduce
only the cited surface (m_Expansion_PreviousOwner,
m_Expansion_IsOwnerPlayer, hierarchy, inventory LEVELORDER, juncture,
GetObjectsAtPosition3D, GetPlayers, ObjectDelete, m_LeaseCloseDeletion).
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import (
    EnforceArray,
    EnforceMap,
    EnforceString,
    EnforceVector,
    class_members,
    method_body,
    translate,
)
from tests.xai_helpers import _WireString

REPO_ROOT = addon_root().parent
ADAPTER = REPO_ROOT / "addon_expansion_ai" / "scripts" / "5_Mission" / "MCPExpansionAI.c"
GUIDE = REPO_ROOT / "docs" / "expansion-ai-adapter.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
TOKEN = "a" * 32
GROUP_ID = 123


class _LogString(_WireString):
    """_WireString plus the string + int concatenation Enforce gives."""

    __slots__ = ()

    def __add__(self, other: object) -> "_LogString":
        if isinstance(other, bool):
            return NotImplemented
        if isinstance(other, int):
            return _LogString(self.data + str(other).encode("ascii"))
        if isinstance(other, EnforceString):
            return _LogString(self.data + other.data)
        return NotImplemented


class _Entity:
    def __init__(self) -> None:
        self.parent: object = None
        self.children: list = []

    def GetHierarchyParent(self):
        return self.parent


def _attach(parent, child) -> None:
    parent.children.append(child)
    child.parent = parent


class _FakeCargo:
    pass


class _FakeInventory:
    """LEVELORDER enumeration: the holder, then breadth-first contents."""

    def __init__(self, holder) -> None:
        self._holder = holder

    def GetCargo(self):
        return getattr(self._holder, "cargo", None)

    def EnumerateInventory(self, traversal, out) -> None:
        del out[:]
        queue = [self._holder]
        while queue:
            node = queue.pop(0)
            out.Insert(node)
            queue.extend(node.children)


class _FakeItem(_Entity):
    def __init__(self, previous_owner=None, *, cargo=None, set_for_deletion: bool = False, is_owner_player: bool = False) -> None:
        super().__init__()
        self.m_Expansion_PreviousOwner = previous_owner
        self.m_Expansion_IsOwnerPlayer = is_owner_player
        self.cargo = cargo
        self._set_for_deletion = set_for_deletion

    def GetInventory(self) -> _FakeInventory:
        return _FakeInventory(self)

    def IsSetForDeletion(self) -> bool:
        return self._set_for_deletion


class _FakeMemberEnt(_Entity):
    """An eAIBase member (also used as the eAIBase skipped by the players scan)."""

    def __init__(self, name: str, pos, calls: list) -> None:
        super().__init__()
        self.name = name
        self._pos = EnforceVector(pos)
        self._calls = calls
        self.cargo = None
        self.despawns = 0

    def GetPosition(self) -> EnforceVector:
        return self._pos

    def GetInventory(self) -> _FakeInventory:
        return _FakeInventory(self)

    def GetEmoteManager(self):
        return None

    def eAI_Despawn(self) -> None:
        self.despawns += 1
        self._calls.append(f"despawn:{self.name}")


class _FakePlayer(_Entity):
    def __init__(self) -> None:
        super().__init__()
        self.cargo = None

    def GetInventory(self) -> _FakeInventory:
        return _FakeInventory(self)


class _FakeGroup:
    def __init__(self, calls: list) -> None:
        self._calls = calls
        self.deletes = 0

    def Delete(self) -> None:
        self.deletes += 1
        self._calls.append("group_delete")


class _FakeGame:
    def __init__(self, calls: list) -> None:
        self._calls = calls
        self.world: dict = {}
        self.players: list = []
        self.juncture: set = set()
        self.deleted: list = []
        self.queries: list = []

    def GetCallQueue(self, category):
        return None

    def GetObjectsAtPosition3D(self, pos, radius, objects, cargos) -> None:
        self.queries.append((tuple(pos), radius))
        for obj in self.world.get(tuple(pos), []):
            objects.Insert(obj)

    def GetPlayers(self, players) -> None:
        for player in self.players:
            players.Insert(player)

    def HasInventoryJunctureItem(self, entity) -> bool:
        return entity in self.juncture

    def ObjectDelete(self, obj) -> None:
        self.deleted.append(obj)
        self._calls.append("delete")


def _namespace(calls: list, game: _FakeGame) -> dict:
    logs: list[str] = []

    def log(message) -> None:
        text = message.text if isinstance(message, EnforceString) else str(message)
        logs.append(text)
        calls.append("log")

    namespace: dict = {
        "_S": _LogString,
        "array": EnforceArray,
        "m_AIGroups": EnforceArray(),
        "m_RuntimeObjects": EnforceMap(),
        "m_LeaseCloseDeletion": EnforceArray(),
        "AI_DROPPED_RADIUS_M": 30.0,
        "GetGame": lambda: game,
        "Log": log,
        "_logs": logs,
        "ItemBase": SimpleNamespace(
            Cast=lambda obj: obj if isinstance(obj, _FakeItem) else None
        ),
        "eAIBase": SimpleNamespace(
            Cast=lambda obj: obj if isinstance(obj, _FakeMemberEnt) else None
        ),
        "eAIGroup": SimpleNamespace(GetGroupByID=lambda group_id, create: None),
        "InventoryTraversalType": SimpleNamespace(LEVELORDER=0),
        "MCPBotControl": SimpleNamespace(OnObjectGone=lambda bridge, object_id: None),
        "MCPLeaseCloseEntry": lambda: SimpleNamespace(name=None, state=None, reason=None),
        "MCPAIGroup": lambda: SimpleNamespace(
            release=_LogString.of(""), confirmed=False, released=-1,
            remaining_ids=EnforceArray(), dropped_deleted=0, dropped_kept=0,
        ),
        "CALL_CATEGORY_SYSTEM": 0,
        "this": object(),
    }
    source = ADAPTER.read_text(encoding="utf-8")
    class_types = (
        "MCPAIGroupRecord", "MCPAIGroupMemberRecord", "MCPAIGroup", "eAIGroup",
        "Object", "EmoteManager", "ScriptCallQueue", "Man", "EntityAI",
        "ItemBase", "GameInventory", "CargoBase", "array", "MCPCommand", "MCPResult",
    )
    for signature, members in (
        ("protected void EnsureAIRegistry()", ("m_AIGroups",)),
        ("protected void AIClearEmoteAutoCancel(EmoteManager emotes)", ()),
        ("protected bool AIGroupGone(MCPAIGroupRecord record)", ()),
        ("protected bool AIGroupOriginalPresent(MCPAIGroupRecord record)", ()),
        ("protected bool AIIsMemberEntity(MCPAIGroupRecord record, Man owner)", ()),
        ("protected bool AILastHeldByGroup(MCPAIGroupRecord record, EntityAI entity)", ()),
        ("protected bool AIDroppedFromGroup(MCPAIGroupRecord record, EntityAI entity)", ()),
        ("protected void AISweepDroppedItems(MCPAIGroupRecord record)", ("m_LeaseCloseDeletion",)),
        ("protected bool AIGroupRequestRelease(MCPAIGroupRecord record)", ("m_RuntimeObjects",)),
        ("protected MCPAIGroupRecord FindAIGroupByToken(string spawnToken)", ("m_AIGroups",)),
        ("protected void FillRemainingIds(MCPAIGroupRecord record, MCPAIGroup report)", ()),
        ("protected void DispatchAIGroupRelease(MCPCommand command, MCPResult result)", ("m_AIGroups", "m_RuntimeObjects")),
        ("protected override void LeaseCloseExtensionStop()", ("m_AIGroups",)),
    ):
        translate(source, signature, namespace, members=members, class_types=class_types)
    return namespace


def _member_record(object_id: int, member) -> SimpleNamespace:
    return SimpleNamespace(object_id=object_id, member=member)


def _record(members: list, *, group=None, group_state: str = "active") -> SimpleNamespace:
    return SimpleNamespace(
        spawn_token=_LogString.of(TOKEN),
        group_id=GROUP_ID,
        group=group,
        expansion_id=7,
        group_state=_LogString.of(group_state),
        dropped_deleted=0,
        dropped_kept=0,
        members=EnforceArray(members),
    )


class ProvenanceTest(unittest.TestCase):
    """Brief a: AIIsMemberEntity / AIDroppedFromGroup."""

    def test_a_null_and_player_owners_never_match_and_members_do(self) -> None:
        calls: list = []
        namespace = _namespace(calls, _FakeGame(calls))
        member_a = _FakeMemberEnt("A", (0.0, 0.0, 0.0), calls)
        member_b = _FakeMemberEnt("B", (100.0, 0.0, 0.0), calls)
        player = _FakePlayer()
        record = _record([
            _member_record(11, member_a),
            _member_record(12, None),
            _member_record(13, member_b),
        ])
        is_member = namespace["AIIsMemberEntity"]
        last_held = namespace["AILastHeldByGroup"]
        dropped = namespace["AIDroppedFromGroup"]
        # A null owner never matches, also when a member slot is null.
        self.assertFalse(is_member(record, None))
        self.assertFalse(is_member(record, player))
        self.assertTrue(is_member(record, member_a))
        self.assertTrue(is_member(record, member_b))
        self.assertFalse(dropped(record, None))
        self.assertFalse(last_held(record, None))
        self.assertFalse(dropped(record, _FakeItem(None)))
        self.assertFalse(last_held(record, _FakeItem(None)))
        self.assertFalse(dropped(record, _FakeItem(player)))
        self.assertFalse(last_held(record, _FakeItem(player)))
        self.assertTrue(dropped(record, _FakeItem(member_b)))
        self.assertTrue(last_held(record, _FakeItem(member_b)))
        # AMEND-2 test a: the sticky player flag blocks deletion only.
        self.assertFalse(dropped(record, _FakeItem(member_b, is_owner_player=True)))
        self.assertTrue(last_held(record, _FakeItem(member_b, is_owner_player=True)))
        # A non-ItemBase entity does not pass.
        self.assertFalse(dropped(record, member_a))
        self.assertFalse(dropped(record, _Entity()))


def _sweep_fixture():
    calls: list = []
    game = _FakeGame(calls)
    namespace = _namespace(calls, game)
    member_a = _FakeMemberEnt("A", (0.0, 0.0, 0.0), calls)
    member_b = _FakeMemberEnt("B", (100.0, 0.0, 0.0), calls)
    player = _FakePlayer()

    weapon_b = _FakeItem(member_b)
    _attach(weapon_b, _FakeItem(member_b))
    player_item = _FakeItem(player, is_owner_player=True)
    null_item = _FakeItem(None)
    kept_contents = _FakeItem(member_a)
    _attach(kept_contents, _FakeItem(player, is_owner_player=True))
    kept_juncture = _FakeItem(member_a)
    game.juncture.add(kept_juncture)
    kept_inner_juncture = _FakeItem(member_b)
    inner_mag = _FakeItem(member_b)
    _attach(kept_inner_juncture, inner_mag)
    game.juncture.add(inner_mag)
    set_for_deletion = _FakeItem(member_a, set_for_deletion=True)
    parented = _FakeItem(member_a)
    parented.parent = member_a
    container_b = _FakeItem(member_b, cargo=_FakeCargo())
    laundered_pistol = _FakeItem(member_b, is_owner_player=True)
    kept_flagged_attachment = _FakeItem(member_a)
    _attach(kept_flagged_attachment, _FakeItem(member_a, is_owner_player=True))
    non_item = _Entity()

    game.world[(0.0, 0.0, 0.0)] = [
        weapon_b, player_item, null_item, kept_contents, kept_juncture,
        set_for_deletion, parented, non_item,
    ]
    game.world[(100.0, 0.0, 0.0)] = [weapon_b, kept_inner_juncture, container_b, laundered_pistol, kept_flagged_attachment]

    held_weapon = _FakeItem(member_b, is_owner_player=True)
    _attach(held_weapon, _FakeItem(member_b, is_owner_player=True))
    _attach(player, held_weapon)
    _attach(player, _FakeItem(member_a, is_owner_player=True))
    _attach(player, _FakeItem(player, is_owner_player=True))
    backpack = _FakeItem(member_a, is_owner_player=True)
    case = _FakeItem(player, is_owner_player=True)
    nested_bandage = _FakeItem(member_a, is_owner_player=True)
    _attach(backpack, case)
    _attach(case, nested_bandage)
    _attach(player, backpack)
    game.players = [player, member_a]

    record = _record([
        _member_record(11, member_a),
        _member_record(12, member_b),
        _member_record(13, None),
    ])
    items = SimpleNamespace(
        weapon_b=weapon_b, player_item=player_item, null_item=null_item,
        container_b=container_b,
    )
    return namespace, game, calls, record, items


class SweepTest(unittest.TestCase):
    """Brief b (with AMEND-1 case 5 and AMEND-2 case 5): one sweep."""

    def test_b_sweep_deletes_only_the_groups_dropped_items(self) -> None:
        namespace, game, calls, record, items = _sweep_fixture()
        namespace["AISweepDroppedItems"](record)
        self.assertEqual(record.dropped_deleted, 1)
        self.assertEqual(record.dropped_kept, 8)
        self.assertEqual(game.deleted, [items.weapon_b])
        self.assertEqual(list(namespace["m_LeaseCloseDeletion"]), [items.weapon_b])
        logs = namespace["_logs"]
        self.assertEqual(len(logs), 1)
        self.assertIn("group_id=123", logs[0])
        self.assertIn("dropped_deleted=1", logs[0])
        self.assertIn("dropped_kept=8", logs[0])
        self.assertNotIn(TOKEN, logs[0])
        self.assertEqual(len(game.queries), 2)
        for _pos, radius in game.queries:
            self.assertEqual(radius, 30.0)


class KnownLimitCloneTest(unittest.TestCase):
    """AMEND-3: the player flag is not copied into clone sub-items."""

    def test_known_limit_flag_is_not_copied_into_clone_sub_items(self) -> None:
        # Expansion copies m_Expansion_IsOwnerPlayer only to the root of a
        # clone (eAIBase.c:6030-6036); the recursion that clones attachments
        # and cargo (ExpansionItemSpawnHelper.c:643-676) copies the previous
        # owner (:715-722) but never that flag.
        calls: list = []
        game = _FakeGame(calls)
        namespace = _namespace(calls, game)
        member = _FakeMemberEnt("A", (0.0, 0.0, 0.0), calls)
        rifle = _FakeItem(member)
        _attach(rifle, _FakeItem(member, is_owner_player=True))
        game.world[(0.0, 0.0, 0.0)] = [rifle]
        record = _record([_member_record(11, member)])
        namespace["AISweepDroppedItems"](record)
        self.assertEqual(record.dropped_deleted, 0)
        self.assertEqual(record.dropped_kept, 1)
        self.assertEqual(game.deleted, [])

        calls2: list = []
        game2 = _FakeGame(calls2)
        namespace2 = _namespace(calls2, game2)
        member2 = _FakeMemberEnt("A", (0.0, 0.0, 0.0), calls2)
        clone_rifle = _FakeItem(member2)
        _attach(clone_rifle, _FakeItem(member2))
        game2.world[(0.0, 0.0, 0.0)] = [clone_rifle]
        record2 = _record([_member_record(11, member2)])
        namespace2["AISweepDroppedItems"](record2)
        self.assertEqual(record2.dropped_deleted, 1)
        self.assertEqual(record2.dropped_kept, 0)
        self.assertEqual(game2.deleted, [clone_rifle])


class ReleaseSweepTest(unittest.TestCase):
    """Brief c: the sweep runs once, first, on the active -> releasing change."""

    def _active_record(self, calls, game, name: str = "A"):
        member = _FakeMemberEnt(name, (0.0, 0.0, 0.0), calls)
        item = _FakeItem(member)
        game.world[(0.0, 0.0, 0.0)] = game.world.get((0.0, 0.0, 0.0), []) + [item]
        record = _record([_member_record(11, member)], group=_FakeGroup(calls))
        return record, member, item

    def test_c_release_sweeps_once_before_the_first_despawn(self) -> None:
        calls: list = []
        game = _FakeGame(calls)
        namespace = _namespace(calls, game)
        record, member, _item = self._active_record(calls, game)
        namespace["AIGroupRequestRelease"](record)
        self.assertEqual((record.dropped_deleted, record.dropped_kept), (1, 0))
        self.assertLess(calls.index("delete"), calls.index("despawn:A"))
        self.assertLess(calls.index("log"), calls.index("despawn:A"))
        calls_after_first = list(calls)
        namespace["AIGroupRequestRelease"](record)
        self.assertEqual(calls, calls_after_first)
        self.assertEqual((record.dropped_deleted, record.dropped_kept), (1, 0))
        self.assertEqual((member.despawns, len(game.deleted), len(namespace["_logs"])), (1, 1, 1))

    def test_c_a_releasing_record_is_never_swept(self) -> None:
        calls: list = []
        game = _FakeGame(calls)
        namespace = _namespace(calls, game)
        record, member, _item = self._active_record(calls, game)
        record.group_state = _LogString.of("releasing")
        namespace["AIGroupRequestRelease"](record)
        self.assertEqual(calls, [])
        self.assertEqual((member.despawns, len(game.deleted), len(namespace["_logs"])), (0, 0, 0))
        self.assertEqual((record.dropped_deleted, record.dropped_kept), (0, 0))

    def test_c_lease_close_stop_sweeps_each_active_group_once(self) -> None:
        calls: list = []
        game = _FakeGame(calls)
        namespace = _namespace(calls, game)
        first, first_member, _i1 = self._active_record(calls, game, "A")
        second_member = _FakeMemberEnt("B", (0.0, 0.0, 0.0), calls)
        game.world[(0.0, 0.0, 0.0)] = game.world.get((0.0, 0.0, 0.0), []) + [_FakeItem(second_member)]
        second = _record([_member_record(12, second_member)], group=_FakeGroup(calls))
        namespace["m_AIGroups"] = EnforceArray([first, second])
        namespace["LeaseCloseExtensionStop"]()
        namespace["LeaseCloseExtensionStop"]()
        self.assertEqual((first_member.despawns, second_member.despawns), (1, 1))
        self.assertEqual(len(namespace["_logs"]), 2)
        self.assertEqual(first.dropped_deleted, 1)
        self.assertEqual(second.dropped_deleted, 1)


class ReleaseReplyTest(unittest.TestCase):
    """Brief d: DispatchAIGroupRelease carries the stored counts."""

    def _dispatch(self, namespace, token: str):
        result = SimpleNamespace(ai_group=None, ok=False, error=_LogString.of(""))
        command = SimpleNamespace(args=SimpleNamespace(spawn_token=_LogString.of(token)))
        namespace["DispatchAIGroupRelease"](command, result)
        return result

    def test_d_pending_and_released_carry_counts_and_unknown_is_zero(self) -> None:
        calls: list = []
        game = _FakeGame(calls)
        namespace = _namespace(calls, game)
        member = _FakeMemberEnt("A", (0.0, 0.0, 0.0), calls)
        game.world[(0.0, 0.0, 0.0)] = [_FakeItem(member)]
        record = _record([_member_record(11, member)], group=_FakeGroup(calls))
        namespace["m_AIGroups"] = EnforceArray([record])

        result = self._dispatch(namespace, TOKEN)
        self.assertTrue(result.ok)
        self.assertEqual(result.ai_group.release.data, b"pending")
        self.assertEqual((result.ai_group.dropped_deleted, result.ai_group.dropped_kept), (1, 0))

        record.members.Get(0).member = None
        result = self._dispatch(namespace, TOKEN)
        self.assertEqual(result.ai_group.release.data, b"released")
        self.assertEqual((result.ai_group.dropped_deleted, result.ai_group.dropped_kept), (1, 0))
        self.assertEqual(len(game.deleted), 1)

        result = self._dispatch(namespace, "c" * 32)
        self.assertTrue(result.ok)
        self.assertEqual(result.ai_group.released, 0)
        self.assertEqual((result.ai_group.dropped_deleted, result.ai_group.dropped_kept), (0, 0))


class SourcePinTest(unittest.TestCase):
    """Brief e-g: source, contract, guide and CHANGELOG pins."""

    def test_e_fields_constant_provenance_and_one_line_log(self) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        for class_name in ("MCPAIGroupRecord", "MCPAIGroup"):
            fields = dict((name, kind) for kind, name in class_members(source, class_name))
            self.assertEqual(fields.get("dropped_deleted"), "int")
            self.assertEqual(fields.get("dropped_kept"), "int")
        self.assertIn("AI_DROPPED_RADIUS_M = 30.0", source)
        self.assertIn("m_Expansion_PreviousOwner", source)
        dropped_body = method_body(source, "protected bool AIDroppedFromGroup(MCPAIGroupRecord record, EntityAI entity)")
        self.assertIn("m_Expansion_IsOwnerPlayer", dropped_body)
        sweep = method_body(source, "protected void AISweepDroppedItems(MCPAIGroupRecord record)")
        for declaration in ("array<Object> objects = new array<Object>()", "array<CargoBase> proxyCargos = new array<CargoBase>()", "array<EntityAI> candidates = new array<EntityAI>()", "array<EntityAI> entries = new array<EntityAI>()", "array<Man> players = new array<Man>()"):
            self.assertIn(declaration, sweep)
        element_types = {"objects": "Object", "proxyCargos": "CargoBase", "candidates": "EntityAI", "entries": "EntityAI", "players": "Man"}
        for local, element in element_types.items():
            for found in re.findall(rf"\b{local}\s*=\s*new array<(\w+)>\(\)", sweep):
                self.assertEqual(found, element)
        self.assertIn("AILastHeldByGroup(record, entry)", sweep)
        self.assertIn("AILastHeldByGroup(record, ancestor)", sweep)
        log_lines = [line for line in sweep.splitlines() if "ai_group sweep" in line]
        self.assertEqual(len(log_lines), 1)
        self.assertIn("dropped_deleted=", log_lines[0])
        self.assertIn("dropped_kept=", log_lines[0])
        self.assertTrue(log_lines[0].rstrip().endswith(");"))

    def test_f_contract_census_and_no_modded_expansion_class(self) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        self.assertIn('EXPANSION_AI_ARG_CONTRACT_HASH = "f1ff4689ba7bc8b2"', source)
        self.assertIn('EXPANSION_AI_VERBS = "31"', source)
        census = method_body(
            source,
            "protected override void LeaseCloseExtensionCensus(MCPLeaseCloseCensus census)",
        )
        self.assertEqual(census.count("census.entries.Insert"), 1)
        self.assertIn('"ai_group:"', census)
        self.assertNotIn("dropped", census)
        modded = re.findall(r"modded class (\w+)", source)
        self.assertEqual(set(modded), {"MCPArgs", "MCPResult", "MCPJob", "MCPBridge"})

    def test_g_guide_and_changelog_name_the_sweep(self) -> None:
        guide = GUIDE.read_text(encoding="utf-8")
        for needle in ("dropped_deleted", "dropped_kept", "m_Expansion_PreviousOwner", "m_Expansion_IsOwnerPlayer", "ai_group sweep"):
            self.assertIn(needle, guide)
        self.assertNotIn("stays on the ground after a clean close", guide)
        self.assertNotIn("moved out attached to an item the member", guide)
        self.assertIn("player flag is per entity", guide)
        self.assertIn("whose own `m_Expansion_IsOwnerPlayer` flag", guide)
        self.assertNotIn("that has ever been in a player's inventory\nis never deleted", guide)
        self.assertNotIn("any item that has ever been in a player's inventory", CHANGELOG.read_text(encoding="utf-8"))
        changelog = CHANGELOG.read_text(encoding="utf-8")
        self.assertIn("fb-20261112-125341-9867", changelog)


if __name__ == "__main__":
    unittest.main()

