#!/usr/bin/env python3
"""Inventory real schema shapes and prove that v8 fixtures generate them."""
from __future__ import annotations

import asyncio
import re
import sys
import warnings
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
SEED_COUNT = 64
FAMILY_LABELS = ("COVERAGE", "COVERAGE:expanded-realism")
PIPE_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\s*\|\s*[A-Za-z_][A-Za-z0-9_]*)+"
)

sys.path.insert(0, str(ROOT))
import _gate as gate  # noqa: E402

for dependency_path in reversed(gate._dependency_paths(TOOLS)):
    if dependency_path not in sys.path:
        sys.path.insert(0, dependency_path)

warnings.filterwarnings(
    "ignore",
    message=r"Field 'lifespan' has an incomplete definition:.*",
)
from dayz_mcp.server import ServerConfig, build_app  # noqa: E402


Inventory = dict[str, Counter[str]]


def _mark(inventory: Inventory, axis: str, value: str, count: int = 1) -> None:
    inventory.setdefault(axis, Counter())[value] += count


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        return dict(value)
    except (TypeError, ValueError):
        return {}


def _param_count_value(count: int) -> str:
    if count == 0:
        return "cero"
    if count == 1:
        return "uno"
    return "varios"


def _inventory_description(
    inventory: Inventory,
    axis: str,
    owner: Any,
    param_names: Iterable[str],
) -> None:
    if isinstance(owner, dict):
        present = "description" in owner and owner["description"] is not None
        raw = owner.get("description")
    else:
        present = hasattr(owner, "description") and owner.description is not None
        raw = getattr(owner, "description", None)
    if not present:
        _mark(inventory, axis, "ausente")
        return
    description = raw if isinstance(raw, str) else str(raw)
    if not description:
        _mark(inventory, axis, "vacia")
        return
    matches = list(PIPE_RE.finditer(description))
    if not matches:
        _mark(inventory, axis, "prosa-sin-pipes")
        return
    if len(matches) >= 2:
        _mark(inventory, axis, "dos-grupos-de-pipes")
    names = [str(name) for name in param_names]
    mentions = [
        match
        for name in names
        for match in re.finditer(rf"\b{re.escape(name)}\b", description)
    ]
    for pipe_match in matches:
        pipe = pipe_match.group(0)
        if re.search(r"\s\||\|\s", pipe):
            _mark(inventory, axis, "pipes-con-espacios")
        else:
            _mark(inventory, axis, "pipes-sin-espacios")
        if any(mention.end() <= pipe_match.start() for mention in mentions):
            _mark(inventory, axis, "nombre-antes-de-pipes")
        if any(mention.start() >= pipe_match.end() for mention in mentions):
            _mark(inventory, axis, "pipes-antes-del-nombre")


def _inventory_default(inventory: Inventory, item: dict[str, Any]) -> None:
    axis = "resolver/default [params]"
    if "default" not in item:
        _mark(inventory, axis, "ausente")
        return
    value = item["default"]
    if value is None:
        _mark(inventory, axis, "null")
        return
    if not value:
        _mark(inventory, axis, "presente-falsy")
        if type(value) is bool:
            detail = "False"
        elif type(value) is int:
            detail = "0"
        elif type(value) is float:
            detail = "0.0"
        elif type(value) is str:
            detail = "cadena-vacia"
        else:
            detail = type(value).__name__
        _mark(inventory, axis, "presente-falsy:" + detail)
        return
    _mark(inventory, axis, "presente-verdadero")


def _inventory_param_schema(inventory: Inventory, raw_item: Any) -> None:
    item = _mapping(raw_item)
    _inventory_default(inventory, item)

    declared = item.get("type")
    if isinstance(declared, str):
        _mark(inventory, "resolver/type [params]", "string-suelto")
    elif isinstance(declared, list):
        _mark(inventory, "resolver/type [params]", "lista-de-tipos")
    elif "type" not in item:
        _mark(inventory, "resolver/type [params]", "ausente")
    else:
        _mark(inventory, "resolver/type [params]", "otro")

    enum = item.get("enum")
    if not isinstance(enum, list):
        _mark(inventory, "resolver/enum [params]", "ausente")
    else:
        _mark(inventory, "resolver/enum [params]", "presente")
        if any(not isinstance(value, str) for value in enum):
            _mark(inventory, "resolver/enum [params]", "presente-no-string")

    published_type = gate._schema_type(item)
    published_types = (
        published_type if isinstance(published_type, list) else [published_type]
    )
    if "boolean" in published_types:
        value = "con-enum" if isinstance(enum, list) else "sin-enum"
        _mark(inventory, "resolver/boolean [params boolean]", value)

    for key in sorted(item):
        _mark(inventory, "resolver/claves-schema-param [apariciones]", str(key))
    if "items" in item:
        _mark(inventory, "resolver/items-directo [params]", "presente")
    if "additionalProperties" in item:
        _mark(
            inventory,
            "resolver/additionalProperties-directo [params]",
            "presente",
        )

    union_found = False
    for union_key in ("anyOf", "oneOf"):
        alternatives = item.get(union_key)
        if not isinstance(alternatives, list):
            continue
        union_found = True
        _mark(inventory, "resolver/tipo-de-union [params]", union_key)
        for alternative in alternatives:
            branch = _mapping(alternative)
            keys = set(branch)
            if keys == {"type"}:
                value = "rama-{type}"
            elif keys == {"type", "enum"}:
                value = "rama-{type,enum}"
            elif "items" in keys:
                value = "rama-con-items"
            elif "additionalProperties" in keys:
                value = "rama-con-additionalProperties"
            else:
                value = "rama-otras-claves:" + ",".join(sorted(map(str, keys)))
            _mark(inventory, "resolver/forma-rama-union [ramas]", value)
    if not union_found:
        _mark(inventory, "resolver/tipo-de-union [params]", "sin-union")


