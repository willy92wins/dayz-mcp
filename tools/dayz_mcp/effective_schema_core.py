"""Pure M13 constructor for the effective MCP schema.

The module deliberately accepts already-finalized records.  It does not import
the application, inspect source code, or manufacture expected tool names.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import copy
import hashlib
import json
from typing import Any, Literal, TypeAlias


SCHEMA_VERSION = 1
_PROFILES = frozenset({"standard", "exec_enforce"})
_ROLES = frozenset({"claude", "codex"})
_EFFECTS = frozenset({"wire", "in_game_required"})
_PROFILE_ORDER = {"standard": 0, "exec_enforce": 1}
_ROLE_ORDER = {"claude": 0, "codex": 1}

ProjectedProfile: TypeAlias = Literal["standard", "exec_enforce", "unknown"]
ProjectedRole: TypeAlias = Literal["claude", "codex", "unknown"]
ProjectedIdentity: TypeAlias = tuple[ProjectedProfile, ProjectedRole]


def project_server_config_identity(
    *,
    enable_exec_enforce: object = None,
    client_platform: object = None,
) -> ProjectedIdentity:
    """Purely project two accredited ServerConfig primitives."""

    if type(enable_exec_enforce) is not bool or type(client_platform) is not str:
        return ("unknown", "unknown")
    if client_platform != "claude" and client_platform != "codex":
        return ("unknown", "unknown")
    if enable_exec_enforce is True:
        return ("exec_enforce", client_platform)
    return ("standard", client_platform)


class EffectiveSchemaError(ValueError):
    """Raised when an M13 record cannot be represented safely."""


def _text(value: object, label: str) -> str:
    if type(value) is not str:
        raise EffectiveSchemaError(f"{label} must be a string")
    return value


def _schema(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EffectiveSchemaError("input_schema must be an object")
    return copy.deepcopy(dict(value))


def _read(record: object, *names: str, default: object = None) -> object:
    if isinstance(record, Mapping):
        for name in names:
            if name in record:
                return record[name]
        return default
    for name in names:
        if hasattr(record, name):
            return getattr(record, name)
    return default


def extract_tool_records(tools: Iterable[object]) -> tuple[dict[str, Any], ...]:
    """Normalize synthetic/finalized tool objects without consulting runtime state."""

    if isinstance(tools, (str, bytes, Mapping)):
        raise EffectiveSchemaError("tools must be an iterable of tool records")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        iterator = iter(tools)
    except TypeError as exc:
        raise EffectiveSchemaError("tools must be iterable") from exc
    for item in iterator:
        name = _text(_read(item, "name"), "tool.name")
        if not name or name in seen:
            raise EffectiveSchemaError("tool names must be non-empty and unique")
        seen.add(name)
        description = _read(item, "description", default="")
        if description is None:
            description = ""
        description = _text(description, "tool.description")
        result.append(
            {
                "name": name,
                "description": description,
                "input_schema": _schema(_read(item, "input_schema", "inputSchema", default={})),
            }
        )
    return tuple(result)


def _tool_record(tool: object) -> dict[str, Any]:
    if not isinstance(tool, Mapping):
        raise EffectiveSchemaError("tool record must be an object")
    name = _text(tool.get("name"), "tool.name")
    if not name:
        raise EffectiveSchemaError("tool.name must not be empty")
    description = tool.get("description", "")
    if description is None:
        description = ""
    description = _text(description, "tool.description")
    constraints = tool.get("public_constraints", [])
    if type(constraints) is not list or any(type(item) is not str or not item for item in constraints):
        raise EffectiveSchemaError("public_constraints must be a list of non-empty strings")
    if len(constraints) != len(set(constraints)):
        raise EffectiveSchemaError("public_constraints must be unique")
    effect = _text(tool.get("effect_verification"), "effect_verification")
    if effect not in _EFFECTS:
        raise EffectiveSchemaError("effect_verification is not recognized")
    return {
        "name": name,
        "description": description,
        "input_schema": _schema(tool.get("input_schema", {})),
        "public_constraints": list(constraints),
        "effect_verification": effect,
    }


def build_payload(
    profile: str,
    role: str,
    instructions: str,
    tools: Iterable[object],
    tool_registry_fingerprint: str,
    *,
    catalog: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one canonical profile/role payload from injected finalized values."""

    profile = _text(profile, "profile")
    role = _text(role, "role")
    if profile not in _PROFILES or role not in _ROLES:
        raise EffectiveSchemaError("unknown profile or role")
    instructions = _text(instructions, "instructions")
    fingerprint = _text(tool_registry_fingerprint, "tool_registry_fingerprint")
    if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
        raise EffectiveSchemaError("tool_registry_fingerprint must be a lowercase SHA-256")
    catalog_ids: set[str] | None = None
    if catalog is not None:
        catalog_ids = set()
        for record in catalog:
            if not isinstance(record, Mapping) or type(record.get("id")) is not str:
                raise EffectiveSchemaError("catalog records must have string IDs")
            if record["id"] in catalog_ids:
                raise EffectiveSchemaError("catalog IDs must be unique")
            catalog_ids.add(record["id"])
    if isinstance(tools, (str, bytes, Mapping)):
        raise EffectiveSchemaError("tools must be iterable")
    records = [_tool_record(item) for item in tools]
    names = [item["name"] for item in records]
    if len(names) != len(set(names)):
        raise EffectiveSchemaError("tool names must be unique")
    if catalog_ids is not None:
        for record in records:
            unknown = set(record["public_constraints"]) - catalog_ids
            if unknown:
                raise EffectiveSchemaError("tool constraint is absent from catalog")
    return {
        "profile": profile,
        "role": role,
        "instructions": instructions,
        "tools": records,
        "tool_registry_fingerprint": fingerprint,
    }


def build_envelope(payloads: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Build the v1 envelope, canonically ordered by profile then role."""

    records = [copy.deepcopy(dict(payload)) for payload in payloads]
    identities: set[tuple[str, str]] = set()
    for payload in records:
        identity = (_text(payload.get("profile"), "profile"), _text(payload.get("role"), "role"))
        if identity[0] not in _PROFILES or identity[1] not in _ROLES:
            raise EffectiveSchemaError("unknown profile or role")
        if identity in identities:
            raise EffectiveSchemaError("duplicate profile/role payload")
        identities.add(identity)
        if set(payload) != {"profile", "role", "instructions", "tools", "tool_registry_fingerprint"}:
            raise EffectiveSchemaError("payload has unexpected fields")
    records.sort(key=lambda item: (_PROFILE_ORDER[item["profile"]], _ROLE_ORDER[item["role"]]))
    return {"schema_version": SCHEMA_VERSION, "payloads": records}


def build_effective_schema(
    profile: str,
    role: str,
    instructions: str,
    tools: Iterable[object],
    tool_registry_fingerprint: str,
    *,
    catalog: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Convenience constructor for a one-payload v1 envelope."""

    return build_envelope(
        [build_payload(profile, role, instructions, tools, tool_registry_fingerprint, catalog=catalog)]
    )


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Serialize a schema deterministically for an external hash/oracle."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def schema_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


__all__ = [
    "SCHEMA_VERSION",
    "EffectiveSchemaError",
    "extract_tool_records",
    "build_payload",
    "build_envelope",
    "build_effective_schema",
    "canonical_json_bytes",
    "schema_sha256",
    "ProjectedProfile",
    "ProjectedRole",
    "ProjectedIdentity",
    "project_server_config_identity",
]
