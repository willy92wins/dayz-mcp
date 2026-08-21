from __future__ import annotations

import hashlib
import unittest

from tests._addon_paths import addon_root


WORKSPACE_ROOT = addon_root()
BRIDGE = WORKSPACE_ROOT / "scripts" / "5_Mission" / "MCPBridge.c"
# Re-frozen 2026-08-07 against the PBO-B bridge (`0428691C3282B8D1`), which passed the
# Phase 3 in-game gate: the six new verbs compile (`Mission; 216x files;
# 490x classes`, zero errors) and respond — surface_query returned `cp_gravel`,
# vehicle_prepare_fixture reached wheel_count=4 on a vanilla CivilianSedan (the
# ExampleCar allowlist already gone), object_inspect reported `exists:false` with `ok:true` for
# an invented memory point, object_anim read phase, player_teleport with y==0 landed at
# y=4.80 on terrain, and inventory_give created the item. 13/14 rungs; the only red was
# world_spawn (spawn-readiness gate, a verb Phase 3 did not touch).
# The Enforce source is NOT under version control: this test is the only sentinel
# for unintended bridge changes. Re-freeze ONLY on an in-game gated state,
# never on a source-only edit.
# The gate has TWO halves and both are re-frozen together, against the same state:
#   BRIDGE_SHA256      -> the whole file.
#   BASE_BRIDGE_SHA256 -> the file with the four marker lines removed.
# The second forces a re-freeze of both when the bridge is touched; if only the
# first is updated, this one stays red and drags the whole pair into noise.
# Re-frozen 2026-08-16 against the bridge with the readiness fix: IsSpawnReady
# resolves by IDENTITY (found == job.subject) at SPAWN_READY_RADIUS 8.0, instead of by the
# REQUESTED position at 2.0. In-game gate the same day, server + client 1.29.163709 and both
# peers in version_state ok: world_spawn of a SeaChest requested at y=0 on terrain y=313.29
# returned ok:1 found:1 pos_real=[7500, 313.31, 7500] -- 313.31 m between what was asked and where
# it sat, against the 2.0 m the old radius looked for; and a CivilianSedan requested at y=0
# sat at y=4.69, also above that radius. Two of two, no timeout.
# The other half of the fix is NOT gated (NeutralizeDriveProbeControls on the failure and
# timeout paths of vehicle_drive): vehicle_drive is in the bridge SERVER_COMMANDS but NOT on the
# MCP tool surface, so it is only reachable by calling the daemon raw. A path the
# surface does not expose cannot regress through normal agent use either.
# Re-frozen 2026-08-16 (second time that day) against the bridge with the API-key fix: the key
# is no longer read only once. ReloadKeyAfterFailure re-reads dayz_mcp.json when the poll backoff
# reaches KEY_RELOAD_BACKOFF_S (4 s) and adopts the key ONLY if it changed, resetting backoff to 0; an
# unchanged key returns early, so a downed server does not retry hot. The trigger is persistent
# failure, not a classified error, because OnError delivers an ERestResultState and EREST_ERROR
# shares the value 5 with EREST_ERROR_CLIENTERROR (restapi.c:16-17): a 401 and a refused connection
# are indistinguishable from Enforce.
# In-game gate the same day of the NEW path, not merely that it compiles: a stale key was planted
# BEFORE starting the server, so the bridge was born with a dead credential and reproduced the
# exact symptom -- poll error=5 with backoff_s 4 -> 8 -> 16 -> 30 -> 30, stuck at the cap. After
# restoring the good key live: "poll key reloaded path=$mission:dayz_mcp.json keylen=43", and the
# poll actually resumed (bridge_status.server_peer.last_poll_age_s = 0.216), without restarting the
# mission. Mission compiled 216x files; 490x classes, zero errors.
# For anyone repeating the gate: editing dayz_mcp.json with the bridge ALREADY running does NOTHING -- the key
# lives in memory, polls stay OK and there is no failure to trigger the reload. The stale key has to be planted
# and the process restarted.
# Re-frozen 2026-08-17 against bridge v7 (batch 6): player_teleport / inventory_give without
# PluginDeveloper (only registers under DIAG_DEVELOPER, pluginmanager.c:65,:247-252 -> dead
# in release; VPP substitute pattern: SetPosition + vehicle edge via SetTransform,
# CreateInInventory / CreateInHands), optional uid on teleport/give/notify (FindHumanByUid via
# GetPlainId), entities_query verb (GetObjectsAtPosition3D, nearest-first + count_total),
# IsAllowedSpawnFlags admits ECE_CREATEPHYSICS. In-game gate the same day (run dd0a01cc, server +
# client 1.29.163709, PBO 9BEA4C6D1D67AA33 with this file byte-identical inside): teleport without uid
# at y=0 sat at y=294.73 and query_all_players confirmed it; teleport with uid ok and fake uid ->
# player_not_found; teleport with the player SEATED in a CivilianSedan moved car and player
# (entities_query found the sedan at the destination, in_vehicle stayed 1) -- pos_real came back OLD
# (previous position), mailbox finding confirmed, not yet fixed; inventory_give to inventory ok,
# to hands ok, second time to hands -> hands_occupied; notify_players by uid and broadcast visible
# in the client capture (SendNotificationToPlayerIdentityExtended notifies from server), fake uid
# -> player_not_found; entities_query count_total 38/19/18 sorted ascending and read-only
# with no lease; infected ZmbM_CitizenASkinny_Blue with flags 3108 (PLACE_ON_SURFACE|INITAI|
# CREATEPHYSICS) covered 12.7 m in ~16 s toward the player and hit them (health 0.695 -> 0.483) --
# pos_real of the spawn with explicit flags returned y=0 even though it sat at y=291.97; wait_for
# log_matches waited 56.6 s / 28 probes. Server script log with no bridge errors (the three
# ok=0 are the intended negatives). Mission compiled 216x files; 497x classes.
# DELIBERATELY RED from 2026-08-17 13:05: source-only pos_real fix
# -- DispatchPlayerTeleport reports the TRANSPORT position on the vehicle branch (the occupant's
# lags SetTransform by one frame) and ValidateSpawnArgs resolves y==0 to SurfaceY before
# CreateObjectEx (AI surface placement is deferred and readiness was reading it at y=0).
# Offline contracts: tests/test_player_teleport.py and tests/test_world_spawn_ground_contract.py.
# Re-freeze both halves after the combined gate with action_use (same PBO), not before.
# Re-frozen 2026-08-17 22:2x against the bridge with the pos_real fix, COMBINED gate with action_use
# (run 28f2e26f, server + client 1.29.163709, PBO BCA758A161B95058 with the 11 entries byte-identical
# to source, this file 0ED14AF5... inside): on-foot teleport y=0 -> pos_real y=294.73; world_spawn
# LFPG_BTCAtmAdmin flags=0 y=0 -> pos_real y=294.69 found:1; action_use LFPG_ActionOpenBTCAtm on
# LFPG_BTCAtmAdmin at 2.94 m -> started:1 and [BTCOpenResponse] + [BTCAtmView] Opened in the client log;
# ui_tree(BTCAtmRoot) 40 nodes; ui_set_text(EditBtcAmount) readable; ui_click(BtnBuyBtc) -> clicked:1
# handler=LFPG_BTCAtmView user_id=100 (Dabs branch LIVE) and [BTCTxResult] type=1 err=6 (stock=0);
# ui_set_text on TextWidget StatusText ok; CivilianSedan flags=0 y=0 -> pos_real y=294.68; SEATED
# teleport -> pos_real=[7150, 294.01, 7720] = NEW transport position (entities_query: sedan at 0.20 m,
# in_vehicle still 1) -- the mailbox finding is closed; telemetry_read of the
# sedan: declared_slots with 16 real names and none empty; infected flags 3108
# y=0 -> pos_real y=294.06 (no longer y=0). Script logs with no bridge errors (the two client ok=0 are
# intended negatives: ui_tree without path on a pre-created host -> no_menu, vehicle_telemetry without
# a client seat -> not_seated). Collateral finding, NOT the bridge: wait_for(log_matches) only looks at
# lines after its own call, so a response that lands before the first probe
# (BTCOpenResponse, BTCTxResult: ~200 ms after the fire) is missed and the verb times out with ok:true.
# Re-frozen 2026-08-21 against the bridge with `infected_drive` (DispatchInfectedDrive, dispatch
# :496 + handler :1229). The bridge changed on 20 Aug 00:35, SEVENTEEN minutes after 29a8e83
# closed the fencing gate and re-froze this file; that session's close annotated the reds
# as "by design until fencing closes", but fencing was already closed and published, and the note
# was born stale. The real reason for the reds was this change without a re-freeze.
# In-game gate of the verb itself, with authoritative position via entities_query
# (run 1a1cb6e1-7230-43c4-9164-41ab3b0be936): imposed heading 90 degrees -> 92.1 actual (error 2.1) and the
# infected covered 76.6 m FLEEING the player it was attacking at 0.95 m, which kills the null hypothesis;
# heading 270 -> 270.1 actual, 41.7 m in 46 s with 0.02 m of lateral drift, which kills coincidence;
# mode="release" returns control to vanilla AI (turns on its own to 171.7, drops to 0.25 m/s). The tool is
# live on the surface (server.py:2731). NOT calibrated: `speed` is not m/s (speed=3 measured 0.91 m/s).
# Why it is re-frozen now: both sentinels and MCPBridge.c itself sit INSIDE the publication
# boundary (measured on `tools/publish/included.json`), so a deliberate red in the private
# tree publishes as a suite that fails on clone -- which is exactly the credential this
# project cannot afford to lose.
BRIDGE_SHA256 = "ADD51C9BFBFEA3F2350F09BDC5609FB4AE04DA8E02B8EF290D4F9724274EB5FA"
BASE_BRIDGE_SHA256 = "7CC833DE5FAB15B57B09851406F7075B1ACD637E1A299F876F4CE800AE57F201"

