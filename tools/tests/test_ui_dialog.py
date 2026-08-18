from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, result_prune, server, ui_dialog
from dayz_mcp.server import ServerConfig, build_app
from tests.test_client_mode import _fixture_client_runtime


def _seven_fields() -> list[dict[str, object]]:
    return [{"id": f"f{index}", "label": f"Field {index}"} for index in range(7)]


def _wire(dialog: dict, **top: object) -> dict:
    payload: dict = {"ok": 1, "dialog": dialog}
    payload.update(top)
    return payload


class _FakeDialogRuntime:
    def __init__(self) -> None:
        self.tool_lock = asyncio.Lock()
        self.enqueued: list[tuple[str, dict, str, float]] = []
        self.abandoned: list[tuple[int, str]] = []
        self._next_id = 1
        self.results: dict[int, dict] = {}

    async def enqueue_bridge(self, cmd: str, args: dict, peer: str, timeout_s: float) -> int:
        self.enqueued.append((cmd, args, peer, timeout_s))
        command_id = self._next_id
        self._next_id += 1
        return command_id

    async def probe_bridge_result(self, cmd: str, command_id: int, peer: str) -> dict | None:
        return self.results.pop(command_id, None)

    async def abandon_bridge(self, command_id: int, reason: str) -> None:
        self.abandoned.append((command_id, reason))


class UiDialogValidationTest(unittest.TestCase):
    def test_unknown_field_key_is_rejected(self) -> None:
        with self.assertRaises(ui_dialog.UiDialogError) as ctx:
            ui_dialog.parse_request(
                "form",
                "Title",
                "optional",
                [
                    {"id": "name", "label": "Name"},
                    {"id": "note", "label": "Note", "colour": "red"},
                ],
            )
        self.assertEqual(
            str(ctx.exception),
            "bad_args: unknown key 'colour' in fields[1]",
        )

    def test_n_plus_one_fields_is_bad_args(self) -> None:
        with self.assertRaises(ui_dialog.UiDialogError) as ctx:
            ui_dialog.parse_request("form", "Title", "", _seven_fields())
        self.assertEqual(str(ctx.exception), "bad_args: fields has 7 items, max 6")

    def test_duplicate_field_id_names_both_indexes(self) -> None:
        with self.assertRaises(ui_dialog.UiDialogError) as ctx:
            ui_dialog.parse_request(
                "form",
                "Title",
                "",
                [
                    {"id": "name", "label": "A"},
                    {"id": "note", "label": "B"},
                    {"id": "name", "label": "C"},
                ],
            )
        self.assertEqual(
            str(ctx.exception),
            "bad_args: fields[2].id duplicates fields[0].id",
        )

    def test_fields_forbidden_on_acknowledge(self) -> None:
        with self.assertRaises(ui_dialog.UiDialogError) as ctx:
            ui_dialog.parse_request(
                "acknowledge",
                "Title",
                "Body",
                [{"id": "name", "label": "Name"}],
            )
        self.assertIn("bad_args: fields", str(ctx.exception))

    def test_acknowledge_requires_message(self) -> None:
        with self.assertRaises(ui_dialog.UiDialogError) as ctx:
            ui_dialog.parse_request("acknowledge", "Title", "   ")
        self.assertIn("bad_args: message", str(ctx.exception))

    def test_timeout_bounds(self) -> None:
        for bad in (4.99, 240.01, float("nan"), float("inf"), True, "60"):
            with self.subTest(bad=bad):
                with self.assertRaises(ui_dialog.UiDialogError) as ctx:
                    ui_dialog.parse_request("acknowledge", "Title", "Body", None, bad)
                self.assertIn("timeout_s", str(ctx.exception))

    def test_bridge_args_sends_object_array_not_parallel_arrays(self) -> None:
        request = ui_dialog.parse_request(
            "form",
            "Title",
            "",
            [{"id": "name", "label": "Name"}],
        )
        payload = ui_dialog.bridge_args(request)
        self.assertIn("fields", payload)
        self.assertNotIn("field_ids", payload)
        self.assertEqual(
            payload["fields"],
            [
                {
                    "id": "name",
                    "label": "Name",
                    "required": True,
                    "default": "",
                }
            ],
        )

    def test_missing_id_and_label_names_id_first(self) -> None:
        with self.assertRaises(ui_dialog.UiDialogError) as ctx:
            ui_dialog.parse_request("form", "Title", "", [{}])
        self.assertEqual(str(ctx.exception), "bad_args: fields[0].id is required")

    def test_unhashable_kind_is_bad_args_not_typeerror(self) -> None:
        with self.assertRaises(ui_dialog.UiDialogError) as ctx:
            ui_dialog.parse_request(["form"], "Title", "Body")
        self.assertIn("bad_args: kind", str(ctx.exception))


