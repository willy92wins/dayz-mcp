"""Every source the audits read must actually parse.

A UTF-8 BOM is the way this breaks on Windows and it breaks asymmetrically:
Python's import machinery strips the BOM, so the module imports and its tests
pass, while `ast.parse(path.read_text(encoding="utf-8"))` raises on U+FEFF. Every
static audit in this tree is built on that second pair, so a BOM does not fail a
file loudly -- it removes the file from analysis and reports one `parse_error`
where the real findings were.

That is not hypothetical here. `tools/mcp_client.py` carried a BOM while holding
four unaccredited HTTP findings, and an exclusion keyed by filename suppressed
the parse_error the BOM produced. Two independent suppressions, each defensible
alone, added up to a security audit that returned zero.

PowerShell's `Set-Content` and `Out-File` write a BOM by default on this
platform, so any contributor editing a source from a shell can reintroduce it.
This test names the cause directly instead of leaving a parse_error for someone
to interpret.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
UTF8_BOM = b"\xef\xbb\xbf"

# Directories that hold neither shipped code nor audited scripts.
_SKIP_DIRS = frozenset({"__pycache__", ".venv-mcp", "node_modules"})


def _sources() -> list[Path]:
    out: list[Path] = []
    for path in TOOLS_DIR.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        out.append(path)
    return sorted(out)


class SourcesAreStaticallyAnalysableTest(unittest.TestCase):
    def test_no_python_source_starts_with_a_utf8_bom(self) -> None:
        offenders = [
            path.relative_to(TOOLS_DIR).as_posix()
            for path in _sources()
            if path.read_bytes().startswith(UTF8_BOM)
        ]
        self.assertEqual(
            offenders,
            [],
            "UTF-8 BOM found. The module will still import, but every ast-based "
            "audit will report parse_error instead of reading it. Re-save as "
            "UTF-8 without BOM (PowerShell: -Encoding utf8NoBOM).",
        )

    def test_every_python_source_parses_the_way_the_audits_read_it(self) -> None:
        failures: list[str] = []
        for path in _sources():
            relative = path.relative_to(TOOLS_DIR).as_posix()
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                failures.append("%s: %s" % (relative, exc))
                continue
            try:
                ast.parse(source, filename=str(path))
            except SyntaxError as exc:
                failures.append("%s: %s" % (relative, exc.msg))
        self.assertEqual(failures, [], "sources the audits cannot parse: %r" % failures)


if __name__ == "__main__":
    unittest.main()
