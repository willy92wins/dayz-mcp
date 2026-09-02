"""Pure M14 registry fingerprint contract tests with frozen external oracles."""

from __future__ import annotations

KNOWN_MARKER_BYTES = b'{"artifact_txid":"0123456789abcdef0123456789abcdef","artifact_version":5,"fingerprint_sha256":"6c3593ab43c4115b98ac0ef186dcf024fe1b9c6acd837a522fe04f2ae43310fd","generator":{"name":"m14-oracle","version":"1"},"payloads":[{"instructions":"instructions:standard|claude","profile":"standard","role":"claude","tool_registry_fingerprint":"377d5e7de29cb26be681528676456e7eacf653a5efba1662c6c17b872f40aea5","tools":[{"description":"literal standard|claude","effect_verification":"wire","input_schema":{"properties":{},"type":"object"},"name":"standard_claude","public_constraints":["constraint:standard_claude"]}]},{"instructions":"instructions:standard|codex","profile":"standard","role":"codex","tool_registry_fingerprint":"8efd0a8edbb8c53ba561a9c5add1866c3026d1a0b14c4d1f1c70864128227f6a","tools":[{"description":"literal standard|codex","effect_verification":"wire","input_schema":{"properties":{},"type":"object"},"name":"standard_codex","public_constraints":["constraint:standard_codex"]}]},{"instructions":"instructions:exec_enforce|claude","profile":"exec_enforce","role":"claude","tool_registry_fingerprint":"007ebb66da4ad2d17ac2c4fa81df5d2750381573698cce65a8e007d29f17d130","tools":[{"description":"literal exec_enforce|claude","effect_verification":"wire","input_schema":{"properties":{},"type":"object"},"name":"exec_enforce_claude","public_constraints":["constraint:exec_enforce_claude"]}]},{"instructions":"instructions:exec_enforce|codex","profile":"exec_enforce","role":"codex","tool_registry_fingerprint":"a84fe92e285aaabd80740989be22eebe9915cb03d1fb866f886c698a2fbee4a1","tools":[{"description":"literal exec_enforce|codex","effect_verification":"wire","input_schema":{"properties":{},"type":"object"},"name":"exec_enforce_codex","public_constraints":["constraint:exec_enforce_codex"]}]}],"producers":[{"path":"tools/approved-launchers.json","sha256":"fcb04047ad1606d8f0647dc2d22304298398eb11acbd10619f82b8d567f14d61"},{"path":"tools/approved-launchers.receipts/fedcba9876543210fedcba9876543210/committed.json","sha256":"04a7c5590bec79006bfea28dbc01ba1f1d2d2cf82ca0131c0ed89ed2345681d0"},{"path":"tools/approved-launchers.receipts/fedcba9876543210fedcba9876543210/prepared.json","sha256":"1109782140dc8aaf2ba784c0cdb15570631e4dc01807c4159cee9d4dc6232965"},{"path":"tools/dayz_mcp/dayz_test_modes.py","sha256":"0b6d35975ef2db3cc8460fec3f1e12714f32defab491173fdef4762297912d09"},{"path":"tools/dayz_mcp/dayz_test_request.py","sha256":"41da13f92bc21c5711c3546c85fdc0567a1bda302a21fcd932290da9894196b2"},{"path":"tools/dayz_mcp/effective_schema_catalog.py","sha256":"28007dba3ef1c1eddc93a2223fb2ad54724571614d1e457d1177b42816fac7ef"},{"path":"tools/dayz_mcp/effective_schema_core.py","sha256":"d8be16a8a425177c9d388e850d455a22153548ba99d2c2427712c65a3eacbbde"},{"path":"tools/dayz_mcp/effective_schema_runtime_validators.py","sha256":"153a605bb9cc1febf6f50f4bed8d6eef8d79a497213b6aa543295d2bf91f6800"},{"path":"tools/dayz_mcp/knowledge.py","sha256":"768212e799bec5f92a193db9026c881a13de5f5a1ab2f7fad29155d65f126a8b"},{"path":"tools/dayz_mcp/server.py","sha256":"e80e5251e9e59e8432b352cd3e9cbf3dc67d22423fa37fbd9ad1011b0ba180e9"},{"path":"tools/dayz_mcp/tool_registry_fingerprint.py","sha256":"d96e2e7567ca7804d1da23ba59bbfc24ae4339f230a1b454920b67ee9dc39bf5"},{"path":"tools/mcp_capture.py","sha256":"f10f1cf5b5d2b21d7856383976032e9115ac4d0bb968409787518948063c439f"},{"path":"tools/native-launchers/dayz-test-v1/app.pyz","sha256":"ce1f8417e6a74d66b7e2e8e196461bd43abada8d34070c2c41e3f5dfe90550cf"},{"path":"tools/native-launchers/dayz-test-v1/closure-manifest.json","sha256":"87c5255a53d9375fd077c22b8972c3f3af96a6c6620172a69e7ba0aa8c41a30c"},{"path":"tools/promote_effective_schema.py","sha256":"b19c683bfccd884e7f6216d031f93c91303f8f96f06f592d9154d2d2c7a6676f"},{"path":"tools/pyproject.toml","sha256":"f9ed8a0b21dff2822e4e73c521adfde8018efe73021056b55f863065d31402b6"},{"path":"tools/requirements-mcp.txt","sha256":"ae0989dbc1d3f192881adae446c638780e9bd39bc7518a3c2210f209b9f53ccb"},{"path":"tools/tests/fixtures/effective_schema_v1/required_constraint_ids.json","sha256":"979e395f1cad3364bbf51f622e70b306d40239989dccf64d58f42cdb9576af9a"},{"path":"tools/tests/fixtures/effective_schema_v5/instructions_required_concepts.json","sha256":"aa4b8e3b1602a69fcdf4ef682819e888d5b4c9fbc78185ad9885b0a9f7b3c583"},{"path":"tools/tests/fixtures/effective_schema_v5/mutation_cases.json","sha256":"88cd0e8975a8703993203ee4775af9f81b28b4640ba9e5ac6fde16f5aef1783b"},{"path":"tools/tests/fixtures/effective_schema_v5/profile_inventory.json","sha256":"e7d7be819d32f59d8c160a592d66a6038e0708aaf9d06acc11ec8772bfd34a9e"},{"path":"tools/tests/fixtures/effective_schema_v5/validator_cases.json","sha256":"dc2bd69e325b445287c1680fda3067c676cb85f7662e61fab7d20b88b86897fc"},{"path":"tools/tests/test_effective_schema_promotion.py","sha256":"9aed45cc2613b3bb84d51123b326f1569af0963ab99932dffb5244c2518093d7"}],"producers_sha256":"c281d3719dcadeb5872310685a4b04ab90a7c47c1539c68961706b915d7c93f8","schema_version":1,"verdict_sha256":"722591a0b5f4c8be3abc4305ced9d7d07f0f2c11f891969b626ec1a6fd81049d"}'
KNOWN_FINGERPRINT_BYTES = b'377d5e7de29cb26be681528676456e7eacf653a5efba1662c6c17b872f40aea5  standard|claude\n8efd0a8edbb8c53ba561a9c5add1866c3026d1a0b14c4d1f1c70864128227f6a  standard|codex\n007ebb66da4ad2d17ac2c4fa81df5d2750381573698cce65a8e007d29f17d130  exec_enforce|claude\na84fe92e285aaabd80740989be22eebe9915cb03d1fb866f886c698a2fbee4a1  exec_enforce|codex\n'
KNOWN_VERDICT_BYTES = b'{"artifact_txid":"0123456789abcdef0123456789abcdef","artifact_version":5,"bank_members":[{"expected_ids":["schema:dayz_test_run:mission","schema:vehicle_get_in_client:seat_index","schema:vehicle_get_in_client:expected_type","manual:new_site_guard","manual:spawn_y_provider","manual:living_infected_flags","manual:wait_log_sources","manual:wait_default_lookback","manual:action_use_target_contract"],"path":"tools/tests/fixtures/effective_schema_v1/required_constraint_ids.json","results":[{"expected":true,"id":"schema:dayz_test_run:mission","observed":true,"verdict":"PASS"},{"expected":true,"id":"schema:vehicle_get_in_client:seat_index","observed":true,"verdict":"PASS"},{"expected":true,"id":"schema:vehicle_get_in_client:expected_type","observed":true,"verdict":"PASS"},{"expected":true,"id":"manual:new_site_guard","observed":true,"verdict":"PASS"},{"expected":true,"id":"manual:spawn_y_provider","observed":true,"verdict":"PASS"},{"expected":true,"id":"manual:living_infected_flags","observed":true,"verdict":"PASS"},{"expected":true,"id":"manual:wait_log_sources","observed":true,"verdict":"PASS"},{"expected":true,"id":"manual:wait_default_lookback","observed":true,"verdict":"PASS"},{"expected":true,"id":"manual:action_use_target_contract","observed":true,"verdict":"PASS"}],"sha256":"979e395f1cad3364bbf51f622e70b306d40239989dccf64d58f42cdb9576af9a","verdict":"PASS"},{"expected_ids":["new_site_guard","spawn_y_provider","living_infected_flags","wait_log_sources","wait_default_lookback","action_use_target_contract"],"path":"tools/tests/fixtures/effective_schema_v5/instructions_required_concepts.json","results":[{"expected":true,"id":"new_site_guard","observed":true,"verdict":"PASS"},{"expected":true,"id":"spawn_y_provider","observed":true,"verdict":"PASS"},{"expected":true,"id":"living_infected_flags","observed":true,"verdict":"PASS"},{"expected":true,"id":"wait_log_sources","observed":true,"verdict":"PASS"},{"expected":true,"id":"wait_default_lookback","observed":true,"verdict":"PASS"},{"expected":true,"id":"action_use_target_contract","observed":true,"verdict":"PASS"}],"sha256":"aa4b8e3b1602a69fcdf4ef682819e888d5b4c9fbc78185ad9885b0a9f7b3c583","verdict":"PASS"},{"expected_ids":["standard|claude","standard|codex","exec_enforce|claude","exec_enforce|codex"],"path":"tools/tests/fixtures/effective_schema_v5/profile_inventory.json","results":[{"expected":true,"id":"standard|claude","observed":true,"verdict":"PASS"},{"expected":true,"id":"standard|codex","observed":true,"verdict":"PASS"},{"expected":true,"id":"exec_enforce|claude","observed":true,"verdict":"PASS"},{"expected":true,"id":"exec_enforce|codex","observed":true,"verdict":"PASS"}],"sha256":"e7d7be819d32f59d8c160a592d66a6038e0708aaf9d06acc11ec8772bfd34a9e","verdict":"PASS"},{"expected_ids":["mission_alias_chernarus","mission_alias_livonia","mission_alias_sakhal","mission_alias_lfheli","mission_sealed_path","mission_external_path","seat_omitted","seat_zero","seat_one","seat_sixty_three","seat_bool","seat_string","seat_negative","seat_sixty_four","type_omitted","type_civilian_sedan","type_boat","type_non_string"],"path":"tools/tests/fixtures/effective_schema_v5/validator_cases.json","results":[{"expected":true,"id":"mission_alias_chernarus","observed":true,"verdict":"PASS"},{"expected":true,"id":"mission_alias_livonia","observed":true,"verdict":"PASS"},{"expected":true,"id":"mission_alias_sakhal","observed":true,"verdict":"PASS"},{"expected":true,"id":"mission_alias_lfheli","observed":true,"verdict":"PASS"},{"expected":true,"id":"mission_sealed_path","observed":true,"verdict":"PASS"},{"expected":true,"id":"mission_external_path","observed":true,"verdict":"PASS"},{"expected":true,"id":"seat_omitted","observed":true,"verdict":"PASS"},{"expected":true,"id":"seat_zero","observed":true,"verdict":"PASS"},{"expected":true,"id":"seat_one","observed":true,"verdict":"PASS"},{"expected":true,"id":"seat_sixty_three","observed":true,"verdict":"PASS"},{"expected":true,"id":"seat_bool","observed":true,"verdict":"PASS"},{"expected":true,"id":"seat_string","observed":true,"verdict":"PASS"},{"expected":true,"id":"seat_negative","observed":true,"verdict":"PASS"},{"expected":true,"id":"seat_sixty_four","observed":true,"verdict":"PASS"},{"expected":true,"id":"type_omitted","observed":true,"verdict":"PASS"},{"expected":true,"id":"type_civilian_sedan","observed":true,"verdict":"PASS"},{"expected":true,"id":"type_boat","observed":true,"verdict":"PASS"},{"expected":true,"id":"type_non_string","observed":true,"verdict":"PASS"}],"sha256":"dc2bd69e325b445287c1680fda3067c676cb85f7662e61fab7d20b88b86897fc","verdict":"PASS"},{"expected_ids":["field_removed_from_app_schema","catalog_constraint_removed","fixture_constraint_removed","runtime_adapter_extra","runtime_adapter_dangling","validator_logic_altered","extra_wrapper_removed","extra_arguments_accepted","offline_public","mission_external_accepted","parameter_renamed","bridge_marked_wire","runtime_extra_after_alias"],"path":"tools/tests/fixtures/effective_schema_v5/mutation_cases.json","results":[{"expected":true,"id":"field_removed_from_app_schema","observed":true,"verdict":"PASS"},{"expected":true,"id":"catalog_constraint_removed","observed":true,"verdict":"PASS"},{"expected":true,"id":"fixture_constraint_removed","observed":true,"verdict":"PASS"},{"expected":true,"id":"runtime_adapter_extra","observed":true,"verdict":"PASS"},{"expected":true,"id":"runtime_adapter_dangling","observed":true,"verdict":"PASS"},{"expected":true,"id":"validator_logic_altered","observed":true,"verdict":"PASS"},{"expected":true,"id":"extra_wrapper_removed","observed":true,"verdict":"PASS"},{"expected":true,"id":"extra_arguments_accepted","observed":true,"verdict":"PASS"},{"expected":true,"id":"offline_public","observed":true,"verdict":"PASS"},{"expected":true,"id":"mission_external_accepted","observed":true,"verdict":"PASS"},{"expected":true,"id":"parameter_renamed","observed":true,"verdict":"PASS"},{"expected":true,"id":"bridge_marked_wire","observed":true,"verdict":"PASS"},{"expected":true,"id":"runtime_extra_after_alias","observed":true,"verdict":"PASS"}],"sha256":"88cd0e8975a8703993203ee4775af9f81b28b4640ba9e5ac6fde16f5aef1783b","verdict":"PASS"}],"schema_version":1,"verdict":"PASS"}'
KNOWN_PRODUCERS_BYTES = b'{"active_approval":{"committed_path":"tools/approved-launchers.receipts/fedcba9876543210fedcba9876543210/committed.json","prepared_path":"tools/approved-launchers.receipts/fedcba9876543210fedcba9876543210/prepared.json","rolled_back_absent":true,"txid":"fedcba9876543210fedcba9876543210"},"artifact_txid":"0123456789abcdef0123456789abcdef","artifact_version":5,"producers":[{"path":"tools/approved-launchers.json","sha256":"fcb04047ad1606d8f0647dc2d22304298398eb11acbd10619f82b8d567f14d61"},{"path":"tools/approved-launchers.receipts/fedcba9876543210fedcba9876543210/committed.json","sha256":"04a7c5590bec79006bfea28dbc01ba1f1d2d2cf82ca0131c0ed89ed2345681d0"},{"path":"tools/approved-launchers.receipts/fedcba9876543210fedcba9876543210/prepared.json","sha256":"1109782140dc8aaf2ba784c0cdb15570631e4dc01807c4159cee9d4dc6232965"},{"path":"tools/dayz_mcp/dayz_test_modes.py","sha256":"0b6d35975ef2db3cc8460fec3f1e12714f32defab491173fdef4762297912d09"},{"path":"tools/dayz_mcp/dayz_test_request.py","sha256":"41da13f92bc21c5711c3546c85fdc0567a1bda302a21fcd932290da9894196b2"},{"path":"tools/dayz_mcp/effective_schema_catalog.py","sha256":"28007dba3ef1c1eddc93a2223fb2ad54724571614d1e457d1177b42816fac7ef"},{"path":"tools/dayz_mcp/effective_schema_core.py","sha256":"d8be16a8a425177c9d388e850d455a22153548ba99d2c2427712c65a3eacbbde"},{"path":"tools/dayz_mcp/effective_schema_runtime_validators.py","sha256":"153a605bb9cc1febf6f50f4bed8d6eef8d79a497213b6aa543295d2bf91f6800"},{"path":"tools/dayz_mcp/knowledge.py","sha256":"768212e799bec5f92a193db9026c881a13de5f5a1ab2f7fad29155d65f126a8b"},{"path":"tools/dayz_mcp/server.py","sha256":"e80e5251e9e59e8432b352cd3e9cbf3dc67d22423fa37fbd9ad1011b0ba180e9"},{"path":"tools/dayz_mcp/tool_registry_fingerprint.py","sha256":"d96e2e7567ca7804d1da23ba59bbfc24ae4339f230a1b454920b67ee9dc39bf5"},{"path":"tools/mcp_capture.py","sha256":"f10f1cf5b5d2b21d7856383976032e9115ac4d0bb968409787518948063c439f"},{"path":"tools/native-launchers/dayz-test-v1/app.pyz","sha256":"ce1f8417e6a74d66b7e2e8e196461bd43abada8d34070c2c41e3f5dfe90550cf"},{"path":"tools/native-launchers/dayz-test-v1/closure-manifest.json","sha256":"87c5255a53d9375fd077c22b8972c3f3af96a6c6620172a69e7ba0aa8c41a30c"},{"path":"tools/promote_effective_schema.py","sha256":"b19c683bfccd884e7f6216d031f93c91303f8f96f06f592d9154d2d2c7a6676f"},{"path":"tools/pyproject.toml","sha256":"f9ed8a0b21dff2822e4e73c521adfde8018efe73021056b55f863065d31402b6"},{"path":"tools/requirements-mcp.txt","sha256":"ae0989dbc1d3f192881adae446c638780e9bd39bc7518a3c2210f209b9f53ccb"},{"path":"tools/tests/fixtures/effective_schema_v1/required_constraint_ids.json","sha256":"979e395f1cad3364bbf51f622e70b306d40239989dccf64d58f42cdb9576af9a"},{"path":"tools/tests/fixtures/effective_schema_v5/instructions_required_concepts.json","sha256":"aa4b8e3b1602a69fcdf4ef682819e888d5b4c9fbc78185ad9885b0a9f7b3c583"},{"path":"tools/tests/fixtures/effective_schema_v5/mutation_cases.json","sha256":"88cd0e8975a8703993203ee4775af9f81b28b4640ba9e5ac6fde16f5aef1783b"},{"path":"tools/tests/fixtures/effective_schema_v5/profile_inventory.json","sha256":"e7d7be819d32f59d8c160a592d66a6038e0708aaf9d06acc11ec8772bfd34a9e"},{"path":"tools/tests/fixtures/effective_schema_v5/validator_cases.json","sha256":"dc2bd69e325b445287c1680fda3067c676cb85f7662e61fab7d20b88b86897fc"},{"path":"tools/tests/test_effective_schema_promotion.py","sha256":"9aed45cc2613b3bb84d51123b326f1569af0963ab99932dffb5244c2518093d7"}],"schema_version":1,"validator_sources":{"build_app.instructions":"tools/dayz_mcp/server.py","dayz_test_request.parse_dayz_test_request":"tools/dayz_mcp/dayz_test_request.py","vehicle_get_in_client.expected_type":"tools/dayz_mcp/server.py","vehicle_get_in_client.seat_index":"tools/dayz_mcp/server.py"}}'
KNOWN_RECEIPTS_BYTES = b'{"artifact_txid":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","kind":"commit","operation_txid":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","previous_schema_sha256":null,"schema_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}\n{"kind":"legacy-note","note":"preserved-prefix"}\n{"artifact_txid":"0123456789abcdef0123456789abcdef","kind":"commit","operation_txid":"0123456789abcdef0123456789abcdef","previous_schema_sha256":null,"schema_sha256":"9b1b1cba496698a0be51aac987259215d68c1d8fb976d9ee64e6dd78196c7dd6"}\n'

