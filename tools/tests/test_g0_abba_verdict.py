"""Pure-logic tests for the G0 ABBA in-game diagnostic driver."""

from __future__ import annotations

import copy
import math
import unittest
from pathlib import Path

from dayz_mcp.loopback import validate_command_args

try:
    import g0_abba_gate as gate
except ModuleNotFoundError:
    gate = None


def sample(
    t: float,
    x: float,
    *,
    z: float = 0.0,
    control_active: bool = True,
    requested: float = 1.0,
    applied: float = 1.0,
    direction: tuple[float, float] = (0.0, 1.0),
    body_contact_count: int = 0,
    wheel_spin: float = 0.0,
    gear: int = 2,
) -> dict:
    return {
        "sequence": int(round(t * 20.0)),
        "monotonic_s": t,
        "sample_dt_s": 0.05,
        "position_x": x,
        "position_y": 0.0,
        "position_z": z,
        "velocity_x": 0.0,
        "velocity_y": 0.0,
        "velocity_z": 0.0,
        "direction_x": direction[0],
        "direction_y": 0.0,
        "direction_z": direction[1],
        "control_active": control_active,
        "throttle_requested": requested,
        "throttle_applied": applied,
        "wheel_angular_velocity_0": wheel_spin,
        "wheel_angular_velocity_1": wheel_spin,
        "wheel_angular_velocity_2": wheel_spin,
        "wheel_angular_velocity_3": wheel_spin,
        "wheel_count": 4,
        "wheels_present": 4,
        "engine_on": True,
        "gear": gear,
        "is_owner": True,
        "is_authority_owner": False,
        "net_id_low": 23,
        "net_id_high": 0,
        "body_contact_count": body_contact_count,
    }


def cell(
    site: str,
    delta_2s: float,
    delta_5s: float,
    *,
    direction: tuple[float, float] = (0.0, 1.0),
    body_contact_count: int = 0,
    wheel_spin: float = 0.0,
    owner_identity: str = "owner-1",
    trace_status: str = "PASS",
) -> dict:
    s0 = sample(10.0, 0.0, direction=direction)
    s2 = sample(
        12.0,
        delta_2s,
        direction=direction,
        body_contact_count=body_contact_count,
        wheel_spin=wheel_spin,
    )
    s5 = sample(
        15.0,
        delta_5s,
        direction=direction,
        body_contact_count=body_contact_count,
        wheel_spin=wheel_spin,
    )
    return {
        "site": site,
        "object_id": 1,
        "owner_identity": owner_identity,
        "net_id": [23, 0],
        "trace_id": "0123456789abcdef0123456789abcdef",
        "trace_owner_identity": owner_identity,
        "trace_net_id": [23, 0],
        "trace_gates": {"status": trace_status},
        "samples": [s0, s2, s5],
        "S0": s0,
        "S2": s2,
        "S5": s5,
        "deltas": {
            "delta_2s_3d": delta_2s,
            "delta_2s_xz": delta_2s,
            "delta_5s_3d": delta_5s,
            "delta_5s_xz": delta_5s,
        },
    }


