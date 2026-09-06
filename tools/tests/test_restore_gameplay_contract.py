from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, server
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS
from tests._addon_paths import addon_root


MOD_SCRIPTS = addon_root() / "scripts"

CLIENT_BRIDGE = MOD_SCRIPTS / "5_Mission" / "MCPClientBridge.c"


def _content_json(content: Any) -> dict[str, Any]:
    if isinstance(content, tuple):
        _blocks, structured = content
        if isinstance(structured, dict):
            return structured
        content = _blocks
    parsed = json.loads(content[0].text)
    if not isinstance(parsed, dict):
        raise AssertionError(f"expected dict content, got {parsed!r}")
    return parsed


def _camera_probe(**camera: object) -> dict[str, Any]:
    """A camera_get result as it comes off the wire.

    Enforce serializes bools as 0/1 (tests/test_mcp_tools.py:80) and
    BuildCameraResult (MCPClientBridge.c:3645-3684) has exactly three exits:
    not in game (camera.ok=false), no scripted camera (viewport_moved=false
    plus error="player_camera_active") and a scripted camera mounted
    (viewport_moved=true). The middle one is the discriminator measured
    in-game on 2026-08-16 for BUG-075.
    """
    block: dict[str, Any] = {
        "ok": 1,
        "applied_mode": "get",
        "viewport_moved": 0,
        "error": "",
    }
    block.update(camera)
    return {"id": 8, "ok": 1, "error": "", "camera": block}


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
            new=AsyncMock(
                side_effect=[
                    {"id": 7, "ok": 1, "error": ""},
                    _camera_probe(error="player_camera_active"),
                ]
            ),
        ) as call:
            await app.call_tool("restore_gameplay", {"timeout_s": 1.0})

        # The restore itself is still the exact empty client command. The second
        # bridge call is the postcondition probe, contracted in the class below.
        self.assertEqual(
            call.await_args_list[0].args, ("restore_gameplay", {}, "client", 1.0)
        )


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


class RestoreGameplayPostconditionContractTest(unittest.IsolatedAsyncioTestCase):
    """ok:1 must mean an observed postcondition, not a line of code reached.

    The Enforce dispatch closes the verdict blind: RestoreGameplay() and
    ReleaseCamera() return nothing and result.ok = true is unconditional
    (MCPClientBridge.c:706-711), while both have exits that do nothing at all
    (if (!mission) return; at :3919-3922; the m_ControlsSuppressed guard at
    :3924). Ficha fb-20260903-125244-4f83. Python cannot read controls, HUD or
    the simulation flag from here, but it can re-read the camera through the
    camera_get verb that already exists, so that is the one postcondition this
    layer may claim -- and the only ground on which it may answer ok.
    """

    def _app(self) -> tuple[Any, Any]:
        return server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def test_ok_is_given_only_after_the_probe_shows_the_player_camera(self) -> None:
        app, runtime = self._app()
        with patch.object(
            runtime,
            "call_bridge",
            new=AsyncMock(
                side_effect=[
                    {"id": 7, "ok": 1, "error": ""},
                    _camera_probe(error="player_camera_active"),
                ]
            ),
        ) as call:
            result = _content_json(
                await app.call_tool("restore_gameplay", {"timeout_s": 1.0})
            )

        self.assertEqual(len(call.await_args_list), 2)
        self.assertEqual(call.await_args_list[1].args[0], "camera_get")
        self.assertEqual(call.await_args_list[1].args[2], "client")
        self.assertTrue(result["ok"])
        self.assertIs(result["camera_released"], True)
        # A green that does not name what it never looked at reads as a full
        # restore, which is the class of lie the ficha reported.
        self.assertEqual(result["not_verified"], ["controls", "hud", "simulation"])

    async def test_a_camera_still_mounted_is_not_answered_as_ok(self) -> None:
        app, runtime = self._app()
        with patch.object(
            runtime,
            "call_bridge",
            new=AsyncMock(
                side_effect=[
                    {"id": 7, "ok": 1, "error": ""},
                    _camera_probe(viewport_moved=1, error=""),
                ]
            ),
        ):
            with self.assertRaises(Exception) as raised:
                await app.call_tool("restore_gameplay", {"timeout_s": 1.0})

        self.assertEqual(type(raised.exception).__name__, "ToolError")
        self.assertIn("camera_still_active", str(raised.exception))

    async def test_an_unreadable_probe_fails_closed(self) -> None:
        app, runtime = self._app()
        with patch.object(
            runtime,
            "call_bridge",
            new=AsyncMock(
                side_effect=[
                    {"id": 7, "ok": 1, "error": ""},
                    server.ToolError("timeout waiting for camera_get id=8"),
                ]
            ),
        ):
            with self.assertRaises(Exception) as raised:
                await app.call_tool("restore_gameplay", {"timeout_s": 1.0})

        self.assertEqual(type(raised.exception).__name__, "ToolError")
        self.assertIn("restore_unverified", str(raised.exception))
        # The caller must be able to tell "the restore ran and I could not read
        # it" from "the restore never ran".
        self.assertIn("camera_get", str(raised.exception))

    async def test_a_probe_without_a_camera_block_fails_closed(self) -> None:
        app, runtime = self._app()
        with patch.object(
            runtime,
            "call_bridge",
            new=AsyncMock(
                side_effect=[
                    {"id": 7, "ok": 1, "error": ""},
                    {"id": 8, "ok": 1, "error": ""},
                ]
            ),
        ):
            with self.assertRaises(Exception) as raised:
                await app.call_tool("restore_gameplay", {"timeout_s": 1.0})

        self.assertEqual(type(raised.exception).__name__, "ToolError")
        self.assertIn("restore_unverified", str(raised.exception))

    async def test_a_probe_that_could_not_look_fails_closed(self) -> None:
        # camera.ok=false is BuildCameraResult's client_not_in_game exit
        # (MCPClientBridge.c:3654-3660): the probe answered, and its answer is
        # "I could not look", which is not a released camera.
        app, runtime = self._app()
        with patch.object(
            runtime,
            "call_bridge",
            new=AsyncMock(
                side_effect=[
                    {"id": 7, "ok": 1, "error": ""},
                    _camera_probe(ok=0, error="client_not_in_game"),
                ]
            ),
        ):
            with self.assertRaises(Exception) as raised:
                await app.call_tool("restore_gameplay", {"timeout_s": 1.0})

        self.assertEqual(type(raised.exception).__name__, "ToolError")
        self.assertIn("restore_unverified", str(raised.exception))
        self.assertIn("client_not_in_game", str(raised.exception))

    async def test_a_failed_restore_is_never_masked_by_the_probe(self) -> None:
        # wait_for_result already raises on a falsy bridge ok, so the probe must
        # not run and must not rewrite that red into a verification verdict.
        app, runtime = self._app()
        with patch.object(
            runtime,
            "call_bridge",
            new=AsyncMock(side_effect=server.ToolError("client_not_in_game")),
        ) as call:
            with self.assertRaises(Exception) as raised:
                await app.call_tool("restore_gameplay", {"timeout_s": 1.0})

        self.assertEqual(len(call.await_args_list), 1)
        self.assertIn("client_not_in_game", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
