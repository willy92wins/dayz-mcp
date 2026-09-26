---
date: 2026-07-25
project: DayZ_MCP
topic: stale-client-credential-recovery
status: APPROVED
approval: user-2026-07-25
implementation: complete
---

# Plan aprobado — Recuperación definitiva de credencial MCP obsoleta

> **Gate aprobado 2026-07-25:** el usuario aprobó H12, la neutralidad de la
> capa manteniendo H2/H5 entre generaciones, la telemetría INFO sanitizada y la
> implementación RED→GREEN.

## 1. Resultado buscado

Un proceso `dayz_mcp --client` ya vivo debe sobrevivir a una recreación,
rotación o restart autorizado del daemon/keyfile. Si su primer request recibe
401 de un daemon acreditado, debe revalidar exactamente la misma autoridad,
releer de forma segura exactamente el mismo keyfile y reintentar el mismo
request una sola vez. No se reinicia la tarea, no se mata ningún proceso y no se
modifica ownership de sesión/lifecycle.

## 2. Causa raíz aprobable

- `[EXACT]` El bridge guarda A en `ClientRuntime.self.key` al construir el
  runtime: `tools/dayz_mcp/server.py:294-353`.
- `[EXACT]` El control guarda otra A en `ControlClient.self._key`:
  `tools/dayz_mcp/control_client.py:94-123`.
- `[EXACT]` Ambos reutilizan esas strings:
  `tools/dayz_mcp/server.py:557-591`;
  `tools/dayz_mcp/control_client.py:125-173`.
- `[EXACT]` El doctor, en cambio, relee B en cada ejecución:
  `tools/dayz_mcp/doctor.py:125-152`.
- `[EXACT]` El daemon sólo produce 401 antes de dispatch/body/side effects:
  `tools/dayz_mcp/loopback.py:1025-1127`.
- `[EXACT]` La acreditación de socket/owner ocurre antes de emitir la key:
  `tools/dayz_mcp/accredited_daemon_transport.py:204-298`.

La reproducción A→B aislada obtuvo:

- control: un intento, `unauthorized`, credencial no vigente;
- bridge: un intento, 401, credencial no vigente;
- doctor: PASS usando la vigente;
- POST 401: cero mutación de cola.

Research completo:
`C:/Users/guill/ObsidianVault/AI/10_Projects/DayZ_MCP/research/2026-07-25-stale-client-credential-codex.md`.

## 3. Gate DPF

El plan vigente de vehicle-trace traza a G3 y no cubre este fallo:
`plans/2026-07-25-vehicle-trace-atomic-instrumentation.md`.

A5 tampoco es suficiente: su sujeto es el lazo del mod cuando cae/vuelve el
servidor, no un cliente MCP Python vivo: `product-spec.md:35-45`.

H10 garantiza acreditación pre-request, no continuidad de credencial:
`product-spec.md:128-138`.

### Criterio propuesto

`[DESIGN]` Añadir, previa aprobación, el siguiente criterio al grupo H:

> **H12 — Continuidad de credencial de clientes vivos.** Ante un HTTP 401 del
> daemon acreditado por H10, todo cliente MCP vivo revalida sin drift la misma
> policy/provenance/keyfile, relee la credencial con el lector endurecido y
> reintenta el mismo request como máximo una vez dentro del deadline original.
> Un segundo 401, una fuente no acreditada o cualquier fallo de revalidación
> falla cerrado con códigos sanitizados. El mecanismo es común a bridge,
> sesión, lifecycle y discovery; es thread-safe, no altera identidad,
> operation_id, lease, ticket, run ni daemon_generation, no mata/spawnea ante un
> listener no acreditado y no expone secretos. Doctor distingue recuperación de
> cliente obsoleto, desincronización daemon-keyfile y daemon no acreditado.

## 4. Alternativas consideradas

### A. Tarea/subagente nuevos

**Descartada como fix.** La observación local demuestra que ambos crean otro
proceso MCP y por eso leen B. Desbloquean, pero el proceso anterior sigue roto.

