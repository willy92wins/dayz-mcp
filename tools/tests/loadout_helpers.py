"""Loadout Enforce bridge fakes shared by offline tests."""
from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import EnforceString, method_body, translate
from tests.vehicle_eject_helpers import EjectGuardFake


GET = "loadout_get"
APPLY = "loadout_apply"
CLEAR = "loadout_clear"
SCRIPTS = addon_root() / "scripts"
BRIDGE_PATH = SCRIPTS / "5_Mission" / "MCPBridge.c"
MESSAGES_PATH = SCRIPTS / "5_Mission" / "MCPMessages.c"

_ENFORCE_STRING = re.compile(r'"(?:\\.|[^"\\])*"')

GET_FN = "protected bool DispatchLoadoutGet(MCPCommand command, MCPResult result)"
APPLY_FN = "protected bool DispatchLoadoutApply(MCPCommand command, MCPResult result)"
CLEAR_FN = "protected bool DispatchLoadoutClear(MCPCommand command, MCPResult result)"
RESOLVE_FN = "protected PlayerBase ResolveLoadoutTarget(MCPArgs args, out string error)"
ROWS_FN = "protected string LoadoutRowsError(array<ref MCPLoadoutItem> rows)"
CLEAR_LOADOUT_FN = "protected bool ClearLoadout(PlayerBase target, array<EntityAI> cleared)"
ROLLBACK_FN = "protected bool DeleteLoadoutRollback(EntityAI item)"
APPLY_LOADOUT_FN = "protected void ApplyLoadout(PlayerBase target, MCPArgs args, MCPResult result)"
CREATE_FN = (
    "protected EntityAI CreateLoadoutItem(PlayerBase target, EntityAI owner, "
    "MCPLoadoutItem row, out string error)"
)
READ_FN = "protected void ReadLoadout(PlayerBase target, MCPLoadout loadoutRead)"
APPEND_FN = (
    "protected void AppendLoadoutRow(MCPLoadout loadoutRead, EntityAI item, "
    "string dest, int parent, int level)"
)
SLOT_NAME_FN = "protected string LoadoutSlotName(EntityAI item)"
CLEAR_DONE_FN = "protected bool IsLoadoutClearDone(MCPJob job)"
POST_CLEAR_FN = "protected void PostLoadoutClearJob(MCPJob job, bool complete)"
# The bodies these verbs add to MCPBridge.c.
_ENFORCE_FUNCTIONS = (
    GET_FN,
    APPLY_FN,
    CLEAR_FN,
    RESOLVE_FN,
    ROWS_FN,
    CLEAR_LOADOUT_FN,
    ROLLBACK_FN,
    APPLY_LOADOUT_FN,
    CREATE_FN,
    READ_FN,
    APPEND_FN,
    SLOT_NAME_FN,
    CLEAR_DONE_FN,
    POST_CLEAR_FN,
)
# The job runner the clear's job goes through. It is main's code, run here as
# it is, and not held to the style checks above.
HAS_JOB_FN = "protected bool HasActiveJobFor(Human actor, Object subject)"
PROCESS_JOBS_FN = "protected void ProcessJobs()"
JOB_READY_FN = "protected bool IsJobReady(MCPJob job)"
JOB_SUCCESS_FN = "protected void PostJobSuccess(MCPJob job)"
JOB_TIMEOUT_FN = "protected void PostJobTimeout(MCPJob job)"
# main's player resolvers, run as they are by _Bridge(real_players=True).
RESOLVE_PLAYER_FN = "protected PlayerBase ResolvePlayer(MCPArgs args, out string error)"
FIRST_HUMAN_FN = "protected Human GetFirstHuman()"
HUMAN_BY_UID_FN = "protected Human FindHumanByUid(string uid)"
LOADOUT_CTOR = "void MCPLoadout()"


# ---------------------------------------------------------------------------
# The Enforce bodies run on fakes (#261 review Y6, Y7). Each translated body
# is the real one in MCPBridge.c or MCPMessages.c; only `new T()`, template
# types and the calls to the two functions with mixed out parameters are
# rewritten into names the translator can read.


class _Str(EnforceString):
    """An Enforce string with the + a log line uses: string + int or float too."""

    __slots__ = ()

    def __add__(self, other: object) -> "_Str":
        if isinstance(other, EnforceString):
            return _Str(self.data + other.data)
        if isinstance(other, (int, float)) and not isinstance(other, bool):
            return _Str(self.data + str(other).encode("ascii"))
        raise TypeError(f"string + {type(other).__name__}")


