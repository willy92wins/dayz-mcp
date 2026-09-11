"""Startup observability of the daemon: which spawn branch it took, which Job
Object it lives in, and why it stopped (ficha fb-20260901-225325-76dd).

The ficha blames the idle watchdog for losing live runs. It cannot be that:
compute_idle_seconds credits both peer polls, so a live game keeps the daemon
awake. The open question is whether the daemon dies WITH the session that spawned
it (Job Object with KILL_ON_JOB_CLOSE) or after the game already died -- and the
line that would answer it, SPAWN:, is written by the PARENT to a stderr nobody
keeps, while the child's own stdout/stderr go to DEVNULL. These tests pin the
durable record that makes the question answerable, and pin that a broken audit
sink degrades the record instead of the daemon.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import daemon
from dayz_mcp.runtime_state import JsonlAuditWriter, RuntimePaths
from dayz_mcp.server import ServerConfig


_WINDOWS_ONLY = "the two-branch spawn and the job-object query are the Windows path"


def _config(**kw) -> ServerConfig:
    base = dict(mode="daemon", key="dkey", port=0, log_sink=lambda _m: None)
    base.update(kw)
    return ServerConfig(**base)


def _audit_rows(localappdata: str) -> list[dict]:
    path = Path(localappdata) / "DayZ_MCP" / "audit" / "events.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class _FakePopen:
    def __init__(self, pid: int) -> None:
        self.pid = pid


class _FakeHttpd:
    def __init__(self, port: int) -> None:
        self.server_address = ("127.0.0.1", port)
        self.shutdowns = 0

    def serve_forever(self, poll_interval: float = 0.1) -> None:
        return None

    def shutdown(self) -> None:
        self.shutdowns += 1

    def server_close(self) -> None:
        return None


class _InterruptingStop:
    """A stop event whose wait() is interrupted, to reach run_daemon's SIGINT arm."""

    def __init__(self) -> None:
        self.set_calls = 0

    def set(self) -> None:
        self.set_calls += 1

    def is_set(self) -> bool:
        return self.set_calls > 0

    def wait(self, timeout: float | None = None) -> bool:
        raise KeyboardInterrupt


# ---------------------------------------------------------------------------
# P-76.2 -- the parent knows the branch; the marker is how the child learns it.
# ---------------------------------------------------------------------------


