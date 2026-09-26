# DayZ_MCP — Auditoría adversarial, exhaustiva e independiente (Fable 5)

- **Fecha:** 2026-07-16
- **Auditor:** Claude Fable 5 (principal) + 4 sub-auditorías independientes (contrato · concurrencia/auth · lifecycle/ownership · tests/evidencia)
- **Alcance:** `DayZ_MCP_dev` (servidor Python + docs), `DayZ_MCP` (bridge Enforce), fixtures `MCPTest`, memoria durable, runbook operativo, y `MERCEDES_AMGLF_dev` solo como frontera de integración.
- **Modo:** revisión únicamente. No se modificó código, config, doc ni memoria. Única escritura: este informe. No se lanzó/cerró ningún proceso, daemon o juego; no se adquirieron leases; no se tocó TCP 8765 ni UDP 2302; no se abrió ningún `.key`.

---

## 1. Veredicto

**`SOUND-with-fixes`.**

El núcleo (coordinación FIFO, autenticación fail-closed, lifecycle de procesos, redacción de secretos) está **genuinamente bien construido y probado**: fail-closed por defecto, disciplina de locks correcta, guard de terminación con revalidación de 4 campos + SHA256 e independiente del snapshot de fase 1, y una suite real (**518/518 verde, re-ejecutada por mí**) que ejercita el componente de producción con inyección de dependencias, no fakes-del-SUT. No encontré ningún bypass de auth, corrupción de datos de jugador en producción, ni doble-ownership en el camino `--client` normal.

Se degrada de `SOUND` a `SOUND-with-fixes` por **un P1 confirmado** (el reclaim *embedded* por ancestro puede matar un daemon **sano**, violando la invariante dura LL-156 que el propio código dice proteger) y un conjunto acotado de P2/P3 (integridad de evidencia del gate H8, gate TTY de admin solo en la CLI, run "RUNNING con owner fantasma" irreconciliable sin restart, y huecos de cobertura de tests sobre el guard PS1 real y los orquestadores H8). Ninguno es un fallo pervasivo; el P1 es real pero **latente** (requiere modo embedded concurrente con un daemon huérfano sano), y casi todos los fixes son de una línea.

No es `NEEDS-WORK`: la arquitectura y el núcleo son correctos y están respaldados por evidencia. No es `SOUND` a secas: el P1 contradice una invariante documentada y la evidencia estrella (H8 "4-Codex") tiene gaps de trazabilidad que conviene cerrar antes de tratarla como prueba re-verificable.

---

## 2. Resumen ejecutivo

- **Contrato A–H:** implementado y en su mayor parte fielmente documentado. A–F verificados in-game en su día; G (drivability fase 5) sigue `❓ sin empezar`; H (coordinación multiagente) verificado in-game **solo con 4 Codex** (excepción D-17), con la mitad mixta 2 Claude + 2 Codex de H1 **honestamente marcada como pendiente**. No hallé acreditación falsa de la mitad Claude — la documentación es explícita en su límite.
- **SHA256 de evidencia:** los tres hashes load-bearing declarados en HANDOFF/product-spec/validation-matrix **coinciden byte-a-byte** con los archivos en disco (verificados con `Get-FileHash`/`sha256sum`). El artefacto H8 es internamente **consistente y creíble** (4 sesiones/PID/runner-PID distintos, una `daemon_generation` compartida, timeline FIFO coherente, expiry TTL medido 120.012 s / grant 120.063 s, secret-scans en cero).
- **P1 (lifecycle):** `orphan_guard.should_reclaim_listener` no excluye un daemon; el camino *embedded* (`server.start_loopback` → `LoopbackServer(reclaim_orphans=True)` sin probe de salud previo) puede reclamar el puerto matando un daemon sano cuyo spawner ya murió — su estado normal por diseño. Confirmado a nivel de código; latente en operación `--client`-only.
- **Auth/concurrencia:** sólido. Key con `hmac.compare_digest` (tiempo constante), 401 antes de parsear body, bind loopback, retail-quarantine fail-closed, exec-enforce con chokepoint audit-before-action en ambos ingress, reservas abortadas en `finally`, cleanup fuera del condition lock, bind-first activation en el daemon. Residual conocido: BUG-035 (version-gate escribible por un key-holder) sigue abierto/documentado.
- **Tests/evidencia:** 518/518 real; el núcleo de seguridad se prueba de verdad. Huecos: el `process-guard.ps1` real (revalidación + Kill) **no lo ejecuta ningún test de la suite** (solo string-match; sí lo ejercita `process_guard_gate.py` fuera de suite); los orquestadores H8 se asertan por `inspect.getsource`; la etiqueta "4-Codex" es **auto-declarada**; dos gates escriben el **mismo** archivo de evidencia y el distribuido solo lo escribe en happy-path.
- **Incidente Box 0/0/1:** **no es un bug** — es el comportamiento correcto de un runner que clasifica un daemon ajeno como no-touch, sobre un **hueco de contrato**: "box libre" no está definido en ninguna doc, nadie es responsable del cierre normal del daemon (solo idle self-shutdown a 30 min o kill del SO), y un runner legacy que exige 0/0/0 es contractualmente incompatible con el daemon persistente D-14.

