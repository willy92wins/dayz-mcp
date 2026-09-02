"""Independent declarative catalog for M13 public constraints."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import copy


CATALOG_RECORD_KEYS = frozenset(
    {
        "id", "tool", "fields", "kind", "summary", "error_code",
        "validator_source", "accept_case_ids", "reject_case_ids", "effect_verification",
    }
)


def _record(
    identifier: str,
    tool: str,
    fields: object,
    kind: str,
    summary: str,
    error_code: str,
    validator_source: str,
    accepts: tuple[str, ...],
    rejects: tuple[str, ...],
    effect: str,
) -> dict[str, object]:
    return {
        "id": identifier,
        "tool": tool,
        "fields": copy.deepcopy(fields),
        "kind": kind,
        "summary": summary,
        "error_code": error_code,
        "validator_source": validator_source,
        "accept_case_ids": list(accepts),
        "reject_case_ids": list(rejects),
        "effect_verification": effect,
    }


CATALOG_RECORDS = (
    _record(
        "schema:dayz_test_run:mission", "dayz_test_run", {"mission": {"type": "string"}}, "schema",
        "Closed mission aliases or a sealed local mission path.", "invalid_dayz_test_request",
        "dayz_test_request.parse_dayz_test_request", (
            "mission_alias_chernarus", "mission_alias_livonia", "mission_alias_sakhal",
            "mission_alias_lfheli", "mission_sealed_path",
        ), ("mission_external_path",), "in_game_required",
    ),
    _record(
        "schema:vehicle_get_in_client:seat_index", "vehicle_get_in_client", {"seat_index": {"type": "integer", "default": 0, "minimum": 0, "maximum": 63}}, "schema",
        "Strict integer seat index in the closed range 0..63, defaulting to zero.", "bad_args",
        "vehicle_get_in_client.seat_index", (
            "seat_omitted", "seat_zero", "seat_one", "seat_sixty_three",
        ), ("seat_bool", "seat_string", "seat_negative", "seat_sixty_four"), "in_game_required",
    ),
    _record(
        "schema:vehicle_get_in_client:expected_type", "vehicle_get_in_client", {"expected_type": {"type": "string", "default": ""}}, "schema",
        "Optional exact classname filter; empty preserves legacy selection.", "bad_args",
        "vehicle_get_in_client.expected_type", (
            "type_omitted", "type_civilian_sedan", "type_boat",
        ), ("type_non_string",), "in_game_required",
    ),
    _record(
        "manual:new_site_guard", "instructions", {}, "manual", "New sites use place_safely first.", "",
        "build_app.instructions", (), (), "wire",
    ),
    _record(
        "manual:spawn_y_provider", "instructions", {}, "manual", "surface_query supplies spawn Y.", "",
        "build_app.instructions", (), (), "wire",
    ),
    _record(
        "manual:living_infected_flags", "instructions", {}, "manual", "Living infected use the frozen flags.", "",
        "build_app.instructions", (), (), "wire",
    ),
    _record(
        "manual:wait_log_sources", "instructions", {}, "manual", "wait_for/logs_since exclude chat and ADM.", "",
        "build_app.instructions", (), (), "wire",
    ),
    _record(
        "manual:wait_default_lookback", "instructions", {}, "manual", "wait_for defaults to 200 lines.", "",
        "build_app.instructions", (), (), "wire",
    ),
    _record(
        "manual:action_use_target_contract", "instructions", {}, "manual", "action_use target tuple is closed.", "",
        "build_app.instructions", (), (), "in_game_required",
    ),
)


def catalog_records() -> tuple[dict[str, object], ...]:
    return tuple(copy.deepcopy(record) for record in CATALOG_RECORDS)


def catalog_by_id() -> dict[str, dict[str, object]]:
    return {record["id"]: record for record in catalog_records()}


def validate_catalog(records: Iterable[Mapping[str, object]] = CATALOG_RECORDS) -> bool:
    seen: set[str] = set()
    for record in records:
        if set(record) != set(CATALOG_RECORD_KEYS):
            return False
        identifier = record.get("id")
        if type(identifier) is not str or not identifier or identifier in seen:
            return False
        seen.add(identifier)
        if type(record.get("tool")) is not str or type(record.get("kind")) is not str:
            return False
        if not isinstance(record.get("fields"), Mapping):
            return False
        if record.get("effect_verification") not in {"wire", "in_game_required"}:
            return False
        for key in ("accept_case_ids", "reject_case_ids"):
            values = record.get(key)
            if type(values) is not list or any(type(item) is not str or not item for item in values):
                return False
            if len(values) != len(set(values)):
                return False
        if set(record["accept_case_ids"]) & set(record["reject_case_ids"]):
            return False
    return True


__all__ = ["CATALOG_RECORD_KEYS", "CATALOG_RECORDS", "catalog_records", "catalog_by_id", "validate_catalog"]
