"""fb-20261026-133108-035a: read vehicle lights on the server and the client.

telemetry_read(object_at) fills MCPTelemetry.lights_on_server from
Transport.LightIsOn on a Car (transport.c:229-230). vehicle_telemetry fills
MCPResult.lights_on_client the same way on a CarScript replica. An optional
pos finds the nearest Transport within 4 m even when the player is on foot;
seated is whether the local player sits in that Transport, and seat is empty
when they do not.

No Enforce runtime: tests.enforce_subset_helpers translates
PopulateTelemetryObject and DispatchVehicleTelemetry from the source and runs
them against fakes of the vanilla calls they make.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import result_prune, server
from dayz_mcp.server import ServerConfig, build_app
from tests._addon_paths import addon_root
from tests.client_helpers import _fixture_client_runtime
from tests.enforce_subset_helpers import EnforceString, method_body, translate

SCRIPTS = addon_root() / "scripts" / "5_Mission"
CLIENT_BRIDGE = SCRIPTS / "MCPClientBridge.c"
SERVER_BRIDGE = SCRIPTS / "MCPBridge.c"
MESSAGES = SCRIPTS / "MCPMessages.c"

POPULATE = "protected void PopulateTelemetryObject(Object found, MCPTelemetry telemetry)"
TELEMETRY = "protected bool DispatchVehicleTelemetry(MCPCommand command, MCPResult result)"
LIVE = "protected Transport ResolveLiveSeatedTransport(PlayerBase player)"
FINITE = "protected bool IsFiniteFloat(float value)"
SEAT_TOKEN = "protected string VehicleTelemetrySeatToken(int vehicleSeat)"

SEATS = SimpleNamespace(
    VEHICLESEAT_DRIVER=101,
    VEHICLESEAT_CODRIVER=102,
    VEHICLESEAT_PASSENGER_L=103,
    VEHICLESEAT_PASSENGER_R=104,
)


class FakeArray:
    def __init__(self, values: list[float] | None = None) -> None:
        self.values = list(values or [])

    def Count(self) -> int:
        return len(self.values)

    def Get(self, index: int) -> float:
        return self.values[index]


class FakeTransport:
    """A Transport; carscript=False stands for a boat, not a CarScript."""

    def __init__(
        self,
        *,
        lights: bool = False,
        carscript: bool = True,
        type_name: str = "CivilianSedan",
        classname: str = "CivilianSedan",
    ) -> None:
        self.lights = lights
        self.carscript = carscript
        self.type_name = type_name
        self.classname = classname
        self.light_calls = 0
        self.owner = True
        self.authority_owner = False
        self.identity = None
        self.gear = 0
        self.speedo = 0.0
        self.engine_on = False
        self.net_strategy = 2
        self.position = (0.0, 0.0, 0.0)
        self.direction = (0.0, 0.0, 1.0)

    def GetType(self) -> str:
        return self.type_name

    def ClassName(self) -> str:
        return self.classname

    def GetDirection(self):
        return self.direction

    def GetPosition(self):
        return self.position

    def GetSpeedometer(self) -> float:
        return self.speedo

    def GetGear(self) -> int:
        return self.gear

    def EngineIsOn(self) -> bool:
        return self.engine_on

    def LightIsOn(self) -> bool:
        self.light_calls += 1
        return self.lights

    def GetNetworkMoveStrategy(self) -> int:
        return self.net_strategy

    def IsOwner(self) -> bool:
        return self.owner

    def IsAuthorityOwner(self) -> bool:
        return self.authority_owner

    def GetOwnerIdentity(self):
        return self.identity

    def GetNetworkID(self, low: int, high: int) -> None:
        return None

    def GetParent(self):
        return None

    def CrewMemberIndex(self, player: object) -> int:
        return getattr(player, "crew_index", -1)

    def GetOrientation(self):
        return (0.0, 0.0, 0.0)

    def GetHealth01(self, _zone: object, _hit: object) -> float:
        return 1.0

    def WheelCountPresent(self) -> int:
        return 4

    def GetFluidFraction(self, _fluid: object) -> float:
        return 1.0


class FakeVehicleCommand:
    def __init__(self, transport: FakeTransport, seat: int) -> None:
        self.transport = transport
        self.seat = seat

    def GetTransport(self) -> FakeTransport:
        return self.transport

    def GetVehicleSeat(self) -> int:
        return self.seat


class FakePlayer:
    def __init__(
        self,
        command: FakeVehicleCommand | None = None,
        *,
        parent: FakeTransport | None = None,
        crew_index: int = -1,
    ) -> None:
        self.command = command
        self.parent = parent
        self.crew_index = crew_index

    def GetCommand_Vehicle(self) -> FakeVehicleCommand | None:
        return self.command

    def GetParent(self):
        return self.parent


class FakeGame:
    def __init__(self) -> None:
        self.player: FakePlayer | None = None
        self.player_reads = 0
        self.object_reads = 0

    def GetPlayer(self) -> FakePlayer | None:
        self.player_reads += 1
        return self.player


class Caster:
    def __init__(self, accepts) -> None:
        self.accepts = accepts

    def Cast(self, value: object) -> object:
        return value if value is not None and self.accepts(value) else None


class DriveModel:
    """Translated client telemetry, wired to fakes of one game."""

    def __init__(self, source: str) -> None:
        prepared = source.replace("new array<float>()", "AllocFloatArray()")
        self.game = FakeGame()
        self.near: FakeTransport | None = None
        self.find_calls = 0
        self.namespace: dict[str, object] = {
            "GetGame": lambda: self.game,
            "PlayerBase": Caster(lambda value: isinstance(value, FakePlayer)),
            "Transport": Caster(lambda value: isinstance(value, FakeTransport)),
            "CarScript": Caster(
                lambda value: isinstance(value, FakeTransport) and value.carscript
            ),
            "DayZPlayerConstants": SEATS,
            "Vector": lambda x, y, z: (x, y, z),
            "AllocFloatArray": list,
            "VectorToArray": lambda _vec, _arr: None,
            "EncodeNetworkMoveStrategy": lambda strategy: strategy,
            "FindTransportNearClient": self._find,
        }
        translate(prepared, FINITE, self.namespace)
        translate(
            prepared,
            LIVE,
            self.namespace,
            class_types={"PlayerBase", "Transport"},
        )
        translate(
            prepared,
            SEAT_TOKEN,
            self.namespace,
        )
        self.dispatch = translate(
            prepared,
            TELEMETRY,
            self.namespace,
            class_types={
                "PlayerBase",
                "HumanCommandVehicle",
                "Transport",
                "CarScript",
                "PlayerIdentity",
                "MCPArgs",
                "MCPCommand",
                "MCPResult",
            },
        )

    def _find(self, _pos: object) -> FakeTransport | None:
        self.find_calls += 1
        self.game.object_reads += 1
        return self.near

    def run(self, pos: list[float] | None = None, args: object | None = None):
        if args is None:
            if pos is None:
                args_obj = SimpleNamespace(pos=None)
            else:
                args_obj = SimpleNamespace(pos=FakeArray(pos))
        else:
            args_obj = args
        command = SimpleNamespace(args=args_obj)
        result = SimpleNamespace(
            ok=None,
            error=EnforceString(b""),
            found=None,
            seated=None,
            seat=None,
            type=None,
            classname=None,
            direction=None,
            speedo_max=None,
            gear=None,
            lights_on_client=False,
            engine_on_server=None,
            net_strategy=None,
            pos_real=None,
            is_owner=None,
            is_authority_owner=None,
            owner_identity=None,
            net_id_low=None,
            net_id_high=None,
        )
        posted = self.dispatch(command, result)
        return posted, result


class PopulateModel:
    def __init__(self, source: str) -> None:
        self.namespace: dict[str, object] = {
            "Car": Caster(lambda value: isinstance(value, FakeTransport) and value.carscript),
            "EntityAI": Caster(lambda _value: False),
            "GetVelocity": lambda _found: (0.0, 0.0, 0.0),
            "VectorToArray": lambda _vec, _arr: None,
            "CarFluid": SimpleNamespace(FUEL=1),
            "PopulateTelemetryInventory": lambda _entity, _telemetry: None,
        }
        self.populate = translate(
            source,
            POPULATE,
            self.namespace,
            class_types={"Object", "MCPTelemetry", "EntityAI", "Car", "string"},
        )

    def run(self, found: FakeTransport) -> SimpleNamespace:
        telemetry = SimpleNamespace(
            found=False,
            type="",
            class_name="",
            pos=[],
            orientation=[],
            direction=[],
            velocity=[],
            health01=0.0,
            engine_on_server=False,
            speedo=0.0,
            wheel_count=0,
            fuel_fraction=0.0,
            lights_on_server=False,
        )
        self.populate(found, telemetry)
        return telemetry


def _class_body(source: str, name: str) -> str:
    start = source.index(f"\nclass {name}\n")
    end = source.find("\nclass ", start + 1)
    return source[start:end if end != -1 else len(source)]


class MessageFieldsLightsTest(unittest.TestCase):
    """The scripts assign these fields; without the declaration Enforce does not compile."""

    def test_mcptelemetry_declares_lights_on_server(self) -> None:
        body = _class_body(MESSAGES.read_text(encoding="utf-8"), "MCPTelemetry")
        self.assertIn("\tbool lights_on_server;\n", body)

    def test_mcpresult_declares_lights_on_client(self) -> None:
        body = _class_body(MESSAGES.read_text(encoding="utf-8"), "MCPResult")
        self.assertIn("\tbool lights_on_client;\n", body)


class PopulateTelemetryLightsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = SERVER_BRIDGE.read_text(encoding="utf-8")
        self.model = PopulateModel(self.source)
        body = method_body(self.source, POPULATE)
        self.assertIn("telemetry.lights_on_server = car.LightIsOn();", body)
        self.assertIn("car.GetFluidFraction(CarFluid.FUEL)", body)

    def test_car_with_lights_on_sets_lights_on_server_true(self) -> None:
        car = FakeTransport(lights=True)
        telemetry = self.model.run(car)
        self.assertIs(telemetry.lights_on_server, True)
        self.assertEqual(car.light_calls, 1)

    def test_car_with_lights_off_sets_lights_on_server_false(self) -> None:
        car = FakeTransport(lights=False)
        telemetry = self.model.run(car)
        self.assertIs(telemetry.lights_on_server, False)
        self.assertEqual(car.light_calls, 1)

    def test_non_car_does_not_call_light_is_on_and_keeps_default_false(self) -> None:
        prop = FakeTransport(carscript=False, type_name="Apple", classname="Apple")
        telemetry = self.model.run(prop)
        self.assertIs(telemetry.lights_on_server, False)
        self.assertEqual(prop.light_calls, 0)


class DispatchVehicleTelemetryLightsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = CLIENT_BRIDGE.read_text(encoding="utf-8")
        self.model = DriveModel(self.source)
        body = method_body(self.source, TELEMETRY)
        self.assertIn("result.lights_on_client = car.LightIsOn();", body)
        self.assertEqual(body.count("ResolveLiveSeatedTransport(player)"), 1)

    def _seat_driver(self, car: FakeTransport, *, owner_command: bool = True) -> FakePlayer:
        command = FakeVehicleCommand(car, SEATS.VEHICLESEAT_DRIVER)
        player = FakePlayer(command if owner_command else None, parent=car, crew_index=0)
        self.model.game.player = player
        return player

    def test_seated_carscript_lights_on(self) -> None:
        car = FakeTransport(lights=True)
        self._seat_driver(car)
        posted, result = self.model.run()
        self.assertIs(posted, True)
        self.assertIs(result.ok, True)
        self.assertIs(result.found, True)
        self.assertIs(result.seated, True)
        self.assertIs(result.lights_on_client, True)

    def test_seated_carscript_lights_off(self) -> None:
        car = FakeTransport(lights=False)
        self._seat_driver(car)
        posted, result = self.model.run()
        self.assertIs(posted, True)
        self.assertIs(result.ok, True)
        self.assertIs(result.lights_on_client, False)

    def test_pos_finds_car_while_player_is_on_foot(self) -> None:
        car = FakeTransport(
            lights=True, type_name="OffroadHatchback", classname="OffroadHatchback"
        )
        self.model.game.player = FakePlayer()
        self.model.near = car
        posted, result = self.model.run(pos=[1.0, 2.0, 3.0])
        self.assertIs(posted, True)
        self.assertIs(result.ok, True)
        self.assertIs(result.found, True)
        self.assertIs(result.seated, False)
        self.assertEqual(
            result.seat.text if isinstance(result.seat, EnforceString) else result.seat,
            "",
        )
        self.assertEqual(result.type, "OffroadHatchback")
        self.assertEqual(result.classname, "OffroadHatchback")
        self.assertIs(result.lights_on_client, True)

    def test_pos_finds_the_seated_car(self) -> None:
        car = FakeTransport(lights=False)
        self._seat_driver(car)
        self.model.near = car
        posted, result = self.model.run(pos=[1.0, 2.0, 3.0])
        self.assertIs(result.found, True)
        self.assertIs(result.seated, True)
        seat = result.seat.text if hasattr(result.seat, "text") else result.seat
        self.assertEqual(seat, "driver")

    def test_pos_with_no_transport_nearby_is_found_false(self) -> None:
        self.model.game.player = FakePlayer()
        self.model.near = None
        posted, result = self.model.run(pos=[1.0, 2.0, 3.0])
        self.assertIs(posted, True)
        self.assertIs(result.ok, True)
        self.assertIs(result.found, False)

    def test_bad_pos_does_not_read_player_or_objects(self) -> None:
        self.model.game.player = FakePlayer()
        self.model.near = FakeTransport(lights=True)
        cases = (
            [float("nan"), 2.0, 3.0],
            [1.0, float("inf"), 3.0],
            [1.0, 2.0],
        )
        for pos in cases:
            with self.subTest(pos=pos):
                self.model.game.player_reads = 0
                self.model.game.object_reads = 0
                self.model.find_calls = 0
                posted, result = self.model.run(pos=pos)
                self.assertIs(posted, True)
                self.assertIs(result.ok, False)
                self.assertEqual(result.error.text, "bad_args")
                self.assertEqual(self.model.game.player_reads, 0)
                self.assertEqual(self.model.game.object_reads, 0)
                self.assertEqual(self.model.find_calls, 0)

    def test_without_pos_matches_today(self) -> None:
        posted, result = self.model.run()
        self.assertIs(posted, True)
        self.assertIs(result.ok, True)
        self.assertIs(result.found, False)
        car = FakeTransport(lights=True)
        self._seat_driver(car)
        posted, result = self.model.run()
        self.assertIs(result.found, True)
        self.assertIs(result.seated, True)
        self.assertIs(result.lights_on_client, True)
        self.assertEqual(self.model.find_calls, 0)


class VehicleTelemetryToolPosTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _m: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=runtime):
            self.app, self.runtime = build_app(config)

    async def test_with_pos_forwards_pos_to_the_bridge(self) -> None:
        self.runtime.call_bridge = AsyncMock(return_value={"ok": True, "found": False})
        await self.app.call_tool("vehicle_telemetry", {"pos": [1.0, 2.0, 3.0], "timeout_s": 1.0})
        self.runtime.call_bridge.assert_awaited_once_with(
            "vehicle_telemetry", {"pos": [1.0, 2.0, 3.0]}, "client", 1.0
        )

    async def test_without_pos_forwards_empty_args(self) -> None:
        self.runtime.call_bridge = AsyncMock(return_value={"ok": True, "found": False})
        await self.app.call_tool("vehicle_telemetry", {"timeout_s": 1.0})
        self.runtime.call_bridge.assert_awaited_once_with(
            "vehicle_telemetry", {}, "client", 1.0
        )

    async def test_invalid_pos_is_tool_error_before_the_bridge(self) -> None:
        self.runtime.call_bridge = AsyncMock(return_value={"ok": True, "found": False})
        with self.assertRaises(ToolError) as caught:
            await self.app.call_tool(
                "vehicle_telemetry", {"pos": [1.0, float("nan"), 3.0]}
            )
        self.assertIn("bad_pos", str(caught.exception))
        self.runtime.call_bridge.assert_not_awaited()


class ResultPruneLightsTest(unittest.TestCase):
    def test_lights_on_client_stays_on_vehicle_telemetry(self) -> None:
        self.assertEqual(
            result_prune.OWNED_SCALAR_FIELDS.get("lights_on_client"),
            frozenset({"vehicle_telemetry"}),
        )
        pruned = result_prune.prune_unfilled_fields(
            "vehicle_telemetry", {"ok": 1, "lights_on_client": 0, "gear": 0}
        )
        self.assertEqual(pruned["lights_on_client"], 0)
        self.assertEqual(pruned["gear"], 0)

    def test_lights_on_client_is_dropped_on_vehicle_control(self) -> None:
        pruned = result_prune.prune_unfilled_fields(
            "vehicle_control",
            {"ok": 1, "lights_on_client": 0, "engine_on_server": 1},
        )
        self.assertNotIn("lights_on_client", pruned)
        self.assertEqual(pruned["engine_on_server"], 1)


class TelemetryReadDescriptionLightsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _m: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=runtime):
            self.app, _runtime = build_app(config)

    def test_telemetry_read_lists_lights_on_server(self) -> None:
        tool = self.app._tool_manager.get_tool("telemetry_read")
        description = tool.description or ""
        self.assertIn("lights_on_server", description)
        self.assertIn(
            "engine_on_server,speedo,wheel_count,fuel_fraction,lights_on_server",
            description,
        )

    def test_vehicle_telemetry_describes_lights_and_pos(self) -> None:
        tool = self.app._tool_manager.get_tool("vehicle_telemetry")
        description = tool.description or ""
        self.assertIn("lights_on_client", description)
        self.assertIn("LightIsOn", description)
        self.assertIn("4 m", description)
        self.assertIn("ToolError(bad_pos)", description)
        self.assertIn("It is omitted when no transport is read", description)
        self.assertNotIn("math", description)


if __name__ == "__main__":
    unittest.main()
