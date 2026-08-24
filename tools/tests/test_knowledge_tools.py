"""Tests for local read-only Knowledge Pack query tools."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import knowledge
from dayz_mcp.server import ServerConfig, build_app


INDEX = [
    {
        "name": "GetPlayers",
        "signature": "GetGame().GetPlayers(players)",
        "module": "3_game",
        "evidence": [{"path": "scripts/3_game/global/game.c", "line": 947}],
        "gotchas": [],
        "source_file": "skills/runtime/SKILL.md",
        "version_verified": "1.29.0.163451",
    },
    {
        "name": "GetPosition",
        "signature": "Object.GetPosition()",
        "module": "3_game",
        "evidence": [{"path": "scripts/3_game/entities/object.c", "line": 293}],
        "gotchas": [],
        "source_file": "skills/runtime/SKILL.md",
        "version_verified": "1.29.0.163451",
    },
    {
        "name": "SetPosition",
        "signature": "Object.SetPosition(vector position)",
        "module": "3_game",
        "evidence": [{"path": "scripts/3_game/entities/object.c", "line": 301}],
        "gotchas": [],
        "source_file": "skills/runtime/references/object.md",
        "version_verified": "1.29.0.163451",
    },
    {
        "name": "StartItemSoundServer",
        "signature": "StartItemSoundServer(int id)",
        "module": "4_world",
        "evidence": [{"path": "scripts/4_world/entities/itembase.c", "line": 4468}],
        "gotchas": [],
        "source_file": "skills/audio/SKILL.md",
        "version_verified": "1.29.0.163451",
    },
]


def _content_value(content: Any) -> Any:
    if isinstance(content, tuple):
        blocks, structured = content
        if structured is not None:
            if isinstance(structured, dict) and set(structured) == {"result"}:
                return structured["result"]
            return structured
        content = blocks
    return json.loads(content[0].text)


class KnowledgeQueryTest(unittest.TestCase):
    def test_find_matches_name_module_and_signature_case_insensitively(self) -> None:
        self.assertEqual(knowledge.find(INDEX, "players"), [INDEX[0]])
        self.assertEqual(knowledge.find(INDEX, "4_WORLD"), [INDEX[3]])
        self.assertEqual(knowledge.find(INDEX, "vector POSITION"), [INDEX[2]])
        self.assertEqual(knowledge.find(INDEX, "not-present"), [])

    def test_find_returns_at_most_twenty_in_index_order(self) -> None:
        entries = [
            {
                "name": f"Api{number:02d}",
                "signature": None,
                "module": None,
                "evidence": [{"path": "game.c", "line": number + 1}],
                "gotchas": [],
                "source_file": "knowledge/runtime.md",
                "version_verified": None,
            }
            for number in range(25)
        ]

        hits = knowledge.find(entries, "API")

        self.assertEqual(len(hits), 20)
        self.assertEqual([hit["name"] for hit in hits], [f"Api{n:02d}" for n in range(20)])

    def test_show_returns_the_exact_entry(self) -> None:
        self.assertIs(knowledge.show(INDEX, "GetPosition"), INDEX[1])

    def test_show_unknown_api_names_field_and_three_closest_suggestions(self) -> None:
        suggestions_index = [
            {"name": "GetPlayers"},
            {"name": "GetPlayer"},
            {"name": "GetPosition"},
            {"name": "SetPosition"},
        ]

        with self.assertRaises(ToolError) as raised:
            knowledge.show(suggestions_index, "GetPlayrs")

        self.assertEqual(
            str(raised.exception),
            "unknown_api: name 'GetPlayrs' is not in the knowledge index; "
            "suggestions: GetPlayers, GetPlayer, GetPosition",
        )


class KnowledgeMcpToolsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.local_app_data = Path(self._temporary.name)
        self.default_index = self.local_app_data / "DayZ_MCP" / "knowledge.json"

    def _write_index(self, path: Path | None = None) -> Path:
        target = path or self.default_index
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(INDEX), encoding="utf-8")
        return target

    async def test_server_build_registers_both_knowledge_tools(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))

        names = {tool.name for tool in await app.list_tools()}

        self.assertIn("dayz_knowledge_find", names)
        self.assertIn("dayz_knowledge_show", names)

    async def test_registered_find_and_show_use_default_path_without_writing(self) -> None:
        index_path = self._write_index()
        before_bytes = index_path.read_bytes()
        before_mtime = index_path.stat().st_mtime_ns

        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.local_app_data)}):
            os.environ.pop("DAYZ_MCP_KNOWLEDGE_JSON", None)
            app = FastMCP("knowledge-test")
            knowledge.register_knowledge_tools(app)
            found = _content_value(
                await app.call_tool("dayz_knowledge_find", {"query": "players"})
            )
            shown = _content_value(
                await app.call_tool("dayz_knowledge_show", {"name": "GetPosition"})
            )

        self.assertEqual(found, [INDEX[0]])
        self.assertEqual(shown, INDEX[1])
        self.assertEqual(index_path.read_bytes(), before_bytes)
        self.assertEqual(index_path.stat().st_mtime_ns, before_mtime)

    async def test_missing_env_override_returns_typed_install_remedy(self) -> None:
        missing = self.local_app_data / "missing.json"
        with patch.dict(
            os.environ,
            {"DAYZ_MCP_KNOWLEDGE_JSON": str(missing), "LOCALAPPDATA": str(self.local_app_data)},
        ):
            app = FastMCP("knowledge-test")
            knowledge.register_knowledge_tools(app)
            with self.assertRaises(ToolError) as raised:
                await app.call_tool("dayz_knowledge_find", {"query": "Get"})

        self.assertEqual(
            str(raised.exception),
            "Error executing tool dayz_knowledge_find: knowledge_not_installed: "
            "run python -m dayz_mcp.knowledge extract --pack "
            "%LOCALAPPDATA%\\DayZ_MCP\\knowledge-pack --out "
            "%LOCALAPPDATA%\\DayZ_MCP\\knowledge.json",
        )

    async def test_tool_descriptions_state_result_and_missing_index_remedy(self) -> None:
        app = FastMCP("knowledge-test")
        knowledge.register_knowledge_tools(app)
        tools = {tool.name: tool for tool in await app.list_tools()}

        for name in ("dayz_knowledge_find", "dayz_knowledge_show"):
            description = tools[name].description or ""
            with self.subTest(tool=name):
                self.assertIn("Returns", description)
                self.assertIn(
                    "If the index is missing, run python -m dayz_mcp.knowledge extract",
                    description,
                )


if __name__ == "__main__":
    unittest.main()
