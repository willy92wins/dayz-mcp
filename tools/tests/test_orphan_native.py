from __future__ import annotations

import time
import unittest
from contextlib import nullcontext
from dataclasses import asdict
from unittest.mock import patch

from dayz_mcp import loopback, native_process_snapshot, orphan_guard
from dayz_mcp.native_process_guard import identity_hashes


class FakeAccessDenied(Exception):
    pass


class FakeNoSuchProcess(Exception):
    pass


class FakeProcess:
    def __init__(self, pid: int, argv: object) -> None:
        self.pid = pid
        self.argv = argv

    def oneshot(self) -> object:
        return nullcontext()

    def cmdline(self) -> list[str]:
        if isinstance(self.argv, BaseException):
            raise self.argv
        return self.argv  # type: ignore[return-value]


class FakePsutil:
    AccessDenied = FakeAccessDenied
    NoSuchProcess = FakeNoSuchProcess

    def __init__(self, process: FakeProcess) -> None:
        self.process = process

    def Process(self, pid: int) -> FakeProcess:
        if pid != self.process.pid:
            raise FakeNoSuchProcess()
        return self.process


class NativeArgvTest(unittest.TestCase):
    def test_command_argv_returns_structured_copy_and_fails_closed(self) -> None:
        expected = [r"C:\Python\python.exe", "-m", "dayz_mcp", "--daemon"]
        with patch.object(
            native_process_snapshot,
            "psutil",
            FakePsutil(FakeProcess(42, expected)),
        ):
            observed = orphan_guard.command_argv_of(42)

        self.assertEqual(observed, expected)
        self.assertIsNot(observed, expected)

        for invalid in ([], ["python", ""], FakeAccessDenied()):
            with (
                self.subTest(invalid=type(invalid).__name__),
                patch.object(
                    native_process_snapshot,
                    "psutil",
                    FakePsutil(FakeProcess(42, invalid)),
                ),
            ):
                self.assertIsNone(orphan_guard.command_argv_of(42))

    def test_exact_argv_classifies_normal_legacy_bootstrap_and_foreign(self) -> None:
        cases = (
            (["python", "-m", "dayz_mcp", "--daemon", "--port", "8765"], "normal_daemon"),
            (["python", "-m", "dayz_mcp", "--embedded", "--port", "8765"], "legacy_embedded"),
            (
                [
                    "python",
                    "-I",
                    "-B",
                    r"P:\DayZ_MCP_dev\tools\p0s_daemon_bootstrap.py",
                    "--security-manifest",
                    r"P:\manifest.json",
                    "daemon",
                ],
                "p0s_bootstrap_daemon",
            ),
            (["python", "-m", "dayz_mcp", "--client"], "foreign"),
            (["python", "-m", "dayz_mcp", "-m", "attacker", "--daemon"], "foreign"),
            (["python", "-m=dayz_mcp", "--daemon"], "foreign"),
            (["python", "-m", "dayz_mcp_extra", "--daemon"], "foreign"),
        )
        for argv, expected in cases:
            with self.subTest(argv=argv):
                self.assertEqual(orphan_guard.classify_dayz_argv(argv), expected)

    def test_port_match_requires_one_exact_flag_value_pair(self) -> None:
        self.assertTrue(
            orphan_guard.argv_targets_port(
                ["python", "-m", "dayz_mcp", "--port", "8765"], 8765
            )
        )
        for argv in (
            ["python", "-m", "dayz_mcp", "--port=8765"],
            ["python", "-m", "dayz_mcp", "--port", "8765", "--port", "8765"],
            ["python", "-m", "dayz_mcp", "--port", "87650"],
        ):
            with self.subTest(argv=argv):
                self.assertFalse(orphan_guard.argv_targets_port(argv, 8765))


