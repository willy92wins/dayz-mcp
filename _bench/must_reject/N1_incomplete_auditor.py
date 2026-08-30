"""HOLDOUT N1 - lento pero correcto.  VEREDICTO ESPERADO: ACCEPT.

Implementacion integra y autocontenida -- no importa el modulo real, para que
`sys.modules` no le devuelva una copia que ligo `build_app` antes del parche --
con 6 segundos de retraso en la resolucion. Una implementacion legitima puede
ser lenta: cachear, validar de mas, o correr en una maquina cargada.

Que gap prueba: el timeout de A4. Un padre que elija un limite tacano convierte
"lento" en "inservible" y crea un falso negativo silencioso, que es el peor de
los dos errores porque no se nota hasta que rechaza trabajo bueno.

No es un impostor: es el control de que el aislamiento no se pago con un gate
que rechaza lo legitimo.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from dayz_mcp.server import ServerConfig, build_app

PARAM_SYNONYM_SETS: tuple[frozenset[str], ...] = (frozenset({"type", "classname"}),)
CODE_PARAM_NAME_DIVERGENCE = "PARAM-NAME-DIVERGENCE"


def resolve_effective_schemas() -> dict[str, dict]:
    time.sleep(6.0)
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
