from __future__ import annotations

import base64
import io
import json
import subprocess
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


if __name__ == "__main__":
    unittest.main()