---

## 3. Hallazgos (ordenados por severidad)

> Severidad P0–P3. Clasificación: crash / exception / corruption / degradation / security / cosmetic. Cada hallazgo material verificado personalmente contra `path:line`.

### F-01 · P1 · corruption (lifecycle/reclaim) — el reclaim *embedded* por ancestro puede matar un daemon SANO

- **Archivo:línea:** `tools/dayz_mcp/orphan_guard.py:596-617` (`should_reclaim_listener`) + `:620-698` (`try_reclaim_port`); activación embedded: `tools/dayz_mcp/server.py:130-144` (`start_loopback` crea `LoopbackServer` **sin** `reclaim_orphans` → default `True` en `loopback.py:1385`) y `server.py:1122-1124` (embedded no hace `probe_status_healthy` antes de bindear, a diferencia de `daemon.py:264`).
- **Contrato afectado:** `DayZ_MCP_dev/CLAUDE.md` §"Invariante dura (LL-156)": el daemon "**NO** es reclamable por liveness de ancestro (eso mataría un daemon sano)". El comentario `orphan_guard.py:701-708` lo admite explícitamente ("a healthy detached daemon has a dead spawner and would be wrongly killed").
- **Evidencia:** `should_reclaim_listener` devuelve `True` cuando: listener python + cmdline con `-m dayz_mcp` (token exacto, `:611`) + `--port <p>` (`:613`) + ancestro real muerto (`:615`). La cmdline de un daemon (`… -m dayz_mcp --daemon --port 8765 …`, `daemon.py:328`) satisface los tres primeros y **no hay exclusión del token `--daemon` ni health-probe** en este camino. Un daemon detached cuyo spawner cerró (su estado normal por LL-156) tiene el ancestro muerto → los 4 criterios se cumplen → `kill_pid(daemon)`.
- **Escenario reproducible:** (1) sesión `--client` lanza el daemon detached; (2) esa sesión termina — el daemon sobrevive sano (LL-156 funcionando); (3) alguien ejecuta `python -m dayz_mcp --embedded --port 8765` (comando documentado en `README-mcp.md:46`) o bare `-m dayz_mcp` en la misma caja; (4) el bind embedded falla `EADDRINUSE` → `try_reclaim_port` → `should_reclaim_listener=True` → **TerminateProcess sobre el daemon sano**; (5) el embedded se queda el puerto con `coordination=None` → las mutaciones dejan de exigir lease y el retail-probe desaparece del camino `/enqueue` (degradación silenciosa del enforcement para las N sesiones que servía el daemon).
- **Reachability (honesto):** latente. En la operación declarada (todas las sesiones interactivas `--client`; embedded solo CI/offline/gates 0-4) no se dispara a diario. Se dispara si (a) alguien corre el comando `--embedded` documentado, o un gate 0-4, mientras el daemon está vivo, y (b) el ancestro muerto del daemon resuelve como confirmado-muerto (`walk_past_redirectors` no devuelve `None`; si devuelve `None` el reclaim aborta fail-closed, `:675-677`). Impacto real cuando dispara: matar un proceso sano ajeno + bypass del enforcement de coordinación.
- **Nota de método:** una de mis dos sub-auditorías de lifecycle marcó esto P1 trazando el camino embedded; la otra lo declaró "BLOCKED" mirando solo el reclaim health-gated del daemon-vs-daemon. Lo verifiqué personalmente: el camino embedded **no** pasa por el health-gate. La redundancia adversarial es lo que lo destapó.
- **Fix mínimo:** en `should_reclaim_listener`, `return False` si la cmdline contiene el token `--daemon` (el daemon ya tiene su reclaim health-gated propio). Defensa en profundidad: que embedded haga `probe_status_healthy(port,key)` antes de bindear y aborte si hay un holder sano (espejo de `daemon.run_daemon:264`), y/o no reclamar si `probe_listener_responsive(port)` es `True`.
- **Test de regresión:** `try_reclaim_port` con listener `cmdline="…python.exe -m dayz_mcp --daemon --port 8765 --keyfile K"`, imagen python, ancestro muerto → debe devolver `False` y `kill` **no** invocado (espejo de `tools/tests/test_port_reclaim.py`).

### F-02 · P2 · corruption (acceptance/evidence) — la etiqueta "4-Codex" del gate H8 es auto-declarada

