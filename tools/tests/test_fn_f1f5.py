from __future__ import annotations

import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from dayz_mcp import server
from dayz_mcp.server_cli import parse_server_tail_silent
from tests.test_client_mode import _fixture_client_runtime
from tests.test_mcp_tools import _content_json


LOCAL8B_NAMES = frozenset(
    {
        "bridge_status",
        "session_acquire_wait",
        "session_heartbeat",
        "session_release",
        "session_status",
        "pipeline_inbox",
        "pipeline_feedback",
        "pipeline_resolve",
        "dayz_knowledge_status",
        "dayz_knowledge_find",
        "dayz_knowledge_show",
        "dayz_knowledge_prepare",
        "dayz_test_run",
        "dayz_test_stop",
        "dayz_test_close",
        "wait_for",
    }
)


def _catalog_bytes(tools: list[object]) -> int:
    payload = [
        tool.model_dump(mode="json", by_alias=True, exclude_none=True)
        for tool in tools
    ]
    return len(
        json.dumps(
            payload, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    )


def _announced_snapshot() -> dict[str, object]:
    return {
        "server_peer": {
            "last_poll_age_s": 0.1,
            "version_state": "ok",
            "capabilities": {
                "state": "announced",
                "announced_commands": sorted(server._BRIDGE_COMMAND_TOOLS["server"]),
                "announced_arg_contract_hash": server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
            },
        },
        "client_peer": {
            "last_poll_age_s": 0.1,
            "version_state": "ok",
            "capabilities": {
                "state": "announced",
                "announced_commands": sorted(server._BRIDGE_COMMAND_TOOLS["client"]),
            },
        },
    }


def _ready_snapshot() -> dict[str, object]:
    # #94 (0878): server ready requires an accredited caps census.
    return {
        "server_peer": {
            "last_poll_age_s": 0.1,
            "version_state": "ok",
            "capabilities": {"state": "match", "reason": "ok"},
        },
        "client_peer": {"last_poll_age_s": 0.1, "version_state": "ok"},
    }


def _assert_suggestions(
    case: unittest.TestCase,
    result: dict[str, object],
    registered: set[str],
) -> list[dict[str, object]]:
    suggestions = result.get("suggested_calls")
    case.assertIsInstance(suggestions, list)
    case.assertGreaterEqual(len(suggestions), 1)
    case.assertLessEqual(len(suggestions), 2)
    for call in suggestions:
        case.assertEqual(set(call), {"tool", "args"})
        case.assertIn(call["tool"], registered)
        case.assertNotEqual(call["tool"], "lifecycle_status")
        case.assertIsInstance(call["args"], dict)
    return suggestions


class ToolPackCliTest(unittest.IsolatedAsyncioTestCase):
    def test_parse_args_defaults_to_full(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = server.parse_args(["--keyfile", "dummy.key"])

        self.assertEqual(config.tool_pack, "full")

    def test_parse_args_accepts_local8b_for_client(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = server.parse_args(
                ["--keyfile", "dummy.key", "--client", "--tool-pack", "local8b"]
            )

        self.assertEqual(config.mode, "client")
        self.assertEqual(config.tool_pack, "local8b")

    def test_environment_selects_local8b_only_when_cli_omits_flag(self) -> None:
        with patch.dict(
            os.environ, {"DAYZ_MCP_TOOL_PACK": "  local8b  "}, clear=True
        ):
            from_environment = server.parse_args(["--keyfile", "dummy.key"])
            cli_wins = server.parse_args(
                ["--keyfile", "dummy.key", "--tool-pack", "full"]
            )
            equals_cli_wins = server.parse_args(
                ["--keyfile", "dummy.key", "--tool-pack=full"]
            )

        self.assertEqual(from_environment.tool_pack, "local8b")
        self.assertEqual(cli_wins.tool_pack, "full")
        self.assertEqual(equals_cli_wins.tool_pack, "full")

    def test_invalid_cli_and_environment_fail_closed(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit):
                server.parse_args(
                    ["--keyfile", "dummy.key", "--tool-pack", "not-a-pack"]
                )
        with patch.dict(
            os.environ, {"DAYZ_MCP_TOOL_PACK": "not-a-pack"}, clear=True
        ):
            with self.assertRaises(SystemExit):
                server.parse_args(["--keyfile", "dummy.key"])

    def test_silent_parser_is_argv_only_and_validates_tool_pack(self) -> None:
        with patch.dict(
            os.environ, {"DAYZ_MCP_TOOL_PACK": "local8b"}, clear=True
        ):
            omitted = parse_server_tail_silent(["--keyfile", "dummy.key"])
            parsed = parse_server_tail_silent(
                ["--keyfile", "dummy.key", "--tool-pack", "local8b"]
            )
            invalid = parse_server_tail_silent(
                ["--keyfile", "dummy.key", "--tool-pack", "not-a-pack"]
            )

        self.assertEqual(omitted.status, "parsed")
        self.assertIsNotNone(omitted.namespace)
        self.assertEqual(omitted.namespace.tool_pack, "full")
        self.assertEqual(parsed.status, "parsed")
        self.assertIsNotNone(parsed.namespace)
        self.assertEqual(parsed.namespace.tool_pack, "local8b")
        self.assertEqual(invalid.status, "invalid")

    def test_local_pack_is_not_forwarded_to_daemon(self) -> None:
        config = server.ServerConfig(tool_pack="local8b", keyfile="dummy.key")

        argv = server.daemon.build_daemon_argv(config, python="python")

        self.assertNotIn("--tool-pack", argv)
        self.assertNotIn("local8b", argv)

    async def test_parsed_local8b_builds_exact_catalog(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = server.parse_args(
                ["--keyfile", "dummy.key", "--tool-pack", "local8b"]
            )

        app, _runtime = server.build_app(config)
        names = {tool.name for tool in await app.list_tools()}

        self.assertEqual(names, LOCAL8B_NAMES)
        self.assertEqual(len(names), 16)

    async def test_parsed_default_build_keeps_object_inspect(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = server.parse_args(["--keyfile", "dummy.key"])

        app, _runtime = server.build_app(config)

        self.assertIn("object_inspect", {tool.name for tool in await app.list_tools()})


class Local8BToolPackTest(unittest.IsolatedAsyncioTestCase):
    async def test_local8b_catalog_is_small_and_exact(self) -> None:
        full_app, _ = server.build_app(
            server.ServerConfig(log_sink=lambda _message: None)
        )
        local_app, _ = server.build_app(
            server.ServerConfig(tool_pack="local8b", log_sink=lambda _message: None)
        )

        full = await full_app.list_tools()
        local = await local_app.list_tools()
        local_names = {tool.name for tool in local}
        full_bytes = _catalog_bytes(full)
        local_bytes = _catalog_bytes(local)

        self.assertEqual(local_names, LOCAL8B_NAMES)
        self.assertGreaterEqual(len(local_names), 12)
        self.assertLessEqual(len(local_names), 16)
        self.assertLess(local_bytes, full_bytes // 2)
        self.assertNotIn("session_acquire", local_names)
        self.assertNotIn("session_wait", local_names)
        self.assertNotIn("lease_acquire", local_names)
        self.assertNotIn("session_cancel", local_names)

    async def test_local8b_census_matches_and_fingerprint_reflects_pack(self) -> None:
        full_app, full_runtime = server.build_app(
            server.ServerConfig(log_sink=lambda _message: None)
        )
        local_app, local_runtime = server.build_app(
            server.ServerConfig(tool_pack="local8b", log_sink=lambda _message: None)
        )
        snapshot = _announced_snapshot()

        # #94 moved the capability comparison into bridge_status_payload, so
        # feed the raw status underneath it instead of replacing it.
        with patch.object(
            full_runtime,
            "status",
            new=MagicMock(return_value=snapshot),
        ):
            full_status = _content_json(
                await full_app.call_tool("bridge_status", {})
            )
        with patch.object(
            local_runtime,
            "status",
            new=MagicMock(return_value=snapshot),
        ):
            local_status = _content_json(
                await local_app.call_tool("bridge_status", {})
            )

        for peer in ("server_peer", "client_peer"):
            self.assertEqual(
                local_status[peer]["capabilities"]["state"], "match"
            )
            self.assertEqual(
                local_status[peer]["capabilities"]["reason"], "ok"
            )
        self.assertNotEqual(
            local_status["tool_registry_fingerprint"],
            full_status["tool_registry_fingerprint"],
        )

    async def test_default_pack_keeps_object_inspect(self) -> None:
        app, _ = server.build_app(server.ServerConfig(log_sink=lambda _message: None))
        self.assertIn("object_inspect", {tool.name for tool in await app.list_tools()})

    def test_unknown_tool_pack_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            server.build_app(
                server.ServerConfig(
                    tool_pack="not-a-pack", log_sink=lambda _message: None
                )
            )


class BridgeSuccessHintTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _embedded_runtime(tool_pack: str = "full"):
        app, runtime = server.build_app(
            server.ServerConfig(
                tool_pack=tool_pack, log_sink=lambda _message: None
            )
        )
        runtime.loopback = MagicMock()
        runtime.status = MagicMock(return_value=_ready_snapshot())
        runtime.loopback.state.enqueue_command.return_value = (200, {"id": 7})
        return app, runtime

    async def test_query_all_players_changes_hint_for_empty_and_nonempty(self) -> None:
        app, runtime = self._embedded_runtime()
        registered = {tool.name for tool in await app.list_tools()}

        runtime.loopback.state.take_result.return_value = {"ok": 1, "players": []}
        empty = await runtime.call_bridge(
            "query_all_players", {}, "server", timeout_s=5.0
        )
        empty_calls = _assert_suggestions(self, empty, registered)
        self.assertEqual(empty_calls[0]["tool"], "wait_for")
        self.assertEqual(
            empty_calls[0]["args"],
            {"condition": "players_at_least", "value": 1},
        )

        runtime.loopback.state.take_result.return_value = {
            "ok": 1,
            "players": [{"uid": "player-1", "pos": [1.0, 2.0, 3.0]}],
        }
        nonempty = await runtime.call_bridge(
            "query_all_players", {}, "server", timeout_s=5.0
        )
        nonempty_calls = _assert_suggestions(self, nonempty, registered)
        self.assertEqual(nonempty_calls[0], {"tool": "query_player_state", "args": {}})
        self.assertNotEqual(empty_calls[0], nonempty_calls[0])

    async def test_entities_query_uses_object_inspect_only_when_visible(self) -> None:
        full_app, full_runtime = self._embedded_runtime()
        full_registered = {tool.name for tool in await full_app.list_tools()}
        entity_result = {
            "ok": 1,
            "entities": [
                {
                    "type": "SeaChest",
                    "pos": [100.0, 10.0, 200.0],
                    "distance": 1.5,
                }
            ],
        }
        full_runtime.loopback.state.take_result.return_value = entity_result
        full = await full_runtime.call_bridge(
            "entities_query",
            {"pos": [100.0, 10.0, 200.0], "radius": 20.0, "limit": 32},
            "server",
            timeout_s=5.0,
        )
        full_calls = _assert_suggestions(self, full, full_registered)
        self.assertEqual(full_calls[0]["tool"], "object_inspect")
        self.assertEqual(full_calls[0]["args"]["type"], "SeaChest")

        local_app, local_runtime = self._embedded_runtime("local8b")
        local_registered = {tool.name for tool in await local_app.list_tools()}
        local_runtime.loopback.state.take_result.return_value = entity_result
        local = await local_runtime.call_bridge(
            "entities_query",
            {"pos": [100.0, 10.0, 200.0], "radius": 20.0, "limit": 32},
            "server",
            timeout_s=5.0,
        )
        local_calls = _assert_suggestions(self, local, local_registered)
        self.assertNotIn("object_inspect", {call["tool"] for call in local_calls})
        self.assertEqual(local_calls[0]["tool"], "bridge_status")

    async def test_client_wrapper_adds_registry_valid_hints(self) -> None:
        runtime = _fixture_client_runtime(
            server.ServerConfig(
                mode="client",
                key="k",
                port=12345,
                log_sink=lambda _message: None,
            )
        )
        registered = {
            "bridge_status",
            "query_player_state",
            "session_status",
            "wait_for",
        }
        runtime._registered_tool_names = frozenset(registered)
        runtime.bridge_status_payload = AsyncMock(return_value=_ready_snapshot())
        runtime._call = MagicMock(return_value=(200, {"id": 9}))
        runtime._await_result = AsyncMock(return_value={"ok": 1, "players": []})

        result = await runtime.call_bridge(
            "query_all_players", {}, "server", timeout_s=5.0
        )

        calls = _assert_suggestions(self, result, registered)
        self.assertEqual(calls[0]["tool"], "wait_for")

    async def test_not_ready_envelope_has_no_success_hints(self) -> None:
        _app, runtime = self._embedded_runtime()
        runtime.status = MagicMock(
            return_value={
                "server_peer": {
                    "last_poll_age_s": None,
                    "version_state": "ok",
                },
                "client_peer": {
                    "last_poll_age_s": None,
                    "version_state": "ok",
                },
            }
        )

        result = await runtime.call_bridge(
            "query_all_players", {}, "server", timeout_s=5.0
        )

        self.assertIs(result["ok"], False)
        self.assertNotIn("suggested_calls", result)


if __name__ == "__main__":
    unittest.main()
