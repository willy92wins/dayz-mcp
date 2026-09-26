# DayZ_MCP — Trabajo pendiente y prompts listos (2026-07-27)

Handoff maestro de la sesión de concurrencia multi-agente. Cada bloque es un **deliverable
independiente** con su prompt listo para copiar. Regla: **un deliverable por sesión**.

Contexto completo: `AI/30_Sessions/2026-07-27-DayZ_MCP-concurrencia-multiagente-bug062-063.md`.
Estado vivo: `HANDOFF.md`. Ledger: `AI/10_Projects/DayZ_MCP/bug-ledger.md`.

---

## Estado: qué está cerrado y qué no

| Item | Estado | Evidencia |
|---|---|---|
| BUG-062 mitigación (idle-timeout 600→3600) | **cerrado** | 10 clientes frescos con `3600` |
| BUG-062(b) re-acreditación + 2 códigos accionables | **cerrado offline** | 58/58 + `py_compile` |
| BUG-063 doctor (`CONFIG_PROBE_FAILED`) | **cerrado** | 51/51 + funcional |
| BUG-064 hijo de test huérfano | proceso muerto, **fix del harness abierto** | — |
| Cola del run (el problema original) | **sin construir** | plan fases 0-6 |
| R21 independiente de BUG-062(b) | **sin hacer** | — |
| `vehicle_trace` Fase A0→A4 | sin hacer (frente aparte) | prompt propio |

**Lo que de verdad falta para resolver lo que motivó la sesión**: la cola del run. Lo cerrado
hoy fue infraestructura de credencial, no la contención entre agentes.

---

## Reglas operativas ganadas en esta sesión — aplicar en todos los prompts

1. **Workspace de Codex NUNCA en OneDrive.** Verificado: 3 lanzamientos sobre OneDrive, uno hubo
   que rescatarlo y otro pasó 46 min sin crear un proceso hijo ni una línea de log. El mismo
   prompt sobre `%TEMP%` arrancó en 30 s. Copiar sólo lo necesario (excluir `spike0` y
   `native-launchers`: 173 de 181 MB) y traer el resultado con diff + verificación.
2. **El marcador terminal del rollout de Codex es `task_complete`**, no sólo `tokens used`. Un
   waiter que no lo vigile declara "cuelgue" sobre tareas terminadas.
3. **Nunca filtrar procesos por substring para decidir un kill**: token exacto + excluir el PID
   propio. El `codex.exe` de un job NO lleva el job-id en argv; para cancelar hay que recorrer la
   cadena de parentesco.
4. **Las reviews de seguridad NO van a Codex** (el filtro del proveedor las bloquea): van a
   subagentes Claude. El código de credencial/acreditación entra en esa categoría.
5. **No re-registrar el MCP con `install-mcp.ps1 -Register`** para cambiar un flag: hace
   `remove`+`add` sin setear `timeout`/`tool_timeout_sec` y tira los timeouts de 7 días.

---

## P1 — R21 adversarial de BUG-062(b) · destino: SUBAGENTE CLAUDE (no Codex)

> **Por qué Claude y no Codex**: es una review de un guard de acreditación. El filtro del
> proveedor de Codex bloquea sistemáticamente ese encuadre (verificado 3 veces).

