from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from PIL import Image, ImageDraw

import mcp_capture


class MCPCaptureTest(unittest.TestCase):
    def test_grab_self_test_covers_client_area_liveness_contract(self) -> None:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                mcp_capture.GRAB_SCRIPT,
                "-SelfTest",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20.0,
            check=False,
        )

        self.assertEqual(0, proc.returncode, proc.stderr)
        stdout_lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        self.assertTrue(stdout_lines, proc.stderr)
        payload = json.loads(stdout_lines[-1])
        self.assertIs(payload.get("ok"), True)
        self.assertEqual(
            {
                "whole_live_client_black": True,
                "client_live": True,
                "roi_null_fail_closed": True,
                "roi_empty_fail_closed": True,
                "roi_out_of_bounds_fail_closed": True,
                "mean_boundary_strict": True,
                "ratio_boundary_strict": True,
                "above_boundaries_live": True,
            },
            payload.get("cases"),
        )

    def test_stable_frame_rejects_titlebar_only_client_area(self) -> None:
        def titlebar_only(output_path: str, **_: object) -> dict[str, object]:
            image = Image.new("RGB", (200, 120), (0, 0, 0))
            ImageDraw.Draw(image).rectangle([0, 0, 199, 19], fill=(180, 180, 180))
            image.save(output_path, format="PNG")
            return {
                "ok": True,
                "error": "",
                "method": "printwindow",
                "clientStats": {"meanBrightness": 0.0, "nonBlackRatio": 0.0},
            }

        with mock.patch.object(mcp_capture, "_run_window_capture", side_effect=titlebar_only):
            result = mcp_capture.grab_stable_frame(frames=1)

        self.assertIsInstance(result, dict)
        self.assertEqual("frame_client_all_black", result.get("error"))

    def test_stable_frame_accepts_nonblack_client_area(self) -> None:
        def live_client(output_path: str, **_: object) -> dict[str, object]:
            Image.new("RGB", (200, 120), (80, 100, 120)).save(output_path, format="PNG")
            return {
                "ok": True,
                "error": "",
                "method": "printwindow",
                "clientStats": {"meanBrightness": 96.0, "nonBlackRatio": 1.0},
            }

        with mock.patch.object(mcp_capture, "_run_window_capture", side_effect=live_client):
            result = mcp_capture.grab_stable_frame(frames=1)

        self.assertIsInstance(result, Image.Image)
        if isinstance(result, Image.Image):
            result.close()

    def test_capture_dual_reports_the_selected_frames_identity_and_hashes(self) -> None:
        colors = ((10, 20, 30), (80, 100, 120), (81, 101, 121))
        windows = (
            {
                "pid": 1101,
                "class": "DayZ",
                "title": "first",
                "left": 10,
                "top": 20,
                "width": 2,
                "height": 2,
            },
            {
                "pid": 2202,
                "class": "DayZ",
                "title": "selected",
                "left": 30,
                "top": 40,
                "width": 2,
                "height": 2,
            },
            {
                "pid": 3303,
                "class": "DayZ",
                "title": "last",
                "left": 50,
                "top": 60,
                "width": 2,
                "height": 2,
            },
        )
        backend_hashes = ("1" * 64, "2" * 64, "3" * 64)
        capture_index = 0

        def capture_frame(output_path: str, **_: object) -> dict[str, object]:
            nonlocal capture_index
            index = capture_index
            capture_index += 1
            Image.new("RGB", (2, 2), colors[index]).save(output_path, format="PNG")
            return {
                "ok": True,
                "error": "",
                "method": "printwindow",
                "window": windows[index],
                "stats": {"meanBrightness": 96.0, "nonBlackRatio": 1.0},
                "sha256": backend_hashes[index],
                "client": {"left": 0, "top": 0, "width": 2, "height": 2},
                "clientStats": {"meanBrightness": 96.0, "nonBlackRatio": 1.0},
            }

        with mock.patch.object(mcp_capture, "_run_window_capture", side_effect=capture_frame):
            result = mcp_capture.capture_dual(frames=3)

        meta = result["meta"]
        self.assertEqual(windows[1], meta.get("window"))
        self.assertEqual(backend_hashes[1], meta.get("backend_sha256"))
        self.assertEqual(
            "5404c70428b88bac746c2fad022b29fc754c259f23d3022233a8b02923d01e80",
            meta.get("frame_sha256"),
        )

    def test_stable_frame_rejects_unverified_client_stats(self) -> None:
        cases: tuple[object, ...] = (
            None,
            {"meanBrightness": "not-a-number", "nonBlackRatio": 0.5},
            {"meanBrightness": float("nan"), "nonBlackRatio": 0.5},
            {"meanBrightness": 50.0, "nonBlackRatio": float("inf")},
        )
        for index, client_stats in enumerate(cases):
            with self.subTest(client_stats=client_stats):
                def unverified(output_path: str, **_: object) -> dict[str, object]:
                    Image.new("RGB", (200, 120), (80, 100, 120)).save(
                        output_path, format="PNG"
                    )
                    result: dict[str, object] = {
                        "ok": True,
                        "error": "",
                        "method": "printwindow",
                    }
                    if index > 0:
                        result["clientStats"] = client_stats
                    return result

                with mock.patch.object(
                    mcp_capture, "_run_window_capture", side_effect=unverified
                ):
                    result = mcp_capture.grab_stable_frame(frames=1)

                try:
                    self.assertIsInstance(result, dict)
                    self.assertEqual("frame_client_area_unverified", result.get("error"))
                finally:
                    if isinstance(result, Image.Image):
                        result.close()

    def test_downscale_to_budget_builds_image_content(self) -> None:
        img = Image.new("RGB", (1280, 720), (120, 150, 180))
        draw = ImageDraw.Draw(img)
        for index in range(0, 1280, 64):
            color = (50 + index % 160, 80 + index % 120, 70)
            draw.rectangle([index, 360, min(1279, index + 40), 700], fill=color)
        source = io.BytesIO()
        img.save(source, format="PNG")

        content = mcp_capture.image_content_from_png_bytes(source.getvalue(), scale="small", max_tokens=1000)

        self.assertEqual(content["type"], "image")
        # Default delivery format is now JPEG (fits ~2.85x the resolution inside the same token budget).
        self.assertEqual(content["mimeType"], "image/jpeg")
        self.assertLessEqual(len(content["data"]), int(1000 * mcp_capture.CHARS_PER_TOKEN))
        decoded = base64.b64decode(content["data"].encode("ascii"), validate=True)
        with Image.open(io.BytesIO(decoded)) as decoded_img:
            self.assertLessEqual(decoded_img.width, mcp_capture.SCALE_WIDTHS["small"])
            self.assertGreater(decoded_img.width, 0)
            self.assertGreater(decoded_img.height, 0)

    def test_png_format_is_still_selectable(self) -> None:
        img = Image.new("RGB", (640, 360), (100, 120, 140))
        content = mcp_capture.image_content_from_image(img, scale="small", max_tokens=25000, fmt="png")
        self.assertEqual(content["mimeType"], "image/png")

    def test_capture_window_not_found_returns_is_error(self) -> None:
        result = mcp_capture.capture_screenshot(
            scale="tiny",
            max_tokens=mcp_capture.DEFAULT_MAX_TOKENS,
            frames=1,
            process_name="__DayZ_MCP_missing_window__",
        )

        self.assertTrue(result.get("isError"))
        self.assertEqual(result.get("error"), "window_not_found")


