"""Shared doubles for the Expansion AI adapter tests (foundation and spawn)."""

from __future__ import annotations

import re
from pathlib import Path

from dayz_mcp import loopback
from dayz_mcp.bridge_readiness import (
    EXPANSION_AI_ARG_CONTRACT,
    EXPANSION_AI_VERB_BITS,
    EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH,
    parse_xai,
)
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import EnforceString
from tests.fence_helpers import INST_SERVER, PID_SERVER

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


ADAPTER = (
    addon_root().parent
    / "addon_expansion_ai"
    / "scripts"
    / "5_Mission"
    / "MCPExpansionAI.c"
)


def bound_state() -> loopback.ServerState:
    state = loopback.ServerState(key="k")
    state.daemon_generation = "gen-xai"
    state.install_bound_peer(
        instance=INST_SERVER, role="server", pid=PID_SERVER, run_id="xai-run"
    )
    return state


def announce(
    state: loopback.ServerState,
    commands: list[str] | None,
    ach: str | None,
    xai: str | None,
    *,
    generation: str | None = None,
    announced_at: float | None = None,
) -> None:
    if commands is None:
        state._peer_caps.pop("server", None)
        return
    state._peer_caps["server"] = {
        "generation": state.daemon_generation if generation is None else generation,
        "commands": list(commands),
        "reason": "ok",
        "arg_contract_hash": ach,
        "expansion_arg_contract_hash": xai,
        "announced_at": state._now() if announced_at is None else announced_at,
        "instance": INST_SERVER,
    }


def adapter_census() -> list[str]:
    """Stored census with the adapter: base names plus the verbs the
    broker expands out of the adapter's xai bitmask."""

    source = ADAPTER.read_text(encoding="utf-8")
    verbs = re.search(r'EXPANSION_AI_VERBS = "([0-9]+)"', source)
    assert verbs is not None
    _hash, names = parse_xai(f"{GOOD_XAI}.{verbs.group(1)}")
    return _base_commands() + list(names)


def good_commands(*, lease_close: bool = True, adapter: bool = True) -> list[str]:
    commands = adapter_census() if adapter else _base_commands()
    if lease_close:
        commands += ["lease_close"]
    return commands
