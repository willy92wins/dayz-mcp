<!-- Salida CRUDA de una lane Qwen3.8-Flash-Next (GX10, ronda 4, contexto completo), sin editar. NO es el informe: puede contener errores.
     Verificación mecánica del receptor (EVID literal a ±10 líneas en HEAD 57269d2): == W4c2_host_security.md findings: 12 {'EXACT': 12}
     Veredicto del receptor: ver ../INFORME.md §4 y §8. -->



## Resumen (máximo 8 líneas)
En el modelo de 127.0.0.1 con un solo usuario, el núcleo justificable es la autoridad mínima de daemon: contrato de política normal, keyfile pinneado, credencial y transporte acreditado.  
El transporte acreditado evita enviar la API key a un daemon suplantado; el keyfile pinneado evita leer un secreto vía symlink/hardlink.  
La autoridad de rutas protege bien a `dayz_test_run`, pero puede ir tras el extra porque no pertenece al camino básico de instalar addon + `bridge_status`.  
La carga bootstrap, la migración de identidad y la auditoría de seguridad en runtime son caras y parecen más adecuadas como opcional, legacy o verificación de desarrollo.  
La extracción debería dejar en el núcleo una interfaz de cliente daemon normal, sin importar launcher, bundle, registro o supervisión de procesos nativos.  
`dayz_test_run` sin extra debe fallar de forma tipada si necesita crear procesos o sellar rutas; puede seguir leyendo estado si ya hay un daemon acreditado.  
No conviene proponer quitar 127.0.0.1, API key ni validación básica de política.

## A. Mapa

| Módulo | Qué hace en este material | Dependencias visibles | Uso aparente | Líneas clave |
|---|---|---:|---:|---:|
| `security_runtime_audit.py` | Auditoría AST de sinks HTTP y de superficies de creación de procesos; mantiene allowlists por owner/función. | `ast`, `re`, `pathlib` | [INFERENCIA] herramienta de desarrollo o CI; no se ve importador de producción. | `tools/dayz_mcp/security_runtime_audit.py:1239`; `tools/dayz_mcp/security_runtime_audit.py:1615` |
| `identity_migration.py` | Transacción de backup de `runs` v1 a v2 con locks, quiescencia, scan de procesos, handles Win32 y receipts. | `ctypes`, `msvcrt`, `psutil`, `native_process_guard`, `orphan_guard`, `runtime_state`, `server_cli` | [INFERENCIA] arranque/migración del daemon para evitar corrupción del estado antiguo. | `tools/dayz_mcp/identity_migration.py:1255`; `tools/dayz_mcp/identity_migration.py:1317` |
| `daemon_policy.py` | Carga política de daemon normal o bootstrap; valida handle heredado, manifest, liveness pipe y revalidación. | `bootstrap_parent`, `host_config`, `daemon_contract`, `daemon_policy_contract` | [INFERENCIA] CLI/worker que necesita autoridad del daemon, especialmente bootstrap. | `tools/dayz_mcp/daemon_policy.py:369`; `tools/dayz_mcp/daemon_policy.py:390` |
| `normal_daemon_policy.py` | Carga/política normal sin importar ciclos de vida; serializa e ingiere política heredada por env de forma acotada. | `host_config`, `daemon_policy_contract` | [INFERENCIA] sesiones normales que no deben arrastrar imports de launcher. | `tools/dayz_mcp/normal_daemon_policy.py:79`; `tools/dayz_mcp/normal_daemon_policy.py:158` |
| `daemon_policy_contract.py` | Valor inmutable `AccreditedDaemonPolicy` con validación de host, puerto, rutas, argv, sha256 y revalidación. | `daemon_contract` | Base compartida por políticas y credenciales. | `tools/dayz_mcp/daemon_policy_contract.py:88`; `tools/dayz_mcp/daemon_policy_contract.py:132` |
| `request_path_authority.py` | Pinna y acredita raíces de filesystem para requests de `dayz-test`; bloquea salidas, reparse y drives remotos. | `dayz_test_request`, `win32_fileinfo`, `ctypes`, `ntpath` | [INFERENCIA] validación de paths en `dayz_test_run`. | `tools/dayz_mcp/request_path_authority.py:523`; `tools/dayz_mcp/request_path_authority.py:420` |
| `pinned_keyfile.py` | Lee la API key de forma acotada y local; rechaza symlink, hardlink, BOM, remoto y tamaños inválidos. | `win32_fileinfo`, `ctypes`, `ntpath` | Usado por `daemon_credential.py`. | `tools/dayz_mcp/pinned_keyfile.py:122`; `tools/dayz_mcp/pinned_keyfile.py:157` |
| `daemon_credential.py` | Gestiona una credencial refrescable para una autoridad inmutable; reintenta 401 y reacreedita ante daemon reemplazado. | `accredited_daemon_transport`, `pinned_keyfile`, `daemon_policy_contract` | [INFERENCIA] clientes de requests a daemon. | `tools/dayz_mcp/daemon_credential.py:246`; `tools/dayz_mcp/daemon_credential.py:362` |
| `accredited_daemon_transport.py` | Envía HTTP autenticado solo tras acreditar el owner PID del socket loopback y su identidad. | `native_process_guard`, `native_process_snapshot`, `daemon_contract`, `psutil`, `http.client` | Usado por `daemon_credential.py`. | `tools/dayz_mcp/accredited_daemon_transport.py:227`; `tools/dayz_mcp/accredited_daemon_transport.py:282` |

