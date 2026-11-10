from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence

_GIB = 1 << 30
# v2 calibration (monitor only, not JobObject): floor F=3 GiB; Caps E1=4,
# E2=6 GiB. The supervisor never enforces these at the OS level.
DEFAULT_FLOOR_FREE_PHYSICAL_BYTES = 3 * _GIB
DEFAULT_CAP_E1_PRIVATE_BYTES = 4 * _GIB
DEFAULT_CAP_E2_PRIVATE_BYTES = 6 * _GIB


class StopOutcomeKind(str, Enum):
    SUCCEEDED_PIDS_ZERO = "succeeded_pids_zero"
    STOP_FAILED = "stop_failed"
    STOP_REFUSED_STATE = "stop_refused_state"  # product refusal, e.g. run_not_active
    STOP_SESSION_BUSY = "stop_session_busy"
    LAUNCH_CANCEL_PENDING = "launch_cancel_pending"
    LAUNCH_CANCEL_DEGRADED = "launch_cancel_degraded"
    SAMPLE_FAILED = "sample_failed"
    SAMPLE_STALE = "sample_stale"
    NO_OWNED_PIDS = "no_owned_pids"
    THRESHOLD_BREACH = "threshold_breach"  # decision recorded; stop may follow


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    creation_time_utc: str
    path: str | None
    role: str | None = None
    identity_scheme: str | None = None


@dataclass(frozen=True)
class RunSnapshot:
    run_id: str
    state: str
    processes: tuple[ProcessIdentity, ...]
    observed_at_monotonic: float


@dataclass(frozen=True)
class HostSample:
    free_physical_bytes: int | None
    commit_total_bytes: int | None
    commit_limit_bytes: int | None
    private_bytes_by_pid: Mapping[int, int]
    observed_at_monotonic: float
    ok: bool
    error: str | None = None
    free_physical_kib: int | None = None
    working_set_bytes_by_pid: Mapping[int, int] = field(default_factory=dict)
    physical_available_bytes: int | None = None
    gpi_available: bool | None = None
    gpi_error: str | None = None
    observed_at_wall_iso: str | None = None


@dataclass(frozen=True)
class MonitorThresholds:
    """Soft monitor thresholds — NOT OS/JobObject hard limits.

    Defaults mirror the v2 calibration: floor F=3 GiB, Cap E1=4 GiB. E2=6 GiB
    is available as a constant for the wider box. All are config-adjustable.
    """

    floor_free_physical_bytes: int = DEFAULT_FLOOR_FREE_PHYSICAL_BYTES
    max_private_sum_bytes: int = DEFAULT_CAP_E1_PRIVATE_BYTES
    max_sample_age_s: float = 5.0
    note: str = "monitor thresholds only; hard OS containment NOT implemented"


@dataclass
class TimelineEvent:
    name: str
    monotonic: float
    wall_iso: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class SupervisorResult:
    run_id: str
    events: list[TimelineEvent]
    outcome: StopOutcomeKind
    claimed_real_shutdown: bool
    cadence_samples: list[float]
    mean_sample_interval_s: float | None
    hard_os_limit_implemented: bool = False
    message: str = ""
    # S3: a STARTING cancel can land after the launch's last pre-Popen check,
    # so storage rotation may already have run. The supervisor never claims a
    # storage rollback; it only reports the durable row/binding settlement.
    storage_rollback_claimed: bool = False
    stop_state: str | None = None
    stop_error_code: str | None = None
    stop_method: str | None = None
    # Monotonic stamps only. G_STOP_LATENCY is NOT measured by this hop (N3);
    # the runtime plan owns the latency gate.
    t_decision_monotonic: float | None = None
    t_stop_return_monotonic: float | None = None


class LifecyclePort(Protocol):
    """Seam to observe run_id / PIDs. Fake in unit tests; real adapter optional."""

    def get_run(self, run_id: str) -> RunSnapshot | None: ...


class HostMetricsPort(Protocol):
    def sample(self, pids: Sequence[int]) -> HostSample: ...


class StopPort(Protocol):
    """Seam for dayz_test_stop. Must never claim success if call fails."""

    def stop(self, run_id: str) -> dict[str, Any]:
        """Return dict with keys: ok:bool, error_code:str|None, stop_method:str|None,
        raw_status:str|None, state:str|None, run_id:str|None, terminated:int|None,
        cleanup_degraded:bool|None.
        """
        ...


# States the public dayz_test_tool accepts for a stop at ba47dbe7.
# STARTING is stoppable through the owned in-flight cancel branch; the
# supervisor must use it instead of a fixed "STARTING is not stoppable" policy.
LIVE_STOPPABLE_STATES = frozenset(
    {"STARTING", "RUNNING", "RUNNING_IDLE", "UNRECONCILED"}
)
