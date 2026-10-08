"""Vehicle-testing doc contract (inbox 33cd).

docs/VEHICLE_TESTING.md used to carry a universal claim -- the server's
replica of a client-owned car "never even moves" from the spawn point --
while its test ladder never called `vehicle_enter`, so the claim's own
evidence was client-get-in-only. The doc now scopes that claim to the runs
that measured it, adds a server-side ladder (fixture -> teleport ->
`vehicle_enter` -> `vehicle_get_in_client` -> engine/control) whose final
step compares server- against client-side positions instead of assuming
replication, and states that `vehicle_release` clears trace/control without
dismounting anybody.

Ground truth is the document text plus the bridge sources the doc cites
(no DayZ, no daemon, no writes): section headers anchor the regions, and
line numbers of the doc itself are never pinned -- the prose moves under
active editing, the step verbs and the cited symbols do not.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

REPO = Path(
    os.environ.get("DAYZ_MCP_WATCHDOG_REPO")
    or Path(__file__).resolve().parents[2]
)
DOC = REPO / "docs" / "VEHICLE_TESTING.md"
CLIENT_BRIDGE = REPO / "addon" / "scripts" / "5_Mission" / "MCPClientBridge.c"

_LADDER_HEADER = "## Server-side ladder"
_EXIT_HEADER = "## The exit reality"

# The recipe order the doc must teach: fixture, teleport, server enter,
# client enter, then the engine/control leg.
_STEP_VERBS = (
    "world_spawn",
    "player_teleport",
    "vehicle_enter",
    "vehicle_get_in_client",
    "engine_set",
)


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _section(text: str, header: str) -> str:
    m = re.search(
        rf"^{re.escape(header)}.*?(?=^## )", text, re.S | re.M
    )
    assert m is not None, f"section header lost: {header}"
    return m.group(0)


def _numbered_items(section: str) -> list[str]:
    """The bodies of the section's markdown ordered list, in order.

    Continuation lines are indented, so a top-level ``N. `` anchor cannot
    fire inside an item.
    """
    return re.findall(r"^\d+\. \S.*?(?=^\d+\. \S|^\Z)", section, re.S | re.M)


def _first_command(item: str) -> str | None:
    """First backticked verb of a step, args allowed ("engine_set start")."""
    for span in re.findall(r"`([^`]+)`", item):
        m = re.match(r"[a-z][a-z_0-9]*", span)
        if m and (m.end() == len(span) or span[m.end()] == " "):
            return m.group(0)
    return None


class ServerLadderOrderTest(unittest.TestCase):
    """The server-side ladder exists and teaches the steps in working order.

    A reader who follows the ladder out of order seats the client without a
    server crew (the old ladder's blind spot) or commands a car the client
    does not own; the order is the contract, so it is pinned verb by verb.
    """

    def test_ladder_section_exists_with_all_steps(self) -> None:
        items = _numbered_items(_section(_doc(), _LADDER_HEADER))
        self.assertGreaterEqual(
            len(items), 5,
            "the server-side ladder lost steps; restore the fixture -> "
            "teleport -> vehicle_enter -> vehicle_get_in_client -> "
            "engine/control sequence")

    def test_ladder_steps_run_in_recipe_order(self) -> None:
        items = _numbered_items(_section(_doc(), _LADDER_HEADER))
        verbs = [v for v in (_first_command(i) for i in items) if v]
        self.assertEqual(
            verbs[: len(_STEP_VERBS)], list(_STEP_VERBS),
            f"the server-side ladder teaches {_STEP_VERBS} in that order; "
            f"it now starts {verbs[: len(_STEP_VERBS)]}; "
            "the server enter (`vehicle_enter`) must precede the client "
            "get-in, and both must precede engine/control")

    def test_fixture_step_prepares_the_registered_object(self) -> None:
        first, *_ = _numbered_items(_section(_doc(), _LADDER_HEADER))
        self.assertIn(
            "vehicle_prepare_fixture", first,
            "the ladder's fixture step must end in vehicle_prepare_fixture "
            "(mode: object_at): every later step and the teardown address "
            "the car through that registered in-session id")
        self.assertLess(
            first.index("`world_spawn`"), first.index("`vehicle_prepare_fixture`"),
            "the fixture is spawned before it is prepared")

    def test_control_leg_and_comparison_close_the_ladder(self) -> None:
        section = _section(_doc(), _LADDER_HEADER)
        self.assertIn(
            "vehicle_control", section,
            "the ladder must reach the traced vehicle_control leg")
        self.assertIn(
            "pos_real", section,
            "the comparison step must name the client-side read it trusts "
            "(vehicle_telemetry pos_real / vehicle_trace), not 'compare "
            "positions' left undefined")
        self.assertIn(
            "entities_query", section,
            "the comparison step must name the server-side read "
            "(entities_query / object_inspect)")


class UniversalReplicaClaimDocsTest(unittest.TestCase):
    """The old universal claim stays gone, and what replaced it stays labeled.

    The claim the doc used to make -- the server replica of a client-owned
    car never moves from the spawn point -- was measured on client-get-in-only
    runs. The doc must keep the observation bound to its run, never
    re-generalized, and the ladder's comparison must be recorded per run.
    """

    def test_universal_never_moves_claim_is_gone(self) -> None:
        doc = _doc()
        self.assertNotIn(
            "never even moves", doc,
            "the universal frozen-replica claim is back; state the run and "
            "the client-get-in-only configuration it was measured on instead")

    def test_scoped_claim_names_run_and_configuration(self) -> None:
        exit_section = _section(_doc(), _EXIT_HEADER)
        self.assertIn(
            "fb-20260824-133301-ecf5", exit_section,
            "the replica observation lost its run id; an unlabeled "
            "observation drifts back into a universal claim")
        self.assertIn(
            "no `vehicle_enter`", exit_section,
            "the replica observation must name its configuration "
            "(client-side get-in, no server crew enter)")

    def test_comparison_is_recorded_with_run_and_version(self) -> None:
        section = _section(_doc(), _LADDER_HEADER)
        self.assertIn(
            "run id", section,
            "the ladder's position comparison must be recorded with the "
            "run id, or the next reader cannot tell which configuration "
            "the tracks belong to")
        self.assertIn(
            "version", section,
            "the ladder's position comparison must be recorded with the "
            "bridge/game version")


class VehicleReleaseNotGetOutDocsTest(unittest.TestCase):
    """`vehicle_release` clears trace/control; the registered-id delete ejects.

    Ground truth for the cite: DispatchVehicleRelease (MCPClientBridge.c)
    only aborts the trace and clears the drive control state -- the doc must
    not promise a dismount the bridge does not perform, and the teardown
    must stay the object_delete of the registered fixture id.
    """

    def test_doc_states_release_scoped_to_trace_and_control(self) -> None:
        doc = _doc()
        m = re.search(
            r"only (?:aborts the\s+trace and\s+clears|clears)\s+"
            r"(?:the )?trace/control[^.]*`MCPClientBridge\.c:\d+-\d+`|"
            r"`MCPClientBridge\.c:\d+-\d+`[^.]*not a get-out",
            doc, re.S)
        self.assertIsNotNone(
            m,
            "the release clause lost its scope or its cite: the doc must "
            "say vehicle_release only clears trace/control "
            "(an MCPClientBridge.c line range) and is not a get-out")
        self.assertIn(
            "not a get-out", doc,
            "the doc must state outright that vehicle_release is not a "
            "get-out")

    def test_cited_release_lines_match_the_bridge(self) -> None:
        if not CLIENT_BRIDGE.is_file():
            self.skipTest("addon/ absent (sparse public clone)")
        # The range comes from the doc's own cite, so a bridge edit that
        # moves the release body fails here until the cite is re-anchored.
        cites = sorted({(int(a), int(b)) for a, b in re.findall(
            r"`MCPClientBridge\.c:(\d+)-(\d+)`", _doc())})
        self.assertTrue(cites, "the doc lost its MCPClientBridge.c release cite")
        lines = CLIENT_BRIDGE.read_text(encoding="utf-8").splitlines()
        for start, end in cites:
            body = "\n".join(lines[start - 1:end])
            self.assertIn(
                "DispatchVehicleRelease", body,
                f"the cited MCPClientBridge.c:{start}-{end} no longer holds "
                "DispatchVehicleRelease; re-anchor the doc's release cite")
            self.assertIn(
                "MCPVehicleTrace.Abort", body,
                "DispatchVehicleRelease no longer aborts the trace at the "
                f"cited lines {start}-{end}")
            self.assertIn(
                "MCPCarDrive.Clear", body,
                "DispatchVehicleRelease no longer clears the drive control "
                f"state at the cited lines {start}-{end}")

    def test_teardown_stays_the_registered_id_delete(self) -> None:
        section = _section(_doc(), _LADDER_HEADER)
        self.assertIn(
            "`object_delete` of the registered fixture id", section,
            "the server ladder's teardown must stay object_delete of the "
            "registered in-session fixture id -- the release does not "
            "eject anybody")


if __name__ == "__main__":
    unittest.main()
