"""MCP adapter for the generic playbook runner.

``playbook_run`` is a compositor: it does not wrap its body in
``runtime.tool_lock``. Each step tool takes the lock as usual (same rule
as ``wait_for`` and ``ui_dialog``). It does not launch DayZ and does not
open a nested MCP stdio client. ``certified`` is always false until a
FROZEN sidecar registry exists (the content SHA lives outside the TOML).
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import inspect
import math
import re
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError


NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
PLAYBOOKS_DIR = Path(__file__).resolve().parents[2] / "playbooks"
DRAFT_NOTE = "DRAFT playbook: verdict is advisory"
CERTIFIED_REASON = "no_frozen_registry"
MAX_PLAYBOOK_STEPS = 32
PLAYBOOK_DENIED_TOOLS = frozenset(
    {
        "dayz_test_run",
        "dayz_test_stop",
        "exec_enforce",
        "session_release",
        "session_acquire",
        "session_acquire_wait",
        "lease_acquire",
        "session_wait",
        "session_cancel",
        "session_heartbeat",
        "playbook_run",
        "playbook_reload",
    }
)
_FILE_SCHEMA_FIELDS = frozenset(
    {
        "status",
        "steps",
        "on_fail",
        "tol_ref",
        "id",
        "version",
        "requires_tools",
        "tool",
        "expect",
        "calibration",
        "params",
    }
)

_runner: ModuleType | None = None
RUNNER_MODULE = "dayz_playbook_runner"
_runner_lock = threading.RLock()
_active_runs = 0
_runner_loading = False
# A receipt for the exact source compiled by a successfully published loader.
# Observers read this; reload never edits ServerSourceWatch's boot reference.
_runner_source: tuple[ModuleType, Path, str] | None = None


class _RunnerSourceLoader(importlib.machinery.SourceFileLoader):
    def __init__(self, path: Path, source: bytes) -> None:
        super().__init__(RUNNER_MODULE, str(path))
        self.source = source

    def get_code(self, fullname: str):
        # Always compile these bytes: timestamp-based pyc caches can serve old
        # code after a same-size save in the same second, even with Python -B.
        return self.source_to_code(self.source, self.path)


def _load_runner_locked() -> ModuleType:
    """Construct separately; restore the import slot on every failed load."""
    global _runner, _runner_source, _runner_loading
    if _runner_loading:
        raise ToolError("playbook_runner_busy: load_in_progress")
    _runner_loading = True
    previous = sys.modules.get(RUNNER_MODULE)
    try:
        path = PLAYBOOKS_DIR / "runner.py"
        source = path.read_bytes()
        loader = _RunnerSourceLoader(path, source)
        spec = importlib.util.spec_from_file_location(RUNNER_MODULE, path, loader=loader)
        if spec is None:
            raise ToolError("playbook_runner_missing")
        module = importlib.util.module_from_spec(spec)
        # Module-level dataclasses/imports require the canonical import slot.
        # The lock keeps runner consumers/observers outside this staging window.
        sys.modules[RUNNER_MODULE] = module
        loader.exec_module(module)
        if (not callable(getattr(module, "load_playbook", None))
                or not inspect.iscoroutinefunction(getattr(module, "_async_run", None))
                or not all(
                    isinstance(getattr(module, name, None), type)
                    and issubclass(getattr(module, name), Exception)
                    for name in ("SchemaError", "ToolCallError")
                )):
            raise ToolError("playbook_runner_invalid: required_api_missing")
        if path.read_bytes() != source:
            raise ToolError("playbook_runner_invalid: source_changed_during_load")
        receipt = (module, path, hashlib.sha256(source).hexdigest())
    except BaseException as exc:
        if previous is None:
            sys.modules.pop(RUNNER_MODULE, None)
        else:
            sys.modules[RUNNER_MODULE] = previous
        if isinstance(exc, ToolError):
            raise
        if isinstance(exc, FileNotFoundError):
            raise ToolError("playbook_runner_missing") from None
        # Keep the previous module/receipt intact, even on a module-level exit.
        raise ToolError(f"playbook_runner_load_failed: {type(exc).__name__}") from None
    else:
        _runner_source = receipt
        _runner = module
        return module
    finally:
        _runner_loading = False


def load_runner() -> ModuleType:
    """Load ``playbooks/runner.py`` without adding a generic ``runner`` name."""
    with _runner_lock:
        if _runner_loading:
            raise ToolError("playbook_runner_busy: load_in_progress")
        return _runner if _runner is not None else _load_runner_locked()


def runner_source_snapshot() -> tuple[ModuleType, Path, str] | None:
    """Return only the receipt belonging to the currently published runner."""
    # A slow/blocked import must not hold every MCP freshness observation.
    # An unavailable receipt means unknown until publication completes.
    if not _runner_lock.acquire(blocking=False):
        return None
    try:
        if (_runner_loading or _runner_source is None
                or _runner_source[0] is not _runner
                or sys.modules.get(RUNNER_MODULE) is not _runner):
            return None
        return _runner_source
    finally:
        _runner_lock.release()


def reload_runner(module: object) -> dict[str, Any]:
    """Explicit one-entry allowlist; refuse instead of waiting for active runs."""
    if not isinstance(module, str) or module != RUNNER_MODULE:
        raise ToolError(f"playbook_reload_refused: module_not_allowed; allowed={RUNNER_MODULE}")
    if not _runner_lock.acquire(blocking=False):
        raise ToolError("playbook_reload_refused: load_in_progress")
    try:
        if _active_runs:
            raise ToolError(f"playbook_reload_refused: playbook_in_flight; active_runs={_active_runs}")
        _load_runner_locked()
        return {"status": "reloaded", "module": RUNNER_MODULE, "scope": "current_mcp_process"}
    finally:
        _runner_lock.release()


@contextmanager
def _runner_execution():
    global _active_runs
    if not _runner_lock.acquire(blocking=False):
        raise ToolError("playbook_runner_busy: load_in_progress")
    try:
        runner = load_runner()
        _active_runs += 1
    finally:
        _runner_lock.release()
    try:
        yield runner
    finally:
        with _runner_lock:
            _active_runs -= 1


def list_known_playbooks(playbooks_dir: Path | None = None) -> list[str]:
    root = Path(playbooks_dir or PLAYBOOKS_DIR)
    names: list[str] = []
    if not root.is_dir():
        return names
    for path in sorted(root.glob("*.toml")):
        if NAME_RE.fullmatch(path.stem):
            names.append(path.stem)
    return names


def _known_csv(playbooks_dir: Path) -> str:
    names = list_known_playbooks(playbooks_dir)
    return ", ".join(names) if names else "(none)"


def _is_json_simple(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return True
    if isinstance(value, str):
        return True
    if isinstance(value, int) and not isinstance(value, bool):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_simple(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_simple(item) for key, item in value.items())
    return False


def map_schema_error(exc: BaseException, name: str) -> ToolError:
    field = getattr(exc, "field", None)
    detail = str(exc)
    if isinstance(field, str) and (
        field == "args" or field.startswith("args.") or field.startswith("params.")
    ):
        named = field if field.startswith("params.") else f"params.{field}"
        return ToolError(f"bad_args: {named} {detail}")
    if isinstance(field, str) and (field in _FILE_SCHEMA_FIELDS or field.startswith("steps")):
        return ToolError(f"playbook_invalid: {name}: {detail}")
    if isinstance(field, str) and field:
        return ToolError(f"playbook_invalid: {name}: {detail}")
    return ToolError(f"playbook_invalid: {name}: {detail}")


def attach_playbook_meta(
    verdict: dict[str, Any],
    name: str,
    playbook: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(verdict)
    status = playbook.get("status")
    payload["playbook"] = {
        "id": playbook.get("id"),
        "name": name,
        "status": status,
        "certified": False,
        "certified_reason": CERTIFIED_REASON,
    }
    if status == "DRAFT":
        payload["note"] = DRAFT_NOTE
    return payload


def resolve_playbook_path(name: object, playbooks_dir: Path | None = None) -> Path:
    root = Path(playbooks_dir or PLAYBOOKS_DIR).resolve()
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ToolError(
            f"bad_args: name {name!r} must match ^[a-z][a-z0-9_]{{0,31}}$"
        )
    path = (root / f"{name}.toml").resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise ToolError(
            f"bad_args: name '{name}' is not in the playbook dictionary; "
            f"known: {_known_csv(root)}"
        ) from None
    if path.name != f"{name}.toml" or not path.is_file():
        raise ToolError(
            f"bad_args: name '{name}' is not in the playbook dictionary; "
            f"known: {_known_csv(root)}"
        )
    return path


def coerce_params(params: object) -> dict[str, Any]:
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise ToolError("bad_args: params must be an object")
    if any(not isinstance(key, str) for key in params):
        raise ToolError("bad_args: params keys must be strings")
    if not _is_json_simple(params):
        raise ToolError("bad_args: params values must be JSON-simple")
    return dict(params)


def reject_unknown_params(name: str, playbook: dict[str, Any], params: dict[str, Any]) -> None:
    declared = playbook.get("params") if isinstance(playbook.get("params"), dict) else {}
    unknown = sorted(key for key in params if key not in declared)
    if not unknown:
        return
    declared_csv = ", ".join(sorted(declared)) if declared else "(none)"
    raise ToolError(
        f"bad_args: params.{unknown[0]} is not declared by playbook '{name}'; "
        f"declared: {declared_csv}"
    )


async def invoke_registered(
    app: Any,
    step_id: str,
    tool_name: str,
    arguments: Any,
    runner: ModuleType,
) -> Any:
    if tool_name in PLAYBOOK_DENIED_TOOLS:
        wrapped = runner.ToolCallError(
            f"tool '{tool_name}' is not allowed inside a playbook"
        )
        wrapped.step_id = step_id
        wrapped.tool = tool_name
        raise wrapped
    manager = getattr(app, "_tool_manager", None)
    get_tool = getattr(manager, "get_tool", None) if manager is not None else None
    tool = get_tool(tool_name) if callable(get_tool) else None
    if tool is None:
        raise runner.ToolCallError(f"unknown tool {tool_name!r}")
    args = arguments if isinstance(arguments, dict) else {}
    extra = None
    context_kwarg = getattr(tool, "context_kwarg", None)
    if context_kwarg:
        get_context = getattr(app, "get_context", None)
        extra = {context_kwarg: get_context()} if callable(get_context) else None
    try:
        result = await tool.fn_metadata.call_fn_with_arg_validation(
            tool.fn, tool.is_async, args, extra
        )
    except ToolError as exc:
        wrapped = runner.ToolCallError(str(exc))
        wrapped.step_id = step_id
        wrapped.tool = tool_name
        raise wrapped from exc
    return result


async def execute_playbook_run(
    app: Any,
    name: object,
    params: object = None,
    *,
    playbooks_dir: Path | None = None,
) -> dict[str, Any]:
    """Run ``playbooks/<name>.toml`` against already-registered tools.

    Does not wrap the body in ``runtime.tool_lock``. Each step tool takes
    the lock as usual. There is no envelope timeout: each sub-tool applies
    its own budget (``DEFAULT_TOOL_TIMEOUT_S`` 15s up to ``MAX_TIMEOUT_S``
    300s; ``wait_for`` <= 600s; ``ui_dialog`` <= 250s). At most
    ``MAX_PLAYBOOK_STEPS`` steps. ``certified`` is always false until a
    FROZEN sidecar registry exists. Does not launch DayZ.
    """
    with _runner_execution() as runner:
        root = Path(playbooks_dir or PLAYBOOKS_DIR)
        path = resolve_playbook_path(name, root)
        merged = coerce_params(params)
        label = str(name)
        try:
            playbook = runner.load_playbook(path)
        except runner.SchemaError as exc:
            raise map_schema_error(exc, label) from None

        reject_unknown_params(label, playbook, merged)
        steps = playbook.get("steps") if isinstance(playbook.get("steps"), list) else []
        if len(steps) > MAX_PLAYBOOK_STEPS:
            raise ToolError(
                f"bad_args: playbook has {len(steps)} steps, max {MAX_PLAYBOOK_STEPS}"
            )

        async def invoke(step_id: str, tool: str, arguments: Any) -> Any:
            return await invoke_registered(app, step_id, tool, arguments, runner)

        try:
            verdict = await runner._async_run(playbook, merged, invoke, "live")
        except runner.SchemaError as exc:
            raise map_schema_error(exc, label) from None
        return attach_playbook_meta(verdict, label, playbook)
