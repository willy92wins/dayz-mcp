"""fb-20260904-200821-dae1, parts 1 and 2 (audit R9: H-A2-3 and A1-F7).

Part 1 (H-A2-3). The reaper can retire a run while dayz_test_stop is on its way.
The stop then answered run_not_adopted about a run the caller owned and the
daemon itself had retired: the sealed worker needed a status round trip to learn
the box was free, and when the reap landed before its adopt the stop failed
with run_not_adoptable. The daemon now answers that stop with the reply the
sealed worker maps to success (ok, run_id, state EXITED; dayz_test_worker
_successful_run), stop_method already_exited, and only to the session and lease
that owned the run when it was reaped.

Part 2 (A1-F7). A daemon that died between the Popen of start_run and the
manifest write left the launched DayZ outside every row, and the next daemon
found an unknown DayZ occupying the box. start_run now writes a launch intent
before the Popen; the next daemon start records the process that matches it
exactly (argv, instance token, creation time) and retires the intent otherwise.
Nothing is killed that the manifest or the intent does not name.

The audit repros (repro_A2_reap.py A2-R4, repro_A1_crash.py) used real child
processes and os._exit; here every process is a row of a fake host table and a
crash is the copy of the runtime files at the instant it happens. No process is
started, stopped or read.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sys
import time
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import (
    daemon,
    dayz_test_request,
    dayz_test_worker,
    loopback,
    native_broker_protocol,
)
from dayz_mcp.instance_fence import format_creation_time_utc
from dayz_mcp.native_process_guard import identity_hashes
from dayz_mcp.process_lifecycle import (
    ProcessLifecycle,
    ProcessRecord,
    RunManifestStore,
    RunRecord,
)
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.lifecycle_helpers import Handle, Sequence
from tests.process_lifecycle_helpers import AuditSink, IDENTITY_A, IDENTITY_B
from tests.steam_helpers import FakeSteamGate


RUN_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
INTENT_FIELDS = {
    "version",
    "run_id",
    "role",
    "instance",
    "profiles",
    "command_line_sha256",
    "owner_session_id",
    "owner_lease_id",
    "not_before_utc",
}


class _Host:
    """The processes that outlive a daemon, as its guard and probes read them.

    It is also the process guard: snapshot answers from the table, and
    terminate ends only a process whose full identity matches, the way
    NativeProcessGuard does. Every terminate call is recorded.
    """

    def __init__(self) -> None:
        self.snapshots: dict[int, dict[str, object]] = {}
        self.images: dict[int, str] = {}
        self.terminate_calls: list[ProcessRecord] = []
        self.diag_known = True

    def spawn(self, pid: int, argv: list[str], created_at: float) -> None:
        hashes = identity_hashes(argv[0], argv)
        self.snapshots[pid] = {
            "pid": pid,
            "creation_time_utc": format_creation_time_utc(created_at),
            "executable_sha256": hashes["executable_sha256"],
            "command_line_sha256": hashes["command_line_sha256"],
            "identity_scheme": "psutil-argv-v2",
            "identity_complete": True,
            "exit_code": 0,
        }
        self.images[pid] = "DayZDiag_x64.exe"

    def record(self, pid: int, role: str) -> ProcessRecord:
        record = ProcessLifecycle._record_from_snapshot(self.snapshots[pid], role)
        assert record is not None
        return record

    def kill(self, pid: int) -> None:
        self.snapshots[pid] = {"pid": pid, "error": "process_not_found", "exit_code": 4}
        self.images.pop(pid, None)

    def snapshot(self, pid: int) -> dict[str, object]:
        return dict(
            self.snapshots.get(
                pid, {"pid": pid, "error": "process_not_found", "exit_code": 4}
            )
        )

    def terminate(self, record: ProcessRecord) -> dict[str, object]:
        self.terminate_calls.append(record)
        if not ProcessLifecycle._identity_matches(record, self.snapshot(record.pid)):
            return {
                "terminated": False,
                "error": "process_identity_mismatch",
                "exit_code": 4,
            }
        self.kill(record.pid)
        return {"terminated": True, "exit_code": 0}

    def diag(self) -> dict[str, object]:
        if not self.diag_known:
            return {"known": False, "processes": []}
        return {
            "known": True,
            "processes": [
                {"pid": pid, "name": name} for pid, name in sorted(self.images.items())
            ],
        }


class _Box:
    """One machine: a game folder, profiles, the host table and its daemons.

    A daemon here is a ProcessLifecycle on a runtime folder. A crash is
    crash_image(): the runtime files copied at that instant, which is all a
    dead daemon leaves behind for the next one.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.game = root / "DayZ"
        self.game.mkdir()
        for name in ("DayZDiag_x64.exe", "DayZ_BE.exe", "DayZ_x64.exe"):
            (self.game / name).write_bytes(b"")
        self.host = _Host()
        self.audit = AuditSink()
        self.retail: dict[str, object] = {"known": True, "processes": []}
        self.clock = 1_790_000_000.0
        self.spawn_delay_s = 0.25
        self.next_pid = 7001
        self.crash_at: str | None = None
        self.live_paths: RuntimePaths | None = None
        self.image: RuntimePaths | None = None
        self.launches: list[list[str]] = []
        self._images = 0

    def paths(self, name: str) -> RuntimePaths:
        root = self.root / name
        return RuntimePaths(root, root / "audit", root / "coordination.json", root / "runs.json")

    def profiles(self, name: str) -> Path:
        directory = self.root / name / "profiles"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "dayz_mcp.json").write_text(
            json.dumps({"url": "http://127.0.0.1:8765/", "key": "k", "pollHz": 5}),
            encoding="utf-8",
        )
        return directory

    def mission(self) -> Path:
        directory = self.root / "mpmissions" / "dayzOffline.chernarusplus"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def daemon_on(
        self, paths: RuntimePaths, *, bound: bool = False
    ) -> ProcessLifecycle:
        coordinator = SessionCoordinator(
            token_fn=Sequence("token"), id_fn=Sequence("lease"), audit=self.audit
        )
        lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=coordinator,
            manifest=RunManifestStore(paths),
            audit=self.audit,
            guard=self.host,
            retail_probe=lambda: self.retail,
            diag_probe=self.host.diag,
            game_path=self.game,
            launcher=self.launch,
        )
        lifecycle._launch_intent_clock = lambda: self.clock
        if bound:
            state = loopback.ServerState("k")
            state.lifecycle = lifecycle
            lifecycle.bindings = state
        self.live_paths = paths
        original_replace = lifecycle.manifest.replace

        def replace(run: RunRecord) -> None:
            original_replace(run)
            # The write that records the launched process: RUNNING, after the
            # launcher ran. Before it the row is STARTING.
            if (
                self.crash_at == "after_manifest"
                and self.image is None
                and self.launches
                and run.state == "RUNNING"
            ):
                self.image = self.crash_image()

        lifecycle.manifest.replace = replace  # type: ignore[method-assign]
        return lifecycle

    def lease(self, lifecycle: ProcessLifecycle, identity=IDENTITY_A) -> tuple[str, str]:
        status, acquired = lifecycle.coordinator.acquire(identity, "dae1")
        assert status == 200, acquired
        return str(acquired["lease_token"]), str(acquired["lease_id"])

    def launch(self, argv: list[str], cwd: str, window_style: str) -> Handle:
        self.launches.append(list(argv))
        if self.crash_at == "before_popen":
            self.image = self.crash_image()
            raise OSError("the daemon died before the Popen")
        pid = self.next_pid
        self.next_pid += 1
        self.host.spawn(pid, list(argv), self.clock + self.spawn_delay_s)
        if self.crash_at == "after_popen":
            self.image = self.crash_image()
        return Handle(pid)

    def crash_image(self) -> RuntimePaths:
        assert self.live_paths is not None
        self._images += 1
        image = self.paths(f"crash-{self._images}")
        image.root.mkdir(parents=True)
        for name in ("runs.json", "launch-intent.json"):
            source = self.live_paths.runs_path.with_name(name)
            if source.exists():
                shutil.copyfile(source, image.root / name)
        return image

    def intent_path(self, paths: RuntimePaths) -> Path:
        return paths.runs_path.with_name("launch-intent.json")

    def client_argv(self, profiles: Path) -> list[str]:
        return [
            str((self.game / "DayZDiag_x64.exe").resolve()),
            "-mod=@Mod",
            "-connect=127.0.0.1",
            "-port=2302",
            f"-profiles={profiles}",
            "-name=Survivor",
            "-window",
            "-noPause",
        ]

    def client_request(
        self, profiles: Path, run_id: str, *, witness: int | None = None
    ) -> dict[str, object]:
        request: dict[str, object] = {
            "argv": self.client_argv(profiles),
            "cwd": str(self.game),
            "role": "client",
            "window_style": "normal",
            "label": "@Mod client",
            "mod": "@Mod",
            "profiles": str(profiles),
            "mission": "",
            "run_id": run_id,
        }
        if witness is not None:
            request["replace_if_not_polling_since"] = witness
        return request

    def server_argv(self, profiles: Path) -> list[str]:
        return [
            str((self.game / "DayZDiag_x64.exe").resolve()),
            "-server",
            f"-profiles={profiles}",
            f"-mission={self.mission()}",
            "-mod=@Mod",
            "-port=2302",
        ]

    def new_server_request(self, profiles: Path, run_id: str) -> dict[str, object]:
        request: dict[str, object] = {
            "argv": self.server_argv(profiles),
            "cwd": str(self.game),
            "role": "server",
            "window_style": "normal",
            "label": "@Mod server",
            "mod": "@Mod",
            "profiles": str(profiles),
            "mission": str(self.mission()),
            "storage_seal": "a" * 64,
            "new_run_id": run_id,
            "launch_operation_id": "22222222-2222-4222-8222-222222222222",
        }
        request["launch_request_sha256"] = hashlib.sha256(
            json.dumps(
                request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        return request

    def server_run(
        self, lifecycle: ProcessLifecycle, lease_id: str, *, with_client: bool = False
    ) -> list[ProcessRecord]:
        """A RUNNING run owned by IDENTITY_A, acknowledged, server (+ client) alive."""
        server_profiles = self.profiles("server")
        self.host.spawn(5001, self.server_argv(server_profiles), self.clock - 600.0)
        records = [self.host.record(5001, "server")]
        if with_client:
            client_argv = self.client_argv(self.profiles("client"))
            self.host.spawn(5002, client_argv, self.clock - 300.0)
            records.append(self.host.record(5002, "client"))
        lifecycle.manifest.add(
            RunRecord(
                RUN_ID,
                IDENTITY_A.session_id,
                lease_id,
                "RUNNING",
                "@Mod server",
                "@Mod",
                str(server_profiles),
                "",
                list(records),
            )
        )
        return records


def _freeze_witness(lifecycle: ProcessLifecycle, now: float) -> None:
    """start_run ages the replacement witness against time.time(); pin it."""

    def frozen(parsed, *, now=None):
        return ProcessLifecycle._replacement_witness_error(lifecycle, parsed, now=frozen_now)

    frozen_now = now
    lifecycle._replacement_witness_error = frozen  # type: ignore[method-assign]
    lifecycle.bridge_probe = lambda now=None: {
        "peers": {
            "client": {
                "last_poll_age_s": 999.0,
                "bound_last_poll_age_s": None,
                "binding_state": None,
            }
        }
    }


# --------------------------------------------------------------------- part 1


class ReapedRunStopTest(unittest.TestCase):
    """H-A2-3: repro_A2_reap.py A2-R4 as a unit test, with its control."""

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.box = _Box(Path(self.temporary.name))
        self.lifecycle = self.box.daemon_on(self.box.paths("runtime"))
        self.token, self.lease_id = self.box.lease(self.lifecycle)
        argv = self.box.server_argv(self.box.profiles("server"))
        self.box.host.spawn(5001, argv, self.box.clock - 600.0)
        self.box.host.spawn(5002, self.box.client_argv(self.box.profiles("client")), self.box.clock - 300.0)
        self.records = [
            self.box.host.record(5001, "server"),
            self.box.host.record(5002, "client"),
        ]
        self.lifecycle.manifest.add(
            RunRecord(RUN_ID, None, None, "UNRECONCILED", "red", "@Mod", "p", "m", list(self.records))
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _adopt_then_die(self) -> None:
        adopted = self.lifecycle.adopt_run(IDENTITY_A, self.token, RUN_ID)
        self.assertIs(adopted.get("ok"), True, adopted)
        for record in self.records:
            self.box.host.kill(record.pid)

    def test_stop_after_the_reaper_answers_the_reply_the_worker_maps_to_success(self) -> None:
        self._adopt_then_die()
        self.assertEqual(self.lifecycle.reap_dead_runs(), [RUN_ID])

        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token, RUN_ID)

        self.assertEqual(
            stopped,
            {
                "ok": True,
                "run_id": RUN_ID,
                "state": "EXITED",
                "terminated": 0,
                "stop_method": "already_exited",
                "exit_metrics_valid": False,
            },
        )
        # The exact predicate the sealed worker applies to the stop reply.
        self.assertTrue(dayz_test_worker._successful_run(stopped, RUN_ID, "EXITED"))
        self.assertEqual(self.box.host.terminate_calls, [])
        row = self.lifecycle.manifest.get(RUN_ID)
        self.assertEqual((row.state, row.owner_session_id, row.processes), ("EXITED", None, []))
        stops = [e for e in self.box.audit.events if e.get("event") == "lifecycle_stop"]
        self.assertEqual(stops[-1]["reason"], "already_reaped")
        outcomes = [
            e for e in self.box.audit.events if e.get("event") == "lifecycle_stop_outcome"
        ]
        self.assertEqual(
            (outcomes[-1]["decision"], outcomes[-1]["state"], outcomes[-1]["terminated"]),
            ("already_exited", "EXITED", 0),
        )

    def test_control_without_the_reaper_the_stop_itself_retires_the_run(self) -> None:
        self._adopt_then_die()

        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token, RUN_ID)

        self.assertEqual(stopped.get("stop_method"), "no_live_owned", stopped)
        self.assertTrue(dayz_test_worker._successful_run(stopped, RUN_ID, "EXITED"))
        self.assertEqual(self.box.host.terminate_calls, [])

    def test_a_repeated_stop_gets_the_same_answer(self) -> None:
        self._adopt_then_die()
        self.lifecycle.reap_dead_runs()

        first = self.lifecycle.stop_run(IDENTITY_A, self.token, RUN_ID)
        second = self.lifecycle.stop_run(IDENTITY_A, self.token, RUN_ID)

        self.assertEqual(first, second)
        self.assertEqual(second.get("stop_method"), "already_exited")

    def test_another_session_is_still_refused(self) -> None:
        self._adopt_then_die()
        self.lifecycle.reap_dead_runs()
        self.lifecycle.coordinator.release(IDENTITY_A, self.token)
        token_b, _lease_b = self.box.lease(self.lifecycle, IDENTITY_B)

        stopped = self.lifecycle.stop_run(IDENTITY_B, token_b, RUN_ID)

        self.assertEqual(stopped.get("error"), "run_not_adopted", stopped)
        self.assertNotIn("ok", stopped)

    def test_the_same_session_under_a_new_lease_is_still_refused(self) -> None:
        self._adopt_then_die()
        self.lifecycle.reap_dead_runs()
        self.lifecycle.coordinator.release(IDENTITY_A, self.token)
        token, lease_id = self.box.lease(self.lifecycle)
        self.assertNotEqual(lease_id, self.lease_id)

        stopped = self.lifecycle.stop_run(IDENTITY_A, token, RUN_ID)

        self.assertEqual(stopped.get("error"), "run_not_adopted", stopped)

    def test_a_run_reaped_without_an_owner_is_still_refused(self) -> None:
        for record in self.records:
            self.box.host.kill(record.pid)
        self.assertEqual(self.lifecycle.reap_dead_runs(), [RUN_ID])

        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token, RUN_ID)

        self.assertEqual(stopped.get("error"), "run_not_adopted", stopped)

    def test_a_process_that_reads_alive_again_is_never_answered_success(self) -> None:
        """Fail closed: the answer re-reads the reaped identities at stop time."""
        self._adopt_then_die()
        self.lifecycle.reap_dead_runs()
        for doubt in (
            # The same full identity as the record: owned, i.e. alive.
            {
                "pid": 5001,
                "creation_time_utc": self.records[0].creation_time_utc,
                "executable_sha256": self.records[0].executable_sha256,
                "command_line_sha256": self.records[0].command_line_sha256,
                "identity_scheme": "psutil-argv-v2",
                "identity_complete": True,
            },
            # A guard that cannot tell.
            {"pid": 5001, "error": "identity_unavailable", "exit_code": 3},
        ):
            with self.subTest(doubt=doubt.get("error", "owned")):
                self.box.host.snapshots[5001] = doubt
                stopped = self.lifecycle.stop_run(IDENTITY_A, self.token, RUN_ID)
                self.assertEqual(stopped.get("error"), "run_not_adopted", stopped)
                self.assertEqual(self.box.host.terminate_calls, [])

    def test_an_audit_failure_refuses_the_answer(self) -> None:
        self._adopt_then_die()
        self.lifecycle.reap_dead_runs()
        self.box.audit.fail_events.add("lifecycle_stop")

        stopped = self.lifecycle.stop_run(IDENTITY_A, self.token, RUN_ID)

        self.assertEqual(stopped.get("error"), "audit_failed", stopped)


