"""Concrete adapters for the supervisor: CIM/GPI host metrics, the real
lifecycle surface, and the public FastMCP ``dayz_test_stop`` tool.

No launcher or Steam fake lives here. The stop adapter always names the run
explicitly (N1) and reads the durable row the product returns; it never turns
``launch_cancel_degraded`` into a clean shutdown and never claims a storage
rollback (S3).
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from typing import Any, Sequence

from .types import HostSample, ProcessIdentity, RunSnapshot
from .windows_metrics import (
    GpiUnavailable,
    kib_to_bytes,
    read_cim_free_physical_kib,
    read_cim_process_memory,
    read_performance_info,
    utc_now_iso,
)

_TOKEN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
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


def _token(error: BaseException) -> str:
    if isinstance(error, OSError):
        number = getattr(error, "winerror", None) or error.errno or 0
        return f"os_error_{number}"
    text = str(error).split(":", 1)[0].strip()
    if _TOKEN.fullmatch(text):
        return text
    return type(error).__name__.lower()


def _error_code(error: BaseException) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        return code
    token = str(error).split(":", 1)[0].strip()
    if _TOKEN.fullmatch(token):
        return token
    return "stop_transport_failed"


def _tool_payload(result: Any) -> dict[str, Any]:
    if isinstance(result, tuple):
        blocks, structured = result
        if isinstance(structured, dict):
            return structured
        result = blocks
    if isinstance(result, dict):
        return result
    if isinstance(result, list) and result:
        first = result[0]
        text = getattr(first, "text", None)
        if text is None and isinstance(first, dict):
            text = first.get("text")
        if isinstance(text, str):
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
    raise ValueError("unexpected_dayz_test_stop_result")


class AsyncRunner:
    """A private event loop thread for calling the product's async surface."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._serve, name="supervisor-async", daemon=True
        )
        self._closed = False
        self._thread.start()

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coroutine: Any, timeout_s: float) -> Any:
        if self._closed:
            raise RuntimeError("async_runner_closed")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        return future.result(timeout_s)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2.0)
        if not self._loop.is_closed():
            self._loop.close()


class RealHostMetrics:
    """CIM FreePhysicalMemory + Win32_Process, and psapi GPI commit figures."""

    def sample(self, pids: Sequence[int]) -> HostSample:
        now = time.monotonic()
        wall = utc_now_iso()
        requested = tuple(int(pid) for pid in pids)
        try:
            free_kib = read_cim_free_physical_kib()
            free_bytes = kib_to_bytes(free_kib)
            memory = read_cim_process_memory(requested)
        except Exception as error:
            return HostSample(
                free_physical_bytes=None,
                commit_total_bytes=None,
                commit_limit_bytes=None,
                private_bytes_by_pid={},
                observed_at_monotonic=now,
                ok=False,
                error=_token(error),
                observed_at_wall_iso=wall,
            )
        private = {pid: memory[pid][0] for pid in requested}
        working_set = {pid: memory[pid][1] for pid in requested}
        commit_total: int | None = None
        commit_limit: int | None = None
        physical_available: int | None = None
        gpi_available: bool | None = None
        gpi_error: str | None = None
        try:
            gpi = read_performance_info()
        except GpiUnavailable as error:
            # Documented degradation: FreePhysical + Private stay the RAM gate;
            # commit is absent, never invented.
            gpi_available = False
            gpi_error = _token(error)
        except Exception as error:
            return HostSample(
                free_physical_bytes=free_bytes,
                commit_total_bytes=None,
                commit_limit_bytes=None,
                private_bytes_by_pid=private,
                observed_at_monotonic=now,
                ok=False,
                error=f"gpi_{_token(error)}",
                free_physical_kib=free_kib,
                working_set_bytes_by_pid=working_set,
                gpi_available=False,
                gpi_error=_token(error),
                observed_at_wall_iso=wall,
            )
        else:
            gpi_available = True
            commit_total = gpi["commit_total_bytes"]
            commit_limit = gpi["commit_limit_bytes"]
            physical_available = gpi["physical_available_bytes"]
        return HostSample(
            free_physical_bytes=free_bytes,
            commit_total_bytes=commit_total,
            commit_limit_bytes=commit_limit,
            private_bytes_by_pid=private,
            observed_at_monotonic=now,
            ok=True,
            error=None,
            free_physical_kib=free_kib,
            working_set_bytes_by_pid=working_set,
            physical_available_bytes=physical_available,
            gpi_available=gpi_available,
            gpi_error=gpi_error,
            observed_at_wall_iso=wall,
        )


class LifecycleUnavailable(RuntimeError):
    """The real lifecycle surface could not answer; never a guessed snapshot."""


