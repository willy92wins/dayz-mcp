"""Client extension admission, active-run client diagnostics, camera settle deadline.

Public dayz_test_run is driven with a fake session_status and a fake
execute/stop boundary. The extension gate and the replacement decision are
the real functions. Camera settle steps a runner with the constants read
from the bridge source. No DayZ process is started.
"""
from __future__ import annotations

import asyncio
import re
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import server as server_module
from dayz_mcp.dayz_test_tool import (
    CLIENT_ALREADY_POLLING,
    DayzTestToolError,
    _client_record_from_status,
    _decide_client_replacement,
    require_extension_run,
)
from dayz_mcp.loopback import validate_command_args
from dayz_mcp.process_lifecycle import (
    ProcessLifecycle,
    RunManifestStore,
    RunRecord,
    takeover_target_run_id,
)
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.server import ServerConfig, TAKEOVER_REQUIRED
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator
from tests.client_helpers import _fixture_client_runtime
from tests.lifecycle_helpers import HASH_A, HASH_B, Sequence, stamp_launcher
from tests.mcp_helpers import _content_json
from tests.process_lifecycle_helpers import AuditSink, FakeGuard, FakeLauncher, process, snapshot
from tests.steam_helpers import FakeSteamGate


_RUN = "11111111-1111-4111-8111-111111111111"
_OTHER = "22222222-2222-4222-8222-222222222222"
_PROJECT = "ExampleMod"
_MOD = "@ExampleMod"
_POLICY = SimpleNamespace(mod=_PROJECT, dev_root=r"P:\ExampleMod_Suite")
_BRIDGE = (
    Path(__file__).resolve().parents[2]
    / "addon"
    / "scripts"
    / "5_Mission"
    / "MCPClientBridge.c"
)
_CREATED = "2026-07-15T00:00:01.000000Z"
_SERVER_PID = 801
_CLIENT_PID = 802


def _bridge_float(name: str) -> float:
    text = _BRIDGE.read_text(encoding="utf-8")
    match = re.search(rf"{name} = ([0-9.]+);", text)
    if match is None:
        raise AssertionError(name)
    return float(match.group(1))


def _bridge_int(name: str) -> int:
    return int(_bridge_float(name))


class _RunnerJob:
    def __init__(self, settle_s: float, slack_s: float) -> None:
        self.sample_s_target = settle_s
        self.deadline_s = settle_s + slack_s
        self.phase = "apply"
        self.sample_start_s = 0.0
        self.error = ""
        self.done = False
        self.timed_out = False
        self.applied = False


def _step_camera(job: _RunnerJob, elapsed: float, *, apply_ok: bool) -> None:
    """One MCPJobRunner pass: a finished process beats the deadline check."""

    if job.done:
        return
    if job.phase == "apply":
        if not apply_ok:
            job.error = "apply_failed"
            job.done = True
            return
        job.applied = True
        job.phase = "settle"
        job.sample_start_s = elapsed
        return
    if job.phase == "settle":
        if elapsed - job.sample_start_s >= job.sample_s_target:
            job.phase = "report"
            job.done = True
            return
    if elapsed > job.deadline_s:
        job.timed_out = True
        job.done = True


def _run_camera(settle_ticks: int, *, apply_ok: bool, caller_timeout_s: float) -> dict:
    step_s = _bridge_float("CAMERA_SETTLE_STEP_S")
    slack_s = _bridge_float("CAMERA_JOB_TIMEOUT_S")
    default_ticks = _bridge_int("CAMERA_DEFAULT_SETTLE_TICKS")
    ticks = default_ticks if settle_ticks == 0 else settle_ticks
    job = _RunnerJob(ticks * step_s, slack_s)
    elapsed = 0.0
    caller = "pending"
    limit = job.deadline_s + step_s * 2
    while elapsed <= limit and not job.done:
        if caller == "pending" and elapsed >= caller_timeout_s:
            # The caller gave up. The bridge keeps the job. Applied or not
            # is already unknown to the caller.
            caller = "timeout_unknown"
        _step_camera(job, elapsed, apply_ok=apply_ok)
        if job.done:
            break
        elapsed += step_s
    if caller == "pending":
        caller = "completed" if job.done and not job.timed_out else "timeout_unknown"
    return {
        "applied": job.applied,
        "timed_out": job.timed_out,
        "error": job.error,
        "phase": job.phase,
        "elapsed": elapsed,
        "deadline_s": job.deadline_s,
        "caller": caller,
    }


