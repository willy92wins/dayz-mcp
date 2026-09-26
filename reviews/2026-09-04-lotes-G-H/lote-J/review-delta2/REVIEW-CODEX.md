## VEREDICTO

SÍ, ES SEGURO INTEGRAR este producto conforme al tope vinculante de la ronda 3: F-04 queda cerrada y los casos exigidos de F-05 pasan; queda un hallazgo MEDIA con repro sobre una variante adyacente de fila malformada, que por decisión del ledger va a BACKLOG y no abre ronda 4.

## HALLAZGOS

### MEDIA — F-06: la validación de F-05 sigue siendo parcial; dos filas inválidas se publican como `null`

**Ubicación:** `../ws/tools/dayz_mcp/loopback.py:3160-3173`. El contrato real de `RunRecord` está en `../ws/tools/dayz_mcp/process_lifecycle.py:478-482,518-543`: `owner_session_id` sólo puede ser `str | None` y `state` debe pertenecer a `RUN_STATES`.

**Diff literal [EXACT]:**

```diff
+                for run in rows:
+                    if not (
+                        hasattr(run, "state")
+                        and hasattr(run, "owner_session_id")
+                        and hasattr(run, "run_id")
+                        and isinstance(run.state, str)
+                        and isinstance(run.run_id, str)
+                    ):
+                        unreadable = True
+                        break
+                    if (
+                        run.state == "RUNNING_IDLE"
+                        and run.owner_session_id is None
+                    ):
+                        idle.append(run)
```

La comprobación añadida valida presencia y los tipos de `state`/`run_id`, pero no valida que `owner_session_id` sea `str | None` ni que el `state` string sea uno de los estados permitidos. Ambas filas se filtran como “no candidatas”; al quedar `idle` vacío, `loopback.py:3183-3185` publica `adopted_run: null`.

**Fallo concreto:**

- `list_runs() -> [row(state="RUNNING_IDLE", owner_session_id=7, run_id="x")]` produce `200 active adopted_run:null`.
- `list_runs() -> [row(state="ALIEN", owner_session_id=None, run_id="x")]` produce la misma salida.

Ninguna lista es válida según `RunRecord.validate()`, por lo que `null` todavía no queda reservado universalmente a una enumeración válida sin candidato. No hay pérdida de datos ni proceso muerto: es una clasificación diagnóstica incorrecta bajo un colaborador que viola su contrato. El `RunManifestStore` concreto valida al cargar, añadir, reemplazar y clonar (`process_lifecycle.py:515,518-543,711-712,740-742,749-766`), así que no demostré que pueda generar espontáneamente estas filas.

**Repro ejecutable [EXACT]: sí.** Ejecutado desde `../ws/tools`:

```powershell
$py='C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe'
$env:PYTHONPATH='.'; $env:PYTHONDONTWRITEBYTECODE='1'
@'
from types import SimpleNamespace
from tests.test_daemon import DaemonHttpServer, _config, _http, IDENTITY
rows = (
    ("OWNER_NONSTR", SimpleNamespace(state="RUNNING_IDLE", owner_session_id=7, run_id="x")),
    ("UNKNOWN_STATE", SimpleNamespace(state="ALIEN", owner_session_id=None, run_id="x")),
)
for label, row in rows:
    srv = DaemonHttpServer(_config(), adopt_fixture=False); srv.start()
    manifest = srv.state.lifecycle.manifest; real = manifest.list_runs
    manifest.list_runs = lambda row=row: [row]
    try:
        status, body = _http(srv.base, "POST", "/session/acquire", srv.key,
                             {"identity": IDENTITY, "purpose": "drive"})
    finally:
        manifest.list_runs = real
    print(label, status, body.get("status"), repr(body.get("adopted_run")))
    token = body.get("lease_token")
    if token:
        _http(srv.base, "POST", "/session/release", srv.key,
              {"identity": IDENTITY, "lease_token": token})
    srv.stop()
'@ | & $py -
```

Salida observada:

```text
OWNER_NONSTR 200 active None
UNKNOWN_STATE 200 active None
```

**Propuesta:** BACKLOG, no ronda 4. Completar la validación mínima de `_adopt_on_grant` con `owner_session_id is None or isinstance(owner_session_id, str)` y pertenencia de `state` al conjunto cerrado que valida `RunRecord.validate()`, con ambos negativos HTTP. Es la misma familia semántica de F-05, pero requiere inyectar un retorno que el productor concreto no emite y sólo degrada el diagnóstico.

### Comprobaciones solicitadas sin hallazgo

**F-04 [EXACT].** El delta de `process_lifecycle.py:2601-2627` recoge el retorno de `abort_reservation`, intenta `reject_reservation` si el aborto lanza y sólo declara `reservation_abort_failed` cuando también lanza el rechazo. `loopback.py:3138-3152,3216-3234` copia la bolsa no vacía a `adopted_run.cleanup_degraded`. Las firmas y efectos se verificaron en `session_coordination.py:1373-1411` (`abort_reservation -> tuple[str, ...]`, pop en `:1397`) y `:1413-1475` (`reject_reservation -> AuthorizationDecision`, pop en `:1444-1450`).

Salida de los cinco caminos ejecutados:

```text
ORDINARY_ABORT_WORKS status 200 adopted {'ok': False, 'run_id': 'test-run', 'error': 'adopt_failed'} pending 0
ABORT_DEGRADED status 200 adopted {'ok': False, 'run_id': 'test-run', 'error': 'adopt_failed', 'cleanup_degraded': ['audit_failed']} pending 0
ABORT_RAISES_REJECT_WORKS status 200 adopted {'ok': False, 'run_id': 'test-run', 'error': 'adopt_failed'} pending 0
BOTH_RAISE status 200 adopted {'ok': False, 'run_id': 'test-run', 'error': 'adopt_failed', 'cleanup_degraded': ['reservation_abort_failed']} pending 1
REJECT_DEGRADED 200 {'ok': False, 'run_id': 'test-run', 'error': 'adopt_failed', 'cleanup_degraded': ['audit_failed']} pending 0
```

