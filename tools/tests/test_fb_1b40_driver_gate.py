"""fb-20260822-191204-1b40: engine_set and vehicle_control act only for the owning driver.

ResolveOwnedCar (MCPClientBridge.c) returned the CarScript of any vehicle
command the local player had, so a passenger, or a driver whose client does not
own the car, got ok=true from engine_set and vehicle_control. It now returns
null with an error and both verbs answer that error before touching the car:
not_seated for the old refusals (no player, no vehicle command, not a
CarScript), then not_driver when HumanCommandVehicle.GetVehicleSeat
(human.c:696) is not DayZPlayerConstants.VEHICLESEAT_DRIVER (dayzplayer.c:674),
then not_owner when Pawn.IsOwner (pawn.c:193-194) is false.

No Enforce runtime: tests.enforce_subset_helpers translates ResolveOwnedCar,
DispatchEngineSet, DispatchVehicleControl and IsFiniteFloat from the source and
runs them against fakes of the vanilla calls they make. The fake seat constants
are distinct values unrelated to crew indices, so only a comparison with the
named constant passes.
"""
from __future__ import annotations

import re
import unittest
from types import SimpleNamespace

from dayz_mcp import loopback
from dayz_mcp.server import ServerConfig, build_app
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import EnforceString, method_body, translate

SCRIPTS = addon_root() / "scripts" / "5_Mission"
CLIENT_BRIDGE = SCRIPTS / "MCPClientBridge.c"
MESSAGES = SCRIPTS / "MCPMessages.c"
RESOLVE = "protected CarScript ResolveOwnedCar(out string error)"
ENGINE_SET = "protected bool DispatchEngineSet(MCPCommand command, MCPResult result)"
VEHICLE_CONTROL = "protected bool DispatchVehicleControl(MCPCommand command, MCPResult result)"
FINITE = "protected bool IsFiniteFloat(float value)"
GET_IN_PREP = "protected bool ProcessVehicleGetInClientPrep(MCPJob job)"
CAPTURE = "protected void CaptureDriveProbeClientOwnership(MCPJob job, CarScript car)"
DRIVER_CHECK = "if (vehicleCommand.GetVehicleSeat() != DayZPlayerConstants.VEHICLESEAT_DRIVER)"
REFUSALS = ("not_seated", "not_driver", "not_owner")

SEATS = SimpleNamespace(
    VEHICLESEAT_DRIVER=101,
    VEHICLESEAT_CODRIVER=102,
    VEHICLESEAT_PASSENGER_L=103,
    VEHICLESEAT_PASSENGER_R=104,
)
CONTROL_ARGS = {"throttle": 0.5, "steer": -0.25, "brake": 0.0, "handbrake": 0.0, "hold_ttl_s": 8.0}
TICK_TIME = 100.0


class FakeTransport:
    """A seated transport. carscript=False stands for a boat, not a CarScript."""

    def __init__(self, *, owner: bool, carscript: bool = True) -> None:
        self.owner = owner
        self.carscript = carscript
        self.engine_on = False
        self.calls: list[str] = []

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


class FakeVehicleCommand:
    def __init__(self, transport: FakeTransport, seat: int) -> None:
        self.transport = transport
        self.seat = seat

    def GetTransport(self) -> FakeTransport:
        return self.transport

    def GetVehicleSeat(self) -> int:
        return self.seat


class FakePlayer:
    def __init__(self, command: FakeVehicleCommand | None) -> None:
        self.command = command

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


class FakeCarDrive:
    def __init__(self) -> None:
        self.sets: list[tuple] = []

    def Set(self, *values: object) -> None:
        self.sets.append(values)


def _seated(seat: int, *, owner: bool = True, carscript: bool = True):
    transport = FakeTransport(owner=owner, carscript=carscript)
    return FakePlayer(FakeVehicleCommand(transport, seat)), transport


