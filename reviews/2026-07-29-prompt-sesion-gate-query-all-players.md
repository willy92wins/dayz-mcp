# Prompt de arranque — sesión DayZ_MCP: rebuild PBO + gate in-game agrupado (incluye `query_all_players`)

Generado 2026-07-29 al cerrar la sesión de implementación de GameMaster IG-1.
Pegar el bloque entre los marcadores en una sesión NUEVA de Claude (Cowork).

===== PROMPT INICIO =====

Sesión nueva de **DayZ_MCP**. Objetivo: **un solo rebuild del PBO `@DayZ_MCP` y un solo gate
in-game agrupado** (DZ-R5) que cubra los frentes Enforce pendientes MÁS un verbo nuevo que quedó
escrito y sin gate.

## Carga inicial mínima (en este orden, y nada más)

1. `C:\Users\guill\ObsidianVault\AI\00_System\workflow.md`
2. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\HANDOFF.md`
   — el bloque LIVE-STATE. Su «gate de arranque» y sus «invariantes CERRADAS» mandan.
3. `C:\Users\guill\ObsidianVault\AI\30_Sessions\2026-07-29-gamemaster-ig1-implementacion-cdefg.md`
   — solo para entender de dónde sale el verbo nuevo. NO implementes nada de GameMaster.
4. Skills a invocar cuando toque (no leer por adelantado): `dayz-test-ingame` (build+deploy+launch)
   y `dayz-mcp-verify` (conducir el bridge).

NO abras los planes de GameMaster, ni `plans/` de Fase 2, ni el Knowledge Pack.

## Estado de partida (verificado el 2026-07-29 — NO lo rehagas)

En el árbol de `DayZ_MCP` hay un **verbo nuevo ya escrito, revisado y SIN gate in-game**:
`query_all_players`. Devuelve el estado de TODOS los jugadores conectados (el
`query_player_state` existente solo devuelve `m_Players.Get(0)`).

Lo que ya está hecho y comprobado, no lo repitas:
- `MCPMessages.c`: clase nueva `MCPAllPlayer {uid, pos[3], health, in_vehicle}` + campo nuevo
  `ref array<ref MCPAllPlayer> players;` en `MCPResult`. `MCPPlayerState` **intacta** (la comparte
  `query_player_state`, no se amplió a propósito).
- `MCPBridge.c`: builder `BuildAllPlayers()` (recorre `GetGame().GetPlayers(m_Players)`, omite los
  `Man` sin identidad con `if (!ident) continue`, usa `GetPlainId()` / `GetPosition()` /
  `GetHealth01("","")` / `IsInTransport()`) + rama nueva en el dispatch. Array vacío = `ok=true`.
- `loopback.py`: `"query_all_players"` añadido a `SERVER_COMMANDS` (una línea).
- `server.py`: wrapper `query_all_players`, clon del de `query_player_state`.
- Test dirigido `tools\tests\test_daemon_query_all_players.py`: **5 tests OK**.
- Verificado por diff: **solo adiciones, cero líneas eliminadas** en los cuatro archivos.
- Verificado sin BOM: `MCPMessages.c` empieza por `63 6F 6E`, `MCPBridge.c` por `63 6C 61`.
  Llaves balanceadas antes y después (27→29 y 390→394). CRLF = 0.

Copia de seguridad de los cuatro archivos ORIGINALES (por si hay que revertir):
`%TEMP%\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\53bb8bae-3b0b-4d89-87c9-38e1bee0b4f6\scratchpad\ab_backup\`

**Lo único que le falta a ese verbo es el gate in-game**, que en Enforce **es** la verificación:
no hay compilador offline y ningún gate de escritorio caza un fallo de compilación del módulo.

## Qué hacer en esta sesión

Agrupa en UN rebuild + UNA sesión con la caja levantada (DZ-R5) los frentes Enforce pendientes
del LIVE-STATE (frente 1: **BUG-066 (c)** — `declared_slots` devuelve `[""]`; BUG-061 cadence;
`OnContact` owner-client; gate de captura con `MAX_MCP_OUTPUT_TOKENS=75000`) **más** el gate del
verbo nuevo. Un solo ciclo de rebuild para todo.

### Orden obligatorio (importa: protege a los demás proyectos)

El PBO `@DayZ_MCP` es **infraestructura compartida** — lo consumen LFHeli, SUB_BRZ, A6_SR2M y
todos los gates in-game. Un error en `MCPMessages.c` no rompe solo el verbo nuevo: tumba el
módulo Enforce entero y deja sin instrumento a todas esas líneas. Por eso:

0. `session_status` primero. Si otra sesión tiene la caja (el LIVE-STATE del 29-jul de madrugada
   anotaba un run `@LFHeli_OH1` vivo), es **intocable**: espera o coordina, no mates procesos.
   Adquiere el lease antes de mutar y libéralo al terminar.
1. **Restore point del PBO ACTUAL antes de rebuildear**: copia el `.pbo` desplegado con su
   **SHA-256** y su tamaño (el del 28-jul son 131.843 B), nombrado por contenido. Si el nuevo
   rompe algo, revertir tiene que ser inmediato y verificable por hash, no por fecha (LL-095).
2. Rebuild + deploy del PBO.
3. **Arrancar el servidor y confirmar compilación limpia del módulo** ANTES de montar ningún
   escenario. Si aparece `CParser: quoted string not closed on line 1`, el culpable es un BOM
   (`EF BB BF`) y no una comilla — pero los tres primeros bytes ya se verificaron sin BOM, así
   que si sale, mira qué otro fichero se tocó.
4. **Smoke de REGRESIÓN de los verbos VIEJOS antes que el nuevo**: como mínimo
   `query_player_state`, `world_spawn`, `object_delete`. Cuesta un minuto y es lo que protege a
   los otros proyectos. Si esto falla, revierte al restore point del paso 1 y para.
5. Recién entonces, el gate del **verbo nuevo**:
   - **0 jugadores conectados** → `ok=true` y `players=[]` (array vacío es ÉXITO, no error).
   - **≥1 jugador con identidad** → una entrada por jugador, con `uid` estable (Steam64 crudo),
     `pos` de 3 componentes, `health` en **0..1** (el consumidor lo multiplica por 100) e
     `in_vehicle` correcto.
   - **2 clientes, uno dentro de un vehículo** → dos entradas, `pos` ±1 m de lo real,
     `in_vehicle` verdadero solo en el que va en coche.
   - Un `Man` sin identidad (p. ej. durante el connect) queda **omitido** del array, no emite
     `uid` vacío.
   - Comprueba de paso qué emite el JSON para `players` en las respuestas de los **otros** verbos
     (donde el campo queda null). `MCPResult` ya tiene 7 campos `ref` null en la mayoría de
     respuestas y se serializa con `JsonSerializer.WriteToString`
     (`MCPBridge.c:2619-2621`), así que no debería haber sorpresa — pero mírala una vez.
6. Los demás frentes del frente 1 (BUG-066 (c), etc.) en la misma sesión con la caja levantada.
7. `session_status` antes de cerrar; documenta cualquier cierre degradado.

## Cosas ya cerradas — NO re-litigar

- El diseño del verbo: clase **nueva** `MCPAllPlayer` en vez de ampliar `MCPPlayerState`;
  identity-less **omitidos**; `health` emitido en 0..1; array vacío = éxito. Decidido en el Grill B
  de GameMaster IG-1 y ya implementado.
- No usar `exec_enforce` como alternativa al verbo: viene deshabilitado por defecto, con allowlist
  y auditoría, y abrirlo para un sidecar en bucle sería cambiar un añadido auditable por un
  agujero permanente.
- La suite del daemon tiene **4 módulos no deterministas** ya documentados en el LIVE-STATE
  (`test_bug046_startup_deadlock`, `test_task7_review_regressions`,
  `test_bug046_audit_fault_recovery`, `test_client_mode`). No los persigas y no uses «todo verde»
  como criterio: gate dirigido.

## Prohibido en esta sesión

- Abrir **trabajo de plataforma** (D-33), Fase 2, el fondo de BUG-067, Fases 3-6, el frente del
  Knowledge Pack o `vehicle_trace`. El verbo nuevo es trabajo de **instrumento** (bridge Enforce),
  que sí está en el frente 1; no lo confundas con plataforma.
- Tocar `session_coordination.py` (BUG-046).
- Matar procesos DayZ a mano: usa el lifecycle guard.
- Implementar nada de GameMaster: ese proyecto queda **congelado** hasta que este gate cierre.

## Al terminar

1. Actualiza el LIVE-STATE de `DayZ_MCP_dev\HANDOFF.md` con el resultado del gate y el SHA/tamaño
   del PBO nuevo.
2. Handoff de sesión en `AI\30_Sessions\`.
3. Avisa de que **GameMaster IG-1 queda desbloqueado**: su siguiente paso es R21 doble revisión +
   skill `rigorous-data-audit` + su propio gate in-game (7 escenarios, `DayZServer_x64` **release**,
   GM-D-005), con el estado descrito en
   `AI\10_Projects\GameMaster\HANDOFF.md`.

===== PROMPT FIN =====
