# DayZ-MCP Vehicle Testing Protocol

Vehicle tests are the one tool family where the *test site* is part of the
contract. On 2026-08-24 an A-B-B-A differential proved that drivability is
positional: the same `CivilianSedan`, config, and build froze at
`[6063, 1931]` (2-second displacement 0.114-0.118 m at full throttle) and
drove 91 m twice at `[6063, 1971]`, 40 m away. Two months of G0 "drivability
failures" were a bad test site. Every drive test therefore runs at a
**certified site**, and a failing drive at an uncertified site is evidence
about the site, not about the tools.

## The exit reality: teardown is by fixture delete, not by the get-out action

`ActionGetOutTransport.ActionCondition` requires
`crewIndex >= 0 && CrewCanGetThrough && IsAreaAtDoorFree`
(`actiongetouttransport.c:68-77`), and for `CivilianSedan`,
`CrewCanGetThrough` is false while the driver door is CLOSED
(`civiliansedan.c:214-222`). On this stand that condition is
**site-independently unsatisfiable** (measured at 7 pristine sites plus the
two historical ones, fb-20260824-133301-ecf5): the fixture spawns with the
door closed, and the injected `action_use ActionOpenCarDoors` starts on the
client but its server half (`OnStartServer` → `SetAnimationPhase`,
`actioncardoors.c:92`) never lands — the server's replica of the
client-authoritative car never even moves from the spawn point. The sanctioned
scenario teardown is therefore `object_delete` of the fixture with the
ejection verified via telemetry (`not_seated`; 13/13 clean across two
sessions), which `finish_site` reports as `OK_FORCED_DELETE`.

Surface limits that shape site choice:

- `vehicle_control` has **no reverse** — throttle is clamped to `0..1`
  ([`tools/dayz_mcp/server.py:3695`](../tools/dayz_mcp/server.py)); a car
  stopped against an obstacle cannot be recovered by driving.
- `world_spawn`'s `rotation` argument is **`RF_*` flags, not a heading**
  (`MCPBridge.c:553`); spawn heading is inherited from the terrain and there
  is no orientation setter in the tool surface.
- `scene_raycast` with `intersect="view"` does **not** detect the statics
  that stop a car (measured 8/8 false CLEAR over a line with a proven
  obstacle cluster). Corridor checks must use `entities_query` **with the
  player teleported into the area first** (fb-20260824-123204-638e).
- `object_anim` / `object_inspect` resolve by position on the **server**,
  which still has a driven fixture at its spawn point — target them there,
  or not at all (fb-20260824-133301-ecf5).

## Site certification bar

Candidate starts come from a two-phase run of `tools/g0_site_gate.py`.
Phase one is a **surface scan** (no lease, no player proximity —
`surface_query` answers truthfully anywhere): a 50 m grid over the two
Chernarus airfield rectangles, cached in a versioned sidecar that is only
reused when it is a complete, error-free scan of exactly the same grids.
Candidates are pavement cells (`asphalt`/`concrete`/`tarmac`/`runway`
surface types, `y >= 1 m`) ranked by consecutive pavement to the north,
with the frozen canonical site re-certified first when the current scan
still shows pavement near it. Phase two certifies each finalist under a
session lease when all of:

