"""Offline contract and tool tests for chat_read (fb-20261110-164723-0205)."""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import (
    bridge_readiness,
    loopback,
    result_prune,
    session_coordination,
    tool_catalog,
)
from dayz_mcp.server import (
    DEFAULT_TOOL_TIMEOUT_S,
    LEASE_TOOL_LINE,
    ServerConfig,
    ToolError,
    build_app,
)
from tests._addon_paths import addon_root


COMMAND = "chat_read"
MAX_LITERAL_CHARS = 237
INT32_MAX = 2_147_483_647
LINE_SENDER_MAX_CHARS = 1_024
LINE_TEXT_MAX_CHARS = 16_384
DROP_SENDER_MAX_CHARS = 64
DROP_TEXT_MAX_CHARS = 256
LINE_BYTES_MAX = 491_520
SCRIPTS_5_MISSION = addon_root() / "scripts" / "5_Mission"
CHAT_LOG_PATH = SCRIPTS_5_MISSION / "MCPChatLog.c"
MISSION_GAMEPLAY_PATH = SCRIPTS_5_MISSION / "MissionGameplay.c"
CLIENT_BRIDGE_PATH = SCRIPTS_5_MISSION / "MCPClientBridge.c"
MESSAGES_PATH = SCRIPTS_5_MISSION / "MCPMessages.c"

_COMMENT_OR_STRING_RE = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')
_CAPS_DECL_RE = re.compile(
    r"\bprotected\s+const\s+string\s+CLIENT_POLL_CAPS\s*=\s*([^;]+);"
)
_STRING_LITERAL_RE = re.compile(r'"((?:\\.|[^"\\\n])*)"')
_INNER_GENERIC_RE = re.compile(
    r"\b([A-Za-z_]\w*)\s*<\s*"
    r"(?:ref\s+)?[A-Za-z_]\w*"
    r"(?:\s*,\s*(?:ref\s+)?[A-Za-z_]\w*)*"
    r"\s*>"
)
_LOCAL_DECL_RE = re.compile(
    r"^\s*(?:int|float|bool|string|array|map|set|MCPChatLog|MCPChatLine|"
    r"MCPChatDrop|MCPChatRead|MCPClientBridge|MissionGameplay|"
    r"ChatMessageEventParams)\s+([A-Za-z_]\w*)\b"
)
_CONTROL_KEYWORDS = frozenset({"if", "else", "while", "for", "do", "return", "switch"})


