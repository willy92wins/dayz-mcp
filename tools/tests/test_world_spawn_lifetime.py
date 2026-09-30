"""fb-20260930-080543-bd28: world_spawn(lifetime_s) keeps a fixture through the CE cleanup.

Measured on DayZDiag 1.29: a world_spawn CivilianSedan vanished 3-27 s after the
player went ~114 m or more away and survived with the player ~44 m away. The
mission's CE deletes an entity whose economy lifetime has run out once no player
is within CleanupAvoidance (100 m, db/globals.xml), and CivilianSedan's
types.xml lifetime is 3 s. lifetime_s (seconds, in (0, 3888000]) overrides that
lifetime on the entity world_spawn creates. It travels with lifetime_s_set the
way fb-20260930-065425-8779 made every optional value travel, because an absent
key reaches Enforce as 0 or false.

These text contracts pin the layers: the tool validates lifetime_s and sends the
pair or neither; the loopback ingress accepts the flag only beside its value and
only as true; the bridge checks the flag and the range before CreateObjectEx,
calls SetLifetimeMax then SetLifetime on an EntityAI (and deletes and refuses
anything else), and the reply reads the entity's GetLifetime and GetLifetimeMax.
"""
from __future__ import annotations

import re
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, result_prune, server
from tests._addon_paths import addon_root
from tests.fence_helpers import bind_both_peers


MISSION = addon_root() / "scripts" / "5_Mission"
BRIDGE_PATH = MISSION / "MCPBridge.c"
MESSAGES_PATH = MISSION / "MCPMessages.c"
_POS = [10.0, 0.0, 20.0]
_SPAWN: dict[str, Any] = {"type": "CivilianSedan", "pos": [1.0, 2.0, 3.0], "flags": 0, "rotation": 0}
_PAIR: dict[str, Any] = {**_SPAWN, "lifetime_s": 600.0, "lifetime_s_set": True}
_BARE_KEYS = {"type", "pos", "flags", "rotation"}

_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')
_MEMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_>])(?P<type>(?:ref\s+)?[A-Za-z_][A-Za-z0-9_]*(?:\s*<[^;{}]+>)?)"
    r"\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*;"
)