# --- crop_space fixture ---------------------------------------------------------------------------
# A window bitmap with red chrome around a green client viewport that carries a blue block in its
# lower-right quadrant; the viewport is deliberately offset from the bitmap origin and leaves chrome
# on all four sides. Every expected rectangle, pixel hash and stat below is derived from this
# geometry directly (rectangle intersections, colour counts), never from the helpers under test.
WINDOW_W, WINDOW_H = 640, 360
CLIENT_RECT = (20, 40, 600, 300)  # left, top, width, height in window-bitmap space
BLUE_RECT = (320, 190, 300, 150)  # lower-right quadrant of the viewport, window-bitmap space
RED, GREEN, BLUE = (200, 20, 20), (20, 200, 20), (20, 20, 200)
WINDOW_META = {
    "pid": 4242,
    "class": "DayZ",
    "title": "fixture",
    "left": 100,
    "top": 50,
    "width": WINDOW_W,
    "height": WINDOW_H,
}
BACKEND_SHA = "f" * 64
_ABSENT = object()  # the backend payload carries no "client" key at all
_DEFAULT_CLIENT = object()  # use the fixture's own client rect


def _rect(left: int, top: int, width: int, height: int) -> dict[str, int]:
    return {"left": left, "top": top, "width": width, "height": height}


def _expected_region(left: int, top: int, width: int, height: int) -> Image.Image:
    """Pixels of the fixture inside a window-space rectangle, painted from the geometry, not
    cropped from anything."""
    img = Image.new("RGB", (width, height), RED)
    draw = ImageDraw.Draw(img)
    for (rl, rt, rw, rh), color in ((CLIENT_RECT, GREEN), (BLUE_RECT, BLUE)):
        il, it = max(left, rl), max(top, rt)
        ir, ib = min(left + width, rl + rw), min(top + height, rt + rh)
        if ir > il and ib > it:
            draw.rectangle([il - left, it - top, ir - left - 1, ib - top - 1], fill=color)
    return img


def _sha(img: Image.Image) -> str:
    return hashlib.sha256(img.tobytes()).hexdigest()


def _luma(rgb: tuple[int, int, int]) -> int:
    # ITU-R 601 luma with the integer weights Pillow applies for RGB -> L.
    r, g, b = rgb
    return (r * 19595 + g * 38470 + b * 7471 + 0x8000) >> 16


def _expected_stats(img: Image.Image) -> dict[str, object]:
    total = img.width * img.height
    colors = img.getcolors(maxcolors=total)
    mean = sum(count * _luma(color) for count, color in colors) / total
    non_black = sum(count for count, color in colors if _luma(color) >= 9)
    return {
        "width": img.width,
        "height": img.height,
        "meanBrightness": float(mean),
        "nonBlackRatio": float(non_black / total),
    }


