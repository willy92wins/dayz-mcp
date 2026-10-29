"""B4 (fb-20261028-131107-9249): live profile the daemon enforces.

With the profile on, ServerState.enqueue_command refuses, with a named
error, every command outside the closed live list, whatever client sends
it, and Handler._handle_lifecycle refuses every /lifecycle/* route (the
routes the real dayz_test* tools use) before delegating to the lifecycle.
Camera, input, UI, spawn, exec_enforce, bot_*, vehicle_* and the test
tools stay out even when admin mutations are enabled: only the explicit
LIVE_ADMIN_COMMANDS are gated on admin, refused with
live_profile_admin_disabled until expressly enabled. Server/player/world
reads and warnings (notify_players) are allowed by default. Daemon-
internal publications, owner cleanup and releases are not filtered.
Profile off changes nothing.
"""

from __future__ import annotations

import json
import os
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import daemon, loopback, tool_pack
from dayz_mcp.session_coordination import ClientIdentity
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


class AlwaysRefusedTest(unittest.TestCase):
    """Refused with live_profile_refused, with and without admin."""

    def test_excluded_families_refused_regardless_of_admin(self) -> None:
        refused = (
            ("bot_start", {}, "server"),
            ("bot_stop", {}, "server"),
            ("player_move", {}, "client"),
            ("weapon_fire", {}, "client"),
            ("player_view_set", {}, "client"),
            ("action_cursor", {}, "client"),
            ("action_cursor_ids", {}, "server"),
            ("input_describe", {}, "client"),
            ("vehicle_enter", {}, "server"),
            ("vehicle_control", {}, "client"),
            ("vehicle_telemetry", {}, "client"),
            ("player_kill", {}, "server"),
            ("world_spawn", {}, "server"),
            ("restore_gameplay", {}, "client"),
        )
        for admin in (False, True):
            state = _state(live_profile=True, live_admin_enabled=admin)
            for cmd, args, peer in refused:
                with self.subTest(cmd=cmd, admin=admin):
                    status, body = state.enqueue_command(cmd, args, peer=peer)
                    self.assertEqual(
                        (status, body), (403, {"error": "live_profile_refused"})
                    )


class PartitionTest(unittest.TestCase):
    """Every bridge command is exactly one of allowed, admin or refused."""

    def test_partition_of_all_bridge_commands(self) -> None:
        all_commands = loopback.SERVER_COMMANDS | loopback.CLIENT_COMMANDS

        def classify(admin: bool) -> tuple[set[str], set[str], set[str]]:
            allowed, gated, refused = set(), set(), set()
            for name in all_commands:
                refusal = loopback.live_profile_refusal(
                    name, enabled=True, admin_enabled=admin
                )
                if refusal is None:
                    allowed.add(name)
                elif refusal == loopback.LIVE_PROFILE_ADMIN_DISABLED:
                    gated.add(name)
                else:
                    self.assertEqual(refusal, loopback.LIVE_PROFILE_REFUSED)
                    refused.add(name)
            return allowed, gated, refused

        allowed, gated, refused = classify(admin=False)
        self.assertEqual(allowed, set(loopback.LIVE_ALLOWED_COMMANDS))
        self.assertEqual(gated, set(loopback.LIVE_ADMIN_COMMANDS))
        self.assertEqual(allowed | gated | refused, set(all_commands))
        self.assertFalse(allowed & gated)
        self.assertFalse((allowed | gated) & refused)

        allowed_with_admin, gated_with_admin, refused_with_admin = classify(
            admin=True
        )
        self.assertEqual(allowed_with_admin, allowed | gated)
        self.assertEqual(gated_with_admin, set())
        self.assertEqual(refused_with_admin, refused)

    def test_admin_list_is_explicit_and_excludes_excluded_families(self) -> None:
        self.assertLessEqual(
            loopback.LIVE_ADMIN_COMMANDS, loopback.SERVER_COMMANDS
        )
        # Each name is a bridge command the admin pack's tools enqueue.
        self.assertLessEqual(
            loopback.LIVE_ADMIN_COMMANDS, tool_pack.ADMIN_TOOL_NAMES
        )
        self.assertFalse(
            loopback.LIVE_ADMIN_COMMANDS & loopback.LIVE_ALLOWED_COMMANDS
        )
        excluded = {
            name
            for name in loopback.SERVER_COMMANDS | loopback.CLIENT_COMMANDS
            if name.startswith(("bot_", "vehicle_", "camera_", "ui_"))
        } | {
            "player_move",
            "weapon_fire",
            "player_view_set",
            "action_cursor",
            "action_cursor_ids",
            "input_describe",
            "world_spawn",
            "world_spawn_batch",
            "restore_gameplay",
        }
        self.assertFalse(loopback.LIVE_ADMIN_COMMANDS & excluded)
        self.assertFalse(loopback.LIVE_ALLOWED_COMMANDS & excluded)
        # Only server/player/world state reads (plus notify_players) allowed.
        self.assertLessEqual(
            loopback.LIVE_ALLOWED_COMMANDS,
            loopback.SERVER_COMMANDS | {"notify_players"},
        )


IDENTITY_PAYLOAD = {
    "platform": "codex",
    "pid": 11,
    "ppid": 1,
    "started_at_utc": "2026-07-15T00:00:00Z",
    "session_id": "A",
    "task_label": "live-profile",
}


