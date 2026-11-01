"""Execute #298's actual Enforce eject helper against a small fake engine.

This checks control flow and fail-closed ordering, not native animation or
network/HUD settlement; those still require the separately built PBO in game.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.enforce_subset_helpers import clean
from tests.vehicle_eject_helpers import Command, Entity, Human, Node, SOURCE, Vehicle, helper


class EjectExecutionTest(unittest.TestCase):
    def test_first_guard_discovers_parented_human_without_seat_or_history(self):
        car = Vehicle()
        person = Human(car, 2)
        car.crew[2] = None
        car.attach(person)
        command = person.command
        gate = helper()
        gate.Add(car)
        self.assertTrue(gate.Begin(0))
        self.assertTrue(gate.HasOccupants())
        self.assertEqual(command.requests, 1)
        self.assertFalse(gate.Ready(0))
        self.assertFalse(gate.Ready(1))
        person.leave(car)
        self.assertFalse(gate.Ready(2))
        self.assertTrue(gate.Ready(3))

    def test_first_guard_discovers_command_only_human_from_world_without_history(self):
        car = Vehicle()
        person = Human(car, 2)
        car.crew[2] = None
        person.parent = None
        command = person.command
        gate = helper(players=[person])
        gate.Add(car)
        self.assertTrue(gate.Begin(0))
        self.assertTrue(gate.HasOccupants())
        self.assertEqual(command.requests, 1)
        self.assertFalse(gate.Ready(1))
        person.leave(car)
        self.assertFalse(gate.Ready(2))
        self.assertTrue(gate.Ready(3))

    def test_first_guard_refuses_parent_only_association_without_exit_command(self):
        car = Vehicle()
        person = Human(car, 2)
        car.crew[2] = None
        car.attach(person)
        person.command = None
        gate = helper()
        gate.Add(car)
        self.assertFalse(gate.Begin(0))
        self.assertTrue(gate.HasOccupants())
        self.assertFalse(gate.Ready(1))

    def test_world_human_in_another_vehicle_does_not_block_first_guard(self):
        car_a, car_b = Vehicle(), Vehicle()
        person = Human(car_b, 2)
        gate = helper(players=[person])
        gate.Add(car_a)
        self.assertTrue(gate.Begin(0))
        self.assertFalse(gate.HasOccupants())
        self.assertFalse(gate.Ready(0))
        self.assertTrue(gate.Ready(1))
        self.assertEqual(person.command.requests, 0)

    def test_rescan_discovers_late_command_only_association(self):
        car = Vehicle()
        people = []
        gate = helper(players=people)
        gate.Add(car)
        self.assertTrue(gate.Begin(0))
        self.assertFalse(gate.Ready(1))
        person = Human(car, 2)
        car.crew[2] = None
        person.parent = None
        people.append(person)
        self.assertFalse(gate.Ready(2))
        self.assertEqual(person.command.requests, 0)
        person.leave(car)
        self.assertFalse(gate.Ready(3))
        self.assertTrue(gate.Ready(4))

    def test_discovered_occupant_transfers_without_blocking_old_vehicle(self):
        car_a, car_b = Vehicle(), Vehicle()
        person = Human(car_a, 2)
        car_a.crew[2] = None
        car_a.attach(person)
        shared = {}
        gate = helper(shared=shared, players=[person])
        gate.Add(car_a)
        self.assertTrue(gate.Begin(0))
        person.leave(car_a)
        car_a.attach()
        car_b.attach(person)
        person.command = Command(car_b)
        car_b.crew[2] = person
        self.assertFalse(gate.Ready(1))
        retry = helper(shared=shared, players=[person])
        retry.Add(car_a)
        self.assertTrue(retry.Begin(1))
        self.assertFalse(retry.Ready(1))
        self.assertTrue(retry.Ready(2))
        self.assertTrue(gate.Ready(2))
        self.assertEqual(person.command.requests, 0)

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

    def test_unconscious_mid_exit_blocks_while_still_associated(self):
        car = Vehicle()
        occupant = Human(car, 1)
        gate = helper()
        gate.Add(car)
        self.assertTrue(gate.Begin(0))
        car.crew[1] = None
        occupant.unconscious = True
        self.assertFalse(gate.Ready(2))
        self.assertFalse(gate.Ready(3))
        occupant.leave(car)
        self.assertFalse(gate.Ready(4))
        self.assertTrue(gate.Ready(5))

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

    def test_transfer_to_other_vehicle_allows_delete_and_retry_after_extra_tick(self):
        for observe_exit in (False, True):
            for discard_first in (False, True):
                with self.subTest(observe_exit=observe_exit, discard_first=discard_first):
                    shared = {}
                    car_a, car_b = Vehicle(), Vehicle()
                    person = Human(car_a, 2)
                    first = helper(shared=shared)
                    first.Add(car_a)
                    self.assertTrue(first.Begin(0))
                    person.leave(car_a)
                    if observe_exit:
                        self.assertFalse(first.Ready(1))
                    person.parent = car_b
                    person.command = Command(car_b)
                    car_b.crew[2] = person
                    if discard_first:
                        del first  # timed-out/refused deletion loses its instance
                    else:
                        self.assertFalse(first.Ready(1))
                    retry = helper(shared=shared)
                    retry.Add(car_a)
                    self.assertTrue(retry.Begin(1))
                    self.assertTrue(retry.HasOccupants())  # shared cooldown, not B
                    self.assertFalse(retry.Ready(1))
                    self.assertFalse(retry.Ready(1))
                    self.assertTrue(retry.Ready(2))
                    if not discard_first:
                        self.assertTrue(first.Ready(2))
                    for name in ("s_PendingVehicles", "s_PendingOccupants", "s_ClearTicks"):
                        self.assertEqual(shared[name], [], name)
                    later = helper(shared=shared)
                    later.Add(car_a)
                    self.assertTrue(later.Begin(2))
                    self.assertFalse(later.HasOccupants())
                    self.assertEqual(person.command.requests, 0)
                    self.assertIs(car_b.crew[2], person)

    def test_transfer_still_blocks_if_other_vehicle_is_protected(self):
        for association in ("command", "parent", "crew"):
            with self.subTest(association=association):
                shared = {}
                car_a, car_b = Vehicle(), Vehicle()
                person = Human(car_a, 2)
                gate = helper(shared=shared)
                gate.Add(car_a)
                gate.Add(car_b)
                self.assertTrue(gate.Begin(0))
                person.leave(car_a)
                self.assertFalse(gate.Ready(1))
                if association == "command":
                    person.command = Command(car_b)
                elif association == "parent":
                    person.parent = car_b
                else:
                    car_b.crew[2] = person
                self.assertFalse(gate.Ready(2))
                self.assertFalse(gate.Ready(3))
                retry = None
                if association != "crew":
                    retry = helper(shared=shared)
                    retry.Add(car_a)
                    retry.Add(car_b)
                    self.assertTrue(retry.Begin(3))
                    self.assertTrue(retry.HasOccupants())
                    self.assertFalse(retry.Ready(3))
                    self.assertFalse(retry.Ready(4))
                person.leave(car_b)
                self.assertFalse(gate.Ready(5))
                if retry is not None:
                    self.assertFalse(retry.Ready(5))
                    self.assertTrue(retry.Ready(6))
                self.assertTrue(gate.Ready(6))
                self.assertEqual(shared["s_PendingOccupants"], [])

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
            ("first_seat_only", "\t\t\twhile (seat < seats)", "\t\t\twhile (seat < 1)",
             "test_every_seat_including_gaps_and_last_seat_gets_one_request"),
            ("skip_transport_match", "if (command.GetTransport() != vehicle) { return false; }", "",
             "test_preflight_refuses_all_before_ejecting_any_if_a_command_is_invalid"),
            ("skip_alive_gate", "\t\t\t\t\tif (!occupant.IsAlive() || occupant.IsUnconscious()) { return false; }", "",
             "test_preflight_refuses_all_before_ejecting_any_if_a_command_is_invalid"),
            ("same_tick_ready", "return tick > m_ClearTick;", "return tick >= m_ClearTick;",
             "test_empty_vehicle_still_requires_an_extra_tick"),
            ("skip_pending_command", "if (command && command.GetTransport() == vehicle) { return false; }", "",
             "test_empty_seats_do_not_hide_pending_command_or_parent"),
            ("skip_parent", "if (occupant.GetParent() == vehicle) { return false; }", "",
             "test_empty_seats_do_not_hide_pending_command_or_parent"),
            ("unrelated_command_blocks", "command && command.GetTransport() == vehicle", "command",
             "test_transfer_to_other_vehicle_allows_delete_and_retry_after_extra_tick"),
            ("unrelated_parent_blocks", "occupant.GetParent() == vehicle", "Transport.Cast(occupant.GetParent())",
             "test_transfer_to_other_vehicle_allows_delete_and_retry_after_extra_tick"),
            ("skip_inventory", "if (entity && m_InventoryVisited.Find(entity) < 0)", "if (false)",
             "test_vehicle_inserted_into_root_after_begin_is_not_missed"),
            ("skip_rescan", "if (!Collect()) { m_ClearTick = -1; return false; }", "",
             "test_vehicle_inserted_into_root_after_begin_is_not_missed"),
            ("skip_inventory_count", "if (m_Inventory.Count() != count) { return false; }", "",
             "test_failed_inventory_read_and_cycles_fail_closed"),
            ("forget_pending_exit", "Remember(vehicle, occupant);", "",
             "test_second_guard_or_retry_keeps_exit_pending_after_crew_slot_clears"),
            ("forget_transferred_exit", "Remember(m_Vehicles.Get(vehicleIndex), priorOccupant);", "",
             "test_transfer_still_blocks_if_other_vehicle_is_protected"),
            ("same_frame_prune", "else if (tick > clearTick) { completed = true; }",
             "else if (tick >= clearTick) { completed = true; }",
             "test_three_guards_in_the_exit_tick_cannot_bypass_shared_cooldown"),
            ("skip_initial_associations", "if (!CollectAssociations(true)) { return false; }", "",
             "test_first_guard_discovers_parented_human_without_seat_or_history"),
            ("skip_association_rescan", "CollectAssociations(false);", "",
             "test_rescan_discovers_late_command_only_association"),
        )
        factory = helper
        for name, old, new, test_name in mutants:
            with self.subTest(mutant=name):
                self.assertIn(old, source)
                mutant = source.replace(old, new)
                with patch(__name__ + ".helper", side_effect=lambda **kw: factory(mutant, **kw)):
                    result = unittest.TestResult()
                    EjectExecutionTest(test_name).run(result)
                self.assertFalse(result.wasSuccessful(), name)


if __name__ == "__main__":
    unittest.main()
