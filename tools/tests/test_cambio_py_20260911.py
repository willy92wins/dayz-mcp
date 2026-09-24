"""Offline pins for the 2026-09-11 CAMBIO Python batch + 47c4 diagnosis."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from dayz_mcp.client_steam_bootstrap import (
    diagnose_client_steam_bootstrap,
    snapshot_client_dumps,
)
from dayz_mcp.server_freshness import schema_signal as freshness_schema_signal

_WINDOWS = sys.platform == "win32"


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
            diagnose_client_steam_bootstrap(
                error_code="client_dead_after_ack",
                client_alive=False,
                steam_startup="old_stable_unobserved",
            ),
            "steam_bootstrap",
        )

    def test_observed_startup_is_not_bootstrap_and_does_not_repair(self) -> None:
        self.assertIsNone(
            diagnose_client_steam_bootstrap(
                error_code="client_dead_after_ack",
                client_alive=False,
                steam_startup="observed",
            )
        )
        self.assertIsNone(
            diagnose_client_steam_bootstrap(
                error_code="client_dead_after_ack",
                client_alive=True,
                steam_startup="old_stable_unobserved",
            )
        )
        self.assertIsNone(
            diagnose_client_steam_bootstrap(
                error_code="steam_session_stale",
                client_alive=False,
                steam_startup="old_stable_unobserved",
            )
        )

    def _observed(self, baseline, *, client_alive: object = False) -> str | None:
        return diagnose_client_steam_bootstrap(
            error_code="client_dead_after_ack",
            client_alive=client_alive,
            steam_startup="observed",
            dump_baseline=baseline,
        )

    def test_observed_with_mdmp_api_loaded_no_diagnoses_steam_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            profile_dir = Path(tmp_dir)
            baseline = snapshot_client_dumps([profile_dir])
            mdmp_file = profile_dir / "ErrorMessage_DayZDiag_x64_2026-09-24_03-04-46.mdmp"
            mdmp_file.write_bytes(
                b"header\x00data\x00SteamInternal_SetMinidumpSteamID ... [API loaded no]\x00trailer"
            )
            self.assertEqual(self._observed(baseline), "steam_bootstrap")

    def test_observed_with_mdmp_without_marker_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            profile_dir = Path(tmp_dir)
            baseline = snapshot_client_dumps([profile_dir])
            mdmp_file = profile_dir / "ErrorMessage_DayZDiag_x64_2026-09-24_03-04-46.mdmp"
            mdmp_file.write_bytes(b"some other crash minidump without marker")
            self.assertIsNone(self._observed(baseline))

    def test_observed_with_no_mdmp_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            profile_dir = Path(tmp_dir)
            baseline = snapshot_client_dumps([profile_dir])
            (profile_dir / "DayZDiag_x64.RPT").write_text("just rpt", encoding="utf-8")
            self.assertIsNone(self._observed(baseline))

    def _marker_dump(self, profile_dir: Path, *, age_s: float = 0.0) -> Path:
        dump = profile_dir / "ErrorMessage_DayZDiag_x64_2026-09-24_03-04-46.mdmp"
        dump.write_bytes(
            b"MDMP\x00SteamInternal_SetMinidumpSteamID:  Caching Steam ID:  1 [API loaded no]\x00"
        )
        if age_s:
            stamp = time.time() - age_s
            os.utime(dump, (stamp, stamp))
        return dump

    def test_dump_from_before_this_call_is_not_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            profile_dir = Path(tmp_dir)
            self._marker_dump(profile_dir, age_s=3600)
            baseline = snapshot_client_dumps([profile_dir])
            self.assertIsNone(self._observed(baseline))

    def test_no_baseline_reads_no_dump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            profile_dir = Path(tmp_dir)
            self._marker_dump(profile_dir)
            self.assertIsNone(self._observed(None))

    def test_newest_new_dump_decides_over_an_older_new_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            profile_dir = Path(tmp_dir)
            baseline = snapshot_client_dumps([profile_dir])
            self._marker_dump(profile_dir, age_s=30)
            newer = profile_dir / "ErrorMessage_DayZDiag_x64_2026-09-24_03-05-10.mdmp"
            newer.write_bytes(b"MDMP\x00access violation, no steam line\x00")
            self.assertIsNone(self._observed(baseline))

    def test_mdmp_marker_ignored_if_client_alive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            profile_dir = Path(tmp_dir)
            baseline = snapshot_client_dumps([profile_dir])
            self._marker_dump(profile_dir)
            self.assertIsNone(self._observed(baseline, client_alive=True))


@unittest.skipUnless(_WINDOWS, "FastMCP server import binds Win32 kernel32")
class PublicSurfaceWindowsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from unittest.mock import AsyncMock

        from dayz_mcp import server
        from dayz_mcp.server import ServerConfig, build_app

        self.server = server
        self.app, self.runtime = build_app(
            ServerConfig(key="k", port=0, log_sink=lambda _m: None)
        )
        self.runtime.lifecycle_status = AsyncMock(return_value={"runs": []})

    async def test_public_list_tools_exposes_key_press_and_player_respawn(self) -> None:
        names = {tool.name for tool in await self.app.list_tools()}
        self.assertIn("key_press", names)
        self.assertIn("player_respawn", names)
        press = next(tool for tool in await self.app.list_tools() if tool.name == "key_press")
        self.assertIn("dik", press.inputSchema.get("properties", {}))

    async def test_lookback_max_is_named_in_the_rejection(self) -> None:
        beyond = self.server.WAIT_FOR_LOOKBACK_MAX + 1
        with self.assertRaises(Exception) as ctx:
            await self.app.call_tool(
                "wait_for",
                {
                    "condition": "log_matches",
                    "pattern": "[DayZ-MCP]",
                    "lookback_lines": beyond,
                    "timeout_s": 0.1,
                    "poll_interval_s": 0.05,
                },
            )
        self.assertIn(f"0..{self.server.WAIT_FOR_LOOKBACK_MAX}", str(ctx.exception))

    async def test_world_tools_publish_in_game_effect_boundary(self) -> None:
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        self.assertIn("in_game_required", tools["world_time_set"].description)
        self.assertIn("world-effect", tools["world_time_set"].description)
        self.assertIn("in_game_required", tools["world_weather_set"].description)
        self.assertIn("world-effect", tools["world_weather_set"].description)

    async def test_ui_click_description_names_root_bubble_and_center(self) -> None:
        tool = next(item for item in await self.app.list_tools() if item.name == "ui_click")
        text = tool.description
        self.assertIn("root", text)
        self.assertIn("bubble", text)
        self.assertIn("center", text)
        props = tool.inputSchema["properties"]
        self.assertIn("root", props)
        self.assertIn("bubble", props)

    def test_compact_result_publishes_the_diagnosis(self) -> None:
        from dayz_mcp import dayz_test_tool

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

    def _observed_death_payload(self, baseline: object) -> dict:
        from dayz_mcp import dayz_test_tool

        terminal = dayz_test_tool.WorkerTerminal(
            cleanup_degraded=False,
            error_code="client_dead_after_ack",
            exit_code=1,
            ok=False,
            run_id="run-1",
        )
        return dayz_test_tool._compact_result(
            terminal=terminal,
            project="ExampleMod",
            mode="client",
            started_at=time.monotonic() - 5,
            artifacts_paths=[],
            client_alive=False,
            steam_startup="observed",
            client_dump_baseline=baseline,
        )

    def test_compact_result_names_api_loaded_no_from_this_calls_dump(self) -> None:
        """296b: the measured case, steam_startup=observed plus a new marker dump."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            profile_dir = Path(tmp_dir)
            baseline = snapshot_client_dumps([profile_dir])
            (profile_dir / "ErrorMessage_DayZDiag_x64_2026-09-24_03-04-46.mdmp").write_bytes(
                b"MDMP\x00SteamInternal_SetMinidumpSteamID:  Caching Steam ID:  1 [API loaded no]\x00"
            )
            payload = self._observed_death_payload(baseline)
        self.assertEqual(payload["client_death_diagnosis"], "steam_bootstrap")
        self.assertEqual(payload["steam_startup"], "observed")

    def test_compact_result_ignores_an_older_runs_dump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            profile_dir = Path(tmp_dir)
            dump = profile_dir / "ErrorMessage_DayZDiag_x64_2026-09-24_03-04-46.mdmp"
            dump.write_bytes(b"[API loaded no]")
            baseline = snapshot_client_dumps([profile_dir])
            payload = self._observed_death_payload(baseline)
        self.assertIsNone(payload["client_death_diagnosis"])


if __name__ == "__main__":
    unittest.main()
