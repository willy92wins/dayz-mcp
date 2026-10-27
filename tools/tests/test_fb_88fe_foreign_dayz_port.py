"""fb-20261026-203149-88fe: a DayZ without a run record holds the default port.

The server of another box's run held 2302, 2304 and 27016. dayz_test_run on the
default port answered port_in_use_foreign with "pass another port=" and no
queue, also under on_busy="queue"; with port=2402 the box still counted that
DayZ as its occupant and queued until it exited. While such a DayZ runs a
launch is refused on any port, so the answer points to the wait for the box.
The on_busy="queue" test is Fb88feForeignDayzQueueTest in
test_fb_2223_box_queue_offer, next to the box FIFO harness it uses.
"""
from __future__ import annotations

import unittest

from dayz_mcp import server

_FOREIGN_SERVER = {"port": 2302, "mods": ["@DayZ_MCP"], "profiles": "profiles-130"}


def _box(*, foreign: list[dict[str, object]], runs: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "occupied": bool(foreign or runs),
        "runs": list(runs or []),
        "foreign": list(foreign),
        "ports_in_use": [2302, 2304, 27016],
        "foreign_ports": [2302, 2304],
        "foreign_ports_all": [2302, 2304, 27016],
        "queue": [],
        "scan_known": True,
        "port_scan_known": True,
        "port_scan_reason": None,
    }


def _failed(
    box: dict[str, object], port: int, caller_session: str | None = None
) -> dict[str, object]:
    return server._failed_active_run_result(
        project="DayZ_MCP", mode="all", box=box, started=0.0, caller_session=caller_session, port=port
    )


def _run(state: str, owner_session: str) -> dict[str, object]:
    return {
        "run_id": "run-x",
        "mod": "@M",
        "label": "l",
        "age_s": 1.0,
        "state": state,
        "owner_session": owner_session,
    }


def _refusals(
    box: dict[str, object], port: int, caller_session: str | None
) -> tuple[tuple[str, dict[str, object]], ...]:
    """The three answers that carry a held port: at launch, after a wait, and
    a box taken during the build."""
    return (
        (
            "launch",
            server._enrich_active_run_result(
                {"status": "failed"}, box, caller_session=caller_session, port=port
            ),
        ),
        ("wait", _failed(box, port, caller_session)),
        (
            "build",
            server._box_taken_result(
                {"status": "failed"},
                box,
                built=True,
                caller_session=caller_session,
                port=port,
            ),
        ),
    )


class ForeignDayzPortAnswerTest(unittest.TestCase):
    def test_a_port_held_while_a_foreign_dayz_occupies_the_box_points_to_the_wait(self) -> None:
        box = _box(foreign=[dict(_FOREIGN_SERVER)])
        for port in (2302, 27016):
            with self.subTest(port=port):
                failed = _failed(box, port)
                self.assertEqual(failed["error_code"], "active_run_exists")
                self.assertEqual(failed["reason"], "port_in_use_foreign")
                self.assertEqual(failed["port"], port)
                self.assertIn("another port= does not help yet", failed["hint"])
                self.assertIn('on_busy="queue"', failed["hint"])
                self.assertNotIn("box reads free", failed["hint"])
                offer = failed["queue_offer"]
                self.assertIsInstance(offer, dict)
                self.assertEqual(offer["retry"].get("on_busy"), "queue")
                self.assertIsNone(server._box_wait_cannot_help(box, port=port))

    def test_a_port_nobody_holds_keeps_the_busy_box_answer(self) -> None:
        failed = _failed(_box(foreign=[dict(_FOREIGN_SERVER)]), 2402)
        self.assertNotIn("reason", failed)
        self.assertIn("wait_for_box_s", failed["hint"])
        self.assertIsInstance(failed["queue_offer"], dict)

    def test_without_a_foreign_dayz_a_held_port_still_refuses_the_wait(self) -> None:
        run = {"run_id": "run-x", "mod": "@M", "label": "l", "age_s": 1.0, "owner_session": "other"}
        for label, box, words in (
            ("a free box", _box(foreign=[]), "box reads free"),
            ("a managed run", _box(foreign=[], runs=[run]), "after the box frees"),
        ):
            with self.subTest(label):
                failed = _failed(box, 2302)
                self.assertEqual(failed["reason"], "port_in_use_foreign")
                self.assertIn(words, failed["hint"])
                self.assertIn("pass another port=", failed["hint"])
                self.assertIsNone(failed["queue_offer"])
                self.assertEqual(
                    server._box_wait_cannot_help(box, port=2302), "port_in_use_foreign"
                )

    def test_a_managed_run_beside_the_foreign_dayz_still_points_to_the_wait(self) -> None:
        # Another session's run does not stop the wait, and the foreign DayZ
        # still refuses every port: the box comes first.
        box = _box(foreign=[dict(_FOREIGN_SERVER)], runs=[_run("RUNNING", "other")])
        failed = _failed(box, 2302)
        self.assertEqual(failed["reason"], "port_in_use_foreign")
        self.assertIn("another port= does not help yet", failed["hint"])
        offer = failed["queue_offer"]
        self.assertIsInstance(offer, dict)
        self.assertEqual(offer["retry"].get("on_busy"), "queue")
        self.assertIsNone(server._box_wait_cannot_help(box, port=2302))

    def test_what_else_stops_the_wait_keeps_the_managed_run_answer(self) -> None:
        # An unreconciled run, or the caller's own run, refuses the wait
        # whatever the port, so the answer must not send the caller to it.
        for label, run, caller, cannot in (
            ("an unreconciled run", _run("UNRECONCILED", "other"), None, "unreconciled"),
            ("the caller's own run", _run("RUNNING", "sess-mine"), "sess-mine", "own_run"),
        ):
            box = _box(foreign=[dict(_FOREIGN_SERVER)], runs=[run])
            self.assertEqual(
                server._box_wait_cannot_help(box, caller_session=caller, port=2302), cannot
            )
            for where, answer in _refusals(box, 2302, caller):
                with self.subTest(label, where=where):
                    self.assertEqual(answer["reason"], "port_in_use_foreign")
                    self.assertEqual(answer["port"], 2302)
                    self.assertIn("waiting for the box alone does not free the port", answer["hint"])
                    self.assertNotIn("does not help yet", answer["hint"])
                    self.assertIsNone(answer["queue_offer"])


if __name__ == "__main__":
    unittest.main()