### B. Releer el keyfile antes de cada request

**No recomendada.** Elimina la caché larga, pero añade I/O y validación a cada
poll/heartbeat, amplía la ventana de fallo durante un replace y no elimina la
carrera “leer A justo antes de que el daemon cambie a B”; seguiría necesitando
el retry 401.

### C. Watcher de mtime/file-id

**Descartada.** Un watcher puede perder eventos, mtime no es autoridad y agrega
otro thread/lifecycle. El 401 acreditado es el discriminador real y ya está en
el camino de request.

### D. Reiniciar automáticamente clientes/daemon

**Descartada.** Puede perder contexto stdio, disparar competencia de procesos y
no cumple “sesión viva”. Un 401 no autoriza matar ni reemplazar procesos.

### E. Credencial compartida recargable + retry único

**Recomendada.** Conserva el camino rápido actual, usa el 401 como señal,
centraliza las dos copias y permite una política single-flight con límites
exactos.

## 5. Diseño recomendado

### 5.1 Una sola fuente de credencial por `ClientRuntime`

- `[DESIGN]` Crear un componente interno
  `RefreshingDaemonCredential` en
  `tools/dayz_mcp/daemon_credential.py`.
- Mantendrá un snapshot `(valor secreto, epoch interno)` y un
  `threading.Lock`.
- El valor secreto no tendrá `repr`, serialización ni logging. El epoch será un
  entero local no derivado de la key.
- Se construirá con el `AccreditedDaemonPolicy` normal ya acreditado y leerá el
  keyfile mediante `[EXACT] read_pinned_keyfile(path)`:
  `tools/dayz_mcp/pinned_keyfile.py:159-222`.
- `ClientRuntime` creará una sola instancia y la inyectará a `ControlClient`.
  El bridge y control dejarán de mantener `self.key`/`self._key` independientes.
- Un `ControlClient` usado fuera de `ClientRuntime` creará su propio provider,
  manteniendo su uso standalone.
- `[EXACT]` La CLI productiva no rellena `ServerConfig.key`; sólo pasa
  `keyfile`: `tools/dayz_mcp/server.py:1368-1384`. Para conservar fixtures
  existentes, `[DESIGN]` un `config.key` explícito podrá servir únicamente como
  snapshot inicial inyectado; la autoridad de refresh seguirá siendo
  `policy.keyfile`, nunca ese valor ni una fuente alternativa.

### 5.2 Algoritmo de request

`[DESIGN]` Para cada request:

1. Calcular un solo deadline absoluto.
2. Conservar los checks normales actuales del caller y acreditar siempre el
   socket contra la autoridad original. No añadir una nueva lectura de configs
   al fast path bridge.
3. Capturar snapshot `(A, epoch=N)`.
4. Ejecutar el request una vez por el transporte acreditado H10.
5. Si el status no es 401, devolverlo sin refresh.
6. Si es 401, entrar al lock de refresh:
   - si otro thread ya avanzó el epoch, reutilizar su snapshot;
   - si el epoch sigue en N, revalidar policy, releer el mismo path con
     `read_pinned_keyfile`, revalidar otra vez y publicar atómicamente el nuevo
     snapshot;
   - si path/provenance/policy no coincide, no publicar y no reintentar HTTP;
   - si la lectura falla, conservar el snapshot anterior para que otra llamada
     futura pueda recuperarse.
7. Reenviar una sola vez el mismo method/path/query/body/headers bajo el mismo
   deadline, añadiendo únicamente una cabecera diagnóstica constante y no
   sensible.
8. No recurrir. Un segundo 401 se convierte en error estable.

El lock no se mantiene durante I/O HTTP. Sólo serializa
revalidación+read+publicación. Cada caller realiza como máximo dos requests.

### 5.3 Matriz de clasificación

