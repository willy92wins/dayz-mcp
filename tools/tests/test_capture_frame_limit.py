"""Frame-cap visibility: a request above 5 is still 5 grabs, and the evidence says so.

The cap itself is unchanged. A failed grab stays an error; frames_capped is a warning.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from PIL import Image

import mcp_capture


WINDOW = {"pid": 4242, "class": "DayZ", "title": "DayZ", "left": 0, "top": 0, "width": 40, "height": 30}
CLIENT_RECT = {"left": 0, "top": 0, "width": 40, "height": 30}
LIVE_STATS = {"meanBrightness": 96.0, "nonBlackRatio": 1.0}
# Index 1 and 2 are the only close pair, so the stable pick is index 1 (lowest tie), not 0.
_COLORS = (
    (255, 0, 0),
    (10, 0, 0),
    (12, 0, 0),
    (200, 0, 0),
    (0, 255, 0),
    (0, 0, 255),
)


def _backend(calls: list[int]):
    def fake(output_path: str, **_: object) -> dict[str, object]:
        index = len(calls)
        calls.append(index)
        color = _COLORS[index] if index < len(_COLORS) else (index % 256, 1, 2)
        Image.new("RGB", (WINDOW["width"], WINDOW["height"]), color).save(output_path, format="PNG")
        return {
            "ok": True,
            "error": "",
            "method": "printwindow",
            "window": dict(WINDOW),
            "client": dict(CLIENT_RECT),
            "clientStats": dict(LIVE_STATS),
            "sha256": "a" * 64,
        }

    return fake


class FrameLimitEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="frame_limit_")
        self.addCleanup(self._tmp.cleanup)
        state_path = os.path.join(self._tmp.name, "capture-frame-state.json")
        patcher = mock.patch.dict(os.environ, {"DAYZ_MCP_FRAME_STATE_PATH": state_path})
        patcher.start()
        self.addCleanup(patcher.stop)
        sleep = mock.patch.object(mcp_capture.time, "sleep", return_value=None)
        sleep.start()
        self.addCleanup(sleep.stop)

    def _grab(self, frames: int, calls: list[int]) -> Image.Image | dict:
        with mock.patch.object(mcp_capture, "_run_window_capture", side_effect=_backend(calls)):
            return mcp_capture.grab_stable_frame(frames=frames, cmdline_match="frame-limit-test")

    def _dual(self, frames: int, calls: list[int]) -> dict:
        with mock.patch.object(mcp_capture, "_run_window_capture", side_effect=_backend(calls)):
            return mcp_capture.capture_dual(frames=frames, cmdline_match="frame-limit-test", scale=32)

    def _legacy(self, frames: int, calls: list[int]) -> dict:
        with mock.patch.object(mcp_capture, "_run_window_capture", side_effect=_backend(calls)):
            return mcp_capture.capture_screenshot(frames=frames, cmdline_match="frame-limit-test", scale=32)

    def test_twelve_frames_grab_five_and_warn(self) -> None:
        calls: list[int] = []
        result = self._dual(12, calls)
        self.assertNotIn("isError", result)
        self.assertEqual([0, 1, 2, 3, 4], calls)
        detail = result["meta"]["frame_stale_detail"]
        self.assertEqual(12, detail["requested_frames"])
        self.assertEqual(5, detail["effective_frames"])
        self.assertEqual(5, detail["frame_limit"])
        self.assertEqual("frame_limit", detail["limit_reason"])
        self.assertEqual(5, detail["frames"])
        self.assertIn("frames_capped", result["meta"]["warnings"])

    def test_four_frames_are_not_capped(self) -> None:
        calls: list[int] = []
        result = self._dual(4, calls)
        self.assertEqual([0, 1, 2, 3], calls)
        detail = result["meta"]["frame_stale_detail"]
        self.assertEqual(4, detail["requested_frames"])
        self.assertEqual(4, detail["effective_frames"])
        self.assertEqual(5, detail["frame_limit"])
        self.assertNotIn("limit_reason", detail)
        self.assertNotIn("frames_capped", result["meta"].get("warnings", []))

    def test_legacy_entry_publishes_the_same_cap(self) -> None:
        capped_calls: list[int] = []
        capped = self._legacy(12, capped_calls)
        self.assertNotIn("isError", capped)
        self.assertEqual(5, len(capped_calls))
        detail = capped["meta"]["frame_stale_detail"]
        self.assertEqual(12, detail["requested_frames"])
        self.assertEqual(5, detail["effective_frames"])
        self.assertEqual("frame_limit", detail["limit_reason"])
        self.assertIn("frames_capped", capped["meta"]["warnings"])

        plain_calls: list[int] = []
        plain = self._legacy(4, plain_calls)
        self.assertEqual(4, len(plain_calls))
        plain_detail = plain["meta"]["frame_stale_detail"]
        self.assertEqual(4, plain_detail["requested_frames"])
        self.assertEqual(4, plain_detail["effective_frames"])
        self.assertNotIn("limit_reason", plain_detail)
        self.assertNotIn("frames_capped", plain["meta"].get("warnings", []))

    def test_frame_selection_is_unchanged_by_the_cap(self) -> None:
        twelve: list[int] = []
        capped = self._grab(12, twelve)
        five: list[int] = []
        full = self._grab(5, five)
        self.assertIsInstance(capped, Image.Image)
        self.assertIsInstance(full, Image.Image)
        self.assertEqual(5, len(twelve))
        self.assertEqual(5, len(five))
        assert isinstance(capped, Image.Image) and isinstance(full, Image.Image)
        self.assertEqual(full.tobytes(), capped.tobytes())
        # The pick among the five grabbed frames is still the lowest-score neighbour, index 1.
        palette = [Image.new("RGB", (40, 30), color) for color in _COLORS[:5]]
        self.assertEqual(1, mcp_capture._stable_frame_index(palette, mcp_capture._adjacent_pair_deltas(palette)))
        self.assertEqual(palette[1].tobytes(), capped.tobytes())

    def test_tool_description_states_the_frame_limit(self) -> None:
        from dayz_mcp.server import ServerConfig, build_app

        app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))
        tool = app._tool_manager.get_tool("capture_screenshot")
        description = tool.description or ""
        self.assertIn("frames_capped", description)
        self.assertIn("frame_limit", description)
        self.assertIn("requested_frames", description)
        self.assertIn("above 5 frames", description)


if __name__ == "__main__":
    unittest.main()
