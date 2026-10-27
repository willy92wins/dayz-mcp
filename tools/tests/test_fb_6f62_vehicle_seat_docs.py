"""What vehicle_enter's seated=1 and timeout mean, and what a seat or a lookup needs (inbox 6f62).

The descriptions said seated=1 only confirmed that the command was accepted. The
bridge answers seated=1 on the first tick IsSeatReady reads the player in the
driver seat of that vehicle with the get-in over, and timeout on a tick that does
not once its own job deadline, JOB_TIMEOUT_S, has passed, whatever the tool's
timeout_s. These tests pin the descriptions and docs/VEHICLE_TESTING.md to that
source, which step of ActionSwitchLights reads which seat, and the attached-item
lookup the Arma2Quad run measured.
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


def _squashed(text: str) -> str:
    return " ".join(text.split())


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
        body = _squashed(method_body(BRIDGE, "protected bool IsSeatReady(MCPJob job)"))
        # Each check refuses on its own branch, and the driver seat is the only yes.
        for refusal in (
            "if (!human) { return false; }",
            "if (!vehicleCommand) { return false; }",
            "if (transport != job.subject) { return false; }",
            "if (vehicleCommand.IsGettingIn()) { return false; }",
        ):
            with self.subTest(refusal=refusal):
                self.assertIn(refusal, body)
        self.assertIn(
            "if (vehicleCommand.GetVehicleSeat() == DayZPlayerConstants.VEHICLESEAT_DRIVER)"
            " { return true; } return false;",
            body,
        )
        self.assertEqual(body.count("return true;"), 1)
        # The description's "or, if that one is gone, the first player".
        self.assertIn("if (!human) { human = GetFirstHuman(); job.actor = human; }", body)
        success = method_body(BRIDGE, "protected void PostSeatSuccess(MCPJob job)")
        self.assertIn("result.seated = true;", success)
        self.assertIn('result.seat = "driver";', success)
        timeout = method_body(BRIDGE, "protected void PostJobTimeout(MCPJob job)")
        self.assertIn('result.error = "timeout";', timeout)

    def test_each_tick_reads_the_seat_before_the_deadline(self) -> None:
        ready = _squashed(method_body(BRIDGE, "protected bool IsJobReady(MCPJob job)"))
        self.assertIn('if (job.kind == "seat") { return IsSeatReady(job); }', ready)
        jobs = _squashed(method_body(BRIDGE, "protected void ProcessJobs()"))
        success = jobs.index("else if (IsJobReady(job)) { PostJobSuccess(job);")
        deadline = jobs.index("else if (m_ElapsedS > job.deadline_s) { PostJobTimeout(job);")
        self.assertLess(success, deadline)


class SeatDescriptionsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))
        self.tools = {tool.name: tool for tool in await app.list_tools()}

    async def test_vehicle_enter_names_the_bridge_wait_from_the_source(self) -> None:
        description = self.tools["vehicle_enter"].description or ""
        self.assertIn(
            f"timeout on a tick that does not once {_job_timeout_s():g} s have passed, "
            "whatever timeout_s is",
            description,
        )
        self.assertIn("seated=1 on the first tick that reads it", description)
        self.assertIn("(or, if that one is gone, the first player)", description)
        self.assertIn("once the get-in ended", description)
        self.assertIn("read query_all_players (in_vehicle) before you retry", description)
        self.assertNotIn("at most", description)

    async def test_vehicle_get_in_client_says_which_step_reads_which_seat(self) -> None:
        description = _squashed(self.tools["vehicle_get_in_client"].description or "")
        self.assertIn(
            "action_use checks an action's ActionCondition on the client, where "
            "ActionSwitchLights reads this client seat",
            description,
        )
        self.assertIn(
            "the lights toggle in the action's server step, which reads "
            "vehicle_enter's server seat",
            description,
        )
        self.assertIn("vehicle_enter runs no client get-in", description)
        self.assertIn("vehicle_enter in a second car seats only the server's player", description)
        self.assertNotIn("with either alone", description)
        self.assertNotIn("still fail until vehicle_enter", description)

    async def test_object_resolve_says_an_attached_item_is_not_found(self) -> None:
        description = _squashed(self.tools["object_resolve"].description or "")
        self.assertIn("The search does not walk attachments", description)
        self.assertIn(
            "with no loose object of that class in range it answers object_not_found",
            description,
        )
        self.assertIn("the item it attaches as item_object_id", description)


class VehicleTestingDocTest(unittest.TestCase):
    def test_step_three_reads_seated_as_the_server_seat(self) -> None:
        text = VEHICLE_DOC.read_text(encoding="utf-8")
        start = text.index("3. `vehicle_enter` near the fixture pos")
        step = _squashed(text[start : text.index("4. `vehicle_get_in_client`", start)])
        self.assertIn(
            "`seated=1` means the server read the player it seated (or, if that one "
            "is gone, the first player) in the driver seat of that car once the "
            "get-in ended (`IsSeatReady`).",
            step,
        )
        self.assertIn(
            "answers `timeout` on a tick that does not read it once 5 s "
            "(`JOB_TIMEOUT_S`) have passed, whatever `timeout_s` is",
            step,
        )
        self.assertIn("read `query_all_players` (`in_vehicle`) before retrying", step)
        self.assertNotIn("`seated=1` says the command was accepted", text)


if __name__ == "__main__":
    unittest.main()
