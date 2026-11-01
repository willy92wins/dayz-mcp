"""Execute #298's actual Enforce eject helper against a small fake engine.

This checks control flow and fail-closed ordering, not native animation or
network/HUD settlement; those still require the separately built PBO in game.
"""
from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import EnforceArray, clean, translate


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
    "m_Roots", "m_Vehicles", "m_Occupants", "m_Nodes", "m_Inventory",
    "m_InventoryVisited", "m_Siblings", "m_ClearTick", "m_Begun",
)
SIGNATURES = (
    "void MCPVehicleEject()",
    "void Add(Object root)",
    "bool HasVehicles()",
    "bool HasOccupants()",
    "protected bool IsClear(Human occupant)",
    "protected void Remember(Transport vehicle, Human occupant)",
    "protected void CollectPending(int tick)",
    "protected bool QueueNode(IEntity node)",
    "protected bool Collect()",
    "bool Begin(int tick)",
    "bool Ready(int tick)",
)


def helper(source=None, shared=None):
    text = SOURCE.read_text(encoding="utf-8") if source is None else source
    namespace = {
        "array": Array,
        "EntityAI": SimpleNamespace(Cast=lambda item: item if isinstance(item, Entity) else None),
        "Transport": SimpleNamespace(Cast=lambda item: item if isinstance(item, Vehicle) else None),
        "InventoryTraversalType": SimpleNamespace(PREORDER=0),
    }
    for member in MEMBERS:
        namespace[member] = None
    if shared is not None:
        namespace.update(shared)
    for signature in SIGNATURES:
        translate(text, signature, namespace, members=MEMBERS,
                  class_types=("Object", "IEntity", "EntityAI", "GameInventory",
                               "Transport", "Human", "HumanCommandVehicle", "array"))
    namespace["MCPVehicleEject"]()
    if shared is not None:
        shared.update({name: namespace[name] for name in ("s_PendingVehicles", "s_PendingOccupants", "s_ClearTicks")})
    return SimpleNamespace(**{name: namespace[name] for name in
                             ("Add", "Begin", "HasVehicles", "HasOccupants", "Ready")})


