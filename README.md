# DayZ-MCP

**An MCP server that puts an agent's hands on a running DayZ: build a mod, launch the
game, put the world into a state, act, and read back what the engine did — 54 typed
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
frame, raycast a placement, record a 20 Hz drive trace as a regression fixture — which
is why the surface reads like a Swiss-army knife. It is one; the two loops above are
what the blades add up to.

## The development loop, as tools

| Step | Tool(s) | What it does |
|---|---|---|
| **Build + launch** | `dayz_test_run(project, mode, build=True, …)` | Packs the mod with AddonBuilder, starts a diag server and/or client with it loaded, waits for readiness. Managed run, returns a `run_id`. |
| **Set up the scene** | `world_spawn`, `player_teleport`, `vehicle_enter`, `inventory_give`, `world_time_set`, `world_weather_set`, `engine_set` | Put the world into the state the test needs — deterministically, from script. |
| **Act** | `vehicle_control`, `object_anim`, `camera_set`, `notify_players` | Drive, animate, frame the shot. |
| **Iterate the UI** | `ui_reload_layout`, `ui_tree`, `ui_dialog`, `capture_screenshot` | Reload a `.layout` written into the client's profile directory and read back the rectangles the engine computed for it. The file is re-read on every call, so a panel can be edited and re-measured in seconds instead of one repack-and-boot per change. `ui_dialog` is the client modal (acknowledge/confirm/form) for the local player. |
| **Observe** | `wait_for`, `logs_since`, `query_player_state`, `object_inspect`, `vehicle_telemetry`, `vehicle_trace`, `scene_raycast`, `surface_query`, `capture_screenshot` | Structured state from the server, log tails since a cursor, 20 Hz vehicle traces, raycasts, frames. Data an agent can assert on, not pixels to squint at. |
| **Reset + repeat** | `restore_gameplay`, `dayz_test_stop`, `session_acquire_wait`, `session_release`, `session_status` | Return the world to normal, stop the managed run, hand the game to the next session. |

An agent that can call these can iterate on a mod the way it iterates on code: change,
build, run, measure, fix — the way this repo itself was developed and gated.

## The server, as tools

| Need | Tool(s) |
|---|---|
| Who is online, where, in what state | `query_all_players`, `query_player_state`, `object_inspect` |
| Move, equip, stage | `player_teleport`, `inventory_give`, `world_spawn`, `object_delete`, `object_anim`, `vehicle_prepare_fixture` |
| Set the stage | `world_time_set`, `world_weather_set`, `engine_set` |
| Talk to players | `notify_players` |
| Watch | `logs_since` (tail from a cursor), `wait_for` (block on a condition or a log substring), `telemetry_read`, `vehicle_telemetry` |
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

**54 tools (+ `exec_enforce` when an allowlist is configured)** across world, player,
vehicle, camera, telemetry, lifecycle and session coordination:
`action_use`, `bridge_status`, `camera_get`, `camera_set`, `capture_screenshot`,
`dayz_test_run`, `dayz_test_stop`, `engine_set`, `entities_query`, `infected_drive`, `inventory_give`,
`lease_acquire`, `list_projects`, `logs_since`, `notify_players`, `object_anim`,
`object_delete`, `object_inspect`, `pipeline_feedback`, `pipeline_inbox`,
`pipeline_resolve`, `playbook_run`, `player_teleport`, `query_all_players`, `query_get_in_condition`,
`query_player_state`, `restore_gameplay`, `scene_raycast`, `session_acquire`,
`session_acquire_wait`, `session_cancel`, `session_heartbeat`, `session_release`,
`session_status`, `session_wait`, `surface_query`, `telemetry_read`, `ui_click`, `ui_dialog`, `ui_focus`, `ui_reload_layout`,
`ui_set_text`, `ui_tree`, `vehicle_control`, `vehicle_enter`, `vehicle_get_in_client`,
`vehicle_prepare_fixture`, `vehicle_release`, `vehicle_telemetry`, `vehicle_trace`,
`wait_for`, `world_spawn`, `world_time_set`, `world_weather_set`.
Several agent sessions can share one running game through a single daemon that owns
the port and hands out leases. The full surface, the transport and the security
model are in [`dayz-mcp-architecture.md`](dayz-mcp-architecture.md);
the acceptance contract is in [`product-spec.md`](product-spec.md).

## Measured, not claimed

Numbers from in-game runs. Each citation is the line that records the figure, not a design target.

