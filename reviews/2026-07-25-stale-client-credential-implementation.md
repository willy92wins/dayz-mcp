# Revisión de implementación — H12 / BUG-056

Fecha: 2026-07-25  
Plan: `plans/2026-07-25-stale-client-credential-recovery.md`  
Estado: **GREEN H12; baseline global no verde sin regresión atribuible**

## Resultado

Un proceso `dayz_mcp --client` puede permanecer vivo durante una rotación del
keyfile y un reemplazo acreditado del daemon. Bridge, health y control comparten
un único provider. Sólo un 401 recibido después de acreditar el socket permite
revalidar la autoridad original, releer el mismo keyfile y repetir una vez el
mismo request bajo el deadline original.

La capa no llama a `spawn`, reconcile, release, adopt, lifecycle stop ni ninguna
API de procesos. No modifica lease, ticket, operation, run u ownership.

## Causa raíz verificada

- En el baseline, `ClientRuntime` leía una key en
  `tools/dayz_mcp/server.py:353` y la reutilizaba en `:505` y `:580`.
  `ControlClient` mantenía otra copia en
  `tools/dayz_mcp/control_client.py:117` y la reutilizaba en `:144`.
- Doctor releía el keyfile vigente por request en
  `tools/dayz_mcp/doctor.py:125-152`, incluido el lector pinneado en `:131`.
- El transporte acredita PID/executable/argv/cwd sobre el socket conectado antes
  de emitir HTTP en
  `tools/dayz_mcp/accredited_daemon_transport.py:204-298`.
- El daemon sólo devuelve 401 en auth, antes de leer body o hacer dispatch:
  `tools/dayz_mcp/loopback.py:1121-1131`.
- La reproducción aislada A→B produjo exactamente: cliente cacheado A→401,
  key vigente B→200 y POST rechazado sin cambio de cola/estado. Evidencia:
  `C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\research\2026-07-25-stale-client-credential-codex.md:24-110`.

Por tanto, el incidente fue una **degradación de autenticación/liveness por
credencial cacheada**, no un crash, una adopción de identidad ni una invalidación
por `daemon_generation`.

## Tarea nueva frente a subagente

La metadata local mostró que una tarea raíz nueva creó un árbol lógico
`dayz_mcp --client` nuevo 96 ms después de su rollout. Dos subagentes históricos
observados también crearon cada uno su propio árbol lógico, 91 ms y 82 ms después
de su rollout. Ninguno compartió exclusivamente el objeto cliente del padre.

Ambos casos pueden ocultar el fallo porque el proceso nuevo lee B al arrancar,
pero no reparan el proceso padre que conserva A. Evidencia:
`C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\research\2026-07-25-stale-client-credential-codex.md:113-149`.

## Implementación revisada

- Provider, snapshot secreto sin `repr`, autoridad original pinneada, envelope
  congelado y retry único:
  `tools/dayz_mcp/daemon_credential.py:17-216`.
- Inyección exacta del mismo provider en control:
  `tools/dayz_mcp/control_client.py:94-191`.
- Composición bridge/control, health y bloqueo de `spawn` por auth/identity:
  `tools/dayz_mcp/server.py:294-404,437-567,614-690`.
- Telemetría RAM-only, saturada y registrada sólo después de auth correcta:
  `tools/dayz_mcp/loopback.py:251-293,1121-1131,1434-1446`.
- Doctor con doble revalidación, desincronización separada e INFO sanitizado:
  `tools/dayz_mcp/doctor.py:113-170,589-639,887-910`.

Errores públicos exactos:

- `credential_source_untrusted`
- `stale_client_credential_refresh_failed`
- `stale_client_credential_retry_rejected`
- `stale_client_credential_retry_transport_failed`
- `daemon_identity_unverified`
- `DAEMON_CREDENTIAL_DESYNCHRONIZED` en doctor cuando su key recién leída
  también recibe 401

## RED → GREEN y hallazgos de revisión

1. RED: A→B no tenía provider común; bridge/control conservaban dos copias.
2. RED: el request podía observar `query`/`headers` mutados entre intentos.
   GREEN: el provider congela query, headers y body antes del primer envío
   (`tools/tests/test_daemon_credential.py:130`).
