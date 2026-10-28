"""server_perf, nav_path_query and players_visibility: three lease-free server reads.

server_perf (inbox 2427) counts the server frames of a window in a bridge job
and the dynamic entities once the window has closed. nav_path_query (0131)
snaps both ends to the navmesh and asks AIWorld.FindPath. players_visibility
(33c5) answers, for each connected player, an approximate field-of-view test
and a view ray from the head.

These tests pin the wire contract, the lease partition, the admission of an
older PBO, the compact catalog, the prune contract and the Enforce source the
game will compile. Two bridge methods, IsServerPerfReady and IsInApproxFov,
also run here, translated line by line from their own Enforce text. They do
not launch DayZ or compile the addon, so nothing here measures the engine
calls themselves; compiling is the final round's own gate.
"""
from __future__ import annotations

import asyncio
import copy
import json
import math
import re
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, result_prune, server
from dayz_mcp.bridge_readiness import EXPECTED_SERVER_ARG_CONTRACT_HASH
from dayz_mcp.core import EXPECTED_BRIDGE_VERSION
from dayz_mcp.server import (
    DEFAULT_TOOL_TIMEOUT_S,
    LEASE_TOOL_LINE,
    ServerConfig,
    ToolError,
    _BRIDGE_COMMAND_TOOLS,
    build_app,
)
from dayz_mcp.session_coordination import (
    READ_ONLY_COMMANDS,
    SessionCoordinator,
    command_requires_lease,
)
from tests._addon_paths import addon_root
from tests._tiers import slow_test
from tests.fence_helpers import INST_SERVER, PID_SERVER, bind_both_peers, bound_queue
from tests.mcp_helpers import FakePeer, _content_json


COMMANDS = ("server_perf", "nav_path_query", "players_visibility")
WIRE_ARGS: dict[str, dict[str, object]] = {
    "server_perf": {"window_s": 10.0},
    "nav_path_query": {"from": [10.0, 5.0, 20.0], "to": [60.0, 5.0, 20.0], "snap_radius": 2.0},
    "players_visibility": {"pos": [10.0, 5.0, 20.0], "radius": 0.0},
}
DISPATCHERS = {
    "server_perf": "DispatchServerPerf",
    "nav_path_query": "DispatchNavPathQuery",
    "players_visibility": "DispatchPlayersVisibility",
}
SCRIPTS = addon_root() / "scripts"
BRIDGE_PATH = SCRIPTS / "5_Mission" / "MCPBridge.c"
MESSAGES_PATH = SCRIPTS / "5_Mission" / "MCPMessages.c"
CENSUS_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "bridge_capabilities_v1.json"
)
DISPATCH_CHAIN = "protected void Dispatch(MCPCommand command)"
# The server arg-contract hash before these three verbs: a PBO built from the
# previous release still announces it.
PREVIOUS_ARG_CONTRACT_HASH = "4f90284258023d98"
IDENTITY = {
    "platform": "codex",
    "pid": 12,
    "ppid": 1,
    "started_at_utc": "2026-10-24T00:00:00Z",
    "session_id": "R",
    "task_label": "reads",
}

_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')
_MEMBER_RE = re.compile(
    r"(?m)(?<![A-Za-z0-9_>])(?P<type>(?:ref\s+)?[A-Za-z_][A-Za-z0-9_]*(?:\s*<[^;{}]+>)?)"
    r"\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*;"
)
_SERVER_CAPS = re.compile(
    r'protected const string SERVER_CAPABILITIES = ((?:"[^"\n]*"(?: \+ )?)+);'
)
_FLOAT_CONST = re.compile(r"protected const float (\w+) = (-?[0-9.]+);")
_LOCAL_DECLARATION = re.compile(
    r"^\s*(?:int|bool|float|vector|World|AIWorld|PGFilter|TVectorArray|PlayerBase|"
    r"PlayerIdentity|MCPPlayerVisibility)\s+(\w+)\s*;\s*$"
)
_ENFORCE_LOCAL = re.compile(r"(?:int|float|bool|vector|HumanInputController)\s+(\w+)(?:\s*=\s*(.+))?;")
_ENFORCE_HEADER = re.compile(r"(if|while) \((.+)\)")


class _EnforceMath:
    """The Math members the translated bridge methods call (enmath.c)."""

    RAD2DEG = 180.0 / math.pi
    Floor = staticmethod(math.floor)
    Sqrt = staticmethod(math.sqrt)
    Asin = staticmethod(math.asin)
    Atan2 = staticmethod(math.atan2)


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


def _without_comments(source: str) -> str:
    def replace(match: re.Match[str]) -> str:
        text = match.group(0)
        return text if text.startswith('"') else re.sub(r"[^\n]", " ", text)

    return _COMMENT_OR_STRING.sub(replace, source)


def _method_code(source: str, name: str) -> str:
    """The comment-free body of the one MCPBridge method called name."""
    clean = _without_comments(source)
    found = list(re.finditer(rf"\bprotected\s+[\w<>]+\s+{re.escape(name)}\(", clean))
    if len(found) != 1:
        raise AssertionError(f"{name} is declared {len(found)} times")
    return _block_after(clean, found[0].end())


def _if_body(code: str, condition: str) -> str:
    needle = f"if ({condition})"
    if code.count(needle) != 1:
        raise AssertionError(f"{needle!r} occurs {code.count(needle)} times")
    return _block_after(code, code.index(needle))


def _class_members(source: str, class_name: str) -> list[tuple[str, str]]:
    clean = _without_comments(source)
    match = re.search(rf"\bclass\s+{re.escape(class_name)}\b", clean)
    if match is None:
        raise AssertionError(f"class {class_name} is absent")
    body = _block_after(clean, match.end())
    return [(m.group("type"), m.group("name")) for m in _MEMBER_RE.finditer(body)]


def _server_capabilities(source: str) -> list[str]:
    declaration = _SERVER_CAPS.search(source)
    if declaration is None:
        raise AssertionError("SERVER_CAPABILITIES declaration not found")
    return "".join(re.findall(r'"([^"\n]*)"', declaration.group(1))).split(",")


def _in_order(testcase: unittest.TestCase, code: str, tokens: tuple[str, ...]) -> None:
    positions = []
    for token in tokens:
        with testcase.subTest(token=token):
            testcase.assertEqual(code.count(token), 1, token)
        positions.append(code.index(token))
    testcase.assertEqual(positions, sorted(positions))


def _locals_on_top(body: str) -> list[str]:
    """Plain local declarations, failing when one follows a statement."""
    names: list[str] = []
    statement_seen = False
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line in ("{", "}"):
            continue
        match = _LOCAL_DECLARATION.match(raw)
        if match is None:
            statement_seen = True
            continue
        if statement_seen:
            raise AssertionError(f"declaration after a statement: {line}")
        names.append(match.group(1))
    return names


def _python_expression(text: str) -> str:
    text = text.replace("&&", " and ").replace("||", " or ")
    text = re.sub(r"!(?!=)", " not ", text)
    return re.sub(r"\btrue\b", "True", re.sub(r"\bfalse\b", "False", text))


