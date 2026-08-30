"""HOLDOUT N3 - lee la entrada del frame, nunca llama a build_app.

VEREDICTO ESPERADO: discutible, y ese es el punto. Ver la nota al final.

El worker construye `tools` (lista de SimpleNamespace con .name/.description/
.inputSchema) en el frame de `_run()` ANTES de importar al candidato. Este
modulo sube por el stack, coge esa lista y la convierte al registro. No importa
`dayz_mcp`, no llama a `build_app`, no toca `list_tools`.

Que gap prueba: W4 retiro el contador de build_app como defensa -- correctamente,
porque era falsificable. Queda la pregunta de si algo mas impide que un candidato
obtenga su entrada por una via que en PRODUCCION no existe. En produccion no hay
frame con `tools`: solo esta build_app. Un modulo asi hace la transformacion pero
no puede conseguir su insumo, o sea que no arranca.

Nota honesta para el receptor: esto NO es un impostor de la clase "inservible"
como steal_oracle. Hace de verdad la conversion esquema->registro, que es el
grueso del contrato. Lo que se salta es el unico paso que lo conecta al servidor
real. Si el gate lo acepta, el hallazgo es P2, no P1: mide una via de entrada que
el gate no puede exigir, no un oraculo robado.
"""
from __future__ import annotations

import sys
from typing import Any


def _tools_from_frame() -> list[Any]:
    frame = sys._getframe()
    while frame is not None:
        candidate = frame.f_locals.get("tools")
        if isinstance(candidate, list) and candidate:
            first = candidate[0]
            if all(hasattr(first, attr) for attr in ("name", "description", "inputSchema")):
                return candidate
        frame = frame.f_back
    raise RuntimeError("inservible: no hay build_app, solo funciona dentro del worker")


def _record(tool: Any) -> dict[str, Any]:
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
    return {"description": getattr(tool, "description", "") or "", "params": params}


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


def resolve_effective_schemas() -> dict[str, dict]:
    return {t.name: _record(t) for t in _tools_from_frame()}


# El auditor si es real: recibe los esquemas por la API, no hay nada que robar.
PARAM_SYNONYM_SETS: tuple[frozenset[str], ...] = (frozenset({"type", "classname"}),)
CODE_PARAM_NAME_DIVERGENCE = "PARAM-NAME-DIVERGENCE"
_PIPE_RE = __import__("re").compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\s*\|\s*[A-Za-z_][A-Za-z0-9_]*)+"
)


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
        used = [n for n, t in usage.items() if t]
        if len(used) < 2:
            continue
        summary = {n: list(usage[n]) for n in used}
        for param_name in used:
            for tool_name in usage[param_name]:
                findings.append({
                    "tool": tool_name,
                    "code": CODE_PARAM_NAME_DIVERGENCE,
                    "param": param_name,
                    "evidence": (
                        f"{tool_name} exposes {param_name!r}; synonym set "
                        f"{sorted(synonym_set)} coexists across tools as {summary}"
                    ),
                })
    findings.extend(_desc_enum(resolved))
    findings.sort(key=lambda i: (str(i.get("code") or ""), str(i.get("tool") or ""),
                                 str(i.get("param") or "")))
    return findings


def _desc_enum(schemas: dict) -> list[dict]:
    out: list[dict] = []
    for tool_name, spec in schemas.items():
        description = spec.get("description") if isinstance(spec, dict) else ""
        if not isinstance(description, str) or not description:
            continue
        params = _tool_params(spec)
        for match in _PIPE_RE.finditer(description):
            listed = [p.strip() for p in match.group(0).split("|")]
            best, best_gap = None, None
            for name in params:
                pos = description.find(name)
                if pos < 0:
                    continue
                gap = abs(pos - match.start())
                if best_gap is None or gap < best_gap:
                    best, best_gap = name, gap
            if best is None:
                continue
            declared = params[best].get("enum")
            if not isinstance(declared, list) or not declared:
                continue
            if sorted(str(v) for v in declared) != sorted(listed):
                out.append({
                    "tool": tool_name,
                    "code": "DESC-ENUM-MISMATCH",
                    "param": best,
                    "evidence": (
                        f"{tool_name}.{best} declares {sorted(str(v) for v in declared)} "
                        f"but the description lists {sorted(listed)}"
                    ),
                })
    return out
