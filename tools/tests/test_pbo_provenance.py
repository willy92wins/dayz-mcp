"""tools/dev/pbo_provenance.py: a built PBO is compared file by file with addon/ at
a git ref, and must carry the build marker that names that ref (e307).

fb-20260819-024951-e307: a PBO prepared from an older tree would have silently
reverted another session's layout work; the only way to see it was to compare file
by file. pack-addon.ps1 now writes mcp_build.json into the stage, and this tool
fails on any entry that differs, any file missing, any extra entry other than that
marker, and a marker that names another commit.

The reader runs on synthetic PBOs from tests/pbo_helpers.py, which follow the layout
measured on 24 DayZ_MCP.pbo builds; the command line runs against a throwaway git
repository. The pack-addon side of the marker is tested in test_pack_addon_staging.
"""

from __future__ import annotations

import hashlib
import importlib.util
import struct
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests._tiers import slow_test
from tests.pbo_helpers import build_pbo, marker_bytes

TOOLS_DIR = Path(__file__).resolve().parents[1]
TOOL = TOOLS_DIR / "dev" / "pbo_provenance.py"
_TIMEOUT_S = 90

COMMIT = "1111111111111111111111111111111111111111"
TREE = "2222222222222222222222222222222222222222"
OTHER = "3333333333333333333333333333333333333333"
BLOBS = {
    "$PBOPREFIX$": b"DayZ_MCP\n",
    "config.cpp": b"class CfgPatches {};\n",
    "scripts/5_Mission/MCPBridge.c": b"// MCPBridge\r\nclass MCPBridge {};\r\n",
}