- **Archivo:línea:** `tools/_session_coordination/h8_real_codex_gate.py:87-105` (`--client-platform codex` auto-pasado) + `:164` (`_identity_from` lo "verifica" — tautología de plataforma); roster `h8_distributed_codex_gate.py:104-140` (valida shape/unicidad, no procedencia externa). Artefacto: `reviews/2026-07-16-h8-real-4-codex.json` (`task_id` de B/C/D son **paths** — `/root/h8_gate_b`, `/root/h8_gate_c`, `/root/h8_gate_b/h8_gate_d_child` con D **anidado dentro de B**, inconsistente con "4 tareas hermanas de primer nivel").
- **Contrato afectado:** excepción D-17 / product-spec §H8: "cuatro sesiones Codex fresh … cuatro `session_id` y PID distintos, una misma `daemon_generation`, la secuencia completa y el cierre limpio".
- **Evidencia:** lo que el JSON **sí** demuestra (verificado por mí): 4 PIDs runner únicos (91060/99272/64068/31924), 4 proxies MCP distintos (64612/45120/88300/64608), 4 sesiones/leases coordinándose FIFO contra un daemon real (`58646e4f…`), con expiry TTL medido y cierre limpio. Lo que **no** demuestra: que fueran tareas **Codex** — `platform:"codex"` es un flag que el propio gate se pasa; un solo orquestador local con 4 procesos Python produce evidencia idéntica.
- **Impacto:** el gate funcional H8 (4 agentes coordinan sobre un daemon) está **legítimamente** probado; la parte "específicamente Codex" del claim descansa en auto-etiquetado, no en verificación. No invalida H8, matiza su etiqueta.
- **Fix mínimo:** renombrar la composición a "4 distributed runners (platform label: codex)" salvo que se añada verificación externa de las tareas (p.ej. sellar cada rol con un token del spawner Codex real capturado al arrancar).
- **Test de regresión:** test que cargue el JSON y afirme invariantes internos (PIDs/sesiones únicos, generación compartida, counts==timeline, `no_session_release` para C) — convierte el artefacto en re-verificable (ver F-06).

### F-03 · P2 · degradation (test coverage) — el `process-guard.ps1` real no lo ejecuta ningún test de la suite

- **Archivo:línea:** `tools/tests/test_process_lifecycle.py:65-80` (`FakeGuard`), `:473-480` (único test que toca el ps1, por `assertIn`/orden de substrings); `tools/process-guard.ps1` (no ejecutado por la suite). PID-reuse/fingerprint-mismatch se prueban con un **snapshot fake** (capa Python), no con el kill real.
- **Contrato afectado:** la salvaguarda TOCTOU (revalidar identidad del `$proc` justo antes de `$proc.Kill()`, `process-guard.ps1:126-135`) debe funcionar en runtime, no solo existir en el source.
- **Evidencia:** los 24 tests de `ProcessLifecycleTest` usan `FakeGuard`; el único que mira el `.ps1` hace string-match del orden del código. Si la comparación del `.ps1` estuviera invertida o `Kill()` no revalidara, la suite seguiría verde.
- **Mitigación existente:** `tools/_session_coordination/process_guard_gate.py` **sí** ejercita el `.ps1` con identidades forjadas rechazadas una a una (artefacto PASS fresco), pero está **fuera de la suite** (lanza proceso). Verifiqué que el guard PS1 en sí es correcto por lectura (revalidación de 4 campos + SHA256, independiente del snapshot de fase 1, kill sobre el handle de fase 2 inmune a PID-reuse).
- **Impacto:** la única salvaguarda que efectivamente mata procesos no tiene guard de regresión ejecutable dentro de los 518.
- **Fix mínimo:** integrar el núcleo de `process_guard_gate.py` como test hermético (proceso hijo desechable efímero) que afirme (a) fingerprint-mismatch → no mata, (b) match → mata; o declarar explícitamente en el ledger que la validación runtime del `.ps1` depende de un gate out-of-suite.

### F-04 · P2 · degradation (test coverage / false-confidence) — los orquestadores H8 se asertan por `inspect.getsource`, no por ejecución

- **Archivo:línea:** `tools/tests/test_h8_distributed_gate.py:115-136,248-254,371-384` (`inspect.getsource(gate._run_a)` + `assertLess(index...)`); `_cleanup_gate` (`:138-204`) sí ejecuta, pero con todos los tool-calls parcheados y resultados fabricados por `iter([...])`.
- **Contrato afectado:** que la coordinación FIFO real entre 4 procesos (grant→authorize→release, expiry por crash, adopt) esté guardada por regresión ejecutable.
- **Evidencia:** reordenar la lógica real de `_run_a` sin cambiar el texto asertado mantendría el string-match verde. Los **helpers** (redacción, roster, expiry-evidence, cleanup-discovery) sí están bien probados con negativos.
- **Impacto:** el "gate 4-Codex funciona" no está respaldado por ejecución del orquestador en la suite; solo por la corrida real que produjo el JSON (que a su vez tiene F-02/F-06).
- **Fix mínimo:** sustituir las aserciones `inspect.getsource(...index...)` por un test que ejecute el orquestador con tool-calls inyectados (como ya hace `_cleanup_gate`) y verifique el orden real de efectos.

### F-05 · P3 · security (aceptado por threat-model) — el gate TTY de admin vive solo en la CLI; el endpoint HTTP es automatizable con la key

