# Auditoría R9 — pérdida de datos ajena / seguridad / fail-closed — lote D

## VEREDICTO

CRITICAL=0 (ninguno con repro ejecutable de impacto crítico) · MAJOR=4 · BACKLOG=2

**Dictamen:** el lote corrige el caso estable que originó la ficha —un holder ya bindeado en el puerto solicitado es rechazado aunque su imagen no sea DayZ—, pero **todavía no garantiza que el MCP no lance encima de un servidor ajeno**. Persisten una ventana pre-bind para `DayZServer_x64.exe`/imágenes renombradas, una carrera check→launch sin reserva, un respaldo `netstat` capaz de certificar como vacío un dump truncado y una protección exclusivamente puntual frente a lanzadores externos posteriores.

Los 23 tests focales pasan, pero no cubren esas secuencias. La suite completa entregada tampoco está verde: `SUITE-D.txt:1-8` registra 2595 tests, 6 fallos y 6 skips.

## C-1 — Cobertura del testigo durante el arranque

### Qué cubre y qué no

- Un `DayZDiag_x64.exe` ajeno queda visible **antes del bind** por el `diag_probe`; `_foreign_diag_reason` cierra ante cualquier PID observado no registrado (`tools/dayz_mcp/process_lifecycle.py:3525-3533`).
- Un `DayZServer_x64.exe` estándar no tiene ese testigo. El `retail_probe` enumera solo `DayZ_BE.exe` y `DayZ_x64.exe` (`tools/dayz_mcp/orphan_guard.py:213-216`, `:288-290`) y el `diag_probe` de producción solo `DayZDiag_x64.exe` (`tools/dayz_mcp/daemon.py:525-528`). Hasta que aparezca un socket UDP, el nuevo `port_probe` devuelve legítimamente una lista vacía y `_foreign_port_reason` abre (`tools/dayz_mcp/process_lifecycle.py:3274-3285`).
- La ampliación del conjunto `_DAYZ_IMAGE_NAMES` a `DayZServer_x64.exe` solo ayuda **después** del bind (`tools/dayz_mcp/orphan_guard.py:389-397`); no amplía el testigo de procesos del daemon. El comentario del cambio dice que la tabla de sockets es «el segundo testigo» (`tools/dayz_mcp/daemon.py:529-531`), pero el diff no documenta por qué se dejó fuera `DayZServer_x64.exe` del primer testigo. Es una omisión, no una decisión justificada.

### Medición pasiva en este host

El PID solicitado, 45428, ya no existía cuando pude observarlo. El control del orquestador sí lo había visto sosteniendo 2302/2304 (`COMUN.txt:40-41`). Durante esta auditoría apareció otro servidor, sin tocarlo:

[EXACT — salida literal recortada de `netstat -ano -p UDP` y psutil]

```text
UDP    0.0.0.0:2302    *:*    52144
UDP    0.0.0.0:2304    *:*    52144
addr addr(ip='0.0.0.0', port=2302) pid=52144
addr addr(ip='0.0.0.0', port=2304) pid=52144
PID 52144 name=DayZDiag_x64.exe created=2026-09-04T16:55:38.292801+02:00
client PID 26180 created=2026-09-04T16:55:50.527256+02:00
```

El RPT del mismo proceso registra `Hostname of server` a las 16:55:48.490 y `Network simulation` a las 16:55:48.565 (`_server/profiles/DayZDiag_x64_2026-09-04_16-55-38.RPT:38-40`). El worker no lanza el cliente hasta que `wait_for_owned_udp` acredita que 2302 pertenece exactamente al PID gestionado (`tools/dayz_mcp/dayz_test_worker.py:618-639`; `tools/dayz_mcp/dayz_test_readiness.py:143-158`). Por tanto, el bind ocurrió como máximo 12,235 s después de crear el servidor. **Esto es una cota superior, no una medida del instante exacto de bind.** Un observador de 180 s no capturó otro arranque:

[EXACT — salida literal]

```text
baseline_server_pids= [52144]
NO_NEW_SERVER_WITHIN_180S
```

El repo contempla explícitamente una espera de hasta 60 s antes de lanzar el cliente (`tools/spike0/spike0-ping.ps1:49-55`; default `server_wait_s=60` en `tools/dayz_mcp/dayz_test_tool.py:128-132`), pero eso es un timeout de diseño, **no evidencia** de que una configuración real permanezca 30–60 s sin bind.

