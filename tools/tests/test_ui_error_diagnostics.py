"""fb-20260829-221423-b2c4: a bridge ok:0 keeps the diagnostics it filled before the error.

The bridge sets user_id, handler and clicked before it decides not_handled
(MCPClientBridge.c:1465-1480) and echoes the request in ui_request; the Python
layer used to raise ToolError("not_handled") and drop all of it. The message is
the only thing that crosses the MCP wire, so the fields now ride in it after the
code -- for the core UI verbs only. MCPResult is one flat class, so every wire
result carries handler="", user_id=0, clicked=false: the decision is by verb,
and a world_spawn timeout keeps the bare code the rest of the suite pins.
"""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import PropertyMock, patch

from mcp.shared.memory import create_connected_server_and_client_session

from dayz_mcp import server
from dayz_mcp.server import ServerConfig, build_app

NOT_HANDLED: dict[str, Any] = {
    "ok": 0,
    "error": "not_handled",
    "handler": "LFPG_SorterView_TEST",
    "user_id": 506,
    "clicked": False,
    "ui_request": {
        "requested_path": "BtnCloseX",
        "requested_root": "",
        "requested_text": "",
        "matched_path": "LFPG_Sorter/BtnCloseX",
    },
}

# The shape every result has on the wire: the flat MCPResult scalars ride along
# unassigned (MCPMessages.c:423-479) and result_prune leaves falsy scalars alone.
FLAT_TIMEOUT: dict[str, Any] = {
    "ok": 0,
    "error": "timeout",
    "object_id": 7,
    "handler": "",
    "user_id": 0,
    "clicked": False,
    "ui_request": {},
}

# A non-UI result that LOOKS like it has diagnostics. The bridge never produces
# it, but the test has to show that the verb decides, not the payload: without
# the verb gate the allowed echo below would leak into a world_spawn error.
LOADED_TIMEOUT: dict[str, Any] = {
    "ok": 0,
    "error": "timeout",
    "object_id": 7,
    "handler": "SomeHandler",
    "user_id": 99,
    "clicked": True,
    "ui_request": {"requested_path": "A", "matched_path": "Root/A"},
}


class BridgeErrorDiagnosticsTest(unittest.TestCase):
    def test_ui_click_fields_ride_in_the_message_after_the_code(self) -> None:
        message = str(server._bridge_error(dict(NOT_HANDLED), "ui_click"))
        self.assertEqual(
            message,
            "not_handled; handler='LFPG_SorterView_TEST' user_id=506 clicked=False; "
            "requested_path='BtnCloseX' matched_path='LFPG_Sorter/BtnCloseX'",
        )

    def test_empty_handler_is_reported_as_no_handler_ran(self) -> None:
        message = str(
            server._bridge_error(
                {"ok": 0, "error": "not_handled", "handler": "", "user_id": 0, "clicked": False},
                "ui_click",
            )
        )
        self.assertIn("handler=''", message)

    def test_flat_scalars_do_not_enrich_non_ui_verbs(self) -> None:
        # Codex C B-01 repro: the flat defaults must not turn "timeout" into
        # "timeout; handler='' user_id=0 clicked=False".
        error = server._bridge_error(dict(FLAT_TIMEOUT), "world_spawn")
        self.assertEqual(str(error), "timeout")
        self.assertEqual(error.object_id, 7)
        self.assertEqual(str(server._bridge_error(dict(FLAT_TIMEOUT), None)), "timeout")
        self.assertEqual(str(server._bridge_error({"ok": 0, "error": "bad_pos"}, "player_teleport")), "bad_pos")
        self.assertEqual(str(server._bridge_error({"ok": 0}, "object_inspect")), "bridge_error")

    def test_verb_gate_decides_not_the_payload(self) -> None:
        # Codex C R2 B-02: a non-UI verb keeps the bare code even when the
        # payload carries a non-empty allowed echo and non-empty click scalars.
        # Dropping the verb gate makes this observable (the echo would leak).
        for cmd in ("world_spawn", "player_teleport", "key_press", None):
            with self.subTest(cmd):
                error = server._bridge_error(dict(LOADED_TIMEOUT), cmd)
                self.assertEqual(str(error), "timeout")
                self.assertEqual(error.object_id, 7)

    def test_other_ui_verbs_get_the_echo_but_not_the_click_scalars(self) -> None:
        result = {
            "ok": 0,
            "error": "text_not_writable",
            "handler": "",
            "user_id": 0,
            "clicked": False,
            "ui_request": {
                "requested_path": "Cmd",
                "requested_root": "",
                "requested_text": "dump secret-token",
                "matched_path": "",
            },
        }
        message = str(server._bridge_error(result, "ui_set_text"))
        self.assertEqual(message, "text_not_writable; requested_path='Cmd'")
        # requested_text replays caller input: it never enters an error message.
        self.assertNotIn("secret-token", str(server._bridge_error(result, "ui_click")))

    def test_echo_is_an_allowlist_not_an_iteration(self) -> None:
        # A key the bridge may grow tomorrow must not be published by this
        # message until someone decides it belongs there.
        result = {
            "ok": 0,
            "error": "not_handled",
            "handler": "H",
            "user_id": 1,
            "clicked": False,
            "ui_request": {"requested_path": "A", "future_key": "x", "requested_text": "t"},
        }
        message = str(server._bridge_error(result, "ui_click"))
        self.assertEqual(message, "not_handled; handler='H' user_id=1 clicked=False; requested_path='A'")

    def test_empty_echo_keeps_the_bare_code_even_for_ui_verbs(self) -> None:
        self.assertEqual(
            str(server._bridge_error({"ok": 0, "error": "widget_not_found", "ui_request": {}}, "ui_focus")),
            "widget_not_found",
        )
        self.assertEqual(
            str(server._bridge_error({"ok": 0, "error": "widget_not_found", "ui_request": None}, "ui_tree")),
            "widget_not_found",
        )


