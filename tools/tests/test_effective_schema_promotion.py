"""ficha d366: v1 constraint ids promote onto the v5 concept list."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from promote_effective_schema import (
    PromotionError,
    promote_v1_constraint_ids,
    promote_v1_document,
    promotion_matches_v5,
    v5_concept_ids,
)


ROOT = Path(__file__).parent / "fixtures"


def _read(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


class EffectiveSchemaPromotionTests(unittest.TestCase):
    def test_bank_v1_promotes_onto_bank_v5_concepts(self) -> None:
        v1 = _read("effective_schema_v1/required_constraint_ids.json")
        v5 = _read("effective_schema_v5/instructions_required_concepts.json")
        self.assertTrue(promotion_matches_v5(v1, v5))
        self.assertEqual(
            promote_v1_document(v1),
            (
                "new_site_guard",
                "spawn_y_provider",
                "living_infected_flags",
                "wait_log_sources",
                "wait_default_lookback",
                "action_use_target_contract",
            ),
        )
        self.assertEqual(v5_concept_ids(v5), promote_v1_document(v1))

    def test_schema_layer_ids_are_not_concepts(self) -> None:
        self.assertEqual(
            promote_v1_constraint_ids(
                ("schema:dayz_test_run:mission", "manual:new_site_guard")
            ),
            ("new_site_guard",),
        )

    def test_rejects_unknown_prefix_and_bad_version(self) -> None:
        with self.assertRaises(PromotionError):
            promote_v1_constraint_ids(("other:wait_default_lookback",))
        with self.assertRaises(PromotionError):
            promote_v1_document(
                {"schema_version": 5, "required_constraint_ids": ["manual:x"]}
            )
        with self.assertRaises(PromotionError):
            promote_v1_constraint_ids("manual:new_site_guard")
