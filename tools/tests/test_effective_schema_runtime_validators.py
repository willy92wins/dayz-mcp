"""Synthetic runtime enumerator tests; no server or fixture imports are allowed."""

from __future__ import annotations

import unittest

from dayz_mcp import effective_schema_runtime_validators as runtime_validators


class EffectiveSchemaRuntimeValidatorTests(unittest.TestCase):
    def test_enumerates_schema_properties_and_explicit_manual_adapters(self) -> None:
        tools = [
            {
                "name": "alpha",
                "input_schema": {"type": "object", "properties": {"z": {}, "a": {}}},
            },
            {"name": "empty", "input_schema": {"type": "object", "properties": {}}},
        ]
        adapters = {
            "manual:boundary": {"source": "synthetic"},
            "manual:other": {"source": "synthetic"},
        }

        result = runtime_validators.enumerate_constraints(tools, adapters)

        self.assertEqual(
            result,
            (
                "manual:boundary",
                "manual:other",
                "schema:alpha:a",
                "schema:alpha:z",
            ),
        )

    def test_extra_constraint_requires_explicit_no_extra_wrapper(self) -> None:
        tools = [{"name": "strict", "input_schema": {"properties": {}}}]
        without_wrapper = runtime_validators.enumerate_constraints(
            tools, {"strict": {"manual": True}}
        )
        with_wrapper = runtime_validators.enumerate_constraints(
            tools, {"strict": {"manual": True, "additional_properties": False}}
        )
        self.assertEqual(without_wrapper, ("manual:strict",))
        self.assertEqual(with_wrapper, ("manual:strict", "schema:strict:__extra__"))

    def test_rejects_dangling_or_duplicate_runtime_adapters(self) -> None:
        tools = [{"name": "known", "input_schema": {"properties": {}}}]
        with self.assertRaises(runtime_validators.RuntimeValidatorError):
            runtime_validators.enumerate_constraints(tools, {"unknown": {}})
        with self.assertRaises(runtime_validators.RuntimeValidatorError):
            runtime_validators.enumerate_constraints(
                [
                    {"name": "known", "input_schema": {"properties": {"x": {}}}},
                    {"name": "known", "input_schema": {"properties": {}}},
                ],
                {},
            )

    def test_runtime_view_has_a_stable_record_shape(self) -> None:
        view = runtime_validators.build_runtime_view(
            [{"name": "known", "input_schema": {"properties": {"x": {}}}}],
            {"manual:known": {"source": "synthetic"}},
        )
        self.assertEqual(view, {"schema_version": 1, "constraint_ids": ["manual:known", "schema:known:x"]})


if __name__ == "__main__":
    unittest.main()
