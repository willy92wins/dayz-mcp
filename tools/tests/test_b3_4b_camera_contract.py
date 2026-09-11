"""Focal offline gates for B3 #4b camera (decision 6 + paired fichas).

Source contracts also live in test_guards_bridge (g7) and
test_camera_native_crash. This module stays importable without Win32.
"""
from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from dayz_mcp.camera_restore import restore_camera_verdict
from tests._addon_paths import addon_root

import mcp_capture

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "tools/dayz_mcp/server.py"
GRAB = Path(mcp_capture.GRAB_SCRIPT)


class CameraGetViewContractTest(unittest.TestCase):
    def test_verdict_trichotomy_is_observable(self) -> None:
        player = {"camera": {"ok": 1, "view": "player", "viewport_moved": 0, "error": ""}}
        scripted = {"camera": {"ok": 1, "view": "scripted", "viewport_moved": 1, "error": ""}}
        illegible = {
            "camera": {
                "ok": 0,
                "view": "",
                "viewport_moved": 0,
                "error": "camera_illegible_player_transform",
            }
        }
        inferred = {
            "camera": {
                "ok": 1,
                "viewport_moved": 0,
                "view": "",
                "error": "",
            }
        }
        self.assertEqual(restore_camera_verdict(player), ("released", ""))
        self.assertEqual(restore_camera_verdict(scripted)[0], "still_active")
        self.assertEqual(restore_camera_verdict(illegible)[0], "unverified")
        self.assertEqual(restore_camera_verdict(inferred)[0], "unverified")

    def test_mcp_camera_declares_view_field(self) -> None:
        messages = (addon_root() / "scripts/5_Mission/MCPMessages.c").read_text(
            encoding="utf-8"
        )
        block = messages[messages.index("class MCPCamera") :]
        self.assertIn("string view;", block.split("};", 1)[0])


class CameraGetRecipeTest(unittest.TestCase):
    def test_camera_get_description_names_states_and_no_poll_recipe(self) -> None:
        source = SERVER.read_text(encoding="utf-8")
        start = source.index('"Read the client camera through camera_get.')
        description = source[start : source.index("async def camera_get")]
        for token in (
            "view='player'",
            "view='scripted'",
            "camera_illegible_player_transform",
            "does not poll",
            "client_not_polling",
            "GetCurrentCameraTransform",
        ):
            self.assertIn(token, description)

    def test_restore_description_requires_view_player(self) -> None:
        source = SERVER.read_text(encoding="utf-8")
        start = source.index("release the camera. camera_set has no off mode.")
        description = source[start : source.index("async def restore_gameplay")]
        self.assertIn("camera.view='player'", description)
        self.assertIn("restore_unverified", description)
        self.assertIn("Do not treat a missing scripted camera as liberation", description)


class CaptureFocusBanTest(unittest.TestCase):
    def test_default_grab_method_is_printwindow(self) -> None:
        self.assertEqual(mcp_capture.DEFAULT_GRAB_METHOD, "printwindow")
        signature = inspect.signature(mcp_capture.capture_dual)
        self.assertEqual(
            signature.parameters["method"].default, mcp_capture.DEFAULT_GRAB_METHOD
        )

    def test_auto_path_never_force_foregrounds(self) -> None:
        source = GRAB.read_text(encoding="utf-8")
        auto_idx = source.index("if ($Method -eq 'printwindow' -or $Method -eq 'auto')")
        fg_opt_in = source.index("if ($Method -eq 'foreground')")
        self.assertLess(auto_idx, fg_opt_in)
        auto_block = source[auto_idx:fg_opt_in]
        self.assertNotIn("ForceForeground", auto_block)
        self.assertIn("ForceForeground", source[fg_opt_in:])
        self.assertNotIn("$Method -eq 'foreground' -or $Method -eq 'auto'", source)


if __name__ == "__main__":
    unittest.main()
