## VEREDICTO

NO ES SEGURO INTEGRAR este producto de ronda 2: el caso ordinario de F-01 quedó corregido, pero un fallo del propio cierre compensatorio deja una autorización pendiente y oculta la degradación del audit; F-02 todavía devuelve `null` ante una enumeración malformada. Ambos tienen repro ejecutable. Al ser ya ronda 2, el siguiente paso corresponde al orquestador según la regla de parada del ledger.

## HALLAZGOS

### MEDIA — F-01 incompleto: el fallo de `abort_reservation` se traga y la respuesta declara un cierre que no ocurrió

**Ubicación:** `../ws/tools/dayz_mcp/process_lifecycle.py:2595-2608`. El contrato real del cierre está en `../ws/tools/dayz_mcp/session_coordination.py:1373-1411`: `abort_reservation` elimina la entrada de `pending_authorizations` en `:1397`, incrementa la revisión en `:1398`, intenta publicar `decision="authorization_aborted"` en `:1399-1408` y devuelve degradaciones en `:1411`. La ruta de rechazo declarada usa otro evento (`authorization_rejected`) en `:1413-1476`.

**Diff literal [EXACT]:**

```diff
+        except Exception:
+            # Unexpected exit after _authorize opened a reservation and before
+            # commit: abort (not reject). abort_reservation is the coordinator
+            # contract for an operation that could not complete; reject is for
+            # declared command outcomes. Same pattern as stop_run's
+            # manifest.replace failure.
+            if not committed:
+                try:
+                    self.coordinator.abort_reservation(
+                        authority[0], authority[1], authority[2], "adopt_failed"
+                    )
+                except Exception:
+                    pass
+            return self._error("adopt_failed", 503)
```

El `except: pass` de `:2606-2607` contradice “toda salida inesperada cierra la reserva”: si el compensador lanza, se devuelve `adopt_failed` como si el cierre hubiese terminado, pero la autorización sigue viva. Además, aun cuando `abort_reservation` sí elimina la reserva pero devuelve `("audit_failed",)`, el resultado se ignora; a diferencia de `_reject_reserved` (`process_lifecycle.py:1449-1462`) y del precedente de `stop_run` (`:2277-2289`), la respuesta no incorpora `cleanup_degraded`. Por tanto, una pérdida del evento durable `authorization_aborted` queda declarada sólo como un fallo ordinario de adopción.

**Fallo concreto:** `manifest.get` lanza después de `_authorize`; el `abort_reservation` compensatorio también lanza. `/session/acquire` responde `200 active` con token y `adopted_run:{ok:false,error:"adopt_failed"}`, pero el lease mantiene una reserva pendiente. Un heartbeat no la limpia. Otro cliente queda en `202 queued` por el lease activo —esa salida no discrimina la reserva— y `/session/release` del dueño sí elimina el lease completo. Es degradación de coordinación/auditoría, no crash ni corrupción.

Salida observada:

```text
ABORT_FAIL acquire 200 active {'ok': False, 'run_id': 'test-run', 'error': 'adopt_failed'}
ABORT_FAIL pending 1
ABORT_FAIL heartbeat 200 active None
ABORT_FAIL second_acquire 202 queued None
ABORT_FAIL release 200 None None
ABORT_FAIL after_release_active False
```

**Repro ejecutable [EXACT]: sí.** Desde `../ws/tools`:

```powershell
$py='C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe'
$src=@'
from tests.test_daemon import DaemonHttpServer, _config, _http, IDENTITY, IDENTITY_B
srv=DaemonHttpServer(_config(), adopt_fixture=False); srv.start()
m=srv.state.lifecycle.manifest
real_get=m.get
real_abort=srv.state.coordination.abort_reservation
m.get=lambda _rid: (_ for _ in ()).throw(OSError("manifest get boom"))
srv.state.coordination.abort_reservation=lambda *_a,**_k: (_ for _ in ()).throw(OSError("abort boom"))
try:
    st,body=_http(srv.base,"POST","/session/acquire",srv.key,{"identity":IDENTITY,"purpose":"drive"})
finally:
    m.get=real_get
    srv.state.coordination.abort_reservation=real_abort
active=srv.state.coordination._active
print("acquire",st,body.get("status"),body.get("adopted_run"))
print("pending",len(active.pending_authorizations) if active else None)
tok=body["lease_token"]
print("heartbeat",_http(srv.base,"POST","/session/heartbeat",srv.key,{"identity":IDENTITY,"lease_token":tok})[0])
print("second_acquire",_http(srv.base,"POST","/session/acquire",srv.key,{"identity":IDENTITY_B,"purpose":"other"})[0])
print("release",_http(srv.base,"POST","/session/release",srv.key,{"identity":IDENTITY,"lease_token":tok})[0])
srv.stop()
'@
$src | & $py -
```

