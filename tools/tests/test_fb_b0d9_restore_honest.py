"""restore_gameplay names an unverified render; capture_screenshot names a freeze.

No daemon, no window, no DayZ: restore uses a fake bridge; capture reuses the
synthetic grab fakes from test_capture_frame_stale.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any
from unittest import mock
from unittest.mock import AsyncMock, patch

import mcp_capture
from dayz_mcp import server
from tests.test_capture_frame_stale import CMDLINE, _backend
from tests.test_restore_gameplay_contract import _camera_probe, _content_json
from tests._tiers import slow_test


def _warnings(payload: dict[str, Any] | None) -> list[Any]:
    warnings = (payload or {}).get("warnings")
    return list(warnings) if isinstance(warnings, list) else []


class RestoreHonestB0d9Test(unittest.IsolatedAsyncioTestCase):
    def _app(self) -> tuple[Any, Any]:
        return server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def _restore_description(self, app: Any) -> str:
        try:
            tools = {tool.name: tool for tool in await app.list_tools()}
            return tools["restore_gameplay"].description or ""
        except Exception as exc:
            if isinstance(exc, self.failureException):
                raise
            self.fail(str(exc))

    def _assert_no_retry_advice(self, text: str) -> None:
        self.assertNotIn("Retry restore_gameplay", text)
        self.assertNotRegex(text, r"(?i)retry\s+restore_gameplay")
        self.assertNotIn("retried", text)

    async def test_b0d9_ok_restore_names_render_in_not_verified(self) -> None:
        app, runtime = self._app()
        try:
            with patch.object(
                runtime,
                "call_bridge",
                new=AsyncMock(
                    side_effect=[
                        {"id": 7, "ok": 1, "error": ""},
                        _camera_probe(),
                    ]
                ),
            ):
                result = _content_json(
                    await app.call_tool("restore_gameplay", {"timeout_s": 1.0})
                )
        except Exception as exc:
            if isinstance(exc, self.failureException):
                raise
            self.fail(str(exc))
        self.assertEqual(
            result["not_verified"], ["controls", "hud", "simulation", "render"]
        )

    async def test_b0d9_probe_failure_does_not_advise_retry_restore_gameplay(self) -> None:
        app, runtime = self._app()
        try:
            description = await self._restore_description(app)
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
                await app.call_tool("restore_gameplay", {"timeout_s": 1.0})
        except server.ToolError as exc:
            message = str(exc)
        except Exception as exc:
            if isinstance(exc, self.failureException):
                raise
            self.fail(str(exc))
        else:
            self.fail("restore_gameplay did not raise")
        self.assertIn("restore_unverified", message)
        self._assert_no_retry_advice(message)
        self._assert_no_retry_advice(description)

    async def test_b0d9_camera_still_active_does_not_advise_retry_restore_gameplay(
        self,
    ) -> None:
        app, runtime = self._app()
        try:
            description = await self._restore_description(app)
            with patch.object(
                runtime,
                "call_bridge",
                new=AsyncMock(
                    side_effect=[
                        {"id": 7, "ok": 1, "error": ""},
                        _camera_probe(viewport_moved=1, view="scripted", error=""),
                    ]
                ),
            ):
                await app.call_tool("restore_gameplay", {"timeout_s": 1.0})
        except server.ToolError as exc:
            message = str(exc)
        except Exception as exc:
            if isinstance(exc, self.failureException):
                raise
            self.fail(str(exc))
        else:
            self.fail("restore_gameplay did not raise")
        self.assertIn("camera_still_active", message)
        self._assert_no_retry_advice(message)
        self._assert_no_retry_advice(description)

    async def test_b0d9_unverified_restore_does_not_advise_retry_restore_gameplay(
        self,
    ) -> None:
        app, runtime = self._app()
        try:
            description = await self._restore_description(app)
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
                await app.call_tool("restore_gameplay", {"timeout_s": 1.0})
        except server.ToolError as exc:
            message = str(exc)
        except Exception as exc:
            if isinstance(exc, self.failureException):
                raise
            self.fail(str(exc))
        else:
            self.fail("restore_gameplay did not raise")
        self.assertIn("restore_unverified", message)
        self._assert_no_retry_advice(message)
        self._assert_no_retry_advice(description)


class CaptureFrozenSignalB0d9Test(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="frame_state_")
        self.addCleanup(self._tmp.cleanup)
        self.state_path = os.path.join(self._tmp.name, "capture-frame-state.json")
        patcher = mock.patch.dict(os.environ, {"DAYZ_MCP_FRAME_STATE_PATH": self.state_path})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _capture(
        self,
        seed: int,
        *,
        distinct_per_frame: bool = False,
        frames: int = 1,
    ) -> dict[str, Any]:
        with mock.patch.object(
            mcp_capture,
            "_run_window_capture",
            side_effect=_backend(seed, distinct_per_frame=distinct_per_frame),
        ):
            return mcp_capture.capture_dual(frames=frames, cmdline_match=CMDLINE, scale=128)

    def _annotate(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return mcp_capture._annotate_render_frozen_signal(payload)
        except Exception as exc:
            if isinstance(exc, self.failureException):
                raise
            self.fail(str(exc))

    @slow_test
    def test_b0d9_frames_4_identical_carry_render_frozen_signal(self) -> None:
        try:
            result = self._capture(seed=7, frames=4)
        except Exception as exc:
            if isinstance(exc, self.failureException):
                raise
            self.fail(str(exc))
        self.assertIn("render_frozen_signal", _warnings(result.get("meta")))

    @slow_test
    def test_b0d9_frames_4_distinct_do_not_carry_render_frozen_signal(self) -> None:
        try:
            frozen = self._capture(seed=7, frames=4)
            live = self._capture(seed=500, frames=4, distinct_per_frame=True)
        except Exception as exc:
            if isinstance(exc, self.failureException):
                raise
            self.fail(str(exc))
        self.assertIn("render_frozen_signal", _warnings(frozen.get("meta")))
        self.assertNotIn("render_frozen_signal", _warnings(live.get("meta")))

    @slow_test
    def test_b0d9_frames_1_does_not_carry_render_frozen_signal(self) -> None:
        try:
            frozen = self._capture(seed=7, frames=4)
            single = self._capture(seed=8, frames=1)
        except Exception as exc:
            if isinstance(exc, self.failureException):
                raise
            self.fail(str(exc))
        self.assertIn("render_frozen_signal", _warnings(frozen.get("meta")))
        self.assertNotIn("render_frozen_signal", _warnings(single.get("meta")))

    @slow_test
    def test_b0d9_missing_metrics_do_not_carry_render_frozen_signal(self) -> None:
        try:
            frozen = self._capture(seed=7, frames=4)
            payloads = [
                self._annotate({"frame_stale_detail": {}}),
                self._annotate(
                    {"frame_stale_detail": {"frames": 4, "distinct_frames": 1}}
                ),
                self._annotate(
                    {
                        "frame_stale_detail": {
                            "frames": 4,
                            "distinct_frames": 1,
                            "max_adjacent_delta": "0",
                        }
                    }
                ),
                self._annotate(
                    {
                        "frame_stale_detail": {
                            "frames": 4,
                            "distinct_frames": True,
                            "max_adjacent_delta": 0,
                        }
                    }
                ),
            ]
            with mock.patch.object(mcp_capture, "_frame_evidence", return_value={}):
                missing = self._capture(seed=11, frames=4)
        except Exception as exc:
            if isinstance(exc, self.failureException):
                raise
            self.fail(str(exc))
        self.assertIn("render_frozen_signal", _warnings(frozen.get("meta")))
        for payload in payloads:
            self.assertNotIn("render_frozen_signal", _warnings(payload))
        self.assertNotIn("render_frozen_signal", _warnings(missing.get("meta")))

    @slow_test
    def test_b0d9_existing_warnings_survive_and_token_is_not_duplicated(self) -> None:
        try:
            result = self._capture(seed=7, frames=4)
            payload = dict(result["meta"])
            payload["warnings"] = ["already_there"]
            once = self._annotate(payload)
            twice = self._annotate(once)
        except Exception as exc:
            if isinstance(exc, self.failureException):
                raise
            self.fail(str(exc))
        self.assertIn("already_there", twice["warnings"])
        self.assertEqual(twice["warnings"].count("render_frozen_signal"), 1)

    @slow_test
    def test_b0d9_token_leaves_other_meta_fields_identical(self) -> None:
        try:
            result = self._capture(seed=7, frames=4)
            meta = result["meta"]
            stripped = {key: value for key, value in meta.items() if key != "warnings"}
            annotated = self._annotate(dict(stripped))
        except Exception as exc:
            if isinstance(exc, self.failureException):
                raise
            self.fail(str(exc))
        self.assertIn("render_frozen_signal", _warnings(meta))
        self.assertEqual(
            {key: annotated[key] for key in stripped},
            stripped,
        )
        self.assertEqual(set(annotated) - set(stripped), {"warnings"})


if __name__ == "__main__":
    unittest.main()
