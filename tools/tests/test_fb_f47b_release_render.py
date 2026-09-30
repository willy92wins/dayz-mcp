"""fb-20260910-102449-f47b: releasing a scripted camera must not freeze the client render.

Measured on DayZDiag 1.29 (runs ad4aaa5f and 151b98d0, 2026-09-30): after camera_set
lookat plus restore_gameplay, camera_get read view "player" while the render stayed on
the last scripted frame; releasing after camera_set free rendered live. Keeping the
staticcamera undeleted did not help (run 24cf553a). The release now copies vanilla
CameraToolsMenu: hand the view to FreeDebugCamera, leave that one a few ticks later.

No game, no daemon. Two halves:
  - Enforce SOURCE gates (not compiled or run): outside shutdown, a release that finds
    an owned staticcamera active turns it off, turns the free camera on at its pose and
    turns the player simulation off; OnTick undoes that CAMERA_HANDOFF_TICKS later
    (free off, simulation on, staticcamera deleted); restore_gameplay replies only
    after that; a camera_set and a shutdown finish a running handoff first.
  - The Python rule: render_frozen_signal fires when max_adjacent_delta is under
    RENDER_FROZEN_DELTA_EPS, even when the sha-distinct count is above 1.
"""

from __future__ import annotations

import math
import re
import unittest

import mcp_capture
from dayz_mcp.server import ServerConfig, build_app
from tests._addon_paths import addon_root


BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
RELEASE = "protected void ReleaseCamera()"
BEGIN = "protected bool BeginCameraHandoff()"
TICK = "protected void TickCameraHandoff()"
FINISH = "protected void FinishCameraHandoff()"
QUEUE = "protected bool QueueRestoreGameplayJob(MCPCommand command, MCPResult result)"
APPLY_STATIC = "protected bool ApplyCameraSet(MCPJob job)"
APPLY_FREE = "protected bool ApplyFreeCamera(MCPJob job, MCPCameraValidation validation)"
ON_TICK = "void OnTick(float timeslice)"
CONSTRUCTOR = "void MCPClientBridge()"
SHUTDOWN = "void Shutdown()"
DISPATCH = "protected void Dispatch(MCPCommand command)"
DISPATCH_SET = "protected bool DispatchCameraSet(MCPCommand command, MCPResult result)"
EXCLUSIVE = "protected bool HasExclusiveJob()"
PROCESS_JOB = "override bool MCP_ProcessJob(MCPJob job)"
POST_SUCCESS = "override void MCP_PostJobSuccess(MCPJob job)"
RESTORE_BRANCH = 'else if (command.cmd == "restore_gameplay")'
OWNED_ACTIVE = "if (m_ActiveCam && m_ActiveCamOwned && m_ActiveCam.IsActive())"

_TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.S)


def _source() -> str:
    """The bridge without comments, string literals kept: a comment cannot satisfy a gate."""
    text = BRIDGE.read_text(encoding="utf-8-sig")
    return _TOKENS.sub(lambda m: m[0] if m[0].startswith('"') else "", text)


def _body(source: str, signature: str) -> str:
    """Text between the braces that follow signature. Braces are counted with string
    literals masked, so a "{" inside a literal does not end the block early."""
    start = source.find(signature)
    if start < 0:
        raise AssertionError(f"missing source contract: {signature}")
    masked = _TOKENS.sub(lambda m: " " * len(m[0]), source)
    opening = masked.index("{", start + len(signature))
    depth = 0
    for pos in range(opening, len(masked)):
        if masked[pos] == "{":
            depth += 1
        elif masked[pos] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1 : pos]
    raise AssertionError(f"unterminated source contract: {signature}")


