"""Inbox fb-20261112-153815-d4b4: ui_menu_set opens and closes the 2D map
without a key press, and ui_map_set sets its scale and centre.

ui_menu_set imitates the 2D-map branch of vanilla UAMapToggle
(missiongameplay.c:606-615, HandleMapToggleByKeyboardShortcut at
:1459-1473) without its map-ownership and 3D-map gates, and closes through
MapMenu.CloseMapMenu (mapmenu.c:332-367) on the FindMenu(MENU_MAP)
instance. ui_map_set calls SetScale and SetMapPos on the open menu's
MapWidget, as MapMenu.Init does (mapmenu.c:97-98), found by class.
"""
from __future__ import annotations

import json
import math
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
from tests.enforce_subset_helpers import (
    EnforceArray,
    EnforceString,
    EnforceVector,
    fake_class,
    method_body as enforce_method_body,
    translate,
)
from tests.fence_helpers import (
    INST_CLIENT,
    PID_CLIENT,
    bind_both_peers,
    bound_queue,
    poll_census_query,
)


MENU_COMMAND = "ui_menu_set"
MAP_COMMAND = "ui_map_set"
MENU_MAP_ID = 22  # vanilla MENU_MAP
SCRIPTS = addon_root() / "scripts"
CLIENT_PATH = SCRIPTS / "5_Mission" / "MCPClientBridge.c"
SERVER_BRIDGE_PATH = SCRIPTS / "5_Mission" / "MCPBridge.c"
MESSAGES_PATH = SCRIPTS / "5_Mission" / "MCPMessages.c"
CENSUS_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bridge_capabilities_v1.json"

DISPATCH_CHAIN = "protected void Dispatch(MCPCommand command)"
MENU_DISPATCH_TARGET = "postNow = DispatchUiMenuSet(command, result);"
MAP_DISPATCH_TARGET = "postNow = DispatchUiMapSet(command, result);"
PAUSE_DISPATCH_TARGET = "postNow = DispatchUiPause(command, result);"
DISPATCH_UI_MENU = "protected bool DispatchUiMenuSet(MCPCommand command, MCPResult result)"
PROCESS_UI_MENU = "protected bool ProcessUiMenuJob(MCPJob job)"
POST_UI_MENU = "protected void PostUiMenuJob(MCPJob job)"
DISPATCH_UI_MAP = "protected bool DispatchUiMapSet(MCPCommand command, MCPResult result)"
PROCESS_UI_MAP = "protected bool ProcessUiMapJob(MCPJob job)"
POST_UI_MAP = "protected void PostUiMapJob(MCPJob job)"
STOP_MAP = "protected void StopLeaseCloseMap()"
MAINTAIN_MAP = "protected void MaintainMCPMapMenu()"
CLOSE_DISPATCH = "protected bool DispatchLeaseCloseClient(MCPCommand command, MCPResult result)"
CENSUS = "protected MCPLeaseCloseCensus BuildLeaseCloseCensus(MCPArgs closeArgs)"
CENSUS_UI_MENU = "bool UiMenuCensusActive()"
CENSUS_UI_MENU_FIRST = "\t\tbool mapActive = false;\n"
CENSUS_UI_MENU_ENTRY = '\t\tAddCloseEntry(census, "ui_menu", mapActive, "ui_menu");\n'
STOP_GUARD = "if (ui.FindMenu(MENU_MAP) != m_MCPMapMenu)"
MENU_GUARD = "if (ui.FindMenu(menuId) != m_MCPMapMenu)"
FIND_WIDGET = "protected MapWidget FindMapWidgetByClass(Widget root)"
WIDGET_ROUTE = "protected MapWidget UiMapWidget(UIManager ui, int menuId, out UIScriptedMenu menu)"
MENU_TABLE = "protected bool UiMenuId(string menu, out int menuId)"
HAS_EXCLUSIVE = "protected bool HasExclusiveJob()"
FORBIDDEN_CALLS = ("RestoreGameplay", "ReleaseCamera", "PlayerControlEnable")


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
        raise AssertionError(
            f"{signature!r} occurs {count} times; expected the single "
            "ui_menu_set/ui_map_set dispatcher shape of d4b4"
        )
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


def _tool_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    blocks = raw[0] if isinstance(raw, tuple) else raw
    return json.loads(blocks[0].text)


def _in_order(body: str, *tokens: str) -> None:
    positions: list[int] = []
    for token in tokens:
        if token not in body:
            raise AssertionError(f"missing step {token!r} in:\n{body}")
        positions.append(body.index(token))
    if positions != sorted(positions) or len(set(positions)) != len(positions):
        raise AssertionError(f"steps out of order: {list(zip(tokens, positions))}")