import copy
import hashlib
import json
import math
import unittest
from collections.abc import Iterator

from dayz_mcp.effective_schema_core import EffectiveSchemaError
from dayz_mcp.tool_registry_fingerprint import (
    AuthorityBundleBytes,
    AuthoritySnapshot,
    RegistrySnapshot,
    canonical_json_bytes,
    canonical_registry_fingerprint,
    capture_registry_snapshot,
    compare_snapshot_to_authority,
    read_authority_marker,
)

PAIR_BYTES = {
    ("standard", "claude"): (
        b'{"tools":[{"description":"literal standard|claude","effect_verification":"wire",'
        b'"input_schema":{"properties":{},"type":"object"},"name":"standard_claude",'
        b'"public_constraints":["constraint:standard_claude"]}]}'
    ),
    ("standard", "codex"): (
        b'{"tools":[{"description":"literal standard|codex","effect_verification":"wire",'
        b'"input_schema":{"properties":{},"type":"object"},"name":"standard_codex",'
        b'"public_constraints":["constraint:standard_codex"]}]}'
    ),
    ("exec_enforce", "claude"): (
        b'{"tools":[{"description":"literal exec_enforce|claude","effect_verification":"wire",'
        b'"input_schema":{"properties":{},"type":"object"},"name":"exec_enforce_claude",'
        b'"public_constraints":["constraint:exec_enforce_claude"]}]}'
    ),
    ("exec_enforce", "codex"): (
        b'{"tools":[{"description":"literal exec_enforce|codex","effect_verification":"wire",'
        b'"input_schema":{"properties":{},"type":"object"},"name":"exec_enforce_codex",'
        b'"public_constraints":["constraint:exec_enforce_codex"]}]}'
    ),
}
PAIR_SHA256 = {
    ("standard", "claude"): "377d5e7de29cb26be681528676456e7eacf653a5efba1662c6c17b872f40aea5",
    ("standard", "codex"): "8efd0a8edbb8c53ba561a9c5add1866c3026d1a0b14c4d1f1c70864128227f6a",
    ("exec_enforce", "claude"): "007ebb66da4ad2d17ac2c4fa81df5d2750381573698cce65a8e007d29f17d130",
    ("exec_enforce", "codex"): "a84fe92e285aaabd80740989be22eebe9915cb03d1fb866f886c698a2fbee4a1",
}

