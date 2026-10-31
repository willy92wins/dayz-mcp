"""Opt-in MCP tool packs.

The full registry remains the default. Packs are closed groups applied once,
after build_app has registered and patched every tool.
"""

from __future__ import annotations

from typing import Any

from dayz_mcp.agent_loop import PUBLIC_NEXT_TOOLS


LOCAL8B_TOOL_NAMES = frozenset(
    {
        "bridge_status",
        "session_acquire_wait",
        "session_heartbeat",
        "session_release",
        "session_status",
        "pipeline_inbox",
        "pipeline_feedback",
        "pipeline_resolve",
        "dayz_knowledge_status",
        "dayz_knowledge_find",
        "dayz_knowledge_show",
        "dayz_knowledge_prepare",
        "dayz_test_run",
        "dayz_test_stop",
        "dayz_test_close",
        "wait_for",
    }
)

# Fixed live-admin catalog (ticket C1, fb-20261028-131018-d528): a closed
# set of 23 tools for operating a live server with a small local model:
# session/lease handling, lease-free reads, player warning, teleport and
# heal, time/weather, spawn/remove and restore. The same set is listed
# before and after the lease is taken (see tool_catalog: progressive
# disclosure is disabled for this pack), so a host that never re-lists
# after tools/list_changed still sees every verb. The pack removes the
# tools it leaves out from the registry (a call by name returns
# Unknown tool); that removal is not daemon authorization, which still
# gates the tools that remain registered (ticket B4). Sizing assumes decision D7
# as a working assumption (catalog at most 5000 tokens; see
# tests/test_admin_tool_pack.py for the estimator). The listed catalog
# carries one-sentence descriptions and flat schemas (ticket C2,
# fb-20261028-131036-6edd; see tool_catalog._admin_catalog).
ADMIN_TOOL_NAMES = frozenset(
    {
        "bridge_status",
        "session_acquire_wait",
        "session_heartbeat",
        "session_release",
        "session_status",
        "query_all_players",
        "player_find",
        "query_player_state",
        "world_time_get",
        "logs_since",
        "wait_for",
        "surface_query",
        "entities_query",
        "notify_players",
        "player_teleport",
        "player_heal",
        "player_godmode",
        "world_time_set",
        "world_weather_set",
        "world_spawn",
        "object_delete",
        "restore_gameplay",
        "server_perf",
    }
)

_TOOL_PACKS: dict[str, frozenset[str] | None] = {
    "full": None,
    "local8b": LOCAL8B_TOOL_NAMES,
    "admin": ADMIN_TOOL_NAMES,
}


def tool_names(pack: str) -> frozenset[str] | None:
    """Return the closed name set, or None for the unfiltered full registry."""
    if not isinstance(pack, str) or pack not in _TOOL_PACKS:
        raise ValueError("invalid tool_pack: expected 'full', 'local8b' or 'admin'")
    return _TOOL_PACKS[pack]


# Public next-step tools under the daemon live profile (B4,
# fb-20261028-131107-9249): the admin pack's session/status core plus the
# public hints whose bridge command the live list allows (object_inspect,
# query_player_state). The dayz_test* lifecycle tools and the knowledge and
# pipeline tools are outside the closed live list, so an error hint under
# that profile never names them. Kept as a literal so this module stays
# import-light; tests/test_c4_next_steps_profile.py pins it against
# loopback.LIVE_ALLOWED_COMMANDS / LIVE_ADMIN_COMMANDS.
LIVE_NEXT_TOOLS = frozenset(
    {
        "bridge_status",
        "session_acquire_wait",
        "session_heartbeat",
        "session_release",
        "session_status",
        "query_player_state",
        "object_inspect",
        "wait_for",
    }
)

_PROFILE_NEXT_TOOLS: dict[str, frozenset[str]] = {
    "test": PUBLIC_NEXT_TOOLS,
    "live": LIVE_NEXT_TOOLS,
    "admin": PUBLIC_NEXT_TOOLS & ADMIN_TOOL_NAMES,
}


def next_step_tools(pack: str) -> frozenset[str]:
    """Public tools a next_step hint may name under one tool pack."""
    allowed = tool_names(pack)
    if allowed is None:
        return PUBLIC_NEXT_TOOLS
    return PUBLIC_NEXT_TOOLS & allowed


def profile_next_tools(profile: str) -> frozenset[str]:
    """Public tools a next_step hint may name under one named profile.

    "test" is the harness profile (full public set), "live" is the B4
    daemon live profile and "admin" is the C1 admin tool pack.
    """
    if not isinstance(profile, str) or profile not in _PROFILE_NEXT_TOOLS:
        raise ValueError("invalid profile: expected 'test', 'live' or 'admin'")
    return _PROFILE_NEXT_TOOLS[profile]


def apply_tool_pack(tool_manager: Any, pack: str) -> frozenset[str] | None:
    """Remove tools outside the selected pack from one FastMCP ToolManager."""
    allowed = tool_names(pack)
    if allowed is None:
        return None
    for tool in tuple(tool_manager.list_tools()):
        # Internal supervisor transport tools are not part of a public pack.
        if str(tool.name).startswith("__dayz_mcp_"):
            continue
        if tool.name not in allowed:
            tool_manager.remove_tool(tool.name)
    return allowed
