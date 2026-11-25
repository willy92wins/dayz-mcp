"""A world target the client's search does not find says what the search saw
(inbox b951).

In run c8c2ec4c (23/11, DayZ Exp 1.30.164393), action_use ActionLFSKMount on
LFSkate_BoardGrindEast answered target_not_found twice for a board spawned
0.8 m from the teleported player, and nothing in the reply said where the
client had searched or what it had found. With target world the search runs
on the client: DispatchActionUse calls FindNearestObjectNearClient, which asks
GetObjectsAtPosition3D around the local player's position (or pos) with the
radius (5 by default) and keeps the nearest object whose GetType() or
ClassName() is the classname. Before it answers target_not_found,
FillTargetSearch now reads that same search into result.target_search
(MCPActionSearch): the centre, the local player's position on the client, the
radius, how many objects the search returned without the player, and up to 8
of them nearest first (MCPSearchHit: GetType() and the distance to the centre),
with truncated when it returned more. bridge_errors publishes it as observed=
for the five action verbs and that code only. The success path, the radius and
the class match do not change.

The Enforce side is read from the source (SearchReportSourceTest); the error
text is bridge_errors', the prune result_prune's and the descriptions the
server's.
"""
from __future__ import annotations

import math
import re
import sys
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import result_prune, server
from dayz_mcp.bridge_errors import _bridge_error
from tests.deploy_hologram_helpers import BRIDGE_PATH, MESSAGES_PATH
from tests.enforce_subset_helpers import class_members, clean, method_body

DISPATCH = "protected bool DispatchActionUse(MCPCommand command, MCPResult result)"
FILL = "protected void FillTargetSearch(MCPResult result, vector center, float radius, PlayerBase player)"
ACTION_COMMANDS = (
    "action_use",
    "action_use_door",
    "action_use_target",
    "action_use_component",
    "action_hold",
)
OTHER_COMMANDS = ("action_cursor", "vehicle_enter", "object_resolve", "world_spawn")
ABSENT = object()

CENTER = [4813.2998046875, 8.896016120910645, 2464.60009765625]
PLAYER = [4854.41162109375, 8.949518203735352, 2464.623291015625]
HITS = [
    {"type": "LFSkate_Board", "distance": 0.8},
    {"type": "StaticObj_Misc_ConcreteBlock1", "distance": 2.25},
    {"type": "Land_Pond", "distance": 4.5},
]
REPORT = {
    "center": CENTER,
    "player_pos": PLAYER,
    "radius": 5.0,
    "scanned": 3,
    "truncated": False,
    "nearest": HITS,
}
PAIR_CENTER = "search_center=4813.2998046875,8.896016120910645,2464.60009765625"
PAIR_PLAYER = "player_pos=4854.41162109375,8.949518203735352,2464.623291015625"
PAIR_RADIUS = "search_radius=5.0"
PAIR_SCANNED = "scanned=3"
PAIR_NEAREST = "nearest=LFSkate_Board@0.8,StaticObj_Misc_ConcreteBlock1@2.25,Land_Pond@4.5"
PAIRS = [PAIR_CENTER, PAIR_PLAYER, PAIR_RADIUS, PAIR_SCANNED, PAIR_NEAREST]
HOLOGRAM = {
    "checks": ["is_floating", "is_base_viable"],
    "pos": [7500.0, 12.5, 2000.25],
    "min_m": 2.5,
    "max_m": 5.0,
}
HOLOGRAM_TEXT = (
    "hologram_colliding; observed=hologram_checks=is_floating,is_base_viable "
    "hologram_pos=7500.0,12.5,2000.25 hologram_min_m=2.5 hologram_max_m=5.0"
)


def observed(pairs: list[str]) -> str:
    return "target_not_found; observed=" + " ".join(pairs)


def error_text(command: str, error: str, report: object = ABSENT, **extra: object) -> str:
    result: dict = {"ok": False, "error": error}
    if report is not ABSENT:
        result["target_search"] = report
    result.update(extra)
    return str(_bridge_error(result, command))


def with_field(key: str, value: object) -> dict:
    report = dict(REPORT)
    report[key] = value
    return report


