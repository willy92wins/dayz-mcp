# BUG-046 — revisión adversarial R2 del hardening de provenance/routing

**VERDICT DEL PLAN R2: GREEN**

**GATE PRE-PRODUCCIÓN: BLOCKED — los RED actuales todavía no falsan V25f–V25k completos. V25l sí está suficientemente cubierto.**

La spec R2 ya es implementable: cierra las autoridades launch/native, schemas y defaults, pinning read-only, orden keyfile/port, métricas pre/post-connect, roots/sink del auditor y exclusión legacy. No detecté un bloqueante conceptual nuevo en el plan. Sin embargo, el propio gate de `plan:922` prohíbe pasar a GREEN material hasta implementar y poner verdes V25f–V25l; el conjunto actual pasa, pero varios tests todavía permiten implementaciones incorrectas.

No edité plan, tests ni producción. El único archivo creado por esta revisión es este informe.

## Snapshot revisado

| Archivo | Líneas | SHA-256 |
|---|---:|---|
| `plans/2026-07-22-bug046-lease-queue-liveness-plan.md` | 1066 | `5A5DC524D1AB0E41CC8DBDF1A192D517158A5986AD7CDA0F17642039870D2B0F` |
| `product-spec.md` | 257 | `3E24D8BF16E3B76D5BD001EE4BFE4A9DD10E780905096CF444728445718B7648` |
| `tools/tests/test_mcp_host_timeouts.py` | 724 | `B11AA37741D28F331B7CF4D71A854A9DA3B67C18F88B8212112193C0E55DB1A7` |
| `tools/tests/test_admin_cli.py` | 312 | `B6AD0E379EFF1AC47412DA9B81DE2DBB14E62F501B36A330217C645444BA10C6` |
| `tools/tests/test_security_runtime_audit.py` | 193 | `7A01413AA8D4397ED27F402C13577AC64434E4903D0E9C1FA054279CCCEA1BB4` |
| `tools/tests/test_daemon_security_gate.py` | 485 | `97ED899DDB65D8BA4FCE5058D83607E6CED64B7A33A891ADB29E0A3B2E355A50` |
| `tools/tests/test_client_mode.py` | 953 | `2D3BCFFBEE2441DBAED2F4E8282AB6DE5F6B9D53338971D2158929655D75364B` |

Ejecución focal observada:

- comando: `python -m unittest tests.test_mcp_host_timeouts tests.test_admin_cli tests.test_security_runtime_audit tests.test_daemon_security_gate tests.test_client_mode tests.test_daemon`;
- resultado: **124 tests OK, 1 skipped**, en 10.548 s;
- interpretación: estado verde del corpus actual, no acreditación de V25f–V25k mientras falten sus fixtures/aserciones.

## Por qué el plan R2 recibe GREEN

### 1. H10/V25 vuelven a ser compatibles

El plan ya separa correctamente:

- config/provenance/port/keyfile inválidos: `key_read=0`, `connect=0`, `request=0` (`plan:918`);
- foreign/rebind/PID/identity drift: `connect=1`, `request=0`, `http_bytes_sent=0`, `key_disclosed=0` (`plan:918`).

Eso coincide con V25 (`plan:347-349`) y con H10 (`product-spec.md:136`). También coincide con el transporte real: `connect()` precede a la acreditación y `connection.request()` sólo ocurre después (`tools/dayz_mcp/orphan_guard.py:983-1014`).

### 2. Launch/native queda normativo y trazable a APIs reales

`plan:912` define sin alias:

- `launch_executable = canonical(sys.executable)`, usado para construir argv;
- `native_executable = canonical(full_image_path_of(os.getpid()))`, usado como única imagen OS esperada;
- owner executable contra native y owner argv exacto conservando launch en `argv[0]`.

Las APIs existen con esas semánticas: `full_image_path_of` en `orphan_guard.py:292-314`, `build_daemon_argv(..., python=...)` en `daemon.py:741-759`, hashes separados de executable/argv en `native_process_guard.py:70-93`, y acreditación exacta en `orphan_guard.py:841-932`.

### 3. Schema/defaults ya forman un contrato cerrado

`plan:914` enumera fields, tipos, constantes de timeout, stdio, prefix, flags requeridos, duplicados separados/`=`, boolean con `=`, unknowns y las seis ausencias. También decide que task-label es metadata cliente fuera del daemon consensus. No queda una decisión de parser que el implementador deba inventar.

### 4. Pinning y keyfile ya tienen orden y resultados observables

`plan:916` fija ambos handles antes del primer parse con `GENERIC_READ + FILE_SHARE_READ`, permite readers, impide write/delete/replace, exige relectura identidad+bytes, cierre en error y detección de aparición/drift. `plan:918` exige port+keyfile antes de `_read_key` y connect, incluido el caso de dos ficheros existentes distintos. Ambos bloques son implementables y suficientemente concretos.

### 5. Auditor y exclusión legacy ya son decisiones cerradas

`plan:920` define roots, imports productivos transitivos, único sink autenticado, probe nominal y clases de bypass. También decide, con owner y cuatro scripts de evidencia, que `tools/mcp_client.py` es harness legacy contra `mcp_server.py`, fuera del H10 del daemon. Esto concuerda con el changelog D-18 (`product-spec.md:195-199`) y evita scope creep.

### 6. El gate previo a producción es explícito

