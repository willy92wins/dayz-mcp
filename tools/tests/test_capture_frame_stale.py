"""Frozen-frame contract of capture_screenshot (fb-20260904-145230-6d22, fb-20260904-025027-8f76 a).

Run:
    cd tools && ./.venv-mcp/Scripts/python.exe -m unittest tests.test_capture_frame_stale -v

HOW THE IMAGE IS FABRICATED -- no game, no window, no GPU.
`mcp_capture._run_window_capture` (mcp_capture.py:477) is the ONLY seam to the host: it
shells out to mcp-grab.ps1 and returns {ok, method, window, stats, sha256, client,
clientStats} after writing a PNG to `output_path`. Every test below replaces it with
`mock.patch.object(..., side_effect=fake)`, exactly like the two tests already in
tools/tests/test_mcp_capture.py:56-89. The fake writes a synthetic PIL image and returns a
synthetic backend payload, so "the same frame twice" is literally the same RGB bytes and
"a different frame" is one changed block. Determinism is total: every assertion is about
sha256 equality of bytes this file creates.

CONTRACT UNDER TEST (see reviews/2026-09-04-reserva/R1-capture-6d22-diseno.md, Option 1):
  meta.frame_stale         bool | None   None = no comparable baseline (first capture,
                                         surface changed, or state store unavailable)
  meta.frame_stale_detail  dict          key, surface, current_sha256, previous_sha256,
                                         same_as_capture_ts, age_s, repeat_count, frames,
                                         distinct_frames, max_adjacent_delta,
                                         state_backend, state_error
  mcp_capture.frame_state_path()         env DAYZ_MCP_FRAME_STATE_PATH >
                                         %LOCALAPPDATA%\\DayZ_MCP\\capture-frame-state.json
  sidecar JSON  {"version": 1, "windows": {<key>: {surface, sha256, ts, repeat_count}}}
"""

from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from unittest import mock

from PIL import Image, ImageDraw

import mcp_capture


WINDOW = {"pid": 4242, "class": "DayZ", "title": "DayZ", "left": 0, "top": 0, "width": 400, "height": 300}
CLIENT_RECT = {"left": 0, "top": 20, "width": 400, "height": 280}
LIVE_STATS = {"meanBrightness": 96.0, "nonBlackRatio": 1.0}
BLACK_STATS = {"meanBrightness": 0.0, "nonBlackRatio": 0.0}
CMDLINE = r"P:\DayZ_MCP_dev\_client\profiles"


def _frame(seed: int, black: bool = False) -> Image.Image:
    """Deterministic synthetic window bitmap. Same seed => byte-identical RGB."""
    base = (0, 0, 0) if black else (40, 60, 80)
    image = Image.new("RGB", (WINDOW["width"], WINDOW["height"]), base)
    if not black:
        draw = ImageDraw.Draw(image)
        # A block whose colour is a pure function of the seed: two different seeds differ
        # in pixels, the same seed does not.
        draw.rectangle([50, 60, 350, 240], fill=(seed % 256, 128, 200))
    return image


def _backend(seed: int, black: bool = False, distinct_per_frame: bool = False):
    """Fake _run_window_capture. distinct_per_frame=True gives every grab of the SAME call
    a different image, which is what a healthy render looks like."""
    counter = {"n": 0}

    def fake(output_path: str, **_: object) -> dict[str, object]:
        counter["n"] += 1
        effective = seed + (counter["n"] if distinct_per_frame else 0)
        _frame(effective, black=black).save(output_path, format="PNG")
        return {
            "ok": True,
            "error": "",
            "method": "printwindow",
            "window": dict(WINDOW),
            "client": dict(CLIENT_RECT),
            "clientStats": dict(BLACK_STATS if black else LIVE_STATS),
            "sha256": "f" * 64,
        }

    return fake


class FrameStaleTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="frame_state_")
        self.addCleanup(self._tmp.cleanup)
        self.state_path = os.path.join(self._tmp.name, "capture-frame-state.json")
        patcher = mock.patch.dict(os.environ, {"DAYZ_MCP_FRAME_STATE_PATH": self.state_path})
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- helpers ---------------------------------------------------------------
    def _capture(self, seed: int, black: bool = False, distinct_per_frame: bool = False, frames: int = 1):
        with mock.patch.object(
            mcp_capture, "_run_window_capture", side_effect=_backend(seed, black, distinct_per_frame)
        ):
            return mcp_capture.capture_dual(frames=frames, cmdline_match=CMDLINE, scale=128)

    def _sidecar(self) -> dict:
        with open(self.state_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _sidecar_key(self) -> str:
        keys = list(self._sidecar()["windows"].keys())
        self.assertEqual(1, len(keys), "one window under test")
        return keys[0]

    # -- 1. same pixels twice => frame_stale True ------------------------------
    def test_second_capture_with_identical_pixels_is_declared_stale(self) -> None:
        first = self._capture(seed=7)
        second = self._capture(seed=7)

        self.assertIsNone(first["meta"]["frame_stale"], "first capture has no baseline")
        self.assertIs(True, second["meta"]["frame_stale"])
        detail = second["meta"]["frame_stale_detail"]
        self.assertEqual(detail["current_sha256"], detail["previous_sha256"])
        self.assertEqual(2, detail["repeat_count"])
        self.assertIsNotNone(detail["same_as_capture_ts"])
        self.assertIsInstance(detail["age_s"], float)
        # The flag must not cost the image: the capture is still delivered.
        self.assertIn("data", second["inline"])

    # -- 2. different pixels => frame_stale False ------------------------------
    def test_capture_with_changed_pixels_is_not_stale(self) -> None:
        self._capture(seed=7)
        second = self._capture(seed=200)

        self.assertIs(False, second["meta"]["frame_stale"])
        detail = second["meta"]["frame_stale_detail"]
        self.assertNotEqual(detail["current_sha256"], detail["previous_sha256"])
        self.assertEqual(1, detail["repeat_count"])
        self.assertIsNone(detail["same_as_capture_ts"])

    # -- 3. first capture => None, never False ---------------------------------
    def test_first_capture_reports_null_not_false(self) -> None:
        meta = self._capture(seed=7)["meta"]

        self.assertIsNone(meta["frame_stale"])
        detail = meta["frame_stale_detail"]
        self.assertIsNone(detail["previous_sha256"])
        self.assertEqual(1, detail["repeat_count"])
        self.assertEqual("sidecar", detail["state_backend"])
        self.assertIsNone(detail["state_error"])

    # -- 4. repeat_count keeps counting, and resets ----------------------------
    def test_repeat_count_grows_while_the_frame_does_not_change(self) -> None:
        self._capture(seed=7)
        self._capture(seed=7)
        third = self._capture(seed=7)

        self.assertIs(True, third["meta"]["frame_stale"])
        self.assertEqual(3, third["meta"]["frame_stale_detail"]["repeat_count"])
        # ...and resets the moment the render advances.
        fourth = self._capture(seed=99)
        self.assertEqual(1, fourth["meta"]["frame_stale_detail"]["repeat_count"])

    # -- 5. all-black AND stale ------------------------------------------------
    def test_all_black_capture_still_records_state_and_keeps_its_own_error(self) -> None:
        first = self._capture(seed=1, black=True)
        second = self._capture(seed=1, black=True)

        # The existing fail-closed error is NOT weakened (mcp_capture.py:583).
        for result in (first, second):
            self.assertIs(True, result.get("isError"))
            self.assertEqual("frame_client_all_black", result.get("error"))
        # But the frame identity IS recorded, so the next successful capture -- and any
        # later wire-side enrichment -- can say "black since T, 2 captures in a row".
        record = self._sidecar()["windows"][self._sidecar_key()]
        self.assertEqual(2, record["repeat_count"])

    # -- 6. survives a process restart -----------------------------------------
    def test_state_survives_a_process_restart_because_it_lives_on_disk(self) -> None:
        self._capture(seed=7)

        # A brand-new process would re-import the module with empty globals. reload() is
        # the honest simulation: it wipes any in-process cache and keeps the file.
        importlib.reload(mcp_capture)
        self.addCleanup(importlib.reload, mcp_capture)

        after_restart = self._capture(seed=7)
        self.assertIs(True, after_restart["meta"]["frame_stale"])
        self.assertEqual(2, after_restart["meta"]["frame_stale_detail"]["repeat_count"])

    # -- 7. intra-call evidence, available on the FIRST capture ----------------
    def test_intra_call_frame_distinctness_is_published(self) -> None:
        frozen = self._capture(seed=7, frames=4)
        live = self._capture(seed=500, frames=4, distinct_per_frame=True)

        self.assertEqual(4, frozen["meta"]["frame_stale_detail"]["frames"])
        self.assertEqual(1, frozen["meta"]["frame_stale_detail"]["distinct_frames"])
        self.assertEqual(0.0, frozen["meta"]["frame_stale_detail"]["max_adjacent_delta"])
        self.assertEqual(4, live["meta"]["frame_stale_detail"]["distinct_frames"])
        self.assertGreater(live["meta"]["frame_stale_detail"]["max_adjacent_delta"], 0.0)

    # -- 8. an unusable state store never costs a capture ----------------------
    def test_unwritable_state_store_fails_open_to_null(self) -> None:
        with mock.patch.object(mcp_capture, "_write_frame_state", side_effect=OSError("denied")):
            result = self._capture(seed=7)

        self.assertIsNone(result["meta"]["frame_stale"])
        detail = result["meta"]["frame_stale_detail"]
        self.assertEqual("unavailable", detail["state_backend"])
        self.assertIsInstance(detail["state_error"], str)
        self.assertIn("data", result["inline"], "the image is delivered anyway")


if __name__ == "__main__":
    unittest.main()
