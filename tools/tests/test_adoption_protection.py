"""250f PR 2: only the launcher adopts a run that is not abandoned.

The daemon decides in adopt_run with the full session id. The MCP side only
turns that refusal into messages. Clocks are real time.time(): adopt_run reads
box_occupancy() with no injected now.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import server as server_module
from dayz_mcp import session_handoff
from dayz_mcp.input_activity import InputAttributor, InputSample
from dayz_mcp.process_lifecycle import (
    INPUT_SIGNAL_STALE_S,
    RUN_IDLE_CUT_S,
    ProcessLifecycle,
    RunManifestStore,
    RunRecord,
    caller_may_adopt_ownerless,
)
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.server import (
    ADOPT_BLOCKED_ON,
    ServerConfig,
    TAKEOVER_REQUIRED,
    _runtime_holds_lease,
    _session_status_blocked_on,
    box_available_for,
)
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator
from tests.client_helpers import _fixture_client_runtime
from tests.daemon_helpers import (
    DaemonHttpServer,
    _attach_fixture_transport,
    _config,
    _http,
    _wait_until_lease_claimable,
)
from tests.lifecycle_helpers import stamp_launcher
from tests.mcp_helpers import _content_json
from tests.process_lifecycle_helpers import (
    AuditSink,
    FakeGuard,
    FakeLauncher,
    process,
    snapshot,
)
from tests.steam_helpers import FakeSteamGate


GENERATION = "gen-now"
OTHER_WINDOW_PID = 31337
RUN_ID = "run-x"
PREFIX = "0123456789ab"

LAUNCHER = ClientIdentity(
    "codex", 11, 1, "2026-07-15T00:00:00Z", "launcher-session", "owner"
)
OTHER = ClientIdentity(
    "claude", 22, 2, "2026-07-15T00:00:01Z", "stranger-session", "other"
)
RELOADED = ClientIdentity(
    "codex", 99, 7, "2026-09-29T12:00:00Z", LAUNCHER.session_id, "reloaded"
)
REOPENED = ClientIdentity(
    "codex", 11, 1, "2026-09-29T12:00:01Z", "reopened-session", "owner"
)
PREFIX_LAUNCHER = ClientIdentity(
    "codex", 11, 1, "2026-07-15T00:00:00Z", f"{PREFIX}-launcher", "owner"
)
PREFIX_OTHER = ClientIdentity(
    "claude", 22, 2, "2026-07-15T00:00:01Z", f"{PREFIX}-stranger", "other"
)


class _Bindings:
    def __init__(self) -> None:
        self.bound: set[str] = set()

    def run_has_bound_binding(self, run_id: str) -> bool:
        return run_id in self.bound

    def fence_runs(self, run_ids: list[str]) -> None:
        return None

    def unfence_runs(self, run_ids: list[str]) -> None:
        return None


def _records() -> list:
    return [process(801, role="server"), process(802, role="client")]


class AdoptionFixture(unittest.TestCase):
    """Fake guard and a known diag scan, same shape as the use_state tests."""

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        (self.game / "DayZDiag_x64.exe").write_bytes(b"")
        paths = RuntimePaths(
            self.root / "runtime",
            self.root / "runtime" / "audit",
            self.root / "runtime" / "coordination.json",
            self.root / "runtime" / "runs.json",
        )
        self.audit = AuditSink()
        self.coordinator = SessionCoordinator(
            token_fn=lambda: "token-adopt",
            id_fn=lambda: "lease-adopt",
            audit=self.audit,
        )
        self.store = RunManifestStore(paths)
        self.guard = FakeGuard()
        self.live: list[int] = []
        self.bindings = _Bindings()
        self.lifecycle = self._lifecycle()
        self.holder: ClientIdentity | None = None
        self.token: str | None = None

    def tearDown(self) -> None:
        self._release()
        self.temporary.cleanup()

    def _lifecycle(self) -> ProcessLifecycle:
        return ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {
                "known": True,
                "processes": [
                    {"pid": pid, "name": "DayZDiag_x64.exe"} for pid in self.live
                ],
            },
            game_path=self.game,
            launcher=FakeLauncher(),
            id_fn=lambda: "run-1",
            bindings=self.bindings,
            daemon_generation=GENERATION,
        )

    def _release(self) -> None:
        if self.holder is None or self.token is None:
            return
        status, body = self.coordinator.release(self.holder, self.token)
        self.holder = None
        self.token = None
        self.assertIn(status, (200, 202), body)

    def _lease(self, client: ClientIdentity) -> str:
        self._release()
        status, body = self.coordinator.acquire(client, "lifecycle")
        self.assertEqual(status, 200, body)
        self.holder = client
        self.token = body["lease_token"]
        return self.token

    def _clear_use(self) -> None:
        life = self.lifecycle
        with life._activity_lock:
            key = life._activity_key(RUN_ID)
            for table in (
                life._ownerless_since,
                life._human_input_at,
                life._uncertain_input_at,
                life._launched_by,
            ):
                table.pop(key, None)
            life._input_good_at = None
            life._signal_recovered_at = None
            life._input_attributor = InputAttributor(bridge_s=INPUT_SIGNAL_STALE_S)
        life._invalidate_box_cache()

    def _put(
        self,
        *,
        live_client: bool,
        state: str = "RUNNING_IDLE",
        generation: str | None = GENERATION,
    ) -> None:
        records = _records()
        self.live.clear()
        self.live.append(801)
        if live_client:
            self.live.append(802)
        for record in records:
            self.guard.snapshots[record.pid] = snapshot(record)
        self.bindings.bound.clear()
        run = RunRecord(
            RUN_ID,
            None,
            None,
            state,
            "label",
            "@Mod",
            "profiles",
            "mission",
            records,
            daemon_generation_at_launch=generation,
        )
        if self.store.get(RUN_ID) is None:
            self.store.add(run)
        else:
            self.store.replace(run)
        self._clear_use()

    def _ownerless(self, at: float) -> None:
        life = self.lifecycle
        with life._activity_lock:
            life._ownerless_since[life._activity_key(RUN_ID)] = at

    def _good(self, at: float, *, foreground: int = OTHER_WINDOW_PID, tick: int = 100) -> None:
        self.lifecycle.record_input_sample(
            InputSample(
                at=at, ok=True, last_input_tick=tick, idle_ms=0, foreground_pid=foreground
            )
        )

    def _healthy(self, start: float, until: float) -> None:
        at = start
        while at < until:
            self._good(at)
            at += 1.5
        self._good(until)

    def _human(self, at: float) -> None:
        self._good(at - 0.25, foreground=802, tick=1000)
        self.lifecycle.record_input_sample(
            InputSample(
                at=at, ok=True, last_input_tick=1100, idle_ms=0, foreground_pid=802
            )
        )

    def _row(self) -> dict[str, object]:
        box = self.lifecycle.box_occupancy()
        for item in box["runs"]:
            if item["run_id"] == RUN_ID:
                return item
        self.fail(f"no row: {box['runs']}")

    def shape(self, kind: str) -> dict[str, object]:
        """Install kind and return the row adopt_run will classify."""

        if kind == "unknown":
            self._put(live_client=True)
        elif kind == "human":
            self._put(live_client=True)
            now = time.time()
            self._human(now)
            self._good(time.time())
        elif kind == "idle":
            self._put(live_client=False)
            self._ownerless(time.time())
        elif kind == "idle_waiting":
            self._put(live_client=True)
            start = time.time() - RUN_IDLE_CUT_S - 2.0
            self._ownerless(start)
            self._healthy(start, time.time())
        elif kind == "abandoned":
            self._put(live_client=False)
            self._ownerless(time.time() - RUN_IDLE_CUT_S - 1.0)
        elif kind == "unreconciled":
            self._put(live_client=False, state="UNRECONCILED")
        else:
            self.fail(kind)
        return self._row()

    def _pending(self) -> list:
        active = self.coordinator._active
        self.assertIsNotNone(active)
        return list(active.pending_authorizations)


class DirectAdoptMatrixTest(AdoptionFixture):
    def test_state_by_caller(self) -> None:
        cases = (
            ("unknown", "unknown", "input_signal_unavailable", False),
            ("human", "human", None, True),
            ("idle", "idle", None, True),
            ("idle_waiting", "idle_waiting", "bridge_not_ready", False),
            ("abandoned", "abandoned", "client_gone", False),
        )
        for kind, state, reason, retry in cases:
            for caller_name, client in (("launcher", LAUNCHER), ("other", OTHER)):
                with self.subTest(kind=kind, caller=caller_name):
                    row = self.shape(kind)
                    self.assertEqual(row["use_state"], state, row)
                    self.assertEqual(row["use_reason"], reason, row)
                    stamp_launcher(self.lifecycle, RUN_ID, LAUNCHER)
                    token = self._lease(client)
                    result = self.lifecycle.adopt_run(client, token, RUN_ID)
                    if caller_name == "launcher" or kind == "abandoned":
                        self.assertIs(result.get("ok"), True, result)
                        stored = self.store.get(RUN_ID)
                        self.assertEqual(stored.state, "RUNNING")
                        self.assertEqual(stored.owner_session_id, client.session_id)
                        continue
                    self.assertEqual(result.get("error"), "run_protected", result)
                    self.assertEqual(result.get("_http_status"), 409)
                    self.assertEqual(result.get("use_state"), state)
                    self.assertEqual(result.get("use_reason"), reason)
                    hint = str(result.get("hint") or "")
                    self.assertIn("run_protected", hint)
                    self.assertNotIn("takeover=true", hint)
                    if retry:
                        self.assertGreater(result.get("retry_after_s"), 0)
                        self.assertIn("retry in", hint)
                    else:
                        self.assertNotIn("retry_after_s", result)
                    self.assertEqual(self._pending(), [])
                    stored = self.store.get(RUN_ID)
                    self.assertEqual(stored.state, "RUNNING_IDLE")
                    self.assertIsNone(stored.owner_session_id)

    def test_unreconciled_stranger_still_adopts(self) -> None:
        row = self.shape("unreconciled")
        self.assertIsNone(row["use_state"])
        token = self._lease(OTHER)
        result = self.lifecycle.adopt_run(OTHER, token, RUN_ID)
        self.assertIs(result.get("ok"), True, result)
        stored = self.store.get(RUN_ID)
        self.assertEqual(stored.state, "RUNNING")
        self.assertEqual(stored.owner_session_id, OTHER.session_id)

    def test_server_reload_keeps_the_launcher(self) -> None:
        self.shape("idle")
        stamp_launcher(self.lifecycle, RUN_ID, LAUNCHER)
        token = self._lease(RELOADED)
        result = self.lifecycle.adopt_run(RELOADED, token, RUN_ID)
        self.assertIs(result.get("ok"), True, result)
        self.assertNotEqual(RELOADED.pid, LAUNCHER.pid)
        self.assertEqual(RELOADED.session_id, LAUNCHER.session_id)

    def test_client_reopen_loses_the_launcher(self) -> None:
        self.shape("unknown")
        stamp_launcher(self.lifecycle, RUN_ID, LAUNCHER)
        token = self._lease(REOPENED)
        result = self.lifecycle.adopt_run(REOPENED, token, RUN_ID)
        self.assertEqual(result.get("error"), "run_protected", result)
        self.assertEqual(result.get("use_state"), "unknown")
        self.assertEqual(self.store.get(RUN_ID).state, "RUNNING_IDLE")

    def test_input_after_the_snapshot_is_not_abandoned(self) -> None:
        # F2: the sample lands inside _probes_for_snapshot, after the clocks
        # were copied and before the adopt decides. The deciding read has to
        # see it.
        row = self.shape("idle_waiting")
        self.assertEqual(row["use_state"], "idle_waiting", row)
        self.bindings.bound.add(RUN_ID)
        self.lifecycle._invalidate_box_cache()
        self.assertEqual(self._row()["use_state"], "abandoned")
        stamp_launcher(self.lifecycle, RUN_ID, LAUNCHER)
        token = self._lease(OTHER)
        life = self.lifecycle
        original = life._probes_for_snapshot
        observed: dict[str, object] = {}

        def input_during_classification(snapshot, *, use_cache):
            if "injected" not in observed:
                observed["injected"] = True
                self._human(time.time())
                observed["after_input"] = self._row()["use_state"]
            return original(snapshot, use_cache=use_cache)

        life._probes_for_snapshot = input_during_classification
        try:
            result = life.adopt_run(OTHER, token, RUN_ID)
        finally:
            life._probes_for_snapshot = original
        self.assertEqual(observed.get("after_input"), "human")
        self.assertEqual(result.get("error"), "run_protected", result)
        self.assertEqual(result.get("use_state"), "human")
        stored = self.store.get(RUN_ID)
        self.assertIsNone(stored.owner_session_id)
        self.assertEqual(stored.state, "RUNNING_IDLE")
        self.assertEqual(self._pending(), [])

    def test_deciding_probe_failure_refuses(self) -> None:
        self.shape("abandoned")
        stamp_launcher(self.lifecycle, RUN_ID, LAUNCHER)
        token = self._lease(OTHER)
        life = self.lifecycle
        original = life._probes_for_snapshot
        calls = {"n": 0}

        def fail_the_deciding_probe(snapshot, *, use_cache):
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("deciding probe down")
            return original(snapshot, use_cache=use_cache)

        life._probes_for_snapshot = fail_the_deciding_probe
        try:
            result = life.adopt_run(OTHER, token, RUN_ID)
        finally:
            life._probes_for_snapshot = original
        self.assertGreater(calls["n"], 1)
        self.assertEqual(result.get("error"), "run_protected", result)
        self.assertEqual(result.get("use_reason"), "unclassified")
        self.assertIsNone(self.store.get(RUN_ID).owner_session_id)
        self.assertEqual(self.store.get(RUN_ID).state, "RUNNING_IDLE")
        self.assertEqual(self._pending(), [])

    def _client_config(self) -> ServerConfig:
        return ServerConfig(
            mode="client",
            key="reload-key",
            port=19437,
            client_platform="codex",
            auto_spawn_daemon=False,
            log_sink=lambda _message: None,
        )

    def test_reload_after_release_keeps_the_launcher(self) -> None:
        # F1: the real worker path. session_release clears the lease carrier.
        # The next ClientRuntime of the same supervisor must still be the launcher.
        carrier = self.root / "session-handoff.json"
        config = self._client_config()
        with patch.dict(os.environ, {session_handoff.HANDOFF_ENV: str(carrier)}):
            old = _fixture_client_runtime(config)
            self.shape("idle")
            stamp_launcher(self.lifecycle, RUN_ID, old.identity)
            old._mirror_lease_to_carrier("test-token", "test-lease")
            self.assertTrue(carrier.is_file())
            old._mirror_lease_to_carrier(None, None)
            self.assertFalse(carrier.exists())
            identity_file = session_handoff.supervisor_identity_path(carrier)
            self.assertTrue(identity_file.is_file())
            reloaded = _fixture_client_runtime(config)
        self.assertEqual(reloaded.identity, old.identity)
        self.assertEqual(reloaded.identity.session_id, old.identity.session_id)
        token = self._lease(reloaded.identity)
        result = self.lifecycle.adopt_run(reloaded.identity, token, RUN_ID)
        self.assertIs(result.get("ok"), True, result)
        self.assertEqual(self.store.get(RUN_ID).owner_session_id, old.identity.session_id)

    def test_reopen_and_daemon_restart_lose_the_reloaded_launcher(self) -> None:
        carrier = self.root / "session-handoff.json"
        reopened_carrier = self.root / "other-supervisor" / "session-handoff.json"
        config = self._client_config()
        with patch.dict(os.environ, {session_handoff.HANDOFF_ENV: str(carrier)}):
            old = _fixture_client_runtime(config)
            self.shape("idle")
            stamp_launcher(self.lifecycle, RUN_ID, old.identity)
            old._mirror_lease_to_carrier("test-token", "test-lease")
            old._mirror_lease_to_carrier(None, None)
            reloaded = _fixture_client_runtime(config)
        self.assertEqual(reloaded.identity.session_id, old.identity.session_id)
        with patch.dict(os.environ, {session_handoff.HANDOFF_ENV: str(reopened_carrier)}):
            reopened = _fixture_client_runtime(config)
        self.assertNotEqual(reopened.identity.session_id, old.identity.session_id)
        token = self._lease(reopened.identity)
        refused = self.lifecycle.adopt_run(reopened.identity, token, RUN_ID)
        self.assertEqual(refused.get("error"), "run_protected", refused)
        self.assertEqual(self.store.get(RUN_ID).state, "RUNNING_IDLE")
        with patch.dict(os.environ, {session_handoff.HANDOFF_ENV: ""}):
            unsupervised = _fixture_client_runtime(config)
        self.assertNotEqual(unsupervised.identity.session_id, old.identity.session_id)
        restarted = self._lifecycle()
        self.lifecycle = restarted
        self.assertEqual(restarted._launched_by, {})
        token = self._lease(reloaded.identity)
        forgotten = restarted.adopt_run(reloaded.identity, token, RUN_ID)
        self.assertEqual(forgotten.get("error"), "run_protected", forgotten)
        self.assertEqual(self.store.get(RUN_ID).state, "RUNNING_IDLE")

    def test_live_carrier_beats_the_supervisor_file(self) -> None:
        carrier = self.root / "session-handoff.json"
        config = self._client_config()
        with patch.dict(os.environ, {session_handoff.HANDOFF_ENV: str(carrier)}):
            first = _fixture_client_runtime(config)
            session_handoff.write_handoff(
                carrier,
                identity=OTHER,
                lease_token="carried-token",
                lease_id="carried-lease",
                generation=1,
            )
            carried = _fixture_client_runtime(config)
        self.assertEqual(carried.identity, OTHER)
        self.assertNotEqual(carried.identity.session_id, first.identity.session_id)
        self.assertEqual(carried.active_lease_token, "carried-token")

    def test_input_inside_the_durable_write_stays_protected(self) -> None:
        # The deciding classification has already dropped _activity_lock.
        # Input that lands inside manifest.replace, before the bytes, is
        # still this run's human use and must not leave a foreign owner.
        self.shape("idle_waiting")
        self.bindings.bound.add(RUN_ID)
        self.lifecycle._invalidate_box_cache()
        self.assertEqual(self._row()["use_state"], "abandoned")
        stamp_launcher(self.lifecycle, RUN_ID, LAUNCHER)
        token = self._lease(OTHER)
        original = self.store.replace
        observed: dict[str, object] = {}

        def sample_before_replace(run):
            if "owner_before_replace" not in observed:
                observed["owner_before_replace"] = self.store.get(RUN_ID).owner_session_id
                self._human(time.time())
                observed["use_state_before_replace"] = self._row()["use_state"]
            return original(run)

        self.store.replace = sample_before_replace
        try:
            result = self.lifecycle.adopt_run(OTHER, token, RUN_ID)
        finally:
            self.store.replace = original
        stored = self.store.get(RUN_ID)
        self.assertIsNone(observed["owner_before_replace"])
        self.assertEqual(observed["use_state_before_replace"], "human")
        self.assertIsNone(result.get("ok"))
        self.assertEqual(result.get("error"), "run_protected", result)
        self.assertEqual(result.get("use_state"), "human")
        self.assertIsNone(stored.owner_session_id)
        self.assertEqual(stored.state, "RUNNING_IDLE")
        self.assertFalse(self.lifecycle._adoption_marker_path().exists())
        self.assertEqual(self._pending(), [])

    def test_crash_after_the_owner_write_reverts_and_keeps_the_trace(self) -> None:
        # Residue of a kill between manifest.replace and the revert: the
        # foreign owner is durable, and the marker says human input won.
        self.shape("abandoned")
        life = self.lifecycle
        with life._activity_lock:
            life._note_pending_adoption_locked(RUN_ID, OTHER.session_id, "lease-crash")
            life._invalidate_pending_adoption_locked(RUN_ID, "human")
        run = self.store.get(RUN_ID)
        assert run is not None
        run.owner_session_id = OTHER.session_id
        run.owner_lease_id = "lease-crash"
        run.state = "RUNNING"
        self.store.replace(run)
        self.assertEqual(self.store.get(RUN_ID).owner_session_id, OTHER.session_id)
        restarted = self._lifecycle()
        self.lifecycle = restarted
        stored = self.store.get(RUN_ID)
        assert stored is not None
        self.assertIsNone(stored.owner_session_id)
        self.assertEqual(stored.state, "RUNNING_IDLE")
        self.assertFalse(restarted._adoption_marker_path().exists())
        settled = json.loads(
            restarted._adoption_settled_path().read_text(encoding="utf-8")
        )
        self.assertTrue(settled["settled"])
        self.assertTrue(settled["reverted"])
        self.assertTrue(settled["invalidated"])
        self.assertEqual(settled["invalidated_by"], "human")
        self.assertEqual(settled["owner_session_id"], OTHER.session_id)

    def test_copied_supervisor_identity_is_not_the_launcher(self) -> None:
        carrier_a = self.root / "supervisor-a" / "session-handoff.json"
        carrier_b = self.root / "supervisor-b" / "session-handoff.json"
        config = self._client_config()
        with patch.dict(os.environ, {session_handoff.HANDOFF_ENV: str(carrier_a)}):
            original = _fixture_client_runtime(config)
            self.shape("idle")
            stamp_launcher(self.lifecycle, RUN_ID, original.identity)
            original._mirror_lease_to_carrier("test-token", "test-lease")
            original._mirror_lease_to_carrier(None, None)
        identity_a = session_handoff.supervisor_identity_path(carrier_a)
        identity_b = session_handoff.supervisor_identity_path(carrier_b)
        identity_b.parent.mkdir()
        shutil.copyfile(identity_a, identity_b)
        session_handoff.clear_supervisor_identity(identity_a)
        with patch.dict(os.environ, {session_handoff.HANDOFF_ENV: str(carrier_b)}):
            impostor = _fixture_client_runtime(config)
        self.assertNotEqual(impostor.identity.session_id, original.identity.session_id)
        self.assertNotEqual(impostor.identity, original.identity)
        copied = json.loads(identity_b.read_text(encoding="utf-8"))
        self.assertEqual(copied["identity"]["session_id"], original.identity.session_id)
        token = self._lease(impostor.identity)
        result = self.lifecycle.adopt_run(impostor.identity, token, RUN_ID)
        self.assertIsNone(result.get("ok"))
        self.assertEqual(result.get("error"), "run_protected", result)
        self.assertIsNone(self.store.get(RUN_ID).owner_session_id)
        self.assertEqual(self.store.get(RUN_ID).state, "RUNNING_IDLE")

    def test_daemon_restart_forgets_the_launcher(self) -> None:
        self.shape("abandoned")
        stamp_launcher(self.lifecycle, RUN_ID, LAUNCHER)
        before = self.store.paths.runs_path.read_bytes()
        restarted = self._lifecycle()
        self.lifecycle = restarted
        self.assertEqual(restarted._launched_by, {})
        row = self._row()
        # Clocks live in daemon memory. A restart starts them at its own origin,
        # so a run that was abandoned reads idle and stays protected.
        self.assertEqual(row["use_state"], "idle", row)
        self.assertIsNone(row["launched_by"])
        token = self._lease(LAUNCHER)
        result = restarted.adopt_run(LAUNCHER, token, RUN_ID)
        self.assertEqual(result.get("error"), "run_protected", result)
        self.assertEqual(self.store.paths.runs_path.read_bytes(), before)
        self.assertEqual(self.store.get(RUN_ID).state, "RUNNING_IDLE")

    def test_public_prefix_is_not_authority(self) -> None:
        self.shape("idle")
        stamp_launcher(self.lifecycle, RUN_ID, PREFIX_LAUNCHER)
        row = self._row()
        self.assertEqual(row["launched_by"]["session"], PREFIX)
        self.assertTrue(caller_may_adopt_ownerless(row, PREFIX_OTHER.session_id))
        box = self.lifecycle.box_occupancy()
        self.assertIs(
            box_available_for(box, PREFIX_OTHER.session_id)["adopt"], True
        )
        token = self._lease(PREFIX_OTHER)
        result = self.lifecycle.adopt_run(PREFIX_OTHER, token, RUN_ID)
        self.assertEqual(result.get("error"), "run_protected", result)
        self.assertNotEqual(PREFIX_OTHER.session_id, PREFIX_LAUNCHER.session_id)

    def test_launcher_adopts_when_classification_throws(self) -> None:
        self.shape("unknown")
        stamp_launcher(self.lifecycle, RUN_ID, LAUNCHER)

        def explode(*_args, **_kwargs):
            raise RuntimeError("classification down")

        self.lifecycle.box_occupancy = explode
        token = self._lease(LAUNCHER)
        result = self.lifecycle.adopt_run(LAUNCHER, token, RUN_ID)
        self.assertIs(result.get("ok"), True, result)

    def test_stranger_is_refused_when_classification_throws(self) -> None:
        self.shape("abandoned")
        stamp_launcher(self.lifecycle, RUN_ID, LAUNCHER)

        def explode(*_args, **_kwargs):
            raise RuntimeError("classification down")

        self.lifecycle.box_occupancy = explode
        token = self._lease(OTHER)
        result = self.lifecycle.adopt_run(OTHER, token, RUN_ID)
        self.assertEqual(result.get("error"), "run_protected", result)
        self.assertIsNone(result.get("use_state"))
        self.assertEqual(result.get("use_reason"), "unclassified")
        self.assertEqual(self._pending(), [])
        self.assertEqual(self.store.get(RUN_ID).state, "RUNNING_IDLE")


class HttpAdoptionMatrixTest(unittest.TestCase):
    """Grant, explicit adopt, and the worker stop path, through the daemon HTTP API."""

    def setUp(self) -> None:
        self.srv = DaemonHttpServer(_config(key="adopt-key"), adopt_fixture=False)
        self.srv.start()
        self.life = self.srv.state.lifecycle
        run = self.life.manifest.get("test-run")
        self.assertIsNotNone(run)
        self.pid = run.processes[0].pid
        self.life.diag_probe = lambda: {
            "known": True,
            "processes": [{"pid": self.pid, "name": "DayZDiag_x64.exe"}],
        }
        self.life.port_probe = lambda: {"known": True, "holders": []}
        self.holder: ClientIdentity | None = None
        self.token: str | None = None

    def tearDown(self) -> None:
        self._release()
        self.srv.stop()

    def _release(self) -> None:
        if self.holder is None or self.token is None:
            return
        _http(
            self.srv.base,
            "POST",
            "/session/release",
            self.srv.key,
            {"identity": self.holder.to_payload(), "lease_token": self.token},
        )
        self.holder = None
        self.token = None
        _wait_until_lease_claimable(self.srv.state.coordination)

    def _clear_use(self) -> None:
        with self.life._activity_lock:
            key = self.life._activity_key("test-run")
            for table in (
                self.life._ownerless_since,
                self.life._human_input_at,
                self.life._uncertain_input_at,
                self.life._launched_by,
            ):
                table.pop(key, None)
            self.life._input_good_at = None
            self.life._signal_recovered_at = None
            self.life._input_attributor = InputAttributor(bridge_s=INPUT_SIGNAL_STALE_S)
        self.life._invalidate_box_cache()

    def _good(self, at: float, *, foreground: int, tick: int) -> None:
        self.life.record_input_sample(
            InputSample(
                at=at, ok=True, last_input_tick=tick, idle_ms=0, foreground_pid=foreground
            )
        )

    def shape(self, kind: str, launcher: ClientIdentity | None) -> dict[str, object]:
        self._clear_use()
        run = self.life.manifest.get("test-run")
        record = run.processes[0]
        role = "server"
        state = "RUNNING_IDLE"
        generation = self.life.daemon_generation
        if kind in {"unknown", "human", "idle_waiting"}:
            role = "client"
        if kind == "unreconciled":
            state = "UNRECONCILED"
        if kind == "idle_waiting":
            generation = "gen-before"
        run.processes = [replace(record, role=role)]
        run.state = state
        run.owner_session_id = None
        run.owner_lease_id = None
        run.daemon_generation_at_launch = generation
        self.life.manifest.replace(run)
        if kind == "human":
            now = time.time()
            self._good(now - 0.25, foreground=self.pid, tick=1000)
            self.life.record_input_sample(
                InputSample(
                    at=now,
                    ok=True,
                    last_input_tick=1100,
                    idle_ms=0,
                    foreground_pid=self.pid,
                )
            )
            self._good(time.time(), foreground=OTHER_WINDOW_PID, tick=1100)
        elif kind == "idle_waiting":
            start = time.time() - RUN_IDLE_CUT_S - 2.0
            with self.life._activity_lock:
                self.life._ownerless_since[self.life._activity_key("test-run")] = start
            at = start
            until = time.time()
            while at < until:
                self._good(at, foreground=OTHER_WINDOW_PID, tick=100)
                at += 1.5
            self._good(until, foreground=OTHER_WINDOW_PID, tick=100)
        elif kind in {"idle", "abandoned"}:
            age = 0.0 if kind == "idle" else RUN_IDLE_CUT_S + 1.0
            with self.life._activity_lock:
                self.life._ownerless_since[self.life._activity_key("test-run")] = (
                    time.time() - age
                )
        if launcher is not None:
            stamp_launcher(self.life, "test-run", launcher)
        self.life._invalidate_box_cache()
        box = self.life.box_occupancy()
        for item in box["runs"]:
            if item["run_id"] == "test-run":
                return item
        self.fail(f"no row: {box['runs']}")

    def _expect_row(self, kind: str, row: dict[str, object]) -> None:
        expected = {
            "unknown": ("unknown", "input_signal_unavailable"),
            "human": ("human", None),
            "idle": ("idle", None),
            "idle_waiting": ("idle_waiting", "previous_generation"),
            "abandoned": ("abandoned", "client_gone"),
            "unreconciled": (None, None),
        }[kind]
        self.assertEqual((row.get("use_state"), row.get("use_reason")), expected, row)

    def _acquire(self, client: ClientIdentity) -> dict:
        self._release()
        status, body = _http(
            self.srv.base,
            "POST",
            "/session/acquire",
            self.srv.key,
            {"identity": client.to_payload(), "purpose": "adopt"},
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body.get("status"), "active", body)
        self.holder = client
        self.token = body["lease_token"]
        return body

    def _post(self, action: str, client: ClientIdentity) -> tuple[int, dict]:
        return _http(
            self.srv.base,
            "POST",
            f"/lifecycle/{action}",
            self.srv.key,
            {
                "identity": client.to_payload(),
                "lease_token": self.token,
                "run_id": "test-run",
            },
        )

    def _allowed(self, kind: str, caller: str) -> bool:
        return caller == "launcher" or kind in {"abandoned", "unreconciled"}

    def test_grant(self) -> None:
        for kind in ("unknown", "human", "idle", "idle_waiting", "abandoned", "unreconciled"):
            for caller_name, client in (("launcher", LAUNCHER), ("other", OTHER)):
                with self.subTest(kind=kind, caller=caller_name):
                    self._release()
                    row = self.shape(
                        kind, LAUNCHER if caller_name == "launcher" else None
                    )
                    self._expect_row(kind, row)
                    granted = self._acquire(client)
                    adopted = granted.get("adopted_run") or {}
                    if self._allowed(kind, caller_name):
                        self.assertIs(adopted.get("ok"), True, granted)
                        self.assertEqual(adopted.get("state"), "RUNNING")
                        continue
                    self.assertEqual(granted.get("status"), "active")
                    self.assertIn("lease_token", granted)
                    self.assertIs(adopted.get("ok"), False, granted)
                    self.assertEqual(adopted.get("error"), "run_protected")
                    self.assertEqual(adopted.get("use_state"), row["use_state"])
                    if kind in {"idle", "human"}:
                        self.assertGreater(adopted.get("retry_after_s"), 0)
                    else:
                        self.assertNotIn("retry_after_s", adopted)
                    stored = self.life.manifest.get("test-run")
                    self.assertEqual(stored.state, "RUNNING_IDLE")
                    self.assertIsNone(stored.owner_session_id)

    def test_explicit_adopt_and_worker_stop(self) -> None:
        for kind in ("unknown", "human", "idle", "idle_waiting", "abandoned", "unreconciled"):
            for caller_name, client in (("launcher", LAUNCHER), ("other", OTHER)):
                with self.subTest(kind=kind, caller=caller_name):
                    self._release()
                    # EXITED cannot keep processes. STOPPING is not adoptable,
                    # so the grant returns adopted_run null and the explicit
                    # POST /lifecycle/adopt is the call under test.
                    parked = self.life.manifest.get("test-run")
                    parked.state = "STOPPING"
                    parked.owner_session_id = None
                    parked.owner_lease_id = None
                    self.life.manifest.replace(parked)
                    granted = self._acquire(client)
                    self.assertIsNone(granted.get("adopted_run"), granted)
                    row = self.shape(
                        kind, LAUNCHER if caller_name == "launcher" else None
                    )
                    self._expect_row(kind, row)
                    status, adopted = self._post("adopt", client)
                    if self._allowed(kind, caller_name):
                        self.assertEqual(status, 200, adopted)
                        self.assertIs(adopted.get("ok"), True, adopted)
                        continue
                    self.assertEqual(status, 409, adopted)
                    self.assertEqual(adopted.get("error"), "run_protected")
                    self.assertEqual(adopted.get("use_state"), row["use_state"])
                    stop_status, stopped = self._post("stop", client)
                    self.assertEqual(stop_status, 409, stopped)
                    self.assertEqual(stopped.get("error"), "run_not_adopted")
                    stored = self.life.manifest.get("test-run")
                    self.assertEqual(stored.state, "RUNNING_IDLE")
                    self.assertIsNone(stored.owner_session_id)

    def test_protected_grant_keeps_the_lease_for_an_old_heartbeat(self) -> None:
        row = self.shape("idle", None)
        self.assertEqual(row["use_state"], "idle")
        granted = self._acquire(OTHER)
        adopted = granted["adopted_run"]
        self.assertEqual(adopted.get("error"), "run_protected")
        self.assertEqual(granted.get("status"), "active")
        status, heartbeat = _http(
            self.srv.base,
            "POST",
            "/session/heartbeat",
            self.srv.key,
            {"identity": OTHER.to_payload(), "lease_token": self.token},
        )
        self.assertEqual(status, 200, heartbeat)
        stored = self.life.manifest.get("test-run")
        self.assertEqual(stored.state, "RUNNING_IDLE")
        self.assertIsNone(stored.owner_session_id)
        queued_status, queued = _http(
            self.srv.base,
            "POST",
            "/session/acquire",
            self.srv.key,
            {"identity": LAUNCHER.to_payload(), "purpose": "next"},
        )
        self.assertEqual(queued_status, 202, queued)
        self.assertEqual(queued.get("status"), "queued")


class McpProtectionMessageTest(unittest.IsolatedAsyncioTestCase):
    def _config(self) -> ServerConfig:
        return ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            auto_spawn_daemon=False,
            log_sink=lambda _message: None,
        )

    def _box(self, runtime, *, launched: bool, use_state: str = "idle") -> dict:
        prefix = runtime.identity.session_id[:12] if launched else "not-launcher"
        return {
            "occupied": True,
            "runs": [
                {
                    "run_id": "run-protected",
                    "mod": "@M",
                    "label": "lab",
                    "age_s": 1.0,
                    "state": "RUNNING_IDLE",
                    "owner_session": None,
                    "use_state": use_state,
                    "use_reason": None,
                    "idle_s": 1.0,
                    "launched_by": {
                        "platform": "codex",
                        "session": prefix,
                        "started_at_utc": "2026-07-15T00:00:00Z",
                        "task_label": "",
                    },
                }
            ],
            "foreign": [],
            "ports_in_use": [2302],
            "queue": [],
            "scan_known": True,
            "port_scan_known": True,
        }

    async def _run(self, *, launched: bool, takeover: bool, on_busy: str, wait_s: float):
        runtime = _fixture_client_runtime(self._config())
        box = self._box(runtime, launched=launched)
        seen: dict[str, float] = {}

        async def fake_wait(client, wait_s, **kwargs):
            seen["wait_s"] = float(wait_s)
            abort = kwargs.get("abort_if")
            if abort is not None:
                seen["abort"] = abort(box)
            return {"ok": False, "box": box}

        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        stop = AsyncMock(side_effect=AssertionError("must not stop"))
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = server_module.build_app(self._config())
        with (
            patch.object(runtime, "session_status", new=AsyncMock(return_value={"box": box})),
            patch.object(server_module, "execute_wait_for_box", fake_wait),
            patch.object(server_module.dayz_test_tool, "execute_dayz_test_run", execute),
            patch.object(server_module.dayz_test_tool, "execute_dayz_test_stop", stop),
        ):
            payload = _content_json(
                await app.call_tool(
                    "dayz_test_run",
                    {
                        "project": "ExampleMod",
                        "mode": "server",
                        "on_busy": on_busy,
                        "wait_for_box_s": wait_s,
                        "takeover": takeover,
                    },
                )
            )
        return payload, seen, execute, stop

    async def test_queue_adopts_at_once_for_the_launcher(self) -> None:
        payload, seen, execute, stop = await self._run(
            launched=True, takeover=False, on_busy="queue", wait_s=0.2
        )
        execute.assert_not_awaited()
        stop.assert_not_awaited()
        self.assertNotIn("wait_s", seen)
        self.assertEqual(payload.get("reason"), "adopt")
        self.assertEqual(payload.get("error_code"), TAKEOVER_REQUIRED)
        self.assertIsNone(payload.get("queue_offer"))

    async def test_queue_does_not_adopt_for_another_session(self) -> None:
        payload, seen, execute, stop = await self._run(
            launched=False, takeover=False, on_busy="queue", wait_s=0.2
        )
        execute.assert_not_awaited()
        stop.assert_not_awaited()
        self.assertEqual(seen.get("wait_s"), 0.2)
        self.assertIsNone(seen.get("abort"))
        self.assertNotEqual(payload.get("reason"), "adopt")
        self.assertEqual(payload.get("error_code"), "run_protected")
        self.assertEqual(payload.get("use_state"), "idle")
        self.assertIsNotNone(payload.get("queue_offer"))
        self.assertNotIn("takeover=true", str(payload.get("hint") or ""))

    async def test_takeover_true_does_not_stop_a_protected_run(self) -> None:
        payload, _seen, execute, stop = await self._run(
            launched=False, takeover=True, on_busy="fail", wait_s=0.0
        )
        execute.assert_not_awaited()
        stop.assert_not_awaited()
        self.assertEqual(payload.get("error_code"), "run_protected")
        self.assertNotIn("takeover=true", str(payload.get("hint") or ""))

    async def test_stop_precheck_refuses_a_protected_run(self) -> None:
        runtime = _fixture_client_runtime(self._config())
        box = self._box(runtime, launched=False, use_state="human")
        box["runs"][0]["human_input_age_s"] = 10.0
        stop = AsyncMock(side_effect=AssertionError("must not stop"))
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = server_module.build_app(self._config())
        with (
            patch.object(runtime, "session_status", new=AsyncMock(return_value={"box": box})),
            patch.object(server_module.dayz_test_tool, "execute_dayz_test_stop", stop),
        ):
            payload = _content_json(
                await app.call_tool("dayz_test_stop", {"run_id": "run-protected"})
            )
        stop.assert_not_awaited()
        self.assertEqual(payload.get("status"), "failed")
        self.assertEqual(payload.get("error_code"), "run_protected")
        self.assertEqual(payload.get("use_state"), "human")
        self.assertEqual(payload.get("run_id"), "run-protected")
        self.assertGreater(payload.get("retry_after_s"), 0)

    async def test_stop_of_a_foreign_running_run_reaches_the_worker(self) -> None:
        runtime = _fixture_client_runtime(self._config())
        box = {
            "occupied": True,
            "runs": [
                {
                    "run_id": "run-owned",
                    "state": "RUNNING",
                    "owner_session": "someoneelse",
                }
            ],
            "foreign": [],
            "port_scan_known": True,
        }
        stop = AsyncMock(return_value={"status": "succeeded", "run_id": "run-owned"})
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = server_module.build_app(self._config())
        with (
            patch.object(runtime, "session_status", new=AsyncMock(return_value={"box": box})),
            patch.object(server_module.dayz_test_tool, "execute_dayz_test_stop", stop),
        ):
            payload = _content_json(
                await app.call_tool("dayz_test_stop", {"run_id": "run-owned"})
            )
        stop.assert_awaited()
        self.assertEqual(payload.get("status"), "succeeded")

    async def test_protected_grant_is_released_as_box_protected(self) -> None:
        srv = DaemonHttpServer(_config(key="wrap-key"), adopt_fixture=False)
        srv.start()
        try:
            life = srv.state.lifecycle
            run = life.manifest.get("test-run")
            life.diag_probe = lambda: {"known": True, "processes": []}
            life.port_probe = lambda: {"known": True, "holders": []}
            life._invalidate_box_cache()
            row = next(item for item in life.box_occupancy()["runs"] if item["run_id"] == "test-run")
            self.assertNotEqual(row["use_state"], "abandoned", row)
            runtime = _fixture_client_runtime(
                ServerConfig(
                    mode="client",
                    key=srv.key,
                    port=srv.port,
                    client_platform="codex",
                    auto_spawn_daemon=False,
                    log_sink=lambda _message: None,
                )
            )
            _attach_fixture_transport(runtime, srv)
            payload = await runtime.session_acquire_wait("watch the box", 5.0)
            stored = life.manifest.get("test-run")
        finally:
            srv.stop()
        self.assertIs(payload.get("ok"), False, payload)
        self.assertEqual(payload.get("error"), "box_protected")
        self.assertEqual(payload.get("status"), "released")
        self.assertNotIn("lease_token", payload)
        self.assertEqual(payload.get("use_state"), row["use_state"])
        self.assertIsNone(runtime.active_lease_token)
        self.assertFalse(_runtime_holds_lease(runtime))
        self.assertEqual(stored.state, "RUNNING_IDLE")
        self.assertIsNone(stored.owner_session_id)

    async def test_release_failure_keeps_the_token(self) -> None:
        runtime = _fixture_client_runtime(self._config())

        async def acquire(purpose, max_wait_s=None, progress_cb=None):
            runtime._control.active_lease_token = "tok-kept"
            return {
                "status": "active",
                "lease_token": "tok-kept",
                "adopted_run": {
                    "ok": False,
                    "error": "run_protected",
                    "run_id": "test-run",
                    "use_state": "human",
                    "hint": "run_protected: this ownerless run is in use (human)",
                    "retry_after_s": 12.5,
                },
            }

        async def boom(_token):
            raise RuntimeError("nope")

        runtime._control.session_acquire_wait = acquire
        runtime.session_release = boom
        payload = await runtime.session_acquire_wait("watch", 5.0)
        self.assertIs(payload.get("ok"), False, payload)
        self.assertEqual(payload.get("error"), "box_protected")
        self.assertEqual(payload.get("status"), "release_failed")
        self.assertEqual(payload.get("release_error"), "RuntimeError")
        self.assertEqual(payload.get("lease_token"), "tok-kept")
        self.assertEqual(payload.get("retry_after_s"), 12.5)
        self.assertEqual(runtime.active_lease_token, "tok-kept")
        self.assertTrue(_runtime_holds_lease(runtime))


class BlockedOnProtectionTest(unittest.TestCase):
    def test_names_the_state_and_when_it_can_be_abandoned(self) -> None:
        row = {
            "run_id": "run-1",
            "state": "RUNNING_IDLE",
            "owner_session": None,
            "use_state": "idle",
            "use_reason": None,
            "idle_s": 100.0,
        }
        box = {
            "occupied": True,
            "runs": [row],
            "foreign": [],
            "port_scan_known": True,
            "scan_known": True,
        }
        text = _session_status_blocked_on({"owner": None, "box": box}, "stranger")
        self.assertIn("protected", text)
        self.assertIn("idle", text)
        self.assertIn("can be abandoned in 500", text)
        self.assertNotIn("to adopt it", text)
        launcher = "0123456789ab-launcher"
        row["launched_by"] = {"session": launcher[:12]}
        self.assertEqual(
            _session_status_blocked_on({"owner": None, "box": box}, launcher),
            ADOPT_BLOCKED_ON,
        )
        self.assertIs(
            box_available_for(box, launcher)["adopt"],
            True,
        )
        self.assertIs(box_available_for(box, None)["adopt"], False)
