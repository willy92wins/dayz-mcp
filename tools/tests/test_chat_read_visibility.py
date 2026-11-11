"""Offline Enforce-translated and tool tests for chat_read visibility (fb-20261110-164740-c7cf)."""
from __future__ import annotations

import contextlib
import json
import re
import sys
import unittest
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import result_prune, tool_catalog
from dayz_mcp.server import DEFAULT_TOOL_TIMEOUT_S, ServerConfig, build_app
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import (
    EnforceArray,
    EnforceString,
    clean,
    fake_class,
    method_body,
    translate,
)


COMMAND = "chat_read"
SCRIPTS_5_MISSION = addon_root() / "scripts" / "5_Mission"
CHAT_LOG_PATH = SCRIPTS_5_MISSION / "MCPChatLog.c"
MISSION_GAMEPLAY_PATH = SCRIPTS_5_MISSION / "MissionGameplay.c"
MESSAGES_PATH = SCRIPTS_5_MISSION / "MCPMessages.c"

LOG_MEMBERS = (
    "m_Lines",
    "m_Drops",
    "m_LastSeq",
    "m_TotalLines",
    "m_MaxEvictedSeq",
)
CLASS_TYPES = (
    "array",
    "MCPChatLog",
    "MCPChatLine",
    "MCPChatDrop",
    "MCPChatRead",
    "ChatMessageEventParams",
    "MissionGameplay",
    "IngameHud",
    "Chat",
    "ChatLine",
    "Widget",
    "MCPClientBridge",
)
FORBIDDEN_REF_TYPES = frozenset(
    {"Chat", "ChatLine", "Widget", "TextWidget", "ImageWidget", "IngameHud"}
)


class _Utf8EnforceString(EnforceString):
    """EnforceString with UTF-8 code-point helpers used by MCPChatLog.RecordLine."""

    __slots__ = ()

    def LengthUtf8(self) -> int:
        return len(self.data.decode("utf-8"))

    def SubstringUtf8(self, start: int, length: int) -> "_Utf8EnforceString":
        if type(start) is not int or type(length) is not int or start < 0 or length < 0:
            raise AssertionError(f"invalid SubstringUtf8({start!r}, {length!r})")
        chars = self.data.decode("utf-8")
        if start + length > len(chars):
            raise AssertionError(
                f"SubstringUtf8({start!r}, {length!r}) outside {len(chars)}-char string"
            )
        return _Utf8EnforceString(chars[start : start + length].encode("utf-8"))


class _RingArray(EnforceArray):
    """EnforceArray with RemoveOrdered(index) used by MCPChatLog rings."""

    def RemoveOrdered(self, index: int) -> None:
        if type(index) is not int or not 0 <= index < len(self):
            raise AssertionError(f"RemoveOrdered({index!r}) outside array of {len(self)}")
        del self[index]


class _FakeWidget:
    """Stand-in for DayZ Widget / TextWidget (enwidgets.c:138-158)."""

    def __init__(
        self,
        name: str = "widget",
        *,
        alpha: float = 1.0,
        shown: bool = True,
        parent: "_FakeWidget | None" = None,
        screen_rect: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0),
    ) -> None:
        self.name = name
        self.alpha = float(alpha)
        self.shown = bool(shown)
        self.parent = parent
        self.screen_rect = (
            float(screen_rect[0]),
            float(screen_rect[1]),
            float(screen_rect[2]),
            float(screen_rect[3]),
        )
        self.screen_pos_reads = 0
        self.screen_size_reads = 0

    def Show(self, state: bool) -> None:
        self.shown = bool(state)

    def IsVisible(self) -> bool:
        return self.shown

    def IsVisibleHierarchy(self) -> bool:
        cur: _FakeWidget | None = self
        while cur is not None:
            if not cur.shown:
                return False
            cur = cur.parent
        return True

    def SetAlpha(self, alpha: float) -> None:
        self.alpha = float(alpha)

    def GetAlpha(self) -> float:
        return self.alpha

    def GetParent(self) -> "_FakeWidget | None":
        return self.parent

    def GetScreenPos(self, _x: float, _y: float) -> None:
        self.screen_pos_reads += 1

    def GetScreenSize(self, _w: float, _h: float) -> None:
        self.screen_size_reads += 1

    def TestScreenX(self) -> float:
        self.screen_pos_reads += 1
        return self.screen_rect[0]

    def TestScreenY(self) -> float:
        return self.screen_rect[1]

    def TestScreenW(self) -> float:
        self.screen_size_reads += 1
        return self.screen_rect[2]

    def TestScreenH(self) -> float:
        return self.screen_rect[3]


class _FakeChatParams:
    def __init__(
        self,
        channel: int = 0,
        sender: str = "",
        text: str = "",
        color_name: str = "",
    ) -> None:
        self.param1 = int(channel)
        self.param2 = _Utf8EnforceString.of(sender)
        self.param3 = _Utf8EnforceString.of(text)
        self.param4 = _Utf8EnforceString.of(color_name)


class _FakeChatLine:
    """Stand-in for vanilla ChatLine + modded class ChatLine from MCPChatLog.c."""

    def __init__(
        self,
        index: int,
        chat_frame: _FakeWidget,
        chat_log_src: str,
        get_chat_log: Callable[[], object | None],
    ) -> None:
        self.m_RootWidget: _FakeWidget | None = _FakeWidget(
            f"ChatLineRoot_{index}",
            alpha=1.0,
            shown=False,
            parent=chat_frame,
            screen_rect=(12.0, float(818 - index * 20), 460.0, 22.0),
        )
        self.m_NameWidget: _FakeWidget | None = _FakeWidget(
            f"ChatItemSenderWidget_{index}",
            alpha=1.0,
            shown=True,
            parent=self.m_RootWidget,
            screen_rect=(16.0, float(820 - index * 20), 90.0, 18.0),
        )
        self.m_TextWidget: _FakeWidget | None = _FakeWidget(
            f"ChatItemTextWidget_{index}",
            alpha=1.0,
            shown=True,
            parent=self.m_RootWidget,
            screen_rect=(24.0, float(820 - index * 20), 420.0, 18.0),
        )
        self.m_MCPShownSeq: int = 0
        self.super_set_calls: int = 0

        def _super_set(_params: object) -> None:
            self.super_set_calls += 1
            # Vanilla ChatLine.Set + WidgetFadeTimer.FadeIn (chatline.c:47,109;
            # tools.c:464-465): shows m_RootWidget and starts fade-in at alpha 0.
            if self.m_RootWidget is not None:
                self.m_RootWidget.Show(True)
                self.m_RootWidget.SetAlpha(0.0)

        self._set_ns: dict[str, Any] = {
            "_S": _Utf8EnforceString,
            "m_MCPShownSeq": 0,
            "super": SimpleNamespace(Set=_super_set),
            "MCPChatLog": SimpleNamespace(Get=get_chat_log),
        }
        self._translated_set = translate(
            chat_log_src,
            "override void Set(ChatMessageEventParams params)",
            self._set_ns,
            class_types=CLASS_TYPES,
            members=("m_MCPShownSeq",),
        )

    def Set(self, params: object) -> None:
        self._set_ns["m_MCPShownSeq"] = self.m_MCPShownSeq
        self._translated_set(params)
        self.m_MCPShownSeq = int(self._set_ns["m_MCPShownSeq"])

    def Clear(self) -> None:
        if self.m_RootWidget is not None:
            self.m_RootWidget.Show(False)


class _FakeChat:
    """Stand-in for vanilla Chat (chat.c:14-145) + modded class Chat.GetMCPChatLines."""

    LINE_COUNT = 12

    def __init__(
        self,
        chat_frame: _FakeWidget,
        chat_log_src: str,
        get_chat_log: Callable[[], object | None],
    ) -> None:
        self.m_Lines: _RingArray | None = _RingArray()
        self.m_LastLine: int = 0
        for idx in range(self.LINE_COUNT):
            self.m_Lines.Insert(
                _FakeChatLine(idx, chat_frame, chat_log_src, get_chat_log)
            )

    def GetMCPChatLines(self) -> _RingArray | None:
        return self.m_Lines

    def Clear(self) -> None:
        if self.m_Lines is not None:
            for line in self.m_Lines:
                line.Clear()

    def Destroy(self) -> None:
        if self.m_Lines is not None:
            self.m_Lines.Clear()

    def AddInternal(self, params: object) -> _FakeChatLine:
        assert self.m_Lines is not None and self.m_Lines.Count() > 0
        self.m_LastLine = (self.m_LastLine + 1) % self.m_Lines.Count()
        line: _FakeChatLine = self.m_Lines.Get(self.m_LastLine)
        line.Set(params)
        return line


class _FakeHud:
    """Stand-in for DayZ IngameHud (ingamehud.c:27, 1026-1028)."""

    def __init__(self, panel: _FakeWidget | None) -> None:
        self.panel = panel

    def GetHudPanelWidget(self) -> _FakeWidget | None:
        return self.panel


_SCOPE_STRING_OR_BRACE = re.compile(r'"(?:\\.|[^"\\])*"|[{}]')


def _scoped_body_span(cleaned: str, scope_header: str) -> tuple[int, int]:
    pattern = re.compile(rf"(?m)^\s*{re.escape(scope_header)}\s*\{{")
    matches = list(pattern.finditer(cleaned))
    if len(matches) != 1:
        raise AssertionError(
            f"scope {scope_header!r} matched {len(matches)} times"
        )
    open_brace = matches[0].end() - 1
    depth = 0
    for token_match in _SCOPE_STRING_OR_BRACE.finditer(cleaned, open_brace):
        token = token_match.group()
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
            if depth == 0:
                return open_brace + 1, token_match.start()
    raise AssertionError(f"unterminated scope: {scope_header!r}")


def _mutate_in_scope(
    src: str, scope_header: str, target: str, replacement: str
) -> str:
    cleaned = clean(src)
    if len(cleaned) != len(src):
        raise AssertionError("clean(src) changed source length")
    body_start, body_end = _scoped_body_span(cleaned, scope_header)
    cleaned_body = cleaned[body_start:body_end]
    count = cleaned_body.count(target)
    if count != 1:
        raise AssertionError(
            f"target in {scope_header!r} occurred {count} times: {target!r}"
        )
    rel_idx = cleaned_body.index(target)
    abs_start = body_start + rel_idx
    abs_end = abs_start + len(target)
    if src[abs_start:abs_end] != target:
        raise AssertionError(
            f"target in {scope_header!r} spans a comment: {target!r}"
        )
    mutant = src[:abs_start] + replacement + src[abs_end:]
    if clean(mutant) == cleaned:
        raise AssertionError(
            f"mutation in {scope_header!r} left cleaned source unchanged: {target!r}"
        )
    return mutant


def _rewrite_fill_screen_queries(chat_log_src: str) -> str:
    """Replace FillReadVisibility's two out-parameter widget calls with TestScreen* getters."""
    scope = "void FillReadVisibility(MCPChatRead outRead)"
    rewritten = _mutate_in_scope(
        chat_log_src,
        scope,
        "textWidget.GetScreenPos(sx, sy);",
        "sx = textWidget.TestScreenX();\n\t\t\t\t\tsy = textWidget.TestScreenY();",
    )
    return _mutate_in_scope(
        rewritten,
        scope,
        "textWidget.GetScreenSize(sw, sh);",
        "sw = textWidget.TestScreenW();\n\t\t\t\t\tsh = textWidget.TestScreenH();",
    )


def _parse_int_constants(chat_log_src: str) -> dict[str, int]:
    cleaned = clean(chat_log_src)
    return {
        name: int(val)
        for name, val in re.findall(
            r"^\s*const\s+int\s+([A-Za-z_]\w*)\s*=\s*(-?\d+)\s*;",
            cleaned,
            re.M,
        )
    }


