"""BUG-112: the stable frame is the calmest pair's, wherever that pair sits.

choose_stable_frame, and grab_stable_frame on the capture path, keep the frame
whose closest neighbour moved least. The only integration case until now
(test_mcp_capture, test_grab_stable_frame_keeps_the_chosen_frames_client_rect)
has three frames whose calmest pair includes the centre, so a choice reduced to
frames[len(frames) // 2] passed it. Every case here keeps the calmest pair away
from index len(frames) // 2, so that reduction fails them.

Frames are solid grey, so the delta between two of them is exactly the
difference of their levels. Where the calmest pair sits is checked from those
levels, never from the helpers under test; both frames of that pair score the
same, and the choice keeps the first of them.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from PIL import Image

import mcp_capture

SIZE = (16, 16)

# label -> (grey level of each frame, first index of the calmest adjacent pair)
CASES = {
    "4 frames, calmest pair first": ((100, 102, 160, 30), 0),
    "5 frames, calmest pair first": ((100, 102, 160, 90, 20), 0),
    "5 frames, calmest pair last": ((20, 90, 160, 100, 102), 3),
}


def _frame(level: int) -> Image.Image:
    return Image.new("RGB", SIZE, (level, level, level))


class _CalmestPairCase(unittest.TestCase):
    def assert_fixture_keeps_the_centre_out(self, levels: tuple[int, ...], chosen: int) -> None:
        """The calmest adjacent pair is (chosen, chosen + 1), unique, and not the centre."""
        steps = [abs(left - right) for left, right in zip(levels, levels[1:])]
        self.assertEqual(steps.count(min(steps)), 1, steps)
        self.assertEqual(steps.index(min(steps)), chosen, steps)
        self.assertNotIn(len(levels) // 2, (chosen, chosen + 1))


class ChooseStableFrameTest(_CalmestPairCase):
    def test_the_calmest_pair_wins_when_it_is_not_the_centre(self) -> None:
        for label, (levels, chosen) in CASES.items():
            with self.subTest(label):
                self.assert_fixture_keeps_the_centre_out(levels, chosen)
                frames = [_frame(level) for level in levels]
                self.assertIs(frames[chosen], mcp_capture.choose_stable_frame(frames))


class GrabStableFrameTest(_CalmestPairCase):
    def _grab(self, levels: tuple[int, ...]) -> object:
        grabbed = 0

        def capture_frame(output_path: str, **_: object) -> dict[str, object]:
            nonlocal grabbed
            index = grabbed
            grabbed += 1
            _frame(levels[index]).save(output_path, format="PNG")
            return {
                "ok": True,
                "error": "",
                "method": "printwindow",
                "window": {
                    "pid": 4242,
                    "class": "DayZ",
                    "title": "fixture",
                    "left": 0,
                    "top": 0,
                    "width": SIZE[0],
                    "height": SIZE[1],
                },
                "stats": {"meanBrightness": float(levels[index]), "nonBlackRatio": 1.0},
                # Tells the frames apart on the returned image's info.
                "sha256": f"{index:064x}",
                "client": {"left": 0, "top": 0, "width": SIZE[0], "height": SIZE[1]},
                "clientStats": {"meanBrightness": float(levels[index]), "nonBlackRatio": 1.0},
            }

        with tempfile.TemporaryDirectory(prefix="frame_state_") as tmp:
            state_path = os.path.join(tmp, "capture-frame-state.json")
            with (
                mock.patch.dict(os.environ, {mcp_capture.FRAME_STATE_ENV: state_path}),
                mock.patch.object(mcp_capture, "DEFAULT_FRAME_INTERVAL_S", 0.0),
                mock.patch.object(
                    mcp_capture, "_run_window_capture", side_effect=capture_frame
                ),
            ):
                frame = mcp_capture.grab_stable_frame(frames=len(levels))
        self.assertEqual(len(levels), grabbed)
        return frame

    def test_the_grab_keeps_the_calmest_frame_when_it_is_not_the_centre(self) -> None:
        for label, (levels, chosen) in CASES.items():
            with self.subTest(label):
                self.assert_fixture_keeps_the_centre_out(levels, chosen)
                frame = self._grab(levels)
                self.assertIsInstance(frame, Image.Image)
                self.assertEqual(f"{chosen:064x}", frame.info.get("sha256"))
                self.assertEqual((levels[chosen],) * 3, frame.getpixel((0, 0)))


if __name__ == "__main__":
    unittest.main()
