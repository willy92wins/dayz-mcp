"""Source text for tests that read a module's or a function's source.

fb-20260819-153716-8491: inspect.getsource takes its line numbers from the code
compiled at import and its text from the file as it is on disk now. When
another session rewrites a module while the suite runs, the two disagree and a
test reads some other function's lines. Measured: the source of playbook_run
came back as one line of pipeline_inbox after 32 lines were inserted above it.
That red belongs to nobody, and the next run is green.

source_of() reads each file once per process, compiles those bytes the way the
import did, and answers every later request for that file from the snapshot.
It raises SourceChangedMidSuite, whose message starts with
source_changed_mid_suite and names the file, instead of answering when:

- the file on disk no longer has the snapshot's bytes; or
- a function it is asked about does not start in the snapshot at the line its
  code object gives, or starts there but compiles to other code: bytecode,
  names, local, free or cell variables, argument counts, constants and nested
  functions are compared. For a module, every function the module and its
  classes define is checked the same way, a function a decorator keeps as
  __wrapped__ behind some other callable (functools.lru_cache) included.

The second check is what ties the first read to the code this process
imported. What it cannot see, before the first read, is an edit that compiles
to the same code (comments, blank lines or layout inside a body), or one to
what runs outside a function's own body: a module's statements, class
attributes, parameter defaults, decorator arguments. A module keeps no code
object once imported, so a module asked about is checked through its
functions only.

The warning filters are never touched: every thread shares them, and
catch_warnings() would change them under all of them. The compile therefore
shows a file's compile-time warnings again, as its import did, and a filter
set to "error" (python -W error, say) turns such a warning into a
SyntaxError. While a filter like that covers SyntaxWarning or
DeprecationWarning, a file that fails to compile keeps the byte check alone.
After the first read every byte counts. Modules and functions only; anything
else is a TypeError.
"""

from __future__ import annotations

import hashlib
import inspect
import io
import os
import threading
import tokenize
import warnings
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
    # False when a warning filter set to "error" may have stopped the compile:
    # that file gets the byte check only
    compiled: bool
    # (qualname, first line) -> every code object compiled from the snapshot there
    code: dict[tuple[str, int], tuple[CodeType, ...]]
    # qualname -> every line a code object of that name starts on
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
        _require_same_code(snapshot, function.__code__)
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
            module_code = _compile_like_the_import(data, path)
        except (SyntaxError, UnicodeDecodeError, ValueError) as error:
            # The module compiled when it was imported, so this is a later text.
            raise SourceChangedMidSuite(
                f"{SOURCE_CHANGED_MID_SUITE}: {path} no longer compiles as the "
                f"module this process imported ({error})"
            ) from error
        if module_code is None:
            code, starts = {}, {}
        else:
            code, starts = _compiled_definitions(module_code)
        snapshot = _Snapshot(
            path=path,
            key=key,
            sha256=hashlib.sha256(data).hexdigest(),
            lines=tuple(lines),
            compiled=module_code is not None,
            code=code,
            starts=starts,
        )
        _SNAPSHOTS[key] = snapshot
        return snapshot


def _decoded_lines(data: bytes) -> list[str]:
    """The lines linecache, and so inspect, reads from these bytes."""
    encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    lines = io.TextIOWrapper(io.BytesIO(data), encoding, line_buffering=True).readlines()
    if not lines:
        lines = ["\n"]  # linecache on 3.14; on 3.11 inspect raises for an empty file
    elif not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return lines


def _compile_like_the_import(data: bytes, path: str) -> CodeType | None:
    """compile() as SourceLoader.source_to_code runs it for an import: the raw
    bytes, dont_inherit, the interpreter's optimisation level.

    None when a warning filter set to "error" may be what stopped it: compile
    hands such a warning back as a SyntaxError (or, raised as is, as the
    warning), and that says nothing about the file having changed.
    """
    try:
        return compile(data, path, "exec", dont_inherit=True, optimize=-1)
    except Warning:
        return None
    except SyntaxError:
        if _warnings_can_stop_a_compile():
            return None
        raise


def _warnings_can_stop_a_compile() -> bool:
    """True when a filter set to "error" covers what compile() warns with:
    SyntaxWarning, or DeprecationWarning on 3.11. Order and patterns are not
    weighed, so the answer leans to the byte-only fallback. Read only."""
    current = getattr(warnings, "_get_filters", None)  # 3.14, context-aware
    filters = list(current() if current is not None else warnings.filters)
    return any(
        action == "error"
        and isinstance(category, type)
        and (
            issubclass(SyntaxWarning, category)
            or issubclass(DeprecationWarning, category)
        )
        for action, _message, category, _module, _line in filters
    )