**A1 — authoritative player position, 2026-06-07.** `query_player_state` against an independent mission marker (`target=marker`). An earlier harness compared the bridge to its own spawn write and printed 0.000 m; that comparison was discarded. Recert distance: **0.0313 m** (`product-spec.md:42`). The on-disk verdict stores **0.031348823463742695 m** (`tools/poc-verdict.json:117`). Pass line was < 0.5 m. 0.0313 m is the player's vertical settle (`HANDOFF.md`, fase-0 closure note).

**A2 — async round-trip does not stall the tick, 2026-06-07.** Python held `/poll` for 600 ms. The sim kept ticking: `ticks_in_flight = tick_poll_callback - tick_poll_sent` = **4668**, the figure the in-tree verdict carries and a clone can read for itself (`tools/poc-verdict.json:140`). The original 2026-06-07 run recorded 4741 (`product-spec.md:43`, run_045213; `reviews/2026-06-07-r21-claude-poc-code.md:40`). Pass line is ≥ 5; a blocking `RestContext.GET_now` would read ~0. The diag `fps_in_flight` on that run (~7901) is a tick-counter artifact, not production 60 Hz (`reviews/2026-06-07-r21-claude-poc-code.md:40-41`).

**Infected heading, 2026-08-20.** `infected_drive` with heading 90° (east, +X). Authoritative heading via `entities_query`: **92.1°** (error 2.1°). Run `1a1cb6e1-7230-43c4-9164-41ab3b0be936` (`tools/tests/test_task9_spawn_phase_markers.py:101-105`; `HANDOFF.md`, `infected_drive` promotion note). Same run: 76.6 m walked *away* from a player the infected had been attacking at 0.95 m. Inverse command 270° measured **270.1°**, 41.7 m in 46 s, 0.02 m lateral deviation. `mode="release"` returns control to vanilla AI (turns to 171.7° on its own, drops to 0.25 m/s).

**B3 probe — server-side car drive discarded, 2026-06-08.** Fixture ready, throttle 1.0, `NetworkMoveStrategy.PHYSICS`: `engine_on_server=1`, `speedo≈0`, `pos_delta≈0` (`product-spec.md:56`). Vanilla `ActionStartEngine` returns on `INSTANCETYPE_SERVER` when the vehicle is PHYSICS (`actionstartengine.c:51-58`). Decision: do not drive cars from `MissionServer` (`product-spec.md:325`; `decisions/decision-log.md:15`). Cars move from the owning client (`vehicle_control`). Infected use a different controller; that path is the heading measurement above.

## What this cannot do, and why

