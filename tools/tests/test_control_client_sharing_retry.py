"""Brief sharing retry: a brief ERROR_SHARING_VIOLATION no longer fails a call.

Fake-policy cases run on a replaced module clock and sleep, so they never
wait in real time. The Windows cases use a real second process holding a
synthetic pinned config, as in test_shareconflict_windows.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import control_client, daemon_policy, host_config
from tests._tiers import slow_test
from tests.test_control_client import _policy


def _identity():
    return control_client.ControlIdentity(
        platform="unknown",
        pid=123,
        ppid=45,
        started_at_utc="2026-07-22T00:00:00Z",
        session_id="12345678-1234-4234-8234-1234567890ab",
        task_label="sharing-retry",
    )


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _client_with_hook(hook):
    """Build a client whose revalidation runs hook only after construction."""
    temporary = tempfile.TemporaryDirectory()
    keyfile = Path(temporary.name) / "daemon.key"
    keyfile.write_text("fixture-key\n", encoding="utf-8")
    policy = _policy(keyfile)
    ready = False

    def revalidate() -> None:
        if ready:
            hook()

    object.__setattr__(policy, "_revalidation_hook", revalidate)
    client = control_client.ControlClient(policy=policy, identity=_identity())
    ready = True
    return client, temporary


class SharingRetryFakePolicyTests(unittest.TestCase):
    def test_sharing_violation_twice_then_passes_is_sent(self) -> None:
        attempts = 0

        def hook() -> None:
            nonlocal attempts
            attempts += 1
            if attempts <= 2:
                raise host_config.HostConfigError(
                    "daemon_provenance_conflict", winerror=32
                )

        client, temporary = _client_with_hook(hook)
        clock = _FakeClock()
        try:
            with (
                patch.object(
                    client._credential_provider,
                    "request_with_refresh",
                    return_value=(200, b'{"status":"ok"}'),
                ) as send,
                patch.object(control_client, "_monotonic", clock.monotonic),
                patch.object(control_client, "_revalidation_sleep", clock.sleep),
            ):
                result = client._request_once("/session/status", {}, 5.0)
        finally:
            temporary.cleanup()
        self.assertEqual(result, {"status": "ok"})
        send.assert_called_once()
        self.assertEqual(attempts, 3)
        self.assertEqual(clock.sleeps, [0.05, 0.05])

    def test_sharing_violation_past_budget_fails_with_validated_step(self) -> None:
        attempts = 0

        def hook() -> None:
            nonlocal attempts
            attempts += 1
            raise host_config.HostConfigError(
                "daemon_provenance_conflict", winerror=32
            )

        client, temporary = _client_with_hook(hook)
        clock = _FakeClock()
        try:
            with (
                patch.object(
                    client._credential_provider,
                    "request_with_refresh",
                    return_value=(200, b'{"status":"ok"}'),
                ) as send,
                patch.object(control_client, "_monotonic", clock.monotonic),
                patch.object(control_client, "_revalidation_sleep", clock.sleep),
            ):
                with self.assertRaises(
                    control_client.ControlClientError
                ) as raised:
                    client._request_once("/session/status", {}, 5.0)
        finally:
            temporary.cleanup()
        error = raised.exception
        self.assertEqual(error.code, "client_policy_untrusted_open_new_session")
        self.assertEqual(error.policy_cause, "HostConfigError:32")
        self.assertEqual(error.request_stage, "pre_request")
        self.assertEqual(error.http_bytes_sent, 0)
        send.assert_not_called()
        self.assertGreater(attempts, 1)
        self.assertAlmostEqual(sum(clock.sleeps), 0.5, places=6)
        self.assertIn("could not be validated", error.hint)
        self.assertNotIn("If the tool list includes server_reload", error.hint)

    def test_slow_attempt_is_not_retried_once_budget_is_spent(self) -> None:
        # Each attempt itself takes 0.49 s of the 0.5 s budget: the first
        # failure leaves only the remaining 0.01 s to wait, and no second
        # attempt may start at or past the deadline, even though it would
        # pass. Without the deadline check before retrying, the second
        # attempt runs, passes at 0.99 s and the request is sent.
        attempts = 0
        clock = _FakeClock()

        def hook() -> None:
            nonlocal attempts
            attempts += 1
            clock.now += 0.49
            if attempts == 1:
                raise host_config.HostConfigError(
                    "daemon_provenance_conflict", winerror=32
                )

        client, temporary = _client_with_hook(hook)
        try:
            with (
                patch.object(
                    client._credential_provider,
                    "request_with_refresh",
                    return_value=(200, b'{"status":"ok"}'),
                ) as send,
                patch.object(control_client, "_monotonic", clock.monotonic),
                patch.object(control_client, "_revalidation_sleep", clock.sleep),
            ):
                with self.assertRaises(
                    control_client.ControlClientError
                ) as raised:
                    client._request_once("/session/status", {}, 5.0)
        finally:
            temporary.cleanup()
        error = raised.exception
        self.assertEqual(attempts, 1)
        self.assertEqual(len(clock.sleeps), 1)
        self.assertAlmostEqual(clock.sleeps[0], 0.01, places=6)
        self.assertEqual(error.code, "client_policy_untrusted_open_new_session")
        self.assertEqual(error.policy_cause, "HostConfigError:32")
        self.assertEqual(error.request_stage, "pre_request")
        self.assertEqual(error.http_bytes_sent, 0)
        send.assert_not_called()
        self.assertIn("could not be validated", error.hint)
        self.assertNotIn("If the tool list includes server_reload", error.hint)

    def test_attempt_started_inside_budget_that_passes_late_is_sent(self) -> None:
        # Each attempt itself takes 0.44 s: the first fails at 0.44 s,
        # one 0.05 s wait leaves the second attempt starting at 0.49 s,
        # inside the 0.5 s budget, and passing at 0.93 s. The budget
        # bounds when an attempt may start, not how long it takes, so
        # the late pass is accepted and the request is sent.
        attempts = 0
        clock = _FakeClock()

        def hook() -> None:
            nonlocal attempts
            attempts += 1
            clock.now += 0.44
            if attempts == 1:
                raise host_config.HostConfigError(
                    "daemon_provenance_conflict", winerror=32
                )

        client, temporary = _client_with_hook(hook)
        try:
            with (
                patch.object(
                    client._credential_provider,
                    "request_with_refresh",
                    return_value=(200, b'{"status":"ok"}'),
                ) as send,
                patch.object(control_client, "_monotonic", clock.monotonic),
                patch.object(control_client, "_revalidation_sleep", clock.sleep),
            ):
                result = client._request_once("/session/status", {}, 5.0)
        finally:
            temporary.cleanup()
        self.assertEqual(result, {"status": "ok"})
        send.assert_called_once()
        self.assertEqual(attempts, 2)
        self.assertEqual(clock.sleeps, [0.05])
        self.assertAlmostEqual(clock.now, 1000.93, places=6)

    def test_attempt_started_inside_budget_that_fails_late_is_not_retried(self) -> None:
        # Same 0.44 s attempts, but the second attempt also fails with
        # winerror 32, at 0.93 s: no third attempt may start, and the
        # call ends in the same rejection as an exhausted budget.
        attempts = 0
        clock = _FakeClock()

        def hook() -> None:
            nonlocal attempts
            attempts += 1
            clock.now += 0.44
            raise host_config.HostConfigError(
                "daemon_provenance_conflict", winerror=32
            )

        client, temporary = _client_with_hook(hook)
        try:
            with (
                patch.object(
                    client._credential_provider,
                    "request_with_refresh",
                    return_value=(200, b'{"status":"ok"}'),
                ) as send,
                patch.object(control_client, "_monotonic", clock.monotonic),
                patch.object(control_client, "_revalidation_sleep", clock.sleep),
            ):
                with self.assertRaises(
                    control_client.ControlClientError
                ) as raised:
                    client._request_once("/session/status", {}, 5.0)
        finally:
            temporary.cleanup()
        error = raised.exception
        self.assertEqual(attempts, 2)
        self.assertEqual(clock.sleeps, [0.05])
        self.assertAlmostEqual(clock.now, 1000.93, places=6)
        self.assertEqual(error.code, "client_policy_untrusted_open_new_session")
        self.assertEqual(error.policy_cause, "HostConfigError:32")
        self.assertEqual(error.request_stage, "pre_request")
        self.assertEqual(error.http_bytes_sent, 0)
        send.assert_not_called()
        self.assertIn("could not be validated", error.hint)
        self.assertNotIn("If the tool list includes server_reload", error.hint)

    def test_winerror_5_is_not_retried(self) -> None:
        attempts = 0

        def hook() -> None:
            nonlocal attempts
            attempts += 1
            raise host_config.HostConfigError(
                "daemon_provenance_conflict", winerror=5
            )

        client, temporary = _client_with_hook(hook)
        clock = _FakeClock()
        try:
            with (
                patch.object(
                    client._credential_provider,
                    "request_with_refresh",
                    return_value=(200, b'{"status":"ok"}'),
                ) as send,
                patch.object(control_client, "_monotonic", clock.monotonic),
                patch.object(control_client, "_revalidation_sleep", clock.sleep),
            ):
                with self.assertRaises(
                    control_client.ControlClientError
                ) as raised:
                    client._request_once("/session/status", {}, 5.0)
        finally:
            temporary.cleanup()
        error = raised.exception
        self.assertEqual(attempts, 1)
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(error.code, "client_policy_untrusted_open_new_session")
        self.assertEqual(error.policy_cause, "HostConfigError:5")
        self.assertEqual(error.request_stage, "pre_request")
        self.assertEqual(error.http_bytes_sent, 0)
        send.assert_not_called()
        self.assertIn("could not be validated", error.hint)

    def test_policy_drift_is_not_retried(self) -> None:
        attempts = 0

        def hook() -> None:
            nonlocal attempts
            attempts += 1
            raise ValueError("daemon_policy_drift")

        client, temporary = _client_with_hook(hook)
        clock = _FakeClock()
        try:
            with (
                patch.object(
                    client._credential_provider,
                    "request_with_refresh",
                    return_value=(200, b'{"status":"ok"}'),
                ) as send,
                patch.object(control_client, "_monotonic", clock.monotonic),
                patch.object(control_client, "_revalidation_sleep", clock.sleep),
            ):
                with self.assertRaises(
                    control_client.ControlClientError
                ) as raised:
                    client._request_once("/session/status", {}, 5.0)
        finally:
            temporary.cleanup()
        error = raised.exception
        self.assertEqual(attempts, 1)
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(error.code, "client_policy_untrusted_open_new_session")
        self.assertEqual(error.policy_cause, "ValueError:daemon_policy_drift")
        self.assertEqual(error.request_stage, "pre_request")
        self.assertEqual(error.http_bytes_sent, 0)
        send.assert_not_called()
        self.assertIn("If the tool list includes server_reload", error.hint)


# Independent raw Win32 holder, as in test_shareconflict_windows: it never
# imports dayz_mcp and never writes the opened file.
_HOLDER = r"""
import ctypes, json, os, sys
from ctypes import wintypes
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                              wintypes.HANDLE)
kernel.CreateFileW.restype = wintypes.HANDLE
kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel.CloseHandle.restype = wintypes.BOOL
handle = kernel.CreateFileW(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]),
                            None, 3, 0x80, None)
