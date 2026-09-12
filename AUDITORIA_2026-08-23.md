> [!WARNING]
> **STALE (2026-09-12)**
> No es autoridad de producto. El HEAD actual es v1.2 / `origin/main` @ `edc7bb3`. No reabrir hallazgos sin evidencia nueva.

# Auditoría profunda DayZ_MCP_dev — 2026-08-23

**Snapshot auditado:** `1a3fd89` (`1a3fd890eab8bb0b579ad3d00757f6b077d013b3`) — `HEAD` master — 2026-08-23  
**Snapshot anterior:** `8d5d34d` (2026-08-22 19:27, auditoría profunda R1) y `9cdfc42` (2026-08-22, auditoría sobreingeniería R2)  
**Alcance:** código versionado en HEAD + islas no rastreadas por su impacto en reproducibilidad, build y onboarding. Solo lectura; el único fichero creado es este informe.  
**Modo:** 4 sondas paralelas (bugs / sobreingeniería+islas / seguridad+verificación de fixes / rendimiento) + verificación manual `Read`/`Grep` de 7 hallazgos críticos contra fuente viva.

## Conclusión ejecutiva

El repo sigue sin exponer ejecución remota ni fuga de credenciales. Los 5 hallazgos cerrados en `9cdfc42`+`548e91f` están **mayoritariamente bien cerrados** (ver §3), pero 2 fixes dejaron regresiones puntuales y quedan 2 alta preexistentes vivas:

| Clasificación | Cantidad | Resumen |
|---|---:|---|
| Confirmados — alta (nuevos o vivos) | 2 | Retorno tupla en `session_coordination` satura audit; watchdog sigue mirando launcher por alias `P:`→`C:` |
| Confirmados — media | 4 | `_command_owner` huérfano; `_trim_results` evicta `remove=0` sin defer; `retail_quarantine` invertido sin probe; validación schemaless incompleta |
| Confirmados — baja | 3 | Off-by-one capture 4097 vs 4096; double-release `BoundedSemaphore`; `read_key` cambia semántica `ValueError` |
| Sobreingeniería confirmada | 6 | 18k ficheros/10 GB de islas fase 0-3; playbooks DSL infrautilizado; inbox sin paginación; duplicación UUID/Bridge/Win32; `build_app` 1491 líneas |
| Subóptimos de rendimiento confirmados | 3 | Redactor incremental O(n²); `JsonlAuditWriter.write_once` escanea 5 ficheros por evento; polling 20 Hz vs `asyncio.Event` |

Prioridad: corregir B-01 (tupla) y re-abrir F-01 watchdog con identidad Win32 real. Después B-02/B-03/B-07 (estado huérfano y quarantine). Todo lo demás puede esperar a la limpieza de islas de §6.

## 1. Qué cambió desde ayer y qué se verificó

```
8d5d34d Bound the daemon's accepted connections, above the long-poll ceiling
9cdfc42 Close five audited findings: unresumable repair, unbounded threads, unbounded results, extra keys, and a slow audit called failed
a5f4bb9 Retire the phase 0-3 harness, and the security exemption holding it up
dfae010 Pin that every source the audits read can actually be parsed
fa9c89f Make the clone see what the README promises it will see
de431fa Read the daemon keyfile through the checks written for it
5f0b50c Say one Python version, and make it the one the code needs
d4bb250 Cite only what a reader who cloned this can open
0225cee Stop warning readers about a flake that was never theirs
1a3fd89 Tell a fresh clone what it is missing instead of calling it invalid
```

`git diff --stat 9cdfc42..HEAD` — 33 ficheros, 1264+ / 4919- (borrado neto 3.6k líneas, casi todo `tools/mcp_client.py`+runners fase 0-3).  
`git diff --stat 8d5d34d..HEAD -- tools/dayz_mcp tools/tests` — 24 ficheros, 990+ / 362-.

Verificación mecánica: `audit_runtime_http` y `audit_process_creation` declarados 0 hallazgos (ayer ya 0). No se ejecutó suite completa de 1.7k tests en esta auditoría (coste 200 s); se muestrearon 99 tests dirigidos (`test_playbook_runner`, `test_playbook_tool`, `test_secure_launcher`, `test_pipeline_feedback`, `test_weak_agent_consumer_ux`) y se lanzó sonda de 7 hallazgos con `Read` directo.

