# R21 estructural — CONSOLIDADO (Claude + Codex) · fase 1 (Control) DayZ-MCP

Doble revisión independiente del código de fase 1 (Paso 0 + 1a + 1b). Fuentes: `2026-06-07-r21-fase1-claude.md`
(receptor host-direct de Claude) + `2026-06-07-r21-fase1-codex.md` (Codex independiente, sesión 14). Reconciliado
por Claude con re-verificación host-direct de cada finding.

**Veredicto: reject → X.5 correctiva (sesión 15) antes del test in-game.** El transporte/infra y las invariantes
están limpios; el bloqueo es la **validez del DATO del probe B3**: en el estado actual `speedo_max≈0`/`pos_delta≈0`
NO distingue client-auth de artefacto de fixture/secuencia (viola LL-115). 3 FAIL lo causan.

## Convergencia (el R21 independiente pagó)
- Codex **encontró independientemente** el throttle-no-sostenido (mi C-1) y lo **subió a FAIL** (yo lo marqué
  WARN). Acepto FAIL: un DATO de decisión contaminado inutiliza el test in-game (caro, R5). Mismo patrón que fase 0
  (Codex cazó el A1 tautológico).
- Codex aportó 2 FAIL + 2 WARN que mi pase no marcó (R21-002/003/005/006). Yo aporté C-3 (coherencia
  MAX_QUEUE/MAX_PENDING) y C-4 (radio hardcoded). Todo verificado host-direct.

## Hallazgos consolidados (severidad final, todos verificados por Claude)

### FAIL — bloquean el test in-game (van al X.5)
- **R21-001 · throttle no sostenido** (`MCPBridge.c:883-885` set una vez; SAMPLE `:896-923` no re-aplica). Vanilla
  setea throttle cada frame (`carscript.c:1377`); `SetThrottle` es "future throttle value" (`car.c:201`).
  **Fix**: en SAMPLE, re-aplicar `SetThrottle(throttle)` + `SetHandbrake(0)` + `SetBrake(0)` cada tick antes de medir.
- **R21-002 · fixture readiness incompleto** (`:836` solo `WheelCountPresent()==WheelCount()`). EngineStart exige
  fuel + batería/bujía si vitales (`carscript.c:1991/1996/2004`). **Fix**: `vehicle_fixture_ready=true` solo si
  ruedas completas **y** `GetFluidFraction(CarFluid.FUEL)>0` **y** (si `IsVitalCarBattery/IsVitalTruckBattery` →
  batería presente) **y** (si `IsVitalSparkPlug` → bujía presente). Si no, reportar `fixture_ready=false` (no interpretar).
- **R21-003 · throttle no fail-closed** (`:877-880` toma `args.throttle>0` sin tope). **Fix**: validar `throttle`
  en `0..1` (ausente→default 1.0; fuera de rango→error de negocio `bad_throttle`) y capar `duration` a un rango de
  probe (p.ej. ≤300 ticks) ANTES de crear el job / tocar el coche.

### WARN — al X.5 (cierran limpio el DATO / robustez)
- **R21-004 · engine_on_server mismo-tick** (`:862-863`). **Fix**: re-muestrear `engine_on_server` durante
  SAMPLE/REPORT (o `engine_on_server_final`); interpretar el estado posterior, no solo el inmediato.
- **R21-005 · m_Jobs sin backpressure / concurrencia** (`m_Jobs.Insert` sin tope; probes de
  `DRIVE_PROBE_TIMEOUT_TICKS` largos sobre el mismo player). **Fix**: rechazar `vehicle_enter`/`vehicle_drive` con
  error de negocio si ya hay un job activo para el actor/subject. (Subsume C-3: además, alinear `MAX_QUEUE`≤`MAX_PENDING`
  o que `/poll` respete capacidad — opcional en el X.5.)
- **R21-006 · B2 readiness no comprueba el vehículo** (`IsSeatReady` `:735-766` no compara transport). **Fix**:
  exigir `human.GetCommand_Vehicle().GetTransport() == job.subject` antes del predicado de asiento.

### NIT — al X.5 si es barato, si no al backlog
- **R21-007 · spawn readiness sin el fallback del plan 0.4** (`:708-732` solo enumeración 2m). Decidir semántica de
  B1 (enumerable vs subject creado).
- **C-4 · radio `FindTransportNear` hardcoded 4m** → const nombrada.

## Re-certificación (gate de cierre del X.5)
Tras los fixes: bridge compila (AddonBuilder + smoke) + tests Python 3/3 OK + grep invariantes (sin ternario,
sin DestroyRestApi). Y la propiedad clave: el probe **sostiene** throttle durante SAMPLE y **gate** de fixture
mecánico completo → `speedo_max≈0` solo es posible por client-auth, no por artefacto. Luego sí: test in-game (R5).

Status: **open → X.5 (sesión 15)**.
Lección de proceso: la R21 independiente volvió a justificar su coste (Codex escaló mi WARN a FAIL + 4 findings
que no marqué). Cross-ref fase 0 (A1 tautológico).
