"""notify_players refuses title/detail/icon over the ScriptRPC string bound."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from dayz_mcp import loopback, server
from dayz_mcp.server import ServerConfig, build_app
from tests.fence_helpers import bind_both_peers
from tests.mcp_helpers import _content_json


FIELDS = ("title", "detail", "icon")
ASCII_AT = "a" * 1023
ASCII_OVER = "a" * 1024
TWO_BYTE_AT = "ñ" * 511  # 1022 UTF-8 bytes
TWO_BYTE_OVER = "ñ" * 512  # 1024 UTF-8 bytes
MIXED_AT = "ñ" * 511 + "a"  # 1023 UTF-8 bytes, 512 characters
MIXED_OVER = "ñ" * 511 + "aa"  # 1024 UTF-8 bytes, 513 characters


def _args(field: str, value: str) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "show_time": 5.0,
        "title": "Notice",
        "detail": "",
        "icon": "",
        "timeout_s": 1.0,
    }
    arguments[field] = value
    return arguments


class NotifyPlayersStringLimitToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(log_sink=lambda _message: None)
        )
        self.bridge_calls: list[str] = []

    async def _fake_bridge(
        self, cmd: str, _args: dict[str, Any], _peer: str, _timeout_s: float
    ) -> dict[str, Any]:
        self.bridge_calls.append(cmd)
        if cmd == "query_all_players":
            return {"players": [{"uid": "player-1"}]}
        return {"ok": True, "sent": 1}

    async def _call(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with patch.object(self.runtime, "call_bridge", self._fake_bridge):
            return _content_json(await self.app.call_tool("notify_players", arguments))

    async def _refusal(self, arguments: dict[str, Any]) -> str:
        with self.assertRaises(server.ToolError) as raised:
            with patch.object(self.runtime, "call_bridge", self._fake_bridge):
                await self.app.call_tool("notify_players", arguments)
        message = str(raised.exception)
        wrapper = "Error executing tool notify_players: "
        self.assertTrue(message.startswith(wrapper), message)
        return message[len(wrapper):]

    def test_constant_is_the_measured_bound(self) -> None:
        self.assertEqual(loopback.NOTIFY_STRING_MAX_BYTES, 1023)

    async def test_ascii_boundary_per_field(self) -> None:
        for field in FIELDS:
            with self.subTest(field=field, size=1023):
                self.bridge_calls = []
                result = await self._call(_args(field, ASCII_AT))
                self.assertTrue(result["ok"])
                self.assertEqual(
                    self.bridge_calls, ["query_all_players", "notify_players"]
                )
            with self.subTest(field=field, size=1024):
                self.bridge_calls = []
                message = await self._refusal(_args(field, ASCII_OVER))
                self._assert_limit_refusal(message, field, ASCII_OVER)
                self.assertEqual(self.bridge_calls, [])

    async def test_utf8_byte_count_not_char_count_per_field(self) -> None:
        cases = (
            (TWO_BYTE_AT, True),
            (TWO_BYTE_OVER, False),
            (MIXED_AT, True),
            (MIXED_OVER, False),
        )
        for field in FIELDS:
            for value, passes in cases:
                with self.subTest(field=field, chars=len(value), passes=passes):
                    self.bridge_calls = []
                    if passes:
                        result = await self._call(_args(field, value))
                        self.assertTrue(result["ok"])
                        self.assertEqual(
                            self.bridge_calls,
                            ["query_all_players", "notify_players"],
                        )
                    else:
                        message = await self._refusal(_args(field, value))
                        self._assert_limit_refusal(message, field, value)
                        self.assertEqual(self.bridge_calls, [])

    def _assert_limit_refusal(self, message: str, field: str, value: str) -> None:
        self.assertTrue(message.startswith(f"bad_args: {field}"), message)
        self.assertIn("1024", message)
        self.assertIn("1023", message)
        self.assertIn("bytes", message)
        self.assertIn("not characters", message)
        self.assertIn("one-string", message)
        self.assertIn("combined size", message)
        self.assertIn("was not measured", message)
        self.assertNotIn(value, message)

    async def test_all_fields_at_bound_together_pass(self) -> None:
        # Per-field check only: the bound comes from a one-string probe and
        # the combined size of the three strings in one RPC was not
        # measured, so three fields each at 1023 bytes are not refused here.
        arguments = _args("title", ASCII_AT)
        arguments["detail"] = ASCII_AT
        arguments["icon"] = ASCII_AT
        result = await self._call(arguments)
        self.assertTrue(result["ok"])
        self.assertEqual(self.bridge_calls, ["query_all_players", "notify_players"])

    async def test_existing_empty_title_and_type_refusals_still_hold(self) -> None:
        self.bridge_calls = []
        message = await self._refusal(_args("title", ""))
        self.assertTrue(message.startswith("bad_args: title"), message)
        self.assertEqual(self.bridge_calls, [])

        self.bridge_calls = []
        arguments = _args("detail", "")
        arguments["detail"] = 3
        with self.assertRaises(Exception):
            with patch.object(self.runtime, "call_bridge", self._fake_bridge):
                await self.app.call_tool("notify_players", arguments)
        self.assertEqual(self.bridge_calls, [])


class NotifyPlayersStringLimitLoopbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        bind_both_peers(self.state)

    def _enqueue(self, field: str, value: object) -> tuple[int, dict]:
        arguments: dict[str, Any] = {
            "show_time": 5.0,
            "title": "Notice",
            "detail": "",
            "icon": "",
        }
        arguments[field] = value
        return self.state.enqueue_command("notify_players", arguments)

    def test_ascii_and_utf8_boundaries_per_field(self) -> None:
        cases = (
            (ASCII_AT, 200),
            (ASCII_OVER, 400),
            (TWO_BYTE_AT, 200),
            (TWO_BYTE_OVER, 400),
            (MIXED_AT, 200),
            (MIXED_OVER, 400),
        )
        for field in FIELDS:
            for value, expected_status in cases:
                with self.subTest(
                    field=field, chars=len(value), status=expected_status
                ):
                    status, body = self._enqueue(field, value)
                    self.assertEqual(status, expected_status)
                    if expected_status == 400:
                        self.assertEqual(body, {"error": "bad_args"})
                    else:
                        self.assertEqual(body["peer"], "server")

    def test_all_fields_at_bound_together_pass(self) -> None:
        # Same per-field choice as the tool layer: the combined size of
        # the three strings in one RPC was not measured (see the caveat on
        # loopback.NOTIFY_STRING_MAX_BYTES).
        status, body = self.state.enqueue_command(
            "notify_players",
            {
                "show_time": 5.0,
                "title": ASCII_AT,
                "detail": ASCII_AT,
                "icon": ASCII_AT,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["peer"], "server")

    def test_existing_empty_title_and_type_refusals_still_hold(self) -> None:
        status, body = self._enqueue("title", "")
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "bad_args"})

        status, body = self._enqueue("detail", 3)
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "bad_args"})


if __name__ == "__main__":
    unittest.main()
