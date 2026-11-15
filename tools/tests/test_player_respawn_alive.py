"""player_respawn refuses a living, conscious, unrestrained local player.

Vanilla respawns only a dead, unconscious or restrained player:
MissionServer.OnClientRespawnEvent kills an unconscious or restrained one and
leaves any other living one alone (missionserver.c:611-624). The client's
DispatchPlayerRespawn is translated with tests/enforce_subset_helpers.py and
run on fakes, as tests/test_5ad8_respawn_survivor.py does; the daemon's error
text, a full runtime round trip, the description and the changelog are checked
through their public surfaces. Literal values below are pins, not values read
back from the code under test.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from dayz_mcp import loopback
from dayz_mcp.bridge_errors import _bridge_error
from dayz_mcp.core import EXPECTED_BRIDGE_VERSION
from dayz_mcp.server import ServerConfig, ToolError, build_app
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import EnforceArray, EnforceString, method_body, translate
from tests.fence_helpers import INST_CLIENT, PID_CLIENT, bind_both_peers

TOOL = "player_respawn"
PINNED = "player_respawn_survivor"
CODE = "player_alive_conscious"
TEXT = (
    "player_alive_conscious; the local player is alive, conscious and not restrained; "
    "vanilla respawns only a dead, unconscious or restrained player, so nothing was "
    "requested (player_kill first for a new body)"
)
SIGNATURE = "protected bool DispatchPlayerRespawn(MCPCommand command, MCPResult result)"
BRIDGE = (addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c").read_text(encoding="utf-8")
CHANGELOG = (Path(__file__).resolve().parents[2] / "CHANGELOG.md").read_text(encoding="utf-8")
VERSION = f"{EXPECTED_BRIDGE_VERSION}~1.30"
CLIENT_MASK = "1.1ffffffffffff"
CUSTOM = 0
RANDOM = 1
CLASS = "SurvivorM_Manua"

# (alive, unconscious, restrained)
ALIVE_CONSCIOUS_FREE = (True, False, False)
RESPAWNABLE = {
    "dead": (False, False, False),
    "dead, restrained flag left": (False, False, True),
    "unconscious": (True, True, False),
    "restrained": (True, False, True),
    "unconscious and restrained": (True, True, True),
}
HEAD = {
    TOOL: (("SetRandomCharacterForced", True),),
    PINNED: (("SetCharacterType", CLASS), ("SetRandomCharacterForced", False)),
}
TAIL = (
    ("RespawnPlayer",),
    ("SimulateDeath", True),
    ("ShowDeadScreen", True, 0),
    ("DestroyAllMenus",),
    ("SetPlayerRespawning", True),
    ("Continue",),
    ("Close",),
)
TAIL_WITHOUT_PLAYER = (
    ("RespawnPlayer",),
    ("DestroyAllMenus",),
    ("SetPlayerRespawning", True),
    ("Continue",),
    ("Close",),
)


class _CharData:
    def __init__(self, log: list) -> None:
        self._log = log

    def SetCharacterType(self, value: Any) -> None:
        self._log.append(("SetCharacterType", value.text))

    def SetRandomCharacterForced(self, value: bool) -> None:
        self._log.append(("SetRandomCharacterForced", value))


class _Player:
    def __init__(self, log: list, state: tuple[bool, bool, bool]) -> None:
        self._log = log
        self._alive, self._unconscious, self._restrained = state

    def IsAlive(self) -> bool:
        return self._alive

    def IsUnconscious(self) -> bool:
        return self._unconscious

    def IsRestrained(self) -> bool:
        return self._restrained

    def SimulateDeath(self, flag: bool) -> None:
        self._log.append(("SimulateDeath", flag))

    def ShowDeadScreen(self, *_args: Any) -> None:
        raise AssertionError("ShowDeadScreen is passed to Call, not called directly")


class _CallQueue:
    def __init__(self, log: list) -> None:
        self._log = log

    def Call(self, _fn: Any, *args: Any) -> None:
        self._log.append(("ShowDeadScreen",) + args)


class _Menu:
    def __init__(self, log: list) -> None:
        self._log = log

    def Close(self) -> None:
        self._log.append(("Close",))


class _UI:
    def __init__(self, menu: _Menu) -> None:
        self._menu = menu

    def GetMenu(self) -> _Menu:
        return self._menu


class _Mission:
    def __init__(self, log: list, mode: int) -> None:
        self._log = log
        self._mode = mode

    def GetRespawnModeClient(self) -> int:
        return self._mode

    def DestroyAllMenus(self) -> None:
        self._log.append(("DestroyAllMenus",))

    def SetPlayerRespawning(self, flag: bool) -> None:
        self._log.append(("SetPlayerRespawning", flag))

    def Continue(self) -> None:
        self._log.append(("Continue",))


class _Game:
    def __init__(self, log: list, player: _Player | None, mode: int, available: Any) -> None:
        self._log = log
        self._player = player
        self._mission = _Mission(log, mode)
        self._ui = _UI(_Menu(log))
        self._char_data = _CharData(log)
        self._queue = _CallQueue(log)
        self._available = available

    def GetMission(self) -> _Mission:
        return self._mission

    def GetUIManager(self) -> _UI:
        return self._ui

    def GetMenuDefaultCharacterData(self, _flag: bool) -> _CharData:
        return self._char_data

    def ListAvailableCharacters(self) -> Any:
        return self._available

    def RespawnPlayer(self) -> None:
        self._log.append(("RespawnPlayer",))

    def GetPlayer(self) -> _Player | None:
        return self._player

    def GetCallQueue(self, _category: int) -> _CallQueue:
        return self._queue


class _Identity:
    @staticmethod
    def Cast(value: Any) -> Any:
        return value


class _Dummy:
    pass


def _run(
    cmd: str,
    state: tuple[bool, bool, bool] | None,
    *,
    mode: int = CUSTOM,
    survivor: str = CLASS,
    available: tuple[str, ...] = (CLASS,),
) -> tuple[Any, Any, list]:
    log: list = []
    player = None if state is None else _Player(log, state)
    game = _Game(log, player, mode, EnforceArray(EnforceString.of(name) for name in available))
    namespace: dict[str, Any] = {
        "GetGame": lambda: game,
        "MissionGameplay": _Identity,
        "PlayerBase": _Identity,
        "UIScriptedMenu": _Dummy,
        "UIManager": _Dummy,
        "MenuDefaultCharacterData": _Dummy,
        "GameConstants": SimpleNamespace(RESPAWN_MODE_CUSTOM=CUSTOM),
        "CALL_CATEGORY_GUI": 1,
    }
    fn = translate(
        BRIDGE,
        SIGNATURE,
        namespace,
        class_types=("MissionGameplay", "array", "UIScriptedMenu", "UIManager", "MenuDefaultCharacterData", "PlayerBase"),
    )
    command = SimpleNamespace(
        cmd=EnforceString.of(cmd), args=SimpleNamespace(survivor_type=EnforceString.of(survivor))
    )
    # ok and requested start unset, so a refusal that forgets to write ok
    # is seen, and requested stays unset on every refusal.
    result = SimpleNamespace(ok=None, error=EnforceString(b""), requested=None)
    returned = fn(command, result)
    return returned, result, log


class TranslatedDispatchTest(unittest.TestCase):
    def test_a_living_conscious_free_player_is_refused_before_anything_changes(self) -> None:
        for cmd in (TOOL, PINNED):
            with self.subTest(cmd=cmd):
                returned, result, log = _run(cmd, ALIVE_CONSCIOUS_FREE)
                self.assertIs(returned, True)
                self.assertEqual((result.ok, result.requested, result.error.text), (False, None, CODE))
                self.assertEqual(log, [])

    def test_dead_unconscious_and_restrained_players_still_get_todays_recipe(self) -> None:
        for cmd in (TOOL, PINNED):
            for label, state in RESPAWNABLE.items():
                with self.subTest(cmd=cmd, state=label):
                    returned, result, log = _run(cmd, state)
                    self.assertIs(returned, True)
                    self.assertEqual((result.ok, result.requested, result.error.text), (True, True, ""))
                    self.assertEqual(log, [*HEAD[cmd], *TAIL])

    def test_no_local_player_is_not_refused(self) -> None:
        # Vanilla's menu reads a null player as not alive and offers the
        # respawn (ingamemenu.c:321, 326); the recipe skips the body steps.
        for cmd in (TOOL, PINNED):
            with self.subTest(cmd=cmd):
                returned, result, log = _run(cmd, None)
                self.assertIs(returned, True)
                self.assertEqual((result.ok, result.requested, result.error.text), (True, True, ""))
                self.assertEqual(log, [*HEAD[cmd], *TAIL_WITHOUT_PLAYER])

    def test_the_survivor_type_refusals_come_first(self) -> None:
        # Static refusals win, so a caller is not sent to player_kill only to
        # meet respawn_mode_random or survivor_type_unavailable next.
        cases = (
            ("mode random", {"mode": RANDOM}, "respawn_mode_random"),
            ("unavailable", {"available": ("SurvivorF_Eva",)}, "survivor_type_unavailable"),
            ("empty", {"survivor": ""}, "bad_args"),
        )
        for label, kwargs, error in cases:
            with self.subTest(case=label):
                returned, result, log = _run(PINNED, ALIVE_CONSCIOUS_FREE, **kwargs)
                self.assertIs(returned, True)
                self.assertEqual((result.ok, result.requested, result.error.text), (False, None, error))
                self.assertEqual(log, [])

    def test_the_default_call_ignores_the_mode_but_not_the_living_player(self) -> None:
        for mode in (CUSTOM, RANDOM, -1):
            with self.subTest(mode=mode):
                _returned, refused, refused_log = _run(TOOL, ALIVE_CONSCIOUS_FREE, mode=mode)
                self.assertEqual((refused.ok, refused.error.text), (False, CODE))
                self.assertEqual(refused_log, [])
                _returned, done, done_log = _run(TOOL, RESPAWNABLE["dead"], mode=mode)
                self.assertEqual((done.ok, done.requested), (True, True))
                self.assertEqual(done_log, [*HEAD[TOOL], *TAIL])


class EnforceSourceTest(unittest.TestCase):
    def test_the_check_sits_between_the_argument_checks_and_the_first_change(self) -> None:
        body = method_body(BRIDGE, SIGNATURE)
        read = "PlayerBase localPlayer = PlayerBase.Cast(GetGame().GetPlayer());"
        check = (
            "if (localPlayer && localPlayer.IsAlive() && !localPlayer.IsUnconscious() "
            "&& !localPlayer.IsRestrained())"
        )
        refusal = 'result.error = "player_alive_conscious";'
        for text in (read, check, refusal, "GetGame().RespawnPlayer();"):
            with self.subTest(text=text):
                self.assertEqual(body.count(text), 1)
        order = [
            body.index('result.error = "respawn_mode_random";'),
            body.index(read),
            body.index(check),
            body.index(refusal),
            body.index("GetMenuDefaultCharacterData"),
            body.index("GetGame().RespawnPlayer();"),
            body.index("PlayerBase player = PlayerBase.Cast(GetGame().GetPlayer());"),
            body.index("player.SimulateDeath(true);"),
        ]
        self.assertEqual(order, sorted(order))


class DaemonErrorTextTest(unittest.TestCase):
    def test_both_commands_carry_the_fixed_text(self) -> None:
        for cmd in (TOOL, PINNED):
            with self.subTest(cmd=cmd):
                self.assertEqual(str(_bridge_error({"ok": 0, "error": CODE}, cmd)), TEXT)

    def test_other_codes_and_other_verbs_stay_bare(self) -> None:
        for cmd, code in (
            (TOOL, "no_mission"),
            (PINNED, "respawn_mode_random"),
            (PINNED, "survivor_type_unavailable"),
            ("player_kill", CODE),
            ("player_heal", CODE),
        ):
            with self.subTest(cmd=cmd, code=code):
                self.assertEqual(str(_bridge_error({"ok": 0, "error": code}, cmd)), code)


def _tool_error_text(exc: BaseException) -> str:
    message = str(exc)
    wrapper = f"Error executing tool {TOOL}: "
    return message[len(wrapper):] if message.startswith(wrapper) else message


class ToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(ServerConfig(key="test-key", port=0, log_sink=lambda _m: None))

    async def test_the_refusal_reaches_the_caller_through_the_full_runtime(self) -> None:
        state = loopback.ServerState("test-key")
        bind_both_peers(state)

        def poll() -> dict[str, Any]:
            status, body = state.record_poll(
                "client", VERSION, instance=INST_CLIENT, source_pid=PID_CLIENT, cm=CLIENT_MASK
            )
            self.assertEqual(status, 200, body)
            return body

        poll()
        self.runtime.loopback = SimpleNamespace(state=state)
        original_enqueue = state.enqueue_command
        sent: list[dict[str, Any]] = []

        def enqueue_and_refuse(*args: Any, **kwargs: Any) -> tuple[int, dict[str, Any]]:
            status, body = original_enqueue(*args, **kwargs)
            if status == 200:
                sent.extend(poll()["commands"])
                stored = state.store_result({"id": body["id"], "ok": 0, "error": CODE}, instance=INST_CLIENT)
                self.assertEqual(stored[0], 200, stored[1])
            return status, body

        with patch.object(state, "enqueue_command", side_effect=enqueue_and_refuse):
            for arguments, wire in (
                ({}, {"cmd": TOOL, "args": {}}),
                ({"survivor_type": CLASS}, {"cmd": PINNED, "args": {"survivor_type": CLASS}}),
            ):
                with self.subTest(arguments=arguments):
                    sent.clear()
                    with self.assertRaises(ToolError) as raised:
                        await self.app.call_tool(TOOL, {**arguments, "timeout_s": 2.0})
                    self.assertEqual(_tool_error_text(raised.exception), TEXT)
                    self.assertEqual([{k: v for k, v in c.items() if k != "id"} for c in sent], [wire])

    async def test_the_description_names_the_refusal(self) -> None:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        description = tools[TOOL].description or ""
        for phrase in (
            "Vanilla respawns only a dead, unconscious or restrained player, and the server kills an "
            "unconscious or restrained one to respawn it.",
            "With or without survivor_type, a living, conscious, unrestrained local player is refused "
            "as player_alive_conscious and nothing changes (player_kill first for a new body)",
            "a client bridge older than this release answers ok/requested instead and nothing happens",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, description)

    def test_the_changelog_names_the_refusal_and_the_pbo(self) -> None:
        unreleased = CHANGELOG.split("## [Unreleased]", 1)[1].split("## [", 1)[0]
        fixed = unreleased.split("### Fixed", 1)[1].split("### ", 1)[0]
        entry = [line for line in fixed.splitlines() if "`player_alive_conscious`" in line]
        self.assertEqual(len(entry), 1)
        for phrase in ("missionserver.c:611-624", "Requires a new PBO", "in-game validation is pending"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, entry[0])


if __name__ == "__main__":
    unittest.main()
