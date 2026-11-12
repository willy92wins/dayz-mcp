"""Inbox fb-20261110-170149-726c: ui_pause opens the client pause menu like Esc.

The client bridge dispatches ui_pause through MissionGameplay.Pause() (the same
method MissionGameplay.OnUpdate invokes on UAUIMenu / Esc in vanilla 1.30.164289,
missiongameplay.c:700-704, :1299-1332), records menu_id_before (-1 when GetMenu()
is null), reads menu_id_after immediately on synchronous branches (answering in
the same call without waiting on m_JobRunner), and reserves an MCPJob on
m_JobRunner for Pause()'s deferred m_PauseQueued branches (:1306-1317, drained by
MissionGameplay.OnUpdate at :308-311) so PostUiPauseJob reads menu_id_after on
the next tick. Answers ok=true iff menu_id_after == MENU_INGAME and
pause_not_opened otherwise, and emits one Log(...) line with both IDs and the
outcome on every completion or early-refusal path.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, server
from dayz_mcp.result_prune import PRUNABLE_FIELDS, prune_unfilled_fields
from dayz_mcp.server import (
    DEFAULT_TOOL_TIMEOUT_S,
    LEASE_TOOL_LINE,
    ServerConfig,
    ToolError,
    _BRIDGE_COMMAND_TOOLS,
    build_app,
)
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease
from tests._addon_paths import addon_root
from tests.bridge_client_capabilities_helpers import announced_caps, dispatch_census
from tests.enforce_subset_helpers import EnforceString, fake_class, method_body, translate
from tests.fence_helpers import (
    INST_CLIENT,
    PID_CLIENT,
    bind_both_peers,
    bound_queue,
    poll_census_query,
)


COMMAND = "ui_pause"
MENU_INGAME_ID = 9  # vanilla MENU_INGAME on 1.30.164289
DAYZ_GAME_STATE_IN_GAME = 5
SCRIPTS = addon_root() / "scripts"
CLIENT_PATH = SCRIPTS / "5_Mission" / "MCPClientBridge.c"
MISSION_GAMEPLAY_PATH = SCRIPTS / "5_Mission" / "MissionGameplay.c"
RUNNER_PATH = SCRIPTS / "5_Mission" / "MCPJobRunner.c"
SERVER_BRIDGE_PATH = SCRIPTS / "5_Mission" / "MCPBridge.c"
MESSAGES_PATH = SCRIPTS / "5_Mission" / "MCPMessages.c"
CENSUS_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bridge_capabilities_v1.json"

DISPATCH_CHAIN = "protected void Dispatch(MCPCommand command)"
IN_GAME = "protected bool IsClientInGame()"
CONTINUE_START_PAUSE = "protected void ContinueStartPauseMenu()"
DISPATCH_UI_PAUSE = "protected bool DispatchUiPause(MCPCommand command, MCPResult result)"
PROCESS_UI_PAUSE = "protected bool ProcessUiPauseJob(MCPJob job)"
POST_UI_PAUSE = "protected void PostUiPauseJob(MCPJob job)"
VERB_METHODS = (DISPATCH_UI_PAUSE, PROCESS_UI_PAUSE, POST_UI_PAUSE)
MISSION_IS_PAUSE_QUEUED = "bool MCP_IsPauseQueued()"
MISSION_CANCEL_QUEUED_PAUSE = "void MCP_CancelQueuedPause()"
MISSION_PAUSE_METHODS = (MISSION_IS_PAUSE_QUEUED, MISSION_CANCEL_QUEUED_PAUSE)

PROCESS_HOOK = "override bool MCP_ProcessJob(MCPJob job)"
READY_HOOK = "override bool MCP_IsJobReady(MCPJob job)"
SUCCESS_HOOK = "override void MCP_PostJobSuccess(MCPJob job)"
FAILURE_HOOK = "override void MCP_PostJobFailure(MCPJob job)"
TIMEOUT_HOOK = "override void MCP_PostJobTimeout(MCPJob job)"
CLEAR_HOOK = "override void MCP_ClearJobRefs(MCPJob job)"
OWNER_HOOKS = (
    PROCESS_HOOK,
    READY_HOOK,
    SUCCESS_HOOK,
    FAILURE_HOOK,
    TIMEOUT_HOOK,
    CLEAR_HOOK,
)
RUNNER_METHODS = (
    "float GetElapsedS()",
    "void AddJob(MCPJob job)",
    "int CountExcluding(string kind)",
    "int CountOfKind(string kind)",
    "void Tick(float timeslice, MCPJobRunnerOwner owner)",
    "protected void ProcessJobs(MCPJobRunnerOwner owner)",
)
LOCAL_CLASS_TYPES = frozenset(
    {
        "DayZPlayerCamera",
        "HumanCommandVehicle",
        "MCPCommand",
        "MCPJob",
        "MCPJobRunnerOwner",
        "MCPResult",
        "MCPUiPause",
        "MissionGameplay",
        "PlayerBase",
        "Transport",
        "UIManager",
        "UIScriptedMenu",
    }
)

_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')
_CONSTRUCTOR_EXPR = re.compile(r"\bnew\s+[A-Za-z_]\w*\s*(?:<[^()]*>)?\s*\([^)]*\)")
_PAUSE_METHOD_CALL = re.compile(r"(?:\b[A-Za-z_]\w*\.)?\bPause\s*\([^)]*\)")
_MEMBER_NAME = re.compile(r"(?<![\w.])(m_[A-Za-z0-9_]+)\b")
_NEW_CALL = re.compile(r"\bnew\s+([A-Za-z_]\w*)\s*\(")
_ENFORCE_LITERAL = re.compile(r'"(?:\\.|[^"\\])*"')

_LOCAL_TYPES = (
    "MissionGameplay",
    "PlayerBase",
    "UIManager",
    "UIScriptedMenu",
    "MCPUiPause",
    "MCPJob",
    "MCPResult",
    "float",
    "int",
    "bool",
    "string",
)
_LOCAL_DECLARATION = re.compile(r"^\s*(?:" + "|".join(_LOCAL_TYPES) + r")\s+(\w+)\s*;\s*$")

LOCAL_PLAYER_GUARD = (
    "\t\tif (!GetGame() || !GetGame().GetPlayer() || GetGame().IsDedicatedServer())\n"
    "\t\t{\n"
    '\t\t\tresult.ok = false;\n'
    '\t\t\tresult.error = "client_not_in_game";\n'
    '\t\t\tLog("ui_pause before=-1 after=-1 ok=0 error=client_not_in_game");\n'
    "\t\t\treturn true;\n"
    "\t\t}"
)
MISSION_CAST_GUARD = (
    "\t\tmission = MissionGameplay.Cast(GetGame().GetMission());\n"
    "\t\tif (!mission)\n"
    "\t\t{\n"
    '\t\t\tresult.ok = false;\n'
    '\t\t\tresult.error = "no_mission";\n'
    '\t\t\tLog("ui_pause before=-1 after=-1 ok=0 error=no_mission");\n'
    "\t\t\treturn true;\n"
    "\t\t}"
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _block_after(source: str, start: int) -> str:
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated block at offset {start}")


def _method_body(source: str, signature: str) -> str:
    count = source.count(signature)
    if count != 1:
        raise AssertionError(f"{signature!r} occurs {count} times")
    return _block_after(source, source.index(signature))


def _if_body(source: str, condition: str) -> str:
    needle = f"if ({condition})"
    count = source.count(needle)
    if count != 1:
        raise AssertionError(f"{needle!r} occurs {count} times")
    return _block_after(source, source.index(needle))


def _class_body(source: str, name: str) -> str:
    match = re.search(rf"^class {re.escape(name)}\b", source, re.M)
    if match is None:
        raise AssertionError(f"class {name} is absent")
    return _block_after(source, match.end())


def _without_comments(source: str) -> str:
    def replace(match: re.Match[str]) -> str:
        text = match.group(0)
        return text if text.startswith('"') else re.sub(r"[^\n]", " ", text)

    return _COMMENT_OR_STRING.sub(replace, source)


def _without_comments_or_strings(source: str) -> str:
    def replace(match: re.Match[str]) -> str:
        text = match.group(0)
        return '""' if text.startswith('"') else re.sub(r"[^\n]", " ", text)

    return _COMMENT_OR_STRING.sub(replace, source)


def _pause_method_calls(source: str) -> list[str]:
    """Return every Pause(...) method call in `source`, ignoring `new <Type>(...)` constructors."""
    without_literals = _without_comments_or_strings(source)
    without_constructors = _CONSTRUCTOR_EXPR.sub("", without_literals)
    return _PAUSE_METHOD_CALL.findall(without_constructors)


def _locals_declared_at_top(body: str) -> list[str]:
    names: list[str] = []
    statement_seen = False
    for raw in _without_comments(body).splitlines():
        line = raw.strip()
        if not line or line in ("{", "}"):
            continue
        match = _LOCAL_DECLARATION.match(raw)
        if match is None:
            statement_seen = True
            continue
        if statement_seen:
            raise AssertionError(f"declaration after a statement: {line}")
        if match.group(1) in names:
            raise AssertionError(f"local declared twice: {match.group(1)}")
        names.append(match.group(1))
    return names


def _mutate(source: str, owner: str, old: str, new: str) -> str:
    """Return `source` with `old` replaced by `new` inside `owner`'s block only."""
    if source.count(owner) != 1:
        raise AssertionError(f"{owner!r} occurs {source.count(owner)} times")
    start = source.index(owner)
    body = _block_after(source, start)
    at = source.index(body, start)
    if body.count(old) != 1:
        raise AssertionError(f"{old!r} occurs {body.count(old)} times after {owner!r}")
    return source[:at] + body.replace(old, new, 1) + source[at + len(body) :]


