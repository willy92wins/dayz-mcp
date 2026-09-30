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
        result = await playbook_tool.execute_playbook_run(
            app, "place_safely", {"x": 7512.0, "z": 7502.0}
        )
        self.assertEqual(result["overall"], "FAIL")
        self.assertEqual(result["stopped_at"], "S1")
        self.assertIn("tool_error:", result["reason"] or "")
        self.assertIn("lease_required", result["reason"] or "")

    async def test_live_place_safely_without_coords_is_bad_args(self) -> None:
        for params in (None, {}):
            with self.subTest(params=params):
                with self.assertRaises(ToolError) as ctx:
                    await playbook_tool.execute_playbook_run(
                        _StubApp(_place_handlers()), "place_safely", params
                    )
                message = str(ctx.exception)
                self.assertTrue(message.startswith("bad_args:"))
                self.assertIn("params.x", message)
                self.assertIn("params.z", message)
                self.assertIn("required in live", message)

    async def test_live_place_safely_only_x_is_bad_args(self) -> None:
        with self.assertRaises(ToolError) as ctx:
            await playbook_tool.execute_playbook_run(
                _StubApp(_place_handlers()), "place_safely", {"x": 7512.0}
            )
        message = str(ctx.exception)
        self.assertTrue(message.startswith("bad_args:"))
        self.assertIn("params.z", message)
        self.assertIn("required in live", message)
        self.assertNotIn("params.x", message)

    async def test_live_place_safely_explicit_zero_is_valid(self) -> None:
        result = await playbook_tool.execute_playbook_run(
            _StubApp(_place_handlers()), "place_safely", {"x": 0, "z": 0}
        )
        self.assertIn(result["overall"], {"PASS", "PASS_WITH_WARNINGS"})
        self.assertEqual(result["mode"], "live")
        self.assertEqual(result["params"]["x"], 0)
        self.assertEqual(result["params"]["z"], 0)
        self.assertEqual(result["params"]["clear_r"], 15.0)

    async def test_live_lease_spawn_prepare_trace_without_coords_inherits_toml(
        self,
    ) -> None:
        result = await playbook_tool.execute_playbook_run(
            _StubApp({}), "lease_spawn_prepare_trace"
        )
        self.assertEqual(result["mode"], "live")
        self.assertEqual(result["params"]["x"], 0.0)
        self.assertEqual(result["params"]["z"], 0.0)
        self.assertEqual(result["params"]["y"], 0.0)
        self.assertEqual(result["params"]["vehicle_type"], "CivilianSedan")
        self.assertEqual(result["overall"], "FAIL")
        self.assertIn("tool_error:", result["reason"] or "")

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

    async def test_lifecycle_tools_are_denied_without_running(self) -> None:
        # No playbook may close, stop or launch the game: dayz_test_close is
        # denied like dayz_test_run and dayz_test_stop. The stubs succeed, so a
        # tool missing from the denylist would run and the playbook would pass.
        ran: list[str] = []

        def recorder(tool_name: str):
            async def handler(**kwargs):
                ran.append(tool_name)
                return {"ok": 1}

            return handler

        app = _StubApp(
            {
                name: _StubTool(recorder(name))
                for name in (
                    "dayz_test_close",
                    "dayz_test_stop",
                    "dayz_test_run",
                    "query_all_players",
                )
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for tool_name in ("dayz_test_close", "dayz_test_stop", "dayz_test_run"):
                with self.subTest(tool=tool_name):
                    _write_playbook(
                        root,
                        "lifecycle",
                        "\n".join(
                            [
                                'id = "lifecycle"',
                                'version = "1"',
                                'status = "DRAFT"',
                                f'requires_tools = ["{tool_name}", "query_all_players"]',
                                "",
                                "[[steps]]",
                                'id = "S1"',
                                f'tool = "{tool_name}"',
                                'args = { run_id = "r1" }',
                                "expect = []",
                                'on_fail = { action = "STOP", reason = "denied" }',
                                "",
                                "[[steps]]",
                                'id = "S2"',
                                'tool = "query_all_players"',
                                "args = {}",
                                "expect = []",
                                'on_fail = { action = "STOP", reason = "denied" }',
                            ]
                        ),
                    )
                    result = await playbook_tool.execute_playbook_run(
                        app, "lifecycle", playbooks_dir=root
                    )
                    self.assertEqual(result["overall"], "FAIL")
                    self.assertEqual(result["stopped_at"], "S1")
                    self.assertIn(
                        f"tool '{tool_name}' is not allowed inside a playbook",
                        result["reason"] or "",
                    )
                    self.assertEqual(result["steps"][1]["status"], "SKIPPED")
        self.assertEqual(ran, [])

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
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            playbook_tool, "_runner", None
        ), patch.object(playbook_tool, "PLAYBOOKS_DIR", Path(tmp)):
            with self.assertRaises(ToolError) as ctx:
                playbook_tool.load_runner()
            self.assertEqual(str(ctx.exception), "playbook_runner_missing")
            self.assertNotIn(tmp, str(ctx.exception))

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
        self.assertIn("params.x", description)
        self.assertIn("params.z", description)
        self.assertIn("Live", description)
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
            # The entities_query tool now follows up with a players probe to
            # stamp reliability (fb-20260824-123204-638e).
            "query_all_players",
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
        self.assertGreaterEqual(len(report["results"]), 5)
        self.assertTrue(all(item["match"] for item in report["results"]))


def _ask_restart_app(answer: object) -> tuple[_StubApp, list[dict]]:
    calls: list[dict] = []

    async def ui_dialog(**kwargs):
        calls.append(dict(kwargs))
        if isinstance(answer, Exception):
            raise answer
        return dict(answer)

    async def boom(**kwargs):
        raise AssertionError("ask_restart must not close or launch the game")

    tools = {"ui_dialog": _StubTool(ui_dialog)}
    for name in ("dayz_test_close", "dayz_test_stop", "dayz_test_run"):
        tools[name] = _StubTool(boom)
    return _StubApp(tools), calls


class AskRestartRunTest(unittest.IsolatedAsyncioTestCase):
    """playbook_run(name="ask_restart") (4ed0): restart only on the player's Yes."""

    async def test_yes_is_a_draft_pass_from_one_confirm(self) -> None:
        app, calls = _ask_restart_app(
            {"ok": 1, "state": "completed", "elapsed_s": 2.0, "choice": "yes"}
        )
        result = await playbook_tool.execute_playbook_run(app, "ask_restart")
        self.assertEqual(result["overall"], "PASS")
        self.assertEqual(result["mode"], "live")
        self.assertEqual(result["playbook"]["id"], "ask_restart")
        self.assertEqual(result["playbook"]["status"], "DRAFT")
        self.assertIs(result["playbook"]["certified"], False)
        self.assertEqual(result["note"], playbook_tool.DRAFT_NOTE)
        defaults = runner.load_playbook(PLAYBOOKS / "ask_restart.toml")["params"]
        self.assertEqual(
            calls,
            [
                {
                    "kind": "confirm",
                    "title": defaults["title"],
                    "message": defaults["message"],
                    "timeout_s": 30.0,
                }
            ],
        )

    async def test_no_cancel_and_timeout_are_not_a_restart(self) -> None:
        answers = (
            {"ok": 1, "state": "completed", "elapsed_s": 3.0, "choice": "no"},
            {"ok": 1, "state": "cancelled", "elapsed_s": 1.5},
            {"ok": 1, "state": "timed_out", "elapsed_s": 30.0},
        )
        for answer in answers:
            with self.subTest(state=answer["state"]):
                app, _calls = _ask_restart_app(answer)
                result = await playbook_tool.execute_playbook_run(app, "ask_restart")
                self.assertEqual(result["overall"], "FAIL")
                self.assertEqual(result["stopped_at"], "S1")
                self.assertEqual(result["reason"], "restart_not_confirmed")
                self.assertEqual(result["steps"][0]["observed"], answer)

    async def test_no_answer_from_the_bridge_is_not_a_restart(self) -> None:
        app, _calls = _ask_restart_app(ToolError("timeout waiting for ui_dialog id=7"))
        result = await playbook_tool.execute_playbook_run(app, "ask_restart")
        self.assertEqual(result["overall"], "FAIL")
        self.assertEqual(result["stopped_at"], "S1")
        self.assertIn("tool_error:timeout waiting for ui_dialog", result["reason"] or "")

    async def test_caller_cannot_change_the_dialog_kind(self) -> None:
        app, calls = _ask_restart_app(
            {"ok": 1, "state": "completed", "elapsed_s": 2.0, "choice": "yes"}
        )
        with self.assertRaises(ToolError) as ctx:
            await playbook_tool.execute_playbook_run(
                app, "ask_restart", {"kind": "acknowledge"}
            )
        message = str(ctx.exception)
        self.assertIn(
            "bad_args: params.kind is not declared by playbook 'ask_restart'", message
        )
        self.assertIn("declared: message, timeout_s, title", message)
        self.assertEqual(calls, [])

    async def _through_registered_ui_dialog(
        self, dialog: dict, params: dict | None = None
    ) -> tuple[dict, list[tuple], list[tuple]]:
        """Run the playbook against the real ui_dialog tool; the bridge answers with dialog."""
        app, runtime = build_app(ServerConfig(log_sink=lambda _m: None))
        enqueued: list[tuple] = []
        abandoned: list[tuple] = []

        async def fake_enqueue(cmd, args, peer, timeout_s):
            enqueued.append((cmd, dict(args), peer, timeout_s))
            return len(enqueued)

        async def fake_probe(cmd, command_id, peer):
            del cmd, command_id, peer
            return {"ok": 1, "dialog": dict(dialog)}

        async def fake_abandon(command_id, reason):
            abandoned.append((command_id, reason))

        with (
            patch.object(runtime, "enqueue_bridge", side_effect=fake_enqueue),
            patch.object(runtime, "probe_bridge_result", side_effect=fake_probe),
            patch.object(runtime, "abandon_bridge", side_effect=fake_abandon),
        ):
            result = await playbook_tool.execute_playbook_run(app, "ask_restart", params)
        self.assertFalse(runtime.tool_lock.locked())
        return result, enqueued, abandoned

    async def test_registered_ui_dialog_gets_the_confirm_and_yes_passes(self) -> None:
        result, enqueued, abandoned = await self._through_registered_ui_dialog(
            {"state": "completed", "choice": "yes", "elapsed_s": 3.0},
            {"message": "Redeploy @MyMod: restart DayZ now?"},
        )
        self.assertEqual(result["overall"], "PASS")
        self.assertEqual(
            result["steps"][0]["observed"],
            {"ok": 1, "state": "completed", "elapsed_s": 3.0, "choice": "yes"},
        )
        defaults = runner.load_playbook(PLAYBOOKS / "ask_restart.toml")["params"]
        # One client command: the confirm, with the 30 s window plus the 10 s bridge slack.
        self.assertEqual(
            enqueued,
            [
                (
                    "ui_dialog",
                    {
                        "kind": "confirm",
                        "title": defaults["title"],
                        "message": "Redeploy @MyMod: restart DayZ now?",
                        "timeout_s": 30.0,
                    },
                    "client",
                    40.0,
                )
            ],
        )
        self.assertEqual(abandoned, [])

    async def test_registered_ui_dialog_cancel_is_not_a_restart(self) -> None:
        result, enqueued, _abandoned = await self._through_registered_ui_dialog(
            {"state": "cancelled", "elapsed_s": 1.0}
        )
        self.assertEqual(result["overall"], "FAIL")
        self.assertEqual(result["stopped_at"], "S1")
        self.assertEqual(result["reason"], "restart_not_confirmed")
        self.assertEqual(
            result["steps"][0]["observed"],
            {"ok": 1, "state": "cancelled", "elapsed_s": 1.0},
        )
        self.assertEqual(len(enqueued), 1)

    async def test_out_of_range_timeout_shows_no_dialog(self) -> None:
        # The bridge would answer Yes; the request must be refused before it.
        result, enqueued, _abandoned = await self._through_registered_ui_dialog(
            {"state": "completed", "choice": "yes", "elapsed_s": 1.0},
            {"timeout_s": 300},
        )
        self.assertEqual(result["overall"], "FAIL")
        self.assertEqual(result["stopped_at"], "S1")
        self.assertTrue(
            (result["reason"] or "").startswith("tool_error:bad_args: timeout_s")
        )
        self.assertEqual(enqueued, [])


if __name__ == "__main__":
    unittest.main()
