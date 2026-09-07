"""Ratchet: a new wire-coercible tool parameter must be allowlisted.

Pydantic coerces at the FastMCP frontier before any executor guard runs.
A validation written inside the handler never sees ``false`` on a bare
``float | None`` -- it sees ``0.0``. This module enumerates live tool
signatures from the registry, classifies that shape, and fails when a
new coercible parameter appears without an explicit reason.

The classifier is fail-closed: a union is coercible if ANY branch
converts, Strict metadata is read by value not by class name, and an
unknown form is coercible until a wire test proves otherwise.
"""
from __future__ import annotations

import enum
import inspect
import re
import sys
import typing
import unittest
from collections.abc import Mapping
from dataclasses import dataclass
from types import UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin
from unittest.mock import patch

from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Strict, StrictBool, StrictFloat, StrictInt

from dayz_mcp.server import ServerConfig, ToolError, build_app


# Floor against an empty census, which would otherwise read as "all clear".
# Measured 2026-09-07 ronda 2: 61 tools (default 60 + exec_enforce), 217
# caller parameters. Exact so dropping the enableable surface fails the gate.
MIN_REGISTERED_TOOLS = 61
MIN_REGISTERED_PARAMS = 217

_SCALAR_COERCIBLE = frozenset({bool, int, float, str})
_UNION_ORIGINS = (Union, UnionType)
_CONTAINER_ORIGINS = (list, dict, tuple, set, frozenset)
CONDITIONAL_TOOL_REGISTRY_FLAGS = ("enable_exec_enforce",)


def _is_union(annotation: object) -> bool:
    return get_origin(annotation) in _UNION_ORIGINS


def _is_context(annotation: object) -> bool:
    core, _optional = _strip_optional(annotation)
    if core is Context:
        return True
    if isinstance(core, type) and core.__name__ == "Context":
        return True
    if _is_union(core) and any(_is_context(branch) for branch in get_args(core)):
        return True
    return False


def _strict_on(meta: object) -> bool:
    """True only when this metadata actually enables strict validation."""
    return getattr(meta, "strict", None) is True


def _strip_optional(annotation: object) -> tuple[object, bool]:
    origin = get_origin(annotation)
    if origin is Annotated:
        core, inner_optional = _strip_optional(get_args(annotation)[0])
        return core, inner_optional
    if _is_union(annotation):
        raw = get_args(annotation)
        remaining = [branch for branch in raw if branch is not type(None)]
        optional = len(remaining) != len(raw)
        if len(remaining) == 1:
            core, inner_optional = _strip_optional(remaining[0])
            return core, optional or inner_optional
        return Union[tuple(remaining)], optional
    return annotation, False


def _literal_is_coercible(annotation: object) -> bool:
    """Numeric/bool literals convert ``false``; string-only literals reject it."""
    return any(isinstance(value, (int, float)) for value in get_args(annotation))


def _enum_is_coercible(annotation: object) -> bool:
    if not isinstance(annotation, type):
        return True
    try:
        if not issubclass(annotation, enum.Enum):
            return True
    except TypeError:
        return True
    return any(isinstance(member.value, (int, float)) for member in annotation)


def _form_is_coercible(annotation: object) -> bool:
    """Recursive conversion behaviour. Unknown forms are coercible."""
    origin = get_origin(annotation)
    if origin is Annotated:
        args = get_args(annotation)
        if any(_strict_on(meta) for meta in args[1:]):
            return False
        return _form_is_coercible(args[0])
    if _is_union(annotation):
        branches = [
            branch for branch in get_args(annotation) if branch is not type(None)
        ]
        if not branches:
            return False
        return any(_form_is_coercible(branch) for branch in branches)
    if origin is Literal:
        return _literal_is_coercible(annotation)
    if origin in _CONTAINER_ORIGINS:
        return False
    if annotation in _SCALAR_COERCIBLE:
        return True
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return _enum_is_coercible(annotation)
    return True


def annotation_is_wire_coercible(annotation: object) -> bool:
    """True when the wire may coerce bool/int/str across this annotation.

    The annotation is a hint; ``call_tool`` with ``false`` is the fact.
    A union is coercible when any branch is. ``Strict(False)`` is not
    strict. Unknown forms fail closed (treated as coercible).
    """
    if annotation is inspect.Parameter.empty or _is_context(annotation):
        return False
    return _form_is_coercible(annotation)


def annotation_label(annotation: object) -> str:
    if annotation is inspect.Parameter.empty:
        return "<empty>"
    origin = get_origin(annotation)
    if origin is Annotated:
        inner = annotation_label(get_args(annotation)[0])
        metas = get_args(annotation)[1:]
        if any(_strict_on(meta) for meta in metas):
            return {
                "bool": "StrictBool",
                "int": "StrictInt",
                "float": "StrictFloat",
            }.get(inner, f"Strict[{inner}]")
        return inner
    if _is_union(annotation):
        parts: list[str] = []
        optional = False
        for branch in get_args(annotation):
            if branch is type(None):
                optional = True
            else:
                parts.append(annotation_label(branch))
        text = " | ".join(parts)
        if optional:
            text += " | None"
        return text
    if origin is Literal:
        values = ", ".join(repr(value) for value in get_args(annotation))
        return f"Literal[{values}]"
    if origin is list:
        args = get_args(annotation)
        return f"list[{annotation_label(args[0])}]" if args else "list"
    if origin is dict:
        args = get_args(annotation)
        if len(args) == 2:
            return (
                f"dict[{annotation_label(args[0])}, {annotation_label(args[1])}]"
            )
        return "dict"
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation).replace("typing.", "")


