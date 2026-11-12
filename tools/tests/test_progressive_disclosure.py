"""Progressive tools/list disclosure (fb-20260917-092908-2ad1).

The initial client-mode catalog is the session and lifecycle core plus the
reads that need no lease: about 17 KB since fb-20260925-233937-b753 added the
reads (11 KB before) and about 20 KB since loadout_get, server_perf,
nav_path_query, players_visibility and scene_entities joined them (fb99,
2427, 0131, 33c5, 165a) and about 23 KB with chat_read and ai_group_state
(0205, 90f9), back to about 19 KB once the listing dropped the schema titles
Pydantic generates (PX3 c), against about 88 KB for the whole catalog. The
mutating world_*/vehicle_*/ui_* tools appear only after a lease is held.
"""

from __future__ import annotations

import json
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

from mcp.shared.memory import create_connected_server_and_client_session

from dayz_mcp.server import ServerConfig, build_app
from dayz_mcp.tool_catalog import _drop_generated_titles
from tests.camera_tool_helpers import _FakeClientRuntime
from tests._tiers import slow_test


LEASE_REVEAL_PREFIXES = ("world_", "vehicle_", "ui_")
# The three lease-free reads behind a reveal prefix (b753, and world_time_get);
# every other world_*/vehicle_*/ui_* tool mutates and waits for the lease.
PREFIXED_LEASE_FREE_READS = frozenset({"ui_tree", "vehicle_telemetry", "world_time_get"})
INITIAL_CATALOG_MAX_BYTES = 23_000
INITIAL_CATALOG_TARGET_BYTES = 20_000


def _catalog_bytes(tools: list[object]) -> int:
    payload = [
        tool.model_dump(mode="json", by_alias=True, exclude_none=True)
        for tool in tools
    ]
    return len(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )


def _names(tools: list[object]) -> set[str]:
    return {tool.name for tool in tools}


def _schema_titles(schema: object, path: str = "") -> list[str]:
    """Paths of the string "title" annotations in a schema's own positions."""
    found: list[str] = []
    if not isinstance(schema, dict):
        return found
    for key, value in schema.items():
        if key == "title" and isinstance(value, str):
            found.append(path or "/")
        elif key in ("properties", "patternProperties", "$defs", "definitions") and isinstance(value, dict):
            for name, sub in value.items():
                found.extend(_schema_titles(sub, f"{path}/{key}/{name}"))
        elif key in ("anyOf", "oneOf", "allOf", "prefixItems", "items") and isinstance(value, list):
            for index, sub in enumerate(value):
                found.extend(_schema_titles(sub, f"{path}/{key}/{index}"))
        elif key in ("items", "additionalProperties", "not"):
            found.extend(_schema_titles(value, f"{path}/{key}"))
    return found


async def _protocol_list_tools(app) -> list[object]:
    """tools/list as an MCP client sees it, not app.list_tools()."""
    async with create_connected_server_and_client_session(app._mcp_server) as session:
        listed = await session.list_tools()
    return list(listed.tools)


