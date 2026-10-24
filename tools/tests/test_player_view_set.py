"""Inbox fb-20261009-104228-854c: player_view_set, the local player's first or third person view.

The client bridge sets the view with DayZPlayerImplement.SetIsInThirdPerson,
waits camera_set's default settle and reads IsInThirdPerson and the class of
the player's GetCurrentCamera back. These tests pin the wire contract, the
census, the older-PBO admission, the prune and error contracts and the Enforce
wiring. The client's Dispatch, its readiness gate, HasExclusiveJob, the verb's
three methods, the job runner hooks and MCPJobRunner itself also run here,
translated from the .c sources by tests/enforce_subset_helpers.py and played
against fake engine objects. That is not a compiler and not the game: the
final round checks both.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dayz_mcp import bridge_errors, loopback, result_prune, server
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
from tests.enforce_subset_helpers import EnforceString, method_body, translate
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
RUNNER_PATH = SCRIPTS / "5_Mission" / "MCPJobRunner.c"

DISPATCH_CHAIN = "protected void Dispatch(MCPCommand command)"
DISPATCH = "protected bool DispatchPlayerViewSet(MCPCommand command, MCPResult result)"
PROCESS = "protected bool ProcessPlayerViewSetJob(MCPJob job)"
POST = "protected void PostPlayerViewSetJob(MCPJob job)"
VERB_METHODS = (DISPATCH, PROCESS, POST)
PROCESS_HOOK = "override bool MCP_ProcessJob(MCPJob job)"
SUCCESS_HOOK = "override void MCP_PostJobSuccess(MCPJob job)"
EXCLUSIVE = "protected bool HasExclusiveJob()"
CAMERA_SET = "protected bool DispatchCameraSet(MCPCommand command, MCPResult result)"
IN_GAME = "protected bool IsClientInGame()"
OWNER_HOOKS = (
    "override bool MCP_ProcessJob(MCPJob job)",
    "override bool MCP_IsJobReady(MCPJob job)",
    "override void MCP_PostJobSuccess(MCPJob job)",
    "override void MCP_PostJobFailure(MCPJob job)",
    "override void MCP_PostJobTimeout(MCPJob job)",
    "override void MCP_ClearJobRefs(MCPJob job)",
)
RUNNER_METHODS = (
    "float GetElapsedS()",
    "void AddJob(MCPJob job)",
    "int CountExcluding(string kind)",
    "int CountOfKind(string kind)",
    "void Tick(float timeslice, MCPJobRunnerOwner owner)",
    "protected void ProcessJobs(MCPJobRunnerOwner owner)",
)
# The job kinds HasExclusiveJob leaves out; the tool description names them.
NON_EXCLUSIVE_KINDS = ("ui_dialog", "weapon_action", "input_trigger", "player_move")
LOCAL_CLASS_TYPES = frozenset(
    {
        "DayZPlayerCamera", "HumanCommandVehicle", "MCPCommand", "MCPJob",
        "MCPJobRunnerOwner", "MCPResult", "PlayerBase", "Transport",
    }
)
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


def _mutate(source: str, owner: str, old: str, new: str) -> str:
    """`source` with `old` replaced by `new` inside the block after `owner` only."""
    if source.count(owner) != 1:
        raise AssertionError(f"{owner!r} occurs {source.count(owner)} times")
    start = source.index(owner)
    body = _block_after(source, start)
    at = source.index(body, start)
    if body.count(old) != 1:
        raise AssertionError(f"{old!r} occurs {body.count(old)} times after {owner!r}")
    return source[:at] + body.replace(old, new) + source[at + len(body) :]


# Codex r1's surviving mutants of 6e36673, kept as red controls.
E2_BUSY_GOES_ON = (DISPATCH, 'result.error = "busy";\n\t\t\treturn true;', 'result.error = "busy";')
E3_NO_WAIT = (
    PROCESS,
    "elapsed = m_JobRunner.GetElapsedS() - job.sample_start_s;",
    "elapsed = job.sample_s_target;",
)
E4_MISMATCH_OK = (
    POST,
    '\t\t\tresult.ok = false;\n\t\t\tresult.error = "view_not_applied";',
    '\t\t\tresult.error = "view_not_applied";',
)
M2_SECOND_VIEW = ("class MCPArgs", "\tstring view;\n", "\tstring view;\n\tint view;\n")
B2_NO_UNDERSCORE = re.compile(r"[A-Za-z0-9]{1,64}")

_MEMBER_NAME = re.compile(r"(?<![\w.])(m_[A-Za-z0-9_]+)\b")
_NEW_CALL = re.compile(r"\bnew\s+([A-Za-z_]\w*)\s*\(")
_ENFORCE_LITERAL = re.compile(r'"(?:\\.|[^"\\])*"')
_MESSAGE_MEMBER = re.compile(
    r"(?<![\w<>])(?P<type>(?:ref\s+)?[A-Za-z_]\w*(?:\s*<[^;{}]+>)?)\s+(?P<name>[A-Za-z_]\w*)\s*;"
)
_ENFORCE_DEFAULTS: dict[str, object] = {"int": 0, "float": 0.0, "bool": False}


def _free_method(source: str, signature: str) -> str:
    """`signature`'s method as a free function for the Enforce subset: members
    read and written through `this`, and `new T()` as a call of T."""

    def rewrite(code: str) -> str:
        return _NEW_CALL.sub(r"\1(", _MEMBER_NAME.sub(r"this.\1", code))

    body = method_body(source, signature)
    pieces: list[str] = []
    last = 0
    for match in _ENFORCE_LITERAL.finditer(body):
        pieces += [rewrite(body[last : match.start()]), match.group(0)]
        last = match.end()
    pieces.append(rewrite(body[last:]))
    return signature + "\n{" + "".join(pieces) + "}\n"


def _top_level(class_body: str) -> str:
    """A class body without the bodies of its methods."""
    kept: list[str] = []
    depth = 0
    for char in class_body:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif depth == 0:
            kept.append(char)
    return "".join(kept)


def _message_members(messages: str, name: str) -> list[tuple[str, str]]:
    """(type, name) of each member `class name` declares, whatever its indentation."""
    body = _top_level(_class_body(_without_comments(messages), name))
    return [(match.group("type"), match.group("name")) for match in _MESSAGE_MEMBER.finditer(body)]


def _message_class(messages: str, name: str) -> type:
    """A Python stand-in for `class name`: only its declared members, at Enforce's defaults."""
    members = _message_members(messages, name)
    names = [member for _type, member in members]
    twice = sorted({member for member in names if names.count(member) > 1})
    if twice:
        raise AssertionError(f"{name} declares {twice} more than once")

    def __init__(self: object) -> None:
        for type_name, member in members:
            value = EnforceString(b"") if type_name == "string" else _ENFORCE_DEFAULTS.get(type_name)
            setattr(self, member, value)

    return type(name, (), {"__slots__": tuple(names), "__init__": __init__})


