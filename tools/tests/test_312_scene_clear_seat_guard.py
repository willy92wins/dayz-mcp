"""Issue #312: scene_clear refuses like object_delete while a client seat may exist.

scene_clear deletes vehicles too, so it runs the #302 client seat check of
object_delete before it sends anything to the server. The public MCP tool is
exercised with the game transport mocked; no live game is involved.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import server
from tests.mcp_helpers import _content_json

_SCENE = "fixture_312"
_CLEARED = {
    "ok": 1,
    "scene_clear": {"deleted": 2, "already_deleted": 0, "errors": 0, "error_ids": []},
}


class SceneClearSeatGuardTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        self.runtime.bridge_status_payload = AsyncMock(
            return_value={"client_peer": {"last_poll_age_s": 0.1}}
        )

    async def _clear(self, scene_id: str = _SCENE) -> dict:
        return _content_json(await self.app.call_tool(
            "scene_clear", {"scene_id": scene_id, "timeout_s": 2.0}
        ))

    def _assert_refused(self, result: dict, error: str) -> None:
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], error)
        self.assertEqual(result["scene_id"], _SCENE)
        self.assertEqual(result["deleted"], 0)
        self.assertNotIn("object_id", result)

    async def test_client_owned_seat_refuses_without_clearing(self) -> None:
        for authority in (0, False):
            with self.subTest(authority=authority):
                telemetry = {
                    "ok": 1, "seated": 1, "is_owner": 1,
                    "is_authority_owner": authority,
                    "type": "CivilianSedan", "net_id_low": 42, "net_id_high": 1,
                }
                call = AsyncMock(side_effect=lambda cmd, *_args:
                                 telemetry if cmd == "vehicle_telemetry" else _CLEARED)
                with patch.object(self.runtime, "call_bridge", new=call):
                    result = await self._clear()
                self._assert_refused(result, "vehicle_client_seated")
                self.assertIn("vehicle_enter", result["hint"])
                call.assert_awaited_once_with("vehicle_telemetry", {}, "client", 2.0)

    async def test_no_client_seat_clears_and_keeps_the_reply(self) -> None:
        for telemetry in (
            {"ok": 1, "seated": 0, "is_authority_owner": 0},
            {"ok": 1, "seated": 1, "is_owner": 0, "is_authority_owner": 1},
            {"ok": True, "seated": False},
        ):
            with self.subTest(telemetry=telemetry):
                reply = {
                    "ok": 1,
                    "scene_clear": {
                        "deleted": 2, "already_deleted": 0, "errors": 0, "error_ids": [],
                    },
                }
                call = AsyncMock(side_effect=lambda cmd, *_args:
                                 telemetry if cmd == "vehicle_telemetry" else reply)
                with patch.object(self.runtime, "call_bridge", new=call):
                    result = await self._clear()
                self.assertEqual(result, reply)
                self.assertEqual([item.args for item in call.await_args_list], [
                    ("vehicle_telemetry", {}, "client", 2.0),
                    ("scene_clear", {"scene_id": _SCENE}, "server", 2.0),
                ])

    async def test_unreadable_seat_never_clears(self) -> None:
        for telemetry in (
            {"ok": 1},
            {"ok": 1, "seated": "yes"},
            {"ok": 0, "seated": 0},
            None,
        ):
            with self.subTest(telemetry=telemetry):
                call = AsyncMock(return_value=telemetry)
                with patch.object(self.runtime, "call_bridge", new=call):
                    result = await self._clear()
                self._assert_refused(result, "vehicle_seat_unverified")
                call.assert_awaited_once_with("vehicle_telemetry", {}, "client", 2.0)

    async def test_unprobeable_client_never_probes_or_clears(self) -> None:
        for status in (
            {},
            {"client_peer": {"last_poll_age_s": None}},
            {"client_peer": {"last_poll_age_s": 20.0}},
        ):
            with self.subTest(status=status):
                self.runtime.bridge_status_payload = AsyncMock(return_value=status)
                call = AsyncMock(return_value=_CLEARED)
                with patch.object(self.runtime, "call_bridge", new=call):
                    self._assert_refused(await self._clear(), "vehicle_seat_unverified")
                call.assert_not_awaited()

    async def test_status_error_never_probes_or_clears(self) -> None:
        self.runtime.bridge_status_payload = AsyncMock(side_effect=RuntimeError("down"))
        call = AsyncMock(return_value=_CLEARED)
        with patch.object(self.runtime, "call_bridge", new=call):
            self._assert_refused(await self._clear(), "vehicle_seat_unverified")
        call.assert_not_awaited()

    async def test_telemetry_error_never_clears(self) -> None:
        call = AsyncMock(side_effect=ToolError("remote_error: client gone"))
        with patch.object(self.runtime, "call_bridge", new=call):
            with self.assertRaises(ToolError):
                await self._clear()
        call.assert_awaited_once_with("vehicle_telemetry", {}, "client", 2.0)

    async def test_seat_check_waits_for_the_tool_lock(self) -> None:
        # Sol #316 r1 R1: the status read and the telemetry run under the same
        # tool_lock as the clear, so nothing starts while another tool holds it.
        telemetry = {"ok": 1, "seated": 0}
        call = AsyncMock(side_effect=lambda cmd, *_args:
                         telemetry if cmd == "vehicle_telemetry" else _CLEARED)
        with patch.object(self.runtime, "call_bridge", new=call):
            await self.runtime.tool_lock.acquire()
            try:
                task = asyncio.create_task(self._clear())
                for _ in range(20):
                    await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.runtime.bridge_status_payload.assert_not_awaited()
                call.assert_not_awaited()
            finally:
                self.runtime.tool_lock.release()
            result = await task
        self.assertEqual(result, _CLEARED)
        self.runtime.bridge_status_payload.assert_awaited_once()
        self.assertEqual([item.args[0] for item in call.await_args_list], ["vehicle_telemetry", "scene_clear"])

    async def test_invalid_scene_id_never_reads_status(self) -> None:
        call = AsyncMock(return_value=_CLEARED)
        with patch.object(self.runtime, "call_bridge", new=call):
            with self.assertRaises(ToolError):
                await self._clear("bad scene")
        self.runtime.bridge_status_payload.assert_not_awaited()
        call.assert_not_awaited()

    async def test_live_profile_refuses_without_any_bridge_call(self) -> None:
        app, runtime = server.build_app(server.ServerConfig(
            key="test-key", port=0, live_profile=True, live_admin_enabled=True,
            log_sink=lambda _message: None,
        ))
        runtime.bridge_status_payload = AsyncMock(return_value={
            "client_peer": {"last_poll_age_s": 0.1},
        })
        with patch.object(runtime, "call_bridge", new=AsyncMock(return_value=_CLEARED)) as call:
            result = _content_json(await app.call_tool(
                "scene_clear", {"scene_id": _SCENE, "timeout_s": 2.0}
            ))
        self._assert_refused(result, "vehicle_seat_unverified")
        self.assertIn("live", result["hint"])
        call.assert_not_awaited()

    def test_description_states_the_seat_check(self) -> None:
        tools = {tool.name: tool for tool in self.app._tool_manager.list_tools()}
        clear = tools["scene_clear"].description
        for claim in ("vehicle_seat_unverified", "vehicle_client_seated", "deleted=0"):
            with self.subTest(claim=claim):
                self.assertIn(claim, clear)


if __name__ == "__main__":
    unittest.main()
