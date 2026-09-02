from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path

from tests._addon_paths import addon_root


MOD_SCRIPTS = addon_root() / "scripts"
CLIENT_BRIDGE = MOD_SCRIPTS / "5_Mission" / "MCPClientBridge.c"
MESSAGES = MOD_SCRIPTS / "5_Mission" / "MCPMessages.c"

TELEMETRY_REGION_SHA256 = (
    "31d4012c51e3de93113af00dfac6a1c686723d8127d8640f30034ad88695fbef"
)

_TELEMETRY_REGION = re.compile(
    r"(?ms)^\tprotected bool DispatchVehicleTelemetry\(.*?(?=^\tprotected bool DispatchVehicleTrace\()"
)
_GET_IN_RESULT = re.compile(
    r"(?ms)^\t\tif \(job\.kind == \"vehicle_get_in\"\).*?(?=^\t\tif \(job\.kind == \"ui_dialog\"\))"
)


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


def _telemetry_region(source: str) -> str:
    match = _TELEMETRY_REGION.search(source)
    if not match:
        raise AssertionError("DispatchVehicleTelemetry region not isolated")
    return match.group(0)


def _telemetry_sha256(source: str) -> str:
    return hashlib.sha256(_telemetry_region(source).encode("utf-8")).hexdigest()


def _get_in_result_block(source: str) -> str:
    match = _GET_IN_RESULT.search(source)
    if not match:
        raise AssertionError("vehicle_get_in result region not isolated")
    return match.group(0)


def map_vehicle_get_in_seat_token(vehicle_seat: int, named: dict[str, int]) -> str:
    if vehicle_seat == named["VEHICLESEAT_DRIVER"]:
        return "driver"
    if vehicle_seat == named["VEHICLESEAT_CODRIVER"]:
        return "codriver"
    if vehicle_seat == named["VEHICLESEAT_PASSENGER_L"]:
        return "passenger_left"
    if vehicle_seat == named["VEHICLESEAT_PASSENGER_R"]:
        return "passenger_right"
    return "unknown"


def select_get_in_transport(
    nearby: list[dict[str, str]], expected_type: str
) -> tuple[dict[str, str] | None, str | None]:
    transports = [item for item in nearby if item.get("kind") == "transport"]
    if expected_type == "":
        if not transports:
            return None, "no_vehicle"
        return transports[0], None
    matches = [item for item in transports if item.get("type") == expected_type]
    if not matches:
        return None, "no_vehicle"
    if len(matches) != 1:
        return None, "bad_args"
    return matches[0], None


def reject_seat_before_side_effect(seat: int, crew_size: int) -> str | None:
    if seat < 0 or seat > 63:
        return "bad_args"
    if seat >= crew_size:
        return "bad_args"
    return None


def start_vehicle_indices(seat: int) -> tuple[int, int]:
    return seat, seat


def observed_receipt(
    observed: dict[str, object], named_seats: dict[str, int]
) -> dict[str, object]:
    crew_index = int(observed["crew_index"])
    return {
        "seated": crew_index >= 0,
        "seat": map_vehicle_get_in_seat_token(int(observed["vehicle_seat"]), named_seats),
        "type": observed["type"],
        "classname": observed["classname"],
    }


