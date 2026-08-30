"""Tercera implementacion legitima, distinta de legit_alt y legit_alt2.

Resuelve con un ensamblador iterativo (pila de nodos type/anyOf/oneOf, sin
cola FIFO ni walk recursivo). Audita sinonimos por id de grupo entero, no por
frozenset ni etiqueta 'entity-selector', y extrae pipes partiendo el texto en
tokens whitespace/puntuacion en vez de regex o del escaner char-a-char de alt2.
"""
from __future__ import annotations

import asyncio
from typing import Any

from dayz_mcp.server import ServerConfig, build_app

SYNONYM_GROUP_ID = {
    "type": 1,
    "classname": 1,
}
CODE_DIVERGENCE = "PARAM-NAME-DIVERGENCE"
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


def _push_type_nodes(stack: list[Any], node: Any) -> None:
    if isinstance(node, str):
        stack.append(("type", node))
        return
    if isinstance(node, list):
        for item in reversed(node):
            stack.append(("node", item))
        return
    if isinstance(node, dict):
        stack.append(("node", node))


def _published_type(schema: dict[str, Any]) -> Any:
    named: list[str] = []
    seen: set[str] = set()
    stack: list[tuple[str, Any]] = [("node", schema)]
    while stack:
        kind, node = stack.pop()
        if kind == "type":
            if isinstance(node, str) and node != "null" and node not in seen:
                seen.add(node)
                named.append(node)
            continue
        if not isinstance(node, dict):
            if isinstance(node, str) and node != "null" and node not in seen:
                seen.add(node)
                named.append(node)
            continue
        declared = node.get("type")
        if isinstance(declared, str):
            if declared != "null" and declared not in seen:
                seen.add(declared)
                named.append(declared)
            continue
        if isinstance(declared, list):
            for item in declared:
                if isinstance(item, str) and item != "null" and item not in seen:
                    seen.add(item)
                    named.append(item)
            if named:
                continue
        progressed = False
        for key in ("anyOf", "oneOf"):
            alts = node.get(key)
            if not isinstance(alts, list):
                continue
            for alt in reversed(alts):
                stack.append(("node", alt))
            progressed = True
        if progressed and named:
            break
    if not named:
        return None
    if len(named) == 1:
        return named[0]
    return named


def _published_enum(schema: dict[str, Any]) -> list[Any] | None:
    raw = schema.get("enum")
    if isinstance(raw, list):
        return list(raw)
    stack: list[Any] = []
    for key in ("anyOf", "oneOf"):
        alts = schema.get(key)
        if isinstance(alts, list):
            stack.extend(alts)
    while stack:
        node = stack.pop(0)
        if not isinstance(node, dict):
            continue
        raw = node.get("enum")
        if isinstance(raw, list):
            return list(raw)
        for key in ("anyOf", "oneOf"):
            alts = node.get(key)
            if isinstance(alts, list):
                stack.extend(alts)
    return None


def _param_record(name: str, raw: Any, required: set[str]) -> dict[str, Any]:
    schema = _mapping(raw)
    return {
        "required": name in required,
        "default": schema["default"] if "default" in schema else None,
        "type": _published_type(schema),
        "enum": _published_enum(schema),
    }


class RegistryAssembler:
    def __init__(self) -> None:
        self._rows: list[tuple[str, dict[str, Any]]] = []

    def add_tool(self, tool: Any) -> None:
        schema = _mapping(getattr(tool, "inputSchema", None))
        required = {
            item
            for item in (schema.get("required") or [])
            if isinstance(item, str)
        }
        params: dict[str, dict[str, Any]] = {}
        properties = schema.get("properties") or {}
        if not isinstance(properties, dict):
            properties = _mapping(properties)
        for name, pschema in properties.items():
            key = str(name)
            params[key] = _param_record(key, pschema, required)
        self._rows.append(
            (
                str(getattr(tool, "name", "")),
                {
                    "description": getattr(tool, "description", None) or "",
                    "params": params,
                },
            )
        )

    def as_dict(self) -> dict[str, dict[str, Any]]:
        return {name: record for name, record in self._rows}


async def _assemble() -> dict[str, dict]:
    app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))
    listed = await app.list_tools()
    assembler = RegistryAssembler()
    for tool in listed:
        assembler.add_tool(tool)
    return assembler.as_dict()


def resolve_effective_schemas() -> dict[str, dict]:
    return asyncio.run(_assemble())


