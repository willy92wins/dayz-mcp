"""Shared starting-cancel fixture for ProcessLifecycle STARTING-window tests.

Moved verbatim from test_starting_cancel.py so test_starting_spawn_window
stops importing that test module (CrossTestImportRatchetTest, CI shard 1/4
on #368). test_starting_cancel imports these names back unchanged.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore, RunRecord
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.dayz_test_tool_helpers import RUN_ID
from tests.lifecycle_helpers import stamp_launcher
from tests.process_lifecycle_helpers import (
    AuditSink,
    FakeGuard,
    IDENTITY_A,
    process,
    snapshot,
)
from tests.steam_helpers import FakeSteamGate

_OP_ID = "abcdefab-cdef-4abc-8def-abcdefabcdef"


class _BlockingLauncher:
    """A launcher that holds the launch window open until the test releases it."""

    def __init__(
        self,
        pid: int = 91001,
        *,
        confirmed_exit: bool = True,
        wait_delay: float = 0.0,
    ) -> None:
        self.pid = pid
        self.confirmed_exit = confirmed_exit
        self.wait_delay = wait_delay
        self.entered = threading.Event()
        self.release = threading.Event()
        self.terminate_calls = 0

    def __call__(self, argv: list[str], cwd: str, window_style: str):
        self.entered.set()
        if not self.release.wait(5.0):
            raise TimeoutError("launcher held too long")
        return self

    def terminate(self) -> None:
        self.terminate_calls += 1

    def wait(self, timeout: float) -> int:
        if self.wait_delay:
            time.sleep(self.wait_delay)
        if not self.confirmed_exit:
            raise TimeoutError("still running")
        return 0

    def poll(self):
        return 0 if self.confirmed_exit else None


class _Bindings:
    """The one binding method a cancelled launch touches."""

    def __init__(self) -> None:
        self.minted = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        self.retired: list[tuple[str, str, str]] = []

    def prepare(self, run_id: str, role: str, profiles: str) -> str:
        return self.minted

    def retire_role(self, run_id: str, role: str, reason: str) -> None:
        self.retired.append((run_id, role, reason))

    def retire_run(self, run_id: str, reason: str) -> None:
        return None


def _fresh_request(game: Path, mission: Path) -> dict[str, object]:
    request: dict[str, object] = {
        "argv": [str(game / "DayZDiag_x64.exe"), "-mission=" + str(mission)],
        "cwd": str(game),
        "role": "offline",
        "window_style": "hidden",
        "label": "red",
        "mod": "@StorageMod",
        "profiles": "profiles",
        "mission": str(mission),
        "storage_seal": "a" * 64,
        "new_run_id": RUN_ID,
        "launch_operation_id": _OP_ID,
    }
    canonical = json.dumps(
        request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    request["launch_request_sha256"] = hashlib.sha256(canonical).hexdigest()
    return request


class _Fixture:
    def __init__(
        self,
        *,
        confirmed_exit: bool = True,
        bindings: object | None = None,
    ) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        (self.game / "DayZDiag_x64.exe").write_bytes(b"")
        self.mission = self.root / "mpmissions" / "dayzOffline.chernarusplus"
        self.mission.mkdir(parents=True)
        self.paths = RuntimePaths(
            self.root / "runtime",
            self.root / "runtime" / "audit",
            self.root / "runtime" / "coordination.json",
            self.root / "runtime" / "runs.json",
        )
        self.audit = AuditSink()
        self.sequence = 0

        def token_fn() -> str:
            self.sequence += 1
            return f"token-{self.sequence}"

        def id_fn() -> str:
            return f"lease-{self.sequence}"

        self.coordinator = SessionCoordinator(
            token_fn=token_fn, id_fn=id_fn, audit=self.audit
        )
        status, acquired = self.coordinator.acquire(IDENTITY_A, "cancel")
        assert status == 200
        self.token = acquired["lease_token"]
        self.lease_id = acquired["lease_id"]
        self.store = RunManifestStore(self.paths)
        self.guard = FakeGuard()
        self.launcher = _BlockingLauncher(confirmed_exit=confirmed_exit)
        self.bindings = bindings
        self.lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=self.launcher,
            id_fn=lambda: "run-1",
            bindings=bindings,
        )
        self.lifecycle._starting_cancel_settle_s = 1.0
        self.request = _fresh_request(self.game, self.mission)
        self.guard.snapshots[self.launcher.pid] = snapshot(
            process(self.launcher.pid, role="offline")
        )

    def close(self) -> None:
        self.temporary.cleanup()

    def add_run(
        self,
        records: list[object],
        *,
        run_id: str = "run-existing",
        state: str = "RUNNING",
        owner: str | None = IDENTITY_A.session_id,
        lease: str | None = None,
    ) -> RunRecord:
        run = RunRecord(
            run_id,
            owner,
            lease if lease is not None else (self.lease_id if owner else None),
            state,
            "same",
            "@StorageMod",
            "profiles",
            "mission",
            list(records),
        )
        self.store.add(run)
        # The run was launched by A even when it is ownerless now: the adopt
        # gate uses that attribution to tell an idle run from a stranger's.
        stamp_launcher(self.lifecycle, run.run_id, IDENTITY_A)
        return run

    def wait_for_cancel_request(self, timeout: float = 2.0) -> bool:
        """Wait until the stop has signalled the in-flight launch.

        The launcher is released only after this, so the test cannot race the
        stop: the launch is guaranteed to be inside its STARTING window when
        the cancel arrives, and the stop is guaranteed to have reached the
        daemon's cancel branch before the launch can settle.
        """
        lookup = getattr(self.lifecycle, "_launch_cancel_for", None)
        if lookup is None:
            # BASE has no launch-cancel registry: there is nothing to wait for,
            # and the release below lets the launch settle so the feature
            # assertions can fail for the real reason.
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cancel = lookup(RUN_ID)
            if cancel is not None and cancel.requested.is_set():
                return True
            time.sleep(0.005)
        return False

    def start_in_thread(self) -> threading.Thread:
        self.start_result: dict[str, object] = {}

        def run() -> None:
            self.start_result["value"] = self.lifecycle.start_run(
                IDENTITY_A, self.token, self.request
            )

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        assert self.launcher.entered.wait(2.0), "launch never reached the launcher"
        return thread

    def stop_in_thread(
        self,
        client: object = IDENTITY_A,
        token: str | None = None,
    ) -> threading.Thread:
        self.stop_result: dict[str, object] = {}
        chosen_token = self.token if token is None else token

        def run() -> None:
            self.stop_result["value"] = self.lifecycle.stop_run(
                client, chosen_token, RUN_ID
            )

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread
