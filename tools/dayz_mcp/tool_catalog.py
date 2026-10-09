"""Which public tools the catalog lists, and which visible tool a reply names next.

Progressive disclosure (the compact pre-lease catalog of client mode) and the
next-step hints attached to replies. Moved out of server.py unchanged (backlog
71fc); server.py imports every name back, so dayz_mcp.server.<name> is the
same object.
"""

from __future__ import annotations

from typing import Any

from dayz_mcp.agent_loop import PUBLIC_NEXT_TOOLS, ok_next_step
from dayz_mcp.session_coordination import command_requires_lease


# Progressive disclosure (fb-20260917-092908-2ad1): the first tools/list a
# client-mode caller sees is a compact catalog: the session and lifecycle core
# plus the reads that need no lease. The rest of world_*/vehicle_*/ui_* stays
# off the catalog until a lease is held. Embedded mode keeps the full registry
# so in-process tests and the host-side catalog stay complete. The listing is
# not an access control: a tool it leaves out still runs when called by name,
# and the lease gate refuses a mutation without a lease (fb-20260925-233937-b753).
_LEASE_REVEAL_PREFIXES = ("world_", "vehicle_", "ui_")
_INITIAL_CORE_NAMES = frozenset(
    {
        "bridge_status",
        "dayz_knowledge_find",
        "dayz_knowledge_prepare",
        "dayz_knowledge_show",
        "dayz_knowledge_status",
        "dayz_test_close",
        "dayz_test_run",
        "dayz_test_stop",
        "lease_acquire",
        "pipeline_feedback",
        "pipeline_inbox",
        "pipeline_resolve",
        "session_acquire_wait",
        "session_heartbeat",
        "session_release",
        "session_status",
        "wait_for",
    }
)
# The public tools whose only bridge command is in READ_ONLY_COMMANDS (the tool
# _BRIDGE_COMMAND_TOOLS names for it), plus logs_since, which reads host logs
# and never reaches the game. They run without a lease, so hiding them until
# one is held only took the information away from weak callers (b753).
_INITIAL_READ_TOOL_NAMES = frozenset(
    {
        "action_cursor",
        "camera_get",
        "entities_query",
        "input_describe",
        "logs_since",
        "object_doors",
        "object_inspect",
        "query_all_players",
        "query_get_in_condition",
        "query_player_state",
        "scene_raycast",
        "surface_query",
        "telemetry_read",
        "ui_tree",
        "vehicle_telemetry",
        "weapon_state",
        "world_time_get",
    }
)
_INITIAL_CATALOG_NAMES = _INITIAL_CORE_NAMES | _INITIAL_READ_TOOL_NAMES
# Whole sentences are kept up to this many characters (b753); see
# _compact_description for what happens when the first sentence is longer.
_INITIAL_DESCRIPTION_LIMIT = 120
_COMPACT_DESCRIPTION_MARKER = "…"
_OPENER_FOR_CLOSER = {")": "(", "]": "[", "}": "{"}
# Claude Code never re-lists after tools/list_changed (#93, e7ef): a compact
# catalog there hides the game verbs for the whole session, so this platform
# lists the full catalog from the start, as --no-progressive-disclosure does.
_FULL_CATALOG_PLATFORMS = frozenset({"claude"})


def _runtime_holds_lease(runtime: Any) -> bool:
    token = getattr(runtime, "active_lease_token", None)
    if callable(token):
        try:
            token = token()
        except Exception:
            token = None
    return bool(token)


def _progressive_disclosure_enabled(config: Any) -> bool:
    """True when this process lists the compact catalog while it holds no lease."""
    return (
        getattr(config, "mode", None) == "client"
        and getattr(config, "progressive_disclosure", True) is not False
        and getattr(config, "client_platform", None) not in _FULL_CATALOG_PLATFORMS
    )


def _progressive_disclosure_active(runtime: Any) -> bool:
    return _progressive_disclosure_enabled(
        getattr(runtime, "config", None)
    ) and not _runtime_holds_lease(runtime)


def _is_lease_revealed_tool(name: str) -> bool:
    return name.startswith(_LEASE_REVEAL_PREFIXES)


def _compact_description(description: str) -> str:
    """Shorten a description for the compact catalog without a misleading cut.

    Keeps the longest run of whole sentences that fits in
    _INITIAL_DESCRIPTION_LIMIT. When even the first sentence is longer, it is
    cut at the last space outside brackets that fits, with the separator it
    leaves dangling removed. Either way the dropped text is marked with a
    trailing "…", and with no such space the marker stands alone. A cut inside a
    word read as a real value (b753: "kind must be bug | request | find…").
    """
    if len(description) <= _INITIAL_DESCRIPTION_LIMIT:
        return description
    room = _INITIAL_DESCRIPTION_LIMIT - len(" " + _COMPACT_DESCRIPTION_MARKER)
    sentence_end = word_end = 0
    open_brackets: list[str] = []
    for index, char in enumerate(description[: room + 1]):
        if char in "([{":
            open_brackets.append(char)
        elif char in _OPENER_FOR_CLOSER:
            if open_brackets and open_brackets[-1] == _OPENER_FOR_CLOSER[char]:
                open_brackets.pop()
        elif char.isspace() and index > 0 and not open_brackets:
            word_end = index
            if description[index - 1] in ".!?" and description[
                max(0, index - 4) : index
            ].lower() not in ("e.g.", "i.e."):
                sentence_end = index
    if sentence_end:
        kept = description[:sentence_end].rstrip()
    else:
        kept = description[:word_end].rstrip().rstrip(",;:|/-=>+&").rstrip()
    if not kept:
        return _COMPACT_DESCRIPTION_MARKER
    return f"{kept} {_COMPACT_DESCRIPTION_MARKER}"


