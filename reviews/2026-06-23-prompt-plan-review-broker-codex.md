# Prompt R22 — plan-review broker/daemon (para Codex CLI)

> Builder: codex-handoff-template (patrón plan-review, R22). Variante: el plan YA está implementado
> y verificado offline, así que Codex revisa el diseño + sanity-check de la implementación contra el
> plan. NO es un R21 completo (eso es una pasada aparte). Workdir sugerido para Codex:
> `-C "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev"` (read-only; no necesita escribir
> en el repo salvo el archivo de review).

===== PROMPT INICIO =====

Tarea: **R22 (doble revisión) del PLAN del refactor broker/daemon de DayZ-MCP**, con sanity-check de
que la implementación ya escrita lo realiza fielmente. Es revisión READ-ONLY: NO implementes, NO
reescribas el plan, NO toques código. Tu único output es un archivo de review (ruta abajo).

CONTEXTO: el plan resuelve que el lock exclusivo de `:8765` dejaba a toda sesión Cowork salvo una sin
tools `dayz-mcp`. Solución Python-only: un daemon standalone dueño de `:8765` habla con el juego; las
sesiones corren `--client` y proxyan por HTTP. El plan ya está IMPLEMENTADO y verificado offline
(suite unittest 113/113 + E2E binario real 5/5). Tu trabajo: ¿es el diseño sólido y la implementación
lo realiza sin huecos/regresiones?

CARGA INICIAL OBLIGATORIA (rutas absolutas; léelas completas antes de opinar)

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-23-broker-refactor.md
   (el plan a revisar — Context, decisiones a-e, puntos de diseño, no-regresión, verificación).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\product-spec.md
   (grupo de aceptación nuevo **F (F1-F5)** + Changelog 2026-06-23 + reencuadre de E4; verifica que
   el plan/impl traza a estos criterios).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\decisions\decision-log.md (D-14) +
   C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\HANDOFF.md (bloque LIVE-STATE:
   invariantes cerradas que NO deben romperse).
4. Los módulos Python del bridge:
   C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\core.py (nuevo),
   ...\dayz_mcp\daemon.py (nuevo), ...\dayz_mcp\loopback.py, ...\dayz_mcp\orphan_guard.py,
   ...\dayz_mcp\server.py.
5. Los tests nuevos:
   C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_daemon.py +
   ...\tools\tests\test_client_mode.py.
6. El E2E del binario real + su evidencia:
   C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\_broker\e2e_daemon.py +
   ...\tools\_broker\e2e_result.json.
7. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\install-mcp.ps1 (registración `--client`).

NO leas el resto del árbol (PBO, Enforce `*.c`, fases 0-3 viejas) salvo para verificar un contrato
concreto que el plan/código cite. NO ejecutes la suite (puedes asumir 113/113 según el plan; tu foco
es el razonamiento, no re-correr). Si quieres re-correr algo: `cd tools` y
`.venv-mcp\Scripts\python.exe -m unittest discover -s tests -t .` (unittest, NO pytest).

DECISIONES YA CERRADAS CON EL USUARIO (NO las re-litigues; solo evalúa si están BIEN realizadas):
(a) auto-spawn lazy detached por la 1ª sesión + discovery por `/status`; (b) cliente por defecto,
`--embedded` (=bare) como fallback; (c) first-come serializado, SIN lease, "conduce una a la vez"
documentado; (d) version-gate/exec-chokepoint/lock E4/orphan-guard en el DAEMON, cliente solo proxya;
(e) registración pasa a `--client`.

DIMENSIONES DE LA REVISIÓN (prioriza las marcadas ★ — son las de mayor riesgo de este refactor)

1) ★ **Reclaim vs daemon sano**: el plan afirma que un daemon DEBE sobrevivir a su sesión, y que por
   eso (i) el daemon NO arma parent-death watchdog y (ii) su reclaim se decide por HEALTH-PROBE
   (`orphan_guard.probe_status_healthy` / `try_reclaim_unresponsive_listener`), no por liveness de
   ancestro. ¿Es correcto y completo? ¿Hay algún camino por el que un daemon SANO pueda ser matado
   (por el reclaim del daemon, por el del embedded `try_reclaim_port`, o por la carrera de doble-spawn)?
   ¿Sigue cubierto C1 (un orphan EMBEDDED de parent muerto SÍ se reclama)? Confirma que los dos
   discriminadores (embedded por-ancestro / daemon por-health) están separados y no se pisan.