def _inventory_resolver_tools(tools: Iterable[Any]) -> Inventory:
    inventory: Inventory = {}
    for tool in tools:
        schema = _mapping(getattr(tool, "inputSchema", None) or {})
        properties = _mapping(schema.get("properties") or {})
        param_names = [str(name) for name in properties]
        _mark(
            inventory,
            "resolver/params-por-tool [tools]",
            _param_count_value(len(properties)),
        )
        _inventory_description(
            inventory, "resolver/descripcion [tools/grupos]", tool, param_names
        )
        for key in sorted(schema):
            _mark(inventory, "resolver/claves-schema-tool [apariciones]", str(key))

        if "required" not in schema:
            _mark(inventory, "resolver/required [tools]", "clave-ausente")
        else:
            required = schema.get("required")
            if isinstance(required, list) and not required:
                _mark(inventory, "resolver/required [tools]", "lista-vacia")
            elif isinstance(required, list):
                _mark(inventory, "resolver/required [tools]", "lista-no-vacia")
                required_names = {item for item in required if isinstance(item, str)}
                if 0 < len(required_names) < len(properties):
                    _mark(inventory, "resolver/required [tools]", "parcial")
                elif properties and len(required_names) >= len(properties):
                    _mark(inventory, "resolver/required [tools]", "todos")
            else:
                _mark(inventory, "resolver/required [tools]", "valor-no-lista")

        for item in properties.values():
            _inventory_param_schema(inventory, item)
    return inventory


def _inventory_audit_cases(cases: Iterable[dict[str, dict]]) -> Inventory:
    inventory: Inventory = {}
    for schemas in cases:
        total_params = 0
        for spec in schemas.values():
            record = _mapping(spec)
            params = _mapping(record.get("params") or {})
            total_params += len(params)
            _mark(
                inventory,
                "auditor/params-por-tool [tools]",
                _param_count_value(len(params)),
            )
            _inventory_description(
                inventory,
                "auditor/descripcion [tools/grupos]",
                record,
                [str(name) for name in params],
            )
        _mark(
            inventory,
            "auditor/params-por-caso [casos]",
            _param_count_value(total_params),
        )
    return inventory


def _merge(target: Inventory, source: Inventory) -> None:
    for axis, values in source.items():
        target.setdefault(axis, Counter()).update(values)


async def _real_tools() -> list[Any]:
    app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))
    return list(await app.list_tools())


def _generated_inventory() -> Inventory:
    inventory: Inventory = {}
    for seed in range(SEED_COUNT):
        for label in FAMILY_LABELS:
            tools = gate._resolver_fixture(seed, label)
            _merge(inventory, _inventory_resolver_tools(tools))
            audit_cases = [
                schemas
                for _name, schemas, _expected in gate._audit_fixtures(seed, label)
            ]
            _merge(inventory, _inventory_audit_cases(audit_cases))
    return inventory


def _format_count(value: int) -> str:
    return str(value)


def main() -> int:
    real_tools = asyncio.run(_real_tools())
    real: Inventory = {}
    _merge(real, _inventory_resolver_tools(real_tools))
    real_registry = gate._expected_registry(real_tools)
    _merge(real, _inventory_audit_cases([real_registry]))
    generated = _generated_inventory()

    print(f"SEMILLAS={SEED_COUNT}")
    print(f"FAMILIAS_POR_SEMILLA={len(FAMILY_LABELS)}")
    print(
        "JUSTIFICACION=64 semillas deterministas por dos familias; "
        "los valores estructurales se emiten por construccion en cada familia "
        "y el barrido cubre tambien sus combinaciones aleatorias."
    )
    print("eje | valor | en el arbol real | generado por fixtures | OK/FALTA")
    print("---|---|---:|---:|---")
    missing: list[tuple[str, str]] = []
    axes = sorted(set(real) | set(generated))
    for axis in axes:
        values = sorted(set(real.get(axis, ())) | set(generated.get(axis, ())))
        for value in values:
            real_count = real.get(axis, Counter())[value]
            generated_count = generated.get(axis, Counter())[value]
            ok = real_count == 0 or generated_count > 0
            if not ok:
                missing.append((axis, value))
            print(
                f"{axis} | {value} | {_format_count(real_count)} | "
                f"{_format_count(generated_count)} | {'OK' if ok else 'FALTA'}"
            )
    print(f"VALORES_FALTANTES={len(missing)}")
    print("COVERAGE_RC=" + ("0" if not missing else "1"))
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