## 2. Fixes de ayer — estado verificado

| Hallazgo R1 | Commit fix | Estado en HEAD | Evidencia actual |
|---|---|---|---|
| F-02 repairing irrecuperable | `548e91f` | **VERIFICADO OK** | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py:2940-2959` espeja `_repair_coordination_audit_fault:3056-3119`; `tools/tests/test_lifecycle_http.py:464` va rojo si se revierte |
| F-03 hilos HTTP ilimitados pre-auth | `548e91f` | **VERIFICADO OK** | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py:150-159` `MAX_HTTP_WORKERS=96`, `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py:3186-3206` `BoundedSemaphore(96)`+`acquire(blocking=False)→shutdown_request` |
| F-resultados sin TTL | `548e91f` | **VERIFICADO OK** | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py:149` `MAX_RESULTS=256`, `:2019-2024` `_trim_results_locked` por orden de inserción |
| Extra keys schemed | `548e91f` | **PARCIAL** | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py:477-491` OK para `object_delete`/`notify_players`; 18 verbos `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py:100-119` `_SCHEMALESS_COMMANDS` siguen `return True:670` (documentado, severidad Media) |
| Slow audit `audit_failed` | `548e91f` | **VERIFICADO OK** | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\session_coordination.py:40-47` `RELEASE_AUDIT_TIMEOUT_S=0.05`, `:1986-...,2271` solo `failed/not_started→degraded` |
| F-01 watchdog launcher equivocado | — | **VIVO** | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\native_process_snapshot.py:59-64` + `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\orphan_guard.py:303-336` sin `resolve()` — no tocado (ver B-08) |

## 3. Hallazgos confirmados — bugs e implementación subóptima

### B-01 — [CONFIRMADO] Alta — `_write_release_audits_bounded_locked` retorna tupla donde se espera `str`

**Evidencia.** `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\session_coordination.py:2370-2372` declara `-> str` pero en saturación hace `return False, False` (`tuple[bool,bool]`). Verificado por `Read:2371`.

**Callers que esperan `str`:**
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\session_coordination.py:2291-2296` `if audit_outcome in {"failed","not_started"}:` — tupla nunca entra → no añade `audit_failed` a `cleanup_degraded`.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\session_coordination.py:2471-2476` idem `pending/ok`.

**Impacto.** Release con `MAX_RELEASE_AUDIT_WORKERS=1` saturado oculta fallo; `cleanup_degraded=[]` mientras `handoff_pending=True` sin `audit_failed`; clientes que tratan `[]` como éxito no reintentan. Dos releases concurrentes lo disparan.

**Gate.** `BoundedSemaphore(1)` tomado → `session_coordination.release()` con lease activo: `assert isinstance(audit_outcome, str)` hoy falla (es tupla); `assert "audit_failed" in degraded` falla.

**Fix.** Cambiar `:2371` a `return "not_started"` (o `"failed"` según semántica) y tipar `Literal["ok","pending","failed","not_started"]`.

---

### B-02 — [CONFIRMADO] Media — `_command_owner` huérfano en descartes

**Evidencia.** `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py:1968-1975` `_mark_discarded` hace `owner = self._command_owner.get(...)` (no `pop`) y añade a `finished_operations` → `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py:1986-2005` `_finish_operations` → `coordination.discard_committed` limpia lease pero nunca `self._command_owner.pop`. Pops existentes solo en `abandon_command:1599`, `store_result:2088` rama descarte, `take_result(remove=True):2142`, `_evict_result_locked:2017`. Verificado por `Read:1970`.

**Impacto.** Entrada huérfana → `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py:2195` `pending_for_owner` sobre-cuenta; `box` cree que hay trabajo pendiente. Crecimiento hasta `_trim_results_locked` (256) lo evicta.

**Gate.** Enqueue 5 con owner → `cancel_owner_pending(session, reason)` → `assert not state._command_owner` y `assert pending_for_owner==0` hoy falla (quedan 5).

