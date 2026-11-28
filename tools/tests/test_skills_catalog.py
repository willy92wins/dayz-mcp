"""SEP-2640 skills catalog: manifest, reads, legacy fallback and shipped skills.

Offline. No DayZ, no daemon, no ports. Oracle frozen before the implementation
(written by a different family than the implementer): do not edit to make it pass.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import unittest
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from dayz_mcp import server, skills_catalog

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_skill(
    root: Path,
    name: str = "demo",
    description: str = "Demo skill.",
    extra: str = "",
    body: str = "# Demo\n\nRead references/guide.md\n",
    ref: str | None = "# Guide\n\nUse it.\n",
) -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(
        f"---\nname: {name}\ndescription: {description}\n{extra}---\n\n{body}".encode()
    )
    if ref is not None:
        (skill / "references").mkdir()
        (skill / "references" / "guide.md").write_bytes(ref.encode())
    return skill


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class CatalogManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_entry_has_the_sep_2640_shape_and_digests_of_the_served_bytes(self) -> None:
        skill = _write_skill(self.root, extra="license: MIT\n")
        catalog = skills_catalog.SkillCatalog.load(self.root)
        entries = catalog.list_entries()
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(set(entry), {"uri", "frontmatter", "resources"})
        self.assertEqual(entry["uri"], "skill://demo/SKILL.md")
        self.assertEqual(
            entry["frontmatter"],
            {"name": "demo", "description": "Demo skill.", "license": "MIT"},
        )
        skill_md = (skill / "SKILL.md").read_bytes()
        guide = (skill / "references" / "guide.md").read_bytes()
        self.assertEqual(
            entry["resources"],
            [
                {"uri": "skill://demo/SKILL.md", "digest": _sha(skill_md), "size": len(skill_md)},
                {
                    "uri": "skill://demo/references/guide.md",
                    "digest": _sha(guide),
                    "size": len(guide),
                },
            ],
        )
        self.assertEqual(catalog.get_entry("skill://demo/SKILL.md"), entry)

    def test_manifest_is_complete_and_lists_skill_md_first_then_sorted(self) -> None:
        skill = _write_skill(self.root)
        (skill / "references" / "a.md").write_bytes(b"a\n")
        (skill / "zeta.md").write_bytes(b"z\n")
        catalog = skills_catalog.SkillCatalog.load(self.root)
        uris = [item["uri"] for item in catalog.list_entries()[0]["resources"]]
        self.assertEqual(
            uris,
            [
                "skill://demo/SKILL.md",
                "skill://demo/references/a.md",
                "skill://demo/references/guide.md",
                "skill://demo/zeta.md",
            ],
        )
        self.assertEqual(sorted(catalog.uris()), sorted(uris))

    def test_entries_are_sorted_by_uri_and_fresh_copies(self) -> None:
        _write_skill(self.root, name="zebra")
        _write_skill(self.root, name="alpha", ref=None)
        catalog = skills_catalog.SkillCatalog.load(self.root)
        entries = catalog.list_entries()
        self.assertEqual(
            [entry["uri"] for entry in entries],
            ["skill://alpha/SKILL.md", "skill://zebra/SKILL.md"],
        )
        entries[0]["frontmatter"]["name"] = "tampered"
        self.assertEqual(catalog.list_entries()[0]["frontmatter"]["name"], "alpha")

    def test_read_returns_the_raw_bytes_and_a_mime_type(self) -> None:
        skill = _write_skill(self.root)
        (skill / "notes.txt").write_bytes(b"plain\n")
        catalog = skills_catalog.SkillCatalog.load(self.root)
        data, mime = catalog.read("skill://demo/SKILL.md")
        self.assertEqual(data, (skill / "SKILL.md").read_bytes())
        self.assertEqual(mime, "text/markdown")
        self.assertEqual(catalog.read("skill://demo/notes.txt"), (b"plain\n", "text/plain"))

    def test_crlf_bytes_are_served_exactly_as_digested(self) -> None:
        skill = _write_skill(self.root)
        raw = b"---\r\nname: demo\r\ndescription: Demo skill.\r\n---\r\n\r\nBody\r\n"
        (skill / "SKILL.md").write_bytes(raw)
        catalog = skills_catalog.SkillCatalog.load(self.root)
        entry = catalog.list_entries()[0]
        self.assertEqual(catalog.read("skill://demo/SKILL.md")[0], raw)
        self.assertEqual(entry["resources"][0]["digest"], _sha(raw))
        self.assertEqual(entry["resources"][0]["size"], len(raw))
        self.assertEqual(entry["frontmatter"], {"name": "demo", "description": "Demo skill."})

    def test_unknown_or_escaping_uris_are_not_served(self) -> None:
        _write_skill(self.root)
        (self.root / "outside.md").write_bytes(b"secret\n")
        catalog = skills_catalog.SkillCatalog.load(self.root)
        for uri in (
            "skill://demo/missing.md",
            "skill://demo/../outside.md",
            "skill://demo/references/../../outside.md",
            "skill://DEMO/SKILL.md",
            "skill://demo/SKILL.md/",
            "skill://demo",
            "skill://demo/references/guide.md?x=1",
            "file:///etc/passwd",
            "",
        ):
            with self.subTest(uri=uri):
                with self.assertRaises(KeyError):
                    catalog.read(uri)
        with self.assertRaises(KeyError):
            catalog.get_entry("skill://demo/references/guide.md")
        with self.assertRaises(KeyError):
            catalog.get_entry("skill://nope/SKILL.md")

    def test_empty_or_missing_root_is_an_empty_catalog(self) -> None:
        self.assertEqual(skills_catalog.SkillCatalog.load(self.root).list_entries(), [])
        self.assertEqual(
            skills_catalog.SkillCatalog.load(self.root / "absent").list_entries(), []
        )

    def test_quoted_frontmatter_values_keep_their_colons(self) -> None:
        _write_skill(self.root, description='"Use when: a thing, then: another."')
        entry = skills_catalog.SkillCatalog.load(self.root).list_entries()[0]
        self.assertEqual(entry["frontmatter"]["description"], "Use when: a thing, then: another.")


class CatalogRejectsInvalidSkillsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _assert_rejected(self) -> None:
        with self.assertRaises(skills_catalog.SkillError):
            skills_catalog.SkillCatalog.load(self.root)

    def test_name_must_match_the_directory(self) -> None:
        skill = _write_skill(self.root)
        (skill / "SKILL.md").write_bytes(b"---\nname: other\ndescription: d\n---\n")
        self._assert_rejected()

    def test_name_and_description_are_required(self) -> None:
        skill = _write_skill(self.root)
        (skill / "SKILL.md").write_bytes(b"---\nname: demo\n---\n")
        self._assert_rejected()
        (skill / "SKILL.md").write_bytes(b"---\ndescription: d\n---\n")
        self._assert_rejected()

    def test_skill_md_must_open_with_frontmatter(self) -> None:
        skill = _write_skill(self.root)
        (skill / "SKILL.md").write_bytes(b"# no frontmatter\n")
        self._assert_rejected()

    def test_agent_skills_naming_and_length_rules(self) -> None:
        for bad in ("Demo", "demo_skill", "-demo", "demo-", "de--mo", "a" * 65):
            with self.subTest(name=bad):
                self.setUp()
                _write_skill(self.root, name=bad)
                self._assert_rejected()
        self.setUp()
        _write_skill(self.root, description="x" * 1025)
        self._assert_rejected()

    def test_frontmatter_it_cannot_represent_is_refused_not_dropped(self) -> None:
        for extra in ("metadata:\n  k: v\n", "tags:\n  - a\n", "k: [a, b]\n"):
            with self.subTest(extra=extra):
                self.setUp()
                _write_skill(self.root, extra=extra)
                self._assert_rejected()

    def test_plain_scalars_that_yaml_would_not_read_as_strings_are_refused(self) -> None:
        # A host compares its own YAML parse of SKILL.md against `frontmatter` field by field:
        # a plain `1` or `true` would be an int or a bool there and a string here.
        for value in (
            "true", "False", "yes", "NO", "on", "Off", "null", "Null", "~", "1", "-3", "1.5",
            "1e3", "0x1F", "0o7", ".inf", ".NaN", "2026-11-28", "a: b", "a #comment",
        ):
            with self.subTest(value=value):
                self.setUp()
                _write_skill(self.root, extra=f"label: {value}\n")
                self._assert_rejected()
        for value in ("MIT", "Apache-2.0", "v1", "two words", "3d"):
            with self.subTest(value=value):
                self.setUp()
                _write_skill(self.root, extra=f"label: {value}\n")
                entry = skills_catalog.SkillCatalog.load(self.root).list_entries()[0]
                self.assertEqual(entry["frontmatter"]["label"], value)
        self.setUp()
        _write_skill(self.root, extra='label: "true"\nother: \'1\'\n')
        entry = skills_catalog.SkillCatalog.load(self.root).list_entries()[0]
        self.assertEqual(entry["frontmatter"]["label"], "true")
        self.assertEqual(entry["frontmatter"]["other"], "1")

    def test_binary_or_unsupported_files_are_refused(self) -> None:
        skill = _write_skill(self.root)
        (skill / "references" / "blob.bin").write_bytes(b"\x00\x01")
        self._assert_rejected()
        (skill / "references" / "blob.bin").unlink()
        (skill / "references" / "bad.md").write_bytes(b"\xff\xfe\x00bad")
        self._assert_rejected()

    def test_symlinks_are_refused(self) -> None:
        skill = _write_skill(self.root)
        target = self.root / "target.md"
        target.write_bytes(b"x\n")
        try:
            os.symlink(target, skill / "link.md")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available here")
        self._assert_rejected()

    def test_per_skill_limits_of_the_extension(self) -> None:
        skill = _write_skill(self.root)
        for index in range(512):
            (skill / f"f{index:03d}.md").write_bytes(b"x\n")
        self._assert_rejected()  # 1 + 1 + 512 files is over 512 entries
        for path in skill.glob("f*.md"):
            path.unlink()
        (skill / "big.md").write_bytes(b"x" * (16 * 1024 * 1024))
        self._assert_rejected()  # over 16 MiB with SKILL.md and the guide


class LegacyFallbackTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.skill = _write_skill(Path(self._tmp.name))
        self.catalog = skills_catalog.SkillCatalog.load(Path(self._tmp.name))

    async def test_install_publishes_every_file_as_a_resource(self) -> None:
        app = FastMCP("t")
        skills_catalog.install(app, self.catalog)
        resources = {str(item.uri): item for item in await app.list_resources()}
        self.assertEqual(set(resources), set(self.catalog.uris()))
        head = resources["skill://demo/SKILL.md"]
        self.assertEqual(head.name, "demo")
        self.assertEqual(head.description, "Demo skill.")
        self.assertEqual(head.mimeType, "text/markdown")
        for uri in self.catalog.uris():
            contents = list(await app.read_resource(uri))
            self.assertEqual(len(contents), 1)
            data, _mime = self.catalog.read(uri)
            self.assertEqual(contents[0].content, data.decode("utf-8"))

    def test_instructions_pointer_names_every_skill_uri_and_stays_short(self) -> None:
        pointer = skills_catalog.instructions_pointer(self.catalog)
        self.assertIn("skill://demo/SKILL.md", pointer)
        self.assertLessEqual(len(pointer), 800)
        self.assertEqual(
            skills_catalog.instructions_pointer(
                skills_catalog.SkillCatalog.load(self.skill.parent / "absent")
            ),
            "",
        )


class ShippedSkillsTest(unittest.IsolatedAsyncioTestCase):
    SKILL_URI = "skill://dayz-test-run/SKILL.md"

    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = skills_catalog.SkillCatalog.load()
        cls.app, _runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    def test_the_initial_skill_is_shipped_and_valid(self) -> None:
        entry = self.catalog.get_entry(self.SKILL_URI)
        self.assertEqual(entry["frontmatter"]["name"], "dayz-test-run")
        self.assertGreater(len(entry["frontmatter"]["description"]), 40)
        self.assertLessEqual(entry["resources"][0]["size"], 6000)
        self.assertEqual(entry["resources"][0]["uri"], self.SKILL_URI)

    def test_wheel_staging_ships_every_skill_file_byte_for_byte(self) -> None:
        # stage_wheel_tree copies the package by hand; the package-data globs only work on
        # what it stages, so a skill left out of the stage is missing from the release wheel.
        import build_wheel

        tools = REPO_ROOT / "tools"
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / "stage"
            build_wheel.stage_wheel_tree(tools, REPO_ROOT / "playbooks", stage, b"{}")
            src = tools / "dayz_mcp" / "skills"
            dst = stage / "dayz_mcp" / "skills"
            wanted = {
                path.relative_to(src): path.read_bytes()
                for path in src.rglob("*")
                if path.is_file() and "__pycache__" not in path.parts
            }
            self.assertTrue(wanted)
            got = {
                path.relative_to(dst): path.read_bytes()
                for path in dst.rglob("*")
                if path.is_file()
            } if dst.is_dir() else {}
            self.assertEqual(got, wanted)
            # and a catalog loaded from the staged tree serves the same skills
            staged = skills_catalog.SkillCatalog.load(dst)
            shipped = skills_catalog.SkillCatalog.load(src)
            self.assertEqual(staged.list_entries(), shipped.list_entries())

    def test_relative_references_resolve_inside_the_manifest(self) -> None:
        text = self.catalog.read(self.SKILL_URI)[0].decode("utf-8")
        listed = {item["uri"] for item in self.catalog.get_entry(self.SKILL_URI)["resources"]}
        links = set(re.findall(r"\breferences/[A-Za-z0-9_./-]+\.md\b", text))
        self.assertTrue(links, "the skill should point at its reference file")
        for link in links:
            self.assertIn("skill://dayz-test-run/" + link, listed)
        for uri in listed:
            relative = uri.removeprefix("skill://dayz-test-run/")
            if relative != "SKILL.md":
                self.assertIn(relative, links, "every supporting file is linked from SKILL.md")

    def test_every_identifier_in_the_skill_is_grounded_in_the_repo(self) -> None:
        tools = {tool.name: tool for tool in self.app._tool_manager.list_tools()}
        grounded = set(tools)
        for tool in tools.values():
            grounded.update(tool.parameters.get("properties", {}))
        corpus = "\n".join(
            (REPO_ROOT / name).read_text(encoding="utf-8")
            for name in ("AGENTS.md", "README.md", "QUICKSTART.md")
        ) + (REPO_ROOT / "tools" / "dayz_mcp" / "server.py").read_text(encoding="utf-8")
        for uri in self.catalog.uris():
            text = self.catalog.read(uri)[0].decode("utf-8")
            for token in set(re.findall(r"`([a-z]+(?:_[a-z0-9]+)+)`", text)):
                with self.subTest(uri=uri, token=token):
                    self.assertTrue(
                        token in grounded or token in corpus,
                        f"`{token}` is not a tool, a parameter, or a word of AGENTS/README/QUICKSTART/server.py",
                    )

    def test_skill_states_the_dayz_test_run_protocol(self) -> None:
        text = self.catalog.read(self.SKILL_URI)[0].decode("utf-8")
        for needle in (
            "dayz_test_run",
            "dayz_test_stop",
            "session_acquire_wait",
            "session_release",
            "session_status",
            "on_busy",
            "queue_offer",
        ):
            self.assertIn(needle, text)

    async def test_server_publishes_the_skill_as_resource_and_in_instructions(self) -> None:
        self.assertIn(self.SKILL_URI, self.app._mcp_server.instructions or "")
        self.assertIn("Expose DayZDiag through typed MCP tools.", self.app._mcp_server.instructions)
        uris = {str(item.uri) for item in await self.app.list_resources()}
        self.assertTrue(set(self.catalog.uris()) <= uris)

    def test_run_serves_stdio_through_the_dual_era_front(self) -> None:
        import inspect

        source = inspect.getsource(server.run)
        self.assertIn("dual_era", source)
        self.assertNotIn('app.run(transport="stdio")', source)


if __name__ == "__main__":
    unittest.main()
