# Quickstart

Five steps, from a fresh clone to an agent reading live state out of your server.
An AI agent can follow [llms-install.md](llms-install.md), which is the same clone path in short form (the no-clone install from a GitHub release is not published yet).

Everything else in this repo is optional until this works.

**You need** Windows, DayZ **and** DayZ Tools from Steam, Python **3.11 or newer**,
and a DayZ server you may load mods on. The daemon binds `127.0.0.1` and drives a game on the same machine;
there is no remote mode.

You also need **Claude Code** and **Codex CLI**, both installed as
native Windows executables. `.\install-mcp.ps1 -Register` (step 2) requires both:
it refuses with `registration check failed for Codex: command missing` (or the
Claude equivalent) if either is absent, and it does not register Claude alone.
The pre-check uses `claude` / `codex.cmd`; registration then needs the native
`claude.exe` / `codex.exe` (`install_mcp.py`). Without those you get
`installer_cli_not_native_exe`. The `claude mcp add` / `codex.cmd mcp add`
lines the installer prints are only for doing it by hand (`tools/install-mcp.ps1`).
When both are present, they must hold an identical
`dayz-mcp` registration (a Codex config file without the entry, or Codex
registered without Claude, still refuses with `daemon_provenance_incomplete`
in `tools/dayz_mcp/host_config.py`, `resolve_daemon_provenance`).

AddonBuilder also needs the **Visual C++ runtimes**. Without any of them it
dies with `System.BadImageFormatException: Could not load file or assembly
'SteamHelper.dll' ... (0x800700C1)` (exit `-532462766`) before packing
anything. Steam ships the installers under
`<Steam>\steamapps\common\Steamworks Shared\_CommonRedist\vcredist\{2010,2012,2013,2019}`,
but only runs them the first time a game is launched from its UI; on a clean
machine, run those installers yourself first.

**GitHub Release variant (historical).** Do not use the v1.5 addon zip on a current
(1.6) clone: that zip ships `bridge_version` 12, and this tree expects 13, so
`bridge_status` stays `ready: false` (`version_mismatch`). The v1.5 zip is only
valid after `git checkout v1.5`, or pack the PBO from this clone (step 3).
A labelled historical file from that release is
[VERSION.json](https://github.com/willy92wins/dayz-mcp/releases/download/v1.5/VERSION.json)
(v1.5; not an install recipe). The Python half is still the clone path: the wheel
and `dayz-mcp-requirements.txt` are not in a published release.

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

`-Register` adds `dayz-mcp` only when Claude and Codex do not already have it,
and that path does not remove an existing name. If they do, or the name appears
before the add, the script stops. Re-register from this directory with `python
install_mcp.py --register` (it refuses to drop options the registration already
carries unless `--allow-option-removal` is passed), or pass
`-ReplaceExistingRegistration` to remove and replace it with the script.

`-Register` resolves **both** native CLIs and stops with
`installer_cli_not_native_exe` when either is missing. On a host with Claude
Code only (no `codex` on `PATH` and no `~/.codex/config.toml`), register from
this directory instead:
```powershell
python install_mcp.py --pin-clis --claude-exe "C:\path\to\claude.exe"
python install_mcp.py --server-profiles "C:\path\to\your\server\profiles" --register
```
If a `codex` CLI or a Codex config appears later, `--register` refuses with
`installer_cli_missing` until you pin again with `--codex-exe`. An installed
(wheel) `dayz-mcp doctor` then reports Codex as `CLI_NOT_PINNED`; from this
editable checkout `doctor` does not use the pinned CLIs: it checks the Codex
registration through the `codex` on `PATH`.

**3 — Pack the addon** into `<DayZ>\!Workshop\@DayZ_MCP\Addons\DayZ_MCP.pbo`. Set
`DAYZ_TOOLS_PATH` if DayZ Tools are not under `C:\Program Files (x86)`; the script names
every path it tried before failing. The script packs the git-tracked `addon/` of the
commit named by `-Ref` (default `HEAD`) through a stage folder under
`P:\temp\dayz-pack-stage`, so commit first, because uncommitted edits and untracked files
are not packed. `-Source <folder>` packs a plain folder instead and copies every file
except editor backups and Markdown:
```powershell
.\pack-addon.ps1 -ModName DayZ_MCP
```

**4 — Load it.** Add `-mod=<DayZ>\!Workshop\@DayZ_MCP` to your existing server command
line. Nothing else changes. Retail works for everything here; `DayZDiag_x64.exe` is only
needed if you want `-filePatching`. If the path contains spaces (the default
Steam location does), quote **the whole argument**, never just the value:
`"-mod=C:\Program Files (x86)\Steam\steamapps\common\DayZ\!Workshop\@DayZ_MCP"`.
To load the addon only on the server instead, see
[docs/SERVERMOD.md](docs/SERVERMOD.md) (`-serverMod=`).

**5 — Ask the agent for `bridge_status`.** `ready: true` with `server_peer.last_poll_age_s`
under a second or two means the tools are live — try `query_all_players`, then
`logs_since`. If it is not ready, `reason` names the cause: `no_run` (no server seen),
`server_poll_stale` (mod loaded but not polling — check `dayz_mcp.json` landed in the
profiles folder the server actually uses), or `version_mismatch` (repack the PBO).

For lease cadences (heartbeat 45 s vs TTL 120 s) and coordination details, see [tools/README-mcp.md#lease-cadences](tools/README-mcp.md#lease-cadences).

If you need the installer `--client` on stdin/stdout instead of the host MCP channel, spawn that registered process (plan B) and close it at handoff: [tools/README-mcp.md#host-vsock-failure-official-stdio-plan-b](tools/README-mcp.md#host-vsock-failure-official-stdio-plan-b). Host-only policy (when a host transport is dead for the rest of a session) lives in the host runbook, not here.

Not needed for any of this: the native launcher, `dayz_test_run`, and the launcher
registry — those let the agent *start* the game itself. [README.md](README.md) covers
them, the run modes, the security model, and what this project cannot do.

## Troubleshooting

**Packing must not need Steam open.** `pack-addon.ps1` passes
`-toolsDirectory=<DayZ Tools root>` to AddonBuilder itself, so packing works
from a session that cannot see a running Steam (SSH, a service, another
logon session). If your copy of the script does not do that yet and
AddonBuilder logs `[ERROR]: DayZ Tools path is unknown, Steam must be running
or specify -toolsDirectory.` and exits 0 without writing the PBO, pass the
argument manually as a workaround: add
`-toolsDirectory=<DayZ Tools folder>` (the folder containing
`Bin\AddonBuilder\AddonBuilder.exe`) to the AddonBuilder invocation. Writing
the `HKCU\Software\Bohemia Interactive\Dayz Tools` registry key does not fix
this on its own.

**Interrupted install.** If another program edits the `dayz-mcp` entry while
`--register` runs, the installer keeps that edit and stops with
`registration_rollback_preserved_foreign_edit`; later runs stop with
`registration_preserved_foreign_edit` until you decide. Remove the entry
(`claude mcp remove -s user dayz-mcp`, `codex mcp remove dayz-mcp`; a named
instance uses its own name) or put back the registration you had before, then
run the install again: it releases that attempt's journals and registers.
`registration_recovery_conflict` means an interrupted run left a journal that
no longer matches the files or the registration, and nothing was overwritten.
Its remedy says which case applies: remove the changed entry and put back the
previous registration in the other client; or, for a journal of an older
installer or another server name, resolve that install (removing an entry does
not release it); otherwise put everything back to what that install recorded.
