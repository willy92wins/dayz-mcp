from __future__ import annotations

import asyncio
import base64
import inspect
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import agent_loop, inbox, server
from dayz_mcp.server import ServerConfig, build_app
from tests.test_client_mode import _fixture_client_runtime
from tests.test_mcp_tools import _content_json


LOW_LEVEL_TOOLS = (
    "session_acquire",
    "session_wait",
    "session_cancel",
    "session_heartbeat",
)


class WeakAgentCatalogTest(unittest.IsolatedAsyncioTestCase):
    async def test_session_catalog_marks_low_level_and_registers_alias(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _m: None))
        tools = {tool.name: tool for tool in await app.list_tools()}
        for name in LOW_LEVEL_TOOLS:
            self.assertIn(name, tools)
            description = tools[name].description or ""
            self.assertTrue(
                description.startswith("LOW-LEVEL: "),
                f"{name} description={description!r}",
            )
            self.assertIn("session_acquire_wait", description)
        self.assertIn("session_acquire_wait", tools)
        self.assertTrue(
            (tools["session_acquire_wait"].description or "").startswith("Preferred: "),
            tools["session_acquire_wait"].description,
        )
        self.assertIn("lease_acquire", tools)
        self.assertEqual(
            tools["lease_acquire"].description,
            "alias of session_acquire_wait",
        )
        acquire_fn = app._tool_manager.get_tool("session_acquire_wait").fn
        alias_fn = app._tool_manager.get_tool("lease_acquire").fn
        self.assertIs(acquire_fn, alias_fn)
        self.assertEqual(
            inspect.signature(acquire_fn),
            inspect.signature(alias_fn),
        )
        wait_desc = tools["wait_for"].description or ""
        self.assertIn("players_at_least", wait_desc)
        self.assertIn("players_at_most", wait_desc)
        self.assertIn("log_matches", wait_desc)
        self.assertIn("playbook_run", tools)
        playbook_desc = tools["playbook_run"].description or ""
        self.assertTrue(playbook_desc.startswith("Requires a lease (session_acquire_wait)."))
        self.assertIn("checklist", playbook_desc.lower())
        self.assertIn("Does not launch DayZ", playbook_desc)
        self.assertLessEqual(len(playbook_desc), 200)
        for name in (
            "player_teleport",
            "world_spawn",
            "inventory_give",
            "action_use",
            "ui_click",
            "ui_set_text",
            "ui_dialog",
            "playbook_run",
            "vehicle_trace",
            "dayz_test_close",
        ):
            first = (tools[name].description or "").splitlines()[0]
            self.assertTrue(
                first.startswith("Requires a lease (session_acquire_wait)."),
                f"{name}={first!r}",
            )

    async def test_playbook_run_unknown_name_is_bad_args(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _m: None))
        with self.assertRaises(Exception) as ctx:
            await app.call_tool("playbook_run", {"name": "not_in_dictionary"})
        message = str(ctx.exception)
        self.assertIn("bad_args: name", message)
        self.assertIn("not_in_dictionary", message)

    def test_fastmcp_instructions_name_the_junior_flow(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _m: None))
        text = app.instructions or ""
        self.assertIn("session_acquire_wait", text)
        self.assertIn("bridge_status.ready", text)
        self.assertIn("wait_for", text)
        self.assertIn("session_release", text)
        self.assertIn("place_safely", text)
        self.assertIn("lookback_lines", text)


