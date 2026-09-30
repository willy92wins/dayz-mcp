"""Gate the packaging metadata against what the tree actually ships.

Two files declare the runtime dependencies: requirements-mcp.txt, which
install-mcp.ps1 provisions the venv from, and pyproject.toml, which is what a
`pip install .` of the published repo reads. Duplicated lists drift in silence,
so the agreement is pinned here rather than left to whoever edits one of them.

The bootstrap is pinned the same way (dc90). install-mcp.ps1 and both CI jobs
install one exact pip before the requirements, and pyproject.toml builds the
package with one exact setuptools. `--upgrade pip` and `setuptools>=64` took
whatever was newest, so a release of either could break a fresh install or CI
with no change in this repo.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None  # type: ignore[assignment]

TOOLS_DIR = Path(__file__).resolve().parents[1]
PYPROJECT = TOOLS_DIR / "pyproject.toml"
REQUIREMENTS = TOOLS_DIR / "requirements-mcp.txt"
INSTALLER = TOOLS_DIR / "install-mcp.ps1"
WORKFLOW = TOOLS_DIR.parent / ".github" / "workflows" / "tests.yml"
_EXACT_PIN = r"^[A-Za-z0-9._-]+==[0-9][0-9A-Za-z.+!-]*(\s*;\s*.+)?$"
_EXACT_PIP = r"^pip==[0-9][0-9A-Za-z.+!-]*$"
_PIP_REQUIREMENT = re.compile(r'(?m)^\$PipRequirement\s*=\s*"([^"\n]*)"[ \t]*$')
_PIP_INSTALL = re.compile(r"-m\s+pip\s+install\b(.*)")
_VENV = re.compile(r"-m\s+venv\b")


def _pinned_requirements() -> list[str]:
    specs = []
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            specs.append(line)
    return specs


def _code_lines(text: str) -> list[str]:
    """The lines that are not comments; `#` opens one in PowerShell and in YAML alike."""
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def _pip_install_args(line: str) -> list[str] | None:
    """The arguments of a `python -m pip install` line; None for any other line.

    The installer and the workflow both run pip as `python -m pip`. A trailing
    comment is dropped, and so are the quotes around an argument.
    """
    match = _PIP_INSTALL.search(line)
    if match is None:
        return None
    arguments = re.split(r"\s#", match.group(1), maxsplit=1)[0]
    return [token.strip("'\"") for token in arguments.split()]


def _is_upgrade_flag(token: str) -> bool:
    """--upgrade, -U, or -U inside a cluster of short flags such as -qU."""
    return token == "--upgrade" or re.fullmatch(r"-[A-Za-z]*U[A-Za-z]*", token) is not None


def _names(requirement: str, project: str) -> bool:
    """True when a requirement is on `project`, whatever its version specifier."""
    return re.match(rf"(?i){re.escape(project)}(?![A-Za-z0-9_.-])", requirement) is not None


@unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
class PackagingDeclarationsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.document = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

    def test_pyproject_declares_the_pinned_runtime_dependencies(self) -> None:
        declared = self.document["project"]["dependencies"]
        self.assertEqual(sorted(declared), sorted(_pinned_requirements()))

    def test_dependencies_are_pinned_to_exact_versions(self) -> None:
        for spec in self.document["project"]["dependencies"]:
            self.assertRegex(spec, _EXACT_PIN, f"not an exact pin: {spec!r}")

    def test_build_requirements_are_pinned_to_exact_versions(self) -> None:
        requires = self.document["build-system"]["requires"]
        self.assertTrue(any(_names(spec, "setuptools") for spec in requires), requires)
        for spec in requires:
            self.assertRegex(spec, _EXACT_PIN, f"not an exact pin: {spec!r}")

    def test_declared_packaging_targets_exist(self) -> None:
        setuptools = self.document["tool"]["setuptools"]
        for package in setuptools["packages"]:
            init = TOOLS_DIR / package / "__init__.py"
            self.assertTrue(init.is_file(), f"missing package: {package}")
        for module in setuptools["py-modules"]:
            source = TOOLS_DIR / f"{module}.py"
            self.assertTrue(source.is_file(), f"missing module: {module}")

    def test_requires_python_is_the_language_floor(self) -> None:
        self.assertEqual(
            self.document["project"]["requires-python"], ">=3.11")


@unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
class BootstrapPinsTest(unittest.TestCase):
    """dc90: one exact pip in the installer and in CI, one exact setuptools to build."""

    def setUp(self) -> None:
        self.installer = INSTALLER.read_text(encoding="utf-8")
        self.workflow = WORKFLOW.read_text(encoding="utf-8")
        document = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        self.build_requires = document["build-system"]["requires"]

    def _pip(self) -> str:
        pins = _PIP_REQUIREMENT.findall(self.installer)
        self.assertEqual(len(pins), 1, f"install-mcp.ps1 assigns $PipRequirement {len(pins)} times")
        self.assertRegex(pins[0], _EXACT_PIP, f"not an exact pip pin: {pins[0]!r}")
        return pins[0]

    def _setuptools(self) -> str:
        pins = [spec for spec in self.build_requires if _names(spec, "setuptools")]
        self.assertEqual(len(pins), 1, self.build_requires)
        return pins[0]

    def _assert_no_drift(self, source: str, lines: list[str], variables: dict[str, str]) -> None:
        """No pip install upgrades, and pip or setuptools appear only at their pins."""
        pip, setuptools = self._pip(), self._setuptools()
        for line in lines:
            arguments = _pip_install_args(line)
            if arguments is None:
                continue
            resolved = [variables.get(token, token) for token in arguments]
            for token in resolved:
                self.assertFalse(_is_upgrade_flag(token), f"{source} upgrades: {line.strip()}")
                if _names(token, "pip"):
                    self.assertEqual(resolved, [pip], f"{source}: {line.strip()}")
                if _names(token, "setuptools"):
                    self.assertEqual(token, setuptools, f"{source}: {line.strip()}")

    def test_installer_installs_the_exact_pip_before_anything_else(self) -> None:
        pip = self._pip()
        lines = _code_lines(self.installer)
        installs = [args for args in map(_pip_install_args, lines) if args is not None]
        self.assertTrue(installs, "install-mcp.ps1 runs no pip install")
        self.assertEqual(installs[0], ["$PipRequirement"], installs)
        # Assigned before the install reads it, or pip gets no requirement at all.
        self.assertLess(
            _PIP_REQUIREMENT.search(self.installer).start(),
            self.installer.index("& $VenvPython -m pip install $PipRequirement"),
        )
        self._assert_no_drift("install-mcp.ps1", lines, {"$PipRequirement": pip})

    def test_ci_installs_the_installer_pip_right_after_each_venv(self) -> None:
        pip = self._pip()
        lines = _code_lines(self.workflow)
        venvs = 0
        waiting = False
        for line in lines:
            if _VENV.search(line):
                self.assertFalse(waiting, f"the venv before this one got no pip: {line.strip()}")
                venvs += 1
                waiting = True
                continue
            arguments = _pip_install_args(line)
            if waiting and arguments is not None:
                self.assertEqual(arguments, [pip], line.strip())
                waiting = False
        self.assertGreater(venvs, 0, "tests.yml no longer creates the venv")
        self.assertFalse(waiting, "the last venv in tests.yml got no pip install")
        self._assert_no_drift("tests.yml", lines, {})


class Pywin32ProvisioningTest(unittest.TestCase):
    """296b: wmi_host needs pywin32; a clean install must get it or stop."""

    def test_requirements_declare_pywin32_for_windows_only(self) -> None:
        specs = [s for s in _pinned_requirements() if s.lower().startswith("pywin32")]
        self.assertEqual(len(specs), 1, specs)
        self.assertRegex(specs[0], r'^pywin32==[0-9.]+;\s*sys_platform\s*==\s*"win32"$')

    def test_installer_refuses_an_environment_without_pywin32(self) -> None:
        script = (TOOLS_DIR / "install-mcp.ps1").read_text(encoding="utf-8")
        install = script.index("& $VenvPython -m pip install -r $Requirements")
        probe = script.index('& $VenvPython -c "import pythoncom, win32com.client"')
        self.assertLess(install, probe)
        after_probe = script[probe:probe + 400]
        self.assertIn("if ($LASTEXITCODE -ne 0) {", after_probe)
        self.assertIn('throw "pywin32 is missing', after_probe)
        after_install = script[install:probe]
        self.assertIn("throw \"pip install -r $Requirements failed\"", after_install)

    def test_installer_stops_when_the_pip_install_fails(self) -> None:
        script = (TOOLS_DIR / "install-mcp.ps1").read_text(encoding="utf-8")
        pin = script.index("& $VenvPython -m pip install $PipRequirement")
        install = script.index("& $VenvPython -m pip install -r $Requirements")
        between = script[pin:install]
        self.assertIn("if ($LASTEXITCODE -ne 0) {", between)
        self.assertIn('throw "pip install $PipRequirement failed"', between)

    def test_installer_probe_matches_wmi_host_imports(self) -> None:
        source = (TOOLS_DIR / "dayz_mcp" / "wmi_host.py").read_text(encoding="utf-8")
        self.assertIn("import pythoncom", source)
        self.assertIn("import win32com.client", source)


if __name__ == "__main__":
    unittest.main()
