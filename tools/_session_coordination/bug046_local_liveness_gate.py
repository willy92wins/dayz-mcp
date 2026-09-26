"""Local BUG-046 liveness gate with three real client processes.

The retained output is public aggregate evidence only. The temporary daemon key,
lease tokens and complete client identities remain in process memory and the key
is delivered to workers only through their child environment.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import psutil


TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from dayz_mcp import daemon, host_config, orphan_guard  # noqa: E402
from dayz_mcp.server import ClientRuntime, ServerConfig  # noqa: E402


class GateError(RuntimeError):
    pass


def _native_python() -> str:
    executable = orphan_guard.full_image_path_of(os.getpid())
    if not executable or not os.path.isabs(executable):
        raise GateError("native_executable_unavailable")
    return executable


def _child_pythonpath(*leading: Path) -> str:
    site_packages = TOOLS / ".venv-mcp" / "Lib" / "site-packages"
    return os.pathsep.join(str(path) for path in (*leading, TOOLS, site_packages))


def _test_provenance(port: int, keyfile: Path) -> host_config.DaemonProvenance:
    config = ServerConfig(mode="daemon", keyfile=str(keyfile), port=port)
    launch = str(Path(sys.executable).resolve(strict=True))
    return host_config.DaemonProvenance(
        launch_executable=launch,
        native_executable=_native_python(),
        argv=tuple(daemon.build_daemon_argv(config, python=launch)),
        cwd=daemon.daemon_runtime_cwd(),
        port=port,
        keyfile=str(keyfile),
        auto_spawn_daemon=True,
    )


def _require(value: object, code: str) -> None:
    if not value:
        raise GateError(code)


def _write_signal(root: Path, name: str, payload: dict[str, object]) -> None:
    target = root / f"{name}.json"
    temporary = root / f"{name}.{os.getpid()}.next"
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(temporary, target)


async def _wait_signal(root: Path, name: str, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    target = root / f"{name}.json"
    while time.monotonic() < deadline:
        if target.is_file():
            return
        await asyncio.sleep(0.02)
    raise GateError(f"signal_timeout_{name}")


def _public_identity(runtime: ClientRuntime) -> dict[str, object]:
    value = runtime.identity.public_payload()
    return {
        "pid": os.getpid(),
        "platform": value["platform"],
        "session": value["session"],
        "task_label": value["task_label"],
    }


async def _worker(role: str, port: int, signal_root: Path, keyfile: Path) -> None:
    key = os.environ.pop("BUG046_GATE_KEY", "")
    _require(bool(key), "worker_key_missing")
    with patch.object(
        host_config,
        "resolve_daemon_provenance",
        return_value=_test_provenance(port, keyfile),
    ):
        runtime = ClientRuntime(
            ServerConfig(
                mode="client",
                key=key,
                keyfile=str(keyfile),
                port=port,
                client_platform="claude" if role == "B" else "codex",
                task_label=f"bug046-local-{role.lower()}",
                log_sink=lambda _message: None,
            )
        )
    public = _public_identity(runtime)
    if role == "A":
        acquired = await runtime.session_acquire_wait("local-owner-a", 10.0)
        _write_signal(signal_root, "ready-A", public)
        await _wait_signal(signal_root, "release-A")
        await runtime.session_release(acquired["lease_token"])
        _write_signal(signal_root, "released-A", public)
        return
    if role == "B":
        queued = await runtime.session_acquire("local-abandoned-b")
        _require(queued.get("status") == "queued", "b_not_queued")
        _write_signal(signal_root, "ready-B", public | {"position": queued["position"]})
        await _wait_signal(signal_root, "cancel-B")
        cancelled = await runtime.session_cancel(queued["ticket"])
        _require(cancelled.get("cancelled") is True, "b_not_cancelled")
        _write_signal(signal_root, "cancelled-B", public)
        return
    if role == "C":
        acquisition = asyncio.create_task(
            runtime.session_acquire_wait("local-live-c", 15.0)
        )
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            status = await runtime.session_status()
            own = status.get("self")
            if isinstance(own, dict) and own.get("state") == "queued":
                _write_signal(
                    signal_root,
                    "ready-C",
                    public | {"position": own.get("position")},
                )
                break
            await asyncio.sleep(0.02)
        else:
            acquisition.cancel()
            raise GateError("c_not_queued")
        acquired = await acquisition
        _require(acquired.get("status") == "active", "c_not_active")
        _write_signal(signal_root, "acquired-C", public)
        await runtime.session_release(acquired["lease_token"])
        _write_signal(signal_root, "released-C", public)
        return
    raise GateError("bad_worker_role")


class _LocalDaemon:
    def __init__(self, runtime_root: Path, key: str, keyfile: Path) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.port = int(reservation.getsockname()[1])
        self.key = key
        self.config = ServerConfig(
            mode="daemon", keyfile=str(keyfile), port=self.port
        )
        self.executable = _native_python()
        self.argv = daemon.build_daemon_argv(
            self.config, python=sys.executable
        )
        self.cwd = daemon.daemon_runtime_cwd()
        self.env = os.environ.copy()
        self.env["LOCALAPPDATA"] = str(runtime_root)
        fixture_root = TOOLS / "tests" / "fixtures" / "dayz_mcp"
        self.env["PYTHONPATH"] = _child_pythonpath(fixture_root)
        self.env["DAYZ_MCP_FIXTURE_MODE"] = "none"
        self.env["DAYZ_MCP_FIXTURE_SIGNAL"] = str(runtime_root / "fixture.signal")
        self.env["DAYZ_MCP_FIXTURE_MIGRATION"] = str(
            runtime_root / "fixture-migration"
        )
        self.process: subprocess.Popen[str] | None = None
        self.listener_pid: int | None = None

    def start(self) -> None:
        self.process = subprocess.Popen(
            self.argv,
            cwd=self.cwd,
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            shell=False,
        )
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise GateError(f"daemon_exit_{self.process.returncode}")
            if orphan_guard.probe_status_healthy(
                self.port,
                self.key,
                deadline=min(deadline, time.monotonic() + 0.5),
                expected_executable=self.executable,
                expected_argv=self.argv,
                expected_cwd=self.cwd,
            ):
                listener_pid = orphan_guard.listener_pid_for_port(self.port)
                if listener_pid is None:
                    raise GateError("daemon_listener_unavailable")
                self.listener_pid = listener_pid
                return
            time.sleep(0.02)
        raise GateError("daemon_start_timeout")

    def close(self) -> None:
        listener_pid = self.listener_pid
        if (
            listener_pid is not None
            and orphan_guard.listener_pid_for_port(self.port) == listener_pid
            and orphan_guard.full_image_path_of(listener_pid) == self.executable
            and orphan_guard.command_argv_of(listener_pid) == self.argv
            and os.path.normcase(
                os.path.abspath(orphan_guard.working_directory_of(listener_pid) or "")
            )
            == os.path.normcase(os.path.abspath(self.cwd))
        ):
            try:
                listener = psutil.Process(listener_pid)
                listener.terminate()
                try:
                    listener.wait(timeout=5.0)
                except psutil.TimeoutExpired:
                    listener.kill()
                    listener.wait(timeout=5.0)
            except psutil.NoSuchProcess:
                pass
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5.0)


def _read_signal(root: Path, name: str, timeout_s: float = 15.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout_s
    path = root / f"{name}.json"
    while time.monotonic() < deadline:
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            _require(isinstance(value, dict), f"signal_invalid_{name}")
            return value
        time.sleep(0.02)
    raise GateError(f"signal_timeout_{name}")


def _touch_signal(root: Path, name: str) -> None:
    _write_signal(root, name, {"status": "continue"})


def _spawn_worker(
    role: str, port: int, root: Path, key: str, keyfile: Path
) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["BUG046_GATE_KEY"] = key
    env["PYTHONPATH"] = _child_pythonpath()
    return subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            role,
            "--port",
            str(port),
            "--signal-root",
            str(root),
            "--keyfile",
            str(keyfile),
        ],
        cwd=str(TOOLS),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
    )


def _wait_workers(workers: dict[str, subprocess.Popen[str]]) -> None:
    failures: list[str] = []
    for role, process in workers.items():
        try:
            stdout, stderr = process.communicate(timeout=10.0)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.communicate(timeout=5.0)
            failures.append(f"{role}:timeout")
            continue
        if process.returncode != 0 or stdout or stderr:
            failures.append(f"{role}:exit_{process.returncode}")
    if failures:
        raise GateError("worker_failure_" + "_".join(failures))


def _run_parent() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="bug046-local-gate-") as temporary:
        root = Path(temporary)
        runtime_root = root / "runtime"
        signal_root = root / "signals"
        signal_root.mkdir()
        key = secrets.token_urlsafe(32)
        keyfile = root / ".dayz_mcp.key"
        keyfile.write_text(key + "\n", encoding="utf-8")
        local = _LocalDaemon(runtime_root, key, keyfile)
        local.start()
        workers: dict[str, subprocess.Popen[str]] = {}
        with patch.object(
            host_config,
            "resolve_daemon_provenance",
            return_value=_test_provenance(local.port, keyfile),
        ):
            observer = ClientRuntime(
                ServerConfig(
                    mode="client",
                    key=key,
                    keyfile=str(keyfile),
                    port=local.port,
                    client_platform="codex",
                    task_label="bug046-local-observer",
                    log_sink=lambda _message: None,
                )
            )
        try:
            workers["A"] = _spawn_worker(
                "A", local.port, signal_root, key, keyfile
            )
            ready_a = _read_signal(signal_root, "ready-A")
            workers["B"] = _spawn_worker(
                "B", local.port, signal_root, key, keyfile
            )
            ready_b = _read_signal(signal_root, "ready-B")
            workers["C"] = _spawn_worker(
                "C", local.port, signal_root, key, keyfile
            )
            ready_c = _read_signal(signal_root, "ready-C")
            _require(
                len({ready_a["pid"], ready_b["pid"], ready_c["pid"]}) == 3,
                "worker_pids_not_distinct",
            )
            _require(
                len({ready_a["session"], ready_b["session"], ready_c["session"]}) == 3,
                "worker_sessions_not_distinct",
            )
            _require((ready_b.get("position"), ready_c.get("position")) == (1, 2), "fifo_positions_invalid")

            _touch_signal(signal_root, "release-A")
            _read_signal(signal_root, "released-A")
            status_after_release = asyncio.run(observer.session_status())
            _require(status_after_release.get("owner") is None, "blind_grant_after_release")
            queue = status_after_release.get("queue")
            _require(isinstance(queue, list) and len(queue) == 2, "queue_not_preserved")

            _touch_signal(signal_root, "cancel-B")
            _read_signal(signal_root, "cancelled-B")
            _read_signal(signal_root, "acquired-C")
            _read_signal(signal_root, "released-C")
            _wait_workers(workers)

            final = asyncio.run(observer.session_status())
            _require(final.get("owner") is None and final.get("queue") == [], "final_not_clean")
            generation = final.get("daemon_generation")
            _require(isinstance(generation, str) and bool(generation), "generation_missing")
            audit_path = runtime_root / "DayZ_MCP" / "audit" / "events.jsonl"
            events = [json.loads(line) for line in audit_path.read_text().splitlines()]
            fifo_grants = [
                event
                for event in events
                if event.get("event") == "session_granted"
                and event.get("reason") == "fifo_head"
            ]
            b_grants = [
                event
                for event in events
                if event.get("event") == "session_granted"
                and event.get("client", {}).get("session") == ready_b["session"]
            ]
            without_live_wait = [
                event for event in fifo_grants if event.get("source") != "live_wait"
            ]
            _require(
                len(fifo_grants) == 2,
                f"fifo_grant_count_invalid_{len(fifo_grants)}",
            )
            _require(not b_grants, "abandoned_b_was_granted")
            _require(not without_live_wait, "fifo_grant_without_live_wait")
            return {
                "status": "PASS",
                "client_processes": 3,
                "daemon_generation_present": True,
                "fifo_grants": len(fifo_grants),
                "fifo_grants_without_live_wait": 0,
                "abandoned_grants": 0,
                "final_clean": True,
            }
        finally:
            for process in workers.values():
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5.0)
            local.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", choices=("A", "B", "C"))
    parser.add_argument("--port", type=int)
    parser.add_argument("--signal-root", type=Path)
    parser.add_argument("--keyfile", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.worker is not None:
        if args.port is None or args.signal_root is None or args.keyfile is None:
            return 2
        try:
            asyncio.run(
                _worker(args.worker, args.port, args.signal_root, args.keyfile)
            )
        except Exception:
            return 2
        return 0
    try:
        result = _run_parent()
    except Exception as error:
        code = str(error)
        if not code.replace("_", "").isalnum():
            code = "local_gate_failed"
        print(json.dumps({"status": "FAIL", "error": code}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
