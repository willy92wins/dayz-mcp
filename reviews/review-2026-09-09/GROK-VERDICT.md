Voy a leer el brief de la revisión adversarial y las reglas de proceso que apliquen, y seguiré ese encargo al pie de la letra, incluida la frontera de no escribir nada.El encargo es revisión adversarial de solo lectura: voy a contrastar el código, los tests y los informes sin escribir ni ejecutar nada.Voy a contrastar los diffs, los call-sites citados y los logs de rojo contra el código, sin ejecutar tests.Siguiente: contrastar el puente Enforce, las citas de 62e3/6157 y las líneas vanilla de los dos diagnósticos.Revisión adversarial, solo lectura. Nada de esto está verificado en el motor; ningún informe lo finge.

---

## Hallazgos

**SERIO** — `reviews/steamfastpath-2026-09-09/red.log:646` y el cuerpo de ERROR/FAIL. Los 43 ERROR contra BEFORE son todos `AttributeError` de superficie nueva, no fallos de comportamiento. Conteo: 43 ERROR, 7 FAIL. Los 43 son `SteamRemediationResult` sin `steam_restart_fallback` / `steam_pid_repair_reason`, o `WindowsSteamRemediationHost` sin `write_active_process_pid`. Los 7 FAIL sí son `AssertionError` de conducta (el código viejo no escribe el DWORD y, si el PID ya era el bueno, igual hace `-shutdown`/`-silent`; uno de los siete es el `tearDown` del mismo `test_windows_host_writes_only_pid`, porque el viejo llamó a `Popen`). Los negativos «nunca escribe un candidato malo» contra BEFORE no discriminan esa invariante: el viejo no escribe ningún PID. Cierra con un recuento de FAIL que fallen en `write_attempts == []` *y* en el motivo, o con asserts anteriores a los campos nuevos.

**SERIO** — `tools/tests/test_night0909_inventory_inspect.py` vs `reviews/night-2026-09-09/b256/red.txt`. De 7 tests, 5 pasan contra `MCPBridge.c.BEFORE`. Discriminan de verdad solo `test_inventory_snapshot_uses_same_resolved_target_before_success` y `test_requesting_inventory_preserves_existing_memory_point_behavior`. El de la tool pública usa un Runtime mockeado y pasaría igual con el puente viejo. `test_reused_reader_*` lee `PopulateTelemetryInventory`, que ya existía. Un test que pasa contra el código viejo no vale.

**MENOR** — `tools/dayz_mcp/steam_preflight.py:447-460`. La carrera residual que declara DIAGNOSIS existe: entre el segundo `target_failure()` y `SetValueEx` el PID puede morir o reutilizarse; el escritor no retiene handle ni `GetProcessTimes`. Está acotada, no cerrada. Post-check: `evaluate_steam_session` exige de nuevo existencia + basename `steam.exe`; un reuso no-Steam no se reporta `applied` y cae al ciclo viejo. El reuso que seguiría siendo `steam.exe` sí quedaría escrito. No hay test que inyecte reuso *entre* la última sonda y `writer()`. Cierra con un doble que cambie imagen/existencia dentro de `write_active_process_pid`.

**MENOR** — `tools/dayz_mcp/server.py:5000-5018`. `action_use` dice «Confirm with wait_for(condition=log_matches)» y, en el mismo párrafo, que `started:true` no acredita efecto. Para una continua, 3fc1 muestra que el input se puede acabar solo; alargar `wait_for` no recupera un efecto ya cancelado. Receta que un agente puede leer como contrato de verificación.

**MENOR** — `reviews/night-2026-09-09/3fc1/DIAGNOSIS.md` («cancelar inmediatamente»). `ProcessActionInputEnd` no corre en `UA_AM_PENDING` (`actionmanagerclient.c:67-68`); el `EndInput` efectivo es el `default` tras `Start` (`:99-101` cliente, `actionmanagerserver.c:287-291`). No es el mismo tick que `PerformActionStart`. El mecanismo sigue en pie; «inmediatamente» sobra.

**MENOR** — `tools/dayz_mcp/server.py:4212-4218`. `object_inspect` ahora puede devolver `telemetry` con `want=["inventory"]` (`MCPBridge.c:1520-1525`) y la descripción pública sigue siendo solo memory points / bounding_center. Es subpromesa (la frontera de `server.py` lo explica). No es el fallo que pedía el ataque 2.

