from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import core, daemon, loopback, orphan_guard
from dayz_mcp.native_process_guard import identity_hashes
from dayz_mcp.server import ServerConfig


IDENTITY = {
    "platform": "codex",
    "pid": 11,
    "ppid": 1,
    "started_at_utc": "2026-07-14T10:00:00Z",
    "session_id": "daemon-test",
    "task_label": "daemon",
}


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


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


class DaemonHttpServer:
    """A daemon-style loopback (version validator + status_provider) on a port."""

    def __init__(self, config: ServerConfig, port: int = 0) -> None:
        self.key = config.key
        self.runtime_dir = TemporaryDirectory()
        with patch.dict(os.environ, {"LOCALAPPDATA": self.runtime_dir.name}), patch.object(
            daemon.orphan_guard,
            "snapshot_retail_processes",
            return_value={"known": True, "processes": []},
        ), patch.object(daemon, "_ensure_identity_migration", return_value=None):
            self.state = daemon.build_server_state(
                config, self.key, activate_coordination=True
            )
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
        self.runtime_dir.cleanup()

    @staticmethod
    def _drain_coordination_workers(timeout: float = 5.0) -> None:
        """Wait for the lease workers that keep writing after a request returns.

        Releasing a lease starts named daemon threads (session_coordination.py:
        2056, 2217, 2318) that append audit records to the runtime directory.
        They are meant to outlive the request; production exits the process
        rather than deleting their working directory, so only a fixture that
        deletes it has to wait for them.
        """
        deadline = time.monotonic() + timeout
        while True:
            workers = [
                thread
                for thread in threading.enumerate()
                if thread.is_alive() and thread.name.startswith("dayz-mcp-")
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


class DaemonEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.servers: list[DaemonHttpServer] = []

    def tearDown(self) -> None:
        for srv in self.servers:
            srv.stop()

    def _daemon(self, **kw) -> DaemonHttpServer:
        srv = DaemonHttpServer(_config(**kw))
        srv.start()
        self.servers.append(srv)
        return srv

    def test_status_endpoint_returns_rich_payload(self) -> None:
        srv = self._daemon()
        status, body = _http(srv.base, "GET", "/status", srv.key)
        self.assertEqual(status, 200)
        self.assertIn("server_peer", body)
        self.assertIn("client_peer", body)
        self.assertEqual(body["server_version"], core.EXPECTED_BRIDGE_VERSION)
        self.assertIn("daemon_generation", body)
        self.assertTrue(hasattr(srv.state, "daemon_generation"))
        self.assertEqual(body["daemon_generation"], srv.state.daemon_generation)
        self.assertIn("coordination", body)
        self.assertIsNotNone(srv.state.coordination)
        self.assertIsNotNone(srv.state.coordination_store)
        self.assertNotIn("lease_token", json.dumps(body, separators=(",", ":")))
        # require_version False + no poll → "legacy" (not blocked).
        self.assertEqual(body["server_peer"]["version_state"], "legacy")

    def test_status_requires_key(self) -> None:
        srv = self._daemon()
        status, body = _http(srv.base, "GET", "/status", None)
        self.assertEqual(status, 401)
        self.assertEqual(body, {"error": "unauthorized"})

    def test_only_authenticated_retry_records_sanitized_recovery(self) -> None:
        srv = self._daemon()
        retry_header = {"X-DayZ-MCP-Credential-Retry": "1"}

        rejected_status, _rejected_body = _http(
            srv.base,
            "GET",
            "/status",
            "wrong-fixture-key",
            extra_headers=retry_header,
        )
        status_before, body_before = _http(
            srv.base,
            "GET",
            "/status",
            srv.key,
        )
        recovered_status, recovered_body = _http(
            srv.base,
            "GET",
            "/status",
            srv.key,
            extra_headers=retry_header,
        )

        self.assertEqual(rejected_status, 401)
        self.assertEqual(status_before, 200)
        self.assertEqual(recovered_status, 200)
        self.assertIn("credential_recovery", body_before)
        self.assertIn("credential_recovery", recovered_body)
        self.assertEqual(
            body_before["credential_recovery"],
            {
                "recovered_count": 0,
                "recent": False,
                "last_recovered_age_s": None,
            },
        )
        recovery = recovered_body["credential_recovery"]
        self.assertEqual(recovery["recovered_count"], 1)
        self.assertIs(recovery["recent"], True)
        self.assertGreaterEqual(recovery["last_recovered_age_s"], 0.0)
        serialized = json.dumps(recovery, separators=(",", ":"))
        self.assertNotIn(str(srv.key), serialized)
        self.assertNotIn("wrong-fixture-key", serialized)
        self.assertNotIn("session", serialized.casefold())
        self.assertNotIn("identity", serialized.casefold())

    def test_credential_retry_does_not_change_active_lease_or_run_owner(
        self,
    ) -> None:
        srv = self._daemon()
        acquire_status, acquired = _http(
            srv.base,
            "POST",
            "/session/acquire",
            srv.key,
            {"identity": IDENTITY, "purpose": "rotation-fixture"},
        )
        self.assertEqual(acquire_status, 200)
        before_status_code, before_status = _http(
            srv.base,
            "POST",
            "/session/status",
            srv.key,
            {"identity": IDENTITY},
        )
        self.assertEqual(before_status_code, 200)

        active_run = {
            "run_id": "run-credential-rotation",
            "owner_session_id": IDENTITY["session_id"],
            "owner_lease_id": acquired["lease_id"],
            "state": "RUNNING_IDLE",
        }

        class Lifecycle:
            def __init__(self) -> None:
                self.calls = 0

            def status(self, _client) -> dict[str, object]:
                self.calls += 1
                return {"runs": [dict(active_run)]}

        lifecycle = Lifecycle()
        srv.state.lifecycle = lifecycle
        run_before = json.dumps(
            active_run, separators=(",", ":"), sort_keys=True
        )
        srv.state.key = "rotated-fixture-key"

        rejected_status, _rejected_body = _http(
            srv.base,
            "POST",
            "/lifecycle/status",
            srv.key,
            {"identity": IDENTITY},
        )
        recovered_status, recovered = _http(
            srv.base,
            "POST",
            "/lifecycle/status",
            srv.state.key,
            {"identity": IDENTITY},
            extra_headers={"X-DayZ-MCP-Credential-Retry": "1"},
        )
        after_status_code, after_status = _http(
            srv.base,
            "POST",
            "/session/status",
            srv.state.key,
            {"identity": IDENTITY},
        )

        self.assertEqual(rejected_status, 401)
        self.assertEqual(recovered_status, 200)
        self.assertEqual(after_status_code, 200)
        self.assertEqual(lifecycle.calls, 1)
        self.assertEqual(recovered["runs"], [active_run])
        self.assertEqual(
            json.dumps(active_run, separators=(",", ":"), sort_keys=True),
            run_before,
        )
        for field in ("queue", "self"):
            self.assertEqual(after_status[field], before_status[field])
        for field in ("state", "lease_id", "purpose", "client"):
            self.assertEqual(
                after_status["owner"][field],
                before_status["owner"][field],
            )
        self.assertGreater(after_status["owner"]["expires_in_s"], 0.0)
        self.assertLessEqual(
            after_status["owner"]["expires_in_s"],
            before_status["owner"]["expires_in_s"],
        )
        self.assertEqual(
            after_status["pending_commands"],
            before_status["pending_commands"],
        )

    def test_enqueue_version_blocked_when_require_version(self) -> None:
        srv = self._daemon(require_version=True)
        status, body = _http(
            srv.base,
            "POST",
            "/enqueue",
            srv.key,
            {"identity": IDENTITY, "cmd": "query_player_state", "args": {}},
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "version_blocked")
        self.assertEqual(body["state"], "legacy_blocked")

    def test_enqueue_ok_when_version_matches(self) -> None:
        srv = self._daemon(require_version=True, expected_game_version="1.29.0")
        _http(srv.base, "GET", "/poll", srv.key, query={"peer": "server", "ver": f"{core.EXPECTED_BRIDGE_VERSION}~1.29.0"})
        status, body = _http(
            srv.base,
            "POST",
            "/enqueue",
            srv.key,
            {"identity": IDENTITY, "cmd": "query_player_state", "args": {}},
        )
        self.assertEqual(status, 200)
        self.assertIn("id", body)

    def test_enqueue_version_mismatch_blocked(self) -> None:
        srv = self._daemon(require_version=True, expected_game_version="1.29.0")
        _http(srv.base, "GET", "/poll", srv.key, query={"peer": "server", "ver": f"{core.EXPECTED_BRIDGE_VERSION}~9.9.9"})
        status, body = _http(
            srv.base,
            "POST",
            "/enqueue",
            srv.key,
            {"identity": IDENTITY, "cmd": "query_player_state", "args": {}},
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["state"], "version_mismatch")

    def test_touch_client_advances_idle_metric(self) -> None:
        srv = self._daemon()
        self.assertIsNone(srv.state.status_snapshot().get("last_client_request_at"))
        _http(srv.base, "GET", "/status", srv.key)
        self.assertIsNotNone(srv.state.status_snapshot().get("last_client_request_at"))


class ProbeStatusHealthyTest(unittest.TestCase):
    EXECUTABLE = r"C:\Python\python.exe"
    ARGV = [
        EXECUTABLE, "-m", "dayz_mcp", "--daemon", "--port", "8765",
    ]
    CWD = r"C:\DayZ_MCP\tools"

    def _probe(self, key: str, request_fn) -> bool:
        return orphan_guard.probe_status_healthy(
            8765,
            key,
            deadline=12.0,
            expected_executable=self.EXECUTABLE,
            expected_argv=list(self.ARGV),
            expected_cwd=self.CWD,
            request_fn=request_fn,
        )

    def setUp(self) -> None:
        self.servers: list[DaemonHttpServer] = []

    def tearDown(self) -> None:
        for srv in self.servers:
            srv.stop()

    def test_healthy_daemon_probes_true_bad_key_false(self) -> None:
        payload = StrictDaemonProbeTest._payload()

        def request(**kwargs: object) -> tuple[int, bytes]:
            status = 200 if kwargs["key"] == "pk" else 401
            return status, json.dumps(payload).encode("utf-8")

        self.assertTrue(self._probe("pk", request))
        self.assertFalse(self._probe("wrong", request))

    def test_no_listener_probes_false(self) -> None:
        def refused(**_kwargs: object) -> tuple[int, bytes]:
            raise ConnectionRefusedError

        self.assertFalse(self._probe("k", refused))

    def test_status_with_many_exited_runs_between_64k_and_transport_limit_is_healthy(self) -> None:
        srv = DaemonHttpServer(_config(key="pk"))
        srv.state.lifecycle = SimpleNamespace(
            public_status=lambda: {
                "runs": [
                    {
                        "run_id": f"exited-{index:04d}",
                        "owner_session_id": None,
                        "owner_lease_id": None,
                        "state": "EXITED",
                        "label": "completed-regression-run",
                        "mod": r"P:\Mods\@StorageMod",
                        "profiles": r"C:\DayZ\profiles",
                        "mission": "dayzOffline.chernarusplus",
                        "processes": [],
                        "launch_operation_id": None,
                        "launch_request_sha256": None,
                        "launch_acknowledged": True,
                    }
                    for index in range(512)
                ],
                "retail_quarantine": False,
            }
        )
        srv.start()
        self.servers.append(srv)
        observed_sizes: list[int] = []

        def request(**kwargs: object) -> tuple[int, bytes]:
            url = (
                f"{srv.base}/status?"
                + urllib.parse.urlencode({"key": str(kwargs["key"])})
            )
            with urllib.request.urlopen(url, timeout=2.0) as response:
                body = response.read(int(kwargs["max_response_bytes"]) + 1)
                if len(body) > int(kwargs["max_response_bytes"]):
                    raise ValueError("daemon_response_too_large")
                observed_sizes.append(len(body))
                return int(response.status), body

        self.assertTrue(self._probe(srv.key, request))
        self.assertEqual(
            orphan_guard.MAX_STATUS_BODY_BYTES,
            orphan_guard.MAX_AUTHENTICATED_RESPONSE_BYTES,
        )
        self.assertGreater(observed_sizes[0], 64 * 1024)
        self.assertLess(observed_sizes[0], 4 * 1024 * 1024)

    def test_status_over_authenticated_transport_limit_is_unhealthy(self) -> None:
        payload = StrictDaemonProbeTest._payload()
        payload["padding"] = "x" * (4 * 1024 * 1024)
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        limits: list[int] = []

        def request(**kwargs: object) -> tuple[int, bytes]:
            limits.append(int(kwargs["max_response_bytes"]))
            return 200, body

        self.assertFalse(self._probe("pk", request))
        self.assertEqual(limits, [orphan_guard.MAX_AUTHENTICATED_RESPONSE_BYTES])

    def test_responsive_probe_treats_foreign_key_as_alive(self) -> None:
        # A daemon answering 401 (wrong key) is NOT healthy to us but IS responsive —
        # the reclaim guard must read it as a live server (B-1) and never kill it.
        srv = DaemonHttpServer(_config(key="pk"))
        srv.start()
        self.servers.append(srv)
        self.assertFalse(
            self._probe("wrong", lambda **_kwargs: (401, b'{}'))
        )
        self.assertTrue(orphan_guard.probe_listener_responsive(srv.port, timeout=2.0))

    def test_responsive_probe_false_when_no_listener(self) -> None:
        port = _free_port()  # nobody is listening here
        self.assertFalse(orphan_guard.probe_listener_responsive(port, timeout=0.5))


class StrictDaemonProbeTest(unittest.TestCase):
    EXECUTABLE = r"C:\Python\python.exe"
    ARGV = [
        EXECUTABLE,
        "-m",
        "dayz_mcp",
        "--daemon",
        "--port",
        "8765",
        "--keyfile",
        r"C:\runtime\key.txt",
    ]

    class Response:
        def __init__(self, payload: object, status: int = 200) -> None:
            self.status = status
            self._body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _limit: int = -1) -> bytes:
            return self._body

    class Guard:
        def __init__(self, snapshot: dict[str, object]) -> None:
            self.snapshot_payload = snapshot
            self.calls: list[int] = []

        def snapshot(self, pid: int) -> dict[str, object]:
            self.calls.append(pid)
            return dict(self.snapshot_payload)

    def _identity(self, argv: list[str] | None = None) -> dict[str, object]:
        return {
            "pid": 4321,
            "creation_time_utc": "2026-07-22T00:00:00.000000Z",
            **identity_hashes(self.EXECUTABLE, argv or self.ARGV),
            "identity_scheme": "psutil-argv-v2",
            "identity_complete": True,
            "exit_code": 0,
        }

    @staticmethod
    def _payload(generation: str = "generation-a") -> dict[str, object]:
        coordination = {"revision": 7}
        return {
            "daemon_generation": generation,
            "coordination": coordination,
            "daemon_status": {
                "schema": "dayz-mcp-daemon-status-v1",
                "product": "dayz_mcp",
                "mode": "daemon",
                "daemon_generation": generation,
                "coordination_revision": 7,
            },
        }

    def _probe(
        self,
        payload: object,
        *,
        expected_generation: str | None = None,
        argv: list[str] | None = None,
        urlopen_calls: list[str] | None = None,
        strict_argv: bool = True,
    ) -> bool:
        calls = urlopen_calls if urlopen_calls is not None else []
        observed_argv = list(argv if argv is not None else self.ARGV)
        expected_argv = self.ARGV if strict_argv else observed_argv

        def request_fn(**kwargs: object) -> tuple[int, bytes]:
            if observed_argv != kwargs["expected_argv"]:
                raise ConnectionError("daemon_identity_unverified")
            calls.append("authenticated-request")
            return 200, json.dumps(payload, separators=(",", ":")).encode("utf-8")

        return orphan_guard.probe_status_healthy(
            8765,
            "secret-do-not-log",
            deadline=10.25,
            expected_generation=expected_generation,
            expected_executable=self.EXECUTABLE,
            expected_argv=list(expected_argv),
            expected_cwd=r"C:\DayZ_MCP\tools",
            request_fn=request_fn,
        )

    def test_foreign_listener_identity_blocks_before_key_request(self) -> None:
        calls: list[str] = []
        foreign = [self.EXECUTABLE, "-m", "http.server", "8765"]

        self.assertFalse(self._probe(self._payload(), argv=foreign, urlopen_calls=calls))
        self.assertEqual(calls, [])

    def test_equivalent_daemon_argv_order_keeps_exact_observed_fingerprint(self) -> None:
        reordered = [
            self.EXECUTABLE,
            "-m",
            "dayz_mcp",
            "--keyfile",
            r"C:\runtime\key.txt",
            "--daemon",
            "--port",
            "8765",
        ]
        self.assertTrue(
            self._probe(self._payload(), argv=reordered, strict_argv=False)
        )

    def test_exact_identity_and_closed_status_schema_are_required(self) -> None:
        self.assertTrue(
            self._probe(self._payload(), expected_generation="generation-a")
        )

        malformed = self._payload()
        malformed["daemon_status"] = {"product": "dayz_mcp", "mode": "daemon"}
        self.assertFalse(self._probe(malformed))

        extra = self._payload()
        extra["daemon_status"] = dict(extra["daemon_status"], unexpected=True)
        self.assertFalse(self._probe(extra))

        inconsistent = self._payload()
        inconsistent["coordination"] = {"revision": 8}
        self.assertFalse(self._probe(inconsistent))

        self.assertFalse(
            self._probe(self._payload("generation-b"), expected_generation="generation-a")
        )

    def test_key_is_not_exposed_by_probe_diagnostics(self) -> None:
        calls: list[str] = []
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertFalse(
                self._probe({"not": "a daemon"}, urlopen_calls=calls)
            )
        self.assertEqual(len(calls), 1)
        self.assertNotIn("secret-do-not-log", stderr.getvalue())

    def test_listener_pid_uses_native_structured_table_without_child(self) -> None:
        connection = SimpleNamespace(
            status="LISTEN",
            laddr=SimpleNamespace(ip="127.0.0.1", port=8765),
            pid=4321,
        )
        native = SimpleNamespace(
            CONN_LISTEN="LISTEN",
            net_connections=lambda *, kind: [connection] if kind == "tcp" else [],
        )
        with (
            patch.object(orphan_guard, "psutil", native),
            patch.object(orphan_guard.subprocess, "run") as child,
        ):
            self.assertEqual(orphan_guard._native_listener_pid_for_port(8765), 4321)
        child.assert_not_called()