```
Revisión adversarial independiente (R21) del fix de BUG-062(b) en DayZ_MCP. Read-only:
no modifiques ningún archivo.

Alcance exacto:
- C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\daemon_credential.py
- C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\control_client.py
- C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\accredited_daemon_transport.py
- C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\server.py
- sus tests bajo ...\tools\tests\

Qué cambió: ante un fallo seguro de identidad de daemon (pre-request, 0 bytes enviados), un
epoch bajo el lock existente single-flightea la revalidación y permite EXACTAMENTE UN replay por
el transporte acreditado completo, con el deadline original. Dos códigos terminales nuevos:
client_policy_untrusted_open_new_session (drift de policy) y
daemon_reaccreditation_failed_open_new_session (segundo fallo / 401 / deadline).

Ataca estas hipótesis, cada una con cita path:line:
1. ¿Puede el replay saltarse ALGUNA comprobación que sí hace la construcción inicial del cliente
   (owner del socket, PID único y estable, executable/argv/cwd canónicos, doble snapshot)?
2. ¿Existe algún camino donde se encadene un TERCER intento, o donde el deadline original se
   extienda de hecho?
3. ¿Puede el single-flight entregar a un hilo un secreto revalidado por otro bajo una autoridad
   distinta?
4. ¿Puede el drift de policy/config acabar clasificado como "daemon reemplazado" y por tanto
   recuperarse cuando debería fallar cerrado?
5. ¿Se filtra algún secreto, keyfile o token a mensajes, excepciones o logs?
6. ¿Resucita algún lease/ticket de una daemon_generation anterior? (invariantes H2/H5)
7. ¿Algún fallo que antes era visible baja de severidad o desaparece del resumen?

Para cada hallazgo: severidad (CRITICAL/HIGH/MEDIUM/LOW), cita path:line, escenario concreto de
fallo (entradas y estado que lo producen) y por qué el test existente no lo cubre. Si no
encuentras nada en una hipótesis, dilo explícitamente en vez de rellenar.

Veredicto final: SOUND / SOUND-with-fixes / UNSOUND.
```

---

## P2 — Fase 0 del plan de concurrencia · destino: CODEX (scratchpad local)

> Precondición de todo lo demás. Sin la tabla de presupuesto de arena, la unión de mods es una
> apuesta: la arena `4_World` estaba al 97 % de 32 MiB **sin** LFPG cargado.

```
Fase 0 del plan C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-07-26-multi-agent-run-sharing-plan.md.
NO implementes ninguna otra fase. NO toques código de producto en esta fase.

Entregables, en este orden:

1. BASELINE CONGELADO. Ejecuta la suite completa dos veces y produce un manifest con los IDs de
   test, su resultado y el hash de los archivos de test. Las dos observaciones deben coincidir;
   si no coinciden, para y reporta la diferencia. El HANDOFF documenta un baseline global no
   verde (discover 1227: 11 failures, 31 errors, 4 skips): NO lo arregles, sólo congélalo. Un
   baseline inestable invalida todo lo que venga después.

2. TABLA DE PRESUPUESTO DE SCRIPT ARENA POR MOD. Para cada mod del stack habitual, mide cuánta
   arena consume al cargarse. Documenta el método exacto que usaste y su margen de error. Si el
   método no es fiable, dilo y propón alternativa en vez de dar números inventados.

3. Deja ambos artefactos como archivos en el workspace, con hashes.

Fuera de alcance: no toques session_coordination.py, process_lifecycle.py, dayz_test_*.py ni el
bridge Enforce. No arranques DayZ ni adquieras lease.

Entrega bloques A B C D con salida real de comandos.
```

---

## P3 — Fase 1: rechazo de pre-admisión honesto · destino: CODEX (scratchpad local)

```
Fase 1 del plan 2026-07-26-multi-agent-run-sharing-plan.md. SOLO la Fase 1.
Precondición: la Fase 0 debe estar cerrada con baseline congelado. Si no lo está, para.

Problema: cuando el lifecycle rechaza un start ANTES de admitirlo (p.ej. active_run_exists), el
worker devuelve un run_id que nunca se registró, marca cleanup_degraded=true por intentar parar
ese run inexistente, y reintenta 3 veces un rechazo determinista. Anclas verificadas en
dayz_test_worker.py: run_id generado en :373-378, 3 reintentos en :393-402, stop best-effort y
cleanup_degraded en :431-439. El set cerrado WORKER_ERROR_CODES (:24-38) no incluye el motivo
real, así que se pierde.

Implementa: para una LISTA CERRADA de rechazos de pre-admisión, un solo intento, sin run_id, sin
cleanup_degraded, y propagando el motivo real.

GUARDARRAÍL QUE NO SE NEGOCIA: el discriminador debe ser esa lista cerrada. Ante CUALQUIER
resultado fuera de ella, conserva el comportamiento conservador actual. El fallo a evitar no es
un run_id feo: es FILTRAR UN RUN (procesos DayZ vivos sin registro). Fail-closed gana a limpieza
cosmética. Si dudas, no limpies.

TDD estricto: primero un test que observe el rechazo de pre-admisión y falle por la razón
esperada. Añade negativos: un fallo POSTERIOR a la admisión debe seguir produciendo run_id y
cleanup del run creado.

Fuera de alcance: no toques session_coordination.py ni la semántica de leases (BUG-046 está CORE
GREEN, no se reabre). No arranques DayZ.

Entrega bloques A B C D con salida real de unittest.
```

