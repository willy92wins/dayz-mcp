"""fb-20261127-181405-b95f: engine_set and vehicle_control drive a vanilla motorbike.

A vanilla motorbike (MotorbikeScript, motorbikescript.c:53; Motorbike_01_* in DayZ 1.30) is not a
CarScript, so both verbs answered not_a_car for its driver. The bridge now also resolves the motorbike
the local player drives and whose simulation this client owns, with the car's refusal codes in the car's
order: not_seated, then not_a_car (a vehicle that is neither a car nor a motorbike, such as a boat), then
not_driver, then not_owner. A motorbike reports VEHICLESEAT_DRIVER for every seat
(MotorbikeScript.GetSeatAnimationType, motorbikescript.c:449-452) and its config marks both crew
positions as drivers, so its driver is crew position 0 (Transport.CrewMemberIndex, transport.c:186). The
fake bike below reports the driver seat for every position, so a resolver that reads the seat type lets
the passenger drive and fails here.

No Enforce runtime: tests.enforce_subset_helpers translates the bridge methods and MCPCarDrive's statics
from the source and runs them against fakes of the vanilla calls they make.
"""
from __future__ import annotations

import re
import unittest
from types import SimpleNamespace

from dayz_mcp.server import ServerConfig, build_app
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import EnforceString, method_body, translate

CLIENT_BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
CAR_SCRIPT = addon_root() / "scripts" / "4_World" / "MCP_CarScript.c"
BIKE_SCRIPT = addon_root() / "scripts" / "4_World" / "MCP_MotorbikeScript.c"
RESOLVE_CAR = "protected CarScript ResolveOwnedCar(out string error)"
RESOLVE_BIKE = "protected MotorbikeScript ResolveOwnedMotorbike(out string error)"
ENGINE_SET = "protected bool DispatchEngineSet(MCPCommand command, MCPResult result)"
VEHICLE_CONTROL = "protected bool DispatchVehicleControl(MCPCommand command, MCPResult result)"
FINITE = "protected bool IsFiniteFloat(float value)"
DRIVE_SET = "static void Set(CarScript car, float throttle, float steer, float brake, float handbrake, float deadlineS)"
DRIVE_SET_BIKE = (
    "static void SetMotorbike(MotorbikeScript bike, float throttle, float steer, float brake, float handbrake, "
    "float deadlineS)"
)
DRIVE_CLEAR = "static void Clear()"
REFUSALS = ("not_seated", "not_a_car", "not_driver", "not_owner")
DRIVE_MEMBERS = (
    "s_Active",
    "s_Car",
    "s_Bike",
    "s_Throttle",
    "s_Steer",
    "s_Brake",
    "s_Handbrake",
    "s_DeadlineS",
    "s_TickEngineReady",
    "s_TickThrottleSet",
    "s_LastAutoShiftS",
)

SEATS = SimpleNamespace(
    VEHICLESEAT_DRIVER=101,
    VEHICLESEAT_CODRIVER=102,
    VEHICLESEAT_PASSENGER_L=103,
    VEHICLESEAT_PASSENGER_R=104,
)
CAR_POSITION = {
    SEATS.VEHICLESEAT_DRIVER: 0,
    SEATS.VEHICLESEAT_CODRIVER: 1,
    SEATS.VEHICLESEAT_PASSENGER_L: 2,
    SEATS.VEHICLESEAT_PASSENGER_R: 3,
}
CONTROL_ARGS = {"throttle": 0.5, "steer": -0.25, "brake": 0.0, "handbrake": 0.0, "hold_ttl_s": 8.0}
TICK_TIME = 100.0


