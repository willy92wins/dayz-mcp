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

from dayz_mcp import dayz_test_tool, server
from dayz_mcp.server import _DAYZ_TEST_VALUE_ERROR_CODES, _is_safe_error_token


PACKAGE = Path(__file__).resolve().parents[1] / "dayz_mcp"
# Everything a run or a stop traverses. The first three were the original list;
# the rest were added 2026-08-21 after a session hit a mute ValueError that came
# from none of them -- control_client alone raises seven, on the internal-lease
# path dayz_test_run always takes.
PATH_MODULES = (
    "native_launcher_transaction.py",
    "dayz_test_tool.py",
    "process_lifecycle.py",
    "control_client.py",
    "native_bundle.py",
    "dayz_test_request.py",
    "request_path_authority.py",
    "secure_launcher.py",
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
    def test_every_token_on_the_path_reaches_the_caller_named(self) -> None:
        """Named via the map, or via the safe-token fallback. Never mute.

        This asserts the OUTCOME, not membership of a list. Enumerating modules
        was the original shape and it left seven tokens in control_client.py
        uncovered, which is what a session hit on 2026-08-21.
        """
        mute: list[str] = []
        for name in PATH_MODULES:
            path = PACKAGE / name
            if not path.is_file():
                continue
            for token in sorted(_constant_value_error_tokens(path.read_text(encoding="utf-8"))):
                named = token in _DAYZ_TEST_VALUE_ERROR_CODES or _is_safe_error_token(
                    token
                )
                if not named:
                    mute.append(f"{name}: {token!r}")
        self.assertEqual(
            mute,
            [],
            "these reach the caller as a bare dayz_test_failed:ValueError. Either "
            "give the raise an identifier-shaped token, or add it to "
            "_DAYZ_TEST_VALUE_ERROR_CODES in server.py: " + ", ".join(mute),
        )

    def test_a_message_that_could_hold_a_host_path_stays_mute(self) -> None:
        """The fallback must not become a leak: only bare identifiers pass."""
        for leaky in (
            r"C:\Users\someone\DayZ Projects\LFPowerGrid_dev",
            "invalid literal for int() with base 10: 'x'",
            "Invalid isoformat string: '2026-13-45'",
            "failed to open P:/mod/config.cpp",
            "run_exists: 5b66bb2e",
            "server.py",
            "no",
            "",
        ):
            self.assertFalse(_is_safe_error_token(leaky), leaky)

    def test_identifier_shaped_tokens_pass(self) -> None:
        for token in (
            "invalid_session_lease",
            "invalid_native_launcher_bundle",
            "RUN_PREPRUNE_BACKUP_SLOTS_EXHAUSTED",
            "run_exists",
        ):
            self.assertTrue(_is_safe_error_token(token), token)

    def test_the_codes_are_constant_strings_that_carry_no_paths(self) -> None:
        """A code crosses the MCP wire, so it must not become a host path."""
        for token, code in _DAYZ_TEST_VALUE_ERROR_CODES.items():
            self.assertIsInstance(code, str)
            self.assertTrue(code)
            self.assertNotIn(":", code, f"{token} -> {code}")
            self.assertNotIn("\\", code, f"{token} -> {code}")
            self.assertNotIn("/", code, f"{token} -> {code}")
            self.assertEqual(code, code.strip())


class TypedValueErrorWiringTest(unittest.TestCase):
    """The predicate is only useful if the context manager actually consults it.

    Asserting `_is_safe_error_token` alone would stay green with the fallback
    unwired, which is the whole defect: the token existed, the caller never
    saw it.
    """

    def test_an_unmapped_identifier_token_reaches_the_caller_named(self) -> None:
        with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
            with server._typed_dayz_test_value_errors():
                raise ValueError("invalid_session_lease")
        self.assertEqual(caught.exception.code, "invalid_session_lease")

    def test_a_mapped_token_keeps_its_curated_name(self) -> None:
        """The map still wins, so a poorly named token can still be renamed."""
        with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
            with server._typed_dayz_test_value_errors():
                raise ValueError("invalid_native_launcher_transaction")
        self.assertEqual(caught.exception.code, "launcher_transaction_invalid")

    def test_a_message_that_could_carry_a_path_still_escapes_untyped(self) -> None:
        """Untyped means server.py drops the message: the leak stays closed."""
        with self.assertRaises(ValueError) as caught:
            with server._typed_dayz_test_value_errors():
                raise ValueError(r"cannot open C:\Users\someone\LFPowerGrid_dev")
        self.assertNotIsInstance(caught.exception, dayz_test_tool.DayzTestToolError)

    def test_a_non_value_error_is_untouched(self) -> None:
        with self.assertRaises(KeyError):
            with server._typed_dayz_test_value_errors():
                raise KeyError("untouched")


if __name__ == "__main__":
    unittest.main()