class StatusProviderMarkerTest(unittest.TestCase):
    def test_producer_emits_marker_consistent_with_coordination(self) -> None:
        snapshot = {
            "peers": {
                "server": {
                    "last_poll_age_s": None,
                    "queue_depth": 0,
                    "version": None,
                },
                "client": {
                    "last_poll_age_s": None,
                    "queue_depth": 0,
                    "version": None,
                },
            },
            "results_pending": 0,
        }
        state = SimpleNamespace(
            daemon_generation="generation-a",
            coordination=SimpleNamespace(
                snapshot_payload=lambda: {"revision": 7}
            ),
            lifecycle=None,
            status_snapshot=lambda: snapshot,
        )

        payload = daemon.make_status_provider(_config(), state)()

        self.assertEqual(
            payload["daemon_status"],
            {
                "schema": "dayz-mcp-daemon-status-v1",
                "product": "dayz_mcp",
                "mode": "daemon",
                "daemon_generation": "generation-a",
                "coordination_revision": 7,
            },
        )

    def _inert_state(self) -> SimpleNamespace:
        snapshot = {
            "peers": {
                "server": {"last_poll_age_s": None, "queue_depth": 0, "version": None},
                "client": {"last_poll_age_s": None, "queue_depth": 0, "version": None},
            },
            "results_pending": 0,
        }
        return SimpleNamespace(
            daemon_generation="generation-a",
            coordination=SimpleNamespace(snapshot_payload=lambda: {"revision": 7}),
            lifecycle=None,
            status_snapshot=lambda: snapshot,
        )

    def test_edited_unsealed_module_is_reported_stale_until_restart(self) -> None:
        # F2.3, all three steps against REAL files: a warning that only ever
        # appears could be a hardcoded constant, and one that only ever stays
        # absent proves nothing. Uses a temp dir so the project tree is untouched.
        state = self._inert_state()
        with TemporaryDirectory() as directory:
            module = Path(directory) / "loopback.py"
            module.write_text("# fixture\n", encoding="utf-8")
            with patch.object(
                daemon,
                "_loaded_module_files",
                return_value={"loopback.py": str(module)},
            ):
                # 1. daemon boots: nothing edited since load.
                provider = daemon.make_status_provider(_config(), state)
                fresh = provider()
                self.assertEqual(fresh["daemon_modules"]["stale"], [])
                self.assertNotIn("daemon_module_stale", fresh.get("warnings", []))
                self.assertIsNotNone(fresh["daemon_modules"]["daemon_started_at"])

                # 2. the file is edited while the daemon keeps the old copy.
                stamp = module.stat().st_mtime + 120.0
                os.utime(module, (stamp, stamp))
                stale = provider()
                self.assertEqual(stale["daemon_modules"]["stale"], ["loopback.py"])
                self.assertIn("daemon_module_stale", stale["warnings"])
                self.assertEqual(stale["daemon_modules"]["watched_count"], 1)

                # 3. restart: the new provider re-snapshots, warning clears.
                restarted = daemon.make_status_provider(_config(), state)()
                self.assertEqual(restarted["daemon_modules"]["stale"], [])
                self.assertNotIn(
                    "daemon_module_stale", restarted.get("warnings", [])
                )

    def test_missing_watched_module_is_not_reported_stale(self) -> None:
        # Negative control: absent file must not masquerade as an edit.
        state = self._inert_state()
        with TemporaryDirectory() as directory:
            with patch.object(
                daemon,
                "_loaded_module_files",
                return_value={"absent.py": str(Path(directory) / "absent.py")},
            ):
                payload = daemon.make_status_provider(_config(), state)()
        self.assertEqual(payload["daemon_modules"]["stale"], [])
        self.assertNotIn("daemon_module_stale", payload.get("warnings", []))

    def test_watch_set_is_this_process_import_closure(self) -> None:
        # H5 (Grok R21 of F2.3): a hand-written list both lied (naming modules
        # the daemon never imports) and missed (omitting ones it does). The set
        # is now whatever THIS process actually imported.
        # NOTE: asserting server.py is absent would be wrong here -- the test
        # process imports it. The property under test is that the set is derived
        # from sys.modules and every entry is a real file on disk.
        files = daemon._loaded_module_files()

        self.assertIn("core.py", files)
        self.assertIn("loopback.py", files)
        for name, path in files.items():
            self.assertEqual(Path(path).name, name)
            self.assertTrue(Path(path).exists(), path)
        loaded = {
            Path(module.__file__).name
            for name, module in sys.modules.items()
            if name.startswith("dayz_mcp.") and getattr(module, "__file__", None)
        }
        self.assertEqual(set(files), loaded)

    def test_generation_accreditation_has_injectable_bounded_timing(self) -> None:
        now = [10.0]
        sleeps: list[float] = []
        probes: list[float] = []

        def sleep(duration: float) -> None:
            sleeps.append(duration)
            now[0] += duration

        def probe(*_args: object, **kwargs: object) -> bool:
            probes.append(float(kwargs["deadline"]))
            return False

        self.assertFalse(
            daemon._status_accredits_generation(
                8765,
                "fixture-key",
                "generation-a",
                deadline=10.2,
                expected_executable=r"C:\Python\python.exe",
                expected_argv=[
                    r"C:\Python\python.exe", "-m", "dayz_mcp", "--daemon",
                    "--port", "8765",
                ],
                expected_cwd=r"C:\DayZ_MCP\tools",
                probe_fn=probe,
                time_fn=lambda: now[0],
                sleep_fn=sleep,
            )
        )
        self.assertAlmostEqual(now[0], 10.2)
        self.assertTrue(probes)
        self.assertEqual(set(probes), {10.2})
        self.assertTrue(sleeps)

        for invalid in (float("nan"), float("inf"), 0.0, -1.0):
            with self.subTest(invalid=invalid):
                self.assertFalse(
                    daemon._status_accredits_generation(
                        8765,
                        "fixture-key",
                        "generation-a",
                        deadline=invalid,
                        expected_executable=r"C:\Python\python.exe",
                        expected_argv=[
                            r"C:\Python\python.exe", "-m", "dayz_mcp",
                            "--daemon", "--port", "8765",
                        ],
                        expected_cwd=r"C:\DayZ_MCP\tools",
                        probe_fn=lambda *_args, **_kwargs: self.fail(
                            "invalid timeout must not probe"
                        ),
                    )
                )


