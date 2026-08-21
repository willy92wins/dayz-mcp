"""Upper bounds a mutation run walked straight through on 2026-08-21.

That run flipped 22 constants across 10 modules; 13 died against the suite and 9
survived. Every survivor was a CEILING, and the reason is the same in all nine: the
existing tests only ever pushed on the floor, so widening the ceiling changed no
observable behaviour. A bound with a test on one side is not pinned.

So each test here pairs the LAST ACCEPTED value with the FIRST REJECTED one. Moving
the bound in either direction breaks one half of the pair. Where a bound is repeated
across modules it is pinned once per module: a copy without its own test is exactly
how the ceilings drifted apart in the first place.
"""

from __future__ import annotations

import unittest

from dayz_mcp import daemon_contract
from dayz_mcp import daemon_policy_contract
from dayz_mcp import dayz_test_request
from dayz_mcp import loopback
from dayz_mcp import native_broker_protocol
from dayz_mcp import playbook_tool
from dayz_mcp import vehicle_trace


def _trace_args(**overrides: object) -> dict[str, object]:
    """A vehicle_trace arg set that passes every check except the one under test."""
    args: dict[str, object] = {
        "mode": "status",
        "trace_id": "0" * 32,
        "cursor": 0,
        "limit": 1,
        "sample_hz": 60,
        "max_samples": 2,
    }
    args.update(overrides)
    return args


def _minimal_trace(**overrides: object) -> dict[str, object]:
    """A trace complete enough that validate_trace reaches the checks under test.

    Built FROM the module's own REQUIRED_TRACE_FIELDS rather than from a hand-written
    list: validate_trace returns early on any missing required field, so a field added
    later would otherwise turn this fixture back into an early return and quietly stop
    exercising everything below it.
    """
    trace: dict[str, object] = dict.fromkeys(vehicle_trace.REQUIRED_TRACE_FIELDS)
    trace.update(
        {
            "schema": vehicle_trace.TRACE_SCHEMA,
            "mode": "read",
            "trace_id": "0" * 32,
            "active": False,
            "complete": True,
            "overflow": False,
            "stop_reason": "done",
            "sample_hz": 60,
        }
    )
    trace.update(overrides)
    return trace


def _nest(levels: int) -> object:
    """A dict chain `levels` deep with a string leaf at the bottom."""
    node: object = "leaf"
    for _ in range(levels):
        node = {"k": node}
    return node


class SampleHzCeilingTests(unittest.TestCase):
    """20..60 Hz, enforced in three separate places that must not drift apart."""

    def test_loopback_ingress_accepts_60_and_rejects_61(self) -> None:
        ok, _ = loopback.validate_command_args("vehicle_trace", _trace_args())
        self.assertTrue(ok, "60 Hz is the documented ceiling and must be accepted")
        ok, error = loopback.validate_command_args(
            "vehicle_trace", _trace_args(sample_hz=61)
        )
        self.assertFalse(ok)
        self.assertEqual(error, "bad_args")

    def test_loopback_ingress_accepts_20_and_rejects_19(self) -> None:
        ok, _ = loopback.validate_command_args("vehicle_trace", _trace_args(sample_hz=20))
        self.assertTrue(ok)
        ok, _ = loopback.validate_command_args("vehicle_trace", _trace_args(sample_hz=19))
        self.assertFalse(ok)

    def test_normalize_request_accepts_60_and_rejects_61(self) -> None:
        self.assertEqual(
            vehicle_trace.normalize_request("status", "0" * 32, 0, 1, 60, 2)["sample_hz"],
            60,
        )
        with self.assertRaises(ValueError) as caught:
            vehicle_trace.normalize_request("status", "0" * 32, 0, 1, 61, 2)
        self.assertEqual(str(caught.exception), "bad_sample_hz")

    def test_validate_trace_accepts_60_and_stops_on_61(self) -> None:
        def sample_hz_stops(value: object) -> bool:
            report = vehicle_trace.validate_trace(_minimal_trace(sample_hz=value))
            return any(
                check.get("id") == "sample_hz" and check.get("status") == "STOP"
                for check in report["checks"]
            )

        self.assertFalse(sample_hz_stops(60), "60 Hz must not raise a STOP")
        self.assertTrue(sample_hz_stops(61))


class LimitAndSampleCeilingTests(unittest.TestCase):
    """The two bounds the same run DID catch, kept next to the one it did not."""

    def test_limit_accepts_64_and_rejects_65(self) -> None:
        self.assertEqual(
            vehicle_trace.normalize_request("status", "0" * 32, 0, 64, 60, 2)["limit"], 64
        )
        with self.assertRaises(ValueError) as caught:
            vehicle_trace.normalize_request("status", "0" * 32, 0, 65, 60, 2)
        self.assertEqual(str(caught.exception), "bad_limit")

    def test_max_samples_accepts_8192_and_rejects_8193(self) -> None:
        self.assertEqual(
            vehicle_trace.normalize_request("status", "0" * 32, 0, 1, 60, 8192)["max_samples"],
            8192,
        )
        with self.assertRaises(ValueError) as caught:
            vehicle_trace.normalize_request("status", "0" * 32, 0, 1, 60, 8193)
        self.assertEqual(str(caught.exception), "bad_max_samples")


