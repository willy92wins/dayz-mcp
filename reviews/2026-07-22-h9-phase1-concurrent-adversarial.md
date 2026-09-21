# BUG-046 / H9 / H10 — revisión adversarial de la Fase 1 concurrente

Fecha: 2026-07-22  
Veredicto: **PHASE1 RED — no implementar las fases siguientes, no registrar launcher**

## Alcance y trazabilidad

Revisión read-only de los cambios de Fase 1 aparecidos concurrentemente mientras el
plan A7 seguía sin aprobación humana ni atestación detached válida. No se lanzó daemon,
DayZ, launcher ni proceso de producto. Los tests se ejecutaron bajo el runner P0.S
deny-launch; cualquier intento de proceso fue interceptado antes de crear el child.

Plan actual:

- `plans/2026-07-22-bug046-h9-native-launcher-plan.md`
- SHA-256 real: `A9F54A7D8B155ED22C35BC0DA4DC9049C661D101A21D5B9FAA505DE2A37294A6`
- El header todavía acredita `D65FCE3F...D6B96B7`; por tanto no acredita estos bytes.
- La aprobación humana recibida fue `apruebo A4`, no una aprobación del alcance material A7.

Snapshot root estable usado para las reproducciones finales:

| Fichero | SHA-256 |
|---|---|
| `tools/dayz_mcp/server.py` | `8B86171DA20D249492EE3FF3806B301DDC6FA033CAD511FC3E56B0FF50AB6229` |
| `tools/dayz_mcp/control_client.py` | `1A5D53CFADC35E7A6130E745226304F4679AAE6781380C5B26F11601955444FD` |
| `tools/dayz_mcp/dayz_test_request.py` | `38317B249F7DA35E0D6B7147A213971FFBA31EB62809BAC4BB1F3500560ABF8F` |
| `tools/dayz_mcp/daemon_policy.py` | `8FA803F61FE5CFA04CDEDBF14AE41BD17BC4DD07A2563F445CB18F45AAD14549` |
| `tools/dayz_mcp/accredited_daemon_transport.py` | `FEEC5F9657EBFDAB668923CFBE897F3AA2E14FD9CFC2AAB843BAF7F0D07B6556` |

El subagente adversarial cerró sobre un snapshot anterior de `control_client.py`
(`3ED5B980...83F7`) y detectó 6 High + 4 Medium. Root volvió a comprobar los
hallazgos sobre los hashes anteriores; los resultados vigentes se enumeran abajo.

## Hallazgos bloqueantes

### H1 — una cancelación puede ocultar un ticket vivo

`control_client.py:388-394` cambia siempre `state` a `CLOSED`, incluso si el
`operation_id` que termina ya no coincide. A la vez,
`control_client.py:420-429` usa `asyncio.shield` pero convierte la cancelación del
wrapper en una degradación y deja el cleanup antiguo desprendido.

Reproducción sobre SHA `1A5D53CF...5444FD`:

```text
{'cleanup_result': 'session cleanup degraded: CancelledError',
 'operation': 'new', 'ticket': 'new-ticket', 'state': 'CLOSED'}
```

Impacto: **corruption** de la máquina de estado y **degradation** directa de
liveness; la cola conserva un ticket que el cliente deja de representar como vivo.

Fix requerido: el cleanup no puede desprenderse; debe conservar el fence/lock hasta
resultado terminal pese a cancelaciones repetidas, y `_clear_operation` no puede
modificar ningún campo si el operation ID no coincide. Añadir RED real old/new y
grant/cancel.

### H2 — la policy de request es lexical y acepta autorizar todo `P:\`