class ConnectedSocketAuthenticationTest(unittest.TestCase):
    EXECUTABLE = r"C:\Python\python.exe"
    CWD = r"C:\DayZ_MCP\tools"
    ARGV = [
        EXECUTABLE,
        "-m",
        "dayz_mcp",
        "--daemon",
        "--port",
        "8765",
        "--keyfile",
        r"C:\runtime\key.txt",
    ]

    class Socket:
        def getsockname(self):
            return ("127.0.0.1", 50000)

        def getpeername(self):
            return ("127.0.0.1", 8765)

    class Response:
        status = 200

        def read(self, _limit: int = -1) -> bytes:
            return b'{"ok":true}'

    class Connection:
        def __init__(self, events: list[object], sock: object | None = None) -> None:
            self.events = events
            self.sock = None
            self._connected_socket = sock or ConnectedSocketAuthenticationTest.Socket()

        def connect(self) -> None:
            self.events.append("connect")
            self.sock = self._connected_socket

        def request(
            self,
            method: str,
            target: str,
            body: bytes | None = None,
            headers: dict[str, str] | None = None,
        ) -> None:
            self.events.append(("request", method, target, body, headers))

        def getresponse(self):
            self.events.append("response")
            return ConnectedSocketAuthenticationTest.Response()

        def close(self) -> None:
            self.events.append("close")

    class Guard:
        def __init__(self, argv: list[str]) -> None:
            self.snapshot_payload = {
                "pid": 4321,
                "creation_time_utc": "2026-07-22T00:00:00.000000Z",
                **identity_hashes(
                    ConnectedSocketAuthenticationTest.EXECUTABLE, argv
                ),
                "identity_scheme": "psutil-argv-v2",
                "identity_complete": True,
                "exit_code": 0,
            }

        def snapshot(self, _pid: int) -> dict[str, object]:
            return dict(self.snapshot_payload)

    def _connections(self):
        return [
            SimpleNamespace(
                status="ESTABLISHED",
                laddr=SimpleNamespace(ip="127.0.0.1", port=8765),
                raddr=SimpleNamespace(ip="127.0.0.1", port=50000),
                pid=4321,
            )
        ]

    def _request(
        self,
        observed_argv: list[str] | None = None,
        events: list[object] | None = None,
        *,
        socket_object: object | None = None,
        connections_fn=None,
        get_executable=None,
        get_argv=None,
        get_cwd=None,
        guard=None,
    ):
        observed_events = events if events is not None else []
        argv = observed_argv or self.ARGV
        result = orphan_guard.verified_daemon_http_request(
            host="127.0.0.1",
            port=8765,
            key="secret-connected-only",
            method="GET",
            path="/status",
            query={},
            body=None,
            headers={},
            deadline=11.0,
            expected_executable=self.EXECUTABLE,
            expected_argv=list(self.ARGV),
            expected_cwd=self.CWD,
            connection_factory=lambda _host, _port, timeout: self.Connection(
                observed_events, socket_object
            ),
            connections_fn=connections_fn or self._connections,
            get_executable=get_executable or (lambda _pid: self.EXECUTABLE),
            get_argv=get_argv or (lambda _pid: list(argv)),
            get_cwd=get_cwd or (lambda _pid: self.CWD),
            guard=guard or self.Guard(argv),
            time_fn=lambda: 10.0,
        )
        return result, observed_events

    def _assert_fails_before_http(self, **kwargs: object) -> None:
        events: list[object] = []
        with self.assertRaises(ConnectionError):
            self._request(events=events, **kwargs)
        self.assertEqual(events[0], "connect")
        self.assertFalse(
            any(isinstance(event, tuple) and event[0] == "request" for event in events)
        )
        self.assertNotIn("secret-connected-only", repr(events))

    def test_key_is_written_only_after_connected_socket_owner_is_accredited(self) -> None:
        (status, body), events = self._request()

        self.assertEqual((status, body), (200, b'{"ok":true}'))
        self.assertEqual(events[0], "connect")
        request_index = next(
            index
            for index, event in enumerate(events)
            if isinstance(event, tuple) and event[0] == "request"
        )
        self.assertGreater(request_index, 0)
        self.assertIn("key=secret-connected-only", events[request_index][2])

    def test_foreign_connected_owner_receives_zero_http_bytes(self) -> None:
        foreign = [self.EXECUTABLE, "-m", "http.server", "8765"]
        events: list[object] = []
        with self.assertRaises(ConnectionError):
            self._request(foreign, events)
        self.assertFalse(
            any(isinstance(event, tuple) and event[0] == "request" for event in events)
        )

    def test_provenance_arguments_are_mandatory_and_exact(self) -> None:
        events: list[object] = []
        with self.assertRaises((TypeError, ValueError, ConnectionError)):
            orphan_guard.verified_daemon_http_request(
                host="127.0.0.1",
                port=8765,
                key="secret-connected-only",
                method="GET",
                path="/status",
                query={},
                body=None,
                headers={},
                deadline=11.0,
                expected_executable=self.EXECUTABLE,
                expected_argv=None,
                expected_cwd=self.CWD,
                connection_factory=lambda _host, _port, timeout: self.Connection(events),
                connections_fn=self._connections,
                get_executable=lambda _pid: self.EXECUTABLE,
                get_argv=lambda _pid: list(self.ARGV),
                get_cwd=lambda _pid: self.CWD,
                guard=self.Guard(self.ARGV),
                time_fn=lambda: 10.0,
            )
        self.assertFalse(
            any(isinstance(event, tuple) and event[0] == "request" for event in events)
        )

    def test_connection_closed_between_accreditations_receives_zero_http_bytes(self) -> None:
        class ClosedSocket(self.Socket):
            def __init__(self) -> None:
                self.peer_reads = 0

            def getpeername(self):
                self.peer_reads += 1
                if self.peer_reads > 1:
                    raise OSError("closed")
                return super().getpeername()

        self._assert_fails_before_http(socket_object=ClosedSocket())

    def test_connection_rebind_between_accreditations_receives_zero_http_bytes(self) -> None:
        tables = [self._connections(), self._connections(), []]
        self._assert_fails_before_http(connections_fn=lambda: tables.pop(0))

    def test_connected_owner_pid_drift_receives_zero_http_bytes(self) -> None:
        second = self._connections()
        second[0].pid = 9876
        tables = [self._connections(), second]
        self._assert_fails_before_http(connections_fn=lambda: tables.pop(0))

    def test_argv_drift_receives_zero_http_bytes(self) -> None:
        observed = [list(self.ARGV), [self.EXECUTABLE, "-m", "http.server"]]
        self._assert_fails_before_http(get_argv=lambda _pid: observed.pop(0))

    def test_cwd_drift_receives_zero_http_bytes(self) -> None:
        observed = [self.CWD, r"C:\Foreign"]
        self._assert_fails_before_http(get_cwd=lambda _pid: observed.pop(0))

    def test_fingerprint_drift_receives_zero_http_bytes(self) -> None:
        parent = self

        class DriftGuard(self.Guard):
            def __init__(self) -> None:
                super().__init__(parent.ARGV)
                self.reads = 0

            def snapshot(self, _pid: int) -> dict[str, object]:
                self.reads += 1
                payload = dict(self.snapshot_payload)
                if self.reads > 1:
                    payload["creation_time_utc"] = "2026-07-22T00:00:01.000000Z"
                return payload

        self._assert_fails_before_http(guard=DriftGuard())


