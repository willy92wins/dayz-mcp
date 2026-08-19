"""ui_reload_layout: reload a .layout into the client without repacking the mod.

The happy path is cheap to get right and cheap to re-measure in game. What these
gates hold is the pair of orderings inside the Enforce handler: CreateWidgets on
a missing layout dies inside the native call and takes the client with it, so the
FileExist guard has to come first; and a second CreateWidgets stacks another root
on top of the previous one, so the Unlink has to come first too. Both are
invisible to a compiler and to any test that only calls the verb with good args.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, session_coordination
from dayz_mcp.server import LEASE_TOOL_LINE, ServerConfig, build_app
from tests._addon_paths import addon_root


def _client_bridge_source() -> str:
    path = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
    return path.read_text(encoding="utf-8")


def _handler_source(text: str) -> str:
    needle = "protected bool DispatchUiReloadLayout("
    start = text.find(needle)
    if start < 0:
        return ""
    end = text.find("\n\tprotected ", start + len(needle))
    if end < 0:
        end = len(text)
    return text[start:end]


class UiReloadLayoutRoutingTest(unittest.TestCase):
    def test_is_a_client_command(self) -> None:
        self.assertIn("ui_reload_layout", loopback.CLIENT_COMMANDS)
        self.assertIn("ui_reload_layout", loopback.WHITELISTED_COMMANDS)
        self.assertEqual(loopback.peer_for_command("ui_reload_layout"), "client")

    def test_requires_a_lease(self) -> None:
        # It rebuilds the client widget tree. ui_tree is the read-only UI verb.
        self.assertNotIn("ui_reload_layout", session_coordination.READ_ONLY_COMMANDS)
        self.assertTrue(
            session_coordination.command_requires_lease("ui_reload_layout")
        )


class UiReloadLayoutArgsTest(unittest.TestCase):
    def _check(self, args: dict) -> tuple[bool, str | None]:
        return loopback.validate_command_args("ui_reload_layout", args)

    def test_reload_with_a_path_is_accepted(self) -> None:
        for args in (
            {"path": "$profile:mcp_hot.layout"},
            {"mode": "reload", "path": "$profile:mcp_hot.layout"},
            {"mode": "reload", "path": "DayZ_MCP/gui/layouts/mcp_dialog.layout"},
            {"mode": "reload", "path": "$profile:mcp_hot.layout", "limit": 512},
        ):
            ok, err = self._check(args)
            self.assertTrue(ok, args)
            self.assertIsNone(err, args)

    def test_close_carries_no_path(self) -> None:
        ok, err = self._check({"mode": "close"})
        self.assertTrue(ok)
        self.assertIsNone(err)
        ok_bad, err_bad = self._check({"mode": "close", "path": "$profile:x.layout"})
        self.assertFalse(ok_bad)
        self.assertEqual(err_bad, "bad_args")

    def test_rejects_bad_args(self) -> None:
        for args in (
            {},
            {"mode": "reload"},
            {"path": ""},
            {"path": 5},
            {"mode": "unlink", "path": "a.layout"},
            {"mode": 1, "path": "a.layout"},
            {"path": "a.layout", "limit": 0},
            {"path": "a.layout", "limit": 513},
            {"path": "a.layout", "limit": True},
            {"path": "a.layout", "widget": "Root"},
        ):
            ok, err = self._check(args)
            self.assertFalse(ok, args)
            self.assertEqual(err, "bad_args", args)


class UiReloadLayoutToolTest(unittest.TestCase):
    def test_tool_is_exposed_and_declares_the_lease(self) -> None:
        app, _runtime = build_app(
            ServerConfig(key="k", port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name: tool for tool in app._tool_manager.list_tools()}
        self.assertIn("ui_reload_layout", tools)
        description = tools["ui_reload_layout"].description or ""
        self.assertTrue(description.startswith(LEASE_TOOL_LINE), description[:80])
        # The prefix rule is the whole point of the verb: an addon path is served
        # by the PBO, so a caller pointing there sees a stale layout, not an edit.
        self.assertIn("$profile:", description)


class UiReloadLayoutEnforceTest(unittest.TestCase):
    def test_command_is_dispatched(self) -> None:
        text = _client_bridge_source()
        self.assertIn('command.cmd == "ui_reload_layout"', text)
        self.assertIn("postNow = DispatchUiReloadLayout(command, result);", text)

    def test_file_exist_guards_create_widgets(self) -> None:
        body = _handler_source(_client_bridge_source())
        self.assertNotEqual(body, "", "DispatchUiReloadLayout not found")
        guard = body.find("FileExist(args.path)")
        create = body.find("CreateWidgets(args.path)")
        self.assertGreater(guard, 0, "the FileExist guard is gone")
        self.assertGreater(
            create, guard, "CreateWidgets must never run before the FileExist guard"
        )

    def test_previous_root_is_unlinked_before_the_new_one_loads(self) -> None:
        body = _handler_source(_client_bridge_source())
        unlink = body.find("m_UiPreviewRoot.Unlink();")
        create = body.find("CreateWidgets(args.path)")
        self.assertGreater(unlink, 0, "the preview root is never unlinked")
        self.assertGreater(
            create, unlink, "a second CreateWidgets would stack roots on screen"
        )

    def test_shutdown_unlinks_the_preview(self) -> None:
        # The workspace survives a mission change; without this the next mission
        # starts with a stale tree drawn on top of it.
        text = _client_bridge_source()
        shutdown = text.find("\tvoid Shutdown()")
        self.assertGreater(shutdown, 0, "Shutdown() not found")
        self.assertIn("m_UiPreviewRoot.Unlink();", text[shutdown:])


if __name__ == "__main__":
    unittest.main()
