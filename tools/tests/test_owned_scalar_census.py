"""Owned-scalar census: which commands' dispatch writes each owned MCPResult scalar.

result_prune.OWNED_SCALAR_FIELDS keeps an owned scalar only for its owner
commands (fb-20260823-130809-a412) and NEVER_FILLED_SCALAR_FIELDS drops a scalar
no command assigns (fb-20260823-141958-dde3). Both are hand-kept, so this test
derives them again from the two bridges, MCPBridge.c and MCPClientBridge.c:
every assignment to one of those fields on an MCPResult, attributed to the
commands that can reach it. A command that writes an owned field without being
one of its owners fails here, so a new verb cannot lose its answer to the prune
unnoticed. A listed owner that no longer writes the field fails too, so the prune
does not keep a stale 0 for it. A never-filled field must have no write at all.

This is attribution, not execution. Comments are blanked, preprocessor lines
ignored and string contents masked, then every method of every class is found
by brace depth. A write, a call or a `job.kind = "..."` is attributed through
the `if` blocks around it:

- `if (command.cmd == "x")`, with `command` an MCPCommand, narrows to {x};
- `if (job.kind == "k")`, with `job` an MCPJob, narrows to the commands whose
  dispatch reaches `job.kind = "k"`;
- a method called only from its own class gets the union of its call sites.
  A method that is public, `override`, referenced without a call, called on
  another object or named in a string literal can be reached from outside, so it
  starts with every command.

Nothing else narrows: an `else`, a loop, a negated test or a gate mixed with
another test under `||`. Each of these simplifications can only widen the set of
commands a write is attributed to, so the census can report a writer that does
not exist but cannot miss one that does. A write it cannot place (a variable it
cannot type, a chained target, a write that no command reaches or that any
command reaches) and a `job.kind` set from a non-literal fail the test instead of
being guessed. A field passed as a bare call argument counts as a write, because
an `out` parameter writes it.
"""
from __future__ import annotations

import bisect
import re
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback
from dayz_mcp.result_prune import (
    NEVER_FILLED_SCALAR_FIELDS,
    OWNED_SCALAR_FIELDS,
    PRUNABLE_FIELDS,
)
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import clean

SCRIPTS = addon_root() / "scripts"
MISSION = SCRIPTS / "5_Mission"
BRIDGES = ("MCPBridge.c", "MCPClientBridge.c")
WATCHED = frozenset(OWNED_SCALAR_FIELDS) | NEVER_FILLED_SCALAR_FIELDS

# "Any command": a method reachable from outside its class, before narrowing.
TOP = None

_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
_PREPROCESSOR = re.compile(r"(?m)^[ \t]*#[^\n]*")
_CLASS_HEADER = re.compile(r"^(?:modded\s+)?class\s+(\w+)\b")
_METHOD_HEADER = re.compile(
    r"^(?P<mods>(?:(?:protected|private|static|override|proto|native|sealed|"
    r"external|volatile|event)\s+)*)"
    r"(?P<ret>[\w<>\[\],\s]+?)\s+(?P<name>~?\w+)\s*\((?P<params>[^()]*)\)$"
)
# Used with match(text, pos, endpos), which anchors at pos; `^` would not.
_IF_HEADER = re.compile(r"(?:else\s+)?if\s*\(")
# A declaration: a type (optionally generic) then a name, then `=` `;` `,` `)` or
# `:` (foreach). Keywords that can precede a name are not types.
_DECLARATION = re.compile(
    r"(?<![\w.])([A-Za-z_]\w*)(?:\s*<[^<>;{}()]*(?:<[^<>;{}()]*>[^<>;{}()]*)?>)?"
    r"\s+(\w+)\s*(?=[=;,):])"
)
_NOT_TYPES = frozenset(
    {
        "return", "delete", "new", "else", "case", "out", "inout", "ref",
        "autoptr", "const", "static", "notnull", "private", "protected",
        "override", "class", "modded", "extends", "if", "while", "for",
        "foreach", "switch", "break", "continue", "goto", "thread", "typedef",
    }
)
_CALL = re.compile(r"(?<![\w.~])(\w+)\s*\(")
_QUALIFIED_CALL = re.compile(r"(\w+)\s*\.\s*(\w+)\s*\(")
_BARE_NAME = re.compile(r"(?<![\w.])(\w+)\b(?!\s*\()")
_THIS_NAME = re.compile(r"\bthis\s*\.\s*(\w+)\b(?!\s*\()")
_LITERAL_NAME = re.compile(r'"(\w+)"')
_ASSIGN_AFTER = re.compile(r"\s*(?:(?:[-+*/%&|^]|<<|>>)?=(?!=)|\+\+|--)")
_ROOT_BEFORE = re.compile(r"(?<![\w.)\]])(\w+)\s*$")
_KIND_WRITE = re.compile(r"(?<![\w.])(\w+)\s*\.\s*kind\s*=(?!=)\s*")
_KEYWORD_CALLS = frozenset({"if", "while", "for", "foreach", "switch", "return"})


