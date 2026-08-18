"""Focal tests for the playbook_run MCP adapter."""
from __future__ import annotations

import inspect
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import playbook_tool
from dayz_mcp.server import ServerConfig, build_app


def _playbooks() -> Path:
    here = Path(__file__).resolve()
    for candidate in (here.parents[2] / "playbooks", here.parents[3] / "playbooks"):
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("playbooks directory not found")


PLAYBOOKS = _playbooks()
if str(PLAYBOOKS) not in sys.path:
    sys.path.insert(0, str(PLAYBOOKS))

import runner  # noqa: E402


class _FnMeta:
    async def call_fn_with_arg_validation(self, fn, fn_is_async, arguments, extra):
        del extra
        if fn_is_async:
            return await fn(**arguments)
        return fn(**arguments)


class _StubTool:
    def __init__(self, handler):
        self.fn = handler
        self.is_async = inspect.iscoroutinefunction(handler)
        self.fn_metadata = _FnMeta()
        self.context_kwarg = None


class _StubManager:
    def __init__(self, tools: dict[str, _StubTool]) -> None:
        self._tools = tools

    def get_tool(self, name: str) -> _StubTool | None:
        return self._tools.get(name)


class _StubApp:
    def __init__(self, tools: dict[str, _StubTool]) -> None:
        self._tool_manager = _StubManager(tools)


def _place_handlers(fail_tool: str | None = None) -> dict[str, _StubTool]:
    async def surface_query(*, x, z):
        del x, z
        if fail_tool == "surface_query":
            raise ToolError("lease_required: call session_acquire_wait(purpose=...)")
        return {"ok": 1, "y": 315.56}

    async def scene_raycast(**kwargs):
        if fail_tool == "scene_raycast":
            raise ToolError("game_not_ready")
        return {
            "ok": 1,
            "raycast": {"hit": True, "pos": [0.0, 315.56, 0.0]},
        }

    async def query_all_players(**kwargs):
        return {"ok": 1, "players": []}

    async def entities_query(**kwargs):
        return {"ok": 1, "count_total": 0, "entities": []}

    async def boom(**kwargs):
        raise AssertionError("denied tool must not run")

    return {
        "surface_query": _StubTool(surface_query),
        "scene_raycast": _StubTool(scene_raycast),
        "query_all_players": _StubTool(query_all_players),
        "entities_query": _StubTool(entities_query),
        "playbook_run": _StubTool(boom),
        "dayz_test_run": _StubTool(boom),
    }


def _write_playbook(root: Path, name: str, body: str) -> Path:
    path = root / f"{name}.toml"
    path.write_text(body, encoding="utf-8")
    return path