| Stage | Requirement |
|---|---|
| CANOPY | The `place_safely` canopy gate at the player point **and** the car point, both before the teleport: `scene_raycast` (`geom`, from y+30 to y-5) must land within 0.05 m of `surface_query` y. A raw `[x, 0, z]` teleport buried the player inside a building on 2026-08-24 (fb-20260824-115220-1bc1); the same gate also rejects water (the ray stops on the surface plane above the seabed) and tree canopies |
| PRECHECK | `player_teleport` to the surface y lands within 30 m; the corridor band (|dx| <= 25 m, -10..+160 m along +Z) is enumerated by three r=65 spheres at dz 20/85/150, each queried at its local surface y. Fail-closed: a sphere whose surface probe or entity query fails, whose reply is not `ok`, whose `count_total` is missing, or whose truncation (`count_total > rows`) is not proven irrelevant by the farthest returned row (>= 45 m, nearest-first ordering) makes the corridor **unverified**. Zero blocking entities in the band. Passable by measured exception: `BushSoft_*` (a sedan drives through soft bushes) and config-less ground props (`GetType()` empty with engine `classname` `Object` — 83-88 of ~85 in-band rows on the certified runway, at terrain height; everything real carries a config type). Any other entity, `BushHard_*` included, blocks |
| DRIVE | `delta_2s_xz > 1.0 m` (the product-spec G0 contract) and `delta_5s_xz >= 10 m` at throttle 1.0, with the 20 Hz trace integrity gates green (owner stable, net id stable, sample cadence) |
| EXIT | `finish_site` ends `"OK"` or `"OK_FORCED_DELETE"`: the scenario tears down verifiably — either the get-out action succeeded, or the fixture delete ejected the player and telemetry confirmed `not_seated`. The get-out action itself is site-independently blocked on this stand (see "The exit reality" above) and does not discriminate sites. A `CLEANUP_DEGRADED` finish aborts the remaining candidates: their evidence would run on contaminated state |

A certified row also requires the exact bridge PBO SHA-256 (the gate
refuses to run without one) and a confirmed session release — an
unconfirmed release keeps the row in the JSON but exits non-zero.

The corridor is checked along +Z because default spawn heading on the
certified stand points roughly north (measured drive direction
`[0.005, 1.0]`). The box is sized from measurement, not wishes: the full
drive envelope (5 s traced full throttle plus braking) is ~91 m, so 160 m
forward keeps a >=150 m bar with margin, and the 25 m half-width absorbs
the ~8-12 degree terrain-inherited heading spread.

## Certified canonical site

| Label | Site `[x, y, z]` | Certified | Build (PBO SHA-256, first 12) | Evidence |
|---|---|---|---|---|
| NWAF_x4200_z10650 | `[4200.0, 0.0, 10650.0]` | 2026-08-24 | 28226C93B9B8 | Certification verdict + 20 Hz trace sidecar, produced by this reviewed gate: canopy dy 0.0 at both points, corridor fully enumerated with zero truncation (`count_total` 105/83/62 = rows) and zero blockers, `delta_2s_xz` 3.216 m, `delta_5s_xz` 22.68 m, 65.2 m span, heading `[0.003, 1.0]`, trace integrity gates PASS, teardown verified, session release confirmed |

Measured alternates on the same NWAF concrete (drive PASS in every session,
delta_2s_xz 3.1-3.7 m across 6 sessions):

- `[4300.0, 0.0, 10500.0]` — certified by the pre-review gate; on
  re-certification the player-point canopy ray hit +1.67 m, so the
  hardened gate demoted it (fail-closed beats a remembered green).
- `[4350.0, 0.0, 10400.0]` — intermittently trips the trace cadence gate
  at its `>= 20.0` Hz boundary (sampler jitter 19.996-20.011 Hz), not
  signal loss.
- `[4600.0, 0.0, 9950.0]` — one `BushHard` at 133 m in the corridor;
  passable under the old tolerance, blocking under the current rule.

Re-certify when any of these change: map, vehicle type, game build
(compatibility drift), or the mod's spawn/control path. Run the gate with:

[EXACT]

```powershell
python tools\g0_site_gate.py --pbo-sha256 <sha> --out _site_protocol_verdict.json
```

The game must already be running with the bridge ready (the gate drives the
broker daemon over raw authenticated HTTP and never launches DayZ itself).

## Standard test ladder (one vehicle scenario)

1. Run the canopy gate at the player point **and** at the car point, then
   `player_teleport` the driver to the surface y (mutation — hold a session
   lease). Never teleport to unverified coordinates: the vertical column,
   not just the ground, must be clear (fb-20260824-115220-1bc1).
