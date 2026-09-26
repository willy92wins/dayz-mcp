# Prompt next-session — RETOMAR broker DayZ-MCP (verificar/endurecer + R21 + gate in-vivo)

> Generado 2026-06-23. El broker YA está implementado y offline-verified (D-14, suite 113/113,
> E2E 5/5). Pega el bloque entre marcadores en una sesión Cowork NUEVA.

```
===== PROMPT INICIO =====

Sesión nueva. Proyecto DAYZ-MCP. Tarea: RETOMAR el refactor broker/daemon que YA está
implementado y verificado offline (D-14): (1) re-verificar offline desde cero, (2) correr un
REVIEW R21 adversarial del código del broker y aplicar findings, (3) guiar al usuario en el
gate in-vivo (no simulable desde Cowork) + re-registrar. Python-only (Enforce/PBO intactos).

CARGA INICIAL MÍNIMA (lee solo esto, en este orden; no abras más todavía)
1. C:\Users\guill\ObsidianVault\AI\00_System\workflow.md — reglas (R1/R2/R11/R18/R21).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\HANDOFF.md — LIVE-STATE: lee
   "Estado actual", "Próxima acción concreta" e "Invariantes cerradas / Broker (D-14)".
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-23-broker-refactor.md
   — el plan aprobado (a-e resueltos).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\daemon.py — el broker.
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\server.py — modos
   client/daemon/embedded + ClientRuntime.
6. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\core.py — gate de
   versión / status / exec compartido.
7. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\product-spec.md — AC; añade/cierra
   la AC del broker (grupo F) si falta.

(Para el review R21 abrirás además loopback.py + orphan_guard.py — partes broker — y
tests/test_daemon.py + tests/test_client_mode.py. El CLAUDE.md del proyecto se auto-inyecta.)

ESTADO (verificado 2026-06-23 — NO rehacer; re-verifica con read/grep, no de memoria):
- Broker IMPLEMENTADO + offline-verified. Suite 113/113 (unittest, .venv-mcp). E2E binario real
  5/5 (tools/_broker/e2e_daemon.py: P1 bind+/status, P2 round-trip juego<->cliente, P3 2 clientes
  concurrentes, P4 idle-shutdown exit0, P5 supervivencia tras salir el spawner detached).
- 3 modos de -m dayz_mcp: --client (lo que registra install; proxya + spawn lazy detached +
  re-spawn on connection-refused), --daemon (dueño standalone de :8765; idle self-shutdown SIN
  parent-death watchdog; reclaim health-gated por /status), bare/--embedded (camino de hoy intacto,
  gates 0-4 byte-estables).
- Invariante dura (LL-156): el daemon DEBE sobrevivir a su spawner -> NO parent-death watchdog, NO
  reclamable por ancestro; discriminador del reclaim = responder /status sano. El reclaim por
  ancestro del embedded (try_reclaim_port, C1) es SEPARADO y NO se toca.

OBJETIVO DE LA SESIÓN (entregable principal = review R21 del broker + fixes)
1. Re-verificar offline desde cero: correr la suite y el E2E, confirmar 113/113 + 5/5. Mira el
   "Exception in thread serve_forever" que aparece en stderr de la suite: confirma que es ruido de
   teardown de un test (unittest reporta OK) y no un thread huérfano real.
2. REVIEW R21 ADVERSARIAL del código del broker (es infra/tooling -> R21, NO R9). Ángulos mínimos:
   (a) Race de discovery: 2 daemons spawneados a la vez (daemon.run_daemon: el perdedor sale 0) -
       ¿hay ventana donde ambos bindean o ninguno?
   (b) Reclaim health-gated: ¿puede matar un daemon SANO?, ¿puede NO reclamar uno colgado (status
       no responde pero proceso vivo)? Fail-closed.
   (c) Self-heal del cliente: re-spawn on connection-refused - ¿bucle infinito / tormenta de
       spawns si el daemon crashea en loop?
   (d) Lifecycle detached vs Job-Object de Cowork (el riesgo del gate in-vivo): ¿el detach real
       sobrevive al cierre del spawner? (P5 cubre spawner intermediario, no el Job-Object.)
   (e) Fail-closed preservado en el split: key por request, whitelist, version gate (409), exec
       chokepoint - todo vive en el daemon, el cliente solo proxya; sin fugas.
   (f) Concurrencia first-come sin lease: interleave de 2 clientes (A set_camera, B capture) -
       coherencia/serialización correcta y declarada.
   (g) Embedded byte-estable: bare -m dayz_mcp sigue idéntico (gates 0-4 no regresan).
   Findings -> arreglar in-place + test, o al ledger si fuera de alcance. Re-correr suite tras fixes.
3. Guiar el GATE IN-VIVO (lo hace el usuario, no simulable desde Cowork): 2 sesiones Cowork reales
   con tools dayz-mcp cargadas a la vez (verificar con ToolSearch, NO `claude mcp list`) sobre un
   juego; matar la sesión spawner; comprobar que las otras siguen operativas o re-spawnean limpio.
   Entrega los pasos exactos. Tras el gate OK: re-registrar con install-mcp.ps1 -Register (ahora
   emite --client).

YA CERRADO (NO relitigar):
- El diseño broker (a-e) está decidido y plasmado en el plan. "Más puertos" sigue RECHAZADO. NO
  re-diseñar; esto es verificar/endurecer lo implementado.
- Idle self-shutdown (BUG-033), orphan-guard C1, lock E4 exclusivo, gate de versión, exec
  chokepoint: cerrados e integrados en el broker. NO re-derivar.

REGLAS QUE APLICAN
- R2: cite-then-verify. Re-lee el código antes de afirmar nada de su comportamiento.
- R21: review adversarial multi-ángulo de código infra implementado (read-only primero, luego
  fixes con test). Puedes lanzar subagentes en paralelo por ángulo.
- R18: ambigüedad de alcance/destino -> AskUserQuestion. El usuario decide si el R21 lo corres tú
  o se hace handoff a Codex (ya hay reviews/2026-06-23-prompt-plan-review-broker-codex.md del plan).
- R6: fail-closed es sagrado aquí; cualquier finding que abra el modelo de seguridad es P1.
- R11: conclusión arriba.

ENTREGABLE
1. Veredicto de re-verificación (suite + E2E) con números.
2. Review R21 en DayZ_MCP_dev\reviews\2026-06-DD-broker-r21-*.md (findings por severidad + verdict).
3. Fixes aplicados + suite verde de nuevo (no regresar de 113).
4. Pasos exactos del gate in-vivo + (tras OK) re-registración.
5. HANDOFF LIVE-STATE actualizado.

ENTORNO / GOTCHAS
- Tests: python -m unittest discover -s tests -t .  desde DayZ_MCP_dev\tools con
  .venv-mcp\Scripts\python.exe (NO pytest). E2E: tools/_broker/e2e_daemon.py.
- Editable install -> cambios a tools\dayz_mcp\*.py los coge una sesión nueva sin reinstalar.
- NO editar .py grandes directo en OneDrive (null bytes/truncado): edits quirúrgicos +
  read-after-write.
- ToolSearch (no `claude mcp list`) es el gate de readiness de tools MCP - `claude mcp list` da
  falsos "Failed" cuando otra sesión tiene el puerto.
- :8765 está LIBRE ahora (procesos MCP colgados matados al cierre de la sesión anterior).

PROHIBIDO en esta sesión
- Tocar Enforce / PBO (el broker es Python-only; el juego sigue sondeando un único :8765).
- Re-diseñar el broker o re-derivar idle/orphan-guard/E4.
- Romper el camino embedded (gates 0-4 byte-estables) ni el reclaim por-ancestro del embedded.
- Declarar el broker "in-vivo OK" sin el gate real del usuario (no es simulable desde Cowork).

MEJORAS DE SKILL (paso obligatorio del handoff)
Al cerrar, propón sin que te lo pidan qué SKILL.md/runbook/CLAUDE.md habría evitado/captura los
patrones de esta sesión, APPEND-only, citando el hallazgo. Aplica los aprobados.

===== PROMPT FIN =====
```