class _Harness:
    def __init__(self) -> None:
        self.config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            auto_spawn_daemon=False,
            log_sink=lambda _message: None,
        )
        self.runtime = _fixture_client_runtime(self.config)
        self.session = self.runtime.identity.session_id
        self.stop = AsyncMock(side_effect=AssertionError("must not stop"))
        self.executed: list[dict] = []
        self.status_box: dict = {}
        self.extension_status: dict = {}
        self.bridge_status: dict = {}
        self.pid_alive: bool | None = False

    def box(self, **overrides: object) -> dict:
        row = {
            "run_id": _RUN,
            "mod": _MOD,
            "label": "lab",
            "age_s": 1.0,
            "state": "RUNNING_IDLE",
            "owner_session": None,
            "use_state": "abandoned",
            "use_reason": "client_gone",
            "launched_by": {
                "platform": "codex",
                "session": self.session[:12],
                "started_at_utc": "2026-07-15T00:00:00Z",
                "task_label": "",
            },
        }
        row.update(overrides)
        return {
            "occupied": True,
            "runs": [row],
            "foreign": [],
            "ports_in_use": [2302],
            "queue": [],
            "scan_known": True,
            "port_scan_known": True,
        }

    def extension(self, **overrides: object) -> dict:
        row = {
            "run_id": _RUN,
            "state": "RUNNING_IDLE",
            "mod": _MOD,
            # require_extension_run validates this recorded anchor against the
            # policy dev_root above and the bound (default) leaf.
            "profiles": r"P:\ExampleMod_Suite\_server\profiles",
            "processes": [
                {
                    "pid": _SERVER_PID,
                    "role": "server",
                    "creation_time_utc": _CREATED,
                },
                {
                    "pid": _CLIENT_PID,
                    "role": "client",
                    "creation_time_utc": _CREATED,
                },
            ],
        }
        row.update(overrides)
        return {"runs": [row]}

    async def call(self, arguments: dict) -> dict:
        payload = {
            "project": _PROJECT,
            "mode": "client",
            "run_id": _RUN,
            "takeover": False,
            "on_busy": "fail",
            "wait_for_box_s": 0.0,
        }
        payload.update(arguments)
        self.status_box = payload.pop("box")  # type: ignore[assignment]
        self.extension_status = payload.pop("extension_status")  # type: ignore[assignment]
        self.bridge_status = payload.pop("bridge_status", {})  # type: ignore[assignment]
        self.pid_alive = payload.pop("pid_alive", False)  # type: ignore[assignment]

        async def execute(_client: object, **kwargs: object) -> dict:
            self.executed.append(kwargs)
            try:
                require_extension_run(self.extension_status, _POLICY, str(kwargs["run_id"]))
            except DayzTestToolError as exc:
                return {
                    "status": "failed",
                    "error_code": exc.code,
                    "run_id": kwargs["run_id"],
                }
            with patch(
                "dayz_mcp.dayz_test_tool._pid_alive",
                return_value=self.pid_alive,
            ):
                record = _client_record_from_status(
                    self.extension_status, str(kwargs["run_id"])
                )
                decision = _decide_client_replacement(
                    record, self.bridge_status, budget_s=30.0
                )
            if not decision.replace:
                code = (
                    CLIENT_ALREADY_POLLING
                    if decision.reason == "client_polling"
                    else decision.reason
                )
                return {
                    "status": "failed",
                    "error_code": code,
                    "run_id": kwargs["run_id"],
                    "client_replace_reason": decision.reason,
                }
            return {
                "status": "succeeded",
                "run_id": _RUN,
                "server_pid": _SERVER_PID,
                "server_creation_time_utc": _CREATED,
                "client_pid": 900,
            }

        async def session_status() -> dict:
            return {"box": self.status_box}

        with patch.object(server_module, "ClientRuntime", return_value=self.runtime):
            app, _built = server_module.build_app(self.config)
        with (
            patch.object(self.runtime, "session_status", new=AsyncMock(side_effect=session_status)),
            patch.object(
                self.runtime,
                "session_box_status",
                new=AsyncMock(side_effect=session_status),
            ),
            patch.object(server_module.dayz_test_tool, "execute_dayz_test_run", execute),
            patch.object(server_module.dayz_test_tool, "execute_dayz_test_stop", self.stop),
        ):
            return _content_json(await app.call_tool("dayz_test_run", payload))


class ClientExtensionAdmissionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.harness = _Harness()

    async def test_released_launcher_client_extension_does_not_stop(self) -> None:
        # Fails on unmodified code: takeover_required is returned before execute.
        result = await self.harness.call(
            {"box": self.harness.box(), "extension_status": self.harness.extension()}
        )
        self.harness.stop.assert_not_awaited()
        self.assertEqual(result.get("status"), "succeeded")
        self.assertEqual(result.get("run_id"), _RUN)
        self.assertEqual(result.get("server_pid"), _SERVER_PID)
        self.assertEqual(result.get("server_creation_time_utc"), _CREATED)
        self.assertEqual(result.get("client_pid"), 900)
        self.assertEqual(self.harness.executed[0]["mode"], "client")
        self.assertEqual(self.harness.executed[0]["run_id"], _RUN)
        self.assertIsNone(result.get("evicted_run_id"))

    async def test_fresh_all_mode_stays_takeover_gated(self) -> None:
        result = await self.harness.call(
            {
                "mode": "all",
                "run_id": None,
                "box": self.harness.box(),
                "extension_status": self.harness.extension(),
            }
        )
        self.harness.stop.assert_not_awaited()
        self.assertEqual(self.harness.executed, [])
        self.assertEqual(result.get("error_code"), TAKEOVER_REQUIRED)
        self.assertEqual(
            takeover_target_run_id(
                self.harness.box(), caller_session=self.harness.session
            ),
            _RUN,
        )

    async def test_foreign_launcher_is_not_admitted(self) -> None:
        result = await self.harness.call(
            {
                "box": self.harness.box(
                    launched_by={
                        "platform": "codex",
                        "session": "ffffffffffff",
                        "started_at_utc": "2026-07-15T00:00:00Z",
                        "task_label": "",
                    },
                    use_state="idle",
                    use_reason=None,
                ),
                "extension_status": self.harness.extension(),
            }
        )
        self.assertEqual(self.harness.executed, [])
        self.harness.stop.assert_not_awaited()
        self.assertEqual(result.get("error_code"), "run_protected")

    async def test_wrong_project_and_wrong_run_stay_gated(self) -> None:
        wrong_project = await self.harness.call(
            {
                "box": self.harness.box(mod="@Other"),
                "extension_status": self.harness.extension(),
            }
        )
        self.assertEqual(wrong_project.get("error_code"), TAKEOVER_REQUIRED)
        wrong_run = await self.harness.call(
            {
                "run_id": _OTHER,
                "box": self.harness.box(),
                "extension_status": self.harness.extension(),
            }
        )
        self.assertEqual(wrong_run.get("error_code"), TAKEOVER_REQUIRED)
        self.assertEqual(self.harness.executed, [])
        self.harness.stop.assert_not_awaited()

    async def test_daemon_restart_forgets_the_launcher(self) -> None:
        result = await self.harness.call(
            {
                "box": self.harness.box(launched_by=None, use_state="idle"),
                "extension_status": self.harness.extension(),
            }
        )
        self.assertEqual(self.harness.executed, [])
        self.assertEqual(result.get("error_code"), "run_protected")

    async def test_prefix_selects_the_path_and_does_not_authorise(self) -> None:
        prefix = self.harness.session[:12]
        other = ClientIdentity(
            "claude", 22, 2, "2026-07-15T00:00:01Z", prefix + "-other-full", "x"
        )
        launcher = ClientIdentity(
            "codex",
            11,
            1,
            "2026-07-15T00:00:00Z",
            prefix + "-launcher-full",
            "owner",
        )
        result = await self.harness.call(
            {"box": self.harness.box(), "extension_status": self.harness.extension()}
        )
        self.assertEqual(result.get("status"), "succeeded")
        life = _lifecycle_with_run()
        try:
            stamp_launcher(life.lifecycle, _RUN, launcher)
            self.assertTrue(life.lifecycle._client_is_launcher(launcher, _RUN))
            self.assertFalse(life.lifecycle._client_is_launcher(other, _RUN))
        finally:
            life.close()

    async def test_starting_stopping_unreconciled_do_not_replace(self) -> None:
        for state in ("STARTING", "STOPPING", "UNRECONCILED"):
            with self.subTest(state=state):
                self.harness.executed.clear()
                result = await self.harness.call(
                    {
                        "box": self.harness.box(state=state),
                        "extension_status": self.harness.extension(state=state),
                    }
                )
                self.harness.stop.assert_not_awaited()
                self.assertEqual(result.get("error_code"), "run_not_extensible")
                self.assertEqual(len(self.harness.executed), 1)

    async def test_unknown_liveness_and_healthy_client_do_not_replace(self) -> None:
        unknown = await self.harness.call(
            {
                "box": self.harness.box(),
                "extension_status": self.harness.extension(),
                "pid_alive": None,
                "bridge_status": {},
            }
        )
        self.assertNotEqual(unknown.get("status"), "succeeded")
        self.assertNotEqual(unknown.get("error_code"), CLIENT_ALREADY_POLLING)
        healthy = await self.harness.call(
            {
                "box": self.harness.box(use_state="idle", use_reason=None),
                "extension_status": self.harness.extension(),
                "pid_alive": True,
                "bridge_status": {
                    "client_peer": {"last_poll_age_s": 0.2, "binding_state": ""}
                },
            }
        )
        self.assertEqual(healthy.get("error_code"), CLIENT_ALREADY_POLLING)
        self.harness.stop.assert_not_awaited()

    async def test_queue_and_immediate_both_reach_extension(self) -> None:
        for on_busy, wait_s in (("fail", 0.0), ("fail", 0.02), ("queue", 0.2)):
            with self.subTest(on_busy=on_busy, wait_s=wait_s):
                self.harness.executed.clear()
                result = await self.harness.call(
                    {
                        "on_busy": on_busy,
                        "wait_for_box_s": wait_s,
                        "box": self.harness.box(),
                        "extension_status": self.harness.extension(),
                    }
                )
                self.assertEqual(result.get("status"), "succeeded", result)
                self.assertEqual(len(self.harness.executed), 1)
        self.harness.stop.assert_not_awaited()

    async def test_status_change_between_admission_and_extension_refuses(self) -> None:
        changed = self.harness.extension(state="STOPPING")
        result = await self.harness.call(
            {"box": self.harness.box(), "extension_status": changed}
        )
        self.assertEqual(result.get("error_code"), "run_not_extensible")
        self.harness.stop.assert_not_awaited()
        polling = await self.harness.call(
            {
                "box": self.harness.box(),
                "extension_status": self.harness.extension(),
                "pid_alive": True,
                "bridge_status": {
                    "client_peer": {"last_poll_age_s": 0.1, "binding_state": ""}
                },
            }
        )
        self.assertEqual(polling.get("error_code"), CLIENT_ALREADY_POLLING)

    async def test_another_run_foreign_and_unknown_scan_stay_blocked(self) -> None:
        other = self.harness.box()
        other["runs"].append(
            {
                "run_id": _OTHER,
                "mod": _MOD,
                "state": "STOPPING",
                "owner_session": None,
            }
        )
        blocked = await self.harness.call(
            {"box": other, "extension_status": self.harness.extension()}
        )
        self.assertEqual(blocked.get("error_code"), TAKEOVER_REQUIRED)
        foreign = self.harness.box()
        foreign["foreign"] = [{"mods": ["@Foreign"], "port": 2302}]
        foreign_result = await self.harness.call(
            {"box": foreign, "extension_status": self.harness.extension()}
        )
        self.assertEqual(foreign_result.get("error_code"), TAKEOVER_REQUIRED)
        unknown = self.harness.box()
        unknown["port_scan_known"] = False
        unknown_result = await self.harness.call(
            {"box": unknown, "extension_status": self.harness.extension()}
        )
        self.assertNotEqual(unknown_result.get("status"), "succeeded")
        self.assertEqual(self.harness.executed, [])
        self.harness.stop.assert_not_awaited()


