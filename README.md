# DayZ-MCP

**An MCP server that puts an agent's hands on a running DayZ: build a mod, launch the
game, put the world into a state, act, and read back what the engine did — 86 typed
tools, server-authoritative, no keyboard, no OCR.**

Two things fall out of that, and both are new for this game:

- **The autonomous mod-development loop closes.** Editing Enforce Script, config.cpp
  and models is file work; packing a PBO is a command. What could not be automated was
  the part that decides whether the change actually works — getting the game up with
  the mod loaded, putting a player, a vehicle or an object into the exact situation the
  change is about, and reading the result. That step was a human at the client.
  DayZ-MCP makes it tool calls: an agent can now change, build, run, measure and fix a
  mod on its own.
- **An agent can run a server.** The same verbs that set up a test scene are the ones
  an event director or an admin needs: see every player, teleport one, spawn or remove
  something, change time and weather, message everyone, watch the log, wait for a
  condition — all through `MissionServer`, all serialized behind one daemon with leases
  and an audit trail. Point an agent at a server and it can *operate* it, not just
  query it.

The verbs are also useful one at a time — spawn a car and read its telemetry, grab a
frame, raycast a placement, record a 20 Hz drive trace as a regression fixture
(`tools/dayz_mcp/vehicle_trace.py:17`).

## The development loop, as tools

| Step | Tool(s) | What it does |
|---|---|---|
| **Build + launch** | `dayz_test_run(project, mode, build=True, …)` | Packs the mod with AddonBuilder, starts a diag server and/or client with it loaded, waits for readiness. Managed run, returns a `run_id`. |
| **Set up the scene** | `world_spawn`, `player_teleport`, `player_heal`, `player_godmode`, `vehicle_enter`, `inventory_attach`, `inventory_give`, `hands_take`, `world_time_set`, `world_weather_set`, `engine_set` | Put the world into the state the test needs — deterministically, from script. Heal a player at once, seated or not, or switch its godmode. |
| **Act** | `vehicle_control`, `object_anim`, `vehicle_door`, `camera_set`, `notify_players`, `weapon_raise`, `weapon_aim`, `weapon_fire`, `weapon_sights`, `input_trigger`, `player_move` | Drive, animate, open or close a car door, frame the shot, raise, aim and fire the local weapon, click, hold or press a key through the script key handlers, and walk the on-foot player in a direction or to a point. |
| **Iterate the UI** | `ui_reload_layout`, `ui_tree`, `ui_dialog`, `capture_screenshot` | Reload a `.layout` written into the client's profile directory and read back the rectangles the engine computed for it. The file is re-read on every call, so a panel can be edited and re-measured in seconds instead of one repack-and-boot per change. `ui_dialog` is the client modal (acknowledge/confirm/form) for the local player. |
| **Observe** | `wait_for`, `logs_since`, `query_player_state`, `weapon_state`, `object_inspect`, `object_doors`, `input_describe`, `world_time_get`, `vehicle_telemetry`, `vehicle_trace`, `player_trace`, `anim_timeline`, `scene_raycast`, `surface_query`, `capture_screenshot` | Structured state from the server, log tails since a cursor, 20 Hz vehicle and on-foot player traces, client action/animation-phase timelines, raycasts, frames. Building door flags, registered input bindings and the in-game clock are reads. Data an agent can assert on, not pixels to squint at. |
| **Reset + repeat** | `restore_gameplay`, `dayz_test_stop`, `session_acquire_wait`, `session_release`, `session_status` | Return the world to normal, stop the managed run, hand the game to the next session. |

An agent that can call these can iterate on a mod the way it iterates on code: change,
build, run, measure, fix — the way this repo itself was developed and gated.

**Godmode is on by default.** In every run that loads DayZ_MCP, the server makes each
player immune to damage when it connects or respawns: falls, hits, hunger and thirst
do not hurt or kill it. This changes how damage behaves in the game you test. To test
vanilla damage, call `player_godmode(on=false)`: the player takes damage again, and it
stays that way for that player identity across `player_respawn` until the mission ends
or you switch it back on. `query_all_players` and `query_player_state` report `godmode`
for each player, and `player_heal` heals a player at once without moving it.

