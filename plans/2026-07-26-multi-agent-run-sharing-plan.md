# Plan — Concurrencia multi-agente sobre una caja DayZ

**Spec**: [`2026-07-26-multi-agent-run-sharing-spec.md`](2026-07-26-multi-agent-run-sharing-spec.md)
**Date**: 2026-07-26
**Status**: Draft — pendiente de revisión adversarial (R22) antes de tocar producción
**Reparto**: Claude orquesta/revisa; la implementación se delega a Codex por fase (G7).

## Principio de ordenación

Las fases están ordenadas por **dolor resuelto / riesgo asumido**, no por elegancia. Las fases
1 y 2 eliminan el bloqueo mutuo que ya te ha costado dos mediciones, **sin tocar el formato
persistente ni el modelo de datos**. Si el proyecto se detuviera ahí, el problema operativo
principal quedaría resuelto (G5: la opción simple primero).

Cada fase es entregable y reversible por separado. Ninguna fase toca `session_coordination.py`
(BUG-046 está `CORE GREEN`; no se reabre).

## Fase −1 — Churn de daemon (BUG-062) — ADELANTADA, mitigación APLICADA 2026-07-26

Se antepone a todo lo demás: quita el dolor diario de **todos** los agentes y es más pequeña
que cualquier otra fase. El daemon se autoapagaba a los 10 min de inactividad
(`--idle-timeout 600`, reenviado al daemon por `daemon_contract.py:33-36`), el siguiente cliente
lo re-spawneaba con `daemon_generation` nueva, y cada reemplazo desarmaba a todos los clientes
vivos por drift de la tupla de autoridad.

**Mitigación aplicada (config-only, sin código):** `--idle-timeout` 600 → **3600** en los dos
registros. Cambio quirúrgico de un solo token, preservando el resto — en particular los timeouts
de 7 días (`"timeout": 604800000` en `.claude.json`, `tool_timeout_sec = 604800` en
`config.toml`) de los que dependen las esperas FIFO de `dayz_test_run`.

- **`install-mcp.ps1 -Register` NO sirve para esto**: hace `remove`+`add` en ambas plataformas
  (`install-mcp.ps1:405-411`) y no setea esos timeouts → los tiraría, regresando BUG-046. Usar
  el instalador aquí es un error; documentado para que nadie lo repita.
- Backup byte-exacto: `C:\tmp\dayz-mcp-cfg-backup-20260726-idletimeout`
  (`claude.json.bak` sha `25127F71…`, `config.toml.bak` sha `53C94B49…`).
- **Pendiente de verificación post-reinicio**: la app escribe `.claude.json` en vivo; puede
  revertir la edición al cerrarse. Reverificar `--idle-timeout 3600` con las apps ya reiniciadas
  antes de dar la mitigación por buena.

**Cura de fondo (adjudicada, pendiente — va a Codex):** el timeout largo reduce la frecuencia
pero no cura. Cualquier reemplazo de daemon —incluido editar `dayz_mcp/*.py`— sigue desarmando a
todos. La cura es que el cliente detecte generación nueva y **se re-acredite** en vez de fallar
cerrado, más un código de error distinguible que diga "abrir sesión nueva" (BUG-063 incluido).
Toca el guard de acreditación H10 → exige spec + TDD + revisión independiente; no improvisar.

## Fase 0 — Baseline y medición (sin código de producto)

Precondición de todo lo demás. El HANDOFF reporta el baseline global no verde
(`discover 1227: 11 failures, 31 errors, 4 skips`); arrancar sin congelarlo repetiría el drift
que ya bloqueó BUG-046 dos veces.

1. Dos observaciones idénticas de la suite → manifest de IDs/fingerprints/hashes congelado.
2. **Medir el presupuesto de script arena por mod** (tabla): el riesgo #1 de la feature. Dato
   conocido de partida: la arena `4_World` estaba al 97 % de 32 MiB *sin* LFPG cargado
   (proyecto `lfpowergrid-compile-footprint`). Sin esta tabla no hay guard posible y la unión
   de mods es una apuesta.
3. Adjudicar Q1/Q2/Q3 del spec (idle TTL del SHARED, desalojo por EXCLUSIVE, congelado del
   presupuesto).

**Salida**: baseline fingerprinted + tabla de presupuesto + Q1-Q3 resueltas.
**Gate**: sin esto, no empieza la Fase 1.

## Fase 1 — Rechazo de pre-admisión honesto (riesgo bajo, valor inmediato)

Arregla el síntoma que hace ilegibles los fallos de hoy. Tres defectos en el mismo camino:

