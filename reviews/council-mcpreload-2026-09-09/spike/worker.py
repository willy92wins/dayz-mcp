"""Throwaway worker: a real FastMCP server over the pipes the supervisor gives it.

impl is imported once, at process start, exactly like the tool modules whose staleness
started all this. Only a new process can serve a new VERSION.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import impl  # noqa: E402  imported once, on purpose
from mcp.server.fastmcp import FastMCP  # noqa: E402

app = FastMCP("spike-worker")


@app.tool()
def impl_version() -> str:
    """Return the VERSION this process imported, plus the pid that served it."""
    return f"{impl.VERSION}|pid={os.getpid()}"


@app.tool()
def slow_echo(text: str, seconds: float = 0.0) -> str:
    """Stay in flight for a while, so a recycle has something to drain."""
    import time

    time.sleep(min(seconds, 10.0))
    return f"{text}|pid={os.getpid()}"


@app.tool()
def big_payload(kilobytes: int = 1024) -> str:
    """A capture_screenshot-sized answer: the case that can deadlock an anonymous pipe."""
    return "A" * (max(1, min(kilobytes, 32768)) * 1024)


if __name__ == "__main__":
    app.run()
