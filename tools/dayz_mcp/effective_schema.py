"""Effective MCP tool schemas after build_app, and a contract auditor.

The public contract of a DayZ-MCP tool is the schema FastMCP exposes after
build_app (aliases already applied), not the Python function signature.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from dayz_mcp.server import ServerConfig, build_app

# Same caller-facing concept, different param names. Detected by the names
# themselves — never by a wired list of tool names.
PARAM_SYNONYM_SETS: tuple[frozenset[str], ...] = (
    frozenset({"type", "classname"}),
)

CODE_PARAM_NAME_DIVERGENCE = "PARAM-NAME-DIVERGENCE"
CODE_DESC_ENUM_MISMATCH = "DESC-ENUM-MISMATCH"

_PIPE_ENUM_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\s*\|\s*[A-Za-z_][A-Za-z0-9_]*)+"
)


def resolve_effective_schemas() -> dict[str, dict]:
    """Return the post-build_app schema of every registered tool, keyed by name."""
    return asyncio.run(_resolve_async())


async def _resolve_async() -> dict[str, dict]:
    app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))
    listed = await app.list_tools()
    return {tool.name: _record_from_tool(tool) for tool in listed}


def _record_from_tool(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "inputSchema", None) or {}
    if not isinstance(schema, dict):
        schema = dict(schema)
    properties = schema.get("properties") or {}
    required = {
        item for item in (schema.get("required") or []) if isinstance(item, str)
    }
    params: dict[str, dict[str, Any]] = {}
    for name, pschema in properties.items():
        params[str(name)] = _param_from_schema(str(name), pschema, required)
    return {
        "description": tool.description or "",
        "params": params,
    }


def _param_from_schema(
    name: str, pschema: Any, required: set[str]
) -> dict[str, Any]:
    if not isinstance(pschema, dict):
        pschema = {}
    return {
        "required": name in required,
        "default": pschema["default"] if "default" in pschema else None,
        "type": _json_type(pschema),
        "enum": _json_enum(pschema),
    }


def _collapse_types(named: list[str]) -> str | list[str] | None:
    if not named:
        return None
    if len(named) == 1:
        return named[0]
    return named


def _append_json_type(named: list[str], value: Any) -> None:
    if isinstance(value, str):
        if value != "null" and value not in named:
            named.append(value)
        return
    if isinstance(value, list):
        for item in value:
            _append_json_type(named, item)


def _json_type(pschema: dict[str, Any]) -> str | list[str] | None:
    declared = pschema.get("type")
    if isinstance(declared, str):
        return declared if declared != "null" else None
    if isinstance(declared, list):
        named: list[str] = []
        _append_json_type(named, declared)
        return _collapse_types(named)
    named = []
    for key in ("anyOf", "oneOf"):
        alts = pschema.get(key)
        if not isinstance(alts, list):
            continue
        for alt in alts:
            if isinstance(alt, dict):
                _append_json_type(named, alt.get("type"))
        if named:
            return _collapse_types(named)
    return None


def _json_enum(pschema: dict[str, Any]) -> list[Any] | None:
    raw = pschema.get("enum")
    if isinstance(raw, list):
        return list(raw)
    for key in ("anyOf", "oneOf"):
        alts = pschema.get(key)
        if not isinstance(alts, list):
            continue
        for alt in alts:
            if isinstance(alt, dict) and isinstance(alt.get("enum"), list):
                return list(alt["enum"])
    return None


def audit_contracts(schemas: dict | None = None) -> list[dict]:
    """Audit effective schemas against their descriptions.

    ``schemas is None`` resolves the live tree. A dict is audited as-is so
    the checker can be proven against names that do not exist in this tree.
    """
    resolved = resolve_effective_schemas() if schemas is None else schemas
    if not isinstance(resolved, dict):
        return []
    findings: list[dict] = []
    findings.extend(_audit_param_name_divergence(resolved))
    findings.extend(_audit_desc_enum_mismatch(resolved))
    findings.sort(
        key=lambda item: (
            str(item.get("code") or ""),
            str(item.get("tool") or ""),
            str(item.get("param") or ""),
        )
    )
    return findings


def _tool_params(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        return {}
    params = spec.get("params")
    return params if isinstance(params, dict) else {}


def _audit_param_name_divergence(schemas: dict) -> list[dict]:
    findings: list[dict] = []
    for synonym_set in PARAM_SYNONYM_SETS:
        usage: dict[str, list[str]] = {name: [] for name in synonym_set}
        for tool_name, spec in schemas.items():
            for param_name in _tool_params(spec):
                if param_name in synonym_set:
                    usage[param_name].append(str(tool_name))
        used_names = [name for name, tools in usage.items() if tools]
        if len(used_names) < 2:
            continue
        usage_summary = {
            name: list(usage[name]) for name in used_names
        }
        for param_name in used_names:
            for tool_name in usage[param_name]:
                findings.append(
                    {
                        "tool": tool_name,
                        "code": CODE_PARAM_NAME_DIVERGENCE,
                        "param": param_name,
                        "evidence": (
                            f"{tool_name} exposes {param_name!r}; synonym set "
                            f"{sorted(synonym_set)} coexists across tools as "
                            f"{usage_summary}"
                        ),
                    }
                )
    return findings


def _sentence_bounds(text: str, pos: int) -> tuple[int, int]:
    start = 0
    for match in re.finditer(r"[.!?]+(?:\s+|$)", text):
        if match.end() <= pos:
            start = match.end()
        else:
            return start, match.end()
    return start, len(text)


def _span_gap(a0: int, a1: int, b0: int, b1: int) -> int:
    if a1 <= b0:
        return b0 - a1
    if b1 <= a0:
        return a0 - b1
    return 0


def _param_mentions(
    description: str, param_names: list[str]
) -> list[tuple[int, int, str]]:
    mentions: list[tuple[int, int, str]] = []
    for name in param_names:
        for match in re.finditer(rf"\b{re.escape(name)}\b", description):
            mentions.append((match.start(), match.end(), name))
    return mentions


def _nearest_param_for_span(
    description: str,
    start: int,
    end: int,
    mentions: list[tuple[int, int, str]],
) -> str | None:
    if not mentions:
        return None
    sent_lo, sent_hi = _sentence_bounds(description, start)
    local = [item for item in mentions if sent_lo <= item[0] < sent_hi]
    pool = local or mentions

    def sort_key(mention: tuple[int, int, str]) -> tuple[int, int]:
        return (_span_gap(start, end, mention[0], mention[1]), mention[0])

    return min(pool, key=sort_key)[2]


def _audit_desc_enum_mismatch(schemas: dict) -> list[dict]:
    findings: list[dict] = []
    for tool_name, spec in schemas.items():
        if not isinstance(spec, dict):
            continue
        description = spec.get("description") or ""
        if not isinstance(description, str) or not description:
            continue
        params = _tool_params(spec)
        if not params:
            continue
        mentions = _param_mentions(description, [str(name) for name in params])
        for match in _PIPE_ENUM_RE.finditer(description):
            pipe_vals = [part.strip() for part in match.group(0).split("|")]
            pipe_vals = [item for item in pipe_vals if item]
            if len(pipe_vals) < 2:
                continue
            param_name = _nearest_param_for_span(
                description, match.start(), match.end(), mentions
            )
            if param_name is None:
                continue
            pinfo = params.get(param_name)
            if not isinstance(pinfo, dict):
                continue
            enum_vals = pinfo.get("enum")
            if not isinstance(enum_vals, list):
                continue
            if set(pipe_vals) == set(enum_vals):
                continue
            findings.append(
                {
                    "tool": str(tool_name),
                    "code": CODE_DESC_ENUM_MISMATCH,
                    "param": str(param_name),
                    "evidence": (
                        f"{tool_name}.{param_name} description lists "
                        f"{pipe_vals} but schema enum is {list(enum_vals)}"
                    ),
                }
            )
    return findings
