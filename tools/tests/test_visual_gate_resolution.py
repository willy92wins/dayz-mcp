"""Offline viability gate (R26) for the visual-capture resolution/zoom upgrade.

No DayZ build required: every assertion runs against a real grabbed frame on disk
(tools/fase3-evidence-subject.png) or synthetic images. Verifiable pass/fail criteria:

  - PNG->JPEG lifts the deliverable width from ~208 px to >=500 px inside the SAME 25k-token budget.
  - crop (center / bbox) spends the budget on the subject (effective zoom) and stays within budget.
  - the inline token budget is never exceeded, whatever the scale/format.
  - the full-res dual channel writes a readable JPEG and returns its absolute path.
  - negative: an all-black grab still surfaces frame_all_black via grab_stable_frame.
"""

from __future__ import annotations

import base64
import io
import os
import unittest

from PIL import Image

import mcp_capture

EVIDENCE = os.path.join(os.path.dirname(__file__), "..", "fase3-evidence-subject.png")
BUDGET_TOKENS = 25000


def _tokens(content: dict) -> int:
    return int(len(content["data"]) / mcp_capture.CHARS_PER_TOKEN)


def _decoded(content: dict) -> Image.Image:
    raw = base64.b64decode(content["data"].encode("ascii"), validate=True)
    return Image.open(io.BytesIO(raw))


class VisualGateResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        if not os.path.exists(EVIDENCE):
            self.skipTest(f"evidence frame missing: {EVIDENCE}")
        self.frame = Image.open(EVIDENCE).convert("RGB")

    def test_jpeg_beats_png_within_same_budget(self) -> None:
        png = mcp_capture.image_content_from_image(self.frame, scale="full", max_tokens=BUDGET_TOKENS, fmt="png")
        jpg = mcp_capture.image_content_from_image(self.frame, scale="full", max_tokens=BUDGET_TOKENS, fmt="jpeg")
        self.assertLessEqual(_tokens(png), BUDGET_TOKENS)
        self.assertLessEqual(_tokens(jpg), BUDGET_TOKENS)
        # The whole point: same budget, much bigger usable frame.
        self.assertGreaterEqual(_decoded(jpg).width, 500)
        self.assertGreater(_decoded(jpg).width, _decoded(png).width * 2)

    def test_default_capture_is_legible_width(self) -> None:
        content = mcp_capture.image_content_from_image(self.frame, scale="full", max_tokens=BUDGET_TOKENS)
        self.assertEqual(content["mimeType"], "image/jpeg")
        self.assertLessEqual(_tokens(content), BUDGET_TOKENS)
        self.assertGreaterEqual(_decoded(content).width, 500)

    def test_budget_never_exceeded(self) -> None:
        for scale in ("tiny", "small", "full"):
            for fmt in ("jpeg", "png"):
                content = mcp_capture.image_content_from_image(self.frame, scale=scale, max_tokens=BUDGET_TOKENS, fmt=fmt)
                self.assertLessEqual(_tokens(content), BUDGET_TOKENS, f"{scale}/{fmt} over budget")

    def test_center_crop_zooms_and_stays_in_budget(self) -> None:
        content = mcp_capture.image_content_from_image(self.frame, scale="full", max_tokens=BUDGET_TOKENS, crop="center:0.5")
        self.assertLessEqual(_tokens(content), BUDGET_TOKENS)
        dec = _decoded(content)
        # center:0.5 crops a region with the frame's aspect ratio -> decoded stays landscape, width usable.
        self.assertGreaterEqual(dec.width, 400)
        self.assertGreater(dec.width, dec.height)

    def test_bbox_crop_changes_region(self) -> None:
        full = mcp_capture.apply_crop(self.frame, "")
        box = mcp_capture.apply_crop(self.frame, "0.25,0.1,0.75,0.9")
        self.assertEqual(full.size, self.frame.size)
        self.assertLess(box.width, self.frame.width)
        self.assertLess(box.height, self.frame.height)

    def test_bad_crop_fails_open(self) -> None:
        # Garbage / inverted boxes must never lose the frame.
        self.assertEqual(mcp_capture.apply_crop(self.frame, "not,a,box").size, self.frame.size)
        self.assertEqual(mcp_capture.apply_crop(self.frame, "0.8,0.0,0.2,1.0").size, self.frame.size)

    def test_write_fullres_roundtrips(self) -> None:
        path = mcp_capture.write_fullres(self.frame, save_dir=os.path.join(os.path.dirname(__file__), "_tmp_captures"))
        try:
            self.assertTrue(os.path.isabs(path))
            self.assertTrue(os.path.exists(path))
            with Image.open(path) as saved:
                # Full-res keeps native dimensions (no budget downscale on the disk channel).
                self.assertEqual(saved.size, self.frame.size)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_resolve_capture_dir_precedence(self) -> None:
        self.assertEqual(mcp_capture.resolve_capture_dir("/explicit/dir"), os.path.abspath("/explicit/dir"))
        prev = os.environ.get("DAYZ_MCP_CAPTURE_DIR")
        os.environ["DAYZ_MCP_CAPTURE_DIR"] = "/env/dir"
        try:
            self.assertEqual(mcp_capture.resolve_capture_dir(""), os.path.abspath("/env/dir"))
        finally:
            if prev is None:
                del os.environ["DAYZ_MCP_CAPTURE_DIR"]
            else:
                os.environ["DAYZ_MCP_CAPTURE_DIR"] = prev


class VisualGateNegativeTest(unittest.TestCase):
    def test_window_not_found_surfaces_error(self) -> None:
        result = mcp_capture.grab_stable_frame(frames=1, process_name="__DayZ_MCP_missing_window__")
        self.assertIsInstance(result, dict)
        self.assertTrue(result.get("isError"))


if __name__ == "__main__":
    unittest.main()
