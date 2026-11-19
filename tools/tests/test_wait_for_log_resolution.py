"""wait_for(log_matches): the log resolution inside the deadline, every line read.

fb-20261116-141235-88db and fb-20261117-213827-3915, both on LIVE:

* 88db: with the line already inside lookback_lines the call answered
  satisfied=true with probes=1 after 62-68 s, whatever poll_interval_s. With
  lookback_lines=0 and timeout_s=100 it timed out after 103 s with probes=2,
  and a line written between the two probes was never matched.
* 3915: after the lease expired and session_acquire_wait re-adopted the run,
  every call answered satisfied=false, probes=0, scanned.files=[] and an
  elapsed_s that did not follow timeout_s (10 -> 68.6 s, 30 -> 71.0 s).

Measured on the live host on 2026-11-18: launch_logs._profile_dirs_from_runs
took 47.2 s over the store's 144 EXITED runs, one sealed launcher policy load
per run at 0.28-0.35 s each, and execute_wait_for resolved the log paths once
before polling and again on every probe, neither bounded by its deadline.

What each class pins (C6 is the rest of the wait_for suite, left untouched):

* C1 a resolution slower than timeout_s ends the call within timeout_s + 1 s,
  with satisfied=false and last_error="log_paths_timeout", for the lines, the
  launch and the marker scans, and when what is slow is the synchronous
  sealed-policy load rather than lifecycle_status; a slow later resolution
  never stretches the call either. The tool description names
  log_paths_timeout.
* C2 a line already inside lookback_lines matches on the first probe in under
  1 s on the store's shape: 144 EXITED rows plus the live or re-adopted run.
* C3 lifecycle_status is not called once per probe: at most 3 calls in a 10 s
  wait polled every second (one before polling, at most one refresh per 5 s).
  A log created during the wait is still read, from its first byte.
* C4 with lookback_lines=0 every line appended between two probes reaches the
  matcher, or the answer says scanned["truncated"] is true.
* C5 the row of a re-adopted run resolves the same logs as before its lease
  expired: adopt_run changes the owner and the state, nothing the resolution
  reads.
* C7 _profile_dirs_from_runs loads the sealed policy at most once per call,
  returns what the per-run algorithm returned, and still fails closed.

Added after the review of the first implementation:

* P1 #1 listing the profile folders is bounded by the deadline too, before
  polling (lines and launch scans) and on a later probe.
* P1 #2 resolutions abandoned at their deadline do not starve the event
  loop's default executor, and at most 2 of their workers are live at once.
* P2 #3 a profiles folder created during the wait is read on a later probe,
  with no other lifecycle_status call or sealed-policy load.
* P2 #4 scanned.truncated is true when lines the call asked for were lost and
  false after a complete read; file_matches does not publish it.

Added after the second review:

* P1 a status request abandoned at its deadline (a coroutine whose blocking
  request runs through asyncio.to_thread, as the client's does) does not
  starve the default executor; at most 2 are live at once.
* The periodic refresh follows a run that changes during the wait, within
  the C3 bound on lifecycle_status calls.

Seams these tests rely on, shared with the rest of the suite: the deadline and
elapsed_s come from time.monotonic and the wait sleeps between probes with
asyncio.sleep (the fake clocks patch both, as _ExactWaitClock users do); a
sealed policy load goes through launcher_registry.try_open_approved_launcher,
opened.validate_native_pe() and secure_launcher.load_verified_bundle, each
looked up on its module at call time, as dayz_test_tool._close_project_policy
does today (dayz_test_tool.py:4352-4358).
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import (
    dayz_test_tool,
    launch_logs,
    launcher_registry,
    log_tail,
    process_lifecycle,
    secure_launcher,
    server,
    server_cli,
)
from tests._tiers import slow_test
from tests.dayz_test_tool_helpers import _policy
from tests.wait_for_helpers import _ExactWaitClock


LOG_PATHS_TIMEOUT = "log_paths_timeout"
NEEDLE = "[MCP-RESOLVE] needle line"
# "timeout_s + 1 s at most": the contract's own slack, not a latency budget.
DEADLINE_SLACK_S = 1.0
# How long a slow lifecycle_status takes in C1: several times any timeout_s
# used with it, so an unbounded resolution shows as an elapsed_s about this.
SLOW_STATUS_S = 4.0
# How long a slow sealed-policy load blocks the thread it runs on in C1. The
# load is synchronous code (dayz_test_tool.py:4352-4358): asyncio can bound it
# only if it runs off the event loop's thread.
SLOW_LOAD_S = 4.0
# How long a slow listing of a profiles folder blocks the thread it runs on.
SLOW_LIST_S = 4.0
# Measured on the live host on 2026-11-18: one sealed-policy load, 0.28-0.35 s.
MEASURED_LOAD_S = 0.3
# Measured on the same day: EXITED rows in LIVE's store, one mod, one profiles.
LIVE_STORE_EXITED_RUNS = 144
# C3: one lifecycle_status call before polling plus at most one refresh per 5 s
# of a 10 s wait. Calling it on every probe of that wait makes 8 calls.
MAX_STATUS_CALLS_IN_10_S = 3
_SHA256 = "0123456789abcdef" * 4
_GENERATION = "gen-live"


def _leaf() -> str:
    """The profiles leaf the bound instance admits (``profiles`` when unbound)."""

    return server_cli.profile_leaf_name(server_cli.bound_instance_token())


def _stamp(seconds_ago: float = 30.0) -> str:
    """A process start recent enough that files the test writes pass the floor."""

    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )


def _append(path: Path, *lines: str) -> None:
    """Append LF-terminated lines as bytes, the way the game appends its logs."""

    with path.open("ab") as handle:
        handle.write("".join(line + "\n" for line in lines).encode("utf-8"))


class _Project:
    """One project as its sealed policy names it, with a live launch's logs."""

    def __init__(
        self, root: Path, mod: str = "ExampleMod", *, with_client: bool = True
    ) -> None:
        self.mod = mod
        self.dev_root = root / f"{mod}_Suite"
        self.server = self.dev_root / "_server" / _leaf()
        self.client = self.dev_root / "_client" / _leaf()
        self.server.mkdir(parents=True)
        if with_client:
            self.client.mkdir(parents=True)
        self.rpt = self.server / "DayZDiag_x64_2026-11-18_10-00-00.RPT"
        self.script_log = self.server / "script_2026-11-18_10-00-00.log"
        _append(self.rpt, "10:00:00 Mission read.", "10:00:01 Server started.")
        _append(self.script_log, "SCRIPT : mission init", "SCRIPT : bridge loaded")
        self.policy = _policy(mod=mod, dev_root=str(self.dev_root))


def _process(role: str, pid: int) -> process_lifecycle.ProcessRecord:
    return process_lifecycle.ProcessRecord(
        pid=pid,
        creation_time_utc=_stamp(),
        executable_sha256=_SHA256,
        command_line_sha256=_SHA256,
        role=role,
        identity_scheme="psutil-argv-v2",
    )


def _mission(project: _Project) -> str:
    missions = project.dev_root / "_server" / "mpmissions"
    return str(missions / "dayzOffline.chernarusplus")


def _launched_run(project: _Project) -> process_lifecycle.RunRecord:
    """The live run as a launch leaves it: RUNNING, owned, server and client."""

    return process_lifecycle.RunRecord(
        run_id="run-live",
        owner_session_id="session-launcher",
        owner_lease_id="lease-launcher",
        state="RUNNING",
        label="",
        mod="@" + project.mod,
        profiles=str(project.server),
        mission=_mission(project),
        processes=[_process("server", 4242), _process("client", 4243)],
        daemon_generation_at_launch=_GENERATION,
    )


def _exited_run(project: _Project, index: int) -> process_lifecycle.RunRecord:
    """A retired run of the same project: EXITED keeps no owner and no process."""

    return process_lifecycle.RunRecord(
        run_id=f"run-exited-{index:03d}",
        owner_session_id=None,
        owner_lease_id=None,
        state="EXITED",
        label="",
        mod="@" + project.mod,
        profiles=str(project.server),
        mission=_mission(project),
        processes=[],
        daemon_generation_at_launch=_GENERATION,
    )


