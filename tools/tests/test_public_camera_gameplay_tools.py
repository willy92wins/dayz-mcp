"""Public readers and camera controls reuse the existing bridge contracts."""
from __future__ import annotations

import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dayz_mcp import bridge_readiness, loopback, result_prune
from dayz_mcp.server import ServerConfig, build_app
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator, command_requires_lease
from tests.camera_tool_helpers import (
    _FakeClientRuntime,
    BRIDGE, _PATH_OWNER, _ToolCase, _camera_answer, _function,
)
from tests.fence_helpers import INST_CLIENT, PID_CLIENT, bind_both_peers
from tests.enforce_subset_helpers import translate


POSE = {"cam_mode": "orient", "cam_pos": [1.0, 2.0, 3.0], "cam_orientation": [0.0, 90.0, 0.0]}
NEW_TOOLS = {"gameplay_state", "camera_path_release", "camera_set_focus"}


class PublicGameplayToolTest(_ToolCase):
    def test_authorization_is_read_only_and_camera_controls_require_lease(self) -> None:
        coordinator = SessionCoordinator(audit=lambda _: True)
        client = ClientIdentity.from_payload(_PATH_OWNER)
        self.assertTrue(coordinator.authorize(client, None, "gameplay_state").allowed)
        for name in ("camera_path_play", "camera_path_release", "camera_set", "camera_set_focus"):
            decision = coordinator.authorize(client, None, name)
            self.assertFalse(decision.allowed)
            self.assertEqual(decision.error, "lease_required")

    def test_reader_requires_capability_and_accepts_current_bridge(self) -> None:
        state = loopback.ServerState("test-key")
        bind_both_peers(state)
        names = sorted(bridge_readiness._BRIDGE_COMMAND_TOOLS["client"])
        state.record_poll("client", "12~1.30", instance=INST_CLIENT,
                          source_pid=PID_CLIENT, caps=",".join(n for n in names if n != "gameplay_state"))
        self.assertEqual(state.enqueue_command("gameplay_state", {}, peer="client"),
                         (409, {"error": "bridge_capability_missing"}))
        state.record_poll("client", "12~1.30", instance=INST_CLIENT,
                          source_pid=PID_CLIENT, caps=",".join(names))
        status, body = state.enqueue_command("gameplay_state", {}, peer="client")
        self.assertEqual(status, 200, body)

    async def test_read_forwards_only_empty_args_and_keeps_false_flags(self) -> None:
        answer = {
            "ok": 1,
            "gameplay": {"controls_suppressed": False, "engine_controls_disabled": True,
                         "hud_shown": False, "player_simulation_disabled": False},
            "hud": {"visible": False, "hud_hide": True, "quickbar_hide": False},
        }
        args, result = await self._forwarded("gameplay_state", {"timeout_s": 2.0}, answer)
        self.assertEqual(args, ("gameplay_state", {}, "client", 2.0))
        self.assertEqual(result, answer)
        self.assertEqual(result_prune.prune_unfilled_fields("gameplay_state", answer), answer)
        self.assertFalse(command_requires_lease("gameplay_state"))
        self.assertEqual(loopback.validate_command_args("gameplay_state", {}), (True, None))
        self.assertEqual(loopback.validate_command_args("gameplay_state", {"visible": True}), (False, "bad_args"))

    async def test_invalid_timeout_does_not_dispatch(self) -> None:
        for timeout in (-1.0, math.inf, math.nan, True, "2"):
            with self.subTest(timeout=timeout):
                await self._refused("gameplay_state", {"timeout_s": timeout})

    async def test_catalogs_expose_reader_before_lease_and_mutators_after(self) -> None:
        for progressive in (True, False):
            config = ServerConfig(mode="client", key="test", port=12345,
                                  progressive_disclosure=progressive, log_sink=lambda _: None)
            with patch("dayz_mcp.server.ClientRuntime", _FakeClientRuntime):
                app, runtime = build_app(config)
            before = {tool.name for tool in await app.list_tools()}
            self.assertIn("gameplay_state", before)
            self.assertEqual(NEW_TOOLS <= before, not progressive)
            runtime.active_lease_token = "catalog-lease"
            self.assertTrue(NEW_TOOLS <= {tool.name for tool in await app.list_tools()})
        for name in NEW_TOOLS:
            self.assertEqual(bridge_readiness._BRIDGE_COMMAND_TOOLS["client"][name], name)


class PublicCameraReleaseToolTest(_ToolCase):
    async def test_dispatch_preserves_matched_for_both_answers(self) -> None:
        for matched in (True, False):
            args, result = await self._forwarded(
                "camera_path_release", {"command_id": 42, "timeout_s": 2.0},
                {"ok": 1, "matched": matched},
            )
            self.assertEqual(args, ("camera_path_release", {"command_id": 42}, "client", 2.0))
            self.assertIs(result["matched"], matched)
        self.assertTrue(command_requires_lease("camera_path_release"))
        self.assertIn("camera_path_release", loopback._OWNER_CLEANUP_COMMANDS)

    async def test_invalid_id_or_timeout_never_reaches_bridge(self) -> None:
        for command_id in (0, -1, True, 1.0, "42", None):
            with self.subTest(command_id=command_id):
                await self._refused("camera_path_release", {"command_id": command_id})
        await self._refused("camera_path_release", {})
        await self._refused("camera_path_release", {"command_id": 42, "timeout_s": -1.0})


