"""Implementacion alternativa legitima (ronda 2).

Recorrido distinto a `legit_alt.py` y al modulo real: cola FIFO sobre el
esquema, tools en orden inverso, sinonimos por etiqueta de grupo, auditor que
emite divergencias de nombre ANTES que mismatches de enum, y un escaner de
pipes a mano (sin regex).
"""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Any

from dayz_mcp.server import ServerConfig, build_app

GROUP_LABEL = {
    "type": "entity-selector",
    "classname": "entity-selector",
}
CODE_NAME = "PARAM-NAME-DIVERGENCE"
CODE_ENUM = "DESC-ENUM-MISMATCH"


def _as_mapping(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        return dict(raw)
    except Exception:
        return {}


def _ident_end(text: str, index: int) -> int | None:
    if index >= len(text):
        return None
    ch = text[index]
    if not (ch.isalpha() or ch == "_"):
        return None
    index += 1
    while index < len(text) and (text[index].isalnum() or text[index] == "_"):
        index += 1
    return index


def _scan_pipe_runs(text: str) -> list[tuple[int, int, list[str]]]:
    runs: list[tuple[int, int, list[str]]] = []
    index = 0
    length = len(text)
    while index < length:
        end = _ident_end(text, index)
        if end is None:
            index += 1
            continue
        parts = [text[index:end]]
        cursor = end
        found_pipe = False
        while True:
            skip = cursor
            while skip < length and text[skip].isspace():
                skip += 1
            if skip >= length or text[skip] != "|":
                break
            skip += 1
            while skip < length and text[skip].isspace():
                skip += 1
            nxt = _ident_end(text, skip)
            if nxt is None:
                break
            parts.append(text[skip:nxt])
            cursor = nxt
            found_pipe = True
        if found_pipe:
            runs.append((index, cursor, parts))
            index = cursor
        else:
            index = end
    return runs


def _sentence_slice(text: str, pos: int) -> tuple[int, int]:
    start = 0
    i = 0
    while i < len(text):
        if text[i] in ".!?" :
            j = i + 1
            while j < len(text) and text[j].isspace():
                j += 1
            if j <= pos:
                start = j
            else:
                return start, j
            i = j
            continue
        i += 1
    return start, len(text)


def _gap(a0: int, a1: int, b0: int, b1: int) -> int:
    if a1 <= b0:
        return b0 - a1
    if b1 <= a0:
        return a0 - b1
    return 0


def _nearest_name(description: str, start: int, end: int, names: list[str]) -> str | None:
    hits: list[tuple[int, int, str]] = []
    for name in names:
        needle = name
        pos = 0
        while True:
            found = description.find(needle, pos)
            if found < 0:
                break
            before = found == 0 or not (description[found - 1].isalnum() or description[found - 1] == "_")
            after_index = found + len(needle)
            after = after_index >= len(description) or not (
                description[after_index].isalnum() or description[after_index] == "_"
            )
            if before and after:
                hits.append((found, after_index, name))
            pos = found + 1
    if not hits:
        return None
    sent_lo, sent_hi = _sentence_slice(description, start)
    local = [hit for hit in hits if sent_lo <= hit[0] < sent_hi]
    pool = local or hits
    return min(pool, key=lambda hit: (_gap(start, end, hit[0], hit[1]), hit[0]))[2]


def _collapse_declared(declared: Any) -> Any:
    if isinstance(declared, str):
        return None if declared == "null" else declared
    if isinstance(declared, list):
        named: list[str] = []
        work = deque(declared)
        while work:
            item = work.popleft()
            if isinstance(item, str):
                if item != "null" and item not in named:
                    named.append(item)
            elif isinstance(item, list):
                work.extend(item)
        if not named:
            return None
        if len(named) == 1:
            return named[0]
        return named
    return None


def _type_from_schema(pschema: dict[str, Any]) -> Any:
    if "type" in pschema:
        return _collapse_declared(pschema.get("type"))
    for union_key in ("anyOf", "oneOf"):
        alts = pschema.get(union_key)
        if not isinstance(alts, list):
            continue
        named: list[str] = []
        for alt in alts:
            if not isinstance(alt, dict):
                continue
            collapsed = _collapse_declared(alt.get("type"))
            if isinstance(collapsed, str):
                if collapsed not in named:
                    named.append(collapsed)
            elif isinstance(collapsed, list):
                for item in collapsed:
                    if item not in named:
                        named.append(item)
        if named:
            return named[0] if len(named) == 1 else named
    return None


def _enum_from_schema(pschema: dict[str, Any]) -> list[Any] | None:
    raw = pschema.get("enum")
    if isinstance(raw, list):
        return list(raw)
    for union_key in ("anyOf", "oneOf"):
        alts = pschema.get(union_key)
        if not isinstance(alts, list):
            continue
        for alt in alts:
            if isinstance(alt, dict) and isinstance(alt.get("enum"), list):
                return list(alt["enum"])
    return None


def _param_record(name: str, pschema: Any, required: set[str]) -> dict[str, Any]:
    schema = pschema if isinstance(pschema, dict) else {}
    return {
        "required": name in required,
        "default": schema["default"] if "default" in schema else None,
        "type": _type_from_schema(schema),
        "enum": _enum_from_schema(schema),
    }


async def _walk_registry() -> dict[str, dict]:
    app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))
    listed = list(await app.list_tools())
    listed.sort(key=lambda tool: str(getattr(tool, "name", "")), reverse=True)
    out: dict[str, dict] = {}
    for tool in listed:
        schema = _as_mapping(getattr(tool, "inputSchema", None) or {})
        required = {
            item for item in (schema.get("required") or []) if isinstance(item, str)
        }
        properties = schema.get("properties") or {}
        names = [str(name) for name in properties]
        names.sort(reverse=True)
        params = {
            name: _param_record(name, properties.get(name), required) for name in names
        }
        out[str(tool.name)] = {
            "description": tool.description or "",
            "params": params,
        }
    return out


