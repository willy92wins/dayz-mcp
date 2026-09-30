"""fb-20260818-232129-1233: a server that hangs while loading is named.

The UDP readiness probe (sealed, in the worker) says readiness_timeout both
for a server still loading and for one that hung. The daemon watches the RPT
of each server launch while it starts; the stop that follows a timeout records
whether the RPT had been silent for more than 75 s, and dayz_test_run then
reports server_start_hung instead of the generic timeout. Every clock here is
a fake one and every RPT a fake file whose mtime the test sets.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import mcp_capture
from dayz_mcp import dayz_test_tool, dayz_test_worker, native_launcher_transaction
from dayz_mcp import process_lifecycle, steam_preflight
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.dayz_test_tool_helpers import (
    RUN_ID,
    _Bundle,
    _Opened,
    _Runtime,
    _policy,
    _sealed,
    _terminal,
)
from tests.process_lifecycle_helpers import (
    AuditSink,
    FakeGuard,
    IDENTITY_A,
    process,
    snapshot,
)
from tests.steam_helpers import FakeSteamGate

# A fixed point on the epoch axis: the fake clock and the fake mtimes share it.
T0 = 1_700_000_000.0
_HEADER = (
    b"=====================================================================\r\n"
    b"== DayZDiag_x64.exe\r\n"
    b"== -server -profiles=P:\\x\\_server\\profiles\r\n"
    b"=====================================================================\r\n"
    b"Exe timestamp: 2026/08/15 04:37:25\r\n"
    b"Current time:  2026/09/30 20:31:09\r\n"
    b"Version 1.29.163709\r\n"
    b"=====================================================================\r\n"
    b"\r\n"
)


def _write(path: Path, data: bytes, mtime: float, *, append: bool = False) -> None:
    with path.open("ab" if append else "wb") as handle:
        handle.write(data)
    os.utime(path, (mtime, mtime))


class LaunchRptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_the_newest_rpt_written_since_the_launch_is_the_one(self) -> None:
        old = self.root / "DayZDiag_x64_old.RPT"
        new = self.root / "DayZDiag_x64_new.RPT"
        script = self.root / "script_new.log"
        _write(old, b"old", T0 - 600)
        _write(new, _HEADER, T0 + 3)
        _write(script, b"much newer", T0 + 30)
        self.assertEqual(
            process_lifecycle._launch_rpt(str(self.root), T0),
            (str(new), len(_HEADER), T0 + 3),
        )

    def test_an_rpt_older_than_the_launch_is_not_this_launch(self) -> None:
        _write(self.root / "DayZDiag_x64_old.RPT", b"old", T0 - 60)
        self.assertIsNone(process_lifecycle._launch_rpt(str(self.root), T0))

    def test_an_unreadable_folder_is_no_rpt(self) -> None:
        self.assertIsNone(
            process_lifecycle._launch_rpt(str(self.root / "missing"), T0)
        )


class ServerStartWatchTest(unittest.TestCase):
    """The watch alone: fake clock values, a fake RPT with set mtimes."""

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.rpt = self.root / "DayZDiag_x64_2026-09-30_20-31-07.RPT"
        self.watch = process_lifecycle._ServerStartWatch("run-1", str(self.root), T0)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_an_rpt_that_went_silent_for_more_than_75_s_is_hung(self) -> None:
        self.watch.sample(T0 + 1)  # nothing written yet
        _write(self.rpt, _HEADER, T0 + 3)
        self.watch.sample(T0 + 4)
        _write(self.rpt, b"20:31:12.000 loading\r\n", T0 + 40, append=True)
        self.watch.sample(T0 + 41)
        self.watch.sample(T0 + 100)  # unchanged
        self.watch.sample(T0 + 116)
        self.assertEqual(
            self.watch.verdict(T0 + 116),
            {"run_id": "run-1", "code": "server_start_hung", "rpt_stalled_s": 76.0},
        )

    def test_the_threshold_is_strictly_more_than_75_s(self) -> None:
        _write(self.rpt, _HEADER, T0 + 3)
        self.watch.sample(T0 + 4)
        self.assertIsNone(self.watch.verdict(T0 + 78))  # exactly 75 s silent
        self.assertIsNotNone(self.watch.verdict(T0 + 78.5))

    def test_an_rpt_that_keeps_growing_is_loading_not_hung(self) -> None:
        for offset in range(10, 300, 20):
            _write(self.rpt, b"line\r\n", T0 + offset, append=True)
            self.watch.sample(T0 + offset + 1)
        self.assertIsNone(self.watch.verdict(T0 + 300))

    def test_no_rpt_at_all_is_no_verdict(self) -> None:
        for offset in (1, 100, 400):
            self.watch.sample(T0 + offset)
        self.assertIsNone(self.watch.verdict(T0 + 400))

    def test_a_single_late_look_dates_the_silence_by_the_rpt_mtime(self) -> None:
        """No poll during the start: the final look alone still measures it."""
        _write(self.rpt, _HEADER + b"20:31:12.000 Load entity type\r\n", T0 + 50)
        self.watch.sample(T0 + 300)
        self.assertEqual(self.watch.verdict(T0 + 300)["rpt_stalled_s"], 250.0)

    def test_an_mtime_outside_the_window_never_lengthens_the_silence(self) -> None:
        # Changed since the last look but dated before it: a clock that cannot
        # be trusted. The change is dated at this look instead.
        _write(self.rpt, _HEADER, T0 + 3)
        self.watch.sample(T0 + 4)
        _write(self.rpt, b"more\r\n", T0 + 2, append=True)
        self.watch.sample(T0 + 200)
        self.assertIsNone(self.watch.verdict(T0 + 200))
        # Dated in the future: same answer.
        _write(self.rpt, b"more\r\n", T0 + 10_000, append=True)
        self.watch.sample(T0 + 210)
        self.assertIsNone(self.watch.verdict(T0 + 210))

    def test_an_older_look_is_never_folded_after_a_newer_one(self) -> None:
        self.watch.observe(("x.RPT", 10, T0 + 3), T0 + 50)
        self.watch.observe(("x.RPT", 20, T0 + 45), T0 + 40)
        self.assertEqual(self.watch.grew_at, T0 + 3)
        self.assertEqual(self.watch.sampled_at, T0 + 50)


def _worker_request(root: Path, role: str, run_id: str | None = None) -> dict[str, object]:
    runtime = dayz_test_worker.WorkerRuntimePolicy(
        dev_root=str(root / "ExampleMod_Suite"),
        mod="ExampleMod",
        diag_executable=str(root / "DayZ" / "DayZDiag_x64.exe"),
        game_directory=str(root / "DayZ"),
        mission_aliases=(
            ("chernarus", str(root / "mpmissions" / "dayzOffline.chernarusplus")),
        ),
        mods_root=str(root / "Mods"),
        build_temp_root=str(root / "temp"),
        build_source_basename=None,
    )
    payload: dict[str, object] = {
        "auto_remediate_steam": False,
        "base_mods": [],
        "extra_mods": [],
        "height": 1080,
        "mission": "chernarus",
        "no_file_patching": False,
        "player_name": "Dev",
        "port": 2302,
        "server_mods": [],
        "width": 1920,
    }
    return dayz_test_worker._start_core(payload, runtime, role=role, run_id=run_id)


class ServerStartVerdictLifecycleTest(unittest.TestCase):
    """start_run -> status polls -> stop_run, as the worker drives a timeout."""

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        game = self.root / "DayZ"
        game.mkdir()
        (game / "DayZDiag_x64.exe").write_bytes(b"")
        (self.root / "mpmissions" / "dayzOffline.chernarusplus").mkdir(parents=True)
        self.profiles = self.root / "ExampleMod_Suite" / "_server" / "profiles"
        self.profiles.mkdir(parents=True)
        paths = RuntimePaths(
            self.root / "runtime",
            self.root / "runtime" / "audit",
            self.root / "runtime" / "coordination.json",
            self.root / "runtime" / "runs.json",
        )
        audit = AuditSink()
        coordinator = SessionCoordinator(
            token_fn=lambda: "token-A", id_fn=lambda: "lease-A", audit=audit
        )
        status, acquired = coordinator.acquire(IDENTITY_A, "lifecycle")
        self.assertEqual(status, 200)
        self.token = acquired["lease_token"]
        self.guard = FakeGuard()
        self.pids = iter((9001, 9002, 9003))
        run_ids = iter(("run-1", "run-2"))
        self.lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=coordinator,
            manifest=RunManifestStore(paths),
            audit=audit,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=game,
            launcher=self._launch,
            id_fn=lambda: next(run_ids),
        )
        self.now = T0
        self.lifecycle._server_start_clock = lambda: self.now
        self.rpt = self.profiles / "DayZDiag_x64_2026-09-30_20-31-07.RPT"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _launch(self, argv: list[str], cwd: str, window_style: str) -> object:
        pid = next(self.pids)
        role = "server" if "-server" in argv else "client"
        self.guard.snapshots[pid] = snapshot(process(pid, role))
        return mock.Mock(pid=pid)

    def _status(self, at: float) -> dict[str, object]:
        self.now = at
        return self.lifecycle.status(IDENTITY_A)

    def _start_server(self) -> str:
        result = self.lifecycle.start_run(
            IDENTITY_A, self.token, _worker_request(self.root, "server")
        )
        self.assertTrue(result.get("ok"), result)
        return str(result["run_id"])

    def _stop(self, run_id: str, at: float) -> None:
        self.now = at
        result = self.lifecycle.stop_run(IDENTITY_A, self.token, run_id)
        self.assertEqual(result.get("state"), "EXITED", result)

    def test_a_server_whose_rpt_went_silent_is_stopped_with_a_hung_verdict(self) -> None:
        run_id = self._start_server()
        _write(self.rpt, _HEADER, T0 + 3)
        self._status(T0 + 4)
        _write(self.rpt, b"20:31:40.000 Load entity type\r\n", T0 + 40, append=True)
        self._status(T0 + 42)
        self._status(T0 + 150)  # the readiness probe keeps polling, nothing new
        self._stop(run_id, T0 + 200)

        verdict = self._status(T0 + 201).get("server_start_verdict")
        self.assertEqual(
            verdict,
            {"run_id": run_id, "code": "server_start_hung", "rpt_stalled_s": 160.0},
        )

    def test_a_server_still_writing_its_rpt_leaves_no_verdict(self) -> None:
        """Negative control: only the silence differs from the test above."""
        run_id = self._start_server()
        _write(self.rpt, _HEADER, T0 + 3)
        self._status(T0 + 4)
        _write(self.rpt, b"20:33:50.000 still loading\r\n", T0 + 170, append=True)
        self._status(T0 + 171)
        self._stop(run_id, T0 + 200)
        self.assertNotIn("server_start_verdict", self._status(T0 + 201))

    def test_a_server_that_never_wrote_an_rpt_gets_no_verdict(self) -> None:
        run_id = self._start_server()
        self._status(T0 + 100)
        self._stop(run_id, T0 + 400)
        self.assertNotIn("server_start_verdict", self._status(T0 + 401))

    def test_the_next_server_launch_clears_the_verdict(self) -> None:
        run_id = self._start_server()
        _write(self.rpt, _HEADER, T0 + 3)
        self._status(T0 + 4)
        self._stop(run_id, T0 + 200)
        self.assertIn("server_start_verdict", self._status(T0 + 201))

        self.now = T0 + 300
        self._start_server()
        self.assertNotIn("server_start_verdict", self._status(T0 + 301))

    def test_a_client_joining_the_run_ends_the_start_window(self) -> None:
        """The worker adds the client only after readiness passed."""
        run_id = self._start_server()
        _write(self.rpt, _HEADER, T0 + 3)
        self._status(T0 + 4)
        self.now = T0 + 30
        joined = self.lifecycle.start_run(
            IDENTITY_A, self.token, _worker_request(self.root, "client", run_id)
        )
        self.assertTrue(joined.get("ok"), joined)
        self._stop(run_id, T0 + 600)
        self.assertNotIn("server_start_verdict", self._status(T0 + 601))

    def test_a_watch_is_sampled_at_most_once_per_interval(self) -> None:
        self._start_server()
        with mock.patch.object(
            process_lifecycle, "_launch_rpt", wraps=process_lifecycle._launch_rpt
        ) as looked:
            self._status(T0 + 10.0)
            self._status(T0 + 10.5)
            self._status(T0 + 11.0)
        self.assertEqual(looked.call_count, 2)


class ServerStartHungToolTest(unittest.IsolatedAsyncioTestCase):
    """dayz_test_run(mode=all) whose sealed worker answers readiness_timeout."""

    def setUp(self) -> None:
        for name, value in (
            (
                "evaluate_steam_session",
                steam_preflight.SteamSessionResult(
                    error_code=None,
                    steam_registered_pid=1,
                    steam_live_pids=(1,),
                    remediation=steam_preflight.REMEDIATION,
                ),
            ),
            (
                "evaluate_prerun_desktop",
                mcp_capture.PrerunDesktopResult(
                    error_code=None,
                    desktop="unlocked",
                    mean_brightness=80.0,
                    nonblack_ratio=0.9,
                    waited_s=0.01,
                    remediation="",
                ),
            ),
            (
                "preflight_vpp_request",
                native_launcher_transaction.VppPreflightResult(
                    error_code=None,
                    missing=(),
                    warnings=(),
                    hint=native_launcher_transaction.VPP_PREFLIGHT_HINT,
                ),
            ),
        ):
            patcher = mock.patch.object(dayz_test_tool, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def run_all(
        self,
        lifecycle: dict[str, object],
        *,
        cleanup_degraded: bool = False,
        terminal_run_id: str | None = None,
    ) -> dict[str, object]:
        runtime = _Runtime(lifecycle=lifecycle)

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": cleanup_degraded,
                        "error_code": "readiness_timeout",
                        "exit_code": 2,
                        "ok": False,
                        "run_id": terminal_run_id,
                    }
                ),
            )
            return 2

        with mock.patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), mock.patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(_policy())),
        ), mock.patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            return await dayz_test_tool.execute_dayz_test_run(
                runtime, project="ExampleMod", mode="all", extra_mods=["@DayZ_MCP"]
            )

    @staticmethod
    def status(verdict: object, *, state: str = "EXITED") -> dict[str, object]:
        payload: dict[str, object] = {
            "runs": [{"run_id": RUN_ID, "state": state, "processes": []}]
        }
        if verdict is not None:
            payload["server_start_verdict"] = verdict
        return payload

    @staticmethod
    def hung(**overrides: object) -> dict[str, object]:
        verdict: dict[str, object] = {
            "run_id": RUN_ID,
            "code": "server_start_hung",
            "rpt_stalled_s": 160.0,
        }
        verdict.update(overrides)
        return verdict

    async def test_a_hung_start_is_named_instead_of_the_generic_timeout(self) -> None:
        result = await self.run_all(self.status(self.hung()))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "server_start_hung")
        self.assertIn("dayz_test_run", str(result["remediation"]))
        self.assertIsNone(result["run_id"])

    async def test_without_a_verdict_the_timeout_stays_generic(self) -> None:
        """Negative control: same terminal, no verdict on the status."""
        result = await self.run_all(self.status(None))
        self.assertEqual(result["error_code"], "readiness_timeout")
        self.assertIsNone(result["remediation"])

    async def test_a_verdict_that_does_not_read_exactly_right_is_ignored(self) -> None:
        cases = (
            ("the run is still alive", self.status(self.hung(), state="RUNNING")),
            ("another code", self.status(self.hung(code="server_start_slow"))),
            ("not past the threshold", self.status(self.hung(rpt_stalled_s=75.0))),
            ("stall not a number", self.status(self.hung(rpt_stalled_s="160"))),
            ("stall is a bool", self.status(self.hung(rpt_stalled_s=True))),
            ("run id not a uuid4", self.status(self.hung(run_id="run-1"))),
            ("verdict not a dict", self.status("server_start_hung")),
        )
        for label, lifecycle in cases:
            with self.subTest(label):
                result = await self.run_all(lifecycle)
                self.assertEqual(result["error_code"], "readiness_timeout")

    async def test_a_degraded_stop_matches_the_verdict_by_run_id(self) -> None:
        """The terminal names the run when its stop degraded: it must match."""
        matching = await self.run_all(
            self.status(self.hung(), state="RUNNING"),
            cleanup_degraded=True,
            terminal_run_id=RUN_ID,
        )
        self.assertEqual(matching["error_code"], "server_start_hung")
        other = "abcdefab-1234-4234-8234-1234567890ab"
        mismatched = await self.run_all(
            self.status(self.hung(run_id=other)),
            cleanup_degraded=True,
            terminal_run_id=RUN_ID,
        )
        self.assertEqual(mismatched["error_code"], "readiness_timeout")

    async def test_an_unreadable_status_keeps_the_generic_timeout(self) -> None:
        runtime_status: dict[str, object] = {}

        async def boom() -> dict[str, object]:
            raise RuntimeError("daemon unavailable")

        with mock.patch.object(_Runtime, "lifecycle_status", side_effect=boom):
            result = await self.run_all(runtime_status)
        self.assertEqual(result["error_code"], "readiness_timeout")


if __name__ == "__main__":
    unittest.main()
