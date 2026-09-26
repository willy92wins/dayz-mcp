# Lote V — parches del orquestador

## Ronda 1 (entrega de Composer 2.5): ninguno

La entrega llegó completa a la primera: sello `gate/` 4/4; write-set respetado; oráculo
`PASS=27 FAIL=0 UNMET=0 de 27 — ORACULO-VERDE` (baseline PASS=10 FAIL=17); suite acotada (12 módulos)
`SUITE-ACOTADA OK`; `tests.test_lote_v_products` 5 OK. Diff LF-normalizado contra HEAD 00d4303:
+114 −22. Commit cd53f7c. El worker no pudo ejecutar ningún gate (hooks del host bloquean su shell,
igual que en M y W); todas las cifras son del orquestador.

## Ronda 2 (tras el dictamen Codex R1, BLOQUEANTES=2): tres parches del orquestador

- **B-01 → V2 evidencia positiva.** `_annotate_entities_reliability` (`server.py`, `players_raw`)
  conserva el valor crudo de `players`; `reason: "no_player_connected"` sólo si es una lista vacía. Un
  `ok: 1` sin lista / `None` / no-list queda `remote_unverified` sin `reason`. Test nuevo
  `test_ok_probe_without_players_list_claims_nothing` (tres sondas); oráculo
  `V2-ok-probe-without-list-claims-nothing`.
- **B-02 → V4 deadline único de verdad.** El sleep del bucle (`remaining_sleep`) se acota al tiempo
  restante y sale si no queda; con `timeout_s=0.1, poll_interval_s=0.5` la llamada dura ~0.1 s (antes
  0.508). Corrige también el caso preexistente (una sonda insatisfecha dormía el intervalo entero).
  Test nuevo `test_deadline_bounds_the_sleep`; oráculo `V4-deadline-bounds-the-sleep`.
- **Backlog aplicado**: comentario interno de `READY_REASONS` («Published ready.reason set…», ya no
  «Closed»).

Medido por el orquestador en el workspace tras los parches (sello ronda 2, oráculo b7130de8…):
`PASS=29 FAIL=0 UNMET=0 de 29 — ORACULO-VERDE`; `SUITE-ACOTADA OK`; `test_lote_v_products +
test_wait_for + test_wave_fixes_20260824`: `Ran 43 tests OK`. Delta de la ronda 2 contra cd53f7c:
+45 −6 (`review-V/DIFF-R2-delta.patch`).

## Ronda 3 (misma tanda, antes de lanzar Codex R2): la petición documental de la ficha 17 (7c88)

La sesión par cerró 7c88 y dejó abierta su petición documental («una línea en la descripción de
mode/run_id de dayz_test_run»). La prosa de la tool ya decía «client requires run_id; server|all
forbid run_id» (lote E/M); faltaba lo que la ficha pedía (que `mode=client` reengancha SOLO el
cliente conservando servidor y mundo) y las dos propiedades del schema seguían publicando «Mode» y
«Run Id». Parche del orquestador: `_describe_run_id_matrix(app, "dayz_test_run")` en `build_app`,
justo tras `_patch_mode_enum_from_authority`, con las constantes `RUN_ID_MATRIX_MODE_DESCRIPTION` /
`RUN_ID_MATRIX_RUN_ID_DESCRIPTION`; la prosa gana «mode=client requires run_id: it reattaches only the
client to a live run, preserving the server and the world state (no server reboot); mode=server|all
must NOT pass run_id». Test `RunIdMatrixDescriptionTest` (comprueba además que el enum de la autoridad
sobrevive al parche); oráculo V6 (tres checks; sello ronda 3, oracle d8ab5569…).

Medido por el orquestador tras la ronda 3: `PASS=32 FAIL=0 UNMET=0 de 32 — ORACULO-VERDE`;
`SUITE-ACOTADA OK`; `test_lote_v_products + test_wait_for + test_wave_fixes_20260824 +
test_effective_schema + test_mcp_tools`: `Ran 103 tests OK`. Delta acumulado de las rondas 2+3 contra
cd53f7c: +101 −7 (`review-V/DIFF-R2-delta.patch`); lote entero contra 00d4303: +335 −29
(`review-V/DIFF-V2.patch`).

Detalle de integración, no del producto: el README del árbol de trabajo va en CRLF por
`core.autocrlf` (HEAD en LF); `integrar.py` escribe LF y git lo normaliza igual. La ronda 3 se integró
con `--skip-guard` tras comprobar por sha que los bytes vivos eran exactamente los de la ronda 2.