# state -> (builder of (player, transport), refusal code, or "" when the verb acts)
SCENARIOS = {
    "no_player": (lambda: (None, None), "not_seated"),
    "on_foot": (lambda: (FakePlayer(None), None), "not_seated"),
    "boat_driver": (lambda: _seated(SEATS.VEHICLESEAT_DRIVER, carscript=False), "not_seated"),
    "codriver": (lambda: _seated(SEATS.VEHICLESEAT_CODRIVER), "not_driver"),
    "passenger_left": (lambda: _seated(SEATS.VEHICLESEAT_PASSENGER_L), "not_driver"),
    "passenger_right": (lambda: _seated(SEATS.VEHICLESEAT_PASSENGER_R), "not_driver"),
    "codriver_not_owner": (lambda: _seated(SEATS.VEHICLESEAT_CODRIVER, owner=False), "not_driver"),
    "driver_not_owner": (lambda: _seated(SEATS.VEHICLESEAT_DRIVER, owner=False), "not_owner"),
    "owning_driver": (lambda: _seated(SEATS.VEHICLESEAT_DRIVER), ""),
}


def _float_constant(source: str, name: str) -> float:
    match = re.search(r"protected const float " + name + r" = ([0-9.]+);", source)
    if match is None:
        raise AssertionError(f"missing constant {name}")
    return float(match.group(1))


class DriveModel:
    """The translated client methods, wired to fakes of one game."""

    def __init__(self, source: str) -> None:
        self.game = FakeGame()
        self.drive = FakeCarDrive()
        self.namespace: dict[str, object] = {
            "GetGame": lambda: self.game,
            "PlayerBase": Caster(lambda value: isinstance(value, FakePlayer)),
            "CarScript": Caster(lambda value: isinstance(value, FakeTransport) and value.carscript),
            "DayZPlayerConstants": SEATS,
            "MCPCarDrive": self.drive,
            "VEHICLE_CONTROL_DEFAULT_TTL_S": _float_constant(source, "VEHICLE_CONTROL_DEFAULT_TTL_S"),
            "VEHICLE_CONTROL_MAX_TTL_S": _float_constant(source, "VEHICLE_CONTROL_MAX_TTL_S"),
        }
        self.resolve = translate(
            source, RESOLVE, self.namespace, class_types={"PlayerBase", "HumanCommandVehicle", "CarScript"}
        )
        translate(source, FINITE, self.namespace)
        resolver = {"ResolveOwnedCar": 1}
        self.engine_set = translate(
            source, ENGINE_SET, self.namespace, class_types={"CarScript"}, out_functions=resolver
        )
        self.vehicle_control = translate(
            source, VEHICLE_CONTROL, self.namespace, class_types={"CarScript", "MCPArgs"}, out_functions=resolver
        )

    def seat(self, build) -> FakeTransport | None:
        player, transport = build()
        self.game.player = player
        self.drive.sets.clear()
        return transport

    def run_engine_set(self, mode: str):
        command = SimpleNamespace(args=SimpleNamespace(mode=EnforceString.of(mode)))
        result = SimpleNamespace(ok=None, error=EnforceString(b""), engine_on_server=None)
        return self.engine_set(command, result), result

    def run_vehicle_control(self):
        command = SimpleNamespace(args=SimpleNamespace(**CONTROL_ARGS))
        result = SimpleNamespace(ok=None, error=EnforceString(b""), engine_on_server=None)
        return self.vehicle_control(command, result), result


def _outcomes(model: DriveModel) -> dict[tuple[str, str], tuple]:
    """(verb, state) -> (posted, ok, error, what reached the car), by value only."""
    outcomes: dict[tuple[str, str], tuple] = {}
    for state, (build, _expected) in SCENARIOS.items():
        for mode in ("start", "stop"):
            transport = model.seat(build)
            posted, result = model.run_engine_set(mode)
            effects = tuple(call for call in (transport.calls if transport else []) if call != "IsOwner")
            outcomes[("engine_set " + mode, state)] = (posted, result.ok, result.error.text, effects)
        transport = model.seat(build)
        posted, result = model.run_vehicle_control()
        # The seated transport becomes a marker so two runs compare by behaviour.
        sets = tuple(
            ("seated_car" if values[0] is transport else repr(values[0]),) + tuple(values[1:])
            for values in model.drive.sets
        )
        outcomes[("vehicle_control", state)] = (posted, result.ok, result.error.text, sets)
    return outcomes


