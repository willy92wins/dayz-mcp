"""capture_screenshot warns when the last camera_set failed or the caller has no lease.

Inbox 06a6. The picture stays available. The lease is not renewed, a held
lease does not certify the pose, and an unreadable session_status is
unknown rather than expired.
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import server
from dayz_mcp.lease_result_ttl import caller_presence
from dayz_mcp.server import ServerConfig, ToolError, build_app

_JPEG = b"\xff\xd8\xff\xd9"
_HELD = {
    "self": {"state": "active", "lease_id": "lease-1"},
    "owner": {"state": "active", "lease_id": "lease-1", "expires_in_s": 40.0},
}
_ABSENT = {"self": {"state": "none", "lease_id": None}, "owner": {"state": "none"}}


def _runs(run_id: str, generation: str = "gen-1", state: str = "RUNNING") -> dict[str, Any]:
    return {
        "runs": [
            {
                "run_id": run_id,
                "state": state,
                "daemon_generation_current": generation,
                "daemon_generation_at_launch": generation,
            }
        ]
    }


class CaptureCameraUnverifiedTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        self.runtime._control = SimpleNamespace(
            active_lease_id="lease-1",
            active_lease_token="tok",
            session_status=AsyncMock(return_value=_HELD),
            lifecycle_status=AsyncMock(return_value=_runs("run-a")),
        )
        self.runtime.lifecycle_status = AsyncMock(return_value=_runs("run-a"))
        self.runtime.session_heartbeat = AsyncMock(return_value={"ok": True})
        self.runtime.session_acquire = AsyncMock(return_value={"ok": True})
        self.runtime._control_with_lazy_spawn = AsyncMock(
            side_effect=AssertionError("capture must not lazy-spawn")
        )
        self.fail_camera = False
        self.fail_restore = False
        self.fail_token = "lease_expired"
        self.block_camera = False
        self.camera_entered = asyncio.Event()

        async def call_bridge(cmd: str, args: dict[str, Any], peer: str, timeout_s: float):
            if cmd == "camera_set" and self.block_camera:
                self.camera_entered.set()
                await asyncio.Event().wait()
            if cmd == "camera_set" and self.fail_camera:
                raise ToolError(self.fail_token)
            if cmd == "restore_gameplay" and self.fail_restore:
                raise ToolError(self.fail_token)
            if cmd == "camera_set":
                return {
                    "ok": 1,
                    "cmd": "camera_set",
                    "camera": {"ok": 1, "view": "scripted", "viewport_moved": 1},
                }
            if cmd == "restore_gameplay":
                return {"ok": 1, "cmd": "restore_gameplay"}
            if cmd == "camera_get":
                return {
                    "ok": 1,
                    "camera": {"ok": 1, "view": "player", "viewport_moved": 0},
                }
            raise AssertionError(cmd)

        self._bridge = patch.object(self.runtime, "call_bridge", call_bridge)
        self._bridge.start()
        self.addCleanup(self._bridge.stop)

    def _fake_capture(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "inline": {
                "data": base64.b64encode(_JPEG).decode("ascii"),
                "mimeType": "image/jpeg",
            },
            "fullres_path": None,
            "meta": {
                "frame_sha256": "abc",
                "warnings": ["render_frozen_signal"],
            },
        }

    def _set_runs(self, run_id: str, generation: str = "gen-1", state: str = "RUNNING") -> None:
        status = _runs(run_id, generation, state)
        self.runtime.lifecycle_status.return_value = status
        self.runtime._control.lifecycle_status.return_value = status

    async def _camera(self) -> None:
        await self.app.call_tool(
            "camera_set",
            {
                "cam_mode": "orient",
                "cam_pos": [1.0, 2.0, 3.0],
                "cam_orientation": [0.0, 0.0, 0.0],
                "timeout_s": 1.0,
            },
        )

    async def _capture(self) -> tuple[Any, dict[str, Any]]:
        with patch.object(server.mcp_capture, "capture_dual", side_effect=self._fake_capture):
            blocks = await self.app.call_tool("capture_screenshot", {"frames": 1})
        if isinstance(blocks, tuple):
            blocks = blocks[0]
        self.assertEqual("image", blocks[0].type)
        delivered = blocks[0].data
        if isinstance(delivered, str):
            delivered = base64.b64decode(delivered)
        self.assertEqual(_JPEG, delivered)
        meta = json.loads(blocks[1].text)
        return blocks, meta

    async def test_06a6_success_then_failed_camera_warns_on_capture(self) -> None:
        # A held lease after a successful set adds nothing: it does not certify the pose.
        await self._camera()
        _blocks, clean = await self._capture()
        self.assertNotIn("camera_unverified", clean.get("warnings") or [])
        self.assertNotIn("camera_verified", clean)
        self.assertNotIn("camera_unverified_reason", clean)

        self.fail_camera = True
        with self.assertRaises(ToolError) as raised:
            await self._camera()
        self.assertIn("lease_expired", str(raised.exception))

        seen = {"inside_lock": False}

        async def session_status(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            seen["inside_lock"] = self.runtime.tool_lock.locked()
            return _HELD

        self.runtime._control.session_status = session_status
        _blocks, meta = await self._capture()
        self.assertTrue(seen["inside_lock"])
        self.assertEqual(meta["warnings"], ["render_frozen_signal", "camera_unverified"])
        self.assertEqual(meta["camera_unverified_reason"], "lease_expired")
        self.assertEqual(meta["warnings"].count("camera_unverified"), 1)
        self.assertEqual(self.runtime.session_heartbeat.await_count, 0)
        self.runtime.session_acquire.assert_not_awaited()
        self.runtime._control_with_lazy_spawn.assert_not_awaited()

    async def test_06a6_confirmed_absence_warns_without_saying_expired(self) -> None:
        self.runtime._control.active_lease_id = None
        self.runtime._control.session_status = AsyncMock(return_value=_ABSENT)
        _blocks, meta = await self._capture()
        self.assertEqual(meta["warnings"], ["render_frozen_signal", "camera_unverified"])
        self.assertEqual(meta["camera_unverified_reason"], "no_lease")
        self.assertNotIn("expired", meta["camera_unverified_reason"])
        self.assertEqual(self.runtime.session_heartbeat.await_count, 0)

    async def test_06a6_unreadable_observation_is_unknown_not_expired(self) -> None:
        self.runtime._control.active_lease_id = None
        self.runtime._control.session_status = AsyncMock(side_effect=RuntimeError("down"))
        _blocks, meta = await self._capture()
        self.assertEqual(meta["camera_unverified_reason"], "unknown")
        self.assertNotIn("camera_unverified", meta.get("warnings") or [])
        self.assertNotIn("expired", json.dumps(meta))
        self.assertEqual(meta["warnings"], ["render_frozen_signal"])
        self.assertEqual(_JPEG, base64.b64decode(_blocks[0].data))

        # No "self" row is not confirmed absence. classify_status would return
        # None here; that must not become expired either.
        self.runtime._control.session_status = AsyncMock(
            return_value={"owner": {"state": "active", "lease_id": "other", "expires_in_s": 9}}
        )
        _blocks, meta = await self._capture()
        self.assertEqual(meta["camera_unverified_reason"], "unknown")
        self.assertNotIn("expired", json.dumps(meta))
        self.assertNotIn("camera_unverified", meta.get("warnings") or [])

    async def test_06a6_run_or_generation_change_drops_the_failed_attempt(self) -> None:
        self.fail_camera = True
        for run_id, generation in (("run-b", "gen-1"), ("run-a", "gen-2")):
            with self.subTest(run_id=run_id, generation=generation):
                self._set_runs("run-a", "gen-1")
                with self.assertRaises(ToolError):
                    await self._camera()
                self._set_runs(run_id, generation)
                _blocks, meta = await self._capture()
                self.assertNotIn("camera_unverified", meta.get("warnings") or [])
                self.assertNotEqual(meta.get("camera_unverified_reason"), "lease_expired")
                self.assertNotIn("expired", json.dumps(meta))

    async def test_06a6_restore_gameplay_drops_the_failed_attempt(self) -> None:
        self.fail_camera = True
        with self.assertRaises(ToolError):
            await self._camera()
        restored = await self.app.call_tool("restore_gameplay", {"timeout_s": 1.0})
        if isinstance(restored, tuple):
            restored = json.loads(restored[0][0].text)
        elif not isinstance(restored, dict):
            restored = json.loads(restored[0].text)
        self.assertTrue(restored.get("ok", True))
        _blocks, meta = await self._capture()
        self.assertNotIn("camera_unverified", meta.get("warnings") or [])
        self.assertNotEqual(meta.get("camera_unverified_reason"), "lease_expired")

    async def test_06a6_capture_keeps_image_and_existing_warnings(self) -> None:
        self.fail_camera = True
        with self.assertRaises(ToolError):
            await self._camera()
        blocks, meta = await self._capture()
        self.assertEqual(_JPEG, base64.b64decode(blocks[0].data))
        self.assertEqual(meta["frame_sha256"], "abc")
        self.assertEqual(meta["warnings"][0], "render_frozen_signal")
        self.assertIn("camera_unverified", meta["warnings"])
        self.assertIsNone(meta["fullres_path"])


    async def test_06a6_rejected_restore_keeps_the_failed_attempt(self) -> None:
        await self._camera()
        self.fail_camera = True
        self.fail_restore = True
        self.fail_token = "busy"
        with self.assertRaises(ToolError):
            await self._camera()
        with self.assertRaises(ToolError):
            await self.app.call_tool("restore_gameplay", {"timeout_s": 1.0})
        _blocks, meta = await self._capture()
        self.assertIn("camera_unverified", meta["warnings"])
        self.assertEqual(meta["camera_unverified_reason"], "busy")

    async def test_06a6_lifecycle_state_change_keeps_the_failed_attempt(self) -> None:
        self.fail_camera = True
        self.fail_token = "client_not_in_game"
        self._set_runs("run-a", "gen-1", "STARTING")
        with self.assertRaises(ToolError):
            await self._camera()
        _blocks, warned = await self._capture()
        self.assertEqual(warned["camera_unverified_reason"], "client_not_in_game")
        self._set_runs("run-a", "gen-1", "RUNNING")
        _blocks, still = await self._capture()
        self.assertIn("camera_unverified", still["warnings"])
        self.assertEqual(still["camera_unverified_reason"], "client_not_in_game")

    async def test_06a6_unreadable_self_state_keeps_the_image(self) -> None:
        # A non-string self.state must not escape classification and drop the grab.
        for state in ([], {"nested": True}, 1):
            with self.subTest(state=type(state).__name__):
                self.runtime._control.session_status = AsyncMock(
                    return_value={"self": {"state": state}}
                )
                blocks, meta = await self._capture()
                self.assertEqual(_JPEG, base64.b64decode(blocks[0].data))
                self.assertEqual(meta["camera_unverified_reason"], "unknown")
                self.assertNotIn("camera_unverified", meta.get("warnings") or [])
                self.assertNotIn("expired", json.dumps(meta))
                self.assertNotIn("no_lease", json.dumps(meta))

    async def test_06a6_cancelled_attempt_stays_unverified(self) -> None:
        await self._camera()
        self.fail_camera = True
        self.fail_token = "busy"
        with self.assertRaises(ToolError):
            await self._camera()
        _blocks, busy = await self._capture()
        self.assertEqual(busy["camera_unverified_reason"], "busy")
        self.assertIn("camera_unverified", busy["warnings"])

        self.fail_camera = False
        self.block_camera = True
        task = asyncio.create_task(self._camera())
        await asyncio.wait_for(self.camera_entered.wait(), 2.0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.block_camera = False
        blocks, meta = await self._capture()
        self.assertEqual(_JPEG, base64.b64decode(blocks[0].data))
        self.assertIn("camera_unverified", meta["warnings"])
        self.assertEqual(meta["camera_unverified_reason"], "cancelled")
        self.assertEqual(self.runtime.session_heartbeat.await_count, 0)

    async def test_06a6_self_without_state_is_unknown(self) -> None:
        self.runtime._control.active_lease_id = None
        self.runtime._control.session_status = AsyncMock(return_value={"self": {}})
        _blocks, meta = await self._capture()
        self.assertEqual(meta["camera_unverified_reason"], "unknown")
        self.assertNotIn("camera_unverified", meta.get("warnings") or [])
        self.assertNotEqual(meta["camera_unverified_reason"], "no_lease")


class CaptureDoesNotLazySpawnTest(unittest.IsolatedAsyncioTestCase):
    async def test_06a6_unavailable_lifecycle_does_not_spawn(self) -> None:
        from dayz_mcp.control_client import ControlClientError
        from tests.client_helpers import _fixture_client_runtime

        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, _built = build_app(config)
        spawned: list[str] = []

        def _ensure_daemon() -> bool:
            spawned.append("ensure")
            return False

        runtime._ensure_daemon = _ensure_daemon

        async def session_call(path: str, body: object = None, timeout_s: float | None = None):
            raise ControlClientError(
                "daemon_unavailable",
                request_stage="pre_request",
                http_bytes_sent=0,
            )

        runtime._control._session_call = session_call
        runtime._control.session_status = AsyncMock(
            return_value={"self": {"state": "active", "lease_id": "lease-1"}}
        )
        with patch.object(
            server.mcp_capture,
            "capture_dual",
            return_value={
                "inline": {
                    "data": base64.b64encode(_JPEG).decode("ascii"),
                    "mimeType": "image/jpeg",
                },
                "fullres_path": None,
                "meta": {"warnings": ["render_frozen_signal"]},
            },
        ):
            blocks = await app.call_tool("capture_screenshot", {"frames": 1})
        if isinstance(blocks, tuple):
            blocks = blocks[0]
        self.assertEqual("image", blocks[0].type)
        self.assertEqual(spawned, [])


class CallerPresenceTest(unittest.TestCase):
    def test_06a6_missing_status_is_unknown_and_idle_self_is_absent(self) -> None:
        self.assertEqual(caller_presence(None), "unknown")
        self.assertEqual(caller_presence({"owner": {"state": "active"}}), "unknown")
        self.assertEqual(caller_presence(_ABSENT), "absent")
        self.assertEqual(caller_presence(_HELD), "held")
        self.assertEqual(caller_presence({"self": {}}), "unknown")
        self.assertEqual(caller_presence({"self": {"state": "mystery"}}), "unknown")
        self.assertEqual(caller_presence({"self": {"state": []}}), "unknown")
        self.assertEqual(caller_presence({"self": {"state": {"x": 1}}}), "unknown")
        self.assertEqual(caller_presence({"self": {"state": 0}}), "unknown")
        self.assertNotEqual(caller_presence(None), "expired")


if __name__ == "__main__":
    unittest.main()
