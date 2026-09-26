# Prompt — X.5 correctiva: 2 P1 harness + 2 P2 bridge · Codex sesión 9

```
===== PROMPT INICIO =====

Tarea: sesión correctiva X.5 del POC fase 0 de DayZ-MCP. Aplicar EXACTAMENTE 4 fixes de la R21 consolidada
(2 P1 de integridad del harness + 2 P2 baratos del bridge) y RE-CERTIFICAR A1-A5 con el harness arreglado.
NO añadas features, NO empieces fase 1, NO toques backpressure (diferido). Scope cerrado y confirmado con el usuario.

## Carga inicial (R2 + R12)
1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-07-r21-consolidated.md  (los 4 fixes con path:line + el gate de re-cert).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-06-poc-fase-0-roundtrip.md  (contrato A1-A5, §3 componentes, §11 APIs).
3. enforce-script-reference (skill) — para el teardown del bridge (P2-2).
Archivos a tocar: tools\run-poc.ps1 (P1-1, P1-2); DayZ_MCP\scripts\5_Mission\MCPBridge.c (P2-1, P2-2) + MissionServer.c (P2-2).

## Los 4 fixes (NO más)

### P1-1 — A1 deja de ser tautológico (run-poc.ps1)
HOY `run-poc.ps1:633-636` cae a `playerReadyPos` (lectura del propio bridge) como fixture de A1 cuando
`Get-SpawnActual` devuelve null porque el marker `spawn_actual` aún no se ha flusheado al log → A1 compara la
salida del bridge contra otra lectura del bridge → distance 0 trivial (verificado: disparó en run_045213). FIX:
- Quita el fallback a `playerReadyPos`.
- Tras player-ready, reintenta `Get-SpawnActual` en bucle hasta que el marker aparezca (timeout ~15s).
- Si el marker NO aparece tras el timeout → FALLA el gate (no inventes fixture).
- NO cambies el umbral 0.5 m ni la precisión del print: marker truncado (6063.02) vs bridge (6063.0185546875)
  = 0.0031 m < 0.5 m → pasa legítimo.

### P1-2 — verdict no puede falsear overall_pass (run-poc.ps1)
HOY si el cliente A1-A4 crashea, `mcp_client.py:288-292` escribe `{overall_pass:false}` SIN `tests`, y
`Update-PocVerdictTest` (`run-poc.ps1:363-374`) recomputa `overall_pass` solo desde los tests presentes (A5)
→ puede quedar `true`. FIX en `Update-PocVerdictTest`:
- `overall_pass=true` SOLO si A1-A5 están TODOS presentes Y todos pass, sin `error` top-level, y el cliente no
  falló. AND-ea con el `overall_pass` previo del cliente en vez de recomputar desde cero.
- Mantén además el gate de consola por `clientExitCode` (ya existe, `run-poc.ps1:803-806`).

### P2-1 — malformed-JSON dispara backoff (MCPBridge.c)
HOY `OnPollSuccess` (`MCPBridge.c:160-167`) hace `m_Backoff=0` ANTES de chequear el parse; un HTTP 200 con JSON
inválido resetea el backoff y reanuda a 5Hz → log spam si el server devuelve basura. FIX:
- Mueve `m_Backoff=0` a DESPUÉS de un parse OK.
- En parse-fail o `commands==null`, aplica backoff rate-limited (como OnPollFail).
- NO rompas el happy path: con JSON válido, backoff sigue a 0 y el dispatch procede igual.

### P2-2 — teardown del bridge (MCPBridge.c + MissionServer.c)
HOY el bridge no limpia `RestContext`/refs en fin de mission. FIX:
- `Shutdown()` en MCPBridge: clear `m_CallbackRefs`, null `m_Ctx`/refs, `m_Configured=false`.
- Llámalo desde el destructor de la modded `MissionServer` (o un hook de fin de mission).
- R2: VERIFICA en ..\scripts\ antes de usar: existencia/firma de `RestContext.reset()` (restapi.c),
  `DestroyRestApi()` (restapi.c) y el destructor de `MissionServer` (missionserver.c). Si alguna no
  existe/no aplica, haz el teardown con lo que sí exista (al menos clear de refs) y dilo en C.

## Re-certificación (gate de cierre — OBLIGATORIO)
Build PBO + 1 run (run-poc.ps1) con el harness arreglado. PASS del X.5 =
- A1 distance ≈ 0.003 m, NO 0.000 exacto (un 0 exacto = el fallback sigue firing → fix incompleto). El `target`
  del verdict debe ser ~6063.02 (marker), NO 6063.0185546875 (bridge).
- A2-A5 siguen PASS; `overall_pass=true` legítimo con A1-A5 todos presentes.
- (P1-2) demuestra la lógica: un caso donde el cliente reporta error/overall_pass=false NO acaba en true
  (razónalo o dry-run de Update-PocVerdictTest; no hace falta crashear el cliente real).
- (P2-1/P2-2) el run normal no regresiona (A1-A5 PASS, RPT sin crash, sin spam nuevo).

## Output A/B/C/D
- A — archivos tocados + 1 línea por fix.
- B — LITERAL del re-cert: A1 distance (cita el valor, debe ser ~0.003) y `target`, A2-A5, overall_pass, ruta del
  run. Cita las APIs de teardown que verificaste (path:line).
- C — Hallazgos nuevos / APIs de teardown que no existían / regresiones del happy path.
- D — Handoff: X.5 cerrado → fase 1 (control) desbloqueada. Deuda diferida: P2-3 backpressure + P2-4
  CreateRestApi-por-tick (verificar) → diseño de fase 1; P3 al bug-ledger.

## Restricciones
1. SOLO los 4 fixes + re-cert. NO fase 1, NO MCP stdio, NO backpressure, NO otras tools.
2. R2 estricto para las APIs de teardown (no inventes firmas; cítalas de ..\scripts\).
3. OneDrive: escritura atómica + read-after-write verify (no bash para verificar).
4. Sin el run con A1 distance ≈0.003 (no 0.000), el X.5 NO está cerrado.

===== PROMPT FIN =====
```

## Notas para Claude (receptor)
- Verificación clave host-direct: en el verdict del re-cert, A1 `target` debe ser `6063.02…` (marker) y distance
  ≈0.003, NO `6063.0185546875`/0.000 (bridge → fallback no quitado).
- Verificar las firmas de teardown que Codex cite (R2). Confirmar A2-A5 sin regresión en el log real del run.
