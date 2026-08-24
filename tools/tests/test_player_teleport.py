from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from tests._addon_paths import addon_root
from dayz_mcp import loopback, server
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease


COMMAND = "player_teleport"
VALID_ARGS = {"pos": [7500.0, 0.0, 7500.0]}
BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"


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


class PlayerTeleportIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        from tests.fence_helpers import bind_both_peers

        bind_both_peers(self.state)

    def test_happy_path_server_mutating(self) -> None:
        self.assertIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "server")
        self.assertNotIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertTrue(command_requires_lease(COMMAND))

        status, body = self.state.enqueue_command(COMMAND, dict(VALID_ARGS))
        self.assertEqual(status, 200)
        self.assertEqual(body["peer"], "server")

    def test_rejects_bad_args(self) -> None:
        invalid = [
            {},
            {"pos": [1.0, 2.0]},
            {"pos": [1.0, 2.0, 3.0, 4.0]},
            {"pos": [float("nan"), 0.0, 0.0]},
            {"pos": [0.0, 0.0, 0.0], "extra": 1},
        ]
        for args in invalid:
            with self.subTest(args=args):
                status, body = self.state.enqueue_command(COMMAND, args)  # type: ignore[arg-type]
                self.assertEqual(status, 400)
                self.assertEqual(body, {"error": "bad_args"})


class PlayerTeleportEnforceContractTest(unittest.TestCase):
    def test_handler_snaps_y_zero_and_errors_without_player(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        body = _method_body(source, "protected bool DispatchPlayerTeleport(")
        required = [
            "ValidatePositionArgs(command.args)",
            "ResolvePlayer(command.args, resolveError)",
            "result.error = resolveError",
            "position[1] == 0",
            "SurfaceY(position[0], position[2])",
            "GetCommand_Vehicle()",
            "GetTransport()",
            "SetTransform(mat)",
            "player.SetPosition(position)",
            "applied = veh.GetPosition()",
            "applied = player.GetPosition()",
            "result.pos_real = new array<float>()",
        ]
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, body)
        self.assertNotIn("PluginDeveloper", body)

    def test_vehicle_branch_reports_the_transport_position(self) -> None:
        # Gate 2026-08-17: with the occupant seated, pos_real came back as the PREVIOUS position
        # because player.GetPosition() lags SetTransform by a sim frame. The transport is what
        # moved, so the vehicle branch must read it back; the single stale read after both
        # branches must be gone.
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        body = _method_body(source, "protected bool DispatchPlayerTeleport(")
        self.assertNotIn("vector applied = player.GetPosition()", body)
        set_transform = body.index("veh.SetTransform(mat)")
        transport_read = body.index("applied = veh.GetPosition()")
        occupant_read = body.index("applied = player.GetPosition()")
        publish = body.index("VectorToArray(applied, result.pos_real)")
        self.assertLess(set_transform, transport_read)
        self.assertLess(transport_read, occupant_read)
        self.assertLess(occupant_read, publish)
        self.assertEqual(body.count("veh.SetTransform(mat)"), 1)


class PlayerTeleportAppToolTest(unittest.IsolatedAsyncioTestCase):
    async def _build(self):
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name for tool in await app.list_tools()}
        self.assertIn(COMMAND, tools)
        return app, runtime

    async def test_skip_clearance_forwards_directly(self) -> None:
        app, runtime = await self._build()
        with patch.object(
            runtime,
            "call_bridge",
            new=AsyncMock(return_value={"ok": 1, "pos_real": [7500.0, 10.0, 7500.0]}),
        ) as call:
            await app.call_tool(
                COMMAND,
                {
                    "pos": [7500.0, 0.0, 7500.0],
                    "skip_clearance_check": True,
                    "timeout_s": 1.0,
                },
            )
        call.assert_awaited_once_with(
            COMMAND, {"pos": [7500.0, 0.0, 7500.0]}, "server", 1.0
        )

    async def test_clear_column_probes_then_teleports(self) -> None:
        app, runtime = await self._build()
        responses = [
            {"ok": 1, "y": 10.0, "type": "cp_concrete2"},
            {"ok": 1, "raycast": {"hit": True, "pos": [7500.0, 10.02, 7500.0]}},
            {"ok": 1, "pos_real": [7500.0, 10.0, 7500.0]},
        ]
        with patch.object(
            runtime, "call_bridge", new=AsyncMock(side_effect=responses)
        ) as call:
            await app.call_tool(
                COMMAND, {"pos": [7500.0, 0.0, 7500.0], "timeout_s": 1.0}
            )
        self.assertEqual(call.await_count, 3)
        first, second, third = call.await_args_list
        self.assertEqual(first.args[0], "surface_query")
        self.assertEqual(second.args[0], "scene_raycast")
        self.assertEqual(second.args[1]["from"], [7500.0, 40.0, 7500.0])
        self.assertEqual(second.args[1]["to"], [7500.0, 5.0, 7500.0])
        self.assertEqual(second.args[1]["intersect"], "geom")
        self.assertEqual(second.args[1]["ignore"], "player")
        self.assertEqual(third.args[0], COMMAND)

    async def test_covered_column_refuses_without_teleporting(self) -> None:
        app, runtime = await self._build()
        responses = [
            {"ok": 1, "y": 10.0},
            {
                "ok": 1,
                "raycast": {
                    "hit": True,
                    "pos": [7500.0, 25.0, 7500.0],
                    "object_type": "Land_Airport_Hangar",
                },
            },
        ]
        with patch.object(
            runtime, "call_bridge", new=AsyncMock(side_effect=responses)
        ) as call:
            await app.call_tool(
                COMMAND, {"pos": [7500.0, 0.0, 7500.0], "timeout_s": 1.0}
            )
        awaited = [item.args[0] for item in call.await_args_list]
        self.assertEqual(awaited, ["surface_query", "scene_raycast"])

    async def test_no_ground_hit_refuses_without_teleporting(self) -> None:
        app, runtime = await self._build()
        responses = [
            {"ok": 1, "y": 10.0},
            {"ok": 1, "raycast": {"hit": False, "pos": []}},
        ]
        with patch.object(
            runtime, "call_bridge", new=AsyncMock(side_effect=responses)
        ) as call:
            await app.call_tool(
                COMMAND, {"pos": [7500.0, 0.0, 7500.0], "timeout_s": 1.0}
            )
        awaited = [item.args[0] for item in call.await_args_list]
        self.assertEqual(awaited, ["surface_query", "scene_raycast"])

    async def test_explicit_above_surface_target_skips_the_column_probe(self) -> None:
        app, runtime = await self._build()
        responses = [
            {"ok": 1, "y": 10.0},
            {"ok": 1, "pos_real": [7500.0, 50.0, 7500.0]},
        ]
        with patch.object(
            runtime, "call_bridge", new=AsyncMock(side_effect=responses)
        ) as call:
            await app.call_tool(
                COMMAND, {"pos": [7500.0, 50.0, 7500.0], "timeout_s": 1.0}
            )
        awaited = [item.args[0] for item in call.await_args_list]
        self.assertEqual(awaited, ["surface_query", COMMAND])


if __name__ == "__main__":
    unittest.main()