class _LifecycleBroker:
    """The native broker as the sealed worker sees it, wired to one lifecycle.

    Each LIFECYCLE_CLI frame is answered by the lifecycle method its HTTP route
    calls (loopback._handle_lifecycle), with _http_status popped the way the
    route pops it. after_adopt runs between the adopt and the next frame: the
    window the reaper thread can take.
    """

    def __init__(self, lifecycle: ProcessLifecycle, token: str, after_adopt) -> None:
        self.lifecycle = lifecycle
        self.token = token
        self.after_adopt = after_adopt
        self.commands: list[str] = []

    async def invoke(self, frame: bytes) -> dict[str, object]:
        request = native_broker_protocol.decode_request(frame)
        command = str(request.payload["command"])
        run_id = request.payload["run_id"]
        self.commands.append(command)
        if command == "adopt":
            result = self.lifecycle.adopt_run(IDENTITY_A, self.token, run_id)
            self.after_adopt()
        elif command == "stop":
            result = self.lifecycle.stop_run(IDENTITY_A, self.token, run_id)
        elif command == "status":
            result = dict(self.lifecycle.status(IDENTITY_A))
            result["runs"] = [
                row for row in result.get("runs", []) if row.get("run_id") == run_id
            ]
        else:
            raise AssertionError(f"unexpected lifecycle command {command}")
        result = dict(result)
        result.pop("_http_status", None)
        return result


