"""Pure, synthetic contract tests for the M13 effective-schema core."""

from __future__ import annotations

import inspect
import json
import unittest
from pathlib import Path
from typing import Literal, get_type_hints

from dayz_mcp import effective_schema_core


class EffectiveSchemaCoreTests(unittest.TestCase):
    def test_extracts_synthetic_records_without_reordering_or_runtime_access(self) -> None:
        records = [
            {
                "name": "first",
                "description": "First tool.",
                "inputSchema": {"type": "object", "properties": {"x": {"type": "integer"}}},
            },
            {
                "name": "second",
                "description": "Second tool.",
                "input_schema": {"type": "object", "properties": {}},
            },
        ]

        extracted = effective_schema_core.extract_tool_records(records)

        self.assertEqual(
            extracted,
            (
                {
                    "name": "first",
                    "description": "First tool.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"x": {"type": "integer"}},
                    },
                },
                {"name": "second", "description": "Second tool.", "input_schema": {"type": "object", "properties": {}}},
            ),
        )

    def test_builds_canonical_payload_from_injected_values(self) -> None:
        tools = (
            {
                "name": "synthetic",
                "description": "Injected description",
                "input_schema": {"type": "object", "properties": {}},
                "public_constraints": ["manual:synthetic"],
                "effect_verification": "wire",
            },
        )

        payload = effective_schema_core.build_payload(
            profile="standard",
            role="claude",
            instructions="Injected instructions",
            tools=tools,
            tool_registry_fingerprint="a" * 64,
        )

        self.assertEqual(payload["profile"], "standard")
        self.assertEqual(payload["role"], "claude")
        self.assertEqual(payload["instructions"], "Injected instructions")
        self.assertEqual(payload["tools"], list(tools))
        self.assertEqual(payload["tool_registry_fingerprint"], "a" * 64)

    def test_envelope_orders_profiles_and_rejects_duplicate_identity(self) -> None:
        def payload(profile: str, role: str) -> dict[str, object]:
            return effective_schema_core.build_payload(
                profile=profile,
                role=role,
                instructions="",
                tools=(),
                tool_registry_fingerprint="b" * 64,
            )

        envelope = effective_schema_core.build_envelope(
            [payload("exec_enforce", "codex"), payload("standard", "claude")]
        )
        self.assertEqual(
            [(item["profile"], item["role"]) for item in envelope["payloads"]],
            [("standard", "claude"), ("exec_enforce", "codex")],
        )
        with self.assertRaises(effective_schema_core.EffectiveSchemaError):
            effective_schema_core.build_envelope([payload("standard", "claude")] * 2)

    def test_validation_is_fail_closed_for_unknown_effect_verification(self) -> None:
        with self.assertRaises(effective_schema_core.EffectiveSchemaError):
            effective_schema_core.build_payload(
                profile="standard",
                role="claude",
                instructions="",
                tools=(
                    {
                        "name": "bad",
                        "description": "",
                        "input_schema": {},
                        "public_constraints": [],
                        "effect_verification": "unknown",
                    },
                ),
                tool_registry_fingerprint="c" * 64,
            )

    def test_validation_rejects_unknown_identity_bad_fingerprint_and_extra_payload(self) -> None:
        valid_tool = {
            "name": "known",
            "description": "",
            "input_schema": {},
            "public_constraints": [],
            "effect_verification": "wire",
        }
        for profile, role in (("unknown", "claude"), ("standard", "unknown")):
            with self.subTest(profile=profile, role=role):
                with self.assertRaises(effective_schema_core.EffectiveSchemaError):
                    effective_schema_core.build_payload(profile, role, "", (valid_tool,), "d" * 64)
        for fingerprint in ("d" * 63, "D" * 64):
            with self.subTest(fingerprint=fingerprint):
                with self.assertRaises(effective_schema_core.EffectiveSchemaError):
                    effective_schema_core.build_payload("standard", "claude", "", (valid_tool,), fingerprint)
        payload = effective_schema_core.build_payload("standard", "claude", "", (valid_tool,), "e" * 64)
        payload["unexpected"] = True
        with self.assertRaises(effective_schema_core.EffectiveSchemaError):
            effective_schema_core.build_envelope([payload])


