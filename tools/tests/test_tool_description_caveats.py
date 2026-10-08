"""Published tool-description caveats for batch b7_docs (4d7e, c879+769c, 1d31, 0eb8).

The contract a caller reads is the description FastMCP publishes after
build_app, so every wording assertion here goes through
effective_schema.resolve_effective_schemas -- the same list_tools path a
client sees -- and never greps server.py source. The compact pre-lease
catalog truncates descriptions for the tools it lists; these four tools are not
on it, so they are hidden there and only appear in the full catalog, with the
full description these tests assert. The one behavioral check (capture
save_fullres against a crop) lives in its own class, apart from the wording
checks.
"""
from __future__ import annotations

import functools
import hashlib
import os
import tempfile
import unittest
from unittest import mock

from PIL import Image

import mcp_capture
from dayz_mcp.effective_schema import resolve_effective_schemas
from tests._frame_state_isolation import isolate_capture_frame_state


@functools.lru_cache(maxsize=1)
def _published() -> dict[str, dict]:
    """The post-build_app published schema of every tool, resolved once."""
    return resolve_effective_schemas()


def _description(name: str) -> str:
    return _published()[name]["description"]


class CaptureScreenshotFullresWordingTest(unittest.TestCase):
    """4d7e: fullres_path is the cropped effective surface, not the whole window."""

    def test_fullres_is_described_as_the_effective_surface(self) -> None:
        text = _description("capture_screenshot")
        self.assertIn(
            "save_fullres=True also writes the native-resolution effective_surface",
            text,
        )
        self.assertIn("after client-area selection and cropping", text)
        self.assertIn("before inline downscaling", text)
        self.assertIn(
            "the effective_surface dimensions — not the whole window's — apply to the file",
            text,
        )
        # The key the caller reads the geometry from is named on the same sentence.
        self.assertIn("fullres_path", text)


class PlayerTeleportSettlementWordingTest(unittest.TestCase):
    """c879+769c: assigned position vs settled physics, documented honestly."""

    def test_assignment_is_distinguished_from_settled_physics(self) -> None:
        text = _description("player_teleport")
        self.assertIn("position assignment (SetPosition)", text)
        self.assertIn("does not attest", text)
        self.assertIn("settled at that position", text)
        self.assertIn("moving floor keeps carrying the player", text)
        # The reported hover is attributed, not asserted as engine fact.
        self.assertIn("reported, not reproduced", text)
        # No automatic settle operation is promised.
        self.assertIn("There is no settle operation", text)

    def test_movement_workaround_is_described_as_real_displacement(self) -> None:
        text = _description("player_teleport")
        self.assertIn('player_move(speed="walk", phase="hold", hold_s=1)', text)
        # Restricted to the intended local player: teleport may select a uid,
        # player_move may not.
        self.assertIn("drives the local on-foot player only", text)
        self.assertIn("requests real movement", text)
        self.assertIn("does not guarantee displacement or settlement", text)
        # phase="release" alone does not move anyone: no claim about every phase.
        self.assertNotIn("every phase", text)
        self.assertIn("no zero-speed hold exists", text)


class KeyPressRouteWordingTest(unittest.TestCase):
    """1d31: key_press is mission-only; game-level keys go through input_trigger."""

    def test_game_level_route_is_input_trigger_with_game_entry(self) -> None:
        text = _description("key_press")
        self.assertIn("Mission.OnKeyPress", text)
        self.assertIn("the mission handler only", text)
        self.assertIn(
            'input_trigger(kind="key", dik=1, entry="game", phase="click")',
            text,
        )
        self.assertIn("DayZGame.OnKeyPress/OnKeyRelease", text)

    def test_delivery_is_not_confirmation_of_the_ui_effect(self) -> None:
        text = _description("key_press")
        self.assertIn("not confirmation", text)
        self.assertIn("ui_tree", text)
        # No claim that the delivered key closes any menu is published.
        self.assertNotIn("closes the menu", text)
        self.assertNotIn("closes an open menu", text)


