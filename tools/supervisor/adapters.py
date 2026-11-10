from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from .types import HostSample, ProcessIdentity, RunSnapshot


@dataclass
class FakeLifecycle:
    """In-memory lifecycle for unit tests — NOT the daemon."""

    runs: dict[str, RunSnapshot] = field(default_factory=dict)

    def get_run(self, run_id: str) -> RunSnapshot | None:
        return self.runs.get(run_id)

    def put(self, snap: RunSnapshot) -> None:
        self.runs[snap.run_id] = snap


@dataclass
class FakeHostMetrics:
    free_physical_bytes: int = 10 * (1 << 30)
    commit_total_bytes: int = 50 * (1 << 30)
    commit_limit_bytes: int = 100 * (1 << 30)
    private_by_pid: dict[int, int] = field(default_factory=dict)
    working_set_by_pid: dict[int, int] = field(default_factory=dict)
    fail_next: bool = False
    stale_s: float = 0.0
    omit_pids: tuple[int, ...] = ()
    samples: int = 0

    def sample(self, pids: Sequence[int]) -> HostSample:
        self.samples += 1
        now = time.monotonic()
        if self.fail_next:
            self.fail_next = False
            return HostSample(
                free_physical_bytes=None,
                commit_total_bytes=None,
                commit_limit_bytes=None,
                private_bytes_by_pid={},
                observed_at_monotonic=now,
                ok=False,
                error="fake_sample_failure",
            )
        priv = {
            int(p): int(self.private_by_pid.get(int(p), 0))
            for p in pids
            if int(p) not in self.omit_pids
        }
        working = {
            int(p): int(self.working_set_by_pid.get(int(p), 0))
            for p in pids
            if int(p) not in self.omit_pids
        }
        return HostSample(
            free_physical_bytes=self.free_physical_bytes,
            commit_total_bytes=self.commit_total_bytes,
            commit_limit_bytes=self.commit_limit_bytes,
            private_bytes_by_pid=priv,
            observed_at_monotonic=now - self.stale_s,
            ok=True,
            free_physical_kib=self.free_physical_bytes // 1024,
            working_set_bytes_by_pid=working,
            physical_available_bytes=self.free_physical_bytes,
            gpi_available=True,
            observed_at_wall_iso="2026-11-10T00:00:00.000Z",
        )


@dataclass
class FakeStop:
    """Records stop calls; does not touch daemon."""

    responses: list[dict[str, Any]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    default_ok: bool = True

    def stop(self, run_id: str) -> dict[str, Any]:
        self.calls.append(run_id)
        if self.responses:
            return self.responses.pop(0)
        if self.default_ok:
            return {
                "ok": True,
                "error_code": None,
                "stop_method": "forced_kill",
                "raw_status": "succeeded",
                "state": "EXITED",
                "run_id": run_id,
                "terminated": 1,
                "cleanup_degraded": False,
            }
        return {
            "ok": False,
            "error_code": "run_stop_failed",
            "stop_method": None,
            "raw_status": "failed",
            "state": "UNRECONCILED",
            "run_id": run_id,
            "terminated": None,
            "cleanup_degraded": None,
        }


@dataclass
class RealStopSeamDocs:
    """Documented sentinel from the fake-only package — NOT invoked.

    The wired stop is :class:`supervisor.real_adapters.RealStop`, which calls
    the public FastMCP ``dayz_test_stop`` with an explicit run_id. This class is
    kept only to name the old seam; calling it is a programming error.
    """

    def stop(self, run_id: str) -> dict[str, Any]:
        raise RuntimeError(
            "RealStopSeamDocs is a documented sentinel; use "
            "supervisor.real_adapters.RealStop"
        )


def make_process(
    pid: int,
    creation: str,
    path: str = r"C:\fake\DayZDiag_x64.exe",
    role: str = "server",
    identity_scheme: str | None = "psutil-argv-v2",
) -> ProcessIdentity:
    return ProcessIdentity(
        pid=pid,
        creation_time_utc=creation,
        path=path,
        role=role,
        identity_scheme=identity_scheme,
    )
