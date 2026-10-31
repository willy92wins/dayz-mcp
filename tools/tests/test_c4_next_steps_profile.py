"""C4 (fb-20261028-131128-792e): error next_step hints follow the active profile.

The hint that accompanies a result (agent_loop.next_step / with_next_step /
ok_next_step) must never name a tool the active profile does not expose:
the test profile keeps the full public set, the live profile (B4 closed
list) drops the test/lifecycle and knowledge tools, and the admin pack
(C1) drops everything outside ADMIN_TOOL_NAMES. A disallowed hint falls
back to the next public tool the profile does expose, or to none.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from mcp import types

from dayz_mcp import agent_loop, daemon, loopback, server, tool_pack


def _profiles() -> dict[str, frozenset[str]]:
    return {
        "test": tool_pack.profile_next_tools("test"),
        "live": tool_pack.profile_next_tools("live"),
        "admin": tool_pack.profile_next_tools("admin"),
    }


class ProfileSetsTest(unittest.TestCase):
    def test_test_profile_is_the_full_public_set(self) -> None:
        self.assertEqual(
            tool_pack.profile_next_tools("test"), agent_loop.PUBLIC_NEXT_TOOLS
        )
        self.assertEqual(tool_pack.next_step_tools("full"), agent_loop.PUBLIC_NEXT_TOOLS)

    def test_admin_profile_is_public_intersected_with_the_pack(self) -> None:
        expected = agent_loop.PUBLIC_NEXT_TOOLS & tool_pack.ADMIN_TOOL_NAMES
        self.assertEqual(tool_pack.profile_next_tools("admin"), expected)
        self.assertEqual(tool_pack.next_step_tools("admin"), expected)
        self.assertNotIn("dayz_test_run", expected)
        self.assertNotIn("dayz_knowledge_status", expected)

    def test_local8b_pack_intersects_the_same_way(self) -> None:
        self.assertEqual(
            tool_pack.next_step_tools("local8b"),
            agent_loop.PUBLIC_NEXT_TOOLS & tool_pack.LOCAL8B_TOOL_NAMES,
        )

    def test_live_profile_matches_the_b4_closed_list(self) -> None:
        derived = agent_loop.PUBLIC_NEXT_TOOLS & (
            tool_pack.ADMIN_TOOL_NAMES
            | loopback.LIVE_ALLOWED_COMMANDS
            | loopback.LIVE_ADMIN_COMMANDS
        )
        self.assertEqual(tool_pack.profile_next_tools("live"), derived)
        self.assertEqual(tool_pack.LIVE_NEXT_TOOLS, derived)
        for excluded in ("dayz_test_run", "dayz_test_stop", "dayz_test_close",
                         "dayz_knowledge_status", "pipeline_inbox"):
            self.assertNotIn(excluded, derived)
        self.assertIn("object_inspect", derived)
        self.assertIn("bridge_status", derived)

    def test_unknown_profile_or_pack_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            tool_pack.profile_next_tools("nope")
        with self.assertRaises(ValueError):
            tool_pack.next_step_tools("nope")


class NextStepStaysInsideProfileTest(unittest.TestCase):
    def test_no_hint_names_a_tool_outside_the_profile(self) -> None:
        for name, allowed in _profiles().items():
            with self.subTest(profile=name):
                for tool in agent_loop.PUBLIC_NEXT_TOOLS:
                    hint = agent_loop.next_step(tool, allowed=allowed)
                    self.assertTrue(hint is None or hint in allowed, (tool, hint))
                    if tool in allowed:
                        self.assertEqual(hint, tool)
                    else:
                        self.assertIsNone(hint)
                    message = agent_loop.with_next_step("boom", tool, allowed=allowed)
                    if "next_step=" in message:
                        named = message.rsplit("next_step=", 1)[1]
                        self.assertIn(named, allowed)

    def test_ok_next_step_falls_back_inside_the_profile_or_to_none(self) -> None:
        commands = list(agent_loop._OK_NEXT_BY_COMMAND) + ["world_spawn"]
        for name, allowed in _profiles().items():
            with self.subTest(profile=name):
                for cmd in commands:
                    follow = agent_loop.ok_next_step(
                        cmd, mutating=True, allowed=allowed
                    )
                    self.assertTrue(
                        follow is None or follow in allowed, (cmd, follow)
                    )

    def test_ok_next_step_mapped_tool_survives_when_profile_exposes_it(self) -> None:
        for name in ("admin", "live"):
            allowed = tool_pack.profile_next_tools(name)
            with self.subTest(profile=name):
                # session_release maps to session_acquire_wait, exposed by both.
                self.assertEqual(
                    agent_loop.ok_next_step("session_release", allowed=allowed),
                    "session_acquire_wait",
                )

    def test_ok_next_step_falls_back_to_bridge_status_when_mapped_excluded(self) -> None:
        # session_release maps to session_acquire_wait; exclude exactly that
        # destination so only the fallback chain can answer.
        allowed = tool_pack.profile_next_tools("live") - {"session_acquire_wait"}
        self.assertNotIn("session_acquire_wait", allowed)
        self.assertEqual(
            agent_loop.ok_next_step("session_release", allowed=allowed),
            "bridge_status",
        )
        self.assertEqual(
            agent_loop.ok_next_step(
                "session_release",
                allowed=frozenset({"bridge_status", "session_status"}),
            ),
            "bridge_status",
        )

    def test_ok_next_step_falls_back_to_session_status_when_bridge_excluded(self) -> None:
        self.assertEqual(
            agent_loop.ok_next_step(
                "session_release", allowed=frozenset({"session_status"})
            ),
            "session_status",
        )

    def test_ok_next_step_names_nothing_when_no_fallback_is_allowed(self) -> None:
        self.assertIsNone(
            agent_loop.ok_next_step(
                "session_release", allowed=frozenset({"wait_for"})
            )
        )


class TestProfileRegressionTest(unittest.TestCase):
    """Without an explicit profile the hints are exactly the pre-C4 ones."""

    def test_default_next_step_unchanged(self) -> None:
        for tool in agent_loop.PUBLIC_NEXT_TOOLS:
            self.assertEqual(agent_loop.next_step(tool), tool)
        self.assertIsNone(agent_loop.next_step("lifecycle_status"))
        self.assertEqual(
            agent_loop.with_next_step("boom", "session_status"),
            "boom; next_step=session_status",
        )
        self.assertEqual(
            agent_loop.with_next_step("boom", "lifecycle_status"), "boom"
        )

    def test_default_ok_next_step_unchanged(self) -> None:
        self.assertEqual(agent_loop.ok_next_step("lease_acquire"), "bridge_status")
        self.assertEqual(
            agent_loop.ok_next_step("session_release"), "session_acquire_wait"
        )
        self.assertEqual(
            agent_loop.ok_next_step("world_spawn", mutating=True),
            "session_heartbeat",
        )
        self.assertIsNone(agent_loop.ok_next_step("world_spawn"))
        self.assertIsNone(agent_loop.ok_next_step("bridge_status"))


class EffectiveProfileTest(unittest.TestCase):
    """The daemon live profile is propagated and intersected with the pack."""

    def test_live_intersects_full_and_local8b_packs(self) -> None:
        live = tool_pack.profile_next_tools("live")
        self.assertEqual(
            tool_pack.effective_next_tools("full", live_enabled=True), live
        )
        self.assertEqual(
            tool_pack.effective_next_tools("local8b", live_enabled=True),
            live & tool_pack.LOCAL8B_TOOL_NAMES,
        )
        self.assertNotIn(
            "dayz_knowledge_status",
            tool_pack.effective_next_tools("local8b", live_enabled=True),
        )

    def test_profile_off_keeps_the_pack_set(self) -> None:
        self.assertEqual(
            tool_pack.effective_next_tools("local8b"),
            tool_pack.next_step_tools("local8b"),
        )

    def test_server_effective_set_ignores_the_local_environment(self) -> None:
        # The client's DAYZ_MCP_LIVE_PROFILE is not the serving daemon's:
        # it must neither enable nor disable the live hint set.
        live = tool_pack.profile_next_tools("live")
        config = server.ServerConfig(
            tool_pack="full", live_profile=True, log_sink=lambda _m: None
        )
        with patch.dict(os.environ, {"DAYZ_MCP_LIVE_PROFILE": "0"}):
            self.assertEqual(server._effective_next_tools(config), live)
        env_config = server.ServerConfig(
            tool_pack="local8b", log_sink=lambda _m: None
        )
        with patch.dict(os.environ, {"DAYZ_MCP_LIVE_PROFILE": "1"}):
            self.assertEqual(
                server._effective_next_tools(env_config),
                tool_pack.next_step_tools("local8b"),
            )
        off_config = server.ServerConfig(
            tool_pack="full", live_profile=False, log_sink=lambda _m: None
        )
        with patch.dict(os.environ, {"DAYZ_MCP_LIVE_PROFILE": "1"}):
            self.assertEqual(
                server._effective_next_tools(off_config),
                agent_loop.PUBLIC_NEXT_TOOLS,
            )

    def test_serving_state_wins_over_config_and_environment(self) -> None:
        # Embedded repro (Sol): the real serving state has the profile
        # off while the local environment says on; hints stay valid.
        live = tool_pack.profile_next_tools("live")
        config = server.ServerConfig(
            tool_pack="full", log_sink=lambda _m: None
        )
        off_runtime = SimpleNamespace(
            loopback=SimpleNamespace(
                state=SimpleNamespace(live_profile=False)
            )
        )
        with patch.dict(os.environ, {"DAYZ_MCP_LIVE_PROFILE": "1"}):
            self.assertEqual(
                server._effective_next_tools(config, runtime=off_runtime),
                agent_loop.PUBLIC_NEXT_TOOLS,
            )
        on_runtime = SimpleNamespace(
            loopback=SimpleNamespace(state=SimpleNamespace(live_profile=True))
        )
        with patch.dict(os.environ, {"DAYZ_MCP_LIVE_PROFILE": "0"}):
            self.assertEqual(
                server._effective_next_tools(config, runtime=on_runtime), live
            )


class StatusProfileFailClosedTest(unittest.TestCase):
    """Runtime.status() must not crash on a state without a profile.

    Absent or non-boolean is unknown, never full: it publishes as
    live/restrictive (and admin disabled), matching _serving_live_enabled.
    """

    def _runtime(self, state: object) -> server.Runtime:
        runtime = object.__new__(server.Runtime)
        runtime.config = server.ServerConfig(log_sink=lambda _m: None)
        runtime.loopback = SimpleNamespace(state=state)
        return runtime

    def test_absent_profile_publishes_live_not_full(self) -> None:
        real = loopback.ServerState("c4-status-test")
        state = SimpleNamespace(status_snapshot=real.status_snapshot)
        self.assertFalse(hasattr(state, "live_profile"))
        payload = self._runtime(state).status()
        self.assertIs(payload["live_profile"], True)
        self.assertIs(payload["live_admin_enabled"], False)

    def test_non_bool_profile_is_unknown_not_full(self) -> None:
        real = loopback.ServerState("c4-status-test")
        state = SimpleNamespace(
            status_snapshot=real.status_snapshot,
            live_profile=None,
            live_admin_enabled=None,
        )
        payload = self._runtime(state).status()
        self.assertIs(payload["live_profile"], True)
        self.assertIs(payload["live_admin_enabled"], False)

    def test_explicit_bool_profile_is_published_verbatim(self) -> None:
        for live, admin in ((False, False), (True, True), (True, False)):
            with self.subTest(live=live, admin=admin):
                real = loopback.ServerState("c4-status-test")
                state = SimpleNamespace(
                    status_snapshot=real.status_snapshot,
                    live_profile=live,
                    live_admin_enabled=admin,
                )
                payload = self._runtime(state).status()
                self.assertIs(payload["live_profile"], live)
                self.assertIs(payload["live_admin_enabled"], admin)


class ServingDaemonAuthorityTest(unittest.TestCase):
    """A client reusing a daemon learns its profile from that daemon.

    The learning entry is ClientRuntime._learn_serving_live_profile, fed
    by the daemon's /status in bridge_status_payload and _daemon_healthy;
    it is exercised synchronously so the authority rule is checked even
    where an event loop cannot be created.
    """

    def _client(self, payload: dict) -> server.ClientRuntime:
        client = object.__new__(server.ClientRuntime)
        client._serving_live_profile = None
        client._serving_live_generation = None
        client._learn_serving_live_profile(dict(payload))
        return client

    def test_reused_live_daemon_filters_despite_client_env_off(self) -> None:
        client = self._client(
            {"daemon_generation": "g1", "live_profile": True}
        )
        config = server.ServerConfig(
            tool_pack="full", live_profile=False, log_sink=lambda _m: None
        )
        with patch.dict(os.environ, {"DAYZ_MCP_LIVE_PROFILE": "0"}):
            self.assertEqual(
                server._effective_next_tools(config, runtime=client),
                tool_pack.profile_next_tools("live"),
            )

    def test_reused_full_daemon_keeps_hints_despite_client_env_on(self) -> None:
        client = self._client(
            {"daemon_generation": "g1", "live_profile": False}
        )
        config = server.ServerConfig(
            tool_pack="full", live_profile=True, log_sink=lambda _m: None
        )
        with patch.dict(os.environ, {"DAYZ_MCP_LIVE_PROFILE": "1"}):
            self.assertEqual(
                server._effective_next_tools(config, runtime=client),
                agent_loop.PUBLIC_NEXT_TOOLS,
            )

    def test_status_without_live_profile_teaches_nothing(self) -> None:
        client = self._client({"daemon_generation": "g1"})
        self.assertIsNone(client._serving_live_profile)
        self.assertFalse(server._serving_live_known(client))

    def test_daemon_entry_publishes_its_serving_profile_on_status(self) -> None:
        state = SimpleNamespace(live_profile=True, live_admin_enabled=False)
        captured: dict = {}

        def fake_run_daemon(_config: object) -> int:
            provider = daemon.make_status_provider(_config, state)
            captured.update(provider())
            return 0

        with (
            patch.object(
                daemon,
                "make_status_provider",
                lambda _cfg, _state: (lambda: {"daemon_generation": "g1"}),
            ),
            patch.object(daemon, "run_daemon", fake_run_daemon),
        ):
            config = server.ServerConfig(log_sink=lambda _m: None)
            self.assertEqual(server._run_daemon(config), 0)
        self.assertEqual(captured["live_profile"], True)
        self.assertEqual(captured["live_admin_enabled"], False)


class McpErrorProfileFilterTest(unittest.IsolatedAsyncioTestCase):
    """A real MCP error leaving build_app is filtered under the live profile."""

    async def _knowledge_find_error_text(
        self,
        *,
        live: bool,
        serving_live: bool | None = None,
        declared_none: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "DAYZ_MCP_KNOWLEDGE_JSON": str(Path(tmp) / "missing.json"),
                "LOCALAPPDATA": tmp,
            }
            env.update(extra_env or {})
            with patch.dict(os.environ, env):
                app, _runtime = server.build_app(
                    server.ServerConfig(
                        tool_pack="full",
                        live_profile=None if declared_none else live,
                        log_sink=lambda _m: None,
                    )
                )
                if serving_live is not None:
                    _runtime.loopback = SimpleNamespace(
                        state=SimpleNamespace(
                            live_profile=serving_live,
                            live_admin_enabled=False,
                        )
                    )
                request = types.CallToolRequest(
                    method="tools/call",
                    params=types.CallToolRequestParams(
                        name="dayz_knowledge_find", arguments={"query": "Get"}
                    ),
                )
                response = await app._mcp_server.request_handlers[
                    types.CallToolRequest
                ](request)
        result = response.root
        self.assertTrue(result.isError)
        return "".join(
            block.text for block in result.content if isinstance(block, types.TextContent)
        )

    async def test_live_profile_strips_knowledge_next_step_from_real_error(self) -> None:
        text = await self._knowledge_find_error_text(live=True)
        # The error really is the no-index knowledge error...
        self.assertIn("knowledge_", text)
        # ...but its next_step names nothing outside the live profile.
        self.assertNotIn("next_step=dayz_knowledge", text)
        live = tool_pack.profile_next_tools("live")
        for token in text.split("next_step=")[1:]:
            self.assertIn(token.split(";")[0].split()[0], live)

    async def test_profile_off_keeps_knowledge_next_step_in_real_error(self) -> None:
        text = await self._knowledge_find_error_text(live=False)
        self.assertIn("next_step=dayz_knowledge_status", text)

    async def test_serving_state_off_keeps_hint_despite_local_env_on(self) -> None:
        # Sol repro in embedded: the serving state has the profile off
        # and nothing declares it, while the local environment says on.
        text = await self._knowledge_find_error_text(
            live=False,
            serving_live=False,
            declared_none=True,
            extra_env={"DAYZ_MCP_LIVE_PROFILE": "1"},
        )
        self.assertIn("next_step=dayz_knowledge_status", text)

    async def test_serving_state_on_strips_hint_despite_local_env_off(self) -> None:
        text = await self._knowledge_find_error_text(
            live=False,
            serving_live=True,
            declared_none=True,
            extra_env={"DAYZ_MCP_LIVE_PROFILE": "0"},
        )
        self.assertNotIn("next_step=dayz_knowledge", text)


class ServerProfileFilterTest(unittest.TestCase):
    def test_admin_pack_rewrites_an_out_of_pack_next_step(self) -> None:
        payload = server._profile_ok_next_step(
            {"ok": True, "next_step": "dayz_test_run"}, "world_spawn", "admin"
        )
        self.assertIn(payload["next_step"], tool_pack.profile_next_tools("admin"))

    def test_full_pack_keeps_the_current_next_step(self) -> None:
        payload = server._profile_ok_next_step(
            {"ok": True, "next_step": "dayz_test_run"}, "world_spawn", "full"
        )
        self.assertEqual(payload["next_step"], "dayz_test_run")

    def test_live_profile_rewrites_an_out_of_profile_next_step(self) -> None:
        config = server.ServerConfig(
            tool_pack="full", live_profile=True, log_sink=lambda _m: None
        )
        payload = server._profile_ok_next_step(
            {"ok": True, "next_step": "dayz_test_run"},
            "world_spawn",
            allowed=server._effective_next_tools(config),
        )
        self.assertIn(payload["next_step"], tool_pack.profile_next_tools("live"))

    def test_session_hint_survives_the_admin_pack(self) -> None:
        payload = server._profile_ok_next_step(
            {"ok": True}, "session_release", "admin"
        )
        self.assertEqual(payload["next_step"], "session_acquire_wait")


def _bare_client(
    payload: dict | None = None,
    *,
    request_once=None,
) -> server.ClientRuntime:
    """Bare client whose only daemon transport is the given _request_once.

    Tests simulate the daemon at _request_once so the real no-spawn
    probe (_probe_serving_live_profile_sync via _request_once) runs;
    no bridge_status_payload stub is installed.
    """
    client = object.__new__(server.ClientRuntime)
    client._serving_live_profile = None
    client._serving_live_generation = None
    client._probe = None  # type: ignore[attr-defined]
    client._credential_provider = object()  # type: ignore[attr-defined]
    calls: list = []

    def _recorded(*args, **kwargs):
        calls.append((args, kwargs))
        if request_once is None:
            raise RuntimeError("no daemon simulated")
        return request_once(*args, **kwargs)

    client._request_once = _recorded  # type: ignore[attr-defined]
    client._request_calls = calls  # type: ignore[attr-defined]
    if payload is not None:
        client._learn_serving_live_profile(dict(payload))
    return client


def _fake_filter_app(error_text: str):
    app = SimpleNamespace()
    handlers: dict = {}

    async def original(_request):
        return types.ServerResult(
            types.CallToolResult(
                isError=True,
                content=[types.TextContent(type="text", text=error_text)],
            )
        )

    handlers[types.CallToolRequest] = original
    app._mcp_server = SimpleNamespace(request_handlers=handlers)
    return app


async def _run_filter(app, runtime, config) -> str:
    server._install_next_step_profile_filter(
        app,
        lambda: server._effective_next_tools(config, runtime=runtime),
        runtime=runtime,
        config=config,
    )
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(
            name="dayz_knowledge_find", arguments={"query": "Get"}
        ),
    )
    response = await app._mcp_server.request_handlers[types.CallToolRequest](
        request
    )
    result = response.root
    return "".join(
        block.text
        for block in result.content
        if isinstance(block, types.TextContent)
    )


class GenerationCacheRegressionTest(unittest.TestCase):
    """P2 r3-1: cache is bound to the daemon generation."""

    def test_generation_change_invalidates_cached_full_profile(self) -> None:
        client = _bare_client({"daemon_generation": "g1", "live_profile": False})
        config = server.ServerConfig(
            mode="client", tool_pack="full", log_sink=lambda _m: None
        )
        self.assertEqual(
            server._effective_next_tools(config, runtime=client),
            agent_loop.PUBLIC_NEXT_TOOLS,
        )
        client._learn_serving_live_profile(
            {"daemon_generation": "g2", "live_profile": True}
        )
        self.assertEqual(client._serving_live_generation, "g2")
        self.assertEqual(
            server._effective_next_tools(config, runtime=client),
            tool_pack.profile_next_tools("live"),
        )

    def test_new_generation_without_live_profile_invalidates_to_unknown(self) -> None:
        client = _bare_client({"daemon_generation": "g1", "live_profile": False})
        client._learn_serving_live_profile({"daemon_generation": "g2"})
        self.assertIsNone(client._serving_live_profile)
        self.assertEqual(client._serving_live_generation, "g2")
        self.assertFalse(server._serving_live_known(client))
        config = server.ServerConfig(
            mode="client", tool_pack="full", log_sink=lambda _m: None
        )
        self.assertEqual(
            server._effective_next_tools(config, runtime=client),
            tool_pack.profile_next_tools("live"),
        )


class GenerationMcpRegressionTest(unittest.IsolatedAsyncioTestCase):
    async def test_full_to_live_same_client_strips_knowledge_hint(self) -> None:
        # Daemon replaced: the real probe (via _request_once) now sees g2 live.
        client = _bare_client(
            {"daemon_generation": "g1", "live_profile": False},
            request_once=lambda *a, **k: (
                200,
                {"daemon_generation": "g2", "live_profile": True},
            ),
        )
        config = server.ServerConfig(
            mode="client", tool_pack="full", log_sink=lambda _m: None
        )
        app = _fake_filter_app("knowledge_no_index; next_step=dayz_knowledge_status")
        text = await _run_filter(app, client, config)
        self.assertGreaterEqual(len(client._request_calls), 1)  # type: ignore[attr-defined]
        self.assertEqual(client._request_calls[0][0][:2], ("GET", "/status"))  # type: ignore[attr-defined]
        self.assertNotIn("next_step=dayz_knowledge", text)
        self.assertEqual(client._serving_live_generation, "g2")
        self.assertTrue(client._serving_live_profile)


class UnknownProfileFailClosedTest(unittest.TestCase):
    def test_unknown_client_profile_is_restrictive_not_full(self) -> None:
        client = _bare_client({"daemon_generation": "g1"})
        self.assertIsNone(client._serving_live_profile)
        config = server.ServerConfig(
            mode="client",
            tool_pack="full",
            live_profile=False,
            log_sink=lambda _m: None,
        )
        effective = server._effective_next_tools(config, runtime=client)
        self.assertEqual(effective, tool_pack.profile_next_tools("live"))
        self.assertNotIn("dayz_knowledge_status", effective)


class UnknownProfileMcpRegressionTest(unittest.IsolatedAsyncioTestCase):
    async def test_status_without_live_profile_strips_knowledge_hint_via_mcp(self) -> None:
        client = _bare_client(
            request_once=lambda *a, **k: (200, {"daemon_generation": "g1"})
        )
        config = server.ServerConfig(
            mode="client", tool_pack="full", log_sink=lambda _m: None
        )
        app = _fake_filter_app("knowledge_no_index; next_step=dayz_knowledge_status")
        text = await _run_filter(app, client, config)
        self.assertGreaterEqual(len(client._request_calls), 1)  # type: ignore[attr-defined]
        self.assertNotIn("next_step=dayz_knowledge", text)
        self.assertIsNone(client._serving_live_profile)
        self.assertFalse(server._serving_live_known(client))

    async def test_failed_probe_keeps_unknown_restrictive_via_mcp(self) -> None:
        def _down(*_a, **_k):
            raise RuntimeError("status down")

        client = _bare_client(request_once=_down)
        config = server.ServerConfig(
            mode="client", tool_pack="full", log_sink=lambda _m: None
        )
        app = _fake_filter_app("knowledge_no_index; next_step=dayz_knowledge_status")
        text = await _run_filter(app, client, config)
        self.assertGreaterEqual(len(client._request_calls), 1)  # type: ignore[attr-defined]
        self.assertNotIn("next_step=dayz_knowledge", text)


class NoSpawnProbeSyncTest(unittest.TestCase):
    def test_no_spawn_probe_never_calls_ensure_daemon_sync(self) -> None:
        # Sync core of probe_serving_live_profile_no_spawn: the async
        # wrapper only adds asyncio.to_thread around this, so checking
        # it here runs even where an event loop cannot be created.
        client = _bare_client({"daemon_generation": "g1", "live_profile": False})
        ensure_calls: list[int] = []
        client._ensure_daemon = lambda *a, **k: ensure_calls.append(1) or True  # type: ignore[attr-defined]
        client._probe = None  # type: ignore[attr-defined]
        client._credential_provider = object()  # type: ignore[attr-defined]
        client._request_once = lambda *a, **k: (  # type: ignore[attr-defined]
            200,
            {"daemon_generation": "g2", "live_profile": True},
        )
        ok = client._probe_serving_live_profile_sync(1.0)
        self.assertTrue(ok)
        self.assertEqual(ensure_calls, [])
        self.assertTrue(client._serving_live_profile)
        self.assertEqual(client._serving_live_generation, "g2")


class FailedProbeFailClosedTest(unittest.TestCase):
    """P2 r5-1: a failed probe forgets a cached full profile (sync core)."""

    def _config(self) -> server.ServerConfig:
        return server.ServerConfig(
            mode="client", tool_pack="full", log_sink=lambda _m: None
        )

    def test_transport_failure_forgets_cached_full(self) -> None:
        def _down(*_a, **_k):
            raise RuntimeError("status down")

        client = _bare_client(
            {"daemon_generation": "g1", "live_profile": False},
            request_once=_down,
        )
        self.assertEqual(
            server._effective_next_tools(self._config(), runtime=client),
            agent_loop.PUBLIC_NEXT_TOOLS,
        )
        self.assertFalse(client._probe_serving_live_profile_sync(1.0))
        self.assertIsNone(client._serving_live_profile)
        self.assertEqual(
            server._effective_next_tools(self._config(), runtime=client),
            tool_pack.profile_next_tools("live"),
        )

    def test_non_200_forgets_cached_full(self) -> None:
        client = _bare_client(
            {"daemon_generation": "g1", "live_profile": False},
            request_once=lambda *a, **k: (503, {}),
        )
        self.assertFalse(client._probe_serving_live_profile_sync(1.0))
        self.assertIsNone(client._serving_live_profile)
        self.assertEqual(
            server._effective_next_tools(self._config(), runtime=client),
            tool_pack.profile_next_tools("live"),
        )

    def test_payload_without_live_profile_forgets_cached_full(self) -> None:
        client = _bare_client(
            {"daemon_generation": "g1", "live_profile": False},
            request_once=lambda *a, **k: (200, {"daemon_generation": "g1"}),
        )
        self.assertTrue(client._probe_serving_live_profile_sync(1.0))
        self.assertIsNone(client._serving_live_profile)
        self.assertEqual(
            server._effective_next_tools(self._config(), runtime=client),
            tool_pack.profile_next_tools("live"),
        )


class FailedProbeMcpRegressionTest(unittest.IsolatedAsyncioTestCase):
    async def test_failed_probe_after_cached_full_strips_hint_via_mcp(self) -> None:
        def _down(*_a, **_k):
            raise RuntimeError("status down")

        client = _bare_client(
            {"daemon_generation": "g1", "live_profile": False},
            request_once=_down,
        )
        config = server.ServerConfig(
            mode="client", tool_pack="full", log_sink=lambda _m: None
        )
        app = _fake_filter_app("knowledge_no_index; next_step=dayz_knowledge_status")
        text = await _run_filter(app, client, config)
        self.assertGreaterEqual(len(client._request_calls), 1)  # type: ignore[attr-defined]
        self.assertNotIn("next_step=dayz_knowledge", text)
        self.assertIsNone(client._serving_live_profile)

    async def test_unknown_payload_after_cached_full_strips_hint_via_mcp(self) -> None:
        client = _bare_client(
            {"daemon_generation": "g1", "live_profile": False},
            request_once=lambda *a, **k: (200, {"daemon_generation": "g1"}),
        )
        config = server.ServerConfig(
            mode="client", tool_pack="full", log_sink=lambda _m: None
        )
        app = _fake_filter_app("knowledge_no_index; next_step=dayz_knowledge_status")
        text = await _run_filter(app, client, config)
        self.assertNotIn("next_step=dayz_knowledge", text)
        self.assertIsNone(client._serving_live_profile)


class NoHintNoSpawnRegressionTest(unittest.IsolatedAsyncioTestCase):
    async def test_error_without_hint_skips_status_and_spawn(self) -> None:
        client = _bare_client(
            request_once=lambda *a, **k: (
                200,
                {"daemon_generation": "g2", "live_profile": True},
            )
        )
        config = server.ServerConfig(
            mode="client", tool_pack="full", log_sink=lambda _m: None
        )
        spawn_calls: list[int] = []
        client._ensure_daemon = lambda *a, **k: spawn_calls.append(1) or True  # type: ignore[attr-defined]
        app = _fake_filter_app("bad_purpose")
        text = await _run_filter(app, client, config)
        self.assertEqual(text, "bad_purpose")
        self.assertEqual(len(client._request_calls), 0)  # type: ignore[attr-defined]
        self.assertEqual(spawn_calls, [])


if __name__ == "__main__":
    unittest.main()
