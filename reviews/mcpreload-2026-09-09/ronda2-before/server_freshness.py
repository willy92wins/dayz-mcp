"""Read-only source drift evidence for the process serving MCP tools.

This is a source snapshot, not a bytecode attestation or a reload mechanism.
Capture it at server import, not at the first status request or each app build.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from mcp import types
from mcp.server.fastmcp import FastMCP

REMEDIATION = "reopen_mcp_client"
MARKER = "server_code_freshness"
_TOOLS_ROOT = Path(__file__).resolve().parents[1]


def loaded_source_files() -> dict[str, Path | None]:
    """Watch the loaded package and local tools/ helpers, not third-party code."""
    files: dict[str, Path | None] = {}
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if name == "dayz_mcp" or name.startswith("dayz_mcp."):
            files[name] = Path(path) if isinstance(path, str) else None
        elif name in {"mcp_capture", "knowledge_pack"}:
            # These helpers also implement tools. Keep them covered when a
            # Windows drive alias spells tools/ differently from resolve().
            files[name] = Path(path) if isinstance(path, str) else None
        elif isinstance(path, str) and Path(path).parent == _TOOLS_ROOT:
            files[name] = Path(path)
    return files


def _digest(path: Path | None) -> str | None:
    if path is None or path.suffix != ".py":
        return None
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


class ServerSourceWatch:
    def __init__(self, files: dict[str, Path | None]) -> None:
        self.started_at = time.time()
        self.pid = os.getpid()
        self._files = dict(files)
        self._hashes = {name: _digest(path) for name, path in files.items()}

    def snapshot(self) -> dict[str, Any]:
        # Read every time: metadata-preserving edits and new ACL denies must not
        # silently pass. Callers move these reads off the asyncio event loop.
        current = loaded_source_files()
        files = {**current, **self._files}
        stale: list[str] = []
        unreadable: dict[str, str] = {}
        for name, path in sorted(files.items()):
            if name not in self._files:
                # A late import has no baseline. Reading it now cannot prove
                # which bytes were imported; never silently re-anchor it.
                unreadable[name] = "loaded_after_server_snapshot"
            elif self._hashes[name] is None:
                unreadable[name] = "source_unreadable_at_server_snapshot"
            elif name in current and current[name] != path:
                unreadable[name] = "loaded_module_path_changed"
            else:
                digest = _digest(path)
                if digest is None:
                    unreadable[name] = "source_unreadable_now"
                elif digest != self._hashes[name]:
                    stale.append(name)
        return {
            "server_started_at": self.started_at,
            "server_pid": self.pid,
            "watched_count": len(files),
            "stale": stale,
            "unreadable": sorted(unreadable),
            "unreadable_reasons": unreadable,
        }


def source_stale(snapshot: dict[str, Any]) -> bool | dict[str, Any]:
    """A positive witness decides stale even when another source is unreadable."""
    if snapshot["stale"]:
        return True
    if snapshot["unreadable"] or not snapshot["watched_count"]:
        return {
            "status": "unknown",
            "reason": "unverifiable_server_sources",
            "modules": snapshot["unreadable_reasons"],
        }
    return False


def _call_marker(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any] | None:
    stale = sorted(set(before["stale"]) | set(after["stale"]))
    unreadable = sorted(set(before["unreadable"]) | set(after["unreadable"]))
    if not stale and not unreadable and before["watched_count"] and after["watched_count"]:
        return None
    return {
        "status": "stale" if stale else "unknown",
        "scope": "loaded_server_modules",
        "stale": stale,
        "unreadable": unreadable,
        "unreadable_reasons": {
            **before["unreadable_reasons"], **after["unreadable_reasons"]
        },
        "server_pid": after["server_pid"],
        "server_started_at": after["server_started_at"],
        "remediation": REMEDIATION,
    }


def install_result_freshness(app: FastMCP, watch: ServerSourceWatch) -> None:
    """Annotate the final MCP result, including SDK-generated tool errors.

    Keep outputSchema/structuredContent and original content intact. _meta is
    machine-readable; the extra TextContent is necessary because clients need
    not expose _meta to the model. Direct in-process app.call_tool calls are not
    protocol responses; their enclosing MCP call receives the marker here.
    """
    handlers = app._mcp_server.request_handlers
    original = handlers[types.CallToolRequest]

    async def handler(request: types.CallToolRequest) -> types.ServerResult:
        before = await asyncio.to_thread(watch.snapshot)
        response = await original(request)
        after = await asyncio.to_thread(watch.snapshot)
        marker = _call_marker(before, after)
        result = response.root
        if marker is not None and isinstance(result, types.CallToolResult):
            result.meta = {**(result.meta or {}), MARKER: marker}
            result.content = [
                *result.content,
                types.TextContent(type="text", text=json.dumps({MARKER: marker})),
            ]
        return response

    handlers[types.CallToolRequest] = handler
