"""fb-20260930-023111-29c1: vehicle_door, one car door through the vanilla server write.

The command repeats the server half of ActionCarDoorsOutside
(actioncardoorsoutside.c:66-118): ForceUpdateLightsStart, SetAnimationPhase on
the target and ForceUpdateLightsEnd later, on a door found through the attached
CarDoor parts the way the action's condition finds it. These tests pin the wire
contract and the source shape the game will compile. They do not launch DayZ;
whether the phase holds is measured in game.
"""
from __future__ import annotations

import json
import re
import typing
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, result_prune, server
from dayz_mcp.server import (
    LEASE_TOOL_LINE,
    ServerConfig,
    ToolError,
    _BRIDGE_COMMAND_TOOLS,
    build_app,
)
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease
from tests._addon_paths import addon_root
from tests.bridge_client_capabilities_helpers import dispatch_census


COMMAND = "vehicle_door"
MODES = ("read", "open", "close")
SCRIPTS = addon_root() / "scripts"
BRIDGE_PATH = SCRIPTS / "5_Mission" / "MCPBridge.c"
MESSAGES_PATH = SCRIPTS / "5_Mission" / "MCPMessages.c"
RESOLVER_PATH = SCRIPTS / "4_World" / "MCP_CarDoor.c"
CENSUS_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "bridge_capabilities_v1.json"
)
DISPATCH_SIGNATURE = "protected bool DispatchVehicleDoor("

WRITE_START = "car.ForceUpdateLightsStart();"
WRITE_PHASE = "car.SetAnimationPhase(command.args.source, targetPhase);"
WRITE_END = (
    "GetGame().GetCallQueue(CALL_CATEGORY_GAMEPLAY).CallLater("
    "car.ForceUpdateLightsEnd, VEHICLE_DOOR_LIGHTS_PULSE_MS, false);"
)

# The resolver's two loops, compared whitespace-squashed. Exact blocks, not
# loose tokens, so the mutants below die (#156 R1 F4): a cap bound as <=, a scan
# that keeps going after a match, an inverted source test, a list that is not
# cleared before the native call, a match that is not equality.
COMPONENT_SOURCE_LOOP = """
names.Clear();
door.GetActionComponentNameList(component, names);
while (nameScan < names.Count())
{
    componentSource = car.GetAnimSourceFromSelection(names.Get(nameScan));
    if (componentSource != "")
    {
        selection = names.Get(nameScan);
        return componentSource;
    }
    nameScan = nameScan + 1;
}
return "";
"""
RESOLVE_SCAN = """
while (attachmentScan < inventory.AttachmentCount())
{
    candidate = CarDoor.Cast(inventory.GetAttachmentFromIndex(attachmentScan));
    if (candidate)
    {
        componentScan = 0;
        while (componentScan < COMPONENT_SCAN_CAP)
        {
            if (ComponentSource(car, candidate, componentScan, names, candidateSelection) == wanted)
            {
                door = candidate;
                component = componentScan;
                selection = candidateSelection;
                return true;
            }
            componentScan = componentScan + 1;
        }
    }
    attachmentScan = attachmentScan + 1;
}
return false;
"""

EXPECTED_VEHICLE_DOOR_MEMBERS = [
    ("string", "source"),
    ("string", "mode"),
    ("string", "slot"),
    ("string", "door_type"),
    ("string", "selection"),
    ("int", "component_index"),
    ("float", "phase"),
    ("string", "state"),
    ("bool", "written"),
    ("float", "phase_requested"),
    ("float", "phase_reply"),
    ("string", "state_reply"),
    ("bool", "is_authority_owner"),
    ("float", "tick_time_s"),
    ("ref array<float>", "pos"),
]

# object_anim sentences pinned by tests/test_object_anim.py that the new
# pointer sentence must not disturb.
# fb-20260930-065425-8779 replaced "does not hold on the vehicle" and "cannot
# keep a door open": each object_anim read wrote phase 0.
OBJECT_ANIM_PINNED = (
    "The returned phase is the same-tick re-read",
    "can still read the old value",
    "confirm a write with a later read",
    "A read writes nothing.",
    "A written phase holds",
    "For building doors, read object_doors",
    "an unknown source name also reads 0",
)