class SearchReportWireTest(unittest.TestCase):
    def test_target_not_found_carries_what_the_search_saw(self) -> None:
        # P1: the five verbs, the fields in this order, nearest as received.
        for command in ACTION_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(error_text(command, "target_not_found", REPORT), observed(PAIRS))

    def test_truncated_adds_the_mark_last(self) -> None:
        for command in ACTION_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(
                    error_text(command, "target_not_found", with_field("truncated", True)),
                    observed(PAIRS + ["nearest_truncated=1"]),
                )

    def test_nearest_keeps_the_order_it_came_in(self) -> None:
        report = with_field(
            "nearest",
            [{"type": "Far_Thing", "distance": 4.0}, {"type": "Near_Thing", "distance": 1.0}],
        )
        self.assertEqual(
            error_text("action_use", "target_not_found", report),
            observed(PAIRS[:4] + ["nearest=Far_Thing@4.0,Near_Thing@1.0"]),
        )

    def test_each_bad_position_drops_alone(self) -> None:
        # N1: a position crosses only as three finite numbers.
        bad_positions = (
            [1.0, 2.0],
            [1.0, 2.0, 3.0, 4.0],
            [1.0, "2", 3.0],
            [True, 2.0, 3.0],
            [1.0, math.nan, 2.0],
            [1.0, math.inf, 2.0],
            "1,2,3",
            None,
            {"x": 1.0},
        )
        for key, pair in (("center", PAIR_CENTER), ("player_pos", PAIR_PLAYER)):
            for value in bad_positions:
                with self.subTest(key=key, value=value):
                    self.assertEqual(
                        error_text("action_use_target", "target_not_found", with_field(key, value)),
                        observed([p for p in PAIRS if p != pair]),
                    )

    def test_each_bad_radius_or_count_drops_alone(self) -> None:
        cases = (
            ("radius", PAIR_RADIUS, (math.nan, math.inf, -1.0, True, "5", None)),
            ("scanned", PAIR_SCANNED, (-1, 2.5, 3.0, True, "3", None)),
        )
        for key, pair, values in cases:
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assertEqual(
                        error_text("action_hold", "target_not_found", with_field(key, value)),
                        observed([p for p in PAIRS if p != pair]),
                    )
        # Zero is a real reading for both.
        zero = dict(REPORT, radius=0, scanned=0)
        self.assertEqual(
            error_text("action_use", "target_not_found", zero),
            observed([PAIR_CENTER, PAIR_PLAYER, "search_radius=0.0", "scanned=0", PAIR_NEAREST]),
        )

    def test_an_entry_crosses_only_with_a_known_type_and_distance(self) -> None:
        good = {"type": "LFSkate_Board", "distance": 0.8}
        bad_entries = (
            {"type": "rm -rf /", "distance": 1.0},
            {"type": "", "distance": 1.0},
            {"type": "a" * 65, "distance": 1.0},
            {"type": "Tipo-x", "distance": 1.0},
            {"type": "Tipo@x", "distance": 1.0},
            {"type": "Tipo,x", "distance": 1.0},
            {"type": 5, "distance": 1.0},
            {"type": None, "distance": 1.0},
            {"type": "Tipo", "distance": math.nan},
            {"type": "Tipo", "distance": math.inf},
            {"type": "Tipo", "distance": -0.5},
            {"type": "Tipo", "distance": True},
            {"type": "Tipo", "distance": "1"},
            {"type": "Tipo"},
            {"distance": 1.0},
            "LFSkate_Board@0.8",
            ["LFSkate_Board", 0.8],
            None,
        )
        for entry in bad_entries:
            with self.subTest(entry=entry):
                self.assertEqual(
                    error_text("action_use", "target_not_found", with_field("nearest", [entry, good])),
                    observed(PAIRS[:4] + ["nearest=LFSkate_Board@0.8"]),
                )
        longest = {"type": "a" * 64, "distance": 0.0}
        self.assertEqual(
            error_text("action_use", "target_not_found", with_field("nearest", [longest])),
            observed(PAIRS[:4] + ["nearest=" + "a" * 64 + "@0.0"]),
        )

    def test_a_nearest_that_is_not_a_list_drops_alone(self) -> None:
        for value in ({"type": "LFSkate_Board", "distance": 0.8}, "LFSkate_Board@0.8", None, 3):
            with self.subTest(value=value):
                self.assertEqual(
                    error_text("action_use", "target_not_found", with_field("nearest", value)),
                    observed(PAIRS[:4]),
                )
        self.assertEqual(
            error_text("action_use", "target_not_found", with_field("nearest", [])),
            observed(PAIRS[:4]),
        )

    def test_only_a_true_truncated_adds_the_mark(self) -> None:
        for value in (1, "true", "1", None, 0):
            with self.subTest(value=value):
                self.assertEqual(
                    error_text("action_use", "target_not_found", with_field("truncated", value)),
                    observed(PAIRS),
                )

    def test_more_than_eight_entries_cross_as_the_first_eight_and_the_mark(self) -> None:
        twelve = [{"type": f"Thing_{n}", "distance": float(n)} for n in range(12)]
        text = error_text("action_use", "target_not_found", with_field("nearest", twelve))
        self.assertEqual(
            text,
            observed(PAIRS[:4] + ["nearest=" + ",".join(f"Thing_{n}@{float(n)!r}" for n in range(8)), "nearest_truncated=1"]),
        )
        eight = twelve[:8]
        self.assertEqual(
            error_text("action_use", "target_not_found", with_field("nearest", eight)),
            observed(PAIRS[:4] + ["nearest=" + ",".join(f"Thing_{n}@{float(n)!r}" for n in range(8))]),
        )

    def test_nothing_that_crosses_leaves_todays_text(self) -> None:
        # N2: the bare code, byte for byte; the filled report is the control.
        all_bad = {
            "center": [1.0, 2.0],
            "player_pos": "x",
            "radius": -1.0,
            "scanned": -3,
            "truncated": "yes",
            "nearest": [{"type": "rm -rf /", "distance": 1.0}],
        }
        for report in (ABSENT, {}, None, [], "x", 5, all_bad, {"nearest": []}):
            for command in ACTION_COMMANDS:
                with self.subTest(report=report, command=command):
                    self.assertEqual(error_text(command, "target_not_found", report), "target_not_found")
        self.assertEqual(error_text("action_use", "target_not_found", REPORT), observed(PAIRS))

    def test_other_codes_and_verbs_carry_nothing_of_the_search(self) -> None:
        # N3: only the five verbs' target_not_found reads target_search.
        codes = (
            "condition_failed",
            "no_hologram",
            "setup_failed",
            "held_item_mismatch",
            "bad_args",
            "not_a_building",
            "door_out_of_range",
            "door_component_not_found",
        )
        for command in ACTION_COMMANDS:
            for code in codes:
                with self.subTest(command=command, code=code):
                    self.assertEqual(error_text(command, code, REPORT), code)
            with self.subTest(command=command, code="hologram_colliding"):
                self.assertEqual(
                    error_text(command, "hologram_colliding", REPORT, hologram=HOLOGRAM), HOLOGRAM_TEXT
                )
        for command in OTHER_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(error_text(command, "target_not_found", REPORT), "target_not_found")
        self.assertEqual(error_text("action_use_door", "target_not_found", REPORT), observed(PAIRS))


