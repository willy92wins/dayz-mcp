"""Shared doubles for the Expansion AI adapter tests (foundation and spawn)."""

from __future__ import annotations

import re
from pathlib import Path

from dayz_mcp.bridge_readiness import (
    EXPANSION_AI_ARG_CONTRACT,
    EXPANSION_AI_VERB_BITS,
    EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH,
)
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import EnforceString

GOOD_XAI = EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH


def xai_wire(commands: list[str] | tuple[str, ...], hash_: str = GOOD_XAI) -> str:
    """The poll's xai= value announcing the adapter verbs among commands."""

    bits = 0
    for index, name in enumerate(EXPANSION_AI_VERB_BITS):
        if name in commands:
            bits |= 1 << index
    return f"{hash_}.{bits}"


def wire_caps(commands: list[str] | tuple[str, ...]) -> list[str]:
    """caps= names on the wire: adapter verbs travel in the xai mask."""

    return [name for name in commands if name not in EXPANSION_AI_ARG_CONTRACT]
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
