from __future__ import annotations

import json
import os
import sys
import time
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import dayz_mcp.stdio_bridge as stdio_bridge
from dayz_mcp.stdio_bridge import (
    PLAN_B_ATTEMPTS,
    PLAN_B_BACKOFF_S,
    PLAN_B_READ_TIMEOUT_S,
    StdioBridgeError,
    classify_probe_error,
    is_host_startup_error,
    list_tools_once,
    official_client_argv,
    probe_tools_list,
    retry_with_backoff,
    main,
)
from dayz_mcp.server import parse_args as parse_server_args
from install_mcp import build_client_args, parse_args as parse_installer_args
from tests._tiers import slow_test


_MINIMAL_MCP_SERVER = r"""
import json
import sys

def _write(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()

def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            return
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        method = message.get("method")
        ident = message.get("id")
        if method == "initialize":
            _write({
                "jsonrpc": "2.0",
                "id": ident,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "stdio-fixture", "version": "0"},
                },
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            _write({
                "jsonrpc": "2.0",
                "id": ident,
                "result": {
                    "tools": [
                        {
                            "name": "bridge_status",
                            "description": "fixture",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ]
                },
            })
        elif method == "ping":
            _write({"jsonrpc": "2.0", "id": ident, "result": {}})

if __name__ == "__main__":
    main()
"""

_HUNG_MCP_SERVER = r"""
import os
import sys
from pathlib import Path

if len(sys.argv) > 1:
    Path(sys.argv[1]).write_text(str(os.getpid()), encoding="utf-8")
while True:
    line = sys.stdin.readline()
    if not line:
        break
"""


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        process_query = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(handle)
        still_active = 259
        return bool(ok) and exit_code.value == still_active
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class HostStartupErrorTest(unittest.TestCase):
    def test_markers(self) -> None:
        self.assertTrue(is_host_startup_error("McpStartupError: vsock connect failed"))
        self.assertTrue(is_host_startup_error("failed to start MCP over vsock"))
        self.assertFalse(is_host_startup_error("lease_required"))
        self.assertFalse(is_host_startup_error("daemon_unavailable"))
        self.assertFalse(is_host_startup_error("PermissionError: bad keyfile"))


class ClassifyProbeErrorTest(unittest.TestCase):
    def test_permanent_keyfile_is_not_host_vsock(self) -> None:
        code, retryable, message = classify_probe_error(PermissionError("bad keyfile"))
        self.assertEqual(code, "keyfile_unreadable")
        self.assertFalse(retryable)
        self.assertNotIn("vsock", message)
        self.assertIn("keyfile", message)

    def test_import_and_auth_are_permanent(self) -> None:
        import_code, import_retry, import_message = classify_probe_error(
            ModuleNotFoundError("mcp")
        )
        self.assertEqual(import_code, "import_error")
        self.assertFalse(import_retry)
        self.assertNotIn("vsock", import_message)
        auth_code, auth_retry, auth_message = classify_probe_error(
            RuntimeError("unauthorized 401")
        )
        self.assertEqual(auth_code, "unauthorized")
        self.assertFalse(auth_retry)
        self.assertNotIn("vsock", auth_message)

    def test_host_channel_text_is_not_remapped_as_retryable_vsock(self) -> None:
        code, retryable, message = classify_probe_error(
            ConnectionError("McpStartupError: vsock unavailable")
        )
        self.assertEqual(code, "host_channel")
        self.assertFalse(retryable)
        self.assertIn("cannot observe", message)

    def test_unknown_exception_is_not_retryable(self) -> None:
        code, retryable, message = classify_probe_error(
            RuntimeError("unclassified permanent boom")
        )
        self.assertEqual(code, "stdio_probe_failed")
        self.assertFalse(retryable)
        self.assertIn("RuntimeError", message)
        self.assertNotIn("unclassified permanent boom", message)


