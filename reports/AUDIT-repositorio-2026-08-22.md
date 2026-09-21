# Auditoría integral del repositorio DayZ_MCP_dev

**Fecha:** 2026-08-22 · **Tipo:** read-only (no se ha modificado nada del repo; este informe es el único fichero creado) · **Alcance:** todo el árbol visible en este checkout.

**Método:** 4 pasadas paralelas por áreas (superficie MCP `server.py`; capa coordinación/daemon/transporte; stack launcher/seguridad/procesos; tests + higiene git + documentación), más verificación directa de las citas load-bearing antes de escribirlas aquí. Cada hallazgo lleva etiqueta de severidad y confianza: **CONFIRMADO** (código leído con `path:línea`) o **SOSPECHOSO** (patrón detectado, no reproducido).

## Qué NO se ha verificado (honestidad de cobertura)

- **El código Enforce (`addon/`: MCPBridge.c 78 KB, MCPClientBridge.c 74 KB, etc.) NO ha sido auditado línea a línea.** En este checkout ni siquiera está materializado (sparse-exclude); la revisión profunda del puente in-game es una pasada pendiente.
- El diff del hermano `DayZ_MCP\MCPDialogController.c` vs git se comparó **por tamaño exacto (+978 B), sin hash**.
- Las ACL de `tools\.dayz_mcp.key` se observaron con `icacls` en una pasada; no se re-verificaron después.
- El escaneo de secretos cubrió los directorios de documentación (limpio); **no** binarios ni todo `tools\`.
- La suite de tests se ejecutó una vez (2066 tests, OK); no se midió flakiness repetida.
- `origin/main` no se contrastó contra GitHub remoto, solo contra HEAD local.
- Los totales LOC varían ±3 % según el momento de lectura (los `.bak` junto a los módulos engordan conteos).

---

# Resumen ejecutivo

El núcleo funcional (puente HTTP → daemon → juego, whitelist server-side, leases, lifecycle de procesos, suite de 2066 tests en verde) está **genuinamente bien construido**: fail-closed real, PID-identity defendida, escrituras atómicas, TTLs monotónicos. Los tres problemas serios son otros:

| # | Hallazgo | Sev | Conf |
|---|---|---|---|
| 1 | **Gestión de secretos contradictoria con el modelo fail-closed**: clave viva con DACL que incluye SIDs de sandbox; claves en texto plano dentro de configs de misión/perfiles del juego; clave como query param en cada llamada | HIGH | CONFIRMADO |
| 2 | **Sobreingeniería masiva cuantificable: ~12–14k LOC (~40 % del código Python) eliminables sin pérdida funcional**, repartidos entre anti-tamper ceremonial del launcher (~5k), WAL/tombstones que protegen estado que se descarta al reiniciar (~4,7k), y capas policy/credenciales duplicadas (~1,3k) | MED | CONFIRMADO |
| 3 | **La capa operativa (HANDOFF.md, PROJECT-MAP.md, decisions/, plans/, reviews/, CLAUDE.md…) no está en git**: un clon fresco pierde el manual completo; además hay módulos sin trackear de los que depende la suite en verde → el checkout actual no es reproducible desde cero | HIGH | CONFIRMADO (intención desconocida) |
| 4 | Contrato documentado de `wait_for` en timeout **no coincide con el código** (README dice `ok:true/satisfied:false`; el código devuelve `ok:false`) — exactamente el tipo de desajuste que rompe agentes cliente | MED | CONFIRMADO |
| 5 | ~11,9 GB de escombros de fases pasadas (`_fase*`, `_poc`, `_s0`, `_gamemaster_h0_*`, bundle duplicado de 40 MB, ~30 `.bak` dentro del propio paquete) sobre una ruta OneDrive sincronizada | MED | CONFIRMADO |

---

# 1. Higiene del repositorio / git

### 1.1 La capa documental operativa no está versionada — HIGH
- **CONFIRMADO**: 257 ficheros trackeados vs 106+ entradas untracked top-level. Sin trackear: `HANDOFF.md`, `PROJECT-MAP.md`, `AGENTS.md`, `CLAUDE.md`, `NEXT-SESSION-PROMPT.txt`, `decisions/`, `plans/`, `reviews/` (306 ficheros), `reports/`, `test-contracts/`. Trackeados sí: README, QUICKSTART, product-spec, dayz-mcp-architecture, LICENSE, `playbooks/`.
- Consecuencia: un clon fresco recibe solo la capa "pública pulida" y pierde el manual operativo completo. Si es deliberado (privacidad de un repo público), falta un segundo respaldo: hoy esa capa existe únicamente en OneDrive. **SOSPECHOSO el intento, CONFIRMADO el hecho.**
- Recomendación: decidir destino (rama privada, vault con sync propio, o release assets) y documentar la decisión en el README.

### 1.2 Checkout no reproducible desde cero — HIGH
- **CONFIRMADO**: la suite pasa hoy (ver §6) pero depende de módulos/fuentes presentes en disco y sin trackear; además `addon/` está en git pero excluido por sparse-checkout (`/*` + `!/addon/`), así que este checkout no puede construir el PBO sin apuntar al árbol hermano o des-sparsear (QUICKSTART.md:6-8 lo documenta).
- Recomendación: `git add` de lo que la suite necesita; decidir si `addon/` se materializa o se formaliza el contrato de árbol hermano en CI.

### 1.3 Escombros acumulados — MED
- **CONFIRMADO**: raíz con `_backups/`, `_compile/`, `_fase1/2/3/`, `_poc/`, `_restore/`, `_s0/`, `_step0/`, `_gamemaster_h0_build/`, `_gamemaster_h0_deploy_20260702_013948/`, `_gamemaster_h0_release/` ≈ **11,9 GB** sobre ruta OneDrive.
- Trío raíz `*_bak_infdrive_20260820` sin trackear (README_bak 10.978 B vs live 17.504 B trackeado; HANDOFF_bak 159.416 B vs live 226.757 B **también sin trackear**; architecture_bak 19.555 B vs live 20.273 B).
- **~30 `.bak_pre_*_20260819` bajo `tools\`**, incluidos **dentro del paquete**: `dayz_mcp\process_lifecycle.py.bak_pre_fencing_20260819`, `session_coordination.py.bak_pre_agujero1_20260819`, `loopback.py_bak_infdrive_20260820`, `server.py_bak_infdrive_20260820`, `build_native_launcher.py.bak_*`. Peligro real: `security_runtime_audit._runtime_sources` hace rglob de `*.py` bajo el paquete y estos ficheros contaminan el barrido. Git ya guarda la historia → borrar.
- `tools\native-launchers\dayz-test-v1.lfheli-rolled-back-20260722-234857\` = **copia íntegra ~40 MB** del bundle (runtime+exe+pyz), solo mencionada en `publish\boundary.py`. Archivar fuera o borrar.
- `tools\_broker\` (logs e2e + **`.e2e.key` comprometida en disco, 14 B**), `tools\_fase4a\`, `tools\_session_coordination\`, `tools\spike0\` = artefactos de fases cerradas. Borrar tras rotar la key.
- `.gitignore` cubre bien lo machine-local (`tools/.dayz_mcp.key`, `approved-launchers.receipts/`, `.venv-mcp/`, `_audit/`, `_server/_client`, `__pycache__`), pero **no** cubre `_*` de raíz ni `*.bak*`/`*_bak_*`.

### 1.4 Duplicación de historial en tres capas — LOW
- HANDOFF post-línea 1894 (archivo), `reviews/` (306 ficheros de prompts/verdicts sesión a sesión) y `reports/rollback/` (copias congeladas íntegras de decisions/plans/HANDOFF/product-spec). No es corrupción, pero triplica superficie de búsqueda. Consolidar en una sola capa de archivo.

---

# 2. Seguridad

### 2.1 Hallazgos reales

| ID | Sev | Conf | Evidencia | Problema → Arreglo |
|---|---|---|---|---|
| S1 | HIGH | CONFIRMADO | `tools\.dayz_mcp.key` — DACL observada con `icacls`: `Willy\CodexSandboxUsers:(M)` + ~13 SIDs de sandbox; gemela `tools\_broker\.e2e.key` | La clave loopback viva es legible/reemplazable por procesos sandboxes → mover fuera del árbol con DACL protegida (el patrón correcto ya existe en `host_config.py:808-831`); **rotar ambas claves** |
| S2 | HIGH | CONFIRMADO | `install_mcp.py:976-1003` (escrituras verificadas en :987-998), `install-mcp.ps1:342-361`; creación débil `open("x")` sin DACL en `install_mcp.py:918` | La API key se escribe **en texto plano dentro de configs del juego**: `tools\_mcp_config\server_profiles\dayz_mcp.json`, `client_profiles\dayz_mcp.json`, `mpmissions\dayzOffline.chernarusplus\dayz_mcp.json` (+ copias opcionales vía `--mission-path`). Riesgo de fuga vía empaquetado de misión → dejar de hornear keys en contenido del juego; DACL al keyfile; rotación |
| S3 | MED | CONFIRMADO | `accredited_daemon_transport.py:281-283` (key como query param en cada llamada); `loopback.py:765-793` (`_seed_bridge_config` escribe `{"url","key","pollHz"}` en el profiles dir del juego) | Cualquier proceso que lea la config del puente obtiene la clave en claro; el check es correcto (`hmac.compare_digest`, `loopback.py:2427-2431`) → pasar la key a header (`X-DayZ-Key`) y DPAPI-proteger la copia del puente. Nota: el README documenta honestamente por qué va en URL (limitación de `RestContext.SetHeader` del motor) — el arreglo aplica al tramo Python↔daemon, no al tramo motor |
| S4 | LOW | CONFIRMADO | `bootstrap_parent.py:80-81`, `identity_migration.py:667-668`, `secure_launcher.py:250-256` | Except-catchalls que colapsan bugs y ataques en un mismo camino/código → loguear clase+contexto localmente antes de colapsar |

### 2.2 Lo que está bien (positivos verificados)

- Generación de clave correcta: CSPRNG 256-bit (`install_mcp.py:942`, `install-mcp.ps1:21-34`).
- Defensas anti path-traversal sólidas: whitelist relativa + containment (`launcher_registry.py:403-426`, `dayz_test_request.py:108-148`).
- TOCTOU del lock de registro resuelto (fstat identidad antes/después de `LockFileEx`, `registry_lock.py:91-123`).
- Whitelist **realmente server-side**: `WHITELISTED_COMMANDS` rechazada en `_enqueue_command` con 400 `not_whitelisted` (`loopback.py:1336-1337`); schemas por comando (:262-650); `exec_enforce` opt-in con allowlist de cadena exacta y auditoría durable (:1417-1525) — genuinamente ajustado.
- PID-reuse bien defendido (normalización creation-time + fencing AMBIGUOUS: `loopback.py:1834-1885`, `instance_fence.py:92-113`; atribución TCP owner-única `accredited_daemon_transport.py:78-112`). TTLs en `time.monotonic` inmunes a skew (`session_coordination.py:1116-1124`). Worker pools acotados.
- Caps de tamaño: 1 MiB entrada (`loopback.py:154,2449-2453`), 4 MiB salida (:27,289-291).
- Estado runtime deliberadamente FUERA de OneDrive (`%LOCALAPPDATA%\DayZ_MCP`, `runtime_state.py:196-200`); lecturas pinadas stat-identidad que fallan cerradas (:1795-1831), cuarentena de snapshots corruptos (:1616-1646), escrituras temp+fsync+replace (:1890-1948). El diseño anti-OneDrive-corruption está bien resuelto donde importa.

### 2.3 Huecos del perímetro (documentados pero reales)

- **18 verbos se saltan la validación de schema del daemon** vía `_SCHEMALESS_COMMANDS` (`loopback.py:97-116`; los 18 contados uno a uno): confían en la validación upstream de `server.py`. Gap documentado, debilita al daemon como última línea. Añadir schemas mínimos o reducir la lista.
- **Los leases serializan, no autentican**: mutaciones exigen `lease_token` (`loopback.py:1190-1204`) pero cualquier poseedor de la key puede mintearse uno, y los comandos read-only no necesitan ninguno (`session_coordination.py:19-39`). El fencing para inyección cross-*juego*, no cross-client — coherente con threat-model single-user, pero conviene decirlo explícito en la doc de seguridad.
- **Puertas de test dentro del ServerState de producción**: `TestIdentityOverride` / `install_bound_peer(run_id="testrun")` (`loopback.py:653-684, 878-907`). Hoy inalcanzables vía HTTP (LOW); idealmente tras flag de build.
- Escaneo de secretos en dirs de docs: limpio (solo prosa de política e IDs de test). Pendiente barrer binarios y resto de `tools\`.

---

# 3. Bugs y robustez por capa

## 3.1 Superficie MCP (`server.py` + secundarios)

| Sev | Conf | Evidencia | Hallazgo |
|---|---|---|---|
| MED | CONFIRMADO | `README.md:110` + `tools/README-mcp.md:124` dicen «wait_for en timeout devuelve `ok:true, satisfied:false`»; el código devuelve `"ok": satisfied` → **ok:false** (`server.py:1888-1897`; verificado por lectura directa) | Contrato documentado ≠ código. Un agente que gatee en `ok` clasifica mal cada timeout. Arreglo trivial: alinear doc o código (decisión: ¿cuál es el contrato deseado?) |
| MED | CONFIRMADO | int 0/1 del Enforce vs bool Python (`server.py:1890`), `logs_since` devuelve `"ok":1` int (:2782); `execute_wait_for_box` devuelve envelope `ok:false` en vez de raise (:2298-2303) mientras sus hermanos lanzan ToolError | Caos de tipos/estilo de fallo en el campo `ok` → normalizar a bool y decidir una sola política raise-vs-envelope |
| MED | CONFIRMADO | Rama fallback muerta que crashearía si se alcanzara: payload no-dict en `server.py:610-615, 666-671` frente al contrato `enqueue_command -> tuple[int, dict]` (`loopback.py:1162`) | Eliminar la rama o hacerla defensiva real |
| MED | CONFIRMADO | `Runtime.call_bridge`/`enqueue_bridge` embebidos llaman enqueue **síncrono dentro de handlers async** (`server.py:604-618, 658-672`) cuando el authorize puede fsync el audit durable (`loopback.py:1187-1192`); el hermano `call_exec_enforce` sí usa `asyncio.to_thread` (:623) | Bloqueo del event loop en modo embedded → envolver en `asyncio.to_thread`, igualando a call_exec_enforce |
| MED | SOSPECHOSO | `telemetry_read(mode="fixture_jsonl", path=...)` reenvía **cualquier ruta del host** al puente (`server.py:2896-2921`), sin la política fail-closed `is_allowed_profiles_dir` que sí aplica `log_tail.py:98-118` | Forwarder de rutas arbitrarias → aplicar la misma política de directorios permitidos |
| LOW | CONFIRMADO | Clamps silenciosos inconsistentes: poll ≥0,5 s (:1945); wait_for caps timeout a 600 sin avisar (:1941) mientras otras tools *rechazan* >300 (`_timeout` :1420-1429); `world_time_set` acepta 31 de febrero (:3159-3166); igualdad float en handbrake (:3447) y floats redondeados en `vehicle_trace.py:665` | Unificar: clamp con warning en respuesta, o rechazo explícito |
| LOW | SOSPECHOSO | Decode utf-8/replace de logs cp1252 (`log_tail.py:184-188`) | Patrones con acentos podrían no matchear nunca; offsets seguros porque el split es por bytes |

Código muerto confirmado (imports verificados): `_offset_before_last_lines` (`server.py:1634-1657`), `ServerConfig.session_ttl_s`/`.runtime_dir` nunca leídos (:497-498), imports sin uso `Field`(:22)/`Annotated`(:18), cola inalcanzable de `compute_bridge_ready` (:386), ramas idénticas de `map_schema_error` (`playbook_tool.py:122-126`). ~50 LOC.

Estructura: todo vive dentro de `build_app()` de **~1.488 líneas** (`server.py:2332-3819`) — god-function; `validate_trace` ~399 líneas (`vehicle_trace.py:282-680`), `_validate_course` ~284 (:786-1069). Ceremonia por handler ≈85 %: 35 sitios `call_bridge`, 46 bloques `tool_lock`, 35 `_timeout` con el mismo esqueleto (`world_spawn` :2793-2802 tiene 10 líneas y lógica única cero). Un helper `_bridge_call(...)` ahorra ~100 LOC realistas. Convención de errores fragmentada: literal `"bad_args"` ×69 vs códigos con campo vs prosa vs token-table (:170-187).

## 3.2 Coordinación / daemon / transporte

| Sev | Conf | Evidencia | Hallazgo |
|---|---|---|---|
| MED | CONFIRMADO | `_cancel_operation_remote_until_resolved` sondea `/session/cancel-operation` **cada 50 ms sin deadline** (`control_client.py:534-548`) | Daemon atascado en `resolving` → cliente colgado sosteniendo `_transition_lock` para siempre. Poner deadline+backoff |
| MED | SOSPECHOSO | `RELEASE_AUDIT_TIMEOUT_S = 0.05` (`session_coordination.py:43`); resultado `(False, True)` ruido-no-latch (:2270-2286) | Cualquier fsync >50 ms marca releases `cleanup_degraded:"audit_failed"` crónicamente bajo carga → subir presupuesto o hacerlo async |
| LOW | CONFIRMADO | Thread-watcher que bloquea en `terminal_event.wait()` sin timeout (`session_coordination.py:2343-2347`) | Fuga de hilo si el evento nunca dispara |
| LOW | CONFIRMADO | Tragado silencioso de escrituras de audit fence/exec en 6 sitios (`loopback.py:1113-1116, 1514-1517, 1584-1589, 1825-1829, 2145-2150, 2340-2345`) + `note_run_reaped` (:1912-1921) | Denegaciones sin audit trail debilitan la trazabilidad exec |
| LOW | CONFIRMADO | `JsonlAuditWriter` **reescribe el fichero entero (≤5 MB) en cada evento** de auditoría (`runtime_state.py:339-349`); `write_once` escanea hasta 6 ficheros rotados parseando línea a línea por llamada (:263-294) | O(n) por evento; amplifica AV/OneDrive-scan → append O(1) |
| INFO | CONFIRMADO | Danza manual `condition.release()/acquire()` ~15× (p.ej. `session_coordination.py:3476-3525`) | Hoy los emparejamientos son consistentes (solo nesting gate→condition); un despiste futuro = deadlock. Merece helper RAII-style |

Positivo: bind race resuelto con probe winner-exits + elección por file-lock (`daemon.py:715-757`); TOCTOU authorize→enqueue compensado (`session_coordination.py:1272-1313`, aborts en `finally` `loopback.py:1282-1296`).

Seguridad de esta capa: credencial estática compartida en plaintext; sin endurecimiento DACL encontrado en `pinned_keyfile` (SOSPECHOSO ausencia); key en URL (ver S3); fencing de resultados correcto (epoch seals `session_coordination.py:1009-1018`, result-fencing :2001-2035).

## 3.3 Launcher / procesos / instalación

| Sev | Conf | Evidencia | Hallazgo |
|---|---|---|---|
| HIGH (portabilidad) | CONFIRMADO | `tools\approved-launchers.json`: `root` = ruta absoluta OneDrive + file-id NTFS + volume serial | La acreditación queda anclada a esta máquina/placeholder → rompe en otro equipo o si OneDrive recrea el placeholder. Derivar root del checkout o mover bundle+registro fuera de OneDrive; documentar rebuild |
| MED | CONFIRMADO | Parseo frágil por regex de texto humano de `claude mcp get` y layout de `netstat` (`doctor.py:288-308`, `install_mcp.py:508-571`, `orphan_guard.py:345-363`) | Un cambio de CLI/locale deja doctor en CONFIG_UNREADABLE silenciosamente → parsear los ficheros de config directamente (host_config ya lo hace) |
| LOW | CONFIRMADO | `except: pass` sin log en hilos de cleanup (`process_lifecycle.py:882-885, 1983-1985, 2007-2010`) | Fail-safe deliberado pero mudo → línea de log local-only |
| LOW | SOSPECHOSO | Registro/lock anclados a `Path(__file__).parents[1]` (`registry_lock.py:31`, `launcher_registry.py:25`) | Correcto solo en checkout dev; instalado como wheel miraría a site-packages → anclar a ruta de config explícita |

Rutas hardcodeadas del autor: **dentro de `tools\dayz_mcp\` cero** (limpio, verificado). Fuera del paquete: `diag_server_ownership.py:4,8`, gates tramoA/B, `checks\check_task9_protocol_deployment.py:10,52` (ObsidianVault), `publish\export_public_repo.py:26-29` — asumible si es privado, rompe si se publica.

---

# 4. Sobreingeniería / sobrecomplejidad (cuantificada)

Total auditado ≈ **30k+ LOC Python** en tres capas: superficie MCP 6.290 · coordinación/daemon/transporte 13.458 · launcher/seguridad/procesos ~17,5k scoped (incluye scripts de instalación). Estimación total reducible **sin pérdida funcional ni de seguridad: ~12–14k LOC (~40 %)**, más artefactos (276 JSONs de receipts, 40 MB de bundle duplicado, 11,9 GB de escombros).

## 4.1 Capa launcher/seguridad — anti-tamper ≈ 35–40 % del scope

Reparto medido: teatro anti-tamper ≈ 6.900 LOC · funcionalidad real ≈ 8.600 · solapamiento de valor genuino (PID-safe terminate, JobObject cleanup, watchdogs, backups pre-prune) se conserva.

| ID | Qué es | Evidencia | Reducible |
|---|---|---|---|
| R1 | Maquinaria de allowlist que decide qué exe lanza DayZ (registry 459 + lock 127 + native_pe 76 + native_bundle 846 con allowlists WinSxS comctl32/GAC VisualBasic :144-175 + bootstrap_parent 81 + win32_fileinfo 57) | Verificado import a import | **≈1.550 LOC** → un fichero `{path, sha256}` comprobado antes de CreateProcess (~80 LOC) da la misma garantía local |
| R1b | Ceremonia de receipts: `approved-launchers.receipts\` = 69 directorios × 4 JSONs (~276 ficheros) **para UN launcher** | Conteo en disco | Artefactos, 0 LOC runtime → git history basta |
| R1c | `launcher_registry_update.py` (504): ops-only, único importador = checks | — | ≈500 → plegar en script de check |
| R2 | Acreditación que se computa y se descarta: `secure_launcher.py:97-98` hace `del accredited_paths`; request_path_authority (585) + media build_native_launcher (~400) solo sostienen bookkeeping | — | ≈800 → conservar el handle-pinning, tirar el diccionario de identidades |
| R3 | `lifecycle_cli.py` (174): duplicado runtime-morto de `app_main._lifecycle_main` | — | 174 → borrar |
| R4 | El bundle se verifica contra **bytes vivos del dev-tree** de 14 módulos (`native_bundle.py:814-820`): editar cualquier fuente bricks la acreditación hasta rebuild | — | Simplificación: verificar solo bytes sellados dentro del dir del bundle |
| R5 | `identity_migration.py` (1.428): gate de migración one-shot de UN JSON que **escanea cada python.exe del sistema en cada boot, para siempre** | — | ≈1.300 → confirmar migrado y archivar tras flag admin |
| — | Reproibilidad triple-build (clean-1/clean-2/offline, `build_native_launcher.py:1081-1102`) + re-pin por máquina (`relock_toolchain.py`, 276): "reproducible" solo tras relock local → decorativo en la práctica | — | ≈400 builder + 276 relock → single build + hashes registrados |
| — | Maquinaria de aprobación debug-event (native_debug_state 297 + native_child_announcement 124 + ~60 % native_launcher_backend ~1.000): protocolo ContinueDebugEvent/child-self-attestation para hijos que ya son binarios hash-pineados | — | ≈900–1.200 → handshake por pipe simple |
| — | CLI-pinning de claude/codex en install_mcp (manifest+fixtures+probes ≈400, `install_mcp.py:36-481`) — la transacción de registro en sí es correcta (argv-list, shell=False :633-640; remove-add transaccional :1033-1078) | — | ≈250–300 |

Además, **mal ubicado**: `security_runtime_audit.py` (1.751 LOC) es un auditor dev-time dentro del paquete shippable (solo lo importan tests + un name-check en `native_launcher_backend.py:901`) → mover a `tools\checks\`.

## 4.2 Capa coordinación/daemon — −60 % estimado (13.458 → ≈5.150)

El hallazgo central: **la máquina de estados fault-marker protege estado que se tira al reiniciar**. `consume_previous_generation()` limpia active/releasing/queue y emite `daemon_restart_invalidated` pase lo que pase (`runtime_state.py:1239-1269`), porque `SessionCoordinator` es puramente en memoria. Por tanto:

- La fase-machine armed→prepared→committed→published sha-chained de 13 fases (`runtime_state.py:33-101`) + `CoordinationFaultStore` CAS con `msvcrt.locking` (:418-627) + protocolo admin de repair (`loopback.py:2977-3085`) defienden una ventana de crash de milisegundos cuyo fallback (invalidación total) ya existe.
- Tombstones de operación + reservas de queue + compensación grant-in-flight ≈ **1.800 de las 3.735 LOC** de `session_coordination.py`.
- `LifecycleRecoveryFaultStore` (event log hash-chained + receipts + pointer CAS, ~500 LOC, `runtime_state.py:665-1172`) graba evidencia de "un cleanup falló".

Duplicaciones confirmadas:
- `daemon_policy.py:86-140` duplica **verbatim** helpers de `daemon_policy_contract.py:18-72` (~60 LOC); `load_normal_daemon_policy()` existe **dos veces** (`daemon_policy.py:319` y `normal_daemon_policy.py:158`) parseando el mismo manifiesto de 10 claves. No hay ni una jerarquía de clases: el smell es triplicación, no herencia.
- `authority_sha256` (`daemon_policy_contract.py:115-130`) es un SHA de los propios campos del dataclass — checksum de autoconsistencia presentándose como "authority": no prueba quién lo emitió. Hooks de revalidación inyectados con `object.__setattr__` sobre dataclass frozen (policy:330/364/404/413, normal:165).
- `RefreshingDaemonCredential` compara su autoridad consigo misma hasta 3× por request (`daemon_credential.py:85-143, 292-321`).
- JSON-espeja-JSON: cada session/enqueue/lifecycle re-persiste el snapshot completo a `coordination.json` tras revision guard (`loopback.py:2606-2627`).
- Gemelos de wire mantenidos a mano: `ControlIdentity` re-implementa `ClientIdentity` campo a campo (`control_client.py:41-74` vs `session_coordination.py:53-99`).
- Stack POC legacy vivo: `LoopbackServer`/`loopback.main` paralelo al stack daemon (`loopback.py:3149-3233`) — **−1.700 LOC** borrándolo; bloque de contador fence-reject pegado 3× (:1382, 1450, 1482); reconstrucción de survivor-list pegada 4× (:1527, 1556, 2124, 2333); dos idiomas de envelope JSON.
- Enum + tres Optionals independientes en `ControlClient.state` (`control_client.py:135-141`): boolean-trio disfrazado.

Mapa objetivo propuesto: conservar `daemon_contract`(104) · `instance_fence`(171) · `native_broker_protocol`(252, ortogonal y apropiado) · `request_path_authority`(585, trabajo real) · `lease_supervisor`(213); reescribir pequeño: coordination ~500 · loopback ~1.500 · runtime_state ~750 (conservando atomic-write/pinned-read/audit) · daemon ~600 · control+credential ~500 · policy pair ~250 · transport ~230.

## 4.3 Superficie MCP — grasa realista ≈ 200–300 LOC (~4 %)

La ceremonia typed-tool **está justificada** (FastMCP necesita firmas reales para generar schemas; las descriptions son load-bearing para clientes LLM). Grasa concreta: cuádruple validación de args de ui_dialog (handler `server.py:2110` + `loopback.py:591→1340` + Enforce + pydantic); duplicación enqueue/error entre ambos runtimes (~60 LOC); `Runtime.audit_exec` (:687-699) re-implementa `core.make_exec_auditor` (`core.py:133-150`); acoplamiento a APIs privadas (`_patch_public_argument_alias` `server.py:1461-1479`, `playbook_tool.py:208-222`); 69 literales `"bad_args"` → un helper `bad_args(field)`. Split mecánico de `build_app()` por dominios (session/vehicle/ui/world) recomendado aunque solo sea por navegabilidad.

---

# 5. Tests

- **CONFIRMADO**: `python -m unittest discover -s tests -t .` → **Ran 2066 tests in 231,9 s — OK**. 4 skips, todos justificados (gates de privilegio Windows-symlink). Sin tests asert-less ni skips incondicionales. Suite sana.
- Fuente histórica de flake identificada y documentada in-code: tests de integración con deadlines de polling (`tests/test_bug046_startup_deadlock.py:775-781, 838-850`; comentario de carrera medida "1/3 bajo carga de suite completa" :1076-1077). Vigilar reintentos en CI; no acción urgente.
- Estado machine-local correctamente firewalled del git (receipts/.venv-mcp/egg-info ignorados).
- El problema no es la suite sino la reproducibilidad del entorno que la rodea (§1.2).

---

# 6. Documentación / drift

| Hallazgo | Conf | Detalle |
|---|---|---|
| PROJECT-MAP.md desfasado en sus propias cifras fijadas | CONFIRMADO | Dice HANDOFF 2.821 líneas / bloque vivo hasta 1812; realidad: **2.903 líneas, LIVE-STATE 3→1894** (+82/+82). Cabecera "Generated 2026-08-07" citando toques de 2026-08-21/22 (contradicción interna). Además: "8 files, 128 KB" de Enforce cuando la realidad son **9 .c ≈196 KB**, omite `MCPDialogController.c` y cifra ClientBridge como 46 KB (~73 KB real). Su propia línea 21 admite que una cifra fijada miente → regenerar programáticamente midiendo en runtime |
| CLAUDE.md raíz obsoleto | CONFIRMADO | Habla de "construyendo el POC fase 0" y "Fase 4 aún NO" (cerrados hace meses) y "11 tools en 6 dominios" cuando hoy hay 54 nombres registrados |
| product-spec.md:369 cita errónea | CONFIRMADO | Sitúa `MAX_TIMEOUT_S` en `server.py:50`; está en `:52`. Ya figuraba en NEXT-SESSION-PROMPT.txt:50-51 |
| Contrato wait_for desfasado en dos docs | CONFIRMADO | `README.md:110` + `tools/README-mcp.md:124` vs `server.py:1888-1897` (§3.1) |
| NEXT-SESSION-PROMPT.txt hereda el "~1812" obsoleto y apunta a `%TEMP%\glm-watchdog-20260822` efímero | CONFIRMADO | Refrescar en próximo cierre |
| QUICKSTART.md correcto | CONFIRMADO (positivo) | 11/11 rutas y flags existen; ordena bien los pasos; única nota: `.venv-mcp` es machine-local por diseño |
| README ↔ product-spec coherentes | CONFIRMADO (positivo) | A1/A2/B3/exec_enforce/bind loopback cruzan limpios entre ambos |
| Python >=3.10 vs 3.14 | CONFIRMADO (no-bug) | Discrepancia documentada a propósito (README:125-126, QUICKSTART:10-12, `install-mcp.ps1:40` exige `-3.14`); pyproject declara deps pineadas exactas: mcp==1.27.2, Pillow==12.2.0, psutil==7.2.2 |
| Realidad del lado Enforce | CONFIRMADO | `addon/` trackeado en git (13 ficheros, skip-worktree) pero NO materializado en este checkout; copia viva en el hermano `DayZ_MCP\` con tamaños idénticos a HEAD **salvo `MCPDialogController.c` +978 B por delante de git** (comparado por tamaño, sin hash) → riesgo de trabajo OneDrive-only no commiteado. Un full clone sí puede construir el PBO (`pack-addon.ps1` trackeado) |
| AGENTS.md vs CLAUDE.md | CONFIRMADO | Sin contradicciones; AGENTS.md es duplicado exacto del bloque final de CLAUDE.md (redundancia intencional para agentes no-Claude) |

Conteo de tools: README anuncia **53**; hay **54 sitios `@app.tool` siempre registrados** (53 tools + alias `lease_acquire`, `server.py:2436`) + `exec_enforce` condicional (`server.py:3379-3385`) → 55 con allowlist activa. Ajustar el número o el texto.

---

# 7. Plan de acción priorizado

**P0 — Secretos (hoy):**
1. Rotar `tools\.dayz_mcp.key` y `tools\_broker\.e2e.key`; borrar `_broker\`.
2. Reubicar keyfile fuera del árbol con DACL protegida (patrón `host_config.py:808-831`); DACL en `install_mcp.py:918`.
3. Dejar de escribir keys en configs de misión/perfiles (`install_mcp.py:976-1003`, `install-mcp.ps1:342-361`) — referencia al keyfile, no la key.
4. Key por header en el tramo Python↔daemon (`accredited_daemon_transport.py:281`); DPAPI para la copia del puente (`loopback.py:783`).

**P1 — Reproducibilidad (esta semana):**
5. Decidir destino de la capa doc interna (track / rama privada / vault respaldado) — es el único punto único de fallo real del repo.
6. Commit de los módulos sin trackear que la suite necesita; materializar `addon/` o formalizar contrato del árbol hermano; commitear el drift de `MCPDialogController.c`.

**P2 — Bugs concretos (diffs pequeños, alto valor):**
7. Alinear contrato `wait_for` (doc o código, decidir cuál) — `server.py:1888` vs `README.md:110`.
8. Deadline+backoff en cancel-loop (`control_client.py:534`); presupuesto de 50 ms de release-audit (`session_coordination.py:43`); timeout al watcher (`:2343`); `asyncio.to_thread` en enqueue embebido (`server.py:604,658`); política de paths a `telemetry_read fixture_jsonl` (`server.py:2896`); audit appends O(1) (`runtime_state.py:339`).

**P3 — Simplificación estructural (programa con objetivo medible, −12–14k LOC):**
9. Coordinación: eliminar WAL/tombstones/CAS que protegen estado descartado al reiniciar (mantener atomic-write/pinned-read/audit); fusionar trío policy; borrar stack POC legacy (`loopback.main`); deduplicar gemelos Identity. Objetivo: 13.458 → ~5.150.
10. Launcher: sustituir maquinaria de allowlist por fichero `{path,sha256}`; retirar receipts/migración/acreditación-descartada/triple-build; verificación del bundle solo contra bytes sellados. Objetivo: −4.300–5.000 LOC.
11. MCP surface: helper `_bridge_call`, normalizar `ok`/estilo de error, borrar muertos (~50 LOC), split de `build_app()`.
12. Mover `security_runtime_audit.py` a `tools\checks\`; borrar `lifecycle_cli.py`; plegar `launcher_registry_update.py`.

**P4 — Higiene:**
13. Borrar/archivar: `_fase*`, `_poc`, `_s0/_step0/_compile/_restore`, `_gamemaster_h0_*` (~11,9 GB fuera de OneDrive), bundle rolled-back de 40 MB, todos los `.bak*` (incluidos los del interior del paquete).
14. `.gitignore`: añadir `_*` de raíz y `*.bak*`/`*_bak_*`.
15. Regenerar PROJECT-MAP midiendo en runtime; refrescar CLAUDE.md raíz; corregir product-spec.md:369; consolidar reviews/(306)+reports/rollback en una capa de archivo.

---

*Informe generado por auditoría read-only multi-agente + verificación directa de citas. Ningún fichero del repositorio fue modificado.*