- **Archivo:línea:** `tools/dayz_mcp/admin_cli.py:61-66` (isatty + reason) y `:84-88,139-142` (confirmación interactiva) — correctos. Daemon-side: `tools/dayz_mcp/loopback.py:1196-1239` — `/admin/release` exige solo key + reason + `confirmation == "FORCE {lease_id}"`; el `lease_id` es legible en `/status`/`/session/status` con la misma key.
- **Contrato afectado:** diseño §6.2 ("Su modo normal rechazará invocación no interactiva … no pretende crear aislamiento de SO frente a un usuario con shell completo"). Se cumple para la CLI; **no** es una propiedad del sistema — cualquier proceso same-user con la key puede componer la confirmación y hacer force-release/reconcile por HTTP sin TTY.
- **Impacto:** coherente con el riesgo residual same-user declarado; ambas rutas **auditan** y **ninguna termina procesos** (release solo invalida el lease; reconcile solo edita el manifiesto). El matiz real: un **agente** (LLM) con la key, inducido por contenido, podría force-release el lease de otro programáticamente, y el runbook/README presentan el TTY como si fuera el control.
- **Fix mínimo:** alinear la letra de §6.2/README ("la CLI oficial exige TTY; el endpoint queda cubierto por el threat-model same-user"), o exigir en el body un nonce de un archivo escribible solo interactivamente.

### F-06 · P3 · degradation (evidence integrity) — dos gates comparten `RESULT_PATH` y el distribuido solo escribe en happy-path

- **Archivo:línea:** `tools/_session_coordination/h8_real_codex_gate.py:37` y `h8_distributed_codex_gate.py:56` escriben ambos a `reviews/2026-07-16-h8-real-4-codex.json`. `overall_pass=True` hardcodeado (`h8_distributed_codex_gate.py:372,739,1419`); el fail real es un `_require` que lanza (`:1325`) → en fallo **no se escribe** el archivo → un PASS previo queda intacto en disco. El gate "real" en cambio escribe siempre (pass o fail, `h8_real_codex_gate.py:1255-1271`).
- **Impacto:** un re-run fallido del distribuido es indistinguible de "no se corrió" mirando solo `reviews/`; el filename ("real") no casa con el schema del contenido (`dayz-mcp-h8-distributed-v1`). Estructuralmente fail-closed (fail → no escribe), pero el artefacto es un "certificado de happy-path" y puede quedar stale.
- **Fix mínimo:** separar `RESULT_PATH` por gate; sellar cada artefacto con timestamp + hash del runner + argv capturados al inicio; que `_finalize` escriba también los fallos (`overall_pass:false`).

### F-07 · P3 · degradation (state-machine) — transiciones vivas a `UNRECONCILED` retienen `owner_session_id`/`owner_lease_id`

- **Archivo:línea:** `tools/dayz_mcp/process_lifecycle.py:897` (stop fase-1 mismatch), `:975` (fase-2 cuarentena), `:1013` (fase-2 terminate-fail): fijan `state="UNRECONCILED"` sin limpiar owner. Contraste: el path exitoso→EXITED **sí** los limpia (`:1045-1046`) y `recover_after_restart` **sí** los limpia (`:264-268`).
- **Impacto:** inerte para seguridad — ninguna operación autoriza off `owner_*` en estados no-RUNNING/RUNNING_IDLE (stop exige RUNNING `:884`, adopt exige RUNNING_IDLE `:1106`), así que el owner stale no concede kill/adopt/herencia. Es inconsistencia de observabilidad: `status`/audit muestran un run UNRECONCILED "propiedad" de una sesión muerta.
- **Fix mínimo:** en esas tres rutas, `run.owner_session_id=None; run.owner_lease_id=None` junto a `state="UNRECONCILED"`, espejo de `recover_after_restart`.

### F-08 · P3 · degradation (state-machine) — run "RUNNING con owner fantasma" irreconciliable sin restart tras fallo de persistencia en release

- **Archivo:línea:** `tools/dayz_mcp/daemon.py:118-135` (el `cleanup` llama `lifecycle.release_owner`; si lanza, solo anota `run_manifest_failed` en `cleanup_degraded`, **sin retry**); el lease se invalida de todos modos (`session_coordination.py:_release_active_locked`). Estado bloqueado por `process_lifecycle.py:880-885` (stop exige lease vivo), `:1106` (adopt exige RUNNING_IDLE), `:1263-1269` (admin_reconcile rechaza RUNNING con owner).
- **Impacto:** **doble-fault** (release + fallo de disco en `runs.json`) deja un run `RUNNING` con `owner_lease_id` de un lease ya muerto; ninguna ruta lo saca salvo reiniciar el daemon (recover_after_restart → RUNNING_IDLE), lo que corta la coordinación de todas las sesiones. Fail-closed en la dirección segura (sin kills indebidos), autorreparable con restart.
- **Fix mínimo:** que `admin_reconcile` admita `RUNNING` cuyo `owner_lease_id` no coincida con ningún lease vivo del coordinator (consulta read-only), o que el cleanup reintente/deje la degradación para que un `expire_due` posterior repita `release_owner`.

### F-09 · P3 · degradation (efficiency) — el audit JSONL reescribe el fichero entero en cada append