| Defecto | Ancla | Arreglo |
|---|---|---|
| `run_id` fantasma nunca registrado | `dayz_test_worker.py:373-378`, `:431-439` | no devolver `run_id` si el start fue rechazado pre-admisión |
| `cleanup_degraded=true` por hacer `stop` de un run inexistente | `dayz_test_worker.py:431-439` | no invocar `_stop_best_effort` cuando nada se registró |
| 3 reintentos ciegos de un rechazo determinista | `dayz_test_worker.py:393-402` | 1 solo intento para rechazos de la lista cerrada |
| el motivo real se pierde (`worker_failed`) | `dayz_test_worker.py:24-38` (set cerrado sin `active_run_exists`) | añadir códigos y propagarlos |

**Guardarraíl que no se negocia** (riesgo #3 del spec): el discriminador debe ser una **lista
cerrada de rechazos de pre-admisión**. Ante cualquier resultado fuera de esa lista se conserva
el comportamiento conservador de hoy. El fallo que hay que evitar no es un `run_id` feo: es
**filtrar un run** — procesos DayZ vivos sin registro. Fail-closed gana a limpieza cosmética.

**Estrategia**: TDD estricto. Test que observe el rechazo pre-admisión y falle por la razón
esperada antes de tocar producción.

## Fase 2 — El run como recurso encolable

Sustituye el rechazo por espera. Sin compartir todavía, sin schema nuevo.

- `active_run_exists` (`process_lifecycle.py:832,871,874`) deja de cruzar la frontera pública.
- `dayz_test_run` gana espera por el recurso *run*, con el mismo patrón de progreso que ya usa
  para el lease (`dayz_test_tool.py:426-434`: `queued(position) → executing`).
- La espera sigue siendo **request-bound** (BUG-046 §2.1): no se introduce cola durable.

**Efecto operativo**: el caso SUB_BRZ-bloquea-a-LFPowerGrid deja de ser un fallo y pasa a ser
una espera con posición visible. Este es el punto donde el dolor principal desaparece.

## Fase 3 — Clases de run y adhesión

Primer cambio de formato persistente → **legacy + rollback obligatorios** (G5).

- `RunRecord` gana campos **aditivos opcionales**: `run_class`, `effective_mods`, `participants`
  (`process_lifecycle.py:97-110`). El patrón ya existe en la clase (`launch_acknowledged`
  con default). Rollback demostrado por fixture (SC-011), apoyado en que `from_payload` lee por
  `value.get(...)` y no rechaza claves extra (`:112-133`).
- Tools `dayz_run_join` / `dayz_run_leave`; `dayz_test_run` gana `exclusivity`.
- Adhesión invalidada por cambio de `daemon_generation`, igual que leases y tickets (H2/H5).

## Fase 4 — Drain coordinado + unión de mods

- `dayz_run_drain` con **una sola** reejecución aunque N agentes lo pidan a la vez (SC-006).
- **Guard de presupuesto de arena** consultando la tabla de Fase 0: si la unión no cabe, falla
  cerrado *antes* de parar el run vivo (escenario 7). Este guard es la razón por la que Fase 0
  es precondición y no papeleo.
- El `EXCLUSIVE` nunca hereda mods de la unión (SC-008).

## Fase 5 — Zonas de trabajo

- Pool de zonas disjuntas por mapa (`chernarus`/`livonia`/`sakhal`,
  `dayz_test_request.py:283-285`); un mapa por path arbitrario cae a `EXCLUSIVE`.
- `world_spawn` fuera de la zona propia → fail-closed.
- Liberación de zona + limpieza de objetos al salir o expirar (SC-005, SC-012).

## Fase 6 — Gates de cierre

1. **`rigorous-data-audit` (DZ-R9)** — no negociable: el cambio cruza manifest persistente +
   cola + lifecycle + comandos admin.
2. **Gate in-game multi-sesión real**: 2 sesiones concurrentes adheridas, drain con 3, y una
   medición exclusiva esperando en cola. Es el mismo montaje que exige el **gate mixto
   2 Claude + 2 Codex de H9**, hoy abierto: conviene ejecutarlos juntos y cerrar ambos.
3. Actualizar `product-spec.md` §H con los criterios nuevos y el runbook del protocolo.

## Lo que este plan NO hace

- No toca `session_coordination.py` ni la semántica de leases/grants (BUG-046 `CORE GREEN`).
- No introduce multi-instancia de DayZ (fuera de alcance en `product-spec.md`).
- No introduce cola durable de comandos ni reanudación tras caída del host.
- No arregla el baseline global no verde: lo congela y no lo empeora.

## Riesgo residual declarado

Un juego compartido significa que **el mod de otro agente corre en tu sesión**. Un error de
script ajeno puede degradar tu prueba, y ninguna cantidad de código del host lo evita —
sólo el guard de presupuesto y SC-007 lo acotan. Para trabajo cuyo resultado depende del stack
exacto (mediciones de footprint, gates de release), la respuesta correcta sigue siendo
`EXCLUSIVE`, no compartir.
