from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, server
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS
from tests._addon_paths import addon_root


MOD_SCRIPTS = addon_root() / "scripts"

CLIENT_BRIDGE = MOD_SCRIPTS / "5_Mission" / "MCPClientBridge.c"


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


class RestoreGameplayIngressContractTest(unittest.TestCase):
    def test_command_is_client_only_mutating_and_accepts_only_empty_args(self) -> None:
        self.assertIn("restore_gameplay", loopback.CLIENT_COMMANDS)
        self.assertNotIn("restore_gameplay", READ_ONLY_COMMANDS)
        self.assertEqual(loopback.peer_for_command("restore_gameplay"), "client")

        state = loopback.ServerState("test-key")
        from tests.fence_helpers import bind_both_peers

        bind_both_peers(state)
        status, body = state.enqueue_command("restore_gameplay", {})
        self.assertEqual((status, body["peer"]), (200, "client"))
        self.assertEqual(
            state.enqueue_command("restore_gameplay", {"extra": 1}),
            (400, {"error": "bad_args"}),
        )
        self.assertEqual(
            state.enqueue_command("restore_gameplay", {}, peer="server"),
            (400, {"error": "bad_peer"}),
        )


class RestoreGameplayFastMCPContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_public_tool_forwards_exact_empty_client_command(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name for tool in await app.list_tools()}
        self.assertIn("restore_gameplay", tools)

        with patch.object(
            runtime,
            "call_bridge",
            new=AsyncMock(return_value={"ok": 1}),
        ) as call:
            await app.call_tool("restore_gameplay", {"timeout_s": 1.0})

        call.assert_awaited_once_with("restore_gameplay", {}, "client", 1.0)


class RestoreGameplayEnforceSourceContractTest(unittest.TestCase):
    def test_dispatch_restores_gameplay_and_reports_success(self) -> None:
        source = CLIENT_BRIDGE.read_text(encoding="utf-8")
        dispatch = _method_body(source, "protected void Dispatch(MCPCommand command)")
        branch = 'else if (command.cmd == "restore_gameplay")'
        self.assertIn(branch, dispatch)
        branch_start = dispatch.index(branch)
        branch_end = dispatch.index("}", branch_start)
        branch_body = dispatch[branch_start:branch_end]
        self.assertIn("RestoreGameplay();", branch_body)
        self.assertIn("result.ok = true;", branch_body)

    def test_restore_gameplay_command_releases_the_camera_like_shutdown(self) -> None:
        # The command branch only called RestoreGameplay, which covers
        # simulation, controls and HUD and never the camera, so the tool answered
        # ok:1 with the view still locked to the debug camera and reconnecting was
        # the only way out. Shutdown already performed the full teardown, so the
        # fix was to share it rather than copy it.
        source = CLIENT_BRIDGE.read_text(encoding="utf-8")
        dispatch = _method_body(source, "protected void Dispatch(MCPCommand command)")
        branch_start = dispatch.index('else if (command.cmd == "restore_gameplay")')
        branch_end = dispatch.index("}", branch_start)
        self.assertIn("ReleaseCamera();", dispatch[branch_start:branch_end])

        release = _method_body(source, "protected void ReleaseCamera()")
        self.assertIn("m_ActiveCam.SetActive(false);", release)
        self.assertIn("DeleteOwnedCamera();", release)

        # One copy only. An inline teardown in Shutdown is exactly how the command
        # path came to be missing it, so a second copy must not reappear there.
        shutdown = _method_body(source, "void Shutdown()")
        self.assertIn("ReleaseCamera();", shutdown)
        self.assertNotIn("m_ActiveCam.SetActive(false);", shutdown)

    def test_switching_to_the_free_camera_drops_the_owned_one(self) -> None:
        # Found by tracing the invariant above to its other call sites: this method
        # overwrites m_ActiveCam and clears m_ActiveCamOwned, so an owned
        # staticcamera that was active would stay in the world with no reference
        # left able to delete it. The static path already drops the previous camera
        # before building its replacement.
        source = CLIENT_BRIDGE.read_text(encoding="utf-8")
        apply_free = _method_body(
            source, "protected bool ApplyFreeCamera(MCPJob job, MCPCameraValidation validation)"
        )
        self.assertIn("DeleteOwnedCamera();", apply_free)
        self.assertLess(
            apply_free.index("DeleteOwnedCamera();"), apply_free.index("m_ActiveCam = freeCam;")
        )

    def test_vehicle_get_in_restores_once_before_reading_vehicle_command(self) -> None:
        # Deliberately NOT extended with ReleaseCamera: this path restores
        # simulation as a precondition for reading the vehicle command, not as an
        # exit from camera control, and tearing the camera down here would break
        # filming a drive from a placed camera.
        source = CLIENT_BRIDGE.read_text(encoding="utf-8")
        prep = _method_body(source, "protected bool ProcessVehicleGetInClientPrep(MCPJob job)")
        guard = "if (!job.sim_restored)"
        self.assertIn(guard, prep)
        self.assertIn("RestoreGameplay();", prep)
        self.assertIn("job.sim_restored = true;", prep)
        self.assertLess(prep.index(guard), prep.index("player.GetCommand_Vehicle()"))


if __name__ == "__main__":
    unittest.main()