class SpawnMarkerTest(unittest.TestCase):
    def _marker_of(self, kwargs: dict) -> str:
        env = kwargs.get("env")
        self.assertIsInstance(env, dict, "the child must be launched with an explicit env")
        return env[daemon.DAEMON_SPAWN_MARKER_ENV]

    def test_spawn_detached_marks_the_child_with_the_branch_it_took(self) -> None:
        seen: list[dict] = []

        def fake_popen(_argv, **kwargs):
            seen.append(kwargs)
            return _FakePopen(4242)

        with patch.object(daemon.subprocess, "Popen", side_effect=fake_popen):
            pid = daemon.spawn_detached([sys.executable, "-m", "dayz_mcp"], cwd=None)

        self.assertEqual(pid, 4242)
        self.assertEqual(len(seen), 1)
        expected_branch = (
            daemon.SPAWN_BRANCH_BREAKAWAY_OK
            if sys.platform == "win32"
            else daemon.SPAWN_BRANCH_POSIX_NEW_SESSION
        )
        self.assertEqual(
            self._marker_of(seen[0]), f"{expected_branch}:{os.getpid()}"
        )

    @unittest.skipUnless(sys.platform == "win32", _WINDOWS_ONLY)
    def test_breakaway_denied_marks_the_child_job_bound(self) -> None:
        seen: list[dict] = []

        def fake_popen(_argv, **kwargs):
            seen.append(kwargs)
            if int(kwargs.get("creationflags", 0)) & daemon._CREATE_BREAKAWAY_FROM_JOB:
                raise OSError(87, "breakaway denied by the job")
            return _FakePopen(77)

        with patch.object(daemon.subprocess, "Popen", side_effect=fake_popen):
            pid = daemon.spawn_detached([sys.executable], cwd=None)

        self.assertEqual(pid, 77)
        self.assertEqual(len(seen), 2)
        self.assertEqual(
            self._marker_of(seen[0]),
            f"{daemon.SPAWN_BRANCH_BREAKAWAY_OK}:{os.getpid()}",
        )
        self.assertEqual(
            self._marker_of(seen[1]),
            f"{daemon.SPAWN_BRANCH_JOB_BOUND}:{os.getpid()}",
        )
        # Both attempts stay windowless, preserving the process group and the
        # breakaway-first, job-bound-fallback order.
        self.assertEqual(
            int(seen[0]["creationflags"]),
            daemon._CREATE_NO_WINDOW
            | daemon._CREATE_NEW_PROCESS_GROUP
            | daemon._CREATE_BREAKAWAY_FROM_JOB,
        )
        self.assertEqual(
            int(seen[1]["creationflags"]),
            daemon._CREATE_NO_WINDOW | daemon._CREATE_NEW_PROCESS_GROUP,
        )

    def test_the_windowless_flag_is_the_documented_constant_and_stands_alone(self) -> None:
        """The assertions above compare symbol to symbol and would pass on any value.

        This one pins what the fix is actually about. Windows ignores CREATE_NO_WINDOW
        when DETACHED_PROCESS is also set, so a launch that ORs them is a launch with no
        console at all -- and under the venv redirector that is what hands the daemon a
        visible console window it can be killed by.
        """
        self.assertEqual(daemon._CREATE_NO_WINDOW, 0x08000000)
        self.assertEqual(daemon._CREATE_NEW_PROCESS_GROUP, 0x00000200)
        self.assertEqual(daemon._CREATE_BREAKAWAY_FROM_JOB, 0x01000000)
        self.assertFalse(
            hasattr(daemon, "_DETACHED_PROCESS"),
            "DETACHED_PROCESS is back; combined with CREATE_NO_WINDOW it wins and the "
            "stray-console defect returns",
        )

    @unittest.skipUnless(sys.platform == "win32", _WINDOWS_ONLY)
    def test_both_launch_attempts_failing_still_returns_none(self) -> None:
        with patch.object(
            daemon.subprocess, "Popen", side_effect=OSError(5, "denied")
        ):
            self.assertIsNone(daemon.spawn_detached([sys.executable], cwd=None))

    def test_child_env_is_the_parent_environment_plus_the_marker(self) -> None:
        seen: list[dict] = []

        def fake_popen(_argv, **kwargs):
            seen.append(kwargs)
            return _FakePopen(1)

        with patch.object(daemon.subprocess, "Popen", side_effect=fake_popen):
            daemon.spawn_detached([sys.executable], cwd=None)

        env = dict(seen[0]["env"])
        env.pop(daemon.DAEMON_SPAWN_MARKER_ENV)
        inherited = dict(os.environ)
        inherited.pop(daemon.DAEMON_SPAWN_MARKER_ENV, None)
        self.assertEqual(env, inherited)


class ReadSpawnBranchTest(unittest.TestCase):
    def test_marker_from_this_processs_own_parent_is_accepted(self) -> None:
        env = {daemon.DAEMON_SPAWN_MARKER_ENV: f"{daemon.SPAWN_BRANCH_BREAKAWAY_OK}:11"}
        self.assertEqual(
            daemon.read_spawn_branch(env=env, ppid=11),
            daemon.SPAWN_BRANCH_BREAKAWAY_OK,
        )

    def test_marker_left_by_a_grandparent_reads_as_stale(self) -> None:
        env = {daemon.DAEMON_SPAWN_MARKER_ENV: f"{daemon.SPAWN_BRANCH_JOB_BOUND}:11"}
        self.assertEqual(
            daemon.read_spawn_branch(env=env, ppid=12),
            daemon.SPAWN_BRANCH_STALE_MARKER,
        )

    def test_absent_marker_reads_as_no_marker(self) -> None:
        self.assertEqual(
            daemon.read_spawn_branch(env={}, ppid=11), daemon.SPAWN_BRANCH_NO_MARKER
        )

    def test_unknown_branch_token_is_refused(self) -> None:
        env = {daemon.DAEMON_SPAWN_MARKER_ENV: "invented_branch:11"}
        self.assertEqual(
            daemon.read_spawn_branch(env=env, ppid=11), daemon.SPAWN_BRANCH_STALE_MARKER
        )

    def test_malformed_marker_is_refused_without_raising(self) -> None:
        for value in ("", "no-colon", "breakaway_ok:", ":11", "breakaway_ok:abc"):
            with self.subTest(marker=value):
                self.assertEqual(
                    daemon.read_spawn_branch(
                        env={daemon.DAEMON_SPAWN_MARKER_ENV: value}, ppid=11
                    ),
                    daemon.SPAWN_BRANCH_STALE_MARKER
                    if value
                    else daemon.SPAWN_BRANCH_NO_MARKER,
                )