_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')
_MEMBER_RE = re.compile(
    r"(?m)(?<![A-Za-z0-9_>])(?P<type>(?:ref\s+)?[A-Za-z_][A-Za-z0-9_]*(?:\s*<[^;{}]+>)?)"
    r"\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*;"
)
# A local declaration of one of the types these functions use.
_LOCAL_DECLARATION = re.compile(
    r"^\s*(?:string|int|float|bool|vector|Object|CarScript|CarDoor|MCPVehicleDoor|"
    r"GameInventory|TStringArray|InventoryLocation)\s+(\w+)\s*(?:=[^;]*)?;\s*$"
)


def _squash(text: str) -> str:
    return " ".join(text.split())


def _block_after(source: str, start: int) -> str:
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated block at offset {start}")


def _method_body(source: str, signature: str) -> str:
    if source.count(signature) != 1:
        raise AssertionError(f"{signature!r} occurs {source.count(signature)} times")
    return _block_after(source, source.index(signature))


def _if_body(source: str, condition: str) -> str:
    needle = f"if ({condition})"
    if needle not in source:
        raise AssertionError(f"missing {needle}")
    return _block_after(source, source.index(needle))


def _without_comments(source: str) -> str:
    def replace(match: re.Match[str]) -> str:
        text = match.group(0)
        return text if text.startswith('"') else re.sub(r"[^\n]", " ", text)

    return _COMMENT_OR_STRING.sub(replace, source)


def _server_capabilities(source: str) -> list[str]:
    declaration = re.search(
        r'protected const string SERVER_CAPABILITIES = ((?:"[^"\n]*"(?: \+ )?)+);',
        source,
    )
    if declaration is None:
        raise AssertionError("SERVER_CAPABILITIES declaration not found")
    return "".join(re.findall(r'"([^"\n]*)"', declaration.group(1))).split(",")


def _class_members(source: str, class_name: str) -> list[tuple[str, str]]:
    clean = _without_comments(source)
    match = re.search(rf"\bclass\s+{re.escape(class_name)}\b", clean)
    if match is None:
        raise AssertionError(f"class {class_name} is absent")
    body = _block_after(clean, match.end())
    return [(m.group("type"), m.group("name")) for m in _MEMBER_RE.finditer(body)]


def _locals_declared_at_top(body: str) -> list[str]:
    """Local names, failing when one follows a statement or is declared twice."""
    names: list[str] = []
    statement_seen = False
    for raw in _without_comments(body).splitlines():
        line = raw.strip()
        if not line or line in ("{", "}"):
            continue
        match = _LOCAL_DECLARATION.match(raw)
        if match is None:
            statement_seen = True
            continue
        if statement_seen:
            raise AssertionError(f"declaration after a statement: {line}")
        if match.group(1) in names:
            raise AssertionError(f"local declared twice: {match.group(1)}")
        names.append(match.group(1))
    return names


def _assert_resolver_loops(source: str) -> None:
    squashed = _squash(source)
    for name, block in (
        ("component source loop", COMPONENT_SOURCE_LOOP),
        ("resolve scan", RESOLVE_SCAN),
    ):
        if squashed.count(_squash(block)) != 1:
            raise AssertionError(f"{name} is not the exact vanilla-shaped block")


def _tool_error_text(exc: BaseException) -> str:
    message = str(exc)
    wrapper = f"Error executing tool {COMMAND}: "
    return message[len(wrapper) :] if message.startswith(wrapper) else message


def _door_missing_wire() -> dict[str, object]:
    return {
        "ok": 0,
        "error": "door_missing",
        "type": "CivilianSedan",
        "object_id": 3,
        "vehicle_door": {
            "source": "DoorsDriver",
            "mode": "open",
            "slot": "CivSedanDriverDoors",
            "door_type": "",
            "selection": "",
            "component_index": -1,
            "phase": 0.0,
            "state": "missing",
            "written": False,
            "phase_requested": -1.0,
            "phase_reply": 0.0,
            "state_reply": "",
            "is_authority_owner": True,
            "tick_time_s": 812.5,
            "pos": [],
        },
    }


class VehicleDoorIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        from tests.fence_helpers import bind_both_peers

        bind_both_peers(self.state)

    def test_server_command_leased_by_name_like_object_anim(self) -> None:
        self.assertIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertNotIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertIn(COMMAND, loopback.WHITELISTED_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "server")
        # mode=read shares the command name, and the lease follows the name.
        self.assertNotIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertTrue(command_requires_lease(COMMAND))

    def test_both_target_shapes_enqueue_with_every_mode(self) -> None:
        for mode in MODES:
            for args in (
                {"object_id": 5, "source": "DoorsDriver", "mode": mode},
                {
                    "type": "CivilianSedan",
                    "pos": [1.0, 2.0, 3.0],
                    "source": "DoorsTrunk",
                    "mode": mode,
                },
            ):
                with self.subTest(args=args):
                    status, body = self.state.enqueue_command(COMMAND, dict(args))
                    self.assertEqual(status, 200)
                    self.assertEqual(body["peer"], "server")
                    self.assertEqual(body["cmd"], COMMAND)

    def test_absent_or_unknown_mode_and_other_bad_args_are_refused(self) -> None:
        by_id = {"object_id": 5, "source": "DoorsDriver", "mode": "open"}
        by_type = {
            "type": "CivilianSedan",
            "pos": [1.0, 2.0, 3.0],
            "source": "DoorsDriver",
            "mode": "open",
        }
        invalid = [
            {},
            {key: value for key, value in by_id.items() if key != "mode"},
            {key: value for key, value in by_type.items() if key != "mode"},
            {**by_id, "mode": "toggle"},
            {**by_id, "mode": ""},
            {**by_id, "mode": 1},
            {**by_id, "mode": True},
            {**by_id, "mode": None},
            {**by_id, "mode": ["open"]},
            {**by_id, "source": ""},
            {key: value for key, value in by_id.items() if key != "source"},
            {**by_id, "source": 7},
            {**by_id, "phase": 1.0},
            {**by_type, "phase": 1.0},
            {**by_id, "object_id": 0},
            {**by_id, "object_id": -1},
            {**by_id, "object_id": True},
            {**by_id, "extra": 1},
            {**by_id, "type": "CivilianSedan", "pos": [1.0, 2.0, 3.0]},
            {key: value for key, value in by_type.items() if key != "pos"},
            {**by_type, "pos": [1.0, 2.0]},
            {**by_type, "type": ""},
        ]
        for args in invalid:
            with self.subTest(args=args):
                status, body = self.state.enqueue_command(COMMAND, args)
                self.assertEqual(status, 400)
                self.assertEqual(body, {"error": "bad_args"})

    def test_tool_ingress_and_bridge_accept_the_same_three_modes(self) -> None:
        self.assertEqual(typing.get_args(server.VehicleDoorMode), MODES)
        body = _method_body(BRIDGE_PATH.read_text(encoding="utf-8"), DISPATCH_SIGNATURE)
        self.assertIn(
            'if (doorMode != "read" && doorMode != "open" && doorMode != "close")',
            body,
        )
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertEqual(
                    loopback.validate_command_args(
                        COMMAND, {"object_id": 1, "source": "DoorsDriver", "mode": mode}
                    ),
                    (True, None),
                )


