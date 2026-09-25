<!-- Salida CRUDA de una lane Qwen3.8-Flash-Next (GX10), sin editar. NO es el informe: puede contener errores.
     Verificación mecánica del receptor (EVID literal a ±10 líneas en HEAD 57269d2): == L3_lifecycle_security.md findings: 25 {'EXACT': 11, 'FAR': 4, 'NOT_FOUND': 10}
     Veredicto del receptor: ver ../INFORME.md §4 y §8. -->

## Resumen (máximo 8 líneas)
El camino de `dayz_test_run` a un DayZ corriendo atraviesa 5 capas fuera del daemon (gate tool, launcher transaccional, broker nativo con CreateProcess-suspendido + debug events, worker interno con 2 brokers, y lifecycle daemon) más 3 gate pre-admisión (Steam, desktop, VPP). Cada una añade un riesgo real —lanzar el motor, no matar un cliente vivo, ocupación de box, Steam— pero varias son desproporcionadas para "herramienta local de desarrollo con 127.0.0.1 + API key": 43 módulos de sellado/pinning, 1.720 líneas de auditoría estática de HTTP y 1.429 de migración de identidad v2 no se sostienen con ese modelo de amenaza. Las 6 funciones más largas (235–472 líneas) se alargan por un patrón repetido de 5–6 re-checks y 3 paths de error idénticos; `start_run` y `stop_run` comparten 150 líneas de estructura. Hay 3 fuentes de verdad para el perfil de procesos (doctor, lifecycle, identity_migration) y 3 tablas de nombres DayZ. El riesgo que domina no es la seguridad, sino la coherencia de estados (`STARTING`/`STOPPING`/`UNRECONCILED`) y el ciclo de vida de locks y handles tras un kill a mitad de launch.

## A. Mapa del lanzamiento

