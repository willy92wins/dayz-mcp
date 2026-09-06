"""Independent M13 catalog and fixture contract tests."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from dayz_mcp import effective_schema_catalog as catalog


ROOT = Path(__file__).parent / "fixtures"
EXPECTED_PROFILE_TOOL_NAMES = {
    ("standard", "claude"): [
        "dayz_knowledge_find", "dayz_knowledge_show", "dayz_knowledge_status", "dayz_knowledge_prepare",
        "session_acquire", "session_wait", "session_acquire_wait", "lease_acquire", "session_cancel",
        "session_heartbeat", "session_release", "session_status", "dayz_test_run", "dayz_test_stop",
        "query_player_state", "query_all_players", "logs_since", "world_spawn", "object_delete",
        "notify_players", "vehicle_enter", "scene_raycast", "telemetry_read", "query_get_in_condition",
        "vehicle_prepare_fixture", "surface_query", "player_teleport", "object_anim", "infected_drive",
        "inventory_give", "object_inspect", "entities_query", "world_time_set", "world_weather_set",
        "camera_set", "camera_get", "restore_gameplay", "key_press", "player_respawn", "capture_screenshot",
        "bridge_status", "vehicle_get_in_client", "engine_set", "vehicle_control", "vehicle_telemetry",
        "vehicle_trace", "vehicle_release", "ui_tree", "ui_set_text", "ui_click", "ui_reload_layout",
        "ui_focus", "ui_dialog", "action_use", "wait_for", "list_projects", "pipeline_feedback",
        "pipeline_inbox", "pipeline_resolve", "playbook_run", "dayz_effective_schema",
    ],
    ("standard", "codex"): [
        "dayz_knowledge_find", "dayz_knowledge_show", "dayz_knowledge_status", "dayz_knowledge_prepare",
        "session_acquire", "session_wait", "session_acquire_wait", "lease_acquire", "session_cancel",
        "session_heartbeat", "session_release", "session_status", "dayz_test_run", "dayz_test_stop",
        "query_player_state", "query_all_players", "logs_since", "world_spawn", "object_delete",
        "notify_players", "vehicle_enter", "scene_raycast", "telemetry_read", "query_get_in_condition",
        "vehicle_prepare_fixture", "surface_query", "player_teleport", "object_anim", "infected_drive",
        "inventory_give", "object_inspect", "entities_query", "world_time_set", "world_weather_set",
        "camera_set", "camera_get", "restore_gameplay", "key_press", "player_respawn", "capture_screenshot",
        "bridge_status", "vehicle_get_in_client", "engine_set", "vehicle_control", "vehicle_telemetry",
        "vehicle_trace", "vehicle_release", "ui_tree", "ui_set_text", "ui_click", "ui_reload_layout",
        "ui_focus", "ui_dialog", "action_use", "wait_for", "list_projects", "pipeline_feedback",
        "pipeline_inbox", "pipeline_resolve", "playbook_run", "dayz_effective_schema",
    ],
    ("exec_enforce", "claude"): [
        "dayz_knowledge_find", "dayz_knowledge_show", "dayz_knowledge_status", "dayz_knowledge_prepare",
        "session_acquire", "session_wait", "session_acquire_wait", "lease_acquire", "session_cancel",
        "session_heartbeat", "session_release", "session_status", "dayz_test_run", "dayz_test_stop",
        "query_player_state", "query_all_players", "logs_since", "world_spawn", "object_delete",
        "notify_players", "vehicle_enter", "scene_raycast", "telemetry_read", "query_get_in_condition",
        "vehicle_prepare_fixture", "surface_query", "player_teleport", "object_anim", "infected_drive",
        "inventory_give", "object_inspect", "entities_query", "world_time_set", "world_weather_set",
        "camera_set", "camera_get", "restore_gameplay", "key_press", "player_respawn", "capture_screenshot",
        "exec_enforce", "bridge_status", "vehicle_get_in_client", "engine_set", "vehicle_control",
        "vehicle_telemetry", "vehicle_trace", "vehicle_release", "ui_tree", "ui_set_text", "ui_click",
        "ui_reload_layout", "ui_focus", "ui_dialog", "action_use", "wait_for", "list_projects",
        "pipeline_feedback", "pipeline_inbox", "pipeline_resolve", "playbook_run", "dayz_effective_schema",
    ],
    ("exec_enforce", "codex"): [
        "dayz_knowledge_find", "dayz_knowledge_show", "dayz_knowledge_status", "dayz_knowledge_prepare",
        "session_acquire", "session_wait", "session_acquire_wait", "lease_acquire", "session_cancel",
        "session_heartbeat", "session_release", "session_status", "dayz_test_run", "dayz_test_stop",
        "query_player_state", "query_all_players", "logs_since", "world_spawn", "object_delete",
        "notify_players", "vehicle_enter", "scene_raycast", "telemetry_read", "query_get_in_condition",
        "vehicle_prepare_fixture", "surface_query", "player_teleport", "object_anim", "infected_drive",
        "inventory_give", "object_inspect", "entities_query", "world_time_set", "world_weather_set",
        "camera_set", "camera_get", "restore_gameplay", "key_press", "player_respawn", "capture_screenshot",
        "exec_enforce", "bridge_status", "vehicle_get_in_client", "engine_set", "vehicle_control",
        "vehicle_telemetry", "vehicle_trace", "vehicle_release", "ui_tree", "ui_set_text", "ui_click",
        "ui_reload_layout", "ui_focus", "ui_dialog", "action_use", "wait_for", "list_projects",
        "pipeline_feedback", "pipeline_inbox", "pipeline_resolve", "playbook_run", "dayz_effective_schema",
    ],
}
EXPECTED_CONSTRAINT_IDS = (
    "schema:dayz_test_run:mission",
    "schema:vehicle_get_in_client:seat_index",
    "schema:vehicle_get_in_client:expected_type",
    "manual:new_site_guard",
    "manual:spawn_y_provider",
    "manual:living_infected_flags",
    "manual:wait_log_sources",
    "manual:wait_default_lookback",
    "manual:action_use_target_contract",
)
EXPECTED_CONCEPTS = (
    {
        "id": "new_site_guard",
        "relation": "before",
        "value": "playbook_run(place_safely) precede sitio nuevo",
        "source_anchor": "build_app.instructions + playbook:place_safely",
    },
    {
        "id": "spawn_y_provider",
        "relation": "provides_component",
        "value": "surface_query.y provee pos[1]",
        "source_anchor": "build_app.instructions + playbook:place_safely",
    },
    {
        "id": "living_infected_flags",
        "relation": "applies_to",
        "value": "flags=3108 se liga a “infectado vivo”; 3108=ECE_PLACE_ON_SURFACE 1060 + ECE_INITAI 2048, incluyendo ECE_CREATEPHYSICS 1024",
        "source_anchor": "build_app.instructions + P:/scripts/3_game/ce/centraleconomy.c:16-17,37",
    },
    {
        "id": "wait_log_sources",
        "relation": "reads_excluding",
        "value": "wait_for/logs_since excluyen .ADM/chat",
        "source_anchor": "build_app.instructions + execute_wait_for",
    },
    {
        "id": "wait_default_lookback",
        "relation": "default",
        "value": "lookback_lines=200 evita perder respuesta corta",
        "source_anchor": "build_app.wait_for + execute_wait_for",
    },
    {
        "id": "action_use_target_contract",
        "relation": "tuple",
        "value": "held item, componentIndex=-1, classname exacto",
        "source_anchor": "build_app.action_use + MCPClientBridge.DispatchActionUse",
    },
)


def read_fixture(relative: str) -> object:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


class EffectiveSchemaCatalogTests(unittest.TestCase):
    def test_catalog_records_have_the_frozen_shape_and_ids(self) -> None:
        records = catalog.catalog_records()
        self.assertEqual(
            {record["id"] for record in records},
            set(EXPECTED_CONSTRAINT_IDS),
        )
        required_keys = {
            "id", "tool", "fields", "kind", "summary", "error_code",
            "validator_source", "accept_case_ids", "reject_case_ids", "effect_verification",
        }
        for record in records:
            self.assertEqual(set(record), required_keys)
        self.assertTrue(catalog.validate_catalog(records))
        seat = catalog.catalog_by_id()["schema:vehicle_get_in_client:seat_index"]["fields"]["seat_index"]
        self.assertEqual(seat, {"type": "integer", "default": 0, "minimum": 0, "maximum": 63})
        expected_type = catalog.catalog_by_id()["schema:vehicle_get_in_client:expected_type"]["fields"]["expected_type"]
        self.assertEqual(expected_type, {"type": "string", "default": ""})

    def test_required_constraint_fixture_is_independent_and_complete(self) -> None:
        fixture = read_fixture("effective_schema_v1/required_constraint_ids.json")
        self.assertEqual(fixture["schema_version"], 1)
        self.assertEqual(tuple(fixture["required_constraint_ids"]), EXPECTED_CONSTRAINT_IDS)
        self.assertEqual(len(set(fixture["required_constraint_ids"])), len(EXPECTED_CONSTRAINT_IDS))

    def test_profile_inventory_has_four_manual_pairs_and_closed_expected_arrays(self) -> None:
        fixture = read_fixture("effective_schema_v5/profile_inventory.json")
        self.assertEqual(fixture["schema_version"], 1)
        records = fixture["profiles"]
        self.assertEqual([(item["profile"], item["role"]) for item in records], list(EXPECTED_PROFILE_TOOL_NAMES))
        for item in records:
            self.assertEqual(item["expected_tool_names"], EXPECTED_PROFILE_TOOL_NAMES[(item["profile"], item["role"])])
            self.assertEqual(
                item["config"],
                {"enable_exec_enforce": item["profile"] == "exec_enforce", "client_platform": item["role"]},
            )

    def test_instruction_fixture_contains_exactly_the_six_independent_rows(self) -> None:
        fixture = read_fixture("effective_schema_v5/instructions_required_concepts.json")
        self.assertEqual(fixture["schema_version"], 1)
        self.assertEqual(fixture["concepts"], list(EXPECTED_CONCEPTS))

    def test_case_fixtures_are_declarative_and_cover_required_boundaries(self) -> None:
        validators = read_fixture("effective_schema_v5/validator_cases.json")
        mutations = read_fixture("effective_schema_v5/mutation_cases.json")
        self.assertEqual(validators["schema_version"], 1)
        self.assertEqual(mutations["schema_version"], 1)
        expected_validator_ids = {
            "mission_alias_chernarus", "mission_alias_livonia", "mission_alias_sakhal", "mission_alias_lfheli",
            "mission_sealed_path", "mission_external_path", "seat_omitted", "seat_zero", "seat_one",
            "seat_sixty_three", "seat_bool", "seat_string", "seat_negative", "seat_sixty_four",
            "type_omitted", "type_civilian_sedan", "type_boat", "type_non_string",
        }
        expected_mutation_ids = {
            "field_removed_from_app_schema", "catalog_constraint_removed", "fixture_constraint_removed",
            "runtime_adapter_extra", "runtime_adapter_dangling", "validator_logic_altered",
            "extra_wrapper_removed", "extra_arguments_accepted", "offline_public", "mission_external_accepted",
            "parameter_renamed", "bridge_marked_wire", "runtime_extra_after_alias",
        }
        self.assertEqual({case["id"] for case in validators["cases"]}, expected_validator_ids)
        self.assertEqual({case["id"] for case in mutations["cases"]}, expected_mutation_ids)
        self.assertEqual(len({case["id"] for case in validators["cases"]}), len(validators["cases"]))
        self.assertEqual(len({case["id"] for case in mutations["cases"]}), len(mutations["cases"]))
        omitted = next(case for case in validators["cases"] if case["id"] == "type_omitted")
        self.assertNotIn("forward", omitted)
        self.assertEqual(omitted["forward_absent"], ["MCPArgs.type"])
        self.assertTrue(all(set(case) >= {"id", "constraint_id", "outcome", "input"} for case in validators["cases"]))
        self.assertTrue(all(set(case) >= {"id", "expected"} for case in mutations["cases"]))
        fixture_by_constraint = {}
        for case in validators["cases"]:
            fixture_by_constraint.setdefault(case["constraint_id"], {"accept": [], "reject": []})[case["outcome"]].append(case["id"])
        for record in catalog.catalog_records():
            cases = fixture_by_constraint.get(record["id"], {"accept": [], "reject": []})
            self.assertEqual(record["accept_case_ids"], cases["accept"])
            self.assertEqual(record["reject_case_ids"], cases["reject"])


if __name__ == "__main__":
    unittest.main()