NFC_CANONICAL_BYTES = (
    b'{"tools":[{"description":"Caf\xc3\xa9.",'
    b'"effect_verification":"wire",'
    b'"input_schema":{"properties":{"caf\xc3\xa9":{"type":"string"}},"type":"object"},'
    b'"name":"caf\xc3\xa9",'
    b'"public_constraints":["constraint:caf\xc3\xa9"]}]}'
)
NFC_CANONICAL_SHA256 = "4e0175a61cd8b67511b132bfbf45325a7a98acff9b447838bd5238a52475a548"

BANK_TABLE = (
    (
        "tools/tests/fixtures/effective_schema_v1/required_constraint_ids.json",
        "979e395f1cad3364bbf51f622e70b306d40239989dccf64d58f42cdb9576af9a",
        9,
        "8aaffe2751a511a30c166ecad33c11995f249fd25a8787507781fe4124c43ff3",
        (
            "schema:dayz_test_run:mission",
            "schema:vehicle_get_in_client:seat_index",
            "schema:vehicle_get_in_client:expected_type",
            "manual:new_site_guard",
            "manual:spawn_y_provider",
            "manual:living_infected_flags",
            "manual:wait_log_sources",
            "manual:wait_default_lookback",
            "manual:action_use_target_contract",
        ),
    ),
    (
        "tools/tests/fixtures/effective_schema_v5/instructions_required_concepts.json",
        "aa4b8e3b1602a69fcdf4ef682819e888d5b4c9fbc78185ad9885b0a9f7b3c583",
        6,
        "63075c8888d0c80aa8aca55f4da29a69bb6e7b1ccd61de933db10954593b9f94",
        (
            "new_site_guard",
            "spawn_y_provider",
            "living_infected_flags",
            "wait_log_sources",
            "wait_default_lookback",
            "action_use_target_contract",
        ),
    ),
    (
        "tools/tests/fixtures/effective_schema_v5/profile_inventory.json",
        "e7d7be819d32f59d8c160a592d66a6038e0708aaf9d06acc11ec8772bfd34a9e",
        4,
        "314755d4a61b0ba382b4286ecd42f00efebdfdbee5dc8e7c4702c4e649ad0306",
        ("standard|claude", "standard|codex", "exec_enforce|claude", "exec_enforce|codex"),
    ),
    (
        "tools/tests/fixtures/effective_schema_v5/validator_cases.json",
        "dc2bd69e325b445287c1680fda3067c676cb85f7662e61fab7d20b88b86897fc",
        18,
        "2035e4558adbb877fe22f0c012c5eaeb3dc442d65ec21a403458b9048266bef6",
        (
            "mission_alias_chernarus",
            "mission_alias_livonia",
            "mission_alias_sakhal",
            "mission_alias_lfheli",
            "mission_sealed_path",
            "mission_external_path",
            "seat_omitted",
            "seat_zero",
            "seat_one",
            "seat_sixty_three",
            "seat_bool",
            "seat_string",
            "seat_negative",
            "seat_sixty_four",
            "type_omitted",
            "type_civilian_sedan",
            "type_boat",
            "type_non_string",
        ),
    ),
    (
        "tools/tests/fixtures/effective_schema_v5/mutation_cases.json",
        "88cd0e8975a8703993203ee4775af9f81b28b4640ba9e5ac6fde16f5aef1783b",
        13,
        "8f6e6c03433b6101fa3e4a408226c9c06a9436a4cdf561af34f7b407a3774ab0",
        (
            "field_removed_from_app_schema",
            "catalog_constraint_removed",
            "fixture_constraint_removed",
            "runtime_adapter_extra",
            "runtime_adapter_dangling",
            "validator_logic_altered",
            "extra_wrapper_removed",
            "extra_arguments_accepted",
            "offline_public",
            "mission_external_accepted",
            "parameter_renamed",
            "bridge_marked_wire",
            "runtime_extra_after_alias",
        ),
    ),
)

