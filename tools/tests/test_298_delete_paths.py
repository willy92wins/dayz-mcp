"""#298 ordering gates; the ejection state machine is exercised separately.

These guards deliberately read production method bodies. They catch moving a
delete above its safety check, including inventory rollback paths.
"""
from pathlib import Path
import unittest

from tests.enforce_subset_helpers import clean, method_body


ROOT = Path(__file__).resolve().parents[2]
BRIDGE = (ROOT / "addon/scripts/5_Mission/MCPBridge.c").read_text(encoding="utf-8")


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
        # The lifetime failure deletes only a non-EntityAI. Transport inherits
        # EntityAI, so it must take this return before reaching that delete.
        lifetime = method_body(BRIDGE, routes[-1])
        self.assertIn("entity = EntityAI.Cast(spawned);", lifetime)
        self.assertIn("if (entity)", lifetime)
        self.assertLess(lifetime.index("return false;", lifetime.index("if (entity)")),
                        lifetime.index(".ObjectDelete("))

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
