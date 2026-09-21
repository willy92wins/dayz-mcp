# R21 orphan-guard DayZ-MCP — CONSOLIDADO (Claude + Codex)

> Doble revisión independiente del feature orphan-proof (BUG-032). Mitades:
> `2026-06-23-orphan-guard-r21-claude.md` (workflow 30 agentes) + `2026-06-23-orphan-guard-review-codex.md`
> (codex-cli 0.130.0, gpt-5.5 xhigh). Cada hallazgo cross-verificado host-direct por Claude (receptor).

## Veredicto consolidado: UNSOUND

Las dos mitades llegaron a **UNSOUND de forma independiente** y coincidieron en los **3 P1 core**
(cross-validación fuerte). El feature no logra orphan-proofing en la topología de despliegue real (C1),
y el reclaim tiene dos defectos de seguridad reales (sobre-mata / fail-OPEN). E4 **sí** está intacto
(`allow_reuse_address=False` en `loopback.py:407`, el retry re-instancia la misma clase) — confirmado por
ambas mitades; el problema está *alrededor* del lock, no en el lock.

**Estado de la suite:** verde en el host (`59/59`, re-verificado 2× por Claude host-direct). Codex la vio
**roja (58/59)** en su sandbox porque `wmic`/`Get-CimInstance` están bloqueados ahí → `command_line_of()→None`
→ el integration de reclaim falla. En el host `command_line_of()` sí devuelve el cmdline. La roja es
artefacto de entorno, pero **expone una fragilidad real**: el reclaim depende de wmic/CIM y hace no-op
silencioso donde no estén.

## Matriz consolidada (cross-verificada host-direct)

| ID | file:line | sev | convergencia | resumen | fix |
|---|---|---|---|---|---|
| **C1** | orphan_guard.py:330,393 + server.py:561-568 | FAIL/P1 | Claude (WD-1/RCL-4/COV-1) + Codex (OG-P1-03) | Watchdog vigila el launcher A del venv (que sobrevive a Claude) → nunca dispara; reclaim ve A vivo → no recupera. Ni protege ni recupera en `Claude Code→A→B`. | Gated en gate in-vivo (decide si/cómo): recrear venv `--copies` (sin launcher) / vigilar ancestro no-python / trigger por EOF de stdin |
| **RCL-DISC** | orphan_guard.py:389 | FAIL/P1 | Claude + Codex (OG-P1-01) | `"dayz_mcp" in cmdline.lower()` es substring del cmdline COMPLETO incl. path; el venv vive bajo `DayZ_MCP_dev` → cualquier python del venv lo satisface. Exploit verificado por Codex: `<venv>\python.exe -m http.server --port 8765` con parent muerto → `should_reclaim` True. | Token exacto `-m dayz_mcp` (espejo de `_cmdline_targets_port`) |
| **RCL-FAILOPEN** | orphan_guard.py:435 | FAIL/P1 | Claude + Codex (OG-P1-02) | `is_alive(ppid) if ppid else False`: ppid None (ToolHelp falla/race) ⇒ parent_alive=False ⇒ reclamable ⇒ mata instancia VIVA. Contradice el contrato fail-closed de :383-385. | `if ppid is None: return False` (indeterminación → no reclamar) |
| **TST-WD** | test_parent_watchdog.py:88-129 | FAIL/P1 | Claude (WD-2) + Codex (OG-P1-03) | El integration mata el parent inmediato (launcher), nunca reproduce la muerte del abuelo (C1); el skip oculta el caso real. PASS falso. | Caso 3-niveles G→A→B matando solo G; `expectedFailure` mientras C1 abierto |
| **TST-RCL** | test_port_reclaim.py:203 + orphan_guard.py:223-259 | FAIL/P1 | Claude (TST-RCL/COV-1) + Codex (OG-P1-04) | El integration inyecta `is_alive=False` → no ejerce liveness real; + entrena el falso positivo (squatter con `dayz_mcp` suelto); + cmdline retrieval frágil (None donde wmic/CIM no van). | Liveness real contra parent vivo (assert NO mata) + negativo con el path real del venv + cmdline fail-closed con log distinto |
| **WAITFAIL** | orphan_guard.py:121 | WARN/P2 | Codex (OG-P2-01); Claude lo omitió | `WaitForSingleObject` ignora el retorno → `on_parent_death`(→`os._exit(0)`) corre tras CUALQUIER retorno, incl. `WAIT_FAILED` → puede tumbar un server sano. | Disparar solo si retorno == `WAIT_OBJECT_0`; en `WAIT_FAILED` cerrar+loggear, no salir |
| **NETSTAT-EP** | orphan_guard.py:203 | WARN/P2 | Codex (OG-P2-02); Claude parcial | `parts[1].endswith(":port")` casa `0.0.0.0:port`/`[::]:port`/`[::1]:port`, no ancla a `127.0.0.1` → puede devolver el PID de otra fila. (matiz: un listener 0.0.0.0 no provoca EADDRINUSE en bind 127.0.0.1 — verificado Claude — así que el vector se estrecha a IPv6/múltiples filas 127.0.0.1) | Exigir `parts[1] == "127.0.0.1:<port>"` |
| WMIC-ML | orphan_guard.py:235-238 | WARN/P2 | Claude (RCL-WMIC) | `_wmic_command_line` solo 1ª línea tras `CommandLine=` (cmdline multilínea) + dependencia frágil de wmic. | Acumular hasta clave/EOF; Get-CimInstance primario; no-op→log distinto |
| LOCK-RACE | server.py:551-558 + loopback.stop | WARN/P2 | Claude (WD-4) | TOCTOU stop_loopback sin lock entre hilo watchdog y finally del lifespan (benigno hoy por os._exit, frágil ante refactor). | flag `_shutting_down`/lock |
| PIDREUSE/TOCTOU | orphan_guard.py:424-453,434 | PARTIAL | Claude | PID-reuse del parent / PID fluye por ~4 snapshots sin handle estable (impacto acotado). | OpenProcess una vez y operar sobre ESE handle |
| Higiene | leak handle+hilo (:114-122), exit-code os._exit vs SystemExit(2), `.sig` en %TEMP%, netstat extra en test_instance_lock | NIT | both | menores. | opcional |