## B. Veredicto por capa

| Capa / módulos visibles | Escenario de amenaza | ¿Aplica a 127.0.0.1 + un solo usuario? | Coste de mantenimiento | Qué se pierde si se simplifica/quita | Veredicto |
|---|---|---|---:|---|---|
| **Pinning de keyfile** `pinned_keyfile.py` | Otro proceso local del mismo usuario, un bug de configuración o una ruta maliciosamente sustituida convierte el keyfile en symlink/hardlink a otro secreto o archivo sensible. | **Parcial**. No hay multiusuario, pero sí coexisten procesos, agentes, editores y errores humanos. | Bajo/medio, pero muy Windows-específico. | Lectura ingenua del keyfile; riesgo de leer pipe, symlink o archivo con enlaces extraños. | **MANTENER** |
| **Transporte acreditado + credencial** `accredited_daemon_transport.py`, `daemon_credential.py` | Un proceso local o un daemon antiguo/reemplazado escucha en 127.0.0.1:port, acepta la API key y responde datos/acciones falsas; o el cliente envía a un daemon distinto al esperado. | **Sí**. En un mismo usuario, otro proceso puede suplantar el puerto; la API key por sí sola prueba conocimiento del secreto, no identidad de proceso. | Alto: psutil, Win32 identity, snapshots, timeouts, reintentos, errores sanitizados. | Envío de la API key a cualquier listener loopback; sin reacreeditación tras sustitución de daemon. | **MANTENER** |
| **Política del daemon - contrato** `daemon_policy_contract.py` | Cliente o worker construye host/puerto/ruta/argv inválidos y se apunta a un daemon incoherente o a una política mutable corrupta. | **Parcial**. Más corrección y defensa en profundidad que ataque externo. | Bajo/medio. | Menor coherencia en host, puerto, rutas, argv y hash de autoridad. | **MANTENER** |
| **Política normal + bootstrap** `normal_daemon_policy.py`, parte bootstrap de `daemon_policy.py` | Un bootstrap pasa un manifest o liveness pipe incorrectos; una sesión normal importa accidentalmente lógica de launcher o recibe política heredada no normal. | **Parcial** para normal; **sí pero solo si bootstrap existe** para p0s. | Normal: medio. Bootstrap: alto por handles, manifest y revalidación. | Normal: menos aislamiento de sesiones normales. Bootstrap: bootstrap no podría verificar su autoridad heredada. | **MANTENER normal; OPCIONAL bootstrap** |
| **Autoridad de rutas** `request_path_authority.py` | Un request de agente usa `..`, junctions, mounts, UNC o rutas remotas para leer/sobrescribir fuera de raíces autorizadas durante `dayz_test_run`. | **Sí** si `dayz_test_run` puede recibir paths de un agente. Para camino básico sin ese tool, no aplica. | Alto: Win32, reparse, identity, handles, apertura de jerarquías. | `dayz_test_run` pierde límite real de filesystem y pasa a depender de confianza en el input. | **OPCIONAL (MANTENER dentro del extra)** |
| **Auditoría de seguridad en runtime** `security_runtime_audit.py` | Un futuro commit añade sinks HTTP no autenticados, `urlopen` con query de API key, spawns fuera del boundary o bypass del launcher. | **No** como runtime de usuario; **sí** como amenaza de desarrollo para un repo grande con agentes. | Muy alto: dos visitors AST, allowlists, gramáticas, contract checks. | Guarda automática de regresión en sinks HTTP y creación de procesos. | **SIMPLIFICAR / dev-only** |
| **Migración de identidad / backup v1→v2** `identity_migration.py` | Durante upgrade, un daemon v1 y uno v2 coexisten, `runs` cambia mientras se copia, o la identidad de proceso antigua queda ambigua. | **Parcial/temporal**. Solo aplica a instalaciones con datos v1 o migración activa. | Muy alto: transacciones, locks, handles, scan de procesos, receipt, recovery. | Backup/migración atómico y quiescencia antes de escribir el estado v2. | **OPCIONAL (retirable si se abandona v1)** |
| **Guardia de procesos nativos, bundle, registro, transacciones de instalación, secure_launcher** | No están completos en este material. La auditoría runtime los referencia estáticamente, pero no puedo juzgar su justificación sin verlos. | No determinable con este material. | No determinable. | No determinable. | **Reservado a la otra lane** |