def _snapshot_from_status(status: Any, run_id: str) -> RunSnapshot | None:
    if not isinstance(status, dict):
        raise LifecycleUnavailable("status_not_a_dict")
    runs = status.get("runs")
    if not isinstance(runs, list):
        raise LifecycleUnavailable("status_runs_missing")
    for row in runs:
        if not isinstance(row, dict) or row.get("run_id") != run_id:
            continue
        state = row.get("state")
        if not isinstance(state, str) or not state:
            raise LifecycleUnavailable("run_state_invalid")
        raw_processes = row.get("processes")
        if not isinstance(raw_processes, list):
            raise LifecycleUnavailable("run_processes_invalid")
        processes: list[ProcessIdentity] = []
        for item in raw_processes:
            if not isinstance(item, dict):
                raise LifecycleUnavailable("process_row_invalid")
            pid = item.get("pid")
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                raise LifecycleUnavailable("process_pid_invalid")
            creation = item.get("creation_time_utc")
            creation_text = creation if isinstance(creation, str) else ""
            role = item.get("role")
            scheme = item.get("identity_scheme")
            processes.append(
                ProcessIdentity(
                    pid=pid,
                    creation_time_utc=creation_text,
                    path=None,
                    role=role if isinstance(role, str) and role else None,
                    identity_scheme=(
                        scheme if isinstance(scheme, str) and scheme else None
                    ),
                )
            )
        return RunSnapshot(
            run_id=run_id,
            state=state,
            processes=tuple(processes),
            observed_at_monotonic=time.monotonic(),
        )
    return None


class RealLifecycle:
    """Reads run_id/state/processes from the product's lifecycle surface."""

    def __init__(
        self,
        runtime: Any,
        *,
        runner: AsyncRunner | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self._runtime = runtime
        self._runner = runner if runner is not None else AsyncRunner()
        self._owns_runner = runner is None
        self._timeout_s = timeout_s

    def get_run(self, run_id: str) -> RunSnapshot | None:
        if not isinstance(run_id, str) or not run_id:
            raise LifecycleUnavailable("run_id_required")
        try:
            status = self._runner.run(
                self._runtime.lifecycle_status(), self._timeout_s
            )
        except Exception as error:
            raise LifecycleUnavailable(_token(error)) from error
        return _snapshot_from_status(status, run_id)

    def close(self) -> None:
        if self._owns_runner:
            self._runner.close()


class RealStop:
    """Calls the public FastMCP ``dayz_test_stop`` tool with an explicit run_id.

    The call crosses ClientRuntime -> ControlClient -> loopback
    ``/lifecycle/stop`` exactly as the G7 acceptance path does. It never uses
    ``execute_dayz_test_stop(..., direct_inflight=True)`` directly.
    """

    def __init__(
        self,
        app: Any,
        *,
        runner: AsyncRunner | None = None,
        timeout_s: float = 40.0,
    ) -> None:
        self._app = app
        self._runner = runner if runner is not None else AsyncRunner()
        self._owns_runner = runner is None
        self._timeout_s = timeout_s
        self.calls: list[str] = []

    def stop(self, run_id: str) -> dict[str, Any]:
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("real stop requires an explicit run_id (N1)")
        self.calls.append(run_id)
        try:
            raw = self._runner.run(
                self._app.call_tool("dayz_test_stop", {"run_id": run_id}),
                self._timeout_s,
            )
            payload = _tool_payload(raw)
        except Exception as error:
            return {
                "ok": False,
                "error_code": _error_code(error),
                "stop_method": None,
                "raw_status": "failed",
                "state": None,
                "run_id": run_id,
                "terminated": None,
                "cleanup_degraded": None,
                "detail": f"{type(error).__name__}: {str(error)[:300]}",
            }
        status = payload.get("status")
        ok = status == "succeeded"
        error_code = payload.get("error_code")
        stop_method = payload.get("stop_method")
        state = payload.get("state")
        returned_run_id = payload.get("run_id")
        terminated = payload.get("terminated")
        cleanup_degraded = payload.get("cleanup_degraded")
        reason = payload.get("reason")
        return {
            "ok": ok,
            "error_code": error_code if isinstance(error_code, str) else None,
            "stop_method": stop_method if isinstance(stop_method, str) else None,
            "raw_status": status if isinstance(status, str) else "unknown",
            "state": state if isinstance(state, str) else None,
            "run_id": (
                returned_run_id
                if isinstance(returned_run_id, str)
                else run_id
            ),
            "terminated": (
                terminated
                if isinstance(terminated, int) and not isinstance(terminated, bool)
                else None
            ),
            "cleanup_degraded": (
                cleanup_degraded
                if isinstance(cleanup_degraded, bool)
                else None
            ),
            "reason": reason if isinstance(reason, str) else None,
        }

    def close(self) -> None:
        if self._owns_runner:
            self._runner.close()


__all__ = [
    "AsyncRunner",
    "LifecycleUnavailable",
    "RealHostMetrics",
    "RealLifecycle",
    "RealStop",
    "_DEGRADED_CODES",
    "_PENDING_CODES",
    "_REFUSED_CODES",
    "_SESSION_BUSY_CODES",
]
