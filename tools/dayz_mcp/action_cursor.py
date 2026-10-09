"""Normalize an action_cursor snapshot and correlate network pairs to registry ids.

The client bridge observes the HUD cursor. This module decides the public
contract: slot names, null widgets, component -1, freshness versus coherence,
and object_id only by an exact network pair inside the same run. It does not
infer an id from classname, distance or index.
"""

from __future__ import annotations

import math
import time
import uuid
from typing import Any

SCHEMA_VERSION = 1
SLOT_NAMES = (
    "primary",
    "secondary",
    "continuous_primary",
    "continuous_secondary",
)
WIDGET_NAMES = (
    "root",
    "item",
    "item_desc",
    "item_flag_icon",
    "primary",
    "secondary",
    "continuous_primary",
    "continuous_secondary",
)
_CURSOR_ERRORS = frozenset(
    {
        "no_player",
        "no_action_manager",
        "cursor_unavailable",
        "snapshot_invalidated",
        "bad_cursor_result",
    }
)


class CursorContractError(Exception):
    def __init__(self, code: str) -> None:
        if code not in _CURSOR_ERRORS and code != "bad_cursor_result":
            code = "bad_cursor_result"
        self.code = code
        super().__init__(code)


_FENCE_DRIFT = frozenset(
    {"binding_changed", "broker_infrastructure_missing", "bad_binding_fence"}
)


def binding_fence(status: object, peers: tuple[str, ...]) -> dict[str, Any] | None:
    """Complete generation and binding tokens for the peers this operation uses.

    A missing generation, run id, or token is incomplete. An instance prefix
    is never a substitute.
    """
    if not isinstance(status, dict) or not peers:
        return None
    generation = status.get("daemon_generation")
    if not isinstance(generation, str) or generation == "":
        return None
    built: dict[str, dict[str, str]] = {}
    for peer in peers:
        block = status.get(f"{peer}_peer")
        if not isinstance(block, dict):
            return None
        run_id = block.get("run_id")
        token = block.get("binding_token")
        if (
            not isinstance(run_id, str)
            or run_id == ""
            or not isinstance(token, str)
            or token == ""
        ):
            return None
        built[peer] = {"run_id": run_id, "binding_token": token}
    return {"generation": generation, "peers": built}


def fence_provenance(fence: object) -> tuple[object, object, object]:
    """Run id, daemon generation, and the client binding token."""
    if isinstance(fence, tuple) and len(fence) == 4:
        return fence[0], fence[1], fence[2]
    if not isinstance(fence, dict):
        raise CursorContractError("snapshot_invalidated")
    peers = fence.get("peers")
    client = peers.get("client") if isinstance(peers, dict) else None
    if not isinstance(client, dict):
        raise CursorContractError("snapshot_invalidated")
    return client.get("run_id"), fence.get("generation"), client.get("binding_token")


async def call_bridge_fenced(
    runtime: Any,
    cmd: str,
    args: dict[str, Any],
    peer: str,
    timeout_s: float,
    fence: dict[str, Any],
) -> dict[str, Any]:
    """Send one fenced bridge call. A fence the broker rejects is a new session."""
    from mcp.server.fastmcp.exceptions import ToolError

    try:
        raw = await runtime.call_bridge(
            cmd, args, peer, timeout_s, expected_fence=fence
        )
    except ToolError as exc:
        if str(exc) in _FENCE_DRIFT:
            raise ToolError("snapshot_invalidated") from exc
        raise
    return raw


def peer_announces(status: object, peer: str, command: str) -> bool:
    if not isinstance(status, dict):
        return False
    block = status.get(peer)
    if not isinstance(block, dict):
        return False
    capabilities = block.get("capabilities")
    if not isinstance(capabilities, dict) or capabilities.get("state") != "announced":
        return False
    announced = capabilities.get("announced_commands")
    return isinstance(announced, list) and command in announced


def _finite_vec3(value: object) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise CursorContractError("bad_cursor_result")
    out: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise CursorContractError("bad_cursor_result")
        number = float(item)
        if not math.isfinite(number):
            raise CursorContractError("bad_cursor_result")
        out.append(number)
    return out


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    raise CursorContractError("bad_cursor_result")


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CursorContractError("bad_cursor_result")
    return value


