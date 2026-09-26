# Prompt Codex — sesión 28 — X.5 correctiva (implementación) fase 2

Patrón: implementation-handoff acotado (X.5). Aplica los 2 hallazgos **P1** de la R21 fase-2 (`2026-06-08-r21-fase2-codex.md`): F2-001 (allowlist de path) + F2-002 (fixture vacío). Claude verifica host-direct + orquesta deploy/in-game. Generado por Claude 2026-06-09 (citas verificadas host-direct). Copiar entre marcadores.

````
===== PROMPT INICIO =====

Tarea: aplicar **únicamente** los 2 hallazgos P1 de la R21 fase-2 (F2-001 y F2-002) editando **solo** `MCPBridge.c`. Es una sesión correctiva X.5: SOLO esos dos fixes. NO los WARN, NO harness, NO rediseño, NO autorrevisión, NO build/deploy/in-game.

## Carga inicial obligatoria (lee antes de tocar nada)
1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c — el archivo a editar. Símbolos: `ValidateTelemetryArgs` (:692-755, rama `fixture_jsonl` :729-751), `StringHasPrefix` (:1031-1044), `DispatchTelemetryFixtureJsonl` (:910-952).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-08-r21-fase2-codex.md — tu propia R21; detalle F2-001 (:26-32), F2-002 (:34-40).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-08-fase2-observacion.md — contrato. Semántica ok/error (:70-72), allowlist path (:109-117), expected del verdict (:117).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_client.py líneas **21-23, 957, 963-970** — **SOLO LECTURA** para entender los acceptance paths del harness. **NO edites este archivo.** Verás 3 rutas distintas bajo el prefijo `$mission:dayz_mcp/`: por eso el fix de F2-001 NO puede ser igualdad exacta de una sola ruta.

NO leas el código de fase 0/1 ni el resto del harness (`run-fase2.ps1`, `mcp_server.py`). NO abras skills.

## Fix 1 — F2-001: endurecer la allowlist de path (fail-closed, R6) SIN romper el harness
El harness usa **tres** rutas bajo `$mission:dayz_mcp/` (mcp_client.py:21-23): `telemetry_fixture.jsonl` (positivo), `missing_fixture.jsonl` (→`fixture_not_found`), `telemetry_bad.jsonl` (→`parse_error`). La igualdad exacta de UNA ruta que sugeriste en la R21 **rompería** los negativos `fixture_not_found` y `parse_error` (pasarían a `bad_args`). NO uses single-exact.

En `ValidateTelemetryArgs` rama `fixture_jsonl` (:729-751), implementa un predicado **deny-by-default**: aceptar `path` SOLO si cumple TODO:
- empieza por el prefijo exacto `$mission:dayz_mcp/` (ya está, :731-736), y
- el resto tras el prefijo: NO vacío, NO contiene `/`, NO contiene `\`, NO contiene `..`.
Cualquier otro caso → `validation.error = "bad_args"; return validation;`.

R2/R2.1 obligatorio: la operación de string Enforce que uses para detectar `/`, `\`, `..` (substring / index-of / char-scan) **verifícala contra el source vanilla** (clase `string`) y **cita `path:line`** en tu reporte. NO asumas que existe `IndexOf`/`Contains`. Enforce 1.29 **no tiene ternario `?:`** (error histórico de este proyecto) — usa if/else.

### Matriz de aceptación F2-001 (el código debe satisfacerla por construcción)
MUST ACCEPT (llega a `OpenFile`):
- `$mission:dayz_mcp/telemetry_fixture.jsonl`
- `$mission:dayz_mcp/missing_fixture.jsonl`
- `$mission:dayz_mcp/telemetry_bad.jsonl`
MUST REJECT → `bad_args`:
- `$profile:telemetry_fixture.jsonl`
- `$mission:dayz_mcp/../telemetry_fixture.jsonl`
- `$mission:dayz_mcp/sub/telemetry_fixture.jsonl`
- `$mission:dayz_mcp/` (vacío tras prefijo)
- cualquier path con `\`

## Fix 2 — F2-002: fixture existente pero vacío NO debe falsear éxito
En `DispatchTelemetryFixtureJsonl` (:910-952), tras `CloseFile(handle)` (:947) y antes de `telemetry.found = true` (:948), si `line_count_read == 0` devolver error en vez de éxito. Spec [EXACT]:

```c
CloseFile(handle);
if (telemetry.line_count_read == 0)
{
    telemetry.found = false;
    telemetry.parse_error = "empty_fixture";
    result.ok = false;
    result.error = "parse_error";
    result.telemetry = telemetry;
    return true;
}
telemetry.found = true;
result.ok = true;
result.telemetry = telemetry;
return true;
```

`result.error = "parse_error"` reusa el set de códigos del contrato (plan:71) — NO inventes un código nuevo (rompería el harness). El campo diagnóstico `parse_error` puede llevar `"empty_fixture"`.

### Matriz de aceptación F2-002
- fixture vacío (existe, 0 líneas) → `ok=false, error="parse_error", found=false`.
- positivo (2 líneas) → SIN cambios: `ok=true, found=true, line_count_read=2, last_valid.fixture_id=="fx2"`.
- missing → SIN cambios: `fixture_not_found`.
- bad json → SIN cambios: `parse_error`.

## Restricciones (vinculantes toda la sesión)
1. **Edita SOLO `MCPBridge.c`.** No toques `MCPMessages.c` (el DTO ya tiene `parse_error`/`found`/`line_count_read`), ni el harness, ni nada más.
2. **NO los WARN.** F2-003 (techo de `max_lines`/línea), F2-004 (`radius`/`IsFiniteFloat` vs Inf), F2-005 (schema/DTO por línea) quedan en backlog P2. **No los implementes ni parcialmente.** F2-004 te tentará porque tocas validación de args en el mismo archivo: **resiste** — `IsFiniteFloat` y `radius` se quedan como están.
3. **No uses el single-exact** que sugeriste en la R21 para F2-001 — rompe el harness (ver arriba). Harden-prefix, no exact-single.
4. **No build, no deploy, no in-game, no AddonBuilder.** El gate de carga es in-game y lo orquesta Claude (LL-117). Tú editas el source y reportas.
5. **No autorrevisión R21** ni pasadas de cleanup. Aplica los 2 fixes y para.
6. **Deny-by-default (R6).** Ante cualquier duda en el predicado de path → `bad_args`.

## Output esperado (escribe a C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-09-x5-fase2-impl-codex.md)
- **Bloque A — Archivos**: `MCPBridge.c` con los 2 diffs (line ranges + before/after exactos).
- **Bloque B — Evidencia** (no hay unittest; es Enforce): rellena AMBAS matrices de aceptación (F2-001 y F2-002): por cada path/caso, qué rama toma el código nuevo y por qué, citando la línea del código modificado. Incluye la cita `path:line` de la API string vanilla verificada para `/`,`\`,`..`.
- **Bloque C — Hallazgos**: cualquier desajuste (la API string no ofrece lo esperado; plan:110 dice "EXACTA" en el título pero "empieza por prefijo" en la regla → recomiéndalo para disambiguar, pero **no edites el plan**).
- **Bloque D — Handoff a Claude**: confirma que falta (a) verificación host-direct de Claude, (b) re-deploy PBO `P:\Mods\@DayZ_MCP`, (c) 1 run in-game agrupado (recuento de clases / `[MCP-POC] config loaded` + suite C2 sin regresión).

Al cerrar, cross-link con timestamp + status=open en C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\codex-review-inbox.md.

===== PROMPT FIN =====
````