error = ctypes.get_last_error()
opened = handle != ctypes.c_void_p(-1).value
print(json.dumps({"pid": os.getpid(), "opened": opened,
                  "winerror": 0 if opened else error}), flush=True)
if opened:
    try:
        sys.stdin.readline()
    finally:
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())
"""


@contextmanager
def _held_by_child(path: Path, access: int, share: int):
    """Yield a release callable for the holder child holding path."""
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", _HOLDER, str(path), str(access), str(share)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", cwd=str(path.parent),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    ready = queue.Queue()
    threading.Thread(target=lambda: ready.put(child.stdout.readline()),
                     daemon=True).start()
    released = False

    def release() -> None:
        nonlocal released
        if released:
            return
        released = True
        try:
            child.stdin.write("release\n")
            child.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass

    try:
        line = ready.get(timeout=15)
        if not line:
            raise AssertionError("holder did not report its native open result")
        info = json.loads(line)
        if not info["opened"]:
            raise AssertionError(f"holder did not open: {info}")
        yield release
    finally:
        release()
        try:
            _, errors = child.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            # Owned test helper only; never a daemon, host CLI, or game process.
            child.kill()
            child.communicate()
            raise AssertionError("owned holder did not release on pipe input")
        if child.returncode != 0 or errors:
            raise AssertionError(f"holder exit={child.returncode}: {errors}")


@unittest.skipUnless(os.name == "nt", "real Windows sharing semantics required")
class SharingRetryWindowsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="sharing-retry-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.paths = {"claude": self.root / ".claude.json",
                      "codex": self.root / "config.toml"}
        self.keyfile = self.root / "fixture.key"
        self.keyfile.write_bytes(b"synthetic-fixture-only")
        for platform, path in self.paths.items():
            entry = {"command": str(Path(sys.executable).resolve()), "args": [
                "-m", "dayz_mcp", "--client", "--port", "18765",
                "--keyfile", str(self.keyfile), "--idle-timeout", "12.5",
                "--client-platform", platform,
            ]}
            if platform == "claude":
                entry.update(type="stdio", timeout=host_config.CLAUDE_TIMEOUT_MS)
                raw = (json.dumps({"mcpServers": {"dayz-mcp": entry}}) + "\n").encode()
            else:
                entry["tool_timeout_sec"] = host_config.CODEX_TIMEOUT_SECONDS
                lines = ["[mcp_servers.dayz-mcp]"]
                lines.extend(
                    f"{key} = {json.dumps(value)}" for key, value in entry.items()
                )
                raw = ("\n".join(lines) + "\n").encode()
            path.write_bytes(raw)
        original_resolve = host_config.resolve_daemon_provenance
        resolve = lambda: original_resolve(  # noqa: E731
            claude_path=self.paths["claude"], codex_path=self.paths["codex"])
        patcher = patch.object(
            host_config, "resolve_daemon_provenance", side_effect=resolve)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.policy = daemon_policy.load_normal_daemon_policy()
        identity = control_client.ControlIdentity(
            platform="codex", pid=os.getpid(), ppid=os.getppid(),
            started_at_utc="2026-09-08T00:00:00Z",
            session_id="sharing-retry-offline",
        )
        self.client = control_client.ControlClient(
            policy=self.policy, identity=identity)
        send_patcher = patch.object(
            self.client._credential_provider, "request_with_refresh",
            return_value=(200, b'{"status":"ok"}'))
        self.send = send_patcher.start()
        self.addCleanup(send_patcher.stop)

    @slow_test
    def test_writer_released_after_about_100ms_lets_request_through(self):
        with _held_by_child(self.paths["claude"], 0x40000000, 7) as release:
            timer = threading.Timer(0.1, release)
            timer.start()
            try:
                result = self.client._request_once("/session/status", {}, 5.0)
            finally:
                timer.cancel()
        self.assertEqual(result, {"status": "ok"})
        self.send.assert_called_once()

    @slow_test
    def test_writer_held_for_whole_call_fails_with_validated_step(self):
        with _held_by_child(self.paths["claude"], 0x40000000, 7):
            with self.assertRaises(control_client.ControlClientError) as raised:
                self.client._request_once("/session/status", {}, 5.0)
        error = raised.exception
        self.assertEqual(error.code, "client_policy_untrusted_open_new_session")
        self.assertEqual(error.policy_cause, "HostConfigError:32")
        self.assertEqual(error.request_stage, "pre_request")
        self.assertEqual(error.http_bytes_sent, 0)
        self.send.assert_not_called()
        self.assertIn("could not be validated", error.hint)
        self.assertNotIn("If the tool list includes server_reload", error.hint)


if __name__ == "__main__":
    unittest.main()