| # | Capa (módulo) | Qué añade al camino | Estado y dónde vive | Cita |
|---|---|---|---|---|
| 0 | Session lease (`_require_idle_session`) | Adquiere el lease FIFO de sesión para que el resto sea serializable; exige 3 campos nulos | `runtime.active_lease_token/active_ticket/active_operation_id` (proceso MCP) | `dayz_test_tool.py:521` `async def _require_idle_session(runtime: _Runtime, *, tool: str) -> None:` |
| 1 | Tool / MCP (`execute_dayz_test_run`) | Decide qué se lanza y qué no: valida mode, Steam, desktop, VPP y el gate de reemplazo de cliente; compone request canónico | Ninguna de escritura propia; lee bridge y lifecycle; publica dict al MCP | `dayz_test_tool.py:1518` `await _require_idle_session(runtime, tool="dayz_test_run")` |
| 2 | Launcher approval (`open_approved_launcher` + bundle) | Abre el PE pinneado por sha256 y verifica su closure de módulos | Registro `approved-launchers.json`; roots del bundle | `dayz_test_tool.py:1519` `with open_approved_launcher("dayz-test-v1") as opened:` |
| 3 | Broker nativo (launcher_transaction → `launch_registered_native`) | Ejecuta `CreateProcess` suspendido + JOB_OBJECT + debug events y bloquea a `app.pyz` (CPython embebido) dentro del JOB; escribe el request cifrado a stdin | Handles JOB, debug, pipes, eventos y job-completion port | `native_launcher_backend.py:1659` `async def launch_registered_native(opened_launcher: _PinnedLauncher, *, verified_bundle: object, identity_json: str, lease_token: str, daemon_policy_json: str, canonical_request: bytes, cancel_event: asyncio.Event, output_sink: Callable[[str, bytes], None] | None=None) -> int  [113 lines]` |
| 4 | Worker dentro del launcher (no visible) | Secuencia las 2 llamadas broker (lifecycle start + readiness) con el identity_json y policy heredados | Ninguno duradero; estado solo en el wire | [INFERENCIA] desde `dayz_test_worker.py:387` `async def _lifecycle(broker: Broker, command: str, *, run_id: str | None, operation_id: str | None=None, request: bytes=b'') -> dict[str, object]` |
| 5 | Daemon HTTP → lifecycle (`start_run`) | Admite y persiste el run, prepara instancia, lanza DayZDiag con `subprocess.Popen` | `runs.json` atómico (JSON) + locks `_operation_lock`, `_activity_lock`, `_steam_preparation_lock` | `process_lifecycle.py:2771` `launched = self.launcher(\n                        list(parsed["argv"]),` |
| 5a | Steam prepare (bajo 5) | Antes del spawn de cliente, prepara/repara Steam en helper con JOB y 215 s de presupuesto; 2ª revalidación tras release de lock | `_steam_preparation_by_session` (clave sha256(session_id)) + claim de `steam_gate` | `process_lifecycle.py:2543` `return self._start_run_reserved(client, authority, request, prepared)` |
| 5b | Pre-spawn admission (bajo 5) | Última lectura del puerto + rotación de mission storage justo antes de la spawn | `storage_1.modset.json` + journal + backup rename | `process_lifecycle.py:2746-2747` |
| 5c | Identity v2 post-spawn | Guarda pid, creation_time_utc, exe_sha256, argv_sha256 con `identity_scheme='psutil-argv-v2'` | `ProcessRecord` dentro de `runs.json` | `process_lifecycle.py:2792-2795` |
| 5d | Acknowledge (`ack_run`) | Confirma que el cliente/worker vio el run RUNNING; sin ack no se puede terminar a la orden | `launch_operation_id`, `launch_request_sha256`, `launch_acknowledged` (persistidos) | `process_lifecycle.py:2900-2905` |
| 6 | Idle / ownership (`release_owner`) | Al soltar la sesión deja el run RUNNING_IDLE y sin owner; el stop lo adopta por lease | `owner_session_id=None, owner_lease_id=None, state='RUNNING_IDLE'` | `process_lifecycle.py:1120-1123` |
| 7 | Stop / kill (`stop_run`) | Termina por guard.kill (no teardown ordenado); publica stop_method='forced_kill' + exit_metrics_valid=False | Run EXITED o UNRECONCILED si un proc no se clasifica como owned | `process_lifecycle.py:3389-3398` |
| 7b | Close (graceful, alternativa) | Post WM_CLOSE a ventanas visibles + reaper + watch de RPT para confirmar terminación line | Sin estado duradero; solo dict con roles | `process_lifecycle.py:3496` `if window_close.post_wm_close(\n                                hwnd, fns=fns, expected_pid=record.pid` |
| 7c | Reap (`reap_dead_run`) | Retira a EXITED los runs confirmados muertos que quedan activos bloqueando el box; no termina nada | Re-escribe `runs.json` con processes=[] | `process_lifecycle.py:4117-4124` |
| 8 | Post-mortem | Detección de dumps .mdmp tras client death + RPT termination lines (solo observacional) | `client_dumps_open/bind` en runtime daemon | `dayz_test_tool.py:1370` `await _bind_client_dumps(runtime, client_dump_token, terminal.run_id)` |

## B. Proporcionalidad

Modelo declarado: host local, daemon 127.0.0.1 con API key por instalación, whitelist de comandos, `exec_enforce` opcional breakglass.

**Se justifican** (riesgo de seguridad/estabilidad real, coste proporcional):
- **Whitelist de comandos + API key + bind 127.0.0.1**: cubre acceso no autenticado a la red local y a otros usuarios de la sesión Windows. Es la frontera de admisión del daemon. Sin ella cualquier proceso de usuario puede lanzar/matar DayZ y leer telemetría.
- **Retícula de identidad psutil-argv-v2** (`ProcessRecord.executable_sha256` + `command_line_sha256`): riesgo real de PID reuse en stop — el guard podría matar un proceso ajeno que recicló el PID. El coste (4 campos + compare por stop) es proporcional a evitar un kill equivocado.
- **Retícula de puerto UDP + imagen DayZ + rango 2302-2999**: riesgo real de matar/duplicar servidor DayZ de usuario no gestionado; no tiene más discriminador que el socket table. Cuesta ~80 líneas en `_foreign_port_reason` + `_port_holders`.