class ProgressiveDisclosureTest(unittest.IsolatedAsyncioTestCase):
    def _client_app(self):
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            log_sink=lambda _message: None,
        )
        with patch("dayz_mcp.server.ClientRuntime", _FakeClientRuntime):
            return build_app(config)

    async def test_embedded_catalog_keeps_world_vehicle_ui(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))
        names = _names(await app.list_tools())
        self.assertIn("world_spawn", names)
        self.assertIn("vehicle_enter", names)
        self.assertIn("ui_click", names)

    async def test_client_initial_catalog_is_compact_and_hides_lease_tools(self) -> None:
        app, runtime = self._client_app()
        self.assertIsNone(runtime.active_lease_token)
        tools = await app.list_tools()
        names = _names(tools)
        size = _catalog_bytes(tools)

        self.assertGreaterEqual(size, 4_000)
        self.assertLessEqual(size, INITIAL_CATALOG_MAX_BYTES)
        self.assertLess(
            abs(size - INITIAL_CATALOG_TARGET_BYTES),
            4_000,
            f"initial tools/list was {size} bytes, expected ~{INITIAL_CATALOG_TARGET_BYTES}",
        )
        for name in names:
            if name.startswith(LEASE_REVEAL_PREFIXES):
                self.assertIn(
                    name,
                    PREFIXED_LEASE_FREE_READS,
                    f"{name} must stay hidden until lease",
                )
        self.assertIn("session_acquire_wait", names)
        self.assertIn("bridge_status", names)
        self.assertNotIn("world_spawn", names)
        self.assertNotIn("vehicle_control", names)
        self.assertNotIn("ui_dialog", names)

    async def test_initial_catalog_drops_only_the_generated_schema_titles(self) -> None:
        # PX3 c: the generated titles were about 4 KB of the 23 KB listing.
        # Mutants killed: the compact listing keeping them, and the full
        # listing (after the lease) losing them.
        app, runtime = self._client_app()
        compact = {tool.name: tool for tool in await app.list_tools()}
        for name, tool in compact.items():
            with self.subTest(tool=name):
                self.assertEqual(_schema_titles(tool.inputSchema), [])
        feedback = compact["pipeline_feedback"].inputSchema["properties"]
        self.assertEqual(feedback["title"], {"maxLength": 120, "minLength": 1, "type": "string"})
        runtime.active_lease_token = "lease-token-after-acquire"
        full = {tool.name: tool for tool in await app.list_tools()}
        self.assertIn("/properties/group_id", _schema_titles(full["ai_group_state"].inputSchema))
        for name, tool in compact.items():
            with self.subTest(tool=name):
                self.assertEqual(tool.inputSchema, _drop_generated_titles(full[name].inputSchema))

    def test_drop_generated_titles_walks_only_schema_positions(self) -> None:
        schema = {
            "title": "exampleArguments",
            "type": "object",
            "properties": {
                "title": {"title": "Title", "type": "string"},
                "mode": {"title": "Mode", "enum": ["title"], "default": {"title": "x"}},
                "pair": {"anyOf": [{"title": "A", "type": "integer"}, {"type": "null"}]},
                "points": {"type": "array", "items": {"title": "Point", "type": "number"}},
            },
            "$defs": {"Inner": {"title": "Inner", "type": "object"}},
        }
        self.assertEqual(
            _drop_generated_titles(schema),
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "mode": {"enum": ["title"], "default": {"title": "x"}},
                    "pair": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                    "points": {"type": "array", "items": {"type": "number"}},
                },
                "$defs": {"Inner": {"type": "object"}},
            },
        )
        self.assertEqual(schema["properties"]["title"], {"title": "Title", "type": "string"})

    async def test_client_catalog_reveals_world_vehicle_ui_after_lease(self) -> None:
        app, runtime = self._client_app()
        before = _names(await app.list_tools())
        runtime.active_lease_token = "lease-token-after-acquire"
        after = _names(await app.list_tools())

        self.assertNotIn("world_spawn", before)
        self.assertIn("world_spawn", after)
        self.assertIn("vehicle_enter", after)
        self.assertIn("ui_click", after)
        self.assertGreater(_catalog_bytes(await app.list_tools()), INITIAL_CATALOG_MAX_BYTES)

    @slow_test
    async def test_protocol_tools_list_is_compact_before_lease(self) -> None:
        app, runtime = self._client_app()
        self.assertIsNone(runtime.active_lease_token)
        manager_names = {tool.name for tool in app._tool_manager.list_tools()}
        self.assertIn("world_spawn", manager_names)

        tools = await _protocol_list_tools(app)
        names = _names(tools)
        size = _catalog_bytes(tools)

        self.assertGreaterEqual(size, 4_000)
        self.assertLessEqual(size, INITIAL_CATALOG_MAX_BYTES)
        self.assertLess(
            abs(size - INITIAL_CATALOG_TARGET_BYTES),
            4_000,
            f"protocol tools/list was {size} bytes, expected ~{INITIAL_CATALOG_TARGET_BYTES}",
        )
        for name in names:
            if name.startswith(LEASE_REVEAL_PREFIXES):
                self.assertIn(
                    name,
                    PREFIXED_LEASE_FREE_READS,
                    f"{name} must stay hidden until lease on protocol tools/list",
                )
        self.assertIn("session_acquire_wait", names)
        self.assertIn("bridge_status", names)
        self.assertNotIn("world_spawn", names)
        self.assertNotIn("vehicle_control", names)
        self.assertNotIn("ui_dialog", names)

    async def test_opt_out_lists_the_full_catalog_before_the_lease(self) -> None:
        # --no-progressive-disclosure (#93, e7ef): hosts that never re-list after
        # tools/list_changed get every tool, full descriptions included, up front.
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            log_sink=lambda _message: None,
            progressive_disclosure=False,
        )
        with patch("dayz_mcp.server.ClientRuntime", _FakeClientRuntime):
            app, runtime = build_app(config)
        self.assertIsNone(runtime.active_lease_token)
        registered = {tool.name: tool for tool in app._tool_manager.list_tools()}

        listed = await _protocol_list_tools(app)

        self.assertEqual(_names(listed), set(registered))
        for tool in listed:
            self.assertEqual(tool.description, registered[tool.name].description, tool.name)
        self.assertGreater(_catalog_bytes(listed), INITIAL_CATALOG_MAX_BYTES)

    def test_opt_out_is_a_flag_and_the_default_stays_progressive(self) -> None:
        from dayz_mcp.server import parse_args

        base = ["--client", "--keyfile", "k"]
        self.assertTrue(parse_args(base).progressive_disclosure)
        self.assertFalse(
            parse_args([*base, "--no-progressive-disclosure"]).progressive_disclosure
        )

    @slow_test
    async def test_protocol_tools_list_reveals_after_lease(self) -> None:
        app, runtime = self._client_app()
        before = _names(await _protocol_list_tools(app))
        runtime.active_lease_token = "lease-token-after-acquire"
        after_tools = await _protocol_list_tools(app)
        after = _names(after_tools)

        self.assertNotIn("world_spawn", before)
        self.assertIn("world_spawn", after)
        self.assertIn("vehicle_enter", after)
        self.assertIn("ui_click", after)
        self.assertGreater(_catalog_bytes(after_tools), INITIAL_CATALOG_MAX_BYTES)


if __name__ == "__main__":
    unittest.main()
