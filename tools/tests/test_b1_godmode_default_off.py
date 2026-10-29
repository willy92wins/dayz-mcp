"""B1 (fb-20261028-131140-ea02, SUPUESTO D1 option a): godmode and the
water/energy refill are OFF by default for everyone. They turn on by default
only when the MCP bridge config on disk says profile=test, which only the
harness writes. No config, or a config without profile=test, leaves players
mortal and without refill. player_godmode(on=true/false) stays explicit.
"""
from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from dayz_mcp import loopback, server
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import EnforceString, fake_class, method_body, translate

TOOLS = Path(__file__).resolve().parents[1]
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


CONFIG_SIG = "static bool ConfigIsTestProfile()"
PROFILE_CFG = "$profile:dayz_mcp.json"
MISSION_CFG = "$mission:dayz_mcp.json"


def _run_config_is_test_profile(files: dict[str, dict[str, str] | None]) -> tuple[bool, list[str]]:
    """Run ConfigIsTestProfile translated, against a fake disk.

    `files` maps a config path to its parsed payload, or None for a file
    that exists but LoadFile cannot parse. The generic loader call is the
    one statement the subset translator cannot spell, so it is renamed to
    the LoadConfig stub; the stub does what LoadFile(string, out MCPConfig,
    out string) does on success (fills cfg, returns true) and on failure
    (leaves cfg, returns false). Every branch, comparison and the path
    choice are the reader's own. A reader replaced by `return true;` fails
    the absent/live cases below; `return false;` fails the test case.
    """
    loaded: list[str] = []

    def file_exist(path: EnforceString) -> bool:
        return path.text in files

    def load_config(path: EnforceString, cfg: Any, load_error: EnforceString) -> bool:
        loaded.append(path.text)
        payload = files.get(path.text)
        if payload is None:
            return False
        if "profile" in payload:
            cfg.profile = EnforceString.of(payload["profile"])
        return True

    namespace: dict[str, Any] = {"FileExist": file_exist, "LoadConfig": load_config}
    fake_class(MESSAGES, "MCPConfig", namespace)
    body = method_body(BRIDGE, CONFIG_SIG).replace(
        "JsonFileLoader<MCPConfig>.LoadFile", "LoadConfig"
    )
    reader = translate(
        CONFIG_SIG + "\n{" + body + "}\n",
        CONFIG_SIG,
        namespace,
        class_types=("MCPConfig",),
    )
    return reader(), loaded


class _ChoiceMap:
    """map<string, bool> as Remember/IsOnFor use it, keyed by string bytes."""

    def __init__(self) -> None:
        self.items: dict[bytes, bool] = {}

    def Contains(self, key: EnforceString) -> bool:
        return key.data in self.items

    def Get(self, key: EnforceString) -> bool | None:
        return self.items.get(key.data)

    def Set(self, key: EnforceString, value: bool) -> None:
        self.items[key.data] = value


class _CareBody:
    def __init__(self) -> None:
        self.damage_writes: list[bool] = []

    def SetAllowDamage(self, value: bool) -> None:
        self.damage_writes.append(value)


_GODMODE_MEMBERS = ("s_Choice", "s_DefaultOn", "s_OnCount", "s_AnyOn", "s_Players", "s_TopUpAccumS")


def _godmode_namespace() -> dict[str, Any]:
    """MCPGodmode's choice/switch methods translated, on one static state."""
    namespace: dict[str, Any] = {
        "s_Choice": None,
        "s_DefaultOn": False,
        "s_OnCount": 0,
        "s_AnyOn": False,
        "s_Players": None,
        "s_TopUpAccumS": 0.0,
        "map": _ChoiceMap,
        "array": list,
        "TopUp": lambda player: None,
        "Note": lambda on, plain_id, why: None,
    }
    for signature in (
        "static void ResetMission()",
        "static void SetDefaultOn(bool on)",
        "protected static void Remember(string plainId, bool on)",
        "protected static void Apply(PlayerBase player, bool on)",
        "static void Choose(PlayerBase player, string plainId, bool on)",
    ):
        body = method_body(CARE, signature)
        translate(
            signature + "\n{" + body + "}\n",
            signature,
            namespace,
            class_types=("PlayerBase",),
            members=_GODMODE_MEMBERS,
        )
    return namespace


class AddonDefaultOffTest(unittest.TestCase):
    def test_config_carries_profile_and_bridge_reads_test_only(self) -> None:
        cfg = re.search(r"class MCPConfig\s*\{([^}]*)\}", MESSAGES)
        self.assertIsNotNone(cfg)
        self.assertIn("string profile;", cfg.group(1))
        body = _body(BRIDGE, "static bool ConfigIsTestProfile()")
        self.assertIn('"$profile:dayz_mcp.json"', body)
        self.assertIn('"$mission:dayz_mcp.json"', body)
        self.assertIn('cfg.profile == "test"', body)
        # The checked, non-deprecated loader, with its result and the
        # config's nullness checked before profile is read.
        self.assertIn("JsonFileLoader<MCPConfig>.LoadFile(path, cfg, loadError);", body)
        self.assertIn("if (!loaded)", body)
        self.assertIn("if (!cfg)", body)
        self.assertNotIn("JsonLoadFile", body)

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


