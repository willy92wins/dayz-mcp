"""C5: resolutor real para la app inyectada; auditor deliberadamente inutil."""
from __future__ import annotations

import asyncio

from dayz_mcp.server import ServerConfig, build_app


def _json_type(schema):
    declared = schema.get("type")
    if isinstance(declared, str):
        return None if declared == "null" else declared
    if isinstance(declared, list):
        values = []
        for item in declared:
            if isinstance(item, str) and item != "null" and item not in values:
                values.append(item)
        return values[0] if len(values) == 1 else values or None
    values = []
    for key in ("anyOf", "oneOf"):
        for alternative in schema.get(key) or []:
            item = alternative.get("type") if isinstance(alternative, dict) else None
            if isinstance(item, str) and item != "null" and item not in values:
                values.append(item)
        if values:
            return values[0] if len(values) == 1 else values
    return None


def _json_enum(schema):
    if isinstance(schema.get("enum"), list):
        return list(schema["enum"])
    for key in ("anyOf", "oneOf"):
        for alternative in schema.get(key) or []:
            if isinstance(alternative, dict) and isinstance(alternative.get("enum"), list):
                return list(alternative["enum"])
    return None


async def _resolve_async():
    app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))
    out = {}
    for tool in await app.list_tools():
        schema = tool.inputSchema or {}
        required = set(schema.get("required") or [])
        params = {}
        for name, param_schema in (schema.get("properties") or {}).items():
            params[name] = {
                "required": name in required,
                "default": param_schema["default"] if "default" in param_schema else None,
                "type": _json_type(param_schema),
                "enum": _json_enum(param_schema),
            }
        out[tool.name] = {"description": tool.description or "", "params": params}
    return out


def resolve_effective_schemas():
    return asyncio.run(_resolve_async())


def audit_contracts(schemas=None):
    return []
