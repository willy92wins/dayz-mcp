# Cierres menores del buzón — recibo (sesión local_32a70c44, 2026-09-04 tarde)

Decisión de Guillermo (picker 14:55): cerrar 344d, 0fde y 251d con la propuesta por defecto. 5930 (nueva, 12:32Z)
se trata aquí en sus puntos 1, 2 y 4; su punto 3 es anexo de f70b/92e5 y va con el lote J de la sesión par.

## 344d — deriva 1.2.3/1.2.4 en LFPowerGrid: YA CORREGIDA antes de esta sesión
- Commit `2e8ef7d` (2026-08-29 13:12, repo `LFPowerGrid`, rama `sorter/v4-finish`): `scripts/3_Game/LFPG_Defines.c`
  1 línea, `LFPG_VERSION_STR = "1.2.4"`. Medido hoy: `config.cpp:208 version = "1.2.4"` y `LFPG_Defines.c:527 = "1.2.4"`.
- Gate recongelado en 1.2.4: `LFPowerGrid_dev/tests/test_version_gate.py:16 EXPECTED = '1.2.4'`, y `version_gate()` de
  `LFPowerGrid_dev/tools/build_guarded.py` exige `["1.2.4", "1.2.4"]`. Ejecutado hoy:
  `python LFPowerGrid_dev/tests/test_version_gate.py` → `OK - version gate accepts only 1.2.4/1.2.4 and blocks every
  negative before build`, rc=0.
- NO commiteado por esta sesión: `tools/build_guarded.py` (+43, el gate) y `tests/test_version_gate.py` (untracked) siguen
  en el árbol de trabajo de `LFPowerGrid_dev` (26 entradas en vuelo de otra sesión; LL-353: no se absorbe trabajo ajeno).
- Colateral de la ficha (suites `authority` / `server_script_boundary` UNKNOWN por entorno) no tratado.

## 0fde — hook promotion-gate.ps1 vs CONTRIBUTING.md: la contradicción se resolvió por el lado del documento
- `DayZ-Modding-Knowledge-Pack` commit `94a6da3` (2026-09-04 00:42, «fix the editable-direction contradiction»):
  CONTRIBUTING.md punto 7 dice ahora lo que decía el hook: el repo del Pack es la fuente editable; el árbol vivo y los
  plugins son TARGETS (`PROMOTION-TARGET-UNEXPLAINED`); «a document without a gate loses to a gate».
- Lo que le faltaba al hook era el paso que hace ejecutable la receta: resellar `sources/source-map.json`
  (`output_hash` de cada fichero tocado; `packctl validate` da `SOURCE-HASH-MISMATCH` hasta entonces —
  `packctl/validation.py:438`). No hay subcomando `reseal` en packctl (cli.py: validate/build/gate/api-index/eval/promote);
  el propio 94a6da3 reselló 7 hashes a mano.
- Commit `b5add82` en `~/.claude` (`hooks/promotion-gate.ps1`, +5 −3; parse PowerShell 0 errores; solo esa ruta).
- Memoria host `skill-edits-delivered-as-package.md` corregida con sección fechada (decía lo contrario desde el 08-31).

## 251d y 5930 — lote B (commit `adc1c22` en work/inbox-20260830-modules; Codex: BLOQUEANTES=0 (review B, gpt-5.6-sol effort high: mutante default 200->0 rojo, wire 60 tools, 118 tests OK; dictamen en reviews/2026-09-04-cierres-menores/lote-B/REVIEW-CODEX-B.md))
- 251d: el default `lookback_lines=200` ya estaba restaurado (`server.py` execute_wait_for) y la frase explicativa vive en las
  instructions del servidor («lookback_lines=200 includes the last N lines already on disk…»); los dos brazos tenían test por
  separado. Añadido `test_same_bytes_default_sees_and_zero_misses` (tests/test_wait_for.py, clase WaitForBug086EvidenceTest):
  un solo fichero, sha256 igual entre las dos llamadas, default → satisfied, lookback 0 → timed_out con probes ≥ 1.
  El segundo defecto de la ficha (`tool_profile` default "agent" que quitaba siete tools) no existe hoy en server.py (0 hits).