_POLICY = dayz_test_request.RequestProjectPolicy(
    mod="ExampleMod",
    dev_root=r"P:\ExampleMod_Suite",
    default_source=r"P:\ExampleMod",
    default_base_mods=("@CF",),
    mission_roots=(r"C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions",),
    mod_roots=(r"P:\Mods",),
)
_RUNTIME = dayz_test_worker.WorkerRuntimePolicy(
    dev_root=_POLICY.dev_root,
    mod="ExampleMod",
    diag_executable=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe",
    game_directory=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
    mission_aliases=(
        ("chernarus", r"C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions\dayzOffline.chernarusplus"),
        ("livonia", r"C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions\dayzOffline.enoch"),
        ("sakhal", r"C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions\dayzOffline.sakhal"),
    ),
    mods_root=r"P:\Mods",
    build_temp_root=r"P:\temp",
    build_source_basename=None,
)


class WorkerStopAfterReapTest(unittest.TestCase):
    """dayz_test_stop through the SEALED worker, with the reaper in a window.

    Between the adopt and the stop, the worker still reached success before
    this change, but only through its status fallback (fb-20260918-165857-744a):
    adopt, stop -> run_not_adopted, status. Between the status read of the tool
    and the adopt, the adopt answers run_not_adoptable and the reconcile branch
    of the worker (dayz_test_worker.py:690-699) has no such fallback: the stop
    failed with run_not_adoptable. The daemon's own reply is now enough in both.
    """

    def _stop_through_the_worker(self, *, reap_before_adopt: bool):
        with TemporaryDirectory() as temporary:
            box = _Box(Path(temporary))
            lifecycle = box.daemon_on(box.paths("runtime"))
            token, lease_id = box.lease(lifecycle)
            records = box.server_run(lifecycle, lease_id, with_client=True)

            def reap() -> None:
                for record in records:
                    box.host.kill(record.pid)
                self.assertEqual(lifecycle.reap_dead_runs(), [RUN_ID])

            if reap_before_adopt:
                reap()
            broker = _LifecycleBroker(
                lifecycle, token, (lambda: None) if reap_before_adopt else reap
            )
            raw = json.dumps(
                {
                    "version": 1,
                    "dev_root": _POLICY.dev_root,
                    "mod": _POLICY.mod,
                    "mode": "offline",
                    "kill": True,
                    "run_id": RUN_ID,
                }
            ).encode("utf-8")
            parsed = dayz_test_request.parse_dayz_test_request(raw, policies=(_POLICY,))

            result = asyncio.run(
                dayz_test_worker.execute_dayz_test_worker(
                    parsed.canonical_bytes,
                    request_sha256=parsed.sha256,
                    request_policies=(_POLICY,),
                    runtime_policy=_RUNTIME,
                    broker=broker,
                )
            )
            self.assertEqual(box.host.terminate_calls, [])
            return result, broker.commands

    def test_reaped_between_adopt_and_stop(self) -> None:
        result, commands = self._stop_through_the_worker(reap_before_adopt=False)

        self.assertEqual(result, dayz_test_worker.WorkerResult(0, RUN_ID))
        self.assertEqual(commands, ["adopt", "stop"])

    def test_reaped_before_the_adopt(self) -> None:
        result, commands = self._stop_through_the_worker(reap_before_adopt=True)

        self.assertEqual(result, dayz_test_worker.WorkerResult(0, RUN_ID))
        self.assertEqual(commands, ["adopt", "stop"])


