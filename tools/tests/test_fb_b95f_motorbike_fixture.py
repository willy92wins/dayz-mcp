"""fb-20261127-181405-b95f (GA270): vehicle_prepare_fixture also prepares a vanilla motorbike.

No MCP verb could fuel a motorbike, so a Motorbike_01_* from world_spawn could not start: its tank is empty
until OnDebugSpawn (motorbike_01.c:152-162) and vanilla OnBeforeEngineStart refuses without fuel
(motorbikescript.c:1573). DispatchVehiclePrepareFixture now hands a MotorbikeScript to
DispatchMotorbikeFixture: OnDebugSpawn when it is not ready, then the same vehicle_fixture job and wait the
car has (fb-20261026-150310-9582). A motorbike is ready as vanilla's CheckOperationalRequirements asks
(fuel above 0, the spark plug when vital; motorbikescript.c:1581-1604), plus every wheel on and the battery
when it needs one. Its reply carries the fields PopulateTelemetryObject fills for a car, through
PopulateMotorbikeTelemetry; PopulateTelemetryObject itself, which telemetry_read shares, does not change.
Anything that is neither a car nor a motorbike, such as a boat, still answers fixture_not_vehicle.

The readiness and the delayed reply are translated from the source and run against fakes
(tests.enforce_subset_helpers); the dispatch is read as the 9582 contract reads the car's.
"""
from __future__ import annotations

import re
import unittest
from types import SimpleNamespace

from dayz_mcp.server import ServerConfig, build_app
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import EnforceString, clean, method_body, translate

BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
DISPATCH = "protected bool DispatchVehiclePrepareFixture(MCPCommand command, MCPResult result)"
BIKE_DISPATCH = (
    "protected bool DispatchMotorbikeFixture(MCPCommand command, MCPResult result, MCPTelemetry telemetry, "
    "MotorbikeScript bike)"
)
BIKE_READY = "protected bool IsMotorbikeFixtureReady(MotorbikeScript bike)"
BIKE_TELEMETRY = "protected void PopulateMotorbikeTelemetry(MotorbikeScript bike, MCPTelemetry telemetry)"
POST_FIXTURE = "protected void PostVehicleFixture(MCPJob job, bool ready)"
IS_JOB_READY = "protected bool IsJobReady(MCPJob job)"
POPULATE = "protected void PopulateTelemetryObject(Object found, MCPTelemetry telemetry)"
BIKE_NOT_READY = "if (!IsMotorbikeFixtureReady(bike))"

ROUTING = (
    "MotorbikeScript bike = MotorbikeScript.Cast(match); "
    "if (bike) { return DispatchMotorbikeFixture(command, result, telemetry, bike); } "
    'result.ok = false; result.error = "fixture_not_vehicle"; result.telemetry = telemetry; return true;'
)
JOB_BRANCH = (
    "if (MotorbikeScript.Cast(job.subject)) { return IsMotorbikeFixtureReady(MotorbikeScript.Cast(job.subject)); } "
    "return IsVehicleFixtureReady(CarScript.Cast(job.subject));"
)
QUEUE_IN_ORDER = (
    "MCPJob job = new MCPJob();",
    "job.id = command.id;",
    'job.kind = "vehicle_fixture";',
    "job.args = command.args;",
    "job.subject = bike;",
    "job.deadline_s = m_ElapsedS + VEHICLE_FIXTURE_WAIT_S;",
    "job.tick_poll_sent = result.tick_poll_sent;",
    "job.tick_poll_callback = result.tick_poll_callback;",
    "job.tick_dispatch = result.tick_dispatch;",
    "m_Jobs.Insert(job.id, job);",
    "return false;",
)
FUEL = 7  # MotorbikeFluid.FUEL in the fake; CarFluid is not in the namespace at all.


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _block_after(text: str, needle: str, start: int = 0) -> tuple[int, int, int]:
    at = text.find(needle, start)
    if at < 0:
        raise AssertionError(f"missing source contract: {needle!r}")
    brace = text.index("{", at + len(needle))
    if text[at + len(needle) : brace].strip():
        raise AssertionError(f"text between {needle!r} and its block")
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return at, brace + 1, index
    raise AssertionError(f"unterminated block after {needle!r}")


def _in_order(text: str, tokens: tuple[str, ...]) -> None:
    position = 0
    for token in tokens:
        found = text.find(token, position)
        if found < 0:
            raise AssertionError(f"missing or out of order: {token}")
        position = found + len(token)


