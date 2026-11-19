"""Contract tests for dayz-mcp-evidence-v1 (tools/dayz_mcp/evidence.py)."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest import mock

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import bridge_readiness, evidence


_EXPECTED_SCHEMA = "dayz-mcp-evidence-v1"

_EXPECTED_UNITS: frozenset[str] = frozenset({"m", "m/s", "deg", "s", "ms"})

_EXPECTED_FIELD_TYPES: frozenset[str] = frozenset(
    {"number", "vec3", "int", "uid", "token", "flag"}
)

_EXPECTED_FORBIDDEN_NAME_PARTS: frozenset[str] = frozenset(
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


PROBE = evidence.EvidenceKind(
    name="probe",
    fields={
        "distance_m": evidence.EvidenceField("number", "m"),
        "pos": evidence.EvidenceField("vec3", "m"),
        "seq": evidence.EvidenceField("int"),
        "target_uid": evidence.EvidenceField("uid"),
        "hit_class": evidence.EvidenceField("token"),
        "in_vehicle": evidence.EvidenceField("flag"),
        "pitch_deg": evidence.EvidenceField("number", "deg", optional=True),
        "clearance_m": evidence.EvidenceField(
            "number",
            "m",
            absent_with=frozenset({"ray_skipped"}),
        ),
    },
    gap_kinds=frozenset({"ray_skipped", "clock_not_monotonic"}),
)

_MISSING = object()


def _wire_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "uid": "76561198000000001",
        "tick_ms": 120000,
        "gap_kind": "",
        "distance_m": 12.5,
        "pos": [1.0, 2.0, 3.0],
        "seq": 7,
        "target_uid": "76561198000000002",
        "hit_class": "-",
        "in_vehicle": 0,
        "pitch_deg": 0,
        "pitch_deg_available": 0,
        "clearance_m": 0.75,
    }
    for key, value in overrides.items():
        if value is _MISSING:
            row.pop(key, None)
        else:
            row[key] = value
    return row


class EvidenceContractTest(unittest.TestCase):
    def _assert_value_error(
        self, expected_message: str, fn: object, *args: object, **kwargs: object
    ) -> None:
        with self.assertRaises(ValueError) as ctx:
            fn(*args, **kwargs)  # type: ignore[operator]
        self.assertEqual(str(ctx.exception), expected_message)

    def test_t1_envelope_shape(self) -> None:
        result = evidence.evidence_result(PROBE, [_wire_row()], truncated=False)
        expected = {
            "schema": "dayz-mcp-evidence-v1",
            "kind": "probe",
            "units": {
                "tick_ms": "ms",
                "distance_m": "m",
                "pos": "m",
                "pitch_deg": "deg",
                "clearance_m": "m",
            },
            "rows": [
                {
                    "uid": "76561198000000001",
                    "tick_ms": 120000,
                    "gap_kind": "",
                    "distance_m": 12.5,
                    "pos": [1.0, 2.0, 3.0],
                    "seq": 7,
                    "target_uid": "76561198000000002",
                    "hit_class": "-",
                    "in_vehicle": False,
                    "pitch_deg_available": False,
                    "clearance_m": 0.75,
                }
            ],
            "truncated": False,
        }
        self.assertEqual(result, expected)
        self.assertEqual(
            list(result["units"]),
            ["tick_ms", "distance_m", "pos", "pitch_deg", "clearance_m"],
        )
        self.assertNotIn("pitch_deg", result["rows"][0])
        self.assertIs(result["rows"][0]["in_vehicle"], False)
        self.assertIs(result["rows"][0]["pitch_deg_available"], False)
        self.assertIs(result["truncated"], False)

    def test_t2_unknown_wire_member_rejected(self) -> None:
        cases: tuple[tuple[object, object], ...] = (
            ("extra", 1),
            ("visible", True),
            ("distance_m_available", 1),
            ("clearance_m_available", 1),
            (7, 1),
        )
        for member, value in cases:
            with self.subTest(member=member):
                self._assert_value_error(
                    f"bad_evidence_row: rows[0].{member}",
                    evidence.normalize_rows,
                    PROBE,
                    [_wire_row() | {member: value}],
                )

    def test_t3_nothing_filtered(self) -> None:
        distances = [0, 0.001, 49.99, 1000000, -3]
        wire_rows = [_wire_row(distance_m=value) for value in distances]
        published = evidence.normalize_rows(PROBE, wire_rows)
        self.assertEqual(len(published), 5)
        self.assertEqual(published[0]["distance_m"], 0)
        self.assertEqual(published[1]["distance_m"], 0.001)
        self.assertEqual(published[2]["distance_m"], 49.99)
        self.assertEqual(published[3]["distance_m"], 1000000)
        self.assertEqual(published[4]["distance_m"], -3)

    def test_t4_absent_with_gap_kind(self) -> None:
        published = evidence.normalize_rows(
            PROBE,
            [_wire_row(gap_kind="ray_skipped", clearance_m=0)],
        )
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0]["gap_kind"], "ray_skipped")
        self.assertNotIn("clearance_m", published[0])

        self._assert_value_error(
            "bad_evidence_row: rows[0].clearance_m",
            evidence.normalize_rows,
            PROBE,
            [_wire_row(gap_kind="", clearance_m=_MISSING)],
        )
        self._assert_value_error(
            "bad_evidence_row: rows[0].clearance_m",
            evidence.normalize_rows,
            PROBE,
            [_wire_row(gap_kind="clock_not_monotonic", clearance_m=_MISSING)],
        )

    def test_t5_optional_field(self) -> None:
        available = evidence.normalize_rows(
            PROBE,
            [_wire_row(pitch_deg_available=1, pitch_deg=-12.5)],
        )
        self.assertEqual(available[0]["pitch_deg"], -12.5)
        self.assertIs(available[0]["pitch_deg_available"], True)

        unavailable = evidence.normalize_rows(
            PROBE,
            [_wire_row(pitch_deg_available=0, pitch_deg=33)],
        )
        self.assertIs(unavailable[0]["pitch_deg_available"], False)
        self.assertNotIn("pitch_deg", unavailable[0])

        self._assert_value_error(
            "bad_evidence_row: rows[0].pitch_deg_available",
            evidence.normalize_rows,
            PROBE,
            [_wire_row(pitch_deg_available=_MISSING)],
        )
        self._assert_value_error(
            "bad_evidence_row: rows[0].pitch_deg",
            evidence.normalize_rows,
            PROBE,
            [_wire_row(pitch_deg_available=1, pitch_deg=_MISSING)],
        )
        self._assert_value_error(
            "bad_evidence_row: rows[0].pitch_deg",
            evidence.normalize_rows,
            PROBE,
            [_wire_row(pitch_deg_available=1, pitch_deg="x")],
        )

    def test_t6_common_fields_validated(self) -> None:
        cases = (
            ("uid_missing", "uid", _MISSING),
            ("uid_empty", "uid", ""),
            ("uid_non_str", "uid", 123),
            ("tick_ms_missing", "tick_ms", _MISSING),
            ("tick_ms_negative", "tick_ms", -1),
            ("tick_ms_bool", "tick_ms", True),
            ("tick_ms_float", "tick_ms", 1.5),
            ("gap_kind_missing", "gap_kind", _MISSING),
            ("gap_kind_non_str", "gap_kind", 0),
            ("gap_kind_unknown", "gap_kind", "lag"),
        )
        for label, member, value in cases:
            with self.subTest(case=label):
                self._assert_value_error(
                    f"bad_evidence_row: rows[0].{member}",
                    evidence.normalize_rows,
                    PROBE,
                    [_wire_row(**{member: value})],
                )

    def test_t7_field_types_validated(self) -> None:
        cases = (
            ("distance_str", "distance_m", "12.5"),
            ("distance_bool", "distance_m", True),
            ("distance_nan", "distance_m", float("nan")),
            ("distance_inf", "distance_m", float("inf")),
            ("distance_neg_inf", "distance_m", float("-inf")),
            ("pos_len_2", "pos", [1.0, 2.0]),
            ("pos_str_item", "pos", [1.0, "2.0", 3.0]),
            ("seq_float", "seq", 1.5),
            ("seq_bool", "seq", True),
            ("seq_str", "seq", "7"),
            ("target_uid_empty", "target_uid", ""),
            ("hit_class_empty", "hit_class", ""),
            ("hit_class_non_str", "hit_class", 123),
            ("in_vehicle_2", "in_vehicle", 2),
            ("in_vehicle_str", "in_vehicle", "true"),
        )
        for label, member, value in cases:
            with self.subTest(case=label):
                self._assert_value_error(
                    f"bad_evidence_row: rows[0].{member}",
                    evidence.normalize_rows,
                    PROBE,
                    [_wire_row(**{member: value})],
                )

        large_int = 10**309
        with self.subTest(case="distance_large_int"):
            published = evidence.normalize_rows(
                PROBE, [_wire_row(distance_m=large_int)]
            )
            self.assertEqual(published[0]["distance_m"], large_int)
            self.assertIs(type(published[0]["distance_m"]), int)

        with self.subTest(case="pos_large_int"):
            published = evidence.normalize_rows(
                PROBE, [_wire_row(pos=[1, large_int, 3])]
            )
            self.assertEqual(published[0]["pos"], [1, large_int, 3])
            self.assertIs(type(published[0]["pos"][1]), int)

    def test_t8_container_validation(self) -> None:
        self._assert_value_error(
            "bad_evidence_row: rows",
            evidence.normalize_rows,
            PROBE,
            (_wire_row(),),
        )
        self._assert_value_error(
            "bad_evidence_row: rows",
            evidence.normalize_rows,
            PROBE,
            {"rows": [_wire_row()]},
        )
        self._assert_value_error(
            "bad_evidence_row: rows[1]",
            evidence.normalize_rows,
            PROBE,
            [_wire_row(), "not_a_dict"],
        )

    def test_t9_wire_row_not_mutated(self) -> None:
        w0 = _wire_row()
        snapshot = copy.deepcopy(w0)
        published = evidence.normalize_rows(PROBE, [w0])
        self.assertEqual(w0, snapshot)
        self.assertIsNot(published[0], w0)
        self.assertIsNot(published[0]["pos"], w0["pos"])

    def test_t10_truncated_and_empty_rows(self) -> None:
        truncated_true = evidence.evidence_result(PROBE, [_wire_row()], truncated=True)
        self.assertIs(truncated_true["truncated"], True)

        with self.assertRaises(TypeError):
            evidence.evidence_result(PROBE, [_wire_row()])  # type: ignore[call-arg]

        self._assert_value_error(
            "bad_evidence_result: truncated",
            evidence.evidence_result,
            PROBE,
            [_wire_row()],
            truncated=1,
        )
        self._assert_value_error(
            "bad_evidence_result: truncated",
            evidence.evidence_result,
            PROBE,
            [_wire_row()],
            truncated=None,
        )

        empty = evidence.evidence_result(PROBE, [], truncated=False)
        self.assertEqual(empty["rows"], [])
        self.assertIs(empty["truncated"], False)

    def test_t11_forbidden_vocabulary(self) -> None:
        gaps = frozenset({"ray_skipped"})

        for word in sorted(_EXPECTED_FORBIDDEN_NAME_PARTS):
            with self.subTest(field_alone=word):
                self._assert_value_error(
                    f"bad_evidence_kind: fields.{word}",
                    evidence.EvidenceKind,
                    "probe",
                    {word: evidence.EvidenceField("number", "m")},
                    gaps,
                )
            compound = f"is_{word}"
            with self.subTest(field_compound=compound):
                self._assert_value_error(
                    f"bad_evidence_kind: fields.{compound}",
                    evidence.EvidenceKind,
                    "probe",
                    {
                        "distance_m": evidence.EvidenceField("number", "m"),
                        compound: evidence.EvidenceField("flag"),
                    },
                    gaps,
                )

        for field_name in ("weight_hint", "speed_limit", "kick_reason", "is_cheater"):
            with self.subTest(named_compound=field_name):
                self._assert_value_error(
                    f"bad_evidence_kind: fields.{field_name}",
                    evidence.EvidenceKind,
                    "probe",
                    {
                        "distance_m": evidence.EvidenceField("number", "m"),
                        field_name: evidence.EvidenceField("token"),
                    },
                    gaps,
                )

        for kind_name in ("cheat", "speed_limit", "verdict_probe", "detection"):
            with self.subTest(kind_name=kind_name):
                self._assert_value_error(
                    "bad_evidence_kind: name",
                    evidence.EvidenceKind,
                    kind_name,
                    {"distance_m": evidence.EvidenceField("number", "m")},
                    gaps,
                )

        for gap_name in ("ban", "kick_reason", "threshold_hit", "suspect"):
            with self.subTest(gap_name=gap_name):
                self._assert_value_error(
                    f"bad_evidence_kind: gap_kinds.{gap_name}",
                    evidence.EvidenceKind,
                    "probe",
                    {"distance_m": evidence.EvidenceField("number", "m")},
                    frozenset({gap_name}),
                )

        passing = evidence.EvidenceKind(
            name="probe",
            fields={
                "band_m": evidence.EvidenceField("number", "m"),
                "urban_m": evidence.EvidenceField("number", "m"),
            },
            gap_kinds=frozenset({"ray_budget_exceeded"}),
        )
        self.assertEqual(passing.name, "probe")

    def test_t12_magnitude_field_required(self) -> None:
        self._assert_value_error(
            "bad_evidence_kind: fields",
            evidence.EvidenceKind,
            "probe",
            {
                "in_vehicle": evidence.EvidenceField("flag"),
                "hit_class": evidence.EvidenceField("token"),
                "seq": evidence.EvidenceField("int"),
                "target_uid": evidence.EvidenceField("uid"),
            },
            frozenset({"ray_skipped"}),
        )

        with_number = evidence.EvidenceKind(
            name="probe",
            fields={
                "in_vehicle": evidence.EvidenceField("flag"),
                "hit_class": evidence.EvidenceField("token"),
                "seq": evidence.EvidenceField("int"),
                "target_uid": evidence.EvidenceField("uid"),
                "distance_m": evidence.EvidenceField("number", "m"),
            },
            gap_kinds=frozenset({"ray_skipped"}),
        )
        self.assertIn("distance_m", with_number.fields)

    def test_t13_units_validation(self) -> None:
        gaps = frozenset({"ray_skipped"})
        cases = (
            ("number_empty_unit", "number", ""),
            ("number_bad_unit", "number", "km/h"),
            ("vec3_empty_unit", "vec3", ""),
            ("vec3_bad_unit", "vec3", "km/h"),
            ("int_with_unit", "int", "ms"),
            ("uid_with_unit", "uid", "m"),
            ("token_with_unit", "token", "s"),
            ("flag_with_unit", "flag", "deg"),
        )
        for label, field_type, unit in cases:
            with self.subTest(case=label):
                self._assert_value_error(
                    "bad_evidence_kind: fields.val",
                    evidence.EvidenceKind,
                    "probe",
                    {
                        "distance_m": evidence.EvidenceField("number", "m"),
                        "val": evidence.EvidenceField(field_type, unit),
                    },
                    gaps,
                )

    def test_t14_names_and_field_type(self) -> None:
        gaps = frozenset({"ray_skipped"})

        for reserved in ("uid", "tick_ms", "gap_kind", "x_available"):
            with self.subTest(reserved_field=reserved):
                self._assert_value_error(
                    f"bad_evidence_kind: fields.{reserved}",
                    evidence.EvidenceKind,
                    "probe",
                    {
                        "distance_m": evidence.EvidenceField("number", "m"),
                        reserved: evidence.EvidenceField("int"),
                    },
                    gaps,
                )

        for bad_name in ("Distance", "1x", "a-b", ""):
            with self.subTest(bad_kind_name=bad_name):
                self._assert_value_error(
                    "bad_evidence_kind: name",
                    evidence.EvidenceKind,
                    bad_name,
                    {"distance_m": evidence.EvidenceField("number", "m")},
                    gaps,
                )
            with self.subTest(bad_field_name=bad_name):
                self._assert_value_error(
                    f"bad_evidence_kind: fields.{bad_name}",
                    evidence.EvidenceKind,
                    "probe",
                    {
                        "distance_m": evidence.EvidenceField("number", "m"),
                        bad_name: evidence.EvidenceField("int"),
                    },
                    gaps,
                )

        with self.subTest(bad_type="bool"):
            self._assert_value_error(
                "bad_evidence_kind: fields.bad_flag",
                evidence.EvidenceKind,
                "probe",
                {
                    "distance_m": evidence.EvidenceField("number", "m"),
                    "bad_flag": evidence.EvidenceField("bool"),
                },
                gaps,
            )

        with self.subTest(non_str_field_key=7):
            self._assert_value_error(
                "bad_evidence_kind: fields.7",
                evidence.EvidenceKind,
                "probe",
                {7: evidence.EvidenceField("number", "m")},
                gaps,
            )

        with self.subTest(non_str_gap_kind=7):
            self._assert_value_error(
                "bad_evidence_kind: gap_kinds.7",
                evidence.EvidenceKind,
                "probe",
                {"distance_m": evidence.EvidenceField("number", "m")},
                frozenset({"ray_skipped", 7}),
            )

    def test_t15_gap_kinds_and_absent_with(self) -> None:
        with self.subTest(case="empty_gap_kinds"):
            self._assert_value_error(
                "bad_evidence_kind: gap_kinds",
                evidence.EvidenceKind,
                "probe",
                {"distance_m": evidence.EvidenceField("number", "m")},
                frozenset(),
            )

        with self.subTest(case="absent_with_unknown_gap"):
            self._assert_value_error(
                "bad_evidence_kind: fields.clearance_m",
                evidence.EvidenceKind,
                "probe",
                {
                    "clearance_m": evidence.EvidenceField(
                        "number", "m", absent_with=frozenset({"unknown_gap"})
                    ),
                },
                frozenset({"ray_skipped"}),
            )

        with self.subTest(case="absent_with_empty_string"):
            self._assert_value_error(
                "bad_evidence_kind: fields.clearance_m",
                evidence.EvidenceKind,
                "probe",
                {
                    "clearance_m": evidence.EvidenceField(
                        "number", "m", absent_with=frozenset({""})
                    ),
                },
                frozenset({"ray_skipped"}),
            )

        with self.subTest(case="optional_and_absent_with"):
            self._assert_value_error(
                "bad_evidence_kind: fields.clearance_m",
                evidence.EvidenceKind,
                "probe",
                {
                    "clearance_m": evidence.EvidenceField(
                        "number",
                        "m",
                        optional=True,
                        absent_with=frozenset({"ray_skipped"}),
                    ),
                },
                frozenset({"ray_skipped"}),
            )

    def test_t16_ach_gate_detects_contract_change(self) -> None:
        original_hash = bridge_readiness.EXPECTED_SERVER_ARG_CONTRACT_HASH
        self.assertEqual(bridge_readiness.server_arg_contract_hash(), original_hash)
        census = sorted(bridge_readiness._BRIDGE_COMMAND_TOOLS["server"])
        registered = frozenset(
            tool
            for tool in bridge_readiness._BRIDGE_COMMAND_TOOLS["server"].values()
            if tool
        )
        cases = (
            (
                "new_command",
                {"evidence_probe": ("max_rows", "uid")},
            ),
            (
                "new_players_visibility_key",
                {
                    "players_visibility": tuple(
                        sorted(
                            bridge_readiness.SERVER_ARG_CONTRACT["players_visibility"]
                            + ("include_gaps",)
                        )
                    )
                },
            ),
        )
        for label, contract_patch in cases:
            with self.subTest(case=label):
                with mock.patch.dict(
                    bridge_readiness.SERVER_ARG_CONTRACT,
                    contract_patch,
                ):
                    new_hash = bridge_readiness.server_arg_contract_hash()
                    self.assertNotEqual(new_hash, original_hash)
                    with mock.patch.object(
                        bridge_readiness,
                        "EXPECTED_SERVER_ARG_CONTRACT_HASH",
                        new_hash,
                    ):
                        result = bridge_readiness._compare_bridge_capabilities(
                            "server",
                            {
                                "state": "announced",
                                "reason": "ok",
                                "announced_commands": census,
                                "announced_arg_contract_hash": original_hash,
                            },
                            registered,
                        )
                self.assertEqual(result["state"], "mismatch")
                self.assertEqual(result["reason"], "arg_contract_mismatch")
                self.assertEqual(result["expected_arg_contract_hash"], new_hash)
                self.assertEqual(result["announced_arg_contract_hash"], original_hash)
        self.assertEqual(bridge_readiness.server_arg_contract_hash(), original_hash)

    def test_t17_doc_names_contract_constants(self) -> None:
        with self.subTest(constant="EVIDENCE_SCHEMA"):
            self.assertEqual(evidence.EVIDENCE_SCHEMA, _EXPECTED_SCHEMA)
        with self.subTest(constant="UNITS"):
            self.assertEqual(evidence.UNITS, _EXPECTED_UNITS)
        with self.subTest(constant="FIELD_TYPES"):
            self.assertEqual(evidence.FIELD_TYPES, _EXPECTED_FIELD_TYPES)
        with self.subTest(constant="FORBIDDEN_NAME_PARTS"):
            self.assertEqual(
                evidence.FORBIDDEN_NAME_PARTS, _EXPECTED_FORBIDDEN_NAME_PARTS
            )

        doc_path = Path(__file__).resolve().parents[2] / "docs" / "evidence-contract.md"
        self.assertTrue(doc_path.is_file(), f"missing {doc_path}")
        text = doc_path.read_text(encoding="utf-8")
        self.assertIn(_EXPECTED_SCHEMA, text)
        for unit in sorted(_EXPECTED_UNITS):
            with self.subTest(unit=unit):
                self.assertIn(f"`{unit}`", text)
        for field_type in sorted(_EXPECTED_FIELD_TYPES):
            with self.subTest(field_type=field_type):
                self.assertIn(f"`{field_type}`", text)
        for word in sorted(_EXPECTED_FORBIDDEN_NAME_PARTS):
            with self.subTest(forbidden=word):
                self.assertIn(f"`{word}`", text)


if __name__ == "__main__":
    unittest.main()
