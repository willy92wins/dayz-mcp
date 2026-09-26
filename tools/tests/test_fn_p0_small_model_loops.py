"""P0 Flash-Next / ~8B loop contract (2026-09-17 handoff).

Tickets: fb-20260917-100411-5edf, fb-20260917-100554-d0e0,
fb-20260917-095637-8011, plus the lifecycle_status next_step family.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# inbox.py reads LOCALAPPDATA at import; knowledge.py imports msvcrt.
os.environ.setdefault("LOCALAPPDATA", str(Path(tempfile.gettempdir()) / "dayz-mcp-localapp"))
if sys.platform != "win32":
    import ctypes

    class _FakeWinDLL:
        def __init__(self, name: str, *args: object, **kwargs: object) -> None:
            self._name = name

        def __getattr__(self, name: str) -> MagicMock:
            return MagicMock(name=f"{self._name}.{name}")

    ctypes.WinDLL = _FakeWinDLL  # type: ignore[misc, assignment]
    sys.modules.setdefault(
        "msvcrt",
        types.SimpleNamespace(
            LK_NBLCK=1,
            LK_UNLCK=2,
            get_osfhandle=lambda fd: fd,
            locking=lambda *_a, **_k: None,
        ),
    )

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import agent_loop, instance_fence, knowledge, knowledge_pack, loopback, server
from dayz_mcp.control_client import ControlClient, ControlClientError
from dayz_mcp.peer_liveness import PEER_STALE_S
from dayz_mcp.server import ServerConfig, build_app


INST_S1 = "33333333-3333-4333-8333-333333333333"
INST_S2 = "55555555-5555-4555-8555-555555555555"


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _profile_dir(root: Path, name: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "dayz_mcp.json").write_text(
        json.dumps(
            {"url": "http://127.0.0.1:8765/", "key": "k", "pollHz": 5},
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return directory


def _bind(state: loopback.ServerState, instance: str, role: str, pid: int) -> None:
    state.install_bound_peer(
        instance=instance,
        role=role,
        pid=pid,
        run_id="run-old",
        creation_time_utc=f"2026-08-18T00:00:{pid % 60:02d}.000000Z",
    )


class HonestReadyAfterLaunchTest(unittest.TestCase):
    def test_prepare_forgets_dead_prelaunch_poll_clocks(self) -> None:
        clock = FakeClock()
        state = loopback.ServerState("k", time_fn=clock)
        _bind(state, INST_S1, "server", 1001)
        state.record_poll(
            "server", "10~1.29.0", instance=INST_S1, source_pid=1001
        )
        clock.advance(60.0)
        before = state.status_snapshot()["peers"]["server"]
        self.assertGreaterEqual(before["last_poll_age_s"], 60.0)
        with tempfile.TemporaryDirectory() as raw:
            state.prepare("run-new", "server", str(_profile_dir(Path(raw), "p")))
        after = state.status_snapshot()["peers"]["server"]
        self.assertIsNone(after["last_poll_age_s"])
        self.assertIsNone(after["bound_last_poll_age_s"])
        self.assertEqual(after["binding_state"], "STARTING")

    def test_fresh_launch_ready_is_binding_not_ready_not_poll_stale(self) -> None:
        clock = FakeClock()
        state = loopback.ServerState("k", time_fn=clock)
        _bind(state, INST_S1, "server", 1001)
        state.record_poll(
            "server", "10~1.29.0", instance=INST_S1, source_pid=1001
        )
        clock.advance(90.0)
        with tempfile.TemporaryDirectory() as raw:
            state.prepare("run-new", "server", str(_profile_dir(Path(raw), "p")))
        snapshot = state.status_snapshot()
        ready = server.compute_bridge_ready(
            {
                "server_peer": {**snapshot["peers"]["server"], "version_state": "ok"},
                "client_peer": {**snapshot["peers"]["client"], "version_state": "ok"},
            }
        )
        self.assertFalse(ready["ready"])
        self.assertEqual(ready["reason"], "binding_not_ready")
        self.assertNotEqual(ready["reason"], "server_poll_stale")

    def test_bound_without_this_generation_poll_is_binding_not_ready(self) -> None:
        ready = server.compute_bridge_ready(
            {
                "server_peer": {
                    "binding_state": "BOUND",
                    "last_poll_age_s": 400.0,
                    "bound_last_poll_age_s": None,
                    "version_state": "ok",
                },
                "client_peer": {
                    "binding_state": "BOUND",
                    "last_poll_age_s": 400.0,
                    "bound_last_poll_age_s": None,
                    "version_state": "ok",
                },
            }
        )
        self.assertFalse(ready["ready"])
        self.assertEqual(ready["reason"], "binding_not_ready")

    def test_stale_bound_plus_starting_reports_starting(self) -> None:
        clock = FakeClock()
        state = loopback.ServerState("k", time_fn=clock)
        _bind(state, INST_S1, "server", 1001)
        state.record_poll(
            "server", "10~1.29.0", instance=INST_S1, source_pid=1001
        )
        clock.advance(PEER_STALE_S + 1.0)
        # Do not call prepare (that forgets clocks). Mint STARTING beside the
        # leftover BOUND to lock the status-view preference.
        with tempfile.TemporaryDirectory() as raw:
            profiles = _profile_dir(Path(raw), "p")
            minted = str(__import__("uuid").uuid4())
            payload = json.loads((profiles / "dayz_mcp.json").read_text(encoding="utf-8"))
            payload["instance"] = minted
            (profiles / "dayz_mcp.json").write_text(
                json.dumps(payload, separators=(",", ":")), encoding="utf-8"
            )
            state._bindings[minted] = instance_fence.Binding(
                instance=minted,
                run_id="run-new",
                role="server",
                epoch=99,
                pid=None,
                creation_time_utc=None,
                state=instance_fence.BINDING_STARTING,
            )
        snapshot = state.status_snapshot()
        self.assertEqual(snapshot["peers"]["server"]["binding_state"], "STARTING")
        ready = server.compute_bridge_ready(
            {
                "server_peer": {**snapshot["peers"]["server"], "version_state": "ok"},
                "client_peer": {**snapshot["peers"]["client"], "version_state": "ok"},
            }
        )
        self.assertEqual(ready["reason"], "binding_not_ready")

    def test_live_stale_bound_without_starting_stays_server_poll_stale(self) -> None:
        ready = server.compute_bridge_ready(
            {
                "server_peer": {
                    "binding_state": "BOUND",
                    "last_poll_age_s": 40.0,
                    "bound_last_poll_age_s": 40.0,
                    "version_state": "ok",
                },
                "client_peer": {
                    "last_poll_age_s": None,
                    "version_state": "legacy_blocked",
                },
            }
        )
        self.assertEqual(ready["reason"], "server_poll_stale")

    def test_ready_envelope_puts_reason_first_and_publishes_ages(self) -> None:
        payload = server._with_ready(
            {
                "server_peer": {
                    "last_poll_age_s": 0.2,
                    "bound_last_poll_age_s": 0.2,
                    "version_state": "ok",
                    # 0878: a live server peer needs an announced census + ach.
                    "capabilities": {
                        "state": "announced",
                        "reason": "ok",
                        "announced_commands": sorted(
                            server._BRIDGE_COMMAND_TOOLS["server"]
                        ),
                        "announced_arg_contract_hash": (
                            server.EXPECTED_SERVER_ARG_CONTRACT_HASH
                        ),
                    },
                },
                "client_peer": {
                    "last_poll_age_s": 0.3,
                    "bound_last_poll_age_s": 0.3,
                    "version_state": "ok",
                },
            }
        )
        ready = payload["ready"]
        self.assertEqual(list(payload)[:1], ["ready"])
        self.assertEqual(list(ready)[:2], ["ready", "reason"])
        self.assertNotIn("next_step", ready)
        self.assertIs(ready["ready"], True)
        self.assertEqual(ready["reason"], "ready")
        self.assertEqual(ready["stale_threshold_s"], PEER_STALE_S)
        self.assertEqual(ready["server_last_poll_age_s"], 0.2)
        self.assertEqual(ready["client_last_poll_age_s"], 0.3)

    def test_not_ready_envelope_names_public_next_step_before_ages(self) -> None:
        payload = server._with_ready(
            {
                "server_peer": {
                    "binding_state": "BOUND",
                    "last_poll_age_s": 400.0,
                    "bound_last_poll_age_s": None,
                    "version_state": "ok",
                },
                "client_peer": {
                    "binding_state": "BOUND",
                    "last_poll_age_s": 400.0,
                    "bound_last_poll_age_s": None,
                    "version_state": "ok",
                },
            }
        )
        self.assertEqual(list(payload)[:1], ["ready"])
        ready = payload["ready"]
        self.assertEqual(list(ready)[:3], ["ready", "reason", "next_step"])
        self.assertEqual(ready["reason"], "binding_not_ready")
        self.assertEqual(ready["next_step"], "bridge_status")
        self.assertIn(ready["next_step"], agent_loop.PUBLIC_NEXT_TOOLS)
        self.assertNotEqual(ready["next_step"], "lifecycle_status")
        self.assertEqual(ready["stale_threshold_s"], PEER_STALE_S)
        self.assertEqual(ready["server_last_poll_age_s"], 400.0)

    def test_every_not_ready_reason_names_a_public_tool(self) -> None:
        for reason in sorted(server.READY_REASONS - {"ready"}):
            tool = server._ready_next_tool(reason, is_ready=False)
            self.assertIsNotNone(tool, reason)
            self.assertEqual(tool, "bridge_status")
            self.assertIn(tool, agent_loop.PUBLIC_NEXT_TOOLS)
            self.assertNotEqual(tool, "lifecycle_status")
        self.assertIsNone(server._ready_next_tool("ready", is_ready=True))

    def test_world_read_not_ready_uses_uniform_game_not_ready_envelope(self) -> None:
        runtime = types.SimpleNamespace(_registered_tool_names={"bridge_status", "session_status"})
        status = {
            "server_peer": {"last_poll_age_s": None, "version_state": "ok"},
            "client_peer": {"last_poll_age_s": None, "version_state": "ok"},
        }
        envelope = server._world_read_not_ready(runtime, "query_all_players", status)
        self.assertIsNotNone(envelope)
        assert envelope is not None
        self.assertIs(envelope["ok"], False)
        self.assertEqual(envelope["error"], "game_not_ready:reason=no_run")
        self.assertEqual(envelope["reason"], "no_run")
        self.assertEqual(envelope["next_step"], {"tool": "bridge_status", "args": {}})
        self.assertTrue(envelope["error"].startswith("game_not_ready:reason="))


class RegistrySafeNextStepTest(unittest.TestCase):
    def test_next_step_rejects_internal_lifecycle_status(self) -> None:
        self.assertIsNone(agent_loop.next_step("lifecycle_status"))
        self.assertEqual(
            agent_loop.with_next_step("boom", "lifecycle_status"),
            "boom",
        )
        self.assertEqual(
            agent_loop.with_next_step("boom", "session_status"),
            "boom; next_step=session_status",
        )

    def test_fence_hints_name_session_status_not_lifecycle_status(self) -> None:
        blob = " ".join(instance_fence.FENCE_HINTS.values())
        blob += " ".join(instance_fence._RETIREMENT_HINTS.values())
        self.assertNotIn("lifecycle_status", blob)
        self.assertIn("session_status", blob)
        self.assertIn("next_step=session_status", blob)
        self.assertIn("next_step=bridge_status", instance_fence.FENCE_HINTS["binding_not_ready"])

    def test_lease_recipes_name_public_tools(self) -> None:
        self.assertIn("next_step=session_acquire_wait", server.LEASE_REQUIRED_RECIPE)
        self.assertIn("next_step=session_acquire_wait", server.LEASE_EXPIRED_RECIPE)
        self.assertIn("next_step=session_status", server.LEASE_INVALID_RECIPE)
        self.assertNotIn("lifecycle_status", server.LEASE_EXPIRED_RECIPE)
        self.assertNotIn("lifecycle_status", server.LEASE_INVALID_RECIPE)
        self.assertEqual(
            server._public_enqueue_error({"error": "lease_expired", "hint": "x"}),
            server.LEASE_EXPIRED_RECIPE,
        )
        self.assertEqual(
            server._public_enqueue_error({"error": "lease_invalid", "hint": "x"}),
            server.LEASE_INVALID_RECIPE,
        )


class LeaseExpiredOnSilentReleaseTest(unittest.IsolatedAsyncioTestCase):
    def _bare_client(self, token: str) -> ControlClient:
        client = object.__new__(ControlClient)
        client._transition_lock = asyncio.Lock()
        client._state_lock = threading.Lock()
        client.active_lease_token = token
        client.active_lease_id = "lease-1"
        client.active_ticket = None
        client.active_operation_id = None
        client.state = "ACTIVE"
        return client

    async def test_held_token_lease_invalid_becomes_lease_expired(self) -> None:
        client = self._bare_client("held-token")
        error = ControlClientError(
            "lease_invalid", request_stage="post_request", http_bytes_sent=1
        )
        with patch.object(client, "_session_call", AsyncMock(side_effect=error)):
            with self.assertRaises(ControlClientError) as raised:
                await client.session_release("held-token")
        self.assertEqual(raised.exception.code, "lease_expired")
        self.assertIsNone(client.active_lease_token)

    async def test_foreign_token_stays_lease_invalid(self) -> None:
        client = self._bare_client("mine")
        error = ControlClientError(
            "lease_invalid", request_stage="post_request", http_bytes_sent=1
        )
        with patch.object(client, "_session_call", AsyncMock(side_effect=error)):
            with self.assertRaises(ControlClientError) as raised:
                await client.session_release("someone-elses")
        self.assertEqual(raised.exception.code, "lease_invalid")
        self.assertEqual(client.active_lease_token, "mine")


class KnowledgeDeadEndTest(unittest.IsolatedAsyncioTestCase):
    async def test_find_does_not_route_to_prepare_when_pack_missing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            missing_index = root / "missing.json"
            missing_pack = root / "missing-pack"
            with patch.dict(
                os.environ,
                {
                    "DAYZ_MCP_KNOWLEDGE_JSON": str(missing_index),
                    "DAYZ_MCP_PACK_DIR": str(missing_pack),
                    "LOCALAPPDATA": str(root),
                },
            ):
                app = FastMCP("fn-p0-knowledge")
                knowledge.register_knowledge_tools(app)
                with self.assertRaises(ToolError) as raised:
                    await app.call_tool("dayz_knowledge_find", {"query": "Get"})
            message = str(raised.exception)
            self.assertIn("knowledge_pack_missing", message)
            self.assertIn(knowledge_pack.INSTALLER_REMEDY, message)
            self.assertIn("next_step=dayz_knowledge_status", message)
            self.assertNotIn("then dayz_knowledge_prepare", message)

    async def test_status_publishes_install_command_when_pack_missing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            missing_index = root / "missing.json"
            missing_pack = root / "missing-pack"
            with patch.dict(
                os.environ,
                {
                    "DAYZ_MCP_KNOWLEDGE_JSON": str(missing_index),
                    "DAYZ_MCP_PACK_DIR": str(missing_pack),
                    "LOCALAPPDATA": str(root),
                },
            ):
                status = knowledge._status(missing_index)
        self.assertFalse(status["can_prepare"])
        self.assertEqual(status["pack_state"], "missing")
        self.assertEqual(status["install_command"], knowledge_pack.INSTALLER_REMEDY)
        self.assertNotIn("dayz_knowledge_prepare", status["install_command"])

    async def test_find_still_routes_to_prepare_when_pack_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            missing_index = root / "missing.json"
            pack = root / "pack"
            pack.mkdir()
            with (
                patch.dict(
                    os.environ,
                    {
                        "DAYZ_MCP_KNOWLEDGE_JSON": str(missing_index),
                        "DAYZ_MCP_PACK_DIR": str(pack),
                    },
                ),
                patch.object(knowledge_pack, "resolve_pack_dir", return_value=pack),
                patch.object(
                    knowledge,
                    "extract_pack",
                    return_value=[
                        {
                            "name": "GetPlayers",
                            "source_file": "x.md",
                            "evidence": [{"path": "a.c", "line": 1}],
                        }
                    ],
                ),
            ):
                app = FastMCP("fn-p0-knowledge-prepare-ok")
                knowledge.register_knowledge_tools(app)
                with self.assertRaises(ToolError) as raised:
                    await app.call_tool("dayz_knowledge_find", {"query": "Get"})
            message = str(raised.exception)
            self.assertIn("knowledge_not_installed", message)
            self.assertIn("next_step=dayz_knowledge_prepare", message)


class WaitForBindingNotReadyTest(unittest.IsolatedAsyncioTestCase):
    async def test_waits_through_binding_not_ready(self) -> None:
        class _Runtime:
            tool_lock = __import__("asyncio").Lock()
            bridge_calls = 0

            async def call_bridge(self, cmd, args, peer, timeout_s):
                self.bridge_calls += 1
                if self.bridge_calls < 3:
                    raise server.ToolError("binding_not_ready")
                return {"ok": 1, "players": [{}]}

        result = await server.execute_wait_for(
            _Runtime(),
            "players_at_least",
            value=1,
            timeout_s=10.0,
            poll_interval_s=0.05,
        )
        self.assertTrue(result["satisfied"])
        self.assertEqual(result["not_ready_probes"], 2)


class CatalogDoesNotExposeLifecycleStatus(unittest.IsolatedAsyncioTestCase):
    async def test_public_catalog_has_no_lifecycle_status_tool(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _m: None))
        names = {tool.name for tool in await app.list_tools()}
        self.assertNotIn("lifecycle_status", names)
        self.assertIn("session_status", names)
        self.assertIn("bridge_status", names)
        self.assertIn("dayz_test_run", names)
        self.assertTrue(agent_loop.PUBLIC_NEXT_TOOLS.issubset(names))


if __name__ == "__main__":
    unittest.main()