class EffectiveSchemaIdentityProjectionTests(unittest.TestCase):
    def test_public_aliases_signature_annotations_and_exports(self) -> None:
        self.assertEqual(
            effective_schema_core.ProjectedProfile,
            Literal["standard", "exec_enforce", "unknown"],
        )
        self.assertEqual(
            effective_schema_core.ProjectedRole,
            Literal["claude", "codex", "unknown"],
        )
        self.assertEqual(
            effective_schema_core.ProjectedIdentity,
            tuple[effective_schema_core.ProjectedProfile, effective_schema_core.ProjectedRole],
        )
        for name in (
            "ProjectedProfile",
            "ProjectedRole",
            "ProjectedIdentity",
            "project_server_config_identity",
        ):
            self.assertIn(name, effective_schema_core.__all__)
        fn = effective_schema_core.project_server_config_identity
        signature = inspect.signature(fn)
        self.assertEqual(list(signature.parameters), ["enable_exec_enforce", "client_platform"])
        self.assertNotIn("tools", signature.parameters)
        for name, parameter in signature.parameters.items():
            self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
            self.assertIs(parameter.default, None)
            self.assertEqual(parameter.annotation, "object")
        self.assertEqual(signature.return_annotation, "ProjectedIdentity")
        hints = get_type_hints(fn)
        self.assertEqual(hints["enable_exec_enforce"], object)
        self.assertEqual(hints["client_platform"], object)
        self.assertEqual(hints["return"], effective_schema_core.ProjectedIdentity)

    def test_four_positive_pairs_are_literal_and_independent(self) -> None:
        self.assertEqual(
            effective_schema_core.project_server_config_identity(
                enable_exec_enforce=False,
                client_platform="claude",
            ),
            ("standard", "claude"),
        )
        self.assertEqual(
            effective_schema_core.project_server_config_identity(
                enable_exec_enforce=False,
                client_platform="codex",
            ),
            ("standard", "codex"),
        )
        self.assertEqual(
            effective_schema_core.project_server_config_identity(
                enable_exec_enforce=True,
                client_platform="claude",
            ),
            ("exec_enforce", "claude"),
        )
        self.assertEqual(
            effective_schema_core.project_server_config_identity(
                enable_exec_enforce=True,
                client_platform="codex",
            ),
            ("exec_enforce", "codex"),
        )

    def test_omitted_or_none_fields_return_full_unknown_without_typeerror(self) -> None:
        unknown = ("unknown", "unknown")
        self.assertEqual(effective_schema_core.project_server_config_identity(), unknown)
        self.assertEqual(
            effective_schema_core.project_server_config_identity(enable_exec_enforce=False),
            unknown,
        )
        self.assertEqual(
            effective_schema_core.project_server_config_identity(client_platform="claude"),
            unknown,
        )
        self.assertEqual(
            effective_schema_core.project_server_config_identity(
                enable_exec_enforce=None,
                client_platform="claude",
            ),
            unknown,
        )
        self.assertEqual(
            effective_schema_core.project_server_config_identity(
                enable_exec_enforce=False,
                client_platform=None,
            ),
            unknown,
        )

    def test_invalid_types_and_roles_return_full_unknown(self) -> None:
        unknown = ("unknown", "unknown")
        cases = (
            (0, "claude"),
            (1, "codex"),
            ("false", "claude"),
            (False, 0),
            (False, True),
            (False, "Claude"),
            (False, "unknown"),
            (False, "grok"),
            (False, ""),
            (True, None),
            (True, 0),
            (True, True),
            (True, "Claude"),
            (True, "unknown"),
            (True, "grok"),
            (True, ""),
            (object(), "claude"),
            (object(), "codex"),
        )
        for enable, platform in cases:
            with self.subTest(enable_exec_enforce=enable, client_platform=platform):
                self.assertEqual(
                    effective_schema_core.project_server_config_identity(
                        enable_exec_enforce=enable,
                        client_platform=platform,
                    ),
                    unknown,
                )

    def test_projection_is_pure_and_does_not_mutate_inputs(self) -> None:
        first = effective_schema_core.project_server_config_identity(
            enable_exec_enforce=False,
            client_platform="claude",
        )
        second = effective_schema_core.project_server_config_identity(
            enable_exec_enforce=False,
            client_platform="claude",
        )
        self.assertEqual(first, ("standard", "claude"))
        self.assertEqual(second, ("standard", "claude"))
        marker = ["claude"]
        self.assertEqual(
            effective_schema_core.project_server_config_identity(
                enable_exec_enforce=False,
                client_platform=marker,
            ),
            ("unknown", "unknown"),
        )
        self.assertEqual(marker, ["claude"])

    def test_inventory_fixture_corroborates_four_positive_configs_only(self) -> None:
        path = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "effective_schema_v5"
            / "profile_inventory.json"
        )
        records = json.loads(path.read_text(encoding="utf-8"))["profiles"]
        self.assertEqual(
            [
                (
                    item["profile"],
                    item["role"],
                    item["config"]["enable_exec_enforce"],
                    item["config"]["client_platform"],
                )
                for item in records
            ],
            [
                ("standard", "claude", False, "claude"),
                ("standard", "codex", False, "codex"),
                ("exec_enforce", "claude", True, "claude"),
                ("exec_enforce", "codex", True, "codex"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