class PublicCameraFocusToolTest(_ToolCase):
    async def test_all_camera_modes_reuse_camera_set_and_echo_focus(self) -> None:
        poses = [POSE, {"cam_mode": "look_at", "cam_pos": [1.0, 2.0, 3.0], "look_at": [4.0, 5.0, 6.0]},
                 {"cam_mode": "free", "cam_pos": [1.0, 2.0, 3.0], "look_at": [4.0, 5.0, 6.0]},
                 {"cam_mode": "matrix", "cam_matrix": [1.0] * 12}]
        for pose in poses:
            with self.subTest(pose=pose):
                args, result = await self._forwarded(
                    "camera_set_focus", dict(pose, focus_distance=1e9, focus_blur=0.0,
                                             fov=1.0, settle_ticks=4, timeout_s=2.0), _camera_answer(),
                )
                expected = dict(pose, focus_distance=1e9, focus_blur=0.0, fov=1.0, settle_ticks=4)
                if expected["cam_mode"] == "look_at":
                    expected["cam_mode"] = "lookat"
                self.assertEqual(args, ("camera_set_focus", expected, "client", 2.0))
                self.assertEqual(loopback.validate_command_args(args[0], args[1]), (True, None))
                self.assertEqual(result["focus_applied"], {"distance": 1e9, "blur": 0.0})
        self.assertTrue(command_requires_lease("camera_set_focus"))

    async def test_bad_focus_and_camera_arguments_never_dispatch(self) -> None:
        good = dict(POSE, focus_distance=2.0, focus_blur=0.0)
        for changes in ({"focus_distance": 1e9 + 1}, {"focus_distance": 0.0},
                        {"focus_distance": 1e-46}, {"focus_distance": math.inf},
                        {"focus_distance": None}, {"focus_distance": True},
                        {"focus_blur": -1.0}, {"focus_blur": 1e9 + 1},
                        {"cam_mode": "bad"}, {"cam_pos": [1.0]}, {"fov": -1.0},
                        {"settle_ticks": 601}, {"timeout_s": -1.0}):
            with self.subTest(changes=changes):
                error = await self._refused("camera_set_focus", dict(good, **changes))
                if changes.get("focus_distance") == 1e9 + 1:
                    self.assertIn("bad_args", error)
        for missing in ("focus_distance", "focus_blur"):
            args = dict(good)
            del args[missing]
            await self._refused("camera_set_focus", args)


class ExecutedGameplayReadTest(unittest.TestCase):
    """Execute the Enforce reader on engine fakes exposing only getters.

    Independent bridge/engine controls and HUD flags catch accidental echoes.
    Missing setters fail any attempt to change game state during this read.
    """
    def _read(self, suppressed=False, disabled=True, shown=False, simulation=False,
              hud_hide=True, quickbar_hide=False, missing=""):
        panel = SimpleNamespace(IsVisible=lambda: shown)
        visibility = SimpleNamespace(IsContextFlagActive=lambda flag: {
            "HUD_HIDE": hud_hide, "QUICKBAR_HIDE": quickbar_hide}[flag])
        hud = SimpleNamespace(GetHudVisibility=lambda: None if missing == "visibility" else visibility,
                              GetHudPanelWidget=lambda: None if missing == "panel" else panel)
        mission = SimpleNamespace(GetHud=lambda: None if missing == "hud" else hud,
                                  IsControlDisabled=lambda: disabled)
        bridge = SimpleNamespace(m_ControlsSuppressed=suppressed, m_PlayerSimulationDisabled=simulation)
        before = dict(vars(bridge))
        namespace = {
            "GetGame": lambda: SimpleNamespace(GetMission=lambda: None if missing == "mission" else mission),
            "MissionGameplay": SimpleNamespace(Cast=lambda value: value),
            "IngameHud": SimpleNamespace(Cast=lambda value: value),
            "MCPGameplayState": SimpleNamespace, "MCPHudState": SimpleNamespace,
            "EHudContextFlags": SimpleNamespace(HUD_HIDE="HUD_HIDE", QUICKBAR_HIDE="QUICKBAR_HIDE"),
        }
        head, code = _function(BRIDGE.read_text(encoding="utf-8"),
                               "protected bool DispatchGameplayState(MCPCommand command, MCPResult result)",
                               this_type="MCPClientBridge")
        reader = translate(code, head, namespace, class_types={
            "MissionGameplay", "IngameHud", "IngameHudVisibility", "Widget", "MCPGameplayState", "MCPHudState"})
        result = SimpleNamespace()
        self.assertTrue(reader(bridge, SimpleNamespace(args=None), result))
        self.assertEqual(vars(bridge), before)
        return result

    def test_reads_engine_and_bridge_independently_without_mutation(self) -> None:
        for suppressed, disabled, shown, simulation, hud_hide, quickbar_hide in (
            (False, True, False, False, True, False),
            (True, False, True, True, False, True),
            (False, False, True, False, False, False),
        ):
            result = self._read(suppressed, disabled, shown, simulation, hud_hide, quickbar_hide)
            self.assertTrue(result.ok)
            self.assertEqual(vars(result.gameplay), {
                "controls_suppressed": suppressed, "engine_controls_disabled": disabled,
                "hud_shown": shown, "player_simulation_disabled": simulation})
            self.assertEqual(vars(result.hud), {"hud_hide": hud_hide, "quickbar_hide": quickbar_hide,
                                               "visible": not hud_hide and not quickbar_hide})

    def test_unavailable_engine_objects_fail_closed(self) -> None:
        for missing in ("mission", "hud", "visibility", "panel"):
            result = self._read(missing=missing)
            self.assertFalse(result.ok)
            self.assertEqual(result.error.data.decode(), "client_not_in_game" if missing == "mission" else "hud_unavailable")
            self.assertFalse(hasattr(result, "gameplay"))