@dataclass(frozen=True)
class ParamRecord:
    tool: str
    name: str
    annotation: str
    coercible: bool


def iter_registered_tools(app: Any) -> list[Any]:
    """Caller-facing registry. list_tools() is the set that would go stale."""
    return list(app._tool_manager.list_tools())


def census_parameters(app: Any) -> list[ParamRecord]:
    records: list[ParamRecord] = []
    for tool in sorted(iter_registered_tools(app), key=lambda item: item.name):
        hints = typing.get_type_hints(tool.fn, include_extras=True)
        signature = inspect.signature(tool.fn)
        for name, param in signature.parameters.items():
            if name in {"self", "cls"}:
                continue
            annotation = hints.get(name, param.annotation)
            if _is_context(annotation):
                continue
            records.append(
                ParamRecord(
                    tool=tool.name,
                    name=name,
                    annotation=annotation_label(annotation),
                    coercible=annotation_is_wire_coercible(annotation),
                )
            )
    return records


def _build_app(*, enable_exec_enforce: bool = True):
    """Census the enableable surface too; exec_enforce is off in production."""
    return build_app(
        ServerConfig(
            key="k",
            port=0,
            log_sink=lambda _message: None,
            enable_exec_enforce=enable_exec_enforce,
        )
    )


_TIMEOUT_S_COMMENT = (
    "False becomes 0.0 then the >0 timeout bound rejects it; remaining values "
    "are a duration, not a guard"
)

# Tools whose timeout_s goes through _timeout / an equivalent >0 bound.
# A new tool with timeout_s is NOT auto-allowed: add it here with eyes open.
_TIMEOUT_S_TOOLS = frozenset(
    {
        "action_use",
        "camera_get",
        "camera_set",
        "engine_set",
        "entities_query",
        "exec_enforce",
        "infected_drive",
        "inventory_give",
        "key_press",
        "notify_players",
        "object_anim",
        "object_delete",
        "object_inspect",
        "player_respawn",
        "player_teleport",
        "query_all_players",
        "query_get_in_condition",
        "query_player_state",
        "restore_gameplay",
        "scene_raycast",
        "surface_query",
        "telemetry_read",
        "ui_click",
        "ui_dialog",
        "ui_focus",
        "ui_reload_layout",
        "ui_set_text",
        "ui_tree",
        "vehicle_control",
        "vehicle_enter",
        "vehicle_get_in_client",
        "vehicle_prepare_fixture",
        "vehicle_release",
        "vehicle_telemetry",
        "vehicle_trace",
        "wait_for",
        "world_spawn",
        "world_time_set",
        "world_weather_set",
    }
)

_FREE_TEXT = "free text; pydantic 2 rejects bool/int, and any string is valid here"
_OPTIONAL_ID = "optional id string; omit/None means default, any text is an identifier"
_COUNT = "any integer in the handler's range is a count, not a permission"
_WORLD_FLOAT = "any finite number is a world magnitude here"
_ENUM_LIKE_STR = (
    "bare str with a handler allowlist; False/0 are rejected, leftover values "
    "are names not switches"
)

