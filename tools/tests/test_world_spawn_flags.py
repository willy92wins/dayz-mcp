"""world_spawn ECE flag mask: KEEPHEIGHT/NOLIFETIME stay excluded (fb-9195).

The engine defines ECE_KEEPHEIGHT (524288) and ECE_NOLIFETIME (4194304), but
MCPBridge.IsAllowedSpawnFlags does not admit them. This ticket cannot change
Enforce/PBO, so the tool documents that mask and returns bad_flags for those
bits and for any other unknown bit.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp import server
from tests._addon_paths import addon_root
from tests.test_precondition_docs import _assert_world_spawn_copy
from tests.test_vehicle_trace_contract import _method_body


SERVER_BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"

_ALLOWED_CASES = (
    0,
    server.ECE_PLACE_ON_SURFACE,
    server.ECE_CREATEPHYSICS | server.ECE_TRACE,
    server.ECE_PLACE_ON_SURFACE | server.ECE_INITAI | server.ECE_CREATEPHYSICS,  # 3108
    server.ECE_PLACE_ON_SURFACE | server.ECE_EQUIP_ATTACHMENTS,
    server.ECE_PLACE_ON_SURFACE | server.ECE_NOPERSISTENCY_WORLD,
)

_BAD_CASES = (
    server.ECE_KEEPHEIGHT,
    server.ECE_NOLIFETIME,
    server.ECE_KEEPHEIGHT_NOLIFETIME,
    server.ECE_PLACE_ON_SURFACE | server.ECE_KEEPHEIGHT,
    server.ECE_PLACE_ON_SURFACE | server.ECE_NOLIFETIME,
    1,
    4096,  # ECE_AIRBORNE
    server.ECE_PLACE_ON_SURFACE | 16384,  # ECE_EQUIP_CARGO
)


class AllowedSpawnFlagsHelperTest(unittest.TestCase):
    def test_keepheight_and_nolifetime_are_not_allowed(self) -> None:
        for flags in _BAD_CASES:
            with self.subTest(flags=flags):
                self.assertFalse(server.is_allowed_spawn_flags(flags))

    def test_documented_mask_values_are_allowed(self) -> None:
        for flags in _ALLOWED_CASES:
            with self.subTest(flags=flags):
                self.assertTrue(server.is_allowed_spawn_flags(flags))

    def test_combo_constant_is_the_inbox_value(self) -> None:
        self.assertEqual(server.ECE_KEEPHEIGHT_NOLIFETIME, 4718592)

    def test_bridge_allowlist_still_omits_keepheight_and_nolifetime(self) -> None:
        flags_fn = _method_body(
            SERVER_BRIDGE.read_text(encoding="utf-8"),
            "protected bool IsAllowedSpawnFlags(",
        )
        self.assertIn(
            "ECE_INITAI | ECE_EQUIP_ATTACHMENTS | ECE_NOPERSISTENCY_WORLD | ECE_CREATEPHYSICS",
            flags_fn,
        )
        self.assertNotIn("ECE_KEEPHEIGHT", flags_fn)
        self.assertNotIn("ECE_NOLIFETIME", flags_fn)


class WorldSpawnFlagsToolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="k", port=0, log_sink=lambda _m: None)
        )

    def test_tool_description_names_the_allowed_mask_and_exclusions(self) -> None:
        description = self.app._tool_manager.get_tool("world_spawn").description or ""
        _assert_world_spawn_copy(self, description)
        self.assertIn(server.WORLD_SPAWN_FLAGS_LINE, description)

    async def test_keepheight_nolifetime_and_unknown_bits_are_bad_flags(self) -> None:
        with patch.object(self.runtime, "call_bridge", new=AsyncMock()) as call:
            for flags in _BAD_CASES:
                with self.subTest(flags=flags):
                    with self.assertRaises(server.ToolError) as raised:
                        await self.app.call_tool(
                            "world_spawn",
                            {
                                "type": "Apple",
                                "pos": [1.0, 2.0, 3.0],
                                "flags": flags,
                                "timeout_s": 1.0,
                            },
                        )
                    self.assertEqual(str(raised.exception), "Error executing tool world_spawn: bad_flags")
        call.assert_not_awaited()

    async def test_allowed_mask_still_reaches_the_bridge(self) -> None:
        for flags in _ALLOWED_CASES:
            with self.subTest(flags=flags):
                with patch.object(
                    self.runtime,
                    "call_bridge",
                    new=AsyncMock(return_value={"ok": 1}),
                ) as call:
                    await self.app.call_tool(
                        "world_spawn",
                        {
                            "type": "Apple",
                            "pos": [1.0, 2.0, 3.0],
                            "flags": flags,
                            "timeout_s": 1.0,
                        },
                    )
                call.assert_awaited_once()
                self.assertEqual(call.await_args.args[1]["flags"], flags)


if __name__ == "__main__":
    unittest.main()