@dataclass
class _Method:
    path: str
    klass: str
    name: str
    modifiers: frozenset[str]
    header: int
    open: int
    close: int
    types: dict[str, set[str]] = field(default_factory=dict)
    blocks: list[tuple[int, int, list[tuple[frozenset[str], frozenset[str]]]]] = field(
        default_factory=list
    )


@dataclass
class _Source:
    path: str
    text: str  # comments and preprocessor lines blanked, strings intact
    masked: str  # same length, string interiors masked

    def line(self, pos: int) -> int:
        return self.text.count("\n", 0, pos) + 1


def _prepare(path: str, raw: str) -> _Source:
    text = clean(raw)
    text = _PREPROCESSOR.sub(lambda m: " " * len(m.group()), text)
    masked = _STRING.sub(lambda m: '"' + "_" * (len(m.group()) - 2) + '"', text)
    return _Source(path, text, masked)


def _blocks(source: _Source) -> list[tuple[int, int, int, int]]:
    """(open, close, depth, header start) of every brace block."""
    stack: list[tuple[int, int]] = []
    found: list[tuple[int, int, int, int]] = []
    boundary = -1
    for index, char in enumerate(source.masked):
        if char == "{":
            stack.append((index, boundary + 1))
            boundary = index
        elif char == "}":
            if not stack:
                raise AssertionError(f"{source.path}:{source.line(index)}: unbalanced }}")
            open_at, header_at = stack.pop()
            found.append((open_at, index, len(stack), header_at))
            boundary = index
        elif char == ";":
            boundary = index
    if stack:
        raise AssertionError(f"{source.path}: unbalanced {{")
    return sorted(found)