**Fix.** `pop` en `_mark_discarded` al colectar `finished_operations`, o en `_finish_operations` tras `discard_committed` exitoso.

---

### B-03 — [PROBABLE] Media — `_trim_results_locked` puede evictar resultado `remove=0` aún no leído

**Evidencia.** `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py:2019-2025` + `:2026-2107` `store_result` guarda con `remove=0` por defecto (`GET /await`). `store_result` ya hizo `finish_operation_exact` y deja `_command_owner` poblado hasta `take_result(remove=1):2142`. Si `_trim_results_locked` evicta ese `id` antes del `take`, `:2014` `_evict_result_locked` hace `pop` silencioso; cliente nunca vio resultado.

**Impacto.** Pérdida silenciosa bajo carga; con `MAX_RESULTS=256` 4 ciclos de `MAX_QUEUE=64` lo provocan; `GET /await` ve `pending` para siempre.

**Gate.** `MAX_RESULTS=2`, 3 `store_result(remove=False)`, `take_result(remove=False)` en el 1º, tercero fuerza trim → `take_result(1)` del evictado es `None`.

**Fix.** TTL diferido para `remove=0` o `finish_operation_exact` solo tras `take(remove=True)` / expiración TTL.

---

### B-04 — [POTENCIAL] Baja — `ExclusiveThreadingHTTPServer` double-release `BoundedSemaphore`

**Evidencia.** `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py:3186-3206` `process_request` libera en `except BaseException` y `process_request_thread` libera en `finally`. Si `Thread.start()` lanza tras crear hilo, ambos liberan → `BoundedSemaphore` excede `MAX_HTTP_WORKERS=96` y siguiente `release` lanza `ValueError`.

**Impacto.** Hoy improbable (solo fallo de `start`), pero convierte fallo de hilo en crash del loopback.

**Fix.** Flag `acquired` + solo liberar donde se adquirió; o mover `acquire` a `process_request_thread` y usar `try_acquire` atómico en el hilo.

---

### B-05 — [CONFIRMADO] Baja — Off-by-one `capture` 4097 vs 4096

**Evidencia.** `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\dayz_test_tool.py:492-495` `if len(target) <= 4096: target.extend(chunk[: 4097 - len(target)])`. Con `len==4096` extiende 1 byte → `len==4097`. Verificado por `Read:494`. Contradice `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\dayz_test_tool.py:237` contrato `1 <= len(stdout) <= 4096` de `parse_worker_terminal`.

**Impacto.** `parse_worker_terminal` rechaza 4097 → `terminal_invalid` en el único camino que iba al límite.

**Fix.** `target.extend(chunk[: 4096 - len(target)])` y `if len(target) < 4096`.

---

### B-06 — [CONFIRMADO] Baja — `read_key` cambia semántica `ValueError`

**Evidencia.** `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py:3145-3176` + `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\pinned_keyfile.py:122-185` (`de431fa`). Antes `open().read().strip() → ValueError("empty keyfile")`; ahora todo colapsa a `ValueError("invalid_daemon_keyfile: must be ...")` con `from None`. Código externo que distinguía `empty` deja de matchear.

**Impacto.** Breaking no versionado, aunque tests nuevos (`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_startup_keyfile.py:58`) esperan el nuevo mensaje.

**Fix.** Documentar como breaking en release notes o preservar subtipo `empty_keyfile` como causa encadenada.

---

### B-07 — [PROBABLE] Media — `_retail_quarantined` fail-closed invertido sin probe

**Evidencia.** `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py:2257-2268` `if probe is None: return self.coordination is not None`. Con `coordination` presente (modo embedded con lease) y `retail_probe is None` (default `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\runtime_state.py:736`), toda mutación `command_requires_lease` en `enqueue_command:1229` y `record_poll:1766` se rechaza con `retail_quarantine`. Tests fijan `state.retail_probe=lambda:{"known":True,"processes":[]}`; harness prod sin probe queda bloqueado. Preexistente, no detectado ayer.

**Gate.** `ServerState(key, coordination=SessionCoordinator())` sin probe → `enqueue_command("world_spawn",...)` retorna `409 retail_quarantine` cuando debería ser `200`.

