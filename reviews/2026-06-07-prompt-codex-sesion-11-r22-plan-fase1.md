# Prompt Codex — sesión 11 · R22 plan-review fase 1 (Control)

> Patrón: plan-review (R22). Codex revisa el plan; NO rediseña. Output = veredicto + matriz de hallazgos.
> Generado 2026-06-07.

```
===== PROMPT INICIO =====

Tarea: R22 (doble revisión) del plan de fase 1 (Control) de DayZ-MCP. Revisas el plan EXISTENTE y
emites veredicto + matriz de hallazgos. NO rediseñas ni escribes un plan alternativo (findings, no
redesign).

## Carga inicial obligatoria (leer antes de nada)
1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-07-fase1-control.md
   (EL PLAN a revisar — completo).
2. C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\research\2026-06-07-fase1-control.md
   (research consolidado del que sale el plan; CONFLICT-1 B3 client-auth, CONFLICT-2 predicado de asiento).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\product-spec.md
   (grupo B = criterios B1/B2/B3 + cláusula de desafío; ¿el plan traza y respeta el Intent?).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\dayz-mcp-architecture.md
   (3 actores, §7 seguridad fail-closed, §8 fases; ¿el plan respeta el modelo server-authoritative?).
5. C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\verified-apis.md
   (APIs durables del proyecto; ¿el plan inventa alguna no verificada?).
6. C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\bug-ledger.md
   (BUGs activos; ¿el plan los tiene en cuenta?).

PARA VERIFICAR CITAS (dimensión 4, leer on-demand, NO precargar):
- Source vanilla: C:\Users\guill\OneDrive\Documentos\DayZ Projects\scripts\ (raíz; rechaza forks de mods).
- Bridge: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\
  (MCPBridge.c, MCPMessages.c, MCPCallbacks.c, MissionServer.c).
- Server Python: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\
  (mcp_server.py, mcp_client.py).

NO releas los research -claude.md / -codex.md (el consolidado basta).

## Dimensiones de la revisión
1) Consistencia interna: numeración paso0/1a/1b, dependencias (¿el probe B3 depende de B2
   correctamente?), contradicciones entre secciones, refs a archivos/líneas inexistentes.
2) Consistencia con el research consolidado: ¿el plan respeta los hallazgos? En particular
   (a) B3 tratado como client-auth/probe (CONFLICT-1), (b) predicado de asiento con constante nombrada
   NO ==0 (CONFLICT-2), (c) P2-4 sin DestroyRestApi, (d) args TIPADO (no string crudo).
3) Consistencia con product-spec grupo B + cláusula de desafío: ¿cada pieza traza a B1/B2/B3? El probe
   B3 ¿respeta el Intent ("conducir sin input SO") sin pretender ser PASS de producto? ¿Hace falta
   changelog de alcance en product-spec por la realidad client-auth de los coches PHYSICS?
4) APIs y citas [EXACT] (R2 cite-then-verify): por cada path:line del plan, abre el archivo real y
   confirma firma + que dice lo que el plan dice. Cubre vanilla (game.c:702/929, human.c:1492/1494/
   689-734, car.c, carscript.c:1980-2016, actionstartengine.c:51-58, transport.c:116/465/475,
   centraleconomy.c:37, dayzplayer.c:674) y bridge (MCPBridge.c:168/252, MCPMessages.c:8, mcp_server.py:14).
5) Cobertura de deuda + riesgos: P2-3 backpressure (¿el diseño MAX_DISPATCH_PER_TICK + cap de cola +
   m_Pending cubre "N spawns en un tick"?), P2-4 (¿bien resuelto?), drift, EngineStart gated.
6) Seguridad fail-closed (R6): ¿el plan extiende la whitelist con los 3 comandos? ¿valida args
   (type/pos) antes de CreateObjectEx? ¿algún comando con side-effects sin gate o sin límite?
7) Testing (R26): ¿cada pieza tiene criterio testable + fixture pos/neg donde aplique? ¿El probe
   distingue DATO de PASS de producto? ¿Los fixtures NO derivan de la misma lectura que miden (LL-115)?
8) Orden de implementación: paso0 -> 1a -> 1b, ¿sin ciclos? ¿el readiness map rompe el camino
   síncrono de query_player_state?

Foco especial (verifica a fondo):
- DTO args tipado (MCPArgs union): ¿sólido para JsonSerializer.ReadFromString? ¿type-mismatch (vector
  vs array<float>; int flags)? ¿`{}`->defaults OK? ¿`pos` como array<float> casa con el JSON `[x,y,z]`
  que Python manda?
- Backpressure (m_Pending + MAX_DISPATCH_PER_TICK): ¿interactúa bien con la cadencia de poll y
  m_PollInFlight? ¿puede quedar trabajo atascado o doble-despachado?
- Readiness pending-jobs (MCPJob + IsReady por tipo): ¿timeout/deadline bien? ¿la ref a Object subject
  puede quedar colgada (leak) si el job expira?

## Criterio de severidad
- FAIL bloqueante: invalida la implementación si no se resuelve antes (API inexistente, type-mismatch
  en el DTO, contradicción con research/architecture, dependencia circular, side-effect sin gate).
- WARN mayor: retrabajo significativo si se ignora (cobertura testing insuficiente, backpressure con
  hueco, criterio no testable).
- NIT: mejora opcional.

NO introduzcas hallazgos nuevos de diseño. Si crees que el plan completo debe replantearse, dilo en el
resumen ejecutivo, pero no escribas un plan alternativo.

## Output esperado
Archivo: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-07-plan-review-codex.md
Estructura:
### Resumen ejecutivo — veredicto (approve / approve with minor / reject) + 3-5 líneas.
### Matriz de hallazgos — | ID | §plan | Severidad | Resumen | Resolución sugerida | (IDs R22-001...).
### Hallazgos detallados — por ID: sección, cita exacta del plan (1-3 líneas), problema, resolución,
    ref a research/architecture/product-spec/bug si aplica.
### Cobertura — deuda P2-3/P2-4 cubierta sí/no; criterios product-spec trazados; APIs verificadas vs no.
### Próximo paso — approve / approve-with-minor (Claude aplica FAIL+WARN y reenvía) / reject.

Cuando termines, cross-linkea en:
C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\codex-review-inbox.md
(append con fecha + status=open; créalo si no existe).

===== PROMPT FIN =====
```
