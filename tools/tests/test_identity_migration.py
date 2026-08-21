from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import nullcontext, redirect_stderr
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from dayz_mcp.identity_migration import (
    RunsBackupGateError,
    capture_launch_ancestor_identity,
    daemon_startup_election,
    ensure_runs_v1_backup,
    scan_dayz_mcp_processes,
)
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.native_process_guard import identity_hashes


class RunsBackupGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        runtime = self.root / "runtime"
        self.paths = RuntimePaths(
            runtime,
            runtime / "audit",
            runtime / "coordination.json",
            runtime / "runs.json",
        )
        self.migration = self.root / "migration"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_gate(self, **kwargs: object) -> dict[str, object]:
        return ensure_runs_v1_backup(
            self.paths,
            8765,
            migration_dir=self.migration,
            scan_fn=kwargs.pop("scan_fn", lambda _allowed: ()),
            listener_fn=kwargs.pop("listener_fn", lambda _port: False),
            **kwargs,
        )

    def test_present_source_is_copied_byte_exact_and_receipt_is_create_only(self) -> None:
        source = b'{"runs":[{"run_id":"legacy"}]}\r\n'
        self.paths.root.mkdir(parents=True)
        self.paths.runs_path.write_bytes(source)
        scans: list[object] = []

        receipt = self.run_gate(scan_fn=lambda allowed: scans.append(allowed) or ())

        backup = self.migration / "runs.pre-v2.json"
        receipt_path = self.migration / "runs-backup-receipt.json"
        self.assertEqual(backup.read_bytes(), source)
        self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8")), receipt)
        self.assertFalse(receipt["source_absent"])
        self.assertEqual(receipt["source"], receipt["backup"])
        self.assertEqual(len(scans), 2)

        original_backup = backup.read_bytes()
        original_receipt = receipt_path.read_bytes()
        self.paths.runs_path.write_bytes(b'{"runs":[]}\n')
        second = self.run_gate()
        self.assertEqual(second, receipt)
        self.assertEqual(backup.read_bytes(), original_backup)
        self.assertEqual(receipt_path.read_bytes(), original_receipt)

    def test_absent_source_creates_receipt_but_not_a_fabricated_backup(self) -> None:
        receipt = self.run_gate()

        self.assertTrue(receipt["source_absent"])
        self.assertIsNone(receipt["source"])
        self.assertIsNone(receipt["backup"])
        self.assertFalse((self.migration / "runs.pre-v2.json").exists())
        self.assertTrue((self.migration / "runs-backup-receipt.json").is_file())

    def test_settled_migration_ignores_blockers_that_only_gate_writing(self) -> None:
        # The quiescence assert ran before the receipt fast path, so a
        # migration that had finished long ago still demanded an empty machine.
        # Once every open session's `-m dayz_mcp` client counted as a blocker, no
        # daemon could boot at all: each candidate died at its startup deadline
        # with nothing left to write.
        self.paths.root.mkdir(parents=True)
        self.paths.runs_path.write_bytes(b'{"runs":[{"run_id":"settled"}]}\n')
        receipt = self.run_gate()
        backup_bytes = (self.migration / "runs.pre-v2.json").read_bytes()
        receipt_bytes = (self.migration / "runs-backup-receipt.json").read_bytes()

        variants = (
            {"scan_fn": lambda _allowed: (1234,)},
            {"listener_fn": lambda _port: True},
        )
        for kwargs in variants:
            with self.subTest(kwargs=sorted(kwargs)):
                self.assertEqual(self.run_gate(**kwargs), receipt)

        # Read-only has to mean read-only, or the skipped gate would be a real hole.
        self.assertEqual((self.migration / "runs.pre-v2.json").read_bytes(), backup_bytes)
        self.assertEqual(
            (self.migration / "runs-backup-receipt.json").read_bytes(), receipt_bytes
        )

    def test_blockers_still_gate_every_receipt_state_with_work_left(self) -> None:
        # The controls that keep the fast path narrow. Each case below has a
        # receipt on disk and must still hit the gate, because each still has
        # something to write. Asserting the specific error is what proves the gate
        # was reached rather than some later validation.
        self.paths.root.mkdir(parents=True)
        self.paths.runs_path.write_bytes(b'{"runs":[]}\n')
        self.run_gate()
        receipt_path = self.migration / "runs-backup-receipt.json"
        published = receipt_path.read_bytes()
        blocked = {"scan_fn": lambda _allowed: (1234,)}

        leftovers = (
            ("runs-backup-transaction.json", "marker the recovery path unlinks"),
            ("runs-backup-receipt.pending", "interrupted publish"),
            ("runs-backup-transaction.next", "half written marker"),
        )
        for name, reason in leftovers:
            with self.subTest(leftover=reason):
                artifact = self.migration / name
                artifact.write_bytes(b"{}")
                with self.assertRaisesRegex(RunsBackupGateError, "dayz_mcp_process_present"):
                    self.run_gate(**blocked)
                artifact.unlink()

        with self.subTest(leftover="unreadable receipt"):
            receipt_path.write_bytes(b"not json")
            with self.assertRaisesRegex(RunsBackupGateError, "dayz_mcp_process_present"):
                self.run_gate(**blocked)
            receipt_path.write_bytes(published)

    def test_listener_or_dayz_process_blocks_before_backup_or_receipt(self) -> None:
        self.paths.root.mkdir(parents=True)
        self.paths.runs_path.write_bytes(b"legacy")
        variants = (
            {"listener_fn": lambda _port: True},
            {"scan_fn": lambda _allowed: (1234,)},
        )
        for kwargs in variants:
            with self.subTest(kwargs=sorted(kwargs)):
                with self.assertRaises(RunsBackupGateError):
                    self.run_gate(**kwargs)
                self.assertFalse((self.migration / "runs.pre-v2.json").exists())
                self.assertFalse((self.migration / "runs-backup-receipt.json").exists())

    def test_second_scan_or_source_drift_aborts_without_receipt(self) -> None:
        self.paths.root.mkdir(parents=True)
        self.paths.runs_path.write_bytes(b"legacy-v1")
        calls = 0

        def racing_scan(_allowed: object) -> tuple[int, ...]:
            nonlocal calls
            calls += 1
            if calls == 2:
                self.paths.runs_path.write_bytes(b"changed-after-backup")
            return ()

        with self.assertRaises(RunsBackupGateError):
            self.run_gate(scan_fn=racing_scan)
        self.assertTrue((self.migration / "runs.pre-v2.json").is_file())
        self.assertFalse((self.migration / "runs-backup-receipt.json").exists())

        with self.assertRaises(RunsBackupGateError):
            self.run_gate()

    def test_existing_receipt_or_backup_shape_drift_is_rejected(self) -> None:
        receipt = self.run_gate()
        receipt_path = self.migration / "runs-backup-receipt.json"
        payload = dict(receipt)
        payload["kind"] = "unexpected"
        receipt_path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(RunsBackupGateError):
            self.run_gate()

    def test_transient_second_scan_is_durably_recovered_on_next_run(self) -> None:
        self.paths.root.mkdir(parents=True)
        source = b'{"runs":[{"run_id":"recoverable"}]}\n'
        self.paths.runs_path.write_bytes(source)
        scans = 0

        def transient(_allowed: object) -> tuple[int, ...]:
            nonlocal scans
            scans += 1
            return (4321,) if scans == 2 else ()

        with self.assertRaisesRegex(RunsBackupGateError, "dayz_mcp_process_present"):
            self.run_gate(scan_fn=transient)
        self.assertTrue((self.migration / "runs-backup-transaction.json").is_file())
        self.assertTrue((self.migration / "runs.pre-v2.json").is_file())

        receipt = self.run_gate()

        self.assertEqual((self.migration / "runs.pre-v2.json").read_bytes(), source)
        self.assertFalse((self.migration / "runs-backup-transaction.json").exists())
        self.assertFalse((self.migration / "runs-backup-transaction.next").exists())
        self.assertFalse((self.migration / "runs-backup-receipt.pending").exists())
        self.assertEqual(receipt["source"], receipt["backup"])

    def test_crash_after_each_published_phase_is_reentrant(self) -> None:
        class SimulatedCrash(BaseException):
            pass

        phases = (
            "after_marker_prepared",
            "after_backup_write",
            "after_marker_backup_written",
            "after_second_quiescence",
            "after_pending_receipt",
            "after_marker_receipt_pending",
            "after_receipt_publish",
        )
        for phase in phases:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
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
                source = b'{"runs":[{"run_id":"phase"}]}\n'
                paths.runs_path.write_bytes(source)

                def crash(actual: str) -> None:
                    if actual == phase:
                        raise SimulatedCrash()

                with self.assertRaises(SimulatedCrash):
                    ensure_runs_v1_backup(
                        paths,
                        8765,
                        migration_dir=migration,
                        scan_fn=lambda _allowed: (),
                        listener_fn=lambda _port: False,
                        fault_injector=crash,
                    )

                receipt = ensure_runs_v1_backup(
                    paths,
                    8765,
                    migration_dir=migration,
                    scan_fn=lambda _allowed: (),
                    listener_fn=lambda _port: False,
                )
                self.assertEqual((migration / "runs.pre-v2.json").read_bytes(), source)
                self.assertEqual(receipt["source"], receipt["backup"])
                self.assertFalse((migration / "runs-backup-transaction.json").exists())
                self.assertFalse((migration / "runs-backup-transaction.next").exists())

    def test_initial_marker_temp_crash_is_reentrant_without_data_artifacts(self) -> None:
        class SimulatedCrash(BaseException):
            pass

        self.paths.root.mkdir(parents=True)
        self.paths.runs_path.write_bytes(b'{"runs":[]}\n')
        def crash_before_publish(actual: str) -> None:
            if actual == "marker_before_rename":
                raise SimulatedCrash()

        with self.assertRaises(SimulatedCrash):
            self.run_gate(fault_injector=crash_before_publish)

        self.assertFalse((self.migration / "runs-backup-transaction.json").exists())
        self.assertTrue((self.migration / "runs-backup-transaction.next").is_file())
        self.assertFalse((self.migration / "runs.pre-v2.json").exists())
        self.assertFalse((self.migration / "runs-backup-receipt.pending").exists())
        receipt = self.run_gate()
        self.assertEqual(receipt["source"], receipt["backup"])
        self.assertFalse((self.migration / "runs-backup-transaction.next").exists())

    def test_new_transaction_marker_is_immutable_prepared_provenance(self) -> None:
        self.paths.root.mkdir(parents=True)
        self.paths.runs_path.write_bytes(b'{"runs":[]}\n')
        observed: list[bytes] = []
        checkpoints = {
            "after_marker_prepared",
            "after_marker_backup_written",
            "after_marker_receipt_pending",
            "after_receipt_publish",
        }

        def capture_marker(phase: str) -> None:
            if phase in checkpoints:
                observed.append(
                    (self.migration / "runs-backup-transaction.json").read_bytes()
                )

        receipt = self.run_gate(fault_injector=capture_marker)

        self.assertEqual(receipt["source"], receipt["backup"])
        self.assertEqual(len(observed), len(checkpoints))
        self.assertTrue(all(value == observed[0] for value in observed))
        marker = json.loads(observed[0])
        self.assertEqual(marker["revision"], 1)
        self.assertIsNone(marker["previous_sha256"])
        self.assertEqual(marker["phase"], "prepared")

    def test_recovery_never_deletes_legacy_or_drifted_artifacts(self) -> None:
        self.paths.root.mkdir(parents=True)
        self.paths.runs_path.write_bytes(b"source-v1")
        self.migration.mkdir()
        backup = self.migration / "runs.pre-v2.json"
        backup.write_bytes(b"legacy-or-external")

        with self.assertRaises(RunsBackupGateError):
            self.run_gate()

        self.assertEqual(backup.read_bytes(), b"legacy-or-external")


