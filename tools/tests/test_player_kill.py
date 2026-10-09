"""player_kill (inbox f4de): closed schema, wire, admission, source contract."""

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
from dayz_mcp.core import EXPECTED_BRIDGE_VERSION, version_state_for
from dayz_mcp.session_coordination import (
    LEASE_GRACE_S,
    SESSION_TTL_S,
    ClientIdentity,
    SessionCoordinator,
)
from tests._addon_paths import addon_root
from dayz_mcp.peer_liveness import PEER_STALE_S
from tests.fence_helpers import INST_SERVER, PID_SERVER, bound_queue


BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
MESSAGES = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
UID = "76561198000000001"


def _tool_error_text(exc: BaseException) -> str:
    text = str(exc)
    for attr in ("exceptions", "error"):
        nested = getattr(exc, attr, None)
        if nested:
            text = text + " " + str(nested)
    return text


def _identity() -> dict[str, object]:
    return {
        "platform": "codex",
        "pid": 101,
        "ppid": 10,
        "started_at_utc": "2026-07-14T20:00:00Z",
        "session_id": "kill-owner",
        "task_label": "player kill",
    }


def _bound_state(**kwargs: object) -> loopback.ServerState:
    state = loopback.ServerState(key="k", **kwargs)  # type: ignore[arg-type]
    state.daemon_generation = "gen-kill"
    state.install_bound_peer(instance=INST_SERVER, role="server", pid=PID_SERVER, run_id="kill-run")
    return state


def _announce(state: loopback.ServerState, commands: list[str], ach: str | None, generation: str | None = None) -> None:
    state._peer_caps["server"] = {
        "generation": state.daemon_generation if generation is None else generation,
        "commands": list(commands),
        "reason": "ok",
        "arg_contract_hash": ach,
        "announced_at": state._now(),
        "instance": INST_SERVER,
    }


def _full_commands() -> list[str]:
    return sorted(server._BRIDGE_COMMAND_TOOLS["server"])


class PlayerKillSchemaTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        self.tools = {tool.name: tool for tool in self.app._tool_manager.list_tools()}

    async def _forwarded(self, arguments: dict) -> tuple:
        with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})) as call:
            await self.app.call_tool("player_kill", arguments)
        call.assert_awaited_once()
        return call.await_args.args

    async def _refused(self, arguments: dict) -> str:
        with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})) as call:
            with self.assertRaises(Exception) as caught:
                await self.app.call_tool("player_kill", arguments)
        call.assert_not_awaited()
        return _tool_error_text(caught.exception)

    def test_uid_is_required_and_the_schema_is_closed(self) -> None:
        tool = self.tools["player_kill"]
        self.assertEqual(set(tool.parameters["properties"]), {"uid", "timeout_s"})
        self.assertEqual(tool.parameters.get("required"), ["uid"])
        self.assertIs(tool.parameters.get("additionalProperties"), False)
        self.assertIn("player_kill", server._CLOSED_SCHEMA_TOOLS)

    async def test_wire_is_uid_only(self) -> None:
        self.assertEqual(
            await self._forwarded({"uid": UID}),
            ("player_kill", {"uid": UID}, "server", 15.0),
        )
        self.assertEqual(
            await self._forwarded({"uid": UID, "timeout_s": 4}),
            ("player_kill", {"uid": UID}, "server", 4.0),
        )

    async def test_empty_uid_and_unknown_keys_are_rejected(self) -> None:
        for arguments, part in (
            ({}, "uid"),
            ({"uid": ""}, "non-empty"),
            ({"uid": UID, "id": UID}, "unexpected arguments: id"),
            ({"player": UID}, "unexpected arguments: player"),
        ):
            with self.subTest(arguments=arguments):
                self.assertIn(part, await self._refused(arguments))


