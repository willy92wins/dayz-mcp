"""Guard that the suite runs under the approved project interpreter."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


def approved_interpreter(package_file: Path) -> Path:
    return (
        package_file.resolve().parents[1]
        / ".venv-mcp"
        / "Scripts"
        / "python.exe"
    )


def verdict(
    current: Path,
    approved: Path,
    *,
    approved_exists: bool,
) -> tuple[str, str]:
    if not approved_exists:
        return (
            "absent",
            f"approved interpreter not found at {approved}; expected tools/.venv-mcp",
        )
    # Resolve both sides before comparing, as daemon.py:193-198 does: two spellings of the
    # same executable (junction, `..` segment) must not read as a mismatch (Codex backlog 2).
    if os.path.normcase(str(Path(current).resolve())) == os.path.normcase(str(Path(approved).resolve())):
        return ("ok", f"interpreter matches approved {approved}")
    return (
        "mismatch",
        f"interpreter mismatch: current={current} approved={approved}",
    )


class InterpreterGuardTests(unittest.TestCase):
    def test_suite_runs_on_approved_interpreter(self) -> None:
        import dayz_mcp

        approved = approved_interpreter(Path(dayz_mcp.__file__))
        approved_exists = approved.is_file()
        status, message = verdict(
            Path(sys.executable),
            approved,
            approved_exists=approved_exists,
        )
        if status == "mismatch":
            self.fail(message)
        if status == "absent":
            self.skipTest(message)
        self.assertEqual(status, "ok")


if __name__ == "__main__":
    unittest.main()
