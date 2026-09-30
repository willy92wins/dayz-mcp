"""tools/dev/setup_worktree.sh: a worktree's venv must run THAT worktree's code.

fb-20260818-220336-2eb5: setuptools' editable finder sits at the end of
sys.meta_path, behind PathFinder, and maps dayz_mcp to an absolute tree. From inside
tools/, `python -c "import dayz_mcp"` finds the local folder first and stays green
while the map points at another checkout; a test subprocess started elsewhere falls
through to the map and runs that other checkout. These tests build a throwaway venv
whose editable map points inside or outside a synthetic worktree, and run the
script's --check from inside that worktree's tools/ with PYTHONPATH naming it: the
two conditions that kept the old gate green.

Review R1 (Codex) F4: the check derived the accepted tree from the interpreter's
sys.prefix and resolved it, so a worktree whose .venv-mcp was a junction to another
checkout's venv sealed that other checkout. The check now takes the requested
worktree, refuses a junction or symlink on the way to its venv, and wants every own
module inside that worktree's tools/.

Review R2 (Codex) F5: only the worktree, tools/ and .venv-mcp were checked, so the
same tree named through a junctioned ancestor passed as sealed. The check now reads
every component from the drive root down with lstat, before anything resolves it,
and refuses a name surrogate (a junction or a symlink, tag bit 0x20000000). Other
reparse points pass: the live repository sits under OneDrive, whose cloud-file
placeholders carry non-surrogate tags. A real cloud tag cannot be made in a test, so
the walk also runs in-process against a fake lstat, as the launcher-registry tests do.

Not run here: the create path past its refusals. It adds a worktree to the
repository that holds the script and downloads pip, the requirements and
setuptools. Its refusals run on a copy of the script inside a throwaway repository,
so a regression can never add a worktree to this one.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from tests._tiers import slow_test

TOOLS_DIR = Path(__file__).resolve().parents[1]
SCRIPT = TOOLS_DIR / "dev" / "setup_worktree.sh"
INSTALLER = TOOLS_DIR / "install-mcp.ps1"
_TIMEOUT_S = 120
_PIP_REQUIREMENT = re.compile(r'(?m)^\$PipRequirement\s*=\s*"([^"\n]*)"[ \t]*$')

# A minimal setuptools editable finder: appended to sys.meta_path by a .pth file,
# it maps top-level names to absolute paths, which is where the 2eb5 leak lives.
_FINDER = '''\
import importlib.util
import sys
from pathlib import Path

MAPPING = {mapping!r}


class _Finder:
    @classmethod
    def find_spec(cls, fullname, path=None, target=None):
        if fullname not in MAPPING:
            return None
        base = Path(MAPPING[fullname])
        if (base / "__init__.py").is_file():
            return importlib.util.spec_from_file_location(
                fullname, base / "__init__.py", submodule_search_locations=[str(base)]
            )
        if base.with_suffix(".py").is_file():
            return importlib.util.spec_from_file_location(fullname, base.with_suffix(".py"))
        return None


def install():
    if _Finder not in sys.meta_path:
        sys.meta_path.append(_Finder)
'''


def _git_bash() -> str | None:
    """Git's bash. A bare "bash" from Python can be WSL's System32\\bash.exe."""
    bases = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramW6432"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
    ]
    for base in bases:
        if base:
            candidate = Path(base) / "Git" / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
    git = shutil.which("git")
    if git:
        candidate = Path(git).resolve().parents[1] / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def _run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, check=False, timeout=_TIMEOUT_S, **kwargs)


@unittest.skipUnless(sys.platform == "win32", "the synthetic venv uses the Windows layout")
class WorktreeCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bash = _git_bash()
        if self.bash is None:
            self.skipTest("Git Bash not found under ProgramFiles, LOCALAPPDATA or beside git")
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.wt = self._tree("wt")
        self.other = self._tree("other")

    def _tree(self, name: str) -> Path:
        tools = self.root / name / "tools"
        (tools / "dayz_mcp").mkdir(parents=True)
        (tools / "dayz_mcp" / "__init__.py").write_text(f"TREE = {name!r}\n", encoding="utf-8")
        (tools / "mcp_capture.py").write_text(f"TREE = {name!r}\n", encoding="utf-8")
        return self.root / name

    def _junction(self, link: Path, target: Path) -> None:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        # Cleanups run last-in first-out: the junction goes before the temporary tree.
        self.addCleanup(os.rmdir, link)

    def _venv(self, mapping: dict[str, Path], tree: Path | None = None) -> Path:
        venv = (tree or self.wt) / "tools" / ".venv-mcp"
        made = _run([sys.executable, "-m", "venv", "--without-pip", str(venv)])
        self.assertEqual(made.returncode, 0, made.stderr)
        python = venv / "Scripts" / "python.exe"
        purelib = _run(
            [str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
            text=True,
        )
        self.assertEqual(purelib.returncode, 0, purelib.stderr)
        site = Path(purelib.stdout.strip())
        finder = _FINDER.format(mapping={name: str(path) for name, path in mapping.items()})
        (site / "__editable___fake_1_0_0_finder.py").write_text(finder, encoding="utf-8")
        (site / "__editable__.fake-1.0.0.pth").write_text(
            "import __editable___fake_1_0_0_finder; __editable___fake_1_0_0_finder.install()\n",
            encoding="utf-8",
        )
        return python

    def check(self, worktree: Path | None = None) -> tuple[int, str]:
        worktree = worktree or self.wt
        env = os.environ.copy()
        env["PYTHONPATH"] = str(worktree / "tools")
        done = _run(
            [self.bash, SCRIPT.as_posix(), "--check", worktree.as_posix()],
            cwd=str(worktree / "tools"),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return done.returncode, done.stdout + done.stderr

    @slow_test
    def test_a_map_into_the_worktree_is_sealed(self) -> None:
        tools = self.wt / "tools"
        self._venv({"dayz_mcp": tools / "dayz_mcp", "mcp_capture": tools / "mcp_capture"})

        code, output = self.check()

        self.assertEqual(code, 0, output)
        self.assertIn("SEALED", output)
        self.assertNotIn("LEAK", output)

    @slow_test
    def test_a_map_into_another_checkout_leaks_even_from_inside_tools(self) -> None:
        other = self.other / "tools"
        python = self._venv({"dayz_mcp": other / "dayz_mcp", "mcp_capture": other / "mcp_capture"})

        code, output = self.check()

        self.assertEqual(code, 1, output)
        self.assertIn("LEAK: the editable finder maps dayz_mcp to {0}".format(other / "dayz_mcp"), output)
        self.assertIn("LEAK: dayz_mcp resolves to {0}".format(other / "dayz_mcp" / "__init__.py"), output)
        self.assertNotIn("SEALED", output)
        # The gate this replaces stays green in the same venv: the local folder wins.
        old_gate = _run(
            [str(python), "-c", "import dayz_mcp; print(dayz_mcp.TREE)"],
            cwd=str(self.wt / "tools"),
            text=True,
        )
        self.assertEqual(old_gate.returncode, 0, old_gate.stderr)
        self.assertEqual(old_gate.stdout.strip(), "wt")

    @slow_test
    def test_one_module_outside_the_worktree_is_enough_to_leak(self) -> None:
        self._venv(
            {
                "dayz_mcp": self.wt / "tools" / "dayz_mcp",
                "mcp_capture": self.other / "tools" / "mcp_capture",
            }
        )

        code, output = self.check()

        self.assertEqual(code, 1, output)
        self.assertIn("LEAK: mcp_capture resolves to", output)
        self.assertNotIn("LEAK: dayz_mcp", output)

    @slow_test
    def test_a_venv_that_only_pythonpath_could_satisfy_is_not_sealed(self) -> None:
        self._venv({})

        code, output = self.check()

        self.assertEqual(code, 1, output)
        self.assertIn("LEAK: dayz_mcp resolves to nothing", output)

    @slow_test
    def test_r1_f4_a_venv_junctioned_from_another_checkout_leaks(self) -> None:
        # The reviewer's repro: wt/tools/.venv-mcp is a junction to other/'s venv,
        # whose map keeps dayz_mcp inside other/tools, so other/ looks sealed.
        other = self.other / "tools"
        self._venv({"dayz_mcp": other / "dayz_mcp", "mcp_capture": other / "mcp_capture"}, tree=self.other)
        self._junction(self.wt / "tools" / ".venv-mcp", other / ".venv-mcp")

        code, output = self.check()

        self.assertEqual(code, 1, output)
        self.assertIn(".venv-mcp is a junction or a symlink", output)
        self.assertIn("LEAK: dayz_mcp resolves to {0}".format(other / "dayz_mcp" / "__init__.py"), output)
        self.assertNotIn("SEALED", output)

    @slow_test
    def test_r1_f4_a_tools_folder_junctioned_to_another_checkout_leaks(self) -> None:
        other = self.other / "tools"
        self._venv({"dayz_mcp": other / "dayz_mcp", "mcp_capture": other / "mcp_capture"}, tree=self.other)
        junctioned = self.root / "junctioned"
        junctioned.mkdir()
        self._junction(junctioned / "tools", other)

        code, output = self.check(junctioned)

        self.assertEqual(code, 1, output)
        # The path comes back through cygpath's mount table: compare without case.
        self.assertIn("{0} is a junction or a symlink".format(junctioned / "tools").lower(), output.lower())
        self.assertNotIn("SEALED", output)

    @slow_test
    def test_r2_f5_a_junction_on_the_worktrees_parent_is_refused(self) -> None:
        # The reviewer's R2 repro: a tree sealed under its own name must not pass
        # when named through a junctioned parent.
        worktree = self._tree("physical/wt")
        tools = worktree / "tools"
        self._venv({"dayz_mcp": tools / "dayz_mcp", "mcp_capture": tools / "mcp_capture"}, tree=worktree)
        code, output = self.check(worktree)
        self.assertEqual(code, 0, output)
        self.assertIn("SEALED", output)
        alias = self.root / "alias"
        self._junction(alias, worktree.parent)

        code, output = self.check(alias / "wt")

        self.assertEqual(code, 1, output)
        # The path comes back through cygpath's mount table: compare without case.
        self.assertIn("{0} is a junction or a symlink (reparse tag 0xa0000003)".format(alias).lower(), output.lower())
        self.assertNotIn("SEALED", output)

    @slow_test
    def test_a_worktree_without_a_venv_is_refused(self) -> None:
        code, output = self.check()

        self.assertNotEqual(code, 0, output)
        self.assertIn("no venv at", output)


class RedirectingComponentsTest(unittest.TestCase):
    """--check's component walk, run in-process against a fake lstat.

    Only a name surrogate (tag bit 0x20000000: a junction or a symlink) can make a
    path name another tree. The live repository sits under OneDrive, whose
    cloud-file placeholders carry other tags and must pass; a real cloud tag cannot
    be made in a test, so these stand in for it (as in test_secure_launcher).
    """

    FIXTURE = "C:\\fixture\\parent\\wt\\tools\\.venv-mcp"
    ANCESTOR = "C:\\fixture\\parent"

    @classmethod
    def setUpClass(cls) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        opening = "<<'PY') || status=$?\n"
        start = text.index(opening) + len(opening)
        end = text.index("\nPY\n", start)
        namespace: dict[str, object] = {"__name__": "setup_worktree_check"}
        exec(compile(text[start:end], str(SCRIPT), "exec"), namespace)
        cls.walk = staticmethod(namespace["redirecting_components"])

    def walk_with(self, tags: dict[str, int], *, link: str = "", missing: str = "") -> list[str]:
        def lstat(path: object) -> SimpleNamespace:
            name = str(path)
            if name == missing:
                raise PermissionError(13, "Access is denied", name)
            mode = stat.S_IFLNK if name == link else stat.S_IFDIR
            return SimpleNamespace(st_mode=mode, st_reparse_tag=tags.get(name, 0))

        return [f"{component} {why}" for component, why in self.walk(self.FIXTURE, lstat=lstat)]

    @unittest.skipUnless(sys.platform == "win32", "the fixture is a Windows path")
    def test_r2_f5_non_surrogate_tags_on_an_ancestor_pass(self) -> None:
        for tag in (0x9000001A, 0x9000601A, 0x80000021, 0x8000001E, 0x8000001A):
            with self.subTest(tag=hex(tag)):
                self.assertEqual(self.walk_with({self.ANCESTOR: tag}), [])

    @unittest.skipUnless(sys.platform == "win32", "the fixture is a Windows path")
    def test_r2_f5_a_name_surrogate_on_an_ancestor_is_refused(self) -> None:
        for tag in (0xA0000003, 0xA000000C, 0xA000001D):
            with self.subTest(tag=hex(tag)):
                self.assertEqual(
                    self.walk_with({self.ANCESTOR: tag}),
                    [f"{self.ANCESTOR} is a junction or a symlink (reparse tag 0x{tag:08X})"],
                )

    @unittest.skipUnless(sys.platform == "win32", "the fixture is a Windows path")
    def test_r2_f5_a_symlink_without_a_tag_is_refused(self) -> None:
        self.assertEqual(
            self.walk_with({}, link=self.ANCESTOR),
            [f"{self.ANCESTOR} is a junction or a symlink (reparse tag 0x00000000)"],
        )

    @unittest.skipUnless(sys.platform == "win32", "the fixture is a Windows path")
    def test_r2_f5_every_component_is_read_and_one_that_cannot_be_fails_closed(self) -> None:
        tags = {self.FIXTURE: 0xA0000003, "C:\\fixture": 0xA000000C}
        self.assertEqual(
            self.walk_with(tags),
            [
                "C:\\fixture is a junction or a symlink (reparse tag 0xA000000C)",
                f"{self.FIXTURE} is a junction or a symlink (reparse tag 0xA0000003)",
            ],
        )
        found = self.walk_with({}, missing=self.ANCESTOR)
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith(f"{self.ANCESTOR} cannot be inspected"), found)


@unittest.skipUnless(sys.platform == "win32", "Git Bash is the shell this script is written for")
class SetupScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bash = _git_bash()
        if self.bash is None:
            self.skipTest("Git Bash not found under ProgramFiles, LOCALAPPDATA or beside git")

    def pip_requirement(self, installer: bytes) -> subprocess.CompletedProcess:
        # Sourcing defines the functions and runs nothing.
        return _run(
            [self.bash, "-c", 'source "$1"; pip_requirement', "setup_worktree", SCRIPT.as_posix()],
            input=installer,
        )

    def throwaway_copy(self, installer: bytes | None = None) -> tuple[Path, Path]:
        """A copy of the script inside a fresh repository: git work lands there, never here.

        With ``installer``, tools/install-mcp.ps1 holds it and everything is committed.
        """
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        repo = root / "repo"
        (repo / "tools" / "dev").mkdir(parents=True)
        copy = repo / "tools" / "dev" / "setup_worktree.sh"
        shutil.copyfile(SCRIPT, copy)
        self.assertEqual(_run(["git", "init", "-q", str(repo)]).returncode, 0)
        if installer is not None:
            (repo / "tools" / "install-mcp.ps1").write_bytes(installer)
            for args in (
                ("config", "user.name", "setup-worktree-test"),
                ("config", "user.email", "setup-worktree-test@example.invalid"),
                ("add", "-A"),
                ("commit", "-q", "-m", "seed"),
            ):
                done = _run(["git", "-C", str(repo), *args])
                self.assertEqual(done.returncode, 0, done.stderr)
        return root, copy

    def branch_exists(self, copy: Path, branch: str) -> bool:
        listed = _run(["git", "-C", str(copy.parents[2]), "branch", "--list", branch], text=True)
        return listed.stdout.strip() != ""

    @slow_test
    def test_a_base_without_an_exact_pip_pin_is_refused_before_the_worktree_exists(self) -> None:
        root, copy = self.throwaway_copy(installer=b'$PipRequirement = "pip>=26"\n')

        done = _run(
            [self.bash, copy.as_posix(), (root / "wt").as_posix(), "new-branch", "HEAD"], text=True
        )

        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("$PipRequirement", done.stderr)
        self.assertFalse((root / "wt").exists())
        self.assertFalse(self.branch_exists(copy, "new-branch"))

    @slow_test
    def test_a_relative_worktree_path_is_relative_to_the_caller(self) -> None:
        root, copy = self.throwaway_copy(installer=INSTALLER.read_bytes())
        env = os.environ.copy()
        # Stop right after `git worktree add`: nothing is installed, nothing downloaded.
        env["PYTHON"] = "no-such-python-for-this-test"

        done = _run(
            [self.bash, copy.as_posix(), "relative-wt", "new-branch", "HEAD"],
            cwd=str(root),
            env=env,
            text=True,
        )

        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("READY", done.stdout)
        self.assertTrue((root / "relative-wt" / "tools" / "install-mcp.ps1").is_file(), done.stderr)
        self.assertFalse((copy.parents[2] / "relative-wt").exists())
        self.assertTrue(self.branch_exists(copy, "new-branch"))

    @slow_test
    def test_pip_requirement_is_the_installer_pin(self) -> None:
        pins = _PIP_REQUIREMENT.findall(INSTALLER.read_text(encoding="utf-8"))
        self.assertEqual(len(pins), 1, pins)

        done = self.pip_requirement(INSTALLER.read_bytes())

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.decode("utf-8").strip(), pins[0])

    @slow_test
    def test_pip_requirement_refuses_anything_but_one_exact_pin(self) -> None:
        cases = (
            b"Write-Host 'no pin here'\n",
            b'$PipRequirement = "pip>=26"\n',
            b'$PipRequirement = "pip=="\n',
            b'$PipRequirement = "pip==1.0"\n$PipRequirement = "pip==2.0"\n',
        )
        for installer in cases:
            with self.subTest(installer=installer):
                done = self.pip_requirement(installer)

                self.assertNotEqual(done.returncode, 0)
                self.assertEqual(done.stdout, b"")
                self.assertIn(b"$PipRequirement", done.stderr)

    @slow_test
    def test_an_existing_directory_is_refused_before_git_is_touched(self) -> None:
        root, copy = self.throwaway_copy()
        existing = root / "existing"
        existing.mkdir()

        done = _run([self.bash, copy.as_posix(), existing.as_posix(), "new-branch"], text=True)

        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("EXISTS", done.stderr)
        self.assertFalse(self.branch_exists(copy, "new-branch"))
        worktrees = _run(["git", "-C", str(copy.parents[2]), "worktree", "list", "--porcelain"], text=True)
        self.assertEqual(worktrees.stdout.count("worktree "), 1, worktrees.stdout)

    @slow_test
    def test_wrong_arguments_print_the_usage_and_exit_2(self) -> None:
        _root, copy = self.throwaway_copy()
        for args in ((), ("only-one",), ("--check",), ("--check", "a", "b"), ("-x", "y"), ("a", "b", "c", "d")):
            with self.subTest(args=args):
                done = _run([self.bash, copy.as_posix(), *args], text=True)

                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertIn("usage: setup_worktree.sh", done.stderr)


class SetupScriptContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_pip_comes_pinned_from_the_installer_and_is_never_upgraded(self) -> None:
        installs = [line for line in self.text.splitlines() if re.search(r"-m\s+pip\s+install\b", line)]
        self.assertEqual(len(installs), 3, installs)
        for line in installs:
            self.assertNotRegex(line, r"--upgrade|\s-[A-Za-z]*U\b")
        self.assertIn('"$python" -m pip install -q "$pip_req"', installs[0])
        pin = self.text.index('pip_req="$(git -C "$repo" show "$base:tools/install-mcp.ps1" | pip_requirement)"')
        self.assertLess(pin, self.text.index('git -C "$repo" worktree add'))

    def test_the_create_path_ends_with_the_isolated_check(self) -> None:
        self.assertIn('neutral="$(mktemp -d)"', self.text)
        # The check gets the worktree as named: `pwd -W` / `pwd -P` would resolve a junction.
        self.assertIn('(cd "$neutral" && "$python" -I -B - "$tools_arg" <<\'PY\')', self.text)
        code = [line for line in self.text.splitlines() if not line.lstrip().startswith("#")]
        self.assertEqual([line for line in code if "pwd -W" in line or "pwd -P" in line], [])
        create = self.text[self.text.index("create_worktree() {"):self.text.index("main() {")]
        self.assertLess(create.index("pip install -q -e ."), create.index('check_worktree "$wt"'))
        self.assertLess(create.index('check_worktree "$wt"'), create.index('echo "READY'))


if __name__ == "__main__":
    unittest.main()