| Evento observado | Refresh | Retry HTTP | Error/resultado propuesto |
|---|---:|---:|---|
| Respuesta distinta de 401 | no | no | comportamiento actual |
| 401, policy exacta, key cambia, retry 2xx/4xx no-401 | sí | 1 | devolver respuesta; registrar recovery sanitizado |
| 401, policy exacta, segundo 401, key había cambiado | sí | 1 | `stale_client_credential_retry_rejected` |
| 401, policy exacta, key no cambió, segundo 401 | sí | 1 | `stale_client_credential_retry_rejected` |
| 401, keyfile ilegible temporalmente | intento fallido | 0 | `stale_client_credential_refresh_failed` |
| policy/path/provenance drift | no | 0 | `credential_source_untrusted` |
| socket owner no acreditado | no | 0 | `daemon_identity_unverified` |
| pre-request refused, sin listener | no | spawn existente acotado | recovery normal del daemon |
| retry tras 401 falla en transporte | ya hecho | no más | `stale_client_credential_retry_transport_failed` |

Los códigos son `[DESIGN]`; se fijarán como strings exactas en los tests RED
antes de implementación.

### 5.4 Cubre todas las tools y discovery

- `[EXACT]` Bridge y status usan `ClientRuntime._call`:
  `tools/dayz_mcp/server.py:595-695`.
- `[EXACT]` Sesión y lifecycle entran por `ControlClient`:
  `tools/dayz_mcp/server.py:413-495`.
- `[EXACT]` El health probe actual usa también la key cacheada y colapsa 401 a
  “no saludable”:
  `tools/dayz_mcp/server.py:502-542`;
  `tools/dayz_mcp/orphan_guard.py:709-759`.

`[DESIGN]` El provider común se usará también en discovery/health mediante un
resultado clasificado dentro de `ClientRuntime`; la semántica de reclaim de
`orphan_guard` queda intacta. El cliente dejará de confundir:

- connection-refused acreditable → auto-spawn normal, una vez;
- 401 de daemon acreditado → refresh y retry, sin spawn;
- listener/owner no acreditado → error fail-closed, cero spawn;
- segundo 401 → error de credencial, cero spawn adicional.

Esto es imprescindible para down→up con rotación: arreglar sólo las tools deja
al health loop usando A.

### 5.5 Neutralidad de leases, tickets y runs

El retry se implementará debajo de `_session_call` y debajo de `_call`, sobre
los bytes ya construidos. No volverá a invocar operaciones públicas.

Por tanto se conserva exactamente:

- la misma identidad serializada;
- el mismo `operation_id`;
- el mismo lease/ticket;
- el mismo command body;
- el mismo owner y run.

`[EXACT]` El daemon rechaza auth antes de leer el body:
`tools/dayz_mcp/loopback.py:1047-1127`. El primer 401 no puede haber creado,
adoptado, liberado ni encolado nada.

La capa de credencial no llamará a `_clear_matching_lease`,
`_clear_matching_ticket`, reconcile, adopt, release, lifecycle stop ni kill.

**Límite contractual a aprobar:** un restart real del daemon seguirá aplicando
H2/H5 y puede invalidar leases/tickets de la generación anterior. Este plan
garantiza que el refresh no añade pérdida ni adopción; no cambia la semántica de
generaciones.

### 5.6 Concurrencia

`[DESIGN]` Carrera esperada A→B:

1. T1 y T2 capturan epoch N/A.
2. Ambos reciben 401.
3. T1 entra al lock, valida, lee B y publica N+1.
4. T2 entra después, ve N+1 y no vuelve a leer/publicar.
5. Ambos reintentan una vez con B.

Si ocurre B→C antes de un retry, ese caller puede terminar en el segundo 401 y
fallar estable; la siguiente tool call podrá refrescar C. Nunca hay loop ni
rollback de epoch.

### 5.7 Doctor y observabilidad sin secretos

Un doctor externo no puede inspeccionar directamente una string cacheada dentro
de otro proceso. Comparar edad del proceso con mtime del keyfile daría falsos
positivos después de una recuperación, por lo que se descarta.