# --------------------------------------------------------------------- part 2


class LaunchIntentWriteTest(unittest.TestCase):
    """start_run: the intent exists exactly while the durable row is STARTING."""

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.box = _Box(Path(self.temporary.name))
        self.paths = self.box.paths("runtime")
        self.lifecycle = self.box.daemon_on(self.paths, bound=True)
        self.token, self.lease_id = self.box.lease(self.lifecycle)
        self.box.server_run(self.lifecycle, self.lease_id)
        self.profiles = self.box.profiles("client")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _start(self) -> dict[str, object]:
        return self.lifecycle.start_run(
            IDENTITY_A, self.token, self.box.client_request(self.profiles, RUN_ID)
        )

    def test_the_intent_names_the_launch_before_the_popen(self) -> None:
        seen: dict[str, object] = {}
        launch = self.box.launch

        def watching(argv: list[str], cwd: str, window_style: str) -> Handle:
            intent = self.box.intent_path(self.paths)
            seen["intent"] = json.loads(intent.read_text(encoding="utf-8"))
            seen["instance"] = json.loads(
                (self.profiles / "dayz_mcp.json").read_text(encoding="utf-8")
            )["instance"]
            seen["argv"] = list(argv)
            return launch(argv, cwd, window_style)

        self.lifecycle.launcher = watching

        result = self._start()

        self.assertTrue(result.get("ok"), result)
        intent = seen["intent"]
        self.assertEqual(set(intent), INTENT_FIELDS)
        argv = seen["argv"]
        self.assertEqual(
            intent,
            {
                "version": 1,
                "run_id": RUN_ID,
                "role": "client",
                "instance": seen["instance"],
                "profiles": str(self.profiles),
                "command_line_sha256": identity_hashes(argv[0], argv)["command_line_sha256"],
                "owner_session_id": IDENTITY_A.session_id,
                "owner_lease_id": self.lease_id,
                "not_before_utc": format_creation_time_utc(self.box.clock),
            },
        )
        self.assertEqual(uuid.UUID(str(intent["instance"])).version, 4)
        self.assertFalse(self.box.intent_path(self.paths).exists())
        self.assertNotIn(
            "lifecycle_launch_intent_unmatchable",
            [event.get("event") for event in self.box.audit.events],
        )

    def test_the_intent_is_cleared_after_a_failed_launch(self) -> None:
        seen: list[bool] = []

        def refusing(argv: list[str], cwd: str, window_style: str) -> Handle:
            seen.append(self.box.intent_path(self.paths).exists())
            raise OSError("spawn failed")

        self.lifecycle.launcher = refusing

        result = self._start()

        self.assertEqual(seen, [True])
        self.assertEqual(result.get("error"), "lifecycle_start_failed", result)
        self.assertEqual(self.lifecycle.manifest.get(RUN_ID).state, "RUNNING")
        self.assertFalse(self.box.intent_path(self.paths).exists())

    def test_the_intent_is_kept_while_the_row_is_still_starting(self) -> None:
        """A settlement that could not persist leaves STARTING: the trace stays."""
        store = self.lifecycle.manifest
        write = store.replace

        def failing(run: RunRecord) -> None:
            if run.state != "STARTING":
                raise OSError("disk")
            write(run)

        def refusing(argv: list[str], cwd: str, window_style: str) -> Handle:
            store.replace = failing  # type: ignore[method-assign]
            raise OSError("spawn failed")

        self.lifecycle.launcher = refusing

        result = self._start()

        self.assertIn("manifest_failed", result.get("cleanup_degraded", []), result)
        self.assertEqual(store.get(RUN_ID).state, "STARTING")
        self.assertTrue(self.box.intent_path(self.paths).exists())

    def test_an_intent_that_cannot_be_written_refuses_the_launch(self) -> None:
        with patch.object(
            self.lifecycle, "_write_launch_intent", side_effect=OSError("disk full")
        ):
            result = self._start()

        self.assertEqual(result.get("error"), "launch_intent_failed", result)
        self.assertEqual(self.box.launches, [])
        row = self.lifecycle.manifest.get(RUN_ID)
        self.assertEqual((row.state, [r.role for r in row.processes]), ("RUNNING", ["server"]))
        minted = json.loads((self.profiles / "dayz_mcp.json").read_text(encoding="utf-8"))[
            "instance"
        ]
        self.assertIn(minted, self.lifecycle.bindings._retired_instances)

    def test_no_intent_without_a_bridge_token(self) -> None:
        self.lifecycle.bindings = None
        seen: list[bool] = []
        launch = self.box.launch

        def watching(argv: list[str], cwd: str, window_style: str) -> Handle:
            seen.append(self.box.intent_path(self.paths).exists())
            return launch(argv, cwd, window_style)

        self.lifecycle.launcher = watching

        result = self._start()

        self.assertTrue(result.get("ok"), result)
        self.assertEqual(seen, [False])

    def test_a_launch_the_intent_could_not_match_is_audited(self) -> None:
        """What a promotion window reads: a real launch the recovery would miss."""
        launch = self.box.launch

        def other_argv(argv: list[str], cwd: str, window_style: str) -> Handle:
            handle = launch(argv, cwd, window_style)
            self.box.host.spawn(handle.pid, argv + ["-extra"], self.box.clock + 0.25)
            return handle

        self.lifecycle.launcher = other_argv

        result = self._start()

        self.assertTrue(result.get("ok"), result)
        unmatchable = [
            event
            for event in self.box.audit.events
            if event.get("event") == "lifecycle_launch_intent_unmatchable"
        ]
        self.assertEqual(len(unmatchable), 1, unmatchable)
        self.assertEqual(unmatchable[0]["reason"], "argv_mismatch")