- **Archivo:línea:** `tools/dayz_mcp/runtime_state.py:80-85` (`previous = read_text(); _atomic_write_text(previous + line)` con fsync, bajo `self._lock`). O(n) por evento hasta el cap de 5 MiB; con el `_audit_gate` serializando todos los audits del coordinator, un fichero cercano al cap ralentiza el throughput de mutaciones.
- **Impacto:** local, acotado (FIFO serializa las mutaciones de todos modos; las lecturas casi no auditan). Amplifica la ventana de C-03/F-01 en discos lentos (activación del daemon con varios appends).
- **Fix mínimo:** append incremental real (`open(path,"a")` con fsync) en vez de read-modify-write; la rotación ya existe.

### F-10 · P3 · degradation (evidence re-verifiability) — la evidencia H8 no la ingiere ningún test; skips condicionales cuentan como PASS

- **Archivo:línea:** `reviews/2026-07-16-h8-real-4-codex.json` (ningún `test_*` lo lee); `h8_real_codex_gate.py` (1271 líneas, sin ningún test unitario — no existe `test_h8_real*`). "518/518 OK" mezcla colectados con pasados; skips condicionales (`test_parent_watchdog.py:191`, `test_port_reclaim.py:370`, `skipUnless win32`) cuentan dentro del 518 y, en un entorno sin `wmic` o sin venv-redirector, degradarían a skip silencioso.
- **Impacto:** la confianza en la evidencia y en la cobertura de integración depende de la integridad del proceso, no de un guard automático.
- **Fix mínimo:** test que cargue y valide el JSON (F-02); reportar el desglose `ran/passed/skipped` y fallar el gate si hay skips en los módulos de integración security-critical.

### Residuales conocidos, verificados como presentes (no elevados a hallazgo nuevo)

- **BUG-035 (P2, abierto):** el version-gate lee `_poll_versions[peer]`, escribible por cualquier key-holder vía `/poll?peer=server&ver=<ok>` (`loopback.py:361-364,505`). Confirmado presente; documentado (threat-model integridad single-user, no boundary break).
- **BUG-021/028 (P3, abierto):** `world_time_set.year` sin rango en la capa Enforce; el `/enqueue` directo no revalida args de `world_time_set`/`world_spawn` (solo la capa FastMCP y el bridge). Confirmado; documentado, requiere rebuild PBO.
- **BUG-024 (P3, abierto):** timeout de tool deja `_results`/`_enqueued_at` huérfanos y la ráfaga zombie ≤64 al reconectar; mitigado por el reconnect-flush BUG-041 (`loopback.py:512-528`). Documentado.
- **BUG-032 C1 (gated):** watchdog parent-death en topología redirector venv — la integración `test_parent_watchdog::WatchdogIntegrationTest` **sí** ejercita el ancestor-walk (base≠venv confirmado), pero el ledger lo lista "eficacia en producción SIN confirmar". Posible ledger stale (P3 cosmetic): reconciliar la nota con el test que lo valida.
- **BUG-040 (P3, defendido):** `vehicle_get_in_client` sin `RestoreGameplay()`, defendido por la regla no-freecam de la escalera. Sin cambios.
- **C1-embedded IsFiniteFloat (Enforce):** el helper actual del bridge **sí** rechaza Inf (`MCPBridge.c:1665`, `value > float.MAX || value < -float.MAX`), a diferencia de lo que sugiere el texto de BUG-011; el residuo real de BUG-011 es "no se llama en todos los sitios" (fase-2, backlog, requiere rebuild).

---

## 4. Matriz A–H resumida

| Grupo / criterio | Estado declarado | Estado real (verificado) | Evidencia |
|---|---|---|---|
| **A1–A5** transporte & readiness | ✓ in-game 2026-06-07 | Implementado + probado in-game (POC fase 0) | product-spec §A; bridge `MCPBridge.c`; suite |
| **B1–B2** spawn + seat | ✓ in-game 2026-06-08 | Implementado + probado in-game | product-spec §B |
| **B3** drive server-side | probe (client-auth), diferido a G | Correctamente diferido; no es deuda oculta | D-06; `actionstartengine.c:51-58` |
| **C1–C2** raycast + telemetry | ✓ in-game 2026-06-08 | Implementado + probado in-game | product-spec §C |
| **D1–D2** cámara + captura | ✓ in-game 2026-06-10 | Implementado + probado in-game; captura cap-aware posterior | product-spec §D; HANDOFF |
| **E1** tool surface MCP | ✓ (12 canónicas) | Implementado; **25 `@app.tool` hoy** (fase 5 + 5 session_*) → el conteo "12" de E1 está **stale** (drift documental, no defecto) | `server.py` grep `@app.tool`=25 |
| **E2** seguridad endurecida | ✓ in-game | Implementado; handshake de versión + exec gating verificados; residual BUG-035 | product-spec §E; loopback |
| **E3–E4** install + concurrencia | ✓ in-game 2026-06-10 | Implementado + probado; lock E4 reencuadrado al daemon (F) | product-spec §E |
| **F1–F5** broker multi-sesión | [verify] offline ✓; falta in-vivo | Implementado + offline-verified; el lock E4 del daemon tiene el hueco **F-01** en el camino embedded | product-spec §F; F-01 |
| **G0–G2** drivability fase 5 | ❓ sin empezar | Confirmado sin empezar (no reclamado como hecho) | product-spec §G |
| **H1** topología 2 Claude+2 Codex | ✓ offline; mixto pendiente | Offline ✓; **mixto NO in-game** (honesto) | product-spec §H1; assumptions.md |
| **H2–H7** identidad/FIFO/TTL/cleanup/audit | ✓ offline | Offline ✓ verificado en código + suite; TTL boundary `>=` correcto (`session_coordination.py:1270,1302`, 119 vigente / 120 expira) | §H; suite 518/518 |
| **H8** gate combinado real | ✓ in-game (4-Codex, D-17) | Ejecutado de verdad; **creíble** pero etiqueta "Codex" auto-declarada (**F-02**) y evidencia no re-verificable por test (**F-06/F-10**) | `2026-07-16-h8-real-4-codex.json` |