def _readopted(run: process_lifecycle.RunRecord) -> process_lifecycle.RunRecord:
    """The same run after its lease expired and session_acquire_wait took it back.

    The expired lease's cleanup ends in RunManifestStore.release_owner
    (process_lifecycle.py:2384-2387): no owner, RUNNING_IDLE. The grant of
    session_acquire_wait then adopts the one ownerless idle run
    (loopback.py:10333-10358), and adopt_run sets the new owner and RUNNING
    (process_lifecycle.py:9886-9888) before it writes the record back. Neither
    step touches mod, profiles or processes.
    """

    expired = copy.deepcopy(run)
    expired.owner_session_id = None
    expired.owner_lease_id = None
    expired.state = "RUNNING_IDLE"
    expired.validate()
    adopted = copy.deepcopy(expired)
    adopted.owner_session_id = "session-after-expiry"
    adopted.owner_lease_id = "lease-after-expiry"
    adopted.state = "RUNNING"
    adopted.validate()
    return adopted


def _status_rows(records: list[process_lifecycle.RunRecord]) -> list[dict[str, Any]]:
    """lifecycle_status rows the way ProcessLifecycle._projected_run builds them.

    process_lifecycle.py:3009-3013: the record as a dict, its generation
    projection and its client diagnostics.
    """

    rows: list[dict[str, Any]] = []
    for record in records:
        record.validate()
        row = dataclasses.asdict(record)
        row.update(process_lifecycle._generation_projection(record, _GENERATION))
        row["client_diagnostics"] = []
        rows.append(row)
    return rows


class _OpenedLauncher:
    def __init__(self, store: "_SealedPolicyStore", *, blocked: bool = False) -> None:
        self._store = store
        self._blocked = blocked

    def __enter__(self) -> "_OpenedLauncher":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._blocked:
            self._blocked = False
            self._store.load_finished()

    def validate_native_pe(self) -> None:
        self._store.native_checks += 1
        if self._store.fail_at == "native_pe":
            raise ValueError("launcher_hash_drift")


class _VerifiedBundle:
    def __init__(self, sealed_policies: object) -> None:
        self.sealed_policies = sealed_policies

    def __enter__(self) -> "_VerifiedBundle":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class _SealedPolicyStore:
    """The sealed launcher policy, loaded where every load has to pass.

    dayz_test_tool._close_project_policy imports
    launcher_registry.try_open_approved_launcher at call time, checks
    opened.validate_native_pe() and reads the policies through
    secure_launcher.load_verified_bundle. Patching the two module attributes
    stands in for the registry the suite does not build and counts every load.
    ``fail_at`` makes one step of the load fail. ``clock`` charges each load to
    a fake clock at ``load_cost_s``, the cost measured on the live host.
    ``block_s`` makes load number ``block_from`` and every later one block the
    calling thread, synchronously, as the real registry read and PE check do;
    ``release`` cuts that block short so a worker thread can be let go instead
    of waited out, and ``drained`` says when no load is left inside the fake.
    """

    def __init__(
        self,
        *policies: object,
        clock: _ExactWaitClock | None = None,
        load_cost_s: float = 0.0,
        block_s: float = 0.0,
        block_from: int = 1,
    ) -> None:
        self.sealed = tuple(SimpleNamespace(policy=policy) for policy in policies)
        self.clock = clock
        self.load_cost_s = load_cost_s
        self.block_s = block_s
        self.block_from = block_from
        self.fail_at: str | None = None
        self.opens = 0
        self.native_checks = 0
        self.bundles = 0
        self.release = threading.Event()
        self.blocked_threads: list[int] = []
        self.max_in_flight = 0
        self._in_flight = 0
        self._lock = threading.Lock()

    @property
    def in_flight(self) -> int:
        """Threads inside a blocked load right now."""

        with self._lock:
            return self._in_flight

    def try_open_approved_launcher(self, launcher_id: str) -> _OpenedLauncher:
        with self._lock:
            self.opens += 1
            blocked = self.block_s > 0 and self.opens >= self.block_from
            if blocked:
                self._in_flight += 1
                self.max_in_flight = max(self.max_in_flight, self._in_flight)
                self.blocked_threads.append(threading.get_ident())
        if self.clock is not None:
            self.clock.now += self.load_cost_s
        if blocked:
            self.release.wait(self.block_s)
        if self.fail_at == "open":
            if blocked:
                self.load_finished()
            raise RuntimeError("launcher_registry_busy")
        return _OpenedLauncher(self, blocked=blocked)

    def load_finished(self) -> None:
        with self._lock:
            self._in_flight -= 1

    async def drained(self, timeout_s: float) -> bool:
        """Whether every blocked load left the fake within ``timeout_s`` (real time)."""

        deadline = time.monotonic() + timeout_s
        while True:
            with self._lock:
                if self._in_flight == 0:
                    return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.01)

    def load_verified_bundle(self, opened: object) -> _VerifiedBundle:
        self.bundles += 1
        if self.fail_at == "bundle":
            raise ValueError("bundle_signature_invalid")
        if self.fail_at == "policies":
            # Not a tuple: _semantic_policies answers launcher_policy_invalid.
            return _VerifiedBundle(list(self.sealed))
        return _VerifiedBundle(self.sealed)

    @contextmanager
    def installed(self) -> Iterator["_SealedPolicyStore"]:
        with (
            patch.object(
                launcher_registry,
                "try_open_approved_launcher",
                self.try_open_approved_launcher,
            ),
            patch.object(
                secure_launcher, "load_verified_bundle", self.load_verified_bundle
            ),
        ):
            yield self


class _PollClock(_ExactWaitClock):
    """_ExactWaitClock that also runs one action per sleep between probes.

    A sleep of 0 s is a yield, not a poll interval, and runs nothing.
    """

    def __init__(self) -> None:
        super().__init__()
        self.between_probes: list[Callable[[], None]] = []

    async def sleep(self, delay: float) -> None:
        await super().sleep(delay)
        if delay > 0 and self.between_probes:
            self.between_probes.pop(0)()

    @contextmanager
    def installed(self) -> Iterator["_PollClock"]:
        with (
            patch("dayz_mcp.server.time.monotonic", self),
            patch("dayz_mcp.server.asyncio.sleep", self.sleep),
        ):
            yield self


class _SlowListing:
    """log_tail.resolve_log_files that blocks its thread, then lists for real.

    _current_launch_logs looks the lister up on log_tail at call time
    (launch_logs.py:99), so every listing of a profiles folder for its logs
    passes here, on whatever thread runs it. A listing that starts ``after_s``
    or more after ``started_at`` blocks for ``block_s``, synchronously, as a
    slow disk or a busy synced folder would; ``release`` cuts the block short
    and ``drained`` says when no listing is left inside the fake.
    """

    def __init__(self, block_s: float, *, after_s: float = 0.0) -> None:
        self._real = log_tail.resolve_log_files
        self.block_s = block_s
        self.after_s = after_s
        self.started_at: float | None = None
        self.release = threading.Event()
        self.listings = 0
        self.blocked_threads: list[int] = []
        self._in_flight = 0
        self._lock = threading.Lock()

    def __call__(self, profiles_dir: str) -> list[str]:
        with self._lock:
            self.listings += 1
            blocked = (
                self.started_at is not None
                and time.monotonic() - self.started_at >= self.after_s
            )
            if blocked:
                self._in_flight += 1
                self.blocked_threads.append(threading.get_ident())
        try:
            if blocked:
                self.release.wait(self.block_s)
            return self._real(profiles_dir)
        finally:
            if blocked:
                with self._lock:
                    self._in_flight -= 1

    async def drained(self, timeout_s: float) -> bool:
        """Whether every blocked listing left the fake within ``timeout_s``."""

        deadline = time.monotonic() + timeout_s
        while True:
            with self._lock:
                if self._in_flight == 0:
                    return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.01)

    @contextmanager
    def installed(self) -> Iterator["_SlowListing"]:
        with patch.object(log_tail, "resolve_log_files", self):
            yield self


class _StatusRuntime:
    """What execute_wait_for touches for log_matches: tool_lock and lifecycle_status.

    lifecycle_status is a coroutine, as ClientRuntime's is (server.py:2784; the
    control client runs the HTTP round trip in asyncio.to_thread,
    control_client.py:506-508). ``delays[i]`` is how long call i + 1 takes and
    the last one repeats. On a fake clock a delay moves the clock; without one
    the call really sleeps, so asyncio can time it out.
    """

    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        delays: tuple[float, ...] = (0.0,),
        clock: _ExactWaitClock | None = None,
    ) -> None:
        self.tool_lock = asyncio.Lock()
        self.rows = rows
        self.delays = delays
        self.clock = clock
        self.status_calls = 0

    async def lifecycle_status(self) -> dict[str, Any]:
        delay = self.delays[min(self.status_calls, len(self.delays) - 1)]
        self.status_calls += 1
        if delay > 0:
            if self.clock is not None:
                self.clock.now += delay
            else:
                await asyncio.sleep(delay)
        return {"runs": copy.deepcopy(self.rows)}

    async def call_bridge(
        self, cmd: str, args: dict[str, Any], peer: str, timeout_s: float
    ) -> dict[str, Any]:
        raise AssertionError(f"log_matches must not call the bridge (got {cmd})")


