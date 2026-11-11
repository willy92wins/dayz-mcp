"""Enforce source helpers and the log string shared by test_player_move and
test_player_look_mirror: blocks, bodies and locals read from the addon's
sources, a method as a free function for translate, and the string + of the
log lines. Moved verbatim from test_player_move.py."""
from __future__ import annotations

import re
import unittest
from types import SimpleNamespace

from tests.enforce_subset_helpers import EnforceString, method_body

_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')
_LOCAL_TYPES = (
    "string", "int", "float", "bool", "vector", "PlayerBase", "HumanInputController",
    "MCPArgs", "MCPJob", "MCPResult", "MCPPlayerMove", "MCPPlayerMoveRequest",
    "ScriptInputUserData",
)
_LOCAL_DECLARATION = re.compile(
    r"^\s*(?:" + "|".join(_LOCAL_TYPES) + r")\s+(\w+)\s*(?:=[^;]*)?;\s*$"
)


def _block_after(source: str, start: int) -> str:
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated block at offset {start}")


def _method_body(source: str, signature: str) -> str:
    if source.count(signature) != 1:
        raise AssertionError(f"{signature!r} occurs {source.count(signature)} times")
    return _block_after(source, source.index(signature))


def _if_body(source: str, condition: str) -> str:
    needle = f"if ({condition})"
    if source.count(needle) != 1:
        raise AssertionError(f"{needle!r} occurs {source.count(needle)} times")
    return _block_after(source, source.index(needle))


def _flat(text: str) -> str:
    return " ".join(text.split())


def _without_comments(source: str) -> str:
    def replace(match: re.Match[str]) -> str:
        text = match.group(0)
        return text if text.startswith('"') else re.sub(r"[^\n]", " ", text)

    return _COMMENT_OR_STRING.sub(replace, source)


def _locals_declared_at_top(body: str) -> list[str]:
    """Local names, failing when one follows a statement or is declared twice."""
    names: list[str] = []
    statement_seen = False
    for raw in _without_comments(body).splitlines():
        line = raw.strip()
        if not line or line in ("{", "}"):
            continue
        match = _LOCAL_DECLARATION.match(raw)
        if match is None:
            statement_seen = True
            continue
        if statement_seen:
            raise AssertionError(f"declaration after a statement: {line}")
        if match.group(1) in names:
            raise AssertionError(f"local declared twice: {match.group(1)}")
        names.append(match.group(1))
    return names


def _in_order(test: unittest.TestCase, body: str, *tokens: str) -> None:
    positions = []
    for token in tokens:
        test.assertIn(token, body)
        positions.append(body.index(token))
    test.assertEqual(positions, sorted(positions), tokens)


class _LogString(EnforceString):
    """An Enforce string with the + the log lines use."""

    __slots__ = ()

    def __add__(self, other: object) -> "_LogString":
        if not isinstance(other, EnforceString):
            raise TypeError(f"string + {type(other).__name__}")
        return _LogString(self.data + other.data)


_ENFORCE_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
_TO_STRING = re.compile(r"\b([A-Za-z_][\w.]*)\.ToString\(\)")
INSTANCE_TYPES = SimpleNamespace(INSTANCETYPE_SERVER=21, INSTANCETYPE_CLIENT=22)


def _free_function(source: str, signature: str, members: str, owner: str) -> str:
    """One method of `source` as a free function: `members` read through `owner`,
    X.ToString() as ToStr(X) and `new ScriptInputUserData()` as a call."""
    member = re.compile(r"(?<![\w.])(" + members + r")\b")

    def rewrite(code: str) -> str:
        code = member.sub(owner + r".\1", code).replace("new ScriptInputUserData()", "ScriptInputUserData()")
        return _TO_STRING.sub(r"ToStr(\1)", code)

    body = method_body(source, signature)
    pieces: list[str] = []
    last = 0
    for match in _ENFORCE_STRING.finditer(body):
        pieces += [rewrite(body[last : match.start()]), match.group(0)]
        last = match.end()
    pieces.append(rewrite(body[last:]))
    return signature + "\n{" + "".join(pieces) + "}\n"