def _compact_initial_catalog(tools: list[Any]) -> list[Any]:
    """The pre-lease tools/list for 8B clients: the core plus lease-free reads.

    About 17 KB with the reads (b753), most of it input schemas, which stay
    whole. Descriptions go through _compact_description; outputSchema is
    dropped. A world_*/vehicle_*/ui_* tool is listed only when it is a read.
    """
    compacted: list[Any] = []
    for tool in tools:
        name = getattr(tool, "name", "")
        if name not in _INITIAL_CATALOG_NAMES:
            continue
        if _is_lease_revealed_tool(name) and name not in _INITIAL_READ_TOOL_NAMES:
            continue
        description = getattr(tool, "description", None) or ""
        updates: dict[str, Any] = {}
        compact_description = _compact_description(description)
        if compact_description != description:
            updates["description"] = compact_description
        if getattr(tool, "outputSchema", None) is not None:
            updates["outputSchema"] = None
        compacted.append(tool.model_copy(update=updates) if updates else tool)
    return compacted


def _visible_public_tools(runtime: Any) -> frozenset[str]:
    """Return the real registry when build_app published it, else the full public set."""
    names = getattr(runtime, "_registered_tool_names", None)
    if isinstance(names, (set, frozenset)):
        return frozenset(name for name in names if isinstance(name, str))
    return PUBLIC_NEXT_TOOLS


def _next_public_call(runtime: Any, *preferred: str) -> dict[str, Any]:
    visible = _visible_public_tools(runtime)
    for tool in preferred:
        if tool in PUBLIC_NEXT_TOOLS and tool in visible:
            return {"tool": tool, "args": {}}
    # Every supported registry pack includes bridge_status. The fallback also
    # keeps direct Runtime instances useful before build_app attaches its set.
    return {"tool": "bridge_status", "args": {}}


def _bridge_success_candidates(
    cmd: str, result: dict[str, Any]
) -> list[tuple[str, dict[str, Any]]]:
    """Return ordered follow-up candidates derived only from wire-visible facts."""
    if cmd == "query_all_players":
        players = result.get("players")
        if isinstance(players, list) and players:
            return [("query_player_state", {}), ("bridge_status", {})]
        if isinstance(players, list):
            return [
                ("wait_for", {"condition": "players_at_least", "value": 1}),
                ("bridge_status", {}),
            ]
    if cmd == "entities_query":
        entities = result.get("entities")
        if isinstance(entities, list) and entities:
            row = next((item for item in entities if isinstance(item, dict)), None)
            if isinstance(row, dict):
                object_type = row.get("type") or row.get("classname")
                pos = row.get("pos")
                if (
                    isinstance(object_type, str)
                    and object_type
                    and isinstance(pos, list)
                    and len(pos) == 3
                ):
                    return [
                        (
                            "object_inspect",
                            {
                                "type": object_type,
                                "pos": list(pos),
                                "want": ["bounding_center"],
                            },
                        ),
                        ("bridge_status", {}),
                    ]
    return [("bridge_status", {}), ("session_status", {})]


def _with_bridge_success_hints(
    runtime: Any, cmd: str, result: dict[str, Any]
) -> dict[str, Any]:
    """Add at most two calls that exist in this client's exposed registry."""
    if not isinstance(result, dict) or not result.get("ok"):
        return result
    visible = _visible_public_tools(runtime)
    suggested: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tool, args in _bridge_success_candidates(cmd, result):
        if tool in seen or tool not in PUBLIC_NEXT_TOOLS or tool not in visible:
            continue
        suggested.append({"tool": tool, "args": dict(args)})
        seen.add(tool)
        if len(suggested) == 2:
            break
    if not suggested:
        return _with_ok_next_step(result, cmd)
    payload = dict(result)
    payload["suggested_calls"] = suggested
    return _with_ok_next_step(payload, cmd)


def _with_ok_next_step(result: dict[str, Any], cmd: str) -> dict[str, Any]:
    """Attach next_step from PUBLIC_NEXT_TOOLS on ok:true mutation/session results."""
    if not isinstance(result, dict):
        return result
    if result.get("ok") not in (True, 1) and "ok" in result:
        return result
    if result.get("error"):
        return result
    existing = result.get("next_step")
    if isinstance(existing, str) and existing in PUBLIC_NEXT_TOOLS:
        return result
    mutating = command_requires_lease(cmd)
    follow = ok_next_step(cmd, mutating=mutating)
    if follow is None:
        return result
    payload = dict(result)
    if payload.get("ok") not in (True, 1):
        payload["ok"] = True
    payload["next_step"] = follow
    return payload
