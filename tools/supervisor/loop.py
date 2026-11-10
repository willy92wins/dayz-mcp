from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Sequence

from .belonging import index_owned, owned_pids, reject_foreign_pid
from .types import (
    LIVE_STOPPABLE_STATES,
    HostMetricsPort,
    LifecyclePort,
    MonitorThresholds,
    StopOutcomeKind,
    StopPort,
    SupervisorResult,
    TimelineEvent,
)

_PENDING_CODES = frozenset({"launch_cancel_pending"})
_DEGRADED_CODES = frozenset({"launch_cancel_degraded"})
_SESSION_BUSY_CODES = frozenset({"session_busy", "session_transition_conflict"})
_REFUSED_CODES = frozenset(
    {
        "run_not_active",
        "run_not_found",
        "run_not_adopted",
        "run_protected",
        "run_not_adoptable",
        "retail_quarantine",
    }
)


def _wall() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def _event(name: str, **detail) -> TimelineEvent:
    return TimelineEvent(
        name=name, monotonic=time.monotonic(), wall_iso=_wall(), detail=dict(detail)
    )


def _mean(xs: list[float]) -> float | None:
    if not xs:
        return None
    return sum(xs) / len(xs)


def _result(
    *,
    run_id: str,
    events: list[TimelineEvent],
    outcome: StopOutcomeKind,
    cadence: list[float],
    message: str,
    claimed: bool = False,
    stop_state: str | None = None,
    stop_error_code: str | None = None,
    stop_method: str | None = None,
    t_decision: float | None = None,
    t_stop_return: float | None = None,
) -> SupervisorResult:
    return SupervisorResult(
        run_id=run_id,
        events=events,
        outcome=outcome,
        claimed_real_shutdown=claimed,
        cadence_samples=cadence,
        mean_sample_interval_s=_mean(cadence),
        hard_os_limit_implemented=False,
        message=message,
        storage_rollback_claimed=False,
        stop_state=stop_state,
        stop_error_code=stop_error_code,
        stop_method=stop_method,
        t_decision_monotonic=t_decision,
        t_stop_return_monotonic=t_stop_return,
    )


def _durable_state(
    lifecycle: LifecyclePort, run_id: str, fallback: str | None
) -> tuple[str | None, str]:
    """Read the durable row after a refused stop.

    N2: the supervisor is governed by the stop result and the durable row, not
    by an assumption that the run caller emits ``launch_cancel_degraded``. A
    degraded cancel must report the durable state (typically UNRECONCILED),
    never the pre-stop STARTING.
    """
    try:
        after = lifecycle.get_run(run_id)
    except Exception:
        return fallback, "envelope_fallback"
    if after is None:
        return fallback, "row_missing"
    return after.state, "durable_row"


