"""fb-20260910-043740-a429: diagnostic overlay on status tools.

Fixtures P1–P6 / N1–N6 from docs/plan-a429.md.
"""

from __future__ import annotations

import asyncio
import copy
import json
import sys
import time
import unittest
import urllib.request
from pathlib import Path
from typing import Callable
from unittest.mock import AsyncMock, patch

from mcp import types

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import daemon, dayz_test_tool, server
from dayz_mcp.server import EXPECTED_BRIDGE_VERSION, Runtime, ServerConfig, build_app
from tests.fence_helpers import bind_both_peers
from tests.test_client_mode import _fixture_client_runtime
from tests.test_mcp_tools import FakePeer, _content_json
from tests.test_session_status_blocked_on import BOX_BLOCKED_ON


ADOPT_BLOCKED_ON = (
    "DayZ test box has an ownerless RUNNING_IDLE run; next: call "
    "session_acquire_wait(purpose=...) to adopt it. dayz_test_run wait_for_box_s "
    "is for a new launch, not this box."
)

_FRESH_MODULES = {
    "server_started_at": 1.0,
    "server_pid": 123,
    "watched_count": 1,
    "stale": [],
    "unreadable": [],
    "unreadable_reasons": {},
}
_STALE_MODULES = {
    **_FRESH_MODULES,
    "stale": ["fixture"],
}


def _status_payload(
    *,
    owner: dict[str, object] | None = None,
    occupied: bool,
    runs: list[dict[str, object]] | None = None,
    foreign: list[object] | None = None,
    port_scan_known: bool | None = True,
) -> dict[str, object]:
    box: dict[str, object] = {
        "occupied": occupied,
        "runs": [] if runs is None else runs,
        "foreign": [] if foreign is None else foreign,
        "ports_in_use": [],
        "queue": [],
        "scan_known": True,
    }
    if port_scan_known is not None:
        box["port_scan_known"] = port_scan_known
    return {
        "owner": owner,
        "queue": [],
        "self": {"state": "none", "position": None},
        "claimable": True,
        "audit_fault": None,
        "lifecycle_recovery_fault": None,
        "operation_tombstones": {
            "count": 0,
            "capacity": 4096,
            "saturated": False,
        },
        "cleanup_degraded": [],
        "daemon_generation": "generation-a",
        "pending_commands": 0,
        "box": box,
    }


def _idle_run(run_id: str = "run-idle") -> dict[str, object]:
    return {
        "run_id": run_id,
        "state": "RUNNING_IDLE",
        "owner_session": None,
        "mod": "@Fixture",
        "label": "idle",
        "age_s": 12.0,
    }


def _daemon_modules(*, stale: list[str] | None = None) -> dict[str, object]:
    return {
        "daemon_started_at": 1.0,
        "watched_count": 2,
        "stale": [] if stale is None else stale,
        "unreadable": [],
    }


async def _protocol_result(app, name: str, arguments: dict | None = None):
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments or {}),
    )
    response = await app._mcp_server.request_handlers[types.CallToolRequest](request)
    return types.CallToolResult.model_validate_json(
        response.root.model_dump_json(by_alias=True)
    )