def _without_comments(source: str) -> str:
    return _COMMENT_OR_STRING.sub(
        lambda match: match.group(0) if match.group(0).startswith('"') else " ",
        source,
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
    raise AssertionError("unterminated block")


def _method_body(source: str, signature: str) -> str:
    if source.count(signature) != 1:
        raise AssertionError(f"expected one {signature!r}, found {source.count(signature)}")
    return _block_after(source, source.index(signature))


def _if_block(body: str, condition: str) -> str:
    """The braced body of the one `if (<condition>)` in body."""
    marker = f"if ({condition})"
    if body.count(marker) != 1:
        raise AssertionError(f"expected one {marker!r}, found {body.count(marker)}")
    start = body.index(marker) + len(marker)
    if body[start:].lstrip()[:1] != "{":
        raise AssertionError(f"{marker!r} has no braced body")
    return _block_after(body, start)


def _class_members(source: str, class_name: str) -> list[tuple[str, str]]:
    clean = _without_comments(source)
    match = re.search(rf"\bclass\s+{re.escape(class_name)}\b", clean)
    if match is None:
        raise AssertionError(f"class {class_name} is absent")
    body = _block_after(clean, match.end())
    return [(m.group("type"), m.group("name")) for m in _MEMBER_RE.finditer(body)]


class SpawnLifetimeEnforceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.messages_raw = MESSAGES_PATH.read_text(encoding="utf-8")
        cls.messages = _without_comments(cls.messages_raw)
        cls.bridge_raw = BRIDGE_PATH.read_text(encoding="utf-8")
        cls.bridge = _without_comments(cls.bridge_raw)

    @property
    def apply(self) -> str:
        return _method_body(
            self.bridge, "protected bool ApplySpawnLifetime(MCPCommand command, MCPResult result)"
        )

    def test_mcpargs_declares_the_value_and_its_flag(self) -> None:
        args = _method_body(self.messages, "class MCPArgs")
        self.assertEqual(len(re.findall(r"(?m)^\s*float\s+lifetime_s\s*;", args)), 1)
        self.assertEqual(len(re.findall(r"(?m)^\s*bool\s+lifetime_s_set\s*;", args)), 1)
        constructor = _method_body(args, "void MCPArgs()")
        # An absent flag must read false: nothing may default it to true.
        self.assertIsNone(re.search(r"\blifetime_s_set\s*=", constructor))
        # The sentinel is a second guard only; the range check refuses float.MAX.
        self.assertIn("lifetime_s = MCP_ARG_FLOAT_UNSET;", constructor)

    def test_the_bridge_bound_equals_both_python_copies(self) -> None:
        declared = re.findall(r"protected const float SPAWN_LIFETIME_MAX_S = ([0-9.]+);", self.bridge)
        self.assertEqual(len(declared), 1)
        self.assertEqual(float(declared[0]), 3888000.0)
        self.assertEqual(server.WORLD_SPAWN_LIFETIME_MAX_S, 3888000.0)
        self.assertEqual(loopback._WORLD_SPAWN_LIFETIME_MAX_S, server.WORLD_SPAWN_LIFETIME_MAX_S)

    def test_the_flag_and_the_range_are_checked_before_create(self) -> None:
        validate = _method_body(self.bridge, "protected MCPSpawnValidation ValidateSpawnArgs(MCPArgs args)")
        check = _if_block(validate, "args.lifetime_s_set")
        self.assertIn(
            "if (!IsFiniteFloat(args.lifetime_s) || args.lifetime_s <= 0.0 || "
            "args.lifetime_s > SPAWN_LIFETIME_MAX_S)",
            check,
        )
        self.assertIn('validation.error = "bad_lifetime";', check)
        self.assertIn("return validation;", check)
        self.assertEqual(
            validate.count("args.lifetime_s"),
            check.count("args.lifetime_s") + 1,  # + the flag in the condition
            "lifetime_s read outside its flag block",
        )
        self.assertLess(validate.index("if (args.lifetime_s_set)"), validate.index("validation.ok = true;"))
        spawn = _method_body(self.bridge, "protected bool DispatchWorldSpawn(")
        self.assertLess(spawn.index("ValidateSpawnArgs(command.args)"), spawn.index("GetGame().CreateObjectEx("))

    def test_dispatch_applies_the_lifetime_once_the_spawn_is_queued(self) -> None:
        dispatch = _method_body(self.bridge, "protected void Dispatch(MCPCommand command)")
        branch = _if_block(dispatch, 'command.cmd == "world_spawn"')
        self.assertLess(
            branch.index("postNow = DispatchWorldSpawn(command, result);"), branch.index("if (!postNow)")
        )
        queued = _if_block(branch, "!postNow")
        self.assertEqual(queued.strip(), "postNow = ApplySpawnLifetime(command, result);")
        self.assertEqual(self.bridge.count("ApplySpawnLifetime(command, result)"), 1)
        # The helper lives outside the pinned DispatchWorldSpawn region
        # (tests/test_task9_spawn_phase_markers.py), which stays lifetime-free.
        start = self.bridge_raw.index("protected bool DispatchWorldSpawn")
        end = self.bridge_raw.index("protected bool DispatchObjectDelete", start)
        self.assertNotIn("ifetime", self.bridge_raw[start:end])
        helper = self.bridge_raw.index("protected bool ApplySpawnLifetime(")
        self.assertFalse(start <= helper < end)

    def test_an_entityai_gets_the_maximum_then_the_remaining_lifetime(self) -> None:
        gate = _if_block(self.apply, "!command.args || !command.args.lifetime_s_set")
        self.assertEqual(gate.strip(), "return false;")
        # The object comes from the spawn job just queued, not from the
        # never-cleared registry, where a reused id can name an older object.
        self.assertIn("job = m_Jobs.Get(command.id);", self.apply)
        self.assertEqual(_if_block(self.apply, "job").strip(), "spawned = job.subject;")
        self.assertNotIn("m_RuntimeObjects.Get(", self.apply)
        self.assertIn("entity = EntityAI.Cast(spawned);", self.apply)
        self.assertLess(
            self.apply.index("if (!command.args || !command.args.lifetime_s_set)"),
            self.apply.index("EntityAI.Cast("),
        )
        applied = _if_block(self.apply, "entity")
        self.assertLess(
            applied.index("entity.SetLifetimeMax(command.args.lifetime_s);"),
            applied.index("entity.SetLifetime(command.args.lifetime_s);"),
        )
        self.assertTrue(applied.rstrip().endswith("return false;"))
        # No other economy lifetime write anywhere in the bridge.
        self.assertEqual(self.bridge.count("SetLifetimeMax("), 1)
        self.assertEqual(self.bridge.count("SetLifetime("), 1)

    def test_an_object_that_is_not_an_entityai_is_deleted_and_refused(self) -> None:
        after = self.apply[self.apply.index("if (entity)") :]
        refusal = after[after.index("m_Jobs.Remove(command.id);") :]
        for statement in (
            "m_RuntimeObjects.Remove(command.id);",
            "GetGame().ObjectDelete(spawned);",
            "result.ok = false;",
            'result.error = "lifetime_unsupported";',
        ):
            with self.subTest(statement=statement):
                self.assertIn(statement, refusal)
        self.assertTrue(refusal.rstrip().endswith("return true;"))
        self.assertEqual(_if_block(self.apply, "spawned").strip(), "GetGame().ObjectDelete(spawned);")
        # The deleted object is not handed back: object_id stays 0 on the refusal.
        self.assertNotIn("object_id", self.apply)

    def test_the_reply_reads_the_entity_lifetime_when_flagged(self) -> None:
        post = _method_body(self.bridge, "protected void PostJobSuccess(MCPJob job)")
        readback = _if_block(post, "job.args.lifetime_s_set")
        self.assertIn("EntityAI lifetimeEntity = EntityAI.Cast(job.subject);", readback)
        filled = _if_block(readback, "lifetimeEntity")
        self.assertIn("result.lifetime = new MCPSpawnLifetime();", filled)
        self.assertIn("result.lifetime.remaining_s = lifetimeEntity.GetLifetime();", filled)
        self.assertIn("result.lifetime.max_s = lifetimeEntity.GetLifetimeMax();", filled)
        self.assertLess(post.index("if (job.args.lifetime_s_set)"), post.index("PostResult(result);"))
        self.assertEqual(self.bridge.count("result.lifetime"), filled.count("result.lifetime"))

    def test_the_read_back_payload_is_primitives_and_prunable(self) -> None:
        self.assertEqual(
            _class_members(self.messages_raw, "MCPSpawnLifetime"),
            [("float", "remaining_s"), ("float", "max_s")],
        )
        result = _class_members(self.messages_raw, "MCPResult")
        self.assertEqual(result.count(("ref MCPSpawnLifetime", "lifetime")), 1)
        self.assertLess(
            self.messages_raw.index("class MCPSpawnLifetime"), self.messages_raw.index("class MCPResult")
        )
        # Unfilled (no lifetime_s) the key leaves the reply; filled, it stays.
        self.assertIn("lifetime", result_prune.PRUNABLE_FIELDS)
        for unfilled in ({}, None):
            with self.subTest(unfilled=unfilled):
                self.assertEqual(
                    result_prune.prune_unfilled_fields("world_spawn", {"ok": 1, "lifetime": unfilled}),
                    {"ok": 1},
                )
        filled = {"ok": 1, "lifetime": {"remaining_s": 600.0, "max_s": 600.0}}
        self.assertEqual(result_prune.prune_unfilled_fields("world_spawn", filled), filled)

    def test_compile_hazards_one_declaration_each_and_no_local(self) -> None:
        self.assertEqual(
            re.findall(r"(?m)^\s*(MCPJob|Object|EntityAI)\s+(\w+)\s*;", self.apply),
            [("MCPJob", "job"), ("Object", "spawned"), ("EntityAI", "entity")],
        )
        post = _method_body(self.bridge, "protected void PostJobSuccess(MCPJob job)")
        self.assertEqual(len(re.findall(r"\bEntityAI\s+lifetimeEntity\b", post)), 1)
        for body in (self.apply, post):
            self.assertIsNone(re.search(r"\blocal\b", body))
            self.assertNotIn("CastTo(", body)


class SpawnLifetimeToolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def _sent(self, arguments: dict[str, Any]) -> dict[str, Any]:
        call = AsyncMock(return_value={"ok": 1})
        with patch.object(self.runtime, "call_bridge", new=call):
            await self.app.call_tool("world_spawn", {**arguments, "timeout_s": 1.0})
        call.assert_awaited_once()
        command, args = call.await_args.args[0], dict(call.await_args.args[1])
        self.assertEqual(command, "world_spawn")
        self.assertEqual(
            loopback.validate_command_args(command, args),
            (True, None),
            f"world_spawn sent {args!r}, which the ingress refuses",
        )
        return args

    async def test_lifetime_travels_with_its_flag_and_neither_without_it(self) -> None:
        for given, sent in ((600, 600.0), (0.5, 0.5), (3888000.0, 3888000.0), (3888000, 3888000.0)):
            with self.subTest(lifetime_s=given):
                args = await self._sent({"type": "CivilianSedan", "pos": _POS, "lifetime_s": given})
                self.assertEqual(set(args), _BARE_KEYS | {"lifetime_s", "lifetime_s_set"})
                self.assertEqual(args["lifetime_s"], sent)
                self.assertIs(type(args["lifetime_s"]), float)
                self.assertIs(args["lifetime_s_set"], True)
        for arguments in (
            {"type": "CivilianSedan", "pos": _POS},
            {"type": "CivilianSedan", "pos": _POS, "lifetime_s": None},
        ):
            with self.subTest(omitted=arguments):
                self.assertEqual(set(await self._sent(arguments)), _BARE_KEYS)

    async def test_out_of_range_nan_and_inf_are_refused_before_the_bridge(self) -> None:
        call = AsyncMock(return_value={"ok": 1})
        with patch.object(self.runtime, "call_bridge", new=call):
            for value in (0.0, 0, -1.0, 3888000.5, 1e12, float("nan"), float("inf"), float("-inf")):
                with self.subTest(lifetime_s=value):
                    with self.assertRaises(server.ToolError) as raised:
                        await self.app.call_tool(
                            "world_spawn",
                            {"type": "CivilianSedan", "pos": _POS, "lifetime_s": value, "timeout_s": 1.0},
                        )
                    message = str(raised.exception)
                    self.assertIn("bad_args: lifetime_s", message)
                    self.assertIn("greater than 0 and at most 3888000", message)
        call.assert_not_awaited()

    async def test_booleans_are_refused_before_the_bridge(self) -> None:
        call = AsyncMock(return_value={"ok": 1})
        with patch.object(self.runtime, "call_bridge", new=call):
            for value in (True, False):
                with self.subTest(lifetime_s=value):
                    with self.assertRaises(server.ToolError) as raised:
                        await self.app.call_tool(
                            "world_spawn",
                            {"type": "CivilianSedan", "pos": _POS, "lifetime_s": value, "timeout_s": 1.0},
                        )
                    self.assertIn("lifetime_s", str(raised.exception))
        call.assert_not_awaited()

    def test_description_names_the_cleanup_and_the_override(self) -> None:
        tool = self.app._tool_manager.get_tool("world_spawn")
        description = tool.description or ""
        for fragment in (
            "Once no player is within the mission's CleanupAvoidance, a spawned object lives "
            "only as long as its economy lifetime",
            "measured on 1.29, a CivilianSedan (types.xml lifetime 3 s, CleanupAvoidance 100 m) "
            "vanished 3-27 s after the player went ~114 m away and survived at ~44 m",
            "lifetime_s (seconds, above 0 and at most 3888000) overrides the economy lifetime "
            "for fixtures",
            "SetLifetimeMax then SetLifetime",
            "remaining_s (GetLifetime) and max_s (GetLifetimeMax)",
            "lifetime_unsupported",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, description)
        properties = tool.parameters["properties"]
        self.assertIn("lifetime_s", properties)
        self.assertNotIn("lifetime_s", tool.parameters.get("required", []))
        # The flag is wire-only: the caller never sets it by hand.
        self.assertNotIn("lifetime_s_set", properties)


class SpawnLifetimeIngressTest(unittest.TestCase):
    def test_the_pair_is_accepted_and_each_one_key_departure_refused(self) -> None:
        self.assertEqual(loopback.validate_command_args("world_spawn", dict(_SPAWN)), (True, None))
        for value in (600.0, 0.001, 3888000.0, 600):
            accepted = {**_SPAWN, "lifetime_s": value, "lifetime_s_set": True}
            with self.subTest(accepted=value):
                self.assertEqual(loopback.validate_command_args("world_spawn", accepted), (True, None))
        departures = {
            "value_without_flag": {**_SPAWN, "lifetime_s": 600.0},
            "flag_without_value": {**_SPAWN, "lifetime_s_set": True},
            "flag_false": {**_PAIR, "lifetime_s_set": False},
            "flag_int_1": {**_PAIR, "lifetime_s_set": 1},
            "flag_float_1": {**_PAIR, "lifetime_s_set": 1.0},
            "flag_string": {**_PAIR, "lifetime_s_set": "true"},
            "flag_null": {**_PAIR, "lifetime_s_set": None},
        }
        for label, refused in departures.items():
            with self.subTest(case=label):
                self.assertEqual(loopback.validate_command_args("world_spawn", refused), (False, "bad_args"))

    def test_a_value_outside_the_range_is_refused(self) -> None:
        # Positive control: the pair is a known shape, so each refusal below
        # comes from the value and not from an unknown key.
        self.assertEqual(loopback.validate_command_args("world_spawn", dict(_PAIR)), (True, None))
        for value in (0.0, 0, -1.0, 3888000.5, float("nan"), float("inf"), True, 10**400, "600", None):
            with self.subTest(lifetime_s=value):
                self.assertEqual(
                    loopback.validate_command_args("world_spawn", {**_PAIR, "lifetime_s": value}),
                    (False, "bad_args"),
                )

    def test_enqueue_refuses_an_unflagged_lifetime_before_the_queue(self) -> None:
        state = loopback.ServerState("test-key")
        bind_both_peers(state)
        status, body = state.enqueue_command("world_spawn", dict(_PAIR))
        self.assertEqual(status, 200, body)
        status, body = state.enqueue_command("world_spawn", {**_SPAWN, "lifetime_s": 600.0})
        self.assertEqual((status, body), (400, {"error": "bad_args"}))


if __name__ == "__main__":
    unittest.main()
