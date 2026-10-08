"""bot_start / bot_stop (inbox 120f): allowlist, admission, compile guards."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, server
from dayz_mcp.bridge_readiness import EXPECTED_SERVER_ARG_CONTRACT_HASH
from dayz_mcp.core import EXPECTED_BRIDGE_VERSION
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator
from tests._addon_paths import addon_root
from tests.fence_helpers import INST_SERVER, PID_SERVER, bound_queue


BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
BOT = addon_root() / "scripts" / "5_Mission" / "MCP_BotControl.c"
ACTION = "PLAYER_BOT_RANDOMIZE_MOVEMENT"


def _tool_error_text(exc: BaseException) -> str:
    text = str(exc)
    nested = getattr(exc, "exceptions", None)
    if nested:
        text = text + " " + str(nested)
    return text


def _bound_state(**kwargs: object) -> loopback.ServerState:
    state = loopback.ServerState(key="k", **kwargs)  # type: ignore[arg-type]
    state.daemon_generation = "gen-bot"
    state.install_bound_peer(instance=INST_SERVER, role="server", pid=PID_SERVER, run_id="bot-run")
    return state


def _announce(state: loopback.ServerState) -> None:
    state._peer_caps["server"] = {
        "generation": state.daemon_generation,
        "commands": sorted(server._BRIDGE_COMMAND_TOOLS["server"]),
        "reason": "ok",
        "arg_contract_hash": EXPECTED_SERVER_ARG_CONTRACT_HASH,
        "announced_at": state._now(),
        "instance": INST_SERVER,
    }


def _strip_comments(source: str) -> str:
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)


class BotSchemaTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        self.tools = {tool.name: tool for tool in self.app._tool_manager.list_tools()}

    async def _refused(self, tool: str, arguments: dict) -> str:
        with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})) as call:
            with self.assertRaises(Exception) as caught:
                await self.app.call_tool(tool, arguments)
        call.assert_not_awaited()
        return _tool_error_text(caught.exception)

    async def _forwarded(self, tool: str, arguments: dict) -> tuple:
        with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})) as call:
            await self.app.call_tool(tool, arguments)
        return call.await_args.args

    def test_closed_schemas_and_allowlist(self) -> None:
        start = self.tools["bot_start"]
        stop = self.tools["bot_stop"]
        self.assertEqual(set(start.parameters["properties"]), {"object_id", "action", "ttl_s", "timeout_s"})
        self.assertEqual(start.parameters.get("required"), ["object_id", "action"])
        self.assertIs(start.parameters.get("additionalProperties"), False)
        self.assertEqual(set(stop.parameters["properties"]), {"object_id", "timeout_s"})
        self.assertEqual(stop.parameters.get("required"), ["object_id"])
        self.assertIs(stop.parameters.get("additionalProperties"), False)
        self.assertEqual(loopback.BOT_START_ACTIONS, tuple(sorted(loopback.BOT_START_ACTIONS)) if False else loopback.BOT_START_ACTIONS)
        self.assertNotIn("PLAYER_BOT_STOP_CURRENT", loopback.BOT_START_ACTIONS)
        self.assertNotIn("PLAYER_BOT_TEST_SWAP_C2H", loopback.BOT_START_ACTIONS)
        self.assertEqual(len(loopback.BOT_START_ACTIONS), 10)

    async def test_wire_and_malformed_arguments(self) -> None:
        self.assertEqual(
            await self._forwarded("bot_start", {"object_id": 4, "action": ACTION}),
            ("bot_start", {"object_id": 4, "action": ACTION, "bot_ttl_s": 5.0}, "server", 15.0),
        )
        self.assertEqual(
            await self._forwarded("bot_stop", {"object_id": 4}),
            ("bot_stop", {"object_id": 4}, "server", 15.0),
        )
        for tool, arguments, part in (
            ("bot_start", {"object_id": 0, "action": ACTION}, "positive"),
            ("bot_start", {"object_id": 4, "action": "PLAYER_BOT_STOP_CURRENT"}, "action"),
            ("bot_start", {"object_id": 4, "action": "12"}, "action"),
            ("bot_start", {"object_id": 4, "action": ACTION, "ttl_s": 0}, "(0, 30]"),
            ("bot_start", {"object_id": 4, "action": ACTION, "ttl_s": 31}, "(0, 30]"),
            ("bot_start", {"object_id": 4, "action": ACTION, "extra": 1}, "unexpected arguments: extra"),
            ("bot_stop", {"object_id": -1}, "positive"),
            ("bot_stop", {"id": 4}, "unexpected arguments: id"),
        ):
            with self.subTest(tool=tool, arguments=arguments):
                self.assertIn(part, await self._refused(tool, arguments))


class BotAdmissionTest(unittest.TestCase):
    def test_older_census_does_not_queue_either_verb(self) -> None:
        for cmd, args in (
            ("bot_start", {"object_id": 4, "action": ACTION, "bot_ttl_s": 5.0}),
            ("bot_stop", {"object_id": 4}),
        ):
            with self.subTest(cmd=cmd):
                state = _bound_state()
                state._peer_caps["server"] = {
                    "generation": state.daemon_generation,
                    "commands": ["entities_query", "world_spawn"],
                    "reason": "ok",
                    "arg_contract_hash": EXPECTED_SERVER_ARG_CONTRACT_HASH,
                }
                before = state._next_id
                status, body = state.enqueue_command(cmd, args, peer="server")
                self.assertEqual(body["error"], "bridge_capability_missing")
                self.assertNotEqual(status, 200)
                self.assertEqual(state._next_id, before)
                self.assertEqual(bound_queue(state, "server"), [])

    def test_lease_release_delivers_bot_stop_for_the_owned_dummy(self) -> None:
        state_box: dict[str, loopback.ServerState] = {}

        def cleanup(session_id: str, lease_id: str, reason: str, vehicle_active: bool):
            return state_box["state"].cleanup_owner(
                session_id, lease_id, reason, vehicle_active
            )

        coordinator = SessionCoordinator(
            token_fn=lambda: "bot-token",
            id_fn=lambda: "bot-lease",
            audit=lambda _event: True,
            cleanup=cleanup,
        )
        state = _bound_state(coordination=coordinator)
        state_box["state"] = state
        state.retail_probe = lambda: {"known": True, "processes": []}
        _announce(state)
        identity = {
            "platform": "codex",
            "pid": 101,
            "ppid": 10,
            "started_at_utc": "2026-07-14T20:00:00Z",
            "session_id": "bot-owner",
            "task_label": "bot",
        }
        client = ClientIdentity.from_payload(identity)
        status, active = coordinator.acquire(client, "bot")
        self.assertEqual(status, 200, active)
        status, body = state.enqueue_command(
            "bot_start",
            {"object_id": 9, "action": ACTION, "bot_ttl_s": 30.0},
            peer="server",
            identity_payload=identity,
            lease_token=active["lease_token"],
        )
        self.assertEqual(status, 200, body)
        status, started = state.record_poll(
            "server",
            f"{EXPECTED_BRIDGE_VERSION}~1.30.0",
            instance=INST_SERVER,
            source_pid=PID_SERVER,
            caps=",".join(sorted(server._BRIDGE_COMMAND_TOOLS["server"])),
            ach=EXPECTED_SERVER_ARG_CONTRACT_HASH,
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            started["commands"],
            [{"id": body["id"], "cmd": "bot_start", "args": {"object_id": 9, "action": ACTION, "bot_ttl_s": 30.0}}],
        )
        status, released = coordinator.release(client, active["lease_token"])
        self.assertEqual(status, 200, released)
        self.assertEqual(released["cleanup"].get("bot_stop_enqueued"), 1)
        self.assertFalse(released.get("cleanup_degraded"))
        status, polled = state.record_poll(
            "server",
            f"{EXPECTED_BRIDGE_VERSION}~1.30.0",
            instance=INST_SERVER,
            source_pid=PID_SERVER,
            caps=",".join(sorted(server._BRIDGE_COMMAND_TOOLS["server"])),
            ach=EXPECTED_SERVER_ARG_CONTRACT_HASH,
        )
        self.assertEqual(status, 200)
        self.assertEqual(polled["bind"], "BOUND")
        self.assertEqual(
            polled["commands"],
            [{"id": body["id"] + 1, "cmd": "bot_stop", "args": {"object_id": 9}}],
        )
        self.assertIsNone(state.take_result(body["id"] + 1))
        source = Path(_TOOLS_DIR / "dayz_mcp" / "loopback.py").read_text(encoding="utf-8")
        self.assertIn("enqueue_bot_stops_for_lease(lease_id)", source)
        self.assertNotIn("enqueue_bot_stops_for_lease(release_lease_id)", source)

    def test_cleanup_reports_a_bot_stop_that_admission_refuses(self) -> None:
        state_box: dict[str, loopback.ServerState] = {}

        def cleanup(session_id: str, lease_id: str, reason: str, vehicle_active: bool):
            return state_box["state"].cleanup_owner(
                session_id, lease_id, reason, vehicle_active
            )

        coordinator = SessionCoordinator(
            token_fn=lambda: "bot-token-2",
            id_fn=lambda: "bot-lease-2",
            audit=lambda _event: True,
            cleanup=cleanup,
        )
        state = _bound_state(coordination=coordinator)
        state_box["state"] = state
        state.retail_probe = lambda: {"known": True, "processes": []}
        _announce(state)
        identity = {
            "platform": "codex",
            "pid": 102,
            "ppid": 10,
            "started_at_utc": "2026-07-14T20:00:00Z",
            "session_id": "bot-owner-2",
            "task_label": "bot",
        }
        client = ClientIdentity.from_payload(identity)
        status, active = coordinator.acquire(client, "bot")
        self.assertEqual(status, 200, active)
        status, _body = state.enqueue_command(
            "bot_start",
            {"object_id": 9, "action": ACTION, "bot_ttl_s": 5.0},
            peer="server",
            identity_payload=identity,
            lease_token=active["lease_token"],
        )
        self.assertEqual(status, 200)
        state._peer_caps["server"]["commands"] = ["entities_query"]
        before = state._next_id
        status, released = coordinator.release(client, active["lease_token"])
        self.assertEqual(status, 200, released)
        self.assertEqual(released["cleanup"].get("bot_stop_enqueued"), 0)
        self.assertIn("bot_stop_failed", released["cleanup_degraded"])
        self.assertEqual(state._next_id, before)


class BotSourceTest(unittest.TestCase):
    def test_bot_symbols_stay_inside_the_three_guards(self) -> None:
        raw = BOT.read_text(encoding="utf-8")
        clean = _strip_comments(raw)
        depth = 0
        guarded = []
        open_macros = []
        for line in clean.splitlines():
            stripped = line.strip()
            if stripped.startswith("#ifdef ") or stripped.startswith("#ifndef "):
                open_macros.append(stripped.split(None, 1)[1])
                if open_macros[-3:] == ["DAYZ_1_30", "ROBOCLIENT", "INPUT_OVERRIDE"] or (
                    len(open_macros) >= 3 and open_macros[-3:] == ["DAYZ_1_30", "ROBOCLIENT", "INPUT_OVERRIDE"]
                ):
                    depth = 3
                continue
            if stripped == "#endif":
                if open_macros:
                    open_macros.pop()
                depth = 3 if open_macros[-3:] == ["DAYZ_1_30", "ROBOCLIENT", "INPUT_OVERRIDE"] else 0
                continue
            if depth < 3:
                guarded.append(stripped)
        outside = "\n".join(guarded)
        for symbol in ("m_Bot", "BotEventStartDebug", "BotEventStop", "OnSpawnedFromConsole", "EActions"):
            self.assertNotIn(symbol, outside, symbol)
        self.assertNotRegex(outside, r"\bBot\b")
        self.assertIn("bot_unavailable", clean)
        self.assertLess(clean.index("Available()"), clean.index("OnSpawnedFromConsole"))

    def test_dispatch_validates_then_refuses_before_init(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        match = re.search(
            r"protected bool DispatchBotStart\(MCPCommand command, MCPResult result\)\s*\{(?P<body>.*?)\n\t\}",
            source,
            re.S,
        )
        self.assertIsNotNone(match)
        body = match.group("body")
        self.assertLess(body.index("BotStartArgsOk"), body.index("Available()"))
        self.assertLess(body.index("bot_unavailable"), body.index("MCPBotControl.Start"))
        self.assertIn("bot_busy", BOT.read_text(encoding="utf-8"))
        self.assertIn("bot_action_rejected", BOT.read_text(encoding="utf-8"))
        self.assertIn('releasedBy = "idle"', BOT.read_text(encoding="utf-8"))
        self.assertIn('Release(bridge, objectId, "ttl")', BOT.read_text(encoding="utf-8"))
        self.assertIn('Release(bridge, objectId, "shutdown")', BOT.read_text(encoding="utf-8"))
        self.assertIn('Release(bridge, objectId, "death")', BOT.read_text(encoding="utf-8"))
        self.assertIn('Release(bridge, objectId, "deleted")', BOT.read_text(encoding="utf-8"))
        for name in loopback.BOT_START_ACTIONS:
            self.assertIn(f'action == "{name}"', source)
        self.assertNotIn("PLAYER_BOT_STOP_CURRENT", source)
        self.assertNotIn("PLAYER_BOT_TEST_SWAP_C2H", source)
        self.assertEqual(server.SERVER_ARG_CONTRACT["bot_start"], ("action", "bot_ttl_s", "object_id"))
        self.assertEqual(server.SERVER_ARG_CONTRACT["bot_stop"], ("object_id",))
        self.assertIn("bot_start", loopback.SERVER_COMMANDS)
        self.assertIn("bot_stop", loopback.SERVER_COMMANDS)
        self.assertIn('"bot_start": "bot_start"', Path(_TOOLS_DIR / "dayz_mcp" / "bridge_readiness.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
