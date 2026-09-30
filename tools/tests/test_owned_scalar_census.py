"""Owned-scalar census: which commands' dispatch writes each owned MCPResult scalar.

result_prune.OWNED_SCALAR_FIELDS keeps an owned scalar only for its owner
commands (fb-20260823-130809-a412) and NEVER_FILLED_SCALAR_FIELDS drops a scalar
no command assigns (fb-20260823-141958-dde3). Both are hand-kept, so this test
derives them again from the Enforce sources: MCPBridge.c and MCPClientBridge.c,
where commands are dispatched, and MCPMessages.c, where MCPResult and the other
message classes are declared. A command that writes an owned field without being
one of its owners fails here, so a new verb cannot lose its answer to the prune
unnoticed. A listed owner that no longer writes the field fails too, so the prune
does not keep a stale 0 for it. A never-filled field must have no write at all.

A write is an assignment to a watched field of an MCPResult. In a bridge it is an
assignment on a variable of MCPResult (or of a class derived from it). In
MCPMessages.c a method writes a field when it assigns it on such a variable, when
it belongs to MCPResult (or to a class derived from it) and assigns the field on
itself (`gear = ...`, `this.gear = ...`), or when it calls a method that does. A
bridge call to such a method writes those fields where the call is: an instance
call on a variable of a message class, a static call on a message class, or
`new` of a message class whose constructor writes. Review R1 F1: a new verb
calling result.SetGear(2), with SetGear declared in MCPResult, went unseen while
MCPMessages.c was not read.

This is attribution, not execution. Comments are blanked, preprocessor lines
ignored and string contents masked, then every method of every class is found
by brace depth. A write, a call or a `job.kind = "..."` is attributed through
the `if` blocks around it:

- `if (command.cmd == "x")`, with `command` an MCPCommand, narrows to {x};
- `if (job.kind == "k")`, with `job` an MCPJob, narrows to the commands whose
  dispatch reaches `job.kind = "k"`;
- a bridge method called only from its own class gets the union of its call
  sites. One that is public, `override`, referenced without a call, called on
  another object or named in a string literal can be reached from outside, so
  it starts with every command.

Nothing else narrows: an `else`, a loop, a negated test or a gate mixed with
another test under `||`. Each of these simplifications can only widen the set of
commands a write is attributed to, so the census can report a writer that does
not exist but cannot miss one that does. What it cannot place fails the test
instead of being guessed: a write on a variable it cannot type or through a
chain, a write that no command or every command reaches, a `job.kind` set from a
non-literal, a method called on a message-class variable when that class does
not declare it, and a writing method called on a receiver it cannot type. A field
passed as a bare call argument counts as a write, because an `out` parameter
writes it.
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
MESSAGES = "MCPMessages.c"
RESULT_CLASS = "MCPResult"
WATCHED = frozenset(OWNED_SCALAR_FIELDS) | NEVER_FILLED_SCALAR_FIELDS

# "Any command": a method reachable from outside its class, before narrowing.
TOP = None

_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
_PREPROCESSOR = re.compile(r"(?m)^[ \t]*#[^\n]*")
_CLASS_HEADER = re.compile(r"^(?:modded\s+)?class\s+(\w+)\b(?:\s*(?::|extends)\s*(\w+))?")
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
_NEW_CALL = re.compile(r"(?<![\w.])new\s+(\w+)\s*\(")
_DOTTED_CALL = re.compile(r"\.\s*(\w+)\s*\(")
_BARE_NAME = re.compile(r"(?<![\w.])(\w+)\b(?!\s*\()")
_THIS_NAME = re.compile(r"\bthis\s*\.\s*(\w+)\b(?!\s*\()")
_LITERAL_NAME = re.compile(r'"(\w+)"')
_ASSIGN_AFTER = re.compile(r"\s*(?:(?:[-+*/%&|^]|<<|>>)?=(?!=)|\+\+|--)")
_ROOT_BEFORE = re.compile(r"(?<![\w.)\]])(\w+)\s*$")
_KIND_WRITE = re.compile(r"(?<![\w.])(\w+)\s*\.\s*kind\s*=(?!=)\s*")
_KEYWORD_CALLS = frozenset({"if", "while", "for", "foreach", "switch", "return"})
_SELF = frozenset({"this", "super"})


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


@dataclass
class _Parsed:
    methods: list[_Method]
    members: dict[str, dict[str, set[str]]]  # class -> member -> declared types
    bases: dict[str, str | None]  # every class of the file -> its base, if any
    class_text: dict[str, str]  # class -> its body with every method blanked


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


def _parse(source: _Source) -> _Parsed:
    """Every class of the file: its methods, members, base and class-level text."""
    blocks = _blocks(source)
    classes: list[tuple[str, int, int]] = []
    bases: dict[str, str | None] = {}
    methods: list[_Method] = []
    for open_at, close_at, depth, header_at in blocks:
        header = " ".join(source.masked[header_at:open_at].split())
        if depth == 0:
            klass = _CLASS_HEADER.match(header)
            if klass:
                classes.append((klass.group(1), open_at, close_at))
                bases[klass.group(1)] = klass.group(2)
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
    class_text: dict[str, str] = {}
    for name, start, end in classes:
        # Class level only: every method, signature included, is blanked.
        chars = list(source.masked[start + 1 : end])
        for method in methods:
            if method.klass == name and start < method.open < end:
                for index in range(method.header - start - 1, method.close - start):
                    chars[index] = " "
        class_text[name] = "".join(chars)
        members[name] = {}
        _declare(members[name], class_text[name])
    return _Parsed(methods, members, bases, class_text)


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


def _where(source: _Source, method: _Method, pos: int) -> str:
    return f"{source.path}:{source.line(pos)} ({method.klass}.{method.name})"


def _lookup(
    classes: dict[str, dict[str, _Method]],
    bases: dict[str, str | None],
    klass: str | None,
    name: str,
) -> _Method | None:
    """Method `name` of `klass`, or of the nearest base that declares it."""
    seen: set[str] = set()
    while klass is not None and klass not in seen:
        seen.add(klass)
        method = classes.get(klass, {}).get(name)
        if method is not None:
            return method
        klass = bases.get(klass)
    return None


def _constructors(
    classes: dict[str, dict[str, _Method]], bases: dict[str, str | None], klass: str
) -> list[_Method]:
    """The constructors `new klass(...)` runs: its own and those of its bases."""
    found: list[_Method] = []
    seen: set[str] = set()
    current: str | None = klass
    while current in classes and current not in seen:
        seen.add(current)
        constructor = classes[current].get(current)
        if constructor is not None:
            found.append(constructor)
        current = bases.get(current)
    return found


def _result_types(bases: dict[str, str | None]) -> frozenset[str]:
    """MCPResult and every class whose base chain reaches it."""
    found = {RESULT_CLASS}
    for klass in bases:
        seen: set[str] = set()
        current: str | None = klass
        while current is not None and current not in seen:
            if current == RESULT_CLASS:
                found.add(klass)
                break
            seen.add(current)
            current = bases.get(current)
    return frozenset(found)


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


def _is_write(before: str, after: str, root_pattern: str) -> bool:
    """True when the name that ends `before` (matched by root_pattern) is written."""
    if _ASSIGN_AFTER.match(after) is not None:
        return True
    if re.search(rf"(?:\+\+|--)\s*{root_pattern}$", before):
        return True
    # A bare argument can be an `out` parameter, which writes the field.
    if re.search(rf"[(,]\s*{root_pattern}$", before) and re.match(r"\s*[,)]", after):
        call = _enclosing_call(before)
        return call is not None and call not in _KEYWORD_CALLS
    return False


def _writes_in(
    source: _Source, method: _Method, resolve, result_types: frozenset[str]
) -> list[_Write]:
    """Writes of watched fields on an MCPResult variable (or `this` in a result class)."""
    body = source.masked[method.open + 1 : method.close]
    offset = method.open + 1
    found: list[_Write] = []
    for name in sorted(WATCHED):
        for match in re.finditer(rf"\.\s*{name}\b", body):
            pos = offset + match.start()
            before = body[: match.start()]
            if not _is_write(before, body[match.end() :], r"\w+\s*"):
                continue
            root = _ROOT_BEFORE.search(before)
            if root is None:
                raise AssertionError(
                    f"{source.path}:{source.line(pos)}: .{name} written through a "
                    "target the census cannot type"
                )
            if root.group(1) in _SELF:
                # this.x names the member of the class the method belongs to.
                if method.klass in result_types:
                    found.append(_Write(method, pos, name, source.line(pos)))
                continue
            declared = resolve(method, root.group(1))
            if declared is None:
                raise AssertionError(
                    f"{source.path}:{source.line(pos)}: .{name} written on "
                    f"{root.group(1)!r}, which the census cannot type"
                )
            if declared & result_types:
                found.append(_Write(method, pos, name, source.line(pos)))
    return found


def _self_writes(source: _Source, method: _Method) -> list[_Write]:
    """Bare writes of watched fields in a method of a result class: fields of `this`."""
    body = source.masked[method.open + 1 : method.close]
    offset = method.open + 1
    found: list[_Write] = []
    for name in sorted(WATCHED):
        for match in re.finditer(rf"(?<![\w.]){name}\b", body):
            if _is_write(body[: match.start()], body[match.end() :], ""):
                pos = offset + match.start()
                found.append(_Write(method, pos, name, source.line(pos)))
    return found


def _message_calls(
    source: _Source,
    method: _Method,
    resolve,
    classes: dict[str, dict[str, _Method]],
    bases: dict[str, str | None],
) -> list[tuple[_Method, int]]:
    """Message-class methods `method` calls, each with the position of the call.

    `new T(...)` runs T's constructors; `v.X(...)` calls X of the message classes
    `v` is declared as; `T.X(...)` calls X of message class T; `X(...)`,
    `this.X(...)` and `super.X(...)` call a method of the caller's own class or a
    base, which is a message method inside a message class (or a class derived
    from one). A method a message class does not declare, called on it, cannot
    be resolved and fails. A chained receiver is left to _unresolved_writer_calls.
    """
    body = source.masked[method.open + 1 : method.close]
    offset = method.open + 1
    inside = method.klass in classes
    found: list[tuple[_Method, int]] = []
    for match in _NEW_CALL.finditer(body):
        for constructor in _constructors(classes, bases, match.group(1)):
            found.append((constructor, offset + match.start()))
    for match in _CALL.finditer(body):
        callee = _lookup(classes, bases, method.klass, match.group(1))
        if callee is not None:
            found.append((callee, offset + match.start()))
    for match in _QUALIFIED_CALL.finditer(body):
        start = match.start()
        if start > 0 and (body[start - 1] in ".)]" or body[start - 1].isalnum() or body[start - 1] == "_"):
            continue
        qualifier, name = match.group(1), match.group(2)
        pos = offset + start
        if qualifier in _SELF:
            owner = method.klass if qualifier == "this" else bases.get(method.klass)
            callee = _lookup(classes, bases, owner, name)
            if callee is not None:
                found.append((callee, pos))
            elif inside:
                raise AssertionError(
                    f"{_where(source, method, pos)}: calls {qualifier}.{name}, which "
                    f"{method.klass} and its bases do not declare in {MESSAGES}; the "
                    "census cannot resolve the call"
                )
            # Otherwise a bridge's own method: the bridge call graph has it.
            continue
        if qualifier in classes:
            targets = [qualifier]
        else:
            targets = sorted((resolve(method, qualifier) or set()) & set(classes))
        if not targets:
            continue
        callees = [
            callee
            for callee in (_lookup(classes, bases, target, name) for target in targets)
            if callee is not None
        ]
        if not callees:
            raise AssertionError(
                f"{_where(source, method, pos)}: calls {qualifier}.{name}, which "
                f"{'/'.join(targets)} does not declare in {MESSAGES}; the census "
                "cannot resolve the call"
            )
        found.extend((callee, pos) for callee in callees)
    return found


def _unresolved_writer_calls(
    source: _Source,
    method: _Method,
    resolve,
    classes: dict[str, dict[str, _Method]],
    bases: dict[str, str | None],
    writer_names: frozenset[str],
) -> None:
    """Fail on a writing method called through a receiver the census cannot type,
    or referenced without a call (CallLater(result.SetGear, ...)): when that runs,
    and for which command, cannot be placed."""
    body = source.masked[method.open + 1 : method.close]
    offset = method.open + 1
    for match in re.finditer(r"\.\s*(\w+)\b(?!\s*\()", body):
        if match.group(1) in writer_names:
            raise AssertionError(
                f"{_where(source, method, offset + match.start())}: .{match.group(1)}, "
                "which writes a watched field, is referenced without a call; the "
                "census cannot resolve when it runs"
            )
    if method.klass in classes:
        for match in _BARE_NAME.finditer(body):
            name = match.group(1)
            if name in writer_names and _lookup(classes, bases, method.klass, name) is not None:
                raise AssertionError(
                    f"{_where(source, method, offset + match.start())}: {name}, which "
                    "writes a watched field, is referenced without a call; the census "
                    "cannot resolve when it runs"
                )
    for match in _DOTTED_CALL.finditer(body):
        name = match.group(1)
        if name not in writer_names:
            continue
        pos = offset + match.start()
        root = _ROOT_BEFORE.search(body[: match.start()])
        if root is None:
            raise AssertionError(
                f"{_where(source, method, pos)}: calls .{name}, which writes a watched "
                "field, through a chain; the census cannot resolve the receiver"
            )
        qualifier = root.group(1)
        if qualifier in _SELF or qualifier in classes:
            continue
        if resolve(method, qualifier) is None:
            raise AssertionError(
                f"{_where(source, method, pos)}: calls {qualifier}.{name}, which "
                f"writes a watched field, on a name the census cannot resolve"
            )


def _message_writers(
    source: _Source, parsed: _Parsed, result_types: frozenset[str]
) -> tuple[dict[str, dict[str, _Method]], dict[int, frozenset[str]], frozenset[str]]:
    """Message classes by name, the watched fields each of their methods writes on an
    MCPResult (directly or through the methods it calls), and the writing names."""
    classes: dict[str, dict[str, _Method]] = {name: {} for name in parsed.bases}
    for method in parsed.methods:
        classes.setdefault(method.klass, {})[method.name] = method

    def resolve(method: _Method, name: str) -> set[str] | None:
        return _resolve(method, parsed.members.get(method.klass, {}), name)

    writes: dict[int, frozenset[str]] = {}
    callees: dict[int, list[_Method]] = {}
    for method in parsed.methods:
        fields = {write.field for write in _writes_in(source, method, resolve, result_types)}
        if method.klass in result_types:
            fields |= {write.field for write in _self_writes(source, method)}
        writes[id(method)] = frozenset(fields)
        callees[id(method)] = [
            callee
            for callee, _pos in _message_calls(source, method, resolve, classes, parsed.bases)
        ]
    changed = True
    while changed:
        changed = False
        for method in parsed.methods:
            reached = writes[id(method)].union(*(writes[id(c)] for c in callees[id(method)]))
            if reached != writes[id(method)]:
                writes[id(method)] = reached
                changed = True
    writer_names = frozenset(method.name for method in parsed.methods if writes[id(method)])
    for method in parsed.methods:
        _unresolved_writer_calls(source, method, resolve, classes, parsed.bases, writer_names)
    return classes, writes, writer_names


def census(sources: dict[str, str]) -> dict[str, frozenset[str]]:
    """Watched field -> the commands whose dispatch writes it.

    `sources` maps a file name to its text: the bridges and MCPMessages.c.
    Raises AssertionError for a write, a call or a job kind it cannot place.
    """
    if MESSAGES not in sources:
        raise AssertionError(
            f"the census reads {MESSAGES}, where MCPResult and its methods are declared"
        )
    messages = _prepare(MESSAGES, sources[MESSAGES])
    parsed_messages = _parse(messages)
    parsed_bridges = [
        (source, _parse(source))
        for source in (_prepare(path, raw) for path, raw in sources.items() if path != MESSAGES)
    ]
    bases = dict(parsed_messages.bases)
    for _source, parsed in parsed_bridges:
        bases.update(parsed.bases)
    result_types = _result_types(bases)
    classes, message_writes, writer_names = _message_writers(
        messages, parsed_messages, result_types
    )

    methods = [method for _source, parsed in parsed_bridges for method in parsed.methods]
    members = {
        (source.path, klass): declared
        for source, parsed in parsed_bridges
        for klass, declared in parsed.members.items()
    }

    def resolve(method: _Method, name: str) -> set[str] | None:
        return _resolve(method, members.get((method.path, method.klass), {}), name)

    by_name: dict[tuple[str, str], dict[str, _Method]] = {}
    for method in methods:
        by_name.setdefault((method.path, method.klass), {})[method.name] = method

    entries: set[int] = set()
    calls: dict[int, list[tuple[_Method, int]]] = {}
    kind_sites: dict[str, list[tuple[_Method, int]]] = {}
    writes: list[_Write] = []
    for source, _parsed in parsed_bridges:
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
                writes.extend(_writes_in(source, method, resolve, result_types))
                if method.klass in result_types:
                    writes.extend(_self_writes(source, method))
                for callee, pos in _message_calls(source, method, resolve, classes, bases):
                    for name in sorted(message_writes[id(callee)]):
                        writes.append(_Write(method, pos, name, source.line(pos)))
                _unresolved_writer_calls(source, method, resolve, classes, bases, writer_names)

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
    unplaced: list[str] = []
    for write in writes:
        commands = _narrow(reach[id(write.method)], _atoms_at(write.method, write.pos), kinds)
        where = f"{write.method.path}:{write.line} ({write.method.klass}.{write.method.name})"
        if commands is TOP:
            unplaced.append(f"{where}: .{write.field} is written on a path any command reaches")
        elif not commands:
            unplaced.append(f"{where}: .{write.field} is written where the census finds no command")
        else:
            observed[write.field] = observed[write.field] | commands
    if unplaced:
        raise AssertionError("; ".join(sorted(set(unplaced))))
    return observed


def _owner_problems(observed: dict[str, frozenset[str]]) -> list[str]:
    """Where OWNED_SCALAR_FIELDS and the Enforce writers disagree."""
    problems: list[str] = []
    for name, owners in sorted(OWNED_SCALAR_FIELDS.items()):
        unlisted = sorted(observed[name] - owners)
        stale = sorted(owners - observed[name])
        if unlisted:
            problems.append(
                f"{name}: {unlisted} write it, but OWNED_SCALAR_FIELDS drops it from their result"
            )
        if stale:
            problems.append(
                f"{name}: owners {stale} no longer write it; the prune would keep their "
                "unassigned default"
            )
    return problems


def _never_filled_problems(observed: dict[str, frozenset[str]]) -> list[str]:
    return [
        f"{name}: {sorted(observed[name])} write it; move it to OWNED_SCALAR_FIELDS"
        for name in sorted(NEVER_FILLED_SCALAR_FIELDS)
        if observed[name]
    ]


def _census_sources() -> dict[str, str]:
    return {
        name: (MISSION / name).read_text(encoding="utf-8") for name in (*BRIDGES, MESSAGES)
    }


def _mcp_result_declarations(messages: str) -> list[tuple[str, str, str | None]]:
    """(type with `ref` kept, name, initializer or None) of every MCPResult member."""
    parsed = _parse(_prepare(MESSAGES, messages))
    declarations: list[tuple[str, str, str | None]] = []
    for statement in parsed.class_text[RESULT_CLASS].split(";"):
        declaration, equals, initializer = statement.partition("=")
        words = declaration.split()
        if len(words) >= 2:
            declarations.append(
                (" ".join(words[:-1]), words[-1], initializer.strip() if equals else None)
            )
    return declarations


def _mcp_result_members(messages: str) -> dict[str, str]:
    """name -> declared type of every MCPResult member, `ref` kept in the type."""
    return {name: type_name for type_name, name, _init in _mcp_result_declarations(messages)}


def _mcp_result_initializers(messages: str) -> dict[str, str]:
    """name -> initializer of every MCPResult member declared with one."""
    return {
        name: initializer
        for _type, name, initializer in _mcp_result_declarations(messages)
        if initializer is not None
    }


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
        self.assertEqual(_owner_problems(census(_census_sources())), [])

    def test_never_filled_fields_have_no_write(self) -> None:
        self.assertEqual(_never_filled_problems(census(_census_sources())), [])

    def test_owned_and_never_filled_fields_are_scalar_members_of_mcp_result(self) -> None:
        messages = (MISSION / MESSAGES).read_text(encoding="utf-8")
        members = _mcp_result_members(messages)
        # An initializer fills the field for every command, which an owner set
        # cannot express.
        initialized = _mcp_result_initializers(messages)

        self.assertEqual(set(OWNED_SCALAR_FIELDS) & NEVER_FILLED_SCALAR_FIELDS, set())
        for name in sorted(WATCHED):
            with self.subTest(field=name):
                self.assertIn(name, members)
                self.assertFalse(members[name].startswith("ref "), members[name])
                self.assertNotIn(name, PRUNABLE_FIELDS)
                self.assertNotIn(name, initialized)

    def test_every_owner_is_a_whitelisted_bridge_command(self) -> None:
        commands = loopback.SERVER_COMMANDS | loopback.CLIENT_COMMANDS
        for name, owners in sorted(OWNED_SCALAR_FIELDS.items()):
            with self.subTest(field=name):
                self.assertTrue(owners)
                self.assertLessEqual(owners, commands)

    def test_only_the_analyzed_files_name_mcp_result(self) -> None:
        # The census reads the two bridges and MCPMessages.c. Any other script
        # that names MCPResult could build or fill one without being read, so
        # that would widen this test first.
        analyzed = {*BRIDGES, MESSAGES}
        others = [
            path.relative_to(SCRIPTS).as_posix()
            for path in sorted(SCRIPTS.rglob("*.c"))
            if not (path.parent == MISSION and path.name in analyzed)
            and re.search(r"\bMCPResult\b", clean(path.read_text(encoding="utf-8")))
        ]
        self.assertEqual(others, [])

    def test_the_census_does_not_run_without_the_message_classes(self) -> None:
        sources = _census_sources()
        del sources[MESSAGES]
        with self.assertRaisesRegex(AssertionError, MESSAGES):
            census(sources)


class OwnedScalarCensusControlTest(unittest.TestCase):
    """Negative controls: each edit a future change could make is seen."""

    # The dispatch branch the message-class controls add, and where.
    OBJECT_DELETE = '\t\telse if (command.cmd == "object_delete")\n'
    DELETE_BODY = "\t\tresult.object_id = objectId;\n\t\tresult.deleted = 0;\n"
    SET_GEAR = "\tint gear;\n\tvoid SetGear(int value)\n\t{\n\t\tgear = value;\n\t}\n"

    def setUp(self) -> None:
        self.sources = _census_sources()

    def _peek_branch(self, sources: dict[str, str], statement: str) -> dict[str, str]:
        return _mutant(
            sources,
            "MCPBridge.c",
            self.OBJECT_DELETE,
            '\t\telse if (command.cmd == "vehicle_peek")\n\t\t{\n'
            f"\t\t\t{statement}\n\t\t\tresult.ok = true;\n\t\t}}\n" + self.OBJECT_DELETE,
        )

    def test_a_verb_that_starts_writing_an_owned_field_is_named(self) -> None:
        mutated = _mutant(
            self.sources,
            "MCPBridge.c",
            self.DELETE_BODY,
            self.DELETE_BODY + "\t\tresult.gear = 0;\n",
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
                    self.sources, "MCPBridge.c", self.DELETE_BODY, self.DELETE_BODY + statement
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

    # R1 F1: writes that live in MCPMessages.c methods.

    def test_a_result_method_that_writes_a_field_is_attributed_to_its_caller(self) -> None:
        # The review's case: SetGear, declared in MCPResult, assigns gear, and a
        # new verb calls it. The owner test goes red for it.
        mutated = _mutant(self.sources, MESSAGES, "\tint gear;\n", self.SET_GEAR)
        mutated = self._peek_branch(mutated, "result.SetGear(2);")

        observed = census(mutated)

        self.assertEqual(observed["gear"], {"vehicle_telemetry", "vehicle_peek"})
        self.assertEqual(
            _owner_problems(observed),
            [
                "gear: ['vehicle_peek'] write it, but OWNED_SCALAR_FIELDS drops it "
                "from their result"
            ],
        )

    def test_a_writer_reached_through_a_static_helper_and_this_is_named(self) -> None:
        # A static helper of another message class calls a result method that
        # writes this.seated.
        mutated = _mutant(
            self.sources,
            MESSAGES,
            "\tbool seated;\n",
            "\tbool seated;\n\tvoid MarkSeated()\n\t{\n\t\tthis.seated = true;\n\t}\n",
        )
        mutated = _mutant(
            mutated,
            MESSAGES,
            "class MCPJob\n{",
            "class MCPResultFill\n{\n\tstatic void Seat(MCPResult target)\n\t{\n"
            "\t\ttarget.MarkSeated();\n\t}\n};\n\nclass MCPJob\n{",
        )
        mutated = _mutant(
            mutated,
            "MCPBridge.c",
            self.DELETE_BODY,
            self.DELETE_BODY + "\t\tMCPResultFill.Seat(result);\n",
        )
        self.assertIn("object_delete", census(mutated)["seated"])

    def test_a_result_constructor_that_writes_a_field_fails(self) -> None:
        # Every dispatch builds its reply with new MCPResult(): a write in the
        # constructor reaches every command, which no owner set can express.
        mutated = _mutant(
            self.sources,
            MESSAGES,
            "\tint gear;\n",
            "\tint gear;\n\tvoid MCPResult()\n\t{\n\t\tgear = -1;\n\t}\n",
        )
        with self.assertRaisesRegex(AssertionError, "any command reaches"):
            census(mutated)

    def test_a_method_the_result_class_does_not_declare_fails(self) -> None:
        mutated = self._peek_branch(self.sources, "result.SetGear(2);")
        with self.assertRaisesRegex(AssertionError, "cannot resolve the call"):
            census(mutated)

    def test_a_writing_method_on_a_receiver_it_cannot_type_fails(self) -> None:
        with_setter = _mutant(self.sources, MESSAGES, "\tint gear;\n", self.SET_GEAR)
        for label, statement in (
            ("chained", "command.args.reply.SetGear(2);"),
            ("undeclared", "m_Reply.SetGear(2);"),
        ):
            with self.subTest(receiver=label):
                with self.assertRaisesRegex(AssertionError, "cannot resolve"):
                    census(self._peek_branch(with_setter, statement))

    def test_a_writing_method_referenced_without_a_call_fails(self) -> None:
        # CallLater(result.SetGear, ...) runs SetGear later, for no command the
        # census can place. Inside MCPResult, a bare SetGear reference is the same.
        with_setter = _mutant(self.sources, MESSAGES, "\tint gear;\n", self.SET_GEAR)
        deferred = self._peek_branch(
            with_setter,
            "GetGame().GetCallQueue(CALL_CATEGORY_GAMEPLAY).CallLater(result.SetGear, 0, false, 2);",
        )
        inside = _mutant(
            with_setter,
            MESSAGES,
            self.SET_GEAR,
            self.SET_GEAR
            + "\tvoid SetGearLater()\n\t{\n"
            "\t\tGetGame().GetCallQueue(CALL_CATEGORY_GAMEPLAY).CallLater(SetGear, 0, false, 2);\n"
            "\t}\n",
        )
        for label, mutated in (("bridge", deferred), ("result class", inside)):
            with self.subTest(where=label):
                with self.assertRaisesRegex(AssertionError, "referenced without a call"):
                    census(mutated)

    def test_a_same_named_member_of_another_message_class_is_not_a_result_write(self) -> None:
        # MCPJob declares is_owner too. Its own method writing it writes the job,
        # not a reply, so the owner sets do not move.
        mutated = _mutant(
            self.sources,
            MESSAGES,
            "class MCPJob\n{",
            "class MCPJob\n{\n\tvoid ClearOwner()\n\t{\n\t\tis_owner = false;\n\t}\n",
        )
        mutated = _mutant(
            mutated,
            "MCPBridge.c",
            '\t\tjob.kind = "seat";\n',
            '\t\tjob.kind = "seat";\n\t\tjob.ClearOwner();\n',
        )
        self.assertEqual(_owner_problems(census(mutated)), [])

    def test_a_result_class_declared_in_a_bridge_file_fails_closed(self) -> None:
        # A class derived from MCPResult inside a bridge file: its public method
        # can be called from anywhere, so its bare write reaches any command.
        mutated = dict(self.sources)
        mutated["MCPBridge.c"] = (
            self.sources["MCPBridge.c"]
            + "\nclass MCPReplyEx : MCPResult\n{\n\tvoid MarkGear()\n\t{\n\t\tgear = 3;\n\t}\n};\n"
        )
        with self.assertRaisesRegex(AssertionError, "any command reaches"):
            census(mutated)

    def test_an_initialized_watched_field_is_reported(self) -> None:
        messages = _mutant(self.sources, MESSAGES, "\tint gear;\n", "\tint gear = -1;\n")[MESSAGES]
        self.assertEqual(_mcp_result_initializers(messages), {"gear": "-1"})
        self.assertEqual(_mcp_result_initializers(self.sources[MESSAGES]), {})


if __name__ == "__main__":
    unittest.main()