class FakeProcess:
    def __init__(
        self,
        pid: int,
        name: str,
        executable: object,
        argv: object,
    ) -> None:
        self.pid = pid
        self.info = {"pid": pid, "name": name}
        self._executable = executable
        self._argv = argv

    def oneshot(self) -> object:
        return nullcontext()

    def exe(self) -> str:
        if isinstance(self._executable, BaseException):
            raise self._executable
        return self._executable  # type: ignore[return-value]

    def cmdline(self) -> list[str]:
        if isinstance(self._argv, BaseException):
            raise self._argv
        return self._argv  # type: ignore[return-value]


class FakeNoSuchProcess(Exception):
    pass


class FakeAccessDenied(Exception):
    pass


class FakePsutil:
    NoSuchProcess = FakeNoSuchProcess
    AccessDenied = FakeAccessDenied

    def __init__(self, processes: list[FakeProcess]) -> None:
        self.processes = processes

    def process_iter(self, attrs: list[str], ad_value: object = None) -> list[FakeProcess]:
        if attrs != ["pid", "name"] or ad_value is not None:
            raise AssertionError("unexpected process_iter contract")
        return list(self.processes)


class FakeGuard:
    def __init__(self, snapshots: dict[int, dict[str, object]]) -> None:
        self.snapshots = snapshots

    def snapshot(self, pid: int) -> dict[str, object]:
        return dict(self.snapshots[pid])


