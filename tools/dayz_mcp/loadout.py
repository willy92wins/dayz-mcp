"""loadout_get and loadout_apply: the loadout format and its wire rows (inbox fb99, 220f).

A loadout is what one target wears and holds, the same JSON for both verbs:

    {"attachments": [item, ...], "hands": item or null}
    item = {"type": str, "slot": str, "attachments": [item, ...], "cargo": [item, ...]}

slot is required in an attachments list and absent in cargo and in hands;
an item's attachments and cargo, and both keys of the loadout, are optional.
At most MAX_ITEMS items in all, and at most MAX_DEPTH levels below the target:
what the target wears and the item in its hands are level 1. No quantity,
health or other item state travels.

The bridge never sees the tree. MCPArgs.loadout_items (MCPLoadoutItem in
addon/scripts/5_Mission/MCPMessages.c) is one flat list of rows
{type, slot, dest, parent}: dest is attachment, cargo or hands, slot is ""
outside attachments, and parent is the index of the row the item goes into,
-1 for the target. Rows are in apply order: the target's attachments in list
order, then the hands item, each followed by its own attachments and then its
cargo, depth first. A parent always comes before its children, so the bridge
creates them in one pass. Every key travels in every row: Enforce reads an
absent key as 0 or "", and 0 is a row index (fb-20260930-065425-8779).

The bridge answers in MCPResult.loadout (MCPLoadout): items, the target's
inventory read in the same rows; applied, loadout_apply's outcome for each
request row in request order ({ok, error}); too_large, set when the read went
past the caps, with items then empty. This module turns those rows back into
the tree and the outcomes into the per-item list. MAX_ITEMS and MAX_DEPTH
mirror LOADOUT_MAX_ITEMS and LOADOUT_MAX_DEPTH in MCPBridge.c; a test keeps
the copies equal.

clear_first is a separate bridge verb, loadout_clear, which the tool sends
and waits for before loadout_apply. It answers deleted, how many objects it
deleted, cleanup_complete, whether they were all gone at its job's last
check, which can come one bridge tick after the 5 s deadline, and uid, the
player it acted on (MCPResult scalars); clear_reply and cleared_uid read
them. With neither object_id nor uid given, the tool sends loadout_apply to
that uid, so the creates go to the player the clear acted on even when the
first human has changed in between (#261 review Y11).
"""
from __future__ import annotations

from typing import Any


MAX_ITEMS = 64
MAX_DEPTH = 4
# loadout_apply's per-item errors (MCPBridge.c CreateLoadoutItem and ApplyLoadout).
ITEM_ERRORS = (
    "unknown_type",
    "slot_not_found",
    "slot_occupied",
    "cannot_attach",
    "no_room",
    "parent_failed",
)
# Warning of an ok answer whose loadout cannot be read: ok is then 0.
INCOMPLETE = "loadout_incomplete"

_LOADOUT_KEYS = ("attachments", "hands")
_ITEM_KEYS = ("type", "slot", "attachments", "cargo")
_ROW_KEYS = frozenset({"type", "slot", "dest", "parent"})


def _refusal(path: str, value: object, requirement: str) -> str:
    return f"bad_args: {path} {value!r} must {requirement}"


def _is_name(value: object) -> bool:
    """A classname or a slot name: a non-empty string without whitespace."""
    return (
        isinstance(value, str)
        and value != ""
        and not any(character.isspace() for character in value)
    )