# Every currently coercible (tool, param) must appear here. The comment is why
# THIS parameter's coercion is accepted as the past; a new key has no comment
# and the ratchet fails. Defects stay listed so fixing them is a deliberate
# allowlist edit, not a silent green.
COERCIBLE_ALLOWLIST: dict[tuple[str, str], str] = {
    (tool, "timeout_s"): _TIMEOUT_S_COMMENT for tool in sorted(_TIMEOUT_S_TOOLS)
}
COERCIBLE_ALLOWLIST.update(
    {
        ("action_use", "action"): "free text Enforce action class name",
        ("action_use", "classname"): "free text target GetType()",
        ("action_use", "radius"): _WORLD_FLOAT,
        ("camera_get", "cam_mode"): _ENUM_LIKE_STR,
        ("camera_set", "cam_mode"): _ENUM_LIKE_STR,
        ("camera_set", "fov"): (
            "0 already means leave FOV unchanged; False becomes that no-op"
        ),
        ("camera_set", "settle_ticks"): _COUNT,
        ("capture_screenshot", "scale"): _ENUM_LIKE_STR,
        ("capture_screenshot", "max_tokens"): (
            "budget clamp; False becomes 0 and the resolver treats <=0 as the cap"
        ),
        ("capture_screenshot", "frames"): _COUNT,
        ("capture_screenshot", "process_name"): "free text process name",
        ("capture_screenshot", "fmt"): _ENUM_LIKE_STR,
        ("capture_screenshot", "quality"): _COUNT,
        ("capture_screenshot", "crop"): "free text crop spec",
        ("capture_screenshot", "crop_space"): _ENUM_LIKE_STR,
        ("capture_screenshot", "save_fullres"): (
            "defect: 1/'true' writes a native frame to disk; inventoried until StrictBool"
        ),
        ("capture_screenshot", "save_dir"): "free text directory",
        ("dayz_knowledge_find", "query"): "free text search substring",
        ("dayz_knowledge_show", "name"): "free text symbol name",
        ("dayz_test_run", "project"): "free text project name",
        ("dayz_test_run", "mode"): (
            "bare str gated by a wrapper enum before pydantic; leftover values are names"
        ),
        ("dayz_test_run", "mission"): "free text mission alias",
        ("dayz_test_run", "build"): (
            "defect: 1/'true' authorizes a pack; inventoried until StrictBool"
        ),
        ("dayz_test_run", "clean"): (
            "defect: 1/'true' authorizes a clean; inventoried until StrictBool"
        ),
        ("dayz_test_run", "pack_only"): (
            "defect: 1/'true' authorizes pack-only; inventoried until StrictBool"
        ),
        ("dayz_test_run", "preflight"): (
            "defect: 1/'true' authorizes preflight; inventoried until StrictBool"
        ),
        ("dayz_test_run", "run_id"): _OPTIONAL_ID,
        ("dayz_test_run", "no_base_mods"): (
            "defect: 1/'true' drops base mods; inventoried until StrictBool"
        ),
        ("dayz_test_run", "no_file_patching"): (
            "defect: 1/'true' disables filepatching; inventoried until StrictBool"
        ),
        ("dayz_test_run", "port"): "any port integer in range is a bind, not a guard",
        ("dayz_test_run", "width"): _COUNT,
        ("dayz_test_run", "height"): _COUNT,
        ("dayz_test_run", "player_name"): "free text player name",
        ("dayz_test_run", "server_wait_s"): (
            "seconds to wait; False becomes 0 which is a short wait, not a disabled guard"
        ),
        ("dayz_test_run", "wait_for_box_s"): (
            "defect: False becomes 0.0 and skips the box wait; the bool parse never "
            "runs. Same shape as the original budget. Inventoried until StrictFloat"
        ),
        ("dayz_test_stop", "run_id"): "free text run id",
        ("engine_set", "mode"): _ENUM_LIKE_STR,
        ("entities_query", "radius"): _WORLD_FLOAT,
        ("entities_query", "limit"): _COUNT,
        ("exec_enforce", "expr"): (
            "free text Enforce expression; allowlist runs after pydantic"
        ),
        ("exec_enforce", "main_fn"): (
            "free text entry function name; empty is the default"
        ),
        ("infected_drive", "type"): "free text classname",
        ("infected_drive", "heading"): (
            "defect: False becomes 0.0 degrees and drives; inventoried until StrictFloat"
        ),
        ("infected_drive", "speed"): (
            "defect: False becomes 0.0 speed and drives; inventoried until StrictFloat"
        ),
        ("infected_drive", "mode"): _OPTIONAL_ID,
        ("inventory_give", "classname"): "free text classname",
        ("inventory_give", "dest"): _ENUM_LIKE_STR,
        ("inventory_give", "uid"): "free text player id, empty means first human",
        ("lease_acquire", "purpose"): "free text purpose",
        ("lease_acquire", "max_wait_s"): (
            "False becomes 0.0 then the range rejects it; remaining values are a wait"
        ),
        ("logs_since", "marker"): (
            "str | dict cursor; a bool is rejected, but the bare-str arm "
            "keeps this key coercible so a numeric branch cannot hide"
        ),
        ("logs_since", "max_lines"): _COUNT,
        ("logs_since", "run_id"): _OPTIONAL_ID,
        ("notify_players", "show_time"): (
            "False becomes 0.0 then the >0 bound rejects it"
        ),
        ("notify_players", "title"): "free text title",
        ("notify_players", "detail"): "free text body",
        ("notify_players", "icon"): "free text icon",
        ("notify_players", "uid"): "free text player id, empty means broadcast",
        ("object_anim", "source"): "free text source",
        ("object_anim", "type"): "free text classname",
        ("object_anim", "phase"): (
            "defect: False becomes 0.0 and WRITES phase 0; same float|None as the "
            "original budget. Inventoried until StrictFloat"
        ),
        ("object_anim", "object_id"): (
            "False becomes 0 (no object_id); True would name object 1"
        ),
        ("object_delete", "object_id"): (
            "False becomes 0 and the positive-int check rejects it; True would name 1"
        ),
        ("object_inspect", "type"): "free text classname",
        ("object_inspect", "object_id"): (
            "False becomes 0 (untargeted); True would name object 1"
        ),
        ("pipeline_feedback", "title"): "free text title",
        ("pipeline_feedback", "body"): "free text body",
        ("pipeline_feedback", "project"): "free text project",
        ("pipeline_inbox", "limit"): _COUNT,
        ("pipeline_inbox", "kind"): "free text kind filter",
        ("pipeline_inbox", "include_resolved"): (
            "1 includes resolved rows; a filter, not a safety guard"
        ),
        ("pipeline_resolve", "feedback_id"): "free text id",
        ("pipeline_resolve", "resolution"): "free text resolution",
        ("pipeline_resolve", "evidence_ref"): _OPTIONAL_ID,
        ("playbook_run", "name"): "free text playbook name",
        ("player_teleport", "uid"): "free text player id, empty means first human",
        ("player_teleport", "skip_clearance_check"): (
            "defect: 1 and 'true' skip the covered-column probe; the isinstance(bool) "
            "check runs after pydantic already made a bool. Inventoried until StrictBool"
        ),
        ("query_get_in_condition", "component"): (
            "seat index; False becomes 0, which is a real component not a disabled probe"
        ),
        ("scene_raycast", "method"): _ENUM_LIKE_STR,
        ("scene_raycast", "ignore"): _ENUM_LIKE_STR,
        ("scene_raycast", "radius"): _WORLD_FLOAT,
        ("scene_raycast", "intersect"): _ENUM_LIKE_STR,
        ("session_acquire", "purpose"): "free text purpose",
        ("session_acquire_wait", "purpose"): "free text purpose",
        ("session_acquire_wait", "max_wait_s"): (
            "False becomes 0.0 then the range rejects it; remaining values are a wait"
        ),
        ("session_cancel", "ticket"): "free text ticket",
        ("session_heartbeat", "lease_token"): "free text token",
        ("session_release", "lease_token"): "free text token",
        ("session_wait", "ticket"): "free text ticket",
        ("session_wait", "timeout_s"): (
            "False becomes 0.0 which this wait accepts as an immediate return"
        ),
        ("surface_query", "x"): _WORLD_FLOAT,
        ("surface_query", "z"): _WORLD_FLOAT,
        ("telemetry_read", "mode"): _ENUM_LIKE_STR,
        ("telemetry_read", "type"): "free text classname",
        ("telemetry_read", "radius"): _WORLD_FLOAT,
        ("telemetry_read", "path"): "free text path",
        ("telemetry_read", "max_lines"): _COUNT,
        ("ui_click", "path"): "free text widget path",
        ("ui_click", "root"): "free text root name",
        ("ui_dialog", "title"): "free text title",
        ("ui_dialog", "message"): "free text message",
        ("ui_focus", "path"): "free text widget path",
        ("ui_focus", "root"): "free text root name",
        ("ui_reload_layout", "path"): "free text layout path",
        ("ui_reload_layout", "limit"): _COUNT,
        ("ui_set_text", "path"): "free text widget path",
        ("ui_set_text", "text"): "free text widget value",
        ("ui_set_text", "root"): "free text root name",
        ("ui_tree", "path"): "free text widget path",
        ("ui_tree", "limit"): _COUNT,
        ("ui_tree", "root"): "free text root name",
        ("vehicle_control", "throttle"): (
            "defect: True becomes 1.0 (full throttle); inventoried until StrictFloat"
        ),
        ("vehicle_control", "steer"): (
            "defect: True becomes 1.0 full steer; inventoried until StrictFloat"
        ),
        ("vehicle_control", "brake"): (
            "defect: True becomes 1.0 full brake; inventoried until StrictFloat"
        ),
        ("vehicle_control", "handbrake"): (
            "defect: True becomes 1.0 which this field treats as on; inventoried until StrictFloat"
        ),
        ("vehicle_control", "hold_ttl_s"): (
            "deadman seconds; False becomes 0.0 (release now), True becomes 1.0"
        ),
        ("vehicle_prepare_fixture", "type"): "free text classname",
        ("vehicle_prepare_fixture", "radius"): _WORLD_FLOAT,
        ("vehicle_trace", "mode"): _ENUM_LIKE_STR,
        ("vehicle_trace", "trace_id"): "free text trace id",
        ("vehicle_trace", "cursor"): _COUNT,
        ("vehicle_trace", "limit"): _COUNT,
        ("vehicle_trace", "sample_hz"): _COUNT,
        ("vehicle_trace", "max_samples"): _COUNT,
        ("wait_for", "marker"): (
            "str | dict cursor; a bool is rejected, but the bare-str arm "
            "keeps this key coercible so a numeric branch cannot hide"
        ),
        ("wait_for", "value"): (
            "False becomes 0, a legal player-count bound; the bool check never runs"
        ),
        ("wait_for", "pattern"): "free text substring",
        ("wait_for", "poll_interval_s"): (
            "False becomes 0.0 then the >0 bound rejects it"
        ),
        ("wait_for", "lookback_lines"): _COUNT,
        ("world_spawn", "type"): "free text classname",
        ("world_spawn", "flags"): (
            "defect: True becomes 1 and changes CreateObjectEx flags; inventoried until StrictInt"
        ),
        ("world_spawn", "rotation"): (
            "defect: True becomes 1, an RF_* flag not an angle; inventoried until StrictInt"
        ),
        ("world_time_set", "year"): _COUNT,
        ("world_time_set", "month"): _COUNT,
        ("world_time_set", "day"): _COUNT,
        ("world_time_set", "hour"): _COUNT,
        ("world_time_set", "minute"): _COUNT,
        ("world_time_set", "time_multiplier"): (
            "defect: False becomes 0.0, which the range accepts and freezes the sim; "
            "same float|None as the original budget. Inventoried until StrictFloat"
        ),
        ("world_weather_set", "overcast"): (
            "defect: False becomes 0.0 and SETS overcast to clear; inventoried until StrictFloat"
        ),
        ("world_weather_set", "rain"): (
            "defect: False becomes 0.0 and SETS rain to none; inventoried until StrictFloat"
        ),
        ("world_weather_set", "fog"): (
            "defect: False becomes 0.0 and SETS fog to none; inventoried until StrictFloat"
        ),
        ("world_weather_set", "time"): (
            "transition seconds; False becomes 0.0 (instant), which the field documents"
        ),
        ("world_weather_set", "min_duration"): (
            "hold seconds; False becomes 0.0 (no hold), which the field documents"
        ),
    }
)