### Conclusión C-1

Sí existe la ventana para `DayZServer_x64.exe`: el código abre mientras el proceso existe pero aún no tiene socket. Una reproducción aplicó al proceso ajeno el filtro exacto de producción (`[DayZDiag_x64.exe]`), obtuvo un snapshot conocido/vacío, pidió expresamente `role=server`/`-server` y terminó `RUNNING` con una llamada al launcher:

[EXACT — reproducción pre-bind]

```text
diag_snapshot={'known': True, 'processes': []}
result={'ok': True, 'run_id': 'run-1', 'state': 'RUNNING'}
launcher_calls=1
argv=[...DayZDiag_x64.exe, -server, -mission=test, -port=2302]
```

Además hay una carrera independiente: el sondeo termina en `tools/dayz_mcp/process_lifecycle.py:1913-1922`, pero el launcher no se invoca hasta `:2020-2024`, después de auditoría, commit, escritura de manifest y preparación. Un probe secuencial confirmó que solo hubo una lectura y que un holder aparecido al entrar al launcher no fue reevaluado:

[EXACT — reproducción check→launch]

```text
result={'ok': True, 'run_id': 'run-1', 'state': 'RUNNING'}
port_probe_calls=1 launcher_calls=1
holder_at_launch={'known': True,
  'holders': [{'port': 2302, 'pid': 777, 'name': 'foreign.exe'}]}
```

`tools/README-mcp.md:80` reconoce que no existe registro de reservas.

[DESIGN — corrección propuesta]

```text
1. Ampliar el diag_probe de producción, como mínimo, a:
   DayZDiag_x64.exe + DayZServer_x64.exe.
2. Mantener el sondeo UDP fresco justo antes del launch; repetirlo después de
   _prepare_instance y antes de self.launcher para reducir la ventana.
3. Tras crear el proceso propio, verificar que el puerto solicitado queda
   atribuido solo al PID propio; si aparece un owner ajeno, retirar únicamente
   el proceso recién lanzado por el MCP y devolver port_in_use_foreign.
4. Para imágenes renombradas y lanzadores externos no cooperativos, la garantía
   absoluta exige que todos usen una misma reserva/lease. Un nombre ampliado no
   puede detectar un binario arbitrariamente renombrado antes de su bind.
```

## C-2 — Imagen renombrada y contradicción `box_occupancy` / `start_run`

La seguridad puntual de `start_run` funciona: cualquier holder no registrado del puerto solicitado bloquea aunque se llame `renamed.exe` (`tools/dayz_mcp/process_lifecycle.py:3277-3284`; test existente `tools/tests/test_box_port_occupancy.py:239-248`).

La caja pública usa otra regla: solo recuerda el puerto y crea `foreign` si el PID es registrado o el nombre pasa `_is_dayz_image`; cualquier imagen desconocida se salta en `tools/dayz_mcp/process_lifecycle.py:3410-3430`. Eso produce la contradicción indicada:

[EXACT — repro ejecutado con el intérprete obligatorio]

```text
renamed-holder-on-requested-port
box={'occupied': False, 'runs': [], 'foreign': [], 'ports_in_use': [],
     'queue': [], 'scan_known': True, 'port_scan_known': True}
result={'error': 'active_run_exists'} launcher_calls=0
audit=['port_in_use_foreign']
```

`wait_for_box_s` consulta esa caja y solo espera si `occupied=True` (`tools/dayz_mcp/server.py:3133-3159`). Tras el rechazo, el servidor vuelve a leer la misma caja (`tools/dayz_mcp/server.py:3198-3216`) y `occupancy_error_fields` deja `foreign=False` si no hay run, fila foreign ni `occupied=True` (`tools/dayz_mcp/process_lifecycle.py:373-453`). El cliente recibe así un `active_run_exists` inexplicable. También contradice el contrato público «a DayZ server holding a game port counts as an occupied box» (`tools/dayz_mcp/server.py:3084-3090`; `tools/README-mcp.md:78`).

[DESIGN — criterio propuesto]

