"""Every constant ValueError token on the dayz_test path must reach the caller named.

FastMCP serialises `str(exc)` alone, so an unmapped token arrives as
`dayz_test_failed:ValueError` -- a string that says nothing, matches nothing, and
sends whoever hit it to read source. That happened on 2026-08-21: the cause was
RUN_PREPRUNE_BACKUP_SLOTS_EXHAUSTED, which `python -m dayz_mcp.doctor` reports in one
line, and the launch failure gave no hint the doctor was worth running.

This scans the modules on that path for `raise ValueError("<constant>")` and requires a
mapping for each. Adding a new token without a code fails here rather than in a
teammate's afternoon.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from dayz_mcp.server import _DAYZ_TEST_VALUE_ERROR_CODES


PACKAGE = Path(__file__).resolve().parents[1] / "dayz_mcp"
# The modules _execute_request actually traverses for a run or a stop.
PATH_MODULES = (
    "native_launcher_transaction.py",
    "dayz_test_tool.py",
    "process_lifecycle.py",
)


def _constant_value_error_tokens(source: str) -> set[str]:
    """Tokens of `raise ValueError("literal")`. Non-constant raises are out of scope."""
    tokens: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        call = node.exc
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
            continue
        if call.func.id != "ValueError" or len(call.args) != 1:
            continue
        argument = call.args[0]
        if isinstance(argument, ast.Constant) and type(argument.value) is str:
            tokens.add(argument.value)
    return tokens


class DayzTestValueErrorCodesTest(unittest.TestCase):
    def test_every_token_on_the_path_has_a_caller_facing_code(self) -> None:
        unmapped: list[str] = []
        for name in PATH_MODULES:
            path = PACKAGE / name
            if not path.is_file():
                continue
            for token in sorted(_constant_value_error_tokens(path.read_text(encoding="utf-8"))):
                if token not in _DAYZ_TEST_VALUE_ERROR_CODES:
                    unmapped.append(f"{name}: {token}")
        self.assertEqual(
            unmapped,
            [],
            "these reach the caller as a bare dayz_test_failed:ValueError; add each to "
            "_DAYZ_TEST_VALUE_ERROR_CODES in server.py: " + ", ".join(unmapped),
        )

    def test_the_codes_are_constant_strings_that_carry_no_paths(self) -> None:
        """A code crosses the MCP wire, so it must not become a host path."""
        for token, code in _DAYZ_TEST_VALUE_ERROR_CODES.items():
            self.assertIsInstance(code, str)
            self.assertTrue(code)
            self.assertNotIn(":", code, f"{token} -> {code}")
            self.assertNotIn("\\", code, f"{token} -> {code}")
            self.assertNotIn("/", code, f"{token} -> {code}")
            self.assertEqual(code, code.strip())


if __name__ == "__main__":
    unittest.main()
