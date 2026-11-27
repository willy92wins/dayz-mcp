# Install DayZ-MCP with an AI agent

DayZ-MCP is an MCP server and in-game bridge mod that lets an AI agent inspect and control a local DayZ game. Everything stays on this machine: the daemon listens only on `127.0.0.1`, and `setup` generates a separate API key for each installation. This guide is for an AI agent (Claude Code or Codex) installing DayZ-MCP on Windows for a person.

## What the person does

You cannot do these steps from a shell, so ask the person to handle them:

- Subscribe to the [DayZ-MCP Workshop item](https://steamcommunity.com/sharedfiles/filedetails/?id=3846741037) in Steam and wait for Steam to download it (no command can subscribe without their click).
- Have Steam with DayZ installed, a DayZ server on this same machine that they may load mods on (retail `DayZServer` works), and Claude Code installed as a native Windows executable. Codex is optional: when neither a Codex CLI nor `~/.codex/config.toml` is present, `setup` registers Claude only.
- Restart the MCP client when you ask, and start the game with the launch arguments `setup` prints.

No repository clone and no pre-installed Python are needed.

## Commands

```powershell
winget install --id astral-sh.uv -e
uv python install 3.14 --no-bin --no-registry
& (uv python find --managed-python 3.14) -c "import os, subprocess, sys; subprocess.run([os.path.realpath(sys.executable), '-m', 'venv', '--without-pip', sys.argv[1]], check=True)" "$env:LOCALAPPDATA\DayZ_MCP\venv-1.5"
uv pip install --python "$env:LOCALAPPDATA\DayZ_MCP\venv-1.5\Scripts\python.exe" --link-mode=copy --require-hashes -r https://github.com/willy92wins/dayz-mcp/releases/download/v1.5/dayz-mcp-requirements.txt
& "$env:LOCALAPPDATA\DayZ_MCP\venv-1.5\Scripts\dayz-mcp.exe" setup --json
```

Run the first line only if `uv` is not already on `PATH`, and use a new shell afterwards because `winget` puts `uv` on the `PATH` of new shells.
The virtual environment is created from the real path of uv's Python rather than with `uv venv`, because the `python.exe` that `uv venv` makes is a launcher that runs from another folder: while a daemon started through it runs, any other DayZ-MCP daemon on the machine waits 30 s and exits.
The requirements file pins the wheel and every dependency by hash, so if even one byte changes, `uv` installs nothing.
The venv folder carries the release version (`venv-1.5` for this release), so an upgrade runs the same commands with the new version into a new folder.

## Reading the setup JSON

When `dayz-mcp setup --json` exits `0` with `ok: true`, give the launch details to the person:

- `mod_arg` is the `-mod=` argument for both the server and the client; if the path contains spaces (the default Steam library path does), quote the whole argument (`"-mod=..."`), never just the value.
- `server_profiles_arg` and `client_profiles_arg` are the `-profiles=` arguments whose folders contain `dayz_mcp.json`.
- `registered` lists which MCP clients now have the server (`server_name`) registered.
- If the server verifies signatures (`verifySignatures = 2`, the default on retail `DayZServer`), have the person copy `Keys\DayZ_MCP.bikey` from the Workshop item folder into the server's `keys` folder.

When `setup --json` exits `2`, `missing` lists each problem with its `code` and `remedy`; follow the `remedy` and run `setup --json` again.

## Finish and verify

Ask the person to restart the MCP client, because the newly registered server appears only after a restart. Once the game is running with those arguments, call `session_status`, which starts the daemon, and then `bridge_status`. `ready.ready` equal to `true` completes the installation; otherwise `reason` gives the cause.

## Checking the installation later

Run `dayz-mcp doctor --json` from the same `Scripts` folder to check whether the Workshop item and the wheel still match; if they do not, it prints the command that fixes it.
