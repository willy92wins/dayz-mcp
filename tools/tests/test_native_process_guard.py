from __future__ import annotations

import hashlib
import json
import ntpath
import sys
import unittest
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dayz_mcp import native_process_guard
from dayz_mcp.native_process_guard import NativeProcessGuard, identity_hashes


class FakeNoSuchProcess(Exception):
    pass


class FakeAccessDenied(Exception):
    pass


class FakeTimeoutExpired(Exception):
    pass


class FakeProcess:
    def __init__(
        self,
        pid: int,
        *,
        create_time: object = 1_700_000_000.125,
        exe: object = r"C:\Python314\python.exe",
        cmdline: object = None,
        name: object = "python.exe",
        children: object = None,
    ) -> None:
        self.pid = pid
        self._create_time = create_time
        self._exe = exe
        self._cmdline = [r"C:\Python314\python.exe", "-I", "-B"] if cmdline is None else cmdline
        self._name = name
        self._children = [] if children is None else children
        self.kill_calls = 0
        self.wait_calls: list[float] = []
        self.kill_error: BaseException | None = None
        self.wait_error: BaseException | None = None

    @staticmethod
    def _read(value: object) -> object:
        if isinstance(value, BaseException):
            raise value
        return value

    def oneshot(self) -> nullcontext[None]:
        return nullcontext()

    def create_time(self) -> object:
        return self._read(self._create_time)

    def exe(self) -> object:
        return self._read(self._exe)

    def cmdline(self) -> object:
        return self._read(self._cmdline)

    def name(self) -> object:
        return self._read(self._name)

    def children(self, *, recursive: bool = False) -> object:
        if recursive:
            raise AssertionError("discover must implement explicit breadth-first traversal")
        return self._read(self._children)

    def kill(self) -> None:
        self.kill_calls += 1
        if self.kill_error is not None:
            raise self.kill_error

    def wait(self, *, timeout: float) -> int:
        self.wait_calls.append(timeout)
        if self.wait_error is not None:
            raise self.wait_error
        return 0


class FakePsutil:
    NoSuchProcess = FakeNoSuchProcess
    AccessDenied = FakeAccessDenied
    TimeoutExpired = FakeTimeoutExpired

    def __init__(self, processes: dict[int, FakeProcess | BaseException]) -> None:
        self.processes = processes
        self.process_calls: list[int] = []

    def Process(self, pid: int) -> FakeProcess:
        self.process_calls.append(pid)
        value = self.processes.get(pid, FakeNoSuchProcess())
        if isinstance(value, BaseException):
            raise value
        return value


@dataclass(frozen=True)
class ExpectedRecord:
    pid: int
    creation_time_utc: str
    executable_sha256: str
    command_line_sha256: str
    role: str = "daemon"
    identity_scheme: str = "psutil-argv-v2"


