# Recepción de la ronda 6 — 2026-09-04 04:2x

Todo lo de abajo lo midió el orquestador; nada se toma del STATE.md del implementador.

## Sello, write-set, hashes
- Sello `runs6/GATE-SEAL.txt`: **4/4 intactos** (GATES.md, oracle.py, run.sh, suite.sh).
- Write-set: 6 ficheros tocados desde STARTED, **0 fuera del allowlist** (process_lifecycle.py,
  loopback.py, test_box_occupancy.py, test_process_lifecycle.py, test_loopback.py, STATE.md).
- `process_lifecycle.py` SHA-256 `2195ca23cc57b011e856950f0bd7769ba99ba7ea0e5dc58fcf6dc85b3541ea04`
- `loopback.py` SHA-256 `52ee08f83cb79f59c8343f4ce24b8c792a3dd8f4404ef5c865c706674c8651cb`

## Diff LF-normalizado contra la entrega de la ronda 5 (`integ/tools`)
| fichero | + | − | CRLF en el entregado |
|---|---|---|---|
| process_lifecycle.py | 170 | 81 | **2964** (el editor de Grok reescribió el fichero entero a CRLF: normalizar a LF al integrar) |
| loopback.py | 30 | 0 | 0 |
| test_box_occupancy.py | 94 | 15 | 0 |
| test_process_lifecycle.py | 258 | 0 | 0 |
| test_loopback.py | 8 | 0 | 0 |
| **total** | **560** | **96** | |

Producción del lote entero desde el árbol base ≈ 270 (r1-r5) + 119 netas (r6).

## Gates corridos por el orquestador
```
run.sh:   ORACULO: PASS=29 FAIL=0 UNMET=0 de 29 · ORACULO-VERDE
suite.sh: test_box_occupancy 70 OK · test_process_lifecycle 98 OK · test_lifecycle_http 8 OK ·
          test_loopback 65 OK · test_mcp_tools 45 OK · test_session_coordination 46 OK
          SUITE-ACOTADA OK  (332 tests)
```

## Rojo-antes verificado por el orquestador (tests nuevos sobre el árbol de la ronda 5)
- `test_process_lifecycle`: **8/8 FAIL** — los seis caminos de P4 (`stop_run` desde STOPPING y
  desde EXITED, `begin_release_owner`, `repair_recovery_fault`, `repair_manifest_recovery`,
  `_reap_run_locked`, `admin_reconcile`) y P5 (`test_failed_extend_discards_activity_credited_in_the_confirm_window`).
- `test_loopback`: **1/1 FAIL** (`test_read_after_retire_run_is_binding_retired`).
- `test_box_occupancy`: el módulo no importa en el árbol viejo (`_derive_box` no existía); con
  ese import guardado en una copia temporal, **3/3 FAIL** (coherencia P1, high-water P3, filas sin
  caché P2) y la guarda de preservación `test_later_unbound_observation_still_publishes_unknown`
  verde antes y después, como declara el implementador.
Cuadra con el STATE (12 FAIL, 1 PASS de 13).

## Nota del implementador que hay que llevar a la revisión
N17 del oráculo no cablea `lifecycle.bindings` (mi `build()` no pasa `bindings`), así que la
retirada del binding en el lifecycle es invisible para ese check: lo que N17 mide es la segunda
capa, el rechazo en el enqueue cuando el run ya salió de RUNNING según el manifiesto
(`loopback._bound_run_has_left_running`). La primera capa (retirada antes de la transición
durable) la acreditan los 8 tests de arriba con rojo-antes. **Consecuencia de diseño a juzgar**:
cada enqueue consulta ahora el estado del run en el manifiesto (lookup en memoria bajo el lock del
store, sin E/S) — acoplamiento nuevo loopback→manifest en la ruta caliente.

## Defectos del instrumento en la recepción (los dos corregidos y re-probados)
- `recibir.py` lanzaba los gates con una ruta Windows absoluta → bash la trituraba
  (`C:Usersguill...`). Ruta relativa con `cwd=ws`.
- Y el `bash` que resuelve Python desde un subprocess es el de WSL (`System32\bash.exe`), no el
  de Git: para él `C:\...\python.exe` es «command not found». Ahora se usa
  `C:\Program Files\Git\bin\bash.exe` explícito. La prueba en seco no ejercitaba el paso 5;
  el primer uso real lo delató dos veces.

## Estado
Entrega recibida: **OK**. Pendiente: revisión Codex (`review6/`) y revisión Opus (`review6-opus/`)
ciegas entre sí → R9 (`r9_r6.workflow.js`) sobre el árbol final → suite completa en el repo →
integración por rutas exactas con normalización a LF.