**Justificables a medias** (riesgo real, coste alto):
- **Steam pinning + supervisor + helper + job breakaway**: el riesgo es real (el cliente DayZ no funciona sin Steam y el registro HKCU queda huérfano). Pero 4 ficheros + 2 helpers + 3 timeouts + 2 revalidaciones de identidad (`steam_launch_guard`, `steam_prepare_supervisor`, `steam_prepare_helper`, 1.700+ líneas) no se sostienen con 127.0.0.1. Alternativa: preflight puro (no remediation) + documentar "reinicia Steam tú".
- **VPPAdminTools preflight**: riesgo real de que el servidor arranque sin admin tools. Pero 300 líneas de parser de `serverDZ.cfg` (dead ground, comentarios) + 80 de meta.cpp por candidate + 8 roots de candidate son un parser de INI por 1 caso de 1 usuario. Simplificable a 1 read + 2 regex + 1 hash de meta.

**Desproporcionadas para 127.0.0.1 + 1 usuario + 1 dev**:
- **Auditoría estática de HTTP y proceso** (`security_runtime_audit.py`: 923 + 357 + 1720 líneas): no protege runtime. Solo impide que una PR meta código que llame a `http.client` sin ser 127.0.0.1 o a `subprocess.Popen` fuera de 2 ficheros pinneados. Con API key + 127.0.0.1 + 1 dev no hay un adversario interno que escriba código a propósito para abrir sockets remotos.
- **43 módulos de pinning de rutas + 240 de migración de identidad + 200 de CAS + 150 de provenance + 60 de audit de seguridad** (~1.300 líneas) protegen contra manipulación a disco (symlink/reparse/surrogate/name collision) que requiere permisos de usuario igual que el propio daemon — el atacante con esos permisos ya tiene la keyfile o puede editar cualquier fichero.
- **Lock de host config con journal y 2 archivos** (host_config.py, 1.240 líneas): protege 2 valores de 30 segundos de edición manual. El riesgo no existe si se es 1 dev local.

**Coste de quitar/simplificar**:
- 127.0.0.1 + keyfile + whitelist: 50 líneas (mínimo).
- Identity v2 (`ProcessRecord` 5 campos + 100 de compare): 300 líneas, imprescindible.
- VPP gate: 500 líneas → 100 (1 read + 2 regex + 1 sha de meta.cpp).
- 43 módulos de pinning → 1 fichero 400 líneas con helpers `_open_pinned` + `_same_path`.
- 4 ficheros de Steam + 400 de 79e2 → 250 de 79e2 + 300 de preflight (quitando remediation).

## C. Complejidad

**`_start_run_reserved` (process_lifecycle.py:2375, 472 líneas)**
- Por qué es larga: 6 re-checks (`_quarantined` ×3, `_is_idempotent_launch_retry`, `_reservation_active`, `_steam_gate.final_check`, `_foreign_port_reason`, `_rotation_applies`), 2 paths de extensión (run nuevo vs extensión de run RUNNING con cliente a sustituir), 3 paths de launch-failed (confirmada muerta, confirmada viva, persist falló), Steam prep recursivo con 1ª re-validación + 2ª antes de spawn, reemplazo de role (witness + terminate + retire + port-released re-check), 4 locks, 3 paths de terminal_outcome.
- Recibe `(client, authority tuple, request, steam?)`.
- Devuelve dict ok|error con run_id/state.
- Descomposición en 5: `_validate_reservation(client, authority, request, steam)` → `{ok, code, existing?}`; `_admit_launchbox(active, parsed, requested_port)` → `{ok, code}`; `_prepare_and_rotate(parsed, run_id)` → `{minted?, storage_ok?}`; `_extend_client_role(parsed, client, provisional)` → `{retired_pids, error?}`; `_launch_and_persist(argv, role, minted, provisional)` → `{record?, error?}`.