class WeakAgentWaitForTest(unittest.IsolatedAsyncioTestCase):
    def test_wait_for_timeout_ok_stays_true(self) -> None:
        # Timeout is a normal result. Clients that treat ok:false as a tool
        # error would convert a quiet wait into a failure.
        unsatisfied = server._wait_for_response(
            condition="players_at_least",
            started=time.monotonic(),
            probes=1,
            observed=0,
            satisfied=False,
        )
        self.assertIs(unsatisfied["ok"], True)
        self.assertIs(unsatisfied["satisfied"], False)
        self.assertTrue(unsatisfied["timed_out"])
        self.assertEqual(unsatisfied["tool"], "wait_for")
        satisfied = server._wait_for_response(
            condition="players_at_least",
            started=time.monotonic(),
            probes=1,
            observed=2,
            satisfied=True,
        )
        self.assertIs(satisfied["ok"], True)
        self.assertIs(satisfied["satisfied"], True)
        self.assertFalse(satisfied["timed_out"])

    async def test_wait_for_bad_args_name_the_field(self) -> None:
        runtime = server.Runtime(ServerConfig(log_sink=lambda _m: None))
        cases = (
            ("log_matches", {"pattern": ""}, "pattern"),
            ("players_at_least", {"value": -1}, "value"),
            ("players_at_least", {"value": 1, "timeout_s": 0}, "timeout_s"),
            ("players_at_least", {"value": 1, "poll_interval_s": 0}, "poll_interval_s"),
        )
        for condition, kwargs, field in cases:
            with self.subTest(field=field):
                with self.assertRaises(server.ToolError) as ctx:
                    await server.execute_wait_for(runtime, condition, **kwargs)
                message = str(ctx.exception)
                self.assertIn("bad_args", message)
                self.assertIn(field, message)

    async def test_wait_for_timeout_error_names_wait_for_not_probe(self) -> None:
        class _TimeoutRuntime:
            tool_lock = asyncio.Lock()

            async def call_bridge(self, cmd, args, peer, timeout_s):
                raise server.ToolError(
                    "timeout waiting for query_all_players id=12; "
                    "server peer last poll 745.1s ago"
                )

        with self.assertRaises(server.ToolError) as ctx:
            await server.execute_wait_for(
                _TimeoutRuntime(),
                "players_at_least",
                value=1,
                timeout_s=1.0,
                poll_interval_s=0.5,
            )
        message = str(ctx.exception)
        self.assertIn("wait_for", message)
        self.assertNotIn("query_all_players", message)


class WeakAgentLeaseRequiredTest(unittest.TestCase):
    def test_lease_required_recipe_names_session_acquire_wait(self) -> None:
        message = server._public_enqueue_error({"error": "lease_required"})
        self.assertIn("lease_required", message)
        self.assertIn("session_acquire_wait", message)
        self.assertIn("purpose=", message)