class _Life:
    def __init__(self) -> None:
        self.temporary = TemporaryDirectory()
        root = Path(self.temporary.name)
        game = root / "DayZ"
        game.mkdir()
        paths = RuntimePaths(
            root / "runtime",
            root / "runtime" / "audit",
            root / "runtime" / "coordination.json",
            root / "runtime" / "runs.json",
        )
        self.guard = FakeGuard()
        self.launcher = FakeLauncher()
        self.lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=SessionCoordinator(
                token_fn=Sequence("token"),
                id_fn=Sequence("lease"),
                audit=AuditSink(),
            ),
            manifest=RunManifestStore(paths),
            audit=AuditSink(),
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=game,
            launcher=self.launcher,
            daemon_generation="gen-now",
        )
        self.store = self.lifecycle.manifest

    def close(self) -> None:
        self.temporary.cleanup()


def _lifecycle_with_run() -> _Life:
    life = _Life()
    server = process(_SERVER_PID, "server")
    client = process(_CLIENT_PID, "client")
    life.guard.snapshots[server.pid] = snapshot(server)
    life.guard.snapshots[client.pid] = {
        "error": "process_not_found",
        "exit_code": 4,
        "process_exit_code": 1,
        "exit_time_utc": "2026-08-01T00:00:00.000Z",
    }
    life.store.add(
        RunRecord(
            _RUN,
            None,
            None,
            "RUNNING_IDLE",
            "label",
            _MOD,
            "profiles",
            "mission",
            [server, client],
            daemon_generation_at_launch="gen-now",
        )
    )
    return life


class _BlockedProbe:
    """Scripted guard for the overlapping-reader interleaving.

    The first probe blocks until released and then answers with the pending
    (weaker) snapshot; every later probe answers with the death snapshot.
    Observation stamps are scripted too: probe one belongs to reader A,
    probe two to reader B, probe three to reader C, so the first death
    stamp and the later confirm stamp are distinct constants.
    """

    def __init__(
        self,
        life: _Life,
        pending: dict,
        death: dict,
    ) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self._pending = pending
        self._death = death
        self._probes = 0
        self._probes_lock = threading.Lock()
        self._index_stamps = {
            1: "2026-08-01T00:00:00.500Z",
            2: "2026-08-01T00:00:01.000Z",
            3: "2026-08-01T00:00:02.000Z",
        }
        self._stamps = iter(
            [
                "2026-08-01T00:00:00.500Z",
                "2026-08-01T00:00:01.000Z",
                "2026-08-01T00:00:02.000Z",
            ]
        )
        self._stamps_lock = threading.Lock()
        self._post_probe: dict[int, str] = {}
        life.guard.snapshot = self.snapshot  # type: ignore[method-assign]
        life.lifecycle._observation_now = self.stamp  # type: ignore[method-assign]

    def snapshot(self, pid: int) -> dict[str, object]:
        with self._probes_lock:
            self._probes += 1
            index = self._probes
        if index == 1:
            self.entered.set()
            self.release.wait(5.0)
            result = dict(self._pending)
        else:
            result = dict(self._death)
        # Available only after this probe returns. A lifecycle that stamps
        # first still consumes the pre-probe iterator below.
        self._post_probe[threading.get_ident()] = self._index_stamps.get(
            index, "2026-08-01T00:00:03.000Z"
        )
        return result

    def stamp(self) -> str:
        ready = self._post_probe.pop(threading.get_ident(), None)
        if ready is not None:
            return ready
        with self._stamps_lock:
            return next(self._stamps, "2026-08-01T00:00:03.000Z")


