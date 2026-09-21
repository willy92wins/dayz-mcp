# Council (deep) — DayZ-MCP — proveedor: Codex (gpt-5.5, reasoning xhigh)

Fecha: 2026-06-25 · Modo: claude-council deep-execution · Proveedores: Codex (único)
Calidad agente: good · Confianza: high · Follow-up: no necesario

> Codex evaluó SOLO desde el brief (declaró explícitamente que no auditó el repo).
> El bloque "Cruce contra el estado real" lo añade Claude como receptor, contrastando
> contra HANDOFF.md (header vivo) + DayZ_MCP_dev/CLAUDE.md. Marcas de confianza honestas.

---

## Recomendaciones de Codex (key recommendations)

1. **Priorizar lifecycle/contrato/observabilidad/concurrencia ANTES de más tools.** Cerrar primero el gate in-vivo del broker/daemon (P0): daemon detached bajo Job-Object, reconexión del cliente, limpieza de sesiones muertas sin perder ownership de `:8765`.
2. **Contrato versionado Bridge/Python** con `protocol_version`, `request_id`, `session_id`, `command_id`, `ttl_ms` y errores tipados (`validation_denied`, `bridge_offline`, `engine_refused`, `timeout`, `unsupported_headless`, `capture_failed`).
3. **Semántica de concurrencia explícita**: lecturas concurrentes permitidas, mutaciones serializadas por dominio (mundo, cámara, vehículo, tiempo/clima, exec), leases por recurso. El broker como árbitro, no solo proxy.
4. **Window-grab como canal auxiliar/evidencia dirigida, NO fuente de verdad**: cámara → thumbnail/crop → guardar imagen como artefacto fuera del prompt → pasar handle/resumen; combinar con raycast + world snapshot estructurado dado el tope de ~320×180.
5. **Construir la capa que falta para autonomía**: WorldState normalizado (IDs persistentes, deltas "qué cambió tras actuar", confianza por fuente engine>telemetry>visual) + evaluador de objetivos.
6. **Separar planner LLM de executor determinista** (límites, retries, locks, timeouts, validación) + policy runtime con budgets, kill switch, approval gates para exec/borrados/cambios globales y dry-run.

## Perspectiva única
Codex invierte la prioridad natural del autor: en vez de "más tools", insiste en cerrar la base operacional (lifecycle del daemon, contrato versionado, concurrencia, observabilidad) primero, porque más capacidades sobre base ambigua solo amplifican fallos. Identifica que el cuello de botella para la IA orquestadora no es el LLM sino la **representación fiable del estado del mundo y del delta tras cada acción**.

## Blind spots (declarados por Codex + del propio análisis)
- Asume el brief como única fuente; **no valida** que la madurez declarada (120/120 tests, E2E 5/5, fases PASS) sea real ni si el código ya implementa parte de lo que recomienda.
- No cuantifica esfuerzo ni latencias/RTT reales (los marca "falta probar" sin estimar).
- No cuestiona si DayZDiag (exe de desarrollo) sostiene multi-world/multi-agente estable, ni la transferibilidad a un entorno de producción.
- Señala la fragilidad del window-grab (foco/minimizado/resolución) pero no estima su coste de mantenimiento.

---

## Respuesta completa de Codex

Tomo el brief como fuente; esto es evaluación de arquitectura, no auditoría del repo.

### 1. ¿Va bien encaminado?
Veredicto: sí, la dirección es correcta y bastante madura. La decisión central buena es haber evitado UI automation/OCR y haber convertido DayZDiag en un sistema controlable por tools engine-native. Eso es mucho más defendible para QA y orquestación que simular teclado/ratón.

**Lo sólido:**
- Server-authoritative: la decisión más importante. CreateObjectEx, raycasts, vehicle control, time/weather y telemetry desde engine dan señales y acciones verificables, no inferidas.
- Bridge Enforce fino + Python como plano de control: buen split. Enforce debe quedarse como adaptador mínimo al engine; Python debe absorber MCP, queues, seguridad, observabilidad y lifecycle.
- Async-only en tick: correcto. Cualquier llamada bloqueante en OnUpdate sería deuda crítica.
- Broker/daemon: arquitectónicamente necesario si el juego solo sondea 127.0.0.1:8765. No es complicación gratuita; resuelve una restricción real.
- Fail-closed desde el inicio: bien. Para un sistema que puede spawnear, ejecutar comandos y modificar el mundo, la seguridad no puede añadirse al final.
- exec_enforce gated: correcto, y no debe convertirse en pilar por la limitación Developer-only/headless.

**Deuda o riesgo:**
- El daemon ahora es pieza crítica. Si muere, se queda stale, acepta comandos de sesiones viejas o mezcla operaciones concurrentes, todo falla de forma difícil de depurar.
- La captura visual es pragmática, no robusta. PrintWindow/window-grab es aceptable como canal auxiliar, no fuente de verdad.
- API key en query string: comprensible, pero exige higiene estricta (no loguear query completa, rotación, TTL, separación token bridge / token cliente).
- MCP stdio es buena interfaz para agentes, no necesariamente buen bus interno. Para orquestadora querrás eventos, streams, replay y coordinación; MCP como fachada, no núcleo.
- Riesgo de drift Bridge/Python sin contrato versionado.

