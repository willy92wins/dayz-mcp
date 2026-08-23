"""Resolve the Enforce addon root, which ships inside this repository.

The addon is published at ``addon/``; a staging run keeps a copy at ``mod/``
beside the tools. Both names are tried at every ancestor, innermost first.

The sibling ``DayZ_MCP`` layout that predates the extraction is deliberately
not searched. While it was, a development tree that kept ``addon/`` out of its
worktree resolved to the sibling instead, so the suite validated a tree this
repository does not publish, and nothing would have reported the two drifting
apart.
"""

from pathlib import Path

_BRIDGE = Path("scripts") / "5_Mission" / "MCPBridge.c"


def addon_root() -> Path:
    """Return the addon directory that contains ``scripts/5_Mission/MCPBridge.c``.

    A candidate is accepted only when the bridge source file is present; an
    empty or similarly named directory is not enough. Raises
    ``FileNotFoundError`` naming both layouts if neither is found.
    """
    for level in Path(__file__).resolve().parents:
        for name in ("addon", "mod"):
            candidate = level / name
            if (candidate / _BRIDGE).is_file():
                return candidate
    raise FileNotFoundError(
        "addon root not found: looked for 'addon' and 'mod' containing "
        "scripts/5_Mission/MCPBridge.c under each parent of "
        f"{Path(__file__).resolve()}"
    )
