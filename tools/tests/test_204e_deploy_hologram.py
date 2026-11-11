"""A deploy refused for its hologram says why, and action_use says whether the
player is placing (inbox 204e, 9/11 in-game round).

On the runway's concrete, the FenceKit's ActionDeployObject answered
condition_failed and nothing told a floating hologram from any other refusal.
The strong hypothesis is the camera: with target hands the cursor is
vector.Zero, so the camera ray places the hologram, and a look at 1.5 m is
nearer than half the kit's box diagonal. DispatchActionUse now calls
ApplyDeployRefusal when action.Can is false. For an ActionDeployObject it
answers no_hologram when the local player is not placing and
hologram_colliding when the local hologram collides, with the checks that
refuse it, the projection's position and the band of distances from the
player where it does not float. Any other refusal stays condition_failed.
After the start, placing is PlayerBase.IsPlacingLocal, so the toggle says
which way it went.

ApplyDeployRefusal and VectorToArray run as Python (enforce_subset_helpers) on
fakes of the player and its Hologram (deploy_hologram_helpers), with MCPResult
and MCPHologram built from their declarations; DeployRefusalDispatchTest in
test_204e_instant_action runs the tail of DispatchActionUse with the addon's
ApplyDeployRefusal; the error text is bridge_errors'.
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

from dayz_mcp import bridge_errors, result_prune, server
from dayz_mcp.bridge_errors import _bridge_error
from tests.deploy_hologram_helpers import (
    BRIDGE_PATH,
    CHECKS,
    MESSAGES_PATH,
    REFUSAL,
    Deploy,
    Hologram,
    Projection,
    checks_of,
    model,
)
from tests.enforce_subset_helpers import (
    EnforceString,
    class_members,
    clean,
    method_body,
)

DISPATCH = "protected bool DispatchActionUse(MCPCommand command, MCPResult result)"
ACTION_COMMANDS = (
    "action_use",
    "action_use_door",
    "action_use_target",
    "action_use_component",
    "action_hold",
)


class Other:
    """Any other action."""


class Player:
    def __init__(self, hologram: Hologram | None, placing: bool | None = None) -> None:
        self.hologram = hologram
        self.placing = hologram is not None if placing is None else placing

    def IsPlacingLocal(self) -> bool:
        return self.placing

    def GetHologramLocal(self) -> Hologram | None:
        return self.hologram


def refuse(namespace: dict, action: object, player: Player):
    result = namespace["MCPResult"]()
    result.ok = False
    result.error = EnforceString.of("condition_failed")
    namespace["ApplyDeployRefusal"](action, player, result)
    return result


class DeployRefusalModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.namespace = model()

    def test_another_action_keeps_condition_failed_and_reads_no_hologram(self) -> None:
        hologram = Hologram()
        result = refuse(self.namespace, Other(), Player(hologram))
        self.assertEqual(result.error.text, "condition_failed")
        self.assertIsNone(result.hologram)
        self.assertEqual(hologram.asked, [])

    def test_a_deploy_with_no_hologram_is_no_hologram(self) -> None:
        cases = {
            "not placing": Player(None),
            "placing with no hologram": Player(None, placing=True),
            "a hologram with no projection": Player(Hologram(projection=None)),
            "not placing with a hologram": Player(Hologram(), placing=False),
        }
        for name, player in cases.items():
            with self.subTest(case=name):
                result = refuse(self.namespace, Deploy(), player)
                self.assertEqual(result.error.text, "no_hologram")
                self.assertIsNone(result.hologram)
                if player.hologram is not None:
                    self.assertNotIn("IsColliding", player.hologram.asked)

    def test_a_deploy_whose_hologram_does_not_collide_keeps_condition_failed(self) -> None:
        hologram = Hologram(colliding=False, refusing=tuple(name for name, _m, _r in CHECKS))
        result = refuse(self.namespace, Deploy(), Player(hologram))
        self.assertEqual(result.error.text, "condition_failed")
        self.assertIsNone(result.hologram)
        self.assertEqual(hologram.asked, ["GetProjectionEntity", "IsColliding"])

    def test_each_refusing_check_names_itself(self) -> None:
        for name, method, _refuses in CHECKS:
            with self.subTest(check=name):
                hologram = Hologram(refusing=(name,))
                result = refuse(self.namespace, Deploy(), Player(hologram))
                self.assertEqual(result.error.text, "hologram_colliding")
                self.assertEqual(checks_of(result), [name])
                self.assertIn(method, hologram.asked)

    def test_every_check_is_asked_and_listed_in_order(self) -> None:
        names = [name for name, _m, _r in CHECKS]
        result = refuse(self.namespace, Deploy(), Player(Hologram(refusing=tuple(names))))
        self.assertEqual(checks_of(result), names)
        # A hologram that collides by a check not among the five lists none.
        none = refuse(self.namespace, Deploy(), Player(Hologram()))
        self.assertEqual(none.error.text, "hologram_colliding")
        self.assertEqual(checks_of(none), [])

    def test_the_position_is_the_projection_s(self) -> None:
        hologram = Hologram(projection=Projection((1.5, -2.25, 3.0)))
        result = refuse(self.namespace, Deploy(), Player(hologram))
        self.assertEqual(list(result.hologram.pos), [1.5, -2.25, 3.0])

    def test_the_band_is_the_radius_and_twice_it(self) -> None:
        # Hologram.GetProjectionEntityPosition: radius and twice it, 1 m and
        # 2 m under a 1 m radius, 6 m at most.
        cases = {0.4: (1.0, 2.0), 1.0: (1.0, 2.0), 2.5: (2.5, 5.0), 3.5: (3.5, 6.0), 7.0: (7.0, 6.0)}
        for radius, band in cases.items():
            with self.subTest(radius=radius):
                result = refuse(self.namespace, Deploy(), Player(Hologram(radius=radius)))
                self.assertEqual((result.hologram.min_m, result.hologram.max_m), band)


class DeployRefusalSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.code = clean(BRIDGE_PATH.read_text(encoding="utf-8"))
        self.body = method_body(self.code, DISPATCH)

    def test_the_refusal_runs_only_where_can_was_false(self) -> None:
        self.assertEqual(len(re.findall(r"\bApplyDeployRefusal\s*\(", self.code)), 2)
        block = re.search(
            r"if\s*\(\s*!\s*action\s*\.\s*Can\s*\(\s*player\s*,\s*actionTarget\s*,\s*heldItem\s*\)\s*\)"
            r"\s*\{(?P<inner>[^{}]*)\}",
            self.body,
        )
        self.assertIsNotNone(block)
        inner = re.sub(r"\s+", "", block.group("inner"))
        self.assertEqual(
            inner,
            'result.ok=false;result.error="condition_failed";'
            "ApplyDeployRefusal(action,player,result);returntrue;",
        )

    def test_placing_is_read_once_after_the_watch_ends(self) -> None:
        bare = re.sub(r"\s+", "", self.body)
        self.assertEqual(bare.count("result.placing="), 1)
        self.assertIn(
            "intstarts=MCPActionUseWatch.End();result.placing=player.IsPlacingLocal();", bare
        )

    def test_the_check_names_are_the_error_text_s(self) -> None:
        body = method_body(self.code, REFUSAL)
        names = tuple(re.findall(r'report\.checks\.Insert\("(\w+)"\)', body))
        self.assertEqual(names, tuple(name for name, _m, _r in CHECKS))
        self.assertEqual(names, bridge_errors._HOLOGRAM_CHECKS)

    def test_the_messages_declare_the_report(self) -> None:
        messages = MESSAGES_PATH.read_text(encoding="utf-8")
        self.assertEqual(
            class_members(messages, "MCPHologram"),
            [
                ("ref array<string>", "checks"),
                ("ref array<float>", "pos"),
                ("float", "min_m"),
                ("float", "max_m"),
            ],
        )
        members = class_members(messages, "MCPResult")
        self.assertIn(("bool", "placing"), members)
        self.assertIn(("ref MCPHologram", "hologram"), members)


class DeployRefusalWireTest(unittest.TestCase):
    REPORT = {
        "checks": ["is_floating", "is_base_viable"],
        "pos": [7500.0, 12.5, 2000.25],
        "min_m": 2.5,
        "max_m": 5.0,
    }

    def _error(self, command: str, error: str, report: object) -> str:
        return str(_bridge_error({"ok": False, "error": error, "hologram": report}, command))

    def test_hologram_colliding_carries_what_the_client_read(self) -> None:
        for command in ACTION_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(
                    self._error(command, "hologram_colliding", self.REPORT),
                    "hologram_colliding; observed=hologram_checks=is_floating,is_base_viable "
                    "hologram_pos=7500.0,12.5,2000.25 hologram_min_m=2.5 hologram_max_m=5.0",
                )

    def test_only_known_names_and_finite_numbers_cross(self) -> None:
        report = {
            "checks": ["is_floating", "rm -rf /", 3, "IS_FLOATING"],
            "pos": [1.0, math.nan, 2.0],
            "min_m": True,
            "max_m": -1.0,
        }
        self.assertEqual(
            self._error("action_hold", "hologram_colliding", report),
            "hologram_colliding; observed=hologram_checks=is_floating",
        )
        for pos in ([1.0, 2.0], [1.0, 2.0, 3.0, 4.0], [1.0, "2", 3.0], "1,2,3", [1.0, math.inf, 2.0]):
            with self.subTest(pos=pos):
                text = self._error("action_use_target", "hologram_colliding", {"pos": pos, "min_m": 1})
                self.assertEqual(text, "hologram_colliding; observed=hologram_min_m=1.0")

    def test_nothing_readable_leaves_the_bare_code(self) -> None:
        for report in ({}, None, [], {"checks": []}, "x"):
            with self.subTest(report=report):
                self.assertEqual(self._error("action_use_target", "hologram_colliding", report), "hologram_colliding")

    def test_other_codes_and_commands_carry_no_hologram(self) -> None:
        for error in ("no_hologram", "condition_failed", "setup_failed"):
            with self.subTest(error=error):
                self.assertEqual(self._error("action_use_target", error, self.REPORT), error)
        for command in ("world_spawn", "actor_emote", "player_move"):
            with self.subTest(command=command):
                self.assertEqual(self._error(command, "hologram_colliding", self.REPORT), "hologram_colliding")

    def test_the_prune_keeps_a_filled_report_and_placing_for_the_action_verbs(self) -> None:
        for command in ACTION_COMMANDS:
            with self.subTest(command=command):
                kept = result_prune.prune_unfilled_fields(
                    command, {"ok": 1, "placing": False, "hologram": {}}
                )
                self.assertEqual(kept, {"ok": 1, "placing": False})
                filled = result_prune.prune_unfilled_fields(
                    command, {"ok": 1, "hologram": dict(self.REPORT)}
                )
                self.assertEqual(filled["hologram"], self.REPORT)
        dropped = result_prune.prune_unfilled_fields("ui_click", {"ok": 1, "placing": False, "hologram": {}})
        self.assertEqual(dropped, {"ok": 1})


class DeployRefusalDescriptionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app, _runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    def _description(self, name: str) -> str:
        return self.app._tool_manager.get_tool(name).description

    def test_action_use_names_the_codes_and_the_fields(self) -> None:
        description = self._description("action_use")
        for part in (
            "answers no_hologram instead when the local player is not placing",
            "hologram_colliding when the local hologram collides",
            "its observed= carries hologram_checks= (those of is_floating, is_colliding_bbox, "
            "is_clipping_roof, is_base_viable and height_placement_check that refuse it",
            "hologram_pos= (the projection's position), hologram_min_m= and hologram_max_m=",
            "farther than hologram_min_m and nearer than hologram_max_m from the player",
            "placing is whether the local player is placing once the call ran",
        ):
            with self.subTest(part=part):
                self.assertIn(part, description)

    def test_action_hold_names_the_codes(self) -> None:
        self.assertIn(
            "A deploy refused for its hologram answers no_hologram or hologram_colliding",
            self._description("action_hold"),
        )


if __name__ == "__main__":
    unittest.main()