def assert_get_in_wire_source(source: str) -> None:
    if _telemetry_sha256(source) != TELEMETRY_REGION_SHA256:
        raise AssertionError("DispatchVehicleTelemetry region hash drifted")

    telemetry = _method_body(source, "protected bool DispatchVehicleTelemetry(")
    dispatch = _method_body(source, "protected bool DispatchVehicleGetInClient(")
    prep = _method_body(source, "protected bool ProcessVehicleGetInClientPrep(MCPJob job)")
    result = _get_in_result_block(source)
    probe = _method_body(source, "protected bool ProcessDriveProbeClientPrep(MCPJob job)")
    owned = _method_body(source, "protected CarScript ResolveOwnedCar()")
    select = ""
    mapper = ""
    if "protected Transport SelectVehicleGetInTransport(" in source:
        select = _method_body(source, "protected Transport SelectVehicleGetInTransport(")
    if "protected string VehicleGetInSeatToken(" in source:
        mapper = _method_body(source, "protected string VehicleGetInSeatToken(")
    get_in_span = dispatch + select + prep + mapper + result
    prep = prep + select

    if "DispatchVehicleTelemetry(" in get_in_span:
        raise AssertionError("WIRE flow must not invoke DispatchVehicleTelemetry")
    if "GetSeatAnimationType" in telemetry:
        raise AssertionError("telemetry must not host GetSeatAnimationType")
    if "OnDebugSpawn();" not in prep:
        raise AssertionError("legacy CivilianSedan/0 conditioning missing")
    if "CaptureDriveProbeClientOwnership(job, car);" not in prep:
        raise AssertionError("legacy ownership capture missing")
    if "IsDriveClientVehicleFixtureReady(car)" not in prep:
        raise AssertionError("legacy fixture-ready missing")
    if "GetSeatAnimationType(0)" not in probe:
        raise AssertionError("drive probe seat-0 start must stay untouched")
    if "StartCommand_Vehicle(foundCar, 0, seatAnim)" not in probe:
        raise AssertionError("drive probe StartCommand_Vehicle(0) must stay untouched")
    if "CarScript.Cast(vehicleCommand.GetTransport())" not in owned:
        raise AssertionError("ResolveOwnedCar must stay byte-semantic")

    if "command.args.seat" not in dispatch:
        raise AssertionError("dispatch does not consume MCPArgs.seat")
    if "command.args.seat < 0" not in dispatch and "command.args.seat < 0 || command.args.seat > 63" not in dispatch:
        if not re.search(r"command\.args\.seat < 0", dispatch):
            raise AssertionError("dispatch does not fail-closed on negative seat")
    if "command.args.seat > 63" not in dispatch:
        raise AssertionError("dispatch does not fail-closed on seat > 63")
    if 'result.error = "bad_args"' not in dispatch:
        raise AssertionError("dispatch seat reject must use existing bad_args")
    range_guard = "command.args.seat < 0 || command.args.seat > 63"
    add_job = "m_JobRunner.AddJob(job)"
    range_idx = dispatch.find(range_guard)
    add_idx = dispatch.find(add_job)
    if range_idx < 0:
        raise AssertionError("dispatch omits seat 0..63 range guard")
    if add_idx < 0:
        raise AssertionError("dispatch omits AddJob side effect")
    if range_idx >= add_idx:
        raise AssertionError("seat range guard must precede AddJob side effect")

    prep_only = _method_body(
        source, "protected bool ProcessVehicleGetInClientPrep(MCPJob job)"
    )
    select_call_idx = prep_only.find("SelectVehicleGetInTransport(")
    subject_match = re.search(r"(?m)^[ \t]*job\.subject = foundCar;", prep_only)
    command_idx = prep_only.find("player.GetCommand_Vehicle()")
    if select_call_idx < 0:
        raise AssertionError("prep omits SelectVehicleGetInTransport")
    if not subject_match:
        raise AssertionError("prep omits job.subject = foundCar")
    subject_assign_idx = subject_match.start()
    if command_idx < 0:
        raise AssertionError("prep omits player.GetCommand_Vehicle()")
    if select_call_idx >= command_idx or subject_assign_idx >= command_idx:
        raise AssertionError(
            "already-seated path accepts GetCommand_Vehicle before selection/subject"
        )
    if "observed != job.subject" not in prep_only:
        raise AssertionError("prep omits observed != job.subject")
    if "crewIndex != seatIndex" not in prep_only:
        raise AssertionError("prep omits crewIndex != seatIndex")

    if "job.args.seat" not in prep:
        raise AssertionError("prep does not consume job.args.seat")
    if "job.args.type" not in prep:
        raise AssertionError("prep does not consume job.args.type")
    if "CrewSize()" not in prep:
        raise AssertionError("prep omits CrewSize fail-closed")
    if "seatIndex >= crewSize" not in prep:
        raise AssertionError("prep omits seat>=CrewSize reject")
    if "GetType()" not in prep:
        raise AssertionError("prep omits GetType exact-match filter")
    if "CrewMemberIndex(player)" not in prep:
        raise AssertionError("prep omits CrewMemberIndex correlation")
    if "GetTransport()" not in prep:
        raise AssertionError("prep omits observed GetTransport correlation")

    if "matches == 0" not in prep:
        raise AssertionError("zero-match reject missing")
    if "matches != 1" not in prep:
        raise AssertionError("multiple-match reject missing")

    anim = re.search(r"GetSeatAnimationType\(([^)]+)\)", prep)
    start = re.search(r"StartCommand_Vehicle\([^,]+,\s*([^,]+),", prep)
    if not anim or not start:
        raise AssertionError("prep missing GetSeatAnimationType or StartCommand_Vehicle")
    anim_expr = anim.group(1).strip()
    start_expr = start.group(1).strip()
    if anim_expr != start_expr:
        raise AssertionError("anim and start seat indices differ")
    if anim_expr in {"0", "1"}:
        raise AssertionError("seat index is hardcoded, not job.args.seat")
    if anim_expr != "job.args.seat" and f"{anim_expr} = job.args.seat" not in prep:
        raise AssertionError("seat index is not copied from job.args.seat")

    if 'resultGetIn.seat = "driver"' in result:
        raise AssertionError("receipt hardcodes driver")
    if "GetType()" not in result:
        raise AssertionError("receipt type is not observed GetType")
    if "ClassName()" not in result:
        raise AssertionError("receipt classname is not observed ClassName")
    if "GetVehicleSeat()" not in result:
        raise AssertionError("receipt seat is not observed GetVehicleSeat")
    if "CrewMemberIndex(" not in result:
        raise AssertionError("receipt seated is not observed CrewMemberIndex")
    if "job.args.type" in result or "job.args.seat" in result:
        raise AssertionError("receipt derived from request args")
    if "DayZPlayerConstants.VEHICLESEAT_DRIVER" not in result + prep:
        raise AssertionError("named VEHICLESEAT_DRIVER mapping missing")
    if "VEHICLESEAT_CODRIVER" not in result and "VEHICLESEAT_CODRIVER" not in prep:
        if "VehicleGetInSeatToken" not in source:
            raise AssertionError("named seat mapping missing CODRIVER")
    mapper = ""
    if "protected string VehicleGetInSeatToken(" in source:
        mapper = _method_body(source, "protected string VehicleGetInSeatToken(")
    mapped = result + mapper
    for token in (
        'return "driver"',
        'return "codriver"',
        'return "passenger_left"',
        'return "passenger_right"',
        'return "unknown"',
        "VEHICLESEAT_DRIVER",
        "VEHICLESEAT_CODRIVER",
        "VEHICLESEAT_PASSENGER_L",
        "VEHICLESEAT_PASSENGER_R",
    ):
        if token not in mapped:
            raise AssertionError(f"seat mapper missing {token}")


