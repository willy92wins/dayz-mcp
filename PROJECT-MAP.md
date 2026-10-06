# DayZ_MCP - location map

Generated 2026-10-01 00:44:18 UTC by `tools\gen-project-map.ps1`.
Este mapa indica DÓNDE están las cosas. El estado actual vive en `HANDOFF.md`.
Regenerar tras mover o cambiar archivos: `powershell -NoProfile -ExecutionPolicy Bypass -File tools\gen-project-map.ps1`.
Tamaños tomados al generar; KB = KiB (1024 bytes), redondeados al entero más cercano.

## Anchors

| What | Path |
|---|---|
| Dev / docs | `.` (raíz del repositorio) |
| Mod source (censo Enforce) | `addon` |
| Tools | `tools` |
| Tests | `tools\tests` |
| Plans | `plans` |
| Reviews | `reviews` |
| Decisions | `decisions` (ausente en este checkout) |
| Server logs (RPT + script.log) | `_server\profiles` (ausente en este checkout) |
| Client logs (RPT + script.log) | `_client\profiles` (ausente en este checkout) |
| Destino PBO | `tools/pack-addon.ps1`: parámetro `-Destination`; evidencias de builds en `reviews/` |

## HANDOFF: cuánto leer

En `HANDOFF.md`, leer el bloque entre `<!-- LIVE-STATE:START -->` y `<!-- LIVE-STATE:END -->`.
Detener la lectura inicial en `LIVE-STATE:END`; buscar después solo las secciones necesarias.
No fijar números de línea, límites de lectura ni tamaños: el bloque cambia en cada cierre.
El histórico separado, si existe, está en `HANDOFF-ARCHIVE.md`.

## Enforce scripts (13 files, 359 KB)

Rutas relativas a la fuente del mod: `addon`.
Censo de `scripts/**/*.c`; no se atraviesan ni se cuentan reparse points (incluidos junctions).

**4_World/**

- `scripts/4_World/MCP_AnimTimeline.c` - 14 KB
- `scripts/4_World/MCP_CarDoor.c` - 4 KB
- `scripts/4_World/MCP_CarScript.c` - 19 KB
- `scripts/4_World/MCP_DiagPlugins.c` - 2 KB
- `scripts/4_World/MCP_Weapon.c` - 29 KB

**5_Mission/**

- `scripts/5_Mission/MCPBridge.c` - 96 KB
- `scripts/5_Mission/MCPCallbacks.c` - 2 KB
- `scripts/5_Mission/MCPClientBridge.c` - 152 KB
- `scripts/5_Mission/MCPDialogController.c` - 17 KB
- `scripts/5_Mission/MCPJobRunner.c` - 2 KB
- `scripts/5_Mission/MCPMessages.c` - 21 KB
- `scripts/5_Mission/MissionGameplay.c` - 647 B
- `scripts/5_Mission/MissionServer.c` - 409 B

- **config.cpp** -> `addon\config.cpp` (536 B)

## Build / test entry points

Rutas relativas a `tools/`. Scripts `.ps1`, `.bat` y `.cmd` en su raíz y en
`_session_coordination/`, `publish/` y `spike0/` (recursivos, sin reparse points).
Se excluyen dependencias, entornos virtuales, fixtures y archivos de trabajo de otras carpetas.
Los módulos unittest están en `tools/tests/`; el gate de este mapa es `tests.test_docs_truth`.

- `_session_coordination/process-identity-probe.ps1`
- `_session_coordination/process-native-query-probe.ps1`
- `deploy-addon.ps1`
- `gen-project-map.ps1`
- `install-mcp.ps1`
- `mcp-grab.ps1`
- `pack-addon.ps1`
- `process-guard.ps1`
- `run-tests.ps1`

## Docs in this project

Documentos `.md` de la raíz; el mapa se excluye del inventario para evitar autorreferencia.
- `HANDOFF.md` - estado vivo; leer hasta `LIVE-STATE:END`.
- `AGENTS.md` - 381 B, touched 2026-09-30 (UTC)
- `ARCHITECTURE-DECISIONS.md` - 11 KB, touched 2026-09-30 (UTC)
- `AUDITORIA_2026-08-23.md` - 26 KB, touched 2026-09-30 (UTC)
- `AUDITORIA_ANGULOS_ADICIONALES_2026-08-23.md` - 20 KB, touched 2026-09-30 (UTC)
- `AUDITORIA_MCP_2026-09-07.md` - 51 KB, touched 2026-09-30 (UTC)
- `AUDITORIA_PROFUNDA_2026-08-22.md` - 45 KB, touched 2026-09-30 (UTC)
- `AUDITORIA_SOBREINGENIERIA_RONDA2_2026-08-22.md` - 39 KB, touched 2026-09-30 (UTC)
- `CHANGELOG.md` - 48 KB, touched 2026-10-06 (UTC)
- `CLAUDE.md` - 7 KB, touched 2026-09-30 (UTC)
- `dayz-harness-apis.md` - 26 KB, touched 2026-09-30 (UTC)
- `dayz-mcp-architecture.md` - 20 KB, touched 2026-09-30 (UTC)
- `GATES.md` - 4 KB, touched 2026-09-30 (UTC)
- `HANDOFF-ARCHIVE.md` - 272 KB, touched 2026-09-30 (UTC)
- `product-spec.md` - 71 KB, touched 2026-10-06 (UTC)
- `QUICKSTART.md` - 4 KB, touched 2026-09-30 (UTC)
- `README.md` - 23 KB, touched 2026-10-01 (UTC)

## Reparse points omitidos

Se comprueba `FILE_ATTRIBUTE_REPARSE_POINT` (`0x400`) antes de descender o contar.
Ninguno encontrado dentro de los directorios censados.
