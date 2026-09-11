"""Offline S3 demonstration using a fresh Python process and temporary source only."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "tools"))

from tests.test_server_freshness import ServerFreshnessTest, _NEW


async def demonstrate() -> dict:
    fixture = ServerFreshnessTest("test_dayz_run_keeps_old_behavior_and_marks_stale_on_wire")
    fixture.setUp()
    try:
        fresh = await fixture.call()
        status_before = (await fixture.call("bridge_status", {})).structuredContent
        fixture.assertIs(status_before["tool_registry_source_stale"], False)
        fixture.assertNotIn("server_code_freshness", fresh.meta or {})
        fixture.path.write_text(_NEW, encoding="utf-8")
        stale = await fixture.call()
        status_after = (await fixture.call("bridge_status", {})).structuredContent
        fixture.assertFalse(stale.isError)
        fixture.assertEqual(stale.structuredContent["implementation"], "old")
        fixture.assertEqual(fixture.marker(stale)["status"], "stale")
        fixture.assertIs(status_after["tool_registry_source_stale"], True)
        return {
            "verdict": "PASS",
            "scope": "offline_fresh_python_process_with_real_MCP_handler",
            "fresh_source_stale": status_before["tool_registry_source_stale"],
            "stale_source_stale": status_after["tool_registry_source_stale"],
            "server_modules": status_after["server_modules"],
            "stale_tool_result": stale.model_dump(mode="json", by_alias=True, exclude_none=True),
        }
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(demonstrate()), indent=2))