class PlayerKillAdmissionTest(unittest.TestCase):
    def _enqueue(self, state: loopback.ServerState, args: dict | None = None, **kwargs: object):
        before_id = state._next_id
        before_q = len(bound_queue(state, "server"))
        status, body = state.enqueue_command(
            "player_kill",
            {"uid": UID} if args is None else args,
            peer="server",
            **kwargs,
        )
        self.assertEqual(len(bound_queue(state, "server")), before_q)
        self.assertEqual(state._next_id, before_id)
        self.assertNotEqual(status, 200)
        return status, body

    def test_old_version_refuses_without_a_queue_id(self) -> None:
        state = _bound_state(version_validator=lambda _version: "version_mismatch")
        _announce(state, _full_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        status, body = self._enqueue(state)
        self.assertEqual(body["error"], "version_blocked")
        self.assertEqual(status, 409)

    def test_missing_verb_wrong_hash_and_stale_census_refuse(self) -> None:
        full = _full_commands()
        cases = {
            "missing_verb": (["entities_query"], EXPECTED_SERVER_ARG_CONTRACT_HASH, None, "bridge_capability_missing"),
            "absent_hash": (full, None, None, "arg_contract_mismatch"),
            "wrong_hash": (full, "0" * 16, None, "arg_contract_mismatch"),
            "stale": (full, EXPECTED_SERVER_ARG_CONTRACT_HASH, "gen-old", "bridge_capability_missing"),
        }
        for name, (commands, ach, generation, error) in cases.items():
            with self.subTest(name=name):
                state = _bound_state()
                _announce(state, commands, ach, generation)
                _status, body = self._enqueue(state)
                self.assertEqual(body["error"], error)

    def test_no_lease_expired_lease_and_fenced_run_do_not_queue(self) -> None:
        clock = {"now": 0.0}
        coordinator = SessionCoordinator(
            time_fn=lambda: clock["now"],
            token_fn=lambda: "kill-token",
            id_fn=lambda: "kill-lease",
            audit=lambda _event: True,
            cleanup=lambda *_args: None,
        )
        state = _bound_state(coordination=coordinator)
        state.retail_probe = lambda: {"known": True, "processes": []}
        _announce(state, _full_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        client = ClientIdentity.from_payload(_identity())
        status, _body = self._enqueue(state, identity_payload=_identity())
        self.assertEqual(status, 423)
        acquired, active = coordinator.acquire(client, "kill")
        self.assertEqual(acquired, 200, active)
        token = active["lease_token"]
        state._fenced_runs.add("kill-run")
        status, body = self._enqueue(
            state, identity_payload=_identity(), lease_token=token
        )
        self.assertEqual(body["error"], "run_not_owned")
        state._fenced_runs.discard("kill-run")
        clock["now"] = SESSION_TTL_S + LEASE_GRACE_S + 1.0
        status, body = self._enqueue(
            state, identity_payload=_identity(), lease_token=token
        )
        self.assertEqual(body["error"], "lease_expired")

    def test_retired_binding_refuses(self) -> None:
        state = _bound_state()
        _announce(state, _full_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        with state._lock:
            state._retire_instance_locked(INST_SERVER, "stopped", [], [])
        status, body = state.enqueue_command(
            "player_kill", {"uid": UID}, peer="server"
        )
        self.assertEqual(body["error"], "binding_retired")
        self.assertNotEqual(status, 200)
        self.assertEqual(state._next_id, 1)

    def test_accredited_census_queues_one_command(self) -> None:
        state = _bound_state()
        _announce(state, _full_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        status, body = state.enqueue_command("player_kill", {"uid": UID}, peer="server")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["cmd"], "player_kill")
        queued = [
            {key: value for key, value in command.items() if key != "_broker_pin"}
            for command in bound_queue(state, "server")
        ]
        self.assertEqual(queued, [{"id": 1, "cmd": "player_kill", "args": {"uid": UID}}])
        status, bad = state.enqueue_command("player_kill", {"uid": ""}, peer="server")
        self.assertEqual((status, bad), (400, {"error": "bad_args"}))
        self.assertEqual(len(bound_queue(state, "server")), 1)

    def test_delivery_drops_a_command_the_bound_peer_no_longer_advertises(self) -> None:
        state = _bound_state()
        _announce(state, _full_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        status, body = state.enqueue_command("player_kill", {"uid": UID}, peer="server")
        self.assertEqual(status, 200, body)
        reduced = [name for name in _full_commands() if name != "player_kill"]
        status, polled = accredited_poll_with_caps(state, reduced, EXPECTED_SERVER_ARG_CONTRACT_HASH)
        self.assertEqual(status, 200)
        self.assertEqual(polled["commands"], [])
        stored = state.take_result(body["id"])
        self.assertEqual(stored["error"], "bridge_capability_missing")
        self.assertEqual(bound_queue(state, "server"), [])

    def test_aged_announcement_and_replaced_binding_do_not_allocate(self) -> None:
        clock = {"now": 0.0}
        coordinator = SessionCoordinator(
            time_fn=lambda: clock["now"],
            token_fn=lambda: "kill-token",
            id_fn=lambda: "kill-lease",
            audit=lambda _event: True,
            cleanup=lambda *_args: None,
        )
        state = _bound_state(time_fn=lambda: clock["now"], coordination=coordinator)
        state.retail_probe = lambda: {"known": True, "processes": []}
        state.version_validator = lambda version: version_state_for(
            version, require_version=True, expected_game_version=None
        )[0]
        _announce(state, _full_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        state._poll_versions["server"] = f"{EXPECTED_BRIDGE_VERSION}~1.30.0"
        clock["now"] = 1000.0
        self.assertGreaterEqual(clock["now"] - 0.0, PEER_STALE_S)
        client = ClientIdentity.from_payload(_identity())
        acquired, active = coordinator.acquire(client, "kill")
        self.assertEqual(acquired, 200, active)
        status, body = self._enqueue(
            state, identity_payload=_identity(), lease_token=active["lease_token"]
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "bridge_capability_missing")

        clock["now"] = 0.0
        state = _bound_state(time_fn=lambda: clock["now"])
        _announce(state, _full_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        with state._lock:
            state._retire_instance_locked(INST_SERVER, "stopped", [], [])
        replacement = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        state.install_bound_peer(
            instance=replacement, role="server", pid=PID_SERVER + 1, run_id="kill-run-2"
        )
        before = state._next_id
        status, body = state.enqueue_command(
            "player_kill", {"uid": UID}, peer="server"
        )
        self.assertEqual(body["error"], "bridge_capability_missing")
        self.assertNotEqual(status, 200)
        self.assertEqual(state._next_id, before)
        self.assertEqual(state._bound_queues.get(replacement, []), [])


def accredited_poll_with_caps(state, commands: list[str], ach: str):
    return state.record_poll(
        "server",
        f"{EXPECTED_BRIDGE_VERSION}~1.29.0",
        instance=INST_SERVER,
        source_pid=PID_SERVER,
        caps=",".join(commands),
        ach=ach,
    )


class PlayerKillSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = BRIDGE.read_text(encoding="utf-8")
        self.messages = MESSAGES.read_text(encoding="utf-8")
        match = re.search(
            r"protected bool DispatchPlayerKill\(MCPCommand command, MCPResult result\)\s*\{(?P<body>.*?)\n\t\}",
            self.bridge,
            re.S,
        )
        self.assertIsNotNone(match)
        self.body = match.group("body")

    def test_handler_order_and_the_vanilla_primitive(self) -> None:
        self.assertLess(self.body.index('killUid == ""'), self.body.index("FindHumanByUid"))
        self.assertLess(self.body.index("FindHumanByUid"), self.body.index("GetIdentity"))
        self.assertLess(self.body.index("GetPlainId() != killUid"), self.body.index("IsAlive()"))
        self.assertLess(self.body.index("IsAlive()"), self.body.index("ReleaseBody"))
        self.assertLess(self.body.index("ReleaseBody"), self.body.index("SetHealth(0)"))
        self.assertIn("kill_not_applied", self.body)
        self.assertIn("SetAllowDamage(damageAllowed)", self.body)
        self.assertIn("godmode_policy_preserved = true", self.body)
        self.assertNotIn("EEKilled", self.body)
        self.assertNotIn("EEHitBy", self.body)
        self.assertNotIn("player_respawn", self.body)
        self.assertNotIn("Choose(", self.body)
        self.assertIn('const string MCP_BRIDGE_VERSION = "12";', self.messages)
        self.assertIn("player_kill", loopback.SERVER_COMMANDS)
        self.assertEqual(server.SERVER_ARG_CONTRACT["player_kill"], ("uid",))
        self.assertIn(
            server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
            self.bridge,
        )

    def test_unknown_uid_cannot_fall_back_to_the_first_human(self) -> None:
        self.assertNotIn("GetFirstHuman", self.body)
        self.assertNotIn("ResolvePlayer", self.body)


if __name__ == "__main__":
    unittest.main()