2. `world_spawn` the vehicle at the site (`flags: 0`; y=0 grounds it).
3. `vehicle_prepare_fixture` (`mode: object_at`) at the real spawn position.
4. `restore_gameplay` + `vehicle_release` (clean slate), then
   `vehicle_get_in_client` and assert: `seated`, `seat == "driver"`,
   `vehicle_fixture_ready`, `is_owner` true, `is_authority_owner` false,
   `net_strategy == 2`, non-empty `owner_identity`, integer net id pair.
5. `engine_set start`; assert `engine_on_server` via `vehicle_telemetry`.
6. `vehicle_trace start` (20 Hz, 256 samples), `vehicle_control`
   throttle 1.0 with `hold_ttl_s`, page the trace live, stop the trace.
7. Judge `delta_2s_xz` / `delta_5s_xz` from S0/S2/S5 plus the trace
   integrity gates. Ownership evidence comes from the same samples.
8. Brake to a stop (`brake 1.0`, `handbrake 1.0`), `engine_set stop`.
9. Attempt `action_use ActionGetOutTransport`; expect `condition_failed`
   on this stand (see "The exit reality"). Tear down with `vehicle_release`
   and `object_delete` of the fixture; verify the ejection via telemetry
   (`not_seated`), then `restore_gameplay`.
10. Release the session lease.

Steps 2-9 are exactly `prepare_site` / `run_cell` / `finish_site` in
[`tools/g0_abba_gate.py`](../tools/g0_abba_gate.py); drive new tests through
that library instead of re-implementing the ladder. The threshold judgement
of step 7 (`delta_2s_xz` / `delta_5s_xz` against the contract) lives in the
site gate's `drive_metrics`, not in the library.

## Session shape

Run the whole scenario inside **one process lifetime** (managed run start,
waits, gate, teardown): daemons spawned under a harness command die with its
process tree, and an externally started daemon is not adoptable by new
clients. Reference runner: `g_protocol_run.py` (lanes archive 2026-08-24).
## Multi-agent certification (2026-08-24, bridge v9)

The canonical site's drivability was re-confirmed on the v9 bridge (PBO
`91A542E17CA91B76DFEE5AB7EE40B5C8FDAB38EA3BEAE7A4AA37A87301DE9127`) by three
independent external agents, each with a minimal brief (this document's ladder,
no other context), its own MCP client and its own session lease, sequentially
against one live game:

| agent | harness | seated | drive XZ | teardown | verdict |
|---|---|---|---|---|---|
| Grok 4.6 | grok CLI, zero native tools, MCP only | yes | 163.2 m | deleted + released | PASS |
| GPT-5.6 | codex exec | yes | 100.6 m | deleted + released | PASS |
| Ox Alpha (free) | opencode, external skills disabled | yes | 132.5 m | deleted + released | PASS |

Every lane drove `session_acquire_wait -> world_spawn -> vehicle_prepare_fixture
-> vehicle_get_in_client -> engine_set -> vehicle_control -> vehicle_telemetry
-> object_anim(object_id) -> object_delete -> session_release` and emitted its
own verdict JSON. Evidence: `ma_cert_report.json` + per-lane transcripts in the
session archive.

Instrument findings the shakedown surfaced (both fixed in this tree):

- **Clearance self-collision**: the vertical clearance probe (teleport gate and
  site-gate canopy) hit the survivor already standing in the probed column
  (dy 1.671 m = player height). That signature also explains the round-13
  "canopy +1.67 m" demotion of x4300. Both probes now pass `ignore: "player"`
  -- players are not cover.
- **Cadence boundary flake**: the 20 Hz trace sampler delivers 19.996-20.011
  effective Hz; the strict `>= 20.0` gate flaked on jitter. `MIN_EFFECTIVE_HZ`
  now carries a 0.5% allowance (19.9).

Known caveat kept honest: an `object_anim` write applies (the door visibly
moves), but the phase re-read in the same tick can lag the write -- a value
read one command later reflects the transition (measured 0.0 -> 0.599 -> 1.0
trajectory). Confirm writes with a follow-up read, not with the write reply
alone. The site-gate certificate for a release build is produced against that
build's PBO hash at release time.