---

## 1. Fast path de Steam — PASA

No escribe un PID sin snapshot estable, `ActiveUser` DWORD positivo, exactamente un `steam.exe` vivo, `process_exists` e imagen `steam.exe` (basename, casefold), y esas dos sondas otra vez justo antes de mutar. Varios `steam.exe` → `multiple_steam_processes`, sin elegir el más viejo. Cero, lista ilegible, imagen rara, registro que cambia → no escribe. `write_active_process_pid` (`:272-281`) abre la clave existente con `KEY_SET_VALUE` y solo `SetValueEx(..., "pid", REG_DWORD)`; no toca `ActiveUser` ni crea claves. PID ya igual → `already_correct`, sin write ni restart. Tras escribir, relee el snapshot `(target_pid, ActiveUser previo)` y exige `evaluate_steam_session` verde.

La carrera residual está **acotada** (doble sonda + verify; un DWORD no-Steam no queda como éxito) y **no minimizada a transacción**. Aceptable para firmar el fast path; no es atomicidad.

El consumidor MCP (`dayz_test_tool.py:1436`) no copia los campos nuevos; lo declaran fuera de alcance. `remediate` solo se llama si `evaluate` ya falló (`dayz_test_tool.py:1423`).

---

## 2. Contratos de tool — PASA

