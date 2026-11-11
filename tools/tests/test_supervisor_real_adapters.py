"""Acceptance for the REAL supervisor adapters (A1).

No DayZ is launched. No launcher/Steam fake is used as accreditation of an
abort with a game. What is exercised for real:

- CIM FreePhysicalMemory / Win32_Process PrivatePageCount+WorkingSetSize and
  psapi GetPerformanceInfo against this host.
- The product loopback lifecycle surface (RunManifestStore + ProcessLifecycle)
  over a durable RUNNING_IDLE row built by the real APIs, not FakeLifecycle.
- The public FastMCP ``dayz_test_stop`` tool with an explicit run_id, crossing
  ClientRuntime -> ControlClient -> loopback /lifecycle/stop -> a real
  NativeProcessGuard that kills a real child process.

The only replaced host primitive is the native launcher transport: the stop of
an existing row does not launch anything, so the transport is never entered.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp.native_process_guard import NativeProcessGuard
from dayz_mcp.process_lifecycle import ProcessRecord, RunRecord
from supervisor.loop import run_supervisor
from supervisor.real_adapters import (
    AsyncRunner,
    RealHostMetrics,
    RealLifecycle,
    RealStop,
)
from supervisor.types import MonitorThresholds, StopOutcomeKind
from supervisor.windows_metrics import bytes_to_gib
from tests.lifecycle_helpers import stamp_launcher
from tests.test_starting_cancel import RUN_ID, _Fixture, _LoopbackSurface

_EVIDENCE: dict[str, object] = {}


def tearDownModule() -> None:
    path = os.environ.get("A1_ACCEPTANCE_OUT")
    if not path:
        return
    _EVIDENCE["schema"] = "antihack-supervisor-real-acceptance/v1"
    _EVIDENCE["hard_os_limit_implemented"] = False
    _EVIDENCE["no_dayz_launched"] = True
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(_EVIDENCE, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _host_figures(sample) -> dict[str, object]:
    return {
        "ok": sample.ok,
        "error": sample.error,
        "free_physical_kib": sample.free_physical_kib,
        "free_physical_bytes": sample.free_physical_bytes,
        "free_physical_gib": (
            bytes_to_gib(sample.free_physical_bytes)
            if sample.free_physical_bytes is not None
            else None
        ),
        "commit_total_bytes": sample.commit_total_bytes,
        "commit_limit_bytes": sample.commit_limit_bytes,
        "physical_available_bytes": sample.physical_available_bytes,
        "gpi_available": sample.gpi_available,
        "gpi_error": sample.gpi_error,
        "private_bytes_by_pid": {
            str(pid): value
            for pid, value in sorted(sample.private_bytes_by_pid.items())
        },
        "working_set_bytes_by_pid": {
            str(pid): value
            for pid, value in sorted(sample.working_set_bytes_by_pid.items())
        },
        "observed_at_wall_iso": sample.observed_at_wall_iso,
        "units": {
            "free_physical_kib": "CIM Win32_OperatingSystem.FreePhysicalMemory (KiB)",
            "free_physical_gib": "(KiB * 1024) / 2**30",
            "private_bytes_by_pid": "CIM Win32_Process.PrivatePageCount (bytes)",
            "working_set_bytes_by_pid": "CIM Win32_Process.WorkingSetSize (bytes)",
            "commit": "psapi GetPerformanceInfo pages * PageSize",
        },
    }


class RealHostMetricsAcceptanceTest(unittest.TestCase):
    def test_real_host_sample_on_this_machine(self) -> None:
        metrics = RealHostMetrics()
        sample = metrics.sample([os.getpid()])
        self.assertTrue(sample.ok, sample.error)
        self.assertIsNotNone(sample.free_physical_kib)
        self.assertIsNotNone(sample.free_physical_bytes)
        self.assertGreater(sample.free_physical_bytes, 0)
        self.assertGreater(sample.free_physical_kib, 0)
        self.assertEqual(
            sample.free_physical_bytes, sample.free_physical_kib * 1024
        )
        self.assertIn(os.getpid(), sample.private_bytes_by_pid)
        self.assertGreater(sample.private_bytes_by_pid[os.getpid()], 0)
        self.assertIn(os.getpid(), sample.working_set_bytes_by_pid)
        self.assertGreater(sample.working_set_bytes_by_pid[os.getpid()], 0)
        if sample.gpi_available:
            self.assertGreater(sample.commit_total_bytes, 0)
            self.assertGreater(sample.commit_limit_bytes, 0)
            self.assertGreaterEqual(
                sample.commit_limit_bytes, sample.commit_total_bytes
            )
            self.assertGreater(sample.physical_available_bytes, 0)
        else:
            # Documented degradation: FreePhysical + Private stay the gate.
            self.assertIsNone(sample.commit_total_bytes)
            self.assertIsNone(sample.commit_limit_bytes)
            self.assertIsNotNone(sample.gpi_error)
        _EVIDENCE["host_metrics"] = _host_figures(sample)
        _EVIDENCE["host_metrics"]["pid"] = os.getpid()


class RealLifecycleStopAcceptanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _Fixture()
        self.fixture.lifecycle.guard = NativeProcessGuard()
        self.surface = _LoopbackSurface(self.fixture)
        self.surface.start()
        self.addCleanup(self.surface.close)
        self.addCleanup(self.fixture.close)

        self.child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self._reap_child)

        self.guard = NativeProcessGuard()
        snapshot: dict[str, object] = {}
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            snapshot = self.guard.snapshot(self.child.pid)
            if snapshot.get("identity_complete") is True:
                break
            time.sleep(0.02)
        self.assertIs(snapshot.get("identity_complete"), True, snapshot)

        self.record = ProcessRecord(
            int(snapshot["pid"]),
            str(snapshot["creation_time_utc"]),
            str(snapshot["executable_sha256"]),
            str(snapshot["command_line_sha256"]),
            "client",
            str(snapshot["identity_scheme"]),
        )
        run = RunRecord(
            RUN_ID,
            None,
            None,
            "RUNNING_IDLE",
            "same",
            "@StorageMod",
            str(Path(self.surface.policy.dev_root) / "_client" / "profiles"),
            "mission",
            [self.record],
        )
        self.fixture.store.add(run)
        stamp_launcher(
            self.fixture.lifecycle, RUN_ID, self.surface.runtime.identity
        )

        self.runner = AsyncRunner()
        self.addCleanup(self.runner.close)
        self.lifecycle = RealLifecycle(
            self.surface.runtime, runner=self.runner
        )
        self.stop = RealStop(self.surface.app, runner=self.runner)
        self.metrics = RealHostMetrics()

    def _reap_child(self) -> None:
        if self.child.poll() is None:
            self.child.kill()
        try:
            self.child.wait(5)
        except Exception:
            pass

    def test_real_lifecycle_observes_pid_and_creation(self) -> None:
        snapshot = self.lifecycle.get_run(RUN_ID)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.run_id, RUN_ID)
        self.assertEqual(snapshot.state, "RUNNING_IDLE")
        self.assertEqual(len(snapshot.processes), 1)
        process = snapshot.processes[0]
        self.assertEqual(process.pid, self.child.pid)
        self.assertTrue(process.creation_time_utc)
        self.assertEqual(process.identity_scheme, "psutil-argv-v2")
        self.assertEqual(process.role, "client")
        # The native launcher transport was never entered: no launch happened.
        self.assertFalse(self.fixture.launcher.entered.is_set())
        _EVIDENCE["lifecycle"] = {
            "source": (
                "ClientRuntime.lifecycle_status -> ControlClient -> loopback "
                "/lifecycle/status (real RunManifestStore/ProcessLifecycle)"
            ),
            "run_id": snapshot.run_id,
            "state": snapshot.state,
            "processes": [
                {
                    "pid": item.pid,
                    "creation_time_utc": item.creation_time_utc,
                    "identity_scheme": item.identity_scheme,
                    "role": item.role,
                }
                for item in snapshot.processes
            ],
            "fake_lifecycle": False,
            "path_exposed_by_lifecycle": False,
        }

    def test_real_stop_through_public_tool_kills_only_own_process(self) -> None:
        before = self.lifecycle.get_run(RUN_ID)
        self.assertIsNotNone(before)
        assert before is not None
        self.assertEqual(before.state, "RUNNING_IDLE")
        self.assertEqual([item.pid for item in before.processes], [self.child.pid])

        host_sample = self.metrics.sample([self.child.pid, os.getpid()])
        self.assertTrue(host_sample.ok, host_sample.error)

        thresholds = MonitorThresholds(
            floor_free_physical_bytes=1 << 62,
            max_private_sum_bytes=1 << 62,
            max_sample_age_s=30.0,
        )
        result = run_supervisor(
            run_id=RUN_ID,
            lifecycle=self.lifecycle,
            metrics=self.metrics,
            stop=self.stop,
            thresholds=thresholds,
            tick_s=0.0,
            max_ticks=5,
            require_pids_zero_after_ok_stop=True,
        )

        self.assertEqual(
            result.outcome, StopOutcomeKind.SUCCEEDED_PIDS_ZERO, result.message
        )
        self.assertTrue(result.claimed_real_shutdown, result.message)
        self.assertFalse(result.storage_rollback_claimed)
        self.assertFalse(result.hard_os_limit_implemented)
        self.assertEqual(result.stop_state, "EXITED")
        self.assertEqual(result.stop_method, "forced_kill")
        self.assertIsNone(result.stop_error_code)
        self.assertIsNotNone(result.t_decision_monotonic)
        self.assertIsNotNone(result.t_stop_return_monotonic)
        # N1: the adapter always named this run explicitly.
        self.assertEqual(self.stop.calls, [RUN_ID])

        # The real child is dead and the durable row is terminal.
        self.assertIsNotNone(self.child.wait(5))
        self.assertIsNotNone(self.child.poll())
        after = self.lifecycle.get_run(RUN_ID)
        self.assertIsNotNone(after)
        assert after is not None
        self.assertEqual(after.state, "EXITED")
        self.assertEqual(after.processes, ())
        self.assertFalse(self.fixture.launcher.entered.is_set())
        self.assertEqual(self.fixture.launcher.terminate_calls, 0)

        stop_return = [
            event for event in result.events if event.name == "stop_return"
        ]
        self.assertEqual(len(stop_return), 1)
        self.assertEqual(stop_return[0].detail.get("raw_status"), "succeeded")
        self.assertEqual(stop_return[0].detail.get("run_id"), RUN_ID)

        _EVIDENCE["host_metrics"] = _host_figures(host_sample)
        _EVIDENCE["host_metrics"]["pids"] = [self.child.pid, os.getpid()]
        _EVIDENCE["stop"] = {
            "adapter": (
                "RealStop -> FastMCP dayz_test_stop -> ClientRuntime -> "
                "ControlClient -> loopback /lifecycle/stop"
            ),
            "explicit_run_id": RUN_ID,
            "outcome": result.outcome.value,
            "claimed_real_shutdown": result.claimed_real_shutdown,
            "storage_rollback_claimed": result.storage_rollback_claimed,
            "stop_state": result.stop_state,
            "stop_method": result.stop_method,
            "stop_error_code": result.stop_error_code,
            "hard_os_limit_implemented": result.hard_os_limit_implemented,
            "t_decision_monotonic": result.t_decision_monotonic,
            "t_stop_return_monotonic": result.t_stop_return_monotonic,
            "child_pid": self.child.pid,
            "child_dead": self.child.poll() is not None,
            "durable_after": {
                "state": after.state,
                "processes": [],
            },
            "direct_inflight_used": False,
            "fake_stop_used": False,
            "fake_lifecycle_used": False,
            "launcher_transport_entered": self.fixture.launcher.entered.is_set(),
        }
        _EVIDENCE["limits"] = {
            "floor_free_physical_bytes": thresholds.floor_free_physical_bytes,
            "max_private_sum_bytes": thresholds.max_private_sum_bytes,
            "max_sample_age_s": thresholds.max_sample_age_s,
            "thresholds_are_monitor_only": True,
        }
        _EVIDENCE["s3"] = {
            "storage_rollback_claimed": False,
            "note": (
                "A STARTING cancel may leave a storage rotation done after the "
                "pre-check; the supervisor reports the terminal row/bindings/"
                "intent settlement, not a storage rollback."
            ),
        }


class RealStopExplicitRunIdTest(unittest.TestCase):
    def test_stop_requires_an_explicit_run_id(self) -> None:
        runner = AsyncRunner()
        self.addCleanup(runner.close)
        stop = RealStop(app=None, runner=runner)
        with self.assertRaises(ValueError):
            stop.stop("")
        with self.assertRaises(ValueError):
            stop.stop(None)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
