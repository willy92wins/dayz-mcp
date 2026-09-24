"""Gate the packaging metadata against what the tree actually ships.

Two files declare the runtime dependencies: requirements-mcp.txt, which
install-mcp.ps1 provisions the venv from, and pyproject.toml, which is what a
`pip install .` of the published repo reads. Duplicated lists drift in silence,
so the agreement is pinned here rather than left to whoever edits one of them.
"""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None  # type: ignore[assignment]

TOOLS_DIR = Path(__file__).resolve().parents[1]
PYPROJECT = TOOLS_DIR / "pyproject.toml"
REQUIREMENTS = TOOLS_DIR / "requirements-mcp.txt"
_EXACT_PIN = r"^[A-Za-z0-9._-]+==[0-9][0-9A-Za-z.+!-]*(\s*;\s*.+)?$"


def _pinned_requirements() -> list[str]:
    specs = []
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            specs.append(line)
    return specs


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

    def test_installer_stops_when_the_pip_upgrade_fails(self) -> None:
        script = (TOOLS_DIR / "install-mcp.ps1").read_text(encoding="utf-8")
        upgrade = script.index("& $VenvPython -m pip install --upgrade pip")
        install = script.index("& $VenvPython -m pip install -r $Requirements")
        between = script[upgrade:install]
        self.assertIn("if ($LASTEXITCODE -ne 0) {", between)
        self.assertIn('throw "pip install --upgrade pip failed"', between)

    def test_installer_probe_matches_wmi_host_imports(self) -> None:
        source = (TOOLS_DIR / "dayz_mcp" / "wmi_host.py").read_text(encoding="utf-8")
        self.assertIn("import pythoncom", source)
        self.assertIn("import win32com.client", source)


if __name__ == "__main__":
    unittest.main()
