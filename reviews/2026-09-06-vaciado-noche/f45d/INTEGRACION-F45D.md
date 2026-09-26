# f45d — recepción, revisión, integración y merge

Sesión «Vaciado de buzón MCP» (`local_32a70c44`), 2026-09-06 21:5x–22:5x. Diagnóstico y alcance: `DECISION-F45D.md`.

## Recepción (21:5x–22:0x)

- Lane: Cursor CLI, modelo verificado en el `init` del stream: «Cursor Grok 4.6 Extra High»
  (`cursor-grok-4.6-xhigh`), `runner_cursor.sh` con HOME de sandbox; RC=0 en 12 minutos.
- Write-set entregado: exactamente los tres ficheros del brief (`tools/dayz_mcp/server.py`,
  `tools/tests/test_wait_for.py`, `tools/tests/test_enqueue_refusal_reaches_the_caller.py` nuevo) más `STATE.md`.
  Hashes de STATE = hashes medidos aquí (`5641ec14…`, `571dba41…`, `6143cefa…`); 0 CRLF.
- Tests ejecutados por el receptor en la copia (`RUN-F45D.txt`): módulo nuevo 12 OK; `test_wait_for` 23 OK;
  regresión de diez módulos 337 OK. STATE (`STATE-F45D-grok.md`): rojo-primero 8 fallos de 35 con T5 verde de
  partida (como pedía el brief), verde final, censo de 25 códigos, tres mutantes con 6/5/2 rojos.

## Revisión Anthropic (22:0x–22:2x, `REVIEW-ANTHROPIC-F45D.md`)

Veredicto **SEGURO INTEGRAR**, sin BLOQUEA ni DEBE; cuatro PUEDE:

- **H1** `session_granting` (carrera de concesión, `session_coordination.py:3423` → `loopback.py:1591-1605`) también
  sale por `/enqueue` y también llegaba desnudo; invisible al censo AST por construcción (`decision.error` es una
  variable). Aplicado: entra en la lista blanca y en el censo, con un test de pin.
- **H2** `_carriable_hint` dejaba pasar `\t`, NUL y secuencias ANSI (la spec del brief sólo vetaba `\n`/`\r`).
  Aplicado: `hint.isprintable()`, con dos casos más en el test de hints malformados.
- **H3** la receta de la descripción de `wait_for` era cierta sólo con UN run ocioso sin dueño (`_adopt_on_grant`,
  `loopback.py:3199-3208`). Aplicado: la frase exacta del revisor (adopta el único run `RUNNING_IDLE` sin dueño y
  lo publica en `adopted_run`; con varios, `multiple_idle_runs` y no adopta).
- **H4** tres líneas largas (108/182/169) — ya re-envueltas por el integrador antes de la revisión
  (`REFLOW-F45D.patch`, sólo saltos de línea); los módulos se volvieron a correr sobre lo comiteado.

Los tres diffs se aplicaron literalmente a la propuesta del revisor (`apply_review_f45d.py` en el scratchpad), sin
segunda ronda de revisión: no introducen lógica nueva fuera de lo que el dictamen ya verificó.

## Integración y commit (22:3x)

- Worktree `wt-f45d`, rama `work/f45d-enqueue-refusal-reason` desde `4e264bb`. Tras H1–H3: nueve módulos en el
  worktree, **303 tests OK** (`test_enqueue_refusal_reaches_the_caller`, `test_wait_for`,
  `test_weak_agent_consumer_ux`, `test_client_mode`, `test_bad_args_messages`, `test_server_response_truth`,
  `test_session_e2e`, `test_loopback`, `test_box_occupancy`); ninguna línea añadida supera 100 caracteres.
- Commit `44de5ff` (3 ficheros, +304/−4), staged por rutas exactas y `git commit --only -F … -- <rutas>`.
  Parche final: `FINAL-F45D-44de5ff.patch`; mensaje: `COMMIT-F45D.txt`.

## Merge a la rama viva (22:4x)

- Por plumbing (`merge_f45d.py`: `merge-tree --write-tree` → `commit-tree` con dos padres → `update-ref` guardado por
  la punta vieja → `checkout HEAD -- <rutas>`), para no arrastrar el `decisions/decision-log.md` staged de la
  tercera sesión ni los dos ficheros del arnés que Reserva tiene modificados sin commitear.
- Punta viva antes: `d3f49dd` (Reserva, df53); después: **`8f5727f`** (padres `d3f49dd` + `44de5ff`), 3 ficheros;
  los tres bytes a bytes iguales a la rama (`074aacab…`, `571dba41…`, `6ae3cef2…`); rutas del merge limpias;
  staged sólo el `decision-log.md` ajeno.
- Coordinación: Reserva dio vía libre a las 22:1x tras terminar su propia suite (línea base `Ran 2912`, rojos = los
  dos centinelas bd90) y comitear `d3f49dd`; su ventana de daemon con presupuesto corto no afecta a la suite (la
  variable la lee el proceso del daemon, no el shell de la suite).

## Suite completa en la viva tras el merge

`Ran 2926 tests in 244.368s` → `FAILED (failures=2, skipped=6)` (RC=1); rojos: `test_full_source_hash_is_frozen`, `test_removing_only_marker_lines_restores_frozen_source_hash`. Los dos centinelas de bd90 (`test_full_source_hash_is_frozen`, `test_removing_only_marker_lines_restores_frozen_source_hash`) son la línea base conocida; cualquier otro rojo se anota arriba con su nombre. Log: `SUITE-post-merge.log` en esta carpeta. Intermitentes
conocidos de la misma familia (verdes en aislado, no deterministas en suite completa, sin contaminador identificado,
NUNCA a `allowed_red`), ninguno apareció en esta pasada: `FailedLaunchSettlementTest`
(`test_task7_rereview_regressions.py:571` y `:649`, ficha `fb-20260906-172844-30c1` de Reserva) y
`test_acquire_survives_adopt_run_manifest_get_failure` (`test_daemon.py:686`, ficha `fb-20260906-201106-7caf`).

## Lo que NO se verificó

- El caso medido de las 21:07 contra un proceso vivo: la corrección vive en el proceso cliente del MCP
  (`ClientRuntime`), así que sólo la ve una sesión MCP nueva. Repro para esa sesión, sin Steam ni caja larga:
  `dayz_test_run(mode="server", …)` → NO adoptar → `wait_for(players_at_least, 1, 60)` debe decir
  `run_not_owned: This run has no owner (RUNNING_IDLE). Adopt the existing run before dispatching.` en vez de
  `remote_error`; después `session_acquire_wait` (con `adopted_run` en la respuesta) y repetir → espera normal.
- La acreditación del transporte (procedencia del hint) está verificada por lectura, no ejecutando el daemon.
- `tests.test_process_lifecycle` no lo corrió el revisor (levanta procesos); la suite completa sí lo incluye.
