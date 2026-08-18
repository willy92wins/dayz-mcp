from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, native_process_snapshot, orphan_guard
from dayz_mcp.native_process_guard import identity_hashes


def _process_snapshot(
    pid: int,
    executable: str,
    argv: list[str],
    *,
    creation: str = "2026-07-28T00:00:00.000000Z",
) -> dict[str, object]:
    return {
        "pid": pid,
        "creation_time_utc": creation,
        **identity_hashes(executable, argv),
        "identity_scheme": "psutil-argv-v2",
        "identity_complete": True,
        "exit_code": 0,
    }


class _FakeGuard:
    def __init__(
        self,
        snapshots: list[dict[str, object]],
        *,
        terminate_ok: bool = True,
    ) -> None:
        self.snapshots = [dict(snapshot) for snapshot in snapshots]
        self.terminate_calls: list[object] = []
        self.terminate_ok = terminate_ok

    def snapshot(self, _pid: int) -> dict[str, object]:
        return dict(self.snapshots.pop(0))

    def terminate(self, expected: object) -> dict[str, object]:
        self.terminate_calls.append(expected)
        return {"terminated": self.terminate_ok}


class ArgvPortMatchTest(unittest.TestCase):
    def test_space_separated_port_matches(self) -> None:
        argv = ["py", "-m", "dayz_mcp", "--port", "8765"]
        self.assertTrue(orphan_guard.argv_targets_port(argv, 8765))

    def test_equals_separated_port_is_rejected(self) -> None:
        argv = ["py", "-m", "dayz_mcp", "--port=8765"]
        self.assertFalse(orphan_guard.argv_targets_port(argv, 8765))

    def test_other_port_does_not_match(self) -> None:
        argv = ["py", "-m", "dayz_mcp", "--port", "8765"]
        self.assertFalse(orphan_guard.argv_targets_port(argv, 9001))

    def test_prefix_collision_does_not_match(self) -> None:
        argv = ["py", "-m", "dayz_mcp", "--port", "87650"]
        self.assertFalse(orphan_guard.argv_targets_port(argv, 8765))

    def test_missing_port_does_not_match(self) -> None:
        argv = ["py", "-m", "dayz_mcp", "--keyfile", "k"]
        self.assertFalse(orphan_guard.argv_targets_port(argv, 8765))


class ArgvModuleClassificationTest(unittest.TestCase):
    def test_exact_module_token_matches(self) -> None:
        argv = ["py", "-m", "dayz_mcp", "--port", "8765"]
        self.assertEqual(orphan_guard.classify_dayz_argv(argv), "legacy_embedded")

    def test_module_equals_form_is_foreign(self) -> None:
        argv = ["py", "-m=dayz_mcp", "--port", "8765"]
        self.assertEqual(orphan_guard.classify_dayz_argv(argv), "foreign")

    def test_path_substring_does_not_match(self) -> None:
        argv = [
            r"C:\Users\example\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\.venv-mcp\Scripts\python.exe",
            "-m",
            "http.server",
            "--port",
            "8765",
        ]
        self.assertEqual(orphan_guard.classify_dayz_argv(argv), "foreign")

    def test_prefix_collision_does_not_match(self) -> None:
        argv = ["py", "-m", "dayz_mcp_extra", "--port", "8765"]
        self.assertEqual(orphan_guard.classify_dayz_argv(argv), "foreign")


def _probe(behaviour: object) -> object:
    """Injected liveness probe: a bool, a sequence of bools, or an exception."""
    if isinstance(behaviour, BaseException):
        def raising() -> bool:
            raise behaviour

        return raising
    if isinstance(behaviour, (list, tuple)):
        answers = list(behaviour)

        def sequenced() -> bool:
            return bool(answers.pop(0)) if answers else False

        return sequenced
    return lambda: bool(behaviour)