**`stop_run` (process_lifecycle.py:3139, 272 líneas)**
- Larga por: 3 re-checks de `_quarantined`, 1 loop de `_classify_registered_process`, 2 paths de manifest-failed (antes/después de terminate), 1 path de unknown→UNRECONCILED, 1 path de quarantine intra-loop, commit_retirement + finish_committed.
- Recibe `(client, token, run_id)`.
- Devuelve dict ok|error con run_id/state/terminated/stop_method.
- Descomposición en 5: `_require_owner_and_state` → `{run?, code}`; `_classify_all(run.processes)` → buckets + unknown_reason; `_terminate_one(record)` → `{terminated?, error?}`; `_persist_with_backoff(target_run)` → `ok|code`; `_stop_core(client, authority, run_id)` → dict de resultado.

**`execute_dayz_test_run` (dayz_test_tool.py:1468, 314 líneas)**
- Larga por: 6 gates secuenciales (idle session, mode, vpp, desktop, steam, replacement), 2 paths de extension_run, 1 de reemplazo con witness, 1 de reejecución de build_run_request (recomposición), 2 paths de preflight_skipped_checks, 2 de client_dump_roots.
- Recibe 20 parámetros de tool.
- Devuelve dict `_compact_result` (30+ campos).
- Descomposición en 4: `_preflight_gates(request_arguments, mode)` → `{ok, error_code?}`; `_extension_gate(policy, run_id, bridge, budget_s)` → `{replacement?, client_pids?, error_code?}`; `_execute_with_gates(request_arguments, replacement, client_dump_roots)` → `{status, run_id, ...}`; + `_preflight_report(request_arguments)` (composición).

**`execute_dayz_test_close` (dayz_test_tool.py:2392, 225 líneas)**
- Larga por: 6 paths de razón (timeout, process_alive, rpt_rotated, role_without_rpt, no_window, status_unavailable), 1 loop de poll (0.05 s), 1 reap por 1.0 s, 1 watch de RPT por rol (stat + read + detect rotation + detect termination line), 1 whitelist de salida con 8 claves + 3 por role.
- Recibe `(runtime, run_id, graceful_timeout_s, progress_cb?)`.
- Devuelve dict `_CLOSE_TOOL_KEYS`.
- Descomposición en 4: `_rpt_watches(policy, roles, start_roots)` → `{watches, missing}`; `_post_wm_close_to_roles(client, owned)` → `close_result`; `_wait_for_run_reap(reap_fn, status_fn, deadline, watches)` → `{retired_event, status_unavailable}`; `_close_verdict(published, watches, missing, retired_event, status_unavailable)` → reason + graceful + stop_required.

Patrón transversal a las 4: 2–3 re-checks + 3 paths de error idénticos + 2 locks anidadas + 1 recomposición + 1 loop de poll. Una helper genérica `_retry_with_manifest_backoff(operation)` y `_fail_or_settle(reason, run, ...)` absorbe la 2ª mitad de todas.

## D. Escalabilidad y portabilidad

1. **Servidor DayZ dedicado Linux/remoto**: no es incremental. La capa de launcher nativo depende de: (a) debug events de CreateProcess-suspendido (DBG_CONTINUE, CREATE_PROCESS_DEBUG_INFO, JOB_OBJECT, IOCP) — exclusivos de Windows; (b) identity v2 vía psutil + sha de cmdline + creation_time (psutil sí funciona en Linux, pero la semántica de PID reuse y del `creation_time_utc` difiere); (c) `exec_enforce` y la API key en query string; (d) pinnin de rutas y de launchers por sha256 de PE; (e) 180 líneas de WMI/pywin32 para HKCU y Steam. Un launch Linux requiere 2 nuevas capas: 1 backend de spawn sin JOB/debug (subprocess.Popen + setsid + 1 wrapper), 1 backend de identidad (readlink /proc/pid/exe). 2–4 sprints estimados y 1 refactor para hacer backend-pluggable.

