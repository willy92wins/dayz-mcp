"""world_time_get: a lease-free read of the server's in-game clock.

The verb answers World.GetDate (world.c:33) in its own reply member,
world_time, and writes nothing. World has SetTimeMultiplier and no getter
(world.c:19), so the multiplier is not read. GetDate can report minute=60
(fb-20260911-230929-311d), so the tool normalizes world_time the way
world_time_set normalizes its applied echo and keeps the raw read beside it.

These tests pin the wire contract, the lease partition, the compact catalog,
the prune contract and the Enforce source the game will compile. They do not
launch DayZ.
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, result_prune, server
from dayz_mcp.server import (
    DEFAULT_TOOL_TIMEOUT_S,
    EXPECTED_BRIDGE_VERSION,
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
from tests.fence_helpers import bind_both_peers
from tests.mcp_helpers import FakePeer, _content_json


COMMAND = "world_time_get"
FIELD = "world_time"
SCRIPTS = addon_root() / "scripts"
BRIDGE_PATH = SCRIPTS / "5_Mission" / "MCPBridge.c"
MESSAGES_PATH = SCRIPTS / "5_Mission" / "MCPMessages.c"
CENSUS_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "bridge_capabilities_v1.json"
)
VANILLA_WORLD = Path(r"P:\scripts\3_Game\global\world.c")
DISPATCH = "protected bool DispatchWorldTimeGet(MCPCommand command, MCPResult result)"
DISPATCH_CHAIN = "protected void Dispatch(MCPCommand command)"

CLOCK = {"year": 2026, "month": 9, "day": 30, "hour": 14, "minute": 5}
DATE = {"year": 2026, "month": 9, "day": 28, "hour": 9, "minute": 0}
IDENTITY = {
    "platform": "codex",
    "pid": 11,
    "ppid": 1,
    "started_at_utc": "2026-07-15T00:00:00Z",
    "session_id": "A",
    "task_label": "clock",
}

_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')
_MEMBER_RE = re.compile(
    r"(?m)(?<![A-Za-z0-9_>])(?P<type>(?:ref\s+)?[A-Za-z_][A-Za-z0-9_]*(?:\s*<[^;{}]+>)?)"
    r"\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*;"
)
_LOCAL_DECLARATION = re.compile(
    r"^\s*(?:int|World|MCPWorldTime)\s+(\w+)\s*(?:=[^;]*)?;\s*$"
)
_SERVER_CAPS = re.compile(
    r'protected const string SERVER_CAPABILITIES = ((?:"[^"\n]*"(?: \+ )?)+);'
)


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


def _without_comments(source: str) -> str:
    def replace(match: re.Match[str]) -> str:
        text = match.group(0)
        return text if text.startswith('"') else re.sub(r"[^\n]", " ", text)

    return _COMMENT_OR_STRING.sub(replace, source)


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


def _server_capabilities(source: str) -> list[str]:
    declaration = _SERVER_CAPS.search(source)
    if declaration is None:
        raise AssertionError("SERVER_CAPABILITIES declaration not found")
    return "".join(re.findall(r'"([^"\n]*)"', declaration.group(1))).split(",")


class _ListingRuntime:
    """Enough of a client runtime for tools/list: the config and the lease token."""

    def __init__(self, config: ServerConfig, **_kwargs: object) -> None:
        self.config = config
        self.active_lease_token = None
        self._registered_tool_names = None


def _client_app(**fields: object):
    base: dict[str, object] = dict(
        mode="client", key="k", port=12345, log_sink=lambda _message: None
    )
    base.update(fields)
    with patch("dayz_mcp.server.ClientRuntime", _ListingRuntime):
        return build_app(ServerConfig(**base))


class WorldTimeGetIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[dict[str, object]] = []
        self.state = loopback.ServerState("test-key")
        bind_both_peers(self.state)

    def _coordinate(self) -> None:
        self.state.coordination = SessionCoordinator(
            token_fn=lambda: "token",
            id_fn=lambda: "lease",
            audit=lambda event: self.events.append(event) or True,
            cleanup=lambda session, lease, reason, active: self.state.cleanup_owner(
                session, lease, reason, active
            ),
        )

    def test_server_read_that_needs_no_lease(self) -> None:
        self.assertIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertNotIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "server")
        self.assertIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertFalse(command_requires_lease(COMMAND))
        status, body = self.state.enqueue_command(COMMAND, {})
        self.assertEqual((status, body["peer"], body["cmd"]), (200, "server", COMMAND))
        self.assertEqual(
            self.state.enqueue_command(COMMAND, {}, peer="client"),
            (400, {"error": "bad_peer"}),
        )

    def test_the_wire_carries_no_argument(self) -> None:
        variants, delegated = loopback._COMMAND_ARG_SCHEMAS[COMMAND]
        self.assertIsNone(delegated)
        self.assertEqual(variants, ((frozenset(), frozenset(), {}),))
        self.assertEqual(loopback.validate_command_args(COMMAND, {}), (True, None))
        for args in ({"timeout_s": 1.0}, dict(DATE), {"time_multiplier": 1.0}):
            with self.subTest(args=args):
                self.assertEqual(
                    self.state.enqueue_command(COMMAND, args),
                    (400, {"error": "bad_args"}),
                )

    def test_a_session_without_a_lease_reads_and_cannot_set(self) -> None:
        self._coordinate()
        status, body = self.state.enqueue_command(
            COMMAND, {}, "server", identity_payload=IDENTITY
        )
        self.assertEqual((status, body["peer"], body["cmd"]), (200, "server", COMMAND))
        # Negative control: the set beside it still needs the lease.
        refused = self.state.enqueue_command(
            "world_time_set", dict(DATE), "server", identity_payload=IDENTITY
        )
        self.assertEqual(refused, (423, {"error": "lease_required"}))
        decisions = [event.get("decision") for event in self.events]
        self.assertIn("read", decisions)
        self.assertIn("lease_required", decisions)

    def test_a_retail_process_does_not_block_the_read(self) -> None:
        # Retail quarantine gates mutations only; this verb is not one.
        self._coordinate()
        self.state.retail_probe = lambda: {
            "known": True,
            "processes": [{"pid": 44, "name": "DayZ_x64.exe"}],
        }
        status, _body = self.state.enqueue_command(
            COMMAND, {}, "server", identity_payload=IDENTITY
        )
        self.assertEqual(status, 200)


class WorldTimeGetCensusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = BRIDGE_PATH.read_text(encoding="utf-8")

    def test_server_caps_and_dispatch_name_the_command(self) -> None:
        caps = _server_capabilities(self.source)
        self.assertIn(COMMAND, caps)
        self.assertEqual(caps, sorted(caps))
        dispatch = _method_body(self.source, DISPATCH_CHAIN)
        branch = _if_body(dispatch, f'command.cmd == "{COMMAND}"')
        self.assertEqual(branch.strip(), "postNow = DispatchWorldTimeGet(command, result);")

    def test_daemon_map_and_fixture_name_the_tool_and_the_version_and_hash_stay(
        self,
    ) -> None:
        self.assertEqual(_BRIDGE_COMMAND_TOOLS["server"].get(COMMAND), COMMAND)
        self.assertNotIn(COMMAND, _BRIDGE_COMMAND_TOOLS["client"])
        fixture = json.loads(CENSUS_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(fixture["peers"]["server"].get(COMMAND), COMMAND)
        self.assertNotIn(COMMAND, fixture["peers"]["client"])
        # A no-argument verb adds nothing to the arg contract, and a new verb
        # does not bump the bridge version.
        self.assertNotIn(COMMAND, server.SERVER_ARG_CONTRACT)
        self.assertEqual(server.EXPECTED_SERVER_ARG_CONTRACT_HASH, "e5a0ed288dbae72f")
        self.assertIn('SERVER_ARG_CONTRACT_HASH = "e5a0ed288dbae72f"', self.source)
        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        self.assertIn('const string MCP_BRIDGE_VERSION = "11";', messages)

    def test_stale_server_census_names_this_tool(self) -> None:
        announced = sorted(_BRIDGE_COMMAND_TOOLS["server"])
        announced.remove(COMMAND)
        registered = frozenset(
            tool for tool in _BRIDGE_COMMAND_TOOLS["server"].values() if tool
        )
        result = server._compare_bridge_capabilities(
            "server",
            {
                "state": "announced",
                "reason": "ok",
                "announced_commands": announced,
                "announced_arg_contract_hash": server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
            },
            registered,
        )
        self.assertEqual(result["state"], "mismatch")
        self.assertEqual(result["reason"], "census_disagrees_with_registered_tools")
        self.assertEqual(result["registered_without_announced_command"], [COMMAND])


class WorldTimeGetEnforceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = BRIDGE_PATH.read_text(encoding="utf-8")
        self.body = _method_body(self.source, DISPATCH)
        self.code = _without_comments(self.body)

    def test_the_dispatch_reads_get_date_and_writes_nothing(self) -> None:
        null_world = _if_body(self.code, "!world")
        self.assertIn("result.ok = false;", null_world)
        self.assertIn('result.error = "world_unavailable";', null_world)
        self.assertIn("return true;", null_world)
        self.assertEqual(
            set(re.findall(r'result\.error = "(\w+)"', self.code)), {"world_unavailable"}
        )
        tokens = (
            "World world = GetGame().GetWorld();",
            "if (!world)",
            "world.GetDate(year, month, day, hour, minute);",
            "worldTime = new MCPWorldTime();",
            "worldTime.year = year;",
            "worldTime.month = month;",
            "worldTime.day = day;",
            "worldTime.hour = hour;",
            "worldTime.minute = minute;",
            "result.world_time = worldTime;",
            "result.ok = true;",
        )
        positions = []
        for token in tokens:
            with self.subTest(token=token):
                self.assertEqual(self.code.count(token), 1)
            positions.append(self.code.index(token))
        self.assertEqual(positions, sorted(positions))
        for forbidden in (
            "SetDate",
            "SetTimeMultiplier",
            "GetTimeMultiplier",
            "command.args",
            "result.applied",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.code)

    def test_compile_hazards_locals_on_top_and_declared_once(self) -> None:
        self.assertEqual(
            _locals_declared_at_top(self.body),
            ["year", "month", "day", "hour", "minute", "world", "worldTime"],
        )
        self.assertIsNone(re.search(r"\?[^;]*:", self.code), "no ternary in Enforce")
        # The helper can fail: a late or a repeated declaration is caught.
        with self.assertRaises(AssertionError):
            _locals_declared_at_top("\t\tint a = 0;\n\t\ta = 1;\n\t\tint b = 0;\n")
        with self.assertRaises(AssertionError):
            _locals_declared_at_top("\t\tint a;\n\t\tint a;\n")

    def test_reply_class_is_five_ints_owned_by_the_result(self) -> None:
        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        members = _class_members(messages, "MCPWorldTime")
        self.assertEqual(
            members,
            [("int", "year"), ("int", "month"), ("int", "day"), ("int", "hour"), ("int", "minute")],
        )
        self.assertEqual(
            _class_members(messages, "MCPResult").count(("ref MCPWorldTime", FIELD)), 1
        )
        self.assertLess(messages.index("class MCPWorldTime"), messages.index("class MCPResult"))
        self.assertNotRegex(
            messages[messages.index("class MCPWorldTime") : messages.index("class MCPResult")],
            r"\btime_multiplier\b",
        )


class VanillaWorldPremiseTest(unittest.TestCase):
    """The premise, read from the vanilla scripts: it holds with or without this verb."""

    def test_vanilla_world_reads_the_date_and_has_no_multiplier_getter(self) -> None:
        if not VANILLA_WORLD.is_file():
            self.skipTest("vanilla world.c not mounted")
        world = VANILLA_WORLD.read_text(encoding="utf-8")
        self.assertRegex(
            world,
            r"proto\s+void\s+GetDate\(out int year, out int month, out int day, "
            r"out int hour, out int minute\);",
        )
        self.assertIn("proto native void SetTimeMultiplier(float timeMultiplier);", world)
        self.assertNotIn("GetTimeMultiplier", world)


class WorldTimeGetPruneTest(unittest.TestCase):
    def test_the_reply_is_prunable_and_kept_for_its_verb(self) -> None:
        self.assertIn(FIELD, result_prune.PRUNABLE_FIELDS)
        self.assertNotIn(FIELD, result_prune.OWNED_SCALAR_FIELDS)
        unfilled = result_prune.prune_unfilled_fields("world_time_set", {"ok": 1, FIELD: {}})
        self.assertNotIn(FIELD, unfilled)
        midnight = {"year": 2026, "month": 1, "day": 1, "hour": 0, "minute": 0}
        kept = result_prune.prune_unfilled_fields(COMMAND, {"ok": 1, FIELD: dict(midnight)})
        self.assertEqual(kept[FIELD], midnight)

    def test_store_and_take_keep_the_clock_and_drop_the_other_refs(self) -> None:
        state = loopback.ServerState("test-key")
        status, enqueued = state.enqueue_command(COMMAND, {})
        self.assertEqual(status, 200)
        command_id = enqueued["id"]
        stored_status, _stored = state.store_result(
            {"id": command_id, "ok": 1, FIELD: dict(CLOCK), "applied": {}, "input_trigger": {}}
        )
        self.assertEqual(stored_status, 200)
        taken = state.take_result(command_id, remove=True)
        self.assertIsNotNone(taken)
        pruned = result_prune.prune_unfilled_fields(COMMAND, taken)
        self.assertEqual(pruned[FIELD], CLOCK)
        self.assertNotIn("applied", pruned)
        self.assertNotIn("input_trigger", pruned)


class WorldTimeGetToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def _call(self, bridge_answer: dict[str, object], **arguments: object):
        call = AsyncMock(return_value=bridge_answer)
        with patch.object(self.runtime, "call_bridge", new=call):
            result = _content_json(await self.app.call_tool(COMMAND, dict(arguments)))
        return result, call

    async def _description(self, name: str = COMMAND) -> str:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        return tools[name].description or ""

    async def test_the_tool_sends_no_argument_to_the_server(self) -> None:
        result, call = await self._call({"ok": 1, FIELD: dict(CLOCK)}, timeout_s=1.0)
        call.assert_awaited_once_with(COMMAND, {}, "server", 1.0)
        self.assertEqual(result[FIELD], CLOCK)
        self.assertEqual(result["world_time_echo"], CLOCK)
        self.assertIs(result["clock_normalized"], False)
        self.assertEqual(result["ok"], 1)
        self.assertNotIn("warnings", result)
        _default, default_call = await self._call({"ok": 1, FIELD: dict(CLOCK)})
        default_call.assert_awaited_once_with(COMMAND, {}, "server", DEFAULT_TOOL_TIMEOUT_S)

    async def test_a_bad_timeout_never_reaches_the_bridge(self) -> None:
        for timeout in (0.0, -1.0, 300.5):
            with self.subTest(timeout=timeout):
                call = AsyncMock(return_value={"ok": 1, FIELD: dict(CLOCK)})
                with patch.object(self.runtime, "call_bridge", new=call):
                    with self.assertRaises(ToolError) as raised:
                        await self.app.call_tool(COMMAND, {"timeout_s": timeout})
                self.assertIn("bad_timeout", str(raised.exception))
                call.assert_not_awaited()
        call = AsyncMock(return_value={"ok": 1, FIELD: dict(CLOCK)})
        with patch.object(self.runtime, "call_bridge", new=call):
            with self.assertRaises(ToolError):
                await self.app.call_tool(COMMAND, {"timeout_s": True})
        call.assert_not_awaited()

    async def test_a_minute_sixty_read_is_carried_and_the_raw_read_kept(self) -> None:
        cases = (
            (
                {"year": 2026, "month": 9, "day": 30, "hour": 8, "minute": 60},
                {"year": 2026, "month": 9, "day": 30, "hour": 9, "minute": 0},
            ),
            (
                {"year": 2026, "month": 9, "day": 30, "hour": 23, "minute": 60},
                {"year": 2026, "month": 10, "day": 1, "hour": 0, "minute": 0},
            ),
            (
                {"year": 2026, "month": 12, "day": 31, "hour": 23, "minute": 60},
                {"year": 2027, "month": 1, "day": 1, "hour": 0, "minute": 0},
            ),
        )
        for raw, carried in cases:
            with self.subTest(raw=raw):
                bridge_answer = {"ok": 1, FIELD: dict(raw)}
                expected_raw = copy.deepcopy(raw)
                result, _call = await self._call(bridge_answer, timeout_s=1.0)
                self.assertEqual(result[FIELD], carried)
                self.assertEqual(result["world_time_echo"], expected_raw)
                self.assertIs(result["clock_normalized"], True)
                self.assertEqual(result["ok"], 1)
                self.assertNotIn("warnings", result)
                # The bridge answer the tool was handed is not rewritten.
                self.assertEqual(bridge_answer[FIELD], expected_raw)

    async def test_an_incomplete_read_is_not_ok(self) -> None:
        cases = (
            ("member_absent", {"ok": 1}),
            ("member_not_an_object", {"ok": 1, FIELD: [2026, 9, 30, 14, 5]}),
            ("minute_absent", {"ok": 1, FIELD: {k: v for k, v in CLOCK.items() if k != "minute"}}),
            ("hour_string", {"ok": 1, FIELD: {**CLOCK, "hour": "14"}}),
            ("day_null", {"ok": 1, FIELD: {**CLOCK, "day": None}}),
            ("month_bool", {"ok": 1, FIELD: {**CLOCK, "month": True}}),
            ("year_fraction", {"ok": 1, FIELD: {**CLOCK, "year": 2026.5}}),
        )
        for label, bridge_answer in cases:
            with self.subTest(label):
                result, _call = await self._call(bridge_answer, timeout_s=1.0)
                self.assertEqual(result["ok"], 0)
                self.assertEqual(result["warnings"], ["world_time_incomplete"])

    async def test_a_not_ready_envelope_passes_through_untouched(self) -> None:
        envelope = {
            "ok": False,
            "error": "game_not_ready:reason=no_run",
            "code": "not_ready",
            "reason": "no_run",
            "next_step": {"tool": "bridge_status", "args": {}},
        }
        result, call = await self._call(dict(envelope), timeout_s=1.0)
        call.assert_awaited_once_with(COMMAND, {}, "server", 1.0)
        self.assertEqual(result, envelope)

    async def test_a_bridge_refusal_reaches_the_caller_through_the_tool(self) -> None:
        # The embedded runtime's own call_bridge and wait_for_result, with only
        # the loopback state and the readiness snapshot faked.
        ready = {
            "server_peer": {
                "last_poll_age_s": 0.1,
                "version_state": "ok",
                "capabilities": {"state": "match", "reason": "ok"},
            },
            "client_peer": {"last_poll_age_s": 0.1, "version_state": "ok"},
        }
        enqueued: list[tuple[str, dict[str, object], object]] = []

        def enqueue(cmd: str, args: dict[str, object], peer: object = None, **_kwargs: object):
            enqueued.append((cmd, dict(args), peer))
            return 200, {"id": 7}

        state = SimpleNamespace(
            enqueue_command=enqueue,
            take_result=lambda *_args, **_kwargs: {"ok": 0, "error": "world_unavailable"},
        )
        with patch.object(self.runtime, "status", return_value=ready), patch.object(
            self.runtime, "loopback", SimpleNamespace(state=state)
        ):
            with self.assertRaises(ToolError) as raised:
                await self.app.call_tool(COMMAND, {"timeout_s": 1.0})
        self.assertTrue(
            str(raised.exception).endswith(": world_unavailable"), str(raised.exception)
        )
        self.assertEqual(enqueued, [(COMMAND, {}, "server")])

    async def test_the_description_states_what_is_read_and_what_is_not(self) -> None:
        description = await self._description()
        self.assertFalse(description.startswith(LEASE_TOOL_LINE), description)
        self.assertNotIn("Requires a lease", description)
        for fragment in (
            "Read the server's in-game date and time: world_time {year, month, "
            "day, hour, minute} from World.GetDate.",
            "needs no lease",
            "GetDate can report minute=60",
            "the divmod rule world_time_set applies to its applied echo",
            "world_time_echo keeps the raw GetDate values",
            "clock_normalized",
            "world_time_incomplete",
            "The time multiplier cannot be read: World has SetTimeMultiplier and "
            "no getter",
            "applied, applied_echo, date_applied, multiplier_applied",
            "world_unavailable",
            "unknown_command",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, description)
        world_time_set = await self._description("world_time_set")
        self.assertIn("world_time_get", world_time_set)
        self.assertTrue(world_time_set.startswith(LEASE_TOOL_LINE))


class WorldTimeGetCatalogTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_compact_catalog_lists_it_before_a_lease(self) -> None:
        self.assertIn(COMMAND, server._INITIAL_READ_TOOL_NAMES)
        app, runtime = _client_app(client_platform="codex")
        self.assertIsNone(runtime.active_lease_token)
        listed = {tool.name: tool for tool in await app.list_tools()}
        self.assertIn(COMMAND, listed)
        self.assertNotIn("world_time_set", listed)
        self.assertEqual(
            listed[COMMAND].description,
            "Read the server's in-game date and time: world_time {year, month, "
            "day, hour, minute} from World.GetDate. …",
        )

    async def test_the_embedded_registry_has_it(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))
        self.assertIn(COMMAND, {tool.name for tool in app._tool_manager.list_tools()})


class WorldTimeGetRoundTripTest(unittest.IsolatedAsyncioTestCase):
    """The tool through the embedded runtime, the ingress and a polling fake."""

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
    async def test_a_polled_read_arrives_normalized_with_the_other_refs_pruned(self) -> None:
        def answer(command: dict[str, object]) -> dict[str, object]:
            result: dict[str, object] = {"id": command["id"], "ok": 1, "cmd": command["cmd"]}
            if command["cmd"] == COMMAND:
                # The flat MCPResult: the verb's own member plus empty others.
                result[FIELD] = {"year": 2026, "month": 9, "day": 30, "hour": 8, "minute": 60}
                result["applied"] = {}
                result["input_trigger"] = {}
            return result

        server_peer = self._peer("server", answer)
        self._peer("client")
        deadline = time.monotonic() + 2.0
        while not server.compute_bridge_ready(self.runtime.status())["ready"]:
            if time.monotonic() > deadline:
                self.fail("bridge never became ready")
            await asyncio.sleep(0.02)

        result = _content_json(await self.app.call_tool(COMMAND, {"timeout_s": 2.0}))

        self.assertEqual(result["ok"], 1)
        self.assertEqual(
            result[FIELD], {"year": 2026, "month": 9, "day": 30, "hour": 9, "minute": 0}
        )
        self.assertEqual(result["world_time_echo"]["minute"], 60)
        self.assertIs(result["clock_normalized"], True)
        self.assertNotIn("applied", result)
        self.assertNotIn("input_trigger", result)
        sent = [command for command in server_peer.commands_seen if command["cmd"] == COMMAND]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].get("args", {}), {})


if __name__ == "__main__":
    unittest.main()
