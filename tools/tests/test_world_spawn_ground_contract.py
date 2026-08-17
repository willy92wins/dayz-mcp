from __future__ import annotations

import unittest

from tests._addon_paths import addon_root


BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"


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


class WorldSpawnGroundContractTest(unittest.TestCase):
    """world_spawn: y == 0 means "on the ground", resolved by the bridge before CreateObjectEx.

    Gate 2026-08-17: an infected spawned with explicit flags (ECE_PLACE_ON_SURFACE|ECE_INITAI|
    ECE_CREATEPHYSICS) at y=0 reported pos_real y=0 although it settled at y=291.97 a frame later.
    The readiness probe reads GetPosition() before the deferred surface placement runs, so the
    requested Y must already be the surface Y (same contract player_teleport applies).
    """

    def setUp(self) -> None:
        self.source = BRIDGE_PATH.read_text(encoding="utf-8")
        self.body = _method_body(self.source, "protected MCPSpawnValidation ValidateSpawnArgs(")

    def test_y_zero_resolves_to_surface_before_flags_and_position(self) -> None:
        self.assertIn("if (y == 0)", self.body)
        self.assertIn("y = GetGame().SurfaceY(x, z);", self.body)
        finite_check = self.body.index("IsFiniteWorldCoord(y, worldSize)")
        resolve = self.body.index("y = GetGame().SurfaceY(x, z);")
        flags = self.body.index("if (args.flags != 0)")
        position = self.body.index("validation.pos = Vector(x, y, z);")
        self.assertLess(finite_check, resolve)
        self.assertLess(resolve, flags)
        self.assertLess(resolve, position)
        self.assertEqual(self.body.count("SurfaceY("), 1)

    def test_dispatch_still_creates_from_the_validated_position(self) -> None:
        dispatch = _method_body(self.source, "protected bool DispatchWorldSpawn(")
        self.assertIn("GetGame().CreateObjectEx(command.args.type, validation.pos, validation.flags, validation.rotation)", dispatch)
        self.assertNotIn("SurfaceY(", dispatch)


if __name__ == "__main__":
    unittest.main()
