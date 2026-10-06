"""Advisory lease TTL on MCP tool results.

The number is the daemon's expires_in_s for the caller's own active lease,
including the operation pin that status already folds in. It authorizes
nothing. Observation uses ControlClient.session_status and does not acquire,
heartbeat, reconcile, or spawn a daemon.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
from typing import Any

from mcp import types


LEASE_TTL_OBSERVE_S = 0.75
# Worker tool the supervisor calls. It reads ControlClient.session_status and
# does not lazy-spawn. Hidden from the public catalog.
LEASE_TTL_OBSERVE_TOOL = "__dayz_mcp_lease_ttl_status__"
LEASE_LOCAL_TOOL = "__dayz_mcp_lease_local__"
_VISIBLE = "lease_ttl_s"


def classify_status(status: object, local_lease_id: str | None) -> dict[str, Any] | None:
    """Map one session_status body to the result annotation, or None to omit it.

    A stored token is not ownership. The caller's self lease id and the owner
    lease id must be the same id, and when the caller already knows its local
    lease id that id must match too. Another owner's expires_in_s is never used.
    """

    if not isinstance(local_lease_id, str) or not local_lease_id:
        local_lease_id = None
    if not isinstance(status, dict):
        return _unknown() if local_lease_id is not None else None
    self_row = status.get("self")
    owner = status.get("owner")
    self_row = self_row if isinstance(self_row, dict) else {}
    owner = owner if isinstance(owner, dict) else {}
    self_state = self_row.get("state")
    self_id = self_row.get("lease_id") if self_state == "active" else None
    owner_state = owner.get("state")
    owner_id = owner.get("lease_id") if owner_state in (None, "active") else None
    ttl = owner.get("expires_in_s")
    confirmed = (
        isinstance(self_id, str)
        and self_id != ""
        and self_id == owner_id
        and (local_lease_id is None or local_lease_id == self_id)
        and isinstance(ttl, (int, float))
        and not isinstance(ttl, bool)
        and math.isfinite(float(ttl))
        and float(ttl) >= 0.0
    )
    if confirmed:
        return {"lease_ttl_s": float(ttl)}
    if local_lease_id is None:
        return None
    # A successful status whose caller is not active is confirmed absence,
    # whether or not some other session owns the box. A stale local id must
    # not turn that into "unknown", and must not copy the other owner's TTL.
    if "self" in status and self_state != "active":
        return None
    return _unknown()


def _unknown() -> dict[str, Any]:
    return {"lease_ttl_s": None, "lease_ttl_status": "unknown"}


def visible_text(annotation: dict[str, Any]) -> str:
    if annotation.get("lease_ttl_status") == "unknown" or annotation.get("lease_ttl_s") is None:
        return f"{_VISIBLE}=unknown"
    return f"{_VISIBLE}={float(annotation['lease_ttl_s']):.3f}"


def parse_tool_status(message: object) -> dict[str, Any] | None:
    """Pull a session_status object out of a worker JSON-RPC response."""

    if not isinstance(message, dict):
        return None
    result = message.get("result")
    if not isinstance(result, dict):
        return None
    if result.get("isError") is True:
        return None
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    # A bare status object (tests, and a worker that answered the dict itself).
    if "self" in result or "owner" in result:
        return result
    content = result.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = block.get("text")
            if not isinstance(text, str):
                continue
            try:
                parsed = json.loads(text)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return None


def apply_call_tool_result(result: types.CallToolResult, annotation: dict[str, Any]) -> None:
    """Attach the annotation without dropping image bytes, errors, or metadata."""

    meta = dict(result.meta or {})
    meta["lease_ttl_s"] = annotation.get("lease_ttl_s")
    if annotation.get("lease_ttl_status") == "unknown":
        meta["lease_ttl_status"] = "unknown"
    result.meta = meta
    if not result.isError:
        structured = result.structuredContent
        if isinstance(structured, dict):
            updated = dict(structured)
            updated["lease_ttl_s"] = annotation.get("lease_ttl_s")
            if annotation.get("lease_ttl_status") == "unknown":
                updated["lease_ttl_status"] = "unknown"
            result.structuredContent = updated
        for block in result.content:
            if getattr(block, "type", None) != "text":
                continue
            text = getattr(block, "text", None)
            if not isinstance(text, str):
                continue
            try:
                parsed = json.loads(text)
            except ValueError:
                continue
            if not isinstance(parsed, dict):
                continue
            parsed = dict(parsed)
            parsed["lease_ttl_s"] = annotation.get("lease_ttl_s")
            if annotation.get("lease_ttl_status") == "unknown":
                parsed["lease_ttl_status"] = "unknown"
            block.text = json.dumps(parsed)
            break
    result.content = [
        *result.content,
        types.TextContent(type="text", text=visible_text(annotation)),
    ]


def _request_tool_name(request: object) -> str | None:
    params = getattr(request, "params", None)
    name = getattr(params, "name", None)
    return name if isinstance(name, str) else None


def is_internal_probe(request: object) -> bool:
    """Tools whose body is an ownership probe. Their result must not be decorated."""

    return _request_tool_name(request) in (LEASE_LOCAL_TOOL, LEASE_TTL_OBSERVE_TOOL)


def _probe_result(payload: dict[str, Any], *, is_error: bool = False) -> types.ServerResult:
    return types.ServerResult(
        types.CallToolResult(
            isError=is_error,
            structuredContent=None if is_error else payload,
            content=[types.TextContent(type="text", text=json.dumps(payload))],
        )
    )


async def _read_session_status(runtime: object) -> object:
    """One bounded ControlClient.session_status read. Does not spawn or renew."""

    control = getattr(runtime, "_control", None)
    status_fn = getattr(control, "session_status", None)
    if not callable(status_fn):
        return None
    try:
        status = status_fn(timeout_s=LEASE_TTL_OBSERVE_S)
    except TypeError:
        status = status_fn()
    if inspect.isawaitable(status):
        status = await status
    return status


def _current_local_lease_id(runtime: object) -> str | None:
    control = getattr(runtime, "_control", None)
    lease_id = getattr(control, "active_lease_id", None)
    if not isinstance(lease_id, str) or not lease_id:
        return None
    return lease_id


async def answer_internal_probe(runtime: object, request: object) -> types.ServerResult:
    """Answer a probe with no await after the value the supervisor will trust.

    The lease-TTL decorator used to await session_status after the local probe
    had already copied active_lease_id into its body. A release during that
    await left the copied id stale. The status read now happens first, and the
    id in the response is read after it returns.
    """

    name = _request_tool_name(request)
    if name == LEASE_TTL_OBSERVE_TOOL:
        try:
            status = await asyncio.wait_for(
                _read_session_status(runtime), timeout=LEASE_TTL_OBSERVE_S + 0.25
            )
        except Exception:
            status = None
        if not isinstance(status, dict):
            return _probe_result({"error": "daemon_unavailable"}, is_error=True)
        return _probe_result(status)
    try:
        await asyncio.wait_for(
            _read_session_status(runtime), timeout=LEASE_TTL_OBSERVE_S + 0.25
        )
    except Exception:
        pass
    return _probe_result({"local_lease_id": _current_local_lease_id(runtime)})


async def observe_caller_ttl(runtime: object) -> dict[str, Any] | None:
    """TTL for the lease this process holds, or None when it holds none.

    A failed observation while the local lease id is still the one we started
    with is unknown. A lease id that changes during the read is not reported:
    that would publish a lease this result did not hold.
    """

    control = getattr(runtime, "_control", None)
    if control is None:
        return None
    local_id = getattr(control, "active_lease_id", None)
    if not isinstance(local_id, str) or not local_id:
        return None
    status_fn = getattr(control, "session_status", None)
    if not callable(status_fn):
        return _unknown()
    try:
        status = await asyncio.wait_for(
            status_fn(timeout_s=LEASE_TTL_OBSERVE_S),
            timeout=LEASE_TTL_OBSERVE_S + 0.25,
        )
    except TypeError:
        try:
            status = await asyncio.wait_for(
                status_fn(), timeout=LEASE_TTL_OBSERVE_S + 0.25
            )
        except Exception:
            if getattr(control, "active_lease_id", None) != local_id:
                return None
            return _unknown()
    except Exception:
        if getattr(control, "active_lease_id", None) != local_id:
            return None
        return _unknown()
    if getattr(control, "active_lease_id", None) != local_id:
        return None
    return classify_status(status, local_id)


def install_lease_ttl_annotation(app: Any, runtime: object) -> None:
    """Decorate CallToolResult. Install before the freshness wrapper."""

    installed = app._mcp_server
    handlers = installed.request_handlers
    original = handlers[types.CallToolRequest]

    async def handler(request: types.CallToolRequest) -> types.ServerResult:
        # Probes skip this decorator and the SDK tool body. The snapshot is the
        # last step of answer_internal_probe, with no await between it and the
        # response this handler returns.
        if is_internal_probe(request):
            return await answer_internal_probe(runtime, request)
        response = await original(request)
        result = response.root
        if not isinstance(result, types.CallToolResult):
            return response
        annotation = await observe_caller_ttl(runtime)
        if annotation is not None:
            apply_call_tool_result(result, annotation)
        return response

    handlers[types.CallToolRequest] = handler


def render_supervisor_result(
    payload: dict[str, Any], annotation: dict[str, Any] | None, *, is_error: bool
) -> dict[str, Any]:
    """JSON-RPC tools/call result the supervisor synthesizes itself."""

    shown = dict(payload)
    if annotation is not None and not is_error:
        shown["lease_ttl_s"] = annotation.get("lease_ttl_s")
        if annotation.get("lease_ttl_status") == "unknown":
            shown["lease_ttl_status"] = "unknown"
    content: list[dict[str, Any]] = [
        {"type": "text", "text": json.dumps(shown)},
    ]
    result: dict[str, Any] = {"content": content, "isError": is_error}
    if annotation is None:
        return result
    if not is_error:
        result["structuredContent"] = dict(shown)
    content.append({"type": "text", "text": visible_text(annotation)})
    meta: dict[str, Any] = {"lease_ttl_s": annotation.get("lease_ttl_s")}
    if annotation.get("lease_ttl_status") == "unknown":
        meta["lease_ttl_status"] = "unknown"
    result["_meta"] = meta
    return result