ARTIFACT_TXID = "0123456789abcdef0123456789abcdef"
UNKNOWN_REGISTRY = RegistrySnapshot(
    session_id=None,
    profile="unknown",
    role="unknown",
    captured_at_utc=None,
    fingerprint=None,
    canonical_bytes=None,
    status="unknown",
)
UNKNOWN_AUTHORITY = AuthoritySnapshot(
    artifact_txid=None,
    profile="unknown",
    role="unknown",
    fingerprint=None,
    status="unknown",
)


def _oracle_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tool(*args: object, **overrides: object) -> dict[str, object]:
    name = overrides.pop("name", args[0] if args else "alpha")
    record: dict[str, object] = {
        "name": name,
        "description": f"literal {name}",
        "input_schema": {"type": "object", "properties": {}},
        "public_constraints": [f"constraint:{name}"],
        "effect_verification": "wire",
    }
    record.update(overrides)
    return record


def _pair_tool(profile: str, role: str) -> dict[str, object]:
    name = f"{profile}_{role}"
    return _tool(name, description=f"literal {profile}|{role}", public_constraints=[f"constraint:{name}"])


def _known_bundle(**overrides: object) -> AuthorityBundleBytes:
    fields = {
        "marker": KNOWN_MARKER_BYTES,
        "fingerprint_sidecar": KNOWN_FINGERPRINT_BYTES,
        "verdict_sidecar": KNOWN_VERDICT_BYTES,
        "producers_sidecar": KNOWN_PRODUCERS_BYTES,
        "receipts": KNOWN_RECEIPTS_BYTES,
    }
    fields.update(overrides)
    return AuthorityBundleBytes(**fields)


