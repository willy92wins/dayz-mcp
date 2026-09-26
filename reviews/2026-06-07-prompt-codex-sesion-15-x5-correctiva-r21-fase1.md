# Prompt Codex — sesión 15 · X.5 correctiva R21 fase 1

> Patrón: implementation-handoff (correctiva X.5). Scope: SOLO aplicar los findings R21 consolidados.
> NO features nuevas. Tras esto: re-verificación + test in-game agrupado (R5). Generado 2026-06-07.

```
===== PROMPT INICIO =====

Tarea: sesión correctiva X.5 — aplicar los findings de la R21 estructural de fase 1 (Control) de DayZ-MCP.
Esta sesión cubre ÚNICAMENTE los fixes R21-001..006 (+ 007/C-4 si triviales). NO añadas features, NO toques
nada fuera de esos fixes. El objetivo es dejar el DATO del probe B3 INTERPRETABLE antes del test in-game.

## Carga inicial obligatoria (leer antes de tocar nada)
1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-07-r21-fase1-consolidated.md
   (los findings consolidados con su fix — VINCULANTE; aplícalos uno a uno).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-07-fase1-control.md
   (plan v2 — contexto de la spec; el probe es DATO de decisión, no PASS).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\CLAUDE.md
   (invariantes: async only, Enforce 1.29 SIN ternario ?:, fail-closed).

CÓDIGO A EDITAR:
- C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c (todos los fixes salvo 003-server).
- (R21-003 es bridge-side: la validación de throttle/duration va en el bridge antes del job; el server ya valida args-dict.)
Source vanilla para verificar firmas: C:\Users\guill\OneDrive\Documentos\DayZ Projects\scripts\ .

## Fixes a aplicar (del consolidado)

### R21-001 (FAIL) — sostener inputs en SAMPLE
En la fase SAMPLE (`ProcessDriveProbeSample`), ANTES de cada lectura de speedo/pos, re-aplicar:
`car.SetThrottle(throttle); car.SetHandbrake(0); car.SetBrake(0);` (guarda `throttle` en el job para reusarlo).
Mantén el gear estable (ShiftTo(FIRST) si GetGear() < FIRST). Luego mide.

### R21-002 (FAIL) — fixture readiness mecánico completo
`vehicle_fixture_ready=true` solo si: `WheelCountPresent()==WheelCount()` **Y** `GetFluidFraction(CarFluid.FUEL)>0`
(car.c:367) **Y** (si `IsVitalCarBattery()||IsVitalTruckBattery()` → batería presente, p.ej. `GetBattery()!=null`)
**Y** (si `IsVitalSparkPlug()` → bujía presente, `FindAttachmentBySlotName("SparkPlug")!=null`). `OnDebugSpawn`
llena todo eso; si tras el prep_deadline NO se cumple, REPORTA `fixture_ready=false` (no interpretar el speedo).
Verifica los nombres de las APIs vitales en `carscript.c` (IsVital*, GetBattery, GetFluidFraction) antes de usarlas.

### R21-003 (FAIL) — throttle/duration fail-closed
En `DispatchVehicleDriveProbe` (antes de crear el job): si `args.throttle` presente y NO está en `0..1` →
PostResult error `bad_throttle` (sin job). `duration`: capar a un máximo de probe (p.ej. 300 ticks); si fuera de
rango → `bad_duration` o clamp documentado. Ausente → default (throttle 1.0, duration por defecto).

### R21-004 (WARN) — engine_on_server posterior
Re-muestrear el estado del motor: además del inicial, registrar `engine_on_server` también durante SAMPLE (p.ej.
OR de EngineIsOn a lo largo del muestreo, o un `engine_on_server_final`). La interpretación usa el estado posterior.

### R21-005 (WARN) — fail-closed de jobs concurrentes
Antes de crear un job `seat`/`drive_probe`, si ya existe en `m_Jobs` un job activo para el mismo actor (player) o
subject (vehículo) → PostResult error `busy` (sin crear el job). (Evita interferencia de probes solapados.)

### R21-006 (WARN) — B2 readiness comprueba el vehículo
En `IsSeatReady`: exigir `human.GetCommand_Vehicle().GetTransport() == job.subject` además de
`!IsGettingIn() && GetVehicleSeat()==DayZPlayerConstants.VEHICLESEAT_DRIVER`.

### R21-007 / C-4 (NIT, si triviales)
- B1: si quieres el fallback literal del plan 0.4, acepta `subject!=null` como readiness y reporta `found` por
  separado; si no, déjalo y NO lo llames fallback. (Decisión tuya, documenta en Bloque C.)
- `FindTransportNear`: el radio 4m a una const nombrada.

## Restricciones críticas
1. **SOLO los fixes de arriba.** NO features, NO refactor de la infra que no pida un fix. `query_player_state` y
   `world_spawn` intactos.
2. **Async only; SIN ternario `?:`** (if/else). **NO `DestroyRestApi`**; guard `GetRestApi()`-first intacto.
3. **NO improvises**: si un nombre de API vital no existe como esperas, verifícalo en vanilla y usa el real; si no
   hay vía limpia, anótalo en Bloque C con interpretación conservadora (p.ej. fixture_ready=false fail-closed).
4. **NO self-review / no in-game** en esta sesión: aplica, compila, corre tests Python, y para.

## Output esperado al cerrar (A/B/C/D)
### Bloque A — Archivos modificados (paths + tamaño).
### Bloque B — Evidencia de compilación (AddonBuilder + smoke) + `python -m unittest discover` (3/3 OK) + confirmación grep: sin ternario, sin DestroyRestApi.
### Bloque C — Por cada R21-00N: qué cambiaste (file:line) y cómo. Hallazgos (nombres de API vitales reales usados).
### Bloque D — Handoff: fase 1 lista para re-verificación de Claude + test in-game agrupado (R5). Deuda restante.

===== PROMPT FIN =====
```
