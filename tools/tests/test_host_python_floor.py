"""Host Python floor: packaging, both installers, and the selector agree.

pyproject.toml is the declared floor. install-mcp.ps1 cannot pass a range
to `py`, so it reads `py -0p` and refuses anything older. install_mcp.py
refuses to import on an older interpreter. This file locks the three
together and exercises the PowerShell selector without running the rest
of the installer.
"""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import install_mcp as installer

PYPROJECT = TOOLS_DIR / "pyproject.toml"
INSTALL_PS1 = TOOLS_DIR / "install-mcp.ps1"


def _requires_python() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'(?m)^requires-python\s*=\s*"([^"]+)"\s*$', text)
    if match is None:
        raise AssertionError("requires-python vanished from pyproject.toml")
    return match.group(1)


def _floor_tuple() -> tuple[int, int]:
    match = re.fullmatch(r">=3\.(\d+)", _requires_python())
    if match is None:
        raise AssertionError(
            f"requires-python is not a simple >=3.N floor: {_requires_python()!r}"
        )
    return (3, int(match.group(1)))


def _selector_block() -> str:
    source = INSTALL_PS1.read_text(encoding="utf-8")
    start = source.index("$MinPythonVersion")
    end = source.index("function Write-DayZMcpConfig")
    block = source[start:end].strip()
    if "function Select-PythonFromLauncherList" not in block:
        raise AssertionError("selector functions missing from install-mcp.ps1")
    if "function Resolve-HostPython" not in block:
        raise AssertionError("Resolve-HostPython missing from install-mcp.ps1")
    return block


