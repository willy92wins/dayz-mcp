## MATRIX

Rutas relativas al checkout. `d/` = `tools/dayz_mcp/`; `D` = `%LOCALAPPDATA%/DayZ_MCP`; `N` = `%LOCALAPPDATA%/DayZ_MCP_<token>`; `T` = tools tree propio; `S` = shared root; `M` = `migration/P0S-IDENTITY-V2`; `J` = `D/host-config-transaction`; `F` = `lifecycle-recovery-faults`. `→` conserva el sufijo sustituyendo D por N. «Conservar» significa artefacto durable; los locks OS se liberan sin borrar su archivo.

**1. Nombres y consumidores**

| Helper; path:line | Default | Named | Escribe | Lee | Retira/reproduce |
|---|---|---|---|---|---|
| `d/server_cli.py:52 state_root_name` | DayZ_MCP | DayZ_MCP_token | — | Helpers | — |
| `d/server_cli.py:58 registration_name` | dayz-mcp | dayz-mcp-token | Instaladores | Provenance/doctor | Provider.remove |
| `d/server_cli.py:75 shared_root` | S | S | Locks | Locks | Conservar; M1 |
| `d/server_cli.py:106 shared_build_lock_paths` | S/build-locks/hash.lock | Igual | shared_build_lock | Worker | OS |
| `d/box_admission.py:24 _lock_path` | S/box-admission.lock | Igual | _acquire_os | Lifecycle | _release_os |
| `d/instance_context.py:192 try_acquire` | D/.daemon-startup.lock, byte 1 | → | Lease | Lease | release |
| `d/identity_migration.py:347 daemon_startup_election` | D/.daemon-startup.lock, byte 0 | → | Election | Election | finally |
| `d/identity_migration.py:115 default_migration_dir` | D/M | → | Migration | Migration | Conservar |
| `d/identity_migration.py:1530 ensure_runs_v1_backup` | D/M/{runs.pre-v2.json,runs-backup-receipt.json,.runs-v1.lock,runs-backup-transaction.json,runs-backup-transaction.next,runs-backup-receipt.pending} | → | Migration | Receipt/recovery | _recover_backup_transaction |
| `d/runtime_state.py:203 from_env` | D/{audit,coordination.json,runs.json} | D; factory explícitamente default | Stores | Stores/doctor | Recovery |
| `d/runtime_state.py:212 for_token` | D/{audit,coordination.json,runs.json} | → | Stores | Stores/doctor | Recovery |
| `d/runtime_state.py:230 coordination_fault_path` | D/coordination-fault.json | → | FaultStore | Recovery | clear |
| `d/runtime_state.py:234 lifecycle_recovery_faults_dir` | D/F | → | RecoveryStore | Recovery | Retención |
| `d/runtime_state.py:238 lifecycle_recovery_active_path` | D/lifecycle-recovery-active.json | → | RecoveryStore | load_active | Transiciones |
| `d/runtime_state.py:242 lifecycle_manifest_checkpoint_path` | D/lifecycle-manifest-checkpoint.json | → | checkpoint_manifest | Recovery | Reemplazo |
| `d/runtime_state.py:272 JsonlAuditWriter.__init__` | D/audit/events.jsonl | → | Audit | Doctor/write_once | Rotación |
| `d/runtime_state.py:378 _rotate_locked` | events.jsonl.1… | → | Audit | write_once | oldest.unlink |
| `d/runtime_state.py:393 _coordination_fault_transaction` | D/.coordination-fault.json.lock | → | FaultStore | FaultStore | OS |
| `d/runtime_state.py:805 arm` | D/F/id/{fault.json,events/00000000.json} | → | RecoveryStore | load_active | Conservar |
| `d/runtime_state.py:877 transition` | D/F/id/events/sequence.json | → | RecoveryStore | load_active | Conservar |
| `d/runtime_state.py:947 create_receipt` | D/F/id/receipts/hash.json | → | RecoveryStore | _receipt_exists | Conservar |
| `d/runtime_state.py:966 create_manifest_backup` | D/F/backups/hash/{manifest.bin,receipt.json} | → | RecoveryStore | Recovery | Prune/quarantine |
| `d/runtime_state.py:1415 _quarantine_incomplete_manifest_backup` | D/F/backups/quarantine-* | → | RecoveryStore | Retención | Conservar |
| `d/runtime_state.py:1995 _quarantine_coordination_snapshot` | D/coordination.json.corrupt.hash[.n] | → | Startup recovery | Evidencia | Conservar |
| `d/runtime_state.py:2285 _atomic_write_text` | target.tmp.random | Igual junto al target | Stores | Replace | finally; crash sin sweep |
| `d/process_lifecycle.py:1693 _create_preprune_backup` | D/runs.json.bak-preprune[.2…10] | → | Manifest | Doctor | Conservar/fallo unlink |
| `d/doctor.py:961 _preprune_backup_slots_exhausted` | Mismos backups | → | — | Doctor | — |
| `d/process_lifecycle.py:4120 _launch_intent_path` | D/launch-intent.json | → | Lifecycle | Recovery | _clear_launch_intent |
| `d/process_lifecycle.py:6184 _adoption_marker_path` | D/adoption-unconfirmed.json | → | Lifecycle | Recovery | _delete_adoption_marker |
| `d/process_lifecycle.py:6191 _adoption_settled_path` | D/adoption-unconfirmed-settled.json | → | Lifecycle | Recovery | Conservar |
| `d/daemon.py:343 _audit_path` | T/_audit/exec_enforce.jsonl | exec_enforce-token.jsonl | Daemon | Auditor | Conservar |
| `d/server.py:856 exec_audit_path` | T/_audit/exec_enforce.jsonl | Igual | Embedded | Auditor | Conservar; M8 |
| `d/daemon.py:1402 record_daemon_event` | No deriva: NameError | Igual | Fallback fallido | — | M7 |
| `d/inbox.py:26 inbox_directory` | D/inbox | → | Feedback | Inbox | Conservar |
| `d/inbox.py:39 feedback_path` | D/inbox/feedback.jsonl | → | Append | Inbox | Conservar |
| `d/inbox.py:137 _lock_path` | feedback.jsonl.lock | → | Append | Append | OS |
| `d/knowledge.py:450 _default_knowledge_json` | D/knowledge.json | → | Prepare | Knowledge | Reemplazo |
| `d/knowledge.py:595 _publish_index` | knowledge.json.{prepare.lock,candidate.uuid} | → | Prepare | Prepare | OS/replace/finally |
| `d/knowledge_pack.py:45 resolve_pack_dir` | D/knowledge-pack | → | Git | Knowledge | Conservar |
| `d/knowledge_pack.py:62 default_skills_dir` | ~/.agents/skills | Igual; named no propietario | Sync | Agentes | unsync |
| `d/knowledge_pack.py:66 default_manifest_path` | D/knowledge-pack-skills-manifest.json | → | Sync | unsync | unsync |
| `d/knowledge_pack.py:156 _atomic_write_manifest` | manifest.tmp | → | Sync | Replace | replace/except |
| `tools/mcp_capture.py:560 frame_state_path` | D/capture-frame-state.json | → | Capture | Capture | Reemplazo; M5 |
| `tools/mcp_capture.py:584 _FrameStateLock.__init__` | sidecar.lock | → | Capture | Capture | exit/stale |
| `tools/mcp_capture.py:678 _write_frame_state` | .capture-frame-state-*.tmp | → | Capture | Replace | except/sweep |
| `tools/mcp_capture.py:1466 resolve_capture_dir` | TEMP/dayz_mcp_captures | TEMP/dayz_mcp_captures_token | Capture | Consumidor | Conservar |
| `tools/mcp_capture.py:1478 write_fullres` | capture_timestamp_ms.jpg | Directorio named | Capture | Consumidor | Conservar |
| `d/host_config.py:911 _default_journal_root` | J | J; compartido intencional | Timeouts | Recovery | _cleanup_journal |
| `d/host_config.py:959 _manifest_path` | J/manifest.json | Igual | Timeouts | Recovery | Cleanup |
| `d/host_config.py:963 _persist_manifest` | J/manifest.next | Igual | Timeouts | Replace | Replace/cleanup; M10 |
| `tools/install_mcp.py:148 installer_security_dir` | D/security | D/security; autoridad compartida | Pin/probe | Instaladores | Conservar |
| `tools/install_mcp.py:161 installer_cli_manifest_path` | security/installer-cli-manifest-v1.json | Igual | Pin | Instaladores | Reemplazo |
| `tools/install_mcp.py:165 installer_not_found_fixtures_path` | security/installer-not-found-fixtures-v1.json | Igual | Probe | Provider | Retire |
| `tools/install_mcp.py:378 _atomic_write_json` | target.tmp | Igual | Pin/probe | Replace | replace/except |
| `tools/install_mcp.py:394 _retire_installer_not_found_fixture` | fixture.stale | Igual | Pin | — | Conservar |
| `tools/install_mcp.py:821 parse_args` | T/.dayz_mcp.key | T propio/.dayz_mcp.key | Installer | Cliente/daemon | Conservar |
| `tools/install_mcp.py:1039 install_runtime` | T/{.venv-mcp,_mcp_config}; profiles explícitos | Mismos relativos, T propio | Installer | Runtime/juego | Conservar |
| `tools/install-mcp.ps1:105 roots` | T/{.venv-mcp,.dayz_mcp.key,_mcp_config} | Mismos relativos, T propio | Installer | Runtime/juego | Conservar |
| `tools/build_native_launcher.py:243 _cache_path` | D/dependency-cache/CPython | Igual; caché compartida | Builder | Builder | Conservar |
| `tools/build_native_launcher.py:250 _acquire_cpython` | cache.{download.lock,partial.PID} | Igual | Builder | Builder | OS/replace; M9 |
| `tools/build_native_launcher.py:489 resolve_launcher_policy_path` | D/launcher-policy.json | Igual salvo policy explícita | Operador | Builder | Conservar |

