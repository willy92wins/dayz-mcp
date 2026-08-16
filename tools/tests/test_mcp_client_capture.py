from __future__ import annotations

import tempfile
import unittest
from unittest import mock

from PIL import Image

import mcp_client


class MCPClientCaptureTest(unittest.TestCase):
    def test_burst_preserves_exact_capture_provenance(self) -> None:
        expected = {
            "ok": True,
            "error": "",
            "method": "printwindow",
            "window": {
                "pid": 4242,
                "class": "DayZ",
                "title": "DayZ",
                "left": 10,
                "top": 20,
                "width": 200,
                "height": 120,
            },
            "stats": {"meanBrightness": 30.0, "nonBlackRatio": 0.5},
            "client": {"left": 8, "top": 31, "width": 184, "height": 81},
            "clientStats": {"meanBrightness": 42.0, "nonBlackRatio": 0.75},
            "sha256": "A" * 64,
        }

        def grab(output_path: str, **_: object) -> dict[str, object]:
            Image.new("RGB", (200, 120), (80, 100, 120)).save(output_path, format="PNG")
            return dict(expected)

        with tempfile.TemporaryDirectory() as dest_dir:
            with mock.patch.object(
                mcp_client.mcp_capture, "grab_window_to_file", side_effect=grab
            ):
                result = mcp_client.grab_burst_max_liveness(
                    dest_dir,
                    "capture",
                    client_pid=4242,
                    frames=1,
                    interval_s=0.0,
                )

        self.assertIs(result.get("ok"), True)
        self.assertEqual([expected], result.get("grabs"))


if __name__ == "__main__":
    unittest.main()
