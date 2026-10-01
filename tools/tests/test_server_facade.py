"""dayz_mcp.server stays a facade after backlog 71fc moved definitions out of it.

The fixture is sorted(vars(dayz_mcp.server)) at 3b31cbe, captured in a clean
interpreter before the move. Every name in it must still resolve on the module,
so `from dayz_mcp.server import X` and `dayz_mcp.server.X` keep working, and a
name that now lives in a split-out module must be the same object on both.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from dayz_mcp import (
    box_occupancy,
    bridge_errors,
    bridge_readiness,
    launch_logs,
    server,
    tool_args,
    tool_catalog,
    world_results,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "server_namespace_3b31cbe.json"
SPLIT_MODULES = (
    box_occupancy,
    bridge_errors,
    bridge_readiness,
    launch_logs,
    tool_args,
    tool_catalog,
    world_results,
)
# server.py binds these only under `if os.name == "nt":`.
_NT_ONLY = frozenset({"native_bundle", "native_launcher_backend"})


def _base_names() -> set[str]:
    names = set(json.loads(FIXTURE.read_text(encoding="utf-8"))["names"])
    if os.name != "nt":
        names -= _NT_ONLY
    return names


class ServerFacadeTest(unittest.TestCase):
    def test_every_name_server_had_before_the_split_still_resolves(self) -> None:
        missing = sorted(_base_names() - set(vars(server)))
        self.assertEqual(missing, [], "dayz_mcp.server lost names it had at 3b31cbe")

    def test_a_split_out_name_is_the_same_object_on_server(self) -> None:
        for module in SPLIT_MODULES:
            for name in sorted(_base_names() & set(vars(module))):
                with self.subTest(module=module.__name__, name=name):
                    self.assertIs(getattr(server, name), getattr(module, name))


if __name__ == "__main__":
    unittest.main()
