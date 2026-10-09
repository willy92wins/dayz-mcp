"""Real ClientRuntime plus HTTP ServerState fixture shared by broker tests.

No DayZ process is started. The accredited transport still uses a real socket
to this process.
"""

from __future__ import annotations

import http.client
import os
import threading
import time
import unittest
from types import SimpleNamespace

import psutil

from dayz_mcp import daemon, loopback, server
from dayz_mcp.accredited_daemon_transport import verified_daemon_http_request
from dayz_mcp.native_process_guard import identity_hashes
from dayz_mcp.server import ServerConfig
from dayz_mcp.session_coordination import SessionCoordinator
from tests.client_helpers import _fixture_client_runtime
from tests.daemon_helpers import _http

_CTIME = "2026-08-18T00:00:00.000000Z"


def release_registry():
    return (
        (
            "vehicle_telemetry",
            "vehicle_release",
            lambda command_id, args: {},
        ),
    )


class RunningPeer:
    state = "RUNNING"


class ProbeLifecycle:
    def __init__(self, run_id: str, on_probe=None) -> None:
        self._run_ids = {run_id}
        self.manifest = self
        self.on_probe = on_probe

    def remember(self, run_id: str) -> None:
        self._run_ids.add(run_id)

    def get(self, run_id: str):
        if run_id in self._run_ids:
            return RunningPeer()
        return None

    def classify_registered_client_liveness(self, run_id, destination):
        if self.on_probe is not None:
            self.on_probe(run_id, destination)
        return "alive"

    def public_status(self) -> dict:
        return {}


class BrokerFixture:
    def __init__(self, test: unittest.TestCase, *, registry=None, coordination=False):
        self.test = test
        self.patches = []
        coordinator = None
        if coordination:
            coordinator = SessionCoordinator(daemon_generation="broker-gen")
        self.state = loopback.ServerState(
            "broker-key",
            coordination=coordinator,
            release_registry=registry,
            time_fn=time.monotonic,
        )
        self.state.daemon_generation = "broker-gen"
        if coordination:
            self.state.retail_probe = lambda: {"known": True, "processes": []}
        # /status as the daemon serves it (core.build_status), not the bare
        # loopback's raw snapshot: a broker client only ever sees the former.
        self.httpd = loopback.create_http_server(
            0,
            self.state,
            log_sink=lambda _message: None,
            reclaim_orphans=False,
            status_provider=daemon.make_status_provider(ServerConfig(), self.state),
        )
        self.httpd.daemon_threads = False
        self.port = int(self.httpd.server_address[1])
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()
        self.runtime = self._runtime()

    def _runtime(self) -> server.ClientRuntime:
        config = ServerConfig(
            mode="client",
            key="broker-key",
            port=self.port,
            log_sink=lambda _message: None,
            auto_spawn_daemon=False,
            client_platform="codex",
        )
        runtime = _fixture_client_runtime(config)
        policy = runtime._credential_provider.policy

        def _stable_authority() -> None:
            policy.__post_init__()

        object.__setattr__(policy, "_revalidation_hook", _stable_authority)
        real = verified_daemon_http_request
        executable = runtime._daemon_executable
        argv = list(runtime._daemon_argv)
        cwd = runtime._daemon_cwd
        hashes = identity_hashes(executable, argv)
        observed: dict[str, object] = {}

        class Guard:
            def snapshot(self, pid: int) -> dict:
                return {
                    "pid": pid,
                    "creation_time_utc": _CTIME,
                    "executable_sha256": hashes["executable_sha256"],
                    "command_line_sha256": hashes["command_line_sha256"],
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": True,
                }

        def connection_factory(host: str, port: int, timeout: float):
            connection = http.client.HTTPConnection(host, port, timeout)
            connect = connection.connect

            def connect_and_note() -> None:
                connect()
                observed["sock"] = connection.sock

            connection.connect = connect_and_note  # type: ignore[method-assign]
            return connection

        def connections_fn():
            sock = observed.get("sock")
            if sock is None:
                return []
            return [
                SimpleNamespace(
                    status=psutil.CONN_ESTABLISHED,
                    laddr=sock.getpeername(),
                    raddr=sock.getsockname(),
                    pid=os.getpid(),
                )
            ]

        def wrapped(**kwargs):
            kwargs["connection_factory"] = connection_factory
            kwargs["connections_fn"] = connections_fn
            kwargs["get_executable"] = lambda _pid: executable
            kwargs["get_argv"] = lambda _pid: list(argv)
            kwargs["get_cwd"] = lambda _pid: cwd
            kwargs["guard"] = Guard()
            return real(**kwargs)

        runtime._credential_provider._request_fn = lambda **kwargs: wrapped(
            time_fn=runtime._time_fn, **kwargs
        )
        return runtime

    def stop(self) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=2)
        self.httpd.server_close()

    def bind(self, instance: str, role: str, pid: int, run_id: str = "run-broker") -> str:
        self.state.install_bound_peer(
            instance=instance,
            role=role,
            pid=pid,
            run_id=run_id,
            creation_time_utc=_CTIME,
        )
        token = self.state.bound_instance_token(run_id, role)
        self.test.assertIsInstance(token, str)
        return token

    def http_abandon(self, identity: dict, generation: str, command_id: int, reason: str, token=None):
        body = {
            "identity": identity,
            "generation": generation,
            "id": command_id,
            "reason": reason,
        }
        if token is not None:
            body["lease_token"] = token
        return _http(
            f"http://127.0.0.1:{self.port}",
            "POST",
            "/abandon",
            "broker-key",
            payload=body,
        )