Decisión que reconsideraría: no quitaría el broker, pero separaría explícitamente tres capas: `bridge protocol`, `daemon world-session broker`, `MCP adapter`. Que MCP no sea dueño conceptual del dominio; que sea una interfaz encima.

Riesgos/blind spots: el gate in-vivo del daemon es el riesgo número uno ahora. Falta probar latencia real, reconexión, duplicados, comandos expirados y multi-cliente agresivo. El límite visual ~320×180 impide autonomía visual seria si no se compensa con estado estructurado.

### 2. Roadmap priorizado
Veredicto: no priorizaría "más tools". Priorizaría lifecycle, contrato, observabilidad y concurrencia.

**P0 — cerrar base operacional:**
1. Gate in-vivo del broker/daemon (detached bajo Job-Object; cliente se cae/reconecta; daemon sobrevive, limpia sesiones muertas, no pierde ownership de :8765).
2. Contrato versionado Bridge/Python (protocol_version, request_id, session_id, command_id, ttl_ms, created_at; errores tipados).
3. Semántica de concurrencia (read-only concurrente; mutaciones serializadas por dominio; leases explícitos cámara/entidad/vehículo/sesión de test).
4. Observabilidad mínima seria (JSONL audit log con request_id/session_id/comando/latencia/tick/resultado/error_code; health endpoints; métricas tick budget y RTT).
5. Hardening de seguridad (exec_enforce disabled por defecto; capability profiles read_only/qa_mutation/admin/exec; token sanitizado en logs; rate limit).

**P1 — útil para QA real:**
6. World snapshot estructurado (entidades cercanas, tipo, pos, orientación, estado, vehículo, jugador, clima, hora; deltas entre snapshots).
7. Scenario harness (arrange → act → assert → cleanup; fixtures reproducibles; salida JSON).
8. Record/replay (reproducir secuencias contra fake bridge y DayZDiag).
9. Golden E2E in-vivo (spawn → camera → raycast → vehicle enter → screenshot → cleanup, ×N para cazar flakes).

**P2 — rendimiento y DX:**
10. Batching y backpressure (agrupar lecturas, limitar comandos por tick, rechazar/posponer cuando la queue crece).
11. Captura visual inteligente (crops bajo demanda, downscale, metadata de cámara, imagen como artefacto + handle/resumen).
12. Packaging (dayz-mcp daemon/client/smoke-test; config versionada; diagnóstico "por qué no tengo tools").

**P3 — capacidades nuevas:**
13. Tools de alto nivel (scenario_start/step/assert/cleanup; world_snapshot; entity_query; entity_focus_camera; wait_until(condition, timeout)).
14. Multi-world si aparece la necesidad (registry de instancias; namespace por servidor; no antes de que duela).

Blind spots: no metas IA autónoma encima hasta tener asserts, replay y world snapshots; no uses screenshot como sustituto de queries estructuradas; no escales exec_enforce.

### 3. Adaptación para IA orquestadora
Veredicto: hoy tienes tools. Para una IA orquestadora falta una capa de runtime autónomo: estado del mundo, memoria, scheduler, políticas de seguridad y acciones compuestas. El cuello de botella no es el LLM; es la representación fiable de "qué pasa" y "qué cambió después de actuar".

Arquitectura objetivo:
```
LLM planner -> task/scenario goal -> action executor -> DayZ-MCP tools
  -> observation collector -> world state / event log -> evaluator -> replan or stop
```

Capas necesarias:
- **WorldState**: estado normalizado (jugador, entidades, vehículos, clima, hora, cámara, últimos errores); IDs persistentes/aliases estables; deltas; confianza por fuente engine>telemetry>visual.
- **Action layer**: acciones compuestas (spawn_fixture, inspect_entity, drive_vehicle_to, run_mod_smoke_test, reset_scene) con precondiciones, efectos esperados, timeout y cleanup.
- **Planner/executor separado**: LLM decide objetivos; executor determinista aplica límites/retries/locks/timeouts/validación; el LLM no tiene control directo ilimitado sobre exec_enforce ni mutaciones globales.
- **Memoria** en 3 niveles: sesión (estado actual, plan activo, acciones recientes), episódica (runs anteriores, flakes, errores conocidos), conocimiento (clases, fixtures, mapas, convenciones). No mezclar memoria durable con snapshots ruidosos.
- **Feedback visual cerrado**: con ~320×180 la imagen sirve para orientación gruesa, no inspección fina. Usar visual como evidencia dirigida (camera_set → thumbnail → crop/región → combinar con raycast/snapshot → artefacto fuera del prompt). Ideal: capa local de percepción imagen → datos pequeños.
- **Concurrencia multi-agente**: broker como árbitro; leases por cámara/entidad/vehículo/dominio; event log común; roles observer/tester/admin/scenario-runner; mutaciones globales serializadas.
- **Seguridad autónoma**: budgets (nº comandos, duración, spawn count, radio); kill switch; approval gates; dry-run; auditoría replayable.

