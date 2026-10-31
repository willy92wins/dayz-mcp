"""Small-model loop helpers: next_step may only name public MCP tools.

Flash-Next / ~8B callers copy tool names from error text. Naming an internal
method (lifecycle_status) or a tool that is not in the exposed registry
dead-ends the loop. This module is the single allowlist those recipes use.
"""

from __future__ import annotations

import re

# Tools that exist on the standard public catalog and are safe to name in
# error recipes. Keep this list a subset of build_app() registrations.
PUBLIC_NEXT_TOOLS = frozenset(
    {
        "bridge_status",
        "dayz_knowledge_find",
        "dayz_knowledge_prepare",
        "dayz_knowledge_show",
        "dayz_knowledge_status",
        "dayz_test_close",
        "dayz_test_run",
        "dayz_test_stop",
        "object_inspect",
        "pipeline_feedback",
        "pipeline_inbox",
        "query_player_state",
        "session_acquire_wait",
        "session_heartbeat",
        "session_release",
        "session_status",
        "wait_for",
    }
)


# Named after an ok:true session or mutation result. Values stay inside
# PUBLIC_NEXT_TOOLS so an 8B caller can copy the next tool from the payload.
_OK_NEXT_BY_COMMAND = {
    "lease_acquire": "bridge_status",
    "session_acquire": "bridge_status",
    "session_acquire_wait": "bridge_status",
    "session_cancel": "session_status",
    "session_heartbeat": "bridge_status",
    "session_release": "session_acquire_wait",
    "session_status": "bridge_status",
    "session_wait": "session_status",
}


# When the mapped tool is outside the active profile, an ok:true payload
# falls back to these, in order, before naming nothing at all. Both are in
# every shipped profile (full, local8b, admin, live), so the fallback only
# bites for a caller-supplied allowed set that excludes the mapped tool.
_OK_FALLBACK_TOOLS = ("bridge_status", "session_status")


def next_step(
    tool: str, allowed: frozenset[str] | set[str] | None = None
) -> str | None:
    """Return tool if it is safe to name under the active profile; else None.

    With allowed=None the profile is the full public set (PUBLIC_NEXT_TOOLS),
    which is the pre-C4 behavior. A caller that runs under a closed profile
    (tool pack or live profile) passes that profile's exposed tools, and a
    tool outside it is never named.
    """
    if tool in PUBLIC_NEXT_TOOLS and (allowed is None or tool in allowed):
        return tool
    return None


def ok_next_step(
    command: str,
    *,
    mutating: bool = False,
    allowed: frozenset[str] | set[str] | None = None,
) -> str | None:
    """Public tool to name after an ok:true mutation or session payload."""
    mapped = _OK_NEXT_BY_COMMAND.get(command)
    if mapped is None and mutating:
        mapped = "session_heartbeat"
    if mapped is None:
        return None
    named = next_step(mapped, allowed)
    if named is not None:
        return named
    for fallback in _OK_FALLBACK_TOOLS:
        named = next_step(fallback, allowed)
        if named is not None:
            return named
    return None


_NEXT_STEP_TOKEN_RE = re.compile(r";?\s*next_step=([A-Za-z0-9_]+);?")


def filter_next_step_text(
    message: str, allowed: frozenset[str] | set[str] | None
) -> str:
    """Drop next_step=<tool> tokens the active profile does not expose.

    Error text built at import time (for example the knowledge remedies)
    cannot know the profile a server runs under, so the MCP boundary
    rewrites it on the way out. Allowed tokens pass through unchanged;
    with allowed=None the text is returned as-is.
    """
    if allowed is None:
        return message

    def _keep(match: re.Match[str]) -> str:
        return match.group(0) if match.group(1) in allowed else ""

    return _NEXT_STEP_TOKEN_RE.sub(_keep, message).strip(" ;")


def with_next_step(
    message: str, tool: str, allowed: frozenset[str] | set[str] | None = None
) -> str:
    """Append next_step=<public tool> or leave the message unchanged."""
    name = next_step(tool, allowed)
    if name is None:
        return message
    token = f"next_step={name}"
    if token in message:
        return message
    return f"{message}; {token}"
