"""ui_dialog tool/bridge contract (phase 1: Python validation only).

Phase 1 sends ``fields`` as an array of objects. If Enforce cannot
deserialize ``array<ref T>`` as MCPArgs input, flatten here into
parallel arrays (field_ids, field_labels, field_required,
field_defaults) without changing the tool signature.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any


KINDS = frozenset({"acknowledge", "confirm", "form"})
TERMINAL_STATES = frozenset(
    {"completed", "cancelled", "timed_out", "disconnected", "rejected"}
)
TITLE_MIN_CHARS = 1
TITLE_MAX_CHARS = 80
MESSAGE_MAX_CHARS = 600
FIELDS_MIN = 1
FIELDS_MAX = 6
LABEL_MIN_CHARS = 1
LABEL_MAX_CHARS = 60
DEFAULT_MAX_CHARS = 256
TIMEOUT_MIN_S = 5.0
TIMEOUT_MAX_S = 240.0
DEFAULT_TIMEOUT_S = 60.0
BRIDGE_SLACK_S = 10.0
FIELD_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
ALLOWED_ARG_KEYS = frozenset({"kind", "title", "message", "fields", "timeout_s"})
ALLOWED_FIELD_KEYS = frozenset({"id", "label", "required", "default"})
REQUIRED_FIELD_KEYS = frozenset({"id", "label"})


class UiDialogError(ValueError):
    """Validation or bridge-result contract failure (message is public)."""


@dataclass(frozen=True)
class FieldSpec:
    id: str
    label: str
    required: bool
    default: str


@dataclass(frozen=True)
class UiDialogRequest:
    kind: str
    title: str
    message: str
    fields: tuple[FieldSpec, ...] | None
    timeout_s: float

    @property
    def field_ids(self) -> tuple[str, ...]:
        if self.fields is None:
            return ()
        return tuple(field.id for field in self.fields)


def bridge_wait_budget_s(timeout_s: float) -> float:
    """Python wait budget: player timeout plus slack, always below MAX_TIMEOUT_S."""
    return float(timeout_s) + BRIDGE_SLACK_S


def _bad(field: str, reason: str) -> None:
    raise UiDialogError(f"bad_args: {field} {reason}")


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        _bad(field, "must be a string")
    return value


def parse_request(
    kind: object,
    title: object,
    message: object = "",
    fields: object = None,
    timeout_s: object = DEFAULT_TIMEOUT_S,
) -> UiDialogRequest:
    if not isinstance(kind, str) or kind not in KINDS:
        raise UiDialogError(
            "bad_args: kind must be acknowledge, confirm, or form"
        )
    kind_text = kind

    title_text = _require_str(title, "title").strip()
    if not TITLE_MIN_CHARS <= len(title_text) <= TITLE_MAX_CHARS:
        _bad("title", f"must be {TITLE_MIN_CHARS}..{TITLE_MAX_CHARS} chars")

    message_text = _require_str(message, "message")
    if len(message_text) > MESSAGE_MAX_CHARS:
        _bad("message", f"must be 0..{MESSAGE_MAX_CHARS} chars")
    if kind_text in {"acknowledge", "confirm"} and message_text.strip() == "":
        _bad("message", f"is required for kind {kind_text}")

    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
        _bad("timeout_s", f"must be a finite number in {TIMEOUT_MIN_S}..{TIMEOUT_MAX_S}")
    try:
        timeout_value = float(timeout_s)
    except (TypeError, ValueError, OverflowError) as exc:
        raise UiDialogError(
            f"bad_args: timeout_s must be a finite number in {TIMEOUT_MIN_S}..{TIMEOUT_MAX_S}"
        ) from exc
    if not math.isfinite(timeout_value) or not TIMEOUT_MIN_S <= timeout_value <= TIMEOUT_MAX_S:
        _bad("timeout_s", f"must be a finite number in {TIMEOUT_MIN_S}..{TIMEOUT_MAX_S}")

    if fields is not None and kind_text != "form":
        _bad("fields", "must be absent unless kind is form")
    parsed_fields: tuple[FieldSpec, ...] | None = None
    if kind_text == "form":
        if fields is None:
            _bad("fields", "required when kind is form")
        if not isinstance(fields, list):
            _bad("fields", "must be a list")
        if not FIELDS_MIN <= len(fields) <= FIELDS_MAX:
            _bad("fields", f"has {len(fields)} items, max {FIELDS_MAX}" if len(fields) > FIELDS_MAX else f"has {len(fields)} items, min {FIELDS_MIN}")
        parsed_fields = tuple(
            _parse_field(index, item) for index, item in enumerate(fields)
        )
        seen: dict[str, int] = {}
        for index, field in enumerate(parsed_fields):
            previous = seen.get(field.id)
            if previous is not None:
                raise UiDialogError(
                    f"bad_args: fields[{index}].id duplicates fields[{previous}].id"
                )
            seen[field.id] = index

    return UiDialogRequest(
        kind=kind_text,
        title=title_text,
        message=message_text,
        fields=parsed_fields,
        timeout_s=timeout_value,
    )


def _parse_field(index: int, item: object) -> FieldSpec:
    prefix = f"fields[{index}]"
    if not isinstance(item, dict):
        _bad(prefix, "must be an object")
    unknown = [key for key in item if key not in ALLOWED_FIELD_KEYS]
    if unknown:
        key = sorted(str(entry) for entry in unknown)[0]
        raise UiDialogError(f"bad_args: unknown key '{key}' in {prefix}")
    missing = sorted(key for key in REQUIRED_FIELD_KEYS if key not in item)
    if missing:
        _bad(f"{prefix}.{missing[0]}", "is required")

    field_id = _require_str(item.get("id"), f"{prefix}.id").strip()
    if not FIELD_ID_RE.fullmatch(field_id):
        _bad(f"{prefix}.id", "must match ^[a-z][a-z0-9_]{0,31}$")

    label = _require_str(item.get("label"), f"{prefix}.label").strip()
    if not LABEL_MIN_CHARS <= len(label) <= LABEL_MAX_CHARS:
        _bad(
            f"{prefix}.label",
            f"must be {LABEL_MIN_CHARS}..{LABEL_MAX_CHARS} chars",
        )

    if "required" not in item:
        required = True
    else:
        required_value = item.get("required")
        if not isinstance(required_value, bool):
            _bad(f"{prefix}.required", "must be a bool")
        required = required_value

    if "default" not in item:
        default = ""
    else:
        default = _require_str(item.get("default"), f"{prefix}.default")
        if len(default) > DEFAULT_MAX_CHARS:
            _bad(f"{prefix}.default", f"must be 0..{DEFAULT_MAX_CHARS} chars")

    return FieldSpec(id=field_id, label=label, required=required, default=default)


def validate_command_args(args: dict[str, Any]) -> tuple[bool, str | None]:
    """Daemon-path check. Returns the fixed token ``bad_args`` (no caller text).

    The detailed ``bad_args: <field> …`` message stays on the MCP tool path.
    A long token here would become ``abort_reason`` and collapse to
    ``remote_error`` via ``_public_enqueue_error``.
    """
    if not isinstance(args, dict):
        return False, "bad_args"
    try:
        unknown = [key for key in args if key not in ALLOWED_ARG_KEYS]
        if unknown:
            return False, "bad_args"
        parse_request(
            args.get("kind"),
            args.get("title"),
            args.get("message", ""),
            args.get("fields"),
            args.get("timeout_s", DEFAULT_TIMEOUT_S),
        )
    except (UiDialogError, TypeError):
        return False, "bad_args"
    return True, None


def bridge_args(request: UiDialogRequest) -> dict[str, Any]:
    """Map validated tool args to the bridge command payload.

    Phase 1 sends ``fields`` as an array of objects (preferred). Cycle 6
    may flatten that array here if Enforce input cannot carry
    ``array<ref T>``.
    """
    payload: dict[str, Any] = {
        "kind": request.kind,
        "title": request.title,
        "message": request.message,
        "timeout_s": request.timeout_s,
    }
    if request.fields is not None:
        payload["fields"] = [
            {
                "id": field.id,
                "label": field.label,
                "required": field.required,
                "default": field.default,
            }
            for field in request.fields
        ]
    return payload


def _bridge_bad(reason: str) -> None:
    raise UiDialogError(f"bridge_bad_result: {reason}")


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    return number


def interpret_result(request: UiDialogRequest, result: dict[str, Any]) -> dict[str, Any]:
    """Validate a nested ``dialog`` wire object and flatten it for the agent.

    Wire: ``{ok, dialog: {state, …}, id?, _server?}``. Public: the previous
    flat shape. ``MCPResult.state`` is a player-state object, so the enum
    cannot live at the top level.
    """
    try:
        return _interpret_dialog_result(request, result)
    except TypeError as exc:
        raise UiDialogError("bridge_bad_result: invalid dialog field") from exc


def _interpret_dialog_result(
    request: UiDialogRequest, result: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(result, dict):
        _bridge_bad("result is not an object")
    if not result.get("ok"):
        _bridge_bad("ok is not true")

    dialog = result.get("dialog")
    if not isinstance(dialog, dict):
        _bridge_bad("dialog missing")

    state = dialog.get("state")
    if not isinstance(state, str) or state not in TERMINAL_STATES:
        _bridge_bad(f"state {state!r} is not a terminal enum value")

    elapsed = _finite_number(dialog.get("elapsed_s"))
    if elapsed is None:
        _bridge_bad("elapsed_s must be a finite number")

    values = dialog.get("values")
    choice = dialog.get("choice")
    dismissed_by = dialog.get("dismissed_by")
    reason = dialog.get("reason")

    if state == "completed":
        if request.kind == "acknowledge":
            if dismissed_by != "ok":
                _bridge_bad("completed acknowledge requires dismissed_by='ok'")
            if choice is not None or values is not None:
                _bridge_bad("completed acknowledge must not include choice or values")
        elif request.kind == "confirm":
            if not isinstance(choice, str) or choice not in {"yes", "no"}:
                _bridge_bad("completed confirm requires choice 'yes' or 'no'")
            if values is not None or dismissed_by is not None:
                _bridge_bad("completed confirm must not include values or dismissed_by")
        else:
            _validate_completed_values(request, values)
            if choice is not None or dismissed_by is not None:
                _bridge_bad("completed form must not include choice or dismissed_by")
    elif state == "rejected":
        if reason != "busy":
            _bridge_bad("rejected requires reason='busy'")
        if values is not None or choice is not None:
            _bridge_bad("rejected must not include values or choice")
    else:
        # cancelled / timed_out / disconnected: valid answers, never "no", never partials
        if values is not None:
            _bridge_bad(f"{state} must not include values")
        if choice is not None:
            _bridge_bad(f"{state} must not include choice")

    output: dict[str, Any] = {
        "ok": result.get("ok"),
        "state": state,
        "elapsed_s": dialog.get("elapsed_s"),
    }
    if "id" in result:
        output["id"] = result["id"]
    if "_server" in result:
        output["_server"] = result["_server"]
    if dismissed_by is not None:
        output["dismissed_by"] = dismissed_by
    if choice is not None:
        output["choice"] = choice
    if reason is not None:
        output["reason"] = reason
    if values is not None:
        output["values"] = values
        output["values_by_id"] = {
            item["id"]: item["value"] for item in values
        }
    return output


def _validate_completed_values(request: UiDialogRequest, values: object) -> None:
    declared = request.field_ids
    if not isinstance(values, list):
        _bridge_bad("completed form requires values as a list")
    if len(values) != len(declared):
        _bridge_bad(
            f"values has {len(values)} items, expected {len(declared)} declared field ids"
        )
    for index, item in enumerate(values):
        if not isinstance(item, dict):
            _bridge_bad(f"values[{index}] must be an object")
        extra = [key for key in item if key not in {"id", "value"}]
        if extra:
            key = sorted(str(entry) for entry in extra)[0]
            _bridge_bad(f"unknown key '{key}' in values[{index}]")
        if "id" not in item or "value" not in item:
            _bridge_bad(f"values[{index}] must have id and value")
        if item.get("id") != declared[index]:
            _bridge_bad(
                f"values[{index}].id {item.get('id')!r} does not match declared {declared[index]!r}"
            )
        if not isinstance(item.get("value"), str):
            _bridge_bad(f"values[{index}].value must be a string")
