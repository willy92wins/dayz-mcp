"""Argument validation for the MCP tools, and the FastMCP schema patches.

The bad_args helpers a tool handler runs before anything reaches the bridge,
and the wrappers that close, alias or enum-gate a registered tool's
arguments. Moved out of server.py unchanged (backlog 71fc); server.py imports
every name back, so dayz_mcp.server.<name> is the same object.
"""

from __future__ import annotations

import math
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import dayz_test_modes
from dayz_mcp.bridge_errors import _is_safe_error_token
from dayz_mcp.loopback import (
    INPUT_NAME_MAX_CHARS,
    INPUT_TRIGGER_DIK_MAX,
    INPUT_TRIGGER_HOLD_MAX_S,
    INPUT_TRIGGER_PRESS_MAX_TTL_S,
    is_printable_input_name,
)


# Upper bound for per-tool bridge timeouts. 300 s, not 120 s, because
# dayz_test_run in mode=all measured 28.6 s and the operation pin already caps
# at MAX_OPERATION_PIN_S=300.0 (session_coordination).
MAX_TIMEOUT_S = 300.0


def _bad_args(field: str, value: object, requirement: str) -> str:
    return f"bad_args: {field} {value!r} must {requirement}"


# fb-20260823-131632-4f1c: the bridge answers a bare unknown_type when type is
# empty or ConfigIsExisting("CfgVehicles " + type) is false (MCPBridge.c
# ValidateSpawnArgs), so type="vehicle" never said what type is. The tool knows
# the type it sent and names it. type is caller input of any length, so the
# echo is cut at _SPAWN_TYPE_ECHO_MAX characters.
_SPAWN_TYPE_ECHO_MAX = 64


def _unknown_spawn_type(type_name: str) -> str:
    shown = repr(type_name[:_SPAWN_TYPE_ECHO_MAX])
    if len(type_name) > _SPAWN_TYPE_ECHO_MAX:
        shown += f" (first {_SPAWN_TYPE_ECHO_MAX} of {len(type_name)} characters)"
    return (
        f"unknown_type: type {shown} is not a CfgVehicles classname; type takes "
        "a classname such as CivilianSedan (a mod's classnames exist only while "
        "that mod is loaded)"
    )


def _require_vec3(value: list[float] | None, name: str) -> list[float]:
    error = (
        "bad_pos"
        if name == "pos"
        else _bad_args(name, value, "be a list of 3 finite numbers")
    )
    if not isinstance(value, list) or len(value) != 3:
        raise ToolError(error)
    try:
        vec = [float(value[0]), float(value[1]), float(value[2])]
    except (TypeError, ValueError) as exc:
        raise ToolError(error) from exc
    if not all(math.isfinite(item) for item in vec):
        raise ToolError(error)
    return vec


