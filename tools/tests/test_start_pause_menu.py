"""The client bridge closes the pause menu the client starts with (inbox 5880/1d31).

In the 9/11 round's P1b the client started with the in-game pause menu open in
2 of 3 launches; ui_click(continuebtn) closed it and key_press(dik=1) did not.
MCPClientBridge.ContinueStartPauseMenu, called from OnTick once the bridge is
configured, closes a pause menu (MENU_INGAME) on top with the mission's
Continue() within the first START_PAUSE_WINDOW_S of the mission, once, and
logs one line: closed, not open or skipped.

ContinueStartPauseMenu runs as Python (enforce_subset_helpers) against fakes of
the game, its UI manager, the menu on top and the mission; OnTick is read for
where it calls it.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import EnforceString, clean, method_body, translate

BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
CONTINUE = "protected void ContinueStartPauseMenu()"
ON_TICK = "void OnTick(float timeslice)"
CONSTRUCTOR = "void MCPClientBridge()"
MEMBERS = ("m_StartPauseDone", "m_StartPauseWatched", "m_JobRunner", "START_PAUSE_WINDOW_S")
# Any value: the model compares the menu's id with the namespace's constant.
MENU_INGAME = 2
MENU_OPTIONS = 9


class _LogLine(EnforceString):
    """An Enforce string with the + a log line uses on a float."""

    __slots__ = ()

    def __add__(self, other: object) -> "_LogLine":
        if isinstance(other, EnforceString):
            return _LogLine(self.data + other.data)
        if isinstance(other, (int, float)) and not isinstance(other, bool):
            return _LogLine(self.data + repr(other).encode())
        raise TypeError(f"string + {type(other).__name__}")


class Menu:
    def __init__(self, menu_id: int) -> None:
        self.menu_id = menu_id

    def GetID(self) -> int:
        return self.menu_id


class Client:
    """The game a ContinueStartPauseMenu sees: the menu on top, the mission and
    the job runner's clock; Continue closes a pause menu on top, as vanilla's."""

    def __init__(self, source: str, *, mission_gameplay: bool = True) -> None:
        self.now = 0.0
        self.menu: Menu | None = None
        self.continues = 0
        self.log: list[str] = []
        self.has_game = True
        self.has_ui = True
        self.mission = SimpleNamespace(Continue=self._continue) if mission_gameplay else None
        ui = SimpleNamespace(GetMenu=lambda: self.menu)
        game = SimpleNamespace(
            GetUIManager=lambda: ui if self.has_ui else None,
            GetMission=lambda: self.mission,
        )
        self.namespace: dict = {
            "_S": _LogLine,
            "GetGame": lambda: game if self.has_game else None,
            "MissionGameplay": SimpleNamespace(Cast=lambda mission: mission),
            "MENU_INGAME": MENU_INGAME,
            "Log": lambda line: self.log.append(line.text),
            "m_StartPauseDone": False,
            "m_StartPauseWatched": False,
            "m_JobRunner": SimpleNamespace(GetElapsedS=lambda: self.now),
            "START_PAUSE_WINDOW_S": constant(source),
        }
        self.tick = translate(
            source, CONTINUE, self.namespace, class_types=("UIManager", "UIScriptedMenu", "MissionGameplay"),
            members=MEMBERS,
        )

    def _continue(self) -> None:
        self.continues += 1
        if self.menu is not None and self.menu.menu_id == MENU_INGAME:
            self.menu = None

    def at(self, now: float, menu: int | None = None) -> None:
        """One OnTick at `now` with `menu` on top."""
        self.now = now
        self.menu = None if menu is None else Menu(menu)
        self.tick()


def constant(source: str) -> float:
    match = re.search(r"protected const float START_PAUSE_WINDOW_S\s*=\s*([\d.]+)\s*;", clean(source))
    if match is None:
        raise AssertionError("START_PAUSE_WINDOW_S is not declared")
    return float(match.group(1))


class StartPauseMenuModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = BRIDGE_PATH.read_text(encoding="utf-8")

    def client(self, **kwargs: object) -> Client:
        return Client(self.source, **kwargs)

    def test_the_window_is_sixty_seconds(self) -> None:
        self.assertEqual(constant(self.source), 60.0)

    def test_a_pause_menu_on_top_in_the_window_is_continued_once(self) -> None:
        client = self.client()
        client.at(0.0)
        client.at(0.5, MENU_INGAME)
        self.assertEqual(client.continues, 1)
        self.assertIsNone(client.menu)
        self.assertEqual(client.log, ["start pause menu closed elapsed_s=0.5"])
        # One shot: a pause menu opened later in the window stays open.
        client.at(10.0, MENU_INGAME)
        client.at(61.0, MENU_INGAME)
        self.assertEqual(client.continues, 1)
        self.assertEqual(len(client.log), 1)

    def test_a_pause_menu_that_opens_late_in_the_window_is_continued(self) -> None:
        client = self.client()
        for now in (0.0, 5.0, 30.0):
            client.at(now)
        client.at(60.0, MENU_INGAME)
        self.assertEqual(client.continues, 1)
        self.assertEqual(client.log, ["start pause menu closed elapsed_s=60.0"])

    def test_no_pause_menu_in_the_window_logs_not_open_and_leaves_a_later_one(self) -> None:
        client = self.client()
        for now in (0.0, 20.0, 59.9):
            client.at(now)
        self.assertEqual(client.log, [])
        client.at(60.5)
        self.assertEqual(client.log, ["start pause menu not open elapsed_s=60.5"])
        client.at(75.0, MENU_INGAME)
        self.assertEqual(client.continues, 0)
        self.assertEqual(client.menu.GetID(), MENU_INGAME)
        self.assertEqual(len(client.log), 1)

    def test_a_bridge_configured_after_the_window_skips_and_leaves_the_menu(self) -> None:
        client = self.client()
        client.at(75.0, MENU_INGAME)
        self.assertEqual(client.continues, 0)
        self.assertEqual(client.log, ["start pause menu skipped elapsed_s=75.0"])
        client.at(76.0, MENU_INGAME)
        self.assertEqual(len(client.log), 1)

    def test_another_menu_on_top_is_left_and_the_watch_goes_on(self) -> None:
        client = self.client()
        client.at(1.0, MENU_OPTIONS)
        self.assertEqual((client.continues, client.log), (0, []))
        client.at(2.0, MENU_INGAME)
        self.assertEqual(client.continues, 1)
        self.assertEqual(client.log, ["start pause menu closed elapsed_s=2.0"])

    def test_nothing_to_read_keeps_watching_until_the_window_passes(self) -> None:
        client = self.client()
        client.has_game = False
        client.at(1.0, MENU_INGAME)
        client.has_game = True
        client.has_ui = False
        client.at(2.0, MENU_INGAME)
        self.assertEqual((client.continues, client.log), (0, []))
        client.has_ui = True
        client.at(3.0, MENU_INGAME)
        self.assertEqual(client.continues, 1)

    def test_a_mission_that_is_not_gameplay_is_not_continued(self) -> None:
        client = self.client(mission_gameplay=False)
        client.at(1.0, MENU_INGAME)
        self.assertEqual((client.continues, client.log), (0, []))
        client.at(61.0, MENU_INGAME)
        self.assertEqual(client.log, ["start pause menu not open elapsed_s=61.0"])


class StartPauseMenuSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.code = clean(BRIDGE_PATH.read_text(encoding="utf-8"))

    def test_on_tick_calls_it_once_configured_and_before_the_drain(self) -> None:
        body = method_body(self.code, ON_TICK)
        self.assertEqual(len(re.findall(r"\bContinueStartPauseMenu\s*\(", self.code)), 2)
        self.assertEqual(len(re.findall(r"\bContinueStartPauseMenu\s*\(\s*\)\s*;", body)), 1)
        gate = body.index("if (!m_Configured)")
        gate_end = body.index("return;", gate)
        call = body.index("ContinueStartPauseMenu();")
        self.assertLess(gate_end, call)
        self.assertLess(call, body.index("DrainPending();"))

    def test_a_new_bridge_has_not_looked(self) -> None:
        body = re.sub(r"\s+", "", method_body(self.code, CONSTRUCTOR))
        self.assertIn("m_StartPauseDone=false;", body)
        self.assertIn("m_StartPauseWatched=false;", body)

    def test_it_closes_only_through_the_mission_s_continue(self) -> None:
        body = method_body(self.code, CONTINUE)
        self.assertEqual(re.findall(r"\.\s*(Continue|Close|CloseMenu|CloseAll\w*)\s*\(", body), ["Continue"])
        self.assertNotIn("OnClick", body)


if __name__ == "__main__":
    unittest.main()
