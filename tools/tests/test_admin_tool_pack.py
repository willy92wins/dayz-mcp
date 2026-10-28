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
from unittest.mock import MagicMock, patch

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