**Fix sugerido:** no silenciar el fallo del compensador. Consumir y propagar las degradaciones que devuelve `abort_reservation`; si éste lanza y la reserva sigue activa, intentar el cierre alternativo seguro del coordinador o revocar/liberar explícitamente el lease, y declarar una degradación distinta (`reservation_abort_failed`) en la respuesta. El test y JB10 deben inyectar también este segundo fallo y exigir cero reservas pendientes.

### MEDIA — F-02 incompleto: una fila sin `state` se interpreta como enumeración válida vacía

**Ubicación:** `../ws/tools/dayz_mcp/loopback.py:3136-3153`.

**Diff literal [EXACT]:**

```diff
+            try:
+                idle = [
+                    run
+                    for run in manifest.list_runs()
+                    if getattr(run, "state", None) == "RUNNING_IDLE"
+                    and getattr(run, "owner_session_id", None) is None
+                ]
+            except Exception:
+                payload["adopted_run"] = {
+                    "ok": False,
+                    "run_id": None,
+                    "error": _DURABLE_UNREADABLE,
+                }
+                return payload
+            if not idle:
+                payload["adopted_run"] = None
+                return payload
```

El `try` corrige el no-iterable (`None` produce `run_state_unavailable`), pero `getattr(..., None)` vuelve tolerante una fila que no cumple el contrato. `[object()]` no lanza: se filtra, `idle` queda vacío y `:3151-3153` publica `null`. Aquí la tolerancia sí borra la diferencia que F-02 debía preservar: “enumeré cero runs válidos” y “el enumerador devolvió basura” vuelven a ser la misma salida.

**Fallo concreto:** `list_runs() -> [object()]` seguido de `/session/acquire` produce `200 active adopted_run None`; el cliente interpreta “no había candidato” en vez de `run_state_unavailable`.

Salida observada:

```text
NON_ITERABLE 200 active {'ok': False, 'run_id': None, 'error': 'run_state_unavailable'}
MISSING_STATE 200 active None
```

**Repro ejecutable [EXACT]: sí.** Desde `../ws/tools`:

```powershell
$py='C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe'
$src=@'
from tests.test_daemon import DaemonHttpServer, _config, _http, IDENTITY
for label,value in (("NON_ITERABLE",None),("MISSING_STATE",[object()])):
    srv=DaemonHttpServer(_config(), adopt_fixture=False); srv.start()
    m=srv.state.lifecycle.manifest; real=m.list_runs; m.list_runs=lambda v=value:v
    try:
        st,body=_http(srv.base,"POST","/session/acquire",srv.key,{"identity":IDENTITY,"purpose":"drive"})
    finally:
        m.list_runs=real
    print(label,st,body.get("status"),body.get("adopted_run"))
    tok=body.get("lease_token")
    if tok: _http(srv.base,"POST","/session/release",srv.key,{"identity":IDENTITY,"lease_token":tok})
    srv.stop()
'@
$src | & $py -
```

**Fix sugerido:** materializar y validar las filas antes de filtrar. Cualquier elemento que no tenga el esquema mínimo (`state`, `owner_session_id`, `run_id`) debe llevar la enumeración completa a `run_state_unavailable`; `null` queda exclusivamente para una lista válida sin candidato.

### Verificado sin hallazgo adicional

- **F-01, camino ordinario:** una excepción de `manifest.get` ya devuelve el grant entero y deja `pending_authorizations=[]`. El mismo wrapper sirve a `acquire` y `wait` (`loopback.py:3279-3280`); el repro específico de `wait` dio `200 active`, `adopt_failed` y `pending 0`. La rama idempotente también está dentro de la frontera (`process_lifecycle.py:2510-2538`): con `_audit` lanzando devolvió `503 adopt_failed`, cero pendientes y el run siguió `RUNNING` con dueño A. `_persist_coordination` no propaga: captura su fallo en `loopback.py:3291-3312`; con `write_coordination` lanzando, el HTTP siguió en 200 y añadió `cleanup_degraded:["snapshot_failed"]`.
- **F-03:** el único cambio del test es el nombre en `../ws/tools/tests/test_process_lifecycle.py:2594`; el diff no cambia ninguna aserción. En `gate/` + `tools/`, la única referencia al nombre viejo está en el control negativo intencional JC1 (`../ws/gate/oracle_tests.py:642`). No hay selector ni otro test que dependa de él.
- **Integridad/regresión:** los cuatro tests declarados congelados son byte-idénticos a ronda 1. Los once métodos `test_*` añadidos en ronda 1 siguen presentes; el duodécimo cambio de pruebas de esa ronda era el test ahora renombrado y conserva su lógica. No apareció ningún decorador `skip`/`expectedFailure` en los dos módulos modificados. Los módulos modificados dieron `245/245`; los cuatro módulos congelados, `205/205`.
- **Discriminación de tests nuevos:** los tres tests conductuales añadidos en ronda 2 se ejecutaron contra `../ws-frozen-r1/tools/` y quedaron rojos (dos `FAIL`, un `ERROR RemoteDisconnected`); pasan en el producto actual. El test renombrado pasa también contra r1, como corresponde: no pretende acreditar código nuevo, sólo corrige F-03. No encontré un test conductual nuevo tautológico.

