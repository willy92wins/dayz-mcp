"""Compile-order guard for `modded class` members (RPT 2026-11-12 00:37).

DayZ compiles every file of a script module (the directories named by
``files[]`` in ``addon/config.cpp`` under ``$PBOPREFIX$``) by file name, and
a ``modded class X`` block only sees the members that earlier files of the
chain (and the vanilla class) have already declared. The offline linter
accepts a reference to a member declared by a later file of the same chain,
but the engine does not: the server died at boot with

    DayZ_MCP/scripts/4_World/mcp_lookat.c(1171): Can't find variable 'm_MCPConnGen'
    SCRIPT (E): Can't compile "World" script module!

because ``MCPLookCloseServer`` (MCP_LookAt.c) read ``m_MCPConnGen``, declared
only in MCP_PlayerMove.c, which compiles after MCP_LookAt.c.

This test walks each module's files in that same name order and fails when
code inside a ``modded class X`` block uses a field, or calls a non-override
method, whose only declaration in this repo is another ``modded class X``
in a file that compiles later. Override methods are not reported: the
vanilla class already declares them, so an earlier call binds to vanilla.
Comments and string literals are stripped before scanning, so a mention in
prose is not a use.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests._addon_paths import addon_root

_MODDED_RE = re.compile(r"\bmodded\s+class\s+(\w+)")
_FIELD_RE = re.compile(
    r"^\s*(?:protected|private|public)?\s*(?:static\s+)?(?:ref\s+)?"
    r"(?:const\s+)?(?:autoptr\s+)?[\w\[\]]+(?:\s*<.*>)?\s+(m_\w+)\s*(?:=[^;]*)?;\s*$"
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
            found.append((field.group(1), "field"))
            continue
        method = _METHOD_RE.match(line)
        if method and "(" in line:
            kind = "override" if method.group(1) else "method"
            found.append((method.group(2), kind))
    return found


def _violations() -> list[str]:
    problems: list[str] = []
    for module_dir in _module_dirs():
        files = sorted(module_dir.glob("*.c"), key=lambda p: p.name.lower())
        order = {path.name.lower(): idx for idx, path in enumerate(files)}
        # (class, member) -> (earliest file index, file name, kinds)
        declared: dict[tuple[str, str], tuple[int, str, set[str]]] = {}
        parsed: list[tuple[Path, list[tuple[str, int, list[tuple[int, str]]]]]] = []
        for path in files:
            text = _strip_comments_and_strings(path.read_text(encoding="utf-8"))
            blocks = _modded_blocks(text.splitlines())
            parsed.append((path, blocks))
            for class_name, _start, block in blocks:
                for member, kind in _declarations(block):
                    key = (class_name, member)
                    idx = order[path.name.lower()]
                    if key not in declared or idx < declared[key][0]:
                        kinds = {kind} if key not in declared else declared[key][2] | {kind}
                        declared[key] = (idx, path.name, kinds)
                    else:
                        declared[key][2].add(kind)
        for path, blocks in parsed:
            idx = order[path.name.lower()]
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
                            rel = path.relative_to(addon_root())
                            problems.append(
                                f"{rel}:{lineno}: modded class {class_name} uses "
                                f"{member}, only declared in {decl_file} "
                                f"(compiles later): {line.strip()}"
                            )
    return problems


class ModdedClassCompileOrderTests(unittest.TestCase):
    def test_modules_come_from_config(self) -> None:
        names = [d.name for d in _module_dirs()]
        self.assertEqual(names, ["4_World", "5_Mission"])

    def test_no_use_of_a_member_declared_only_by_a_later_file(self) -> None:
        self.assertEqual(_violations(), [])


if __name__ == "__main__":
    unittest.main()