class PortRangeTests(unittest.TestCase):
    """1..65535. Before this file, the string 65535 appeared in zero test modules."""

    def test_highest_legal_port_is_accepted(self) -> None:
        self.assertTrue(daemon_contract.argv_targets_port(["--port", "65535"], 65535))

    def test_one_past_the_top_is_rejected(self) -> None:
        self.assertFalse(daemon_contract.argv_targets_port(["--port", "65536"], 65536))

    def test_an_ordinary_high_port_is_accepted(self) -> None:
        self.assertTrue(daemon_contract.argv_targets_port(["--port", "65531"], 65531))

    def test_zero_and_negative_are_rejected(self) -> None:
        self.assertFalse(daemon_contract.argv_targets_port(["--port", "0"], 0))
        self.assertFalse(daemon_contract.argv_targets_port(["--port", "-1"], -1))

    def test_lowest_legal_port_is_accepted(self) -> None:
        self.assertTrue(daemon_contract.argv_targets_port(["--port", "1"], 1))


class ValidTextTests(unittest.TestCase):
    """valid_text rejects control content, not just oversized content."""

    def test_embedded_nul_is_rejected(self) -> None:
        self.assertTrue(daemon_policy_contract.valid_text("ab"))
        self.assertFalse(daemon_policy_contract.valid_text("a\0b"))

    def test_lone_surrogate_is_rejected(self) -> None:
        self.assertFalse(daemon_policy_contract.valid_text("a\ud800b"))

    def test_length_bounds_are_pinned_on_both_sides(self) -> None:
        self.assertFalse(daemon_policy_contract.valid_text(""))
        self.assertTrue(daemon_policy_contract.valid_text("x" * 520))
        self.assertFalse(daemon_policy_contract.valid_text("x" * 521))


class UnicodeTreeDepthTests(unittest.TestCase):
    """Both guards cut at depth > 4, but they enter the recursion at different depths."""

    def test_request_tree_accepts_its_deepest_legal_nesting(self) -> None:
        self.assertTrue(dayz_test_request._valid_unicode_tree(_nest(3)))
        self.assertFalse(dayz_test_request._valid_unicode_tree(_nest(4)))

    def test_broker_tree_accepts_one_level_more_than_the_request_tree(self) -> None:
        self.assertTrue(native_broker_protocol._valid_unicode(_nest(4)))
        self.assertFalse(native_broker_protocol._valid_unicode(_nest(5)))

    def test_the_rejection_is_the_depth_and_not_the_leaf(self) -> None:
        """A too-deep tree whose every leaf is legal must still be rejected.

        Without this the depth cut can be widened and the tests stay green, because
        the leaf checks reject the fixture for an unrelated reason.
        """
        self.assertTrue(native_broker_protocol._valid_unicode("leaf"))
        self.assertTrue(native_broker_protocol._valid_unicode({"k": "leaf"}))
        self.assertFalse(native_broker_protocol._valid_unicode(_nest(5)))


class PlaybookToolLiteralTests(unittest.TestCase):
    """Names and notes the agent sees, pinned as literals rather than as constants."""

    def test_name_rejection_names_the_pattern_it_wants(self) -> None:
        with self.assertRaises(playbook_tool.ToolError) as caught:
            playbook_tool.resolve_playbook_path("Not A Name")
        message = str(caught.exception)
        self.assertIn("bad_args", message)
        self.assertIn("^[a-z][a-z0-9_]{0,31}$", message)
        self.assertIn("Not A Name", message)

    def test_name_bounds_are_pinned_on_both_sides(self) -> None:
        self.assertIsNotNone(playbook_tool.NAME_RE.fullmatch("a" * 32))
        self.assertIsNone(playbook_tool.NAME_RE.fullmatch("a" * 33))
        self.assertIsNone(playbook_tool.NAME_RE.fullmatch("1abc"))
        self.assertIsNone(playbook_tool.NAME_RE.fullmatch("Abc"))
        self.assertIsNotNone(playbook_tool.NAME_RE.fullmatch("a"))

    def test_draft_note_is_the_exact_sentence_the_agent_reads(self) -> None:
        payload = playbook_tool.attach_playbook_meta({}, "demo", {"status": "DRAFT"})
        self.assertEqual(payload["note"], "DRAFT playbook: verdict is advisory")

    def test_a_non_draft_playbook_carries_no_note(self) -> None:
        payload = playbook_tool.attach_playbook_meta({}, "demo", {"status": "FROZEN"})
        self.assertNotIn("note", payload)


if __name__ == "__main__":
    unittest.main()
