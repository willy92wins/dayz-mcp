"""What vehicle_enter's seated=1 and timeout mean, and what a seat or a lookup needs (inbox 6f62).

The descriptions said seated=1 only confirmed that the command was accepted. The
bridge answers seated=1 after IsSeatReady read the player in the driver seat of
that vehicle with the get-in over, and answers timeout when its own job deadline,
JOB_TIMEOUT_S, passes first, whatever the tool's timeout_s. These tests pin the
descriptions and docs/VEHICLE_TESTING.md to that source, and the two seats and
the attached-item lookup the Arma2Quad run measured.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from dayz_mcp.server import ServerConfig, build_app
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import method_body

BRIDGE = (addon_root() / "scripts" / "5_Mission" / "MCPBridge.c").read_text(encoding="utf-8")
VEHICLE_DOC = Path(__file__).resolve().parents[2] / "docs" / "VEHICLE_TESTING.md"


def _job_timeout_s() -> float:
    match = re.search(r"protected const float JOB_TIMEOUT_S = ([0-9.]+);", BRIDGE)
    if match is None:
        raise AssertionError("JOB_TIMEOUT_S not found in MCPBridge.c")
    return float(match.group(1))


class SeatJobSourceTest(unittest.TestCase):
    def test_the_seat_job_deadline_is_job_timeout_s(self) -> None:
        self.assertEqual(_job_timeout_s(), 5.0)
        body = method_body(
            BRIDGE, "protected bool DispatchVehicleEnter(MCPCommand command, MCPResult result)"
        )
        self.assertIn('job.kind = "seat";', body)
        self.assertIn("job.deadline_s = m_ElapsedS + JOB_TIMEOUT_S;", body)
        self.assertNotIn("timeout_s", body)

    def test_seated_is_posted_only_for_the_driver_seat_after_the_get_in(self) -> None:
        body = method_body(BRIDGE, "protected bool IsSeatReady(MCPJob job)")
        for check in (
            "human.GetCommand_Vehicle()",
            "transport != job.subject",
            "vehicleCommand.IsGettingIn()",
            "vehicleCommand.GetVehicleSeat() == DayZPlayerConstants.VEHICLESEAT_DRIVER",
        ):
            with self.subTest(check=check):
                self.assertIn(check, body)
        success = method_body(BRIDGE, "protected void PostSeatSuccess(MCPJob job)")
        self.assertIn("result.seated = true;", success)
        self.assertIn('result.seat = "driver";', success)
        timeout = method_body(BRIDGE, "protected void PostJobTimeout(MCPJob job)")
        self.assertIn('result.error = "timeout";', timeout)


class SeatDescriptionsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))
        self.tools = {tool.name: tool for tool in await app.list_tools()}

    async def test_vehicle_enter_names_the_bridge_wait_from_the_source(self) -> None:
        description = self.tools["vehicle_enter"].description or ""
        self.assertIn(f"at most {_job_timeout_s():g} s, whatever timeout_s is", description)
        self.assertIn("once the get-in ended", description)
        self.assertIn("read query_all_players (in_vehicle) before you retry", description)

    async def test_vehicle_get_in_client_says_switch_lights_needs_both_seats(self) -> None:
        description = self.tools["vehicle_get_in_client"].description or ""
        self.assertIn("with either alone action_use answers condition_failed", description)
        self.assertIn("vehicle_enter in a second car seats only the server", description)
        self.assertNotIn("still fail until vehicle_enter", description)

    async def test_object_resolve_says_an_attached_item_is_not_found(self) -> None:
        description = self.tools["object_resolve"].description or ""
        self.assertIn("An item attached to another entity", description)
        self.assertIn("answers object_not_found", description)
        self.assertIn("item_object_id", description)


class VehicleTestingDocTest(unittest.TestCase):
    def test_step_three_reads_seated_as_the_server_seat(self) -> None:
        text = VEHICLE_DOC.read_text(encoding="utf-8")
        self.assertIn("at most 5 s (`JOB_TIMEOUT_S`)", text)
        self.assertIn("`query_all_players` (`in_vehicle`)", text)
        self.assertNotIn("`seated=1` says the command was accepted", text)


if __name__ == "__main__":
    unittest.main()
