"""Phase 3B: box_turn status and run, with a temporary shared root."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[1]
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator


_ESPERA = (
    "import os, pathlib, sys, time\n"
    "pathlib.Path(os.environ['READY']).write_text('up', encoding='utf-8')\n"
    "limit = time.time() + 30\n"
    "while time.time() < limit:\n"
    "    if pathlib.Path(os.environ['RELEASE']).exists():\n"
    "        raise SystemExit(0)\n"
    "    time.sleep(0.05)\n"
    "raise SystemExit(3)\n"
)

_DAYZ_CHILD = (
    "import os, pathlib, time\n"
    "pathlib.Path(os.environ['CHILD_PIDFILE']).write_text("
    "str(os.getpid()), encoding='utf-8')\n"
    "time.sleep(60)\n"
)


def _identity(name: str) -> ClientIdentity:
    return ClientIdentity(
        "claude", 11, 1, "2026-07-15T00:00:00Z", name, "box"
    )


class BoxTurnF3(unittest.TestCase):
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
        from dayz_mcp import box_turn

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

    def _run(
        self,
        args: list[str],
        env: dict[str, str] | None = None,
        spec: Path | None = None,
    ) -> subprocess.CompletedProcess:
        spec_path = spec or self._spec()
        return subprocess.run(
            [sys.executable, "-m", "tests.box_turn_driver", str(spec_path), *args],
            cwd=str(_TOOLS),
            env=env or self._env(),
            capture_output=True,
            text=True,
            timeout=40,
        )

    def _line(self, completed: subprocess.CompletedProcess) -> dict:
        text = (completed.stderr or "").strip().splitlines()
        self.assertTrue(text, completed.stderr)
        return json.loads(text[-1])

    def test_f3_neg3_missing_or_empty_purpose_writes_nothing(self) -> None:
        missing = self._run(["run", "--", sys.executable, "-c", "print('no')"])
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stdout, "")
        self.assertEqual(self._line(missing), {"box_turn": "bad_args", "exit": 2})
        empty = self._run(
            ["run", "--purpose", "  ", "--", sys.executable, "-c", "print('no')"]
        )
        self.assertEqual(empty.returncode, 2)
        self.assertEqual(empty.stdout, "")
        self.assertEqual(self._line(empty), {"box_turn": "bad_args", "exit": 2})
        absent = self._run(["run", "--purpose", "", "--", sys.executable, "-c", "print(1)"])
        self.assertEqual(absent.returncode, 2)
        self.assertEqual(self._line(absent), {"box_turn": "bad_args", "exit": 2})
        self.assertFalse((self.root / "box-queue.json").exists())

    def test_f3_exit_command_75_is_ran_and_timeout_is_wait_expired(self) -> None:
        ran = self._run(
            [
                "run",
                "--purpose",
                "exit75",
                "--wait-s",
                "2",
                "--",
                sys.executable,
                "-c",
                "import sys; sys.exit(75)",
            ]
        )
        self.assertEqual(ran.returncode, 75)
        self.assertEqual(self._line(ran), {"box_turn": "ran", "exit": 75})

        owner = _identity("mcp-blocker")
        coord = SessionCoordinator(wall_time_fn=time.time)
        held = coord.box_wait_touch(owner, claim=True, purpose="holds")
        self.assertTrue(held.get("box_claimed"))
        marker = self.root / "must-not-run"
        try:
            expired = self._run(
                [
                    "run",
                    "--purpose",
                    "late",
                    "--wait-s",
                    "0.4",
                    "--",
                    sys.executable,
                    "-c",
                    f"import pathlib; pathlib.Path({str(marker)!r}).write_text('x')",
                ]
            )
        finally:
            coord.box_wait_touch(owner, held.get("box_ticket"), done=True)
        self.assertEqual(expired.returncode, 75)
        self.assertEqual(self._line(expired), {"box_turn": "wait_expired", "exit": 75})
        self.assertFalse(marker.exists())

    def test_f3_pos4_tcp_listen_is_free_reservation_or_unknown_occupies(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        try:
            free = self._run(["status", "--json"])
        finally:
            sock.close()
        self.assertEqual(free.returncode, 0)
        payload = json.loads(free.stdout)
        self.assertIs(payload["occupied"], False)
        self.assertIsNone(payload["reason"])

        reservation = self.root / "m1.espera"
        reservation.write_text("m1", encoding="utf-8")
        held = self._run(
            ["status", "--json"],
            spec=self._spec(reservation=str(reservation)),
        )
        self.assertEqual(held.returncode, 0)
        held_payload = json.loads(held.stdout)
        self.assertIs(held_payload["occupied"], True)
        self.assertEqual(held_payload["reason"], "box_reserved")

        unknown = self._run(
            ["status", "--json"],
            spec=self._spec(scan="unknown"),
        )
        self.assertEqual(unknown.returncode, 0)
        unknown_payload = json.loads(unknown.stdout)
        self.assertIs(unknown_payload["occupied"], True)
        self.assertEqual(unknown_payload["reason"], "scan_unknown")

    def test_f3_pos3_waits_for_mcp_run_then_occupies_as_foreign_holder(self) -> None:
        from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore
        from dayz_mcp.runtime_state import RuntimePaths
        from tests.process_lifecycle_helpers import (
            AuditSink,
            FakeGuard,
            FakeLauncher,
            process,
            snapshot,
        )
        from tests.steam_helpers import FakeSteamGate

        owner = _identity("instance-mcp")
        other = _identity("instance-other")
        coord = SessionCoordinator(wall_time_fn=time.time)
        held = coord.box_wait_touch(owner, claim=True, purpose="mcp")
        self.assertTrue(held.get("box_claimed"))

        ready = self.root / "ready"
        release = self.root / "release"
        spec = self._spec()
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.box_turn_driver",
                str(spec),
                "run",
                "--purpose",
                "t",
                "--holder",
                "hb",
                "--wait-s",
                "20",
                "--",
                sys.executable,
                "-c",
                _ESPERA,
            ],
            cwd=str(_TOOLS),
            env=self._env(READY=str(ready), RELEASE=str(release)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            time.sleep(0.6)
            self.assertFalse(ready.exists(), "box_turn ran while the MCP claim was held")
            coord.box_wait_touch(owner, held.get("box_ticket"), done=True)
            held = None
            deadline = time.time() + 15
            while time.time() < deadline and not ready.exists():
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
            self.assertTrue(ready.exists(), "box_turn did not start after the MCP run ended")

            self.assertTrue(coord.box_queue_occupies(other.session_id))
            foreign = coord.box_external_holder()
            self.assertIsNotNone(foreign)
            self.assertEqual(foreign["holder"], "hb")
            self.assertEqual(foreign["purpose"], "t")
            self.assertEqual(foreign["instance"], "external")
            self.assertIsNone(foreign["run_id"])

            waiting = coord.box_wait_touch(other, purpose="queued-run")
            self.assertIs(waiting.get("box_claimed"), False)
            self.assertGreater(int(waiting.get("box_position") or 0), 1)

            game = self.root / "DayZ"
            game.mkdir()
            (game / "DayZDiag_x64.exe").write_bytes(b"")
            paths = RuntimePaths(
                self.root / "rt",
                self.root / "rt" / "audit",
                self.root / "rt" / "c.json",
                self.root / "rt" / "r.json",
            )
            launcher = FakeLauncher()
            guard = FakeGuard()
            launched = process(launcher.pid)
            guard.snapshots[launched.pid] = snapshot(launched)
            lifecycle = ProcessLifecycle(
                steam_gate=FakeSteamGate(),
                coordinator=coord,
                manifest=RunManifestStore(paths),
                audit=AuditSink(),
                guard=guard,
                retail_probe=lambda: {"known": True, "processes": []},
                diag_probe=lambda: {"known": True, "processes": []},
                port_probe=lambda: {"known": True, "holders": []},
                game_path=game,
                launcher=launcher,
                id_fn=lambda: "run-other",
            )
            _status, lease = coord.acquire(other, "run")
            refused = lifecycle.start_run(
                other,
                lease["lease_token"],
                {
                    "argv": [str(game / "DayZDiag_x64.exe"), "-mission=test"],
                    "cwd": str(game),
                    "role": "client",
                    "window_style": "normal",
                    "label": "l",
                    "mod": "@M",
                    "profiles": "p",
                    "mission": "m",
                },
            )
            self.assertFalse(refused.get("ok"), refused)
            self.assertEqual(len(launcher.calls), 0)
            coord.box_wait_touch(other, waiting.get("box_ticket"), done=True)
            coord.release(other, lease["lease_token"])

            release.write_text("go", encoding="utf-8")
            stdout, stderr = proc.communicate(timeout=20)
        finally:
            if held is not None:
                coord.box_wait_touch(owner, held.get("box_ticket"), done=True)
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(json.loads(stderr.strip().splitlines()[-1]), {"box_turn": "ran", "exit": 0})
        self.assertEqual(stdout, "")
        free = self._run(["status", "--json"])
        self.assertEqual(json.loads(free.stdout)["occupied"], False)
        self.assertEqual(json.loads(free.stdout)["queue"], [])

    def test_f3_neg4_kill_purges_owner_and_live_child_keeps_the_box(self) -> None:
        pidfile = self.root / "dayz-pids"
        spec = self._spec(pidfile=str(pidfile))
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.box_turn_driver",
                str(spec),
                "run",
                "--purpose",
                "child",
                "--holder",
                "hb",
                "--wait-s",
                "10",
                "--",
                sys.executable,
                "-c",
                _DAYZ_CHILD,
            ],
            cwd=str(_TOOLS),
            env=self._env(CHILD_PIDFILE=str(pidfile)),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        child = 0
        try:
            deadline = time.time() + 15
            while time.time() < deadline and not pidfile.is_file():
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
            self.assertTrue(pidfile.is_file(), "the child did not publish its pid")
            child = int(pidfile.read_text(encoding="utf-8").strip())
            proc.kill()
            proc.wait(timeout=10)
            self.assertTrue(_alive(child), "the DayZ child died with box_turn")
            status = self._run(
                ["status", "--json"],
                spec=self._spec(pidfile=str(pidfile)),
            )
            payload = json.loads(status.stdout)
            self.assertIs(payload["occupied"], True)
            self.assertEqual(payload["reason"], "dayz")
            self.assertEqual(payload["queue"], [])
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
            if child:
                subprocess.run(
                    ["taskkill", "/PID", str(child), "/F"],
                    capture_output=True,
                )
        gone = self._run(
            ["status", "--json"],
            env=self._env(CHILD_PIDFILE=str(pidfile)),
            spec=self._spec(pidfile=str(pidfile)),
        )
        self.assertIs(json.loads(gone.stdout)["occupied"], False)

    def test_p1_1_scan_unknown_exits_70_without_waiting(self) -> None:
        started = time.monotonic()
        proc = self._run(
            [
                "run",
                "--purpose",
                "t",
                "--wait-s",
                "5",
                "--",
                sys.executable,
                "-c",
                "pass",
            ],
            spec=self._spec(scan="unknown"),
        )
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertEqual(proc.returncode, 70)
        self.assertEqual(self._line(proc), {"box_turn": "unreadable", "exit": 70})
        self.assertFalse((self.root / "box-queue.json").exists())

        (self.root / "box-queue.json").write_text("{", encoding="utf-8")
        broken = self._run(
            ["run", "--purpose", "t", "--wait-s", "5", "--", sys.executable, "-c", "pass"],
        )
        self.assertEqual(broken.returncode, 70)
        self.assertEqual(self._line(broken), {"box_turn": "unreadable", "exit": 70})

    def test_p1_2_command_stdio_is_inherited(self) -> None:
        proc = self._run(
            [
                "run",
                "--purpose",
                "out",
                "--wait-s",
                "2",
                "--",
                sys.executable,
                "-c",
                "import sys; print('CMD_STDOUT'); print('CMD_STDERR', file=sys.stderr)",
            ],
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("CMD_STDOUT", proc.stdout)
        self.assertIn("CMD_STDERR", proc.stderr)

    def test_p1_3_ctrl_c_does_not_free_the_box_under_a_live_child(self) -> None:
        import signal
        import threading

        from dayz_mcp import box_turn

        pidfile = self.root / "child.pid"
        box_turn.install_probe_hooks(
            names=lambda: [{"known": True, "processes": []}],
            udp=lambda: {"known": True, "holders": []},
            reservation=lambda: None,
        )
        code = (
            "import os, pathlib, time; "
            f"pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()), encoding='utf-8'); "
            "time.sleep(30)"
        )

        def send_sigint() -> None:
            deadline = time.time() + 10
            while time.time() < deadline and not pidfile.is_file():
                time.sleep(0.05)
            signal.raise_signal(signal.SIGINT)

        threading.Thread(target=send_sigint, daemon=True).start()
        child_pid = 0
        try:
            try:
                box_turn.main(
                    [
                        "run",
                        "--purpose",
                        "ctrlc",
                        "--wait-s",
                        "5",
                        "--",
                        sys.executable,
                        "-c",
                        code,
                    ]
                )
            except KeyboardInterrupt:
                pass
            child_pid = int(pidfile.read_text(encoding="utf-8").strip())
            alive = _alive(child_pid)
            status = box_turn.collect_status()
            self.assertFalse(alive and status["occupied"] is False)
        finally:
            box_turn.clear_probe_hooks()
            if child_pid:
                subprocess.run(
                    ["taskkill", "/PID", str(child_pid), "/F"],
                    capture_output=True,
                )

    def test_p1_4_probe_env_cannot_hide_a_live_dayz(self) -> None:
        from unittest.mock import patch

        from dayz_mcp import box_reservation, box_turn, orphan_guard

        live_dayz = {"known": True, "processes": [{"pid": 4242, "name": "DayZDiag_x64.exe"}]}
        live_udp = {"known": True, "holders": [{"port": 2302, "pid": 4242, "name": "DayZDiag_x64.exe"}]}
        live_m1 = {"path": r"C:\tmp\caja-dayz\m1.espera", "age_s": 5.0}
        with (
            patch.object(orphan_guard, "snapshot_retail_processes", return_value={"known": True, "processes": []}),
            patch.object(orphan_guard, "snapshot_processes_by_name", return_value=live_dayz),
            patch.object(orphan_guard, "snapshot_udp_port_holders", return_value=live_udp),
            patch.object(box_reservation, "current_reservation", return_value=live_m1),
        ):
            self.assertIs(box_turn.collect_status()["occupied"], True)
            os.environ["DAYZ_BOX_TURN_SCAN"] = "inject"
            os.environ["DAYZ_BOX_TURN_RESERVATION"] = str(self.root / "missing")
            try:
                bypassed = box_turn.collect_status()
            finally:
                os.environ.pop("DAYZ_BOX_TURN_SCAN", None)
                os.environ.pop("DAYZ_BOX_TURN_RESERVATION", None)
            self.assertIs(bypassed["occupied"], True)

    def test_p3_wait_s_isfinite_udp_unknown_wins_and_invalid_ticket_clears(self) -> None:
        from dayz_mcp import box_turn

        refused = self._run(
            ["run", "--purpose", "t", "--wait-s", "nan", "--", sys.executable, "-c", "pass"],
        )
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(self._line(refused), {"box_turn": "bad_args", "exit": 2})
        reason = box_turn._udp_reason(
            {
                "known": True,
                "holders": [
                    {"port": 2302, "pid": 1, "name": "DayZ_x64.exe"},
                    {"port": 2303, "pid": 2, "name": None},
                ],
            }
        )
        self.assertEqual(reason, "scan_unknown")
        turn = box_turn.Turn("t", "h", 1.0, [sys.executable])
        turn.ticket = "stale"
        turn._remember({"response": {"box_ticket": None, "box_wait_error": "box_ticket_invalid"}})
        self.assertIsNone(turn.ticket)

    def test_f3_reg_admission_still_refuses_a_start_without_the_head_ticket(self) -> None:
        from dayz_mcp import shared_box_queue as queue

        owner = _identity("reg-owner")
        coord = SessionCoordinator(wall_time_fn=time.time)
        held = coord.box_wait_touch(owner, claim=True, purpose="reg")
        try:
            reason = queue.admit(
                "someone-else",
                None,
                now=time.time(),
                root=self.root,
            )
            self.assertEqual(reason, "box_claimed")
            self.assertIsNone(
                queue.admit(
                    owner.session_id,
                    held.get("box_ticket"),
                    now=time.time(),
                    root=self.root,
                )
            )
        finally:
            coord.box_wait_touch(owner, held.get("box_ticket"), done=True)


def _alive(pid: int) -> bool:
    from dayz_mcp.native_process_guard import NativeProcessGuard

    snap = NativeProcessGuard().snapshot(pid)
    return isinstance(snap, dict) and snap.get("error") != "process_not_found"


if __name__ == "__main__":
    unittest.main()