class EjectExecutionTest(unittest.TestCase):
    def test_plain_object_is_ready_without_a_delay(self):
        gate = helper()
        gate.Add(Node())
        self.assertTrue(gate.Begin(0))
        self.assertFalse(gate.HasVehicles())
        self.assertFalse(gate.HasOccupants())
        self.assertTrue(gate.Ready(10))

    def test_empty_vehicle_still_requires_an_extra_tick(self):
        gate = helper()
        gate.Add(Vehicle())
        self.assertTrue(gate.Begin(0))
        self.assertTrue(gate.HasVehicles())
        self.assertFalse(gate.HasOccupants())
        self.assertFalse(gate.Ready(10))
        self.assertFalse(gate.Ready(10))
        self.assertTrue(gate.Ready(11))

    def test_every_seat_including_gaps_and_last_seat_gets_one_request(self):
        car = Vehicle(9)
        people = [Human(car, seat) for seat in (0, 3, 8)]
        commands = [person.command for person in people]
        gate = helper()
        gate.Add(car)
        self.assertTrue(gate.Begin(0))
        self.assertTrue(gate.HasOccupants())
        self.assertEqual([command.requests for command in commands], [1, 1, 1])
        self.assertFalse(gate.Ready(1))
        self.assertFalse(gate.Ready(2))
        self.assertEqual([command.requests for command in commands], [1, 1, 1])
        for person in people:
            person.leave(car)
        self.assertFalse(gate.Ready(3))
        self.assertTrue(gate.Ready(4))

    def test_preflight_refuses_all_before_ejecting_any_if_a_command_is_invalid(self):
        for condition in ("missing", "wrong_vehicle", "getting_in", "switching", "dead", "unconscious"):
            with self.subTest(condition=condition):
                car = Vehicle()
                driver, passenger = Human(car, 0), Human(car, 3)
                if condition == "missing":
                    passenger.command = None
                elif condition == "wrong_vehicle":
                    passenger.command.vehicle = Vehicle()
                elif condition == "dead":
                    passenger.alive = False
                elif condition == "unconscious":
                    passenger.unconscious = True
                else:
                    setattr(passenger.command, condition, True)
                gate = helper()
                gate.Add(car)
                self.assertFalse(gate.Begin(0))
                self.assertEqual(driver.command.requests, 0)
                self.assertFalse(gate.Ready(20))

    def test_already_getting_out_is_not_restarted(self):
        car = Vehicle()
        passenger = Human(car, 3)
        passenger.command.getting_out = True
        gate = helper()
        gate.Add(car)
        self.assertTrue(gate.Begin(0))
        self.assertEqual(passenger.command.requests, 0)
        self.assertFalse(gate.Ready(1))

    def test_unconscious_mid_exit_never_authorizes_deletion(self):
        car = Vehicle()
        occupant = Human(car, 1)
        gate = helper()
        gate.Add(car)
        self.assertTrue(gate.Begin(0))
        occupant.leave(car)
        occupant.unconscious = True
        self.assertFalse(gate.Ready(2))
        self.assertFalse(gate.Ready(3))

    def test_dead_crew_refuses_but_extracted_corpse_allows_retry_after_cooldown(self):
        shared = {}
        car = Vehicle()
        corpse = Human(car, 3)
        corpse.alive = False
        first = helper(shared=shared)
        first.Add(car)
        self.assertFalse(first.Begin(0))
        self.assertEqual(corpse.command.requests, 0)
        corpse.leave(car)  # vanilla manual body extraction completed
        retry = helper(shared=shared)
        retry.Add(car)
        self.assertTrue(retry.Begin(1))
        self.assertTrue(retry.HasOccupants())
        self.assertFalse(retry.Ready(1))
        self.assertTrue(retry.Ready(2))
        self.assertEqual(shared["s_PendingOccupants"], [])

    def test_death_during_exit_still_waits_for_vehicle_command_to_end(self):
        car = Vehicle()
        occupant = Human(car, 1)
        gate = helper()
        gate.Add(car)
        self.assertTrue(gate.Begin(0))
        occupant.alive = False
        car.crew[1] = None
        occupant.parent = None
        self.assertFalse(gate.Ready(1))
        self.assertFalse(gate.Ready(2))
        occupant.command = None
        self.assertFalse(gate.Ready(3))
        self.assertTrue(gate.Ready(4))

    def test_empty_seats_do_not_hide_pending_command_or_parent(self):
        for remaining in ("command", "parent"):
            with self.subTest(remaining=remaining):
                car = Vehicle()
                person = Human(car, 2)
                gate = helper()
                gate.Add(car)
                self.assertTrue(gate.Begin(0))
                car.crew[2] = None
                if remaining == "command":
                    person.parent = None
                else:
                    person.command = None
                self.assertFalse(gate.Ready(1))
                self.assertFalse(gate.Ready(99))
                person.leave(car)
                self.assertFalse(gate.Ready(100))
                self.assertTrue(gate.Ready(101))

    def test_late_entrant_resets_delay_and_remains_tracked_after_seat_clears(self):
        car = Vehicle()
        gate = helper()
        gate.Add(car)
        self.assertTrue(gate.Begin(0))
        self.assertFalse(gate.Ready(1))
        entrant = Human(car, 3)
        self.assertFalse(gate.Ready(2))
        car.crew[3] = None
        self.assertFalse(gate.Ready(3))
        entrant.leave(car)
        self.assertFalse(gate.Ready(4))
        self.assertFalse(gate.Ready(4))
        self.assertTrue(gate.Ready(5))

    def test_nested_inventory_and_non_inventory_hierarchy_are_both_scanned(self):
        for via_inventory in (False, True):
            with self.subTest(inventory=via_inventory):
                root = Entity()
                holder = Entity()
                car = Vehicle()
                occupant = Human(car, 3)
                if via_inventory:
                    root.inventory.items = [root, holder, car]
                else:
                    root.attach(holder)
                    holder.attach(car)
                gate = helper()
                gate.Add(root)
                gate.Add(car)  # overlapping scene roots are legal
                self.assertTrue(gate.Begin(0))
                self.assertEqual(occupant.command.requests, 1)
                self.assertFalse(gate.Ready(1))

    def test_vehicle_inserted_into_root_after_begin_is_not_missed(self):
        root = Entity()
        gate = helper()
        gate.Add(root)
        self.assertTrue(gate.Begin(0))
        car = Vehicle()
        person = Human(car, 2)
        root.inventory.items = [car]
        self.assertFalse(gate.Ready(1))
        self.assertTrue(gate.HasVehicles())
        person.leave(car)
        self.assertFalse(gate.Ready(2))
        self.assertTrue(gate.Ready(3))

    def test_second_guard_or_retry_keeps_exit_pending_after_crew_slot_clears(self):
        for discard_first in (False, True):
            with self.subTest(discard_first=discard_first):
                shared = {}
                car = Vehicle()
                occupant = Human(car, 3)
                first = helper(shared=shared)
                first.Add(car)
                self.assertTrue(first.Begin(0))
                car.crew[3] = None
                occupant.parent = None
                if discard_first:
                    del first  # timeout/refused synchronous delete
                retry = helper(shared=shared)
                retry.Add(car)
                self.assertTrue(retry.Begin(0))
                self.assertTrue(retry.HasOccupants())
                self.assertFalse(retry.Ready(10))
                self.assertFalse(retry.Ready(11))
                occupant.leave(car)
                self.assertFalse(retry.Ready(12))
                self.assertTrue(retry.Ready(13))

    def test_pending_prune_keeps_vehicle_occupant_pairs_correlated(self):
        shared = {}
        cars = [Vehicle(), Vehicle()]
        people = [Human(car, 0) for car in cars]
        for car in cars:
            gate = helper(shared=shared)
            gate.Add(car)
            self.assertTrue(gate.Begin(0))
        people[0].leave(cars[0])
        observe = helper(shared=shared)
        observe.Add(cars[0])
        self.assertTrue(observe.Begin(1))
        cars[1].crew[0] = None
        people[1].parent = None
        retry = helper(shared=shared)
        retry.Add(cars[1])
        self.assertTrue(retry.Begin(2))
        self.assertTrue(retry.HasOccupants())
        self.assertFalse(retry.Ready(1))
        self.assertEqual(shared["s_PendingVehicles"], [cars[1]])
        self.assertEqual(shared["s_PendingOccupants"], [people[1]])

    def test_three_guards_in_the_exit_tick_cannot_bypass_shared_cooldown(self):
        shared = {}
        car = Vehicle()
        occupant = Human(car, 2)
        first = helper(shared=shared)
        first.Add(car)
        self.assertTrue(first.Begin(10))
        occupant.leave(car)
        for _ in range(3):
            retry = helper(shared=shared)
            retry.Add(car)
            self.assertTrue(retry.Begin(10))
            self.assertTrue(retry.HasOccupants())
        later = helper(shared=shared)
        later.Add(car)
        self.assertTrue(later.Begin(11))
        self.assertFalse(later.HasOccupants())
        self.assertEqual(shared["s_PendingVehicles"], [])

    def test_late_entrant_is_shared_with_a_second_guard(self):
        shared = {}
        car = Vehicle()
        first = helper(shared=shared)
        first.Add(car)
        self.assertTrue(first.Begin(0))
        entrant = Human(car, 3)
        self.assertFalse(first.Ready(1))
        car.crew[3] = None
        entrant.parent = None
        second = helper(shared=shared)
        second.Add(car)
        self.assertTrue(second.Begin(2))
        self.assertTrue(second.HasOccupants())
        self.assertFalse(second.Ready(3))

    def test_reentering_resets_shared_clear_tick_before_another_retry(self):
        shared = {}
        car = Vehicle()
        person = Human(car, 1)
        first = helper(shared=shared)
        first.Add(car)
        self.assertTrue(first.Begin(0))
        person.leave(car)
        self.assertFalse(first.Ready(1))
        person.parent = car
        person.command = Command(car)
        car.crew[1] = person
        self.assertFalse(first.Ready(2))
        person.leave(car)
        retry = helper(shared=shared)
        retry.Add(car)
        self.assertTrue(retry.Begin(3))
        self.assertTrue(retry.HasOccupants())
        later = helper(shared=shared)
        later.Add(car)
        self.assertTrue(later.Begin(4))
        self.assertFalse(later.HasOccupants())

    def test_failed_inventory_read_and_cycles_fail_closed(self):
        for condition in ("count_mismatch", "failed_read", "null_entry", "sibling_cycle"):
            with self.subTest(condition=condition):
                root = Entity()
                if condition == "count_mismatch":
                    root.inventory.items = [Vehicle()]
                    root.inventory.reported_count = 2
                elif condition == "failed_read":
                    root.inventory.items = [Vehicle()]
                    root.inventory.succeeded = False
                elif condition == "null_entry":
                    root.inventory.items = [None]
                else:
                    child = Node()
                    root.attach(child)
                    child.sibling = child
                gate = helper()
                gate.Add(root)
                self.assertFalse(gate.Begin(0))
                self.assertFalse(gate.Ready(20))

    def test_helper_never_deletes_and_uses_transport_not_just_cars(self):
        source = clean(SOURCE.read_text(encoding="utf-8"))
        self.assertNotIn("ObjectDelete", source)
        self.assertNotIn("CarScript.Cast", source)
        self.assertIn("Transport.Cast", source)