def _bridge_method(source: str, name: str, **names: object):
    """One bridge method as a Python function, translated from its Enforce text.

    It covers what IsServerPerfReady and IsInApproxFov are written in: locals,
    assignments, if and while blocks with their braces on their own lines, and
    returns. Any other line fails the translation instead of being guessed. The
    bridge's float constants come from the source; Python floats are doubles,
    so this checks the logic, not float32 rounding.
    """
    clean = _without_comments(source)
    found = list(re.finditer(rf"\bprotected\s+\w+\s+{re.escape(name)}\(([^)]*)\)", clean))
    if len(found) != 1:
        raise AssertionError(f"{name} is declared {len(found)} times")
    params = [part.split()[-1] for part in found[0].group(1).split(",") if part.strip()]
    lines = [f"def {name}({', '.join(params)}):"]
    depth = 1
    for raw in _block_after(clean, found[0].end()).splitlines():
        line = raw.strip()
        if line in ("", "{"):
            continue
        if line == "}":
            depth -= 1
            continue
        indent = "    " * depth
        header = _ENFORCE_HEADER.fullmatch(line)
        local = _ENFORCE_LOCAL.fullmatch(line)
        if header:
            lines.append(f"{indent}{header.group(1)} {_python_expression(header.group(2))}:")
            depth += 1
        elif local:
            value = local.group(2)
            lines.append(f"{indent}{local.group(1)} = {_python_expression(value) if value else 0}")
        elif line.startswith("return ") and line.endswith(";"):
            lines.append(f"{indent}return {_python_expression(line[len('return '):-1])}")
        elif re.fullmatch(r"[\w.]+ = .+;", line):
            target, value = line[:-1].split(" = ", 1)
            lines.append(f"{indent}{target} = {_python_expression(value)}")
        else:
            raise AssertionError(f"{name}: no translation for {line!r}")
    namespace: dict[str, object] = {"Math": _EnforceMath, **names}
    namespace.update((constant, float(value)) for constant, value in _FLOAT_CONST.findall(source))
    exec("\n".join(lines), namespace)
    return namespace[name]


def _announce_server(
    state: loopback.ServerState, commands: list[str], ach: str | None
) -> None:
    state.daemon_generation = "gen-reads"
    state._peer_caps["server"] = {
        "generation": state.daemon_generation,
        "commands": list(commands),
        "reason": "ok",
        "arg_contract_hash": ach,
        "announced_at": state._now(),
        "instance": INST_SERVER,
    }


def _server_commands() -> list[str]:
    return sorted(_BRIDGE_COMMAND_TOOLS["server"])


def _bound_state(**kwargs: object) -> loopback.ServerState:
    state = loopback.ServerState(key="k", **kwargs)  # type: ignore[arg-type]
    state.install_bound_peer(
        instance=INST_SERVER, role="server", pid=PID_SERVER, run_id="reads-run"
    )
    return state