class LaunchIntentRecoveryTest(unittest.TestCase):
    """repro_A1_crash.py as unit tests: crash images, then the next daemon."""

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.box = _Box(Path(self.temporary.name))
        self.paths = self.box.paths("runtime")
        self.lifecycle = self.box.daemon_on(self.paths, bound=True)
        self.token, self.lease_id = self.box.lease(self.lifecycle)
        self.profiles = self.box.profiles("client")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _crash_during_client_start(self, point: str, *, with_client: bool = False) -> list[ProcessRecord]:
        records = self.box.server_run(self.lifecycle, self.lease_id, with_client=with_client)
        witness = None
        if with_client:
            now = time.time()
            witness = int(now * 1000)
            _freeze_witness(self.lifecycle, now)
        self.box.crash_at = point
        self.lifecycle.start_run(
            IDENTITY_A,
            self.token,
            self.box.client_request(self.profiles, RUN_ID, witness=witness),
        )
        self.assertIsNotNone(self.box.image, "the crash point was never reached")
        return records

    def _restart(self) -> ProcessLifecycle:
        assert self.box.image is not None
        self.box.crash_at = None
        return self.box.daemon_on(self.box.image)

    def _retired(self, outcome: dict[str, object], reason: str) -> None:
        self.assertEqual(outcome.get("launch_intent"), "retired", outcome)
        self.assertEqual(outcome.get("reason"), reason, outcome)
        self.assertFalse(self.box.intent_path(self.box.image).exists())
        retired = [
            event
            for event in self.box.audit.events
            if event.get("event") == "lifecycle_launch_intent_retired"
        ]
        self.assertEqual(retired[-1]["reason"], reason)

    def test_crash_between_the_popen_and_the_manifest_records_the_process(self) -> None:
        records = self._crash_during_client_start("after_popen")
        restarted = self._restart()

        outcome = restarted.recover_launch_intent()

        self.assertEqual(outcome.get("launch_intent"), "recovered", outcome)
        orphan = self.box.host.record(7001, "client")
        row = restarted.manifest.get(RUN_ID)
        self.assertEqual(
            (row.state, row.owner_session_id, row.owner_lease_id, row.processes),
            ("RUNNING", IDENTITY_A.session_id, self.lease_id, [records[0], orphan]),
        )
        self.assertFalse(self.box.intent_path(self.box.image).exists())
        # Then the restart recovery gives it what it gives any RUNNING run of a
        # vanished owner (#177 pinned that scope).
        restarted.manifest.recover_after_restart()
        row = restarted.manifest.get(RUN_ID)
        self.assertEqual(
            (row.state, row.owner_session_id, row.processes),
            ("RUNNING_IDLE", None, [records[0], orphan]),
        )
        self.assertEqual(self.box.host.terminate_calls, [])

    def test_crash_after_superseding_the_client_is_the_audit_point_five(self) -> None:
        records = self._crash_during_client_start("after_popen", with_client=True)
        self.assertEqual([r.pid for r in self.box.host.terminate_calls], [records[1].pid])
        restarted = self._restart()

        outcome = restarted.recover_launch_intent()

        self.assertEqual(outcome.get("launch_intent"), "recovered", outcome)
        row = restarted.manifest.get(RUN_ID)
        self.assertEqual(
            [(r.pid, r.role) for r in row.processes], [(5001, "server"), (7001, "client")]
        )
        self.assertEqual([r.pid for r in self.box.host.terminate_calls], [records[1].pid])

    def test_crash_between_the_intent_and_the_popen_retires_the_intent(self) -> None:
        records = self._crash_during_client_start("before_popen")
        restarted = self._restart()

        outcome = restarted.recover_launch_intent()

        self._retired(outcome, "process_not_found")
        row = restarted.manifest.get(RUN_ID)
        self.assertEqual((row.state, row.processes), ("STARTING", [records[0]]))
        restarted.manifest.recover_after_restart()
        self.assertEqual(restarted.manifest.get(RUN_ID).state, "UNRECONCILED")
        self.assertEqual(self.box.host.terminate_calls, [])

    def test_crash_after_the_manifest_never_duplicates_the_record(self) -> None:
        records = self._crash_during_client_start("after_manifest")
        self.assertTrue(self.box.intent_path(self.box.image).exists())
        restarted = self._restart()

        outcome = restarted.recover_launch_intent()

        self._retired(outcome, "launch_settled")
        row = restarted.manifest.get(RUN_ID)
        self.assertEqual(
            (row.state, row.processes),
            ("RUNNING", [records[0], self.box.host.record(7001, "client")]),
        )

    def test_a_clean_start_leaves_no_intent_behind(self) -> None:
        """The legacy path: a daemon upgrade starts on a folder with no intent."""
        self.box.server_run(self.lifecycle, self.lease_id)
        result = self.lifecycle.start_run(
            IDENTITY_A, self.token, self.box.client_request(self.profiles, RUN_ID)
        )
        self.assertTrue(result.get("ok"), result)
        before = self.paths.runs_path.read_bytes()
        restarted = self.box.daemon_on(self.paths)

        outcome = restarted.recover_launch_intent()

        self.assertEqual(outcome, {"launch_intent": "absent"})
        self.assertEqual(self.paths.runs_path.read_bytes(), before)

    def test_a_stale_intent_of_a_settled_launch_is_retired(self) -> None:
        self._crash_during_client_start("before_popen")
        restarted = self._restart()
        row = restarted.manifest.get(RUN_ID)
        row.state = "EXITED"
        row.owner_session_id = None
        row.owner_lease_id = None
        row.processes = []
        restarted.manifest.replace(row)

        outcome = restarted.recover_launch_intent()

        self._retired(outcome, "launch_settled")
        self.assertEqual(restarted.manifest.get(RUN_ID).state, "EXITED")

    def test_an_argv_match_with_the_wrong_token_is_never_adopted(self) -> None:
        self._crash_during_client_start("after_popen")
        config = self.profiles / "dayz_mcp.json"
        payload = json.loads(config.read_text(encoding="utf-8"))
        payload["instance"] = str(uuid.uuid4())
        config.write_text(json.dumps(payload), encoding="utf-8")
        restarted = self._restart()

        outcome = restarted.recover_launch_intent()

        self._retired(outcome, "instance_mismatch")
        row = restarted.manifest.get(RUN_ID)
        self.assertEqual([r.role for r in row.processes], ["server"])
        self.assertIn(7001, self.box.host.images)
        self.assertEqual(self.box.host.terminate_calls, [])

    def test_a_process_older_than_the_intent_is_never_adopted(self) -> None:
        self.box.spawn_delay_s = -30.0
        self._crash_during_client_start("after_popen")
        restarted = self._restart()

        outcome = restarted.recover_launch_intent()

        self._retired(outcome, "created_outside_intent")
        self.assertEqual(
            [r.role for r in restarted.manifest.get(RUN_ID).processes], ["server"]
        )

    def test_two_processes_with_the_argv_are_never_adopted(self) -> None:
        self._crash_during_client_start("after_popen")
        self.box.host.spawn(7100, self.box.client_argv(self.profiles), self.box.clock + 0.5)
        restarted = self._restart()

        outcome = restarted.recover_launch_intent()

        self._retired(outcome, "process_ambiguous")
        self.assertEqual(self.box.host.terminate_calls, [])

    def test_a_dayz_the_guard_cannot_read_stops_the_match(self) -> None:
        self._crash_during_client_start("after_popen")
        self.box.host.images[7200] = "DayZDiag_x64.exe"
        self.box.host.snapshots[7200] = {
            "pid": 7200,
            "error": "identity_unavailable",
            "exit_code": 3,
        }
        restarted = self._restart()

        outcome = restarted.recover_launch_intent()

        self._retired(outcome, "identity_unavailable")

    def test_an_unknown_process_scan_retires_the_intent(self) -> None:
        self._crash_during_client_start("after_popen")
        self.box.host.diag_known = False
        restarted = self._restart()

        outcome = restarted.recover_launch_intent()

        self._retired(outcome, "diag_snapshot_unknown")

    def test_retail_quarantine_retires_the_intent(self) -> None:
        self._crash_during_client_start("after_popen")
        self.box.retail = {"known": True, "processes": [{"pid": 77, "name": "DayZ_x64.exe"}]}
        restarted = self._restart()

        outcome = restarted.recover_launch_intent()

        self._retired(outcome, "retail_quarantine")

    def test_an_unreadable_intent_is_retired_without_touching_the_manifest(self) -> None:
        self._crash_during_client_start("after_popen")
        assert self.box.image is not None
        self.box.intent_path(self.box.image).write_text("{", encoding="utf-8")
        before = self.box.image.runs_path.read_bytes()
        restarted = self._restart()

        outcome = restarted.recover_launch_intent()

        self._retired(outcome, "intent_unreadable")
        self.assertEqual(self.box.image.runs_path.read_bytes(), before)

    def test_a_recovery_that_cannot_be_audited_keeps_the_intent(self) -> None:
        self._crash_during_client_start("after_popen")
        restarted = self._restart()
        self.box.audit.fail_events.add("lifecycle_launch_intent_recovered")

        outcome = restarted.recover_launch_intent()

        self.assertEqual(
            (outcome.get("launch_intent"), outcome.get("reason")), ("kept", "audit_failed")
        )
        self.assertEqual(restarted.manifest.get(RUN_ID).state, "STARTING")
        self.assertTrue(self.box.intent_path(self.box.image).exists())