class ClientDiagnosticsTest(unittest.TestCase):
    def test_server_alive_client_dead_observes_without_managing_processes(self) -> None:
        life = _lifecycle_with_run()
        try:
            life.lifecycle._observation_now = lambda: "2026-08-01T00:00:01.000Z"  # type: ignore[method-assign]
            payload = life.lifecycle.public_status()
            row = payload["runs"][0]
            clients = row["client_diagnostics"]
            self.assertEqual(len(clients), 1)
            diag = clients[0]
            self.assertEqual(diag["state"], "dead")
            self.assertEqual(diag["pid"], _CLIENT_PID)
            self.assertEqual(diag["first_observed_dead_at_utc"], "2026-08-01T00:00:01.000Z")
            self.assertEqual(diag["exit_code"], 1)
            self.assertEqual(diag["exit_time_utc"], "2026-08-01T00:00:00.000Z")
            self.assertEqual(diag["observed_at_utc"], "2026-08-01T00:00:01.000Z")
            self.assertEqual(life.guard.terminate_calls, [])
            self.assertEqual(life.launcher.calls, [])
            self.assertEqual(life.guard.snapshot_calls, [_CLIENT_PID])
            stored = life.store.get(_RUN)
            self.assertEqual(stored.state, "RUNNING_IDLE")
            self.assertEqual([item.role for item in stored.processes], ["server", "client"])
            life.lifecycle._observation_now = lambda: "2026-08-01T00:00:09.000Z"  # type: ignore[method-assign]
            again = life.lifecycle.public_status()["runs"][0]["client_diagnostics"][0]
            self.assertEqual(again["first_observed_dead_at_utc"], "2026-08-01T00:00:01.000Z")
            self.assertEqual(again["observed_at_utc"], "2026-08-01T00:00:09.000Z")
        finally:
            life.close()

    def test_unknown_probe_pid_reuse_reattach_restart_and_bound(self) -> None:
        life = _lifecycle_with_run()
        try:
            client = process(_CLIENT_PID, "client")
            life.guard.snapshots[client.pid] = {
                "error": "identity_unavailable",
                "exit_code": 3,
            }
            life.lifecycle._observation_now = lambda: "2026-08-02T00:00:00.000Z"  # type: ignore[method-assign]
            unknown = life.lifecycle.public_status()["runs"][0]["client_diagnostics"][0]
            self.assertEqual(unknown["state"], "unknown")
            self.assertIsNone(unknown["first_observed_dead_at_utc"])
            self.assertIsNone(unknown["exit_code"])

            life.guard.snapshots[client.pid] = {
                "pid": client.pid,
                "creation_time_utc": "2099-01-01T00:00:00.000000Z",
                "executable_sha256": HASH_A,
                "command_line_sha256": HASH_B,
                "identity_scheme": client.identity_scheme,
                "identity_complete": True,
                "process_exit_code": 9,
                "exit_time_utc": "should-not-copy",
            }
            reused = life.lifecycle.public_status()["runs"][0]["client_diagnostics"][0]
            self.assertEqual(reused["state"], "dead")
            self.assertIsNone(reused["exit_code"])
            self.assertIsNone(reused["exit_time_utc"])
            self.assertEqual(reused["creation_time_utc"], client.creation_time_utc)

            replacement = process(803, "client")
            life.guard.snapshots[replacement.pid] = snapshot(replacement)
            run = life.store.get(_RUN)
            run.processes = [process(_SERVER_PID, "server"), replacement]
            life.store.replace(run)
            rows = life.lifecycle.public_status()["runs"][0]["client_diagnostics"]
            by_pid = {item["pid"]: item for item in rows}
            self.assertEqual(by_pid[_CLIENT_PID]["current"], False)
            self.assertEqual(by_pid[_CLIENT_PID]["state"], "dead")
            self.assertEqual(by_pid[803]["current"], True)
            self.assertEqual(by_pid[803]["state"], "alive")
            self.assertIsNone(by_pid[803]["first_observed_dead_at_utc"])

            restarted = ProcessLifecycle(
                steam_gate=FakeSteamGate(),
                coordinator=life.lifecycle.coordinator,
                manifest=life.store,
                audit=AuditSink(),
                guard=life.guard,
                retail_probe=lambda: {"known": True, "processes": []},
                diag_probe=lambda: {"known": True, "processes": []},
                game_path=life.lifecycle.game_path,
                launcher=life.launcher,
                daemon_generation="gen-now",
            )
            self.assertEqual(restarted._client_role_observations, {})
            fresh = restarted.public_status()["runs"][0]["client_diagnostics"]
            self.assertEqual([item["pid"] for item in fresh], [803])

            for index in range(_CLIENT_ROLE_BOUND()):
                key = ("gen-now", f"old-{index}", index + 1, f"t{index}")
                restarted._client_role_observations[key] = {"state": "dead"}
            restarted.public_status()
            self.assertLessEqual(
                len(restarted._client_role_observations),
                _CLIENT_ROLE_BOUND(),
            )
            self.assertIn(
                ("gen-now", _RUN, 803, replacement.creation_time_utc),
                restarted._client_role_observations,
            )
            self.assertEqual(life.guard.terminate_calls, [])
        finally:
            life.close()

    def test_overlapping_status_reads_during_identity_replacement(self) -> None:
        life = _lifecycle_with_run()
        try:
            life.lifecycle.public_status()
            replacement = process(803, "client")
            life.guard.snapshots[replacement.pid] = snapshot(replacement)
            run = life.store.get(_RUN)
            run.processes = [process(_SERVER_PID, "server"), replacement]
            life.store.replace(run)
            started = threading.Event()
            release = threading.Event()
            blocked = {"once": False}

            def slow_snapshot(pid: int) -> dict:
                if pid == replacement.pid and not blocked["once"]:
                    blocked["once"] = True
                    started.set()
                    release.wait(1.0)
                return FakeGuard.snapshot(life.guard, pid)

            life.guard.snapshot = slow_snapshot  # type: ignore[method-assign]
            errors: list[BaseException] = []

            def read_status() -> None:
                try:
                    life.lifecycle.public_status()
                except BaseException as exc:
                    errors.append(exc)

            reader = threading.Thread(target=read_status)
            reader.start()
            self.assertTrue(started.wait(1.0))
            # The reader is inside the client probe, outside the table lock.
            # This status inserts the replacement while that read is in flight.
            during = life.lifecycle.public_status()
            release.set()
            reader.join(2.0)
            self.assertFalse(reader.is_alive())
            self.assertEqual(errors, [])
            pids = {item["pid"] for item in during["runs"][0]["client_diagnostics"]}
            self.assertIn(_CLIENT_PID, pids)
            self.assertIn(803, pids)
            self.assertEqual(life.guard.terminate_calls, [])
        finally:
            life.close()

    def test_overlapping_readers_cannot_erase_an_observed_death(self) -> None:
        # Fails on the previous tree: the late reader stored its stale
        # unknown probe over the death another reader had published.
        life = _lifecycle_with_run()
        try:
            probe = _BlockedProbe(
                life,
                pending={"error": "identity_unavailable", "exit_code": 3},
                death={"error": "process_not_found", "exit_code": 4},
            )
            payloads: list[dict[str, object]] = []
            errors: list[BaseException] = []

            def read_status() -> None:
                try:
                    payloads.append(life.lifecycle.public_status())
                except BaseException as exc:
                    errors.append(exc)

            reader = threading.Thread(target=read_status)
            reader.start()
            self.assertTrue(probe.entered.wait(5.0))
            # Reader A is blocked inside its guard probe. Reader B observes
            # the death and publishes it before A resumes.
            published = life.lifecycle.public_status()["runs"][0][
                "client_diagnostics"
            ][0]
            self.assertEqual(published["state"], "dead")
            first_dead = published["first_observed_dead_at_utc"]
            self.assertEqual(first_dead, "2026-08-01T00:00:01.000Z")
            probe.release.set()
            reader.join(5.0)
            self.assertFalse(reader.is_alive())
            self.assertEqual(errors, [])
            late = payloads[0]["runs"][0]["client_diagnostics"][0]
            self.assertEqual(late["state"], "dead")
            self.assertEqual(late["first_observed_dead_at_utc"], first_dead)
            # The merged row is a detached copy: editing the payload cannot
            # rewrite the stored observation.
            late["first_observed_dead_at_utc"] = None
            stored = life.lifecycle._client_role_observations[
                (
                    "gen-now",
                    _RUN,
                    _CLIENT_PID,
                    process(_CLIENT_PID, "client").creation_time_utc,
                )
            ]
            self.assertEqual(stored["state"], "dead")
            self.assertEqual(stored["first_observed_dead_at_utc"], first_dead)
            confirmed = life.lifecycle.public_status()["runs"][0][
                "client_diagnostics"
            ][0]
            self.assertEqual(confirmed["state"], "dead")
            self.assertEqual(confirmed["first_observed_dead_at_utc"], first_dead)
            self.assertNotEqual(
                confirmed["first_observed_dead_at_utc"], "2026-08-01T00:00:02.000Z"
            )
            self.assertEqual(life.guard.terminate_calls, [])
        finally:
            life.close()

    def test_late_unknown_merge_keeps_retained_exit_fields(self) -> None:
        # Fails on the previous tree: the same stale merge dropped the exact
        # exit fields the published death observation had retained.
        life = _lifecycle_with_run()
        try:
            probe = _BlockedProbe(
                life,
                pending={"error": "identity_unavailable", "exit_code": 3},
                death={
                    "error": "process_not_found",
                    "exit_code": 4,
                    "process_exit_code": 1,
                    "exit_time_utc": "2026-08-01T00:00:00.900Z",
                },
            )
            payloads: list[dict[str, object]] = []
            errors: list[BaseException] = []

            def read_status() -> None:
                try:
                    payloads.append(life.lifecycle.public_status())
                except BaseException as exc:
                    errors.append(exc)

            reader = threading.Thread(target=read_status)
            reader.start()
            self.assertTrue(probe.entered.wait(5.0))
            published = life.lifecycle.public_status()["runs"][0][
                "client_diagnostics"
            ][0]
            self.assertEqual(published["exit_code"], 1)
            self.assertEqual(published["exit_time_utc"], "2026-08-01T00:00:00.900Z")
            probe.release.set()
            reader.join(5.0)
            self.assertFalse(reader.is_alive())
            self.assertEqual(errors, [])
            late = payloads[0]["runs"][0]["client_diagnostics"][0]
            self.assertEqual(late["state"], "dead")
            self.assertEqual(late["exit_code"], 1)
            self.assertEqual(late["exit_time_utc"], "2026-08-01T00:00:00.900Z")
            confirmed = life.lifecycle.public_status()["runs"][0][
                "client_diagnostics"
            ][0]
            self.assertEqual(confirmed["exit_code"], 1)
            self.assertEqual(confirmed["exit_time_utc"], "2026-08-01T00:00:00.900Z")
            self.assertEqual(life.guard.terminate_calls, [])
        finally:
            life.close()

    def test_delayed_dead_observation_keeps_published_death_evidence(self) -> None:
        # Fails on the previous tree: reader A stamps before its probe, then
        # min() replaces B's first-death time with that earlier stamp.
        life = _lifecycle_with_run()
        try:
            probe = _BlockedProbe(
                life,
                pending={"error": "process_not_found", "exit_code": 4},
                death={
                    "error": "process_not_found",
                    "exit_code": 4,
                    "process_exit_code": 1,
                    "exit_time_utc": "2026-08-01T00:00:00.900Z",
                },
            )
            payloads: list[dict[str, object]] = []
            errors: list[BaseException] = []

            def read_status() -> None:
                try:
                    payloads.append(life.lifecycle.public_status())
                except BaseException as exc:
                    errors.append(exc)

            reader = threading.Thread(target=read_status)
            reader.start()
            self.assertTrue(probe.entered.wait(5.0))
            published = life.lifecycle.public_status()["runs"][0][
                "client_diagnostics"
            ][0]
            self.assertEqual(
                published["first_observed_dead_at_utc"], "2026-08-01T00:00:01.000Z"
            )
            self.assertEqual(published["exit_code"], 1)
            self.assertEqual(published["exit_time_utc"], "2026-08-01T00:00:00.900Z")
            probe.release.set()
            reader.join(5.0)
            self.assertFalse(reader.is_alive())
            self.assertEqual(errors, [])
            late = payloads[0]["runs"][0]["client_diagnostics"][0]
            confirmed = life.lifecycle.public_status()["runs"][0][
                "client_diagnostics"
            ][0]
            for row in (late, confirmed):
                self.assertEqual(row["state"], "dead")
                self.assertEqual(
                    row["first_observed_dead_at_utc"], "2026-08-01T00:00:01.000Z"
                )
                self.assertEqual(row["exit_code"], 1)
                self.assertEqual(row["exit_time_utc"], "2026-08-01T00:00:00.900Z")
            self.assertNotEqual(
                late["first_observed_dead_at_utc"], "2026-08-01T00:00:00.500Z"
            )
            self.assertEqual(life.guard.terminate_calls, [])
        finally:
            life.close()

    def test_conflicting_older_exit_evidence_does_not_replace_recorded(self) -> None:
        # Fails on the previous tree: a later probe's non-null exit fields
        # replace the recorded (1, 00:00:00.900Z) with (2, 00:00:00.400Z).
        life = _lifecycle_with_run()
        try:
            probe = _BlockedProbe(
                life,
                pending={
                    "error": "process_not_found",
                    "exit_code": 4,
                    "process_exit_code": 2,
                    "exit_time_utc": "2026-08-01T00:00:00.400Z",
                },
                death={
                    "error": "process_not_found",
                    "exit_code": 4,
                    "process_exit_code": 1,
                    "exit_time_utc": "2026-08-01T00:00:00.900Z",
                },
            )
            payloads: list[dict[str, object]] = []
            errors: list[BaseException] = []

            def read_status() -> None:
                try:
                    payloads.append(life.lifecycle.public_status())
                except BaseException as exc:
                    errors.append(exc)

            reader = threading.Thread(target=read_status)
            reader.start()
            self.assertTrue(probe.entered.wait(5.0))
            published = life.lifecycle.public_status()["runs"][0][
                "client_diagnostics"
            ][0]
            self.assertEqual(published["exit_code"], 1)
            self.assertEqual(published["exit_time_utc"], "2026-08-01T00:00:00.900Z")
            probe.release.set()
            reader.join(5.0)
            self.assertFalse(reader.is_alive())
            self.assertEqual(errors, [])
            late = payloads[0]["runs"][0]["client_diagnostics"][0]
            self.assertEqual(late["exit_code"], 1)
            self.assertEqual(late["exit_time_utc"], "2026-08-01T00:00:00.900Z")
            self.assertEqual(
                late["first_observed_dead_at_utc"], "2026-08-01T00:00:01.000Z"
            )
            self.assertEqual(life.guard.terminate_calls, [])
        finally:
            life.close()

    def test_not_started_when_the_run_has_no_client(self) -> None:
        life = _lifecycle_with_run()
        try:
            run = life.store.get(_RUN)
            run.processes = [process(_SERVER_PID, "server")]
            life.store.replace(run)
            diag = life.lifecycle.public_status()["runs"][0]["client_diagnostics"]
            self.assertEqual(diag[0]["state"], "not_started")
            self.assertIsNone(diag[0]["pid"])
            self.assertEqual(life.guard.snapshot_calls, [])
            self.assertEqual(life.guard.terminate_calls, [])
        finally:
            life.close()


