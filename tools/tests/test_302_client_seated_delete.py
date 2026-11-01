"""Issue #302: refuse deletion before a client-owned seat can dangle.

Exercise the public MCP tool with the game transport mocked; no live game,
launcher, registry or packaged module is involved.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import server
from tests.mcp_helpers import _content_json


class ClientSeatedDeleteTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        self.runtime.bridge_status_payload = AsyncMock(
            return_value={"client_peer": {"last_poll_age_s": 0.1}}
        )

    async def _delete(self, object_id: int = 7) -> dict:
        return _content_json(await self.app.call_tool(
            "object_delete", {"object_id": object_id, "timeout_s": 2.0}
        ))

    async def test_client_owned_seat_refuses_every_id_without_server_delete(self) -> None:
        # No registry id in telemetry: even an apparently unrelated target
        # cannot safely be excluded from the vehicle's deletion hierarchy.
        for object_id in (7, 99):
            for authority in (0, False, None):
                with self.subTest(object_id=object_id, authority=authority):
                    telemetry = {
                        "ok": 1, "seated": 1, "is_owner": 1,
                        "is_authority_owner": authority,
                        "type": "CivilianSedan", "net_id_low": 42, "net_id_high": 1,
                    }
                    call = AsyncMock(side_effect=lambda cmd, *_args:
                                     telemetry if cmd == "vehicle_telemetry" else {"ok": 1, "deleted": 1})
                    with patch.object(self.runtime, "call_bridge", new=call):
                        result = await self._delete(object_id)
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["error"], "vehicle_client_seated")
                    self.assertEqual(result["deleted"], 0)
                    self.assertEqual(result["object_id"], object_id)
                    self.assertIn("exit", result["hint"])
                    self.assertIn("vehicle_enter", result["hint"])
                    call.assert_awaited_once_with("vehicle_telemetry", {}, "client", 2.0)

    async def test_on_foot_or_authority_owned_seat_preserves_delete_reply(self) -> None:
        for telemetry in (
            {"ok": 1, "seated": 0, "is_authority_owner": 0},
            {"ok": 1, "seated": 1, "is_owner": 0, "is_authority_owner": 1},
        ):
            for deleted in (0, 1):
                with self.subTest(telemetry=telemetry, deleted=deleted):
                    reply = {"ok": 1, "object_id": 7, "deleted": deleted}
                    call = AsyncMock(side_effect=lambda cmd, *_args:
                                     telemetry if cmd == "vehicle_telemetry" else reply)
                    with patch.object(self.runtime, "call_bridge", new=call):
                        result = await self._delete()
                    self.assertEqual(result, reply)
                    self.assertEqual([item.args for item in call.await_args_list], [
                        ("vehicle_telemetry", {}, "client", 2.0),
                        ("object_delete", {"object_id": 7}, "server", 2.0),
                    ])

    async def test_unreadable_telemetry_payload_matches_teleport_policy(self) -> None:
        for telemetry in (None, {}, {"ok": 0}, "unreadable"):
            with self.subTest(telemetry=telemetry):
                reply = {"ok": 1, "object_id": 7, "deleted": 1}
                call = AsyncMock(side_effect=lambda cmd, *_args:
                                 telemetry if cmd == "vehicle_telemetry" else reply)
                with patch.object(self.runtime, "call_bridge", new=call):
                    result = await self._delete()
                self.assertEqual(result, reply)
                self.assertEqual(call.await_count, 2)
                self.assertEqual(call.await_args.args,
                                 ("object_delete", {"object_id": 7}, "server", 2.0))

    async def test_unprobeable_client_skips_telemetry_like_teleport(self) -> None:
        for status in ({}, {"client_peer": {"last_poll_age_s": None}},
                       {"client_peer": {"last_poll_age_s": 20.0}}):
            with self.subTest(status=status):
                self.runtime.bridge_status_payload.return_value = status
                reply = {"ok": 1, "object_id": 7, "deleted": 1}
                with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value=reply)) as call:
                    self.assertEqual(await self._delete(), reply)
                call.assert_awaited_once_with("object_delete", {"object_id": 7}, "server", 2.0)

    async def test_status_error_skips_telemetry_like_teleport(self) -> None:
        self.runtime.bridge_status_payload.side_effect = RuntimeError("status unreadable")
        reply = {"ok": 1, "object_id": 7, "deleted": 1}
        with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value=reply)) as call:
            self.assertEqual(await self._delete(), reply)
        call.assert_awaited_once_with("object_delete", {"object_id": 7}, "server", 2.0)

    async def test_telemetry_transport_error_never_sends_delete(self) -> None:
        with patch.object(self.runtime, "call_bridge",
                          new=AsyncMock(side_effect=ToolError("client_timeout"))) as call:
            with self.assertRaisesRegex(ToolError, "client_timeout"):
                await self._delete()
        call.assert_awaited_once_with("vehicle_telemetry", {}, "client", 2.0)

    async def test_invalid_id_never_probes_or_deletes(self) -> None:
        with patch.object(self.runtime, "call_bridge", new=AsyncMock()) as call:
            with self.assertRaises(ToolError):
                await self._delete(0)
        self.runtime.bridge_status_payload.assert_not_awaited()
        call.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
