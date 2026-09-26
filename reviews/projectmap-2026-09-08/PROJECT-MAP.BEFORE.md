# DayZ_MCP - location map

Generated 2026-09-08 01:56 by `tools\gen-project-map.ps1`. Regenerate after moving files.
This answers WHERE things are. Current state lives in `HANDOFF.md`; do not duplicate it here.

## Anchors

| What | Path |
|---|---|
| Dev / docs | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev` |
| Mod source | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP` |
| Tools | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools` |
| Plans | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans` |
| Reviews | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews` |
| Server logs (RPT + script.log) | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\_server\profiles` |
| Client logs (RPT + script.log) | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\_client\profiles` |
| Last PBO built | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-08-19-lane-fence\DayZ_MCP_fence_DCC8730F.pbo` (200 KB, 2026-08-19) |

## HANDOFF: cuánto leer

`HANDOFF.md` tiene **125 líneas**; el bloque vivo termina en la **línea 125** (100%).

**Leerlo así:** `Read(HANDOFF.md, limit: 125)`. El resto es archivo histórico.

## Enforce scripts (9 files, 216 KB)

**4_World/**

- `scripts\4_World\MCP_CarScript.c` - 15 KB

**5_Mission/**

- `scripts\5_Mission\MCPClientBridge.c` - 92 KB
- `scripts\5_Mission\MCPBridge.c` - 79 KB
- `scripts\5_Mission\MCPDialogController.c` - 17 KB
- `scripts\5_Mission\MCPMessages.c` - 10 KB
- `scripts\5_Mission\MCPJobRunner.c` - 2 KB
- `scripts\5_Mission\MCPCallbacks.c` - 1 KB
- `scripts\5_Mission\MissionGameplay.c` - 443 B
- `scripts\5_Mission\MissionServer.c` - 409 B

- **config.cpp** -> `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\config.cpp` (536 B)

## Build / test entry points

- `activate.bat` - This file is UTF-8 encoded, so we need to update the current code page while executing it
- `Activate.ps1`
- `clone_cycle.ps1` - Re-export DayZ-MCP and measure it the way a clone would.
- `deactivate.bat`
- `install-mcp.ps1`
- `mcp-grab.ps1` - Canonical host-side window grab for the DayZ-MCP visual pipeline (fixes the stale-frame bug,
- `mcp-grab-diag.ps1` - ONE-LAUNCH diagnostic for the phase-3 visual capture (2026-06-14). Spawns the player + AKS74U via
- `pack-addon.ps1` - Pack addon/ into <DayZ>\!Workshop\@<ModName>\Addons\<ModName>.pbo with AddonBuilder.
- `process-guard.ps1`
- `process-identity-probe.ps1`
- `process-native-query-probe.ps1`
- `run-s0-gate.ps1` - S0 gate launcher (Fase 5 drivability spike).
- `run-step0.ps1`
- `spike0-deploy-ping.ps1` - Spike 0.1 deploy - repack @MCPTest with the T-A ping (replaces the MakeScreenshot probe as the
- `spike0-grab.ps1` - Spike 0.2 + 0.3 feeder - launch a rendered DayZ client, then capture the CORRECT client window
- `spike0-ping.ps1` - Spike 0.1 orchestrator - T-A transport probe (ratifies SUP-2 / D-12).
- `spike0-window-enum.ps1` - Spike 0.2 - deterministic selector for the DayZDiag CLIENT render window (fixes review P2-a).

## Docs in this project

- `HANDOFF-ARCHIVE.md` - 272 KB, touched 2026-08-29
- `product-spec.md` - 53 KB, touched 2026-09-02
- `AUDITORIA_MCP_2026-09-07.md` - 51 KB, touched 2026-09-08
- `AUDITORIA_PROFUNDA_2026-08-22.md` - 45 KB, touched 2026-08-22
- `AUDITORIA_SOBREINGENIERIA_RONDA2_2026-08-22.md` - 39 KB, touched 2026-08-22
- `AUDITORIA_2026-08-23.md` - 26 KB, touched 2026-08-23
- `dayz-harness-apis.md` - 26 KB, touched 2026-08-22
- `AUDITORIA_ANGULOS_ADICIONALES_2026-08-23.md` - 20 KB, touched 2026-08-23
