"""action_use answers started for a local instant action that ran (204e).

fb-20261009-122149-204e: action_use(ActionTogglePlaceObject) answered
setup_failed every time. The action is local and instant: ActionStart runs its
Start and then OnActionEnd at once (actionmanagerclient.c:719-721 in 1.30,
:677-679 in 1.29), so GetRunningAction() is null when PerformActionStart
returns, as after a start that SetupAction refused. The modded
ActionManagerClient counts OnActionEnd calls, and DispatchActionUse compares
the count across its PerformActionStart.

The Enforce code is read as text, with comments blanked and whitespace outside
string literals removed, so a layout change keeps these tests green and a
change of what the code does turns them red.
"""
from __future__ import annotations

import re
import unittest

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import clean, method_body

BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
HOLD_PATH = addon_root() / "scripts" / "4_World" / "MCP_ActionHold.c"
DISPATCH = "protected bool DispatchActionUse(MCPCommand command, MCPResult result)"
ON_ACTION_END = "override void OnActionEnd()"
ACTION_ENDS = "int MCP_ActionEnds()"

# The end of DispatchActionUse, after the action_hold branch has returned.
DISPATCH_TAIL = """
int endsBefore = amc.MCP_ActionEnds();
amc.PerformActionStart(action, actionTarget, heldItem, NULL);
if (amc.GetRunningAction() == null)
{
    if (amc.MCP_ActionEnds() != endsBefore)
    {
        result.started = true;
        result.ok = true;
        return true;
    }

    result.started = false;
    result.ok = false;
    result.error = "setup_failed";
    return true;
}

result.started = true;
result.ok = true;
return true;
"""
ON_ACTION_END_BODY = """
m_MCP_ActionEnds++;
MCPHoldControl.BeforeActionEnd(this);
super.OnActionEnd();
MCPHoldControl.AfterActionEnd(this);
"""
ACTION_ENDS_BODY = "return m_MCP_ActionEnds;"

_STRING = re.compile(r'("(?:\\.|[^"\\])*")')
_COUNT_WRITE = re.compile(
    r"\bm_MCP_ActionEnds\s*(?:\+\+|--|[-+*/]?=(?!=))|(?:\+\+|--)\s*m_MCP_ActionEnds\b"
)


def _bare(text: str) -> str:
    """`text` without the whitespace outside its string literals."""
    parts = _STRING.split(text)
    return "".join(
        part if index % 2 else re.sub(r"\s+", "", part) for index, part in enumerate(parts)
    )


def verify_dispatch(source: str) -> None:
    if not _bare(method_body(source, DISPATCH)).endswith(_bare(DISPATCH_TAIL)):
        raise AssertionError("DispatchActionUse does not end with the 204e start check")


def verify_counter(source: str) -> None:
    if _bare(method_body(source, ON_ACTION_END)) != _bare(ON_ACTION_END_BODY):
        raise AssertionError("OnActionEnd does not count its calls first")
    if _bare(method_body(source, ACTION_ENDS)) != _bare(ACTION_ENDS_BODY):
        raise AssertionError("MCP_ActionEnds does not return the count")
    if not re.search(r"\bprotected\s+int\s+m_MCP_ActionEnds\s*;", source):
        raise AssertionError("no protected int m_MCP_ActionEnds")


class InstantActionStartTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = BRIDGE_PATH.read_text(encoding="utf-8")
        self.hold = HOLD_PATH.read_text(encoding="utf-8")

    def test_dispatch_reads_the_count_around_its_start(self) -> None:
        verify_dispatch(self.bridge)

    def test_on_action_end_counts_its_calls(self) -> None:
        verify_counter(self.hold)

    def test_only_on_action_end_changes_the_count(self) -> None:
        for path in sorted(addon_root().glob("scripts/**/*.c")):
            code = clean(path.read_text(encoding="utf-8"))
            with self.subTest(file=path.name):
                if path == HOLD_PATH:
                    self.assertEqual(_COUNT_WRITE.findall(code), ["m_MCP_ActionEnds++"])
                else:
                    self.assertNotRegex(code, r"\bm_MCP_ActionEnds\b")

    def test_mutants_are_red(self) -> None:
        dispatch_mutants = {
            "count_read_after_the_start": (
                "int endsBefore = amc.MCP_ActionEnds();\n"
                "\t\tamc.PerformActionStart(action, actionTarget, heldItem, NULL);",
                "amc.PerformActionStart(action, actionTarget, heldItem, NULL);\n"
                "\t\tint endsBefore = amc.MCP_ActionEnds();",
            ),
            "same_count_reads_as_ran": (
                "amc.MCP_ActionEnds() != endsBefore",
                "amc.MCP_ActionEnds() == endsBefore",
            ),
            "ran_answers_not_started": (
                "\t\t\t\tresult.started = true;\n\t\t\t\tresult.ok = true;",
                "\t\t\t\tresult.started = false;\n\t\t\t\tresult.ok = true;",
            ),
            "ran_answers_not_ok": (
                "\t\t\t\tresult.started = true;\n\t\t\t\tresult.ok = true;",
                "\t\t\t\tresult.started = true;\n\t\t\t\tresult.ok = false;",
            ),
        }
        for name, (old, new) in dispatch_mutants.items():
            with self.subTest(mutant=name):
                self.assertEqual(self.bridge.count(old), 1)
                with self.assertRaises(AssertionError):
                    verify_dispatch(self.bridge.replace(old, new))
        counter_mutants = {
            "end_not_counted": ("\t\tm_MCP_ActionEnds++;\n", ""),
            "count_after_super": (
                "\t\tm_MCP_ActionEnds++;\n\t\tMCPHoldControl.BeforeActionEnd(this);\n"
                "\t\tsuper.OnActionEnd();\n",
                "\t\tMCPHoldControl.BeforeActionEnd(this);\n\t\tsuper.OnActionEnd();\n"
                "\t\tm_MCP_ActionEnds++;\n",
            ),
            "accessor_returns_zero": (
                "\t\treturn m_MCP_ActionEnds;\n",
                "\t\treturn 0;\n",
            ),
        }
        for name, (old, new) in counter_mutants.items():
            with self.subTest(mutant=name):
                self.assertEqual(self.hold.count(old), 1)
                with self.assertRaises(AssertionError):
                    verify_counter(self.hold.replace(old, new))

    def test_a_layout_change_stays_green(self) -> None:
        def spread(text: str) -> str:
            return text.replace(";", " ;\n\t ")

        bridge = self.bridge.replace(
            "if (amc.MCP_ActionEnds() != endsBefore)",
            "if (amc.MCP_ActionEnds() != endsBefore) // the action ran",
        )
        verify_dispatch(spread(bridge))
        verify_counter(spread(self.hold))


if __name__ == "__main__":
    unittest.main()