## C. Extracción

Frontera recomendada:

- **Núcleo mínimo**
  - Política normal del daemon: `AccreditedDaemonPolicy` con host fijo `127.0.0.1`, puerto, keyfile, executable, argv, cwd y hash de autoridad.
  - Lectura segura del keyfile con `pinned_keyfile.read_pinned_keyfile`.
  - Cliente de daemon con reintentos 401/reacreeditación mediante `RefreshingDaemonCredential`.
  - Transporte acreditado para no mandar la API key a un socket cuyo owner no sea el daemon esperado.
  - Herramientas básicas que no crean procesos del juego ni piden `dayz_test_run` con paths sensibles.

- **Extra `dayz-mcp[launcher]`**
  - Launcher nativo, bundle, registro, transacciones de instalación, `secure_launcher`, supervisión de procesos y guardia de procesos nativos, según otra lane.
  - Carga bootstrap de `daemon_policy.py` si solo la necesita el arranque nativo/worker con handle heredado.
  - `request_path_authority.py` cuando exista una tool que pueda recibir paths de proyecto, mods, misiones o fuentes.
  - `identity_migration.py` si el producto todavía soporta migración desde v1.
  - `security_runtime_audit.py` como comando de desarrollo/CI, no como dependencia de ejecución básica.

Interfaz mínima que vería el núcleo:

```python
class DaemonClient(Protocol):
    def request(
        self,
        *,
        method: str,
        path: str,
        query: dict[str, str] | None = None,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        deadline: float,
    ) -> tuple[int, bytes]: ...

def get_normal_daemon_policy() -> AccreditedDaemonPolicy | None: ...

def make_daemon_client(
    policy: AccreditedDaemonPolicy,
) -> DaemonClient: ...
```

Condiciones:

1. El núcleo no debe importar `native_launcher_backend`, `secure_launcher`, bundle/registro ni `identity_migration`.
2. El núcleo no necesita conocer sha256 de launchers aprobados ni transacciones de instalación.
3. El núcleo puede pedir al extra, si instalado, un sello de paths antes de una tool de creación de proceso.
4. La otra mitad debería exponer, en su lane, una interfaz mínima tipo:
   - `LauncherController` para `start`/`stop`/`health` de launchers aprobados.
   - `PathPolicy` para verificar que un request no se sale de raíces autorizadas.
   - `ProcessSupervision` para devolver estados, no handles crudos al núcleo.

Comportamiento de `dayz_test_run` sin extra:

1. Si la request solo consulta estado y hay un daemon normal acreditado, puede responder a través de `DaemonClient`.
2. Si la request implica crear un launcher, spawnear DayZDiag, pinchar raíces de paths o usar `exec_enforce`, debe devolver error tipado, por ejemplo:
   - `launcher_extra_required`
   - `request_path_authority_not_available`
   - `bootstrap_policy_not_available`
3. No debería importar perezosamente los módulos del extra para “intentar igualmente” si no están instalados; eso reintroduce el coste y los fallos parciales.
4. Un fallback a “DayZDiag directo” sin extra no es recomendable para el modelo de seguridad actual, porque implicaría recrear parte del sello de políticas/rutas/procesos en el camino básico.

Pasos ejecutables:

1. Definir un módulo o paquete de acceso a cliente daemon con la interfaz `DaemonClient` y política normal.
2. Quitar de los imports core cualquier dependencia de bootstrap, launcher, bundle, registro y transacciones.
3. Mover la rama bootstrap de `load_daemon_policy` a un entrypoint del extra `launcher`.
4. Hacer que `request_path_authority` se importe solo desde tools de creación/validación de paths para `dayz_test_run`.
5. Colocar `identity_migration` bajo un comando o feature gate de migración legacy, no en startup esencial si ya no hay usuarios v1.
6. Mover `security_runtime_audit` a script o comando de desarrollo/CI con entrada explícita, no a dependencia de arranque.
7. Añadir dependencia optativa `launcher` que arrastre los módulos nativos y sus dependencias.
8. Verificar con una instalación limpia:
   - `pip install .` + arranque normal + `bridge_status`
   - sin extra, `dayz_test_run` devuelve error tipado si necesita spawn/pinning
   - con extra, las tools de launcher quedan disponibles
9. Verificar con un check de imports que ninguna tool básica importa módulos del extra.

## D. Complejidad

No tengo a la vista los cuerpos de `_supervise_created_launcher` y `load_verified_bundle`; por tanto no puedo asignar responsabilidades verificadas a sus líneas internas. Lo siguiente es una plantilla de refactor con base en los nombres, el contexto verificado y el material disponible, marcada como inferencia.

Para `_supervise_created_launcher`:

| Pieza propuesta | Recibe | Devuelve | Justificación |
|---|---|---:|---|
| `_wait_for_listener_or_exit()` | Identidad de proceso, puerto, deadline | Estado: listener pronto, proceso muerto, timeout | [INFERENCIA] separar espera de red del resto de supervisión facilita testear timeouts sin Win32 real. |
| `_snapshot_launcher_identity()` | PID del launcher, política esperada | Snapshot validado de identidad | [INFERENCIA] alinear con `NativeProcessGuard.snapshot()` y con la idea de identidad PID+executable+argv+cwd visible en transporte. |
| `_classify_startup_failure()` | Estado de proceso, listener, error HTTP/timeout | Causal de fallo tipado | [INFERENCIA] evita mezclar causas: proceso muerto, puerto no listo, identidad incorrecta, timeout. |
| `_cleanup_or_keep_on_failure()` | Estado, recursos abiertos, política | Resultado de limpieza | [INFERENCIA] separar side effects de destrucción de recursos; facilita auditoría de leaks. |

Para `load_verified_bundle`:

| Pieza propuesta | Recibe | Devuelve | Justificación |
|---|---|---:|---|
| `_read_bundle_manifest()` | Root del bundle, límite de tamaño | Manifest canónico crudo | [INFERENCIA] separar lectura/acotación de verificación criptográfica. |
| `_verify_bundle_hashes()` | Manifest, rutas de artefactos | Recibo de verificación o error | [INFERENCIA] si hay registro sha256, la verificación debería devolver estructura, no solo lanzar excepción. |
| `_build_bundle_receipt()` | Manifest + hashes verificados | Receipt immutable | [INFERENCIA] separa estado verificable de carga posterior. |
| `_load_bundle_files()` | Receipt, límites de tamaño/tipos | Datos cargados o error tipado | [INFERENCIA] separa carga costosa de la validación de sello. |

Recomendación concreta: la otra lane debería mover funciones de 200/400 líneas a estas cuatro piezas nombradas antes de añadir más comportamiento, porque las funciones largas mezclan lectura, verificación, supervisión, side effects y errores.

## E. Propuestas priorizadas