**Visual capture is not engine-native.** `MakeScreenshot` exists as a proto (`proto.c:142`) and is a no-op on DayZDiag as well as retail ([T165276](https://feedback.bistudio.com/T165276)). Probe 2026-06-06: `MakeScreenshot("$profile:mcpshot.dds")` twice, zero `.dds` anywhere, `ScreenShots` folder never created (`dayz-mcp-architecture.md:17-24`). `RenderTargetWidget` is display-only — no readback to file or bytes (`dayz-mcp-architecture.md:28-32`). Frames come from an external window-grab of the rendered client (`Graphics.CopyFromScreen`; first probe meanB 65, nbRatio 0.999 — `dayz-mcp-architecture.md:41-42`). That grab is the only non-native piece in the stack. A headless server returns data, not pixels (`dayz-mcp-architecture.md:62-64`; `product-spec.md:162-163`).

**The API key cannot go in an HTTP header.** `RestContext.SetHeader(string)` sets Content-Type only (`restapi.c:135-141`; `decisions/decision-log.md:11`). The key travels as `?key=`. The listener binds `127.0.0.1`; there is no `0.0.0.0` mode and no remote mode (`product-spec.md:168`).

**`SetTimeMultiplier(0)` freezes the entire simulation**, animations included (`world.c:19`; `dayz-mcp-architecture.md:185-187`). Condition lighting and weather after seating and animations have finished, not before a pending get-in.

**`infected_drive` `speed` is not metres per second.** `speed=3` measured **0.91 m/s** (`tools/tests/test_task9_spawn_phase_markers.py:105`; `HANDOFF.md`, same note). The scale is uncalibrated.

**PHYSICS cars do not move from the server.** See the B3 probe above. `vehicle_control` is the client-owner path.

**`exec_enforce` does not execute on a headless diag server.** `ExecuteEnforceScript` is marked Developer-only (`game.c:776`) and returned `false` under `NO_GUI`, including with the vanilla script-console wrapper (`product-spec.md:171-177`; `reviews/2026-06-10-fase4b-gate-ingame.md:55-66`). Allowlist gating and JSONL audit are verified in-game; script effect is not a contract. The tool is opt-in breakglass, not a general interpreter (`product-spec.md:167`).

**`wait_for` and `logs_since` read script logs and `.RPT` only.** Player chat is not in those files. With `-adminlog`, chat lands in a profiles `.ADM` that no tool reads (see the `wait_for`/`logs_since` notes in `tools/README-mcp.md` and those tools' descriptions in `tools/dayz_mcp/server.py`). `wait_for` on timeout still returns `ok: true` with `satisfied: false` — gate on `satisfied` (`tools/README-mcp.md:124`). Its `pattern` is a plain substring, never a regex: `[DayZ-MCP]`, not `\[DayZ-MCP\]`. For a line printed at mission start, pass `lookback_from="launch"`; `lookback_lines` cannot reach that far back.

**Synchronous RestApi calls block the sim.** `POST_now` is documented as a thread-blocking operation (`restapi.c:125-128`). The bridge uses callback `GET`/`POST` only (`dayz-mcp-architecture.md` §9, "Bloqueo del loop").

**No OS keystrokes, no OCR, no second game process.** Control is `CreateObjectEx` / `StartCommand_Vehicle` / `MissionServer` reads (`product-spec.md:160-166`). Several agent sessions share one running instance through one daemon.

**Not tested — not claimed** (`HANDOFF.md`, open-questions note):

- Navmesh follow: `AIWorld.FindPath`, `RaycastNavMesh`, `SampleNavmeshPosition`, `PGFilter.SetCost` (all in `aiworld.c`; the four protos are adjacent but none has been driven). `AIWorld` has a private constructor (`aiworld.c`, `private void AIWorld()`); `new PGFilter()` has not been instantiated in-game.
- Survivor locomotion overrides: `HumanInputController.OverrideMovementSpeed` / `OverrideMovementAngle` / `OverrideAimChangeX` / `OverrideAimChangeY` (`human.c:234-243`) and the vanilla bot FSM under `4_world/systems/bot/`. Whether a synthetic survivor population is viable is open.
- Calibrating `infected_drive` `speed` to m/s (same note).

## What you need

- Windows, with DayZ and DayZ Tools installed (the server talks to `DayZDiag_x64`)
- Python **3.14** — the installer asks `py` for 3.14 specifically; `pyproject.toml`
  still declares `>=3.10`, see `tools/README-mcp.md`
- A DayZ server you are allowed to run mods on. The daemon binds `127.0.0.1` only and
  sits on the same machine as the game it drives — there is no remote mode and no
  multi-user mode, by design. The reference deployment is a local DayZDiag server plus
  a client for visual capture; the server-side verbs need only the bridge mod loaded.

## Two halves

| | |
|---|---|
| [`addon/`](addon/) | The in-game bridge, in Enforce Script. A `modded class MissionServer` that dispatches commands on `OnUpdate` and answers over HTTP. Build it into a PBO, or run it with file patching. Tracked in git; the development tree sparse-excludes it, so it exists in a full clone but not in every checkout. |
| [`tools/`](tools/) | The Python MCP server, its installer, and the offline gates. |

The mod **pulls** commands and **pushes** results; the Python side is a passive
endpoint. Nothing is client-authoritative: positions and state are read in
`MissionServer`.

## Install

The usual installer does not need a CLI pin. It registers by calling
`claude mcp add` / `codex.cmd mcp add` directly, so an npm `.cmd` shim is fine:

```powershell
cd tools
.\install-mcp.ps1 -Register
```

It creates `tools\.venv-mcp`, installs the pinned dependencies plus this package,
generates an API key, and writes the client configuration. `-Register` also
registers the server with your MCP client.

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

Three run modes (`python -m dayz_mcp`):

- `--client` — what the installer registers. Does not bind; proxies to the daemon
  and starts it lazily. Lets several agent sessions share one running game.
- `--daemon` — the single owner of the port; the only process that talks to the game.
- no flag — embedded single-session mode.

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
   `tools/native-launchers/dayz-test-v1/src/`; the build output is tens of
   megabytes of compiled launcher and embedded CPython, sealed against the
   machine that built it. The shipped dependency lock pins the author's MSVC
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

```powershell
cd tools
.\.venv-mcp\Scripts\python.exe -m unittest discover -s tests -t .
```

Tests that need a built launcher, an installed registry or development-only
evidence skip with the reason named, so a fresh clone is red only for real
regressions — with one exception. The process tests in
`test_bug046_startup_deadlock` spawn a real daemon and wait 12 s for it; on a
loaded machine that wait expires, and because one of them checks four crash
stages a single flaky run reports up to five `TimeoutExpired` errors from that
module alone. Errors from anywhere else are worth reporting.

## Licence

MIT — see [LICENSE](LICENSE).

DayZ is a trademark of Bohemia Interactive. This project is not affiliated with or
endorsed by Bohemia Interactive, and ships none of their game data.