**Clasificación por madurez:** A–F implementado-y-probado-in-game; G pendiente-por-diseño; H implementado + offline + gate real 4-Codex, con H1-mixto pendiente. **No detecté ninguna afirmación documental falsa o exagerada** en la matriz — los límites (H1 mixto, G sin empezar, exec_enforce fuera de alcance en headless) están explícitos y son fieles.

---

## 5. Incidente `Box 0/0/1` — respuesta explícita

**Discovery read-only (redescubierto, no asumido):** TCP `127.0.0.1:8765` está **LISTENING** con PID **85576** = `python … -m dayz_mcp --daemon --port 8765 …` (el daemon D-14), `StartTime 2026-07-16 13:20:34`, parent 84444. **UDP 2302 ausente** (ningún juego). Es exactamente el estado "Box 0/0/1": lease=0, DayZ=0, daemon-listener=1. **No lo toqué.** (Detalle: el mismo PID 85576 aparece como `listener_pid` en la evidencia H8 de las 12:00, pero aquel proceso arrancó a otra hora → **reutilización de PID real** el mismo día, justo lo que la revalidación de creation-time del guard defiende.)

**Diagnóstico contractual (verificado en código):**

1. **Qué significa "box libre":** **no está definido en ninguna doc** (product-spec, CLAUDE, README, runbook). El sistema rastrea tres cosas **ortogonales** y nunca acuña "box libre": (a) juego DayZ vivo = proceso `DayZ*`/`DayZDiag` + owner de UDP 2302; (b) lease libre = `session_status.owner==null` y cola vacía; (c) daemon-listener en 8765. El incidente es la confusión de (c) con "box ocupada".

2. **¿Puede declararse "libre" con lease/juego/UDP liberados pero el daemon aún escuchando?** **Sí, y así debe ser.** Un daemon escuchando sin juego ni lease es el **estado estable esperado** de D-14. El propio doctor lo trata como limpio: `MULTIPLE_LISTENERS` solo dispara con >1 listener (`doctor.py:715-717`); un único daemon sano sin juego → `exit 0, findings=[]`. El juego está libre (no hay UDP 2302 / `process_counts=0`) y el lease está libre (`owner=null`), aunque el listener siga en pie.

3. **¿El protocolo distingue los cuatro estados?** **Parcialmente.** El runbook clasifica *operaciones* (lecturas / mutaciones / lifecycle) y `session_status` distingue juego-vía-`bridge_status`, lease-vía-`owner`, y daemon-vía-`daemon_generation`. Pero **ningún artefacto define "box compatible con un runner legacy que exige 0/0/0"** — esa cuarta noción no existe en el contrato. Un runner que conflaba "8765 ocupado" con "box ocupada" bloqueará por diseño.

4. **Quién es responsable del cierre normal del daemon:** **nadie explícitamente.** El daemon se cierra **solo** por (a) idle self-shutdown tras `idle_timeout_s` (default **1800 s = 30 min**) sin peticiones de cliente **y** sin polls del juego (`daemon.py:290-313`, `orphan_guard.compute_idle_seconds:838-857` + `install_idle_watchdog:860-896`), o (b) kill del SO / `KILL_ON_JOB_CLOSE` si quedó job-bound. **No existe endpoint ni comando de "stop daemon"**: `admin_release` libera un **lease**, no el daemon. Por LL-156 el daemon debe sobrevivir a su spawner, así que ninguna sesión "posee" cerrarlo. Mientras cualquier sesión `--client` lo toque (`/status`, `/enqueue`, `/await`) o el juego sondee, el idle-timer se resetea y el daemon **persiste indefinidamente**.

