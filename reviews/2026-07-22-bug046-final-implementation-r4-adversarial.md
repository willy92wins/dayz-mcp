# BUG-046 — revisión adversarial final de implementación R4

## Veredicto

**BLOCKED.** La coordinación FIFO y el arranque del daemon han mejorado materialmente y las baterías focales pasan, pero el objetivo operativo que originó esta tarea aún no está cerrado. Hay tres bloqueantes HIGH reproducibles:

1. el launcher que el runbook y la skill ordenan usar está desactivado por diseño, no acepta los argumentos documentados y no tiene ningún consumidor registrado;
2. `session_acquire_wait` acepta esperas de hasta 3.600 s aunque ambos hosts están configurados para cortar a 1.860 s, por lo que la API admite exactamente el tipo de ejecución que vuelve a pararse esperando lease;
3. la transacción de timeouts no reconoce una escritura propia realmente parcial y deja la configuración en conflicto permanente tras un crash dentro de `WriteFile`.

Además, el snapshot cambió dos veces durante esta revisión. Conforme al gate de estabilidad pedido por el agente principal, un estado que continúa recibiendo cambios de producción/tests no puede recibir luz verde como “final”. No se modificó producción, tests ni documentación operativa; el único archivo creado es este informe.

## Snapshot y drift observado

El encargo comenzó sobre un snapshot que declaraba, entre otros:

| Archivo | Snapshot inicial comunicado |
|---|---|
| `tools/dayz_mcp/host_config.py` | 574 líneas, `A3C2F5DD…` |
| `tools/tests/test_mcp_host_timeouts.py` | 216 líneas, `EE6BE6BE…` |
| `tools/dayz_mcp/admin_cli.py` | 188 líneas, `4611F07C…` |
| `tools/dayz_mcp/doctor.py` | 888 líneas, `4F56D4A9…` |
| `tools/dayz_mcp/lifecycle_cli.py` | estado previo al gate nuevo de procedencia |

Durante la revisión, sin ninguna escritura de este subagente sobre esos ficheros:

- `host_config.py` pasó primero a 730 líneas / `D54B4586…` y después a 732 líneas / `B8FD6E71…` (`LastWriteTimeUtc=2026-07-22T09:30:05.7950890Z`);
- `test_mcp_host_timeouts.py` pasó primero a 329 líneas / `CFA0CD8E…` y después a 352 líneas / `29E7D636…` (`LastWriteTimeUtc=2026-07-22T09:30:05.7960894Z`);
- `admin_cli.py`, `doctor.py`, `lifecycle_cli.py` y `test_admin_cli.py` también cambiaron durante la pasada para añadir acreditación desde las dos configuraciones host.

Último snapshot revalidado antes de redactar el informe:

| Archivo | Líneas | SHA-256 |
|---|---:|---|
| `tools/dayz_mcp/session_coordination.py` | 3.216 | `51DD7DC8CC9BA808ECA779D9DDB8FD4268A1EF1842E47E011C7AABEF4471745F` |
| `tools/dayz_mcp/runtime_state.py` | 1.388 | `7E33A95A99A3B84D32D95A2E392C2B6FF8A417BB4EA239BBC403617204ECB6EC` |
| `tools/dayz_mcp/daemon.py` | 804 | `B5FBF3D3098A3427835049B183A798A1500C17D935A8D89046653CFE4AA3D633` |
| `tools/dayz_mcp/server.py` | 1.325 | `CDC511A288705F1214AE03266E9D66B1AFB4B557E77F8A1FFEA3098601B1852D` |
| `tools/dayz_mcp/host_config.py` | 732 | `B8FD6E71FF0808F9F5365308B22BE6109F840957EF782B4B0762377936974614` |
| `tools/dayz_mcp/secure_launcher.py` | 415 | `0EB1B31BB92F0CE33C1CEF2CA2DC7DCC304B76C0B7E5261F368A8297386458F4` |
| `tools/dayz_mcp/admin_cli.py` | 209 | `E26B132BC377C95CB218A94FF7B9EF8EC41C3B5CEE0D7A532492C0EA52BEFD55` |
| `tools/dayz_mcp/doctor.py` | 898 | `C34795FEA95E669385C1ECDDA3105C7561ABCD1197E17F47354AB9871950163F` |
| `tools/dayz_mcp/lifecycle_cli.py` | 103 | `706E32EB4915F81E8A73FBC792D351DE9788093020AB2EF53B7678E3DF34D51C` |
| `tools/approved-launchers.json` | 4 | `330B04E8D7AB06E7EE850326C1CAE180F119ED21486745DC0EC9BAAE203C653B` |

## Hallazgos