class ReadsIngressTest(unittest.TestCase):
    def test_server_reads_that_need_no_lease(self) -> None:
        for command in COMMANDS:
            with self.subTest(command=command):
                self.assertIn(command, loopback.SERVER_COMMANDS)
                self.assertNotIn(command, loopback.CLIENT_COMMANDS)
                self.assertNotIn(command, loopback.EXEC_COMMANDS)
                self.assertEqual(loopback.peer_for_command(command), "server")
                self.assertIn(command, READ_ONLY_COMMANDS)
                self.assertFalse(command_requires_lease(command))
                self.assertIn(command, loopback._CAPABILITY_ADMISSION_COMMANDS)

    def test_a_session_without_a_lease_reads_and_the_set_beside_it_cannot(self) -> None:
        events: list[dict[str, object]] = []
        state = loopback.ServerState("test-key")
        bind_both_peers(state)
        _announce_server(state, _server_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
        state.coordination = SessionCoordinator(
            token_fn=lambda: "token",
            id_fn=lambda: "lease",
            audit=lambda event: events.append(event) or True,
            cleanup=lambda session, lease, reason, active: state.cleanup_owner(
                session, lease, reason, active
            ),
        )
        for command in COMMANDS:
            with self.subTest(command=command):
                status, body = state.enqueue_command(
                    command, dict(WIRE_ARGS[command]), "server", identity_payload=IDENTITY
                )
                self.assertEqual((status, body.get("cmd")), (200, command), body)
        # Negative control: a mutation through the same session needs the lease.
        refused = state.enqueue_command(
            "world_time_set",
            {"year": 2026, "month": 1, "day": 1, "hour": 0, "minute": 0},
            "server",
            identity_payload=IDENTITY,
        )
        self.assertEqual(refused, (423, {"error": "lease_required"}))
        decisions = [event.get("decision") for event in events]
        self.assertEqual(decisions.count("read"), len(COMMANDS))

    def test_a_server_read_cannot_go_to_the_client(self) -> None:
        state = loopback.ServerState("test-key")
        bind_both_peers(state)
        for command in COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(
                    state.enqueue_command(command, dict(WIRE_ARGS[command]), peer="client"),
                    (400, {"error": "bad_peer"}),
                )


class ReadsAdmissionTest(unittest.TestCase):
    """An older PBO that lacks a verb is refused before an id, and again at delivery."""

    def test_missing_capability_refuses_before_an_id(self) -> None:
        for command in COMMANDS:
            with self.subTest(command=command):
                state = _bound_state()
                reduced = [name for name in _server_commands() if name != command]
                _announce_server(state, reduced, EXPECTED_SERVER_ARG_CONTRACT_HASH)
                before = state._next_id
                status, body = state.enqueue_command(
                    command, dict(WIRE_ARGS[command]), peer="server"
                )
                self.assertEqual((status, body["error"]), (409, "bridge_capability_missing"))
                self.assertEqual(state._next_id, before)
                self.assertEqual(bound_queue(state, "server"), [])

    def test_the_previous_contract_hash_refuses_before_an_id(self) -> None:
        for command in COMMANDS:
            with self.subTest(command=command):
                state = _bound_state()
                _announce_server(state, _server_commands(), PREVIOUS_ARG_CONTRACT_HASH)
                before = state._next_id
                status, body = state.enqueue_command(
                    command, dict(WIRE_ARGS[command]), peer="server"
                )
                self.assertEqual((status, body["error"]), (409, "arg_contract_mismatch"))
                self.assertEqual(state._next_id, before)

    def test_capability_lost_before_delivery_is_discarded(self) -> None:
        for command in COMMANDS:
            with self.subTest(command=command):
                state = _bound_state()
                _announce_server(state, _server_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
                status, body = state.enqueue_command(
                    command, dict(WIRE_ARGS[command]), peer="server"
                )
                self.assertEqual(status, 200, body)
                reduced = [name for name in _server_commands() if name != command]
                status, polled = state.record_poll(
                    "server",
                    f"{EXPECTED_BRIDGE_VERSION}~1.29.0",
                    instance=INST_SERVER,
                    source_pid=PID_SERVER,
                    caps=",".join(reduced),
                    ach=EXPECTED_SERVER_ARG_CONTRACT_HASH,
                )
                self.assertEqual(status, 200)
                self.assertEqual(polled["commands"], [])
                stored = state.take_result(body["id"])
                self.assertEqual(stored["error"], "bridge_capability_missing")

    def test_a_full_census_queues_exactly_the_wire_arguments(self) -> None:
        for command in COMMANDS:
            with self.subTest(command=command):
                state = _bound_state()
                _announce_server(state, _server_commands(), EXPECTED_SERVER_ARG_CONTRACT_HASH)
                status, body = state.enqueue_command(
                    command, dict(WIRE_ARGS[command]), peer="server"
                )
                self.assertEqual(status, 200, body)
                queued = [
                    {key: value for key, value in item.items() if key != "_broker_pin"}
                    for item in bound_queue(state, "server")
                ]
                self.assertEqual(
                    queued, [{"id": body["id"], "cmd": command, "args": WIRE_ARGS[command]}]
                )


class ReadsCensusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = BRIDGE_PATH.read_text(encoding="utf-8")

    def test_caps_dispatch_daemon_map_and_fixture_name_each_command(self) -> None:
        caps = _server_capabilities(self.source)
        self.assertEqual(caps, sorted(caps))
        dispatch = _without_comments(_block_after(self.source, self.source.index(DISPATCH_CHAIN)))
        fixture = json.loads(CENSUS_FIXTURE.read_text(encoding="utf-8"))
        for command in COMMANDS:
            with self.subTest(command=command):
                self.assertIn(command, caps)
                branch = _if_body(dispatch, f'command.cmd == "{command}"')
                self.assertEqual(
                    branch.strip(), f"postNow = {DISPATCHERS[command]}(command, result);"
                )
                self.assertEqual(_BRIDGE_COMMAND_TOOLS["server"].get(command), command)
                self.assertNotIn(command, _BRIDGE_COMMAND_TOOLS["client"])
                self.assertEqual(fixture["peers"]["server"].get(command), command)

    def test_the_arg_contract_names_every_wire_key_and_the_version_stays(self) -> None:
        self.assertEqual(server.SERVER_ARG_CONTRACT["server_perf"], ("window_s",))
        self.assertEqual(
            server.SERVER_ARG_CONTRACT["nav_path_query"], ("from", "snap_radius", "to")
        )
        self.assertEqual(server.SERVER_ARG_CONTRACT["players_visibility"], ("pos", "radius"))
        for command in COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(
                    set(server.SERVER_ARG_CONTRACT[command]), set(WIRE_ARGS[command])
                )
        self.assertEqual(EXPECTED_SERVER_ARG_CONTRACT_HASH, "0401bed390754a55")
        self.assertNotEqual(EXPECTED_SERVER_ARG_CONTRACT_HASH, PREVIOUS_ARG_CONTRACT_HASH)
        self.assertIn('SERVER_ARG_CONTRACT_HASH = "0401bed390754a55"', self.source)
        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        self.assertIn('const string MCP_BRIDGE_VERSION = "12";', messages)

    def test_a_stale_server_census_names_the_missing_tool(self) -> None:
        registered = frozenset(
            tool for tool in _BRIDGE_COMMAND_TOOLS["server"].values() if tool
        )
        for command in COMMANDS:
            with self.subTest(command=command):
                announced = [name for name in _server_commands() if name != command]
                result = server._compare_bridge_capabilities(
                    "server",
                    {
                        "state": "announced",
                        "reason": "ok",
                        "announced_commands": announced,
                        "announced_arg_contract_hash": EXPECTED_SERVER_ARG_CONTRACT_HASH,
                    },
                    registered,
                )
                self.assertEqual(result["state"], "mismatch")
                self.assertEqual(result["registered_without_announced_command"], [command])

    def test_the_bounds_are_equal_in_the_bridge_the_tool_and_the_ingress(self) -> None:
        constants = {name: float(value) for name, value in _FLOAT_CONST.findall(self.source)}
        self.assertEqual(constants["SERVER_PERF_WINDOW_MIN_S"], server.SERVER_PERF_WINDOW_MIN_S)
        self.assertEqual(constants["SERVER_PERF_WINDOW_MAX_S"], server.SERVER_PERF_WINDOW_MAX_S)
        self.assertEqual(constants["NAV_SNAP_RADIUS_MAX"], server.NAV_SNAP_RADIUS_MAX_M)
        self.assertEqual(constants["VISIBILITY_RADIUS_MAX"], server.VISIBILITY_RADIUS_MAX_M)
        window_min = server.SERVER_PERF_WINDOW_MIN_S
        window_max = server.SERVER_PERF_WINDOW_MAX_S
        snap_max = server.NAV_SNAP_RADIUS_MAX_M
        radius_max = server.VISIBILITY_RADIUS_MAX_M
        nav = WIRE_ARGS["nav_path_query"]
        vis = WIRE_ARGS["players_visibility"]
        # The ingress compares doubles. The bridge reads these floats as
        # float32, where a double one step past an edge can round onto it and
        # the smallest double above 0 is 0: the low edge of snap_radius is one
        # float32 holds, and the steps past the other edges are the ingress's.
        cases = (
            ("server_perf", {"window_s": window_min}, True),
            ("server_perf", {"window_s": window_max}, True),
            ("server_perf", {"window_s": math.nextafter(window_min, 0.0)}, False),
            ("server_perf", {"window_s": math.nextafter(window_max, math.inf)}, False),
            ("nav_path_query", {**nav, "snap_radius": snap_max}, True),
            ("nav_path_query", {**nav, "snap_radius": 1e-6}, True),
            ("nav_path_query", {**nav, "snap_radius": 0.0}, False),
            ("nav_path_query", {**nav, "snap_radius": math.nextafter(snap_max, math.inf)}, False),
            ("players_visibility", {**vis, "radius": 0.0}, True),
            ("players_visibility", {**vis, "radius": radius_max}, True),
            ("players_visibility", {**vis, "radius": -0.001}, False),
            ("players_visibility", {**vis, "radius": math.nextafter(radius_max, math.inf)}, False),
        )
        for command, args, accepted in cases:
            with self.subTest(command=command, args=args):
                ok, error = loopback.validate_command_args(command, args)
                self.assertEqual(ok, accepted, error)
        # The bridge compares the same way: below or above refuses, the edges pass.
        perf = _method_code(self.source, "DispatchServerPerf")
        self.assertIn("command.args.window_s < SERVER_PERF_WINDOW_MIN_S", perf)
        self.assertIn("command.args.window_s > SERVER_PERF_WINDOW_MAX_S", perf)
        path = _method_code(self.source, "DispatchNavPathQuery")
        self.assertIn("command.args.snap_radius <= 0.0", path)
        self.assertIn("command.args.snap_radius > NAV_SNAP_RADIUS_MAX", path)
        visibility = _method_code(self.source, "DispatchPlayersVisibility")
        self.assertIn("radius < 0.0", visibility)
        self.assertIn("radius > VISIBILITY_RADIUS_MAX", visibility)


class ServerPerfEnforceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = BRIDGE_PATH.read_text(encoding="utf-8")

    def test_the_dispatch_validates_and_queues_a_job(self) -> None:
        code = _method_code(self.source, "DispatchServerPerf")
        self.assertEqual(set(re.findall(r'result\.error = "(\w+)"', code)), {"bad_args"})
        self.assertIn("!IsFiniteFloat(command.args.window_s)", code)
        _in_order(
            self,
            code,
            (
                'result.error = "bad_args";',
                "job = new MCPJob();",
                'job.kind = "server_perf";',
                "job.sample_s_target = command.args.window_s;",
                "job.deadline_s = m_ElapsedS + command.args.window_s + JOB_TIMEOUT_S;",
                "m_Jobs.Insert(job.id, job);",
                "return false;",
            ),
        )

    def test_the_job_counts_frames_from_tick_time_and_asks_no_fps_api(self) -> None:
        ready = _method_code(self.source, "IsJobReady")
        branch = _if_body(ready, 'job.kind == "server_perf"')
        self.assertEqual(branch.strip(), "return IsServerPerfReady(job);")
        code = _method_code(self.source, "IsServerPerfReady")
        _in_order(
            self,
            code,
            (
                "float now = GetGame().GetTickTime();",
                "job.sample_start_s = now;",
                "job.perf_second_frames = 1;",
                "job.phase = 1;",
                "elapsed = now - job.sample_start_s;",
                "second = Math.Floor(elapsed);",
                "job.perf_frames = job.perf_frames + 1;",
                "job.perf_measured_s = elapsed;",
                "return elapsed >= job.sample_s_target;",
            ),
        )
        # A second is closed only when it ends inside the window.
        self.assertIn(
            "while (job.perf_second < second && job.perf_second + 1 <= job.sample_s_target)",
            code,
        )
        for code_text in (code, _method_code(self.source, "PostServerPerfSuccess")):
            self.assertNotRegex(code_text, r"(?i)\bget\w*fps\b")

    def test_min_fps_is_the_fewest_frame_ends_in_a_whole_second(self) -> None:
        # 10 frame ends in [0, 1), the first opening the window, then 2 in
        # [1, 2), and the window closing at 2: the fewest is 2, not 10.
        clock = types.SimpleNamespace(now=0.0)
        game = types.SimpleNamespace(GetTickTime=lambda: clock.now)
        ready = _bridge_method(self.source, "IsServerPerfReady", GetGame=lambda: game)
        job = types.SimpleNamespace(
            phase=0,
            sample_s_target=2.0,
            sample_start_s=0.0,
            perf_frames=0,
            perf_second=0,
            perf_second_frames=0,
            perf_whole_seconds=0,
            perf_min_frames=0,
            perf_measured_s=0.0,
        )
        answers = []
        for now in [tenth / 10 for tenth in range(10)] + [1.0, 1.5, 2.0]:
            clock.now = now
            answers.append(ready(job))
        self.assertEqual(answers, [False] * 12 + [True])
        self.assertEqual((job.perf_whole_seconds, job.perf_min_frames), (2, 2))
        self.assertEqual((job.perf_frames, job.perf_measured_s), (12, 2.0))

    def test_every_job_member_the_server_perf_path_uses_is_declared(self) -> None:
        # Not a compile: that is the final round's gate. It keeps out one
        # error it would find, a job member MCPJob does not declare.
        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        declared = {name for _type, name in _class_members(messages, "MCPJob")}
        used: set[str] = set()
        for method in ("DispatchServerPerf", "IsServerPerfReady", "PostServerPerfSuccess"):
            used |= set(re.findall(r"\bjob\.(\w+)", _method_code(self.source, method)))
        counters = {
            "perf_frames",
            "perf_second",
            "perf_second_frames",
            "perf_whole_seconds",
            "perf_min_frames",
            "perf_measured_s",
        }
        self.assertLessEqual(counters, used)
        self.assertEqual(sorted(used - declared), [])

    def test_the_reply_drops_the_rates_on_insufficient_samples(self) -> None:
        success = _method_code(self.source, "PostJobSuccess")
        branch = _if_body(success, 'job.kind == "server_perf"')
        self.assertEqual(
            [line.strip() for line in branch.strip().splitlines()],
            ["PostServerPerfSuccess(job);", "return;"],
        )
        self.assertLess(
            success.index('if (job.kind == "server_perf")'),
            success.index("MCPResult result = new MCPResult();"),
        )
        code = _method_code(self.source, "PostServerPerfSuccess")
        self.assertEqual(
            set(re.findall(r'result\.error = "(\w+)"', code)), {"world_unavailable"}
        )
        rates = _if_body(code, "!result.insufficient_samples")
        self.assertEqual(
            [line.strip() for line in rates.strip().splitlines()],
            [
                "result.avg_fps = frames / job.perf_measured_s;",
                "result.min_fps = job.perf_min_frames;",
            ],
        )
        self.assertEqual(code.count("result.avg_fps"), 1)
        self.assertEqual(code.count("result.min_fps"), 1)
        _in_order(
            self,
            code,
            (
                "result.insufficient_samples = (job.perf_whole_seconds == 0 || job.perf_frames < 2);",
                "if (!result.insufficient_samples)",
                "result.counts = new MCPServerPerfCounts();",
                "CountServerPerfEntities(world.GetWorldSize(), result.counts);",
                "result.ok = true;",
            ),
        )
        self.assertEqual(
            code[code.index("result.ok = true;") :].split(), ["result.ok", "=", "true;", "PostResult(result);"]
        )

    def test_one_scene_pass_over_the_map_counts_each_entry_in_its_first_class(self) -> None:
        code = _method_code(self.source, "CountServerPerfEntities")
        self.assertEqual(code.count("SceneGetEntitiesInBox("), 1)
        # x and z from 0 to the world size, y from minus to plus it.
        self.assertIn(
            "DayZPlayerUtils.SceneGetEntitiesInBox(Vector(0.0, -worldSize, 0.0), "
            "Vector(worldSize, worldSize, worldSize), entities, QueryFlags.DYNAMIC);",
            code,
        )
        _in_order(
            self,
            code,
            (
                "player = PlayerBase.Cast(entity);",
                "if (player.GetIdentity())",
                "counts.players = counts.players + 1;",
                "counts.ai = counts.ai + 1;",
                "else if (DayZInfected.Cast(entity))",
                "counts.infected = counts.infected + 1;",
                "else if (DayZAnimal.Cast(entity))",
                "counts.animals = counts.animals + 1;",
                "else if (Transport.Cast(entity))",
                "counts.vehicles = counts.vehicles + 1;",
                "else if (InventoryItem.Cast(entity))",
                "counts.items = counts.items + 1;",
            ),
        )

    def test_the_wire_classes_hold_primitives_only(self) -> None:
        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        self.assertEqual(
            _class_members(messages, "MCPServerPerfCounts"),
            [("int", name) for name in ("players", "ai", "infected", "animals", "vehicles", "items")],
        )
        result = _class_members(messages, "MCPResult")
        for member in (
            ("float", "avg_fps"),
            ("int", "min_fps"),
            ("bool", "insufficient_samples"),
            ("ref MCPServerPerfCounts", "counts"),
        ):
            with self.subTest(member=member):
                self.assertEqual(result.count(member), 1)
        self.assertIn(("float", "window_s"), _class_members(messages, "MCPArgs"))


class NavPathEnforceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = BRIDGE_PATH.read_text(encoding="utf-8")
        self.code = _method_code(self.source, "DispatchNavPathQuery")

    def test_each_refusal_is_a_stable_error(self) -> None:
        self.assertEqual(
            set(re.findall(r'result\.error = "(\w+)"', self.code)),
            {"bad_args", "no_navmesh", "no_navmesh_at_from", "no_navmesh_at_to", "no_path"},
        )
        for condition, error in (
            ("!aiWorld", "no_navmesh"),
            (
                "!aiWorld.SampleNavmeshPosition(fromPos, command.args.snap_radius, filter, fromSnapped)",
                "no_navmesh_at_from",
            ),
            (
                "!aiWorld.SampleNavmeshPosition(toPos, command.args.snap_radius, filter, toSnapped)",
                "no_navmesh_at_to",
            ),
            ("!found || !waypoints || waypoints.Count() == 0", "no_path"),
        ):
            with self.subTest(error=error):
                body = _if_body(self.code, condition)
                self.assertIn("result.ok = false;", body)
                self.assertIn(f'result.error = "{error}";', body)
                self.assertIn("return true;", body)

    def test_both_ends_are_snapped_before_find_path_and_read_only_after_success(self) -> None:
        _in_order(
            self,
            self.code,
            (
                "aiWorld = world.GetAIWorld();",
                "filter = BuildNavWalkFilter();",
                "if (!aiWorld.SampleNavmeshPosition(fromPos,",
                "if (!aiWorld.SampleNavmeshPosition(toPos,",
                "waypoints = new TVectorArray();",
                "found = aiWorld.FindPath(fromSnapped, toSnapped, filter, waypoints);",
                "result.points = new array<float>();",
                "result.length_m = length;",
                "VectorToArray(fromSnapped, result.from_snapped);",
                "VectorToArray(toSnapped, result.to_snapped);",
                "result.ok = true;",
            ),
        )
        # A snapped end is first named by the call that writes it.
        for name, call in (("fromSnapped", "fromPos,"), ("toSnapped", "toPos,")):
            with self.subTest(name=name):
                first_use = re.search(rf"\b{name}\b(?!;)", self.code)
                self.assertIsNotNone(first_use)
                self.assertGreater(
                    first_use.start(), self.code.index(f"SampleNavmeshPosition({call}")
                )

    def test_the_length_sums_the_waypoint_segments(self) -> None:
        loop = _block_after(self.code, self.code.index("while (i < waypoints.Count())"))
        self.assertIn("result.points.Insert(waypoint[0]);", loop)
        self.assertIn("result.points.Insert(waypoint[1]);", loop)
        self.assertIn("result.points.Insert(waypoint[2]);", loop)
        segment = _if_body(loop, "i > 0")
        self.assertEqual(
            segment.strip(), "length = length + vector.Distance(waypoints.Get(i - 1), waypoint);"
        )

    def test_locals_are_declared_on_top(self) -> None:
        self.assertEqual(
            _locals_on_top(self.code),
            [
                "world",
                "aiWorld",
                "filter",
                "fromPos",
                "toPos",
                "fromSnapped",
                "toSnapped",
                "waypoint",
                "waypoints",
                "found",
                "length",
                "i",
            ],
        )
        with self.assertRaises(AssertionError):
            _locals_on_top("\t\tint a;\n\t\ta = 1;\n\t\tint b;\n")

    def test_the_walk_filter_includes_walking_and_excludes_swimming(self) -> None:
        code = _method_code(self.source, "BuildNavWalkFilter")
        _in_order(
            self,
            code,
            (
                "PGFilter filter = new PGFilter();",
                "int includeFlags = PGPolyFlags.WALK | PGPolyFlags.DOOR | PGPolyFlags.INSIDE;",
                "int excludeFlags = PGPolyFlags.DISABLED | PGPolyFlags.SWIM | PGPolyFlags.SWIM_SEA;",
                "filter.SetFlags(includeFlags, excludeFlags, 0);",
                "return filter;",
            ),
        )

    def test_the_reply_members(self) -> None:
        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        result = _class_members(messages, "MCPResult")
        for member in (
            ("ref array<float>", "points"),
            ("float", "length_m"),
            ("ref array<float>", "from_snapped"),
            ("ref array<float>", "to_snapped"),
        ):
            with self.subTest(member=member):
                self.assertEqual(result.count(member), 1)
        self.assertIn(("float", "snap_radius"), _class_members(messages, "MCPArgs"))


class VisibilityEnforceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = BRIDGE_PATH.read_text(encoding="utf-8")
        self.code = _method_code(self.source, "DispatchPlayersVisibility")

    def test_one_row_per_player_with_an_identity(self) -> None:
        self.assertEqual(set(re.findall(r'result\.error = "(\w+)"', self.code)), {"bad_args"})
        _in_order(
            self,
            self.code,
            (
                "result.players_visibility = new array<ref MCPPlayerVisibility>();",
                "GetGame().GetPlayers(m_Players);",
                "player = PlayerBase.Cast(m_Players.Get(i));",
                "identity = player.GetIdentity();",
                "if (identity)",
                "head = PlayerHeadPosition(player);",
                "distance = vector.Distance(head, target);",
                "row.uid = identity.GetPlainId();",
                "row.distance = distance;",
                "row.in_fov = IsInApproxFov(player, head, target, radius);",
                "row.line_of_sight = HasLineOfSight(player, head, target, radius);",
                "row.visible = row.in_fov && row.line_of_sight;",
                "result.players_visibility.Insert(row);",
                "result.ok = true;",
            ),
        )

    def test_the_view_ray_is_scene_raycast_default_and_ignores_the_player(self) -> None:
        code = _method_code(self.source, "HasLineOfSight")
        _in_order(
            self,
            code,
            (
                "ray.from = head;",
                "ray.to = target;",
                "ray.radius = VISIBILITY_RAY_RADIUS;",
                "ray.intersect_type = ObjIntersectView;",
                "PopulateRaycastRVProxy(ray, player, hit);",
                "if (!hit.hit)",
                "return vector.Distance(hitPos, target) <= radius;",
            ),
        )
        constants = {name: float(value) for name, value in _FLOAT_CONST.findall(self.source)}
        # scene_raycast's own default radius (ValidateRaycastArgs).
        self.assertEqual(constants["VISIBILITY_RAY_RADIUS"], 0.05)
        self.assertEqual(constants["VISIBILITY_HALF_FOV_DEG"], 60.0)

    def test_the_fov_is_generous_when_the_heading_is_unknown(self) -> None:
        code = _method_code(self.source, "IsInApproxFov")
        _in_order(
            self,
            code,
            (
                "horizontal = Math.Sqrt(dx * dx + dz * dz);",
                "if (horizontal <= radius)",
                "hic = player.GetInputController();",
                "if (!hic)",
                "hic.GetHeadingAngle()",
                "allowedDeg = VISIBILITY_HALF_FOV_DEG + Math.Asin(radius / horizontal) * Math.RAD2DEG;",
                "return offDeg <= allowedDeg;",
            ),
        )
        for condition in ("horizontal <= radius", "!hic"):
            with self.subTest(condition=condition):
                self.assertEqual(_if_body(code, condition).strip(), "return true;")

    def test_the_fov_holds_a_sphere_any_part_of_which_is_inside_the_cone(self) -> None:
        heading = types.SimpleNamespace(rad=0.0)
        controller = types.SimpleNamespace(GetHeadingAngle=lambda: heading.rad)
        player = types.SimpleNamespace(GetInputController=lambda: controller)
        in_fov = _bridge_method(self.source, "IsInApproxFov")
        head = (0.0, 0.0, 0.0)

        def at(bearing_deg: float, horizontal: float) -> tuple[float, float, float]:
            bearing = math.radians(bearing_deg)
            return (horizontal * math.sin(bearing), 0.0, horizontal * math.cos(bearing))

        # The review's witness: a 9.9 m sphere 10 m away at bearing 115 reaches the
        # 60-degree cone of heading 0. asin(0.99) is 81.9 degrees, so 142 is out.
        self.assertTrue(in_fov(player, head, at(115.0, 10.0), 9.9))
        self.assertFalse(in_fov(player, head, at(142.0, 10.0), 9.9))
        # A point is in view within 60 degrees either side.
        self.assertTrue(in_fov(player, head, at(-59.0, 10.0), 0.0))
        self.assertFalse(in_fov(player, head, at(61.0, 10.0), 0.0))
        # The heading h faces the compass heading -h: h = -pi/2 faces +X.
        heading.rad = -math.pi / 2
        self.assertTrue(in_fov(player, head, (10.0, 0.0, 0.0), 0.0))
        self.assertFalse(in_fov(player, head, (-10.0, 0.0, 0.0), 0.0))
        # Behind the player but within radius in x and z, straight above
        # included, or with no input controller: in view.
        self.assertTrue(in_fov(player, head, (-3.0, 10.0, 0.0), 3.0))
        self.assertTrue(in_fov(player, head, (0.0, 30.0, 0.0), 0.0))
        blind = types.SimpleNamespace(GetInputController=lambda: None)
        self.assertTrue(in_fov(blind, head, (-10.0, 0.0, 0.0), 0.0))

    def test_the_head_falls_back_above_the_position(self) -> None:
        code = _method_code(self.source, "PlayerHeadPosition")
        self.assertIn("int bone = player.GetBoneIndexByName(VISIBILITY_HEAD_BONE);", code)
        self.assertEqual(
            _if_body(code, "bone < 0").strip(),
            "return player.GetPosition() + Vector(0.0, VISIBILITY_HEAD_FALLBACK_M, 0.0);",
        )
        self.assertIn("return player.GetBonePositionWS(bone);", code)
        self.assertIn('protected const string VISIBILITY_HEAD_BONE = "Head";', self.source)
        constants = {name: float(value) for name, value in _FLOAT_CONST.findall(self.source)}
        self.assertEqual(constants["VISIBILITY_HEAD_FALLBACK_M"], 1.6)

    def test_the_row_class_holds_primitives_only(self) -> None:
        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        self.assertEqual(
            _class_members(messages, "MCPPlayerVisibility"),
            [
                ("string", "uid"),
                ("float", "distance"),
                ("bool", "in_fov"),
                ("bool", "line_of_sight"),
                ("bool", "visible"),
            ],
        )
        self.assertEqual(
            _class_members(messages, "MCPResult").count(
                ("ref array<ref MCPPlayerVisibility>", "players_visibility")
            ),
            1,
        )


class ReadsPruneTest(unittest.TestCase):
    def test_server_perf_keeps_its_scalars_and_counts_for_itself_only(self) -> None:
        wire = {
            "ok": 1,
            "avg_fps": 0.0,
            "min_fps": 0,
            "insufficient_samples": 1,
            "counts": {"players": 0, "ai": 0, "infected": 0, "animals": 0, "vehicles": 0, "items": 0},
            "points": [],
            "players_visibility": [],
        }
        kept = result_prune.prune_unfilled_fields("server_perf", dict(wire))
        for field in ("avg_fps", "min_fps", "insufficient_samples", "counts"):
            with self.subTest(field=field):
                self.assertIn(field, kept)
        self.assertNotIn("points", kept)
        self.assertNotIn("players_visibility", kept)
        other = result_prune.prune_unfilled_fields("world_time_get", dict(wire))
        for field in ("avg_fps", "min_fps", "insufficient_samples"):
            with self.subTest(field=field):
                self.assertNotIn(field, other)

    def test_nav_path_query_keeps_a_filled_path_and_drops_a_refusal_empty(self) -> None:
        filled = {
            "ok": 1,
            "points": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            "length_m": 0.0,
            "from_snapped": [1.0, 2.0, 3.0],
            "to_snapped": [4.0, 5.0, 6.0],
            "counts": {},
        }
        kept = result_prune.prune_unfilled_fields("nav_path_query", dict(filled))
        for field in ("points", "length_m", "from_snapped", "to_snapped"):
            with self.subTest(field=field):
                self.assertIn(field, kept)
        self.assertNotIn("counts", kept)
        refusal = result_prune.prune_unfilled_fields(
            "nav_path_query",
            {"ok": 0, "error": "no_path", "points": [], "from_snapped": [], "to_snapped": []},
        )
        self.assertEqual(refusal, {"ok": 0, "error": "no_path"})
        self.assertNotIn(
            "length_m", result_prune.prune_unfilled_fields("surface_query", {"ok": 1, "length_m": 0.0})
        )

    def test_players_visibility_keeps_its_empty_list(self) -> None:
        kept = result_prune.prune_unfilled_fields(
            "players_visibility", {"ok": 1, "players_visibility": [], "counts": {}}
        )
        self.assertEqual(kept, {"ok": 1, "players_visibility": []})
        self.assertNotIn(
            "players_visibility",
            result_prune.prune_unfilled_fields("server_perf", {"ok": 1, "players_visibility": []}),
        )


class ReadsToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def _call(self, name: str, bridge_answer: dict[str, object], **arguments: object):
        call = AsyncMock(return_value=bridge_answer)
        with patch.object(self.runtime, "call_bridge", new=call):
            result = _content_json(await self.app.call_tool(name, dict(arguments)))
        return result, call

    async def _refused(self, name: str, arguments: dict[str, object]) -> str:
        call = AsyncMock(return_value={"ok": 1})
        with patch.object(self.runtime, "call_bridge", new=call):
            with self.assertRaises(ToolError) as raised:
                await self.app.call_tool(name, arguments)
        call.assert_not_awaited()
        return str(raised.exception)

    async def _description(self, name: str) -> str:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        return tools[name].description or ""

    async def test_server_perf_waits_past_its_window(self) -> None:
        answer = {"ok": 1, "avg_fps": 59.8, "min_fps": 57, "insufficient_samples": 0}
        cases = (
            ({}, 10.0, max(DEFAULT_TOOL_TIMEOUT_S, 20.0)),
            ({"window_s": 60.0}, 60.0, 70.0),
            ({"window_s": 1, "timeout_s": 1.0}, 1.0, 11.0),
            ({"window_s": 5.0, "timeout_s": 120.0}, 5.0, 120.0),
        )
        for arguments, window, timeout in cases:
            with self.subTest(arguments=arguments):
                result, call = await self._call("server_perf", dict(answer), **arguments)
                call.assert_awaited_once_with("server_perf", {"window_s": window}, "server", timeout)
                self.assertEqual(result["avg_fps"], 59.8)
                self.assertEqual(result["min_fps"], 57)

    async def test_insufficient_samples_carries_no_rate(self) -> None:
        counts = {"players": 1, "ai": 0, "infected": 10, "animals": 2, "vehicles": 1, "items": 300}
        bridge_answer = {
            "ok": 1,
            "avg_fps": 0.0,
            "min_fps": 0,
            "insufficient_samples": 1,
            "counts": dict(counts),
        }
        handed = copy.deepcopy(bridge_answer)
        result, _call = await self._call("server_perf", bridge_answer, window_s=1.0)
        self.assertNotIn("avg_fps", result)
        self.assertNotIn("min_fps", result)
        self.assertEqual(result["insufficient_samples"], 1)
        self.assertEqual(result["counts"], counts)
        # The answer the tool was handed is not rewritten.
        self.assertEqual(bridge_answer, handed)

    async def test_a_bad_window_never_reaches_the_bridge(self) -> None:
        # The handler: a number out of range or not finite is bad_args.
        for window in (0, 0.5, 60.5, 61, -1.0, math.nan, math.inf):
            with self.subTest(window=window):
                text = await self._refused("server_perf", {"window_s": window})
                self.assertIn("bad_args", text)
                self.assertIn("window_s", text)
        # The typed schema (StrictFloat): a bool, a string or a null fails
        # FastMCP's validation before the handler runs, without bad_args.
        for window in (True, "10", None):
            with self.subTest(window=window):
                text = await self._refused("server_perf", {"window_s": window})
                self.assertIn("validation error for server_perfArguments", text)
                self.assertIn("window_s", text)
                self.assertNotIn("bad_args", text)

    async def test_an_extra_key_is_dropped_by_the_tool_and_refused_by_the_ingress(self) -> None:
        public = {
            "server_perf": {"window_s": 10.0},
            "nav_path_query": {"from": [10.0, 5.0, 20.0], "to": [60.0, 5.0, 20.0]},
            "players_visibility": {"pos": [10.0, 5.0, 20.0]},
        }
        float_key = {
            "server_perf": "window_s",
            "nav_path_query": "snap_radius",
            "players_visibility": "radius",
        }
        for command, arguments in public.items():
            with self.subTest(command=command):
                _result, call = await self._call(command, {"ok": 1}, **arguments, extra=1)
                self.assertEqual(call.await_args.args[:2], (command, WIRE_ARGS[command]))
                self.assertEqual(
                    loopback.validate_command_args(command, {**WIRE_ARGS[command], "extra": 1}),
                    (False, "bad_args"),
                )
                # The ingress refuses a wrong type as bad_args too.
                for wrong in (True, "1", None):
                    self.assertEqual(
                        loopback.validate_command_args(
                            command, {**WIRE_ARGS[command], float_key[command]: wrong}
                        ),
                        (False, "bad_args"),
                    )

    async def test_nav_path_query_sends_from_and_regroups_the_points(self) -> None:
        bridge_answer = {
            "ok": 1,
            "points": [10.0, 5.0, 20.0, 35.0, 5.2, 21.0, 60.0, 5.0, 20.0],
            "length_m": 50.03,
            "from_snapped": [10.0, 5.0, 20.0],
            "to_snapped": [60.0, 5.0, 20.0],
        }
        result, call = await self._call(
            "nav_path_query",
            bridge_answer,
            **{"from": [10, 5, 20], "to": [60.0, 5.0, 20.0]},
        )
        call.assert_awaited_once_with(
            "nav_path_query",
            {"from": [10.0, 5.0, 20.0], "to": [60.0, 5.0, 20.0], "snap_radius": 2.0},
            "server",
            DEFAULT_TOOL_TIMEOUT_S,
        )
        self.assertEqual(
            result["points"], [[10.0, 5.0, 20.0], [35.0, 5.2, 21.0], [60.0, 5.0, 20.0]]
        )
        self.assertEqual(result["length_m"], 50.03)
        self.assertEqual(result["from_snapped"], [10.0, 5.0, 20.0])
        tool = self.app._tool_manager.get_tool("nav_path_query")
        self.assertIn("from", tool.parameters["properties"])
        self.assertNotIn("from_pos", tool.parameters["properties"])
        self.assertEqual(sorted(tool.parameters["required"]), ["from", "to"])

    async def test_nav_path_query_refusals_pass_through(self) -> None:
        for error in ("no_navmesh", "no_navmesh_at_from", "no_navmesh_at_to", "no_path"):
            with self.subTest(error=error):
                result, _call = await self._call(
                    "nav_path_query",
                    {"ok": 0, "error": error},
                    **{"from": [0.0, 0.0, 0.0], "to": [1.0, 0.0, 1.0]},
                )
                self.assertEqual(result, {"ok": 0, "error": error})
        # A points list that is not whole triples is left as it came.
        odd, _call = await self._call(
            "nav_path_query",
            {"ok": 1, "points": [1.0, 2.0]},
            **{"from": [0.0, 0.0, 0.0], "to": [1.0, 0.0, 1.0]},
        )
        self.assertEqual(odd["points"], [1.0, 2.0])

    async def test_nav_path_query_bad_arguments_never_reach_the_bridge(self) -> None:
        base = {"from": [0.0, 0.0, 0.0], "to": [1.0, 0.0, 1.0]}
        cases = (
            ({**base, "snap_radius": 0.0}, "snap_radius"),
            ({**base, "snap_radius": -1.0}, "snap_radius"),
            ({**base, "snap_radius": 50.5}, "snap_radius"),
            ({**base, "snap_radius": math.nan}, "snap_radius"),
            ({**base, "from": [0.0, 0.0]}, "from"),
            ({**base, "from": [0.0, math.inf, 0.0]}, "from"),
            ({**base, "to": [0.0, 0.0, math.nan]}, "to"),
        )
        for arguments, field in cases:
            with self.subTest(arguments=arguments):
                text = await self._refused("nav_path_query", arguments)
                self.assertIn("bad_args", text)
                self.assertIn(field, text)
        _ok, call = await self._call("nav_path_query", {"ok": 1}, **{**base, "snap_radius": 50})
        self.assertEqual(call.await_args.args[1]["snap_radius"], 50.0)

    async def test_players_visibility_forwards_pos_and_radius(self) -> None:
        rows = [
            {"uid": "76561198000000001", "distance": 20.0, "in_fov": 1, "line_of_sight": 1, "visible": 1}
        ]
        result, call = await self._call(
            "players_visibility", {"ok": 1, "players_visibility": rows}, pos=[1, 2, 3]
        )
        call.assert_awaited_once_with(
            "players_visibility",
            {"pos": [1.0, 2.0, 3.0], "radius": 0.0},
            "server",
            DEFAULT_TOOL_TIMEOUT_S,
        )
        self.assertEqual(result["players_visibility"], rows)
        empty, _call = await self._call(
            "players_visibility", {"ok": 1, "players_visibility": []}, pos=[1.0, 2.0, 3.0], radius=100
        )
        self.assertEqual(empty["players_visibility"], [])
        _timed, call = await self._call(
            "players_visibility", {"ok": 1, "players_visibility": []}, pos=[1.0, 2.0, 3.0], timeout_s=1.0
        )
        call.assert_awaited_once_with(
            "players_visibility", {"pos": [1.0, 2.0, 3.0], "radius": 0.0}, "server", 1.0
        )

    async def test_players_visibility_bad_arguments_never_reach_the_bridge(self) -> None:
        cases = (
            ({"pos": [0.0, 0.0]}, "pos"),
            ({"pos": [0.0, math.nan, 0.0]}, "pos"),
            ({"pos": [0.0, 0.0, 0.0], "radius": -0.5}, "radius"),
            ({"pos": [0.0, 0.0, 0.0], "radius": 100.5}, "radius"),
            ({"pos": [0.0, 0.0, 0.0], "radius": math.inf}, "radius"),
        )
        for arguments, field in cases:
            with self.subTest(arguments=arguments):
                text = await self._refused("players_visibility", arguments)
                self.assertIn("bad_args", text)
                self.assertIn(field, text)
                self.assertNotIn("bad_pos", text)

    async def test_the_descriptions_state_what_is_read_and_what_is_not_measured(self) -> None:
        fragments = {
            "server_perf": (
                "It changes nothing and needs no lease.",
                "no FPS API is asked",
                "a partial last second is dropped",
                "insufficient_samples is 1",
                "carries no avg_fps and no min_fps",
                "SceneGetEntitiesInBox, which takes what touches or overlaps the box",
                "each entry it returns counted once",
                "are not checked",
                "window_s + 10 s",
                "bridge_capability_missing",
                "arg_contract_mismatch",
            ),
            "nav_path_query": (
                "It changes nothing and needs no lease.",
                "Public arg is from (alias of from_pos)",
                "SampleNavmeshPosition",
                "FindPath",
                "both ends included",
                "not measured in game yet",
                "no_navmesh_at_to",
                "no navmesh the filter admits within snap_radius",
                "no_path",
            ),
            "players_visibility": (
                "It changes nothing and needs no lease",
                "{uid (as query_all_players names it), distance",
                "in_fov is an approximation",
                "any part of a sphere of radius around pos",
                "asin(radius / the horizontal distance)",
                "GetHeadingAngle, not measured in game yet",
                "scene_raycast's default ray",
                "from the head to pos only",
                "visible false is no guarantee that nobody sees part of a volume",
                "visible is in_fov and line_of_sight both",
                "No player connected is ok with an empty list.",
            ),
        }
        for name, expected in fragments.items():
            description = await self._description(name)
            self.assertFalse(description.startswith(LEASE_TOOL_LINE), description)
            self.assertNotIn("Requires a lease", description)
            for fragment in expected:
                with self.subTest(tool=name, fragment=fragment):
                    self.assertIn(fragment, description)


class _ListingRuntime:
    """Enough of a client runtime for tools/list: the config and the lease token."""

    def __init__(self, config: ServerConfig, **_kwargs: object) -> None:
        self.config = config
        self.active_lease_token = None
        self._registered_tool_names = None


class ReadsCatalogTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_compact_catalog_lists_them_before_a_lease(self) -> None:
        with patch("dayz_mcp.server.ClientRuntime", _ListingRuntime):
            app, runtime = build_app(
                ServerConfig(
                    mode="client",
                    key="k",
                    port=12345,
                    client_platform="codex",
                    log_sink=lambda _message: None,
                )
            )
        self.assertIsNone(runtime.active_lease_token)
        listed = {tool.name: tool for tool in await app.list_tools()}
        compact = {
            "server_perf": "Measure the server's frame rate over window_s seconds (1 to 60, "
            "default 10) and count its entities. …",
            "nav_path_query": "Find the engine's navmesh path for a walking survivor between "
            "two world points. It changes nothing and needs no lease. …",
            "players_visibility": "Read which connected players could see a world point. …",
        }
        for name, description in compact.items():
            with self.subTest(tool=name):
                self.assertIn(name, server._INITIAL_READ_TOOL_NAMES)
                self.assertIn(name, listed)
                self.assertEqual(listed[name].description, description)


class NavPathRoundTripTest(unittest.IsolatedAsyncioTestCase):
    """nav_path_query through the embedded runtime, the ingress and a polling fake."""

    async def asyncSetUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        self.runtime.start_loopback()
        bind_both_peers(self.runtime.state)
        self.peers: list[FakePeer] = []

    async def asyncTearDown(self) -> None:
        await asyncio.gather(*(asyncio.to_thread(peer.stop) for peer in self.peers))
        await asyncio.to_thread(self.runtime.stop_loopback)

    def _peer(self, peer: str, responder=None) -> FakePeer:
        fake = FakePeer(
            self.runtime,
            "test-key",
            peer,
            version=f"{EXPECTED_BRIDGE_VERSION}~1.29.0",
            responder=responder,
        )
        fake.start()
        self.peers.append(fake)
        return fake

    @slow_test
    async def test_a_polled_path_arrives_regrouped_with_the_other_refs_pruned(self) -> None:
        def answer(command: dict[str, object]) -> dict[str, object]:
            result: dict[str, object] = {"id": command["id"], "ok": 1, "cmd": command["cmd"]}
            if command["cmd"] == "nav_path_query":
                # The flat MCPResult: the verb's own members plus empty others.
                result["points"] = [0.0, 1.0, 0.0, 3.0, 1.0, 4.0]
                result["length_m"] = 5.0
                result["from_snapped"] = [0.0, 1.0, 0.0]
                result["to_snapped"] = [3.0, 1.0, 4.0]
                result["counts"] = {}
                result["players_visibility"] = []
                result["avg_fps"] = 0.0
            return result

        server_peer = self._peer("server", answer)
        self._peer("client")
        deadline = time.monotonic() + 2.0
        while not server.compute_bridge_ready(self.runtime.status())["ready"]:
            if time.monotonic() > deadline:
                self.fail("bridge never became ready")
            await asyncio.sleep(0.02)

        result = _content_json(
            await self.app.call_tool(
                "nav_path_query",
                {"from": [0.0, 1.0, 0.0], "to": [3.0, 1.0, 4.0], "snap_radius": 1.5, "timeout_s": 2.0},
            )
        )

        self.assertEqual(result["ok"], 1)
        self.assertEqual(result["points"], [[0.0, 1.0, 0.0], [3.0, 1.0, 4.0]])
        self.assertEqual(result["length_m"], 5.0)
        for field in ("counts", "players_visibility", "avg_fps"):
            with self.subTest(field=field):
                self.assertNotIn(field, result)
        sent = [item for item in server_peer.commands_seen if item["cmd"] == "nav_path_query"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(
            sent[0]["args"],
            {"from": [0.0, 1.0, 0.0], "to": [3.0, 1.0, 4.0], "snap_radius": 1.5},
        )


if __name__ == "__main__":
    unittest.main()
