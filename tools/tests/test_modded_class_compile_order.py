"""Compile-order guard for `modded class` members (RPT 2026-11-12 00:37).

Compile order is a HYPOTHESIS, not taken from the engine: the RPT does not
log per-file loading. The only evidence is ronda-A-server.RPT:125/156
(2026-11-12 00:37): mcp_lookat.c failed to see a field declared in
mcp_playermove.c, so LookAt compiled first; this is consistent with
case-insensitive file-name order (the RPT reports lower-cased paths) and is
what this test assumes. Refuted by: an in-game run where a field declared in
a later-named file is visible from an earlier-named one, or where
MCP_ActionCursor.c / MCPBridge.c (5_Mission, where lower() and ordinal
order disagree) show the other order.

That disagreement is real in 5_Mission: ``MCP_`` sorts before ``MCPBridge``
with ``lower()`` (``_`` < ``b``) but after it in ordinal ASCII order
(``B`` < ``_``). This test therefore checks BOTH orders and fails if either
one is violated; a member is safe only when a declaration in the same or an
earlier file exists under both orders.

A ``modded class X`` block is assumed to see only the members that earlier
files of the chain (and the vanilla class) have already declared. The
offline linter accepts a reference to a member declared by a later file of
the same chain, but the engine did not: the server died at boot with

    DayZ_MCP/scripts/4_World/mcp_lookat.c(1171): Can't find variable 'm_MCPConnGen'
    SCRIPT (E): Can't compile "World" script module!

because ``MCPLookCloseServer`` (MCP_LookAt.c) read ``m_MCPConnGen``, declared
only in MCP_PlayerMove.c, which compiles after MCP_LookAt.c.

This test walks each module's files in both name orders above and fails when
code inside a ``modded class X`` block uses a field, or calls a non-override
method, whose only declaration in this repo is another ``modded class X``
in a file that compiles later. Override methods are not reported: the
vanilla class already declares them, so an earlier call binds to vanilla.
The method check is also a hypothesis: the RPT only covers fields
(``Can't find variable``); there is no evidence that the engine fails when
calling a non-override method declared in a later file, so a method-only
failure may be a false positive. Comments and string literals are stripped
before scanning, so a mention in prose is not a use.
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from tests._addon_paths import addon_root

_MODDED_RE = re.compile(r"\bmodded\s+class\s+(\w+)")
_FIELD_RE = re.compile(
    r"^\s*(?:protected|private|public)?\s*(?:static\s+)?(?:ref\s+)?"
    r"(?:const\s+)?(?:autoptr\s+)?[\w\[\]]+(?:\s*<.*>)?\s+"
    r"((?:m_\w+\s*(?:=[^;,]*)?)(?:\s*,\s*m_\w+\s*(?:=[^;,]*)?)*)\s*;\s*$"
)
_METHOD_RE = re.compile(
    r"^\s*(override\s+)?(?:protected\s+|private\s+|public\s+)?(?:static\s+)?"
    r"[\w\[\]]+(?:\s*<.*>)?\s+(\w+)\s*\("
)


def _strip_comments_and_strings(text: str) -> str:
    """Blank // and /* */ comments and "..." literals, keeping lines."""
    out: list[str] = []
    i = 0
    state = "code"
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if state == "code":
            if ch == "/" and nxt == "/":
                state = "line_comment"
                out.append("  ")
                i += 2
            elif ch == "/" and nxt == "*":
                state = "block_comment"
                out.append("  ")
                i += 2
            elif ch == '"':
                state = "string"
                out.append(" ")
                i += 1
            else:
                out.append(ch)
                i += 1
        elif state == "line_comment":
            if ch == "\n":
                state = "code"
                out.append(ch)
            else:
                out.append(" ")
            i += 1
        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                state = "code"
                out.append("  ")
                i += 2
            else:
                out.append(ch if ch == "\n" else " ")
                i += 1
        else:  # string
            if ch == "\\" and nxt:
                out.append("  ")
                i += 2
            elif ch == '"':
                state = "code"
                out.append(" ")
                i += 1
            else:
                out.append(ch if ch == "\n" else " ")
                i += 1
    return "".join(out)


def _module_dirs() -> list[Path]:
    """Script module directories from config.cpp, via $PBOPREFIX$."""
    root = addon_root()
    prefix = (root / "$PBOPREFIX$").read_text(encoding="utf-8").strip()
    config = (root / "config.cpp").read_text(encoding="utf-8")
    dirs: list[Path] = []
    for entry in re.findall(r'files\[\]\s*=\s*\{"([^"]+)"\}', config):
        if not entry.startswith(prefix + "/"):
            raise AssertionError(f"files[] entry outside $PBOPREFIX$: {entry}")
        dirs.append(root / entry[len(prefix) + 1:])
    return dirs


def _modded_blocks(lines: list[str]) -> list[tuple[str, int, list[tuple[int, str]]]]:
    """(class name, start line, [(line number, line)]) per modded block."""
    blocks: list[tuple[str, int, list[tuple[int, str]]]] = []
    i = 0
    while i < len(lines):
        match = _MODDED_RE.search(lines[i])
        if not match:
            i += 1
            continue
        depth = 0
        started = False
        block: list[tuple[int, str]] = []
        j = i
        while j < len(lines):
            block.append((j + 1, lines[j]))
            depth += lines[j].count("{") - lines[j].count("}")
            if "{" in lines[j]:
                started = True
            j += 1
            if started and depth == 0:
                break
        blocks.append((match.group(1), i + 1, block))
        i = j
    return blocks


