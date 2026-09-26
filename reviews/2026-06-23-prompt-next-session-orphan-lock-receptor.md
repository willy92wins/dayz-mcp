# Prompt de arranque — sesión correctiva MCP orphan-lock (Claude receptor)

> Bootstrap para una sesión Cowork NUEVA que conduce el fix orphan-lock del server `dayz-mcp`:
> lanza Codex con el prompt ya escrito, hace de receptor (A/B/C/D), corre el gate offline + in-vivo,
> y deja el MCP self-healing. El smoke de MercedesAMGLF Fase 2 queda DESPUÉS (su bootstrap aparte).
> Origen: 2026-06-23 — el huérfano en :8765 bloqueó el smoke 2 veces; causa raíz diagnosticada y
> prompt de impl entregado a Codex.

Copia de marcador a marcador:

```
===== PROMPT INICIO =====

Sesión nueva. Fix correctivo "orphan-lock" del server MCP del proyecto DayZ-MCP. Esta sesión
implementa (vía Codex) + verifica (Claude receptor) que el server `dayz-mcp` nunca deje un proceso
huérfano reteniendo el puerto 8765 entre sesiones. NO hagas el smoke de MercedesAMGLF aquí (es la
tarea siguiente, con su propio bootstrap).

## 0. PRE-VERIFY (antes de cargar nada) — estado del MCP

El bug a arreglar se manifiesta como huérfanos en :8765. Antes de empezar, deja el entorno limpio:
- `Get-NetTCPConnection -LocalPort 8765` → si hay PID en Listen, identifícalo
  (`Get-CimInstance Win32_Process -Filter "ProcessId=<pid>"`). Si es un `python -m dayz_mcp` huérfano,
  `Stop-Process -Id <pid> -Force`. Repite hasta que :8765 esté libre y 0 procesos `dayz_mcp` vivos.
- `claude mcp list` → `dayz-mcp … √ Connected`. (Si da `Failed to connect` = sigue ocupado → vuelve a limpiar.)
- Nota: una sesión cuyo dayz-mcp falló al arrancar parece re-spawnear/orfanar en segundo plano; si el
  puerto se re-ensucia solo, mátalo otra vez y sigue — el fix de esta sesión es justo para eso.

## CARGA INICIAL MÍNIMA (no abrir más todavía, en este orden)

1. C:\Users\guill\ObsidianVault\AI\00_System\workflow.md
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\HANDOFF.md
   (bloque LIVE-STATE: invariantes cerradas; en especial "Lock E4 … NO tocar `allow_reuse_address`").
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\CLAUDE.md
   (stack stdlib-only, fail-closed R6, convenciones).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-23-prompt-implementacion-orphan-lock-codex-sesion-1.md
   (EL SPEC: diagnóstico de causa raíz + alcance watchdog + auto-reclaim + tests + bloques A/B/C/D.
   Contiene el prompt exacto que va a Codex.)

Los 3 archivos de código (`dayz_mcp\server.py`, `dayz_mcp\loopback.py`, `tests\test_instance_lock.py`)
se leen ON-DEMAND al verificar el Bloque A de Codex, NO los precargues. Invoca la skill
`codex-handoff-template` (protocolo receptor) cuando Codex devuelva su salida.

## OBJETIVO DE LA SESIÓN

Dejar el server `dayz-mcp` self-healing (watchdog de muerte-del-parent + auto-reclaim del puerto),
verificado offline y con el gate in-vivo preparado, sin tocar nada fuera de scope.

El entregable resuelve explícitamente, en orden:

(a) **Lanzar Codex** con el prompt de `2026-06-23-prompt-implementacion-orphan-lock-codex-sesion-1.md`.
    Preferente: pégalo tú en tu terminal Codex (simple, robusto). Alternativa: Claude lo lanza headless
    con `codex exec` — aplica los caveats LL-142 de la skill `codex-handoff-template` §"Lanzamiento
    headless" (stdin con `< NUL`/EOF, smoke "Reply READY" primero, flags `-C <workdir> -s workspace-write
    --skip-git-repo-check`, waiter host-direct con `Select-String "tokens used|Turn failed"`, NO Monitor bash).
(b) **Receptor (skill codex-handoff-template)**: parsear A/B/C/D. Bloque A → `Read` cada path (Codex a
    veces reclama archivos que no escribió). Bloque B → que parezca output real de unittest, no
    parafraseado; re-correr la suite tú mismo: desde `…\DayZ_MCP_dev\tools\`,
    `.venv-mcp\Scripts\python.exe -m unittest discover -s tests -t . -v` → baseline 38/38 + tests nuevos
    verdes. Bloque C → `reviews\codex-review-inbox.md`. Bloque D → base del handoff de cierre.
(c) **Scope-check por mtime**: confirmar que Codex SOLO tocó `server.py`/`loopback.py` + tests nuevos +
    `bug-ledger.md`. Cualquier toque a las 11 tools, bridge Enforce, install-mcp.ps1 o el chokepoint
    exec/versión = scope creep → rechazar y anotar.
(d) **Gate in-vivo del fix** (lo que Codex NO pudo hacer — necesita el Claude Code real):
    - **Re-registrar** si Codex cambió el editable/entry (`install-mcp.ps1 -Register` o `claude mcp` según
      proceda) para que la sesión siguiente cargue el código nuevo.
    - **Watchdog no rompe arranque sano**: tras re-registrar, `claude mcp list` → `√ Connected`. El gate
      DEFINITIVO es que la sesión siguiente (la del smoke) cargue las tools `dayz-mcp` — confírmalo
      verificando que el server arranca y NO se auto-mata (revisa stderr/log: el watchdog debe loggear el
      PPID y, si el handle del parent no está, deshabilitarse, nunca `os._exit` en un arranque normal).
    - **Reclaim funciona**: planta un huérfano de prueba (un `dayz_mcp --port 8765` cuyo parent muera) y
      confirma que un arranque posterior lo reclama y bindea; y que un proceso ajeno / `--port` distinto
      NO se mata (fail-closed E4 intacto). Pasos concretos los deja Codex en su Bloque D.
(e) **Actualizar memoria**: en `HANDOFF.md` cambiar la invariante E4 para reflejar que el server ahora es
    self-healing (watchdog + reclaim, sin tocar `allow_reuse_address`); y la fila TROUBLESHOOTING de la
    skill `dayz-mcp-verify` ("mata el squatter a mano" → "el server lo auto-recupera; si no, regresó el
    watchdog/reclaim"). Registrar el BUG-NNN del ledger como fixed. Handoff de cierre en `AI\30_Sessions\`.

## YA CERRADO (no relitigar)

- Alcance = **watchdog + auto-reclaim** (el usuario lo adjudicó 2026-06-23). No re-discutir watchdog-solo.
- Causa raíz YA diagnosticada [EXACT] en el spec — no re-derivarla.
- E4 (`allow_reuse_address=False`) se respeta; el reclaim solo retira huérfanos con parent muerto.
- Reparto: Codex implementa el Python; Claude es receptor (no implementa el Python en paralelo).

## REGLAS QUE APLICAN

- R2 cite-then-verify: cada path/firma que verifiques, ábrelo (no de memoria).
- R21: la verificación del receptor + tests offline + gate in-vivo ES el gate de este fix. NO es
  data-critical (infra/tooling) → R9/rigorous-data-audit NO aplica.
- R18: ante ambigüedad (alcance, quién lanza Codex, formato), AskUserQuestion, no presuponer.
- R22: declarar QUÉ se verificó y CÓMO; "tests verdes" solo con el output pegado.
- R11: conclusión arriba, sin floritura.
- bindfs/OneDrive: verificar escrituras con Read tool / PowerShell host-direct, no bash. Waiter de Codex
  host-direct, no Monitor bash sobre /mnt/c (LL-142).

## ENTREGABLE

1. Código del fix aceptado (server.py/loopback.py + tests) con scope limpio.
2. Suite re-corrida verde (output pegado) + gate in-vivo pasado (watchdog no rompe arranque; reclaim ok).
3. HANDOFF + skill `dayz-mcp-verify` + bug-ledger actualizados; handoff de cierre en 30_Sessions.

## PROHIBIDO en esta sesión

- Hacer el smoke de MercedesAMGLF (es la sesión siguiente; su bootstrap:
  `MERCEDES_AMGLF_dev\reviews\2026-06-23-prompt-next-session-fase2-smoke.md`).
- Tocar tools/bridge Enforce/install/chokepoint fuera del fix.
- Aceptar el Bloque B sin re-correr los tests tú mismo.
- Declarar el fix "listo" sin el gate in-vivo (watchdog no rompe arranque + reclaim verificado).

Cuando el MCP quede self-healing y verificado → el smoke de Fase 2 está desbloqueado: arranca su
bootstrap en una sesión nueva (esa sesión, al conectar limpio con el fix puesto, ES la prueba in-vivo final).

===== PROMPT FIN =====
```
