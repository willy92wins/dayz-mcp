"""input_describe: client read of a registered UAInput and its selected bind."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, server
from dayz_mcp.result_prune import prune_unfilled_fields
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease
from tests._addon_paths import addon_root
from tests.mcp_helpers import _content_json


COMMAND = "input_describe"
BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
MESSAGES_PATH = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
PRESSING_SENTENCE = (
    "Pressing the input is out of scope; use key_press for an OnKeyPress handler."
)
EXISTS_RULE = (
    "exists is true only when GetInputByName returns non-null and input.ID() >= 0"
)
PLACEHOLDER_RULE = "shared placeholder whose index is -1"
PROBE_WHY = "including that placeholder, so a caller can see why exists is false"
RETRACTED_CAVEAT = "an unknown name can also return exists true"
PROBE_FIELDS = (
    ("int", "input_id"),
    ("int", "name_hash"),
    ("int", "name_string_hash"),
    ("bool", "by_id_found"),
    ("bool", "by_id_same_hash"),
    ("bool", "in_active_inputs"),
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


def _class_body(source: str, class_name: str) -> str:
    start = source.index(f"class {class_name}")
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated class: {class_name}")


def _brace_body_after(source: str, header: str) -> str:
    """Brace body of the single `header` (an `if (...)` with its condition)."""
    count = source.count(header)
    if count != 1:
        raise AssertionError(f"{header!r} occurs {count} times")
    at = source.index(header)
    brace = source.index("{", at)
    if source[at + len(header) : brace].strip() != "":
        raise AssertionError(f"tokens between {header!r} and its brace")
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated guard: {header}")


# Each true write is the body of its own guard, and the flag is false before
# that guard. Replacing the condition with `if (true)` drops the header.
_PROBE_TRUE_GUARDS = (
    (
        "probe.by_id_found",
        "if (byId)",
        "UAInput byId = api.GetInputByID(inputId);",
    ),
    (
        "probe.by_id_same_hash",
        "if (byIdHash == nameHash)",
        "int byIdHash = byId.NameHash();",
    ),
    (
        "probe.in_active_inputs",
        "if (activeIndex >= 0)",
        "int activeIndex = activeIds.Find(inputId);",
    ),
)


def _assert_probe_true_guards(body: str) -> None:
    for field, guard, prelude in _PROBE_TRUE_GUARDS:
        guarded = _brace_body_after(body, guard)
        true_at = body.index(f"{field} = true")
        false_at = body.index(f"{field} = false")
        guard_at = body.index(guard)
        prelude_at = body.index(prelude)
        if body.count(f"{field} = true") != 1:
            raise AssertionError(f"{field} = true occurs {body.count(f'{field} = true')} times")
        if not (false_at < prelude_at < guard_at < true_at):
            raise AssertionError(
                f"{field} is not false, then {prelude}, then {guard}, then true"
            )
        if f"{field} = true" not in guarded:
            raise AssertionError(f"{field} = true is outside {guard}")
        if f"{field} = false" in guarded:
            raise AssertionError(f"{field} = false is inside {guard}")


def _sample_probe() -> dict[str, int]:
    return {
        "input_id": 17,
        "name_hash": 101,
        "name_string_hash": 101,
        "by_id_found": 1,
        "by_id_same_hash": 1,
        "in_active_inputs": 1,
    }


class InputDescribeIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")

    def test_client_read_needs_no_lease(self) -> None:
        self.assertIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertNotIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "client")
        self.assertIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertFalse(command_requires_lease(COMMAND))

        status, body = self.state.enqueue_command(COMMAND, {"name": "UAMoveForward"})
        self.assertEqual((status, body["peer"]), (200, "client"))
        self.assertEqual(
            self.state.enqueue_command(COMMAND, {"name": "UAMoveForward"}, peer="server"),
            (400, {"error": "bad_peer"}),
        )

    def test_unknown_name_is_not_an_ingress_error(self) -> None:
        ok, err = loopback.validate_command_args(
            COMMAND, {"name": "MCP_NoSuchInput_e1ae"}
        )
        self.assertEqual((ok, err), (True, None))

    def test_name_bounds_match_printable_ascii(self) -> None:
        self.assertTrue(loopback.is_printable_input_name(" "))
        self.assertTrue(loopback.is_printable_input_name("a" * 128))
        self.assertFalse(loopback.is_printable_input_name(""))
        self.assertFalse(loopback.is_printable_input_name("a" * 129))
        self.assertFalse(loopback.is_printable_input_name("UA\nMove"))
        self.assertFalse(loopback.is_printable_input_name("UA\tMove"))
        self.assertFalse(loopback.is_printable_input_name("UA\x7f"))
        self.assertFalse(loopback.is_printable_input_name("UA\u00f1"))
        self.assertEqual(loopback.INPUT_NAME_MAX_CHARS, 128)

    def test_exists_false_survives_prune_and_an_empty_ref_does_not(self) -> None:
        described = {
            "exists": False,
            "binding_count": 0,
            "locked": False,
            "conflict_count": 0,
            "keys": [],
        }
        pruned = prune_unfilled_fields(
            COMMAND,
            {"ok": 1, "input_describe": described, "building_doors": {}},
        )
        self.assertEqual(pruned["input_describe"], described)
        self.assertNotIn("building_doors", pruned)

    def test_probe_survives_store_and_prune(self) -> None:
        # Field names come from the Enforce class, so this fails if the probe
        # type is removed. store_result copies the body; prune must not eat
        # the nested object, filled or empty.
        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        probe_body = _class_body(messages, "MCPInputProbe")
        for _kind, field in PROBE_FIELDS:
            self.assertIn(f"{field};", probe_body)

        probe = _sample_probe()
        described = {
            "exists": 1,
            "binding_count": 1,
            "locked": 0,
            "conflict_count": 0,
            "keys": [{"index": 0, "key_code": 17, "device": 1}],
            "probe": probe,
        }
        status, enqueued = self.state.enqueue_command(COMMAND, {"name": "UAFire"})
        self.assertEqual(status, 200)
        command_id = enqueued["id"]
        stored_status, _stored = self.state.store_result(
            {
                "id": command_id,
                "ok": 1,
                "input_describe": described,
                "building_doors": {},
            }
        )
        self.assertEqual(stored_status, 200)
        taken = self.state.take_result(command_id, remove=True)
        self.assertIsNotNone(taken)
        self.assertEqual(taken["input_describe"]["probe"], probe)
        pruned = prune_unfilled_fields(COMMAND, taken)
        self.assertEqual(pruned["input_describe"]["probe"], probe)
        self.assertEqual(pruned["input_describe"]["exists"], 1)
        self.assertNotIn("building_doors", pruned)

        unset = {
            "exists": 0,
            "binding_count": 0,
            "locked": 0,
            "conflict_count": 0,
            "keys": [],
            "probe": {},
        }
        kept = prune_unfilled_fields(
            COMMAND,
            {"ok": 1, "input_describe": unset, "building_doors": {}},
        )
        self.assertEqual(kept["input_describe"], unset)
        self.assertNotIn("building_doors", kept)


class InputDescribeFastMCPTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_forwards_the_name_and_says_pressing_is_out_of_scope(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name: tool for tool in await app.list_tools()}
        self.assertIn(COMMAND, tools)
        description = tools[COMMAND].description or ""
        self.assertIn(PRESSING_SENTENCE, description)
        self.assertNotIn("Requires a lease", description)

        payload = {
            "ok": 1,
            "input_describe": {
                "exists": True,
                "binding_count": 1,
                "locked": False,
                "conflict_count": 0,
                "keys": [{"index": 0, "key_code": 17, "device": 1}],
            },
        }
        with patch.object(
            runtime, "call_bridge", new=AsyncMock(return_value=payload)
        ) as call:
            await app.call_tool(COMMAND, {"name": "UAMoveForward", "timeout_s": 1.0})

        call.assert_awaited_once_with(
            COMMAND, {"name": "UAMoveForward"}, "client", 1.0
        )

    async def test_tool_rejects_a_name_the_bridge_would_refuse(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        with patch.object(runtime, "call_bridge", new=AsyncMock()) as call:
            for name in ("", "a" * 129, "UA\n", "UA\u00f1"):
                with self.subTest(name=name):
                    with self.assertRaises(server.ToolError) as ctx:
                        await app.call_tool(COMMAND, {"name": name, "timeout_s": 1.0})
                    self.assertIn("bad_args", str(ctx.exception))
                    self.assertIn("32..126", str(ctx.exception))
        call.assert_not_awaited()

    async def test_tool_returns_the_probe_and_states_the_index_rule(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name: tool for tool in await app.list_tools()}
        description = tools[COMMAND].description or ""
        self.assertIn(EXISTS_RULE, description)
        self.assertIn(PLACEHOLDER_RULE, description)
        self.assertIn(PROBE_WHY, description)
        self.assertIn("uainput.c:25", description)
        self.assertNotIn(RETRACTED_CAVEAT, description)
        self.assertIn("none of its fields are present", description)
        self.assertNotIn("empty object", description)
        for _kind, field in PROBE_FIELDS:
            self.assertIn(field, description)
        self.assertNotIn("exists false means the name is not registered", description)
        self.assertNotIn("An unknown name returns ok with exists false", description)

        probe = _sample_probe()
        registered = {
            "ok": 1,
            "input_describe": {
                "exists": 1,
                "binding_count": 1,
                "locked": 0,
                "conflict_count": 0,
                "keys": [{"index": 0, "key_code": 17, "device": 1}],
                "probe": probe,
            },
        }
        placeholder_probe = _sample_probe()
        placeholder_probe["input_id"] = -1
        placeholder = {
            "ok": 1,
            "input_describe": {
                "exists": 0,
                "binding_count": 0,
                "keys": [],
                "probe": placeholder_probe,
            },
        }
        with patch.object(
            runtime,
            "call_bridge",
            new=AsyncMock(side_effect=[registered, placeholder]),
        ):
            registered_result = _content_json(
                await app.call_tool(COMMAND, {"name": "UAFire", "timeout_s": 1.0})
            )
            placeholder_result = _content_json(
                await app.call_tool(
                    COMMAND, {"name": "UA_DayZMCP_NoSuchInput", "timeout_s": 1.0}
                )
            )
        self.assertEqual(
            registered_result["input_describe"], registered["input_describe"]
        )
        self.assertEqual(
            placeholder_result["input_describe"], placeholder["input_describe"]
        )


class InputDescribeEnforceContractTest(unittest.TestCase):
    def test_null_input_is_exists_false_before_any_bind_read(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        dispatch = _method_body(source, "protected void Dispatch(MCPCommand command)")
        gate = dispatch.index("client_not_in_game")
        branch = dispatch.index('command.cmd == "input_describe"')
        dialog = dispatch.index('command.cmd == "ui_dialog"')
        self.assertLess(gate, branch)
        self.assertLess(branch, dialog)
        self.assertIn("input_describe", source[source.index("CLIENT_POLL_CAPS") : source.index("CLIENT_POLL_CAPS") + 400])

        body = _method_body(source, "protected bool DispatchInputDescribe(")
        null_at = body.index("if (!input)")
        bind_at = body.index("BindingCount()")
        null_branch = body[null_at:bind_at]
        self.assertIn("described.exists = false", null_branch)
        self.assertIn("result.ok = true", null_branch)
        self.assertNotIn("BindingCount", null_branch)
        self.assertNotIn("result.error", null_branch)
        self.assertIn('result.error = "input_api_unavailable"', body)
        self.assertIn('result.error = "input_bind_unreadable"', body)
        for token in (
            "GetUApi()",
            "GetInputByName(command.args.name)",
            "BindKeyCount()",
            "GetBindKey(keyIndex)",
            "GetBindDevice(keyIndex)",
            "IsLocked()",
            "ConflictCount()",
            "described.binding_count = bindingCount",
            "result.input_describe = described",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)
        for forbidden in (
            "SelectAlternative",
            "LocalPress",
            "BindCombo",
            "ClearBinding",
            "input.Lock(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)

        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        self.assertIn("class MCPInputKey", messages)
        self.assertIn("class MCPInputDescribe", messages)
        self.assertIn("ref MCPInputDescribe input_describe;", messages)

    def test_stale_client_census_names_this_tool(self) -> None:
        announced = sorted(server._BRIDGE_COMMAND_TOOLS["client"])
        announced.remove(COMMAND)
        registered = frozenset(
            tool for tool in server._BRIDGE_COMMAND_TOOLS["client"].values() if tool
        )
        result = server._compare_bridge_capabilities(
            "client",
            {"state": "announced", "reason": "ok", "announced_commands": announced},
            registered,
        )
        self.assertEqual(result["state"], "mismatch")
        self.assertEqual(result["reason"], "census_disagrees_with_registered_tools")
        self.assertEqual(result["registered_without_announced_command"], [COMMAND])

    def test_index_gates_exists_and_the_placeholder_skips_binds(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        body = _method_body(source, "protected bool DispatchInputDescribe(")
        null_branch = _brace_body_after(body, "if (!input)")
        self.assertIn("described.exists = false", null_branch)
        for absent in (
            "described.probe",
            "GetActiveInputs",
            "NameHash",
            ".Hash()",
            "GetInputByID",
            "input.ID()",
            "BindingCount",
            "BindKeyCount",
            "GetBindKey",
            "GetBindDevice",
            "IsLocked",
            "ConflictCount",
        ):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, null_branch)

        # ID() >= 0 is the exists rule (uainput.c:25, the input index). The
        # comparison is int against int: the proto result is stored first.
        self.assertEqual(body.count("int inputId = input.ID();"), 1)
        self.assertLess(body.index("int inputId = input.ID();"), body.index("if (inputId >= 0)"))
        registered = _brace_body_after(body, "if (inputId >= 0)")
        for absent in ("described.probe", "new MCPInputProbe", "GetInputByID"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, registered)
        self.assertIn("described.exists = true", registered)
        self.assertEqual(registered.count("described.exists = true"), 1)
        self.assertEqual(body.count("described.exists = true"), 1)
        self.assertNotIn("described.exists = false", registered)
        for token in (
            "BindingCount(",
            "BindKeyCount(",
            "GetBindKey(",
            "GetBindDevice(",
            "IsLocked(",
            "ConflictCount(",
        ):
            with self.subTest(token=token):
                self.assertIn(token, registered)
                self.assertEqual(body.count(token), registered.count(token))

        self.assertEqual(body.count("described.exists = false"), 1)
        self.assertEqual(body.count("GetInputByName("), 1)
        self.assertEqual(body.count("GetActiveInputs("), 1)
        self.assertEqual(body.count("new TIntArray"), 1)
        self.assertEqual(body.count("new MCPInputProbe"), 1)
        self.assertEqual(body.count("input.ID()"), 1)
        header_at = body.index("if (inputId >= 0)")
        brace_at = body.index("{", header_at)
        guard_end = brace_at + 1 + len(registered)
        self.assertEqual(body[guard_end], "}")
        probe_at = body.index("described.probe = probe")
        published = body.index("result.input_describe = described", probe_at)
        self.assertLess(guard_end, probe_at)
        self.assertLess(probe_at, published)
        unreadable = body.index('result.error = "input_bind_unreadable"')
        self.assertLess(body.index("described.exists = false"), body.index("input.ID()"))
        self.assertLess(unreadable, probe_at)
        self.assertIn("return true", body[unreadable:probe_at])
        self.assertNotIn("described.probe", body[unreadable:probe_at])
        self.assertNotIn("result.input_describe", body[unreadable:probe_at])
        self.assertLess(body.index("described.exists = true"), probe_at)
        _assert_probe_true_guards(body)

        for token in (
            "input.ID()",
            "input.NameHash()",
            "command.args.name.Hash()",
            "api.GetInputByID(inputId)",
            "byId.NameHash()",
            "api.GetActiveInputs(activeIds)",
            "activeIds.Find(inputId)",
            "probe.input_id = inputId",
            "probe.name_hash = nameHash",
            "probe.name_string_hash = command.args.name.Hash()",
            "probe.by_id_found = false",
            "probe.by_id_found = true",
            "probe.by_id_same_hash = false",
            "probe.by_id_same_hash = true",
            "probe.in_active_inputs = false",
            "probe.in_active_inputs = true",
            "described.probe = probe",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)

        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        self.assertNotIn("exists false means the name is not registered", messages)
        self.assertNotIn(RETRACTED_CAVEAT, messages)
        self.assertIn("exists is true only when GetInputByName returns non-null", messages)
        self.assertIn("input.ID() >= 0", messages)
        self.assertIn(PLACEHOLDER_RULE, messages)
        self.assertIn(PROBE_WHY, messages)
        self.assertIn("uainput.c:25", messages)
        self.assertIn("ficha 4f50", messages)
        self.assertIn("none of its fields are present", messages)
        self.assertNotIn("serializes as {}", messages)
        probe_body = _class_body(messages, "MCPInputProbe")
        for kind, field in PROBE_FIELDS:
            with self.subTest(field=field):
                self.assertIn(f"{kind} {field};", probe_body)
        describe_body = _class_body(messages, "MCPInputDescribe")
        self.assertIn("ref MCPInputProbe probe;", describe_body)
        ctor = describe_body[describe_body.index("void MCPInputDescribe") :]
        self.assertNotIn("probe", ctor)


if __name__ == "__main__":
    unittest.main()