class ShouldReclaimDiscriminatorTest(unittest.TestCase):
    """The security-critical decision: kill only a confirmed embedded orphan."""

    PID = 4321
    EXECUTABLE = r"C:\venv\python.exe"
    OURS = [
        EXECUTABLE,
        "-m",
        "dayz_mcp",
        "--embedded",
        "--keyfile",
        "k",
        "--port",
        "8765",
        "--require-version",
    ]

    def _run(
        self,
        *,
        listener_image: str = "python.exe",
        observed_argv: list[str] | None = None,
        parent_alive: bool = False,
        port: int = 8765,
        responsive: object = False,
    ) -> tuple[bool, list[int]]:
        expected_argv = list(self.OURS)
        snapshot = _process_snapshot(self.PID, self.EXECUTABLE, expected_argv)
        guard = _FakeGuard([snapshot, snapshot])
        result = orphan_guard.try_reclaim_port(
            port,
            find_listener=lambda _port: self.PID,
            get_image=lambda _pid: listener_image,
            get_argv=lambda _pid: list(observed_argv or expected_argv),
            get_parent=lambda _pid: 999,
            get_full_path=lambda _pid: r"C:\Claude\node.exe",
            is_alive=lambda _pid: parent_alive,
            guard=guard,
            wait_free=lambda _port: True,
            is_responsive=_probe(responsive),
            sleep=lambda _seconds: None,
            self_exe=self.EXECUTABLE,
            expected_executable=self.EXECUTABLE,
            expected_argv=expected_argv,
        )
        killed = [int(getattr(record, "pid")) for record in guard.terminate_calls]
        return result, killed

    def test_our_orphan_with_dead_parent_is_reclaimed(self) -> None:
        result, killed = self._run()
        self.assertTrue(result)
        self.assertEqual(killed, [self.PID])

    def test_serving_orphan_with_dead_ancestor_is_preserved(self) -> None:
        # BUG-070: ancestry said orphan, but the holder was still answering. The old
        # code killed it and the bridge was left talking to a differently-keyed
        # holder. Identity and ancestry are unchanged here; only health differs.
        result, killed = self._run(responsive=True)
        self.assertFalse(result)
        self.assertEqual(killed, [])

    def test_holder_answering_only_on_the_second_probe_is_preserved(self) -> None:
        # One probe is not enough: a live server can miss a single short probe under
        # load. Re-probing is what keeps that from being a death sentence.
        result, killed = self._run(responsive=[False, True])
        self.assertFalse(result)
        self.assertEqual(killed, [])

    def test_unusable_probe_preserves_the_holder(self) -> None:
        # Fail-closed: if the probe itself cannot answer, that is not permission.
        result, killed = self._run(responsive=OSError("probe exploded"))
        self.assertFalse(result)
        self.assertEqual(killed, [])

    def test_silent_holder_with_dead_ancestor_is_still_reclaimed(self) -> None:
        # Negative control for the three above: health only ADDS a condition, so a
        # genuinely dead-and-silent orphan must still be reclaimed as before.
        result, killed = self._run(responsive=[False, False])
        self.assertTrue(result)
        self.assertEqual(killed, [self.PID])

    def test_our_instance_with_live_parent_is_not_reclaimed(self) -> None:
        result, killed = self._run(parent_alive=True)
        self.assertFalse(result)
        self.assertEqual(killed, [])

    def test_non_python_listener_is_not_reclaimed(self) -> None:
        result, killed = self._run(listener_image="node.exe")
        self.assertFalse(result)
        self.assertEqual(killed, [])

    def test_foreign_process_is_not_reclaimed(self) -> None:
        foreign = [self.EXECUTABLE, "-m", "http.server", "--port", "8765"]
        result, killed = self._run(observed_argv=foreign)
        self.assertFalse(result)
        self.assertEqual(killed, [])

    def test_same_module_different_port_is_not_reclaimed(self) -> None:
        result, killed = self._run(port=9999)
        self.assertFalse(result)
        self.assertEqual(killed, [])