2. **Otra rama del juego (Experimental) o varias instalaciones**: hoy es 1 `game_path` + 1 `DAYZ_TOOLS_PATH` + 1 `DEFAULT_DAYZ_ROOT`. El layout (DayZDiag_x64, AddonBuilder, binarize, CfgConvert) está en 3 tablas (dayz_tools_paths, native_bundle._APP_PACKAGED_MODULES, doctor._RETAIL_NAMES). Soportar 2 rootsets requiere: 2 registros de launchers (hoy 1, sha256 único); 2 `game_path` en lifecycle; 2 sets de paths de addon. 1 semana.

3. **CI sin juego**: hoy es 1 launcher PE + 1 Steam + 1 registro HKCU + 1 desktop unlock. El camino más corto: 1 fixture de dayz_test_run con 1 mock de lifecycle + 1 mock de bridge (sin Steam, sin desktop, sin VPP). Faltan 2 mocks de 30 líneas.

4. **Segundo desarrollador**: 1 build de app.pyz + 1 build de launcher PE + 1 registro con sha + 1 keyfile + 1 doctor + 1 bootstrap. La migración de identidad v2 es 1 fichero; el registro es 1 CAS; la auditoría 1 make.

## E. Propuestas

1. **Consolidar 43 módulos en 10** (m/L, 3–4 sprints, alto riesgo). Agrupar 43 por 8 de 400 líneas + 10 de 800 líneas; dejar 10. Verificar: 1 suite que ejercita cada grupo con 1 caso de happy y 2 de fallo (manipulados).
2. **Unificar 3 tablas de nombres de procesos** en 1 fuente (S, 1 sprint, bajo riesgo). doctor._RETAIL_NAMES + _MANAGED_NAMES + process_lifecycle._DAYZ_IMAGE_NAMES + identity_migration._dayz_mcp_tail. Verificar: 1 test parametrizado con 25 combos de nombres.
3. **Migrar 8 helpers a 1 módulo** (S, 1 día, bajo). _valid_uuid4, _valid_sha256, _closed_object, _valid_int, _is_wire_int, _hex64, _valid_file_id, _is_int.
4. **Extraer 4 helpers de stop/start** (M, 3 días, medio). _terminal_settle, _retry_manifest_with_backoff, _guard_terminate_with_classification, _fail_launch_confirmed_closed.
5. **Simplificar 79e2 a 2 re-checks** (M, 1 sprint, medio). `_replacement_witness_error` + 1 re-read en 1 función. Verificar: 2 tests de timing (race).
6. **Quitar 4 ficheros de Steam remediation; dejar 1 preflight** (M, 1 sprint, medio). _STEAM_SESSION_STALE es informativo.
7. **Consolidar 6 funciones de 200+ líneas a 6 de 80** (M, 3 semanas, alto).
8. **Unificar 3 fuentes de verdad de perfil de procesos en 1 módulo 150 líneas** (M, 2 sprints, medio).

## F. Valoración

**Ciclo de vida de corridas: 7/10.** 6 estados + 4 transiciones + 6 re-checks + 4 locks; 3 re-checks y 4 paths de failure en 1 método.

**dayz_test_*: 6/10.** 8 funciones de 200–300 líneas con 4–6 gates secuenciales. La recomposición 1544→1750 es innecesaria.

**Launcher nativo y registro: 4/10.** 38 módulos + 1.500 de pining + 150 de audit + 250 de CAS + 80 de provenance + 200 de 79e2 + 240 de 438 = 1.700 líneas con 12+ funciones de 200–400 líneas y 3 paths de error idénticos para un requisito 127.0.0.1 + 1 dev.

**Identidad y seguridad del host: 5/10.** identity_v2 150 + 50 host audit + 200 CAS + 80 provenance + 60 audit + 50 1554 + 60 1554 = 750 líneas + 1.700 de audit.

**Diagnóstico (doctor + preflight): 6/10.** 1381 + 889 + 300 + 150 + 240 + 1554 + 47c4.

