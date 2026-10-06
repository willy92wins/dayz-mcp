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

_TOOL_PACKS: dict[str, frozenset[str] | None] = {
    "full": None,
    "local8b": LOCAL8B_TOOL_NAMES,
}


def tool_names(pack: str) -> frozenset[str] | None:
    """Return the closed name set, or None for the unfiltered full registry."""
    if not isinstance(pack, str) or pack not in _TOOL_PACKS:
        raise ValueError("invalid tool_pack: expected 'full' or 'local8b'")
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
