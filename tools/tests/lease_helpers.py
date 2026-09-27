"""Fakes shared by the lease and coordination tests.

Moved verbatim from test_session_coordination.py, test_session_http.py
and test_bug046_lease_queue_liveness.py so tests stop importing each
other (review 2026-09-25, W4d step 1). Unifying them with the
near-duplicates in lifecycle_helpers is a later, separate step.
"""
from __future__ import annotations

import threading

from dayz_mcp.session_coordination import (
    LEASE_GRACE_S,
    SESSION_TTL_S,
    ClientIdentity,
    SessionCoordinator,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class SequentialIds:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.next_value = 1

    def __call__(self) -> str:
        value = f"{self.prefix}-{self.next_value}"
        self.next_value += 1
        return value


class AuditSink:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.fail_events: set[str] = set()

    def __call__(self, event: dict[str, object]) -> None:
        if event.get("event") in self.fail_events:
            raise OSError("audit unavailable")
        self.events.append(dict(event))


class CleanupSink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []

    def __call__(
        self, session_id: str, _lease_id: str, reason: str, vehicle_active: bool
    ) -> dict[str, object]:
        self.calls.append((session_id, reason, vehicle_active))
        return {"cancelled": 0, "vehicle_release": vehicle_active}


def _identity(name: str, *, session_id: str | None = None) -> ClientIdentity:
    return ClientIdentity.from_payload(
        {
            "platform": "codex" if name != "b" else "claude",
            "pid": 100 + ord(name[0]),
            "ppid": 10,
            "started_at_utc": f"2026-07-14T20:00:0{len(name)}Z",
            "session_id": session_id or f"session-{name}",
            "task_label": name,
        }
    )


class SnapshotStore:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []
        self.raise_on_write = False
        self.return_value = True
        self.lock_probe: SessionCoordinator | None = None

    def persisted_revision(self) -> int | None:
        if not self.payloads:
            return None
        revision = self.payloads[-1].get("revision")
        if isinstance(revision, int) and not isinstance(revision, bool):
            return revision
        return None

    def write_coordination(self, payload: dict[str, object]) -> bool:
        self.payloads.append(payload)
        if self.lock_probe is not None:
            acquired: list[bool] = []

            def probe() -> None:
                condition = self.lock_probe._condition  # type: ignore[attr-defined]
                locked = condition.acquire(timeout=0.5)
                acquired.append(locked)
                if locked:
                    condition.release()

            thread = threading.Thread(target=probe)
            thread.start()
            thread.join(timeout=1.0)
            if acquired != [True]:
                raise RuntimeError("snapshot_ran_under_coordinator_condition")
        if self.raise_on_write:
            raise OSError("snapshot write failed")
        return self.return_value


def parse_dpf_table(markdown: str, heading: str) -> dict[str, dict[str, str]]:
    """Return the named DPF table as structured criterion records."""

    lines = markdown.splitlines()
    try:
        start = lines.index(heading)
    except ValueError as error:
        raise AssertionError(f"missing DPF heading: {heading}") from error

    header_index = start + 1
    while header_index < len(lines) and not lines[header_index].startswith("| # |"):
        header_index += 1
    if header_index == len(lines):
        raise AssertionError(f"missing DPF table after: {heading}")

    rows: dict[str, dict[str, str]] = {}
    for line in lines[header_index + 2 :]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4:
            raise AssertionError(f"malformed DPF row: {line}")
        key, criterion, verification, state = cells
        if key in rows:
            raise AssertionError(f"duplicate DPF criterion: {key}")
        rows[key] = {
            "criterion": criterion,
            "verification": verification,
            "state": state,
        }
    return rows


# --- from test_0ab2_r9.py: shared by several domain files (W4d step 3) ---
TTL = SESSION_TTL_S
G = LEASE_GRACE_S
MID = TTL + (G / 2.0)


def _coord(clock: FakeClock, *, attached: bool, cleanup=None) -> SessionCoordinator:
    return SessionCoordinator(
        time_fn=clock,
        token_fn=SequentialIds("token"),
        id_fn=SequentialIds("id"),
        audit=AuditSink(),
        cleanup=cleanup or CleanupSink(),
        attached_run_probe=lambda _session, _lease: attached,
    )