def _CLIENT_ROLE_BOUND() -> int:
    from dayz_mcp.process_lifecycle import _CLIENT_ROLE_OBSERVATION_BOUND

    return _CLIENT_ROLE_OBSERVATION_BOUND


class CameraSettleDeadlineTest(unittest.IsolatedAsyncioTestCase):
    def test_runner_keeps_a_200_tick_job_past_five_seconds(self) -> None:
        text = _BRIDGE.read_text(encoding="utf-8")
        self.assertIn(
            "job.deadline_s = m_JobRunner.GetElapsedS() + job.sample_s_target + CAMERA_JOB_TIMEOUT_S;",
            text,
        )
        ordinary = _run_camera(3, apply_ok=True, caller_timeout_s=40.0)
        self.assertFalse(ordinary["timed_out"])
        self.assertTrue(ordinary["applied"])
        self.assertLess(ordinary["elapsed"], 5.0)
        long = _run_camera(200, apply_ok=True, caller_timeout_s=40.0)
        self.assertFalse(long["timed_out"])
        self.assertGreater(long["deadline_s"], 5.0)
        self.assertGreater(long["elapsed"], 5.0)
        self.assertEqual(long["caller"], "completed")
        edges = (
            _run_camera(0, apply_ok=True, caller_timeout_s=40.0),
            _run_camera(600, apply_ok=True, caller_timeout_s=40.0),
        )
        self.assertFalse(edges[0]["timed_out"])
        self.assertAlmostEqual(edges[0]["deadline_s"], 3 * 0.05 + 5.0)
        self.assertAlmostEqual(edges[1]["deadline_s"], 600 * 0.05 + 5.0)
        failed = _run_camera(200, apply_ok=False, caller_timeout_s=40.0)
        self.assertFalse(failed["applied"])
        self.assertEqual(failed["error"], "apply_failed")
        self.assertFalse(failed["timed_out"])
        short = _run_camera(200, apply_ok=True, caller_timeout_s=4.0)
        self.assertEqual(short["caller"], "timeout_unknown")
        self.assertTrue(short["applied"])
        self.assertFalse(short["timed_out"])

    def test_wire_and_tool_reject_settle_ticks_outside_0_to_600(self) -> None:
        ok, _error = validate_command_args(
            "camera_set",
            {
                "cam_mode": "lookat",
                "cam_pos": [1.0, 2.0, 3.0],
                "look_at": [1.0, 2.0, 4.0],
                "fov": 0.0,
                "settle_ticks": 600,
            },
        )
        self.assertTrue(ok)
        for ticks in (-1, 601):
            accepted, reason = validate_command_args(
                "camera_set",
                {
                    "cam_mode": "lookat",
                    "cam_pos": [1.0, 2.0, 3.0],
                    "look_at": [1.0, 2.0, 4.0],
                    "fov": 0.0,
                    "settle_ticks": ticks,
                },
            )
            self.assertFalse(accepted)
            self.assertEqual(reason, "bad_args")
        zero, _zero_error = validate_command_args(
            "camera_set",
            {
                "cam_mode": "lookat",
                "cam_pos": [1.0, 2.0, 3.0],
                "look_at": [1.0, 2.0, 4.0],
                "fov": 0.0,
                "settle_ticks": 0,
            },
        )
        self.assertTrue(zero)

    async def test_tool_validates_before_enqueue_and_timeout_stays_unknown(self) -> None:
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            auto_spawn_daemon=False,
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        bridge = AsyncMock(return_value={"ok": 1, "id": 1})
        runtime.call_bridge = bridge
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = server_module.build_app(config)
        description = ""
        for tool in app._tool_manager.list_tools():
            if getattr(tool, "name", None) == "camera_set":
                description = tool.description or ""
        self.assertIn("settle_ticks * 0.05", description)
        self.assertIn("does not prove the camera was not applied", description)
        with self.assertRaises(Exception) as rejected:
            await app.call_tool(
                "camera_set",
                {
                    "cam_mode": "lookat",
                    "cam_pos": [1.0, 2.0, 3.0],
                    "look_at": [1.0, 2.0, 4.0],
                    "settle_ticks": 601,
                    "timeout_s": 40.0,
                },
            )
        self.assertIn("bad_args", str(rejected.exception))
        bridge.assert_not_awaited()
        await app.call_tool(
            "camera_set",
            {
                "cam_mode": "lookat",
                "cam_pos": [13255.4, 20.0, 7148.3],
                "look_at": [13252.8, 19.9, 7148.0],
                "settle_ticks": 200,
                "timeout_s": 40.0,
            },
        )
        args = bridge.await_args.args
        self.assertEqual(args[0], "camera_set")
        self.assertEqual(args[1]["settle_ticks"], 200)
        bridge.side_effect = server_module.ToolError(
            "timeout waiting for camera_set id=7; client polling"
        )
        with self.assertRaises(Exception) as timed:
            await app.call_tool(
                "camera_set",
                {
                    "cam_mode": "lookat",
                    "cam_pos": [1.0, 2.0, 3.0],
                    "look_at": [1.0, 2.0, 4.0],
                    "settle_ticks": 200,
                    "timeout_s": 4.0,
                },
            )
        self.assertIn("timeout waiting for camera_set", str(timed.exception))


if __name__ == "__main__":
    unittest.main()