class EjectMutationTest(unittest.TestCase):
    def test_behavioral_cases_reject_representative_unsafe_mutants(self):
        source = SOURCE.read_text(encoding="utf-8")
        mutants = (
            ("first_seat_only", "while (seat < seats)", "while (seat < 1)",
             "test_every_seat_including_gaps_and_last_seat_gets_one_request"),
            ("skip_transport_match", "if (command.GetTransport() != vehicle) { return false; }", "",
             "test_preflight_refuses_all_before_ejecting_any_if_a_command_is_invalid"),
            ("skip_alive_gate", "\t\t\t\t\tif (!occupant.IsAlive() || occupant.IsUnconscious()) { return false; }", "",
             "test_preflight_refuses_all_before_ejecting_any_if_a_command_is_invalid"),
            ("same_tick_ready", "return tick > m_ClearTick;", "return tick >= m_ClearTick;",
             "test_empty_vehicle_still_requires_an_extra_tick"),
            ("skip_pending_command", "priorOccupant.GetCommand_Vehicle() || Transport.Cast(priorOccupant.GetParent())",
             "Transport.Cast(priorOccupant.GetParent())",
             "test_empty_seats_do_not_hide_pending_command_or_parent"),
            ("skip_parent", "priorOccupant.GetCommand_Vehicle() || Transport.Cast(priorOccupant.GetParent())",
             "priorOccupant.GetCommand_Vehicle()",
             "test_empty_seats_do_not_hide_pending_command_or_parent"),
            ("skip_inventory", "if (entity && m_InventoryVisited.Find(entity) < 0)", "if (false)",
             "test_vehicle_inserted_into_root_after_begin_is_not_missed"),
            ("skip_rescan", "if (!Collect()) { m_ClearTick = -1; return false; }", "",
             "test_vehicle_inserted_into_root_after_begin_is_not_missed"),
            ("skip_inventory_count", "if (m_Inventory.Count() != count) { return false; }", "",
             "test_failed_inventory_read_and_cycles_fail_closed"),
            ("forget_pending_exit", "Remember(vehicle, occupant);", "",
             "test_second_guard_or_retry_keeps_exit_pending_after_crew_slot_clears"),
            ("forget_late_exit", "occupied = true;\n\t\t\t\t\t\tRemember(vehicle, occupant);",
             "occupied = true;", "test_late_entrant_is_shared_with_a_second_guard"),
            ("same_frame_prune", "else if (tick > clearTick) { completed = true; }",
             "else if (tick >= clearTick) { completed = true; }",
             "test_three_guards_in_the_exit_tick_cannot_bypass_shared_cooldown"),
        )
        factory = helper
        for name, old, new, test_name in mutants:
            with self.subTest(mutant=name):
                self.assertIn(old, source)
                mutant = source.replace(old, new, 1)
                with patch(__name__ + ".helper", side_effect=lambda **kw: factory(mutant, **kw)):
                    result = unittest.TestResult()
                    EjectExecutionTest(test_name).run(result)
                self.assertFalse(result.wasSuccessful(), name)


if __name__ == "__main__":
    unittest.main()
