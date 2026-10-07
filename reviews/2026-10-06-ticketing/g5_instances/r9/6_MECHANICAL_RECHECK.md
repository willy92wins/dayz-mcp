## MATRIX

**Resultado: quedan brechas verificadas.** Censo estático del diff y sus consumidores. En las tablas, `D/` significa `tools/dayz_mcp/`. `R/W` significa `FILE_SHARE_READ | FILE_SHARE_WRITE`; `CRT?` indica que el código delega el modo de compartición al runtime.

**1. Aperturas y propietarios de locks**

Para locks de Windows basados en handles, la muerte del proceso libera el handle; esto no elimina necesariamente el archivo.

| Apertura `path:line` | Compartición; adquisición/espera | Propietario; liberación éxito/error |
|---|---|---|
| `D/server_cli.py:133` | R/W, sin DELETE; helper | Descriptor devuelto; conversión fallida sin cierre explícito |
| `D/server_cli.py:274`, `:322` | Helper anterior; `LK_NBLCK` en `:216`; deadline configurable, 900 s por defecto | Lista de descriptores; `finally` libera en reversa; error cierra el descriptor pendiente |
| `D/box_admission.py:46` | Helper; `LK_NBLCK`, intento inmediato `:52` | Descriptor global y contador; contexto libera; error cierra |
| `D/instance_context.py:154`, `:202` | Helper; `LK_NBLCK`, byte 1 `:209` | Token explícito conservado, o objeto `owner`; `release()`/error cierran |
| `D/instance_context.py:151` | `os.open`, rama sin msvcrt | Fallback del helper; `try_acquire()` rechaza esa plataforma antes de abrir |
| `D/identity_migration.py:370` | Helper; elección byte 0, `LK_NBLCK :385`, inmediata | Descriptor local; `finally` desbloquea/cierra |
| `D/identity_migration.py:420` | Helper; migración, `LK_NBLCK :434`, inmediata | Descriptor local; error cierra; `finally` desbloquea/cierra |
| `tools/install_mcp.py:1231`, `:1256` | R/W sin DELETE; `LK_NBLCK :1259`, polling, 120 s por defecto | `_RegistrationLockToken`; `finally :1489` libera; conversión fallida sin cierre |
| `D/runtime_state.py:410` | CRT?; **`LK_LOCK :421`**, sin deadline propio | Descriptor local; error cierra; `finally :436` desbloquea/cierra |
| `tools/build_native_launcher.py:264` | CRT?; **`LK_LOCK :271`**, sin deadline propio | Descriptor local; `finally :293` desbloquea/cierra |
| `D/knowledge.py:521` | CRT?; `LK_NBLCK :530`, inmediato | Descriptor local; error/finally cierran |
| `D/inbox.py:181` | CRT?; `LK_NBLCK :150` o flock; polling 10 s | Descriptor local; `finally :200` cierra |
| `D/registry_lock.py:255` | `Path.open`, CRT?; `LockFileEx :272`, inmediato | `RegistryLock`; contexto/errores cierran |
| `D/registry_lock.py:160`, `:219` | Directorio/CreateFileW y creación/NtCreateFile: R/W sin DELETE; sin espera | Handles locales; `finally :187` y cierre `:236` |
| `tools/mcp_capture.py:636` | CRT?; exclusión por `O_EXCL`; polling 0,5 s | Descriptor; salida cierra/elimina; tras muerte, siguiente intento elimina por antigüedad >30 s |
| `D/host_config.py:641`, `:784` | Exclusión por apertura: share=0 y READ respectivamente; inmediatas | Objetos `_WinFile`/`_PinnedConfigFile`; error/contextos cierran |
| `tools/dev/swap_pbo.ps1:111` | PBO vivo y temporal share=0 (`:273`, `:309`); backup READ (`:291`) | Streams locales; `finally :366` dispone |

No encontré propietarios de estos locks identificados mediante `id()`, ni aperturas explícitas anteriores que incluyan `FILE_SHARE_DELETE`.

**2. Archivos/directorios creados y recuperación**