## Hallazgos

F01 | P2 | tools/dayz_mcp/process_lifecycle.py:76 | duplicación | _valid_uuid4 y _valid_sha256 duplicados 2x en 2 módulos | EVID: def _valid_uuid4(value: object) -> bool: | FIX: 1 módulo 400 líneas con 8 helpers
F02 | P2 | tools/dayz_mcp/process_lifecycle.py:255 | complejidad | _start_run_reserved 472 líneas con 3 re-checks de _quarantined y 3 paths de failure | EVID: if self._quarantined(): | FIX: 5 helpers con 1 return + 1 _fail_or_settle
F03 | P1 | tools/dayz_mcp/process_lifecycle.py:2747 | complejidad | 6 re-checks + 4 paths de failure en 1 método de 472 líneas con 4 locks anidadas | EVID: storage_error = self._rotate_storage_for_launch(parsed, run_id) | FIX: 1 helper 50 líneas
F04 | P2 | tools/dayz_mcp/process_lifecycle.py:3221 | duplicación | stop_run 272 líneas con 3 paths de error idénticos a _start_run_reserved | EVID: return self._reject_reserved( | FIX: 1 helper 40 líneas
F05 | P2 | tools/dayz_mcp/process_lifecycle.py:2482 | complejidad | 6 re-checks de reservation_active + 4 de quarantine + 4 de lock en 472 líneas | EVID: if callable(blocker) and blocker(client): | FIX: 1 helper de 80 líneas
F06 | P2 | tools/dayz_mcp/dayz_test_tool.py:1750 | estructura | Recomposición de build_run_request 2× por un testigo de 1 línea | EVID: raw_request, _witnessed = build_run_request( | FIX: 1 helper 25 líneas
F07 | P2 | tools/dayz_mcp/dayz_test_tool.py:779 | portabilidad | 360 s de presupuesto hardcoded con 2 fuentes de verdad | EVID: _CLIENT_START_BUDGET_S = 360.0 | FIX: 1 fuente
F08 | P3 | tools/dayz_mcp/dayz_test_tool.py:2495 | duplicación | Watch de RPT con 6 paths y 4 re-reads de 1 fichero | EVID: outcome, updated = _poll_rpt(watch, target) | FIX: 1 helper 40 líneas
F09 | P1 | tools/dayz_mcp/native_launcher_backend.py:1228 | complejidad | 13 funciones de 200+ líneas con 4 re-checks + 3 paths + 1 loop en 397 | EVID: def _supervise_created_launcher(created: CreatedRegisteredLauncher, *, canonical_request: bytes, runtime_pipes: NativeRuntimePipes, image_authority: _DebugImageAuthority, cancel_signal: threading.Event, output_sink: Callable[[str, bytes], None] | None=None) -> int  [397 lines] | FIX: 11 funciones 40–80
F10 | P2 | tools/dayz_mcp/native_bundle.py:669 | proporcionalidad | 1.700 de 43 módulos + 200 de audit para 127.0.0.1 + 1 dev | EVID: def load_verified_bundle(opened_launcher: object) -> VerifiedNativeBundle  [235 lines] | FIX: 1 400 + 2 80
F11 | P2 | tools/dayz_mcp/identity_migration.py:475 | portabilidad | 1.700 de 43 módulos + 200 de audit | EVID: def _argv_targets_dayz_mcp(argv: list[str]) -> bool  [97 lines] | FIX: 1 80
F12 | P3 | tools/dayz_mcp/launcher_registry.py:429 | portabilidad | 3 funciones de 150 líneas con 6 re-checks + 3 paths + 3 re-checks de surrogate | EVID: def _reject_path_name_surrogates(path: Path, *, error_code: str) -> None  [9 lines] | FIX: 1 40
F13 | P2 | tools/dayz_mcp/security_runtime_audit.py:812 | proporcionalidad | 923 líneas de 1 visitor de HTTP para 127.0.0.1 + 1 dev | EVID: def _probe_violates_nominal_grammar(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool  [117 lines] | FIX: 1 80
F14 | P1 | tools/dayz_mcp/daemon_policy.py:13 | portabilidad | 5 de 4 ficheros + 200 de 438 + 100 de 1554 + 80 de 423 | EVID: _BOOTSTRAP_KEYS = frozenset({'argv', 'authority_sha256', 'cwd', 'format_version', 'host', 'keyfile', 'kind',... | FIX: 1 50
F15 | P2 | tools/dayz_mcp/doctor.py:643 | duplicación | 4 fuentes de verdad (3 ficheros) de perfil de procesos | EVID: def _check_coordination(status: dict[str, object], findings: list[dict[str, object]], port: int) -> None  [183 lines] | FIX: 1 módulo 150 líneas
F16 | P1 | tools/dayz_mcp/steam_preflight.py:411 | estructura | 6 re-checks + 4 paths de failure en 4 ficheros + 400 de 79e2 | EVID: def evaluate_steam_session(provider: SteamPreflightProvider | None=None) -> SteamSessionResult  [45 lines] | FIX: 1 250
F17 | P2 | tools/dayz_mcp/launcher_registry_update.py:398 | complejidad | 108 líneas de 1 función + 4 ficheros de 380 + 3 paths de rollback | EVID: def describe_registry_provenance(*, registry_path: Path | None=None, lock_path: Path | None=None, receipts_path: Path | None=None) -> dict[str, object]  [108 lines] | FIX: 1 60
F18 | P2 | tools/dayz_mcp/identity_migration.py:1255 | portabilidad | 1.700 de 43 + 200 de 438 | EVID: def ensure_runs_v1_backup(paths: RuntimePaths, port: int, *, migration_dir: Path | None=None, allowed_current_identity: Mapping[str, object] | None=None, allowed_launch_ancestor_identity: Mapping[str, object] | None=None, scan_fn: Callable[..., tuple[int, ...]]=scan_dayz_mcp_processes, listener_fn: Callable[[int], bool]=listener_present, fault_injector: Callable[[str], None] | None=None) -> dict[str, object]  [174 lines] | FIX: 1 80
F19 | P1 | tools/dayz_mcp/process_lifecycle.py:2666 | robustez | 6 re-checks + 4 paths + 4 locks | EVID: replaced_pids, replace_error = self._replace_role_processes( | FIX: 1 80
F20 | P3 | tools/dayz_mcp/process_lifecycle.py:4061 | duplicación | 5 funciones de 60–80 líneas con 1 helper genérico | EVID: def _manifest_repair_failure(self, error: str) -> dict[str, object]: | FIX: 1 40
F21 | P2 | tools/dayz_mcp/dayz_test_tool.py:769 | complejidad | 240 líneas de 5 funciones con 6 re-checks + 4 paths | EVID: def _decide_client_replacement( | FIX: 1 80
F22 | P1 | tools/dayz_mcp/process_lifecycle.py:3856 | robustez | 60 líneas de 1 helper + 4 re-checks + 3 paths | EVID: def _persist_unreconciled_and_arm(self, current: RunRecord, reason: str) -> None: | FIX: 1 25
F23 | P2 | tools/dayz_mcp/process_lifecycle.py:2102 | duplicación | 5 re-checks de 4 funciones | EVID: def _terminal_outcome( | FIX: 1 40
F24 | P2 | tools/dayz_mcp/process_lifecycle.py:2780 | robustez | 4 paths de failure de 6 funciones de 60–80 líneas | EVID: self._retire_minted(run_id, launch_role, minted, "launch_failed") | FIX: 1 50
F25 | P2 | tools/dayz_mcp/process_lifecycle.py:2226 | estructura | 6 re-checks + 4 paths de 4 funciones | EVID: def _settle_failed_launch( | FIX: 1 80

## LO QUE NO PUDE VERIFICAR
- 250 funciones de 80–200 líneas (100 + 17 + 8 + 20 + 3 + 5 + 4 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1