class _ChatVisibilityModel:
    """Runs translated MCPChatLog, ChatLine.Set, and MissionGameplay methods on fakes."""

    def __init__(
        self,
        chat_log_src: str | None = None,
        mission_src: str | None = None,
        messages_src: str | None = None,
    ) -> None:
        self.chat_log_src = (
            CHAT_LOG_PATH.read_text(encoding="utf-8")
            if chat_log_src is None
            else chat_log_src
        )
        self.exec_chat_log_src = _rewrite_fill_screen_queries(self.chat_log_src)
        self.mission_src = (
            MISSION_GAMEPLAY_PATH.read_text(encoding="utf-8")
            if mission_src is None
            else mission_src
        )
        self.messages_src = (
            MESSAGES_PATH.read_text(encoding="utf-8")
            if messages_src is None
            else messages_src
        )

        self.now_ms: int = 1000
        self.has_game: bool = True
        self.in_game: bool = True
        self.events: list[str] = []
        self.on_bridge_tick: Callable[[float], None] | None = None
        self._super_update_hook: Callable[[float], None] | None = None

        # HUD hierarchy mirrors missiongameplay.c:132-140: ChatFrameWidget and
        # HudPanel are siblings under m_HudRootWidget (HudPanel is not Chat's ancestor).
        self.hud_root = _FakeWidget("HudRootWidget", alpha=1.0, shown=True, parent=None)
        self.hud_panel = _FakeWidget(
            "HudPanel", alpha=1.0, shown=True, parent=self.hud_root
        )
        self.chat_frame = _FakeWidget(
            "ChatFrameWidget", alpha=1.0, shown=True, parent=self.hud_root
        )
        self.hud: _FakeHud | None = _FakeHud(self.hud_panel)

        self._game = SimpleNamespace(
            GetTime=lambda: self.now_ms,
            GetMission=lambda: self.mission if self.in_game else None,
        )
        get_game = lambda: self._game if self.has_game else None

        self.consts = _parse_int_constants(self.chat_log_src)
        self.msg_ns: dict[str, Any] = {"array": _RingArray}
        fake_class(self.messages_src, "MCPChatLine", self.msg_ns)
        fake_class(self.messages_src, "MCPChatDrop", self.msg_ns)
        fake_class(self.messages_src, "MCPChatRead", self.msg_ns)

        self._static_ns: dict[str, Any] = {
            "_S": _Utf8EnforceString,
            "GetGame": get_game,
            "MissionGameplay": SimpleNamespace(
                Cast=lambda obj: obj if obj is self.mission else None
            ),
        }
        self.static_get_log = translate(
            self.chat_log_src,
            "static MCPChatLog Get()",
            self._static_ns,
            class_types=CLASS_TYPES,
        )

        self.chat: _FakeChat | None = _FakeChat(
            self.chat_frame, self.chat_log_src, self.static_get_log
        )

        def _make_chat_log() -> SimpleNamespace:
            log_ns: dict[str, Any] = {
                "_S": _Utf8EnforceString,
                "array": _RingArray,
                "MCPChatLine": self.msg_ns["MCPChatLine"],
                "MCPChatDrop": self.msg_ns["MCPChatDrop"],
                "MCPChatRead": self.msg_ns["MCPChatRead"],
                "GetGame": get_game,
                "MissionGameplay": SimpleNamespace(
                    Cast=lambda obj: obj if obj is self.mission else None
                ),
                "IngameHud": SimpleNamespace(
                    Cast=lambda obj: obj if isinstance(obj, _FakeHud) else None
                ),
                "m_Lines": _RingArray(),
                "m_Drops": _RingArray(),
                "m_LastSeq": 0,
                "m_TotalLines": 0,
                "m_MaxEvictedSeq": 0,
                **self.consts,
            }
            for sig in (
                "int GetTotalLines()",
                "int GetLastSeq()",
                "void RecordLine(ChatMessageEventParams params)",
                "protected ChatLine FindChatLineBySeq(int seq)",
                "void FillReadVisibility(MCPChatRead outRead)",
                "void SampleShownLines(int nowMs)",
            ):
                translate(
                    self.exec_chat_log_src,
                    sig,
                    log_ns,
                    class_types=CLASS_TYPES,
                    members=LOG_MEMBERS,
                )

            raw_sample = log_ns["SampleShownLines"]

            def _logged_sample(now_ms: int) -> None:
                self.events.append("sample")
                raw_sample(now_ms)

            return SimpleNamespace(
                ns=log_ns,
                GetTotalLines=log_ns["GetTotalLines"],
                GetLastSeq=log_ns["GetLastSeq"],
                RecordLine=log_ns["RecordLine"],
                FindChatLineBySeq=log_ns["FindChatLineBySeq"],
                FillReadVisibility=log_ns["FillReadVisibility"],
                SampleShownLines=_logged_sample,
            )

        def _advance_frame() -> None:
            self.events.append("frame_advance")

        def _super_on_update(timeslice: float) -> None:
            self.events.append("super_update")
            if self._super_update_hook is not None:
                self._super_update_hook(timeslice)

        def _bridge_on_tick(timeslice: float) -> None:
            self.events.append("bridge_tick")
            if self.on_bridge_tick is not None:
                self.on_bridge_tick(timeslice)

        self.bridge = SimpleNamespace(OnTick=_bridge_on_tick)
        self.mission_ns: dict[str, Any] = {
            "_S": _Utf8EnforceString,
            "m_MCPChatLog": None,
            "MCPChatLog": _make_chat_log,
            "MCPFrame": SimpleNamespace(Advance=_advance_frame),
            "super": SimpleNamespace(OnUpdate=_super_on_update),
            "GetGame": get_game,
            "MCPClientBridge": SimpleNamespace(Get=lambda: self.bridge),
        }
        get_mcp_chat_log = translate(
            self.mission_src,
            "MCPChatLog GetMCPChatLog()",
            self.mission_ns,
            class_types=CLASS_TYPES,
            members=("m_MCPChatLog",),
        )
        on_update = translate(
            self.mission_src,
            "override void OnUpdate(float timeslice)",
            self.mission_ns,
            class_types=CLASS_TYPES,
            members=("m_MCPChatLog",),
        )
        self.mission = SimpleNamespace(
            m_Chat=self.chat,
            GetHud=lambda: self.hud,
            GetMCPChatLog=get_mcp_chat_log,
            OnUpdate=on_update,
        )

    @property
    def chat_log(self) -> SimpleNamespace | None:
        return self.mission_ns["m_MCPChatLog"]

    def emit_line(
        self,
        text: str,
        *,
        t_ms: int | None = None,
        channel: int = 0,
        sender: str = "",
        alpha_after_set: float | None = None,
    ) -> _FakeChatLine:
        if t_ms is not None:
            self.now_ms = t_ms
        self.mission.m_Chat = self.chat
        assert self.chat is not None
        chat_line = self.chat.AddInternal(
            _FakeChatParams(channel=channel, sender=sender, text=text)
        )
        if alpha_after_set is not None and chat_line.m_RootWidget is not None:
            chat_line.m_RootWidget.SetAlpha(alpha_after_set)
        return chat_line

    def tick_frame(
        self,
        now_ms: int,
        timeslice: float = 0.016,
        on_super_update: Callable[[float], None] | None = None,
    ) -> None:
        self.now_ms = now_ms
        self.mission.m_Chat = self.chat
        self._super_update_hook = on_super_update
        try:
            self.mission.OnUpdate(timeslice)
        finally:
            self._super_update_hook = None

    def read(self, marker: int = 0, max_lines: int = 200) -> Any:
        self.mission.m_Chat = self.chat
        log = self.mission.GetMCPChatLog()
        out_read = self.msg_ns["MCPChatRead"]()
        lines = log.ns["m_Lines"]
        out_read.truncated = log.ns["m_MaxEvictedSeq"] > marker
        cut = False
        last_seq = 0
        for item in lines:
            if item is not None and item.seq > marker:
                if out_read.lines.Count() >= max_lines:
                    cut = True
                    break
                # Stored MCPChatLine reference, matching MCPChatLog.Read (:270).
                out_read.lines.Insert(item)
                last_seq = item.seq
        out_read.marker = last_seq if cut else log.ns["m_LastSeq"]
        log.FillReadVisibility(out_read)
        return out_read


def _block_after(source: str, start: int) -> tuple[str, int]:
    brace = source.find("{", start)
    if brace == -1:
        raise AssertionError(f"missing '{{' starting at {start}")
    depth = 0
    for idx in range(brace, len(source)):
        ch = source[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : idx], idx + 1
    raise AssertionError(f"unterminated '{{' block starting at {start}")


def _extract_class_body(cleaned: str, header_regex: str) -> str:
    matches = list(re.finditer(header_regex, cleaned, re.M))
    if len(matches) != 1:
        raise AssertionError(f"{header_regex!r} matched {len(matches)} times")
    body, _ = _block_after(cleaned, matches[0].end())
    return body


def _class_member_declarations(class_body: str) -> list[tuple[str, str]]:
    """Return (raw_type_spec, member_name) for every field declared in class_body."""
    decls: list[tuple[str, str]] = []
    pos = 0
    n = len(class_body)
    while pos < n:
        semi = class_body.find(";", pos)
        brace = class_body.find("{", pos)
        if semi == -1 and brace == -1:
            break
        if brace != -1 and (semi == -1 or brace < semi):
            _, pos = _block_after(class_body, brace)
            continue
        assert semi != -1
        stmt = " ".join(class_body[pos:semi].split())
        pos = semi + 1
        if not stmt:
            continue
        # Strip optional initializer (`const int X = 256`).
        lhs = stmt.split("=", 1)[0].strip()
        m = re.match(
            r"^(?:(?:protected|private|static|const)\s+)*"
            r"(?P<type>(?:ref\s+)?[A-Za-z_]\w*(?:\s*<[^>]+>)?)\s+"
            r"(?P<name>[A-Za-z_]\w*)$",
            lhs,
        )
        if m is None:
            raise AssertionError(f"unparsed class declaration: {stmt!r}")
        decls.append((m.group("type"), m.group("name")))
    return decls


def _class_field_statements(class_body: str) -> list[str]:
    """Every field declaration of class_body as written (modifiers kept), whitespace-normalized."""
    statements: list[str] = []
    pos = 0
    n = len(class_body)
    while pos < n:
        semi = class_body.find(";", pos)
        brace = class_body.find("{", pos)
        if semi == -1 and brace == -1:
            break
        if brace != -1 and (semi == -1 or brace < semi):
            _, pos = _block_after(class_body, brace)
            continue
        stmt = " ".join(class_body[pos:semi].split())
        pos = semi + 1
        if stmt:
            statements.append(stmt)
    return statements


# The fields this ticket adds, as plain members: JsonSerializer writes instance members, a
# static member is one value shared by every instance, and MCPChatLog reads the ChatLine
# field from another class, so none of them may be static, protected, private or const.
NEW_LINE_FIELDS = (
    "bool visible_hierarchy",
    "float alpha",
    "float screen_x",
    "float screen_y",
    "float screen_w",
    "float screen_h",
    "bool replaced",
    "int shown_at_ms",
)


def verify_new_fields_are_plain_members(chat_log_src: str, messages_src: str) -> None:
    """Verify the new MCPChatLine and MCPChatRead fields and ChatLine.m_MCPShownSeq are plain instance members."""
    cleaned_messages = clean(messages_src)
    line_fields = _class_field_statements(
        _extract_class_body(cleaned_messages, r"^\s*class\s+MCPChatLine\b")
    )
    for field in NEW_LINE_FIELDS:
        if line_fields.count(field) != 1:
            raise AssertionError(
                f"MCPChatLine must declare the plain instance member {field!r} once, got {line_fields}"
            )
    read_fields = _class_field_statements(
        _extract_class_body(cleaned_messages, r"^\s*class\s+MCPChatRead\b")
    )
    if read_fields.count("bool hud_shown") != 1 or any(
        stmt.endswith(" hud_shown") and stmt != "bool hud_shown" for stmt in read_fields
    ):
        raise AssertionError(
            f"MCPChatRead must declare the plain instance member 'bool hud_shown' once, got {read_fields}"
        )
    chatline_fields = _class_field_statements(
        _extract_class_body(clean(chat_log_src), r"^\s*modded\s+class\s+ChatLine\b")
    )
    if chatline_fields != ["int m_MCPShownSeq"]:
        raise AssertionError(
            "modded class ChatLine must declare exactly the plain instance member "
            f"'int m_MCPShownSeq' (one value per ChatLine, readable by MCPChatLog), got {chatline_fields}"
        )


def verify_no_widget_or_chatline_refs(
    chat_log_src: str, messages_src: str
) -> None:
    """Verify MCPChatLog, MCPChatLine, MCPChatDrop and MCPChatRead store no widget or ChatLine reference."""
    checks = (
        (clean(chat_log_src), r"^\s*class\s+MCPChatLog\b", "MCPChatLog"),
        (clean(messages_src), r"^\s*class\s+MCPChatLine\b", "MCPChatLine"),
        (clean(messages_src), r"^\s*class\s+MCPChatDrop\b", "MCPChatDrop"),
        (clean(messages_src), r"^\s*class\s+MCPChatRead\b", "MCPChatRead"),
    )
    for cleaned, header_re, label in checks:
        body = _extract_class_body(cleaned, header_re)
        for type_spec, member_name in _class_member_declarations(body):
            tokens = set(re.findall(r"[A-Za-z_]\w*", type_spec))
            bad = tokens & FORBIDDEN_REF_TYPES
            if bad:
                raise AssertionError(
                    f"{label}.{member_name} holds forbidden UI/chat type {sorted(bad)}: {type_spec!r}"
                )


