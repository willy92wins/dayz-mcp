# DayZ-MCP

An MCP server that drives **DayZ** (through `DayZDiag_x64`) as typed tools, so an
agent can spawn objects, move players and vehicles, read engine state and capture
frames without synthesising keystrokes or reading the screen with OCR.

Control and data are engine-native: `CreateObjectEx`, `StartCommand_Vehicle`, the
`Car` setters, `RaycastRVProxy`, `SetTimeMultiplier`. The one exception is visual
capture — `MakeScreenshot` is broken in the diag build (T165276), so frames come
from an external window grab of the rendered client, which only reads pixels.

**39 tools** across world, player, vehicle, camera, telemetry and session
coordination. The full surface, the transport and the security model are in
[`dayz-mcp-architecture.md`](dayz-mcp-architecture.md); the acceptance contract is
in [`product-spec.md`](product-spec.md).

## What you need

- Windows, with DayZ and DayZ Tools installed (the server talks to `DayZDiag_x64`)
- Python **3.10 or newer**
- A DayZ server you are allowed to run mods on. This is a development tool: it is
  meant for a local diag server, not for a live one.

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

## What is deliberately not in this repo

- **The built native launcher.** Its source is under
  `tools/native-launchers/dayz-test-v1/src/`; the build output is tens of megabytes
  of compiled launcher and embedded CPython, sealed against the machine that built
  it. Build it yourself with `tools/build_native_launcher.py`.
- **The launcher policy.** The builder reads it from a host file outside the repo;
  `tools/launcher-policy.example.json` is the shape to fill in with your own roots.
- **The launcher registry.** Only the empty baseline ships; a clone installs onto it.

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
