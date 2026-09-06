"""Tests for the deterministic Knowledge Pack extractor."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dayz_mcp import knowledge


class KnowledgeExtractTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.pack = Path(self._temporary.name) / "mini-pack"

        self._write(
            "compatibility-matrix.md",
            "Target stable build: **DayZ PC 1.29.0.163451**\n",
        )
        self._write(
            "skills/audio/SKILL.md",
            """# Audio

| API | Evidence |
|---|---|
| `SEffectManager.PlaySound(soundSet, position)` | `scripts/3_game/effectmanager.c:169` |

`UnverifiedCall()` has no source citation and must not be emitted.

Filename and asset suffixes `_1h`, `__init__.py`, `helper.py`, and `_co` are not symbols
(`scripts/3_game/effectmanager.c:170`).
""",
        )
        self._write(
            "skills/physics/SKILL.md",
            """# Physics

`proto native void dBodySetMass(notnull IEntity body, float mass)`
is verified at `scripts/1_core/proto/enphysics.c:123`.

| Symbol | Evidence |
|---|---|
| `PhxInteractionLayers` | `scripts/3_game/global/dayzphysics.c:1` |
""",
        )
        self._write(
            "skills/audio/references/item-sound.md",
            "Use `StartItemSoundServer(id)` (`scripts/4_world/entities/itembase.c:4468`).\n",
        )
        self._write(
            "knowledge/runtime.md",
            "`GetGame().GetPlayers(players)` is server-side (`scripts/3_game/global/game.c:947`).\n",
        )
        self._write(
            "knowledge/vault-notes/not-in-scope.md",
            "`IgnoredCall()` (`scripts/3_game/missing.c:2`).\n",
        )

        for evidence_path in (
            "scripts/1_core/proto/enphysics.c",
            "scripts/3_game/effectmanager.c",
            "scripts/3_game/global/dayzphysics.c",
            "scripts/3_game/global/game.c",
            "scripts/4_world/entities/itembase.c",
        ):
            self._write(evidence_path, "// controlled vanilla fixture\n")

        self.expected = [
            {
                "name": "dBodySetMass",
                "signature": "proto native void dBodySetMass(notnull IEntity body, float mass)",
                "module": "1_core",
                "evidence": [
                    {"path": "scripts/1_core/proto/enphysics.c", "line": 123}
                ],
                "gotchas": [],
                "source_file": "skills/physics/SKILL.md",
                "version_verified": "1.29.0.163451",
            },
            {
                "name": "GetGame.GetPlayers",
                "signature": "GetGame().GetPlayers(players)",
                "module": "3_game",
                "evidence": [
                    {"path": "scripts/3_game/global/game.c", "line": 947}
                ],
                "gotchas": [],
                "source_file": "knowledge/runtime.md",
                "version_verified": "1.29.0.163451",
            },
            {
                "name": "PhxInteractionLayers",
                "signature": None,
                "module": "3_game",
                "evidence": [
                    {"path": "scripts/3_game/global/dayzphysics.c", "line": 1}
                ],
                "gotchas": [],
                "source_file": "skills/physics/SKILL.md",
                "version_verified": "1.29.0.163451",
            },
            {
                "name": "SEffectManager.PlaySound",
                "signature": "SEffectManager.PlaySound(soundSet, position)",
                "module": "3_game",
                "evidence": [
                    {"path": "scripts/3_game/effectmanager.c", "line": 169}
                ],
                "gotchas": [],
                "source_file": "skills/audio/SKILL.md",
                "version_verified": "1.29.0.163451",
            },
            {
                "name": "StartItemSoundServer",
                "signature": "StartItemSoundServer(id)",
                "module": "4_world",
                "evidence": [
                    {"path": "scripts/4_world/entities/itembase.c", "line": 4468}
                ],
                "gotchas": [],
                "source_file": "skills/audio/references/item-sound.md",
                "version_verified": "1.29.0.163451",
            },
        ]

    def _write(self, relative_path: str, content: str) -> None:
        path = self.pack / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_extract_pack_is_deterministic_and_conservative(self) -> None:
        first = knowledge.extract_pack(self.pack)
        second = knowledge.extract_pack(self.pack)

        self.assertEqual(first, self.expected)
        self.assertEqual(second, self.expected)
        self.assertNotIn("UnverifiedCall", [entry["name"] for entry in first])
        self.assertNotIn("IgnoredCall", [entry["name"] for entry in first])
        self.assertNotIn("_1h", [entry["name"] for entry in first])
        self.assertNotIn("__init__.py", [entry["name"] for entry in first])
        self.assertNotIn("helper.py", [entry["name"] for entry in first])
        self.assertNotIn("_co", [entry["name"] for entry in first])

    def test_every_emitted_evidence_path_exists_in_the_fixture(self) -> None:
        entries = knowledge.extract_pack(self.pack)

        # An empty extraction would leave the loop below vacuously green.
        self.assertTrue(entries, "extract_pack returned no entries for the fixture")
        # This is exhaustive for the controlled fixture. The real Pack cites an
        # external vanilla tree, so its corresponding path check is sampled, not total.
        for entry in entries:
            for evidence in entry["evidence"]:
                with self.subTest(name=entry["name"], path=evidence["path"]):
                    self.assertTrue((self.pack / evidence["path"]).is_file())

    def test_extract_pack_output_passes_the_strong_published_validator(self) -> None:
        # The strengthened pre-publication contract (plan point 4) must accept
        # exactly the schema extract_pack() itself produces, optional fields
        # included, so a prepared index never disagrees with a generated one.
        entries = knowledge.extract_pack(self.pack)
        self.assertTrue(entries)
        self.assertIs(knowledge.validate_index(entries), entries)

    def test_module_cli_writes_the_same_deterministic_json(self) -> None:
        output = Path(self._temporary.name) / "knowledge.json"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "dayz_mcp.knowledge",
                "extract",
                "--pack",
                str(self.pack),
                "--out",
                str(output),
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), self.expected)


if __name__ == "__main__":
    unittest.main()