class PlaybookNameTest(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_name_is_bad_args_and_lists_known(self) -> None:
        with self.assertRaises(ToolError) as ctx:
            await playbook_tool.execute_playbook_run(_StubApp({}), "not_a_playbook")
        message = str(ctx.exception)
        self.assertIn("bad_args: name 'not_a_playbook'", message)
        self.assertIn("playbook dictionary", message)
        self.assertIn("place_safely", message)

    async def test_invalid_and_traversal_names_are_bad_args(self) -> None:
        cases = (
            "Place_Safely",
            "../place_safely",
            "a/b",
            "place-safely",
            "",
            "place_safely.toml",
            "a" * 40,
        )
        for name in cases:
            with self.subTest(name=name):
                with self.assertRaises(ToolError) as ctx:
                    await playbook_tool.execute_playbook_run(_StubApp({}), name)
                self.assertIn("bad_args: name", str(ctx.exception))

    async def test_params_must_be_json_simple_object(self) -> None:
        with self.assertRaises(ToolError) as ctx:
            await playbook_tool.execute_playbook_run(
                _StubApp({}), "place_safely", params=["x"]
            )
        self.assertIn("bad_args: params", str(ctx.exception))
        with self.assertRaises(ToolError) as ctx:
            await playbook_tool.execute_playbook_run(
                _StubApp({}), "place_safely", params={"x": object()}
            )
        self.assertIn("bad_args: params", str(ctx.exception))

    async def test_unknown_param_key_is_rejected(self) -> None:
        with self.assertRaises(ToolError) as ctx:
            await playbook_tool.execute_playbook_run(
                _StubApp(_place_handlers()), "place_safely", params={"X": 7512}
            )
        message = str(ctx.exception)
        self.assertIn("bad_args: params.X", message)
        self.assertIn("is not declared by playbook 'place_safely'", message)
        self.assertIn("x", message)
        self.assertIn("z", message)


class PlaybookSchemaMapTest(unittest.IsolatedAsyncioTestCase):
    async def test_schema_error_names_params_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_playbook(
                root,
                "broken",
                "\n".join(
                    [
                        'id = "broken"',
                        'version = "0"',
                        'status = "DRAFT"',
                        'requires_tools = ["surface_query"]',
                        "",
                        "[[steps]]",
                        'id = "S1"',
                        'tool = "surface_query"',
                        'args = { x = "$nope" }',
                        "expect = []",
                        'on_fail = { action = "STOP", reason = "x" }',
                    ]
                ),
            )
            with self.assertRaises(ToolError) as ctx:
                await playbook_tool.execute_playbook_run(
                    _StubApp({}), "broken", playbooks_dir=root
                )
            message = str(ctx.exception)
            self.assertIn("bad_args: params.", message)
            self.assertIn("nope", message)

    async def test_file_schema_error_is_playbook_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_playbook(
                root,
                "badstatus",
                "\n".join(
                    [
                        'id = "badstatus"',
                        'version = "0"',
                        'status = "NOPE"',
                        'requires_tools = ["surface_query"]',
                        "",
                        "[[steps]]",
                        'id = "S1"',
                        'tool = "surface_query"',
                        "args = {}",
                        "expect = []",
                        'on_fail = { action = "STOP", reason = "x" }',
                    ]
                ),
            )
            with self.assertRaises(ToolError) as ctx:
                await playbook_tool.execute_playbook_run(
                    _StubApp({}), "badstatus", playbooks_dir=root
                )
            message = str(ctx.exception)
            self.assertTrue(message.startswith("playbook_invalid: badstatus:"))
            self.assertIn("status", message)


class PlaybookRunExecuteTest(unittest.IsolatedAsyncioTestCase):
    async def test_draft_place_safely_is_advisory_not_certified(self) -> None:
        app = _StubApp(_place_handlers())
        result = await playbook_tool.execute_playbook_run(
            app, "place_safely", {"x": 7512.0, "z": 7502.0}
        )
        self.assertIn(result["overall"], {"PASS", "PASS_WITH_WARNINGS"})
        self.assertNotIn("verdict", result)
        self.assertEqual(result["playbook"]["id"], "place_safely")
        self.assertEqual(result["playbook"]["name"], "place_safely")
        self.assertEqual(result["playbook"]["status"], "DRAFT")
        self.assertIs(result["playbook"]["certified"], False)
        self.assertEqual(
            result["playbook"]["certified_reason"], playbook_tool.CERTIFIED_REASON
        )
        self.assertEqual(result["note"], playbook_tool.DRAFT_NOTE)
        self.assertIn("steps", result)
        self.assertEqual(result["mode"], "live")

    async def test_subtool_error_is_collected_not_raised(self) -> None:
        app = _StubApp(_place_handlers(fail_tool="surface_query"))
        result = await playbook_tool.execute_playbook_run(app, "place_safely")
        self.assertEqual(result["overall"], "FAIL")
        self.assertEqual(result["stopped_at"], "S1")
        self.assertIn("tool_error:", result["reason"] or "")
        self.assertIn("lease_required", result["reason"] or "")

    async def test_frozen_on_disk_is_still_uncertified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_playbook(
                root,
                "frozenone",
                "\n".join(
                    [
                        'id = "frozenone"',
                        'version = "1"',
                        'status = "FROZEN"',
                        'requires_tools = ["surface_query"]',
                        "",
                        "[[steps]]",
                        'id = "S1"',
                        'tool = "surface_query"',
                        "args = {}",
                        'expect = [{ field = "ok", op = "eq", value = 1 }]',
                        'on_fail = { action = "STOP", reason = "x" }',
                    ]
                ),
            )

            async def surface_query(**kwargs):
                return {"ok": 1}

            app = _StubApp({"surface_query": _StubTool(surface_query)})
            result = await playbook_tool.execute_playbook_run(
                app, "frozenone", playbooks_dir=root
            )
            self.assertIs(result["playbook"]["certified"], False)
            self.assertEqual(
                result["playbook"]["certified_reason"], "no_frozen_registry"
            )
            self.assertEqual(result["playbook"]["status"], "FROZEN")
            self.assertEqual(result["playbook"]["id"], "frozenone")

    async def test_denied_tools_fail_without_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_playbook(
                root,
                "denied",
                "\n".join(
                    [
                        'id = "denied"',
                        'version = "1"',
                        'status = "DRAFT"',
                        'requires_tools = ["playbook_run", "dayz_test_run"]',
                        "",
                        "[[steps]]",
                        'id = "S1"',
                        'tool = "playbook_run"',
                        "args = {}",
                        "expect = []",
                        'on_fail = { action = "STOP", reason = "denied" }',
                        "",
                        "[[steps]]",
                        'id = "S2"',
                        'tool = "dayz_test_run"',
                        "args = {}",
                        "expect = []",
                        'on_fail = { action = "STOP", reason = "denied" }',
                    ]
                ),
            )
            app = _StubApp(_place_handlers())
            result = await playbook_tool.execute_playbook_run(
                app, "denied", playbooks_dir=root
            )
            self.assertEqual(result["overall"], "FAIL")
            self.assertEqual(result["stopped_at"], "S1")
            self.assertIn(
                "tool 'playbook_run' is not allowed inside a playbook",
                result["reason"] or "",
            )
            self.assertEqual(result["steps"][1]["status"], "SKIPPED")

            _write_playbook(
                root,
                "denied2",
                "\n".join(
                    [
                        'id = "denied2"',
                        'version = "1"',
                        'status = "DRAFT"',
                        'requires_tools = ["dayz_test_run"]',
                        "",
                        "[[steps]]",
                        'id = "S1"',
                        'tool = "dayz_test_run"',
                        "args = {}",
                        "expect = []",
                        'on_fail = { action = "STOP", reason = "denied" }',
                    ]
                ),
            )
            result2 = await playbook_tool.execute_playbook_run(
                app, "denied2", playbooks_dir=root
            )
            self.assertEqual(result2["overall"], "FAIL")
            self.assertIn(
                "tool 'dayz_test_run' is not allowed inside a playbook",
                result2["reason"] or "",
            )

    async def test_too_many_steps_is_bad_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lines = [
                'id = "toobig"',
                'version = "1"',
                'status = "DRAFT"',
                'requires_tools = ["surface_query"]',
                "",
            ]
            for index in range(playbook_tool.MAX_PLAYBOOK_STEPS + 1):
                lines.extend(
                    [
                        "[[steps]]",
                        f'id = "S{index + 1}"',
                        'tool = "surface_query"',
                        "args = {}",
                        "expect = []",
                        'on_fail = { action = "STOP", reason = "x" }',
                        "",
                    ]
                )
            _write_playbook(root, "toobig", "\n".join(lines))
            with self.assertRaises(ToolError) as ctx:
                await playbook_tool.execute_playbook_run(
                    _StubApp(_place_handlers()), "toobig", playbooks_dir=root
                )
            self.assertEqual(
                str(ctx.exception),
                "bad_args: playbook has 33 steps, max 32",
            )

    async def test_missing_runner_is_typed_without_host_path(self) -> None:
        playbook_tool._runner = None
        original = playbook_tool.PLAYBOOKS_DIR
        with tempfile.TemporaryDirectory() as tmp:
            playbook_tool.PLAYBOOKS_DIR = Path(tmp)
            try:
                with self.assertRaises(ToolError) as ctx:
                    playbook_tool.load_runner()
                self.assertEqual(str(ctx.exception), "playbook_runner_missing")
                self.assertNotIn(tmp, str(ctx.exception))
            finally:
                playbook_tool.PLAYBOOKS_DIR = original
                playbook_tool._runner = None

    async def test_registered_tool_dispatches_and_does_not_hold_lock(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _m: None))
        tool = app._tool_manager.get_tool("playbook_run")
        self.assertIsNotNone(tool)
        source = inspect.getsource(tool.fn)
        self.assertNotIn("async with runtime.tool_lock", source)
        self.assertIn("execute_playbook_run", source)
        description = tool.description or ""
        self.assertLessEqual(len(description), 200)
        self.assertIn("Does not launch DayZ", description)
        self.assertIn("certified is always false", description)
        self.assertTrue(description.startswith("Requires a lease"))

    async def test_happy_path_uses_registered_tools_and_releases_lock(self) -> None:
        app, runtime = build_app(ServerConfig(log_sink=lambda _m: None))
        calls: list[tuple[str, dict, str]] = []

        async def fake_call_bridge(cmd, args, peer, timeout_s):
            del timeout_s
            calls.append((cmd, dict(args), peer))
            if cmd == "surface_query":
                return {"ok": 1, "y": 315.56}
            if cmd == "scene_raycast":
                return {
                    "ok": 1,
                    "raycast": {"hit": True, "pos": [7512.0, 315.56, 7502.0]},
                }
            if cmd == "query_all_players":
                return {"ok": 1, "players": []}
            if cmd == "entities_query":
                return {"ok": 1, "count_total": 0, "entities": []}
            raise AssertionError(cmd)

        with patch.object(runtime, "call_bridge", side_effect=fake_call_bridge):
            result = await playbook_tool.execute_playbook_run(
                app, "place_safely", {"x": 7512.0, "z": 7502.0}
            )
        self.assertIn(result["overall"], {"PASS", "PASS_WITH_WARNINGS"})
        self.assertEqual([item[0] for item in calls], [
            "surface_query",
            "scene_raycast",
            "query_all_players",
            "entities_query",
        ])
        self.assertIn("from", calls[1][1])
        self.assertFalse(runtime.tool_lock.locked())
        self.assertIs(result["playbook"]["certified"], False)


class PlaybookFixturesStillGreenTest(unittest.TestCase):
    def test_place_safely_fixture_dir_still_matches(self) -> None:
        playbook = runner.load_playbook(PLAYBOOKS / "place_safely.toml")
        report = runner.run_fixture_dir(
            playbook, PLAYBOOKS / "fixtures" / "place_safely"
        )
        self.assertEqual(report["overall"], "PASS")
        self.assertGreaterEqual(len(report["results"]), 4)
        self.assertTrue(all(item["match"] for item in report["results"]))


if __name__ == "__main__":
    unittest.main()
