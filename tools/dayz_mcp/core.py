"""Shared bridge policy helpers: version gate, status assembly, exec config.

Extracted from ``server.Runtime`` so the standalone daemon and the embedded
runtime share ONE source of truth for the version gate (R6 fail-closed), the
``/status`` payload shape, and the exec allowlist/audit. The broker split (daemon
owns the loopback, sessions proxy as clients) requires this policy to live where
both processes can import it, not buried in the embedded Runtime.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


EXPECTED_BRIDGE_VERSION = "7"

# Peer version_state values that must block command delivery / enqueue.
BLOCKED_VERSION_STATES = {"legacy_blocked", "version_mismatch"}


def version_state_for(
    version: str | None,
    *,
    require_version: bool,
    expected_game_version: str | None,
    expected_bridge_version: str = EXPECTED_BRIDGE_VERSION,
) -> tuple[str, str]:
    """Classify a peer poll ``ver=`` string into (state, detail).

    Verbatim from the former ``Runtime._version_state_for`` so the daemon and the
    embedded runtime classify identically.
    """
    if version is None:
        if require_version:
            return "legacy_blocked", "poll did not include ver="
        return "legacy", "poll did not include ver="

    bridge_version, sep, game_version = version.partition("~")
    if not sep:
        return "version_mismatch", "ver= must be <bridge_version>~<game_version>"
    if bridge_version != expected_bridge_version:
        return "version_mismatch", f"bridge_version {bridge_version!r} != {expected_bridge_version!r}"
    if expected_game_version is not None and game_version != expected_game_version:
        return "version_mismatch", f"game_version {game_version!r} != {expected_game_version!r}"
    return "ok", "version accepted"


def _peer_status(
    snapshot: dict[str, Any],
    peer: str,
    *,
    require_version: bool,
    expected_game_version: str | None,
    expected_bridge_version: str,
) -> dict[str, Any]:
    peer_snapshot = snapshot["peers"][peer]
    version = peer_snapshot.get("version")
    state, detail = version_state_for(
        version,
        require_version=require_version,
        expected_game_version=expected_game_version,
        expected_bridge_version=expected_bridge_version,
    )
    return {
        "last_poll_age_s": peer_snapshot.get("last_poll_age_s"),
        "queue_depth": peer_snapshot.get("queue_depth", 0),
        "version": version,
        "version_state": state,
        "version_detail": detail,
    }


def build_status(
    snapshot: dict[str, Any],
    *,
    require_version: bool,
    expected_game_version: str | None,
    expected_bridge_version: str = EXPECTED_BRIDGE_VERSION,
) -> dict[str, Any]:
    """Assemble the rich status dict (per-peer version_state + metadata) from a raw
    ``ServerState.status_snapshot()``. Shared by ``Runtime.status()`` and the
    daemon ``/status`` endpoint so embedded and client ``bridge_status`` agree.
    """
    server_peer = _peer_status(
        snapshot,
        "server",
        require_version=require_version,
        expected_game_version=expected_game_version,
        expected_bridge_version=expected_bridge_version,
    )
    client_peer = _peer_status(
        snapshot,
        "client",
        require_version=require_version,
        expected_game_version=expected_game_version,
        expected_bridge_version=expected_bridge_version,
    )
    return {
        "server_peer": server_peer,
        "client_peer": client_peer,
        "results_pending": snapshot["results_pending"],
        "version_state": {
            "server": server_peer["version_state"],
            "client": client_peer["version_state"],
        },
        "server_version": expected_bridge_version,
        "expected_game_version": expected_game_version,
        "require_version": require_version,
    }


def load_exec_allowlist(path: str | None) -> set[str]:
    """Load the exec allowlist JSON. ``utf-8-sig`` tolerates a BOM that Windows
    editors prepend, which plain utf-8 choked on at startup (GATE4B-002)."""
    if path is None:
        return set()
    with open(path, "r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise ValueError("--exec-allowlist must be a JSON list of strings")
    return set(payload)


def make_exec_auditor(audit_path: Path) -> Callable[[str, str, str, int | None], None]:
    """Return an ExecAudit callable that appends a JSONL audit entry. Matches the
    loopback ``ExecAudit`` signature ``(expr, verdict, main_fn, command_id)``."""

    def audit(expr: str, verdict: str, main_fn: str = "", command_id: int | None = None) -> None:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        entry: dict[str, Any] = {
            "ts_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "expr": expr,
            "main_fn": main_fn,
            "verdict": verdict,
        }
        if command_id is not None:
            entry["command_id"] = command_id
        with audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, separators=(",", ":")) + "\n")

    return audit