class CameraReleaseHandoffSourceTest(unittest.TestCase):
    def _assert_in_order(self, body: str, statements: list[str]) -> None:
        previous = -1
        for statement in statements:
            index = body.find(statement)
            self.assertGreater(index, previous, f"{statement!r} missing or out of order")
            previous = index

    def test_release_outside_shutdown_hands_an_active_static_camera_off(self) -> None:
        release = _body(_source(), RELEASE)
        guard = _body(release, "if (!m_Shutdown)")
        # A second release during a handoff leaves it running (its reply waits too).
        self.assertEqual(_body(guard, "if (m_CameraHandoffPending)").strip(), "return;")
        owned = _body(guard, OWNED_ACTIVE)
        self.assertEqual(_body(owned, "if (BeginCameraHandoff())").strip(), "return;")
        self.assertLess(guard.index("if (m_CameraHandoffPending)"), guard.index(OWNED_ACTIVE))
        self.assertEqual(release.count("BeginCameraHandoff()"), 1)

    def test_handoff_begins_static_off_then_free_on_then_simulation_off(self) -> None:
        body = _body(_source(), BEGIN)
        # No free camera, no handoff: ReleaseCamera falls through to the immediate release.
        self.assertEqual(_body(body, "if (!freeCam)").strip(), "return false;")
        self._assert_in_order(body, [
            "FreeDebugCamera freeCam = FreeDebugCamera.GetInstance();",
            "vector handoffPos = m_ActiveCam.GetPosition();",
            "vector handoffOri = m_ActiveCam.GetOrientation();",
            "m_ActiveCam.SetActive(false);",
            "freeCam.SetPosition(handoffPos);",
            "freeCam.SetOrientation(handoffOri);",
            "freeCam.SetActive(true);",
            "player.DisableSimulation(true);",
            "m_PlayerSimulationDisabled = true;",
            "m_CameraHandoffCam = m_ActiveCam;",
            "m_CameraHandoffTicks = 0;",
            "m_CameraHandoffPending = true;",
            "m_ActiveCam = freeCam;",
            "m_ActiveCamOwned = false;",
            "return true;",
        ])
        self.assertNotIn("ObjectDelete", body)

    def test_handoff_finishes_free_off_then_simulation_on_then_delete(self) -> None:
        body = _body(_source(), FINISH)
        self.assertEqual(_body(body, "if (!m_CameraHandoffPending)").strip(), "return;")
        self._assert_in_order(body, [
            "m_CameraHandoffPending = false;",
            "freeCam.SetActive(false);",
            "m_ActiveCam = null;",
            "player.DisableSimulation(false);",
            "m_PlayerSimulationDisabled = false;",
            "g_Game.ObjectDelete(m_CameraHandoffCam);",
            "m_CameraHandoffCam = null;",
        ])
        # Simulation comes back only while this bridge still holds it off.
        self.assertIn("if (player && m_PlayerSimulationDisabled)", body)
        self.assertEqual(_body(body, "if (m_CameraHandoffCam)").strip(), "g_Game.ObjectDelete(m_CameraHandoffCam);")

    def test_on_tick_finishes_the_handoff_after_the_named_tick_count(self) -> None:
        source = _source()
        ticks = re.search(r"protected const int CAMERA_HANDOFF_TICKS = (\d+);", source)
        self.assertIsNotNone(ticks)
        self.assertEqual(int(ticks.group(1)), 10)
        tick = _body(source, TICK)
        self.assertEqual(_body(tick, "if (!m_CameraHandoffPending)").strip(), "return;")
        self._assert_in_order(tick, [
            "m_CameraHandoffTicks = m_CameraHandoffTicks + 1;",
            "if (m_CameraHandoffTicks >= CAMERA_HANDOFF_TICKS)",
        ])
        self.assertEqual(
            _body(tick, "if (m_CameraHandoffTicks >= CAMERA_HANDOFF_TICKS)").strip(), "FinishCameraHandoff();"
        )
        on_tick = _body(source, ON_TICK)
        self.assertEqual(on_tick.count("TickCameraHandoff();"), 1)
        # Ahead of the job runner (the restore job posts in the same tick) and of the
        # unconfigured early return.
        self.assertLess(on_tick.index("TickCameraHandoff();"), on_tick.index("m_JobRunner.Tick(timeslice, this);"))
        self.assertLess(on_tick.index("TickCameraHandoff();"), on_tick.index("if (!m_Configured)"))

    def test_restore_gameplay_replies_only_after_the_handoff_finished(self) -> None:
        source = _source()
        branch = _body(_body(source, DISPATCH), RESTORE_BRANCH)
        self._assert_in_order(branch, [
            "RestoreGameplay();",
            "ReleaseCamera();",
            "result.ok = true;",
            "postNow = !QueueRestoreGameplayJob(command, result);",
        ])
        queue = _body(source, QUEUE)
        self.assertEqual(_body(queue, "if (!m_CameraHandoffPending)").strip(), "return false;")
        for statement in (
            "job.id = command.id;",
            'job.kind = "restore_gameplay";',
            "job.deadline_s = m_JobRunner.GetElapsedS() + CAMERA_JOB_TIMEOUT_S;",
            "job.tick_poll_sent = result.tick_poll_sent;",
            "job.tick_poll_callback = result.tick_poll_callback;",
            "job.tick_dispatch = result.tick_dispatch;",
        ):
            with self.subTest(statement=statement):
                self.assertIn(statement, queue)
        self.assertRegex(queue, r"m_JobRunner\.AddJob\(job\);[\s\S]*return true;\s*$")
        process = _body(source, PROCESS_JOB)
        self.assertEqual(
            _body(process, 'else if (job.kind == "restore_gameplay")').strip(), "return !m_CameraHandoffPending;"
        )
        # The reply the dispatch posts when no handoff runs: same fields, nothing else.
        post = _body(_body(source, POST_SUCCESS), 'if (job.kind == "restore_gameplay")')
        self.assertEqual([line.strip() for line in post.splitlines() if line.strip()], [
            "MCPResult resultRestore = new MCPResult();",
            "resultRestore.id = job.id;",
            "resultRestore.ok = true;",
            "resultRestore.tick_poll_sent = job.tick_poll_sent;",
            "resultRestore.tick_poll_callback = job.tick_poll_callback;",
            "resultRestore.tick_dispatch = job.tick_dispatch;",
            "PostResult(resultRestore);",
        ])

    def test_camera_set_during_a_handoff_is_refused_or_finishes_it_first(self) -> None:
        source = _source()
        # While the restore job waits, camera_set answers busy: every job except
        # ui_dialog and weapon_action counts as exclusive.
        exclusive = _body(source, EXCLUSIVE)
        self.assertIn('m_JobRunner.CountExcluding("ui_dialog")', exclusive)
        self.assertNotIn("restore_gameplay", exclusive)
        self.assertIn("if (HasExclusiveJob())", _body(source, DISPATCH_SET))
        # A camera_set that reaches apply anyway (the job timed out, or it was queued
        # first) finishes the handoff before it touches a camera or the simulation.
        apply = _body(source, APPLY_STATIC)
        finish = apply.index("FinishCameraHandoff();")
        self.assertLess(apply.index("ValidateCameraArgs(job.args)"), finish)
        for later in (
            "SuppressGameplay();",
            "return ApplyFreeCamera(job, validation);",
            "DeleteOwnedCamera();",
            "g_Game.CreateObject(cameraType, validation.pos, true)",
        ):
            with self.subTest(later=later):
                self.assertLess(finish, apply.index(later))

    def test_shutdown_finishes_a_handoff_and_tears_down_at_once(self) -> None:
        source = _source()
        shutdown = _body(source, SHUTDOWN)
        self._assert_in_order(shutdown, ["m_Shutdown = true;", "RestoreGameplay();", "ReleaseCamera();"])
        release = _body(source, RELEASE)
        guard = _body(release, "if (!m_Shutdown)")
        immediate = release[release.index(guard) + len(guard):]
        self._assert_in_order(immediate, [
            "FinishCameraHandoff();",
            "m_ActiveCam.SetActive(false);",
            "freeCam.SetActive(false);",
            "DeleteOwnedCamera();",
        ])
        self.assertNotIn("BeginCameraHandoff", immediate)
        self.assertNotIn("BeginCameraHandoff", shutdown)

    def test_handoff_state_cannot_strand_a_camera(self) -> None:
        source = _source()
        self.assertIn("protected Camera m_CameraHandoffCam;", source)
        self.assertNotRegex(source, r"\bref\s+Camera\s+m_CameraHandoffCam\b")
        self.assertEqual(len(re.findall(r"\bm_CameraHandoffCam\s*=(?!=)", source)), 2)
        self.assertIn("m_CameraHandoffCam = m_ActiveCam;", _body(source, BEGIN))
        self.assertIn("m_CameraHandoffCam = null;", _body(source, FINISH))
        # Raised in one place, lowered only where the camera is deleted (and at birth).
        self.assertEqual(len(re.findall(r"\bm_CameraHandoffPending\s*=\s*true;", source)), 1)
        self.assertIn("m_CameraHandoffPending = true;", _body(source, BEGIN))
        self.assertEqual(len(re.findall(r"\bm_CameraHandoffPending\s*=\s*false;", source)), 2)
        self.assertIn("m_CameraHandoffPending = false;", _body(source, CONSTRUCTOR))
        self.assertIn("m_CameraHandoffPending = false;", _body(source, FINISH))
        # Every way a handoff ends goes through FinishCameraHandoff.
        sites = {TICK: 1, RELEASE: 1, APPLY_STATIC: 1}
        for signature, count in sites.items():
            with self.subTest(site=signature):
                self.assertEqual(_body(source, signature).count("FinishCameraHandoff();"), count)
        self.assertEqual(source.count("FinishCameraHandoff();"), sum(sites.values()))

    def test_round_one_retire_logic_is_gone(self) -> None:
        # The handoff deletes the staticcamera itself, so the retire-until-next-
        # camera_set state of round 1 (54c2508) went, and DeleteOwnedCamera is back on
        # its three call sites.
        source = _source()
        for name in ("m_RetiredCam", "RetireOwnedCamera", "DeleteRetiredCamera"):
            with self.subTest(name=name):
                self.assertNotIn(name, source)
        sites = {APPLY_STATIC: 1, APPLY_FREE: 1, RELEASE: 1}
        for signature, count in sites.items():
            with self.subTest(site=signature):
                self.assertEqual(_body(source, signature).count("DeleteOwnedCamera();"), count)
        self.assertEqual(source.count("DeleteOwnedCamera();"), sum(sites.values()))