def census_floor_failures(n_tools: int, n_params: int) -> list[str]:
    """Names of floor checks that failed. Empty means the census is populated."""
    failures: list[str] = []
    if n_tools < MIN_REGISTERED_TOOLS:
        failures.append("tools")
    if n_params < MIN_REGISTERED_PARAMS:
        failures.append("params")
    return failures


def _coercible_keys(records: list[ParamRecord]) -> set[tuple[str, str]]:
    return {(item.tool, item.name) for item in records if item.coercible}


def unallowlisted_coercible_keys(
    records: list[ParamRecord],
    allowlist: Mapping[tuple[str, str], str] = COERCIBLE_ALLOWLIST,
) -> list[tuple[str, str]]:
    return sorted(_coercible_keys(records) - set(allowlist))


def assert_no_unallowlisted_coercibles(
    test_case: unittest.TestCase,
    records: list[ParamRecord],
    allowlist: Mapping[tuple[str, str], str] = COERCIBLE_ALLOWLIST,
) -> None:
    """The gate check. Deleting this assertion must fail the positive control."""
    extra = unallowlisted_coercible_keys(records, allowlist)
    test_case.assertEqual(
        extra,
        [],
        "new wire-coercible parameter; add it to COERCIBLE_ALLOWLIST "
        "with a one-line reason, or tighten the annotation",
    )


