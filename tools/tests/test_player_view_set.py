"""Inbox fb-20261009-104228-854c: player_view_set, the local player's first or third person view.

The client bridge sets the view with DayZPlayerImplement.SetIsInThirdPerson,
waits camera_set's default settle and reads IsInThirdPerson and the class of
the player's GetCurrentCamera back. These tests pin the wire contract, the
census, the older-PBO admission, the prune and error contracts and the Enforce
wiring. Text asserts on Enforce are wiring only: they do not credit in-game
behavior, which the final round checks.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, result_prune, server
from dayz_mcp.result_prune import PRUNABLE_FIELDS, prune_unfilled_fields
from dayz_mcp.server import (
    DEFAULT_TOOL_TIMEOUT_S,
    LEASE_TOOL_LINE,
    ServerConfig,
    ToolError,
    _BRIDGE_COMMAND_TOOLS,
    build_app,
)
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease
from tests._addon_paths import addon_root
from tests.bridge_client_capabilities_helpers import announced_caps, dispatch_census
from tests.fence_helpers import (
    INST_CLIENT,
    PID_CLIENT,
    bind_both_peers,
    bound_queue,
    poll_census_query,
)


COMMAND = "player_view_set"
FIELDS = ("person_view", "camera_class")
SCRIPTS = addon_root() / "scripts"
CLIENT_PATH = SCRIPTS / "5_Mission" / "MCPClientBridge.c"
SERVER_BRIDGE_PATH = SCRIPTS / "5_Mission" / "MCPBridge.c"
MESSAGES_PATH = SCRIPTS / "5_Mission" / "MCPMessages.c"
CENSUS_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bridge_capabilities_v1.json"

DISPATCH_CHAIN = "protected void Dispatch(MCPCommand command)"
DISPATCH = "protected bool DispatchPlayerViewSet(MCPCommand command, MCPResult result)"
PROCESS = "protected bool ProcessPlayerViewSetJob(MCPJob job)"
POST = "protected void PostPlayerViewSetJob(MCPJob job)"
VERB_METHODS = (DISPATCH, PROCESS, POST)
PROCESS_HOOK = "override bool MCP_ProcessJob(MCPJob job)"
SUCCESS_HOOK = "override void MCP_PostJobSuccess(MCPJob job)"
EXCLUSIVE = "protected bool HasExclusiveJob()"
CAMERA_SET = "protected bool DispatchCameraSet(MCPCommand command, MCPResult result)"
BRIDGE_CODES = {"bad_args", "no_player", "script_camera", "busy", "view_not_applied"}
BAD_VIEWS = ("3rd", "", "Third", "FIRST", "toggle", 3, 0, True, None, ["third"], {"view": "third"})

_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')
_LOCAL_TYPES = ("string", "int", "float", "bool", "PlayerBase", "MCPJob", "MCPResult", "DayZPlayerCamera")
_LOCAL_DECLARATION = re.compile(r"^\s*(?:" + "|".join(_LOCAL_TYPES) + r")\s+(\w+)\s*;\s*$")


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


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
    if source.count(needle) != 1:
        raise AssertionError(f"{needle!r} occurs {source.count(needle)} times")
    return _block_after(source, source.index(needle))


def _class_body(source: str, name: str) -> str:
    match = re.search(rf"^class {re.escape(name)}\b", source, re.M)
    if match is None:
        raise AssertionError(f"class {name} is absent")
    return _block_after(source, match.end())


def _without_comments(source: str) -> str:
    def replace(match: re.Match[str]) -> str:
        text = match.group(0)
        return text if text.startswith('"') else re.sub(r"[^\n]", " ", text)

    return _COMMENT_OR_STRING.sub(replace, source)


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


def _in_order(test: unittest.TestCase, body: str, *tokens: str) -> None:
    positions = []
    for token in tokens:
        test.assertIn(token, body)
        positions.append(body.index(token))
    test.assertEqual(positions, sorted(positions), tokens)


def _bridge_codes() -> set[str]:
    """Every error code the client bridge's three methods for this verb can answer."""
    client = _source(CLIENT_PATH)
    codes: set[str] = set()
    for signature in VERB_METHODS:
        codes |= set(re.findall(r'result\.error = "(\w+)";', _method_body(client, signature)))
    return codes


