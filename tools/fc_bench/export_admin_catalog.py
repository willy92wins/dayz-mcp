"""Write catalog_admin.json from the tools/list answer of --tool-pack admin.

List only: this builds the app in-process and dumps it, as
tests/test_admin_tool_pack.py::test_embedded_admin_catalog_is_exact does.
No server, loopback, daemon or port. Run from ``tools/``:

    python -B -m fc_bench.export_admin_catalog

tests/test_fc_bench_catalog.py pins the committed file against this output.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from dayz_mcp.server import ServerConfig, build_app

SOURCE = (
    "The tools/list answer of --tool-pack admin: name, description and "
    "inputSchema of each listed tool, in listing order. Written by "
    "fc_bench/export_admin_catalog.py; tests/test_fc_bench_catalog.py fails "
    "when it no longer matches the code."
)


def render(tools: list[dict]) -> str:
    return (
        json.dumps(
            {"meta": {"source": SOURCE, "tools": len(tools)}, "tools": tools},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


async def admin_catalog_tools() -> list[dict]:
    app, _runtime = build_app(ServerConfig(tool_pack="admin", log_sink=lambda _message: None))
    tools = await app.list_tools()
    dumped: list[dict] = []
    for tool in tools:
        dump = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
        dumped.append({
            "name": dump["name"],
            "description": dump["description"],
            "inputSchema": dump["inputSchema"],
        })
    return dumped


def main() -> int:
    tools = asyncio.run(admin_catalog_tools())
    path = Path(__file__).with_name("catalog_admin.json")
    path.write_text(render(tools), encoding="utf-8", newline="\n")
    print(f"{path}: {len(tools)} tools", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