class ConfigReaderExecutedTest(unittest.TestCase):
    """ConfigIsTestProfile run (translated), not just read as text."""

    def test_no_config_is_false(self) -> None:
        result, loaded = _run_config_is_test_profile({})
        self.assertFalse(result)
        self.assertEqual(loaded, [])

    def test_config_without_profile_is_false(self) -> None:
        result, loaded = _run_config_is_test_profile(
            {PROFILE_CFG: {"url": "http://127.0.0.1:8765/", "key": "k"}}
        )
        self.assertFalse(result)
        self.assertEqual(loaded, [PROFILE_CFG])

    def test_only_the_exact_test_profile_is_true(self) -> None:
        for profile, expected in (("live", False), ("Test", False), ("", False), ("test", True)):
            with self.subTest(profile=profile):
                result, _loaded = _run_config_is_test_profile({PROFILE_CFG: {"profile": profile}})
                self.assertEqual(result, expected)

    def test_an_unloadable_config_is_false(self) -> None:
        result, _loaded = _run_config_is_test_profile({PROFILE_CFG: None})
        self.assertFalse(result)

    def test_profile_path_wins_over_mission_path(self) -> None:
        result, loaded = _run_config_is_test_profile(
            {PROFILE_CFG: {"profile": "live"}, MISSION_CFG: {"profile": "test"}}
        )
        self.assertFalse(result, "the $mission: test config must not be reached")
        self.assertEqual(loaded, [PROFILE_CFG])

    def test_mission_path_is_the_fallback(self) -> None:
        result, loaded = _run_config_is_test_profile({MISSION_CFG: {"profile": "test"}})
        self.assertTrue(result)
        self.assertEqual(loaded, [MISSION_CFG])


class AnyOnRecalcExecutedTest(unittest.TestCase):
    """s_AnyOn after ON->OFF, run translated: it must not stay latched."""

    def test_switching_the_last_on_choice_off_clears_any_on(self) -> None:
        godmode = _godmode_namespace()
        godmode["ResetMission"]()  # type: ignore[operator]
        body = _CareBody()
        godmode["Choose"](body, EnforceString.of("a"), True)  # type: ignore[operator]
        self.assertTrue(godmode["s_AnyOn"])
        godmode["Choose"](body, EnforceString.of("a"), True)  # type: ignore[operator]
        godmode["Choose"](body, EnforceString.of("a"), False)  # type: ignore[operator]
        self.assertFalse(godmode["s_AnyOn"], "no on choice and default off: Tick must leave at once")
        self.assertEqual(godmode["s_OnCount"], 0)

    def test_any_on_survives_until_the_last_on_choice_goes_off(self) -> None:
        godmode = _godmode_namespace()
        godmode["ResetMission"]()  # type: ignore[operator]
        godmode["Choose"](_CareBody(), EnforceString.of("a"), True)  # type: ignore[operator]
        godmode["Choose"](_CareBody(), EnforceString.of("b"), True)  # type: ignore[operator]
        godmode["Choose"](_CareBody(), EnforceString.of("a"), False)  # type: ignore[operator]
        self.assertTrue(godmode["s_AnyOn"], "b is still on")
        godmode["Choose"](_CareBody(), EnforceString.of("b"), False)  # type: ignore[operator]
        self.assertFalse(godmode["s_AnyOn"])

    def test_any_on_stays_with_the_default_on(self) -> None:
        godmode = _godmode_namespace()
        godmode["ResetMission"]()  # type: ignore[operator]
        godmode["SetDefaultOn"](True)  # type: ignore[operator]
        godmode["Choose"](_CareBody(), EnforceString.of("a"), False)  # type: ignore[operator]
        self.assertTrue(godmode["s_AnyOn"], "default-on bodies still need the top-up pass")


class InstallerConfigsStayMortalTest(unittest.TestCase):
    """A real install writes no profile, so its config leaves godmode off."""

    def test_python_installer_config_payload_has_no_profile(self) -> None:
        tree = ast.parse((TOOLS / "install_mcp.py").read_text(encoding="utf-8"))
        payloads = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Dict)
            and {key.value for key in node.keys if isinstance(key, ast.Constant)} >= {"url", "pollHz"}
        ]
        self.assertEqual(len(payloads), 1)
        keys = {key.value for key in payloads[0].keys if isinstance(key, ast.Constant)}
        self.assertEqual(keys, {"url", "key", "pollHz"})
        self.assertNotIn("profile", keys)

    def test_powershell_installer_config_has_no_profile(self) -> None:
        text = (TOOLS / "install-mcp.ps1").read_text(encoding="utf-8")
        match = re.search(r"\$jsonConfig\s*=\s*@\{(?P<body>.*?)\}", text, re.S)
        self.assertIsNotNone(match)
        body = match.group("body")  # type: ignore[union-attr]
        self.assertIn("pollHz", body)
        self.assertNotIn("profile", body.lower())


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