class G0AbbaPureLogicTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertIsNotNone(gate, "g0_abba_gate.py does not exist yet")

    def test_raw_enqueue_normalizes_vehicle_trace_start_id_for_ingress(self) -> None:
        class CapturingDaemon:
            def __init__(self) -> None:
                self.tool = None
                self.args = None

            def run(self, tool, args, peer, timeout_s):
                self.tool = tool
                self.args = args
                return {"ok": True}

        logical_args = {
            "mode": "start",
            "trace_id": "",
            "cursor": 0,
            "limit": 1,
            "sample_hz": 20,
            "max_samples": 256,
            "timeout_s": 30.0,
        }
        daemon = CapturingDaemon()

        result = gate._logical_tool_call(
            daemon, "vehicle_trace", logical_args, "client"
        )

        self.assertEqual({"ok": True}, result)
        self.assertEqual("", logical_args["trace_id"])
        self.assertEqual("vehicle_trace", daemon.tool)
        self.assertRegex(daemon.args["trace_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(
            (True, None),
            validate_command_args("vehicle_trace", daemon.args),
        )

    def test_public_cell_records_sidecar_relative_to_verdict_directory(self) -> None:
        sidecar = Path.cwd() / "private-machine-path" / "verdict.json.cell1.samples.json"

        public = gate._public_cell({"samples_file": str(sidecar)})

        self.assertEqual("verdict.json.cell1.samples.json", public["samples_file"])

    def test_extracts_s0_s2_s5_at_first_samples_within_tolerance(self) -> None:
        rows = [
            sample(9.95, 0.0, control_active=False, requested=0.0),
            sample(10.00, 0.0, requested=0.999),
            sample(11.99, 0.8),
            sample(12.074, 1.2),
            sample(14.99, 3.0),
            sample(15.050, 4.0),
        ]

        selected = gate.extract_s0_s2_s5(rows)

        self.assertEqual("PASS", selected["status"])
        self.assertEqual(10.00, selected["S0"]["monotonic_s"])
        self.assertEqual(12.074, selected["S2"]["monotonic_s"])
        self.assertEqual(15.050, selected["S5"]["monotonic_s"])
        self.assertEqual(
            {
                "delta_2s_3d": 1.2,
                "delta_2s_xz": 1.2,
                "delta_5s_3d": 4.0,
                "delta_5s_xz": 4.0,
            },
            gate.cell_deltas(selected["S0"], selected["S2"], selected["S5"]),
        )

    def test_s2_after_tolerance_is_trace_setup_failed(self) -> None:
        rows = [sample(3.0, 0.0), sample(5.0750005, 1.2), sample(8.0, 4.0)]

        selected = gate.extract_s0_s2_s5(rows)

        self.assertEqual("TRACE_SETUP_FAILED", selected["status"])
        self.assertEqual("S2_OUTSIDE_TOLERANCE", selected["S2"]["absence_type"])
        self.assertEqual(5.0750005, selected["S2"]["observed_monotonic_s"])

    def test_s0_throttle_just_outside_literal_tolerance_is_absent(self) -> None:
        rows = [sample(1.0, 0.0, requested=0.9989995)]

        selected = gate.extract_s0_s2_s5(rows)

        self.assertEqual("H1_CONTROL_NOT_OBSERVABLE", selected["status"])

    def test_integral_trace_without_s0_is_control_not_observable(self) -> None:
        rows = [
            sample(1.0, 0.0, control_active=False),
            sample(2.0, 0.0, requested=0.5),
        ]

        selected = gate.extract_s0_s2_s5(rows)

        self.assertEqual("H1_CONTROL_NOT_OBSERVABLE", selected["status"])
        self.assertEqual("CONTROL_NOT_OBSERVABLE", selected["S0"]["absence_type"])
        self.assertEqual("NO_S0", selected["S2"]["absence_type"])
        self.assertEqual("NO_S0", selected["S5"]["absence_type"])

    def test_trace_quality_rejects_gap_and_sub_20hz_effective_rate(self) -> None:
        rows = [sample(0.0, 0.0), sample(0.05, 0.1), sample(0.13, 0.2)]

        result = gate.trace_sample_gate(rows, rows[0], rows[-1])

        self.assertEqual("TRACE_SETUP_FAILED", result["status"])
        self.assertEqual(0.08, result["max_sample_gap_s"])
        self.assertLess(result["effective_hz"], 20.0)

    def test_first_tree_row_isolates_h2_with_observed_contact(self) -> None:
        cells = [
            cell("CONTROL", 2.0, 4.0),
            cell("RED", 0.4, 0.8, body_contact_count=1),
            cell("RED", 0.5, 0.9, body_contact_count=1),
            cell("CONTROL", 2.2, 4.2),
        ]

        result = gate.verdict_tree(cells)

        self.assertEqual("H2_POSITION_ISOLATED_OBSERVED_MECHANISM", result["row"])
        self.assertEqual(
            "H2 aislada a nivel de posición. La mecánica concreta sigue abierta entre obstáculo, suelo y contacto; no tocar drivetrain.",
            result["verdict"],
        )

    def test_h2_observation_uses_the_whole_s0_to_s5_window(self) -> None:
        cells = [
            cell("CONTROL", 2.0, 4.0),
            cell("RED", 0.4, 0.8),
            cell("RED", 0.5, 0.9),
            cell("CONTROL", 2.2, 4.2),
        ]
        for red in cells[1:3]:
            red["samples"].insert(
                1,
                sample(11.0, 0.2, body_contact_count=1),
            )

        result = gate.verdict_tree(cells)

        self.assertEqual("H2_POSITION_ISOLATED_OBSERVED_MECHANISM", result["row"])

    def test_second_tree_row_is_current_g0_pass_when_all_cells_move(self) -> None:
        cells = [
            cell("CONTROL", 1.1, 3.0),
            cell("RED", 1.2, 3.1),
            cell("RED", 1.3, 3.2),
            cell("CONTROL", 1.4, 3.3),
        ]

        result = gate.verdict_tree(cells)

        self.assertEqual("NO_REPRODUCE_G0_PASS", result["row"])
        self.assertEqual(
            "El rojo antiguo no reproduce con el build actual; H1 histórica/build o command path anterior queda primera. G0 actual PASS, sin afirmar qué bytes produjeron el JSON antiguo.",
            result["verdict"],
        )

    def test_vertical_settle_is_not_shadowed_by_generic_h1_h4(self) -> None:
        cells = [
            cell("CONTROL", 0.2, 0.3),
            cell("RED", 0.2, 0.3),
            cell("RED", 0.2, 0.3),
            cell("CONTROL", 0.2, 0.3),
        ]
        for current in cells:
            current["deltas"]["delta_2s_xz"] = 0.01
            for row in current["samples"]:
                row["throttle_applied"] = 0.0

        result = gate.verdict_tree(cells)

        self.assertEqual("H5_SETTLE", result["row"])

    def test_replica_threshold_disagreement_is_entity_variance(self) -> None:
        cells = [
            cell("CONTROL", 1.2, 2.0),
            cell("RED", 0.4, 0.7),
            cell("RED", 1.2, 2.0),
            cell("CONTROL", 1.3, 2.1),
        ]

        result = gate.verdict_tree(cells)

        self.assertEqual("INCONCLUSIVE_ENTITY_VARIANCE", result["row"])
        self.assertEqual("replica_delta_verdict", result["field"])

    def test_direction_dot_below_point_99_is_setup_failed(self) -> None:
        just_below = 0.9899995
        cells = [
            cell("CONTROL", 1.2, 2.0, direction=(0.0, 1.0)),
            cell(
                "RED",
                1.2,
                2.0,
                direction=(math.sqrt(1.0 - just_below * just_below), just_below),
            ),
            cell("RED", 1.3, 2.1, direction=(0.0, 1.0)),
            cell("CONTROL", 1.3, 2.1, direction=(0.0, 1.0)),
        ]

        result = gate.comparability_gate(cells)

        self.assertEqual("SETUP_FAILED", result["status"])
        self.assertEqual("direction_xz_dot", result["field"])
        self.assertLess(result["minimum_dot_xz"], 0.99)

    def test_wheel_count_difference_is_entity_variance(self) -> None:
        cells = [
            cell("CONTROL", 1.2, 2.0),
            cell("RED", 1.2, 2.0),
            cell("RED", 1.3, 2.1),
            cell("CONTROL", 1.3, 2.1),
        ]
        cells[2] = copy.deepcopy(cells[2])
        cells[2]["S0"]["wheels_present"] = 3

        result = gate.comparability_gate(cells)

        self.assertEqual("INCONCLUSIVE_ENTITY_VARIANCE", result["status"])
        self.assertEqual("wheel_counts", result["field"])

    def test_missing_initial_wheel_count_is_setup_failed(self) -> None:
        cells = [
            cell("CONTROL", 1.2, 2.0),
            cell("RED", 1.2, 2.0),
            cell("RED", 1.3, 2.1),
            cell("CONTROL", 1.3, 2.1),
        ]
        del cells[2]["S0"]["wheel_count"]

        result = gate.comparability_gate(cells)

        self.assertEqual("SETUP_FAILED", result["status"])
        self.assertEqual("wheel_counts", result["field"])


if __name__ == "__main__":
    unittest.main()
