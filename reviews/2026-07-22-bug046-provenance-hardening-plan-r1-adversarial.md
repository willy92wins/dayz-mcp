# BUG-046 — revisión adversarial R1 del hardening de provenance/routing

**VERDICT: BLOCKED**

El addendum de `Step 3a.1` corrige riesgos reales, pero todavía no está listo para implementar como spec cerrada. Hay una contradicción verificable con V25 sobre el contador de conexiones, el contrato `launch_executable`/`native_executable` no queda falsado en los cuatro consumidores, y faltan viability tests para el schema raw, el pin simultáneo read-only, el orden del keyfile y la clausura productiva del auditor.

No edité plan, tests ni producción. El único archivo creado por esta revisión es este informe.

## Alcance e integridad del snapshot

Revisado:

- `plans/2026-07-22-bug046-lease-queue-liveness-plan.md:903-905` (H10/Step 3a/Step 3a.1).
- `plans/2026-07-22-bug046-lease-queue-liveness-plan.md:347-351` (V25–V25e).
- `product-spec.md:121-136` y `product-spec.md:195-199` (Intent + H10).
- RED inicialmente entregados en `tools/tests/test_mcp_host_timeouts.py:74-451` y `tools/tests/test_admin_cli.py:44-299`.
- APIs reales de construcción/acreditación/routing citadas abajo.

Snapshot congelado al comenzar:

| Archivo | Líneas | SHA-256 |
|---|---:|---|
| `plans/2026-07-22-bug046-lease-queue-liveness-plan.md` | 1049 | `1E3A481094C6628CA808A2682CC95AF236CCC5B5F48DED3FE2BBFDCDA2B193D5` |
| `product-spec.md` | 257 | `3E24D8BF16E3B76D5BD001EE4BFE4A9DD10E780905096CF444728445718B7648` |
| `tools/tests/test_mcp_host_timeouts.py` | 724 | `B11AA37741D28F331B7CF4D71A854A9DA3B67C18F88B8212112193C0E55DB1A7` |
| `tools/tests/test_admin_cli.py` | 303 | `DFE4C147987C178206DCF8B89CF5B791F277071FE9B99F67A4F52C89DDEE7E92` |

Durante la revisión hubo escritura concurrente fuera de este subagente: `tools/dayz_mcp/host_config.py` pasó de 828 líneas/SHA `F5D0C384…` a revisiones posteriores, y `tools/tests/test_admin_cli.py` pasó después a 312 líneas/SHA `B6AD0E37…`. Por eso el dictamen se refiere al plan y RED congelados arriba; uso producción sólo para verificar contratos reales, no para juzgar una implementación en movimiento.

## Contratos reales verificados

1. `orphan_guard.full_image_path_of(pid)` devuelve la imagen OS mediante `QueryFullProcessImageNameW` y documenta expresamente la diferencia entre redirector de venv e intérprete base (`tools/dayz_mcp/orphan_guard.py:292-314`).
2. `daemon.build_daemon_argv(config, python=...)` pone el launcher recibido en `argv[0]` y construye el tail canónico del daemon (`tools/dayz_mcp/daemon.py:741-759`). `daemon.daemon_runtime_cwd()` existe y devuelve el cwd productivo (`tools/dayz_mcp/daemon.py:153-154`).
3. `server_cli.parse_server_tail_silent(argv)` existe con status `parsed|terminal|invalid`, parser sin abreviaturas y namespace opcional (`tools/dayz_mcp/server_cli.py:8-12`, `:91-113`).
4. La acreditación real compara la imagen OS observada con `expected_executable`, el argv exacto con `expected_argv` y el cwd, y vuelve a comprobar PID/snapshot B (`tools/dayz_mcp/orphan_guard.py:841-932`). Los hashes v2 separan executable y argv (`tools/dayz_mcp/native_process_guard.py:70-93`, `:104-159`).
5. El transporte canónico hace primero `connect()`, acredita el socket, y sólo después añade `key` al query y llama `connection.request()` (`tools/dayz_mcp/orphan_guard.py:935-1021`). Éste es el orden que H10 exige.
6. Los cuatro consumidores productivos previstos son reales: `ClientRuntime._request_once` ya usa el transporte verificado (`tools/dayz_mcp/server.py:632-668`); doctor, admin y lifecycle tienen sus callsites en `tools/dayz_mcp/doctor.py:122-149`, `tools/dayz_mcp/admin_cli.py:20-49` y `tools/dayz_mcp/lifecycle_cli.py:21-43`.

