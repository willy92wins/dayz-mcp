"""Client poll-caps census read from MCPClientBridge.c.

Shared by the capability contract and the weapon-action contract. The parsers
stay here so one test module does not import another.
"""
from __future__ import annotations

import re


DISPATCH_SIGNATURE = "protected void Dispatch(MCPCommand command)"

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_NAMES = 64
MAX_BYTES = 4096
# Longest single string literal anywhere in vanilla is 237 bytes
# (4_world/entities/vehicles/carscript.c:82). The census is longer than that,
# so it is written as short literals joined with `+` at comma boundaries
# (precedent 5_mission/gui/chat/chatline.c:8-10); no piece may approach the
# vanilla ceiling. A compile failure is invisible to a text gate, which is why
# the ceiling is asserted here and its control re-injects the single literal.
MAX_LITERAL_CHARS = 200

CAPS_DECL_RE = re.compile(
    r'protected const string CLIENT_POLL_CAPS = ((?:"[^"\n]*"(?: \+ )?)+);'
)
LITERAL_RE = re.compile(r'"([^"\n]*)"')
BRANCH_RE = re.compile(r'if \(command\.cmd == "([^"\n]*)"\)')
UNKNOWN_LITERAL = 'result.error = "unknown_command";'


def _method_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated method: {signature}")


def dispatch_census(source: str) -> list[str]:
    """Every command.cmd branch of Dispatch(), in order, up to unknown_command.

    Raises AssertionError when the chain does not end in the terminal else or
    when a branch sits outside the single if/else-if chain.
    """
    body = _method_body(source, DISPATCH_SIGNATURE)
    if body.count(UNKNOWN_LITERAL) != 1:
        raise AssertionError("Dispatch must end in exactly one unknown_command branch")
    unknown_idx = body.index(UNKNOWN_LITERAL)
    matches = list(BRANCH_RE.finditer(body))
    if not matches:
        raise AssertionError("Dispatch has no command.cmd branches")
    names = [match.group(1) for match in matches]
    if matches[-1].start() > unknown_idx:
        raise AssertionError("a command.cmd branch follows unknown_command")
    # A single chain: one `if`, the rest `else if`, then the terminal `else`.
    plain_if = body.count("if (command.cmd ==")
    else_if = body.count("else if (command.cmd ==")
    if plain_if != len(names) or else_if != len(names) - 1:
        raise AssertionError("command.cmd branches must form one if/else-if chain")
    tail = body[matches[-1].end() : unknown_idx]
    if "\t\telse\n" not in tail or "command.cmd" in tail:
        raise AssertionError("the last branch must be followed by the terminal else")
    if len(set(names)) != len(names):
        raise AssertionError("duplicate command.cmd branch")
    return names


def announced_literals(source: str) -> list[str]:
    """The string literals of the CLIENT_POLL_CAPS initializer, in order."""
    match = CAPS_DECL_RE.search(source)
    if not match:
        raise AssertionError("CLIENT_POLL_CAPS declaration not found")
    pieces = LITERAL_RE.findall(match.group(1))
    if not pieces:
        raise AssertionError("CLIENT_POLL_CAPS has no string literal")
    for index, piece in enumerate(pieces):
        if len(piece) > MAX_LITERAL_CHARS:
            raise AssertionError(
                f"literal piece {index} is {len(piece)} chars, above the {MAX_LITERAL_CHARS} ceiling"
            )
        if piece == "":
            raise AssertionError(f"literal piece {index} is empty")
        last = index == len(pieces) - 1
        if not last and not piece.endswith(","):
            raise AssertionError(f"literal piece {index} must end at a comma boundary")
        if last and piece.endswith(","):
            raise AssertionError("last literal piece must not end with a comma")
    return pieces


def announced_caps(source: str) -> list[str]:
    raw = "".join(announced_literals(source))
    if len(raw.encode("ascii", "strict")) > MAX_BYTES:
        raise AssertionError("announcement exceeds 4096 bytes")
    names = raw.split(",") if raw else []
    if len(names) > MAX_NAMES:
        raise AssertionError("announcement exceeds 64 names")
    for name in names:
        if not NAME_RE.match(name):
            raise AssertionError(f"invalid capability name {name!r}")
    if len(set(names)) != len(names):
        raise AssertionError("duplicate capability name")
    if names != sorted(names):
        raise AssertionError("announcement must be sorted bytewise (canonical)")
    return names
