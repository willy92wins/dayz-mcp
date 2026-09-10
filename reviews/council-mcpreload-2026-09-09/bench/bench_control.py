"""The control the spike did not run: the same payload, WITHOUT a supervisor.

The spike timed `await session.call_tool("big_payload", ...)` through the mcp SDK with a
supervisor in the middle, measured 32 MB in 15.63 s, and concluded that forwarding by
lines is superlinear and a production supervisor needs length framing.

But that timing spans both SDK ends as well. Measured separately today: the supervisor's
own share -- readline + loads + dumps + write, plus both pipes -- is ~0.2 s for 32 MB.

So run the client straight at the worker. If the direct path is also slow, the cost is
the SDK at the two ends, it is paid with or without a supervisor, and the framing
requirement is void.
"""
import asyncio
import sys
import time
from pathlib import Path

SPIKE = Path(
    r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev"
    r"\reviews\council-mcpreload-2026-09-09\spike"
)

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


def text_of(result) -> str:
    parts = getattr(result, "content", None) or []
    return "".join(getattr(part, "text", "") for part in parts)


async def measure(command: list[str], label: str) -> None:
    params = StdioServerParameters(command=command[0], args=command[1:], cwd=str(SPIKE))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print(f"\n=== {label} ===")
            print(f"{'carga':>8} {'tiempo':>10} {'MB/s':>9}")
            for kb in (1024, 8192, 32768):
                start = time.perf_counter()
                blob = text_of(await session.call_tool("big_payload", {"kilobytes": kb}))
                took = time.perf_counter() - start
                assert len(blob) == kb * 1024, f"{len(blob)} != {kb * 1024}"
                print(f"{kb // 1024:>6} MB {took:>9.3f}s {(kb / 1024) / took:>8.1f}")


async def main() -> None:
    # Direct: client -> worker. No supervisor in the path at all.
    await measure([sys.executable, "-u", str(SPIKE / "worker.py")], "SIN supervisor (control)")
    # The spike's own path, for comparison on this machine today.
    await measure([sys.executable, "-u", str(SPIKE / "supervisor.py")], "CON supervisor (como el spike)")


if __name__ == "__main__":
    asyncio.run(main())
