"""Offline pins for the 2026-09-11 CAMBIO Python batch + 47c4 diagnosis."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from dayz_mcp import dayz_test_tool, server
from dayz_mcp.server import ServerConfig, build_app
from dayz_mcp.server_freshness import schema_signal as freshness_schema_signal


class KeyPressRespawnExposureTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_list_tools_exposes_both_verbs(self) -> None:
        app, _runtime = build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))
        names = {tool.name for tool in await app.list_tools()}
        self.assertIn("key_press", names)
        self.assertIn("player_respawn", names)
        press = next(tool for tool in await app.list_tools() if tool.name == "key_press")
        self.assertIn("dik", press.inputSchema.get("properties", {}))


class WaitForLookbackMaxTests(unittest.IsolatedAsyncioTestCase):
    async def test_lookback_max_is_named_in_the_rejection(self) -> None:
        app, runtime = build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))
        runtime.lifecycle_status = AsyncMock(return_value={"runs": []})
        beyond = server.WAIT_FOR_LOOKBACK_MAX + 1
        with self.assertRaises(Exception) as ctx:
            await app.call_tool(
                "wait_for",
                {
                    "condition": "log_matches",
                    "pattern": "[DayZ-MCP]",
                    "lookback_lines": beyond,
                    "timeout_s": 0.1,
                    "poll_interval_s": 0.05,
                },
            )
        self.assertIn(f"0..{server.WAIT_FOR_LOOKBACK_MAX}", str(ctx.exception))


class WorldEffectsContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_world_tools_publish_in_game_effect_boundary(self) -> None:
        app, _runtime = build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))
        tools = {tool.name: tool for tool in await app.list_tools()}
        self.assertIn("in_game_required", tools["world_time_set"].description)
        self.assertIn("world-effect", tools["world_time_set"].description)
        self.assertIn("in_game_required", tools["world_weather_set"].description)
        self.assertIn("world-effect", tools["world_weather_set"].description)


class UiClickContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_description_names_root_bubble_and_center(self) -> None:
        app, _runtime = build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))
        tool = next(item for item in await app.list_tools() if item.name == "ui_click")
        text = tool.description
        self.assertIn("root", text)
        self.assertIn("bubble", text)
        self.assertIn("center", text)
        props = tool.inputSchema["properties"]
        self.assertIn("root", props)
        self.assertIn("bubble", props)


class SchemaSignalFreshnessTests(unittest.TestCase):
    def test_stale_content_is_stale_client_not_unknown(self) -> None:
        self.assertEqual(
            freshness_schema_signal(
                {
                    "stale": ["server.py"],
                    "unreadable": [],
                    "watched_count": 1,
                }
            ),
            "stale_client",
        )
        self.assertEqual(
            freshness_schema_signal(
                {
                    "stale": [],
                    "unreadable": [],
                    "watched_count": 1,
                }
            ),
            "fresh",
        )
        self.assertEqual(
            freshness_schema_signal(
                {
                    "stale": [],
                    "unreadable": ["server.py"],
                    "watched_count": 1,
                }
            ),
            "unknown",
        )


class ClientSteamBootstrapDiagnosisTests(unittest.TestCase):
    def test_old_stable_unobserved_plus_dead_client_is_steam_bootstrap(self) -> None:
        self.assertEqual(
            dayz_test_tool.diagnose_client_steam_bootstrap(
                error_code="client_dead_after_ack",
                client_alive=False,
                steam_startup="old_stable_unobserved",
            ),
            "steam_bootstrap",
        )

    def test_observed_startup_is_not_bootstrap_and_does_not_repair(self) -> None:
        self.assertIsNone(
            dayz_test_tool.diagnose_client_steam_bootstrap(
                error_code="client_dead_after_ack",
                client_alive=False,
                steam_startup="observed",
            )
        )
        self.assertIsNone(
            dayz_test_tool.diagnose_client_steam_bootstrap(
                error_code="client_dead_after_ack",
                client_alive=True,
                steam_startup="old_stable_unobserved",
            )
        )
        self.assertIsNone(
            dayz_test_tool.diagnose_client_steam_bootstrap(
                error_code="steam_session_stale",
                client_alive=False,
                steam_startup="old_stable_unobserved",
            )
        )

    def test_compact_result_publishes_the_diagnosis(self) -> None:
        terminal = dayz_test_tool.WorkerTerminal(
            cleanup_degraded=False,
            error_code="client_dead_after_ack",
            exit_code=1,
            ok=False,
            run_id="run-1",
        )
        payload = dayz_test_tool._compact_result(
            terminal=terminal,
            project="ExampleMod",
            mode="client",
            started_at=0.0,
            artifacts_paths=[],
            client_alive=False,
            steam_startup="old_stable_unobserved",
        )
        self.assertEqual(payload["client_death_diagnosis"], "steam_bootstrap")
        self.assertEqual(payload["steam_startup"], "old_stable_unobserved")
        self.assertEqual(payload["error_code"], "client_dead_after_ack")


if __name__ == "__main__":
    unittest.main()
