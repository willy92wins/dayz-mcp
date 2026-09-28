"""Process-lifecycle fakes shared by many tests.

Moved verbatim from test_process_lifecycle.py so tests stop importing each other
(review 2026-09-25, T1). HASH_A and HASH_B come from lifecycle_helpers, where
the same values already lived.
"""
from __future__ import annotations

from dayz_mcp.process_lifecycle import ProcessRecord
from dayz_mcp.session_coordination import ClientIdentity

from tests.lifecycle_helpers import HASH_A, HASH_B


IDENTITY_A = ClientIdentity("codex", 11, 1, "2026-07-15T00:00:00Z", "A", "owner")


IDENTITY_B = ClientIdentity("claude", 22, 2, "2026-07-15T00:00:01Z", "B", "other")


class AuditSink:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.fail_events: set[str] = set()

    def __call__(self, event: dict[str, object]) -> bool:
        self.events.append(event)
        return event.get("event") not in self.fail_events


class FakeLauncher:
    def __init__(self, pid: int = 9001) -> None:
        self.pid = pid
        self.calls: list[tuple[list[str], str, str]] = []
        self.terminated: list[int] = []
        self.confirmed_exit = True

    def __call__(self, argv: list[str], cwd: str, window_style: str):
        self.calls.append((list(argv), cwd, window_style))
        return self

    def terminate(self) -> None:
        self.terminated.append(self.pid)

    def wait(self, timeout: float) -> None:
        if not self.confirmed_exit:
            raise TimeoutError("still running")
        return

    def poll(self):
        return 0 if self.confirmed_exit else None


class FakeGuard:
    def __init__(self) -> None:
        self.snapshots: dict[int, dict[str, object]] = {}
        self.snapshot_calls: list[int] = []
        self.terminate_calls: list[ProcessRecord] = []
        self.terminate_results: list[dict[str, object]] = []

    def snapshot(self, pid: int) -> dict[str, object]:
        self.snapshot_calls.append(pid)
        return dict(self.snapshots.get(pid, {"error": "identity_unavailable"}))

    def terminate(self, record: ProcessRecord) -> dict[str, object]:
        self.terminate_calls.append(record)
        if self.terminate_results:
            return self.terminate_results.pop(0)
        return {"terminated": True}


def process(pid: int, role: str = "client") -> ProcessRecord:
    return ProcessRecord(
        pid,
        f"2026-07-15T00:00:{pid % 60:02d}.0000000Z",
        HASH_A,
        HASH_B,
        role,
        identity_scheme="psutil-argv-v2",
    )


def legacy_process(pid: int, role: str = "client") -> ProcessRecord:
    return ProcessRecord(
        pid,
        f"2026-07-15T00:00:{pid % 60:02d}.0000000Z",
        HASH_A,
        HASH_B,
        role,
    )


def snapshot(record: ProcessRecord) -> dict[str, object]:
    return {
        "pid": record.pid,
        "creation_time_utc": record.creation_time_utc,
        "executable_sha256": record.executable_sha256,
        "command_line_sha256": record.command_line_sha256,
        "identity_scheme": record.identity_scheme,
        "identity_complete": True,
    }