def process_snapshot(
    pid: int,
    executable: str,
    argv: list[str],
    *,
    creation: str = "2026-07-22T00:00:00.000000Z",
) -> dict[str, object]:
    return {
        "pid": pid,
        "creation_time_utc": creation,
        **identity_hashes(executable, argv),
        "identity_scheme": "psutil-argv-v2",
        "identity_complete": True,
        "exit_code": 0,
    }


class FakeGuard:
    def __init__(self, snapshots: list[dict[str, object]]) -> None:
        self.snapshots = list(snapshots)
        self.terminate_calls: list[object] = []
        self.terminate_result: dict[str, object] = {"terminated": True}

    def snapshot(self, _pid: int) -> dict[str, object]:
        return dict(self.snapshots.pop(0))

    def terminate(self, expected: object) -> dict[str, object]:
        self.terminate_calls.append(expected)
        return dict(self.terminate_result)


class NativeDaemonReclaimTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pid = 4321
        self.executable = r"C:\Python\python.exe"
        self.argv = [
            self.executable,
            "-m",
            "dayz_mcp",
            "--daemon",
            "--port",
            "8765",
            "--keyfile",
            r"C:\DayZ MCP\shared.key",
            "--idle-timeout",
            "1800",
        ]
        self.snapshot = process_snapshot(self.pid, self.executable, self.argv)

    def call(self, **overrides: object) -> tuple[bool, FakeGuard, list[float]]:
        guard = overrides.pop(
            "guard", FakeGuard([self.snapshot, self.snapshot])
        )
        sleeps: list[float] = []
        values = {
            "is_healthy": lambda: False,
            "is_responsive": lambda: False,
            "sleep": sleeps.append,
            "find_listener": lambda _port: self.pid,
            "get_image": lambda _pid: "python.exe",
            "get_argv": lambda _pid: list(self.argv),
            "guard": guard,
            "wait_free": lambda _port: True,
            "expected_executable": self.executable,
            "expected_argv": list(self.argv),
            "deadline": time.monotonic() + 5.0,
        }
        values.update(overrides)
        result = orphan_guard.try_reclaim_unresponsive_listener(8765, **values)
        return result, guard, sleeps  # type: ignore[return-value]

    def test_exact_policy_two_stable_snapshots_terminate_complete_record(self) -> None:
        result, guard, sleeps = self.call()

        self.assertTrue(result)
        self.assertEqual(sleeps, [0.5])
        self.assertEqual(len(guard.terminate_calls), 1)
        expected = guard.terminate_calls[0]
        self.assertEqual(expected.role, "daemon")
        self.assertEqual(expected.identity_scheme, "psutil-argv-v2")
        for field in (
            "pid",
            "creation_time_utc",
            "executable_sha256",
            "command_line_sha256",
            "identity_scheme",
        ):
            self.assertEqual(asdict(expected)[field], self.snapshot[field])

    def test_second_responsive_probe_preserves_without_snapshot(self) -> None:
        responses = iter((False, True))
        guard = FakeGuard([])

        result, guard, sleeps = self.call(
            is_responsive=lambda: next(responses),
            guard=guard,
        )

        self.assertFalse(result)
        self.assertEqual(sleeps, [0.5])
        self.assertEqual(guard.terminate_calls, [])

    def test_pid_snapshot_policy_or_expected_drift_preserves_process(self) -> None:
        drifted = process_snapshot(
            self.pid,
            self.executable,
            self.argv,
            creation="2026-07-22T00:00:01.000000Z",
        )
        listener_pids = iter((self.pid, self.pid + 1))
        variants = (
            {"find_listener": lambda _port: next(listener_pids)},
            {"guard": FakeGuard([self.snapshot, drifted])},
            {"expected_argv": [*self.argv, "--require-version"]},
            {"expected_executable": None},
            {"get_argv": lambda _pid: [self.executable, "-m", "dayz_mcp", "--client"]},
        )
        for values in variants:
            with self.subTest(values=tuple(values)):
                result, guard, _sleeps = self.call(**values)
                self.assertFalse(result)
                self.assertEqual(guard.terminate_calls, [])


class NativeEmbeddedReclaimTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pid = 5001
        self.executable = r"C:\Python\python.exe"
        self.argv = [
            self.executable,
            "-m",
            "dayz_mcp",
            "--embedded",
            "--port",
            "8765",
        ]
        self.snapshot = process_snapshot(self.pid, self.executable, self.argv)

    def call(self, **overrides: object) -> tuple[bool, FakeGuard]:
        guard = overrides.pop("guard", FakeGuard([self.snapshot, self.snapshot]))
        values = {
            "find_listener": lambda _port: self.pid,
            "get_image": lambda _pid: "python.exe",
            "get_argv": lambda _pid: list(self.argv),
            "get_parent": lambda _pid: 100,
            "get_full_path": lambda _pid: r"C:\Claude\claude.exe",
            "is_alive": lambda _pid: False,
            "guard": guard,
            "wait_free": lambda _port: True,
            "is_responsive": lambda: False,
            "sleep": lambda _seconds: None,
            "self_exe": self.executable,
            "expected_executable": self.executable,
            "expected_argv": list(self.argv),
        }
        values.update(overrides)
        result = orphan_guard.try_reclaim_port(8765, **values)
        return result, guard  # type: ignore[return-value]

    def test_dead_parent_exact_embedded_policy_uses_complete_v2_record(self) -> None:
        result, guard = self.call()

        self.assertTrue(result)
        self.assertEqual(len(guard.terminate_calls), 1)
        expected = guard.terminate_calls[0]
        self.assertEqual(expected.role, "embedded")
        self.assertEqual(expected.pid, self.pid)
        self.assertEqual(expected.identity_scheme, "psutil-argv-v2")

    def test_live_parent_or_daemon_topology_is_never_reclaimed(self) -> None:
        daemon_argv = [
            self.executable,
            "-m",
            "dayz_mcp",
            "--daemon",
            "--port",
            "8765",
        ]
        for values in (
            {"is_alive": lambda _pid: True},
            {
                "get_argv": lambda _pid: list(daemon_argv),
                "expected_argv": list(daemon_argv),
            },
        ):
            with self.subTest(values=tuple(values)):
                result, guard = self.call(**values)
                self.assertFalse(result)
                self.assertEqual(guard.terminate_calls, [])

    def test_loopback_supplies_current_structured_policy_to_reclaim(self) -> None:
        captured: dict[str, object] = {}
        sentinel = object()
        in_use = OSError("in use")
        in_use.errno = 10048

        def reclaim(_port: int, **kwargs: object) -> bool:
            captured.update(kwargs)
            return True

        with (
            patch.object(loopback, "ExclusiveThreadingHTTPServer", side_effect=[in_use, sentinel]),
            patch.object(loopback.orphan_guard, "try_reclaim_port", side_effect=reclaim),
            patch.object(loopback.sys, "orig_argv", list(self.argv), create=True),
            patch.object(loopback.sys, "executable", self.executable),
        ):
            server = loopback._bind_exclusive(8765, lambda _message: None, True)

        self.assertIs(server, sentinel)
        self.assertEqual(captured["expected_executable"], self.executable)
        self.assertEqual(captured["expected_argv"], self.argv)

    def test_pid_or_snapshot_drift_preserves_embedded_process(self) -> None:
        listener_pids = iter((self.pid, self.pid + 1))
        drifted = process_snapshot(
            self.pid,
            self.executable,
            self.argv,
            creation="2026-07-22T00:00:02.000000Z",
        )
        for values in (
            {"find_listener": lambda _port: next(listener_pids)},
            {"guard": FakeGuard([self.snapshot, drifted])},
        ):
            with self.subTest(values=tuple(values)):
                result, guard = self.call(**values)
                self.assertFalse(result)
                self.assertEqual(guard.terminate_calls, [])


if __name__ == "__main__":
    unittest.main()