def _compiled_definitions(
    module_code: CodeType,
) -> tuple[dict[tuple[str, int], tuple[CodeType, ...]], dict[str, frozenset[int]]]:
    """Every code object nested in the module's, by qualname and first line.

    The compiler names them exactly as the imported code objects are named: a
    decorated def starts at its first decorator, a method is Class.method, a
    closure is outer.<locals>.inner.
    """
    code: dict[tuple[str, int], list[CodeType]] = {}
    starts: dict[str, set[int]] = {}
    pending = [module_code]
    while pending:
        current = pending.pop()
        for constant in current.co_consts:
            if isinstance(constant, CodeType):
                place = (constant.co_qualname, constant.co_firstlineno)
                code.setdefault(place, []).append(constant)
                starts.setdefault(constant.co_qualname, set()).add(constant.co_firstlineno)
                pending.append(constant)
    return (
        {place: tuple(found) for place, found in code.items()},
        {qualname: frozenset(lines) for qualname, lines in starts.items()},
    )


def _functions_defined_in(module: ModuleType) -> list[FunctionType]:
    """Every function in the module's namespace and in the classes it defines.

    Whatever keeps a function as __wrapped__ is followed to it, the way
    inspect.unwrap follows it, callables that are not functions included
    (functools.lru_cache). The attribute is read with getattr_static, so no
    __getattr__ of a module, a proxy or a mock runs.
    """
    found: list[FunctionType] = []
    seen: set[int] = set()
    pending: list[object] = list(vars(module).values())
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        try:
            wrapped = inspect.getattr_static(value, "__wrapped__", None)
        except Exception:
            wrapped = None
        if wrapped is not None:
            pending.append(wrapped)
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


def _require_same_code(snapshot: _Snapshot, code: CodeType) -> None:
    if not snapshot.compiled:
        return  # an "error" warning filter stopped the compile: bytes only
    if _key(code.co_filename) != snapshot.key:
        return  # defined in another file; this snapshot says nothing about it
    compiled = snapshot.code.get((code.co_qualname, code.co_firstlineno), ())
    if not compiled:
        starts = snapshot.starts.get(code.co_qualname, frozenset())
        where = ", ".join(str(line) for line in sorted(starts)) or "no line"
        raise SourceChangedMidSuite(
            f"{SOURCE_CHANGED_MID_SUITE}: {snapshot.path} does not match the code "
            f"this process imported: {code.co_qualname} starts at line "
            f"{code.co_firstlineno} in memory and at {where} in the file; "
            f"rerun on a tree nobody is writing to"
        )
    if not any(_same_code(code, candidate) for candidate in compiled):
        raise SourceChangedMidSuite(
            f"{SOURCE_CHANGED_MID_SUITE}: {snapshot.path} does not match the code "
            f"this process imported: {code.co_qualname} at line "
            f"{code.co_firstlineno} compiles to other code from the file; "
            f"rerun on a tree nobody is writing to"
        )


def _same_code(imported: CodeType, compiled: CodeType) -> bool:
    """Same code as far as the source decides it; line tables are left out.

    co_code is the unspecialised, uninstrumented bytecode on 3.11 and later,
    so a function that already ran hot, or under a tracer, still compares.
    """
    if (
        imported.co_name != compiled.co_name
        or imported.co_qualname != compiled.co_qualname
        or imported.co_code != compiled.co_code
        or imported.co_names != compiled.co_names
        or imported.co_varnames != compiled.co_varnames
        or imported.co_freevars != compiled.co_freevars
        or imported.co_cellvars != compiled.co_cellvars
        or imported.co_argcount != compiled.co_argcount
        or imported.co_posonlyargcount != compiled.co_posonlyargcount
        or imported.co_kwonlyargcount != compiled.co_kwonlyargcount
        or len(imported.co_consts) != len(compiled.co_consts)
    ):
        return False
    return all(
        _same_constant(left, right)
        for left, right in zip(imported.co_consts, compiled.co_consts)
    )


def _same_constant(left: object, right: object) -> bool:
    if isinstance(left, CodeType) or isinstance(right, CodeType):
        return (
            isinstance(left, CodeType)
            and isinstance(right, CodeType)
            and _same_code(left, right)
        )
    if type(left) is not type(right):
        return False  # 1 == True == 1.0, but they are three constants
    if isinstance(left, (float, complex)):
        return repr(left) == repr(right)  # nan != nan, and -0.0 == 0.0
    if isinstance(left, tuple):
        return len(left) == len(right) and all(
            _same_constant(a, b) for a, b in zip(left, right)
        )
    return left == right