def _decode(content: dict[str, object]) -> Image.Image:
    raw = base64.b64decode(str(content["data"]).encode("ascii"), validate=True)
    with Image.open(io.BytesIO(raw)) as img:
        return img.convert("RGB").copy()


def _backend(client: object):
    """Fake _run_window_capture: writes the fixture bitmap and returns the wire payload the grab
    script emits, with the client rect under test (or no client key at all)."""

    def run(output_path: str, **_: object) -> dict[str, object]:
        _expected_region(0, 0, WINDOW_W, WINDOW_H).save(output_path, format="PNG")
        payload: dict[str, object] = {
            "ok": True,
            "error": "",
            "method": "printwindow",
            "window": dict(WINDOW_META),
            "stats": {"meanBrightness": 90.0, "nonBlackRatio": 1.0},
            "sha256": BACKEND_SHA,
            "clientStats": {"meanBrightness": 104.75, "nonBlackRatio": 1.0},
        }
        if client is not _ABSENT:
            payload["client"] = client
        return payload

    return run


class CaptureDualCropSpaceTest(unittest.TestCase):
    """capture_dual crop_space contract: client viewport by default, strict and fail-closed, with an
    auditable surface map; window keeps the legacy fail-open crop."""

    def _capture(self, client: object = _DEFAULT_CLIENT, **kwargs: object):
        if client is _DEFAULT_CLIENT:
            client = _rect(*CLIENT_RECT)
        backend = mock.Mock(side_effect=_backend(client))
        with mock.patch.object(mcp_capture, "_run_window_capture", backend), mock.patch.object(
            mcp_capture, "apply_crop", wraps=mcp_capture.apply_crop
        ) as apply_spy, mock.patch.object(
            mcp_capture, "_encode_to_budget", wraps=mcp_capture._encode_to_budget
        ) as encode_spy:
            result = mcp_capture.capture_dual(frames=1, **kwargs)
        return result, apply_spy, encode_spy, backend

    def _assert_stats(self, expected: dict[str, object], actual: object) -> None:
        self.assertIsInstance(actual, dict)
        assert isinstance(actual, dict)
        self.assertEqual(expected["width"], actual.get("width"))
        self.assertEqual(expected["height"], actual.get("height"))
        self.assertAlmostEqual(expected["meanBrightness"], actual.get("meanBrightness"), places=6)
        self.assertAlmostEqual(expected["nonBlackRatio"], actual.get("nonBlackRatio"), places=6)

    def test_client_default_selects_the_viewport_before_the_downscale(self) -> None:
        result, apply_spy, encode_spy, _ = self._capture(scale="tiny", fmt="png")

        self.assertNotIn("isError", result)
        meta = result["meta"]
        self.assertEqual("client", meta["crop_space"])
        self.assertIs(True, meta["chrome_excluded"])
        window = _expected_region(0, 0, WINDOW_W, WINDOW_H)
        viewport = _expected_region(*CLIENT_RECT)

        # Surface map, each record against its own oracle.
        self.assertEqual(_rect(0, 0, WINDOW_W, WINDOW_H), meta["window_surface"]["rect"])
        self.assertEqual(_sha(window), meta["window_surface"]["pixel_sha256"])
        self._assert_stats(_expected_stats(window), meta["window_surface"]["stats"])
        self.assertEqual(_rect(*CLIENT_RECT), meta["client_surface"]["rect_window"])
        self.assertEqual(_sha(viewport), meta["client_surface"]["pixel_sha256"])
        self._assert_stats(_expected_stats(viewport), meta["client_surface"]["stats"])
        effective = meta["effective_surface"]
        self.assertEqual(_rect(*CLIENT_RECT), effective["rect_window"])
        self.assertEqual((600, 300), (effective["native_width"], effective["native_height"]))
        self.assertEqual(_sha(viewport), effective["native_pixel_sha256"])
        self._assert_stats(_expected_stats(viewport), effective["native_stats"])
        self.assertNotEqual(meta["window_surface"]["pixel_sha256"], effective["native_pixel_sha256"])

        # The viewport was cut out BEFORE the encode tail: apply_crop untouched, the primitive
        # received the 600x300 viewport, and the delivery is its downscale.
        self.assertEqual(0, apply_spy.call_count)
        self.assertEqual(1, encode_spy.call_count)
        self.assertEqual((600, 300), encode_spy.call_args.args[0].size)
        delivered = _decode(result["inline"])
        self.assertEqual("image/png", result["inline"]["mimeType"])
        self.assertEqual((320, 160), delivered.size)
        self.assertEqual(delivered.size, (effective["delivered_width"], effective["delivered_height"]))
        self.assertLess(effective["delivered_width"], effective["native_width"])
        self.assertEqual(_sha(delivered), effective["delivered_pixel_sha256"])
        self._assert_stats(_expected_stats(delivered), effective["delivered_stats"])
        # No chrome reached the delivery: red is the only fixture colour whose R channel is not 20.
        self.assertEqual((20, 20), delivered.getextrema()[0])

        # Legacy fields keep their shape and semantics.
        self.assertEqual(WINDOW_META, meta["window"])
        self.assertEqual(BACKEND_SHA, meta["backend_sha256"])
        self.assertEqual(meta["window_surface"]["pixel_sha256"], meta["frame_sha256"])
        self.assertEqual((WINDOW_W, WINDOW_H), (meta["native_width"], meta["native_height"]))
        self.assertEqual("", meta["crop"])
        self.assertEqual(len(result["inline"]["data"]), meta["inline_base64_len"])
        # Default branch: no file, but the whole surface contract is present.
        self.assertIsNone(result["fullres_path"])
        self.assertIsNone(meta["fullres_file_sha256"])

    def test_client_additional_crop_normalizes_over_the_viewport(self) -> None:
        cases = (
            ("0.5,0.5,1,1", BLUE_RECT),
            ("0,0,1,1", CLIENT_RECT),
            ("center", (170, 115, 300, 150)),
            ("center:0.5", (170, 115, 300, 150)),
            ("  CENTER:0.5 ", (170, 115, 300, 150)),
            ("0.25,0.1,0.75,0.9", (170, 70, 300, 240)),
            ("center:1", CLIENT_RECT),
            ("center:0.05", (305, 182, 30, 15)),
        )
        for crop, rect in cases:
            with self.subTest(crop=crop):
                result, apply_spy, encode_spy, _ = self._capture(crop=crop, scale="full", fmt="png")

                self.assertNotIn("isError", result)
                expected = _expected_region(*rect)
                effective = result["meta"]["effective_surface"]
                self.assertEqual(_rect(*rect), effective["rect_window"])
                self.assertEqual((rect[2], rect[3]), (effective["native_width"], effective["native_height"]))
                self.assertEqual(_sha(expected), effective["native_pixel_sha256"])
                self._assert_stats(_expected_stats(expected), effective["native_stats"])
                self.assertEqual(0, apply_spy.call_count)
                self.assertEqual((rect[2], rect[3]), encode_spy.call_args.args[0].size)
                # PNG at full scale on a small fixture: delivered is the native surface bit for bit.
                delivered = _decode(result["inline"])
                self.assertEqual(expected.tobytes(), delivered.tobytes())
                self.assertEqual(_sha(delivered), effective["delivered_pixel_sha256"])
                self.assertEqual(result["meta"]["client_surface"]["pixel_sha256"], _sha(_expected_region(*CLIENT_RECT)))
                self.assertEqual(crop, result["meta"]["crop"])

        # The same bbox normalized over the OUTER window would land on a different rectangle.
        result, _, _, _ = self._capture(crop="0.5,0.5,1,1", scale="full", fmt="png")
        self.assertNotEqual(
            _sha(_expected_region(320, 180, 320, 180)),
            result["meta"]["effective_surface"]["native_pixel_sha256"],
        )

    def test_client_rejects_an_unverifiable_client_rect(self) -> None:
        cases: tuple[tuple[str, object], ...] = (
            ("absent", _ABSENT),
            ("null", None),
            ("not_a_dict", "20,40,600,300"),
            ("list", [20, 40, 600, 300]),
            ("missing_height", {"left": 20, "top": 40, "width": 600}),
            ("bool_left", _rect(True, 40, 600, 300)),
            ("bool_width", _rect(20, 40, True, 300)),
            ("float_left", _rect(20.0, 40, 600, 300)),
            ("string_width", _rect(20, 40, "600", 300)),
            ("negative_left", _rect(-1, 40, 600, 300)),
            ("negative_top", _rect(20, -1, 600, 300)),
            ("zero_width", _rect(20, 40, 0, 300)),
            ("zero_height", _rect(20, 40, 600, 0)),
            ("negative_width", _rect(20, 40, -600, 300)),
            ("overflow_right", _rect(41, 40, 600, 300)),
            ("overflow_bottom", _rect(20, 61, 600, 300)),
            ("wider_than_bitmap", _rect(0, 0, WINDOW_W + 1, WINDOW_H)),
        )
        for label, client in cases:
            with self.subTest(rect=label):
                result, apply_spy, encode_spy, _ = self._capture(client=client, fmt="png")

                self.assertEqual({"isError": True, "error": "frame_client_rect_unverified"}, result)
                self.assertEqual(0, apply_spy.call_count)
                self.assertEqual(0, encode_spy.call_count)

    def test_client_rect_at_the_bitmap_origin_is_valid(self) -> None:
        for rect in ((0, 0, WINDOW_W, WINDOW_H), (0, 0, 600, 300)):
            with self.subTest(rect=rect):
                result, apply_spy, _, _ = self._capture(client=_rect(*rect), scale="full", fmt="png")

                self.assertNotIn("isError", result)
                meta = result["meta"]
                expected = _expected_region(*rect)
                self.assertEqual(_rect(*rect), meta["client_surface"]["rect_window"])
                self.assertEqual(_sha(expected), meta["client_surface"]["pixel_sha256"])
                self.assertEqual(_rect(*rect), meta["effective_surface"]["rect_window"])
                self.assertEqual(_sha(expected), meta["effective_surface"]["native_pixel_sha256"])
                self.assertEqual(0, apply_spy.call_count)
        # A viewport covering the whole bitmap yields the window hash for every surface.
        result, _, _, _ = self._capture(client=_rect(0, 0, WINDOW_W, WINDOW_H), scale="full", fmt="png")
        meta = result["meta"]
        self.assertEqual(meta["frame_sha256"], meta["client_surface"]["pixel_sha256"])
        self.assertEqual(meta["frame_sha256"], meta["effective_surface"]["native_pixel_sha256"])

    def test_client_rejects_every_invalid_crop_class(self) -> None:
        cases: tuple[tuple[str, object], ...] = (
            ("syntax_words", "not,a,box"),
            ("syntax_word", "abc"),
            ("syntax_center_word", "center:abc"),
            ("syntax_center_empty", "center:"),
            ("syntax_center_glued", "center0.5"),
            ("syntax_semicolons", "0.1;0.2;0.3;0.4"),
            ("syntax_trailing_comma", "0.1,0.2,0.3,"),
            ("arity_three", "0.1,0.2,0.3"),
            ("arity_five", "0.1,0.2,0.3,0.4,0.5"),
            ("arity_one", "0.5"),
            ("nan_bbox", "nan,0,1,1"),
            ("inf_bbox", "0,0,inf,1"),
            ("neg_inf_bbox", "0,0,1,-inf"),
            ("nan_center", "center:nan"),
            ("inf_center", "center:inf"),
            ("range_left_negative", "-0.1,0,1,1"),
            ("range_right_above_one", "0,0,1.5,1"),
            ("range_top_negative", "0,-0.5,1,1"),
            ("range_center_below_min", "center:0.04"),
            ("range_center_above_max", "center:1.01"),
            ("range_center_zero", "center:0"),
            ("range_center_negative", "center:-0.5"),
            ("degenerate_equal_x", "0.5,0,0.5,1"),
            ("degenerate_inverted_x", "0.8,0.0,0.2,1.0"),
            ("degenerate_equal_y", "0,0.5,1,0.5"),
            ("degenerate_inverted_y", "0,0.6,1,0.5"),
            ("degenerate_after_rounding", "0.5,0.5,0.5001,1"),
            ("not_a_string", 1.5),
        )
        for label, crop in cases:
            with self.subTest(crop=label):
                result, apply_spy, encode_spy, _ = self._capture(crop=crop, fmt="png")

                self.assertEqual({"isError": True, "error": "bad_crop"}, result)
                self.assertEqual(0, apply_spy.call_count)
                self.assertEqual(0, encode_spy.call_count)

    def test_bad_crop_space_is_rejected_before_any_grab_and_is_distinct_from_bad_crop(self) -> None:
        for crop_space in ("screen", "", "Client", "WINDOW", " client", None, 1, ["client"], {"client": 1}):
            with self.subTest(crop_space=crop_space):
                result, apply_spy, encode_spy, backend = self._capture(crop_space=crop_space, fmt="png")

                self.assertEqual({"isError": True, "error": "bad_crop_space"}, result)
                self.assertEqual(0, backend.call_count)
                self.assertEqual(0, apply_spy.call_count)
                self.assertEqual(0, encode_spy.call_count)

        # Same bad crop, two different tokens depending on where the caller went wrong.
        wrong_space, _, _, _ = self._capture(crop_space="screen", crop="not,a,box", fmt="png")
        wrong_crop, _, _, _ = self._capture(crop_space="client", crop="not,a,box", fmt="png")
        self.assertEqual("bad_crop_space", wrong_space["error"])
        self.assertEqual("bad_crop", wrong_crop["error"])
        self.assertNotEqual(wrong_space["error"], wrong_crop["error"])
        # An unverifiable rect is reported before the crop is interpreted over it.
        unverified, _, _, _ = self._capture(client=None, crop="not,a,box", fmt="png")
        self.assertEqual("frame_client_rect_unverified", unverified["error"])

    def test_window_mode_keeps_the_legacy_fail_open_crop(self) -> None:
        window = _expected_region(0, 0, WINDOW_W, WINDOW_H)
        whole = (0, 0, WINDOW_W, WINDOW_H)
        cases = (
            ("", whole),
            ("0.5,0.5,1,1", (320, 180, 320, 180)),
            ("center:0.5", (160, 90, 320, 180)),
            ("not,a,box", whole),  # legacy fail-open: garbage keeps the frame
            ("0.8,0.0,0.2,1.0", whole),  # legacy fail-open: inverted box keeps the frame
            ("center:2", whole),  # legacy clamp to 1.0
        )
        for crop, rect in cases:
            with self.subTest(crop=crop):
                result, apply_spy, encode_spy, _ = self._capture(crop_space="window", crop=crop, scale="full", fmt="png")

                self.assertNotIn("isError", result)
                meta = result["meta"]
                self.assertEqual("window", meta["crop_space"])
                self.assertIs(False, meta["chrome_excluded"])
                expected = _expected_region(*rect)
                effective = meta["effective_surface"]
                self.assertEqual(_rect(*rect), effective["rect_window"])
                self.assertEqual(_sha(expected), effective["native_pixel_sha256"])
                self.assertEqual(expected.tobytes(), _decode(result["inline"]).tobytes())
                self.assertEqual(_sha(_decode(result["inline"])), effective["delivered_pixel_sha256"])
                # The legacy wrapper path: exactly one apply_crop over the whole window frame.
                self.assertEqual(1, apply_spy.call_count)
                self.assertEqual((WINDOW_W, WINDOW_H), apply_spy.call_args.args[0].size)
                self.assertEqual(crop, apply_spy.call_args.args[1])
                self.assertEqual((rect[2], rect[3]), encode_spy.call_args.args[0].size)
                # Window space keeps publishing the accredited viewport next to the window.
                self.assertEqual(_sha(window), meta["frame_sha256"])
                self.assertEqual(meta["frame_sha256"], meta["window_surface"]["pixel_sha256"])
                self.assertEqual(_rect(*CLIENT_RECT), meta["client_surface"]["rect_window"])
                self.assertEqual(_sha(_expected_region(*CLIENT_RECT)), meta["client_surface"]["pixel_sha256"])
                self.assertEqual(WINDOW_META, meta["window"])

        # Empty crop in window space delivers the chrome; the same request in client space does not.
        result, _, _, _ = self._capture(crop_space="window", scale="full", fmt="png")
        self.assertEqual(200, _decode(result["inline"]).getextrema()[0][1])
        # The same bbox selects different pixels in the two spaces.
        in_window, _, _, _ = self._capture(crop_space="window", crop="0.5,0.5,1,1", scale="full", fmt="png")
        in_client, _, _, _ = self._capture(crop_space="client", crop="0.5,0.5,1,1", scale="full", fmt="png")
        self.assertNotEqual(
            in_window["meta"]["effective_surface"]["native_pixel_sha256"],
            in_client["meta"]["effective_surface"]["native_pixel_sha256"],
        )
        # Window space does not need the client rect: it reports the gap instead of failing.
        for client in (_ABSENT, None, _rect(41, 40, 600, 300)):
            with self.subTest(client=client):
                result, apply_spy, _, _ = self._capture(client=client, crop_space="window", scale="full", fmt="png")
                self.assertNotIn("isError", result)
                self.assertIsNone(result["meta"]["client_surface"])
                self.assertEqual(_sha(window), result["meta"]["effective_surface"]["native_pixel_sha256"])
                self.assertEqual(1, apply_spy.call_count)

    def test_fullres_saves_the_native_effective_surface_not_the_chosen_frame(self) -> None:
        cases = (
            # (crop_space, crop, expected native rect, chrome predicate on the saved file)
            # Client rows: no chrome may survive (R <= 60 after JPEG q92) and the file is never the
            # whole window, which is what HEAD saved. Window row: legacy parity -- HEAD passes it by
            # construction -- kept because it discriminates a mutant that crops the viewport in
            # window space: the chrome's R=200 must still be in the file.
            ("client", "0.5,0.5,1,1", BLUE_RECT, "no_chrome"),
            ("client", "", CLIENT_RECT, "no_chrome"),
            ("window", "", (0, 0, WINDOW_W, WINDOW_H), "has_chrome"),
        )
        for crop_space, crop, rect, chrome in cases:
            with self.subTest(crop_space=crop_space, crop=crop), tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(mcp_capture, "write_fullres", wraps=mcp_capture.write_fullres) as write_spy:
                    result, _, _, _ = self._capture(
                        crop_space=crop_space, crop=crop, scale=100, fmt="png", save_fullres=True, save_dir=tmp
                    )

                self.assertNotIn("isError", result)
                meta = result["meta"]
                effective = meta["effective_surface"]
                expected = _expected_region(*rect)
                # The image handed to the writer IS the native effective surface (same pixels).
                self.assertEqual(1, write_spy.call_count)
                passed = write_spy.call_args.args[0]
                self.assertEqual((rect[2], rect[3]), passed.size)
                self.assertEqual(_sha(expected), _sha(passed))
                self.assertEqual(_sha(passed), effective["native_pixel_sha256"])
                # The file on disk: absolute, inside save_dir, hashed separately, native size.
                path = result["fullres_path"]
                self.assertTrue(os.path.isabs(path))
                self.assertEqual(os.path.abspath(tmp), os.path.dirname(path))
                with open(path, "rb") as handle:
                    self.assertEqual(hashlib.sha256(handle.read()).hexdigest(), meta["fullres_file_sha256"])
                with Image.open(path) as saved:
                    self.assertEqual((rect[2], rect[3]), saved.size)
                    max_red = saved.convert("RGB").getextrema()[0][1]
                    if chrome == "no_chrome":
                        self.assertNotEqual((WINDOW_W, WINDOW_H), saved.size)
                        self.assertLessEqual(max_red, 60)
                    else:
                        self.assertGreaterEqual(max_red, 190)
                # The inline is the downscale of that same surface, never the other way round.
                self.assertEqual(100, effective["delivered_width"])
                self.assertLess(effective["delivered_width"], effective["native_width"])
                self.assertEqual((100, round(100 * rect[3] / rect[2])), _decode(result["inline"]).size)

        # The solid blue crop survives JPEG q92 almost exactly: the file is that region, not chrome.
        with tempfile.TemporaryDirectory() as tmp:
            result, _, _, _ = self._capture(crop="0.5,0.5,1,1", scale=100, fmt="png", save_fullres=True, save_dir=tmp)
            with Image.open(result["fullres_path"]) as saved:
                for channel, value in zip(saved.convert("RGB").getextrema(), BLUE):
                    self.assertLessEqual(abs(channel[0] - value), 6)
                    self.assertLessEqual(abs(channel[1] - value), 6)

    def test_default_jpeg_delivery_is_measured_on_the_returned_bytes(self) -> None:
        result, apply_spy, _, _ = self._capture()

        self.assertNotIn("isError", result)
        self.assertEqual("image/jpeg", result["inline"]["mimeType"])
        self.assertEqual(0, apply_spy.call_count)
        meta = result["meta"]
        effective = meta["effective_surface"]
        delivered = _decode(result["inline"])
        self.assertEqual((512, 256), delivered.size)
        self.assertEqual(delivered.size, (effective["delivered_width"], effective["delivered_height"]))
        self.assertEqual(_sha(delivered), effective["delivered_pixel_sha256"])
        self._assert_stats(_expected_stats(delivered), effective["delivered_stats"])
        # Native measurements are lossless even when the delivery is not.
        self.assertEqual(_sha(_expected_region(*CLIENT_RECT)), effective["native_pixel_sha256"])
        self.assertNotEqual(effective["native_pixel_sha256"], effective["delivered_pixel_sha256"])
        # No chrome in the JPEG either: the red channel stays far below the chrome's 200.
        self.assertLess(delivered.getextrema()[0][1], 100)

    def test_grab_stable_frame_keeps_the_chosen_frames_client_rect(self) -> None:
        rects = (_rect(0, 0, WINDOW_W, WINDOW_H), _rect(*CLIENT_RECT), _rect(10, 10, 600, 300))
        capture_index = 0

        def capture_frame(output_path: str, **_: object) -> dict[str, object]:
            nonlocal capture_index
            index = capture_index
            capture_index += 1
            # Frame 0 is a different image; frames 1 and 2 are identical, so the stable pick is 1.
            if index == 0:
                Image.new("RGB", (WINDOW_W, WINDOW_H), RED).save(output_path, format="PNG")
            else:
                _expected_region(0, 0, WINDOW_W, WINDOW_H).save(output_path, format="PNG")
            return {
                "ok": True,
                "error": "",
                "method": "printwindow",
                "window": dict(WINDOW_META),
                "stats": {"meanBrightness": 90.0, "nonBlackRatio": 1.0},
                "sha256": BACKEND_SHA,
                "client": rects[index],
                "clientStats": {"meanBrightness": 104.75, "nonBlackRatio": 1.0},
            }

        with mock.patch.object(mcp_capture, "_run_window_capture", side_effect=capture_frame):
            frame = mcp_capture.grab_stable_frame(frames=3)
        self.assertIsInstance(frame, Image.Image)
        self.assertEqual(rects[1], frame.info.get("client"))

        capture_index = 0
        with mock.patch.object(mcp_capture, "_run_window_capture", side_effect=capture_frame):
            result = mcp_capture.capture_dual(frames=3, scale="full", fmt="png")
        self.assertEqual(rects[1], result["meta"]["client_surface"]["rect_window"])
        self.assertEqual(_sha(_expected_region(*CLIENT_RECT)), result["meta"]["effective_surface"]["native_pixel_sha256"])

    def test_legacy_entry_points_still_go_through_apply_crop(self) -> None:
        frame = _expected_region(0, 0, WINDOW_W, WINDOW_H)
        with mock.patch.object(mcp_capture, "apply_crop", wraps=mcp_capture.apply_crop) as apply_spy:
            content = mcp_capture.image_content_from_image(frame, scale="full", fmt="png", crop="0.5,0.5,1,1")
        self.assertEqual(1, apply_spy.call_count)
        self.assertEqual(_expected_region(320, 180, 320, 180).tobytes(), _decode(content).tobytes())

        with mock.patch.object(mcp_capture, "_run_window_capture", side_effect=_backend(_rect(*CLIENT_RECT))):
            content = mcp_capture.capture_screenshot(frames=1, scale="full", fmt="png", crop="0.5,0.5,1,1")
        self.assertEqual(_expected_region(320, 180, 320, 180).tobytes(), _decode(content).tobytes())

    def test_error_tokens_are_the_literal_wire_identifiers(self) -> None:
        from dayz_mcp import server

        tokens = {
            "bad_crop_space": mcp_capture.ERROR_BAD_CROP_SPACE,
            "bad_crop": mcp_capture.ERROR_BAD_CROP,
            "frame_client_rect_unverified": mcp_capture.ERROR_CLIENT_RECT_UNVERIFIED,
        }
        for literal, token in tokens.items():
            with self.subTest(token=literal):
                self.assertEqual(literal, token)
                # The server wire filter keeps identifier-shaped tokens verbatim.
                self.assertEqual(token, server._wire_safe_error(object(), "capture_screenshot", token))
        self.assertEqual(3, len(set(tokens.values())))
        self.assertEqual(("client", "window"), mcp_capture.CROP_SPACES)
        self.assertEqual("client", mcp_capture.DEFAULT_CROP_SPACE)

    def test_head_identity_scenario_keeps_frame_sha256_as_the_window_hash_in_both_spaces(self) -> None:
        """Companion of MCPCaptureTest.test_capture_dual_reports_the_selected_frames_identity_and_hashes,
        which stays byte-identical to HEAD. There the backend client rect covers the whole 2x2
        bitmap, so under the new client default the viewport crop is the identity and that test
        cannot tell a window hash from an effective-surface hash. Same three frames with a 1x1
        client rect: the constant HEAD froze (sha256 of the 2x2 frame-1 RGB) must remain
        meta.frame_sha256 in both spaces, while the effective surface is the 1x1 viewport in client
        space only."""
        colors = ((10, 20, 30), (80, 100, 120), (81, 101, 121))
        head_frame_sha256 = "5404c70428b88bac746c2fad022b29fc754c259f23d3022233a8b02923d01e80"
        self.assertEqual(head_frame_sha256, hashlib.sha256(bytes(colors[1]) * 4).hexdigest())
        viewport_sha256 = hashlib.sha256(bytes(colors[1])).hexdigest()
        for crop_space in ("window", "client", None):
            with self.subTest(crop_space=crop_space):
                capture_index = 0

                def capture_frame(output_path: str, **_: object) -> dict[str, object]:
                    nonlocal capture_index
                    index = capture_index
                    capture_index += 1
                    Image.new("RGB", (2, 2), colors[index]).save(output_path, format="PNG")
                    return {
                        "ok": True,
                        "error": "",
                        "method": "printwindow",
                        "window": {"pid": 2202, "class": "DayZ", "title": "selected", "left": 30, "top": 40, "width": 2, "height": 2},
                        "stats": {"meanBrightness": 96.0, "nonBlackRatio": 1.0},
                        "sha256": "2" * 64,
                        "client": {"left": 1, "top": 1, "width": 1, "height": 1},
                        "clientStats": {"meanBrightness": 96.0, "nonBlackRatio": 1.0},
                    }

                kwargs: dict[str, object] = {} if crop_space is None else {"crop_space": crop_space}
                with mock.patch.object(mcp_capture, "_run_window_capture", side_effect=capture_frame):
                    result = mcp_capture.capture_dual(frames=3, scale="full", fmt="png", **kwargs)

                self.assertNotIn("isError", result)
                meta = result["meta"]
                self.assertEqual(head_frame_sha256, meta["frame_sha256"])
                self.assertEqual(head_frame_sha256, meta["window_surface"]["pixel_sha256"])
                self.assertEqual({"left": 1, "top": 1, "width": 1, "height": 1}, meta["client_surface"]["rect_window"])
                self.assertEqual(viewport_sha256, meta["client_surface"]["pixel_sha256"])
                effective = meta["effective_surface"]["native_pixel_sha256"]
                if crop_space == "window":
                    self.assertEqual(head_frame_sha256, effective)
                    self.assertEqual((2, 2), _decode(result["inline"]).size)
                else:
                    self.assertEqual(viewport_sha256, effective)
                    self.assertNotEqual(head_frame_sha256, effective)
                    self.assertEqual((1, 1), _decode(result["inline"]).size)


