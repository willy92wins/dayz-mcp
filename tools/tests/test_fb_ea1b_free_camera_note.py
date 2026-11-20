"""ea1b: the published descriptions of camera_get and dev_free_camera_set say that camera_get does not see DayZDiag's
developer free camera.

Measured in game on 2026-11-20 (run 9f2f4872, REPORT-st4.md section E): after dev_free_camera_set(active=true) answered
active_after true, camera_get still answered camera.view='player' and viewport_moved=0, with a pose within 7.8 mm of
the one read with the free camera off, while the screenshot already showed the free camera's view. Only
dev_free_camera_set's active_before/active_after or a capture tells whether the free camera is on.

The assertions read the descriptions a client gets (resolve_effective_schemas, the list_tools path) and split them into
sentences at the whitespace after a full stop.
"""

from __future__ import annotations

import functools
import re
import unittest

from dayz_mcp.effective_schema import resolve_effective_schemas


@functools.lru_cache(maxsize=1)
def _schemas() -> dict:
    return resolve_effective_schemas()


def _description(tool: str) -> str:
    return _schemas()[tool]["description"]


def _sentences(tool: str) -> list[str]:
    return [s for s in re.split(r"(?<=\.)\s+", _description(tool)) if s.strip()]


class FreeCameraNoteTest(unittest.TestCase):
    def test_camera_get_says_it_answers_player_with_the_free_camera_on(self) -> None:
        about = [s for s in _sentences("camera_get") if "dev_free_camera_set" in s]
        self.assertTrue(about, "camera_get's description does not name dev_free_camera_set")
        self.assertTrue(any("view='player'" in s for s in about), about)

    def test_camera_get_gives_the_measured_pose_difference(self) -> None:
        self.assertIn("7.8 mm", _description("camera_get"))

    def test_camera_get_names_what_tells_the_free_camera_is_on(self) -> None:
        text = _description("camera_get")
        self.assertIn("active_after", text)
        self.assertIn("capture_screenshot", text)

    def test_dev_free_camera_set_says_camera_get_does_not_see_it(self) -> None:
        about = [s for s in _sentences("dev_free_camera_set") if "camera_get" in s]
        self.assertTrue(about, "dev_free_camera_set's description does not name camera_get")
        self.assertTrue(any("view='player'" in s for s in about), about)


if __name__ == "__main__":
    unittest.main()
