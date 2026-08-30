"""Resolver that collapses falsy defaults to None.

Same contract walk as a legitimate alternative, except published default is
`schema.get("default") or None`. The v7 fixture family never emits 0, 0.0,
False or "". The live tree does.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from dayz_mcp.server import ServerConfig, build_app

SYNONYM_PAIRS = (("classname", "type"),)
PIPE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\s*\|\s*[A-Za-z_][A-Za-z0-9_]*)+")


def _as_schema(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        return dict(raw)
    except Exception:
        return {}


def _published_type(pschema: dict[str, Any]) -> Any:
    declared = pschema.get("type")
    if isinstance(declared, str):
        return None if declared == "null" else declared
    collected: list[str] = []
    if isinstance(declared, list):
        for item in declared:
            if isinstance(item, str) and item != "null" and item not in collected:
                collected.append(item)
        if len(collected) == 1:
            return collected[0]
        return collected or None
    for union_key in ("oneOf", "anyOf"):
        alts = pschema.get(union_key)
        if not isinstance(alts, list):
            continue
        for alt in alts:
            if not isinstance(alt, dict):
                continue
            item = alt.get("type")
            if isinstance(item, str) and item != "null" and item not in collected:
                collected.append(item)
            elif isinstance(item, list):
                for nested in item:
                    if isinstance(nested, str) and nested != "null" and nested not in collected:
                        collected.append(nested)
        if collected:
            return collected[0] if len(collected) == 1 else collected
    return None


def _published_enum(pschema: dict[str, Any]) -> list[Any] | None:
    raw = pschema.get("enum")
    if isinstance(raw, list):
        return list(raw)
    for union_key in ("oneOf", "anyOf"):
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
        "default": schema.get("default") or None,
        "type": _published_type(schema),
        "enum": _published_enum(schema),
    }


async def _index_tools() -> dict[str, dict]:
    app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))
    listed = list(await app.list_tools())
    listed.sort(key=lambda tool: str(getattr(tool, "name", "")))
    out: dict[str, dict] = {}
    for tool in listed:
        schema = _as_schema(getattr(tool, "inputSchema", None) or {})
        required = {
            item for item in (schema.get("required") or []) if isinstance(item, str)
        }
        properties = schema.get("properties") or {}
        params = {
            str(name): _param_record(str(name), pschema, required)
            for name, pschema in properties.items()
        }
        out[tool.name] = {
            "description": tool.description or "",
            "params": params,
        }
    return out


def resolve_effective_schemas() -> dict[str, dict]:
    return asyncio.run(_index_tools())


def _params_of(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        return {}
    params = spec.get("params")
    return params if isinstance(params, dict) else {}


def _sentence_window(text: str, pos: int) -> tuple[int, int]:
    start = 0
    for match in re.finditer(r"[.!?]+(?:\s+|$)", text):
        if match.end() <= pos:
            start = match.end()
        else:
            return start, match.end()
    return start, len(text)


def _gap(a0: int, a1: int, b0: int, b1: int) -> int:
    if a1 <= b0:
        return b0 - a1
    if b1 <= a0:
        return a0 - b1
    return 0


def _closest_param(description: str, start: int, end: int, param_names: list[str]) -> str | None:
    mentions: list[tuple[int, int, str]] = []
    for name in param_names:
        for match in re.finditer(rf"\b{re.escape(name)}\b", description):
            mentions.append((match.start(), match.end(), name))
    if not mentions:
        return None
    sent_lo, sent_hi = _sentence_window(description, start)
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
        params = _params_of(spec)
        if not params:
            continue
        names = [str(name) for name in params]
        for match in PIPE_RE.finditer(description):
            advertised = [part.strip() for part in match.group(0).split("|") if part.strip()]
            if len(advertised) < 2:
                continue
            param_name = _closest_param(description, match.start(), match.end(), names)
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
                    "code": "DESC-ENUM-MISMATCH",
                    "param": str(param_name),
                    "evidence": (
                        f"pipe {advertised} disagrees with enum {list(enum_vals)} "
                        f"on {tool_name}.{param_name}"
                    ),
                }
            )
    return findings


def _synonym_findings(schemas: dict) -> list[dict]:
    findings: list[dict] = []
    for pair in SYNONYM_PAIRS:
        synonym_set = frozenset(pair)
        usage: dict[str, list[str]] = {name: [] for name in synonym_set}
        for tool_name, spec in schemas.items():
            for param_name in _params_of(spec):
                if param_name in synonym_set:
                    usage[param_name].append(str(tool_name))
        used = [name for name, tools in usage.items() if tools]
        if len(used) < 2:
            continue
        summary = {name: list(usage[name]) for name in used}
        for param_name in used:
            for tool_name in usage[param_name]:
                findings.append(
                    {
                        "tool": tool_name,
                        "code": "PARAM-NAME-DIVERGENCE",
                        "param": param_name,
                        "evidence": f"{param_name} coexists with {summary}",
                    }
                )
    return findings


def audit_contracts(schemas: dict | None = None) -> list[dict]:
    resolved = resolve_effective_schemas() if schemas is None else schemas
    if not isinstance(resolved, dict):
        return []
    findings = _enum_findings(resolved)
    findings.extend(_synonym_findings(resolved))
    findings.sort(key=lambda item: (item["code"], item["tool"], item["param"]))
    return findings