def _strip_comments(body: str) -> str:
    """Executable Enforce only: // and /* */ comments gone, strings kept.

    A token inside a comment is not a step the bridge runs, so every
    contract check below reads the body through this first.
    """
    out: list[str] = []
    index = 0
    in_string = False
    while index < len(body):
        char = body[index]
        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < len(body):
                out.append(body[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if body.startswith("//", index):
            end = body.find("\n", index)
            if end == -1:
                break
            out.append("\n")
            index = end + 1
            continue
        if body.startswith("/*", index):
            end = body.find("*/", index + 2)
            if end == -1:
                raise AssertionError("unterminated /* */ comment in Enforce body")
            out.append(" ")
            index = end + 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _no_forbidden_calls(*bodies: str) -> None:
    for body in bodies:
        code = _strip_comments(body)
        for call in FORBIDDEN_CALLS:
            if re.search(rf"\b{call}\s*\(", code):
                raise AssertionError(f"dispatcher must not call {call}")


def assert_exclusive_contract(body: str) -> None:
    code = _strip_comments(body)
    for kind in ("ui_menu", "ui_map"):
        if f'CountOfKind("{kind}")' not in code:
            raise AssertionError(f"HasExclusiveJob must count {kind} jobs")
    for subtraction in (
        "blocking = blocking - uiMenuJobs;",
        "blocking = blocking - uiMapJobs;",
    ):
        if subtraction not in code:
            raise AssertionError(f"HasExclusiveJob must subtract: {subtraction}")


def assert_dispatch_targets(dispatch: str) -> None:
    code = _strip_comments(dispatch)
    for command, target in (
        (MENU_COMMAND, MENU_DISPATCH_TARGET),
        (MAP_COMMAND, MAP_DISPATCH_TARGET),
    ):
        branch = _if_body(code, f'command.cmd == "{command}"')
        if branch.strip() != target:
            raise AssertionError(
                f"Dispatch branch for {command!r} must be {target!r}, "
                f"got {branch.strip()!r}"
            )


def assert_ui_menu_contract(dispatch: str, process: str, post: str) -> None:
    dispatch = _strip_comments(dispatch)
    process = _strip_comments(process)
    post = _strip_comments(post)
    _in_order(
        dispatch,
        "!UiMenuId(command.args.menu, menuId)",
        'result.error = "bad_args";',
        'result.error = "client_not_in_game";',
        'result.error = "no_mission";',
        "openBefore = ui.IsMenuOpen(menuId);",
        "topBefore = ui.GetMenu();",
        "if (topBefore)",
        "beforeId = topBefore.GetID();",
        "if (openBefore == wantOpen)",
        "if (wantOpen && topBefore)",
        'result.error = "ui_menu_busy";',
        "ui.EnterScriptedMenu(menuId, null);",
        'mission.AddActiveInputExcludes({"map"});',
        "mission.AddActiveInputRestriction(EInputRestrictors.MAP);",
        "m_MCPMapMenu = ui.FindMenu(menuId);",
    )
    _in_order(
        dispatch,
        "mapMenu = MapMenu.Cast(ui.FindMenu(menuId));",
        "openedByTool = m_MCPMapMenu && mapMenu == m_MCPMapMenu;",
        "mapMenu.CloseMapMenu();",
        'if (mission.IsInputExcludeActive("map"))',
        'mission.RemoveActiveInputExcludes({"map"});',
        "if (mission.IsInputRestrictionActive(EInputRestrictors.MAP))",
        "mission.RemoveActiveInputRestriction(EInputRestrictors.MAP);",
        MENU_GUARD,
        "m_MCPMapMenu = null;",
    )
    # P2 (r5): the mark falls only once FindMenu no longer returns the
    # tool's instance. An unconditional clear after CloseMapMenu would
    # read clean in the lease census while a deferred or rejected
    # close left the map open, so the clear must sit inside the guard
    # and nowhere else in the dispatch.
    if dispatch.count("m_MCPMapMenu = null;") != 1:
        raise AssertionError("DispatchUiMenuSet must clear the map mark exactly once")
    opened_guard_at = dispatch.find(MENU_GUARD)
    if opened_guard_at < 0:
        raise AssertionError("the explicit close must guard the mark clear on FindMenu")
    guard_block = _block_after(dispatch, opened_guard_at)
    if "m_MCPMapMenu = null;" not in guard_block:
        raise AssertionError("the explicit close must clear the mark inside its FindMenu guard")
    # E1 (P2): the remaining removals run only for the instance this
    # tool opened. Replacing if (openedByTool) with if (mapMenu) would
    # strip a foreign mod map's excludes, so the removals and the mark
    # clear must sit inside that block and nowhere else in the dispatch.
    if "if (openedByTool)" not in dispatch:
        raise AssertionError("the E1 cleanup must be conditioned on openedByTool")
    opened_block = _block_after(dispatch, dispatch.index("if (openedByTool)"))
    for removal in (
        'mission.RemoveActiveInputExcludes({"map"});',
        "mission.RemoveActiveInputRestriction(EInputRestrictors.MAP);",
        "m_MCPMapMenu = null;",
    ):
        if removal not in opened_block:
            raise AssertionError(f"E1 cleanup step must be inside if (openedByTool): {removal}")
        if dispatch.count(removal) != 1:
            raise AssertionError(f"E1 cleanup step must occur once, inside if (openedByTool): {removal}")
    # The busy rejection must exit the dispatcher, not only name the
    # error: without its return, execution falls through to the open.
    busy_block = _block_after(dispatch, dispatch.index("if (wantOpen && topBefore)"))
    if "return true;" not in busy_block:
        raise AssertionError("the ui_menu_busy block must return before entering")
    if 'result.error = "ui_menu_busy";' not in busy_block:
        raise AssertionError("the ui_menu_busy block must set ui_menu_busy")
    if "EnterScriptedMenu" in busy_block:
        raise AssertionError("the ui_menu_busy block must not enter a menu")
    # E1: what the close still owes is queried, never deduced from the
    # config gates that CloseMapMenu (mapmenu.c:352-364) consults.
    for gate in (
        "CfgGameplayHandler.GetMapIgnoreMapOwnership",
        "CfgGameplayHandler.GetUse3DMap",
    ):
        if gate in dispatch:
            raise AssertionError(f"the close must query state, not deduce from {gate}")
    if "job.ui_menu_removed = removed;" not in dispatch:
        raise AssertionError("the read-back job must carry what the close removed")
    if '" ok=1 removed=" + removed' not in dispatch:
        raise AssertionError("a successful close must log what it removed")
    if '" ok=1 removed=" + removedPost' not in post:
        raise AssertionError("a deferred successful close must log what it removed")
    if '" ok=0 error=ui_menu_not_closed removed=" + removedPost' not in post:
        raise AssertionError("a failed close must log what it removed")
    if '" ok=0 error=" + job.error + " removed=" + removedPost' not in post:
        raise AssertionError("an aborted close must log what it removed")
    if "job.kind = \"ui_menu\";" not in dispatch or "return false;" not in dispatch:
        raise AssertionError("ui_menu_set must queue its one-tick read-back job")
    if "return m_JobRunner.GetElapsedS() > job.sample_start_s;" not in process:
        raise AssertionError("ProcessUiMenuJob must read on the next tick")
    for token in ('result.error = "ui_menu_not_opened";', 'result.error = "ui_menu_not_closed";'):
        if token not in post:
            raise AssertionError(f"PostUiMenuJob is missing {token!r}")
    _no_forbidden_calls(dispatch, process, post)


def assert_ui_map_contract(
    dispatch: str, process: str, post: str, finder: str, route: str = ""
) -> None:
    dispatch = _strip_comments(dispatch)
    process = _strip_comments(process)
    post = _strip_comments(post)
    finder = _strip_comments(finder)
    route = _strip_comments(route)
    _in_order(
        dispatch,
        "!IsFiniteFloat(command.args.scale)",
        'result.error = "bad_args";',
        'result.error = "client_not_in_game";',
        'result.error = "no_mission";',
        "ui.IsMenuOpen(menuId)",
        'result.error = "ui_map_not_open";',
        "widget = UiMapWidget(ui, menuId, menu);",
        'result.error = "ui_map_widget_missing";',
        "widget.SetScale(command.args.scale);",
        "widget.SetMapPos(Vector(command.args.center.Get(0), 0.0, command.args.center.Get(1)));",
    )
    if "MapWidget.Cast(root)" not in finder and "MapWidget.Cast(" not in finder:
        raise AssertionError("the MapWidget must be found by class (MapWidget.Cast)")
    # The widget may sit below the layout root, so the finder must walk
    # the children recursively: deleting the recursive call must fail.
    _in_order(
        finder,
        "MapWidget.Cast(root)",
        "return found;",
        "child = root.GetChildren();",
        "while (child)",
        "hit = FindMapWidgetByClass(child);",
        "if (hit)",
        "return hit;",
        "child = child.GetSibling();",
    )
    for by_name in ("FindAnyWidget", "GetName()", '"Map"'):
        if by_name in finder:
            raise AssertionError(f"the MapWidget must not be found by name ({by_name})")
    if route:
        # The complete route: the menu must be fetched with FindMenu
        # before its layout root is searched. Deleting only
        # `menu = ui.FindMenu(menuId);` leaves menu null, so the route
        # returns null and ui_map_set answers ui_map_widget_missing
        # with MENU_MAP open and a MapWidget in it.
        _in_order(
            route,
            "menu = null;",
            "if (!ui)",
            "menu = ui.FindMenu(menuId);",
            "if (!menu)",
            "return FindMapWidgetByClass(menu.GetLayoutRoot());",
        )
        if route.count("menu = ui.FindMenu(menuId);") != 1:
            raise AssertionError(
                "the active UiMapWidget route must call ui.FindMenu(menuId) exactly once"
            )
        for by_name in ("FindAnyWidget", "GetName()", '"Map"'):
            if by_name in route:
                raise AssertionError(
                    f"the active UiMapWidget route must not find by name ({by_name})"
                )
    if "job.kind = \"ui_map\";" not in dispatch:
        raise AssertionError("ui_map_set must queue its one-tick read-back job")
    if "return m_JobRunner.GetElapsedS() > job.sample_start_s;" not in process:
        raise AssertionError("ProcessUiMapJob must read on the next tick")
    if 'result.error = "ui_map_not_set";' not in post:
        raise AssertionError("PostUiMapJob is missing ui_map_not_set")
    _no_forbidden_calls(dispatch, process, post)


def assert_lease_close_map_contract(stop: str, close: str, census: str) -> None:
    stop = _strip_comments(stop)
    close = _strip_comments(close)
    census = _strip_comments(census)
    # E3 stop: only the instance this tool opened, closed as
    # ui_menu_set("map", false) closes it, with the E1 cleanup after.
    _in_order(
        stop,
        "if (!m_MCPMapMenu)",
        "mapMenu = MapMenu.Cast(ui.FindMenu(MENU_MAP));",
        "if (!mapMenu || mapMenu != m_MCPMapMenu)",
        "mapMenu.CloseMapMenu();",
        'if (mission.IsInputExcludeActive("map"))',
        'mission.RemoveActiveInputExcludes({"map"});',
        "if (mission.IsInputRestrictionActive(EInputRestrictors.MAP))",
        "mission.RemoveActiveInputRestriction(EInputRestrictors.MAP);",
        STOP_GUARD,
        "m_MCPMapMenu = null;",
    )
    # P2 (r5): as in the explicit close, the lease stop must keep the
    # mark while FindMenu still returns the tool's instance, or the
    # census below reads clean over a map a deferred or rejected
    # CloseMapMenu left open.
    if stop.count("m_MCPMapMenu = null;") != 1:
        raise AssertionError("StopLeaseCloseMap must clear the map mark exactly once")
    stop_guard_at = stop.find(STOP_GUARD)
    if stop_guard_at < 0:
        raise AssertionError("StopLeaseCloseMap must guard the mark clear on FindMenu")
    stop_guard_block = _block_after(stop, stop_guard_at)
    if "m_MCPMapMenu = null;" not in stop_guard_block:
        raise AssertionError("StopLeaseCloseMap must clear the mark inside its FindMenu guard")
    # E3 dispatch: after AbortAll and alongside the pause stop, before
    # the census that reads what is left.
    _in_order(
        close,
        "m_JobRunner.AbortAll(this);",
        "StopLeaseClosePause();",
        "StopLeaseCloseMap();",
        "MCPLeaseCloseCensus census = BuildLeaseCloseCensus(command.args);",
    )
    if close.count("StopLeaseCloseMap();") != 1:
        raise AssertionError("the lease close must run StopLeaseCloseMap exactly once")
    # E3 census: a ui_menu row, active only while the tool's own map
    # instance is still the open MENU_MAP.
    _in_order(
        census,
        'AddCloseEntry(census, "ui_pause", pauseActive, "ui_pause");',
        "if (m_MCPMapMenu)",
        "closeMapUi.FindMenu(MENU_MAP) == m_MCPMapMenu",
        'AddCloseEntry(census, "ui_menu", mapActive, "ui_menu");',
    )
    _no_forbidden_calls(stop)


def _census_ui_menu_function(source: str) -> str:
    """The census's ui_menu lines, from its flag to its AddCloseEntry, as
    a function that returns the flag (residue when it is true)."""
    body = enforce_method_body(source, CENSUS)
    if body.count(CENSUS_UI_MENU_FIRST) != 1 or body.count(CENSUS_UI_MENU_ENTRY) != 1:
        raise AssertionError("the census has no single ui_menu flag and entry")
    lines = body[body.index(CENSUS_UI_MENU_FIRST) : body.index(CENSUS_UI_MENU_ENTRY)]
    return CENSUS_UI_MENU + "\n{\n" + lines + "\t\treturn mapActive;\n}\n"


def _explicit_close_wrapper(dispatch_body: str) -> str:
    """DispatchUiMenuSet's close block as a free function returning removed.

    The block's own locals are declared with their dispatch types; ui,
    mission and the mark arrive as members, as in the bridge.
    """
    start = dispatch_body.index("mapMenu = MapMenu.Cast(ui.FindMenu(menuId));")
    if_map = dispatch_body.index("if (mapMenu)", start)
    interior = _block_after(dispatch_body, if_map)
    brace = dispatch_body.index("{", if_map)
    fragment = dispatch_body[start : brace + 1 + len(interior) + 1]
    fragment = fragment.replace('{"map"}', "MAP_EXCLUDES")
    prologue = (
        "\tint menuId;\n\tmenuId = MENU_MAP;\n\tMapMenu mapMenu;\n"
        "\tbool openedByTool;\n\tstring removed;\n"
        '\topenedByTool = false;\n\tremoved = "none";\n'
    )
    return "string CloseExplicitMap()\n{\n" + prologue + fragment + "\n\treturn removed;\n}\n"


class _LeaseMapMenu:
    """MapMenu double: effective, deferred or rejected CloseMapMenu."""

    def __init__(self, ui: "_LeaseUi", mode: str) -> None:
        self._ui = ui
        self.mode = mode
        self.close_calls = 0

    def CloseMapMenu(self) -> None:
        self.close_calls += 1
        if self.mode == "effective":
            self._ui.open_map = None
        elif self.mode == "deferred":
            self._ui.pending_close = True
        elif self.mode != "rejected":
            raise AssertionError(f"unknown close mode {self.mode!r}")


class _LeaseUi:
    def __init__(self) -> None:
        self.open_map: _LeaseMapMenu | None = None
        self.pending_close = False

    def FindMenu(self, menu_id: int) -> _LeaseMapMenu | None:
        return self.open_map if menu_id == MENU_MAP_ID else None

    def complete_pending_close(self) -> None:
        if self.pending_close:
            self.open_map = None
            self.pending_close = False


class _LeaseMission:
    def __init__(self) -> None:
        self.exclude_active = True
        self.restriction_active = True
        self.removed: list[str] = []

    def IsInputExcludeActive(self, name: Any) -> bool:
        if (name.text if isinstance(name, EnforceString) else name) != "map":
            raise AssertionError(f"unexpected exclude name {name!r}")
        return self.exclude_active

    def RemoveActiveInputExcludes(self, _excludes: Any) -> None:
        self.exclude_active = False
        self.removed.append("map_exclude")

    def IsInputRestrictionActive(self, _restrictor: Any) -> bool:
        return self.restriction_active

    def RemoveActiveInputRestriction(self, _restrictor: Any) -> None:
        self.restriction_active = False
        self.removed.append("map_restriction")


class _RouteWidget:
    """Widget double: a child chain linked by siblings, as in Enforce."""

    def __init__(self, children: list["_RouteWidget"] | None = None) -> None:
        self._children = list(children or [])
        self._sibling: _RouteWidget | None = None
        for index, child in enumerate(self._children):
            child._sibling = (
                self._children[index + 1] if index + 1 < len(self._children) else None
            )

    def GetChildren(self) -> "_RouteWidget | None":
        return self._children[0] if self._children else None

    def GetSibling(self) -> "_RouteWidget | None":
        return self._sibling


class _RouteMapWidget(_RouteWidget):
    pass


class _RouteMenu:
    def __init__(self, root: _RouteWidget) -> None:
        self._root = root

    def GetLayoutRoot(self) -> _RouteWidget:
        return self._root


class _RouteUi:
    def __init__(self, menu: _RouteMenu | None) -> None:
        self._menu = menu

    def FindMenu(self, menu_id: int) -> _RouteMenu | None:
        return self._menu if menu_id == MENU_MAP_ID else None


def _map_widget_model(
    source: str,
    *,
    route_body: str | None = None,
    with_widget: bool = True,
) -> tuple[dict[str, Any], _RouteUi, _RouteMenu, _RouteMapWidget | None]:
    """Real FindMapWidgetByClass and UiMapWidget, translated and run.

    The layout is root -> container -> [plain leaf, MapWidget], so the
    widget is found only through the recursive descent and the sibling
    walk, and only if UiMapWidget first fetches the menu with
    ui.FindMenu(menuId) and searches menu.GetLayoutRoot().
    """
    map_widget = _RouteMapWidget() if with_widget else None
    nested = [child for child in (_RouteWidget(), map_widget) if child is not None]
    root = _RouteWidget([_RouteWidget(nested)])
    menu = _RouteMenu(root)
    ui = _RouteUi(menu)
    ns: dict[str, Any] = {
        "MapWidget": SimpleNamespace(
            Cast=lambda value: value if isinstance(value, _RouteMapWidget) else None
        ),
    }
    translate(
        source,
        FIND_WIDGET,
        ns,
        class_types=("MapWidget", "Widget"),
    )
    real_route = (
        route_body
        if route_body is not None
        else enforce_method_body(source, WIDGET_ROUTE)
    )
    translate(
        WIDGET_ROUTE + "\n{\n" + real_route + "\n}\n",
        WIDGET_ROUTE,
        ns,
        class_types=("MapWidget", "Widget", "UIManager", "UIScriptedMenu"),
    )
    return ns, ui, menu, map_widget


def _map_close_model(
    source: str,
    mode: str,
    *,
    marked: bool = True,
    stop_body: str | None = None,
    dispatch_body: str | None = None,
) -> tuple[dict[str, Any], _LeaseUi, _LeaseMission, _LeaseMapMenu]:
    """Real StopLeaseCloseMap, MaintainMCPMapMenu, census ui_menu row and
    explicit close block, translated against the doubles above.

    The translator has no Enforce array literal, so {"map"} is replaced
    in memory only with MAP_EXCLUDES, an EnforceArray holding "map";
    every branch, comparison and assignment under test is the .c one.
    """
    ui = _LeaseUi()
    mission = _LeaseMission()
    map_menu = _LeaseMapMenu(ui, mode)
    ui.open_map = map_menu
    game = SimpleNamespace(
        GetMission=lambda: mission, GetUIManager=lambda: ui
    )
    ns: dict[str, Any] = {
        "GetGame": lambda: game,
        "MissionGameplay": SimpleNamespace(
            Cast=lambda value: value if isinstance(value, _LeaseMission) else None
        ),
        "MapMenu": SimpleNamespace(
            Cast=lambda value: value if isinstance(value, _LeaseMapMenu) else None
        ),
        "EInputRestrictors": SimpleNamespace(MAP=7),
        "MENU_MAP": MENU_MAP_ID,
        "MAP_EXCLUDES": EnforceArray([EnforceString.of("map")]),
        "m_MCPMapMenu": map_menu if marked else None,
        "ui": ui,
        "mission": mission,
    }
    real_stop = stop_body if stop_body is not None else enforce_method_body(source, STOP_MAP)
    real_stop = real_stop.replace('{"map"}', "MAP_EXCLUDES")
    translate(
        STOP_MAP + "\n{" + real_stop + "}\n",
        STOP_MAP,
        ns,
        class_types=("MissionGameplay", "UIManager", "MapMenu"),
        members=("m_MCPMapMenu",),
    )
    translate(
        source,
        MAINTAIN_MAP,
        ns,
        class_types=("UIManager",),
        members=("m_MCPMapMenu",),
    )
    translate(
        _census_ui_menu_function(source),
        CENSUS_UI_MENU,
        ns,
        class_types=("UIManager",),
        members=("m_MCPMapMenu",),
    )
    real_dispatch = (
        dispatch_body
        if dispatch_body is not None
        else enforce_method_body(source, DISPATCH_UI_MENU)
    )
    translate(
        _explicit_close_wrapper(real_dispatch),
        "string CloseExplicitMap()",
        ns,
        class_types=("MapMenu",),
        members=("m_MCPMapMenu", "ui", "mission"),
    )
    return ns, ui, mission, map_menu


class UiMenuEnforceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(CLIENT_PATH)
        self.dispatch = _method_body(self.source, DISPATCH_CHAIN)
        self.menu_dispatch = _method_body(self.source, DISPATCH_UI_MENU)
        self.menu_process = _method_body(self.source, PROCESS_UI_MENU)
        self.menu_post = _method_body(self.source, POST_UI_MENU)
        self.map_dispatch = _method_body(self.source, DISPATCH_UI_MAP)
        self.map_process = _method_body(self.source, PROCESS_UI_MAP)
        self.map_post = _method_body(self.source, POST_UI_MAP)
        self.finder = _method_body(self.source, FIND_WIDGET)
        self.route = _method_body(self.source, WIDGET_ROUTE)

    def test_caps_and_dispatch_name_both_verbs(self) -> None:
        caps = announced_caps(self.source)
        self.assertIn(MENU_COMMAND, caps)
        self.assertIn(MAP_COMMAND, caps)
        names = dispatch_census(self.source)
        self.assertIn(MENU_COMMAND, names)
        self.assertIn(MAP_COMMAND, names)
        assert_dispatch_targets(self.dispatch)

    def test_dispatch_targets_negative_controls(self) -> None:
        # P2 (r7): naming a verb in Dispatch is not routing it. Retargeting
        # either branch to DispatchUiPause must fail: with a client in game
        # and no menu open, ui_menu_set("map", true) would open the pause
        # menu and answer ui_pause, and ui_map_set would do the same.
        assert_dispatch_targets(self.dispatch)
        for target in (MENU_DISPATCH_TARGET, MAP_DISPATCH_TARGET):
            with self.subTest(target=target):
                retargeted = self.dispatch.replace(
                    target, PAUSE_DISPATCH_TARGET, 1
                )
                self.assertNotEqual(retargeted, self.dispatch)
                with self.assertRaises(AssertionError):
                    assert_dispatch_targets(retargeted)
        # Swapping the two targets with each other must fail too.
        swapped = self.dispatch.replace(MENU_DISPATCH_TARGET, MAP_DISPATCH_TARGET, 1)
        map_branch_at = swapped.index(f'command.cmd == "{MAP_COMMAND}"')
        map_call_at = swapped.index("DispatchUiMapSet", map_branch_at)
        swapped = (
            swapped[:map_call_at]
            + "DispatchUiMenuSet"
            + swapped[map_call_at + len("DispatchUiMapSet") :]
        )
        self.assertNotEqual(swapped, self.dispatch)
        with self.assertRaises(AssertionError):
            assert_dispatch_targets(swapped)

    def test_menu_table_has_only_map(self) -> None:
        table = _method_body(self.source, MENU_TABLE)
        self.assertIn('menu == "map"', table)
        self.assertIn("menuId = MENU_MAP;", table)
        self.assertEqual(loopback.UI_MENU_NAMES, frozenset({"map"}))

    def test_ui_menu_checks_busy_before_entering_and_closes_on_find_menu(self) -> None:
        assert_ui_menu_contract(self.menu_dispatch, self.menu_process, self.menu_post)

    def test_ui_map_finds_widget_by_class_and_sets_scale_and_centre(self) -> None:
        assert_ui_map_contract(
            self.map_dispatch, self.map_process, self.map_post, self.finder, self.route
        )

    def test_top_before_read_is_required(self) -> None:
        # Deleting only the GetMenu() read leaves topBefore null, so
        # with the pause menu open ui_menu_set("map", true) would enter
        # the map instead of answering ui_menu_busy.
        assert_ui_menu_contract(self.menu_dispatch, self.menu_process, self.menu_post)
        no_top = self.menu_dispatch.replace("topBefore = ui.GetMenu();", "", 1)
        self.assertNotEqual(no_top, self.menu_dispatch)
        with self.assertRaises(AssertionError):
            assert_ui_menu_contract(no_top, self.menu_process, self.menu_post)

    def test_finder_recursion_is_required(self) -> None:
        # Deleting only the recursive call leaves descendants unvisited,
        # so a MapWidget below the layout root reads as missing.
        assert_ui_map_contract(
            self.map_dispatch, self.map_process, self.map_post, self.finder, self.route
        )
        no_recursion = self.finder.replace(
            "hit = FindMapWidgetByClass(child);", "", 1
        )
        self.assertNotEqual(no_recursion, self.finder)
        with self.assertRaises(AssertionError):
            assert_ui_map_contract(
                self.map_dispatch, self.map_process, self.map_post,
                no_recursion, self.route,
            )

    def test_finder_returns_the_found_widget(self) -> None:
        # P2: return null instead of return found would answer
        # ui_map_widget_missing with the map open and a MapWidget in it.
        assert_ui_map_contract(
            self.map_dispatch, self.map_process, self.map_post, self.finder, self.route
        )
        no_return = self.finder.replace("return found;", "return null;", 1)
        self.assertNotEqual(no_return, self.finder)
        with self.assertRaises(AssertionError):
            assert_ui_map_contract(
                self.map_dispatch, self.map_process, self.map_post,
                no_return, self.route,
            )

    def test_ui_map_widget_route_finds_nested_widget_executed(self) -> None:
        # P2 (r6): the complete UiMapWidget route, executed. With
        # MENU_MAP open and a MapWidget nested below the layout root,
        # the real route returns that widget and the menu; deleting
        # only `menu = ui.FindMenu(menuId);` returns null, so
        # ui_map_set(scale=0.5) would answer ui_map_widget_missing.
        ns, ui, menu, map_widget = _map_widget_model(self.source)
        widget, found_menu = ns["UiMapWidget"](ui, MENU_MAP_ID)
        self.assertIs(widget, map_widget)
        self.assertIs(found_menu, menu)
        # No menu for MENU_MAP, and a menu without a MapWidget, both
        # return null (the widget for the second, with its menu).
        widget, found_menu = ns["UiMapWidget"](_RouteUi(None), MENU_MAP_ID)
        self.assertIsNone(widget)
        self.assertIsNone(found_menu)
        ns, ui, menu, _none = _map_widget_model(self.source, with_widget=False)
        widget, found_menu = ns["UiMapWidget"](ui, MENU_MAP_ID)
        self.assertIsNone(widget)
        self.assertIs(found_menu, menu)
        # Negative control: the same route without its FindMenu fetch.
        route_body = enforce_method_body(self.source, WIDGET_ROUTE)
        mutant_body = route_body.replace("menu = ui.FindMenu(menuId);", "", 1)
        self.assertNotEqual(mutant_body, route_body)
        with self.assertRaises(AssertionError):
            assert_ui_map_contract(
                self.map_dispatch, self.map_process, self.map_post,
                self.finder, mutant_body,
            )
        ns, ui, _menu, _widget = _map_widget_model(
            self.source, route_body=mutant_body
        )
        widget, found_menu = ns["UiMapWidget"](ui, MENU_MAP_ID)
        self.assertIsNone(widget)
        self.assertIsNone(found_menu)

    def test_close_removals_are_conditioned_on_tool_ownership(self) -> None:
        # P2: if (mapMenu) instead of if (openedByTool) would strip the
        # excludes of a foreign mod map whose CloseMapMenu only closes.
        assert_ui_menu_contract(self.menu_dispatch, self.menu_process, self.menu_post)
        foreign = self.menu_dispatch.replace("if (openedByTool)", "if (mapMenu)", 1)
        self.assertNotEqual(foreign, self.menu_dispatch)
        with self.assertRaises(AssertionError):
            assert_ui_menu_contract(foreign, self.menu_process, self.menu_post)

    def test_lease_close_closes_tool_map_and_census_counts_it(self) -> None:
        stop = _method_body(self.source, STOP_MAP)
        close = _method_body(self.source, CLOSE_DISPATCH)
        census = _method_body(self.source, CENSUS)
        assert_lease_close_map_contract(stop, close, census)
        # Without the stop step the tool's map stays open and the
        # ui_menu row reads residue instead of clean.
        no_stop_call = close.replace("StopLeaseCloseMap();", "", 1)
        self.assertNotEqual(no_stop_call, close)
        with self.assertRaises(AssertionError):
            assert_lease_close_map_contract(stop, no_stop_call, census)
        no_stop_close = stop.replace("mapMenu.CloseMapMenu();", "", 1)
        self.assertNotEqual(no_stop_close, stop)
        with self.assertRaises(AssertionError):
            assert_lease_close_map_contract(no_stop_close, close, census)
        # Without the instance guard a foreign map would be closed.
        foreign_stop = stop.replace(
            "if (!mapMenu || mapMenu != m_MCPMapMenu)", "if (!mapMenu)", 1
        )
        self.assertNotEqual(foreign_stop, stop)
        with self.assertRaises(AssertionError):
            assert_lease_close_map_contract(foreign_stop, close, census)
        # Without the census row the residue is invisible.
        no_row = census.replace(
            'AddCloseEntry(census, "ui_menu", mapActive, "ui_menu");', "", 1
        )
        self.assertNotEqual(no_row, census)
        with self.assertRaises(AssertionError):
            assert_lease_close_map_contract(stop, close, no_row)
        # A census that counts any open map counts the player's too.
        foreign_census = census.replace("if (m_MCPMapMenu)", "if (true)", 1)
        self.assertNotEqual(foreign_census, census)
        with self.assertRaises(AssertionError):
            assert_lease_close_map_contract(stop, close, foreign_census)
        # An unconditional mark clear after CloseMapMenu must fail too:
        # it reads clean while a deferred or rejected close is open.
        unguarded_stop = stop.replace(STOP_GUARD, "if (true)", 1)
        self.assertNotEqual(unguarded_stop, stop)
        with self.assertRaises(AssertionError):
            assert_lease_close_map_contract(unguarded_stop, close, census)

    def test_lease_close_stop_then_census_executed(self) -> None:
        # P2 (r5): the real stop -> census sequence, not tokens alone.
        # Effective: the map is gone, the mark falls, the row is clean.
        ns, ui, _mission, map_menu = _map_close_model(self.source, "effective")
        ns["StopLeaseCloseMap"]()
        self.assertEqual(map_menu.close_calls, 1)
        self.assertIsNone(ui.open_map)
        self.assertIsNone(ns["m_MCPMapMenu"])
        self.assertFalse(ns["UiMenuCensusActive"]())
        # Deferred: Close() has not taken effect yet, so the mark and
        # the residue stay; once it lands, Maintain drops the mark.
        ns, ui, _mission, map_menu = _map_close_model(self.source, "deferred")
        ns["StopLeaseCloseMap"]()
        self.assertIs(ns["m_MCPMapMenu"], map_menu)
        self.assertTrue(ns["UiMenuCensusActive"]())
        ui.complete_pending_close()
        ns["MaintainMCPMapMenu"]()
        self.assertIsNone(ns["m_MCPMapMenu"])
        self.assertFalse(ns["UiMenuCensusActive"]())
        # Rejected: an override keeps the map open; the row is residue.
        ns, _ui, _mission, map_menu = _map_close_model(self.source, "rejected")
        ns["StopLeaseCloseMap"]()
        self.assertIs(ns["m_MCPMapMenu"], map_menu)
        self.assertTrue(ns["UiMenuCensusActive"]())
        # Without the stop the tool's open map is residue as well.
        ns, _ui, _mission, map_menu = _map_close_model(self.source, "effective")
        self.assertEqual(map_menu.close_calls, 0)
        self.assertTrue(ns["UiMenuCensusActive"]())
        # A map the tool did not open is neither closed nor counted.
        ns, _ui, _mission, map_menu = _map_close_model(
            self.source, "effective", marked=False
        )
        ns["StopLeaseCloseMap"]()
        self.assertEqual(map_menu.close_calls, 0)
        self.assertFalse(ns["UiMenuCensusActive"]())
        # Mutant: an unconditional clear loses the rejected residue.
        stop_body = enforce_method_body(self.source, STOP_MAP)
        mutant_body = stop_body.replace(STOP_GUARD, "if (true)", 1)
        self.assertNotEqual(mutant_body, stop_body)
        ns, _ui, _mission, _map = _map_close_model(
            self.source, "rejected", stop_body=mutant_body
        )
        ns["StopLeaseCloseMap"]()
        self.assertIsNone(ns["m_MCPMapMenu"])
        self.assertFalse(ns["UiMenuCensusActive"]())

    def test_explicit_close_keeps_mark_until_find_menu_drops_it_executed(self) -> None:
        # P2 (r5): the same three closes through DispatchUiMenuSet's
        # real close block.
        ns, ui, _mission, _map = _map_close_model(self.source, "effective")
        removed = ns["CloseExplicitMap"]()
        self.assertIsNone(ui.open_map)
        self.assertIsNone(ns["m_MCPMapMenu"])
        self.assertEqual(removed.text, "map_exclude+map_restriction")
        self.assertFalse(ns["UiMenuCensusActive"]())
        ns, ui, _mission, map_menu = _map_close_model(self.source, "deferred")
        ns["CloseExplicitMap"]()
        self.assertIs(ns["m_MCPMapMenu"], map_menu)
        self.assertTrue(ns["UiMenuCensusActive"]())
        ui.complete_pending_close()
        ns["MaintainMCPMapMenu"]()
        self.assertIsNone(ns["m_MCPMapMenu"])
        self.assertFalse(ns["UiMenuCensusActive"]())
        ns, _ui, _mission, map_menu = _map_close_model(self.source, "rejected")
        ns["CloseExplicitMap"]()
        self.assertIs(ns["m_MCPMapMenu"], map_menu)
        self.assertTrue(ns["UiMenuCensusActive"]())
        # Mutant: an unconditional clear loses the rejected residue.
        dispatch_body = enforce_method_body(self.source, DISPATCH_UI_MENU)
        mutant_body = dispatch_body.replace(MENU_GUARD, "if (true)", 1)
        self.assertNotEqual(mutant_body, dispatch_body)
        ns, _ui, _mission, _map = _map_close_model(
            self.source, "rejected", dispatch_body=mutant_body
        )
        ns["CloseExplicitMap"]()
        self.assertIsNone(ns["m_MCPMapMenu"])
        self.assertFalse(ns["UiMenuCensusActive"]())

    def test_read_jobs_do_not_count_as_exclusive(self) -> None:
        assert_exclusive_contract(_method_body(self.source, HAS_EXCLUSIVE))

    def test_negative_controls_fail_on_mutated_copies(self) -> None:
        assert_dispatch_targets(self.dispatch)
        # Retargeting either Dispatch branch to DispatchUiPause must fail.
        for target in (MENU_DISPATCH_TARGET, MAP_DISPATCH_TARGET):
            with self.subTest(target=target):
                retargeted = self.dispatch.replace(
                    target, PAUSE_DISPATCH_TARGET, 1
                )
                self.assertNotEqual(retargeted, self.dispatch)
                with self.assertRaises(AssertionError):
                    assert_dispatch_targets(retargeted)
        assert_ui_menu_contract(self.menu_dispatch, self.menu_process, self.menu_post)
        assert_ui_map_contract(
            self.map_dispatch, self.map_process, self.map_post, self.finder, self.route
        )
        assert_exclusive_contract(_method_body(self.source, HAS_EXCLUSIVE))
        # Without only the top-menu read the busy check never fires.
        no_top = self.menu_dispatch.replace("topBefore = ui.GetMenu();", "", 1)
        self.assertNotEqual(no_top, self.menu_dispatch)
        with self.assertRaises(AssertionError):
            assert_ui_menu_contract(no_top, self.menu_process, self.menu_post)
        # Without only the recursive descent the finder misses nested widgets.
        no_recursion = self.finder.replace(
            "hit = FindMapWidgetByClass(child);", "", 1
        )
        self.assertNotEqual(no_recursion, self.finder)
        with self.assertRaises(AssertionError):
            assert_ui_map_contract(
                self.map_dispatch, self.map_process, self.map_post,
                no_recursion, self.route,
            )
        # Without the ui_menu_busy check (d) the contract must fail.
        no_busy = self.menu_dispatch.replace(
            "if (wantOpen && topBefore)", "if (wantOpen && false)", 1
        )
        self.assertNotEqual(no_busy, self.menu_dispatch)
        with self.assertRaises(AssertionError):
            assert_ui_menu_contract(no_busy, self.menu_process, self.menu_post)
        # Commenting the busy block out must fail too: it is not executable.
        busy_start = self.menu_dispatch.index("if (wantOpen && topBefore)")
        busy_end = self.menu_dispatch.index(
            'result.error = "ui_menu_busy";', busy_start
        )
        commented_busy = (
            self.menu_dispatch[:busy_start]
            + "/*"
            + self.menu_dispatch[busy_start:busy_end]
            + "*/"
            + self.menu_dispatch[busy_end:]
        )
        with self.assertRaises(AssertionError):
            assert_ui_menu_contract(commented_busy, self.menu_process, self.menu_post)
        # Entering before the busy check must fail too.
        busy_after_enter = self.menu_dispatch.replace(
            'result.error = "ui_menu_busy";', 'result.error = "ui_menu_busy_late";', 1
        )
        with self.assertRaises(AssertionError):
            assert_ui_menu_contract(busy_after_enter, self.menu_process, self.menu_post)
        # Keeping the busy error but dropping its return must fail too:
        # execution would fall through to EnterScriptedMenu.
        busy_return_start = self.menu_dispatch.index(
            "return true;", self.menu_dispatch.index("if (wantOpen && topBefore)")
        )
        no_busy_return = (
            self.menu_dispatch[:busy_return_start]
            + self.menu_dispatch[busy_return_start + len("return true;") :]
        )
        self.assertNotEqual(no_busy_return, self.menu_dispatch)
        with self.assertRaises(AssertionError):
            assert_ui_menu_contract(no_busy_return, self.menu_process, self.menu_post)
        # A forbidden call in a dispatcher must fail, with or without a
        # space before its parenthesis.
        for call in (
            "RestoreGameplay();",
            "RestoreGameplay ();",
            "ReleaseCamera ();",
            "mission.PlayerControlEnable (true);",
        ):
            with self.subTest(call=call):
                with_forbidden = self.menu_dispatch.replace(
                    "wantOpen = command.args.open;",
                    f"wantOpen = command.args.open;\n\t\t{call}",
                    1,
                )
                self.assertNotEqual(with_forbidden, self.menu_dispatch)
                with self.assertRaises(AssertionError):
                    assert_ui_menu_contract(
                        with_forbidden, self.menu_process, self.menu_post
                    )
        with_restore_map = self.map_dispatch.replace(
            "FillUiMapRead(reply, widget);",
            "RestoreGameplay ();\n\t\tFillUiMapRead(reply, widget);",
            1,
        )
        with self.assertRaises(AssertionError):
            assert_ui_map_contract(
                with_restore_map, self.map_process, self.map_post,
                self.finder, self.route,
            )
        # Without the E1 remaining-removal, without its state queries,
        # without removed on a failed close, or without the instance
        # mark, the contract must fail.
        no_removal = self.menu_dispatch.replace(
            "mission.RemoveActiveInputRestriction(EInputRestrictors.MAP);",
            "",
            1,
        )
        self.assertNotEqual(no_removal, self.menu_dispatch)
        with self.assertRaises(AssertionError):
            assert_ui_menu_contract(no_removal, self.menu_process, self.menu_post)
        no_query = self.menu_dispatch.replace(
            'if (mission.IsInputExcludeActive("map"))',
            "if (true)",
            1,
        )
        self.assertNotEqual(no_query, self.menu_dispatch)
        with self.assertRaises(AssertionError):
            assert_ui_menu_contract(no_query, self.menu_process, self.menu_post)
        gate_deduced = self.menu_dispatch.replace(
            'if (mission.IsInputRestrictionActive(EInputRestrictors.MAP))',
            "if (!CfgGameplayHandler.GetMapIgnoreMapOwnership())",
            1,
        )
        self.assertNotEqual(gate_deduced, self.menu_dispatch)
        with self.assertRaises(AssertionError):
            assert_ui_menu_contract(gate_deduced, self.menu_process, self.menu_post)
        no_removed_on_failure = self.menu_post.replace(
            '" ok=0 error=ui_menu_not_closed removed=" + removedPost',
            '" ok=0 error=ui_menu_not_closed"',
            1,
        )
        self.assertNotEqual(no_removed_on_failure, self.menu_post)
        with self.assertRaises(AssertionError):
            assert_ui_menu_contract(
                self.menu_dispatch, self.menu_process, no_removed_on_failure
            )
        no_mark = self.menu_dispatch.replace(
            "m_MCPMapMenu = ui.FindMenu(menuId);",
            "m_MCPMapMenu = null;",
            1,
        )
        with self.assertRaises(AssertionError):
            assert_ui_menu_contract(no_mark, self.menu_process, self.menu_post)
        # Without only the FindMenu fetch the route never reaches the
        # layout root, so the widget reads as missing (P2 r6).
        no_find_menu = self.route.replace("menu = ui.FindMenu(menuId);", "", 1)
        self.assertNotEqual(no_find_menu, self.route)
        with self.assertRaises(AssertionError):
            assert_ui_map_contract(
                self.map_dispatch, self.map_process, self.map_post,
                self.finder, no_find_menu,
            )
        # Returning null for the widget found by class must fail.
        no_found = self.finder.replace("return found;", "return null;", 1)
        self.assertNotEqual(no_found, self.finder)
        with self.assertRaises(AssertionError):
            assert_ui_map_contract(
                self.map_dispatch, self.map_process, self.map_post,
                no_found, self.route,
            )
        # Conditioning the E1 removals on any map, not on the tool's
        # own instance, must fail.
        foreign_removal = self.menu_dispatch.replace(
            "if (openedByTool)", "if (mapMenu)", 1
        )
        self.assertNotEqual(foreign_removal, self.menu_dispatch)
        with self.assertRaises(AssertionError):
            assert_ui_menu_contract(foreign_removal, self.menu_process, self.menu_post)
        # An unconditional mark clear in the explicit close must fail:
        # it would read clean while the map is still open (P2 r5).
        unguarded_close = self.menu_dispatch.replace(MENU_GUARD, "if (true)", 1)
        self.assertNotEqual(unguarded_close, self.menu_dispatch)
        with self.assertRaises(AssertionError):
            assert_ui_menu_contract(unguarded_close, self.menu_process, self.menu_post)
        # Finding the widget by name instead of by class must fail, in
        # the finder and in the active UiMapWidget route alike.
        by_name = self.finder.replace(
            "MapWidget.Cast(root)", 'MapWidget.Cast(FindAnyWidget("Map"))', 1
        )
        with self.assertRaises(AssertionError):
            assert_ui_map_contract(
                self.map_dispatch, self.map_process, self.map_post,
                by_name, self.route,
            )
        by_name_route = self.route.replace(
            "FindMapWidgetByClass(menu.GetLayoutRoot())",
            'MapWidget.Cast(menu.GetLayoutRoot().FindAnyWidget("Map"))',
            1,
        )
        self.assertNotEqual(by_name_route, self.route)
        with self.assertRaises(AssertionError):
            assert_ui_map_contract(
                self.map_dispatch, self.map_process, self.map_post,
                self.finder, by_name_route,
            )
        # A read job that counts as exclusive must fail: renamed away,
        # or counted but never subtracted.
        exclusive_body = _method_body(self.source, HAS_EXCLUSIVE)
        renamed = exclusive_body.replace(
            'CountOfKind("ui_menu")', 'CountOfKind("ui_menu_removed")', 1
        )
        with self.assertRaises(AssertionError):
            assert_exclusive_contract(renamed)
        not_subtracted = exclusive_body.replace(
            "blocking = blocking - uiMenuJobs;\n", "", 1
        )
        self.assertNotEqual(not_subtracted, exclusive_body)
        with self.assertRaises(AssertionError):
            assert_exclusive_contract(not_subtracted)

    def test_message_classes_and_prune_wiring(self) -> None:
        messages = _source(MESSAGES_PATH)
        ui_menu_cls = _class_body(messages, "MCPUiMenu")
        for field in ("menu", "open_before", "open_after", "class_name", "menu_id_before", "menu_id_after"):
            self.assertIn(field, ui_menu_cls)
        ui_map_cls = _class_body(messages, "MCPUiMap")
        for field in ("scale_before", "scale_after", "center_before", "center_after", "class_name"):
            self.assertIn(field, ui_map_cls)
        args_cls = _class_body(messages, "MCPArgs")
        for field in ("menu", "open", "scale", "center"):
            self.assertRegex(args_cls, rf"(?m)^\t.*\b{field};$")
        mcp_result = _class_body(messages, "MCPResult")
        self.assertRegex(mcp_result, r"(?m)^\tref MCPUiMenu ui_menu;$")
        self.assertRegex(mcp_result, r"(?m)^\tref MCPUiMap ui_map;$")
        mcp_job = _class_body(messages, "MCPJob")
        self.assertRegex(mcp_job, r"(?m)^\tref MCPUiMenu ui_menu;$")
        self.assertRegex(mcp_job, r"(?m)^\tref MCPUiMap ui_map;$")
        self.assertIn("ui_menu", PRUNABLE_FIELDS)
        self.assertIn("ui_map", PRUNABLE_FIELDS)
        filled_menu = {"menu": "map", "open_before": 0, "open_after": 1, "class_name": "MapMenu", "menu_id_before": -1, "menu_id_after": MENU_MAP_ID}
        self.assertEqual(
            prune_unfilled_fields(MENU_COMMAND, {"ok": 1, "ui_menu": filled_menu})["ui_menu"],
            filled_menu,
        )
        filled_map = {"scale_before": 0.33, "scale_after": 0.5, "center_before": [1.0, 2.0], "center_after": [3.0, 4.0], "class_name": "MapMenu"}
        self.assertEqual(
            prune_unfilled_fields(MAP_COMMAND, {"ok": 1, "ui_map": filled_map})["ui_map"],
            filled_map,
        )

    def test_server_bridge_does_not_announce_either_verb(self) -> None:
        self.assertNotIn(MENU_COMMAND, _source(SERVER_BRIDGE_PATH))
        self.assertNotIn(MAP_COMMAND, _source(SERVER_BRIDGE_PATH))
        fixture = json.loads(CENSUS_FIXTURE.read_text(encoding="utf-8"))
        for command in (MENU_COMMAND, MAP_COMMAND):
            self.assertNotIn(command, fixture["peers"]["server"])
            self.assertEqual(fixture["peers"]["client"].get(command), command)
            self.assertNotIn(command, _BRIDGE_COMMAND_TOOLS["server"])
            self.assertEqual(_BRIDGE_COMMAND_TOOLS["client"].get(command), command)
            self.assertNotIn(command, server.SERVER_ARG_CONTRACT)


class UiMenuContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        bind_both_peers(self.state)
        self.caps = poll_census_query("client")["caps"].split(",")

    def _poll(self, caps: list[str]) -> dict[str, Any]:
        status, body = self.state.record_poll(
            "client",
            "13~1.30",
            instance=INST_CLIENT,
            source_pid=PID_CLIENT,
            caps=",".join(caps),
        )
        self.assertEqual(status, 200, body)
        return body

    def test_both_commands_require_a_lease_like_ui_click(self) -> None:
        for command in (MENU_COMMAND, MAP_COMMAND):
            with self.subTest(command=command):
                self.assertIn(command, loopback.CLIENT_COMMANDS)
                self.assertNotIn(command, loopback.SERVER_COMMANDS)
                self.assertEqual(loopback.peer_for_command(command), "client")
                self.assertNotIn(command, READ_ONLY_COMMANDS)
                self.assertEqual(
                    command_requires_lease(command), command_requires_lease("ui_click")
                )
                self.assertTrue(command_requires_lease(command))

    def test_ingress_schema_for_ui_menu_set(self) -> None:
        self.assertEqual(
            loopback.validate_command_args(MENU_COMMAND, {"menu": "map", "open": True}),
            (True, None),
        )
        for bad in (
            {"open": True},
            {"menu": "map"},
            {"menu": "inventory", "open": True},
            {"menu": "", "open": True},
            {"menu": None, "open": True},
            {"menu": 1, "open": True},
            {"menu": "map", "open": 1},
            {"menu": "map", "open": 0},
            {"menu": "map", "open": "true"},
            {"menu": "map", "open": None},
            {"menu": "map", "open": True, "extra": 1},
        ):
            with self.subTest(args=bad):
                self.assertEqual(
                    loopback.validate_command_args(MENU_COMMAND, bad),
                    (False, "bad_args"),
                )

    def test_ingress_schema_for_ui_map_set(self) -> None:
        for good in (
            {"scale": 1.5},
            {"center": [100.0, 200.0]},
            {"scale": 0.5, "center": [100.0, 200.0]},
        ):
            with self.subTest(args=good):
                self.assertEqual(
                    loopback.validate_command_args(MAP_COMMAND, good), (True, None)
                )
        for bad in (
            {},
            {"scale": 0},
            {"scale": -1},
            {"scale": math.nan},
            {"scale": math.inf},
            {"scale": "1"},
            {"center": [100.0]},
            {"center": [1.0, 2.0, 3.0]},
            {"center": [1.0, math.inf]},
            {"center": [math.nan, 2.0]},
            {"scale": 1.0, "extra": 1},
        ):
            with self.subTest(args=bad):
                self.assertEqual(
                    loopback.validate_command_args(MAP_COMMAND, bad),
                    (False, "bad_args"),
                )

    def test_older_client_pbo_is_refused_and_current_client_receives_both(self) -> None:
        wire = {
            MENU_COMMAND: {"menu": "map", "open": True},
            MAP_COMMAND: {"scale": 1.0},
        }
        for command in (MENU_COMMAND, MAP_COMMAND):
            with self.subTest(command=command):
                older = [name for name in self.caps if name != command]
                self._poll(older)
                self.assertEqual(
                    self.state.enqueue_command(command, dict(wire[command])),
                    (409, {"error": "bridge_capability_missing"}),
                )
                self.assertEqual(bound_queue(self.state, "client"), [])
        self._poll(self.caps)
        for command in (MENU_COMMAND, MAP_COMMAND):
            with self.subTest(command=command):
                status, body = self.state.enqueue_command(command, dict(wire[command]))
                self.assertEqual((status, body["peer"], body["cmd"]), (200, "client", command))


class UiMenuToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def _fn(self, name: str):
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        self.assertIn(name, tools)
        return tools[name]

    async def test_registered_tools_schema_and_description(self) -> None:
        menu_tool = await self._fn(MENU_COMMAND)
        description = menu_tool.description or ""
        self.assertTrue(description.startswith(LEASE_TOOL_LINE), description)
        for phrase in (
            "UAMapToggle",
            "CloseMapMenu",
            "without vanilla's two gates",
            "idempotent",
            "the lease close closes a map this tool opened",
            "a map opened by the player or a mod stays open",
            "restore_gameplay does not close it",
            "ui_menu_busy",
            "bridge_capability_missing",
            "Only MENU_MAP is covered",
            "a class a mod creates for MENU_MAP reports its own",
            "a mod map opened with another menu id, such as Expansion's, "
            "is not seen by this tool",
        ):
            self.assertIn(phrase, description)
        self.assertNotIn("ExpansionMapMenu", description)
        schema = menu_tool.inputSchema
        self.assertEqual(set(schema["properties"]), {"menu", "open", "timeout_s"})
        self.assertEqual(set(schema.get("required") or []), {"menu", "open"})
        self.assertIs(schema.get("additionalProperties"), False)
        self.assertIn(MENU_COMMAND, server._CLOSED_SCHEMA_TOOLS)

        map_tool = await self._fn(MAP_COMMAND)
        map_description = map_tool.description or ""
        self.assertTrue(map_description.startswith(LEASE_TOOL_LINE), map_description)
        for phrase in (
            "SetScale and SetMapPos",
            "MapWidget",
            "ui_map_not_open",
            "ui_map_widget_missing",
            "ui_map_not_set",
            "bridge_capability_missing",
            "Only MENU_MAP is covered",
            "the lease close closes a map ui_menu_set opened",
            "a class a mod creates for MENU_MAP keeps working",
            "a mod map opened with another menu id, such as Expansion's, "
            "is not seen by this tool",
        ):
            self.assertIn(phrase, map_description)
        self.assertNotIn("ExpansionMapMenu", map_description)
        map_schema = map_tool.inputSchema
        self.assertEqual(set(map_schema["properties"]), {"scale", "center", "timeout_s"})
        self.assertEqual(map_schema.get("required") or [], [])
        self.assertIs(map_schema.get("additionalProperties"), False)
        self.assertIn(MAP_COMMAND, server._CLOSED_SCHEMA_TOOLS)

    async def test_ui_menu_set_forwards_open_and_close(self) -> None:
        for open_value in (True, False):
            with self.subTest(open=open_value):
                reply = {"ok": 1, "ui_menu": {"menu": "map", "open_after": 1 if open_value else 0}}
                call = AsyncMock(return_value=reply)
                with patch.object(self.runtime, "call_bridge", new=call):
                    raw = await self.app.call_tool(
                        MENU_COMMAND, {"menu": "map", "open": open_value}
                    )
                call.assert_awaited_once_with(
                    MENU_COMMAND,
                    {"menu": "map", "open": open_value},
                    "client",
                    DEFAULT_TOOL_TIMEOUT_S,
                )
                self.assertEqual(_tool_json(raw), reply)

    async def test_ui_map_set_forwards_scale_centre_and_both(self) -> None:
        for arguments, wire in (
            ({"scale": 1.5}, {"scale": 1.5}),
            ({"center": [100.0, 200.0]}, {"center": [100.0, 200.0]}),
            (
                {"scale": 0.5, "center": [100.0, 200.0], "timeout_s": 3.5},
                {"scale": 0.5, "center": [100.0, 200.0]},
            ),
        ):
            with self.subTest(arguments=arguments):
                call = AsyncMock(return_value={"ok": 1, "ui_map": {}})
                with patch.object(self.runtime, "call_bridge", new=call):
                    await self.app.call_tool(MAP_COMMAND, arguments)
                expected_timeout = arguments.get("timeout_s", DEFAULT_TOOL_TIMEOUT_S)
                call.assert_awaited_once_with(
                    MAP_COMMAND, wire, "client", expected_timeout
                )

    async def test_ui_menu_set_bad_args_never_reach_the_bridge(self) -> None:
        # Through the public entry (app.call_tool), not tool.fn: the
        # daemon's bad_args must survive FastMCP validation.
        call = AsyncMock(return_value={"ok": 1})
        bad_calls = (
            {"menu": "inventory", "open": True},
            {"menu": "", "open": True},
            {"menu": None, "open": True},
            {"menu": 1, "open": True},
            {"menu": "map", "open": 1},
            {"menu": "map", "open": 0},
            {"menu": "map", "open": "true"},
            {"menu": "map", "open": None},
            {"menu": "map", "open": True, "extra": 1},
        )
        with patch.object(self.runtime, "call_bridge", new=call):
            for arguments in bad_calls:
                with self.subTest(arguments=arguments):
                    with self.assertRaises(ToolError) as raised:
                        await self.app.call_tool(MENU_COMMAND, arguments)
                    self.assertIn("bad_args", str(raised.exception))
        call.assert_not_awaited()

    async def test_ui_map_set_bad_args_never_reach_the_bridge(self) -> None:
        call = AsyncMock(return_value={"ok": 1})
        bad_calls = (
            {},
            {"scale": 0},
            {"scale": -1},
            {"scale": math.nan},
            {"scale": math.inf},
            {"scale": "1"},
            {"scale": True},
            {"center": [100.0]},
            {"center": [1.0, 2.0, 3.0]},
            {"center": [1.0, math.inf]},
            {"center": [math.nan, 2.0]},
            {"center": [True, 200.0]},
            {"center": ["100", 200.0]},
            {"scale": 0.5, "centre": [100.0, 200.0]},
        )
        with patch.object(self.runtime, "call_bridge", new=call):
            for arguments in bad_calls:
                with self.subTest(arguments=arguments):
                    with self.assertRaises(ToolError) as raised:
                        await self.app.call_tool(MAP_COMMAND, arguments)
                    self.assertIn("bad_args", str(raised.exception))
        call.assert_not_awaited()


# --- Executed bridge model (r9) -------------------------------------------
# The text contracts above cannot see a deleted assignment or Log line
# whose token still appears elsewhere. The model below translates the
# real bodies of DispatchUiMenuSet, ProcessUiMenuJob, PostUiMenuJob,
# FillUiMenuRead, UiMenuId, DispatchUiMapSet, ProcessUiMapJob,
# PostUiMapJob, FillUiMapRead and UiMapMatches (plus UiMapWidget,
# FindMapWidgetByClass, StopLeaseCloseMap, MaintainMCPMapMenu and
# IsFiniteFloat) with
# tests.enforce_subset_helpers.translate and runs them against doubles,
# asserting reply fields, double calls, error codes and Log lines.
# Two call shapes the translator cannot render are rewritten in memory
# only, outside string literals, exactly as loadout_helpers rewrites
# its out-parameter callees: UiMenuId's out int becomes an adapter that
# runs the translated UiMenuId, and UiMapWidget's out menu becomes
# UiMapWidgetOut plus LastUiMapMenu. {"map"} becomes MAP_EXCLUDES (the
# translator has no array literal), as in _map_close_model above.


class _LogStr(EnforceString):
    """EnforceString with Enforce's implicit number-to-string on +."""

    __slots__ = ()

    def __add__(self, other: Any) -> "_LogStr":
        if isinstance(other, EnforceString):
            return _LogStr(self.data + other.data)
        if isinstance(other, bool):
            return _LogStr(self.data + (b"true" if other else b"false"))
        if isinstance(other, (int, float)):
            return _LogStr(self.data + repr(other).encode("ascii"))
        return NotImplemented


UI_MENU_ID_FN = "protected bool UiMenuId(string menu, out int menuId)"
FILL_MENU_READ_FN = "protected void FillUiMenuRead(MCPUiMenu reply, int menuId)"
FILL_MAP_READ_FN = "protected void FillUiMapRead(MCPUiMap reply, MapWidget widget)"
UI_MAP_MATCHES_FN = "protected bool UiMapMatches(MCPUiMap reply, MCPJob job)"
IS_FINITE_FN = "protected bool IsFiniteFloat(float value)"
_EXEC_CLASS_TYPES = (
    "MissionGameplay", "UIManager", "UIScriptedMenu", "MapMenu", "Widget",
    "MapWidget", "MCPUiMenu", "MCPUiMap", "MCPJob", "MCPResult", "MCPCommand",
)
_EXEC_FUNCTIONS = (
    UI_MENU_ID_FN, FILL_MENU_READ_FN, DISPATCH_UI_MENU, PROCESS_UI_MENU,
    POST_UI_MENU, FIND_WIDGET, WIDGET_ROUTE, FILL_MAP_READ_FN,
    UI_MAP_MATCHES_FN, DISPATCH_UI_MAP, PROCESS_UI_MAP, POST_UI_MAP,
    STOP_MAP, MAINTAIN_MAP, IS_FINITE_FN,
)
_FLOAT_UNSET = 3.4028234663852886e38


def _txt(value: Any) -> str:
    return value.text if isinstance(value, EnforceString) else str(value)


def _standalone_exec(source: str, signature: str) -> str:
    body = enforce_method_body(source, signature)
    body = body.replace(
        "if (!command.args || !UiMenuId(command.args.menu, menuId))",
        "menuId = UiMenuIdFromArgs(command.args); if (menuId == -1)",
    )
    body = body.replace(
        "UiMenuId(reply.menu, menuId);", "menuId = UiMenuIdFromMenu(reply.menu);"
    )
    body = body.replace(
        'UiMenuId("map", menuId);', 'menuId = UiMenuIdFromMenu("map");'
    )
    body = body.replace(
        "widget = UiMapWidget(ui, menuId, menu);",
        "widget = UiMapWidgetOut(ui, menuId); menu = LastUiMapMenu();",
    )
    body = body.replace('{"map"}', "MAP_EXCLUDES")
    return signature + "\n{\n" + body + "\n}\n"


class _ExecMission:
    def __init__(self) -> None:
        self.exclude_active = False
        self.restriction_active = False
        self.added_excludes: list[str] = []
        self.added_restrictions: list[int] = []
        self.removed_excludes: list[str] = []
        self.removed_restrictions: list[int] = []

    def AddActiveInputExcludes(self, excludes: Any) -> None:
        self.added_excludes.append(_txt(list(excludes)[0]))
        self.exclude_active = True

    def AddActiveInputRestriction(self, restrictor: Any) -> None:
        self.added_restrictions.append(int(restrictor))
        self.restriction_active = True

    def IsInputExcludeActive(self, name: Any) -> bool:
        return self.exclude_active

    def RemoveActiveInputExcludes(self, excludes: Any) -> None:
        self.removed_excludes.append(_txt(list(excludes)[0]))
        self.exclude_active = False

    def IsInputRestrictionActive(self, restrictor: Any) -> bool:
        return self.restriction_active

    def RemoveActiveInputRestriction(self, restrictor: Any) -> None:
        self.removed_restrictions.append(int(restrictor))
        self.restriction_active = False


class _ExecGame:
    def __init__(self) -> None:
        self.player: Any = object()
        self.dedicated = False
        self.mission: Any = None
        self.ui: Any = None

    def GetPlayer(self) -> Any:
        return self.player

    def IsDedicatedServer(self) -> bool:
        return self.dedicated

    def GetMission(self) -> Any:
        return self.mission

    def GetUIManager(self) -> Any:
        return self.ui


class _ExecMenu:
    def __init__(
        self,
        ui: "_ExecUi",
        menu_id: int = MENU_MAP_ID,
        class_name: str = "MapMenu",
        root: Any = None,
        close_mode: str = "effective",
    ) -> None:
        self._ui = ui
        self._id = menu_id
        self._class_name = class_name
        self._root = root
        self.close_mode = close_mode
        self.close_calls = 0
        self.close_clears: tuple[bool, bool] | None = None
        self.mission: _ExecMission | None = None

    def GetID(self) -> int:
        return self._id

    def ClassName(self) -> EnforceString:
        return EnforceString.of(self._class_name)

    def GetLayoutRoot(self) -> Any:
        return self._root

    def CloseMapMenu(self) -> None:
        self.close_calls += 1
        if self.close_clears is not None and self.mission is not None:
            if self.close_clears[0]:
                self.mission.exclude_active = False
            if self.close_clears[1]:
                self.mission.restriction_active = False
        if self.close_mode == "effective":
            self._ui._remove(self)
        elif self.close_mode == "deferred":
            self._ui.pending_close = self
        elif self.close_mode != "rejected":
            raise AssertionError(f"unknown close mode {self.close_mode!r}")


class _ExecUi:
    def __init__(self) -> None:
        self.menus: dict[int, _ExecMenu] = {}
        self.top: _ExecMenu | None = None
        self.entered: list[tuple[int, Any]] = []
        self.enter_mode = "instant"
        self.enter_class_name = "MapMenu"
        self.pending_enter: _ExecMenu | None = None
        self.pending_close: _ExecMenu | None = None
        self.force_open_ids: set[int] = set()

    def install(self, menu: _ExecMenu) -> _ExecMenu:
        self.menus[menu.GetID()] = menu
        self.top = menu
        return menu

    def _remove(self, menu: _ExecMenu) -> None:
        if self.menus.get(menu.GetID()) is menu:
            del self.menus[menu.GetID()]
        if self.top is menu:
            self.top = None

    def complete_pending(self) -> None:
        if self.pending_enter is not None:
            self.install(self.pending_enter)
            self.pending_enter = None
        if self.pending_close is not None:
            self._remove(self.pending_close)
            self.pending_close = None

    def IsMenuOpen(self, menu_id: int) -> bool:
        return menu_id in self.menus or menu_id in self.force_open_ids

    def GetMenu(self) -> _ExecMenu | None:
        return self.top

    def FindMenu(self, menu_id: int) -> _ExecMenu | None:
        return self.menus.get(menu_id)

    def EnterScriptedMenu(self, menu_id: int, parent: Any) -> None:
        self.entered.append((menu_id, parent))
        if self.enter_mode == "instant":
            self.install(_ExecMenu(self, menu_id, self.enter_class_name))
        elif self.enter_mode == "deferred":
            self.pending_enter = _ExecMenu(self, menu_id, self.enter_class_name)
        elif self.enter_mode != "never":
            raise AssertionError(f"unknown enter mode {self.enter_mode!r}")


class _ExecMapWidget(_RouteWidget):
    def __init__(self, mode: str = "instant") -> None:
        super().__init__()
        self.mode = mode
        self.scale = 0.33
        self.pos = [10.0, 0.0, 20.0]
        self.set_scale_calls: list[float] = []
        self.set_pos_calls: list[list[float]] = []
        self._pending_scale: float | None = None
        self._pending_pos: list[float] | None = None

    def GetScale(self) -> float:
        return self.scale

    def GetMapPos(self) -> Any:
        return EnforceVector(self.pos)

    def SetScale(self, value: float) -> None:
        self.set_scale_calls.append(float(value))
        if self.mode == "clamp":
            self.scale = min(float(value), 0.4)
        elif self.mode == "pending":
            self._pending_scale = float(value)
        elif self.mode == "ignore":
            pass
        else:
            self.scale = float(value)

    def SetMapPos(self, value: Any) -> None:
        pos = [float(value[0]), float(value[1]), float(value[2])]
        self.set_pos_calls.append(pos)
        if self.mode == "offset":
            self.pos = [pos[0] + 2.0, pos[1], pos[2]]
        elif self.mode == "pending":
            self._pending_pos = pos
        elif self.mode == "ignore":
            pass
        else:
            self.pos = pos

    def apply_pending(self) -> None:
        if self._pending_scale is not None:
            self.scale = self._pending_scale
            self._pending_scale = None
        if self._pending_pos is not None:
            self.pos = self._pending_pos
            self._pending_pos = None


class _ExecRunner:
    def __init__(self) -> None:
        self.elapsed = 100.0
        self.jobs: list[Any] = []

    def GetElapsedS(self) -> float:
        return self.elapsed

    def AddJob(self, job: Any) -> None:
        self.jobs.append(job)


class _ExecWorld:
    def __init__(self) -> None:
        self.game = _ExecGame()
        self.game_present = True
        self.mission = _ExecMission()
        self.game.mission = self.mission
        self.ui = _ExecUi()
        self.game.ui = self.ui
        self.runner = _ExecRunner()
        self.logs: list[str] = []
        self.posted: list[Any] = []
        self.ns: dict[str, Any] = {}


def _exec_model(source: str, messages: str) -> _ExecWorld:
    world = _ExecWorld()
    ns: dict[str, Any] = world.ns
    holder: dict[str, Any] = {}
    ns.update(
        {
            "_S": _LogStr,
            "GetGame": lambda: world.game if world.game_present else None,
            "MissionGameplay": SimpleNamespace(
                Cast=lambda v: v if isinstance(v, _ExecMission) else None
            ),
            "MapMenu": SimpleNamespace(
                Cast=lambda v: v if isinstance(v, _ExecMenu) else None
            ),
            "MapWidget": SimpleNamespace(
                Cast=lambda v: v if isinstance(v, _ExecMapWidget) else None
            ),
            "EInputRestrictors": SimpleNamespace(MAP=7),
            "MENU_MAP": MENU_MAP_ID,
            "MAP_EXCLUDES": EnforceArray([EnforceString.of("map")]),
            "CAMERA_JOB_TIMEOUT_S": 5.0,
            "MCP_ARG_FLOAT_UNSET": _FLOAT_UNSET,
            "Vector": lambda x, y, z: EnforceVector((x, y, z)),
            "array": EnforceArray,
            "Log": lambda line: world.logs.append(_txt(line)),
            "PostResult": lambda result: world.posted.append(result),
            "m_MCPMapMenu": None,
            "m_JobRunner": world.runner,
        }
    )
    for class_name in ("MCPUiMenu", "MCPUiMap", "MCPResult", "MCPJob"):
        fake_class(messages, class_name, ns, constants={"MCP_ARG_FLOAT_UNSET": _FLOAT_UNSET})

    def _from_menu(menu: Any) -> int:
        ok, menu_id = ns["UiMenuId"](menu)
        return int(menu_id) if ok else -1

    def _from_args(args: Any) -> int:
        if args is None:
            return -1
        return _from_menu(args.menu)

    def _widget_out(ui: Any, menu_id: int) -> Any:
        widget, menu = ns["UiMapWidget"](ui, menu_id)
        holder["menu"] = menu
        return widget

    ns["UiMenuIdFromMenu"] = _from_menu
    ns["UiMenuIdFromArgs"] = _from_args
    ns["UiMapWidgetOut"] = _widget_out
    ns["LastUiMapMenu"] = lambda: holder.get("menu")
    # IsFiniteFloat is not substituted here: its real body is translated
    # below with the other functions. It only rejects NaN (MCPClientBridge.c
    # IsFiniteFloat: `if (value != value) return false; return true;`), so
    # +inf passes it; the old math.isfinite substitution rejected +inf too.
    for signature in _EXEC_FUNCTIONS:
        translate(
            _standalone_exec(source, signature),
            signature,
            ns,
            class_types=_EXEC_CLASS_TYPES,
            members=("m_MCPMapMenu", "m_JobRunner"),
        )
    return world


def _exec_args(
    menu: str = "map",
    open_value: bool = True,
    scale: float = _FLOAT_UNSET,
    center: Any = None,
) -> Any:
    return SimpleNamespace(
        menu=EnforceString.of(menu),
        open=open_value,
        scale=scale,
        center=EnforceArray(list(center)) if center is not None else EnforceArray(),
    )


def _exec_command(args: Any, command_id: int = 42) -> Any:
    return SimpleNamespace(id=command_id, args=args)


def _exec_result(world: _ExecWorld) -> Any:
    result = world.ns["MCPResult"]()
    result.ok = True
    result.error = EnforceString.of("stale")
    result.tick_poll_sent = 11
    result.tick_poll_callback = 22
    result.tick_dispatch = 33
    return result


def _map_layout(world: _ExecWorld, widget_mode: str = "instant", with_widget: bool = True):
    widget = _ExecMapWidget(widget_mode) if with_widget else None
    children = [c for c in (_RouteWidget(), widget) if c is not None]
    root = _RouteWidget([_RouteWidget(children)])
    menu = world.ui.install(_ExecMenu(world.ui, MENU_MAP_ID, "MapMenu", root=root))
    return menu, widget


class UiMenuExecutedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(CLIENT_PATH)
        self.messages = _source(MESSAGES_PATH)
        self.world = _exec_model(self.source, self.messages)
        self.ns = self.world.ns

    def _menu_dispatch(self, args: Any):
        result = _exec_result(self.world)
        returned = self.ns["DispatchUiMenuSet"](_exec_command(args), result)
        return returned, result

    def _map_dispatch(self, args: Any):
        result = _exec_result(self.world)
        returned = self.ns["DispatchUiMapSet"](_exec_command(args), result)
        return returned, result

    # -- UiMenuId / FillUiMenuRead, executed directly --------------------
    def test_ui_menu_id_and_fill_read_executed(self) -> None:
        self.assertEqual(
            self.ns["UiMenuId"](EnforceString.of("map")), (True, MENU_MAP_ID)
        )
        self.assertEqual(
            self.ns["UiMenuId"](EnforceString.of("inventory")), (False, -1)
        )
        self.assertEqual(self.ns["UiMenuId"](EnforceString.of("")), (False, -1))

        reply = self.ns["MCPUiMenu"]()
        reply.open_after = True
        reply.class_name = EnforceString.of("stale")
        reply.menu_id_after = 99
        # No game: the read resets to closed/""/-1 and touches nothing.
        self.world.game_present = False
        self.ns["FillUiMenuRead"](reply, MENU_MAP_ID)
        self.assertFalse(reply.open_after)
        self.assertEqual(_txt(reply.class_name), "")
        self.assertEqual(reply.menu_id_after, -1)
        # Game but no UI manager: same reset, no crash.
        self.world.game_present = True
        self.world.game.ui = None
        reply.open_after = True
        reply.menu_id_after = 99
        self.ns["FillUiMenuRead"](reply, MENU_MAP_ID)
        self.assertFalse(reply.open_after)
        self.assertEqual(reply.menu_id_after, -1)
        # Open map on top: all three fields come from the doubles.
        self.world.game.ui = self.world.ui
        menu = self.world.ui.install(
            _ExecMenu(self.world.ui, MENU_MAP_ID, "ModMapMenu")
        )
        self.ns["FillUiMenuRead"](reply, MENU_MAP_ID)
        self.assertTrue(reply.open_after)
        self.assertEqual(_txt(reply.class_name), "ModMapMenu")
        self.assertEqual(reply.menu_id_after, MENU_MAP_ID)
        # Open but not on top (another menu above): id is the top's, and
        # the class still comes from FindMenu.
        pause = _ExecMenu(self.world.ui, 9, "PauseMenu")
        self.world.ui.install(pause)
        self.world.ui.menus[MENU_MAP_ID] = menu
        self.ns["FillUiMenuRead"](reply, MENU_MAP_ID)
        self.assertTrue(reply.open_after)
        self.assertEqual(reply.menu_id_after, 9)
        self.assertEqual(_txt(reply.class_name), "ModMapMenu")
        # Closed: class stays "" even though no menu is found.
        self.world.ui.menus.clear()
        self.world.ui.top = None
        self.ns["FillUiMenuRead"](reply, MENU_MAP_ID)
        self.assertFalse(reply.open_after)
        self.assertEqual(_txt(reply.class_name), "")
        self.assertEqual(reply.menu_id_after, -1)

    # -- DispatchUiMenuSet, executed over its branches -------------------
    def test_ui_menu_dispatch_branches_executed(self) -> None:
        # bad_args: unknown menu and missing args, before any game check.
        for args in (_exec_args(menu="inventory"), None):
            with self.subTest(args=args):
                self.world = _exec_model(self.source, self.messages)
                self.ns = self.world.ns
                self.world.game_present = False
                returned, result = self._menu_dispatch(args)
                self.assertTrue(returned)
                self.assertFalse(result.ok)
                self.assertEqual(_txt(result.error), "bad_args")
                self.assertEqual(self.world.logs, ["ui_menu_set ok=0 error=bad_args"])
                self.assertEqual(self.world.ui.entered, [])
        # client_not_in_game: no game, no player, dedicated server.
        for mutate in ("absent", "no_player", "dedicated"):
            with self.subTest(mutate=mutate):
                self.world = _exec_model(self.source, self.messages)
                self.ns = self.world.ns
                if mutate == "absent":
                    self.world.game_present = False
                elif mutate == "no_player":
                    self.world.game.player = None
                else:
                    self.world.game.dedicated = True
                returned, result = self._menu_dispatch(_exec_args())
                self.assertTrue(returned)
                self.assertEqual(_txt(result.error), "client_not_in_game")
                self.assertEqual(
                    self.world.logs, ["ui_menu_set ok=0 error=client_not_in_game"]
                )
        # no_mission: the mission cast refuses a foreign mission object.
        self.world = _exec_model(self.source, self.messages)
        self.ns = self.world.ns
        self.world.game.mission = object()
        returned, result = self._menu_dispatch(_exec_args())
        self.assertTrue(returned)
        self.assertEqual(_txt(result.error), "no_mission")
        self.assertEqual(self.world.logs, ["ui_menu_set ok=0 error=no_mission"])

    def test_ui_menu_open_close_and_busy_executed(self) -> None:
        # Open, immediate: EnterScriptedMenu, both input states, mark.
        returned, result = self._menu_dispatch(_exec_args(open_value=True))
        self.assertTrue(returned)
        self.assertTrue(result.ok)
        self.assertEqual(self.world.ui.entered, [(MENU_MAP_ID, None)])
        self.assertEqual(self.world.mission.added_excludes, ["map"])
        self.assertEqual(self.world.mission.added_restrictions, [7])
        self.assertIs(self.ns["m_MCPMapMenu"], self.world.ui.menus[MENU_MAP_ID])
        reply = result.ui_menu
        self.assertEqual(_txt(reply.menu), "map")
        self.assertFalse(reply.open_before)
        self.assertTrue(reply.open_after)
        self.assertEqual(_txt(reply.class_name), "MapMenu")
        self.assertEqual(reply.menu_id_before, -1)
        self.assertEqual(reply.menu_id_after, MENU_MAP_ID)
        self.assertEqual(self.world.runner.jobs, [])
        self.assertEqual(
            self.world.logs,
            [f"ui_menu_set menu=map before=-1 after={MENU_MAP_ID} ok=1"],
        )
        # Idempotent open: no second Enter, no second excludes.
        returned, result = self._menu_dispatch(_exec_args(open_value=True))
        self.assertTrue(returned)
        self.assertTrue(result.ok)
        self.assertTrue(result.ui_menu.open_before)
        self.assertEqual(len(self.world.ui.entered), 1)
        self.assertEqual(self.world.mission.added_excludes, ["map"])
        # Busy: the pause menu is on top while the map is closed.
        world = _exec_model(self.source, self.messages)
        world.ui.install(_ExecMenu(world.ui, 9, "PauseMenu"))
        result = _exec_result(world)
        returned = world.ns["DispatchUiMenuSet"](
            _exec_command(_exec_args(open_value=True)), result
        )
        self.assertTrue(returned)
        self.assertFalse(result.ok)
        self.assertEqual(_txt(result.error), "ui_menu_busy")
        self.assertEqual(result.ui_menu.menu_id_before, 9)
        self.assertEqual(result.ui_menu.menu_id_after, 9)
        self.assertFalse(result.ui_menu.open_after)
        self.assertEqual(world.ui.entered, [])
        self.assertEqual(
            world.logs,
            ["ui_menu_set menu=map before=9 after=9 ok=0 error=ui_menu_busy"],
        )
        # Idempotent close with nothing open: removed=none, no Close call.
        world = _exec_model(self.source, self.messages)
        result = _exec_result(world)
        returned = world.ns["DispatchUiMenuSet"](
            _exec_command(_exec_args(open_value=False)), result
        )
        self.assertTrue(returned)
        self.assertTrue(result.ok)
        self.assertEqual(
            world.logs,
            ["ui_menu_set menu=map before=-1 after=-1 ok=1 removed=none"],
        )

    def test_ui_menu_close_variants_executed(self) -> None:
        def closed_world(close_mode="effective", clears=None, marked=True):
            world = _exec_model(self.source, self.messages)
            menu = world.ui.install(
                _ExecMenu(world.ui, MENU_MAP_ID, "MapMenu", close_mode=close_mode)
            )
            menu.mission = world.mission
            menu.close_clears = clears
            world.mission.exclude_active = True
            world.mission.restriction_active = True
            world.ns["m_MCPMapMenu"] = menu if marked else None
            return world, menu

        # Tool-owned, CloseMapMenu removed neither state: both removed
        # here, mark falls, removed names both.
        world, menu = closed_world()
        result = _exec_result(world)
        returned = world.ns["DispatchUiMenuSet"](
            _exec_command(_exec_args(open_value=False)), result
        )
        self.assertTrue(returned)
        self.assertTrue(result.ok)
        self.assertEqual(menu.close_calls, 1)
        self.assertEqual(world.mission.removed_excludes, ["map"])
        self.assertEqual(world.mission.removed_restrictions, [7])
        self.assertIsNone(world.ns["m_MCPMapMenu"])
        self.assertEqual(
            world.logs,
            ["ui_menu_set menu=map before=22 after=-1 ok=1 "
             "removed=map_exclude+map_restriction"],
        )
        # CloseMapMenu already removed the exclude: only the restriction
        # is removed here and named (kills the removed=map_restriction
        # assignment and the initial removed="none").
        world, menu = closed_world(clears=(True, False))
        result = _exec_result(world)
        world.ns["DispatchUiMenuSet"](
            _exec_command(_exec_args(open_value=False)), result
        )
        self.assertTrue(result.ok)
        self.assertEqual(world.mission.removed_excludes, [])
        self.assertEqual(world.mission.removed_restrictions, [7])
        self.assertTrue(world.logs[0].endswith("removed=map_restriction"), world.logs)
        # CloseMapMenu removed both: nothing removed here, removed=none.
        world, menu = closed_world(clears=(True, True))
        result = _exec_result(world)
        world.ns["DispatchUiMenuSet"](
            _exec_command(_exec_args(open_value=False)), result
        )
        self.assertTrue(result.ok)
        self.assertEqual(world.mission.removed_excludes, [])
        self.assertTrue(world.logs[0].endswith("removed=none"), world.logs)
        # Foreign map: closed through CloseMapMenu only, no removals.
        world, menu = closed_world(marked=False)
        foreign_mark = _ExecMenu(world.ui, MENU_MAP_ID, "Other")
        world.ns["m_MCPMapMenu"] = foreign_mark
        result = _exec_result(world)
        world.ns["DispatchUiMenuSet"](
            _exec_command(_exec_args(open_value=False)), result
        )
        self.assertTrue(result.ok)
        self.assertEqual(menu.close_calls, 1)
        self.assertEqual(world.mission.removed_excludes, [])
        self.assertEqual(world.mission.removed_restrictions, [])
        self.assertIs(world.ns["m_MCPMapMenu"], foreign_mark)
        self.assertTrue(world.logs[0].endswith("removed=none"), world.logs)

    def test_ui_menu_deferred_read_and_post_executed(self) -> None:
        # Deferred open: dispatch queues the one-tick job with every
        # field; Process waits for the next tick; Post reads success.
        self.world.ui.enter_mode = "deferred"
        returned, result = self._menu_dispatch(_exec_args(open_value=True))
        self.assertFalse(returned)
        self.assertEqual(len(self.world.runner.jobs), 1)
        job = self.world.runner.jobs[0]
        self.assertEqual(job.id, 42)
        self.assertEqual(_txt(job.kind), "ui_menu")
        self.assertTrue(job.ui_menu_open)
        self.assertEqual(_txt(job.ui_menu_removed), "none")
        self.assertIs(job.ui_menu, result.ui_menu if result.ui_menu else job.ui_menu)
        self.assertEqual(job.sample_start_s, 100.0)
        self.assertEqual(job.deadline_s, 105.0)
        self.assertEqual(
            (job.tick_poll_sent, job.tick_poll_callback, job.tick_dispatch),
            (11, 22, 33),
        )
        self.assertFalse(self.ns["ProcessUiMenuJob"](job))
        self.assertFalse(self.ns["ProcessUiMenuJob"](None))
        self.world.ui.complete_pending()
        self.world.runner.elapsed = 100.5
        self.assertTrue(self.ns["ProcessUiMenuJob"](job))
        self.ns["PostUiMenuJob"](job)
        self.assertEqual(len(self.world.posted), 1)
        posted = self.world.posted[0]
        self.assertEqual(posted.id, 42)
        self.assertEqual(
            (posted.tick_poll_sent, posted.tick_poll_callback, posted.tick_dispatch),
            (11, 22, 33),
        )
        self.assertTrue(posted.ok)
        self.assertTrue(posted.ui_menu.open_after)
        self.assertEqual(_txt(posted.ui_menu.class_name), "MapMenu")
        self.assertEqual(
            self.world.logs[-1],
            f"ui_menu_set menu=map before=-1 after={MENU_MAP_ID} ok=1",
        )
        # Never opens: Post answers ui_menu_not_opened.
        world = _exec_model(self.source, self.messages)
        world.ui.enter_mode = "never"
        result = _exec_result(world)
        returned = world.ns["DispatchUiMenuSet"](
            _exec_command(_exec_args(open_value=True)), result
        )
        self.assertFalse(returned)
        world.ns["PostUiMenuJob"](world.runner.jobs[0])
        posted = world.posted[0]
        self.assertFalse(posted.ok)
        self.assertEqual(_txt(posted.error), "ui_menu_not_opened")
        self.assertEqual(
            world.logs[-1],
            "ui_menu_set menu=map before=-1 after=-1 ok=0 "
            "error=ui_menu_not_opened",
        )
        # Deferred close: mark and removed travel on the job; Post logs
        # removed. Rejected close answers ui_menu_not_closed.
        for mode, want_ok, want_error in (
            ("deferred", True, ""),
            ("rejected", False, "ui_menu_not_closed"),
        ):
            with self.subTest(mode=mode):
                world = _exec_model(self.source, self.messages)
                menu = world.ui.install(
                    _ExecMenu(world.ui, MENU_MAP_ID, "MapMenu", close_mode=mode)
                )
                world.mission.exclude_active = True
                world.mission.restriction_active = True
                world.ns["m_MCPMapMenu"] = menu
                result = _exec_result(world)
                returned = world.ns["DispatchUiMenuSet"](
                    _exec_command(_exec_args(open_value=False)), result
                )
                self.assertFalse(returned)
                job = world.runner.jobs[0]
                self.assertFalse(job.ui_menu_open)
                self.assertEqual(
                    _txt(job.ui_menu_removed), "map_exclude+map_restriction"
                )
                self.assertIs(world.ns["m_MCPMapMenu"], menu)
                world.ui.complete_pending()
                world.ns["PostUiMenuJob"](job)
                posted = world.posted[0]
                self.assertEqual(posted.ok, want_ok)
                if want_ok:
                    self.assertEqual(
                        world.logs[-1],
                        "ui_menu_set menu=map before=22 after=-1 ok=1 "
                        "removed=map_exclude+map_restriction",
                    )
                else:
                    self.assertEqual(_txt(posted.error), want_error)
                    self.assertEqual(
                        world.logs[-1],
                        "ui_menu_set menu=map before=22 after=22 ok=0 "
                        "error=ui_menu_not_closed "
                        "removed=map_exclude+map_restriction",
                    )
        # Aborted job: its error is posted, with removed on a close and
        # without it on an open; an empty removed reads as none.
        for open_value, removed, suffix in (
            (False, "map_exclude", " removed=map_exclude"),
            (False, "", " removed=none"),
            (True, "map_exclude", ""),
        ):
            with self.subTest(open=open_value, removed=removed):
                world = _exec_model(self.source, self.messages)
                job = world.ns["MCPJob"]()
                job.id = 7
                job.ui_menu = world.ns["MCPUiMenu"]()
                job.ui_menu.menu = EnforceString.of("map")
                job.ui_menu_open = open_value
                job.ui_menu_removed = EnforceString.of(removed)
                job.error = EnforceString.of("timeout")
                world.ns["PostUiMenuJob"](job)
                posted = world.posted[0]
                self.assertFalse(posted.ok)
                self.assertEqual(_txt(posted.error), "timeout")
                self.assertTrue(
                    world.logs[-1].endswith("ok=0 error=timeout" + suffix),
                    world.logs,
                )
        # Post with no job and with a job carrying no reply.
        posted_before = len(self.world.posted)
        self.ns["PostUiMenuJob"](None)
        self.assertEqual(len(self.world.posted), posted_before)
        job = self.ns["MCPJob"]()
        job.id = 8
        job.ui_menu_open = True
        self.ns["PostUiMenuJob"](job)
        posted = self.world.posted[-1]
        self.assertIsNotNone(posted.ui_menu)
        self.assertEqual(_txt(posted.error), "ui_menu_not_opened")
        # No UI manager at dispatch: before reads -1 and the open is
        # queued for the next-tick read instead of crashing.
        world = _exec_model(self.source, self.messages)
        world.game.ui = None
        result = _exec_result(world)
        returned = world.ns["DispatchUiMenuSet"](
            _exec_command(_exec_args(open_value=True)), result
        )
        self.assertFalse(returned)
        # The deferred path leaves result.ui_menu null: DispatchUiMenuSet
        # assigns result.ui_menu only on its immediate, idempotent and
        # busy returns, while a queued read carries the reply on
        # job.ui_menu (job = new MCPJob(); job.ui_menu = reply) and
        # PostUiMenuJob posts it later. The before read therefore lives
        # on the job's reply, not on result.
        self.assertIsNone(result.ui_menu)
        self.assertEqual(world.logs, [])
        self.assertEqual(len(world.runner.jobs), 1)
        job = world.runner.jobs[0]
        self.assertEqual(job.id, 42)
        self.assertEqual(_txt(job.kind), "ui_menu")
        self.assertIsNotNone(job.ui_menu)
        self.assertEqual(_txt(job.ui_menu.menu), "map")
        self.assertEqual(job.ui_menu.menu_id_before, -1)
        self.assertFalse(job.ui_menu.open_before)
        self.assertFalse(job.ui_menu.open_after)
        self.assertEqual(job.ui_menu.menu_id_after, -1)
        self.assertEqual(_txt(job.ui_menu.class_name), "")
        self.assertTrue(job.ui_menu_open)
        self.assertEqual(_txt(job.ui_menu_removed), "none")
        self.assertEqual(job.sample_start_s, 100.0)
        self.assertEqual(job.deadline_s, 105.0)
        self.assertEqual(
            (job.tick_poll_sent, job.tick_poll_callback, job.tick_dispatch),
            (11, 22, 33),
        )

    # -- DispatchUiMapSet, executed over its branches --------------------
    def test_ui_map_dispatch_branches_executed(self) -> None:
        # +inf is not bad_args at the bridge: the real IsFiniteFloat only
        # rejects NaN. Its cases are in
        # test_r11_ui_map_nonfinite_bridge_behaviour_executed below.
        bad = (
            _exec_args(scale=0.0), _exec_args(scale=-1.0),
            _exec_args(scale=math.nan),
            _exec_args(center=[1.0]), _exec_args(center=[1.0, 2.0, 3.0]),
            _exec_args(center=[math.nan, 2.0]),
            _exec_args(), None,
        )
        for args in bad:
            with self.subTest(args=args):
                world = _exec_model(self.source, self.messages)
                result = _exec_result(world)
                returned = world.ns["DispatchUiMapSet"](
                    _exec_command(args), result
                )
                self.assertTrue(returned)
                self.assertFalse(result.ok)
                self.assertEqual(_txt(result.error), "bad_args")
                self.assertEqual(world.logs, ["ui_map_set ok=0 error=bad_args"])
        for mutate, error in (
            ("absent", "client_not_in_game"),
            ("foreign_mission", "no_mission"),
        ):
            with self.subTest(mutate=mutate):
                world = _exec_model(self.source, self.messages)
                if mutate == "absent":
                    world.game_present = False
                else:
                    world.game.mission = object()
                result = _exec_result(world)
                world.ns["DispatchUiMapSet"](
                    _exec_command(_exec_args(scale=1.0)), result
                )
                self.assertEqual(_txt(result.error), error)
                self.assertEqual(world.logs, [f"ui_map_set ok=0 error={error}"])
        # Not open (closed map, and no UI manager) and widget missing.
        world = _exec_model(self.source, self.messages)
        result = _exec_result(world)
        world.ns["DispatchUiMapSet"](_exec_command(_exec_args(scale=1.0)), result)
        self.assertEqual(_txt(result.error), "ui_map_not_open")
        self.assertIsNotNone(result.ui_map)
        world = _exec_model(self.source, self.messages)
        world.game.ui = None
        result = _exec_result(world)
        world.ns["DispatchUiMapSet"](_exec_command(_exec_args(scale=1.0)), result)
        self.assertEqual(_txt(result.error), "ui_map_not_open")
        world = _exec_model(self.source, self.messages)
        _map_layout(world, with_widget=False)
        result = _exec_result(world)
        world.ns["DispatchUiMapSet"](_exec_command(_exec_args(scale=1.0)), result)
        self.assertEqual(_txt(result.error), "ui_map_widget_missing")
        self.assertEqual(_txt(result.ui_map.class_name), "MapMenu")
        self.assertEqual(world.logs, ["ui_map_set ok=0 error=ui_map_widget_missing"])
        # Widget missing with FindMenu refusing: class stays "".
        world = _exec_model(self.source, self.messages)
        world.ui.force_open_ids.add(MENU_MAP_ID)
        result = _exec_result(world)
        world.ns["DispatchUiMapSet"](_exec_command(_exec_args(scale=1.0)), result)
        self.assertEqual(_txt(result.error), "ui_map_widget_missing")
        self.assertEqual(_txt(result.ui_map.class_name), "")

    # -- Round 11: the real IsFiniteFloat only rejects NaN ---------------
    def _assert_map_scale_inf_open(self, world: _ExecWorld) -> None:
        _menu, widget = _map_layout(world)
        result = _exec_result(world)
        result.ok = False
        returned = world.ns["DispatchUiMapSet"](
            _exec_command(_exec_args(scale=math.inf)), result
        )
        self.assertTrue(returned)
        self.assertTrue(result.ok)
        self.assertEqual(widget.set_scale_calls, [math.inf])
        self.assertEqual(widget.set_pos_calls, [])
        reply = result.ui_map
        self.assertIsNotNone(reply)
        self.assertAlmostEqual(reply.scale_before, 0.33)
        self.assertTrue(math.isinf(reply.scale_after))
        self.assertGreater(reply.scale_after, 0.0)
        self.assertEqual(list(reply.center_before), [10.0, 20.0])
        self.assertEqual(list(reply.center_after), [10.0, 20.0])
        self.assertEqual(_txt(reply.class_name), "MapMenu")
        self.assertEqual(world.runner.jobs, [])
        self.assertEqual(world.logs, ["ui_map_set ok=1 class=MapMenu"])

    def _assert_map_center_inf_open(self, world: _ExecWorld) -> None:
        _menu, widget = _map_layout(world)
        result = _exec_result(world)
        result.ok = False
        returned = world.ns["DispatchUiMapSet"](
            _exec_command(_exec_args(center=[1.0, math.inf])), result
        )
        self.assertTrue(returned)
        self.assertTrue(result.ok)
        self.assertEqual(widget.set_scale_calls, [])
        self.assertEqual(widget.set_pos_calls, [[1.0, 0.0, math.inf]])
        reply = result.ui_map
        self.assertIsNotNone(reply)
        self.assertAlmostEqual(reply.scale_before, 0.33)
        self.assertAlmostEqual(reply.scale_after, 0.33)
        self.assertEqual(list(reply.center_before), [10.0, 20.0])
        center_after = list(reply.center_after)
        self.assertEqual(center_after[0], 1.0)
        self.assertTrue(math.isinf(center_after[1]))
        self.assertGreater(center_after[1], 0.0)
        self.assertEqual(_txt(reply.class_name), "MapMenu")
        self.assertEqual(world.runner.jobs, [])
        self.assertEqual(world.logs, ["ui_map_set ok=1 class=MapMenu"])

    def test_r11_ui_map_nonfinite_bridge_behaviour_executed(self) -> None:
        # The Python ingress rejects +inf before the bridge (see
        # test_ui_map_set_bad_args_never_reach_the_bridge), so these cases
        # only exist at the bridge. The real IsFiniteFloat (translated
        # above, not substituted) is `value != value` -> false, else true:
        # it rejects NaN and accepts +inf. For scale, `scale <= 0.0` is
        # also false for +inf, so +inf passes both bridge checks.
        is_finite = self.ns["IsFiniteFloat"]
        self.assertTrue(is_finite(1.0))
        self.assertTrue(is_finite(math.inf))
        self.assertTrue(is_finite(-math.inf))
        self.assertFalse(is_finite(math.nan))
        # Map closed: +inf scale and +inf centre pass bad_args and reach
        # the open check, so both answer ui_map_not_open, not bad_args.
        for args in (
            _exec_args(scale=math.inf),
            _exec_args(center=[1.0, math.inf]),
        ):
            with self.subTest(args=args):
                world = _exec_model(self.source, self.messages)
                result = _exec_result(world)
                returned = world.ns["DispatchUiMapSet"](
                    _exec_command(args), result
                )
                self.assertTrue(returned)
                self.assertFalse(result.ok)
                self.assertEqual(_txt(result.error), "ui_map_not_open")
                self.assertIsNotNone(result.ui_map)
                self.assertEqual(
                    world.logs, ["ui_map_set ok=0 error=ui_map_not_open"]
                )
                self.assertEqual(world.runner.jobs, [])
        # Map open, scale=+inf: SetScale(inf) is called and the reply is
        # ok. What the model proves: the widget double stores +inf
        # verbatim, GetScale reads +inf back, and the real UiMapMatches
        # runs on it (scale_after - scale is inf - inf = NaN, and
        # NaN > 0.001 * inf is false), so the real dispatch answers ok
        # immediately with no job. It does not prove a retail MapWidget
        # stores +inf: any clamping inside the engine SetScale is
        # outside this model.
        self._assert_map_scale_inf_open(
            _exec_model(self.source, self.messages)
        )
        # Map open, centre=[1, +inf]: SetMapPos with (1, 0, +inf) is
        # called and the reply is ok, by the same read-back: GetMapPos
        # returns +inf in z, centre dz is inf - inf = NaN, and
        # NaN >= 1.0 is false in the real UiMapMatches. The same
        # retail-MapWidget caveat as for scale applies.
        self._assert_map_center_inf_open(
            _exec_model(self.source, self.messages)
        )
        # NaN is what the real helper does reject: bad_args, before the
        # widget is touched, for NaN scale and for a NaN centre element.
        for args in (
            _exec_args(scale=math.nan),
            _exec_args(center=[1.0, math.nan]),
            _exec_args(center=[math.nan, 2.0]),
        ):
            with self.subTest(args=args):
                world = _exec_model(self.source, self.messages)
                _menu, widget = _map_layout(world)
                result = _exec_result(world)
                returned = world.ns["DispatchUiMapSet"](
                    _exec_command(args), result
                )
                self.assertTrue(returned)
                self.assertFalse(result.ok)
                self.assertEqual(_txt(result.error), "bad_args")
                self.assertEqual(
                    world.logs, ["ui_map_set ok=0 error=bad_args"]
                )
                self.assertEqual(widget.set_scale_calls, [])
                self.assertEqual(widget.set_pos_calls, [])
                self.assertEqual(world.runner.jobs, [])

    def test_r11_old_isfinite_substitution_fails_on_inf(self) -> None:
        # Negative control: putting the old math.isfinite substitution
        # back (in memory only) must make the +inf open cases above
        # fail. Under that substitution both answer bad_args and never
        # reach SetScale/SetMapPos, so the assertions do not hold.
        def old_is_finite(v: Any) -> bool:
            return (
                isinstance(v, (int, float))
                and not isinstance(v, bool)
                and math.isfinite(float(v))
            )
        for assert_open in (
            self._assert_map_scale_inf_open,
            self._assert_map_center_inf_open,
        ):
            with self.subTest(assert_open=assert_open.__name__):
                mutant = _exec_model(self.source, self.messages)
                mutant.ns["IsFiniteFloat"] = old_is_finite
                self.assertFalse(mutant.ns["IsFiniteFloat"](math.inf))
                with self.assertRaises(AssertionError):
                    assert_open(mutant)

    def test_ui_map_set_and_deferred_executed(self) -> None:
        # Scale only, immediate: SetScale called, SetMapPos not, the
        # before read is the widget's previous scale and centre.
        world = _exec_model(self.source, self.messages)
        _menu, widget = _map_layout(world)
        result = _exec_result(world)
        returned = world.ns["DispatchUiMapSet"](
            _exec_command(_exec_args(scale=0.5)), result
        )
        self.assertTrue(returned)
        self.assertTrue(result.ok)
        self.assertEqual(widget.set_scale_calls, [0.5])
        self.assertEqual(widget.set_pos_calls, [])
        reply = result.ui_map
        self.assertAlmostEqual(reply.scale_before, 0.33)
        self.assertAlmostEqual(reply.scale_after, 0.5)
        self.assertEqual(list(reply.center_before), [10.0, 20.0])
        self.assertEqual(list(reply.center_after), [10.0, 20.0])
        self.assertEqual(_txt(reply.class_name), "MapMenu")
        self.assertEqual(world.runner.jobs, [])
        self.assertEqual(world.logs, ["ui_map_set ok=1 class=MapMenu"])
        # Centre only: SetMapPos with (x, 0, z), SetScale not called.
        world = _exec_model(self.source, self.messages)
        _menu, widget = _map_layout(world)
        result = _exec_result(world)
        returned = world.ns["DispatchUiMapSet"](
            _exec_command(_exec_args(center=[100.0, 200.0])), result
        )
        self.assertTrue(returned)
        self.assertTrue(result.ok)
        self.assertEqual(widget.set_scale_calls, [])
        self.assertEqual(widget.set_pos_calls, [[100.0, 0.0, 200.0]])
        self.assertEqual(list(result.ui_map.center_after), [100.0, 200.0])
        self.assertAlmostEqual(result.ui_map.scale_after, 0.33)
        # A centre the widget lands 2 m away from queues the job, whose
        # fields carry the request; Post then answers ui_map_not_set.
        world = _exec_model(self.source, self.messages)
        _menu, widget = _map_layout(world, widget_mode="offset")
        result = _exec_result(world)
        returned = world.ns["DispatchUiMapSet"](
            _exec_command(_exec_args(scale=0.5, center=[100.0, 200.0])), result
        )
        self.assertFalse(returned)
        self.assertEqual(len(world.runner.jobs), 1)
        job = world.runner.jobs[0]
        self.assertEqual(job.id, 42)
        self.assertEqual(_txt(job.kind), "ui_map")
        self.assertAlmostEqual(job.ui_map_scale, 0.5)
        self.assertTrue(job.ui_map_scale_set)
        self.assertEqual(list(job.ui_map_center), [100.0, 200.0])
        self.assertTrue(job.ui_map_center_set)
        self.assertEqual(job.sample_start_s, 100.0)
        self.assertEqual(job.deadline_s, 105.0)
        self.assertEqual(
            (job.tick_poll_sent, job.tick_poll_callback, job.tick_dispatch),
            (11, 22, 33),
        )
        self.assertFalse(world.ns["ProcessUiMapJob"](job))
        self.assertFalse(world.ns["ProcessUiMapJob"](None))
        world.runner.elapsed = 100.5
        self.assertTrue(world.ns["ProcessUiMapJob"](job))
        world.ns["PostUiMapJob"](job)
        posted = world.posted[0]
        self.assertFalse(posted.ok)
        self.assertEqual(_txt(posted.error), "ui_map_not_set")
        self.assertEqual(
            world.logs[-1], "ui_map_set ok=0 error=ui_map_not_set class=MapMenu"
        )
        # Deferred success: the widget applies the scale a tick later.
        world = _exec_model(self.source, self.messages)
        _menu, widget = _map_layout(world, widget_mode="pending")
        result = _exec_result(world)
        returned = world.ns["DispatchUiMapSet"](
            _exec_command(_exec_args(scale=0.5)), result
        )
        self.assertFalse(returned)
        job = world.runner.jobs[0]
        self.assertEqual(list(job.ui_map_center), [])
        self.assertFalse(job.ui_map_center_set)
        widget.apply_pending()
        world.ns["PostUiMapJob"](job)
        posted = world.posted[0]
        self.assertTrue(posted.ok)
        self.assertAlmostEqual(posted.ui_map.scale_after, 0.5)
        self.assertEqual(world.logs[-1], "ui_map_set ok=1 class=MapMenu")
        # Post: aborted job, missing widget at post, no game at post.
        world = _exec_model(self.source, self.messages)
        _menu, widget = _map_layout(world)
        job = world.ns["MCPJob"]()
        job.id = 5
        job.ui_map = world.ns["MCPUiMap"]()
        job.error = EnforceString.of("timeout")
        world.ns["PostUiMapJob"](job)
        self.assertEqual(_txt(world.posted[0].error), "timeout")
        self.assertEqual(world.logs[-1], "ui_map_set ok=0 error=timeout")
        world = _exec_model(self.source, self.messages)
        job = world.ns["MCPJob"]()
        job.ui_map = world.ns["MCPUiMap"]()
        job.ui_map.scale_after = 9.0
        job.ui_map_scale_set = True
        job.ui_map_scale = 9.0
        world.ns["PostUiMapJob"](job)  # no menu open: read stays, no crash
        self.assertAlmostEqual(world.posted[0].ui_map.scale_after, 9.0)
        world = _exec_model(self.source, self.messages)
        world.game_present = False
        job = world.ns["MCPJob"]()
        job.ui_map = world.ns["MCPUiMap"]()
        world.ns["PostUiMapJob"](job)
        self.assertEqual(len(world.posted), 1)
        world.ns["PostUiMapJob"](None)
        self.assertEqual(len(world.posted), 1)

    # -- FillUiMapRead / UiMapMatches / finder, executed directly --------
    def test_ui_map_read_matches_and_finder_executed(self) -> None:
        reply = self.ns["MCPUiMap"]()
        reply.center_after.Insert(9.0)
        reply.center_after.Insert(9.0)
        # No widget: scale reads 0 and the centre is cleared (r8 P2:
        # deleting reply.scale_after = widget.GetScale() must fail).
        self.ns["FillUiMapRead"](reply, None)
        self.assertEqual(reply.scale_after, 0.0)
        self.assertEqual(list(reply.center_after), [])
        widget = _ExecMapWidget()
        widget.scale = 0.5
        widget.pos = [100.0, 7.0, 200.0]
        reply.center_after.Insert(9.0)
        self.ns["FillUiMapRead"](reply, widget)
        self.assertAlmostEqual(reply.scale_after, 0.5)
        self.assertEqual(list(reply.center_after), [100.0, 200.0])

        def matches(scale_set, scale, after_scale, center_set, center, after):
            rep = self.ns["MCPUiMap"]()
            rep.scale_after = after_scale
            rep.center_after.Clear()
            for value in after:
                rep.center_after.Insert(value)
            job = self.ns["MCPJob"]()
            job.ui_map_scale_set = scale_set
            job.ui_map_scale = scale
            job.ui_map_center_set = center_set
            job.ui_map_center = EnforceArray(list(center))
            return self.ns["UiMapMatches"](rep, job)

        # Scale tolerance is relative (1e-3); centre tolerance is < 1 m.
        self.assertTrue(matches(True, 0.5, 0.5004, False, [], []))
        self.assertFalse(matches(True, 0.5, 0.4, False, [], []))
        self.assertFalse(matches(True, 0.5, 0.6, False, [], []))
        self.assertTrue(matches(False, 0.0, 9.0, True, [0.0, 0.0], [0.6, 0.7]))
        self.assertFalse(matches(False, 0.0, 0.0, True, [0.0, 0.0], [1.0, 0.0]))
        self.assertFalse(matches(False, 0.0, 0.0, True, [0.0, 0.0], [0.0, 2.0]))
        self.assertFalse(matches(False, 0.0, 0.0, True, [0.0, 0.0], [2.0, 0.0]))
        self.assertFalse(matches(False, 0.0, 0.0, True, [0.0, 0.0], [1.0]))
        self.assertTrue(matches(False, 0.0, 9.0, False, [], []))
        # Finder and route: null root, widgetless tree, nested widget,
        # no UI manager, no menu.
        self.assertIsNone(self.ns["FindMapWidgetByClass"](None))
        _menu, widget = _map_layout(self.world)
        self.assertIs(self.ns["FindMapWidgetByClass"](_menu.GetLayoutRoot()), widget)
        self.assertIsNone(
            self.ns["FindMapWidgetByClass"](_RouteWidget([_RouteWidget()]))
        )
        found, menu_out = self.ns["UiMapWidget"](None, MENU_MAP_ID)
        self.assertIsNone(found)
        self.assertIsNone(menu_out)
        found, menu_out = self.ns["UiMapWidget"](_RouteUi(None), MENU_MAP_ID)
        self.assertIsNone(found)
        self.assertIsNone(menu_out)

    # -- Lease-close stop / maintain guards, executed --------------------
    def test_lease_stop_and_maintain_guards_executed(self) -> None:
        # No mission and no UI manager: the tool's map is not closed
        # and the mark stays, without crashing.
        for mutate in ("no_mission", "no_ui", "no_game"):
            with self.subTest(mutate=mutate):
                world = _exec_model(self.source, self.messages)
                menu = world.ui.install(_ExecMenu(world.ui, MENU_MAP_ID, "MapMenu"))
                world.ns["m_MCPMapMenu"] = menu
                if mutate == "no_mission":
                    world.game.mission = object()
                elif mutate == "no_ui":
                    world.game.ui = None
                else:
                    world.game_present = False
                world.ns["StopLeaseCloseMap"]()
                self.assertEqual(menu.close_calls, 0)
                self.assertIs(world.ns["m_MCPMapMenu"], menu)
        # Foreign map under the stop: not closed. Own map with no UI
        # manager under Maintain: the mark falls.
        world = _exec_model(self.source, self.messages)
        menu = world.ui.install(_ExecMenu(world.ui, MENU_MAP_ID, "MapMenu"))
        world.ns["StopLeaseCloseMap"]()
        self.assertEqual(menu.close_calls, 0)
        world.ns["m_MCPMapMenu"] = menu
        world.game.ui = None
        world.ns["MaintainMCPMapMenu"]()
        self.assertIsNone(world.ns["m_MCPMapMenu"])
        # Maintain keeps the mark while FindMenu returns the instance.
        world = _exec_model(self.source, self.messages)
        menu = world.ui.install(_ExecMenu(world.ui, MENU_MAP_ID, "MapMenu"))
        world.ns["m_MCPMapMenu"] = menu
        world.ns["MaintainMCPMapMenu"]()
        self.assertIs(world.ns["m_MCPMapMenu"], menu)
        world.ns["MaintainMCPMapMenu"]()
        world.game_present = False
        world.ns["m_MCPMapMenu"] = menu
        world.ns["MaintainMCPMapMenu"]()
        self.assertIsNone(world.ns["m_MCPMapMenu"])

    # -- Round 10: single-line deletions the text contracts cannot see --
    def test_r10_idempotent_read_and_ok_executed(self) -> None:
        # Dispatch:8888/8890: the already-in-state branch fills the reply
        # and sets ok itself. The result starts ok=False here, so a
        # missing assignment is not masked by the caller's default.
        world = _exec_model(self.source, self.messages)
        menu = world.ui.install(
            _ExecMenu(world.ui, MENU_MAP_ID, "ModMapMenu")
        )
        world.ui.install(_ExecMenu(world.ui, 9, "PauseMenu"))
        world.ui.menus[MENU_MAP_ID] = menu
        result = _exec_result(world)
        result.ok = False
        returned = world.ns["DispatchUiMenuSet"](
            _exec_command(_exec_args(open_value=True)), result
        )
        self.assertTrue(returned)
        self.assertTrue(result.ok)
        self.assertIsNotNone(result.ui_menu)
        self.assertTrue(result.ui_menu.open_before)
        self.assertTrue(result.ui_menu.open_after)
        self.assertEqual(_txt(result.ui_menu.class_name), "ModMapMenu")
        self.assertEqual(result.ui_menu.menu_id_before, 9)
        self.assertEqual(result.ui_menu.menu_id_after, 9)
        self.assertEqual(world.ui.entered, [])
        self.assertEqual(
            world.logs, ["ui_menu_set menu=map before=9 after=9 ok=1"]
        )
        # The same branch for an idempotent close, also from ok=False.
        world = _exec_model(self.source, self.messages)
        result = _exec_result(world)
        result.ok = False
        returned = world.ns["DispatchUiMenuSet"](
            _exec_command(_exec_args(open_value=False)), result
        )
        self.assertTrue(returned)
        self.assertTrue(result.ok)
        self.assertFalse(result.ui_menu.open_before)
        self.assertFalse(result.ui_menu.open_after)
        self.assertEqual(_txt(result.ui_menu.class_name), "")
        self.assertEqual(result.ui_menu.menu_id_before, -1)
        self.assertEqual(result.ui_menu.menu_id_after, -1)
        self.assertEqual(
            world.logs,
            ["ui_menu_set menu=map before=-1 after=-1 ok=1 removed=none"],
        )

    def test_r10_immediate_success_ok_executed(self) -> None:
        # Dispatch:8973 and DispatchUiMapSet:9306: result.ok on the
        # immediate success paths, again starting from ok=False.
        result = _exec_result(self.world)
        result.ok = False
        returned = self.ns["DispatchUiMenuSet"](
            _exec_command(_exec_args(open_value=True)), result
        )
        self.assertTrue(returned)
        self.assertTrue(result.ok)
        self.assertTrue(result.ui_menu.open_after)

        world = _exec_model(self.source, self.messages)
        _menu, _widget = _map_layout(world)
        result = _exec_result(world)
        result.ok = False
        returned = world.ns["DispatchUiMapSet"](
            _exec_command(_exec_args(scale=0.5)), result
        )
        self.assertTrue(returned)
        self.assertTrue(result.ok)
        self.assertAlmostEqual(result.ui_map.scale_after, 0.5)

    def test_r10_fill_read_open_after_guard_executed(self) -> None:
        # FillUiMenuRead:8794: class_name is read only when open_after.
        # Open: a stale class is replaced by the FindMenu class.
        reply = self.ns["MCPUiMenu"]()
        reply.class_name = EnforceString.of("stale")
        self.world.ui.install(
            _ExecMenu(self.world.ui, MENU_MAP_ID, "ModMapMenu")
        )
        self.ns["FillUiMenuRead"](reply, MENU_MAP_ID)
        self.assertTrue(reply.open_after)
        self.assertEqual(_txt(reply.class_name), "ModMapMenu")
        self.assertEqual(reply.menu_id_after, MENU_MAP_ID)
        # Not open while FindMenu still returns an instance (a UI in
        # transition reports the two differently): the class stays "".
        world = _exec_model(self.source, self.messages)
        world.ui.install(_ExecMenu(world.ui, MENU_MAP_ID, "ModMapMenu"))
        world.ui.IsMenuOpen = lambda menu_id: False  # type: ignore[method-assign]
        reply = world.ns["MCPUiMenu"]()
        world.ns["FillUiMenuRead"](reply, MENU_MAP_ID)
        self.assertFalse(reply.open_after)
        self.assertEqual(_txt(reply.class_name), "")
        self.assertEqual(reply.menu_id_after, MENU_MAP_ID)

    def test_r10_map_job_reply_and_post_fields_executed(self) -> None:
        # Dispatch:9284: the queued job carries the reply, with the
        # before read, not a null or a fresh reply made later.
        world = _exec_model(self.source, self.messages)
        menu, _widget = _map_layout(world, widget_mode="offset")
        result = _exec_result(world)
        returned = world.ns["DispatchUiMapSet"](
            _exec_command(_exec_args(scale=0.5, center=[100.0, 200.0])),
            result,
        )
        self.assertFalse(returned)
        self.assertIsNone(result.ui_map)
        job = world.runner.jobs[0]
        self.assertIsNotNone(job.ui_map)
        self.assertAlmostEqual(job.ui_map.scale_before, 0.33)
        self.assertEqual(list(job.ui_map.center_before), [10.0, 20.0])
        self.assertAlmostEqual(job.ui_map.scale_after, 0.5)
        self.assertEqual(list(job.ui_map.center_after), [102.0, 200.0])
        self.assertEqual(_txt(job.ui_map.class_name), "MapMenu")
        # Post:9350/9352: the posted class comes from the menu open at
        # post time; rename it after dispatch so a skipped assignment
        # leaves the dispatch-time class behind.
        menu._class_name = "ModMapMenu"
        world.ns["PostUiMapJob"](job)
        posted = world.posted[0]
        # Post:9356-9359: the posted result carries the job identity.
        self.assertEqual(posted.id, 42)
        self.assertEqual(
            (posted.tick_poll_sent, posted.tick_poll_callback,
             posted.tick_dispatch),
            (11, 22, 33),
        )
        self.assertFalse(posted.ok)
        self.assertEqual(_txt(posted.ui_map.class_name), "ModMapMenu")
        self.assertEqual(
            world.logs[-1],
            "ui_map_set ok=0 error=ui_map_not_set class=ModMapMenu",
        )
        # Post:9338: a job with no reply gets a fresh one, filled from
        # the open menu's widget.
        world = _exec_model(self.source, self.messages)
        _map_layout(world)
        job = world.ns["MCPJob"]()
        self.assertIsNone(job.ui_map)
        job.id = 13
        job.tick_poll_sent = 101
        job.tick_poll_callback = 102
        job.tick_dispatch = 103
        world.ns["PostUiMapJob"](job)
        posted = world.posted[0]
        self.assertEqual(posted.id, 13)
        self.assertEqual(
            (posted.tick_poll_sent, posted.tick_poll_callback,
             posted.tick_dispatch),
            (101, 102, 103),
        )
        self.assertTrue(posted.ok)
        self.assertIsNotNone(posted.ui_map)
        self.assertAlmostEqual(posted.ui_map.scale_after, 0.33)
        self.assertEqual(list(posted.ui_map.center_after), [10.0, 20.0])
        self.assertEqual(_txt(posted.ui_map.class_name), "MapMenu")
        # Post:9350 with no menu at post: the reply's class is kept and
        # nothing is dereferenced.
        world = _exec_model(self.source, self.messages)
        job = world.ns["MCPJob"]()
        job.id = 14
        job.tick_poll_sent = 104
        job.tick_poll_callback = 105
        job.tick_dispatch = 106
        job.ui_map = world.ns["MCPUiMap"]()
        job.ui_map.class_name = EnforceString.of("StaleMenu")
        world.ns["PostUiMapJob"](job)
        posted = world.posted[0]
        self.assertEqual(posted.id, 14)
        self.assertEqual(
            (posted.tick_poll_sent, posted.tick_poll_callback,
             posted.tick_dispatch),
            (104, 105, 106),
        )
        self.assertTrue(posted.ok)
        self.assertEqual(_txt(posted.ui_map.class_name), "StaleMenu")
        self.assertEqual(world.logs[-1], "ui_map_set ok=1 class=StaleMenu")

    def test_r10_lease_stop_foreign_instance_executed(self) -> None:
        # StopLeaseCloseMap:10530: the return on a foreign instance.
        # The mark names another instance than FindMenu returns, so
        # the earlier !m_MCPMapMenu return is not what stops this.
        world = _exec_model(self.source, self.messages)
        menu = world.ui.install(_ExecMenu(world.ui, MENU_MAP_ID, "MapMenu"))
        foreign = _ExecMenu(world.ui, MENU_MAP_ID, "ForeignMenu")
        world.ns["m_MCPMapMenu"] = foreign
        world.mission.exclude_active = True
        world.mission.restriction_active = True
        world.ns["StopLeaseCloseMap"]()
        self.assertEqual(menu.close_calls, 0)
        self.assertIs(world.ui.menus[MENU_MAP_ID], menu)
        self.assertIs(world.ns["m_MCPMapMenu"], foreign)
        self.assertEqual(world.mission.removed_excludes, [])
        self.assertEqual(world.mission.removed_restrictions, [])
        # Same guard with FindMenu returning nothing at all.
        world = _exec_model(self.source, self.messages)
        foreign = _ExecMenu(world.ui, MENU_MAP_ID, "ForeignMenu")
        world.ns["m_MCPMapMenu"] = foreign
        world.ns["StopLeaseCloseMap"]()
        self.assertIs(world.ns["m_MCPMapMenu"], foreign)


if __name__ == "__main__":
    unittest.main()