class LaunchIntentActivationTest(unittest.TestCase):
    """daemon._activate_server_coordination runs the recovery before the rest."""

    def _activate(self, box: _Box, localappdata: Path) -> loopback.ServerState:
        state = loopback.ServerState("key")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(localappdata)}), patch.object(
            daemon, "NativeProcessGuard", return_value=box.host
        ), patch.object(
            daemon.orphan_guard,
            "snapshot_retail_processes",
            return_value={"known": True, "processes": []},
        ), patch.object(
            daemon.orphan_guard,
            "snapshot_processes_by_name",
            side_effect=lambda _names: box.host.diag(),
        ), patch.object(
            daemon.orphan_guard,
            "snapshot_udp_port_holders",
            return_value={"known": True, "holders": []},
        ):
            daemon._activate_server_coordination(state, "generation-after-the-crash")
        return state

    def _image_into(self, box: _Box, localappdata: Path) -> None:
        assert box.image is not None
        target = localappdata / "DayZ_MCP"
        target.mkdir(parents=True)
        for name in ("runs.json", "launch-intent.json"):
            source = box.image.root / name
            if source.exists():
                shutil.copyfile(source, target / name)

    def test_a_client_launched_before_the_crash_rejoins_its_run_idle(self) -> None:
        with TemporaryDirectory() as temporary:
            box = _Box(Path(temporary))
            live = box.daemon_on(RuntimePaths.from_env({"LOCALAPPDATA": temporary}), bound=True)
            token, lease_id = box.lease(live)
            records = box.server_run(live, lease_id)
            box.crash_at = "after_popen"
            live.start_run(IDENTITY_A, token, box.client_request(box.profiles("client"), RUN_ID))
            after = Path(temporary) / "after"
            self._image_into(box, after)

            state = self._activate(box, after)

            row = state.lifecycle.manifest.get(RUN_ID)
            self.assertEqual(
                (row.state, row.owner_session_id, row.processes),
                ("RUNNING_IDLE", None, [records[0], box.host.record(7001, "client")]),
            )
            self.assertEqual(box.host.terminate_calls, [])
            self.assertFalse((after / "DayZ_MCP" / "launch-intent.json").exists())

    def test_an_unacknowledged_server_launch_is_cleaned_like_any_other(self) -> None:
        """Its row names the process now, so the unacknowledged recovery ends it."""
        new_run = "11111111-1111-4111-8111-111111111111"
        with TemporaryDirectory() as temporary:
            box = _Box(Path(temporary))
            live = box.daemon_on(RuntimePaths.from_env({"LOCALAPPDATA": temporary}), bound=True)
            token, _lease_id = box.lease(live)
            box.crash_at = "after_popen"
            live.start_run(
                IDENTITY_A, token, box.new_server_request(box.profiles("server"), new_run)
            )
            after = Path(temporary) / "after"
            self._image_into(box, after)
            orphan = box.host.record(7001, "server")

            state = self._activate(box, after)

            row = state.lifecycle.manifest.get(new_run)
            self.assertEqual((row.state, row.processes), ("EXITED", []))
            self.assertEqual(box.host.terminate_calls, [orphan])
            self.assertNotIn(7001, box.host.images)


if __name__ == "__main__":
    unittest.main()