def _file_names(result: dict[str, Any]) -> list[str]:
    return sorted(entry["name"] for entry in result["scanned"]["files"])


class _ProjectCase:
    """A temporary project, removed after the test."""

    def setUp(self) -> None:
        super().setUp()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = _Project(Path(temp.name))


class SlowResolutionKeepsTheDeadlineTest(
    _ProjectCase, unittest.IsolatedAsyncioTestCase
):
    """C1: lifecycle_status slower than timeout_s, as 3915's probes=0 calls were."""

    TIMEOUT_S = 0.5

    def _slow_runtime(
        self, delays: tuple[float, ...] = (SLOW_STATUS_S,)
    ) -> _StatusRuntime:
        return _StatusRuntime(
            _status_rows([_launched_run(self.project)]), delays=delays
        )

    async def _timed(
        self, runtime: _StatusRuntime, **kwargs: Any
    ) -> tuple[float, dict[str, Any]]:
        store = _SealedPolicyStore(self.project.policy)
        started = time.monotonic()
        with store.installed():
            result = await server.execute_wait_for(
                runtime, "log_matches", pattern=NEEDLE, **kwargs
            )
        return time.monotonic() - started, result

    def _assert_inside_the_deadline(
        self,
        elapsed: float,
        result: dict[str, Any],
        timeout_s: float,
        cause: str = f"lifecycle_status took {SLOW_STATUS_S:g} s",
    ) -> None:
        bound = timeout_s + DEADLINE_SLACK_S
        self.assertLessEqual(
            elapsed,
            bound,
            f"wait_for(timeout_s={timeout_s:g}) returned after {elapsed:.2f} s: "
            f"{cause} and the log path "
            "resolution was not bounded by the call's deadline "
            f"(probes={result.get('probes')}, satisfied={result.get('satisfied')}, "
            f"last_error={result.get('last_error')!r})",
        )
        self.assertLessEqual(result["elapsed_s"], bound)
        self.assertFalse(
            result["satisfied"],
            "no log path was resolved inside the deadline, so nothing may match",
        )
        self.assertTrue(result["timed_out"])

    @slow_test
    async def test_lines_scan_stops_at_the_deadline_and_names_the_resolution(
        self,
    ) -> None:
        _append(self.project.script_log, f"SCRIPT : {NEEDLE}")
        elapsed, result = await self._timed(
            self._slow_runtime(), timeout_s=self.TIMEOUT_S, lookback_lines=200
        )
        self._assert_inside_the_deadline(elapsed, result, self.TIMEOUT_S)
        self.assertEqual(result.get("last_error"), LOG_PATHS_TIMEOUT)

    @slow_test
    async def test_launch_scan_stops_at_the_deadline_and_names_the_resolution(
        self,
    ) -> None:
        _append(self.project.script_log, f"SCRIPT : {NEEDLE}")
        elapsed, result = await self._timed(
            self._slow_runtime(), timeout_s=self.TIMEOUT_S, lookback_from="launch"
        )
        self._assert_inside_the_deadline(elapsed, result, self.TIMEOUT_S)
        self.assertEqual(result.get("last_error"), LOG_PATHS_TIMEOUT)

    @slow_test
    async def test_marker_scan_stops_at_the_deadline_and_names_the_resolution(
        self,
    ) -> None:
        path = str(self.project.script_log)
        at_end = log_tail.read_since(path, None)["marker"]
        marker = log_tail.encode_marker({path: at_end})
        _append(self.project.script_log, f"SCRIPT : {NEEDLE}")
        elapsed, result = await self._timed(
            self._slow_runtime(), timeout_s=self.TIMEOUT_S, marker=marker
        )
        self._assert_inside_the_deadline(elapsed, result, self.TIMEOUT_S)
        self.assertEqual(result.get("last_error"), LOG_PATHS_TIMEOUT)

    @slow_test
    async def test_a_slow_later_resolution_does_not_stretch_the_call(self) -> None:
        # The first resolution is quick; any later one takes SLOW_STATUS_S. A
        # design that resolves once per call never meets it; one that refreshes
        # has to bound the refresh by the same deadline.
        timeout_s = 1.0
        elapsed, result = await self._timed(
            self._slow_runtime(delays=(0.0, SLOW_STATUS_S)),
            timeout_s=timeout_s,
            poll_interval_s=0.5,
            lookback_lines=0,
        )
        self._assert_inside_the_deadline(elapsed, result, timeout_s)

    async def _timed_with_a_blocking_load(
        self, store: _SealedPolicyStore, **kwargs: Any
    ) -> tuple[float, dict[str, Any], bool]:
        """Run the wait with a quick lifecycle_status and ``store``'s slow load.

        Returns the real elapsed time, the answer, and whether a blocked load
        ran on the event loop's own thread. A load left blocked in a worker
        thread is released and drained here, with the fake still installed, so
        no thread is inside it when the next test starts.
        """

        runtime = _StatusRuntime(_status_rows([_launched_run(self.project)]))
        loop_thread = threading.get_ident()
        self.addCleanup(store.release.set)
        started = time.monotonic()
        with store.installed():
            try:
                result = await server.execute_wait_for(
                    runtime, "log_matches", pattern=NEEDLE, **kwargs
                )
            finally:
                elapsed = time.monotonic() - started
                store.release.set()
                drained = await store.drained(5.0)
        self.assertTrue(
            drained,
            "a thread was still inside the sealed-policy load 5 s after its release",
        )
        return elapsed, result, loop_thread in store.blocked_threads

    @slow_test
    async def test_a_slow_synchronous_policy_load_does_not_stretch_the_call(
        self,
    ) -> None:
        # GA166: C1 stays a defence if the sealed-policy load is slow again.
        # lifecycle_status answers at once; the load blocks its thread.
        _append(self.project.script_log, f"SCRIPT : {NEEDLE}")
        cases = (
            # label, first blocked load, timeout_s, wait arguments
            ("before polling", 1, self.TIMEOUT_S, {"lookback_lines": 200}),
            ("later", 2, 1.0, {"poll_interval_s": 0.5, "lookback_lines": 0}),
        )
        for label, block_from, timeout_s, kwargs in cases:
            with self.subTest(resolution=label):
                store = _SealedPolicyStore(
                    self.project.policy, block_s=SLOW_LOAD_S, block_from=block_from
                )
                elapsed, result, on_loop = await self._timed_with_a_blocking_load(
                    store, timeout_s=timeout_s, **kwargs
                )
                where = "the event loop's thread" if on_loop else "a worker thread"
                cause = f"the sealed-policy load blocked {SLOW_LOAD_S:g} s on {where}"
                self._assert_inside_the_deadline(elapsed, result, timeout_s, cause)
                if block_from == 1:
                    # Only the resolution before polling is owed by every design;
                    # one that resolves once per call never makes a later load.
                    self.assertEqual(result.get("last_error"), LOG_PATHS_TIMEOUT)

    async def _timed_with_a_slow_listing(
        self, listing: _SlowListing, **kwargs: Any
    ) -> tuple[float, dict[str, Any], bool]:
        """Run the wait with quick status and policy and ``listing``'s slow lister.

        Returns the real elapsed time, the answer, and whether a blocked
        listing ran on the event loop's own thread. A listing left blocked in a
        worker thread is released and drained here, with the fakes installed.
        """

        runtime = _StatusRuntime(_status_rows([_launched_run(self.project)]))
        store = _SealedPolicyStore(self.project.policy)
        loop_thread = threading.get_ident()
        self.addCleanup(listing.release.set)
        with store.installed(), listing.installed():
            started = time.monotonic()
            listing.started_at = started
            try:
                result = await server.execute_wait_for(
                    runtime, "log_matches", pattern=NEEDLE, **kwargs
                )
            finally:
                elapsed = time.monotonic() - started
                listing.release.set()
                drained = await listing.drained(5.0)
        self.assertTrue(
            drained, "a thread was still inside a listing 5 s after its release"
        )
        return elapsed, result, loop_thread in listing.blocked_threads

    @slow_test
    async def test_a_slow_listing_before_polling_stops_at_the_deadline(self) -> None:
        # Review P1 #1: status and policy answer at once, listing the profile
        # folders blocks. The listing is part of the log path resolution, so
        # it ends the call like any other slow part of it; in the launch scan
        # a match read after the deadline is not a success.
        _append(self.project.script_log, f"SCRIPT : {NEEDLE}")
        for scan, kwargs in (
            ("lines", {"lookback_lines": 200}),
            ("launch", {"lookback_from": "launch"}),
        ):
            with self.subTest(scan=scan):
                listing = _SlowListing(SLOW_LIST_S)
                elapsed, result, on_loop = await self._timed_with_a_slow_listing(
                    listing, timeout_s=self.TIMEOUT_S, **kwargs
                )
                where = "the event loop's thread" if on_loop else "a worker thread"
                cause = f"a profiles listing blocked {SLOW_LIST_S:g} s on {where}"
                self._assert_inside_the_deadline(
                    elapsed, result, self.TIMEOUT_S, cause
                )
                self.assertEqual(result.get("last_error"), LOG_PATHS_TIMEOUT)

    @slow_test
    async def test_a_slow_listing_on_a_later_probe_does_not_stretch_the_call(
        self,
    ) -> None:
        # Review P1 #1, second half: listings in the first 0.25 s (before
        # polling and the first probe) are quick; a later one, such as the
        # re-listing of the known profile folders on the second probe, blocks.
        timeout_s = 1.5
        listing = _SlowListing(SLOW_LIST_S, after_s=0.25)
        elapsed, result, on_loop = await self._timed_with_a_slow_listing(
            listing, timeout_s=timeout_s, poll_interval_s=0.5, lookback_lines=0
        )
        where = "the event loop's thread" if on_loop else "a worker thread"
        cause = f"a later listing blocked {SLOW_LIST_S:g} s on {where}"
        self._assert_inside_the_deadline(elapsed, result, timeout_s, cause)


