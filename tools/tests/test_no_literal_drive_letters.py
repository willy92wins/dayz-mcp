"""Fail if the published package hardcodes a Windows drive letter.

The 2026-08-21 cleanup cleared author-local `X:\\...` paths out of
`tools/dayz_mcp/*.py` by hand. Nothing was checking they stayed gone.

Exceptions (short, each with a reason):

1. A string that is the declared `C:\\Program Files` Steam/Windows default
   install (including `(x86)`). Those are fallbacks, not this host.
2. Module/class/function docstrings. Example paths belong in documentation.
3. `tools/tests/**` is out of scope. Those tests build fake Windows paths on
   purpose (`test_doctor.py`, `test_install_mcp.py`, `test_secure_launcher.py`).
   This scan is the published package only.
"""

from __future__ import annotations

import ast
import io
import re
import tempfile
import tokenize
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1]
PACKAGE = TOOLS_DIR / "dayz_mcp"

# Exception 1. Compared case-insensitively against the slice that starts at
# the drive match, so `C:\Program Files (x86)\Steam\...` is covered by this
# one prefix and does not need its own entry.
_ALLOWED_DRIVE_PREFIXES = (
    "c:\\program files",  # declared Steam/Windows default-install fallback
)

_DRIVE_IN_STRING = re.compile(r"[A-Za-z]:\\")


def _docstring_lines(source: str) -> set[int]:
    """Line numbers of module/class/function docstrings (exception 2)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    lines: set[int] = set()

    def mark(node: ast.AST) -> None:
        body = getattr(node, "body", None)
        if not body:
            return
        first = body[0]
        if not (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            return
        start = first.lineno
        end = first.end_lineno or start
        lines.update(range(start, end + 1))

    mark(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            mark(node)
    return lines


def _decoded_string_tokens(source: str) -> list[tuple[int, str]]:
    """Non-docstring string literals as `(lineno, decoded_value)`."""
    skip = _docstring_lines(source)
    out: list[tuple[int, str]] = []
    readline = io.StringIO(source).readline
    try:
        tokens = list(tokenize.generate_tokens(readline))
    except tokenize.TokenError:
        return out
    fstring_middle = getattr(tokenize, "FSTRING_MIDDLE", None)
    for tok in tokens:
        if tok.type == tokenize.STRING:
            if tok.start[0] in skip:
                continue
            try:
                value = ast.literal_eval(tok.string)
            except (ValueError, SyntaxError):
                value = tok.string
            if isinstance(value, bytes):
                value = value.decode("utf-8", "replace")
            if isinstance(value, str):
                out.append((tok.start[0], value))
        elif fstring_middle is not None and tok.type == fstring_middle:
            if tok.start[0] not in skip:
                out.append((tok.start[0], tok.string))
    return out


def _illegal_drive_snippets(value: str) -> list[str]:
    snippets: list[str] = []
    for match in _DRIVE_IN_STRING.finditer(value):
        rest = value[match.start() :]
        folded = rest.casefold()
        if any(folded.startswith(prefix) for prefix in _ALLOWED_DRIVE_PREFIXES):
            continue
        snippets.append(rest.splitlines()[0][:80])
    return snippets


def drive_letter_hits(package: Path) -> list[str]:
    """Human-readable hits: `relative.py:line: snippet`."""
    reports: list[str] = []
    for path in sorted(package.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        rel = path.relative_to(package).as_posix()
        for lineno, value in _decoded_string_tokens(source):
            for snippet in _illegal_drive_snippets(value):
                reports.append(f"{rel}:{lineno}: {snippet}")
    return reports


class NoLiteralDriveLettersTest(unittest.TestCase):
    def test_injected_literal_in_a_module_turns_red_then_green(self) -> None:
        """The scanner must go red on a planted `Z:\\...` and green once removed.

        Runs against a temp copy of `dayz_mcp/__init__.py`, never the tree.
        """
        original = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
        planted = original + '\nLEAK = r"Z:\\Users\\guill\\injected"\n'
        with tempfile.TemporaryDirectory() as raw:
            pkg = Path(raw) / "dayz_mcp"
            pkg.mkdir()
            target = pkg / "__init__.py"
            target.write_text(planted, encoding="utf-8")
            red = drive_letter_hits(pkg)
            self.assertTrue(
                red,
                "planting Z:\\Users\\guill\\injected in a module copy must fail",
            )
            self.assertTrue(any("Z:\\Users\\guill\\injected" in row for row in red))
            target.write_text(original, encoding="utf-8")
            self.assertEqual(drive_letter_hits(pkg), [])

    def test_program_files_fallback_is_allowed(self) -> None:
        source = (
            "_DAYZ = r"
            '"C:\\Program Files (x86)\\Steam\\steamapps\\common\\DayZ"\n'
        )
        with tempfile.TemporaryDirectory() as raw:
            pkg = Path(raw) / "dayz_mcp"
            pkg.mkdir()
            (pkg / "fallback.py").write_text(source, encoding="utf-8")
            self.assertEqual(drive_letter_hits(pkg), [])

    def test_docstring_example_path_is_allowed(self) -> None:
        source = (
            '"""Example host path: ``C:\\\\Users\\\\example\\\\DayZ``."""\n'
            "VALUE = 1\n"
        )
        with tempfile.TemporaryDirectory() as raw:
            pkg = Path(raw) / "dayz_mcp"
            pkg.mkdir()
            (pkg / "docs.py").write_text(source, encoding="utf-8")
            self.assertEqual(drive_letter_hits(pkg), [])

    def test_code_string_other_than_program_files_is_reported(self) -> None:
        source = 'HOME = r"C:\\Users\\guill\\OneDrive\\Documentos"\n'
        with tempfile.TemporaryDirectory() as raw:
            pkg = Path(raw) / "dayz_mcp"
            pkg.mkdir()
            (pkg / "leak.py").write_text(source, encoding="utf-8")
            hits = drive_letter_hits(pkg)
            self.assertTrue(hits)
            self.assertTrue(any("C:\\Users\\guill" in row for row in hits))

    def test_published_package_has_no_literal_drive_letters(self) -> None:
        self.assertTrue(PACKAGE.is_dir(), f"missing package {PACKAGE}")
        hits = drive_letter_hits(PACKAGE)
        self.assertEqual(
            hits,
            [],
            "literal drive letter in a published package string:\n"
            + "\n".join(hits),
        )


if __name__ == "__main__":
    unittest.main()