| Artefacto | Creador | Éxito | Fallo / muerte del proceso |
|---|---|---|---|
| `registration-transaction/<identidad>/` y `manifest.json` | `tools/install_mcp.py:1505`, `:1304` | `rmtree :1590` | Rollback y limpieza `:1595`; rollback fallido conserva; próxima ejecución de **esa identidad** recupera `:1507–1523` |
| `manifest.staging`, `manifest.next` del registro | `tools/install_mcp.py:1307` | Renombrados hasta manifest.json `:1308–1309`; limpieza final | Fragmentos no JSON se eliminan `:1317`; next completo se recupera/descarta `:1443`; staging se sobrescribe |
| `registration-transaction.lock` y directorio padre | `tools/install_mcp.py:1250`, `:1231` | Handle liberado; archivo permanece | Handle cerrado; archivo permanece y se reutiliza |
| Journal host-config, `manifest.json`, `manifest.next` | `D/host_config.py:1292`, `:965` | `rmtree :1313` | Rollback `:1318`; próxima llamada recupera `:1260`; next completo se acepta; next truncado queda rechazado |
| Root compartido y `box-admission.lock` | `D/box_admission.py:28`, `:46` | Permanecen | Permanecen; handle liberado |
| `build-locks/` y hashes `.lock` | `D/server_cli.py:269`, `:274`, `:318`, `:322` | Permanecen | Permanecen; handles liberados; sin replay necesario |
| `.daemon-startup.lock` | `D/instance_context.py:195`, `:202`; `identity_migration.py:359`, `:370` | Permanece | Permanece; bytes liberados al cerrar/morir |
| Lock de migración | `D/identity_migration.py:1584`, `:420` | Permanece | Se reutiliza; los cambios no crean nuevos artefactos de datos de migración |

**3. Entry points**

| Entrada | Validación compartida antes de efectos | Conflicto de entorno |
|---|---|---|
| Server CLI | Sí, componentes compartidos; `D/server.py:7319–7333` | Sí |
| Daemon | Binding valida antes de lease/stores; `D/daemon.py:1555` | Sí `:1556`; antes lee keyfile `:1550` |
| Client spawn | Vía CLI sí; `_default_spawn :1640`/`spawn_detached :1903` no revalidan | Vía CLI sí; spawn directo no |
| Embedded | Sí, `D/server.py:7431`, antes de `build_app/start_loopback` | Sí vía parse_args |
| Installer Python | Sí, `tools/install_mcp.py:841` | Sí `:859`, `:1687` |
| Installer PowerShell | Implementación equivalente propia `tools/install-mcp.ps1:103–134` | Sí `:135–148` |
| p0s_gate | Sí `tools/p0s_gate.py:309`, `:339` | Sí para backup `:347`; ramas freeze/probe no |
| secure_launcher | CLI sí `D/secure_launcher.py:288`; función directa valida después de abrir launcher/bundle `:185–205` | CLI sí `:289`; función directa no |
| doctor | Sí `D/doctor.py:1453` | **No**, sigue a binding `:1463` |
| host_config | Registro almacenado usa parser compartido `D/host_config.py:280`, después de abrir configs `:396` | **No**; depende del llamador |
| stdio_bridge | Sí `D/stdio_bridge.py:441`, `:454` | **No** |
| capture | Validación indirecta `tools/mcp_capture.py:592`, antes de captura `:1509` | **No** |
| knowledge pack | CLI sí `D/knowledge_pack.py:335`; función directa prepara pack antes de resolver owner `:282–284` | **No** |

**4. Vocabulario de errores**

| Código; origen | Traducción/consumidor |
|---|---|
| `registration_busy`; `tools/install_mcp.py:1263` | Conservado por main `:1787–1791` |
| `build_busy`; `D/server_cli.py:81`, `:231`, `:334` | Worker → error tipado `D/dayz_test_worker.py:856`; app_main conserva `tools/native-launchers/dayz-test-v1/src/app_main.py:348` |
| `relative_shared_root`; `D/server_cli.py:164` | `D/dayz_test_tool.py:460` → MCP genérico `dayz_test_failed:OSError` |
| `invalid_build_lock_wait`; `D/server_cli.py:106`, `:108`, `:199`, `:202` | Mismo borrado de código desde `D/dayz_test_tool.py:464` |
| `build_lock_wait_invalid`; `D/dayz_test_request.py:400` | → `bad_dayz_test_request:build_lock_wait_invalid`, `D/dayz_test_tool.py:498–499` |
| `lock_file_open_failed`; `D/server_cli.py:144` | Root lease → `root_writer_lock_open_failed :156`; migración → errores de lock `:399`, `:449`; worker no lo tipa → `internal_failure` en app_main `:359` |
| `registration_lock_open_failed`; `tools/install_mcp.py:1242` | OSError con errno → `installer_failed :1790` |
| `registration_journal_unavailable`, `invalid_registration_name`, `invalid_registration_lock_timeout`, `registration_journal_invalid`, `registration_rollback_verify_failed`, `registration_journal_owner_mismatch`; `tools/install_mcp.py:1160`, `:1170`, `:1248`, `:1287`, `:1414`, `:1515` | Main conserva; dentro del bloque transaccional puede envolver como `registration_transaction_failed :1599` |
| `invalid_instance_token`, `invalid_game_path`, `glued_instance_flag`, `duplicate_instance_flag`, `instance_environment_conflict`; `D/server_cli.py:39`, `:50`, `:603`, `:605`, `:434` | CLI/doctor/bridge/capture/pack conservan; secure launcher → “invalid arguments”; daemon → startup contended; host_config → provenance conflict |