No encontré una rama de este correctivo que afirme silenciosamente un cierre inexistente: si ambos cierres fallan, la reserva queda pendiente pero la respuesta lo declara. El camino ordinario de ronda 2 conserva `200 active`, `adopt_failed`, manifiesto intacto y cero pendientes.

**F-05, casos pedidos [EXACT].** `loopback.py:3155-3185` materializa la enumeración y la invalida completa antes de filtrar. Resultados ejecutados:

```text
EMPTY 200 None
VALID_NONCANDIDATE 200 None
MIXED_VALID_INVALID 200 {'ok': False, 'run_id': None, 'error': 'run_state_unavailable'}
MIXED_INVALID_VALID 200 {'ok': False, 'run_id': None, 'error': 'run_state_unavailable'}
RUN_ID_NONSTR 200 {'ok': False, 'run_id': None, 'error': 'run_state_unavailable'}
STATE_NONSTR 200 {'ok': False, 'run_id': None, 'error': 'run_state_unavailable'}
```

Por tanto, lista mixta, `run_id` no-string y `state` no-string cumplen el ledger. La reserva de `null` sólo falla en las dos variantes adicionales de F-06.

**Regresiones y no-tautología [EXACT].** Los únicos `.py` distintos entre `../ws-frozen-r2/tools/` y `../ws/tools/` son los cuatro declarados. `test_session_e2e.py`, `test_loopback.py`, `test_instance_fence.py` y `test_client_mode.py` son byte-idénticos a r2. El censo sube de 2471 a 2476 métodos `test_*`, exactamente cinco; no hay líneas añadidas/eliminadas con `skip` o `expectedFailure`. Los cinco tests nuevos pasan en el producto y, cargando esos mismos tests contra los módulos de `ws-frozen-r2`, fallan los cinco:

```text
R2_CONTROL ran 5 failures 5 errors 0 successful False
```

No hay test nuevo que también quede verde contra r2.

## ASERCIONES CAMBIADAS

No se modificó ninguna aserción preexistente. Todas las aserciones añadidas pertenecen a cinco tests nuevos:

| Test | Antes (motor `ws-frozen-r2`) | Después (`ws`) | F-item |
|---|---|---|---|
| `test_acquire_survives_adopt_run_when_abort_reservation_raises` (`test_daemon.py:689`) | FAIL: pendiente 1 y `cleanup_degraded=[]` | PASS: rechazo alternativo deja pendientes 0; admite declaración si el doble cierre falla | F-04 |
| `test_acquire_malformed_list_runs_row_declares_run_state_unavailable` (`test_daemon.py:741`) | FAIL: `adopted_run is None` | PASS: `run_state_unavailable` | F-05 |
| `test_adopt_run_abort_degradations_reach_cleanup_degraded` (`test_process_lifecycle.py:1733`) | FAIL: falta `audit_failed` | PASS: la degradación del aborto llega al resultado | F-04 |
| `test_adopt_run_abort_raise_closes_via_reject_reservation` (`test_process_lifecycle.py:1765`) | FAIL: una reserva pendiente | PASS: pendientes 0 | F-04 |
| `test_adopt_run_abort_and_reject_raise_declares_reservation_abort_failed` (`test_process_lifecycle.py:1798`) | FAIL: falta `reservation_abort_failed` | PASS: doble fallo declarado | F-04 |

## BACKLOG

- **F-06 · MEDIA · repro archivado arriba:** completar los tipos/valores del esquema mínimo de `_adopt_on_grant`. Disposición vinculante: integrar y llevar a backlog; no abre ronda 4.
- **COSMÉTICO, fuera del producto:** `../ws/gate/run_oracle_tests.sh:2` todavía dice `Oracle of lote I round 2`; el script ejecuta correctamente el oráculo del lote J.

## NO VERIFICADO

- No ejecuté DayZDiag ni un peer real de juego. La adopción, compensación y publicación HTTP se verificaron con el servidor y los fixtures del repositorio.
- No demostré que `RunManifestStore` produzca por sí mismo ninguna fila malformada; al contrario, su validación actual lo impide. F-06 es el comportamiento de la frontera defensiva ante un colaborador inyectado que viola el contrato.
- No provoqué fallos espontáneos del almacenamiento/audit del host: las combinaciones F-04 se verificaron por inyección controlada. Tampoco verifiqué la recuperación tras reiniciar con el doble cierre fallido; sí observé inmediatamente la reserva pendiente y la declaración `reservation_abort_failed`.
- La suite completa no obtuvo un veredicto semántico verde en este sandbox. La muestra reproducida separa las causas ambientales admitidas: `Path.home().resolve(strict=True)` falla con `WinError 5` en el launcher nativo; la autoridad de rutas devuelve `invalid_dayz_test_path_authority`; y la carrera de daemon falla al encontrar `psutil.AccessDenied` sobre otro `python.exe`. Ninguno de esos módulos pertenece al delta, y `product_frozen.sh` confirma que están sellados.
- Tres gates ejecutados desde `../ws`; última línea literal de cada uno:

  - `bash gate/product_frozen.sh` → `PRODUCTO-CONGELADO OK (61 ficheros sellados + 2 del write-set)`
  - `bash gate/run_oracle_tests.sh` → `ORACULO-TESTS-VERDE`
  - `bash gate/suite_full.sh` → `SUITE-COMPLETA ROJA`
