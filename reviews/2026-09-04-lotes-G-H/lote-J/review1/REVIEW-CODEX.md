## VEREDICTO

NO ES SEGURO INTEGRAR en ronda 1: P-J1 queda implementado de forma coherente, pero P-J2 tiene un fallo ALTO reproducible que puede conceder el lease, cortar la respuesta HTTP y dejar una reserva de mutación huérfana.

## HALLAZGOS

### ALTA — Una excepción de `adopt_run` corta la concesión ya comprometida y deja lease + reserva activos

**Ubicación:** `../ws/tools/dayz_mcp/loopback.py:3155`, llamado después de que `coordination.acquire`/`wait` ya devolvió la concesión en `../ws/tools/dayz_mcp/loopback.py:3208-3210,3265-3268`. La reserva se crea en `../ws/tools/dayz_mcp/process_lifecycle.py:2491-2500`; la segunda lectura que reproduce el fallo está en `../ws/tools/dayz_mcp/process_lifecycle.py:2538-2542`.

**Diff literal [EXACT]:**

```diff
+        run_id = getattr(idle[0], "run_id", None)
+        result = lifecycle.adopt_run(client, token, run_id)
+        if not isinstance(result, dict):
+            payload["adopted_run"] = {
+                "ok": False,
+                "run_id": run_id,
+                "error": "adopt_failed",
+            }
...
+        if action in {"acquire", "wait"} and status == 200:
+            payload = self._adopt_on_grant(client, payload)
+
         payload = self._persist_coordination(payload)
```

No hay frontera de excepción alrededor de `lifecycle.adopt_run`. Además, `adopt_run` autoriza y abre una reserva antes de entrar en `_operation_lock`; si luego una dependencia lanza, no pasa por `_reject_reserved`, `_commit_reserved` ni `abort_reservation` (`../ws/tools/dayz_mcp/process_lifecycle.py:2491-2500`; API de aborto real en `../ws/tools/dayz_mcp/session_coordination.py:1373-1411`).

**Fallo concreto:** con un único run `RUNNING_IDLE` sin dueño, `/session/acquire` concede el lease. La primera `manifest.list_runs()` del helper selecciona el run; si la segunda, dentro de `adopt_run`, lanza `OSError`, el handler termina con una excepción y el cliente recibe `RemoteDisconnected`, no el `200` con `lease_token` y `adopted_run:{ok:false,...}` exigido por P-J2. Después del corte, `/session/status` muestra todavía `owner.state="active"` y un `lease_id`; internamente queda también una autorización pendiente. Otro cliente queda fuera de la caja aunque el titular nunca recibió el token con el que liberarla. La misma ruta afecta a `/session/wait` cuando llega el turno.

**Repro ejecutable [EXACT]: sí.** Ejecutado desde `../ws/tools`:

```powershell
$src = @'
from tests.test_daemon import DaemonHttpServer, _config, _http, IDENTITY
srv = DaemonHttpServer(_config(), adopt_fixture=False); srv.start()
real = srv.state.lifecycle.manifest.list_runs; calls = 0
def flaky():
    global calls
    calls += 1
    if calls == 2: raise OSError("manifest second read failed")
    return real()
srv.state.lifecycle.manifest.list_runs = flaky
try:
    _http(srv.base, "POST", "/session/acquire", srv.key,
          {"identity": IDENTITY, "purpose": "drive"})
except Exception as exc:
    print("acquire_exception", type(exc).__name__)
finally:
    srv.state.lifecycle.manifest.list_runs = real
active = srv.state.coordination._active
print("active", active is not None,
      "pending_authorizations", len(active.pending_authorizations) if active else None)
srv.stop()
'@
$src | & 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -
```

Salida observada, tras el traceback del handler:

```text
acquire_exception RemoteDisconnected
active True pending_authorizations 1
```

**Fix sugerido:** hacer `ProcessLifecycle.adopt_run` exception-safe después de `_authorize`: toda salida inesperada anterior al commit debe cerrar la reserva mediante `abort_reservation` o rechazo equivalente. Después, envolver la llamada en `_adopt_on_grant` y devolver el lease ya concedido con `adopted_run:{ok:false,run_id,error:"adopt_failed"}`. Añadir tests HTTP para `acquire` y `wait` que inyecten una excepción después de autorizar y comprueben simultáneamente: respuesta 200 con token, error declarado y cero reservas pendientes.

### MEDIA — Un manifiesto ilegible se declara como “no había run”, ocultando el fallo de adopción

**Ubicación:** `../ws/tools/dayz_mcp/loopback.py:3134-3146`.

**Diff literal [EXACT]:**

```diff
+        try:
+            idle = [
+                run
+                for run in manifest.list_runs()
+                if getattr(run, "state", None) == "RUNNING_IDLE"
+                and getattr(run, "owner_session_id", None) is None
+            ]
+        except Exception:
+            payload["adopted_run"] = None
+            return payload
+        if not idle:
+            payload["adopted_run"] = None
+            return payload
```

