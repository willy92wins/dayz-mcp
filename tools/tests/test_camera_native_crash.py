"""Offline regression for the native GetCurrentCamera null dereference.

The forbidden call is identified independently by both 2026-09-08 SUB_BRZ RPTs
and minidumps, not by the proposed transformation. These are SOURCE gates,
plus tests of the actual Python restore consumer; they do not run Enforce.
MCP_CAMERA_SOURCE permits a red control without reverting the shared tree.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp import server
from tests._addon_paths import addon_root


BRIDGE = addon_root() / "scripts/5_Mission/MCPClientBridge.c"
BUILD = "protected MCPCamera BuildCameraResult(string mode)"
READ_ERROR = "protected string CameraReadError()"
LIVE_TRANSPORT = "protected Transport ResolveLiveSeatedTransport(PlayerBase player)"


def _source() -> str:
    path = Path(os.environ.get("MCP_CAMERA_SOURCE", str(BRIDGE)))
    source = path.read_text(encoding="utf-8-sig")
    # Preserve string literals; remove comments so a comment cannot satisfy a gate.
    token = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.S)
    return token.sub(lambda m: m[0] if m[0].startswith('"') else "", source)


def _body(source: str, signature: str) -> str:
    start = source.find(signature)
    if start < 0:
        raise AssertionError(f"missing source contract: {signature}")
    opening = source.index("{", start)
    depth = 0
    for pos in range(opening, len(source)):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1 : pos]
    raise AssertionError(f"unterminated source contract: {signature}")


DENIED = (
    ("!IsClientInGame()", "client_not_in_game"),
    ("!cameraPlayer", "camera_unavailable_player"),
    ("ResolveLiveSeatedTransport(cameraPlayer)", "camera_unavailable_vehicle"),
    ("cameraPlayer.GetParent()", "camera_unavailable_parented_player"),
    ("!m_ActiveCam", "camera_unavailable_no_scripted_camera"),
    ("!m_ActiveCam.IsActive()", "camera_unavailable_inactive"),
)


def assert_live_vehicle_guard(source: str) -> None:
    helper = _body(source, LIVE_TRANSPORT)
    body = _body(source, READ_ERROR)
    if "GetCommand_Vehicle" in helper or "cameraPlayer.GetCommand_Vehicle()" in body:
        raise AssertionError("vehicle command cannot be the camera presence source")

    helper_ordered = (
        "Transport.Cast(player.GetParent())",
        "if (!transport)",
        "transport.CrewMemberIndex(player)",
        "if (crewIndex < 0)",
        "return transport;",
    )
    previous = -1
    for token in helper_ordered:
        index = helper.find(token)
        if index < 0 or index <= previous:
            raise AssertionError(f"live camera helper order/contract missing: {token}")
        previous = index

    fresh = body.index("PlayerBase cameraPlayer = PlayerBase.Cast(GetGame().GetPlayer())")
    live = body.index("if (ResolveLiveSeatedTransport(cameraPlayer))")
    parent = body.index("if (cameraPlayer.GetParent())")
    active = body.index("if (!m_ActiveCam)")
    if not fresh < live < parent < active:
        raise AssertionError("fresh player/live transport/parent guards must precede camera natives")
    if source.count('"camera_unavailable_vehicle"') != 1:
        raise AssertionError("camera_unavailable_vehicle must have one production emitter")


class CameraNativeCrashSourceTest(unittest.TestCase):
    def test_native_getter_from_both_crash_stacks_is_never_called(self) -> None:
        self.assertEqual(
            re.findall(r"\bGetCurrentCamera\s*\(", _source()), [],
            "Both RPTs map deployed BuildCameraResult:3664 to this native call; "
            "a null check on its return value is too late.",
        )

    def test_readiness_and_state_guards_return_before_camera_reads(self) -> None:
        body = _body(_source(), READ_ERROR)
        previous = -1
        for condition, error in DENIED:
            with self.subTest(condition=condition):
                signature = f"if ({condition})"
                self.assertGreater(body.index(signature), previous)
                previous = body.index(signature)
                self.assertEqual(_body(body, signature).strip(), f'return "{error}";')
        self.assertTrue(body.rstrip().endswith('return "";'))
        self.assertNotRegex(body, r"(?:Camera\.|GetCurrentCamera|GetTransform|GetWorldPosition)")

    def test_camera_guard_resolves_fresh_player_and_live_membership_each_call(self) -> None:
        source = _source()
        assert_live_vehicle_guard(source)
        body = _body(source, READ_ERROR)
        self.assertEqual(body.count("GetGame().GetPlayer()"), 1)
        self.assertNotRegex(source, r"protected\s+PlayerBase\s+m_.*(?:Camera|Player)")

    def test_stale_vehicle_command_red_control(self) -> None:
        source = _source()
        assert_live_vehicle_guard(source)
        camera_body = _body(source, READ_ERROR)
        mutant_body = camera_body.replace(
            "ResolveLiveSeatedTransport(cameraPlayer)",
            "cameraPlayer.GetCommand_Vehicle()",
            1,
        )
        self.assertNotEqual(mutant_body, camera_body)
        mutant = source.replace(camera_body, mutant_body, 1)
        with self.assertRaises(AssertionError):
            assert_live_vehicle_guard(mutant)

    def test_camera_reference_is_checked_before_the_instance_native(self) -> None:
        body = _body(_source(), READ_ERROR)
        self.assertLess(body.index("if (!m_ActiveCam)"), body.index("m_ActiveCam.IsActive()"))
        self.assertIn('return "camera_unavailable_no_scripted_camera";',
                      _body(body, "if (!m_ActiveCam)"))

    def test_failed_snapshot_is_empty_and_explicitly_not_ok(self) -> None:
        body = _body(_source(), BUILD)
        self.assertIn("string cameraError = CameraReadError();", body)
        guard = _body(body, 'if (cameraError != "")')
        for statement in ("camera.ok = false;", "camera.viewport_moved = false;",
                          "camera.error = cameraError;", "return camera;"):
            self.assertIn(statement, guard)
        prefix = body[:body.index("camera.ok = true;")]
        self.assertIn("return camera;", prefix)
        self.assertNotRegex(prefix, r"GetTransform|GetWorldPosition|VectorToArray|MatrixToArray|Camera\.")

    def test_success_reads_the_tracked_active_camera_after_the_guard(self) -> None:
        source = _source()
        body = _body(source, BUILD)
        self.assertIn("protected Camera m_ActiveCam;", source)
        self.assertIn("Camera current = m_ActiveCam;", body)
        guard = body.index('if (cameraError != "")')
        for statement in ("current.GetTransform(matrix);",
                          "VectorToArray(current.GetWorldPosition(), camera.pos);",
                          "camera.fov = Camera.GetCurrentFOV();",
                          "camera.interpolation_complete = Camera.IsInterpolationComplete();"):
            self.assertGreater(body.index(statement), guard)
        self.assertGreater(body.index("camera.viewport_moved = true;"),
                           body.index("current.GetTransform(matrix);"))

    def test_missing_owned_camera_is_never_claimed_as_observed_player_camera(self) -> None:
        body = _body(_source(), BUILD)
        self.assertNotIn('"player_camera_active"', body)
        self.assertNotRegex(body, r"GetCurrentCamera(?:Position|Direction)\s*\(")

    def test_camera_get_uses_the_common_snapshot(self) -> None:
        body = _body(_source(), "protected bool DispatchCameraGet(MCPCommand command, MCPResult result)")
        self.assertIn("result.camera = BuildCameraResult(mode);", body)

    def test_camera_set_report_uses_the_same_snapshot(self) -> None:
        body = _body(_source(), "override void MCP_PostJobSuccess(MCPJob job)")
        report = _body(body, 'if (job.kind == "camera_set")')
        self.assertIn("result.camera = BuildCameraResult(job.args.cam_mode);", report)
        self.assertGreater(report.index("PostResult(result);"), report.index("BuildCameraResult("))

    def test_settle_reports_unavailable_state_before_global_interpolation_read(self) -> None:
        body = _body(_source(), "protected bool ProcessCameraSetJob(MCPJob job)")
        settle = _body(body, "if (job.phase == CAMERA_PHASE_SETTLE)")
        guard = _body(settle, 'if (CameraReadError() != "")')
        self.assertIn("job.phase = CAMERA_PHASE_REPORT;", guard)
        self.assertIn("return true;", guard)
        self.assertNotIn("job.error", guard)  # report produces the camera.ok=false block
        self.assertLess(settle.index("CameraReadError()"),
                        settle.index("Camera.IsInterpolationComplete()"))


class CameraRestoreConsumerTest(unittest.IsolatedAsyncioTestCase):
    async def test_actual_restore_tool_keeps_every_unavailable_state_unverified(self) -> None:
        body = _body(_source(), READ_ERROR)
        for _condition, error in DENIED:
            with self.subTest(error=error):
                self.assertIn(f'return "{error}";', body)
                app, runtime = server.build_app(
                    server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
                )
                probe = {"ok": 1, "camera": {"ok": 0, "viewport_moved": 0,
                         "error": error, "pos": [], "matrix": [], "dir": []}}
                with patch.object(runtime, "call_bridge", new=AsyncMock(side_effect=[
                    {"ok": 1, "error": ""}, probe,
                ])) as call:
                    with self.assertRaises(Exception) as caught:
                        await app.call_tool("restore_gameplay", {"timeout_s": 1.0})
                self.assertEqual(type(caught.exception).__name__, "ToolError")
                self.assertIn("restore_unverified", str(caught.exception))
                self.assertIn(error, str(caught.exception))
                self.assertEqual([item.args[0] for item in call.await_args_list],
                                 ["restore_gameplay", "camera_get"])

    def test_consumer_retains_positive_reading_and_active_camera_distinction(self) -> None:
        released = {"camera": {"ok": 1, "viewport_moved": 0, "error": "player_camera_active"}}
        active = {"camera": {"ok": 1, "viewport_moved": 1, "error": ""}}
        self.assertEqual(server._restore_camera_verdict(released)[0], "released")
        self.assertEqual(server._restore_camera_verdict(active)[0], "still_active")


if __name__ == "__main__":
    unittest.main()