## ASERCIONES CAMBIADAS

| Test | Antes | Después | F-item |
|---|---|---|---|
| `test_acquire_list_runs_failure_declares_run_state_unavailable` (`test_daemon.py:613`) | No existía; contra r1: `FAIL`, `adopted_run is None`. | Nuevo: exige `200 active` y `{ok:false, run_id:null, error:run_state_unavailable}`. No cubre filas malformadas. | F-02 |
| `test_acquire_survives_adopt_run_manifest_get_failure` (`test_daemon.py:639`) | No existía; contra r1: `ERROR RemoteDisconnected`. | Nuevo: exige respuesta 200, error declarado, cero pendientes, release 200 y acquire posterior 200. No cubre el fallo del propio abort. | F-01 |
| `test_adopt_run_dependency_failure_after_authorize_closes_reservation` (`test_process_lifecycle.py:1703`) | No existía; contra r1: `FAIL` porque propaga `OSError`. | Nuevo: exige `adopt_failed/503`, cero pendientes y manifiesto intacto. No cubre el fallo del propio abort ni su degradación de audit. | F-01 |
| `test_internal_cleanup_enqueued_before_release_is_gone_when_idle_published` | Aserciones de supervivencia/entrega ya existentes; el nombre decía lo contrario. | Sólo renombrado a `..._survives_when_idle_published`; aserciones byte-idénticas. | F-03 |

No se modificó ninguna aserción preexistente fuera del renombrado sin lógica; las demás líneas de aserción del diff pertenecen a los tres tests nuevos.

## BACKLOG

- **COSMÉTICO, fuera del producto:** `../ws/gate/run_oracle_tests.sh:2` aún dice `Oracle of lote I round 2`; el script y el oráculo ejecutado son los del lote J. No afecta al veredicto de runtime.

## NO VERIFICADO

- No ejecuté DayZDiag ni un peer real; adopción, reserva, audit y transporte se verificaron con el servidor HTTP y los fixtures del repositorio.
- No demostré que el `RunManifestStore` concreto pueda producir por sí solo una fila sin `state`: su implementación devuelve clones de `RunRecord` (`process_lifecycle.py:740-742`). El repro de F-02 inyecta un colaborador malformado porque ésa es una entrada que el encargo pide clasificar; no la presento como corrupción observada en disco.
- No demostré un fallo espontáneo de `abort_reservation` en producción; el repro inyecta la excepción para verificar la garantía universal escrita por el delta. Sí reproduje por separado un audit que devuelve fallo: la reserva se cerró y la revisión avanzó, pero `adopt_run` omitió `cleanup_degraded:["audit_failed"]`.
- No verifiqué reinicio del daemon con la reserva compensatoria huérfana; comprobé el estado interno inmediato y las rutas públicas heartbeat/release/acquire posterior.
- La suite completa no obtuvo veredicto semántico en este sandbox. Los errores focales muestran las causas ambientales admitidas por el encargo: `Path.home().resolve(strict=True)` recibió `WinError 5` en el launcher nativo; la autoridad de paths devolvió `invalid_dayz_test_path_authority`; y el único `FAIL` aislado contenía `psutil.AccessDenied` al escanear otro `python.exe`. Ninguno de esos módulos pertenece al delta. Por eso no afirmo suite completa verde.
- Gates ejecutados desde `../ws`, última línea literal:

  - `bash gate/product_frozen.sh` → `PRODUCTO-CONGELADO OK (61 ficheros sellados + 2 del write-set)`
  - `bash gate/run_oracle_tests.sh` → `ORACULO-TESTS-VERDE`
  - `bash gate/suite_full.sh` → `SUITE-COMPLETA ROJA`