class OfficialClientArgvTest(unittest.TestCase):
    def test_matches_build_client_args_default_and_custom(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        tools_root = Path(temporary.name).resolve()
        default_options = parse_installer_args([], tools_root=tools_root)
        custom_key = tools_root / "shared.key"
        custom_key.write_text("not-a-secret-path-only", encoding="utf-8")
        custom_options = parse_installer_args(
            [
                "--port",
                "9876",
                "--keyfile",
                str(custom_key),
                "--expected-game-version",
                "1.28.159000",
                "--idle-timeout-seconds",
                "1800",
            ],
            tools_root=tools_root,
        )
        python = r"C:\tools\.venv-mcp\Scripts\python.exe"
        for options, platform in (
            (default_options, "claude"),
            (default_options, "codex"),
            (custom_options, "claude"),
            (custom_options, "codex"),
        ):
            official = official_client_argv(
                python=python,
                keyfile=str(options.keyfile),
                port=options.port,
                platform=platform,
                idle_timeout_s=options.idle_timeout_seconds,
                expected_game_version=options.expected_game_version,
                allow_legacy=options.allow_legacy,
                tools_root=tools_root,
            )
            self.assertEqual(official[0], python)
            self.assertEqual(official[1:], build_client_args(options, platform))
            self.assertIn("--idle-timeout", official)
            self.assertIn("--client-platform", official)
            self.assertNotIn("--embedded", official)
            parsed = parse_server_args(official[3:])
            self.assertEqual(parsed.mode, "client")
            self.assertEqual(parsed.port, options.port)
            self.assertEqual(parsed.client_platform, platform)
            self.assertEqual(parsed.require_version, not options.allow_legacy)
            self.assertEqual(parsed.idle_timeout_s, options.idle_timeout_seconds)

    def test_allow_legacy_omits_require_version(self) -> None:
        argv = official_client_argv(
            python=r"C:\venv\python.exe",
            keyfile=r"C:\tools\.dayz_mcp.key",
            allow_legacy=True,
            idle_timeout_s=2.5,
            platform="claude",
        )
        self.assertNotIn("--require-version", argv)
        self.assertEqual(argv[argv.index("--idle-timeout") + 1], "2.5")


class RetryWithBackoffTest(unittest.TestCase):
    def test_recovers_after_transient_error(self) -> None:
        sleeps: list[float] = []
        calls = {"n": 0}

        def flaky() -> str:
            calls["n"] = calls["n"] + 1
            if calls["n"] < 3:
                raise ConnectionError("connection refused")
            return "bridge_status"

        result = retry_with_backoff(
            flaky,
            attempts=PLAN_B_ATTEMPTS,
            backoff_s=PLAN_B_BACKOFF_S,
            sleeper=sleeps.append,
        )
        self.assertEqual(result, "bridge_status")
        self.assertEqual(sleeps, [2.0, 4.0])

    def test_permanent_keyfile_does_not_retry(self) -> None:
        calls = {"n": 0}
        sleeps: list[float] = []

        def boom() -> None:
            calls["n"] = calls["n"] + 1
            raise PermissionError("bad keyfile")

        with self.assertRaises(StdioBridgeError) as raised:
            retry_with_backoff(
                boom,
                attempts=PLAN_B_ATTEMPTS,
                backoff_s=PLAN_B_BACKOFF_S,
                sleeper=sleeps.append,
            )
        self.assertEqual(calls["n"], 1)
        self.assertEqual(sleeps, [])
        self.assertEqual(raised.exception.code, "keyfile_unreadable")
        self.assertNotIn("vsock", str(raised.exception))

    def test_unknown_permanent_exception_does_not_retry(self) -> None:
        calls = {"n": 0}
        sleeps: list[float] = []

        def boom() -> None:
            calls["n"] = calls["n"] + 1
            raise RuntimeError("unclassified permanent boom")

        with self.assertRaises(StdioBridgeError) as raised:
            retry_with_backoff(
                boom,
                attempts=PLAN_B_ATTEMPTS,
                backoff_s=PLAN_B_BACKOFF_S,
                sleeper=sleeps.append,
            )
        self.assertEqual(calls["n"], 1)
        self.assertEqual(sleeps, [])
        self.assertEqual(raised.exception.code, "stdio_probe_failed")

    def test_exhausted_transient_budget_keeps_code(self) -> None:
        with self.assertRaises(StdioBridgeError) as raised:
            retry_with_backoff(
                lambda: (_ for _ in ()).throw(ConnectionError("connection refused")),
                attempts=2,
                backoff_s=0.0,
                sleeper=lambda _delay: None,
            )
        message = str(raised.exception)
        self.assertEqual(raised.exception.code, "connection_refused")
        self.assertIn("after 2 attempt", message)
        self.assertNotIn("vsock is not the transport", message)

    def test_rejects_zero_attempts(self) -> None:
        with self.assertRaises(ValueError):
            retry_with_backoff(lambda: None, attempts=0)

    def test_rejects_negative_backoff(self) -> None:
        with self.assertRaises(ValueError):
            retry_with_backoff(lambda: None, backoff_s=-1.0)


class ProbeToolsListTest(unittest.TestCase):
    def test_uses_injected_lister(self) -> None:
        seen: list[list[str]] = []

        def fake_list(command: list[str]) -> list[str]:
            seen.append(command)
            return ["bridge_status", "session_status"]

        names = probe_tools_list(
            ["python", "-m", "dayz_mcp", "--client"],
            list_tools=fake_list,
            sleeper=lambda _delay: None,
        )
        self.assertEqual(names, ["bridge_status", "session_status"])
        self.assertEqual(seen[0][3], "--client")


class ListToolsOnceTransportTest(unittest.TestCase):
    def _script(self, body: str, name: str) -> Path:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_lists_tools_over_real_stdio(self) -> None:
        script = self._script(_MINIMAL_MCP_SERVER, "minimal_mcp.py")
        names = list_tools_once(
            [sys.executable, str(script)],
            read_timeout_s=5.0,
        )
        self.assertEqual(names, ["bridge_status"])

    @slow_test
    def test_hung_handshake_times_out_and_closes_child(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        script = Path(temporary.name) / "hung_mcp.py"
        script.write_text(_HUNG_MCP_SERVER, encoding="utf-8")
        pid_path = Path(temporary.name) / "child.pid"
        started = time.monotonic()
        with self.assertRaises(Exception) as raised:
            list_tools_once(
                [sys.executable, str(script), str(pid_path)],
                read_timeout_s=0.4,
            )
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 8.0)
        code, retryable, message = classify_probe_error(raised.exception)
        self.assertEqual(code, "handshake_timeout")
        self.assertTrue(retryable)
        self.assertIn("handshake_timeout", message)
        self.assertTrue(pid_path.is_file())
        pid = int(pid_path.read_text(encoding="utf-8").strip())
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and _pid_alive(pid):
            time.sleep(0.05)
        self.assertFalse(_pid_alive(pid))

    @slow_test
    def test_probe_budget_survives_hung_attempts(self) -> None:
        script = self._script(_HUNG_MCP_SERVER, "hung_budget.py")
        started = time.monotonic()
        with self.assertRaises(StdioBridgeError) as raised:
            probe_tools_list(
                [sys.executable, str(script)],
                attempts=3,
                backoff_s=0.0,
                read_timeout_s=0.35,
            )
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 15.0)
        self.assertEqual(raised.exception.code, "handshake_timeout")


