# DayZ_MCP - location map

Generated 2026-09-08 01:12:14 UTC by `tools\gen-project-map.ps1`.
Este mapa indica DÓNDE están las cosas. El estado actual vive en `HANDOFF.md`.
Regenerar tras mover o cambiar archivos: `powershell -NoProfile -ExecutionPolicy Bypass -File tools\gen-project-map.ps1`.
Tamaños tomados al generar; KB = KiB (1024 bytes), redondeados al entero más cercano.

## Anchors

| What | Path |
|---|---|
| Dev / docs | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev` |
| Mod source (censo Enforce) | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP` |
| Tools | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools` |
| Tests | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests` |
| Plans | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans` |
| Reviews | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews` |
| Decisions | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\decisions` |
| Server logs (RPT + script.log) | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\_server\profiles` |
| Client logs (RPT + script.log) | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\_client\profiles` |
| Destino PBO | `tools/pack-addon.ps1`: parámetro `-Destination`; evidencias de builds en `reviews/` |

## HANDOFF: cuánto leer

En `HANDOFF.md`, leer el bloque entre `<!-- LIVE-STATE:START -->` y `<!-- LIVE-STATE:END -->`.
Detener la lectura inicial en `LIVE-STATE:END`; buscar después solo las secciones necesarias.
No fijar números de línea, límites de lectura ni tamaños: el bloque cambia en cada cierre.
El histórico separado, si existe, está en `HANDOFF-ARCHIVE.md`.

## Enforce scripts (9 files, 216 KB)

Rutas relativas a la fuente del mod: `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP`.
Censo de `scripts/**/*.c`; no se atraviesan ni se cuentan reparse points (incluidos junctions).

**4_World/**

- `scripts/4_World/MCP_CarScript.c` - 15 KB

**5_Mission/**

- `scripts/5_Mission/MCPBridge.c` - 79 KB
- `scripts/5_Mission/MCPCallbacks.c` - 1 KB
- `scripts/5_Mission/MCPClientBridge.c` - 92 KB
- `scripts/5_Mission/MCPDialogController.c` - 17 KB
- `scripts/5_Mission/MCPJobRunner.c` - 2 KB
- `scripts/5_Mission/MCPMessages.c` - 10 KB
- `scripts/5_Mission/MissionGameplay.c` - 443 B
- `scripts/5_Mission/MissionServer.c` - 409 B

- **config.cpp** -> `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\config.cpp` (536 B)

## Build / test entry points

Rutas relativas a `tools/`. Scripts `.ps1`, `.bat` y `.cmd` en su raíz y en
`_session_coordination/`, `publish/` y `spike0/` (recursivos, sin reparse points).
Se excluyen dependencias, entornos virtuales, fixtures y archivos de trabajo de otras carpetas.
Los módulos unittest están en `tools/tests/`; el gate de este mapa es `tests.test_docs_truth`.

- `_session_coordination/process-identity-probe.ps1`
- `_session_coordination/process-native-query-probe.ps1`
- `gen-project-map.ps1`
- `install-mcp.ps1`
- `mcp-grab.ps1`
- `pack-addon.ps1`
- `process-guard.ps1`
- `publish/clone_cycle.ps1`
- `run-s0-gate.ps1`
- `run-step0.ps1`
- `run-tests.ps1`
- `spike0/mcp-grab-diag.ps1`
- `spike0/spike0-deploy-ping.ps1`
- `spike0/spike0-grab.ps1`
- `spike0/spike0-ping.ps1`
- `spike0/spike0-window-enum.ps1`

## Docs in this project

Documentos `.md` de la raíz; el mapa se excluye del inventario para evitar autorreferencia.
- `HANDOFF.md` - estado vivo; leer hasta `LIVE-STATE:END`.
- `AGENTS.md` - 381 B, touched 2026-07-15 (UTC)
- `AUDITORIA_2026-08-23.md` - 26 KB, touched 2026-08-23 (UTC)
- `AUDITORIA_ANGULOS_ADICIONALES_2026-08-23.md` - 20 KB, touched 2026-08-23 (UTC)
- `AUDITORIA_MCP_2026-09-07.md` - 51 KB, touched 2026-09-07 (UTC)
- `AUDITORIA_PROFUNDA_2026-08-22.md` - 45 KB, touched 2026-08-22 (UTC)
- `AUDITORIA_SOBREINGENIERIA_RONDA2_2026-08-22.md` - 39 KB, touched 2026-08-22 (UTC)
- `CHANGELOG.md` - 2 KB, touched 2026-09-06 (UTC)
- `CLAUDE.md` - 7 KB, touched 2026-08-29 (UTC)
- `dayz-harness-apis.md` - 26 KB, touched 2026-08-22 (UTC)
- `dayz-mcp-architecture.md` - 20 KB, touched 2026-09-02 (UTC)
- `GATES.md` - 4 KB, touched 2026-09-08 (UTC)
- `HANDOFF-ARCHIVE.md` - 272 KB, touched 2026-08-29 (UTC)
- `out.md` - 8 KB, touched 2026-08-28 (UTC)
- `product-spec.md` - 53 KB, touched 2026-09-02 (UTC)
- `QUICKSTART.md` - 3 KB, touched 2026-08-24 (UTC)
- `README.md` - 17 KB, touched 2026-09-02 (UTC)

## Reparse points omitidos

Se comprueba `FILE_ATTRIBUTE_REPARSE_POINT` (`0x400`) antes de descender o contar.
Ninguno encontrado dentro de los directorios censados.
