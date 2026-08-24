# Quickstart

Five steps, from a fresh clone to an agent reading live state out of your server.
Everything else in this repo is optional until this works.

**You need** Windows, DayZ **and** DayZ Tools from Steam, Python **3.11 or newer**,
and a DayZ server you may load mods on. The daemon binds `127.0.0.1` and drives a game on the same machine;
there is no remote mode.

**GitHub Release variant.** If you downloaded `DayZ_MCP-v<version>-addon.zip`,
verify it against the `SHA256SUMS.txt` published beside it, then extract the archive
without changing its layout. It contains `@DayZ_MCP/Addons/DayZ_MCP.pbo` and
`VERSION.json`. The release asset replaces steps 1 and 3: skip the `P:\` mapping and
`pack-addon.ps1`, but still complete steps 2, 4, and 5.

**1 — Map the work drive.** DayZ Tools resolve addon sources through `P:\`, and
`$PBOPREFIX$` is read relative to it. Point it at the folder *containing* `addon`:
```powershell
subst P: "C:\path\to\the\clone"
```

**2 — Install.** Creates `tools\.venv-mcp`, installs three pinned dependencies, generates
an API key, writes `dayz_mcp.json` into that profiles folder (the bridge reads it as
`$profile:dayz_mcp.json`), and registers the server with your MCP client:
```powershell
cd tools
.\install-mcp.ps1 -ServerProfiles "C:\path\to\your\server\profiles" -Register
```

**3 — Pack the addon** into `<DayZ>\!Workshop\@DayZ_MCP\Addons\DayZ_MCP.pbo`. Set
`DAYZ_TOOLS_PATH` if DayZ Tools are not under `C:\Program Files (x86)`; the script names
every path it tried before failing. `addon\include.lst` decides what goes in — without it
AddonBuilder packs the whole folder, editor backups included:
```powershell
.\pack-addon.ps1 -Source "P:\addon" -ModName DayZ_MCP
```

**4 — Load it.** Add `-mod=<DayZ>\!Workshop\@DayZ_MCP` to your existing server command
line. Nothing else changes. Retail works for everything here; `DayZDiag_x64.exe` is only
needed if you want `-filePatching`.

**5 — Ask the agent for `bridge_status`.** `ready: true` with `server_peer.last_poll_age_s`
under a second or two means the tools are live — try `query_all_players`, then
`logs_since`. If it is not ready, `reason` names the cause: `no_run` (no server seen),
`server_poll_stale` (mod loaded but not polling — check `dayz_mcp.json` landed in the
profiles folder the server actually uses), or `version_mismatch` (repack the PBO).

Not needed for any of this: the native launcher, `dayz_test_run`, and the launcher
registry — those let the agent *start* the game itself. [README.md](README.md) covers
them, the run modes, the security model, and what this project cannot do.