def _matching_paren(masked: str, open_at: int, end: int) -> int:
    depth = 0
    for index in range(open_at, end):
        if masked[index] == "(":
            depth += 1
        elif masked[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _trim(masked: str, start: int, end: int) -> tuple[int, int]:
    while start < end and masked[start].isspace():
        start += 1
    while end > start and masked[end - 1].isspace():
        end -= 1
    return start, end


def _strip_parens(masked: str, start: int, end: int) -> tuple[int, int]:
    start, end = _trim(masked, start, end)
    while start < end and masked[start] == "(" and _matching_paren(masked, start, end) == end - 1:
        start, end = _trim(masked, start + 1, end - 1)
    return start, end


def _split_top(masked: str, start: int, end: int, operator: str) -> list[tuple[int, int]]:
    parts: list[tuple[int, int]] = []
    depth = 0
    piece = start
    index = start
    while index < end:
        char = masked[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and masked.startswith(operator, index):
            parts.append((piece, index))
            index += len(operator)
            piece = index
            continue
        index += 1
    parts.append((piece, end))
    return parts


def _single_type(types: dict[str, set[str]], name: str) -> str | None:
    declared = types.get(name)
    if declared and len(declared) == 1:
        return next(iter(declared))
    return None


def _term(
    source: _Source, start: int, end: int, types: dict[str, set[str]]
) -> tuple[frozenset[str], frozenset[str]] | None:
    """(commands, job kinds) one gate term admits, or None when it is not a gate."""
    start, end = _strip_parens(source.masked, start, end)
    text = source.text[start:end]
    for member, wanted in (("cmd", "MCPCommand"), ("kind", "MCPJob")):
        match = re.fullmatch(
            rf'(\w+)\s*\.\s*{member}\s*==\s*"([^"\\]*)"', text
        ) or re.fullmatch(rf'"([^"\\]*)"\s*==\s*(\w+)\s*\.\s*{member}', text)
        if match is None:
            continue
        if text.startswith('"'):
            literal, variable = match.group(1), match.group(2)
        else:
            variable, literal = match.group(1), match.group(2)
        if _single_type(types, variable) != wanted:
            return None
        if member == "cmd":
            return frozenset({literal}), frozenset()
        return frozenset(), frozenset({literal})
    return None


def _disjunction(
    source: _Source, start: int, end: int, types: dict[str, set[str]]
) -> tuple[frozenset[str], frozenset[str]] | None:
    commands: set[str] = set()
    kinds: set[str] = set()
    for part_start, part_end in _split_top(source.masked, start, end, "||"):
        term = _term(source, part_start, part_end, types)
        if term is None:
            return None
        commands |= term[0]
        kinds |= term[1]
    return frozenset(commands), frozenset(kinds)


def _gate_atoms(
    source: _Source, header_at: int, open_at: int, types: dict[str, set[str]]
) -> list[tuple[frozenset[str], frozenset[str]]]:
    """Gate atoms of one block header; each atom is (commands, kinds) to intersect with."""
    start, end = _trim(source.masked, header_at, open_at)
    match = _IF_HEADER.match(source.masked, start, end)
    if match is None:
        return []
    if source.masked[end - 1] != ")":
        return []
    paren = match.end() - 1
    if _matching_paren(source.masked, paren, end) != end - 1:
        return []
    cond_start, cond_end = _strip_parens(source.masked, paren + 1, end - 1)
    if len(_split_top(source.masked, cond_start, cond_end, "||")) > 1:
        whole = _disjunction(source, cond_start, cond_end, types)
        return [whole] if whole is not None else []
    atoms = []
    for part_start, part_end in _split_top(source.masked, cond_start, cond_end, "&&"):
        part_start, part_end = _strip_parens(source.masked, part_start, part_end)
        atom = _disjunction(source, part_start, part_end, types)
        if atom is not None:
            atoms.append(atom)
    return atoms


def _declare(types: dict[str, set[str]], text: str) -> None:
    for match in _DECLARATION.finditer(text):
        type_name, name = match.group(1), match.group(2)
        if type_name in _NOT_TYPES:
            continue
        types.setdefault(name, set()).add(type_name)


def _parse(source: _Source) -> tuple[list[_Method], dict[str, dict[str, set[str]]]]:
    """Methods of every class, and each class's member declarations."""
    blocks = _blocks(source)
    classes: list[tuple[str, int, int]] = []
    methods: list[_Method] = []
    for open_at, close_at, depth, header_at in blocks:
        header = " ".join(source.masked[header_at:open_at].split())
        if depth == 0:
            klass = _CLASS_HEADER.match(header)
            if klass:
                classes.append((klass.group(1), open_at, close_at))
                continue
        owner = "<global>" if depth == 0 else None
        if depth == 1:
            owner = next(
                (name for name, start, end in classes if start < open_at < end), None
            )
            if owner is None:
                continue
        if owner is None or not header.endswith(")"):
            continue
        signature = _METHOD_HEADER.match(header)
        if signature is None:
            raise AssertionError(
                f"{source.path}:{source.line(open_at)}: member block the census "
                f"cannot read: {header!r}"
            )
        method = _Method(
            path=source.path,
            klass=owner,
            name=signature.group("name"),
            modifiers=frozenset(signature.group("mods").split()),
            header=header_at,
            open=open_at,
            close=close_at,
        )
        _declare(method.types, signature.group("params") + ")")
        _declare(method.types, source.masked[open_at + 1 : close_at])
        methods.append(method)
    opens = [block[0] for block in blocks]
    for method in methods:
        index = bisect.bisect_right(opens, method.open)
        while index < len(blocks) and blocks[index][0] < method.close:
            open_at, close_at, _depth, header_at = blocks[index]
            atoms = _gate_atoms(source, header_at, open_at, method.types)
            method.blocks.append((open_at, close_at, atoms))
            index += 1
    members: dict[str, dict[str, set[str]]] = {}
    for name, start, end in classes:
        # Class level only: every method, signature included, is blanked.
        chars = list(source.masked[start + 1 : end])
        for method in methods:
            if method.klass == name and start < method.open < end:
                for index in range(method.header - start - 1, method.close - start):
                    chars[index] = " "
        members[name] = {}
        _declare(members[name], "".join(chars))
    return methods, members


def _resolve(
    method: _Method, class_members: dict[str, set[str]], name: str
) -> set[str] | None:
    """Declared types of `name` in `method`, else among its class's members."""
    if name in method.types:
        return method.types[name]
    return class_members.get(name)


def _atoms_at(method: _Method, pos: int) -> list[tuple[frozenset[str], frozenset[str]]]:
    atoms: list[tuple[frozenset[str], frozenset[str]]] = []
    for open_at, close_at, block_atoms in method.blocks:
        if open_at < pos < close_at:
            atoms.extend(block_atoms)
    return atoms


def _narrow(
    reach: frozenset[str] | None,
    atoms: list[tuple[frozenset[str], frozenset[str]]],
    kinds: dict[str, frozenset[str] | None],
) -> frozenset[str] | None:
    for commands, kind_names in atoms:
        admitted = set(commands)
        unbounded = False
        for kind in kind_names:
            value = kinds.get(kind, frozenset())
            if value is TOP:
                # A kind set where any command reaches admits any command.
                unbounded = True
                break
            admitted |= value
        if unbounded:
            continue
        reach = frozenset(admitted) if reach is TOP else reach & admitted
    return reach


def _union(left: frozenset[str] | None, right: frozenset[str] | None) -> frozenset[str] | None:
    if left is TOP or right is TOP:
        return TOP
    return left | right


@dataclass
class _Write:
    method: _Method
    pos: int
    field: str
    line: int


def census(sources: dict[str, str]) -> dict[str, frozenset[str]]:
    """Watched field -> the commands whose dispatch writes it, over `sources`.

    Raises AssertionError for a write, a call or a job kind it cannot place.
    """
    prepared = [_prepare(path, raw) for path, raw in sources.items()]
    methods: list[_Method] = []
    members: dict[tuple[str, str], dict[str, set[str]]] = {}
    for source in prepared:
        found, class_members = _parse(source)
        methods.extend(found)
        for klass, declared in class_members.items():
            members[(source.path, klass)] = declared

    def resolve(method: _Method, name: str) -> set[str] | None:
        return _resolve(method, members.get((method.path, method.klass), {}), name)

    by_name: dict[tuple[str, str], dict[str, _Method]] = {}
    for method in methods:
        by_name.setdefault((method.path, method.klass), {})[method.name] = method

    entries: set[int] = set()
    calls: dict[int, list[tuple[_Method, int]]] = {}
    kind_sites: dict[str, list[tuple[_Method, int]]] = {}
    writes: list[_Write] = []
    for source in prepared:
        literals = {match.group(1) for match in _LITERAL_NAME.finditer(source.text)}
        for (path, klass), named in by_name.items():
            if path != source.path:
                continue
            for method in named.values():
                if (
                    "override" in method.modifiers
                    or not {"protected", "private"} & method.modifiers
                    or method.name in literals
                ):
                    entries.add(id(method))
            for method in named.values():
                body = source.masked[method.open + 1 : method.close]
                offset = method.open + 1
                for match in _CALL.finditer(body):
                    callee = named.get(match.group(1))
                    if callee is not None and match.group(1) not in _KEYWORD_CALLS:
                        calls.setdefault(id(callee), []).append((method, offset + match.start()))
                for match in _QUALIFIED_CALL.finditer(body):
                    callee = named.get(match.group(2))
                    if callee is None or match.group(1) == "super":
                        continue
                    if match.group(1) == "this":
                        calls.setdefault(id(callee), []).append((method, offset + match.start()))
                    else:
                        entries.add(id(callee))
                for pattern in (_BARE_NAME, _THIS_NAME):
                    for match in pattern.finditer(body):
                        callee = named.get(match.group(1))
                        if callee is not None:
                            entries.add(id(callee))
                for match in _KIND_WRITE.finditer(body):
                    declared = resolve(method, match.group(1))
                    if declared is None:
                        raise AssertionError(
                            f"{source.path}:{source.line(offset + match.start())}: "
                            f"{match.group(1)}.kind assigned on a variable the census cannot type"
                        )
                    if "MCPJob" not in declared:
                        continue
                    literal = re.match(r'"(\w+)"\s*;', source.text[offset + match.end() :])
                    if literal is None:
                        raise AssertionError(
                            f"{source.path}:{source.line(offset + match.start())}: "
                            "job.kind assigned from a non-literal; the census cannot map it"
                        )
                    kind_sites.setdefault(literal.group(1), []).append(
                        (method, offset + match.start())
                    )
                writes.extend(_writes_in(source, method, resolve))

    reach: dict[int, frozenset[str] | None] = {
        id(method): (TOP if id(method) in entries else frozenset()) for method in methods
    }
    kinds: dict[str, frozenset[str] | None] = {}
    changed = True
    while changed:
        changed = False
        for kind, sites in kind_sites.items():
            admitted: frozenset[str] | None = frozenset()
            for method, pos in sites:
                admitted = _union(admitted, _narrow(reach[id(method)], _atoms_at(method, pos), kinds))
            if admitted != kinds.get(kind, frozenset()):
                kinds[kind] = admitted
                changed = True
        for method in methods:
            if id(method) in entries:
                continue
            new: frozenset[str] | None = frozenset()
            for caller, pos in calls.get(id(method), []):
                new = _union(new, _narrow(reach[id(caller)], _atoms_at(caller, pos), kinds))
            if new != reach[id(method)]:
                reach[id(method)] = new
                changed = True

    observed: dict[str, frozenset[str]] = {name: frozenset() for name in WATCHED}
    for write in writes:
        commands = _narrow(reach[id(write.method)], _atoms_at(write.method, write.pos), kinds)
        where = f"{write.method.path}:{write.line} ({write.method.klass}.{write.method.name})"
        if commands is TOP:
            raise AssertionError(
                f"{where}: .{write.field} is written on a path any command reaches"
            )
        if not commands:
            raise AssertionError(
                f"{where}: .{write.field} is written where the census finds no command"
            )
        observed[write.field] = observed[write.field] | commands
    return observed


def _enclosing_call(before: str) -> str | None:
    """Name before the innermost unclosed `(` that ends `before`, if any."""
    depth = 0
    for index in range(len(before) - 1, -1, -1):
        char = before[index]
        if char == ")":
            depth += 1
        elif char == "(":
            if depth == 0:
                name = re.search(r"(\w+)\s*$", before[:index])
                return name.group(1) if name else None
            depth -= 1
        elif char in "{};":
            return None
    return None


def _writes_in(source: _Source, method: _Method, resolve) -> list[_Write]:
    body = source.masked[method.open + 1 : method.close]
    offset = method.open + 1
    found: list[_Write] = []
    for name in sorted(WATCHED):
        for match in re.finditer(rf"\.\s*{name}\b", body):
            pos = offset + match.start()
            after = body[match.end() :]
            before = body[: match.start()]
            assigned = _ASSIGN_AFTER.match(after) is not None or re.search(
                r"(?:\+\+|--)\s*\w+\s*$", before
            )
            # A bare argument can be an `out` parameter, which writes the field.
            argument = False
            if re.search(r"[(,]\s*\w+\s*$", before) and re.match(r"\s*[,)]", after):
                call = _enclosing_call(before)
                argument = call is not None and call not in _KEYWORD_CALLS
            if not (assigned or argument):
                continue
            root = _ROOT_BEFORE.search(before)
            if root is None:
                raise AssertionError(
                    f"{source.path}:{source.line(pos)}: .{name} written through a "
                    "target the census cannot type"
                )
            declared = resolve(method, root.group(1))
            if declared is None:
                raise AssertionError(
                    f"{source.path}:{source.line(pos)}: .{name} written on "
                    f"{root.group(1)!r}, which the census cannot type"
                )
            if "MCPResult" in declared:
                found.append(_Write(method, pos, name, source.line(pos)))
    return found


def _bridge_sources() -> dict[str, str]:
    return {name: (MISSION / name).read_text(encoding="utf-8") for name in BRIDGES}


def _mcp_result_members(messages: str) -> dict[str, str]:
    """name -> declared type of every MCPResult member, `ref` kept in the type."""
    text = clean(messages)
    start = text.index("class MCPResult")
    body = text[text.index("{", start) + 1 : text.index("\n};", start)]
    members: dict[str, str] = {}
    for statement in body.split(";"):
        words = statement.split()
        if len(words) >= 2:
            members[words[-1]] = " ".join(words[:-1])
    return members


def _mutant(sources: dict[str, str], name: str, anchor: str, replacement: str) -> dict[str, str]:
    """A copy of `sources` with `anchor` replaced once in `name`; the anchor must exist."""
    text = sources[name]
    if text.count(anchor) != 1:
        raise AssertionError(
            f"mutation anchor occurs {text.count(anchor)} times in {name}; "
            f"update the anchor: {anchor!r}"
        )
    mutated = dict(sources)
    mutated[name] = text.replace(anchor, replacement)
    return mutated


class OwnedScalarCensusTest(unittest.TestCase):
    def test_owner_sets_are_exactly_the_commands_that_write_each_field(self) -> None:
        observed = census(_bridge_sources())

        for name, owners in sorted(OWNED_SCALAR_FIELDS.items()):
            with self.subTest(field=name):
                self.assertEqual(
                    observed[name] - owners,
                    frozenset(),
                    f"these commands write {name} but OWNED_SCALAR_FIELDS drops it "
                    "from their result",
                )
                self.assertEqual(
                    owners - observed[name],
                    frozenset(),
                    f"these owners of {name} no longer write it; the prune would keep "
                    "their unassigned default",
                )

    def test_never_filled_fields_have_no_write(self) -> None:
        observed = census(_bridge_sources())

        for name in sorted(NEVER_FILLED_SCALAR_FIELDS):
            with self.subTest(field=name):
                self.assertEqual(
                    observed[name],
                    frozenset(),
                    f"{name} is now written; move it to OWNED_SCALAR_FIELDS with "
                    "these owners",
                )

    def test_owned_and_never_filled_fields_are_scalar_members_of_mcp_result(self) -> None:
        members = _mcp_result_members((MISSION / "MCPMessages.c").read_text(encoding="utf-8"))

        self.assertEqual(set(OWNED_SCALAR_FIELDS) & NEVER_FILLED_SCALAR_FIELDS, set())
        for name in sorted(WATCHED):
            with self.subTest(field=name):
                self.assertIn(name, members)
                self.assertFalse(members[name].startswith("ref "), members[name])
                self.assertNotIn(name, PRUNABLE_FIELDS)

    def test_every_owner_is_a_whitelisted_bridge_command(self) -> None:
        commands = loopback.SERVER_COMMANDS | loopback.CLIENT_COMMANDS
        for name, owners in sorted(OWNED_SCALAR_FIELDS.items()):
            with self.subTest(field=name):
                self.assertTrue(owners)
                self.assertLessEqual(owners, commands)

    def test_only_the_two_bridges_build_or_fill_a_result(self) -> None:
        # The census reads two files. Any other script that names MCPResult
        # could fill it without being read, so that would widen this test first.
        declaring = MISSION / "MCPMessages.c"
        others = [
            path.relative_to(SCRIPTS).as_posix()
            for path in sorted(SCRIPTS.rglob("*.c"))
            if path.name not in BRIDGES
            and path != declaring
            and re.search(r"\bMCPResult\b", clean(path.read_text(encoding="utf-8")))
        ]
        self.assertEqual(others, [])


class OwnedScalarCensusControlTest(unittest.TestCase):
    """Negative controls: each edit a future change could make is seen."""

    def setUp(self) -> None:
        self.sources = _bridge_sources()

    def test_a_verb_that_starts_writing_an_owned_field_is_named(self) -> None:
        mutated = _mutant(
            self.sources,
            "MCPBridge.c",
            "\t\tresult.object_id = objectId;\n\t\tresult.deleted = 0;\n",
            "\t\tresult.object_id = objectId;\n\t\tresult.deleted = 0;\n\t\tresult.gear = 0;\n",
        )
        self.assertEqual(census(mutated)["gear"], {"vehicle_telemetry", "object_delete"})

    def test_a_new_command_routed_to_an_owning_dispatch_is_named(self) -> None:
        mutated = _mutant(
            self.sources,
            "MCPClientBridge.c",
            '\t\telse if (command.cmd == "vehicle_trace")\n',
            '\t\telse if (command.cmd == "vehicle_peek")\n\t\t{\n'
            "\t\t\tpostNow = DispatchVehicleTelemetry(command, result);\n\t\t}\n"
            '\t\telse if (command.cmd == "vehicle_trace")\n',
        )
        observed = census(mutated)
        for name in ("seated", "gear", "speedo_max", "owner_identity"):
            with self.subTest(field=name):
                self.assertIn("vehicle_peek", observed[name])

    def test_a_job_kind_shared_with_another_dispatch_is_named(self) -> None:
        mutated = _mutant(
            self.sources,
            "MCPBridge.c",
            '\t\tjob.kind = "spawn";\n',
            '\t\tjob.kind = "seat";\n',
        )
        self.assertEqual(
            census(mutated)["seated"],
            {"vehicle_enter", "world_spawn", "vehicle_get_in_client", "vehicle_telemetry"},
        )

    def test_a_door_write_outside_its_command_gate_is_named(self) -> None:
        # DispatchActionUse serves three commands; only its nested command.cmd
        # gate keeps the door fields on action_use_door.
        mutated = _mutant(
            self.sources,
            "MCPClientBridge.c",
            '\t\t\tif (command.cmd == "action_use_door")\n',
            "\t\t\tif (command.args.door_index >= 0)\n",
        )
        self.assertEqual(
            census(mutated)["door_index"],
            {"action_use", "action_use_door", "action_use_target"},
        )

    def test_a_seat_reply_outside_its_job_kind_gate_fails(self) -> None:
        # PostJobSuccess runs for every finished job; only its job.kind gate
        # ties PostSeatSuccess to the command that queued a seat job.
        mutated = _mutant(
            self.sources,
            "MCPBridge.c",
            '\tprotected void PostJobSuccess(MCPJob job)\n\t{\n\t\tif (job.kind == "seat")\n',
            '\tprotected void PostJobSuccess(MCPJob job)\n\t{\n\t\tif (job.kind != "spawn")\n',
        )
        with self.assertRaisesRegex(AssertionError, "any command reaches"):
            census(mutated)

    def test_a_write_to_a_never_filled_field_is_seen(self) -> None:
        mutated = _mutant(
            self.sources,
            "MCPClientBridge.c",
            "\t\tresult.gear = car.GetGear();\n",
            "\t\tresult.gear = car.GetGear();\n\t\tresult.pos_delta = 1.0;\n",
        )
        self.assertEqual(census(mutated)["pos_delta"], {"vehicle_telemetry"})

    def test_an_owner_that_stops_writing_is_seen(self) -> None:
        mutated = _mutant(
            self.sources,
            "MCPClientBridge.c",
            "\t\tMCPCarDrive.Set(car, throttle, steer, brake, handbrake, GetGame().GetTickTime() + ttl);\n"
            "\t\tresult.engine_on_server = car.EngineIsOn();\n",
            "\t\tMCPCarDrive.Set(car, throttle, steer, brake, handbrake, GetGame().GetTickTime() + ttl);\n",
        )
        self.assertEqual(census(mutated)["engine_on_server"], {"engine_set", "vehicle_telemetry"})

    def test_an_out_argument_counts_as_a_write(self) -> None:
        mutated = _mutant(
            self.sources,
            "MCPClientBridge.c",
            "\t\tresult.engine_on_server = car.EngineIsOn();\n\t\tresult.ok = true;\n\t\treturn true;\n\t}\n\n\tprotected bool DispatchVehicleControl(",
            "\t\tresult.engine_on_server = car.EngineIsOn();\n"
            "\t\tcar.GetNetworkID(result.net_id_low, result.net_id_high);\n"
            "\t\tresult.ok = true;\n\t\treturn true;\n\t}\n\n\tprotected bool DispatchVehicleControl(",
        )
        observed = census(mutated)
        self.assertIn("engine_set", observed["net_id_low"])
        self.assertIn("engine_set", observed["net_id_high"])

    def test_a_write_any_command_can_reach_fails(self) -> None:
        mutated = _mutant(
            self.sources,
            "MCPClientBridge.c",
            "\toverride void MCP_PostJobSuccess(MCPJob job)\n\t{\n",
            "\toverride void MCP_PostJobSuccess(MCPJob job)\n\t{\n"
            "\t\tMCPResult stray = new MCPResult();\n\t\tstray.seated = true;\n",
        )
        with self.assertRaisesRegex(AssertionError, "any command reaches"):
            census(mutated)

    def test_a_chained_or_untyped_target_fails_instead_of_being_guessed(self) -> None:
        for label, statement in (
            ("chained", "\t\tjob.result.seated = true;\n"),
            ("untyped", "\t\tunknownThing.gear = 2;\n"),
        ):
            with self.subTest(target=label):
                mutated = _mutant(
                    self.sources,
                    "MCPBridge.c",
                    "\t\tresult.object_id = objectId;\n\t\tresult.deleted = 0;\n",
                    "\t\tresult.object_id = objectId;\n\t\tresult.deleted = 0;\n" + statement,
                )
                with self.assertRaisesRegex(AssertionError, "cannot type"):
                    census(mutated)

    def test_a_job_kind_from_a_non_literal_fails(self) -> None:
        mutated = _mutant(
            self.sources,
            "MCPBridge.c",
            '\t\tjob.kind = "seat";\n',
            '\t\tstring seatKind = "seat";\n\t\tjob.kind = seatKind;\n',
        )
        with self.assertRaisesRegex(AssertionError, "non-literal"):
            census(mutated)


if __name__ == "__main__":
    unittest.main()
