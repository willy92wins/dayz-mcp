"""Published-description caveats for batch n3_docs2: 449e and d490.

449e: the scene_raycast description must carry the rvproxy radius-zero
substitution (requested radius=0 currently runs with an effective radius of
0.05 m), the engine-returned pos without contact-point reconstruction, the
bullet branch that ignores radius, and the floor-experiment caveat that the
suggested sweep-centre offset is not promised for all surfaces. d490: the
capture_screenshot description must say the pre-run desktop probe is a
point-in-time check that neither keeps the display awake nor guarantees later
client captures. Wording is read from the PUBLISHED schemas
(resolve_effective_schemas), including the compact-catalog cut of the initial
discovery list; behavioral checks live in their own test class and pin the
behavior the wording describes (unchanged by this documentation batch).
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp.effective_schema import resolve_effective_schemas
from dayz_mcp.server import ServerConfig, ToolError, build_app
from dayz_mcp.tool_catalog import _INITIAL_CATALOG_NAMES, _compact_description
from tests.mcp_helpers import _content_json


class PublishedDescriptionCaveatsTest(unittest.TestCase):
    """449e/d490 wording on the post-build_app published metadata."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.schemas = resolve_effective_schemas()

    def test_scene_raycast_documents_radius_zero_substitution(self) -> None:
        description = self.schemas["scene_raycast"]["description"]
        self.assertIn(
            "a requested radius=0 currently runs with an effective radius of 0.05 m",
            description,
        )
        self.assertIn("positive values pass through", description)

    def test_scene_raycast_documents_pos_as_engine_returned(self) -> None:
        description = self.schemas["scene_raycast"]["description"]
        self.assertIn(
            "pos is the engine-returned position copied without contact-point "
            "reconstruction",
            description,
        )
        self.assertIn(
            "no universal sphere-centre or contact-point semantics are established",
            description,
        )

    def test_scene_raycast_documents_bullet_ignores_radius(self) -> None:
        description = self.schemas["scene_raycast"]["description"]
        self.assertIn(
            "With method='bullet', the implementation performs a raycast and does "
            "not use radius.",
            description,
        )

    def test_scene_raycast_limits_the_floor_experiment_interpretation(self) -> None:
        description = self.schemas["scene_raycast"]["description"]
        self.assertIn("a sweep-centre offset for that geometry", description)
        self.assertIn("not promised for all surfaces", description)

    def test_published_radius_default_is_0_05(self) -> None:
        params = self.schemas["scene_raycast"]["params"]
        self.assertEqual(params["radius"]["default"], 0.05)

    def test_capture_screenshot_limits_the_desktop_preflight_promises(self) -> None:
        description = self.schemas["capture_screenshot"]["description"]
        self.assertIn(
            "The preflight is a point-in-time check of current desktop "
            "accessibility and brightness",
            description,
        )
        self.assertIn(
            "it neither keeps the display awake nor guarantees later client captures",
            description,
        )
        # The display-sleep mechanism is a report, not an established cause.
        self.assertIn(
            "a report links a display that entered power-save after the probe "
            "to frame_client_all_black; that cause is not reproduced",
            description,
        )
        self.assertNotIn("can still produce frame_client_all_black", description)

    def test_compact_catalog_cut_of_scene_raycast_is_sentence_aligned(self) -> None:
        # scene_raycast rides the pre-lease compact catalog, so its published
        # description must survive _compact_description without a half-caveat.
        self.assertIn("scene_raycast", _INITIAL_CATALOG_NAMES)
        full = self.schemas["scene_raycast"]["description"]
        compact = _compact_description(full)
        if compact == full:
            self.assertLessEqual(len(full), 120)  # nothing was cut
            return
        self.assertTrue(compact.endswith(" …"), compact)
        kept = compact[:-2]
        self.assertTrue(full.startswith(kept))
        # The cut lands on a sentence boundary: every caveat sentence is either
        # fully published or not at all, never truncated mid-value.
        self.assertTrue(kept.endswith("."), kept)
        self.assertIn("Raycast through the server bridge", kept)

    def test_compact_cut_stays_sentence_aligned_for_capture_screenshot(self) -> None:
        full = self.schemas["capture_screenshot"]["description"]
        compact = _compact_description(full)
        if compact == full:
            return
        self.assertTrue(compact.endswith(" …"), compact)
        kept = compact[:-2]
        self.assertTrue(full.startswith(kept))
        self.assertTrue(kept.endswith("."), kept)


class SceneRaycastBehaviorUnchangedTest(unittest.IsolatedAsyncioTestCase):
    """Behavior the new wording describes: pinned unchanged (449e regression)."""

    async def test_radius_zero_is_accepted_and_forwarded_unchanged(self) -> None:
        app, runtime = build_app(ServerConfig(log_sink=lambda _message: None))
        bridge_hit = {
            "ok": True,
            "hit": {
                "hit": True,
                "pos": [1.0, 2.0, -0.05],
                "normal": [0.0, 0.0, 1.0],
                "distance": 10.05,
            },
        }
        with patch.object(
            runtime, "call_bridge", new=AsyncMock(return_value=bridge_hit)
        ) as call:
            result = await app.call_tool(
                "scene_raycast",
                {"from": [0.0, 0.0, 10.0], "to": [0.0, 0.0, 0.0], "radius": 0.0},
            )
        self.assertEqual(_content_json(result), bridge_hit)  # result shape passes through
        (cmd, args, peer, _timeout), _kwargs = call.await_args
        self.assertEqual(cmd, "scene_raycast")
        self.assertEqual(peer, "server")
        self.assertEqual(args["method"], "rvproxy")
        self.assertEqual(args["radius"], 0.0)  # zero reaches the bridge, not rewritten Python-side

    async def test_negative_radius_is_still_rejected_before_the_bridge(self) -> None:
        app, runtime = build_app(ServerConfig(log_sink=lambda _message: None))
        with patch.object(runtime, "call_bridge", new=AsyncMock()) as call:
            with self.assertRaises(ToolError) as raised:
                await app.call_tool(
                    "scene_raycast",
                    {"from": [0.0, 0.0, 10.0], "to": [0.0, 0.0, 0.0], "radius": -0.01},
                )
        self.assertIn("radius", str(raised.exception))
        self.assertIn("non-negative", str(raised.exception))
        call.assert_not_awaited()