def _require_float_list(
    value: list[float] | None, count: int, name: str = "value"
) -> list[float]:
    error = _bad_args(name, value, f"be a list of {count} finite numbers")
    if not isinstance(value, list) or len(value) != count:
        raise ToolError(error)
    try:
        items = [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise ToolError(error) from exc
    if not all(math.isfinite(item) for item in items):
        raise ToolError(error)
    return items


def _timeout(timeout_s: float) -> float:
    try:
        value = float(timeout_s)
    except (TypeError, ValueError) as exc:
        raise ToolError("bad_timeout") from exc
    if value <= 0.0 or not math.isfinite(value):
        raise ToolError("bad_timeout")
    if value > MAX_TIMEOUT_S:
        raise ToolError("bad_timeout")
    return value


def _finite_float(value: float, error: str = "bad_args") -> float:
    resolved_error = (
        _bad_args("value", value, "be a finite number")
        if error == "bad_args"
        else error
    )
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise ToolError(resolved_error) from exc
    if not math.isfinite(converted):
        raise ToolError(resolved_error)
    return converted


# A hold answers after its release, hold_s after the press: the tool waits at
# least hold_s plus this, the slack MCPClientBridge.c INPUT_TRIGGER_JOB_SLACK_S
# gives the job before it releases the key itself.
INPUT_TRIGGER_HOLD_SLACK_S = 5.0


def _input_trigger_seconds(field: str, value: object, maximum: float, when: str) -> float:
    requirement = f"be a finite number in (0, {maximum}] with {when}"
    if value is None or isinstance(value, bool):
        raise ToolError(_bad_args(field, value, requirement))
    seconds = _finite_float(value, _bad_args(field, value, requirement))
    if seconds <= 0.0 or seconds > maximum:
        raise ToolError(_bad_args(field, value, requirement))
    return seconds


def _input_trigger_request(
    kind: object,
    dik: object,
    name: object,
    entry: object,
    phase: object,
    hold_s: object,
    ttl_s: object,
    timeout_s: object,
) -> tuple[dict[str, Any], float]:
    """The input_trigger wire args and wait budget, or ToolError bad_args.

    Each kind and phase sends exactly the keys of its ingress variant
    (loopback._input_trigger_variant). No number travels without its phase,
    so an absent key never reaches Enforce as 0 (fb-20260930-065425-8779).
    """
    if kind not in ("key", "input"):
        raise ToolError(_bad_args("kind", kind, "be 'key' or 'input'"))
    if phase not in ("click", "hold", "press", "release"):
        raise ToolError(
            _bad_args("phase", phase, "be 'click', 'hold', 'press' or 'release'")
        )
    args: dict[str, Any] = {"trigger_kind": kind, "trigger_edge": phase}
    if kind == "key":
        if (
            isinstance(dik, bool)
            or not isinstance(dik, int)
            or dik < 0
            or dik > INPUT_TRIGGER_DIK_MAX
        ):
            raise ToolError(
                _bad_args(
                    "dik", dik, f"be an int from 0 to {INPUT_TRIGGER_DIK_MAX} with kind='key'"
                )
            )
        if name is not None:
            raise ToolError(_bad_args("name", name, "be omitted with kind='key'"))
        chosen_entry = "game" if entry is None else entry
        if chosen_entry not in ("game", "mission"):
            raise ToolError(_bad_args("entry", entry, "be 'game' or 'mission'"))
        args["trigger_entry"] = chosen_entry
        args["dik"] = dik
    else:
        if not is_printable_input_name(name):
            raise ToolError(
                _bad_args(
                    "name",
                    name,
                    f"be 1..{INPUT_NAME_MAX_CHARS} printable ASCII characters "
                    "(codes 32..126) with kind='input'",
                )
            )
        if dik is not None:
            raise ToolError(_bad_args("dik", dik, "be omitted with kind='input'"))
        if entry is not None:
            raise ToolError(_bad_args("entry", entry, "be omitted with kind='input'"))
        args["name"] = name
    if phase == "hold":
        args["hold_s"] = _input_trigger_seconds(
            "hold_s", hold_s, INPUT_TRIGGER_HOLD_MAX_S, "phase='hold'"
        )
    elif hold_s is not None:
        raise ToolError(_bad_args("hold_s", hold_s, "be omitted unless phase is 'hold'"))
    if phase == "press":
        args["hold_ttl_s"] = _input_trigger_seconds(
            "ttl_s", ttl_s, INPUT_TRIGGER_PRESS_MAX_TTL_S, "phase='press'"
        )
    elif ttl_s is not None:
        raise ToolError(_bad_args("ttl_s", ttl_s, "be omitted unless phase is 'press'"))
    timeout = _timeout(timeout_s)
    if phase == "hold":
        timeout = max(timeout, args["hold_s"] + INPUT_TRIGGER_HOLD_SLACK_S)
    return args, timeout


def _optional_finite_float(
    value: float | None, error: str = "bad_args"
) -> float | None:
    if value is None:
        return None
    return _finite_float(value, error)


def _require_range(
    value: float,
    minimum: float,
    maximum: float,
    error: str = "bad_args",
) -> float:
    resolved_error = (
        _bad_args(
            "value", value, f"be a finite number from {minimum} to {maximum}"
        )
        if error == "bad_args"
        else error
    )
    converted = _finite_float(value, resolved_error)
    if converted < minimum or converted > maximum:
        raise ToolError(resolved_error)
    return converted


def _patch_public_argument_alias(app: FastMCP, tool_name: str, internal: str, public: str) -> None:
    tool = app._tool_manager.get_tool(tool_name)  # type: ignore[attr-defined]
    if tool is None:
        raise RuntimeError(f"missing tool {tool_name}")
    properties = tool.parameters.get("properties", {})
    if internal in properties:
        properties[public] = properties.pop(internal)
    required = tool.parameters.get("required")
    if isinstance(required, list):
        tool.parameters["required"] = [public if item == internal else item for item in required]
    original = tool.fn_metadata.call_fn_with_arg_validation

    async def patched(fn, fn_is_async, arguments_to_validate, arguments_to_pass_directly):
        arguments = dict(arguments_to_validate)
        if public in arguments and internal not in arguments:
            arguments[internal] = arguments.pop(public)
        return await original(fn, fn_is_async, arguments, arguments_to_pass_directly)

    object.__setattr__(tool.fn_metadata, "call_fn_with_arg_validation", patched)


_CLOSED_UNEXPECTED_ARGUMENT_CAP = 5


def _echo_unexpected_argument_key(key: str) -> str:
    """Render a caller-supplied argument name for a closed-schema error.

    Identifier-shaped keys are echoed. Anything else (path, space, punctuation)
    becomes ``<unsafe>`` so host text never crosses the MCP wire. Short names
    such as ``id`` fail ``_is_safe_error_token``'s 3-char floor but are still
    identifier-shaped and safe to name.
    """
    if not isinstance(key, str):
        return "<unsafe>"
    if _is_safe_error_token(key):
        return key
    if (
        1 <= len(key) <= 2
        and key[0].isascii()
        and key[0].isalpha()
        and all(char.isascii() and (char.isalnum() or char == "_") for char in key)
    ):
        return key
    return "<unsafe>"


def _closed_unexpected_arguments_message(
    unknown: set[str],
    allowed: set[str],
    required: list[str] | tuple[str, ...],
    provided: object,
) -> str:
    """``bad_args: unexpected arguments: ... (accepted: ...)`` plus ``; missing:``."""
    ranked = sorted(unknown)
    echoed = [
        _echo_unexpected_argument_key(name)
        for name in ranked[:_CLOSED_UNEXPECTED_ARGUMENT_CAP]
    ]
    unexpected = ", ".join(echoed)
    overflow = len(ranked) - _CLOSED_UNEXPECTED_ARGUMENT_CAP
    if overflow > 0:
        unexpected = f"{unexpected} +{overflow} more"
    accepted = ", ".join(sorted(allowed))
    message = f"bad_args: unexpected arguments: {unexpected} (accepted: {accepted})"
    missing = sorted(name for name in required if name not in provided)
    if missing:
        message = f"{message}; missing: {', '.join(missing)}"
    return message


def _patch_closed_tool_schema(app: FastMCP, tool_name: str) -> None:
    tool = app._tool_manager.get_tool(tool_name)  # type: ignore[attr-defined]
    if tool is None:
        raise RuntimeError(f"missing tool {tool_name}")
    tool.parameters["additionalProperties"] = False
    # A zero-argument tool publishes `required: []` explicitly: a consumer that generates
    # calls from the schema must see a closed, empty contract, not an absent key.
    tool.parameters.setdefault("required", [])
    allowed = set(tool.parameters.get("properties", {}))
    required = tuple(tool.parameters.get("required") or [])
    original = tool.fn_metadata.call_fn_with_arg_validation

    async def patched(fn, fn_is_async, arguments_to_validate, arguments_to_pass_directly):
        unknown = set(arguments_to_validate) - allowed
        if unknown:
            raise ToolError(
                _closed_unexpected_arguments_message(
                    unknown, allowed, required, arguments_to_validate
                )
            )
        return await original(fn, fn_is_async, arguments_to_validate, arguments_to_pass_directly)

    object.__setattr__(tool.fn_metadata, "call_fn_with_arg_validation", patched)


def _patch_mode_enum_from_authority(app: FastMCP, tool_name: str, field: str = "mode") -> None:
    """Publish and enforce a mode enum read from the M12 authority at build time and per call.

    ``dayz_test_modes.public_mode_names()`` is read when the app is BUILT (never when this module
    is imported) for the published schema, and again on EVERY call before validation, so a
    substituted record set is honoured both by a new ``build_app()`` and by the next call
    (Codex B-01, 2026-09-04). Schema and validation close together: the annotation stays ``str``
    and this wrapper is the gate.
    """
    tool = app._tool_manager.get_tool(tool_name)  # type: ignore[attr-defined]
    if tool is None:
        raise RuntimeError(f"missing tool {tool_name}")
    prop = tool.parameters.get("properties", {}).get(field)
    if not isinstance(prop, dict):
        raise RuntimeError(f"missing property {field} on {tool_name}")
    prop["enum"] = list(dayz_test_modes.public_mode_names())
    original = tool.fn_metadata.call_fn_with_arg_validation

    async def patched(fn, fn_is_async, arguments_to_validate, arguments_to_pass_directly):
        allowed = dayz_test_modes.public_mode_names()
        if arguments_to_validate.get(field) not in allowed:
            raise ToolError(f"bad_args: {field} must be one of " + "|".join(allowed))
        return await original(fn, fn_is_async, arguments_to_validate, arguments_to_pass_directly)

    object.__setattr__(tool.fn_metadata, "call_fn_with_arg_validation", patched)


def _object_target_args(
    type: str, pos: list[float] | None, object_id: int
) -> dict[str, Any]:
    """Validate the object_id-or-classname target contract shared by object verbs."""
    if not isinstance(object_id, int) or isinstance(object_id, bool) or object_id < 0:
        raise ToolError(_bad_args("object_id", object_id, "be a non-negative int"))
    if object_id > 0:
        return {"object_id": object_id}
    if not isinstance(type, str) or type == "":
        raise ToolError(
            _bad_args("type", type, "be a non-empty string when object_id is omitted")
        )
    if not _inspect_type_is_valid(type):
        raise ToolError(
            _bad_args("type", type, "be a DayZ classname without whitespace")
        )
    return {"type": type, "pos": _require_vec3(pos, "pos")}


def _inspect_type_is_valid(type: object) -> bool:
    """True for a classname token object_inspect can echo on rejection."""
    if not isinstance(type, str) or not type:
        return False
    if type.strip() != type or any(ch.isspace() for ch in type):
        return False
    if "/" in type or "\\" in type or type[0] in "{[":
        return False
    return True
