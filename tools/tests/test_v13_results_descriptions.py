"""Honest results and descriptions (v1.3 backlog): a412, 95d8, c931, 4f1c, 00c4 part b.

Tool descriptions and one Python ToolError text only; no Enforce, no PBO.
- a412: the vehicle_control and engine_set replies confirm the command reached
  the car and are not telemetry (fb-20260823-130809-a412).
- 95d8: the server-side body does not travel with a client-owned car, so the
  player reads keep the server position (fb-20260823-130833-95d8).
- c931: condition_failed means action_use did not dispatch; it does not promise
  that nothing follows (fb-20260818-161011-c931).
- 4f1c: world_spawn's type is a CfgVehicles classname, and unknown_type names
  the type received (fb-20260823-131632-4f1c).
- 00c4 part b: session_status.claimable does not mean the lease is free
  (fb-20260821-162508-00c4). The coordinator tests anchor the two cases the
  description names, so the sentence fails here if _claimable_locked changes.
The object_doors raycast sentence (#93 point 6) is pinned in test_object_doors.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp import server
from dayz_mcp.server import ServerConfig, ToolError, build_app
from tests.lease_helpers import MID, FakeClock, _coord, _identity


class V13DescriptionsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, _runtime = build_app(
            ServerConfig(key="k", port=0, log_sink=lambda _m: None)
        )
        self.tools = {tool.name: tool for tool in await self.app.list_tools()}

    def _description(self, name: str) -> str:
        return self.tools[name].description or ""

    def test_drive_replies_say_they_are_not_telemetry(self) -> None:
        for name, subject in (("vehicle_control", "control"), ("engine_set", "command")):
            with self.subTest(tool=name):
                description = self._description(name)
                self.assertIn(
                    f"The reply confirms the {subject} reached the car and is not telemetry",
                    description,
                )
                self.assertIn(
                    "engine_on_server is the only vehicle field it carries", description
                )
                self.assertIn(
                    "vehicle_telemetry reports gear, speed, position and ownership",
                    description,
                )

    def test_player_reads_say_the_body_stays_off_a_client_owned_car(self) -> None:
        for name in ("query_player_state", "query_all_players"):
            with self.subTest(tool=name):
                description = self._description(name)
                self.assertIn("vehicle_get_in_client seats only the client", description)
                self.assertIn(
                    "the server-side body does not travel with the client-owned car",
                    description,
                )
                self.assertIn("player_teleport position", description)
                self.assertIn("Read the car with vehicle_telemetry.", description)
        self.assertIn("in_vehicle stays 0", self._description("query_all_players"))

    def test_action_use_condition_failed_is_not_a_promise_of_no_effect(self) -> None:
        description = self._description("action_use")
        self.assertIn("condition_failed means this call did not dispatch the action", description)
        self.assertIn("PerformActionStart was not called", description)
        self.assertIn("it does not guarantee that nothing follows", description)
        self.assertIn("about 10 s later", description)
        self.assertIn("Confirm with a later read.", description)

    def test_world_spawn_names_the_classname_contract(self) -> None:
        description = self._description("world_spawn")
        self.assertIn("type is a CfgVehicles classname", description)
        self.assertIn('type="CivilianSedan"', description)
        self.assertIn("returns unknown_type naming the type received", description)

    def test_session_status_says_what_claimable_does_not_mean(self) -> None:
        description = self._description("session_status")
        self.assertIn(
            "claimable only says that no fault, fence, handoff, or grant or release "
            "in flight blocks the next grant",
            description,
        )
        self.assertIn("It stays true while a session holds the lease", description)
        self.assertIn("while grace reserves a lapsed lease", description)
        self.assertIn("claimable=true does not mean the lease is free", description)
        self.assertIn("owner is null when no session holds it", description)
        self.assertIn("only when grace is null and queue is empty too", description)


class UnknownSpawnTypeTextTest(unittest.IsolatedAsyncioTestCase):
    """4f1c: the bridge sends a bare unknown_type; the tool names the type it sent."""

    async def asyncSetUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="k", port=0, log_sink=lambda _m: None)
        )

    async def _spawn_error(self, spawn_type: str, bridge_code: str) -> tuple[str, AsyncMock]:
        # The error the runtime raises for {ok:0, error:<code>} (wait_for_result).
        refusal = server._bridge_error({"ok": 0, "error": bridge_code}, "world_spawn")
        call = AsyncMock(side_effect=refusal)
        with patch.object(self.runtime, "call_bridge", new=call):
            with self.assertRaises(ToolError) as raised:
                await self.app.call_tool(
                    "world_spawn", {"type": spawn_type, "pos": [7500.0, 0.0, 7500.0]}
                )
        message = str(raised.exception)
        wrapper = "Error executing tool world_spawn: "
        self.assertTrue(message.startswith(wrapper), message)
        return message[len(wrapper) :], call

    async def test_unknown_type_names_the_type_and_the_classname_contract(self) -> None:
        message, call = await self._spawn_error("vehicle", "unknown_type")

        self.assertEqual(
            message,
            "unknown_type: type 'vehicle' is not a CfgVehicles classname; type takes "
            "a classname such as CivilianSedan (a mod's classnames exist only while "
            "that mod is loaded)",
        )
        call.assert_awaited_once()
        self.assertEqual(call.await_args.args[0], "world_spawn")
        self.assertEqual(call.await_args.args[1]["type"], "vehicle")

    async def test_an_empty_type_is_named_too(self) -> None:
        message, _call = await self._spawn_error("", "unknown_type")
        self.assertTrue(message.startswith("unknown_type: type '' is not"), message)

    async def test_a_long_type_is_cut_in_the_echo(self) -> None:
        spawn_type = "Land_" + "x" * 95
        message, _call = await self._spawn_error(spawn_type, "unknown_type")

        self.assertIn(repr(spawn_type[:64]), message)
        self.assertIn("(first 64 of 100 characters)", message)
        self.assertNotIn(spawn_type, message)

    async def test_other_bridge_refusals_keep_their_bare_code(self) -> None:
        for code in ("spawn_failed", "bad_pos", "bad_flags", "timeout"):
            with self.subTest(code=code):
                message, _call = await self._spawn_error("CivilianSedan", code)
                self.assertEqual(message, code)


class ClaimableIsNotAFreeLeaseTest(unittest.TestCase):
    """The two cases the session_status sentence names (_claimable_locked)."""

    def test_claimable_stays_true_while_a_session_holds_the_lease(self) -> None:
        coordinator = _coord(FakeClock(), attached=False)
        status, granted = coordinator.acquire(_identity("a"), "drive")
        self.assertEqual((status, granted["status"]), (200, "active"))

        snapshot = coordinator.status(_identity("b"))

        self.assertIsInstance(snapshot["owner"], dict)
        self.assertIs(snapshot["claimable"], True)
        self.assertEqual(coordinator.acquire(_identity("b"), "next")[0], 202)

    def test_claimable_stays_true_while_grace_reserves_a_lapsed_lease(self) -> None:
        # 00c4: owner null, empty queue, claimable true, and still no grant.
        clock = FakeClock()
        coordinator = _coord(clock, attached=True)
        coordinator.acquire(_identity("a"), "drive")
        clock.advance(MID)

        snapshot = coordinator.status(_identity("b"))

        self.assertIsNone(snapshot["owner"])
        self.assertEqual(snapshot["queue"], [])
        self.assertIsInstance(snapshot["grace"], dict)
        self.assertIs(snapshot["claimable"], True)
        status, queued = coordinator.acquire(_identity("b"), "next")
        self.assertEqual((status, queued["status"]), (202, "queued"))


if __name__ == "__main__":
    unittest.main()
