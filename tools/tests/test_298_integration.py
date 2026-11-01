"""Issue 298 bridge integration against controlled and real ejection boundaries.

The ejection helper has its own source-execution tests. This fake makes its
responses independently controllable so the bridge's real dispatch/job code
must preserve objects on refusal and wait before deleting.
Inventory retries run the translated helper with shared pending state so their
cooldown cannot be bypassed by a fresh guard in the same tick.
"""

from __future__ import annotations

import unittest


class DeleteBridgeIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        from tests import scene_helpers as scenes
        from tests.enforce_subset_helpers import translate

        self.scenes = scenes
        self.bridge = scenes.SceneBridgeFixture()
        self.bridge.setUpClass()
        self.bridge.setUp()
        for signature in (
            "protected bool DispatchObjectDelete(MCPCommand command, MCPResult result)",
            "protected bool IsObjectDeleteReady(MCPJob job)",
            "protected void PostObjectDelete(MCPJob job)",
        ):
            translate(
                self.bridge.bridge,
                signature,
                self.bridge.ns,
                class_types=scenes._BRIDGE_CLASS_TYPES,
                members=scenes._BRIDGE_MEMBERS,
            )

    def _car(self, scene="a", object_id=7):
        car = self.scenes._Entity("CivilianSedan")
        self.bridge._register(scene, object_id, car)
        return car

    def _delete(self, object_id=7, command_id=70):
        command = self.bridge._command(command_id)
        command.args.object_id = object_id
        result = self.bridge.ns["MCPResult"]()
        return self.bridge.ns["DispatchObjectDelete"](command, result), result

    def test_object_refusal_keeps_registry_and_control_hooks_untouched(self) -> None:
        car = self._car()
        car.eject_allowed = False
        post_now, result = self._delete()
        self.assertTrue(post_now)
        self.assertEqual((result.ok, result.error.text, result.object_id, result.deleted),
                         (False, "vehicle_eject_failed", 7, 0))
        self.assertIs(self.bridge.ns["m_RuntimeObjects"].Get(7), car)
        self.assertEqual(self.bridge.game.deleted, [])
        self.assertEqual(self.bridge.gone, [])
        self.assertEqual(self.bridge.drive_gone, [])
        self.assertEqual(self.bridge.ns["m_Jobs"].Count(), 0)

    def test_object_waits_for_occupants_and_guard_before_deleting(self) -> None:
        car = self._car()
        car.occupants = [object(), object(), object()]
        self.assertFalse(self._delete()[0])
        self.assertTrue(car.exit_requested)
        self.assertEqual(self.bridge.game.deleted, [])
        self.bridge._run_jobs_at(0.1)
        car.occupants.pop()
        self.bridge._run_jobs_at(0.2)
        self.assertEqual(self.bridge.game.deleted, [])
        self.assertEqual(self.bridge.posted, [])
        car.occupants.clear()
        self.bridge._run_jobs_at(0.3)
        self.assertEqual(self.bridge.game.deleted, [car])
        self.assertFalse(self.bridge.ns["m_RuntimeObjects"].Contains(7))
        self.assertEqual(self.bridge.gone, [7])
        self.assertEqual(self.bridge.drive_gone, [7])
        result = self.bridge.posted[0]
        self.assertEqual((result.ok, result.object_id, result.deleted), (True, 7, 1))

    def test_object_exit_timeout_preserves_id_and_can_be_retried(self) -> None:
        car = self._car()
        car.occupants = [object()]
        self._delete()
        self.bridge._run_jobs_at(5.1)
        result = self.bridge.posted[0]
        self.assertEqual((result.ok, result.error.text, result.object_id, result.deleted),
                         (False, "vehicle_eject_failed", 7, 0))
        self.assertIs(self.bridge.ns["m_RuntimeObjects"].Get(7), car)
        self.assertEqual(self.bridge.game.deleted, [])
        car.occupants.clear()
        self.assertFalse(self._delete(command_id=71)[0])
        self.bridge._run_jobs_at(5.2)
        self.assertEqual(self.bridge.game.deleted, [car])
        self.assertTrue(self.bridge.posted[-1].ok)

    def test_object_deletes_empty_root_after_vehicle_detaches_and_is_occupied(self) -> None:
        from tests import vehicle_eject_helpers as eject

        root = eject.Entity()
        car = eject.Vehicle()
        root.attach(car)
        players = []
        self.bridge.ns["MCPVehicleEject"] = lambda: eject.helper(players=players)
        self.bridge.ns["m_RuntimeObjects"].Insert(7, root)
        self.assertFalse(self._delete()[0])
        self.assertEqual(self.bridge.game.deleted, [])

        root.attach()
        car.parent = None
        occupant = eject.Human(car, 0)
        players.append(occupant)
        self.bridge._run_jobs_at(0.1)

        self.assertEqual(self.bridge.game.deleted, [root])
        self.assertFalse(self.bridge.ns["m_RuntimeObjects"].Contains(7))
        result = self.bridge.posted[0]
        self.assertEqual((result.ok, result.error.text, result.object_id, result.deleted),
                         (True, "", 7, 1))
        self.assertIs(car.crew[0], occupant)
        self.assertEqual(occupant.command.requests, 0)

    def test_scene_preflight_refusal_deletes_nothing_including_nonvehicles(self) -> None:
        car = self._car()
        car.eject_allowed = False
        barrel = self.scenes._Entity("Barrel_Green")
        self.bridge._register("a", 9, barrel)
        post_now, _result = self.bridge._clear("a")
        self.assertFalse(post_now)
        self.assertEqual(self.bridge.game.deleted, [])
        self.assertEqual(self.bridge.gone, [])
        self.assertEqual(self.bridge.drive_gone, [])
        self.assertEqual(self.bridge.ns["m_RuntimeObjects"].Count(), 2)
        self.assertEqual(self.bridge.ns["m_Jobs"].Count(), 0)
        result = self.bridge.posted[0]
        self.assertEqual((result.ok, result.error.text), (False, "vehicle_eject_failed"))
        self.assertEqual((result.scene_clear.deleted, result.scene_clear.errors), (0, 2))
        self.assertEqual(list(result.scene_clear.error_ids), [7, 9])

    def test_scene_timeout_keeps_entire_scene_for_retry(self) -> None:
        car = self._car()
        car.occupants = [object()]
        self.bridge._register("a", 9, self.scenes._Entity("Barrel_Green"))
        self.bridge._clear("a")
        self.bridge._run_jobs_at(5.1)
        self.assertEqual(self.bridge.game.deleted, [])
        self.assertEqual(self.bridge.ns["m_RuntimeObjects"].Count(), 2)
        result = self.bridge.posted[0]
        self.assertEqual((result.ok, result.error.text, result.scene_clear.deleted),
                         (False, "vehicle_eject_failed", 0))
        self.assertEqual(list(result.scene_clear.error_ids), [7, 9])

    def test_scene_shared_is_checked_before_any_exit_request(self) -> None:
        car = self._car()
        other = self.scenes._Entity("CarBattery", holder=car)
        self.bridge._register("b", 9, other)
        post_now, result = self.bridge._clear("a")
        self.assertTrue(post_now)
        self.assertEqual(result.error.text, "scene_shared")
        self.assertFalse(getattr(car, "exit_requested", False))
        self.assertEqual(self.bridge.game.deleted, [])

    def test_new_cross_scene_child_during_exit_refuses_the_whole_delete(self) -> None:
        car = self._car()
        self.bridge._clear("a")
        self.bridge._run_jobs_at(0.1)
        other = self.scenes._Entity("CarBattery", holder=car)
        self.bridge._register("b", 9, other)
        self.bridge._run_jobs_at(0.2)
        self.assertEqual(self.bridge.game.deleted, [])
        self.assertEqual(self.bridge.ns["m_RuntimeObjects"].Count(), 2)
        result = self.bridge.posted[0]
        self.assertEqual((result.ok, result.error.text), (False, "scene_shared"))
        self.assertEqual((result.scene_clear.carrier_id, result.scene_clear.carried_id), (7, 9))

    def test_detached_same_scene_child_gets_its_own_delete(self) -> None:
        car = self._car()
        battery = self.scenes._Entity("CarBattery", holder=car)
        self.bridge._register("a", 9, battery)
        self.bridge._clear("a")
        self.bridge._run_jobs_at(0.1)
        battery.holder = None
        car.children.remove(battery)
        self.bridge._run_jobs_at(0.2)
        self.assertEqual(self.bridge.game.deleted, [car, battery])
        self.bridge._engine_removes(car, battery)
        self.bridge._run_jobs_at(0.3)
        result = self.bridge.posted[0]
        self.assertTrue(result.ok)
        self.assertEqual((result.scene_clear.deleted, result.scene_clear.errors), (2, 0))


class InventoryDeleteIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        from tests import loadout_helpers as loadout
        from tests import vehicle_eject_helpers as eject

        self.loadout = loadout
        self.eject = eject
        self.bridge = loadout._Bridge()
        shared = {}
        self.bridge.namespace["MCPVehicleEject"] = lambda: eject.helper(shared=shared)

    def test_clear_keeps_all_items_while_vehicle_exit_is_pending(self) -> None:
        player = self.bridge.survivor(3)
        shirt = self.eject.Entity()
        player.inventory.attach(shirt, self.loadout._SLOT_IDS["Body"])
        vehicle = self.eject.Vehicle()
        player.hands = vehicle
        occupants = [self.eject.Human(vehicle, seat) for seat in (0, 3)]
        exit_commands = [occupant.command for occupant in occupants]
        result = self.loadout._Result()
        command = self.loadout._Command(self.loadout.CLEAR, self.loadout._Args(object_id=3), 21)
        self.assertTrue(self.bridge.call("DispatchLoadoutClear", command, result))
        self.assertEqual((result.ok, result.error.text, result.deleted, result.cleanup_complete),
                         (False, "vehicle_eject_failed", 0, False))
        self.assertEqual([command.requests for command in exit_commands], [1, 1])
        self.assertEqual(self.bridge.world.deleted, [])
        self.assertEqual(self.bridge.jobs.Count(), 0)
        for occupant in occupants:
            occupant.leave(vehicle)
        self.assertEqual(self.bridge.namespace["m_Tick"], 0)
        for _ in range(2):
            retry = self.loadout._Result()
            self.assertTrue(self.bridge.call("DispatchLoadoutClear", command, retry))
            self.assertEqual((retry.ok, retry.error.text, retry.deleted, retry.cleanup_complete),
                             (False, "vehicle_eject_failed", 0, False))
            self.assertEqual(self.bridge.world.deleted, [])
            self.assertEqual(self.bridge.jobs.Count(), 0)
        self.bridge.namespace["m_Tick"] = 1
        self.assertFalse(self.bridge.call("DispatchLoadoutClear", command, self.loadout._Result()))
        self.assertEqual(self.bridge.world.deleted, [shirt, vehicle])

    def test_inventory_rollback_never_deletes_an_occupied_vehicle(self) -> None:
        vehicle = self.eject.Vehicle()
        occupant = self.eject.Human(vehicle, 0)
        exit_command = occupant.command
        self.assertFalse(self.bridge.call("DeleteLoadoutRollback", vehicle))
        self.assertEqual(exit_command.requests, 1)
        self.assertEqual(self.bridge.world.deleted, [])
        occupant.leave(vehicle)
        self.assertEqual(self.bridge.namespace["m_Tick"], 0)
        for _ in range(2):
            self.assertFalse(self.bridge.call("DeleteLoadoutRollback", vehicle))
            self.assertEqual(self.bridge.world.deleted, [])
        self.bridge.namespace["m_Tick"] = 1
        self.assertTrue(self.bridge.call("DeleteLoadoutRollback", vehicle))
        self.assertEqual(self.bridge.world.deleted, [vehicle])


if __name__ == "__main__":
    unittest.main()
