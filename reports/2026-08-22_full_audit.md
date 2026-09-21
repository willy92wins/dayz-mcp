# Audit completo — DayZ_MCP_dev
**Fecha:** 2026-08-22 · **Método:** 5 subagentes en paralelo (calidad de código, seguridad, documentación, tests/tooling, higiene de repo) · **Ámbito:** `tools/dayz_mcp` (~37k líneas), docs raíz, tests, estructura.

Todas las citas `path:line` fueron verificadas contra el árbol actual por los subagentes. Nada fue modificado.

---

## Resumen ejecutivo

El código del paquete tiene un nivel de hardening inusualmente alto (verificación de identidad de proceso con doble snapshot, paths sellados con handles NTFS, fail-closed sistemático, redactor de secretos, suite de ~2047 tests, dependencias pineadas y lockeadas). Los problemas reales se concentran en cinco frentes:

1. **Dos bugs críticos de bloqueo indefinido** en el daemon (handlers HTTP sin timeout y `Condition.wait()` sin deadline).
2. **Un hallazgo de seguridad alto**: el campo `executable_sha256` no hashea el binario, sino la ruta.
3. **~11 GB de snapshots muertos** en la raíz del árbol de trabajo.
4. **Documentación desincronizada**: PROJECT-MAP.md, NEXT-SESSION-PROMPT.txt y CLAUDE.md describen un estado de hace semanas/meses y pueden desorientar a la siguiente sesión.
5. **Sin CI**: 2047 tests que solo corren si alguien los ejecuta a mano.

---

## A. Bugs de código

### CRÍTICO

| # | Hallazgo | Evidencia |
|---|----------|-----------|
| A1 | **Handlers HTTP sin socket timeout.** `Handler(BaseHTTPRequestHandler)` no define `timeout`; `ThreadingHTTPServer` + `rfile.read(length)` en `_read_json` bloquea para siempre si un cliente declara `Content-Length` y no envía el cuerpo. Cada conexión atascada consume un hilo del daemon indefinidamente (fuga de hilos, DoS trivial desde localhost). | `tools/dayz_mcp/loopback.py:2355`, `:2455` |
| A2 | **`Condition.wait()` sin deadline** en dos puntos del camino de adquisición de sesión. Si el hilo que haría `notify_all` muere (excepción en `audit_failed`/`coordination_changed`), el hilo en espera queda bloqueado indefinidamente sosteniendo el handler HTTP. El resto de `wait` del módulo sí usa `remaining`; estos dos son la excepción. | `tools/dayz_mcp/session_coordination.py:671`, `:770` |

### ALTO

| # | Hallazgo | Evidencia |
|---|----------|-----------|
| A3 | **Anti-patrón `release()/acquire()` manual sobre `Condition(RLock)`** para "hacer I/O fuera del lock" (fsync de audit, persist de snapshot). El lock de un `Condition` es un RLock: si el `_…_locked` se llama anidado en otro contexto del mismo hilo (hay ≥303 llamadas `_locked(`), un solo `release()` solo decrementa la recursión y **el lock sigue sostenido durante toda la I/O**, derrotando el objetivo y pudiendo serializar el daemon. No hay aserción de profundidad que lo detecte. Fuente probable de stalls difíciles de reproducir. | `session_coordination.py:2451-2462`, `:2520-2535`, `:3143-3160`, `:3180-3230`, `:3490-3520`, `:3570-3580` |
| A4 | **107+ `except Exception` que tragan la causa sin log.** Ejemplos: `cleanup_begin` resume cualquier fallo como `"run_manifest_failed"` sin traceback; `_terminate_open_handle` devuelve `False` silencioso; `invoke_cleanup` degrada a `"cleanup_failed"` sin tipo ni mensaje. Un fallo de disco es indistinguible de un bug de código. | `daemon.py:90-105`, `process_lifecycle.py:884-905`, `loopback.py:1425-1465`, `session_coordination.py:2156-2213` |
| A5 | **`build_app` es una única función de ~1480 líneas** que define ~40 tools MCP, dos runtimes y composición. Handlers duplicados (`dayz_test_run`/`dayz_test_stop` repiten el mismo patrón de error). | `server.py:2256-3738`, `:2522-2588` vs `:3220-3238` |
| A6 | **`_activate_server_coordination` (~230 líneas)** mezcla 7 stores, recuperación de manifiesto corrupto, faults y wiring. | `daemon.py:361-589` |
| A7 | **`validated_startup_budget_s` solo valida el default.** La comprobación `budget <= required` se aplica solo si `value is None`; un caller que pase `startup_budget_s=0.5` falla luego con `TimeoutError` opaco. Además el docstring dice "typically 5-12s" cuando el default real es 40 s. | `daemon.py:150-165`, `:73`; `server.py:990`, `:770` |