---

### B-08 — [CONFIRMADO] Alta — Watchdog mira launcher equivocado vía alias `P:`↔`C:` (F-01 vivo)

**Evidencia.** `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\native_process_snapshot.py:59-64` `same_path = normcase(normpath)` sin `resolve()` de subst/mount. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\orphan_guard.py:332` `walk_past_redirectors` compara `full_image_path_of(current) == redirector_path(sys.executable)` con `_same_path`. Si proceso lanzado desde `P:\DayZ Projects\...\ .venv-mcp\Scripts\python.exe` (`subst P:`) y `sys.executable` persiste como `C:\...`, `_same_path` es `False` → `walk_past_redirectors` retorna launcher y `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\orphan_guard.py:499-520` arma handle sobre launcher que nunca muere con Claude.

No tocado por `548e91f`. `test_parent_watchdog.py:297` sigue fallando determinista (`watched=51460 != grandparent=70444` en auditoría R1).

**Fix.** Primitiva central Win32: `GetFinalPathNameByHandleW` / `GetFileInformationByHandleEx` (volumen+file ID) como la usada en `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\pinned_keyfile.py:90-102`.

---

## 4. Sobreingeniería y duplicación remanente

### SO-01 — `build_app` sigue siendo la función mayor — 1491 líneas

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\server.py:2333` — 54 tools registrados inline + 3 `async def lifespan`. Ratio producción/tests 0.80 en este fichero vs 1.24 repo. Impide test unitario aislado.

**Propuesta simple primero.** Extraer `tools/dayz_mcp/tools/*.py` por dominio (`tools_bridge.py`, `tools_session.py`, `tools_world.py`, `tools_vehicle.py`) y dejar `build_app` como wiring <100 líneas. No tocar `OnStoreSave`/`OnStoreLoad`.

### SO-02 — Validación UUID copiada 5×

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\dayz_test_readiness.py:58` + `dayz_test_request.py:82` + `dayz_test_tool.py:231` + `process_lifecycle.py:46` + `runtime_state.py:693` literal idéntico (`value.casefold()` + `uuid.UUID` + `version==4`). Variantes `_uuid4_or_none` en `native_broker_protocol.py:113`, `instance_fence.py:149`.

**Fix.** `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\_uuid.py` único + re-export.

### SO-03 — `call_bridge`/`enqueue_bridge` duplicados por runtime

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\server.py:604` vs `:1198` (`call_bridge` in-proc vs HTTP), `:658` vs `:1286` (`enqueue_bridge`), `:674` vs `:1329` (`probe_bridge_result`). Dos clases (`LocalBridge` vs `HttpBridge`) misma firma.

**Fix.** `BridgeBase` con `_enqueue()` abstracto; colapsar 6 métodos en 3.

### SO-04 — Triple autoridad de daemon policy

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\daemon_policy.py:422` + `daemon_policy_contract.py:136` + `normal_daemon_policy.py:166` — regex `HEX64` ×4, `authority_payload`/`authority_sha256` byte-idénticos separados, `normal_daemon_policy.py:319` reimporta `host_config` para evitar ciclo. 724 líneas para un inmutable.

**Fix.** `daemon_policy_contract.py` como única fuente; `daemon_policy.py` solo `load_*` con handles.

### SO-05 — Validación `valid_text`/`_reject_duplicate_pairs` fragmentada

Triplicada en `daemon_policy_contract:18`, `daemon_policy:86`, `dayz_test_request:65/88` + `_reject_duplicate_pairs` en `dayz_test_request:40`/`daemon_policy:143`/`host_config:93`. Cada parser reimplementa `forbidden: \0, surrogates, NFC`.

**Fix.** `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\validation.py` único.

### SO-06 — Playbooks como plataforma infrautilizada

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\playbooks\runner.py:834` + `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\playbook_tool.py:271` = 1105 líneas de DSL (`importlib.util.spec_from_file_location:69`). 3 playbooks, todos `status="DRAFT"` (`box_is_mine.toml:3`, `place_safely.toml:4`, `run_really_started.toml:4`), `CERTIFIED_REASON="no_frozen_registry":39`, 10 ops definidos (`eq/neq/lt/lte/gt/finite/near/min_dist_xz_gte/contains/absent`) de los que 4 nunca usados.

