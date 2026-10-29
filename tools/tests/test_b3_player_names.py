"""B3 (fb-20261028-131200-20dc): names in query_all_players, player_find.

Addon side is a text/structural contract over the .c sources, as in
test_player_heal_godmode. The search side drives the real tool through
build_app with a fake bridge runtime, as in test_daemon_query_all_players:
one match returns it, several raise ambiguous_player_name listing every
candidate and mutating nothing (the only bridge call is the read), none
raises player_not_found, and a blank name raises bad_args before any call.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import server
from tests.fence_helpers import announced_capabilities

REPO = Path(__file__).resolve().parents[2]
MESSAGES = (REPO / "addon/scripts/5_Mission/MCPMessages.c").read_text(encoding="utf-8")
BRIDGE = (REPO / "addon/scripts/5_Mission/MCPBridge.c").read_text(encoding="utf-8")


def _row(name: str, uid: str) -> dict:
    return {"uid": uid, "name": name, "pos": [1.0, 2.0, 3.0], "health": 1.0, "in_vehicle": False}


PLAYERS = [_row("Alex", "76561198000000001"), _row("alex", "76561198000000002"), _row("Bea", "76561198000000003")]


class AddonNameRowTest(unittest.TestCase):
    def test_all_player_row_declares_name_after_uid(self) -> None:
        start = MESSAGES.index("class MCPAllPlayer")
        block = MESSAGES[start:MESSAGES.index("};", start)]
        self.assertIn("string uid;", block)
        self.assertIn("string name;", block)
        self.assertLess(block.index("string uid;"), block.index("string name;"))

    def test_bridge_fills_name_from_identity(self) -> None:
        start = BRIDGE.index("protected array<ref MCPAllPlayer> BuildAllPlayers()")
        block = BRIDGE[start:BRIDGE.index("\n\t}\n", start)]
        self.assertIn("player.uid = ident.GetPlainId();", block)
        self.assertIn("player.name = ident.GetName();", block)
        # Rows without an identity stay omitted, as before B3.
        self.assertIn("if (!ident)", block)


class _ResultState:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[str] = []
        self.daemon_generation = "generation-test"

    def enqueue_command(self, cmd, args, peer=None, *, operation_timeout_s=0.0, **_kw):
        self.calls.append(cmd)
        if cmd != "query_all_players" or args != {} or peer != "server":
            return 400, {"error": "unexpected_bridge_call"}
        return 200, {"id": 7}

    def status_snapshot(self) -> dict:
        version = f"{server.EXPECTED_BRIDGE_VERSION}~1.29.0"
        return {"peers": {peer: {"last_poll_age_s": 0.1, "queue_depth": 0, "version": version, "binding_state": "BOUND", "instance_prefix": peer, "bound_last_poll_age_s": 0.1, "capabilities": announced_capabilities(peer)} for peer in ("server", "client")}, "results_pending": 0}

    def take_result(self, command_id, remove=True):
        result, self.result = self.result, None
        return result

    def abandon_command(self, command_id, reason) -> None:
        raise AssertionError(f"unexpected timeout for {command_id}: {reason}")


class _BridgeRuntime(server.Runtime):
    def __init__(self, config, result) -> None:
        super().__init__(config)
        self.loopback = SimpleNamespace(state=_ResultState(result))

    def ensure_peer_allowed(self, peer) -> None:
        return None


class PlayerFindToolTest(unittest.IsolatedAsyncioTestCase):
    async def _call(self, name, players=PLAYERS):
        config = server.ServerConfig(mode="embedded", key="b3-test", log_sink=lambda _m: None)
        runtime = _BridgeRuntime(config, {"ok": 1, "players": players})
        with patch.object(server, "Runtime", return_value=runtime):
            app, built = server.build_app(config)
        self.assertIs(built, runtime)
        return app, runtime

    async def test_one_match_returns_name_and_uid(self) -> None:
        app, runtime = await self._call(" bea ")
        _content, structured = await app.call_tool("player_find", {"name": " bea "})
        self.assertEqual(structured["name"], "Bea")
        self.assertEqual(structured["uid"], "76561198000000003")
        self.assertEqual(structured["candidates"], [{"name": "Bea", "uid": "76561198000000003"}])
        self.assertEqual(runtime.loopback.state.calls, ["query_all_players"])

    async def test_several_matches_error_lists_all_and_mutates_nothing(self) -> None:
        app, runtime = await self._call("ALEX")
        with self.assertRaisesRegex(server.ToolError, "ambiguous_player_name") as caught:
            await app.call_tool("player_find", {"name": "ALEX"})
        message = str(caught.exception)
        for uid in ("76561198000000001", "76561198000000002"):
            self.assertIn(uid, message)
        # The read is the only bridge call: nothing was dispatched or mutated.
        self.assertEqual(runtime.loopback.state.calls, ["query_all_players"])

    async def test_no_match_is_player_not_found(self) -> None:
        app, _runtime = await self._call("Nobody")
        with self.assertRaisesRegex(server.ToolError, "player_not_found"):
            await app.call_tool("player_find", {"name": "Nobody"})

    async def test_blank_name_is_bad_args_before_any_bridge_call(self) -> None:
        for name in ("", "   "):
            app, runtime = await self._call(name)
            with self.assertRaisesRegex(server.ToolError, "bad_args"):
                await app.call_tool("player_find", {"name": name})
            self.assertEqual(runtime.loopback.state.calls, [])


if __name__ == "__main__":
    unittest.main()
