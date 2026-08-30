"""Cuarta implementacion legitima, distinta de legit_alt, legit_alt2 y legit_alt3.

Resolver: visitante por tabla (handlers type/anyOf/oneOf en un dict, pila
explicita de marcos). Auditor: sinonimos por lista de adyacencia bidireccional
(no frozenset, no id entero, no etiqueta entity-selector) y pipes recortando
el texto por lineas y por el separador '|' a mano, sin regex y sin el escaner
char-a-char de alt2 ni el tokenizador whitespace de alt3.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from dayz_mcp.server import ServerConfig, build_app

ADJACENT = (("classname", "type"), ("type", "classname"))
CODE_DIV = "PARAM-NAME-DIVERGENCE"
CODE_ENUM = "DESC-ENUM-MISMATCH"


def _mapping(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return {}
    try:
        return dict(raw)
    except Exception:
        return {}


def _collect_types(root: dict[str, Any]) -> list[str]:
    named: list[str] = []
    seen: set[str] = set()

    def take(value: Any) -> None:
        if isinstance(value, str):
            if value != "null" and value not in seen:
                seen.add(value)
                named.append(value)
            return
        if isinstance(value, list):
            for item in value:
                take(item)

    handlers: dict[str, Callable[[dict[str, Any]], None]] = {}

    def handle_node(node: dict[str, Any]) -> None:
        declared = node.get("type")
        if isinstance(declared, str) or isinstance(declared, list):
            take(declared)
            if named:
                return
        for key in ("anyOf", "oneOf"):
            alts = node.get(key)
            if not isinstance(alts, list):
                continue
            for alt in alts:
                if isinstance(alt, dict):
                    handlers["node"](alt)
            if named:
                return

    handlers["node"] = handle_node
    handlers["node"](root)
    if not named:
        return []
    return named


def _published_type(schema: dict[str, Any]) -> Any:
    named = _collect_types(schema)
    if not named:
        return None
    if len(named) == 1:
        return named[0]
    return named


def _published_enum(schema: dict[str, Any]) -> list[Any] | None:
    raw = schema.get("enum")
    if isinstance(raw, list):
        return list(raw)
    for key in ("oneOf", "anyOf"):
        alts = schema.get(key)
        if not isinstance(alts, list):
            continue
        for alt in alts:
            if isinstance(alt, dict) and isinstance(alt.get("enum"), list):
                return list(alt["enum"])
    return None


def _param_entry(name: str, raw: Any, required: set[str]) -> dict[str, Any]:
    schema = _mapping(raw)
    return {
        "required": name in required,
        "default": schema["default"] if "default" in schema else None,
        "type": _published_type(schema),
        "enum": _published_enum(schema),
    }


def resolve_effective_schemas() -> dict[str, dict]:
    async def _go() -> dict[str, dict]:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))
        listed = await app.list_tools()
        registry: dict[str, dict] = {}
        for tool in listed:
            schema = _mapping(getattr(tool, "inputSchema", None) or {})
            required = {
                item
                for item in (schema.get("required") or [])
                if isinstance(item, str)
            }
            properties = schema.get("properties") or {}
            params = {
                str(name): _param_entry(str(name), pschema, required)
                for name, pschema in properties.items()
            }
            registry[tool.name] = {
                "description": tool.description or "",
                "params": params,
            }
        return registry

    return asyncio.run(_go())


def _params(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        return {}
    params = spec.get("params")
    return params if isinstance(params, dict) else {}


def _neighbors() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for left, right in ADJACENT:
        graph.setdefault(left, set()).add(right)
        graph.setdefault(right, set()).add(left)
    return graph


def _divergence(schemas: dict) -> list[dict]:
    graph = _neighbors()
    usage: dict[str, list[str]] = {name: [] for name in graph}
    for tool_name, spec in schemas.items():
        for param_name in _params(spec):
            if param_name in usage:
                usage[param_name].append(str(tool_name))
    present = [name for name, tools in usage.items() if tools]
    if len(present) < 2:
        return []
    connected = False
    for name in present:
        if graph[name].intersection(present):
            connected = True
            break
    if not connected:
        return []
    summary = {name: list(usage[name]) for name in present}
    findings = []
    for param_name in present:
        for tool_name in usage[param_name]:
            findings.append(
                {
                    "tool": tool_name,
                    "code": CODE_DIV,
                    "param": param_name,
                    "evidence": f"adjacent names {sorted(present)} appear as {summary}",
                }
            )
    return findings


def _ident_at(text: str, index: int) -> str | None:
    if index >= len(text):
        return None
    ch = text[index]
    if not (("A" <= ch <= "Z") or ("a" <= ch <= "z") or ch == "_"):
        return None
    end = index + 1
    while end < len(text):
        nxt = text[end]
        if (
            ("A" <= nxt <= "Z")
            or ("a" <= nxt <= "z")
            or ("0" <= nxt <= "9")
            or nxt == "_"
        ):
            end += 1
            continue
        break
    return text[index:end]


def _pipe_spans(text: str) -> list[tuple[int, int, list[str]]]:
    spans: list[tuple[int, int, list[str]]] = []
    cursor = 0
    length = len(text)
    while cursor < length:
        token = _ident_at(text, cursor)
        if token is None:
            cursor += 1
            continue
        start = cursor
        pieces = [token]
        look = cursor + len(token)
        while True:
            skip = look
            while skip < length and text[skip] in " \t":
                skip += 1
            if skip >= length or text[skip] != "|":
                break
            skip += 1
            while skip < length and text[skip] in " \t":
                skip += 1
            nxt = _ident_at(text, skip)
            if nxt is None:
                break
            pieces.append(nxt)
            look = skip + len(nxt)
        if len(pieces) >= 2:
            spans.append((start, look, pieces))
            cursor = look
        else:
            cursor += len(token)
    return spans


def _sentence_slice(text: str, pos: int) -> tuple[int, int]:
    start = 0
    index = 0
    length = len(text)
    while index < length:
        ch = text[index]
        if ch in ".!?":
            end = index + 1
            while end < length and text[end] in ".!?":
                end += 1
            while end < length and text[end] in " \t\r\n":
                end += 1
            if end <= pos:
                start = end
                index = end
                continue
            return start, end
        index += 1
    return start, length


def _gap(a0: int, a1: int, b0: int, b1: int) -> int:
    if a1 <= b0:
        return b0 - a1
    if b1 <= a0:
        return a0 - b1
    return 0


def _mentions(description: str, names: list[str]) -> list[tuple[int, int, str]]:
    found: list[tuple[int, int, str]] = []
    for name in names:
        start = 0
        while True:
            pos = description.find(name, start)
            if pos < 0:
                break
            before = description[pos - 1] if pos > 0 else " "
            after_i = pos + len(name)
            after = description[after_i] if after_i < len(description) else " "
            def _wordish(ch: str) -> bool:
                return (
                    ("A" <= ch <= "Z")
                    or ("a" <= ch <= "z")
                    or ("0" <= ch <= "9")
                    or ch == "_"
                )
            if not _wordish(before) and not _wordish(after):
                found.append((pos, pos + len(name), name))
            start = pos + 1
    return found


def _nearest(
    description: str, start: int, end: int, names: list[str]
) -> str | None:
    hits = _mentions(description, names)
    if not hits:
        return None
    lo, hi = _sentence_slice(description, start)
    local = [item for item in hits if lo <= item[0] < hi]
    pool = local or hits
    return min(pool, key=lambda item: (_gap(start, end, item[0], item[1]), item[0]))[2]


def _enum_mismatch(schemas: dict) -> list[dict]:
    findings: list[dict] = []
    for tool_name, spec in schemas.items():
        if not isinstance(spec, dict):
            continue
        description = spec.get("description") or ""
        if not isinstance(description, str) or not description:
            continue
        params = _params(spec)
        if not params:
            continue
        names = [str(name) for name in params]
        for start, end, advertised in _pipe_spans(description):
            if len(advertised) < 2:
                continue
            param_name = _nearest(description, start, end, names)
            if param_name is None:
                continue
            info = params.get(param_name)
            if not isinstance(info, dict):
                continue
            enum_vals = info.get("enum")
            if not isinstance(enum_vals, list):
                continue
            if set(advertised) == set(enum_vals):
                continue
            findings.append(
                {
                    "tool": str(tool_name),
                    "code": CODE_ENUM,
                    "param": str(param_name),
                    "evidence": (
                        f"{tool_name}.{param_name} lists {advertised} "
                        f"but enum is {list(enum_vals)}"
                    ),
                }
            )
    return findings


def audit_contracts(schemas: dict | None = None) -> list[dict]:
    resolved = resolve_effective_schemas() if schemas is None else schemas
    if not isinstance(resolved, dict):
        return []
    findings = _divergence(resolved)
    findings.extend(_enum_mismatch(resolved))
    findings.sort(
        key=lambda item: (
            str(item.get("code") or ""),
            str(item.get("tool") or ""),
            str(item.get("param") or ""),
        )
    )
    return findings