| Prioridad | Propuesta | Coste | Riesgo | Verificación |
|---:|---|---:|---|---|
| 1 | Dejar en el núcleo solo política normal, credential, keyfile y transporte; mover bootstrap y launcher a extra. | M | Puede romper instalaciones que dependan de bootstrap en arranque normal. | Instalación limpia sin extra: `bridge_status` funciona; `dayz_test_run` que requiere spawn devuelve error tipado. |
| 2 | Mantener transporte acreditado como requisito antes de enviar la API key. | S | Bajo si ya existe; alto si se toca a la baja. | Test de integración: un segundo listener en loopback no debe recibir la key; solo el daemon acreditado responde. |
| 3 | Mantener `pinned_keyfile` pero mover cualquier lógica CLI/registro a otro módulo si existe. | S | Bajo. | Test de keyfile symlink/hardlink/BOM/remote y revisión de imports. |
| 4 | Convertir `identity_migration` en feature gate o comando de migración legacy. | M | Puede romper upgrades desde v1 si se apaga sin aviso. | Test de arranque sin migrar con flag desactivado; test de migración v1→v2 con flag activado. |
| 5 | Mover `request_path_authority` a importación perezosa o extra para tools de paths. | M | Una tool básica podría empezar a fallar si necesita paths sin aviso claro. | Check de imports para camino básico; smoke de tool de path sin extra con error esperado. |
| 6 | Convertir `security_runtime_audit` en comando de desarrollo/CI, no dependencia de runtime. | M | Menos protección si no se ejecuta en CI. | CI fallida al añadir un sink HTTP no autorizado o un spawn fuera del boundary permitido. |
| 7 | Extraer una fachada `DaemonClient` para ocultar transporte y credential a server/tools. | M | Refactor de varias callsites. | Que ninguna tool básica importe `http.client`, `ctypes` o transporte interno directamente. |
| 8 | Reemplazar validaciones duplicadas de path/UTF-16/NFC/remote-drive en policy, keyfile y authority por un helper interno único. | S/M | Diferencias sutiles de semántica pueden cambiar errores. | Tests paramétricos que cubran NFC, surrogates, NUL, remoto, UNC y longitud en los tres contextos. |

## F. Valoración

| Dimensión | Nota | Motivos |
|---|---:|---|
| **Proporcionalidad** | 6/10 | El transporte acreditado y el pinning de keyfile tienen una amenaza local concreta: suplantar loopback o leer keyfile malicioso. Pero bootstrap, auditoría AST y migración v1 parecen sobredimensionados para una herramienta local si no hay un usuario o un flujo que los necesite hoy. |
| **Corrección** | 8/10 | Se ven varias decisiones defensivas correctas: host fijo, validación de socket owner, revalidación de autoridad, checks de hardlink/reparse, y transacciones con recovery. Nota no mayor porque no puedo ejecutar ni ver tests reales. |
| **Legibilidad** | 5/10 | `security_runtime_audit.py` es especialmente difícil: AST visitor con scopes, alias, text bindings, allowlists y gramática nominal de probes. Otros módulos también mezclan Win32, hashing, locks y lógica de negocio. |
| **Testabilidad** | 7/10 | Hay seams útiles: funciones con `psutil_module`, `guard`, `scan_fn`, `listener_fn`, `fault_injector`. Pero gran parte depende de kernel32, handles, locks de archivos y procesos reales, lo cual exige mocks muy específicos o VM Windows. |
| **Portabilidad** | 2/10 | Fuerte dependencia de Windows: `msvcrt`, `ctypes.WinDLL`, `ntpath`, paths con drive letter, `SO_EXCLUSIVEADDRUSE`, reparse tags, mount points y handles de archivo/pipe. |

## Hallazgos

