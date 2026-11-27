# Install DayZ-MCP with an AI agent

DayZ-MCP is an MCP server and in-game bridge mod that lets an AI agent inspect and control a local DayZ game. Everything stays on this machine: the daemon listens only on `127.0.0.1`, and the installer generates a separate API key for each installation. This guide is for an AI agent (Claude Code or Codex) installing DayZ-MCP on Windows for a person.

## What the person does

You cannot do these steps from a shell, so ask the person to handle them:

- Subscribe to the [DayZ-MCP Workshop item](https://steamcommunity.com/sharedfiles/filedetails/?id=3846741037) in Steam and wait for Steam to download it (no command can subscribe without their click).
- Have Steam with DayZ installed, a DayZ server on this same machine that they may load mods on (retail `DayZServer` works), and Claude Code installed as a native Windows executable. Codex is optional: when neither a Codex CLI nor `~/.codex/config.toml` is present, the installer registers Claude only.
- Give you the path of their server profiles folder, restart the MCP client when you ask, and start the game with the launch arguments the installer prints.
- A git clone of this repository. The no-clone install from a GitHub release (the wheel and `dayz-mcp-requirements.txt`) is not published yet. Do not invent or use a `releases/download/...` URL for those files until a release includes them and a GET of the asset returns HTTP 200.

## Commands

```powershell
git clone https://github.com/willy92wins/dayz-mcp.git
cd dayz-mcp\tools
.\install-mcp.ps1 -ServerProfiles "<the person's server profiles folder>" -Register
```

The five-step clone path in [QUICKSTART.md](QUICKSTART.md) is the same install; this guide is the short form for an agent when the Workshop item is already subscribed, so packing the addon from `P:\` is not required.

## Reading the installer result

When `install-mcp.ps1` finishes successfully, give the launch details to the person: the `-mod=` argument for both the server and the client (if the path contains spaces, quote the whole argument (`"-mod=..."`), never just the value) and the `-profiles=` folders that contain `dayz_mcp.json`. If the server verifies signatures (`verifySignatures = 2`, the default on retail `DayZServer`), have the person copy `Keys\DayZ_MCP.bikey` from the Workshop item folder into the server's `keys` folder.

If you later run `.\dayz-mcp.exe setup --json` from an installed wheel, do not treat every exit code 2 as a `missing` list. Exit 2 means `ok` is not true: when `missing` is a non-empty list, each entry has a `code` and a `remedy` to follow before running the command again; when `missing` is empty, read `error` (`code`, and `remedy` when present) instead.

## Finish and verify

Ask the person to restart the MCP client, because the newly registered server appears only after a restart. Once the game is running with those arguments, call `session_status`, which starts the daemon, and then `bridge_status`. `ready.ready` equal to `true` completes the installation; otherwise `reason` gives the cause.

## Checking the installation later

From the `tools` directory of the clone, run doctor with the venv that the installer created:

```powershell
.\.venv-mcp\Scripts\python.exe -m dayz_mcp doctor --json
```

If you are in a wheel install's `Scripts` folder instead, invoke the command as `.\dayz-mcp.exe doctor --json`. In a clean PowerShell the `.\` prefix is required; `dayz-mcp` or `dayz-mcp.exe` without it is not found. When the Workshop item and the package still match, doctor exits 0; otherwise each finding carries a `remedy`.