class _Camera:
    def __init__(self, name: str) -> None:
        self.name = name

    def ClassName(self) -> EnforceString:
        return EnforceString.of(self.name)


class _Man:
    """A local player that is not a PlayerBase."""


class _Player(_Man):
    """The local PlayerBase: the view flag, its setter and the camera the engine picks."""

    def __init__(self) -> None:
        self.third = False
        self.set_calls: list[bool] = []
        # False: the server disables third person and HandleView clears the flag.
        self.third_allowed = True
        # "auto": the camera the flag selects on foot; None: no camera; else that class.
        self.camera: str | None = "auto"

    def SetIsInThirdPerson(self, state: object) -> None:
        if type(state) is not bool:
            raise AssertionError(f"SetIsInThirdPerson({state!r})")
        self.set_calls.append(state)
        self.third = state

    def IsInThirdPerson(self) -> bool:
        if not self.third_allowed:
            self.third = False
        return self.third

    def GetCurrentCamera(self) -> _Camera | None:
        if self.camera is None:
            return None
        if self.camera == "auto":
            third = self.IsInThirdPerson()
            return _Camera("DayZPlayerCamera3rdPersonErc" if third else "DayZPlayerCamera1stPerson")
        return _Camera(self.camera)


class _JobMap:
    """MCPJobRunner's map<int, ref MCPJob>, in insertion order."""

    def __init__(self) -> None:
        self.items: list[tuple[int, object]] = []

    def Insert(self, key: int, job: object) -> None:
        self.items.append((key, job))

    def Count(self) -> int:
        return len(self.items)

    def GetKey(self, index: int) -> int:
        return self.items[index][0]

    def GetElement(self, index: int) -> object:
        return self.items[index][1]

    def Remove(self, key: int) -> None:
        self.items = [item for item in self.items if item[0] != key]