class TryReclaimPortDecisionTest(unittest.TestCase):
    """Current native reclaim wiring with injected facts; no real process touched."""

    EXECUTABLE = r"C:\Python\python.exe"
    OURS = [
        EXECUTABLE,
        "-m",
        "dayz_mcp",
        "--embedded",
        "--keyfile",
        "k",
        "--port",
        "8765",
    ]

    def _run(
        self,
        *,
        listener_pid: int | None,
        image: str,
        argv: list[str] | None,
        parent_pid: int | None,
        parent_alive: bool,
        kill_ok: bool = True,
        free_ok: bool = True,
        responsive: object = False,
    ) -> tuple[bool, list[int]]:
        expected_argv = list(self.OURS)
        snapshot_pid = listener_pid if isinstance(listener_pid, int) else 4321
        snapshot = _process_snapshot(snapshot_pid, self.EXECUTABLE, expected_argv)
        guard = _FakeGuard([snapshot, snapshot], terminate_ok=kill_ok)
        result = orphan_guard.try_reclaim_port(
            8765,
            find_listener=lambda _port: listener_pid,
            get_image=lambda _pid: image,
            get_argv=lambda _pid: None if argv is None else list(argv),
            get_parent=lambda _pid: parent_pid,
            get_full_path=lambda _pid: r"C:\Claude\node.exe",
            is_alive=lambda _pid: parent_alive,
            guard=guard,
            wait_free=lambda _port: free_ok,
            is_responsive=_probe(responsive),
            sleep=lambda _seconds: None,
            self_exe=self.EXECUTABLE,
            expected_executable=self.EXECUTABLE,
            expected_argv=expected_argv,
        )
        killed = [int(getattr(record, "pid")) for record in guard.terminate_calls]
        return result, killed

    def test_orphan_is_killed_and_port_freed(self) -> None:
        result, killed = self._run(
            listener_pid=4321,
            image="python.exe",
            argv=self.OURS,
            parent_pid=999,
            parent_alive=False,
        )
        self.assertTrue(result)
        self.assertEqual(killed, [4321])

    def test_live_instance_is_not_killed(self) -> None:
        result, killed = self._run(
            listener_pid=4321,
            image="python.exe",
            argv=self.OURS,
            parent_pid=999,
            parent_alive=True,
        )
        self.assertFalse(result)
        self.assertEqual(killed, [])

    def test_unknown_parent_is_not_killed(self) -> None:
        result, killed = self._run(
            listener_pid=4321,
            image="python.exe",
            argv=self.OURS,
            parent_pid=None,
            parent_alive=False,
        )
        self.assertFalse(result)
        self.assertEqual(killed, [])

    def test_unavailable_argv_logs_identity_uncertain(self) -> None:
        logs: list[str] = []
        guard = _FakeGuard([])
        result = orphan_guard.try_reclaim_port(
            8765,
            log=logs.append,
            find_listener=lambda _port: 4321,
            get_image=lambda _pid: "python.exe",
            get_argv=lambda _pid: None,
            get_parent=lambda _pid: 999,
            get_full_path=lambda _pid: r"C:\Claude\node.exe",
            is_alive=lambda _pid: False,
            guard=guard,
            wait_free=lambda _port: True,
            self_exe=self.EXECUTABLE,
            expected_executable=self.EXECUTABLE,
            expected_argv=list(self.OURS),
        )
        self.assertFalse(result)
        self.assertEqual(guard.terminate_calls, [])
        self.assertTrue(
            any("argv unavailable; preserving E4" in line for line in logs),
            logs,
        )

    def test_current_process_is_never_killed(self) -> None:
        result, killed = self._run(
            listener_pid=os.getpid(),
            image="python.exe",
            argv=self.OURS,
            parent_pid=999,
            parent_alive=False,
        )
        self.assertFalse(result)
        self.assertEqual(killed, [])

    def test_no_listener_found_is_no_op(self) -> None:
        result, killed = self._run(
            listener_pid=None,
            image="python.exe",
            argv=self.OURS,
            parent_pid=999,
            parent_alive=False,
        )
        self.assertFalse(result)
        self.assertEqual(killed, [])

    def test_kill_failure_returns_false(self) -> None:
        result, killed = self._run(
            listener_pid=4321,
            image="python.exe",
            argv=self.OURS,
            parent_pid=999,
            parent_alive=False,
            kill_ok=False,
        )
        self.assertFalse(result)
        self.assertEqual(killed, [4321])