Loop mínimo:
```python
while budget.ok():
    state = observe_world()
    decision = planner.next_action(goal, state, memory)
    if not policy.allowed(decision):
        stop("policy_denied")
    result = executor.run(decision)
    memory.record(decision, result)
    verdict = evaluator.check(goal, state, result)
    if verdict.done or verdict.failed:
        break
```

La pieza que falta hoy: **un modelo de estado + evaluador de objetivos**. Sin eso, la IA solo encadena tools y mira resultados sueltos.

Riesgos/blind spots: autonomía sin observabilidad = loops caros y poco fiables; multi-agente sin locks = carreras sobre cámara/mundo/vehículos; captura visual limitada no soporta "jugar" como humano; seguridad por API key no basta cuando el agente actúa minutos sin supervisión.

---

## Cruce contra el estado real del proyecto (Claude, receptor — R22)

Fuentes: `HANDOFF.md` header vivo + `DayZ_MCP_dev/CLAUDE.md`. `[V]` = verificado en esas fuentes; `[I]` = inferencia razonable no verificada en código.

### Convergencias que VALIDAN decisiones ya tomadas
- **P0.1 gate in-vivo del broker** = exactamente la "próxima acción concreta" del HANDOFF `[V]`. Codex llega a la misma prioridad sin conocerla → señal fuerte de que es el siguiente paso correcto.
- **Tres capas (bridge / daemon broker / MCP adapter)**: el proyecto YA separa modos `--client`/`--daemon`/`--embedded` y el invariante LL-156 (daemon sobrevive a su sesión, discriminador = health-probe no parentesco) `[V]`. La recomendación de Codex de "MCP como fachada, no dueño del dominio" está parcialmente honrada.
- **exec_enforce gated + no convertirlo en pilar**: ya decidido; GATE4B-LIM documenta exec headless fuera de alcance `[V]`.
- **Async-only en tick**: ya es regla dura del proyecto `[V]`.

### Gaps REALES (lo nuevo que aporta el council)
- **Broker como árbitro, no solo proxy** `[V parcial]`: hoy el cliente "proxya por HTTP" y la serialización es global vía lock E4 del daemon; **no hay leases por dominio** (cámara/entidad/vehículo). Es el gap más accionable para multi-agente.
- **Contrato versionado extendido** `[V parcial]`: existe `MCP_BRIDGE_VERSION`/`EXPECTED_BRIDGE_VERSION` + version-gate 4-estados + algunos errores `[V]`, pero `request_id`/`session_id`/`command_id`/`ttl_ms` y el catálogo de errores tipados completo **no constan** `[I]`. Sin esto, multi-cliente + comandos expirados es frágil.
- **WorldState + deltas + evaluador de objetivos** `[I]`: las 12 tools devuelven lecturas puntuales (query_player_state, scene_raycast, telemetry_read); **no hay un snapshot agregado del mundo ni "qué cambió tras actuar"**. Codex lo marca como EL cuello de botella para autonomía. Coincido.
- **Scenario harness expuesto al agente** `[V]`: el proyecto tiene harness de TEST (run-faseN.ps1) pero NO tools `scenario_*`/`wait_until` para que el agente componga escenarios. Salto de "test que yo lanzo" a "escenario que el agente conduce".
- **Observabilidad JSONL por comando** `[I]`: hay audit de exec (allowlist+audit) y `record_poll` registra versión `[V]`, pero un JSONL uniforme con latencia/tick/request_id/error_code para TODOS los comandos no consta.
- **Policy runtime / capability profiles / budgets / kill switch** `[I]`: la seguridad hoy es fail-closed por key + allowlist; no hay perfiles de capacidad ni budgets de acción para operación autónoma prolongada.

### Matiz / desacuerdo
- Codex sugiere que MCP stdio "no es buen bus interno" y empuja a eventos/streams/replay. Cierto para el end-goal orquestador, pero **prematuro**: el proyecto está cerrando la base single→multi-sesión. El bus de eventos pertenece a la fase "orquestadora", no a la inmediata. No reescribir el transporte ahora.
- Codex no sabe que el window-grab YA está resuelto con PrintWindow + content-aware gate (delta live vs stale, 2026-06-14) `[V]`. Su recomendación de "evidencia dirigida + artefacto fuera del prompt" sigue siendo válida como capa de USO, no como fix del grab.

### Lectura de Claude (prioridad sugerida)
1. **Cerrar el gate in-vivo del broker** (ya pendiente; desbloquea todo lo demás).
2. **WorldState + deltas** como primer ladrillo de autonomía — es el cuello de botella que Codex y el estado del proyecto señalan a la vez, y no existe hoy.
3. **Leases por dominio en el daemon** (convertir broker proxy → árbitro) antes de meter un 2º agente real.
4. Contrato extendido (request_id/ttl) + JSONL uniforme como soporte transversal de 1-3.
Dejar planner/executor/policy/memoria para la fase orquestadora explícita, una vez 1-3 estén in-vivo.
