"""Lease contract text, stale acquire annotation, and retired-run traces."""

from __future__ import annotations

import copy
import json
import sys
import threading
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import dayz_test_tool, loopback, server
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore, RunRecord
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.server import ServerConfig, build_app
from dayz_mcp.session_coordination import SESSION_TTL_S, SessionCoordinator
from tests.fence_helpers import bind_both_peers
from tests.steam_helpers import FakeSteamGate
from tests.test_client_mode import _fixture_client_runtime
from tests.test_mcp_tools import _content_json
from tests.test_daemon import IDENTITY, _http
from tests.test_process_lifecycle import FakeGuard, legacy_process
from tests.test_session_coordination import CleanupSink, FakeClock, _identity
from tests.test_session_http import SnapshotStore
from tests.test_session_status_blocked_on import _status_payload

_COMMIT_RETIREMENT_TRIPLES = (
    ("lifecycle_stop_outcome", "stopped", "stopped"),
    ("lifecycle_owner_released", "released", "released"),
    ("lifecycle_recovery_repair", "confirmed_repair", "repaired"),
    ("lifecycle_manifest_recovery", "backup_restored", "repaired"),
    ("run_reaped", "all_processes_gone_or_foreign", "reaped"),
    ("admin_reconcile", "admin_reconciled", "confirmed"),
)


_STALE_WARNING = "tool_registry_stale_reopen_client"
_LEASE_TOOLS = (
    "session_acquire_wait",
    "session_status",
    "session_heartbeat",
    "wait_for",
)
_GEN = "gen-ok"
_RUN_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_RUN_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def _fresh_snapshot() -> dict:
    return {
        "stale": [],
        "unreadable": [],
        "unreadable_reasons": {},
        "watched_count": 3,
        "server_pid": 1,
        "server_started_at": 0.0,
    }


def _stale_snapshot() -> dict:
    payload = _fresh_snapshot()
    payload["stale"] = ["dayz_mcp.server"]
    return payload


def _run_id(index: int) -> str:
    return f"12345678-1234-4234-8234-{index:012d}"


def _diagnostic(run_id: str, **overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "run_id": run_id,
        "daemon_generation_at_launch": _GEN,
        "daemon_generation_current": _GEN,
        "generation_changed": False,
        "event": "run_reaped",
        "reason": "reaped",
        "decision": "retired",
        "state": "EXITED",
    }
    item.update(overrides)
    return item


def _published(item: dict[str, object]) -> dict[str, object]:
    # runs_retired_recently adds client_death_diagnosis (296b); null when this
    # process bound no client dump baseline to the run.
    return {**item, "client_death_diagnosis": None}


class PlenoLeaseAndOrphansTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        self.runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=self.runtime):
            self.app, _built = build_app(config)

    async def test_t2_four_descriptions_name_heartbeat_and_drop_internal_renewal(
        self,
    ) -> None:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        for name in _LEASE_TOOLS:
            with self.subTest(tool=name):
                description = tools[name].description or ""
                self.assertIn("session_heartbeat", description)
                self.assertNotIn("renewal is internal", description)

    async def _acquire_wait(self, snapshot: dict) -> dict:
        async def acquire(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {"status": "active", "lease_token": "tok", "lease_id": "lease-a"}

        with (
            patch.object(self.runtime, "session_acquire_wait", new=acquire),
            patch.object(server._SERVER_SOURCES, "snapshot", return_value=snapshot),
        ):
            return _content_json(
                await self.app.call_tool(
                    "session_acquire_wait", {"purpose": "build"}
                )
            )

    async def test_t2_session_acquire_wait_fresh_registry_is_not_stale(self) -> None:
        result = await self._acquire_wait(_fresh_snapshot())
        self.assertIs(result["caller_tool_registry_stale"], False)
        self.assertNotIn(_STALE_WARNING, result.get("warnings") or [])
        self.assertEqual(result["status"], "active")

    async def test_t2_session_acquire_wait_stale_registry_warns_and_still_grants(
        self,
    ) -> None:
        result = await self._acquire_wait(_stale_snapshot())
        self.assertIs(result["caller_tool_registry_stale"], True)
        self.assertIn(_STALE_WARNING, result.get("warnings") or [])
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["lease_token"], "tok")

    async def _session_status(self, payload: dict[str, object]) -> dict:
        status = AsyncMock(return_value=copy.deepcopy(payload))
        with patch.object(self.runtime, "session_status", new=status):
            result = _content_json(await self.app.call_tool("session_status", {}))
        status.assert_awaited_once_with()
        return result

    async def test_t2_runs_retired_recently_keeps_a_valid_diagnostic(self) -> None:
        payload = _status_payload(claimable=True, occupied=False)
        payload["retired_run_diagnostics"] = [_diagnostic(_RUN_A)]
        result = await self._session_status(payload)
        self.assertEqual(
            result["runs_retired_recently"], [_published(_diagnostic(_RUN_A))]
        )
        self.assertNotIn("retired_run_diagnostics", result)

    async def test_t2_runs_retired_recently_drops_path_non_token_and_extra_field(
        self,
    ) -> None:
        payload = _status_payload(claimable=True, occupied=False)
        payload["retired_run_diagnostics"] = [
            _diagnostic(r"C:\Users\x\run.log"),
            _diagnostic(_RUN_A, daemon_generation_current="old gen"),
            _diagnostic(_RUN_B, pid=4321),
            _diagnostic(_RUN_A),
        ]
        result = await self._session_status(payload)
        self.assertEqual(
            result["runs_retired_recently"], [_published(_diagnostic(_RUN_A))]
        )

    async def test_t2_runs_retired_recently_caps_at_16_newest_first(self) -> None:
        newest_first = [_diagnostic(_run_id(i)) for i in range(17)]
        payload = _status_payload(claimable=True, occupied=False)
        payload["retired_run_diagnostics"] = newest_first
        result = await self._session_status(payload)
        kept = result["runs_retired_recently"]
        self.assertEqual(len(kept), 16)
        self.assertEqual(kept, [_published(item) for item in newest_first[:16]])
        self.assertEqual(kept[0]["run_id"], _run_id(0))
        self.assertNotEqual(kept[-1]["run_id"], _run_id(16))

    async def test_t2_runs_retired_recently_is_null_when_lifecycle_unread(
        self,
    ) -> None:
        payload = _status_payload(claimable=True, occupied=False)
        result = await self._session_status(payload)
        self.assertIsNone(result["runs_retired_recently"])
        self.assertIn("runs_retired_recently", result)

    async def test_t2_r2_session_heartbeat_keeps_low_level_marker(self) -> None:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        description = tools["session_heartbeat"].description or ""
        self.assertTrue(
            description.startswith("LOW-LEVEL: "),
            description,
        )
        self.assertIn("session_acquire_wait", description)
        self.assertIn("run_not_owned", description)

    async def test_t2_r4_session_status_does_not_renew_and_the_contract_says_so(
        self,
    ) -> None:
        client = _identity("a")
        later = SESSION_TTL_S

        def _coordinator(clock: FakeClock) -> SessionCoordinator:
            return SessionCoordinator(
                time_fn=clock,
                audit=lambda _event: True,
                cleanup=CleanupSink(),
            )

        try:
            clock = FakeClock()
            coordinator = _coordinator(clock)
            acquire_status, granted = coordinator.acquire(client, "drive")
        except Exception as exc:
            self.fail(str(exc))
        self.assertEqual(acquire_status, 200)
        try:
            clock.value = 60.0
            coordinator.status(client)
            clock.value = 119.0
            still = coordinator.status(client)
            clock.value = later
            gone = coordinator.status(client)
        except Exception as exc:
            self.fail(str(exc))
        self.assertIsNotNone(still.get("owner"))
        self.assertIsNone(gone.get("owner"))

        try:
            clock_hb = FakeClock()
            coordinator_hb = _coordinator(clock_hb)
            hb_acquire_status, hb_granted = coordinator_hb.acquire(client, "drive")
        except Exception as exc:
            self.fail(str(exc))
        self.assertEqual(hb_acquire_status, 200)
        try:
            clock_hb.value = 60.0
            hb_status, _hb_body = coordinator_hb.heartbeat(
                client, hb_granted["lease_token"]
            )
            clock_hb.value = later
            kept = coordinator_hb.status(client)
        except Exception as exc:
            self.fail(str(exc))
        self.assertEqual(hb_status, 200)
        self.assertIsNotNone(kept.get("owner"))

        tools = {tool.name: tool for tool in await self.app.list_tools()}
        for name in _LEASE_TOOLS:
            with self.subTest(tool=name):
                description = tools[name].description or ""
                self.assertIn("session_status does not renew", description)
        readme = (Path(__file__).resolve().parents[1] / "README-mcp.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("session_status does not renew", readme.replace("`", ""))
        self.assertNotIn("only runs inside `dayz_test_run`", readme)


class PlenoLeaseAndOrphansRound4Test(unittest.TestCase):
    def test_t2_r4_retired_diagnostics_publish_only_tokens(self) -> None:
        huge_event = "E" * (100 * 1024)
        path_reason = r"C:\Users\x\secret"
        space_decision = "has space"
        bad = [
            _diagnostic("pid=4321"),
            _diagnostic(_RUN_A, reason=path_reason),
            _diagnostic(_RUN_A, event=huge_event),
            _diagnostic(_RUN_A, decision=space_decision),
        ]
        kept = [
            _diagnostic(
                uuid.uuid4().hex,
                event=event,
                reason=reason,
                decision=decision,
            )
            for event, reason, decision in _COMMIT_RETIREMENT_TRIPLES
        ]
        try:
            result = dayz_test_tool._runs_retired_recently(bad + kept)
        except Exception as exc:
            self.fail(str(exc))
        serialized = json.dumps(result)
        self.assertNotIn("pid=4321", serialized)
        self.assertNotIn(json.dumps(path_reason).strip('"'), serialized)
        self.assertNotIn(huge_event, serialized)
        self.assertNotIn(space_decision, serialized)
        self.assertEqual(result, [_published(item) for item in kept])

    def test_t2_r4_retired_run_diagnostics_does_not_run_the_legacy_gate(self) -> None:
        temporary = TemporaryDirectory()
        try:
            root = Path(temporary.name)
            paths = RuntimePaths(
                root / "runtime",
                root / "runtime" / "audit",
                root / "runtime" / "coordination.json",
                root / "runtime" / "runs.json",
            )
            store = RunManifestStore(paths)
            run = RunRecord(
                "run-existing",
                "A",
                "lease-A",
                "RUNNING",
                "same",
                "@SameMod",
                "profiles",
                "mission",
                [legacy_process(77)],
            )
            store.add(run)
            stored = store.get("run-existing")
            self.assertIsNotNone(stored)
            self.assertEqual(stored.state, "RUNNING")
            self.assertEqual(stored.owner_session_id, "A")
            self.assertEqual(stored.owner_lease_id, "lease-A")
            self.assertTrue(
                any(
                    record.identity_scheme == "legacy-wmi-v1"
                    for record in stored.processes
                )
            )

            class CountingAudit:
                def __init__(self) -> None:
                    self.calls = 0

                def __call__(self, event: dict[str, object]) -> bool:
                    self.calls += 1
                    return True

            audit = CountingAudit()
            lifecycle = ProcessLifecycle(
                steam_gate=FakeSteamGate(),
                coordinator=SessionCoordinator(audit=lambda _event: True),
                manifest=store,
                audit=audit,
                guard=FakeGuard(),
                retail_probe=lambda: {"known": True, "processes": []},
                game_path=root,
            )
            before = paths.runs_path.read_bytes()
            audit_calls_before = audit.calls
            try:
                lifecycle.retired_run_diagnostics()
            except Exception as exc:
                self.fail(str(exc))
            self.assertEqual(paths.runs_path.read_bytes(), before)
            self.assertEqual(audit.calls, audit_calls_before)
            after = store.get("run-existing")
            self.assertIsNotNone(after)
            self.assertEqual(after.state, stored.state)
            self.assertEqual(after.owner_session_id, stored.owner_session_id)
            self.assertEqual(after.owner_lease_id, stored.owner_lease_id)
        finally:
            temporary.cleanup()


class PlenoLeaseAndOrphansRound2Test(unittest.TestCase):
    def test_t2_r2_session_status_does_not_call_lifecycle_status(self) -> None:
        diagnostic = _diagnostic(_RUN_A)

        class Lifecycle:
            def __init__(self) -> None:
                self.calls = 0

            def status(self, _client) -> dict[str, object]:
                self.calls += 1
                return {"runs": []}

            def retired_run_diagnostics(self) -> list[dict[str, object]]:
                return [diagnostic]

        key = "session-key"
        store = SnapshotStore()
        state = loopback.ServerState(key)
        bind_both_peers(state)

        def audit(event: dict[str, object]) -> bool:
            return True

        coordinator = SessionCoordinator(
            audit=audit,
            cleanup=lambda session_id, lease_id, reason, vehicle_active: state.cleanup_owner(
                session_id, lease_id, reason, vehicle_active
            ),
        )
        state.coordination = coordinator
        state.retail_probe = lambda: {"known": True, "processes": []}
        state.coordination_store = store
        state.daemon_generation = "generation-test"
        lifecycle = Lifecycle()
        state.lifecycle = lifecycle
        httpd = loopback.create_http_server(
            0, state, log_sink=lambda _message: None, reclaim_orphans=False
        )
        thread = threading.Thread(
            target=httpd.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        thread.start()
        try:
            host, port = httpd.server_address
            base = f"http://{host}:{port}"
            status_code, body = _http(
                base,
                "POST",
                "/session/status",
                key,
                {"identity": IDENTITY},
            )
            self.assertEqual(status_code, 200)
            self.assertEqual(lifecycle.calls, 0)
            self.assertEqual(body["retired_run_diagnostics"], [diagnostic])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
