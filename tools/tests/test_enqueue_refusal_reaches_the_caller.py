from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import instance_fence, loopback, server
from dayz_mcp.server import ServerConfig, build_app
from tests.test_client_mode import _fixture_client_runtime

_ENQUEUE_ROUTE_FNS = frozenset(
    {
        "enqueue_command",
        "_enqueue_command",
        "_enqueue_fence_target",
        "_enqueue_run_rejection",
        "_fence_reject_response",
        "_enqueue_exec_enforce",
    }
)
_REQUIRED_RUN_FENCE_CODES = (
    "run_not_owned",
    "run_state_unavailable",
    "enqueue_cancelled",
)


def _codes_from_function(fn: ast.FunctionDef) -> set[str]:
    codes: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "error"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    codes.add(value.value)
        if isinstance(node, ast.Return) and node.value is not None:
            returned = node.value
            if isinstance(returned, ast.Constant) and isinstance(returned.value, str):
                codes.add(returned.value)
            elif isinstance(returned, ast.Tuple) and returned.elts:
                first = returned.elts[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    codes.add(first.value)
    return codes


def _enqueue_route_census() -> set[str]:
    source = Path(loopback.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    codes: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "ServerState":
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name in _ENQUEUE_ROUTE_FNS:
                codes.update(_codes_from_function(item))
    codes = {code for code in codes if server._is_safe_error_token(code)}
    codes |= set(instance_fence.FENCE_ENQUEUE_CODES)
    codes.add(loopback._DURABLE_UNREADABLE)
    # Emitted by session_coordination.authorize, outside the walked functions.
    codes.add("session_granting")
    return codes


class EnqueueRefusalCodeTest(unittest.TestCase):
    def test_the_idle_run_refusal_keeps_its_code_and_hint(self) -> None:
        self.assertEqual(
            server._public_enqueue_error(
                {"error": "run_not_owned", "hint": loopback._RUN_NOT_OWNED_HINT}
            ),
            "run_not_owned: " + loopback._RUN_NOT_OWNED_HINT,
        )

    def test_every_run_fence_code_is_whitelisted(self) -> None:
        for code in _REQUIRED_RUN_FENCE_CODES:
            self.assertEqual(server._public_enqueue_error({"error": code}), code)
            self.assertIn(code, server._REMOTE_ERROR_CODES)

    def test_the_lease_grant_race_code_is_whitelisted(self) -> None:
        self.assertEqual(
            server._public_enqueue_error({"error": "session_granting"}),
            "session_granting",
        )

    def test_a_whitelisted_code_without_hint_stays_bare(self) -> None:
        self.assertEqual(
            server._public_enqueue_error({"error": "binding_not_ready"}),
            "binding_not_ready",
        )
        self.assertEqual(
            server._public_enqueue_error({"error": "queue_full", "hint": ""}),
            "queue_full",
        )

    def test_a_fence_hint_travels_with_its_code(self) -> None:
        hinted = [
            code
            for code in instance_fence.FENCE_HINTS
            if code in server._REMOTE_ERROR_CODES
        ]
        self.assertTrue(
            hinted,
            "FENCE_HINTS ∩ _REMOTE_ERROR_CODES is empty",
        )
        for code in hinted:
            payload = instance_fence.fence_error(code)[1]
            self.assertEqual(
                server._public_enqueue_error(payload),
                f"{code}: {instance_fence.FENCE_HINTS[code]}",
            )

    def test_an_unknown_code_drops_its_hint(self) -> None:
        cases = (
            {"error": "arbitrary_remote_text", "hint": "sensitive-test-value"},
            {"error": {"lease_token": "x"}, "hint": "sensitive-test-value"},
            {"error": "run_not_owned" + "x"},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                text = server._public_enqueue_error(payload)
                self.assertEqual(text, "remote_error")
                self.assertNotIn("sensitive", text)

    def test_an_oversized_or_malformed_hint_is_dropped(self) -> None:
        code = "run_not_owned"
        self.assertEqual(
            server._public_enqueue_error({"error": code, "hint": "a" * 241}),
            code,
        )
        self.assertEqual(
            server._public_enqueue_error({"error": code, "hint": "a" * 240}),
            f"{code}: {'a' * 240}",
        )
        self.assertEqual(
            server._public_enqueue_error({"error": code, "hint": 123}),
            code,
        )
        self.assertEqual(
            server._public_enqueue_error({"error": code, "hint": " padded "}),
            code,
        )
        self.assertEqual(
            server._public_enqueue_error({"error": code, "hint": "a\nb"}),
            code,
        )
        self.assertEqual(
            server._public_enqueue_error({"error": code, "hint": "a\tb"}),
            code,
        )
        self.assertEqual(
            server._public_enqueue_error({"error": code, "hint": "a\x1bb"}),
            code,
        )

    def test_the_recipes_keep_their_own_text(self) -> None:
        self.assertEqual(
            server._public_enqueue_error({"error": "lease_required", "hint": "x"}),
            server.LEASE_REQUIRED_RECIPE,
        )
        self.assertEqual(
            server._public_enqueue_error({"error": "retail_quarantine", "hint": "x"}),
            server.RETAIL_QUARANTINE_RECIPE,
        )
        text = server._public_enqueue_error(
            {"error": "version_blocked", "expected": "7", "got": "6", "hint": "x"}
        )
        self.assertTrue(text.startswith("version_blocked"), text)
        self.assertNotIn(": x", text)


class EnqueueCodeCensusTest(unittest.TestCase):
    def test_every_code_the_enqueue_route_can_emit_is_whitelisted(self) -> None:
        census = _enqueue_route_census()
        self.assertTrue(census, "enqueue-route census is empty")
        missing_required = [
            code for code in _REQUIRED_RUN_FENCE_CODES if code not in census
        ]
        self.assertFalse(
            missing_required,
            f"AST census missed required enqueue codes: {missing_required}",
        )
        missing_from_whitelist = sorted(census - set(server._REMOTE_ERROR_CODES))
        self.assertFalse(
            missing_from_whitelist,
            "enqueue codes not in _REMOTE_ERROR_CODES: "
            + ", ".join(missing_from_whitelist),
        )


class ClientModeIdleRunRefusalTest(unittest.IsolatedAsyncioTestCase):
    def _runtime(self) -> server.ClientRuntime:
        return _fixture_client_runtime(
            ServerConfig(mode="client", key="k", port=12345, log_sink=lambda _m: None)
        )

    async def test_call_bridge_surfaces_the_idle_run_refusal_with_its_hint(self) -> None:
        runtime = self._runtime()
        payload = {
            "error": "run_not_owned",
            "hint": loopback._RUN_NOT_OWNED_HINT,
        }
        runtime._call = lambda *_a, **_k: (409, payload)
        with self.assertRaises(server.ToolError) as ctx:
            await runtime.call_bridge("query_all_players", {}, "server", 1.0)
        self.assertEqual(
            str(ctx.exception),
            "run_not_owned: " + loopback._RUN_NOT_OWNED_HINT,
        )

    async def test_enqueue_bridge_surfaces_the_same_text(self) -> None:
        runtime = self._runtime()
        payload = {
            "error": "run_not_owned",
            "hint": loopback._RUN_NOT_OWNED_HINT,
        }
        runtime._call = lambda *_a, **_k: (409, payload)
        with self.assertRaises(server.ToolError) as ctx:
            await runtime.enqueue_bridge("query_all_players", {}, "server", 1.0)
        self.assertEqual(
            str(ctx.exception),
            "run_not_owned: " + loopback._RUN_NOT_OWNED_HINT,
        )

    async def test_a_hinted_stale_lease_still_clears_the_local_lease(self) -> None:
        runtime = self._runtime()
        runtime.active_lease_token = "token-shaped-test-value"
        runtime._call = lambda *_a, **_k: (
            409,
            {"error": "lease_invalid", "hint": "x"},
        )
        with self.assertRaises(server.ToolError) as ctx:
            await runtime.call_bridge("query_all_players", {}, "server", 1.0)
        self.assertTrue(str(ctx.exception).startswith("lease_invalid"))
        self.assertIsNone(runtime.active_lease_token)

    async def test_the_wait_for_description_names_the_ownership_refusal(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _m: None))
        tools = {tool.name: tool for tool in await app.list_tools()}
        description = tools["wait_for"].description or ""
        self.assertIn("run_not_owned", description)
        self.assertIn("session_acquire_wait", description)
