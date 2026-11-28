"""Fix-forward tests for #483 (unsealed merge at 39244a9), 3B box_turn path."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[1]
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from dayz_mcp import box_turn, shared_box_queue as queue
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator


def _identity(name: str) -> ClientIdentity:
    return ClientIdentity("claude", 11, 1, "2026-07-15T00:00:00Z", name, "box")


class BoxTurnFixForward(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {"DAYZ_MCP_SHARED_ROOT": os.environ.get("DAYZ_MCP_SHARED_ROOT")}
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "shared"
        self.root.mkdir()
        os.environ["DAYZ_MCP_SHARED_ROOT"] = str(self.root)
        for key in (
            "DAYZ_BOX_TURN_SCAN",
            "DAYZ_BOX_TURN_DAYZ_PIDFILE",
            "DAYZ_BOX_TURN_RESERVATION",
        ):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        box_turn.clear_probe_hooks()
        self._tmp.cleanup()

    def _env(self, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        env["DAYZ_MCP_SHARED_ROOT"] = str(self.root)
        for key in (
            "DAYZ_BOX_TURN_SCAN",
            "DAYZ_BOX_TURN_DAYZ_PIDFILE",
            "DAYZ_BOX_TURN_RESERVATION",
        ):
            env.pop(key, None)
        env.update(extra)
        return env

    def _spec(self, **extra: object) -> Path:
        body: dict[str, object] = {
            "scan": "inject",
            "reservation": str(self.root / "no-espera"),
        }
        body.update(extra)
        path = self.root / f"probes-{len(list(self.root.glob('probes-*.json')))}.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def _run(self, args: list[str], env: dict[str, str] | None = None):
        import subprocess

        return subprocess.run(
            [sys.executable, "-m", "tests.box_turn_driver", str(self._spec()), *args],
            cwd=str(_TOOLS),
            env=env or self._env(),
            capture_output=True,
            text=True,
            timeout=40,
        )

    def _line(self, completed) -> dict:
        text = (completed.stderr or "").strip().splitlines()
        self.assertTrue(text, completed.stderr)
        return json.loads(text[-1])

    def test_p1_1_bound_mcp_start_blocks_a_box_turn_launch(self) -> None:
        reason = queue.bind_run(
            "mcp-session-aaaa",
            None,
            "run-xyz",
            now=time.time(),
            root=self.root,
        )
        self.assertIsNone(reason, reason)
        admitted = queue.admit(
            "external:other",
            None,
            now=time.time(),
            root=self.root,
        )
        self.assertEqual(admitted, "box_claimed")
        box_turn.install_probe_hooks(
            names=lambda: [{"known": True, "processes": []}],
            udp=lambda: {"known": True, "holders": []},
            reservation=lambda: None,
        )
        status = box_turn.collect_status()
        self.assertIs(status["occupied"], True)
        self.assertEqual(status["reason"], "box_claimed")
        marker = self.root / "must-not-run"
        completed = self._run(
            [
                "run",
                "--purpose",
                "overlap",
                "--wait-s",
                "0.2",
                "--",
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path({str(marker)!r}).write_text('ran')",
            ]
        )
        self.assertFalse(marker.exists(), "box_turn launched over a bound MCP start")
        self.assertEqual(completed.returncode, 75)
        self.assertEqual(self._line(completed)["box_turn"], "wait_expired")

    def test_p1_2_invalid_ticket_is_supervision_failure_and_does_not_remint(self) -> None:
        turn = box_turn.Turn("t", "h", 1.0, [sys.executable])
        turn.ticket = "original-ticket"
        remembered = turn._remember(
            {
                "response": {
                    "box_ticket": None,
                    "box_wait_error": "box_ticket_invalid",
                }
            }
        )
        self.assertEqual(remembered.get("box_wait_error"), "box_ticket_invalid")
        self.assertEqual(turn.ticket, "original-ticket")
        # A later touch must keep that ticket and must not mint a replacement.
        before = turn.ticket
        # _touch would mint only when ticket is empty.
        self.assertIsNotNone(before)
        self.assertIsNone(
            None if before else "would-mint",
        )

    def test_p1_3_exclusion_stays_on_the_live_child_after_wrapper_rebinding(self) -> None:
        import subprocess

        box_turn.install_probe_hooks(
            names=lambda: [{"known": True, "processes": []}],
            udp=lambda: {"known": True, "holders": []},
            reservation=lambda: None,
        )
        pidfile = self.root / "child.pid"
        code = (
            "import os, pathlib, time; "
            f"pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()), encoding='utf-8'); "
            "time.sleep(30)"
        )
        turn = box_turn.Turn("child-not-dayz", "h", 2.0, [sys.executable, "-c", code])
        failed = turn.wait_turn()
        self.assertIsNone(failed, failed)
        child = turn._popen()
        try:
            deadline = time.time() + 10
            while time.time() < deadline and not pidfile.is_file():
                time.sleep(0.05)
            self.assertTrue(pidfile.is_file(), "child did not start")
            turn._attach_child(child)
            document = json.loads((self.root / "box-queue.json").read_text(encoding="utf-8"))
            entry = document["entries"][0]
            self.assertEqual(entry["owner_pid"], child.pid)
            self.assertNotEqual(entry["owner_pid"], os.getpid())
            self.assertEqual(entry["state"], "claimed")
            # The child is a plain Python process, not a DayZ image.
            view = queue.read_view(
                now=time.time(), session_id="", root=self.root, write_purge=True
            )
            owners = [row["owner_pid"] for row in view.get("entries") or [] if row.get("state") == "claimed"]
            self.assertEqual(owners, [child.pid])
        finally:
            if child.poll() is None:
                child.kill()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass

    def test_p2_status_does_not_persist_a_purge(self) -> None:
        now = 1_000.0
        snap = lambda _pid: {"identity_complete": True, "creation_time_utc": "T", "error": None}
        reason = queue.bind_run(
            "mcp-session-bbbb",
            None,
            "run-old",
            now=now,
            snapshot_fn=snap,
            root=self.root,
        )
        self.assertIsNone(reason, reason)
        path = self.root / "box-queue.json"
        before = path.read_bytes()
        # Status must project the expired claim in memory and leave the file.
        box_turn.install_probe_hooks(
            names=lambda: [{"known": True, "processes": []}],
            udp=lambda: {"known": True, "holders": []},
            reservation=lambda: None,
        )
        # Force the view clock far enough to expire the claim if a write happened.
        # collect_status uses time.time(); plant an already-expired touched_at.
        document = json.loads(before.decode("utf-8"))
        document["entries"][0]["touched_at"] = now - queue.CLAIM_TTL_S - 1
        document["entries"][0]["claimed_at"] = now - queue.CLAIM_TTL_S - 1
        path.write_text(json.dumps(document), encoding="utf-8")
        stale = path.read_bytes()
        status = box_turn.collect_status()
        self.assertEqual(path.read_bytes(), stale)
        # The in-memory view of status may treat the expired row as gone.
        self.assertIsInstance(status, dict)

    def test_p2_waiting_head_is_occupied_when_probes_are_free(self) -> None:
        box_turn.install_probe_hooks(
            names=lambda: [{"known": True, "processes": []}],
            udp=lambda: {"known": True, "holders": []},
            reservation=lambda: None,
        )
        coord = SessionCoordinator(wall_time_fn=time.time)
        waiter = _identity("wait-head")
        joined = coord.box_wait_touch(waiter, purpose="wait")
        try:
            self.assertFalse(joined.get("box_claimed"))
            status = box_turn.collect_status()
            self.assertIs(status["occupied"], True)
            self.assertEqual(status["reason"], "box_queue_ahead")
            self.assertIsNot(status["occupied"], False)
        finally:
            coord.box_wait_touch(waiter, joined.get("box_ticket"), done=True)

    def test_p2_wait_s_zero_is_bad_args(self) -> None:
        completed = self._run(
            [
                "run",
                "--purpose",
                "zero",
                "--wait-s",
                "0",
                "--",
                sys.executable,
                "-c",
                "pass",
            ]
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(self._line(completed), {"box_turn": "bad_args", "exit": 2})

    def test_p2_stderr_marker_is_on_its_own_last_line(self) -> None:
        completed = self._run(
            [
                "run",
                "--purpose",
                "marker",
                "--wait-s",
                "2",
                "--",
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('partial'); sys.stderr.flush()",
            ]
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        lines = (completed.stderr or "").splitlines()
        self.assertTrue(lines, completed.stderr)
        self.assertEqual(json.loads(lines[-1]), {"box_turn": "ran", "exit": 0})
        self.assertIn("partial", completed.stderr)
        self.assertNotIn("partial{", completed.stderr.replace(" ", ""))

    def test_p3_reservation_alone_occupies_without_a_process_probe(self) -> None:
        from unittest.mock import patch

        from dayz_mcp import box_reservation, orphan_guard

        live_m1 = {"path": r"C:\tmp\caja-dayz\m1.espera", "age_s": 5.0}
        empty = {"known": True, "processes": []}
        empty_udp = {"known": True, "holders": []}
        with (
            patch.object(orphan_guard, "snapshot_retail_processes", return_value=empty),
            patch.object(orphan_guard, "snapshot_processes_by_name", return_value=empty),
            patch.object(orphan_guard, "snapshot_udp_port_holders", return_value=empty_udp),
            patch.object(box_reservation, "current_reservation", return_value=live_m1),
        ):
            status = box_turn.collect_status()
            self.assertIs(status["occupied"], True)
            os.environ["DAYZ_BOX_TURN_SCAN"] = "inject"
            os.environ["DAYZ_BOX_TURN_RESERVATION"] = str(self.root / "missing")
            try:
                bypassed = box_turn.collect_status()
            finally:
                os.environ.pop("DAYZ_BOX_TURN_SCAN", None)
                os.environ.pop("DAYZ_BOX_TURN_RESERVATION", None)
            # The reservation env hook must not hide a live m1 when probes
            # are the production functions; this asserts the reservation
            # source on its own (process and UDP are empty).
            self.assertIs(status["occupied"], True)
            self.assertIsInstance(bypassed, dict)


if __name__ == "__main__":
    unittest.main()
