"""Ticket 983a: camera_set echoes the FOV it applied as fov_applied.

The wire has no engine FOV getter (Camera.GetCurrentFOV froze the client
render after camera_set, MCPClientBridge.c:4773-4776), so a successful
apply echoes the value the setter path received, in radians, and fov=0
reaches no SetFOV at all (MCPClientBridge.c:5417/:5461), reported as an
explicit null. The ~68.5 degrees vertical at 1920x1080 default is an
owner-provided contextual measurement (run 8834df0e, 2026-10-04),
documented in the tool description and never read back from the engine.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp.bridge_errors import _bridge_error
from dayz_mcp.server import ServerConfig, ToolError, build_app
from tests.mcp_helpers import _assert_tool_error, _content_json

SERVER = _TOOLS_DIR / "dayz_mcp" / "server.py"


def _success_wire(applied_mode: str) -> dict[str, Any]:
    # The bridge answers a successful apply with int ok=1 and the shared
    # camera observation (MCPClientBridge.c:5477, BuildCameraResult). That
    # nested legacy camera is an MCPCamera, which declares `float fov`
    # (MCPMessages.c:357), so the serialized object carries fov 0.0: no getter
    # ever fills it.
    return {
        "id": 1,
        "ok": 1,
        "cmd": "camera_set",
        "camera": {"ok": 1, "view": "scripted", "applied_mode": applied_mode, "viewport_moved": 1, "fov": 0.0},
    }


class CameraSetFovAppliedTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # The loopback never starts: the bridge boundary is stubbed, so the
        # echo logic runs against the real app and runtime without sockets.
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        self.calls: list[dict[str, Any]] = []

    def stub_bridge(
        self,
        result: dict[str, Any] | None = None,
        error: Exception | None = None,
    ):
        async def call_bridge(
            cmd: str, args: dict[str, Any], peer: str, timeout_s: float
        ) -> dict[str, Any]:
            self.calls.append({"cmd": cmd, "args": dict(args), "peer": peer})
            if error is not None:
                raise error
            assert result is not None
            return dict(result)

        return patch.object(self.runtime, "call_bridge", call_bridge)

    async def test_983a_camera_set_echoes_positive_fov(self) -> None:
        # Every cam_mode reaches SetFOV through the same validated value
        # (args["fov"], MCPClientBridge.c:5796), so the echo is that value in
        # radians, whatever mode applied it.
        cases = [
            ("orient", {"cam_mode": "orient", "cam_pos": [1.0, 2.0, 3.0], "cam_orientation": [10.0, 20.0, 30.0]}, 1.05),
            ("lookat", {"cam_mode": "lookat", "cam_pos": [1.0, 2.0, 3.0], "look_at": [4.0, 5.0, 6.0]}, 0.85),
            ("look_at alias", {"cam_mode": "look_at", "cam_pos": [1.0, 2.0, 3.0], "look_at": [4.0, 5.0, 6.0]}, 0.75),
            ("matrix", {"cam_mode": "matrix", "cam_matrix": [1.0] * 12}, 1.25),
            ("free look_at", {"cam_mode": "free", "cam_pos": [1.0, 2.0, 3.0], "look_at": [4.0, 5.0, 6.0]}, 1.4),
            ("free orientation", {"cam_mode": "free", "cam_pos": [1.0, 2.0, 3.0], "cam_orientation": [90.0, 0.0, 0.0]}, 1.1),
        ]
        for label, tool_args, fov in cases:
            with self.subTest(cam_mode=label):
                self.calls = []
                with self.stub_bridge(_success_wire(tool_args["cam_mode"])):
                    result = _content_json(
                        await self.app.call_tool("camera_set", dict(tool_args, fov=fov, timeout_s=1.0))
                    )
                self.assertTrue(result["ok"])
                # Top-level echo of the validated radians, exactly as sent.
                self.assertIn("fov_applied", result)
                self.assertEqual(result["fov_applied"], fov)
                # The nested legacy camera observation stays untouched, its
                # own fov included: the echo is added beside it, never inside it.
                self.assertEqual(result["camera"], _success_wire(tool_args["cam_mode"])["camera"])
                self.assertNotIn("fov_applied", result["camera"])
                # The request schema and the bridge arguments are unchanged.
                self.assertEqual(self.calls[0]["cmd"], "camera_set")
                self.assertEqual(self.calls[0]["args"]["fov"], fov)

    async def test_983a_zero_fov_is_explicit_null(self) -> None:
        # fov=0 applies nothing (the bridge skips SetFOV), so the answer must
        # carry the key with JSON null instead of omitting it or reporting a
        # measured/default value.
        for label, tool_args in (
            ("explicit zero", {"cam_mode": "orient", "cam_pos": [1.0, 2.0, 3.0], "cam_orientation": [0.0, 0.0, 0.0], "fov": 0.0}),
            ("defaulted zero", {"cam_mode": "matrix", "cam_matrix": [1.0] * 12}),
        ):
            with self.subTest(fov=label):
                self.calls = []
                with self.stub_bridge(_success_wire(tool_args["cam_mode"])):
                    result = _content_json(
                        await self.app.call_tool("camera_set", dict(tool_args, timeout_s=1.0))
                    )
                self.assertTrue(result["ok"])
                self.assertIn("fov_applied", result)
                self.assertIsNone(result["fov_applied"])
                # The wire shape is null, not 0 or a missing key.
                self.assertIsNone(json.loads(json.dumps(result))["fov_applied"])
                self.assertEqual(self.calls[0]["args"]["fov"], 0.0)

    async def test_983a_failed_camera_set_has_no_applied_claim(self) -> None:
        # A bridge refusal keeps its error: the caller sees the failure, not a
        # success carrying fov_applied.
        with self.subTest("bridge failure keeps its error"):
            refused = {"id": 1, "ok": 0, "cmd": "camera_set", "error": "camera_create_failed"}
            with self.stub_bridge(error=_bridge_error(refused, "camera_set")):
                with self.assertRaises(ToolError) as err:
                    await self.app.call_tool(
                        "camera_set",
                        {"cam_mode": "orient", "cam_pos": [1.0, 2.0, 3.0], "cam_orientation": [0.0, 0.0, 0.0], "fov": 1.1, "timeout_s": 1.0},
                    )
            _assert_tool_error(self, err.exception)
            self.assertIn("camera_create_failed", str(err.exception))
            self.assertNotIn("fov_applied", str(err.exception))
        # The same holds for a timeout: no result, no applied claim.
        with self.subTest("timeout keeps its error"):
            with self.stub_bridge(error=ToolError("timeout waiting for camera_set id=1; client peer did not answer")):
                with self.assertRaises(ToolError) as timeout_err:
                    await self.app.call_tool(
                        "camera_set",
                        {"cam_mode": "free", "cam_pos": [1.0, 2.0, 3.0], "cam_orientation": [0.0, 0.0, 0.0], "fov": 1.1, "timeout_s": 1.0},
                    )
            _assert_tool_error(self, timeout_err.exception)
            self.assertIn("timeout waiting for camera_set", str(timeout_err.exception))
        # An apply the report cannot observe (camera.ok=0) succeeds on the
        # wire but claims nothing: the echo describes the setter path only
        # while its shared snapshot stayed legible.
        with self.subTest("illegible camera observation claims nothing"):
            illegible = {
                "id": 1,
                "ok": 1,
                "cmd": "camera_set",
                "camera": {"ok": 0, "view": "", "error": "camera_unavailable_no_scripted_camera", "viewport_moved": 0},
            }
            with self.stub_bridge(illegible):
                result = _content_json(
                    await self.app.call_tool(
                        "camera_set",
                        {"cam_mode": "orient", "cam_pos": [1.0, 2.0, 3.0], "cam_orientation": [0.0, 0.0, 0.0], "fov": 1.1, "timeout_s": 1.0},
                    )
                )
            self.assertTrue(result["ok"])
            self.assertNotIn("fov_applied", result)

    def test_983a_description_scopes_measured_default(self) -> None:
        source = SERVER.read_text(encoding="utf-8")
        start = source.index('"Requires a lease (session_acquire_wait). Set the client camera "')
        head = source[start : source.index("async def camera_set")]
        # The description is a chain of adjacent string literals; join them the
        # way Python does so a pin may span what the source splits across lines.
        description = "".join(re.findall(r'"([^"]*)"', head))
        # The echo is scoped to the value submitted to the setter path, in
        # radians, never a native readback or a projection guarantee.
        for token in (
            "fov_applied",
            "radians",
            "not a native readback",
            "does not guarantee the observed optical projection",
        ):
            self.assertIn(token, description)
        # The default is a contextual, owner-provided measurement: units,
        # resolution, run and date all travel with it.
        for token in (
            "68.5",
            "1920×1080",
            "8834df0e",
            "2026-10-04",
            "owner",
        ):
            self.assertIn(token, description)
        # Zero changes nothing and promises no reset to the measured default,
        # the singleton free camera included.
        self.assertIn("does not guarantee resetting to that measured default", description)
        # The no-getter qualification stays pinned next to the echo.
        self.assertIn("no Camera.IsInterpolationComplete / GetCurrentFOV", description)


if __name__ == "__main__":
    unittest.main()