def _load_tool():
    spec = importlib.util.spec_from_file_location("pbo_provenance", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


provenance = _load_tool()


def _entries(blobs: dict[str, bytes] = BLOBS, marker: bytes | None = None) -> list[tuple[str, bytes]]:
    """A -packonly build of ``blobs``: backslash names, plus the marker."""
    entries = [(rel.replace("/", "\\"), data) for rel, data in blobs.items()]
    entries.append(("mcp_build.json", marker_bytes(COMMIT, TREE) if marker is None else marker))
    return entries


def _with_record(data: bytes, name: str, *, mime: int, original: int) -> bytes:
    """``data`` with the header record of ``name`` rewritten and the checksum redone."""
    at = data.index(name.encode("utf-8") + b"\x00") + len(name) + 1
    _mime, _original, reserved, stamp, size = struct.unpack_from("<5I", data, at)
    body = bytearray(data[:-21])
    struct.pack_into("<5I", body, at, mime, original, reserved, stamp, size)
    return bytes(body) + b"\x00" + hashlib.sha1(body).digest()


def _states(lines: list[str]) -> dict[str, str]:
    """Entry path -> its state word: `<path> [<size>] <STATE> [why]` per report line."""
    found: dict[str, str] = {}
    for line in lines:
        parts = line.split()
        if not parts or parts[0] in ("marker", "pbo", "props", "ref", "PROVENANCE"):
            continue
        rest = parts[1:]
        if rest and rest[0].isdigit():
            rest = rest[1:]
        found[parts[0]] = rest[0]
    return found


class ReadPboTest(unittest.TestCase):
    def test_round_trip_keeps_names_bytes_and_properties(self) -> None:
        entries = _entries() + [("empty.txt", b""), ("bin\\blob.bin", bytes(range(256)))]

        properties, got = provenance.read_pbo(build_pbo(entries))

        self.assertEqual(properties, {"product": "dayz ugc", "prefix": "DayZ_MCP"})
        self.assertEqual(got, entries)

    def test_truncated_file_is_refused(self) -> None:
        data = build_pbo(_entries())
        for cut in (1, 21, len(data) // 2, len(data) - 1):
            with self.subTest(cut=cut), self.assertRaises(provenance.PboFormatError):
                provenance.read_pbo(data[:-cut])

    def test_bytes_after_the_checksum_are_refused(self) -> None:
        with self.assertRaises(provenance.PboFormatError):
            provenance.read_pbo(build_pbo(_entries()) + b"\x00")

    def test_a_byte_changed_after_packing_breaks_the_checksum(self) -> None:
        data = bytearray(build_pbo(_entries()))
        data[data.index(b"class CfgPatches")] ^= 0x20

        with self.assertRaises(provenance.PboFormatError) as raised:
            provenance.read_pbo(bytes(data))

        self.assertIn("SHA-1", str(raised.exception))

    def test_compressed_or_encoded_entries_are_refused(self) -> None:
        data = build_pbo(_entries())
        cases = (
            ("compressed", 0x43707273, 999),  # 'Cprs', original size != data size
            ("encoded", 0x456E6372, 0),  # 'Encr'
        )
        for label, mime, original in cases:
            with self.subTest(label), self.assertRaises(provenance.PboFormatError):
                provenance.read_pbo(_with_record(data, "config.cpp", mime=mime, original=original))

    def test_bytes_that_are_not_a_pbo_are_refused(self) -> None:
        for data in (b"", b"no nul terminator", b"\x00DayZ-MCP release fixture\xff\r\n"):
            with self.subTest(data=data), self.assertRaises(provenance.PboFormatError):
                provenance.read_pbo(data)


class ParseMarkerTest(unittest.TestCase):
    def test_git_marker_parses(self) -> None:
        self.assertEqual(
            provenance.parse_marker(marker_bytes(COMMIT, TREE)),
            {"commit": COMMIT, "tree": TREE, "built_utc": "2026-09-30T12:00:00Z", "source": "git"},
        )

    def test_folder_marker_parses_with_null_commit_and_tree(self) -> None:
        marker = provenance.parse_marker(marker_bytes(None, None, source="folder"))

        self.assertIsNone(marker["commit"])
        self.assertIsNone(marker["tree"])

    def test_malformed_markers_are_refused(self) -> None:
        good = marker_bytes(COMMIT, TREE).decode("utf-8")
        cases = {
            "byte-order mark": b"\xef\xbb\xbf" + good.encode("utf-8"),
            "not UTF-8": b'{"commit":"\xff"}',
            "not JSON": b"commit=" + COMMIT.encode("ascii"),
            "a list": b"[]",
            "a key missing": b'{"commit":"%s","tree":"%s","source":"git"}' % (COMMIT.encode(), TREE.encode()),
            "an extra key": good.replace('"source"', '"ref":"HEAD","source"').encode("utf-8"),
            "unknown source": marker_bytes(COMMIT, TREE, source="svn"),
            "git build without commit": marker_bytes(None, TREE),
            "short commit": marker_bytes(COMMIT[:12], TREE),
            "upper-case tree": marker_bytes(COMMIT, TREE.upper().replace("2", "A")),
            "folder build with a commit": marker_bytes(COMMIT, None, source="folder"),
            "local time": marker_bytes(COMMIT, TREE, built_utc="2026-09-30T12:00:00"),
            "space for T": marker_bytes(COMMIT, TREE, built_utc="2026-09-30 12:00:00Z"),
            "impossible date": marker_bytes(COMMIT, TREE, built_utc="2026-02-30T12:00:00Z"),
        }
        for label, data in cases.items():
            with self.subTest(label), self.assertRaises(provenance.MarkerError):
                provenance.parse_marker(data)


class CompareTest(unittest.TestCase):
    def compare(self, entries, blobs=BLOBS, commit=COMMIT, tree=TREE):
        lines, ok = provenance.compare(entries, blobs, commit, tree)
        return lines, ok, _states(lines), "\n".join(lines)

    def test_exact_build_with_its_marker_passes(self) -> None:
        lines, ok, states, _ = self.compare(_entries())

        self.assertTrue(ok, lines)
        self.assertEqual(
            states,
            {"$PBOPREFIX$": "exact", "config.cpp": "exact",
             "scripts/5_Mission/MCPBridge.c": "exact", "mcp_build.json": "marker"},
        )
        self.assertEqual(lines[-1], "marker OK")

    def test_a_changed_file_fails(self) -> None:
        blobs = dict(BLOBS, **{"config.cpp": b"class CfgPatches { changed };\n"})

        lines, ok, states, _ = self.compare(_entries(blobs))

        self.assertFalse(ok)
        self.assertEqual(states["config.cpp"], "DIFF")

    def test_line_endings_alone_still_fail(self) -> None:
        crlf = {rel: data.replace(b"\r\n", b"\n") for rel, data in BLOBS.items()}

        lines, ok, states, _ = self.compare(_entries(crlf))

        self.assertFalse(ok)
        self.assertEqual(states["scripts/5_Mission/MCPBridge.c"], "DIFF-EOL")

    def test_an_extra_entry_fails(self) -> None:
        entries = _entries() + [("scripts\\5_Mission\\MCPBridge.c.bak_old", b"// stale\n")]

        lines, ok, states, _ = self.compare(entries)

        self.assertFalse(ok)
        self.assertEqual(states["scripts/5_Mission/MCPBridge.c.bak_old"], "MISSING_IN_GIT")

    def test_a_missing_file_fails(self) -> None:
        blobs = dict(BLOBS, **{"gui/layouts/mcp_dialog.layout": b"FrameWidgetClass {}\n"})

        lines, ok, states, _ = self.compare(_entries(), blobs=blobs)

        self.assertFalse(ok)
        self.assertEqual(states["gui/layouts/mcp_dialog.layout"], "NOT_IN_PBO")

    def test_a_case_difference_is_another_file(self) -> None:
        entries = [(("Config.cpp" if name == "config.cpp" else name), data) for name, data in _entries()]

        lines, ok, states, _ = self.compare(entries)

        self.assertFalse(ok)
        self.assertEqual(states["Config.cpp"], "MISSING_IN_GIT")
        self.assertEqual(states["config.cpp"], "NOT_IN_PBO")

    def test_a_duplicate_entry_fails(self) -> None:
        entries = _entries() + [("CONFIG.CPP", BLOBS["config.cpp"])]

        lines, ok, _states_, text = self.compare(entries)

        self.assertFalse(ok)
        self.assertIn("CONFIG.CPP", text)
        self.assertIn("DUPLICATE", text)

    def test_a_build_without_marker_fails(self) -> None:
        entries = [entry for entry in _entries() if entry[0] != "mcp_build.json"]

        lines, ok, _states_, text = self.compare(entries)

        self.assertFalse(ok)
        self.assertIn("marker MISSING", text)

    def test_a_marker_from_another_commit_fails(self) -> None:
        lines, ok, _states_, text = self.compare(_entries(marker=marker_bytes(OTHER, OTHER)))

        self.assertFalse(ok)
        self.assertIn(f"marker FAIL: commit {OTHER} is not the ref's commit {COMMIT}", text)
        self.assertNotIn("marker note", text)

    def test_same_addon_tree_from_another_commit_still_fails_with_a_note(self) -> None:
        lines, ok, _states_, text = self.compare(_entries(marker=marker_bytes(OTHER, TREE)))

        self.assertFalse(ok)
        self.assertIn("marker FAIL: commit", text)
        self.assertIn("marker note: its addon/ tree equals the ref's", text)

    def test_a_marker_naming_the_wrong_tree_fails(self) -> None:
        lines, ok, _states_, text = self.compare(_entries(marker=marker_bytes(COMMIT, OTHER)))

        self.assertFalse(ok)
        self.assertIn(f"marker FAIL: tree {OTHER}", text)

    def test_a_folder_build_fails(self) -> None:
        lines, ok, _states_, text = self.compare(
            _entries(marker=marker_bytes(None, None, source="folder"))
        )

        self.assertFalse(ok)
        self.assertIn("built from a folder (-Source)", text)

    def test_an_invalid_marker_fails(self) -> None:
        lines, ok, _states_, text = self.compare(_entries(marker=b"not json\n"))

        self.assertFalse(ok)
        self.assertIn("marker INVALID", text)

    def test_git_tracking_the_marker_name_fails(self) -> None:
        blobs = dict(BLOBS, **{"MCP_Build.json": b"{}\n"})

        lines, ok, states, _ = self.compare(_entries(), blobs=blobs)

        self.assertFalse(ok)
        self.assertEqual(states["MCP_Build.json"], "RESERVED")

    def test_a_marker_below_the_root_is_an_ordinary_file(self) -> None:
        entries = _entries() + [("scripts\\mcp_build.json", marker_bytes(COMMIT, TREE))]

        lines, ok, states, _ = self.compare(entries)

        self.assertFalse(ok)
        self.assertEqual(states["scripts/mcp_build.json"], "MISSING_IN_GIT")
        self.assertIn("marker OK", lines)


def _git(repo: Path, *args: str, stdin: bytes | None = None) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=stdin,
        capture_output=True,
        check=False,
        timeout=_TIMEOUT_S,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({completed.returncode}):\n"
            + completed.stderr.decode("utf-8", "replace")
        )
    return completed.stdout.decode("utf-8", "replace").strip()


class CommandLineTest(unittest.TestCase):
    """The tool as the promotion recipe runs it: python pbo_provenance.py <pbo> <repo> <ref>."""

    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.name", "pbo-provenance-test")
        _git(self.repo, "config", "user.email", "pbo-provenance-test@example.invalid")
        _git(self.repo, "config", "core.autocrlf", "false")
        for rel, data in BLOBS.items():
            path = self.repo / "addon" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (self.repo / "README.txt").write_bytes(b"outside addon/, never compared\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "seed addon")
        self.commit = _git(self.repo, "rev-parse", "HEAD")
        self.tree = _git(self.repo, "rev-parse", "HEAD:addon")
        self.pbo = self.root / "DayZ_MCP.pbo"
        self.pbo.write_bytes(build_pbo(_entries(marker=marker_bytes(self.commit, self.tree))))

    def run_tool(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(TOOL), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=_TIMEOUT_S,
        )

    @slow_test
    def test_the_build_of_a_commit_passes_against_it(self) -> None:
        for ref in ("HEAD", self.commit):
            with self.subTest(ref=ref):
                done = self.run_tool(str(self.pbo), str(self.repo), ref)

                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertEqual(done.stdout.splitlines()[-1], "PROVENANCE OK entries=4")
                self.assertIn(f"sha256 {hashlib.sha256(self.pbo.read_bytes()).hexdigest().upper()}", done.stdout)

    @slow_test
    def test_a_later_commit_fails_on_the_file_and_on_the_marker(self) -> None:
        bridge = self.repo / "addon" / "scripts" / "5_Mission" / "MCPBridge.c"
        bridge.write_bytes(b"// MCPBridge, another session's work\n")
        _git(self.repo, "commit", "-q", "-am", "someone else moves the bridge")

        done = self.run_tool(str(self.pbo), str(self.repo), "HEAD")

        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("scripts/5_Mission/MCPBridge.c", done.stdout)
        self.assertEqual(_states(done.stdout.splitlines())["scripts/5_Mission/MCPBridge.c"], "DIFF")
        self.assertIn(f"marker FAIL: commit {self.commit} is not the ref's commit", done.stdout)
        self.assertEqual(done.stdout.splitlines()[-1], "PROVENANCE FAIL entries=4")
        # The build still proves itself against the commit that made it.
        self.assertEqual(self.run_tool(str(self.pbo), str(self.repo), self.commit).returncode, 0)

    @slow_test
    def test_a_commit_that_leaves_addon_alone_still_fails_with_a_note(self) -> None:
        (self.repo / "README.txt").write_bytes(b"docs only\n")
        _git(self.repo, "commit", "-q", "-am", "docs only")

        done = self.run_tool(str(self.pbo), str(self.repo), "HEAD")

        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("marker note: its addon/ tree equals the ref's", done.stdout)

    @slow_test
    def test_a_file_that_is_not_a_pbo_exits_2(self) -> None:
        garbage = self.root / "garbage.pbo"
        garbage.write_bytes(b"\x00DayZ-MCP release fixture\xff\r\n")

        done = self.run_tool(str(garbage), str(self.repo), "HEAD")

        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("is not a PBO this tool can compare", done.stderr)

    @slow_test
    def test_an_unknown_ref_exits_2(self) -> None:
        done = self.run_tool(str(self.pbo), str(self.repo), "no-such-ref")

        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("rev-parse", done.stderr)

    @slow_test
    def test_a_symlink_in_addon_exits_2(self) -> None:
        target = _git(self.repo, "hash-object", "-w", "--stdin", stdin=b"config.cpp")
        _git(self.repo, "update-index", "--add", "--cacheinfo", f"120000,{target},addon/link.cpp")
        _git(self.repo, "commit", "-q", "-m", "a symlink pack-addon refuses")

        done = self.run_tool(str(self.pbo), str(self.repo), "HEAD")

        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("symlink or a submodule", done.stderr)

    @slow_test
    def test_wrong_arguments_print_the_usage_line_and_exit_2(self) -> None:
        for args in ((), (str(self.pbo), str(self.repo)), (str(self.pbo), str(self.repo), "--", "-x")):
            with self.subTest(args=args):
                done = self.run_tool(*args)

                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertIn("usage: pbo_provenance.py", done.stderr)


class DocumentedUsageTest(unittest.TestCase):
    def test_the_module_docstring_starts_with_the_usage_line(self) -> None:
        self.assertIn(
            "Usage: python tools/dev/pbo_provenance.py <pbo> <repo> <ref>",
            provenance.__doc__.splitlines()[:4],
        )


if __name__ == "__main__":
    unittest.main()
