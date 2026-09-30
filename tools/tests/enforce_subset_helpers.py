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
  bounded, so a mutant that stops advancing fails instead of hanging.
"""
from __future__ import annotations

import keyword
import re
from typing import Any, Callable, Iterable

_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/')
_STRING_OR_BRACE = re.compile(r'"(?:\\.|[^"\\])*"|[{}]')
_CHUNK_TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|[^"{};]+|[{};]')
_EXPR_TOKENS = re.compile(
    r'\s+|"(?:\\.|[^"\\])*"|&&|\|\||==|!=|<=|>=|!|[A-Za-z_]\w*|\d+\.\d+|\d+|[-+*<>(),.]'
)
_SIGNATURE = re.compile(
    r"^(?:(?:protected|private|static|override)\s+)*"
    r"(?P<ret>[A-Za-z_]\w*)\s+(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^()]*)\)$"
)
_DECLARATION = re.compile(
    r"^(?P<type>[A-Za-z_]\w*)\s+(?P<name>[A-Za-z_]\w*)\s*(?:=(?!=)\s*(?P<init>.+))?$"
)
_ASSIGNMENT = re.compile(
    r"^(?P<target>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*=(?!=)\s*(?P<value>.+)$"
)
_CALL = re.compile(r"^(?P<name>[A-Za-z_]\w*)\((?P<args>[^()]*)\)$")
_COMPARISONS = frozenset({"<", ">", "<=", ">=", "==", "!="})
_BASE_DEFAULTS = {"int": "0", "float": "0.0", "bool": "False", "string": "_S(b'')"}
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
        elif tokens[position] == "(":
            depth = 0
            while position < len(tokens):
                depth += (tokens[position] == "(") - (tokens[position] == ")")
                position += 1
                if depth == 0:
                    break
        else:
            break
    if position < len(tokens) and tokens[position] not in ("&&", "||", ")", ","):
        raise AssertionError(
            f"! applies to part of an operand before {tokens[position]!r}; C and Python differ"
        )


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
    comparisons = [0]
    for token in tokens:
        if token == "(":
            comparisons.append(0)
        elif token == ")":
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
        elif token in _LITERALS:
            rendered.append(_LITERALS[token])
        elif re.fullmatch(r"[A-Za-z_]\w*", token):
            if keyword.iskeyword(token) or token.startswith("_"):
                raise AssertionError(f"identifier {token!r} cannot be translated")
            if token in out_functions and (index == 0 or tokens[index - 1] != "."):
                raise AssertionError(f"{token} has out parameters; call it as an assignment")
            rendered.append(token)
        elif token in _COMPARISONS or token in ("+", "-", "*"):
            rendered.append(f" {token} ")
        else:
            rendered.append(token)
    return "".join(rendered)


def _parse_signature(signature: str) -> tuple[str, list[str], list[tuple[str, str]]]:
    match = _SIGNATURE.match(signature.strip())
    if match is None:
        raise AssertionError(f"unsupported signature: {signature}")
    params: list[str] = []
    outs: list[tuple[str, str]] = []
    raw = match.group("params").strip()
    for part in [piece.strip() for piece in raw.split(",")] if raw else []:
        words = part.split()
        if len(words) == 3 and words[0] == "out":
            outs.append((words[2], words[1]))
        elif len(words) == 2:
            params.append(words[1])
        else:
            raise AssertionError(f"unsupported parameter {part!r} in {signature}")
    return match.group("name"), params, outs


def _default(type_name: str, class_types: frozenset[str]) -> str:
    if type_name in _BASE_DEFAULTS:
        return _BASE_DEFAULTS[type_name]
    if type_name in class_types:
        return "None"
    raise AssertionError(f"unknown type {type_name}")


def translate(
    source: str,
    signature: str,
    namespace: dict[str, Any],
    *,
    class_types: Iterable[str] = (),
    out_functions: dict[str, int] | None = None,
) -> Callable[..., Any]:
    """Translate the method `signature` of `source` into `namespace` and return it.

    `class_types` are the non-primitive local types the body may declare.
    `out_functions` maps each callee that has only `out` parameters to their
    count; such a call must be the whole right-hand side of an assignment.
    """
    name, code = python_source(
        source, signature, class_types=class_types, out_functions=out_functions
    )
    namespace.setdefault("_S", EnforceString)
    namespace.setdefault("_bounded", _bounded)
    exec(compile(code, f"<enforce:{name}>", "exec"), namespace)
    return namespace[name]


def python_source(
    source: str,
    signature: str,
    *,
    class_types: Iterable[str] = (),
    out_functions: dict[str, int] | None = None,
) -> tuple[str, str]:
    """(method name, Python text) for the method `signature` of `source`."""
    name, params, outs = _parse_signature(signature)
    class_types = frozenset(class_types)
    out_functions = dict(out_functions or {})
    body = method_body(source, signature)
    declared = set(params) | {out for out, _type in outs}
    lines = [f"def {name}({', '.join(params)}):", "    _loops = [0]"]
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
            declaration.group("type") in _BASE_DEFAULTS or declaration.group("type") in class_types
        ):
            local = declaration.group("name")
            if local in declared:
                raise AssertionError(f"{local} declared twice in {name}")
            if keyword.iskeyword(local) or local.startswith("_"):
                raise AssertionError(f"local {local!r} cannot be translated")
            init = declaration.group("init")
            declared.add(local)
            if init is None:
                return f"{local} = {_default(declaration.group('type'), class_types)}"
            out_call = render_out_call([local], init)
            return out_call or f"{local} = {_expression(init, out_functions)}"
        assignment = _ASSIGNMENT.match(chunk)
        if assignment:
            target = assignment.group("target")
            if "." not in target and target not in declared:
                raise AssertionError(f"assignment to undeclared {target} in {name}")
            out_call = render_out_call([target], assignment.group("value"))
            return out_call or f"{target} = {_expression(assignment.group('value'), out_functions)}"
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