class ToolDescriptionNamesTheResolutionTimeoutTest(unittest.TestCase):
    """C1/C6: a new last_error is named where callers read the contract."""

    def test_the_wait_for_description_names_log_paths_timeout(self) -> None:
        app, _runtime = server.build_app(
            server.ServerConfig(log_sink=lambda _message: None)
        )
        description = app._tool_manager.get_tool("wait_for").description or ""
        self.assertIn(LOG_PATHS_TIMEOUT, description)


class FirstProbeOnTheStoreShapeTest(_ProjectCase, unittest.IsolatedAsyncioTestCase):
    """C2: the store's shape, with each sealed-policy load at its measured cost.

    lifecycle_status itself answers at once (S=0). The fake clock charges
    MEASURED_LOAD_S per load, so a resolution that loads the policy once per
    run costs 145 x 0.3 s = 43.5 s, the order of 88db's and 3915's elapsed_s.
    """

    async def test_the_line_in_lookback_matches_on_the_first_probe(self) -> None:
        _append(self.project.script_log, f"SCRIPT : {NEEDLE}")
        launched = _launched_run(self.project)
        exited = [
            _exited_run(self.project, index) for index in range(LIVE_STORE_EXITED_RUNS)
        ]
        # 3915 used timeout_s 10-30 after the re-adoption. With 100 s the
        # per-run loads still reach the first probe, as 88db's satisfied calls
        # did after 62-68 s.
        for kind, live in (("launched", launched), ("readopted", _readopted(launched))):
            for timeout_s in (10.0, 30.0, 100.0):
                with self.subTest(run=kind, timeout_s=timeout_s):
                    clock = _PollClock()
                    store = _SealedPolicyStore(
                        self.project.policy, clock=clock, load_cost_s=MEASURED_LOAD_S
                    )
                    runtime = _StatusRuntime(_status_rows(exited + [live]), clock=clock)
                    with store.installed(), clock.installed():
                        result = await server.execute_wait_for(
                            runtime,
                            "log_matches",
                            pattern=NEEDLE,
                            timeout_s=timeout_s,
                            poll_interval_s=2.0,
                            lookback_lines=200,
                        )
                    summary = (
                        f"probes={result['probes']}, "
                        f"elapsed_s={result['elapsed_s']:.1f}, "
                        f"sealed-policy loads={store.opens}, "
                        f"lifecycle_status calls={runtime.status_calls}, "
                        f"scanned files={_file_names(result)}"
                    )
                    self.assertTrue(
                        result["satisfied"],
                        "the line was already inside lookback_lines and was not "
                        f"matched: {summary}",
                    )
                    self.assertEqual(result["probes"], 1, summary)
                    self.assertLess(
                        result["elapsed_s"],
                        1.0,
                        f"matched, but not on a prompt first probe: {summary}",
                    )
                    self.assertIn(NEEDLE, result["observed"])


class ProbeCostTest(_ProjectCase, unittest.IsolatedAsyncioTestCase):
    """C3: S=0.5 s, poll_interval_s=1, timeout_s=10, on a fake clock."""

    async def _wait(self, clock: _PollClock, runtime: _StatusRuntime) -> dict[str, Any]:
        store = _SealedPolicyStore(self.project.policy)
        with store.installed(), clock.installed():
            return await server.execute_wait_for(
                runtime,
                "log_matches",
                pattern=NEEDLE,
                timeout_s=10.0,
                poll_interval_s=1.0,
                lookback_lines=0,
            )

    def _runtime(self, clock: _PollClock) -> _StatusRuntime:
        return _StatusRuntime(
            _status_rows([_launched_run(self.project)]), delays=(0.5,), clock=clock
        )

    async def test_lifecycle_status_is_not_called_once_per_probe(self) -> None:
        clock = _PollClock()
        runtime = self._runtime(clock)
        result = await self._wait(clock, runtime)

        self.assertFalse(result["satisfied"])
        self.assertGreaterEqual(
            result["probes"],
            5,
            f"a 10 s wait polled every 1 s made {result['probes']} probes",
        )
        self.assertLessEqual(
            runtime.status_calls,
            MAX_STATUS_CALLS_IN_10_S,
            f"lifecycle_status was called {runtime.status_calls} times for "
            f"{result['probes']} probes of one 10 s wait; the bound is "
            f"{MAX_STATUS_CALLS_IN_10_S} (once before polling, at most one "
            "refresh per 5 s)",
        )

    async def test_a_log_created_during_the_wait_is_read_from_its_first_byte(
        self,
    ) -> None:
        # The client writes its script log once it starts, after the wait began.
        # With lookback_lines=0 a marker at its end would skip the needle, which
        # is its first line.
        new_log = self.project.client / "script_2026-11-18_10-00-20.log"
        clock = _PollClock()
        clock.between_probes.append(
            lambda: _append(
                new_log,
                f"SCRIPT : {NEEDLE}",
                "SCRIPT : client mission init",
                "SCRIPT : client ready",
            )
        )
        result = await self._wait(clock, self._runtime(clock))

        self.assertTrue(
            result["satisfied"],
            "the first line of a log created during the wait was not matched "
            f"(probes={result['probes']}, scanned files={_file_names(result)})",
        )
        self.assertIn(NEEDLE, result["observed"])
        self.assertIn("_client/" + new_log.name, _file_names(result))