# (frames, distinct_frames, max_adjacent_delta) of capture_screenshot on DayZDiag 1.29.
FROZEN_MEASURED = (
    (5, 2, 8.7e-08),  # ad4aaa5f, 3 s after restore_gameplay
    (5, 1, 0.0),  # ad4aaa5f, 24 s after: byte-identical
    (5, 5, 1.39e-06),  # 151b98d0, 3 s after
    (5, 2, 4.69e-04),  # 151b98d0, 23 s after
)
# Only the delta was reported for 151b98d0's live captures; 4/4 is the default shape.
LIVE_MEASURED = (
    (4, 4, 0.080),  # ad4aaa5f, baseline player view
    (4, 4, 0.004),  # ad4aaa5f, still scripted lookat view
    (4, 4, 0.009),  # ad4aaa5f, a new camera_set lookat
    (5, 5, 0.013),  # ad4aaa5f, camera_set free then restore_gameplay
    (4, 4, 0.119),  # 151b98d0, baseline player view
    (4, 4, 0.0079),  # 151b98d0, live lookat scripted view
    (4, 4, 0.114),  # 151b98d0, camera_set free then restore_gameplay
)


def _detail(frames: object, distinct: object, delta: object) -> dict[str, object]:
    return {"frames": frames, "distinct_frames": distinct, "max_adjacent_delta": delta}