def verify_modded_chat_and_chatline_contract(chat_log_src: str) -> None:
    """Verify modded Chat has only one m_Lines getter and modded ChatLine.Set sets m_MCPShownSeq after super.Set."""
    cleaned = clean(chat_log_src)
    chat_body = _extract_class_body(cleaned, r"^\s*modded\s+class\s+Chat\b")
    if _class_member_declarations(chat_body):
        raise AssertionError("modded class Chat must declare no fields")
    getter_re = re.compile(
        r"^\s*array<\s*ref\s+ChatLine\s*>\s+GetMCPChatLines\s*\(\s*\)\s*\{\s*return\s+m_Lines\s*;\s*\}\s*$"
    )
    if not getter_re.match(chat_body):
        raise AssertionError(
            "modded class Chat must contain only array<ref ChatLine> GetMCPChatLines() returning m_Lines"
        )

    chatline_body = _extract_class_body(cleaned, r"^\s*modded\s+class\s+ChatLine\b")
    members = _class_member_declarations(chatline_body)
    if members != [("int", "m_MCPShownSeq")]:
        raise AssertionError(
            f"modded class ChatLine must declare only int m_MCPShownSeq, got {members}"
        )

    set_body = method_body(
        chat_log_src, "override void Set(ChatMessageEventParams params)"
    )
    super_pos = set_body.find("super.Set(params);")
    if super_pos < 0:
        raise AssertionError("ChatLine.Set missing super.Set(params);")
    seq_writes = [m.start() for m in re.finditer(r"\bm_MCPShownSeq\s*=(?!=)", set_body)]
    if len(seq_writes) < 2:
        raise AssertionError(
            "ChatLine.Set must reset m_MCPShownSeq = 0 and set it from GetLastSeq()"
        )
    if any(pos < super_pos for pos in seq_writes):
        raise AssertionError("ChatLine.Set wrote m_MCPShownSeq before super.Set(params)")
    after_super = set_body[super_pos + len("super.Set(params);") :]
    if not re.search(r"^\s*m_MCPShownSeq\s*=\s*0\s*;\s*$", after_super, re.M):
        raise AssertionError("ChatLine.Set must reset m_MCPShownSeq = 0 after super.Set")
    if not re.search(
        r"\bm_MCPShownSeq\s*=\s*chatLog\.GetLastSeq\s*\(\s*\)\s*;", after_super
    ):
        raise AssertionError(
            "ChatLine.Set must set m_MCPShownSeq = chatLog.GetLastSeq() when a line was recorded"
        )


def verify_mission_on_update_sampling_contract(mission_src: str) -> None:
    """Verify MissionGameplay.OnUpdate samples after super.OnUpdate and before bridge.OnTick without creating the log."""
    body = method_body(mission_src, "override void OnUpdate(float timeslice)")
    if "GetMCPChatLog" in body or re.search(r"\bnew\s+MCPChatLog\b", body):
        raise AssertionError(
            "MissionGameplay.OnUpdate must not create MCPChatLog when none exists"
        )
    super_pos = body.find("super.OnUpdate(timeslice);")
    sample_match = re.search(
        r"\bif\s*\(\s*m_MCPChatLog\s*&&\s*GetGame\s*\(\s*\)\s*\)\s*\{\s*"
        r"m_MCPChatLog\.SampleShownLines\s*\(\s*GetGame\s*\(\s*\)\.GetTime\s*\(\s*\)\s*\)\s*;\s*\}",
        body,
    )
    tick_pos = body.find("bridge.OnTick(timeslice);")
    if super_pos < 0 or sample_match is None or tick_pos < 0:
        raise AssertionError(
            "MissionGameplay.OnUpdate must guard m_MCPChatLog.SampleShownLines(GetGame().GetTime()) between super.OnUpdate and bridge.OnTick"
        )
    if not (super_pos < sample_match.start() and sample_match.end() < tick_pos):
        raise AssertionError(
            "SampleShownLines must run after super.OnUpdate(timeslice) and before bridge.OnTick(timeslice)"
        )


def verify_screen_rect_and_read_fill_contract(chat_log_src: str) -> None:
    """Verify Read unconditionally invokes FillReadVisibility(outRead) before its sole return and FillReadVisibility calls GetScreenPos/GetScreenSize on m_TextWidget without touching shown_at_ms."""
    read_body = method_body(chat_log_src, "MCPChatRead Read(int marker, int maxLines)")
    masked_read_body = re.sub(
        r'"(?:\\.|[^"\\])*"',
        lambda m: '"' + (" " * (len(m.group()) - 2)) + '"',
        read_body,
    )
    return_count = len(re.findall(r"\breturn\b", masked_read_body))
    if return_count != 1:
        raise AssertionError(
            f"MCPChatLog.Read must contain exactly 1 return statement, got {return_count}"
        )
    fill_count = len(re.findall(r"\bFillReadVisibility\b", masked_read_body))
    if fill_count != 1 or read_body.count("FillReadVisibility") != 1:
        raise AssertionError(
            f"MCPChatLog.Read must call FillReadVisibility once, got {fill_count}"
        )
    top_level_stmts: list[str] = []
    brace_depth = 0
    stmt_start = 0
    for idx, ch in enumerate(masked_read_body):
        if ch == "{":
            if brace_depth == 0:
                header = " ".join(read_body[stmt_start:idx].split())
                top_level_stmts.append(f"{header} {{...}}" if header else "{...}")
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
            if brace_depth < 0:
                raise AssertionError("unmatched '}' in MCPChatLog.Read")
            if brace_depth == 0:
                stmt_start = idx + 1
        elif brace_depth == 0 and ch == ";":
            top_level_stmts.append(" ".join(read_body[stmt_start:idx].split()))
            stmt_start = idx + 1
    if brace_depth != 0 or read_body[stmt_start:].strip():
        raise AssertionError("unterminated statement or block in MCPChatLog.Read")
    if top_level_stmts[-2:] != ["FillReadVisibility(outRead)", "return outRead"]:
        raise AssertionError(
            "MCPChatLog.Read last two top-level statements must be "
            f"FillReadVisibility(outRead) and return outRead, got {top_level_stmts[-2:]!r}"
        )
    if not re.search(
        r"\bFillReadVisibility\s*\(\s*outRead\s*\)\s*;\s*return\s+outRead\s*;\s*$",
        read_body,
    ):
        raise AssertionError(
            "MCPChatLog.Read must call FillReadVisibility(outRead) right before return outRead"
        )

    fill_body = method_body(chat_log_src, "void FillReadVisibility(MCPChatRead outRead)")
    if "shown_at_ms" in fill_body:
        raise AssertionError("FillReadVisibility must never write or read shown_at_ms")
    if "m_RootWidget" in fill_body or "m_NameWidget" in fill_body:
        raise AssertionError(
            "FillReadVisibility must read ChatLine.m_TextWidget rather than m_RootWidget or m_NameWidget"
        )
    if not re.search(r"\btextWidget\s*=\s*chatLine\.m_TextWidget\s*;", fill_body):
        raise AssertionError(
            "FillReadVisibility must read textWidget = chatLine.m_TextWidget;"
        )
    out_calls_re = re.compile(
        r"\btextWidget\.GetScreenPos\s*\(\s*sx\s*,\s*sy\s*\)\s*;\s*"
        r"textWidget\.GetScreenSize\s*\(\s*sw\s*,\s*sh\s*\)\s*;"
    )
    if not out_calls_re.search(fill_body):
        raise AssertionError(
            "FillReadVisibility must call textWidget.GetScreenPos(sx, sy) and textWidget.GetScreenSize(sw, sh)"
        )


def _tool_payload(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, tuple) and len(raw) == 2 and isinstance(raw[1], dict):
        return raw[1]
    blocks = raw[0] if isinstance(raw, tuple) else raw
    assert isinstance(blocks, list) and blocks, f"unexpected call_tool output: {raw!r}"
    return json.loads(blocks[0].text)


