"""g5-r9: lock sharing, owner identity, relative shared root, build wait, settled writers."""

from __future__ import annotations

import asyncio
import gc
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from dayz_mcp import dayz_test_request, dayz_test_worker, native_broker_protocol
from dayz_mcp.box_admission import box_admission
from dayz_mcp.identity_migration import (
    RunsBackupGateError,
    _exclusive_gate_lock,
    daemon_startup_election,
    ensure_runs_v1_backup,
    scan_dayz_mcp_processes,
)
from dayz_mcp.instance_context import RootWriterLease
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.server_cli import shared_build_lock, shared_root


_POLICY = dayz_test_request.RequestProjectPolicy(
    mod="ExampleMod",
    dev_root=r"P:\ExampleMod_Suite",
    default_source=r"P:\ExampleMod",
    default_base_mods=("@CF",),
    mission_roots=(r"C:\missions",),
    mod_roots=(r"P:\Mods",),
)
_RUNTIME = dayz_test_worker.WorkerRuntimePolicy(
    dev_root=_POLICY.dev_root,
    mod=_POLICY.mod,
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
_PYTHON = r"C:\Python314\python.exe"


class _Marker:
    pass


class _Process:
    def __init__(self, pid: int, argv: list[str], cwd: str | None) -> None:
        self.info = {"pid": pid, "name": "python.exe"}
        self._argv = argv
        self._cwd = cwd

    def oneshot(self):
        return mock.MagicMock(__enter__=lambda _s: None, __exit__=lambda *_a: None)

    def exe(self) -> str:
        return _PYTHON

    def cmdline(self) -> list[str]:
        return self._argv

    def cwd(self) -> str:
        if self._cwd is None:
            raise OSError("cwd_unavailable")
        return self._cwd


class _Psutil:
    def __init__(self, processes: list[_Process]) -> None:
        self._processes = processes

    def process_iter(self, _fields, ad_value=None):
        del ad_value
        return self._processes


class _Broker:
    def __init__(self) -> None:
        self.requests: list[native_broker_protocol.BrokerRequest] = []

    async def invoke(self, frame: bytes) -> dict[str, object]:
        request = native_broker_protocol.decode_request(frame)
        self.requests.append(request)
        if request.kind is native_broker_protocol.BrokerKind.ADDON_BUILDER:
            return {"ok": True, "exit_code": 0, "pbo_size": 8192}
        command = request.payload.get("command")
        if command in {"start", "ack", "adopt"}:
            return {"ok": True, "state": "RUNNING", "run_id": request.payload["run_id"]}
        return {"ok": True}


def _assert_delete_fails(testcase: unittest.TestCase, path: Path) -> None:
    with testcase.assertRaises(OSError):
        path.unlink()


class LockSharingTests(unittest.TestCase):
    def test_each_lock_kind_refuses_delete_while_held(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lease = RootWriterLease(root / "writer")
            self.assertTrue(lease.try_acquire())
            try:
                _assert_delete_fails(self, root / "writer" / ".daemon-startup.lock")
            finally:
                lease.release()

            paths = RuntimePaths(root / "elect", root / "elect" / "audit", root / "c.json", root / "runs.json")
            with daemon_startup_election(paths) as elected:
                self.assertTrue(elected)
                _assert_delete_fails(self, paths.root / ".daemon-startup.lock")

            migration = root / "migration" / ".runs-v1.lock"
            migration.parent.mkdir()
            with _exclusive_gate_lock(migration):
                _assert_delete_fails(self, migration)

            with mock.patch.dict(os.environ, {"DAYZ_MCP_SHARED_ROOT": str(root / "shared")}):
                entered = threading.Event()
                release = threading.Event()

                def hold_box() -> None:
                    with box_admission() as admitted:
                        self.assertTrue(admitted)
                        entered.set()
                        release.wait(5)

                worker = threading.Thread(target=hold_box)
                worker.start()
                self.assertTrue(entered.wait(2))
                try:
                    _assert_delete_fails(self, root / "shared" / "box-admission.lock")
                finally:
                    release.set()
                    worker.join(2)

                target = r"P:\Mods\@ExampleMod\Addons"
                temp = r"P:\temp\ExampleMod"
                with shared_build_lock(target, temp, wait_s=2):
                    from dayz_mcp.server_cli import shared_build_lock_paths

                    locks = shared_build_lock_paths(target, temp, root=root / "shared")
                    for lock in locks:
                        _assert_delete_fails(self, lock)

    def test_collected_owner_id_does_not_admit_a_new_object(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def abandoned() -> RootWriterLease:
                return RootWriterLease(root, owner=_Marker())

            lease = abandoned()
            self.assertTrue(lease.try_acquire())
            try:
                gc.collect()
                admitted = False
                for _ in range(10000):
                    rival = RootWriterLease(root, owner=_Marker())
                    if rival.try_acquire():
                        admitted = True
                        rival.release()
                        break
                self.assertFalse(admitted)
            finally:
                lease.release()


class RelativeSharedRootTests(unittest.TestCase):
    def test_relative_shared_root_is_refused_by_name(self) -> None:
        relative = "g5r9-relative-shared"
        with mock.patch.dict(os.environ, {"DAYZ_MCP_SHARED_ROOT": relative}):
            with self.assertRaises(OSError) as raised:
                shared_root()
            self.assertEqual(str(raised.exception), "relative_shared_root")
            with self.assertRaises(OSError) as admitted:
                with box_admission():
                    pass
            self.assertEqual(str(admitted.exception), "relative_shared_root")
        self.assertFalse((Path.cwd() / relative).exists())


class BuildLockWaitTests(unittest.TestCase):
    def _run_build(
        self,
        *,
        canonical: bytes | None = None,
        sha256: str | None = None,
        wait_s: float | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> tuple[_Broker, object]:
        if canonical is None:
            document: dict[str, object] = {
                "version": 1,
                "dev_root": _POLICY.dev_root,
                "mod": _POLICY.mod,
                "mode": "server",
                "build": True,
            }
            if wait_s is not None:
                document["build_lock_wait_s"] = wait_s
            parsed = dayz_test_request.parse_dayz_test_request(
                json.dumps(document).encode("utf-8"), policies=(_POLICY,)
            )
            canonical = parsed.canonical_bytes
            sha256 = parsed.sha256
        broker = _Broker()
        ids = iter(
            (
                "12345678-1234-4234-8234-1234567890ab",
                "87654321-4321-4321-8321-ba0987654321",
            )
        )

        async def _execute() -> dayz_test_worker.WorkerResult:
            return await dayz_test_worker.execute_dayz_test_worker(
                canonical,
                request_sha256=sha256 or "",
                request_policies=(_POLICY,),
                runtime_policy=_RUNTIME,
                broker=broker,
                id_fn=lambda: next(ids),
                has_binarizable_assets=lambda _source: False,
                cancel_event=cancel_event,
            )

        return broker, asyncio.run(_execute())

    def test_contender_waits_for_a_fifteen_second_holder_then_builds(self) -> None:
        target = r"P:\Mods\@ExampleMod\Addons"
        temp = r"P:\temp\ExampleMod"
        with tempfile.TemporaryDirectory() as temporary:
            started = threading.Event()
            finished = threading.Event()

            def hold() -> None:
                with shared_build_lock(target, temp, root=Path(temporary), wait_s=30):
                    started.set()
                    time.sleep(15)
                finished.set()

            holder = threading.Thread(target=hold)
            holder.start()
            self.assertTrue(started.wait(2))
            environment = {
                "DAYZ_MCP_SHARED_ROOT": temporary,
                "DAYZ_MCP_BUILD_LOCK_WAIT_S": "60",
            }
            began = time.monotonic()
            with mock.patch.dict(os.environ, environment):
                broker, result = self._run_build()
            elapsed = time.monotonic() - began
            holder.join(5)
            self.assertTrue(finished.is_set())
            self.assertGreaterEqual(elapsed, 14)
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(
                broker.requests[0].kind,
                native_broker_protocol.BrokerKind.ADDON_BUILDER,
            )

    def test_short_wait_answers_build_busy(self) -> None:
        target = r"P:\Mods\@ExampleMod\Addons"
        temp = r"P:\temp\ExampleMod"
        with tempfile.TemporaryDirectory() as temporary:
            started = threading.Event()
            release = threading.Event()

            def hold() -> None:
                with shared_build_lock(target, temp, root=Path(temporary), wait_s=30):
                    started.set()
                    release.wait(5)

            holder = threading.Thread(target=hold)
            holder.start()
            self.assertTrue(started.wait(2))
            try:
                with mock.patch.dict(os.environ, {"DAYZ_MCP_SHARED_ROOT": temporary}):
                    with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
                        self._run_build(wait_s=0.3)
                self.assertEqual(raised.exception.code, "build_busy")
            finally:
                release.set()
                holder.join(2)

    def test_sealed_request_carries_the_daemon_wait_into_a_stripped_worker(self) -> None:
        from dayz_mcp import dayz_test_tool
        from tests.dayz_test_tool_helpers import _sealed

        target = r"P:\Mods\@ExampleMod\Addons"
        temp = r"P:\temp\ExampleMod"
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(
                os.environ,
                {"DAYZ_MCP_SHARED_ROOT": temporary, "DAYZ_MCP_BUILD_LOCK_WAIT_S": "0.3"},
            ):
                raw, _selected = dayz_test_tool.build_run_request(
                    _sealed(_POLICY),
                    project="ExampleMod",
                    mode="server",
                    build=True,
                    extra_mods=["@DayZ_MCP"],
                )
            parsed = dayz_test_request.parse_dayz_test_request(raw, policies=(_POLICY,))
            self.assertEqual(parsed.payload["build_lock_wait_s"], 0.3)
            started = threading.Event()
            release = threading.Event()

            def hold() -> None:
                with shared_build_lock(target, temp, root=Path(temporary), wait_s=30):
                    started.set()
                    release.wait(5)

            holder = threading.Thread(target=hold)
            holder.start()
            self.assertTrue(started.wait(2))
            try:
                stripped = {
                    key: value
                    for key, value in os.environ.items()
                    if key != "DAYZ_MCP_BUILD_LOCK_WAIT_S"
                }
                stripped["DAYZ_MCP_SHARED_ROOT"] = temporary
                began = time.monotonic()
                with mock.patch.dict(os.environ, stripped, clear=True):
                    with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
                        self._run_build(canonical=parsed.canonical_bytes, sha256=parsed.sha256)
                self.assertEqual(raised.exception.code, "build_busy")
                self.assertLess(time.monotonic() - began, 2)
            finally:
                release.set()
                holder.join(2)

    def test_cancel_during_lock_wait_does_not_dispatch_the_builder(self) -> None:
        target = r"P:\Mods\@ExampleMod\Addons"
        temp = r"P:\temp\ExampleMod"
        with tempfile.TemporaryDirectory() as temporary:
            started = threading.Event()
            release = threading.Event()

            def hold() -> None:
                with shared_build_lock(target, temp, root=Path(temporary), wait_s=30):
                    started.set()
                    release.wait(5)

            holder = threading.Thread(target=hold)
            holder.start()
            self.assertTrue(started.wait(2))
            try:
                with mock.patch.dict(os.environ, {"DAYZ_MCP_SHARED_ROOT": temporary}):
                    began = time.monotonic()
                    broker, error = self._cancel_while_waiting(temporary)
                self.assertLess(time.monotonic() - began, 2)
                self.assertEqual(error.code, "operation_cancelled")
                self.assertFalse(broker.requests)
            finally:
                release.set()
                holder.join(2)

    def _cancel_while_waiting(self, temporary: str) -> tuple[_Broker, dayz_test_worker.DayzTestWorkerError]:
        del temporary
        document = {
            "version": 1,
            "dev_root": _POLICY.dev_root,
            "mod": _POLICY.mod,
            "mode": "server",
            "build": True,
            "build_lock_wait_s": 60,
        }
        parsed = dayz_test_request.parse_dayz_test_request(
            json.dumps(document).encode("utf-8"), policies=(_POLICY,)
        )
        broker = _Broker()
        ids = iter(
            (
                "12345678-1234-4234-8234-1234567890ab",
                "87654321-4321-4321-8321-ba0987654321",
            )
        )

        async def _execute() -> None:
            cancel_event = asyncio.Event()
            loop = asyncio.get_running_loop()

            def queue_cancel() -> None:
                time.sleep(0.1)
                loop.call_soon_threadsafe(cancel_event.set)

            threading.Thread(target=queue_cancel).start()
            await dayz_test_worker.execute_dayz_test_worker(
                parsed.canonical_bytes,
                request_sha256=parsed.sha256,
                request_policies=(_POLICY,),
                runtime_policy=_RUNTIME,
                broker=broker,
                id_fn=lambda: next(ids),
                has_binarizable_assets=lambda _source: False,
                cancel_event=cancel_event,
            )

        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            asyncio.run(_execute())
        return broker, raised.exception


class SettledWriterTests(unittest.TestCase):
    def test_settled_receipt_still_blocks_a_same_root_legacy_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            paths = RuntimePaths(
                runtime,
                runtime / "audit",
                runtime / "coordination.json",
                runtime / "runs.json",
            )
            migration = root / "migration"
            paths.root.mkdir(parents=True)
            paths.runs_path.write_bytes(b'{"runs":[{"run_id":"settled"}]}\n')
            ensure_runs_v1_backup(
                paths,
                8765,
                migration_dir=migration,
                scan_fn=lambda _allowed: (),
                listener_fn=lambda _port: False,
            )
            legacy = [
                _PYTHON,
                "-m",
                "dayz_mcp",
                "--daemon",
                "--keyfile",
                r"C:\keys\K",
            ]
            client = [
                _PYTHON,
                "-m",
                "dayz_mcp",
                "--client",
                "--keyfile",
                r"C:\keys\K",
                "--port",
                "8765",
            ]

            def scan(processes: list[_Process]):
                def _scan(_allowed, **_kwargs):
                    return scan_dayz_mcp_processes(
                        psutil_module=_Psutil(processes),
                        state_token=None,
                    )

                return _scan

            with self.assertRaisesRegex(RunsBackupGateError, "dayz_mcp_process_present"):
                ensure_runs_v1_backup(
                    paths,
                    8765,
                    migration_dir=migration,
                    scan_fn=scan([_Process(41, legacy, None)]),
                    listener_fn=lambda _port: False,
                )
            receipt = ensure_runs_v1_backup(
                paths,
                8765,
                migration_dir=migration,
                scan_fn=scan([_Process(42, client, None)]),
                listener_fn=lambda _port: False,
            )
            self.assertIn("source", receipt)

    def test_settled_receipt_ignores_an_accredited_other_root_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "tree" / "dayz_mcp"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "instance_context.py").write_text(
                'ROOT_SELECTION_CONTRACT_ID = "97de-v1"\n',
                encoding="utf-8",
            )
            runtime = root / "runtime"
            paths = RuntimePaths(
                runtime,
                runtime / "audit",
                runtime / "coordination.json",
                runtime / "runs.json",
            )
            migration = root / "migration"
            paths.root.mkdir(parents=True)
            paths.runs_path.write_bytes(b'{"runs":[]}\n')
            ensure_runs_v1_backup(
                paths,
                8765,
                migration_dir=migration,
                scan_fn=lambda _allowed: (),
                listener_fn=lambda _port: False,
            )
            argv = [
                _PYTHON,
                "-m",
                "dayz_mcp",
                "--daemon",
                "--keyfile",
                r"C:\keys\K",
                "--instance",
                "other",
            ]

            def _scan(_allowed, **_kwargs):
                return scan_dayz_mcp_processes(
                    psutil_module=_Psutil([_Process(77, argv, str(root / "tree"))]),
                    state_token="130",
                )

            receipt = ensure_runs_v1_backup(
                paths,
                8765,
                migration_dir=migration,
                scan_fn=_scan,
                listener_fn=lambda _port: False,
                state_token="130",
            )
            self.assertIn("source", receipt)


if __name__ == "__main__":
    unittest.main()