class NoGapBetweenProbesTest(_ProjectCase, unittest.IsolatedAsyncioTestCase):
    """C4: lookback_lines=0, lines appended to the script log between two probes."""

    async def _wait_with_append(self, *lines: str) -> dict[str, Any]:
        clock = _PollClock()
        clock.between_probes.append(lambda: _append(self.project.script_log, *lines))
        rows = _status_rows([_launched_run(self.project)])
        runtime = _StatusRuntime(rows, clock=clock)
        store = _SealedPolicyStore(self.project.policy)
        with store.installed(), clock.installed():
            return await server.execute_wait_for(
                runtime,
                "log_matches",
                pattern=NEEDLE,
                timeout_s=10.0,
                poll_interval_s=1.0,
                lookback_lines=0,
            )

    async def test_the_needle_among_other_appended_lines_matches(self) -> None:
        before = [f"SCRIPT : tick {index}" for index in range(20)]
        after = [f"SCRIPT : tick {index}" for index in range(20, 40)]
        result = await self._wait_with_append(*before, f"SCRIPT : {NEEDLE}", *after)

        self.assertTrue(result["satisfied"])
        self.assertIn(NEEDLE, result["observed"])

    async def test_a_burst_past_the_tail_cap_is_read_whole_or_reported(self) -> None:
        # More than log_tail.MAX_TAIL_BYTES lands between two probes and the
        # needle is the oldest of it: read_since keeps the newest bytes, so a
        # read cut by that cap drops it without a word -- one way to lose a
        # line between two probes as 88db did.
        burst: list[str] = []
        size = 0
        while size <= log_tail.MAX_TAIL_BYTES + 64 * 1024:
            line = f"SCRIPT : burst {len(burst):06d} " + "x" * 80
            burst.append(line)
            size += len(line) + 1
        result = await self._wait_with_append(f"SCRIPT : {NEEDLE}", *burst)

        scanned = result.get("scanned") or {}
        self.assertTrue(
            result["satisfied"] or scanned.get("truncated") is True,
            f"{size} bytes were appended between two probes and their oldest line, "
            "the needle, never reached the matcher, and scanned does not say "
            "truncated: "
            f"{ {key: value for key, value in scanned.items() if key != 'files'} }, "
            f"observed={str(result['observed'])[:60]!r}",
        )


class ReadoptedRunTest(_ProjectCase, unittest.IsolatedAsyncioTestCase):
    """C5: the row lifecycle_status returns after a re-adoption (3915)."""

    async def test_a_readopted_run_finds_the_same_logs_and_matches(self) -> None:
        _append(self.project.script_log, f"SCRIPT : {NEEDLE}")
        launched = _launched_run(self.project)
        exited = [
            _exited_run(self.project, index) for index in range(LIVE_STORE_EXITED_RUNS)
        ]
        store = _SealedPolicyStore(self.project.policy)
        results: dict[str, dict[str, Any]] = {}
        for kind, live in (("launched", launched), ("readopted", _readopted(launched))):
            runtime = _StatusRuntime(_status_rows(exited + [live]))
            with store.installed():
                results[kind] = await server.execute_wait_for(
                    runtime,
                    "log_matches",
                    pattern=NEEDLE,
                    timeout_s=2.0,
                    poll_interval_s=0.5,
                    lookback_lines=200,
                )

        for kind, result in results.items():
            with self.subTest(run=kind):
                self.assertTrue(
                    result["satisfied"],
                    f"probes={result['probes']}, scanned files={_file_names(result)}",
                )
                self.assertIn(NEEDLE, result["observed"])
        self.assertTrue(_file_names(results["readopted"]))
        self.assertEqual(
            _file_names(results["readopted"]), _file_names(results["launched"])
        )


def _per_run_profile_dirs(
    runs: list[dict[str, Any]], store: _SealedPolicyStore
) -> list[str]:
    """launch_logs._profile_dirs_from_runs as of c2f4363: one load per run.

    launch_logs.py:158-167 with _recorded_anchor_in_sealed_project
    (launch_logs.py:138-155) and dayz_test_tool._close_project_policy
    (dayz_test_tool.py:4341-4364) inlined over ``store``. The helpers they
    called are called unchanged.
    """

    allowed: list[str] = []
    for item in runs:
        profiles = item.get("profiles")
        if not log_tail.is_allowed_profiles_dir(profiles):
            continue
        try:
            with store.try_open_approved_launcher("dayz-test-v1") as opened:
                opened.validate_native_pe()
                with store.load_verified_bundle(opened) as bundle:
                    policies = dayz_test_tool._semantic_policies(bundle.sealed_policies)
        except Exception:
            continue
        matches = [policy for policy in policies if item.get("mod") == "@" + policy.mod]
        if len(matches) != 1:
            continue
        if dayz_test_tool._validated_recorded_leaf(matches[0], item) is None:
            continue
        allowed.append(str(profiles))
    return launch_logs._sibling_profile_dirs(sorted(set(allowed)))