class WorldSpawnInfectedWordingTest(unittest.TestCase):
    """0eb8: infected without ECE_INITAI is not a durable living fixture."""

    def test_ai_less_infected_is_qualified_as_reported_and_unverified(self) -> None:
        text = _description("world_spawn")
        self.assertIn(
            "Spawning an infected without ECE_INITAI does not establish a "
            "durable living visual fixture",
            text,
        )
        # The delayed health-zero observation is attributed, not proven.
        self.assertIn("was reported", text)
        self.assertIn("the cause is unverified", text)

    def test_health_check_and_living_recipe_are_named_without_guarantees(self) -> None:
        text = _description("world_spawn")
        self.assertIn("health01", text)
        self.assertIn("immediately", text)
        self.assertIn("flags=3108", text)
        self.assertIn("does not guarantee survival", text)


class TextureStreamingCaveatWordingTest(unittest.TestCase):
    """c440: frame_stale=false is not a loaded scene; a long teleport streams."""

    def test_frame_stale_false_is_not_a_finished_load(self) -> None:
        text = _description("capture_screenshot")
        self.assertIn("frame_stale=false", text)
        self.assertIn("means the render advanced, not that the scene finished loading", text)
        self.assertIn("textures and terrain can still be streaming", text)
        self.assertIn("vanilla Chernarus and a custom map", text)
        self.assertIn("the cause, focus or distance, is not established", text)
        self.assertIn("No capture field certifies that streaming finished", text)
        self.assertIn("the caller looks at the image", text)

    def test_long_teleport_capture_can_be_untextured(self) -> None:
        text = _description("player_teleport")
        self.assertIn("After a long teleport the client streams the new area", text)
        self.assertIn("a capture taken right after it can show an untextured scene", text)
        self.assertIn("for minutes on an unfocused client", text)
        self.assertIn("reported; not a measured duration", text)
        # A duration must not be promised as a rule.
        self.assertNotIn("always untextured for", text)


class PublishedContractPreservedTest(unittest.TestCase):
    """The clarifications touch wording only: parameters and masks stay put."""

    def test_player_teleport_keeps_its_parameters_and_adds_no_settle(self) -> None:
        params = _published()["player_teleport"]["params"]
        self.assertEqual(
            {"pos", "uid", "skip_clearance_check", "timeout_s"}, set(params)
        )
        self.assertTrue(params["pos"]["required"])
        self.assertFalse(params["uid"]["required"])
        self.assertFalse(params["skip_clearance_check"]["required"])

    def test_key_press_parameter_is_dik_not_key(self) -> None:
        params = _published()["key_press"]["params"]
        self.assertEqual({"dik", "timeout_s"}, set(params))
        self.assertTrue(params["dik"]["required"])
        self.assertNotIn("key", params)

    def test_world_spawn_flags_masks_are_unchanged(self) -> None:
        params = _published()["world_spawn"]["params"]
        self.assertIn("flags", params)
        self.assertEqual(0, params["flags"]["default"])
        self.assertFalse(params["flags"]["required"])
        text = _description("world_spawn")
        self.assertIn("exact pair ECE_CREATEPHYSICS|ECE_TRACE", text)
        self.assertIn("ECE_PLACE_ON_SURFACE|ECE_NOPERSISTENCY_WORLD", text)
        self.assertIn("flags=4718592", text)
        self.assertIn("Unknown bits also return bad_flags", text)


class CaveatDiscoveryPathTest(unittest.TestCase):
    """The four tools never ride the compact pre-lease catalog.

    _compact_description can drop a late sentence, but only for tools listed
    in _INITIAL_CATALOG_NAMES; none of the four is there, so the compact
    pre-lease catalog does not show them at all, and the catalog that does list
    them is the full one, with the full description the wording tests assert.
    """

    def test_caveated_tools_are_absent_from_the_compact_catalog(self) -> None:
        from dayz_mcp.tool_catalog import _INITIAL_CATALOG_NAMES

        for name in (
            "capture_screenshot",
            "player_teleport",
            "key_press",
            "world_spawn",
        ):
            self.assertNotIn(name, _INITIAL_CATALOG_NAMES)
            self.assertTrue(_description(name))


