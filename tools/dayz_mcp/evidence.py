"""Raw-evidence wire and publication contract (dayz-mcp-evidence-v1).

Measurement tools return raw rows with explicit units and no verdicts, scores,
or thresholds. The Enforce bridge serializes classes with JsonSerializer
(WriteToString in addon/scripts/5_Mission/MCPBridge.c), which always writes
every member with its default value, so an unmeasured quantity reaches the wire
as 0. This module validates an EvidenceKind at construction, strips absent
measurements from wire rows without mutating or filtering them, and builds the
published envelope.

No EvidenceKind is defined here: each producer defines its own kind alongside
its tool.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any


EVIDENCE_SCHEMA = "dayz-mcp-evidence-v1"

UNITS: frozenset[str] = frozenset({"m", "m/s", "deg", "s", "ms"})

FIELD_TYPES: frozenset[str] = frozenset(
    {"number", "vec3", "int", "uid", "token", "flag"}
)

FORBIDDEN_NAME_PARTS: frozenset[str] = frozenset(
    {
        "cheat",
        "cheater",
        "cheating",
        "ban",
        "banned",
        "kick",
        "kicked",
        "sanction",
        "sanctioned",
        "verdict",
        "veto",
        "vetoed",
        "weight",
        "weights",
        "weighted",
        "score",
        "scored",
        "limit",
        "limits",
        "ceiling",
        "threshold",
        "suspect",
        "suspected",
        "suspicious",
        "violation",
        "detect",
        "detected",
        "detection",
    }
)

_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]*")
_COMMON_ROW_FIELDS: frozenset[str] = frozenset({"uid", "tick_ms", "gap_kind"})
_MAGNITUDE_TYPES: frozenset[str] = frozenset({"number", "vec3"})
_AVAILABLE_SUFFIX = "_available"


def _refused(code: str, where: str) -> ValueError:
    return ValueError(f"{code}: {where}")


def _is_valid_name(value: object) -> bool:
    return isinstance(value, str) and _NAME_PATTERN.fullmatch(value) is not None


def _has_forbidden_part(name: str) -> bool:
    return any(part in FORBIDDEN_NAME_PARTS for part in name.split("_"))


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    if _is_int(value):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _is_non_empty_str(value: object) -> bool:
    return isinstance(value, str) and value != ""


def _normalize_flag(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if _is_int(value) and value in (0, 1):
        return bool(value)
    return None


@dataclass(frozen=True)
class EvidenceField:
    """Declaration of one measured or contextual field on an EvidenceKind."""

    type: str
    unit: str = ""
    optional: bool = False
    absent_with: frozenset[str] = frozenset()


@dataclass(frozen=True)
class EvidenceKind:
    """Validated schema for one kind of raw evidence rows."""

    name: str
    fields: dict[str, EvidenceField]
    gap_kinds: frozenset[str]

    def __post_init__(self) -> None:
        _validate_kind(self)
        object.__setattr__(self, "fields", dict(self.fields))


def _validate_kind(kind: EvidenceKind) -> None:
    if not _is_valid_name(kind.name) or _has_forbidden_part(kind.name):
        raise _refused("bad_evidence_kind", "name")

    if not isinstance(kind.gap_kinds, frozenset) or not kind.gap_kinds:
        raise _refused("bad_evidence_kind", "gap_kinds")
    for gap in sorted(kind.gap_kinds, key=str):
        if not _is_valid_name(gap) or _has_forbidden_part(gap):
            raise _refused("bad_evidence_kind", f"gap_kinds.{gap}")

    if not isinstance(kind.fields, dict):
        raise _refused("bad_evidence_kind", "fields")

    has_magnitude = False
    for field_name, spec in kind.fields.items():
        where = f"fields.{field_name}"
        if (
            not _is_valid_name(field_name)
            or field_name in _COMMON_ROW_FIELDS
            or field_name.endswith(_AVAILABLE_SUFFIX)
            or _has_forbidden_part(field_name)
        ):
            raise _refused("bad_evidence_kind", where)
        if not isinstance(spec, EvidenceField):
            raise _refused("bad_evidence_kind", where)
        if not isinstance(spec.type, str) or spec.type not in FIELD_TYPES:
            raise _refused("bad_evidence_kind", where)
        if not isinstance(spec.unit, str):
            raise _refused("bad_evidence_kind", where)
        if spec.type in _MAGNITUDE_TYPES:
            if spec.unit not in UNITS:
                raise _refused("bad_evidence_kind", where)
            has_magnitude = True
        elif spec.unit != "":
            raise _refused("bad_evidence_kind", where)
        if not isinstance(spec.optional, bool):
            raise _refused("bad_evidence_kind", where)
        if not isinstance(spec.absent_with, frozenset):
            raise _refused("bad_evidence_kind", where)
        if spec.optional and spec.absent_with:
            raise _refused("bad_evidence_kind", where)
        for gap in spec.absent_with:
            if not isinstance(gap, str) or gap == "" or gap not in kind.gap_kinds:
                raise _refused("bad_evidence_kind", where)

    if not has_magnitude:
        raise _refused("bad_evidence_kind", "fields")


def _normalize_field_value(field_type: str, value: object, where: str) -> Any:
    if field_type == "number":
        if not _is_number(value):
            raise _refused("bad_evidence_row", where)
        return value
    if field_type == "vec3":
        if (
            not isinstance(value, list)
            or len(value) != 3
            or not all(_is_number(item) for item in value)
        ):
            raise _refused("bad_evidence_row", where)
        return list(value)
    if field_type == "int":
        if not _is_int(value):
            raise _refused("bad_evidence_row", where)
        return value
    if field_type in ("uid", "token"):
        if not _is_non_empty_str(value):
            raise _refused("bad_evidence_row", where)
        return value
    if field_type == "flag":
        normalized = _normalize_flag(value)
        if normalized is None:
            raise _refused("bad_evidence_row", where)
        return normalized
    raise _refused("bad_evidence_row", where)


def normalize_rows(kind: EvidenceKind, rows: object) -> list[dict[str, Any]]:
    """Validate wire rows against kind and return fresh published row dicts.

    Every wire member must be declared by the contract or the kind; unknown
    members are refused. Unavailable optional fields and fields absent under
    the row's gap_kind are omitted from the published row. No row is filtered,
    reordered, or mutated.
    """
    if not isinstance(kind, EvidenceKind):
        raise _refused("bad_evidence_kind", "kind")
    if not isinstance(rows, list):
        raise _refused("bad_evidence_row", "rows")

    allowed_keys = set(_COMMON_ROW_FIELDS)
    for field_name, spec in kind.fields.items():
        allowed_keys.add(field_name)
        if spec.optional:
            allowed_keys.add(f"{field_name}{_AVAILABLE_SUFFIX}")

    out: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        where = f"rows[{index}]"
        if not isinstance(row, dict):
            raise _refused("bad_evidence_row", where)

        for key in row:
            if not isinstance(key, str) or key not in allowed_keys:
                raise _refused("bad_evidence_row", f"{where}.{key}")

        if "uid" not in row or not _is_non_empty_str(row["uid"]):
            raise _refused("bad_evidence_row", f"{where}.uid")
        if "tick_ms" not in row or not _is_int(row["tick_ms"]) or row["tick_ms"] < 0:
            raise _refused("bad_evidence_row", f"{where}.tick_ms")
        if "gap_kind" not in row:
            raise _refused("bad_evidence_row", f"{where}.gap_kind")
        gap_kind = row["gap_kind"]
        if not isinstance(gap_kind, str) or (
            gap_kind != "" and gap_kind not in kind.gap_kinds
        ):
            raise _refused("bad_evidence_row", f"{where}.gap_kind")

        published: dict[str, Any] = {
            "uid": row["uid"],
            "tick_ms": row["tick_ms"],
            "gap_kind": gap_kind,
        }

        for field_name, spec in kind.fields.items():
            if spec.optional:
                avail_key = f"{field_name}{_AVAILABLE_SUFFIX}"
                if avail_key not in row:
                    raise _refused("bad_evidence_row", f"{where}.{avail_key}")
                available = _normalize_flag(row[avail_key])
                if available is None:
                    raise _refused("bad_evidence_row", f"{where}.{avail_key}")
                if available:
                    if field_name not in row:
                        raise _refused("bad_evidence_row", f"{where}.{field_name}")
                    published[field_name] = _normalize_field_value(
                        spec.type, row[field_name], f"{where}.{field_name}"
                    )
                    published[avail_key] = True
                else:
                    published[avail_key] = False
            elif spec.absent_with and gap_kind in spec.absent_with:
                continue
            else:
                if field_name not in row:
                    raise _refused("bad_evidence_row", f"{where}.{field_name}")
                published[field_name] = _normalize_field_value(
                    spec.type, row[field_name], f"{where}.{field_name}"
                )

        out.append(published)

    return out


def evidence_result(
    kind: EvidenceKind, rows: object, *, truncated: bool
) -> dict[str, Any]:
    """Build the five-key published evidence envelope for kind and wire rows."""
    if not isinstance(kind, EvidenceKind):
        raise _refused("bad_evidence_kind", "kind")
    if not isinstance(truncated, bool):
        raise _refused("bad_evidence_result", "truncated")

    units: dict[str, str] = {"tick_ms": "ms"}
    for field_name, spec in kind.fields.items():
        if spec.type in _MAGNITUDE_TYPES:
            units[field_name] = spec.unit

    return {
        "schema": EVIDENCE_SCHEMA,
        "kind": kind.name,
        "units": units,
        "rows": normalize_rows(kind, rows),
        "truncated": truncated,
    }
