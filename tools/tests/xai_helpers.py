"""Shared doubles for the Expansion AI adapter tests (foundation and spawn)."""

from __future__ import annotations

import re
from pathlib import Path

from dayz_mcp.bridge_readiness import EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import EnforceString

GOOD_XAI = EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH
BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"


class _WireString(EnforceString):
    """EnforceString with the concatenation Enforce gives string."""

    __slots__ = ()

    def __add__(self, other: object) -> "_WireString":
        if isinstance(other, EnforceString):
            return _WireString(self.data + other.data)
        return NotImplemented


def _base_commands() -> list[str]:
    caps = re.search(
        r'SERVER_CAPABILITIES = ((?:"[^"]*"\s*\+?\s*)+);',
        Path(BRIDGE).read_text(encoding="utf-8"),
    )
    assert caps is not None
    names: list[str] = []
    for chunk in re.findall(r'"([^"]*)"', caps.group(1)):
        names.extend(name for name in chunk.split(",") if name)
    return names