class TestVehicleTelemetryWireContract(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = CLIENT_BRIDGE.read_text(encoding="utf-8")
        self.messages = MESSAGES.read_text(encoding="utf-8")
        self.named_seats = {
            "VEHICLESEAT_DRIVER": 101,
            "VEHICLESEAT_CODRIVER": 102,
            "VEHICLESEAT_PASSENGER_L": 103,
            "VEHICLESEAT_PASSENGER_R": 104,
        }

    def test_module_exposes_only_wire_contract_class(self) -> None:
        import tests.test_vehicle_telemetry_contract as module

        classes = [
            name
            for name, value in vars(module).items()
            if isinstance(value, type) and name.startswith("Test")
        ]
        self.assertEqual(classes, ["TestVehicleTelemetryWireContract"])
        self.assertFalse(hasattr(module, "TestVehicleTelemetryLiveContract"))

    def test_telemetry_region_is_frozen_before_and_independent_of_wire(self) -> None:
        self.assertEqual(_telemetry_sha256(self.bridge), TELEMETRY_REGION_SHA256)
        telemetry = _method_body(
            self.bridge, "protected bool DispatchVehicleTelemetry("
        )
        self.assertIn("ResolveOwnedCar()", telemetry)
        self.assertNotIn("ProcessVehicleGetInClientPrep", telemetry)
        self.assertNotIn("job.args.seat", telemetry)

    def test_mcp_args_wire_defaults_are_empty_type_and_seat_zero(self) -> None:
        args = _method_body(self.messages, "class MCPArgs")
        ctor = _method_body(self.messages, "void MCPArgs()")
        self.assertIn("string type;", args)
        self.assertIn("int seat;", args)
        self.assertNotIn("type =", ctor)
        self.assertNotIn("seat =", ctor)

    def test_omitted_defaults_select_first_transport_and_seat_zero(self) -> None:
        nearby = [
            {"kind": "prop", "type": "Land_House"},
            {"kind": "transport", "type": "CivilianSedan", "id": "a"},
            {"kind": "transport", "type": "Boat_01_Blue", "id": "b"},
        ]
        chosen, error = select_get_in_transport(nearby, "")
        self.assertIsNone(error)
        assert chosen is not None
        self.assertEqual(chosen["id"], "a")
        self.assertIsNone(reject_seat_before_side_effect(0, 4))
        self.assertEqual(start_vehicle_indices(0), (0, 0))

    def test_explicit_type_requires_exactly_one_gettype_match(self) -> None:
        nearby = [
            {"kind": "transport", "type": "CivilianSedan", "id": "sedan"},
            {"kind": "transport", "type": "Boat_01_Blue", "id": "boat"},
        ]
        chosen, error = select_get_in_transport(nearby, "Boat_01_Blue")
        self.assertIsNone(error)
        assert chosen is not None
        self.assertEqual(chosen["id"], "boat")
        self.assertEqual(
            select_get_in_transport(nearby, "Hatchback_02")[1], "no_vehicle"
        )
        nearby.append({"kind": "transport", "type": "Boat_01_Blue", "id": "boat2"})
        self.assertEqual(select_get_in_transport(nearby, "Boat_01_Blue")[1], "bad_args")

    def test_seat_fail_closed_and_anim_start_share_index(self) -> None:
        self.assertEqual(reject_seat_before_side_effect(-1, 4), "bad_args")
        self.assertEqual(reject_seat_before_side_effect(64, 4), "bad_args")
        self.assertEqual(reject_seat_before_side_effect(4, 4), "bad_args")
        self.assertIsNone(reject_seat_before_side_effect(3, 4))
        self.assertEqual(start_vehicle_indices(3), (3, 3))

    def test_receipt_uses_observed_state_and_unknown_sentinel(self) -> None:
        observed = {
            "crew_index": 1,
            "vehicle_seat": self.named_seats["VEHICLESEAT_CODRIVER"],
            "type": "CivilianSedan",
            "classname": "CivilianSedan",
        }
        request = {"seat": 0, "type": "Hatchback_02"}
        receipt = observed_receipt(observed, self.named_seats)
        self.assertEqual(
            receipt,
            {
                "seated": True,
                "seat": "codriver",
                "type": "CivilianSedan",
                "classname": "CivilianSedan",
            },
        )
        self.assertNotEqual(receipt["type"], request["type"])
        self.assertNotEqual(receipt["seat"], "driver")
        self.assertEqual(
            map_vehicle_get_in_seat_token(0, self.named_seats), "unknown"
        )

    def test_non_carscript_and_non_driver_do_not_require_drive_conditioning(self) -> None:
        prep = _method_body(
            self.bridge, "protected bool ProcessVehicleGetInClientPrep(MCPJob job)"
        )
        start = prep.index("StartCommand_Vehicle(")
        cast = prep.index("CarScript.Cast(")
        self.assertLess(start, cast)
        self.assertIn("seatIndex == 0 && car", prep)

    def test_source_transports_seat_and_type_through_selection_start_and_receipt(self) -> None:
        assert_get_in_wire_source(self.bridge)

    def test_discriminating_mutants_fail_the_wire_contract(self) -> None:
        assert_get_in_wire_source(self.bridge)
        mutants = {
            "force_seat_zero": (
                "GetSeatAnimationType(seatIndex)",
                "GetSeatAnimationType(0)",
            ),
            "split_anim_start": (
                "StartCommand_Vehicle(foundCar, seatIndex, seatAnim)",
                "StartCommand_Vehicle(foundCar, 0, seatAnim)",
            ),
            "drop_type_filter": ("job.args.type", "/* type omitted */"),
            "drop_crew_bound": ("if (seatIndex >= crewSize)", "if (false)"),
            "drop_crew_size": ("CrewSize()", "/* CrewSize omitted */"),
            "hardcode_driver": (
                'resultGetIn.seat = VehicleGetInSeatToken(getInCommand.GetVehicleSeat());',
                'resultGetIn.seat = "driver";',
            ),
            "receipt_from_request": (
                "resultGetIn.type = getInTransport.GetType();",
                "resultGetIn.type = job.args.type;",
            ),
            "invoke_telemetry": (
                "job.kind = \"vehicle_get_in\";",
                "DispatchVehicleTelemetry(command, result);\n\t\tjob.kind = \"vehicle_get_in\";",
            ),
            "accept_any_type": ("vehicle.GetType() == expectedType", "true"),
            "accept_zero_matches": ("if (matches == 0)", "if (false)"),
            "accept_multiple_matches": ("if (matches != 1)", "if (false)"),
            "skip_negative_seat": (
                "if (command.args.seat < 0 || command.args.seat > 63)",
                "if (false)",
            ),
            "drop_subject_assign": (
                "job.subject = foundCar;",
                "/* job.subject = foundCar; */",
            ),
            "drop_observed_subject": (
                "observed != job.subject",
                "false",
            ),
            "drop_crew_index": (
                "if (crewIndex != seatIndex)",
                "if (false)",
            ),
            "already_seated_bypass": (
                "foundCar = SelectVehicleGetInTransport(job, seatPos, expectedType);",
                "if (player.GetCommand_Vehicle()) { /* already seated skip selection */ } else foundCar = SelectVehicleGetInTransport(job, seatPos, expectedType);",
            ),
            "range_guard_after_addjob": (
                "\t\tif (command.args.seat < 0 || command.args.seat > 63)\n"
                "\t\t{\n"
                "\t\t\tresult.ok = false;\n"
                "\t\t\tresult.error = \"bad_args\";\n"
                "\t\t\treturn true;\n"
                "\t\t}\n\n"
                "\t\tif (HasExclusiveJob())\n"
                "\t\t{\n"
                "\t\t\tresult.ok = false;\n"
                "\t\t\tresult.error = \"busy\";\n"
                "\t\t\treturn true;\n"
                "\t\t}\n\n"
                "\t\tMCPJob job = new MCPJob();\n"
                "\t\tjob.id = command.id;\n"
                "\t\tjob.kind = \"vehicle_get_in\";\n"
                "\t\tjob.args = command.args;\n"
                "\t\tjob.phase = DRIVE_CLIENT_PHASE_PREP;\n"
                "\t\tjob.deadline_s = m_JobRunner.GetElapsedS() + DRIVE_CLIENT_TIMEOUT_S;\n"
                "\t\tjob.prep_deadline_s = m_JobRunner.GetElapsedS() + DRIVE_CLIENT_PREP_TIMEOUT_S;\n"
                "\t\tjob.net_strategy = -1;\n"
                "\t\tjob.tick_poll_sent = result.tick_poll_sent;\n"
                "\t\tjob.tick_poll_callback = result.tick_poll_callback;\n"
                "\t\tjob.tick_dispatch = result.tick_dispatch;\n"
                "\t\tm_JobRunner.AddJob(job);",
                "\t\tif (HasExclusiveJob())\n"
                "\t\t{\n"
                "\t\t\tresult.ok = false;\n"
                "\t\t\tresult.error = \"busy\";\n"
                "\t\t\treturn true;\n"
                "\t\t}\n\n"
                "\t\tMCPJob job = new MCPJob();\n"
                "\t\tjob.id = command.id;\n"
                "\t\tjob.kind = \"vehicle_get_in\";\n"
                "\t\tjob.args = command.args;\n"
                "\t\tjob.phase = DRIVE_CLIENT_PHASE_PREP;\n"
                "\t\tjob.deadline_s = m_JobRunner.GetElapsedS() + DRIVE_CLIENT_TIMEOUT_S;\n"
                "\t\tjob.prep_deadline_s = m_JobRunner.GetElapsedS() + DRIVE_CLIENT_PREP_TIMEOUT_S;\n"
                "\t\tjob.net_strategy = -1;\n"
                "\t\tjob.tick_poll_sent = result.tick_poll_sent;\n"
                "\t\tjob.tick_poll_callback = result.tick_poll_callback;\n"
                "\t\tjob.tick_dispatch = result.tick_dispatch;\n"
                "\t\tm_JobRunner.AddJob(job);\n"
                "\t\tif (command.args.seat < 0 || command.args.seat > 63)\n"
                "\t\t{\n"
                "\t\t\tresult.ok = false;\n"
                "\t\t\tresult.error = \"bad_args\";\n"
                "\t\t\treturn true;\n"
                "\t\t}",
            ),
        }
        for name, (old, new) in mutants.items():
            with self.subTest(mutant=name):
                mutant = self.bridge.replace(old, new, 1)
                self.assertNotEqual(mutant, self.bridge, msg=name)
                with self.assertRaises(AssertionError, msg=name):
                    assert_get_in_wire_source(mutant)

        mapper_unknown = self.bridge.replace('return "unknown";', 'return "driver";', 1)
        with self.assertRaises(AssertionError):
            assert_get_in_wire_source(mapper_unknown)


if __name__ == "__main__":
    unittest.main()
