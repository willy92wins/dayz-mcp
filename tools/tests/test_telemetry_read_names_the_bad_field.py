"""telemetry_read must say WHICH argument is wrong.

Reported from the consumer side: a bare `bad_args` leaves the caller guessing
between mode, type and radius, and guessing is the entire cost of the error. The
rest of the tool surface already names the field (`bad_throttle`, `bad_steer`,
`bad_hold_ttl_s`), so this only brings one verb in line.

A field name is not host content, so it can cross the MCP wire --
what that rule keeps out is paths and messages, not the identity of a parameter
the caller itself supplied.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402

from dayz_mcp import server  # noqa: E402
from dayz_mcp.server import ServerConfig, build_app  # noqa: E402

from tests.test_client_mode import _fixture_client_runtime  # noqa: E402


class TelemetryReadNamesTheBadFieldTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _m: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=runtime):
            self.app, _built = build_app(config)

    async def _error_for(self, **kwargs) -> str:
        with self.assertRaises(Exception) as ctx:
            await self.app.call_tool("telemetry_read", kwargs)
        return str(ctx.exception)

    async def test_unknown_mode_says_mode(self) -> None:
        message = await self._error_for(mode="nonsense")
        self.assertIn("bad_mode", message)

    async def test_object_at_without_type_says_type(self) -> None:
        message = await self._error_for(
            mode="object_at", pos=[1.0, 2.0, 3.0], radius=5.0
        )
        self.assertIn("bad_type", message)

    async def test_object_at_with_bad_radius_says_radius(self) -> None:
        message = await self._error_for(
            mode="object_at", type="CivilianSedan", pos=[1.0, 2.0, 3.0], radius=0.0
        )
        self.assertIn("bad_radius", message)

    async def test_the_three_errors_are_distinguishable(self) -> None:
        # The point of the change: three different mistakes, three different
        # answers. If any two collapse, the caller is back to guessing.
        mode = await self._error_for(mode="nonsense")
        kind = await self._error_for(mode="object_at", pos=[1.0, 2.0, 3.0], radius=5.0)
        radius = await self._error_for(
            mode="object_at", type="CivilianSedan", pos=[1.0, 2.0, 3.0], radius=-1.0
        )
        self.assertEqual(3, len({mode, kind, radius}), (mode, kind, radius))


if __name__ == "__main__":
    unittest.main()