class RenderFrozenDeltaRuleTest(unittest.TestCase):
    def test_threshold_sits_between_the_measured_frozen_and_live_bands(self) -> None:
        eps = mcp_capture.RENDER_FROZEN_DELTA_EPS
        self.assertLess(max(delta for _f, _d, delta in FROZEN_MEASURED), eps)
        self.assertGreater(min(delta for _f, _d, delta in LIVE_MEASURED), eps)

    def test_measured_frozen_captures_carry_the_signal(self) -> None:
        for frames, distinct, delta in FROZEN_MEASURED:
            with self.subTest(frames=frames, distinct=distinct, delta=delta):
                self.assertTrue(mcp_capture._is_render_frozen_signal(_detail(frames, distinct, delta)))

    def test_measured_live_captures_do_not_carry_the_signal(self) -> None:
        for frames, distinct, delta in LIVE_MEASURED:
            with self.subTest(frames=frames, distinct=distinct, delta=delta):
                self.assertFalse(mcp_capture._is_render_frozen_signal(_detail(frames, distinct, delta)))

    def test_threshold_is_strict(self) -> None:
        eps = mcp_capture.RENDER_FROZEN_DELTA_EPS
        self.assertFalse(mcp_capture._is_render_frozen_signal(_detail(4, 4, eps)))
        self.assertTrue(mcp_capture._is_render_frozen_signal(_detail(4, 4, math.nextafter(eps, 0.0))))

    def test_annotation_attaches_the_signal_to_a_near_identical_pair(self) -> None:
        payload = {"frame_stale_detail": _detail(5, 2, 4.69e-04), "warnings": ["already_there"]}
        annotated = mcp_capture._annotate_render_frozen_signal(payload)
        self.assertEqual(annotated["warnings"], ["already_there", mcp_capture.RENDER_FROZEN_SIGNAL])

    # Unchanged by f47b, and the wider rule must not loosen them.
    def test_a_single_frame_is_never_a_freeze(self) -> None:
        for delta in (0.0, 1e-9):
            with self.subTest(delta=delta):
                self.assertFalse(mcp_capture._is_render_frozen_signal(_detail(1, 1, delta)))

    def test_illegible_evidence_is_never_a_freeze(self) -> None:
        for detail in (
            _detail(4, 2, -1e-6),
            _detail(4, 2, math.nan),
            _detail(4, 2, -math.inf),
            _detail(4, 2, "0"),
            _detail(4, True, 0.0),
            _detail(True, 2, 0.0),
            {"frames": 4, "distinct_frames": 2},
            None,
        ):
            with self.subTest(detail=detail):
                self.assertFalse(mcp_capture._is_render_frozen_signal(detail))


class RenderFrozenRuleDescriptionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        app, _runtime = build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))
        self.tools = {tool.name: tool for tool in await app.list_tools()}
        self.eps = f"{mcp_capture.RENDER_FROZEN_DELTA_EPS:g}"

    def test_capture_screenshot_states_the_delta_rule(self) -> None:
        description = self.tools["capture_screenshot"].description or ""
        self.assertIn(f"max_adjacent_delta below {self.eps} is a frozen-render signal", description)
        self.assertIn("even when distinct_frames is above 1", description)
        self.assertIn("It is a warning, not a verdict", description)
        self.assertIn("a genuinely still live view can fall under it too", description)
        self.assertNotIn("with distinct_frames=1 plus max_adjacent_delta=0 is a frozen-render signal", description)

    def test_restore_gameplay_points_at_the_same_rule(self) -> None:
        description = self.tools["restore_gameplay"].description or ""
        self.assertIn(f"render_frozen_signal when max_adjacent_delta is below {self.eps}", description)
        self.assertIn("even with distinct_frames above 1", description)


if __name__ == "__main__":
    unittest.main()
