"""fb-20260910-102449-f47b: releasing a scripted camera must not freeze the client render.

Measured on DayZDiag 1.29 (runs ad4aaa5f and 151b98d0, 2026-09-30): after camera_set
lookat plus restore_gameplay, camera_get read view "player" while the render stayed on
the last scripted frame. The owned staticcamera was deactivated and deleted in the same
frame; the FreeDebugCamera path, which is only deactivated, came back live.

No game, no daemon. Two halves:
  - Enforce SOURCE gates (not compiled or run): a release deactivates the owned
    staticcamera and retires it; the retired camera is deleted only after the next
    camera_set has activated its own camera, or at shutdown, and no path overwrites
    the retired reference.
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
RETIRE = "protected void RetireOwnedCamera()"
DELETE_RETIRED = "protected void DeleteRetiredCamera()"
APPLY_STATIC = "protected bool ApplyCameraSet(MCPJob job)"
APPLY_FREE = "protected bool ApplyFreeCamera(MCPJob job, MCPCameraValidation validation)"
SHUTDOWN = "void Shutdown()"
DISPATCH = "protected void Dispatch(MCPCommand command)"
RESTORE_BRANCH = 'else if (command.cmd == "restore_gameplay")'

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


class CameraReleaseEnforceSourceTest(unittest.TestCase):
    def test_release_deactivates_and_retires_without_deleting_in_that_frame(self) -> None:
        source = _source()
        release = _body(source, RELEASE)
        self.assertIn("m_ActiveCam.SetActive(false);", release)
        self.assertIn("freeCam.SetActive(false);", release)
        self.assertIn("RetireOwnedCamera();", release)
        self.assertLess(release.index("m_ActiveCam.SetActive(false);"), release.index("RetireOwnedCamera();"))
        self.assertLess(release.index("freeCam.SetActive(false);"), release.index("RetireOwnedCamera();"))
        # The frame that deactivates the camera deletes nothing: not here, and not in
        # the restore_gameplay branch that calls it.
        dispatch = _body(source, DISPATCH)
        branch = _body(dispatch, RESTORE_BRANCH)
        self.assertIn("RestoreGameplay();", branch)
        self.assertIn("ReleaseCamera();", branch)
        for forbidden in ("ObjectDelete", "DeleteOwnedCamera", "DeleteRetiredCamera"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, release)
                self.assertNotIn(forbidden, branch)

    def test_retire_keeps_the_deactivated_camera_referenced(self) -> None:
        body = _body(_source(), RETIRE)
        owned = "if (m_ActiveCam && m_ActiveCamOwned)"
        guard = _body(body, owned)
        # A retired reference is never overwritten: an earlier one is deleted first.
        # That is never the camera this release just deactivated.
        self.assertLess(guard.index("DeleteRetiredCamera();"), guard.index("m_RetiredCam = m_ActiveCam;"))
        self.assertNotIn("ObjectDelete", body)
        self.assertNotIn("DeleteOwnedCamera", body)
        # The bookkeeping is dropped for every camera, owned or the free singleton.
        after = body[body.index(guard) + len(guard):]
        self.assertIn("m_ActiveCam = null;", after)
        self.assertIn("m_ActiveCamOwned = false;", after)
        self.assertNotIn("m_ActiveCam = null;", guard)

    def test_retired_camera_is_a_plain_entity_pointer(self) -> None:
        source = _source()
        self.assertIn("protected Camera m_RetiredCam;", source)
        self.assertNotRegex(source, r"\bref\s+Camera\s+m_RetiredCam\b")

    def test_delete_retired_camera_deletes_then_clears(self) -> None:
        body = _body(_source(), DELETE_RETIRED)
        self.assertEqual(_body(body, "if (m_RetiredCam)").strip(), "g_Game.ObjectDelete(m_RetiredCam);")
        self.assertRegex(body, r"\}\s*m_RetiredCam = null;\s*$")

    def test_the_retired_reference_has_exactly_two_writers(self) -> None:
        source = _source()
        self.assertEqual(len(re.findall(r"\bm_RetiredCam\s*=(?!=)", source)), 2)
        self.assertIn("m_RetiredCam = m_ActiveCam;", _body(source, RETIRE))
        self.assertIn("m_RetiredCam = null;", _body(source, DELETE_RETIRED))

    def test_static_path_deletes_the_retired_camera_once_its_own_is_live(self) -> None:
        body = _body(_source(), APPLY_STATIC)
        delete = body.index("DeleteRetiredCamera();")
        self.assertLess(body.index("cam.SetActive(true);"), delete)
        self.assertLess(body.index("m_ActiveCam = cam;"), delete)
        # Only on success: a failed apply keeps the retired camera referenced.
        self.assertRegex(body, r"DeleteRetiredCamera\(\);\s*return true;\s*$")

    def test_free_path_deletes_the_retired_camera_once_its_own_is_live(self) -> None:
        body = _body(_source(), APPLY_FREE)
        delete = body.index("DeleteRetiredCamera();")
        self.assertLess(body.index("freeCam.SetActive(true);"), delete)
        self.assertLess(body.index("m_ActiveCam = freeCam;"), delete)
        self.assertRegex(body, r"DeleteRetiredCamera\(\);\s*return true;\s*$")

    def test_shutdown_deletes_the_retired_camera_after_the_shared_release(self) -> None:
        body = _body(_source(), SHUTDOWN)
        self.assertLess(body.index("ReleaseCamera();"), body.index("DeleteRetiredCamera();"))

    def test_camera_deletes_happen_only_on_these_paths(self) -> None:
        # DeleteOwnedCamera stays on the two replace paths, before the new camera is
        # activated in the same apply, as before f47b; the release no longer calls it.
        source = _source()
        owned_sites = {APPLY_STATIC: 1, APPLY_FREE: 1}
        retired_sites = {RETIRE: 1, APPLY_STATIC: 1, APPLY_FREE: 1, SHUTDOWN: 1}
        for sites, call in ((owned_sites, "DeleteOwnedCamera();"), (retired_sites, "DeleteRetiredCamera();")):
            for signature, count in sites.items():
                with self.subTest(call=call, site=signature):
                    self.assertEqual(_body(source, signature).count(call), count)
            self.assertEqual(source.count(call), sum(sites.values()), call)


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