```text
Para box_occupancy, considerar foreign a todo holder no registrado de:
- el puerto base solicitado/configurado; y
- el conjunto de puertos reservados que DayZ vaya a usar.

Para la configuración por defecto, modelar explícitamente 2302..2306 hasta
verificar una relación más precisa base/query/Steam. Publicar source="port",
port e image cuando exista, pero nunca PID.
```

Coste: un servicio no-DayZ que use 2302–2306 hará que la caja espere/bloquee. No es un falso positivo para la seguridad del lanzamiento: DayZ competiría por ese puerto. Sí puede ser conservador si algún puerto del rango no se usa realmente; por eso conviene derivar el conjunto de la configuración efectiva y no convertir 2302–2306 en una constante universal. DNS/otros servicios fuera del conjunto seguirían ignorados.

## C-3 — Entrada no confiable (`netstat` y psutil)

### Ataques al parser `netstat`

El parser decodifica bytes con reemplazo (`tools/dayz_mcp/orphan_guard.py:339-342`), separa por espacios y toma el puerto desde el último `:` del endpoint local (`:400-431`). La ejecución adversarial produjo:

[EXACT — salida literal]

```text
normal                 => [(2302, 45428)]
ipv6                   => [(2302, 45428)]
extra_after_pid        => [(2302, None)]
pid_0                  => [(2302, None)]
pid_4                  => [(2302, 4)]
truncated_after_remote => [(2302, None)]
truncated_after_local  => []
bad_port               => []
oem_bytes              => [(2302, 45428)]
```

Valoración:

- OEM/acentos e IPv6 `[::]:2302`: correctos.
- Columnas extra después del PID, PID `0` y truncado después de `*:*`: se pierde atribución, pero **no el holder**; `pid=None` sigue bloqueando el puerto solicitado. PID `4` se conserva y también bloquea.
- Una línea truncada a `UDP 0.0.0.0:2302` tiene menos de tres columnas y desaparece (`tools/dayz_mcp/orphan_guard.py:410-413`). El respaldo considera cualquier `returncode==0` una respuesta válida (`:455-468`) y `snapshot_udp_port_holders` devuelve entonces `known=True` (`:483-504`). Es el falso negativo peligroso.

[EXACT — encadenamiento hasta lifecycle]

```text
netstat stdout=b'UDP    0.0.0.0:2302\r\n', returncode=0
snapshot={'known': True, 'holders': []}
box={'occupied': False, 'foreign': [], 'ports_in_use': [],
     'scan_known': True, 'port_scan_known': True}
start_run: launcher_calls=1
```

No hay prueba de que `subprocess.run(capture_output=True)` trunque normalmente la salida de Windows; el hallazgo es que el parser no puede distinguir «cero sockets» de «fila candidata corrupta/truncada» y certifica ambas como conocidas.

[DESIGN — corrección propuesta]

```text
Hacer que el parser devuelva holders + parse_confident. Si una línea que empieza
por UDP contiene un endpoint local candidato pero carece de las columnas mínimas,
tiene puerto inválido o estructura ambigua, invalidar el snapshot completo:
known=False. No basta con declarar unknown siempre que holders=[] porque una tabla
UDP realmente vacía es válida.
```

### Formas de `laddr` de psutil

La implementación solo lee `getattr(laddr, "port", None)` (`tools/dayz_mcp/orphan_guard.py:443-451`):

[EXACT — salida literal]

```text
namedtuple_like => [(2302, 45428)]
plain_tuple     => []
empty_tuple     => []
```

En la versión **pinneada** hoy (`tools/requirements-mcp.txt:3`, `tools/pyproject.toml:14`), psutil 7.2.2 de Windows transforma todo `laddr` INET no vacío en `ntp.addr(*laddr)` (`tools/.venv-mcp/Lib/site-packages/psutil/_common.py:501-509`) y `_pswindows.net_connections` pasa cada fila por esa conversión (`tools/.venv-mcp/Lib/site-packages/psutil/_pswindows.py:339-359`); `addr` define campos `ip, port` (`tools/.venv-mcp/Lib/site-packages/psutil/_ntuples.py:129-133`). Por tanto, la tupla plana no es una forma productiva de esta versión. `()` representa ausencia de endpoint local y no acredita un bind.

