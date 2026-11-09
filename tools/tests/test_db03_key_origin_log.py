"""db03 (and its correction ef72): the client logs every key the mission gets, with its origin.

A configured client logs "key press key=<k> origin=mcp|engine app_active=0|1"
for each Mission.OnKeyPress, before the dialog checks. origin=mcp only while
the MCP itself delivers that key (key_press, or input_trigger through either
entry); app_active is whether the game window had the focus. A client that
is not configured (a player with the PBO) logs nothing.

The bodies are the real ones of MCPClientBridge.c, translated and run on
fakes (enforce_subset_helpers). The fake game and mission forward a press to
OnMissionKeyPress, as DayZGame.OnKeyPress and the modded
MissionGameplay.OnKeyPress do.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import EnforceString, translate

BRIDGE = (addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c").read_text(
    encoding="utf-8"
)
ON_KEY = "void OnMissionKeyPress(int key)"
KEY_PRESS = "protected bool DispatchKeyPress(MCPCommand command, MCPResult result)"
DELIVER = "static bool Deliver(string entry, int dik, bool press)"
F10 = 68


class _Line(EnforceString):
    """A log string: Log's concatenations of strings and ints."""

    def __add__(self, other: object) -> "_Line":
        if isinstance(other, bool) or not isinstance(other, (int, EnforceString)):
            return NotImplemented
        tail = other.data if isinstance(other, EnforceString) else str(other).encode("ascii")
        return _Line(self.data + tail)


class _Client:
    """OnMissionKeyPress, DispatchKeyPress and Deliver over one namespace."""

    def __init__(self, *, configured: bool = True, app_active: bool = True) -> None:
        self.log: list[str] = []
        self.cancels: list[tuple[int, float]] = []
        self.calls: list[tuple[str, int, bool]] = []
        self.dialog_open = False
        dialog = SimpleNamespace(
            IsOpen=lambda: self.dialog_open,
            TryCancelFromKey=lambda key, now: self.cancels.append((key, now)),
        )
        ns: dict = {}
        mission = SimpleNamespace(
            OnKeyPress=lambda key: self._mission_press(key),
            OnKeyRelease=lambda key: self.calls.append(("release", key, ns["s_Injecting"])),
        )
        game = SimpleNamespace(
            IsAppActive=lambda: app_active,
            GetMission=lambda: mission,
            # DayZGame.OnKeyPress ends in the mission's (dayzgame.c:2804-2918).
            OnKeyPress=lambda key: self._mission_press(key),
            OnKeyRelease=lambda key: self.calls.append(("release", key, ns["s_Injecting"])),
        )

        class Control:
            # MCPInputTriggerControl's statics, shared with Deliver's bare names.
            def __getattr__(self, name: str) -> object:
                return ns[name]

            def __setattr__(self, name: str, value: object) -> None:
                ns[name] = value

        ns.update(
            _S=_Line,
            Log=lambda line: self.log.append(line.data.decode("ascii")),
            GetGame=lambda: game,
            MCPInputTriggerControl=Control(),
            s_Injecting=False,
            s_InjectingDik=0,
            m_Configured=configured,
            m_Dialog=dialog,
            m_JobRunner=SimpleNamespace(GetElapsedS=lambda: 12.5),
        )
        translate(BRIDGE, ON_KEY, ns)
        translate(BRIDGE, KEY_PRESS, ns)
        translate(
            BRIDGE,
            DELIVER,
            ns,
            class_types=("Mission",),
            members=("s_Injecting", "s_InjectingDik"),
        )
        self.ns = ns

    def _mission_press(self, key: int) -> None:
        self.calls.append(("press", key, self.ns["s_Injecting"]))
        self.ns["OnMissionKeyPress"](key)

    def engine_press(self, key: int) -> None:
        self.ns["OnMissionKeyPress"](key)

    def key_press(self, dik: int) -> SimpleNamespace:
        result = SimpleNamespace(ok=False, error=_Line(b""), delivered=False, dik=-1)
        command = SimpleNamespace(args=SimpleNamespace(dik=dik))
        self.ns["DispatchKeyPress"](command, result)
        return result

    def deliver(self, entry: str, dik: int, press: bool) -> bool:
        return self.ns["Deliver"](_Line.of(entry), dik, press)

    def key_lines(self) -> list[str]:
        return [line for line in self.log if line.startswith("key press ")]


class KeyOriginLogTest(unittest.TestCase):
    def test_an_engine_key_is_logged_with_its_origin_and_focus(self) -> None:
        for app_active, flag in ((True, "1"), (False, "0")):
            with self.subTest(app_active=app_active):
                client = _Client(app_active=app_active)
                client.engine_press(F10)
                self.assertEqual(
                    client.key_lines(), [f"key press key=68 origin=engine app_active={flag}"]
                )

    def test_a_key_the_mcp_delivers_is_logged_as_mcp(self) -> None:
        paths = {
            "key_press": lambda client: client.key_press(F10).delivered,
            "game": lambda client: client.deliver("game", F10, True),
            "mission": lambda client: client.deliver("mission", F10, True),
        }
        for path, deliver in paths.items():
            with self.subTest(path=path):
                client = _Client()
                self.assertTrue(deliver(client))
                # Marked only during its own press: the next engine key is the engine's.
                self.assertEqual(client.calls, [("press", F10, True)])
                self.assertIs(client.ns["s_Injecting"], False)
                client.engine_press(F10)
                self.assertEqual(
                    client.key_lines(),
                    [
                        "key press key=68 origin=mcp app_active=1",
                        "key press key=68 origin=engine app_active=1",
                    ],
                )

    def test_another_key_during_an_mcp_press_is_the_engine_s(self) -> None:
        client = _Client()
        client.ns["s_Injecting"] = True
        client.ns["s_InjectingDik"] = F10
        client.engine_press(59)
        self.assertEqual(client.key_lines(), ["key press key=59 origin=engine app_active=1"])

    def test_a_release_is_not_marked(self) -> None:
        client = _Client()
        for entry in ("game", "mission"):
            self.assertTrue(client.deliver(entry, F10, False))
        self.assertEqual(client.calls, [("release", F10, False)] * 2)
        self.assertEqual(client.key_lines(), [])

    def test_a_client_that_is_not_configured_logs_nothing(self) -> None:
        client = _Client(configured=False)
        client.engine_press(F10)
        client.key_press(F10)
        self.assertEqual(client.log, [])

    def test_an_open_dialog_still_gets_the_key(self) -> None:
        client = _Client()
        client.dialog_open = True
        client.engine_press(F10)
        self.assertEqual(client.cancels, [(F10, 12.5)])
        self.assertEqual(client.log[-1], "OnKeyPress key=68")


if __name__ == "__main__":
    unittest.main()