## Hallazgos bloqueantes

### H-01 — “Todo negativo = cero connect” contradice V25 y el transporte real

**Severidad: alta — contrato imposible de satisfacer literalmente.**

`Step 3a.1` termina con “Todo negativo demuestra cero connect/request/key” (`plan:905`). Sin embargo V25 exige explícitamente `connect=1`, `request=0` para un owner foreign (`plan:347`), y foreign/rebind sólo se pueden descubrir sobre el socket ya conectado. El transporte real confirma ese orden: `connect()` en `orphan_guard.py:986`, acreditación en `:988-1001`, request en `:1012-1014`.

Cambio mínimo de plan:

- negativos de config/provenance pre-connect y mismatch port/keyfile: `connect=0`, `request=0`, `key_read=0`;
- foreign/rebind/PID/identity drift: `connect=1`, `request=0`, `http_bytes_sent=0`, `key_disclosed=0`;
- no usar “key=0” sin distinguir `key_read` local de `key_disclosed` al listener.

Esto alinea el addendum con H10 (`product-spec.md:136`) y V25–V25c (`plan:347-349`).

### H-02 — Autoridades launch/native: el diseño es circular y los RED sólo protegen admin

**Severidad: alta — una regresión en doctor/lifecycle puede enviar el launcher como imagen OS esperada.**

La frase “`native_executable` procede de la imagen OS del proceso local acreditado” (`plan:905`) es circular: cuando se resuelve provenance todavía no existe un owner remoto acreditado. La API disponible obtiene la imagen del proceso cliente actual (`full_image_path_of(os.getpid())`); después esa expectativa se compara con el owner del socket.

El RED distingue ambos campos y exige `argv[0] == launch_executable` (`test_mcp_host_timeouts.py:210-267`), y admin comprueba que `expected_executable` recibe el native (`test_admin_cli.py`, snapshot inicial `:85-113`). Pero el fixture conserva además un alias legacy `executable=launcher` (`test_admin_cli.py:20-41`), y los positivos de lifecycle/doctor no asertan `expected_executable` (`test_admin_cli.py`, snapshot inicial `:115-164`). Por tanto esos dos consumidores podrían seguir usando el launcher/alias equivocado sin que sus tests focales lo detecten.

Cambio mínimo de plan:

- definir `launch_executable = canonical(sys.executable)`, consensuado con ambos `command`, usado sólo para `daemon.build_daemon_argv(..., python=launch_executable)`;
- definir `native_executable = canonical(full_image_path_of(os.getpid()))`, usado como único `expected_executable`/hash de imagen OS;
- comparar owner executable/snapshot con native, pero owner argv exacto con el vector construido cuyo `argv[0]` es launch;
- eliminar cualquier campo/alias ambiguo `executable` del contrato público de provenance.

RED mínimo adicional: ejecutar el caso `launch != native` a través de admin, doctor, lifecycle y ClientRuntime; los cuatro deben entregar native a `expected_executable`, launch en `expected_argv[0]`, y fallar si se intercambian.

### H-03 — “Schema raw cerrado” y defaults siguen sin tabla normativa ni tests de tipo

**Severidad: alta — provenance autoritativa puede aceptar shapes equivalentes por coerción de Python/argparse.**

El plan ordena cerrar el schema, pero no enumera el set exacto de fields y tipos de cada host ni el valor de cada default ausente (`plan:905`). Los RED sí cubren fields extra concretos, `type=http`, JSON `NaN` y subtabla TOML (`test_mcp_host_timeouts.py:280-330`), pero no cubren:

- field obligatorio ausente por host;
- `timeout/tool_timeout_sec` boolean, string o float integral (`1860.0 == 1860` en Python);
- `command`/`args` con tipos incorrectos o elementos no-string;
- duplicado mixto `--opt value` + `--opt=value`;
- ausencia individual de cada opcional. El positivo de ausencia sólo retira `--require-version` y no demuestra las seis semánticas por separado (`test_mcp_host_timeouts.py:364-396`).

Cambio mínimo de plan: añadir una tabla cerrada:

