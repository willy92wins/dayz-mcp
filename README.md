# DayZ-MCP

**An MCP server that puts an agent's hands on a running DayZ: build a mod, launch the
game, put the world into a state, act, and read back what the engine did — 39 typed
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
| **Observe** | `wait_for`, `logs_since`, `query_player_state`, `object_inspect`, `vehicle_telemetry`, `vehicle_trace`, `scene_raycast`, `surface_query`, `capture_screenshot` | Structured state from the server, log tails since a cursor, 20 Hz vehicle traces, raycasts, frames. Data an agent can assert on, not pixels to squint at. |
| **Reset + repeat** | `restore_gameplay`, `dayz_test_stop`, `session_*` | Return the world to normal, stop the managed run, hand the game to the next session. |

An agent that can call these can iterate on a mod the way it iterates on code: change,
build, run, measure, fix — the way this repo itself was developed and gated.

## The server, as tools

| Need | Tool(s) |
|---|---|
| Who is online, where, in what state | `query_all_players`, `query_player_state`, `object_inspect` |
| Move, equip, stage | `player_teleport`, `inventory_give`, `world_spawn`, `object_delete`, `object_anim`, `vehicle_prepare_fixture` |
| Set the stage | `world_time_set`, `world_weather_set`, `engine_set` |
| Talk to players | `notify_players` |
| Watch | `logs_since` (tail from a cursor), `wait_for` (block on a condition or a log pattern), `telemetry_read`, `vehicle_telemetry` |
| Undo, hand over | `restore_gameplay`, `session_acquire_wait` / `session_release` / `session_status` |

Everything server-side works against a headless server — it returns data, not frames.
Visual capture (`capture_screenshot`, `camera_*`) needs a rendered client on the same
machine. Several agents can share one running game: the daemon owns the port, hands
out one lease at a time and audits what each holder did.

## How it works

Control and data are **engine-native and server-authoritative**: `CreateObjectEx`,
`StartCommand_Vehicle`, the `Car` setters, `RaycastRVProxy`, `SetTimeMultiplier`,
read back in `MissionServer`. No synthesised keystrokes, no OCR. The one exception is
visual capture — `MakeScreenshot` is broken in the diag build (T165276), so frames
come from an external window grab of the rendered client, which only reads pixels.

**39 tools** across world, player, vehicle, camera, telemetry, lifecycle and session
coordination. Several agent sessions can share one running game through a single
daemon that owns the port and hands out leases. The full surface, the transport and
the security model are in [`dayz-mcp-architecture.md`](dayz-mcp-architecture.md);
the acceptance contract is in [`product-spec.md`](product-spec.md).

## What you need

- Windows, with DayZ and DayZ Tools installed (the server talks to `DayZDiag_x64`)
- Python **3.10 or newer**
- A DayZ server you are allowed to run mods on. The daemon binds `127.0.0.1` only and
  sits on the same machine as the game it drives — there is no remote mode and no
  multi-user mode, by design. The reference deployment is a local DayZDiag server plus
  a client for visual capture; the server-side verbs need only the bridge mod loaded.

## Two halves

| | |
|---|---|
| [`addon/`](addon/) | The in-game bridge, in Enforce Script. A `modded class MissionServer` that dispatches commands on `OnUpdate` and answers over HTTP. Build it into a PBO, or run it with file patching. |
| [`tools/`](tools/) | The Python MCP server, its installer, and the offline gates. |

The mod **pulls** commands and **pushes** results; the Python side is a passive
endpoint. Nothing is client-authoritative: positions and state are read in
`MissionServer`.

## Install

```powershell
cd tools
.\install-mcp.ps1 -Register
```

It creates `tools\.venv-mcp`, installs the pinned dependencies plus this package,
generates an API key, and writes the client configuration. `-Register` also
registers the server with your MCP client.

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
evidence skip with the reason named, so a fresh clone is green rather than red for
things it was never given.

## Licence

MIT — see [LICENSE](LICENSE).

DayZ is a trademark of Bohemia Interactive. This project is not affiliated with or
endorsed by Bohemia Interactive, and ships none of their game data.