V25f–V25l están en la matriz (`plan:352-358`), `plan:922` exige implementarlos y ponerlos verdes antes del GREEN material, y el focal de `plan:1030` ya incluye admin, daemon security y runtime audit. Por tanto el plan puede aprobarse sin fingir que los RED actuales ya cumplen el gate.

## Estado de falsabilidad de V25f–V25l

### V25f — BLOCKED en RED

Cobertura actual útil:

- provenance distingue launch/native y conserva launch en argv (`test_mcp_host_timeouts.py:210-267`);
- admin aserta native en `expected_executable` (`test_admin_cli.py:92-120`).

Huecos:

- el fixture aún incluye el alias legacy `executable=launcher` (`test_admin_cli.py:20-41`), por lo que puede ocultar el uso incorrecto;
- lifecycle y doctor no asertan `expected_executable` ni `expected_argv[0]` (`test_admin_cli.py:122-171`);
- ClientRuntime sólo aserta tipos/no-vacío, no native vs launch (`test_client_mode.py:866-888`);
- no existe negativo que intercambie ambas autoridades y exija fallo cerrado.

RED requerido: quitar el alias del fixture; usar `launch != native` en los cuatro consumidores; asertar native en `expected_executable`, launch en `expected_argv[0]`, e intercambio rechazado.

### V25g — BLOCKED en RED

Cobertura actual: entries reales 0/1, drift de policy, fields extra, `type=http`, JSON NaN, TOML anidado y duplicados separados (`test_mcp_host_timeouts.py:165-396`).

Faltan:

- cada field obligatorio ausente y wrong-type por host;
- timeout bool/string/float integral;
- duplicado mixto separado + `--opt=value`;
- boolean con `=`;
- ausencia individual de cada opcional con su resultado normativo. El positivo actual sólo retira require-version (`:386-396`).

### V25h — BLOCKED en RED

Cobertura actual: replace de Codex falla mientras empieza el parse de Claude y symlink de Claude se rechaza (`test_mcp_host_timeouts.py:398-451`).

Faltan:

- ambos configs read-only resolviendo con éxito;
- reader concurrente permitido;
- open-for-write, overwrite in-place, replace y delete bloqueados para **ambos** paths;
- prueba explícita de que ambos handles están abiertos antes de cualquier parse;
- aparición de path inicialmente ausente;
- drift final de identity y bytes;
- cierre verificable en success y en cada excepción.

El test actual también pasaría reutilizando un handle read-write/share=0, por lo que no acredita `GENERIC_READ + FILE_SHARE_READ`.

### V25i — BLOCKED en RED

Cobertura actual: admin/lifecycle no leen key si falla el resolver; los tres módulos rechazan un mismatch antes de entrar al transporte (`test_admin_cli.py:173-308`).

Faltan:

- resolver failure equivalente para doctor;
- expected.key y foreign.key **ambos existentes**. Los negativos actuales usan strings `C:\foreign\...` no creados y un helper de igualdad textual (`test_admin_cli.py:44-47,223-308`), así que no prueban la comparación canónica real;
- contadores explícitos `key_read=0`, `connect=0`, `request=0` para cada módulo y positivo exact-match.

### V25j — PARTIAL/BLOCKED en RED

`test_daemon.py:640-736` ya cubre el orden positivo, owner foreign, socket cerrado, rebind, PID/argv/cwd/fingerprint drift y ausencia de evento request. Falta convertirlo al criterio exacto de V25j:

- asertar exactamente un evento connect en cada negativo relevante;
- instrumentar bytes enviados, no inferirlos sólo de ausencia de `request`;
- asertar que la key literal no aparece en ninguna captura del listener/eventos.

### V25k — BLOCKED en RED

Cobertura actual:

- aliases importados y asignados, factories/connection request y `urlencode({'key': ...})` (`test_security_runtime_audit.py:40-93`);
- allowlist nominal no file-wide, incluido un segundo sink inesperado dentro de la función canónica (`:95-147`);
- exclusión estrecha de `mcp_client.py` (`:149-189`).

Faltan los elementos expresos de `plan:920`/V25k:

- reexport a través de un segundo módulo;
- `getattr` dinámico no resoluble;
- concat y f-string de `?key`;
- fixture de root que importa transitivamente el módulo con bypass;
- control que demuestre que se calcula clausura desde roots. El test actual coloca un `.py` arbitrario en el paquete, por lo que un scan de todos los archivos pasa sin implementar clausura productiva.

### V25l — READY

`test_security_runtime_audit.py:149-171` exige protocolo, owner, razón y los cuatro scripts de evidencia; además verifica que cada script referencia `mcp_server.py` y `mcp_client.py`. `:173-189` prueba que la exclusión top-level no oculta un módulo homónimo dentro del package. Esto es suficiente para la decisión cerrada de `plan:920`.

## Gate operativo antes de más producción

Orden requerido por el plan aprobado:

1. Completar los RED anteriores para V25f–V25k sin editar producción.
2. Ejecutarlos y demostrar que fallan por las razones previstas o que ya protegen una implementación existente mediante mutation/canary equivalente.
3. Sólo entonces ajustar producción hasta GREEN.
4. Reejecutar el focal de `plan:1030` y el discovery global de `plan:1035`.

Los cambios de producción ya presentes deben considerarse provisionales hasta superar este gate; el resultado 124/124 actual no lo sustituye.

## Conclusión

**PLAN R2: GREEN.** No requiere otra corrección de arquitectura para arrancar TDD.

**IMPLEMENTATION/PRODUCTION READINESS: BLOCKED.** Completar V25f–V25k es condición previa explícita; V25l ya está listo.