def _capture(profile_value: str, role_value: str, tools: object, **identity: object) -> RegistrySnapshot:
    profile = identity.get("profile", profile_value)
    role = identity.get("role", role_value)
    return capture_registry_snapshot(
        session_id=identity.get("session_id", "session-x"),
        profile=profile,
        role=role,
        captured_at_utc=identity.get("captured_at_utc", "2026-09-01T00:00:00Z"),
        tools=tools,
    )


def _loads(raw: bytes) -> object:
    return json.loads(raw.decode("utf-8"))


def _relink(marker: dict, fingerprint: bytes, verdict: bytes, producers: bytes, receipts_prefix: bytes | None = None) -> tuple[bytes, bytes]:
    marker = copy.deepcopy(marker)
    marker["fingerprint_sha256"] = _sha(fingerprint)
    marker["verdict_sha256"] = _sha(verdict)
    marker["producers_sha256"] = _sha(producers)
    marker["producers"] = _loads(producers)["producers"]
    marker_bytes = _oracle_json_bytes(marker)
    current = _oracle_json_bytes(
        {
            "kind": "commit",
            "operation_txid": marker["artifact_txid"],
            "artifact_txid": marker["artifact_txid"],
            "previous_schema_sha256": None,
            "schema_sha256": _sha(marker_bytes),
        }
    )
    prefix = KNOWN_RECEIPTS_BYTES.split(b"\n")[0] + b"\n" + KNOWN_RECEIPTS_BYTES.split(b"\n")[1] + b"\n"
    if receipts_prefix is not None:
        prefix = receipts_prefix
    return marker_bytes, prefix + current + b"\n"


class OracleReproductionTests(unittest.TestCase):
    def test_independent_oracle_reproduces_known_blobs_and_pair_table(self) -> None:
        self.assertEqual(len(NFC_CANONICAL_BYTES), 196)
        self.assertEqual(_sha(NFC_CANONICAL_BYTES), NFC_CANONICAL_SHA256)
        for pair, raw in PAIR_BYTES.items():
            self.assertEqual(_sha(raw), PAIR_SHA256[pair], pair)
        self.assertEqual(_oracle_json_bytes(_loads(KNOWN_MARKER_BYTES)), KNOWN_MARKER_BYTES)
        self.assertEqual(_oracle_json_bytes(_loads(KNOWN_VERDICT_BYTES)), KNOWN_VERDICT_BYTES)
        self.assertEqual(_oracle_json_bytes(_loads(KNOWN_PRODUCERS_BYTES)), KNOWN_PRODUCERS_BYTES)
        for path, fixture_sha, count, ids_sha, ids in BANK_TABLE:
            self.assertEqual(len(ids), count, path)
            self.assertEqual(_sha(_oracle_json_bytes({"ids": list(ids)})), ids_sha, path)
            self.assertEqual(len(fixture_sha), 64)


class RegistryCanonTests(unittest.TestCase):
    def test_four_pairs_match_external_bytes_and_sha(self) -> None:
        for profile, role in PAIR_BYTES:
            raw, digest = canonical_registry_fingerprint((_pair_tool(profile, role),))
            self.assertEqual(raw, PAIR_BYTES[(profile, role)])
            self.assertEqual(digest, PAIR_SHA256[(profile, role)])
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
            self.assertFalse(raw.endswith(b"\n"))
            self.assertNotIn(b"\r", raw)

    def test_record_order_does_not_change_digest_constraint_order_does(self) -> None:
        first = _tool("alpha", public_constraints=["c:a", "c:b"])
        second = _tool("zeta")
        forward, digest = canonical_registry_fingerprint((first, second))
        reversed_records, reversed_digest = canonical_registry_fingerprint((second, first))
        self.assertEqual(forward, reversed_records)
        self.assertEqual(digest, reversed_digest)
        swapped, swapped_digest = canonical_registry_fingerprint(
            (_tool("alpha", public_constraints=["c:b", "c:a"]), second)
        )
        self.assertNotEqual(swapped, forward)
        self.assertNotEqual(swapped_digest, digest)

    def test_mutating_each_field_changes_digest(self) -> None:
        base, digest = canonical_registry_fingerprint((_tool("alpha"),))
        mutants = (
            _tool("alpha", name="beta"),
            _tool("alpha", description="changed"),
            _tool("alpha", input_schema={"type": "object", "properties": {"x": {"type": "string"}}}),
            _tool("alpha", public_constraints=["constraint:changed"]),
            _tool("alpha", effect_verification="in_game_required"),
        )
        for mutant in mutants:
            raw, mutant_digest = canonical_registry_fingerprint((mutant,))
            self.assertNotEqual(raw, base)
            self.assertNotEqual(mutant_digest, digest)

    def test_invalid_records_and_sequences_yield_fully_unknown_snapshots(self) -> None:
        valid = _tool("alpha")
        cases = {
            "missing_name": {k: v for k, v in valid.items() if k != "name"},
            "missing_description": {k: v for k, v in valid.items() if k != "description"},
            "missing_schema": {k: v for k, v in valid.items() if k != "input_schema"},
            "missing_constraints": {k: v for k, v in valid.items() if k != "public_constraints"},
            "missing_effect": {k: v for k, v in valid.items() if k != "effect_verification"},
            "extra_field": {**valid, "extra": 1},
            "three_fields": {"name": "alpha", "description": "x", "input_schema": {}},
            "description_none": _tool("alpha", description=None),
            "name_empty": _tool(name=""),
            "name_int": _tool(name=1),
            "description_int": _tool("alpha", description=1),
            "schema_list": _tool("alpha", input_schema=[]),
            "constraints_tuple": _tool("alpha", public_constraints=("constraint:alpha",)),
            "constraint_empty": _tool("alpha", public_constraints=[""]),
            "constraint_int": _tool("alpha", public_constraints=[1]),
            "constraint_dup": _tool("alpha", public_constraints=["c:a", "c:a"]),
            "effect_other": _tool("alpha", effect_verification="status"),
            "item_list": ["not-mapping"],
        }
        for label, tools in cases.items():
            with self.subTest(label):
                payload = tools if label == "item_list" else (tools,)
                self.assertEqual(_capture("standard", "claude", payload), UNKNOWN_REGISTRY)
        sequence_cases = {
            "none": None,
            "mapping": {"name": "alpha"},
            "string": "alpha",
            "bytes": b"alpha",
            "generator": (item for item in (_tool("alpha"),)),
        }
        for label, tools in sequence_cases.items():
            with self.subTest(label):
                self.assertEqual(_capture("standard", "claude", tools), UNKNOWN_REGISTRY)
                self.assertIsInstance(sequence_cases["generator"], Iterator)
        self.assertEqual(
            _capture("standard", "claude", (_tool("dup"), _tool("dup"))),
            UNKNOWN_REGISTRY,
        )

    def test_nfc_positive_and_post_nfc_collisions(self) -> None:
        self.assertEqual(len(NFC_CANONICAL_BYTES), 196)
        nfc_tool = {
            "name": "cafe\u0301",
            "description": "Cafe\u0301.",
            "input_schema": {"type": "object", "properties": {"cafe\u0301": {"type": "string"}}},
            "public_constraints": ["constraint:cafe\u0301"],
            "effect_verification": "wire",
        }
        raw, digest = canonical_registry_fingerprint((nfc_tool,))
        self.assertEqual(raw, NFC_CANONICAL_BYTES)
        self.assertEqual(digest, NFC_CANONICAL_SHA256)
        self.assertEqual(
            _capture("standard", "claude", (_tool("cafe\u0301"), _tool("caf\u00e9"))),
            UNKNOWN_REGISTRY,
        )
        self.assertEqual(
            _capture(
                "standard",
                "claude",
                (_tool("alpha", public_constraints=["cafe\u0301", "caf\u00e9"]),),
            ),
            UNKNOWN_REGISTRY,
        )
        colliding = _tool(
            "alpha",
            input_schema={"type": "object", "properties": {"cafe\u0301": {"type": "string"}, "caf\u00e9": {"type": "integer"}}},
        )
        self.assertEqual(_capture("standard", "claude", (colliding,)), UNKNOWN_REGISTRY)

    def test_json_illegal_values_are_unknown_or_error(self) -> None:
        illegal = (
            _tool("alpha", input_schema={1: "x"}),
            _tool("alpha", input_schema={"x": (1, 2)}),
            _tool("alpha", input_schema={"x": {1, 2}}),
            _tool("alpha", input_schema={"x": b"ab"}),
            _tool("alpha", input_schema={"x": math.nan}),
            _tool("alpha", input_schema={"x": math.inf}),
            _tool("alpha", input_schema={"x": -math.inf}),
            _tool(name="caf\ud800"),
        )
        for item in illegal:
            with self.subTest(item=item):
                self.assertEqual(_capture("standard", "claude", (item,)), UNKNOWN_REGISTRY)
        with self.assertRaises(EffectiveSchemaError):
            canonical_json_bytes({"x": math.nan})
        with self.assertRaises(EffectiveSchemaError):
            canonical_json_bytes({1: "x"})


