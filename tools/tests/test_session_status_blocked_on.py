"""Actionable next steps in the public session_status response."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import server
from dayz_mcp.server import ServerConfig, build_app
from tests.test_client_mode import _fixture_client_runtime
from tests.test_mcp_tools import _content_json


LEASE_BLOCKED_ON = (
    "session lease; next: call session_acquire_wait(purpose=...) "
    "to join the lease FIFO"
)
BOX_BLOCKED_ON = (
    "DayZ test box; next: call dayz_test_run(..., wait_for_box_s=<n>) "
    "to join the box FIFO"
)


def _status_payload(
    *, owner: dict[str, object] | None = None, claimable: bool, occupied: bool
) -> dict[str, object]:
    return {
        "owner": owner,
        "queue": [],
        "self": {"state": "none", "position": None},
        "claimable": claimable,
        "audit_fault": None,
        "lifecycle_recovery_fault": None,
        "operation_tombstones": {
            "count": 0,
            "capacity": 4096,
            "saturated": False,
        },
        "cleanup_degraded": [],
        "daemon_generation": "generation-a",
        "pending_commands": 0,
        "box": {
            "occupied": occupied,
            "runs": [],
            "foreign": [],
            "ports_in_use": [],
            "queue": [],
            "scan_known": True,
        },
    }


class SessionStatusBlockedOnTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        self.runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=self.runtime):
            self.app, _built = build_app(config)

    async def _session_status(self, payload: dict[str, object]) -> dict:
        status = AsyncMock(return_value=copy.deepcopy(payload))
        with patch.object(self.runtime, "session_status", new=status):
            result = _content_json(await self.app.call_tool("session_status", {}))
        status.assert_awaited_once_with()
        return result

    async def test_free_state_has_nothing_to_wait_for(self) -> None:
        result = await self._session_status(
            _status_payload(claimable=True, occupied=False)
        )

        self.assertIn("blocked_on", result)
        self.assertIsNone(result["blocked_on"])

    async def test_foreign_lease_points_to_the_lease_fifo(self) -> None:
        owner = {
            "state": "active",
            "lease_id": "lease-a",
            "purpose": "build",
            "client": {
                "platform": "claude",
                "session": "other-session",
                "started_at_utc": "2026-08-23T00:00:00Z",
                "task_label": "other lane",
            },
            "expires_in_s": 90.0,
        }
        result = await self._session_status(
            _status_payload(owner=owner, claimable=True, occupied=False)
        )

        self.assertEqual(result.get("blocked_on"), LEASE_BLOCKED_ON)

    async def test_occupied_box_with_free_lease_points_to_the_box_fifo(self) -> None:
        result = await self._session_status(
            _status_payload(claimable=False, occupied=True)
        )

        self.assertEqual(result.get("blocked_on"), BOX_BLOCKED_ON)
        self.assertNotIn("session_acquire_wait", result["blocked_on"])

    async def test_lease_ttl_and_internal_renewal_are_in_both_descriptions(self) -> None:
        tools = {tool.name: tool for tool in await self.app.list_tools()}

        for name in ("session_acquire_wait", "session_status"):
            with self.subTest(tool=name):
                description = tools[name].description or ""
                self.assertIn("120", description)
                self.assertIn("renewal is internal", description)


if __name__ == "__main__":
    unittest.main()
