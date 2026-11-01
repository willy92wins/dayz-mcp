"""Run a restricted translation of real Enforce method bodies in Python.

Shared by the offline contracts of fb-20260822-191204-46b3 (bridge URL rule)
and fb-20260822-191204-1b40 (drive-verb seat gate). This is not an Enforce
compiler or VM: it turns the brace/semicolon structure of one method body into
Python and runs it against fakes the test supplies, so the branching,
comparisons and arithmetic under test are the ones in the .c source. It knows
only the statements those bodies use; anything else raises AssertionError
instead of being guessed (fails closed).

Enforce behaviour kept on purpose:
- strings are byte strings (EnforceString). Length counts bytes (enstring.c:199),
  Substring takes (start, length) (enstring.c:113) and a call outside the string
  fails the test instead of returning something, ToAscii is the first byte
  (enstring.c:76-86) and refuses an empty string;
- a local name is declared once per function (a second declaration does not
  compile in Enforce, and fails here);
- `out` parameters come back as extra return values, in declaration order;
- `!` must apply to a whole operand, and comparisons do not chain: C and Python
  disagree on both, so either shape fails instead of being translated;
- `/` and `%` are refused (integer division differs), and every while loop is
  bounded, so a mutant that stops advancing fails instead of hanging;
- `new T(...)` and `new T<...>(...)` call the namespace's `T` (the element type of
  a generic is dropped), so the test supplies `array`, `map` and the message
  classes (EnforceArray, EnforceMap, fake_class);
- a `vector` is copied on every assignment, as Enforce copies the value type, and
  `v[i]` reads or writes one component;
- the class members a body uses bare are named in `members`, become globals of
  the namespace, and may be assigned.
"""
from __future__ import annotations

import keyword
import re
from typing import Any, Callable, Iterable