class PrintCommandCliTest(unittest.TestCase):
    def test_default_prints_official_argv(self) -> None:
        buf = StringIO()
        with patch("sys.stdout", buf):
            code = main(
                [
                    "--print-command",
                    "--python",
                    r"C:\venv\python.exe",
                    "--keyfile",
                    r"C:\tools\.dayz_mcp.key",
                    "--port",
                    "8765",
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["plan"], "stdio_client")
        argv = payload["argv"]
        self.assertEqual(argv[3], "--client")
        self.assertIn("--idle-timeout", argv)
        self.assertIn("--require-version", argv)
        self.assertEqual(argv[argv.index("--client-platform") + 1], "claude")
        self.assertEqual(payload["close_at"], "session_end")
        self.assertEqual(payload["read_timeout_s"], PLAN_B_READ_TIMEOUT_S)
        self.assertNotIn("embedded", " ".join(argv))
        self.assertNotIn("vsock", payload["note"].casefold())

    def test_custom_port_keyfile_and_codex_platform(self) -> None:
        buf = StringIO()
        with patch("sys.stdout", buf):
            code = main(
                [
                    "--print-command",
                    "--python",
                    r"C:\venv\python.exe",
                    "--keyfile",
                    r"D:\custom\grid.key",
                    "--port",
                    "9876",
                    "--client-platform",
                    "codex",
                    "--expected-game-version",
                    "1.28.159000",
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        argv = payload["argv"]
        self.assertEqual(argv[argv.index("--port") + 1], "9876")
        self.assertEqual(argv[argv.index("--keyfile") + 1], r"D:\custom\grid.key")
        self.assertEqual(argv[argv.index("--client-platform") + 1], "codex")
        self.assertIn("--require-version", argv)
        parsed = parse_server_args(argv[3:])
        self.assertEqual(parsed.mode, "client")
        self.assertEqual(parsed.port, 9876)
        self.assertEqual(parsed.client_platform, "codex")
        self.assertTrue(parsed.require_version)
        self.assertEqual(parsed.expected_game_version, "1.28.159000")


class ProbeCliTest(unittest.TestCase):
    def test_probe_success_json(self) -> None:
        buf = StringIO()
        with patch(
            "dayz_mcp.stdio_bridge.probe_tools_list",
            return_value=["bridge_status"],
        ) as probe:
            with patch("sys.stdout", buf):
                code = main(
                    [
                        "--probe",
                        "--python",
                        r"C:\venv\python.exe",
                        "--keyfile",
                        r"C:\tools\.dayz_mcp.key",
                    ]
                )
        self.assertEqual(code, 0)
        self.assertEqual(probe.call_count, 1)
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["tools"], ["bridge_status"])
        self.assertIn("--idle-timeout", payload["argv"])

    def test_probe_permanent_failure_is_not_vsock(self) -> None:
        calls = {"n": 0}

        def boom(_command: list[str], **_kwargs: object) -> list[str]:
            calls["n"] = calls["n"] + 1
            raise PermissionError("bad keyfile")

        buf = StringIO()
        with patch.object(stdio_bridge, "list_tools_once", side_effect=boom):
            with patch("sys.stdout", buf):
                code = main(
                    [
                        "--probe",
                        "--attempts",
                        "3",
                        "--backoff-s",
                        "0",
                        "--python",
                        r"C:\venv\python.exe",
                        "--keyfile",
                        r"C:\tools\.dayz_mcp.key",
                    ]
                )
        self.assertEqual(code, 1)
        self.assertEqual(calls["n"], 1)
        payload = json.loads(buf.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "keyfile_unreadable")
        self.assertNotIn("vsock", payload["error"].casefold())

    def test_probe_and_print_command_are_exclusive(self) -> None:
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main(["--probe", "--print-command"])
        self.assertEqual(raised.exception.code, 2)

    def test_attempts_zero_is_parser_error(self) -> None:
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main(["--probe", "--attempts", "0"])
        self.assertEqual(raised.exception.code, 2)

    def test_negative_backoff_is_parser_error(self) -> None:
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main(["--probe", "--backoff-s", "-1"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
