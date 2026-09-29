"""fb-20260927-171545-0063 part 2: native weapon raise, aim, fire, sights.

Client mutations. The overrides and the shot live in Enforce; these tests pin
the wire contract and the source shape the game will compile. They do not
launch DayZ.
"""
from __future__ import annotations

import math
import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, server
from dayz_mcp.server import EXPECTED_SERVER_ARG_CONTRACT_HASH, LEASE_TOOL_LINE
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease
from tests._addon_paths import addon_root
from tests.bridge_client_capabilities_helpers import announced_caps, dispatch_census


BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
CLIENT = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
WEAPON = addon_root() / "scripts" / "4_World" / "MCP_Weapon.c"

VERBS = ("weapon_raise", "weapon_aim", "weapon_fire", "weapon_sights")


def _method_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated method: {signature}")


class WeaponActionIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        from tests.fence_helpers import bind_both_peers

        bind_both_peers(self.state)

    def test_four_verbs_are_leased_client_mutations(self) -> None:
        for verb in VERBS:
            with self.subTest(verb=verb):
                self.assertIn(verb, loopback.CLIENT_COMMANDS)
                self.assertNotIn(verb, loopback.SERVER_COMMANDS)
                self.assertEqual(loopback.peer_for_command(verb), "client")
                self.assertNotIn(verb, READ_ONLY_COMMANDS)
                self.assertTrue(command_requires_lease(verb))

    def test_minimal_payloads_enqueue_on_the_client(self) -> None:
        payloads = {
            "weapon_raise": {"raised": True, "hold_ttl_s": 3.0},
            "weapon_aim": {"dx": 0.0, "dy": 0.0},
            "weapon_fire": {},
            "weapon_sights": {"mode": "ironsights"},
        }
        for verb, args in payloads.items():
            with self.subTest(verb=verb):
                status, body = self.state.enqueue_command(verb, args)
                self.assertEqual(status, 200)
                self.assertEqual(body["peer"], "client")
                self.assertEqual(body["cmd"], verb)

    def test_bounds_and_enums_are_rejected(self) -> None:
        rejected = (
            ("weapon_raise", {}),
            ("weapon_raise", {"raised": True}),
            ("weapon_raise", {"hold_ttl_s": 3.0}),
            ("weapon_raise", {"raised": 1, "hold_ttl_s": 3.0}),
            ("weapon_raise", {"raised": True, "hold_ttl_s": 0}),
            ("weapon_raise", {"raised": True, "hold_ttl_s": 0.0}),
            ("weapon_raise", {"raised": True, "hold_ttl_s": -0.1}),
            ("weapon_raise", {"raised": True, "hold_ttl_s": 30.5}),
            ("weapon_raise", {"raised": True, "hold_ttl_s": float("nan")}),
            ("weapon_raise", {"raised": True, "hold_ttl_s": float("inf")}),
            ("weapon_raise", {"raised": True, "hold_ttl_s": 3.0, "extra": 1}),
            ("weapon_aim", {}),
            ("weapon_aim", {"dx": 0.0}),
            ("weapon_aim", {"dx": 3.141594, "dy": 0.0}),
            ("weapon_aim", {"dx": 0.0, "dy": -3.141594}),
            ("weapon_aim", {"dx": True, "dy": 0.0}),
            ("weapon_aim", {"dx": float("nan"), "dy": 0.0}),
            ("weapon_aim", {"dx": 0.0, "dy": 0.0, "extra": 1}),
            ("weapon_fire", {"extra": 1}),
            ("weapon_sights", {}),
            ("weapon_sights", {"mode": "red_dot"}),
            ("weapon_sights", {"mode": ["ironsights"]}),
            ("weapon_sights", {"mode": "none", "extra": 1}),
        )
        for verb, args in rejected:
            with self.subTest(verb=verb, args=args):
                status, body = self.state.enqueue_command(verb, args)
                self.assertEqual(status, 400)
                self.assertEqual(body, {"error": "bad_args"})

    def test_pi_passes_the_aim_cap_and_the_hash_is_unchanged(self) -> None:
        # 3.141593 is pi rounded up. A tighter literal would reject math.pi.
        self.assertEqual(
            loopback.validate_command_args(
                "weapon_aim", {"dx": math.pi, "dy": -math.pi}
            ),
            (True, None),
        )
        self.assertGreater(loopback.WEAPON_AIM_ABS_MAX, math.pi)
        self.assertEqual(EXPECTED_SERVER_ARG_CONTRACT_HASH, "3c77a99c95fd05a4")
        bridge = BRIDGE.read_text(encoding="utf-8")
        self.assertIn('SERVER_ARG_CONTRACT_HASH = "3c77a99c95fd05a4"', bridge)
        for verb in VERBS:
            self.assertNotIn(f'"{verb}"', bridge)