**2. Simetría de limpieza**

| Creación | Éxito | Fallo | Tras muerte del proceso |
|---|---|---|---|
| Lease: `instance_context.py:211` | daemon finally:1557 | activation except:413 | OS; activación programática: M11 |
| Caja: `box_admission.py:48` | _release_os:65 | Cierra descriptor | OS |
| Build locks: `server_cli.py:153` | finally:172 | Cierra adquiridos | OS |
| Migration directorio/lock: `identity_migration.py:1568` | Conservar/liberar | Liberar | OS |
| Backup:1651 | Durable | Recovery:1514 | Recovery |
| Receipt.pending:1683 | Rename:1692 | Recovery:1496 | Recovery |
| Transaction.next:1245 | Rename:1258 | Recovery:1446/1504 | Recovery |
| Transaction.json:1640 | Unlink:1700 | Recovery:1521 | Recovery |
| Receipt final:1692 | Durable validado | Revalidar | _settled_receipt:1353 |
| Registration: `install_mcp.py:1197`; PS:878/894 | Verify | Python rollback:1210; PS ninguno | Ningún journal; M6 |
| Host journal: `host_config.py:1259` | Cleanup:1280 | Restore:1287 | Recovery:1141; M10 |
| Runtime/knowledge temporales | Replace | finally | Sin sweep encontrado |
| Capture sidecar/lock/tmp | Replace/unlink | except/unlink | stale:601; sweep:628 |
| Pack/skills/manifest | Durable; unsync | Manifest tmp.unlink | Sin transacción Git/skills |
| CPython partial:274 | Replace:291 | Cleanup incompleto | M9 |
| Key/config/venv/capturas/audits | Durables | Parciales posibles | Sin rollback global |

