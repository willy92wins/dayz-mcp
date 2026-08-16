from __future__ import annotations

import os
import unittest

from PIL import Image

import mcp_capture


class TokenBudgetTest(unittest.TestCase):
    """Cap-aware inline budget (MAX_MCP_OUTPUT_TOKENS) + WebP opt-in encode."""

    def setUp(self) -> None:
        self._prev = os.environ.get("MAX_MCP_OUTPUT_TOKENS")

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("MAX_MCP_OUTPUT_TOKENS", None)
        else:
            os.environ["MAX_MCP_OUTPUT_TOKENS"] = self._prev

    def _set(self, val) -> None:
        if val is None:
            os.environ.pop("MAX_MCP_OUTPUT_TOKENS", None)
        else:
            os.environ["MAX_MCP_OUTPUT_TOKENS"] = str(val)

    def test_cap_defaults_when_unset(self) -> None:
        self._set(None)
        self.assertEqual(mcp_capture.client_token_cap(), 25000)

    def test_cap_reads_env(self) -> None:
        self._set(75000)
        self.assertEqual(mcp_capture.client_token_cap(), 75000)

    def test_cap_ignores_junk_and_nonpositive(self) -> None:
        for bad in ("", "abc", "0", "-10", "  "):
            self._set(bad)
            self.assertEqual(mcp_capture.client_token_cap(), 25000, f"bad={bad!r}")

    def test_default_applies_margin(self) -> None:
        self._set(50000)
        self.assertEqual(mcp_capture.default_max_tokens(), int(50000 * mcp_capture.TOKEN_SAFETY_MARGIN))
        self.assertLess(mcp_capture.default_max_tokens(), 50000)

    def test_resolve_clamps_above_cap(self) -> None:
        self._set(25000)
        self.assertEqual(mcp_capture.resolve_request_budget(10 ** 9), mcp_capture.default_max_tokens())

    def test_resolve_honors_below_cap(self) -> None:
        self._set(100000)
        self.assertEqual(mcp_capture.resolve_request_budget(5000), 5000)

    def test_resolve_nonpositive_or_junk_uses_cap(self) -> None:
        self._set(25000)
        cap = mcp_capture.default_max_tokens()
        for v in (0, -1, None, "x"):
            self.assertEqual(mcp_capture.resolve_request_budget(v), cap, f"v={v!r}")

    def test_webp_encode_and_mime(self) -> None:
        img = Image.new("RGB", (320, 180), (90, 110, 130))
        data = mcp_capture.encode_bytes(img, fmt="webp", quality=80)
        self.assertEqual(data[:4], b"RIFF")
        self.assertEqual(data[8:12], b"WEBP")
        self.assertEqual(mcp_capture._mime_for("webp"), "image/webp")

    def test_webp_image_content_roundtrips(self) -> None:
        img = Image.new("RGB", (640, 360), (40, 80, 120))
        content = mcp_capture.image_content_from_image(img, scale="small", max_tokens=25000, fmt="webp")
        self.assertEqual(content["mimeType"], "image/webp")


if __name__ == "__main__":
    unittest.main()
