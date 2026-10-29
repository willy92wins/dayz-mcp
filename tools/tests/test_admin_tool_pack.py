"""Fixed admin tool pack (ticket C1, fb-20261028-131018-d528).

The admin pack is a closed catalog of 22 tools that a client lists the
same before and after the lease is taken, without progressive
disclosure, sized for the D7 catalog budget (working assumption:
at most 5000 tokens). It is a catalog, not access control (B4).
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from mcp.shared.memory import create_connected_server_and_client_session

os.environ.setdefault(
    "LOCALAPPDATA", str(Path(tempfile.gettempdir()) / "dayz-mcp-localapp")
)
if sys.platform != "win32":
    import ctypes

    class _FakeWinDLL:
        def __init__(self, name: str, *args: object, **kwargs: object) -> None:
            self._name = name

        def __getattr__(self, name: str) -> MagicMock:
            return MagicMock(name=f"{self._name}.{name}")

    ctypes.WinDLL = _FakeWinDLL  # type: ignore[misc, assignment]
    sys.modules.setdefault(
        "msvcrt",
        types.SimpleNamespace(
            LK_NBLCK=1,
            LK_UNLCK=2,
            get_osfhandle=lambda fd: fd,
            locking=lambda *_a, **_k: None,
        ),
    )

from dayz_mcp import tool_pack
from dayz_mcp.server import ServerConfig, build_app, parse_args
from tests._tiers import slow_test


ADMIN_NAMES = frozenset(
    {
        "bridge_status",
        "session_acquire_wait",
        "session_heartbeat",
        "session_release",
        "session_status",
        "query_all_players",
        "query_player_state",
        "world_time_get",
        "logs_since",
        "wait_for",
        "surface_query",
        "entities_query",
        "notify_players",
        "player_teleport",
        "player_heal",
        "player_godmode",
        "world_time_set",
        "world_weather_set",
        "world_spawn",
        "object_delete",
        "restore_gameplay",
        "server_perf",
    }
)

# The four tools whose bridge fallback is the first player; under admin
# they must name a player (uid_required, fix1).
UID_REQUIRED_TOOLS = frozenset(
    {"notify_players", "player_teleport", "player_heal", "player_godmode"}
)
UID_TOOL_ARGS: dict[str, dict[str, object]] = {
    "notify_players": {"show_time": 1.0, "title": "hi"},
    "player_teleport": {
        "pos": [1.0, 0.0, 3.0],
        "skip_clearance_check": True,
    },
    "player_heal": {},
    "player_godmode": {"on": True},
}
TEST_UID = "76561198000000000"

# D7 working assumption: the whole listed catalog fits in 5000 tokens.
CATALOG_TOKEN_BUDGET = 5000


def _catalog_tokens(tools: list[object]) -> int:
    """Estimate listed-catalog tokens as ceil(JSON chars / 4)."""
    payload = [
        tool.model_dump(mode="json", by_alias=True, exclude_none=True)
        for tool in tools
    ]
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return math.ceil(len(text) / 4)


def _spy_bridge(runtime: object, calls: list[str]) -> None:
    """Replace runtime.call_bridge with a recording stub (no dispatch)."""

    async def fake_call_bridge(
        cmd: str, _args: dict, _peer: str, _timeout: object
    ) -> dict[str, object]:
        calls.append(cmd)
        if cmd == "query_all_players":
            return {"ok": True, "players": [{"uid": TEST_UID}]}
        return {"ok": True}

    runtime.call_bridge = fake_call_bridge  # type: ignore[attr-defined]
    runtime.start_loopback = lambda: None  # type: ignore[attr-defined]
    runtime.stop_loopback = lambda: None  # type: ignore[attr-defined]
    runtime.bridge_status_payload = AsyncMock(  # type: ignore[attr-defined]
        return_value={}
    )


def _result_text(result: object) -> str:
    content = getattr(result, "content", None) or []
    return " ".join(
        str(getattr(block, "text", "") or "") for block in content
    )


class _FakeClientRuntime:
    def __init__(self, config: ServerConfig, **_kwargs: object) -> None:
        self.config = config
        self.active_lease_token = None
        self._registered_tool_names = None


class AdminToolPackTest(unittest.IsolatedAsyncioTestCase):
    def test_pack_is_closed_and_has_22_tools(self) -> None:
        self.assertEqual(tool_pack.tool_names("admin"), ADMIN_NAMES)
        self.assertEqual(len(ADMIN_NAMES), 22)
        self.assertEqual(tool_pack.ADMIN_TOOL_NAMES, ADMIN_NAMES)

    def test_unknown_pack_still_rejected(self) -> None:
        with self.assertRaises(ValueError):
            tool_pack.tool_names("not-a-pack")

    def test_parse_args_selects_admin_by_cli_and_environment(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = parse_args(
                ["--keyfile", "dummy.key", "--client", "--tool-pack", "admin"]
            )
        self.assertEqual(config.tool_pack, "admin")
        with patch.dict(
            os.environ, {"DAYZ_MCP_TOOL_PACK": "admin"}, clear=True
        ):
            self.assertEqual(
                parse_args(["--keyfile", "dummy.key"]).tool_pack, "admin"
            )

    async def test_embedded_admin_catalog_is_exact(self) -> None:
        app, _runtime = build_app(
            ServerConfig(tool_pack="admin", log_sink=lambda _message: None)
        )
        tools = await app.list_tools()
        self.assertEqual({tool.name for tool in tools}, ADMIN_NAMES)

    @slow_test
    async def test_client_admin_catalog_is_fixed_across_lease(self) -> None:
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            tool_pack="admin",
            log_sink=lambda _message: None,
        )
        with patch("dayz_mcp.server.ClientRuntime", _FakeClientRuntime):
            app, runtime = build_app(config)
        before_tools = await app.list_tools()
        before = {tool.name for tool in before_tools}
        runtime.active_lease_token = "lease-token-after-acquire"
        after_tools = await app.list_tools()
        after = {tool.name for tool in after_tools}

        self.assertEqual(before, ADMIN_NAMES)
        self.assertEqual(after, ADMIN_NAMES)
        self.assertEqual(before, after)
        # The verbs the council bench lost without a fixed catalog.
        for name in (
            "session_acquire_wait",
            "notify_players",
            "player_teleport",
            "player_heal",
            "world_time_set",
        ):
            self.assertIn(name, before)

    async def test_admin_catalog_has_one_sentence_descriptions(self) -> None:
        app, _runtime = build_app(
            ServerConfig(tool_pack="admin", log_sink=lambda _message: None)
        )
        tools = await app.list_tools()
        self.assertEqual({tool.name for tool in tools}, ADMIN_NAMES)
        for tool in tools:
            with self.subTest(tool=tool.name):
                description = tool.description or ""
                self.assertTrue(description.endswith("."))
                self.assertEqual(description.count("."), 1)
                self.assertNotIn("…", description)

    async def test_admin_catalog_schemas_are_flat_with_uid_required(
        self,
    ) -> None:
        app, _runtime = build_app(
            ServerConfig(tool_pack="admin", log_sink=lambda _message: None)
        )
        tools = await app.list_tools()

        def walk(node: object) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "properties" and isinstance(value, dict):
                        for property_schema in value.values():
                            walk(property_schema)
                        continue
                    self.assertNotIn(
                        key,
                        {"anyOf", "oneOf", "$defs", "$ref", "default",
                         "title", "description"},
                    )
                    self.assertNotEqual(value, "null")
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        uid_tools = 0
        for tool in tools:
            with self.subTest(tool=tool.name):
                self.assertIsNone(tool.outputSchema)
                schema = tool.inputSchema or {}
                walk(schema)
                self.assertEqual(schema.get("type"), "object")
                properties = schema.get("properties") or {}
                for property_schema in properties.values():
                    self.assertIn(
                        property_schema.get("type"),
                        {"string", "integer", "number", "boolean", "array"},
                    )
                if "uid" in properties:
                    uid_tools += 1
                    self.assertEqual(properties["uid"], {"type": "string"})
                    self.assertIn("uid", schema.get("required") or [])
        self.assertGreater(uid_tools, 0)

    async def test_admin_catalog_explicitly_requires_uid_for_four_tools(
        self,
    ) -> None:
        app, _runtime = build_app(
            ServerConfig(tool_pack="admin", log_sink=lambda _message: None)
        )
        tools = {tool.name: tool for tool in await app.list_tools()}
        self.assertEqual(
            UID_REQUIRED_TOOLS,
            {"notify_players", "player_teleport", "player_heal",
             "player_godmode"},
        )
        for name in sorted(UID_REQUIRED_TOOLS):
            with self.subTest(tool=name):
                tool = tools[name]
                schema = tool.inputSchema or {}
                properties = schema.get("properties") or {}
                self.assertEqual(properties.get("uid"), {"type": "string"})
                self.assertIn("uid", schema.get("required") or [])

    async def test_admin_uid_required_over_mcp_transport(self) -> None:
        for name in sorted(UID_REQUIRED_TOOLS):
            with self.subTest(tool=name):
                app, runtime = build_app(
                    ServerConfig(
                        tool_pack="admin",
                        keyfile="dummy.key",
                        log_sink=lambda _message: None
                    )
                )
                calls: list[str] = []
                _spy_bridge(runtime, calls)
                args = dict(UID_TOOL_ARGS[name])
                async with create_connected_server_and_client_session(
                    app._mcp_server
                ) as session:
                    for rejected_args in (args, {**args, "uid": ""}):
                        rejected = await session.call_tool(
                            name, rejected_args
                        )
                        self.assertTrue(rejected.isError, rejected)
                        self.assertIn("uid_required", _result_text(rejected))
                    # Nothing was dispatched for either rejected call.
                    self.assertEqual(calls, [])

                    dispatched = await session.call_tool(
                        name, {**args, "uid": TEST_UID}
                    )
                    self.assertFalse(dispatched.isError, dispatched)
                    self.assertIn(name, calls)

    async def test_full_pack_still_accepts_without_uid_over_mcp_transport(
        self,
    ) -> None:
        for name in sorted(UID_REQUIRED_TOOLS):
            with self.subTest(tool=name):
                app, runtime = build_app(
                    ServerConfig(
                        tool_pack="full",
                        keyfile="dummy.key",
                        log_sink=lambda _message: None
                    )
                )
                calls: list[str] = []
                _spy_bridge(runtime, calls)
                async with create_connected_server_and_client_session(
                    app._mcp_server
                ) as session:
                    result = await session.call_tool(
                        name, dict(UID_TOOL_ARGS[name])
                    )
                    self.assertFalse(result.isError, result)
                    self.assertNotIn("uid_required", _result_text(result))
                    self.assertIn(name, calls)

    async def test_full_catalog_keeps_long_descriptions_and_defaults(
        self,
    ) -> None:
        app, _runtime = build_app(
            ServerConfig(tool_pack="full", log_sink=lambda _message: None)
        )
        tools = {tool.name: tool for tool in await app.list_tools()}
        heal = tools["player_heal"]
        self.assertGreater(len(heal.description or ""), 100)
        uid = (heal.inputSchema or {})["properties"]["uid"]
        self.assertEqual(uid.get("default"), "")
        self.assertNotIn("uid", (heal.inputSchema or {}).get("required") or [])

    @slow_test
    async def test_admin_catalog_fits_the_d7_token_budget(self) -> None:
        app, _runtime = build_app(
            ServerConfig(tool_pack="admin", log_sink=lambda _message: None)
        )
        tools = await app.list_tools()
        self.assertLessEqual(
            _catalog_tokens(tools),
            CATALOG_TOKEN_BUDGET,
            "admin catalog exceeds the D7 budget "
            f"({CATALOG_TOKEN_BUDGET} tokens, ceil(chars/4) estimator); "
            "shortening descriptions/schemas is ticket C2",
        )


if __name__ == "__main__":
    unittest.main()