# ---------------------------------------------------------------------------
# The job-object reading: pure, so every branch is testable off the kernel.
# ---------------------------------------------------------------------------


class JobObjectReadingTest(unittest.TestCase):
    def test_a_process_outside_any_job_cannot_be_killed_by_one(self) -> None:
        """The only conclusive survival answer: no job, no job to close."""
        reading = daemon.describe_job_object(
            {"platform": "win32", "api": "available", "in_job": False}
        )
        self.assertEqual(reading["verdict"], "no_job")
        self.assertIs(reading["in_job"], False)
        self.assertIs(reading["queried_job_kill_on_close"], False)
        self.assertEqual(reading["other_jobs_risk"], "none")

    def test_the_kill_bit_is_the_only_conclusive_death_signal(self) -> None:
        reading = daemon.describe_job_object(
            {
                "platform": "win32",
                "api": "available",
                "in_job": True,
                "limit_flags": 0x00002000,
            }
        )
        self.assertEqual(reading["verdict"], "job_bound_kill_on_close")
        self.assertIs(reading["queried_job_kill_on_close"], True)
        self.assertIs(reading["breakaway_ok"], False)
        self.assertEqual(reading["limit_flags"], "0x00002000")
        self.assertEqual(reading["other_jobs_risk"], "unknown")

    def test_a_job_without_the_kill_bit_is_indeterminate_not_a_survival(self) -> None:
        """Refuted in round 1 with two REAL nested jobs: a child whose queried
        job carried no kill bit read as "survives" and died 3 ms after the OUTER
        job closed. QueryInformationJobObject answers about ONE job of the chain
        and gives no way to name which -- measured three deep, it answered about
        the outermost -- so the absence of the bit is an unknown, never a
        survival claim."""
        reading = daemon.describe_job_object(
            {
                "platform": "win32",
                "api": "available",
                "in_job": True,
                "limit_flags": 0x00000800,
            }
        )
        self.assertEqual(reading["verdict"], "job_bound_ancestors_unknown")
        self.assertNotIn("surviv", str(reading["verdict"]))
        self.assertIs(reading["queried_job_kill_on_close"], False)
        self.assertEqual(reading["other_jobs_risk"], "unknown")
        self.assertIs(reading["breakaway_ok"], True)
        self.assertIs(reading["silent_breakaway_ok"], False)

    def test_a_failed_query_refuses_a_verdict_and_keeps_its_win32_error(self) -> None:
        reading = daemon.describe_job_object(
            {
                "platform": "win32",
                "api": "available",
                "in_job": True,
                "query_information_job_object_error": 5,
            }
        )
        self.assertEqual(reading["verdict"], "query_failed")
        self.assertIsNone(reading["queried_job_kill_on_close"])
        self.assertIsNone(reading["limit_flags"])
        self.assertEqual(reading["other_jobs_risk"], "unknown")
        self.assertEqual(reading["win32_error"]["query_information_job_object"], 5)

    def test_a_failed_membership_probe_keeps_its_win32_error(self) -> None:
        reading = daemon.describe_job_object(
            {"platform": "win32", "api": "available", "is_process_in_job_error": 6}
        )
        self.assertEqual(reading["verdict"], "is_process_in_job_failed")
        self.assertIsNone(reading["in_job"])
        self.assertEqual(reading["win32_error"]["is_process_in_job"], 6)

    def test_a_platform_without_the_api_says_so(self) -> None:
        reading = daemon.describe_job_object({"platform": "linux", "api": "unavailable"})
        self.assertEqual(reading["verdict"], "api_unavailable")
        self.assertIsNone(reading["in_job"])

    def test_a_malformed_observation_never_raises(self) -> None:
        for raw in ({}, {"api": "available", "in_job": "yes"}, {"limit_flags": "x"}):
            with self.subTest(raw=raw):
                reading = daemon.describe_job_object(raw)
                self.assertIn("verdict", reading)

    @unittest.skipUnless(sys.platform == "win32", _WINDOWS_ONLY)
    def test_the_thin_win32_layer_answers_about_this_very_process(self) -> None:
        """Positive control: the ctypes bindings run against the live kernel32 and
        produce one of the declared verdicts instead of raising."""
        raw = daemon._job_object_raw()
        self.assertEqual(raw.get("platform"), "win32")
        reading = daemon.describe_job_object(raw)
        self.assertIn(
            reading["verdict"],
            {
                "no_job",
                "job_bound_kill_on_close",
                "job_bound_ancestors_unknown",
                "query_failed",
                "is_process_in_job_failed",
                "api_unavailable",
            },
        )
        json.dumps(reading)  # the reading has to survive the audit serializer


