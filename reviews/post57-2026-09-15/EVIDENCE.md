# Evidencia — verificación de #57 (2026-09-15)

Puntero local, sin versionar. La evidencia vive en el vault:
`C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\reviews\2026-09-15-post57\`.
Sesión: `C:\Users\guill\ObsidianVault\AI\30_Sessions\2026-09-15-dayzmcp-post57.md`.

| Ficha | Veredicto | Evidencia en el vault |
|---|---|---|
| `bcd8` | Arreglo verificado: `pack-addon.ps1 -Clear` pasa `-packonly` y termina en Build Successful con el fixture de LFSecure todavía bajo `P:\`. Deja una regresión nueva, `63c9`. | `AddonBuilder_first_pack.rpt`, `provenance_first_pack_737DE493.txt` |
| `ba70` | Verde: `capture_screenshot` ok dos veces sin workaround, con el `PSModulePath` heredado en el entorno; 4 frames distintos en cada una. | nota de sesión (run `0be29aea`) |
| `7ad1` | Verde: sin fichero antes del release; después, volcado con `stop_reason=vehicle_release` y 810 filas contiguas. El `stop` posterior da `trace_not_found`. | `dayz_mcp_trace_ba87305522f943dd957c9b2266b3cd83.jsonl` |
| `81f3` | Verde: `player_teleport` del jugador sentado devuelve `occupant_client_seated` con `hint`; servidor y cliente sin cambio de posición. | `logs/`, nota de sesión |
| `de68` | Launcher reconstruido (3 compilaciones con el mismo PE) y registro renovado; `dayz_test_run` vuelve a lanzar. | `build_native_launcher.log`, `launcher/` |

- PBO instalado: `EB62B4E7…` (254861 B), 13/13 `exact` frente a `6096446`, sin entradas ajenas (`provenance_clean_pack_EB62B4E7.txt`).
- Fichas nuevas: `63c9` (`-packonly` ignora `include.lst`) y `1004` (`object_delete` con el cliente sentado deja el render en negro).
- `f298`: sonda pasiva en `bin/f298_probe.py`, registro `f298_probe_launch2.jsonl`.
