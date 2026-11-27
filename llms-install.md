# Install DayZ-MCP with an AI agent

DayZ-MCP is an MCP server and in-game bridge mod that lets an AI agent inspect and control a local DayZ game. Everything stays on this machine: the daemon listens only on `127.0.0.1`, and the installer generates a separate API key for each installation. This guide is for an AI agent (Claude Code or Codex) installing DayZ-MCP on Windows for a person.

## What the person does

You cannot do these steps from a shell, so ask the person to handle them:

- Subscribe to the [DayZ-MCP Workshop item](https://steamcommunity.com/sharedfiles/filedetails/?id=3846741037) in Steam and wait for Steam to download it (no command can subscribe without their click).
- Have Steam with DayZ installed, a DayZ server on this same machine that they may load mods on (retail `DayZServer` works), Python 3.11 or newer, and both Claude Code and Codex installed as native Windows executables. `.\install-mcp.ps1 -Register` requires both: it refuses with `registration check failed for Codex: command missing` (or the Claude equivalent) if either is absent, and it does not register Claude alone.
- Give you the path of their server profiles folder and of their client profiles folder (`-ClientProfiles`), restart the MCP client when you ask, and start the game with the `-mod=` and `-profiles=` arguments you build below.
- A git clone of this repository. The no-clone install from a GitHub release (the wheel and `dayz-mcp-requirements.txt`) is not published yet. Do not invent or use a `releases/download/...` URL for those files until a release includes them and a GET of the asset returns HTTP 200.

## Commands

```powershell
git clone https://github.com/willy92wins/dayz-mcp.git
cd dayz-mcp\tools
.\install-mcp.ps1 -ServerProfiles "<the person's server profiles folder>" -ClientProfiles "<the person's client profiles folder>" -Register
```

The five-step clone path in [QUICKSTART.md](QUICKSTART.md) is the same install; this guide is the short form for an agent when the Workshop item is already subscribed, so packing the addon from `P:\` is not required. Without `-ClientProfiles` the installer does not write the client's `dayz_mcp.json`.

## Reading the installer result

`install-mcp.ps1` does not print `-mod=` or `-profiles=`. After a successful install, build those arguments yourself and give them to the person.

The Workshop item lands at `<Steam>\steamapps\workshop\content\221100\3846741037` (Steam's library folder is usually `C:\Program Files (x86)\Steam`). Confirm that folder contains `Addons\DayZ_MCP.pbo`. The launch argument is `-mod=<that folder>`. If the path contains spaces, quote the whole argument (`"-mod=C:\Program Files (x86)\Steam\steamapps\workshop\content\221100\3846741037"`), never just the value. Use the same `-mod=` on the server and on the client.

`-profiles=` is the profiles folder you passed: `-profiles=<server profiles>` on the server and `-profiles=<client profiles>` on the client. Those folders are where the installer writes `dayz_mcp.json` when you pass `-ServerProfiles` and `-ClientProfiles`.

If the server verifies signatures (`verifySignatures = 2`, the default on retail `DayZServer`), have the person copy `Keys\DayZ_MCP.bikey` from the Workshop item folder into the server's `keys` folder.

If you later run `.\dayz-mcp.exe setup --json` from an installed wheel, do not treat every exit code 2 as a `missing` list. Exit 2 means `ok` is not true: when `missing` is a non-empty list, each entry has a `code` and a `remedy` to follow before running the command again; when `missing` is empty, read `error` (`code`, and `remedy` when present) instead.

## Finish and verify

Ask the person to restart the MCP client, because the newly registered server appears only after a restart. Once the game is running with those arguments, call `session_status`, which starts the daemon, and then `bridge_status`. `ready.ready` equal to `true` completes the installation; otherwise `reason` gives the cause.

## Checking the installation later

From the `tools` directory of the clone, run doctor with the venv that the installer created:

```powershell
.\.venv-mcp\Scripts\python.exe -m dayz_mcp.doctor --daemon-policy normal --json
```

If you are in a wheel install's `Scripts` folder instead, invoke doctor as `.\dayz-mcp.exe doctor --daemon-policy normal --json`. In a clean PowerShell the `.\` prefix is required; `dayz-mcp` or `dayz-mcp.exe` without it is not found. When the Workshop item and the package still match, doctor exits 0; otherwise each finding carries a `remedy`.