class VehicleDoorCensusTest(unittest.TestCase):
    def test_capabilities_and_dispatch_name_the_command(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        self.assertIn(COMMAND, _server_capabilities(source))
        names = dispatch_census(source)
        self.assertIn(COMMAND, names)
        self.assertEqual(names[-1], "entities_query")
        dispatch = _method_body(source, "protected void Dispatch(MCPCommand command)")
        branch = _if_body(dispatch, f'command.cmd == "{COMMAND}"')
        self.assertEqual(branch.strip(), "postNow = DispatchVehicleDoor(command, result);")

    def test_daemon_map_and_fixture_map_the_command_to_its_tool(self) -> None:
        self.assertEqual(_BRIDGE_COMMAND_TOOLS["server"].get(COMMAND), COMMAND)
        self.assertNotIn(COMMAND, _BRIDGE_COMMAND_TOOLS["client"])
        fixture = json.loads(CENSUS_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(fixture["peers"]["server"].get(COMMAND), COMMAND)
        self.assertNotIn(COMMAND, fixture["peers"]["client"])

    def test_the_arg_contract_hash_does_not_cover_the_command(self) -> None:
        # The hash covers vehicle_prepare_fixture only; a new command leaves it as is.
        self.assertNotIn(COMMAND, server.SERVER_ARG_CONTRACT)
        self.assertEqual(server.EXPECTED_SERVER_ARG_CONTRACT_HASH, "3c77a99c95fd05a4")
        self.assertIn(
            'SERVER_ARG_CONTRACT_HASH = "3c77a99c95fd05a4"',
            BRIDGE_PATH.read_text(encoding="utf-8"),
        )


class VehicleDoorWriteContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = BRIDGE_PATH.read_text(encoding="utf-8")
        self.body = _method_body(self.bridge, DISPATCH_SIGNATURE)
        self.resolver = RESOLVER_PATH.read_text(encoding="utf-8")

    def test_write_is_the_vanilla_server_half_in_order(self) -> None:
        for token in (WRITE_START, WRITE_PHASE, WRITE_END):
            with self.subTest(token=token):
                self.assertEqual(self.body.count(token), 1)
        self.assertLess(self.body.index(WRITE_START), self.body.index(WRITE_PHASE))
        self.assertLess(self.body.index(WRITE_PHASE), self.body.index(WRITE_END))
        self.assertEqual(self.body.count(".SetAnimationPhase("), 1)
        self.assertIn("protected const int VEHICLE_DOOR_LIGHTS_PULSE_MS = 1000;", self.bridge)

    def test_neither_the_now_variant_nor_a_reset_is_used(self) -> None:
        for label, text in (("DispatchVehicleDoor", self.body), ("MCP_CarDoor.c", self.resolver)):
            for banned in ("SetAnimationPhaseNow(", "ResetAnimationPhase("):
                with self.subTest(where=label, banned=banned):
                    self.assertNotIn(banned, text)
        # The resolver only reads.
        self.assertNotIn("SetAnimationPhase(", self.resolver)
        self.assertNotIn("ForceUpdateLights", self.resolver)

    def test_every_write_sits_inside_the_non_read_block(self) -> None:
        block = _if_body(self.body, 'doorMode != "read"')
        outside = self.body.replace(block, "")
        for token in (
            "ForceUpdateLightsStart(",
            ".SetAnimationPhase(",
            "ForceUpdateLightsEnd",
            "CallLater(",
            "report.written = true;",
            "report.phase_requested = targetPhase;",
        ):
            with self.subTest(token=token):
                self.assertIn(token, block)
                self.assertNotIn(token, outside)
        # open writes 1; close keeps the 0 the local starts with.
        self.assertIn("float targetPhase = 0.0;", self.body)
        self.assertEqual(_if_body(block, 'doorMode == "open"').strip(), "targetPhase = 1.0;")
        self.assertEqual(self.body.count("targetPhase = "), 2)

    def test_phase_and_state_are_read_before_the_write_and_the_replies_after(self) -> None:
        block_at = self.body.index('if (doorMode != "read")')
        block = _if_body(self.body, 'doorMode != "read"')
        self.assertEqual(self.body.count(block), 1)
        block_end = self.body.index(block) + len(block)
        before = (
            "report.phase = car.GetAnimationPhase(command.args.source);",
            "report.state = MCPCarDoorResolver.StateName(car.GetCarDoorsState(report.slot));",
            "report.is_authority_owner = car.IsAuthorityOwner();",
            "report.tick_time_s = GetGame().GetTickTime();",
        )
        after = (
            "report.phase_reply = car.GetAnimationPhase(command.args.source);",
            "report.state_reply = MCPCarDoorResolver.StateName(car.GetCarDoorsState(report.slot));",
        )
        for token in before:
            with self.subTest(token=token):
                self.assertEqual(self.body.count(token), 1)
                self.assertLess(self.body.index(token), block_at)
        for token in after:
            with self.subTest(token=token):
                self.assertEqual(self.body.count(token), 1)
                self.assertGreater(self.body.index(token), block_end)
        self.assertGreater(self.body.rindex("result.ok = true;"), block_end)
        self.assertEqual(self.body.count("result.ok = true;"), 1)

    def test_refusals_are_the_four_codes_in_order_and_door_missing_keeps_the_payload(self) -> None:
        codes = set(re.findall(r'result\.error = "(\w+)"', self.body))
        self.assertEqual(codes, {"bad_args", "not_a_car", "door_missing", "door_not_found"})
        self.assertIn("result.error = error;", self.body)
        order = [
            self.body.index('if (doorMode != "read" && doorMode != "open" && doorMode != "close")'),
            self.body.index("ResolveCommandObject(command.args, error)"),
            self.body.index("car = CarScript.Cast(match);"),
            self.body.index('result.error = "not_a_car";'),
            self.body.index("report.phase = car.GetAnimationPhase(command.args.source);"),
            self.body.index("result.vehicle_door = report;"),
            self.body.index("crewSlot = MCPCarDoorResolver.CrewDoorSlot(car, command.args.source);"),
            self.body.index('result.error = "door_missing";'),
            self.body.index(
                "MCPCarDoorResolver.Resolve(car, command.args.source, carDoor, doorComponent, doorSelection)"
            ),
            self.body.index('result.error = "door_not_found";'),
            self.body.index('if (doorMode != "read")'),
        ]
        self.assertEqual(order, sorted(order))
        missing = _if_body(self.body, 'crewSlot != "" && !car.FindAttachmentBySlotName(crewSlot)')
        for token in (
            "report.slot = crewSlot;",
            'report.state = "missing";',
            "result.ok = false;",
            'result.error = "door_missing";',
            "return true;",
        ):
            with self.subTest(token=token):
                self.assertIn(token, missing)

    def test_the_found_door_fills_type_slot_selection_component_and_pos(self) -> None:
        resolved_at = self.body.index('result.error = "door_not_found";')
        block_at = self.body.index('if (doorMode != "read")')
        for token in (
            "report.door_type = carDoor.GetType();",
            "report.slot = MCPCarDoorResolver.SlotName(carDoor);",
            "report.selection = doorSelection;",
            "report.component_index = doorComponent;",
            "doorWorldPos = carDoor.ModelToWorld(carDoor.GetSelectionPositionMS(doorSelection));",
            "VectorToArray(doorWorldPos, report.pos);",
        ):
            with self.subTest(token=token):
                self.assertEqual(self.body.count(token), 1)
                self.assertGreater(self.body.index(token), resolved_at)
                self.assertLess(self.body.index(token), block_at)

    def test_compile_hazards_locals_on_top_and_no_stored_entity(self) -> None:
        names = _locals_declared_at_top(self.body)
        for name in ("doorMode", "targetPhase", "doorWorldPos", "car", "carDoor", "report"):
            self.assertIn(name, names)
        # No bridge member (m_Name) is read or written: nothing outlives the call.
        self.assertIsNone(re.search(r"\bm_[A-Z]", self.body))
        self.assertIsNone(re.search(r"\blocal\b", _without_comments(self.body)))
        # Static-type casts only, never a comparison of entity handles.
        self.assertIn("car = CarScript.Cast(match);", self.body)
        self.assertIsNone(re.search(r"(==|!=)\s*(match|car|carDoor)\b", self.body))


class VehicleDoorResolverContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = RESOLVER_PATH.read_text(encoding="utf-8")

    def _body(self, signature: str) -> str:
        return _method_body(self.resolver, signature)

    def test_component_scan_mirrors_the_action_condition(self) -> None:
        _assert_resolver_loops(self.resolver)
        self.assertIn("static const int COMPONENT_SCAN_CAP = 64;", self.resolver)
        # GetActionComponentsForSelectionName (object.c:204) is not the vanilla path.
        self.assertNotIn("GetActionComponentsForSelectionName", self.resolver)

    def test_resolver_loop_mutants_are_rejected(self) -> None:
        squashed = _squash(self.resolver)
        mutants = {
            "cap_bound_inclusive": (
                "componentScan < COMPONENT_SCAN_CAP",
                "componentScan <= COMPONENT_SCAN_CAP",
            ),
            "scan_keeps_going_after_a_match": (
                "selection = candidateSelection; return true;",
                "selection = candidateSelection;",
            ),
            "source_test_inverted": ('if (componentSource != "")', 'if (componentSource == "")'),
            "names_not_cleared": ("names.Clear(); ", ""),
            "match_not_equality": ("candidateSelection) == wanted", "candidateSelection) != wanted"),
            "any_attachment_not_a_door": (
                "candidate = CarDoor.Cast(inventory.GetAttachmentFromIndex(attachmentScan));",
                "candidate = inventory.GetAttachmentFromIndex(attachmentScan);",
            ),
        }
        for name, (old, new) in mutants.items():
            with self.subTest(mutant=name):
                self.assertEqual(squashed.count(old), 1, old)
                with self.assertRaises(AssertionError):
                    _assert_resolver_loops(squashed.replace(old, new))

    def test_out_parameters_are_assigned_before_any_return(self) -> None:
        component_source = self._body("static string ComponentSource(")
        resolve = self._body("static bool Resolve(")
        first_return = component_source.index("return")
        self.assertLess(component_source.index('selection = "";'), first_return)
        first_return = resolve.index("return")
        for token in ("door = null;", "component = -1;", 'selection = "";'):
            with self.subTest(token=token):
                self.assertLess(resolve.index(token), first_return)

    def test_slot_crew_slot_and_state_follow_vanilla(self) -> None:
        slot = self._body("static string SlotName(CarDoor door)")
        for token in (
            "doorInventory.GetCurrentInventoryLocation(location)",
            "if (location.GetSlot() == -1)",
            "return InventorySlots.GetSlotName(location.GetSlot());",
        ):
            with self.subTest(token=token):
                self.assertIn(token, slot)
        crew = self._body("static string CrewDoorSlot(CarScript car, string source)")
        self.assertLess(
            crew.index("seat = car.GetSeatIndexFromDoor(source);"), crew.index("if (seat < 0)")
        )
        self.assertIn("return car.GetDoorInvSlotNameFromSeatPos(seat);", crew)
        state = _squash(self._body("static string StateName(int doorState)"))
        self.assertIn(
            _squash('if (doorState == CarDoorState.DOORS_OPEN) { return "open"; }'), state
        )
        self.assertIn(
            _squash('if (doorState == CarDoorState.DOORS_CLOSED) { return "closed"; }'), state
        )
        self.assertTrue(state.endswith('return "missing";'))

    def test_world_file_names_no_mission_class_and_keeps_no_entity(self) -> None:
        mission_classes: set[str] = set()
        for path in (SCRIPTS / "5_Mission").glob("*.c"):
            code = _without_comments(path.read_text(encoding="utf-8"))
            mission_classes |= set(re.findall(r"\bclass\s+(\w+)", code))
        self.assertIn("MCPVehicleDoor", mission_classes)
        code = _without_comments(self.resolver)
        used = sorted(name for name in mission_classes if re.search(rf"\b{name}\b", code))
        self.assertEqual(used, [])
        statics = re.findall(r"(?m)^\tstatic\s+(?:const\s+)?\w+\s+(\w+)\s*(?:=|;)", code)
        self.assertEqual(statics, ["COMPONENT_SCAN_CAP"])
        self.assertIsNone(re.search(r"\blocal\b", code))
        for signature in (
            "static string ComponentSource(",
            "static bool Resolve(",
            "static string SlotName(",
            "static string CrewDoorSlot(",
        ):
            with self.subTest(signature=signature):
                _locals_declared_at_top(self._body(signature))

    def test_local_check_rejects_late_or_repeated_declarations(self) -> None:
        with self.assertRaises(AssertionError):
            _locals_declared_at_top("\t\tint a = 0;\n\t\ta = 1;\n\t\tint b = 0;\n")
        with self.assertRaises(AssertionError):
            _locals_declared_at_top("\t\tint a = 0;\n\t\tstring a = \"\";\n")
        self.assertEqual(_locals_declared_at_top("\t\tint a = 0;\n\n\t\ta = 1;\n"), ["a"])


class VehicleDoorPayloadTest(unittest.TestCase):
    def test_payload_class_has_the_contract_fields_and_no_entity(self) -> None:
        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        members = _class_members(messages, "MCPVehicleDoor")
        self.assertEqual(members, EXPECTED_VEHICLE_DOOR_MEMBERS)
        for member_type, _name in members:
            self.assertNotRegex(member_type, r"\b(CarScript|CarDoor|Object|EntityAI|Entity)\b")
        constructor = _squash(
            _method_body(_without_comments(messages), "void MCPVehicleDoor()")
        )
        self.assertEqual(
            constructor,
            "component_index = -1; phase_requested = -1.0; pos = new array<float>();",
        )
        result = _class_members(messages, "MCPResult")
        self.assertEqual(result.count(("ref MCPVehicleDoor", "vehicle_door")), 1)
        self.assertLess(messages.index("class MCPVehicleDoor"), messages.index("class MCPResult"))

    def test_the_payload_is_prunable_and_its_component_index_is_not_the_owned_scalar(self) -> None:
        self.assertIn(COMMAND, result_prune.PRUNABLE_FIELDS)
        self.assertNotIn(COMMAND, result_prune.OWNED_SCALAR_FIELDS)
        self.assertNotIn((COMMAND, COMMAND), result_prune.SEMANTIC_EMPTY_FIELDS)
        unfilled = result_prune.prune_unfilled_fields(
            "world_spawn", {"ok": 1, "vehicle_door": {}}
        )
        self.assertNotIn(COMMAND, unfilled)
        filled = {"source": "DoorsDriver", "component_index": 0, "phase": 0.0}
        kept = result_prune.prune_unfilled_fields(
            COMMAND,
            {"ok": 1, "vehicle_door": dict(filled), "component_index": 0, "door_index": 0},
        )
        # The nested component 0 is real; the flat one belongs to action_use_door.
        self.assertEqual(kept[COMMAND], filled)
        self.assertNotIn("component_index", kept)
        self.assertNotIn("door_index", kept)


class VehicleDoorToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def _description(self, name: str) -> str:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        return tools[name].description or ""

    async def test_description_states_the_vanilla_write_and_the_follow_up_read(self) -> None:
        description = await self._description(COMMAND)
        self.assertTrue(description.startswith(LEASE_TOOL_LINE), description)
        for fragment in (
            "mode=open or close calls ForceUpdateLightsStart and SetAnimationPhase(source, 1 or 0)",
            "the server half of ActionCarDoorsOutside",
            "ForceUpdateLightsEnd one second later",
            "it does not use SetAnimationPhaseNow, which object_anim uses",
            "mode=read changes nothing",
            "DoorsDriver, DoorsCoDriver, DoorsCargo1, DoorsCargo2, DoorsHood, DoorsTrunk",
            "Target by object_id (world_spawn or inventory_attach.item_object_id) or by classname near pos",
            "door_missing names the empty slot of a crew door",
            "(vehicle_prepare_fixture or inventory_attach fills it)",
            "door_not_found means no attached door part maps to source",
            "phase (GetAnimationPhase) and state (GetCarDoorsState: open above 0.5, "
            "closed, or missing) read before the call",
            "the door part's type, slot and world pos, is_authority_owner and the server tick_time_s",
            "phase_reply is read in the same tick",
            "confirm with mode=read at +1 s or later",
            "It checks neither the player's reach nor an obstructed door",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, description)
        self.assertIsNone(re.search(r"\binstantly\b", description, re.IGNORECASE))
        # "one second" is the pulse constant the bridge schedules.
        pulse = re.search(
            r"VEHICLE_DOOR_LIGHTS_PULSE_MS = (\d+);", BRIDGE_PATH.read_text(encoding="utf-8")
        )
        self.assertIsNotNone(pulse)
        self.assertEqual(int(pulse.group(1)), 1000)

    async def test_description_names_every_error_the_bridge_can_return(self) -> None:
        # #156 R1 F5: read from the Enforce source, so a new refusal there fails
        # here until the description names it.
        description = await self._description(COMMAND)
        bridge = BRIDGE_PATH.read_text(encoding="utf-8")
        codes = set(re.findall(r'result\.error = "(\w+)"', _method_body(bridge, DISPATCH_SIGNATURE)))
        # result.error = error forwards the shared resolver's codes.
        shared = _method_body(bridge, "protected Object ResolveCommandObject(")
        shared += _method_body(bridge, "protected Object FindUniqueObjectNearType(")
        codes |= set(re.findall(r'error = "(\w+)"', shared))
        self.assertEqual(
            codes,
            {
                "bad_args",
                "not_a_car",
                "door_missing",
                "door_not_found",
                "object_id_unknown",
                "object_id_stale",
                "object_not_found",
                "ambiguous_object",
            },
        )
        listed = description[description.index("Errors: ") :]
        for code in sorted(codes):
            with self.subTest(code=code):
                self.assertIn(code, listed)

    async def test_the_tool_sends_exactly_the_wire_args_on_the_server_peer(self) -> None:
        with patch.object(
            self.runtime,
            "call_bridge",
            new=AsyncMock(return_value={"ok": 1, "vehicle_door": {"source": "DoorsDriver"}}),
        ) as call:
            await self.app.call_tool(
                COMMAND,
                {"object_id": 7, "source": "DoorsDriver", "mode": "open", "timeout_s": 1.0},
            )
            call.assert_awaited_once_with(
                COMMAND,
                {"source": "DoorsDriver", "mode": "open", "object_id": 7},
                "server",
                1.0,
            )
            call.reset_mock()
            await self.app.call_tool(
                COMMAND,
                {
                    "type": "CivilianSedan",
                    "pos": [1.0, 2.0, 3.0],
                    "source": "DoorsTrunk",
                    "mode": "read",
                    "timeout_s": 1.0,
                },
            )
            call.assert_awaited_once_with(
                COMMAND,
                {
                    "source": "DoorsTrunk",
                    "mode": "read",
                    "type": "CivilianSedan",
                    "pos": [1.0, 2.0, 3.0],
                },
                "server",
                1.0,
            )
            self.assertEqual(
                loopback.validate_command_args(COMMAND, call.await_args.args[1]),
                (True, None),
            )

    async def test_bad_arguments_raise_before_the_bridge(self) -> None:
        cases = (
            {"object_id": 7, "source": "DoorsDriver"},
            {"object_id": 7, "source": "DoorsDriver", "mode": "toggle"},
            {"object_id": 7, "source": "DoorsDriver", "mode": 1},
            {"object_id": 7, "source": "", "mode": "read"},
            {"object_id": 7, "source": 5, "mode": "read"},
            {"source": "DoorsDriver", "mode": "read"},
            {"type": "CivilianSedan", "source": "DoorsDriver", "mode": "read"},
            {"object_id": True, "source": "DoorsDriver", "mode": "read"},
            {"object_id": -1, "source": "DoorsDriver", "mode": "read"},
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                call = AsyncMock(return_value={"ok": 1})
                with patch.object(self.runtime, "call_bridge", new=call):
                    with self.assertRaises(ToolError):
                        await self.app.call_tool(COMMAND, {**arguments, "timeout_s": 1.0})
                call.assert_not_awaited()
        call = AsyncMock(return_value={"ok": 1})
        with patch.object(self.runtime, "call_bridge", new=call):
            with self.assertRaises(ToolError) as raised:
                await self.app.call_tool(
                    COMMAND, {"object_id": 7, "source": "", "mode": "open", "timeout_s": 1.0}
                )
        self.assertTrue(_tool_error_text(raised.exception).startswith("bad_args: source"))
        call.assert_not_awaited()

    async def test_object_anim_points_car_doors_at_vehicle_door(self) -> None:
        description = await self._description("object_anim")
        self.assertIn("For a car door, use vehicle_door.", description)
        for sentence in OBJECT_ANIM_PINNED:
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, description)

    async def test_door_missing_reaches_the_caller_with_the_empty_slot(self) -> None:
        # Only the ToolError message crosses the MCP wire, so the slot the
        # bridge filled before refusing rides in it (like fixture_not_ready).
        wire = _door_missing_wire()
        state = SimpleNamespace(take_result=lambda *_args, **_kwargs: json.loads(json.dumps(wire)))
        with patch.object(self.runtime, "loopback", SimpleNamespace(state=state)):
            with self.assertRaises(ToolError) as raised:
                await self.runtime.wait_for_result(COMMAND, 1, "server", 1.0)
        self.assertEqual(
            str(raised.exception), "door_missing; slot=CivSedanDriverDoors state=missing phase=0.0"
        )
        self.assertEqual(raised.exception.object_id, 3)

    def test_door_missing_detail_is_gated_on_verb_and_code_and_leaks_nothing(self) -> None:
        wire = _door_missing_wire()
        self.assertEqual(str(server._bridge_error(dict(wire), "object_anim")), "door_missing")
        self.assertEqual(str(server._bridge_error(dict(wire), None)), "door_missing")
        self.assertEqual(
            str(server._bridge_error({**wire, "error": "door_not_found"}, COMMAND)),
            "door_not_found",
        )
        report = dict(wire["vehicle_door"])  # type: ignore[arg-type]
        report.update(slot="C:\\Users\\secret path", source="caller-text-7f3a")
        message = str(server._bridge_error({**wire, "vehicle_door": report}, COMMAND))
        self.assertEqual(message, "door_missing; state=missing phase=0.0")
        self.assertNotIn("caller-text-7f3a", message)
        self.assertEqual(
            str(server._bridge_error({"ok": 0, "error": "door_missing"}, COMMAND)),
            "door_missing",
        )
        report.update(slot="CivSedanCargo1Doors", phase=True)
        self.assertEqual(
            str(server._bridge_error({**wire, "vehicle_door": report}, COMMAND)),
            "door_missing; slot=CivSedanCargo1Doors state=missing",
        )


if __name__ == "__main__":
    unittest.main()
