"""Release staging contract for a prebuilt DayZ_MCP PBO.

The fixture is a synthetic PBO whose build marker (mcp_build.json) names GIT_SHA:
make_release refuses a PBO built from any other commit, or one that does not say
(fb-20260819-024951-e307). The opaque bytes the fixture used to be are now one of
its entries, so the release still has to copy the PBO byte for byte.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path

from dayz_mcp.core import EXPECTED_BRIDGE_VERSION
from tests.pbo_helpers import build_pbo, marker_bytes


TOOLS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOLS_DIR.parent
PROJECT_FILE = TOOLS_DIR / "pyproject.toml"
BRIDGE_FILE = REPO_ROOT / "addon" / "scripts" / "5_Mission" / "MCPMessages.c"

OPAQUE_BYTES = b"\x00DayZ-MCP release fixture\xff\r\n"
GIT_SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"
ADDON_TREE = "89abcdef0123456789abcdef0123456789abcdef"
BUILT_UTC = "2026-08-24T12:34:56Z"


def _pbo(marker: bytes | None) -> bytes:
    entries = [("config.cpp", b"class CfgPatches {};\n"), ("scripts\\fixture.bin", OPAQUE_BYTES)]
    if marker is not None:
        entries.append(("mcp_build.json", marker))
    return build_pbo(entries)


PBO_BYTES = _pbo(marker_bytes(GIT_SHA, ADDON_TREE))
PROJECT_VERSION = tomllib.loads(PROJECT_FILE.read_text(encoding="utf-8"))[
    "project"
]["version"]
BRIDGE_VERSION = EXPECTED_BRIDGE_VERSION


class MakeReleaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.release = importlib.import_module("make_release")
            cls.import_error = None
        except ModuleNotFoundError as error:
            cls.release = None
            cls.import_error = error

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.pbo = self.root / "input.pbo"
        self.pbo.write_bytes(PBO_BYTES)
        self.out = self.root / "dist"
        self.zip_path = self.out / f"DayZ_MCP-v{PROJECT_VERSION}-addon.zip"
        self.version_path = self.out / "VERSION.json"
        self.sums_path = self.out / "SHA256SUMS.txt"

    def require_release(self):
        if self.release is None:
            self.fail(f"make_release module is missing: {self.import_error}")
        return self.release

    def stage(self, **overrides: object) -> None:
        release = self.require_release()
        options = {
            "pbo_path": self.pbo,
            "out_dir": self.out,
            "allow_dirty": False,
            "repo_root": REPO_ROOT,
            "git_status_fn": lambda _repo: "",
            "git_sha_fn": lambda _repo: GIT_SHA,
            "built_utc_fn": lambda: BUILT_UTC,
        }
        options.update(overrides)
        release.stage_release(**options)

    def test_zip_has_exact_layout_and_pbo_bytes(self) -> None:
        self.stage()

        with zipfile.ZipFile(self.zip_path) as archive:
            self.assertEqual(
                archive.namelist(),
                ["@DayZ_MCP/Addons/DayZ_MCP.pbo"],
            )
            item = archive.infolist()[0]
            self.assertEqual(archive.read(item), PBO_BYTES)
            self.assertEqual(item.date_time, (1980, 1, 1, 0, 0, 0))
            self.assertEqual(item.compress_type, zipfile.ZIP_DEFLATED)
            self.assertEqual((item.external_attr >> 16) & 0o777, 0o644)

    def test_sha256sums_match_independent_recomputation(self) -> None:
        self.stage()

        checksums = {}
        for line in self.sums_path.read_text(encoding="ascii").splitlines():
            digest, separator, name = line.partition("  ")
            self.assertEqual(separator, "  ")
            checksums[name] = digest
        expected = {
            "DayZ_MCP.pbo": hashlib.sha256(PBO_BYTES).hexdigest(),
            self.zip_path.name: hashlib.sha256(self.zip_path.read_bytes()).hexdigest(),
            "VERSION.json": hashlib.sha256(self.version_path.read_bytes()).hexdigest(),
        }
        self.assertEqual(checksums, expected)

    def test_version_json_matches_injected_fields(self) -> None:
        self.stage()

        self.assertEqual(
            json.loads(self.version_path.read_text(encoding="utf-8")),
            {
                "version": PROJECT_VERSION,
                "bridge_version": BRIDGE_VERSION,
                "git_sha": GIT_SHA,
                "pbo_sha256": hashlib.sha256(PBO_BYTES).hexdigest(),
                "built_utc": BUILT_UTC,
            },
        )

    def test_repeated_staging_overwrites_assets_byte_identically(self) -> None:
        self.stage()
        first = {
            path.name: path.read_bytes()
            for path in (self.zip_path, self.version_path, self.sums_path)
        }
        for path in (self.zip_path, self.version_path, self.sums_path):
            path.write_bytes(b"stale release output")

        self.stage()

        second = {
            path.name: path.read_bytes()
            for path in (self.zip_path, self.version_path, self.sums_path)
        }
        self.assertEqual(second, first)

    def test_dirty_tree_refusal_names_field_and_remedy(self) -> None:
        release = self.require_release()

        with self.assertRaises(release.ReleaseRefusal) as raised:
            self.stage(git_status_fn=lambda _repo: "?? local-output.txt")

        self.assertEqual(raised.exception.code, "dirty_tree")
        self.assertIn("--allow-dirty", str(raised.exception))

    def test_allow_dirty_bypasses_dirty_tree_refusal(self) -> None:
        self.stage(
            allow_dirty=True,
            git_status_fn=lambda _repo: "?? local-output.txt",
        )

        self.assertTrue(self.zip_path.is_file())

    def test_missing_pbo_refusal_names_field_and_remedy(self) -> None:
        release = self.require_release()

        with self.assertRaises(release.ReleaseRefusal) as raised:
            self.stage(
                pbo_path=self.root / "missing.pbo",
                git_status_fn=lambda _repo: self.fail(
                    "git status must not run before the PBO input is validated"
                ),
            )

        self.assertEqual(raised.exception.code, "pbo_missing")
        self.assertIn("--pbo", str(raised.exception))

    def assert_refused(self, code: str, pbo_bytes: bytes, **overrides: object) -> str:
        release = self.require_release()
        self.pbo.write_bytes(pbo_bytes)

        with self.assertRaises(release.ReleaseRefusal) as raised:
            self.stage(**overrides)

        self.assertEqual(raised.exception.code, code)
        self.assertFalse(self.out.exists(), "a refused release must not write any asset")
        return str(raised.exception)

    def test_pbo_built_from_another_commit_is_refused(self) -> None:
        message = self.assert_refused(
            "pbo_commit_mismatch", _pbo(marker_bytes(OTHER_SHA, ADDON_TREE))
        )

        self.assertIn(OTHER_SHA, message)
        self.assertIn(GIT_SHA, message)
        self.assertIn("tools/pack-addon.ps1", message)

    def test_pbo_built_from_a_folder_is_refused(self) -> None:
        message = self.assert_refused(
            "pbo_commit_mismatch", _pbo(marker_bytes(None, None, source="folder"))
        )

        self.assertIn("-Source", message)

    def test_allow_dirty_does_not_bypass_the_build_commit_check(self) -> None:
        self.assert_refused(
            "pbo_commit_mismatch",
            _pbo(marker_bytes(OTHER_SHA, ADDON_TREE)),
            allow_dirty=True,
            git_status_fn=lambda _repo: "?? local-output.txt",
        )

    def test_pbo_without_a_build_marker_is_refused(self) -> None:
        message = self.assert_refused("pbo_marker_missing", _pbo(None))

        self.assertIn("mcp_build.json", message)

    def test_malformed_build_marker_is_refused(self) -> None:
        self.assert_refused(
            "pbo_marker_invalid", _pbo(b'{"commit":"' + GIT_SHA.encode() + b'"}\n')
        )

    def test_bytes_that_are_not_a_pbo_are_refused(self) -> None:
        message = self.assert_refused("pbo_unreadable", OPAQUE_BYTES)

        self.assertIn("--pbo", message)

    def test_parsers_watch_real_workspace_files(self) -> None:
        release = self.require_release()

        self.assertEqual(release.read_project_version(PROJECT_FILE), PROJECT_VERSION)
        self.assertEqual(release.read_bridge_version(BRIDGE_FILE), BRIDGE_VERSION)

    def test_parsers_refuse_missing_or_duplicate_exact_lines(self) -> None:
        release = self.require_release()
        cases = (
            (
                release.read_project_version,
                'version="1.2.3"\n',
                "version_parse_failed",
            ),
            (
                release.read_project_version,
                'version = "1.2.3"\nversion = "4.5.6"\n',
                "version_parse_failed",
            ),
            (
                release.read_bridge_version,
                'const string MCP_BRIDGE_VERSION="9";\n',
                "bridge_version_parse_failed",
            ),
            (
                release.read_bridge_version,
                'const string MCP_BRIDGE_VERSION = "9";\n'
                'const string MCP_BRIDGE_VERSION = "11";\n',
                "bridge_version_parse_failed",
            ),
        )
        for index, (reader, content, code) in enumerate(cases):
            with self.subTest(index=index, code=code):
                source = self.root / f"source-{index}.txt"
                source.write_text(content, encoding="utf-8")
                with self.assertRaises(release.ReleaseRefusal) as raised:
                    reader(source)
                self.assertEqual(raised.exception.code, code)
                self.assertIn("exactly one", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