For a custom terrain, `dayz_test_run(project, mode="server", navmesh_data_server=True, …)`
starts the managed server with `-startNavmeshDataServer` for DayZ Tools NavMeshGenerator.
The option defaults to false and rejects other modes and `pack_only`; `preflight`
only validates. Connect the generator and save its `.nm` separately: launcher readiness
does not certify a generator connection or a usable navmesh. Stop this run with
`dayz_test_stop(run_id)` as usual.

## The server, as tools

| Need | Tool(s) |
|---|---|
| Who is online, where, in what state | `query_all_players`, `query_player_state`, `object_inspect` |
| Move, equip, stage | `player_teleport`, `player_heal`, `player_godmode`, `inventory_give`, `inventory_attach`, `world_spawn`, `object_delete`, `object_anim`, `vehicle_prepare_fixture` |
| Set the stage | `world_time_set`, `world_weather_set`, `engine_set` |
| Talk to players | `notify_players` |
| Watch | `logs_since` (tail from a cursor), `wait_for` (block on a condition or a log substring), `telemetry_read`, `vehicle_telemetry`, `world_time_get` (the in-game clock) |
| Undo, hand over | `restore_gameplay`, `session_acquire_wait` / `session_release` / `session_status` |

Everything server-side works against a headless server — it returns data, not frames.
Visual capture (`capture_screenshot`, `camera_get`, `camera_set`) needs a rendered
client on the same machine. Several agents can share one running game: the daemon
owns the port, hands out one lease at a time and audits what each holder did.

## How it works

Control and data are **engine-native and server-authoritative**: `CreateObjectEx`,
`StartCommand_Vehicle`, the `Car` setters, `RaycastRVProxy`, `SetTimeMultiplier`,
read back in `MissionServer`. No synthesised keystrokes, no OCR. The one exception is
visual capture — `MakeScreenshot` is broken in the diag build (T165276), so frames
come from an external window grab of the rendered client, which only reads pixels.

**86 tools (+ `exec_enforce` when an allowlist is configured)** across world, player,
vehicle, camera, telemetry, lifecycle, knowledge and session coordination:
`action_cursor`, `action_hold`, `action_use`, `anim_timeline`, `bridge_status`, `camera_get`, `camera_set`, `capture_screenshot`,
`dayz_knowledge_find`, `dayz_knowledge_prepare`, `dayz_knowledge_show`, `dayz_knowledge_status`, `dayz_test_close`, `dayz_test_run`, `dayz_test_stop`, `engine_set`, `entities_query`, `hands_take`, `infected_drive`, `input_describe`, `input_trigger`, `inventory_attach`, `inventory_give`, `key_press`,
`lease_acquire`, `list_projects`, `logs_since`, `notify_players`, `object_anim`,
`object_delete`, `object_doors`, `object_inspect`, `object_resolve`, `pipeline_feedback`, `pipeline_inbox`,
`pipeline_resolve`, `playbook_reload`, `playbook_run`, `player_godmode`, `player_heal`, `player_kill`, `player_look_at`, `player_move`, `player_respawn`, `player_teleport`, `player_trace`, `bot_start`, `bot_stop`, `query_all_players`, `query_get_in_condition`,
`query_player_state`, `restore_gameplay`, `scene_raycast`, `session_acquire`,
`session_acquire_wait`, `session_cancel`, `session_heartbeat`, `session_release`,
`session_status`, `session_wait`, `surface_query`, `telemetry_read`, `ui_click`, `ui_dialog`, `ui_focus`, `ui_reload_layout`,
`ui_set_text`, `ui_tree`, `vehicle_control`, `vehicle_door`, `vehicle_enter`, `vehicle_get_in_client`,
`vehicle_prepare_fixture`, `vehicle_release`, `vehicle_telemetry`, `vehicle_trace`,
`wait_for`, `weapon_aim`, `weapon_fire`, `weapon_raise`, `weapon_sights`, `weapon_state`, `world_spawn`, `world_time_get`, `world_time_set`, `world_weather_set`.
Several agent sessions can share one running game through a single daemon that owns
the port and hands out leases. The full surface, the transport and the security
model are in [`dayz-mcp-architecture.md`](dayz-mcp-architecture.md);
the acceptance contract is in [`product-spec.md`](product-spec.md). The rules that
hold the design together, and why each one exists, are in
[`ARCHITECTURE-DECISIONS.md`](ARCHITECTURE-DECISIONS.md).

## In-game numbers