class WeakAgentReadyTest(unittest.TestCase):
    def test_stale_peers_are_not_ready(self) -> None:
        status = {
            "server_peer": {
                "last_poll_age_s": 52.0,
                "version_state": "ok",
            },
            "client_peer": {
                "last_poll_age_s": None,
                "version_state": "legacy_blocked",
            },
        }
        ready = server.compute_bridge_ready(status)
        self.assertIs(ready["ready"], False)
        self.assertEqual(ready["reason"], "server_poll_stale")

    def test_never_polled_is_no_run(self) -> None:
        status = {
            "server_peer": {"last_poll_age_s": None, "version_state": "legacy"},
            "client_peer": {"last_poll_age_s": None, "version_state": "legacy"},
        }
        ready = server.compute_bridge_ready(status)
        self.assertIs(ready["ready"], False)
        self.assertEqual(ready["reason"], "no_run")

    def test_server_only_run_is_client_not_polling(self) -> None:
        status = {
            "server_peer": {"last_poll_age_s": 0.2, "version_state": "ok"},
            "client_peer": {
                "last_poll_age_s": None,
                "version_state": "legacy_blocked",
            },
        }
        ready = server.compute_bridge_ready(status)
        self.assertIs(ready["ready"], False)
        self.assertEqual(ready["reason"], "client_not_polling")

    def test_fresh_ok_peers_are_ready(self) -> None:
        status = {
            "server_peer": {"last_poll_age_s": 0.2, "version_state": "ok"},
            "client_peer": {"last_poll_age_s": 0.3, "version_state": "ok"},
        }
        ready = server.compute_bridge_ready(status)
        self.assertIs(ready["ready"], True)
        self.assertEqual(ready["reason"], "ready")

    def test_unpolled_version_block_is_game_not_ready(self) -> None:
        message = server._public_enqueue_error(
            {
                "error": "version_blocked",
                "state": "legacy_blocked",
                "got": None,
                "detail": "poll did not include ver=",
                "expected": "7",
            },
            status_snapshot={
                "server_peer": {"last_poll_age_s": None, "version_state": "legacy_blocked"},
                "client_peer": {"last_poll_age_s": None, "version_state": "legacy"},
            },
        )
        self.assertTrue(message.startswith("game_not_ready"), message)
        self.assertIn("reason=", message)
        self.assertNotIn("version_blocked:bridge", message)

    def test_live_mismatch_stays_version_blocked(self) -> None:
        message = server._public_enqueue_error(
            {
                "error": "version_blocked",
                "state": "version_mismatch",
                "got": "6",
                "expected": "7",
                "detail": "bridge_version '6' != '7'",
            },
            status_snapshot={
                "server_peer": {"last_poll_age_s": 0.1, "version_state": "version_mismatch"},
                "client_peer": {"last_poll_age_s": 0.1, "version_state": "ok"},
            },
        )
        self.assertTrue(message.startswith("version_blocked"), message)

    def test_client_target_never_polled_is_game_not_ready(self) -> None:
        message = server._public_enqueue_error(
            {
                "error": "version_blocked",
                "state": "legacy_blocked",
                "got": None,
                "expected": "7",
            },
            status_snapshot={
                "server_peer": {"last_poll_age_s": 0.2, "version_state": "ok"},
                "client_peer": {
                    "last_poll_age_s": None,
                    "version_state": "legacy_blocked",
                },
            },
            peer="client",
        )
        self.assertTrue(message.startswith("game_not_ready"), message)
        self.assertIn("client_not_polling", message)

    def test_lease_required_keeps_recipe_with_live_version_block(self) -> None:
        message = server._public_enqueue_error(
            {
                "error": "lease_required",
                "version_state": "version_mismatch",
                "got": "6",
                "expected": "7",
            },
            status_snapshot={
                "server_peer": {
                    "last_poll_age_s": 0.1,
                    "version_state": "version_mismatch",
                },
                "client_peer": {"last_poll_age_s": 0.1, "version_state": "ok"},
            },
            peer="server",
        )
        self.assertIn("session_acquire_wait", message)
        self.assertIn("version_blocked", message)


class WeakAgentWorldReadFailFastTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _not_ready_snapshot() -> dict[str, object]:
        return {
            "server_peer": {"last_poll_age_s": None, "version_state": "ok"},
            "client_peer": {"last_poll_age_s": None, "version_state": "ok"},
        }

    async def test_embedded_world_read_returns_not_ready_without_enqueue(self) -> None:
        runtime = server.Runtime(ServerConfig(log_sink=lambda _m: None))
        runtime.loopback = MagicMock()
        runtime.loopback.state.enqueue_command.return_value = (200, {"id": 1})
        runtime.loopback.state.take_result.return_value = None
        runtime.status = MagicMock(return_value=self._not_ready_snapshot())
        started = time.monotonic()

        result = await runtime.call_bridge(
            "query_all_players", {}, "server", timeout_s=5.0
        )

        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.5, f"world-read waited {elapsed:.3f}s")
        self.assertIs(result["ok"], False)
        self.assertEqual(result["code"], "not_ready")
        self.assertEqual(result["reason"], "no_run")
        self.assertEqual(result["next_step"], {"tool": "bridge_status", "args": {}})
        self.assertLess(len(server.json.dumps(result, separators=(",", ":"))), 500)
        self.assertNotEqual(result["next_step"]["tool"], "lifecycle_status")
        runtime.loopback.state.enqueue_command.assert_not_called()

    async def test_client_world_read_returns_not_ready_without_enqueue(self) -> None:
        runtime = _fixture_client_runtime(
            ServerConfig(
                mode="client",
                key="k",
                port=12345,
                log_sink=lambda _m: None,
            )
        )
        runtime.bridge_status_payload = AsyncMock(
            return_value=self._not_ready_snapshot()
        )
        runtime._call = MagicMock(
            side_effect=AssertionError("world-read must not enqueue")
        )

        result = await runtime.call_bridge(
            "entities_query",
            {"pos": [1.0, 2.0, 3.0], "radius": 10.0, "limit": 32},
            "server",
            timeout_s=5.0,
        )

        self.assertEqual(result["code"], "not_ready")
        self.assertEqual(result["next_step"]["tool"], "bridge_status")
        runtime._call.assert_not_called()


