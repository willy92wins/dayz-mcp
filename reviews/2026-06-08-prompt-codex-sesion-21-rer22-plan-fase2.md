# Prompt Codex — sesión 21 — re-R22 del plan v2 fase 2 (Observación)

Patrón: plan review (R22), modalidad **re-revisión delta** (el plan v2 ya integró los 6 hallazgos previos de Codex). Generado por Claude 2026-06-08. Copiar entre marcadores.

```
===== PROMPT INICIO =====

Tarea: **re-R22 LIGERO** del plan v2 de fase 2 (Observación) de DayZ-MCP. El plan v2 YA integró los 6 hallazgos de tu R22 anterior (4 P1 + 2 P2). Esta sesión verifica DOS cosas únicamente: (a) que cada uno de esos 6 fixes está correctamente aplicado, y (b) que integrarlos no introdujo nada nuevo que bloquee compile o falsee el verdict. NO re-revises el plan desde cero, NO rediseñes, NO implementes nada.

CARGA INICIAL OBLIGATORIA (lee antes de tocar nada)

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-08-fase2-observacion.md
   (el plan v2 a re-revisar — completo; la sección "Revisión R22 aplicada" lista los 6 fixes).
2. C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\research\2026-06-08-fase2-observacion.md
   (research consolidado; el plan debe respetar sus hallazgos — esp. CONFLICT-1 y LL-093).
3. C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\verified-apis.md
   (APIs verificadas; el bloque "Observación (fase 2)" + caveats).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPMessages.c
   (DTOs actuales; verifica que las ampliaciones del plan son tipables y consistentes — P1-1).
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c
   (patrón Insert vector->array :1227-1229, punto de dispatch :332-368, backpressure :3-5 — P1-1, wiring).
6. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_server.py
   (WHITELISTED_COMMANDS :14, rechazo :116 — P1-3).
7. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_client.py
   (--mode choices :629, output name :638 — P1-3).

NO releas los research separados `-claude.md` / `-codex.md` (ya consolidados). NO abras skills salvo para verificar un contrato concreto que el plan cite. NO re-derives el research.

FOCO DE LA RE-REVISIÓN (delta, no desde cero)

A) **Verificar cada uno de tus 6 hallazgos previos como RESUELTO / PARCIAL / NO-RESUELTO**, citando la sección del plan v2 que lo aborda y confirmando contra el código real:
   - P1-1 (array<float> vs vector): ¿existe el helper `VectorToArray` y se usa en TODOS los campos vector->array (pos/normal/orientation/direction/velocity)? ¿coincide con el patrón Insert de `MCPBridge.c:1227-1229`? ¿algún sitio sigue haciendo `a = vector` (no compila)?
   - P1-2 (orden RaycastRVProxy): ¿C1 itera y elige el nearest por `DistanceSq`, NO `results[0]`? ¿coincide con `weapon_base.c:1815-1834`?
   - P1-3 (harness Python): ¿el Paso 4 cubre whitelist (`mcp_server.py:14`), `--mode phase2` (`mcp_client.py:629`), verdict y `run-fase2.ps1` que escribe el fixture? ¿coherente con el harness real fase 0/1?
   - P1-4 (contrato fixture): ¿path, contenido JSONL, DTO, expected y productor (`run-fase2.ps1`, no el bridge) están FIJOS y no tautológicos (LL-115)?
   - P2-1 (semántica ok/error): ¿es consistente en TODOS los casos de la matriz de validación? ¿algún caso queda sin clasificar?
   - P2-2 (`ignore`): ¿está definido (opcional, "player"->cliente, default null) sin depender de un player inexistente?

B) **Regresión por la integración**: ¿los fixes introdujeron algo nuevo compile/verdict-blocking? (campos DTO referenciados pero no declarados; el bucle nearest mal escrito; el helper mal tipado; el Paso 4 incoherente con el harness; la semántica ok/error contradiciendo un paso; citas que driftaron al reescribir a v2).

C) **Cite-then-verify (R2)** de las citas NUEVAS del v2: `weapon_base.c:1815-1834`, `MCPBridge.c:1227-1229`, `mcp_server.py:14`/`:116`, `mcp_client.py:629`/`:638`. Las marcadas `[A]` en el plan (SurfaceInfo, OpenFile/FGets, layer mask melee, inventario) están explícitamente diferidas a impl — puedes spot-checkearlas pero NO bloquees por ellas salvo contradicción clara.

CRITERIO DE SEVERIDAD
- **FAIL bloqueante**: invalida compile o falsea el verdict si no se resuelve antes de implementar (helper mal tipado, `=vector` residual, DTO field no declarado, cita inexistente, semántica ok/error rota).
- **WARN mayor**: retrabajo significativo (fix parcial, caso de matriz sin cubrir, contrato fixture incompleto).
- **NIT**: mejora opcional (claridad, redacción).

NO INTRODUZCAS HALLAZGOS NUEVOS DE DISEÑO salvo que sean compile/verdict-blocking. Esto es re-revisión de la integración R22, no un rediseño. R22 de tu AGENTS.md aplica.

OUTPUT ESPERADO

Archivo: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-08-plan-review-fase2-codex.md

Estructura:
### Resumen ejecutivo
- Veredicto: approve / approve with minor changes / reject.
- 3-5 líneas.

### Estado de los 6 hallazgos previos
| Hallazgo | Estado (RESUELTO/PARCIAL/NO) | Sección plan v2 | Nota |
|---|---|---|---|
| P1-1 | ... | ... | ... |
| ... (P1-2, P1-3, P1-4, P2-1, P2-2) |

### Matriz de hallazgos nuevos (si los hay)
| ID | Sección plan | Severidad | Resumen | Resolución sugerida |
|---|---|---|---|---|
| RR22-001 | ... | FAIL/WARN/NIT | ... | ... |
Si no hay hallazgos nuevos: "Sin hallazgos nuevos."

### Cite-then-verify
- Cada cita nueva del v2: existe / no existe (con path:line real).

### Próximo paso
- APPROVE: implementación por pasos 0->4 puede empezar.
- APPROVE WITH MINOR: lista exacta de FAIL+WARN a aplicar.
- REJECT: por qué (solo si hay FAIL estructural).

Al terminar, añade un cross-link con timestamp y status=open en:
C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\codex-review-inbox.md

===== PROMPT FIN =====
```