**A1.** `query_player_state` against an independent mission marker (`target=marker`): **0.0313 m**, pass line < 0.5 m (`product-spec.md:42`).

**A2.** Python held `/poll` for 600 ms; `ticks_in_flight` = **4741** (run_045213). Pass line ≥ 5 (`product-spec.md:43`).

**Infected heading.** `infected_drive` at 90°: authoritative heading **92.1°** (error 2.1°). Run `1a1cb6e1-7230-43c4-9164-41ab3b0be936` (`tools/tests/test_task9_spawn_phase_markers.py:101-105`). Same run: 76.6 m away from a player at 0.95 m; 270° measured **270.1°**, 41.7 m in 46 s, 0.02 m lateral; `mode="release"` returns vanilla AI (171.7°, 0.25 m/s).

**B3.** Fixture ready, throttle 1.0, PHYSICS: `engine_on_server=1`, `speedo≈0`, `pos_delta≈0` (`product-spec.md:56`). `ActionStartEngine` returns on `INSTANCETYPE_SERVER` when PHYSICS (`actionstartengine.c:51-58`). Cars are not driven from `MissionServer` (`product-spec.md:325`). Cars move from the owning client (`vehicle_control`).

## What this cannot do, and why

### What this is NOT

DayZ-MCP is a local control and observation surface, not a hosted knowledge service.

- It does not require embeddings, a vector store, or a paid service.
- It does not use OCR to interpret the game.
- It does not call `SendInput` or synthesize operating-system keystrokes.
- It sends no usage analytics or other telemetry; runtime game telemetry is read locally.
- It includes no integrated knowledge database.