class CaptureFullresCropBehaviorTest(unittest.TestCase):
    """4d7e failure scenario, measured on capture_dual.

    crop="0.2,0.0,0.8,1.0" with save_fullres=True: the saved file IS the crop
    (36x60 of a 60x60 client viewport inside a 100x80 window), so applying
    whole-window coordinates to the file selects the wrong region. Kept in a
    separate class from the wording checks above.
    """

    def setUp(self) -> None:
        isolate_capture_frame_state(self)

    WINDOW_W, WINDOW_H = 100, 80
    CLIENT_RECT = (10, 10, 60, 60)  # left, top, width, height, window space
    BLUE_RECT = (30, 20, 10, 10)  # visible only inside the crop, window space
    RED, GREEN, BLUE = (200, 20, 20), (20, 200, 20), (20, 20, 200)

    def _paint(self, width: int, height: int) -> Image.Image:
        """The fixture bitmap, painted from this geometry, not cropped."""
        img = Image.new("RGB", (width, height), self.RED)
        for rect, color in ((self.CLIENT_RECT, self.GREEN), (self.BLUE_RECT, self.BLUE)):
            left, top, w, h = rect
            for x in range(left, min(left + w, width)):
                for y in range(top, min(top + h, height)):
                    img.putpixel((x, y), color)
        return img

    def test_fullres_file_is_the_crop_and_window_coordinates_misread_it(self) -> None:
        window_meta = {
            "pid": 4242,
            "class": "DayZ",
            "title": "fixture",
            "left": 100,
            "top": 50,
            "width": self.WINDOW_W,
            "height": self.WINDOW_H,
        }

        def backend(output_path: str, **_: object) -> dict[str, object]:
            self._paint(self.WINDOW_W, self.WINDOW_H).save(output_path, format="PNG")
            return {
                "ok": True,
                "error": "",
                "method": "printwindow",
                "window": window_meta,
                "stats": {"meanBrightness": 90.0, "nonBlackRatio": 1.0},
                "sha256": "f" * 64,
                "clientStats": {"meanBrightness": 104.75, "nonBlackRatio": 1.0},
                "client": {"left": 10, "top": 10, "width": 60, "height": 60},
            }

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(mcp_capture, "_run_window_capture", side_effect=backend):
                result = mcp_capture.capture_dual(
                    frames=1,
                    crop="0.2,0.0,0.8,1.0",
                    save_fullres=True,
                    save_dir=tmp,
                    fmt="png",
                )

            self.assertNotIn("isError", result)
            meta = result["meta"]
            effective = meta["effective_surface"]
            # Client-space crop 0.2..0.8 x 0..1 of the 60x60 viewport: 36x60
            # at window rect (22, 10).
            self.assertEqual((36, 60), (effective["native_width"], effective["native_height"]))
            self.assertEqual(
                {"left": 22, "top": 10, "width": 36, "height": 60},
                effective["rect_window"],
            )
            path = result["fullres_path"]
            self.assertTrue(path and os.path.isabs(path))
            with open(path, "rb") as handle:
                self.assertEqual(
                    hashlib.sha256(handle.read()).hexdigest(),
                    meta["fullres_file_sha256"],
                )
            with Image.open(path) as saved:
                self.assertEqual((36, 60), saved.size)
                rgb = saved.convert("RGB")
                # The file's origin is the crop origin (green viewport), not
                # the window origin (red chrome): whole-window coordinates
                # applied to this file select the wrong region.
                origin = rgb.getpixel((0, 0))
                self.assertLess(abs(origin[0] - self.GREEN[0]), 8)
                self.assertLess(abs(origin[1] - self.GREEN[1]), 8)
                # The blue block at window (30, 20) lands at crop (8, 10).
                inside_block = rgb.getpixel((12, 15))
                self.assertLess(abs(inside_block[2] - self.BLUE[2]), 8)
                self.assertGreater(inside_block[2], inside_block[0])


if __name__ == "__main__":
    unittest.main()