def _strip_comments(source: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if token.startswith('"'):
            return token
        return re.sub(r"[^\n]", " ", token)

    return _COMMENT_OR_STRING_RE.sub(_replace, source)


def _blank_strings(cleaned: str) -> str:
    return _STRING_LITERAL_RE.sub(
        lambda m: '"' + (" " * len(m.group(1))) + '"', cleaned
    )


def _strip_generic_type_args(code: str) -> str:
    prev = None
    current = code
    while current != prev:
        prev = current
        current = _INNER_GENERIC_RE.sub(r"\1", current)
    return current


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
    raise AssertionError(f"unterminated block starting at {start}")


def _block_after_strict(source: str, start: int) -> tuple[str, int]:
    brace = source.find("{", start)
    if brace == -1 or source[start:brace].strip():
        raise AssertionError(f"expected immediate '{{' block starting at {start}")
    return _block_after(source, brace)


def _paren_after_strict(source: str, start: int) -> tuple[str, int]:
    open_idx = source.find("(", start)
    if open_idx == -1 or source[start:open_idx].strip():
        raise AssertionError(f"expected immediate '(' starting at {start}")
    depth = 0
    for idx in range(open_idx, len(source)):
        ch = source[idx]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return source[open_idx + 1 : idx], idx + 1
    raise AssertionError(f"unterminated '(' starting at {start}")


def _assert_all_control_headers_braced(code: str) -> None:
    for match in re.finditer(r"\b(if|while|for|else|do)\b", code):
        kw = match.group(1)
        if kw in ("if", "while", "for"):
            _, after_paren = _paren_after_strict(code, match.end())
            rest = code[after_paren:].lstrip()
            if not rest.startswith("{"):
                raise AssertionError(f"brace-less {kw} statement")
        elif kw == "else":
            rest = code[match.end() :].lstrip()
            if not (rest.startswith("{") or re.match(r"^if\b", rest)):
                raise AssertionError("brace-less else statement")
        else:
            rest = code[match.end() :].lstrip()
            if not rest.startswith("{"):
                raise AssertionError("brace-less do statement")


def _top_level_simple_statements(block: str) -> list[str]:
    statements: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in block:
        if ch == "{":
            if depth == 0:
                buf.clear()
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                raise AssertionError("unmatched closing brace")
            if depth == 0:
                buf.clear()
        elif depth == 0:
            if ch == ";":
                stmt = "".join(buf).strip()
                if stmt:
                    statements.append(stmt)
                buf.clear()
            else:
                buf.append(ch)
    if depth != 0 or "".join(buf).strip():
        raise AssertionError("unterminated statement or block")
    return statements


def _class_body(cleaned: str, header_regex: str) -> str:
    matches = list(re.finditer(header_regex, cleaned, re.M))
    if len(matches) != 1:
        raise AssertionError(f"{header_regex!r} matched {len(matches)} times")
    body, _ = _block_after(cleaned, matches[0].end())
    return body


def _method_body(container: str, signature: str) -> str:
    if container.count(signature) != 1:
        raise AssertionError(
            f"{signature!r} found {container.count(signature)} times"
        )
    body, _ = _block_after(container, container.index(signature) + len(signature))
    return body


def _iter_class_methods(class_body: str) -> list[tuple[str, str]]:
    methods: list[tuple[str, str]] = []
    pos = 0
    n = len(class_body)
    while pos < n:
        semi = class_body.find(";", pos)
        brace = class_body.find("{", pos)
        if brace == -1:
            break
        if semi != -1 and semi < brace:
            pos = semi + 1
            continue
        signature = class_body[pos:brace].strip()
        body, pos = _block_after(class_body, brace)
        if "(" in signature:
            methods.append((signature, body))
    return methods


def verify_enforce_hygiene(source: str, label: str) -> None:
    """Verify no ternaries, no non-generic ref outside class members, and no local redeclarations."""
    cleaned = _blank_strings(_strip_comments(source))
    if "?" in cleaned:
        raise AssertionError(f"ternary operator in {label}")
    class_headers = list(
        re.finditer(
            r"^\s*(?:modded\s+)?class\s+[A-Za-z_]\w*(?:\s+extends\s+[A-Za-z_]\w*)?\b",
            cleaned,
            re.M,
        )
    )
    if not class_headers:
        raise AssertionError(f"no class found in {label}")
    cursor = 0
    for header in class_headers:
        if header.start() < cursor:
            continue
        outside = cleaned[cursor : header.start()].strip()
        if outside.replace(";", "").strip():
            raise AssertionError(f"unexpected top-level code in {label}: {outside!r}")
        cbody, cursor = _block_after_strict(cleaned, header.end())
        for signature, body in _iter_class_methods(cbody):
            sig_plain = _strip_generic_type_args(signature)
            if re.search(r"\bref\b", sig_plain):
                raise AssertionError(
                    f"non-generic ref in method signature in {label}: {signature!r}"
                )
            body_plain = _strip_generic_type_args(body)
            if re.search(r"\bref\b", body_plain):
                raise AssertionError(f"local ref declaration in {label}")
            seen: set[str] = set()
            for line in body_plain.splitlines():
                decl = _LOCAL_DECL_RE.match(line)
                if decl:
                    name = decl.group(1)
                    if name in seen:
                        raise AssertionError(f"redeclared local {name} in {label}")
                    seen.add(name)
    trailing = cleaned[cursor:].strip()
    if trailing.replace(";", "").strip():
        raise AssertionError(f"unexpected trailing top-level code in {label}: {trailing!r}")


def verify_chatline_set_override(source: str) -> None:
    """Verify modded ChatLine.Set calls super.Set(params) unconditionally at top level before recording."""
    cleaned = _blank_strings(_strip_comments(source))
    chatline_body = _class_body(cleaned, r"^\s*modded\s+class\s+ChatLine\b")
    set_body = _method_body(
        chatline_body, "override void Set(ChatMessageEventParams params)"
    )

    _assert_all_control_headers_braced(set_body)

    super_stmt = "super.Set(params)"
    super_call = f"{super_stmt};"
    if set_body.count("super.Set") != 1 or set_body.count(super_call) != 1:
        raise AssertionError("ChatLine.Set must call super.Set(params); exactly once")
    top_stmts = _top_level_simple_statements(set_body)
    if top_stmts.count(super_stmt) != 1:
        raise AssertionError(
            "ChatLine.Set must call super.Set(params); unconditionally at top level"
        )
    super_pos = set_body.index(super_call)

    prefix = set_body[:super_pos]
    if re.search(r"\b(?:return|break|continue|switch|while|for|do)\b", prefix):
        raise AssertionError("ChatLine.Set must not return or loop before super.Set(params)")

    record_matches = list(re.finditer(r"\bRecordLine\s*\(\s*params\s*\)", set_body))
    if len(record_matches) != 1:
        raise AssertionError("ChatLine.Set must call RecordLine(params) once")
    if record_matches[0].start() < super_pos:
        raise AssertionError("ChatLine.Set recorded line before super.Set(params)")

    for helper_match in re.finditer(r"\bMCPChatLog\.Get\s*\(", set_body):
        if helper_match.start() < super_pos:
            raise AssertionError("ChatLine.Set queried MCPChatLog before super.Set")


def verify_mission_on_event(source: str) -> None:
    """Verify MissionGameplay.OnEvent calls super.OnEvent on all paths, checks ALIVE as vanilla does, and guards RecordDrop with GetTotalLines()."""
    cleaned = _blank_strings(_strip_comments(source))
    mission_body = _class_body(cleaned, r"^\s*modded\s+class\s+MissionGameplay\b")
    on_event_body = _method_body(
        mission_body, "override void OnEvent(EventType eventTypeId, Param params)"
    )

    if re.search(r"\b(?:return|break|continue|switch)\b", on_event_body):
        raise AssertionError("OnEvent must not early-return or jump past super.OnEvent")

    _assert_all_control_headers_braced(on_event_body)

    if_match = re.search(
        r"\bif\s*\(\s*eventTypeId\s*==\s*ChatMessageEventTypeID\s*\)",
        on_event_body,
    )
    if if_match is None:
        raise AssertionError("OnEvent lacks if (eventTypeId == ChatMessageEventTypeID)")

    before_if = on_event_body[: if_match.start()]
    for raw_line in before_if.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        decl_match = re.match(r"^([A-Za-z_]\w*)\s+[A-Za-z_]\w*\s*;$", stripped)
        if not decl_match or decl_match.group(1) in _CONTROL_KEYWORDS:
            raise AssertionError(
                f"unexpected statement before ChatMessageEventTypeID branch: {stripped!r}"
            )

    chat_branch, after_chat = _block_after_strict(on_event_body, if_match.end())
    tail = on_event_body[after_chat:].strip()
    else_match = re.match(r"^else\b", tail)
    if else_match is None:
        raise AssertionError("OnEvent must have an explicit else branch for other events")
    else_branch, after_else = _block_after_strict(tail, else_match.end())
    if tail[after_else:].strip():
        raise AssertionError(
            f"unexpected statements after OnEvent if/else: {tail[after_else:].strip()!r}"
        )

    super_stmt = "super.OnEvent(eventTypeId, params)"
    super_call = f"{super_stmt};"
    for label, branch in (("chat_branch", chat_branch), ("else_branch", else_branch)):
        if branch.count("super.OnEvent") != 1 or branch.count(super_call) != 1:
            raise AssertionError(f"{label} must call {super_call} once")
        top_stmts = _top_level_simple_statements(branch)
        if top_stmts.count(super_stmt) != 1:
            raise AssertionError(f"{label} calls {super_call} conditionally")

    if "RecordDrop" in before_if or "RecordDrop" in else_branch:
        raise AssertionError("RecordDrop may only appear inside ChatMessageEventTypeID branch")
    if on_event_body.count("RecordDrop") != 1 or chat_branch.count("RecordDrop(") != 1:
        raise AssertionError("ChatMessageEventTypeID branch must call RecordDrop once")

    super_pos = chat_branch.index(super_call)
    before_super = chat_branch[:super_pos]
    after_super = chat_branch[super_pos + len(super_call) :]

    alive_matches = list(
        re.finditer(
            r"^\s*([A-Za-z_]\w*)\s*=\s*\(?\s*m_LifeState\s*==\s*EPlayerStates\.ALIVE\s*\)?\s*;\s*$",
            before_super,
            re.M,
        )
    )
    if (
        len(alive_matches) != 1
        or len(re.findall(r"\bm_LifeState\b", on_event_body)) != 1
        or len(re.findall(r"\bEPlayerStates\b", on_event_body)) != 1
    ):
        raise AssertionError(
            "ChatMessageEventTypeID branch must check m_LifeState == EPlayerStates.ALIVE before super.OnEvent"
        )
    alive_var = alive_matches[0].group(1)
    if len(re.findall(rf"\b{re.escape(alive_var)}\s*=(?!=)", on_event_body)) != 1:
        raise AssertionError(f"{alive_var} must be assigned once from m_LifeState == EPlayerStates.ALIVE")

    snap_matches = list(
        re.finditer(
            r"^\s*([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\.GetTotalLines\s*\(\s*\)\s*;\s*$",
            before_super,
            re.M,
        )
    )
    if len(snap_matches) != 1:
        raise AssertionError(
            "ChatMessageEventTypeID branch must snapshot GetTotalLines() before super.OnEvent"
        )
    count_var = snap_matches[0].group(1)
    log_var = snap_matches[0].group(2)
    log_init = re.search(
        rf"^\s*{re.escape(log_var)}\s*=\s*GetMCPChatLog\s*\(\s*\)\s*;\s*$",
        before_super[: snap_matches[0].start()],
        re.M,
    )
    if log_init is None:
        raise AssertionError("GetMCPChatLog() must initialize log variable before GetTotalLines()")
    if len(re.findall(rf"\b{re.escape(count_var)}\s*=(?!=)", on_event_body)) != 1:
        raise AssertionError(f"{count_var} must not be reassigned")
    if len(re.findall(rf"\b{re.escape(log_var)}\s*=(?!=)", on_event_body)) != 1:
        raise AssertionError(f"{log_var} must not be reassigned")

    guard_re = re.compile(
        rf"\bif\s*\(\s*(?:"
        rf"{re.escape(log_var)}\.GetTotalLines\s*\(\s*\)\s*==\s*{re.escape(count_var)}"
        rf"|{re.escape(count_var)}\s*==\s*{re.escape(log_var)}\.GetTotalLines\s*\(\s*\)"
        rf")\s*\)"
    )
    guard_matches = list(guard_re.finditer(after_super))
    if len(guard_matches) != 1 or after_super[: guard_matches[0].start()].strip():
        raise AssertionError(
            "RecordDrop must be guarded by GetTotalLines() == pre-super count after super.OnEvent"
        )
    guard_body, after_guard = _block_after_strict(after_super, guard_matches[0].end())
    if after_super[after_guard:].strip():
        raise AssertionError("unexpected statements after GetTotalLines() drop guard")
    if guard_body.count("RecordDrop(") != 1:
        raise AssertionError("RecordDrop must execute inside the GetTotalLines() equality guard")
    drop_call_re = re.compile(
        rf"^{re.escape(log_var)}\.RecordDrop\s*\(\s*[A-Za-z_]\w*\s*,\s*{re.escape(alive_var)}\s*\)$"
    )
    guard_stmts = _top_level_simple_statements(guard_body)
    if sum(1 for stmt in guard_stmts if drop_call_re.match(stmt)) != 1:
        raise AssertionError(
            f"RecordDrop must be called unconditionally inside the GetTotalLines() guard with {alive_var}"
        )


def verify_seq_ceiling_guard(source: str) -> None:
    """Verify RecordLine and RecordDrop stop at 2147483647 before incrementing m_LastSeq."""
    cleaned = _blank_strings(_strip_comments(source))
    log_body = _class_body(cleaned, r"^\s*class\s+MCPChatLog\b")

    has_max_seq_const = (
        re.search(r"^\s*const\s+int\s+MAX_SEQ\s*=\s*2147483647\s*;\s*$", log_body, re.M)
        is not None
    )
    ceiling_tokens = r"(?:MAX_SEQ|2147483647)" if has_max_seq_const else r"2147483647"
    guard_re = re.compile(rf"\bif\s*\(\s*m_LastSeq\s*>=\s*{ceiling_tokens}\s*\)")
    inc_re = re.compile(
        r"\bm_LastSeq\s*=(?!=)\s*m_LastSeq\s*\+\s*1\s*;"
        r"|\bm_LastSeq\s*\+\+\s*;"
        r"|\+\+\s*m_LastSeq\s*;"
        r"|\bm_LastSeq\s*\+=\s*1\s*;"
    )
    any_seq_write_re = re.compile(
        r"\bm_LastSeq\s*(?:=(?!=)|\+=|-=|\+\+|--)|(?:\+\+|--)\s*m_LastSeq\b"
    )

    for signature in (
        "void RecordLine(ChatMessageEventParams params)",
        "void RecordDrop(ChatMessageEventParams params, bool wasAlive)",
    ):
        body = _method_body(log_body, signature)
        _assert_all_control_headers_braced(body)
        writes = list(any_seq_write_re.finditer(body))
        incs = list(inc_re.finditer(body))
        if len(writes) != 1 or len(incs) != 1:
            raise AssertionError(f"{signature} must increment m_LastSeq once")
        inc_pos = incs[0].start()

        guards = list(guard_re.finditer(body))
        if len(guards) != 1:
            raise AssertionError(
                f"{signature} must guard m_LastSeq >= 2147483647 before incrementing"
            )
        guard_start = guards[0].start()
        depth = 0
        for ch in body[:guard_start]:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
        if depth != 0:
            raise AssertionError(f"{signature} ceiling guard must be unconditional at top level")
        guard_block, guard_end = _block_after_strict(body, guards[0].end())
        if guard_block.strip() != "return;":
            raise AssertionError(f"{signature} ceiling guard must return immediately")
        if guard_end > inc_pos:
            raise AssertionError(f"{signature} ceiling guard must come before m_LastSeq increment")
        prefix = body[:guard_end]
        if any(tok in prefix for tok in ("m_TotalLines", ".Insert(", ".RemoveOrdered(")):
            raise AssertionError(f"{signature} mutates state before m_LastSeq ceiling guard")


def verify_read_drops_unbounded(source: str) -> None:
    """Verify Read returns every drop with seq > marker without an upper seq bound."""
    cleaned = _blank_strings(_strip_comments(source))
    log_body = _class_body(cleaned, r"^\s*class\s+MCPChatLog\b")
    read_body = _method_body(log_body, "MCPChatRead Read(int marker, int maxLines)")
    _assert_all_control_headers_braced(read_body)

    while_match = re.search(
        r"\bwhile\s*\(\s*\w+\s*<\s*m_Drops\.Count\s*\(\s*\)\s*\)", read_body
    )
    if while_match is None:
        raise AssertionError("Read lacks m_Drops iteration loop")
    loop_body, _ = _block_after_strict(read_body, while_match.end())
    if (
        loop_body.count("outRead.dropped.Insert(") != 1
        or read_body.count("outRead.dropped.Insert(") != 1
    ):
        raise AssertionError("Read must insert into outRead.dropped once inside m_Drops loop")
    if_matches = list(re.finditer(r"\bif\b", loop_body))
    if len(if_matches) != 1:
        raise AssertionError("m_Drops loop must have a single if filter")
    cond, after_cond = _paren_after_strict(loop_body, if_matches[0].end())
    if not re.fullmatch(r"\s*(\w+)\s*&&\s*\1\.seq\s*>\s*marker\s*", cond):
        raise AssertionError(f"m_Drops filter must only require seq > marker, got: {cond!r}")
    if_body, _ = _block_after_strict(loop_body, after_cond)
    if not re.fullmatch(r"\s*outRead\.dropped\.Insert\s*\(\s*\w+\s*\)\s*;\s*", if_body):
        raise AssertionError(f"unexpected statements in m_Drops filter body: {if_body!r}")


def read_chat_log_int_constants(source: str) -> dict[str, int]:
    """Parse const int NAME = VALUE; declarations from class MCPChatLog."""
    cleaned = _blank_strings(_strip_comments(source))
    log_body = _class_body(cleaned, r"^\s*class\s+MCPChatLog\b")
    constants: dict[str, int] = {}
    for match in re.finditer(
        r"^\s*const\s+int\s+([A-Za-z_]\w*)\s*=\s*(-?\d+)\s*;\s*$",
        log_body,
        re.M,
    ):
        name = match.group(1)
        if name in constants:
            raise AssertionError(f"duplicate constant {name} in MCPChatLog")
        constants[name] = int(match.group(2))
    return constants


def verify_payload_byte_budget(source: str) -> dict[str, int]:
    """Verify per-line and total /result byte bounds derived from MCPChatLog's constants."""
    consts = read_chat_log_int_constants(source)
    required = (
        "LINE_CAPACITY",
        "DROP_CAPACITY",
        "LINE_SENDER_MAX_CHARS",
        "LINE_TEXT_MAX_CHARS",
        "DROP_SENDER_MAX_CHARS",
        "DROP_TEXT_MAX_CHARS",
        "LINE_BYTES_MAX",
    )
    for name in required:
        if name not in consts or consts[name] <= 0:
            raise AssertionError(
                f"MCPChatLog must declare positive const int {name}"
            )
    max_line_bytes = (
        6 * (consts["LINE_SENDER_MAX_CHARS"] + consts["LINE_TEXT_MAX_CHARS"]) * 4 + 384
    )
    if max_line_bytes >= consts["LINE_BYTES_MAX"]:
        raise AssertionError(
            f"per-line maximum {max_line_bytes} must stay below "
            f"LINE_BYTES_MAX ({consts['LINE_BYTES_MAX']})"
        )
    max_drops_bytes = consts["DROP_CAPACITY"] * (
        6 * (consts["DROP_SENDER_MAX_CHARS"] + consts["DROP_TEXT_MAX_CHARS"]) * 4 + 192
    )
    if loopback.MAX_BODY_BYTES != 1_048_576:
        raise AssertionError(
            f"expected loopback.MAX_BODY_BYTES == 1048576, got {loopback.MAX_BODY_BYTES}"
        )
    if consts["LINE_BYTES_MAX"] + max_drops_bytes >= 1_048_576:
        raise AssertionError(
            f"LINE_BYTES_MAX + max_drops_bytes "
            f"({consts['LINE_BYTES_MAX'] + max_drops_bytes}) must stay below 1048576"
        )
    return {
        **consts,
        "max_line_bytes": max_line_bytes,
        "max_drops_bytes": max_drops_bytes,
    }


def verify_payload_bound_constants(source: str) -> None:
    """Verify MCPChatLog declares the D13 character-cap, ring-capacity, and line-byte bound constants."""
    consts = read_chat_log_int_constants(source)
    expected_consts = (
        ("LINE_CAPACITY", 256),
        ("DROP_CAPACITY", 64),
        ("LINE_SENDER_MAX_CHARS", LINE_SENDER_MAX_CHARS),
        ("LINE_TEXT_MAX_CHARS", LINE_TEXT_MAX_CHARS),
        ("DROP_SENDER_MAX_CHARS", DROP_SENDER_MAX_CHARS),
        ("DROP_TEXT_MAX_CHARS", DROP_TEXT_MAX_CHARS),
        ("LINE_BYTES_MAX", LINE_BYTES_MAX),
    )
    for name, expected_val in expected_consts:
        if consts.get(name) != expected_val:
            raise AssertionError(
                f"MCPChatLog must declare const int {name} = {expected_val}; "
                f"got {consts.get(name)!r}"
            )
    verify_payload_byte_budget(source)


def verify_record_line_utf8_caps(source: str) -> None:
    """Verify RecordLine caps sender and text with SubstringUtf8 when LengthUtf8 exceeds its cap and sets entry.cut."""
    verify_payload_bound_constants(source)
    cleaned = _blank_strings(_strip_comments(source))
    log_body = _class_body(cleaned, r"^\s*class\s+MCPChatLog\b")
    line_body = _method_body(
        log_body, "void RecordLine(ChatMessageEventParams params)"
    )
    _assert_all_control_headers_braced(line_body)
    if re.search(r"\bSubstring\s*\(|\bLength\s*\(", line_body):
        raise AssertionError(
            "RecordLine must use LengthUtf8/SubstringUtf8 rather than byte Length/Substring"
        )

    null_guards = list(re.finditer(r"\bif\s*\(\s*!params\s*\)", line_body))
    if len(null_guards) != 1:
        raise AssertionError("RecordLine must guard if (!params) once")
    null_block, null_end = _block_after_strict(line_body, null_guards[0].end())
    if null_block.strip() != "return;":
        raise AssertionError("RecordLine if (!params) guard must return immediately")
    if "params." in line_body[:null_end]:
        raise AssertionError("RecordLine must not dereference params before if (!params)")

    line_top_stmts = _top_level_simple_statements(line_body)
    new_entry_matches = list(
        re.finditer(
            r"^\s*entry\s*=\s*new\s+MCPChatLine\s*\(\s*\)\s*;\s*$", line_body, re.M
        )
    )
    insert_matches = list(
        re.finditer(r"^\s*m_Lines\.Insert\s*\(\s*entry\s*\)\s*;\s*$", line_body, re.M)
    )
    if (
        len(new_entry_matches) != 1
        or "entry = new MCPChatLine()" not in line_top_stmts
        or len(insert_matches) != 1
        or "m_Lines.Insert(entry)" not in line_top_stmts
    ):
        raise AssertionError(
            "RecordLine must allocate entry = new MCPChatLine() and m_Lines.Insert(entry) unconditionally"
        )

    cut_init_matches = list(
        re.finditer(r"^\s*cut\s*=\s*false\s*;\s*$", line_body, re.M)
    )
    if len(cut_init_matches) != 1 or "cut = false" not in line_top_stmts:
        raise AssertionError(
            "RecordLine must initialize cut = false unconditionally at top level"
        )
    cut_init_end = cut_init_matches[0].end()

    max_cap_end = cut_init_end
    for param_field, entry_field, cap_const in (
        ("param2", "sender", "LINE_SENDER_MAX_CHARS"),
        ("param3", "text", "LINE_TEXT_MAX_CHARS"),
    ):
        if len(re.findall(rf"\bparams\.{param_field}\b", line_body)) != 1:
            raise AssertionError(
                f"RecordLine must read params.{param_field} once ({cap_const})"
            )
        init_matches = list(
            re.finditer(
                rf"^\s*([A-Za-z_]\w*)\s*=\s*params\.{param_field}\s*;\s*$",
                line_body,
                re.M,
            )
        )
        if len(init_matches) != 1 or init_matches[0].start() < null_end:
            raise AssertionError(
                f"RecordLine must copy params.{param_field} into a local before capping with {cap_const}"
            )
        local_var = init_matches[0].group(1)
        if f"{local_var} = params.{param_field}" not in line_top_stmts:
            raise AssertionError(
                f"{local_var} = params.{param_field} must be unconditional at top level ({cap_const})"
            )
        guard_re = re.compile(
            rf"\bif\s*\(\s*{re.escape(local_var)}\.LengthUtf8\s*\(\s*\)\s*>\s*{cap_const}\s*\)"
        )
        guard_matches = list(guard_re.finditer(line_body))
        if (
            len(guard_matches) != 1
            or guard_matches[0].start() < init_matches[0].end()
            or guard_matches[0].start() < cut_init_end
        ):
            raise AssertionError(
                f"RecordLine must guard {local_var}.LengthUtf8() > {cap_const} after cut = false and reading params.{param_field}"
            )
        depth = 0
        for ch in line_body[: guard_matches[0].start()]:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
        if depth != 0:
            raise AssertionError(
                f"RecordLine {cap_const} guard must execute unconditionally at top level"
            )
        cap_block, cap_end = _block_after_strict(line_body, guard_matches[0].end())
        max_cap_end = max(max_cap_end, cap_end)
        if re.match(r"^\s*else\b", line_body[cap_end:]):
            raise AssertionError(
                f"RecordLine {cap_const} guard must not have an else branch"
            )
        if "{" in cap_block:
            raise AssertionError(
                f"RecordLine {cap_const} guard must not contain nested blocks"
            )
        cap_stmts = _top_level_simple_statements(cap_block)
        expected_stmts = [
            f"{local_var} = {local_var}.SubstringUtf8(0, {cap_const})",
            "cut = true",
        ]
        if cap_stmts != expected_stmts:
            raise AssertionError(
                f"RecordLine must cut {local_var} with SubstringUtf8(0, {cap_const}) and set cut = true inside the LengthUtf8() > {cap_const} guard"
            )
        if len(re.findall(rf"(?<!\.)\b{re.escape(local_var)}\s*=(?!=)", line_body)) != 2:
            raise AssertionError(
                f"{local_var} must only be assigned from params.{param_field} and SubstringUtf8 ({cap_const})"
            )
        assign_re = re.compile(
            rf"^\s*entry\.{entry_field}\s*=\s*{re.escape(local_var)}\s*;\s*$",
            re.M,
        )
        assign_matches = list(assign_re.finditer(line_body))
        if (
            len(assign_matches) != 1
            or assign_matches[0].start() < cap_end
            or assign_matches[0].start() < new_entry_matches[0].end()
            or assign_matches[0].start() > insert_matches[0].start()
        ):
            raise AssertionError(
                f"RecordLine must assign entry.{entry_field} = {local_var} after {cap_const} guard and before Insert"
            )
        if f"entry.{entry_field} = {local_var}" not in line_top_stmts:
            raise AssertionError(
                f"entry.{entry_field} = {local_var} must be unconditional at top level ({cap_const})"
            )
        if len(re.findall(rf"\bentry\.{entry_field}\s*=(?!=)", line_body)) != 1:
            raise AssertionError(
                f"entry.{entry_field} must be assigned once in RecordLine ({cap_const})"
            )

    if len(re.findall(r"(?<!\.)\bcut\s*=(?!=)", line_body)) != 3:
        raise AssertionError(
            "RecordLine must assign cut only at cut = false and cut = true in the two cap guards"
        )
    cut_assign_matches = list(
        re.finditer(r"^\s*entry\.cut\s*=\s*cut\s*;\s*$", line_body, re.M)
    )
    if (
        len(cut_assign_matches) != 1
        or "entry.cut = cut" not in line_top_stmts
        or cut_assign_matches[0].start() < max_cap_end
        or cut_assign_matches[0].start() < new_entry_matches[0].end()
        or cut_assign_matches[0].start() > insert_matches[0].start()
        or len(re.findall(r"\bentry\.cut\s*=(?!=)", line_body)) != 1
    ):
        raise AssertionError(
            "RecordLine must assign entry.cut = cut unconditionally once after both cap guards and before Insert"
        )


def verify_record_drop_utf8_caps(source: str) -> None:
    """Verify RecordDrop caps sender and text with SubstringUtf8 when LengthUtf8 exceeds its cap and sets entry.cut."""
    verify_payload_bound_constants(source)
    cleaned = _blank_strings(_strip_comments(source))
    log_body = _class_body(cleaned, r"^\s*class\s+MCPChatLog\b")

    drop_body = _method_body(
        log_body, "void RecordDrop(ChatMessageEventParams params, bool wasAlive)"
    )
    _assert_all_control_headers_braced(drop_body)
    if re.search(r"\bSubstring\s*\(|\bLength\s*\(", drop_body):
        raise AssertionError(
            "RecordDrop must use LengthUtf8/SubstringUtf8 rather than byte Length/Substring"
        )

    drop_top_stmts = _top_level_simple_statements(drop_body)
    new_entry_matches = list(
        re.finditer(
            r"^\s*entry\s*=\s*new\s+MCPChatDrop\s*\(\s*\)\s*;\s*$", drop_body, re.M
        )
    )
    insert_matches = list(
        re.finditer(r"^\s*m_Drops\.Insert\s*\(\s*entry\s*\)\s*;\s*$", drop_body, re.M)
    )
    if (
        len(new_entry_matches) != 1
        or "entry = new MCPChatDrop()" not in drop_top_stmts
        or len(insert_matches) != 1
        or "m_Drops.Insert(entry)" not in drop_top_stmts
    ):
        raise AssertionError(
            "RecordDrop must allocate entry = new MCPChatDrop() and m_Drops.Insert(entry) unconditionally"
        )

    cut_init_matches = list(
        re.finditer(r"^\s*cut\s*=\s*false\s*;\s*$", drop_body, re.M)
    )
    if len(cut_init_matches) != 1 or "cut = false" not in drop_top_stmts:
        raise AssertionError(
            "RecordDrop must initialize cut = false unconditionally at top level"
        )

    params_ifs = list(re.finditer(r"\bif\s*\(\s*params\s*\)", drop_body))
    if len(params_ifs) != 1:
        raise AssertionError("RecordDrop must have a single if (params) block")
    if cut_init_matches[0].end() > params_ifs[0].start():
        raise AssertionError("RecordDrop must initialize cut = false before if (params)")
    params_block, params_end = _block_after_strict(drop_body, params_ifs[0].end())
    if re.match(r"^\s*else\b", drop_body[params_end:]):
        raise AssertionError("RecordDrop if (params) block must not have an else branch")
    params_top_stmts = _top_level_simple_statements(params_block)

    for param_field, entry_field, cap_const in (
        ("param2", "sender", "DROP_SENDER_MAX_CHARS"),
        ("param3", "text", "DROP_TEXT_MAX_CHARS"),
    ):
        if len(re.findall(rf"\bparams\.{param_field}\b", drop_body)) != 1:
            raise AssertionError(f"RecordDrop must read params.{param_field} once")
        init_matches = list(
            re.finditer(
                rf"^\s*([A-Za-z_]\w*)\s*=\s*params\.{param_field}\s*;\s*$",
                params_block,
                re.M,
            )
        )
        if len(init_matches) != 1:
            raise AssertionError(
                f"RecordDrop must copy params.{param_field} into a local before capping"
            )
        local_var = init_matches[0].group(1)
        if f"{local_var} = params.{param_field}" not in params_top_stmts:
            raise AssertionError(
                f"{local_var} = params.{param_field} must be unconditional inside if (params)"
            )
        guard_re = re.compile(
            rf"\bif\s*\(\s*{re.escape(local_var)}\.LengthUtf8\s*\(\s*\)\s*>\s*{cap_const}\s*\)"
        )
        guard_matches = list(guard_re.finditer(params_block))
        if len(guard_matches) != 1 or guard_matches[0].start() < init_matches[0].end():
            raise AssertionError(
                f"RecordDrop must guard {local_var}.LengthUtf8() > {cap_const} after reading params.{param_field}"
            )
        depth = 0
        for ch in params_block[: guard_matches[0].start()]:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
        if depth != 0:
            raise AssertionError(
                f"RecordDrop {cap_const} guard must execute unconditionally inside if (params)"
            )
        cap_block, cap_end = _block_after_strict(params_block, guard_matches[0].end())
        if re.match(r"^\s*else\b", params_block[cap_end:]):
            raise AssertionError(
                f"RecordDrop {cap_const} guard must not have an else branch"
            )
        if "{" in cap_block:
            raise AssertionError(
                f"RecordDrop {cap_const} guard must not contain nested blocks"
            )
        cap_stmts = _top_level_simple_statements(cap_block)
        expected_stmts = [
            f"{local_var} = {local_var}.SubstringUtf8(0, {cap_const})",
            "cut = true",
        ]
        if cap_stmts != expected_stmts:
            raise AssertionError(
                f"RecordDrop must cut {local_var} with SubstringUtf8(0, {cap_const}) and set cut = true inside the LengthUtf8() > {cap_const} guard"
            )
        if len(re.findall(rf"(?<!\.)\b{re.escape(local_var)}\s*=(?!=)", drop_body)) != 2:
            raise AssertionError(
                f"{local_var} must only be assigned from params.{param_field} and SubstringUtf8"
            )
        assign_re = re.compile(
            rf"^\s*entry\.{entry_field}\s*=\s*{re.escape(local_var)}\s*;\s*$",
            re.M,
        )
        assign_matches = list(assign_re.finditer(params_block))
        if len(assign_matches) != 1 or assign_matches[0].start() < cap_end:
            raise AssertionError(
                f"RecordDrop must assign entry.{entry_field} = {local_var} after the {cap_const} guard"
            )
        if f"entry.{entry_field} = {local_var}" not in params_top_stmts:
            raise AssertionError(
                f"entry.{entry_field} = {local_var} must be unconditional inside if (params)"
            )
        if (
            len(re.findall(rf"\bentry\.{entry_field}\s*=(?!=)", params_block)) != 1
            or len(re.findall(rf"\bentry\.{entry_field}\s*=(?!=)", drop_body)) != 2
        ):
            raise AssertionError(
                f"entry.{entry_field} must only be defaulted and assigned once inside if (params)"
            )

    if len(re.findall(r"(?<!\.)\bcut\s*=(?!=)", drop_body)) != 3:
        raise AssertionError(
            "RecordDrop must assign cut only at cut = false and cut = true in the two cap guards"
        )
    cut_assign_matches = list(
        re.finditer(r"^\s*entry\.cut\s*=\s*cut\s*;\s*$", drop_body, re.M)
    )
    if (
        len(cut_assign_matches) != 1
        or "entry.cut = cut" not in drop_top_stmts
        or cut_assign_matches[0].start() < params_end
        or cut_assign_matches[0].start() < new_entry_matches[0].end()
        or cut_assign_matches[0].start() > insert_matches[0].start()
        or len(re.findall(r"\bentry\.cut\s*=(?!=)", drop_body)) != 1
    ):
        raise AssertionError(
            "RecordDrop must assign entry.cut = cut unconditionally once after if (params) and before Insert"
        )


def _parse_plain_class_fields(class_body: str, class_name: str) -> list[tuple[str, str]]:
    if "{" in class_body or "}" in class_body or "(" in class_body:
        raise AssertionError(f"{class_name} must contain plain field declarations only")
    fields: list[tuple[str, str]] = []
    for stmt in _top_level_simple_statements(class_body):
        m = re.fullmatch(r"([A-Za-z_]\w*)\s+([A-Za-z_]\w*)", stmt)
        if m is None:
            raise AssertionError(
                f"unexpected member declaration in {class_name}: {stmt!r}"
            )
        fields.append((m.group(1), m.group(2)))
    return fields


def verify_chat_message_classes(messages_source: str) -> None:
    """Verify MCPChatLine and MCPChatDrop declare bool cut in the wire order required by D13."""
    cleaned = _blank_strings(_strip_comments(messages_source))
    line_body = _class_body(cleaned, r"^\s*class\s+MCPChatLine\b")
    drop_body = _class_body(cleaned, r"^\s*class\s+MCPChatDrop\b")

    line_fields = _parse_plain_class_fields(line_body, "MCPChatLine")
    expected_line = [
        ("int", "seq"),
        ("int", "t_client_ms"),
        ("int", "channel"),
        ("string", "sender"),
        ("string", "text"),
        ("bool", "visible_hierarchy"),
        ("float", "alpha"),
        ("float", "screen_x"),
        ("float", "screen_y"),
        ("float", "screen_w"),
        ("float", "screen_h"),
        ("bool", "replaced"),
        ("int", "shown_at_ms"),
        ("bool", "cut"),
    ]
    if line_fields != expected_line:
        raise AssertionError(
            f"MCPChatLine fields must be {expected_line}, got {line_fields}"
        )

    drop_fields = _parse_plain_class_fields(drop_body, "MCPChatDrop")
    expected_drop = [
        ("int", "seq"),
        ("int", "t_client_ms"),
        ("int", "channel"),
        ("string", "sender"),
        ("string", "text"),
        ("bool", "cut"),
        ("string", "reason"),
    ]
    if drop_fields != expected_drop:
        raise AssertionError(
            f"MCPChatDrop fields must be {expected_drop}, got {drop_fields}"
        )


def verify_monotonic_total_lines(source: str) -> None:
    """Verify GetTotalLines() returns monotonic m_TotalLines, initialized to 0 and only incremented in RecordLine."""
    cleaned = _blank_strings(_strip_comments(source))
    log_body = _class_body(cleaned, r"^\s*class\s+MCPChatLog\b")

    if not re.search(r"^\s*protected\s+int\s+m_TotalLines\s*;\s*$", log_body, re.M):
        raise AssertionError("MCPChatLog must declare protected int m_TotalLines;")

    ctor_body = _method_body(log_body, "void MCPChatLog()")
    _assert_all_control_headers_braced(ctor_body)
    ctor_stmts = _top_level_simple_statements(ctor_body)
    if ctor_stmts.count("m_TotalLines = 0") != 1:
        raise AssertionError("MCPChatLog() must initialize m_TotalLines = 0 unconditionally")

    getter_body = _method_body(log_body, "int GetTotalLines()")
    _assert_all_control_headers_braced(getter_body)
    if "{" in getter_body or _top_level_simple_statements(getter_body) != [
        "return m_TotalLines"
    ]:
        raise AssertionError(
            "GetTotalLines() must unconditionally return m_TotalLines"
        )

    line_body = _method_body(
        log_body, "void RecordLine(ChatMessageEventParams params)"
    )
    _assert_all_control_headers_braced(line_body)
    line_stmts = _top_level_simple_statements(line_body)
    inc_count = sum(
        1
        for stmt in line_stmts
        if re.fullmatch(
            r"m_TotalLines\s*=\s*m_TotalLines\s*\+\s*1"
            r"|m_TotalLines\s*\+\+"
            r"|\+\+\s*m_TotalLines"
            r"|m_TotalLines\s*\+=\s*1",
            stmt,
        )
    )
    if inc_count != 1:
        raise AssertionError(
            "RecordLine must increment m_TotalLines unconditionally once at top level"
        )

    all_writes = list(
        re.finditer(
            r"\bm_TotalLines\s*(?:=(?!=)|\+=|-=|\*=|/=|\+\+|--)|(?:\+\+|--)\s*m_TotalLines\b",
            log_body,
        )
    )
    if len(all_writes) != 2:
        raise AssertionError(
            f"m_TotalLines must only be written in MCPChatLog() and RecordLine, found {len(all_writes)} writes"
        )


def verify_ordered_ring_evictions(source: str) -> None:
    """Verify both m_Lines and m_Drops ring evictions use RemoveOrdered(0) and never unordered Remove."""
    cleaned = _blank_strings(_strip_comments(source))
    log_body = _class_body(cleaned, r"^\s*class\s+MCPChatLog\b")

    if re.search(r"\.\s*Remove\s*\(", log_body):
        raise AssertionError(
            "MCPChatLog must use RemoveOrdered(0) rather than unordered Remove(...)"
        )

    for signature, ring, cap_const in (
        ("void RecordLine(ChatMessageEventParams params)", "m_Lines", "LINE_CAPACITY"),
        (
            "void RecordDrop(ChatMessageEventParams params, bool wasAlive)",
            "m_Drops",
            "DROP_CAPACITY",
        ),
    ):
        body = _method_body(log_body, signature)
        _assert_all_control_headers_braced(body)

        if len(re.findall(rf"\b{ring}\.RemoveOrdered\s*\(", log_body)) != 1:
            raise AssertionError(
                f"{ring}.RemoveOrdered must be called once in MCPChatLog"
            )

        guard_re = re.compile(
            rf"\bif\s*\(\s*{ring}\.Count\s*\(\s*\)\s*>=\s*{cap_const}\s*\)"
        )
        guards = list(guard_re.finditer(body))
        if len(guards) != 1:
            raise AssertionError(
                f"{signature} must guard {ring}.Count() >= {cap_const} once"
            )
        depth = 0
        for ch in body[: guards[0].start()]:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
        if depth != 0:
            raise AssertionError(
                f"{signature} {cap_const} eviction guard must be at top level"
            )
        evict_block, evict_end = _block_after_strict(body, guards[0].end())
        if re.match(r"^\s*else\b", body[evict_end:]):
            raise AssertionError(
                f"{signature} {cap_const} eviction guard must not have an else branch"
            )

        get_match = re.search(rf"\b{ring}\.Get\s*\(\s*0\s*\)", evict_block)
        remove_matches = list(
            re.finditer(
                rf"^\s*{ring}\.RemoveOrdered\s*\(\s*0\s*\)\s*;\s*$",
                evict_block,
                re.M,
            )
        )
        evict_stmts = _top_level_simple_statements(evict_block)
        if (
            get_match is None
            or len(remove_matches) != 1
            or f"{ring}.RemoveOrdered(0)" not in evict_stmts
            or remove_matches[0].start() < get_match.end()
        ):
            raise AssertionError(
                f"{signature} must inspect {ring}.Get(0) and call {ring}.RemoveOrdered(0) unconditionally when full"
            )

        insert_matches = list(
            re.finditer(rf"^\s*{ring}\.Insert\s*\(\s*entry\s*\)\s*;\s*$", body, re.M)
        )
        if len(insert_matches) != 1 or insert_matches[0].start() < evict_end:
            raise AssertionError(
                f"{signature} must call {ring}.Insert(entry) after the {cap_const} eviction guard"
            )


def verify_read_line_byte_guard(source: str) -> None:
    """Verify Read stops adding lines past LINE_BYTES_MAX like maxLines while always returning the first eligible line."""
    verify_payload_bound_constants(source)
    cleaned = _blank_strings(_strip_comments(source))
    log_body = _class_body(cleaned, r"^\s*class\s+MCPChatLog\b")
    read_body = _method_body(log_body, "MCPChatRead Read(int marker, int maxLines)")
    _assert_all_control_headers_braced(read_body)

    while_match = re.search(
        r"\bwhile\s*\(\s*(\w+)\s*<\s*m_Lines\.Count\s*\(\s*\)\s*\)", read_body
    )
    if while_match is None:
        raise AssertionError("Read lacks m_Lines iteration loop")
    before_loop = read_body[: while_match.start()]
    loop_body, after_loop_idx = _block_after_strict(read_body, while_match.end())
    after_loop = read_body[after_loop_idx:]

    if not re.search(r"^\s*linesCut\s*=\s*false\s*;\s*$", before_loop, re.M):
        raise AssertionError("Read must initialize linesCut = false before m_Lines loop")
    bytes_init = list(
        re.finditer(r"^\s*([A-Za-z_]\w*)\s*=\s*0\s*;\s*$", before_loop, re.M)
    )
    init_vars = {m.group(1) for m in bytes_init}

    elig_if = re.search(
        r"\bif\s*\(\s*(\w+)\s*&&\s*\1\.seq\s*>\s*marker\s*\)", loop_body
    )
    if elig_if is None:
        raise AssertionError("m_Lines loop lacks eligible seq > marker filter")
    item_var = elig_if.group(1)
    elig_body, _ = _block_after_strict(loop_body, elig_if.end())

    max_if = re.search(
        r"\bif\s*\(\s*outRead\.lines\.Count\s*\(\s*\)\s*>=\s*maxLines\s*\)",
        elig_body,
    )
    if max_if is None:
        raise AssertionError("Read lacks outRead.lines.Count() >= maxLines cut guard")
    max_cut_body, max_cut_end = _block_after_strict(elig_body, max_if.end())
    if _top_level_simple_statements(max_cut_body) != ["linesCut = true", "break"]:
        raise AssertionError("maxLines cut guard must set linesCut = true and break")

    est_re = re.compile(
        rf"^\s*([A-Za-z_]\w*)\s*=\s*6\s*\*\s*\(\s*"
        rf"{re.escape(item_var)}\.sender\.Length\s*\(\s*\)\s*\+\s*"
        rf"{re.escape(item_var)}\.text\.Length\s*\(\s*\)\s*\)\s*\+\s*384\s*;\s*$",
        re.M,
    )
    est_matches = list(est_re.finditer(elig_body))
    if len(est_matches) != 1:
        raise AssertionError(
            "Read must compute per-line byte estimate 6 * (sender.Length() + text.Length()) + 384"
        )
    item_bytes_var = est_matches[0].group(1)

    byte_guard_re = re.compile(
        r"\bif\s*\(\s*outRead\.lines\.Count\s*\(\s*\)\s*>\s*0\s*&&\s*"
        rf"([A-Za-z_]\w*)\s*\+\s*{re.escape(item_bytes_var)}\s*>\s*LINE_BYTES_MAX\s*\)"
    )
    byte_guards = list(byte_guard_re.finditer(elig_body))
    if len(byte_guards) != 1:
        raise AssertionError(
            "Read must guard outRead.lines.Count() > 0 && lineBytes + itemBytes > LINE_BYTES_MAX so the first eligible line is always returned"
        )
    line_bytes_var = byte_guards[0].group(1)
    if line_bytes_var not in init_vars:
        raise AssertionError(
            f"Read must initialize {line_bytes_var} = 0 before m_Lines loop"
        )
    if byte_guards[0].start() < est_matches[0].end():
        raise AssertionError("Read must compute itemBytes before the LINE_BYTES_MAX guard")
    byte_cut_body, byte_cut_end = _block_after_strict(elig_body, byte_guards[0].end())
    if _top_level_simple_statements(byte_cut_body) != ["linesCut = true", "break"]:
        raise AssertionError(
            "LINE_BYTES_MAX guard must set linesCut = true and break like a maxLines cut"
        )

    insert_match = re.search(
        rf"^\s*outRead\.lines\.Insert\s*\(\s*{re.escape(item_var)}\s*\)\s*;\s*$",
        elig_body,
        re.M,
    )
    if (
        insert_match is None
        or insert_match.start() < max_cut_end
        or insert_match.start() < byte_cut_end
    ):
        raise AssertionError(
            "Read must insert eligible line only after both maxLines and LINE_BYTES_MAX guards"
        )

    elig_top_stmts = _top_level_simple_statements(elig_body)
    accum_stmt = f"{line_bytes_var} = {line_bytes_var} + {item_bytes_var}"
    seq_stmt = f"lastLineSeq = {item_var}.seq"
    if (
        f"outRead.lines.Insert({item_var})" not in elig_top_stmts
        or accum_stmt not in elig_top_stmts
        or seq_stmt not in elig_top_stmts
    ):
        raise AssertionError(
            "Read must insert lineItem, accumulate lineBytes + itemBytes, and update lastLineSeq unconditionally after the cut guards"
        )
    if len(re.findall(rf"(?<!\.)\b{re.escape(line_bytes_var)}\s*=(?!=)", read_body)) != 2:
        raise AssertionError(
            f"{line_bytes_var} must only be initialized to 0 and accumulated per inserted line"
        )

    marker_if = re.search(r"\bif\s*\(\s*linesCut\s*\)", after_loop)
    if marker_if is None:
        raise AssertionError("Read must set outRead.marker from lastLineSeq when linesCut")
    cut_marker_body, after_cut_marker = _block_after_strict(after_loop, marker_if.end())
    if _top_level_simple_statements(cut_marker_body) != ["outRead.marker = lastLineSeq"]:
        raise AssertionError("linesCut branch must set outRead.marker = lastLineSeq")
    else_tail = after_loop[after_cut_marker:].lstrip()
    else_m = re.match(r"^else\b", else_tail)
    if else_m is None:
        raise AssertionError("Read must set outRead.marker = m_LastSeq when not linesCut")
    uncut_marker_body, _ = _block_after_strict(else_tail, else_m.end())
    if _top_level_simple_statements(uncut_marker_body) != ["outRead.marker = m_LastSeq"]:
        raise AssertionError("else branch must set outRead.marker = m_LastSeq")


def _tool_payload(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, tuple) and len(raw) == 2 and isinstance(raw[1], dict):
        return raw[1]
    blocks = raw[0] if isinstance(raw, tuple) else raw
    assert isinstance(blocks, list) and blocks, f"unexpected call_tool output: {raw!r}"
    return json.loads(blocks[0].text)


def _tool_error_text(exc: BaseException) -> str:
    message = str(exc)
    prefix = f"Error executing tool {COMMAND}: "
    return message[len(prefix) :] if message.startswith(prefix) else message


class ChatReadEnforceContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.chat_log_src = CHAT_LOG_PATH.read_text(encoding="utf-8")
        self.mission_src = MISSION_GAMEPLAY_PATH.read_text(encoding="utf-8")
        self.bridge_src = CLIENT_BRIDGE_PATH.read_text(encoding="utf-8")
        self.messages_src = MESSAGES_PATH.read_text(encoding="utf-8")

    def _assert_executable_mutant(self, original: str, mutant: str) -> None:
        self.assertNotEqual(mutant, original)
        self.assertNotEqual(
            _strip_comments(mutant),
            _strip_comments(original),
            "mutant did not change executable code after stripping comments",
        )

    def _mutate_executable(
        self, original: str, target: str, replacement: str
    ) -> str:
        mutant = original.replace(target, replacement, 1)
        self._assert_executable_mutant(original, mutant)
        self.assertEqual(
            _strip_comments(original).count(target),
            1,
            f"expected executable target once outside comments: {target!r}",
        )
        return mutant

    def test_chatline_set_calls_super_before_recording_positive_and_negative(self) -> None:
        verify_chatline_set_override(self.chat_log_src)

        swapped = self._mutate_executable(
            self.chat_log_src,
            "\t\tsuper.Set(params);\n\t\tm_MCPShownSeq = 0;\n\t\tchatLog = MCPChatLog.Get();\n"
            "\t\tif (chatLog)\n\t\t{\n\t\t\tlinesBefore = chatLog.GetTotalLines();\n"
            "\t\t\tchatLog.RecordLine(params);\n"
            "\t\t\tif (chatLog.GetTotalLines() != linesBefore)\n"
            "\t\t\t{\n\t\t\t\tm_MCPShownSeq = chatLog.GetLastSeq();\n\t\t\t}\n\t\t}",
            "\t\tm_MCPShownSeq = 0;\n\t\tchatLog = MCPChatLog.Get();\n"
            "\t\tif (chatLog)\n\t\t{\n\t\t\tlinesBefore = chatLog.GetTotalLines();\n"
            "\t\t\tchatLog.RecordLine(params);\n"
            "\t\t\tif (chatLog.GetTotalLines() != linesBefore)\n"
            "\t\t\t{\n\t\t\t\tm_MCPShownSeq = chatLog.GetLastSeq();\n\t\t\t}\n\t\t}\n"
            "\t\tsuper.Set(params);",
        )
        with self.assertRaisesRegex(AssertionError, "before super.Set"):
            verify_chatline_set_override(swapped)

        missing_super = self._mutate_executable(
            self.chat_log_src,
            "\t\tsuper.Set(params);\n",
            "",
        )
        with self.assertRaisesRegex(AssertionError, "super.Set"):
            verify_chatline_set_override(missing_super)

        # Negative controls (D8): conditional super.Set(params), braced and brace-less.
        braced_if_super = self._mutate_executable(
            self.chat_log_src,
            "\t\tsuper.Set(params);",
            "\t\tif (params.param1 == 0)\n\t\t{\n\t\t\tsuper.Set(params);\n\t\t}",
        )
        verify_enforce_hygiene(braced_if_super, CHAT_LOG_PATH.name)
        with self.assertRaisesRegex(AssertionError, "unconditionally"):
            verify_chatline_set_override(braced_if_super)

        braceless_if_super = self._mutate_executable(
            self.chat_log_src,
            "\t\tsuper.Set(params);",
            "\t\tif (params.param1 == 0) super.Set(params);",
        )
        with self.assertRaisesRegex(AssertionError, "brace-less"):
            verify_chatline_set_override(braceless_if_super)

        for braceless_stmt in (
            "if (params.param1 == 0)\n\t\t{\n\t\t}\n\t\telse super.Set(params);",
            "while (params != null) super.Set(params);",
            "for (;;) super.Set(params);",
            "do super.Set(params); while (false);",
        ):
            with self.subTest(braceless_stmt=braceless_stmt):
                braceless_other = self._mutate_executable(
                    self.chat_log_src,
                    "\t\tsuper.Set(params);",
                    f"\t\t{braceless_stmt}",
                )
                with self.assertRaisesRegex(AssertionError, "brace-less"):
                    verify_chatline_set_override(braceless_other)

        bare_block_super = self._mutate_executable(
            self.chat_log_src,
            "\t\tsuper.Set(params);",
            "\t\t{\n\t\t\tsuper.Set(params);\n\t\t}",
        )
        with self.assertRaisesRegex(AssertionError, "unconditionally"):
            verify_chatline_set_override(bare_block_super)

    def test_mission_on_event_calls_super_on_all_paths_and_scopes_drops(self) -> None:
        verify_mission_on_event(self.mission_src)

        # D7 meta-control: a mutation that only hits a comment fails loudly.
        with self.assertRaisesRegex(AssertionError, "stripping comments"):
            self._mutate_executable(
                self.mission_src,
                "m_LifeState == EPlayerStates.ALIVE",
                "m_LifeState != EPlayerStates.ALIVE",
            )

        # Negative control: missing super.OnEvent on else path.
        no_else_super = self._mutate_executable(
            self.mission_src,
            "\t\telse\n\t\t{\n\t\t\tsuper.OnEvent(eventTypeId, params);\n\t\t}",
            "\t\telse\n\t\t{\n\t\t}",
        )
        with self.assertRaisesRegex(AssertionError, "else_branch"):
            verify_mission_on_event(no_else_super)

        # Negative control: RecordDrop called on non-chat branch.
        drop_outside = self._mutate_executable(
            self.mission_src,
            "\t\telse\n\t\t{\n\t\t\tsuper.OnEvent(eventTypeId, params);\n\t\t}",
            "\t\telse\n\t\t{\n\t\t\tsuper.OnEvent(eventTypeId, params);\n\t\t\tchatLog.RecordDrop(chatParams, false);\n\t\t}",
        )
        with self.assertRaisesRegex(AssertionError, "RecordDrop"):
            verify_mission_on_event(drop_outside)

        # Negative controls (D6a): brace-less if/else/while/for around super.OnEvent.
        for braceless_prefix in (
            "if (wasAlive)",
            "while (wasAlive)",
            "for (linesBefore = 0; linesBefore < 1; linesBefore++)",
        ):
            with self.subTest(braceless=braceless_prefix):
                braceless = self._mutate_executable(
                    self.mission_src,
                    "\t\t\twasAlive = (m_LifeState == EPlayerStates.ALIVE);\n"
                    "\t\t\tsuper.OnEvent(eventTypeId, params);",
                    "\t\t\twasAlive = (m_LifeState == EPlayerStates.ALIVE);\n"
                    f"\t\t\t{braceless_prefix} super.OnEvent(eventTypeId, params);",
                )
                with self.assertRaisesRegex(AssertionError, "brace-less"):
                    verify_mission_on_event(braceless)

        braceless_else = self._mutate_executable(
            self.mission_src,
            "\t\telse\n\t\t{\n\t\t\tsuper.OnEvent(eventTypeId, params);\n\t\t}",
            "\t\telse super.OnEvent(eventTypeId, params);",
        )
        with self.assertRaisesRegex(AssertionError, "brace-less"):
            verify_mission_on_event(braceless_else)

        # Negative controls (D6b, D7): inverted or altered executable alive statement.
        inverted_alive = self._mutate_executable(
            self.mission_src,
            "wasAlive = (m_LifeState == EPlayerStates.ALIVE);",
            "wasAlive = (m_LifeState != EPlayerStates.ALIVE);",
        )
        with self.assertRaisesRegex(AssertionError, "EPlayerStates.ALIVE"):
            verify_mission_on_event(inverted_alive)

        negated_arg = self._mutate_executable(
            self.mission_src,
            "chatLog.RecordDrop(chatParams, wasAlive);",
            "chatLog.RecordDrop(chatParams, !wasAlive);",
        )
        with self.assertRaisesRegex(AssertionError, "RecordDrop"):
            verify_mission_on_event(negated_arg)

        # Negative controls (D6c): RecordDrop outside GetTotalLines() == linesBefore guard.
        unconditional_drop = self._mutate_executable(
            self.mission_src,
            "\t\t\tif (chatLog.GetTotalLines() == linesBefore)\n\t\t\t{\n"
            "\t\t\t\tchatParams = ChatMessageEventParams.Cast(params);\n"
            "\t\t\t\tchatLog.RecordDrop(chatParams, wasAlive);\n\t\t\t}",
            "\t\t\tchatParams = ChatMessageEventParams.Cast(params);\n"
            "\t\t\tchatLog.RecordDrop(chatParams, wasAlive);",
        )
        with self.assertRaisesRegex(AssertionError, "GetTotalLines"):
            verify_mission_on_event(unconditional_drop)

        inverted_line_guard = self._mutate_executable(
            self.mission_src,
            "if (chatLog.GetTotalLines() == linesBefore)",
            "if (chatLog.GetTotalLines() != linesBefore)",
        )
        with self.assertRaisesRegex(AssertionError, "GetTotalLines"):
            verify_mission_on_event(inverted_line_guard)

    def test_seq_ceiling_and_unbounded_drop_read_contract(self) -> None:
        verify_seq_ceiling_guard(self.chat_log_src)
        verify_read_drops_unbounded(self.chat_log_src)

        # Negative control (D5): guard placed after increment in RecordLine.
        late_line_guard = self._mutate_executable(
            self.chat_log_src,
            "\t\tif (m_LastSeq >= MAX_SEQ)\n\t\t{\n\t\t\treturn;\n\t\t}\n"
            "\t\tnowMs = 0;\n\t\tif (GetGame())\n\t\t{\n\t\t\tnowMs = GetGame().GetTime();\n\t\t}\n"
            "\t\tm_LastSeq = m_LastSeq + 1;\n"
            "\t\tm_TotalLines = m_TotalLines + 1;",
            "\t\tnowMs = 0;\n\t\tif (GetGame())\n\t\t{\n\t\t\tnowMs = GetGame().GetTime();\n\t\t}\n"
            "\t\tm_LastSeq = m_LastSeq + 1;\n"
            "\t\tif (m_LastSeq >= MAX_SEQ)\n\t\t{\n\t\t\treturn;\n\t\t}\n"
            "\t\tm_TotalLines = m_TotalLines + 1;",
        )
        with self.assertRaisesRegex(AssertionError, "RecordLine.*before m_LastSeq increment"):
            verify_seq_ceiling_guard(late_line_guard)

        # Negative control (D5): guard placed after increment in RecordDrop.
        late_drop_guard = self._mutate_executable(
            self.chat_log_src,
            "\t\tstring sender;\n\t\tstring text;\n\t\tbool cut;\n"
            "\t\tif (m_LastSeq >= MAX_SEQ)\n\t\t{\n\t\t\treturn;\n\t\t}\n"
            "\t\tnowMs = 0;\n\t\tif (GetGame())\n\t\t{\n\t\t\tnowMs = GetGame().GetTime();\n\t\t}\n"
            "\t\tm_LastSeq = m_LastSeq + 1;",
            "\t\tstring sender;\n\t\tstring text;\n\t\tbool cut;\n"
            "\t\tnowMs = 0;\n\t\tif (GetGame())\n\t\t{\n\t\t\tnowMs = GetGame().GetTime();\n\t\t}\n"
            "\t\tm_LastSeq = m_LastSeq + 1;\n"
            "\t\tif (m_LastSeq >= MAX_SEQ)\n\t\t{\n\t\t\treturn;\n\t\t}",
        )
        with self.assertRaisesRegex(AssertionError, "RecordDrop.*before m_LastSeq increment"):
            verify_seq_ceiling_guard(late_drop_guard)

        # Negative control (D3): re-adding an upper seq bound to dropped in Read.
        bounded_drops = self._mutate_executable(
            self.chat_log_src,
            "if (dropItem && dropItem.seq > marker)",
            "if (dropItem && dropItem.seq > marker && dropItem.seq <= outRead.marker)",
        )
        with self.assertRaisesRegex(AssertionError, "seq > marker"):
            verify_read_drops_unbounded(bounded_drops)

    def test_payload_byte_bounds_drop_utf8_caps_and_line_byte_guard(self) -> None:
        verify_payload_bound_constants(self.chat_log_src)
        verify_record_line_utf8_caps(self.chat_log_src)
        verify_record_drop_utf8_caps(self.chat_log_src)
        verify_read_line_byte_guard(self.chat_log_src)

        # Arithmetic derived from the constants read from MCPChatLog.c (D13e).
        parsed_consts = read_chat_log_int_constants(self.chat_log_src)
        budget = verify_payload_byte_budget(self.chat_log_src)
        self.assertEqual(parsed_consts["DROP_CAPACITY"], 64)
        max_line_bytes = (
            6
            * (
                parsed_consts["LINE_SENDER_MAX_CHARS"]
                + parsed_consts["LINE_TEXT_MAX_CHARS"]
            )
            * 4
            + 384
        )
        max_drops_bytes = parsed_consts["DROP_CAPACITY"] * (
            6
            * (
                parsed_consts["DROP_SENDER_MAX_CHARS"]
                + parsed_consts["DROP_TEXT_MAX_CHARS"]
            )
            * 4
            + 192
        )
        self.assertEqual(max_line_bytes, 418_176)
        self.assertEqual(budget["max_line_bytes"], max_line_bytes)
        self.assertLess(max_line_bytes, parsed_consts["LINE_BYTES_MAX"])
        self.assertEqual(max_drops_bytes, 503_808)
        self.assertEqual(budget["max_drops_bytes"], max_drops_bytes)
        self.assertEqual(parsed_consts["LINE_BYTES_MAX"] + max_drops_bytes, 995_328)
        self.assertEqual(loopback.MAX_BODY_BYTES, 1_048_576)
        self.assertLess(
            parsed_consts["LINE_BYTES_MAX"] + max_drops_bytes,
            1_048_576,
        )

        # Negative controls (D13e): per-line maximum or total budget exceeding ceiling.
        for const_stmt, bad_stmt, budget_err in (
            (
                "\tconst int LINE_SENDER_MAX_CHARS = 1024;",
                "\tconst int LINE_SENDER_MAX_CHARS = 32768;",
                r"per-line maximum.*LINE_BYTES_MAX",
            ),
            (
                "\tconst int LINE_TEXT_MAX_CHARS = 16384;",
                "\tconst int LINE_TEXT_MAX_CHARS = 32768;",
                r"per-line maximum.*LINE_BYTES_MAX",
            ),
            (
                "\tconst int LINE_BYTES_MAX = 491520;",
                "\tconst int LINE_BYTES_MAX = 600000;",
                r"LINE_BYTES_MAX \+ max_drops_bytes.*1048576",
            ),
            (
                "\tconst int DROP_SENDER_MAX_CHARS = 64;",
                "\tconst int DROP_SENDER_MAX_CHARS = 1024;",
                r"LINE_BYTES_MAX \+ max_drops_bytes.*1048576",
            ),
            (
                "\tconst int DROP_TEXT_MAX_CHARS = 256;",
                "\tconst int DROP_TEXT_MAX_CHARS = 1024;",
                r"LINE_BYTES_MAX \+ max_drops_bytes.*1048576",
            ),
            (
                "\tconst int DROP_CAPACITY = 64;",
                "\tconst int DROP_CAPACITY = 200;",
                r"LINE_BYTES_MAX \+ max_drops_bytes.*1048576",
            ),
        ):
            with self.subTest(budget_mutant=bad_stmt):
                bad_budget_src = self._mutate_executable(
                    self.chat_log_src, const_stmt, bad_stmt
                )
                with self.assertRaisesRegex(AssertionError, budget_err):
                    verify_payload_byte_budget(bad_budget_src)

        # Negative controls (D13e): bound constants altered.
        for const_stmt, bad_stmt, const_name in (
            (
                "\tconst int LINE_SENDER_MAX_CHARS = 1024;",
                "\tconst int LINE_SENDER_MAX_CHARS = 64;",
                "LINE_SENDER_MAX_CHARS",
            ),
            (
                "\tconst int LINE_TEXT_MAX_CHARS = 16384;",
                "\tconst int LINE_TEXT_MAX_CHARS = 8192;",
                "LINE_TEXT_MAX_CHARS",
            ),
            (
                "\tconst int DROP_SENDER_MAX_CHARS = 64;",
                "\tconst int DROP_SENDER_MAX_CHARS = 32;",
                "DROP_SENDER_MAX_CHARS",
            ),
            (
                "\tconst int DROP_TEXT_MAX_CHARS = 256;",
                "\tconst int DROP_TEXT_MAX_CHARS = 128;",
                "DROP_TEXT_MAX_CHARS",
            ),
            (
                "\tconst int LINE_BYTES_MAX = 491520;",
                "\tconst int LINE_BYTES_MAX = 655360;",
                "LINE_BYTES_MAX",
            ),
        ):
            with self.subTest(constant=const_name):
                bad_const_src = self._mutate_executable(
                    self.chat_log_src, const_stmt, bad_stmt
                )
                with self.assertRaisesRegex(AssertionError, const_name):
                    verify_payload_bound_constants(bad_const_src)

        # Negative controls (D11d, D13e): RecordLine UTF-8 character caps and cut flag.
        record_line_fields_block = (
            "\t\tcut = false;\n"
            "\t\tsender = params.param2;\n"
            "\t\tif (sender.LengthUtf8() > LINE_SENDER_MAX_CHARS)\n"
            "\t\t{\n\t\t\tsender = sender.SubstringUtf8(0, LINE_SENDER_MAX_CHARS);\n\t\t\tcut = true;\n\t\t}\n"
            "\t\ttext = params.param3;\n"
            "\t\tif (text.LengthUtf8() > LINE_TEXT_MAX_CHARS)\n"
            "\t\t{\n\t\t\ttext = text.SubstringUtf8(0, LINE_TEXT_MAX_CHARS);\n\t\t\tcut = true;\n\t\t}\n\n"
            "\t\tentry = new MCPChatLine();\n"
            "\t\tentry.seq = m_LastSeq;\n"
            "\t\tentry.t_client_ms = nowMs;\n"
            "\t\tentry.channel = params.param1;\n"
            "\t\tentry.sender = sender;\n"
            "\t\tentry.text = text;\n"
            "\t\tentry.cut = cut;"
        )
        whole_param2_line = self._mutate_executable(
            self.chat_log_src,
            record_line_fields_block,
            "\t\tcut = false;\n"
            "\t\ttext = params.param3;\n"
            "\t\tif (text.LengthUtf8() > LINE_TEXT_MAX_CHARS)\n"
            "\t\t{\n\t\t\ttext = text.SubstringUtf8(0, LINE_TEXT_MAX_CHARS);\n\t\t\tcut = true;\n\t\t}\n\n"
            "\t\tentry = new MCPChatLine();\n"
            "\t\tentry.seq = m_LastSeq;\n"
            "\t\tentry.t_client_ms = nowMs;\n"
            "\t\tentry.channel = params.param1;\n"
            "\t\tentry.sender = params.param2;\n"
            "\t\tentry.text = text;\n"
            "\t\tentry.cut = cut;",
        )
        verify_enforce_hygiene(whole_param2_line, CHAT_LOG_PATH.name)
        with self.assertRaisesRegex(AssertionError, "LINE_SENDER_MAX_CHARS"):
            verify_record_line_utf8_caps(whole_param2_line)

        whole_param3_line = self._mutate_executable(
            self.chat_log_src,
            record_line_fields_block,
            "\t\tcut = false;\n"
            "\t\tsender = params.param2;\n"
            "\t\tif (sender.LengthUtf8() > LINE_SENDER_MAX_CHARS)\n"
            "\t\t{\n\t\t\tsender = sender.SubstringUtf8(0, LINE_SENDER_MAX_CHARS);\n\t\t\tcut = true;\n\t\t}\n\n"
            "\t\tentry = new MCPChatLine();\n"
            "\t\tentry.seq = m_LastSeq;\n"
            "\t\tentry.t_client_ms = nowMs;\n"
            "\t\tentry.channel = params.param1;\n"
            "\t\tentry.sender = sender;\n"
            "\t\tentry.text = params.param3;\n"
            "\t\tentry.cut = cut;",
        )
        verify_enforce_hygiene(whole_param3_line, CHAT_LOG_PATH.name)
        with self.assertRaisesRegex(AssertionError, "LINE_TEXT_MAX_CHARS"):
            verify_record_line_utf8_caps(whole_param3_line)

        uncapped_line_sender = self._mutate_executable(
            self.chat_log_src,
            "\t\tif (sender.LengthUtf8() > LINE_SENDER_MAX_CHARS)\n"
            "\t\t{\n\t\t\tsender = sender.SubstringUtf8(0, LINE_SENDER_MAX_CHARS);\n\t\t\tcut = true;\n\t\t}\n",
            "",
        )
        with self.assertRaisesRegex(AssertionError, "LINE_SENDER_MAX_CHARS"):
            verify_record_line_utf8_caps(uncapped_line_sender)

        uncapped_line_text = self._mutate_executable(
            self.chat_log_src,
            "\t\tif (text.LengthUtf8() > LINE_TEXT_MAX_CHARS)\n"
            "\t\t{\n\t\t\ttext = text.SubstringUtf8(0, LINE_TEXT_MAX_CHARS);\n\t\t\tcut = true;\n\t\t}\n",
            "",
        )
        with self.assertRaisesRegex(AssertionError, "LINE_TEXT_MAX_CHARS"):
            verify_record_line_utf8_caps(uncapped_line_text)

        for target_cut, byte_cut in (
            (
                "sender = sender.SubstringUtf8(0, LINE_SENDER_MAX_CHARS);",
                "sender = sender.Substring(0, LINE_SENDER_MAX_CHARS);",
            ),
            (
                "text = text.SubstringUtf8(0, LINE_TEXT_MAX_CHARS);",
                "text = text.Substring(0, LINE_TEXT_MAX_CHARS);",
            ),
        ):
            with self.subTest(byte_line_cut=byte_cut):
                byte_substring_line = self._mutate_executable(
                    self.chat_log_src, target_cut, byte_cut
                )
                with self.assertRaisesRegex(AssertionError, "SubstringUtf8"):
                    verify_record_line_utf8_caps(byte_substring_line)

        for guarded_cut, unguarded_cut, cap_name in (
            (
                "\t\tif (sender.LengthUtf8() > LINE_SENDER_MAX_CHARS)\n"
                "\t\t{\n\t\t\tsender = sender.SubstringUtf8(0, LINE_SENDER_MAX_CHARS);\n\t\t\tcut = true;\n\t\t}",
                "\t\tsender = sender.SubstringUtf8(0, LINE_SENDER_MAX_CHARS);\n\t\tcut = true;",
                "LINE_SENDER_MAX_CHARS",
            ),
            (
                "\t\tif (text.LengthUtf8() > LINE_TEXT_MAX_CHARS)\n"
                "\t\t{\n\t\t\ttext = text.SubstringUtf8(0, LINE_TEXT_MAX_CHARS);\n\t\t\tcut = true;\n\t\t}",
                "\t\ttext = text.SubstringUtf8(0, LINE_TEXT_MAX_CHARS);\n\t\tcut = true;",
                "LINE_TEXT_MAX_CHARS",
            ),
        ):
            with self.subTest(unconditional_line_cut=cap_name):
                unconditional_line_cut = self._mutate_executable(
                    self.chat_log_src, guarded_cut, unguarded_cut
                )
                with self.assertRaisesRegex(AssertionError, cap_name):
                    verify_record_line_utf8_caps(unconditional_line_cut)

        # Negative controls (D13e): RecordLine never sets cut or omits cut on shorten.
        never_sets_line_cut = self._mutate_executable(
            self.chat_log_src,
            record_line_fields_block,
            "\t\tsender = params.param2;\n"
            "\t\tif (sender.LengthUtf8() > LINE_SENDER_MAX_CHARS)\n"
            "\t\t{\n\t\t\tsender = sender.SubstringUtf8(0, LINE_SENDER_MAX_CHARS);\n\t\t}\n"
            "\t\ttext = params.param3;\n"
            "\t\tif (text.LengthUtf8() > LINE_TEXT_MAX_CHARS)\n"
            "\t\t{\n\t\t\ttext = text.SubstringUtf8(0, LINE_TEXT_MAX_CHARS);\n\t\t}\n\n"
            "\t\tentry = new MCPChatLine();\n"
            "\t\tentry.seq = m_LastSeq;\n"
            "\t\tentry.t_client_ms = nowMs;\n"
            "\t\tentry.channel = params.param1;\n"
            "\t\tentry.sender = sender;\n"
            "\t\tentry.text = text;",
        )
        with self.assertRaisesRegex(AssertionError, "cut"):
            verify_record_line_utf8_caps(never_sets_line_cut)

        for cut_target, cut_mutant, cut_err in (
            (
                "\t\t\tsender = sender.SubstringUtf8(0, LINE_SENDER_MAX_CHARS);\n\t\t\tcut = true;",
                "\t\t\tsender = sender.SubstringUtf8(0, LINE_SENDER_MAX_CHARS);",
                r"set cut = true.*LINE_SENDER_MAX_CHARS",
            ),
            (
                "\t\t\ttext = text.SubstringUtf8(0, LINE_TEXT_MAX_CHARS);\n\t\t\tcut = true;",
                "\t\t\ttext = text.SubstringUtf8(0, LINE_TEXT_MAX_CHARS);",
                r"set cut = true.*LINE_TEXT_MAX_CHARS",
            ),
            (
                "\t\tentry.text = text;\n\t\tentry.cut = cut;",
                "\t\tentry.text = text;",
                r"entry\.cut = cut",
            ),
            (
                "\t\tentry.text = text;\n\t\tentry.cut = cut;",
                "\t\tentry.text = text;\n\t\tentry.cut = false;",
                r"entry\.cut = cut",
            ),
            (
                "\t\tm_TotalLines = m_TotalLines + 1;\n\n\t\tcut = false;",
                "\t\tm_TotalLines = m_TotalLines + 1;\n\n\t\tcut = true;",
                r"cut = false",
            ),
        ):
            with self.subTest(line_cut_mutant=cut_mutant):
                bad_line_cut = self._mutate_executable(
                    self.chat_log_src, cut_target, cut_mutant
                )
                with self.assertRaisesRegex(AssertionError, cut_err):
                    verify_record_line_utf8_caps(bad_line_cut)

        # Negative controls (D9e, D13e): RecordDrop UTF-8 character caps and cut flag.
        uncapped_sender = self._mutate_executable(
            self.chat_log_src,
            "\t\t\tif (sender.LengthUtf8() > DROP_SENDER_MAX_CHARS)\n"
            "\t\t\t{\n\t\t\t\tsender = sender.SubstringUtf8(0, DROP_SENDER_MAX_CHARS);\n\t\t\t\tcut = true;\n\t\t\t}\n",
            "",
        )
        with self.assertRaisesRegex(AssertionError, "DROP_SENDER_MAX_CHARS"):
            verify_record_drop_utf8_caps(uncapped_sender)

        uncapped_text = self._mutate_executable(
            self.chat_log_src,
            "\t\t\tif (text.LengthUtf8() > DROP_TEXT_MAX_CHARS)\n"
            "\t\t\t{\n\t\t\t\ttext = text.SubstringUtf8(0, DROP_TEXT_MAX_CHARS);\n\t\t\t\tcut = true;\n\t\t\t}\n",
            "",
        )
        with self.assertRaisesRegex(AssertionError, "DROP_TEXT_MAX_CHARS"):
            verify_record_drop_utf8_caps(uncapped_text)

        byte_substring_drop = self._mutate_executable(
            self.chat_log_src,
            "text = text.SubstringUtf8(0, DROP_TEXT_MAX_CHARS);",
            "text = text.Substring(0, DROP_TEXT_MAX_CHARS);",
        )
        with self.assertRaisesRegex(AssertionError, "SubstringUtf8"):
            verify_record_drop_utf8_caps(byte_substring_drop)

        unconditional_drop_cut = self._mutate_executable(
            self.chat_log_src,
            "\t\t\tif (text.LengthUtf8() > DROP_TEXT_MAX_CHARS)\n"
            "\t\t\t{\n\t\t\t\ttext = text.SubstringUtf8(0, DROP_TEXT_MAX_CHARS);\n\t\t\t\tcut = true;\n\t\t\t}",
            "\t\t\ttext = text.SubstringUtf8(0, DROP_TEXT_MAX_CHARS);\n\t\t\tcut = true;",
        )
        with self.assertRaisesRegex(AssertionError, "DROP_TEXT_MAX_CHARS"):
            verify_record_drop_utf8_caps(unconditional_drop_cut)

        # Negative controls (D13e): RecordDrop never sets cut or omits cut on shorten.
        record_drop_fields_block = (
            "\t\tcut = false;\n"
            "\t\tentry = new MCPChatDrop();\n"
            "\t\tentry.seq = m_LastSeq;\n"
            "\t\tentry.t_client_ms = nowMs;\n"
            "\t\tentry.channel = 0;\n"
            "\t\tentry.sender = \"\";\n"
            "\t\tentry.text = \"\";\n"
            "\t\tif (params)\n"
            "\t\t{\n"
            "\t\t\tsender = params.param2;\n"
            "\t\t\tif (sender.LengthUtf8() > DROP_SENDER_MAX_CHARS)\n"
            "\t\t\t{\n\t\t\t\tsender = sender.SubstringUtf8(0, DROP_SENDER_MAX_CHARS);\n\t\t\t\tcut = true;\n\t\t\t}\n"
            "\t\t\ttext = params.param3;\n"
            "\t\t\tif (text.LengthUtf8() > DROP_TEXT_MAX_CHARS)\n"
            "\t\t\t{\n\t\t\t\ttext = text.SubstringUtf8(0, DROP_TEXT_MAX_CHARS);\n\t\t\t\tcut = true;\n\t\t\t}\n"
            "\t\t\tentry.channel = params.param1;\n"
            "\t\t\tentry.sender = sender;\n"
            "\t\t\tentry.text = text;\n"
            "\t\t}\n"
            "\t\tentry.cut = cut;"
        )
        never_sets_drop_cut = self._mutate_executable(
            self.chat_log_src,
            record_drop_fields_block,
            "\t\tentry = new MCPChatDrop();\n"
            "\t\tentry.seq = m_LastSeq;\n"
            "\t\tentry.t_client_ms = nowMs;\n"
            "\t\tentry.channel = 0;\n"
            "\t\tentry.sender = \"\";\n"
            "\t\tentry.text = \"\";\n"
            "\t\tif (params)\n"
            "\t\t{\n"
            "\t\t\tsender = params.param2;\n"
            "\t\t\tif (sender.LengthUtf8() > DROP_SENDER_MAX_CHARS)\n"
            "\t\t\t{\n\t\t\t\tsender = sender.SubstringUtf8(0, DROP_SENDER_MAX_CHARS);\n\t\t\t}\n"
            "\t\t\ttext = params.param3;\n"
            "\t\t\tif (text.LengthUtf8() > DROP_TEXT_MAX_CHARS)\n"
            "\t\t\t{\n\t\t\t\ttext = text.SubstringUtf8(0, DROP_TEXT_MAX_CHARS);\n\t\t\t}\n"
            "\t\t\tentry.channel = params.param1;\n"
            "\t\t\tentry.sender = sender;\n"
            "\t\t\tentry.text = text;\n"
            "\t\t}",
        )
        with self.assertRaisesRegex(AssertionError, "cut"):
            verify_record_drop_utf8_caps(never_sets_drop_cut)

        for drop_cut_target, drop_cut_mutant, drop_cut_err in (
            (
                "\t\t\t\tsender = sender.SubstringUtf8(0, DROP_SENDER_MAX_CHARS);\n\t\t\t\tcut = true;",
                "\t\t\t\tsender = sender.SubstringUtf8(0, DROP_SENDER_MAX_CHARS);",
                r"set cut = true.*DROP_SENDER_MAX_CHARS",
            ),
            (
                "\t\t\t\ttext = text.SubstringUtf8(0, DROP_TEXT_MAX_CHARS);\n\t\t\t\tcut = true;",
                "\t\t\t\ttext = text.SubstringUtf8(0, DROP_TEXT_MAX_CHARS);",
                r"set cut = true.*DROP_TEXT_MAX_CHARS",
            ),
            (
                "\t\tentry.cut = cut;\n\t\tentry.reason = ClassifyDropReason(params, wasAlive);",
                "\t\tentry.reason = ClassifyDropReason(params, wasAlive);",
                r"entry\.cut = cut",
            ),
            (
                "\t\tentry.cut = cut;\n\t\tentry.reason = ClassifyDropReason(params, wasAlive);",
                "\t\tentry.cut = false;\n\t\tentry.reason = ClassifyDropReason(params, wasAlive);",
                r"entry\.cut = cut",
            ),
            (
                "\t\tcut = false;\n\t\tentry = new MCPChatDrop();",
                "\t\tcut = true;\n\t\tentry = new MCPChatDrop();",
                r"cut = false",
            ),
        ):
            with self.subTest(drop_cut_mutant=drop_cut_mutant):
                bad_drop_cut = self._mutate_executable(
                    self.chat_log_src, drop_cut_target, drop_cut_mutant
                )
                with self.assertRaisesRegex(AssertionError, drop_cut_err):
                    verify_record_drop_utf8_caps(bad_drop_cut)

        # Negative controls (D9e): Read line byte guard and first-eligible-line progress.
        missing_byte_guard = self._mutate_executable(
            self.chat_log_src,
            "\t\t\t\tif (outRead.lines.Count() > 0 && lineBytes + itemBytes > LINE_BYTES_MAX)\n"
            "\t\t\t\t{\n\t\t\t\t\tlinesCut = true;\n\t\t\t\t\tbreak;\n\t\t\t\t}\n",
            "",
        )
        with self.assertRaisesRegex(AssertionError, "LINE_BYTES_MAX"):
            verify_read_line_byte_guard(missing_byte_guard)

        blocks_first_line = self._mutate_executable(
            self.chat_log_src,
            "if (outRead.lines.Count() > 0 && lineBytes + itemBytes > LINE_BYTES_MAX)",
            "if (lineBytes + itemBytes > LINE_BYTES_MAX)",
        )
        with self.assertRaisesRegex(AssertionError, "first eligible line"):
            verify_read_line_byte_guard(blocks_first_line)

        missing_cut_flag = self._mutate_executable(
            self.chat_log_src,
            "\t\t\t\tif (outRead.lines.Count() > 0 && lineBytes + itemBytes > LINE_BYTES_MAX)\n"
            "\t\t\t\t{\n\t\t\t\t\tlinesCut = true;\n\t\t\t\t\tbreak;\n\t\t\t\t}",
            "\t\t\t\tif (outRead.lines.Count() > 0 && lineBytes + itemBytes > LINE_BYTES_MAX)\n"
            "\t\t\t\t{\n\t\t\t\t\tbreak;\n\t\t\t\t}",
        )
        with self.assertRaisesRegex(AssertionError, "linesCut = true"):
            verify_read_line_byte_guard(missing_cut_flag)

        non_accumulating_bytes = self._mutate_executable(
            self.chat_log_src,
            "\t\t\t\tlineBytes = lineBytes + itemBytes;",
            "\t\t\t\tlineBytes = itemBytes;",
        )
        with self.assertRaisesRegex(AssertionError, "lineBytes"):
            verify_read_line_byte_guard(non_accumulating_bytes)

        utf8_char_estimate = self._mutate_executable(
            self.chat_log_src,
            "itemBytes = 6 * (lineItem.sender.Length() + lineItem.text.Length()) + 384;",
            "itemBytes = 6 * (lineItem.sender.LengthUtf8() + lineItem.text.LengthUtf8()) + 384;",
        )
        with self.assertRaisesRegex(AssertionError, "byte estimate"):
            verify_read_line_byte_guard(utf8_char_estimate)

    def test_chat_message_cut_fields_monotonic_total_lines_and_ordered_evictions(self) -> None:
        verify_chat_message_classes(self.messages_src)
        verify_monotonic_total_lines(self.chat_log_src)
        verify_ordered_ring_evictions(self.chat_log_src)

        # Negative controls (D13e): MCPChatLine and MCPChatDrop must declare bool cut in order.
        for msg_target, msg_mutant, msg_err in (
            ("\tbool cut;\n};", "};", "MCPChatLine fields"),
            ("\tbool cut;\n};", "\tint cut;\n};", "MCPChatLine fields"),
            (
                "\tbool cut;\n\tstring reason;\n};",
                "\tstring reason;\n};",
                "MCPChatDrop fields",
            ),
            (
                "\tbool cut;\n\tstring reason;\n};",
                "\tstring reason;\n\tbool cut;\n};",
                "MCPChatDrop fields",
            ),
        ):
            with self.subTest(msg_mutant=msg_mutant):
                bad_messages = self._mutate_executable(
                    self.messages_src, msg_target, msg_mutant
                )
                with self.assertRaisesRegex(AssertionError, msg_err):
                    verify_chat_message_classes(bad_messages)

        # Negative controls (D13e): GetTotalLines() must return monotonic m_TotalLines.
        for total_target, total_mutant, total_err in (
            (
                "\t\treturn m_TotalLines;",
                "\t\treturn m_Lines.Count();",
                r"GetTotalLines\(\) must unconditionally return m_TotalLines",
            ),
            (
                "\t\tm_TotalLines = m_TotalLines + 1;\n",
                "",
                r"RecordLine must increment m_TotalLines",
            ),
            (
                "\t\tm_TotalLines = m_TotalLines + 1;",
                "\t\tm_TotalLines = m_Lines.Count();",
                r"RecordLine must increment m_TotalLines",
            ),
        ):
            with self.subTest(total_mutant=total_mutant):
                bad_total = self._mutate_executable(
                    self.chat_log_src, total_target, total_mutant
                )
                with self.assertRaisesRegex(AssertionError, total_err):
                    verify_monotonic_total_lines(bad_total)

        # Negative controls (D13e): both ring evictions must use RemoveOrdered(0).
        for evict_target, evict_mutant, evict_err in (
            (
                "m_Lines.RemoveOrdered(0);",
                "m_Lines.Remove(0);",
                r"RemoveOrdered\(0\)",
            ),
            (
                "m_Drops.RemoveOrdered(0);",
                "m_Drops.Remove(0);",
                r"RemoveOrdered\(0\)",
            ),
            (
                "\t\t\tm_Lines.RemoveOrdered(0);\n",
                "",
                r"m_Lines\.RemoveOrdered",
            ),
            (
                "\t\t\tm_Drops.RemoveOrdered(0);\n",
                "",
                r"m_Drops\.RemoveOrdered",
            ),
            (
                "m_Lines.RemoveOrdered(0);",
                "m_Lines.RemoveOrdered(1);",
                r"m_Lines\.RemoveOrdered\(0\)",
            ),
        ):
            with self.subTest(evict_mutant=evict_mutant):
                bad_evict = self._mutate_executable(
                    self.chat_log_src, evict_target, evict_mutant
                )
                with self.assertRaisesRegex(AssertionError, evict_err):
                    verify_ordered_ring_evictions(bad_evict)

    def test_client_poll_caps_and_dispatch_include_chat_read(self) -> None:
        cleaned = _strip_comments(self.bridge_src)
        caps_match = _CAPS_DECL_RE.search(cleaned)
        self.assertIsNotNone(caps_match)
        assert caps_match is not None
        pieces = _STRING_LITERAL_RE.findall(caps_match.group(1))
        self.assertGreaterEqual(len(pieces), 2)
        for piece in pieces:
            self.assertLessEqual(len(piece), MAX_LITERAL_CHARS)
        caps = "".join(pieces).split(",")
        self.assertIn(COMMAND, caps)
        self.assertEqual(caps, sorted(set(caps)))

        dispatch_body = _method_body(cleaned, "protected void Dispatch(MCPCommand command)")
        branch_re = re.compile(
            r'else\s+if\s*\(\s*command\.cmd\s*==\s*"chat_read"\s*\)\s*\{\s*'
            r"postNow\s*=\s*DispatchChatRead\s*\(\s*command\s*,\s*result\s*\)\s*;\s*\}"
        )
        branch_match = branch_re.search(dispatch_body)
        self.assertIsNotNone(branch_match)
        assert branch_match is not None
        self.assertLess(branch_match.start(), dispatch_body.index('"unknown_command"'))

        handler_body = _method_body(
            cleaned,
            "protected bool DispatchChatRead(MCPCommand command, MCPResult result)",
        )
        self.assertIn("result.chat_read = chatLog.Read(marker, maxLines);", handler_body)
        self.assertIn("result.ok = true;", handler_body)

    def test_enforce_ring_constants_drop_reasons_and_hygiene(self) -> None:
        verify_enforce_hygiene(self.chat_log_src, CHAT_LOG_PATH.name)
        verify_enforce_hygiene(self.mission_src, MISSION_GAMEPLAY_PATH.name)

        # Positive controls (D1): ref as generic template argument on array and map.
        valid_array_generic = """
class SampleArrayGeneric
{
\tprotected ref array<ref MCPChatLine> m_Lines;
\tvoid CopyFrom(array<ref MCPChatLine> sourceLines)
\t{
\t\tarray<ref MCPChatLine> scratch;
\t\tscratch = new array<ref MCPChatLine>();
\t}
};
"""
        verify_enforce_hygiene(valid_array_generic, "SampleArrayGeneric.c")

        valid_map_generic = """
class SampleMapGeneric
{
\tprotected ref map<string, ref MCPChatLine> m_ByKey;
\tvoid CopyFrom(map<string, ref MCPChatLine> sourceMap)
\t{
\t\tmap<string, ref MCPChatLine> scratch;
\t\tscratch = new map<string, ref MCPChatLine>();
\t}
};
"""
        verify_enforce_hygiene(valid_map_generic, "SampleMapGeneric.c")

        # Negative controls (D1): ref qualifying a local declaration or method signature.
        for bad_local_decl in (
            "ref MCPChatLine entry;",
            "ref array<ref MCPChatLine> scratch;",
            "ref map<string, ref MCPChatLine> scratch;",
        ):
            with self.subTest(bad_local_decl=bad_local_decl):
                bad_local_src = self._mutate_executable(
                    valid_array_generic,
                    "\t\tarray<ref MCPChatLine> scratch;\n"
                    "\t\tscratch = new array<ref MCPChatLine>();",
                    f"\t\t{bad_local_decl}",
                )
                with self.assertRaisesRegex(AssertionError, "local ref declaration"):
                    verify_enforce_hygiene(bad_local_src, "SampleBadLocal.c")

        for bad_sig in (
            "void Accept(ref MCPChatLine entry)",
            "ref MCPChatLine Current()",
        ):
            with self.subTest(bad_sig=bad_sig):
                bad_sig_src = self._mutate_executable(
                    valid_array_generic,
                    "\tvoid CopyFrom(array<ref MCPChatLine> sourceLines)",
                    f"\t{bad_sig}",
                )
                with self.assertRaisesRegex(AssertionError, "non-generic ref in method signature"):
                    verify_enforce_hygiene(bad_sig_src, "SampleBadSig.c")

        self.assertIn("const int LINE_CAPACITY = 256;", self.chat_log_src)
        self.assertIn("const int DROP_CAPACITY = 64;", self.chat_log_src)
        self.assertIn("const int MAX_SEQ = 2147483647;", self.chat_log_src)
        self.assertIn("const int LINE_SENDER_MAX_CHARS = 1024;", self.chat_log_src)
        self.assertIn("const int LINE_TEXT_MAX_CHARS = 16384;", self.chat_log_src)
        self.assertIn("const int DROP_SENDER_MAX_CHARS = 64;", self.chat_log_src)
        self.assertIn("const int DROP_TEXT_MAX_CHARS = 256;", self.chat_log_src)
        self.assertIn("const int LINE_BYTES_MAX = 491520;", self.chat_log_src)
        for reason in ("not_alive", "filtered_by_option", "unknown_channel", "not_shown"):
            self.assertIn(f'"{reason}"', self.chat_log_src)
        for option in ("GAME_MESSAGES", "ADMIN_MESSAGES", "PLAYER_MESSAGES"):
            self.assertIn(f"EDayZProfilesOptions.{option}", self.chat_log_src)

        # MCPArgs reuses max_lines once and adds marker once; MCPResult ends with chat_read.
        cleaned_msgs = _strip_comments(self.messages_src)
        args_body = _class_body(cleaned_msgs, r"^\s*class\s+MCPArgs\b")
        self.assertEqual(len(re.findall(r"^\tint max_lines;$", args_body, re.M)), 1)
        self.assertEqual(len(re.findall(r"^\tint marker;$", args_body, re.M)), 1)
        result_body = _class_body(cleaned_msgs, r"^\s*class\s+MCPResult\b")
        ref_fields = re.findall(r"^\tref\s+(?:array<[^>]+>|\w+)\s+(\w+);$", result_body, re.M)
        self.assertEqual(ref_fields[-1], "chat_read")
        self.assertEqual(tuple(ref_fields), result_prune.PRUNABLE_FIELDS)


class ChatReadCatalogAndReadOnlyTest(unittest.TestCase):
    def test_chat_read_is_read_only_and_in_initial_read_catalog(self) -> None:
        self.assertIn(COMMAND, session_coordination.READ_ONLY_COMMANDS)
        self.assertFalse(session_coordination.command_requires_lease(COMMAND))
        self.assertIn(COMMAND, tool_catalog._INITIAL_READ_TOOL_NAMES)
        self.assertIn(COMMAND, tool_catalog._INITIAL_CATALOG_NAMES)
        self.assertIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertEqual(
            loopback._CLIENT_ANNOUNCED_COMMANDS.get(COMMAND),
            "bridge_capability_missing",
        )
        self.assertEqual(
            bridge_readiness._BRIDGE_COMMAND_TOOLS["client"].get(COMMAND),
            COMMAND,
        )


class ChatReadToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def _tool(self):
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        return tools[COMMAND]

    async def test_schema_and_description_are_closed_and_lease_free(self) -> None:
        tool = await self._tool()
        description = tool.description or ""
        self.assertFalse(description.startswith(LEASE_TOOL_LINE), description)
        self.assertTrue(
            description.startswith("Read lines shown in the client chat since marker. "),
            description,
        )
        self.assertEqual(
            tool_catalog._compact_description(description),
            "Read lines shown in the client chat since marker. …",
        )
        self.assertIn("min(marker, 2147483647)", description)
        self.assertIn("come again on the next read, each with its seq", description)
        self.assertIn(
            "A line keeps up to 1024 characters of its sender and 16384 of its text, "
            "a drop up to 64 and 256, and cut is true on an entry that was "
            "shortened; lines stop like a max_lines cut when the answer would pass "
            "about 480 KiB.",
            description,
        )
        self.assertNotIn("at most 64 characters", description)
        self.assertNotIn("640 KiB", description)
        self.assertIn(
            "The log restarts at seq 1 with each client mission; if a marker this "
            "tool returned earlier comes back as a lower returned marker, the client "
            "mission changed and reading should start again from 0. A requested "
            "marker above the newest seq also returns the newest seq, so a lower "
            "returned marker alone proves nothing.",
            description,
        )
        self.assertNotIn("marker below the requested one", description)
        schema = tool.inputSchema
        self.assertIs(schema.get("additionalProperties"), False)
        self.assertEqual(
            set(schema.get("properties", {})),
            {"marker", "max_lines", "timeout_s"},
        )
        for prop_name, prop_schema in schema["properties"].items():
            self.assertNotIn("anyOf", prop_schema, prop_name)

    async def test_default_arguments_target_client_and_return_bridge_response_verbatim(self) -> None:
        bridge_reply = {
            "ok": 1,
            "chat_read": {
                "lines": [
                    {
                        "seq": 1,
                        "t_client_ms": 4200,
                        "channel": 0,
                        "sender": "",
                        "text": "[LFPG] Replaced 1 existing cable: each port holds only one cable.",
                        "cut": False,
                    }
                ],
                "dropped": [],
                "marker": 1,
                "truncated": False,
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
        self.assertEqual(_tool_payload(raw), bridge_reply)

        custom_reply = {
            "ok": 1,
            "chat_read": {"lines": [], "dropped": [], "marker": 0, "truncated": False},
        }
        call_custom = AsyncMock(return_value=custom_reply)
        with patch.object(self.runtime, "call_bridge", new=call_custom):
            raw_custom = await self.app.call_tool(
                COMMAND, {"marker": 25, "max_lines": 500, "timeout_s": 4.0}
            )
        call_custom.assert_awaited_once_with(
            COMMAND,
            {"marker": 25, "max_lines": 500},
            "client",
            4.0,
        )
        self.assertEqual(_tool_payload(raw_custom), custom_reply)

        # Markers above 32-bit int ceiling are accepted and clamped to 2147483647 (D4),
        # while loopback keeps its own 32-bit ingress maximum.
        for huge_marker in (2_147_483_648, 10**12):
            with self.subTest(marker=huge_marker):
                call_huge = AsyncMock(return_value=custom_reply)
                with patch.object(self.runtime, "call_bridge", new=call_huge):
                    raw_huge = await self.app.call_tool(COMMAND, {"marker": huge_marker})
                call_huge.assert_awaited_once_with(
                    COMMAND,
                    {"marker": INT32_MAX, "max_lines": 200},
                    "client",
                    DEFAULT_TOOL_TIMEOUT_S,
                )
                self.assertEqual(_tool_payload(raw_huge), custom_reply)
        self.assertEqual(
            loopback.validate_command_args(COMMAND, {"marker": INT32_MAX, "max_lines": 200}),
            (True, None),
        )
        self.assertEqual(
            loopback.validate_command_args(COMMAND, {"marker": INT32_MAX + 1, "max_lines": 200}),
            (False, "bad_args"),
        )

    async def test_rejects_out_of_range_booleans_nulls_and_unknown_arguments(self) -> None:
        bad_payloads: tuple[dict[str, Any], ...] = (
            {"marker": -1},
            {"max_lines": 0},
            {"max_lines": 501},
            {"marker": True},
            {"marker": False},
            {"max_lines": True},
            {"max_lines": False},
            {"timeout_s": True},
            {"timeout_s": False},
            {"marker": None},
            {"max_lines": None},
            {"marker": "0"},
            {"marker": 1.5},
            {"unknown_arg": 1},
            {"marker": 0, "extra": "nope"},
        )
        for bad in bad_payloads:
            with self.subTest(arguments=bad):
                call = AsyncMock(return_value={"ok": 1})
                with patch.object(self.runtime, "call_bridge", new=call):
                    with self.assertRaises(ToolError) as raised:
                        await self.app.call_tool(COMMAND, bad)
                self.assertTrue(_tool_error_text(raised.exception))
                call.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