`[DESIGN]` Propuesta:

- El retry llevará `X-DayZ-MCP-Credential-Retry: 1`, constante, sin key,
  identidad, token, path ni hash derivado.
- `ServerState` conservará sólo un contador saturado y la edad monotónica de la
  última recuperación autenticada; sin IDs y sin persistencia. El evento
  tendrá un TTL fijo y acotado para que doctor no repita un finding histórico
  indefinidamente.
- Un retry autenticado que entra correctamente registra `recovered`.
- Un retry que vuelve a 401 no genera telemetría autoritativa en el daemon,
  porque esa petición no está autenticada. El error estable del cliente es la
  fuente diagnóstica.
- `/status` añadirá un objeto sanitizado de telemetría. Doctor producirá:
  - `STALE_CLIENT_CREDENTIAL_RECOVERED`, severidad INFO;
  - `DAEMON_CREDENTIAL_DESYNCHRONIZED`, FAIL, cuando el propio doctor relee la
    key vigente y el daemon acreditado devuelve 401;
  - la identidad no acreditada seguirá separada y no se llamará stale.
- No se emitirá un warning por request ni se incluirán valores de la cabecera en
  logs.

La telemetría desaparece al reiniciar el daemon; no es autoridad ni formato
durable. El error de la tool sigue siendo la fuente inmediata para un retry
persistente.

## 6. Tests RED → GREEN

Los tests se escribirán primero y deberán fallar contra el código actual.

### V1 — Rotación autorizada recuperable

`[DESIGN]` Fixture temporal:

- cliente/provider arranca con A;
- mismo keyfile/provenance pasa a B;
- request A recibe 401;
- una revalidación + una lectura + retry B;
- resultado 200/PASS;
- exactamente dos requests, sin secreto en output.

Se repetirá para una ruta de `ControlClient` y una de bridge usando el mismo
provider.

### V2 — 401 persistente

- El transporte devuelve 401 dos veces.
- Exactamente dos requests.
- Cero tercero aunque el caller público tenga lazy-spawn.
- Error estable según la matriz.

### V3 — Fuente no acreditada

Casos separados:

- drift de policy/provenance;
- keyfile path distinto;
- reparse/hardlink/invalid file;
- daemon identity no acreditada.

En todos: el request inicial que ya devolvió 401 puede existir, pero después del
discriminador hay cero retry HTTP, cero spawn y cero lectura de path alternativo.

### V4 — Rotación concurrente

- Barrera de N threads con snapshot A.
- Un solo publish/epoch advance.
- Cada caller ≤2 requests.
- Ningún deadlock, loop o downgrade A después de B.
- Shake repetido.
- `repr`, exceptions, logs, doctor JSON y telemetry no contienen ninguna de las
  credenciales fixture ni identidad fixture.

### V5 — State neutrality

- Preconfigurar `active_operation_id`, ticket y lease.
- Capturar bytes de los dos intentos y exigir igualdad exacta salvo key/cabecera
  de retry.
- Afirmar estado local idéntico antes/después.
- Usar un `ServerState`/coordinator aislado para probar que el 401 no cambia
  owner, queue, lease, ticket, pending commands ni run.
- Repetir un caso con daemon_generation nueva para demostrar que el provider no
  adopta ni reconcilia.

### V6 — Regresión

Focal mínimo:

- `tests.test_control_client`
- `tests.test_client_mode`
- `tests.test_client_runtime_control_composition`
- `tests.test_doctor`
- `tests.test_daemon`
- `tests.test_accredited_daemon_transport`
- `tests.test_pinned_keyfile`
- `tests.test_security_runtime_audit`

Baseline fresco pre-diff y post-diff. El baseline focal de research fue 175
tests OK, 1 skip, sin `security_runtime_audit`.

Global:

- ejecutar `python -m unittest discover -s tests -t . -v`;
- comparar nominalmente con un baseline tomado justo antes del diff;
- cero fallo/error nuevo;
- no atribuir ni arreglar dentro de este scope los rojos legacy registrados en
  `validation-matrix.md:30`.