def _free_method(source: str, signature: str) -> str:
    """Turn `signature`'s class method into a free function for enforce_subset_helpers.translate."""

    def rewrite(code: str) -> str:
        return _NEW_CALL.sub(r"\1(", _MEMBER_NAME.sub(r"this.\1", code))

    body = method_body(source, signature)
    pieces: list[str] = []
    last = 0
    for match in _ENFORCE_LITERAL.finditer(body):
        pieces += [rewrite(body[last : match.start()]), match.group(0)]
        last = match.end()
    pieces.append(rewrite(body[last:]))
    return signature + "\n{" + "".join(pieces) + "}\n"


def _tool_error_text(exc: BaseException) -> str:
    message = str(exc)
    wrapper = f"Error executing tool {COMMAND}: "
    return message[len(wrapper) :] if message.startswith(wrapper) else message


def _tool_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    blocks = raw[0] if isinstance(raw, tuple) else raw
    return json.loads(blocks[0].text)


def assert_dispatch_ui_pause_contract(
    dispatch_body: str,
    process_body: str | None = None,
    post_body: str | None = None,
) -> None:
    """Verify DispatchUiPause reads immediately on sync branches and defers only m_PauseQueued."""
    code = _without_comments(dispatch_body)
    pause_calls = _pause_method_calls(dispatch_body)
    if pause_calls != ["mission.Pause()"]:
        raise AssertionError(f"expected exactly one mission.Pause() call, got {pause_calls!r}")
    if code.count("mission.Pause();") != 1:
        raise AssertionError("mission.Pause(); statement must appear once")
    if code.count(LOCAL_PLAYER_GUARD) != 1:
        raise AssertionError("missing or altered local-player / dedicated-server guard")
    if code.count(MISSION_CAST_GUARD) != 1:
        raise AssertionError("missing or altered MissionGameplay.Cast guard")

    tokens = (
        LOCAL_PLAYER_GUARD,
        MISSION_CAST_GUARD,
        "beforeId = -1;",
        "mission.Pause();",
        "afterId = -1;",
        "reply = new MCPUiPause();",
        "reply.menu_id_before = beforeId;",
        "reply.menu_id_after = afterId;",
        "deferred = false;",
        "if (!mission.IsPaused() && afterId != MENU_INGAME)",
        "g_Game.GetGameState() != DayZGameState.IN_GAME",
        "(player && !player.IsPlayerLoaded()) || mission.IsPlayerRespawning()",
        "if (deferred)",
        "job = new MCPJob();",
        'job.kind = "ui_pause";',
        "job.ui_pause = reply;",
        "m_JobRunner.AddJob(job);",
        "return false;",
        "result.ui_pause = reply;",
        "if (afterId == MENU_INGAME)",
        'result.error = "pause_not_opened";',
        "\n\t\treturn true;",
    )
    positions: list[int] = []
    for token in tokens:
        if token not in code:
            raise AssertionError(f"missing DispatchUiPause step: {token!r}")
        positions.append(code.index(token))
    if positions != sorted(positions) or len(set(positions)) != len(positions):
        raise AssertionError(f"DispatchUiPause steps out of order: {positions}")
    if positions[0] + len(LOCAL_PLAYER_GUARD) > positions[1]:
        raise AssertionError("mission cast overlaps local-player guard")
    if positions[1] + len(MISSION_CAST_GUARD) > positions[3]:
        raise AssertionError("mission.Pause() runs before MissionGameplay.Cast guard finishes")

    if process_body is not None:
        if _pause_method_calls(process_body) != []:
            raise AssertionError("ProcessUiPauseJob must not call Pause() directly")
        proc_code = _without_comments(process_body)
        if "return m_JobRunner.GetElapsedS() > job.sample_start_s;" not in proc_code:
            raise AssertionError(
                "missing ProcessUiPauseJob next-tick check: "
                "'return m_JobRunner.GetElapsedS() > job.sample_start_s;'"
            )
        for forbidden in ("IsPlayerLoaded", "IsPlayerRespawning", "GetGameState"):
            if forbidden in proc_code:
                raise AssertionError(
                    f"ProcessUiPauseJob must read on the next tick instead of waiting on {forbidden}"
                )

    if post_body is not None:
        if _pause_method_calls(post_body) != []:
            raise AssertionError("PostUiPauseJob must not call Pause() directly")
        post_code = _without_comments(post_body)
        for required in (
            "afterId = -1;",
            "reply.menu_id_after = afterId;",
            "result.ui_pause = reply;",
            "else if (afterId == MENU_INGAME)",
            'result.error = "pause_not_opened";',
            "PostResult(result);",
        ):
            if required not in post_code:
                raise AssertionError(f"missing PostUiPauseJob step: {required!r}")


class _WireString(EnforceString):
    """EnforceString supporting Enforce `+` concatenation with string, int, and float values."""

    __slots__ = ()

    def __add__(self, other: object) -> "_WireString":
        if isinstance(other, EnforceString):
            return _WireString(self.data + other.data)
        if type(other) is int:
            return _WireString(self.data + str(other).encode("ascii"))
        if type(other) is float:
            return _WireString(self.data + repr(other).encode("ascii"))
        raise TypeError(f"unsupported Enforce string + {type(other).__name__}")

    def __radd__(self, other: object) -> "_WireString":
        if isinstance(other, EnforceString):
            return _WireString(other.data + self.data)
        if type(other) is int:
            return _WireString(str(other).encode("ascii") + self.data)
        if type(other) is float:
            return _WireString(repr(other).encode("ascii") + self.data)
        raise TypeError(f"unsupported {type(other).__name__} + Enforce string")


class _Menu:
    def __init__(
        self,
        menu_id: int,
        parent: "_Menu | None" = None,
        visible: bool = True,
    ) -> None:
        self.menu_id = menu_id
        self.parent = parent
        self.visible = visible

    def GetID(self) -> int:
        return self.menu_id

    def IsVisible(self) -> bool:
        return self.visible


class _UIManager:
    """Models vanilla UIManager.GetMenu / IsMenuOpen / CloseAll / CloseMenu / EnterScriptedMenu."""

    def __init__(self, top: _Menu | None = None) -> None:
        self.top = top
        self.enter_visible = True
        self.ingame_openings = 0

    def GetMenu(self) -> _Menu | None:
        return self.top

    def IsMenuOpen(self, target_id: int) -> bool:
        cursor = self.top
        while cursor is not None:
            if cursor.menu_id == target_id:
                return True
            cursor = cursor.parent
        return False

    def CloseAll(self) -> None:
        self.top = None

    def CloseMenu(self, target_id: int) -> None:
        if self.top is not None and self.top.menu_id == target_id:
            self.top = self.top.parent

    def EnterScriptedMenu(self, menu_id: int, parent: _Menu | None) -> _Menu | None:
        if not self.enter_visible:
            return None
        opened = _Menu(menu_id, parent=parent, visible=True)
        if menu_id == MENU_INGAME_ID:
            self.ingame_openings += 1
        self.top = opened
        return opened


class _Man:
    """A local player handle that is not a PlayerBase."""


class _Player(_Man):
    def __init__(self, *, loaded: bool = True) -> None:
        self.loaded = loaded

    def IsPlayerLoaded(self) -> bool:
        return self.loaded


class _MissionGameplay:
    """Models vanilla 1.30.164289 MissionGameplay.IsPaused / Pause / Continue (:308-311, :1294-1349) plus translated modded queue helpers."""

    def __init__(
        self,
        model: "_UiPauseClientModel",
        mission_source: str | None = None,
    ) -> None:
        mission_source = (
            _source(MISSION_GAMEPLAY_PATH) if mission_source is None else mission_source
        )
        self._model = model
        self.m_PauseQueued = False
        self.respawning = False
        self.pause_calls = 0
        self.continue_calls = 0
        mission_ns: dict[str, Any] = {"this": self, "_S": _WireString}
        for signature in MISSION_PAUSE_METHODS:
            translate(
                _free_method(mission_source, signature),
                signature,
                mission_ns,
                class_types=LOCAL_CLASS_TYPES,
            )
        self.MCP_IsPauseQueued = mission_ns["MCP_IsPauseQueued"]
        self.MCP_CancelQueuedPause = mission_ns["MCP_CancelQueuedPause"]

    def IsPaused(self) -> bool:
        return self._model.ui.IsMenuOpen(MENU_INGAME_ID)

    def IsPlayerRespawning(self) -> bool:
        return self.respawning

    def Pause(self) -> None:
        self.pause_calls += 1
        top = self._model.ui.GetMenu()
        if self.IsPaused() or (top is not None and top.GetID() == MENU_INGAME_ID):
            return
        self.m_PauseQueued = True
        if self._model.is_client and self._model.game_state != DAYZ_GAME_STATE_IN_GAME:
            return
        player = self._model.player if isinstance(self._model.player, _Player) else None
        if (player is not None and not player.IsPlayerLoaded()) or self.IsPlayerRespawning():
            return
        self._model.ui.CloseAll()
        menu = self._model.ui.EnterScriptedMenu(MENU_INGAME_ID, self._model.ui.GetMenu())
        if menu is None or not menu.IsVisible():
            return
        # InGameMenu.Init() re-entrantly calls mission.Pause() (ingamemenu.c:73-75),
        # which hits the IsPaused() / MENU_INGAME guard above and returns.
        self.Pause()
        self.m_PauseQueued = False

    def Continue(self) -> None:
        self.continue_calls += 1
        top = self._model.ui.GetMenu()
        if top is not None:
            if not self.IsPaused() or top.GetID() != MENU_INGAME_ID:
                return
        self._model.ui.CloseMenu(MENU_INGAME_ID)


