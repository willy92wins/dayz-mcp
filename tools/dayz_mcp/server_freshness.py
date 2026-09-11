"""Read-only source drift evidence for the process serving MCP tools.

This is a source snapshot, not a bytecode attestation or a reload mechanism.
Capture it at server import, not at the first status request or each app build.
The sole reloadable runner supplies a receipt for its actual compiled source.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from mcp import types
from mcp.server.fastmcp import FastMCP

from dayz_mcp import playbook_tool

REMEDIATION = "reopen_mcp_client"
MARKER = "server_code_freshness"
_TOOLS_ROOT = Path(__file__).resolve().parents[1]
_PLAYBOOKS_ROOT = _TOOLS_ROOT.parent / "playbooks"


def loaded_source_files() -> dict[str, Path | None]:
    """Watch loaded production sources, including file-loaded playbook code."""
    files: dict[str, Path | None] = {}
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if name == "dayz_mcp" or name.startswith("dayz_mcp."):
            files[name] = Path(path) if isinstance(path, str) else None
        elif name in {"mcp_capture", "knowledge_pack", "dayz_playbook_runner"}:
            # Named helpers remain covered even through Windows drive aliases.
            files[name] = Path(path) if isinstance(path, str) else None
        elif isinstance(path, str) and Path(path).parent in {_TOOLS_ROOT, _PLAYBOOKS_ROOT}:
            files[name] = Path(path)
    return files


def _digest(path: Path | None) -> str | None:
    if path is None or path.suffix != ".py":
        return None
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _stat_identity(path: Path | None) -> tuple[int, int, int] | None:
    if path is None or path.suffix != ".py":
        return None
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size, stat.st_ino
    except OSError:
        return None


class ServerSourceWatch:
    """Hash only when (mtime_ns, size, file_id) changes since the last read.

    Known miss, as in daemon._stale_watched_modules: an edit preserving all
    three fields (or an ACL deny with the same stat) is invisible. A failed
    read is retried; an unreadable initial baseline is never re-anchored.
    """

    def __init__(self, files: dict[str, Path | None]) -> None:
        self.started_at = time.time()
        self.pid = os.getpid()
        self._files = dict(files)
        self._lock = threading.Lock()
        self._identities = {name: _stat_identity(path) for name, path in files.items()}
        self._hashes = {name: _digest(path) for name, path in files.items()}
        for name, path in files.items():
            if self._identities[name] is None or self._identities[name] != _stat_identity(path):
                self._hashes[name] = None
        self._cached_hashes = dict(self._hashes)
        self._runner_observed_source = playbook_tool.runner_source_snapshot()

    def snapshot(self) -> dict[str, Any]:
        # Concurrent to_thread observers must not pair a new identity with an
        # old cached hash. All I/O remains off the asyncio event loop.
        with self._lock:
            return self._snapshot()

    def _snapshot(self) -> dict[str, Any]:
        runner_source = playbook_tool.runner_source_snapshot()
        current = loaded_source_files()
        files = {**current, **self._files}
        stale: list[str] = []
        unreadable: dict[str, str] = {}
        for name, path in sorted(files.items()):
            runner = name == playbook_tool.RUNNER_MODULE
            baseline = self._hashes.get(name)
            if runner:
                if runner_source is None:
                    unreadable[name] = "runner_load_unverified"
                    continue
                if runner_source[1] != path:
                    unreadable[name] = "loaded_module_path_changed"
                    continue
                # Compare against the bytes actually compiled, never a baseline
                # refreshed by the reload tool or by a late observation.
                baseline = runner_source[2]
            if name not in self._files:
                # A late import has no baseline. Reading it now cannot prove
                # which bytes were imported; never silently re-anchor it.
                unreadable[name] = "loaded_after_server_snapshot"
            elif baseline is None:
                unreadable[name] = "source_unreadable_at_server_snapshot"
            elif name in current and current[name] != path:
                unreadable[name] = "loaded_module_path_changed"
            else:
                identity = _stat_identity(path)
                digest = self._cached_hashes[name]
                if identity is None:
                    digest = None
                elif (identity != self._identities[name]
                      or (runner and runner_source is not self._runner_observed_source)):
                    digest = _digest(path)
                    if identity != _stat_identity(path):
                        digest = None
                    if digest is not None:
                        # Cache stale hashes too: the boot hash (or the
                        # runner load receipt) still decides drift.
                        self._identities[name] = identity
                        self._cached_hashes[name] = digest
                        if runner:
                            self._runner_observed_source = runner_source
                if digest is None:
                    unreadable[name] = "source_unreadable_now"
                elif digest != baseline:
                    stale.append(name)
        if (playbook_tool.RUNNER_MODULE in files
                and playbook_tool.runner_source_snapshot() is not runner_source):
            # Do not mix an old receipt with disk observed after a concurrent
            # publication (including a source edit reverted during that window).
            name = playbook_tool.RUNNER_MODULE
            if name in stale:
                stale.remove(name)
            unreadable[name] = "runner_changed_during_observation"
        snapshot = {
            "server_started_at": self.started_at,
            "server_pid": self.pid,
            "watched_count": len(files),
            "stale": stale,
            "unreadable": sorted(unreadable),
            "unreadable_reasons": unreadable,
        }
        snapshot["status"] = _status(snapshot)
        return snapshot


def _status(snapshot: dict[str, Any]) -> str:
    if snapshot.get("observation_errors"):
        return "unknown"
    if snapshot["stale"]:
        return "stale"
    if snapshot["unreadable"] or not snapshot["watched_count"]:
        return "unknown"
    return "fresh"


def source_stale(snapshot: dict[str, Any]) -> bool:
    """Conservative bool: only a verifiably fresh observation returns False."""
    return _status(snapshot) != "fresh"


def schema_signal(snapshot: dict[str, Any]) -> str:
    """Named signal for a stale MCP client (ficha 9b7b).

    fresh stays fresh. A verified content drift is stale_client (reopen the
    client). Unreadable/unknown observations stay unknown so they are not
    collapsed into the boolean source_stale=true bucket.
    """
    status = _status(snapshot)
    if status == "stale":
        return "stale_client"
    return status


def _call_marker(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any] | None:
    stale = sorted(set(before["stale"]) | set(after["stale"]))
    unreadable = sorted(set(before["unreadable"]) | set(after["unreadable"]))
    errors = {**before.get("observation_errors", {}), **after.get("observation_errors", {})}
    if not errors and not stale and not unreadable and before["watched_count"] and after["watched_count"]:
        return None
    return {
        "status": "stale" if stale and not errors else "unknown",
        "scope": "loaded_server_modules",
        "stale": stale,
        "unreadable": unreadable,
        "unreadable_reasons": {
            **before["unreadable_reasons"], **after["unreadable_reasons"]
        },
        "observation_errors": errors,
        "server_pid": after["server_pid"],
        "server_started_at": after["server_started_at"],
        "remediation": REMEDIATION,
    }


def marker_text(marker: dict[str, Any]) -> str:
    """Visible diagnostic, not a second JSON payload to concatenate/parse."""
    reasons = {**marker["unreadable_reasons"], **marker["observation_errors"]}
    detail = "; ".join(f"{name}={reason}" for name, reason in sorted(reasons.items()))
    return (
        f"SERVER_CODE_FRESHNESS {marker['status']}: "
        f"stale={','.join(marker['stale']) or 'none'}; "
        f"unverifiable={detail or 'none'}; remediation={REMEDIATION}"
    )


def install_result_freshness(
    app: FastMCP, watch: ServerSourceWatch,
) -> Callable[[], dict[str, Any]]:
    """Annotate the final MCP result, including SDK-generated tool errors.

    Keep outputSchema/structuredContent and original content intact. _meta is
    machine-readable; the extra TextContent is necessary because clients need
    not expose _meta to the model. Direct in-process app.call_tool calls are not
    protocol responses; their enclosing MCP call receives the marker here.
    """
    installed_server = app._mcp_server
    handlers = installed_server.request_handlers
    original = handlers[types.CallToolRequest]
    result_errors: dict[str, str] = {}

    def observe() -> dict[str, Any]:
        snapshot = watch.snapshot()
        errors = dict(result_errors)
        live_server = getattr(app, "_mcp_server", None)
        live_handlers = getattr(live_server, "request_handlers", {})
        # Read the live surface each time; the captured dictionary can be a fossil.
        if live_server is not installed_server:
            errors["call_tool_hook"] = "mcp_server_replaced"
        elif not isinstance(live_handlers, dict) or live_handlers.get(types.CallToolRequest) is not handler:
            errors["call_tool_hook"] = "call_tool_handler_not_wrapper"
        snapshot["observation_errors"] = errors
        snapshot["status"] = _status(snapshot)
        return snapshot

    async def handler(request: types.CallToolRequest) -> types.ServerResult:
        before = await asyncio.to_thread(observe)
        response = await original(request)
        result = response.root
        if not isinstance(result, types.CallToolResult):
            # An incompatible SDK result must be visible, even on fresh sources.
            # Its domain payload cannot safely be preserved as a CallToolResult.
            result_errors["call_tool_result"] = "unexpected_call_tool_result_type"
            result = types.CallToolResult(
                isError=True,
                content=[types.TextContent(
                    type="text", text="MCP result incompatible with freshness observer.",
                )],
            )
            response = types.ServerResult(result)
        after = await asyncio.to_thread(observe)
        marker = _call_marker(before, after)
        if marker is not None:
            result.meta = {**(result.meta or {}), MARKER: marker}
            result.content = [
                *result.content,
                types.TextContent(type="text", text=marker_text(marker)),
            ]
        return response

    handlers[types.CallToolRequest] = handler
    # bridge_status observes independently of dispatch, exposing a detached
    # wrapper in its ordinary visible payload as well as its conservative bool.
    return observe
