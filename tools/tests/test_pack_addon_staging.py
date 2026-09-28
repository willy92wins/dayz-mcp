"""pack-addon.ps1 stages git-tracked addon/ (or a filtered folder), not the live tree.

fb-63c9: -packonly makes AddonBuilder ignore include.lst, so packing the live
folder ships leftovers. Staging from git archive (default) or a walked copy
keeps the PBO to the intended tree. Tests always pass -StageOnly.
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
from pathlib import Path
from tempfile import TemporaryDirectory
from tests._tiers import slow_test


TOOLS_DIR = Path(__file__).resolve().parents[1]
PACK_PS1 = TOOLS_DIR / "pack-addon.ps1"
WORKSPACE_ROOT = TOOLS_DIR.parent
_POISONED_PSMODULEPATH = "/git/bash/poisoned/Modules"
_TIMEOUT_S = 90
_MOD_NAME = "DayZ_MCP"


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
            try:
                completed = _run_stage_only(
                    self, script, stage_root, destination, []
                )
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
                got_paths = [entry["path"] for entry in manifest["files"]]
                self.assertEqual(got_paths, expected_paths)
                self.assertEqual(_stage_relatives(stage), expected_paths)
                for entry in manifest["files"]:
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
            try:
                completed = _run_stage_only(
                    self,
                    PACK_PS1,
                    stage_root,
                    destination,
                    ["-Source", str(source)],
                )
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
                got_paths = [entry["path"] for entry in manifest["files"]]
                expected_included = sorted(
                    list(positives.keys()) + ["gui/layouts/hot/mcp_hot.layout"]
                )
                self.assertEqual(got_paths, expected_included)
                self.assertEqual(_stage_relatives(stage), expected_included)
                for entry in manifest["files"]:
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
            completed = _run_stage_only(
                self, PACK_PS1, stage_root, destination, ["-Ref", sha]
            )
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
            got_paths = [entry["path"] for entry in manifest["files"]]
            self.assertEqual(got_paths, expected_paths)
            stage = Path(manifest["stage"])
            self.assertEqual(_stage_relatives(stage), expected_paths)
            for entry in manifest["files"]:
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


if __name__ == "__main__":
    unittest.main()