## FINDINGS

**N1 — P2. Persisten locks bloqueantes.**  
Literal `msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)` en `tools/dayz_mcp/runtime_state.py:421` y `tools/build_native_launcher.py:271`. Ninguno tiene polling/deadline propio. El censo solicitado sigue teniendo dos excepciones al patrón acotado.

**N2 — P1. PowerShell queda fuera de la transacción durable.**  
Literal `& claude mcp add dayz-mcp -s user -- $VenvPython @claudeArgs`, `tools/install-mcp.ps1:925`, seguido de alta Codex `:941`. No adquiere el lock ni publica/reproduce journal. Una muerte entre altas deja registro parcial; además puede intercalar mutaciones con Python. El rollback `:946` solo cubre un error devuelto por Codex.

**N3 — P2. Rechazo de entorno incompleto.**  
Literales `bind_instance_context(token, game_path, replace=True)` en `tools/dayz_mcp/doctor.py:1463`, `command = official_client_argv(` en `stdio_bridge.py:468`, `return validate_instance_token(current_instance_token())` en `tools/mcp_capture.py:592`, y `token, _game_path = validate_entry_selector(raw)` en `knowledge_pack.py:335`. Sus recorridos no llaman `reject_conflicting_environment`; aceptan discrepancias que el daemon rechaza. Las funciones directas de spawn/launcher y host_config tampoco incorporan esa puerta.

**N4 — P2. Se pierden errores nombrados.**  
Literal `return f"dayz_test_failed:{name}"`, `tools/dayz_mcp/bridge_errors.py:229`, elimina `relative_shared_root`/`invalid_build_lock_wait` recibidos desde `server.py:3833`. Literal `else "installer_failed"` en `tools/install_mcp.py:1790` elimina `registration_lock_open_failed`. El launcher usa literalmente `"secure launcher: invalid arguments"` en `secure_launcher.py:291` para cualquier error de selección.

**N5 — P2. Publicación truncada de host-config queda bloqueada.**  
Literal `_write_private(temporary, payload)`, `tools/dayz_mcp/host_config.py:966`, escribe directamente manifest.next. Una muerte durante esa escritura deja JSON incompleto; `raise HostConfigError("registration_journal_invalid")`, `:1017`, aborta antes de cualquier limpieza. A diferencia del journal de registro, no existe staging descartable.

**N6 — P2. Timeout de registro admite NaN/infinito.**  
Literal `timeout_s < 0`, `tools/install_mcp.py:1247`, es la única restricción numérica. Con NaN, `time.monotonic() >= deadline` (`:1262`) nunca cumple; con infinito tampoco expira. El polling pierde su límite.

**N7 — P3. Fallo de conversión deja handle abierto.**  
Literales `return msvcrt.open_osfhandle(handle, os.O_BINARY)`, `tools/dayz_mcp/server_cli.py:145`, y su equivalente en `tools/install_mcp.py:1243`. Ninguno cierra el handle nativo si la conversión lanza; queda hasta la muerte del proceso.

## NOT VERIFIED

No ejecuté tests, instaladores, daemon, DayZ ni herramientas MCP; no escribí archivos. No medí exclusión, muerte real ni recuperación tras kill. El modo efectivo de compartición de `os.open`/`Path.open` queda **no verificado**: sus callers no expresan los flags Win32. No atribuyo `FILE_SHARE_DELETE` basándome únicamente en el comentario del helper.

