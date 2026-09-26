from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
import urllib.request
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from dayz_mcp.daemon_contract import build_daemon_argv
from dayz_mcp.host_config import CLAUDE_TIMEOUT_MS, CODEX_TIMEOUT_SECONDS


TOOLS_DIR = Path(__file__).resolve().parents[1]
DAEMON_FIXTURE_SITE = Path(__file__).resolve().parent / "fixtures" / "dayz_mcp"
IDLE_TIMEOUT_S = 0.5


def _unused_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
    finally:
        probe.close()


def _daemon_argv(port: int, keyfile: Path, launcher: str) -> list[str]:
    return build_daemon_argv(
        SimpleNamespace(
            port=port,
            keyfile=str(keyfile.resolve()),
            idle_timeout_s=IDLE_TIMEOUT_S,
            expected_game_version=None,
            require_version=False,
            enable_exec_enforce=False,
        ),
        python=launcher,
    )


def _client_args(port: int, keyfile: Path, platform: str) -> list[str]:
    return [
        "-m",
        "dayz_mcp",
        "--client",
        "--keyfile",
        str(keyfile.resolve()),
        "--port",
        str(port),
        "--idle-timeout",
        str(IDLE_TIMEOUT_S),
        "--client-platform",
        platform,
        "--no-daemon-autospawn",
    ]


def _write_host_configs(
    home: Path,
    *,
    port: int,
    keyfile: Path,
    launcher: str,
) -> None:
    home.mkdir(parents=True)
    (home / ".codex").mkdir()
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "dayz-mcp": {
                        "type": "stdio",
                        "command": launcher,
                        "args": _client_args(port, keyfile, "claude"),
                        "timeout": CLAUDE_TIMEOUT_MS,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (home / ".codex" / "config.toml").write_text(
        "\n".join(
            (
                "[mcp_servers.dayz-mcp]",
                f"command = {json.dumps(launcher)}",
                f"args = {json.dumps(_client_args(port, keyfile, 'codex'))}",
                f"tool_timeout_sec = {CODEX_TIMEOUT_SECONDS}",
                "",
            )
        ),
        encoding="utf-8",
    )


def _fixture_environment(base: Path, home: Path) -> dict[str, str]:
    environment = os.environ.copy()
    inherited_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(DAEMON_FIXTURE_SITE)
    if inherited_pythonpath:
        environment["PYTHONPATH"] += os.pathsep + inherited_pythonpath
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)
    environment["LOCALAPPDATA"] = str(base / "local")
    environment["PYTHONUNBUFFERED"] = "1"
    environment["DAYZ_MCP_FIXTURE_MODE"] = "none"
    environment["DAYZ_MCP_FIXTURE_SIGNAL"] = str(base / "unused-signal")
    environment["DAYZ_MCP_FIXTURE_MIGRATION"] = str(base / "migration")
    return environment


