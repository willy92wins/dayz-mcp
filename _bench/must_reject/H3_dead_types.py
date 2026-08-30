"""HOLDOUT H3 - tipos muertos.

Los NOMBRES son todos correctos: mismas tools, mismos parametros, alias incluido
(scene_raycast publica 'from', no 'from_pos'), misma descripcion, mismo
`required`. El auditor es real y da los 8 hallazgos exactos.

Lo unico muerto es el CONTENIDO de cada parametro: `type` siempre 'string',
`enum` siempre None, `default` siempre None. Es decir, el modulo publica un
esquema efectivo que MIENTE sobre los tipos -- justo lo que un consumidor
necesita para generar clientes o validar argumentos.

Que gap prueba: si el oraculo compara el CONJUNTO DE NOMBRES de parametros en
vez del registro completo, este modulo pasa. Es la forma exacta del defecto que
P2 del brief pide cerrar, aplicada a la mitad que suele quedar sin comprobar.
"""
from __future__ import annotations

import asyncio
from typing import Any

from dayz_mcp.server import ServerConfig, build_app

PARAM_SYNONYM_SETS: tuple[frozenset[str], ...] = (
    frozenset({"type", "classname"}),
)
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
    for name in properties:
        # Nombres exactos. Contenido muerto.
        params[str(name)] = {
            "required": str(name) in required,
            "default": None,
            "type": "string",
            "enum": None,
        }
    return {"description": tool.description or "", "params": params}


def _tool_params(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        return {}
    params = spec.get("params")
    return params if isinstance(params, dict) else {}


def audit_contracts(schemas: dict | None = None) -> list[dict]:
    """Auditor real: la divergencia de nombres se detecta bien."""
    resolved = resolve_effective_schemas() if schemas is None else schemas
    if not isinstance(resolved, dict):
        return []
    findings: list[dict] = []
    for synonym_set in PARAM_SYNONYM_SETS:
        usage: dict[str, list[str]] = {name: [] for name in synonym_set}
        for tool_name, spec in resolved.items():
            for param_name in _tool_params(spec):
                if param_name in synonym_set:
                    usage[param_name].append(str(tool_name))
        used_names = [n for n, tools in usage.items() if tools]
        if len(used_names) < 2:
            continue
        usage_summary = {n: list(usage[n]) for n in used_names}
        for param_name in used_names:
            for tool_name in usage[param_name]:
                findings.append({
                    "tool": tool_name,
                    "code": CODE_PARAM_NAME_DIVERGENCE,
                    "param": param_name,
                    "evidence": (
                        f"{tool_name} exposes {param_name!r}; synonym set "
                        f"{sorted(synonym_set)} coexists across tools as "
                        f"{usage_summary}"
                    ),
                })
    findings.sort(
        key=lambda item: (
            str(item.get("code") or ""),
            str(item.get("tool") or ""),
            str(item.get("param") or ""),
        )
    )
    return findings