class ReclaimAncestorWalkTest(unittest.TestCase):
    """C1 judges liveness past the venv launcher, on the real ancestor."""

    VENV = r"C:\venv\Scripts\python.exe"
    OURS = [
        VENV,
        "-m",
        "dayz_mcp",
        "--embedded",
        "--keyfile",
        "k",
        "--port",
        "8765",
    ]

    def _reclaim(self, *, ancestor_alive: bool) -> tuple[bool, list[int]]:
        parents = {100: 200, 200: 300, 300: 0}
        paths = {200: self.VENV, 300: r"C:\Program Files\nodejs\node.exe"}
        snapshot = _process_snapshot(100, self.VENV, self.OURS)
        guard = _FakeGuard([snapshot, snapshot])
        result = orphan_guard.try_reclaim_port(
            8765,
            self_exe=self.VENV,
            find_listener=lambda _port: 100,
            get_image=lambda _pid: "python.exe",
            get_argv=lambda _pid: list(self.OURS),
            get_parent=lambda pid: parents.get(pid),
            get_full_path=lambda pid: paths.get(pid),
            is_alive=lambda pid: ancestor_alive if pid == 300 else False,
            guard=guard,
            wait_free=lambda _port: True,
            is_responsive=lambda: False,
            sleep=lambda _seconds: None,
            expected_executable=self.VENV,
            expected_argv=list(self.OURS),
        )
        killed = [int(getattr(record, "pid")) for record in guard.terminate_calls]
        return result, killed

    def test_dead_real_ancestor_behind_launcher_is_reclaimed(self) -> None:
        result, killed = self._reclaim(ancestor_alive=False)
        self.assertTrue(result)
        self.assertEqual(killed, [100])

    def test_live_real_ancestor_behind_launcher_preserves_e4(self) -> None:
        result, killed = self._reclaim(ancestor_alive=True)
        self.assertFalse(result)
        self.assertEqual(killed, [])

    def test_unresolved_ancestor_chain_preserves_e4(self) -> None:
        snapshot = _process_snapshot(100, self.VENV, self.OURS)
        guard = _FakeGuard([snapshot, snapshot])
        result = orphan_guard.try_reclaim_port(
            8765,
            self_exe=self.VENV,
            find_listener=lambda _port: 100,
            get_image=lambda _pid: "python.exe",
            get_argv=lambda _pid: list(self.OURS),
            get_parent=lambda pid: {100: 200}.get(pid),
            get_full_path=lambda pid: {200: self.VENV}.get(pid),
            is_alive=lambda _pid: False,
            guard=guard,
            wait_free=lambda _port: True,
            expected_executable=self.VENV,
            expected_argv=list(self.OURS),
        )
        self.assertFalse(result)
        self.assertEqual(guard.terminate_calls, [])