class SnapshotComparatorTests(unittest.TestCase):
    def test_four_pairs_known_and_session_does_not_enter_digest(self) -> None:
        for profile, role in PAIR_BYTES:
            snap = _capture(profile, role, (_pair_tool(profile, role),), session_id="session-a")
            other = _capture(profile, role, (_pair_tool(profile, role),), session_id="session-b")
            self.assertEqual(snap.status, "known")
            self.assertEqual(snap.canonical_bytes, PAIR_BYTES[(profile, role)])
            self.assertEqual(snap.fingerprint, PAIR_SHA256[(profile, role)])
            self.assertEqual(other.canonical_bytes, snap.canonical_bytes)
            self.assertEqual(other.fingerprint, snap.fingerprint)
            self.assertNotEqual(snap.session_id, other.session_id)

    def test_invalid_identity_is_fully_unknown(self) -> None:
        tools = (_pair_tool("standard", "claude"),)
        identities = (
            {"session_id": ""},
            {"session_id": None},
            {"session_id": 1},
            {"captured_at_utc": ""},
            {"captured_at_utc": None},
            {"captured_at_utc": 1},
            {"profile": "embedded"},
            {"role": "daemon"},
            {"profile": "standard|claude"},
            {"role": ""},
        )
        for identity in identities:
            with self.subTest(identity=identity):
                self.assertEqual(_capture("standard", "claude", tools, **identity), UNKNOWN_REGISTRY)

    def test_same_pair_distinct_registries_and_cross_pair_digest_copy(self) -> None:
        local_x = _capture("standard", "claude", (_pair_tool("standard", "claude"),), session_id="x")
        local_y = _capture("standard", "claude", (_tool("other"),), session_id="y")
        authority_y = read_authority_marker(
            _known_bundle(),
            expected_profile="standard",
            expected_role="claude",
        )
        # Authority Y in this gate is a distinct known snapshot for the same pair
        # with the Y digest, constructed only after the parser succeeds on the
        # frozen bundle and then rebound to the Y fingerprint for comparison.
        self.assertEqual(authority_y.status, "known")
        self.assertEqual(compare_snapshot_to_authority(local_x, authority_y), "fresh")
        authority_y_digest = AuthoritySnapshot(
            artifact_txid=authority_y.artifact_txid,
            profile=authority_y.profile,
            role=authority_y.role,
            fingerprint=local_y.fingerprint,
            status="known",
        )
        self.assertEqual(compare_snapshot_to_authority(local_x, authority_y_digest), "stale")
        self.assertEqual(compare_snapshot_to_authority(local_y, authority_y_digest), "fresh")
        crossed = read_authority_marker(
            _known_bundle(),
            expected_profile="standard",
            expected_role="codex",
        )
        copied = AuthoritySnapshot(
            artifact_txid=crossed.artifact_txid,
            profile="standard",
            role="codex",
            fingerprint=local_x.fingerprint,
            status="known",
        )
        self.assertEqual(compare_snapshot_to_authority(local_x, copied), "unknown")

    def test_manual_incoherent_dataclasses_are_unknown(self) -> None:
        local = _capture("standard", "claude", (_pair_tool("standard", "claude"),))
        incoherent_local = RegistrySnapshot(
            session_id=local.session_id,
            profile=local.profile,
            role=local.role,
            captured_at_utc=local.captured_at_utc,
            fingerprint="0" * 64,
            canonical_bytes=local.canonical_bytes,
            status="known",
        )
        authority = read_authority_marker(
            _known_bundle(),
            expected_profile="standard",
            expected_role="claude",
        )
        self.assertEqual(compare_snapshot_to_authority(incoherent_local, authority), "unknown")
        partial_unknown = RegistrySnapshot(
            session_id="x",
            profile="unknown",
            role="unknown",
            captured_at_utc=None,
            fingerprint=None,
            canonical_bytes=None,
            status="unknown",
        )
        self.assertEqual(compare_snapshot_to_authority(partial_unknown, authority), "unknown")
        bad_txid = AuthoritySnapshot(
            artifact_txid="ZZ",
            profile="standard",
            role="claude",
            fingerprint=local.fingerprint,
            status="known",
        )
        self.assertEqual(compare_snapshot_to_authority(local, bad_txid), "unknown")
        self.assertEqual(compare_snapshot_to_authority(UNKNOWN_REGISTRY, UNKNOWN_AUTHORITY), "unknown")


