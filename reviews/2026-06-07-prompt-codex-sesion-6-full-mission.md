# Prompt — Step 1 fix: mission completa para que spawnee el jugador · Codex sesión 6

```
===== PROMPT INICIO =====

Tarea: arreglar `run-poc.ps1` para que un jugador realmente spawnee in-game, y re-correr A1/A2/A3.
NO toques el mod ni el transporte: ya están PROBADOS (round-trip OK; no-bloqueo demostrado con
ticks_in_flight=1786 durante un GET con delay 600ms). El único blocker es que NINGÚN jugador llega
al server.

Causa raíz (verificada por Claude en los RPT del run _poc\run_20260607_034443):
- La mission en `$MissionWs` está VACÍA (solo `init.c` + dayz_mcp.json) → sin estructura chernarus.
- Server RPT: CERO eventos de conexión (no `PlayerConnect`/`OnClientNew`/`CreateCharacter`); error
  construyendo la mission `MissionBase()`→`PluginManagerInit()` (missionbase.c:21) desde
  `CreateCustomMission` (init.c:29). Client RPT: 26 líneas, se quedó en carga ~31s, nunca in-game.
- El override `CreateCharacter` (spawn en coord fija) es correcto PERO nunca se llamó (no hubo player).

El fix PROBADO es el de MCPTest: copiar la mission COMPLETA y luego sobrescribir init.c.

## Carga inicial

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\run-poc.ps1 (el que editas).
2. C:\Users\guill\MCPTest\mcp-shot-test.ps1 (patrón PROBADO: copia la mission del install de DayZServer
   y luego sobrescribe init.c; su cliente SÍ llegó in-game — markerSeen=True).

## Cambios en run-poc.ps1 (solo orchestrator)

1. **Mission completa**: ANTES de escribir tu init.c, copia la mission chernarus COMPLETA a `$MissionWs`
   (recursivo) desde el install de DayZServer, p.ej.
   `C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions\dayzOffline.chernarusplus`
   (descubre la ruta en runtime; si DayZServer no está, usa la que use mcp-shot-test.ps1). DESPUÉS
   sobrescribe `$MissionWs\init.c` con tu init.c actual (el del override CreateCharacter + spawn fijo)
   y vuelve a escribir `dayz_mcp.json` en `$MissionWs`. (Espejo de mcp-shot-test.ps1, ~líneas 39-43.)
2. **Espera in-game más larga + gate real**: sube el wait a ~120s. Además del marker `spawn_actual`,
   confirma el player con un sondeo: encola `query_player_state` cada ~3s y NO corras la suite A1/A2/A3
   (ni hagas teardown del cliente) hasta que un resultado venga `ok=1` (player realmente in-game) o
   se agote el timeout. (Con la mission completa, CreateCharacter se llamará y spawn_actual saldrá.)
3. No cambies nada más (deploy `-packonly`, config `$mission:`+`$profile:`, launch, key, collector).

## Recoger evidencia

- Server RPT: ahora SÍ deben aparecer eventos de conexión + `CreateCharacter` + el marker
  `[MCP-POC] spawn_actual=...`. Pega esas líneas.
- Client RPT: que crezca y llegue in-game (no 26 líneas).
- poc-verdict.json + bridge log (`result posted ... ok=1`) + Python hits.

## Regla de decisión

- Player spawnea (ok=1) → A1/A2/A3 evaluables. A1 (pos vs POC_SPAWN <0.5m), A2 (ya probado:
  ticks_in_flight≥5), A3 (2 ids). Reporta los tres con números.
- Si AÚN no spawnea con mission completa (sin `CreateCharacter`/conexión en server RPT) → pega la
  sección de conexión del server RPT + el error MissionBase/PluginManagerInit y PARA. No improvises.

## Restricciones

1. NO toques el mod (`.c`) ni el transporte — probados. Solo `run-poc.ps1`.
2. NO añadas scope (A4/A5, MCP stdio, otras tools). Solo desbloquear A1/A2/A3.
3. NO hables de fallback client-first (el server-side ya funciona; esto es solo spawn de player).
4. R2 para cualquier API que toques. NO auto-review (R21 aparte). OneDrive: escritura atómica + verificar.

## Output (A/B/C/D)

A — run-poc.ps1 modificado (+ confirmación de que la mission tiene ya la estructura chernarus completa).
B — LITERAL: server RPT (conexión + CreateCharacter + spawn_actual); client RPT (llegó in-game);
    poc-verdict.json; A1/A2/A3 con números. C — Hallazgos. D — Handoff: si A1/A2/A3 PASS → cierre A4+A5.
===== PROMPT FIN =====
```

## Notas para Claude (receptor)
- Verificar que el server RPT ahora SÍ tiene `CreateCharacter`/`spawn_actual` (host-direct).
- A1 real: `state.pos` == POC_SPAWN (<0.5m). A2 ya demostrado (1786 ticks). Si PASS → cierre A4+A5, luego fase 1 arquitectura.
- Si el error MissionBase/PluginManagerInit persiste con mission completa, es otra cosa (mirar antes de tocar).