def _ref(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or "present" not in value:
        raise CursorContractError("bad_cursor_result")
    if _bool(value.get("present")) is not True:
        return None
    classname = value.get("classname")
    if not isinstance(classname, str) or classname == "":
        raise CursorContractError("bad_cursor_result")
    return {
        "classname": classname,
        "pos": _finite_vec3(value.get("pos")),
        "net_low": _int(value.get("net_low")),
        "net_high": _int(value.get("net_high")),
        "object_id": None,
    }


def _target(value: object) -> tuple[bool, dict[str, Any] | None]:
    if not isinstance(value, dict) or "present" not in value:
        raise CursorContractError("bad_cursor_result")
    if "object_ref" not in value or "parent_ref" not in value:
        raise CursorContractError("bad_cursor_result")
    present = _bool(value.get("present"))
    if not present:
        return False, None
    obj = _ref(value.get("object_ref"))
    parent = _ref(value.get("parent_ref"))
    component = _int(value.get("component_index"))
    cursor_pos = _finite_vec3(value.get("cursor_pos"))
    if obj is None and parent is None:
        return True, None
    return True, {
        "object": obj,
        "parent": parent,
        "component_index": component,
        "cursor_pos": cursor_pos,
    }


def _widget(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CursorContractError("bad_cursor_result")
    name = value.get("name")
    if name not in WIDGET_NAMES:
        raise CursorContractError("bad_cursor_result")
    if "exists" not in value:
        raise CursorContractError("bad_cursor_result")
    exists = _bool(value.get("exists"))
    if exists is not True:
        return {
            "name": name,
            "exists": False,
            "path": None,
            "type": None,
            "visible": None,
            "visible_hierarchy": None,
            "rect": None,
        }
    path = value.get("path")
    widget_type = value.get("widget_type")
    if not isinstance(path, str) or path == "" or not isinstance(widget_type, str) or widget_type == "":
        raise CursorContractError("bad_cursor_result")
    if "visible" not in value or "visible_hierarchy" not in value:
        raise CursorContractError("bad_cursor_result")
    visible = _bool(value.get("visible"))
    hierarchy = _bool(value.get("visible_hierarchy"))
    rect = {
        "x": value.get("screen_x"),
        "y": value.get("screen_y"),
        "w": value.get("screen_w"),
        "h": value.get("screen_h"),
    }
    numbers = []
    for item in rect.values():
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise CursorContractError("bad_cursor_result")
        number = float(item)
        if not math.isfinite(number):
            raise CursorContractError("bad_cursor_result")
        numbers.append(number)
    return {
        "name": name,
        "exists": True,
        "path": path,
        "type": widget_type,
        "visible": visible,
        "visible_hierarchy": hierarchy,
        "rect": {"x": numbers[0], "y": numbers[1], "w": numbers[2], "h": numbers[3]},
    }


def _slots(value: object, *, with_widget: bool) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        raise CursorContractError("bad_cursor_result")
    found: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            raise CursorContractError("bad_cursor_result")
        slot = item.get("slot")
        if slot not in SLOT_NAMES or slot in found:
            raise CursorContractError("bad_cursor_result")
        if "action_class" not in item or "count" not in item:
            raise CursorContractError("bad_cursor_result")
        action_class = item.get("action_class")
        count = _int(item.get("count"))
        # Enforce has no nullable string. An empty class is "no action selected".
        # A missing key is not that empty string.
        if action_class == "":
            action = None
        elif isinstance(action_class, str):
            action = action_class
        else:
            raise CursorContractError("bad_cursor_result")
        # Vanilla GetActions clears the action reference before its sprint and
        # vehicle returns, and updates the count only after those returns. A
        # hidden cursor can therefore show an empty class beside a retained
        # positive count. A missing key is still a contract error. Coherence
        # compares the two sides afterwards and does not repair this state.
        if count < 0 or (action is not None and count < 1):
            raise CursorContractError("bad_cursor_result")
        row: dict[str, Any] = {"action_class": action, "count": count}
        if with_widget:
            input_name = item.get("input_name")
            widget = item.get("widget")
            if not isinstance(input_name, str) or not isinstance(widget, str):
                raise CursorContractError("bad_cursor_result")
            row["input"] = input_name
            row["widget"] = widget
        found[slot] = row
    if set(found) != set(SLOT_NAMES):
        raise CursorContractError("bad_cursor_result")
    return found


def _identity_key(ref: dict[str, Any] | None) -> tuple[Any, ...] | None:
    if ref is None:
        return None
    return (ref["classname"], ref["net_low"], ref["net_high"], ref["pos"])


def _targets_match(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if left.get("component_index") != right.get("component_index"):
        return False
    if left.get("cursor_pos") != right.get("cursor_pos"):
        return False
    return _identity_key(left.get("object")) == _identity_key(right.get("object")) and (
        _identity_key(left.get("parent")) == _identity_key(right.get("parent"))
    )


def _apply_ids(
    subjects: list[dict[str, Any] | None],
    rows: object,
    run_id: object,
) -> None:
    if not isinstance(rows, list):
        raise CursorContractError("bad_cursor_result")
    by_net: dict[tuple[int, int], dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise CursorContractError("bad_cursor_result")
        key = (_int(row.get("net_low")), _int(row.get("net_high")))
        if key in by_net:
            raise CursorContractError("bad_cursor_result")
        by_net[key] = row
    for subject in subjects:
        if subject is None:
            continue
        row = by_net.get((subject["net_low"], subject["net_high"]))
        if row is None:
            continue
        registry_run = row.get("registry_run", run_id)
        if registry_run != run_id:
            continue
        object_id = row.get("object_id")
        if object_id is None:
            continue
        if isinstance(object_id, bool) or not isinstance(object_id, int) or object_id <= 0:
            raise CursorContractError("bad_cursor_result")
        subject["object_id"] = object_id


def collect_net_pairs(payload: dict[str, Any]) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()

    def take(ref: dict[str, Any] | None) -> None:
        if ref is None:
            return
        key = (ref["net_low"], ref["net_high"])
        if key in seen:
            return
        seen.add(key)
        pairs.append(key)

    for target in (payload["manager"]["target"], payload["cursor"]["target"]):
        if isinstance(target, dict):
            take(target.get("object"))
            take(target.get("parent"))
    take(payload["cursor"].get("display_object"))
    return pairs


def assemble_action_cursor(
    raw: object,
    id_rows: object,
    *,
    fence_before: tuple[object, ...],
    fence_after: tuple[object, ...],
    run_token_sent: object,
    run_token_echo: object,
    run_id: object,
    request_id: str,
) -> dict[str, Any]:
    """Build the public read. A dropped or malformed payload is not success."""
    if fence_before != fence_after or run_token_sent != run_token_echo:
        raise CursorContractError("snapshot_invalidated")
    if not isinstance(raw, dict):
        raise CursorContractError("bad_cursor_result")
    body = raw.get("action_cursor", raw)
    if not isinstance(body, dict):
        raise CursorContractError("bad_cursor_result")
    # A selector is not an observation. Ignore it if a caller stuffed one in.
    read_tick = _int(body.get("read_tick"))
    cursor_tick = _int(body.get("cursor_update_tick"))
    content_fresh = read_tick > 0 and read_tick == cursor_tick
    manager_present, manager_target = _target(body.get("manager_target"))
    cursor_present, cursor_target = _target(body.get("cursor_target"))
    display = _ref(body.get("display_object"))
    manager_slots = _slots(body.get("manager_slots"), with_widget=False)
    cursor_slots = _slots(body.get("cursor_slots"), with_widget=True)
    widgets_in = body.get("widgets")
    if not isinstance(widgets_in, list):
        raise CursorContractError("bad_cursor_result")
    widgets = [_widget(item) for item in widgets_in]
    if [item["name"] for item in widgets] != list(WIDGET_NAMES):
        raise CursorContractError("bad_cursor_result")
    item_desc = body.get("item_description_input")
    if not isinstance(item_desc, str):
        raise CursorContractError("bad_cursor_result")
    slots_match = all(
        manager_slots[name]["action_class"] == cursor_slots[name]["action_class"]
        and manager_slots[name]["count"] == cursor_slots[name]["count"]
        for name in SLOT_NAMES
    )
    coherent = bool(
        content_fresh and slots_match and _targets_match(manager_target, cursor_target)
    )
    subjects: list[dict[str, Any] | None] = []
    for target in (manager_target, cursor_target):
        if isinstance(target, dict):
            subjects.append(target.get("object"))
            subjects.append(target.get("parent"))
    subjects.append(display)
    _apply_ids(subjects, id_rows, run_id)
    run_id, generation, client_instance = fence_provenance(fence_before)
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "run_id": run_id,
        "client_instance": client_instance,
        "generation": generation,
        "sample": {
            "read_tick": read_tick,
            "cursor_update_tick": cursor_tick,
            "content_fresh": content_fresh,
            "coherent": coherent,
        },
        "manager": {
            "action_target_present": manager_present,
            "target": manager_target,
            "selected_slots": {name: manager_slots[name] for name in SLOT_NAMES},
        },
        "cursor": {
            "action_target_present": cursor_present,
            "target": cursor_target,
            "display_object": display,
            "slots": {name: cursor_slots[name] for name in SLOT_NAMES},
            "item_description_input": item_desc,
        },
        "widgets": {
            item["name"]: {key: value for key, value in item.items() if key != "name"}
            for item in widgets
        },
    }


def _rows_from_ids(pairs: list[tuple[int, int]], ids_payload: object, run_id: object) -> list[dict[str, Any]]:
    if not isinstance(ids_payload, dict):
        raise CursorContractError("bad_cursor_result")
    body = ids_payload.get("cursor_ids", ids_payload)
    if not isinstance(body, dict):
        raise CursorContractError("bad_cursor_result")
    object_ids = body.get("object_ids")
    if not isinstance(object_ids, list) or len(object_ids) != len(pairs):
        raise CursorContractError("bad_cursor_result")
    rows = []
    for (net_low, net_high), object_id in zip(pairs, object_ids):
        if object_id == 0:
            object_id = None
        rows.append(
            {
                "net_low": net_low,
                "net_high": net_high,
                "object_id": object_id,
                "registry_run": run_id,
            }
        )
    return rows


async def execute_action_cursor(runtime: Any, timeout_s: float) -> dict[str, Any]:
    from mcp.server.fastmcp.exceptions import ToolError
    from dayz_mcp.bridge_readiness import _client_peer_announces_command
    from dayz_mcp.tool_args import _timeout

    timeout = _timeout(timeout_s)
    deadline = time.monotonic() + timeout

    async def _status() -> dict[str, Any]:
        left = deadline - time.monotonic()
        if left <= 0.0:
            raise ToolError("timeout")
        payload = await runtime.bridge_status_payload(timeout_s=left)
        if time.monotonic() > deadline:
            raise ToolError("timeout")
        if not isinstance(payload, dict):
            raise ToolError("bad_cursor_result")
        return payload

    status = await _status()
    if not _client_peer_announces_command(status, "action_cursor"):
        raise ToolError("bridge_capability_missing")
    if not peer_announces(status, "server_peer", "action_cursor_ids"):
        raise ToolError("bridge_capability_missing")
    fence_before = binding_fence(status, ("client", "server"))
    if fence_before is None:
        raise ToolError("broker_infrastructure_missing")
    run_id = fence_before["peers"]["client"]["run_id"]
    if fence_before["peers"]["server"]["run_id"] != run_id:
        raise ToolError("no_active_run")
    left = deadline - time.monotonic()
    if left <= 0.0:
        raise ToolError("timeout")
    raw = await call_bridge_fenced(runtime, "action_cursor", {}, "client", left, fence_before)
    if time.monotonic() > deadline:
        raise ToolError("timeout")
    if isinstance(raw, dict) and raw.get("ok") is False:
        code = raw.get("error")
        if not isinstance(code, str) or code == "":
            code = "bad_cursor_result"
        raise ToolError(code)
    left = deadline - time.monotonic()
    if left <= 0.0:
        raise ToolError("timeout")
    try:
        preview = assemble_action_cursor(
            raw,
            [],
            fence_before=fence_before,
            fence_after=fence_before,
            run_token_sent="",
            run_token_echo="",
            run_id=run_id,
            request_id="preview",
        )
    except CursorContractError as exc:
        raise ToolError(exc.code) from exc
    pairs = collect_net_pairs(preview)
    run_token = run_id
    ids = await call_bridge_fenced(
        runtime,
        "action_cursor_ids",
        {
            "net_low": [pair[0] for pair in pairs],
            "net_high": [pair[1] for pair in pairs],
            "run_token": run_token,
        },
        "server",
        left,
        fence_before,
    )
    status_after = await _status()
    echo = ""
    if isinstance(ids, dict):
        body = ids.get("cursor_ids")
        if isinstance(body, dict) and isinstance(body.get("run_token"), str):
            echo = body["run_token"]
    try:
        rows = _rows_from_ids(pairs, ids, run_id)
        return assemble_action_cursor(
            raw,
            rows,
            fence_before=fence_before,
            fence_after=binding_fence(status_after, ("client", "server")),
            run_token_sent=run_token,
            run_token_echo=echo,
            run_id=run_id,
            request_id=str(uuid.uuid4()),
        )
    except CursorContractError as exc:
        raise ToolError(exc.code) from exc