**Visual capture is not engine-native.** `MakeScreenshot` exists as a proto (`proto.c:142`) and is a no-op on DayZDiag as well as retail ([T165276](https://feedback.bistudio.com/T165276)). Probe 2026-06-06: `MakeScreenshot("$profile:mcpshot.dds")` twice, zero `.dds` anywhere, `ScreenShots` folder never created (`dayz-mcp-architecture.md:17-24`). `RenderTargetWidget` is display-only — no readback to file or bytes (`dayz-mcp-architecture.md:28-32`). Frames come from an external window-grab of the rendered client (`Graphics.CopyFromScreen`; first probe meanB 65, nbRatio 0.999 — `dayz-mcp-architecture.md:41-42`). That grab is the only non-native piece in the stack. A headless server returns data, not pixels (`dayz-mcp-architecture.md:62-64`; `product-spec.md:162-163`).

**The API key cannot go in an HTTP header.** `RestContext.SetHeader(string)` sets Content-Type only (`restapi.c:135-141`; `tools/README-mcp.md:7`). The key travels as `?key=` (`addon/scripts/5_Mission/MCPBridge.c:226`). The listener binds `127.0.0.1`; there is no `0.0.0.0` mode and no remote mode (`product-spec.md:168`).

**`SetTimeMultiplier(0)` freezes the entire simulation**, animations included (`world.c:19`; `dayz-mcp-architecture.md:185-187`). Condition lighting and weather after seating and animations have finished, not before a pending get-in.

**`infected_drive` `speed` is not metres per second.** `speed=3` measured **0.91 m/s** (`tools/tests/test_task9_spawn_phase_markers.py:105`). The scale is uncalibrated.

**PHYSICS cars do not move from the server.** See the B3 probe above. `vehicle_control` is the client-owner path.

**`exec_enforce` does not execute on a headless diag server.** `ExecuteEnforceScript` is marked Developer-only (`game.c:776`) and returned `false` under `NO_GUI`, including with the vanilla script-console wrapper (`product-spec.md:171-177`). Allowlist gating and JSONL audit are verified in-game; script effect is not a contract. The tool is opt-in breakglass, not a general interpreter (`product-spec.md:167`).

**`wait_for` and `logs_since` read script logs and `.RPT` only.** Player chat is not in those files. With `-adminlog`, chat lands in a profiles `.ADM` that no tool reads (`tools/README-mcp.md:128`). `wait_for` on timeout still returns `ok: true` with `satisfied: false` — gate on `satisfied` (`tools/README-mcp.md:124`). Its `pattern` is a plain substring, never a regex: `[DayZ-MCP]`, not `\[DayZ-MCP\]` (`tools/README-mcp.md:126`). For a line printed at mission start, pass `lookback_from="launch"`; `lookback_lines` cannot reach that far back.

**Synchronous RestApi calls block the sim.** `POST_now` is documented as a thread-blocking operation (`restapi.c:125-128`). The bridge uses callback `GET`/`POST` only (`dayz-mcp-architecture.md` §9, "Bloqueo del loop").

**No OS keystrokes, no OCR, no second game process.** Control is `CreateObjectEx` / `StartCommand_Vehicle` (`dayz-mcp-architecture.md:79`, `dayz-mcp-architecture.md:84`) / `MissionServer` reads. No OS input (`product-spec.md:160-161`). Several agent sessions share one running instance through one daemon (`product-spec.md:164-166`).

**Not tested:**

- Navmesh follow: `AIWorld.FindPath`, `RaycastNavMesh`, `SampleNavmeshPosition`, `PGFilter.SetCost` (all in `aiworld.c`; the four protos are adjacent but none has been driven). `AIWorld` has a private constructor (`aiworld.c`, `private void AIWorld()`); `new PGFilter()` has not been instantiated in-game.
- Survivor locomotion overrides: `HumanInputController.OverrideMovementSpeed` / `OverrideMovementAngle` / `OverrideAimChangeX` / `OverrideAimChangeY` (`human.c:234-243`) and the vanilla bot FSM under `4_world/systems/bot/`. Whether a synthetic survivor population is viable is open.
- Calibrating `infected_drive` `speed` to m/s.

## What you need

- Windows, with DayZ and DayZ Tools installed (the server talks to `DayZDiag_x64`)
- Python **3.11 or newer**
- A DayZ server you are allowed to run mods on. The daemon binds `127.0.0.1` only and
  sits on the same machine as the game it drives — there is no remote mode and no
  multi-user mode, by design. The reference deployment is a local DayZDiag server plus
  a client for visual capture; the server-side verbs need only the bridge mod loaded.

Choose the setup that matches the agent:

- **Experienced DayZ modder:** install DayZ-MCP by itself. The MCP exposes the live
  engine without adding a knowledge layer.
- **New to DayZ modding:** use the paired installer. It installs DayZ-MCP with the
  [DayZ Modding Knowledge Pack](https://github.com/willy92wins/DayZ-Modding-Knowledge-Pack)
  by default; pass `-SkipKnowledgePack` to opt out.
- **Agent with its own DayZ knowledge:** DayZ-MCP also works by itself. Its runtime
  responses remain actionable: `bad_args` names the rejected field and expected unit
  or scale; `wait_for` reports `satisfied`, `timed_out`, `observed`, and `scanned` so
  the next step follows evidence; `bridge_status.reason` names the readiness cause.

## Two halves

| | |
|---|---|
| [`addon/`](addon/) | The in-game bridge, in Enforce Script. A `modded class MissionServer` that dispatches commands on `OnUpdate` and answers over HTTP. Build it into a PBO, or run it with file patching. Tracked in git; the development tree sparse-excludes it, so it exists in a full clone but not in every checkout. |
| [`tools/`](tools/) | The Python MCP server, its installer, and the offline gates. |

The mod **pulls** commands and **pushes** results; the Python side is a passive
endpoint. Positions and state are read in `MissionServer`, the authority. The one
exception is driving: a PHYSICS car only moves from its owning client, so
`vehicle_control` runs there (the B3 probe above).

## Install

The usual installer does not need a CLI pin. It registers by calling
`claude mcp add` / `codex.cmd mcp add` directly, so an npm `.cmd` shim is fine:

```powershell
cd tools
.\install-mcp.ps1 -Register
```

It creates `tools\.venv-mcp`, installs the pinned dependencies plus this package,
generates an API key, and writes the client configuration. `-Register` also
registers the server with your MCP client when `dayz-mcp` is not already
registered.

The hardened Python path is different: `python install_mcp.py --register` talks
only to native x64 `claude.exe` / `codex.exe` recorded on this machine. Pin those
first (writes under `%LOCALAPPDATA%\DayZ_MCP\security\`):

```powershell
cd tools
python install_mcp.py --pin-clis
python install_mcp.py --register
```

If `claude` / `codex` on PATH are shims (`.cmd` / `.ps1`), pass the native x64
executables with `--claude-exe` and `--codex-exe`. Re-run `--pin-clis` after those
binaries change. `.\install-mcp.ps1 -Register` does not read that pin.

In client mode the first tool list is compact: the session and lifecycle tools plus
the reads that need no lease. A lease reveals the rest; releasing it, or a call that
finds it expired or lost, hides them again, and the server sends
`tools/list_changed` each time. Claude Code does not re-list tools after
`tools/list_changed` (#93), so a client registered with `--client-platform claude`
lists every tool from the start, as `--no-progressive-disclosure` does: that is now
the default for Claude. `.\install-mcp.ps1 -Register -ClaudeNoProgressiveDisclosure`
and `python install_mcp.py --register --claude-no-progressive-disclosure`, which add
the flag, are still accepted. Codex keeps the compact list. The list does not gate
calls: a tool it leaves out still runs when called by name, and lease-gated tools
refuse to run without a lease whatever the list shows.

Both installers register the client with `--supervised`: it serves the host from a
worker the supervisor can replace, so `server_reload` picks up edited sources without
the host reconnecting, and the lease crosses each replacement. `--no-supervised` /
`-NoSupervised` opts out. `python install_mcp.py --register` refuses to drop an
option the current registrations carry (`registration_would_drop_options` names each
one) unless you add `--allow-option-removal`. `.\install-mcp.ps1 -Register` only
adds `dayz-mcp` when Claude and Codex do not already have it, and that path
never removes a name. If one of them does, the check cannot be read, or the
name appears before the add, the script stops. Re-register with
`python tools/install_mcp.py --register` from the repository root, or pass
`-ReplaceExistingRegistration` when you really want this script to remove and
replace the current registration (that drops options it already carries).

Three run modes (`python -m dayz_mcp`; `tools/dayz_mcp/server_cli.py:96-118`):

- `--client` — what the installer registers. Does not bind; proxies to the daemon
  and starts it lazily. Lets several agent sessions share one running game.
  It is also the official stdio fallback (plan B): spawn the same process the
  installer registers, speak MCP JSON-RPC on stdin/stdout, and close it at
  session end. See [tools/README-mcp.md#host-vsock-failure-official-stdio-plan-b](tools/README-mcp.md#host-vsock-failure-official-stdio-plan-b).
- `--daemon` — the single owner of the port; the only process that talks to the game.
- no flag — embedded single-session mode.

### Before the first test run

Each of these fails closed with a named error instead of guessing (#93):

- **Server config and profiles.** Each project's `dev_root\_server` and
  `dev_root\_client` roots must already exist, and so must
  `dev_root\_server\serverDZ.cfg`. The default instance also requires the
  legacy leaves `dev_root\_server\profiles` and `dev_root\_client\profiles`
  (offline uses the client leaf); this batch does not create those. A named
  instance (`--instance <token>`) creates only the missing leaf
  `profiles-<token>` under the existing role root during launch preparation,
  and seeds `dayz_mcp.json` there. An existing file at that leaf, a missing
  role root, or a `dayz_mcp.json` whose endpoint or key belongs to another
  daemon is rejected and nothing above the leaf is created. Named VPP
  SuperAdmins and credentials are not copied; put them in the named server
  leaf when that instance should have them. Without the server config the
  server exits with "Could not find server config". A profile the launcher
  cannot seed is `instance_config_missing`.
- **Mission storage that does not classify.** A launch refuses with
  `storage_recovery_required` and does not delete world bytes. The precise
  reason and the repair steps are in
  [docs/STORAGE_RECOVERY.md](docs/STORAGE_RECOVERY.md). Stop retries, inventory
  the mission, and restore only artifacts you can authenticate.
- **One `mod` name per project.** Shared `mod` names are `bad_project`. The PBO file name is not the `$PBOPREFIX$` namespace. `build_namespace_source_mismatch`: "Binarize requires the source folder name to match the PBO namespace, ignoring ASCII case. Use pack_only=true if binarization is unnecessary; for a single-segment namespace, rename the source folder compatibly with the project's registered source policy; otherwise use the project's own build." `pack_only` is exempt.

- **No retail client.** A running `DayZ_x64.exe` or `DayZ_BE.exe` blocks every
  test run with `retail_quarantine`. Close it first.
- **Which DayZDiag.** The daemon starts only the `DayZDiag_x64.exe` under
  `DAYZ_GAME_PATH` (default: `C:\Program Files (x86)\Steam\steamapps\common\DayZ`).
  Any other executable is `executable_not_allowed` (the retail executables of
  that install give `retail_manual_lifecycle_required`). For a second install
  such as `DayZ Exp`, start the daemon with `DAYZ_GAME_PATH` set to it.
- **DayZ Tools on another drive.** The bundle builder and the bundle verifier find
  DayZ Tools the same way: `DAYZ_TOOLS_PATH` if it points at them, then the
  registry, then the default `C:` path. `dayz_test_run(build=true)` runs the
  AddonBuilder sealed into the native launcher when it was built, so the daemon
  must find the same DayZ Tools. If DayZ Tools move, build and register the
  launcher again (steps 2 and 3 under "What is deliberately not in this repo").

## Security model

Fail-closed from the first line: the listener binds `127.0.0.1` only, every request
carries an API key, and commands outside the whitelist are rejected. The key
travels as a query parameter because the engine's `RestContext.SetHeader()` only
sets `Content-Type` — with a loopback bind the exposure stays local, and the key
is generated per install and never committed.

## What is deliberately not in this repo — and how to generate it

Three pieces are machine-local by design. Each one is generated on your machine,
in this order:

1. **The launcher policy.** The builder reads a host file outside the repo and
   never loads the published example. Copy `tools/launcher-policy.example.json`
   to `%LOCALAPPDATA%\DayZ_MCP\launcher-policy.json` and replace `ExampleMod`
   and every path with trees that exist on your machine —
   [tools/README-mcp.md](tools/README-mcp.md) ("Native launcher host policy")
   walks each field.
2. **The built native launcher.** Its source is under
   `tools/native-launchers/dayz-test-v1/src/`; the build output is 40+ MB of
   compiled launcher and embedded CPython, sealed against the
   machine that built it (`.gitignore:15-16`). The shipped dependency lock pins the author's MSVC
   and Windows SDK, so re-pin it to yours, then build:

   ```powershell
   cd tools
   .\.venv-mcp\Scripts\python.exe relock_toolchain.py
   .\.venv-mcp\Scripts\python.exe build_native_launcher.py --verify-reproducible
   ```

   The relock rewrites only the toolchain description; every shipped
   supply-chain pin (vendored psutil wheel, embedded CPython URL and hash)
   stays byte-identical. The build downloads the pinned CPython once, verifies
   it against the lock, and builds twice to prove the output reproducible.
3. **The launcher registry.** Only the empty baseline ships. Seed the live
   registry from it, then install the bundle you just built:

   ```powershell
   .\.venv-mcp\Scripts\python.exe -m dayz_mcp.launcher_registry_update bootstrap
   .\.venv-mcp\Scripts\python.exe -m dayz_mcp.launcher_registry_update install-dayz-test-v1 --expected-sha256 <sha printed by bootstrap>
   ```

## Tests

The MCP server and its test suite are Windows-only.

```powershell
cd tools
.\.venv-mcp\Scripts\python.exe -m unittest discover -s tests -t .
```

Tests that need a built launcher, an installed registry or development-only
evidence skip with the reason named. Everything else passes on a fresh clone, so
anything red is worth reporting.

For the edit-run loop there is a fast tier. With `DAYZ_MCP_FAST_TESTS=1` the
tests marked `@slow_test` in `tools/tests/_tiers.py` skip with their reason: a
few hundred that start real processes or wait on real time, and most of the
suite's run time. The rest runs in about a minute. It is a subset, not a
verdict: CI runs the whole suite on every pull request, and that run is the
one that has to pass.

```powershell
$env:DAYZ_MCP_FAST_TESTS = "1"
.\.venv-mcp\Scripts\python.exe -m unittest discover -s tests -t .
Remove-Item Env:DAYZ_MCP_FAST_TESTS   # back to the whole suite
```

The virtualenv has to be the one the installer creates, at `tools/.venv-mcp`:
the daemon resolves that path when it checks its own identity, and an environment
somewhere else fails at startup rather than falling back. The daemon then prints
one line naming the interpreter it expects and exits with code 78.

## Licence

MIT — see [LICENSE](LICENSE).

DayZ is a trademark of Bohemia Interactive. This project is not affiliated with or
endorsed by Bohemia Interactive, and ships none of their game data.
