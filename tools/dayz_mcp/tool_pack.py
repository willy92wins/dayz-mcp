"""Opt-in MCP tool packs.

The full registry remains the default. Packs are closed groups applied once,
after build_app has registered and patched every tool.
"""

from __future__ import annotations

from typing import Any


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
# set of 22 tools for operating a live server with a small local model:
# session/lease handling, lease-free reads, player warning, teleport and
# heal, time/weather, spawn/remove and restore. The same set is listed
# before and after the lease is taken (see tool_catalog: progressive
# disclosure is disabled for this pack), so a host that never re-lists
# after tools/list_changed still sees every verb. This is a catalog, not
# access control: enforcement is ticket B4. Sizing assumes decision D7
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