class NetstatParserTest(unittest.TestCase):
    def test_listener_pid_requires_127_loopback_endpoint(self) -> None:
        original_run = orphan_guard.subprocess.run
        original_is_windows = orphan_guard._IS_WINDOWS

        class Result:
            returncode = 0
            stdout = "\n".join(
                [
                    "  TCP    0.0.0.0:8765           0.0.0.0:0              LISTENING       111",
                    "  TCP    [::1]:8765             [::]:0                 LISTENING       222",
                    "  TCP    127.0.0.1:8765         0.0.0.0:0              LISTENING       333",
                ]
            )

        try:
            orphan_guard._IS_WINDOWS = True
            orphan_guard.subprocess.run = lambda *args, **kwargs: Result()
            self.assertEqual(orphan_guard.listener_pid_for_port(8765), 333)
        finally:
            orphan_guard.subprocess.run = original_run
            orphan_guard._IS_WINDOWS = original_is_windows

    def test_oem_netstat_bytes_do_not_break_pid_detection(self) -> None:
        raw = (
            b"  TCP    127.0.0.1:8765         0.0.0.0:0              LISTENING       4242 \xa2"
        )
        with patch.object(
            orphan_guard.locale, "getpreferredencoding", return_value="utf-8"
        ):
            with self.assertRaises(UnicodeDecodeError):
                raw.decode("utf-8")
            self.assertEqual(
                orphan_guard._listener_pid_from_netstat_output(raw, 8765), 4242
            )
            with patch.object(
                orphan_guard,
                "_decode_console_bytes",
                side_effect=lambda data: data.decode("utf-8"),
            ):
                with self.assertRaises(UnicodeDecodeError):
                    orphan_guard._listener_pid_from_netstat_output(raw, 8765)

            class Result:
                returncode = 0
                stdout = (
                    b"Active \xa2 Connections\r\n"
                    b"  TCP    127.0.0.1:8765         0.0.0.0:0              LISTENING       4242\r\n"
                )

            original_run = orphan_guard.subprocess.run
            original_is_windows = orphan_guard._IS_WINDOWS
            try:
                orphan_guard._IS_WINDOWS = True
                orphan_guard.subprocess.run = lambda *args, **kwargs: Result()
                self.assertEqual(orphan_guard.listener_pid_for_port(8765), 4242)
            finally:
                orphan_guard.subprocess.run = original_run
                orphan_guard._IS_WINDOWS = original_is_windows


class StructuredArgvSnapshotTest(unittest.TestCase):
    def test_structured_argv_preserves_arguments_with_spaces(self) -> None:
        expected = [
            r"C:\Python\python.exe",
            "-m",
            "dayz_mcp",
            "--keyfile",
            r"C:\tmp\key with spaces.txt",
            "--port",
            "8765",
        ]

        class Process:
            pid = 1234

            def oneshot(self) -> object:
                return nullcontext()

            def cmdline(self) -> list[str]:
                return list(expected)

        fake_psutil = SimpleNamespace(Process=lambda _pid: Process())
        with patch.object(native_process_snapshot, "psutil", fake_psutil):
            observed = orphan_guard.command_argv_of(1234)

        self.assertEqual(observed, expected)
        self.assertIsNot(observed, expected)


_SQUATTER =_SQUATTER = r'''
import socket, sys, time
sigfile = sys.argv[1]
port = None
for i, tok in enumerate(sys.argv):
    if tok == "--port" and i + 1 < len(sys.argv):
        port = int(sys.argv[i + 1])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(("127.0.0.1", port))
    s.listen(1)
except OSError:
    open(sigfile, "w").write("ERROR")
    sys.exit(1)
open(sigfile, "w").write("READY")
while True:
    time.sleep(0.2)
'''


