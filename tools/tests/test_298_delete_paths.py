"""#298 ordering gates; the ejection state machine is exercised separately.

These guards deliberately read production method bodies. They catch moving a
delete above its safety check, including inventory rollback paths.
"""
from pathlib import Path
from types import SimpleNamespace
import unittest

from tests.enforce_subset_helpers import EnforceMap, EnforceString, clean, method_body, translate
from tests.vehicle_eject_helpers import Entity, Human, Node, Vehicle, helper


ROOT = Path(__file__).resolve().parents[2]
BRIDGE = (ROOT / "addon/scripts/5_Mission/MCPBridge.c").read_text(encoding="utf-8")


class LogString(EnforceString):
    def __add__(self, other):
        if isinstance(other, EnforceString):
            return LogString(self.data + other.data)
        if isinstance(other, (int, float)) and not isinstance(other, bool):
            return LogString(self.data + str(other).encode("ascii"))
        raise AssertionError(f"unsupported log value: {other!r}")


def spawn_lifetime_rollback(spawned):
    """Run the production rollback and eject helper; only the engine is fake."""
    jobs, objects, deleted = EnforceMap(), EnforceMap(), []
    command = SimpleNamespace(id=37, args=SimpleNamespace(
        lifetime_s_set=True, lifetime_s=60.0, scene_id=EnforceString.of(""),
    ))
    result = SimpleNamespace(ok=True, error=EnforceString.of(""), object_id=0)
    jobs.Insert(command.id, SimpleNamespace(subject=spawned))
    objects.Insert(command.id, spawned)
    namespace = {
        "_S": LogString,
        "m_Jobs": jobs,
        "m_RuntimeObjects": objects,
        "m_Tick": 8,
        "EntityAI": SimpleNamespace(Cast=lambda item: item if isinstance(item, Entity) else None),
        "MCPVehicleEject": helper,
        "GetGame": lambda: SimpleNamespace(ObjectDelete=deleted.append),
        "Log": lambda line: None,
    }
    translate(
        BRIDGE, "protected void RegisterSpawnScene(MCPCommand command)",
        namespace, class_types=("MCPJob",), members=("m_Jobs",),
    )
    apply = translate(
        BRIDGE, "protected bool ApplySpawnLifetime(MCPCommand command, MCPResult result)",
        namespace, class_types=("MCPJob", "Object", "EntityAI", "MCPVehicleEject"),
        members=("m_Jobs", "m_RuntimeObjects", "m_Tick"),
    )
    post_now = apply(command, result)
    return SimpleNamespace(post_now=post_now, result=result, jobs=jobs, objects=objects, deleted=deleted)