class ChatReadVisibilityTranslatedEnforceTest(unittest.TestCase):
    def test_read_alpha_is_product_of_text_widget_and_ancestors(self) -> None:
        model = _ChatVisibilityModel()
        chat_line = model.emit_line("Cable connected", t_ms=1000, alpha_after_set=0.5)
        assert chat_line.m_TextWidget is not None
        chat_line.m_TextWidget.SetAlpha(1.0)

        read_half = model.read()
        self.assertEqual(read_half.lines.Count(), 1)
        self.assertAlmostEqual(read_half.lines.Get(0).alpha, 0.5, places=6)

        # Positive control: full opacity across all ancestors yields 1.0.
        assert chat_line.m_RootWidget is not None
        chat_line.m_RootWidget.SetAlpha(1.0)
        self.assertAlmostEqual(model.read().lines.Get(0).alpha, 1.0, places=6)

        # Multi-ancestor product: hud_root (0.5) * line_root (0.5) * text (0.8) = 0.2.
        model.hud_root.SetAlpha(0.5)
        chat_line.m_RootWidget.SetAlpha(0.5)
        chat_line.m_TextWidget.SetAlpha(0.8)
        self.assertAlmostEqual(model.read().lines.Get(0).alpha, 0.2, places=6)

        # Text widget at 0.5 with every ancestor at 1.0 -> alpha 0.5, plus mutant
        # control starting the product at textWidget.GetParent().
        def check_text_widget_alpha_read(chat_log_src: str) -> None:
            m = _ChatVisibilityModel(chat_log_src=chat_log_src)
            slot = m.emit_line("Text-only alpha", t_ms=1000, alpha_after_set=1.0)
            assert slot.m_RootWidget is not None
            assert slot.m_TextWidget is not None
            self.assertEqual(m.hud_root.GetAlpha(), 1.0)
            self.assertEqual(m.chat_frame.GetAlpha(), 1.0)
            self.assertEqual(slot.m_RootWidget.GetAlpha(), 1.0)
            slot.m_TextWidget.SetAlpha(0.5)
            self.assertAlmostEqual(
                m.read().lines.Get(0).alpha,
                0.5,
                places=6,
                msg="read alpha must include text widget",
            )

        base_src = CHAT_LOG_PATH.read_text(encoding="utf-8")
        check_text_widget_alpha_read(base_src)
        skip_text_alpha_read = _mutate_in_scope(
            base_src,
            "void FillReadVisibility(MCPChatRead outRead)",
            "currentWidget = textWidget;",
            "currentWidget = textWidget.GetParent();",
        )
        with self.assertRaisesRegex(
            AssertionError, "read alpha must include text widget"
        ):
            check_text_widget_alpha_read(skip_text_alpha_read)

    def test_read_values_come_from_live_widget_between_set_and_read(self) -> None:
        model = _ChatVisibilityModel()
        chat_line = model.emit_line("Port overloaded", t_ms=1000, alpha_after_set=1.0)
        self.assertAlmostEqual(model.read().lines.Get(0).alpha, 1.0, places=6)

        # Root alpha drops to 0 between Set and a later read (e.g. fade-out complete):
        # read must report 0 from the widget, never a value cached at Set.
        assert chat_line.m_RootWidget is not None
        chat_line.m_RootWidget.SetAlpha(0.0)
        self.assertAlmostEqual(model.read().lines.Get(0).alpha, 0.0, places=6)

        # Raising widget alpha again updates the next read on the same stored entry.
        chat_line.m_RootWidget.SetAlpha(0.75)
        self.assertAlmostEqual(model.read().lines.Get(0).alpha, 0.75, places=6)

    def test_hidden_parent_sets_visible_hierarchy_false_while_shown_is_true(self) -> None:
        model = _ChatVisibilityModel()
        chat_line = model.emit_line("Generator online", t_ms=1000, alpha_after_set=1.0)
        assert chat_line.m_RootWidget is not None
        assert chat_line.m_TextWidget is not None

        # Positive: text widget and every ancestor shown -> visible_hierarchy=True.
        self.assertTrue(model.read().lines.Get(0).visible_hierarchy)

        # Negative 1: line root hidden (e.g. Chat.Clear in DestroyAllMenus) while text stays shown.
        chat_line.m_RootWidget.Show(False)
        self.assertTrue(chat_line.m_TextWidget.IsVisible())
        self.assertFalse(model.read().lines.Get(0).visible_hierarchy)

        # Negative 2: HUD root ancestor hidden while line root and text stay shown.
        chat_line.m_RootWidget.Show(True)
        model.hud_root.Show(False)
        self.assertFalse(model.read().lines.Get(0).visible_hierarchy)

        # Sibling independence (missiongameplay.c:136,140): hiding HudPanel flips
        # hud_shown=False without hiding ChatFrameWidget under shown HudRootWidget.
        model.hud_root.Show(True)
        model.hud_panel.Show(False)
        sibling_read = model.read()
        self.assertFalse(sibling_read.hud_shown)
        self.assertTrue(sibling_read.lines.Get(0).visible_hierarchy)

    def test_read_queries_text_widget_screen_pos_and_size(self) -> None:
        text_rect = (108.0, 704.0, 396.0, 18.0)
        root_rect = (10.0, 700.0, 500.0, 22.0)
        name_rect = (14.0, 702.0, 88.0, 20.0)

        def check_screen_rect_execution(chat_log_src: str) -> None:
            m = _ChatVisibilityModel(chat_log_src=chat_log_src)
            chat_line = m.emit_line("Switch toggled", t_ms=1000, alpha_after_set=1.0)
            assert chat_line.m_RootWidget is not None
            assert chat_line.m_NameWidget is not None
            assert chat_line.m_TextWidget is not None
            chat_line.m_RootWidget.screen_rect = root_rect
            chat_line.m_NameWidget.screen_rect = name_rect
            chat_line.m_TextWidget.screen_rect = text_rect

            active_item = m.read().lines.Get(0)
            self.assertFalse(active_item.replaced)
            self.assertEqual(
                (
                    active_item.screen_x,
                    active_item.screen_y,
                    active_item.screen_w,
                    active_item.screen_h,
                ),
                text_rect,
                "active text widget screen rect mismatch",
            )
            self.assertEqual(chat_line.m_TextWidget.screen_pos_reads, 1)
            self.assertEqual(chat_line.m_TextWidget.screen_size_reads, 1)
            self.assertEqual(chat_line.m_RootWidget.screen_pos_reads, 0)
            self.assertEqual(chat_line.m_RootWidget.screen_size_reads, 0)
            self.assertEqual(chat_line.m_NameWidget.screen_pos_reads, 0)
            self.assertEqual(chat_line.m_NameWidget.screen_size_reads, 0)

            for idx in range(2, 14):
                reused = m.emit_line(
                    f"Wrap {idx}", t_ms=1000 + idx, alpha_after_set=1.0
                )
            self.assertIs(reused, chat_line)
            replaced_item = m.read(marker=0, max_lines=20).lines.Get(0)
            self.assertTrue(replaced_item.replaced)
            self.assertEqual(
                (
                    replaced_item.screen_x,
                    replaced_item.screen_y,
                    replaced_item.screen_w,
                    replaced_item.screen_h,
                ),
                (0.0, 0.0, 0.0, 0.0),
                "replaced line screen rect must be zeroed",
            )

        base_src = CHAT_LOG_PATH.read_text(encoding="utf-8")
        check_screen_rect_execution(base_src)

        fill_scope = "void FillReadVisibility(MCPChatRead outRead)"

        # Mutant (a): screen_* overwritten with 0.0 after their correct assignments.
        zeroed_after_set = _mutate_in_scope(
            base_src,
            fill_scope,
            "lineItem.screen_h = sh;",
            "lineItem.screen_h = sh;\n"
            "\t\t\t\t\tlineItem.screen_x = 0.0;\n"
            "\t\t\t\t\tlineItem.screen_y = 0.0;\n"
            "\t\t\t\t\tlineItem.screen_w = 0.0;\n"
            "\t\t\t\t\tlineItem.screen_h = 0.0;",
        )
        with self.assertRaisesRegex(
            AssertionError, "active text widget screen rect mismatch"
        ):
            check_screen_rect_execution(zeroed_after_set)

        # Mutant (b): screen_x and screen_y swapped.
        swapped_xy = _mutate_in_scope(
            base_src,
            fill_scope,
            "lineItem.screen_x = sx;\n\t\t\t\t\tlineItem.screen_y = sy;",
            "lineItem.screen_x = sy;\n\t\t\t\t\tlineItem.screen_y = sx;",
        )
        with self.assertRaisesRegex(
            AssertionError, "active text widget screen rect mismatch"
        ):
            check_screen_rect_execution(swapped_xy)

        # Mutant (c): FillReadVisibility reads chatLine.m_RootWidget instead of m_TextWidget.
        wrong_widget = _mutate_in_scope(
            base_src,
            fill_scope,
            "textWidget = chatLine.m_TextWidget;",
            "textWidget = chatLine.m_RootWidget;",
        )
        with self.assertRaisesRegex(
            AssertionError, "active text widget screen rect mismatch"
        ):
            check_screen_rect_execution(wrong_widget)

    def test_replacement_zeros_fields_on_overwritten_line_and_keeps_active_line_unreplaced(self) -> None:
        model = _ChatVisibilityModel()
        first_slot = model.emit_line("Line 1", t_ms=1000, alpha_after_set=1.0)
        model.tick_frame(1200)

        initial_read = model.read()
        line1 = initial_read.lines.Get(0)
        self.assertEqual(line1.seq, 1)
        self.assertFalse(line1.replaced)
        self.assertTrue(line1.visible_hierarchy)
        self.assertAlmostEqual(line1.alpha, 1.0, places=6)
        self.assertEqual(
            (line1.screen_x, line1.screen_y, line1.screen_w, line1.screen_h),
            (24.0, 800.0, 420.0, 18.0),
        )
        self.assertEqual(line1.shown_at_ms, 1200)

        # Advance 12 more lines through Chat's 12-slot round-robin so seq 13 reuses
        # first_slot (m_MCPShownSeq changes from 1 to 13).
        latest_slot = first_slot
        for i in range(2, 14):
            latest_slot = model.emit_line(f"Line {i}", t_ms=1000 + i * 10, alpha_after_set=0.8)
        self.assertIs(latest_slot, first_slot)
        self.assertEqual(first_slot.m_MCPShownSeq, 13)

        after_wrap = model.read(marker=0, max_lines=20)
        self.assertEqual(after_wrap.lines.Count(), 13)
        replaced_line = after_wrap.lines.Get(0)
        active_line = after_wrap.lines.Get(12)
        self.assertIs(replaced_line, line1)

        # Positive: seq 1's ChatLine now shows seq 13 -> replaced=True and zeroed fields,
        # while shown_at_ms stays at the sampled 1200 ms.
        self.assertTrue(replaced_line.replaced)
        self.assertFalse(replaced_line.visible_hierarchy)
        self.assertEqual(replaced_line.alpha, 0.0)
        self.assertEqual(replaced_line.screen_x, 0.0)
        self.assertEqual(replaced_line.screen_y, 0.0)
        self.assertEqual(replaced_line.screen_w, 0.0)
        self.assertEqual(replaced_line.screen_h, 0.0)
        self.assertEqual(replaced_line.shown_at_ms, 1200)

        # Negative: seq 13 currently occupies that same ChatLine -> replaced=False.
        self.assertEqual(active_line.seq, 13)
        self.assertFalse(active_line.replaced)
        self.assertTrue(active_line.visible_hierarchy)
        self.assertAlmostEqual(active_line.alpha, 0.8, places=6)
        self.assertEqual(
            (
                active_line.screen_x,
                active_line.screen_y,
                active_line.screen_w,
                active_line.screen_h,
            ),
            (24.0, 800.0, 420.0, 18.0),
        )

        # Unlogged Set on that ChatLine (e.g. params=null) resets m_MCPShownSeq to 0,
        # marking seq 13 replaced too without matching any logged entry.
        first_slot.Set(None)
        self.assertEqual(first_slot.m_MCPShownSeq, 0)
        self.assertTrue(model.read(marker=12).lines.Get(0).replaced)

        # Destroying Chat lines marks every remaining active line replaced as well.
        assert model.chat is not None
        model.chat.Destroy()
        destroyed_read = model.read(marker=0, max_lines=20)
        self.assertTrue(all(item.replaced for item in destroyed_read.lines))

    def test_sampling_sets_first_qualifying_frame_within_2000ms_and_leaves_late_or_replaced_minus_one(self) -> None:
        model = _ChatVisibilityModel()

        # Positive case from brief: recorded at t=1000, alpha 0.2 at t+100 (1100),
        # alpha 0.9 & visible at t+450 (1450) -> shown_at_ms = 1450, kept on later frames.
        slot1 = model.emit_line("Fade-in line", t_ms=1000)
        assert slot1.m_RootWidget is not None
        self.assertEqual(model.read().lines.Get(0).shown_at_ms, -1)

        slot1.m_RootWidget.SetAlpha(0.2)
        model.tick_frame(1100)
        self.assertEqual(model.read().lines.Get(0).shown_at_ms, -1)

        slot1.m_RootWidget.SetAlpha(0.89)
        model.tick_frame(1300)
        self.assertEqual(model.read().lines.Get(0).shown_at_ms, -1)

        slot1.m_RootWidget.SetAlpha(0.9)
        model.tick_frame(1450)
        self.assertEqual(model.read().lines.Get(0).shown_at_ms, 1450)

        slot1.m_RootWidget.SetAlpha(1.0)
        model.tick_frame(1800)
        self.assertEqual(model.read().lines.Get(0).shown_at_ms, 1450)

        # Negative case from brief: HUD hidden until t+2100 -> stays -1, and a visible
        # frame at t+2100 does not change it.
        model.hud_root.Show(False)
        slot2 = model.emit_line("Hidden HUD line", t_ms=5000, alpha_after_set=1.0)
        assert slot2.m_RootWidget is not None
        model.tick_frame(5100)
        model.tick_frame(6500)
        model.tick_frame(7000)
        self.assertEqual(model.read(marker=1).lines.Get(0).shown_at_ms, -1)

        model.hud_root.Show(True)
        model.tick_frame(7100)  # t + 2100 > 2000 ms window
        self.assertEqual(model.read(marker=1).lines.Get(0).shown_at_ms, -1)

        # Exact 2000 ms boundary: t+2000 qualifies (positive), t+2001 stops (negative).
        model.hud_root.Show(False)
        model.emit_line("Boundary inside", t_ms=10000, alpha_after_set=0.95)
        model.hud_root.Show(True)
        model.tick_frame(12000)  # 12000 - 10000 == 2000
        self.assertEqual(model.read(marker=2).lines.Get(0).shown_at_ms, 12000)

        model.hud_root.Show(False)
        model.emit_line("Boundary outside", t_ms=20000, alpha_after_set=0.95)
        model.hud_root.Show(True)
        model.tick_frame(22001)  # 22001 - 20000 == 2001 > 2000
        self.assertEqual(model.read(marker=3).lines.Get(0).shown_at_ms, -1)

        # Replaced before reaching alpha >= 0.9: stays -1 even when the reused ChatLine
        # becomes visible at alpha 1.0 for the newer line.
        evicted_slot = model.emit_line("Replaced before fade", t_ms=30000)
        replaced_seq = evicted_slot.m_MCPShownSeq
        for n in range(12):
            reused = model.emit_line(
                f"Burst {n}", t_ms=30050, alpha_after_set=1.0
            )
        self.assertIs(reused, evicted_slot)
        model.tick_frame(30200)
        burst_read = model.read(marker=replaced_seq - 1, max_lines=20)
        self.assertEqual(burst_read.lines.Get(0).seq, replaced_seq)
        self.assertEqual(burst_read.lines.Get(0).shown_at_ms, -1)
        self.assertEqual(burst_read.lines.Get(12).shown_at_ms, 30200)

        # Text widget alpha included during sampling: line root and every ancestor at
        # alpha 1.0 and visible, text widget at 0.2 -> shown_at_ms stays -1; then text
        # at 0.95 -> shown_at_ms is that frame's time, plus mutant control starting
        # the product at textWidget.GetParent() inside SampleShownLines.
        def check_text_widget_alpha_sampling(chat_log_src: str) -> None:
            m = _ChatVisibilityModel(chat_log_src=chat_log_src)
            slot = m.emit_line("Text alpha fade", t_ms=1000, alpha_after_set=1.0)
            assert slot.m_RootWidget is not None
            assert slot.m_TextWidget is not None
            self.assertTrue(m.hud_root.IsVisible())
            self.assertEqual(m.hud_root.GetAlpha(), 1.0)
            self.assertTrue(m.chat_frame.IsVisible())
            self.assertEqual(m.chat_frame.GetAlpha(), 1.0)
            self.assertTrue(slot.m_RootWidget.IsVisible())
            self.assertEqual(slot.m_RootWidget.GetAlpha(), 1.0)
            slot.m_TextWidget.Show(True)
            slot.m_TextWidget.SetAlpha(0.2)

            m.tick_frame(1100)
            low_read = m.read().lines.Get(0)
            self.assertAlmostEqual(low_read.alpha, 0.2, places=6)
            self.assertEqual(
                low_read.shown_at_ms,
                -1,
                "sampling must include text widget alpha",
            )

            slot.m_TextWidget.SetAlpha(0.95)
            m.tick_frame(1450)
            high_read = m.read().lines.Get(0)
            self.assertAlmostEqual(high_read.alpha, 0.95, places=6)
            self.assertEqual(high_read.shown_at_ms, 1450)

        base_src = CHAT_LOG_PATH.read_text(encoding="utf-8")
        check_text_widget_alpha_sampling(base_src)
        skip_text_alpha_sample = _mutate_in_scope(
            base_src,
            "void SampleShownLines(int nowMs)",
            "currentWidget = textWidget;",
            "currentWidget = textWidget.GetParent();",
        )
        with self.assertRaisesRegex(
            AssertionError, "sampling must include text widget alpha"
        ):
            check_text_widget_alpha_sampling(skip_text_alpha_sample)

    def test_mission_on_update_never_creates_log_and_samples_between_super_and_bridge_tick(self) -> None:
        model = _ChatVisibilityModel()
        self.assertIsNone(model.chat_log)

        # Negative: ticking OnUpdate with no chat activity must not allocate MCPChatLog.
        model.tick_frame(500)
        self.assertIsNone(model.chat_log)
        self.assertEqual(model.events, ["frame_advance", "super_update", "bridge_tick"])

        # Positive: once a line exists, super.OnUpdate's fade update is sampled on the
        # same frame before bridge.OnTick dispatches pending client commands.
        slot = model.emit_line("Same-frame fade", t_ms=1000)
        assert slot.m_RootWidget is not None
        model.events.clear()
        seen_in_bridge_tick: list[int] = []
        model.on_bridge_tick = lambda _dt: seen_in_bridge_tick.append(
            model.read().lines.Get(0).shown_at_ms
        )
        model.tick_frame(
            1450,
            on_super_update=lambda _dt: slot.m_RootWidget.SetAlpha(0.92),
        )
        self.assertEqual(
            model.events,
            ["frame_advance", "super_update", "sample", "bridge_tick"],
        )
        self.assertEqual(seen_in_bridge_tick, [1450])

    def test_hud_shown_reflects_hud_panel_and_is_false_without_hud(self) -> None:
        def check_hud_panel_own_visibility(chat_log_src: str) -> None:
            model = _ChatVisibilityModel(chat_log_src=chat_log_src)
            model.emit_line("HUD check", t_ms=1000, alpha_after_set=1.0)

            # Panel shown while its HUD root is hidden -> hud_shown is still True
            # (hudPanel.IsVisible(), not IsVisibleHierarchy()).
            model.hud_panel.Show(True)
            model.hud_root.Show(False)
            hidden_root_read = model.read()
            self.assertTrue(
                hidden_root_read.hud_shown,
                "hud_shown must reflect hudPanel.IsVisible()",
            )
            self.assertFalse(hidden_root_read.lines.Get(0).visible_hierarchy)

            # Panel hidden while its HUD root is shown -> False.
            model.hud_root.Show(True)
            model.hud_panel.Show(False)
            self.assertFalse(model.read().hud_shown)

            # Panel and HUD root both shown -> True.
            model.hud_panel.Show(True)
            self.assertTrue(model.read().hud_shown)

            # No HUD panel / no HUD / not in mission / no game -> False.
            assert model.hud is not None
            model.hud.panel = None
            self.assertFalse(model.read().hud_shown)

            model.hud = None
            self.assertFalse(model.read().hud_shown)

            model.hud = _FakeHud(model.hud_panel)
            model.hud_panel.Show(True)
            model.in_game = False
            self.assertFalse(model.read().hud_shown)

            model.in_game = True
            model.has_game = False
            self.assertFalse(model.read().hud_shown)

        base_src = CHAT_LOG_PATH.read_text(encoding="utf-8")
        check_hud_panel_own_visibility(base_src)
        hierarchy_hud_mutant = _mutate_in_scope(
            base_src,
            "void FillReadVisibility(MCPChatRead outRead)",
            "hudPanel.IsVisible()",
            "hudPanel.IsVisibleHierarchy()",
        )
        with self.assertRaisesRegex(
            AssertionError, r"hud_shown must reflect hudPanel\.IsVisible\(\)"
        ):
            check_hud_panel_own_visibility(hierarchy_hud_mutant)

    def _run_chain_matrix(self, chat_log_src: str, *, use_subtest: bool) -> None:
        model = _ChatVisibilityModel(chat_log_src=chat_log_src)
        widget_names = ("m_TextWidget", "m_RootWidget", "chat_frame", "hud_root")
        cases: list[tuple[str, str, tuple[str, ...]]] = []
        for widget_name in widget_names:
            cases.append((f"{widget_name}:alpha=0.5", "alpha_half", (widget_name,)))
            cases.append((f"{widget_name}:hidden", "hidden", (widget_name,)))
        cases.append(
            (
                "m_RootWidget+chat_frame:alpha=0.9x0.9",
                "alpha_pair_09",
                ("m_RootWidget", "chat_frame"),
            )
        )

        for case_idx, (label, mode, targets) in enumerate(cases):
            ctx = (
                self.subTest(case=label)
                if use_subtest
                else contextlib.nullcontext()
            )
            with ctx:
                model.hud_root.Show(True)
                model.hud_root.SetAlpha(1.0)
                model.chat_frame.Show(True)
                model.chat_frame.SetAlpha(1.0)

                t0 = 1000 + case_idx * 5000
                slot = model.emit_line(
                    f"Chain matrix {case_idx}: {label}",
                    t_ms=t0,
                    alpha_after_set=1.0,
                )
                assert slot.m_TextWidget is not None
                assert slot.m_RootWidget is not None
                slot.m_TextWidget.Show(True)
                slot.m_TextWidget.SetAlpha(1.0)
                slot.m_RootWidget.Show(True)
                slot.m_RootWidget.SetAlpha(1.0)

                seq = slot.m_MCPShownSeq
                self.assertEqual(seq, case_idx + 1)

                chain: dict[str, _FakeWidget] = {
                    "m_TextWidget": slot.m_TextWidget,
                    "m_RootWidget": slot.m_RootWidget,
                    "chat_frame": model.chat_frame,
                    "hud_root": model.hud_root,
                }
                self.assertIs(chain["m_TextWidget"].GetParent(), chain["m_RootWidget"])
                self.assertIs(chain["m_RootWidget"].GetParent(), chain["chat_frame"])
                self.assertIs(chain["chat_frame"].GetParent(), chain["hud_root"])
                self.assertIsNone(chain["hud_root"].GetParent())

                if mode == "alpha_half":
                    target_name = targets[0]
                    chain[target_name].SetAlpha(0.5)
                    for other_name, other_widget in chain.items():
                        self.assertTrue(other_widget.IsVisible())
                        if other_name != target_name:
                            self.assertEqual(other_widget.GetAlpha(), 1.0)

                    first_batch = model.read(marker=seq - 1)
                    self.assertEqual(first_batch.lines.Count(), 1)
                    initial = first_batch.lines.Get(0)
                    self.assertEqual(initial.seq, seq)
                    self.assertEqual(initial.t_client_ms, t0)
                    self.assertEqual(initial.shown_at_ms, -1)
                    self.assertTrue(
                        initial.visible_hierarchy,
                        f"{label}: expected visible_hierarchy True",
                    )
                    self.assertAlmostEqual(
                        initial.alpha,
                        0.5,
                        delta=1e-6,
                        msg=f"{label}: expected read alpha 0.5",
                    )

                    model.tick_frame(t0 + 450)
                    after_low = model.read(marker=seq - 1).lines.Get(0)
                    self.assertEqual(
                        after_low.shown_at_ms,
                        -1,
                        f"{label}: expected shown_at_ms -1 while alpha=0.5",
                    )

                    chain[target_name].SetAlpha(1.0)
                    model.tick_frame(t0 + 900)
                    after_restored = model.read(marker=seq - 1).lines.Get(0)
                    self.assertAlmostEqual(after_restored.alpha, 1.0, delta=1e-6)
                    self.assertEqual(
                        after_restored.shown_at_ms,
                        t0 + 900,
                        f"{label}: expected shown_at_ms {t0 + 900} after restoring alpha",
                    )
                elif mode == "hidden":
                    target_name = targets[0]
                    chain[target_name].Show(False)
                    for other_name, other_widget in chain.items():
                        self.assertEqual(other_widget.GetAlpha(), 1.0)
                        if other_name != target_name:
                            self.assertTrue(other_widget.IsVisible())

                    first_batch = model.read(marker=seq - 1)
                    self.assertEqual(first_batch.lines.Count(), 1)
                    initial = first_batch.lines.Get(0)
                    self.assertEqual(initial.seq, seq)
                    self.assertEqual(initial.t_client_ms, t0)
                    self.assertEqual(initial.shown_at_ms, -1)
                    self.assertFalse(
                        initial.visible_hierarchy,
                        f"{label}: expected visible_hierarchy False",
                    )

                    model.tick_frame(t0 + 450)
                    after_hidden = model.read(marker=seq - 1).lines.Get(0)
                    self.assertEqual(
                        after_hidden.shown_at_ms,
                        -1,
                        f"{label}: expected shown_at_ms -1 while hidden",
                    )

                    chain[target_name].Show(True)
                    model.tick_frame(t0 + 900)
                    after_shown = model.read(marker=seq - 1).lines.Get(0)
                    self.assertTrue(after_shown.visible_hierarchy)
                    self.assertEqual(
                        after_shown.shown_at_ms,
                        t0 + 900,
                        f"{label}: expected shown_at_ms {t0 + 900} after showing widget",
                    )
                else:
                    self.assertEqual(mode, "alpha_pair_09")
                    for target_name in targets:
                        chain[target_name].SetAlpha(0.9)
                    for other_name, other_widget in chain.items():
                        self.assertTrue(other_widget.IsVisible())
                        expected_alpha = 0.9 if other_name in targets else 1.0
                        self.assertEqual(other_widget.GetAlpha(), expected_alpha)

                    first_batch = model.read(marker=seq - 1)
                    self.assertEqual(first_batch.lines.Count(), 1)
                    initial = first_batch.lines.Get(0)
                    self.assertEqual(initial.seq, seq)
                    self.assertEqual(initial.t_client_ms, t0)
                    self.assertEqual(initial.shown_at_ms, -1)
                    self.assertTrue(initial.visible_hierarchy)
                    self.assertAlmostEqual(
                        initial.alpha,
                        0.81,
                        delta=1e-6,
                        msg=f"{label}: expected read alpha 0.81",
                    )

                    model.tick_frame(t0 + 450)
                    after_tick = model.read(marker=seq - 1).lines.Get(0)
                    self.assertAlmostEqual(after_tick.alpha, 0.81, delta=1e-6)
                    self.assertEqual(
                        after_tick.shown_at_ms,
                        -1,
                        f"{label}: expected no sampling hit at alpha 0.81",
                    )

    def test_widget_chain_matrix_for_read_and_sampling(self) -> None:
        base_src = CHAT_LOG_PATH.read_text(encoding="utf-8")
        self._run_chain_matrix(base_src, use_subtest=True)

        sample_scope = "void SampleShownLines(int nowMs)"
        fill_scope = "void FillReadVisibility(MCPChatRead outRead)"
        stop_after_root_replacement = (
            "if (currentWidget == textWidget) "
            "{ currentWidget = currentWidget.GetParent(); } "
            "else { currentWidget = null; }"
        )

        # D15(a): product stops after m_RootWidget (skips ChatFrameWidget and HudRootWidget).
        sample_stops_after_root = _mutate_in_scope(
            base_src,
            sample_scope,
            "currentWidget = currentWidget.GetParent();",
            stop_after_root_replacement,
        )
        self.assertNotEqual(clean(sample_stops_after_root), clean(base_src))
        with self.assertRaisesRegex(
            AssertionError,
            r"chat_frame:alpha=0\.5: expected shown_at_ms -1 while alpha=0\.5",
        ):
            self._run_chain_matrix(sample_stops_after_root, use_subtest=False)

        fill_stops_after_root = _mutate_in_scope(
            base_src,
            fill_scope,
            "currentWidget = currentWidget.GetParent();",
            stop_after_root_replacement,
        )
        self.assertNotEqual(clean(fill_stops_after_root), clean(base_src))
        with self.assertRaisesRegex(
            AssertionError,
            r"chat_frame:alpha=0\.5: expected read alpha 0\.5",
        ):
            self._run_chain_matrix(fill_stops_after_root, use_subtest=False)

        # D15(b): textWidget.IsVisibleHierarchy() -> textWidget.GetParent().IsVisibleHierarchy().
        sample_parent_visibility = _mutate_in_scope(
            base_src,
            sample_scope,
            "textWidget.IsVisibleHierarchy()",
            "textWidget.GetParent().IsVisibleHierarchy()",
        )
        self.assertNotEqual(clean(sample_parent_visibility), clean(base_src))
        with self.assertRaisesRegex(
            AssertionError,
            r"m_TextWidget:hidden: expected shown_at_ms -1 while hidden",
        ):
            self._run_chain_matrix(sample_parent_visibility, use_subtest=False)

        fill_parent_visibility = _mutate_in_scope(
            base_src,
            fill_scope,
            "textWidget.IsVisibleHierarchy()",
            "textWidget.GetParent().IsVisibleHierarchy()",
        )
        self.assertNotEqual(clean(fill_parent_visibility), clean(base_src))
        with self.assertRaisesRegex(
            AssertionError,
            r"m_TextWidget:hidden: expected visible_hierarchy False",
        ):
            self._run_chain_matrix(fill_parent_visibility, use_subtest=False)

    def test_repeated_read_updates_rect_alpha_and_visibility_without_caching(self) -> None:
        initial_rect = (24.0, 800.0, 420.0, 18.0)
        repositioned_rect = (28.0, 780.0, 380.0, 20.0)
        third_rect = (30.0, 760.0, 410.0, 18.0)

        def check_no_caching(chat_log_src: str) -> None:
            m = _ChatVisibilityModel(chat_log_src=chat_log_src)
            slot = m.emit_line("Shifted chat line", t_ms=1000, alpha_after_set=0.4)
            assert slot.m_RootWidget is not None
            assert slot.m_TextWidget is not None
            slot.m_TextWidget.screen_rect = initial_rect

            # Sampling frame before fade completes leaves shown_at_ms at -1 without
            # caching a permanent miss.
            m.tick_frame(1100)

            first_entry = m.read().lines.Get(0)
            self.assertEqual(first_entry.seq, 1)
            self.assertFalse(first_entry.replaced)
            self.assertEqual(
                (
                    first_entry.screen_x,
                    first_entry.screen_y,
                    first_entry.screen_w,
                    first_entry.screen_h,
                ),
                initial_rect,
            )
            self.assertAlmostEqual(first_entry.alpha, 0.4, delta=1e-6)
            self.assertTrue(first_entry.visible_hierarchy)
            self.assertEqual(first_entry.shown_at_ms, -1)
            self.assertEqual(slot.m_TextWidget.screen_pos_reads, 1)
            self.assertEqual(slot.m_TextWidget.screen_size_reads, 1)

            # Sampling re-reads live widget alpha on the next frame inside 2000 ms.
            slot.m_RootWidget.SetAlpha(1.0)
            m.tick_frame(1450)

            # Reposition text widget (chat.c:134-143), change ancestor/text alpha
            # chain, and hide text widget before reading the SAME stored entry again.
            slot.m_TextWidget.screen_rect = repositioned_rect
            m.chat_frame.SetAlpha(0.6)
            slot.m_TextWidget.SetAlpha(0.5)
            slot.m_TextWidget.Show(False)

            second_entry = m.read().lines.Get(0)
            self.assertIs(second_entry, first_entry)
            self.assertFalse(second_entry.replaced)
            self.assertEqual(
                (
                    second_entry.screen_x,
                    second_entry.screen_y,
                    second_entry.screen_w,
                    second_entry.screen_h,
                ),
                repositioned_rect,
                "second read must return updated text widget screen rect",
            )
            self.assertAlmostEqual(
                second_entry.alpha,
                0.3,
                delta=1e-6,
                msg="second read must return updated alpha",
            )
            self.assertFalse(
                second_entry.visible_hierarchy,
                "second read must return updated visible_hierarchy",
            )
            self.assertEqual(second_entry.shown_at_ms, 1450)
            self.assertEqual(slot.m_TextWidget.screen_pos_reads, 2)
            self.assertEqual(slot.m_TextWidget.screen_size_reads, 2)

            # Third read flips visibility back to True and updates rect and alpha again.
            slot.m_TextWidget.screen_rect = third_rect
            slot.m_TextWidget.Show(True)
            slot.m_TextWidget.SetAlpha(1.0)
            m.chat_frame.SetAlpha(1.0)
            m.hud_root.SetAlpha(0.75)

            third_entry = m.read().lines.Get(0)
            self.assertIs(third_entry, first_entry)
            self.assertEqual(
                (
                    third_entry.screen_x,
                    third_entry.screen_y,
                    third_entry.screen_w,
                    third_entry.screen_h,
                ),
                third_rect,
            )
            self.assertAlmostEqual(third_entry.alpha, 0.75, delta=1e-6)
            self.assertTrue(third_entry.visible_hierarchy)
            self.assertEqual(slot.m_TextWidget.screen_pos_reads, 3)
            self.assertEqual(slot.m_TextWidget.screen_size_reads, 3)

        base_src = CHAT_LOG_PATH.read_text(encoding="utf-8")
        check_no_caching(base_src)

        # D15(c): rect read/stored only while screen_w == 0.0 keeps stale rect on re-read.
        fill_scope = "void FillReadVisibility(MCPChatRead outRead)"
        rect_once_mutant = _mutate_in_scope(
            base_src,
            fill_scope,
            "lineItem.screen_x = sx;\n"
            "\t\t\t\t\tlineItem.screen_y = sy;\n"
            "\t\t\t\t\tlineItem.screen_w = sw;\n"
            "\t\t\t\t\tlineItem.screen_h = sh;",
            "if (lineItem.screen_w == 0.0)\n"
            "\t\t\t\t\t{\n"
            "\t\t\t\t\t\tlineItem.screen_x = sx;\n"
            "\t\t\t\t\t\tlineItem.screen_y = sy;\n"
            "\t\t\t\t\t\tlineItem.screen_w = sw;\n"
            "\t\t\t\t\t\tlineItem.screen_h = sh;\n"
            "\t\t\t\t\t}",
        )
        self.assertNotEqual(clean(rect_once_mutant), clean(base_src))
        self.assertNotEqual(
            clean(_rewrite_fill_screen_queries(rect_once_mutant)),
            clean(_rewrite_fill_screen_queries(base_src)),
        )
        with self.assertRaisesRegex(
            AssertionError, "second read must return updated text widget screen rect"
        ):
            check_no_caching(rect_once_mutant)

    def _run_zero_line_reads(
        self,
        chat_log_src: str,
        *,
        use_subtest: bool,
        log_modes: tuple[str, ...] = ("empty_log", "marker_at_last_seq"),
    ) -> None:
        hud_cases = (
            ("panel_shown", True),
            ("panel_hidden", False),
            ("hud_panel_null", False),
            ("hud_null", False),
            ("no_mission", False),
            ("no_game", False),
        )
        for log_mode in log_modes:
            for hud_case, expected_hud_shown in hud_cases:
                label = f"{log_mode}:{hud_case}"
                ctx = (
                    self.subTest(case=label)
                    if use_subtest
                    else contextlib.nullcontext()
                )
                with ctx:
                    model = _ChatVisibilityModel(chat_log_src=chat_log_src)
                    if log_mode == "empty_log":
                        marker = 0
                    else:
                        self.assertEqual(log_mode, "marker_at_last_seq")
                        model.emit_line(
                            "Earlier line 1", t_ms=1000, alpha_after_set=1.0
                        )
                        latest_slot = model.emit_line(
                            "Earlier line 2", t_ms=1100, alpha_after_set=1.0
                        )
                        assert model.chat_log is not None
                        self.assertEqual(model.chat_log.ns["m_Lines"].Count(), 2)
                        marker = model.chat_log.GetLastSeq()
                        self.assertEqual(marker, 2)
                        self.assertEqual(latest_slot.m_MCPShownSeq, marker)

                    model.hud_root.Show(True)
                    model.hud_panel.Show(True)
                    if hud_case == "panel_shown":
                        self.assertTrue(model.hud_panel.IsVisible())
                    elif hud_case == "panel_hidden":
                        model.hud_panel.Show(False)
                        self.assertFalse(model.hud_panel.IsVisible())
                    elif hud_case == "hud_panel_null":
                        assert model.hud is not None
                        model.hud.panel = None
                        self.assertIsNone(model.hud.GetHudPanelWidget())
                    elif hud_case == "hud_null":
                        model.hud = None
                        self.assertIsNone(model.mission.GetHud())
                    elif hud_case == "no_mission":
                        model.in_game = False
                        self.assertIsNone(model._game.GetMission())
                    elif hud_case == "no_game":
                        model.has_game = False
                        self.assertFalse(model.has_game)
                    else:  # pragma: no cover
                        self.fail(f"unknown hud_case: {hud_case!r}")

                    read_out = model.read(marker=marker)
                    self.assertEqual(read_out.lines.Count(), 0)
                    self.assertEqual(read_out.marker, marker)
                    self.assertIsInstance(read_out.hud_shown, bool)
                    if expected_hud_shown:
                        self.assertTrue(
                            read_out.hud_shown,
                            f"{label}: expected hud_shown True",
                        )
                    else:
                        self.assertFalse(
                            read_out.hud_shown,
                            f"{label}: expected hud_shown False",
                        )

    def test_reads_without_lines_report_hud_shown_and_empty_sampling_is_noop(self) -> None:
        base_src = CHAT_LOG_PATH.read_text(encoding="utf-8")
        self._run_zero_line_reads(base_src, use_subtest=True)

        with self.subTest(case="empty_log:sampling_noop"):
            empty_model = _ChatVisibilityModel(chat_log_src=base_src)
            empty_model.tick_frame(1050)
            empty_log = empty_model.mission.GetMCPChatLog()
            empty_log.SampleShownLines(1100)
            empty_model.tick_frame(1150)

        # D17: early return when outRead.lines.Count() == 0 leaves hud_shown False
        # even while HudPanel is visible, both on an empty log and at newest seq.
        fill_scope = "void FillReadVisibility(MCPChatRead outRead)"
        early_return_on_empty_lines = _mutate_in_scope(
            base_src,
            fill_scope,
            "outRead.hud_shown = false;",
            "if (outRead.lines.Count() == 0)\n"
            "\t\t{\n"
            "\t\t\treturn;\n"
            "\t\t}\n"
            "\t\toutRead.hud_shown = false;",
        )
        self.assertNotEqual(clean(early_return_on_empty_lines), clean(base_src))
        self.assertNotEqual(
            clean(_rewrite_fill_screen_queries(early_return_on_empty_lines)),
            clean(_rewrite_fill_screen_queries(base_src)),
        )
        with self.assertRaisesRegex(
            AssertionError,
            r"empty_log:panel_shown: expected hud_shown True",
        ):
            self._run_zero_line_reads(
                early_return_on_empty_lines,
                use_subtest=False,
                log_modes=("empty_log",),
            )
        with self.assertRaisesRegex(
            AssertionError,
            r"marker_at_last_seq:panel_shown: expected hud_shown True",
        ):
            self._run_zero_line_reads(
                early_return_on_empty_lines,
                use_subtest=False,
                log_modes=("marker_at_last_seq",),
            )


