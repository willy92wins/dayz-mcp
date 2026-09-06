"""Lote V product coverage: bridge_status open reason set, entities_query wire,
wait_for timeout rejection, action_use class-name contract."""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import server
from dayz_mcp.server import ServerConfig, ToolError, build_app


def _content_json(result) -> dict:
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, dict):
        return result
    return json.loads(result[0].text)


def _tool_desc(app, name: str) -> str:
    return app._tool_manager.get_tool(name).description or ""


class BridgeStatusDescriptionTest(unittest.TestCase):
    def test_description_lists_authority_and_declares_open_set(self) -> None:
        app, _ = build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))
        desc = _tool_desc(app, "bridge_status")
        reasons = sorted(server.READY_REASONS | set(server._FENCE_BLOCK_READY.values()))
        for reason in reasons:
            self.assertIn(reason, desc)
        low = desc.lower()
        self.assertIn("open set", low)
        self.assertIn("whitelist", low)
        self.assertIn("shape", low)

    def test_fence_mutant_surfaces_in_new_app_only(self) -> None:
        with patch.dict(server._FENCE_BLOCK_READY, {"ZZZ_PROBE": "zzz_fence_probe"}):
            app_fence, _ = build_app(
                ServerConfig(key="k", port=0, log_sink=lambda _m: None)
            )
            desc_fence = _tool_desc(app_fence, "bridge_status")
        app_after, _ = build_app(
            ServerConfig(key="k", port=0, log_sink=lambda _m: None)
        )
        desc_after = _tool_desc(app_after, "bridge_status")
        self.assertIn("zzz_fence_probe", desc_fence)
        self.assertNotIn("zzz_fence_probe", desc_after)


class EntitiesQueryWireTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_players_probe_names_reason(self) -> None:
        app, runtime = build_app(
            ServerConfig(key="k", port=0, log_sink=lambda _m: None)
        )

        async def fake_call(cmd, args, peer, timeout_s):
            if cmd == "entities_query":
                return {"ok": 1, "count_total": 0, "entities": []}
            if cmd == "query_all_players":
                return {"ok": 1, "players": []}
            raise AssertionError(cmd)

        with patch.object(runtime, "call_bridge", fake_call):
            result = _content_json(
                await app.call_tool(
                    "entities_query",
                    {"pos": [1.0, 2.0, 3.0], "radius": 10.0},
                )
            )
        self.assertEqual(result["reason"], "no_player_connected")
        self.assertEqual(result["reliability"], "remote_unverified")


class WaitForWireTest(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_601_via_public_tool(self) -> None:
        app, runtime = build_app(
            ServerConfig(key="k", port=0, log_sink=lambda _m: None)
        )
        calls: list[str] = []

        async def spy(cmd, args, peer, timeout_s):
            calls.append(cmd)
            return {"ok": 1, "players": [{}]}

        with patch.object(runtime, "call_bridge", spy):
            with self.assertRaises(ToolError) as ctx:
                await app.call_tool(
                    "wait_for",
                    {
                        "condition": "players_at_least",
                        "value": 1,
                        "timeout_s": 601,
                        "poll_interval_s": 0.5,
                    },
                )
        self.assertIn("timeout_s must be <= 600", str(ctx.exception))
        self.assertEqual(calls, [])


class ActionUseDescriptionTest(unittest.TestCase):
    def test_description_names_class_name_contract(self) -> None:
        app, _ = build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))
        desc = _tool_desc(app, "action_use")
        low = desc.lower()
        self.assertIn("class name", low)
        self.assertIn("gettype()", low)
        self.assertIn("not the visible", low)


class RunIdMatrixDescriptionTest(unittest.TestCase):
    """fb-20260829-104625-7c88: the client-requires-run_id / server|all-forbid-run_id matrix
    is published on the tool prose AND on the two properties, not only enforced by
    dayz_test_request.py with a bare bad_dayz_test_request."""

    def test_tool_and_property_descriptions_publish_the_matrix(self) -> None:
        app, _ = build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))
        desc = _tool_desc(app, "dayz_test_run")
        self.assertIn("mode=client requires run_id", desc)
        self.assertIn("preserving the server", desc)
        self.assertIn("must NOT pass run_id", desc)
        props = app._tool_manager.get_tool("dayz_test_run").parameters["properties"]
        self.assertEqual(props["mode"]["description"], server.RUN_ID_MATRIX_MODE_DESCRIPTION)
        self.assertEqual(props["run_id"]["description"], server.RUN_ID_MATRIX_RUN_ID_DESCRIPTION)
        # The enum published from the authority survives the description patch.
        self.assertIn("server", props["mode"]["enum"])
        self.assertNotIn("offline", props["mode"]["enum"])


if __name__ == "__main__":
    unittest.main()
