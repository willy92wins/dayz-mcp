"""HOLDOUT H2 - sobre-denuncia.

La resolucion es real. El auditor denuncia TODO nombre de parametro compartido
por dos o mas tools, con la forma de hallazgo correcta. Sobre el arbol real los
8 hallazgos verdaderos son un SUBCONJUNTO de lo que devuelve, junto a decenas de
falsos (`pos`, `type`, `name`, `timeout_s`...).

Que gap prueba: si el oraculo del gate comprueba "estan presentes los hallazgos
esperados" en vez de "el conjunto es EXACTAMENTE el esperado", este modulo pasa
siendo inservible -- un auditor que denuncia todo no distingue nada.
"""
from __future__ import annotations

import asyncio
from typing import Any

from dayz_mcp.server import ServerConfig, build_app

CODE_PARAM_NAME_DIVERGENCE = "PARAM-NAME-DIVERGENCE"


def resolve_effective_schemas() -> dict[str, dict]:
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
    required = {i for i in (schema.get("required") or []) if isinstance(i, str)}
    params: dict[str, dict[str, Any]] = {}
    for name, pschema in properties.items():
        if not isinstance(pschema, dict):
            pschema = {}
        params[str(name)] = {
            "required": str(name) in required,
            "default": pschema["default"] if "default" in pschema else None,
            "type": _json_type(pschema),
            "enum": _json_enum(pschema),
        }
    return {"description": tool.description or "", "params": params}


def _json_type(pschema: dict[str, Any]) -> Any:
    named: list[str] = []
    _append(named, pschema.get("type"))
    for key in ("anyOf", "oneOf"):
        for branch in pschema.get(key) or []:
            if isinstance(branch, dict):
                _append(named, branch.get("type"))
    if not named:
        return None
    return named[0] if len(named) == 1 else named


def _append(named: list[str], value: Any) -> None:
    if isinstance(value, str):
        if value != "null" and value not in named:
            named.append(value)
    elif isinstance(value, list):
        for item in value:
            _append(named, item)


def _json_enum(pschema: dict[str, Any]) -> Any:
    values = pschema.get("enum")
    if isinstance(values, list) and values:
        return list(values)
    for key in ("anyOf", "oneOf"):
        for branch in pschema.get(key) or []:
            if isinstance(branch, dict):
                inner = branch.get("enum")
                if isinstance(inner, list) and inner:
                    return list(inner)
    return None


def _tool_params(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        return {}
    params = spec.get("params")
    return params if isinstance(params, dict) else {}


def audit_contracts(schemas: dict | None = None) -> list[dict]:
    """Denuncia CUALQUIER nombre de parametro que aparezca en 2+ tools."""
    resolved = resolve_effective_schemas() if schemas is None else schemas
    if not isinstance(resolved, dict):
        return []
    usage: dict[str, list[str]] = {}
    for tool_name, spec in resolved.items():
        for param_name in _tool_params(spec):
            usage.setdefault(str(param_name), []).append(str(tool_name))
    findings: list[dict] = []
    for param_name, tools in usage.items():
        if len(tools) < 2:
            continue
        for tool_name in tools:
            findings.append({
                "tool": tool_name,
                "code": CODE_PARAM_NAME_DIVERGENCE,
                "param": param_name,
                "evidence": (
                    f"{tool_name} exposes {param_name!r}; shared across tools "
                    f"as {{{param_name!r}: {sorted(tools)}}}"
                ),
            })
    findings.sort(key=lambda i: (i["code"], i["tool"], i["param"]))
    return findings