F01 | P1 | tools/dayz_mcp/accredited_daemon_transport.py:227 | seguridad | El transporte declara que solo envía HTTP autenticado tras acreditar el socket conectado | EVID: """Send authenticated HTTP only after accrediting this connected socket.""" | FIX: Dejar esa función como única salida HTTP autenticada del núcleo
F02 | P2 | tools/dayz_mcp/accredited_daemon_transport.py:179 | seguridad | La acreditación relee el PID del socket para reducir ventanas de race en identidad | EVID: pid_b = _connected_server_pid(sock, connections_fn=connections_fn) | FIX: Mantener testable con fixture de socket + net_connections
F03 | P1 | tools/dayz_mcp/pinned_keyfile.py:157 | seguridad | El keyfile pinneado rechaza hardlinks para reducir lectura de secretos ajenos | EVID: standard.NumberOfLinks != 1 | FIX: Conservar este check aunque se simplifiquen otros módulos
F04 | P2 | tools/dayz_mcp/pinned_keyfile.py:87 | seguridad | El keyfile pinneado rechaza parents con reparse point | EVID: raise ValueError("invalid_daemon_keyfile") | FIX: Documentar en QUICKSTART que symlink parents no están soportados
F05 | P1 | tools/dayz_mcp/request_path_authority.py:420 | seguridad | La autoridad de rutas obliga a que un path request esté contenido en una raíz sellada | EVID: if not _contains(canonical, root.path): | FIX: Mantener como default si dayz_test_run acepta paths
F06 | P2 | tools/dayz_mcp/request_path_authority.py:440 | seguridad | Se valida el final handle path de cada segmento descendiente | EVID: if not _same_path(item.final_path, expected): | FIX: Añadir tests de junctions, mount points y traversal
F07 | P1 | tools/dayz_mcp/daemon_policy.py:223 | seguridad | Bootstrap exige un handle heredado válido y con tipo esperado | EVID: raise ValueError("invalid_bootstrap_policy_handle") | FIX: Mover rama bootstrap a extra y no importarla en núcleo
F08 | P3 | tools/dayz_mcp/normal_daemon_policy.py:74 | seguridad | La política normal heredada por env está acotada a 8191 caracteres sin NUL | EVID: if not 1 <= len(serialized) <= 8191 or | FIX: Mantener límite; documentar si puede crecer con argv
F09 | P2 | tools/dayz_mcp/security_runtime_audit.py:1239 | seguridad | La auditoría de procesos se centra en native_launcher_backend y secure_launcher | EVID: "dayz_mcp/native_launcher_backend.py", | FIX: Convertirlo en verificación CI con paths explícitos
F10 | P3 | tools/dayz_mcp/security_runtime_audit.py:81 | arquitectura | La auditoría HTTP usa un conjunto de roots productivos acotado | EVID: "dayz_mcp.lifecycle_cli", | FIX: Mantener roots estrechos; no ampliar a todo el repo
F11 | P2 | tools/dayz_mcp/identity_migration.py:747 | seguridad | La migración fail-closed si el scan de procesos es incompleto | EVID: raise RunsBackupGateError("process_scan_incomplete") | FIX: Documentar riesgo de migración bloqueada en máquinas ruidosas
F12 | P3 | tools/dayz_mcp/identity_migration.py:349 | seguridad | La elección de arranque usa un lock de arranque de daemon exclusivo y Windows-only | EVID: raise RunsBackupGateError("daemon_startup_lock_unavailable") | FIX: Hacer este camino opcional o legacy para arranque normal

## LO QUE NO PUDE VERIFICAR
- No vi `native_launcher_backend.py`, `native_bundle.py`, `secure_launcher.py`, registro de launchers ni transacciones de instalación; por eso no puedo dar veredicto de esas capas.
- No vi los cuerpos de `_supervise_created_launcher` ni `load_verified_bundle`; no puedo afirmar qué hacen internamente.
- No tengo pruebas de ejecución ni cobertura de tests; la valoración de corrección es por lectura, no por verificación dinámica.
- No tengo el grafo completo de imports desde `server.py`, CLI o tools FastMCP; el mapa de consumidores es inferencia a partir de dependencias visibles y contexto del encargo.
- No puedo confirmar si `dayz_test_run` usa realmente `request_path_authority` en runtime; es inferencia fuerte por nombres y material visible.
- No puedo confirmar si la auditoría runtime se ejecuta en CI; no hay workflow ni script de entrada en el material.
- No puedo medir impacto de rendimiento de abrir handles de jerarquías completas, psutil net_connections o scans de procesos en máquinas reales.
- No puedo determinar si hay usuarios de migración v1 que obliguen a mantener `identity_migration` en arranque esencial.
- No puedo verificar si bootstrap policy es necesario para la sesión normal o solo para workers nativos con handles heredados.
- No puedo confirmar el tamaño exacto de cada módulo por métricas AST; solo usé el material y los hechos verificados del receptor para el total y funciones largas.

## ¿Qué puede estar mal en la premisa de este encargo?
- Si el modelo de amenaza es realmente “una persona, local, dev”, parte de la seguridad de host puede ser sobredimensionada: el riesgo más frecuente no es un atacante local sofisticado, sino un agente que pide rutas equivocadas o un daemon reemplazado por un reinicio manual.
- Puede que el cuello de botella no sea quitar código, sino definir una frontera clara: núcleo = hablar con daemon; extra = arrancar juego, pinchar paths, supervisar procesos y sellar bundle.
- Si no hay flujo v1 actual, la migración de identidad es deuda viva que debería apagarse o retirarse.
- Si la auditoría AST es una solución a falta de límites de arquitectura, conviene tratarla como herramienta de transición, no como parte permanente del runtime.
- Puede que el verdadero valor de la autoridad de rutas no sea proteger de un atacante local, sino contener a agentes LLM que escriben paths plausibles pero incorrectos.
- La presencia de handles heredados, manifests y liveness pipes sugiere un sistema de arranque nativo complejo; si ese arranque no es necesario para el camino básico, debería ser claramente optativo y no mezclado con la política normal.

GATE NO CORRIDO: revisión por API sin herramientas.