### HIGH-01 — la ruta agent-safe documentada no existe operativamente

**Tipo exacto:** degradación bloqueante de funcionalidad/liveness; no es un crash.

**Evidencia de producción y contrato:**

- `tools/dayz_mcp/secure_launcher.py:258-265` descarta `max_wait_s`, abre la entrada y lanza incondicionalmente `RuntimeError("native_launcher_not_configured")`;
- `tools/dayz_mcp/secure_launcher.py:387-402` acepta únicamente `launcher_id` y `--max-wait-s`; no define el separador `--`, argumentos de consumidor, purpose, acquire, heartbeat, proceso hijo, drains ni cleanup;
- `tools/approved-launchers.json:1-4` contiene `"launchers": []`;
- `tools/tests/test_secure_launcher.py:219-237` exige explícitamente que el launcher termine con `native_launcher_not_configured`;
- `tools/tests/test_task9_launcher_migration.py:261-277` exige registro vacío y que el README no exponga `secure_launcher`;
- `tools/README-mcp.md:68-70` reconoce correctamente que el launcher nativo está pendiente y que el placeholder rechaza todos los consumidores;
- en contradicción, el runbook canónico `C:\Users\guill\ObsidianVault\AI\20_Runbooks\dayz-mcp-agent-session-protocol.md:35-43` ordena ejecutar `...secure_launcher lf_vstorage_dayz_test -- -Mod ...` y afirma que adquiere/renueva/libera, fija el script y redacta ambos streams;
- la skill `C:\Users\guill\.claude\skills\dayz-test-ingame\SKILL.md:463-480` publica la misma ruta como “AGENT-SAFE REGISTRADO” y repite garantías que el código no implementa;
- `..\LF_VStorage_dev\tools\dayz-test.ps1:123-149` confirma la premisa de Claude: el consumidor necesita identidad/token en el entorno de proceso y una shell pelada no puede lanzarlo en modo managed;
- `..\LF_VStorage_dev\tools\dayz-test.ps1:151-179` sí repone las credenciales solo alrededor de `lifecycle_cli` y las restaura en `finally`, pero hoy no existe el padre seguro que deba entregárselas.

**Fixture/exploit reproducido:** se ejecutó literalmente el comando no secreto del runbook. Resultado:

```text
documented_exit=2 id_only_exit=1
secure launcher: invalid arguments
secure launcher failed: ValueError
```

La variante documentada muere en parsing. Incluso quitando los argumentos, el ID no está registrado y termina con error. No llega a adquirir lease ni a crear un hijo, así que la contención fail-closed es real, pero no soluciona la necesidad del usuario.

**Impacto:** Claude tiene razón al negarse a copiar token a una shell/transcript, pero Codex tampoco dispone de la ruta limpia que el protocolo le atribuye. El flujo queda sin camino agent-safe y vuelve a depender de permiso/manualidad o se para. Esto incumple directamente el objetivo explícito de resolver el lanzamiento de `dayz-test.ps1` sin exponer credenciales.

**Fix mínimo requerido:** cerrar primero la contradicción de producto: el H9 vigente (`product-spec.md:135`) exige consumidor nativo/neutral y prohíbe autorizar `.ps1`, mientras el runbook/skill ordenan un launcher PowerShell. Bajo el H9 actual, implementar y revisar el consumidor PE nativo, registrarlo por root+identidad+path+SHA y completar acquire-wait → env privado de hijo → drains/redacción → heartbeat → release/status. Si se decide volver al diseño PowerShell original, requiere aprobación explícita del cambio de H9 antes de implementarlo. Hasta que exista una ruta funcional, runbook y skill deben declarar el bloqueo igual que el README; eso evita instrucciones falsas, pero no cierra el objetivo.

**Gate requerido:** el comando operativo documentado debe pasar con fake runtime queued→active y fake child; argumentos exactos; cero secreto en argv/stdout/stderr/excepciones; secreto solo en env inicial del hijo; drains concurrentes mayores al pipe buffer; heartbeat; terminación acotada solo del wrapper; release/status en todos los caminos. Después, gate vivo autorizado sin copiar credenciales.

### HIGH-02 — la API permite esperar 3.600 s con hosts que cortan a 1.860 s

**Tipo exacto:** degradación de liveness central; no es crash del daemon.

**Evidencia:**

