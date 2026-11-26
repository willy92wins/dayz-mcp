"""c31e: world_spawn's published description warns about ContaminatedArea_Dynamic.

Spawning ContaminatedArea_Dynamic with world_spawn creates the object but its zone never starts (no trigger and no gas
in 310 s in the in-game run of the finding); ContaminatedArea_Local starts its zone on its own. The caller reads the
description FastMCP publishes after build_app, so the assertions go through effective_schema.resolve_effective_schemas
(the list_tools path), as in test_tool_description_caveats, and never grep server.py.
"""

from __future__ import annotations

import functools
import unittest

from dayz_mcp.effective_schema import resolve_effective_schemas


@functools.lru_cache(maxsize=1)
def _world_spawn_description() -> str:
    return resolve_effective_schemas()["world_spawn"]["description"]


class ContaminatedAreaNoteTest(unittest.TestCase):
    def test_dynamic_area_is_named_as_not_starting_its_zone(self) -> None:
        text = _world_spawn_description()
        self.assertIn("ContaminatedArea_Dynamic", text)
        self.assertIn("does not start", text)

    def test_local_area_is_named_for_an_active_zone(self) -> None:
        text = _world_spawn_description()
        self.assertIn("ContaminatedArea_Local", text)
        # The two names belong to one note: the Local advice follows the Dynamic warning.
        self.assertLess(text.index("ContaminatedArea_Dynamic"), text.index("ContaminatedArea_Local"))


if __name__ == "__main__":
    unittest.main()
