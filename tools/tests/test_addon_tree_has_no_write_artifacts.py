"""Fail if the Enforce addon tree still holds write-artifact backups.

AddonBuilder packs the folder whole. Measured 2026-08-21: `DayZ_MCP.pbo` was
453 KB of 469 KB of source because `MCPBridge.c.bak_pre_fencing_20260819`,
`MCPBridge.c_bak_infdrive_20260820` and `MCPClientBridge.c.bak_pre_fencing_20260819`
(~220 KB of old bridge source) rode along. Enforce compiles by `.c` extension,
so they do not break the build; they still ship.

This test cannot import `tools/publish/boundary.py` (publish/ is outside the
boundary; an included import would fail the import check). The matcher below
is the same criterion as `is_write_artifact` at boundary.py:43, including the
underscore-glued `_bak_` fix. When `tools/publish/boundary.py` is present, a
lockstep test compares the two so they cannot drift on this machine.
"""

from __future__ import annotations

import re
import sys
import fnmatch
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from tests._addon_paths import addon_root  # noqa: E402

# Keep in lockstep with tools/publish/boundary.py:27-53.
_WRITE_ARTIFACTS = {".post", ".snap"}
_WRITE_ARTIFACT_PREFIXES = (".bak", ".orig", ".rej")
_GLUED_WRITE_ARTIFACT = re.compile(r"_(bak|orig|rej)(_|$)")

# The five backups that were actually in DayZ_MCP/scripts/5_Mission on 2026-08-21.
_REAL_POSITIVES = (
    "MCPBridge.c.bak_pre_fencing_20260819",
    "MCPBridge.c_bak_infdrive_20260820",
    "MCPClientBridge.c.bak_pre_fencing_20260819",
    "MCPMessages.c.bak_pre_fencing_20260819",
    "MCPMessages.c_bak_infdrive_20260820",
)
# Names the 2026-08-21 manual probe listed as must-not-match, plus three more
# of the same class so a too-wide regex has somewhere to fail.
_REAL_NEGATIVES = (
    "notes.backup_data",
    "archive.tar_baker",
    "test_bak.py",
    "baker.c",
    "backup.py",
    "MCPBridge.c",
)


def _is_write_artifact(p: Path) -> bool:
    """True for edit-mechanism and hand-made backup copies of a real source."""
    suffix = p.suffix.lower()
    if suffix in _WRITE_ARTIFACTS:
        return True
    if any(
        suffix == prefix or suffix.startswith(prefix + "_")
        for prefix in _WRITE_ARTIFACT_PREFIXES
    ):
        return True
    return _GLUED_WRITE_ARTIFACT.search(suffix) is not None


class WriteArtifactCriterionTest(unittest.TestCase):
    """The matcher itself, so a clone still pins both glue styles."""

    def test_dot_glued_and_underscore_glued_backups_are_artifacts(self) -> None:
        for name in _REAL_POSITIVES:
            with self.subTest(name=name):
                self.assertTrue(
                    _is_write_artifact(Path(name)),
                    f"{name} must match (dot-glued .bak_ and underscore-glued _bak_)",
                )

    def test_edit_mechanism_suffixes_are_artifacts(self) -> None:
        for name in ("file.py.post", "file.py.snap", "file.py.orig", "file.py.rej"):
            with self.subTest(name=name):
                self.assertTrue(_is_write_artifact(Path(name)), name)

    def test_lookalikes_are_not_artifacts(self) -> None:
        for name in _REAL_NEGATIVES:
            with self.subTest(name=name):
                self.assertFalse(
                    _is_write_artifact(Path(name)),
                    f"{name} is a false positive the matcher must not take",
                )


class AddonTreeHasNoWriteArtifactsTest(unittest.TestCase):
    def test_criterion_matches_boundary_py_when_publish_tooling_is_present(self) -> None:
        boundary = _TOOLS_DIR / "publish" / "boundary.py"
        if not boundary.is_file():
            self.skipTest("publish tooling not shipped in this checkout")
        source = boundary.read_text(encoding="utf-8")
        self.assertIn('WRITE_ARTIFACTS = {".post", ".snap"}', source)
        self.assertIn('WRITE_ARTIFACT_PREFIXES = (".bak", ".orig", ".rej")', source)
        self.assertIn('r"_(bak|orig|rej)(_|$)"', source)
        self.assertEqual(_GLUED_WRITE_ARTIFACT.pattern, r"_(bak|orig|rej)(_|$)")
        self.assertEqual(_WRITE_ARTIFACTS, {".post", ".snap"})
        self.assertEqual(_WRITE_ARTIFACT_PREFIXES, (".bak", ".orig", ".rej"))

    def test_the_include_list_exists_and_cannot_match_a_write_artifact(self) -> None:
        """The pbo carries what include.lst names, so that list is the real guard.

        Keeping backups next to the sources is the author's business; letting them
        reach the Workshop is not. Measured 2026-08-21: packing without a list
        produced a 453 kB pbo carrying three .bak_ copies of the bridge, ~244 kB of
        stale source. With `*.c;*.layout` the same tree packs to 209 kB, and the
        mission still compiles 217 files and 508 classes -- identical, which is what
        proves Enforce was ignoring those files and only the pbo carried them.

        So this asserts the list is present and that no pattern in it can match an
        artifact, rather than forbidding artifacts from existing at all.
        """
        root = addon_root()
        include = root / "include.lst"
        self.assertTrue(
            include.is_file(),
            "addon/include.lst is missing: AddonBuilder would pack the whole folder, "
            "backups included. See tools/pack-addon.ps1.",
        )
        patterns = [p.strip() for p in include.read_text(encoding="utf-8").split(";")]
        patterns = [p for p in patterns if p]
        self.assertTrue(patterns, "include.lst is empty")

        artifacts = [p for p in root.rglob("*") if p.is_file() and _is_write_artifact(p)]
        reachable = [
            f"{p.relative_to(root).as_posix()} matches {pattern}"
            for p in artifacts
            for pattern in patterns
            if fnmatch.fnmatch(p.name.lower(), pattern.lower())
        ]
        self.assertEqual(
            reachable,
            [],
            "include.lst has a pattern broad enough to pack an edit-mechanism leftover "
            "into DayZ_MCP.pbo: " + ", ".join(reachable),
        )


if __name__ == "__main__":
    unittest.main()
