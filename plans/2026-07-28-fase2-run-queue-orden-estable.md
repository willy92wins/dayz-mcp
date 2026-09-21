# Plan — Fase 2 re-especificada: el run como recurso encolable con orden estable

**Versión**: v2 (post-R22) · **Fecha**: 2026-07-28 · **Estado**: listo para implementar.
**Spec padre**: [`2026-07-26-multi-agent-run-sharing-spec.md`](2026-07-26-multi-agent-run-sharing-spec.md)
**Sustituye a**: la Fase 2 del [`plan de concurrencia`](2026-07-26-multi-agent-run-sharing-plan.md), cuya
implementación (aparcada en `%TEMP%\dayz-mcp-p4-fase2\`) queda **descartada** por decisión del usuario
(2026-07-28): reutilizaba el FIFO del lease con reentrada y no garantizaba orden ni ausencia de inanición.

## 0. Changelog v1 → v2

R22 de Codex sobre el v1: **NEEDS-FIX, 2 BLOCKER + 5 MAJOR + 1 MINOR**. Los ocho aplicados. El v1 tenía un
error estructural: **daba por supuesto un modelo de propiedad del lease que no es el del código**. Quien posee
el lease no es el worker ni la tool, sino la transacción, que lo adquiere antes de lanzar el worker
(`native_launcher_transaction.py:115`) y sólo lo libera en su `finally` (`:82`, `:131`). Verificado
host-direct antes de aceptar el hallazgo.

| ID | Severidad | Qué cambia en el v2 |
|---|---|---|
| R22-01 | BLOCKER | El bucle de espera pasa a la **tool**, que es quien vive durante todo el request. Alcance ampliado al worker. §2 reescrita |
| R22-02 | BLOCKER | **Fence**: toda admisión consulta la cola aunque no haya run activo. A9 corregido |
| R22-03 | MAJOR | Orden fijado: **ticket registrado con el lease aún retenido**, y el release fuera del `_operation_lock` |
| R22-04 | MAJOR | Liveness y claim separados: el ticket se renueva en cada reintento; sólo caduca quien deja de reintentar |
| R22-05 | MAJOR | P1 y P2 reformuladas como condicionales demostrables |
| R22-06 | MAJOR | P4 gana contrato de identidad e idempotencia por petición |
| R22-07 | MAJOR | Criterios nuevos A10-A13; eliminados los no falsables |
| R22-08 | MINOR | Reloj inyectable explícito; A8 pasa a gate por módulos e IDs |

## 1. El problema, con evidencia

`active_run_exists` se emite en `process_lifecycle.py:830-833`, `:869-872` y `:873-874`, y los tres llaman a
`_start_rejection(client, authority, ...)` (`:668`). El parámetro `authority` es la prueba del defecto:
**cuando se rechaza, el cliente ya ha ganado el lease**. Ha esperado su turno en el FIFO del lease y sólo
entonces descubre que el recurso que quería está ocupado.

Las dos salidas posibles hoy son malas: conservar el lease mientras espera bloquea a todos (incluido el dueño
del run que quiera pararlo), y soltarlo para reintentar hace que el orden sea el de cada reentrada. La causa
raíz es que **el run no tiene cola propia**: se usa la cola de otro recurso para ordenar el acceso a él.

## 2. Diseño v2: el ticket ordena, el lease sólo da voz

La idea que hace esto implementable sin tocar la propiedad del lease:

> **La reentrada al FIFO del lease sigue existiendo, pero deja de determinar el orden.** Adquirir el lease
> sólo da derecho a *hablar* con lifecycle. Quién arranca lo decide el **ticket**, que se registró una vez.

### Protocolo

1. **Registro (una sola vez).** El worker llama a `lifecycle_start` con el lease ya retenido. Si no puede
   admitirse —porque hay run activo **o** porque hay cola y no es la cabeza— lifecycle **registra un ticket
   RUN** bajo `_operation_lock` y devuelve un terminal privado `run_queued` con `ticket_id` y posición. El
   ticket se crea **mientras el lease sigue retenido** (R22-03): el estado intermedio seguro es *ticket
   registrado y lease aún en mano*; el contrario nunca debe existir.
2. **Liberación normal.** El worker termina y la transacción libera el lease en su `finally`, como hoy. **El
   release nunca ocurre dentro de `_operation_lock`** (R22-03): `SessionCoordinator.release` dispara cleanup y
   el cleanup de lifecycle vuelve a tomar ese lock (`process_lifecycle.py:1437`, `:1446`, `:1472`).
3. **Espera en la tool.** `dayz_test_run` sigue siendo request-bound y **no devuelve el ticket al agente**:
   ve el terminal privado, reporta `queued(position)` con el patrón existente (`dayz_test_tool.py:399-436`) y
   **reintenta la transacción completa presentando el mismo `ticket_id`**. El reintento *es* el polling: no
   hace falta endpoint nuevo.
4. **Fence en la admisión** (R22-02). Bajo `_operation_lock`, **toda** admisión consulta la cola, también
   cuando `active` está vacío:
   - cola vacía → camino actual, sin cambios;
   - cola no vacía → sólo el ticket de cabeza puede iniciar; una petición **sin** ticket se registra al final.
   Así, un recién llegado no puede colarse en la ventana entre que el run se libera y la cabeza recupera el
   lease.
5. **Liveness por reintento** (R22-04). Cada reintento autenticado del mismo ticket lo renueva. Una cabeza que
   sigue reintentando **nunca caduca**, aunque tarde en ganar el lease. Sólo caduca quien deja de reintentar
   durante la ventana de gracia; al vencer se elimina **una sola** cabeza y el siguiente pasa a elegible
   atómicamente. Reloj **monótono e inyectable** (R22-08).
6. **Identidad e idempotencia** (R22-06). El ticket se liga a `session_id` + `daemon_generation` + hash de la
   petición. Presentarlo desde otra identidad, con otra petición o bajo otra generación **no** lo hereda: se
   rechaza y el ticket original queda intacto. Registrar dos veces la misma petición devuelve el **mismo**
   ticket, nunca uno nuevo.

### Propiedades (reformuladas para ser demostrables — R22-05)

- **P1 — orden estable, condicional**: si A registra ticket antes que B, y **el ticket de A sigue vivo**, A es
  admitido antes que B. La posición de un ticket vivo nunca sube.
- **P2 — sin adelantamientos, no "tiempo finito"**: ningún cliente sin ticket, ni con ticket posterior, es
  admitido antes que la cabeza viva. *No se promete servicio en tiempo finito*: si el run activo no termina
  nunca, nadie entra — y eso es correcto. Cuándo muere un run es Q1/Q2, abiertas y fuera de esta fase.
- **P3 — sin deadlock**: un cliente que espera **no retiene el lease** entre reintentos, y el release nunca
  ocurre dentro de `_operation_lock`.
- **P4 — fail-closed con identidad**: ante cualquier duda (generación, identidad, petición que no cuadra,
  gracia vencida) se rechaza la admisión. Descartar un ticket nunca puede perjudicar a otro ticket legítimo.
- **P5 — sin formato persistente nuevo**: la cola vive en memoria del daemon; **ningún estado de cola llega a
  disco**, y hay criterio que lo demuestra (A12).

## 3. Lo que este plan NO hace

- **NO cierra BUG-067** (run ajeno no registrado). Este plan ordena el acceso al run entre clientes
  que pasan por el lifecycle; su cola se alimenta del **manifest**, igual que el `active` de hoy
  (`process_lifecycle.py:813`). Un servidor DayZ arrancado fuera del lifecycle sigue siendo invisible
  para la admisión, y el arranque lo pisaría igual. Cerrarlo exige consultar **procesos vivos**, no
  sólo el manifest, y es trabajo independiente de esta fase.

- **No toca `session_coordination.py`** ni la semántica de leases (BUG-046 `CORE GREEN`).
- **No introduce cola durable** ni toca `runs.json` (Fase 3+, exige DZ-R9).
- No implementa clases de run, adhesión, drain ni zonas (Fases 3-6).
- No expone el ticket en la superficie pública: `dayz_test_run` sigue devolviendo lo mismo que hoy.

## 4. Alcance de ficheros

| Fichero | Cambio |
|---|---|
| `dayz_mcp/process_lifecycle.py` | cola RUN + fence de admisión; los 3 sitios de `active_run_exists` pasan a registrar ticket |
| `dayz_mcp/dayz_test_worker.py` | transporta `ticket_id` en el terminal privado y lo presenta al reintentar |
| `dayz_mcp/dayz_test_tool.py` | bucle de espera request-bound con reintento por ticket y progreso `queued(position)` |
| `tests/test_*` | suite nueva de la cola + regresión |

**Decisión del riesgo 4 (R22-01)**: `active_run_exists` sale de `PRE_ADMISSION_REJECTION_CODES`
(`dayz_test_worker.py:24`) en cuanto deje de cruzar la frontera. No se deja la entrada huérfana (DZ-R7).

`process_lifecycle.py:1401` (`_reject_reserved(..., "active_run_exists")`) queda **FUERA**: es el camino de
adopción, opera sobre un run existente y no compite por crear uno. Misma decisión consciente que en la Fase 1.

**Consecuencia de despliegue**: `dayz_test_worker.py` es uno de los 13 `PACKAGED_MODULES` sellados en
`app.pyz`. Tocarlo **exige repetir rebuild + rollout CAS** o el cambio queda inerte y `verify_bundle` da
`app_module_drift`. Procedimiento en el `LIVE-STATE`; ejecutado dos veces sin incidencia el 2026-07-28.

## 5. Criterios de aceptación (verificables offline, reloj y run inyectados)

- **A1 (P1)**: tres clientes que chocan en orden A, B, C reciben posiciones 1, 2, 3 y son admitidos en ese
  orden.
- **A2 (P1)**: la posición de un ticket vivo **nunca aumenta**, ni al entrar nuevos ni al caducar otros.
- **A3 (P2, fence)**: **con barreras que fuercen la intercalación exacta de R22-02** — R termina, y C (sin
  ticket) intenta arrancar antes de que A recupere el lease: C **no** arranca y queda al final de la cola.
- **A4 (P3)**: un cliente en espera no retiene el lease entre reintentos; aserción directa sobre el estado del
  coordinador.
- **A5 (P4)**: cambio de `daemon_generation` invalida todos los tickets.
- **A6 (liveness)**: una cabeza que **sigue reintentando** no caduca aunque tarde en ganar el lease; una que
  deja de reintentar caduca al vencer la gracia y el siguiente pasa a elegible. Ambos casos con reloj
  inyectado.
- **A7 (frontera pública)**: `active_run_exists` deja de cruzar la frontera pública de `dayz_test_run`
  (SC-002), y `PRE_ADMISSION_REJECTION_CODES` ya no lo contiene.
- **A8 (no regresión)**: gate **por módulo e ID**, no por conteo global: ningún ID nuevo en rojo respecto a la
  referencia **1265 / 1F / 1E / 4S**, excluyendo los cuatro módulos no deterministas conocidos
  (`test_bug046_startup_deadlock`, `test_task7_review_regressions`, `test_bug046_audit_fault_recovery`,
  `test_client_mode`).
- **A9 (sin contención)**: sin run activo **y sin tickets pendientes**, el camino y el resultado son idénticos
  a los de hoy.
- **A10 (idempotencia — R22-06)**: registrar dos veces la misma petición devuelve el **mismo** ticket.
- **A11 (identidad — R22-06)**: presentar un ticket desde otra `session_id`, con otro hash de petición o bajo
  otra `daemon_generation` se rechaza **y el ticket original sobrevive intacto**.
- **A12 (P5)**: tras una sesión completa con cola no vacía, **ningún fichero del árbol de runtime contiene
  `ticket_id` ni estado de cola**. Criterio falsable por inspección de disco, no por lectura del código.
- **A13 (release seguro — R22-03)**: con barreras, el ticket queda registrado **antes** de liberar el lease; y
  si el release falla, el ticket no se concede: se elimina o expira.

## 6. Riesgos declarados

1. **Cabeza que reintenta muy despacio** — bloquea la cola durante su gracia. Es el coste deliberado de un
   FIFO estable, y A6 acota la ventana.
2. **Reloj** — monótono e inyectable, nunca hora de pared.
3. **Reentrada de `_operation_lock`** — es `RLock` (`process_lifecycle.py:480`), reentrante en el mismo hilo,
   pero el release dispara cleanup que vuelve a tomarlo: mantener el release fuera del lock no es opcional.
4. **Coste de los reintentos** — cada uno adquiere y suelta el lease. Aceptable: es lo que ya ocurre hoy, sólo
   que ahora no decide el orden. Debe medirse que no degrada el caso sin contención (A9).

## 7. Estrategia

TDD estricto: cada propiedad P1-P5 tiene test antes del código. Reloj y run inyectados como dobles.
**Ninguna prueba de orden puede depender de procesos reales ni de `sleep`**: la suite ya tiene cuatro módulos
no deterministas y un quinto haría ilegible el baseline. Las intercalaciones de A3 y A13 se fuerzan con
barreras explícitas, no con temporización.
