"""G1b follow-ups (run 1792c310, 28-nov-2026): the four findings the Auditor listed after #467/#476/#444.

1. vehicle_enter on a boat timed out and left the server reading the player in the
   boat while the client stayed on foot. vehicle_enter seats the server's player only
   (it never runs the client get-in), so that split is the tool's design; what was
   missing is the text that names it and the bridge log that tells the two causes of a
   seat timeout apart (a far player, a get-in that never ends).
2. 890 VM exceptions at vanilla actioninput.c:895 with the player at the boat's wheel.
   Not the addon's seat: BoatScript.SetActions registers no ActionSwitchLights, so the
   vanilla ToggleVehicleLightsActionInput reads a null array for any seated boat.
3. Leaked MCPClientPollCallback / MCPClientResultCallback (1x) at a full game exit: the
   one cached poll callback and the one pooled result callback, which native RestApi
   keeps until the module is cleaned (the server, with no static holder, shows the same
   1x/1x). Documented, no code change.
4. vehicle_prepare_fixture answered bad_args for {type, pos}: the tool's default radius
   was 100 m and the bridge refuses an object_at radius above 50 m.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from dayz_mcp import bridge_errors, loopback, server
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import method_body

BRIDGE = (addon_root() / "scripts" / "5_Mission" / "MCPBridge.c").read_text(encoding="utf-8")
REPO = Path(__file__).resolve().parents[2]
VEHICLE_DOC = (REPO / "docs" / "VEHICLE_TESTING.md").read_text(encoding="utf-8")
CHANGELOG = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")


def _squashed(text: str) -> str:
    return " ".join(text.split())


def _bridge_object_at_max_radius() -> float:
    match = re.search(r"protected const float TELEMETRY_OBJECT_AT_MAX_RADIUS = ([0-9.]+);", BRIDGE)
    if match is None:
        raise AssertionError("TELEMETRY_OBJECT_AT_MAX_RADIUS not found in MCPBridge.c")
    return float(match.group(1))


class PrepareFixtureRadiusTest(unittest.IsolatedAsyncioTestCase):
    """Finding 4: the default radius must be one the bridge accepts."""

    def _app(self):
        return server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def test_tool_without_radius_sends_a_radius_the_bridge_accepts(self) -> None:
        app, runtime = self._app()
        with patch.object(
            runtime,
            "call_bridge",
            new=AsyncMock(return_value={"ok": 1, "vehicle_fixture_ready": True}),
        ) as call:
            await app.call_tool(
                "vehicle_prepare_fixture",
                {"type": "CivilianSedan", "pos": [13062.0, 7.0, 8340.0], "timeout_s": 1.0},
            )
        sent = call.await_args.args[1]
        self.assertGreater(sent["radius"], 0.0)
        self.assertLessEqual(sent["radius"], _bridge_object_at_max_radius())
        # and it still goes through the ingress the daemon applies to the same args
        state = loopback.ServerState("test-key")
        from tests.fence_helpers import bind_both_peers

        bind_both_peers(state)
        status, _body = state.enqueue_command("vehicle_prepare_fixture", dict(sent))
        self.assertEqual(status, 200)

    async def test_radius_above_the_bridge_maximum_is_refused_before_the_bridge(self) -> None:
        app, runtime = self._app()
        too_far = _bridge_object_at_max_radius() + 0.5
        with patch.object(runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})) as call:
            with self.assertRaises(Exception) as caught:
                await app.call_tool(
                    "vehicle_prepare_fixture",
                    {
                        "type": "CivilianSedan",
                        "pos": [13062.0, 7.0, 8340.0],
                        "radius": too_far,
                        "timeout_s": 1.0,
                    },
                )
        call.assert_not_awaited()
        self.assertIn("radius", str(caught.exception))
        self.assertIn("50", str(caught.exception))

    async def test_description_names_the_radius_range(self) -> None:
        app, _runtime = self._app()
        tool = {t.name: t for t in await app.list_tools()}["vehicle_prepare_fixture"]
        text = _squashed(tool.description or "")
        self.assertIn("radius", text)
        self.assertIn("(0, 50]", text)

    def test_ingress_refuses_a_radius_the_bridge_would_refuse(self) -> None:
        state = loopback.ServerState("test-key")
        from tests.fence_helpers import bind_both_peers

        bind_both_peers(state)
        limit = _bridge_object_at_max_radius()
        base = {"mode": "object_at", "type": "CivilianSedan", "pos": [1.0, 0.0, 1.0]}
        for command in ("vehicle_prepare_fixture", "telemetry_read"):
            with self.subTest(command=command, radius=limit):
                status, _ = state.enqueue_command(command, {**base, "radius": limit})
                self.assertEqual(status, 200)
            with self.subTest(command=command, radius=limit + 0.5):
                status, body = state.enqueue_command(command, {**base, "radius": limit + 0.5})
                self.assertEqual(status, 400, body)


class VehicleEnterTimeoutTextTest(unittest.TestCase):
    """Finding 1: the timeout names the half state instead of a bare code."""

    def test_timeout_names_the_server_only_seat_and_the_way_to_read_it(self) -> None:
        message = str(bridge_errors._bridge_error({"ok": 0, "error": "timeout"}, "vehicle_enter"))
        self.assertTrue(message.startswith("timeout; "), message)
        self.assertIn("query_all_players", message)
        self.assertIn("in_vehicle", message)
        self.assertIn("client", message)
        self.assertIn("vehicle_get_in_client", message)

    def test_other_verbs_keep_a_bare_timeout(self) -> None:
        for cmd in ("world_spawn", "scene_clear", "vehicle_prepare_fixture", None):
            with self.subTest(cmd=cmd):
                self.assertEqual(
                    str(bridge_errors._bridge_error({"ok": 0, "error": "timeout"}, cmd)),
                    "timeout",
                )

    def test_vehicle_enter_refusals_stay_bare(self) -> None:
        for code in ("seat_occupied", "already_in_vehicle", "no_vehicle", "seat_failed"):
            with self.subTest(code=code):
                self.assertEqual(
                    str(bridge_errors._bridge_error({"ok": 0, "error": code}, "vehicle_enter")),
                    code,
                )


class SeatJobDiagnosticsSourceTest(unittest.TestCase):
    """Finding 1: the server log separates a far player from a get-in that never ends."""

    def test_dispatch_logs_the_distance_the_player_must_be_moved(self) -> None:
        body = _squashed(
            method_body(BRIDGE, "protected bool DispatchVehicleEnter(MCPCommand command, MCPResult result)")
        )
        self.assertRegex(body, r'Log\("seat dispatch id=" \+ command\.id')
        self.assertIn("dist_m=", body)
        self.assertIn("vector.Distance(human.GetPosition(), vehicle.GetPosition())", body)

    def test_a_seat_timeout_logs_what_the_server_reads(self) -> None:
        timeout = _squashed(method_body(BRIDGE, "protected void PostJobTimeout(MCPJob job)"))
        self.assertIn('if (job.kind == "seat")', timeout)
        self.assertIn("LogSeatTimeout(job);", timeout)
        body = _squashed(method_body(BRIDGE, "protected void LogSeatTimeout(MCPJob job)"))
        for token in (
            '"seat timeout id="',
            "GetCommand_Vehicle()",
            "transport_match=",
            "getting_in=",
            "IsGettingIn()",
            "seat=",
            "GetVehicleSeat()",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)

    def test_the_diagnostics_do_not_change_the_job_outcome(self) -> None:
        body = method_body(BRIDGE, "protected void LogSeatTimeout(MCPJob job)")
        for forbidden in ("PostResult", "StartCommand", "m_Jobs", "result.", "SetPosition"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, body)


class ClientShutdownHolderLogTest(unittest.TestCase):
    """Finding 3: the exit log tells native retention from a request in flight."""

    def test_shutdown_logs_the_holder_count_after_the_terminal_posts(self) -> None:
        client = (addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c").read_text(
            encoding="utf-8"
        )
        body = _squashed(method_body(client, "void Shutdown()"))
        self.assertIn('Log("shutdown rest holder in_flight=" + MCPClientRestHolder.Count());', body)
        # after the terminal POSTs and the teardown, so it counts what stays held
        self.assertGreater(
            body.index("in_flight="), body.index("m_RestSink.Disconnect();")
        )
        self.assertGreater(body.index("in_flight="), body.index("m_Configured = false;"))

    def test_the_holder_itself_is_unchanged(self) -> None:
        holder = (addon_root() / "scripts" / "4_World" / "MCP_ClientRestCallbacks.c").read_text(
            encoding="utf-8"
        )
        self.assertIn("static int Count()", holder)
        self.assertNotIn("Clear()", method_body(holder, "static void Drop(RestCallback cb)"))


class DocsTest(unittest.TestCase):
    def test_vehicle_doc_corrects_the_467_claim_and_names_the_vanilla_cause(self) -> None:
        text = _squashed(VEHICLE_DOC)
        self.assertIn("actioninput.c:895", text)
        self.assertIn("BoatScript", text)
        self.assertIn("ActionSwitchLights", text)
        self.assertIn("No `SCRIPT (E)` line", text)
        self.assertIn("does not reproduce", text)

    def test_vehicle_doc_says_vehicle_enter_never_seats_the_client(self) -> None:
        text = _squashed(VEHICLE_DOC)
        self.assertIn("`vehicle_enter` seats the server's player only", text)
        self.assertIn("run 1792c310", text)

    def test_changelog_documents_the_exit_leak_as_native_retention(self) -> None:
        text = _squashed(CHANGELOG)
        self.assertIn("Leaked 'MCPClientPollCallback' script instance (1x)", text)
        self.assertIn("natively retained", text)
        self.assertIn("1792c310", text)


if __name__ == "__main__":
    unittest.main()