Como endurecimiento futuro, reutilizar la lógica ya existente que acepta `.port` o `tuple[1]` (`tools/dayz_mcp/dayz_test_readiness.py:96-103`). Lo clasifico BACKLOG, no MAJOR actual.

Los tests actuales ejercitan OEM/IPv6/PID no atribuible (`tools/tests/test_orphan_guard_udp.py:11-31`) y un `SimpleNamespace` con `.port` (`:35-57`), pero no fila truncada tras el endpoint ni `laddr` tupla.

## C-4 — Fail-closed real y construcciones de producción

### Todos los retornos de `_port_holders`

| Retorno | Línea | Clasificación |
|---|---:|---|
| `port_probe is None -> (None, [])` | `tools/dayz_mcp/process_lifecycle.py:3224-3225` | **ABRE**: feature ausente/legado. |
| probe lanza excepción -> `("port_scan_unknown", None)` | `:3226-3229` | **CIERRA**. |
| resultado no-dict o `known is not True` | `:3230-3231` | **CIERRA**. |
| `holders` no-list | `:3232-3234` | **CIERRA**. |
| holder no-dict | `:3236-3238` | **CIERRA**. |
| puerto no-int/bool/fuera de rango | `:3239-3245` | **CIERRA**. |
| PID no nulo inválido/bool/≤0 | `:3246-3250` | **CIERRA**. |
| éxito -> `(None, observed)` | `:3251-3259` | **ABRE o CIERRA según contenido**; nombre no-string se normaliza a `None`. |

No hay ningún `except Exception` aquí que devuelva `known=True` o lista vacía; la excepción cierra. En la adquisición, fallo de psutil cae a `netstat` (`tools/dayz_mcp/orphan_guard.py:435-468`) y ausencia de ambas fuentes devuelve `known=False` (`:483-487`). La excepción peligrosa semántica es el `netstat` malformado con exit 0 descrito en C-3, no un `except`.

### Todos los retornos de `_foreign_port_reason`

| Retorno | Línea | Clasificación |
|---|---:|---|
| scan desconocido -> razón/`port_scan_unknown` | `tools/dayz_mcp/process_lifecycle.py:3274-3276` | **CIERRA**. |
| PID registrado -> `continue` | `:3277-3280` | **ABRE para esa fila**, correcto si el registro representa realmente el proceso propio. |
| imagen DayZ en cualquier puerto o cualquier imagen en puerto solicitado -> `port_in_use_foreign` | `:3281-3284` | **CIERRA**. |
| fin sin match -> `None` | `:3285` | **ABRE**. |

### Censo de constructores

[EXACT — comando y salida]

```text
rg -n "ProcessLifecycle\(" tools/dayz_mcp -g "*.py"
0 coincidencias literales

rg -n "\bProcessLifecycle\b" tools/dayz_mcp -g "*.py"
tools/dayz_mcp/daemon.py:520:        ProcessLifecycle,
...tipos/documentación y usos estáticos; ningún otro constructor...
```

La construcción real es indirecta: `bounded_io` llama al callable recibido (`tools/dayz_mcp/daemon.py:382-386`) y en `:519-531` recibe `ProcessLifecycle` **con** `port_probe=orphan_guard.snapshot_udp_port_holders`. Esa activación se ejecuta al arrancar el daemon real (`tools/dayz_mcp/daemon.py:904-907`). `dayz_test_worker.py` y `lifecycle_cli.py` son clientes del broker; no construyen lifecycle. No encontré un camino de producción con el sondeo apagado.

El default `port_probe=None` sigue siendo un footgun fail-open para futuros constructores y está preservado deliberadamente por el test `tools/tests/test_box_port_occupancy.py:302-311`; al no existir hoy en producción, queda BACKLOG, no MAJOR.

## C-5 — Ficha hermana fb-20260904-022558-cabd

### Si el lote ajeno arranca después

La protección dura solo en la admisión de un lanzamiento MCP: `_foreign_port_reason` se invoca en `start_run` (`tools/dayz_mcp/process_lifecycle.py:1913-1922`) y no existe otro llamador productivo. `session_status` sí compone una caja nueva bajo demanda (`tools/dayz_mcp/loopback.py:3308-3325`), con caché máxima de 1,5 s para lectores (`tools/dayz_mcp/process_lifecycle.py:3440-3458`).