def _register_probe(app: Any, name: str, annotation: object) -> None:
    def probe(value: Any = None) -> dict[str, Any]:
        return {"value": value}

    probe.__name__ = name
    probe.__annotations__ = {"value": annotation, "return": dict[str, Any]}
    app.add_tool(probe, name=name)


class _WireIntMode(enum.IntEnum):
    OFF = 0
    ON = 1


class _WireStrMode(enum.Enum):
    A = "a"
    B = "b"


class _MysteryType:
    pass


async def _arrived(app: Any, tool_name: str, arguments: dict[str, Any]) -> Any:
    """Value that crossed pydantic, captured at the Python function.

    Calling the handler directly would skip the frontier this census exists
    to measure.
    """
    tool = app._tool_manager.get_tool(tool_name)
    original = tool.fn
    seen: dict[str, Any] = {}

    async def async_spy(*args: Any, **kwargs: Any) -> dict[str, Any]:
        bound = inspect.signature(original).bind_partial(*args, **kwargs)
        bound.apply_defaults()
        seen.update(dict(bound.arguments))
        return {"ok": 1, "spy": True}

    def sync_spy(*args: Any, **kwargs: Any) -> dict[str, Any]:
        bound = inspect.signature(original).bind_partial(*args, **kwargs)
        bound.apply_defaults()
        seen.update(dict(bound.arguments))
        return {"ok": 1, "spy": True}

    wrapper = async_spy if inspect.iscoroutinefunction(original) else sync_spy
    object.__setattr__(tool, "fn", wrapper)
    try:
        await app.call_tool(tool_name, arguments)
    finally:
        object.__setattr__(tool, "fn", original)
    return seen


class AnnotationClassifierTests(unittest.TestCase):
    def test_original_budget_form_is_coercible(self) -> None:
        """The defect was ``client_start_budget_s: float | None``. Catch that form."""
        self.assertTrue(annotation_is_wire_coercible(float | None))
        self.assertTrue(annotation_is_wire_coercible(float))
        self.assertTrue(annotation_is_wire_coercible(bool))

    def test_fixed_strict_union_is_not_coercible(self) -> None:
        """The repaired annotation must not trip the detector."""
        self.assertFalse(
            annotation_is_wire_coercible(StrictFloat | StrictInt | None)
        )
        self.assertFalse(annotation_is_wire_coercible(StrictFloat))
        self.assertFalse(annotation_is_wire_coercible(StrictInt))
        self.assertFalse(annotation_is_wire_coercible(StrictBool))

    def test_string_literals_and_containers_are_not_coercible(self) -> None:
        self.assertFalse(
            annotation_is_wire_coercible(Literal["direct", "complete"])
        )
        self.assertFalse(annotation_is_wire_coercible(list[float]))
        self.assertFalse(annotation_is_wire_coercible(dict[str, float]))

    def test_union_numeric_branch_is_not_hidden(self) -> None:
        """F1: a non-converting arm does not neutralize a converting arm."""
        self.assertTrue(
            annotation_is_wire_coercible(float | dict[str, float] | None)
        )
        self.assertTrue(
            annotation_is_wire_coercible(float | Literal["off"] | None)
        )
        self.assertTrue(
            annotation_is_wire_coercible(str | dict[str, Any] | None)
        )

    def test_strict_false_metadata_is_coercible(self) -> None:
        """F2: Strict(False) disables strictness; only Strict(True) is safe."""
        self.assertTrue(
            annotation_is_wire_coercible(Annotated[float, Strict(False)])
        )
        self.assertFalse(
            annotation_is_wire_coercible(Annotated[float, Strict(True)])
        )
        self.assertEqual(
            annotation_label(Annotated[float, Strict(False)]), "float"
        )
        self.assertEqual(
            annotation_label(Annotated[float, Strict(True)]), "StrictFloat"
        )

    def test_numeric_literal_and_intenum_are_coercible(self) -> None:
        self.assertTrue(annotation_is_wire_coercible(Literal[0, 1]))
        self.assertTrue(annotation_is_wire_coercible(_WireIntMode))
        self.assertFalse(annotation_is_wire_coercible(_WireStrMode))

    def test_unknown_form_is_coercible_until_proven(self) -> None:
        self.assertTrue(annotation_is_wire_coercible(_MysteryType))


class RegistryCensusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app, _runtime = _build_app()
        cls.records = census_parameters(cls.app)
        cls.tools = iter_registered_tools(cls.app)
        cls.app_default, _default_runtime = _build_app(enable_exec_enforce=False)

    def test_census_uses_the_live_registry_not_a_hand_list(self) -> None:
        names = {tool.name for tool in self.tools}
        self.assertIn("player_teleport", names)
        self.assertIn("dayz_test_run", names)
        self.assertIn("exec_enforce", names)
        self.assertGreaterEqual(len(names), MIN_REGISTERED_TOOLS)
        self.assertGreaterEqual(len(self.records), MIN_REGISTERED_PARAMS)

    def test_empty_census_is_not_a_pass(self) -> None:
        """Zero tools/params is the false-negative of a broken enumerator."""
        self.assertEqual(census_floor_failures(0, 0), ["tools", "params"])
        self.assertEqual(
            census_floor_failures(len(self.tools), len(self.records)),
            [],
        )

    def test_new_coercible_parameter_is_not_silently_accepted(self) -> None:
        assert_no_unallowlisted_coercibles(self, self.records)
        missing = sorted(set(COERCIBLE_ALLOWLIST) - _coercible_keys(self.records))
        self.assertEqual(
            missing,
            [],
            "allowlisted parameter vanished (fixed, renamed, or census broke); "
            "edit COERCIBLE_ALLOWLIST",
        )

    def test_gate_uses_the_shared_ratchet_check(self) -> None:
        src = inspect.getsource(
            RegistryCensusTests.test_new_coercible_parameter_is_not_silently_accepted
        )
        self.assertIn("assert_no_unallowlisted_coercibles", src)

    def test_allowlist_comments_are_present(self) -> None:
        for key, comment in COERCIBLE_ALLOWLIST.items():
            with self.subTest(key=key):
                self.assertTrue(str(comment).strip(), key)

    def test_live_float_or_none_is_flagged(self) -> None:
        """Same annotation the original budget had, still on the live surface."""
        flagged = {
            (item.tool, item.name)
            for item in self.records
            if item.coercible and item.annotation == "float | None"
        }
        self.assertIn(("world_time_set", "time_multiplier"), flagged)
        self.assertIn(("object_anim", "phase"), flagged)

    def test_repaired_budget_is_not_flagged(self) -> None:
        live = _coercible_keys(self.records)
        self.assertNotIn(("dayz_test_run", "client_start_budget_s"), live)
        self.assertNotIn(("dayz_test_run", "auto_remediate_steam"), live)
        self.assertNotIn(("key_press", "dik"), live)
        self.assertNotIn(("ui_click", "button"), live)
        self.assertNotIn(("ui_click", "bubble"), live)

    def test_skip_clearance_check_is_in_the_census(self) -> None:
        live = _coercible_keys(self.records)
        self.assertIn(("player_teleport", "skip_clearance_check"), live)

    def test_unlisted_coercible_on_a_real_registry_fails_the_gate_check(self) -> None:
        """F3: contaminate the live registry; the gate check itself must fail.

        Deleting assertEqual(extra, [], ...) inside
        assert_no_unallowlisted_coercibles must turn this test red.
        """

        def probe_budget(
            client_start_budget_s: float | None = None,
        ) -> dict[str, Any]:
            return {"client_start_budget_s": client_start_budget_s}

        self.app.add_tool(probe_budget, name="probe_budget")
        try:
            records = census_parameters(self.app)
            with self.assertRaises(AssertionError) as ctx:
                assert_no_unallowlisted_coercibles(self, records)
            text = str(ctx.exception)
            self.assertIn("probe_budget", text)
            self.assertIn("client_start_budget_s", text)
        finally:
            self.app.remove_tool("probe_budget")

    def test_exec_enforce_surface_is_in_the_census(self) -> None:
        """F4: registering the tool is enough; the census must not skip it."""
        live = _coercible_keys(self.records)
        self.assertIn(("exec_enforce", "expr"), live)
        self.assertIn(("exec_enforce", "main_fn"), live)
        self.assertIn(("exec_enforce", "timeout_s"), live)
        default_names = {
            tool.name for tool in iter_registered_tools(self.app_default)
        }
        self.assertNotIn("exec_enforce", default_names)
        self.assertEqual(
            {tool.name for tool in self.tools} - default_names,
            {"exec_enforce"},
        )

    def test_conditional_registry_flags_are_inventoried(self) -> None:
        src = inspect.getsource(build_app)
        found = tuple(sorted(set(re.findall(r"config\.(enable_\w+)", src))))
        self.assertEqual(found, CONDITIONAL_TOOL_REGISTRY_FLAGS)
        self.assertEqual(found, ("enable_exec_enforce",))


class WireCoercionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, self.runtime = _build_app()

    async def test_skip_clearance_check_coerces_on_the_wire(self) -> None:
        """false stays false; 1 and 'true' become True and skip the probe.

        The isinstance(bool) guard in the handler never sees 1: pydantic
        already made a bool. Measured through call_tool, not the fn.
        """
        pos = [1.0, 0.0, 3.0]
        arrived_false = await _arrived(
            self.app,
            "player_teleport",
            {"pos": pos, "skip_clearance_check": False},
        )
        arrived_zero = await _arrived(
            self.app,
            "player_teleport",
            {"pos": pos, "skip_clearance_check": 0},
        )
        arrived_one = await _arrived(
            self.app,
            "player_teleport",
            {"pos": pos, "skip_clearance_check": 1},
        )
        arrived_true_str = await _arrived(
            self.app,
            "player_teleport",
            {"pos": pos, "skip_clearance_check": "true"},
        )
        self.assertIs(arrived_false["skip_clearance_check"], False)
        self.assertIs(arrived_zero["skip_clearance_check"], False)
        self.assertIs(arrived_one["skip_clearance_check"], True)
        self.assertIs(arrived_true_str["skip_clearance_check"], True)
        with self.assertRaises(ToolError):
            await _arrived(
                self.app,
                "player_teleport",
                {"pos": pos, "skip_clearance_check": "5"},
            )

    async def test_skip_clearance_one_bypasses_the_probe(self) -> None:
        calls: list[str] = []

        async def spy(cmd: str, args: dict[str, Any], peer: str, timeout_s: float):
            calls.append(cmd)
            if cmd == "surface_query":
                return {"ok": 1, "y": 10.0}
            if cmd == "scene_raycast":
                return {
                    "ok": 1,
                    "raycast": {
                        "hit": 1,
                        "pos": [1.0, 20.0, 3.0],
                        "object_type": "Roof",
                    },
                }
            if cmd == "player_teleport":
                return {"ok": 1}
            raise AssertionError(cmd)

        with patch.object(self.runtime, "call_bridge", spy):
            await self.app.call_tool(
                "player_teleport",
                {"pos": [1.0, 0.0, 3.0], "skip_clearance_check": 1},
            )
        self.assertEqual(calls, ["player_teleport"])

        calls.clear()
        with patch.object(self.runtime, "call_bridge", spy):
            await self.app.call_tool(
                "player_teleport",
                {"pos": [1.0, 0.0, 3.0], "skip_clearance_check": False},
            )
        self.assertEqual(calls, ["surface_query", "scene_raycast"])

    async def test_time_multiplier_false_arrives_as_zero(self) -> None:
        payload = {
            "year": 2020,
            "month": 1,
            "day": 1,
            "hour": 12,
            "minute": 0,
        }
        arrived = await _arrived(
            self.app,
            "world_time_set",
            {**payload, "time_multiplier": False},
        )
        self.assertEqual(arrived["time_multiplier"], 0.0)
        arrived_str = await _arrived(
            self.app,
            "world_time_set",
            {**payload, "time_multiplier": "5"},
        )
        self.assertEqual(arrived_str["time_multiplier"], 5.0)
        arrived_zero = await _arrived(
            self.app,
            "world_time_set",
            {**payload, "time_multiplier": 0},
        )
        self.assertEqual(arrived_zero["time_multiplier"], 0.0)

        seen: list[dict[str, Any]] = []

        async def spy(cmd: str, args: dict[str, Any], peer: str, timeout_s: float):
            seen.append(args)
            return {"ok": 1, "applied": dict(args)}

        with patch.object(self.runtime, "call_bridge", spy):
            await self.app.call_tool(
                "world_time_set",
                {**payload, "time_multiplier": False},
            )
        self.assertEqual(seen[0]["time_multiplier"], 0.0)

    async def test_wait_for_box_s_false_arrives_as_zero(self) -> None:
        arrived = await _arrived(
            self.app,
            "dayz_test_run",
            {"project": "ExampleMod", "mode": "server", "wait_for_box_s": False},
        )
        self.assertEqual(arrived["wait_for_box_s"], 0.0)
        arrived_str = await _arrived(
            self.app,
            "dayz_test_run",
            {"project": "ExampleMod", "mode": "server", "wait_for_box_s": "5"},
        )
        self.assertEqual(arrived_str["wait_for_box_s"], 5.0)
        arrived_zero = await _arrived(
            self.app,
            "dayz_test_run",
            {"project": "ExampleMod", "mode": "server", "wait_for_box_s": 0},
        )
        self.assertEqual(arrived_zero["wait_for_box_s"], 0.0)

    async def test_phase_float_or_none_coerces_like_the_original_budget(self) -> None:
        arrived = await _arrived(
            self.app, "object_anim", {"source": "x", "phase": False}
        )
        self.assertEqual(arrived["phase"], 0.0)
        arrived_str = await _arrived(
            self.app, "object_anim", {"source": "x", "phase": "5"}
        )
        self.assertEqual(arrived_str["phase"], 5.0)
        arrived_zero = await _arrived(
            self.app, "object_anim", {"source": "x", "phase": 0}
        )
        self.assertEqual(arrived_zero["phase"], 0.0)

    async def test_timeout_false_fail_closes_instead_of_disabling_a_guard(self) -> None:
        """Neighbor of wait_for_box_s: same bare float, False does not sneak through."""
        with self.assertRaises(ToolError) as ctx:
            await self.app.call_tool("query_player_state", {"timeout_s": False})
        self.assertIn("bad_timeout", str(ctx.exception))
        with self.assertRaises(ToolError) as ctx_zero:
            await self.app.call_tool("query_player_state", {"timeout_s": 0})
        self.assertIn("bad_timeout", str(ctx_zero.exception))

    async def test_strict_budget_rejects_false_and_numeric_strings(self) -> None:
        for bad in (False, True, "5"):
            with self.subTest(bad=bad), self.assertRaises(ToolError):
                await _arrived(
                    self.app,
                    "dayz_test_run",
                    {
                        "project": "ExampleMod",
                        "mode": "server",
                        "client_start_budget_s": bad,
                    },
                )

    async def test_strict_dik_rejects_false(self) -> None:
        with self.assertRaises(ToolError):
            await _arrived(self.app, "key_press", {"dik": False})
        arrived = await _arrived(self.app, "key_press", {"dik": 0})
        self.assertEqual(arrived["dik"], 0)

    async def test_union_numeric_branch_false_arrives_as_zero(self) -> None:
        """F1 on the wire: dict/Literal arms do not stop float from converting."""
        cases = (
            ("probe_union_dict", float | dict[str, float] | None),
            ("probe_union_lit", float | Literal["off"] | None),
        )
        for name, annotation in cases:
            with self.subTest(name=name):
                app = FastMCP("wire-census")
                _register_probe(app, name, annotation)
                self.assertTrue(annotation_is_wire_coercible(annotation), name)
                seen = await _arrived(app, name, {"value": False})
                self.assertEqual(seen["value"], 0.0)
                self.assertIs(type(seen["value"]), float)

    async def test_strict_false_metadata_false_arrives_as_zero(self) -> None:
        """F2 on the wire: Strict(False) converts; Strict(True) rejects."""
        open_ann = Annotated[float, Strict(False)]
        closed_ann = Annotated[float, Strict(True)]
        self.assertTrue(annotation_is_wire_coercible(open_ann))
        self.assertFalse(annotation_is_wire_coercible(closed_ann))
        app = FastMCP("wire-census")
        _register_probe(app, "probe_strict_off", open_ann)
        _register_probe(app, "probe_strict_on", closed_ann)
        seen = await _arrived(app, "probe_strict_off", {"value": False})
        self.assertEqual(seen["value"], 0.0)
        with self.assertRaises(ToolError):
            await _arrived(app, "probe_strict_on", {"value": False})

    async def test_classifier_is_not_more_permissive_than_the_wire(self) -> None:
        """Fail-open guard: if false converts, the classifier must say so."""
        forms: list[tuple[str, object]] = [
            ("form_float_none", float | None),
            ("form_float_dict_none", float | dict[str, float] | None),
            ("form_float_literal_none", float | Literal["off"] | None),
            ("form_strict_false", Annotated[float, Strict(False)]),
            ("form_strict_true", Annotated[float, Strict(True)]),
            ("form_strictfloat", StrictFloat),
            ("form_strict_union", StrictFloat | StrictInt | None),
            ("form_literal_str", Literal["direct", "complete"]),
            ("form_literal_int", Literal[0, 1]),
            ("form_int_enum", _WireIntMode),
            ("form_str_enum", _WireStrMode),
            ("form_list", list[float]),
            ("form_str_dict_none", str | dict[str, Any] | None),
        ]
        app = FastMCP("wire-census")
        for name, annotation in forms:
            _register_probe(app, name, annotation)
        for name, annotation in forms:
            with self.subTest(name=name):
                classified = annotation_is_wire_coercible(annotation)
                try:
                    seen = await _arrived(app, name, {"value": False})
                except Exception:
                    converted = False
                    arrived = None
                else:
                    arrived = seen.get("value")
                    if arrived is False:
                        converted = False
                    elif isinstance(arrived, enum.Enum):
                        converted = True
                    elif type(arrived) in (int, float):
                        converted = True
                    else:
                        converted = True
                if converted and not classified:
                    self.fail(
                        f"{name}: false arrived as {arrived!r} "
                        f"({type(arrived).__name__}) but classifier said safe"
                    )


if __name__ == "__main__":
    unittest.main()
