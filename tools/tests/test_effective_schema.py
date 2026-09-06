"""Effective schemas are the post-build_app public contract, not the fn body."""
from __future__ import annotations

import unittest

from dayz_mcp.effective_schema import (
    _json_enum,
    _json_type,
    audit_contracts,
    resolve_effective_schemas,
)


def _param(required: bool, enum=None):
    return {"required": required, "default": None, "type": "string", "enum": enum}


class EffectiveSchemaTests(unittest.TestCase):
    def test_scene_raycast_exposes_from_not_from_pos(self) -> None:
        schemas = resolve_effective_schemas()
        self.assertIn("scene_raycast", schemas)
        params = schemas["scene_raycast"]["params"]
        self.assertIn("from", params)
        self.assertNotIn("from_pos", params)
        self.assertIs(True, params["from"]["required"])
        self.assertEqual(params["from"]["type"], "array")

    def test_resolve_shape_has_description_and_params(self) -> None:
        schemas = resolve_effective_schemas()
        self.assertGreaterEqual(len(schemas), 40)
        sample = schemas["scene_raycast"]
        self.assertIsInstance(sample["description"], str)
        self.assertTrue(sample["description"])
        entry = sample["params"]["from"]
        self.assertGreaterEqual(set(entry), {"required", "default", "type", "enum"})

    def test_live_audit_flags_type_classname_divergence(self) -> None:
        findings = audit_contracts()
        self.assertTrue(findings, "live tree splits type/classname; auditor must report it")
        divergences = [
            item
            for item in findings
            if item.get("code") == "PARAM-NAME-DIVERGENCE"
        ]
        self.assertTrue(divergences)
        text = " ".join(str(item) for item in divergences)
        self.assertIn("world_spawn", text)
        self.assertIn("inventory_give", text)

    def test_live_audit_does_not_flag_scene_raycast_from_alias(self) -> None:
        findings = audit_contracts()
        aliased = [
            item
            for item in findings
            if "scene_raycast" in str(item).lower()
            and (
                "from_pos" in str(item).lower()
                or "'from'" in str(item).lower()
                or '"from"' in str(item).lower()
            )
        ]
        self.assertEqual(aliased, [])

    def test_injected_schema_flags_desc_enum_and_name_split(self) -> None:
        schemas = {
            "probe_run": {
                "description": "Run a thing. mode is server|all|client.",
                "params": {
                    "mode": _param(
                        True, ["offline", "server", "client", "all"]
                    ),
                },
            },
            "probe_spawn": {
                "description": "Spawn one entity by class.",
                "params": {"type": _param(True)},
            },
            "probe_give": {
                "description": "Give one entity by class.",
                "params": {"classname": _param(True)},
            },
        }
        findings = audit_contracts(schemas)
        text = " ".join(str(item) for item in findings).lower()
        self.assertTrue(
            any(
                item.get("code") == "DESC-ENUM-MISMATCH" and item.get("tool") == "probe_run"
                for item in findings
            )
        )
        self.assertTrue("probe_spawn" in text or "probe_give" in text)
        self.assertTrue(any(item.get("code") == "PARAM-NAME-DIVERGENCE" for item in findings))

    def test_coherent_injected_schema_is_silent(self) -> None:
        schemas = {
            "clean_run": {
                "description": "Run a thing. mode is offline|server|client|all.",
                "params": {
                    "mode": _param(
                        True, ["offline", "server", "client", "all"]
                    ),
                },
            },
            "clean_spawn": {
                "description": "Spawn one entity by class.",
                "params": {"classname": _param(True)},
            },
            "clean_give": {
                "description": "Give one entity by class.",
                "params": {"classname": _param(True)},
            },
        }
        self.assertEqual(audit_contracts(schemas), [])

    def test_marker_union_keeps_string_and_object(self) -> None:
        schemas = resolve_effective_schemas()
        for tool_name in ("logs_since", "wait_for"):
            published = schemas[tool_name]["params"]["marker"]["type"]
            published_set = (
                set(published)
                if isinstance(published, (list, tuple, set))
                else {published}
            )
            self.assertLessEqual(
                {"string", "object"},
                published_set,
                f"{tool_name}.marker type {published!r} dropped a union branch",
            )

    def test_two_enums_in_one_description_are_not_crossed(self) -> None:
        schemas = {
            "t": {
                "description": "source is auto|manual. mode is safe|manual.",
                "params": {
                    "source": _param(True, ["auto", "manual"]),
                    "mode": _param(True, ["safe", "manual"]),
                },
            }
        }
        self.assertEqual(audit_contracts(schemas), [])

    def test_disjoint_pipe_list_before_param_name_is_mismatch(self) -> None:
        schemas = {
            "t": {
                "description": "server|client are valid values for mode.",
                "params": {"mode": _param(True, ["offline", "local"])},
            }
        }
        findings = audit_contracts(schemas)
        self.assertTrue(
            any(
                item.get("code") == "DESC-ENUM-MISMATCH" and item.get("tool") == "t"
                for item in findings
            )
        )

    def test_divergence_evidence_does_not_claim_object_class(self) -> None:
        schemas = {
            "decode": {
                "description": "Decode a payload. type is the serialization format.",
                "params": {"type": _param(True)},
            },
            "spawn": {
                "description": "Spawn an entity by class.",
                "params": {"classname": _param(True)},
            },
        }
        findings = audit_contracts(schemas)
        self.assertTrue(any(item.get("code") == "PARAM-NAME-DIVERGENCE" for item in findings))
        for item in findings:
            if item.get("tool") == "decode":
                self.assertNotIn(
                    "object class",
                    str(item.get("evidence", "")).lower(),
                )

    def test_nested_union_keeps_every_branch(self) -> None:
        """A union inside a union is still a union: no branch may be dropped."""
        nested = {
            "anyOf": [
                {"type": "string"},
                {"anyOf": [{"type": "integer"}, {"type": "number"}]},
            ]
        }
        self.assertEqual(_json_type(nested), ["string", "integer", "number"])

    def test_every_branch_enum_survives_the_union(self) -> None:
        """Keeping only the first branch's enum invents DESC-ENUM-MISMATCH findings."""
        per_branch = {
            "anyOf": [
                {"type": "string", "enum": ["a", "b"]},
                {"type": "integer", "enum": [1, 2]},
            ]
        }
        self.assertEqual(_json_enum(per_branch), ["a", "b", 1, 2])

    def test_cyclic_schema_resolves_instead_of_recursing(self) -> None:
        """A self-referencing schema must return a value, not RecursionError."""
        cyclic: dict = {}
        cyclic["anyOf"] = [cyclic]
        self.assertIsNone(_json_type(cyclic))
        self.assertIsNone(_json_enum(cyclic))

    def test_non_scalar_enum_values_do_not_crash_the_audit(self) -> None:
        """audit_contracts takes injected records, so its enum compare must not hash."""
        schemas = {
            "t": {
                "description": "mode is alpha|beta.",
                "params": {"mode": _param(True, [{"alpha": 1}, ["beta"]])},
            }
        }
        findings = audit_contracts(schemas)
        self.assertTrue(any(item.get("code") == "DESC-ENUM-MISMATCH" for item in findings))


if __name__ == "__main__":
    unittest.main()
