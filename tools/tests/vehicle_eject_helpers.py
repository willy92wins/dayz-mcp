"""Shared vehicle-ejection fakes and source-execution helper."""
from __future__ import annotations

from types import SimpleNamespace

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import EnforceArray, translate


class EjectGuardFake:
    def __init__(self) -> None:
        self.targets = []
        self.vehicles = []
        self.ready_calls = []

    def Add(self, target) -> None:
        if target is None or target in self.targets:
            return
        self.targets.append(target)
        if getattr(target, "is_transport", False):
            self.vehicles.append(target)
        for child in getattr(target, "children", ()):
            self.Add(child)

    def Begin(self, tick: int) -> bool:
        if any(not getattr(vehicle, "eject_allowed", True) for vehicle in self.vehicles):
            return False
        for vehicle in self.vehicles:
            vehicle.exit_requested = True
        return True

    def HasVehicles(self) -> bool:
        return bool(self.vehicles)

    def HasOccupants(self) -> bool:
        return any(getattr(vehicle, "occupants", ()) for vehicle in self.vehicles)

    def Ready(self, tick: int) -> bool:
        self.ready_calls.append(tick)
        return not self.HasOccupants() and all(
            tick >= getattr(vehicle, "ready_at_tick", 2) for vehicle in self.vehicles
        )


SOURCE = addon_root() / "scripts" / "5_Mission" / "MCPVehicleEject.c"


class Array(EnforceArray):
    def Set(self, index, value):
        self[index] = value

    def Remove(self, index):
        self[index] = self[-1]
        self.pop()


class Node:
    def __init__(self):
        self.parent = None
        self.children = []
        self.sibling = None

    def GetParent(self):
        return self.parent

    def GetChildren(self):
        return self.children[0] if self.children else None

    def GetSibling(self):
        return self.sibling

    def attach(self, *children):
        self.children = list(children)
        for index, child in enumerate(children):
            child.parent = self
            child.sibling = children[index + 1] if index + 1 < len(children) else None


class Inventory:
    def __init__(self):
        self.items = []
        self.reported_count = None
        self.succeeded = True

    def CountInventory(self):
        return len(self.items) if self.reported_count is None else self.reported_count

    def EnumerateInventory(self, traversal, output):
        output.extend(self.items)
        return self.succeeded and bool(self.items)


class Entity(Node):
    def __init__(self):
        super().__init__()
        self.inventory = Inventory()

    def GetInventory(self):
        return self.inventory


class Vehicle(Entity):
    def __init__(self, size=4):
        super().__init__()
        self.crew = [None] * size

    def CrewSize(self):
        return len(self.crew)

    def CrewMember(self, seat):
        return self.crew[seat]


class Command:
    def __init__(self, vehicle):
        self.vehicle = vehicle
        self.getting_in = False
        self.getting_out = False
        self.switching = False
        self.requests = 0

    def GetTransport(self):
        return self.vehicle

    def IsGettingIn(self):
        return self.getting_in

    def IsGettingOut(self):
        return self.getting_out

    def IsSwitchSeat(self):
        return self.switching

    def GetOutVehicle(self):
        self.requests += 1
        self.getting_out = True


class Human(Node):
    def __init__(self, vehicle, seat):
        super().__init__()
        self.parent = vehicle
        self.command = Command(vehicle)
        self.alive = True
        self.unconscious = False
        vehicle.crew[seat] = self

    def IsAlive(self):
        return self.alive

    def IsUnconscious(self):
        return self.unconscious

    def GetCommand_Vehicle(self):
        return self.command

    def leave(self, vehicle):
        for seat, occupant in enumerate(vehicle.crew):
            if occupant is self:
                vehicle.crew[seat] = None
        self.parent = None
        self.command = None


MEMBERS = (
    "s_PendingVehicles", "s_PendingOccupants", "s_ClearTicks",
    "m_Roots", "m_Vehicles", "m_PreviousVehicles", "m_Occupants", "m_Humans", "m_Players", "m_Nodes", "m_Inventory",
    "m_InventoryVisited", "m_Siblings", "m_ClearTick", "m_Begun",
)
SIGNATURES = (
    "void MCPVehicleEject()",
    "void Add(Object root)",
    "bool HasVehicles()",
    "bool HasOccupants()",
    "protected bool IsClear(Transport vehicle, Human occupant)",
    "protected void Remember(Transport vehicle, Human occupant)",
    "protected void CollectPending(int tick)",
    "protected bool QueueNode(IEntity node)",
    "protected bool Collect()",
    "protected bool CollectAssociations(bool validate)",
    "bool Begin(int tick)",
    "bool Ready(int tick)",
)


def helper(source=None, shared=None, players=()):
    text = SOURCE.read_text(encoding="utf-8") if source is None else source
    namespace = {
        "array": Array,
        "EntityAI": SimpleNamespace(Cast=lambda item: item if isinstance(item, Entity) else None),
        "Transport": SimpleNamespace(Cast=lambda item: item if isinstance(item, Vehicle) else None),
        "Human": SimpleNamespace(Cast=lambda item: item if isinstance(item, Human) else None),
        "GetGame": lambda: SimpleNamespace(GetWorld=lambda: SimpleNamespace(
            GetPlayerList=lambda output: output.extend(players))),
        "InventoryTraversalType": SimpleNamespace(PREORDER=0),
    }
    for member in MEMBERS:
        namespace[member] = None
    if shared is not None:
        namespace.update(shared)
    for signature in SIGNATURES:
        translate(text, signature, namespace, members=MEMBERS,
                  class_types=("Object", "IEntity", "EntityAI", "GameInventory",
                               "Transport", "Human", "HumanCommandVehicle", "World", "array"))
    namespace["MCPVehicleEject"]()
    if shared is not None:
        shared.update({name: namespace[name] for name in ("s_PendingVehicles", "s_PendingOccupants", "s_ClearTicks")})
    return SimpleNamespace(**{name: namespace[name] for name in
                             ("Add", "Begin", "HasVehicles", "HasOccupants", "Ready")})