- Si después aparece un `DayZDiag_x64.exe` ajeno, la siguiente lectura de `session_status.box` lo mostrará por nombre incluso antes del bind.
- Si aparece `DayZServer_x64.exe`, lo mostrará después del bind y de que ToolHelp aporte el nombre.
- Si aparece renombrado, no aparecerá en `foreign`; con un run propio vivo la caja seguirá `occupied=True` por el run, pero no explicará que hay dos holders.

[EXACT — reproducción con run propio y segundo holder]

```text
later-standard-foreign:
  occupied=True
  runs=[run-existing]
  foreign=[{port:2302, image:DayZServer_x64.exe, source:port}]
  ports_in_use=[2302]

later-renamed-foreign:
  occupied=True
  runs=[run-existing]
  foreign=[]
  ports_in_use=[2302]
```

No hay monitor continuo, alerta ni acción correctiva. El MCP tampoco debe matar el proceso ajeno. Por ello el lote garantiza solamente:

> En el instante de su sondeo fresco, el MCP rechaza su propio launch si el holder ajeno ya es observable por nombre o sostiene el puerto solicitado.

No garantiza exclusión mutua si `run_batch_f1.ps1` arranca después, ni durante el pre-bind, ni en la carrera entre el sondeo y `self.launcher`. En consecuencia, la ficha hermana **no queda resuelta bidireccionalmente**.

[DESIGN — resolución propuesta para la ficha hermana]

```text
Primario: hacer que run_batch_f1.ps1 participe en el mismo lease/box o, como
mínimo, ejecute su propio preflight fail-closed sobre 2302..2306 y aborte ante
cualquier holder. Complementario: exponer en session_status un estado explícito
port_conflict cuando un puerto de un run propio tiene otro PID; nunca matar el
PID ajeno. Si se desea recuperación automática, retirar solo el proceso MCP
recién lanzado o el propio, con identidad verificada.
```

## HALLAZGOS

### M-1 — MAJOR — ventana pre-bind + carrera check→launch

- **Secuencia exacta:** proceso ajeno `DayZServer_x64.exe` existe sin UDP → `diag_probe` no lo enumera → `port_probe` responde conocido/vacío → `_foreign_port_reason` abre → transcurren commit/manifest/preparación → `self.launcher` es invocado. La misma secuencia vale para una imagen renombrada; también basta con que el ajeno aparezca después del sondeo y antes del launcher.
- **Repro [EXACT]:** el caso pre-bind devolvió `RUNNING` y `launcher_calls=1` para un request `role=server`; el mutante temporal devolvió también `RUNNING` con `port_probe_calls=1` aunque al entrar al launcher ya existía el holder ajeno. El código que abre y después lanza está en `tools/dayz_mcp/process_lifecycle.py:1913-1922` y `:1933-2024`.
- **Impacto:** el MCP puede iniciar su servidor durante la ventana ajena; se conserva el riesgo de colisión/pérdida de estado que motivó el lote.
- **Fix [DESIGN]:** ampliar `diag_probe` a `DayZServer_x64.exe`, repetir el scan inmediatamente pre-launch y verificar owner post-launch retirando solo el proceso propio ante conflicto; coordinación común para cubrir renombrados sin bind.

### M-2 — MAJOR — caja pública libre mientras la admisión rechaza el mismo holder

- **Secuencia exacta:** `renamed.exe` sostiene 2302 → `_collect_probes` lo ignora → `box.occupied=False` y `wait_for_box_s` no espera → `start_run` hace scan fresco y rechaza → el enriquecimiento relee una caja libre → respuesta `active_run_exists`, `foreign=False`, sin puerto.
- **Repro [EXACT]:** salida de C-2; `launcher_calls=0`, audit `port_in_use_foreign`, caja libre.
- **Impacto:** degradación funcional/diagnóstica; el cliente no puede explicar ni esperar correctamente un conflicto real.
- **Fix [DESIGN]:** incluir todo holder no registrado de los puertos reservados, con fila pública por puerto aunque el nombre sea desconocido.

### M-3 — MAJOR — `netstat` truncado puede convertirse en `known=True, holders=[]`