class UiDialogResultTest(unittest.TestCase):
    def _ack(self) -> ui_dialog.UiDialogRequest:
        return ui_dialog.parse_request("acknowledge", "T", "M")

    def _confirm(self) -> ui_dialog.UiDialogRequest:
        return ui_dialog.parse_request("confirm", "T", "M")

    def _form(self) -> ui_dialog.UiDialogRequest:
        return ui_dialog.parse_request(
            "form",
            "T",
            "",
            [
                {"id": "name", "label": "Name"},
                {"id": "note", "label": "Note"},
            ],
        )

    def test_completed_acknowledge(self) -> None:
        result = ui_dialog.interpret_result(
            self._ack(),
            _wire({"state": "completed", "dismissed_by": "ok", "elapsed_s": 1.2}),
        )
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["dismissed_by"], "ok")
        self.assertNotIn("dialog", result)

    def test_completed_confirm_yes_and_no(self) -> None:
        for choice in ("yes", "no"):
            result = ui_dialog.interpret_result(
                self._confirm(),
                _wire({"state": "completed", "choice": choice, "elapsed_s": 0.5}),
            )
            self.assertEqual(result["choice"], choice)

    def test_completed_form_adds_values_by_id_in_declared_order(self) -> None:
        result = ui_dialog.interpret_result(
            self._form(),
            _wire(
                {
                    "state": "completed",
                    "elapsed_s": 3,
                    "values": [
                        {"id": "name", "value": "North"},
                        {"id": "note", "value": ""},
                    ],
                }
            ),
        )
        self.assertEqual(result["values_by_id"], {"name": "North", "note": ""})
        self.assertEqual([item["id"] for item in result["values"]], ["name", "note"])

    def test_cancelled_is_not_choice_no(self) -> None:
        result = ui_dialog.interpret_result(
            self._confirm(),
            _wire({"state": "cancelled", "elapsed_s": 0.2}),
        )
        self.assertEqual(result["state"], "cancelled")
        self.assertNotIn("choice", result)

    def test_cancelled_with_choice_is_bridge_bad_result(self) -> None:
        with self.assertRaises(ui_dialog.UiDialogError) as ctx:
            ui_dialog.interpret_result(
                self._confirm(),
                _wire({"state": "cancelled", "choice": "no", "elapsed_s": 0.2}),
            )
        self.assertIn("bridge_bad_result", str(ctx.exception))

    def test_timed_out_and_disconnected_and_rejected(self) -> None:
        request = self._ack()
        timed = ui_dialog.interpret_result(
            request, _wire({"state": "timed_out", "elapsed_s": 60.0})
        )
        self.assertEqual(timed["state"], "timed_out")
        disconnected = ui_dialog.interpret_result(
            request, _wire({"state": "disconnected", "elapsed_s": 2.0})
        )
        self.assertEqual(disconnected["state"], "disconnected")
        rejected = ui_dialog.interpret_result(
            request,
            _wire({"state": "rejected", "reason": "busy", "elapsed_s": 0.01}),
        )
        self.assertEqual(rejected["reason"], "busy")

    def test_unknown_state_and_mismatched_values_and_bad_choice(self) -> None:
        with self.assertRaises(ui_dialog.UiDialogError) as ctx:
            ui_dialog.interpret_result(
                self._ack(), _wire({"state": "open", "elapsed_s": 1})
            )
        self.assertIn("bridge_bad_result", str(ctx.exception))
        with self.assertRaises(ui_dialog.UiDialogError) as ctx:
            ui_dialog.interpret_result(
                self._confirm(),
                _wire({"state": "completed", "choice": "maybe", "elapsed_s": 1}),
            )
        self.assertIn("bridge_bad_result", str(ctx.exception))
        with self.assertRaises(ui_dialog.UiDialogError) as ctx:
            ui_dialog.interpret_result(
                self._form(),
                _wire(
                    {
                        "state": "completed",
                        "elapsed_s": 1,
                        "values": [
                            {"id": "note", "value": "x"},
                            {"id": "name", "value": "y"},
                        ],
                    }
                ),
            )
        self.assertIn("bridge_bad_result", str(ctx.exception))

    def test_missing_dialog_is_bridge_bad_result(self) -> None:
        with self.assertRaises(ui_dialog.UiDialogError) as ctx:
            ui_dialog.interpret_result(self._ack(), {"ok": 1, "elapsed_s": 1})
        self.assertIn("bridge_bad_result: dialog missing", str(ctx.exception))

    def test_unhashable_state_is_bridge_bad_result(self) -> None:
        with self.assertRaises(ui_dialog.UiDialogError) as ctx:
            ui_dialog.interpret_result(
                self._ack(), _wire({"state": {}, "elapsed_s": 1})
            )
        self.assertIn("bridge_bad_result", str(ctx.exception))

    def test_passthrough_id_and_server_meta(self) -> None:
        meta = {"t_enqueue": 1.0, "rtt_s": 0.04}
        result = ui_dialog.interpret_result(
            self._ack(),
            _wire(
                {"state": "completed", "dismissed_by": "ok", "elapsed_s": 1.2},
                id=44,
                _server=meta,
            ),
        )
        self.assertEqual(result["id"], 44)
        self.assertEqual(result["_server"], meta)

    def test_prune_keeps_dialog_and_drops_empty_player_state(self) -> None:
        raw = _wire(
            {"state": "completed", "dismissed_by": "ok", "elapsed_s": 1.0},
            state={},
            players=[],
        )
        pruned = result_prune.prune_unfilled_fields("ui_dialog", raw)
        self.assertIn("dialog", pruned)
        self.assertEqual(pruned["dialog"]["state"], "completed")
        self.assertNotIn("state", pruned)
        self.assertNotIn("players", pruned)
        public = ui_dialog.interpret_result(self._ack(), pruned)
        self.assertEqual(public["state"], "completed")


