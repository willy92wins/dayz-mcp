"""ea1b: the published descriptions of camera_get and dev_free_camera_set say that camera_get does not see DayZDiag's
developer free camera.

Measured in game on 2026-11-20 (run 9f2f4872, REPORT-st4.md section E): after dev_free_camera_set(active=true) answered
active_before 0 and active_after 1, camera_get still answered camera.view='player' and viewport_moved=0 with the player
camera's pose, 8.0 mm from the one read with the free camera off (two reads with it off differ by up to 11 mm), while
the screenshot already showed the free camera's view.

The first review of the note added four pins:
- the claim cannot be swapped: both notes say that camera_get does not see the free camera;
- the way to tell is not a call that turns the free camera on: camera_get's note names dev_free_camera_set with
  active=false, whose active_before says whether it was on, and never active=true;
- the pose figure is not the z delta (7.8 mm) given as a distance;
- dev_free_camera_set's note bounds the player answer to what camera_get would answer with the free camera off (a
  mounted bridge camera answers 'scripted', a seat 'vehicle').

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

    def test_both_notes_say_that_camera_get_does_not_see_the_free_camera(self) -> None:
        about_cam = [s for s in _sentences("camera_get") if "dev_free_camera_set" in s]
        self.assertTrue(any("does not see" in s for s in about_cam), about_cam)
        about_dev = [s for s in _sentences("dev_free_camera_set") if "camera_get" in s]
        self.assertTrue(any("does not see" in s for s in about_dev), about_dev)

    def test_camera_get_names_a_check_that_does_not_turn_the_free_camera_on(self) -> None:
        text = _description("camera_get")
        self.assertIn("active=false", text)
        self.assertIn("active_before", text)
        self.assertIn("capture_screenshot", text)
        self.assertNotIn("active=true", text)

    def test_camera_get_does_not_give_the_z_delta_as_the_pose_distance(self) -> None:
        self.assertNotIn("7.8 mm", _description("camera_get"))

    def test_dev_free_camera_set_says_camera_get_does_not_see_it(self) -> None:
        about = [s for s in _sentences("dev_free_camera_set") if "camera_get" in s]
        self.assertTrue(about, "dev_free_camera_set's description does not name camera_get")
        self.assertTrue(any("view='player'" in s for s in about), about)

    def test_dev_free_camera_set_bounds_the_player_answer_to_the_off_state(self) -> None:
        about = [s for s in _sentences("dev_free_camera_set") if "camera_get" in s and "view='player'" in s]
        self.assertTrue(about, "no dev_free_camera_set sentence names camera_get with view='player'")
        self.assertTrue(all("with it off" in s for s in about), about)


if __name__ == "__main__":
    unittest.main()
