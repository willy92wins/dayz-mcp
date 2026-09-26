# DayZ_MCP - location map

Generated 2026-09-08 01:12:47 UTC by `tools\gen-project-map.ps1`.
Este mapa indica DÓNDE están las cosas. El estado actual vive en `HANDOFF.md`.
Regenerar tras mover o cambiar archivos: `powershell -NoProfile -ExecutionPolicy Bypass -File tools\gen-project-map.ps1`.
Tamaños tomados al generar; KB = KiB (1024 bytes), redondeados al entero más cercano.

## Anchors

| What | Path |
|---|---|
| Dev / docs | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\projectmap-2026-09-08\fixture\Clone_dev` |
| Mod source (censo Enforce) | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\projectmap-2026-09-08\fixture\Clone_dev\addon` |
| Tools | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\projectmap-2026-09-08\fixture\Clone_dev\tools` |
| Tests | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\projectmap-2026-09-08\fixture\Clone_dev\tools\tests` (ausente en este checkout) |
| Plans | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\projectmap-2026-09-08\fixture\Clone_dev\plans` (ausente en este checkout) |
| Reviews | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\projectmap-2026-09-08\fixture\Clone_dev\reviews` (ausente en este checkout) |
| Decisions | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\projectmap-2026-09-08\fixture\Clone_dev\decisions` (ausente en este checkout) |
| Server logs (RPT + script.log) | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\projectmap-2026-09-08\fixture\Clone_dev\_server\profiles` (ausente en este checkout) |
| Client logs (RPT + script.log) | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\projectmap-2026-09-08\fixture\Clone_dev\_client\profiles` (ausente en este checkout) |
| Destino PBO | `tools/pack-addon.ps1`: parámetro `-Destination`; evidencias de builds en `reviews/` |

## HANDOFF: cuánto leer

En `HANDOFF.md`, leer el bloque entre `<!-- LIVE-STATE:START -->` y `<!-- LIVE-STATE:END -->`.
Detener la lectura inicial en `LIVE-STATE:END`; buscar después solo las secciones necesarias.
No fijar números de línea, límites de lectura ni tamaños: el bloque cambia en cada cierre.
El histórico separado, si existe, está en `HANDOFF-ARCHIVE.md`.

## Enforce scripts (1 files, 14 B)

Rutas relativas a la fuente del mod: `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\projectmap-2026-09-08\fixture\Clone_dev\addon`.
Censo de `scripts/**/*.c`; no se atraviesan ni se cuentan reparse points (incluidos junctions).

**4_World/**

- `scripts/4_World/Real.c` - 14 B

- **config.cpp** -> `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\projectmap-2026-09-08\fixture\Clone_dev\addon\config.cpp` (21 B)

## Build / test entry points

Rutas relativas a `tools/`. Scripts `.ps1`, `.bat` y `.cmd` en su raíz y en
`_session_coordination/`, `publish/` y `spike0/` (recursivos, sin reparse points).
Se excluyen dependencias, entornos virtuales, fixtures y archivos de trabajo de otras carpetas.
Los módulos unittest están en `tools/tests/`; el gate de este mapa es `tests.test_docs_truth`.

- `gen-project-map.ps1`
- `pack.ps1`
- `publish/nested/pack.ps1`

## Docs in this project

Documentos `.md` de la raíz; el mapa se excluye del inventario para evitar autorreferencia.
- `HANDOFF.md` - estado vivo; leer hasta `LIVE-STATE:END`.
- `GUIA.md` - 28 B, touched 2026-09-08 (UTC)

## Reparse points omitidos

Se comprueba `FILE_ATTRIBUTE_REPARSE_POINT` (`0x400`) antes de descender o contar.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\projectmap-2026-09-08\fixture\Clone_dev\addon\scripts\linked`
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\projectmap-2026-09-08\fixture\Clone_dev\tools\publish\linked`
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\projectmap-2026-09-08\fixture\Clone_dev\tools\spike0`
