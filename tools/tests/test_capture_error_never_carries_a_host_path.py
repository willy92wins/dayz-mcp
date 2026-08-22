"""capture_screenshot must not put the host's filesystem on the MCP wire.

Found 2026-08-22 by an independent verification pass over the day's audit, and
confirmed end to end against the tree. `mcp_capture` describes a capture failure
with the thing that broke, and the thing that broke is named by an absolute path:

    mcp_capture.py:315   f"capture_backend_failed: grab script missing {GRAB_SCRIPT}"
    mcp_capture.py:356   f"capture_backend_failed: {stderr}"    <- raw PowerShell stderr
    mcp_capture.py:363   f"capture_backend_failed: {exc}"

GRAB_SCRIPT is built with os.path.abspath, so it is `C:\\Users\\<name>\\...`. The
tool used to forward that string unchanged. The repo is public and the wire
reaches other machines: this is the runtime twin of the leak the publication
boundary exists to stop.

The detail is worth keeping -- it just belongs in the process's own log. These
tests pin the split: token on the wire, everything else local.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import mcp_capture  # noqa: E402
from dayz_mcp import server  # noqa: E402


class _SinkRuntime:
    """Only what _wire_safe_error touches: a local log sink."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._log = self.lines.append


# The real thing, not a stand-in: if this stops being an absolute path the test
# below stops proving anything, so it is asserted rather than assumed.
GRAB = mcp_capture.GRAB_SCRIPT


class CaptureErrorTest(unittest.TestCase):
    def test_the_fixture_is_actually_a_host_path(self) -> None:
        self.assertTrue(Path(GRAB).is_absolute(), GRAB)
        self.assertIn("\\", GRAB)

    def test_the_missing_script_error_does_not_carry_the_path(self) -> None:
        runtime = _SinkRuntime()
        detail = f"capture_backend_failed: grab script missing {GRAB}"
        wire = server._wire_safe_error(runtime, "capture_screenshot", detail)

        self.assertEqual(wire, "capture_backend_failed")
        self.assertNotIn(GRAB, wire)
        self.assertNotIn("\\", wire)
        self.assertNotIn(":", wire)

    def test_the_detail_still_reaches_the_local_log(self) -> None:
        """Dropping it from the wire must not mean losing it."""
        runtime = _SinkRuntime()
        detail = f"capture_backend_failed: grab script missing {GRAB}"
        server._wire_safe_error(runtime, "capture_screenshot", detail)

        self.assertEqual(len(runtime.lines), 1)
        self.assertIn(GRAB, runtime.lines[0])
        self.assertIn("local only", runtime.lines[0])

    def test_raw_powershell_stderr_is_reduced_to_the_token(self) -> None:
        runtime = _SinkRuntime()
        stderr = (
            "capture_backend_failed: At C:\\Users\\someone\\tools\\mcp-grab.ps1:28 "
            "char:5 + PrintWindow failed"
        )
        wire = server._wire_safe_error(runtime, "capture_screenshot", stderr)

        self.assertEqual(wire, "capture_backend_failed")
        self.assertNotIn("Users", wire)

    def test_a_token_without_detail_passes_through_and_logs_nothing(self) -> None:
        runtime = _SinkRuntime()
        wire = server._wire_safe_error(runtime, "capture_screenshot", "capture_timeout")

        self.assertEqual(wire, "capture_timeout")
        self.assertEqual(runtime.lines, [])

    def test_a_leading_token_that_is_not_identifier_shaped_is_replaced(self) -> None:
        """Never trust the text: a searchable name or a generic one, nothing else."""
        runtime = _SinkRuntime()
        for hostile in (
            "C:\\Users\\someone\\thing.ps1: exploded",
            " : leading colon",
            "no",
            "",
        ):
            wire = server._wire_safe_error(runtime, "capture_screenshot", hostile)
            self.assertEqual(wire, "capture_screenshot_failed", hostile)
            self.assertNotIn("Users", wire)

    def test_a_dict_result_is_stringified_without_leaking(self) -> None:
        """The raise site falls back to the whole result when error is absent."""
        runtime = _SinkRuntime()
        wire = server._wire_safe_error(
            runtime, "capture_screenshot", {"isError": True, "path": GRAB}
        )
        self.assertEqual(wire, "capture_screenshot_failed")
        self.assertNotIn("Users", wire)

    def test_the_raise_site_uses_the_helper(self) -> None:
        """A future edit that inlines str(error) again reopens the leak."""
        source = (_TOOLS_DIR / "dayz_mcp" / "server.py").read_text(encoding="utf-8")
        self.assertNotIn(
            'raise ToolError(str(result.get("error") or result))',
            source,
            "capture_screenshot is forwarding the backend message verbatim again",
        )
        self.assertIn('_wire_safe_error(\n                    runtime, "capture_screenshot"', source)


if __name__ == "__main__":
    unittest.main()