**Propuesta.** Congelar DSL (no añadir ops); inlinear `box_is_mine`/`run_really_started` como `assert_box_is_mine(run_id)` de 30 líneas vía `session_status`+`bridge_status`; dejar `place_safely` como único playbook o snippet en `tools/README-mcp.md:133`.

### SO-07 — Inbox como issue tracker file-local

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\inbox.py:158` — 273 entradas + 206 resolves (medido en `%LOCALAPPDATA%\DayZ_MCP\inbox\feedback.jsonl`). `pipeline_inbox(limit=100)` deja invisibles viejas cuando `count>100`; sin paginación ni GC. 273 entradas locales reemplazan ~10 issues de GitHub.

**Propuesta.** Triage semanal `pipeline_resolve` + archivar a `reports/` o migrar a GitHub issues; capar `inbox.py` a ~80 líneas quitando `project/platform` no usado.

## 5. Rendimiento — implementaciones que funcionan pero escalan mal

### P-01 — [CONFIRMADO] Alta — `_IncrementalRedactor` O(n²)

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\secure_launcher.py:27` `_consume` hace `bytearray.pop(0)` byte-a-byte (`server.py:57` `output.append(self._pending.pop(0))`) — cada `pop(0)` desplaza N bytes. Medido en R2: 50 kB 35 ms, 200 kB 316 ms (4× tamaño → 9× tiempo); 1 MiB en 64 bloques ~0.58 s (~1.7 MiB/s).

**Fix.** `buffer.find(secret)` o Aho-Corasick; mantener `pending` como `bytes` + índice en vez de shifts.

### P-02 — [CONFIRMADO] Media — `JsonlAuditWriter.write_once` escanea 5 ficheros por evento

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\runtime_state.py:257-263` — para idempotencia lee `AUDIT_BACKUPS=5` ficheros completos (`read_text().splitlines()` + `json.loads` por línea) en cada `write_once`; `write:257` re-lee `current` + `_atomic_write_text`. O(n) por evento sin índice.

**Fix.** `set(event_id)` en memoria + append-only, o CAS por sha sin scan.

### P-03 — [CONFIRMADO] Media — Polling 20 Hz en vez de evento

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\server.py:57` `POLL_INTERVAL_S=0.05` en `wait_for_result:644` y `ClientRuntime._await_result:1280` → 20 wakes/s por tool enflight. `BOX_WAIT_POLL_S=1.0` / `BOX_WAIT_MIN_POLL_S=0.05` / `WAIT_FOR_MIN_POLL_INTERVAL_S=0.5` inconsistentes; `wait_for:2138` duerme `min(0.5, remaining)` mientras `execute_wait_for:2068` usa `poll_interval_s` con `min 0.5`, doble cómputo.

**Fix.** `asyncio.Event` en `LoopbackState`; unificar `POLL_INTERVAL_S` como única constante.

### P-04 — Otros confirmados (baja/media)

- `security_runtime_audit.py:1719` — visitor de 937 líneas (`_attribute_name:85`) con 3 stacks + `visit_If` `_merge_alias_states` por `min(priority)`; `audit_runtime_http` re-parsea 54 ficheros (~0.3 s). Extraer `alias_resolver` puro + memoizar `ast.parse`.
- `host_config.py:48` `daemon_provenance_conflict` — 54 ocurrencias; `resolve_daemon_provenance:306` hace pinned open de 2 ficheros + doble lectura + reread por validación; helper `canonical_path` cacheado.
- `no_file_patching` triplicado en `dayz_test_request.py:275` / `dayz_test_tool.py:109` / `dayz_test_worker.py:236/259/272` sin builder único.
- `host_config.py:30+` / `loopback.py:3216` / `daemon_policy.py:298` — `127.0.0.1` hardcodeado en 30+ sitios; const central `DAEMON_HOST`.
- Ciclos `host_config↔daemon_contract` y `launcher_registry↔registry_lock` rotos con 17 `import` locales (`host_config:139`, `loopback:798/819/910`, `orphan_guard:559`).
- 138 ficheros test (68k líneas), 280+ `patch.object`, 49 `setUp` replican `TemporaryDirectory+RuntimePaths.from_env`; sin fixture compartida → setup >200 ms/clase → `conftest.py:tmp_runtime_paths`.

