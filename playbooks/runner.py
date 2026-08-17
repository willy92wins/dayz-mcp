"""Generic playbook runner. Playbook identity lives in TOML, not in this file."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
import tomllib
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable


# Live MCP client. Host-specific paths stay in this block, not inlined below.
# Resolved from this file's own location rather than written out: playbooks/ is
# a sibling of tools/ both in the development tree and in a clone, so one
# expression covers both and the runner stops being bound to one machine.
_TOOLS = Path(__file__).resolve().parents[1] / "tools"

CONFIG: dict[str, Any] = {
    "python": str(_TOOLS / ".venv-mcp" / "Scripts" / "python.exe"),
    "args": [
        "-m",
        "dayz_mcp",
        "--client",
        "--keyfile",
        str(_TOOLS / ".dayz_mcp.key"),
        "--port",
        "8765",
        "--require-version",
        "--idle-timeout",
        "3600",
        "--client-platform",
        "claude",
    ],
    "cwd": str(_TOOLS),
    "call_timeout_s": 60.0,
}

OPS = frozenset(
    {
        "eq",
        "neq",
        "lt",
        "lte",
        "gt",
        "gte",
        "finite",
        "contains",
        "absent",
        "near",
        "min_dist_xz_gte",
    }
)
FAIL_ACTIONS = frozenset({"STOP", "WARN"})
STATUSES = frozenset({"DRAFT", "PROBED", "CALIBRATED", "FROZEN"})
PASS_OVERALL = frozenset({"PASS", "PASS_WITH_WARNINGS"})
UNCALIBRATED_REASON = "uncalibrated_gate_downgraded"

# Substitution allows one trailing +n / -n on a $ref. No free expressions.
_REF_RE = re.compile(
    r"^\$([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)"
    r"(?:\s*([+-])\s*(-?\d+(?:\.\d+)?))?$"
)
_MISSING = object()
Invoke = Callable[[str, str, Any], Any]


class SchemaError(Exception):
    """Playbook failed validation. CLI maps this to exit 2."""

    def __init__(
        self,
        message: str,
        *,
        step: str | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.step = step
        self.field = field


class ToolCallError(Exception):
    """Tool invocation failed. Becomes reason tool_error:<detail>."""


def load_playbook(path: str | Path) -> dict[str, Any]:
    with Path(path).open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise SchemaError("playbook field <root>: expected a table")
    return data


def load_fixture(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise SchemaError("fixture field <root>: expected an object")
    return data


def calibrations_by_name(playbook: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in playbook.get("calibration") or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            out[item["name"]] = item
    return out


def _schema(message: str, step: str | None = None, field: str | None = None) -> SchemaError:
    parts = []
    if step:
        parts.append(f"step {step}")
    if field:
        parts.append(f"field {field}")
    prefix = " ".join(parts)
    text = f"{prefix}: {message}" if prefix else message
    return SchemaError(text, step=step, field=field)


def _parse_ref(text: str) -> tuple[str, str | None, str | None] | None:
    match = _REF_RE.fullmatch(text.strip())
    if not match:
        return None
    return match.group(1), match.group(2), match.group(3)


def _check_ref_string(
    text: str,
    *,
    step: str,
    field: str,
    params: dict[str, Any],
    prior_steps: set[str],
) -> None:
    parsed = _parse_ref(text)
    if parsed is None:
        if "$" in text:
            raise _schema(f"unresolvable $ref {text!r}", step, field)
        return
    head = parsed[0].split(".", 1)[0]
    if head in prior_steps or head in params:
        return
    raise _schema(f"unresolvable $ref ${head}", step, field)


def _check_value_refs(
    obj: Any,
    *,
    step: str,
    field: str,
    params: dict[str, Any],
    prior_steps: set[str],
) -> None:
    if isinstance(obj, str):
        _check_ref_string(obj, step=step, field=field, params=params, prior_steps=prior_steps)
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            _check_value_refs(
                value,
                step=step,
                field=f"{field}.{key}",
                params=params,
                prior_steps=prior_steps,
            )
        return
    if isinstance(obj, list):
        for index, value in enumerate(obj):
            _check_value_refs(
                value,
                step=step,
                field=f"{field}.{index}",
                params=params,
                prior_steps=prior_steps,
            )


def validate_schema(playbook: dict[str, Any]) -> None:
    if not isinstance(playbook, dict):
        raise _schema("playbook field <root>: expected a table")
    for key in ("id", "version", "status", "requires_tools", "steps"):
        if key not in playbook:
            raise _schema(f"missing required key {key!r}", field=key)
    status = playbook.get("status")
    if status not in STATUSES:
        raise _schema(f"status must be one of {sorted(STATUSES)}", field="status")
    requires = playbook.get("requires_tools")
    if not isinstance(requires, list) or not all(isinstance(item, str) for item in requires):
        raise _schema("requires_tools must be a list of strings", field="requires_tools")
    require_set = set(requires)
    params = playbook.get("params") or {}
    if not isinstance(params, dict):
        raise _schema("params must be a table", field="params")
    cals = calibrations_by_name(playbook)
    raw_cals = playbook.get("calibration") or []
    if raw_cals and not isinstance(raw_cals, list):
        raise _schema("calibration must be an array of tables", field="calibration")
    for item in raw_cals:
        if not isinstance(item, dict) or not item.get("name"):
            raise _schema("calibration entry needs a name", field="calibration")
    steps = playbook.get("steps")
    if not isinstance(steps, list) or not steps:
        raise _schema("steps must be a non-empty array", field="steps")
    seen: list[str] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise _schema(f"steps[{index}] must be a table", field="steps")
        sid = step.get("id")
        if not isinstance(sid, str) or not sid:
            raise _schema("id is required", step=f"steps[{index}]", field="id")
        if sid in seen:
            raise _schema("duplicate id", step=sid, field="id")
        tool = step.get("tool")
        if not isinstance(tool, str) or not tool:
            raise _schema("tool is required", step=sid, field="tool")
        if tool not in require_set:
            raise _schema(f"{tool!r} not in requires_tools", step=sid, field="tool")
        prior = set(seen)
        _check_value_refs(
            step.get("args") or {},
            step=sid,
            field="args",
            params=params,
            prior_steps=prior,
        )
        expects = step.get("expect") or []
        if not isinstance(expects, list):
            raise _schema("expect must be an array", step=sid, field="expect")
        for exp_i, exp in enumerate(expects):
            if not isinstance(exp, dict):
                raise _schema("expect entry must be a table", step=sid, field="expect")
            loc = exp.get("field", f"expect[{exp_i}]")
            field_name = loc if isinstance(loc, str) else f"expect[{exp_i}]"
            op = exp.get("op")
            if op not in OPS:
                raise _schema(f"unknown operator {op!r}", step=sid, field=field_name)
            if op == "near":
                if "ref" not in exp:
                    raise _schema("near requires ref", step=sid, field=field_name)
                if "tol" not in exp and "tol_ref" not in exp:
                    raise _schema("near requires tol or tol_ref", step=sid, field=field_name)
                tol_ref = exp.get("tol_ref")
                if tol_ref is not None and tol_ref not in cals:
                    raise _schema(
                        f"unknown calibration {tol_ref!r}",
                        step=sid,
                        field="tol_ref",
                    )
            if op == "min_dist_xz_gte":
                if "origin" not in exp or "value" not in exp:
                    raise _schema(
                        "min_dist_xz_gte requires origin and value",
                        step=sid,
                        field=field_name,
                    )
            if op in {"eq", "neq", "lt", "lte", "gt", "gte", "contains"} and "value" not in exp:
                raise _schema(f"{op} requires value", step=sid, field=field_name)
            _check_value_refs(
                {k: v for k, v in exp.items() if k != "field"},
                step=sid,
                field=field_name,
                params=params,
                prior_steps=prior,
            )
        on_fail = step.get("on_fail")
        if not isinstance(on_fail, dict):
            raise _schema("on_fail is required", step=sid, field="on_fail")
        action = on_fail.get("action")
        if action not in FAIL_ACTIONS:
            raise _schema(f"on_fail.action must be STOP or WARN", step=sid, field="on_fail")
        if not isinstance(on_fail.get("reason"), str) or not on_fail.get("reason"):
            raise _schema("on_fail.reason is required", step=sid, field="on_fail")
        seen.append(sid)


def get_field(data: Any, path: str) -> Any:
    if path in {"", None}:
        return data
    current = data
    for part in str(path).split("."):
        if isinstance(current, dict):
            if part not in current:
                return _MISSING
            current = current[part]
        elif isinstance(current, (list, tuple)):
            if not part.isdigit():
                return _MISSING
            index = int(part)
            if index < 0 or index >= len(current):
                return _MISSING
            current = current[index]
        else:
            return _MISSING
    return current


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def resolve_ref(
    text: str,
    *,
    params: dict[str, Any],
    results: dict[str, Any],
    step: str,
    field: str,
) -> Any:
    parsed = _parse_ref(text)
    if parsed is None:
        if "$" in text:
            raise _schema(f"unresolvable $ref {text!r}", step, field)
        return text
    path, op, operand = parsed
    parts = path.split(".")
    head = parts[0]
    rest = ".".join(parts[1:])
    if head in results:
        value = get_field(results[head], rest) if rest else results[head]
        if value is _MISSING:
            raise _schema(f"unresolvable $ref ${path}", step, field)
    elif head in params:
        value = get_field(params[head], rest) if rest else params[head]
        if value is _MISSING:
            raise _schema(f"unresolvable $ref ${path}", step, field)
    else:
        raise _schema(f"unresolvable $ref ${head}", step, field)
    if op is None:
        return value
    number = _as_float(value)
    if number is None:
        raise _schema(f"$ref ${path} is not numeric for arithmetic", step, field)
    delta = float(operand)
    return number + delta if op == "+" else number - delta


def substitute(
    obj: Any,
    *,
    params: dict[str, Any],
    results: dict[str, Any],
    step: str,
    field: str,
) -> Any:
    if isinstance(obj, str):
        if obj.startswith("$") or _parse_ref(obj):
            return resolve_ref(obj, params=params, results=results, step=step, field=field)
        if "$" in obj:
            raise _schema(f"unresolvable $ref {obj!r}", step, field)
        return obj
    if isinstance(obj, dict):
        return {
            key: substitute(value, params=params, results=results, step=step, field=f"{field}.{key}")
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [
            substitute(value, params=params, results=results, step=step, field=f"{field}.{index}")
            for index, value in enumerate(obj)
        ]
    return obj


def _min_dist_xz(entries: Any, origin: Any, threshold: Any) -> bool:
    if entries is _MISSING or entries is None:
        return False
    if isinstance(entries, list) and len(entries) == 0:
        return True
    if not isinstance(entries, list) or not isinstance(origin, (list, tuple)) or len(origin) < 2:
        return False
    ox = _as_float(origin[0])
    oz = _as_float(origin[1])
    limit = _as_float(threshold)
    if ox is None or oz is None or limit is None:
        return False
    best: float | None = None
    for item in entries:
        if not isinstance(item, dict):
            return False
        pos = item.get("pos")
        if not isinstance(pos, (list, tuple)) or len(pos) < 3:
            return False
        px = _as_float(pos[0])
        pz = _as_float(pos[2])
        if px is None or pz is None:
            return False
        dist = math.hypot(px - ox, pz - oz)
        if best is None or dist < best:
            best = dist
    return best is not None and best >= limit


def eval_predicate(
    spec: dict[str, Any],
    observed: Any,
    *,
    params: dict[str, Any],
    results: dict[str, Any],
    calibrations: dict[str, dict[str, Any]],
    step: str,
) -> tuple[bool, bool]:
    """Return (passed, used_uncalibrated_calibration)."""
    op = spec.get("op")
    field = spec.get("field") or ""
    value = get_field(observed, field) if op != "absent" else get_field(observed, field)
    resolved = {
        key: substitute(raw, params=params, results=results, step=step, field=str(key))
        for key, raw in spec.items()
        if key not in {"op", "field", "tol_ref"}
    }
    uncalibrated = False
    if op == "near" and spec.get("tol_ref"):
        cal = calibrations.get(spec["tol_ref"])
        if cal is None:
            raise _schema(f"unknown calibration {spec['tol_ref']!r}", step, "tol_ref")
        resolved["tol"] = cal.get("threshold")
        uncalibrated = cal.get("state") == "uncalibrated"
    if op == "absent":
        return value is _MISSING, False
    if op == "finite":
        number = _as_float(value)
        return number is not None, False
    if op == "eq":
        return value != _MISSING and value == resolved.get("value"), False
    if op == "neq":
        return value != _MISSING and value != resolved.get("value"), False
    if op in {"lt", "lte", "gt", "gte"}:
        left = _as_float(value)
        right = _as_float(resolved.get("value"))
        if left is None or right is None:
            return False, False
        if op == "lt":
            return left < right, False
        if op == "lte":
            return left <= right, False
        if op == "gt":
            return left > right, False
        return left >= right, False
    if op == "contains":
        needle = resolved.get("value")
        if value is _MISSING or needle is None:
            return False, False
        try:
            return needle in value, False
        except TypeError:
            return False, False
    if op == "near":
        left = _as_float(value)
        right = _as_float(resolved.get("ref"))
        tol = _as_float(resolved.get("tol"))
        if left is None or right is None or tol is None:
            return False, False
        return abs(left - right) <= tol, uncalibrated
    if op == "min_dist_xz_gte":
        return _min_dist_xz(value, resolved.get("origin"), resolved.get("value")), False
    raise _schema(f"unknown operator {op!r}", step, field or "op")


def _step_record(
    step: dict[str, Any],
    status: str,
    observed: Any,
    reason: str | None,
) -> dict[str, Any]:
    return {
        "id": step["id"],
        "tool": step["tool"],
        "status": status,
        "observed": observed,
        "reason": reason,
    }


def _verdict_reason(steps: list[dict[str, Any]], stopped_at: str | None) -> str | None:
    if stopped_at:
        for item in steps:
            if item["id"] == stopped_at:
                return item.get("reason")
    for item in steps:
        if item["status"] == "WARN" and item.get("reason"):
            return item["reason"]
    return None


async def _async_run(
    playbook: dict[str, Any],
    params: dict[str, Any],
    invoke: Callable[[str, str, Any], Any],
    mode: str,
) -> dict[str, Any]:
    validate_schema(playbook)
    merged = dict(playbook.get("params") or {})
    merged.update(params)
    cals = calibrations_by_name(playbook)
    results: dict[str, Any] = {}
    steps_out: list[dict[str, Any]] = []
    stopped_at: str | None = None
    skipping = False

    for step in playbook["steps"]:
        sid = step["id"]
        if skipping:
            steps_out.append(_step_record(step, "SKIPPED", None, None))
            continue
        args = substitute(
            step.get("args") or {},
            params=merged,
            results=results,
            step=sid,
            field="args",
        )
        try:
            observed = invoke(sid, step["tool"], args)
            if asyncio.iscoroutine(observed):
                observed = await observed
        except SchemaError:
            raise
        except ToolCallError as exc:
            reason = f"tool_error:{exc}"
            steps_out.append(_step_record(step, "FAIL", None, reason))
            stopped_at = sid
            skipping = True
            continue
        except Exception as exc:  # noqa: BLE001 — tool failures must never pass
            reason = f"tool_error:{exc}"
            steps_out.append(_step_record(step, "FAIL", None, reason))
            stopped_at = sid
            skipping = True
            continue

        status = "PASS"
        reason: str | None = None
        for spec in step.get("expect") or []:
            passed, used_uncal = eval_predicate(
                spec,
                observed,
                params=merged,
                results={**results, sid: observed},
                calibrations=cals,
                step=sid,
            )
            if passed:
                continue
            if used_uncal:
                status = "WARN"
                reason = UNCALIBRATED_REASON
                continue
            fail = step.get("on_fail") or {}
            reason = str(fail.get("reason") or "failed")
            if fail.get("action") == "WARN":
                status = "WARN"
                break
            status = "FAIL"
            stopped_at = sid
            skipping = True
            break
        results[sid] = observed
        steps_out.append(_step_record(step, status, observed, reason))

    if stopped_at:
        overall = "FAIL"
    elif any(item["status"] == "WARN" for item in steps_out):
        overall = "PASS_WITH_WARNINGS"
    else:
        overall = "PASS"
    return {
        "playbook": playbook.get("id"),
        "version": playbook.get("version"),
        "status": playbook.get("status"),
        "mode": mode,
        "params": merged,
        "steps": steps_out,
        "overall": overall,
        "stopped_at": stopped_at,
        "reason": _verdict_reason(steps_out, stopped_at),
    }


def run_playbook(
    playbook: dict[str, Any],
    *,
    params: dict[str, Any] | None = None,
    invoke: Invoke,
    mode: str = "fixtures",
) -> dict[str, Any]:
    async def _invoke(step_id: str, tool: str, arguments: Any) -> Any:
        return invoke(step_id, tool, arguments)

    return asyncio.run(_async_run(playbook, dict(params or {}), _invoke, mode))


def run_fixture(
    playbook: dict[str, Any],
    fixture: dict[str, Any],
    *,
    param_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params = dict(fixture.get("params") or {})
    if param_overrides:
        params.update(param_overrides)
    responses = fixture.get("responses") or {}
    if not isinstance(responses, dict):
        raise _schema("fixture field responses: expected an object", field="responses")

    def invoke(step_id: str, tool: str, arguments: Any) -> Any:
        del tool, arguments
        if step_id not in responses:
            raise ToolCallError(f"missing fixture response for {step_id}")
        payload = responses[step_id]
        if isinstance(payload, dict):
            return json.loads(json.dumps(payload))
        return payload

    return run_playbook(playbook, params=params, invoke=invoke, mode="fixtures")


def fixture_matches(verdict: dict[str, Any], fixture: dict[str, Any]) -> bool:
    return (
        verdict.get("overall") == fixture.get("expect_overall")
        and verdict.get("stopped_at") == fixture.get("expect_stopped_at")
        and verdict.get("reason") == fixture.get("expect_reason")
    )


def run_fixture_dir(
    playbook: dict[str, Any],
    directory: str | Path,
    *,
    param_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paths = sorted(Path(directory).glob("*.json"))
    results = []
    matched = True
    for path in paths:
        fixture = load_fixture(path)
        verdict = run_fixture(playbook, fixture, param_overrides=param_overrides)
        ok = fixture_matches(verdict, fixture)
        matched = matched and ok
        results.append(
            {
                "file": path.name,
                "name": fixture.get("name"),
                "match": ok,
                "verdict": verdict,
            }
        )
    return {
        "playbook": playbook.get("id"),
        "version": playbook.get("version"),
        "status": playbook.get("status"),
        "mode": "fixtures",
        "params": dict(param_overrides or {}),
        "results": results,
        "overall": "PASS" if matched and results else "FAIL",
        "stopped_at": None,
        "reason": None if matched and results else "fixture_mismatch",
    }


def _mcp_text(result: Any) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", None) or []:
        if getattr(item, "type", "") == "text" and getattr(item, "text", None):
            parts.append(item.text)
    return "\n".join(parts)


def _mcp_payload(result: Any) -> Any:
    if getattr(result, "isError", False):
        raise ToolCallError(_mcp_text(result) or "tool returned isError")
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        inner = structured.get("result")
        if isinstance(inner, dict) and "ok" not in structured:
            return inner
        return structured
    text = _mcp_text(result)
    if not text:
        raise ToolCallError("empty tool result")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ToolCallError(f"non-json tool result: {exc}") from exc
    if isinstance(data, dict):
        inner = data.get("result")
        if isinstance(inner, dict) and "ok" not in data:
            return inner
    return data


def run_live(playbook: dict[str, Any], params: dict[str, Any] | None = None) -> dict[str, Any]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    validate_schema(playbook)

    async def _call(session: Any, tool: str, arguments: Any) -> Any:
        timeout = timedelta(seconds=float(CONFIG["call_timeout_s"]))
        try:
            result = await session.call_tool(
                tool,
                arguments if isinstance(arguments, dict) else {},
                read_timeout_seconds=timeout,
            )
        except Exception as exc:  # noqa: BLE001
            raise ToolCallError(str(exc)) from exc
        return _mcp_payload(result)

    async def _main() -> dict[str, Any]:
        server = StdioServerParameters(
            command=str(CONFIG["python"]),
            args=list(CONFIG["args"]),
            cwd=str(CONFIG["cwd"]),
        )
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def invoke(step_id: str, tool: str, arguments: Any) -> Any:
                    del step_id
                    return await _call(session, tool, arguments)

                return await _async_run(playbook, dict(params or {}), invoke, "live")

    return asyncio.run(_main())


def coerce_param(raw: str) -> Any:
    text = raw.strip()
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none"}:
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def parse_param_flags(items: list[str] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"expected k=v, got {item!r}")
        key, value = item.split("=", 1)
        if not key:
            raise ValueError(f"expected k=v, got {item!r}")
        out[key] = coerce_param(value)
    return out


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _emit(payload: dict[str, Any], json_out: str | None) -> None:
    text = _dump(payload)
    print(text)
    if json_out:
        Path(json_out).write_text(text + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a playbook against fixtures or a live MCP bridge.")
    parser.add_argument("playbook", help="Path to a playbook TOML file.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--fixtures",
        metavar="PATH",
        help="Fixture JSON file, or directory of fixtures (CI compare mode).",
    )
    group.add_argument("--live", action="store_true", help="Call tools on a live MCP stdio client.")
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="K=V",
        help="Override a playbook param. Repeatable.",
    )
    parser.add_argument("--json-out", metavar="FILE", help="Also write the verdict JSON to FILE.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else 2
    try:
        playbook = load_playbook(args.playbook)
        overlays = parse_param_flags(args.param)
        if args.live:
            verdict = run_live(playbook, overlays)
            _emit(verdict, args.json_out)
            return 0 if verdict.get("overall") in PASS_OVERALL else 1
        path = Path(args.fixtures)
        if path.is_dir():
            report = run_fixture_dir(playbook, path, param_overrides=overlays)
            _emit(report, args.json_out)
            return 0 if report.get("overall") == "PASS" else 1
        if path.is_file():
            fixture = load_fixture(path)
            verdict = run_fixture(playbook, fixture, param_overrides=overlays)
            _emit(verdict, args.json_out)
            return 0 if verdict.get("overall") in PASS_OVERALL else 1
        print(f"fixtures path not found: {path}", file=sys.stderr)
        return 2
    except SchemaError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (OSError, tomllib.TOMLDecodeError, json.JSONDecodeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