5. **¿Incompatibilidad contractual D-14 ↔ runners legacy 0/0/0?** **Sí, real.** El daemon persistente D-14 mantiene 8765 intencionalmente (hasta 30 min de idle, o para siempre con clientes activos). Un runner legacy que **crea su propio listener** en 8765 (modelo 0/0/0 pre-broker) es contractualmente incompatible: verá siempre 0/0/1. **Matiz importante:** el launcher de Mercedes **ya está migrado** (`MERCEDES_AMGLF_dev/tools/dayz-test.ps1` usa `dayz_mcp.lifecycle_cli`, `-RunId`, sin `Stop-Process` ni listener propio), así que un Mercedes plenamente migrado es **compatible** — habla con el daemon, no compite por el puerto. El "Box 0/0/1" surge cuando un preflight (del runner o de la escalera) trata "8765 ocupado por un proceso que no lancé" como "box ocupada" y (correctamente, por D-16) lo clasifica externo/no-touch y se bloquea.

6. **¿HANDOFF/doctor/launchers/runbook pueden inducir un cleanup indebido?** **El comportamiento observado fue el correcto** (clasificar el daemon ajeno como no-touch = D-16). El riesgo de inducción es el inverso: **ninguna doc le dice al agente que ese 0/0/1 es infra esperada y que la box está de hecho libre** para trabajar vía el daemon. Un agente sin ese contexto puede (a) bloquearse indefinidamente esperando 0/0/0, o (b) —peor— intentar "liberar" el puerto. El F-01 muestra que un intento embedded de recuperar el puerto **sí** mataría el daemon sano. El doctor **no** ofrece acción de stop (correcto), pero tampoco explica el estado.

**Conclusión del incidente:** no es un bug — es un **hueco de contrato/documentación**. El sistema se comportó de forma correcta y fail-closed. **Cambio mínimo recomendado (sin cerrar el daemon):**
- Definir "box libre" en el runbook: *juego libre (sin UDP 2302 / sin proceso DayZ) **y** lease libre (`session_status.owner=null`, cola vacía); el listener del daemon en 8765 es **infra esperada** y NO significa box ocupada.*
- Que el preflight del runner/escalera determine "libre" consultando `session_status`/`bridge_status` del daemon, **no** por "8765 ocupado".
- Documentar en README/runbook que el daemon se cierra **solo** por idle self-shutdown (30 min) o admin/SO, que eso es intencional (LL-156), y que **un runner legacy que exige su propio listener en 8765 es incompatible** y debe migrarse a `lifecycle_cli` (como Mercedes).
- (Enlaza con F-01: cerrar el hueco del reclaim embedded para que un intento de "liberar" 8765 no pueda matar el daemon sano.)

---

## 6. Tests ejecutados, resultados y omitidos

**Ejecutado por mí (ground-truth):** suite completa desde `tools/` con `.venv-mcp`:
`python -m unittest discover -s tests -t .` → **`Ran 518 tests … OK`, 37.758 s** (coincide con el "518/518, 36.647 s" declarado). Ruido esperado: `interactive_tty_required`/`confirmation_mismatch` (tests negativos del gate TTY de admin) y una excepción `serve_forever` de teardown en Windows que `test_daemon.py:92` documenta tragar.

**Aislamiento verificado antes de ejecutar:** toda la suite usa puertos efímeros (`bind(("127.0.0.1", 0))`), `TemporaryDirectory` para `LOCALAPPDATA`, y mocks para bind/kill; los pocos subprocess reales (`test_parent_watchdog`, integraciones) son pythons desechables con temp-files PID-suffixed y puertos efímeros. El único `8765` literal (`test_daemon.py`) tiene `_bind_with_reclaim`/`probe_status_healthy` **parcheados** (`:320-321`) → nunca bindea de verdad. **No toca 8765, ni 2302, ni lanza DayZ** — seguro incluso con el daemon real vivo.

**No ejecutado (y por qué):** ningún gate in-game, `h8_real`/`h8_distributed` con `--run`, `run-fase*.ps1`, `run-poc.ps1`, `process_guard_gate.py --run`, `e2e_daemon.py`/`e2e_agent_sessions.py` binarios, ni H8 real — todos lanzan procesos/daemon/juego o tocan 8765/2302/lifecycle real, prohibidos por el mandato. La suite `unittest discover` **no** incluye esos gates binarios (viven fuera de `tests/`).

**SHA256 verificados (declarado == real):**

| Artefacto | Declarado | Real (calculado) | ✓ |
|---|---|---|---|
| `reviews/2026-07-16-h8-real-4-codex.json` | `E49A4A22…CD873A` | `E49A4A224EE782C99C86C5D71F8DEE8EB502213B94683D619AF3BF2F26CD873A` | ✓ |
| `tools/_session_coordination/e2e_result.json` | `84A73F67…B36D12` | `84A73F670E13F298DFEEB319E5D90867E4A90D8B1D4722122E407F48CEB36D12` | ✓ |
| `tools/_session_coordination/h8_distributed_codex_gate.py` (runner) | `D5D8F813…DC70F9` | `D5D8F81300A05021EEF2FC12B4EA9606A6D0C7902A0D6A1726D0E1CE54DC70F9` | ✓ |

---

## 7. Cobertura y lo que no pudo verificarse