`dayz_test_request.py:108-154` sólo usa strings/`ntpath`; la policy no contiene
volume/file identity (`:157-164`) y `_validate_policies` permite una raíz de volumen
completa (`:174-219`). Reproducción sobre SHA `38317B24...ABF8F`: una policy con
`dev_root`, `default_source`, `mission_roots` y `mod_roots` iguales a `P:\` fue
aceptada y produjo request canónica.

Impacto: **bypass de autorización** de proyecto mediante roots demasiado amplios,
junctions/reparses, aliases 8.3, trailing dot/space o sustitución posterior.

Fix requerido por A7: rechazar volume roots; sellar identidades de volumen/fichero;
abrir por handle; verificar todos los padres y reparses; canonicalizar por identidad
antes de enqueue. Los tests actuales reconocen que cubren sólo confinamiento lexical.

### H3 — la authority bootstrap todavía puede ser fabricada por el caller

El último drift añadió un segundo handle de liveness y routing cerrado, pero no una
raíz de confianza. Los duplicadores sólo comprueban tipo e inheritability
(`daemon_policy.py:206-269`). `AccreditedDaemonPolicy.__post_init__`
(`:277-327`) valida un SHA calculado con los mismos campos no autenticados. El router
acepta los tres valores desde un mapping de entorno aportado por el caller
(`:420-460`); `revalidate` únicamente comprueba que el handle siga teniendo tipo pipe,
no EOF, parent/controller identity ni procedencia (`:329-338`). Los tests positivos
crean su propio fichero/pipe heredable y los aceptan (`test_daemon_policy.py:237-312`).

Impacto: una manifest local y un build ID elegidos por el caller pueden autorizar
port/keyfile/argv arbitrarios.

Fix requerido: ligar ambos handles al controller/PE acreditado, verificar liveness
real/EOF e identidad del creador, anclar build ID fuera del manifest y rechazar
policies fabricadas por un caller no acreditado.

### H4 — la acreditación del socket no autentica los bytes del daemon

`accredited_daemon_transport.py:115-201` compara path/argv/cwd y snapshots.
`native_process_guard.identity_hashes` (`:70-93`) llama
`executable_sha256` al hash del *string normalizado del path*, no al hash de los bytes
del ejecutable. `verified_daemon_http_request` no recibe `security_build_id`
(`accredited_daemon_transport.py:204-225`) y, tras esta acreditación insuficiente,
añade la key y envía el body (`:281-287`).

Impacto: **secret disclosure** posible a un listener impostor que reproduzca la forma
esperada de executable/argv/cwd.

Fix requerido: acreditar por handles e identidades reales la imagen, script y closure
fijados, además de build/liveness bootstrap, antes de formar key, identity o lease.

### H5 — el contrato público sigue parando la espera a los 1800 segundos

`server.py:33` fija `MAX_SESSION_ACQUIRE_WAIT_S = 1800.0`; la tool pública usa ese
valor como default y máximo (`server.py:781-803`). Esto contradice A7:23 y la necesidad
del usuario: sin timeout explícito la ejecución debe permanecer en FIFO hasta grant o
cancelación, no terminar localmente por defecto.

Fix requerido: default indefinido en la ruta productiva, con timeout sólo opt-in y
cleanup exacto del ticket/op cuando se elige.

### H6 — la Fase 1 no pasa sus regresiones ni su auditor de clausura

Evidencia final bajo P0.S:

- `ClientRuntimeControlCompositionTests`: 3/3 `OK` después del último drift.
- Focal completo Fase 1 (parser, policies, process snapshot, daemon contract,
  transport, control, composición, acquire_wait y auditor): 86 tests, **3 failures +
  2 errors**, `attempts=[]`, `intercept_count=0`. Fallan cuatro regresiones de `session_acquire_wait` y
  `test_productive_runtime_closure_has_no_unaccredited_http_path`.
- `tests.test_client_mode`: 46 tests, **14 failures + 7 errors**. Hay roturas de
  serialización acquire/release/wait/heartbeat, limpieza selectiva de ticket/lease,
  redacción de errores y orden de consensus/key-read. Los intentos de spawn quedaron
  interceptados por deny-launch.

Impacto: no existe evidencia de compatibilidad pública ni de una única ruta HTTP
acreditada; declarar GREEN produciría falso positivo.

Fix requerido: migrar los tests antiguos al contrato explícito sin eliminar sus
invariantes, hacer GREEN la clausura productiva y ejecutar focal + suite global sobre
un snapshot inmóvil.

### H7 — el launcher solicitado todavía no existe

`tools/dependency-lock.json` y `tools/native-launchers/dayz-test-v1/` no existen.
`tools/approved-launchers.json` conserva `"launchers": []`. `secure_launcher` sigue
sin backend nativo configurado; lifecycle/admin/doctor todavía resuelven provenance
por su ruta legacy y no implementan el routing normal/bootstrap A7 completo.

Impacto: Claude sigue sin una ruta productiva que reciba JSON por stdin, espere en
FIFO indefinidamente, supervise heartbeat y entregue el lease sólo por entorno al
child autorizado. El problema original del usuario permanece abierto.

## Hallazgos adicionales

### M1 — el gate “non-launching” no cubre el closure transitivo

`ControlClient -> daemon_policy -> host_config -> orphan_guard` alcanza imports y
sinks de subprocess/terminación; el transporte importa el guard completo. Los tests
actuales inspeccionan imports directos o una función, no el closure alcanzable.

### M2 — la key tiene vida excesiva

`ControlClient` lee la key como `str` y la conserva en la instancia antes de acreditar
cada socket. Amplía copias/ventana y no observa rotación. Debe leerse pinneada JIT,
después de acreditar authority/handle, y limpiarse best-effort.

### M3 — provenance normal deriva la imagen del proceso consumidor

`host_config._local_native_executable` usa `os.getpid()` (`host_config.py:141-150`).
Bajo el CPython privado del futuro lifecycle CLI puede esperar esa imagen aunque el
daemon real use otra imagen registrada.

### M4 — la ruta bridge todavía reintenta fallos ambiguos

Aunque sesión ya compone `ControlClient`, `ClientRuntime._call`
(`server.py:557-575`) captura cualquier `ConnectionError/OSError`, spawnea y repite.
Como el transporte acreditado expresa también fallos post-request como
`ConnectionError`, una mutación bridge podría repetirse después de haber enviado
bytes. Debe aplicarse también la tupla exacta pre-request/0 o una semántica idempotente
demostrada.

### M5 — normalización de nesting no está cerrada por contrato

`json.loads` sólo captura `UnicodeDecodeError` y `JSONDecodeError`
(`dayz_test_request.py:232-240`). El test actual pasa en CPython 3.14, pero la ruta no
normaliza explícitamente `RecursionError`/errores de límite; debe cerrarse antes del
runtime embebido fijado.

## Condiciones para una nueva luz verde

1. Aprobación humana explícita de A7 y atestación detached del SHA final, sin self-hash.
2. RED→GREEN de H1–H6, manteniendo intactas las invariantes de los tests legacy.
3. Auditoría adversarial nueva sobre hashes finales inmóviles, con 0 Critical/High.
4. Registro todavía vacío hasta dependency lock, dos builds reproducibles, build
   offline, cierre de manifest, routing normal/bootstrap, lifecycle ACK/heartbeat/
   cleanup y gate vivo seguro.

No se recomienda ni autoriza registrar el launcher actual.