**3. Entradas**

`V` = validación común previa; `E` = rechazo de conflictos de entorno; `L` = lease lifetime; `B` = admisión física. `—` = no aplica; «proxy» aplica en daemon.

| Entrada; ancla | V/E | Fallback | L | B |
|---|---|---|---|---|
| Server CLI `server.py:7314` | Sí/No | Default | Según modo | Proxy |
| Daemon `daemon.py:1532` | CLI/Sí | Default | Sí | Lifecycle |
| Client spawn `server.py:1640` | CLI/No | Default | Proxy | Proxy |
| Installer Python `install_mcp.py:821` | Sí/Sí | Default | No | — |
| Installer PS `install-mcp.ps1:52` | Propia/Sí | Default | No | — |
| p0s backup `p0s_gate.py:327` | Sí/Sí | Default | Gate migration | — |
| Secure launcher `secure_launcher.py:278` | Sí/Sí, sin port | Default | Proxy | Proxy |
| Doctor `doctor.py:1426` | Sin selector | Default | Lectura | — |
| Host config `host_config.py:239` | Selector persistido | dayz-mcp | Locks hosts | — |
| stdio bridge `stdio_bridge.py:432` | No/No | Default | Proxy | Proxy |
| Capture `mcp_capture.py:550` | Error→default/No | Default | Lock sidecar | — |
| Knowledge-pack `knowledge_pack.py:319` | Propia/No | Default | No | — |
| Inbox `inbox.py:26` | Lazy/No | Default | Lock append | — |
| Runtime activation `daemon.py:393` | Token/No | Default | Sí | Lifecycle |

**4. Variables y flags**

