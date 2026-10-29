"""B4 (fb-20261028-131107-9249): live profile the daemon enforces.

With the profile on, ServerState.enqueue_command (the one path every
client command passes before the bridge) refuses, with a named error,
every command outside the closed live list, whatever client sends it.
Camera, input, UI, spawn, exec_enforce and the dayz_test* tools stay out
even when admin mutations are enabled. Admin mutations are refused with
live_profile_admin_disabled until expressly enabled. Reads and warnings
(notify_players) are allowed by default. Profile off changes nothing.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import daemon, loopback
from tests.fence_helpers import bind_both_peers

UID = "76561198000000001"
TELEPORT = {"uid": UID, "pos": [7500.0, 0.0, 7500.0]}


def _state(**kwargs: object) -> loopback.ServerState:
    state = loopback.ServerState("test-key", **kwargs)  # type: ignore[arg-type]
    bind_both_peers(state)
    return state


class ExcludedFamiliesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = _state(live_profile=True, live_admin_enabled=True)

    def _refused(self, cmd: str, args: dict, peer: str | None = None) -> None:
        status, body = self.state.enqueue_command(cmd, args, peer=peer)
        self.assertEqual(status, 403, body)
        self.assertEqual(body, {"error": "live_profile_refused"})

    def test_camera_family_refused(self) -> None:
        self._refused("camera_get", {}, peer="client")
        self._refused("camera_set", {}, peer="client")

    def test_input_family_refused(self) -> None:
        self._refused("key_press", {"dik": 1}, peer="client")
        self._refused("input_describe", {"name": "UAFire"}, peer="client")
        self._refused("input_trigger", {}, peer="client")

    def test_ui_family_refused(self) -> None:
        self._refused("ui_tree", {}, peer="client")
        self._refused("ui_click", {}, peer="client")

    def test_spawn_family_refused(self) -> None:
        self._refused("world_spawn", {"classname": "Apple", "pos": [1.0, 2.0, 3.0]})
        self._refused("world_spawn_batch", {"items": []})

    def test_exec_enforce_refused_even_when_exec_enabled(self) -> None:
        state = _state(
            live_profile=True,
            live_admin_enabled=True,
            enable_exec_enforce=True,
            exec_allowlist={"probe()"},
            exec_audit=lambda *_args: None,
        )
        status, body = state.enqueue_command(
            "exec_enforce", {"expr": "probe()", "main_fn": "Main"}, peer="server"
        )
        self.assertEqual((status, body), (403, {"error": "live_profile_refused"}))

    def test_test_tools_refused(self) -> None:
        self._refused("dayz_test_run", {})
        self._refused("dayz_test_close", {})

    def test_unknown_command_refused_with_live_error_not_not_whitelisted(self) -> None:
        self._refused("not_a_command", {})


class AdminMutationsTest(unittest.TestCase):
    def test_admin_refused_until_enabled_then_allowed(self) -> None:
        state = _state(live_profile=True)
        status, body = state.enqueue_command("player_teleport", dict(TELEPORT))
        self.assertEqual(
            (status, body), (403, {"error": "live_profile_admin_disabled"})
        )

        enabled = _state(live_profile=True, live_admin_enabled=True)
        status, body = enabled.enqueue_command("player_teleport", dict(TELEPORT))
        self.assertEqual(status, 200, body)
        self.assertEqual(body["peer"], "server")

    def test_admin_enabled_still_refuses_excluded_families(self) -> None:
        state = _state(live_profile=True, live_admin_enabled=True)
        status, body = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual((status, body), (403, {"error": "live_profile_refused"}))


class ReadsAndWarningsTest(unittest.TestCase):
    def test_reads_and_warnings_allowed_by_default(self) -> None:
        state = _state(live_profile=True)
        for cmd, args, peer in (
            ("query_all_players", {}, "server"),
            ("query_player_state", {}, "server"),
            ("world_time_get", {}, "server"),
            ("notify_players", {"show_time": 1.0, "title": "hi"}, "server"),
        ):
            with self.subTest(cmd=cmd):
                status, body = state.enqueue_command(cmd, args, peer=peer)
                self.assertEqual(status, 200, body)


class ProfileOffTest(unittest.TestCase):
    def test_profile_off_changes_nothing(self) -> None:
        state = _state()
        self.assertFalse(state.live_profile)
        for cmd, args, peer in (
            ("camera_get", {}, "client"),
            ("key_press", {"dik": 1}, "client"),
            ("player_teleport", dict(TELEPORT), "server"),
            ("query_all_players", {}, "server"),
        ):
            with self.subTest(cmd=cmd):
                status, body = state.enqueue_command(cmd, args, peer=peer)
                self.assertEqual(status, 200, body)
        # The pre-B4 refusal for an unknown command is unchanged.
        status, body = state.enqueue_command("dayz_test_run", {})
        self.assertEqual((status, body), (400, {"error": "not_whitelisted"}))

    def test_live_admin_flag_alone_does_not_enable_profile(self) -> None:
        state = _state(live_admin_enabled=True)
        status, body = state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(status, 200, body)


class EnvWiringTest(unittest.TestCase):
    def test_env_parsing_is_explicit_and_off_by_default(self) -> None:
        self.assertEqual(loopback.live_profile_from_env({}), (False, False))
        self.assertEqual(
            loopback.live_profile_from_env({"DAYZ_MCP_LIVE_PROFILE": "live"}),
            (True, False),
        )
        self.assertEqual(
            loopback.live_profile_from_env(
                {"DAYZ_MCP_LIVE_PROFILE": "1", "DAYZ_MCP_LIVE_ADMIN": "true"}
            ),
            (True, True),
        )
        self.assertEqual(
            loopback.live_profile_from_env({"DAYZ_MCP_LIVE_PROFILE": "off"}),
            (False, False),
        )

    def test_daemon_builds_state_from_environment(self) -> None:
        config = SimpleNamespace(
            enable_exec_enforce=False,
            exec_allowlist=None,
            require_version=False,
            expected_game_version=None,
            port=8765,
            instance_token=None,
        )
        with patch.dict(os.environ, {"DAYZ_MCP_LIVE_PROFILE": "live"}, clear=False):
            state = daemon.build_server_state(config, "test-key")
        self.assertTrue(state.live_profile)
        self.assertFalse(state.live_admin_enabled)

    def test_daemon_builds_state_off_without_environment(self) -> None:
        config = SimpleNamespace(
            enable_exec_enforce=False,
            exec_allowlist=None,
            require_version=False,
            expected_game_version=None,
            port=8765,
            instance_token=None,
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"DAYZ_MCP_LIVE_PROFILE", "DAYZ_MCP_LIVE_ADMIN"}
        }
        with patch.dict(os.environ, env, clear=True):
            state = daemon.build_server_state(config, "test-key")
        self.assertFalse(state.live_profile)
        self.assertFalse(state.live_admin_enabled)


if __name__ == "__main__":
    unittest.main()