class _FakeLifecycle:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.manifest = self

    def list_runs(self) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                state="RUNNING_IDLE", owner_session_id=None, run_id="R"
            )
        ]

    def start_run(self, client, token, value):
        self.calls.append("start")
        return {"ok": True, "run_id": "R", "state": "RUNNING"}

    def ack_run(self, client, token, run_id, launch_operation_id):
        self.calls.append("ack")
        return {"ok": True, "run_id": run_id, "state": "RUNNING"}

    def stop_run(self, client, token, value):
        self.calls.append("stop")
        return {"ok": True, "run_id": value, "state": "EXITED"}

    def close_run(self, client, token, value):
        self.calls.append("close")
        return {"ok": True, "run_id": value, "state": "EXITED"}

    def close_run_roles(self, client, token, value, roles):
        self.calls.append("close_roles")
        return {"ok": True, "run_id": value, "state": "EXITED"}

    def foreground_run(self, client, token, value, hold_s):
        self.calls.append("foreground")
        return {"ok": True, "run_id": value, "state": "RUNNING"}

    def adopt_run(self, client, token, value):
        self.calls.append("adopt")
        return {"ok": True, "run_id": value, "state": "RUNNING"}

    def reap_dead_run(self, client, token, value):
        self.calls.append("reap")
        return {"ok": True, "run_id": value, "state": "EXITED"}

    def status(self, client):
        self.calls.append("status")
        return {"runs": [], "retail_quarantine": False}


_LIFECYCLE_BODIES: dict[str, dict] = {
    # The routes the real dayz_test_run / dayz_test_stop / dayz_test_close /
    # dayz_test_foreground tools post to, plus the remaining routes.
    "start": {
        "identity": IDENTITY_PAYLOAD,
        "lease_token": "token",
        "request": {"argv": ["diag"]},
    },
    "ack": {
        "identity": IDENTITY_PAYLOAD,
        "lease_token": "token",
        "run_id": "R",
        "launch_operation_id": "OP",
    },
    "stop": {"identity": IDENTITY_PAYLOAD, "lease_token": "token", "run_id": "R"},
    "close": {"identity": IDENTITY_PAYLOAD, "lease_token": "token", "run_id": "R"},
    "foreground": {
        "identity": IDENTITY_PAYLOAD,
        "lease_token": "token",
        "run_id": "R",
        "hold_s": 1,
    },
    "adopt": {"identity": IDENTITY_PAYLOAD, "lease_token": "token", "run_id": "R"},
    "reap": {"identity": IDENTITY_PAYLOAD, "lease_token": "token", "run_id": "R"},
    "status": {"identity": IDENTITY_PAYLOAD},
}


def _lifecycle_reply(
    state: loopback.ServerState, action: str, body: dict
) -> tuple[int, dict]:
    """One /lifecycle/<action> through the real handler, no socket."""
    handler = loopback.Handler.__new__(loopback.Handler)
    handler.server = types.SimpleNamespace(state=state, log_sink=lambda _m: None)
    replies: list[tuple[int, dict]] = []
    handler._read_json = lambda: json.loads(json.dumps(body))  # type: ignore[method-assign]
    handler._json = lambda code, payload: replies.append(  # type: ignore[method-assign]
        (code, json.loads(json.dumps(payload)))
    )
    handler._handle_lifecycle(action)
    if len(replies) != 1:
        raise AssertionError(f"{action} replied {len(replies)} times")
    return replies[0]


class LifecycleRoutesTest(unittest.TestCase):
    def _live_state(self, admin: bool) -> tuple[loopback.ServerState, _FakeLifecycle]:
        state = loopback.ServerState(
            "test-key", live_profile=True, live_admin_enabled=admin
        )
        lifecycle = _FakeLifecycle()
        state.lifecycle = lifecycle
        return state, lifecycle

    def test_all_lifecycle_routes_refused_with_and_without_admin(self) -> None:
        for admin in (False, True):
            state, lifecycle = self._live_state(admin)
            for action, body in _LIFECYCLE_BODIES.items():
                with self.subTest(action=action, admin=admin):
                    status, payload = _lifecycle_reply(state, action, body)
                    self.assertEqual(
                        (status, payload),
                        (403, {"error": "live_profile_refused"}),
                    )
            with self.subTest(action="close_roles", admin=admin):
                body = dict(_LIFECYCLE_BODIES["close"], roles=["server"])
                status, payload = _lifecycle_reply(state, "close", body)
                self.assertEqual(
                    (status, payload), (403, {"error": "live_profile_refused"})
                )
            self.assertEqual(lifecycle.calls, [])

    def test_lifecycle_routes_unchanged_with_profile_off(self) -> None:
        state = loopback.ServerState("test-key")
        lifecycle = _FakeLifecycle()
        state.lifecycle = lifecycle
        for action, body in _LIFECYCLE_BODIES.items():
            with self.subTest(action=action):
                status, _payload = _lifecycle_reply(state, action, body)
                self.assertEqual(status, 200)
        self.assertEqual(
            sorted(lifecycle.calls),
            sorted(_LIFECYCLE_BODIES.keys()),
        )

    def test_grant_adoption_never_delegates_with_profile_on(self) -> None:
        state, lifecycle = self._live_state(admin=True)
        handler = loopback.Handler.__new__(loopback.Handler)
        handler.server = types.SimpleNamespace(
            state=state, log_sink=lambda _m: None
        )
        client = ClientIdentity.from_payload(IDENTITY_PAYLOAD)
        payload = {"status": "active", "lease_token": "token"}
        result = handler._adopt_on_grant(client, dict(payload))
        self.assertEqual(result, payload)
        self.assertNotIn("adopted_run", result)
        self.assertEqual(lifecycle.calls, [])


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