- Claude: exactamente `type`, `command`, `args`, `timeout`; `type == "stdio"`; timeout entero no-bool `1_860_000`.
- Codex: exactamente `command`, `args`, `tool_timeout_sec`; timeout entero no-bool `1_860`.
- `command` string absoluto/canónico exacto; `args` lista de strings; prefix exacto `-m dayz_mcp`.
- ausencias: expected-version=`None`, require-version=`False`, exec-enforce=`False`, exec-allowlist=`None`, no-autospawn ausente=`auto_spawn=True`, task-label=`""`; declarar que task-label es metadata cliente y no entra en daemon argv/policy consensus.
- contar formas separadas y `=` como la misma opción; boolean con `=` es inválido; unknown siempre inválido.

### H-04 — El pin simultáneo no tiene viability tests para “read-only” ni revalidación

**Severidad: alta — R26 no permite implementar una autoridad de config sin criterios falsables completos.**

El addendum exige ambos handles simultáneos, read-only, no-follow/no-reparse, identidad+bytes estables y revalidación final (`plan:905`). El RED actual prueba sólo que un `os.replace` sobre Codex falla mientras comienza el parse de Claude (`test_mcp_host_timeouts.py:398-435`) y que un symlink de Claude se rechaza (`:437-451`). Ese test también pasaría con un handle read-write/share=0 reutilizado de la transacción de timeouts; no demuestra la propiedad read-only.

Faltan pruebas de:

- configs marcadas read-only/ACL sólo lectura siguen resolviendo;
- lectores concurrentes funcionan, pero open-for-write, overwrite in-place, replace y delete fallan para **ambos** paths mientras ambos handles viven;
- ambos handles están abiertos antes de parsear cualquiera;
- config ausente creada durante la resolución se detecta en la revalidación;
- drift inyectado de identidad o bytes en la relectura final falla cerrado;
- handles se cierran en success y en cada excepción;
- reparse se rechaza sin seguir el target.

Cambio mínimo de plan: fijar `GENERIC_READ`, compartir sólo lectura (sin share-write/share-delete), adquisición determinista de ambos paths, mantener handles hasta la última relectura, y convertir toda carrera/IO incierto en `daemon_provenance_conflict` antes de key/connect.

### H-05 — El gate de keyfile no demuestra comparación canónica contra otro fichero existente

**Severidad: alta — los negativos actuales pueden pasar sólo porque la ruta foreign no existe.**

El addendum exige comparar el keyfile consensuado antes de leer y antes de connect (`plan:905`). Los RED de mismatch usan `C:\foreign\daemon.key`, que no se crea (`test_admin_cli.py`, snapshot inicial `:216-299`). Una implementación que simplemente haga `resolve(strict=True)` y falle por inexistencia pasaría sin haber comparado dos paths canónicos existentes.

Además, el fallo genérico de `resolve_daemon_provenance()` antes de key-read está cubierto para admin/lifecycle, no para doctor (snapshot inicial `test_admin_cli.py:166-214`).

Cambio mínimo de plan y RED:

- crear `expected.key` y `foreign.key`, ambos existentes; pasar foreign y exigir `key_read=0`, `connect=0`, `request=0` en los tres módulos;
- inyectar `daemon_provenance_incomplete/conflict` en doctor además de admin/lifecycle;
- positivo con el path canónico exacto;
- asertar port y keyfile antes de `_read_key`; sólo después se permite entrar al transporte verificado.

### H-06 — “Clausura productiva” y `mcp_client.py` mantienen una decisión abierta

**Severidad: alta — el auditor puede quedar como scan lexical no relacionado con V25e o excluir un cliente por una condición no cerrada.**

El plan dice a la vez crear `security_runtime_audit.py` (`plan:882,891`) y “usar el auditor existente” (`plan:905`); debe aclarar que se extrae/sustituye el gate AST previo. Tampoco define raíces, cómo se recorre la clausura, ni la frontera entre el único sink autenticado permitido y la allowlist de probes no autenticados.

La clasificación condicional de `tools/mcp_client.py` sigue abierta: “migrarlo si es ejecutable/productivo” (`plan:883,905`). La evidencia real confirma que es ejecutable, pero pertenece al harness legacy: construye `?key` y usa `urlopen` (`tools/mcp_client.py:197-240`), mientras `run-poc.ps1`, `run-fase1.ps1`, `run-fase2.ps1` y `run-fase3.ps1` lo emparejan explícitamente con `mcp_server.py` (`run-poc.ps1:472-473,609,674`; `run-fase1.ps1:357-358,491,556`; `run-fase2.ps1:256-257,406,471`; `run-fase3.ps1:256-257,455,529`). H10 y su changelog acotan el criterio al daemon (`product-spec.md:136,195-199`).