MARKERS = (
    'Log("spawn phase id=" + command.id + " phase=validate_begin");',
    'Log("spawn phase id=" + command.id + " phase=validate_return");',
    'Log("spawn phase id=" + command.id + " phase=create_begin");',
    'Log("spawn phase id=" + command.id + " phase=create_return");',
)

WORLD_SPAWN_DEFERRAL = """\
\t\tbool deferFromWorldSpawn = false;
\t\tint i = 0;
\t\twhile (i < count)
\t\t{
\t\t\tMCPCommand command = batch.commands.Get(i);
\t\t\tif (command && command.cmd == \"world_spawn\")
\t\t\t{
\t\t\t\tdeferFromWorldSpawn = true;
\t\t\t\tLog(\"world_spawn deferred id=\" + command.id + \" from=poll_callback callback_tick=\" + m_TickPollCallback);
\t\t\t}

\t\t\tif (deferFromWorldSpawn)
\t\t\t{
\t\t\t\tQueuePendingOrFail(command);
\t\t\t}
\t\t\telse if (i < MAX_DISPATCH_PER_TICK)
\t\t\t{
\t\t\t\tDispatch(command);
\t\t\t}
\t\t\telse
\t\t\t{
\t\t\t\tQueuePendingOrFail(command);
\t\t\t}
\t\t\ti = i + 1;
\t\t}
"""

