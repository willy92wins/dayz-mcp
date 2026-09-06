"""Lote B products reach the client over the wire (Codex review B backlog).

The three description edits of fb-20260904-123220-5930 and the ui_click failure
contract of fb-20260829-221423-b2c4 are prose. A registry fingerprint catches a
change but cannot say which sentence went missing; this pins the sentences an
agent relies on, read through a real in-memory MCP session rather than the tool
manager, so a wrapper that rewrote descriptions would fail here.
"""
from __future__ import annotations

import unittest

from mcp.shared.memory import create_connected_server_and_client_session

from dayz_mcp import server
from dayz_mcp.server import ServerConfig, build_app

EXPECTED = {
    "entities_query": (
        "pos.y is used as given",
        "nothing snaps it to the surface",
        "pos=[x, surface_query.y, z]",
        "nearest_player_m",
    ),
    "logs_since": (
        "marker advances only to the end of the lines RETURNED, never to EOF",
        "max_lines=1 marks one line INTO the launch tail",
        "script_<date>.log",
    ),
    "capture_screenshot": (
        "PHYSICAL pixels (DPI-aware)",
        "SetProcessDpiAwareness",
        "1920 -> 1280",
    ),
    "ui_click": (
        "On failure the error text keeps the bridge diagnostics after the code",
        "an empty handler means no handler ran, a named one ran and declined",
    ),
}


class LoteBWireDescriptionsTest(unittest.IsolatedAsyncioTestCase):
    async def test_description_sentences_reach_a_real_client_session(self) -> None:
        app, _runtime = build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))
        async with create_connected_server_and_client_session(app._mcp_server) as session:
            listed = await session.list_tools()
        descriptions = {tool.name: tool.description or "" for tool in listed.tools}
        for name, fragments in EXPECTED.items():
            with self.subTest(name):
                self.assertIn(name, descriptions)
                for fragment in fragments:
                    self.assertIn(fragment, descriptions[name], (name, fragment))


if __name__ == "__main__":
    unittest.main()