def _s(text: str) -> _Str:
    return _Str.of(text)


# Slot ids as InventorySlots numbers them here; -1 is InventorySlots.INVALID.
_SLOT_IDS = {
    name: index
    for index, name in enumerate(("Body", "Legs", "Feet", "Back", "Vest", "magazine"))
}
_SURVIVOR_SLOTS = ("Body", "Legs", "Feet", "Back", "Vest")
# class: (its attachment slots, whether it has cargo)
_CATALOG: dict[str, tuple[tuple[str, ...], bool]] = {
    "TShirt_White": ((), False),
    "Jeans_Blue": ((), False),
    "AthleticShoes_Black": ((), False),
    "TaloonBag_Blue": ((), True),
    "SmershVest": ((), True),
    "SmershBag": ((), True),
    "Apple": ((), False),
    "Rag": ((), False),
    "BandageDressing": ((), False),
    "M4A1": (("magazine",), False),
    "Mag_STANAG_30Rnd": ((), False),
}


class _Array:
    """array<T>. Like an Enforce reference, an empty array is not null."""

    def __init__(self, items: Any = ()) -> None:
        self.items = list(items)

    def Insert(self, value: object) -> int:
        self.items.append(value)
        return len(self.items) - 1

    def Get(self, index: int) -> Any:
        if type(index) is not int or not 0 <= index < len(self.items):
            raise AssertionError(f"array index {index!r} outside {len(self.items)} items")
        return self.items[index]

    def Count(self) -> int:
        return len(self.items)

    def Clear(self) -> None:
        self.items.clear()


class _JobMap:
    """map<int, ref MCPJob> m_Jobs."""

    def __init__(self) -> None:
        self.jobs: dict[int, Any] = {}

    def Insert(self, key: int, value: object) -> None:
        self.jobs[key] = value

    def Count(self) -> int:
        return len(self.jobs)

    def GetKey(self, index: int) -> int:
        return list(self.jobs)[index]

    def GetElement(self, index: int) -> Any:
        return list(self.jobs.values())[index]

    def Remove(self, key: int) -> None:
        del self.jobs[key]


class _Row:
    """MCPLoadoutItem as the engine allocates it."""

    def __init__(self, type_name: str = "", slot: str = "", dest: str = "", parent: int = 0) -> None:
        self.type = _s(type_name)
        self.slot = _s(slot)
        self.dest = _s(dest)
        self.parent = parent


class _Applied:
    """MCPLoadoutApplied."""

    def __init__(self) -> None:
        self.ok = False
        self.error = _s("")


class _LoadoutObject:
    """MCPLoadout before its constructor runs: every ref member null."""

    def __init__(self) -> None:
        self.items: Any = None
        self.applied: Any = None
        self.too_large = False


class _Result:
    """MCPResult, the members these verbs touch."""

    def __init__(self) -> None:
        self.id = 0
        self.ok = False
        self.error = _s("")
        self.type = _s("")
        self.object_id = 0
        self.deleted = 0
        self.cleanup_complete = False
        self.uid = _s("")
        self.loadout: Any = None
        self.found = False
        self.pos_real: Any = None
        self.lifetime: Any = None
        self.tick_poll_sent = 0
        self.tick_poll_callback = 0
        self.tick_dispatch = 0


class _Job:
    """MCPJob."""

    def __init__(self) -> None:
        self.id = 0
        self.kind = _s("")
        self.args: Any = None
        self.subject: Any = None
        self.actor: Any = None
        self.loadout_cleared: Any = None
        self.loadout_uid = _s("")
        self.deadline_s = 0.0
        self.error = _s("")
        self.tick_poll_sent = 0
        self.tick_poll_callback = 0
        self.tick_dispatch = 0


class _Args:
    """MCPArgs: the target keys and the rows."""

    def __init__(self, object_id: int = 0, uid: str = "", rows: list[dict[str, Any]] | None = None) -> None:
        self.object_id = object_id
        self.uid = _s(uid)
        self.loadout_items = _wire_rows(rows or [])
        self.type = _s("")
        self.lifetime_s_set = False


class _Command:
    def __init__(self, cmd: str, args: _Args | None, command_id: int = 7) -> None:
        self.id = command_id
        self.cmd = _s(cmd)
        self.args = args


