from __future__ import annotations

import re
import unittest

from dayz_mcp import loopback, server
from tests._addon_paths import addon_root
from tests.fence_helpers import bind_both_peers
from tests.test_bridge_client_capabilities import announced_caps, dispatch_census
from tests.test_bridge_server_capabilities import (
    DISPATCH_SIGNATURE,
    _method_body as _server_method_body,
    _walk_dispatch_chain,
    _without_comments,
)


RETIRED = ("vehicle_drive", "drive_probe_client")
KEEP_DRIVEPROBE_IDENTS = frozenset(
    {
        "CaptureDriveProbeClientOwnership",
        "ProcessDriveProbeClientPrep",
    }
)
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
DRIVEPROBE_RE = re.compile(r"DriveProbe|drive_probe")

MISSION_PATH = addon_root() / "scripts" / "5_Mission" / "MissionGameplay.c"
CLIENT_BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
SERVER_BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
DIALOG_PATH = addon_root() / "scripts" / "5_Mission" / "MCPDialogController.c"


def _method_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated method: {signature}")


class Fb3bb4EscAndDriveRetireTest(unittest.TestCase):
    def _read(self, path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except Exception as exc:
            self.fail(str(exc))

    def _body(self, source: str, signature: str) -> str:
        try:
            return _method_body(source, signature)
        except Exception as exc:
            self.fail(str(exc))

    def test_3bb4_mission_onkeypress_calls_super_before_bridge(self) -> None:
        source = self._read(MISSION_PATH)
        body = self._body(source, "override void OnKeyPress(int key)")
        super_at = body.find("super.OnKeyPress(key)")
        hand_at = body.find("OnMissionKeyPress(key)")
        self.assertGreaterEqual(super_at, 0)
        self.assertGreaterEqual(hand_at, 0)
        self.assertLess(super_at, hand_at)

    def test_3bb4_bridge_onmissionkeypress_acts_only_when_dialog_open(self) -> None:
        source = self._read(CLIENT_BRIDGE_PATH)
        body = self._body(source, "void OnMissionKeyPress(int key)")
        dialog_at = body.find("m_Dialog")
        open_at = body.find("IsOpen()")
        log_at = body.find("Log(")
        cancel_at = body.find("TryCancelFromKey(key, m_JobRunner.GetElapsedS())")
        self.assertGreaterEqual(dialog_at, 0)
        self.assertGreaterEqual(open_at, 0)
        self.assertGreaterEqual(log_at, 0)
        self.assertGreaterEqual(cancel_at, 0)
        self.assertLess(dialog_at, cancel_at)
        self.assertLess(open_at, cancel_at)
        self.assertIn("key", body[log_at:cancel_at])

    def test_3bb4_dialog_cancels_only_escape_when_cancel_available(self) -> None:
        source = self._read(DIALOG_PATH)
        body = self._body(source, "void TryCancelFromKey(int key, float now)")
        finish = 'TryFinish("cancelled", "", "", now)'
        escape_at = body.find("KeyCode.KC_ESCAPE")
        finish_at = body.find(finish)
        self.assertGreaterEqual(escape_at, 0)
        self.assertGreaterEqual(finish_at, 0)
        self.assertLess(escape_at, finish_at)
        self.assertIn("m_BtnCancel", body)
        self.assertIn('"confirm"', body)
        self.assertIn('"form"', body)
        self.assertEqual(body.count(finish), 1)

    def test_3bb4_retired_commands_absent_from_caps_dispatch_whitelist_and_census(self) -> None:
        try:
            client_source = CLIENT_BRIDGE_PATH.read_text(encoding="utf-8")
            server_source = SERVER_BRIDGE_PATH.read_text(encoding="utf-8")
            client_caps = announced_caps(client_source)
            client_dispatch = dispatch_census(client_source)
            server_dispatch = _walk_dispatch_chain(
                _server_method_body(_without_comments(server_source), DISPATCH_SIGNATURE)
            )
            server_caps_match = re.search(
                r'protected const string SERVER_CAPABILITIES = ((?:"[^"\n]*"(?: \+ )?)+);',
                server_source,
            )
            if not server_caps_match:
                raise AssertionError("SERVER_CAPABILITIES declaration not found")
            server_caps = "".join(re.findall(r'"([^"\n]*)"', server_caps_match.group(1)))
            server_cap_names = server_caps.split(",") if server_caps else []
        except Exception as exc:
            self.fail(str(exc))

        for command in RETIRED:
            self.assertNotIn(command, client_caps)
            self.assertNotIn(command, client_dispatch)
            self.assertNotIn(command, server_dispatch)
            self.assertNotIn(command, server_cap_names)
            self.assertNotIn(command, loopback.SERVER_COMMANDS)
            self.assertNotIn(command, loopback.CLIENT_COMMANDS)
            self.assertNotIn(command, loopback.WHITELISTED_COMMANDS)
            self.assertNotIn(command, loopback._COMMAND_ARG_SCHEMAS)

        self.assertNotIn("vehicle_drive", server._BRIDGE_COMMAND_TOOLS["server"])
        self.assertNotIn("drive_probe_client", server._BRIDGE_COMMAND_TOOLS["client"])

        self.assertIn("vehicle_enter", server_cap_names)
        self.assertIn("vehicle_enter", server_dispatch)
        self.assertIn("vehicle_enter", loopback.SERVER_COMMANDS)
        self.assertIn("vehicle_enter", server._BRIDGE_COMMAND_TOOLS["server"])
        self.assertIn("engine_set", client_caps)
        self.assertIn("engine_set", client_dispatch)
        self.assertIn("engine_set", loopback.CLIENT_COMMANDS)
        self.assertIn("engine_set", server._BRIDGE_COMMAND_TOOLS["client"])
        self.assertIn("key_press", client_caps)
        self.assertIn("key_press", client_dispatch)
        self.assertIn("key_press", loopback.CLIENT_COMMANDS)
        self.assertIn("key_press", server._BRIDGE_COMMAND_TOOLS["client"])
        self.assertIn("ui_dialog", client_caps)
        self.assertIn("ui_dialog", client_dispatch)
        self.assertIn("ui_dialog", loopback.CLIENT_COMMANDS)
        self.assertIn("ui_dialog", server._BRIDGE_COMMAND_TOOLS["client"])

    def test_3bb4_loopback_refuses_to_enqueue_retired_commands(self) -> None:
        state = loopback.ServerState("test-3bb4")
        try:
            bind_both_peers(state)
        except Exception as exc:
            self.fail(str(exc))

        for command in RETIRED:
            try:
                status, body = state.enqueue_command(command, {"throttle": 1.0})
            except Exception as exc:
                self.fail(str(exc))
            self.assertEqual(status, 400)
            self.assertEqual(body, {"error": "not_whitelisted"})

        try:
            # Payloads the tools actually send: since 6ce4 these verbs have a
            # schema, and an empty payload is bad_args.
            enter_status, _enter = state.enqueue_command(
                "vehicle_enter", {"pos": [0.0, 0.0, 0.0]}
            )
            engine_status, _engine = state.enqueue_command(
                "engine_set", {"mode": "start"}
            )
            key_status, _key = state.enqueue_command("key_press", {"dik": 1})
            dialog_status, _dialog = state.enqueue_command(
                "ui_dialog",
                {"kind": "acknowledge", "title": "Notice", "message": "Ready"},
            )
        except Exception as exc:
            self.fail(str(exc))
        self.assertEqual(enter_status, 200)
        self.assertEqual(engine_status, 200)
        self.assertEqual(key_status, 200)
        self.assertEqual(dialog_status, 200)

    def test_3bb4_old_addon_announcing_retired_commands_is_unmapped(self) -> None:
        try:
            server_tools = frozenset(
                tool for tool in server._BRIDGE_COMMAND_TOOLS["server"].values() if tool
            )
            client_tools = frozenset(
                tool for tool in server._BRIDGE_COMMAND_TOOLS["client"].values() if tool
            )
            server_result = server._compare_bridge_capabilities(
                "server",
                {
                    "state": "announced",
                    "reason": "ok",
                    "announced_commands": ["vehicle_enter", "vehicle_drive"],
                },
                server_tools,
            )
            client_result = server._compare_bridge_capabilities(
                "client",
                {
                    "state": "announced",
                    "reason": "ok",
                    "announced_commands": ["key_press", "drive_probe_client"],
                },
                client_tools,
            )
        except Exception as exc:
            self.fail(str(exc))
        self.assertIn("vehicle_drive", server_result["unmapped_announced_commands"])
        self.assertIn("drive_probe_client", client_result["unmapped_announced_commands"])
        self.assertNotIn("vehicle_enter", server_result["unmapped_announced_commands"])
        self.assertNotIn("key_press", client_result["unmapped_announced_commands"])

    def test_3bb4_addon_driveprobe_identifiers_only_those_other_verbs_need(self) -> None:
        found: set[str] = set()
        try:
            addon = addon_root()
            for path in addon.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in {".c", ".h"}:
                    continue
                text = path.read_text(encoding="utf-8")
                for token in TOKEN_RE.findall(text):
                    if DRIVEPROBE_RE.search(token):
                        found.add(token)
        except Exception as exc:
            self.fail(str(exc))
        self.assertEqual(found, KEEP_DRIVEPROBE_IDENTS)


if __name__ == "__main__":
    unittest.main()