class FakeBike:
    kind = "bike"

    def __init__(self, *, wheels=2, present=2, fuel=0.4, vital_spark=True, spark=True, needs_battery=False,
                 battery=False, engine=False) -> None:
        self.wheels, self.present, self.fuel = wheels, present, fuel
        self.vital_spark, self.spark = vital_spark, spark
        self.needs_battery, self.battery = needs_battery, battery
        self.engine = engine
        self.calls: list[str] = []

    def WheelCount(self) -> int:
        return self.wheels

    def WheelCountPresent(self) -> int:
        return self.present

    def GetFluidFraction(self, fluid: int) -> float:
        if fluid != FUEL:
            raise AssertionError(f"fluid {fluid!r} is not MotorbikeFluid.FUEL")
        return self.fuel

    def IsVitalSparkPlug(self) -> bool:
        return self.vital_spark

    def FindAttachmentBySlotName(self, slot: object) -> object:
        return object() if str(getattr(slot, "text", slot)) == "SparkPlug" and self.spark else None

    def NeedElectricitySourceDevice(self) -> bool:
        return self.needs_battery

    def GetElectricitySourceDevice(self) -> object:
        return object() if self.battery else None

    def EngineIsOn(self) -> bool:
        return self.engine

    def GetSpeedometer(self) -> float:
        return 12.5

    def LightIsOn(self) -> bool:
        return True


class FakeCar:
    kind = "car"


class Caster:
    def __init__(self, kind: str) -> None:
        self.kind = kind

    def Cast(self, value: object) -> object:
        return value if value is not None and getattr(value, "kind", None) == self.kind else None


def _ready_model(source: str):
    namespace: dict[str, object] = {"MotorbikeFluid": SimpleNamespace(FUEL=FUEL)}
    return translate(source, BIKE_READY, namespace)


class ReplyModel:
    """PostVehicleFixture and PopulateMotorbikeTelemetry, translated, with fake results and posts."""

    def __init__(self, source: str) -> None:
        self.posted: list[SimpleNamespace] = []
        self.populated: list[object] = []
        self.namespace: dict[str, object] = {
            "MCPResult": lambda: SimpleNamespace(
                id=0, ok=False, error=EnforceString(b""), tick_poll_sent=0, tick_poll_callback=0, tick_dispatch=0,
                telemetry=None, vehicle_fixture_ready=False,
            ),
            "MCPTelemetry": lambda: SimpleNamespace(
                mode=EnforceString(b""), found=False, engine_on_server=False, speedo=0.0, wheel_count=0,
                fuel_fraction=0.0, lights_on_server=False,
            ),
            "CarScript": Caster("car"),
            "MotorbikeScript": Caster("bike"),
            "MotorbikeFluid": SimpleNamespace(FUEL=FUEL),
            "PostResult": self.posted.append,
            "PopulateTelemetryObject": self._populate,
        }
        types = {"MCPResult", "MCPTelemetry", "CarScript", "MotorbikeScript"}
        translate(source, BIKE_TELEMETRY, self.namespace, class_types=types)
        self.post = translate(source, POST_FIXTURE, self.namespace, class_types=types)

    def _populate(self, found: object, telemetry: SimpleNamespace) -> None:
        self.populated.append(found)
        telemetry.found = True

    def run(self, subject: object, ready: bool) -> SimpleNamespace:
        job = SimpleNamespace(
            id=41, tick_poll_sent=1, tick_poll_callback=2, tick_dispatch=3,
            args=SimpleNamespace(mode=EnforceString.of("object_at")), subject=subject,
        )
        self.posted.clear()
        self.populated.clear()
        self.post(job, ready)
        if len(self.posted) != 1:
            raise AssertionError(f"{len(self.posted)} results posted")
        return self.posted[0]


def check_ready(source: str) -> None:
    ready = _ready_model(source)
    cases = {
        "ready": (FakeBike(), True),
        "a_wheel_missing": (FakeBike(present=1), False),
        "no_fuel": (FakeBike(fuel=0.0), False),
        "vital_spark_plug_missing": (FakeBike(spark=False), False),
        "spark_plug_not_vital": (FakeBike(vital_spark=False, spark=False), True),
        "battery_needed_and_missing": (FakeBike(needs_battery=True, battery=False), False),
        "battery_needed_and_fitted": (FakeBike(needs_battery=True, battery=True), True),
        "null": (None, False),
    }
    for label, (bike, expected) in cases.items():
        if ready(bike) is not expected:
            raise AssertionError(f"IsMotorbikeFixtureReady {label}: expected {expected}")


def check_routing(source: str) -> None:
    dispatch = method_body(source, DISPATCH)
    _at, start, end = _block_after(dispatch, "if (!car)")
    if _flat(dispatch[start:end]) != ROUTING:
        raise AssertionError("the dispatch does not hand a motorbike to DispatchMotorbikeFixture first")
    body = method_body(source, BIKE_DISPATCH)
    if body.count("bike.OnDebugSpawn();") != 1 or body.count(BIKE_NOT_READY) != 2:
        raise AssertionError("OnDebugSpawn once, behind two readiness reads")
    _first, first_start, first_end = _block_after(body, BIKE_NOT_READY)
    if body[first_start:first_end].strip() != "bike.OnDebugSpawn();":
        raise AssertionError("the first read only spawns")
    _second, second_start, second_end = _block_after(body, BIKE_NOT_READY, first_end)
    _in_order(body[second_start:second_end], QUEUE_IN_ORDER)
    _in_order(
        body[second_end + 1 :],
        (
            "PopulateMotorbikeTelemetry(bike, telemetry);",
            "result.telemetry = telemetry;",
            "result.vehicle_fixture_ready = true;",
            "result.ok = true;",
            "return true;",
        ),
    )
    for token in ("fixture_not_ready", "PostResult", "EngineStart", "SetThrottle", "Shift", "SetPosition"):
        if token in body:
            raise AssertionError(f"{token} in DispatchMotorbikeFixture")


