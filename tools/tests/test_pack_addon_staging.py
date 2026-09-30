"""pack-addon.ps1 stages git-tracked addon/ (or a filtered folder), not the live tree.

fb-63c9: -packonly makes AddonBuilder ignore include.lst, so packing the live
folder ships leftovers. Staging from git archive (default) or a walked copy
keeps the PBO to the intended tree. Tests always pass -StageOnly.

fb-20260819-024951-e307: the stage also gets mcp_build.json in its root, the one
file that is not in the tree: the commit, the addon/ tree id and the UTC build
time, so the PBO says which commit built it. tools/dev/pbo_provenance.py checks a
build against it; the chain from a real stage to that tool runs here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from tests._addon_paths import addon_root
from tests._tiers import slow_test
from tests.pbo_helpers import build_pbo, stage_entries


TOOLS_DIR = Path(__file__).resolve().parents[1]
PACK_PS1 = TOOLS_DIR / "pack-addon.ps1"
PROVENANCE_TOOL = TOOLS_DIR / "dev" / "pbo_provenance.py"
WORKSPACE_ROOT = TOOLS_DIR.parent
_POISONED_PSMODULEPATH = "/git/bash/poisoned/Modules"
_TIMEOUT_S = 90
_MOD_NAME = "DayZ_MCP"
_MARKER = "mcp_build.json"


def _is_reparse(path: Path) -> bool:
    attrs = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
        timeout=_TIMEOUT_S,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "git {args} failed ({code}):\n{err}".format(
                args=" ".join(args),
                code=completed.returncode,
                err=completed.stderr.decode("utf-8", "replace"),
            )
        )
    return completed.stdout.decode("utf-8", "replace")


def _sha256_upper(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _seed_addon(addon: Path) -> dict[str, bytes]:
    positives = {
        "$PBOPREFIX$": b"DayZ_MCP\n",
        "config.cpp": b"class CfgPatches {};\n",
        "scripts/5_Mission/MCPBridge.c": b"// MCPBridge\n",
    }
    for rel, data in positives.items():
        _write_bytes(addon / rel, data)
    return positives


def _seed_negatives(addon: Path, outside: Path) -> list[str]:
    _write_bytes(addon / "CLAUDE.md", b"# leftover\n")
    _write_bytes(
        addon / "scripts" / "5_Mission" / "MCPBridge.c.bak_pre_x",
        b"// bak leftover\n",
    )
    _write_bytes(
        addon / "scripts" / "5_Mission" / "MCPBridge.c_bak_y",
        b"// glued bak leftover\n",
    )
    _write_bytes(
        addon / "gui" / "layouts" / "hot" / "mcp_hot.layout",
        b"HotLayout {}\n",
    )
    outside.mkdir(parents=True, exist_ok=True)
    _write_bytes(outside / "secret.c", b"// outside\n")
    linked = addon / "linked"
    import _winapi

    _winapi.CreateJunction(str(outside), str(linked))
    return [
        "CLAUDE.md",
        "scripts/5_Mission/MCPBridge.c.bak_pre_x",
        "scripts/5_Mission/MCPBridge.c_bak_y",
        "gui/layouts/hot/mcp_hot.layout",
        "linked",
    ]


def _remove_junction(path: Path) -> None:
    if path.exists() or _is_reparse(path):
        os.rmdir(path)


def _init_repo(repo: Path) -> None:
    _git(repo, "init")
    _git(repo, "config", "user.name", "pack-addon-staging-test")
    _git(repo, "config", "user.email", "pack-addon-staging-test@example.invalid")
    _git(repo, "config", "core.autocrlf", "false")


def _require_stage_only_switch(test: unittest.TestCase, script: Path) -> None:
    text = script.read_text(encoding="utf-8")
    test.assertIn(
        "[switch]$StageOnly",
        text,
        "refusing to launch pack-addon.ps1 without [switch]$StageOnly",
    )


def _pack_addon_fenced_lines(text: str) -> list[str]:
    found: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence and "pack-addon.ps1" in line:
            found.append(line)
    return found


def _leading_comment_line_numbers(ps1_text: str) -> set[int]:
    nums: list[int] = []
    started = False
    for i, line in enumerate(ps1_text.splitlines(), 1):
        if line.startswith("#Requires"):
            continue
        if not started:
            if line.startswith("#"):
                started = True
                nums.append(i)
            elif line.strip() == "":
                continue
            else:
                break
        else:
            if line.startswith("#"):
                nums.append(i)
            else:
                break
    return set(nums)


def _run_stage_only(
    test: unittest.TestCase,
    script: Path,
    stage_root: Path,
    destination: Path,
    extra_args: list[str],
) -> subprocess.CompletedProcess[str]:
    _require_stage_only_switch(test, script)
    env = os.environ.copy()
    env["PSModulePath"] = _POISONED_PSMODULEPATH
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-StageOnly",
        "-ModName",
        _MOD_NAME,
        "-StageRoot",
        str(stage_root),
        "-Destination",
        str(destination),
        *extra_args,
    ]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=_TIMEOUT_S,
        env=env,
    )


def _run_stage_only_redirected_stderr(
    test: unittest.TestCase,
    script: Path,
    stage_root: Path,
    destination: Path,
    ref: str,
) -> subprocess.CompletedProcess[str]:
    _require_stage_only_switch(test, script)
    command = (
        "& '{script}' -StageOnly -ModName {mod} -StageRoot '{stage}' "
        "-Destination '{dest}' -Ref {ref} 2>$null"
    ).format(
        script=str(script),
        mod=_MOD_NAME,
        stage=str(stage_root),
        dest=str(destination),
        ref=ref,
    )
    env = os.environ.copy()
    env["PSModulePath"] = _POISONED_PSMODULEPATH
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=_TIMEOUT_S,
        env=env,
    )


def _prepare_git_pack_repo(root: Path) -> tuple[Path, Path, Path]:
    repo = root / "repo"
    repo.mkdir()
    script = repo / "tools" / "pack-addon.ps1"
    script.parent.mkdir(parents=True)
    script.write_bytes(PACK_PS1.read_bytes())
    addon = repo / "addon"
    _seed_addon(addon)
    _init_repo(repo)
    return repo, script, addon


def _manifest_from_stdout(stdout: str) -> dict:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError("pack-addon.ps1 produced no stdout to parse as JSON")
    return json.loads(lines[-1])


def _assert_json_arrays(test: unittest.TestCase, manifest: dict) -> None:
    test.assertIsInstance(manifest["files"], list)
    test.assertIsInstance(manifest["excluded"], list)


def _utc_now_seconds() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _split_marker(test: unittest.TestCase, manifest: dict) -> list[dict]:
    """The manifest's tree files; asserts the marker is listed once, with its hash."""
    markers = [entry for entry in manifest["files"] if entry["path"] == _MARKER]
    test.assertEqual(len(markers), 1, manifest["files"])
    marker_file = Path(manifest["stage"]) / _MARKER
    test.assertEqual(markers[0]["sha256"], _sha256_upper(marker_file.read_bytes()))
    return [entry for entry in manifest["files"] if entry["path"] != _MARKER]


