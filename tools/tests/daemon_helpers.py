"""Daemon loopback fixture shared by tests that talk to a real daemon HTTP server.

Moved out of test_daemon.py, with the client transport attach that used to
live on ClientModeTest, so those tests do not import each other
(test_suite_structure).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dayz_mcp import control_client, daemon, loopback, server
from dayz_mcp.server import ServerConfig
from dayz_mcp.session_coordination import ClientIdentity
from tests.fence_helpers import bind_both_peers
from tests.lifecycle_helpers import stamp_launcher


FIXTURE_IDENTITY = ClientIdentity(
    "codex",
    99,
    1,
    "2026-07-14T10:00:00Z",
    "daemon-fixture-holder",
    "fixture-owner",
)


def _http(
    base,
    method,
    path,
    key,
    payload=None,
    query=None,
    timeout=2.0,
    extra_headers=None,
):
    params = dict(query or {})
    if key is not None:
        params["key"] = key
    url = base + path + "?" + urllib.parse.urlencode(params)
    data = None
    headers = dict(extra_headers or {})
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return int(response.status), json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return int(exc.code), json.loads(exc.read().decode("utf-8") or "{}")
        finally:
            exc.close()


def _wait_until_lease_claimable(coordinator, *, timeout_s: float = 2.0) -> dict:
    """Barrier after own-release: HTTP 200 can return while handoff_pending is still true.

    Release only waits RELEASE_AUDIT_TIMEOUT_S (50ms) for the audit worker. Under
    suite load that worker can still be writing when the next acquire runs, which
    then queues (202). Wait on the coordinator condition until the lease is
    claimable and handoff is clear; dump a snapshot if the deadline expires.
    """
    with coordinator._condition:
        ready = coordinator._condition.wait_for(
            lambda: (
                not coordinator._handoff_pending
                and coordinator._claimable_locked()
            ),
            timeout=timeout_s,
        )
    snapshot = coordinator.snapshot_payload()
    if not ready:
        raise AssertionError(
            f"post-release lease not claimable within {timeout_s}s: {snapshot!r}"
        )
    return snapshot


class DaemonHttpServer:
    """A daemon-style loopback (version validator + status_provider) on a port."""

    def __init__(
        self,
        config: ServerConfig,
        port: int = 0,
        *,
        adopt_fixture: bool = False,
    ) -> None:
        self.key = config.key
        self.runtime_dir = TemporaryDirectory()
        self.fixture_identity = FIXTURE_IDENTITY
        self.fixture_lease_token: str | None = None
        self.fixture_lease_id: str | None = None
        with patch.dict(os.environ, {"LOCALAPPDATA": self.runtime_dir.name}), patch.object(
            daemon.orphan_guard,
            "snapshot_retail_processes",
            return_value={"known": True, "processes": []},
        ), patch.object(daemon, "_ensure_identity_migration", return_value=None):
            self.state = daemon.build_server_state(
                config, self.key, activate_coordination=True
            )
        # P-I10: bind never writes an owner. Callers that need a dispatchable
        # run pass adopt_fixture=True: a fixture identity acquires a real
        # lifecycle lease and adopt_run. The constructor default is False so
        # tests outside this write-set that construct DaemonHttpServer() and
        # then acquire (colas / lease election) still see an empty titular.
        # test_daemon._daemon and test_client_mode._daemon default True.
        bind_both_peers(self.state)
        if adopt_fixture:
            self._acquire_and_adopt_fixture()
        provider = daemon.make_status_provider(config, self.state)
        self.httpd = loopback.create_http_server(
            port, self.state, log_sink=lambda _m: None, reclaim_orphans=False, status_provider=provider
        )
        # ThreadingHTTPServer makes request handlers daemon threads
        # (http/server.py:155), and socketserver only tracks non-daemon ones for
        # joining (socketserver.py:649-653), so server_close() would wait for no
        # handler at all. stop() deletes this fixture's runtime directory, so the
        # wait has to be real: a handler still inside the coordination-fault
        # transaction holds an open descriptor on .coordination-fault.json.lock
        # and Windows refuses the unlink.
        self.httpd.daemon_threads = False
        self.port = int(self.httpd.server_address[1])
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        # Swallow the benign serve_forever/server_close teardown race on Windows so
        # test output stays clean (the threading excepthook would print otherwise).
        try:
            self.httpd.serve_forever(poll_interval=0.01)
        except Exception:
            pass

    def _acquire_and_adopt_fixture(self) -> None:
        lifecycle = self.state.lifecycle
        coordinator = lifecycle.coordinator
        status, acquired = coordinator.acquire(self.fixture_identity, "lifecycle")
        if status != 200:
            raise AssertionError(
                f"fixture acquire failed: {status} {acquired!r}"
            )
        self.fixture_lease_token = acquired["lease_token"]
        self.fixture_lease_id = acquired["lease_id"]
        stamp_launcher(lifecycle, "test-run", self.fixture_identity)
        adopted = lifecycle.adopt_run(
            self.fixture_identity, self.fixture_lease_token, "test-run"
        )
        if not isinstance(adopted, dict) or adopted.get("ok") is not True:
            raise AssertionError(f"fixture adopt_run failed: {adopted!r}")
        if adopted.get("dispatchable") is not True:
            raise AssertionError(f"fixture adopt_run not dispatchable: {adopted!r}")

    def release_fixture_owner(self) -> None:
        if self.fixture_lease_token is None:
            return
        status, payload = self.state.lifecycle.coordinator.release(
            self.fixture_identity, self.fixture_lease_token
        )
        if status not in (200, 202):
            raise AssertionError(
                f"fixture release failed: {status} {payload!r}"
            )
        self.fixture_lease_token = None
        self.fixture_lease_id = None
        self._drain_coordination_workers()

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=2.0)
        if self.thread.is_alive():
            raise RuntimeError("daemon fixture serve thread did not stop")
        # Joins the handler threads made joinable in __init__; the directory is
        # only safe to delete once no handler holds a file inside it.
        self.httpd.server_close()
        self._drain_coordination_workers()
        lease = getattr(self.state, "root_writer_lease", None)
        if lease is not None:
            lease.release()
        self.runtime_dir.cleanup()

    @staticmethod
    def _drain_coordination_workers(timeout: float = 5.0) -> None:
        """Wait for the lease workers that keep writing after a request returns.

        Releasing a lease starts named daemon threads (session_coordination.py
        cleanup, fenced-cleanup and release-audit) that append audit records
        to the runtime directory. They are meant to outlive the request;
        production exits the process rather than deleting their working
        directory, so only a fixture that deletes it has to wait for them.
        Supervisor stdout pumps and the recycle thread share the dayz-mcp-
        prefix and block on a pipe; they do not hold this directory.
        """
        deadline = time.monotonic() + timeout
        while True:
            workers = [
                thread
                for thread in threading.enumerate()
                if thread.is_alive()
                and thread.name.startswith("dayz-mcp-")
                and not thread.name.startswith("dayz-mcp-pump-")
                and thread.name != "dayz-mcp-recycle"
            ]
            if not workers:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                names = ", ".join(sorted(thread.name for thread in workers))
                raise RuntimeError(f"coordination workers still running: {names}")
            for thread in workers:
                thread.join(timeout=max(0.0, deadline - time.monotonic()))


def _config(**kw) -> ServerConfig:
    base = dict(mode="daemon", key="dkey", port=0, log_sink=lambda _m: None)
    base.update(kw)
    return ServerConfig(**base)


def _attach_control_transport(runtime: server.ClientRuntime, request) -> None:
    def control_request(path, payload, timeout_s):
        status, response = request(
            "POST", path, payload, None, timeout_s
        )
        if status not in (200, 202):
            raise control_client.ControlClientError(
                server._remote_error_code(response),
                request_stage="post_request",
                http_bytes_sent=1,
            )
        return response

    runtime._control._request_once = control_request


def _attach_fixture_transport(
    runtime: server.ClientRuntime, srv: DaemonHttpServer
) -> None:
    request = lambda method, path, payload=None, query=None, timeout=5.0: _http(
        srv.base,
        method,
        path,
        srv.key,
        payload=payload,
        query=query,
        timeout=timeout,
    )
    runtime._request_once = request
    _attach_control_transport(runtime, request)