def _mutate(source: str, signature: str, old: str, new: str) -> str:
    start = source.index("{", source.index(signature))
    end = source.index("\n\t}\n", start)
    at = source.find(old, start, end)
    if at < 0:
        raise AssertionError(f"mutation target {old!r} not in {signature}")
    return source[:at] + new + source[at + len(old) :]


class ResolveOwnedCarTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = CLIENT_BRIDGE.read_text(encoding="utf-8")
        self.model = DriveModel(self.source)

    def test_each_seat_state_resolves_to_its_refusal_or_the_car(self) -> None:
        for state, (build, expected) in SCENARIOS.items():
            with self.subTest(state=state):
                transport = self.model.seat(build)
                resolved, error = self.model.resolve()
                if expected:
                    self.assertIsNone(resolved)
                    self.assertEqual(error.text, expected)
                else:
                    self.assertIs(resolved, transport)
                    self.assertEqual(error.text, "")

    def test_every_null_carries_one_of_the_three_codes(self) -> None:
        body = method_body(self.source, RESOLVE)
        coded = re.findall(r'error = "([a-z_]+)";\s*return null;', body)
        self.assertEqual(len(coded), body.count("return null;"))
        self.assertEqual(sorted(set(coded)), sorted(REFUSALS))
        # The seat is read from the live vehicle command, against the named constant.
        self.assertEqual(body.count(DRIVER_CHECK), 1)
        self.assertIn("if (!car.IsOwner())", body)
        self.assertLess(body.index("CarScript.Cast("), body.index(DRIVER_CHECK))
        self.assertLess(body.index(DRIVER_CHECK), body.index("if (!car.IsOwner())"))


class DriveVerbGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = CLIENT_BRIDGE.read_text(encoding="utf-8")
        self.model = DriveModel(self.source)

    def test_engine_set_acts_only_for_the_owning_driver(self) -> None:
        for state, (build, expected) in SCENARIOS.items():
            for mode in ("start", "stop"):
                with self.subTest(state=state, mode=mode):
                    transport = self.model.seat(build)
                    posted, result = self.model.run_engine_set(mode)
                    self.assertIs(posted, True)
                    effects = [call for call in (transport.calls if transport else []) if call != "IsOwner"]
                    if expected:
                        self.assertIs(result.ok, False)
                        self.assertEqual(result.error.text, expected)
                        self.assertEqual(effects, [])
                        self.assertIsNone(result.engine_on_server)
                    else:
                        self.assertIs(result.ok, True)
                        self.assertEqual(effects, ["EngineStart" if mode == "start" else "EngineStop"])
                        self.assertIs(result.engine_on_server, mode == "start")

    def test_vehicle_control_acts_only_for_the_owning_driver(self) -> None:
        for state, (build, expected) in SCENARIOS.items():
            with self.subTest(state=state):
                transport = self.model.seat(build)
                posted, result = self.model.run_vehicle_control()
                self.assertIs(posted, True)
                if expected:
                    self.assertIs(result.ok, False)
                    self.assertEqual(result.error.text, expected)
                    self.assertEqual(self.model.drive.sets, [])
                else:
                    self.assertIs(result.ok, True)
                    deadline = TICK_TIME + CONTROL_ARGS["hold_ttl_s"]
                    self.assertEqual(
                        self.model.drive.sets,
                        [(transport, 0.5, -0.25, 0.0, 0.0, deadline)],
                    )

    def test_both_verbs_answer_with_the_resolver_error(self) -> None:
        for signature in (ENGINE_SET, VEHICLE_CONTROL):
            with self.subTest(verb=signature):
                body = method_body(self.source, signature)
                self.assertEqual(body.count("ResolveOwnedCar(carError)"), 1)
                refusal = " ".join(method_body(body, "if (!car)").split())
                self.assertEqual(refusal, "result.ok = false; result.error = carError; return true;")
                self.assertNotIn('result.error = "not_seated"', body)

    def test_mutants_of_the_gate_are_caught(self) -> None:
        expected = _outcomes(self.model)
        # Outcomes compare by value: the unmutated source reproduces them exactly.
        self.assertEqual(_outcomes(DriveModel(self.source)), expected)
        mutants = {
            "seat_unchecked": (RESOLVE, DRIVER_CHECK, "if (false)"),
            "codriver_counts_as_driver": (
                RESOLVE,
                "!= DayZPlayerConstants.VEHICLESEAT_DRIVER",
                "!= DayZPlayerConstants.VEHICLESEAT_CODRIVER",
            ),
            "owner_unchecked": (RESOLVE, "if (!car.IsOwner())", "if (false)"),
            "owner_inverted": (RESOLVE, "if (!car.IsOwner())", "if (car.IsOwner())"),
            "passenger_reported_not_seated": (RESOLVE, 'error = "not_driver";', 'error = "not_seated";'),
            "engine_set_legacy_code": (ENGINE_SET, "result.error = carError;", 'result.error = "not_seated";'),
            "vehicle_control_legacy_code": (
                VEHICLE_CONTROL,
                "result.error = carError;",
                'result.error = "not_seated";',
            ),
        }
        for label, (signature, old, new) in mutants.items():
            with self.subTest(mutant=label):
                mutant = DriveModel(_mutate(self.source, signature, old, new))
                self.assertNotEqual(_outcomes(mutant), expected, f"{label} is not caught")


