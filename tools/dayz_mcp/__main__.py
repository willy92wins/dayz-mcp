from __future__ import annotations

from dayz_mcp import linux_compat

# Outside Windows, put the Linux implementations in place before any other module
# binds the Windows ones. A no-op on Windows.
linux_compat.install()

from dayz_mcp.server import run  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(run())
