"""Shared camera/bridge fakes for the camera_path tests (moved out of
test_camera_path_focus_hud so other test modules import a helper, not a test).
"""
from __future__ import annotations

import asyncio
import re
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch
from dayz_mcp.server import ServerConfig, build_app
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import method_body
from tests.mcp_helpers import _assert_tool_error, _content_json

MISSION = addon_root() / "scripts" / "5_Mission"


BRIDGE = MISSION / "MCPClientBridge.c"


def _tool_error_text(exc: BaseException, tool: str) -> str:
    message = str(exc)
    wrapper = f"Error executing tool {tool}: "
    return message[len(wrapper) :] if message.startswith(wrapper) else message


_STRING_LITERAL = re.compile(r'"(?:\\.|[^"\\])*"')


_PRIMARY = re.compile(r"\d+\.\d+|\d+|[A-Za-z_]\w*")


_SIGNATURE_PARTS = re.compile(r"^(?P<head>.*?)(?P<name>\w+)\s*\((?P<params>[^()]*)\)$")


def _closing(code: str, start: int) -> int:
    depth = 0
    for index in range(start, len(code)):
        depth += (code[index] == "(") - (code[index] == ")")
        if depth == 0:
            return index + 1
    raise AssertionError(f"unbalanced parentheses in {code!r}")


def _divisions_as_products(code: str) -> str:
    """Each a / b of code (comments blanked, no string literal) as a * Over(b)."""
    pieces: list[str] = []
    position = 0
    while True:
        slash = code.find("/", position)
        if slash < 0:
            pieces.append(code[position:])
            return "".join(pieces)
        start = slash + 1
        while code[start].isspace():
            start += 1
        if code[start] == "(":
            end = _closing(code, start)
        else:
            primary = _PRIMARY.match(code, start)
            if primary is None:
                raise AssertionError(f"division before {code[start:start + 20]!r}")
            end = primary.end()
            while True:
                if code.startswith("(", end):
                    end = _closing(code, end)
                elif code.startswith(".", end) and re.match(r"[A-Za-z_]", code[end + 1 : end + 2]):
                    end = re.compile(r"\.\w+").match(code, end).end()  # type: ignore[union-attr]
                else:
                    break
        pieces.append(code[position:slash] + "* Over(" + code[start:end] + ")")
        position = end


def _rewrite(code: str, members: str, methods: str) -> str:
    code = code.replace("new array<float>()", "FloatArray()")
    code = re.sub(r"\bnew (\w+)\(\)", r"\1()", code)
    code = re.sub(r"\barray<float>\s+(?=\w+\s*;)", "FloatArray ", code)
    code = re.sub(r"(?<![\w.])(" + members + r")\b", r"this.\1", code)
    if methods:
        code = re.sub(r"(?<![\w.])(" + methods + r")\s*\(", r"this.\1(", code)
    return _divisions_as_products(code)


def _function(
    source: str,
    signature: str,
    *,
    this_type: str = "",
    name: str = "",
    members: str = r"m_\w+",
    methods: str = "",
) -> tuple[str, str]:
    """(signature, source) of one method of source as a function, with `this` first."""
    parts = _SIGNATURE_PARTS.match(signature)
    if parts is None:
        raise AssertionError(f"unsupported signature: {signature}")
    params = parts["params"].strip()
    if this_type:
        params = f"{this_type} this" + (f", {params}" if params else "")
    head = f"{parts['head']}{name or parts['name']}({params})"
    body = method_body(source, signature)
    pieces: list[str] = []
    last = 0
    for literal in _STRING_LITERAL.finditer(body):
        pieces += [_rewrite(body[last : literal.start()], members, methods), literal.group()]
        last = literal.end()
    pieces.append(_rewrite(body[last:], members, methods))
    return head, head + "\n{" + "".join(pieces) + "}\n"


class _ToolCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        self.tools = {tool.name: tool for tool in self.app._tool_manager.list_tools()}

    async def _forwarded(
        self, tool: str, arguments: dict[str, Any], answer: dict[str, Any] | None = None
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        bridge = AsyncMock(return_value=dict(answer or {"ok": 1}))
        with patch.object(self.runtime, "call_bridge", new=bridge):
            result = _content_json(await self.app.call_tool(tool, arguments))
        bridge.assert_awaited_once()
        return bridge.await_args.args, result

    async def _refused(self, tool: str, arguments: dict[str, Any]) -> str:
        bridge = AsyncMock(return_value={"ok": 1})
        with patch.object(self.runtime, "call_bridge", new=bridge):
            with self.assertRaises(Exception) as caught:
                await self.app.call_tool(tool, arguments)
        bridge.assert_not_awaited()
        _assert_tool_error(self, caught.exception)
        return _tool_error_text(caught.exception, tool)

    async def _assert_waits_for_the_tool_lock(self, tool: str, arguments: dict[str, Any]) -> None:
        """The call reaches the bridge only once the shared runtime.tool_lock is free."""
        held: list[bool] = []

        async def answer(*_args: object) -> dict[str, Any]:
            held.append(self.runtime.tool_lock.locked())
            return {"ok": 1}

        bridge = AsyncMock(side_effect=answer)
        with patch.object(self.runtime, "call_bridge", new=bridge):
            await self.runtime.tool_lock.acquire()
            call = asyncio.ensure_future(self.app.call_tool(tool, arguments))
            try:
                for _ in range(100):
                    await asyncio.sleep(0)
                waited = not call.done() and bridge.await_count == 0
            finally:
                self.runtime.tool_lock.release()
                await call
        self.assertTrue(waited, "the call did not wait for runtime.tool_lock")
        bridge.assert_awaited_once()
        self.assertEqual(held, [True])


def _camera_answer(**overrides: object) -> dict[str, Any]:
    answer: dict[str, Any] = {
        "id": 1,
        "ok": 1,
        "camera": {"ok": 1, "view": "scripted", "applied_mode": "orient", "viewport_moved": 1, "fov": 0.0},
    }
    answer.update(overrides)
    return answer


# The owner of the lease in the release probes below.
_PATH_OWNER = {
    "platform": "codex",
    "pid": 4343,
    "ppid": 10,
    "started_at_utc": "2026-07-14T20:00:01Z",
    "session_id": "session-path",
    "task_label": "path",
}


class _FakeClientRuntime:
    def __init__(self, config: ServerConfig, **_kwargs: object) -> None:
        self.config = config
        self.active_lease_token = None
        self._registered_tool_names = None