def _assert_build_marker(
    test: unittest.TestCase,
    stage: Path,
    *,
    commit: str | None,
    tree: str | None,
    source: str,
    not_before: datetime,
    not_after: datetime,
) -> None:
    raw = (stage / _MARKER).read_bytes()
    test.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "the marker must not carry a BOM")
    test.assertTrue(raw.endswith(b"}\n"), raw)
    marker = json.loads(raw.decode("utf-8"))
    test.assertEqual(
        {key: marker.get(key) for key in ("commit", "tree", "source")},
        {"commit": commit, "tree": tree, "source": source},
    )
    test.assertEqual(sorted(marker), ["built_utc", "commit", "source", "tree"])
    built = datetime.strptime(marker["built_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    test.assertLessEqual(not_before, built, "built_utc predates the run: not UTC?")
    test.assertLessEqual(built, not_after, "built_utc postdates the run: not UTC?")


def _run_provenance(pbo: Path, repo: Path, ref: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(PROVENANCE_TOOL), str(pbo), str(repo), ref],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=_TIMEOUT_S,
    )


def _stage_relatives(stage: Path) -> list[str]:
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(stage, followlinks=False):
        keep: list[str] = []
        for name in dirnames:
            child = Path(dirpath) / name
            test_reparse = _is_reparse(child)
            if test_reparse:
                continue
            keep.append(name)
        dirnames[:] = keep
        for name in filenames:
            full = Path(dirpath) / name
            rel = full.relative_to(stage).as_posix()
            files.append(rel)
    files.sort()
    return files


@unittest.skipUnless(sys.platform == "win32", "pack-addon.ps1 is launched with Windows PowerShell")
class PackAddonStagingTest(unittest.TestCase):
    def setUp(self) -> None:
        _require_stage_only_switch(self, PACK_PS1)

    @slow_test
    def test_fb_63c9_git_mode_packs_commit_tree_not_worktree(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            outside = root / "outside"
            stage_root = root / "stage"
            destination = root / "dest-must-not-exist"
            stage_root.mkdir()
            repo.mkdir()
            script = repo / "tools" / "pack-addon.ps1"
            script.parent.mkdir(parents=True)
            script.write_bytes(PACK_PS1.read_bytes())
            addon = repo / "addon"
            positives = _seed_addon(addon)
            _init_repo(repo)
            _git(repo, "add", "tools/pack-addon.ps1", "addon")
            _git(repo, "commit", "-m", "seed addon")
            sha = _git(repo, "rev-parse", "HEAD").strip()
            negatives = _seed_negatives(addon, outside)
            _write_bytes(
                addon / "scripts" / "5_Mission" / "MCPBridge.c",
                b"// uncommitted edit, must not be packed\n",
            )
            not_before = _utc_now_seconds()
            try:
                completed = _run_stage_only(
                    self, script, stage_root, destination, []
                )
                not_after = _utc_now_seconds()
                self.assertEqual(
                    completed.returncode,
                    0,
                    "stdout={0!r}\nstderr={1!r}".format(
                        completed.stdout, completed.stderr
                    ),
                )
                manifest = _manifest_from_stdout(completed.stdout)
                _assert_json_arrays(self, manifest)
                self.assertEqual(manifest["source"], "git")
                self.assertEqual(manifest["commit"], sha)
                self.assertFalse(destination.exists())
                stage = Path(manifest["stage"])
                self.assertEqual(stage.name, _MOD_NAME)
                self.assertTrue(stage.is_dir())
                ls_tree = [
                    line.replace("\\", "/")
                    for line in _git(
                        repo, "ls-tree", "-r", "--name-only", sha, "--", "addon"
                    ).splitlines()
                    if line
                ]
                expected_paths = [
                    line[len("addon/") :] if line.startswith("addon/") else line
                    for line in ls_tree
                ]
                expected_paths.sort()
                tree_files = _split_marker(self, manifest)
                got_paths = [entry["path"] for entry in tree_files]
                self.assertEqual(got_paths, expected_paths)
                self.assertEqual(
                    _stage_relatives(stage), sorted(expected_paths + [_MARKER])
                )
                _assert_build_marker(
                    self,
                    stage,
                    commit=sha,
                    tree=_git(repo, "rev-parse", "{0}:addon".format(sha)).strip(),
                    source="git",
                    not_before=not_before,
                    not_after=not_after,
                )
                for entry in tree_files:
                    blob = subprocess.run(
                        [
                            "git",
                            "-C",
                            str(repo),
                            "cat-file",
                            "blob",
                            "{0}:addon/{1}".format(sha, entry["path"]),
                        ],
                        capture_output=True,
                        check=True,
                        timeout=_TIMEOUT_S,
                    ).stdout
                    self.assertEqual(entry["sha256"], _sha256_upper(blob))
                    staged = (stage / entry["path"]).read_bytes()
                    self.assertEqual(staged, blob)
                    self.assertEqual(staged, positives[entry["path"]])
                combined = completed.stdout + completed.stderr
                for rel in negatives:
                    self.assertNotIn(rel, expected_paths)
                    staged_path = stage / rel
                    self.assertFalse(
                        staged_path.exists(),
                        "negative path leaked into the stage: {0}".format(rel),
                    )
                for dirpath, dirnames, filenames in os.walk(stage, followlinks=False):
                    for name in dirnames + filenames:
                        child = Path(dirpath) / name
                        self.assertFalse(
                            _is_reparse(child),
                            "reparse point in stage: {0}".format(child),
                        )
                self.assertNotIn("uncommitted edit", (stage / "scripts" / "5_Mission" / "MCPBridge.c").read_text(encoding="utf-8"))
                self.assertIsInstance(manifest["excluded"], list)
            finally:
                _remove_junction(addon / "linked")

    @slow_test
    def test_fb_63c9_folder_mode_copies_positives_and_names_exclusions(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            outside = root / "outside"
            stage_root = root / "stage"
            destination = root / "dest-must-not-exist"
            stage_root.mkdir()
            positives = _seed_addon(source)
            _seed_negatives(source, outside)
            not_before = _utc_now_seconds()
            try:
                completed = _run_stage_only(
                    self,
                    PACK_PS1,
                    stage_root,
                    destination,
                    ["-Source", str(source)],
                )
                not_after = _utc_now_seconds()
                combined = completed.stdout + completed.stderr
                self.assertEqual(
                    completed.returncode,
                    0,
                    "stdout={0!r}\nstderr={1!r}".format(
                        completed.stdout, completed.stderr
                    ),
                )
                manifest = _manifest_from_stdout(completed.stdout)
                _assert_json_arrays(self, manifest)
                self.assertEqual(manifest["source"], "folder")
                self.assertIsNone(manifest["commit"])
                self.assertFalse(destination.exists())
                stage = Path(manifest["stage"])
                self.assertTrue(stage.is_dir())
                tree_files = _split_marker(self, manifest)
                got_paths = [entry["path"] for entry in tree_files]
                expected_included = sorted(
                    list(positives.keys()) + ["gui/layouts/hot/mcp_hot.layout"]
                )
                self.assertEqual(got_paths, expected_included)
                self.assertEqual(
                    _stage_relatives(stage), sorted(expected_included + [_MARKER])
                )
                _assert_build_marker(
                    self,
                    stage,
                    commit=None,
                    tree=None,
                    source="folder",
                    not_before=not_before,
                    not_after=not_after,
                )
                for entry in tree_files:
                    data = (source / entry["path"]).read_bytes()
                    self.assertEqual(entry["sha256"], _sha256_upper(data))
                    self.assertEqual((stage / entry["path"]).read_bytes(), data)
                named = set(manifest["excluded"])
                must_exclude = [
                    "CLAUDE.md",
                    "scripts/5_Mission/MCPBridge.c.bak_pre_x",
                    "scripts/5_Mission/MCPBridge.c_bak_y",
                    "linked",
                ]
                for rel in must_exclude:
                    self.assertIn(rel, named)
                    self.assertIn(rel, combined)
                    self.assertFalse((stage / rel).exists())
                self.assertNotIn("gui/layouts/hot/mcp_hot.layout", named)
                for dirpath, dirnames, filenames in os.walk(stage, followlinks=False):
                    for name in dirnames + filenames:
                        child = Path(dirpath) / name
                        self.assertFalse(_is_reparse(child))
            finally:
                _remove_junction(source / "linked")

    @slow_test
    def test_fb_63c9_git_mode_poisoned_psmodulepath_exits_0(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            stage_root = root / "stage"
            destination = root / "dest-must-not-exist"
            stage_root.mkdir()
            repo.mkdir()
            script = repo / "tools" / "pack-addon.ps1"
            script.parent.mkdir(parents=True)
            script.write_bytes(PACK_PS1.read_bytes())
            _seed_addon(repo / "addon")
            _init_repo(repo)
            _git(repo, "add", "tools/pack-addon.ps1", "addon")
            _git(repo, "commit", "-m", "seed addon")
            completed = _run_stage_only(self, script, stage_root, destination, [])
            self.assertEqual(
                completed.returncode,
                0,
                "stdout={0!r}\nstderr={1!r}".format(
                    completed.stdout, completed.stderr
                ),
            )
            manifest = _manifest_from_stdout(completed.stdout)
            self.assertEqual(manifest["source"], "git")
            self.assertIsInstance(manifest["files"], list)

    @slow_test
    def test_fb_63c9_folder_mode_poisoned_psmodulepath_exits_0(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            stage_root = root / "stage"
            destination = root / "dest-must-not-exist"
            stage_root.mkdir()
            _seed_addon(source)
            completed = _run_stage_only(
                self,
                PACK_PS1,
                stage_root,
                destination,
                ["-Source", str(source)],
            )
            self.assertEqual(
                completed.returncode,
                0,
                "stdout={0!r}\nstderr={1!r}".format(
                    completed.stdout, completed.stderr
                ),
            )
            manifest = _manifest_from_stdout(completed.stdout)
            self.assertEqual(manifest["source"], "folder")
            self.assertIsNone(manifest["commit"])

    @slow_test
    def test_fb_63c9_workspace_head_matches_git_addon(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage_root = root / "stage"
            destination = root / "dest-must-not-exist"
            stage_root.mkdir()
            sha = _git(WORKSPACE_ROOT, "rev-parse", "HEAD").strip()
            not_before = _utc_now_seconds()
            completed = _run_stage_only(
                self, PACK_PS1, stage_root, destination, ["-Ref", sha]
            )
            not_after = _utc_now_seconds()
            self.assertEqual(
                completed.returncode,
                0,
                "stdout={0!r}\nstderr={1!r}".format(
                    completed.stdout, completed.stderr
                ),
            )
            manifest = _manifest_from_stdout(completed.stdout)
            _assert_json_arrays(self, manifest)
            self.assertEqual(manifest["source"], "git")
            self.assertEqual(manifest["commit"], sha)
            self.assertFalse(destination.exists())
            ls_tree = [
                line.replace("\\", "/")
                for line in _git(
                    WORKSPACE_ROOT,
                    "ls-tree",
                    "-r",
                    "--name-only",
                    sha,
                    "--",
                    "addon",
                ).splitlines()
                if line
            ]
            expected_paths = [
                line[len("addon/") :] if line.startswith("addon/") else line
                for line in ls_tree
            ]
            expected_paths.sort()
            tree_files = _split_marker(self, manifest)
            got_paths = [entry["path"] for entry in tree_files]
            self.assertEqual(got_paths, expected_paths)
            stage = Path(manifest["stage"])
            self.assertEqual(
                _stage_relatives(stage), sorted(expected_paths + [_MARKER])
            )
            _assert_build_marker(
                self,
                stage,
                commit=sha,
                tree=_git(WORKSPACE_ROOT, "rev-parse", "{0}:addon".format(sha)).strip(),
                source="git",
                not_before=not_before,
                not_after=not_after,
            )
            for entry in tree_files:
                blob = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(WORKSPACE_ROOT),
                        "cat-file",
                        "blob",
                        "{0}:addon/{1}".format(sha, entry["path"]),
                    ],
                    capture_output=True,
                    check=True,
                    timeout=_TIMEOUT_S,
                ).stdout
                self.assertEqual(entry["sha256"], _sha256_upper(blob))
                self.assertEqual((stage / entry["path"]).read_bytes(), blob)

    def test_fb_63c9_r2_runbooks_pack_from_git(self) -> None:
        quickstart = (WORKSPACE_ROOT / "QUICKSTART.md").read_text(encoding="utf-8")
        release = (WORKSPACE_ROOT / "docs" / "RELEASE.md").read_text(encoding="utf-8")
        ps1_text = PACK_PS1.read_text(encoding="utf-8")
        ps1_lines = ps1_text.splitlines()
        comment_lines = _leading_comment_line_numbers(ps1_text)
        self.assertTrue(comment_lines)

        for name, text in (("QUICKSTART.md", quickstart), ("docs/RELEASE.md", release)):
            found = _pack_addon_fenced_lines(text)
            self.assertGreaterEqual(
                len(found), 1, "{0} has no fenced pack-addon.ps1 command".format(name)
            )
            for line in found:
                self.assertNotIn("-source", line.lower(), line)

        links = re.findall(r"pack-addon\.ps1#L(\d+)-L(\d+)", release)
        self.assertGreaterEqual(len(links), 2, release)
        first_a, first_b = int(links[0][0]), int(links[0][1])
        second_a, second_b = int(links[1][0]), int(links[1][1])
        first_range = set(range(first_a, first_b + 1))
        self.assertTrue(
            first_range.issubset(comment_lines),
            "first range {0}-{1} is not inside the leading comment block {2}".format(
                first_a, first_b, sorted(comment_lines)
            ),
        )
        self.assertTrue(
            any(
                "P:\\ work drive" in ps1_lines[i - 1]
                for i in range(first_a, first_b + 1)
                if 1 <= i <= len(ps1_lines)
            ),
            "first range does not contain a line with P:\\ work drive",
        )
        self.assertTrue(
            any(
                "bytes)" in ps1_lines[i - 1]
                for i in range(second_a, second_b + 1)
                if 1 <= i <= len(ps1_lines)
            ),
            "second range {0}-{1} does not contain a line with bytes)".format(
                second_a, second_b
            ),
        )

    @slow_test
    def test_fb_63c9_r2_git_mode_refuses_stage_that_differs_from_tree(self) -> None:
        cases = (
            (
                "foreign.c",
                b"foreign.c export-ignore\n",
                {"foreign.c": b"// foreign\n"},
            ),
            (
                "config.cpp",
                b"config.cpp text eol=crlf\n",
                {"config.cpp": b"class CfgPatches {};\n"},
            ),
        )
        for named, attributes, extra_files in cases:
            with self.subTest(named=named), TemporaryDirectory() as tmp:
                root = Path(tmp)
                repo, script, addon = _prepare_git_pack_repo(root)
                _write_bytes(addon / ".gitattributes", attributes)
                for rel, data in extra_files.items():
                    _write_bytes(addon / rel, data)
                _git(repo, "add", "tools/pack-addon.ps1", "addon")
                _git(repo, "commit", "-m", "seed with gitattributes")
                stage_root = root / "stage"
                destination = root / "dest-must-not-exist"
                stage_root.mkdir()
                completed = _run_stage_only(self, script, stage_root, destination, [])
                combined = completed.stdout + completed.stderr
                self.assertNotEqual(
                    completed.returncode,
                    0,
                    "stdout={0!r}\nstderr={1!r}".format(
                        completed.stdout, completed.stderr
                    ),
                )
                self.assertIn(named, combined)
                for line in completed.stdout.splitlines():
                    self.assertFalse(
                        line.startswith('{"commit'),
                        "manifest printed on mismatch: {0!r}".format(line),
                    )

    @slow_test
    def test_fb_63c9_r3_export_subst_is_not_expanded(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, script, addon = _prepare_git_pack_repo(root)
            _write_bytes(addon / ".gitattributes", b"config.cpp export-subst\n")
            _write_bytes(
                addon / "config.cpp",
                b"class CfgPatches {};\nid $Format:%H$\n",
            )
            _git(repo, "add", "tools/pack-addon.ps1", "addon")
            _git(repo, "commit", "-m", "seed with export-subst")
            sha = _git(repo, "rev-parse", "HEAD").strip()
            stage_root = root / "stage"
            destination = root / "dest-must-not-exist"
            stage_root.mkdir()
            completed = _run_stage_only(self, script, stage_root, destination, [])
            self.assertEqual(
                completed.returncode,
                0,
                "stdout={0!r}\nstderr={1!r}".format(
                    completed.stdout, completed.stderr
                ),
            )
            manifest = _manifest_from_stdout(completed.stdout)
            stage = Path(manifest["stage"])
            blob = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "cat-file",
                    "blob",
                    "{0}:addon/config.cpp".format(sha),
                ],
                capture_output=True,
                check=True,
                timeout=_TIMEOUT_S,
            ).stdout
            self.assertEqual((stage / "config.cpp").read_bytes(), blob)

    @slow_test
    def test_fb_63c9_r2_git_warning_on_stderr_does_not_abort(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, script, addon = _prepare_git_pack_repo(root)
            _git(repo, "add", "tools/pack-addon.ps1", "addon")
            _git(repo, "commit", "-m", "seed addon")
            sha = _git(repo, "rev-parse", "HEAD").strip()
            _git(repo, "branch", "release")
            _git(repo, "tag", "release")
            stage_root = root / "stage"
            destination = root / "dest-must-not-exist"
            stage_root.mkdir()
            completed = _run_stage_only_redirected_stderr(
                self, script, stage_root, destination, "release"
            )
            self.assertEqual(
                completed.returncode,
                0,
                "stdout={0!r}\nstderr={1!r}".format(
                    completed.stdout, completed.stderr
                ),
            )
            manifest = _manifest_from_stdout(completed.stdout)
            self.assertEqual(manifest["commit"], sha)

    @slow_test
    def test_fb_63c9_r2_refuses_stage_root_inside_source(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            stage_root = source / "stage"
            destination = root / "dest-must-not-exist"
            completed = _run_stage_only(
                self, PACK_PS1, stage_root, destination, ["-Source", str(source)]
            )
            combined = completed.stdout + completed.stderr
            self.assertNotEqual(
                completed.returncode,
                0,
                "stdout={0!r}\nstderr={1!r}".format(
                    completed.stdout, completed.stderr
                ),
            )
            self.assertIn("overlaps", combined)
            self.assertFalse(stage_root.exists())

    @slow_test
    def test_fb_63c9_r2_refuses_stage_root_equal_to_destination(self) -> None:
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "same-missing"
            completed = _run_stage_only(
                self, PACK_PS1, missing, missing, ["-Ref", "HEAD"]
            )
            combined = completed.stdout + completed.stderr
            self.assertNotEqual(
                completed.returncode,
                0,
                "stdout={0!r}\nstderr={1!r}".format(
                    completed.stdout, completed.stderr
                ),
            )
            self.assertIn("overlaps", combined)
            self.assertFalse(missing.exists())

    @slow_test
    def test_fb_63c9_r2_missing_source_creates_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "missing-source"
            stage_root = source / "stage"
            destination = root / "dest-must-not-exist"
            completed = _run_stage_only(
                self, PACK_PS1, stage_root, destination, ["-Source", str(source)]
            )
            self.assertNotEqual(
                completed.returncode,
                0,
                "stdout={0!r}\nstderr={1!r}".format(
                    completed.stdout, completed.stderr
                ),
            )
            self.assertFalse(source.exists())
            self.assertFalse(stage_root.exists())

    @slow_test
    def test_fb_63c9_r2_folder_mode_excludes_backups_in_any_case(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            stage_root = root / "stage"
            destination = root / "dest-must-not-exist"
            stage_root.mkdir()
            positives = _seed_addon(source)
            backups = ["MCPBridge.c.BAK_pre_x", "x_BAK_old.c", ".BAK_y.c"]
            for name in backups:
                _write_bytes(source / name, b"bak leftover\n")
            completed = _run_stage_only(
                self, PACK_PS1, stage_root, destination, ["-Source", str(source)]
            )
            self.assertEqual(
                completed.returncode,
                0,
                "stdout={0!r}\nstderr={1!r}".format(
                    completed.stdout, completed.stderr
                ),
            )
            manifest = _manifest_from_stdout(completed.stdout)
            named = set(manifest["excluded"])
            stage = Path(manifest["stage"])
            for rel in backups:
                self.assertIn(rel, named)
                self.assertFalse((stage / rel).exists())
            included = [entry["path"] for entry in manifest["files"]]
            for rel in positives:
                self.assertIn(rel, included)
                self.assertTrue((stage / rel).is_file())

    def test_fb_63c9_r2_default_stage_root_is_ignored_by_git(self) -> None:
        probe = "temp/dayz-pack-stage/{0}/DayZ_MCP/config.cpp".format(uuid.uuid4())
        completed = subprocess.run(
            ["git", "-C", str(WORKSPACE_ROOT), "check-ignore", "-q", probe],
            capture_output=True,
            text=True,
            check=False,
            timeout=_TIMEOUT_S,
        )
        self.assertEqual(completed.returncode, 0)

    @slow_test
    def test_e307_git_mode_refuses_a_tracked_marker_name(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, script, addon = _prepare_git_pack_repo(root)
            # Another spelling than the marker's: the stage is on a case-insensitive disk.
            _write_bytes(addon / "MCP_Build.json", b"{}\n")
            _git(repo, "add", "tools/pack-addon.ps1", "addon")
            _git(repo, "commit", "-m", "track the reserved name")
            stage_root = root / "stage"
            destination = root / "dest-must-not-exist"
            stage_root.mkdir()
            completed = _run_stage_only(self, script, stage_root, destination, [])
            combined = " ".join((completed.stdout + completed.stderr).split())
            self.assertNotEqual(completed.returncode, 0, combined)
            self.assertIn("MCP_Build.json", combined)
            self.assertIn("reserved", combined)
            for line in completed.stdout.splitlines():
                self.assertFalse(
                    line.startswith('{"commit'),
                    "manifest printed on refusal: {0!r}".format(line),
                )

    @slow_test
    def test_e307_folder_mode_refuses_a_source_marker_and_creates_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            stage_root = root / "stage"
            destination = root / "dest-must-not-exist"
            _seed_addon(source)
            _write_bytes(source / _MARKER, b'{"commit":"a stale claim"}\n')
            completed = _run_stage_only(
                self, PACK_PS1, stage_root, destination, ["-Source", str(source)]
            )
            combined = " ".join((completed.stdout + completed.stderr).split())
            self.assertNotEqual(completed.returncode, 0, combined)
            self.assertIn(_MARKER, combined)
            self.assertIn("reserved", combined)
            self.assertFalse(stage_root.exists())

    @slow_test
    def test_e307_a_staged_build_proves_its_commit_and_fails_a_later_one(self) -> None:
        """Real stage -> a PBO of every staged file (what -packonly packs) -> the tool."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, script, addon = _prepare_git_pack_repo(root)
            _git(repo, "add", "tools/pack-addon.ps1", "addon")
            _git(repo, "commit", "-m", "seed addon")
            sha = _git(repo, "rev-parse", "HEAD").strip()
            stage_root = root / "stage"
            stage_root.mkdir()
            completed = _run_stage_only(
                self, script, stage_root, root / "dest-must-not-exist", []
            )
            self.assertEqual(
                completed.returncode,
                0,
                "stdout={0!r}\nstderr={1!r}".format(completed.stdout, completed.stderr),
            )
            stage = Path(_manifest_from_stdout(completed.stdout)["stage"])
            entries = stage_entries(stage)
            self.assertIn(_MARKER, [name for name, _ in entries])
            pbo = root / "DayZ_MCP.pbo"
            pbo.write_bytes(build_pbo(entries))

            proven = _run_provenance(pbo, repo, sha)
            self.assertEqual(proven.returncode, 0, proven.stdout + proven.stderr)
            self.assertIn("marker OK", proven.stdout)
            self.assertEqual(
                proven.stdout.splitlines()[-1],
                "PROVENANCE OK entries={0}".format(len(entries)),
            )

            _write_bytes(
                addon / "scripts" / "5_Mission" / "MCPBridge.c",
                b"// another session's bridge\n",
            )
            _git(repo, "commit", "-am", "another session moves the bridge")
            stale = _run_provenance(pbo, repo, "HEAD")
            self.assertEqual(stale.returncode, 1, stale.stdout + stale.stderr)
            bridge_lines = [
                line for line in stale.stdout.splitlines()
                if line.startswith("scripts/5_Mission/MCPBridge.c ")
            ]
            self.assertEqual(len(bridge_lines), 1, stale.stdout)
            self.assertTrue(bridge_lines[0].endswith(" DIFF"), bridge_lines[0])
            self.assertIn("marker FAIL: commit {0}".format(sha), stale.stdout)

    def test_e307_marker_is_written_before_the_stage_only_exit_and_the_build(self) -> None:
        text = PACK_PS1.read_text(encoding="utf-8")
        write = text.index("[IO.File]::WriteAllText((Join-Path $stage $markerName)")
        self.assertLess(
            text.index("Assert-StageMatchesGitTree -StageDir $stage"),
            write,
            "the marker must be written after the stage is proven equal to the tree",
        )
        self.assertLess(write, text.index("if ($StageOnly) {"))
        self.assertLess(write, text.index("& $builder @args"))
        self.assertIn("$markerName = 'mcp_build.json'", text)
        self.assertIn("New-Object System.Text.UTF8Encoding $false", text[write:write + 300])

    def test_e307_enforce_never_reads_the_marker(self) -> None:
        """The marker is inert in game: the mod compiles only its two script folders,
        and no Enforce source names the file."""
        addon = addon_root()
        config = (addon / "config.cpp").read_text(encoding="utf-8")
        self.assertEqual(
            sorted(re.findall(r'files\[\]\s*=\s*\{\s*"([^"]*)"\s*\}', config)),
            ["DayZ_MCP/scripts/4_World", "DayZ_MCP/scripts/5_Mission"],
        )
        self.assertEqual(config.count("files[]"), 2, config)
        for source in sorted(addon.rglob("*")):
            if source.is_file() and source.suffix in (".c", ".cpp", ".layout"):
                self.assertNotIn(
                    "mcp_build", source.read_text(encoding="utf-8", errors="replace"), source
                )


if __name__ == "__main__":
    unittest.main()
