"""B1 (fb-20261028-131140-ea02, SUPUESTO D1 option a): godmode and the
water/energy refill are OFF by default for everyone. They turn on by default
only when the MCP bridge config on disk says profile=test, which only the
harness writes. No config, or a config without profile=test, leaves players
mortal and without refill. player_godmode(on=true/false) stays explicit.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dayz_mcp import loopback, server
from tests._addon_paths import addon_root

SCRIPTS = addon_root() / "scripts"
CARE = (SCRIPTS / "4_World" / "MCP_PlayerCare.c").read_text(encoding="utf-8")
MISSION = (SCRIPTS / "5_Mission" / "MissionServer.c").read_text(encoding="utf-8")
BRIDGE = (SCRIPTS / "5_Mission" / "MCPBridge.c").read_text(encoding="utf-8")
MESSAGES = (SCRIPTS / "5_Mission" / "MCPMessages.c").read_text(encoding="utf-8")


def _body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for i in range(brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : i]
    raise AssertionError(signature)


class AddonDefaultOffTest(unittest.TestCase):
    def test_config_carries_profile_and_bridge_reads_test_only(self) -> None:
        cfg = re.search(r"class MCPConfig\s*\{([^}]*)\}", MESSAGES)
        self.assertIsNotNone(cfg)
        self.assertIn("string profile;", cfg.group(1))
        body = _body(BRIDGE, "static bool ConfigIsTestProfile()")
        self.assertIn('"$profile:dayz_mcp.json"', body)
        self.assertIn('"$mission:dayz_mcp.json"', body)
        self.assertIn('cfg.profile == "test"', body)
        self.assertIn("JsonFileLoader<MCPConfig>.JsonLoadFile(path, cfg);", body)

    def test_mission_start_sets_default_from_config_after_reset(self) -> None:
        start = _body(MISSION, "override void OnMissionStart()")
        self.assertLess(
            start.index("MCPGodmode.ResetMission();"),
            start.index("MCPGodmode.SetDefaultOn(MCPBridge.ConfigIsTestProfile());"),
        )

    def test_godmode_default_is_off_and_explicit_choice_wins(self) -> None:
        reset = _body(CARE, "static void ResetMission()")
        self.assertIn("s_DefaultOn = false;", reset)
        self.assertIn("s_AnyOn = false;", reset)
        is_on_for = _body(CARE, "static bool IsOnFor(string plainId)")
        self.assertIn("s_Choice.Get(plainId)", is_on_for)
        self.assertIn("return s_DefaultOn;", is_on_for)
        self.assertNotIn("return true;", is_on_for)
        ready = _body(CARE, "static void OnBodyReady(PlayerBase player, PlayerIdentity identity)")
        self.assertIn("on = s_DefaultOn;", ready)
        self.assertNotIn("on = true;", ready)
        remember = _body(CARE, "protected static void Remember(string plainId, bool on)")
        self.assertIn("s_Choice.Set(plainId, on);", remember)
        choose = _body(CARE, "static void Choose(PlayerBase player, string plainId, bool on)")
        self.assertIn("Remember(plainId, on);", choose)
        self.assertIn("Apply(player, on);", choose)

    def test_tick_without_any_godmode_body_leaves_before_the_scan(self) -> None:
        tick = _body(CARE, "static void Tick(float timeslice)")
        self.assertIn("if (!s_AnyOn)", tick)
        self.assertLess(tick.index("if (!s_AnyOn)"), tick.index("GetGame().GetPlayers(s_Players);"))
        apply_body = _body(CARE, "protected static void Apply(PlayerBase player, bool on)")
        self.assertIn("s_AnyOn = true;", apply_body)


class HarnessWritesTestProfileTest(unittest.TestCase):
    def test_seeded_config_says_profile_test(self) -> None:
        with TemporaryDirectory() as tmp:
            profiles = Path(tmp) / "profiles"
            profiles.mkdir()
            state = loopback.ServerState("live-key", config_port=8765)
            minted = state.prepare("run-b1", "server", str(profiles))
            written = json.loads((profiles / "dayz_mcp.json").read_text(encoding="utf-8"))
            self.assertEqual(written["profile"], "test")
            self.assertEqual(written["instance"], minted)

    def test_existing_installer_config_gets_profile_test_on_prepare(self) -> None:
        with TemporaryDirectory() as tmp:
            profiles = Path(tmp) / "profiles"
            profiles.mkdir()
            (profiles / "dayz_mcp.json").write_text(
                json.dumps({"url": "http://127.0.0.1:8765/", "key": "k", "pollHz": 5}),
                encoding="utf-8",
            )
            state = loopback.ServerState("k", config_port=8765)
            state.prepare("run-b1", "server", str(profiles))
            written = json.loads((profiles / "dayz_mcp.json").read_text(encoding="utf-8"))
            self.assertEqual(written["profile"], "test")


class DescriptionTruthTest(unittest.TestCase):
    def test_godmode_line_no_longer_says_on_by_default_for_all(self) -> None:
        line = server.GODMODE_DEFAULT_LINE
        self.assertIn("Godmode is OFF by default", line)
        self.assertIn("profile=test", line)
        self.assertIn("player_godmode(on=true)", line)
        self.assertNotIn("Godmode is ON by default", line)
        self.assertNotIn("in every run that loads DayZ_MCP", line)


if __name__ == "__main__":
    unittest.main()
