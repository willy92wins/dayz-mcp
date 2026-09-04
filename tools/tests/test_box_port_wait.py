"""fb-20260904-114520-6927, round 2: the caller learns WHY a launch was refused.

An unreadable socket table makes the box read occupied; the FIFO wait cannot
repair that, so the wait fails at once with port_scan_unknown. A port held by a
process that is not a managed run leaves the box free but the launch refused;
the result names the port instead of prescribing a wait that never ends.
"""
from __future__ import annotations

import asyncio
import unittest

from dayz_mcp import server
from tests.test_box_occupancy import FakeBoxClient


class WaitFailsFastOnUnreadableTableTest(unittest.IsolatedAsyncioTestCase):
    async def test_port_scan_unknown_returns_at_once(self) -> None:
        unreadable = {
            "box": {
                "occupied": True,
                "runs": [],
                "foreign": [],
                "ports_in_use": [],
                "queue": [{"session": "waiter-sessi", "waiting_s": 0.0}],
                "scan_known": True,
                "port_scan_known": False,
                "port_scan_reason": "port_scan_unknown",
            },
            "box_ticket": "t1",
        }
        client = FakeBoxClient([unreadable])
        sleeps: list[float] = []

        async def sleeper(delay: float) -> None:
            sleeps.append(delay)

        result = await server.execute_wait_for_box(
            client, 600.0, sleep_fn=sleeper, time_fn=lambda: 0.0, poll_interval_s=0.05
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "port_scan_unknown")
        self.assertFalse(result["box"]["port_scan_known"])
        self.assertEqual(sleeps, [])
        # Like the timeout return, the ticket is handed back so the caller
        # (dayz_test_run) releases it; no second daemon call is made here.
        self.assertEqual(result["ticket"], "t1")
        self.assertEqual(client.calls, [{"wait": True, "ticket": None}])


class ActiveRunDiagnosisTest(unittest.TestCase):
    def test_held_port_with_a_free_box_names_the_port(self) -> None:
        box = {
            "occupied": False,
            "runs": [],
            "foreign": [],
            "ports_in_use": [2302],
            "foreign_ports": [2302],
            "queue": [],
            "scan_known": True,
            "port_scan_known": True,
            "port_scan_reason": None,
        }
        failed = server._failed_active_run_result(
            project="DayZ_MCP", mode="all", box=box, started=0.0, caller_session=None, port=2302
        )
        self.assertEqual(failed["error_code"], "active_run_exists")
        self.assertEqual(failed["reason"], "port_in_use_foreign")
        self.assertEqual(failed["port"], 2302)
        self.assertIn("pass another port=", failed["hint"])
        enriched = server._enrich_active_run_result({"error_code": "active_run_exists"}, box, port=2302)
        self.assertEqual(enriched["reason"], "port_in_use_foreign")

    def test_another_port_free_keeps_the_generic_hint(self) -> None:
        box = {"occupied": False, "runs": [], "foreign": [], "ports_in_use": [2302], "foreign_ports": [2302], "queue": [], "port_scan_known": True}
        failed = server._failed_active_run_result(
            project="DayZ_MCP", mode="all", box=box, started=0.0, caller_session=None, port=2402
        )
        self.assertNotIn("reason", failed)
        self.assertIn("wait_for_box_s", failed["hint"])

    def test_unreadable_table_says_so(self) -> None:
        box = {"occupied": True, "runs": [], "foreign": [], "ports_in_use": [], "queue": [], "port_scan_known": False, "port_scan_reason": "port_attribution_unknown"}
        failed = server._failed_active_run_result(
            project="DayZ_MCP", mode="all", box=box, started=0.0, caller_session=None, port=2302
        )
        self.assertEqual(failed["reason"], "port_attribution_unknown")
        self.assertIn("waiting does not help", failed["hint"])

    def test_managed_run_holding_the_port_is_not_a_foreign_conflict(self) -> None:
        box = {
            "occupied": True,
            "runs": [{"run_id": "run-x", "mod": "@M", "label": "l", "age_s": 1.0, "owner_session": "other"}],
            "foreign": [],
            "ports_in_use": [2302],
            "foreign_ports": [],
            "queue": [],
            "port_scan_known": True,
        }
        failed = server._failed_active_run_result(
            project="DayZ_MCP", mode="all", box=box, started=0.0, caller_session=None, port=2302
        )
        self.assertEqual(failed["occupied_by_run_id"], "run-x")
        self.assertNotIn("reason", failed)

    def test_run_and_foreign_holder_are_both_named(self) -> None:
        # LOSS R2-M-1: a managed run on 2402 plus a stranger on the requested 2302.
        box = {
            "occupied": True,
            "runs": [{"run_id": "run-x", "mod": "@M", "label": "l", "age_s": 1.0, "owner_session": "other"}],
            "foreign": [],
            "ports_in_use": [2302, 2402],
            "foreign_ports": [2302],
            "queue": [],
            "port_scan_known": True,
        }
        failed = server._failed_active_run_result(
            project="DayZ_MCP", mode="all", box=box, started=0.0, caller_session=None, port=2302
        )
        self.assertEqual(failed["occupied_by_run_id"], "run-x")
        self.assertEqual(failed["reason"], "port_in_use_foreign")
        self.assertEqual(failed["port"], 2302)
        self.assertIn("after the box frees", failed["hint"])
        self.assertIn("does not free the port", failed["hint"])

    def test_any_requested_port_is_diagnosed_not_only_the_band(self) -> None:
        # LOSS R2-B-1: the API accepts ports beyond 2302-2999; foreign_ports is complete.
        box = {"occupied": False, "runs": [], "foreign": [], "ports_in_use": [], "foreign_ports": [3002, 65530], "queue": [], "port_scan_known": True}
        for port in (3002, 65530):
            with self.subTest(port):
                failed = server._failed_active_run_result(
                    project="DayZ_MCP", mode="all", box=box, started=0.0, caller_session=None, port=port
                )
                self.assertEqual(failed["reason"], "port_in_use_foreign")
                self.assertEqual(failed["port"], port)


class BlockedOnTest(unittest.TestCase):
    """ADMIN R2-H-01: blocked_on must not send a caller into the FIFO for a fault the FIFO cannot fix."""

    def _status(self, box: dict) -> dict:
        return {"owner": None, "box": box}

    def test_unreadable_table_names_the_repair_not_the_fifo(self) -> None:
        for reason in ("port_scan_unknown", "port_attribution_unknown", None):
            with self.subTest(reason):
                box = {"occupied": True, "runs": [], "foreign": [], "ports_in_use": [], "queue": [], "port_scan_known": False, "port_scan_reason": reason}
                text = server._session_status_blocked_on(self._status(box))
                self.assertIn(reason or "port_scan_unknown", text)
                self.assertIn("wait_for_box_s does not help", text)
                self.assertNotIn("join the box FIFO", text)

    def test_occupied_by_a_run_keeps_the_fifo_recipe(self) -> None:
        box = {"occupied": True, "runs": [{"run_id": "run-x"}], "foreign": [], "ports_in_use": [2302], "queue": [], "port_scan_known": True}
        text = server._session_status_blocked_on(self._status(box))
        self.assertIn("join the box FIFO", text)

    def test_free_box_is_not_blocked(self) -> None:
        box = {"occupied": False, "runs": [], "foreign": [], "ports_in_use": [], "queue": [], "port_scan_known": True}
        self.assertIsNone(server._session_status_blocked_on(self._status(box)))


if __name__ == "__main__":
    unittest.main()
