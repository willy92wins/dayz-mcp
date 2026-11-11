from __future__ import annotations

from .types import ProcessIdentity, RunSnapshot


def index_owned(snapshot: RunSnapshot) -> dict[tuple[int, str], ProcessIdentity]:
    """Key = (pid, creation_time_utc). Reused PID with different creation is another key."""
    out: dict[tuple[int, str], ProcessIdentity] = {}
    for proc in snapshot.processes:
        if proc.pid <= 0 or not proc.creation_time_utc:
            continue
        out[(proc.pid, proc.creation_time_utc)] = proc
    return out


def reject_foreign_pid(
    candidate_pid: int,
    candidate_creation: str | None,
    owned: dict[tuple[int, str], ProcessIdentity],
) -> str | None:
    """Return refusal reason or None if belonging is exact."""
    if candidate_creation is None or candidate_creation == "":
        return "no_creation_time_attribution"
    key = (candidate_pid, candidate_creation)
    if key not in owned:
        # Same pid different creation => reuse
        if any(pid == candidate_pid for pid, _ in owned):
            return "pid_reused_different_creation"
        return "foreign_pid"
    return None


def owned_pids(snapshot: RunSnapshot) -> list[int]:
    return [p.pid for p in snapshot.processes if p.pid > 0 and p.creation_time_utc]