def _start_daemon(
    *,
    port: int,
    keyfile: Path,
    launcher: str,
    environment: dict[str, str],
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        _daemon_argv(port, keyfile, launcher),
        cwd=str(TOOLS_DIR),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_status(
    port: int,
    key: str,
    process: subprocess.Popen[str],
    timeout_s: float = 10.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = _read_stderr(process)
            raise AssertionError(
                f"fixture daemon exited early rc={process.returncode}: {stderr}"
            )
        url = (
            f"http://127.0.0.1:{port}/status?key="
            + urllib.parse.quote(key, safe="")
        )
        try:
            with urllib.request.urlopen(url, timeout=0.25) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception as error:
            last_error = type(error).__name__
            time.sleep(0.02)
    raise AssertionError(f"fixture daemon did not become ready: {last_error}")


def _read_stderr(process: subprocess.Popen[str]) -> str:
    if process.stderr is None:
        return ""
    try:
        return process.stderr.read()
    finally:
        process.stderr.close()


def _tool_json(result: object) -> dict[str, object]:
    if getattr(result, "isError", False):
        raise AssertionError(f"MCP tool failed: {result!r}")
    content = getattr(result, "content", None)
    if not isinstance(content, list) or len(content) != 1:
        raise AssertionError(f"unexpected MCP content: {result!r}")
    text = getattr(content[0], "text", None)
    if not isinstance(text, str):
        raise AssertionError(f"unexpected MCP text content: {result!r}")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise AssertionError(f"unexpected MCP payload: {payload!r}")
    return payload


class ClientCredentialRotationProcessE2ETest(unittest.IsolatedAsyncioTestCase):
    async def test_same_client_process_recovers_accredited_daemon_replacement(
        self,
    ) -> None:
        owned_daemons: list[subprocess.Popen[str]] = []
        forced_cleanup: list[int] = []
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            home = base / "home"
            keyfile = base / "daemon.key"
            keyfile.write_text("credential-a", encoding="utf-8")
            port = _unused_port()
            launcher = str(Path(sys.executable).resolve(strict=True))
            _write_host_configs(
                home,
                port=port,
                keyfile=keyfile,
                launcher=launcher,
            )
            environment = _fixture_environment(base, home)
            daemon_a = _start_daemon(
                port=port,
                keyfile=keyfile,
                launcher=launcher,
                environment=environment,
            )
            owned_daemons.append(daemon_a)
            client_stderr = (base / "client.stderr").open(
                "w+", encoding="utf-8"
            )

            try:
                ready_a = await asyncio.to_thread(
                    _wait_status, port, "credential-a", daemon_a
                )
                parameters = StdioServerParameters(
                    command=launcher,
                    args=_client_args(port, keyfile, "codex"),
                    env=environment,
                    cwd=TOOLS_DIR,
                )
                async with stdio_client(parameters, errlog=client_stderr) as streams:
                    async with ClientSession(
                        streams[0],
                        streams[1],
                        read_timeout_seconds=timedelta(seconds=15),
                    ) as session:
                        await session.initialize()
                        acquired_a = _tool_json(
                            await session.call_tool(
                                "session_acquire",
                                {"purpose": "credential rotation e2e A"},
                            )
                        )
                        identity_a = json.loads(
                            str(acquired_a["client_identity_json"])
                        )
                        await session.call_tool(
                            "session_release",
                            {"lease_token": acquired_a["lease_token"]},
                        )
                        status_a = _tool_json(
                            await session.call_tool("bridge_status", {})
                        )

                        return_code_a = await asyncio.to_thread(
                            daemon_a.wait, 12.0
                        )
                        stderr_a = _read_stderr(daemon_a)
                        self.assertEqual(return_code_a, 0, stderr_a)

                        replacement = keyfile.with_suffix(".next")
                        replacement.write_text("credential-b", encoding="utf-8")
                        replacement.replace(keyfile)
                        daemon_b = _start_daemon(
                            port=port,
                            keyfile=keyfile,
                            launcher=launcher,
                            environment=environment,
                        )
                        owned_daemons.append(daemon_b)
                        ready_b = await asyncio.to_thread(
                            _wait_status, port, "credential-b", daemon_b
                        )

                        status_b = _tool_json(
                            await session.call_tool("bridge_status", {})
                        )
                        acquired_b = _tool_json(
                            await session.call_tool(
                                "session_acquire",
                                {"purpose": "credential rotation e2e B"},
                            )
                        )
                        identity_b = json.loads(
                            str(acquired_b["client_identity_json"])
                        )
                        await session.call_tool(
                            "session_release",
                            {"lease_token": acquired_b["lease_token"]},
                        )

                        self.assertEqual(identity_b, identity_a)
                        self.assertGreater(int(identity_a["pid"]), 0)
                        self.assertNotEqual(daemon_a.pid, daemon_b.pid)
                        self.assertNotEqual(
                            ready_a["daemon_generation"],
                            ready_b["daemon_generation"],
                        )
                        self.assertEqual(
                            status_a["daemon_generation"],
                            ready_a["daemon_generation"],
                        )
                        self.assertEqual(
                            status_b["daemon_generation"],
                            ready_b["daemon_generation"],
                        )
                        self.assertEqual(
                            status_b["credential_refresh"]["recovered_count"],
                            1,
                        )

                        return_code_b = await asyncio.to_thread(
                            daemon_b.wait, 12.0
                        )
                        stderr_b = _read_stderr(daemon_b)
                        self.assertEqual(return_code_b, 0, stderr_b)
            finally:
                client_stderr.close()
                for process in owned_daemons:
                    if process.poll() is None:
                        forced_cleanup.append(process.pid)
                        process.terminate()
                        process.wait(timeout=5.0)
                    if process.stderr is not None and not process.stderr.closed:
                        _read_stderr(process)
            self.assertEqual(forced_cleanup, [])


if __name__ == "__main__":
    unittest.main()