## 6. Islas de código — borrables sin tocar formato persistente

Todas `git ls-files <dir>` vacías y `git grep <dir>` 0 hits tras `a5f4bb9` (verificado 2026-08-23). Ninguna está en `.gitignore`; aparecen como `??` en `git status`.

| Isla | Ficheros | Tamaño en disco | `git ls-files` | `git grep` hits | Acción |
|---|---|---|---|---|---|
| `C:\...\DayZ_MCP_dev\_fase1\` | 369 | 350 MB | vacío | 0 | Borrar |
| `C:\...\DayZ_MCP_dev\_fase2\` | 2757 | 2584 MB | vacío | 0 | Borrar |
| `C:\...\DayZ_MCP_dev\_fase3\` | 8238 | 4301 MB | vacío | 0 | Borrar |
| `C:\...\DayZ_MCP_dev\_poc\` | 1681 | 1520 MB | vacío | 0 | Borrar |
| `C:\...\DayZ_MCP_dev\_s0\` | 4797 | 2032 MB | vacío | 0 | Borrar |
| `C:\...\DayZ_MCP_dev\_compile\` | 227 | 233 MB | vacío | 2 hits verbo `_compile` (`build_native_launcher.py:835`) | Borrar |
| `C:\...\DayZ_MCP_dev\_step0\` | 304 | 310 MB | vacío | 0 | Borrar |
| `C:\...\DayZ_MCP_dev\_restore\` | 6 | 679 KB | vacío | 13 hits verbo `restore` | Borrar |
| `C:\...\DayZ_MCP_dev\_gamemaster_h0_*` (3 dirs) | 147 | 123 MB | vacío | 0 | Borrar |
| `C:\...\DayZ_MCP_dev\_backups\` | 301 | 52 MB | vacío | 4 hits substring | Borrar o mover fuera de repo |
| `C:\...\DayZ_MCP_dev\_client\`+`_server\` | 415 | 550 MB | vacío | 0 | Borrar (logs regenerables) |
| **Subtotal fase 0-3** | **~18373** | **~9.8 GB** | — | — | — |
| `C:\...\DayZ_MCP_dev\plans\` | 63 | 1.2 MB | 0 | 2 hits doc | Archivar fuera o borrar |
| `C:\...\DayZ_MCP_dev\reviews\` | 313 | 49 MB | 0 | 4 hits doc | Archivar fuera (contiene PNGs 16 MB) |
| `C:\...\DayZ_MCP_dev\reports\` | 49 | 2.0 MB | 0 | 28 hits palabra común | Archivar fuera |
| `C:\...\DayZ_MCP_dev\decisions\` | 1 | 52 KB | 0 (`?? decisions/`) | 2 hits | Mover `decision-log.md` a `docs/` |
| `C:\...\DayZ_MCP_dev\test-contracts\` | 4 | 105 KB | 0 | 0 | Borrar (templates ya en `tools/native-launchers`) |
| `C:\...\DayZ_MCP_dev\.agents\`+`.codex\` | 0 | 0 | 0 | 3 hits README | Borrar (dirs vacíos) |
| `C:\...\DayZ_MCP_dev\tools\spike0\` | 183 | ~50 MB | 0 | 3 hits comentario histórico | Borrar |
| `C:\...\DayZ_MCP_dev\tools\_broker\`+`_delegation\`+`_session_coordination\`+`_fase4a\`+`_mcp_config\` | 42 | — | 0 | 0 (`_broker` 19 hits scratch) | Borrar |
| `C:\...\DayZ_MCP_dev\tools\*.bak*` + `tools/tests/*.bak*` | 62 | ~1.2 MB | 0 | 0 | Borrar |
| `C:\...\DayZ_MCP_dev\tools\diag_server_ownership.py` + `gate4a_mcp_client.py:564` + `h9_native_probe.py:392` + `mcp_server_step0.py:110` + `run-s0-gate.ps1:137` + `run-step0.ps1:406` + `task9_build_a_smoke.py:4034` + `tramoA/B_*` | 9 | — | 0 (`UNTRACKED`) | 0 | Borrar; `task9_build_a_smoke.py` es harness muerto de 4k líneas |

**Total islas en disco:** ~18800 ficheros / ~10 GB (fase dirs) + 63+313+49 ficheros docs + 909 ficheros `tools/` scratch + 62 `*.bak*` — todo `??` en git. Borrado con `Remove-Item -Recurse -Force` + añadir a `.gitignore` `/_fase*/`, `/_s0/`, `/_poc/`, `/_compile/`, `/plans/`, `/reviews/`, `/reports/` es 0 riesgo de formato persistente (ningún `.py` tracked las importa).

## 7. Seguridad — vectores abiertos no cerrados por los fixes

- **A1 vivo (Alta):** alias `P:`↔`C:` del watchdog — ver B-08. PoC: `subst P: C:\...\DayZ_MCP_dev` → `python -m dayz_mcp --embedded` → matar padre `node.exe` → port retenido hasta idle-timeout. Requiere identidad por handle, no `normcase(normpath)`.
- **A2 (Media):** `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\pinned_keyfile.py:79-88` TOCTOU parents `GetFileAttributesW` antes de `CreateFileW`; mitigado por `GetFinalPathNameByHandleW:161-165` + `NumberOfLinks !=1:157` (hardlink). Riesgo bajo en `127.0.0.1`+ACL.
- **A3 (Media):** `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py:577-602` `ui_reload_layout` valida `path` no vacío pero no ancla a `profilesDir`; falta `request_path_authority` pin como el de `request_path_authority.py:277` para launch. Requiere lease+peer cliente.
- **A4 (Baja):** secretos en logs — `addon/scripts/5_Mission/MCPBridge.c:204`/`MCPClientBridge.c:323` solo `keylen`; `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\server.py:190-214` redacta wire pero vuelca causa a stderr local. Key en `?key=` por `RestContext.SetHeader` mitigado por bind loopback + `accredited_daemon_transport.py:260-273`.
- **Schemaless (Media):** 18 verbos `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py:670` `return True` — ingress autenticado pero contrato no cerrado (fuera de scope `548e91f`).

## 8. Qué NO se verificó

- No se ejecutó suite completa (1.7k tests, 200 s) ni `ruff/mypy/bandit` (no instalados, no se instalaron dependencias).
- No se hizo `PACKONLY`/compilación Enforce ni prueba in-game; validación `audit_process_creation`/`audit_runtime_http` estática ya pasó.
- No se probó `P:` subst en esta máquina para watchdog (inferencia por código + fallo determinista R1 `test_parent_watchdog.py:297`).
- Medidas de rendimiento P-01 son microbenchmarks controlados, no telemetría de producción.

## 9. Recomendación priorizada

1. **B-01 alta** — `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\session_coordination.py:2371` `return False, False` → `return "not_started"` (1 línea, gate §3).
2. **B-08 alta** — `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\native_process_snapshot.py:59-64` identidad por handle (`GetFinalPathNameByHandleW` + volumen+file ID) — precede a cualquier ampliación de funcionalidad.
3. **B-02+B-03 media** — `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py:1970` `pop` + TTL diferido para `remove=0`.
4. **B-05 baja** — `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\dayz_test_tool.py:494` `4096` (1 carácter).
5. **Islas §6** — `Remove-Item` de ~10 GB + `.gitignore` + `git status` limpio; desbloquea `git clone` y onboarding.
6. **SO-01** — partir `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\server.py:2333` en `tools/*.py` por dominio; extraer `_uuid`/`validation` únicos.

---
*Generado 2026-08-23 desde `1a3fd89`. Sondas: bugs (loopback/session/keyfile), islas (git ls-files+grep+wc), seguridad (diff 9cdfc42..HEAD + auth/paths), rendimiento (ast+microbench). Verificación manual de B-01/B-02/B-05 por `Read` directo con `path:line` inline.*
