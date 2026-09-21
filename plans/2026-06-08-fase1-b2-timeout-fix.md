# Plan — Fix B2 (deadlines por tiempo) + instrumentación diagnóstica temporal

> Origen: el test in-game agrupado (run `_fase1/run_20260608_002308`) dio B1/negativos/S4 PASS pero
> **B2 (vehicle_enter) timeout**. Causa raíz verificada host-direct: los deadlines del bridge están en
> **ticks** (`JOB_TIMEOUT_TICKS=300`) pero el `OnTick` corre a **~6000-8000 Hz** → 300 ticks ≈ 40-50 ms,
> y la animación de "getting-in" tarda 1-3 s. B1 pasó porque su readiness es instantánea; B2/B3 dependen
> de animación. Decisiones confirmadas con usuario 2026-06-08: **(1) deadlines por tiempo de pala**;
> **(2) instrumentación diagnóstica TEMPORAL autorizada + reducir offset del coche a 2 m**.
> Etiquetas: `[EXACT]` = call-site verificado host-direct · `[DESIGN]` = cambio propuesto.

## Evidencia de la causa raíz (host-direct)
- `fase1-verdict.json`: B2 `error="timeout"`, `seated=0`; player `[6063.02,8.03,1931.91]`, coche `pos_real [6068.09,8.08,1932.02]` → dist **5.07 m**.
- `server_profiles/script_*.log`: `job queued id=16 kind=seat deadline_tick=146161` → timeout. Gap del `set_poll_delay(5s)`: `sent_tick=146807 → callback_tick=176723` = **29916 ticks / 5 s ≈ 5983 Hz** (A2 fase 0: 4741 ticks/600 ms ≈ 7900 Hz). `JOB_TIMEOUT_TICKS=300 ÷ ~6000 = 50 ms`.
- Server RPT limpio (sin excepción de `StartCommand_Vehicle`) → calibración, no crash.
- **Confound NO descartado:** dist 5.07 m podría impedir que la anim arranque aunque el timeout sea amplio → de ahí la instrumentación (parte 2).

## Unidad de tiempo (R2, verificada)
`timeslice` (param de `OnTick`, `[EXACT]` MCPBridge.c:68) está en **segundos**: el bridge ya hace `m_Accum += timeslice` y compara contra `1.0/m_PollHz` (=0.2 s) con éxito (`[EXACT]` MCPBridge.c:91-95, validado por A2). Alternativa `GetGame().GetTickTime()` (`[EXACT]` game.c:913, float) — no usada para no depender de su unidad. **Decisión: acumular `timeslice` en un campo monótono `m_ElapsedS`.**

---

## Parte 1 — Deadlines por tiempo de pared (MCPBridge.c + MCPMessages.c)

### 1.1 — Acumulador de tiempo `[DESIGN]`
- Nuevo campo `protected float m_ElapsedS;` init `0.0` en el constructor.
- En `OnTick(float timeslice)` (MCPBridge.c:68), primera línea tras `m_Tick++`: `m_ElapsedS = m_ElapsedS + timeslice;`. (Se acumula siempre, incluso pre-config, para que los deadlines sean coherentes.)

### 1.2 — Constantes: ticks → segundos `[DESIGN]` (reemplazan MCPBridge.c:6-10)
| Vieja (ticks) | Nueva (segundos) | Razón |
|---|---|---|
| `JOB_TIMEOUT_TICKS=300` | `JOB_TIMEOUT_S=5.0` | seat/spawn; anim 1-3 s + margen |
| `DRIVE_PROBE_PREP_TIMEOUT_TICKS=300` | `DRIVE_PROBE_PREP_TIMEOUT_S=5.0` | fixture `OnDebugSpawn` |
| `DRIVE_PROBE_TIMEOUT_TICKS=900` | `DRIVE_PROBE_TIMEOUT_S=12.0` | prep+ignite+drive+sample+margen |
| `DRIVE_PROBE_DEFAULT_SAMPLE_TICKS=60` | `DRIVE_PROBE_DEFAULT_SAMPLE_S=2.0` | samplear 2 s de movimiento real |
| `DRIVE_PROBE_MAX_SAMPLE_TICKS=300` | `DRIVE_PROBE_MAX_SAMPLE_S=5.0` | tope de sampling |

### 1.3 — MCPJob: campos de deadline a float `[EXACT→edit]` (MCPMessages.c:73-96)
- `int deadline_tick` → `float deadline_s`; `int prep_deadline_tick` → `float prep_deadline_s`.
- `int sample_ticks` / `int sample_ticks_target` → `float sample_start_s` / `float sample_s_target`.
- (MCPJob **no se serializa** — solo MCPResult va al wire; este cambio no toca el formato de red.)

