"""Process-lifecycle fakes and identities shared by the task7 tests.

Moved verbatim from test_task7_review_regressions.py and
test_task7_rereview_regressions.py so tests stop importing each other
(review 2026-09-25, W4d step 1).
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from dayz_mcp.process_lifecycle import (
    ProcessLifecycle,
    ProcessRecord,
    RunManifestStore,
    RunRecord,
)
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator
from tests.steam_helpers import FakeSteamGate


IDENTITY = ClientIdentity(
    "codex", 11, 1, "2026-07-15T00:00:00Z", "same-session", "task7-red"
)
IDENTITY_PAYLOAD = IDENTITY.to_payload()
IDENTITY_B = ClientIdentity(
    "claude", 22, 1, "2026-07-15T00:00:01Z", "other-session", "fifo"
)
HASH_A = "a" * 64
HASH_B = "b" * 64


class Sequence:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"{self.prefix}-{self.value}"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.fail_events: set[str] = set()
        self.on_event = None

    def __call__(self, event: dict[str, object]) -> bool:
        self.events.append(dict(event))
        if self.on_event is not None:
            self.on_event(event)
        return event.get("event") not in self.fail_events


class Handle:
    def __init__(self, pid: int = 7001, *, confirmed_exit: bool = True) -> None:
        self.pid = pid
        self.confirmed_exit = confirmed_exit
        self.terminate_calls = 0

    def terminate(self) -> None:
        self.terminate_calls += 1

    def wait(self, timeout: float):
        if not self.confirmed_exit:
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0

    def poll(self):
        return 0 if self.confirmed_exit else None


class Launcher:
    def __init__(self, handle: Handle | None = None) -> None:
        self.handle = handle or Handle()
        self.calls: list[tuple[list[str], str, str]] = []

    def __call__(self, argv: list[str], cwd: str, window_style: str) -> Handle:
        self.calls.append((list(argv), cwd, window_style))
        return self.handle


class Guard:
    def __init__(self) -> None:
        self.snapshots: dict[int, dict[str, object]] = {}
        self.snapshot_calls: list[int] = []
        self.terminate_calls: list[ProcessRecord] = []
        self.terminate_results: list[dict[str, object]] = []

    def snapshot(self, pid: int) -> dict[str, object]:
        self.snapshot_calls.append(pid)
        return dict(self.snapshots.get(pid, {"error": "identity_unavailable", "exit_code": 3}))

    def terminate(self, record: ProcessRecord) -> dict[str, object]:
        self.terminate_calls.append(record)
        if self.terminate_results:
            return self.terminate_results.pop(0)
        return {"terminated": True, "exit_code": 0}


def record(pid: int, role: str = "client") -> ProcessRecord:
    return ProcessRecord(
        pid,
        f"2026-07-15T00:00:{pid % 60:02d}.0000000Z",
        HASH_A,
        HASH_B,
        role,
        identity_scheme="psutil-argv-v2",
    )


def identity(record_value: ProcessRecord) -> dict[str, object]:
    return {
        "pid": record_value.pid,
        "creation_time_utc": record_value.creation_time_utc,
        "executable_sha256": record_value.executable_sha256,
        "command_line_sha256": record_value.command_line_sha256,
        "identity_scheme": record_value.identity_scheme,
        "identity_complete": True,
    }


class LifecycleFixture:
    def __init__(self, *, confirmed_exit: bool = True) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        for name in ("DayZDiag_x64.exe", "DayZ_BE.exe", "DayZ_x64.exe"):
            (self.game / name).write_bytes(b"")
        self.paths = RuntimePaths(
            self.root / "runtime",
            self.root / "runtime" / "audit",
            self.root / "runtime" / "coordination.json",
            self.root / "runtime" / "runs.json",
        )
        self.audit = Audit()
        self.coordinator = SessionCoordinator(
            token_fn=Sequence("token"),
            id_fn=Sequence("lease"),
            audit=self.audit,
        )
        status, acquired = self.coordinator.acquire(IDENTITY, "task7")
        assert status == 200
        self.token = acquired["lease_token"]
        self.lease_id = acquired["lease_id"]
        self.store = RunManifestStore(self.paths)
        self.guard = Guard()
        self.launcher = Launcher(Handle(confirmed_exit=confirmed_exit))
        self.probe = {"known": True, "processes": []}
        self.lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: self.probe,
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=self.launcher,
            id_fn=Sequence("run"),
        )
        # fb-20260904-200816-79e2: superseding a live client needs the witness
        # of the extension gate AND a bridge row that still agrees when
        # start_run revalidates it. The daemon always wires this probe
        # (daemon.py:545). The default row is a client that has not polled for
        # a long time -- the state the gate acts on -- so the settlement tests
        # written before the witness keep measuring what they measured. The
        # refusal branches have their own tests in test_lifecycle_reconcile.
        self.peer_row: dict[str, object] = {
            "last_poll_age_s": 999.0,
            "bound_last_poll_age_s": None,
            "binding_state": None,
        }
        self.lifecycle.bridge_probe = lambda now=None: {
            "peers": {"client": dict(self.peer_row)}
        }
        # start_run ages the witness against time.time()
        # (process_lifecycle.py:1902-1903). time.time() is not monotonic: a 1ms
        # step back is already replace_witness_stale, and 60s of delay trips
        # the published bound. Pin both sides of that comparison so a loaded
        # machine still reaches settlement instead of the gate.
        self._witness_now = time.time()
        lifecycle = self.lifecycle

        def _frozen_witness(parsed, *, now):
            now = self._witness_now
            return ProcessLifecycle._replacement_witness_error(
                lifecycle, parsed, now=now
            )

        lifecycle._replacement_witness_error = _frozen_witness  # type: ignore[method-assign]

    def close(self) -> None:
        self.temporary.cleanup()

    def request(self, *, run_id: str | None = None) -> dict[str, object]:
        value: dict[str, object] = {
            "argv": [str(self.game / "DayZDiag_x64.exe"), "-mission=test"],
            "cwd": str(self.game),
            "role": "client",
            "window_style": "normal",
            "label": "red",
            "mod": "@SameMod",
            "profiles": "profiles",
            "mission": "test",
        }
        if run_id is not None:
            value["run_id"] = run_id
            # fb-20260904-200816-79e2: dayz_test_worker._start_core stamps the
            # witness on exactly this shape and no other -- a client relaunch
            # over an existing run (dayz_test_worker.py:305-323). A request
            # without it is refused with replace_witness_missing before a
            # process is touched, so a settlement test would never reach the
            # settlement it measures.
            value["replace_if_not_polling_since"] = int(self._witness_now * 1000)
        return value

    def add_run(
        self,
        process: ProcessRecord,
        *,
        run_id: str = "existing",
        state: str = "RUNNING",
        owned: bool = True,
    ) -> RunRecord:
        run = RunRecord(
            run_id,
            IDENTITY.session_id if owned else None,
            self.lease_id if owned else None,
            state,
            "red",
            "@SameMod",
            "profiles",
            "mission",
            [process],
        )
        self.store.add(run)
        return run

    def release_and_reacquire(self) -> tuple[str, str]:
        status, _ = self.coordinator.release(IDENTITY, self.token)
        assert status == 200
        status, acquired = self.coordinator.acquire(IDENTITY, "replacement")
        assert status == 200
        return acquired["lease_token"], acquired["lease_id"]


class LifecycleFixtureContext(LifecycleFixture):
    def __enter__(self) -> "LifecycleFixtureContext":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