class OneSealedPolicyLoadPerCallTest(unittest.TestCase):
    """C7: _profile_dirs_from_runs and the sealed launcher policy."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def _side(self, project: str, parent: str) -> str:
        """One side's profiles folder of a project, created on disk."""

        path = self.root / project / parent / _leaf()
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def _row(self, run_id: str, profiles: object, **fields: object) -> dict[str, Any]:
        row: dict[str, Any] = {
            "run_id": run_id,
            "state": "EXITED",
            "processes": [],
            "profiles": profiles,
        }
        row.update(fields)
        return row

    def _policies(self) -> tuple[object, ...]:
        def policy(mod: str, project: str) -> object:
            return _policy(mod=mod, dev_root=str(self.root / project))

        return (
            policy("ModA", "ModA_dev"),
            policy("ModB", "ModB_dev"),
            policy("ModC", "ModC_dev"),
            # Two policies claim Dup: no single sealed project owns its runs.
            policy("Dup", "Dup_dev"),
            policy("Dup", "Dup_dev"),
            policy("ModE", "ModE_dev"),
            policy("ModF", "ModF_dev"),
        )

    def _one_mod_store(self) -> tuple[list[dict[str, Any]], list[str]]:
        """LIVE's shape: 144 EXITED rows and the live run, one mod, one folder."""

        a_server = self._side("ModA_dev", "_server")
        a_client = self._side("ModA_dev", "_client")
        rows = [
            self._row(f"exited-{index:03d}", a_server, mod="@ModA")
            for index in range(LIVE_STORE_EXITED_RUNS)
        ]
        rows.append(
            self._row(
                "live",
                a_server,
                mod="@ModA",
                state="RUNNING",
                processes=[{"pid": 4242, "creation_time_utc": _stamp()}],
            )
        )
        return rows, sorted([a_server, a_client])

    def _mixed_store(self) -> tuple[list[dict[str, Any]], list[str]]:
        """Several mods, EXITED and live rows, and every way a row is refused."""

        a_server = self._side("ModA_dev", "_server")
        a_client = self._side("ModA_dev", "_client")
        b_server = self._side("ModB_dev", "_server")
        b_client = self._side("ModB_dev", "_client")
        c_client = self._side("ModC_dev", "_client")
        wrong_leaf = self.root / "ModA_dev" / "_server" / (_leaf() + "-elsewhere")
        wrong_leaf.mkdir(parents=True, exist_ok=True)
        traversal = (
            str(self.root / "ModA_dev" / "_client") + "\\..\\_server\\" + _leaf()
        )
        live = [{"pid": 4242, "creation_time_utc": _stamp()}]
        no_profiles_key = {
            "run_id": "no-profiles-key",
            "state": "EXITED",
            "processes": [],
            "mod": "@ModA",
        }
        rows = [
            # Refused before any policy is consulted.
            self._row("traversal", traversal, mod="@ModA"),
            self._row("relative", "_server\\" + _leaf(), mod="@ModA"),
            self._row("wrong-leaf", str(wrong_leaf), mod="@ModA"),
            self._row("no-profiles", None, mod="@ModA"),
            no_profiles_key,
            # Admitted.
            self._row(
                "a-live", a_server, mod="@ModA", state="RUNNING", processes=live
            ),
            *[
                self._row(f"a-exited-{index}", a_server, mod="@ModA")
                for index in range(6)
            ],
            self._row("a-client", a_client, mod="@ModA"),
            self._row("b-exited", b_server, mod="@ModB"),
            # Only the _client side recorded, and no _server folder on disk.
            self._row(
                "c-idle", c_client, mod="@ModC", state="RUNNING_IDLE", processes=live
            ),
            # Refused: no single sealed project for the row's mod.
            self._row("dup", self._side("Dup_dev", "_server"), mod="@Dup"),
            self._row("no-at", self._side("ModE_dev", "_server"), mod="ModE"),
            self._row("other-case", self._side("ModF_dev", "_server"), mod="@modf"),
            self._row(
                "unclaimed", self._side("Unclaimed_dev", "_server"), mod="@Unclaimed"
            ),
            self._row("empty-mod", self._side("EmptyMod_dev", "_server"), mod=""),
            self._row("no-mod-key", self._side("NoMod_dev", "_server")),
            self._row("int-mod", self._side("IntMod_dev", "_server"), mod=7),
            self._row("none-mod", self._side("NoneMod_dev", "_server"), mod=None),
            self._row("list-mod", self._side("ListMod_dev", "_server"), mod=["@ModA"]),
            # Refused: @ModB's leaf outside ModB's dev_root.
            self._row("stray-anchor", self._side("Stray", "_server"), mod="@ModB"),
        ]
        return rows, sorted([a_server, a_client, b_server, b_client, c_client])

    def _call(
        self, store: _SealedPolicyStore, rows: list[dict[str, Any]]
    ) -> list[str]:
        with store.installed():
            return launch_logs._profile_dirs_from_runs(copy.deepcopy(rows))

    def _assert_one_load(
        self, store: _SealedPolicyStore, rows: list[dict[str, Any]]
    ) -> None:
        loads = (
            f"{store.opens} launcher opens, {store.native_checks} PE checks, "
            f"{store.bundles} bundle loads"
        )
        self.assertEqual(
            store.opens,
            1,
            f"one call over {len(rows)} runs loaded the sealed policy {store.opens} "
            f"times ({loads}); the contract is at most once per call",
        )
        self.assertEqual(store.native_checks, 1, loads)
        self.assertEqual(store.bundles, 1, loads)

    def test_many_runs_of_one_mod_load_the_sealed_policy_once(self) -> None:
        rows, expected = self._one_mod_store()
        store = _SealedPolicyStore(*self._policies())
        result = self._call(store, rows)

        self._assert_one_load(store, rows)
        self.assertEqual(result, expected)

    def test_runs_of_several_mods_load_the_sealed_policy_once(self) -> None:
        rows, expected = self._mixed_store()
        store = _SealedPolicyStore(*self._policies())
        result = self._call(store, rows)

        self._assert_one_load(store, rows)
        self.assertEqual(result, expected)

    def test_same_profile_dirs_as_the_per_run_algorithm(self) -> None:
        rows, expected = self._mixed_store()
        for order, ordered in (("as listed", rows), ("reversed", rows[::-1])):
            with self.subTest(order=order):
                reference = _per_run_profile_dirs(
                    ordered, _SealedPolicyStore(*self._policies())
                )
                result = self._call(_SealedPolicyStore(*self._policies()), ordered)
                self.assertEqual(reference, expected)
                self.assertEqual(result, reference)

    def test_exited_runs_are_not_filtered(self) -> None:
        # Filtering them would change what wait_for, logs_since and the capture
        # read; that is a decision for the owner, not part of loading once.
        a_server = self._side("ModA_dev", "_server")
        a_client = self._side("ModA_dev", "_client")
        b_server = self._side("ModB_dev", "_server")
        rows = [
            self._row("a-1", a_server, mod="@ModA"),
            self._row("a-2", a_server, mod="@ModA"),
            self._row("b-1", b_server, mod="@ModB"),
        ]
        reference = _per_run_profile_dirs(rows, _SealedPolicyStore(*self._policies()))
        result = self._call(_SealedPolicyStore(*self._policies()), rows)

        self.assertEqual(reference, sorted([a_client, a_server, b_server]))
        self.assertEqual(result, reference)

    def test_a_failed_load_rejects_every_run(self) -> None:
        rows, _expected = self._mixed_store()
        for fail_at in ("open", "native_pe", "bundle", "policies"):
            with self.subTest(fail_at=fail_at):
                store = _SealedPolicyStore(*self._policies())
                store.fail_at = fail_at
                self.assertEqual(self._call(store, rows), [])

    def test_a_failed_load_is_not_retried_for_each_run(self) -> None:
        rows, _expected = self._one_mod_store()
        for fail_at in ("open", "native_pe", "bundle", "policies"):
            with self.subTest(fail_at=fail_at):
                store = _SealedPolicyStore(*self._policies())
                store.fail_at = fail_at
                result = self._call(store, rows)
                self.assertEqual(result, [])
                self.assertLessEqual(
                    store.opens,
                    1,
                    f"a sealed-policy load failing at {fail_at!r} was attempted "
                    f"{store.opens} times in one call over {len(rows)} runs",
                )
                self.assertLessEqual(store.bundles, 1)

    def test_every_call_loads_the_policy_again(self) -> None:
        rows, expected = self._one_mod_store()
        store = _SealedPolicyStore(*self._policies())
        first = self._call(store, rows)
        store.fail_at = "open"
        second = self._call(store, rows)
        store.fail_at = None
        third = self._call(store, rows)

        self.assertEqual(first, expected)
        self.assertEqual(
            second,
            [],
            "a load that fails on this call must reject every run, whatever an "
            "earlier call loaded",
        )
        self.assertEqual(third, expected)


class AbandonedResolutionsTest(_ProjectCase, unittest.IsolatedAsyncioTestCase):
    """Review P1 #2: a resolution abandoned at its deadline holds no shared thread.

    Every sealed-policy load blocks. A run of short waits, more than any
    default executor has threads (min(32, cpu count + 4)), gives up at its
    deadline one after the other. The event loop's default executor must
    still run an unrelated job; the workers inside the blocked load stay
    within MAX_LIVE_WORKERS, because an abandoned resolution keeps its slot
    until its worker really finishes; and once the loads are released the
    next wait resolves again, so no slot is kept for good.
    """

    CALLS = 40
    BLOCK_S = 6.0
    SHORT_TIMEOUT_S = 0.05
    MAX_LIVE_WORKERS = 2

    @slow_test
    async def test_abandoned_resolutions_do_not_starve_the_default_executor(
        self,
    ) -> None:
        _append(self.project.script_log, f"SCRIPT : {NEEDLE}")
        store = _SealedPolicyStore(self.project.policy, block_s=self.BLOCK_S)
        self.addCleanup(store.release.set)
        runtime = _StatusRuntime(_status_rows([_launched_run(self.project)]))
        answers: list[tuple[float, dict[str, Any]]] = []
        with store.installed():
            try:
                for _ in range(self.CALLS):
                    started = time.monotonic()
                    result = await server.execute_wait_for(
                        runtime,
                        "log_matches",
                        pattern=NEEDLE,
                        timeout_s=self.SHORT_TIMEOUT_S,
                        lookback_lines=200,
                    )
                    answers.append((time.monotonic() - started, result))
                live = store.in_flight
                probe_started = time.monotonic()
                try:
                    probe = await asyncio.wait_for(
                        asyncio.to_thread(lambda: 1), timeout=1.0
                    )
                except TimeoutError:
                    probe = None
                probe_s = time.monotonic() - probe_started
            finally:
                store.release.set()
                drained = await store.drained(10.0)
            store.block_s = 0.0
            recovered = await server.execute_wait_for(
                runtime,
                "log_matches",
                pattern=NEEDLE,
                timeout_s=2.0,
                lookback_lines=200,
            )

        late = [
            (index, round(elapsed, 2))
            for index, (elapsed, _result) in enumerate(answers)
            if elapsed > self.SHORT_TIMEOUT_S + DEADLINE_SLACK_S
        ]
        wrong = [
            (index, result.get("satisfied"), result.get("last_error"))
            for index, (_elapsed, result) in enumerate(answers)
            if result.get("satisfied") or result.get("last_error") != LOG_PATHS_TIMEOUT
        ]
        self.assertEqual(late, [], "short waits overran their deadline (call, s)")
        self.assertEqual(
            wrong, [], "short waits on a blocked load must end in log_paths_timeout"
        )
        self.assertEqual(
            probe,
            1,
            f"after {self.CALLS} waits gave up on a blocked sealed-policy load, an "
            f"unrelated asyncio.to_thread job did not run within 1 s "
            f"({probe_s:.2f} s): {live} workers were still inside the load",
        )
        self.assertLessEqual(
            store.max_in_flight,
            self.MAX_LIVE_WORKERS,
            f"{store.max_in_flight} workers were inside the blocked sealed-policy "
            "load at once; an abandoned resolution keeps its slot until its worker "
            f"finishes, so at most {self.MAX_LIVE_WORKERS} may be live",
        )
        self.assertTrue(
            drained, "a worker was still inside the load 10 s after its release"
        )
        self.assertTrue(
            recovered["satisfied"],
            "once the blocked loads were released the next wait did not resolve: "
            f"last_error={recovered.get('last_error')!r}",
        )


