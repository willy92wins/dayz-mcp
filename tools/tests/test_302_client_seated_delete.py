"""Issue #302: refuse deletion before a client-owned seat can dangle.

Exercise the public MCP tool with the game transport mocked; no live game,
launcher, registry or packaged module is involved.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import loopback, server
from tests.fence_helpers import bind_both_peers
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

    def _assert_unverified(self, result: dict) -> None:
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "vehicle_seat_unverified")
        self.assertEqual(result["object_id"], 7)
        self.assertEqual(result["deleted"], 0)

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
            {"ok": True, "seated": False},
            {"ok": True, "seated": True, "is_authority_owner": True},
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

    async def test_unreadable_telemetry_payload_never_sends_delete(self) -> None:
        for telemetry in (None, {}, {"ok": 0}, "unreadable", [], False, 0):
            with self.subTest(telemetry=telemetry):
                reply = {"ok": 1, "object_id": 7, "deleted": 1}
                call = AsyncMock(side_effect=lambda cmd, *_args:
                                 telemetry if cmd == "vehicle_telemetry" else reply)
                with patch.object(self.runtime, "call_bridge", new=call):
                    result = await self._delete()
                self._assert_unverified(result)
                call.assert_awaited_once_with("vehicle_telemetry", {}, "client", 2.0)

    async def test_non_boolean_seated_never_sends_delete(self) -> None:
        for seated in (None, "false", "true", "0", "1", "", [], {}, -1, 2, 0.0, 1.0):
            with self.subTest(seated=seated):
                telemetry = {"ok": 1, "seated": seated, "is_authority_owner": 1}
                call = AsyncMock(side_effect=lambda cmd, *_args:
                                 telemetry if cmd == "vehicle_telemetry" else {"ok": 1, "deleted": 1})
                with patch.object(self.runtime, "call_bridge", new=call):
                    self._assert_unverified(await self._delete())
                call.assert_awaited_once_with("vehicle_telemetry", {}, "client", 2.0)

    async def test_unprobeable_client_never_probes_or_deletes(self) -> None:
        for status in (None, {}, {"client_peer": {"last_poll_age_s": None}},
                       {"client_peer": {"last_poll_age_s": 15.0}},
                       {"client_peer": {"last_poll_age_s": 20.0}},
                       {"client_peer": {"last_poll_age_s": 0.1, "binding_state": "AMBIGUOUS"}}):
            with self.subTest(status=status):
                self.runtime.bridge_status_payload.return_value = status
                reply = {"ok": 1, "object_id": 7, "deleted": 1}
                with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value=reply)) as call:
                    self._assert_unverified(await self._delete())
                call.assert_not_awaited()

    async def test_status_error_never_probes_or_deletes(self) -> None:
        self.runtime.bridge_status_payload.side_effect = RuntimeError("status unreadable")
        reply = {"ok": 1, "object_id": 7, "deleted": 1}
        with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value=reply)) as call:
            self._assert_unverified(await self._delete())
        call.assert_not_awaited()

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


class LiveClientSeatedDeleteTest(unittest.IsolatedAsyncioTestCase):
    def _client_app(self, *, local_live: bool):
        config = server.ServerConfig(
            key="test-key", port=0, mode="client", live_profile=local_live,
            progressive_disclosure=False, log_sink=lambda _message: None,
        )
        # Keep real status/profile methods, without host registration or startup.
        runtime = object.__new__(server.ClientRuntime)
        runtime.config = config
        runtime.tool_lock = asyncio.Lock()
        with patch.object(server, "ClientRuntime", return_value=runtime):
            return server.build_app(config)

    async def _delete(self, app, object_id: int = 7) -> dict:
        return _content_json(await app.call_tool(
            "object_delete", {"object_id": object_id, "timeout_s": 2.0}
        ))

    def _assert_unverified(self, result: dict, object_id: int = 7) -> None:
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "vehicle_seat_unverified")
        self.assertEqual(result["object_id"], object_id)
        self.assertEqual(result["deleted"], 0)
        self.assertIn("live", result["hint"])

    async def test_live_admin_policy_is_preserved_without_forbidden_precheck(self) -> None:
        state = loopback.ServerState(
            "test-key", live_profile=True, live_admin_enabled=True
        )
        bind_both_peers(state)
        # Real policy controls, independent of the public guard and its mocks.
        self.assertEqual(state.enqueue_command(
            "object_delete", {"object_id": 7}, peer="server"
        )[0], 200)
        self.assertEqual(state.enqueue_command(
            "vehicle_telemetry", {}, peer="client"
        ), (403, {"error": "live_profile_refused"}))
        app, runtime = server.build_app(server.ServerConfig(
            key="test-key", port=0, live_profile=True, live_admin_enabled=True,
            log_sink=lambda _message: None,
        ))
        runtime.bridge_status_payload = AsyncMock(return_value={
            "client_peer": {"last_poll_age_s": 0.1},
        })

        async def bridge(cmd, args, peer, _timeout):
            status, body = state.enqueue_command(cmd, args, peer=peer)
            if status != 200:
                raise ToolError(f"remote_error: {body['error']}")
            return {"ok": 1, "deleted": 1}

        with patch.object(runtime, "call_bridge", new=AsyncMock(side_effect=bridge)) as call:
            for object_id in (7, 99):
                with self.subTest(object_id=object_id):
                    self._assert_unverified(await self._delete(app, object_id), object_id)
            call.assert_not_awaited()

    async def test_live_status_never_certifies_an_empty_client_seat(self) -> None:
        app, runtime = server.build_app(server.ServerConfig(
            key="test-key", port=0, live_profile=True, live_admin_enabled=True,
            log_sink=lambda _message: None,
        ))
        for status in (
            None, {}, {"client_peer": {"last_poll_age_s": None}},
            {"client_peer": {"last_poll_age_s": 20.0}},
            {"client_peer": {"last_poll_age_s": 0.1, "binding_state": "AMBIGUOUS"}},
        ):
            with self.subTest(status=status):
                runtime.bridge_status_payload = AsyncMock(return_value=status)
                with patch.object(runtime, "call_bridge", new=AsyncMock(return_value={"ok": True, "deleted": 1})) as call:
                    self._assert_unverified(await self._delete(app))
                    call.assert_not_awaited()
        runtime.bridge_status_payload = AsyncMock(side_effect=RuntimeError("status unreadable"))
        with patch.object(runtime, "call_bridge", new=AsyncMock(return_value={"ok": True, "deleted": 1})) as call:
            self._assert_unverified(await self._delete(app))
            call.assert_not_awaited()

    async def test_daemon_profile_overrides_local_config_and_stale_cache(self) -> None:
        app, runtime = self._client_app(local_live=False)
        runtime._learn_serving_live_profile({"live_profile": False, "daemon_generation": "old"})
        # Use the real ClientRuntime status parser; mock only transport.
        with patch.object(runtime, "_call", return_value=(200, {
            "live_profile": True, "daemon_generation": "new",
            "client_peer": {"last_poll_age_s": 0.1},
        })) as request, patch.object(runtime, "call_bridge", new=AsyncMock(return_value={"ok": True, "deleted": 1})) as call:
            self._assert_unverified(await self._delete(app))
            request.assert_called_once_with("GET", "/status")
            call.assert_not_awaited()

    async def test_unknown_daemon_profile_refuses_even_without_a_probeable_client(self) -> None:
        app, runtime = self._client_app(local_live=False)
        for payload in ({}, {"live_profile": "false"}, {
            "client_peer": {"last_poll_age_s": 0.1},
        }):
            with self.subTest(payload=payload):
                runtime._learn_serving_live_profile({"live_profile": False})
                with patch.object(runtime, "_call", return_value=(200, payload)), \
                        patch.object(runtime, "call_bridge", new=AsyncMock(return_value={"ok": True, "deleted": 1})) as call:
                    self._assert_unverified(await self._delete(app))
                    call.assert_not_awaited()

    async def test_failed_daemon_status_invalidates_cached_non_live_profile(self) -> None:
        app, runtime = self._client_app(local_live=False)
        for failure in (RuntimeError("status unreadable"), (503, {"error": "unavailable"})):
            with self.subTest(failure=failure):
                runtime._learn_serving_live_profile({"live_profile": False})
                response = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
                with patch.object(runtime, "_call", **response), \
                        patch.object(runtime, "call_bridge", new=AsyncMock(return_value={"ok": True, "deleted": 1})) as call:
                    self._assert_unverified(await self._delete(app))
                    self.assertIsNone(runtime._serving_live_profile)
                    call.assert_not_awaited()

    async def test_confirmed_non_live_daemon_preserves_on_foot_delete(self) -> None:
        app, runtime = self._client_app(local_live=True)
        reply = {"ok": 1, "object_id": 7, "deleted": 1}
        with patch.object(runtime, "_call", return_value=(200, {
            "live_profile": False, "client_peer": {"last_poll_age_s": 0.1},
        })), patch.object(runtime, "call_bridge", new=AsyncMock(side_effect=[
            {"ok": 1, "seated": 0}, reply,
        ])) as call:
            self.assertEqual(await self._delete(app), reply)
            self.assertEqual([item.args for item in call.await_args_list], [
                ("vehicle_telemetry", {}, "client", 2.0),
                ("object_delete", {"object_id": 7}, "server", 2.0),
            ])


if __name__ == "__main__":
    unittest.main()