---

## P4 — Fase 2: el run como recurso encolable · destino: CODEX (scratchpad local)

> **Esta es la fase que resuelve el problema original.** Tras ella, un agente bloqueado por el
> run de otro espera con posición visible en vez de recibir un error.

```
Fase 2 del plan 2026-07-26-multi-agent-run-sharing-plan.md. SOLO la Fase 2.
Precondición: Fases 0 y 1 cerradas.

Objetivo: que active_run_exists deje de cruzar la frontera pública. Hoy dayz_test_run espera en
el FIFO del LEASE indefinidamente (dayz_test_tool.py:426-434, max_wait_s=None) y, ya con el lease
en la mano, choca contra active_run_exists (process_lifecycle.py:874, también :832 y :871).
Esperas tu turno y te rechazan igual.

Implementa la espera por el recurso RUN, con el mismo patrón de progreso que ya existe para el
lease: queued(position) -> executing. La espera sigue siendo REQUEST-BOUND: no introduzcas cola
durable ni persistencia de jobs (rechazado en BUG-046 §2.1 por convertir el daemon en un segundo
orquestador).

Criterios:
- Una petición incompatible se encola con posición visible, o termina con un código explícito.
  Nunca active_run_exists en la superficie pública.
- Timeout o cancel no deja ticket, lease ni run ocultos.
- Si el proceso llamante desaparece, su espera muere con él; nada se promueve a ciegas.

TDD estricto, con negativos para cada salida no exitosa.

Fuera de alcance: no toques session_coordination.py. No introduzcas todavía clases de run
(SHARED/EXCLUSIVE) ni cambios de schema en runs.json: eso es Fase 3.

Entrega bloques A B C D con salida real de unittest.
```

---

## P5 — BUG-064: hijo de test que sobrevive a su padre · destino: CODEX (trivial)

```
Arregla BUG-064 en DayZ_MCP. Cambio pequeño y cerrado.

El harness de test del fix C1 del orphan-guard lanza un hijo %TEMP%\c1fix_child_<pid>.py que
sobrevive indefinidamente a su padre: se observó un par vivo 16 horas después, con el padre
muerto. Además su argv contiene la ruta del proyecto, así que cualquier scan por substring lo
clasifica como proceso dayz_mcp — la misma trampa RCL-DISC ya documentada en BUG-032, cuyo fix
(_cmdline_runs_module, token exacto -m dayz_mcp) sí la evita.

Implementa: el harness mata a su hijo en un finally, o le arma un watchdog de muerte de padre.
Un test que falle hoy y pase después debe demostrar que el hijo no sobrevive al padre.

Fuera de alcance: no toques orphan_guard.py salvo que el fix lo exija; no cambies el
discriminador de procesos.

Entrega bloques A B C D con salida real de unittest.
```

---

## P6 — Fases 3 a 6 (compartición real)

**No lances estas todavía.** Fase 3 toca `runs.json` (formato persistente) y a partir de ahí
aplica DZ-R9: `rigorous-data-audit` obligatorio antes de declarar release-safe. El spec ya lleva
los escenarios de crash-recovery (8-12) y los criterios SC-010..012 que esa auditoría necesita.

Orden: Fase 3 (clases de run + adhesión, schema aditivo con rollback demostrado por fixture) →
Fase 4 (drain coordinado + guard de presupuesto de arena) → Fase 5 (zonas de trabajo) → Fase 6
(rigorous-data-audit + gate in-game multi-sesión, que de paso cierra el gate mixto H9).

---

## P7 — `vehicle_trace` Fase A0→A4 (frente independiente)

Prompt ya existente y con hashes verificados:
`reviews/2026-07-26-prompt-opus-resume-vehicle-trace-r22-r26.md`. No lo mezcles con lo anterior.
Su premisa de "caja idle" hay que reverificarla al arrancar, no heredarla.
