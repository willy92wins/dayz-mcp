from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import server
from dayz_mcp.server import ServerConfig, build_app
from tests.test_client_mode import _fixture_client_runtime
from tests.test_mcp_tools import _content_json


class LogsSinceMarkerRoundtripTest(unittest.IsolatedAsyncioTestCase):
    """Public MCP contract: the marker logs_since returns must feed the next call.

    FastMCP pre-parses a JSON-encoded string into a dict before Pydantic sees it
    (`mcp.server.fastmcp.utilities.func_metadata.FuncMetadata.pre_parse_json`).
    The tool used to annotate `marker` as `str | None`, so the parsed dict was
    rejected with `Input should be a valid string` even though it was the exact
    value from the previous response.
    """

    async def asyncSetUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _m: None,
        )
        self.runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=self.runtime):
            self.app, _built = build_app(config)
        self.profiles = Path(self._temp.name) / "_server" / "profiles"
        self.profiles.mkdir(parents=True)
        self.log = self.profiles / "script_2026-08-18.log"
        self.log.write_text("boot line\n", encoding="utf-8")
        lifecycle = AsyncMock(
            return_value={
                "runs": [{"run_id": "run-a", "profiles": str(self.profiles)}]
            }
        )
        self._lifecycle_patch = patch.object(
            self.runtime, "lifecycle_status", new=lifecycle
        )
        self._lifecycle_patch.start()

    async def asyncTearDown(self) -> None:
        self._lifecycle_patch.stop()
        self._temp.cleanup()

    async def test_returned_marker_is_accepted_on_the_next_call(self) -> None:
        first = _content_json(await self.app.call_tool("logs_since", {}))
        self.assertIn("marker", first)
        marker = first["marker"]

        with self.log.open("a", encoding="utf-8") as handle:
            handle.write("second line\n")

        second = _content_json(
            await self.app.call_tool("logs_since", {"marker": marker})
        )

        self.assertEqual(second["ok"], 1)
        emitted = {
            Path(item["path"]).name: item["lines"] for item in second["files"]
        }
        self.assertEqual(emitted[self.log.name], ["second line"])
        self.assertNotEqual(second["marker"], first["marker"])

    async def test_response_marker_shape_stays_encoded_string(self) -> None:
        first = _content_json(await self.app.call_tool("logs_since", {}))
        self.assertIsInstance(first["marker"], str)
        parsed = json.loads(first["marker"])
        self.assertIsInstance(parsed, dict)

        second = _content_json(
            await self.app.call_tool("logs_since", {"marker": first["marker"]})
        )
        self.assertIsInstance(second["marker"], str)

    async def test_decoded_marker_dict_is_also_accepted(self) -> None:
        first = _content_json(await self.app.call_tool("logs_since", {}))
        parsed = json.loads(first["marker"])
        self.assertIsInstance(parsed, dict)

        with self.log.open("a", encoding="utf-8") as handle:
            handle.write("from dict\n")

        second = _content_json(
            await self.app.call_tool("logs_since", {"marker": parsed})
        )
        emitted = {
            Path(item["path"]).name: item["lines"] for item in second["files"]
        }
        self.assertEqual(emitted[self.log.name], ["from dict"])

    async def test_malformed_string_marker_is_still_bad_marker(self) -> None:
        with self.assertRaises(Exception) as ctx:
            await self.app.call_tool("logs_since", {"marker": "{not json"})
        self.assertIn("bad_marker", str(ctx.exception))

    async def test_session_marker_is_keyed_by_run_id(self) -> None:
        # D39: cursors must not cross between runs. A call for run-a must not
        # consume the session cursor of run-b, and vice versa. Both runs are
        # given the SAME profiles dir on purpose: identical paths is the hardest
        # case for a per-run cursor, since only the key can keep them apart.
        self.runtime.lifecycle_status.return_value = {
            "runs": [
                {"run_id": "run-a", "profiles": str(self.profiles)},
                {"run_id": "run-b", "profiles": str(self.profiles)},
            ]
        }
        first_a = _content_json(
            await self.app.call_tool("logs_since", {"run_id": "run-a"})
        )
        self.assertEqual(first_a["ok"], 1)
        marker_a = first_a["marker"]

        with self.log.open("a", encoding="utf-8") as handle:
            handle.write("appended for a\n")

        # A different run_id starts from its own (empty) cursor, so it re-reads
        # the whole file instead of resuming where run-a left off.
        first_b = _content_json(
            await self.app.call_tool("logs_since", {"run_id": "run-b"})
        )
        self.assertEqual(first_b["ok"], 1)
        emitted_b = {
            Path(item["path"]).name: item["lines"] for item in first_b["files"]
        }
        self.assertIn("boot line", emitted_b[self.log.name])
        self.assertIn("appended for a", emitted_b[self.log.name])

        # run-a's stored cursor still points past "boot line", so it only sees
        # the line appended after it.
        second_a = _content_json(
            await self.app.call_tool("logs_since", {"run_id": "run-a"})
        )
        emitted_a = {
            Path(item["path"]).name: item["lines"] for item in second_a["files"]
        }
        self.assertEqual(emitted_a[self.log.name], ["appended for a"])
        self.assertNotEqual(second_a["marker"], marker_a)