# ---------------------------------------------------------------------------
# The events themselves, and the promise that writing one is never fatal.
# ---------------------------------------------------------------------------


class DaemonEventRecordTest(unittest.TestCase):
    def test_started_event_carries_generation_pid_branch_job_and_parent_death(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {"LOCALAPPDATA": temporary}):
                writer = JsonlAuditWriter(RuntimePaths.from_env(), "gen76dd")
                event = daemon.build_daemon_started_event(
                    pid=4242,
                    ppid=11,
                    parent_image="node.exe",
                    spawn_branch=daemon.SPAWN_BRANCH_JOB_BOUND,
                    job=daemon.describe_job_object(
                        {
                            "platform": "win32",
                            "api": "available",
                            "in_job": True,
                            "limit_flags": 0x00002000,
                        }
                    ),
                    parent_death_armed=daemon.DAEMON_PARENT_DEATH_ARMED,
                    idle_timeout_s=3600.0,
                    listen_port=8765,
                )
                self.assertTrue(daemon.record_daemon_event(event, writer=writer))
                rows = _audit_rows(temporary)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["event"], "daemon_started")
        self.assertEqual(row["daemon_generation"], "gen76dd")
        self.assertTrue(str(row["timestamp_utc"]).endswith("Z"))
        self.assertEqual(row["pid"], 4242)
        self.assertEqual(row["ppid"], 11)
        self.assertEqual(row["parent_image"], "node.exe")
        self.assertEqual(row["spawn_branch"], daemon.SPAWN_BRANCH_JOB_BOUND)
        self.assertEqual(row["job"]["verdict"], "job_bound_kill_on_close")
        self.assertEqual(row["job"]["limit_flags"], "0x00002000")
        self.assertEqual(row["job"]["other_jobs_risk"], "unknown")
        self.assertIs(row["parent_death_armed"], False)
        self.assertEqual(row["idle_timeout_s"], 3600.0)
        self.assertEqual(row["listen_port"], 8765)

    def test_stopping_event_declares_the_reason_the_code_knows(self) -> None:
        for reason in ("idle", "keyboard_interrupt"):
            with self.subTest(reason=reason):
                event = daemon.build_daemon_stopping_event(
                    reason=reason, pid=7, uptime_s=12.5
                )
                self.assertEqual(event["event"], "daemon_stopping")
                self.assertEqual(event["reason"], reason)
                self.assertEqual(event["duration_s"], 12.5)
                self.assertEqual(event["pid"], 7)

    def test_a_broken_audit_sink_is_reported_not_raised(self) -> None:
        class Exploding:
            def write(self, _event):
                raise OSError(5, "the audit directory is read-only")

        logged: list[str] = []
        self.assertFalse(
            daemon.record_daemon_event(
                daemon.build_daemon_stopping_event(reason="idle", pid=1, uptime_s=0.0),
                writer=Exploding(),
                log=logged.append,
            )
        )
        self.assertTrue(logged, "a swallowed audit failure still has to be logged")

    def test_a_missing_runtime_root_is_reported_not_raised(self) -> None:
        with patch.dict(os.environ, {"LOCALAPPDATA": ""}):
            self.assertFalse(
                daemon.record_daemon_event(
                    daemon.build_daemon_stopping_event(
                        reason="idle", pid=1, uptime_s=0.0
                    ),
                    daemon_generation="gen76dd",
                )
            )

    def test_a_held_writer_lock_does_not_hold_up_the_caller(self) -> None:
        """F-03: swallowing exceptions is not enough. The audit writer serialises
        every producer of this process behind ONE lock with no timeout, so the
        append has to be bounded in TIME as well as in errors."""
        with TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {"LOCALAPPDATA": temporary}):
                writer = JsonlAuditWriter(RuntimePaths.from_env(), "gen76dd")
                event = daemon.build_daemon_stopping_event(
                    reason="idle", pid=1, uptime_s=0.0
                )
                writer._lock.acquire()
                try:
                    started = time.monotonic()
                    landed = daemon.record_daemon_event(
                        event, writer=writer, budget_s=0.05
                    )
                    waited = time.monotonic() - started
                finally:
                    writer._lock.release()

        self.assertFalse(landed, "a row that did not land inside its budget is False")
        self.assertLess(waited, 1.0, "the caller waited on a lock it cannot bound")

    def test_a_rejected_payload_is_reported_not_raised(self) -> None:
        with TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {"LOCALAPPDATA": temporary}):
                writer = JsonlAuditWriter(RuntimePaths.from_env(), "gen76dd")
                # No reason/duration_s: JsonlAuditWriter rejects it by contract.
                self.assertFalse(
                    daemon.record_daemon_event({"event": "daemon_started"}, writer=writer)
                )
                self.assertEqual(_audit_rows(temporary), [])