class AuthorityBundleTests(unittest.TestCase):
    def test_literal_bundle_is_known_for_each_pair(self) -> None:
        for profile, role in PAIR_BYTES:
            authority = read_authority_marker(
                _known_bundle(),
                expected_profile=profile,
                expected_role=role,
            )
            self.assertEqual(authority.status, "known")
            self.assertEqual(authority.artifact_txid, ARTIFACT_TXID)
            self.assertEqual(authority.profile, profile)
            self.assertEqual(authority.role, role)
            self.assertEqual(authority.fingerprint, PAIR_SHA256[(profile, role)])

    def test_each_blob_none_is_unknown(self) -> None:
        for field in ("marker", "fingerprint_sidecar", "verdict_sidecar", "producers_sidecar", "receipts"):
            with self.subTest(field=field):
                bundle = _known_bundle(**{field: None})
                self.assertEqual(
                    read_authority_marker(bundle, expected_profile="standard", expected_role="claude"),
                    UNKNOWN_AUTHORITY,
                )

    def test_marker_shape_and_payload_order_mutations(self) -> None:
        marker = _loads(KNOWN_MARKER_BYTES)
        cases = []
        missing = copy.deepcopy(marker)
        missing.pop("generator")
        cases.append(_oracle_json_bytes(missing))
        extra = copy.deepcopy(marker)
        extra["extra"] = 1
        cases.append(_oracle_json_bytes(extra))
        version = copy.deepcopy(marker)
        version["artifact_version"] = True
        cases.append(_oracle_json_bytes(version))
        schema = copy.deepcopy(marker)
        schema["schema_version"] = 2
        cases.append(_oracle_json_bytes(schema))
        txid = copy.deepcopy(marker)
        txid["artifact_txid"] = "ABCDEF"
        cases.append(_oracle_json_bytes(txid))
        generator = copy.deepcopy(marker)
        generator["generator"] = {"name": "m14-oracle"}
        cases.append(_oracle_json_bytes(generator))
        order = copy.deepcopy(marker)
        order["payloads"] = list(reversed(order["payloads"]))
        cases.append(_oracle_json_bytes(order))
        for raw in cases:
            with self.subTest(raw=raw[:40]):
                self.assertEqual(
                    read_authority_marker(_known_bundle(marker=raw), expected_profile="standard", expected_role="claude"),
                    UNKNOWN_AUTHORITY,
                )

    def test_manifest_spacing_order_and_digest_mutations(self) -> None:
        good = KNOWN_FINGERPRINT_BYTES
        lines = good.split(b"\n")
        mutants = [
            b" ".join(good.split(b"  ")),  # one space
            good.replace(b"  ", b"   ", 1),
            good.replace(b"\n", b"\r\n"),
            good[:-1],
            good + b"extra\n",
            good.replace(b"standard|claude", b"claude|standard"),
            b"\t" + good,
            lines[1] + b"\n" + lines[0] + b"\n" + lines[2] + b"\n" + lines[3] + b"\n",
            good[:64] + b"0" + good[65:],
        ]
        for raw in mutants:
            with self.subTest(raw=raw[:20]):
                self.assertEqual(
                    read_authority_marker(
                        _known_bundle(fingerprint_sidecar=raw),
                        expected_profile="standard",
                        expected_role="claude",
                    ),
                    UNKNOWN_AUTHORITY,
                )

    def test_payload_tool_and_hash_sidecars_must_stay_linked(self) -> None:
        marker = _loads(KNOWN_MARKER_BYTES)
        marker["payloads"][0]["tools"][0]["description"] = "mutated without digest"
        self.assertEqual(
            read_authority_marker(
                _known_bundle(marker=_oracle_json_bytes(marker)),
                expected_profile="standard",
                expected_role="claude",
            ),
            UNKNOWN_AUTHORITY,
        )
        marker = _loads(KNOWN_MARKER_BYTES)
        marker["fingerprint_sha256"] = "0" * 64
        self.assertEqual(
            read_authority_marker(
                _known_bundle(marker=_oracle_json_bytes(marker)),
                expected_profile="standard",
                expected_role="claude",
            ),
            UNKNOWN_AUTHORITY,
        )
        verdict = _loads(KNOWN_VERDICT_BYTES)
        verdict["verdict"] = "FAIL"
        self.assertEqual(
            read_authority_marker(
                _known_bundle(verdict_sidecar=_oracle_json_bytes(verdict)),
                expected_profile="standard",
                expected_role="claude",
            ),
            UNKNOWN_AUTHORITY,
        )
        producers = _loads(KNOWN_PRODUCERS_BYTES)
        producers["producers"][0]["sha256"] = "0" * 64
        self.assertEqual(
            read_authority_marker(
                _known_bundle(producers_sidecar=_oracle_json_bytes(producers)),
                expected_profile="standard",
                expected_role="claude",
            ),
            UNKNOWN_AUTHORITY,
        )

    def test_verdict_member_result_and_external_table_mutations(self) -> None:
        verdict = _loads(KNOWN_VERDICT_BYTES)
        verdict["bank_members"][0]["verdict"] = "FAIL"
        self.assertEqual(
            read_authority_marker(_known_bundle(verdict_sidecar=_oracle_json_bytes(verdict)), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )
        omitted = _loads(KNOWN_VERDICT_BYTES)
        omitted["bank_members"][0]["results"] = omitted["bank_members"][0]["results"][1:]
        omitted["bank_members"][0]["expected_ids"] = omitted["bank_members"][0]["expected_ids"][1:]
        self.assertEqual(
            read_authority_marker(_known_bundle(verdict_sidecar=_oracle_json_bytes(omitted)), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )
        extra = _loads(KNOWN_VERDICT_BYTES)
        extra["bank_members"][0]["expected_ids"].append("extra-id")
        extra["bank_members"][0]["results"].append({"id": "extra-id", "expected": True, "observed": True, "verdict": "PASS"})
        self.assertEqual(
            read_authority_marker(_known_bundle(verdict_sidecar=_oracle_json_bytes(extra)), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )
        reordered = _loads(KNOWN_VERDICT_BYTES)
        reordered["bank_members"] = list(reversed(reordered["bank_members"]))
        self.assertEqual(
            read_authority_marker(_known_bundle(verdict_sidecar=_oracle_json_bytes(reordered)), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )
        fixture = _loads(KNOWN_VERDICT_BYTES)
        fixture["bank_members"][0]["sha256"] = "0" * 64
        self.assertEqual(
            read_authority_marker(_known_bundle(verdict_sidecar=_oracle_json_bytes(fixture)), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )
        # Autoconsistent expected_ids change still fails the external table.
        auto = _loads(KNOWN_VERDICT_BYTES)
        auto["bank_members"][0]["expected_ids"] = list(auto["bank_members"][0]["expected_ids"]) + ["injected"]
        auto["bank_members"][0]["results"].append({"id": "injected", "expected": True, "observed": True, "verdict": "PASS"})
        auto_bytes = _oracle_json_bytes(auto)
        marker, receipts = _relink(_loads(KNOWN_MARKER_BYTES), KNOWN_FINGERPRINT_BYTES, auto_bytes, KNOWN_PRODUCERS_BYTES)
        self.assertEqual(
            read_authority_marker(
                _known_bundle(marker=marker, verdict_sidecar=auto_bytes, receipts=receipts),
                expected_profile="standard",
                expected_role="claude",
            ),
            UNKNOWN_AUTHORITY,
        )

    def test_noncanonical_json_and_utf8_are_unknown(self) -> None:
        mutants = [
            b"\xef\xbb\xbf" + KNOWN_MARKER_BYTES,
            KNOWN_MARKER_BYTES.replace(b",", b", "),
            KNOWN_MARKER_BYTES + b"\n",
            KNOWN_MARKER_BYTES.replace(b'"schema_version":1', b'"schema_version":\n1'),
            KNOWN_MARKER_BYTES.replace(b"artifact_txid", b"\\u0061rtifact_txid") if False else KNOWN_MARKER_BYTES[:20] + b"\xff" + KNOWN_MARKER_BYTES[21:],
            KNOWN_MARKER_BYTES.replace(b'"name":"m14-oracle"', b'"name":"m14-oracle","name":"dup"'),
        ]
        pretty = json.dumps(_loads(KNOWN_MARKER_BYTES), indent=2).encode("utf-8")
        mutants.append(pretty)
        escaped = KNOWN_MARKER_BYTES.replace(b"m14-oracle", b"m14-\\u006fracle")
        mutants.append(escaped)
        for raw in mutants:
            with self.subTest(raw=raw[:24]):
                self.assertEqual(
                    read_authority_marker(_known_bundle(marker=raw), expected_profile="standard", expected_role="claude"),
                    UNKNOWN_AUTHORITY,
                )

    def test_producer_union_validator_sources_and_approval(self) -> None:
        producers = _loads(KNOWN_PRODUCERS_BYTES)
        dropped = copy.deepcopy(producers)
        dropped["producers"] = [item for item in dropped["producers"] if item["path"] != "tools/dayz_mcp/server.py"]
        dropped_bytes = _oracle_json_bytes(dropped)
        marker, receipts = _relink(_loads(KNOWN_MARKER_BYTES), KNOWN_FINGERPRINT_BYTES, KNOWN_VERDICT_BYTES, dropped_bytes)
        self.assertEqual(
            read_authority_marker(
                _known_bundle(marker=marker, producers_sidecar=dropped_bytes, receipts=receipts),
                expected_profile="standard",
                expected_role="claude",
            ),
            UNKNOWN_AUTHORITY,
        )
        dup = copy.deepcopy(producers)
        dup["producers"] = list(dup["producers"]) + [dup["producers"][0]]
        self.assertEqual(
            read_authority_marker(_known_bundle(producers_sidecar=_oracle_json_bytes(dup)), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )
        unordered = copy.deepcopy(producers)
        unordered["producers"] = list(reversed(unordered["producers"]))
        self.assertEqual(
            read_authority_marker(_known_bundle(producers_sidecar=_oracle_json_bytes(unordered)), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )
        bad_path = copy.deepcopy(producers)
        bad_path["producers"][0]["path"] = "C:/tools/approved-launchers.json"
        self.assertEqual(
            read_authority_marker(_known_bundle(producers_sidecar=_oracle_json_bytes(bad_path)), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )
        fixture_hash = copy.deepcopy(producers)
        for item in fixture_hash["producers"]:
            if item["path"].endswith("required_constraint_ids.json"):
                item["sha256"] = "0" * 64
        self.assertEqual(
            read_authority_marker(_known_bundle(producers_sidecar=_oracle_json_bytes(fixture_hash)), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )
        missing_source = copy.deepcopy(producers)
        missing_source["validator_sources"].pop("build_app.instructions")
        self.assertEqual(
            read_authority_marker(_known_bundle(producers_sidecar=_oracle_json_bytes(missing_source)), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )
        extra_source = copy.deepcopy(producers)
        extra_source["validator_sources"]["extra.source"] = "tools/dayz_mcp/server.py"
        self.assertEqual(
            read_authority_marker(_known_bundle(producers_sidecar=_oracle_json_bytes(extra_source)), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )
        remapped = copy.deepcopy(producers)
        remapped["validator_sources"]["build_app.instructions"] = "tools/dayz_mcp/knowledge.py"
        self.assertEqual(
            read_authority_marker(_known_bundle(producers_sidecar=_oracle_json_bytes(remapped)), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )
        approval = copy.deepcopy(producers)
        approval["active_approval"]["rolled_back_absent"] = False
        self.assertEqual(
            read_authority_marker(_known_bundle(producers_sidecar=_oracle_json_bytes(approval)), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )
        approval_path = copy.deepcopy(producers)
        approval_path["active_approval"]["prepared_path"] = "tools/other/prepared.json"
        self.assertEqual(
            read_authority_marker(_known_bundle(producers_sidecar=_oracle_json_bytes(approval_path)), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )

    def test_receipts_and_digest_only_incomplete_parser(self) -> None:
        self.assertEqual(
            read_authority_marker(_known_bundle(receipts=b""), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )
        self.assertEqual(
            read_authority_marker(_known_bundle(receipts=b"\xff\n"), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )
        self.assertEqual(
            read_authority_marker(_known_bundle(receipts=b"{not json}\n"), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )
        current = _oracle_json_bytes(
            {
                "kind": "commit",
                "operation_txid": ARTIFACT_TXID,
                "artifact_txid": ARTIFACT_TXID,
                "previous_schema_sha256": None,
                "schema_sha256": "0" * 64,
            }
        )
        prefix = b"\n".join(KNOWN_RECEIPTS_BYTES.split(b"\n")[:2]) + b"\n"
        self.assertEqual(
            read_authority_marker(_known_bundle(receipts=prefix + current + b"\n"), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )
        doubled = KNOWN_RECEIPTS_BYTES + KNOWN_RECEIPTS_BYTES.split(b"\n")[2] + b"\n"
        self.assertEqual(
            read_authority_marker(_known_bundle(receipts=doubled), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )
        extra_key = _oracle_json_bytes(
            {
                "kind": "commit",
                "operation_txid": ARTIFACT_TXID,
                "artifact_txid": ARTIFACT_TXID,
                "previous_schema_sha256": None,
                "schema_sha256": _sha(KNOWN_MARKER_BYTES),
                "extra": 1,
            }
        )
        self.assertEqual(
            read_authority_marker(_known_bundle(receipts=prefix + extra_key + b"\n"), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )
        no_lf = KNOWN_RECEIPTS_BYTES.rstrip(b"\n")
        self.assertEqual(
            read_authority_marker(_known_bundle(receipts=no_lf), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )
        historical_only = KNOWN_RECEIPTS_BYTES.split(b"\n")[0] + b"\n" + KNOWN_RECEIPTS_BYTES.split(b"\n")[1] + b"\n"
        self.assertEqual(
            read_authority_marker(_known_bundle(receipts=historical_only), expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )
        # Historical + legacy remain acceptable when the current commit is present.
        self.assertEqual(
            read_authority_marker(_known_bundle(), expected_profile="standard", expected_role="claude").status,
            "known",
        )
        digest_only = AuthorityBundleBytes(
            marker=b'{"artifact_txid":"%s"}' % ARTIFACT_TXID.encode(),
            fingerprint_sidecar=KNOWN_FINGERPRINT_BYTES,
            verdict_sidecar=None,
            producers_sidecar=None,
            receipts=None,
        )
        self.assertEqual(
            read_authority_marker(digest_only, expected_profile="standard", expected_role="claude"),
            UNKNOWN_AUTHORITY,
        )


if __name__ == "__main__":
    unittest.main()