class ProfileFolderCreatedDuringTheWaitTest(unittest.IsolatedAsyncioTestCase):
    """Review P2 #3: a side's profiles folder created during the wait is read.

    Only _server/<leaf> exists when the wait starts; the client creates
    _client/<leaf> and its script log after the second poll. A later probe
    finds it from the run's approved folders, before the 5 s refresh, with no
    other lifecycle_status call and no other sealed-policy load. role is left
    unset: with role="client" a run that has no client folder yet answers
    profile_unresolved at once (tests/test_fb_33c3_wait_for_role.py:148-155).
    """

    def setUp(self) -> None:
        super().setUp()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = _Project(Path(temp.name), with_client=False)

    async def test_a_client_folder_created_during_the_wait_is_read(self) -> None:
        clock = _PollClock()
        store = _SealedPolicyStore(self.project.policy)
        rows = _status_rows([_launched_run(self.project)])
        runtime = _StatusRuntime(rows, clock=clock)
        new_log = self.project.client / "script_2026-11-18_10-00-20.log"
        at_creation: dict[str, int] = {}

        def client_starts() -> None:
            self.project.client.mkdir(parents=True)
            _append(new_log, f"SCRIPT : {NEEDLE}", "SCRIPT : client ready")
            at_creation["status_calls"] = runtime.status_calls
            at_creation["loads"] = store.opens

        clock.between_probes.extend([lambda: None, client_starts])
        with store.installed(), clock.installed():
            result = await server.execute_wait_for(
                runtime,
                "log_matches",
                pattern=NEEDLE,
                timeout_s=4.0,
                poll_interval_s=0.5,
                lookback_lines=0,
            )

        summary = (
            f"probes={result['probes']}, scanned files={_file_names(result)}, "
            f"lifecycle_status calls={runtime.status_calls}, "
            f"sealed-policy loads={store.opens}, at creation={at_creation}"
        )
        self.assertTrue(
            result["satisfied"],
            "the client profiles folder created during the wait was never read "
            f"before the deadline: {summary}",
        )
        self.assertIn("_client/" + new_log.name, _file_names(result))
        self.assertEqual(
            runtime.status_calls,
            at_creation["status_calls"],
            f"the new folder took another lifecycle_status call: {summary}",
        )
        self.assertEqual(
            store.opens,
            at_creation["loads"],
            f"the new folder took another sealed-policy load: {summary}",
        )


class TruncatedSaysWhetherLinesWereLostTest(
    _ProjectCase, unittest.IsolatedAsyncioTestCase
):
    """Review P2 #4: scanned.truncated is true exactly when lines asked for were lost.

    A lookback window cut by the tail byte cap loses lines the call asked for;
    a complete small read loses none, which an "always true" report would get
    wrong. In the lines scan the field is part of the answer and must be false
    there; the launch and marker scans may leave it out when nothing was lost.
    """

    async def _wait(self, pattern: str = NEEDLE, **kwargs: Any) -> dict[str, Any]:
        clock = _PollClock()
        store = _SealedPolicyStore(self.project.policy)
        rows = _status_rows([_launched_run(self.project)])
        runtime = _StatusRuntime(rows, clock=clock)
        with store.installed(), clock.installed():
            return await server.execute_wait_for(
                runtime,
                "log_matches",
                pattern=pattern,
                timeout_s=2.0,
                poll_interval_s=1.0,
                **kwargs,
            )

    async def test_a_lookback_window_cut_by_the_byte_cap_is_reported(self) -> None:
        # The needle, then 199 lines of 2 KiB: the last 200 lines take about
        # 400 KiB and the rewind reads only the last log_tail.MAX_TAIL_BYTES.
        long_lines = [
            f"SCRIPT : long {index:03d} " + "y" * 2030 for index in range(199)
        ]
        _append(self.project.script_log, f"SCRIPT : {NEEDLE}", *long_lines)
        result = await self._wait(lookback_lines=200)

        scanned = result["scanned"]
        self.assertTrue(
            result["satisfied"] or scanned.get("truncated") is True,
            "lookback_lines=200 asked for the needle's line, the read lost it and "
            f"scanned says truncated={scanned.get('truncated')!r} "
            f"(lines_total={scanned.get('lines_total')})",
        )

    async def test_a_complete_read_is_not_called_truncated(self) -> None:
        _append(self.project.script_log, f"SCRIPT : {NEEDLE}")
        path = str(self.project.script_log)
        at_end = log_tail.encode_marker(
            {path: log_tail.read_since(path, None)["marker"]}
        )
        cases = (
            ("lines, matched", NEEDLE, {"lookback_lines": 200}, True),
            ("lines, no match", "ABSENT-ON-PURPOSE", {"lookback_lines": 200}, True),
            ("launch, matched", NEEDLE, {"lookback_from": "launch"}, False),
            ("marker, no match", NEEDLE, {"marker": at_end}, False),
        )
        for label, pattern, kwargs, field_required in cases:
            with self.subTest(case=label):
                scanned = (await self._wait(pattern, **kwargs))["scanned"]
                if field_required:
                    self.assertIs(
                        scanned.get("truncated"),
                        False,
                        f"nothing was lost, so truncated must be false: {scanned}",
                    )
                else:
                    self.assertIs(scanned.get("truncated", False), False, str(scanned))


_FILE_SESSION = "session-file-matches-0001"
_FILE_LEASE = "lease-file-matches"


class _LeaseRuntime:
    """What file_matches touches: the held lease, its heartbeat, one owned run."""

    def __init__(self, run: dict[str, Any]) -> None:
        self.identity = SimpleNamespace(session_id=_FILE_SESSION)
        self.tool_lock = asyncio.Lock()
        self._control = SimpleNamespace(
            active_lease_token="token", active_lease_id=_FILE_LEASE
        )
        self._run = run

    async def lifecycle_status(self) -> dict[str, Any]:
        return {"runs": [dict(self._run)]}

    async def session_heartbeat(self, token: str) -> dict[str, Any]:
        return {"ok": True}


class FileMatchesScanReportTest(unittest.IsolatedAsyncioTestCase):
    """Review P2 #4, file_matches: scanned.truncated is a log_matches field.

    file_matches does not carry a cut read into its report, so it does not
    publish the field at all, matched or timed out. Reporting it correctly
    would be the other way out; this pins the omission.
    """

    def setUp(self) -> None:
        super().setUp()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        leaf = _leaf()
        self.server = root / "_server" / leaf
        self.server.mkdir(parents=True)
        (root / "_client" / leaf).mkdir(parents=True)
        self.run = {
            "run_id": "run-file-matches",
            "state": "RUNNING",
            "owner_session_id": _FILE_SESSION,
            "owner_lease_id": _FILE_LEASE,
            "mod": "@ExampleMod",
            "profiles": str(self.server),
            "processes": [{"role": "server", "pid": 1}, {"role": "client", "pid": 2}],
        }

        def folder(policy: Any, role: str, _roots: Any, run: Any = None) -> str:
            side = "_client" if role in {"client", "offline"} else "_server"
            return str(Path(policy.dev_root) / side / leaf)

        policy = SimpleNamespace(mod="ExampleMod", dev_root=str(root))
        roots = {"server": "_server", "client": "_client"}
        for name, kwargs in (
            ("_close_project_policy", {"return_value": policy}),
            ("_start_role_roots", {"return_value": roots}),
            ("_close_role_folder", {"side_effect": folder}),
        ):
            patcher = patch.object(dayz_test_tool, name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_file_matches_does_not_publish_truncated(self) -> None:
        _append(self.server / "mission.log", "mission start", f"SCRIPT : {NEEDLE}")
        for label, pattern in (("matched", NEEDLE), ("timed out", "ABSENT-ON-PURPOSE")):
            with self.subTest(case=label):
                clock = _PollClock()
                with clock.installed():
                    result = await server.execute_wait_for(
                        _LeaseRuntime(self.run),
                        "file_matches",
                        pattern=pattern,
                        profile_file="mission.log",
                        timeout_s=2.0,
                        poll_interval_s=0.5,
                        lookback_lines=50,
                    )
                self.assertEqual(result["satisfied"], label == "matched")
                self.assertNotIn(
                    "truncated",
                    result["scanned"],
                    "file_matches publishes scanned.truncated without tracking a cut "
                    f"read: {result['scanned']}",
                )


class _ToThreadStatusRuntime:
    """lifecycle_status shaped like the client's real one.

    ClientRuntime.lifecycle_status is a coroutine whose HTTP round trip is a
    blocking ``_request_once`` run through ``asyncio.to_thread``
    (control_client.py:506-508). Cancelling the coroutine does not stop that
    thread. Here the request blocks on ``release`` for up to ``block_s``;
    ``max_in_flight`` is the most threads ever inside it at once.
    """

    def __init__(self, rows: list[dict[str, Any]], *, block_s: float) -> None:
        self.tool_lock = asyncio.Lock()
        self.rows = rows
        self.block_s = block_s
        self.release = threading.Event()
        self.requests = 0
        self.max_in_flight = 0
        self._in_flight = 0
        self._lock = threading.Lock()

    @property
    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight

    def _request_once(self) -> dict[str, Any]:
        with self._lock:
            self.requests += 1
            blocked = self.block_s > 0
            if blocked:
                self._in_flight += 1
                self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            if blocked:
                self.release.wait(self.block_s)
            return {"runs": copy.deepcopy(self.rows)}
        finally:
            if blocked:
                with self._lock:
                    self._in_flight -= 1

    async def lifecycle_status(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._request_once)

    async def drained(self, timeout_s: float) -> bool:
        """Whether every blocked request ended within ``timeout_s`` (real time)."""

        deadline = time.monotonic() + timeout_s
        while True:
            if self.in_flight == 0:
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.01)

    async def call_bridge(
        self, cmd: str, args: dict[str, Any], peer: str, timeout_s: float
    ) -> dict[str, Any]:
        raise AssertionError(f"log_matches must not call the bridge (got {cmd})")


