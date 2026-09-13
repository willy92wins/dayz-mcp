from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from tests._addon_paths import addon_root
from dayz_mcp import loopback, server
from dayz_mcp.session_coordination import command_requires_lease


COMMAND = "vehicle_prepare_fixture"
VALID_ARGS = {
    "mode": "object_at",
    "type": "CivilianSedan",
    "pos": [7500.0, 0.0, 7500.0],
    "radius": 100.0,
}
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


class VehiclePrepareFixtureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        from tests.fence_helpers import bind_both_peers

        bind_both_peers(self.state)

    def test_command_is_whitelisted_and_routes_only_to_server(self) -> None:
        self.assertIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertIn(COMMAND, loopback.WHITELISTED_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "server")
        self.assertTrue(command_requires_lease(COMMAND))

        status, body = self.state.enqueue_command(COMMAND, dict(VALID_ARGS))
        self.assertEqual(status, 200)
        self.assertEqual(body["peer"], "server")

        # Any non-empty classname is accepted at ingress.
        status, body = self.state.enqueue_command(
            COMMAND, {**VALID_ARGS, "type": "OffroadHatchback"}
        )
        self.assertEqual(status, 200)

        status, body = self.state.enqueue_command(COMMAND, dict(VALID_ARGS), peer="client")
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "bad_peer"})

    def test_ingress_rejects_non_dict_near_matches_and_non_finite_numbers(self) -> None:
        invalid_args = [
            [],
            {},
            {**VALID_ARGS, "mode": "OBJECT_AT"},
            {**VALID_ARGS, "type": ""},
            {**VALID_ARGS, "type": 12},
            {**VALID_ARGS, "pos": [7500.0, 0.0]},
            {**VALID_ARGS, "pos": [7500.0, 0.0, 7500.0, 0.0]},
            {**VALID_ARGS, "pos": [True, 0.0, 7500.0]},
            {**VALID_ARGS, "pos": [float("nan"), 0.0, 7500.0]},
            {**VALID_ARGS, "pos": [float("inf"), 0.0, 7500.0]},
            {**VALID_ARGS, "radius": True},
            {**VALID_ARGS, "radius": 0.0},
            {**VALID_ARGS, "radius": -1.0},
            {**VALID_ARGS, "radius": float("nan")},
            {**VALID_ARGS, "radius": float("inf")},
            {**VALID_ARGS, "radius": "100"},
        ]

        for args in invalid_args:
            with self.subTest(args=args):
                status, body = self.state.enqueue_command(COMMAND, args)  # type: ignore[arg-type]
                self.assertEqual(status, 400)
                self.assertEqual(body, {"error": "bad_args"})

    def test_enforce_handler_has_exact_stationary_fixture_contract(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        self.assertIn('else if (command.cmd == "vehicle_prepare_fixture")', source)
        self.assertIn("DispatchVehiclePrepareFixture(command, result)", source)

        body = _method_body(source, "protected bool DispatchVehiclePrepareFixture(")
        required = [
            'command.args.mode != "object_at"',
            'command.args.type == ""',
            "command.args.pos.Count() != 3",
            "IsFiniteFloat(command.args.radius)",
            "GetGame().GetObjectsAtPosition3D(",
            "found.GetType() == validation.type",
            'result.error = "fixture_not_found"',
            'result.error = "ambiguous_fixture"',
            "CarScript.Cast(match)",
            'result.error = "fixture_not_vehicle"',
            "if (!IsVehicleFixtureReady(car))",
            "car.OnDebugSpawn();",
            "PopulateTelemetryObject(car, telemetry);",
            "result.telemetry = telemetry;",
            "result.vehicle_fixture_ready = IsVehicleFixtureReady(car);",
        ]
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, body)

        # Classname allowlist must stay gone.
        self.assertNotIn("ExampleCar", body)
        self.assertEqual(body.count("car.OnDebugSpawn();"), 1)
        forbidden = [
            "MCPJob",
            "StartCommand_Vehicle",
            "EngineStart",
            "SetThrottle",
            "Shift",
            "SetPosition",
            "GetFirstHuman",
            "Human.Cast",
        ]
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, body)


class VehiclePrepareFixtureAppToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_app_tool_is_registered_and_forwards_server_command(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name for tool in await app.list_tools()}
        self.assertIn(COMMAND, tools)

        with patch.object(
            runtime,
            "call_bridge",
            new=AsyncMock(return_value={"ok": 1, "vehicle_fixture_ready": True}),
        ) as call:
            await app.call_tool(
                COMMAND,
                {
                    "type": "CivilianSedan",
                    "pos": [7500.0, 0.0, 7500.0],
                    "radius": 50.0,
                    "timeout_s": 1.0,
                },
            )

        call.assert_awaited_once_with(
            COMMAND,
            {
                "mode": "object_at",
                "type": "CivilianSedan",
                "pos": [7500.0, 0.0, 7500.0],
                "radius": 50.0,
            },
            "server",
            1.0,
        )