class A429ClientOverlayTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        if not Path(sys.executable).exists():
            self.skipTest("I1: interpreter missing")
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            auto_spawn_daemon=False,
            log_sink=lambda _message: None,
        )
        self.runtime = _fixture_client_runtime(config)
        self.runtime.bridge_status_payload = AsyncMock(
            return_value={
                "ready": {"ready": False, "reason": "no_run"},
                "daemon_modules": _daemon_modules(),
            }
        )
        self.runtime.session_status = AsyncMock(
            return_value=copy.deepcopy(_status_payload(occupied=False))
        )
        with patch.object(server, "ClientRuntime", return_value=self.runtime):
            self.app, _built = build_app(config)

    async def _session_status(self, payload: dict[str, object]) -> dict:
        status = AsyncMock(return_value=copy.deepcopy(payload))
        with patch.object(self.runtime, "session_status", new=status):
            result = _content_json(await self.app.call_tool("session_status", {}))
        status.assert_awaited_once_with()
        return result

    async def _bridge_status(self) -> dict:
        return _content_json(await self.app.call_tool("bridge_status", {}))

    async def test_p1_n1_fresh_has_null_tools_remediation(self) -> None:
        with patch.object(server._SERVER_SOURCES, "snapshot", return_value=dict(_FRESH_MODULES)):
            status = await self._bridge_status()
        self.assertEqual(status["server_modules"]["status"], "fresh")
        self.assertEqual(status["tool_registry_schema_signal"], "fresh")
        self.assertIs(status["tool_registry_source_stale"], False)
        self.assertIsNone(status["tool_registry_remediation"])
        self.assertNotEqual(status["tool_registry_remediation"], "reopen_mcp_client")
        self.assertIsNone(status["daemon_source_remediation"])
        self.assertIn("ready", status)

    async def test_p2_stale_client_is_scoped_tools_not_daemon(self) -> None:
        with patch.object(server._SERVER_SOURCES, "snapshot", return_value=dict(_STALE_MODULES)):
            status = await self._bridge_status()
        remediation = status["tool_registry_remediation"]
        self.assertNotIsInstance(remediation, str)
        self.assertEqual(remediation["code"], "reopen_mcp_client")
        self.assertEqual(remediation["scope"], "tools")
        self.assertIn("stale_client", remediation["applies_when"])
        self.assertIsNone(status["daemon_source_remediation"])

    async def test_unknown_signal_is_object_not_legacy_string(self) -> None:
        unknown = {
            **_FRESH_MODULES,
            "unreadable": ["fixture"],
            "unreadable_reasons": {"fixture": "source_unreadable_now"},
        }
        with patch.object(server._SERVER_SOURCES, "snapshot", return_value=dict(unknown)):
            status = await self._bridge_status()
        self.assertEqual(status["tool_registry_schema_signal"], "unknown")
        remediation = status["tool_registry_remediation"]
        self.assertNotIsInstance(remediation, str)
        self.assertEqual(remediation["code"], "reopen_mcp_client")
        self.assertEqual(remediation["scope"], "tools")
        self.assertIn("unknown", remediation["applies_when"])
        self.assertIsNone(status["daemon_source_remediation"])

    async def test_n2_daemon_stale_does_not_use_reopen_mcp_client(self) -> None:
        self.runtime.bridge_status_payload = AsyncMock(
            return_value={
                "ready": {"ready": True, "reason": "ready"},
                "daemon_modules": _daemon_modules(stale=["x"]),
            }
        )
        with patch.object(server._SERVER_SOURCES, "snapshot", return_value=dict(_FRESH_MODULES)):
            status = await self._bridge_status()
        self.assertIsNone(status["tool_registry_remediation"])
        daemon_fix = status["daemon_source_remediation"]
        self.assertEqual(daemon_fix["scope"], "daemon")
        self.assertEqual(daemon_fix["code"], "none")
        self.assertIn("do not kill", daemon_fix["applies_when"])
        self.assertNotEqual(daemon_fix.get("code"), "reopen_mcp_client")

    async def test_p4_and_p4neg_idle_run_is_adopt_not_fifo(self) -> None:
        result = await self._session_status(
            _status_payload(occupied=True, runs=[_idle_run()])
        )
        self.assertEqual(
            result["box"]["available_for"],
            {"new_launch": False, "adopt": True},
        )
        self.assertEqual(result["blocked_on"], ADOPT_BLOCKED_ON)
        self.assertNotEqual(result["blocked_on"], BOX_BLOCKED_ON)
        self.assertNotIn("join the box FIFO", result["blocked_on"])

    async def test_p5_free_box_is_new_launch(self) -> None:
        result = await self._session_status(_status_payload(occupied=False))
        self.assertEqual(
            result["box"]["available_for"],
            {"new_launch": True, "adopt": False},
        )
        self.assertIsNone(result["blocked_on"])

    async def test_n4_multiple_idle_runs_are_not_adoptable(self) -> None:
        result = await self._session_status(
            _status_payload(
                occupied=True,
                runs=[_idle_run("run-a"), _idle_run("run-b")],
            )
        )
        self.assertEqual(
            result["box"]["available_for"],
            {"new_launch": False, "adopt": False},
        )
        self.assertNotEqual(result["blocked_on"], ADOPT_BLOCKED_ON)
        self.assertNotIn("to adopt it", result["blocked_on"] or "")

    async def test_n5_unreadable_port_scan_is_neither_launch_nor_adopt(self) -> None:
        result = await self._session_status(
            _status_payload(
                occupied=True,
                runs=[_idle_run()],
                port_scan_known=False,
            )
        )
        self.assertEqual(
            result["box"]["available_for"],
            {"new_launch": False, "adopt": False},
        )
        self.assertIn("port_scan_unknown", result["blocked_on"])
        self.assertNotEqual(result["blocked_on"], BOX_BLOCKED_ON)
        self.assertNotEqual(result["blocked_on"], ADOPT_BLOCKED_ON)

    async def test_p6_status_tools_do_not_spawn_or_kill(self) -> None:
        from contextlib import ExitStack

        patches = [
            patch.object(daemon, "spawn_detached"),
            patch.object(dayz_test_tool, "execute_dayz_test_run"),
            patch.object(dayz_test_tool, "execute_dayz_test_stop"),
        ]
        try:
            import psutil
        except ImportError:
            psutil = None
        else:
            patches.append(patch.object(psutil.Process, "kill"))
        with ExitStack() as stack:
            spies = [stack.enter_context(item) for item in patches]
            bridge = await _protocol_result(self.app, "bridge_status")
            session = await _protocol_result(self.app, "session_status")
        self.assertFalse(bridge.isError)
        self.assertFalse(session.isError)
        for spy in spies:
            spy.assert_not_called()


