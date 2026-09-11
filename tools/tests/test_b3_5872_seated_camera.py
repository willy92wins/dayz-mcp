"""Offline gates for B3 5872 seated apply/observe (Sol APROBAR_CONTRATO).

#4b did not break #19: camera_unavailable_vehicle remains the generic
scripted-camera reject. This module locks the seated branch that must run
before that reject, and the four presence cases (parent+crew, parent without
crew, obsolete command without parent, free player).
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from dayz_mcp.camera_restore import restore_camera_verdict
from tests._addon_paths import addon_root
from tests.test_camera_native_crash import (
    BUILD,
    LIVE_TRANSPORT,
    READ_ERROR,
    assert_live_vehicle_guard,
    _body,
    _source,
)


BRIDGE = addon_root() / "scripts/5_Mission/MCPClientBridge.c"
FILL_SEATED = "protected bool FillSeatedCameraView(MCPCamera camera, PlayerBase cameraPlayer)"
OBSERVE_APPLY = "protected void ObserveSeatedCameraApply(MCPCamera camera, MCPArgs args)"
SETTLE_PHASE = "if (job.phase == CAMERA_PHASE_SETTLE)"


def _bridge() -> str:
    return Path(os.environ.get("MCP_CAMERA_SOURCE", str(BRIDGE))).read_text(
        encoding="utf-8-sig"
    )


class SeatedPresenceSourceTest(unittest.TestCase):
    def test_live_helper_is_parent_plus_crew_never_vehicle_command(self) -> None:
        source = _source()
        helper = _body(source, LIVE_TRANSPORT)
        self.assertNotIn("GetCommand_Vehicle", helper)
        self.assertIn("Transport.Cast(player.GetParent())", helper)
        self.assertIn("transport.CrewMemberIndex(player)", helper)
        reject_crew = _body(helper, "if (crewIndex < 0)")
        self.assertEqual(reject_crew.strip(), "return null;")
        reject_parent = _body(helper, "if (!transport)")
        self.assertEqual(reject_parent.strip(), "return null;")
        assert_live_vehicle_guard(source)

    def test_generic_vehicle_reject_stays_single_emitter_in_camera_read_error(self) -> None:
        source = _source()
        body = _body(source, READ_ERROR)
        self.assertEqual(
            _body(body, "if (ResolveLiveSeatedTransport(cameraPlayer))").strip(),
            'return "camera_unavailable_vehicle";',
        )
        self.assertEqual(source.count('"camera_unavailable_vehicle"'), 1)
        self.assertNotIn("GetCommand_Vehicle", body)
        self.assertNotIn("GetCommand_Vehicle", _body(source, LIVE_TRANSPORT))

    def test_seated_observe_branch_runs_before_generic_reject(self) -> None:
        source = _source()
        build = _body(source, BUILD)
        seated = build.index("if (ResolveLiveSeatedTransport(seatedPlayer))")
        fill = build.index("FillSeatedCameraView(camera, seatedPlayer)")
        generic = build.index("camera.error = cameraError;")
        missing = build.index("CameraErrorIsMissingScripted(cameraError)")
        scripted = build.index('camera.view = "scripted";')
        self.assertLess(seated, fill)
        self.assertLess(fill, generic)
        self.assertLess(generic, missing)
        self.assertLess(missing, scripted)
        fail = _body(build, "if (ResolveLiveSeatedTransport(seatedPlayer))")
        self.assertIn("return camera;", fail)
        self.assertIn("camera.error = cameraError;", fail)

    def test_parent_plus_crew_observe_uses_current_camera_transform(self) -> None:
        fill = _body(_source(), FILL_SEATED)
        self.assertIn("ResolveLiveSeatedTransport(cameraPlayer)", fill)
        self.assertIn(
            "GetCurrentCameraTransform(playerPos, playerDir, playerRot)", fill
        )
        self.assertIn('camera.view = "vehicle";', fill)
        self.assertIn("camera.viewport_moved = false;", fill)
        self.assertNotIn("GetCommand_Vehicle", fill)
        self.assertNotRegex(fill, r"\bGetCurrentCamera\s*\(")

    def test_parent_without_crew_is_not_the_seated_branch(self) -> None:
        helper = _body(_source(), LIVE_TRANSPORT)
        self.assertEqual(_body(helper, "if (crewIndex < 0)").strip(), "return null;")
        read = _body(_source(), READ_ERROR)
        parent = read.index("if (cameraPlayer.GetParent())")
        live = read.index("if (ResolveLiveSeatedTransport(cameraPlayer))")
        self.assertLess(live, parent)
        self.assertEqual(
            _body(read, "if (cameraPlayer.GetParent())").strip(),
            'return "camera_unavailable_parented_player";',
        )

    def test_obsolete_command_without_parent_is_not_presence(self) -> None:
        source = _source()
        helper = _body(source, LIVE_TRANSPORT)
        read = _body(source, READ_ERROR)
        fill = _body(source, FILL_SEATED)
        observe = _body(source, OBSERVE_APPLY)
        for body in (helper, read, fill, observe):
            self.assertNotIn("GetCommand_Vehicle", body)
        self.assertIn("if (!transport)", helper)
        self.assertEqual(_body(helper, "if (!transport)").strip(), "return null;")

    def test_free_player_still_uses_player_view_fill(self) -> None:
        source = _source()
        build = _body(source, BUILD)
        fill_player = _body(
            source,
            "protected bool FillPlayerCameraView(MCPCamera camera, PlayerBase cameraPlayer)",
        )
        self.assertIn("FillPlayerCameraView(camera, liberatedPlayer)", build)
        self.assertIn('camera.view = "player";', fill_player)
        self.assertIn(
            "GetCurrentCameraTransform(playerPos, playerDir, playerRot)",
            fill_player,
        )
        seated = build.index("FillSeatedCameraView(camera, seatedPlayer)")
        player = build.index("FillPlayerCameraView(camera, liberatedPlayer)")
        self.assertLess(seated, player)


class SeatedApplyObserveSourceTest(unittest.TestCase):
    def test_apply_compares_observed_transform_to_requested_pose(self) -> None:
        source = _source()
        observe = _body(source, OBSERVE_APPLY)
        self.assertIn('camera.view != "vehicle"', observe)
        self.assertIn("RequestedCameraPosition(args, requested)", observe)
        self.assertIn("CameraPositionsMatch(observed, requested)", observe)
        match = _body(observe, "if (CameraPositionsMatch(observed, requested))")
        self.assertIn('camera.view = "scripted";', match)
        self.assertIn("camera.viewport_moved = true;", match)
        self.assertIn("camera.ok = true;", match)
        self.assertIn('camera.error = "camera_unmoved_cabin";', observe)
        unmoved = observe[observe.index('camera.error = "camera_unmoved_cabin";') :]
        self.assertIn('camera.view = "vehicle";', unmoved)
        self.assertIn("camera.viewport_moved = false;", unmoved)
        self.assertIn("camera.ok = false;", unmoved)

    def test_unmoved_cabin_cannot_pass_from_scripted_snapshot_alone(self) -> None:
        source = _source()
        observe = _body(source, OBSERVE_APPLY)
        build = _body(source, BUILD)
        self.assertNotIn("m_ActiveCam", observe)
        self.assertNotIn("GetCommand_Vehicle", observe)
        report = _body(
            _body(source, "override void MCP_PostJobSuccess(MCPJob job)"),
            'if (job.kind == "camera_set")',
        )
        self.assertLess(
            report.index("BuildCameraResult(job.args.cam_mode)"),
            report.index("ObserveSeatedCameraApply(result.camera, job.args)"),
        )
        self.assertIn('result.error = "camera_unmoved_cabin";', report)
        self.assertIn("result.ok = false;", report)
        # Generic seated observe never claims scripted; only the apply compare does.
        seated_fill = _body(source, FILL_SEATED)
        self.assertNotIn('camera.view = "scripted";', seated_fill)
        self.assertIn('camera.view = "vehicle";', seated_fill)
        self.assertIn('camera.view = "scripted";', build)

    def test_settle_keeps_wall_time_for_seated_apply(self) -> None:
        settle = _body(
            _body(_source(), "protected bool ProcessCameraSetJob(MCPJob job)"),
            SETTLE_PHASE,
        )
        guard = _body(settle, 'if (CameraReadError() != "")')
        self.assertIn("ResolveLiveSeatedTransport(settlePlayer)", guard)
        nested = _body(guard, "if (!ResolveLiveSeatedTransport(settlePlayer))")
        self.assertIn("job.phase = CAMERA_PHASE_REPORT;", nested)
        self.assertIn("elapsed >= job.sample_s_target", settle)


class SeatedRestoreObserveTest(unittest.TestCase):
    def test_restore_accepts_readable_vehicle_view_when_still_seated(self) -> None:
        readable = {
            "camera": {"ok": 1, "view": "vehicle", "viewport_moved": 0, "error": ""}
        }
        unmoved = {
            "camera": {
                "ok": 0,
                "view": "vehicle",
                "viewport_moved": 0,
                "error": "camera_unmoved_cabin",
            }
        }
        scripted = {
            "camera": {"ok": 1, "view": "scripted", "viewport_moved": 1, "error": ""}
        }
        generic = {
            "camera": {
                "ok": 0,
                "view": "",
                "viewport_moved": 0,
                "error": "camera_unavailable_vehicle",
            }
        }
        self.assertEqual(restore_camera_verdict(readable), ("released", ""))
        self.assertEqual(restore_camera_verdict(unmoved)[0], "unverified")
        self.assertEqual(restore_camera_verdict(scripted)[0], "still_active")
        self.assertEqual(restore_camera_verdict(generic)[0], "unverified")


class SeatedPresenceCaseTableTest(unittest.TestCase):
    """Executable table for the four presence cases the brief named."""

    def test_case_table_matches_source_predicates(self) -> None:
        source = _source()
        helper = _body(source, LIVE_TRANSPORT)
        read = _body(source, READ_ERROR)
        build = _body(source, BUILD)
        cases = (
            (
                "parent+crew",
                "ResolveLiveSeatedTransport",
                "FillSeatedCameraView",
            ),
            (
                "parent w/o crew",
                "if (crewIndex < 0)",
                "camera_unavailable_parented_player",
            ),
            (
                "obsolete cmd w/o parent",
                "if (!transport)",
                "GetCommand_Vehicle",
            ),
            (
                "free player",
                "FillPlayerCameraView",
                'camera.view = "player"',
            ),
        )
        self.assertIn(cases[0][1], helper)
        self.assertIn(cases[0][2], build)
        self.assertIn(cases[1][1], helper)
        self.assertIn(f'return "{cases[1][2]}";', read)
        self.assertIn(cases[2][1], helper)
        self.assertNotIn(cases[2][2], helper)
        self.assertNotIn(cases[2][2], read)
        self.assertIn(cases[3][1], build)
        self.assertIn(cases[3][2], _body(
            source,
            "protected bool FillPlayerCameraView(MCPCamera camera, PlayerBase cameraPlayer)",
        ))


if __name__ == "__main__":
    unittest.main()