- `tools/dayz_mcp/server.py:892-900` publica `session_acquire_wait` con default 900 s y acepta cualquier valor hasta 3.600 s;
- `tools/dayz_mcp/host_config.py:21-22` fija Claude en 1.860.000 ms y Codex en 1.860 s;
- el plan aprobado fija `max_wait_s=1800.0` en `plans/2026-07-22-bug046-lease-queue-liveness-plan.md:513,520,546`, precisamente para dejar 60 s de margen al host;
- `tools/tests/test_session_acquire_wait.py:257-272` solo comprueba los nombres del schema; no comprueba default, máximo ni relación con los timeouts host;
- el schema MCP real observado fue `{"default": 900.0, "title": "Max Wait S", "type": "number"}`: tampoco publica un máximo que permita al host/modelo evitar el valor incompatible.

**Fixture/exploit:** A mantiene el lease más de 1.860 s y B llama `session_acquire_wait(..., max_wait_s=3600)`. La validación de la tool lo acepta, pero el host corta la request a 1.860 s. Si el host termina el stdio en vez de cancelar limpiamente la coroutine, el `finally` de seis segundos ni siquiera está garantizado; como mínimo la ejecución de B se para y su ticket queda hasta tombstone/TTL. Sin argumento explícito, el default actual ya para la sesión a los 900 s, desaprovechando la mitad del presupuesto configurado.

**Fix mínimo `[EXACT]` conceptualmente:** una sola constante funcional de 1.800 s; usarla como default y máximo de `_require_range`, y conservar 1.860 s/1.860.000 ms en hosts. Añadir tests que exijan default=1800, rechazo de 1800+ε y `host_timeout > functional_max_wait`; ejecutar además el smoke largo real en sesiones nuevas.

### HIGH-03 — recovery confunde una escritura propia parcial con drift externo

**Tipo exacto:** degradación persistente y configuración on-disk parcial; no mata proceso ni sobrescribe drift externo.

**Evidencia:**

- `tools/dayz_mcp/host_config.py:390-407` escribe en chunks, trunca y flush-ea; un proceso puede morir después de cualquier `WriteFile` y antes de completar/truncar;
- `tools/dayz_mcp/host_config.py:589-594` clasifica únicamente igualdad exacta con original o target; todo prefijo propio parcial cae como `external`;
- `tools/dayz_mcp/host_config.py:597-625` aborta recovery con `registration_recovery_conflict` si cualquiera cae como external;
- el contrato aprobado sí define las familias propias `T[:k] + O[k:]` y `T[:k]` en `plans/2026-07-22-bug046-lease-queue-liveness-plan.md:584-599` y exige recuperación reentrante;
- `tools/tests/test_mcp_host_timeouts.py:298-321` llama “torn” a un crash inyectado **después** de que el fichero Claude ya fue escrito por completo. No interrumpe `WriteFile`, no produce bytes parciales y por eso no cubre el contrato.

**Fixture reproducido:** tras crear un journal `writing`, se sustituyó Claude por una secuencia propia admitida por el plan `target[:k] + original[k:]`, dejando Codex original. La siguiente llamada devolvió:

```text
error=registration_recovery_conflict torn_preserved=True peer_original=True
```

Es fail-closed frente a bytes ambiguos, pero estos bytes no son ambiguos: el journal demuestra que pertenecen exactamente a una frontera de escritura propia. El sistema no converge y puede dejar un host con TOML/JSON inválido o con un timeout nuevo mientras el otro conserva el anterior.

**Fix mínimo requerido:** implementar el clasificador `own_torn(O,T,C)` y el clasificador de restauración definidos en el plan. Antes del primer byte de rollback, persistir/fsync-ar el `recovery_source` byte-exacto y el estado `restoring_original`; reanudar desde cero de forma idempotente tras un segundo crash. Todo byte fuera de las familias propias sigue siendo conflicto y conserva ambos destinos. Los tests deben inyectar short-write/progreso parcial dentro de `_WinFile.write`, crash antes/después de truncate/flush y segundo crash durante rollback.

### MEDIUM-04 — el handle del launcher no impide modificación in-place

**Tipo exacto:** gap de seguridad latente; hoy no es explotable para ejecutar un hijo porque HIGH-01 mantiene el launcher desactivado.

**Evidencia:** `tools/dayz_mcp/secure_launcher.py:225-252` abre el consumidor con `Path.open("rb")`. `revalidate()` (`:44-55`) vuelve a comprobar identidad/hash en un instante, pero el handle permanece abierto con una política que en este Windows bloquea rename/delete y **permite escritura concurrente**.

**Fixture reproducido en Windows:** con el handle `rb` abierto, `os.replace` y `unlink` devolvieron `PermissionError`, pero una segunda apertura `r+b` escribió bytes con éxito; el handle fijado leyó después los bytes nuevos:

```text
write=succeeded
handle_bytes=new
```

Por tanto, “mantener el handle abierto” no equivale a fijar el contenido. Una modificación después de `revalidate` y antes/durante create-process rompe path+SHA.

