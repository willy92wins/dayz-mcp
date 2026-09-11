"""Promote effective-schema v1 constraint ids onto the v5 concept surface.

v1 names schema-layer and manual-layer ids together. v5 splits them: schema
records stay on the catalog, and `manual:*` ids become concept ids. This
module is the named producer for that map (ficha d366). It does not write
fixtures and does not import the live FastMCP app.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence


_MANUAL_PREFIX = "manual:"
_SCHEMA_PREFIX = "schema:"


class PromotionError(ValueError):
    """Raised when a v1 document cannot be projected onto v5 concepts."""


def promote_v1_constraint_ids(ids: object) -> tuple[str, ...]:
    """Return the v5 concept ids implied by a v1 required_constraint_ids list."""
    if not isinstance(ids, Sequence) or isinstance(ids, (str, bytes)):
        raise PromotionError("required_constraint_ids must be a sequence of strings")
    concepts: list[str] = []
    seen: set[str] = set()
    for item in ids:
        if type(item) is not str or item == "":
            raise PromotionError("constraint id must be a non-empty string")
        if item.startswith(_SCHEMA_PREFIX):
            continue
        if not item.startswith(_MANUAL_PREFIX):
            raise PromotionError("constraint id must start with schema: or manual:")
        concept = item[len(_MANUAL_PREFIX) :]
        if concept == "" or concept in seen:
            raise PromotionError("manual concept id is empty or duplicated")
        seen.add(concept)
        concepts.append(concept)
    return tuple(concepts)


def promote_v1_document(document: Mapping[str, object]) -> tuple[str, ...]:
    if document.get("schema_version") != 1:
        raise PromotionError("schema_version must be 1")
    return promote_v1_constraint_ids(document.get("required_constraint_ids"))


def v5_concept_ids(document: Mapping[str, object]) -> tuple[str, ...]:
    concepts = document.get("concepts")
    if not isinstance(concepts, Iterable) or isinstance(concepts, (str, bytes)):
        raise PromotionError("v5 concepts must be an iterable of objects")
    ids: list[str] = []
    for item in concepts:
        if not isinstance(item, Mapping):
            raise PromotionError("v5 concept must be an object")
        identifier = item.get("id")
        if type(identifier) is not str or identifier == "":
            raise PromotionError("v5 concept id must be a non-empty string")
        ids.append(identifier)
    return tuple(ids)


def promotion_matches_v5(
    v1_document: Mapping[str, object],
    v5_document: Mapping[str, object],
) -> bool:
    return promote_v1_document(v1_document) == v5_concept_ids(v5_document)