class SearchReportPruneTest(unittest.TestCase):
    def test_the_prune_keeps_a_filled_report_and_drops_an_empty_one(self) -> None:
        # P2
        for command in ACTION_COMMANDS:
            with self.subTest(command=command):
                kept = result_prune.prune_unfilled_fields(command, {"ok": 1, "target_search": dict(REPORT)})
                self.assertEqual(kept["target_search"], REPORT)
                for empty in ({}, None):
                    self.assertEqual(
                        result_prune.prune_unfilled_fields(command, {"ok": 1, "target_search": empty}),
                        {"ok": 1},
                    )
        for command in ("ui_click", "world_spawn"):
            with self.subTest(command=command):
                self.assertEqual(
                    result_prune.prune_unfilled_fields(command, {"ok": 1, "target_search": {}}), {"ok": 1}
                )

    def test_the_prunable_fields_are_the_result_refs_with_target_search_after_hologram(self) -> None:
        refs = [
            name
            for kind, name in class_members(MESSAGES_PATH.read_text(encoding="utf-8"), "MCPResult")
            if kind.startswith("ref ")
        ]
        self.assertEqual(list(result_prune.PRUNABLE_FIELDS), refs)
        fields = list(result_prune.PRUNABLE_FIELDS)
        self.assertEqual(fields[fields.index("hologram") + 1], "target_search")


class SearchReportDescriptionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app, _runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    def _description(self, name: str) -> str:
        return self.app._tool_manager.get_tool(name).description

    def test_action_use_names_the_code_and_the_fields(self) -> None:
        # P3
        description = self._description("action_use")
        for part in (
            "A world target the client's search does not find answers target_not_found",
            "its observed= carries search_center= (the point searched around: pos when given, "
            "else the local player's position)",
            "player_pos= (the local player's position on the client)",
            "search_radius=",
            "scanned= (how many objects the search returned, the player not counted)",
            "nearest= (up to 8 of them as type@distance, nearest to search_center first, in metres)",
            "nearest_truncated=1 when it returned more",
        ):
            with self.subTest(part=part):
                self.assertIn(part, description)

    def test_action_hold_names_the_code(self) -> None:
        self.assertIn(
            "A world target the client's search does not find answers target_not_found "
            "with the same observed= as action_use",
            self._description("action_hold"),
        )


class SearchReportSourceTest(unittest.TestCase):
    # S1
    def setUp(self) -> None:
        self.code = clean(BRIDGE_PATH.read_text(encoding="utf-8"))
        self.dispatch = method_body(self.code, DISPATCH)

    def _fill(self) -> str:
        return method_body(self.code, FILL)

    def test_the_messages_declare_the_report(self) -> None:
        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        self.assertEqual(
            class_members(messages, "MCPActionSearch"),
            [
                ("ref array<float>", "center"),
                ("ref array<float>", "player_pos"),
                ("float", "radius"),
                ("int", "scanned"),
                ("bool", "truncated"),
                ("ref array<ref MCPSearchHit>", "nearest"),
            ],
        )
        self.assertEqual(class_members(messages, "MCPSearchHit"), [("string", "type"), ("float", "distance")])
        members = class_members(messages, "MCPResult")
        self.assertEqual(
            members.index(("ref MCPActionSearch", "target_search")),
            members.index(("ref MCPHologram", "hologram")) + 1,
        )

    def test_the_fill_runs_only_where_nothing_was_found(self) -> None:
        block = re.search(r"if\s*\(\s*!\s*targetObj\s*\)\s*\{(?P<inner>[^{}]*)\}", self.dispatch)
        self.assertIsNotNone(block)
        self.assertEqual(
            re.sub(r"\s+", "", block.group("inner")),
            "FillTargetSearch(result,searchPos,searchRadius,player);"
            'result.ok=false;result.error="target_not_found";returntrue;',
        )
        self.assertEqual(len(re.findall(r"\bFillTargetSearch\s*\(", self.code)), 2)
        self.assertNotIn("target_search", self.dispatch)
        fill = self._fill()
        self.assertGreaterEqual(fill.count("target_search"), 1)
        self.assertEqual(self.code.count("target_search"), fill.count("target_search"))
        self.assertRegex(fill, r"\bresult\s*\.\s*target_search\s*=")

    def test_the_fill_reads_the_same_search_and_leaves_the_player_out(self) -> None:
        fill = self._fill()
        self.assertIn("m_ReadyObjects", fill)
        self.assertNotIn("GetObjectsAtPosition3D", fill)
        self.assertRegex(fill, r"(!=|==)\s*player\b")
        self.assertRegex(fill, r"\bVectorToArray\s*\(\s*center\s*,")
        self.assertRegex(fill, r"\bVectorToArray\s*\(\s*player\s*\.\s*GetPosition\s*\(\s*\)\s*,")
        self.assertRegex(fill, r"\.\s*radius\s*=\s*radius\s*;")
        self.assertRegex(fill, r"\bvector\s*\.\s*Distance\s*\(\s*center\s*,")
        self.assertRegex(fill, r"\.\s*GetType\s*\(\s*\)")
        self.assertRegex(fill, r"\.\s*scanned\s*(\+\+|\+=\s*1\b|=\s*[\w.]+\s*\.\s*scanned\s*\+\s*1\b)")

    def test_the_list_stops_at_eight_and_says_so(self) -> None:
        self.assertRegex(self.code, r"\bconst\s+int\s+ACTION_SEARCH_NEAREST_MAX\s*=\s*8\s*;")
        fill = self._fill()
        self.assertIn("ACTION_SEARCH_NEAREST_MAX", fill)
        self.assertRegex(fill, r"\.\s*truncated\s*=\s*true\s*;")


if __name__ == "__main__":
    unittest.main()