_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/')
_STRING_OR_BRACE = re.compile(r'"(?:\\.|[^"\\])*"|[{}]')
_CHUNK_TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|[^"{};]+|[{};]')
_EXPR_TOKENS = re.compile(
    r'\s+|"(?:\\.|[^"\\])*"|&&|\|\||==|!=|<=|>=|!|[A-Za-z_]\w*|\d+\.\d+|\d+|[-+*<>(),.\[\]|&]'
)
_SIGNATURE = re.compile(
    r"^(?:(?:protected|private|static|override)\s+)*"
    r"(?P<ret>[A-Za-z_]\w*)\s+(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^()]*)\)$"
)
_DECLARATION = re.compile(
    r"^(?P<type>[A-Za-z_]\w*(?:\s*<[^=]*>)?)\s+(?P<name>[A-Za-z_]\w*)\s*(?:=(?!=)\s*(?P<init>.+))?$"
)
_ASSIGNMENT = re.compile(
    r"^(?P<target>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?:\[[^\[\]=]+\])?)\s*=(?!=)\s*(?P<value>.+)$"
)
_CALL = re.compile(r"^(?P<name>[A-Za-z_]\w*)\((?P<args>[^()]*)\)$")
_COMPARISONS = frozenset({"<", ">", "<=", ">=", "==", "!="})
_BASE_DEFAULTS = {
    "int": "0",
    "float": "0.0",
    "bool": "False",
    "string": "_S(b'')",
    "vector": "_vector()",
}
_LITERALS = {"true": "True", "false": "False", "null": "None"}
WHILE_LIMIT = 10_000


class EnforceString:
    """Byte string with the vanilla string methods these bodies call."""

    __slots__ = ("data",)
    __hash__ = None  # type: ignore[assignment]

    def __init__(self, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise TypeError("EnforceString holds bytes")
        self.data = data

    @classmethod
    def of(cls, value: str | bytes) -> "EnforceString":
        if isinstance(value, str):
            value = value.encode("utf-8")
        return cls(value)

    def Length(self) -> int:
        return len(self.data)

    def Substring(self, start: int, length: int) -> "EnforceString":
        if (
            type(start) is not int
            or type(length) is not int
            or start < 0
            or length < 0
            or start + length > len(self.data)
        ):
            raise AssertionError(
                f"Substring({start!r}, {length!r}) outside a {len(self.data)}-byte string"
            )
        return type(self)(self.data[start : start + length])

    def ToAscii(self) -> int:
        if not self.data:
            raise AssertionError("ToAscii on an empty string")
        return self.data[0]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, EnforceString):
            raise TypeError(f"string compared with {type(other).__name__}")
        return self.data == other.data

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def __bool__(self) -> bool:
        return bool(self.data)

    def __repr__(self) -> str:
        return f"EnforceString({self.data!r})"

    @property
    def text(self) -> str:
        return self.data.decode("utf-8", "replace")


class SignedAsciiString(EnforceString):
    """Same string, with ToAscii reading the first byte as a signed char."""

    __slots__ = ()

    def ToAscii(self) -> int:
        value = super().ToAscii()
        return value - 256 if value >= 128 else value


def _is_value(item: object) -> bool:
    return isinstance(item, (int, float, EnforceString))


class EnforceArray(list):
    """array<T> with the methods these bodies call. A reference is never falsy:
    `!items` tests for null, not for an empty array. Find compares values for
    numbers and strings and identity for everything else; Get outside the array
    fails the test, as the engine reports an out-of-range index."""

    __hash__ = None  # type: ignore[assignment]

    def __bool__(self) -> bool:
        return True

    def Insert(self, value: object) -> int:
        self.append(value)
        return len(self) - 1

    def Get(self, index: int) -> Any:
        if type(index) is not int or not 0 <= index < len(self):
            raise AssertionError(f"Get({index!r}) outside an array of {len(self)}")
        return list.__getitem__(self, index)

    def Count(self) -> int:
        return len(self)

    def Find(self, value: object) -> int:
        for index, item in enumerate(self):
            if item is value or (_is_value(item) and _is_value(value) and item == value):
                return index
        return -1

    def Clear(self) -> None:
        del self[:]


class EnforceMap:
    """map<K, V> in insertion order. Get of a missing key is null; a second
    Insert of one key fails the test instead of guessing which value wins."""

    __slots__ = ("_pairs",)

    def __init__(self) -> None:
        self._pairs: list[tuple[Any, Any]] = []

    def __bool__(self) -> bool:
        return True

    def _find(self, key: object) -> int:
        for index, (known, _value) in enumerate(self._pairs):
            if known is key or known == key:
                return index
        return -1

    def Get(self, key: object) -> Any:
        index = self._find(key)
        return None if index < 0 else self._pairs[index][1]

    def Insert(self, key: object, value: object) -> bool:
        if self._find(key) >= 0:
            raise AssertionError(f"map key {key!r} inserted twice")
        self._pairs.append((key, value))
        return True

    def Remove(self, key: object) -> None:
        index = self._find(key)
        if index >= 0:
            del self._pairs[index]

    def Contains(self, key: object) -> bool:
        return self._find(key) >= 0

    def Count(self) -> int:
        return len(self._pairs)

    def _at(self, index: int) -> tuple[Any, Any]:
        if type(index) is not int or not 0 <= index < len(self._pairs):
            raise AssertionError(f"index {index!r} outside a map of {len(self._pairs)}")
        return self._pairs[index]

    def GetKey(self, index: int) -> Any:
        return self._at(index)[0]

    def GetElement(self, index: int) -> Any:
        return self._at(index)[1]


class EnforceVector(list):
    """vector: three floats, copied on assignment by the translation."""

    __hash__ = None  # type: ignore[assignment]

    def __init__(self, value: Iterable[float] = (0.0, 0.0, 0.0)) -> None:
        components = [float(component) for component in value]
        if len(components) != 3:
            raise AssertionError(f"a vector has 3 components, not {len(components)}")
        super().__init__(components)

    def _check(self, index: object) -> int:
        if type(index) is not int or not 0 <= index < 3:
            raise AssertionError(f"vector index {index!r}")
        return index

    def __getitem__(self, index: object) -> float:  # type: ignore[override]
        return list.__getitem__(self, self._check(index))

    def __setitem__(self, index: object, value: object) -> None:  # type: ignore[override]
        list.__setitem__(self, self._check(index), float(value))  # type: ignore[arg-type]


def clean(source: str) -> str:
    """Blank comments (keeping line breaks) and keep string literals intact."""

    def replace(match: re.Match[str]) -> str:
        value = match.group()
        if value.startswith('"'):
            return value
        return "".join("\n" if char == "\n" else " " for char in value)

    return _COMMENT_OR_STRING.sub(replace, source)


def method_body(source: str, signature: str) -> str:
    """Body of the one method declared with `signature` (comments blanked)."""
    text = clean(source)
    start = text.find(signature)
    if start < 0 or text.find(signature, start + 1) >= 0:
        raise AssertionError(f"signature not found exactly once: {signature}")
    brace = text.index("{", start + len(signature))
    if text[start + len(signature) : brace].strip():
        raise AssertionError(f"text between signature and body: {signature}")
    depth = 0
    for match in _STRING_OR_BRACE.finditer(text, brace):
        token = match.group()
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
            if depth == 0:
                return text[brace + 1 : match.start()]
    raise AssertionError(f"unterminated body: {signature}")


_FIELD = re.compile(
    r"^(?P<ref>ref\s+)?(?P<type>[A-Za-z_]\w*(?:\s*<[^;{}()=]*>)?)\s+(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*=(?!=)\s*(?P<init>.+))?$"
)
_FIELD_ZEROES: dict[str, Any] = {"int": 0, "float": 0.0, "bool": False}


def declared_fields(source: str, class_name: str) -> dict[str, Any]:
    """Each field of `class <class_name>` in `source`, in order, with the value
    a new instance holds: its initializer (true, false, a number or one string
    literal with no quote and no backslash inside) when it has one, else
    Enforce's zero: 0, 0.0, false, "", (0.0, 0.0, 0.0) for a vector and None
    for a ref, an array or a class. The class must hold plain field
    declarations only: a constructor, a method, a modifier or any other
    initializer, such as a concatenation of literals, fails instead of being
    guessed."""
    text = clean(source)
    heads = list(re.finditer(rf"(?m)^class {re.escape(class_name)}\b[^{{;]*\{{", text))
    if len(heads) != 1:
        raise AssertionError(f"class {class_name} declared {len(heads)} times")
    statements: list[str] = []
    buffer = ""
    for token in _CHUNK_TOKENS.finditer(text, heads[0].end()):
        value = token.group()
        if value == "}":
            break
        if value == "{":
            raise AssertionError(f"class {class_name} holds more than fields: {buffer.strip()!r}")
        if value == ";":
            statements.append(_squash(buffer))
            buffer = ""
        else:
            buffer += value
    else:
        raise AssertionError(f"class {class_name} is not closed")
    if buffer.strip():
        raise AssertionError(f"class {class_name}: statement without a terminator {buffer.strip()!r}")
    fields: dict[str, Any] = {}
    for statement in statements:
        match = _FIELD.match(statement)
        if match is None:
            raise AssertionError(f"class {class_name}: not a plain field: {statement!r}")
        name = match.group("name")
        if name in fields:
            raise AssertionError(f"class {class_name}: {name} declared twice")
        fields[name] = _field_value(class_name, match, statement)
    return fields


def _field_value(class_name: str, match: re.Match[str], statement: str) -> Any:
    type_name = match.group("type")
    init = match.group("init")
    if init is None:
        if match.group("ref") or "<" in type_name:
            return None
        if type_name in _FIELD_ZEROES:
            return _FIELD_ZEROES[type_name]
        if type_name == "string":
            return EnforceString(b"")
        if type_name == "vector":
            return (0.0, 0.0, 0.0)
        return None
    init = init.strip()
    if type_name == "bool" and init in ("true", "false"):
        return init == "true"
    if type_name == "int" and re.fullmatch(r"-?\d+", init):
        return int(init)
    if type_name == "float" and re.fullmatch(r"-?\d+(?:\.\d+)?", init):
        return float(init)
    # One literal: no quote inside, so "a" + "b" is not read as one string.
    if type_name == "string" and re.fullmatch(r'"[ !#-~]*"', init) and "\\" not in init:
        return EnforceString(init[1:-1].encode("ascii"))
    raise AssertionError(f"class {class_name}: unsupported initializer in {statement!r}")


def _squash(text: str) -> str:
    """Collapse each whitespace run outside string literals to one space."""
    parts: list[str] = []
    for match in re.finditer(r'"(?:\\.|[^"\\])*"|\s+|[^"\s]+|"', text):
        value = match.group()
        if value == '"':
            raise AssertionError(f"unterminated string literal in {text!r}")
        parts.append(" " if value.isspace() else value)
    return "".join(parts).strip()


def _chunks(body: str) -> list[str]:
    chunks: list[str] = []
    buffer = ""
    for token in _CHUNK_TOKENS.findall(body):
        if token in ("{", "}", ";"):
            if buffer.strip():
                chunks.append(_squash(buffer))
            buffer = ""
            if token != ";":
                chunks.append(token)
        else:
            buffer += token
    if buffer.strip():
        raise AssertionError(f"statement without a terminator: {buffer.strip()!r}")
    return chunks


def _string_literal(token: str) -> str:
    content = token[1:-1]
    if "\\" in content or not all(" " <= char <= "~" for char in content):
        raise AssertionError(f"unsupported string literal: {token}")
    return "_S(" + repr(content.encode("ascii")) + ")"


def _check_negation_operand(tokens: list[str], index: int) -> None:
    """`!` must cover exactly one operand: `!a.b(c)` or `!(...)`, then a boundary."""
    position = index + 1
    if position >= len(tokens):
        raise AssertionError("! without an operand")
    if tokens[position] == "(":
        depth = 0
        while position < len(tokens):
            depth += (tokens[position] == "(") - (tokens[position] == ")")
            position += 1
            if depth == 0:
                break
    elif re.fullmatch(r"[A-Za-z_]\w*", tokens[position]):
        position += 1
    else:
        raise AssertionError(f"! before {tokens[position]!r}")
    while position < len(tokens):
        if tokens[position] == "." and position + 1 < len(tokens):
            position += 2
        elif tokens[position] in ("(", "["):
            opener = tokens[position]
            closer = ")" if opener == "(" else "]"
            depth = 0
            while position < len(tokens):
                depth += (tokens[position] == opener) - (tokens[position] == closer)
                position += 1
                if depth == 0:
                    break
        else:
            break
    if position < len(tokens) and tokens[position] not in ("&&", "||", ")", ","):
        raise AssertionError(
            f"! applies to part of an operand before {tokens[position]!r}; C and Python differ"
        )


def _drop_new_generics(tokens: list[str], text: str) -> list[str]:
    """`new T<...>(` becomes `new T(`; `new` must be followed by a type and `(`."""
    kept: list[str] = []
    position = 0
    while position < len(tokens):
        token = tokens[position]
        kept.append(token)
        position += 1
        if token != "new":
            continue
        if position >= len(tokens) or not re.fullmatch(r"[A-Za-z_]\w*", tokens[position]):
            raise AssertionError(f"new without a type in {text!r}")
        kept.append(tokens[position])
        position += 1
        if position < len(tokens) and tokens[position] == "<":
            depth = 0
            while position < len(tokens):
                if tokens[position] == "<":
                    depth += 1
                elif tokens[position] == ">":
                    depth -= 1
                elif not re.fullmatch(r"[A-Za-z_]\w*|,", tokens[position]):
                    raise AssertionError(f"unsupported generic argument in {text!r}")
                position += 1
                if depth == 0:
                    break
        if position >= len(tokens) or tokens[position] != "(":
            raise AssertionError(f"new without a constructor call in {text!r}")
    return kept


def _expression(text: str, out_functions: Iterable[str] = ()) -> str:
    tokens: list[str] = []
    position = 0
    while position < len(text):
        match = _EXPR_TOKENS.match(text, position)
        if match is None:
            raise AssertionError(f"unsupported expression text {text[position:]!r} in {text!r}")
        if not match.group().isspace():
            tokens.append(match.group())
        position = match.end()
    tokens = _drop_new_generics(tokens, text)
    comparisons = [0]
    for token in tokens:
        if token in ("(", "["):
            comparisons.append(0)
        elif token in (")", "]"):
            if len(comparisons) == 1:
                raise AssertionError(f"unbalanced parentheses in {text!r}")
            comparisons.pop()
        elif token in ("&&", "||", ","):
            comparisons[-1] = 0
        elif token in _COMPARISONS:
            comparisons[-1] += 1
            if comparisons[-1] > 1:
                raise AssertionError(f"chained comparison in {text!r}; C and Python differ")
    if len(comparisons) != 1:
        raise AssertionError(f"unbalanced parentheses in {text!r}")
    rendered: list[str] = []
    out_functions = frozenset(out_functions)
    for index, token in enumerate(tokens):
        if token.startswith('"'):
            rendered.append(_string_literal(token))
        elif token == "&&":
            rendered.append(" and ")
        elif token == "||":
            rendered.append(" or ")
        elif token == "!":
            _check_negation_operand(tokens, index)
            rendered.append(" not ")
        elif token == "new":
            # new T(...) calls the namespace's T (_drop_new_generics checked it).
            rendered.append("")
        elif token in _LITERALS:
            rendered.append(_LITERALS[token])
        elif re.fullmatch(r"[A-Za-z_]\w*", token):
            if keyword.iskeyword(token) or token.startswith("_"):
                raise AssertionError(f"identifier {token!r} cannot be translated")
            if token in out_functions and (index == 0 or tokens[index - 1] != "."):
                raise AssertionError(f"{token} has out parameters; call it as an assignment")
            rendered.append(token)
        elif token in _COMPARISONS or token in ("+", "-", "*", "|", "&"):
            rendered.append(f" {token} ")
        else:
            rendered.append(token)
    return "".join(rendered)


def _parse_signature(
    signature: str,
) -> tuple[str, list[str], list[tuple[str, str]], dict[str, str]]:
    match = _SIGNATURE.match(signature.strip())
    if match is None:
        raise AssertionError(f"unsupported signature: {signature}")
    params: list[str] = []
    outs: list[tuple[str, str]] = []
    types: dict[str, str] = {}
    raw = match.group("params").strip()
    for part in [piece.strip() for piece in raw.split(",")] if raw else []:
        words = part.split()
        if len(words) == 3 and words[0] == "out":
            outs.append((words[2], words[1]))
            types[words[2]] = _base_type(words[1])
        elif len(words) == 2:
            params.append(words[1])
            types[words[1]] = _base_type(words[0])
        else:
            raise AssertionError(f"unsupported parameter {part!r} in {signature}")
    return match.group("name"), params, outs, types


def _base_type(type_name: str) -> str:
    """`array<ref T>` -> `array`; a plain type is itself."""
    return type_name.split("<", 1)[0].strip()


def _default(type_name: str, class_types: frozenset[str]) -> str:
    base = _base_type(type_name)
    if base != type_name.strip() and base not in class_types:
        raise AssertionError(f"unknown generic type {type_name}")
    if base in _BASE_DEFAULTS:
        return _BASE_DEFAULTS[base]
    if base in class_types:
        return "None"
    raise AssertionError(f"unknown type {type_name}")


def translate(
    source: str,
    signature: str,
    namespace: dict[str, Any],
    *,
    class_types: Iterable[str] = (),
    out_functions: dict[str, int] | None = None,
    members: Iterable[str] = (),
) -> Callable[..., Any]:
    """Translate the method `signature` of `source` into `namespace` and return it.

    `class_types` are the non-primitive local types the body may declare
    (`array` and `map` for generic locals). `out_functions` maps each callee
    that has only `out` parameters to their count; such a call must be the
    whole right-hand side of an assignment. `members` are the class members the
    body reads or assigns bare: they live in `namespace`.
    """
    name, code = python_source(
        source,
        signature,
        class_types=class_types,
        out_functions=out_functions,
        members=members,
    )
    namespace.setdefault("_S", EnforceString)
    namespace.setdefault("_bounded", _bounded)
    namespace.setdefault("_vector", EnforceVector)
    exec(compile(code, f"<enforce:{name}>", "exec"), namespace)
    return namespace[name]


def python_source(
    source: str,
    signature: str,
    *,
    class_types: Iterable[str] = (),
    out_functions: dict[str, int] | None = None,
    members: Iterable[str] = (),
) -> tuple[str, str]:
    """(method name, Python text) for the method `signature` of `source`."""
    name, params, outs, local_types = _parse_signature(signature)
    class_types = frozenset(class_types)
    out_functions = dict(out_functions or {})
    members = frozenset(members)
    body = method_body(source, signature)
    declared = set(params) | {out for out, _type in outs}
    if declared & members:
        raise AssertionError(f"{sorted(declared & members)} are both members and parameters")
    lines = [f"def {name}({', '.join(params)}):"]
    if members:
        lines.append(f"    global {', '.join(sorted(members))}")
    lines.append("    _loops = [0]")
    for out, out_type in outs:
        lines.append(f"    {out} = {_default(out_type, class_types)}")
    returns = ", ".join(out for out, _type in outs)

    def render_return(value: str | None) -> str:
        rendered = "None" if value is None else _expression(value, out_functions)
        if outs:
            return f"return ({rendered}, {returns})"
        return f"return {rendered}" if value is not None else "return"

    def render_out_call(targets: list[str], value: str) -> str | None:
        call = _CALL.match(value)
        if call is None or call.group("name") not in out_functions:
            return None
        arguments = [argument.strip() for argument in call.group("args").split(",") if argument.strip()]
        if len(arguments) != out_functions[call.group("name")]:
            raise AssertionError(f"{value}: out parameter count differs")
        for argument in arguments:
            if argument not in declared:
                raise AssertionError(f"{value}: out argument {argument} is not a declared local")
        return f"{', '.join(targets + arguments)} = {call.group('name')}()"

    def render_value(target: str, value: str) -> str:
        rendered = _expression(value, out_functions)
        # A vector is a value type: the local gets its own copy.
        if local_types.get(target) == "vector":
            return f"_vector({rendered})"
        return rendered

    def render(chunk: str) -> str:
        if chunk == "else":
            return "else:"
        for keyword_name, python in (("else if", "elif"), ("if", "if"), ("while", "while")):
            if chunk.startswith(keyword_name + " (") or chunk.startswith(keyword_name + "("):
                if not chunk.endswith(")"):
                    raise AssertionError(f"control statement without braces: {chunk!r}")
                condition = chunk[chunk.index("(") + 1 : -1]
                rendered = _expression(condition, out_functions)
                if python == "while":
                    return f"while _bounded(_loops, {rendered}):"
                return f"{python} {rendered}:"
        if chunk == "return":
            return render_return(None)
        if chunk.startswith("return "):
            return render_return(chunk[len("return ") :])
        declaration = _DECLARATION.match(chunk)
        if declaration and (
            _base_type(declaration.group("type")) in _BASE_DEFAULTS
            or _base_type(declaration.group("type")) in class_types
        ):
            local = declaration.group("name")
            if local in declared or local in members:
                raise AssertionError(f"{local} declared twice in {name}")
            if keyword.iskeyword(local) or local.startswith("_"):
                raise AssertionError(f"local {local!r} cannot be translated")
            init = declaration.group("init")
            declared.add(local)
            local_types[local] = _base_type(declaration.group("type"))
            if init is None:
                return f"{local} = {_default(declaration.group('type'), class_types)}"
            out_call = render_out_call([local], init)
            return out_call or f"{local} = {render_value(local, init)}"
        assignment = _ASSIGNMENT.match(chunk)
        if assignment:
            target = assignment.group("target")
            base = re.match(r"[A-Za-z_]\w*", target).group()  # type: ignore[union-attr]
            if "." not in target and base not in declared and base not in members:
                raise AssertionError(f"assignment to undeclared {target} in {name}")
            if "[" in target:
                return (
                    f"{_expression(target, out_functions)} = "
                    f"{_expression(assignment.group('value'), out_functions)}"
                )
            out_call = render_out_call([target], assignment.group("value"))
            return out_call or f"{target} = {render_value(target, assignment.group('value'))}"
        if re.fullmatch(r"[A-Za-z_][\w.]*\(.*\)", chunk):
            return _expression(chunk, out_functions)
        raise AssertionError(f"unsupported statement in {name}: {chunk!r}")

    indent = 1
    for chunk in _chunks(body):
        if chunk == "{":
            indent += 1
            lines.append("    " * indent + "pass")
        elif chunk == "}":
            indent -= 1
            if indent < 1:
                raise AssertionError(f"unbalanced braces in {name}")
        else:
            lines.append("    " * indent + render(chunk))
    if indent != 1:
        raise AssertionError(f"unbalanced braces in {name}")
    return name, "\n".join(lines) + "\n"


def _bounded(counter: list[int], condition: object) -> object:
    if condition:
        counter[0] += 1
        if counter[0] > WHILE_LIMIT:
            raise AssertionError(f"while loop ran more than {WHILE_LIMIT} times")
    return condition


_CLASS_HEADER = re.compile(r"^class\s+(?P<name>[A-Za-z_]\w*)\b[^\n{]*\n?\{", re.M)
_MEMBER = re.compile(
    r"^(?P<type>(?:ref\s+)?[A-Za-z_]\w*(?:\s*<[^;]*>)?)\s+(?P<name>[A-Za-z_]\w*)$"
)
_CONSTRUCTOR_VALUE = re.compile(
    r'^(?:new\s+(?P<new>[A-Za-z_]\w*)\s*(?:<[^()]*>)?\s*\(\s*\)'
    r'|(?P<number>-?\d+(?:\.\d+)?)|(?P<literal>true|false)|(?P<string>"[ -~]*")'
    r"|(?P<constant>[A-Za-z_]\w*))$"
)


def _class_body(source: str, class_name: str) -> str:
    text = clean(source)
    headers = [m for m in _CLASS_HEADER.finditer(text) if m.group("name") == class_name]
    if len(headers) != 1:
        raise AssertionError(f"class {class_name} declared {len(headers)} times")
    start = headers[0].end()
    depth = 1
    for match in _STRING_OR_BRACE.finditer(text, start):
        token = match.group()
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
            if depth == 0:
                return text[start : match.start()]
    raise AssertionError(f"unterminated class {class_name}")


def class_members(source: str, class_name: str) -> list[tuple[str, str]]:
    """(type, name) of each data member `class_name` declares, in order.

    The type keeps `ref` and its generic arguments with single spaces
    (`ref array<ref MCPSpawnBatchItem>`). Methods are skipped; a member line
    the pattern does not read fails the test instead of being dropped.
    """
    body = _class_body(source, class_name)
    members: list[tuple[str, str]] = []
    statement = ""
    depth = 0
    for token in _CHUNK_TOKENS.findall(body):
        if token == "{":
            if depth == 0:
                statement = ""
            depth += 1
        elif token == "}":
            depth -= 1
        elif depth > 0:
            continue
        elif token == ";":
            text = " ".join(statement.split())
            statement = ""
            if not text:
                continue
            match = _MEMBER.match(text.replace("< ", "<").replace(" >", ">"))
            if match is None:
                raise AssertionError(f"member of {class_name} not read: {text!r}")
            members.append((match.group("type"), match.group("name")))
        else:
            statement += token
    return members


def _member_default(type_name: str) -> Callable[[], Any]:
    base = _base_type(type_name)
    if base == "int":
        return lambda: 0
    if base == "float":
        return lambda: 0.0
    if base == "bool":
        return lambda: False
    if base == "string":
        return lambda: EnforceString(b"")
    if base == "vector":
        return EnforceVector
    # A class, array or map member is null until something assigns it.
    return lambda: None


def fake_class(
    source: str,
    class_name: str,
    namespace: dict[str, Any],
    constants: dict[str, Any] | None = None,
) -> type:
    """A Python stand-in for an Enforce class: exactly its declared data members
    (slots, so a member it does not declare cannot be read or written), their
    Enforce defaults, then what its constructor assigns. Stored in namespace."""
    members = class_members(source, class_name)
    defaults = [(name, _member_default(type_name)) for type_name, name in members]
    assigned: list[tuple[str, Callable[[], Any]]] = []
    signature = f"void {class_name}()"
    if signature in clean(source):
        for statement in [part.strip() for part in method_body(source, signature).split(";")]:
            if not statement:
                continue
            target, sep, value = (piece.strip() for piece in statement.partition("="))
            match = _CONSTRUCTOR_VALUE.match(value)
            if not sep or match is None or target not in {name for _t, name in members}:
                raise AssertionError(f"{class_name} constructor statement not read: {statement!r}")
            assigned.append((target, _constructor_factory(match, namespace, constants or {})))

    def __init__(self: Any) -> None:
        for member, factory in defaults + assigned:
            setattr(self, member, factory())

    def __repr__(self: Any) -> str:
        return f"<{class_name} {id(self):#x}>"

    cls = type(
        class_name,
        (),
        {"__slots__": tuple(name for _t, name in members), "__init__": __init__, "__repr__": __repr__},
    )
    namespace[class_name] = cls
    return cls


def _constructor_factory(
    match: re.Match[str], namespace: dict[str, Any], constants: dict[str, Any]
) -> Callable[[], Any]:
    if match.group("new"):
        type_name = match.group("new")
        if type_name == "array":
            return EnforceArray
        if type_name == "map":
            return EnforceMap
        return lambda: namespace[type_name]()
    if match.group("number"):
        text = match.group("number")
        number = float(text) if "." in text else int(text)
        return lambda: number
    if match.group("literal"):
        flag = match.group("literal") == "true"
        return lambda: flag
    if match.group("string"):
        data = match.group("string")[1:-1].encode("ascii")
        return lambda: EnforceString(data)
    name = match.group("constant")
    if name not in constants:
        raise AssertionError(f"constructor reads {name}; pass it in constants")
    value = constants[name]
    return lambda: value