class FakeVehicle:
    """A transport of one kind: car (CarScript), bike (MotorbikeScript) or boat (neither)."""

    def __init__(self, kind: str, *, owner: bool) -> None:
        self.kind = kind
        self.owner = owner
        self.engine_on = False
        self.crew: dict[int, object] = {}
        self.calls: list[object] = []

    def IsOwner(self) -> bool:
        self.calls.append("IsOwner")
        return self.owner

    def EngineStart(self) -> None:
        self.calls.append("EngineStart")
        self.engine_on = True

    def EngineStop(self) -> None:
        self.calls.append("EngineStop")
        self.engine_on = False

    def EngineIsOn(self) -> bool:
        return self.engine_on

    def CrewMemberIndex(self, player: object) -> int:
        for position, member in self.crew.items():
            if member is player:
                return position
        return -1

    def CrewDriver(self) -> object:
        return self.crew.get(0)

    def SetBrakesActivateWithoutDriver(self, activate: bool = True) -> None:
        self.calls.append(("SetBrakesActivateWithoutDriver", activate))


class FakeVehicleCommand:
    def __init__(self, transport: FakeVehicle, seat: int) -> None:
        self.transport = transport
        self.seat = seat

    def GetTransport(self) -> FakeVehicle:
        return self.transport

    def GetVehicleSeat(self) -> int:
        return self.seat


class FakePlayer:
    def __init__(self) -> None:
        self.command: FakeVehicleCommand | None = None

    def GetCommand_Vehicle(self) -> FakeVehicleCommand | None:
        return self.command


class FakeGame:
    def __init__(self) -> None:
        self.player: FakePlayer | None = None

    def GetPlayer(self) -> FakePlayer | None:
        return self.player

    def GetTickTime(self) -> float:
        return TICK_TIME


class Caster:
    """Class.Cast: the value when it is of the class, else null."""

    def __init__(self, accepts) -> None:
        self.accepts = accepts

    def Cast(self, value: object) -> object:
        return value if value is not None and self.accepts(value) else None