### MEDIO

| # | Hallazgo | Evidencia |
|---|----------|-----------|
| A8 | **`read_key` sin `utf-8-sig`**: un keyfile con BOM produce una clave con `\ufeff` inicial (`str.strip()` no lo quita) y el HMAC falla de forma críptica. `core.py` ya corrigió esto para el allowlist. | `loopback.py:3089` vs `core.py:126`, auth en `loopback.py:2425` |
| A9 | **RPT decodificado como UTF-8**: los RPT de DayZ suelen traer cp1252/cp1251; patrones con acentos/cirílico nunca matchean, silenciosamente. | `server.py:1727`, `:1737` |
| A10 | **Código duplicado divergente**: `audit_exec` duplica verbatim `core.make_exec_auditor` y `daemon._audit_path`; `_version_block_fields` reconstruye a mano los strings de `core.version_state_for`; `call_bridge` vs `enqueue_bridge` copian el bloque POST + stale-lease casi carácter por carácter; fallback `DAYZ_GAME_PATH` hardcodeado duplica `dayz_tools_paths.py`. | `server.py:632-649`, `loopback.py:1314-1322`, `server.py:1143-1181` vs `:1231-1272`, `daemon.py:530-533` |
| A11 | **Rama muerta con `AttributeError` latente** en manejo de errores de enqueue: el `else` llama `.get()` sobre un payload que acaba de fallar `isinstance(..., dict)`. | `server.py:557-559`, `:578-581`, `:613-615` |
| A12 | **`Popen` descartado tras leer `.pid`** en `spawn_detached`: handle de proceso sin close (Windows) / zombie teórico (POSIX). | `daemon.py:993-1010` |
| A13 | **Doble chequeo de cuarentena en `stop_run`** (254 líneas) sin comentario que justifique la duplicación ni deadline visible del caller. | `process_lifecycle.py:1615`, `:1694` |

### BAJO

- A14 — Swallows menores sin telemetría: `daemon.py:945-946`, `server.py:3763-3766`, `:1173-1174`, `:2584-2588` (tupla `except (asyncio.CancelledError, Exception)` reutilizable por error).
- A15 — `_noop`/lambdas de logging definidas 4 veces (`daemon.py:168-176`, `server.py:489`, `:763`, `:3787`).
- A16 — `daemon_contract.py:48` (`argv_targets_port`) nunca valida lo que `build_daemon_argv` construye; el invariante lo defienden tests, no el contrato en runtime.

---

## B. Seguridad

**Verde:** sin `shell=True`, sin inyección de comandos, sin credenciales en logs de los módulos auditados, path traversal bien cerrado (salvo B2), locks/leases y transport loopback con acreditación fuerte del dueño del socket.