class WeakAgentClientModeEnqueueTest(unittest.IsolatedAsyncioTestCase):
    async def test_call_bridge_unpolled_target_is_game_not_ready(self) -> None:
        runtime = _fixture_client_runtime(
            ServerConfig(
                mode="client",
                key="k",
                port=12345,
                log_sink=lambda _m: None,
            )
        )
        runtime._call = lambda *_a, **_k: (
            409,
            {
                "error": "version_blocked",
                "expected": "7",
                "got": None,
                "state": "legacy_blocked",
            },
        )

        async def status_unpolled_client(**_k):
            return {
                "server_peer": {"last_poll_age_s": 0.2, "version_state": "ok"},
                "client_peer": {
                    "last_poll_age_s": None,
                    "version_state": "legacy_blocked",
                },
            }

        runtime.bridge_status_payload = status_unpolled_client
        with self.assertRaises(server.ToolError) as ctx:
            await runtime.call_bridge("camera_set", {}, "client", 1.0)
        message = str(ctx.exception)
        self.assertTrue(message.startswith("game_not_ready"), message)
        self.assertIn("client_not_polling", message)

    async def test_call_bridge_live_mismatch_stays_version_blocked(self) -> None:
        runtime = _fixture_client_runtime(
            ServerConfig(
                mode="client",
                key="k",
                port=12345,
                log_sink=lambda _m: None,
            )
        )
        runtime._call = lambda *_a, **_k: (
            409,
            {
                "error": "version_blocked",
                "expected": "7",
                "got": "6",
                "state": "version_mismatch",
            },
        )

        async def status_live_mismatch(**_k):
            return {
                "server_peer": {"last_poll_age_s": 0.1, "version_state": "ok"},
                "client_peer": {
                    "last_poll_age_s": 0.1,
                    "version_state": "version_mismatch",
                },
            }

        runtime.bridge_status_payload = status_live_mismatch
        with self.assertRaises(server.ToolError) as ctx:
            await runtime.call_bridge("camera_set", {}, "client", 1.0)
        self.assertTrue(str(ctx.exception).startswith("version_blocked"))


class WeakAgentLogsSinceTest(unittest.IsolatedAsyncioTestCase):
    async def test_logs_since_without_marker_skips_files_older_than_launch(self) -> None:
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _m: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, _built = build_app(config)

        with tempfile.TemporaryDirectory() as directory:
            profiles = Path(directory) / "_server" / "profiles"
            profiles.mkdir(parents=True)
            old = profiles / "DayZDiag_x64_2026-07-26.rpt"
            old.write_text("july line\n", encoding="utf-8")
            os.utime(old, (time.time() - 30 * 24 * 3600, time.time() - 30 * 24 * 3600))
            new_log = profiles / "script_2026-08-17.log"
            new_rpt = profiles / "DayZDiag_x64_2026-08-17.rpt"
            new_log.write_text("boot line\n", encoding="utf-8")
            new_rpt.write_text("engine warning\n", encoding="utf-8")
            launch = time.time() - 5.0
            lifecycle = AsyncMock(
                return_value={
                    "runs": [
                        {
                            "run_id": "run-a",
                            "profiles": str(profiles),
                            "processes": [
                                {
                                    "creation_time_utc": time.strftime(
                                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(launch)
                                    )
                                }
                            ],
                        }
                    ]
                }
            )
            with patch.object(runtime, "lifecycle_status", new=lifecycle):
                first = _content_json(await app.call_tool("logs_since", {}))

        names = {Path(item["path"]).name for item in first["files"]}
        self.assertNotIn(old.name, names)
        self.assertIn(new_log.name, names)
        self.assertIn(new_rpt.name, names)
        for item in first["files"]:
            self.assertNotIn("july line", item["lines"])

    async def test_logs_since_without_start_epoch_pairs_newest_rpt_and_script(self) -> None:
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _m: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, _built = build_app(config)

        with tempfile.TemporaryDirectory() as directory:
            profiles = Path(directory) / "_server" / "profiles"
            profiles.mkdir(parents=True)
            old = profiles / "DayZDiag_x64_2026-07-26.rpt"
            old.write_text("july line\n", encoding="utf-8")
            os.utime(old, (time.time() - 30 * 24 * 3600, time.time() - 30 * 24 * 3600))
            quiet = profiles / "script_2026-08-17.log"
            quiet.write_text("quiet current\n", encoding="utf-8")
            os.utime(quiet, (time.time() - 400, time.time() - 400))
            new_rpt = profiles / "DayZDiag_x64_2026-08-17.rpt"
            new_rpt.write_text("engine warning\n", encoding="utf-8")
            lifecycle = AsyncMock(
                return_value={
                    "runs": [
                        {"run_id": "run-a", "profiles": str(profiles)},
                    ]
                }
            )
            with patch.object(runtime, "lifecycle_status", new=lifecycle):
                first = _content_json(await app.call_tool("logs_since", {}))

        names = {Path(item["path"]).name for item in first["files"]}
        self.assertNotIn(old.name, names)
        self.assertIn(quiet.name, names)
        self.assertIn(new_rpt.name, names)