def _params(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        return {}
    params = spec.get("params")
    return params if isinstance(params, dict) else {}


def _is_ident(token: str) -> bool:
    if not token:
        return False
    first = token[0]
    if not (first.isalpha() or first == "_"):
        return False
    return all(ch.isalnum() or ch == "_" for ch in token)


def _tokenize(text: str) -> list[tuple[int, str]]:
    tokens: list[tuple[int, str]] = []
    index = 0
    length = len(text)
    while index < length:
        ch = text[index]
        if ch.isspace():
            index += 1
            continue
        if ch == "|":
            tokens.append((index, "|"))
            index += 1
            continue
        if ch.isalnum() or ch == "_":
            start = index
            index += 1
            while index < length and (text[index].isalnum() or text[index] == "_"):
                index += 1
            tokens.append((start, text[start:index]))
            continue
        index += 1
    return tokens


def _pipe_runs(text: str) -> list[tuple[int, int, list[str]]]:
    tokens = _tokenize(text)
    runs: list[tuple[int, int, list[str]]] = []
    index = 0
    while index < len(tokens):
        _pos, tok = tokens[index]
        if not _is_ident(tok):
            index += 1
            continue
        parts = [tok]
        start = tokens[index][0]
        cursor = index
        found_pipe = False
        while cursor + 2 < len(tokens) and tokens[cursor + 1][1] == "|":
            nxt = tokens[cursor + 2][1]
            if not _is_ident(nxt):
                break
            parts.append(nxt)
            cursor += 2
            found_pipe = True
        if found_pipe:
            end = tokens[cursor][0] + len(tokens[cursor][1])
            runs.append((start, end, parts))
            index = cursor + 1
        else:
            index += 1
    return runs


def _sentence_bounds(text: str, pos: int) -> tuple[int, int]:
    start = 0
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if ch in ".!?":
            j = i + 1
            while j < length and text[j] in ".!?":
                j += 1
            while j < length and text[j].isspace():
                j += 1
            if j <= pos:
                start = j
                i = j
                continue
            return start, j if j > i else length
        i += 1
    return start, length


def _gap(a0: int, a1: int, b0: int, b1: int) -> int:
    if a1 <= b0:
        return b0 - a1
    if b1 <= a0:
        return a0 - b1
    return 0


def _nearest_param(description: str, start: int, end: int, names: list[str]) -> str | None:
    mentions: list[tuple[int, int, str]] = []
    for name in names:
        cursor = 0
        while True:
            found = description.find(name, cursor)
            if found < 0:
                break
            before = description[found - 1] if found > 0 else " "
            after_i = found + len(name)
            after = description[after_i] if after_i < len(description) else " "
            if (not before.isalnum() and before != "_") and (
                not after.isalnum() and after != "_"
            ):
                mentions.append((found, after_i, name))
            cursor = found + 1
    if not mentions:
        return None
    sent_lo, sent_hi = _sentence_bounds(description, start)
    local = [item for item in mentions if sent_lo <= item[0] < sent_hi]
    pool = local or mentions
    return min(pool, key=lambda item: (_gap(start, end, item[0], item[1]), item[0]))[2]


def _enum_findings(schemas: dict) -> list[dict]:
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
        for start, end, advertised in _pipe_runs(description):
            if len(advertised) < 2:
                continue
            param_name = _nearest_param(description, start, end, names)
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
                        f"token-pipe {advertised} != enum {list(enum_vals)} "
                        f"on {tool_name}.{param_name}"
                    ),
                }
            )
    return findings


def _synonym_findings(schemas: dict) -> list[dict]:
    grouped: dict[int, dict[str, list[str]]] = {}
    for tool_name, spec in schemas.items():
        for param_name in _params(spec):
            group_id = SYNONYM_GROUP_ID.get(str(param_name))
            if group_id is None:
                continue
            bucket = grouped.setdefault(group_id, {})
            bucket.setdefault(str(param_name), []).append(str(tool_name))
    findings: list[dict] = []
    for bucket in grouped.values():
        used = [name for name, tools in bucket.items() if tools]
        if len(used) < 2:
            continue
        summary = {name: list(bucket[name]) for name in used}
        for param_name in used:
            for tool_name in bucket[param_name]:
                findings.append(
                    {
                        "tool": tool_name,
                        "code": CODE_DIVERGENCE,
                        "param": param_name,
                        "evidence": f"group {summary}",
                    }
                )
    return findings


def audit_contracts(schemas: dict | None = None) -> list[dict]:
    resolved = resolve_effective_schemas() if schemas is None else schemas
    if not isinstance(resolved, dict):
        return []
    findings = _synonym_findings(resolved)
    findings.extend(_enum_findings(resolved))
    return findings