| # | Severidad | Hallazgo | Evidencia |
|---|-----------|----------|-----------|
| B1 | **ALTA** | **`executable_sha256` es hash de la RUTA, no del binario** (`sha256(b"psutil-exe-v2\0" + ruta)`). Toda la acreditación del transport y del reclaim confía en ese hash: un binario distinto en la misma ruta con mismo argv/cwd pasa todas las verificaciones. Ventana práctica limitada, pero el nombre del campo da una falsa garantía de integridad. | `native_process_guard.py:86-92`; consumidores en `accredited_daemon_transport.py:157-200`, `orphan_guard.py:674-676`, `:972-974` |
| B2 | MEDIA | **Leaf junction escape**: con `allow_root_junction=True` en el root, cualquier descendiente hoja puede ser un mount-point que resuelve fuera del árbol sellado; la identidad acreditada pasa a ser la del target. El comentario del código dice que solo el root debería permitirlo. | `request_path_authority.py:446-455` |
| B3 | MEDIA | **API key en query string** (`GET /status?key=...`): aparece en access logs y traces. Un header `Authorization` no se registraría. | `accredited_daemon_transport.py:281-283`; `orphan_guard.py:784-798` |
| B4 | MEDIA | **Sin verificación de DACL/permisos del keyfile al leer**: `pinned_keyfile` valida todo lo demás (reparse, hardlinks, tamaño, path final) pero no contrasta el modo contra la política `0o600` con la que se crea. | `pinned_keyfile.py:122-168`; creación correcta en `identity_migration.py:178`, `:365`, `:418` |
| B5 | MEDIA-BAJA | **TOCTOU verificación→kill** (PID reuse) en `terminate`: snapshot de identidad y `kill()` sin re-chequeo entre ambos. Limitación estructural de Windows. | `native_process_guard.py:194-227`; mismo patrón en `orphan_guard.py:652-695`, `:944-992` |
| B6 | BAJA | **`netstat` resuelto vía PATH**: un PATH hostil inyecta un netstat falso cuyo output alimenta el pipeline de reclaim (mitigado por re-verificación posterior). Usar `%SystemRoot%\System32\netstat.exe`. | `orphan_guard.py:377-381`, `:562` |
| B7 | BAJA | **`host` sin validar en `probe_listener_responsive`** (su hermana sí valida `127.0.0.1`): SSRF potencial si un caller futuro pasa otro host; `urlopen` además sigue redirects. | `orphan_guard.py:809-833` |
| B8 | BAJA | **Allowlist de la auditoría estática keyed por nombre de función** (`verified_daemon_http_request`): renombrar una función a ese nombre hereda el allowlist. Y `mcp_client.py` está excluido sin expiración. | `security_runtime_audit.py:40-65`, `:67-82`, `:182-192` |
| B9 | INFO | Redactor de secretos cubre `client_identity_json` y `lease_token` pero no la clave del daemon; confirmar que `serialize_normal_daemon_policy` nunca la incluye. Secretos en memoria sin zeroizar. Sincronización del registry de fences vive fuera de `instance_fence.py` — confirmar single-thread o lock. | `secure_launcher.py:106-127`, `daemon_credential.py:48-81`, `instance_fence.py:129-171` |

---

## C. Documentación

**Verde:** las 53 tools del README existen como handlers (54 registradas, la 54ª `ui_dialog` descontada explícitamente en arquitectura); `MCP_BRIDGE_VERSION "8"` coincide con `MCPMessages.c`; scripts citados por QUICKSTART existen.