NOT_READY = {
    "ok": 0,
    "error": "fixture_not_ready",
    "vehicle_fixture_ready": False,
    "telemetry": {
        "wheel_count": 2,
        "fuel_fraction": 1.0,
        "attachment_count": 8,
        "attachment_items": ["CarRadiator", "SparkPlug"],
        "future_key": "do-not-leak",
    },
    "handler": "SomeHandler",
    "user_id": 99,
    "clicked": True,
}


class FixtureNotReadyDiagnosticsTest(unittest.TestCase):
    """fb-20260911-230927-b1ff: fixture_not_ready keeps observed telemetry."""

    def test_observed_scalars_ride_after_the_code(self) -> None:
        message = str(server._bridge_error(dict(NOT_READY), "vehicle_prepare_fixture"))
        self.assertEqual(
            message,
            "fixture_not_ready; wheel_count=2 fuel_fraction=1.0 "
            "attachment_count=8 vehicle_fixture_ready=False",
        )

    def test_allowlist_drops_item_lists_and_unknown_keys(self) -> None:
        message = str(server._bridge_error(dict(NOT_READY), "vehicle_prepare_fixture"))
        self.assertNotIn("attachment_items", message)
        self.assertNotIn("CarRadiator", message)
        self.assertNotIn("future_key", message)
        self.assertNotIn("do-not-leak", message)
        self.assertNotIn("expected", message)

    def test_other_fixture_errors_stay_bare_even_with_telemetry(self) -> None:
        payload = {
            **NOT_READY,
            "error": "fixture_not_found",
        }
        self.assertEqual(
            str(server._bridge_error(payload, "vehicle_prepare_fixture")),
            "fixture_not_found",
        )

    def test_missing_telemetry_stays_bare(self) -> None:
        self.assertEqual(
            str(
                server._bridge_error(
                    {"ok": 0, "error": "fixture_not_ready"},
                    "vehicle_prepare_fixture",
                )
            ),
            "fixture_not_ready",
        )

    def test_verb_gate_does_not_leak_into_world_spawn(self) -> None:
        # Stronger than plan N1: even fixture_not_ready on world_spawn stays
        # the bare code. N1 (timeout + same telemetry payload) is the next test.
        self.assertEqual(
            str(server._bridge_error(dict(NOT_READY), "world_spawn")),
            "fixture_not_ready",
        )

    def test_world_spawn_timeout_with_fixture_payload_stays_bare(self) -> None:
        payload = {**NOT_READY, "error": "timeout"}
        self.assertEqual(
            str(server._bridge_error(payload, "world_spawn")),
            "timeout",
        )

    def test_json_string_scalars_match_unquoted_exact_form(self) -> None:
        payload = {
            "ok": 0,
            "error": "fixture_not_ready",
            "vehicle_fixture_ready": False,
            "telemetry": {
                "wheel_count": "2",
                "fuel_fraction": "1.0",
                "attachment_count": "8",
            },
        }
        message = str(server._bridge_error(payload, "vehicle_prepare_fixture"))
        self.assertEqual(
            message,
            "fixture_not_ready; wheel_count=2 fuel_fraction=1.0 "
            "attachment_count=8 vehicle_fixture_ready=False",
        )
        self.assertNotIn("'2'", message)
        self.assertNotIn("'1.0'", message)

    def test_non_numeric_string_scalars_are_omitted(self) -> None:
        payload = {
            "ok": 0,
            "error": "fixture_not_ready",
            "vehicle_fixture_ready": False,
            "telemetry": {"wheel_count": "two", "fuel_fraction": 1.0},
        }
        message = str(server._bridge_error(payload, "vehicle_prepare_fixture"))
        self.assertEqual(
            message,
            "fixture_not_ready; fuel_fraction=1.0 vehicle_fixture_ready=False",
        )
        self.assertNotIn("two", message)
        self.assertNotIn("'2'", message)


class FixtureNotReadyWireTest(unittest.IsolatedAsyncioTestCase):
    async def test_client_sees_wheel_count(self) -> None:
        from tests.test_ui_error_diagnostics import _wire_error_text

        text = await _wire_error_text(
            "vehicle_prepare_fixture",
            {"type": "Arma2Quad_Test_AWD", "pos": [13157.8, 5.7975, 6876.2134]},
            NOT_READY,
        )
        self.assertEqual(
            text,
            "Error executing tool vehicle_prepare_fixture: fixture_not_ready; "
            "wheel_count=2 fuel_fraction=1.0 attachment_count=8 "
            "vehicle_fixture_ready=False",
        )

    async def test_world_spawn_timeout_with_fixture_payload_stays_bare(self) -> None:
        from tests.test_ui_error_diagnostics import _wire_error_text

        text = await _wire_error_text(
            "world_spawn",
            {"type": "SurvivorM_Mirek", "pos": [7500.0, 0.0, 7500.0]},
            {**NOT_READY, "error": "timeout"},
        )
        self.assertEqual(text, "Error executing tool world_spawn: timeout")


if __name__ == "__main__":
    unittest.main()