def _wire_rows(rows: list[dict[str, Any]]) -> _Array:
    return _Array(_Row(row["type"], row["slot"], row["dest"], row["parent"]) for row in rows)


def _read_rows(read: _LoadoutObject) -> list[dict[str, Any]]:
    return [
        {"type": row.type.text, "slot": row.slot.text, "dest": row.dest.text, "parent": row.parent}
        for row in read.items.items
    ]


class _Location:
    """InventoryLocation: only the slot is read."""

    def __init__(self) -> None:
        self.slot = -1

    def GetSlot(self) -> int:
        return self.slot


class _Cargo:
    def __init__(self) -> None:
        self.items: list[_Entity] = []

    def GetItemCount(self) -> int:
        return len(self.items)

    def GetItem(self, index: int) -> "_Entity":
        return self.items[index]


class _World:
    """The engine of these fakes: what was created and deleted, in order."""

    def __init__(self) -> None:
        self.created: list[_Entity] = []
        self.deleted: list[_Entity] = []
        # True: the next creates make the item but put it somewhere else than asked.
        self.misplace = False

    def make(self, type_name: str, record: bool = True) -> "_Entity | None":
        if type_name not in _CATALOG:
            return None
        slots, cargo = _CATALOG[type_name]
        item = _Entity(self, type_name, slots, cargo)
        if record:
            self.created.append(item)
        return item

    def changes(self) -> tuple[int, int]:
        return (len(self.created), len(self.deleted))

    def finish_deletes(self, *references: _Array) -> None:
        """The engine's next frame: what was deleted leaves its owner and reads null."""
        for item in self.deleted:
            item.detach()
        for array in references:
            array.items = [
                None if entry is not None and entry.gone else entry for entry in array.items
            ]


class _Inventory:
    """GameInventory of one entity."""

    def __init__(self, owner: "_Entity", slots: tuple[str, ...], cargo: bool) -> None:
        self.owner = owner
        self.slot_ids = [_SLOT_IDS[name] for name in slots]
        self.attachments: list[tuple[int, _Entity]] = []
        self.cargo = _Cargo() if cargo else None

    def AttachmentCount(self) -> int:
        return len(self.attachments)

    def GetAttachmentFromIndex(self, index: int) -> "_Entity":
        return self.attachments[index][1]

    def HasAttachmentSlot(self, slot_id: int) -> bool:
        return slot_id in self.slot_ids

    def FindAttachment(self, slot_id: int) -> "_Entity | None":
        for attached_slot, item in self.attachments:
            if attached_slot == slot_id:
                return item
        return None

    def attach(self, item: "_Entity", slot_id: int) -> None:
        self.attachments.append((slot_id, item))
        item.location = (self.owner, "attachment", slot_id)

    def CreateAttachmentEx(self, type_name: EnforceString, slot_id: int) -> "_Entity | None":
        if slot_id not in self.slot_ids or self.FindAttachment(slot_id) is not None:
            return None
        item = self.owner.world.make(type_name.text)
        if item is not None and not self.owner.world.misplace:
            self.attach(item, slot_id)
        return item

    def GetCargo(self) -> _Cargo | None:
        return self.cargo

    def CreateEntityInCargo(self, type_name: EnforceString) -> "_Entity | None":
        if self.cargo is None:
            return None
        item = self.owner.world.make(type_name.text)
        if item is not None and not self.owner.world.misplace:
            self.cargo.items.append(item)
            item.location = (self.owner, "cargo", -1)
        return item

    def HasEntityInCargo(self, item: "_Entity") -> bool:
        return self.cargo is not None and any(entry is item for entry in self.cargo.items)

    def GetCurrentInventoryLocation(self, location: _Location) -> bool:
        where = self.owner.location
        location.slot = where[2] if where is not None and where[1] == "attachment" else -1
        return True