class VehicleDeletePathsTest(unittest.TestCase):
    def test_server_delete_census_has_no_unguarded_route(self):
        routes = (
            "protected bool DispatchObjectDelete(MCPCommand command, MCPResult result)",
            "protected bool IsObjectDeleteReady(MCPJob job)",
            "protected void DeleteSceneObjects(MCPJob job)",
            "protected bool ClearLoadout(PlayerBase target, array<EntityAI> cleared)",
            "protected bool DeleteLoadoutRollback(EntityAI item)",
            "protected bool ApplySpawnLifetime(MCPCommand command, MCPResult result)",
        )
        self.assertEqual(clean(BRIDGE).count(".ObjectDelete("), len(routes))
        for signature in routes:
            with self.subTest(signature=signature):
                self.assertEqual(method_body(BRIDGE, signature).count(".ObjectDelete("), 1)

    def test_lifetime_rollback_preserves_non_entityai_parent_of_occupied_vehicle(self):
        for alive in (True, False):
            with self.subTest(alive=alive):
                root, intermediate, car = Node(), Node(), Vehicle()
                root.attach(intermediate)
                intermediate.attach(car)
                occupant = Human(car, 3)
                occupant.alive = alive
                state = spawn_lifetime_rollback(root)
                self.assertEqual(state.deleted, [])
                self.assertIs(state.objects.Get(37), root)
                self.assertFalse(state.jobs.Contains(37))
                self.assertTrue(state.post_now)
                self.assertFalse(state.result.ok)
                self.assertEqual(state.result.error.text, "vehicle_eject_failed")
                self.assertEqual(state.result.object_id, 37)
                self.assertEqual(occupant.command.requests, 1 if alive else 0)

    def test_lifetime_rollback_still_deletes_a_safe_non_entityai_parent(self):
        for child in (Node(), Vehicle()):
            with self.subTest(child=type(child).__name__):
                root = Node()
                root.attach(child)
                state = spawn_lifetime_rollback(root)
                self.assertEqual(state.deleted, [root])
                self.assertFalse(state.objects.Contains(37))
                self.assertFalse(state.jobs.Contains(37))
                self.assertTrue(state.post_now)
                self.assertFalse(state.result.ok)
                self.assertEqual(state.result.error.text, "lifetime_unsupported")
                self.assertEqual(state.result.object_id, 0)

    def test_object_delete_ejects_before_removing_registry_or_deleting(self):
        body = method_body(BRIDGE, "protected bool DispatchObjectDelete(MCPCommand command, MCPResult result)")
        self.assertLess(body.index("Begin(m_Tick)"), body.index("m_RuntimeObjects.Remove("))
        self.assertLess(body.index("HasVehicles()"), body.index("ObjectDelete("))
        self.assertIn('"vehicle_eject_failed"', body)

    def test_scene_clear_preflights_all_ejections_before_any_delete(self):
        body = method_body(BRIDGE, "protected bool DispatchSceneClear(MCPCommand command, MCPResult result)")
        self.assertLess(body.rindex("RefuseSceneShared("), body.index("Begin(m_Tick)"))
        self.assertLess(body.index("Begin(m_Tick)"), body.index("DeleteSceneObjects("))
        self.assertNotIn("ObjectDelete(", body)

    def test_async_object_delete_checks_readiness_before_deletion(self):
        body = method_body(BRIDGE, "protected bool IsObjectDeleteReady(MCPJob job)")
        self.assertLess(body.index("Ready(m_Tick)"), body.index("ObjectDelete("))
        self.assertLess(body.index("job.deadline_s"), body.index("ObjectDelete("))

    def test_scene_job_rechecks_before_deletion_and_preserves_error(self):
        body = method_body(BRIDGE, "protected bool IsSceneClearDone(MCPJob job)")
        self.assertLess(body.index("Ready(m_Tick)"), body.index("DeleteSceneObjects(job)"))
        post = method_body(BRIDGE, "protected void PostSceneClear(MCPJob job)")
        self.assertIn("result.error = job.error", post)

    def test_inventory_deletes_are_guarded_and_report_failure(self):
        clear = method_body(BRIDGE, "protected bool ClearLoadout(PlayerBase target, array<EntityAI> cleared)")
        self.assertLess(clear.index("Begin(m_Tick)"), clear.index("ObjectDelete("))
        self.assertLess(clear.index("HasOccupants()"), clear.index("ObjectDelete("))
        rollback = method_body(BRIDGE, "protected bool DeleteLoadoutRollback(EntityAI item)")
        self.assertLess(rollback.index("Begin(m_Tick)"), rollback.index("ObjectDelete("))
        self.assertLess(rollback.index("HasOccupants()"), rollback.index("ObjectDelete("))
        create = method_body(BRIDGE, "protected EntityAI CreateLoadoutItem(PlayerBase target, EntityAI owner, MCPLoadoutItem row, out string error)")
        self.assertNotIn("ObjectDelete(", create)
        self.assertEqual(create.count("DeleteLoadoutRollback(item)"), 3)
        self.assertEqual(create.count('error = "vehicle_eject_failed"'), 3)


if __name__ == "__main__":
    unittest.main()