## Rejected (la verificación adversarial los descartó)

- **netstat locale** (Claude): FALSO — `netstat.exe` usa `LISTENING`/`ESTABLISHED` en inglés en toda locale Windows (verificado es-ES). Los `ÉCOUTE`/`ÜBERWACHEN` son de `Get-NetTCPConnection`/`ss`.
- **0.0.0.0 first-row kill** (Claude): NARROW/FALSO — un squatter en `0.0.0.0:port` NO provoca EADDRINUSE en bind exclusivo `127.0.0.1:port` (verificado), así que esa fila no causa el conflicto que dispara el reclaim. NETSTAT-EP sigue válido vía IPv6/múltiples filas 127.0.0.1.

## Recuento

| | FAIL/P1 | WARN/P2 | PARTIAL | NIT |
|---|---|---|---|---|
| Consolidado | 5 | 4 | 1 | varios |

## Próximo paso (sin marcar orphan-proof como cerrado)

1. **Gate in-vivo** (usuario/Claude real, NO simulable): matar Claude Code de verdad y observar si B retiene el puerto (→ C1 muerde) o muere por job-object cascade / EOF de stdin (→ feature redundante). **Decide si/cómo se arregla C1.**
2. **X.5 gate-independiente** (Codex): RCL-DISC, RCL-FAILOPEN, WAITFAIL, NETSTAT-EP, cmdline robustez/fail-closed, y los 3 fixes de test. Prompt: `2026-06-23-prompt-orphan-guard-x5-codex.md`.
3. **C1**: tras el gate, X.5 separado (o cierre como redundante). Recomendación de Claude: si el orphan se confirma, probar recrear `.venv-mcp` con `python -m venv --copies` (elimina la capa launcher de raíz → el watchdog vigila a Claude directamente) antes de soluciones más complejas.

---

## Resolución C1 (2026-06-23, sesión correctiva — Claude implementó, adjudicado por el usuario)

**Estado: C1 CERRADO en código + tests; pendiente SOLO el gate in-vivo manual del usuario.**

### Evidencia empírica (host-direct, no destructiva — NO se tocó el dayz_mcp real de :8765)