| # | Hallazgo | Evidencia |
|---|----------|-----------|
| C1 | **QUICKSTART/README describen el clon público, no este árbol**: `addon/` no existe aquí (sparse checkout); el fuente del mod real está en el árbol hermano de OneDrive. Seguir QUICKSTART paso 3 en `P:\DayZ_MCP_dev` falla. Falta una nota que distinga ambos árboles. | `README.md:131-136`, `QUICKSTART.md:11,26-30`; fuente real en `tools/publish/boundary.py:19`, `PROJECT-MAP.md:11` |
| C2 | **PROJECT-MAP.md stale sobre HANDOFF**: dice "359 líneas, bloque vivo hasta la 161"; realidad: 2651 líneas, LIVE-STATE hasta la 1642. Su instrucción `Read(HANDOFF.md, limit:161)` lee el 6% del bloque vivo. | `PROJECT-MAP.md:21-23` vs `HANDOFF.md:3`, `:1642` |
| C3 | **PROJECT-MAP.md tamaños/fechas stale** (HANDOFF "38 KB / 2026-08-07" vs real 212 KB / 2026-08-22) y no lista README/QUICKSTART. Pide "regenerate after moving files" y no se regeneró tras 2 semanas de cambios grandes. | `PROJECT-MAP.md:67-72`, `:3` |
| C4 | **NEXT-SESSION-PROMPT.txt retrocede 2.5 meses**: fechado 2026-06-09, manda arrancar Fase 3 (cerrada en junio); el HANDOFF actual describe otra hoja de ruta. | `NEXT-SESSION-PROMPT.txt` vs `HANDOFF.md:9-16` |
| C5 | **CLAUDE.md stale**: "construyendo el POC fase 0" (cerró 2026-06-07), "FastMCP aún NO" (está en producción en `server.py`), "11 tools" vs 53 reales. | `CLAUDE.md:14`, `:32`, `:18` vs `product-spec.md:14` |
| C6 | **Deriva de `path:line`**: README cita `server.py:2062-2063` para texto que hoy está en `:2289`; product-spec cita `:2700` para `ui_dialog` (real `:3536`). Citar rango de líneas en docs vivas garantiza esta deriva. | `README.md:110`, `product-spec.md:353` |
| C7 | **~25 backups `.bak`/`_bak` conviven con los canónicos** (raíz + `tools/dayz_mcp/` + `tools/tests/` + árbol del mod). El canónico es el sin sufijo, pero nada lo dice; quien grepee el árbol encuentra versiones divergentes. | p.ej. `HANDOFF.md_bak_infdrive_20260820`, `server.py.bak-20260816-waitfor` |
| C8 | **AGENTS.md/CLAUDE.md dependen del vault de Obsidian** (runbook y estado fuera del repo, no versionados ni verificables aquí). | `AGENTS.md:6`, `CLAUDE.md:5`, `:61`, `:93-97` |
| C9 | Menores: "Last PBO built" en PROJECT-MAP apunta a un tercer árbol con fecha de julio; "three pinned dependencies" de QUICKSTART sin nombrar cuáles. | `PROJECT-MAP.md:17`, `QUICKSTART.md:16-17` |

**Jerarquía canónica observada (implícita):** README+QUICKSTART (clon público) → arquitectura (diseño) → bloque LIVE-STATE de HANDOFF (estado actual) → product-spec (contrato). Debería documentarse explícitamente.

---

## D. Tests y tooling

**Verde:** suite real de ~160 archivos / ~2047 tests en `tools/tests/` (unittest), buena cobertura de session_coordination, process_lifecycle y server; dependencias pineadas con `dependency-lock.json` + `relock_toolchain.py` + tests que gatean el lock; disciplina de regression tests por bug (`test_bug046_*`, `test_bug104_*`); runner con allowlist explícito y deny-launch guard (`p0s_test_runner.py`).

| # | Hallazgo |
|---|----------|
| D1 | **P1 — Sin CI**: no hay `.github/`, workflows, tox, Makefile ni conftest. Los 2047 tests solo corren manualmente; nada garantiza que la suite pase antes de cada commit. |
| D2 | **P1 — Framework sin declarar**: unittest puro sin `pytest` en requirements ni `[project.optional-dependencies] dev`; no hay una única forma canónica documentada de "correr todo". |
| D3 | **P2 — Scripts de un solo uso mezclados con producción** en `tools/`: gates de fase (`tramoA_gate_driver.py`, `tramoB_getin_gate.py`), diagnósticos (`diag_server_ownership.py`, `h9_native_probe.py`) y `task9_build_a_smoke.py` (4034 líneas). Los gates tramoA/tramoB no tienen tests propios. |
| D4 | **P2 — Artefactos de veredicto/logs versionados o sueltos**: 7 `*-verdict.json` con naming inconsistente (`_tramoB-getin` vs `_tramoB_getin`), `_x5_rerun.log`, PNGs de evidencia. |
| D5 | **P3 — Skips condicionales como vía de falsos verdes**: en una máquina sin entorno DayZ, gran parte de los e2e pasan en skip sin que nada lo detecte (`_bundle_paths.py:26-32`, `test_client_credential_rotation_e2e.py`, `test_build_native_launcher_policy.py:292`). Sin CI que fije el entorno, la suite es más verde de lo que parece. |
| D6 | `test-contracts/` no es suite ejecutable (solo docs+skills); `tools/checks/` tiene 3 checks con sus tests. |

---

## E. Higiene de repo