class _ClientModel:
    """The client bridge's Dispatch, IsClientInGame, HasExclusiveJob, this verb's
    three methods and the six runner hooks, with MCPJobRunner's own methods,
    translated from the .c sources and run on fake engine objects. Only the
    engine (player, camera, game) and PostResult are fakes; the message
    classes carry exactly the members MCPMessages.c declares."""

    def __init__(self, client: str | None = None, runner: str | None = None, messages: str | None = None) -> None:
        client = _source(CLIENT_PATH) if client is None else client
        runner = _source(RUNNER_PATH) if runner is None else runner
        messages = _source(MESSAGES_PATH) if messages is None else messages
        self.classes = {
            name: _message_class(messages, name) for name in ("MCPArgs", "MCPCommand", "MCPResult", "MCPJob")
        }
        self.constants: dict[str, float | int] = {
            name: float(value) if kind == "float" else int(value)
            for kind, name, value in re.findall(
                r"protected const (float|int) (CAMERA_\w+) = ([0-9.]+);", client
            )
        }
        self.player: object | None = _Player()
        self.posted: list[object] = []
        self.handled: list[str] = []
        runner_state = SimpleNamespace(m_Jobs=_JobMap(), m_ElapsedS=0.0)
        self.jobs = runner_state.m_Jobs
        runner_ns: dict[str, object] = {"this": runner_state}
        for signature in RUNNER_METHODS:
            translate(_free_method(runner, signature), signature, runner_ns, class_types=LOCAL_CLASS_TYPES)
        self.runner = SimpleNamespace(
            **{name: runner_ns[name] for name in ("GetElapsedS", "AddJob", "CountExcluding", "CountOfKind", "Tick")}
        )
        self.state = SimpleNamespace(
            m_TickPollSent=11,
            m_TickPollCallback=12,
            m_Tick=13,
            m_ActiveCam=None,
            m_CameraHandoffPending=False,
            m_JobRunner=self.runner,
            m_DialogJob=None,
        )
        game = SimpleNamespace(GetPlayer=lambda: self.player)
        self.ns: dict[str, object] = {
            "this": self.state,
            "GetGame": lambda: game,
            "PlayerBase": SimpleNamespace(Cast=lambda value: value if isinstance(value, _Player) else None),
            "PostResult": self.posted.append,
            **self.classes,
            **self.constants,
        }
        for signature in (DISPATCH_CHAIN, IN_GAME, EXCLUSIVE, *VERB_METHODS, *OWNER_HOOKS):
            translate(_free_method(client, signature), signature, self.ns, class_types=LOCAL_CLASS_TYPES)
        handler = self.ns["DispatchPlayerViewSet"]

        def handled(command: object, result: object) -> object:
            self.handled.append(command.cmd.text)
            return handler(command, result)

        self.ns["DispatchPlayerViewSet"] = handled
        hooks = [signature[: signature.index("(")].split()[-1] for signature in OWNER_HOOKS]
        self.owner = SimpleNamespace(**{name: self.ns[name] for name in hooks})

    def dispatch(self, view: str | None = "third", command_id: int = 7) -> None:
        command = self.classes["MCPCommand"]()
        command.id = command_id
        command.cmd = EnforceString.of(COMMAND)
        if view is not None:
            command.args = self.classes["MCPArgs"]()
            command.args.view = EnforceString.of(view)
        self.ns["Dispatch"](command)

    def tick(self, seconds: float) -> None:
        self.runner.Tick(seconds, self.owner)

    def add_job(self, kind: str, job_id: int = 90) -> object:
        job = self.classes["MCPJob"]()
        job.id = job_id
        job.kind = EnforceString.of(kind)
        job.deadline_s = 600.0
        self.runner.AddJob(job)
        return job

    def kinds(self) -> list[str]:
        return [job.kind.text for _key, job in self.jobs.items]

    def replies(self) -> list[tuple[int, bool, str, str, str]]:
        return [
            (reply.id, reply.ok, reply.error.text, reply.person_view.text, reply.camera_class.text)
            for reply in self.posted
        ]

    def set_calls(self) -> list[bool]:
        return list(self.player.set_calls) if isinstance(self.player, _Player) else []


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
        # Each refusal block returns: E2 drops the return of busy.
        for code in ("bad_args", "no_player", "script_camera", "busy"):
            with self.subTest(code=code):
                block = f'\t\t\tresult.error = "{code}";\n\t\t\treturn true;\n\t\t}}'
                self.assertEqual(self.dispatch.count(block), 1)
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
        # E3 replaces the elapsed time with the target itself.
        self.assertIn("elapsed = m_JobRunner.GetElapsedS() - job.sample_start_s;", self.process)
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
        # E4 drops the ok = false of the mismatch.
        mismatch = _if_body(self.post, "!job.args || result.person_view != job.args.view")
        self.assertEqual(mismatch.split(), "result.ok = false; result.error = \"view_not_applied\";".split())
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