**Cubierto (personalmente, `path:line`):** contrato A–H (product-spec entero); `session_coordination.py` completo (máquina de estados, TTL boundary, reservas, cleanup); `loopback.py` (todos los endpoints, auth `compare_digest`, `/enqueue`, `/session/*`, `/lifecycle/*`, `/admin/*`, retail-quarantine, version-gate, exec-chokepoint); `daemon.py` (bind-first, reclaim health-gated, spawn); `server.py` (ClientRuntime, lazy-spawn, tools, identidad); `process_lifecycle.py` (start/stop dos-fases/adopt/release/reconcile/recover); `process-guard.ps1`; `admin_cli.py`; `runtime_state.py` (redacción + atómico); `doctor.py` (clasificación); bridge Enforce `MCPBridge.c` (validación cross-layer). Suite ejecutada. SHAs verificados. Incidente redescubierto read-only.

**Sub-auditorías:** 4 despachadas (contrato · concurrencia/auth · lifecycle/ownership · tests/evidencia). Las de **lifecycle** y **tests/evidencia** devolvieron (dos instancias cada una — la redundancia adversarial resolvió el desacuerdo P1 de F-01 y confirmó F-02/F-06). Las de **contrato** y **concurrencia/auth** no habían devuelto al cierre del informe; su alcance quedó **cubierto por mi revisión directa** (matriz A–H, todos los endpoints, `session_coordination` completo, auth). El veredicto final no se delegó.

**No verificable (fuera de alcance o imposible sin ejecutar):**
- Que el `process-guard.ps1` mate/preserve **en runtime** (F-03) — solo leído; el gate que lo prueba está fuera de suite.
- Que los 4 runners de H8 fueran tareas **Codex** (F-02) — no verificable desde la evidencia.
- Que la suite pase **hoy en otro entorno** (skips condicionales, F-10) — verifiqué que pasan y no skipean en esta máquina.
- Los gates in-game G0–G2 (sin empezar) y la mitad Claude de H1 (sin créditos) — declarados pendientes, no auditables offline.
- El "36.647 s" exacto (medí 37.758 s, dentro de varianza).

**Diferenciación de confianza (G3):** *hechos verificados* = todo lo anterior con `path:line`/checksum/ejecución. *Inferencias* = la reachability de F-01 (código confirmado; frecuencia real depende del uso de embedded), el impacto de F-08 (doble-fault). *Pendientes/hipótesis* = runtime del guard PS1, procedencia Codex de H8, comportamiento en CI distinto.

---

## 8. Correcciones mínimas priorizadas (sin implementar)

| # | Sev | Fix (una línea) | Test de regresión |
|---|---|---|---|
| 1 | **P1** | `should_reclaim_listener`: `return False` si la cmdline lleva el token `--daemon`; y que embedded haga `probe_status_healthy` antes de bindear (espejo `daemon.run_daemon:264`) | `try_reclaim_port` con cmdline `--daemon` + ancestro muerto → `False`, `kill` no invocado |
| 2 | P2 | Renombrar composición a "4 distributed runners (label: codex)" o añadir prueba externa de que las tareas son Codex | test que valide procedencia/roster contra un token del spawner |
| 3 | P2 | Integrar `process_guard_gate.py` como test hermético en la suite (o declarar la dependencia out-of-suite) | fingerprint-mismatch → no mata; match → mata |
| 4 | P2 | Reemplazar `inspect.getsource(...index...)` de H8 por ejecución del orquestador con tool-calls inyectados | orden real de efectos grant→authorize→release |
| 5 | P3 | Alinear §6.2/README: el TTY es de la CLI; el endpoint queda bajo threat-model same-user | test de que `/admin/release` con confirmación errónea → 403 (ya existe) |
| 6 | P3 | Separar `RESULT_PATH` por gate; `_finalize` escribe también fallos; sellar con hash del runner | test de que un fallo escribe `overall_pass:false` |
| 7 | P3 | Limpiar `owner_*` en los tres path→UNRECONCILED de `stop_run` (espejo `recover_after_restart`) | stop con guard_unavailable → UNRECONCILED **y** `owner_session_id is None` |
| 8 | P3 | `admin_reconcile` admite `RUNNING` con `owner_lease_id` sin lease vivo (consulta read-only) | doble-fault release → ruta admin devuelve el run a RUNNING_IDLE sin restart |
| 9 | P3 | Audit JSONL: append incremental (`open(path,"a")`+fsync) en vez de read-modify-write | perf/atomicidad bajo carga |
| 10 | P3 | Definir "box libre" en el runbook + preflight por `session_status`, no por "8765 ocupado" (incidente) | walkthrough del runner con daemon idle presente → procede |

**Nota:** el fix #1 y el #10 juntos cierran el incidente Box 0/0/1 de forma robusta: #10 evita que un agente se bloquee o intente liberar el puerto, y #1 garantiza que aunque lo intente por vía embedded, no pueda matar el daemon sano.

---

## 9. Ruta y SHA256 del informe

- **Ruta:** `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-07-16-fable-full-audit.md`
- **SHA256:** _(calculado tras la escritura atómica — ver mensaje de chat)_