class GetInSeatsTheDriverTest(unittest.TestCase):
    """vehicle_get_in_client seats crew position 0, which for a car is the driver."""

    def test_the_tool_cannot_ask_for_another_seat(self) -> None:
        pos = [1.0, 2.0, 3.0]
        self.assertEqual(loopback.validate_command_args("vehicle_get_in_client", {"pos": pos}), (True, None))
        self.assertEqual(
            loopback.validate_command_args("vehicle_get_in_client", {"pos": pos, "seat": 1}),
            (False, "bad_args"),
        )
        messages = MESSAGES.read_text(encoding="utf-8")
        self.assertIn("int seat;", method_body(messages, "class MCPArgs"))
        self.assertNotRegex(method_body(messages, "void MCPArgs()"), r"\bseat\s*=")

    def test_a_car_at_position_zero_must_end_in_the_driver_seat_the_gate_checks(self) -> None:
        source = CLIENT_BRIDGE.read_text(encoding="utf-8")
        prep = method_body(source, GET_IN_PREP)
        self.assertIn("seatIndex = job.args.seat;", prep)
        self.assertIn("StartCommand_Vehicle(foundCar, seatIndex, seatAnim)", prep)
        car_at_zero = method_body(prep, "if (seatIndex == 0 && car)")
        self.assertIn(DRIVER_CHECK, car_at_zero)
        self.assertIn("CaptureDriveProbeClientOwnership(job, car);", car_at_zero)
        self.assertIn("job.is_owner = car.IsOwner();", method_body(source, CAPTURE))
        self.assertIn(DRIVER_CHECK, method_body(source, RESOLVE))


class DriveVerbRefusalDocsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))
        self.tools = {tool.name: tool for tool in await app.list_tools()}

    async def test_descriptions_name_every_refusal_the_resolver_returns(self) -> None:
        body = method_body(CLIENT_BRIDGE.read_text(encoding="utf-8"), RESOLVE)
        codes = sorted(set(re.findall(r'error = "([a-z_]+)";', body)))
        self.assertEqual(codes, sorted(REFUSALS))
        for tool in ("engine_set", "vehicle_control"):
            with self.subTest(tool=tool):
                description = self.tools[tool].description or ""
                self.assertIn("must drive a car this client owns", description)
                self.assertIn("not_seated (not seated in a car)", description)
                self.assertIn("not_driver (seated, but not in the driver seat)", description)
                self.assertIn("not_owner (in the driver seat, but this client does not own the car)", description)
                for code in codes:
                    self.assertIn(code, description)


if __name__ == "__main__":
    unittest.main()