class WeakAgentCaptureWebpTest(unittest.IsolatedAsyncioTestCase):
    async def test_capture_webp_uses_webp_mime(self) -> None:
        app, runtime = build_app(ServerConfig(log_sink=lambda _m: None))
        tool = app._tool_manager.get_tool("capture_screenshot")
        payload = {
            "inline": {
                "data": base64.b64encode(b"RIFF....WEBP").decode("ascii"),
                "mimeType": "image/webp",
            }
        }
        with patch.object(server.mcp_capture, "capture_dual", return_value=payload):
            result = await tool.fn(fmt="webp")
        # The tool returns [Image, meta JSON] in both save_fullres arms (ficha 268a); the
        # image is the first block and keeps the encoder's mime type.
        self.assertIsInstance(result, list)
        self.assertEqual(2, len(result))
        image = result[0]
        self.assertEqual(image._format, "webp")
        self.assertEqual(image._mime_type, "image/webp")
        content = image.to_image_content()
        self.assertEqual(content.mimeType, "image/webp")


class WeakAgentAutospawnTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_autospawn_raises_typed_recipe_on_ensure_path(self) -> None:
        runtime = _fixture_client_runtime(
            ServerConfig(
                mode="client",
                key="k",
                port=12345,
                auto_spawn_daemon=False,
                log_sink=lambda _m: None,
            ),
            spawn_fn=lambda: (_ for _ in ()).throw(AssertionError("spawn")),
            probe_fn=lambda *_a, **_k: False,
            startup_budget_s=0.01,
        )
        self.assertFalse(runtime._ensure_daemon())
        with self.assertRaises(server.ToolError) as ctx:
            # Force the retryable path used when the first request cannot connect.
            from dayz_mcp.accredited_daemon_transport import AccreditedTransportError

            def boom(*_a, **_k):
                raise AccreditedTransportError(
                    "offline",
                    request_stage="pre_request",
                    http_bytes_sent=0,
                )

            runtime._request_once = boom
            runtime._call("GET", "/status", timeout=0.2)
        message = str(ctx.exception)
        self.assertIn("daemon_autospawn_disabled", message)
        self.assertIn("--no-daemon-autospawn", message)

    def test_failed_autospawn_attempted_once_then_named_error(self) -> None:
        spawns: list[int] = []
        runtime = _fixture_client_runtime(
            ServerConfig(
                mode="client",
                key="k",
                port=12345,
                auto_spawn_daemon=True,
                log_sink=lambda _m: None,
            ),
            spawn_fn=lambda: spawns.append(1) or 4321,
            probe_fn=lambda *_a, **_k: False,
            startup_budget_s=0.01,
        )
        self.assertFalse(runtime._ensure_daemon())
        self.assertFalse(runtime._ensure_daemon())
        self.assertEqual(len(spawns), 1)
        from dayz_mcp.accredited_daemon_transport import AccreditedTransportError

        def boom(*_a, **_k):
            raise AccreditedTransportError(
                "offline",
                request_stage="pre_request",
                http_bytes_sent=0,
            )

        runtime._request_once = boom
        with self.assertRaises(server.ToolError) as ctx:
            runtime._call("GET", "/status", timeout=0.2)
        self.assertIn("autospawn already attempted", str(ctx.exception))


