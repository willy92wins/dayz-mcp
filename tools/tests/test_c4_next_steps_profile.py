"""C4 (fb-20261028-131128-792e): error next_step hints follow the active profile.

The hint that accompanies a result (agent_loop.next_step / with_next_step /
ok_next_step) must never name a tool the active profile does not expose:
the test profile keeps the full public set, the live profile (B4 closed
list) drops the test/lifecycle and knowledge tools, and the admin pack
(C1) drops everything outside ADMIN_TOOL_NAMES. A disallowed hint falls
back to the next public tool the profile does expose, or to none.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import agent_loop, loopback, server, tool_pack


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

    def test_ok_next_step_substitutes_bridge_status_when_mapped_is_outside(self) -> None:
        admin = tool_pack.profile_next_tools("admin")
        # session_release maps to session_acquire_wait, which admin exposes.
        self.assertEqual(
            agent_loop.ok_next_step("session_release", allowed=admin),
            "session_acquire_wait",
        )
        live = tool_pack.profile_next_tools("live")
        self.assertEqual(
            agent_loop.ok_next_step("session_release", allowed=live),
            "session_acquire_wait",
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

    def test_session_hint_survives_the_admin_pack(self) -> None:
        payload = server._profile_ok_next_step(
            {"ok": True}, "session_release", "admin"
        )
        self.assertEqual(payload["next_step"], "session_acquire_wait")


if __name__ == "__main__":
    unittest.main()
