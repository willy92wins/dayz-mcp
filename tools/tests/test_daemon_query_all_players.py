from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, server


class _ResultState:
    def __init__(self, result: dict) -> None:
        self.result = result

    def enqueue_command(
        self,
        cmd: str,
        args: dict,
        peer: str | None = None,
        *,
        operation_timeout_s: float = 0.0,
        **_kwargs,
    ) -> tuple[int, dict]:
        if cmd != "query_all_players" or args != {} or peer != "server":
            return 400, {"error": "unexpected_bridge_call"}
        return 200, {"id": 7}

    def take_result(self, command_id: int, remove: bool = True) -> dict | None:
        result = self.result
        self.result = None
        return result

    def abandon_command(self, command_id: int, reason: str) -> None:
        raise AssertionError(f"unexpected timeout for {command_id}: {reason}")


class _BridgeRuntime(server.Runtime):
    def __init__(self, config: server.ServerConfig, result: dict) -> None:
        super().__init__(config)
        self.loopback = SimpleNamespace(state=_ResultState(result))

    def ensure_peer_allowed(self, peer: str) -> None:
        return None


def _config() -> server.ServerConfig:
    return server.ServerConfig(
        mode="embedded",
        key="query-all-players-test",
        log_sink=lambda _message: None,
    )


class QueryAllPlayersWhitelistTest(unittest.TestCase):
    def test_query_all_players_is_server_command(self) -> None:
        self.assertIn("query_all_players", loopback.SERVER_COMMANDS)

    def test_non_whitelisted_command_is_still_rejected(self) -> None:
        state = loopback.ServerState("query-all-players-test")
        status, body = state.enqueue_command(
            "definitely_not_whitelisted",
            {},
            peer="server",
            operation_timeout_s=1.0,
        )
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "not_whitelisted"})


class QueryAllPlayersToolTest(unittest.IsolatedAsyncioTestCase):
    async def _call_tool(self, bridge_result: dict):
        config = _config()
        runtime = _BridgeRuntime(config, bridge_result)
        with patch.object(server, "Runtime", return_value=runtime):
            app, built_runtime = server.build_app(config)
        self.assertIs(built_runtime, runtime)
        return await app.call_tool("query_all_players", {})

    async def test_wrapper_returns_nonempty_players_body_unchanged(self) -> None:
        body = {
            "ok": 1,
            "players": [
                {
                    "uid": "76561198000000001",
                    "pos": [123.0, 4.5, 678.0],
                    "health": 0.75,
                    "in_vehicle": True,
                }
            ],
        }
        _content, structured = await self._call_tool(body)
        self.assertEqual(structured, body)

    async def test_wrapper_treats_empty_players_as_success(self) -> None:
        body = {"ok": 1, "players": []}
        _content, structured = await self._call_tool(body)
        self.assertEqual(structured, body)

    async def test_wrapper_propagates_bridge_error(self) -> None:
        with self.assertRaisesRegex(server.ToolError, "identity_required"):
            await self._call_tool({"ok": 0, "error": "identity_required"})


if __name__ == "__main__":
    unittest.main()