### 1.4 — Migrar TODOS los call-sites (R7 — trazado host-direct)
| Call-site | Cambio |
|---|---|
| `[EXACT]` DispatchWorldSpawn :390 | `job.deadline_s = m_ElapsedS + JOB_TIMEOUT_S` |
| `[EXACT]` DispatchVehicleEnter :451 | `job.deadline_s = m_ElapsedS + JOB_TIMEOUT_S` |
| `[EXACT]` DispatchVehicleDriveProbe :524-525 | `deadline_s = m_ElapsedS + DRIVE_PROBE_TIMEOUT_S`; `prep_deadline_s = m_ElapsedS + DRIVE_PROBE_PREP_TIMEOUT_S` |
| `[EXACT]` DispatchVehicleDriveProbe :463-495 | validar `duration` en **segundos** (0..`DRIVE_PROBE_MAX_SAMPLE_S`); `sample_s_target = duration>0 ? duration : DRIVE_PROBE_DEFAULT_SAMPLE_S` (sin `Math.Round`) |
| `[EXACT]` ProcessJobs :772 | `else if (m_ElapsedS > job.deadline_s)` |
| `[EXACT]` ProcessDriveProbePrep :976 | `if (m_ElapsedS > job.prep_deadline_s)` |
| `[EXACT]` ProcessDriveProbeDrive :1021 | `job.sample_start_s = m_ElapsedS;` (reemplaza `sample_ticks=0`) |
| `[EXACT]` ProcessDriveProbeSample :1061-1066 | quitar `sample_ticks++`; `if (m_ElapsedS - job.sample_start_s >= job.sample_s_target) → REPORT` |

**Invariante (R7):** ningún `deadline_tick`/`prep_deadline_tick`/`sample_ticks` debe quedar tras el cambio (grep limpio). Los `tick_poll_sent/callback/dispatch` (diagnóstico de ticks en MCPResult) **se quedan** — no son deadlines.

---

## Parte 2 — Instrumentación diagnóstica TEMPORAL (autorizada 2026-06-08) `[DESIGN]`

> **TEST-ONLY. Anchor obligatorio en cada bloque: `// MCP-SEAT-DIAG TEST-ONLY — REVERTIR ANTES DE CERRAR B2`.**
> Objetivo: si B2 vuelve a fallar tras el fix de timeout, el log dice si es proximidad (anim no arranca)
> u otra cosa, SIN otro build/launch (R5). Se revierte al cerrar B2.

- Campo TEST-ONLY en MCPJob: `float last_diag_s;` (throttle del log).
- En `ProcessJobs`, para `job.kind=="seat"` (antes del check de timeout), si `m_ElapsedS - job.last_diag_s >= 0.5`:
  - `Human h = job.actor` (o GetFirstHuman); `HumanCommandVehicle vc = h.GetCommand_Vehicle();`
  - `bool gi = vc ? vc.IsGettingIn() : false;` *(Enforce no tiene `?:` — usar if/else, recordatorio del bug de fase 0)*; `int seat = vc ? vc.GetVehicleSeat() : -1;`
  - `float dist` = `vector.Distance(h.GetPosition(), job.subject.GetPosition())` (guard si subject null).
  - `Log("MCP-SEAT-DIAG id=" + job.id + " getting_in=" + gi + " seat=" + seat + " dist=" + dist + " elapsed_s=" + (m_ElapsedS - (job.deadline_s - JOB_TIMEOUT_S)));`
  - `job.last_diag_s = m_ElapsedS;`
- Sale por `Log()` (prefijo `[MCP-POC]`), así que `run-fase1.ps1` ya lo vuelca en los markers.

---

## Parte 3 — Harness (mcp_client.py modo phase1)
- **Offset del coche 5.0 → 2.0 m** (S0→S1, mitiga el confound de proximidad).
- **B3 `vehicle_drive`: `duration` en SEGUNDOS** (p.ej. `2.0`), NO 60 (ticks). Coherente con 1.2/1.4.
- Timeouts cliente: B2 ≥15 s, B3 ≥30 s (ya amplios). Sin otros cambios al harness.
- `run-fase1.ps1`: sin cambios (ya vuelca markers `[MCP-POC]`, que incluyen SEAT-DIAG).

## Criterios de aceptación (R26)
| Pieza | Criterio |
|---|---|
| Timeout fix | grep: 0 ocurrencias de `deadline_tick`/`sample_ticks` en bridge; `m_ElapsedS` acumula `timeslice` |
| B2 | tras el fix, `vehicle_enter` → `ok && seated && seat=="driver"` < timeout (lo decide el re-run in-game) |
| Instrumentación | si B2 falla, el log SEAT-DIAG muestra getting_in/seat/dist por job; anchor TEST-ONLY presente |
| B3 | desbloqueado si B2 pasa → DATO {fixture_ready, engine_on_server, speedo_max, pos_delta, net_strategy} |
| No-regresión | A1-A5 intactos (query_player_state síncrono no usa deadlines de job); compile OK |

## Riesgos
- `[ASSUMPTION]` 5 s basta para la anim de seat (típico 1-3 s). Si no, subir JOB_TIMEOUT_S.
- `[verify in-game]` el confound de proximidad: lo resuelve el offset 2 m + la instrumentación.
- `[ASSUMPTION]` `vector.Distance` / `GetVehicleSeat()` accesibles en el contexto del diag (verificar firma en R22).
- Enforce 1.29 **sin ternario `?:`** (mordió en fase 0) → la instrumentación usa if/else.

## Fuera de alcance
- Tool final de B3. Decisión B3 (server/client/diferir) — sigue pendiente del DATO. Refactor mayor del probe.
- Revert de la instrumentación (se hace al CERRAR B2, no ahora).