class DriveRecorder:
    """MCPCarDrive as the verbs see it: every static call is recorded with its arguments."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args: self.calls.append((name,) + args)


def _seat(vehicle: FakeVehicle, position: int, seat: int) -> FakePlayer:
    player = FakePlayer()
    player.command = FakeVehicleCommand(vehicle, seat)
    vehicle.crew[position] = player
    return player


def _car(seat: int, *, owner: bool = True):
    car = FakeVehicle("car", owner=owner)
    return _seat(car, CAR_POSITION[seat], seat), car


def _bike(position: int, *, owner: bool = True, driver_present: bool = False):
    bike = FakeVehicle("bike", owner=owner)
    if driver_present:
        _seat(bike, 0, SEATS.VEHICLESEAT_DRIVER)
    # Every motorbike seat reads VEHICLESEAT_DRIVER (motorbikescript.c:449-452).
    return _seat(bike, position, SEATS.VEHICLESEAT_DRIVER), bike


def _boat(seat: int):
    boat = FakeVehicle("boat", owner=True)
    return _seat(boat, CAR_POSITION[seat], seat), boat


# state -> (builder of (player, vehicle), refusal code, or "" when the verb acts on the vehicle)
SCENARIOS = {
    "no_player": (lambda: (None, None), "not_seated"),
    "on_foot": (lambda: (FakePlayer(), None), "not_seated"),
    "boat_driver": (lambda: _boat(SEATS.VEHICLESEAT_DRIVER), "not_a_car"),
    "boat_codriver": (lambda: _boat(SEATS.VEHICLESEAT_CODRIVER), "not_a_car"),
    "car_owning_driver": (lambda: _car(SEATS.VEHICLESEAT_DRIVER), ""),
    "car_codriver": (lambda: _car(SEATS.VEHICLESEAT_CODRIVER), "not_driver"),
    "car_driver_not_owner": (lambda: _car(SEATS.VEHICLESEAT_DRIVER, owner=False), "not_owner"),
    "bike_owning_driver": (lambda: _bike(0), ""),
    "bike_driver_not_owner": (lambda: _bike(0, owner=False), "not_owner"),
    "bike_passenger_behind_driver": (lambda: _bike(1, driver_present=True), "not_driver"),
    "bike_lone_passenger": (lambda: _bike(1), "not_driver"),
    "bike_passenger_not_owner": (lambda: _bike(1, owner=False), "not_driver"),
}
DRIVE_STORE = {"car": "Set", "bike": "SetMotorbike"}


def _float_constant(source: str, name: str) -> float:
    match = re.search(r"protected const float " + name + r" = ([0-9.]+);", source)
    if match is None:
        raise AssertionError(f"missing constant {name}")
    return float(match.group(1))


class BridgeModel:
    """The translated client methods, wired to fakes of one game."""

    def __init__(self, source: str) -> None:
        self.game = FakeGame()
        self.drive = DriveRecorder()
        self.namespace: dict[str, object] = {
            "GetGame": lambda: self.game,
            "PlayerBase": Caster(lambda value: isinstance(value, FakePlayer)),
            "CarScript": Caster(lambda value: isinstance(value, FakeVehicle) and value.kind == "car"),
            "MotorbikeScript": Caster(lambda value: isinstance(value, FakeVehicle) and value.kind == "bike"),
            "DayZPlayerConstants": SEATS,
            "MCPCarDrive": self.drive,
            "VEHICLE_CONTROL_DEFAULT_TTL_S": _float_constant(source, "VEHICLE_CONTROL_DEFAULT_TTL_S"),
            "VEHICLE_CONTROL_MAX_TTL_S": _float_constant(source, "VEHICLE_CONTROL_MAX_TTL_S"),
        }
        resolver_types = {"PlayerBase", "HumanCommandVehicle", "CarScript", "MotorbikeScript"}
        self.resolve_car = translate(source, RESOLVE_CAR, self.namespace, class_types=resolver_types)
        self.resolve_bike = translate(source, RESOLVE_BIKE, self.namespace, class_types=resolver_types)
        translate(source, FINITE, self.namespace)
        resolvers = {"ResolveOwnedCar": 1, "ResolveOwnedMotorbike": 1}
        verb_types = {"CarScript", "MotorbikeScript", "MCPArgs"}
        self.engine_set = translate(
            source, ENGINE_SET, self.namespace, class_types=verb_types, out_functions=resolvers
        )
        self.vehicle_control = translate(
            source, VEHICLE_CONTROL, self.namespace, class_types=verb_types, out_functions=resolvers
        )

    def seat(self, build) -> FakeVehicle | None:
        player, vehicle = build()
        self.game.player = player
        self.drive.calls.clear()
        return vehicle

    def run_engine_set(self, mode: str):
        command = SimpleNamespace(args=SimpleNamespace(mode=EnforceString.of(mode)))
        result = SimpleNamespace(ok=None, error=EnforceString(b""), engine_on_server=None)
        return self.engine_set(command, result), result

    def run_vehicle_control(self):
        command = SimpleNamespace(args=SimpleNamespace(**CONTROL_ARGS))
        result = SimpleNamespace(ok=None, error=EnforceString(b""), engine_on_server=None)
        return self.vehicle_control(command, result), result


def _outcomes(model: BridgeModel) -> dict[tuple[str, str], tuple]:
    """(verb, state) -> (posted, ok, error, what reached the vehicle), by value only."""
    outcomes: dict[tuple[str, str], tuple] = {}
    for state, (build, _expected) in SCENARIOS.items():
        for mode in ("start", "stop"):
            vehicle = model.seat(build)
            posted, result = model.run_engine_set(mode)
            effects = tuple(call for call in (vehicle.calls if vehicle else []) if call != "IsOwner")
            outcomes[("engine_set " + mode, state)] = (posted, result.ok, result.error.text, effects)
        vehicle = model.seat(build)
        posted, result = model.run_vehicle_control()
        calls = tuple(
            (call[0], "seated_vehicle" if call[1] is vehicle else repr(call[1])) + tuple(call[2:])
            for call in model.drive.calls
        )
        outcomes[("vehicle_control", state)] = (posted, result.ok, result.error.text, calls)
    return outcomes


def _mutate(source: str, signature: str, old: str, new: str) -> str:
    start = source.index("{", source.index(signature))
    end = source.index("\n\t}\n", start)
    at = source.find(old, start, end)
    if at < 0:
        raise AssertionError(f"mutation target {old!r} not in {signature}")
    return source[:at] + new + source[at + len(old) :]


class MotorbikeVerbTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = CLIENT_BRIDGE.read_text(encoding="utf-8")
        self.model = BridgeModel(self.source)

    def test_engine_set_acts_only_for_the_owning_driver_of_a_car_or_a_motorbike(self) -> None:
        for state, (build, expected) in SCENARIOS.items():
            for mode in ("start", "stop"):
                with self.subTest(state=state, mode=mode):
                    vehicle = self.model.seat(build)
                    posted, result = self.model.run_engine_set(mode)
                    self.assertIs(posted, True)
                    effects = [call for call in (vehicle.calls if vehicle else []) if call != "IsOwner"]
                    if expected:
                        self.assertIs(result.ok, False)
                        self.assertEqual(result.error.text, expected)
                        self.assertEqual(effects, [])
                        self.assertIsNone(result.engine_on_server)
                    else:
                        self.assertIs(result.ok, True)
                        self.assertEqual(result.error.text, "")
                        self.assertEqual(effects, ["EngineStart" if mode == "start" else "EngineStop"])
                        self.assertIs(result.engine_on_server, mode == "start")

    def test_vehicle_control_hands_the_controls_to_the_store_of_that_vehicle(self) -> None:
        deadline = TICK_TIME + CONTROL_ARGS["hold_ttl_s"]
        for state, (build, expected) in SCENARIOS.items():
            with self.subTest(state=state):
                vehicle = self.model.seat(build)
                posted, result = self.model.run_vehicle_control()
                self.assertIs(posted, True)
                if expected:
                    self.assertIs(result.ok, False)
                    self.assertEqual(result.error.text, expected)
                    self.assertEqual(self.model.drive.calls, [])
                else:
                    self.assertIs(result.ok, True)
                    self.assertEqual(
                        self.model.drive.calls,
                        [(DRIVE_STORE[vehicle.kind], vehicle, 0.5, -0.25, 0.0, 0.0, deadline)],
                    )
                    self.assertIs(result.engine_on_server, False)

    def test_the_motorbike_resolver_returns_the_four_codes_in_the_car_order(self) -> None:
        body = method_body(self.source, RESOLVE_BIKE)
        coded = re.findall(r'error = "([a-z_]+)";\s*return null;', body)
        self.assertEqual(len(coded), body.count("return null;"))
        self.assertEqual(coded, ["not_seated", "not_seated", "not_a_car", "not_driver", "not_owner"])
        self.assertLess(body.index("MotorbikeScript.Cast("), body.index('error = "not_a_car";'))
        self.assertLess(body.index("CrewMemberIndex(player)"), body.index("IsOwner()"))
        # A motorbike's seat type cannot tell its driver from its passenger.
        self.assertNotIn("GetVehicleSeat", body)
        for state, (build, expected) in SCENARIOS.items():
            with self.subTest(state=state):
                vehicle = self.model.seat(build)
                resolved, error = self.model.resolve_bike()
                bike_expected = expected if vehicle is not None and vehicle.kind == "bike" else None
                if vehicle is None:
                    self.assertIsNone(resolved)
                    self.assertEqual(error.text, "not_seated")
                elif vehicle.kind != "bike":
                    self.assertIsNone(resolved)
                    self.assertEqual(error.text, "not_a_car")
                elif bike_expected:
                    self.assertIsNone(resolved)
                    self.assertEqual(error.text, bike_expected)
                else:
                    self.assertIs(resolved, vehicle)
                    self.assertEqual(error.text, "")

    def test_both_verbs_try_the_motorbike_only_after_the_car_answered_not_a_car(self) -> None:
        for signature in (ENGINE_SET, VEHICLE_CONTROL):
            with self.subTest(verb=signature):
                body = method_body(self.source, signature)
                self.assertEqual(body.count("ResolveOwnedCar(carError)"), 1)
                self.assertEqual(body.count("ResolveOwnedMotorbike(carError)"), 1)
                guard = method_body(body, 'if (!car && carError == "not_a_car")')
                self.assertEqual(" ".join(guard.split()), "bike = ResolveOwnedMotorbike(carError);")
                refusal = " ".join(method_body(body, "if (!car && !bike)").split())
                self.assertEqual(refusal, "result.ok = false; result.error = carError; return true;")
                self.assertNotIn('result.error = "not_seated"', body)


class DriveStoreTest(unittest.TestCase):
    """MCPCarDrive drives one vehicle at a time, and a car it lets go gets its driverless brake back."""

    def setUp(self) -> None:
        source = CAR_SCRIPT.read_text(encoding="utf-8")
        self.game = FakeGame()
        self.namespace: dict[str, object] = {"GetGame": lambda: self.game}
        for name in DRIVE_MEMBERS:
            self.namespace[name] = None
        self.namespace.update(s_Active=False, s_LastAutoShiftS=-1.0, s_TickEngineReady=False, s_TickThrottleSet=False)
        self.namespace["AUTO_SHIFT_SETTLE_S"] = 0.3
        types = {"CarScript", "MotorbikeScript"}
        self.set_car = translate(source, DRIVE_SET, self.namespace, class_types=types, members=DRIVE_MEMBERS)
        self.set_bike = translate(source, DRIVE_SET_BIKE, self.namespace, class_types=types, members=DRIVE_MEMBERS)
        self.clear = translate(source, DRIVE_CLEAR, self.namespace, class_types=types, members=DRIVE_MEMBERS)

    def test_a_motorbike_drive_drops_the_car_and_gives_it_its_brake_back(self) -> None:
        car = FakeVehicle("car", owner=True)
        bike = FakeVehicle("bike", owner=True)
        self.set_car(car, 0.5, 0.0, 0.0, 0.0, TICK_TIME + 5.0)
        self.set_bike(bike, 0.7, 0.25, 0.1, 1.0, TICK_TIME + 6.0)
        ns = self.namespace
        self.assertIs(ns["s_Bike"], bike)
        self.assertIsNone(ns["s_Car"])
        self.assertIs(ns["s_Active"], True)
        self.assertEqual(
            (ns["s_Throttle"], ns["s_Steer"], ns["s_Brake"], ns["s_Handbrake"], ns["s_DeadlineS"]),
            (0.7, 0.25, 0.1, 1.0, TICK_TIME + 6.0),
        )
        self.assertIn(("SetBrakesActivateWithoutDriver", True), car.calls)
        self.assertEqual(bike.calls, [])

    def test_a_car_drive_drops_the_motorbike(self) -> None:
        car = FakeVehicle("car", owner=True)
        bike = FakeVehicle("bike", owner=True)
        self.set_bike(bike, 0.7, 0.0, 0.0, 0.0, TICK_TIME + 6.0)
        self.set_car(car, 0.5, 0.0, 0.0, 0.0, TICK_TIME + 5.0)
        self.assertIs(self.namespace["s_Car"], car)
        self.assertIsNone(self.namespace["s_Bike"])

    def test_clear_drops_the_motorbike(self) -> None:
        bike = FakeVehicle("bike", owner=True)
        self.set_bike(bike, 0.7, 0.0, 0.0, 0.0, TICK_TIME + 6.0)
        self.clear()
        self.assertIsNone(self.namespace["s_Bike"])
        self.assertIsNone(self.namespace["s_Car"])
        self.assertIs(self.namespace["s_Active"], False)


class MotorbikeOnInputTest(unittest.TestCase):
    """The modded MotorbikeScript.OnInput applies the stored controls to its own bike only."""

    def test_on_input_drives_only_the_stored_motorbike_with_the_bike_setters(self) -> None:
        # Its own file, so MCP_CarScript.c keeps the one OnInput the car tests read by signature.
        self.assertNotIn("modded class MotorbikeScript", CAR_SCRIPT.read_text(encoding="utf-8"))
        source = BIKE_SCRIPT.read_text(encoding="utf-8")
        self.assertEqual(source.count("modded class MotorbikeScript"), 1)
        bike_class = source[source.index("modded class MotorbikeScript") :]
        body = method_body(bike_class, "override void OnInput(float dt)")
        self.assertTrue(" ".join(body.split()).startswith("super.OnInput(dt);"))
        self.assertIn("MCPCarDrive.s_Bike == this", body)
        self.assertIn("MCPCarDrive.Clear();", body)
        for setter in (
            "SetThrottle(",
            "SetSteering(MCPCarDrive.s_Steer)",
            "SetBrake(MCPCarDrive.s_Brake)",
            "SetHandbrake(MCPCarDrive.s_Handbrake)",
            "SetKickstand(0.0)",
        ):
            self.assertIn(setter, body)
        # Car-only calls stay out: the motorbike has no driverless brake, and vehicle_trace is for cars.
        self.assertNotIn("SetBrakesActivateWithoutDriver", body)
        self.assertNotIn("MCPVehicleTrace", body)
        self.assertNotIn("s_TickEngineReady", body)


class MotorbikeMutantTest(unittest.TestCase):
    def test_mutants_of_the_motorbike_path_are_caught(self) -> None:
        source = CLIENT_BRIDGE.read_text(encoding="utf-8")
        expected = _outcomes(BridgeModel(source))
        self.assertEqual(_outcomes(BridgeModel(source)), expected)
        mutants = {
            "engine_set_never_tries_the_motorbike": (
                ENGINE_SET,
                "bike = ResolveOwnedMotorbike(carError);",
                "bike = null;",
            ),
            "vehicle_control_never_tries_the_motorbike": (
                VEHICLE_CONTROL,
                "bike = ResolveOwnedMotorbike(carError);",
                "bike = null;",
            ),
            "motorbike_driver_by_seat_type": (
                RESOLVE_BIKE,
                "if (bike.CrewMemberIndex(player) != 0)",
                "if (vehicleCommand.GetVehicleSeat() != DayZPlayerConstants.VEHICLESEAT_DRIVER)",
            ),
            "motorbike_owner_unchecked": (RESOLVE_BIKE, "if (!bike.IsOwner())", "if (false)"),
            "motorbike_passenger_reported_not_seated": (
                RESOLVE_BIKE,
                'error = "not_driver";',
                'error = "not_seated";',
            ),
            "motorbike_in_the_car_store": (VEHICLE_CONTROL, "MCPCarDrive.SetMotorbike(", "MCPCarDrive.Set("),
        }
        for label, (signature, old, new) in mutants.items():
            with self.subTest(mutant=label):
                mutant = BridgeModel(_mutate(source, signature, old, new))
                self.assertNotEqual(_outcomes(mutant), expected, f"{label} is not caught")


class MotorbikeDocsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))
        self.tools = {tool.name: tool for tool in await app.list_tools()}

    async def test_both_descriptions_name_the_motorbike_and_its_refusals(self) -> None:
        for tool in ("engine_set", "vehicle_control"):
            with self.subTest(tool=tool):
                description = self.tools[tool].description or ""
                self.assertIn("must drive a car or a motorbike this client owns", description)
                self.assertIn(
                    "not_a_car (seated in a vehicle that is neither a car nor a motorbike, such as a boat)",
                    description,
                )
                self.assertIn(
                    "not_driver (seated, but not in the driver seat; on a motorbike, the driver is crew "
                    "position 0)",
                    description,
                )
                self.assertIn("not_owner (in the driver seat, but this client does not own the vehicle)", description)
                self.assertNotIn("does not own the car", description)

    async def test_vehicle_control_names_the_motorbike_brakes(self) -> None:
        description = self.tools["vehicle_control"].description or ""
        self.assertIn(
            "On a motorbike, brake is the rear brake and handbrake the front brake, and the kickstand goes up "
            "while it drives.",
            description,
        )

    async def test_engine_set_names_the_motorbike_get_in(self) -> None:
        description = self.tools["engine_set"].description or ""
        self.assertIn(
            "For a motorbike, ownership comes from the vanilla get-in (action_use ActionGetInTransport).",
            description,
        )


if __name__ == "__main__":
    unittest.main()