2) ★ **Fail-closed preservado en el split (R6)**: con el cliente proxyando por HTTP, ¿siguen
   enforced daemon-side la key, la whitelist, el gate de versión y el exec-chokepoint? Revisa el
   nuevo `409 version_blocked` en `enqueue_command` (¿guardado de verdad por "validador presente",
   sin romper el shim bare/harness sin validador?), y que `exec_enforce` no se pueda colar sin
   allowlist+audit desde el ingress HTTP del cliente.
3) ★ **No-regresión de fases 0-4 / gates R21-X.5**: el plan apuesta a que bare `-m dayz_mcp` = embedded
   y que los cambios a `loopback.py` son aditivos+guardados. ¿Algún cambio altera el comportamiento del
   camino embedded o del shim bare (`mcp_server.py`/`loopback.main()` sin validador) que los gates 0-3
   usan? ¿El `/await?remove=1` rompe algún consumidor existente (default debe seguir no-remove)?
4) **Idle del daemon**: la métrica reusa `compute_idle_seconds` mapeando `last_tool`→`last_client_request`
   (`touch_client` en /enqueue,/await,/status) + polls del juego. ¿Cubre bien "muere solo si no hay
   juego NI clientes"? ¿Hay un caso donde un cliente activo deje morir el daemon, o uno donde nunca muera?
5) **Spawn detached (Windows)**: `daemon.spawn_detached` (DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP +
   CREATE_BREAKAWAY_FROM_JOB con fallback). El plan declara la supervivencia bajo el Job-Object de Cowork
   como gate in-vivo (no fabricable offline). ¿La degradación elegante (si no rompe el job, muere con el
   spawner → self-healing por re-spawn) es real en el código (el `_call` del cliente re-spawnea on
   connection-refused)? ¿Algún leak de handles/fd en el spawn?
6) **Consistencia plan↔implementación**: ¿lo que el plan describe es lo que el código hace? Señala
   cualquier deriva (símbolo/flag/ruta del plan que no exista en el código, o comportamiento del código
   no descrito en el plan).
7) **Trazabilidad a AC**: cada F1-F5 del product-spec, ¿está respaldado por un test/E2E concreto? ¿Algún
   criterio sin verificación o con verificación tautológica/vacía (tell: assert que pasa por construcción)?
8) **Cobertura de test**: ¿faltan fixtures negativos o casos de carrera relevantes (doble-spawn,
   daemon muere mid-comando, key incorrecta, version_mismatch en cliente)?

CRITERIO DE SEVERIDAD
- **FAIL bloqueante**: rompe fail-closed, mata un daemon sano, regresa un gate 0-4, o el código no
  realiza una decisión cerrada (a-e).
- **WARN mayor**: retrabajo significativo si se ignora (hueco de cobertura, race no manejada, deriva
  plan↔código no trivial).
- **NIT**: mejora opcional (claridad, naming, comentario).

NO INTRODUZCAS UN REDISEÑO. Findings sobre lo que hay, no un plan alternativo. Si crees que el enfoque
entero debe replantearse, dilo en el resumen ejecutivo en 3-5 líneas, sin reescribirlo. Aplica R2
(cite-then-verify): cada hallazgo cita `archivo:línea` o `§sección` del plan.

OUTPUT ESPERADO

Archivo: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-23-plan-review-broker-codex.md

Estructura:
### Resumen ejecutivo
- Veredicto: approve / approve with minor changes / reject.
- 3-5 líneas de justificación (¿diseño sólido? ¿implementación fiel? ¿riesgo residual real?).
### Matriz de hallazgos
| ID | Sección/archivo:línea | Severidad | Resumen | Resolución sugerida |
|----|-----------------------|-----------|---------|---------------------|
| R22-001 | ... | FAIL/WARN/NIT | ... | ... |
### Hallazgos detallados
Por ID: cita exacta (1-3 líneas), problema, propuesta, ref a decisión/invariante si aplica.
### Cobertura
- AC F1-F5 con verificación / sin verificación.
- Invariantes del HANDOFF respetadas / en riesgo.
- Decisiones a-e bien realizadas / desviadas (cada desvío justificado o no).
### (opcional) Gap de proceso/skill
- Si algún hallazgo revela un SKILL.md/runbook/CLAUDE.md que lo habría evitado, nómbralo en 1 línea.
### Próximo paso
- approve → listo para el gate in-vivo del usuario.
- approve-with-minor → lista exacta de FAIL+WARN a aplicar.
- reject → qué replantear.

Cuando termines, devuelve también en el chat los bloques canónicos:
- **A**: archivos creados/modificados (solo el review).
- **B**: si re-corriste la suite, el output real (recuento); si no, dilo explícito.
- **C**: hallazgos abiertos (la matriz, resumida).
- **D**: una línea de handoff para la sesión que aplique los hallazgos.

===== PROMPT FIN =====