NO_PATHGRAPH_FLAG_ALLOWLIST = """\
\t\tint noPathgraphFlags = ECE_CREATEPHYSICS | ECE_TRACE;
\t\tif (flags == noPathgraphFlags)
\t\t{
\t\t\treturn true;
\t\t}
"""


class Task9SpawnPhaseMarkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge_bytes = BRIDGE.read_bytes()
        cls.bridge_text = cls.bridge_bytes.decode("utf-8")
        start = cls.bridge_text.index("protected bool DispatchWorldSpawn")
        end = cls.bridge_text.index("protected bool DispatchObjectDelete", start)
        cls.spawn_block = cls.bridge_text[start:end]
        poll_start = cls.bridge_text.index("void OnPollSuccess")
        poll_end = cls.bridge_text.index("void OnPollError", poll_start)
        cls.poll_block = cls.bridge_text[poll_start:poll_end]
        tick_start = cls.bridge_text.index("void OnTick")
        tick_end = cls.bridge_text.index("protected void TryInit", tick_start)
        cls.tick_block = cls.bridge_text[tick_start:tick_end]
        flags_start = cls.bridge_text.index("protected bool IsAllowedSpawnFlags")
        flags_end = cls.bridge_text.index("protected Human GetFirstHuman", flags_start)
        cls.flags_block = cls.bridge_text[flags_start:flags_end]

    def test_full_source_hash_is_frozen(self) -> None:
        self.assertEqual(hashlib.sha256(self.bridge_bytes).hexdigest().upper(), BRIDGE_SHA256)

    def test_world_spawn_and_following_batch_commands_are_deferred_fifo(self) -> None:
        self.assertEqual(self.bridge_text.count(WORLD_SPAWN_DEFERRAL), 1)
        self.assertIn(WORLD_SPAWN_DEFERRAL, self.poll_block)
        self.assertEqual(self.poll_block.count("bool deferFromWorldSpawn = false;"), 1)
        self.assertEqual(self.poll_block.count("deferFromWorldSpawn = true;"), 1)
        self.assertEqual(self.poll_block.count("if (deferFromWorldSpawn)"), 1)

    def test_on_tick_drains_deferred_spawn_before_starting_another_poll(self) -> None:
        self.assertLess(self.tick_block.index("DrainPending();"), self.tick_block.index("StartPoll();"))

    def test_task9_no_pathgraph_flags_are_exactly_allowlisted(self) -> None:
        self.assertEqual(self.bridge_text.count(NO_PATHGRAPH_FLAG_ALLOWLIST), 1)
        self.assertIn(NO_PATHGRAPH_FLAG_ALLOWLIST, self.flags_block)
        self.assertLess(
            self.flags_block.index(NO_PATHGRAPH_FLAG_ALLOWLIST),
            self.flags_block.index("(flags & ECE_PLACE_ON_SURFACE)"),
        )
        self.assertNotIn("flags & noPathgraphFlags", self.flags_block)

    def test_markers_are_unique_and_strictly_ordered(self) -> None:
        positions: list[int] = []
        for marker in MARKERS:
            with self.subTest(marker=marker):
                self.assertEqual(self.bridge_text.count(marker), 1)
                positions.append(self.spawn_block.index(marker))
        self.assertEqual(positions, sorted(positions))

    def test_markers_bracket_only_the_two_synchronous_calls(self) -> None:
        validate_begin = self.spawn_block.index(MARKERS[0])
        validate_call = self.spawn_block.index("ValidateSpawnArgs(command.args)")
        validate_return = self.spawn_block.index(MARKERS[1])
        create_begin = self.spawn_block.index(MARKERS[2])
        create_call = self.spawn_block.index("GetGame().CreateObjectEx(")
        create_return = self.spawn_block.index(MARKERS[3])
        queued = self.spawn_block.index('Log("job queued id="')

        self.assertLess(validate_begin, validate_call)
        self.assertLess(validate_call, validate_return)
        self.assertLess(validate_return, create_begin)
        self.assertLess(create_begin, create_call)
        self.assertLess(create_call, create_return)
        self.assertLess(create_return, queued)

    def test_removing_only_marker_lines_restores_frozen_source_hash(self) -> None:
        stripped = self.bridge_bytes
        for marker in MARKERS:
            marker_line = ("\t\t" + marker + "\n").encode("utf-8")
            with self.subTest(marker=marker):
                self.assertEqual(stripped.count(marker_line), 1)
                stripped = stripped.replace(marker_line, b"", 1)

        self.assertEqual(hashlib.sha256(stripped).hexdigest().upper(), BASE_BRIDGE_SHA256)


if __name__ == "__main__":
    unittest.main()