### V7 — Cliente vivo sin restart

`[DESIGN]` Añadir un E2E de proceso aislado:

1. `TemporaryDirectory`, puerto efímero, runtime/state/config temporales.
2. Arrancar daemon fixture acreditable con A y HOME/configs aislados, siguiendo
   el patrón multiproceso ya existente en
   `tools/tests/test_bug046_startup_deadlock.py:67-95,706-770`.
3. Arrancar un único proceso cliente MCP y realizar status.
4. Parar limpiamente sólo el daemon fixture, recrear su keyfile temporal con B y
   arrancar su reemplazo acreditado en el mismo puerto/provenance.
5. Volver a llamar desde el mismo proceso cliente.
6. Exigir PASS y misma creación de proceso cliente; no reiniciarlo.
7. Shutdown limpio por APIs/context managers del fixture; no kill, no `:8765`,
   no key real, no DayZ.

### Tests adicionales necesarios

- Down→up con B dentro de `_ensure_daemon`: refresh, healthy, cero spawn extra.
- Foreign listener: `daemon_identity_unverified`, cero refresh/spawn.
- Deadline único: initial+refresh+retry no duplica el presupuesto.
- Daemon/keyfile desincronizados: código distinto de stale client.
- Daemon viejo ignora cabecera nueva; cliente nuevo sigue recuperando.
- Doctor reconoce telemetry sanitizada y nunca incluye material sensible.

## 7. Archivos que cambiarían

### Código

- `[DESIGN]` `tools/dayz_mcp/daemon_credential.py` — provider y retry común.
- `tools/dayz_mcp/server.py` — una sola instancia, bridge y health.
- `tools/dayz_mcp/control_client.py` — provider inyectable, sin `_key` propia.
- `tools/dayz_mcp/orphan_guard.py` — sin cambio productivo; sólo regresión para
  demostrar que reclaim conserva su semántica.
- `tools/dayz_mcp/loopback.py` — telemetry efímera y cabecera de retry.
- `tools/dayz_mcp/doctor.py` — errores/finding codes específicos.

### Tests

- `[DESIGN]` `tools/tests/test_daemon_credential.py` — unitarios RED principales.
- `tools/tests/test_control_client.py`
- `tools/tests/test_client_mode.py`
- `tools/tests/test_client_runtime_control_composition.py`
- `tools/tests/test_daemon.py`
- `tools/tests/test_doctor.py`
- `tools/tests/test_accredited_daemon_transport.py`
- `tools/tests/test_pinned_keyfile.py`
- `tools/tests/test_security_runtime_audit.py`
- `[DESIGN]` `tools/tests/test_client_credential_rotation_e2e.py` o harness
  equivalente aislado.

### Contrato y memoria

- `product-spec.md` — H12, sólo tras aprobación.
- Este plan pasaría de `PROPOSED` a `APPROVED`.
- Memoria Obsidian: `verified-apis.md`, `validation-matrix.md`, `bug-ledger.md`,
  `decision-log.md` y handoff de sesión al cerrar implementación.

## 8. Seguridad

- Nunca se imprime, hashea para output ni serializa la credencial.
- El snapshot secreto debe tener `repr` redacted y tests de no filtración.
- Sólo se relee el path ya sellado por la policy; no se acepta path de una
  respuesta, env var, request o tool argument.
- Revalidación antes y después de leer.
- Identity/provenance failure nunca se convierte en “prueba con otra key”.
- Sólo 401 del daemon ya acreditado habilita refresh.
- Exactamente un retry; sin recursión.
- Un segundo 401 no dispara spawn/restart/kill.
- La cabecera de diagnóstico no concede autoridad.
- Contadores de rechazo saturados/rate-limited para evitar spam/DoS de logs.
- No se tocan APIs de kill, stop, adopt, release ni reconcile.

## 9. Compatibilidad