Las dos situaciones distintas —lectura fallida y lectura válida sin candidato— producen exactamente `adopted_run:null`. Eso contradice el contrato de P-J2: `null` significa que no había run adoptable; un intento fallido debe ser `{ok:false,run_id,error[,hint]}`.

**Fallo concreto:** si `manifest.list_runs()` lanza `OSError("manifest unreadable")`, `/session/acquire` responde `200 {status:"active", adopted_run:null}`. El llamador no puede distinguir un sistema sin run de un estado que no pudo inspeccionarse y puede continuar por el flujo equivocado.

**Repro ejecutable [EXACT]: sí.** Mismo entorno, desde `../ws/tools`:

```powershell
$src = @'
from tests.test_daemon import DaemonHttpServer, _config, _http, IDENTITY
srv = DaemonHttpServer(_config(), adopt_fixture=False); srv.start()
real = srv.state.lifecycle.manifest.list_runs
srv.state.lifecycle.manifest.list_runs = lambda: (_ for _ in ()).throw(OSError("manifest unreadable"))
status, body = _http(srv.base, "POST", "/session/acquire", srv.key,
                     {"identity": IDENTITY, "purpose": "drive"})
srv.state.lifecycle.manifest.list_runs = real
print(status, body.get("status"), "adopted_run", body.get("adopted_run"))
srv.stop()
'@
$src | & 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -
```

Salida observada:

```text
200 active adopted_run None
```

**Fix sugerido:** en esa rama devolver `adopted_run:{ok:false,run_id:null,error:"run_state_unavailable"}` —error ya existente en `../ws/tools/dayz_mcp/loopback.py:1317-1319`— y reservar `null` exclusivamente para una enumeración válida sin candidatos.

### BAJA — El nombre de un test afirma ahora lo contrario de lo que comprueba

**Ubicación:** `../ws/tools/tests/test_process_lifecycle.py:2564-2611`.

**Diff literal [EXACT]:**

```diff
 def test_internal_cleanup_enqueued_before_release_is_gone_when_idle_published(
...
-        self.assertEqual(seen.get("queue"), [])
+        queued = seen.get("queue") or []
+        self.assertEqual([command.get("cmd") for command in queued], ["vehicle_release"])
...
-        self.assertEqual(poll[1].get("commands"), [])
+        self.assertEqual(
+            [command.get("cmd") for command in poll[1].get("commands", [])],
+            ["vehicle_release"],
+        )
```

**Fallo concreto:** al seleccionar o interpretar el test por nombre, un mantenedor entiende que la limpieza debe desaparecer al publicar `RUNNING_IDLE`, mientras la aserción nueva exige que sobreviva y se entregue. Es degradación de mantenibilidad, no fallo de runtime.

**Repro ejecutable:** no; evidencia estática directa. **Fix sugerido:** renombrarlo para expresar `survives_when_idle_published` o equivalente, sin tocar su lógica.

### Controles revisados sin hallazgo