# Refusals at dispatch: (case, view, setup, code). The first refusal that
# applies wins, so the last three cases combine two.
REFUSALS = (
    ("no args", None, lambda model: None, "bad_args"),
    ("empty view", "", lambda model: None, "bad_args"),
    ("3rd", "3rd", lambda model: None, "bad_args"),
    ("Third", "Third", lambda model: None, "bad_args"),
    ("FIRST", "FIRST", lambda model: None, "bad_args"),
    ("toggle", "toggle", lambda model: None, "bad_args"),
    ("not a PlayerBase", "third", lambda model: setattr(model, "player", _Man()), "no_player"),
    ("camera_set's camera", "third", lambda model: setattr(model.state, "m_ActiveCam", object()), "script_camera"),
    ("camera handoff", "third", lambda model: setattr(model.state, "m_CameraHandoffPending", True), "script_camera"),
    ("camera_set job", "third", lambda model: model.add_job("camera_set"), "busy"),
    ("player_look_at job", "third", lambda model: model.add_job("player_look_at"), "busy"),
    ("action_hold job", "third", lambda model: model.add_job("action_hold"), "busy"),
    ("another view job", "first", lambda model: model.add_job(COMMAND), "busy"),
    ("bad view, not a PlayerBase", "3rd", lambda model: setattr(model, "player", _Man()), "bad_args"),
    (
        "not a PlayerBase, a camera",
        "third",
        lambda model: (setattr(model, "player", _Man()), setattr(model.state, "m_ActiveCam", object())),
        "no_player",
    ),
    (
        "a camera, a job",
        "third",
        lambda model: (setattr(model.state, "m_ActiveCam", object()), model.add_job("camera_set")),
        "script_camera",
    ),
)