**Fix mínimo requerido:** abrir el PE con `CreateFileW(GENERIC_READ, FILE_SHARE_READ, ...)`, validar `FILE_ID_INFO` y hash desde ese mismo handle, revalidar inmediatamente antes de spawn y conservarlo hasta la terminación del hijo. Tests Windows deben exigir que `r+b`, replace, delete y retarget fallen mientras vive el handle, manteniendo lectura paralela permitida.

### MEDIUM-05 — las pruebas certifican estados contradictorios y no los criterios que llevan sus nombres

**Tipo exacto:** gap de validación/contrato.

- Los tests del launcher pasan porque exigen que esté deshabilitado (`test_secure_launcher.py:219-237`), mientras runbook/skill lo presentan como productivo.
- `test_task9_protocol_docs.py:114-130` solo exige que la skill contenga nombres de env y conceptos; no compara el comando documentado contra el parser/registro real.
- `test_session_acquire_wait.py:257-272` no fija default/máximo.
- `test_mcp_host_timeouts.py:298-321` no crea un torn write real.
- El DPF mantiene H4, H9 y H10 en `❌` (`product-spec.md:130,135-136`). En particular, H9 describe un launcher nativo aún inexistente. Una implementación no puede declararse final mientras su propio criterio de aceptación central sigue rojo.

**Fix mínimo requerido:** convertir cada reproducción anterior en test RED y hacer un gate cruzado docs↔parser↔registro. Los estados `pending/disabled` son válidos como contención, pero no deben contarse como PASS funcional del launcher.

## Aspectos que sí quedaron sólidos

- El claim FIFO se produce desde el `session_wait` vivo: `session_coordination.py:978-1005` solo entra en `_claim_head_from_wait_locked` si la cabeza sigue presente y no hay owner/release/WAL/handoff; release/expiry no conceden a ciegas.
- El grant queda provisional y no autoriza hasta cerrar WAL y revalidar: `session_coordination.py:2101-2360`; status/token ocultan o rechazan `grant_inflight`.
- Cancelación high-level crea `operation_id` antes del enqueue y ejecuta cleanup en toda salida no-active (`server.py:486-547`); los fixtures de cancel-before-arrival/respuesta perdida/audit busy pasan.
- Elección de daemon, migration candidate drain, parser CPython y recovery multiproceso pasan sus gates; no se observó doble autoridad ni se terminó ningún proceso real durante esta revisión.
- Las configuraciones efectivas del usuario se inspeccionaron sin imprimir contenido: Claude tiene el timeout entero esperado 1.860.000 y Codex 1.860.
- El `dayz-test.ps1` real y la plantilla son byte-idénticos y aplican correctamente captura temprana, limpieza del env y reposición temporal alrededor de `lifecycle_cli`. El defecto es la ausencia del padre seguro, no esa parte del consumidor.

## Validación ejecutada

Con `tools/.venv-mcp/Scripts/python.exe`:

| Grupo | Resultado |
|---|---|
| queue liveness | 24 tests, OK |
| audit fault/recovery | 44 tests, OK |
| acquire-wait | 10 tests, OK |
| secure launcher | 11 tests, OK — certifican placeholder deshabilitado |
| startup deadlock multiproceso | 22 tests, OK; 1 skip por privilegio de symlink |
| identity migration | 21 tests, OK |
| client mode | 39 tests, OK |
| host timeouts/provenance, último snapshot | 11 tests, OK |
| admin CLI | 5 tests, OK |
| doctor | 44 tests, OK |
| lifecycle CLI | 7 tests, OK |

Pruebas adversariales adicionales:

- comando exacto del runbook y variante ID-only del launcher;
- inspección del schema FastMCP real;
- fixture de own-torn admitido por el plan;
- fixture Windows de escritura concurrente sobre el handle `rb`;
- comprobación read-only de timeouts efectivos y resolución de procedencia desde ambas configuraciones.

No se arrancó daemon/DayZ, no se adquirió lease, no se modificó configuración real, no se ejecutó `.ps1` y no se terminó ningún proceso. Las pruebas de startup solo gestionaron sus wrappers fixture propios.

## Cierre requerido antes de repetir revisión

1. Congelar un snapshot sin escritores concurrentes y publicar hashes definitivos.
2. Decidir y materializar la ruta launcher compatible con H9; alinear README, runbook y skill.
3. Unificar máximo funcional 1.800 s con margen host de 60 s.
4. Completar recovery own-torn/restoring-source y sus crashes por syscall.
5. Endurecer el handle nativo contra write, no solo rename/delete.
6. Ejecutar las reproducciones RED→GREEN, suite focal/global y repetir revisión independiente sobre los mismos hashes.

VERDICT: BLOCKED