- **Integridad del producto:** los SHA-256 de `loopback.py`, `process_lifecycle.py` y los cuatro tests entregados coinciden exactamente con los seis valores del brief; `product_frozen.sh` confirma además los 61 ficheros sellados y los dos del write-set.
- **P-J1 / superficie de entrada:** el predicado de exención es exclusivamente pertenencia del id a `_fire_and_forget_ids` (`loopback.py:1154-1157`); los ids nacen monotónicos junto con el set vacío (`:885,921`). El único productor de producto con `internal=True` es `cleanup_owner`, y siempre encola `vehicle_release` (`:2693-2744`). `/enqueue` no propaga `internal` y no encontré una vía para que `exec_enforce` obtenga la marca. Reiniciar el daemon pierde cola y set a la vez; retirar un binding descarta toda su cola con `binding_retired` (`:1113-1137`), por lo que no sobrevive una marca sin su binding.
- **P-J1 / mismo predicado y actividad:** `_drain_run_locked` conserva por `_command_exempt_from_fence` (`:1159-1184`) y ambos snapshots del poll usan el mismo método (`:2222-2229,2249-2258`). `_enqueue_run_rejection` preserva `run_state_unavailable`, `run_not_owned` y `binding_retired` (`:1287-1315`). El bypass final exige además `command_name == "vehicle_release"` (`:2292-2315`). El enqueue interno salta `_note_run_command_activity` (`:1835-1844`), el resultado F&F se elimina sin registrarlo (`:2567-2586`) y `_bound_last_poll_at` sólo alimenta el estado del peer (`:2171,2904`); no encontré vía que saque el run de `RUNNING_IDLE` ni que rebaje el tombstone.
- **P-J1 / carrera:** append + alta en `_fire_and_forget_ids` están bajo el mismo `RLock` que poll y drenaje (`:2727-2742`), por lo que no existe ventana observable entre ambos pasos ni doble entrega. `cleanup_begin` llama secuencialmente primero a `cleanup_owner` y después a `begin_release_owner` (`../ws/tools/dayz_mcp/daemon.py:77-108`); no hay otro productor de `cleanup_owner` en producto. No reproduje una limpieza encolada después de persistir `RUNNING_IDLE`.
- **P-J2 / ramas y locks:** la adopción automática sólo se invoca para `acquire`/`wait` con HTTP 200 (`loopback.py:3265-3266`), no para status, heartbeat ni enqueue. Se ejecuta después de retornar del coordinador. Guard caído, `retail_quarantine`, procesos ausentes y un run activo adicional vuelven como rechazo explícito normal de `adopt_run`; dos idle producen `multiple_idle_runs`. El único hueco encontrado es la frontera de excepción de los dos hallazgos anteriores.
- **P-J2 / identidad y lanzador:** la idempotencia exige simultáneamente estado `RUNNING`, misma sesión y mismo `lease_id` (`process_lifecycle.py:2508-2535`); otra sesión, otro lease o lease caducado no entra. La rama hace audit, commit y finish. Los flujos kill y client conservan su `adopt` explícito (`dayz_test_worker.py:526-546,589-606`), que ahora es no-op válido; `stop_run` y `start_run(existing)` siguen viendo el mismo dueño. `mode=server` continúa chocando con `active_run_exists` si hay un run vivo.
- **Transporte de `adopted_run`:** `_remember_acquire_or_wait` sólo valida status y credencial (`control_client.py:238-262`), `_validate_request_bound_wait_response` no rechaza campos extra (`:264-276`) y `_with_own_identity` copia todo el diccionario (`:278-285`). La clave llega íntegra al tool.
- **Regresiones de tests:** no se borró ningún `test_*`: los seis ficheros pasan de 421 a 433 tests (+12); `test_instance_fence.py` y `test_client_mode.py` mantienen 64 y 53. No cambió ningún marcador `skip`/`expectedFailure` en el write-set. Los cuatro módulos modificados dan 330/330; los dos congelados, 117/117. Los tests positivos nuevos fallan contra `../ws-frozen-r0/tools/`; el único nuevo que también pasa en baseline es el control negativo `test_adopt_from_another_session_stays_rejected`. El oráculo completo discrimina: baseline 4/13 frente a producto 13/13; JA2/JA4/JA5/JB7 son los cuatro controles negativos ya verdes indicados en el brief. Las lecturas privadas de `_bound_queues`/`_fire_and_forget_ids` aíslan deliberadamente el predicado de drenaje, y están respaldadas por el E2E público; no las considero defecto ni prueba tautológica.

## ASERCIONES CAMBIADAS

| Test | Antes | Después | P-item |
|---|---|---|---|
| `tests.test_session_e2e.SessionE2ETest.test_vehicle_release_cleanup_is_enqueued_exactly_once` | Tras release, el peer debía conservar sólo `vehicle_control`, incluso después de 0,1 s. | Espera hasta observar exactamente `vehicle_control, vehicle_release`. | P-J1 |
| `tests.test_process_lifecycle.ProcessLifecycleTest.test_internal_cleanup_enqueued_before_release_is_gone_when_idle_published` | El id no se marcaba F&F; cola y poll debían quedar vacíos tras publicar idle. | Marca el id F&F; cola y poll deben conservar/entregar `vehicle_release`. | P-J1 |

No hay otras aserciones preexistentes modificadas en los cuatro tests del write-set; el resto son 12 tests nuevos. Los tests congelados `test_instance_fence.py` y `test_client_mode.py` no cambiaron y no contienen una aserción preexistente que exija `RUNNING_IDLE` después de adquirir por HTTP.

## BACKLOG

Ningún defecto adicional fuera del alcance del lote. La política preexistente de manifiesto corrupto detectado durante el arranque mantiene la caja no reclamable y encola el acquire hasta reparar (`../ws/tools/tests/test_daemon.py:1514-1576`); no la confundí con el fallo de lectura durante la adopción posterior a una concesión, que sí pertenece a P-J2.

## NO VERIFICADO

- No ejecuté DayZDiag ni un peer real de juego; entrega, guard y lanzador se verificaron con los fakes/fixtures del repositorio.
- No pude demostrar si `gate/allowed_red.txt` fue tocado: `../ws-frozen-r0` sólo contiene `tools/` y no aporta una copia baseline del ledger. El fichero actual contiene exactamente los dos ids esperados, pero eso no prueba su historial.
- La suite completa no obtuvo veredicto semántico en este sandbox: terminó con errores ambientales de autoridad de ruta / launcher nativo y `psutil.AccessDenied`, no atribuibles al diff. Por eso no afirmo “suite completa verde”; sí quedan verdes los 447 tests focalizados indicados arriba.
- Gates ejecutados desde `../ws`, última línea literal:

  - `bash gate/product_frozen.sh` → `PRODUCTO-CONGELADO OK (61 ficheros sellados + 2 del write-set)`
  - `bash gate/run_oracle_tests.sh` → `ORACULO-TESTS-VERDE`
  - `bash gate/suite_full.sh` → `SUITE-COMPLETA ROJA`
