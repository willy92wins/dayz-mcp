"""Bridge readiness: the ready/reason verdict, the arg-contract hash, the command census.

compute_bridge_ready and the helpers that build bridge_status's ready envelope,
compare a peer's announced commands with the registered tools, and name the
game_not_ready reason. Moved out of server.py unchanged (backlog 71fc);
server.py imports every name back, so dayz_mcp.server.<name> is the same object.
"""

from __future__ import annotations

from typing import Any

from dayz_mcp.agent_loop import next_step
from dayz_mcp.peer_liveness import (
    PEER_STALE_S,
    client_peer_probeable as _client_peer_probeable,
    peer_is_live as _peer_is_live,
)
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS
from dayz_mcp.tool_catalog import _next_public_call


# Published ready.reason set. The bridge_status description derives its list
# from this set plus _FENCE_BLOCK_READY.values() at build time and declares it
# OPEN: consumers validate by shape, never against a copied whitelist.
# *_legacy_blocked / version_mismatch only after that peer has polled at least
# once (last_poll_age_s is not None).
READY_REASONS = frozenset({
    "ready",
    "no_run",
    "server_poll_stale",
    "client_not_polling",
    "client_legacy_blocked",
    "version_mismatch",
    "arg_contract_mismatch",
    "capabilities_unknown",
    "binding_ambiguous",
    "unbound_after_restart",
    "binding_not_ready",
    "binding_retired",
    "instance_unknown",
    "instance_unattributed",
    "instance_role_mismatch",
    "instance_malformed",
    "instance_peer_collision",
    "legacy_unbound",
    "creation_time_unreadable",
})
# Public tools named when ready is false. Never lifecycle_status.
# OK payloads do not get next_step here.
_READY_NEXT_TOOLS: dict[str, str] = {
    "no_run": "dayz_test_run",
    "binding_not_ready": "bridge_status",
    "server_poll_stale": "bridge_status",
    "client_not_polling": "bridge_status",
    "binding_retired": "session_status",
    "unbound_after_restart": "dayz_test_run",
    "legacy_unbound": "dayz_test_run",
    "instance_unknown": "dayz_test_run",
    "instance_malformed": "dayz_test_run",
    "instance_role_mismatch": "session_status",
    "instance_peer_collision": "session_status",
    "instance_unattributed": "bridge_status",
    "creation_time_unreadable": "bridge_status",
    "binding_ambiguous": "session_status",
    "version_mismatch": "bridge_status",
    "arg_contract_mismatch": "bridge_status",
    "capabilities_unknown": "bridge_status",
    "client_legacy_blocked": "dayz_test_run",
}


_FENCE_BLOCK_READY = {
    "AMBIGUOUS": "binding_ambiguous",
    "STARTING": "binding_not_ready",
    "RETIRED": "binding_retired",
    "binding_retired": "binding_retired",
    "instance_unknown": "instance_unknown",
    "unbound_after_restart": "unbound_after_restart",
    "instance_unattributed": "instance_unattributed",
    "instance_role_mismatch": "instance_role_mismatch",
    "instance_malformed": "instance_malformed",
    "instance_peer_collision": "instance_peer_collision",
    "creation_time_unreadable": "creation_time_unreadable",
}


async def _runtime_client_peer_probeable(runtime: Any) -> bool:
    try:
        status = await runtime.bridge_status_payload()
    except Exception:
        return False
    return _client_peer_probeable(status)


