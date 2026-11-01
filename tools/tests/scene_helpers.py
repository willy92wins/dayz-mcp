"""Shared scene fakes and source-execution fixture."""
from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

from dayz_mcp.core import ECE_INITAI
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import (
    EnforceArray,
    EnforceMap,
    EnforceString,
    EnforceVector,
    fake_class,
    method_body,
    translate,
)
from tests.vehicle_eject_helpers import EjectGuardFake


BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
MESSAGES = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"

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
        "MCPVehicleEject",
    }
)
_BRIDGE_MEMBERS = frozenset({"m_Scenes", "m_RuntimeObjects", "m_NextBatchObjectId", "m_Jobs", "m_ElapsedS", "m_Tick"})
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
    "protected void DeleteSceneObjects(MCPJob job)",
    "protected bool RefreshSceneDeleteTargets(MCPJob job)",
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
        self.children: list[Any] = []
        self.is_transport = type_name == "CivilianSedan"
        self.occupants: list[Any] = []
        if holder is not None:
            holder.children.append(self)
        if parent is not None:
            parent.children.append(self)
            if parent.is_transport and type_name.startswith("Survivor"):
                parent.occupants.append(self)

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


class SceneBridgeFixture:
    """Shared setup and operations for executing the scene bridge bodies."""

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
                "MCPVehicleEject": EjectGuardFake,
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
                "m_Tick": 0,
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
        self.ns["m_Tick"] += 1
        self.ns["ProcessJobs"]()