def identity(pid: int) -> dict[str, object]:
    return {
        "pid": pid,
        "creation_time_utc": "2026-07-22T00:00:00.000000Z",
        "executable_sha256": "a" * 64,
        "command_line_sha256": "b" * 64,
        "identity_scheme": "psutil-argv-v2",
        "identity_complete": True,
        "exit_code": 0,
    }


class RunsProcessScanTest(unittest.TestCase):
    def test_launch_ancestor_capture_rejects_newer_reused_or_reparented_pid(self) -> None:
        parent_pid = 777
        argv = ["python", "-m", "dayz_mcp", "--daemon"]
        child_created = datetime(2026, 7, 22, 7, 0, tzinfo=UTC)
        parent_created = child_created.timestamp() - 1.0
        hashes = identity_hashes(str(Path(sys.executable).resolve()), argv)
        current = identity(os.getpid())
        current.update(
            {
                "creation_time_utc": child_created.isoformat(
                    timespec="microseconds"
                ).replace("+00:00", "Z"),
                "command_line_sha256": hashes["command_line_sha256"],
            }
        )

        class CurrentProcess:
            def __init__(self, pids: list[int]) -> None:
                self.pids = pids

            def ppid(self) -> int:
                return self.pids.pop(0) if len(self.pids) > 1 else self.pids[0]

        class ParentProcess:
            pid = parent_pid

            def __init__(self, created: float) -> None:
                self.created = created

            def oneshot(self) -> object:
                return nullcontext()

            def create_time(self) -> float:
                return self.created

            def exe(self) -> str:
                return str(Path(sys.executable).resolve())

            def cmdline(self) -> list[str]:
                return list(argv)

        class ProcessModule:
            def __init__(self, created: float, pids: list[int]) -> None:
                self.current = CurrentProcess(pids)
                self.parent = ParentProcess(created)

            def Process(self, pid: int) -> object:
                return self.current if pid == os.getpid() else self.parent

        parent_identity = identity(parent_pid)
        parent_identity.update(
            {
                "creation_time_utc": datetime.fromtimestamp(
                    parent_created, UTC
                ).isoformat(timespec="microseconds").replace("+00:00", "Z"),
                **hashes,
            }
        )
        approved = Path(sys.executable).resolve()

        accepted = capture_launch_ancestor_identity(
            current,
            approved,
            psutil_module=ProcessModule(parent_created, [parent_pid, parent_pid]),
            guard=FakeGuard({parent_pid: parent_identity}),
        )
        self.assertEqual(accepted, parent_identity)

        newer = capture_launch_ancestor_identity(
            current,
            approved,
            psutil_module=ProcessModule(
                child_created.timestamp() + 3600.0,
                [parent_pid, parent_pid],
            ),
            guard=FakeGuard({parent_pid: parent_identity}),
        )
        self.assertIsNone(newer)

        drifted = dict(parent_identity)
        drifted["creation_time_utc"] = "2026-07-21T06:59:00.000000Z"
        reused = capture_launch_ancestor_identity(
            current,
            approved,
            psutil_module=ProcessModule(parent_created, [parent_pid, parent_pid]),
            guard=FakeGuard({parent_pid: drifted}),
        )
        self.assertIsNone(reused)

        reparented = capture_launch_ancestor_identity(
            current,
            approved,
            psutil_module=ProcessModule(parent_created, [parent_pid, parent_pid + 1]),
            guard=FakeGuard({parent_pid: parent_identity}),
        )
        self.assertIsNone(reparented)

    def test_native_image_name_fallback_classifies_protected_non_python_process(self) -> None:
        protected = FakeProcess(21, "", "", FakeAccessDenied())

        self.assertEqual(
            scan_dayz_mcp_processes(
                psutil_module=FakePsutil([protected]),
                image_name_fn=lambda pid: "Secure System" if pid == 21 else None,
            ),
            (),
        )

    def test_exact_module_and_bootstrap_are_found_but_foreign_python_is_ignored(self) -> None:
        processes = [
            FakeProcess(10, "python.exe", r"C:\Python\python.exe", ["python", "-c", "pass"]),
            FakeProcess(11, "python.exe", r"C:\Python\python.exe", ["python", "-m", "dayz_mcp", "--daemon"]),
            FakeProcess(
                12,
                "python.exe",
                r"C:\Python\python.exe",
                ["python", "-I", "-B", r"P:\DayZ_MCP_dev\tools\p0s_daemon_bootstrap.py", "daemon"],
            ),
            FakeProcess(13, "cmd.exe", r"C:\Windows\cmd.exe", ["cmd", "dayz_mcp"]),
            FakeProcess(
                14,
                "python.exe",
                r"C:\Python\python.exe",
                [
                    "python",
                    "-m",
                    "dayz_mcp",
                    "--client",
                    "--keyfile",
                    r"C:\keys\shared.key",
                    "--port",
                    "8765",
                    "--client-platform",
                    "codex",
                ],
            ),
        ]

        found = scan_dayz_mcp_processes(psutil_module=FakePsutil(processes))

        self.assertEqual(found, (11, 12))

    def test_only_exact_unambiguous_client_mode_is_not_a_blocker(self) -> None:
        variants = {
            "client": (["python", "-m", "dayz_mcp", "--client", "--keyfile", "K"], ()),
            "daemon": (["python", "-m", "dayz_mcp", "--daemon", "--keyfile", "K"], (41,)),
            "embedded": (["python", "-m", "dayz_mcp", "--embedded", "--keyfile", "K"], (41,)),
            "default": (["python", "-m", "dayz_mcp", "--keyfile", "K"], (41,)),
            "ambiguous": (["python", "-m", "dayz_mcp", "--client", "--daemon", "--keyfile", "K"], (41,)),
            "unknown": (["python", "-m", "dayz_mcp", "--client", "--evil"], (41,)),
            "client_as_value": (["python", "-m", "dayz_mcp", "--task-label", "--client"], (41,)),
            "client_substring": (["python", "-m", "dayz_mcp", "--client=1"], (41,)),
            "direct_embedded": (["python", r"C:\tools\dayz_mcp\__main__.py", "--embedded", "--keyfile", "K"], (41,)),
            "direct_default": (["python", r"dayz_mcp\__main__.py", "--keyfile", "K"], (41,)),
            "direct_client": (["python", r"dayz_mcp\__main__.py", "--client", "--keyfile", "K"], (41,)),
            "module_main_embedded": (["python", "-m", "dayz_mcp.__main__", "--embedded", "--keyfile", "K"], (41,)),
            "module_main_client": (
                ["python", "-m", "dayz_mcp.__main__", "--client", "--keyfile", "K"],
                (41,),
            ),
        }
        for name, (argv, expected) in variants.items():
            with self.subTest(name=name):
                process = FakeProcess(
                    41, "python.exe", r"C:\Python\python.exe", argv
                )
                self.assertEqual(
                    scan_dayz_mcp_processes(psutil_module=FakePsutil([process])),
                    expected,
                )

    def test_cpython_target_parser_handles_compact_long_and_terminal_options(self) -> None:
        variants = {
            "compact_module_writer": (
                ["python", "-bbmdayz_mcp.__main__", "--daemon"],
                (43,),
            ),
            "compact_client": (
                ["python", "-Imdayz_mcp", "--client", "--keyfile", "K"],
                (),
            ),
            "compact_foreign_module": (["python", "-OOmhttp.server"], ()),
            "compact_code": (["python", "-Bcprint('dayz_mcp')"], ()),
            "separate_code": (["python", "-c", "print('dayz_mcp')"], ()),
            "compact_warning": (
                ["python", "-BWignore", r"C:\tools\lint.py"],
                (),
            ),
            "compact_xoption": (
                ["python", "-BXdev", r"C:\tools\lint.py"],
                (),
            ),
            "hash_mode_writer": (
                [
                    "python",
                    "--check-hash-based-pycs",
                    "default",
                    "-mdayz_mcp.__main__",
                    "--daemon",
                ],
                (43,),
            ),
            "invalid_hash_mode": (
                [
                    "python",
                    "--check-hash-based-pycs",
                    "sometimes",
                    "-mdayz_mcp",
                ],
                (),
            ),
            "terminal_help": (["python", "--help-all", "-mdayz_mcp"], ()),
            "terminal_version": (["python", "--version", "-mdayz_mcp"], ()),
            "unknown_python_option": (["python", "--unknown", "-mdayz_mcp"], ()),
        }
        for name, (argv, expected) in variants.items():
            with self.subTest(name=name):
                process = FakeProcess(
                    43, "python.exe", r"C:\Python\python.exe", argv
                )
                self.assertEqual(
                    scan_dayz_mcp_processes(psutil_module=FakePsutil([process])),
                    expected,
                )

    def test_client_classifier_accepts_server_equals_negative_and_duplicate_forms(self) -> None:
        from dayz_mcp.server import parse_args
        from dayz_mcp.server_cli import parse_server_tail_silent

        cases = (
            (
                ["--client", "--keyfile=K", "--port=-1", "--idle-timeout=-0.5"],
                ["python", "-m", "dayz_mcp"],
            ),
            (
                [
                    "--client",
                    "--client",
                    "--keyfile",
                    "A",
                    "--keyfile",
                    "B",
                    "--port",
                    "-1",
                    "--idle-timeout",
                    "-.5",
                ],
                ["python", "-m", "dayz_mcp"],
            ),
            (
                [
                    "--client",
                    "--keyfile=K",
                    "--port=-2",
                    "--client-platform=codex",
                    "--task-label=-night",
                    "--no-daemon-autospawn",
                    "--no-daemon-autospawn",
                ],
                ["python", "-Imdayz_mcp"],
            ),
        )
        for tail, entrypoint in cases:
            with self.subTest(tail=tail, entrypoint=entrypoint):
                self.assertEqual(parse_args(list(tail)).mode, "client")
                process = FakeProcess(
                    44,
                    "python.exe",
                    r"C:\Python\python.exe",
                    [*entrypoint, *tail],
                )
                self.assertEqual(
                    scan_dayz_mcp_processes(psutil_module=FakePsutil([process])),
                    (),
                )

        for tail, entrypoint in (
            (
                ["--client", "--keyf", "K"],
                ["python", "-m", "dayz_mcp"],
            ),
            (
                ["--client", "--keyfile=K", "--por=-2"],
                ["python", "-m", "dayz_mcp.__main__"],
            ),
            (
                ["--client", "--keyfile=K", "--client-p=codex"],
                ["python", r"C:\tools\dayz_mcp\__main__.py"],
            ),
            (
                ["--client", "--keyfile", "K", "--task-label", "-night"],
                ["python", "-m", "dayz_mcp"],
            ),
            (
                ["--client", "--keyfile", "K", "--task-label", "--daemon"],
                ["python", "-m", "dayz_mcp"],
            ),
        ):
            with self.subTest(invalid_tail=tail, entrypoint=entrypoint):
                self.assertEqual(parse_server_tail_silent(list(tail)).status, "invalid")
                with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                    parse_args(list(tail))
                process = FakeProcess(
                    45,
                    "python.exe",
                    r"C:\Python\python.exe",
                    [*entrypoint, *tail],
                )
                self.assertEqual(
                    scan_dayz_mcp_processes(psutil_module=FakePsutil([process])),
                    (45,),
                )

    def test_entrypoint_text_used_only_as_data_is_not_a_blocker(self) -> None:
        variants = (
            ["python", "-c", "print('dayz_mcp/__main__.py')"],
            ["python", "-m", "pytest", r"dayz_mcp\__main__.py"],
            ["python", r"C:\tools\lint.py", r"dayz_mcp\__main__.py"],
        )
        for argv in variants:
            with self.subTest(argv=argv):
                process = FakeProcess(
                    42, "python.exe", r"C:\Python\python.exe", argv
                )
                self.assertEqual(
                    scan_dayz_mcp_processes(psutil_module=FakePsutil([process])),
                    (),
                )


