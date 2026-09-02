"""Generic M13 runtime constraint enumerator.

M22 supplies finalized tool records and real adapter metadata later.  This
module only enumerates what it is given and never imports catalog or fixtures.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping


class RuntimeValidatorError(ValueError):
    """Raised for an incoherent finalized tool/adapter view."""


def _value(record: object, name: str, default: object = None) -> object:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _tool_schema(record: object) -> Mapping[str, object]:
    schema = _value(record, "input_schema", None)
    if schema is None:
        schema = _value(record, "inputSchema", {})
    if not isinstance(schema, Mapping):
        raise RuntimeValidatorError("input schema must be an object")
    return schema


def enumerate_constraints(
    tools: Iterable[object], adapters: Mapping[str, Mapping[str, object]] | None = None
) -> tuple[str, ...]:
    """Return deterministic schema and explicit-adapter constraint IDs."""

    if adapters is None:
        adapters = {}
    if not isinstance(adapters, Mapping):
        raise RuntimeValidatorError("adapters must be a mapping")
    tool_names: list[str] = []
    ids: set[str] = set()
    for tool in tools:
        name = _value(tool, "name")
        if type(name) is not str or not name or name in tool_names:
            raise RuntimeValidatorError("tool names must be non-empty and unique")
        tool_names.append(name)
        properties = _tool_schema(tool).get("properties", {})
        if not isinstance(properties, Mapping):
            raise RuntimeValidatorError("schema properties must be an object")
        for field in properties:
            if type(field) is not str or not field:
                raise RuntimeValidatorError("schema property names must be non-empty strings")
            identifier = f"schema:{name}:{field}"
            if identifier in ids:
                raise RuntimeValidatorError("duplicate constraint ID")
            ids.add(identifier)
    for key, adapter in adapters.items():
        if type(key) is not str or not key:
            raise RuntimeValidatorError("adapter keys must be non-empty strings")
        if not isinstance(adapter, Mapping):
            raise RuntimeValidatorError("adapter metadata must be an object")
        if key.startswith("manual:"):
            identifier = key
        elif key in tool_names:
            identifier = f"manual:{key}"
        else:
            raise RuntimeValidatorError("dangling runtime adapter")
        if identifier in ids:
            raise RuntimeValidatorError("duplicate constraint ID")
        ids.add(identifier)
        if adapter.get("additional_properties") is False:
            extra_id = f"schema:{key}:__extra__"
            if extra_id in ids:
                raise RuntimeValidatorError("duplicate constraint ID")
            ids.add(extra_id)
    return tuple(sorted(ids))


def build_runtime_view(
    tools: Iterable[object], adapters: Mapping[str, Mapping[str, object]] | None = None
) -> dict[str, object]:
    return {"schema_version": 1, "constraint_ids": list(enumerate_constraints(tools, adapters))}


__all__ = ["RuntimeValidatorError", "enumerate_constraints", "build_runtime_view"]