class IdleMetricTest(unittest.TestCase):
    def test_daemon_idle_includes_client_requests(self) -> None:
        state = loopback.ServerState("k")
        idle_fn = daemon._make_idle_seconds(state, time.monotonic() - 100.0)
        self.assertGreater(idle_fn(), 90.0)  # no game polls, no client requests
        state.touch_client()
        self.assertLess(idle_fn(), 1.0)  # a client request resets the metric


class TryReclaimUnresponsiveTest(unittest.TestCase):
    """The daemon reclaim discriminator is HEALTH, not ancestry: never kill a
    holder that answers /status; reclaim only an unresponsive dayz_mcp orphan."""

    def _reclaim(self, *, healthy, image, cmdline, killed, responsive=False):
        executable = r"C:\Python\python.exe"
        expected_argv = "python -m dayz_mcp --daemon --port 8765".split()
        snapshot = {
            "pid": 4321,
            "creation_time_utc": "2026-07-22T00:00:00.000000Z",
            **identity_hashes(executable, expected_argv),
            "identity_scheme": "psutil-argv-v2",
            "identity_complete": True,
            "exit_code": 0,
        }

        class Guard:
            def snapshot(self, _pid):
                return dict(snapshot)

            def terminate(self, record):
                killed.append(record.pid)
                return {"terminated": True}

        return orphan_guard.try_reclaim_unresponsive_listener(
            8765,
            deadline=11.0,
            is_healthy=healthy if callable(healthy) else lambda: healthy,
            is_responsive=(
                responsive if callable(responsive) else lambda: responsive
            ),
            sleep=lambda _s: None,
            time_fn=lambda: 10.0,
            find_listener=lambda _p: 4321,
            get_image=lambda _p: image,
            get_argv=lambda _p: cmdline.split(),
            guard=Guard(),
            wait_free=lambda _p: True,
            expected_executable=executable,
            expected_argv=expected_argv,
        )

    def test_healthy_holder_is_never_reclaimed(self) -> None:
        killed: list[int] = []
        result = self._reclaim(healthy=True, image="python.exe",
                               cmdline="python -m dayz_mcp --daemon --port 8765", killed=killed)
        self.assertFalse(result)
        self.assertEqual(killed, [])

    def test_unresponsive_dayz_mcp_orphan_is_reclaimed(self) -> None:
        killed: list[int] = []
        result = self._reclaim(healthy=False, image="python.exe",
                               cmdline="python -m dayz_mcp --daemon --port 8765", killed=killed)
        self.assertTrue(result)
        self.assertEqual(killed, [4321])

    def test_unresponsive_non_dayz_holder_is_not_killed(self) -> None:
        killed: list[int] = []
        result = self._reclaim(healthy=False, image="python.exe",
                               cmdline="python -m something_else --port 8765", killed=killed)
        self.assertFalse(result)
        self.assertEqual(killed, [])

    def test_non_python_holder_is_not_killed(self) -> None:
        killed: list[int] = []
        result = self._reclaim(healthy=False, image="node.exe", cmdline="node app.js", killed=killed)
        self.assertFalse(result)
        self.assertEqual(killed, [])

    def test_responsive_foreign_key_holder_is_preserved(self) -> None:
        # B-1: a daemon answering 401 (foreign key) is alive — not_healthy but responsive
        # — and must NOT be killed even though it is a python -m dayz_mcp listener.
        killed: list[int] = []
        result = self._reclaim(healthy=False, responsive=True, image="python.exe",
                               cmdline="python -m dayz_mcp --daemon --port 8765", killed=killed)
        self.assertFalse(result)
        self.assertEqual(killed, [])

    def test_holder_that_becomes_responsive_mid_window_is_preserved(self) -> None:
        # A-1: a freshly-elected winner whose serve loop has not started answering yet
        # flips to responsive within the re-probe window and must NOT be killed.
        killed: list[int] = []
        responses = iter([False, True])
        result = self._reclaim(
            healthy=False,
            responsive=lambda: next(responses),
            image="python.exe",
            cmdline="python -m dayz_mcp --daemon --port 8765",
            killed=killed,
        )
        self.assertFalse(result)
        self.assertEqual(killed, [])