@unittest.skipUnless(sys.platform == "win32", "reclaim end-to-end uses netstat/wmic/TerminateProcess")
class ReclaimIntegrationTest(unittest.TestCase):
    """Real squatter + real netstat/image/cmdline/kill/port-free; only parent
    liveness is injected (False) so the dead-parent branch is deterministic."""

    def _free_port(self) -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def _make_squatter_package(self, prefix: str) -> tuple[Path, dict[str, str]]:
        package_root = Path(tempfile.gettempdir()) / f"{prefix}_{os.getpid()}"
        package_dir = package_root / "dayz_mcp"
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / "__main__.py").write_text(_SQUATTER, encoding="utf-8")
        env = os.environ.copy()
        existing_path = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(package_root) if not existing_path else str(package_root) + os.pathsep + existing_path
        return package_root, env

    def _remove_squatter_package(self, package_root: Path) -> None:
        for path in (package_root / "dayz_mcp" / "__main__.py", package_root / "dayz_mcp", package_root):
            try:
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink()
            except OSError:
                pass

    def test_dead_parent_orphan_is_reclaimed_end_to_end(self) -> None:
        package_root, env = self._make_squatter_package("dayz_mcp_squatter_pkg")
        proc = None
        try:
            for _ in range(3):  # retry on the rare ephemeral-port race
                port = self._free_port()
                sig = Path(tempfile.gettempdir()) / f"dayz_mcp_squatter_{os.getpid()}_{port}.sig"
                if sig.exists():
                    sig.unlink()
                # Cmdline carries the exact module token "-m dayz_mcp" and --port <port>.
                proc = subprocess.Popen(
                    [sys.executable, "-m", "dayz_mcp", str(sig), "--port", str(port)],
                    cwd=package_root,
                    env=env,
                )
                ready = self._await_signal(sig, proc, timeout=10.0)
                if ready == "READY":
                    break
                if proc.poll() is None:
                    proc.terminate()
                proc = None
            self.assertIsNotNone(proc, "squatter never bound a port")
            self.assertEqual(ready, "READY")

            # Sanity: the exclusive bind genuinely fails while the squatter holds it.
            with self.assertRaises(OSError):
                loopback.ExclusiveThreadingHTTPServer(("127.0.0.1", port), loopback.Handler)

            expected_executable, expected_argv = self._listener_policy(port)

            logs: list[str] = []
            reclaimed = orphan_guard.try_reclaim_port(
                port,
                log=logs.append,
                is_alive=lambda _pid: False,
                expected_executable=expected_executable,
                expected_argv=expected_argv,
            )
            self.assertTrue(reclaimed, f"reclaim failed; logs={logs}")
            self.assertIsNotNone(proc)
            proc.wait(timeout=5)
            self.assertIsNotNone(proc.poll(), "squatter should have been terminated")

            # E4 bind succeeds again on the reclaimed port.
            httpd = loopback.create_http_server(port, loopback.ServerState("k"), reclaim_orphans=False)
            httpd.server_close()
        finally:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            self._remove_squatter_package(package_root)

    def test_live_parent_dayz_mcp_listener_is_not_reclaimed_end_to_end(self) -> None:
        package_root, env = self._make_squatter_package("dayz_mcp_live_squatter_pkg")
        proc = None
        try:
            port = self._free_port()
            sig = Path(tempfile.gettempdir()) / f"dayz_mcp_live_squatter_{os.getpid()}_{port}.sig"
            if sig.exists():
                sig.unlink()
            proc = subprocess.Popen(
                [sys.executable, "-m", "dayz_mcp", str(sig), "--port", str(port)],
                cwd=package_root,
                env=env,
            )
            self.assertEqual(self._await_signal(sig, proc, timeout=10.0), "READY")

            expected_executable, expected_argv = self._listener_policy(port)
            logs: list[str] = []
            reclaimed = orphan_guard.try_reclaim_port(
                port,
                log=logs.append,
                expected_executable=expected_executable,
                expected_argv=expected_argv,
            )
            self.assertFalse(reclaimed, f"live parent should preserve E4; logs={logs}")
            self.assertIsNone(proc.poll(), "live-parent squatter must not be terminated")
        finally:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            self._remove_squatter_package(package_root)

    def _listener_policy(self, port: int) -> tuple[str, list[str]]:
        listener_pid = orphan_guard.listener_pid_for_port(port)
        if listener_pid is None:
            self.skipTest("listener pid unavailable in this environment")
        argv = orphan_guard.command_argv_of(listener_pid)
        executable = orphan_guard.full_image_path_of(listener_pid)
        if argv is None or executable is None:
            self.skipTest("structured listener identity unavailable in this environment")
        self.assertEqual(orphan_guard.classify_dayz_argv(argv), "legacy_embedded")
        self.assertTrue(orphan_guard.argv_targets_port(argv, port), argv)
        return executable, argv

    def _await_signal(self, sig: Path, proc: subprocess.Popen, timeout: float) -> str | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if sig.exists():
                return sig.read_text(encoding="utf-8").strip()
            if proc.poll() is not None:
                return sig.read_text(encoding="utf-8").strip() if sig.exists() else None
            time.sleep(0.1)
        return None


if __name__ == "__main__":
    unittest.main()