# ---------------------------------------------------------------------------
# The real consumer: run_daemon, from LISTEN to shutdown.
# ---------------------------------------------------------------------------


class DaemonStartupWiringTest(unittest.TestCase):
    def _drive(
        self,
        temporary: str,
        *,
        idle_timeout_s: float,
        stop: object | None = None,
        writer: object | None = None,
        marker: str | None = None,
    ) -> tuple[int, _FakeHttpd, list[str]]:
        httpd = _FakeHttpd(8765)
        generations: list[str] = []

        def activate(state, daemon_generation, **_kwargs):
            generations.append(daemon_generation)
            state.audit_writer = (
                writer
                if writer is not None
                else JsonlAuditWriter(RuntimePaths.from_env(), daemon_generation)
            )
            state.lifecycle = None

        environment = {"LOCALAPPDATA": temporary}
        if marker is not None:
            environment[daemon.DAEMON_SPAWN_MARKER_ENV] = marker

        kwargs = {} if stop is None else {"stop": stop}
        with (
            patch.dict(os.environ, environment),
            patch.object(daemon.orphan_guard, "probe_status_healthy", return_value=False),
            patch.object(
                daemon, "_ensure_identity_migration_after_candidate_drain",
                return_value=None,
            ),
            patch.object(daemon, "_bind_with_reclaim", return_value=httpd),
            patch.object(daemon, "_activate_server_coordination", activate),
            patch.object(daemon, "_status_accredits_generation", return_value=True),
            patch.object(daemon, "_make_idle_seconds", return_value=lambda: 1e9),
        ):
            code = daemon.run_daemon(
                _config(port=8765, idle_timeout_s=idle_timeout_s), **kwargs
            )
        return code, httpd, generations

    def test_run_daemon_records_a_started_event_with_the_spawn_branch(self) -> None:
        with TemporaryDirectory() as temporary:
            code, _httpd, generations = self._drive(
                temporary,
                idle_timeout_s=1.0,
                marker=f"{daemon.SPAWN_BRANCH_JOB_BOUND}:{os.getppid()}",
            )
            rows = _audit_rows(temporary)

        self.assertEqual(code, 0)
        started = [row for row in rows if row.get("event") == "daemon_started"]
        self.assertEqual(len(started), 1, "exactly one started event per live daemon")
        row = started[0]
        self.assertEqual(row["daemon_generation"], generations[0])
        self.assertEqual(row["pid"], os.getpid())
        self.assertEqual(row["ppid"], os.getppid())
        self.assertEqual(row["listen_port"], 8765)
        self.assertEqual(row["idle_timeout_s"], 1.0)
        self.assertIs(row["parent_death_armed"], False)
        self.assertIn("verdict", row["job"])
        self.assertEqual(row["spawn_branch"], daemon.SPAWN_BRANCH_JOB_BOUND)

    def test_a_marker_planted_by_anyone_but_the_parent_is_refused(self) -> None:
        with TemporaryDirectory() as temporary:
            self._drive(
                temporary,
                idle_timeout_s=1.0,
                marker=f"{daemon.SPAWN_BRANCH_BREAKAWAY_OK}:{os.getpid()}",
            )
            rows = _audit_rows(temporary)

        started = [row for row in rows if row.get("event") == "daemon_started"]
        self.assertEqual(len(started), 1)
        # This very process planted the marker, and it is NOT the daemon's parent.
        self.assertEqual(
            started[0]["spawn_branch"], daemon.SPAWN_BRANCH_STALE_MARKER
        )

    def test_a_daemon_nobody_marked_says_so(self) -> None:
        with TemporaryDirectory() as temporary:
            self._drive(temporary, idle_timeout_s=1.0)
            rows = _audit_rows(temporary)

        started = [row for row in rows if row.get("event") == "daemon_started"]
        self.assertEqual(len(started), 1)
        self.assertEqual(
            started[0]["spawn_branch"], daemon.SPAWN_BRANCH_NO_MARKER
        )

    def test_run_daemon_records_the_idle_shutdown_with_its_reason(self) -> None:
        with TemporaryDirectory() as temporary:
            code, httpd, _generations = self._drive(temporary, idle_timeout_s=1.0)
            rows = _audit_rows(temporary)

        self.assertEqual(code, 0)
        self.assertGreaterEqual(httpd.shutdowns, 1)
        stopping = [row for row in rows if row.get("event") == "daemon_stopping"]
        self.assertEqual(len(stopping), 1)
        self.assertEqual(stopping[0]["reason"], "idle")
        self.assertGreaterEqual(stopping[0]["duration_s"], 0.0)
        self.assertEqual([row["event"] for row in rows][-2:], ["daemon_started", "daemon_stopping"])

    def test_run_daemon_records_a_signal_shutdown_as_such(self) -> None:
        with TemporaryDirectory() as temporary:
            code, _httpd, _generations = self._drive(
                temporary, idle_timeout_s=0.0, stop=_InterruptingStop()
            )
            rows = _audit_rows(temporary)

        self.assertEqual(code, 130)
        stopping = [row for row in rows if row.get("event") == "daemon_stopping"]
        self.assertEqual(len(stopping), 1)
        self.assertEqual(stopping[0]["reason"], "keyboard_interrupt")

    def test_run_daemon_starts_when_the_audit_sink_raises_on_every_write(self) -> None:
        class Exploding:
            def __init__(self) -> None:
                self.calls = 0

            def write(self, _event):
                self.calls += 1
                raise OSError(5, "the audit directory is read-only")

        sink = Exploding()
        with TemporaryDirectory() as temporary:
            code, httpd, _generations = self._drive(
                temporary, idle_timeout_s=1.0, writer=sink
            )

        self.assertEqual(code, 0, "a broken audit sink must not change the exit code")
        self.assertGreaterEqual(httpd.shutdowns, 1)
        self.assertGreaterEqual(sink.calls, 2, "both events were attempted")

    def test_run_daemon_reaches_its_state_with_a_blocked_audit_sink(self) -> None:
        """F-03 on the two critical paths: a sink that never returns must not
        delay the watchdog, nor leave the port closed with the process parked in
        stop.wait()."""
        gate = threading.Event()

        class Blocking:
            def __init__(self) -> None:
                self.calls = 0

            def write(self, _event):
                self.calls += 1
                gate.wait(10.0)
                return True

        sink = Blocking()
        try:
            with TemporaryDirectory() as temporary:
                with patch.object(daemon, "DAEMON_EVENT_WRITE_BUDGET_S", 0.05):
                    started = time.monotonic()
                    code, httpd, _generations = self._drive(
                        temporary, idle_timeout_s=1.0, writer=sink
                    )
                    elapsed = time.monotonic() - started
        finally:
            gate.set()

        self.assertEqual(code, 0)
        self.assertGreaterEqual(httpd.shutdowns, 1)
        self.assertGreaterEqual(sink.calls, 2, "both appends were attempted")
        self.assertLess(elapsed, 5.0, "the blocked appends serialised the daemon")

    def test_a_stop_signalled_from_outside_still_attempts_one_row(self) -> None:
        """F-04: every ORDERLY exit ATTEMPTS exactly one stopping row -- here,
        with a working sink, it lands. It is an attempt and not a guarantee: a
        process that exits while the audit lock is held drops the worker and
        leaves none, so a missing row means "no orderly shutdown was RECORDED"."""
        stop = threading.Event()
        stop.set()
        with TemporaryDirectory() as temporary:
            code, _httpd, _generations = self._drive(
                temporary, idle_timeout_s=0.0, stop=stop
            )
            rows = _audit_rows(temporary)

        self.assertEqual(code, 0)
        stopping = [row for row in rows if row.get("event") == "daemon_stopping"]
        self.assertEqual(len(stopping), 1)
        self.assertEqual(stopping[0]["reason"], "stop_signalled")

    def test_a_worker_that_cannot_start_never_ends_the_daemon(self) -> None:
        """N-02: Thread.start() re-raises the OS failure to create a thread. The
        budget added a worker to a path that must stay best-effort, so the whole
        worker -- construction, start and join -- has to sit inside the guard."""
        real_thread = threading.Thread

        class _FailingStart(real_thread):
            def start(self):
                raise RuntimeError("thread_start_failed")

        def thread_factory(*args, **kwargs):
            if kwargs.get("name") == "daemon-audit-event":
                return _FailingStart(*args, **kwargs)
            return real_thread(*args, **kwargs)

        with TemporaryDirectory() as temporary:
            with patch.object(daemon.threading, "Thread", thread_factory):
                code, httpd, _generations = self._drive(temporary, idle_timeout_s=1.0)
            rows = _audit_rows(temporary)

        self.assertEqual(code, 0, "a logger that cannot spawn must not end the daemon")
        self.assertGreaterEqual(httpd.shutdowns, 1)
        self.assertEqual(rows, [], "no row could be written, and that is all")

    def test_a_raising_job_probe_never_prevents_startup(self) -> None:
        """Question 2 of the review, answered by construction: everything the
        record needs is inside one guard, so a probe that explodes costs the row
        and nothing else."""
        with TemporaryDirectory() as temporary:
            with patch.object(
                daemon, "_job_object_raw", side_effect=RuntimeError("kernel32 is gone")
            ):
                code, httpd, _generations = self._drive(temporary, idle_timeout_s=1.0)
            rows = _audit_rows(temporary)

        self.assertEqual(code, 0)
        self.assertGreaterEqual(httpd.shutdowns, 1)
        self.assertEqual([row for row in rows if row.get("event") == "daemon_started"], [])
        # One broken probe does not silence the rest of the record.
        self.assertEqual(
            len([row for row in rows if row.get("event") == "daemon_stopping"]), 1
        )

    def test_a_refused_parent_lookup_leaves_the_field_null_not_the_row(self) -> None:
        def image(pid: int):
            if pid == os.getpid():
                return sys.executable
            raise OSError(5, "access denied")

        with TemporaryDirectory() as temporary:
            with patch.object(
                daemon.orphan_guard, "full_image_path_of", side_effect=image
            ):
                code, _httpd, _generations = self._drive(temporary, idle_timeout_s=1.0)
            rows = _audit_rows(temporary)

        self.assertEqual(code, 0)
        started = [row for row in rows if row.get("event") == "daemon_started"]
        self.assertEqual(len(started), 1)
        self.assertIsNone(started[0]["parent_image"])

    def test_daemon_module_never_arms_the_parent_death_watchdog(self) -> None:
        """LL-156: a process meant to OUTLIVE its session must not die with its
        parent. The published field and the source have to agree."""
        source = Path(daemon.__file__).read_text(encoding="utf-8")
        for symbol in (
            "install_parent_death_watchdog",
            "open_parent_wait_handle",
            "wait_for_parent_exit",
        ):
            self.assertNotIn(symbol, source, f"daemon.py must not use {symbol}")
        self.assertIs(daemon.DAEMON_PARENT_DEATH_ARMED, False)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