**Verde:** sin secretos commiteados ni untracked (`.dayz_mcp.key` y `approved-launchers.*` correctamente ignorados); cero binarios grandes versionados (254 archivos tracked, el mayor es un wheel de psutil de 137 KB); convención de commits buena.

| # | Hallazgo |
|---|----------|
| E1 | **~11 GB de snapshots muertos** en la raíz: `_fase3/` 4.2 GB, `_fase2/` 2.5 GB, `_s0/` 2.0 GB, `_poc/` 1.5 GB, `_fase1/` 344 MB, `_step0/` 305 MB, `_gamemaster_h0_*` ~121 MB. Todos son snapshots de junio-julio de gates ya superados. |
| E2 | **.gitignore con huecos**: no cubre `_compile/`, `_backups/`, `_restore/`, `.superpowers/`, `*.bak_*`, `*.bak-*`, `*-verdict.json`, `_*.log`, `*_evidence_*.png` — causa de los ~60 untracked. |
| E3 | **Untracked que son activos y deberían decidirse**: `AGENTS.md`, `CLAUDE.md`, `HANDOFF.md`, `PROJECT-MAP.md`, `decisions/`, `plans/`, `reports/` (49 MB — revisar contenido), `test-contracts/`, tests nuevos. Hoy no existen para el clon público. |
| E4 | `_backups/` (53 MB, 20+ backups con timestamp) debería archivarse fuera del árbol de trabajo. |

---

## Plan de arreglos propuesto (ordenado por riesgo/esfuerzo)

### Sprint 1 — Bugs críticos y seguridad alta (días)
1. **A1**: `timeout = 30` (o similar) en `Handler` de `loopback.py:2355` + capturar `socket.timeout` en `_read_json` devolviendo 408. Test: cliente que declara Content-Length y no envía cuerpo no debe colgar un hilo.
2. **A2**: añadir deadline a los dos `wait()` de `session_coordination.py:671/770` (patrón `remaining` ya usado en `:333`/`:1091`), con re-chequeo del predicado tras timeout.
3. **B1**: o hashear el contenido del exe (costoso: cachear por ruta+mtime+size), o renombrar el campo a `executable_route_sha256` / documentar en el contrato que NO verifica integridad del binario. La opción honesta-barata es la segunda HOY y la primera como roadmap.
4. **A8**: `utf-8-sig` en `read_key` (`loopback.py:3089`) — una línea, mismo fix que `core.py:126`.
5. **B2**: restringir el `allow_root_junction` al propio root sellado, no a cualquier hoja descendiente.

### Sprint 2 — Degradación de errores y contrato del daemon
6. **A4**: introducir un helper `swallow(log_sink, "contexto")` que registre tipo+mensaje+traceback antes de degradar; migrar los `except Exception` de las rutas de cleanup/persist primero (son las que pierden diagnósticos de producción).
7. **A3**: aserción de recursión en los `release()/acquire()` manuales (contar profundidad antes del release y fallar ruidosamente si >1), o refactorizar a `_locked` puros + funciones de I/O explícitamente fuera de lock.
8. **A7**: validar `startup_budget_s` también cuando el caller lo pasa explícito; corregir el docstring "5-12s".
9. **B3**: mover la key a header `Authorization` (o `X-Daemon-Key`) manteniendo compat de query string un ciclo.
10. **B4**: verificar modo/ACL del keyfile en `pinned_keyfile` contra la política de creación.

### Sprint 3 — Estructura y mantenibilidad
11. **A5/A6**: partir `build_app` por dominios (`_register_world_tools`, `_register_session_tools`, …) y extraer la recuperación de manifiesto de `_activate_server_coordination`.
12. **A10**: unificar los cuatro focos de duplicación en `core`/`dayz_tools_paths` y borrar las copias.
13. **A9**: decodificar RPT con `cp1252` fallback (o `errors="replace"` tras intentar ambas) y documentarlo.
14. **B6/B7**: ruta absoluta para netstat; validar `host == "127.0.0.1"` en `probe_listener_responsive`.