class _Entity:
    def __init__(self, world: _World, type_name: str, slots: tuple[str, ...] = (), cargo: bool = False) -> None:
        self.world = world
        self.type = _s(type_name)
        self.inventory = _Inventory(self, slots, cargo)
        self.location: tuple[Any, str, int] | None = None
        self.gone = False

    def GetType(self) -> _Str:
        return self.type

    def GetInventory(self) -> _Inventory:
        return self.inventory

    def detach(self) -> None:
        if self.location is not None:
            owner, where, _slot = self.location
            if where == "attachment":
                owner.inventory.attachments = [
                    entry for entry in owner.inventory.attachments if entry[1] is not self
                ]
            elif where == "cargo":
                owner.inventory.cargo.items = [
                    entry for entry in owner.inventory.cargo.items if entry is not self
                ]
            else:
                owner.hands = None
            self.location = None
        self._gone()

    def _gone(self) -> None:
        self.gone = True
        for _slot, child in self.inventory.attachments:
            child._gone()
        if self.inventory.cargo is not None:
            for child in self.inventory.cargo.items:
                child._gone()


class _HumanInventory:
    def __init__(self, player: "_Player") -> None:
        self.player = player

    def CreateInHands(self, type_name: EnforceString) -> _Entity | None:
        if self.player.hands is not None:
            return None
        item = self.player.world.make(type_name.text)
        if item is not None and not self.player.world.misplace:
            self.player.hands = item
            item.location = (self.player, "hands", -1)
        return item


class _Identity:
    """PlayerIdentity: the PlainId FindHumanByUid matches a uid on."""

    def __init__(self, plain_id: str) -> None:
        self.plain_id = _s(plain_id)

    def GetPlainId(self) -> _Str:
        return self.plain_id


class _Player(_Entity):
    """PlayerBase: a survivor world_spawn made, or a connected player (identity)."""

    def __init__(self, world: _World, identity: object = None) -> None:
        super().__init__(world, "SurvivorM_Mirek", _SURVIVOR_SLOTS, False)
        self.hands: _Entity | None = None
        self.identity = identity

    def GetItemInHands(self) -> _Entity | None:
        return self.hands

    def GetHumanInventory(self) -> _HumanInventory:
        return _HumanInventory(self)

    def GetIdentity(self) -> object:
        return self.identity


class _Game:
    def __init__(self, world: _World) -> None:
        self.world = world
        # What GetPlayers lists, the first human first.
        self.players: list[_Player] = []

    def ObjectDelete(self, item: _Entity) -> None:
        self.world.deleted.append(item)

    def GetPlayers(self, players: _Array) -> None:
        for player in self.players:
            players.Insert(player)


class _InventorySlots:
    INVALID = -1

    @staticmethod
    def GetSlotIdFromString(name: EnforceString) -> int:
        return _SLOT_IDS.get(name.text, -1)

    @staticmethod
    def IsSlotIdValid(slot_id: int) -> bool:
        return slot_id >= 0

    @staticmethod
    def GetSlotName(slot_id: int) -> _Str:
        for name, value in _SLOT_IDS.items():
            if value == slot_id:
                return _s(name)
        return _s("")


_BRIDGE_REWRITES = (
    (re.compile(r"new array<EntityAI>\(\)"), "NewEntityArray()"),
    (re.compile(r"new array<int>\(\)"), "NewIntArray()"),
    (re.compile(r"new array<float>\(\)"), "NewFloatArray()"),
    (re.compile(r"\barray<EntityAI>"), "EntityArray"),
    (re.compile(r"\barray<int>"), "IntArray"),
    (
        re.compile(
            r"new (MCPJob|MCPResult|MCPLoadout|MCPLoadoutApplied|MCPLoadoutItem|"
            r"InventoryLocation|MCPSpawnLifetime)\(\)"
        ),
        r"New\1()",
    ),
    # The two callees with in and out parameters, through adapters that run
    # the translated callee and hand its out value back.
    (
        re.compile(r"(\w+) = ResolveLoadoutTarget\(command\.args, (\w+)\);"),
        r"\1 = ResolveLoadoutTargetOut(command.args); \2 = LastOutError();",
    ),
    (
        re.compile(r"(\w+) = CreateLoadoutItem\(target, owner, row, (\w+)\);"),
        r"\1 = CreateLoadoutItemOut(target, owner, row); \2 = LastOutError();",
    ),
    (re.compile(r"\bpos\[(\d)\]"), r"VecAt(pos, \1)"),
)
_CTOR_REWRITES = (
    (re.compile(r"new array<ref MCPLoadout(?:Item|Applied)>\(\)"), "NewArray()"),
    (re.compile(r"(?<![\w.])(items|applied|too_large)\s*="), r"this.\1 ="),
)
_BRIDGE_TYPES = (
    "Object",
    "Man",
    "Human",
    "PlayerBase",
    "PlayerIdentity",
    "EntityAI",
    "GameInventory",
    "CargoBase",
    "InventoryLocation",
    "MCPJob",
    "MCPVehicleEject",
    "MCPResult",
    "MCPLoadout",
    "MCPLoadoutItem",
    "MCPLoadoutApplied",
    "EntityArray",
    "IntArray",
    "RowArray",
    "vector",
)
# (signature in MCPBridge.c, signature to translate it as)
_RUN_FUNCTIONS: tuple[tuple[str, str], ...] = tuple(
    (signature, signature)
    for signature in _ENFORCE_FUNCTIONS
    if signature != ROWS_FN
) + (
    (ROWS_FN, "protected string LoadoutRowsError(RowArray rows)"),
    (HAS_JOB_FN, HAS_JOB_FN),
    (PROCESS_JOBS_FN, PROCESS_JOBS_FN),
    (JOB_READY_FN, JOB_READY_FN),
    (JOB_SUCCESS_FN, JOB_SUCCESS_FN),
    (JOB_TIMEOUT_FN, JOB_TIMEOUT_FN),
)