def _fake_state(result: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        enqueue_command=lambda *args, **kwargs: (200, {"id": 41}),
        take_result=lambda command_id, remove=False: dict(result),
        abandon_command=lambda *args, **kwargs: None,
    )


async def _wire_error_text(tool: str, arguments: dict[str, Any], result: dict[str, Any]) -> str:
    """Error text a real MCP client receives, walking call_bridge -> wait_for_result -> _bridge_error."""
    app, runtime = build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))
    with patch.object(runtime, "ensure_peer_allowed", return_value=None), patch.object(
        type(runtime), "state", new_callable=PropertyMock, return_value=_fake_state(result)
    ):
        async with create_connected_server_and_client_session(app._mcp_server) as session:
            call = await session.call_tool(tool, arguments)
    assert call.isError, call
    # S3 adds process freshness as a separate block, not to the domain error.
    # Check that sidecar independently and retain the exact diagnostic assertion.
    for block in call.content[1:]:
        marker = call.meta["server_code_freshness"]
        assert block.text.startswith(f"SERVER_CODE_FRESHNESS {marker['status']}: "), call
        assert "remediation=reopen_mcp_client" in block.text, call
    return call.content[0].text


class UiClickWireTest(unittest.IsolatedAsyncioTestCase):
    async def test_not_handled_reaches_the_client_with_its_diagnostics(self) -> None:
        text = await _wire_error_text("ui_click", {"path": "BtnCloseX"}, NOT_HANDLED)
        self.assertEqual(
            text,
            "Error executing tool ui_click: not_handled; handler='LFPG_SorterView_TEST' "
            "user_id=506 clicked=False; requested_path='BtnCloseX' matched_path='LFPG_Sorter/BtnCloseX'",
        )

    async def test_world_spawn_timeout_with_flat_scalars_stays_bare(self) -> None:
        text = await _wire_error_text(
            "world_spawn", {"type": "SurvivorM_Mirek", "pos": [7500.0, 0.0, 7500.0]}, FLAT_TIMEOUT
        )
        self.assertEqual(text, "Error executing tool world_spawn: timeout")

    async def test_world_spawn_with_a_loaded_payload_stays_bare(self) -> None:
        text = await _wire_error_text(
            "world_spawn", {"type": "SurvivorM_Mirek", "pos": [7500.0, 0.0, 7500.0]}, LOADED_TIMEOUT
        )
        self.assertEqual(text, "Error executing tool world_spawn: timeout")


if __name__ == "__main__":
    unittest.main()