| Variable/flag | Setter → lector | Hijos/conflicto |
|---|---|---|
| DAYZ_MCP_INSTANCE, PORT, GAME_PATH | Operador → server_cli:250, instaladores, gate, launcher, daemon | Popen hereda; igualdad exacta; no fallback; M3 |
| DAYZ_MCP_SHARED_ROOT | Operador/tests → shared_root:83 | Popen hereda; worker recibe shared_lock_root sellado; relativo admitido |
| LOCALAPPDATA; TEMP/TMP | Windows → roots/tempfile | Heredados; fallbacks distintos preservados |
| DAYZ_GAME_PATH | Operador → daemon:425 | `--game-path` gana |
| FRAME_STATE_PATH, CAPTURE_DIR | Operador → capture:567/1470 | Overrides ganan al namespace |
| KNOWLEDGE_JSON, PACK_DIR | Operador → knowledge:451, pack:46 | Overrides ganan |
| SECURITY_DIR, LAUNCHER_POLICY | Operador → installer:149, builder:494 | Policy CLI gana; autoridad compartida |
| FAST_TESTS | Runner → server_cli:66 | S temporal por PID |
| TOOL_PACK, TASK_LABEL, SESSION_HANDOFF | Host/supervisor → server:7336/1083/1029 | Heredados; flags explícitos prevalecen |
| DAEMON_SPAWN_WMI, SPAWN_MARKER | Operador/spawner → daemon:1911/1135 | Popen copia; WMI no recibe ese bloque |
| NORMAL_POLICY_JSON; BOOTSTRAP_* | Launcher → normal_policy:83, daemon_policy:382 | Consumo/pop; acreditación |
| instance/game-path en registros; instance_token/shared_lock_root en request | Installer/daemon → provenance/request/worker | Comparación persistida; no depende del entorno worker |
| Contract marker; skills owner; receipts/journal status | Código/transacciones → scanner/sync/recovery | Marcador positivo; owner default; schemas cerrados |

## FINDINGS

- **M1 — P1.** `tools/dayz_mcp/server_cli.py:88`: `root = Path(override)`. `box_admission.py:28`: `root.mkdir(parents=True, exist_ok=True)`. Un override relativo deriva locks diferentes en los cwd de árboles independientes. Reproducido en memoria.

- **M2 — P2.** `tools/dayz_mcp/knowledge_pack.py:324`: `args = _build_parser().parse_args(argv)`; :339: `bind_instance_context(owner, replace=True)`. Acepta duplicados, `--instance=130` y `--inst`; reproducido. `stdio_bridge.py:432` tampoco aplica el validador común antes del probe.

- **M3 — P2.** `tools/dayz_mcp/server.py:7328`: `bind_instance_context(instance_token, game_path, replace=True)`. CLI/client/embedded no llaman al rechazo de entorno; el daemon lo hace en :1545. Knowledge-pack y stdio tampoco. La misma configuración conflictiva tiene resultados diferentes según entrada.

- **M4 — P2.** `tools/dayz_mcp/doctor.py:1437`: `args = parser.parse_args(argv)`. Su parser no declara `--instance` ni `--game-path`; doctor named no es seleccionable por CLI.

- **M5 — P2.** `tools/mcp_capture.py:556`: `except Exception:`; :557: `return None`. Un error de selección se convierte en default; :572 deriva `"DayZ_MCP"`, permitiendo escribir el sidecar default.

- **M6 — P2.** `tools/install_mcp.py:1197`: `provider.add(role, desired[role])`; :1204: `apply_host_timeouts(...)`. Registro sin journal durable; recuperación host ocurre después de mutaciones. PS :894 añade Codex y :897 solo hace `throw`: fallo deja Claude cambiado.

- **M7 — P2.** `tools/dayz_mcp/daemon.py:1431`: `RuntimePaths.for_token(getattr(config, "instance_token", None))`. `config` no existe en ese scope. Fallback sin writer devuelve False tras NameError; reproducido.

- **M8 — P3.** `tools/dayz_mcp/daemon.py:351`: `f"exec_enforce-{token}.jsonl"` frente a `server.py:859`: `"exec_enforce.jsonl"`. Dos helpers nombran distinto la misma auditoría named según modo.

- **M9 — P3.** `tools/build_native_launcher.py:274`: `f".partial.{os.getpid()}"`; :293: `finally:` libera únicamente el lock. Error de red/escritura o muerte deja partial; el siguiente PID solo retira su propio nombre.

- **M10 — P2.** `tools/dayz_mcp/host_config.py:966`: `_write_private(temporary, payload)`; :996: `_manifest_path(journal).read_bytes()`. Muerte durante primera publicación deja J sin manifest final; recovery rechaza `registration_journal_invalid`, sin replay de manifest.next.

- **M11 — P2.** `tools/dayz_mcp/daemon.py:401`: `state.root_writer_lease = lease`. Activación programática libera en excepción (:413), pero no hay teardown exitoso conectado; los descriptores permanecen en `_lease_fds` hasta liberación explícita o salida del proceso.

## NOT VERIFIED

- Instalación, registros reales, daemon, MCP, DayZ, concurrencia OS, WMI y cortes reales de proceso.
- Pruebas ejecutadas: exclusivamente funciones/parser aislados en memoria con Python `-B`.
- No escrituras, commit ni artefacto adicional.
- Internos completos de supervisor/session-handoff y publicación nativa fuera de los helpers enumerados.

