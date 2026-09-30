"""Source text for tests that read a module's or a function's source.

fb-20260819-153716-8491: inspect.getsource takes its line numbers from the code
compiled at import and its text from the file as it is on disk now. When
another session rewrites a module while the suite runs, the two disagree and a
test reads some other function's lines. Measured: the source of playbook_run
came back as one line of pipeline_inbox after 32 lines were inserted above it.
That red belongs to nobody, and the next run is green.

source_of() reads each file once per process and answers every later request
for it from that snapshot. It raises SourceChangedMidSuite, whose message
starts with source_changed_mid_suite and names the file, instead of answering
when:

- the file on disk no longer has the snapshot's bytes; or
- a function it is asked about (for a module, every function the module and
  its classes define) does not start in the snapshot at the line its code
  object gives. That is how a rewrite between the import and the first read
  shows up, which comparing bytes alone cannot see.

A rewrite that moves no function is not caught before the first read, and the
text served is then the rewritten one. Modules and functions only; anything
else is a TypeError.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import io
import os
import threading
import tokenize
from dataclasses import dataclass
from types import CodeType, FunctionType, ModuleType

SOURCE_CHANGED_MID_SUITE = "source_changed_mid_suite"


class SourceChangedMidSuite(AssertionError):
    """A source file changed on disk while this test process was reading it."""


@dataclass(frozen=True, eq=False)
class _Snapshot:
    path: str
    key: str
    sha256: str
    lines: tuple[str, ...]
    # qualname -> every line a def or lambda of that name starts on
    starts: dict[str, frozenset[int]]


_SNAPSHOTS: dict[str, _Snapshot] = {}
_SNAPSHOTS_LOCK = threading.Lock()


def source_of(obj: object) -> str:
    """What inspect.getsource(obj) returns, read from this process's snapshot."""
    target = inspect.unwrap(obj)
    if inspect.ismethod(target):
        target = target.__func__
    if inspect.ismodule(target):
        functions = _functions_defined_in(target)
    elif inspect.isfunction(target):
        functions = [target]
    else:
        raise TypeError(
            f"source_of reads a module or a function, not {type(obj).__name__}"
        )
    path = inspect.getsourcefile(target) or _known_path(inspect.getfile(target))
    if not path:
        raise OSError(f"source code not available for {obj!r}")
    snapshot = _snapshot(path)
    _require_same_bytes(snapshot)
    for function in functions:
        _require_same_start(snapshot, function.__code__)
    if inspect.ismodule(target):
        return "".join(snapshot.lines)
    first = target.__code__.co_firstlineno - 1
    return "".join(inspect.getblock(list(snapshot.lines[first:])))


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _known_path(filename: str) -> str | None:
    """The file of a snapshot already taken, once inspect no longer finds it on disk."""
    with _SNAPSHOTS_LOCK:
        known = _SNAPSHOTS.get(_key(filename))
    return None if known is None else known.path


def _snapshot(path: str) -> _Snapshot:
    key = _key(path)
    with _SNAPSHOTS_LOCK:
        cached = _SNAPSHOTS.get(key)
        if cached is not None:
            return cached
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError as error:
            raise OSError(f"could not read the source of {path}: {error}") from error
        try:
            lines = _decoded_lines(data)
            tree = ast.parse("".join(lines), filename=path)
        except (SyntaxError, UnicodeDecodeError, ValueError) as error:
            # The module parsed when it was imported, so this is a later text.
            raise SourceChangedMidSuite(
                f"{SOURCE_CHANGED_MID_SUITE}: {path} no longer parses as the "
                f"module this process imported ({error})"
            ) from error
        snapshot = _Snapshot(
            path=path,
            key=key,
            sha256=hashlib.sha256(data).hexdigest(),
            lines=tuple(lines),
            starts=_definition_starts(tree),
        )
        _SNAPSHOTS[key] = snapshot
        return snapshot


def _decoded_lines(data: bytes) -> list[str]:
    """The lines linecache, and so inspect, reads from these bytes."""
    encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    lines = io.TextIOWrapper(io.BytesIO(data), encoding, line_buffering=True).readlines()
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return lines


def _definition_starts(tree: ast.Module) -> dict[str, frozenset[int]]:
    """Where each def and lambda starts, by the qualname its code object carries.

    A decorated def starts at its first decorator: that is the line the
    compiler gives co_firstlineno. Decorators, defaults and annotations belong
    to the enclosing scope; only the body is inside the def.
    """
    starts: dict[str, set[int]] = {}
    pending: list[tuple[ast.AST, str]] = [(tree, "")]
    while pending:
        node, prefix = pending.pop()
        inner: str | None = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = prefix + node.name
            first = node.decorator_list[0].lineno if node.decorator_list else node.lineno
            starts.setdefault(qualname, set()).add(first)
            inner = qualname + ".<locals>."
        elif isinstance(node, ast.ClassDef):
            inner = prefix + node.name + "."
        elif isinstance(node, ast.Lambda):
            starts.setdefault(prefix + "<lambda>", set()).add(node.lineno)
            inner = prefix + "<lambda>.<locals>."
        for field, value in ast.iter_fields(node):
            scope = inner if inner is not None and field == "body" else prefix
            for child in value if isinstance(value, list) else (value,):
                if isinstance(child, ast.AST):
                    pending.append((child, scope))
    return {qualname: frozenset(lines) for qualname, lines in starts.items()}


def _functions_defined_in(module: ModuleType) -> list[FunctionType]:
    """Every function in the module's namespace and in the classes it defines."""
    found: list[FunctionType] = []
    seen: set[int] = set()
    pending: list[object] = list(vars(module).values())
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, (staticmethod, classmethod)):
            pending.append(value.__func__)
        elif isinstance(value, property):
            pending.extend(
                accessor
                for accessor in (value.fget, value.fset, value.fdel)
                if accessor is not None
            )
        elif inspect.isfunction(value):
            found.append(value)
        elif inspect.isclass(value) and value.__module__ == module.__name__:
            pending.extend(vars(value).values())
    return found


def _require_same_bytes(snapshot: _Snapshot) -> None:
    try:
        with open(snapshot.path, "rb") as handle:
            data = handle.read()
    except OSError as error:
        raise SourceChangedMidSuite(
            f"{SOURCE_CHANGED_MID_SUITE}: {snapshot.path} can no longer be read "
            f"({error})"
        ) from error
    current = hashlib.sha256(data).hexdigest()
    if current != snapshot.sha256:
        raise SourceChangedMidSuite(
            f"{SOURCE_CHANGED_MID_SUITE}: {snapshot.path} changed on disk after "
            f"this test process first read it (sha256 {snapshot.sha256[:12]} -> "
            f"{current[:12]}); rerun on a tree nobody is writing to"
        )


def _require_same_start(snapshot: _Snapshot, code: CodeType) -> None:
    if _key(code.co_filename) != snapshot.key:
        return  # defined in another file; this snapshot says nothing about it
    starts = snapshot.starts.get(code.co_qualname, frozenset())
    if code.co_firstlineno not in starts:
        where = ", ".join(str(line) for line in sorted(starts)) or "no line"
        raise SourceChangedMidSuite(
            f"{SOURCE_CHANGED_MID_SUITE}: {snapshot.path} does not match the code "
            f"this process imported: {code.co_qualname} starts at line "
            f"{code.co_firstlineno} in memory and at {where} in the file; "
            f"rerun on a tree nobody is writing to"
        )
