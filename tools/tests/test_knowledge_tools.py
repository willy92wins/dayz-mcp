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

from dayz_mcp import knowledge, knowledge_pack
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
            "call dayz_knowledge_status, then dayz_knowledge_prepare",
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
                    "If the index is missing, call dayz_knowledge_status, then dayz_knowledge_prepare",
                    description,
                )

    async def test_status_reports_independent_index_and_pack_states_read_only(self) -> None:
        index_path = self._write_index()
        before_bytes = index_path.read_bytes()
        before_names = sorted(path.name for path in index_path.parent.iterdir())
        missing_pack = self.local_app_data / "missing-pack"
        with patch.dict(
            os.environ,
            {
                "DAYZ_MCP_KNOWLEDGE_JSON": str(index_path),
                "DAYZ_MCP_PACK_DIR": str(missing_pack),
            },
        ):
            app = FastMCP("knowledge-status-test")
            knowledge.register_knowledge_tools(app)
            status = _content_value(
                await app.call_tool("dayz_knowledge_status", {})
            )

        self.assertEqual(status["index_state"], "valid")
        self.assertEqual(status["pack_state"], "missing")
        self.assertTrue(status["can_query"])
        self.assertFalse(status["can_prepare"])
        self.assertEqual(status["index_path"], str(index_path.resolve()))
        self.assertEqual(status["pack_path"], str(missing_pack.resolve()))
        self.assertEqual(index_path.read_bytes(), before_bytes)
        self.assertEqual(sorted(path.name for path in index_path.parent.iterdir()), before_names)

    async def test_prepare_uses_installed_pack_and_republishes_deterministically(self) -> None:
        index_path = self.local_app_data / "knowledge.json"
        pack_path = self.local_app_data / "pack"
        pack_path.mkdir()
        with (
            patch.dict(
                os.environ,
                {
                    "DAYZ_MCP_KNOWLEDGE_JSON": str(index_path),
                    "DAYZ_MCP_PACK_DIR": str(pack_path),
                },
            ),
            patch.object(knowledge_pack, "resolve_pack_dir", return_value=pack_path),
            patch.object(knowledge, "extract_pack", return_value=INDEX) as extract,
            patch.object(
                knowledge_pack,
                "ensure_pack",
                side_effect=AssertionError("MCP prepare must not ensure the pack"),
            ),
        ):
            app = FastMCP("knowledge-prepare-test")
            knowledge.register_knowledge_tools(app)
            first = _content_value(
                await app.call_tool("dayz_knowledge_prepare", {})
            )
            first_bytes = index_path.read_bytes()
            second = _content_value(
                await app.call_tool("dayz_knowledge_prepare", {})
            )

        self.assertEqual(first["status"], "published")
        self.assertEqual(second["status"], "published")
        self.assertEqual(first_bytes, index_path.read_bytes())
        self.assertEqual(json.loads(first_bytes), INDEX)
        self.assertEqual(extract.call_count, 2)

    async def test_invalid_extracted_index_is_rejected_without_replacing_previous_bytes(self) -> None:
        index_path = self._write_index()
        before_bytes = index_path.read_bytes()
        pack_path = self.local_app_data / "pack"
        pack_path.mkdir()
        invalid = [{"name": "MissingEvidence", "source_file": "x.md", "evidence": []}]
        with (
            patch.dict(
                os.environ,
                {
                    "DAYZ_MCP_KNOWLEDGE_JSON": str(index_path),
                    "DAYZ_MCP_PACK_DIR": str(pack_path),
                },
            ),
            patch.object(knowledge_pack, "resolve_pack_dir", return_value=pack_path),
            patch.object(knowledge, "extract_pack", return_value=invalid),
        ):
            app = FastMCP("knowledge-invalid-prepare-test")
            knowledge.register_knowledge_tools(app)
            with self.assertRaises(ToolError) as raised:
                await app.call_tool("dayz_knowledge_prepare", {})

        self.assertEqual(
            str(raised.exception),
            "Error executing tool dayz_knowledge_prepare: knowledge_pack_invalid",
        )
        self.assertEqual(index_path.read_bytes(), before_bytes)

    async def test_status_covers_the_complete_independent_three_by_three_matrix(self) -> None:
        index_path = self.local_app_data / "knowledge.json"
        pack_path = self.local_app_data / "pack"
        pack_path.mkdir()
        expected = {
            ("missing", "missing"): (False, False),
            ("missing", "invalid"): (False, False),
            ("missing", "valid"): (False, True),
            ("invalid", "missing"): (False, False),
            ("invalid", "invalid"): (False, False),
            ("invalid", "valid"): (False, True),
            ("valid", "missing"): (True, False),
            ("valid", "invalid"): (True, False),
            ("valid", "valid"): (True, True),
        }
        for (index_state, pack_state), (can_query, can_prepare) in expected.items():
            with self.subTest(index_state=index_state, pack_state=pack_state):
                if index_state == "valid":
                    index_path.write_text(json.dumps(INDEX), encoding="utf-8")
                elif index_state == "invalid":
                    index_path.write_text("{}", encoding="utf-8")
                elif index_path.exists():
                    index_path.unlink()

                if pack_state == "missing":
                    pack_path_value = self.local_app_data / "missing-pack"
                else:
                    pack_path_value = pack_path
                extracted = INDEX if pack_state == "valid" else []
                with (
                    patch.dict(
                        os.environ,
                        {
                            "DAYZ_MCP_KNOWLEDGE_JSON": str(index_path),
                            "DAYZ_MCP_PACK_DIR": str(pack_path_value),
                        },
                    ),
                    patch.object(knowledge_pack, "resolve_pack_dir", return_value=pack_path_value),
                    patch.object(knowledge, "extract_pack", return_value=extracted),
                ):
                    app = FastMCP("knowledge-matrix-test")
                    knowledge.register_knowledge_tools(app)
                    status = _content_value(
                        await app.call_tool("dayz_knowledge_status", {})
                    )
                    self.assertEqual(status["index_state"], index_state)
                    self.assertEqual(status["pack_state"], pack_state)
                    self.assertEqual(status["can_query"], can_query)
                    self.assertEqual(status["can_prepare"], can_prepare)

                    before_prepare = index_path.read_bytes() if index_path.exists() else None
                    if index_state == "valid":
                        found = _content_value(
                            await app.call_tool("dayz_knowledge_find", {"query": "players"})
                        )
                        shown = _content_value(
                            await app.call_tool("dayz_knowledge_show", {"name": "GetPosition"})
                        )
                        self.assertEqual(found, [INDEX[0]])
                        self.assertEqual(shown, INDEX[1])
                        with self.assertRaises(ToolError) as unknown:
                            await app.call_tool("dayz_knowledge_show", {"name": "Unknown"})
                        self.assertIn("unknown_api:", str(unknown.exception))
                    else:
                        for tool_name, arguments in (
                            ("dayz_knowledge_find", {"query": "players"}),
                            ("dayz_knowledge_show", {"name": "GetPosition"}),
                        ):
                            with self.subTest(tool=tool_name), self.assertRaises(ToolError) as raised:
                                await app.call_tool(tool_name, arguments)
                            expected_code = (
                                "knowledge_not_installed"
                                if index_state == "missing"
                                else "knowledge_index_invalid"
                            )
                            self.assertIn(expected_code, str(raised.exception))

                    if pack_state == "valid":
                        prepared = _content_value(
                            await app.call_tool("dayz_knowledge_prepare", {})
                        )
                        self.assertEqual(prepared["status"], "published")
                        self.assertEqual(knowledge.load_index(index_path), INDEX)
                        self.assertEqual(
                            _content_value(
                                await app.call_tool("dayz_knowledge_show", {"name": "GetPosition"})
                            ),
                            INDEX[1],
                        )
                    else:
                        with self.assertRaises(ToolError) as prepared:
                            await app.call_tool("dayz_knowledge_prepare", {})
                        expected_pack_error = (
                            "knowledge_pack_missing"
                            if pack_state == "missing"
                            else "knowledge_pack_invalid"
                        )
                        self.assertIn(expected_pack_error, str(prepared.exception))
                        self.assertEqual(
                            index_path.read_bytes() if index_path.exists() else None,
                            before_prepare,
                        )

    async def test_find_and_show_return_exact_invalid_index_remedy(self) -> None:
        invalid_path = self.local_app_data / "invalid.json"
        invalid_path.write_text("[]", encoding="utf-8")
        with patch.dict(os.environ, {"DAYZ_MCP_KNOWLEDGE_JSON": str(invalid_path)}):
            app = FastMCP("knowledge-invalid-query-test")
            knowledge.register_knowledge_tools(app)
            for tool_name, arguments in (
                ("dayz_knowledge_find", {"query": "API"}),
                ("dayz_knowledge_show", {"name": "API"}),
            ):
                with self.subTest(tool=tool_name), self.assertRaises(ToolError) as raised:
                    await app.call_tool(tool_name, arguments)
                self.assertEqual(
                    str(raised.exception),
                    f"Error executing tool {tool_name}: knowledge_index_invalid: "
                    "call dayz_knowledge_status, then dayz_knowledge_prepare",
                )

    async def test_prepare_missing_pack_preserves_existing_index(self) -> None:
        index_path = self._write_index()
        before_bytes = index_path.read_bytes()
        missing_pack = self.local_app_data / "missing-pack"
        with (
            patch.dict(
                os.environ,
                {
                    "DAYZ_MCP_KNOWLEDGE_JSON": str(index_path),
                    "DAYZ_MCP_PACK_DIR": str(missing_pack),
                },
            ),
            patch.object(knowledge_pack, "resolve_pack_dir", return_value=missing_pack),
        ):
            app = FastMCP("knowledge-missing-pack-test")
            knowledge.register_knowledge_tools(app)
            with self.assertRaises(ToolError) as raised:
                await app.call_tool("dayz_knowledge_prepare", {})
        self.assertEqual(
            str(raised.exception),
            "Error executing tool dayz_knowledge_prepare: knowledge_pack_missing",
        )
        self.assertEqual(index_path.read_bytes(), before_bytes)

    async def test_prepare_invalid_pack_preserves_existing_index(self) -> None:
        index_path = self._write_index()
        before_bytes = index_path.read_bytes()
        invalid_pack = self.local_app_data / "invalid-pack"
        invalid_pack.mkdir()
        with (
            patch.dict(
                os.environ,
                {
                    "DAYZ_MCP_KNOWLEDGE_JSON": str(index_path),
                    "DAYZ_MCP_PACK_DIR": str(invalid_pack),
                },
            ),
            patch.object(knowledge_pack, "resolve_pack_dir", return_value=invalid_pack),
            patch.object(knowledge, "extract_pack", return_value=[]),
        ):
            app = FastMCP("knowledge-invalid-pack-test")
            knowledge.register_knowledge_tools(app)
            with self.assertRaises(ToolError) as raised:
                await app.call_tool("dayz_knowledge_prepare", {})
        self.assertEqual(
            str(raised.exception),
            "Error executing tool dayz_knowledge_prepare: knowledge_pack_invalid",
        )
        self.assertEqual(index_path.read_bytes(), before_bytes)

    async def test_prepare_extractor_failure_is_rejected_without_replacing_index(self) -> None:
        index_path = self._write_index()
        before_bytes = index_path.read_bytes()
        pack_path = self.local_app_data / "pack"
        pack_path.mkdir()
        with (
            patch.dict(
                os.environ,
                {
                    "DAYZ_MCP_KNOWLEDGE_JSON": str(index_path),
                    "DAYZ_MCP_PACK_DIR": str(pack_path),
                },
            ),
            patch.object(knowledge_pack, "resolve_pack_dir", return_value=pack_path),
            patch.object(
                knowledge, "extract_pack", side_effect=OSError("extractor failed")
            ),
        ):
            app = FastMCP("knowledge-extractor-failure-test")
            knowledge.register_knowledge_tools(app)
            with self.assertRaises(ToolError) as raised:
                await app.call_tool("dayz_knowledge_prepare", {})
        self.assertEqual(
            str(raised.exception),
            "Error executing tool dayz_knowledge_prepare: knowledge_pack_invalid",
        )
        self.assertEqual(index_path.read_bytes(), before_bytes)

    async def test_prepare_preserves_foreign_candidate_and_cleans_only_own_on_replace_failure(self) -> None:
        index_path = self._write_index()
        before_bytes = index_path.read_bytes()
        foreign_candidate = Path(f"{index_path}.candidate.foreign")
        foreign_candidate.write_bytes(b"foreign")
        pack_path = self.local_app_data / "pack"
        pack_path.mkdir()
        with (
            patch.dict(
                os.environ,
                {
                    "DAYZ_MCP_KNOWLEDGE_JSON": str(index_path),
                    "DAYZ_MCP_PACK_DIR": str(pack_path),
                },
            ),
            patch.object(knowledge_pack, "resolve_pack_dir", return_value=pack_path),
            patch.object(knowledge, "extract_pack", return_value=INDEX),
        ):
            app = FastMCP("knowledge-candidate-test")
            knowledge.register_knowledge_tools(app)
            result = _content_value(await app.call_tool("dayz_knowledge_prepare", {}))
        self.assertEqual(result["status"], "published")
        self.assertTrue(foreign_candidate.exists())

        with patch.object(knowledge.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                knowledge._publish_index(index_path, INDEX)
        self.assertEqual(index_path.read_bytes(), json.dumps(INDEX, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
        self.assertEqual(before_bytes, json.dumps(INDEX).encode("utf-8"))
        self.assertTrue(foreign_candidate.exists())
        self.assertEqual(list(index_path.parent.glob(f"{index_path.name}.candidate.*")), [foreign_candidate])

    async def test_prepare_candidate_collision_is_a_conflict_without_replacing_index(self) -> None:
        index_path = self._write_index()
        before_bytes = index_path.read_bytes()
        pack_path = self.local_app_data / "pack"
        pack_path.mkdir()
        collision = Path(f"{index_path}.candidate.00000000000000000000000000000000")
        collision.write_bytes(b"foreign")
        fake_uuid = type("FixedUuid", (), {"hex": "00000000000000000000000000000000"})()
        with (
            patch.dict(
                os.environ,
                {
                    "DAYZ_MCP_KNOWLEDGE_JSON": str(index_path),
                    "DAYZ_MCP_PACK_DIR": str(pack_path),
                },
            ),
            patch.object(knowledge_pack, "resolve_pack_dir", return_value=pack_path),
            patch.object(knowledge, "extract_pack", return_value=INDEX),
            patch.object(knowledge.uuid, "uuid4", return_value=fake_uuid),
        ):
            app = FastMCP("knowledge-collision-test")
            knowledge.register_knowledge_tools(app)
            with self.assertRaises(ToolError) as raised:
                await app.call_tool("dayz_knowledge_prepare", {})
        self.assertEqual(
            str(raised.exception),
            "Error executing tool dayz_knowledge_prepare: knowledge_prepare_conflict",
        )
        self.assertEqual(index_path.read_bytes(), before_bytes)
        self.assertEqual(collision.read_bytes(), b"foreign")


class KnowledgeIndexValidationTest(unittest.TestCase):
    def test_strong_validator_rejects_structural_mutants(self) -> None:
        valid = [{"name": "API", "source_file": "source.md", "evidence": [{"path": "x.c", "line": 1}]}]
        mutants = [
            [],
            {},
            ["not-an-entry"],
            [{"source_file": "source.md", "evidence": [{"path": "x.c", "line": 1}]}],
            [{"name": "", "source_file": "source.md", "evidence": [{"path": "x.c", "line": 1}]}],
            [{"name": "API", "source_file": "", "evidence": [{"path": "x.c", "line": 1}]}],
            [{"name": "API", "source_file": "source.md"}],
            [{"name": "API", "source_file": "source.md", "evidence": []}],
            [{"name": "API", "source_file": "source.md", "evidence": "x.c:1"}],
            [{"name": "API", "source_file": "source.md", "evidence": ["x.c:1"]}],
            [{"name": "API", "source_file": "source.md", "evidence": [{"path": "", "line": 1}]}],
            [{"name": "API", "source_file": "source.md", "evidence": [{"line": 1}]}],
            [{"name": "API", "source_file": "source.md", "evidence": [{"path": "x.c", "line": 0}]}],
            [{"name": "API", "source_file": "source.md", "evidence": [{"path": "x.c", "line": -1}]}],
            [{"name": "API", "source_file": "source.md", "evidence": [{"path": "x.c", "line": "1"}]}],
            [{"name": "API", "source_file": "source.md", "evidence": [{"path": "x.c", "line": True}]}],
        ]
        knowledge.validate_index(valid)
        for mutant in mutants:
            with self.subTest(mutant=mutant):
                with self.assertRaises(knowledge.KnowledgeIndexError):
                    knowledge.validate_index(mutant)


if __name__ == "__main__":
    unittest.main()