def resolve_effective_schemas() -> dict[str, dict]:
    return asyncio.run(_walk_registry())


def _params_of(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        return {}
    params = spec.get("params")
    return params if isinstance(params, dict) else {}


def _name_findings(schemas: dict) -> list[dict]:
    grouped: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for tool_name, spec in schemas.items():
        for param_name in _params_of(spec):
            label = GROUP_LABEL.get(str(param_name))
            if label is None:
                continue
            grouped[label][str(param_name)].append(str(tool_name))
    findings: list[dict] = []
    for _label, usage in grouped.items():
        used = [name for name, tools in usage.items() if tools]
        if len(used) < 2:
            continue
        summary = {name: list(usage[name]) for name in used}
        for param_name in used:
            for tool_name in usage[param_name]:
                findings.append(
                    {
                        "tool": tool_name,
                        "code": CODE_NAME,
                        "param": param_name,
                        "evidence": f"group {_label} split across {summary}",
                    }
                )
    return findings


def _enum_findings(schemas: dict) -> list[dict]:
    findings: list[dict] = []
    for tool_name, spec in schemas.items():
        if not isinstance(spec, dict):
            continue
        description = spec.get("description") or ""
        if not isinstance(description, str) or not description:
            continue
        params = _params_of(spec)
        if not params:
            continue
        names = [str(name) for name in params]
        for start, end, advertised in _scan_pipe_runs(description):
            if len(advertised) < 2:
                continue
            param_name = _nearest_name(description, start, end, names)
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
                        f"pipe {advertised} != enum {list(enum_vals)} "
                        f"on {tool_name}.{param_name}"
                    ),
                }
            )
    return findings


def audit_contracts(schemas: dict | None = None) -> list[dict]:
    resolved = resolve_effective_schemas() if schemas is None else schemas
    if not isinstance(resolved, dict):
        return []
    findings = _name_findings(resolved)
    findings.extend(_enum_findings(resolved))
    findings.sort(key=lambda item: (item["code"], item["tool"], item["param"]))
    return findings