def _is_index(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def target_args(object_id: object, uid: object) -> dict[str, Any]:
    """The wire keys that name the target: object_id, uid, or neither.

    object_id > 0 is a same-run registry id; 0 leaves the choice to uid, where
    "" is the first human. Both given is bad_args.
    """
    if not _is_index(object_id) or object_id < 0:
        raise ValueError(
            _refusal("object_id", object_id, "be a registry id above 0, or 0 to target by uid")
        )
    if not isinstance(uid, str):
        raise ValueError(_refusal("uid", uid, "be a string"))
    if object_id > 0 and uid != "":
        raise ValueError(
            f"bad_args: object_id {object_id!r} and uid {uid!r} must not both be "
            "given: object_id names a survivor world_spawn made, uid a connected player"
        )
    if object_id > 0:
        return {"object_id": object_id}
    if uid != "":
        return {"uid": uid}
    return {}


def flatten_request(loadout: object) -> list[tuple[str, dict[str, Any]]]:
    """(path, wire row) pairs in apply order. ValueError names the first bad path."""
    if not isinstance(loadout, dict):
        raise ValueError(_refusal("loadout", loadout, "be an object with attachments and hands"))
    for key in loadout:
        if key not in _LOADOUT_KEYS:
            raise ValueError(
                _refusal(f"loadout.{key}", loadout[key], "be absent: a loadout takes attachments and hands")
            )
    rows: list[tuple[str, dict[str, Any]]] = []
    _flatten_list(loadout, "attachments", "loadout", -1, 1, rows)
    hands = loadout.get("hands")
    if hands is not None:
        _flatten_item(hands, "loadout.hands", "hands", -1, 1, rows)
    return rows


def _flatten_list(
    owner: dict[str, Any],
    key: str,
    owner_path: str,
    parent: int,
    level: int,
    rows: list[tuple[str, dict[str, Any]]],
) -> None:
    if key not in owner:
        return
    items = owner[key]
    path = f"{owner_path}.{key}"
    if not isinstance(items, list):
        raise ValueError(_refusal(path, items, "be a list of items"))
    dest = "attachment" if key == "attachments" else "cargo"
    for index, item in enumerate(items):
        _flatten_item(item, f"{path}[{index}]", dest, parent, level, rows)


def _flatten_item(
    item: object,
    path: str,
    dest: str,
    parent: int,
    level: int,
    rows: list[tuple[str, dict[str, Any]]],
) -> None:
    if not isinstance(item, dict):
        raise ValueError(_refusal(path, item, "be an item object with a type"))
    for key in item:
        if key not in _ITEM_KEYS:
            raise ValueError(
                _refusal(
                    f"{path}.{key}",
                    item[key],
                    "be absent: an item takes type, slot, attachments and cargo",
                )
            )
    if level > MAX_DEPTH:
        raise ValueError(
            f"bad_args: {path} is {level} levels below the target; a loadout "
            f"nests at most {MAX_DEPTH}"
        )
    if len(rows) >= MAX_ITEMS:
        raise ValueError(
            f"bad_args: {path} is item {len(rows) + 1}; a loadout holds at most "
            f"{MAX_ITEMS} items"
        )
    type_name = item.get("type")
    if not _is_name(type_name):
        raise ValueError(
            _refusal(f"{path}.type", type_name, "be a classname: a non-empty string without whitespace")
        )
    slot = ""
    if dest == "attachment":
        slot = item.get("slot")
        if not _is_name(slot):
            raise ValueError(
                _refusal(f"{path}.slot", slot, "be a slot name: a non-empty string without whitespace")
            )
    elif "slot" in item:
        where = "cargo" if dest == "cargo" else "hands"
        raise ValueError(_refusal(f"{path}.slot", item["slot"], f"be absent in {where}"))
    row_index = len(rows)
    rows.append((path, {"type": type_name, "slot": slot, "dest": dest, "parent": parent}))
    _flatten_list(item, "attachments", path, row_index, level + 1, rows)
    _flatten_list(item, "cargo", path, row_index, level + 1, rows)


def is_wire_items(value: object) -> bool:
    """True for a loadout_items list the bridge accepts (MCPBridge.c LoadoutRowsError)."""
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        return False
    levels: list[int] = []
    hands_rows = 0
    for index, row in enumerate(value):
        if not isinstance(row, dict) or set(row) != _ROW_KEYS:
            return False
        slot = row["slot"]
        dest = row["dest"]
        parent = row["parent"]
        if not _is_name(row["type"]) or not isinstance(slot, str):
            return False
        if not _is_index(parent) or not -1 <= parent < index:
            return False
        if dest == "attachment":
            if not _is_name(slot):
                return False
        elif dest == "cargo":
            if parent == -1 or slot != "":
                return False
        elif dest == "hands":
            hands_rows += 1
            if parent != -1 or slot != "" or hands_rows > 1:
                return False
        else:
            return False
        level = 1 if parent == -1 else levels[parent] + 1
        if level > MAX_DEPTH:
            return False
        levels.append(level)
    return True


def _wire_bool(value: object) -> bool | None:
    """An Enforce bool off the wire (it arrives as 0/1), or None for any other shape."""
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    return None


def nest_rows(rows: object) -> dict[str, Any] | None:
    """The loadout tree of a bridge read, or None when the rows do not form one.

    Every item carries attachments and cargo, [] when it holds nothing, and
    slot only in an attachments list, so the read is a loadout_apply input.
    """
    if not isinstance(rows, list):
        return None
    tree: dict[str, Any] = {"attachments": [], "hands": None}
    nodes: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            return None
        type_name = row.get("type")
        slot = row.get("slot", "")
        dest = row.get("dest")
        parent = row.get("parent")
        if not isinstance(type_name, str) or type_name == "" or not isinstance(slot, str):
            return None
        if not _is_index(parent) or not -1 <= parent < index:
            return None
        node: dict[str, Any] = {"type": type_name}
        if dest == "attachment":
            node["slot"] = slot
        node["attachments"] = []
        node["cargo"] = []
        if parent == -1:
            if dest == "attachment":
                tree["attachments"].append(node)
            elif dest == "hands" and tree["hands"] is None:
                tree["hands"] = node
            else:
                return None
        else:
            owner = nodes[parent]
            if dest == "attachment":
                owner["attachments"].append(node)
            elif dest == "cargo":
                owner["cargo"].append(node)
            else:
                return None
        nodes.append(node)
    return tree


def _read_tree(rows: object) -> dict[str, Any] | None:
    """The tree of a bridge read, or None unless it is one loadout_apply takes.

    None when the rows do not nest (nest_rows) or when the tree they nest into
    is one flatten_request refuses (an attachment with no slot name, say).
    """
    tree = nest_rows(rows)
    if tree is None:
        return None
    try:
        flatten_request(tree)
    except ValueError:
        return None
    return tree


def _incomplete(result: dict[str, Any]) -> dict[str, Any]:
    response = dict(result)
    response["ok"] = 0
    response["warnings"] = [INCOMPLETE]
    return response


def get_reply(result: dict[str, Any]) -> dict[str, Any]:
    """loadout_get's answer: the bridge's rows as the loadout tree.

    An answer that is not ok (the not-ready envelope a read returns before
    enqueue) passes through untouched. An ok answer whose rows do not form a
    tree that loadout_apply takes (flatten_request), or that says too_large,
    is ok=0 with warnings [loadout_incomplete].
    """
    if not isinstance(result, dict) or not result.get("ok"):
        return result
    wire = result.get("loadout")
    if not isinstance(wire, dict) or _wire_bool(wire.get("too_large", False)) is not False:
        return _incomplete(result)
    tree = _read_tree(wire.get("items"))
    if tree is None:
        return _incomplete(result)
    response = dict(result)
    response["loadout"] = tree
    return response


def apply_reply(
    rows: list[tuple[str, dict[str, Any]]], result: dict[str, Any]
) -> dict[str, Any]:
    """loadout_apply's answer: status, items in apply order and the read after.

    items[i] is {path, type, ok, error} for the request row rows[i]; type is
    the class the item asked for and error is null when ok. status is "ok"
    when every item was created, else "partial". loadout is the target read
    after the apply, null with loadout_too_large true when that read went past
    the caps. An answer that is not ok passes through untouched. An ok answer
    that cannot be read is ok=0 with warnings [loadout_incomplete]: the apply
    ran, so nothing here raises. That includes a read after whose rows do not
    form a tree loadout_apply takes, as get_reply checks it (#261 review Y16).
    """
    if not isinstance(result, dict) or not result.get("ok"):
        return result
    wire = result.get("loadout")
    if not isinstance(wire, dict):
        return _incomplete(result)
    applied = wire.get("applied")
    too_large = _wire_bool(wire.get("too_large", False))
    if not isinstance(applied, list) or len(applied) != len(rows) or too_large is None:
        return _incomplete(result)
    items: list[dict[str, Any]] = []
    for (path, row), outcome in zip(rows, applied):
        if not isinstance(outcome, dict):
            return _incomplete(result)
        ok = _wire_bool(outcome.get("ok"))
        error = outcome.get("error", "")
        if ok is None or not isinstance(error, str) or ok == (error != ""):
            return _incomplete(result)
        items.append(
            {"path": path, "type": row["type"], "ok": ok, "error": None if ok else error}
        )
    tree = None
    if not too_large:
        tree = _read_tree(wire.get("items"))
        if tree is None:
            return _incomplete(result)
    response = dict(result)
    response["status"] = "ok" if all(item["ok"] for item in items) else "partial"
    response["items"] = items
    response["loadout"] = tree
    response["loadout_too_large"] = too_large
    return response


def clear_reply(result: object) -> dict[str, Any]:
    """loadout_clear's answer as loadout_apply reports it: {deleted, cleanup_complete}.

    deleted is how many objects the clear deleted; cleanup_complete is True
    when they were all gone at the bridge job's last check, which can come one
    bridge tick after the 5 s deadline. An answer without both raises
    ValueError (loadout_incomplete): the tool then sends nothing more.
    """
    deleted = result.get("deleted") if isinstance(result, dict) else None
    complete = _wire_bool(result.get("cleanup_complete")) if isinstance(result, dict) else None
    if not _is_index(deleted) or deleted < 0 or complete is None:
        raise ValueError(
            f"{INCOMPLETE}: loadout_clear answered without deleted and "
            "cleanup_complete, so nothing was applied; read the target with "
            "loadout_get"
        )
    return {"deleted": deleted, "cleanup_complete": complete}


def cleared_uid(result: object) -> str:
    """The uid of the player loadout_clear acted on, for the creates to target.

    An answer without one, or with "", raises ValueError (loadout_incomplete):
    the tool then sends nothing more.
    """
    uid = result.get("uid") if isinstance(result, dict) else None
    if not isinstance(uid, str) or uid == "":
        raise ValueError(
            f"{INCOMPLETE}: loadout_clear answered without the uid of the "
            "player it cleared, so nothing was applied; read the target with "
            "loadout_get"
        )
    return uid


def refusal_after_clear(message: str, clear: dict[str, Any]) -> str:
    """The error of a loadout_apply refused after its clear ran, with what the clear did."""
    return (
        f"{message}; clear=deleted={clear['deleted']} "
        f"cleanup_complete={int(clear['cleanup_complete'])}"
    )