# _Bridge(real_players=True): main's resolvers run as they are, and the one
# call to ResolvePlayer, which has in and out parameters, goes through an
# adapter like the two above.
_PLAYER_FUNCTIONS: tuple[tuple[str, str], ...] = tuple(
    (signature, signature) for signature in (RESOLVE_PLAYER_FN, FIRST_HUMAN_FN, HUMAN_BY_UID_FN)
)
_RESOLVE_PLAYER_REWRITES = (
    (
        re.compile(r"return ResolvePlayer\(args, error\);"),
        "PlayerBase resolvedPlayer = ResolvePlayerOut(args); error = LastOutError(); "
        "return resolvedPlayer;",
    ),
)


def _rewrite(code: str, rewrites) -> str:
    for pattern, replacement in rewrites:
        code = pattern.sub(replacement, code)
    return code


def _standalone(source: str, signature: str, rewrites, as_signature: str) -> str:
    """One method of `source` on its own, its code rewritten (never its string
    literals), declared with `as_signature`."""
    body = method_body(source, signature)
    pieces: list[str] = []
    last = 0
    for match in _ENFORCE_STRING.finditer(body):
        pieces.append(_rewrite(body[last : match.start()], rewrites))
        pieces.append(match.group(0))
        last = match.end()
    pieces.append(_rewrite(body[last:], rewrites))
    return as_signature + "\n{" + "".join(pieces) + "}\n"


def _bridge_constant(source: str, declaration: str) -> float:
    match = re.search(rf"protected const {declaration} = ([0-9.]+);", source)
    if match is None:
        raise AssertionError(f"{declaration} is absent from MCPBridge.c")
    return float(match.group(1))