### Sprint 4 — Repo y docs
15. **E1**: borrar (tras verificación) los ~11 GB de snapshots muertos; archivar `_backups/` y los ~25 `.bak` fuera del árbol.
16. **E2**: completar `.gitignore` con los patrones de E2.
17. **C2/C3/C4/C5**: regenerar PROJECT-MAP.md, reescribir NEXT-SESSION-PROMPT.txt alineado con HANDOFF.md:9-16, actualizar el "Estado actual" de CLAUDE.md, y añadir nota README/QUICKSTART→árbol-dev distinción clon público vs árbol de desarrollo (C1).
18. **C6**: sustituir citas `path:line` en docs vivas por `path` + ancla de símbolo (nombre de función/constante), que no deriva.
19. **E3**: decidir y commitear los untracked activos (docs de sesión, decisions/, plans/, reports/).
20. Documentar explícitamente la jerarquía canónica de docs.

### Sprint 5 — CI (cierra D1/D2/D5)
21. Workflow GitHub Actions: Windows runner + `uv`/pip con `tools/requirements-mcp.txt` + runner `p0s_test_runner.py` (o migrar a pytest con `conftest.py` y discovery). Publicar conteo de skips como métrica — un salto de skips es la señal temprana de entorno roto (D5).
22. Añadir job de `python -m compileall` / lint ligero (ruff) solo con reglas de bugs (no estilo) para cazar A11/A15 por adelantado.

---

## Qué le añadiría (más allá de arreglar)

1. **CI en Windows con matriz mínima** (ver Sprint 5) — es la adición de mayor valor por euro del proyecto entero: protege los 2047 tests que ya existen.
2. **Canalización de logs estructurados del daemon** (JSONL con level/component/correlation-id de lease): hoy los swallows de A4 y los stalls de A3 son casi indistinguibles sin reproducir; con logs estructurados el post-mortem es trivial. Ya hay `log_sink` — generalizarlo.
3. **Health endpoint con introspección de threads**: `GET /debug/threads` (protegido por key) que vuelque stack de cada thread del daemon. Habría detectado A1/A2 en producción en minutos.
4. **Modo `--selftest` del daemon** que al arrancar verifique sus invariantes baratos (permisos del keyfile, budget válido, allowlist de audit al día) y falle ruidosamente — convierte B4/B8/A7 en checks de arranque.
5. **Watchdog de deriva de docs**: un test que verifique las afirmaciones numéricas de las docs (conteo de tools registrado vs README, líneas de HANDOFF citadas por PROJECT-MAP) — las docs dejan de mentir solas (C2/C3/C5).
6. **Métrica de cobertura** (coverage.py) solo sobre `dayz_mcp/` para priorizar dónde añadir tests; los gaps probablemente coinciden con loopback/http y session_coordination wait-paths.
7. **Script `scripts/archive_snapshot.py`** con timestamp a `_archive/` (gitignored) para que la costumbre de snapshottear `_faseN/` deje de dejar 11 GB en la raíz.
8. **Versión y CHANGELOG del paquete**: `__version__` único en `dayz_mcp/__init__.py` referenciado por server, daemon y docs; hoy la noción de "qué build está corriendo" vive en nombres de directorio.
9. **Migrar la doc de sesión del vault de Obsidian al repo** (o un mirror auto-publicado): C8 hace que el protocolo de sesión compartida —la regla más crítica— no sea verificable ni versionado junto al código que la implementa.
10. **Property-based testing (hypothesis) para `request_path_authority` y `pinned_keyfile`**: son los módulos donde los casos borde (reparse, ADS, hardlinks) importan y donde un generador adversarial encuentra lo que los ejemplos no.

---

## Qué NO se verificó

- Los subagentes no ejecutaron la suite de tests ni el daemon; todos los findings son de lectura estática con cita `path:line`.
- El árbol del mod (Enforce Script / `MCPMessages.c`) solo se contrastó en `MCP_BRIDGE_VERSION`; no hubo audit de código C++.
- El contenido de `reviews/` (49 MB) y `reports/` no se revisó íntegro.
- B9 (cobertura del redactor, zeroización, sincronización del fence registry en `loopback.py`) quedó marcado como verificación pendiente, no hallazgo cerrado.