class _JobMap:
    """MCPJobRunner's map<int, ref MCPJob> in insertion order."""

    def __init__(self) -> None:
        self.items: list[tuple[int, Any]] = []

    def Insert(self, key: int, job: Any) -> None:
        self.items.append((key, job))

    def Count(self) -> int:
        return len(self.items)

    def GetKey(self, index: int) -> int:
        return self.items[index][0]

    def GetElement(self, index: int) -> Any:
        return self.items[index][1]

    def Remove(self, key: int) -> None:
        self.items = [item for item in self.items if item[0] != key]


class _UiPauseClientModel:
    """Runs the real translated Enforce methods for ui_pause and MCPJobRunner against vanilla UI fakes."""

    def __init__(
        self,
        client: str | None = None,
        runner: str | None = None,
        messages: str | None = None,
        mission_gameplay: str | None = None,
    ) -> None:
        client = _source(CLIENT_PATH) if client is None else client
        runner = _source(RUNNER_PATH) if runner is None else runner
        messages = _source(MESSAGES_PATH) if messages is None else messages
        mission_gameplay = (
            _source(MISSION_GAMEPLAY_PATH)
            if mission_gameplay is None
            else mission_gameplay
        )

        self.player: object | None = _Player(loaded=True)
        self.is_dedicated_server = False
        self.is_client = True
        self.game_state = DAYZ_GAME_STATE_IN_GAME
        self.ui = _UIManager()
        self.mission: object | None = _MissionGameplay(self, mission_gameplay)
        self.has_game = True

        self.posted: list[Any] = []
        self.logs: list[str] = []
        self.handled: list[str] = []

        runner_state = SimpleNamespace(m_Jobs=_JobMap(), m_ElapsedS=0.0)
        self.jobs = runner_state.m_Jobs
        runner_ns: dict[str, Any] = {"this": runner_state, "_S": _WireString}
        for signature in RUNNER_METHODS:
            translate(
                _free_method(runner, signature),
                signature,
                runner_ns,
                class_types=LOCAL_CLASS_TYPES,
            )
        self.runner = SimpleNamespace(
            **{
                name: runner_ns[name]
                for name in ("GetElapsedS", "AddJob", "CountExcluding", "CountOfKind", "Tick")
            }
        )
        self.state = SimpleNamespace(
            m_TickPollSent=11,
            m_TickPollCallback=12,
            m_Tick=13,
            m_JobRunner=self.runner,
            m_DialogJob=None,
            m_CameraHandoffPending=False,
            m_MCPPauseQueued=False,
            m_StartPauseDone=False,
            m_StartPauseFromS=-1.0,
        )
        constants: dict[str, float | int] = {
            name: float(value) if kind == "float" else int(value)
            for kind, name, value in re.findall(
                r"protected const (float|int) (CAMERA_\w+|START_PAUSE_WINDOW_S)\s*=\s*([0-9.]+)\s*;",
                _without_comments(client),
            )
        }
        if "START_PAUSE_WINDOW_S" not in constants:
            raise AssertionError("START_PAUSE_WINDOW_S is not declared")
        game = SimpleNamespace(
            GetPlayer=lambda: self.player,
            IsDedicatedServer=lambda: self.is_dedicated_server,
            GetMission=lambda: self.mission,
            GetUIManager=lambda: self.ui,
        )
        self.ns: dict[str, Any] = {
            "this": self.state,
            "_S": _WireString,
            "GetGame": lambda: game if self.has_game else None,
            "g_Game": SimpleNamespace(
                IsClient=lambda: self.is_client,
                GetGameState=lambda: self.game_state,
            ),
            "DayZGameState": SimpleNamespace(IN_GAME=DAYZ_GAME_STATE_IN_GAME),
            "MENU_INGAME": MENU_INGAME_ID,
            "MissionGameplay": SimpleNamespace(
                Cast=lambda value: value if isinstance(value, _MissionGameplay) else None
            ),
            "PlayerBase": SimpleNamespace(
                Cast=lambda value: value if isinstance(value, _Player) else None
            ),
            "PostResult": self.posted.append,
            "Log": lambda msg: self.logs.append(
                msg.text if isinstance(msg, EnforceString) else str(msg)
            ),
            **constants,
        }
        for cls_name in ("MCPCommand", "MCPResult", "MCPJob", "MCPUiPause"):
            fake_class(messages, cls_name, self.ns)

        for signature in (
            DISPATCH_CHAIN,
            IN_GAME,
            CONTINUE_START_PAUSE,
            *VERB_METHODS,
            *OWNER_HOOKS,
        ):
            translate(
                _free_method(client, signature),
                signature,
                self.ns,
                class_types=LOCAL_CLASS_TYPES,
            )

        raw_dispatch_ui_pause = self.ns["DispatchUiPause"]

        def tracked_dispatch_ui_pause(command: Any, result: Any) -> Any:
            self.handled.append(command.cmd.text)
            return raw_dispatch_ui_pause(command, result)

        self.ns["DispatchUiPause"] = tracked_dispatch_ui_pause
        hook_names = [signature[: signature.index("(")].split()[-1] for signature in OWNER_HOOKS]
        self.hook_calls: list[str] = []

        def wrap_hook(name: str):
            fn = self.ns[name]

            def call(job: Any) -> Any:
                self.hook_calls.append(name)
                return fn(job)

            return call

        self.owner = SimpleNamespace(**{name: wrap_hook(name) for name in hook_names})

    def dispatch(self, cmd_name: str = COMMAND, command_id: int = 7) -> None:
        command = self.ns["MCPCommand"]()
        command.id = command_id
        command.cmd = _WireString.of(cmd_name)
        self.ns["Dispatch"](command)

    def on_update(self, timeslice: float) -> None:
        """Run modded MissionGameplay.OnUpdate order: super.OnUpdate (draining m_PauseQueued), then bridge OnTick."""
        if isinstance(self.mission, _MissionGameplay):
            if self.mission.m_PauseQueued and not self.mission.IsPaused():
                self.mission.Pause()
        self.runner.Tick(timeslice, self.owner)
        self.ns["ContinueStartPauseMenu"]()

    def replies(self) -> list[tuple[int, bool, str, tuple[int, int] | None]]:
        out: list[tuple[int, bool, str, tuple[int, int] | None]] = []
        for reply in self.posted:
            pair = (
                None
                if reply.ui_pause is None
                else (reply.ui_pause.menu_id_before, reply.ui_pause.menu_id_after)
            )
            out.append((reply.id, reply.ok, reply.error.text, pair))
        return out


class UiPauseCensusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(CLIENT_PATH)

    def test_client_caps_and_dispatch_name_ui_pause(self) -> None:
        self.assertIn(COMMAND, announced_caps(self.source))
        names = dispatch_census(self.source)
        self.assertIn(COMMAND, names)
        self.assertEqual(names[-1], "ui_dialog")
        dispatch = _method_body(self.source, DISPATCH_CHAIN)
        branch = _if_body(dispatch, f'command.cmd == "{COMMAND}"')
        self.assertEqual(branch.strip(), "postNow = DispatchUiPause(command, result);")

    def test_server_bridge_does_not_announce_or_implement_ui_pause(self) -> None:
        self.assertNotIn(COMMAND, _source(SERVER_BRIDGE_PATH))
        fixture = json.loads(CENSUS_FIXTURE.read_text(encoding="utf-8"))
        self.assertNotIn(COMMAND, fixture["peers"]["server"])
        self.assertEqual(fixture["peers"]["client"].get(COMMAND), COMMAND)
        self.assertNotIn(COMMAND, _BRIDGE_COMMAND_TOOLS["server"])
        self.assertEqual(_BRIDGE_COMMAND_TOOLS["client"].get(COMMAND), COMMAND)
        self.assertNotIn(COMMAND, server.SERVER_ARG_CONTRACT)

    def test_stale_client_census_flags_ui_pause_missing(self) -> None:
        registered = frozenset(
            tool for tool in _BRIDGE_COMMAND_TOOLS["client"].values() if tool
        )
        announced = sorted(_BRIDGE_COMMAND_TOOLS["client"])
        announced.remove(COMMAND)
        result = server._compare_bridge_capabilities(
            "client",
            {"state": "announced", "reason": "ok", "announced_commands": announced},
            registered,
        )
        self.assertEqual(result["state"], "mismatch")
        self.assertEqual(result["registered_without_announced_command"], [COMMAND])


class UiPauseEnforceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(CLIENT_PATH)
        self.dispatch = _method_body(self.source, DISPATCH_UI_PAUSE)
        self.process = _method_body(self.source, PROCESS_UI_PAUSE)
        self.post = _method_body(self.source, POST_UI_PAUSE)

    def test_pause_call_extractor_distinguishes_methods_from_constructors(self) -> None:
        self.assertEqual(
            _pause_method_calls("reply = new MCPUiPause();\nmission.Pause();"),
            ["mission.Pause()"],
        )
        self.assertEqual(
            _pause_method_calls("x = new Pause();\nmission.Pause();"),
            ["mission.Pause()"],
        )
        self.assertEqual(_pause_method_calls('Log("mission.Pause()");'), [])
        self.assertEqual(_pause_method_calls("Pause();"), ["Pause()"])
        self.assertEqual(_pause_method_calls("other.Pause();"), ["other.Pause()"])

    def test_pause_runs_once_after_local_player_and_mission_cast_guards(self) -> None:
        assert_dispatch_ui_pause_contract(self.dispatch, self.process, self.post)
        self.assertEqual(
            _locals_declared_at_top(self.dispatch),
            [
                "mission",
                "player",
                "ui",
                "menuBefore",
                "menuAfter",
                "reply",
                "job",
                "beforeId",
                "afterId",
                "queuedBefore",
                "deferred",
            ],
        )
        self.assertEqual(
            _locals_declared_at_top(self.process),
            [],
        )
        self.assertEqual(
            _locals_declared_at_top(self.post),
            ["result", "reply", "mission", "ui", "menuAfter", "beforeId", "afterId"],
        )
        for signature, body in (
            (DISPATCH_UI_PAUSE, self.dispatch),
            (PROCESS_UI_PAUSE, self.process),
            (POST_UI_PAUSE, self.post),
        ):
            with self.subTest(method=signature):
                for line in body.splitlines():
                    if line:
                        self.assertTrue(line.startswith("\t"), f"non-tab indentation: {line!r}")

        process_hook = _method_body(self.source, PROCESS_HOOK)
        self.assertEqual(
            _if_body(process_hook, f'job.kind == "{COMMAND}"').strip(),
            "return ProcessUiPauseJob(job);",
        )
        success_hook = _method_body(self.source, SUCCESS_HOOK)
        self.assertEqual(
            _if_body(success_hook, f'job.kind == "{COMMAND}"').strip(),
            "PostUiPauseJob(job);",
        )
        failure_hook = _method_body(self.source, FAILURE_HOOK)
        self.assertEqual(
            _if_body(failure_hook, f'job && job.kind == "{COMMAND}"').strip(),
            "PostUiPauseJob(job);\n\t\t\treturn;",
        )
        timeout_hook = _method_body(self.source, TIMEOUT_HOOK)
        self.assertEqual(
            _if_body(timeout_hook, f'job.kind == "{COMMAND}"').strip(),
            "PostUiPauseJob(job);\n\t\t\treturn;",
        )

    def test_negative_controls_without_guards_or_wrong_pause_count_fail(self) -> None:
        assert_dispatch_ui_pause_contract(self.dispatch, self.process, self.post)
        mutants = (
            (
                "missing_local_player_guard",
                self.dispatch.replace(LOCAL_PLAYER_GUARD, "", 1),
                r"local-player / dedicated-server guard",
            ),
            (
                "missing_get_player_check",
                self.dispatch.replace(" || !GetGame().GetPlayer()", "", 1),
                r"local-player / dedicated-server guard",
            ),
            (
                "local_player_guard_without_return",
                self.dispatch.replace(
                    '\t\t\tLog("ui_pause before=-1 after=-1 ok=0 error=client_not_in_game");\n\t\t\treturn true;',
                    '\t\t\tLog("ui_pause before=-1 after=-1 ok=0 error=client_not_in_game");',
                    1,
                ),
                r"local-player / dedicated-server guard",
            ),
            (
                "local_player_guard_without_log",
                self.dispatch.replace(
                    '\t\t\tLog("ui_pause before=-1 after=-1 ok=0 error=client_not_in_game");\n',
                    "",
                    1,
                ),
                r"local-player / dedicated-server guard",
            ),
            (
                "missing_mission_cast_guard",
                self.dispatch.replace(MISSION_CAST_GUARD, "", 1),
                r"MissionGameplay\.Cast guard",
            ),
            (
                "mission_guard_without_return",
                self.dispatch.replace(
                    '\t\t\tLog("ui_pause before=-1 after=-1 ok=0 error=no_mission");\n\t\t\treturn true;',
                    '\t\t\tLog("ui_pause before=-1 after=-1 ok=0 error=no_mission");',
                    1,
                ),
                r"MissionGameplay\.Cast guard",
            ),
            (
                "mission_guard_without_log",
                self.dispatch.replace(
                    '\t\t\tLog("ui_pause before=-1 after=-1 ok=0 error=no_mission");\n',
                    "",
                    1,
                ),
                r"MissionGameplay\.Cast guard",
            ),
            (
                "pause_before_guards",
                "\n\t\tmission.Pause();\n" + self.dispatch.replace("\t\tmission.Pause();\n", "", 1),
                r"steps out of order|before MissionGameplay\.Cast guard",
            ),
            (
                "pause_removed",
                self.dispatch.replace("\t\tmission.Pause();\n", "", 1),
                r"expected exactly one mission\.Pause\(\) call",
            ),
            (
                "pause_called_twice",
                self.dispatch.replace(
                    "\t\tmission.Pause();\n",
                    "\t\tmission.Pause();\n\t\tmission.Pause();\n",
                    1,
                ),
                r"expected exactly one mission\.Pause\(\) call",
            ),
            (
                "bare_pause_added",
                self.dispatch.replace(
                    "\t\tmission.Pause();\n",
                    "\t\tmission.Pause();\n\t\tPause();\n",
                    1,
                ),
                r"expected exactly one mission\.Pause\(\) call",
            ),
            (
                "deferred_without_return_false",
                self.dispatch.replace("\t\t\treturn false;", "\t\t\treturn true;", 1),
                r"missing DispatchUiPause step: 'return false;'",
            ),
            (
                "sync_branch_without_return_true",
                self.dispatch.replace("\n\t\treturn true;", "\n\t\treturn false;", 1),
                r"missing DispatchUiPause step:.*return true;",
            ),
            (
                "missing_immediate_after_read",
                self.dispatch.replace("\t\tafterId = -1;\n", "", 1),
                r"missing DispatchUiPause step: 'afterId = -1;'",
            ),
        )
        for label, mutant, pattern in mutants:
            with self.subTest(mutant=label):
                self.assertNotEqual(mutant, self.dispatch)
                with self.assertRaisesRegex(AssertionError, pattern):
                    assert_dispatch_ui_pause_contract(mutant, self.process, self.post)

        proc_mutants = (
            (
                "process_without_next_tick_check",
                self.process.replace(
                    "return m_JobRunner.GetElapsedS() > job.sample_start_s;",
                    "return true;",
                    1,
                ),
                r"missing ProcessUiPauseJob next-tick check",
            ),
            (
                "process_waits_on_is_player_loaded",
                self.process.replace(
                    "return m_JobRunner.GetElapsedS() > job.sample_start_s;",
                    "if (!player.IsPlayerLoaded()) { return false; }\n"
                    "\t\treturn m_JobRunner.GetElapsedS() > job.sample_start_s;",
                    1,
                ),
                r"ProcessUiPauseJob must read on the next tick instead of waiting on IsPlayerLoaded",
            ),
            (
                "process_calls_pause",
                self.process.replace(
                    "return m_JobRunner.GetElapsedS() > job.sample_start_s;",
                    "mission.Pause();\n\t\treturn m_JobRunner.GetElapsedS() > job.sample_start_s;",
                    1,
                ),
                r"ProcessUiPauseJob must not call Pause\(\) directly",
            ),
        )
        for label, proc_mutant, pattern in proc_mutants:
            with self.subTest(mutant=label):
                self.assertNotEqual(proc_mutant, self.process)
                with self.assertRaisesRegex(AssertionError, pattern):
                    assert_dispatch_ui_pause_contract(self.dispatch, proc_mutant, self.post)

    def test_message_class_and_result_prune_wiring(self) -> None:
        messages = _source(MESSAGES_PATH)
        ui_pause_cls = _class_body(messages, "MCPUiPause")
        self.assertRegex(ui_pause_cls, r"(?m)^\tint menu_id_before;$")
        self.assertRegex(ui_pause_cls, r"(?m)^\tint menu_id_after;$")
        mcp_result = _class_body(messages, "MCPResult")
        self.assertRegex(mcp_result, r"(?m)^\tref MCPUiPause ui_pause;$")
        mcp_job = _class_body(messages, "MCPJob")
        self.assertRegex(mcp_job, r"(?m)^\tref MCPUiPause ui_pause;$")
        self.assertIn("ui_pause", PRUNABLE_FIELDS)
        filled = {"ok": 1, "ui_pause": {"menu_id_before": -1, "menu_id_after": MENU_INGAME_ID}}
        self.assertEqual(
            prune_unfilled_fields(COMMAND, filled)["ui_pause"],
            {"menu_id_before": -1, "menu_id_after": MENU_INGAME_ID},
        )
        self.assertNotIn(
            "ui_pause",
            prune_unfilled_fields("gameplay_state", {"ok": 1, "ui_pause": None}),
        )

    def test_mcp_pause_queued_member_and_constructor_init(self) -> None:
        bridge_body = _class_body(
            _without_comments_or_strings(self.source), "MCPClientBridge"
        )
        self.assertEqual(
            len(re.findall(r"(?m)^\tprotected bool m_MCPPauseQueued;\s*$", bridge_body)),
            1,
        )
        ctor_body = re.sub(r"\s+", "", method_body(self.source, "void MCPClientBridge()"))
        self.assertIn("m_MCPPauseQueued=false;", ctor_body)