class _Bridge:
    """The loadout bodies of MCPBridge.c and the job runner, translated and
    run on one fake world. MCPLoadout is built by its translated constructor.

    ResolvePlayer is a stub answering `player`, unless real_players: then
    ResolvePlayer, GetFirstHuman and FindHumanByUid run as they are, on the
    players GetGame().GetPlayers lists (game.players)."""

    def __init__(self, real_players: bool = False) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        self.world = _World()
        self.registry: dict[int, object] = {}
        self.jobs = _JobMap()
        self.posted: list[_Result] = []
        self.logged: list[str] = []
        self.resolve_player_calls: list[_Args] = []
        self.player: _Player | None = None
        self.job_timeout_s = _bridge_constant(source, "float JOB_TIMEOUT_S")
        self._out_error: _Str = _s("")
        self.ctor: dict[str, Any] = {"_S": _Str, "NewArray": _Array}
        translate(
            _standalone(messages, LOADOUT_CTOR, _CTOR_REWRITES, LOADOUT_CTOR),
            LOADOUT_CTOR,
            self.ctor,
        )
        game = _Game(self.world)
        self.game = game
        self.namespace: dict[str, Any] = {
            "MCPVehicleEject": EjectGuardFake,
            "_S": _Str,
            "m_Jobs": self.jobs,
            "m_ElapsedS": 0.0,
            "m_Tick": 0,
            "JOB_TIMEOUT_S": self.job_timeout_s,
            "LOADOUT_MAX_ITEMS": int(_bridge_constant(source, "int LOADOUT_MAX_ITEMS")),
            "LOADOUT_MAX_DEPTH": int(_bridge_constant(source, "int LOADOUT_MAX_DEPTH")),
            "InventorySlots": _InventorySlots,
            "GetGame": lambda: game,
            "PlayerBase": SimpleNamespace(Cast=lambda item: item if isinstance(item, _Player) else None),
            "EntityAI": SimpleNamespace(Cast=lambda item: item if isinstance(item, _Entity) else None),
            "RuntimeObjectById": lambda object_id: self.registry.get(object_id),
            "ResolvePlayer": self._resolve_player,
            "IsKnownInventoryClassname": lambda name: name.text in _CATALOG,
            "PostResult": self.posted.append,
            "Log": lambda line: self.logged.append(line.text),
            "NewEntityArray": _Array,
            "NewIntArray": _Array,
            "NewFloatArray": _Array,
            "NewMCPJob": _Job,
            "NewMCPResult": _Result,
            "NewMCPLoadout": self._new_loadout,
            "NewMCPLoadoutApplied": _Applied,
            "NewMCPLoadoutItem": _Row,
            "NewInventoryLocation": _Location,
            "NewMCPSpawnLifetime": SimpleNamespace,
            "ResolveLoadoutTargetOut": lambda args: self._out("ResolveLoadoutTarget", args),
            "CreateLoadoutItemOut": lambda target, owner, row: self._out(
                "CreateLoadoutItem", target, owner, row
            ),
            "LastOutError": lambda: self._out_error,
            "IsSpawnReady": lambda _job: False,
            "IsSeatReady": lambda _job: False,
            "PostSeatSuccess": self._unexpected,
            "VectorToArray": self._unexpected,
            "VecAt": self._unexpected,
        }
        functions = _RUN_FUNCTIONS
        rewrites = _BRIDGE_REWRITES
        out_functions = {"ResolveLoadoutTarget": 1, "CreateLoadoutItem": 1}
        if real_players:
            del self.namespace["ResolvePlayer"]
            self.namespace.update(
                {
                    "m_Players": _Array(),
                    "Human": SimpleNamespace(
                        Cast=lambda item: item if isinstance(item, _Player) else None
                    ),
                    "ResolvePlayerOut": lambda args: self._out("ResolvePlayer", args),
                }
            )
            functions = functions + _PLAYER_FUNCTIONS
            rewrites = rewrites + _RESOLVE_PLAYER_REWRITES
            out_functions["ResolvePlayer"] = 1
        for signature, as_signature in functions:
            translate(
                _standalone(source, signature, rewrites, as_signature),
                as_signature,
                self.namespace,
                class_types=_BRIDGE_TYPES,
                out_functions=out_functions,
            )

    def _unexpected(self, *_args: object) -> None:
        raise AssertionError("a spawn or seat path ran for a loadout job")

    def _new_loadout(self) -> _LoadoutObject:
        made = _LoadoutObject()
        self.ctor["this"] = made
        self.ctor["MCPLoadout"]()
        return made

    def _resolve_player(self, args: _Args, _error: object) -> _Player | None:
        self.resolve_player_calls.append(args)
        return self.player

    def _out(self, name: str, *args: object) -> Any:
        value, self._out_error = self.namespace[name](*args)
        return value

    def call(self, name: str, *args: object) -> Any:
        return self.namespace[name](*args)

    def tick(self, elapsed_s: float) -> None:
        """One OnUpdate's job pass at game time elapsed_s."""
        self.namespace["m_ElapsedS"] = elapsed_s
        self.call("ProcessJobs")

    def survivor(self, object_id: int, identity: object = None) -> _Player:
        player = _Player(self.world, identity)
        self.registry[object_id] = player
        return player

    def put(self, owner: _Entity, where: str, type_name: str, slot: str = "") -> _Entity:
        """Something the target already has, set up without counting as a change."""
        item = self.world.make(type_name, record=False)
        assert item is not None
        if where == "attachment":
            owner.inventory.attach(item, _SLOT_IDS[slot])
        elif where == "cargo":
            assert owner.inventory.cargo is not None
            owner.inventory.cargo.items.append(item)
            item.location = (owner, "cargo", -1)
        else:
            assert isinstance(owner, _Player)
            owner.hands = item
            item.location = (owner, "hands", -1)
        return item