def run_supervisor(
    *,
    run_id: str,
    lifecycle: LifecyclePort,
    metrics: HostMetricsPort,
    stop: StopPort,
    thresholds: MonitorThresholds,
    tick_s: float = 0.05,
    max_ticks: int = 40,
    require_pids_zero_after_ok_stop: bool = True,
    pids_zero_probe: Sequence[int] | None = None,
) -> SupervisorResult:
    """Continuous soft monitor for one run_id.

    Does NOT implement hard OS limits. Fail-closed: never claimed_real_shutdown
    unless the public stop returns ok AND the owned PIDs are gone from the
    durable lifecycle. STARTING is stoppable through the owned public cancel
    path (ba47dbe7); a product refusal is reported by its named code, never by
    a fixed local policy.
    """
    events: list[TimelineEvent] = []
    cadence: list[float] = []
    last_sample_mono: float | None = None
    events.append(
        _event(
            "supervisor_start",
            run_id=run_id,
            tick_s=tick_s,
            hard_os_limit=False,
            stoppable_states=sorted(LIVE_STOPPABLE_STATES),
        )
    )

    for i in range(max_ticks):
        try:
            snap = lifecycle.get_run(run_id)
        except Exception as error:
            events.append(
                _event(
                    "lifecycle_read_failed",
                    tick=i,
                    error=type(error).__name__,
                )
            )
            return _result(
                run_id=run_id,
                events=events,
                outcome=StopOutcomeKind.SAMPLE_FAILED,
                cadence=cadence,
                message=f"lifecycle read failed: {type(error).__name__}",
            )
        if snap is None:
            events.append(_event("run_missing", tick=i))
            return _result(
                run_id=run_id,
                events=events,
                outcome=StopOutcomeKind.SAMPLE_FAILED,
                cadence=cadence,
                message="run_id not visible in lifecycle",
            )

        owned = index_owned(snap)
        pids = owned_pids(snap)
        for proc in snap.processes:
            reason = reject_foreign_pid(proc.pid, proc.creation_time_utc, owned)
            if not proc.creation_time_utc:
                events.append(
                    _event(
                        "attribution_reject",
                        reason=reason or "no_creation_time",
                        pid=proc.pid,
                    )
                )
                return _result(
                    run_id=run_id,
                    events=events,
                    outcome=StopOutcomeKind.SAMPLE_FAILED,
                    cadence=cadence,
                    message="process missing creation_time_utc",
                )

        sample = metrics.sample(pids)
        now = sample.observed_at_monotonic
        if last_sample_mono is not None:
            cadence.append(now - last_sample_mono)
        last_sample_mono = now
        events.append(
            _event(
                "sample",
                tick=i,
                state=snap.state,
                pids=pids,
                ok=sample.ok,
                free_physical_bytes=sample.free_physical_bytes,
                free_physical_kib=sample.free_physical_kib,
                commit_total_bytes=sample.commit_total_bytes,
                commit_limit_bytes=sample.commit_limit_bytes,
                physical_available_bytes=sample.physical_available_bytes,
                private_sum=(
                    sum(sample.private_bytes_by_pid.values())
                    if sample.ok
                    else None
                ),
                gpi_available=sample.gpi_available,
                gpi_error=sample.gpi_error,
            )
        )

        if not sample.ok:
            events.append(
                _event(
                    "decision_fail_closed",
                    reason="sample_failed",
                    error=sample.error,
                )
            )
            return _result(
                run_id=run_id,
                events=events,
                outcome=StopOutcomeKind.SAMPLE_FAILED,
                cadence=cadence,
                message=f"sample failed: {sample.error}",
            )

        if sample.free_physical_bytes is None or sample.free_physical_bytes < 0:
            events.append(
                _event("decision_fail_closed", reason="free_physical_missing")
            )
            return _result(
                run_id=run_id,
                events=events,
                outcome=StopOutcomeKind.SAMPLE_FAILED,
                cadence=cadence,
                message="sample ok but FreePhysicalMemory missing",
            )

        age = time.monotonic() - sample.observed_at_monotonic
        if age > thresholds.max_sample_age_s:
            events.append(
                _event(
                    "decision_fail_closed",
                    reason="sample_stale",
                    age_s=age,
                    max_sample_age_s=thresholds.max_sample_age_s,
                )
            )
            return _result(
                run_id=run_id,
                events=events,
                outcome=StopOutcomeKind.SAMPLE_STALE,
                cadence=cadence,
                message=f"stale sample ({age:.3f}s > {thresholds.max_sample_age_s:.3f}s)",
            )

        missing_private = [
            pid for pid in pids if pid not in sample.private_bytes_by_pid
        ]
        if missing_private:
            events.append(
                _event(
                    "decision_fail_closed",
                    reason="private_missing",
                    pids=missing_private,
                )
            )
            return _result(
                run_id=run_id,
                events=events,
                outcome=StopOutcomeKind.SAMPLE_FAILED,
                cadence=cadence,
                message=f"private bytes missing for pids {missing_private}",
            )

        breach = None
        if sample.free_physical_bytes < thresholds.floor_free_physical_bytes:
            breach = "floor_free_physical"
        priv_sum = sum(sample.private_bytes_by_pid.values())
        if priv_sum > thresholds.max_private_sum_bytes:
            breach = breach or "max_private_sum"

        if breach is None:
            if tick_s > 0:
                time.sleep(tick_s)
            continue

        events.append(
            _event(
                "decision_threshold",
                breach=breach,
                state=snap.state,
                free_physical_bytes=sample.free_physical_bytes,
                private_sum=priv_sum,
            )
        )
        if snap.state == "STARTING":
            events.append(
                _event(
                    "s3_storage_note",
                    reason=(
                        "cancel may leave a storage rotation that ran after "
                        "the launch's last pre-Popen check; the settlement is "
                        "the terminal row/bindings/intent, not a storage rollback"
                    ),
                )
            )

        if not pids:
            events.append(_event("decision_no_owned_pids"))
            return _result(
                run_id=run_id,
                events=events,
                outcome=StopOutcomeKind.NO_OWNED_PIDS,
                cadence=cadence,
                message="threshold breach but no owned pids yet",
            )

        owned_at_decision = dict(owned)
        t_decision = time.monotonic()
        events.append(
            _event(
                "stop_call",
                t_decision=t_decision,
                run_id=run_id,
                state=snap.state,
            )
        )
        try:
            stop_result = stop.stop(run_id)
        except Exception as error:
            events.append(
                _event(
                    "stop_exception",
                    error=type(error).__name__,
                )
            )
            return _result(
                run_id=run_id,
                events=events,
                outcome=StopOutcomeKind.STOP_FAILED,
                cadence=cadence,
                message=f"stop raised {type(error).__name__}; fail-closed",
                t_decision=t_decision,
            )
        t_stop_return = time.monotonic()
        events.append(
            _event(
                "stop_return",
                t_stop_return=t_stop_return,
                ok=stop_result.get("ok"),
                error_code=stop_result.get("error_code"),
                stop_method=stop_result.get("stop_method"),
                state=stop_result.get("state"),
                run_id=stop_result.get("run_id"),
                terminated=stop_result.get("terminated"),
                cleanup_degraded=stop_result.get("cleanup_degraded"),
                raw_status=stop_result.get("raw_status"),
            )
        )

        if not stop_result.get("ok"):
            code = stop_result.get("error_code") or "stop_failed"
            envelope_state = stop_result.get("state")
            if not isinstance(envelope_state, str) or not envelope_state:
                envelope_state = None
            if code in _PENDING_CODES or code in _DEGRADED_CODES:
                # A degraded cancel must not be reported as STARTING when the
                # durable row says otherwise.
                durable, source = _durable_state(
                    lifecycle, run_id, envelope_state
                )
                kind = (
                    StopOutcomeKind.LAUNCH_CANCEL_PENDING
                    if code in _PENDING_CODES
                    else StopOutcomeKind.LAUNCH_CANCEL_DEGRADED
                )
                events.append(
                    _event(
                        "stop_fail_closed",
                        code=code,
                        durable_state=durable,
                        state_source=source,
                    )
                )
                return _result(
                    run_id=run_id,
                    events=events,
                    outcome=kind,
                    cadence=cadence,
                    message=(
                        f"stop failed fail-closed: {code}; "
                        f"durable_state={durable} ({source})"
                    ),
                    stop_state=durable,
                    stop_error_code=code,
                    stop_method=stop_result.get("stop_method"),
                    t_decision=t_decision,
                    t_stop_return=t_stop_return,
                )
            if code in _SESSION_BUSY_CODES:
                kind = StopOutcomeKind.STOP_SESSION_BUSY
            elif code in _REFUSED_CODES:
                kind = StopOutcomeKind.STOP_REFUSED_STATE
            else:
                kind = StopOutcomeKind.STOP_FAILED
            events.append(
                _event(
                    "stop_fail_closed",
                    code=code,
                    state=envelope_state,
                    detail=stop_result.get("detail"),
                )
            )
            detail = stop_result.get("detail")
            return _result(
                run_id=run_id,
                events=events,
                outcome=kind,
                cadence=cadence,
                message=(
                    f"stop failed fail-closed: {code}"
                    + (f"; {detail}" if detail else "")
                ),
                stop_state=envelope_state,
                stop_error_code=code,
                stop_method=stop_result.get("stop_method"),
                t_decision=t_decision,
                t_stop_return=t_stop_return,
            )

        after = None
        after_error: str | None = None
        if require_pids_zero_after_ok_stop:
            probe = list(pids_zero_probe) if pids_zero_probe is not None else pids
            try:
                after = lifecycle.get_run(run_id)
            except Exception as error:
                after_error = type(error).__name__
            if after_error is not None:
                events.append(
                    _event(
                        "pids_zero_check",
                        error=after_error,
                        remaining=None,
                    )
                )
                return _result(
                    run_id=run_id,
                    events=events,
                    outcome=StopOutcomeKind.STOP_FAILED,
                    cadence=cadence,
                    message=(
                        "stop ok but lifecycle re-read failed; "
                        "NOT claiming real shutdown"
                    ),
                    stop_state=stop_result.get("state"),
                    stop_error_code=None,
                    stop_method=stop_result.get("stop_method"),
                    t_decision=t_decision,
                    t_stop_return=t_stop_return,
                )
            if after is None:
                events.append(
                    _event(
                        "pids_zero_check",
                        run_missing=True,
                        remaining=None,
                    )
                )
                return _result(
                    run_id=run_id,
                    events=events,
                    outcome=StopOutcomeKind.STOP_FAILED,
                    cadence=cadence,
                    message=(
                        "stop ok but run row missing after stop; "
                        "cannot re-read owned pid+creation; "
                        "NOT claiming real shutdown"
                    ),
                    stop_state=stop_result.get("state"),
                    stop_error_code=None,
                    stop_method=stop_result.get("stop_method"),
                    t_decision=t_decision,
                    t_stop_return=t_stop_return,
                )
            else:
                # N4: remaining means the same (pid, creation_time_utc)
                # identity is still on the durable row. A reused PID with a
                # new creation is not the owned process. Missing creation on
                # a previously owned PID is fail-closed (still remaining).
                remaining = []
                owned_pids_at_decision = {pid for pid, _ in owned_at_decision}
                for proc in after.processes:
                    if probe and proc.pid not in probe:
                        continue
                    reason = reject_foreign_pid(
                        proc.pid, proc.creation_time_utc, owned_at_decision
                    )
                    if reason is None or (
                        reason == "no_creation_time_attribution"
                        and proc.pid in owned_pids_at_decision
                    ):
                        remaining.append(proc.pid)
                events.append(
                    _event(
                        "pids_zero_check",
                        run_missing=False,
                        remaining=remaining,
                    )
                )
                if remaining:
                    return _result(
                        run_id=run_id,
                        events=events,
                        outcome=StopOutcomeKind.STOP_FAILED,
                        cadence=cadence,
                        message=(
                            "stop ok but owned pids still present — "
                            "NOT claiming real shutdown"
                        ),
                        stop_state=stop_result.get("state"),
                        stop_error_code=None,
                        stop_method=stop_result.get("stop_method"),
                        t_decision=t_decision,
                        t_stop_return=t_stop_return,
                    )

        final_state = stop_result.get("state")
        if not isinstance(final_state, str) or not final_state:
            final_state = after.state if after is not None else None
        events.append(
            _event(
                "shutdown_claimed",
                stop_method=stop_result.get("stop_method"),
                durable_state=final_state,
                storage_rollback_claimed=False,
            )
        )
        return _result(
            run_id=run_id,
            events=events,
            outcome=StopOutcomeKind.SUCCEEDED_PIDS_ZERO,
            cadence=cadence,
            message=(
                "stop ok and owned pids cleared"
                + ("" if after is not None else " (run row missing after stop)")
                + "; storage rollback not claimed (S3)"
            ),
            claimed=True,
            stop_state=final_state,
            stop_error_code=None,
            stop_method=stop_result.get("stop_method"),
            t_decision=t_decision,
            t_stop_return=t_stop_return,
        )

    events.append(_event("max_ticks_reached"))
    return _result(
        run_id=run_id,
        events=events,
        outcome=StopOutcomeKind.NO_OWNED_PIDS,
        cadence=cadence,
        message="completed without threshold breach",
    )


def assert_candidate_belongs(run_snap, pid: int, creation: str) -> None:
    owned = index_owned(run_snap)
    reason = reject_foreign_pid(pid, creation, owned)
    if reason:
        raise PermissionError(reason)
