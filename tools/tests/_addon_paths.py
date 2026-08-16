"""Resolve the Enforce addon root across the two layouts this project uses.

The tests used to climb a fixed number of parents and assume ``DayZ_MCP``
sat next to the development tree. That only works on the author's machine.
A standalone public repo keeps the same addon *inside* the repo, at
``addon/``, and a staging run keeps it at ``mod/`` beside the tools. All
three names are therefore tried at every ancestor, innermost first:
``addon`` (extracted repo), ``mod`` (staging), ``DayZ_MCP``
(sibling-of-dev-tree layout).
"""

from pathlib import Path

_BRIDGE = Path("scripts") / "5_Mission" / "MCPBridge.c"


def addon_root() -> Path:
    """Return the addon directory that contains ``scripts/5_Mission/MCPBridge.c``.

    Two layouts exist because the project is being extracted from a sibling
    development tree (``DayZ_MCP`` next to ``DayZ_MCP_dev``) into a
    standalone repository (``addon/`` inside the repo). A candidate is
    accepted only when the bridge source file is present; an empty or
    similarly named folder is not enough. Raises ``FileNotFoundError``
    naming both layouts if neither is found.
    """
    for level in Path(__file__).resolve().parents:
        for name in ("addon", "mod", "DayZ_MCP"):
            candidate = level / name
            if (candidate / _BRIDGE).is_file():
                return candidate
    raise FileNotFoundError(
        "addon root not found: looked for 'addon' and 'DayZ_MCP' "
        "containing scripts/5_Mission/MCPBridge.c under each parent of "
        f"{Path(__file__).resolve()}"
    )
