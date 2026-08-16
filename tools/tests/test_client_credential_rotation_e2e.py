from __future__ import annotations

import asyncio
import hashlib
import http.client
import json
import os
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from dayz_mcp import (
    accredited_daemon_transport,
    control_client,
    daemon_credential,
    native_process_snapshot,
    orphan_guard,
)
from dayz_mcp.daemon_contract import build_daemon_argv, daemon_runtime_cwd
from dayz_mcp.daemon_policy import AccreditedDaemonPolicy
from dayz_mcp.native_process_guard import NativeProcessGuard, identity_hashes


def _unused_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
    finally:
        probe.close()


def _policy(
    *,
    argv: list[str],
    cwd: str,
    native_executable: str,
    port: int,
    keyfile: Path,
) -> AccreditedDaemonPolicy:
    authority = {
        "argv": list(argv),
        "cwd": cwd,
        "host": "127.0.0.1",
        "keyfile": str(keyfile),
        "kind": "normal",
        "native_executable": native_executable,
        "port": port,
        "security_build_id": None,
    }
    authority_sha256 = hashlib.sha256(
        json.dumps(
            authority,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return AccreditedDaemonPolicy(
        kind="normal",
        host="127.0.0.1",
        port=port,
        keyfile=str(keyfile),
        native_executable=native_executable,
        argv=tuple(argv),
        cwd=cwd,
        security_build_id=None,
        authority_sha256=authority_sha256,
    )


def _raw_status(port: int, key: str) -> dict[str, object] | None:
    url = (
        f"http://127.0.0.1:{port}/status?key="
        + urllib.parse.quote(key, safe="")
    )
    try:
        with urllib.request.urlopen(url, timeout=0.25) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return value if isinstance(value, dict) else None


def _wait_ready(
    child: subprocess.Popen[str],
    *,
    port: int,
    key: str,
) -> dict[str, object]:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        payload = _raw_status(port, key)
        if payload is not None:
            return payload
        if child.poll() is not None:
            break
        time.sleep(0.025)
    stderr = ""
    if child.poll() is not None:
        _stdout, stderr = child.communicate(timeout=1.0)
    raise AssertionError(
        f"isolated daemon did not become ready; returncode={child.poll()}; "
        f"stderr={stderr}"
    )


def _finish_owned_fixture(
    child: subprocess.Popen[str],
    *,
    timeout: float = 12.0,
) -> tuple[str, str]:
    try:
        return child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        child.terminate()
        try:
            return child.communicate(timeout=3.0)
        except subprocess.TimeoutExpired:
            child.kill()
            return child.communicate(timeout=3.0)


def _tool_payload(result: object) -> dict[str, object]:
    if getattr(result, "isError", None) is not False:
        raise AssertionError(f"MCP tool failed: {result!r}")
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    content = getattr(result, "content", None)
    if not isinstance(content, list) or not content:
        raise AssertionError("MCP tool returned no object content")
    text = getattr(content[0], "text", None)
    if not isinstance(text, str):
        raise AssertionError("MCP tool returned no text content")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise AssertionError("MCP tool returned non-object content")
    return payload


class LiveClientCredentialRotationE2ETest(unittest.TestCase):
    def test_same_live_client_recovers_across_authorized_daemon_restart(
        self,
    ) -> None:
        if native_process_snapshot.psutil is None:
            self.skipTest(
                "accredited E2E requires the project MCP runtime"
            )
        first_key = "e2e-fixture-credential-a"
        second_key = "e2e-fixture-credential-b"
        children: list[subprocess.Popen[str]] = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            keyfile = (root / "daemon.key").resolve()
            keyfile.write_text(first_key + "\n", encoding="utf-8")
            port = _unused_port()
            tools_dir = Path(__file__).resolve().parents[1]
            runtime_python = (
                tools_dir / ".venv-mcp" / "Scripts" / "python.exe"
            )
            if not runtime_python.is_file():
                self.skipTest("project MCP runtime is not installed")
            runtime_python = runtime_python.resolve(strict=True)
            config = SimpleNamespace(
                port=port,
                keyfile=str(keyfile),
                idle_timeout_s=0.5,
                expected_game_version=None,
                require_version=False,
                enable_exec_enforce=False,
            )
            argv = build_daemon_argv(config, python=str(runtime_python))
            cwd = str(Path(daemon_runtime_cwd()).resolve(strict=True))
            environment = os.environ.copy()
            environment["LOCALAPPDATA"] = str(root / "local")
            fixture_site = Path(__file__).parent / "fixtures" / "dayz_mcp"
            inherited_pythonpath = environment.get("PYTHONPATH")
            environment["PYTHONPATH"] = str(fixture_site)
            if inherited_pythonpath:
                environment["PYTHONPATH"] += (
                    os.pathsep + inherited_pythonpath
                )
            migration = root / "migration"
            migration.mkdir()
            environment["DAYZ_MCP_FIXTURE_MODE"] = "none"
            environment["DAYZ_MCP_FIXTURE_SIGNAL"] = str(root / "unused")
            environment["DAYZ_MCP_FIXTURE_MIGRATION"] = str(migration)

            def start_daemon() -> subprocess.Popen[str]:
                child = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                children.append(child)
                return child

            try:
                first_daemon = start_daemon()
                first_status = _wait_ready(
                    first_daemon,
                    port=port,
                    key=first_key,
                )
                listener_pid = orphan_guard.listener_pid_for_port(port)
                self.assertIsInstance(listener_pid, int)
                native_executable = orphan_guard.full_image_path_of(
                    listener_pid
                )
                self.assertIsInstance(native_executable, str)
                self.assertEqual(
                    native_process_snapshot.command_argv_of(
                        listener_pid
                    ),
                    argv,
                )
                self.assertTrue(
                    native_process_snapshot.same_path(
                        native_process_snapshot.working_directory_of(
                            listener_pid
                        ),
                        cwd,
                    )
                )
                expected_hashes = identity_hashes(
                    native_executable,
                    argv,
                )
                identity = NativeProcessGuard().snapshot(listener_pid)
                self.assertIs(identity.get("identity_complete"), True)
                self.assertEqual(
                    identity.get("executable_sha256"),
                    expected_hashes["executable_sha256"],
                )
                self.assertEqual(
                    identity.get("command_line_sha256"),
                    expected_hashes["command_line_sha256"],
                )
                connection = http.client.HTTPConnection(
                    "127.0.0.1",
                    port,
                    timeout=2.0,
                )
                try:
                    connection.connect()
                    self.assertEqual(
                        accredited_daemon_transport._connected_server_pid(
                            connection.sock,
                            connections_fn=lambda: (
                                native_process_snapshot.psutil.net_connections(
                                    kind="tcp"
                                )
                            ),
                        ),
                        listener_pid,
                    )
                finally:
                    connection.close()
                policy = _policy(
                    argv=argv,
                    cwd=cwd,
                    native_executable=native_executable,
                    port=port,
                    keyfile=keyfile,
                )
                provider = daemon_credential.RefreshingDaemonCredential(
                    policy=policy
                )
                identity = control_client.ControlIdentity(
                    platform="codex",
                    pid=os.getpid(),
                    ppid=os.getppid(),
                    started_at_utc="2026-07-25T00:00:00Z",
                    session_id="credential-rotation-e2e",
                    task_label="isolated credential rotation",
                )
                client = control_client.ControlClient(
                    policy=policy,
                    identity=identity,
                    credential_provider=provider,
                )
                live_process_pid = os.getpid()
                live_client_id = id(client)
                live_provider_id = id(provider)

                acquired = asyncio.run(
                    client.session_acquire_wait(
                        "credential-rotation-e2e",
                        max_wait_s=3.0,
                    )
                )
                self.assertEqual(acquired["status"], "active")
                generation_a = first_status["daemon_generation"]
                local_state_before = (
                    client.state,
                    client.active_operation_id,
                    client.active_ticket,
                    client.active_lease_token,
                )
                self.assertEqual(local_state_before[0], "ACTIVE")
                self.assertIsInstance(local_state_before[1], str)
                self.assertIsNone(local_state_before[2])
                self.assertIsInstance(local_state_before[3], str)

                _stdout_a, stderr_a = _finish_owned_fixture(first_daemon)
                self.assertEqual(first_daemon.returncode, 0, stderr_a)

                replacement = root / "daemon.next"
                replacement.write_text(second_key + "\n", encoding="utf-8")
                os.replace(replacement, keyfile)
                second_daemon = start_daemon()
                _wait_ready(second_daemon, port=port, key=second_key)

                current = asyncio.run(client.session_status())
                self.assertEqual(os.getpid(), live_process_pid)
                self.assertEqual(id(client), live_client_id)
                self.assertEqual(id(provider), live_provider_id)
                self.assertNotEqual(
                    current["daemon_generation"],
                    generation_a,
                )
                self.assertIsNone(current["owner"])
                self.assertEqual(current["queue"], [])
                self.assertEqual(current["self"]["state"], "none")
                self.assertEqual(current["pending_commands"], 0)
                self.assertEqual(
                    (
                        client.state,
                        client.active_operation_id,
                        client.active_ticket,
                        client.active_lease_token,
                    ),
                    local_state_before,
                )

                reconciled = asyncio.run(client.reconcile_idle_session())
                self.assertEqual(reconciled, {"reconciled": True})
                self.assertEqual(
                    (
                        client.state,
                        client.active_operation_id,
                        client.active_ticket,
                        client.active_lease_token,
                    ),
                    ("CLOSED", None, None, None),
                )

                status_code, status_body = provider.request_with_refresh(
                    method="GET",
                    path="/status",
                    query={},
                    body=None,
                    headers={},
                    deadline=time.monotonic() + 3.0,
                )
                self.assertEqual(status_code, 200)
                public_status = json.loads(status_body.decode("utf-8"))
                recovery = public_status["credential_recovery"]
                self.assertEqual(recovery["recovered_count"], 1)
                self.assertIs(recovery["recent"], True)
                self.assertIsInstance(
                    recovery["last_recovered_age_s"],
                    (int, float),
                )
                serialized_recovery = json.dumps(
                    recovery, separators=(",", ":")
                )
                for secret in (first_key, second_key):
                    self.assertNotIn(secret, repr(provider))
                    self.assertNotIn(secret, repr(provider._snapshot))
                    self.assertNotIn(secret, serialized_recovery)

                _stdout_b, stderr_b = _finish_owned_fixture(second_daemon)
                self.assertEqual(second_daemon.returncode, 0, stderr_b)
            finally:
                for child in children:
                    if child.poll() is None:
                        _finish_owned_fixture(child, timeout=1.0)

    def test_same_stdio_mcp_process_recovers_without_reinitialization(
        self,
    ) -> None:
        if native_process_snapshot.psutil is None:
            self.skipTest(
                "accredited E2E requires the project MCP runtime"
            )
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        first_key = "stdio-e2e-fixture-credential-a"
        second_key = "stdio-e2e-fixture-credential-b"
        children: list[subprocess.Popen[str]] = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            keyfile = (root / "daemon.key").resolve()
            keyfile.write_text(first_key + "\n", encoding="utf-8")
            port = _unused_port()
            tools_dir = Path(__file__).resolve().parents[1]
            runtime_python = (
                tools_dir / ".venv-mcp" / "Scripts" / "python.exe"
            )
            if not runtime_python.is_file():
                self.skipTest("project MCP runtime is not installed")
            runtime_python = runtime_python.resolve(strict=True)
            idle_timeout_s = 2.0
            config = SimpleNamespace(
                port=port,
                keyfile=str(keyfile),
                idle_timeout_s=idle_timeout_s,
                expected_game_version=None,
                require_version=False,
                enable_exec_enforce=False,
            )
            daemon_argv = build_daemon_argv(
                config,
                python=str(runtime_python),
            )
            cwd = str(Path(daemon_runtime_cwd()).resolve(strict=True))
            fixture_site = Path(__file__).parent / "fixtures" / "dayz_mcp"
            migration = root / "migration"
            migration.mkdir()
            profile = root / "profile"
            codex_config = profile / ".codex"
            codex_config.mkdir(parents=True)

            def client_argv(platform: str) -> list[str]:
                return [
                    "-m",
                    "dayz_mcp",
                    "--client",
                    "--port",
                    str(port),
                    "--keyfile",
                    str(keyfile),
                    "--idle-timeout",
                    str(idle_timeout_s),
                    "--no-daemon-autospawn",
                    "--client-platform",
                    platform,
                ]

            (profile / ".claude.json").write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "dayz-mcp": {
                                "type": "stdio",
                                "command": str(runtime_python),
                                "args": client_argv("claude"),
                                "timeout": 604_800_000,
                            }
                        }
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            (codex_config / "config.toml").write_text(
                "\n".join(
                    [
                        "[mcp_servers.dayz-mcp]",
                        f"command = {json.dumps(str(runtime_python))}",
                        f"args = {json.dumps(client_argv('codex'))}",
                        "tool_timeout_sec = 604800",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            environment = os.environ.copy()
            environment["USERPROFILE"] = str(profile)
            environment["LOCALAPPDATA"] = str(root / "local")
            inherited_pythonpath = environment.get("PYTHONPATH")
            environment["PYTHONPATH"] = str(fixture_site)
            if inherited_pythonpath:
                environment["PYTHONPATH"] += (
                    os.pathsep + inherited_pythonpath
                )
            environment["DAYZ_MCP_FIXTURE_MODE"] = "none"
            environment["DAYZ_MCP_FIXTURE_SIGNAL"] = str(root / "unused")
            environment["DAYZ_MCP_FIXTURE_MIGRATION"] = str(migration)

            def start_daemon() -> subprocess.Popen[str]:
                child = subprocess.Popen(
                    daemon_argv,
                    cwd=cwd,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                children.append(child)
                return child

            def client_process_identities() -> set[tuple[int, float]]:
                identities: set[tuple[int, float]] = set()
                parent = native_process_snapshot.psutil.Process(os.getpid())
                for process in parent.children(recursive=True):
                    try:
                        argv = process.cmdline()
                        if (
                            "--client" in argv
                            and str(port) in argv
                            and str(keyfile) in argv
                        ):
                            identities.add(
                                (int(process.pid), float(process.create_time()))
                            )
                    except (
                        native_process_snapshot.psutil.AccessDenied,
                        native_process_snapshot.psutil.NoSuchProcess,
                        native_process_snapshot.psutil.ZombieProcess,
                    ):
                        continue
                return identities

            first_daemon = start_daemon()
            first_status = _wait_ready(
                first_daemon,
                port=port,
                key=first_key,
            )
            second_daemon: subprocess.Popen[str] | None = None
            errlog = tempfile.TemporaryFile(
                mode="w+",
                encoding="utf-8",
            )

            async def exercise_live_mcp_process() -> tuple[
                dict[str, object],
                dict[str, object],
                set[tuple[int, float]],
                set[tuple[int, float]],
            ]:
                nonlocal second_daemon
                parameters = StdioServerParameters(
                    command=str(runtime_python),
                    args=client_argv("codex"),
                    env=environment,
                    cwd=cwd,
                )
                async with stdio_client(
                    parameters,
                    errlog=errlog,
                ) as (read_stream, write_stream):
                    async with ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(seconds=10),
                    ) as session:
                        await session.initialize()
                        before = _tool_payload(
                            await session.call_tool("session_status", {})
                        )
                        process_before = client_process_identities()
                        if not process_before:
                            raise AssertionError(
                                "isolated stdio MCP client process not found"
                            )

                        _stdout_a, stderr_a = await asyncio.to_thread(
                            _finish_owned_fixture,
                            first_daemon,
                        )
                        if first_daemon.returncode != 0:
                            raise AssertionError(stderr_a)
                        replacement = root / "daemon.next"
                        replacement.write_text(
                            second_key + "\n",
                            encoding="utf-8",
                        )
                        os.replace(replacement, keyfile)
                        second_daemon = start_daemon()
                        await asyncio.to_thread(
                            _wait_ready,
                            second_daemon,
                            port=port,
                            key=second_key,
                        )

                        after = _tool_payload(
                            await session.call_tool("session_status", {})
                        )
                        process_after = client_process_identities()
                        return (
                            before,
                            after,
                            process_before,
                            process_after,
                        )

            try:
                (
                    before,
                    after,
                    process_before,
                    process_after,
                ) = asyncio.run(exercise_live_mcp_process())
                self.assertEqual(
                    before["daemon_generation"],
                    first_status["daemon_generation"],
                )
                self.assertNotEqual(
                    after["daemon_generation"],
                    first_status["daemon_generation"],
                )
                self.assertEqual(process_after, process_before)
                self.assertEqual(after["self"]["state"], "none")
                self.assertEqual(after["queue"], [])
                recovered = _raw_status(port, second_key)
                self.assertIsInstance(recovered, dict)
                self.assertEqual(
                    recovered["credential_recovery"]["recovered_count"],
                    1,
                )
                errlog.flush()
                errlog.seek(0)
                exposed = errlog.read()
                for secret in (first_key, second_key):
                    self.assertNotIn(secret, exposed)
            finally:
                for child in children:
                    if child.poll() is None:
                        _finish_owned_fixture(child, timeout=4.0)
                errlog.close()


if __name__ == "__main__":
    unittest.main()