class PlayerViewSetEnforceRunTest(unittest.TestCase):
    """The real Enforce of the client bridge and the runner, through the subset."""

    def _refusal(self, setup, view: str | None = "third", client: str | None = None):
        """(replies, setter calls, jobs added) of one dispatch after `setup`."""
        model = _ClientModel(client)
        setup(model)
        player = model.player
        before = model.kinds()
        model.dispatch(view, 7)
        calls = list(player.set_calls) if isinstance(player, _Player) else []
        return model.replies(), calls, model.kinds()[len(before) :]

    def _settle(self, client: str | None = None) -> list[list[tuple[int, bool, str, str, str]]]:
        """Replies after each tick: the runner at 1.0 s, a dispatch, then 0.01, 0.13 and 0.02 s."""
        model = _ClientModel(client)
        model.tick(1.0)
        model.dispatch("third", 7)
        seen = []
        for seconds in (0.01, 0.13, 0.02):
            model.tick(seconds)
            seen.append(model.replies())
        return seen

    def _not_kept(self, client: str | None = None):
        """Third requested while the server disables third person."""
        model = _ClientModel(client)
        model.player.third_allowed = False
        model.dispatch("third", 7)
        model.tick(0.2)
        return model.replies(), model.player.set_calls

    def test_third_then_first_are_read_back_after_the_settle(self) -> None:
        model = _ClientModel()
        player = model.player
        model.dispatch("third", 7)
        self.assertEqual((model.replies(), player.set_calls, model.kinds()), ([], [True], [COMMAND]))
        model.tick(0.1)
        self.assertEqual(model.replies(), [])
        model.tick(0.1)
        self.assertEqual(model.replies(), [(7, True, "", "third", "DayZPlayerCamera3rdPersonErc")])
        self.assertEqual(model.kinds(), [])
        reply = model.posted[0]
        self.assertEqual((reply.tick_poll_sent, reply.tick_poll_callback, reply.tick_dispatch), (11, 12, 13))
        model.dispatch("first", 8)
        model.tick(0.2)
        self.assertEqual(model.replies()[1], (8, True, "", "first", "DayZPlayerCamera1stPerson"))
        self.assertEqual(player.set_calls, [True, False])

    def test_the_job_starts_at_the_runner_clock_with_the_camera_deadline(self) -> None:
        model = _ClientModel()
        model.tick(1.0)
        model.dispatch("third", 7)
        ((_key, job),) = model.jobs.items
        settle = model.constants["CAMERA_SETTLE_STEP_S"] * model.constants["CAMERA_DEFAULT_SETTLE_TICKS"]
        self.assertAlmostEqual(settle, 0.15)
        self.assertEqual((job.id, job.kind.text, job.args.view.text), (7, COMMAND, "third"))
        self.assertEqual((job.sample_start_s, job.sample_s_target), (1.0, settle))
        self.assertEqual(job.deadline_s, 1.0 + settle + model.constants["CAMERA_JOB_TIMEOUT_S"])

    def test_the_reply_waits_for_the_settle_and_e3_is_caught(self) -> None:
        settled = [(7, True, "", "third", "DayZPlayerCamera3rdPersonErc")]
        self.assertEqual(self._settle(), [[], [], settled])
        # E3 computes elapsed as the target itself: it answers after 0.01 s.
        mutant = self._settle(_mutate(_source(CLIENT_PATH), *E3_NO_WAIT))
        self.assertNotEqual(mutant, [[], [], settled])
        self.assertEqual(mutant[0], settled)

    def test_each_refusal_answers_before_the_view_changes(self) -> None:
        for case, view, setup, code in REFUSALS:
            with self.subTest(case=case):
                self.assertEqual(self._refusal(setup, view), ([(7, False, code, "", "")], [], []))

    def test_e2_a_busy_branch_that_goes_on_is_caught(self) -> None:
        def busy(model: _ClientModel) -> None:
            model.add_job("camera_set")

        refused = ([(7, False, "busy", "", "")], [], [])
        self.assertEqual(self._refusal(busy), refused)
        # Without its return the branch writes busy, sets the view, adds the job
        # and answers nothing at dispatch.
        mutant = self._refusal(busy, client=_mutate(_source(CLIENT_PATH), *E2_BUSY_GOES_ON))
        self.assertEqual(mutant, ([], [True], [COMMAND]))

    def test_no_local_player_is_client_not_in_game_before_the_handler(self) -> None:
        model = _ClientModel()
        model.player = None
        model.dispatch("third", 7)
        self.assertEqual(model.replies(), [(7, False, "client_not_in_game", "", "")])
        self.assertEqual((model.handled, model.kinds()), ([], []))
        # A local player that is not a PlayerBase reaches the handler.
        model.player = _Man()
        model.dispatch("third", 8)
        self.assertEqual(model.replies()[1], (8, False, "no_player", "", ""))
        self.assertEqual(model.handled, [COMMAND])

    def test_the_four_kinds_left_out_of_exclusive_jobs_do_not_refuse(self) -> None:
        exclusive = _method_body(_source(CLIENT_PATH), EXCLUSIVE)
        left_out = re.findall(r'(?:CountExcluding|CountOfKind)\("(\w+)"\)', exclusive)
        self.assertEqual(sorted(left_out), sorted(NON_EXCLUSIVE_KINDS))
        for kind in NON_EXCLUSIVE_KINDS:
            with self.subTest(kind=kind):
                model = _ClientModel()
                model.add_job(kind)
                self.assertFalse(model.ns["HasExclusiveJob"]())
                model.dispatch("third", 7)
                self.assertEqual(model.replies(), [])
                self.assertEqual((model.player.set_calls, model.kinds()), ([True], [kind, COMMAND]))
                # The view job itself counts: camera_set answers busy until the reply.
                self.assertTrue(model.ns["HasExclusiveJob"]())

    def test_a_view_the_engine_did_not_keep_is_not_ok_and_e4_is_caught(self) -> None:
        refused = ([(7, False, "view_not_applied", "first", "DayZPlayerCamera1stPerson")], [True])
        self.assertEqual(self._not_kept(), refused)
        # E4 drops ok = false: the mismatch would read as a success.
        mutant = self._not_kept(_mutate(_source(CLIENT_PATH), *E4_MISMATCH_OK))
        self.assertEqual(mutant, ([(7, True, "view_not_applied", "first", "DayZPlayerCamera1stPerson")], [True]))

    def test_a_null_camera_reads_back_an_empty_class(self) -> None:
        model = _ClientModel()
        model.player.camera = None
        model.dispatch("third", 7)
        model.tick(0.2)
        self.assertEqual(model.replies(), [(7, True, "", "third", "")])

    def test_after_the_settle_a_lost_player_or_a_mounted_camera_answer_with_the_view_set(self) -> None:
        for case, change, code in (
            ("player gone", lambda model: setattr(model, "player", None), "no_player"),
            ("not a PlayerBase", lambda model: setattr(model, "player", _Man()), "no_player"),
            ("camera mounted", lambda model: setattr(model.state, "m_ActiveCam", object()), "script_camera"),
            ("handoff pending", lambda model: setattr(model.state, "m_CameraHandoffPending", True), "script_camera"),
        ):
            with self.subTest(case=case):
                model = _ClientModel()
                player = model.player
                model.dispatch("third", 7)
                change(model)
                model.tick(0.2)
                self.assertEqual(model.replies(), [(7, False, code, "", "")])
                self.assertEqual((player.set_calls, model.kinds()), ([True], []))


