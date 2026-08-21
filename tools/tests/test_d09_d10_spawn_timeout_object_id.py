"""A failed bridge result must carry its object_id to the caller.

The bridge answers a spawn that timed out with ``ok:0, error:"timeout"`` AND the
id of the object it did create (``MCPBridge.c:3272``). Python used to build the
ToolError from the error string alone, so the handle was dropped and a retry
spawned a second, undeletable object.

The id travels in a structured attribute rather than in the message because
host content stays off the MCP wire: the message stays a fixed code.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402

from dayz_mcp import loopback, server  # noqa: E402


class SpawnTimeoutObjectIdTest(unittest.IsolatedAsyncioTestCase):
    def _runtime_with_result(self, result: dict) -> tuple[server.Runtime, int]:
        """A Runtime whose queue already holds one answered command.

        camera_get on the client peer is only a carrier: a server-peer enqueue is
        refused with 409 legacy_unbound by the instance fence, which has nothing
        to do with what this test measures.
        """
        state = loopback.ServerState("fixture-key")
        status, payload = state.enqueue_command(
            "camera_get", {}, peer="client", operation_timeout_s=5.0
        )
        self.assertEqual(status, 200)
        command_id = int(payload["id"])
        state.store_result({"id": command_id, **result})

        runtime = server.Runtime(
            server.ServerConfig(key="fixture-key", log_sink=lambda _message: None)
        )
        runtime.loopback = SimpleNamespace(state=state)
        return runtime, command_id

    async def test_failed_result_carries_object_id(self) -> None:
        runtime, command_id = self._runtime_with_result(
            {"ok": 0, "error": "timeout", "object_id": 4242}
        )
        with self.assertRaises(ToolError) as ctx:
            await runtime.wait_for_result("world_spawn", command_id, "client", 5.0)

        # The message is the bridge's fixed code, nothing else.
        self.assertEqual(str(ctx.exception), "timeout")
        # ...and the handle survives, so the caller can delete instead of retry.
        self.assertEqual(4242, getattr(ctx.exception, "object_id", None))

    async def test_failed_result_without_object_id_sets_no_attribute(self) -> None:
        # Negative control: the attribute must not appear out of nowhere, or a
        # caller cannot tell "no handle" from "handle 0".
        runtime, command_id = self._runtime_with_result({"ok": 0, "error": "bad_pos"})
        with self.assertRaises(ToolError) as ctx:
            await runtime.wait_for_result("world_spawn", command_id, "client", 5.0)
        self.assertEqual(str(ctx.exception), "bad_pos")
        self.assertIsNone(getattr(ctx.exception, "object_id", None))


if __name__ == "__main__":
    unittest.main()