class WeaponActionEnforceContractTest(unittest.TestCase):
    def test_client_census_lists_the_four_and_keeps_ui_dialog_last(self) -> None:
        source = CLIENT.read_text(encoding="utf-8")
        names = dispatch_census(source)
        self.assertEqual(names[-1], "ui_dialog")
        for verb in VERBS:
            self.assertIn(verb, names)
            self.assertIn(verb, announced_caps(source))
        self.assertNotIn('"hands_take"', source)
        self.assertNotIn('"weapon_state"', source)
        self.assertNotIn("SendInput", source)

    def test_exclusive_jobs_still_block_and_weapon_reads_do_not(self) -> None:
        source = CLIENT.read_text(encoding="utf-8")
        camera = _method_body(source, "protected bool DispatchCameraSet(")
        get_in = _method_body(source, "protected bool DispatchVehicleGetInClient(")
        self.assertIn("HasExclusiveJob()", camera)
        self.assertIn("HasExclusiveJob()", get_in)
        exclusive = _method_body(source, "protected bool HasExclusiveJob(")
        self.assertIn('CountOfKind("weapon_action")', exclusive)
        for signature in (
            "protected bool DispatchWeaponRaise(",
            "protected bool DispatchWeaponAim(",
            "protected bool DispatchWeaponFire(",
            "protected bool DispatchWeaponSights(",
        ):
            body = _method_body(source, signature)
            self.assertNotIn("HasExclusiveJob()", body)
            self.assertNotIn("SendInput", body)

    def test_local_player_comparisons_cast_getplayer(self) -> None:
        # DayZDiag 1.29: != between PlayerBase and DayZPlayer (GetPlayer, game.c:946)
        # is an unsafe down-cast and fails the World module. Both sites store
        # PlayerBase.Cast before the identity check.
        weapon = WEAPON.read_text(encoding="utf-8")
        self.assertNotIn("!= GetGame().GetPlayer()", weapon)
        self.assertNotIn("== GetGame().GetPlayer()", weapon)
        cast = "live = PlayerBase.Cast(GetGame().GetPlayer());"
        handler = _method_body(weapon, "static void OnCommandHandler(")
        command = _method_body(weapon, "override void CommandHandler(")
        self.assertIn(cast, handler)
        self.assertIn("if (player != live)", handler)
        self.assertLess(handler.index(cast), handler.index("if (player != live)"))
        self.assertIn(cast, command)
        self.assertIn("if (this != live)", command)
        self.assertLess(command.index(cast), command.index("if (this != live)"))

    def test_fire_runs_from_command_handler_not_the_mission_bridge(self) -> None:
        client = CLIENT.read_text(encoding="utf-8")
        weapon = WEAPON.read_text(encoding="utf-8")
        self.assertNotIn(".Fire(", client)
        command = _method_body(weapon, "override void CommandHandler(")
        self.assertLess(
            command.index("super.CommandHandler("),
            command.index("MCPWeaponControl.OnCommandHandler("),
        )
        consume = _method_body(weapon, "static void ConsumeFire(")
        handler = _method_body(weapon, "static void OnCommandHandler(")
        self.assertIn("manager.Fire(", consume)
        self.assertNotIn("new ", consume)
        self.assertNotIn("%", consume)
        self.assertNotIn("new ", handler)
        self.assertNotIn("%", handler)
        reasons = (
            "no_weapon_in_hands",
            "weapon_lifted",
            "not_raised",
            "weapon_destroyed",
            "inventory_processing",
            "raise_not_completed",
            "fighting",
            "cooldown",
            "chamber_empty",
            "chamber_fired_out",
            "jammed",
            "cannot_fire",
        )
        previous = -1
        for reason in reasons:
            found = consume.index(f'"{reason}"')
            self.assertGreater(found, previous, reason)
            previous = found
        self.assertLess(consume.index("s_FirePending = false"), consume.index("manager.Fire("))
        self.assertLess(consume.index("held.CanFire()"), consume.index("manager.Fire("))
        self.assertLess(consume.index("manager.CanFire(held)"), consume.index("manager.Fire("))
        self.assertLess(consume.index("manager.CanFire(held)"), consume.index("player.IsAlive()"))

    def test_consume_fire_rechecks_the_actor_immediately_before_fire(self) -> None:
        consume = _method_body(WEAPON.read_text(encoding="utf-8"), "static void ConsumeFire(")
        fire_at = consume.index("manager.Fire(")
        previous = consume.index("manager.CanFire(held)")
        for predicate, reason in (
            ("player.IsAlive()", "player_dead"),
            ("player.IsUnconscious()", "player_unconscious"),
            ("player.IsRestrained()", "player_restrained"),
            ("player.IsInVehicle()", "player_in_vehicle"),
        ):
            found = consume.index(predicate)
            named = consume.index(f'"{reason}"')
            self.assertGreater(found, previous, predicate)
            self.assertLess(found, fire_at, predicate)
            self.assertGreater(named, previous, reason)
            self.assertLess(named, fire_at, reason)
            previous = found

    def test_overrides_are_bounded_and_restore_clears_them(self) -> None:
        weapon = WEAPON.read_text(encoding="utf-8")
        client = CLIENT.read_text(encoding="utf-8")
        self.assertIn("static const float RAISE_DEFAULT_TTL_S = 3.0;", weapon)
        self.assertIn("static const float RAISE_MAX_TTL_S = 30.0;", weapon)
        self.assertIn("static const float AIM_CHANGE_ABS_MAX = 3.141593;", weapon)
        self.assertNotIn("SendInput", weapon)
        disable = _method_body(weapon, "static void DisableRaiseOn(")
        self.assertIn(
            "OverrideRaise(HumanInputControllerOverrideType.DISABLED",
            disable,
        )
        begin = _method_body(weapon, "static int BeginRaise(")
        self.assertIn("OverrideRaise(HumanInputControllerOverrideType.ENABLED, true)", begin)
        self.assertIn("s_RaiseDeadlineS = now + ttlS", begin)
        aim = _method_body(weapon, "static int BeginAim(")
        self.assertIn("OverrideAimChangeX(HumanInputControllerOverrideType.ONE_FRAME", aim)
        self.assertIn("OverrideAimChangeY(HumanInputControllerOverrideType.ONE_FRAME", aim)
        maintain = _method_body(weapon, "static void MaintainFromTick(")
        self.assertIn("DisableRaiseOn(live)", maintain)
        self.assertNotIn("s_RaiseGen = s_RaiseGen + 1", maintain)
        self.assertLess(maintain.index("if (!IsBusy())"), maintain.index("DisableRaiseOn(live)"))
        release = _method_body(weapon, "static void ReleaseAll(")
        self.assertIn('s_RaiseAbort = why', release)
        self.assertIn("s_RaiseGen = s_RaiseGen + 1", release)
        finish = _method_body(weapon, "static void FinishWatch(")
        self.assertNotIn("DisableRaiseOn", finish)
        self.assertNotIn("weapon_raise", finish)
        self.assertIn("generation == s_AimGen", finish)
        self.assertIn("generation == s_FireGen", finish)
        for token in (
            "player_dead",
            "player_unconscious",
            "player_restrained",
            "player_in_vehicle",
            "no_player",
            "no_weapon_in_hands",
            "no_input_controller",
            "no_optics",
            "no_ironsights",
            "aim_unreadable",
            "bad_hold_ttl_s",
            "bad_dx",
            "bad_dy",
            "bad_mode",
            "superseded",
            "cleared",
            "weapon_read_timeout",
            "player_changed",
            "SetIronsights(true)",
            "SwitchOptics(optic, true)",
            "SwitchOptics(optic, false)",
            "ExitSights()",
            "GetAttachedOptics()",
            "CanEnterIronsights()",
        ):
            with self.subTest(token=token):
                self.assertIn(token, client + weapon)
        restore = _method_body(client, "protected void RestoreGameplay(")
        self.assertLess(restore.index('ReleaseAll("cleared")'), restore.index("if (!GetGame())"))
        post = _method_body(client, "protected void PostWeaponActionJob(")
        self.assertLess(post.index("FinishWatch("), post.index("PostResult("))
        timeout = _method_body(client, "override void MCP_PostJobTimeout(")
        self.assertIn('PostWeaponActionJob(job, "weapon_read_timeout")', timeout)
        sights = _method_body(client, "protected bool DispatchWeaponSights(")
        self.assertNotIn("SetOptics(", sights)

    def test_sights_follow_vanilla_transitions(self) -> None:
        sights = _method_body(CLIENT.read_text(encoding="utf-8"), "protected bool DispatchWeaponSights(")
        irons = sights[sights.index('mode == "ironsights"') : sights.index('mode == "optics"')]
        optics = sights[sights.index('mode == "optics"') : sights.index('mode == "none"')]
        leave = sights[sights.index('mode == "none"') :]
        # Optics to ironsights: leave the optic, then enter ironsights.
        self.assertLess(irons.index("CanEnterIronsights()"), irons.index("SwitchOptics(optic, false)"))
        self.assertLess(irons.index("GetAttachedOptics()"), irons.index("SwitchOptics(optic, false)"))
        self.assertLess(irons.index("SwitchOptics(optic, false)"), irons.index("SetIronsights(true)"))
        self.assertNotIn("SetOptics(", irons)
        # Ironsights to optics: leave ironsights, then enter the optic.
        self.assertLess(optics.index("GetAttachedOptics()"), optics.index('"no_optics"'))
        self.assertLess(optics.index('"no_optics"'), optics.index("SetIronsights(false)"))
        self.assertLess(optics.index("SetIronsights(false)"), optics.index("SwitchOptics(optic, true)"))
        self.assertNotIn("SetOptics(", optics)
        # none leaves both, which is what ExitSights does.
        self.assertIn("ExitSights()", leave)
        self.assertNotIn("SetIronsights(", leave)
        self.assertNotIn("SwitchOptics(", leave)
        self.assertNotIn("SetOptics(", leave)


class WeaponActionAppToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_tools_are_leased_and_report_the_read_back(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        tools = {tool.name: tool for tool in await app.list_tools()}
        claims = {
            "weapon_raise": ("input_raised", "expires_at"),
            "weapon_aim": ("aim_lr", "aim_change_0"),
            "weapon_fire": ("weapon_state", "accepted=true"),
            "weapon_sights": ("ironsights", "IsInOptics"),
        }
        for verb, needles in claims.items():
            description = tools[verb].description or ""
            self.assertTrue(description.startswith(LEASE_TOOL_LINE), verb)
            self.assertIn("No OS input", description)
            for needle in needles:
                self.assertIn(needle, description, verb)

        with patch.object(
            runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})
        ) as call:
            await app.call_tool("weapon_raise", {"raised": True, "timeout_s": 1.0})
            call.assert_awaited_once_with(
                "weapon_raise",
                {"raised": True, "hold_ttl_s": 3.0},
                "client",
                1.0,
            )

        with patch.object(
            runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})
        ) as call:
            await app.call_tool(
                "weapon_aim", {"dx": 0.01, "dy": -0.02, "timeout_s": 2.0}
            )
            call.assert_awaited_once_with(
                "weapon_aim", {"dx": 0.01, "dy": -0.02}, "client", 2.0
            )

        with patch.object(
            runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1, "accepted": False, "error": "not_raised"})
        ) as call:
            result = await app.call_tool("weapon_fire", {"timeout_s": 1.0})
            call.assert_awaited_once_with("weapon_fire", {}, "client", 1.0)
            self.assertIsNotNone(result)

        with patch.object(
            runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})
        ) as call:
            await app.call_tool(
                "weapon_sights", {"mode": "optics", "timeout_s": 1.0}
            )
            call.assert_awaited_once_with(
                "weapon_sights", {"mode": "optics"}, "client", 1.0
            )

    async def test_tool_layer_rejects_out_of_range_before_the_bridge(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        refused = (
            ("weapon_raise", {"raised": True, "hold_ttl_s": 0}),
            ("weapon_raise", {"raised": True, "hold_ttl_s": 30.5}),
            ("weapon_raise", {"raised": True, "hold_ttl_s": -1}),
            ("weapon_aim", {"dx": 4.0, "dy": 0.0}),
            ("weapon_sights", {"mode": "red_dot"}),
            ("weapon_raise", {"raised": 1, "hold_ttl_s": 3.0}),
        )
        for verb, args in refused:
            with self.subTest(verb=verb, args=args):
                with patch.object(
                    runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})
                ) as call:
                    with self.assertRaises(Exception):
                        await app.call_tool(verb, args)
                    call.assert_not_awaited()