def _declarations(block: list[tuple[int, str]]) -> list[tuple[str, str]]:
    """(member, kind) declared at class depth; kind is field/method/override."""
    found: list[tuple[str, str]] = []
    depth = 0
    for _lineno, line in block:
        before = depth
        depth += line.count("{") - line.count("}")
        if before != 1:
            continue
        field = _FIELD_RE.match(line)
        if field:
            for name in re.findall(r"\bm_\w+", field.group(1)):
                found.append((name, "field"))
            continue
        method = _METHOD_RE.match(line)
        if method and "(" in line:
            kind = "override" if method.group(1) else "method"
            found.append((method.group(2), kind))
    return found


def _violations(
    module_dirs: list[Path] | None = None, root: Path | None = None
) -> list[str]:
    root = root or addon_root()
    problems: list[str] = []
    seen: set[str] = set()
    for module_dir in (module_dirs if module_dirs is not None else _module_dirs()):
        unsorted = list(module_dir.glob("*.c"))
        # Check both orders: lower() and ordinal disagree in 5_Mission.
        for key in (lambda p: p.name.lower(), lambda p: p.name):
            files = sorted(unsorted, key=key)
            # (class, member) -> (earliest file index, file name, kinds)
            declared: dict[tuple[str, str], tuple[int, str, set[str]]] = {}
            parsed: list[tuple[Path, list[tuple[str, int, list[tuple[int, str]]]]]] = []
            for idx, path in enumerate(files):
                text = _strip_comments_and_strings(path.read_text(encoding="utf-8"))
                blocks = _modded_blocks(text.splitlines())
                parsed.append((path, blocks))
                for class_name, _start, block in blocks:
                    for member, kind in _declarations(block):
                        decl_key = (class_name, member)
                        if decl_key not in declared:
                            declared[decl_key] = (idx, path.name, {kind})
                        else:
                            declared[decl_key][2].add(kind)
            for idx, (path, blocks) in enumerate(parsed):
                for class_name, _start, block in blocks:
                    for lineno, line in block:
                        for (decl_class, member), (decl_idx, decl_file, kinds) in declared.items():
                            if decl_class != class_name or decl_idx <= idx:
                                continue
                            if member.startswith("m_"):
                                used = re.search(r"\b" + re.escape(member) + r"\b", line)
                            else:
                                if kinds != {"method"}:
                                    continue
                                used = re.search(r"\b" + re.escape(member) + r"\s*\(", line)
                                if used and line[: used.start()].rstrip().endswith("super."):
                                    used = None
                            if used:
                                rel = path.relative_to(root)
                                problem = (
                                    f"{rel}:{lineno}: modded class {class_name} uses "
                                    f"{member}, only declared in {decl_file} "
                                    f"(compiles later): {line.strip()}"
                                )
                                if problem not in seen:
                                    seen.add(problem)
                                    problems.append(problem)
    return problems


_USE = (
    "modded class PlayerBase\n{\n\tbool MCPLookCloseServer(int g)\n\t{\n"
    "\t\treturn g == m_MCPConnGen;\n\t}\n}\n"
)
_DECL = "modded class PlayerBase\n{\n\tprotected int m_MCPConnGen;\n}\n"
_DECL_MULTI = (
    "modded class PlayerBase\n{\n\tprotected int m_MCPConnGen, m_Other;\n}\n"
)


class CompileOrderFixtureTests(unittest.TestCase):
    def _run(self, files: dict[str, str]) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            mod = Path(tmp) / "4_World"
            mod.mkdir()
            for name, body in files.items():
                (mod / name).write_text(body, encoding="utf-8", newline="\n")
            return _violations([mod], Path(tmp))

    def test_pos_use_before_later_declaration_fails(self) -> None:
        # Caso RPT 2026-11-12: LookAt usa, PlayerMove (posterior) declara.
        got = self._run({"MCP_LookAt.c": _USE, "MCP_PlayerMove.c": _DECL})
        self.assertEqual(len(got), 1)
        self.assertIn("m_MCPConnGen", got[0])

    def test_neg_declared_in_earlier_file_passes(self) -> None:
        self.assertEqual(
            self._run({"MCP_InventoryMove.c": _DECL, "MCP_LookAt.c": _USE}), []
        )

    def test_multiple_declarations_earlier_and_later_passes(self) -> None:
        self.assertEqual(
            self._run(
                {
                    "MCP_InventoryMove.c": _DECL,
                    "MCP_LookAt.c": _USE,
                    "MCP_PlayerMove.c": _DECL,
                }
            ),
            [],
        )

    def test_multi_member_declaration_in_later_file_fails(self) -> None:
        # Mutante M5 de D-r14: `int m_MCPConnGen, m_Other;` debe registrarse.
        got = self._run({"MCP_LookAt.c": _USE, "MCP_PlayerMove.c": _DECL_MULTI})
        self.assertEqual(len(got), 1)
        self.assertIn("m_MCPConnGen", got[0])

    def test_multi_member_declaration_in_earlier_file_passes(self) -> None:
        self.assertEqual(
            self._run({"MCP_InventoryMove.c": _DECL_MULTI, "MCP_LookAt.c": _USE}),
            [],
        )

    def test_ordinal_only_violation_fails(self) -> None:
        # lower(): MCP_Decl antes que MCPBridge (pasa); ordinal: al reves (falla).
        got = self._run({"MCP_Decl.c": _DECL, "MCPBridge.c": _USE})
        self.assertEqual(len(got), 1)
        self.assertIn("m_MCPConnGen", got[0])


class ModdedClassCompileOrderTests(unittest.TestCase):
    def test_modules_come_from_config(self) -> None:
        names = [d.name for d in _module_dirs()]
        self.assertEqual(names, ["4_World", "5_Mission"])

    def test_no_use_of_a_member_declared_only_by_a_later_file(self) -> None:
        self.assertEqual(_violations(), [])


if __name__ == "__main__":
    unittest.main()
