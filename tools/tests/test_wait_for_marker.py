"""Exact log cursors for wait_for(log_matches)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import server
from dayz_mcp.server import ServerConfig, build_app
from tests.test_client_mode import _fixture_client_runtime
from tests.test_mcp_tools import _content_json


def _live_process() -> dict[str, object]:
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    return {"pid": 4242, "creation_time_utc": stamp}


class WaitForMarkerTest(unittest.IsolatedAsyncioTestCase):
    NEEDLE = "SRV3-MARKER-NEEDLE"

    async def asyncSetUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        self.runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=self.runtime):
            self.app, _built = build_app(config)
        self.profiles = Path(self._temp.name) / "_server" / "profiles"
        self.profiles.mkdir(parents=True)
        self.log = self.profiles / "script.log"
        self.log.write_text(f"SCRIPT : before {self.NEEDLE}\n", encoding="utf-8")
        lifecycle = AsyncMock(
            return_value={
                "runs": [
                    {
                        "run_id": "run-a",
                        "profiles": str(self.profiles),
                        "processes": [_live_process()],
                    }
                ]
            }
        )
        self._lifecycle_patch = patch.object(
            self.runtime, "lifecycle_status", new=lifecycle
        )
        self._lifecycle_patch.start()

    async def asyncTearDown(self) -> None:
        self._lifecycle_patch.stop()
        self._temp.cleanup()

    async def _capture_marker(self) -> str:
        response = _content_json(await self.app.call_tool("logs_since", {}))
        marker = response["marker"]
        self.assertIsInstance(marker, str)
        return marker

    async def _wait(self, **arguments: object) -> dict:
        payload = {
            "condition": "log_matches",
            "pattern": self.NEEDLE,
            "timeout_s": 0.6,
            "poll_interval_s": 0.5,
        }
        payload.update(arguments)
        return _content_json(await self.app.call_tool("wait_for", payload))

    async def test_lookback_can_match_a_line_written_before_the_action(self) -> None:
        result = await self._wait(lookback_lines=200)

        self.assertTrue(result["satisfied"])
        self.assertIn("before", result["observed"])

    async def test_marker_only_matches_the_post_marker_occurrence(self) -> None:
        marker = await self._capture_marker()
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(f"SCRIPT : after {self.NEEDLE}\n")

        result = await self._wait(
            marker=marker,
            lookback_lines=server.WAIT_FOR_LOOKBACK_MAX,
            lookback_from="launch",
        )

        self.assertTrue(result["satisfied"])
        self.assertIn("after", result["observed"])
        self.assertNotIn("before", result["observed"])

    async def test_marker_does_not_match_when_the_pattern_only_precedes_it(self) -> None:
        marker = await self._capture_marker()

        result = await self._wait(
            marker=marker,
            lookback_lines=server.WAIT_FOR_LOOKBACK_MAX,
            lookback_from="launch",
        )

        self.assertFalse(result["satisfied"])

    async def test_invalid_marker_is_a_typed_bad_marker_error(self) -> None:
        with self.assertRaises(Exception) as caught:
            await self._wait(marker="{not json")

        self.assertIn("bad_marker", str(caught.exception))

    async def test_public_contract_declares_both_scan_modes_and_precedence(self) -> None:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        tool = tools["wait_for"]
        self.assertIn("marker", tool.inputSchema["properties"])
        description = tool.description or ""
        self.assertIn("logs_since", description)
        self.assertIn("ignored", description)
        self.assertIn("false positive", description)

        marker_schema = tool.inputSchema["properties"]["marker"]
        self.assertIn("null", json.dumps(marker_schema))


if __name__ == "__main__":
    unittest.main()