class ChatReadVisibilityEveryLineTest(unittest.TestCase):
    """Every line in its window is sampled, every returned line is filled from its own
    ChatLine, seq 0 never matches, and drops sharing the seq counter do not shift the
    seq a ChatLine records. Each check also runs on mutants that must make it fail."""

    SAMPLE = "void SampleShownLines(int nowMs)"
    FILL = "void FillReadVisibility(MCPChatRead outRead)"
    FIND = "protected ChatLine FindChatLineBySeq(int seq)"

    def _assert_mutants_fail(
        self,
        check: Callable[[str], None],
        message: str,
        mutants: tuple[tuple[str, str, str], ...],
    ) -> None:
        base_src = CHAT_LOG_PATH.read_text(encoding="utf-8")
        check(base_src)
        for scope, target, replacement in mutants:
            with self.subTest(scope=scope, mutant=replacement):
                mutant_src = _mutate_in_scope(base_src, scope, target, replacement)
                with self.assertRaisesRegex(AssertionError, message):
                    check(mutant_src)

    def _check_one_frame_samples_every_recent_line(self, chat_log_src: str) -> None:
        model = _ChatVisibilityModel(chat_log_src=chat_log_src)
        model.emit_line("Old line", t_ms=100, alpha_after_set=1.0)
        for offset in (0, 10, 20):
            model.emit_line(f"Recent line {offset}", t_ms=3000 + offset, alpha_after_set=1.0)
        model.tick_frame(3450)
        stamps = [item.shown_at_ms for item in model.read().lines]
        self.assertEqual(
            stamps,
            [-1, 3450, 3450, 3450],
            "one frame must sample every line inside its 2000 ms window",
        )

    def test_one_frame_samples_every_line_inside_its_window(self) -> None:
        self._assert_mutants_fail(
            self._check_one_frame_samples_every_recent_line,
            "one frame must sample every line",
            (
                (self.SAMPLE, "\t\t\tidx = idx - 1;", "\t\t\tidx = -1;"),
                (
                    self.SAMPLE,
                    "lineItem = m_Lines.Get(idx);",
                    "lineItem = m_Lines.Get(m_Lines.Count() - 1);",
                ),
            ),
        )

    def _check_every_returned_line_uses_its_own_chat_line(self, chat_log_src: str) -> None:
        model = _ChatVisibilityModel(chat_log_src=chat_log_src)
        assert model.chat is not None
        slots = list(model.chat.GetMCPChatLines())
        for index, slot in enumerate(slots):
            assert slot.m_TextWidget is not None
            slot.m_TextWidget.SetAlpha(0.5 + index * 0.01)
        shown_by: dict[int, _FakeChatLine] = {}
        for seq in range(1, 14):
            shown_by[seq] = model.emit_line(f"Line {seq}", t_ms=1000 + seq, alpha_after_set=1.0)
        read_out = model.read(marker=0, max_lines=20)
        self.assertEqual([item.seq for item in read_out.lines], list(range(1, 14)))
        for item in read_out.lines:
            if item.seq == 1:
                self.assertTrue(
                    item.replaced, "seq 1 must be replaced: its own ChatLine now shows seq 13"
                )
                continue
            text_widget = shown_by[item.seq].m_TextWidget
            assert text_widget is not None
            self.assertFalse(item.replaced, f"seq {item.seq} must be found in its own ChatLine")
            self.assertTrue(item.visible_hierarchy, f"seq {item.seq} visible in its own ChatLine")
            self.assertAlmostEqual(
                item.alpha,
                text_widget.GetAlpha(),
                places=6,
                msg=f"seq {item.seq} alpha from its own ChatLine",
            )
            self.assertEqual(
                (item.screen_x, item.screen_y, item.screen_w, item.screen_h),
                text_widget.screen_rect,
                f"seq {item.seq} rect from its own ChatLine",
            )

    def test_every_returned_line_is_filled_from_its_own_chat_line(self) -> None:
        self._assert_mutants_fail(
            self._check_every_returned_line_uses_its_own_chat_line,
            "own ChatLine",
            (
                (self.FIND, "idx = 0;", "idx = 1;"),
                (self.FIND, "return chatLine;", "return chatLines.Get(0);"),
                (
                    self.FILL,
                    "lineItem = outRead.lines.Get(lineIdx);",
                    "lineItem = outRead.lines.Get(0);",
                ),
            ),
        )

    def _check_fill_resets_the_widget_for_each_line(self, chat_log_src: str) -> None:
        model = _ChatVisibilityModel(chat_log_src=chat_log_src)
        for seq in range(1, 14):
            model.emit_line(f"Line {seq}", t_ms=1000 + seq, alpha_after_set=1.0)
        stored = {item.seq: item for item in model.read(marker=0, max_lines=20).lines}
        log = model.chat_log
        assert log is not None
        # A found line followed by a replaced one: the replaced line must not reuse
        # the text widget the previous iteration found.
        reordered = model.msg_ns["MCPChatRead"]()
        reordered.lines.Insert(stored[13])
        reordered.lines.Insert(stored[1])
        log.FillReadVisibility(reordered)
        self.assertFalse(stored[13].replaced)
        message = "a replaced line after a found one must not reuse its widget"
        self.assertTrue(stored[1].replaced, message)
        self.assertFalse(stored[1].visible_hierarchy, message)
        self.assertEqual(
            (
                stored[1].alpha,
                stored[1].screen_x,
                stored[1].screen_y,
                stored[1].screen_w,
                stored[1].screen_h,
            ),
            (0.0, 0.0, 0.0, 0.0, 0.0),
            message,
        )

    def test_fill_resets_the_widget_for_each_line(self) -> None:
        self._assert_mutants_fail(
            self._check_fill_resets_the_widget_for_each_line,
            "must not reuse its widget",
            ((self.FILL, "\t\t\t\ttextWidget = null;\n", ""),),
        )

    def _check_seq_zero_never_matches(self, chat_log_src: str) -> None:
        model = _ChatVisibilityModel(chat_log_src=chat_log_src)
        slot = model.emit_line("Line 1", t_ms=1000, alpha_after_set=1.0)
        log = model.chat_log
        assert log is not None and model.chat is not None
        self.assertIs(log.FindChatLineBySeq(1), slot)
        self.assertTrue(any(line.m_MCPShownSeq == 0 for line in model.chat.GetMCPChatLines()))
        message = "seq 0 (a ChatLine that shows no logged line) must never match"
        self.assertIsNone(log.FindChatLineBySeq(0), message)
        self.assertIsNone(log.FindChatLineBySeq(-1), message)

    def test_seq_zero_never_matches_a_chat_line(self) -> None:
        self._assert_mutants_fail(
            self._check_seq_zero_never_matches,
            "must never match",
            ((self.FIND, "if (seq <= 0)", "if (seq < 0)"),),
        )

    def _check_shown_seq_with_drops(self, chat_log_src: str) -> None:
        model = _ChatVisibilityModel(chat_log_src=chat_log_src)
        first = model.emit_line("Line before the drop", t_ms=1000, alpha_after_set=1.0)
        log = model.chat_log
        assert log is not None
        for signature in (
            "static string ClassifyDropReason(ChatMessageEventParams params, bool wasAlive)",
            "void RecordDrop(ChatMessageEventParams params, bool wasAlive)",
        ):
            translate(
                model.exec_chat_log_src,
                signature,
                log.ns,
                class_types=CLASS_TYPES,
                members=LOG_MEMBERS,
            )
        # The real RecordDrop takes seq 2 (reason not_alive) without adding a line.
        log.ns["RecordDrop"](None, False)
        self.assertEqual((log.ns["m_LastSeq"], log.ns["m_TotalLines"]), (2, 1))
        second = model.emit_line("Line after the drop", t_ms=1010, alpha_after_set=1.0)
        message = "a ChatLine must record the seq of its line when a drop took a seq"
        self.assertEqual((first.m_MCPShownSeq, second.m_MCPShownSeq), (1, 3), message)
        self.assertEqual(
            [(item.seq, item.replaced) for item in model.read().lines],
            [(1, False), (3, False)],
            message,
        )

    def test_shown_seq_follows_the_line_seq_when_drops_share_the_counter(self) -> None:
        self._assert_mutants_fail(
            self._check_shown_seq_with_drops,
            "when a drop took a seq",
            (
                ("int GetLastSeq()", "return m_LastSeq;", "return m_TotalLines;"),
                (
                    "override void Set(ChatMessageEventParams params)",
                    "m_MCPShownSeq = chatLog.GetLastSeq();",
                    "m_MCPShownSeq = chatLog.GetTotalLines();",
                ),
            ),
        )

    def _check_walk_reaches_every_pending_line(self, chat_log_src: str) -> None:
        model = _ChatVisibilityModel(chat_log_src=chat_log_src)
        oldest = model.emit_line("Pending, shown later", t_ms=1000, alpha_after_set=1.0)
        hidden = model.emit_line("Hidden", t_ms=1010, alpha_after_set=1.0)
        faint = model.emit_line("Faint", t_ms=1020, alpha_after_set=1.0)
        newest = model.emit_line("Shown first", t_ms=1030, alpha_after_set=1.0)
        assert oldest.m_TextWidget is not None and hidden.m_TextWidget is not None
        assert faint.m_TextWidget is not None and newest.m_TextWidget is not None
        oldest.m_TextWidget.SetAlpha(0.2)
        hidden.m_TextWidget.Show(False)
        faint.m_TextWidget.SetAlpha(0.2)
        model.tick_frame(1100)
        # Newer than the pending oldest line: one already sampled, one hidden, one faint.
        oldest.m_TextWidget.SetAlpha(0.95)
        model.tick_frame(1450)
        self.assertEqual(
            [item.shown_at_ms for item in model.read().lines],
            [1450, -1, -1, 1100],
            "the newest-first walk must reach every pending line of its window",
        )

    def test_sampling_walk_does_not_stop_at_a_sampled_hidden_or_faint_line(self) -> None:
        sampled = "\t\t\t\t\t\t\tlineItem.shown_at_ms = nowMs;\n\t\t\t\t\t\t}\n"
        self._assert_mutants_fail(
            self._check_walk_reaches_every_pending_line,
            "must reach every pending line",
            (
                (
                    self.SAMPLE,
                    "if (nowMs - lineItem.t_client_ms > 2000)",
                    "if (nowMs - lineItem.t_client_ms > 2000 || lineItem.shown_at_ms != -1)",
                ),
                (
                    self.SAMPLE,
                    sampled + "\t\t\t\t\t}\n",
                    sampled
                    + "\t\t\t\t\t}\n\t\t\t\t\telse\n\t\t\t\t\t{\n\t\t\t\t\t\treturn;\n\t\t\t\t\t}\n",
                ),
                (
                    self.SAMPLE,
                    sampled,
                    sampled + "\t\t\t\t\t\telse\n\t\t\t\t\t\t{\n\t\t\t\t\t\t\treturn;\n\t\t\t\t\t\t}\n",
                ),
            ),
        )

    def _check_walk_passes_a_replaced_pending_line(self, chat_log_src: str) -> None:
        model = _ChatVisibilityModel(chat_log_src=chat_log_src)
        oldest = model.emit_line("Pending, shown later", t_ms=1000, alpha_after_set=1.0)
        middle = model.emit_line("Replaced while pending", t_ms=1010, alpha_after_set=1.0)
        model.emit_line("Shown first", t_ms=1020, alpha_after_set=1.0)
        assert oldest.m_TextWidget is not None and middle.m_TextWidget is not None
        assert middle.m_RootWidget is not None
        oldest.m_TextWidget.SetAlpha(0.2)
        middle.m_TextWidget.Show(False)
        model.tick_frame(1100)
        # A Set called on that ChatLine directly (not through Chat's round robin) shows a
        # later line there, so the still pending middle line is replaced.
        model.now_ms = 1030
        middle.Set(_FakeChatParams(text="Set directly on the middle ChatLine"))
        middle.m_RootWidget.SetAlpha(1.0)
        middle.m_TextWidget.Show(True)
        oldest.m_TextWidget.SetAlpha(0.95)
        model.tick_frame(1450)
        lines = list(model.read().lines)
        self.assertEqual([item.seq for item in lines], [1, 2, 3, 4])
        self.assertTrue(lines[1].replaced)
        self.assertEqual(
            [item.shown_at_ms for item in lines],
            [1450, -1, 1100, 1450],
            "the walk must pass a replaced pending line and reach the older ones",
        )

    def test_sampling_walk_does_not_stop_at_a_replaced_line(self) -> None:
        found = "\t\t\t\t\t\ttextWidget = chatLine.m_TextWidget;\n\t\t\t\t\t}\n"
        self._assert_mutants_fail(
            self._check_walk_passes_a_replaced_pending_line,
            "must pass a replaced pending line",
            (
                (
                    self.SAMPLE,
                    found,
                    found + "\t\t\t\t\telse\n\t\t\t\t\t{\n\t\t\t\t\t\treturn;\n\t\t\t\t\t}\n",
                ),
            ),
        )

    def _check_null_states_are_guarded(self, chat_log_src: str) -> None:
        message = "null states must be skipped without an error"
        model = _ChatVisibilityModel(chat_log_src=chat_log_src)
        model.emit_line("Line 1", t_ms=1000, alpha_after_set=1.0)
        model.emit_line("Line 2", t_ms=1010, alpha_after_set=1.0)
        log = model.chat_log
        chat = model.chat
        assert log is not None and chat is not None
        try:
            # No answer, and an answer without its lines array: hud_shown is still read.
            log.FillReadVisibility(None)
            no_lines = model.msg_ns["MCPChatRead"]()
            no_lines.lines = None
            log.FillReadVisibility(no_lines)
            self.assertTrue(no_lines.hud_shown, message)
            # A null entry among the returned lines is skipped and the others are filled.
            stored = list(model.read().lines)
            stored[1].replaced = True
            stored[1].visible_hierarchy = False
            with_null = model.msg_ns["MCPChatRead"]()
            with_null.lines.Insert(None)
            with_null.lines.Insert(stored[1])
            log.FillReadVisibility(with_null)
            self.assertFalse(stored[1].replaced, message)
            self.assertTrue(stored[1].visible_hierarchy, message)
            # A null entry in the ring, then no ring at all: the sampling skips both.
            log.ns["m_Lines"].Insert(None)
            model.tick_frame(1100)
            self.assertEqual([item.shown_at_ms for item in stored], [1100, 1100], message)
            ring = log.ns["m_Lines"]
            log.ns["m_Lines"] = None
            model.tick_frame(1200)
            log.ns["m_Lines"] = ring
            # No Chat on the mission, then a Chat without its lines array: every line reads replaced.
            model.chat = None
            self.assertTrue(all(item.replaced for item in model.read().lines), message)
            model.chat = chat
            chat_lines = chat.m_Lines
            chat.m_Lines = None
            self.assertTrue(all(item.replaced for item in model.read().lines), message)
            chat.m_Lines = chat_lines
        except AssertionError:
            raise
        except Exception as exc:  # a missing guard dereferences null
            self.fail(f"{message}: {exc!r}")

    def test_null_states_are_skipped_without_an_error(self) -> None:
        guard = "\t\tif (!{name})\n\t\t{{\n\t\t\treturn{value};\n\t\t}}\n"
        self._assert_mutants_fail(
            self._check_null_states_are_guarded,
            "null states must be skipped",
            (
                (self.FILL, guard.format(name="outRead", value=""), ""),
                (self.FILL, guard.format(name="outRead.lines", value=""), ""),
                (self.FILL, "\t\t\tif (lineItem)\n", "\t\t\tif (true)\n"),
                (self.SAMPLE, guard.format(name="m_Lines", value=""), ""),
                (self.SAMPLE, "\t\t\tif (lineItem)\n", "\t\t\tif (true)\n"),
                (self.FIND, guard.format(name="chat", value=" null"), ""),
                (self.FIND, guard.format(name="chatLines", value=" null"), ""),
            ),
        )

    def _check_visibility_and_alpha_are_independent(self, chat_log_src: str) -> None:
        model = _ChatVisibilityModel(chat_log_src=chat_log_src)
        slot = model.emit_line("Line", t_ms=1000, alpha_after_set=0.0)
        assert slot.m_TextWidget is not None and slot.m_RootWidget is not None
        message = "visible_hierarchy and alpha must be read independently"
        faded = model.read().lines.Get(0)
        self.assertTrue(faded.visible_hierarchy, message)
        self.assertEqual(faded.alpha, 0.0, message)
        slot.m_RootWidget.SetAlpha(1.0)
        slot.m_TextWidget.Show(False)
        hidden = model.read().lines.Get(0)
        self.assertFalse(hidden.visible_hierarchy, message)
        self.assertAlmostEqual(hidden.alpha, 1.0, places=6, msg=message)

    def test_visibility_and_alpha_are_read_independently(self) -> None:
        self._assert_mutants_fail(
            self._check_visibility_and_alpha_are_independent,
            "must be read independently",
            (
                (
                    self.FILL,
                    "lineItem.visible_hierarchy = textWidget.IsVisibleHierarchy();",
                    "lineItem.visible_hierarchy = textWidget.IsVisibleHierarchy() && hierarchyAlpha > 0.0;",
                ),
                (
                    self.FILL,
                    "lineItem.alpha = hierarchyAlpha;",
                    "lineItem.alpha = hierarchyAlpha;\n\t\t\t\t\tif (!lineItem.visible_hierarchy)\n"
                    "\t\t\t\t\t{\n\t\t\t\t\t\tlineItem.alpha = 0.0;\n\t\t\t\t\t}",
                ),
            ),
        )

    def _check_clear_hides_without_replacing(self, chat_log_src: str) -> None:
        model = _ChatVisibilityModel(chat_log_src=chat_log_src)
        model.emit_line("Shown before Clear", t_ms=1000, alpha_after_set=1.0)
        pending = model.emit_line("Pending at Clear", t_ms=1010, alpha_after_set=1.0)
        assert pending.m_TextWidget is not None and model.chat is not None
        pending.m_TextWidget.SetAlpha(0.2)
        model.tick_frame(1100)
        # DestroyAllMenus calls Chat.Clear (missiongameplay.c:1168-1169), which hides every
        # ChatLine root (chatline.c:130-135) but keeps the ChatLines and what they show.
        model.chat.Clear()
        pending.m_TextWidget.SetAlpha(1.0)
        model.tick_frame(1450)
        message = "Chat.Clear hides the lines without replacing them"
        cleared = list(model.read().lines)
        self.assertEqual([item.replaced for item in cleared], [False, False], message)
        self.assertEqual([item.visible_hierarchy for item in cleared], [False, False], message)
        self.assertEqual([item.shown_at_ms for item in cleared], [1100, -1], message)

    def test_chat_clear_hides_lines_without_replacing_them(self) -> None:
        self._assert_mutants_fail(
            self._check_clear_hides_without_replacing,
            "without replacing them",
            (
                (
                    self.FIND,
                    "if (chatLine && chatLine.m_MCPShownSeq == seq)",
                    "if (chatLine && chatLine.m_MCPShownSeq == seq && chatLine.m_RootWidget.IsVisible())",
                ),
            ),
        )


class ChatReadVisibilitySourceContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.chat_log_src = CHAT_LOG_PATH.read_text(encoding="utf-8")
        self.mission_src = MISSION_GAMEPLAY_PATH.read_text(encoding="utf-8")
        self.messages_src = MESSAGES_PATH.read_text(encoding="utf-8")

    def _mutate_once(self, src: str, target: str, replacement: str) -> str:
        self.assertEqual(clean(src).count(target), 1, f"target not unique: {target!r}")
        mutant = src.replace(target, replacement, 1)
        self.assertNotEqual(clean(mutant), clean(src))
        return mutant

    def _mutate_in_scope(
        self, src: str, scope_header: str, target: str, replacement: str
    ) -> str:
        return _mutate_in_scope(src, scope_header, target, replacement)

    def test_no_chatline_or_widget_reference_stored_in_log_or_messages(self) -> None:
        verify_no_widget_or_chatline_refs(self.chat_log_src, self.messages_src)

        bad_log = self._mutate_once(
            self.chat_log_src,
            "\tprotected int m_MaxEvictedSeq;",
            "\tprotected int m_MaxEvictedSeq;\n\tprotected ChatLine m_LastChatLine;",
        )
        with self.assertRaisesRegex(AssertionError, "MCPChatLog.m_LastChatLine"):
            verify_no_widget_or_chatline_refs(bad_log, self.messages_src)

        bad_line_msg = self._mutate_once(
            self.messages_src,
            "\tint shown_at_ms;",
            "\tint shown_at_ms;\n\tWidget text_widget;",
        )
        with self.assertRaisesRegex(AssertionError, "MCPChatLine.text_widget"):
            verify_no_widget_or_chatline_refs(self.chat_log_src, bad_line_msg)

        bad_read_msg = self._mutate_in_scope(
            self.messages_src,
            "class MCPChatRead",
            "\tbool hud_shown;",
            "\tbool hud_shown;\n\tref Widget hud_panel;",
        )
        with self.assertRaisesRegex(AssertionError, "MCPChatRead.hud_panel"):
            verify_no_widget_or_chatline_refs(self.chat_log_src, bad_read_msg)

    def test_modded_chat_getter_and_chatline_set_seq_after_super(self) -> None:
        verify_modded_chat_and_chatline_contract(self.chat_log_src)

        extra_chat_field = self._mutate_once(
            self.chat_log_src,
            "modded class Chat\n{\n\tarray<ref ChatLine> GetMCPChatLines()",
            "modded class Chat\n{\n\tint m_Extra;\n\tarray<ref ChatLine> GetMCPChatLines()",
        )
        with self.assertRaisesRegex(AssertionError, "declare no fields"):
            verify_modded_chat_and_chatline_contract(extra_chat_field)

        seq_before_super = self._mutate_once(
            self.chat_log_src,
            "\t\tsuper.Set(params);\n\t\tm_MCPShownSeq = 0;",
            "\t\tm_MCPShownSeq = 0;\n\t\tsuper.Set(params);",
        )
        with self.assertRaisesRegex(AssertionError, "before super.Set"):
            verify_modded_chat_and_chatline_contract(seq_before_super)

    def test_new_fields_are_plain_instance_members(self) -> None:
        # The translator's fakes keep one value per instance and read any member, so a
        # static or protected declaration would pass the behavioural tests; check it here.
        verify_new_fields_are_plain_members(self.chat_log_src, self.messages_src)
        for modifier in ("static", "protected", "private"):
            with self.subTest(owner="ChatLine", modifier=modifier):
                bad_log = self._mutate_in_scope(
                    self.chat_log_src,
                    "modded class ChatLine",
                    "\tint m_MCPShownSeq;",
                    f"\t{modifier} int m_MCPShownSeq;",
                )
                with self.assertRaisesRegex(AssertionError, "plain instance member 'int m_MCPShownSeq'"):
                    verify_new_fields_are_plain_members(bad_log, self.messages_src)
            with self.subTest(owner="MCPChatRead", modifier=modifier):
                bad_read = self._mutate_in_scope(
                    self.messages_src,
                    "class MCPChatRead",
                    "\tbool hud_shown;",
                    f"\t{modifier} bool hud_shown;",
                )
                with self.assertRaisesRegex(AssertionError, "plain instance member 'bool hud_shown'"):
                    verify_new_fields_are_plain_members(self.chat_log_src, bad_read)
            with self.subTest(owner="MCPChatLine", modifier=modifier):
                bad_line = self._mutate_in_scope(
                    self.messages_src,
                    "class MCPChatLine",
                    "\tint shown_at_ms;",
                    f"\t{modifier} int shown_at_ms;",
                )
                with self.assertRaisesRegex(AssertionError, "plain instance member 'int shown_at_ms'"):
                    verify_new_fields_are_plain_members(self.chat_log_src, bad_line)

    def test_mission_on_update_sampling_order_and_lazy_log_preservation(self) -> None:
        verify_mission_on_update_sampling_contract(self.mission_src)

        before_super = self._mutate_once(
            self.mission_src,
            "\t\tsuper.OnUpdate(timeslice);\n"
            "\t\tif (m_MCPChatLog && GetGame())\n"
            "\t\t{\n"
            "\t\t\tm_MCPChatLog.SampleShownLines(GetGame().GetTime());\n"
            "\t\t}",
            "\t\tif (m_MCPChatLog && GetGame())\n"
            "\t\t{\n"
            "\t\t\tm_MCPChatLog.SampleShownLines(GetGame().GetTime());\n"
            "\t\t}\n"
            "\t\tsuper.OnUpdate(timeslice);",
        )
        with self.assertRaisesRegex(AssertionError, "after super.OnUpdate"):
            verify_mission_on_update_sampling_contract(before_super)

        eager_log = self._mutate_once(
            self.mission_src,
            "if (m_MCPChatLog && GetGame())",
            "if (GetMCPChatLog() && GetGame())",
        )
        with self.assertRaisesRegex(AssertionError, "must not create MCPChatLog"):
            verify_mission_on_update_sampling_contract(eager_log)

    def test_screen_rect_source_contract_and_read_visibility_hook(self) -> None:
        verify_screen_rect_and_read_fill_contract(self.chat_log_src)
        fill_scope = "void FillReadVisibility(MCPChatRead outRead)"

        wrong_widget = self._mutate_in_scope(
            self.chat_log_src,
            fill_scope,
            "textWidget = chatLine.m_TextWidget;",
            "textWidget = chatLine.m_RootWidget;",
        )
        with self.assertRaisesRegex(AssertionError, "m_TextWidget"):
            verify_screen_rect_and_read_fill_contract(wrong_widget)

        swapped_pos_out_args = self._mutate_in_scope(
            self.chat_log_src,
            fill_scope,
            "textWidget.GetScreenPos(sx, sy);",
            "textWidget.GetScreenPos(sy, sx);",
        )
        with self.assertRaisesRegex(AssertionError, "GetScreenPos"):
            verify_screen_rect_and_read_fill_contract(swapped_pos_out_args)

        swapped_size_out_args = self._mutate_in_scope(
            self.chat_log_src,
            fill_scope,
            "textWidget.GetScreenSize(sw, sh);",
            "textWidget.GetScreenSize(sh, sw);",
        )
        with self.assertRaisesRegex(AssertionError, "GetScreenSize"):
            verify_screen_rect_and_read_fill_contract(swapped_size_out_args)

        writes_shown_at_read = self._mutate_in_scope(
            self.chat_log_src,
            fill_scope,
            "lineItem.replaced = true;",
            "lineItem.replaced = true;\n\t\t\t\t\tlineItem.shown_at_ms = -1;",
        )
        with self.assertRaisesRegex(AssertionError, "shown_at_ms"):
            verify_screen_rect_and_read_fill_contract(writes_shown_at_read)

        missing_hook = self._mutate_in_scope(
            self.chat_log_src,
            "MCPChatRead Read(int marker, int maxLines)",
            "\t\tFillReadVisibility(outRead);\n\t\treturn outRead;",
            "\t\treturn outRead;",
        )
        with self.assertRaisesRegex(AssertionError, "FillReadVisibility"):
            verify_screen_rect_and_read_fill_contract(missing_hook)

        read_scope = "MCPChatRead Read(int marker, int maxLines)"
        read_tail_target = "\t\tFillReadVisibility(outRead);\n\t\treturn outRead;"

        # D19(a): reviewer mutant — early return before FillReadVisibility when lines is empty.
        early_return_empty_lines = self._mutate_in_scope(
            self.chat_log_src,
            read_scope,
            read_tail_target,
            "\t\tif (outRead.lines.Count() == 0)\n"
            "\t\t{\n"
            "\t\t\treturn outRead;\n"
            "\t\t}\n"
            "\t\tFillReadVisibility(outRead);\n"
            "\t\treturn outRead;",
        )
        self.assertNotEqual(clean(early_return_empty_lines), clean(self.chat_log_src))
        with self.assertRaisesRegex(AssertionError, r"exactly 1 return statement"):
            verify_screen_rect_and_read_fill_contract(early_return_empty_lines)

        # D19(b): FillReadVisibility wrapped in a condition before the sole return.
        conditional_fill = self._mutate_in_scope(
            self.chat_log_src,
            read_scope,
            read_tail_target,
            "\t\tif (outRead.lines.Count() > 0)\n"
            "\t\t{\n"
            "\t\t\tFillReadVisibility(outRead);\n"
            "\t\t}\n"
            "\t\treturn outRead;",
        )
        self.assertNotEqual(clean(conditional_fill), clean(self.chat_log_src))
        with self.assertRaisesRegex(AssertionError, r"last two top-level statements"):
            verify_screen_rect_and_read_fill_contract(conditional_fill)

        # D19(c): second return inside the m_Drops loop bypassing FillReadVisibility.
        drop_loop_return = self._mutate_in_scope(
            self.chat_log_src,
            read_scope,
            "\t\t\t\toutRead.dropped.Insert(dropItem);",
            "\t\t\t\toutRead.dropped.Insert(dropItem);\n\t\t\t\treturn outRead;",
        )
        self.assertNotEqual(clean(drop_loop_return), clean(self.chat_log_src))
        with self.assertRaisesRegex(AssertionError, r"exactly 1 return statement"):
            verify_screen_rect_and_read_fill_contract(drop_loop_return)


class ChatReadVisibilityToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def test_tool_returns_visibility_fields_and_hud_shown_verbatim(self) -> None:
        bridge_reply = {
            "ok": 1,
            "chat_read": {
                "lines": [
                    {
                        "seq": 1,
                        "t_client_ms": 4200,
                        "channel": 0,
                        "sender": "",
                        "text": "[LFPG] Cable linked.",
                        "visible_hierarchy": True,
                        "alpha": 0.95,
                        "screen_x": 24.0,
                        "screen_y": 780.0,
                        "screen_w": 310.0,
                        "screen_h": 18.0,
                        "replaced": False,
                        "shown_at_ms": 4650,
                        "cut": False,
                    },
                    {
                        "seq": 2,
                        "t_client_ms": 1000,
                        "channel": 0,
                        "sender": "Survivor",
                        "text": "Earlier replaced line",
                        "visible_hierarchy": False,
                        "alpha": 0.0,
                        "screen_x": 0.0,
                        "screen_y": 0.0,
                        "screen_w": 0.0,
                        "screen_h": 0.0,
                        "replaced": True,
                        "shown_at_ms": -1,
                        "cut": False,
                    },
                ],
                "dropped": [
                    {
                        "seq": 3,
                        "t_client_ms": 4300,
                        "channel": 1,
                        "sender": "",
                        "text": "Filtered system line",
                        "cut": False,
                        "reason": "filtered_by_option",
                    }
                ],
                "marker": 3,
                "truncated": False,
                "hud_shown": True,
            },
        }
        call = AsyncMock(return_value=bridge_reply)
        with patch.object(self.runtime, "call_bridge", new=call):
            raw = await self.app.call_tool(COMMAND, {})
        call.assert_awaited_once_with(
            COMMAND,
            {"marker": 0, "max_lines": 200},
            "client",
            DEFAULT_TOOL_TIMEOUT_S,
        )
        payload = _tool_payload(raw)
        self.assertEqual(payload, bridge_reply)
        self.assertEqual(
            result_prune.prune_unfilled_fields(COMMAND, dict(bridge_reply)),
            bridge_reply,
        )

    async def test_description_names_visibility_fields_shown_at_ms_minus_one_and_hud_shown(self) -> None:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        description = tools[COMMAND].description or ""
        self.assertEqual(
            tool_catalog._compact_description(description),
            "Read lines shown in the client chat since marker. …",
        )
        for token in (
            "visible_hierarchy",
            "alpha",
            "screen_x",
            "screen_y",
            "screen_w",
            "screen_h",
            "replaced",
            "shown_at_ms",
            "-1",
            "2 s",
            "hud_shown",
        ):
            with self.subTest(token=token):
                self.assertIn(token, description)


if __name__ == "__main__":
    unittest.main()