- 5930 puntos 1, 2 y 4: descripciones de `entities_query` (pos.y no se ajusta a la superficie; pasar surface_query.y; el
  delator es nearest_player_m), `logs_since` (el marker avanza solo hasta las líneas DEVUELTAS, nunca a EOF; receta para
  marcar «ahora»; la sonda escribe en script_<fecha>.log, no en el RPT) y `capture_screenshot` (rects en píxeles FÍSICOS,
  DPI-aware; un helper no DPI-aware ve coordenadas virtualizadas). Punto 3 (el lease caduca por inactividad y devuelve el run
  a RUNNING_IDLE) → anexo de f70b/92e5, enviado a la sesión par (lote J) el 2026-09-04 15:20.
- Verificación: suite completa en el árbol vivo con el lote: `Ran 2539 tests … FAILED (failures=2, skipped=6)`, los 2 rojos
  son el centinela de MCPBridge.c (bd90), mismos que la línea base; suites focales 119 OK; wire por sesión en memoria (Codex).

## b2c4 — lote C (commit `7670703`; Codex: BLOQUEANTES=0 en R3 (gpt-5.6-sol effort high; R1 B-01 clase plana → decisión por verbo, R2 B-02 mutante superviviente → negativo con eco cargado, R3 verde; mutantes a-d rojos; dictámenes en reviews/2026-09-04-cierres-menores/lote-C/))
- Capa Python: `_bridge_error` (server.py) conserva el código como cabeza del mensaje y añade, SOLO cuando el resultado los trae,
  `handler`/`user_id`/`clicked` y el eco `ui_request` (`requested_path/root/text`, `matched_path`), p. ej.
  `not_handled; handler='LFPG_SorterView_TEST' user_id=506 clicked=False; requested_path='BtnCloseX' matched_path='…'`.
  Un `handler=''` significa que ningún handler corrió; uno con nombre corrió y declinó. Resultados sin esas claves siguen
  dando el código pelado (`timeout`, `bad_pos`, `widget_not_found`): los mensajes pineados por la suite y la igualdad de
  `game_not_ready:reason=server_poll_stale` en wait_for no cambian. `object_id` sigue viajando como atributo.
- El bridge Enforce ya rellenaba esos campos antes de decidir el error (MCPClientBridge.c:1465-1480) y crea el eco al empezar
  cada verbo UI (:2199-2218; MCPMessages.c:415-419, :476-479): el dato se perdía en el transporte Python, como decía la ficha.
- Tests: `tests/test_ui_error_diagnostics.py` (3 unitarios + wire por sesión MCP en memoria con la propiedad `state`
  sustituida, recorriendo call_bridge → wait_for_result → _bridge_error) y `tests/test_lote_b_products.py` (las frases nuevas
  de las cuatro descripciones llegan por `list_tools`; backlog de Codex B). Descripción pública de `ui_click` anuncia el formato.
- Queda el gate in-game (ver un `not_handled` real con `handler` y `user_id` en el texto) en la sesión con Guillermo, junto a
  f4f2/2762 (mismo `ui_click` sobre `BtnCloseX` con y sin `root`).
- Rondas Codex: R1 B-01 (la clase plana `MCPResult` lleva siempre los escalares → decisión por verbo), R2 B-02 (el mutante
  «gate por verbo quitado» sobrevivía al negativo con eco vacío → negativo con eco permitido no vacío, `LOADED_TIMEOUT`),
  R3 verificación. Mutantes (a)-(d) medidos por el orquestador: los cuatro rojos, producción restaurada por sha
  (`lote-C/MUTANTES-C.txt`). Suite completa 16:17-16:21: `Ran 2550 … failures=3`: los 2 centinelas + un tercero
  TRANSITORIO (`test_read_key_delegates_to_the_pinned_reader`: la sesión par escribió loopback.py/process_lifecycle.py a las
  16:20:02 durante la corrida e `inspect.getsource` leyó el fichero nuevo con las líneas del módulo viejo; el módulo pasa
  solo a las 16:22, 4 OK).

## 6927 — lote D (commit `4e227a3`): ver `lote-D/RECIBO-D.md` (R9: tres auditorías + revisión Codex).