class AbandonedStatusRequestsTest(_ProjectCase, unittest.IsolatedAsyncioTestCase):
    """Second review, P1: the status request abandoned at a deadline holds no thread.

    lifecycle_status is a coroutine whose blocking request runs through
    asyncio.to_thread, as the client's does. A run of short waits, more than
    any default executor has threads, gives up at its deadline one after the
    other. The event loop's default executor must still run an unrelated job;
    at most MAX_LIVE_REQUESTS threads are ever inside the request, because an
    abandoned request keeps its slot until it really ends; and once the
    requests are released the next wait resolves again.
    """

    CALLS = 40
    BLOCK_S = 5.0
    SHORT_TIMEOUT_S = 0.025
    MAX_LIVE_REQUESTS = 2

    @slow_test
    async def test_abandoned_status_requests_do_not_starve_the_default_executor(
        self,
    ) -> None:
        _append(self.project.script_log, f"SCRIPT : {NEEDLE}")
        store = _SealedPolicyStore(self.project.policy)
        rows = _status_rows([_launched_run(self.project)])
        runtime = _ToThreadStatusRuntime(rows, block_s=self.BLOCK_S)
        self.addCleanup(runtime.release.set)
        answers: list[tuple[float, dict[str, Any]]] = []
        with store.installed():
            try:
                for _ in range(self.CALLS):
                    started = time.monotonic()
                    result = await server.execute_wait_for(
                        runtime,
                        "log_matches",
                        pattern=NEEDLE,
                        timeout_s=self.SHORT_TIMEOUT_S,
                        lookback_lines=200,
                    )
                    answers.append((time.monotonic() - started, result))
                live = runtime.in_flight
                lock_free = not runtime.tool_lock.locked()
                probe_started = time.monotonic()
                try:
                    probe = await asyncio.wait_for(
                        asyncio.to_thread(lambda: 1), timeout=1.0
                    )
                except TimeoutError:
                    probe = None
                probe_s = time.monotonic() - probe_started
            finally:
                runtime.release.set()
                drained = await runtime.drained(10.0)
            runtime.block_s = 0.0
            recovered = await server.execute_wait_for(
                runtime,
                "log_matches",
                pattern=NEEDLE,
                timeout_s=2.0,
                lookback_lines=200,
            )

        late = [
            (index, round(elapsed, 3))
            for index, (elapsed, _result) in enumerate(answers)
            if elapsed > self.SHORT_TIMEOUT_S + DEADLINE_SLACK_S
        ]
        wrong = [
            (index, result.get("satisfied"), result.get("last_error"))
            for index, (_elapsed, result) in enumerate(answers)
            if result.get("satisfied") or result.get("last_error") != LOG_PATHS_TIMEOUT
        ]
        self.assertEqual(late, [], "short waits overran their deadline (call, s)")
        self.assertEqual(
            wrong, [], "short waits on a blocked request must end in log_paths_timeout"
        )
        self.assertTrue(lock_free, "tool_lock was still held after the short waits")
        self.assertEqual(
            probe,
            1,
            f"after {self.CALLS} waits gave up on a blocked status request, an "
            f"unrelated asyncio.to_thread job did not run within 1 s "
            f"({probe_s:.2f} s): {live} threads were still inside the request",
        )
        self.assertLessEqual(
            runtime.max_in_flight,
            self.MAX_LIVE_REQUESTS,
            f"{runtime.max_in_flight} threads were inside the blocked status "
            "request at once; an abandoned request keeps its slot until it ends, "
            f"so at most {self.MAX_LIVE_REQUESTS} may be live",
        )
        self.assertTrue(
            drained, "a thread was still inside the request 10 s after its release"
        )
        self.assertTrue(
            recovered["satisfied"],
            "once the blocked requests were released the next wait did not "
            f"resolve: last_error={recovered.get('last_error')!r}",
        )


class _SwitchingRuntime(_StatusRuntime):
    """lifecycle_status that answers ``after`` from fake time ``switch_at`` on."""

    def __init__(
        self,
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
        *,
        switch_at: float,
        clock: _ExactWaitClock,
    ) -> None:
        super().__init__(before, clock=clock)
        self.after = after
        self.switch_at = switch_at

    async def lifecycle_status(self) -> dict[str, Any]:
        self.status_calls += 1
        assert self.clock is not None
        rows = self.after if self.clock.now >= self.switch_at else self.rows
        return {"runs": copy.deepcopy(rows)}


class RunChangeDuringTheWaitTest(unittest.IsolatedAsyncioTestCase):
    """Second review: the periodic refresh follows a run that changes mid-wait.

    lifecycle_status answers the first project's run until 6 s of fake time,
    then that run EXITED and a second project's run, whose script log has the
    needle. Neither folder is the other's sibling, so only a later full
    resolution finds the second run's logs. A wait that resolves before
    polling and on the first probe but never refreshes afterwards misses them.
    The C3 rule bounds the cost: one lifecycle_status call before polling
    plus at most one refresh per 5 s, 1 + 15 // 5 = 4 calls in this wait.
    """

    TIMEOUT_S = 15.0
    SWITCH_AT_S = 6.0

    def setUp(self) -> None:
        super().setUp()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.first = _Project(root, mod="FirstMod")
        self.second = _Project(root, mod="SecondMod")
        self.second_log = self.second.server / "script_2026-11-18_10-00-06.log"
        _append(self.second_log, "SCRIPT : second run init", f"SCRIPT : {NEEDLE}")

    async def test_a_run_started_during_the_wait_is_read_after_a_refresh(
        self,
    ) -> None:
        first = dataclasses.replace(_launched_run(self.first), run_id="run-first")
        first_exited = dataclasses.replace(
            first,
            owner_session_id=None,
            owner_lease_id=None,
            state="EXITED",
            processes=[],
        )
        second = dataclasses.replace(_launched_run(self.second), run_id="run-second")
        clock = _PollClock()
        runtime = _SwitchingRuntime(
            _status_rows([first]),
            _status_rows([first_exited, second]),
            switch_at=self.SWITCH_AT_S,
            clock=clock,
        )
        store = _SealedPolicyStore(self.first.policy, self.second.policy)
        with store.installed(), clock.installed():
            result = await server.execute_wait_for(
                runtime,
                "log_matches",
                pattern=NEEDLE,
                timeout_s=self.TIMEOUT_S,
                poll_interval_s=1.0,
                lookback_lines=0,
            )

        summary = (
            f"probes={result['probes']}, elapsed_s={result['elapsed_s']:.1f}, "
            f"lifecycle_status calls={runtime.status_calls}, "
            f"scanned files={_file_names(result)}"
        )
        self.assertTrue(
            result["satisfied"],
            "the run that started during the wait was never read: the resolution "
            f"was not refreshed after the first probe ({summary})",
        )
        self.assertIn(NEEDLE, result["observed"])
        self.assertIn("_server/" + self.second_log.name, _file_names(result))
        self.assertLess(result["elapsed_s"], self.TIMEOUT_S, summary)
        self.assertLessEqual(
            runtime.status_calls, 1 + int(self.TIMEOUT_S // 5), summary
        )


if __name__ == "__main__":
    unittest.main()