class PlayerViewSetMessageMembersTest(unittest.TestCase):
    """Member names are unique per message class, whatever their type (M2)."""

    def _twice(self, messages: str) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for name in re.findall(r"(?m)^class (\w+)", _without_comments(messages)):
            names = [member for _type, member in _message_members(messages, name)]
            twice = sorted({member for member in names if names.count(member) > 1})
            if twice:
                found[name] = twice
        return found

    def test_every_message_class_declares_each_member_once(self) -> None:
        messages = _source(MESSAGES_PATH)
        self.assertEqual(self._twice(messages), {})
        views = [member for member in _message_members(messages, "MCPArgs") if member[1] == "view"]
        self.assertEqual(views, [("string", "view")])
        result = _message_members(messages, "MCPResult")
        for field in FIELDS:
            with self.subTest(field=field):
                self.assertEqual([member for member in result if member[1] == field], [("string", field)])

    def test_m2_a_second_view_of_another_type_is_caught(self) -> None:
        mutant = _mutate(_source(MESSAGES_PATH), *M2_SECOND_VIEW)
        self.assertEqual(self._twice(mutant), {"MCPArgs": ["view"]})
        with self.assertRaisesRegex(AssertionError, r"MCPArgs declares \['view'\] more than once"):
            _ClientModel(messages=mutant)


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

    def test_class_names_with_underscores_and_the_length_limits_cross(self) -> None:
        for name in ("LF_Camera3rdPerson", "_Cam", "Cam_", "A", "A" * 64, "DayZPlayerCamera3rdPersonVehicle"):
            with self.subTest(camera_class=name):
                wire = {"ok": 0, "error": "view_not_applied", "person_view": "first", "camera_class": name}
                self.assertEqual(
                    str(server._bridge_error(wire, COMMAND)),
                    f"view_not_applied; observed=person_view=first camera_class={name}",
                )

    def test_b2_a_class_pattern_without_the_underscore_is_caught(self) -> None:
        wire = {"ok": 0, "error": "view_not_applied", "person_view": "first", "camera_class": "LF_Camera3rdPerson"}
        kept = "view_not_applied; observed=person_view=first camera_class=LF_Camera3rdPerson"
        self.assertEqual(str(server._bridge_error(wire, COMMAND)), kept)
        with patch.object(bridge_errors, "_PLAYER_VIEW_CAMERA_CLASS_RE", B2_NO_UNDERSCORE):
            self.assertEqual(str(server._bridge_error(wire, COMMAND)), "view_not_applied; observed=person_view=first")


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
            "not a wall-clock bound, and not proof the camera finished moving",
            "and empty when that camera is null.",
            "view_not_applied, and its message carries observed=person_view=... "
            "camera_class=..., without camera_class when it is empty.",
            "client_not_in_game without a local player",
            "no_player when the local player is not a PlayerBase",
            "script_camera while camera_set's camera is mounted",
            "busy while an exclusive client job runs",
            "After the settle the view is already set",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, description)
        # bad_args never reaches the bridge from the tool: the view is a literal.
        for code in sorted(_bridge_codes() - {"bad_args"}) + ["client_not_in_game"]:
            with self.subTest(code=code):
                self.assertIn(code, description)
        for kind in NON_EXCLUSIVE_KINDS:
            with self.subTest(kind=kind):
                self.assertIn(kind, description)
        # Round 1's claims: Dispatch answers client_not_in_game without a player,
        # and four kinds of client job do not make busy.
        for claim in ("no_player without a local player", "busy while another client job"):
            with self.subTest(claim=claim):
                self.assertNotIn(claim, description)

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