class A429EmbeddedOverlayTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.key = "test-key"
        self.peers: list[FakePeer] = []
        self.runtimes: list[Runtime] = []

    async def asyncTearDown(self) -> None:
        await asyncio.gather(*(asyncio.to_thread(peer.stop) for peer in self.peers))
        await asyncio.gather(
            *(asyncio.to_thread(runtime.stop_loopback) for runtime in self.runtimes)
        )

    def build_started(self) -> tuple[object, Runtime]:
        app, runtime = build_app(
            ServerConfig(key=self.key, port=0, log_sink=lambda _message: None)
        )
        runtime.start_loopback()
        bind_both_peers(runtime.state)
        self.runtimes.append(runtime)
        return app, runtime

    def start_peer(
        self, runtime: Runtime, peer: str, version: str | None = None
    ) -> FakePeer:
        fake = FakePeer(runtime, self.key, peer, version=version)
        fake.start()
        self.peers.append(fake)
        return fake

    async def wait_for(self, predicate: Callable[[], bool], timeout_s: float = 1.0) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.02)
        self.fail("condition not reached")

    async def test_p3_n3_ready_true_with_historical_counters(self) -> None:
        app, runtime = self.build_started()
        version = f"{EXPECTED_BRIDGE_VERSION}~1.29.0"
        self.start_peer(runtime, "server", version=version)
        self.start_peer(runtime, "client", version=version)
        await self.wait_for(
            lambda: (
                runtime.status()["server_peer"]["version_state"] == "ok"
                and runtime.status()["client_peer"]["version_state"] == "ok"
                and runtime.status()["server_peer"]["last_poll_age_s"] is not None
                and runtime.status()["client_peer"]["last_poll_age_s"] is not None
            )
        )
        original = runtime.bridge_status_payload

        async def with_rejects():
            payload = await original()
            fence = dict(payload.get("fence") or {})
            rejects = dict(fence.get("mutation_rejects_by_code") or {})
            rejects["legacy_unbound"] = 1
            fence["mutation_rejects_by_code"] = rejects
            payload["fence"] = fence
            return payload

        runtime.bridge_status_payload = with_rejects
        status = _content_json(await app.call_tool("bridge_status", {}))
        self.assertIs(status["ready"]["ready"], True)
        meta = status["fence"]["mutation_rejects_meta"]
        self.assertIs(meta["blocks_now"], False)
        self.assertEqual(meta["kind"], "historical")
        self.assertEqual(status["fence"]["mutation_rejects_by_code"]["legacy_unbound"], 1)
        self.assertIs(type(status["fence"]["mutation_rejects_by_code"]["legacy_unbound"]), int)

    async def test_n6_http_status_keeps_int_counters_and_omits_mcp_overlay(self) -> None:
        _app, runtime = self.build_started()
        host, port = runtime.loopback.httpd.server_address
        url = f"http://{host}:{port}/status?key={self.key}"
        with urllib.request.urlopen(url, timeout=2.0) as response:
            raw = json.loads(response.read().decode("utf-8"))
        encoded = json.dumps(raw)
        for key in (
            "tool_registry_remediation",
            "available_for",
            "daemon_source_remediation",
            "mutation_rejects_meta",
        ):
            self.assertNotIn(key, raw)
            self.assertNotIn(key, encoded)
        rejects = raw["fence"]["mutation_rejects_by_code"]
        for value in rejects.values():
            self.assertIs(type(value), int)


if __name__ == "__main__":
    unittest.main()