class WeakAgentInboxFieldTest(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_dir = inbox.INBOX_DIR
        self._orig_path = inbox.FEEDBACK_PATH
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        inbox.INBOX_DIR = root / "inbox"
        inbox.FEEDBACK_PATH = inbox.INBOX_DIR / "feedback.jsonl"

    def tearDown(self) -> None:
        inbox.INBOX_DIR = self._orig_dir
        inbox.FEEDBACK_PATH = self._orig_path
        self._tmp.cleanup()

    def test_title_limit_names_field_and_limit(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            inbox.append_feedback("bug", "x" * 121, "body")
        message = str(ctx.exception)
        self.assertIn("title", message)
        self.assertIn("120", message)


class WeakAgentForeignPortsTest(unittest.TestCase):
    def test_foreign_ports_flag_dayz_relevant_and_keep_noise_visible(self) -> None:
        # Noise stays on foreign_ports_all; default foreign_ports is DayZ-related.
        box = {
            "foreign_ports": [2302],
            "foreign_ports_all": [53, 2302, 65530],
            "foreign_ports_meta": {"kind": "os_socket_table_ignored_for_occupancy"},
        }
        annotated = server._annotate_box_foreign_ports(box)
        self.assertEqual(
            annotated["foreign_ports"],
            [{"port": 2302, "dayz_relevant": True}],
        )
        self.assertEqual(
            annotated["foreign_ports_all"],
            [
                {"port": 53, "dayz_relevant": False},
                {"port": 2302, "dayz_relevant": True},
                {"port": 65530, "dayz_relevant": False},
            ],
        )
        self.assertEqual(annotated["foreign_ports_meta"]["count"], 1)
        self.assertEqual(annotated["foreign_ports_meta"]["count_all"], 3)
        self.assertEqual(annotated["foreign_ports_meta"]["dayz_relevant"], 1)
        self.assertFalse(server._foreign_ports_contain(annotated["foreign_ports"], 53))
        self.assertTrue(server._foreign_ports_contain(annotated["foreign_ports_all"], 53))
        self.assertTrue(server._foreign_ports_contain(annotated["foreign_ports"], 2302))
        self.assertFalse(server._port_is_dayz_relevant(53))
        self.assertTrue(server._port_is_dayz_relevant(2302))

    def test_annotate_filters_default_and_keeps_all(self) -> None:
        box = {
            "foreign_ports": [2402],
            "foreign_ports_all": [53, 500, 2402],
            "foreign_ports_meta": {"kind": "os_socket_table_ignored_for_occupancy"},
        }
        annotated = server._annotate_box_foreign_ports(box)
        self.assertEqual(
            annotated["foreign_ports"],
            [{"port": 2402, "dayz_relevant": True}],
        )
        self.assertEqual(
            annotated["foreign_ports_all"],
            [
                {"port": 53, "dayz_relevant": False},
                {"port": 500, "dayz_relevant": False},
                {"port": 2402, "dayz_relevant": True},
            ],
        )
        self.assertEqual(annotated["foreign_ports_meta"]["count"], 1)
        self.assertEqual(annotated["foreign_ports_meta"]["count_all"], 3)
        self.assertEqual(annotated["foreign_ports_meta"]["dayz_relevant"], 1)

    def test_port_conflict_still_sees_annotated_foreign_ports(self) -> None:
        box = {
            "foreign_ports": [{"port": 2402, "dayz_relevant": True}],
            "runs": [],
            "port_scan_known": True,
        }
        fields = server._port_conflict_fields(box, 2402)
        self.assertEqual(fields["reason"], "port_in_use_foreign")
        self.assertEqual(fields["port"], 2402)

    def test_port_conflict_uses_foreign_ports_all(self) -> None:
        box = {
            "foreign_ports": [2302],
            "foreign_ports_all": [2302, 3002],
            "runs": [],
            "port_scan_known": True,
        }
        self.assertFalse(server._foreign_ports_contain(box["foreign_ports"], 3002))
        self.assertTrue(server._foreign_ports_contain(box["foreign_ports_all"], 3002))
        fields = server._port_conflict_fields(box, 3002)
        self.assertEqual(fields["reason"], "port_in_use_foreign")
        self.assertEqual(fields["port"], 3002)

    def test_structured_dayz_relevant_is_recalculated_from_port(self) -> None:
        box = {
            "foreign_ports": [
                {"port": 2302, "dayz_relevant": False},
                {"port": 53, "dayz_relevant": True},
            ],
        }
        annotated = server._annotate_box_foreign_ports(box)
        self.assertEqual(
            annotated["foreign_ports"],
            [
                {"port": 2302, "dayz_relevant": True},
                {"port": 53, "dayz_relevant": False},
            ],
        )
        self.assertEqual(annotated["foreign_ports_meta"]["count"], 2)
        self.assertEqual(annotated["foreign_ports_meta"]["dayz_relevant"], 1)


class WeakAgentOkNextStepTest(unittest.TestCase):
    def test_ok_mutation_payload_names_public_next_step(self) -> None:
        result = server._with_ok_next_step(
            {"ok": 1, "object_id": 7}, "world_spawn"
        )
        self.assertEqual(result["ok"], 1)
        self.assertIn(result["next_step"], agent_loop.PUBLIC_NEXT_TOOLS)
        self.assertEqual(result["next_step"], "session_heartbeat")
        self.assertEqual(
            agent_loop.ok_next_step("world_spawn", mutating=True),
            "session_heartbeat",
        )

    def test_ok_session_payload_names_public_next_step(self) -> None:
        acquired = server._with_ok_next_step(
            {"status": "active", "lease_token": "tok"}, "session_acquire_wait"
        )
        self.assertIs(acquired["ok"], True)
        self.assertEqual(acquired["next_step"], "bridge_status")
        self.assertIn(acquired["next_step"], agent_loop.PUBLIC_NEXT_TOOLS)

        released = server._with_ok_next_step({"ok": True}, "session_release")
        self.assertEqual(released["next_step"], "session_acquire_wait")
        self.assertIn(released["next_step"], agent_loop.PUBLIC_NEXT_TOOLS)

    def test_read_only_ok_payload_does_not_invent_next_step(self) -> None:
        result = server._with_ok_next_step(
            {"ok": 1, "players": []}, "query_all_players"
        )
        self.assertNotIn("next_step", result)

    def test_error_payload_does_not_get_ok_next_step(self) -> None:
        result = server._with_ok_next_step(
            {"ok": False, "error": "no_players"}, "notify_players"
        )
        self.assertNotIn("next_step", result)

    def test_heartbeat_next_step_advances_and_does_not_loop(self) -> None:
        first = server._with_ok_next_step({"ok": True}, "session_heartbeat")
        second = server._with_ok_next_step({"ok": True}, "session_heartbeat")
        self.assertEqual(first["next_step"], "bridge_status")
        self.assertEqual(second["next_step"], "bridge_status")
        self.assertNotEqual(first["next_step"], "session_heartbeat")
        self.assertEqual(
            agent_loop.ok_next_step("session_heartbeat"),
            "bridge_status",
        )
        copied = first["next_step"]
        self.assertNotEqual(copied, "session_heartbeat")
        self.assertEqual(agent_loop.ok_next_step(copied), None)

    def test_existing_public_next_step_is_preserved(self) -> None:
        kept = server._with_ok_next_step(
            {"ok": True, "next_step": "wait_for"}, "world_spawn"
        )
        self.assertEqual(kept["next_step"], "wait_for")
        overwritten = server._with_ok_next_step(
            {"ok": True, "next_step": "lifecycle_status"}, "world_spawn"
        )
        self.assertEqual(overwritten["next_step"], "session_heartbeat")


if __name__ == "__main__":
    unittest.main()