def check_job_branch(source: str) -> None:
    is_job_ready = method_body(source, IS_JOB_READY)
    _at, start, end = _block_after(is_job_ready, 'else if (job.kind == "vehicle_fixture")')
    if _flat(is_job_ready[start:end]) != JOB_BRANCH:
        raise AssertionError("the vehicle_fixture job does not read a motorbike's readiness")


def check_reply(source: str) -> None:
    model = ReplyModel(source)
    bike = FakeBike(fuel=0.6, engine=True)
    ok = model.run(bike, True)
    if not (ok.ok is True and ok.vehicle_fixture_ready is True and ok.error.text == "" and model.populated == [bike]):
        raise AssertionError("a ready motorbike is not answered ok with its telemetry")
    tele = ok.telemetry
    if (tele.fuel_fraction, tele.wheel_count, tele.engine_on_server, tele.speedo, tele.lights_on_server) != (
        0.6, 2, True, 12.5, True
    ):
        raise AssertionError("the motorbike reply lacks the car's vehicle fields")
    late = model.run(FakeBike(), False)
    if not (late.ok is False and late.vehicle_fixture_ready is False and late.error.text == "fixture_not_ready"):
        raise AssertionError("a motorbike still not ready at the deadline is not fixture_not_ready")
    gone = model.run(None, True)
    if not (gone.ok is False and gone.error.text == "fixture_not_found" and gone.telemetry.found is False):
        raise AssertionError("a vanished subject is not fixture_not_found")
    car = FakeCar()
    car_reply = model.run(car, True)
    if not (car_reply.ok is True and model.populated == [car]):
        raise AssertionError("a car is no longer answered through PopulateTelemetryObject")


class MotorbikeFixtureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = BRIDGE.read_text(encoding="utf-8")

    def test_a_motorbike_is_ready_as_vanilla_asks(self) -> None:
        check_ready(self.source)

    def test_the_dispatch_spawns_and_waits_for_a_motorbike_as_for_a_car(self) -> None:
        check_routing(self.source)

    def test_the_fixture_job_reads_the_motorbike_it_waits_for(self) -> None:
        check_job_branch(self.source)

    def test_the_delayed_reply_carries_the_motorbike(self) -> None:
        check_reply(self.source)

    def test_telemetry_read_keeps_its_shape(self) -> None:
        populate = method_body(self.source, POPULATE)
        self.assertNotIn("Motorbike", populate)
        self.assertIn("Car car = Car.Cast(found);", populate)
        self.assertIn("PopulateTelemetryObject(bike, telemetry);", method_body(self.source, BIKE_TELEMETRY))


class MotorbikeFixtureMutantTest(unittest.TestCase):
    def test_mutants_are_caught(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        for check in (check_ready, check_routing, check_job_branch, check_reply):
            check(source)
        mutants = {
            "motorbike_answered_not_vehicle": (
                "return DispatchMotorbikeFixture(command, result, telemetry, bike);", "bike = null;", check_routing
            ),
            "motorbike_never_spawned": ("\t\t\tbike.OnDebugSpawn();\n", "", check_routing),
            "motorbike_ready_without_fuel": (
                "if (bike.GetFluidFraction(MotorbikeFluid.FUEL) <= 0.0)", "if (false)", check_ready
            ),
            "motorbike_ready_without_wheels": (
                "if (bike.WheelCountPresent() != bike.WheelCount())", "if (false)", check_ready
            ),
            "job_reads_only_cars": (
                "return IsMotorbikeFixtureReady(MotorbikeScript.Cast(job.subject));",
                "return IsVehicleFixtureReady(CarScript.Cast(job.subject));",
                check_job_branch,
            ),
            "reply_drops_the_motorbike": ("\t\t\t\tPopulateMotorbikeTelemetry(bike, telemetry);\n", "", check_reply),
        }
        for label, (old, new, check) in mutants.items():
            with self.subTest(mutant=label):
                self.assertEqual(source.count(old), 1, f"mutant anchor of {label}")
                with self.assertRaises(AssertionError):
                    check(source.replace(old, new))


class MotorbikeFixtureDocsTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_description_names_the_motorbike(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))
        tools = {tool.name: tool for tool in await app.list_tools()}
        description = tools["vehicle_prepare_fixture"].description or ""
        self.assertIn("Any CarScript or MotorbikeScript classname", description)
        self.assertIn("anything else, such as a boat, returns fixture_not_vehicle", description)
        self.assertIn("fuel above 0", description)


if __name__ == "__main__":
    unittest.main()