class AllBlackFrameReportTest(unittest.TestCase):
    def test_an_all_black_frame_reports_whether_the_frame_was_advancing(self) -> None:
        window = {
            "pid": 4242,
            "class": "DayZ",
            "title": "DayZ",
            "left": 0,
            "top": 0,
            "width": 200,
            "height": 120,
        }
        client = {"left": 0, "top": 20, "width": 200, "height": 100}

        def black_frame(output_path: str, **_: object) -> dict[str, object]:
            Image.new("RGB", (200, 120), (0, 0, 0)).save(output_path, format="PNG")
            return {
                "ok": True,
                "error": "",
                "method": "printwindow",
                "window": dict(window),
                "client": dict(client),
                "clientStats": {"meanBrightness": 0.0, "nonBlackRatio": 0.0},
                "sha256": "0" * 64,
            }

        with tempfile.TemporaryDirectory(prefix="frame_state_") as tmp:
            state_path = os.path.join(tmp, "capture-frame-state.json")
            with mock.patch.dict(os.environ, {"DAYZ_MCP_FRAME_STATE_PATH": state_path}):
                with mock.patch.object(
                    mcp_capture, "_run_window_capture", side_effect=black_frame
                ):
                    first = mcp_capture.grab_stable_frame(
                        frames=1, cmdline_match=r"P:\profiles"
                    )
                    second = mcp_capture.grab_stable_frame(
                        frames=1, cmdline_match=r"P:\profiles"
                    )

        for result in (first, second):
            self.assertIsInstance(result, dict)
            self.assertIs(True, result.get("isError"))
            self.assertEqual("frame_client_all_black", result.get("error"))
            report = result.get("frame_stale_report")
            self.assertIsInstance(report, dict)
            self.assertIn("stale", report)
            self.assertIn("detail", report)

        self.assertIsNone(first["frame_stale_report"]["stale"])
        self.assertIs(True, second["frame_stale_report"]["stale"])


if __name__ == "__main__":
    unittest.main()