3. RED: reemplazar la referencia de policy podía adoptar otra autoridad válida.
   GREEN: comparación in-memory con el snapshot original y transporte siempre
   contra host/port/executable/argv/cwd pinneados
   (`tools/tests/test_daemon_credential.py:181`).
4. RED: un objeto policy equivalente pero distinto podía entrar por inyección.
   GREEN: `ControlClient` exige el mismo objeto exacto.
5. RED: doctor podía enviar una key leída tras drift entre read y request.
   GREEN: segunda `policy.revalidate()` antes del HTTP.
6. RED: identidad no acreditada durante el retry o tras el lazy-spawn podía
   colapsarse a disponibilidad. GREEN: conserva `daemon_identity_unverified` y
   nunca dispara otro intento.
7. El primer intento del E2E stdio falló antes de producto porque `StringIO` no
   ofrece `fileno()` en Windows. El fixture pasó a un archivo temporal real; no
   hubo cambio productivo por ese fallo de harness.

## Viability y validación

- A→B: mismo method/path/query/body/deadline, sólo key + cabecera constante en el
  segundo envío; PASS (`test_daemon_credential.py:70`).
- 401 persistente: exactamente dos requests y error estable; PASS
  (`test_daemon_credential.py:224`).
- Policy/path/identity no acreditada: cero adopción, cero retry adicional y cero
  `spawn`; PASS (`test_daemon_credential.py:181,267` y
  `test_client_runtime_control_composition.py:254`).
- Concurrencia: ocho callers, una lectura/publicación y dos requests máximos por
  caller; PASS y shake **25/25**
  (`test_daemon_credential.py:482`).
- Lease/run activo: el 401 inicial no llega a dispatch y el retry autenticado no
  cambia owner/run; PASS (`test_daemon.py:211`).
- Cliente MCP real: misma sesión stdio y mismo conjunto `(PID, creation_time)`
  antes/después de A→B; PASS **3/3**
  (`test_client_credential_rotation_e2e.py:393`).
- Focal final de provider/control/composición/client/daemon/doctor/transporte/
  keyfile/auditor/loopback/E2E: **290 tests OK, 1 skip**.
- Auditor de sinks HTTP productivos: PASS; sólo permanece el transporte
  acreditado canónico.
- `compileall`: PASS.
- Discover global: **1224 tests; 14 failures, 29 errors, 4 skips**. Los 29
  errors son los mismos dos subtests de daemon security, 26 casos legacy de
  port reclaim y un caso Task7. Los 14 failures son dependency lock, argv
  legacy, registros/docs/skills de launcher y vehicle-trace v6. Ningún ID
  pertenece a H12. Log:
  `tools/reports/validation/2026-07-25-h12-global-unittest.log`.
- `session_status` compartido final, sólo lectura:
  `owner=null`, `queue=[]`, `self.state=none`, `pending_commands=0`, sin faults.

## Seguridad y compatibilidad

- Ningún error, log, finding o telemetría contiene key, token o identidad.
- La key está en un dataclass con `repr=False`; los códigos de error aceptan sólo
  un allowlist cerrado.
- La autoridad de transporte y el keyfile de refresh proceden del snapshot
  original. Una policy inesperada falla antes de HTTP.
- Cada request hace como máximo dos envíos; no hay recursión.
- Un segundo 401, fallo de refresh o identidad no acreditada no se reinterpreta
  como daemon ausente y no autoriza `spawn`.
- No cambia ningún formato durable. La cabecera constante y
  `credential_recovery` son adiciones compatibles: daemon viejo ignora la
  cabecera; doctor nuevo acepta ausencia de telemetría.
- Un proceso cliente cargado con código pre-fix necesita un rollout único. Una
  vez cargado este código, futuras rotaciones autorizadas no requieren cerrar la
  tarea.

## Rollback

Bundle:
`reports/rollback/2026-07-25-stale-client-credential-prechange/`.

1. Verificar paths y SHA-256 de `SHA256SUMS.md`.
2. Restaurar sólo los archivos existentes listados.
3. Eliminar los tres ficheros nuevos de provider/tests y este review.
4. Reejecutar el focal.

No hay migración de datos que deshacer. El rollback no toca keyfile, daemon,
DayZ, leases, tickets, runs ni procesos. El bundle también conserva por separado
el delta concurrente retirado en
`reports/rollback/2026-07-25-stale-client-credential-concurrent-delta-0500/`.
