"""Bridge batch t2_bridge_e: object_resolve, telemetry lifetime, give id, direction.

Items 9d8c, 19c3, 4d80 and c32c. Enforce is checked as source. Nothing here
talks to a live DayZ process.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, result_prune, server
from dayz_mcp.bridge_readiness import EXPECTED_SERVER_ARG_CONTRACT_HASH
from dayz_mcp.core import EXPECTED_BRIDGE_VERSION
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease
from tests._addon_paths import addon_root
from tests.fence_helpers import INST_SERVER, PID_SERVER, bind_both_peers, bound_queue


BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
CLIENT = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
MESSAGES = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"


def _method_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated method: {signature}")


class ObjectResolveContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = BRIDGE.read_text(encoding="utf-8")
        self.body = _method_body(self.bridge, "protected bool DispatchObjectResolve(")
        self.state = loopback.ServerState("test-key")
        bind_both_peers(self.state)
        _announce_server(self.state, _server_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)

    def test_handler_reuses_the_finder_and_an_existing_id(self) -> None:
        self.assertIn("object_resolve", loopback.SERVER_COMMANDS)
        self.assertEqual(loopback.peer_for_command("object_resolve"), "server")
        self.assertNotIn("object_resolve", READ_ONLY_COMMANDS)
        self.assertTrue(command_requires_lease("object_resolve"))
        self.assertIn('"object_resolve"', self.bridge)
        self.assertIn("postNow = DispatchObjectResolve(command, result);", self.bridge)
        self.assertEqual(server._BRIDGE_COMMAND_TOOLS["server"]["object_resolve"], "object_resolve")
        for token in (
            "FindUniqueObjectNearType(command.args.type",
            "RuntimeObjectId(match)",
            "m_RuntimeObjects.Insert(command.id, match)",
            "result.object_id = objectId",
            "result.pos_real",
            'result.error = "bad_args"',
            "OBJECT_RESOLVE_RADIUS_MAX",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.body)
        self.assertLess(self.body.index("RuntimeObjectId(match)"), self.body.index("m_RuntimeObjects.Insert"))
        self.assertLess(self.body.index("if (existingId <= 0)"), self.body.index("m_RuntimeObjects.Insert"))
        # The shared 25 m lookup is not the resolve radius, and the finder
        # still takes the radius its caller passes.
        finder = _method_body(self.bridge, "protected Object FindUniqueObjectNearType(")
        self.assertNotIn("OBJECT_RESOLVE_RADIUS_MAX", finder)
        self.assertNotIn("OBJECT_LOOKUP_RADIUS", self.body)
        self.assertIn("OBJECT_LOOKUP_RADIUS", self.bridge)
        self.assertIn("object_resolve", loopback._CAPABILITY_ADMISSION_COMMANDS)

    def test_closed_schema_accepts_one_shape_and_keeps_bridge_errors(self) -> None:
        ok_status, ok_body = self.state.enqueue_command(
            "object_resolve",
            {"type": "CivilianSedan", "pos": [1.0, 2.0, 3.0], "radius": 50.0},
        )
        self.assertEqual(ok_status, 200)
        self.assertEqual(ok_body["peer"], "server")
        small_status, _small = self.state.enqueue_command(
            "object_resolve",
            {"type": "CivilianSedan", "pos": [0.0, 0.0, 0.0], "radius": 0.5},
        )
        self.assertEqual(small_status, 200)
        rejected = [
            {},
            {"type": "CivilianSedan", "pos": [0.0, 0.0, 0.0]},
            {"type": "", "pos": [0.0, 0.0, 0.0], "radius": 1.0},
            {"type": "CivilianSedan", "pos": [0.0, 0.0, 0.0], "radius": 0.0},
            {"type": "CivilianSedan", "pos": [0.0, 0.0, 0.0], "radius": 50.1},
            {"type": "CivilianSedan", "pos": [0.0, 0.0, 0.0], "radius": -1.0},
            {"type": "CivilianSedan", "pos": [0.0, 0.0], "radius": 1.0},
            {
                "type": "CivilianSedan",
                "pos": [0.0, 0.0, 0.0],
                "radius": 1.0,
                "nearest": True,
            },
        ]
        for args in rejected:
            with self.subTest(args=args):
                status, body = self.state.enqueue_command("object_resolve", args)  # type: ignore[arg-type]
                self.assertEqual(status, 400)
                self.assertEqual(body, {"error": "bad_args"})


# Pre-object_resolve server contract. An older PBO still advertises this hash.
_BASELINE_ARG_CONTRACT_HASH = "e5a0ed288dbae72f"
_RESOLVE_ARGS = {"type": "CivilianSedan", "pos": [0.0, 0.0, 0.0], "radius": 2.0}


def _server_commands() -> list[str]:
    return sorted(server._BRIDGE_COMMAND_TOOLS["server"])


def _announce_server(
    state: loopback.ServerState, commands: list[str], ach: str | None
) -> None:
    state.daemon_generation = "gen-resolve"
    state._peer_caps["server"] = {
        "generation": state.daemon_generation,
        "commands": list(commands),
        "reason": "ok",
        "arg_contract_hash": ach,
        "announced_at": state._now(),
        "instance": INST_SERVER,
    }


class ObjectResolveAdmissionTest(unittest.TestCase):
    """Enqueue and delivery refuse object_resolve the same way as player_kill.

    These fail on a tree that leaves the verb out of _CAPABILITY_ADMISSION_COMMANDS:
    enqueue then returns 200 and allocates an id.
    """

    def _bound(self) -> loopback.ServerState:
        state = loopback.ServerState(key="k")
        state.install_bound_peer(
            instance=INST_SERVER, role="server", pid=PID_SERVER, run_id="resolve-run"
        )
        return state

    def test_missing_capability_refuses_before_an_id(self) -> None:
        state = self._bound()
        reduced = [name for name in _server_commands() if name != "object_resolve"]
        _announce_server(state, reduced, EXPECTED_SERVER_ARG_CONTRACT_HASH)
        before = state._next_id
        status, body = state.enqueue_command("object_resolve", _RESOLVE_ARGS, peer="server")
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "bridge_capability_missing")
        self.assertEqual(state._next_id, before)
        self.assertEqual(bound_queue(state, "server"), [])

    def test_old_contract_hash_refuses_before_an_id(self) -> None:
        state = self._bound()
        _announce_server(state, _server_commands(), _BASELINE_ARG_CONTRACT_HASH)
        before = state._next_id
        status, body = state.enqueue_command("object_resolve", _RESOLVE_ARGS, peer="server")
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "arg_contract_mismatch")
        self.assertEqual(state._next_id, before)
        self.assertEqual(bound_queue(state, "server"), [])

    def test_capability_lost_before_delivery_is_discarded(self) -> None:
        state = self._bound()
        _announce_server(state, _server_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        status, body = state.enqueue_command("object_resolve", _RESOLVE_ARGS, peer="server")
        self.assertEqual(status, 200, body)
        reduced = [name for name in _server_commands() if name != "object_resolve"]
        status, polled = state.record_poll(
            "server",
            f"{EXPECTED_BRIDGE_VERSION}~1.29.0",
            instance=INST_SERVER,
            source_pid=PID_SERVER,
            caps=",".join(reduced),
            ach=EXPECTED_SERVER_ARG_CONTRACT_HASH,
        )
        self.assertEqual(status, 200)
        self.assertEqual(polled["commands"], [])
        stored = state.take_result(body["id"])
        self.assertEqual(stored["error"], "bridge_capability_missing")
        self.assertEqual(bound_queue(state, "server"), [])


class ObjectResolveToolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def test_forwards_and_preserves_bridge_errors(self) -> None:
        names = {tool.name for tool in await self.app.list_tools()}
        self.assertIn("object_resolve", names)
        with patch.object(
            self.runtime,
            "call_bridge",
            new=AsyncMock(
                return_value={
                    "ok": False,
                    "error": "ambiguous_object",
                    "object_id": 0,
                }
            ),
        ) as call:
            result = await self.app.call_tool(
                "object_resolve",
                {
                    "type": "CivilianSedan",
                    "pos": [1.0, 2.0, 3.0],
                    "radius": 4.0,
                    "timeout_s": 1.0,
                },
            )
        call.assert_awaited_once_with(
            "object_resolve",
            {"type": "CivilianSedan", "pos": [1.0, 2.0, 3.0], "radius": 4.0},
            "server",
            1.0,
        )
        text = str(result)
        self.assertIn("ambiguous_object", text)
        self.assertNotIn("object_id_unavailable", text)

    async def test_tool_rejects_a_radius_outside_the_window(self) -> None:
        with patch.object(self.runtime, "call_bridge", new=AsyncMock()) as call:
            with self.assertRaises(Exception) as denied:
                await self.app.call_tool(
                    "object_resolve",
                    {"type": "CivilianSedan", "pos": [0.0, 0.0, 0.0], "radius": 0},
                )
        self.assertIn("bad_args", str(denied.exception))
        call.assert_not_awaited()


class TelemetryLifetimeContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = BRIDGE.read_text(encoding="utf-8")
        self.body = _method_body(self.bridge, "protected bool DispatchTelemetryObjectAt(")

    def test_lifetime_is_filled_after_the_unique_match_only(self) -> None:
        self.assertLess(self.body.index("PopulateTelemetryObject(match, telemetry);"), self.body.index("EntityAI.Cast(match)"))
        filled = self.body[self.body.index("if (lifetimeEntity)") :]
        self.assertIn("result.lifetime = new MCPSpawnLifetime();", filled)
        self.assertIn("result.lifetime.remaining_s = lifetimeEntity.GetLifetime();", filled)
        self.assertIn("result.lifetime.max_s = lifetimeEntity.GetLifetimeMax();", filled)
        before = self.body[: self.body.index("if (matchCount == 0)")]
        self.assertNotIn("result.lifetime", before)
        ambiguous = self.body[self.body.index("if (matchCount > 1)") : self.body.index("PopulateTelemetryObject")]
        self.assertNotIn("result.lifetime", ambiguous)
        # Spawn and fixture paths are not this handler.
        self.assertNotIn("lifetime_s_set", self.body)
        populate = _method_body(self.bridge, "protected void PopulateTelemetryObject(")
        self.assertNotIn("result.lifetime", populate)
        self.assertNotIn("GetLifetime()", populate)

    def test_prune_keeps_zero_lifetime_and_drops_an_empty_one(self) -> None:
        zeros = {"ok": 1, "lifetime": {"remaining_s": 0, "max_s": 0}}
        self.assertEqual(result_prune.prune_unfilled_fields("telemetry_read", zeros), zeros)
        self.assertEqual(
            result_prune.prune_unfilled_fields("telemetry_read", {"ok": 1, "lifetime": {}}),
            {"ok": 1},
        )
        self.assertEqual(
            result_prune.prune_unfilled_fields("telemetry_read", {"ok": 1, "lifetime": None}),
            {"ok": 1},
        )


class TelemetryLifetimeForwardTest(unittest.IsolatedAsyncioTestCase):
    async def test_object_at_result_is_forwarded_with_lifetime(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        payload = {
            "ok": 1,
            "telemetry": {"found": True, "type": "CivilianSedan"},
            "lifetime": {"remaining_s": 0, "max_s": 0},
        }
        with patch.object(runtime, "call_bridge", new=AsyncMock(return_value=payload)) as call:
            result = await app.call_tool(
                "telemetry_read",
                {
                    "mode": "object_at",
                    "type": "CivilianSedan",
                    "pos": [1.0, 2.0, 3.0],
                    "radius": 5.0,
                    "timeout_s": 1.0,
                },
            )
        call.assert_awaited_once()
        self.assertEqual(call.await_args.args[0], "telemetry_read")
        text = str(result)
        self.assertIn("remaining_s", text)
        self.assertIn("max_s", text)


class InventoryGiveIdContractTest(unittest.TestCase):
    def test_success_registers_once_for_both_destinations(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        body = _method_body(source, "protected bool DispatchInventoryGive(")
        self.assertEqual(body.count("m_RuntimeObjects.Insert(command.id, spawned)"), 1)
        self.assertEqual(body.count("result.object_id = command.id"), 1)
        self.assertLess(body.index("if (!spawned)"), body.index("m_RuntimeObjects.Insert"))
        hands = _method_body(body, 'if (command.args.dest == "hands")')
        self.assertNotIn("m_RuntimeObjects.Insert", hands)
        self.assertIn('result.error = "hands_occupied"', hands)
        self.assertIn('result.error = "create_failed"', body)
        failed = body[body.index("if (!spawned)") : body.index("m_RuntimeObjects.Insert")]
        self.assertIn("return true;", failed)


class InventoryGiveIdToolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def _give(self, dest: str, payload: dict) -> str:
        with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value=payload)) as call:
            result = await self.app.call_tool(
                "inventory_give",
                {"classname": "Rag", "dest": dest, "timeout_s": 1.0},
            )
        call.assert_awaited_once_with(
            "inventory_give", {"classname": "Rag", "dest": dest}, "server", 1.0
        )
        return str(result)

    async def test_both_destinations_forward_a_positive_id(self) -> None:
        for dest in ("hands", "inventory"):
            with self.subTest(dest=dest):
                text = await self._give(dest, {"ok": 1, "object_id": 9, "classname": "Rag"})
                self.assertIn("object_id", text)
                self.assertNotIn("object_id_unavailable", text)

    async def test_errors_are_not_marked_unavailable(self) -> None:
        text = await self._give("hands", {"ok": False, "error": "hands_occupied"})
        self.assertIn("hands_occupied", text)
        self.assertNotIn("object_id_unavailable", text)
        text = await self._give("inventory", {"ok": False, "error": "create_failed"})
        self.assertIn("create_failed", text)
        self.assertNotIn("object_id_unavailable", text)

    async def test_old_success_without_an_id_keeps_the_receipt(self) -> None:
        text = await self._give("inventory", {"ok": 1, "classname": "Rag", "found": True})
        self.assertIn("Rag", text)
        self.assertIn("object_id_unavailable", text)
        self.assertIn("Do not retry", text)

    async def test_descriptions_name_the_give_id(self) -> None:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        self.assertIn("object_id", tools["inventory_give"].description)
        self.assertIn("inventory_give.object_id", tools["hands_take"].description)
        self.assertNotIn("does not return one", tools["hands_take"].description)


class VehicleDirectionContractTest(unittest.TestCase):
    def test_direction_is_written_from_the_transport_and_pruned_when_empty(self) -> None:
        messages = MESSAGES.read_text(encoding="utf-8")
        result = messages[messages.index("class MCPResult") :]
        self.assertIn("ref array<float> direction;", result)
        self.assertIn("direction", result_prune.PRUNABLE_FIELDS)
        client = CLIENT.read_text(encoding="utf-8")
        body = _method_body(client, "protected bool DispatchVehicleTelemetry(")
        self.assertLess(body.index("if (!transport)"), body.index("transport.GetDirection()"))
        self.assertLess(body.index("result.classname = transport.ClassName();"), body.index("transport.GetDirection()"))
        self.assertLess(body.index("transport.GetDirection()"), body.index("CarScript.Cast(transport)"))
        self.assertIn("VectorToArray(transport.GetDirection(), result.direction);", body)
        absent = body[: body.index("result.found = true;")]
        self.assertNotIn("result.direction", absent)
        filled = {"ok": 1, "direction": [0.0, 0.0, 1.0]}
        self.assertEqual(result_prune.prune_unfilled_fields("vehicle_telemetry", filled), filled)
        self.assertEqual(
            result_prune.prune_unfilled_fields("vehicle_telemetry", {"ok": 1, "direction": []}),
            {"ok": 1},
        )
        self.assertEqual(
            result_prune.prune_unfilled_fields("vehicle_telemetry", {"ok": 1, "direction": None}),
            {"ok": 1},
        )

    def test_description_calls_direction_a_vector(self) -> None:
        # The description lives on the tool. Checked with the app below.
        self.assertIn("GetDirection", CLIENT.read_text(encoding="utf-8"))


class VehicleDirectionToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_description_and_forwarding(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        tool = next(item for item in await app.list_tools() if item.name == "vehicle_telemetry")
        self.assertIn("[x, y, z]", tool.description)
        self.assertIn("GetDirection", tool.description)
        payload = {"ok": 1, "direction": [1.0, 0.0, 0.0], "found": True}
        with patch.object(runtime, "call_bridge", new=AsyncMock(return_value=payload)) as call:
            result = await app.call_tool("vehicle_telemetry", {"timeout_s": 1.0})
        call.assert_awaited_once_with("vehicle_telemetry", {}, "client", 1.0)
        self.assertIn("direction", str(result))
        self.assertTrue(command_requires_lease("vehicle_telemetry") is False)
