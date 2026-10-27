"""A camera the bridge mounted is reported as scripted while the player sits.

fb-20261026-152109-2277: with the player seated as a car's driver, camera_set
(cam_mode lookat) answered camera_unmoved_cabin, while the capture taken right
after showed the requested view from outside (LFQuad_base on DayZDiag
1.30.164200). BuildCameraResult read the seated player's
GetCurrentCameraTransform, the cabin camera, which does not follow the mounted
camera, so the report compared the cabin pose to the request and failed, and
the camera stayed mounted while the tool said it had not moved. Now a camera
this bridge mounted and that is active is read the way it is read off a
vehicle: view scripted, from m_ActiveCam. Without one, the seated branch reads
the cabin as before, and camera_set still answers camera_unmoved_cabin.

File-only: reads the Enforce source; does not import the server stack.
"""

from __future__ import annotations

import re
import unittest

from tests._addon_paths import addon_root

BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
BUILD = "protected MCPCamera BuildCameraResult(string mode)"
OBSERVE = "protected void ObserveSeatedCameraApply(MCPCamera camera, MCPArgs args)"
SEATED = "if (ResolveLiveSeatedTransport(seatedPlayer))"
MOUNTED = "if (m_ActiveCam && m_ActiveCam.IsActive())"


def _clean(source: str) -> str:
    """Comments out, string literals kept, so a comment cannot satisfy a gate."""
    token = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.S)
    return token.sub(lambda m: m[0] if m[0].startswith('"') else "", source)


def _body(source: str, needle: str) -> str:
    """Body of the brace block that follows the first `needle`."""
    start = source.find(needle)
    if start < 0:
        raise AssertionError(f"missing source contract: {needle}")
    opening = source.index("{", start)
    depth = 0
    for pos in range(opening, len(source)):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1 : pos]
    raise AssertionError(f"unterminated block: {needle}")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _assert_in_order(case: unittest.TestCase, text: str, tokens: tuple[str, ...]) -> None:
    position = 0
    for token in tokens:
        found = text.find(token, position)
        case.assertGreaterEqual(found, 0, f"missing or out of order: {token}")
        position = found + len(token)


class Fb2277SeatedMountedCameraContract:
    """The contract, applied to one client bridge source (shipped or a mutant)."""

    def assert_mounted_camera_reads_as_scripted(self, case: unittest.TestCase, source: str) -> None:
        source = _clean(source)
        build = _body(source, BUILD)

        # cameraError is written twice: read before the seated branch and
        # cleared for a mounted camera. Any later write could bring the seated
        # reject back before the scripted read.
        writes = re.findall(r"\bcameraError\s*=(?!=)[^;]*;", build)
        case.assertEqual([_flat(w) for w in writes],
                         ["cameraError = CameraReadError();", 'cameraError = "";'])

        # The seated branch asks first whether a camera this bridge mounted is
        # active. If it is, it only clears the seated reject and lets the
        # scripted read at the end of the method run.
        seated = _body(build, SEATED)
        case.assertTrue(seated.lstrip().startswith(MOUNTED), "mounted check first in the seated branch")
        case.assertEqual(_flat(_body(seated, MOUNTED)), 'cameraError = "";')
        after_mounted = seated[seated.index(MOUNTED) :]
        after_mounted = after_mounted[after_mounted.index("}") + 1 :]
        case.assertTrue(after_mounted.lstrip().startswith("else"), "the cabin read is the else branch")
        cabin = _body(after_mounted, "else")
        _assert_in_order(
            case,
            cabin,
            (
                "if (FillSeatedCameraView(camera, seatedPlayer))",
                "return camera;",
                "camera.ok = false;",
                "camera.error = cameraError;",
                "return camera;",
            ),
        )
        case.assertNotIn("m_ActiveCam", cabin)

        # After the seated branch nothing between it and the scripted read can
        # answer for a mounted camera whose error was cleared.
        tail = build[build.index(SEATED) :]
        _assert_in_order(
            case,
            tail,
            (
                MOUNTED,
                "FillSeatedCameraView(camera, seatedPlayer)",
                "if (CameraErrorIsMissingScripted(cameraError))",
                'if (cameraError != "")',
                "camera.ok = true;",
                'camera.view = "scripted";',
                "Camera current = m_ActiveCam;",
                "current.GetTransform(matrix);",
                "VectorToArray(current.GetWorldPosition(), camera.pos);",
                "camera.viewport_moved = true;",
                "return camera;",
            ),
        )

        # camera_set's seated compare leaves a scripted snapshot alone, so a
        # mounted camera is never turned into camera_unmoved_cabin.
        observe = _body(source, OBSERVE)
        case.assertEqual(_flat(_body(observe, 'if (!camera || camera.view != "vehicle")')), "return;")
        case.assertLess(observe.index('if (!camera || camera.view != "vehicle")'),
                        observe.index('camera.error = "camera_unmoved_cabin";'))


CONTRACT = Fb2277SeatedMountedCameraContract()


def _mutant(source: str, old: str, new: str) -> str:
    # A missing anchor is an error of this test, not a rejection by the
    # contract: LookupError makes the test error where a rejection fails.
    if source.count(old) != 1:
        raise LookupError(f"mutant anchor not found exactly once: {old!r}")
    return source.replace(old, new)


class Fb2277SeatedMountedCameraTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = BRIDGE.read_text(encoding="utf-8-sig")

    def test_fb_2277_a_mounted_camera_reads_as_scripted_while_seated(self) -> None:
        CONTRACT.assert_mounted_camera_reads_as_scripted(self, self.source)

    def test_fb_2277_shapes_that_read_the_cabin_for_a_mounted_camera_fail(self) -> None:
        mounted_block = MOUNTED + "\n\t\t\t{\n\t\t\t\tcameraError = \"\";\n\t\t\t}"
        mutants = {
            # Main before the fix: a seated player always got the cabin read.
            "mounted_never_read": _mutant(self.source, MOUNTED, "if (false)"),
            "active_not_checked": _mutant(self.source, MOUNTED, "if (m_ActiveCam)"),
            "seated_reject_kept": _mutant(
                self.source, mounted_block, MOUNTED + "\n\t\t\t{\n\t\t\t}"
            ),
            "mounted_reads_the_cabin": _mutant(
                self.source,
                mounted_block,
                MOUNTED + "\n\t\t\t{\n\t\t\t\tFillSeatedCameraView(camera, seatedPlayer);\n\t\t\t\treturn camera;\n\t\t\t}",
            ),
            "error_read_again_after_seated": _mutant(
                self.source,
                "\t\tif (CameraErrorIsMissingScripted(cameraError))",
                "\t\tcameraError = CameraReadError();\n\t\tif (CameraErrorIsMissingScripted(cameraError))",
            ),
            "observe_judges_scripted": _mutant(
                self.source,
                'if (!camera || camera.view != "vehicle")',
                'if (!camera || camera.view == "")',
            ),
        }
        for label, source in mutants.items():
            with self.subTest(label):
                self.assertNotEqual(source, self.source)
                with self.assertRaises(AssertionError):
                    CONTRACT.assert_mounted_camera_reads_as_scripted(self, source)


if __name__ == "__main__":
    unittest.main()