def expected_identity(process: FakeProcess) -> ExpectedRecord:
    created = datetime.fromtimestamp(float(process._create_time), UTC)
    created_text = created.isoformat(timespec="microseconds").replace("+00:00", "Z")
    normalized_exe = ntpath.normpath(str(process._exe)).casefold()
    executable_hash = hashlib.sha256(
        b"psutil-exe-v2\0" + normalized_exe.encode("utf-8")
    ).hexdigest()
    argv_json = json.dumps(
        process._cmdline,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    command_hash = hashlib.sha256(b"psutil-argv-v2\0" + argv_json).hexdigest()
    return ExpectedRecord(
        pid=process.pid,
        creation_time_utc=created_text,
        executable_sha256=executable_hash,
        command_line_sha256=command_hash,
    )


class NativeProcessGuardSnapshotTest(unittest.TestCase):
    def test_identity_hashes_match_snapshot_canonicalization(self) -> None:
        executable = r"C:\Python314\..\Python314\PYTHON.exe"
        argv = [r"C:\Python314\python.exe", "-m", "dayz_mcp", "--daemon"]

        hashes = identity_hashes(executable, argv)

        self.assertEqual(
            hashes["executable_sha256"],
            hashlib.sha256(
                b"psutil-exe-v2\0"
                + ntpath.normpath(executable).casefold().encode("utf-8")
            ).hexdigest(),
        )
        self.assertEqual(
            hashes["command_line_sha256"],
            hashlib.sha256(
                b"psutil-argv-v2\0"
                + json.dumps(
                    argv, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest(),
        )

    def _guard(self, fake_psutil: FakePsutil) -> NativeProcessGuard:
        patcher = patch.object(native_process_guard, "psutil", fake_psutil)
        self.addCleanup(patcher.stop)
        patcher.start()
        return NativeProcessGuard()

    def test_snapshot_returns_exact_psutil_argv_v2_identity(self) -> None:
        process = FakeProcess(
            321,
            create_time=1_700_000_000.125,
            exe=r"C:\Tools\..\Python314\PYTHON.exe",
            cmdline=[r"C:\Python314\python.exe", "-m", "dayz_mcp", "雪"],
        )
        guard = self._guard(FakePsutil({321: process}))

        result = guard.snapshot(321)
        expected = expected_identity(process)

        self.assertEqual(result["exit_code"], 0)
        self.assertIs(result["identity_complete"], True)
        self.assertEqual(result["identity_scheme"], "psutil-argv-v2")
        self.assertEqual(result["pid"], expected.pid)
        self.assertEqual(result["creation_time_utc"], expected.creation_time_utc)
        self.assertEqual(result["executable_sha256"], expected.executable_sha256)
        self.assertEqual(result["command_line_sha256"], expected.command_line_sha256)

    def test_snapshot_maps_missing_process_to_exit_4(self) -> None:
        guard = self._guard(FakePsutil({321: FakeNoSuchProcess()}))

        result = guard.snapshot(321)

        self.assertEqual(result["error"], "process_not_found")
        self.assertEqual(result["exit_code"], 4)
        self.assertIs(result["identity_complete"], False)

    def test_snapshot_maps_access_denied_to_fail_closed_exit_3(self) -> None:
        process = FakeProcess(321, exe=FakeAccessDenied())
        guard = self._guard(FakePsutil({321: process}))

        result = guard.snapshot(321)

        self.assertEqual(result["error"], "identity_unavailable")
        self.assertEqual(result["exit_code"], 3)
        self.assertIs(result["identity_complete"], False)

    def test_snapshot_rejects_partial_or_inconsistent_identity(self) -> None:
        invalid_processes = (
            FakeProcess(321, exe=""),
            FakeProcess(321, exe=r"relative\python.exe"),
            FakeProcess(321, cmdline=[]),
            FakeProcess(321, cmdline=[r"C:\python.exe", 7]),
            FakeProcess(999),
        )

        for process in invalid_processes:
            with self.subTest(process=process.__dict__):
                guard = self._guard(FakePsutil({321: process}))
                result = guard.snapshot(321)
                self.assertEqual(result["error"], "identity_unavailable")
                self.assertEqual(result["exit_code"], 3)
                self.assertIs(result["identity_complete"], False)


class NativeProcessGuardTerminateTest(unittest.TestCase):
    def _guard(self, fake_psutil: FakePsutil) -> NativeProcessGuard:
        patcher = patch.object(native_process_guard, "psutil", fake_psutil)
        self.addCleanup(patcher.stop)
        patcher.start()
        return NativeProcessGuard()

    def test_exact_identity_kills_and_waits_on_the_same_process_object(self) -> None:
        process = FakeProcess(321)
        fake_psutil = FakePsutil({321: process})
        guard = self._guard(fake_psutil)

        result = guard.terminate(expected_identity(process))

        self.assertEqual(fake_psutil.process_calls, [321])
        self.assertEqual(process.kill_calls, 1)
        self.assertEqual(process.wait_calls, [5.0])
        self.assertIs(result["terminated"], True)
        self.assertEqual(result["exit_code"], 0)

    def test_invalid_scheme_or_role_fails_before_process_lookup(self) -> None:
        process = FakeProcess(321)
        fake_psutil = FakePsutil({321: process})
        guard = self._guard(fake_psutil)
        expected = expected_identity(process)

        for invalid in (
            replace(expected, identity_scheme="legacy-wmi-v1"),
            replace(expected, identity_scheme="unknown"),
            replace(expected, role=""),
        ):
            with self.subTest(invalid=invalid):
                result = guard.terminate(invalid)
                self.assertIs(result["terminated"], False)
                self.assertEqual(result["error"], "invalid_expected_identity")

        self.assertEqual(fake_psutil.process_calls, [])
        self.assertEqual(process.kill_calls, 0)

    def test_each_identity_field_drift_preserves_process(self) -> None:
        process = FakeProcess(321)
        expected = expected_identity(process)
        mutations = (
            replace(expected, pid=322),
            replace(expected, creation_time_utc="2020-01-01T00:00:00.000000Z"),
            replace(expected, executable_sha256="0" * 64),
            replace(expected, command_line_sha256="f" * 64),
        )

        for mutation in mutations:
            with self.subTest(mutation=mutation):
                fake_psutil = FakePsutil({mutation.pid: process})
                guard = self._guard(fake_psutil)
                result = guard.terminate(mutation)
                self.assertIs(result["terminated"], False)
                expected_error = (
                    "identity_unavailable"
                    if mutation.pid != process.pid
                    else "process_identity_mismatch"
                )
                self.assertEqual(result["error"], expected_error)
                self.assertEqual(process.kill_calls, 0)

    def test_wait_timeout_never_confirms_termination(self) -> None:
        process = FakeProcess(321)
        process.wait_error = FakeTimeoutExpired()
        guard = self._guard(FakePsutil({321: process}))

        result = guard.terminate(expected_identity(process))

        self.assertEqual(process.kill_calls, 1)
        self.assertIs(result["terminated"], False)
        self.assertEqual(result["error"], "termination_unconfirmed")
        self.assertEqual(result["exit_code"], 3)

    def test_uppercase_hash_is_well_formed_but_cannot_match_exact_v2_identity(self) -> None:
        process = FakeProcess(321)
        expected = expected_identity(process)
        uppercase = replace(
            expected,
            executable_sha256=expected.executable_sha256.upper(),
        )
        guard = self._guard(FakePsutil({321: process}))

        result = guard.terminate(uppercase)

        self.assertEqual(result["error"], "process_identity_mismatch")
        self.assertEqual(process.kill_calls, 0)


class NativeProcessGuardDiscoverTest(unittest.TestCase):
    def _guard(self, fake_psutil: FakePsutil) -> NativeProcessGuard:
        patcher = patch.object(native_process_guard, "psutil", fake_psutil)
        self.addCleanup(patcher.stop)
        patcher.start()
        return NativeProcessGuard()

    def test_discover_is_breadth_first_and_ignores_non_allowlisted_branches(self) -> None:
        grandchild = FakeProcess(4, name="dayzdiag_x64.exe")
        allowed_a = FakeProcess(2, name="DAYZDIAG_X64.EXE", children=[grandchild])
        ignored_child = FakeProcess(5, name="python.exe")
        denied_branch = FakeProcess(3, name="foreign.exe", children=[ignored_child])
        root = FakeProcess(1, children=[allowed_a, denied_branch])
        guard = self._guard(FakePsutil({1: root}))

        result = guard.discover(1, {"DayZDiag_x64.exe"})

        self.assertEqual(result["exit_code"], 0)
        self.assertIs(result["identity_complete"], True)
        self.assertEqual(
            [item["pid"] for item in result["processes"]],
            [1, 2, 4],
        )

    def test_allowed_candidate_access_denied_fails_whole_discovery(self) -> None:
        child = FakeProcess(2, name="dayzdiag_x64.exe", exe=FakeAccessDenied())
        root = FakeProcess(1, children=[child])
        guard = self._guard(FakePsutil({1: root}))

        result = guard.discover(1, {"dayzdiag_x64.exe"})

        self.assertEqual(result["error"], "identity_unavailable")
        self.assertEqual(result["exit_code"], 3)
        self.assertIs(result["identity_complete"], False)

    def test_disappearing_allowed_candidate_is_omitted(self) -> None:
        child = FakeProcess(2, name="dayzdiag_x64.exe", exe=FakeNoSuchProcess())
        root = FakeProcess(1, children=[child])
        guard = self._guard(FakePsutil({1: root}))

        result = guard.discover(1, {"dayzdiag_x64.exe"})

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual([item["pid"] for item in result["processes"]], [1])

    def test_candidate_disappearing_during_name_lookup_is_omitted(self) -> None:
        child = FakeProcess(2, name=FakeNoSuchProcess())
        root = FakeProcess(1, children=[child])
        guard = self._guard(FakePsutil({1: root}))

        result = guard.discover(1, {"dayzdiag_x64.exe"})

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual([item["pid"] for item in result["processes"]], [1])

    def test_root_pid_inconsistency_fails_closed(self) -> None:
        inconsistent_root = FakeProcess(999)
        guard = self._guard(FakePsutil({1: inconsistent_root}))

        result = guard.discover(1, {"dayzdiag_x64.exe"})

        self.assertEqual(result["error"], "identity_unavailable")
        self.assertEqual(result["exit_code"], 3)
        self.assertEqual(result["processes"], [])

    def test_product_callers_have_no_powershell_guard_path(self) -> None:
        for relative in (
            "dayz_mcp/process_lifecycle.py",
            "dayz_mcp/daemon.py",
            "dayz_mcp/doctor.py",
            "dayz_mcp/orphan_guard.py",
        ):
            source = (TOOLS_DIR / relative).read_text(encoding="utf-8")
            with self.subTest(relative=relative):
                for token in (
                    "PowerShellProcessGuard",
                    "process-guard.ps1",
                    "-ExecutionPolicy",
                    "command_line_of",
                    "kill_pid",
                    "wmic",
                    "TerminateProcess",
                ):
                    self.assertFalse(token in source, f"legacy guard token in {relative}")


if __name__ == "__main__":
    unittest.main()