class DaemonStartupElectionTest(unittest.TestCase):
    def test_lock_is_global_for_runtime_root_and_residual_file_is_not_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "DayZ_MCP"
            paths = RuntimePaths(
                root,
                root / "audit",
                root / "coordination.json",
                root / "runs.json",
            )
            with daemon_startup_election(paths) as first:
                self.assertTrue(first)
                with daemon_startup_election(paths) as second:
                    self.assertFalse(second)
            lock_path = root / ".daemon-startup.lock"
            self.assertTrue(lock_path.is_file())
            with daemon_startup_election(paths) as third:
                self.assertTrue(third)

    def test_partial_python_identity_fails_closed(self) -> None:
        process = FakeProcess(20, "python.exe", FakeAccessDenied(), ["python", "-m", "dayz_mcp"])

        with self.assertRaises(RunsBackupGateError):
            scan_dayz_mcp_processes(psutil_module=FakePsutil([process]))

        unnamed = FakeProcess(21, "", r"C:\Python\python.exe", ["python", "-c", "pass"])
        with self.assertRaises(RunsBackupGateError):
            scan_dayz_mcp_processes(psutil_module=FakePsutil([unnamed]))

    def test_only_exact_current_identity_is_allowed(self) -> None:
        process = FakeProcess(
            30,
            "python.exe",
            r"C:\Python\python.exe",
            ["python", "-m", "dayz_mcp", "--daemon"],
        )
        expected = identity(30)
        guard = FakeGuard({30: expected})

        self.assertEqual(
            scan_dayz_mcp_processes(
                allowed_current_identity=expected,
                psutil_module=FakePsutil([process]),
                guard=guard,
            ),
            (),
        )

        drift = dict(expected)
        drift["command_line_sha256"] = "c" * 64
        with self.assertRaises(RunsBackupGateError):
            scan_dayz_mcp_processes(
                allowed_current_identity=drift,
                psutil_module=FakePsutil([process]),
                guard=guard,
            )

    def test_exact_launch_redirector_ancestor_is_allowed_but_drift_is_not(self) -> None:
        current_process = FakeProcess(
            30,
            "python.exe",
            r"C:\Python\python.exe",
            ["python", "-m", "dayz_mcp", "--daemon"],
        )
        redirector_process = FakeProcess(
            31,
            "python.exe",
            r"C:\venv\Scripts\python.exe",
            ["python", "-m", "dayz_mcp", "--daemon"],
        )
        current = identity(30)
        redirector = identity(31)
        guard = FakeGuard({30: current, 31: redirector})

        self.assertEqual(
            scan_dayz_mcp_processes(
                current,
                redirector,
                psutil_module=FakePsutil([current_process, redirector_process]),
                guard=guard,
            ),
            (),
        )

        drifted = dict(redirector)
        drifted["creation_time_utc"] = "2026-07-22T01:00:00.000000Z"
        with self.assertRaisesRegex(
            RunsBackupGateError, "allowed_process_identity_drift"
        ):
            scan_dayz_mcp_processes(
                current,
                drifted,
                psutil_module=FakePsutil([current_process, redirector_process]),
                guard=guard,
            )


if __name__ == "__main__":
    unittest.main()
