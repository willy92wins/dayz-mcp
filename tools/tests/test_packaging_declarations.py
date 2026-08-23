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
_EXACT_PIN = r"^[A-Za-z0-9._-]+==[0-9][0-9A-Za-z.+!-]*$"


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


if __name__ == "__main__":
    unittest.main()