1. **Topología real viva confirmada** (Win32_Process del árbol de la sesión Cowork):
   `claude.exe(597052) → A venv\python.exe(600376) → B venv\python.exe(604104)`, y **B retiene :8765**.
   `QueryFullProcessImageNameW`: B→`C:\Python314\python.exe` (intérprete base), A→`...\.venv-mcp\Scripts\python.exe`
   (== `sys.executable`, el launcher redirector de 255 KB). C1 reproducido en aislamiento: matar solo al abuelo
   deja A+B vivos reteniendo el puerto.

2. **El fix recomendado del consolidado (`--copies`) queda REFUTADO**: en Python 3.14.3 el `venv\python.exe`
   sigue siendo el redirector de 255 320 B (vs 106 328 B del base) tanto en default como con `--copies`, y
   **ambos generan A→B**. `--copies` NO colapsa la capa launcher. (Invalida la "Recomendación de Claude" de
   arriba.) La fix correcta era la **alternativa** ya listada en la mitad-Claude (`2026-06-23-orphan-guard-r21-claude.md:22`):
   *"vigilar/discriminar el 1er ancestro saltando el python.exe del venv"*.

### Fix aplicado — ancestro-walk (stdlib-only, `orphan_guard.py` + tests; cero rebuild PBO, cero re-registro)

- Nuevo `full_image_path_of(pid)` (`QueryFullProcessImageNameW`, `PROCESS_QUERY_LIMITED_INFORMATION`) +
  `walk_past_redirectors(first_ppid, redirector_path, ...)`: sube saltando ancestros cuya ruta real ==
  `sys.executable` (nuestro launcher), devuelve el 1er ancestro NO-launcher; `None` si no se resuelve
  (fail-closed). `max_depth=8` corta ciclos.
- **Watchdog** (`install_parent_death_watchdog`): vigila `walk_past_redirectors(getppid(), sys.executable)`
  en vez del padre inmediato; fallback a `getppid()` si no se resuelve (nunca peor que hoy; el fail-safe
  `decide_watchdog_action` sigue protegiendo el arranque sano).
- **Reclaim** (`try_reclaim_port`): juzga `is_alive` del **ancestro real** del listener (más allá del
  launcher), no del padre inmediato; ancestro irresoluble → preserva E4 (fail-closed). E4
  (`allow_reuse_address=False`) intacto.

### Gates pasados (R22 — qué/cómo)

- **Forward-contract de producción** verificado contra el árbol real vivo: `walk_past_redirectors(A=600376)`
  → **597052 = `claude.exe`** real (`...\claude-code\2.1.181\claude.exe`); `OpenProcess(SYNCHRONIZE)` sobre
  él → **OK → el watchdog ARMARÍA** en producción.
- **Fire-path end-to-end** con el código real (testbed G base-python → A redirector → B): matar solo G →
  puerto liberado + B sale, casi instantáneo. C1 reproducido (sin fix) y resuelto (con fix) en el mismo testbed.
- **Suite**: `79/79` verde (`.venv-mcp` unittest discover), +9 netos: 7 unit `WalkPastRedirectorsTest`,
  1 integración `test_ancestor_walk_frees_port_when_true_grandparent_dies` (reemplaza los 2 integration que
  TST-WD marcó como PASS falso), 3 unit `ReclaimAncestorWalkTest` (ancestro muerto→reclama / vivo→preserva E4
  / irresoluble→fail-closed). `py_compile` OK.

### Pendiente (NO simulable desde Cowork — gate manual del usuario)

Editable install (`pip install -e`, sin copia en site-packages) → una **sesión Cowork NUEVA** arranca el
server con el código nuevo sin re-registrar. El gate definitivo: matar el Claude Code real y comprobar que
:8765 NO queda retenido (sesión siguiente conecta limpio con tools `dayz-mcp` cargadas — verificar con
`Get-NetTCPConnection -LocalPort 8765` y/o ToolSearch, NO `claude mcp list`). Detalle en el HANDOFF.

**Nota de alcance**: el ancestro-walk arregla el huérfano con **parent muerto** (C1). La contención entre
**sesiones Cowork VIVAS** peleando por 8765 (memoria `dayz-mcp-session-port-contention`) es ortogonal — el
reclaim es fail-closed y NUNCA mata una instancia viva (correcto); su remedio sigue siendo 1 sola sesión o
puerto por-sesión (no abordado aquí).