No cambia el formato del keyfile, DB, JSONL, leases, tickets, runs ni body de
las operaciones.

Sí añade una cabecera HTTP constante en el segundo intento y campos opcionales
en `/status`.

- Cliente nuevo + daemon viejo: el daemon ignora la cabecera; recovery funcional,
  sin telemetry.
- Daemon nuevo + cliente viejo: comportamiento viejo; una tarea ya cargada con
  código viejo no puede hot-patchearse.
- Doctor viejo + daemon nuevo: ignora campos extra.
- Doctor nuevo + daemon viejo: ausencia de telemetry no es finding.
- Rollback con daemon que ya emitió telemetry: estado sólo RAM; desaparece al
  reiniciar, sin migración.

## 10. Plan de implementación

1. Aprobación de H12, semántica de leases across generation y telemetry
   recibida el 2026-07-25.
2. Tomar hashes/baseline fresco porque el plan vehicle-trace solapa archivos.
3. Crear bundle de rollback pre-cambio; este árbol no es un repo Git.
4. Escribir V1-V7 RED y fijar strings/schema de error.
5. Implementar provider común mínimo.
6. Integrar control y bridge debajo de las operaciones públicas.
7. Integrar health/discovery con tri-state y bloqueo de spawn no acreditado.
8. Añadir telemetry/doctor sanitizados.
9. Ejecutar focal, shake concurrente, E2E aislado y global delta.
10. Revisión independiente del diff: seguridad, retries, state ownership,
    redacción, compatibilidad y scope.
11. Actualizar memoria/handoff con evidencia y rollback probado.

## 11. Rollback

Como `DayZ_MCP_dev` no está bajo Git:

1. Antes de editar, guardar SHA-256 y copia host-direct de cada target en un
   directorio de rollback timestamped dentro del proyecto.
2. El rollback restaura sólo los archivos de este plan tras verificar path
   absoluto y hashes.
3. Reejecutar focal y comparar con baseline pre-diff.
4. No tocar keyfile, daemon compartido, runtime state, leases, runs ni procesos.

No hay migración de datos que deshacer. Tras rollback, clientes con el código
viejo volverían a requerir restart ante futuras rotaciones; los estados
daemon/lifecycle existentes permanecen intactos.

## 12. Decisiones aprobadas

1. H12 y el diseño de provider+retry único: aprobados.
2. “No perder leases/tickets/runs” significa neutralidad de esta capa,
   manteniendo la invalidación H2/H5 cuando cambia la `daemon_generation`.
3. Telemetry efímera INFO sanitizada y cabecera constante: aprobadas.
4. El delta concurrente se preservó por SHA-256 y se restauró el baseline
   prechange antes de empezar RED→GREEN.

## 13. Resultado de implementación

Estado: **COMPLETE — 2026-07-25**.

- Provider único integrado debajo de bridge, health y todas las operaciones de
  control.
- Autoridad original y request envelope fijados antes del primer envío.
- Un solo refresh single-flight y un solo retry bajo el deadline original.
- Segundo 401, source drift, fallo de refresh/transporte e identidad no
  acreditada devuelven códigos cerrados y nunca habilitan `spawn`.
- Telemetría RAM-only sanitizada y doctor compatible con daemon viejo.
- Lease/run/operation neutrality verificada con fixtures aislados.
- Focal: **290 tests OK, 1 skip**.
- Concurrencia: **25/25**.
- E2E: **3/3**, misma sesión MCP stdio y mismo conjunto
  `(PID, creation_time)` durante A→B.
- Discover global: **1224 tests; 14 failures, 29 errors, 4 skips**. Los 43
  rojos coinciden nominalmente con el baseline histórico; cero suite H12 roja.
- Estado compartido final consultado sólo en lectura:
  owner nulo, cola vacía, self none, cero pending y cero faults.

Review y log:

- `reviews/2026-07-25-stale-client-credential-implementation.md`
- `tools/reports/validation/2026-07-25-h12-global-unittest.log`