class UiPauseEnforceRunTest(unittest.TestCase):
    """Execute the real translated Enforce methods across sync, deferred, and refusal paths."""

    def test_early_refusal_paths_log_both_ids_and_error_without_calling_pause(self) -> None:
        # 1. Dispatch rejects before DispatchUiPause when GetPlayer() is null (!IsClientInGame()).
        no_player = _UiPauseClientModel()
        no_player.player = None
        no_player.dispatch(COMMAND, 7)
        self.assertEqual(no_player.handled, [])
        self.assertEqual(no_player.replies(), [(7, False, "client_not_in_game", None)])
        self.assertEqual(
            no_player.logs,
            ["ui_pause before=-1 after=-1 ok=0 error=client_not_in_game"],
        )

        # Unrelated commands rejected by !IsClientInGame() do not emit ui_pause logs.
        other = _UiPauseClientModel()
        other.player = None
        other.dispatch("camera_get", 8)
        self.assertEqual(other.replies(), [(8, False, "client_not_in_game", None)])
        self.assertEqual(other.logs, [])

        # 2. DispatchUiPause rejects on a dedicated server with Log and without calling Pause().
        dedicated = _UiPauseClientModel()
        dedicated.is_dedicated_server = True
        dedicated.dispatch(COMMAND, 9)
        self.assertEqual(dedicated.handled, [COMMAND])
        assert isinstance(dedicated.mission, _MissionGameplay)
        self.assertEqual(dedicated.mission.pause_calls, 0)
        self.assertEqual(dedicated.replies(), [(9, False, "client_not_in_game", None)])
        self.assertEqual(
            dedicated.logs,
            ["ui_pause before=-1 after=-1 ok=0 error=client_not_in_game"],
        )

        # 3. DispatchUiPause rejects when MissionGameplay.Cast fails, logging both IDs and no_mission.
        no_mission = _UiPauseClientModel()
        no_mission.mission = object()
        no_mission.dispatch(COMMAND, 10)
        self.assertEqual(no_mission.handled, [COMMAND])
        self.assertEqual(no_mission.replies(), [(10, False, "no_mission", None)])
        self.assertEqual(
            no_mission.logs,
            ["ui_pause before=-1 after=-1 ok=0 error=no_mission"],
        )

        # Negative controls: dropping either early-refusal Log is caught at runtime.
        source = _source(CLIENT_PATH)
        no_dispatch_log = _mutate(
            source,
            DISPATCH_CHAIN,
            '\t\t\t\tLog("ui_pause before=-1 after=-1 ok=0 error=client_not_in_game");\n',
            "",
        )
        m1 = _UiPauseClientModel(client=no_dispatch_log)
        m1.player = None
        m1.dispatch(COMMAND, 11)
        self.assertEqual(m1.logs, [])

        no_mission_log = _mutate(
            source,
            DISPATCH_UI_PAUSE,
            '\t\t\tLog("ui_pause before=-1 after=-1 ok=0 error=no_mission");\n',
            "",
        )
        m2 = _UiPauseClientModel(client=no_mission_log)
        m2.mission = object()
        m2.dispatch(COMMAND, 12)
        self.assertEqual(m2.logs, [])

    def test_synchronous_branches_reply_immediately_without_queuing_job_or_losing_esc_race(
        self,
    ) -> None:
        # From gameplay (-1 -> 9), from another non-pause menu (15 -> 9), and idempotent (9 -> 9):
        # Pause() completes in the same call, so DispatchUiPause reads menu_id_after immediately,
        # posts the reply synchronously without queuing an MCPJob, and survives an Esc close right after.
        for before_menu, expected_before in (
            (None, -1),
            (_Menu(15), 15),
            (_Menu(MENU_INGAME_ID), MENU_INGAME_ID),
        ):
            with self.subTest(before=expected_before):
                model = _UiPauseClientModel()
                model.ui.top = before_menu
                model.dispatch(COMMAND, 21)
                self.assertEqual(model.jobs.Count(), 0)
                self.assertEqual(
                    model.replies(),
                    [(21, True, "", (expected_before, MENU_INGAME_ID))],
                )
                self.assertEqual(
                    model.logs,
                    [f"ui_pause before={expected_before} after={MENU_INGAME_ID} ok=1"],
                )
                # Player presses Esc (closing MENU_INGAME) before the next runner tick:
                # the immediate reply already recorded (expected_before, 9), ok=true.
                model.ui.CloseAll()
                model.on_update(0.05)
                self.assertEqual(
                    model.replies(),
                    [(21, True, "", (expected_before, MENU_INGAME_ID))],
                )
                self.assertEqual(len(model.logs), 1)

        # Even if !player.IsPlayerLoaded() holds while MENU_INGAME is already open on top,
        # Pause() returns synchronously at :1301-1304 before :1306-1317, so no job is queued.
        already_paused_unloaded = _UiPauseClientModel()
        already_paused_unloaded.player = _Player(loaded=False)
        already_paused_unloaded.ui.top = _Menu(MENU_INGAME_ID)
        already_paused_unloaded.dispatch(COMMAND, 22)
        self.assertEqual(already_paused_unloaded.jobs.Count(), 0)
        self.assertEqual(
            already_paused_unloaded.replies(),
            [(22, True, "", (MENU_INGAME_ID, MENU_INGAME_ID))],
        )

        # EnterScriptedMenu failure on a loaded in-game player is also synchronous (no deferral guard).
        invisible = _UiPauseClientModel()
        invisible.ui.enter_visible = False
        invisible.dispatch(COMMAND, 23)
        self.assertEqual(invisible.jobs.Count(), 0)
        self.assertEqual(invisible.replies(), [(23, False, "pause_not_opened", (-1, -1))])
        self.assertEqual(
            invisible.logs,
            [
                "ui_pause before=-1 after=-1 ok=0 error=pause_not_opened",
                "ui_pause queue cancelled id=23",
            ],
        )

        # Negative control (review-r2): unconditionally deferring onto m_JobRunner when
        # MENU_INGAME is already open loses the race if the player closes the menu via Esc
        # before the runner reads menu_id_after, wrongly answering pause_not_opened (9, -1).
        always_deferred_src = _mutate(
            _source(CLIENT_PATH),
            DISPATCH_UI_PAUSE,
            "\t\tdeferred = false;\n",
            "\t\tdeferred = true;\n",
        )
        always_deferred = _UiPauseClientModel(client=always_deferred_src)
        always_deferred.ui.top = _Menu(MENU_INGAME_ID)
        always_deferred.dispatch(COMMAND, 24)
        self.assertEqual(always_deferred.replies(), [])
        always_deferred.ui.CloseAll()
        always_deferred.on_update(0.02)
        always_deferred.on_update(0.03)
        self.assertEqual(
            always_deferred.replies(),
            [(24, False, "pause_not_opened", (MENU_INGAME_ID, -1))],
        )

    def test_deferred_branches_read_menu_id_after_on_next_tick(self) -> None:
        # 1. Local player exists in-game, but IsPlayerLoaded() stays false across updates:
        # Pause() sets m_PauseQueued=true without opening MENU_INGAME (:1306, :1314-1317).
        # DispatchUiPause queues an MCPJob, ignores a zero-timeslice OnMissionStart tick,
        # and reads menu_id_after on the very next positive-timeslice tick (16 ms), answering
        # pause_not_opened (-1, -1) instead of waiting until loading ends or deadline_s expires.
        still_loading = _UiPauseClientModel()
        still_loading.player = _Player(loaded=False)
        still_loading.dispatch(COMMAND, 31)
        assert isinstance(still_loading.mission, _MissionGameplay)
        self.assertEqual(still_loading.jobs.Count(), 1)
        self.assertTrue(still_loading.mission.m_PauseQueued)
        self.assertIsNone(still_loading.ui.GetMenu())
        self.assertEqual(still_loading.replies(), [])
        self.assertEqual(still_loading.logs, [])

        still_loading.on_update(0.0)
        self.assertEqual(still_loading.jobs.Count(), 1)
        self.assertEqual(still_loading.replies(), [])

        still_loading.on_update(0.016)
        self.assertEqual(still_loading.jobs.Count(), 0)
        self.assertFalse(still_loading.mission.m_PauseQueued)
        self.assertIsNone(still_loading.ui.GetMenu())
        self.assertEqual(
            still_loading.replies(),
            [(31, False, "pause_not_opened", (-1, -1))],
        )
        self.assertEqual(
            still_loading.logs,
            [
                "ui_pause before=-1 after=-1 ok=0 error=pause_not_opened",
                "ui_pause queue cancelled id=31",
            ],
        )
        for _ in range(6):
            still_loading.on_update(0.05)
        self.assertEqual(len(still_loading.replies()), 1)
        self.assertEqual(len(still_loading.logs), 2)

        # Same next-tick pause_not_opened read-back while respawning or pre-IN_GAME persists.
        still_respawning = _UiPauseClientModel()
        assert isinstance(still_respawning.mission, _MissionGameplay)
        still_respawning.mission.respawning = True
        still_respawning.dispatch(COMMAND, 32)
        self.assertEqual(still_respawning.jobs.Count(), 1)
        self.assertEqual(still_respawning.replies(), [])
        still_respawning.on_update(0.016)
        self.assertEqual(still_respawning.jobs.Count(), 0)
        self.assertEqual(
            still_respawning.replies(),
            [(32, False, "pause_not_opened", (-1, -1))],
        )

        still_connecting = _UiPauseClientModel()
        still_connecting.game_state = 2
        still_connecting.dispatch(COMMAND, 33)
        self.assertEqual(still_connecting.jobs.Count(), 1)
        self.assertEqual(still_connecting.replies(), [])
        still_connecting.on_update(0.016)
        self.assertEqual(still_connecting.jobs.Count(), 0)
        self.assertEqual(
            still_connecting.replies(),
            [(33, False, "pause_not_opened", (-1, -1))],
        )

        # 2. When the deferral condition clears before the next MissionGameplay.OnUpdate,
        # super.OnUpdate drains m_PauseQueued (:308-311) ahead of bridge.OnTick (MissionGameplay.c:25-30),
        # so that same single 16 ms next tick reads MENU_INGAME and answers ok=true.
        loaded_by_next_tick = _UiPauseClientModel()
        player = _Player(loaded=False)
        loaded_by_next_tick.player = player
        loaded_by_next_tick.dispatch(COMMAND, 34)
        assert isinstance(loaded_by_next_tick.mission, _MissionGameplay)
        self.assertEqual(loaded_by_next_tick.jobs.Count(), 1)
        self.assertEqual(loaded_by_next_tick.replies(), [])
        player.loaded = True
        loaded_by_next_tick.on_update(0.016)
        self.assertFalse(loaded_by_next_tick.mission.m_PauseQueued)
        self.assertEqual(loaded_by_next_tick.jobs.Count(), 0)
        self.assertEqual(
            loaded_by_next_tick.replies(),
            [(34, True, "", (-1, MENU_INGAME_ID))],
        )
        self.assertEqual(
            loaded_by_next_tick.logs,
            [f"ui_pause before=-1 after={MENU_INGAME_ID} ok=1"],
        )

        respawn_cleared = _UiPauseClientModel()
        assert isinstance(respawn_cleared.mission, _MissionGameplay)
        respawn_cleared.mission.respawning = True
        respawn_cleared.dispatch(COMMAND, 35)
        self.assertEqual(respawn_cleared.jobs.Count(), 1)
        respawn_cleared.mission.respawning = False
        respawn_cleared.on_update(0.016)
        self.assertEqual(
            respawn_cleared.replies(),
            [(35, True, "", (-1, MENU_INGAME_ID))],
        )

        in_game_reached = _UiPauseClientModel()
        in_game_reached.game_state = 2
        in_game_reached.dispatch(COMMAND, 36)
        self.assertEqual(in_game_reached.jobs.Count(), 1)
        in_game_reached.game_state = DAYZ_GAME_STATE_IN_GAME
        in_game_reached.on_update(0.016)
        self.assertEqual(
            in_game_reached.replies(),
            [(36, True, "", (-1, MENU_INGAME_ID))],
        )

        # 3. Runtime negative controls:
        # (a) Waiting on !player.IsPlayerLoaded() in ProcessUiPauseJob (review-r3) fails to
        # answer at 16 ms or 300 ms and stalls until the 5 s job deadline.
        wait_mutant_src = _mutate(
            _source(CLIENT_PATH),
            PROCESS_UI_PAUSE,
            "\t\tif (!job)\n",
            "\t\tPlayerBase player;\n\t\tif (!job)\n",
        )
        wait_mutant_src = _mutate(
            wait_mutant_src,
            PROCESS_UI_PAUSE,
            "\t\treturn m_JobRunner.GetElapsedS() > job.sample_start_s;\n",
            "\t\tif (m_JobRunner.GetElapsedS() <= job.sample_start_s)\n"
            "\t\t{\n"
            "\t\t\treturn false;\n"
            "\t\t}\n"
            "\t\tplayer = PlayerBase.Cast(GetGame().GetPlayer());\n"
            "\t\tif (player && !player.IsPlayerLoaded())\n"
            "\t\t{\n"
            "\t\t\treturn false;\n"
            "\t\t}\n"
            "\t\treturn true;\n",
        )
        wait_mutant = _UiPauseClientModel(client=wait_mutant_src)
        wait_mutant.player = _Player(loaded=False)
        wait_mutant.dispatch(COMMAND, 37)
        wait_mutant.on_update(0.016)
        self.assertEqual(wait_mutant.replies(), [])
        wait_mutant.on_update(0.284)
        self.assertEqual(wait_mutant.replies(), [])
        wait_mutant.on_update(4.701)
        self.assertEqual(
            wait_mutant.replies(),
            [(37, False, "pause_not_opened", (-1, -1))],
        )

        # (b) Ignoring sample_start_s prematurely finishes on a zero-timeslice OnMissionStart tick.
        zero_tick_src = _mutate(
            _source(CLIENT_PATH),
            PROCESS_UI_PAUSE,
            "\t\treturn m_JobRunner.GetElapsedS() > job.sample_start_s;\n",
            "\t\treturn true;\n",
        )
        zero_tick = _UiPauseClientModel(client=zero_tick_src)
        zero_tick.player = _Player(loaded=False)
        zero_tick.dispatch(COMMAND, 38)
        zero_tick.runner.Tick(0.0, zero_tick.owner)
        self.assertEqual(
            zero_tick.replies(),
            [(38, False, "pause_not_opened", (-1, -1))],
        )

    def test_submenu_timeout_and_failure_hooks_report_menu_ids_and_log(self) -> None:
        # Child submenu (e.g. Options=42) open above MENU_INGAME: IsPaused() is true, so Pause()
        # returns synchronously at :1301-1304 with 42 on top; DispatchUiPause replies immediately
        # without queuing a job.
        submenu = _UiPauseClientModel()
        submenu.ui.top = _Menu(42, parent=_Menu(MENU_INGAME_ID))
        submenu.dispatch(COMMAND, 41)
        self.assertEqual(submenu.jobs.Count(), 0)
        self.assertEqual(submenu.replies(), [(41, False, "pause_not_opened", (42, 42))])
        self.assertEqual(
            submenu.logs,
            ["ui_pause before=42 after=42 ok=0 error=pause_not_opened"],
        )

        # Synthetic expired job routes through MCP_PostJobTimeout -> PostUiPauseJob
        # and answers pause_not_opened with (before, -1) and a Log line.
        timed_out = _UiPauseClientModel()
        timeout_job = timed_out.ns["MCPJob"]()
        timeout_job.id = 42
        timeout_job.kind = _WireString.of(COMMAND)
        timeout_job.ui_pause = timed_out.ns["MCPUiPause"]()
        timeout_job.ui_pause.menu_id_before = -1
        timeout_job.sample_start_s = 10.0
        timeout_job.deadline_s = 5.0
        timed_out.runner.AddJob(timeout_job)
        timed_out.on_update(6.0)
        self.assertIn("MCP_PostJobTimeout", timed_out.hook_calls)
        self.assertEqual(timed_out.replies(), [(42, False, "pause_not_opened", (-1, -1))])
        self.assertEqual(
            timed_out.logs,
            ["ui_pause before=-1 after=-1 ok=0 error=pause_not_opened"],
        )

        # Synthetic job error routes through MCP_PostJobFailure -> PostUiPauseJob with Log.
        failed = _UiPauseClientModel()
        job = failed.ns["MCPJob"]()
        job.id = 43
        job.kind = _WireString.of(COMMAND)
        job.error = _WireString.of("synthetic_error")
        job.ui_pause = failed.ns["MCPUiPause"]()
        job.ui_pause.menu_id_before = 7
        job.sample_start_s = 0.0
        job.deadline_s = 5.0
        failed.runner.AddJob(job)
        failed.on_update(0.05)
        self.assertIn("MCP_PostJobFailure", failed.hook_calls)
        self.assertEqual(failed.replies(), [(43, False, "synthetic_error", (7, -1))])
        self.assertEqual(
            failed.logs,
            ["ui_pause before=7 after=-1 ok=0 error=synthetic_error"],
        )

    def test_case_a_unloaded_player_cancels_queued_pause_and_never_opens_on_load(self) -> None:
        model = _UiPauseClientModel()
        player = _Player(loaded=False)
        model.player = player
        assert isinstance(model.mission, _MissionGameplay)

        model.dispatch(COMMAND, 51)
        self.assertEqual(model.jobs.Count(), 1)
        self.assertTrue(model.mission.m_PauseQueued)
        self.assertTrue(model.state.m_MCPPauseQueued)
        self.assertEqual(model.replies(), [])

        model.on_update(0.25)
        self.assertEqual(model.jobs.Count(), 0)
        self.assertEqual(model.replies(), [(51, False, "pause_not_opened", (-1, -1))])
        self.assertFalse(model.mission.m_PauseQueued)
        self.assertFalse(model.state.m_MCPPauseQueued)
        self.assertEqual(
            model.logs,
            [
                "ui_pause before=-1 after=-1 ok=0 error=pause_not_opened",
                "ui_pause queue cancelled id=51",
            ],
        )

        player.loaded = True
        for _ in range(10):
            model.on_update(0.25)
        self.assertEqual(model.ui.ingame_openings, 0)
        self.assertIsNone(model.ui.GetMenu())
        self.assertEqual(model.mission.continue_calls, 0)
        self.assertEqual(
            model.logs,
            [
                "ui_pause before=-1 after=-1 ok=0 error=pause_not_opened",
                "ui_pause queue cancelled id=51",
            ],
        )

    def test_case_b_respawning_player_cancels_queued_pause_and_never_opens_after_respawn(
        self,
    ) -> None:
        model = _UiPauseClientModel()
        assert isinstance(model.mission, _MissionGameplay)
        model.mission.respawning = True

        model.dispatch(COMMAND, 52)
        self.assertEqual(model.jobs.Count(), 1)
        self.assertTrue(model.mission.m_PauseQueued)
        self.assertTrue(model.state.m_MCPPauseQueued)
        self.assertEqual(model.replies(), [])

        model.on_update(0.25)
        self.assertEqual(model.jobs.Count(), 0)
        self.assertEqual(model.replies(), [(52, False, "pause_not_opened", (-1, -1))])
        self.assertFalse(model.mission.m_PauseQueued)
        self.assertFalse(model.state.m_MCPPauseQueued)
        self.assertEqual(
            model.logs,
            [
                "ui_pause before=-1 after=-1 ok=0 error=pause_not_opened",
                "ui_pause queue cancelled id=52",
            ],
        )

        model.mission.respawning = False
        for _ in range(10):
            model.on_update(0.25)
        self.assertEqual(model.ui.ingame_openings, 0)
        self.assertIsNone(model.ui.GetMenu())
        self.assertEqual(model.mission.continue_calls, 0)
        self.assertEqual(
            model.logs,
            [
                "ui_pause before=-1 after=-1 ok=0 error=pause_not_opened",
                "ui_pause queue cancelled id=52",
            ],
        )

    def test_case_c_deferred_job_failure_and_timeout_cancel_queued_pause(self) -> None:
        # 1. Deferred job finishes with job.error != "" via MCP_PostJobFailure.
        failed = _UiPauseClientModel()
        failed_player = _Player(loaded=False)
        failed.player = failed_player
        assert isinstance(failed.mission, _MissionGameplay)
        failed.dispatch(COMMAND, 53)
        self.assertEqual(failed.jobs.Count(), 1)
        self.assertTrue(failed.mission.m_PauseQueued)
        self.assertTrue(failed.state.m_MCPPauseQueued)
        failed_job = failed.jobs.GetElement(0)
        failed_job.error = _WireString.of("synthetic_failure")
        failed.on_update(0.25)
        self.assertIn("MCP_PostJobFailure", failed.hook_calls)
        self.assertEqual(failed.replies(), [(53, False, "synthetic_failure", (-1, -1))])
        self.assertFalse(failed.mission.m_PauseQueued)
        self.assertFalse(failed.state.m_MCPPauseQueued)
        self.assertEqual(
            failed.logs,
            [
                "ui_pause before=-1 after=-1 ok=0 error=synthetic_failure",
                "ui_pause queue cancelled id=53",
            ],
        )
        failed_player.loaded = True
        for _ in range(10):
            failed.on_update(0.25)
        self.assertEqual(failed.ui.ingame_openings, 0)
        self.assertIsNone(failed.ui.GetMenu())
        self.assertEqual(failed.mission.continue_calls, 0)
        self.assertEqual(len(failed.logs), 2)

        # 2. Deferred job expires past deadline_s with job.error != "" via MCP_PostJobTimeout.
        timeout_err = _UiPauseClientModel()
        timeout_err_player = _Player(loaded=False)
        timeout_err.player = timeout_err_player
        assert isinstance(timeout_err.mission, _MissionGameplay)
        timeout_err.dispatch(COMMAND, 54)
        timeout_err_job = timeout_err.jobs.GetElement(0)
        timeout_err_job.sample_start_s = 10.0
        timeout_err_job.error = _WireString.of("synthetic_timeout")
        timeout_err.on_update(6.0)
        self.assertIn("MCP_PostJobTimeout", timeout_err.hook_calls)
        self.assertEqual(timeout_err.replies(), [(54, False, "synthetic_timeout", (-1, -1))])
        self.assertFalse(timeout_err.mission.m_PauseQueued)
        self.assertFalse(timeout_err.state.m_MCPPauseQueued)
        self.assertEqual(
            timeout_err.logs,
            [
                "ui_pause before=-1 after=-1 ok=0 error=synthetic_timeout",
                "ui_pause queue cancelled id=54",
            ],
        )
        timeout_err_player.loaded = True
        for _ in range(10):
            timeout_err.on_update(0.25)
        self.assertEqual(timeout_err.ui.ingame_openings, 0)
        self.assertIsNone(timeout_err.ui.GetMenu())
        self.assertEqual(timeout_err.mission.continue_calls, 0)
        self.assertEqual(len(timeout_err.logs), 2)

        # 3. Deferred job expires past deadline_s with empty job.error via MCP_PostJobTimeout.
        timeout_plain = _UiPauseClientModel()
        timeout_plain_player = _Player(loaded=False)
        timeout_plain.player = timeout_plain_player
        assert isinstance(timeout_plain.mission, _MissionGameplay)
        timeout_plain.dispatch(COMMAND, 55)
        timeout_plain_job = timeout_plain.jobs.GetElement(0)
        timeout_plain_job.sample_start_s = 10.0
        timeout_plain.on_update(6.0)
        self.assertIn("MCP_PostJobTimeout", timeout_plain.hook_calls)
        self.assertEqual(timeout_plain.replies(), [(55, False, "pause_not_opened", (-1, -1))])
        self.assertFalse(timeout_plain.mission.m_PauseQueued)
        self.assertFalse(timeout_plain.state.m_MCPPauseQueued)
        self.assertEqual(
            timeout_plain.logs,
            [
                "ui_pause before=-1 after=-1 ok=0 error=pause_not_opened",
                "ui_pause queue cancelled id=55",
            ],
        )
        timeout_plain_player.loaded = True
        for _ in range(10):
            timeout_plain.on_update(0.25)
        self.assertEqual(timeout_plain.ui.ingame_openings, 0)
        self.assertIsNone(timeout_plain.ui.GetMenu())
        self.assertEqual(timeout_plain.mission.continue_calls, 0)
        self.assertEqual(len(timeout_plain.logs), 2)

    def test_case_d_preexisting_vanilla_queue_is_preserved_and_opens_on_load(self) -> None:
        model = _UiPauseClientModel()
        player = _Player(loaded=False)
        model.player = player
        assert isinstance(model.mission, _MissionGameplay)
        model.mission.m_PauseQueued = True

        model.dispatch(COMMAND, 56)
        self.assertEqual(model.jobs.Count(), 1)
        self.assertTrue(model.mission.m_PauseQueued)
        self.assertFalse(model.state.m_MCPPauseQueued)

        model.on_update(0.25)
        self.assertEqual(model.jobs.Count(), 0)
        self.assertEqual(model.replies(), [(56, False, "pause_not_opened", (-1, -1))])
        self.assertTrue(model.mission.m_PauseQueued)
        self.assertFalse(model.state.m_MCPPauseQueued)
        self.assertEqual(
            model.logs,
            ["ui_pause before=-1 after=-1 ok=0 error=pause_not_opened"],
        )

        player.loaded = True
        model.on_update(0.5)
        self.assertFalse(model.mission.m_PauseQueued)
        self.assertEqual(model.ui.ingame_openings, 1)
        self.assertEqual(model.mission.continue_calls, 1)
        self.assertIsNone(model.ui.GetMenu())
        self.assertEqual(
            model.logs,
            [
                "ui_pause before=-1 after=-1 ok=0 error=pause_not_opened",
                "start pause menu closed elapsed_s=0.75 from_s=0.25",
            ],
        )

        # Synchronous pause_not_opened also preserves a queue that was already armed beforehand.
        sync_prequeued = _UiPauseClientModel()
        assert isinstance(sync_prequeued.mission, _MissionGameplay)
        sync_prequeued.mission.m_PauseQueued = True
        sync_prequeued.ui.enter_visible = False
        sync_prequeued.dispatch(COMMAND, 57)
        self.assertEqual(sync_prequeued.replies(), [(57, False, "pause_not_opened", (-1, -1))])
        self.assertTrue(sync_prequeued.mission.m_PauseQueued)
        self.assertFalse(sync_prequeued.state.m_MCPPauseQueued)
        self.assertEqual(
            sync_prequeued.logs,
            ["ui_pause before=-1 after=-1 ok=0 error=pause_not_opened"],
        )

    def test_case_e_deferred_pause_opening_on_next_tick_replies_ok_without_cancel_or_start_close(
        self,
    ) -> None:
        model = _UiPauseClientModel()
        player = _Player(loaded=False)
        model.player = player
        assert isinstance(model.mission, _MissionGameplay)

        model.dispatch(COMMAND, 58)
        self.assertEqual(model.jobs.Count(), 1)
        self.assertTrue(model.mission.m_PauseQueued)
        self.assertTrue(model.state.m_MCPPauseQueued)

        player.loaded = True
        model.on_update(0.25)
        self.assertEqual(model.jobs.Count(), 0)
        self.assertEqual(model.replies(), [(58, True, "", (-1, MENU_INGAME_ID))])
        self.assertFalse(model.mission.m_PauseQueued)
        self.assertFalse(model.state.m_MCPPauseQueued)
        self.assertEqual(model.ui.ingame_openings, 1)
        top = model.ui.GetMenu()
        self.assertIsNotNone(top)
        assert top is not None
        self.assertEqual(top.GetID(), MENU_INGAME_ID)
        self.assertEqual(
            model.logs,
            [f"ui_pause before=-1 after={MENU_INGAME_ID} ok=1"],
        )

        for _ in range(5):
            model.on_update(0.25)
        top_after = model.ui.GetMenu()
        self.assertIsNotNone(top_after)
        assert top_after is not None
        self.assertEqual(top_after.GetID(), MENU_INGAME_ID)
        self.assertEqual(model.mission.continue_calls, 0)
        self.assertEqual(
            model.logs,
            [f"ui_pause before=-1 after={MENU_INGAME_ID} ok=1"],
        )

    def test_case_f_synchronous_ok_inside_start_window_keeps_menu_open_across_updates(
        self,
    ) -> None:
        model = _UiPauseClientModel()
        assert isinstance(model.mission, _MissionGameplay)
        model.on_update(0.25)

        model.dispatch(COMMAND, 59)
        self.assertEqual(model.jobs.Count(), 0)
        self.assertEqual(model.replies(), [(59, True, "", (-1, MENU_INGAME_ID))])
        self.assertFalse(model.state.m_MCPPauseQueued)
        top = model.ui.GetMenu()
        self.assertIsNotNone(top)
        assert top is not None
        self.assertEqual(top.GetID(), MENU_INGAME_ID)

        for _ in range(5):
            model.on_update(0.25)
        top_after = model.ui.GetMenu()
        self.assertIsNotNone(top_after)
        assert top_after is not None
        self.assertEqual(top_after.GetID(), MENU_INGAME_ID)
        self.assertEqual(model.mission.continue_calls, 0)
        self.assertEqual(
            model.logs,
            [f"ui_pause before=-1 after={MENU_INGAME_ID} ok=1"],
        )

    def test_case_g_without_ui_pause_start_window_closes_pause_once_as_before(self) -> None:
        model = _UiPauseClientModel()
        player = _Player(loaded=False)
        model.player = player
        assert isinstance(model.mission, _MissionGameplay)
        model.mission.m_PauseQueued = True

        model.on_update(0.25)
        self.assertTrue(model.mission.m_PauseQueued)
        self.assertEqual(model.logs, [])

        player.loaded = True
        model.on_update(0.5)
        self.assertFalse(model.mission.m_PauseQueued)
        self.assertEqual(model.ui.ingame_openings, 1)
        self.assertIsNone(model.ui.GetMenu())
        self.assertEqual(model.mission.continue_calls, 1)
        self.assertEqual(
            model.logs,
            ["start pause menu closed elapsed_s=0.75 from_s=0.25"],
        )

        # One-shot: a later non-MCP pause inside the 60 s window is left open.
        model.mission.Pause()
        model.on_update(1.0)
        top = model.ui.GetMenu()
        self.assertIsNotNone(top)
        assert top is not None
        self.assertEqual(top.GetID(), MENU_INGAME_ID)
        self.assertEqual(model.mission.continue_calls, 1)
        self.assertEqual(len(model.logs), 1)

    def test_overlapping_ui_pause_during_respawn_cancels_queue_once(self) -> None:
        model = _UiPauseClientModel()
        assert isinstance(model.mission, _MissionGameplay)
        model.mission.respawning = True

        model.dispatch(COMMAND, 61)
        model.dispatch(COMMAND, 62)
        self.assertEqual(model.jobs.Count(), 2)
        self.assertEqual(model.replies(), [])

        model.on_update(0.25)
        self.assertEqual(model.jobs.Count(), 0)
        self.assertEqual(
            sorted(model.replies()),
            [
                (61, False, "pause_not_opened", (-1, -1)),
                (62, False, "pause_not_opened", (-1, -1)),
            ],
        )

        model.mission.respawning = False
        for _ in range(10):
            model.on_update(0.25)
        self.assertEqual(model.ui.ingame_openings, 0)
        self.assertIsNone(model.ui.GetMenu())
        self.assertEqual(model.mission.continue_calls, 0)
        self.assertFalse(model.mission.m_PauseQueued)
        self.assertFalse(model.state.m_MCPPauseQueued)
        cancel_lines = [
            line for line in model.logs if line.startswith("ui_pause queue cancelled")
        ]
        self.assertEqual(len(cancel_lines), 1)
        self.assertRegex(cancel_lines[0], r"^ui_pause queue cancelled id=(61|62)$")
        self.assertEqual(
            model.logs.count("ui_pause before=-1 after=-1 ok=0 error=pause_not_opened"),
            2,
        )
        self.assertEqual(len(model.logs), 3)


class UiPauseIngressAndAdmissionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        bind_both_peers(self.state)
        self.caps = poll_census_query("client")["caps"].split(",")
        self.older = [name for name in self.caps if name != COMMAND]

    def _poll(self, caps: list[str]) -> dict[str, Any]:
        status, body = self.state.record_poll(
            "client",
            "12~1.30",
            instance=INST_CLIENT,
            source_pid=PID_CLIENT,
            caps=",".join(caps),
        )
        self.assertEqual(status, 200, body)
        return body

    def test_client_command_requires_lease_like_ui_click(self) -> None:
        self.assertIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertNotIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertNotIn(COMMAND, loopback.EXEC_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "client")
        self.assertNotIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertEqual(
            command_requires_lease(COMMAND),
            command_requires_lease("ui_click"),
        )
        self.assertTrue(command_requires_lease(COMMAND))

    def test_ingress_schema_accepts_empty_dict_and_rejects_unknown_keys(self) -> None:
        self.assertEqual(loopback.validate_command_args(COMMAND, {}), (True, None))
        for bad in (
            {"extra": 1},
            {"mode": "close"},
            {"path": "continuebtn"},
            {"timeout_s": 5.0},
        ):
            with self.subTest(args=bad):
                self.assertEqual(
                    loopback.validate_command_args(COMMAND, bad),
                    (False, "bad_args"),
                )
                self.assertEqual(
                    self.state.enqueue_command(COMMAND, bad),
                    (400, {"error": "bad_args"}),
                )
        self.assertEqual(bound_queue(self.state, "client"), [])

    def test_older_client_pbo_is_refused_and_current_client_receives_command(self) -> None:
        self.assertEqual(
            self.state.enqueue_command(COMMAND, {}),
            (409, {"error": "bridge_capability_missing"}),
        )
        self._poll(self.older)
        self.assertEqual(
            self.state.enqueue_command(COMMAND, {}),
            (409, {"error": "bridge_capability_missing"}),
        )
        self.assertEqual(bound_queue(self.state, "client"), [])

        self._poll(self.caps)
        status, body = self.state.enqueue_command(COMMAND, {})
        self.assertEqual((status, body["peer"], body["cmd"]), (200, "client", COMMAND))
        delivered = self._poll(self.caps)
        self.assertEqual(
            delivered["commands"],
            [{"id": body["id"], "cmd": COMMAND, "args": {}}],
        )


class UiPauseToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def _tool(self):
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        self.assertIn(COMMAND, tools)
        return tools[COMMAND]

    async def test_registered_tool_schema_and_description(self) -> None:
        tool = await self._tool()
        description = tool.description or ""
        self.assertTrue(description.startswith(LEASE_TOOL_LINE), description)
        self.assertIn("Esc", description)
        self.assertIn("ui_click", description)
        self.assertIn("continuebtn", description)
        schema = tool.inputSchema
        self.assertEqual(set(schema["properties"]), {"timeout_s"})
        self.assertEqual(schema.get("required") or [], [])

    async def test_tool_targets_client_and_returns_both_menu_ids_verbatim(self) -> None:
        bridge_reply = {
            "ok": 1,
            "ui_pause": {"menu_id_before": -1, "menu_id_after": MENU_INGAME_ID},
        }
        call = AsyncMock(return_value=bridge_reply)
        with patch.object(self.runtime, "call_bridge", new=call):
            raw = await self.app.call_tool(COMMAND, {"timeout_s": 3.5})
        call.assert_awaited_once_with(COMMAND, {}, "client", 3.5)
        self.assertEqual(_tool_json(raw), bridge_reply)

        call_default = AsyncMock(return_value=bridge_reply)
        with patch.object(self.runtime, "call_bridge", new=call_default):
            await self.app.call_tool(COMMAND, {})
        call_default.assert_awaited_once_with(
            COMMAND, {}, "client", DEFAULT_TOOL_TIMEOUT_S
        )

    async def test_full_runtime_round_trip_preserves_ids_and_propagates_pause_not_opened(self) -> None:
        state = loopback.ServerState("test-key")
        bind_both_peers(state)
        caps = poll_census_query("client")["caps"]
        state.record_poll(
            "client",
            "12~1.30",
            instance=INST_CLIENT,
            source_pid=PID_CLIENT,
            caps=caps,
        )
        self.runtime.loopback = SimpleNamespace(state=state)
        original_enqueue = state.enqueue_command
        fake_wire_result: dict[str, Any] = {}

        def enqueue_and_answer(*args: Any, **kwargs: Any) -> tuple[int, dict[str, Any]]:
            status, body = original_enqueue(*args, **kwargs)
            if status == 200:
                poll_status, poll_body = state.record_poll(
                    "client",
                    "12~1.30",
                    instance=INST_CLIENT,
                    source_pid=PID_CLIENT,
                    caps=caps,
                )
                self.assertEqual(poll_status, 200, poll_body)
                self.assertEqual(
                    poll_body["commands"],
                    [{"id": body["id"], "cmd": COMMAND, "args": {}}],
                )
                store_status, store_body = state.store_result(
                    {"id": body["id"], **fake_wire_result},
                    instance=INST_CLIENT,
                )
                self.assertEqual(store_status, 200, store_body)
            return status, body

        with patch.object(state, "enqueue_command", side_effect=enqueue_and_answer):
            fake_wire_result = {
                "ok": 1,
                "ui_pause": {"menu_id_before": -1, "menu_id_after": MENU_INGAME_ID},
                "gameplay": None,
            }
            ok_raw = await self.app.call_tool(COMMAND, {})
            ok_payload = _tool_json(ok_raw)
            self.assertEqual(ok_payload["ok"], 1)
            self.assertEqual(
                ok_payload["ui_pause"],
                {"menu_id_before": -1, "menu_id_after": MENU_INGAME_ID},
            )
            self.assertNotIn("gameplay", ok_payload)

            for error_code in ("pause_not_opened", "no_mission", "client_not_in_game"):
                with self.subTest(error=error_code):
                    fake_wire_result = {
                        "ok": 0,
                        "error": error_code,
                        "ui_pause": {"menu_id_before": 10, "menu_id_after": 10},
                    }
                    with self.assertRaises(ToolError) as raised:
                        await self.app.call_tool(COMMAND, {})
                    self.assertEqual(_tool_error_text(raised.exception), error_code)
                    self.assertEqual(
                        str(server._bridge_error(fake_wire_result, COMMAND)),
                        error_code,
                    )


if __name__ == "__main__":
    unittest.main()