def _tool_error_text(exc: BaseException) -> str:
    message = str(exc)
    wrapper = f"Error executing tool {COMMAND}: "
    return message[len(wrapper) :] if message.startswith(wrapper) else message


class PlayerViewSetIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        bind_both_peers(self.state)

    def test_a_client_command_that_needs_a_lease(self) -> None:
        self.assertIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertNotIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertNotIn(COMMAND, loopback.EXEC_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "client")
        self.assertNotIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertTrue(command_requires_lease(COMMAND))
        # One call sets the view and nothing stays held: no cleanup command.
        self.assertNotIn(COMMAND, {source for source, _release, _build in loopback.RELEASE_REGISTRY})

    def test_one_variant_with_view_only(self) -> None:
        variants, delegated = loopback._COMMAND_ARG_SCHEMAS[COMMAND]
        self.assertIsNone(delegated)
        self.assertEqual(len(variants), 1)
        required, optional, validators = variants[0]
        self.assertEqual(required, frozenset({"view"}))
        self.assertEqual(optional, frozenset())
        self.assertEqual(set(validators), {"view"})

    def test_anything_but_first_or_third_is_bad_args_before_an_id(self) -> None:
        refused = [{}, {"view": "third", "extra": None}, {"mode": "third"}]
        refused += [{"view": view} for view in BAD_VIEWS]
        for args in refused:
            with self.subTest(args=args):
                self.assertEqual(
                    self.state.enqueue_command(COMMAND, args), (400, {"error": "bad_args"})
                )
        self.assertEqual(bound_queue(self.state, "client"), [])


class PlayerViewSetAdmissionTest(unittest.TestCase):
    """An older client PBO answers unknown_command: it is never handed the verb."""

    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        bind_both_peers(self.state)
        self.caps = poll_census_query("client")["caps"].split(",")
        self.older = [name for name in self.caps if name != COMMAND]

    def _poll(self, caps: list[str]) -> dict:
        status, body = self.state.record_poll(
            "client",
            "12~1.29",
            instance=INST_CLIENT,
            source_pid=PID_CLIENT,
            caps=",".join(caps),
        )
        self.assertEqual(status, 200, body)
        return body

    def test_checked_on_the_client_census_not_the_server_one(self) -> None:
        self.assertIn(COMMAND, loopback._CLIENT_ANNOUNCED_COMMANDS)
        # The server census carries the arg-contract hash; a client census has none.
        self.assertNotIn(COMMAND, loopback._CAPABILITY_ADMISSION_COMMANDS)
        self.assertIn(COMMAND, self.caps)

    def test_a_client_that_never_announced_is_refused_before_an_id(self) -> None:
        self.assertEqual(
            self.state.enqueue_command(COMMAND, {"view": "third"}),
            (409, {"error": "bridge_capability_missing"}),
        )
        self.assertEqual(bound_queue(self.state, "client"), [])

    def test_a_census_without_the_verb_is_refused_before_an_id(self) -> None:
        self._poll(self.older)
        self.assertEqual(
            self.state.enqueue_command(COMMAND, {"view": "first"}),
            (409, {"error": "bridge_capability_missing"}),
        )
        self.assertEqual(bound_queue(self.state, "client"), [])

    def test_the_announcing_client_is_handed_the_verb(self) -> None:
        self._poll(self.caps)
        status, body = self.state.enqueue_command(COMMAND, {"view": "third"})
        self.assertEqual((status, body["peer"], body["cmd"]), (200, "client", COMMAND))
        delivered = self._poll(self.caps)
        self.assertEqual(
            delivered["commands"],
            [{"id": body["id"], "cmd": COMMAND, "args": {"view": "third"}}],
        )

    def test_a_census_that_drops_the_verb_after_enqueue_discards_it_at_delivery(self) -> None:
        self._poll(self.caps)
        status, body = self.state.enqueue_command(COMMAND, {"view": "first"})
        self.assertEqual(status, 200, body)
        delivered = self._poll(self.older)
        self.assertEqual(delivered["commands"], [])
        self.assertEqual(
            self.state.take_result(body["id"]),
            {"id": body["id"], "ok": False, "error": "bridge_capability_missing"},
        )

    def test_action_hold_keeps_hold_not_supported(self) -> None:
        self.assertEqual(
            self.state.enqueue_command(
                "action_hold", {"action": "use", "selector": "world", "hold_timeout_s": 1.0}
            ),
            (409, {"error": "hold_not_supported"}),
        )


class PlayerViewSetCensusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(CLIENT_PATH)

    def test_client_caps_and_dispatch_name_the_command(self) -> None:
        self.assertIn(COMMAND, announced_caps(self.source))
        names = dispatch_census(self.source)
        self.assertIn(COMMAND, names)
        self.assertEqual(names[-1], "ui_dialog")
        dispatch = _method_body(self.source, DISPATCH_CHAIN)
        branch = _if_body(dispatch, f'command.cmd == "{COMMAND}"')
        self.assertEqual(branch.strip(), "postNow = DispatchPlayerViewSet(command, result);")
        _in_order(
            self,
            dispatch,
            'result.error = "client_not_in_game";',
            'command.cmd == "player_look_at_release"',
            f'command.cmd == "{COMMAND}"',
            'command.cmd == "ui_dialog"',
        )

    def test_the_server_bridge_neither_implements_nor_announces_it(self) -> None:
        self.assertNotIn(COMMAND, _source(SERVER_BRIDGE_PATH))
        fixture = json.loads(CENSUS_FIXTURE.read_text(encoding="utf-8"))
        self.assertNotIn(COMMAND, fixture["peers"]["server"])
        self.assertEqual(fixture["peers"]["client"].get(COMMAND), COMMAND)
        self.assertNotIn(COMMAND, _BRIDGE_COMMAND_TOOLS["server"])
        self.assertEqual(_BRIDGE_COMMAND_TOOLS["client"].get(COMMAND), COMMAND)

    def test_the_version_and_the_server_arg_contract_hash_stay(self) -> None:
        # A client command: the hash covers the server's arguments only.
        self.assertNotIn(COMMAND, server.SERVER_ARG_CONTRACT)
        self.assertEqual(server.EXPECTED_SERVER_ARG_CONTRACT_HASH, "4f90284258023d98")
        self.assertEqual(server.server_arg_contract_hash(), "4f90284258023d98")
        self.assertIn('const string MCP_BRIDGE_VERSION = "12";', _source(MESSAGES_PATH))

    def test_a_stale_client_census_names_the_missing_tool(self) -> None:
        registered = frozenset(tool for tool in _BRIDGE_COMMAND_TOOLS["client"].values() if tool)
        announced = sorted(_BRIDGE_COMMAND_TOOLS["client"])
        announced.remove(COMMAND)
        result = server._compare_bridge_capabilities(
            "client",
            {"state": "announced", "reason": "ok", "announced_commands": announced},
            registered,
        )
        self.assertEqual(result["state"], "mismatch")
        self.assertEqual(result["registered_without_announced_command"], [COMMAND])


class PlayerViewSetEnforceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(CLIENT_PATH)
        self.dispatch = _method_body(self.source, DISPATCH)
        self.process = _method_body(self.source, PROCESS)
        self.post = _method_body(self.source, POST)

    def test_every_refusal_comes_before_the_view_changes(self) -> None:
        self.assertIn(
            'command.args.view != "first" && command.args.view != "third"', self.dispatch
        )
        _in_order(
            self,
            self.dispatch,
            'result.error = "bad_args";',
            "player = PlayerBase.Cast(GetGame().GetPlayer());",
            'result.error = "no_player";',
            "if (m_ActiveCam || m_CameraHandoffPending)",
            'result.error = "script_camera";',
            "if (HasExclusiveJob())",
            'result.error = "busy";',
            'third = command.args.view == "third";',
            "player.SetIsInThirdPerson(third);",
            "m_JobRunner.AddJob(job);",
        )
        # The only call to the setter in the whole addon.
        every_script = "".join(_source(path) for path in SCRIPTS.rglob("*.c"))
        self.assertEqual(every_script.count("SetIsInThirdPerson("), 1)

    def test_the_input_system_is_not_touched(self) -> None:
        for name, body in (("dispatch", self.dispatch), ("process", self.process), ("post", self.post)):
            with self.subTest(method=name):
                self.assertNotIn("Input", _without_comments(body))
                self.assertNotIn("UAPersonView", body)

    def test_one_fixed_settle_then_one_read(self) -> None:
        for line in (
            'job.kind = "player_view_set";',
            "job.sample_start_s = m_JobRunner.GetElapsedS();",
            "job.sample_s_target = CAMERA_SETTLE_STEP_S * CAMERA_DEFAULT_SETTLE_TICKS;",
            "job.deadline_s = job.sample_start_s + job.sample_s_target + CAMERA_JOB_TIMEOUT_S;",
        ):
            with self.subTest(line=line):
                self.assertIn(line, self.dispatch)
        self.assertIn("return elapsed >= job.sample_s_target;", self.process)
        self.assertNotIn("GetCurrentCamera", self.process)
        self.assertNotIn("PostResult", self.process)
        process_hook = _method_body(self.source, PROCESS_HOOK)
        branch = _if_body(process_hook, 'job.kind == "player_view_set"')
        self.assertEqual(branch.strip(), "return ProcessPlayerViewSetJob(job);")
        success_hook = _method_body(self.source, SUCCESS_HOOK)
        branch = _if_body(success_hook, 'job.kind == "player_view_set"')
        self.assertEqual(branch.strip(), "PostPlayerViewSetJob(job);")
        self.assertEqual(self.source.count("PostPlayerViewSetJob(job);"), 1)

    def test_camera_set_answers_busy_while_the_settle_runs(self) -> None:
        # The job is not one of the kinds HasExclusiveJob leaves out.
        self.assertNotIn(COMMAND, _method_body(self.source, EXCLUSIVE))
        camera_set = _method_body(self.source, CAMERA_SET)
        _in_order(self, camera_set, "if (HasExclusiveJob())", 'result.error = "busy";')

    def test_the_read_back_is_the_flag_and_the_players_camera_class(self) -> None:
        _in_order(
            self,
            self.post,
            "player = PlayerBase.Cast(GetGame().GetPlayer());",
            'result.error = "no_player";',
            "if (m_ActiveCam || m_CameraHandoffPending)",
            'result.error = "script_camera";',
            'result.person_view = "first";',
            "if (player.IsInThirdPerson())",
            'result.person_view = "third";',
            "playerCamera = player.GetCurrentCamera();",
            "if (playerCamera)",
            "result.camera_class = playerCamera.ClassName();",
            "result.ok = true;",
            "if (!job.args || result.person_view != job.args.view)",
            'result.error = "view_not_applied";',
        )
        # DayZPlayer's camera getter, never the static one of the SUB_BRZ crash.
        for name, body in (("dispatch", self.dispatch), ("process", self.process), ("post", self.post)):
            with self.subTest(method=name):
                self.assertNotIn("Camera.GetCurrentCamera", body)
                self.assertNotIn("IsInterpolationComplete", body)
                self.assertNotIn("GetCurrentFOV", body)
        self.assertEqual(self.post.count("PostResult(result);"), 3)

    def test_the_bridge_answers_these_error_codes(self) -> None:
        self.assertEqual(_bridge_codes(), BRIDGE_CODES)

    def test_compile_hazards_locals_on_top_no_local_keyword_no_ternary(self) -> None:
        every_script = "".join(_source(path) for path in SCRIPTS.rglob("*.c"))
        type_names = set(re.findall(r"\bclass\s+(\w+)", every_script)) | set(_LOCAL_TYPES)
        expected = {
            DISPATCH: ["player", "third", "job"],
            PROCESS: ["elapsed"],
            POST: ["result", "player", "playerCamera"],
        }
        for signature, names in expected.items():
            with self.subTest(method=signature):
                body = _method_body(self.source, signature)
                self.assertEqual(_locals_declared_at_top(body), names)
                self.assertFalse(set(names) & type_names, names)
                code = _without_comments(body)
                self.assertIsNone(re.search(r"\blocal\b", code))
                self.assertNotIn("?", code)
                self.assertNotIn("_now(", code)
        with self.assertRaises(AssertionError):
            _locals_declared_at_top("\t\tint a;\n\t\ta = 1;\n\t\tint b;\n")
        with self.assertRaises(AssertionError):
            _locals_declared_at_top("\t\tint a;\n\t\tint a;\n")

    def test_the_new_lines_are_tab_indented(self) -> None:
        for signature in VERB_METHODS:
            body = _method_body(self.source, signature)
            for line in body.splitlines():
                with self.subTest(method=signature, line=line):
                    self.assertFalse(line.startswith(" "), line)


class PlayerViewSetPayloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.messages = _source(MESSAGES_PATH)

    def test_args_gain_view_and_the_result_two_scalars(self) -> None:
        args = _class_body(self.messages, "MCPArgs")
        self.assertEqual(len(re.findall(r"^\tstring view;$", args, re.M)), 1)
        result = _class_body(self.messages, "MCPResult")
        for field in FIELDS:
            with self.subTest(field=field):
                self.assertEqual(len(re.findall(rf"^\tstring {field};$", result, re.M)), 1)
                self.assertEqual(len(re.findall(rf"\b{field}\b", _without_comments(self.messages))), 1)
        # MCPCamera already has a view member, so the read-back uses new names.
        camera = _class_body(self.messages, "MCPCamera")
        self.assertRegex(camera, r"(?m)^\tstring view;$")
        self.assertNotRegex(_without_comments(result), r"\bview\b")

    def test_the_scalars_are_owned_by_the_verb_and_kept_even_empty(self) -> None:
        for field in FIELDS:
            with self.subTest(field=field):
                self.assertEqual(result_prune.OWNED_SCALAR_FIELDS[field], frozenset({COMMAND}))
                self.assertNotIn(field, PRUNABLE_FIELDS)
        wire = {"id": 3, "ok": 0, "error": "view_not_applied", "person_view": "first", "camera_class": ""}
        kept = prune_unfilled_fields(COMMAND, wire)
        self.assertEqual((kept["person_view"], kept["camera_class"]), ("first", ""))
        for other in ("camera_set", "camera_get", "player_look_at", "restore_gameplay"):
            with self.subTest(command=other):
                dropped = prune_unfilled_fields(other, dict(wire, person_view="", camera_class=""))
                for field in FIELDS:
                    self.assertNotIn(field, dropped)


class PlayerViewSetErrorDetailTest(unittest.TestCase):
    def test_view_not_applied_carries_the_read_back(self) -> None:
        wire = {
            "ok": 0,
            "error": "view_not_applied",
            "person_view": "first",
            "camera_class": "DayZPlayerCamera1stPerson",
        }
        self.assertEqual(
            str(server._bridge_error(wire, COMMAND)),
            "view_not_applied; observed=person_view=first camera_class=DayZPlayerCamera1stPerson",
        )
        # A null camera reads back as "", which is left out.
        self.assertEqual(
            str(server._bridge_error(dict(wire, camera_class=""), COMMAND)),
            "view_not_applied; observed=person_view=first",
        )

    def test_detail_is_gated_on_verb_and_code_and_leaks_nothing(self) -> None:
        wire = {"ok": 0, "person_view": "third", "camera_class": "DayZPlayerCamera3rdPersonErc"}
        for code in ("script_camera", "no_player", "busy", "bad_args", "timeout"):
            with self.subTest(code=code):
                self.assertEqual(str(server._bridge_error(dict(wire, error=code), COMMAND)), code)
        self.assertEqual(
            str(server._bridge_error(dict(wire, error="view_not_applied"), "camera_set")),
            "view_not_applied",
        )
        hostile = (
            ({"person_view": "sideways", "camera_class": ""}, "view_not_applied"),
            ({"person_view": 1, "camera_class": None}, "view_not_applied"),
            ({"person_view": "first", "camera_class": "Bad Class;x=1"}, "view_not_applied; observed=person_view=first"),
            ({"person_view": "first", "camera_class": "A" * 65}, "view_not_applied; observed=person_view=first"),
            ({"person_view": "first", "camera_class": 7}, "view_not_applied; observed=person_view=first"),
            ({"person_view": "x", "camera_class": "DayZPlayerCamera1stPerson"}, "view_not_applied; observed=camera_class=DayZPlayerCamera1stPerson"),
        )
        for fields, expected in hostile:
            with self.subTest(fields=fields):
                result = {"ok": 0, "error": "view_not_applied", **fields}
                self.assertEqual(str(server._bridge_error(result, COMMAND)), expected)


class PlayerViewSetToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def _tool(self):
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        return tools[COMMAND]

    async def test_description_states_the_contract_and_its_limits(self) -> None:
        description = (await self._tool()).description or ""
        self.assertTrue(description.startswith(LEASE_TOOL_LINE), description)
        for fragment in (
            "Set the local player's view to first or third person",
            "SetIsInThirdPerson",
            "person_view is third when IsInThirdPerson, else first",
            "camera_class is the class of the player's current camera (GetCurrentCamera)",
            "It sets the client's view only: the flag is not networked, so the server's copy "
            "of the player is not changed.",
            "The player's view key still toggles it as before.",
            "With a raised weapon aimed down the sights in third person the camera is still "
            "first person, and camera_class shows it.",
            "view_not_applied with observed= person_view and camera_class",
            "Refused with script_camera while camera_set's camera is mounted",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, description)
        # bad_args never reaches the bridge from the tool: the view is a literal.
        for code in sorted(_bridge_codes() - {"bad_args"}):
            with self.subTest(code=code):
                self.assertIn(code, description)

    async def test_the_schema_is_view_and_the_timeout(self) -> None:
        schema = (await self._tool()).inputSchema
        self.assertEqual(set(schema["properties"]), {"view", "timeout_s"})
        self.assertEqual(schema["properties"]["view"]["enum"], ["first", "third"])
        self.assertEqual(schema["required"], ["view"])

    async def test_the_tool_sends_its_view_on_the_client(self) -> None:
        for view in ("first", "third"):
            with self.subTest(view=view):
                call = AsyncMock(return_value={"ok": 1, "person_view": view, "camera_class": "C"})
                with patch.object(self.runtime, "call_bridge", new=call):
                    await self.app.call_tool(COMMAND, {"view": view, "timeout_s": 2.0})
                call.assert_awaited_once_with(COMMAND, {"view": view}, "client", 2.0)
                self.assertEqual(loopback.validate_command_args(COMMAND, {"view": view}), (True, None))
        call = AsyncMock(return_value={"ok": 1})
        with patch.object(self.runtime, "call_bridge", new=call):
            await self.app.call_tool(COMMAND, {"view": "third"})
        self.assertEqual(call.await_args.args[3], DEFAULT_TOOL_TIMEOUT_S)

    async def test_bad_views_raise_before_the_bridge(self) -> None:
        await self._tool()
        for arguments in [{}] + [{"view": view} for view in BAD_VIEWS]:
            with self.subTest(arguments=arguments):
                call = AsyncMock(return_value={"ok": 1})
                with patch.object(self.runtime, "call_bridge", new=call):
                    with self.assertRaises(ToolError) as raised:
                        await self.app.call_tool(COMMAND, arguments)
                self.assertIn("view", _tool_error_text(raised.exception))
                call.assert_not_awaited()

    async def test_an_older_client_is_refused_before_an_id(self) -> None:
        state = loopback.ServerState("test-key")
        bind_both_peers(state)
        self.runtime.loopback = SimpleNamespace(state=state)
        with self.assertRaises(ToolError) as raised:
            await self.app.call_tool(COMMAND, {"view": "third", "timeout_s": 1.0})
        self.assertEqual(_tool_error_text(raised.exception), "bridge_capability_missing")
        self.assertEqual(bound_queue(state, "client"), [])


if __name__ == "__main__":
    unittest.main()