def _run_selector(list_text: str) -> str:
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        list_file = tmp_path / "list.txt"
        list_file.write_text(list_text, encoding="utf-8")
        script_file = tmp_path / "run.ps1"
        script_file.write_text(
            _selector_block()
            + "\n"
            + f"$list = Get-Content -Raw -LiteralPath '{list_file}'\n"
            + "$r = Select-PythonFromLauncherList -ListText $list\n"
            + "if ($null -eq $r -or $r -eq '') { 'NONE' } else { [string]$r }\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_file),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    if completed.returncode != 0:
        raise AssertionError(
            "selector script failed:\n"
            f"stdout={completed.stdout!r}\nstderr={completed.stderr!r}"
        )
    return completed.stdout.strip()


def _run_get_version(exe: str) -> str:
    with TemporaryDirectory() as tmp:
        script_file = Path(tmp) / "ver.ps1"
        script_file.write_text(
            _selector_block()
            + "\n"
            + f"$v = Get-PythonFileVersion -Exe '{exe}'\n"
            + "if ($null -eq $v) { 'NONE' } else { [string]$v }\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_file),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    if completed.returncode != 0:
        raise AssertionError(
            "Get-PythonFileVersion failed:\n"
            f"stdout={completed.stdout!r}\nstderr={completed.stderr!r}"
        )
    return completed.stdout.strip()


def _py_executable(spec: str) -> str | None:
    completed = subprocess.run(
        ["py", spec, "-c", "import sys; print(sys.executable)"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    text = completed.stdout.strip()
    return text or None


class HostPythonFloorLockstepTest(unittest.TestCase):
    def test_python_installer_constant_matches_pyproject(self) -> None:
        self.assertEqual(installer.MIN_PYTHON, _floor_tuple())

    def test_ps1_constant_matches_pyproject(self) -> None:
        source = INSTALL_PS1.read_text(encoding="utf-8")
        match = re.search(
            r'\$MinPythonVersion\s*=\s*\[version\]\s*"3\.(\d+)"', source
        )
        self.assertIsNotNone(match, "$MinPythonVersion missing from install-mcp.ps1")
        self.assertEqual((3, int(match.group(1))), _floor_tuple())

    def test_ps1_does_not_pin_an_exact_py_minor(self) -> None:
        source = INSTALL_PS1.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"& \$py\.Source -3\.\d+", source))
        self.assertIn("listed no matching interpreter", source)
        self.assertIn(
            "neither the py launcher nor python.exe was found", source
        )


class HostPythonSelectorTest(unittest.TestCase):
    def setUp(self) -> None:
        major, minor = _floor_tuple()
        self.floor = f"{major}.{minor}"
        self.below = f"{major}.{minor - 1}"
        self.above = f"{major}.{minor + 3}"
        self.below_path = rf"C:\Python{self.below.replace('.', '')}\python.exe"
        self.floor_path = rf"C:\Python{self.floor.replace('.', '')}\python.exe"
        self.above_path = rf"C:\Python{self.above.replace('.', '')}\python.exe"
        self.astral_path = rf"C:\uv\python\cpython-{self.floor}.16\python.exe"

    def test_selects_highest_at_or_above_floor(self) -> None:
        listing = (
            f" -V:{self.above} *        {self.above_path}\n"
            f" -V:{self.below}          {self.below_path}\n"
            f" -V:Astral/CPython{self.floor}.16 {self.astral_path}\n"
        )
        self.assertEqual(_run_selector(listing), self.above_path)

    def test_selects_astral_tagged_floor_when_it_is_the_newest_match(self) -> None:
        listing = (
            f" -V:{self.below}          {self.below_path}\n"
            f" -V:Astral/CPython{self.floor}.16 {self.astral_path}\n"
        )
        self.assertEqual(_run_selector(listing), self.astral_path)

    def test_rejects_listing_with_nothing_at_or_above_floor(self) -> None:
        listing = (
            f" -V:{self.below}          {self.below_path}\n"
            " -V:2.7           C:\\Python27\\python.exe\n"
        )
        self.assertEqual(_run_selector(listing), "NONE")

    def test_rejects_empty_listing(self) -> None:
        self.assertEqual(_run_selector(""), "NONE")


class HostPythonLiveInterpreterTest(unittest.TestCase):
    def test_python_installer_refuses_a_below_floor_interpreter(self) -> None:
        major, minor = _floor_tuple()
        below = f"{major}.{minor - 1}"
        exe = _py_executable(f"-{below}")
        if exe is None:
            self.skipTest(f"py -{below} is not installed on this host")
        completed = subprocess.run(
            [exe, str(TOOLS_DIR / "install_mcp.py")],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(TOOLS_DIR),
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn(
            f"Python {major}.{minor} or newer is required", completed.stderr
        )
        self.assertIn(below, completed.stderr)

    def test_real_below_floor_interpreter_is_older_than_the_constant(self) -> None:
        major, minor = _floor_tuple()
        below = f"{major}.{minor - 1}"
        exe = _py_executable(f"-{below}")
        if exe is None:
            self.skipTest(f"py -{below} is not installed on this host")
        reported = _run_get_version(exe)
        self.assertNotEqual(reported, "NONE")
        parts = [int(p) for p in reported.split(".")]
        self.assertLess(tuple(parts[:2]), (major, minor))

    def test_resolve_host_python_on_this_machine_meets_the_floor(self) -> None:
        with TemporaryDirectory() as tmp:
            script_file = Path(tmp) / "resolve.ps1"
            script_file.write_text(
                _selector_block()
                + "\n"
                + "$exe = Resolve-HostPython\n"
                + "[string]$exe\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script_file),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(
            completed.returncode,
            0,
            f"Resolve-HostPython failed:\n"
            f"stdout={completed.stdout!r}\nstderr={completed.stderr!r}",
        )
        exe = completed.stdout.strip()
        self.assertTrue(exe, "Resolve-HostPython printed no path")
        probe = subprocess.run(
            [exe, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)
        major, minor = (int(p) for p in probe.stdout.strip().split("."))
        self.assertGreaterEqual((major, minor), _floor_tuple())


if __name__ == "__main__":
    unittest.main()