class UiDialogExecuteTest(unittest.IsolatedAsyncioTestCase):
    async def test_n_plus_one_does_not_enqueue(self) -> None:
        runtime = _FakeDialogRuntime()
        with self.assertRaises(server.ToolError) as ctx:
            await server.execute_ui_dialog(
                runtime,
                "form",
                "Title",
                fields=_seven_fields(),
            )
        self.assertIn("bad_args: fields has 7 items, max 6", str(ctx.exception))
        self.assertEqual(runtime.enqueued, [])

    async def test_tool_rejects_n_plus_one_without_loopback(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _m: None))
        with self.assertRaises(Exception) as ctx:
            await app.call_tool(
                "ui_dialog",
                {
                    "kind": "form",
                    "title": "Title",
                    "fields": _seven_fields(),
                },
            )
        self.assertIn("bad_args", str(ctx.exception))
        self.assertNotIn("loopback not started", str(ctx.exception))

    async def test_sixty_second_wait_does_not_hold_tool_lock(self) -> None:
        runtime = _FakeDialogRuntime()
        acquired = asyncio.Event()
        started = time.monotonic()

        async def contender() -> None:
            await asyncio.sleep(0.05)
            async with runtime.tool_lock:
                acquired.set()

        waiter = asyncio.create_task(
            server.execute_ui_dialog(
                runtime,
                "acknowledge",
                "Title",
                message="Body",
                timeout_s=60.0,
            )
        )
        rival = asyncio.create_task(contender())
        try:
            await asyncio.wait_for(acquired.wait(), timeout=1.0)
        finally:
            waiter.cancel()
            rival.cancel()
            await asyncio.gather(waiter, rival, return_exceptions=True)
        self.assertTrue(acquired.is_set())
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(len(runtime.enqueued), 1)
        self.assertEqual(runtime.enqueued[0][3], 70.0)

    async def test_sleep_sees_unlocked_tool_lock(self) -> None:
        runtime = _FakeDialogRuntime()
        held: list[bool] = []
        saw_sleep = asyncio.Event()
        original = asyncio.sleep

        async def tracking_sleep(delay: float) -> None:
            held.append(runtime.tool_lock.locked())
            saw_sleep.set()
            await original(0)

        with patch("dayz_mcp.server.asyncio.sleep", tracking_sleep):
            waiter = asyncio.create_task(
                server.execute_ui_dialog(
                    runtime,
                    "acknowledge",
                    "Title",
                    message="Body",
                    timeout_s=60.0,
                )
            )
            try:
                await asyncio.wait_for(saw_sleep.wait(), timeout=1.0)
            finally:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
        self.assertTrue(held)
        self.assertTrue(all(item is False for item in held))

    async def test_transport_timeout_is_not_timed_out_state(self) -> None:
        runtime = _FakeDialogRuntime()
        clock = {"t": 0.0}

        def monotonic() -> float:
            return clock["t"]

        async def jump_sleep(delay: float) -> None:
            clock["t"] += 80.0

        with (
            patch("dayz_mcp.server.time.monotonic", monotonic),
            patch("dayz_mcp.server.asyncio.sleep", jump_sleep),
        ):
            with self.assertRaises(server.ToolError) as ctx:
                await server.execute_ui_dialog(
                    runtime,
                    "acknowledge",
                    "Title",
                    message="Body",
                    timeout_s=5.0,
                )
        message = str(ctx.exception)
        self.assertIn("timeout waiting for ui_dialog", message)
        self.assertEqual(runtime.abandoned[-1][1], "tool_timeout")

    async def test_bridge_timed_out_is_a_valid_result(self) -> None:
        runtime = _FakeDialogRuntime()
        runtime.results[1] = _wire({"state": "timed_out", "elapsed_s": 60.0})
        result = await server.execute_ui_dialog(
            runtime, "acknowledge", "Title", message="Body", timeout_s=5.0
        )
        self.assertEqual(result["state"], "timed_out")
        self.assertEqual(result["ok"], 1)
        self.assertEqual(runtime.abandoned, [])

    async def test_description_requires_lease_and_stays_short(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _m: None))
        tools = {tool.name: tool for tool in await app.list_tools()}
        self.assertIn("ui_dialog", tools)
        description = tools["ui_dialog"].description or ""
        first = description.splitlines()[0]
        self.assertTrue(first.startswith("Requires a lease (session_acquire_wait)."))
        self.assertIn("timeout_s", description)
        self.assertNotIn("tool_lock", description)
        self.assertLessEqual(len(description), 200)

    async def test_wait_for_docstring_names_both_entries(self) -> None:
        text = server.execute_wait_for.__doc__ or ""
        self.assertIn("wait_for", text)
        self.assertIn("ui_dialog", text)
        self.assertNotIn("only MCP entry", text)


class UiDialogClientRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_abandon_bridge_is_noop(self) -> None:
        runtime = _fixture_client_runtime(
            ServerConfig(
                mode="client",
                key="k",
                port=12345,
                log_sink=lambda _m: None,
            )
        )
        calls: list[tuple] = []

        def boom(*args: object, **kwargs: object) -> tuple[int, dict]:
            calls.append((args, kwargs))
            return 200, {}

        runtime._call = boom
        await runtime.abandon_bridge(9, "tool_timeout")
        self.assertEqual(calls, [])

    async def test_enqueue_and_probe_pending(self) -> None:
        runtime = _fixture_client_runtime(
            ServerConfig(
                mode="client",
                key="k",
                port=12345,
                log_sink=lambda _m: None,
            )
        )

        def fake_call(method, path, payload=None, query=None, timeout=5.0, deadline=None):
            if path == "/enqueue":
                return 200, {"id": 11}
            return 200, {"status": "pending"}

        runtime._call = fake_call
        command_id = await runtime.enqueue_bridge("ui_dialog", {"kind": "acknowledge"}, "client", 15.0)
        self.assertEqual(command_id, 11)
        self.assertIsNone(await runtime.probe_bridge_result("ui_dialog", 11, "client"))

    async def test_probe_done_ok_returns_pruned_dict(self) -> None:
        runtime = _fixture_client_runtime(
            ServerConfig(
                mode="client",
                key="k",
                port=12345,
                log_sink=lambda _m: None,
            )
        )

        def fake_call(method, path, payload=None, query=None, timeout=5.0, deadline=None):
            return 200, {
                "status": "done",
                "result": _wire(
                    {"state": "timed_out", "elapsed_s": 1.0},
                    state={},
                    players=[],
                ),
            }

        runtime._call = fake_call
        result = await runtime.probe_bridge_result("ui_dialog", 11, "client")
        self.assertIsNotNone(result)
        self.assertIn("dialog", result)
        self.assertEqual(result["dialog"]["state"], "timed_out")
        self.assertNotIn("players", result)
        self.assertNotIn("state", result)

    async def test_probe_done_ok_false_raises_bridge_error(self) -> None:
        runtime = _fixture_client_runtime(
            ServerConfig(
                mode="client",
                key="k",
                port=12345,
                log_sink=lambda _m: None,
            )
        )

        def fake_call(method, path, payload=None, query=None, timeout=5.0, deadline=None):
            return 200, {
                "status": "done",
                "result": {"ok": 0, "error": "unknown_command"},
            }

        runtime._call = fake_call
        with self.assertRaises(server.ToolError) as ctx:
            await runtime.probe_bridge_result("ui_dialog", 11, "client")
        self.assertIn("unknown_command", str(ctx.exception))

    async def test_enqueue_lease_required_maps_public_error(self) -> None:
        runtime = _fixture_client_runtime(
            ServerConfig(
                mode="client",
                key="k",
                port=12345,
                log_sink=lambda _m: None,
            )
        )
        runtime._call = lambda *_a, **_k: (403, {"error": "lease_required"})

        async def status_live(**_k):
            return {
                "server_peer": {"last_poll_age_s": 0.2, "version_state": "ok"},
                "client_peer": {"last_poll_age_s": 0.2, "version_state": "ok"},
            }

        runtime.bridge_status_payload = status_live
        with self.assertRaises(server.ToolError) as ctx:
            await runtime.enqueue_bridge("ui_dialog", {"kind": "acknowledge"}, "client", 15.0)
        self.assertIn("session_acquire_wait", str(ctx.exception))

    async def test_enqueue_version_blocked_maps_public_error(self) -> None:
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

        async def status_live(**_k):
            return {
                "server_peer": {"last_poll_age_s": 0.1, "version_state": "ok"},
                "client_peer": {
                    "last_poll_age_s": 0.1,
                    "version_state": "version_mismatch",
                },
            }

        runtime.bridge_status_payload = status_live
        with self.assertRaises(server.ToolError) as ctx:
            await runtime.enqueue_bridge("ui_dialog", {"kind": "acknowledge"}, "client", 15.0)
        self.assertTrue(str(ctx.exception).startswith("version_blocked"))

    async def test_enqueue_stale_lease_clears_token(self) -> None:
        runtime = _fixture_client_runtime(
            ServerConfig(
                mode="client",
                key="k",
                port=12345,
                log_sink=lambda _m: None,
            )
        )
        runtime.active_lease_token = "tok-1"
        runtime._call = lambda *_a, **_k: (409, {"error": "lease_expired"})
        with self.assertRaises(server.ToolError) as ctx:
            await runtime.enqueue_bridge("ui_dialog", {"kind": "acknowledge"}, "client", 15.0)
        self.assertEqual(str(ctx.exception), "lease_expired")
        self.assertIsNone(runtime.active_lease_token)


class UiDialogWhitelistTest(unittest.TestCase):
    def test_routes_to_client_peer(self) -> None:
        self.assertIn("ui_dialog", loopback.CLIENT_COMMANDS)
        self.assertEqual(loopback.peer_for_command("ui_dialog"), "client")
        ok, error = loopback.validate_command_args(
            "ui_dialog",
            {"kind": "acknowledge", "title": "T", "message": "M"},
        )
        self.assertTrue(ok)
        self.assertIsNone(error)
        ok, error = loopback.validate_command_args(
            "ui_dialog",
            {"kind": "form", "title": "T", "fields": _seven_fields()},
        )
        self.assertFalse(ok)
        self.assertEqual(error, "bad_args")


if __name__ == "__main__":
    unittest.main()
