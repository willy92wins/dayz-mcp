# v11 promotion, step 7 (in-game), 2026-10-07

Evidence: one file per call in this directory (name = checklist step). Runs: default e400ceae (SimpleGroup + probe),
default b0c3a430 (DayZ_MCP + @SecretRock_RH + probe), 130 9bfd261b (DayZ_MCP on DayZ 1.30 Exp). Leases held by this
session's stdio harnesses; every run closed through dayz_test_close. 15 files carry the owner's Steam uid
(owner_identity): redact before any of them goes into a PR.

## Default instance (1.29)

| Check | Verdict | Evidence |
|---|---|---|
| bridge_status "11" + capabilities (object_resolve, player_kill, bot_start/stop, action_use_component) | PASS | 7.03, 8.03 (`11~1.29.163709`) |
| E object_resolve: 0/1/2 objects, small radius, repeat -> same id, bad_args (radius 0, 60), object_not_found, ambiguous_object | PASS | 7.10-7.17 |
| E object_delete only that object (A id 8 deleted, B id 9 still resolves) | PASS | 7.18, 7.19 |
| E telemetry_read lifetime vs native read: Apple 600/600 in both world_spawn read-back and telemetry_read | PASS | 7.20, 7.21 (sedan without lifetime_s: -1 / 3.0, the raw engine reading) |
| E inventory_give -> hands_take -> weapon_state: Apple id 27 to inventory, hands_take accepted, weapon_state object_id 27 | PASS | 7.27-7.29b (FenceKit given to "inventory" landed in hands: hands_take answered already_in_hands, 7.24) |
| E vehicle_telemetry.direction vs vehicle_trace while turning: 8 reads, each equal to a trace sample (same frame, position gap 0.000 m, angle 0.000 deg); 525/525 trace samples agree with their own yaw/pitch | PASS | 7.44-7.51; not tautological: telemetry reads `transport.GetDirection()` (MCPClientBridge.c:2391), the trace `car.GetDirection()` (MCP_CarScript.c:565) |
| 6d21 door above 511: RocaHeli window 1 = door_index 5 -> started, component_index 1095, object_doors door 5 open (door 6 unchanged) | PASS | 8.10-8.18 (player inside the attic; SecretRock's InsideOfWindow gate) |
| fde3 action_use_component: plain call condition_failed (the bug), component_index 99999 component_not_found, component_index 0 + cursor_pos at lift_call_2_action started; effect: landing door 2 closed, door 4 opened after travel | PASS | 8.20-8.26 |
| 06a6 camera_unverified after lease expiry: capture without lease -> camera_unverified_reason no_lease + warning; with lease -> neither | PASS | 7.31, 7.34 |
| c8c7 rotation report reaches the caller: storage_rotated true, storage_backup, storage_reset_notice mission_world_and_character_reset (both launches) | PASS | 7.01, 8.01 |
| 9941 probe trials | INCONCLUSIVE, no trial armed | 9941.* (markers removed, no jsonl written); see findings 1-3 |
| unsuccessful-scan duration (6d21 spec) | NOT MEASURED | no door on RocaHeli lacks a component in 0..2047 |

## Instance 130 (1.30 Exp)

| Check | Verdict | Evidence |
|---|---|---|
| bridge "11" accepted, capabilities announced | PASS | g5.24 (`11~1.30.164014.27`) |
| 120f bot verbs on a world_spawn dummy: start started=1, second start bot_busy, stop stopped=1, restart after stop, unknown id object_not_found | PASS | g5.27-g5.33 |
| bot_unavailable on 1.29 | NOT RUN | offline tests only |

## g5 step 7 (two daemons, one box)

| Check | Verdict | Evidence |
|---|---|---|
| both daemons up together, separate trees, ports, keyfiles, state roots, registrations | PASS | listeners 8765 (LIVE venv) and 8775 (T130 venv, --instance 130); session_status generations differ |
| a run on one instance blocks a launch on the other: same port -> active_run_exists (port_in_use_foreign); other port 2312 -> active_run_exists (foreign occupant) | PASS | g5.03, g5.04 |
| second writer per root refused: exit 75 + "DAEMON: state root already has a writer", original listeners unchanged | PASS | g5.14, g5.15 |
| stores: default 143 backup runs all present (+2 today); 130's 3 backup runs in post-step-1 runs.json.bak-preprune* (EXITED pruned on activation); receipts kind v1, 130 source_absent=true reused | PASS | store comparison in this session |
| first 130 launch | FAIL then PASS after operator fix | g5.20 instance_config_missing; created empty `C:\temp\DayZ_MCP_130_dev\_server\profiles-130` and `_client\profiles-130` (steps.log), g5.21 succeeded |
| 130 close | DEGRADED REPORT | g5.40 graceful=false role_without_rpt although the profiles-130 RPTs end with "Termination successfully completed" |

## Findings for the inbox

1. action_use answers setup_failed for instant local actions (ActionTogglePlaceObject: IsInstant+IsLocal, Start and
   OnActionEnd inside ActionStart, actionmanagerclient.c:684-688), so GetRunningAction() is null although the action ran.
2. 9941 PROCEDURE.md names FenceKit with ActionPlaceObject; kits use ActionDeployObject (kitbase.c:150-151, 5 s),
   ActionPlaceObject belongs to Container_Base and others (container_base.c:52-53). With a WoodenCrate no placement
   hologram was observed after three toggles (with and without camera_set aimed at the ground); ActionPlaceObject stayed
   condition_failed. H1 untested.
3. Named instance profiles: runs use `profiles-<token>` (loopback.py:1817-1819); the first launch fails
   instance_config_missing until the operator creates both directories (seeding only into an existing one,
   loopback.py:1789); README does not say so; artifacts_paths and the close's RPT lookup use the legacy `profiles`
   folder (role_without_rpt, graceful=false, exit_metrics_valid=false on a clean termination).
4. Observation (no defect): vehicle_get_in_client owner-only physics leaves the server copy where it was (documented).
