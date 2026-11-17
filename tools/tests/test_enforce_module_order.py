"""Enforce compiles a script module file by file; each `modded class X` adds a
layer that only sees methods declared in layers compiled before it. A call in
an earlier 4_World file to a PlayerBase method that only a later 4_World file
declares does not compile ("Undefined function 'PlayerBase#...'", then
"Can't compile \"World\" script module!"; dayz-mcp #413 @ c5408fd2, in game
02:13 15-nov: mcp_playercare.c(537) -> MCPStanceBegin in MCP_PlayerStance.c).

This static check orders the addon's 4_World files the way the RPT names them
(lowercased path) and fails when a file calls a method that is first declared
by `modded class PlayerBase` in a file later in that order.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from tests._addon_paths import addon_root

WORLD = addon_root() / "scripts" / "4_World"
_CLASS_RE = re.compile(r"^(?:modded\s+)?class\s+(\w+)", re.M)
_METHOD_RE = re.compile(
    r"^\t(?:(?:override|protected|private|static|proto|native|void|bool|int|float|string|ref|auto)\s+|[A-Z]\w*(?:<[^>]*>)?\s+)+(\w+)\s*\(",
    re.M,
)
_CALL_RE = re.compile(r"(?<![\w.])(?:\w+\.)?(MCP\w+)\s*\(")
_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"')


def _strip(source: str) -> str:
    return _STRING_RE.sub('""', _COMMENT_RE.sub("", source))


def _class_blocks(source: str) -> list[tuple[str, str]]:
    starts = [(m.start(), m.group(1)) for m in _CLASS_RE.finditer(source)]
    blocks = []
    for index, (start, name) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(source)
        blocks.append((name, source[start:end]))
    return blocks


def world_files() -> list[Path]:
    return sorted(WORLD.rglob("*.c"), key=lambda p: p.relative_to(WORLD).as_posix().lower())


def playerbase_methods(source: str) -> set[str]:
    names: set[str] = set()
    for name, block in _class_blocks(_strip(source)):
        if name == "PlayerBase":
            names.update(_METHOD_RE.findall(block))
    return names


def order_violations() -> list[str]:
    files = world_files()
    sources = {path: path.read_text(encoding="utf-8") for path in files}
    first_decl: dict[str, int] = {}
    for index, path in enumerate(files):
        for method in playerbase_methods(sources[path]):
            first_decl.setdefault(method, index)
    problems = []
    for index, path in enumerate(files):
        body = _strip(sources[path])
        for match in _CALL_RE.finditer(body):
            method = match.group(1)
            declared_at = first_decl.get(method)
            if declared_at is not None and declared_at > index:
                line = body.count("\n", 0, match.start()) + 1
                problems.append(
                    f"{path.name}:{line} calls PlayerBase.{method}, first declared in "
                    f"{files[declared_at].name} (compiled later)"
                )
    return problems


class WorldModuleOrderTest(unittest.TestCase):
    def test_no_call_to_a_playerbase_method_declared_in_a_later_world_file(self) -> None:
        self.assertEqual(order_violations(), [])

    def test_detector_sees_the_c5408fd2_failure_shape(self) -> None:
        early = "modded class PlayerBase\n{\n\tstring MCPSetStanceServer(string s)\n\t{\n\t\treturn MCPStanceBegin(s);\n\t}\n};\n"
        late = "modded class PlayerBase\n{\n\tstring MCPStanceBegin(string s)\n\t{\n\t\treturn \"\";\n\t}\n};\n"
        self.assertIn("MCPSetStanceServer", playerbase_methods(early))
        self.assertIn("MCPStanceBegin", playerbase_methods(late))
        self.assertNotIn("MCPStanceBegin", playerbase_methods(early))
        calls = [m.group(1) for m in _CALL_RE.finditer(_strip(early))]
        self.assertIn("MCPStanceBegin", calls)

    def test_order_is_the_rpt_lowercase_order(self) -> None:
        names = [p.name.lower() for p in world_files()]
        self.assertEqual(names, sorted(names))
        self.assertLess(names.index("mcp_playercare.c"), names.index("mcp_playerstance.c"))


if __name__ == "__main__":
    unittest.main()