- **Secuencia exacta:** psutil ausente/falla → `netstat` sale 0 → una fila queda `UDP 0.0.0.0:2302` → parser la omite → snapshot certifica vacío → lifecycle abre → launcher invocado.
- **Repro [EXACT]:** salida de C-3 con `snapshot={'known': True, 'holders': []}` y `launcher_calls=1`.
- **Impacto:** falso negativo fail-open en la única fuente de respaldo. No se observó truncamiento real del comando en este host.
- **Fix [DESIGN]:** separar resultado de parsing y confianza estructural; cualquier fila UDP candidata malformada vuelve todo el scan `known=False`.

### M-4 — MAJOR — exclusión no persistente; la ficha hermana sigue abierta si el ajeno arranca segundo

- **Secuencia exacta:** run MCP ya vive → lanzador externo inicia/bindea después → no hay watchdog → solo una futura llamada a `session_status` vuelve a sondear → proceso estándar se muestra, renombrado no; ninguno se detiene.
- **Repro [EXACT]:** salida `later-standard-foreign` / `later-renamed-foreign` de C-5 y censo de llamadores: `_foreign_port_reason` solo en `start_run`.
- **Impacto:** ambos servidores pueden quedar solapados; el lote evita solo que el MCP sea el segundo lanzador observable, no que el batch ajeno lo sea.
- **Fix [DESIGN]:** coordinar `run_batch_f1.ps1` con el box/lease; preflight fail-closed en el batch y estado público explícito de conflicto post-launch.

### B-1 — BACKLOG — `_udp_holders_via_psutil` no acepta `laddr` tupla

- **Secuencia exacta:** una implementación futura entrega `('0.0.0.0', 2302)` → `getattr(..., 'port')` da `None` → fila omitida → posible conocido/vacío.
- **Repro [EXACT]:** `plain_tuple => []`.
- **Por qué no MAJOR actual:** psutil 7.2.2 Windows pinneado convierte el endpoint no vacío a `addr(ip, port)` antes de devolverlo.
- **Fix [DESIGN]:** reutilizar `_endpoint_port` y aceptar `.port` o `tuple[1]`.

### B-2 — BACKLOG — `port_probe=None` mantiene fail-open por defecto

- **Secuencia exacta:** un constructor futuro omite el argumento → `_port_holders` devuelve éxito vacío → no existe protección UDP.
- **Sin repro productivo → BACKLOG:** el único constructor real del daemon sí lo pasa; los otros constructores hallados son tests/restore.
- **Fix [DESIGN]:** cuando termine la compatibilidad transitoria, hacer obligatorio `port_probe` o tratar `None` como `port_scan_unknown` en perfiles productivos.

## Evidencia de tests

[EXACT — comando focal]

```text
cd tools
$env:PYTHONPATH='.'
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv-mcp\Scripts\python.exe -m unittest -v \
  tests.test_orphan_guard_udp tests.test_box_port_occupancy

Ran 23 tests in 0.055s
OK
```

Los tests verdes prueban el camino nominal y los fallos explícitos de fuente/shape, no refutan M-1..M-4. En particular, el test vivo solo exige que la tabla sea conocida y no vacía (`tools/tests/test_orphan_guard_udp.py:83-90`); no observa el intervalo proceso→bind.

## LO QUE NO PUDE VERIFICAR

- El instante exacto del primer bind UDP desde t=0 ni una demora real de 30–60 s. El PID 45428 ya había terminado; el siguiente arranque solo permitió acotar el bind a ≤12,235 s, y el monitor posterior no vio otro proceso nuevo en 180 s.
- Una colisión real con un `.exe` copiado/renombrado o con `run_batch_f1.ps1`: las fronteras prohibían lanzar DayZ, daemon o tráfico de red. Las reproducciones son deterministas y aisladas con probes/launcher falsos.
- El comportamiento interno del engine ante dos procesos que intentan el mismo puerto (rechazo limpio, coexistencia por opciones de socket o caída posterior). El riesgo auditado es anterior: el MCP sí llega a invocar el launch.
- No repetí los 2595 tests completos; usé el resultado entregado en `SUITE-D.txt:1-8` y ejecuté los 23 tests focales.
- No actualicé memoria durable en Obsidian: estaba fuera de las raíces de escritura y el entregable autorizado era este archivo. No se guardaron secretos ni transcripciones.