**62e3 `telemetry_read` (`server.py:3879-3931`).** Las afirmaciones del ANSWER aguantan contra fuente: dos modos y `bad_mode` en Python (`:3928-3929`); radio ≤ 50 y JSONL 64 / 4096 en el puente (`MCPBridge.c:17-21`, `:1867`, `:1927-1938`, `:2345`); `GetType` exacto, pos sin snap, cero → `ok`+`found=false`, varios → `ambiguous_fixture` (`:2276-2314`); inventario inmediato, tope 16 por array, conteos sin tope, `declared_slots` sin ese tope (`:2428-2506`); DTO `MCPMessages.c:241`; `$mission:dayz_mcp/` hoja, sin `/` `\` ni `..` (`:1889-1924`); JSONL desde el inicio, último `last_valid` del prefijo leído (`:2322-2396`); `seq` ≠ `MCP_FIXTURE_SEQ_UNSET` (`MCPMessages.c:3`). Peer servidor (`server.py:3931`). Cuerpo de la tool: sigue siendo validar y `call_bridge`. El tope 50 no está en Python (solo `<= 0` → `bad_radius`); el puente lo rechaza como `bad_args`. El contrato del sistema se cumple; el código de error no es el que un lector de la descripción de Python esperaría. No es sobrepromesa de capacidad.

**6157 `action_use` (`server.py:4999-5045`).** Peer `client` (`:5045`, mapa `:549`). `PerformActionStart` + target `component=-1` (`MCPClientBridge.c:1991`, `:2030`). `started=true` es solo `GetRunningAction() != null` justo después (`:2034-2043`). En MP no local, `ActionStart` puede dejar `UA_AM_PENDING` (`actionmanagerclient.c:658-660`) y el servidor vuelve a `Can` (`actionmanagerserver.c:142`). `OnExecuteClient` está en `AnimatedActionBase` (`animatedactionbase.c:179-199`), no se invoca desde la tool. La frase de `started` no acredita aceptación, callback ni fin. Correcta. El «incluye OnStartClient / OnExecuteClient» queda anulado en el mismo párrafo.

---

## 3. Inventario (b256) — PASA

Lado servidor (`DispatchObjectInspect` en `MCPBridge.c`). Una sola resolución (`:1503`); `object_id` desconocido/stale falla antes de leer (`:1504-1508`, `:1567-1584`). `want=inventory` reusa `PopulateTelemetryObject` sobre ese `match` (`:1520-1525`). `EntityAI.Cast` nulo → sin inventario, sin crash (`:2412-2416`). `GetInventory()` nulo → return (`:2438-2440`). Adjunto/cargo nulos se saltan. No es EntityAI exigida; un objeto sin inventario queda en ceros, como `telemetry_read`. No hay segundo scan ni fallback por posición si el ID falla. Read-only; `object_inspect` sigue fuera de lease (`session_coordination.py:35`). No hay verbo de mutación disfrazado.

El PBO no carga este árbol suelto; eso ya estaba declarado.

---

## 4. Tests — FALLA

La sospecha de los 43 ERROR se confirma: **43 AttributeError / 7 AssertionError de conducta**. Los 28 tests no son 28 discriminaciones de comportamiento. Los 7 FAIL (6 tests + un tearDown) sí prueban que el fast path es nuevo.

b256: **2/7** discriminan el call-site nuevo; **5/7** pasan contra BEFORE (`b256/red.txt`).

c82e contra BEFORE (2 FAIL / 37 ERROR, casi todo `TypeError: unexpected keyword argument 'entity'`) es el candidato sin aplicar; no lo cuento como fallo suyo.

---

## 5. Diagnósticos — PASA

**3fc1.** El mecanismo está en las líneas que cita: input nace inactivo (`actioninput.c:45`), `OnActionStart` no fabrica hold (`:217-219`), `WasEnded` es `!m_Active` (`:109`), `AIT_CONTINUOUS` lee `LocalHold`/`LocalHoldBegin` (`:125-134`), `InputsUpdate` llama `EndActionInput` si continuo y no se ignora (`actionmanagerclient.c:282-300`), el servidor latchea `INPUT_UDT_STANDARD_ACTION_INPUT_END` (`actionmanagerserver.c:88`) y `EndInput` (`:291`), la continua cancela el componente (`actioncontinuousbase.c:135-142`, `UserEndsAction` `:110-114`) y `CAContinuousTime.Cancel` no pasa por `OnCompletePogress` (`:58-70`). `[HIPOTESIS]` está bien puesta: el camino es de fuente; la corrida del ticket no tiene traza de `PENDING`/`ACCEPTED`/`WasEnded`/clase del mod. No tienen más evidencia causal de la que se atribuyen. No es salto de mecanismo; el «inmediatamente» sí (arriba). «Ninguna continua puede terminar por MCP» lo rechazan bien (quickbar, `WasEnded` override).

**49d0.** Es **conjunción de pruebas para el éxito**, no correlación: `cleanup_complete = state.active_zero and active_zero_completed` (`native_launcher_backend.py:1496-1498`). `active_zero` es mapa de debug vacío (`native_debug_state.py:66-67`); `EXIT_PROCESS` se retira en `complete_continue` (`:249`, `:275-297`). `active_zero_completed` es `JOB_OBJECT_MSG_ACTIVE_PROCESS_ZERO` con la key guardada en `job_completion_key` **antes** de `close_job` (`:1195`, `:238-241`). El error se emite si **falta cualquiera** de las dos (disyunción de fallos). No es el `build_failed` del worker (`dayz_test_worker.py:664`). La observación build=true/false es **correlación** con el camino extra de helpers (`:1392-1399`); ellos lo marcan hipótesis. El overwrite incondicional de `failure` en `:1498` está en el código; puede tapar `native_debug_gate_rejected`. `_wait_job_new_process` puede consumir el ZERO (`:1107-1108`) y dejar a `_wait_active_zero` sin mensaje: mecanismo real, causa de esa corrida hipótesis. Sin salto.

---

## c82e (candidato, sin aplicar)

El patch es **aplicable por hunks** sobre el `server.py` actual: `WAIT_FOR_CONDITIONS` y `execute_wait_for` no los tocaron 62e3/6157; el hunk de la descripción de `wait_for` sigue coincidiendo. No sustituir el fichero entero.

Correcto para el contrato cerrado: predicado cerrado, `telemetry_read`/`object_at` servidor, igualdad exacta, ausencia solo con `found=false`, datos malos abortan, lock suelto al dormir. Tipos con `type(...) is` antes de `_finite_float` (que sí traga `bool`). No finge miembros del sorter.

Al aplicar: `entity_state` no envuelve el `call_bridge` en el `try` de `players_*`; un `timeout waiting for telemetry_read` no se reescribe como el timeout de `wait_for`. Es coherente con «abortar», distinto del otro camino. Repetir los 10 tests contra el `server.py` ya integrado.

---

**Firma:** el fast path, los dos contratos y el call-site de inventario se pueden firmar. Los tests de Steam/b256 no se pueden citar como 28+7 discriminaciones. 3fc1/49d0 se pueden tomar como diagnóstico, no como causa de una corrida concreta.