def _finite_poll_age(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def compute_bridge_ready(status: dict[str, Any]) -> dict[str, Any]:
    """Return {ready, reason} for a bridge_status snapshot. Additive field.

    Version reasons are used only when that peer has polled at least once.
    Server liveness is checked before client liveness.

    A BOUND peer with no accredited poll this generation is binding_not_ready,
    not server_poll_stale: leftover last_poll_age_s from a dead pre-launch
    peer must not look like a failed live server (fb-20260917-100411-5edf).
    """
    server = status.get("server_peer") if isinstance(status.get("server_peer"), dict) else {}
    client = status.get("client_peer") if isinstance(status.get("client_peer"), dict) else {}
    s_age = server.get("last_poll_age_s")
    c_age = client.get("last_poll_age_s")
    s_live = _peer_is_live(server)
    c_live = _peer_is_live(client)
    s_state = server.get("version_state")
    c_state = client.get("version_state")
    s_bind = server.get("binding_state")
    c_bind = client.get("binding_state")
    s_block = _FENCE_BLOCK_READY.get(s_bind)
    c_block = _FENCE_BLOCK_READY.get(c_bind)
    if s_block:
        return {"ready": False, "reason": s_block}
    if c_block:
        return {"ready": False, "reason": c_block}
    if s_bind == "LEGACY_UNBOUND" and s_age is not None:
        return {"ready": False, "reason": "legacy_unbound"}
    if c_bind == "LEGACY_UNBOUND" and c_age is not None:
        return {"ready": False, "reason": "legacy_unbound"}
    # 0878: name-only census can green-wash a stale PBO that rejects current
    # tool args (vehicle_prepare_fixture mode/radius). Server ready requires an
    # accredited caps census AND a matching ach. Capability comparison (B1)
    # must run before this so published status sees mismatch/unknown, not raw
    # announced. Raw announced (no comparison yet) still checks ach directly.
    # Unknown / missing / malformed caps: ready=false (B2).
    s_caps = server.get("capabilities") if isinstance(server.get("capabilities"), dict) else {}
    if s_live:
        caps_state = s_caps.get("state")
        if caps_state == "match":
            pass
        elif caps_state == "announced":
            ach = s_caps.get("announced_arg_contract_hash")
            if (
                not isinstance(ach, str)
                or ach == ""
                or ach != EXPECTED_SERVER_ARG_CONTRACT_HASH
            ):
                return {"ready": False, "reason": "arg_contract_mismatch"}
        elif caps_state == "mismatch":
            # Fail-closed on arg-contract hash independently of the primary
            # compare reason. Census disagreement used to hide absent/wrong ach
            # (B2 residual): missing census cmd + bad/absent ach must not
            # green-wash ready=true. Matching ach + census-only mismatch keeps
            # historical ready behavior.
            if s_caps.get("reason") == "arg_contract_mismatch":
                return {"ready": False, "reason": "arg_contract_mismatch"}
            ach = s_caps.get("announced_arg_contract_hash")
            if (
                not isinstance(ach, str)
                or ach == ""
                or ach != EXPECTED_SERVER_ARG_CONTRACT_HASH
            ):
                return {"ready": False, "reason": "arg_contract_mismatch"}
            # Name-census disagreement with matching ach: historical ready.
            pass
        else:
            return {"ready": False, "reason": "capabilities_unknown"}
    if s_live and c_live and s_state == "ok" and c_state == "ok":
        return {"ready": True, "reason": "ready"}
    if s_bind == "BOUND" and not _finite_poll_age(server.get("bound_last_poll_age_s")):
        return {"ready": False, "reason": "binding_not_ready"}
    if c_bind == "BOUND" and not _finite_poll_age(client.get("bound_last_poll_age_s")):
        return {"ready": False, "reason": "binding_not_ready"}
    if s_age is None and c_age is None:
        return {"ready": False, "reason": "no_run"}
    if not s_live:
        return {"ready": False, "reason": "server_poll_stale"}
    if not c_live:
        return {"ready": False, "reason": "client_not_polling"}
    if c_age is not None and c_state == "legacy_blocked":
        return {"ready": False, "reason": "client_legacy_blocked"}
    if (
        (s_age is not None and s_state in {"version_mismatch", "legacy_blocked"})
        or (c_age is not None and c_state == "version_mismatch")
    ):
        return {"ready": False, "reason": "version_mismatch"}
    if s_state != "ok" or c_state != "ok":
        return {"ready": False, "reason": "version_mismatch"}
    return {"ready": False, "reason": "no_run"}


_BRIDGE_WORLD_READ_COMMANDS = READ_ONLY_COMMANDS - {"logs_since"}


def _world_read_not_ready(
    runtime: Any, cmd: str, status: dict[str, Any]
) -> dict[str, Any] | None:
    """Return the short fail-fast envelope for bridge world reads, or None."""
    if cmd not in _BRIDGE_WORLD_READ_COMMANDS:
        return None
    verdict = compute_bridge_ready(status)
    if verdict["ready"]:
        return None
    reason = str(verdict["reason"])
    return {
        "ok": False,
        "error": f"game_not_ready:reason={reason}",
        "code": "not_ready",
        "reason": reason,
        "next_step": _next_public_call(runtime, "bridge_status"),
    }


def _has_ready_snapshot_shape(status: object) -> bool:
    """True when a client-fetched status has both readiness inputs."""
    if not isinstance(status, dict):
        return False
    for key in ("server_peer", "client_peer"):
        peer = status.get(key)
        if not isinstance(peer, dict):
            return False
        if "last_poll_age_s" not in peer or "version_state" not in peer:
            return False
    return True


# Arg-contract fingerprint (fb-20260924-235528-0878). Command-name census alone
# cannot see a PBO that still lists vehicle_prepare_fixture but rejects the
# tool's mode=/radius= shape. Both sides ship the same 16-hex SHA-256 prefix of
# the canonical form below; the PBO announces it as poll `ach=` and
# `_compare_bridge_capabilities` fails closed on absent/wrong values for the
# server peer. Client peer has no ach gate yet.
SERVER_ARG_CONTRACT: dict[str, tuple[str, ...]] = {
    "vehicle_prepare_fixture": ("mode", "pos", "radius", "type"),
}


def server_arg_contract_canonical() -> str:
    """Stable, newline-joined `cmd=k1,k2` lines (keys sorted, cmds sorted)."""

    return "\n".join(
        f"{cmd}={','.join(keys)}"
        for cmd, keys in sorted(SERVER_ARG_CONTRACT.items())
    )


def server_arg_contract_hash() -> str:
    """First 16 hex chars of sha256(canonical). Must match MCPBridge.c."""

    import hashlib

    return hashlib.sha256(server_arg_contract_canonical().encode("utf-8")).hexdigest()[:16]


EXPECTED_SERVER_ARG_CONTRACT_HASH = server_arg_contract_hash()


# peer + command -> the public tool that fronts it, or None when the command is
# deliberately not exposed. Hand written from the two Enforce dispatchers
# (MCPBridge.c SERVER_CAPABILITIES, MCPClientBridge.c CLIENT_POLL_CAPS).
#
# Explicitly NOT derived from app.list_tools(), from loopback's command lists or
# from the PBO. The census exists so it CAN disagree with what the daemon
# registers; a table derived from either side would agree by construction and
# detect nothing. A command the bridge announces and this table does not know is
# reported as unmapped rather than silently accepted -- that is the case a new
# command shipped in the PBO produces, and it should be visible on the first
# poll instead of on the first failed call.
_BRIDGE_COMMAND_TOOLS: dict[str, dict[str, str | None]] = {
    "server": {
        "entities_query": "entities_query",
        "exec_enforce": None,  # not a public tool by decision
        "hands_take": "hands_take",
        "infected_drive": "infected_drive",
        "inventory_attach": "inventory_attach",
        "inventory_give": "inventory_give",
        "notify_players": "notify_players",
        "object_anim": "object_anim",
        "object_delete": "object_delete",
        "object_doors": "object_doors",
        "object_inspect": "object_inspect",
        "player_godmode": "player_godmode",
        "player_heal": "player_heal",
        "player_teleport": "player_teleport",
        "query_all_players": "query_all_players",
        "query_get_in_condition": "query_get_in_condition",
        "query_player_state": "query_player_state",
        "scene_raycast": "scene_raycast",
        "surface_query": "surface_query",
        "telemetry_read": "telemetry_read",
        "vehicle_door": "vehicle_door",
        "vehicle_enter": "vehicle_enter",
        "vehicle_prepare_fixture": "vehicle_prepare_fixture",
        "weapon_state": "weapon_state",
        "world_spawn": "world_spawn",
        "world_time_get": "world_time_get",
        "world_time_set": "world_time_set",
        "world_weather_set": "world_weather_set",
    },
    "client": {
        "action_use": "action_use",
        "action_use_component": "action_use",
        "action_use_door": "action_use",
        "action_use_target": "action_use",
        "anim_timeline": "anim_timeline",
        "camera_get": "camera_get",
        "camera_set": "camera_set",
        "engine_set": "engine_set",
        "input_describe": "input_describe",
        "input_trigger": "input_trigger",
        "key_press": "key_press",
        "player_move": "player_move",
        "player_respawn": "player_respawn",
        "player_trace": "player_trace",
        "restore_gameplay": "restore_gameplay",
        "ui_click": "ui_click",
        "ui_dialog": "ui_dialog",
        "ui_focus": "ui_focus",
        "ui_reload_layout": "ui_reload_layout",
        "ui_set_text": "ui_set_text",
        "ui_tree": "ui_tree",
        "vehicle_control": "vehicle_control",
        "vehicle_get_in_client": "vehicle_get_in_client",
        "vehicle_release": "vehicle_release",
        "vehicle_telemetry": "vehicle_telemetry",
        "vehicle_trace": "vehicle_trace",
        "weapon_aim": "weapon_aim",
        "weapon_fire": "weapon_fire",
        "weapon_raise": "weapon_raise",
        "weapon_sights": "weapon_sights",
    },
}


def _compare_bridge_capabilities(
    peer: str,
    capabilities: object,
    registered_tools: frozenset[str],
    intended_tools: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Cross one peer's announced census against the registered tools.

    Three verdicts and never a fourth: ``match`` when every mapped command has
    its tool and every tool has its command, ``mismatch`` when they disagree --
    naming exactly which commands -- and ``unknown`` when there is no census to
    judge. ``unknown`` is not a mismatch: an absent, malformed or unaccredited
    announcement means we did not look, and saying otherwise would put a red on
    a bridge that may be perfectly fine.
    """

    block = capabilities if isinstance(capabilities, dict) else {}
    mapping = _BRIDGE_COMMAND_TOOLS.get(peer, {})
    expected_tools = {tool for tool in mapping.values() if tool}
    intended_bridge_tools = (
        expected_tools
        if intended_tools is None
        else expected_tools & set(intended_tools)
    )
    registered_bridge_tools = sorted(intended_bridge_tools & registered_tools)
    announced = block.get("announced_commands")
    if block.get("state") != "announced" or not isinstance(announced, list):
        return {
            "state": "unknown",
            "reason": str(block.get("reason") or "absent"),
            "announced_commands": [],
            "registered_bridge_tools": registered_bridge_tools,
            "announced_without_registered_tool": [],
            "registered_without_announced_command": [],
            "unmapped_announced_commands": [],
            "expected_arg_contract_hash": (
                EXPECTED_SERVER_ARG_CONTRACT_HASH if peer == "server" else None
            ),
            "announced_arg_contract_hash": None,
        }
    announced_set = {item for item in announced if isinstance(item, str)}
    unmapped = sorted(item for item in announced_set if item not in mapping)
    missing_tool = sorted(
        item
        for item in announced_set
        if mapping.get(item) in intended_bridge_tools
        and mapping[item] not in registered_tools
    )
    announced_tools = {mapping[item] for item in announced_set if mapping.get(item)}
    not_announced = sorted(
        tool for tool in registered_bridge_tools if tool not in announced_tools
    )
    agrees = not (unmapped or missing_tool or not_announced)
    announced_hash = block.get("announced_arg_contract_hash")
    expected_hash = (
        EXPECTED_SERVER_ARG_CONTRACT_HASH if peer == "server" else None
    )
    arg_contract_ok = True
    arg_reason = "ok"
    if expected_hash is not None:
        if not isinstance(announced_hash, str) or announced_hash == "":
            arg_contract_ok = False
            arg_reason = "arg_contract_mismatch"
            announced_hash = None
        elif announced_hash != expected_hash:
            arg_contract_ok = False
            arg_reason = "arg_contract_mismatch"
    # Prefer arg_contract_mismatch when both census and ach fail so the
    # fail-closed gate is not hidden behind census_disagrees (B2 residual).
    # Census-only disagreement keeps its historical reason; details remain in
    # announced_without_registered_tool / registered_without_announced_command.
    if not arg_contract_ok:
        state = "mismatch"
        reason = arg_reason
    elif not agrees:
        state = "mismatch"
        reason = "census_disagrees_with_registered_tools"
    else:
        state = "match"
        reason = "ok"
    return {
        "state": state,
        "reason": reason,
        "announced_commands": sorted(announced_set),
        "registered_bridge_tools": registered_bridge_tools,
        "announced_without_registered_tool": missing_tool,
        "registered_without_announced_command": not_announced,
        "unmapped_announced_commands": unmapped,
        "expected_arg_contract_hash": expected_hash,
        "announced_arg_contract_hash": announced_hash if isinstance(announced_hash, str) else None,
    }


def _with_capability_comparison(
    payload: dict[str, Any],
    registered_tools: frozenset[str],
    intended_tools: frozenset[str] | None = None,
) -> dict[str, Any]:
    enriched = dict(payload)
    for peer, key in (("server", "server_peer"), ("client", "client_peer")):
        block = enriched.get(key)
        if not isinstance(block, dict):
            continue
        block = dict(block)
        block["capabilities"] = _compare_bridge_capabilities(
            peer,
            block.get("capabilities"),
            registered_tools,
            intended_tools,
        )
        enriched[key] = block
    return enriched


def _client_peer_announces_command(status: object, command: str) -> bool:
    if not isinstance(status, dict):
        return False
    client_peer = status.get("client_peer")
    if not isinstance(client_peer, dict):
        return False
    capabilities = client_peer.get("capabilities")
    if not isinstance(capabilities, dict):
        return False
    if capabilities.get("state") != "announced":
        return False
    announced = capabilities.get("announced_commands")
    if not isinstance(announced, list):
        return False
    return command in announced


def _front_key(payload: dict[str, Any], key: str) -> dict[str, Any]:
    """Put key first so an 8B scanner sees the verdict before the rest of the blob."""
    if key not in payload:
        return dict(payload)
    ordered: dict[str, Any] = {key: payload[key]}
    for name, value in payload.items():
        if name != key:
            ordered[name] = value
    return ordered


def _ready_next_tool(reason: str, *, is_ready: bool) -> str | None:
    if is_ready or reason == "ready":
        return None
    # Uniform ready=false envelope (fb-20260917-092908-1765 / baf9):
    # always name bridge_status, never a per-reason fork.
    return next_step("bridge_status")


def _with_ready(status: dict[str, Any]) -> dict[str, Any]:
    payload = dict(status)
    verdict = compute_bridge_ready(payload)
    server = payload.get("server_peer") if isinstance(payload.get("server_peer"), dict) else {}
    client = payload.get("client_peer") if isinstance(payload.get("client_peer"), dict) else {}
    is_ready = bool(verdict["ready"])
    reason = str(verdict["reason"])
    # ready/reason/next_step first so an 8B scanner sees the verdict before ages.
    ready: dict[str, Any] = {
        "ready": is_ready,
        "reason": reason,
    }
    follow = _ready_next_tool(reason, is_ready=is_ready)
    if follow is not None:
        ready["next_step"] = follow
    ready["stale_threshold_s"] = PEER_STALE_S
    ready["server_last_poll_age_s"] = server.get("last_poll_age_s")
    ready["client_last_poll_age_s"] = client.get("last_poll_age_s")
    ready["server_bound_last_poll_age_s"] = server.get("bound_last_poll_age_s")
    ready["client_bound_last_poll_age_s"] = client.get("bound_last_poll_age_s")
    payload["ready"] = ready
    return _front_key(payload, "ready")


def _game_not_ready_reason(
    status: dict[str, Any] | None,
    peer: str | None = None,
) -> str:
    if isinstance(status, dict) and peer in {"server", "client"}:
        key = "client_peer" if peer == "client" else "server_peer"
        if not _peer_is_live(status.get(key)):
            return "client_not_polling" if peer == "client" else "server_poll_stale"
    if not isinstance(status, dict):
        return "no_run"
    reason = compute_bridge_ready(status)["reason"]
    if reason == "ready":
        return "no_run"
    return str(reason)


def _target_peer_down(
    status_snapshot: dict[str, Any] | None,
    peer: str | None,
) -> bool:
    """True when the command's target peer is not live (same rule as embedded)."""
    if not isinstance(status_snapshot, dict):
        return False
    if peer in {"server", "client"}:
        key = "client_peer" if peer == "client" else "server_peer"
        return not _peer_is_live(status_snapshot.get(key))
    return not _peer_is_live(status_snapshot.get("server_peer")) and not _peer_is_live(
        status_snapshot.get("client_peer")
    )