class DaemonCoordinationActivationTest(unittest.TestCase):
    def test_startup_recovery_finishes_unacknowledged_before_exposure(self) -> None:
        from types import SimpleNamespace
        from dayz_mcp.session_coordination import CleanupDisposition

        run = SimpleNamespace(
            run_id="R",
            owner_session_id="S",
            owner_lease_id="L",
            launch_operation_id="OP",
            launch_acknowledged=False,
            state="RUNNING",
        )

        class Manifest:
            def list_runs(self):
                return [run]

        calls: list[tuple[str, str]] = []
        terminal = threading.Event()
        terminal.set()

        class Lifecycle:
            def begin_release_owner(self, session_id, lease_id):
                calls.append((session_id, lease_id))
                return CleanupDisposition(
                    True, terminal, {"terminal_safe": True, "runs_released": ["R"]}
                )

        recovered = daemon.recover_unacknowledged_before_listen(
            Lifecycle(), Manifest(), deadline=time.monotonic() + 1.0
        )
        self.assertEqual(recovered, ["R"])
        self.assertEqual(calls, [("S", "L")])

    def test_cleanup_begin_composes_state_cleanup_into_lifecycle_disposition(self) -> None:
        terminal = threading.Event()
        terminal_result: dict[str, object] = {}

        class State:
            def cleanup_owner(self, session_id, lease_id, reason, vehicle_active):
                return {"cancelled": 2, "vehicle_release_enqueued": int(vehicle_active)}

        class Lifecycle:
            def begin_release_owner(self, session_id, lease_id):
                from dayz_mcp.session_coordination import CleanupDisposition

                return CleanupDisposition(True, terminal, terminal_result)

        disposition = daemon.cleanup_begin(
            State(), Lifecycle(), "S", "L", "owner_release", True
        )
        self.assertTrue(disposition.fence_required)
        self.assertEqual(disposition.terminal_result["cancelled"], 2)
        self.assertEqual(disposition.terminal_result["vehicle_release_enqueued"], 1)

    def test_cleanup_begin_manifest_exception_keeps_release_fenced(self) -> None:
        class State:
            def cleanup_owner(self, *_args):
                return {"cancelled": 1}

        class Lifecycle:
            def begin_release_owner(self, *_args):
                raise ValueError("sensitive manifest detail")

        disposition = daemon.cleanup_begin(
            State(), Lifecycle(), "S", "L", "owner_release", False
        )

        self.assertIsInstance(disposition, daemon.CleanupDisposition)
        self.assertTrue(disposition.fence_required)
        self.assertTrue(disposition.terminal_event.is_set())
        self.assertIs(disposition.terminal_result["terminal_safe"], False)
        self.assertEqual(disposition.terminal_result["error"], "run_manifest_failed")

    def _run_losing_candidate(self, localappdata: str) -> int:
        with (
            patch.dict(os.environ, {"LOCALAPPDATA": localappdata}),
            patch.object(orphan_guard, "probe_status_healthy", return_value=False),
            patch.object(daemon, "_ensure_identity_migration", return_value=None),
            patch.object(daemon, "_bind_with_reclaim", return_value=None),
        ):
            return daemon.run_daemon(_config(port=8765, idle_timeout_s=0.0))

    def test_build_state_can_be_inert_or_explicitly_activated(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "DayZ_MCP"
            with patch.dict(os.environ, {"LOCALAPPDATA": temporary}), patch.object(
                daemon, "_ensure_identity_migration", return_value=None
            ):
                inert = daemon.build_server_state(
                    _config(),
                    "k",
                    daemon_generation="inert-generation",
                    activate_coordination=False,
                )
                self.assertIsNone(inert.coordination)
                self.assertIsNone(inert.coordination_store)
                self.assertFalse(root.exists())

                active = daemon.build_server_state(
                    _config(),
                    "k",
                    daemon_generation="active-generation",
                    activate_coordination=True,
                )
                self.assertIsNotNone(active.coordination)
                self.assertIsNotNone(active.coordination_store)
                self.assertIsNotNone(active.lifecycle_recovery_fault_store)
                self.assertTrue((root / "coordination.json").exists())

    def test_corrupt_manifest_arms_repair_fence_and_restart_does_not_mutate_it(self) -> None:
        from dayz_mcp.session_coordination import ClientIdentity

        identity = ClientIdentity.from_payload(
            {
                "platform": "unknown",
                "pid": 123,
                "ppid": 45,
                "started_at_utc": "2026-07-22T00:00:00Z",
                "session_id": "12345678-1234-4234-8234-1234567890ab",
                "task_label": "manifest-recovery-test",
            }
        )
        with TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"LOCALAPPDATA": temporary}
        ), patch.object(daemon, "_ensure_identity_migration", return_value=None):
            first = daemon.build_server_state(
                _config(),
                "k",
                daemon_generation="manifest-checkpoint-generation",
                activate_coordination=True,
            )
            self.assertIsNotNone(first.lifecycle)
            runs_path = Path(temporary) / "DayZ_MCP" / "runs.json"
            runs_path.write_bytes(b"corrupt-manifest")

            fenced = daemon.build_server_state(
                _config(),
                "k",
                daemon_generation="manifest-fenced-generation",
                activate_coordination=True,
            )
            status = fenced.coordination.status(identity)
            fault = status["lifecycle_recovery_fault"]
            self.assertEqual(fault["fault"]["scope"], "manifest")
            self.assertFalse(status["claimable"])
            self.assertEqual(runs_path.read_bytes(), b"corrupt-manifest")

            restarted = daemon.build_server_state(
                _config(),
                "k",
                daemon_generation="manifest-restart-generation",
                activate_coordination=True,
            )
            self.assertEqual(runs_path.read_bytes(), b"corrupt-manifest")
            restarted_status = restarted.coordination.status(identity)
            self.assertFalse(restarted_status["claimable"])
            queued_status, queued = restarted.coordination.acquire(
                identity, "manifest-repair-wait"
            )
            self.assertEqual(queued_status, 202)
            active_fault = restarted_status["lifecycle_recovery_fault"]
            repair_status, repair = loopback._repair_lifecycle_recovery_fault(
                restarted,
                active_fault["fault"]["fault_id"],
                active_fault["pointer"]["head_event_sha256"],
            )
            self.assertEqual((repair_status, repair["repaired"]), (200, True))
            granted_status, granted = restarted.coordination.wait(
                identity, queued["ticket"], 0.0
            )
            self.assertEqual((granted_status, granted["status"]), (200, "active"))
            self.assertEqual(runs_path.read_bytes(), b'{"version":1,"runs":[]}\n')

    def test_losing_candidate_creates_no_coordination_files(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "DayZ_MCP"
            self.assertEqual(
                self._run_losing_candidate(temporary),
                daemon.DAEMON_STARTUP_CONTENDED,
            )
            self.assertEqual(
                sorted(path.name for path in root.iterdir()),
                [".daemon-startup.lock"],
            )
            self.assertFalse((root / "coordination.json").exists())
            self.assertFalse((root / "runs.json").exists())
            self.assertFalse((root / "audit").exists())

    def test_losing_candidate_leaves_existing_coordination_and_audit_identical(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "DayZ_MCP"
            audit_path = root / "audit" / "events.jsonl"
            coordination_path = root / "coordination.json"
            audit_path.parent.mkdir(parents=True)
            coordination_before = (
                json.dumps(
                    {
                        "daemon_generation": "existing-generation",
                        "revision": 7,
                        "active": None,
                        "releasing": None,
                        "queue": [],
                    },
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
            audit_before = b'{"event":"existing"}\n'
            coordination_path.write_bytes(coordination_before)
            audit_path.write_bytes(audit_before)

            self.assertEqual(
                self._run_losing_candidate(temporary),
                daemon.DAEMON_STARTUP_CONTENDED,
            )
            self.assertEqual(coordination_path.read_bytes(), coordination_before)
            self.assertEqual(audit_path.read_bytes(), audit_before)


class BuildDaemonArgvTest(unittest.TestCase):
    def test_forwards_policy_flags(self) -> None:
        config = ServerConfig(
            mode="client", port=8765, keyfile="K", expected_game_version="1.29.0",
            require_version=True, idle_timeout_s=1800.0, enable_exec_enforce=True, exec_allowlist="A.json",
        )
        argv = daemon.build_daemon_argv(config, python="py.exe")
        self.assertEqual(argv[:5], ["py.exe", "-m", "dayz_mcp", "--daemon", "--port"])
        self.assertIn("--keyfile", argv)
        self.assertIn("--require-version", argv)
        self.assertIn("--expected-game-version", argv)
        self.assertIn("--enable-exec-enforce", argv)
        self.assertIn("--exec-allowlist", argv)


if __name__ == "__main__":
    unittest.main()