Cambio mínimo recomendado:

- raíces: `ClientRuntime`, doctor, admin y lifecycle, más sus imports productivos transitivos;
- único sink autenticado: `orphan_guard.verified_daemon_http_request`; su `HTTPConnection.request` y construcción de query no son una allowlist general;
- allowlist nominal separada sólo para `probe_listener_responsive`, que debe permanecer sin key/body sensible;
- declarar `tools/mcp_client.py` exclusión nominal del criterio H10 por ser harness legacy contra `mcp_server.py`, con owner/evidencia anterior. Si se quiere que H10 cubra también el harness legacy, eso amplía DPF/scope y requiere decisión del usuario antes de migrarlo;
- el auditor debe recorrer dependencias alcanzables, no sólo una lista de paths ni “todos los `.py` del directorio”. Debe fallar ante aliases importados/asignados, reexports, `getattr` dinámico no resoluble, instancia `HTTPConnection.request`, y construcción de `?key` por concat/f-string/urlencode fuera del sink.

### H-07 — La suite focal no nombra los nuevos gates

**Severidad: media — riesgo de ejecutar sólo el comando focal y omitir H10.**

El comando focal de `plan:1013` no incluye `tests.test_admin_cli`, `tests.test_security_runtime_audit` ni explícitamente `tests.test_daemon_security_gate`; el discovery global de `plan:1018` sí los descubriría. Añadirlos al focal evita que Task 5B pueda declararse verde con el gate H10 omitido.

## Viability tests mínimos que faltan

Propongo añadir a la matriz del plan:

| ID | Fixture | Resultado verificable |
|---|---|---|
| V25f | `launch != native`; cuatro consumidores | native llega a `expected_executable`; launch sólo a `argv[0]`; intercambio falla cerrado |
| V25g | missing/wrong-type fields, bool/float timeouts, duplicates separados+`=`, ausencia de cada opcional | schema/tipo exacto; cero key-read/connect en error de provenance |
| V25h | dos configs read-only; lector/escritor/replace/delete concurrentes; drift final; config ausente aparece | dos handles simultáneos read-only; lectores sí; escritores/deletes no; drift/aparición falla antes de key/connect; handles cerrados |
| V25i | expected.key y foreign.key existentes; resolver failure en doctor/admin/lifecycle | mismatch/failure: key-read=0, connect=0, request=0; exact match alcanza transporte |
| V25j | owner foreign/rebind con key ya leída | connect=1, request=0, bytes=0, key/identity/lease no observables |
| V25k | módulo transitivo con aliases asignados/reexport/getattr y concat/f-string/urlencode de key | auditor lo detecta; sólo sink acreditado y probe nominal quedan permitidos |
| V25l | referencias reales de `mcp_client.py` | exclusión legacy cerrada y evidenciada, o migración explícita tras ampliar scope; nunca condicional sin resolver |

## Cobertura positiva ya útil

Los RED congelados sí aportan buen valor y deben conservarse:

- cuentan entradas reales 0/1 aunque existan ambos ficheros (`test_mcp_host_timeouts.py:165-189`);
- detectan drift de policy/autospawn (`:191-208`);
- separan launch/native a nivel de provenance (`:210-267`);
- rechazan fields extra, transporte no-stdio, JSON no estándar y TOML anidado (`:280-330`);
- detectan flags requeridos ausentes/duplicados y opcionales repetidos en forma separada (`:332-396`);
- prueban pin antes del primer parse y rechazo de symlink (`:398-451`);
- preservan method/path/query/body y bounded response en routing (`test_admin_cli.py`, snapshot inicial `:85-164`).

## Condición de GREEN para R2

R2 puede ser `GREEN` cuando el plan incorpore las seis decisiones mínimas anteriores y los RED demuestren V25f–V25l. No hace falta rediseñar H10 ni cambiar payload/persistencia; es un cierre de contratos y observabilidad de tests.

**VERDICT FINAL: BLOCKED**
