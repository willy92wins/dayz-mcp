# Prompt — R21 estructural del código del POC fase 0 · Codex sesión 8

```
===== PROMPT INICIO =====

Tarea: REVISIÓN estructural a fondo (R21) del código del POC fase 0 de DayZ-MCP, ya cerrado (A1-A5 PASS
in-game). Eres el detector principal de bugs; esta es una revisión INDEPENDIENTE y en paralelo a la de
Claude — NO has visto sus hallazgos y no debes pedirlos. Solo REVISAR: no apliques fixes en esta sesión
(los fixes son una pasada posterior, una vez consolidados los hallazgos de ambos).

## Carga inicial (R12 + R2)
1. C:\Users\guill\ObsidianVault\AI\00_System\workflow.md (§"Modalidades de R21", §"Sesiones correctivas X.5" para la severidad).
2. enforce-script-reference (skill) — pitfalls de RestApi/callbacks/modded class/tick-loop.
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-06-poc-fase-0-roundtrip.md (contrato + §11 APIs verificadas).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\dayz-harness-apis.md (solo si tocas una API nueva).

## Alcance — los 4 componentes (revisar TODOS)
- Bridge Enforce (server-side, el cimiento que reusan las fases 1-3):
  C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c
  + MCPMessages.c + MCPCallbacks.c + MissionServer.c
- Server Python:  C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_server.py
- Cliente de test: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_client.py
- Orquestador:    C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\run-poc.ps1

## Dimensiones a revisar (traza el DATO, no solo la presencia de código)
1. Correctness/bugs reales: tick-loop, backoff, parse JSON, correlación id<->result.
2. Enforce pitfalls: lifetime de RestCallback (GC), init de RestApi (GetRestApi/CreateRestApi: ¿idempotente?,
   ¿se puede llamar repetido sin fugar?), modded MissionServer (super, hooks), no-new en ticks periódicos.
3. Seguridad fail-closed: key en query string, 401/whitelist 400, bind 127.0.0.1, no-leak de la key en logs.
4. Recursos/lifetime: arrays de refs, crecimiento ilimitado, teardown en fin de mission.
5. Robustez/error-paths: server caído (refused vs timeout), respuestas malformadas, cola sin tope.
6. Integridad del harness de test: ¿el poc-verdict.json refleja FIELMENTE pass/fail en TODOS los caminos
   (incluido crash parcial del cliente)? ¿algún camino puede producir un overall_pass engañoso?
7. Fidelidad config->uso (traza parse->consumo->efecto): la pos del spawn, la key, el pollHz, el delay A2.
8. Simplificación/reuse (en segundo lugar, tras los bugs).

## Output — formato A/B/C/D
- A — Resumen (1-2 líneas) + veredicto: ¿la base (bridge/transporte) es sólida para construir la fase 1 encima?
- B — Hallazgos por SEVERIDAD (P1 bloquea construir encima / P2 deuda próxima sesión / P3 backlog), cada uno con
  `path:line`, qué rompe y en qué escenario se dispara. R2: toda afirmación sobre una API DayZ va citada contra
  `..\scripts\` (vanilla). Marca `[verify]` lo que no hayas confirmado en source.
- C — Falsos-positivos descartados / cosas que parecían bug y no lo son (con el por qué).
- D — Recomendación: ¿lista para fase 1, o hay P1/P2 que cerrar en una sesión correctiva X.5 antes?

## Restricciones
1. SOLO revisar. No apliques fixes ni toques código en esta sesión.
2. No re-ejecutar el POC (A1-A5 ya PASS, verificados host-direct). Revisión estática + razonamiento.
3. R2 estricto para toda API. NO inventes line numbers; cítalos del archivo real.
4. NO re-decidir invariantes cerradas (transporte async, deploy PBO, mission completa, key en query string,
   Enforce 1.29 sin ternario). Si crees que una invariante es un bug, dilo en C como observación, no la cambies.

===== PROMPT FIN =====
```

## Notas para Claude (receptor, próxima vuelta)
- Consolidar la B de Codex con mi review (`2026-06-07-r21-claude-poc-code.md`) en el inbox, por severidad, dedup.
- Verificar host-direct cualquier P1/P2 de Codex antes de aceptarlo (pre-output P2). P1 → sesión correctiva X.5
  antes de fase 1. Cruzar especialmente con mis P2-1 (CreateRestApi por-tick) y P2-2 (overall_pass engañoso).
