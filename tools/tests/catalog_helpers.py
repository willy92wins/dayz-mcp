"""Read the tools/list a client-mode lease holder sees (issue #103).

Progressive disclosure (0b85e9c) keeps the pre-lease client catalog compact:
descriptions cut to 80 chars and tools outside the core set hidden. Contract
tests that pin description sentences or schemas of non-core tools must read
the full catalog, which the server only publishes once a lease is held.
test_progressive_disclosure.py pins the compact pre-lease view itself.
"""

from __future__ import annotations

from typing import Any

_CATALOG_READER_LEASE = "catalog-reader-lease"


async def list_tools_after_lease(app: Any, runtime: Any) -> list[Any]:
    """tools/list as seen while holding a lease; the prior token is restored."""
    previous = runtime.active_lease_token
    runtime.active_lease_token = _CATALOG_READER_LEASE
    try:
        return await app.list_tools()
    finally:
        runtime.active_lease_token = previous
