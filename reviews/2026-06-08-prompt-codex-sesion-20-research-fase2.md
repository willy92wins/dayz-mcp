# Prompt Codex — sesión 20 — research dual (R24) fase 2 (Observación)

> Patrón: research-dual (R24). Claude corre sus Explore en paralelo e independiente. Codex NO consolida
> (Claude consolida al recibir ambos). Generado por Claude 2026-06-08. Copiar de marker a marker.

```
===== PROMPT INICIO =====

Fase 0 (R24 del AGENTS.md global): research dual para la FASE 2 (Observación) de DayZ-MCP.

CONTEXTO PIPELINE
- Proyecto: DayZ-MCP. Repo: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\ (+ compilable
  ..\DayZ_MCP\). Memoria vault: C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\.
- Fase 0 (transporte) y fase 1 (Control) ya DONE. Fase 2 = grupo C del product-spec: C1 `scene_raycast`
  (hit estructurado: objeto/dist/normal) y C2 `telemetry_read` (datos del fixture). El Intent de C es
  "emitir verdicts de test headless SIN captura visual".
- Tu output va a:
  C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\research\2026-06-08-fase2-observacion-codex.md
- Plantilla (frontmatter + secciones fijas):
  C:\Users\guill\ObsidianVault\AI\00_System\templates\research-template.md

CARGA INICIAL (para anclar, NO para copiar)
1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\product-spec.md (grupo C, criterios C1/C2 + Intent).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\dayz-mcp-architecture.md (tool surface fase 2; menciona RaycastRVProxy).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c (el bridge donde encajan las tools: dispatch síncrono `query_player_state` vs jobs diferidos; backpressure ya validado).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\dayz-harness-apis.md y ..\ObsidianVault\AI\10_Projects\DayZ_MCP\verified-apis.md (APIs ya verificadas; NO repitas, amplía).

IMPORTANTE — INDEPENDENCIA
NO leas el archivo "-claude.md" del mismo tema antes de terminar el tuyo. La redundancia es por diseño
(R21 aplicado a research): cada lado explora sin sesgar al otro. Cuando termines tu "-codex.md", NO consolides:
avisa al usuario; Claude consolidará leyendo ambos y produciendo "2026-06-08-fase2-observacion.md" con CONFLICT-N.

PREGUNTA DE RESEARCH
¿Cómo implementar `scene_raycast` (C1) y `telemetry_read` (C2) server-side en el bridge MCP: qué APIs
verificadas (path:line del source vanilla), qué diseño (síncrono como query_player_state vs job diferido),
qué args y qué output estructurado, y qué alcance razonable tiene `telemetry_read`?

DIMENSIONES A CUBRIR (en paralelo, cite-then-verify R2 — cada hecho con path:line del source vanilla)
1) API de raycast server-side: candidatas (`DayZPhysics.RaycastRV`, `RaycastRVProxy`, variantes Bullet),
   firma exacta, parámetros (start/end o dir+dist, layers `PhxInteractionLayers`, flags, ObjIntersect),
   y qué devuelve el hit (pos, normal, parent Object, surface, component, fraction). Buscar en
   C:\Users\guill\OneDrive\Documentos\DayZ Projects\scripts\ (3_game/4_world física) y el skill dayz-physics-engine.
2) `scene_raycast` — diseño de la tool: ¿el raycast es síncrono (resultado en el mismo tick, como
   query_player_state) o necesita job diferido? Args propuestos (origen, dirección/destino, longitud, máscara
   de capas) y output estructurado (hit bool, object type, distance, normal, surface). Determinismo.
3) `telemetry_read` — alcance: ¿qué es "telemetría" para el Intent (verdicts headless)? Opciones: (a) lectura
   de propiedades de una entidad server-side (health/fuel/pos/estado/inventario) vía APIs nativas, (b) un
   fixture JSON-lines que el mod escribe/lee, (c) ambas. Qué APIs de lectura existen y son server-authoritative.
   Propón el alcance mínimo que cumpla C2 sin scope creep.
4) Prior art MCP de observación (Blender/Unreal/Unity/Godot MCP): cómo exponen raycast / scene queries /
   get_property y qué forma tiene el hit-result. Marca lo no verificable como suposición.
5) Integración con el bridge + backpressure: ¿estas tools encajan en el dispatch existente síncrono o como
   jobs (m_Jobs)? ¿Impactan el backpressure ya validado (MAX_DISPATCH_PER_TICK/MAX_PENDING/429)? Reuso vs
   nuevo camino. Deadlines por TIEMPO (el bridge ya usa m_ElapsedS; los timeouts en ticks fueron un bug de fase 1).

REGLAS QUE APLICAN (de tu AGENTS.md)
- R2 cite-then-verify: cada hecho con path:line. Lo no verificado va a "Suposiciones detectadas", NO a "Hechos verificados".
- R23: si encuentras una API durable, anótala para verified-apis.md del proyecto.
- R11: conclusión arriba, detalles después.
- Frontmatter completo (researcher: codex, date: 2026-06-08, git_commit: n/a, branch: n/a, status: draft).

ENTREGABLE
1. El archivo -codex.md en la ruta indicada, con todas las secciones de la plantilla.
2. Sección final "Recomendación para el plan (fase 2)" con tu voto sobre:
   a) ¿Qué API de raycast usar y qué campos del hit son fiables server-side?
   b) ¿scene_raycast síncrono o diferido (job)?
   c) ¿Qué alcance para telemetry_read (entidad vs fixture JSON-lines vs ambas)?

===== PROMPT FIN =====
```

## Lado Claude (lo ejecuto yo con sub-agentes Explore)
Mismas 5 dimensiones, en paralelo, cite-then-verify host-direct contra `..\DayZ Projects\scripts\` + skill
dayz-physics-engine + dayz-ui (telemetría). Output: `research/2026-06-08-fase2-observacion-claude.md`. Consolido
yo ambos en `2026-06-08-fase2-observacion.md` (CONFLICT-N) cuando me traigas el `-codex.md`.
