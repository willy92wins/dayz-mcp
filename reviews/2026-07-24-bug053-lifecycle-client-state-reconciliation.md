# BUG-053 — Reconciliación del estado local de lifecycle

Fecha: 2026-07-24  
Clasificación: **degradation / lifecycle blockage**  
Estado: **fixed + regression-tested + runtime-verified**

## Síntoma y reproducción

El daemon devolvía owner nulo, cola vacía, `self.state=none`, cero comandos y
faults nulos, mientras `dayz_test_run`/`dayz_test_stop` devolvían `session_busy` y
`session_acquire_wait` devolvía `session_transition_conflict`.

La reproducción controlada dejó expirar una lease sin exponer ni reutilizar su
token. El daemon convergió a idle, pero el mismo objeto `ControlClient` conservó
sus marcadores y rechazó la siguiente adquisición en ~2 ms, antes de HTTP.

## Causa raíz

- `session_status()` sólo consulta al daemon
  (`tools/dayz_mcp/control_client.py:190-191`).
- `_begin_operation()` consulta el estado local del objeto y rechaza cualquier
  marcador previo (`control_client.py:270-284`).
- El camino de release sólo limpia local tras una respuesta satisfactoria
  (`control_client.py:467-488`). Expiry, abandono o respuesta ambigua pueden dejar
  al daemon terminal sin ejecutar ese bloque local.
- El guard H11 consultaba esos mismos marcadores, de modo que status y mutaciones
  observaban dos fuentes distintas.

No es un crash: daemon y runtime seguían vivos. Tampoco se demostró corrupción
persistente; era una degradación que bloqueaba el lifecycle.

## Arreglo

`ControlClient` incorpora una reconciliación serializada
(`control_client.py:519-599`) con estas precondiciones acumulativas:

1. existe `active_operation_id` local exacto;
2. status actual: `self=none`, sin ticket/lease, posición nula, cero comandos,
   faults nulos, cleanup vacío y generación no vacía;
3. el daemon confirma `cancelled=true` y devuelve el mismo operation ID para la
   identidad autenticada;
4. un segundo status satisface las mismas invariantes;
5. el snapshot local no cambió bajo `_transition_lock`.

Sólo entonces se limpian lease/ticket/operation ID locales. No se transmite el
lease token. `session_acquire` (`:286-365`), `session_acquire_wait` (`:623-645`)
y el guard H11 (`dayz_test_tool.py:302-313`) usan el mismo mecanismo.

`owner=null` no basta: el status público no muestra reservas de cola y no cerca
una request tardía. El fence autoritativo ya existente
`session_coordination.cancel_operation` (`session_coordination.py:772-841`)
está limitado a identidad + operation ID/source operation ID.

Un cambio de `daemon_generation` se admite únicamente tras toda la prueba; la
generación nueva invalida autoridad vieja, pero nunca actúa como reset.

## Seguridad e invariantes

- Lease/ticket sin operation ID: fail-closed.
- Owner propio real, queued/releasing, pending command, fault, cleanup degradado,
  status malformado o fence no exacto: fail-closed.
- Owner extranjero no se cancela; su presencia no impide cercar una operación
  propia ya terminal, y la nueva adquisición vuelve a FIFO.
- La reconciliación no adopta ni detiene runs. Lifecycle sigue exigiendo `run_id`
  exacto.
- Segunda ejecución de recovery: no-op idempotente.

## Pruebas

- Suite afectada: **135/135 OK**, 8.748 s (repetición final).
- Protocolo coordinación: **56/56 OK**, 2.719 s (repetición final).
- `py_compile`: OK.
- Casos: normal, excepción durante consumer/launch, cierre manual, expiry/pérdida
  de lease, cambio de generación, local-busy/remoto-clean, owner extranjero,
  idempotencia, fence incorrecto, final no-idle y estados no demostrables.
- Discover global: 1181 tests, 3 failures + 29 errors + 4 skips históricos fuera
  del delta. No se declara verde; las suites afectadas no presentan fallos.

## Gate runtime SUB_BRZ

Se validó sin restart del runtime y sin launch/kill manual:

1. status;
2. run server;
3. run client con el mismo run ID;
4. stop exacto;
5. nueva ejecución;
6. gate final server+client con el extra mod sellado `@DayZ_MCP`;
7. `world_spawn(type="SUB_BRZ")` → `found=true`, tipo exacto, object ID positivo;
8. stop exacto y status terminal limpio.

Run final: `cac67cd8-4778-48ca-b485-ee142ff439e9`.

## Veredicto de revisión

**PASS para BUG-053.** El delta corrige la causa raíz sin debilitar el protocolo
compartido. El baseline global legacy queda como deuda separada, no como regresión
del arreglo.
