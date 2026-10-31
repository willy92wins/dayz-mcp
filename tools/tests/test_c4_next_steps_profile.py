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


class ServingDaemonAuthorityTest(unittest.IsolatedAsyncioTestCase):
    """A client reusing a daemon learns its profile from that daemon."""

    def _client(self, payload: dict) -> server.ClientRuntime:
        client = object.__new__(server.ClientRuntime)
        client._serving_live_profile = None
        client._serving_live_generation = None
        client._call = lambda *_args: (200, dict(payload))  # type: ignore[method-assign]
        return client

    async def test_reused_live_daemon_filters_despite_client_env_off(self) -> None:
        client = self._client(
            {"daemon_generation": "g1", "live_profile": True}
        )
        await client.bridge_status_payload()
        config = server.ServerConfig(
            tool_pack="full", live_profile=False, log_sink=lambda _m: None
        )
        with patch.dict(os.environ, {"DAYZ_MCP_LIVE_PROFILE": "0"}):
            self.assertEqual(
                server._effective_next_tools(config, runtime=client),
                tool_pack.profile_next_tools("live"),
            )

    async def test_reused_full_daemon_keeps_hints_despite_client_env_on(self) -> None:
        client = self._client(
            {"daemon_generation": "g1", "live_profile": False}
        )
        await client.bridge_status_payload()
        config = server.ServerConfig(
            tool_pack="full", live_profile=True, log_sink=lambda _m: None
        )
        with patch.dict(os.environ, {"DAYZ_MCP_LIVE_PROFILE": "1"}):
            self.assertEqual(
                server._effective_next_tools(config, runtime=client),
                agent_loop.PUBLIC_NEXT_TOOLS,
            )

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


if __name__ == "__main__":
    unittest.main()
