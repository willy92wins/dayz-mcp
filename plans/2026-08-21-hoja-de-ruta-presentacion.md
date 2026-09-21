# Hoja de ruta — de repo público a producto presentable

> Deriva del council del 2026-08-21
> ([`reviews/2026-08-21-council-producto-presentable.md`](../reviews/2026-08-21-council-producto-presentable.md)).
> Decisión del usuario: **sin fecha**. Se presenta cuando pase el gate de abajo, no antes.

## El principio que ordena todo lo demás

Este público no evalúa por acumulación, evalúa por **la peor cosa que encuentra**. Cincuenta
y tres tools no compensan una clase muerta en el módulo de seguridad; 1.770 tests en verde
no compensan un README que se contradice consigo mismo en la misma línea. La asimetría es
brutal: a ti te hace falta que todo aguante, a ellos les basta con una captura.

De ahí el orden de las fases: **primero lo que se encuentra sin esfuerzo y cuesta horas
arreglar**, después lo que cuesta días, y en paralelo lo que cuesta semanas. No al revés.

Y un corolario que cambia el tono de la presentación: la defensa contra "esto es slop" no
es negarlo, es **haberlo encontrado tú antes y haberlo cerrado**. Un repo con un
`KNOWN-ISSUES.md` honesto y gates que fallan cuando deben es más creíble que uno que
promete que todo funciona.

---

## Fase 0 — Que el repo deje de contradecirse a sí mismo

**Coste: horas. Todo es borrar, renombrar o reescribir texto. Cero riesgo de regresión.**

Es la fase con mejor relación daño-evitado / esfuerzo de todas, y hoy está sin hacer.

| Acción | Cierra |
|---|---|
| Publicar el árbol local: la línea "aún NO implementado" ya está corregida sin commitear, y `infected_drive` está verificado y sin publicar | N-1, N-11 |
| ~~Gate de conteo de tools~~ **YA EXISTE y está verde**: `PublicToolCountDocsTest` (`tools/tests/test_install_mcp.py:1262`) instancia la app y contrasta el conteo contra README y arquitectura. No hay nada que construir. Queda solo decidir si `ui_dialog`, que es una tool viva que el gate obliga a ocultar del README, debe documentarse o retirarse del registro por defecto | N-3 (rebajado a MENOR) |
| Sacar `exec_enforce` del titular; donde se mencione, la limitación pegada a la frase | N-5 |
| **Pintar los siete bordes que la mutación dejó al descubierto**, antes de que el `1770` entre en el README como credencial. Corrida ejecutada el 2026-08-21 sobre la copia limpia: **22 mutaciones en 10 módulos → 13 cazadas, 9 supervivientes**, veredicto *defendible con matices*. Lo que falta pintar: `sample_hz=61` (el techo de 60 está triplicado en `loopback.py`, `vehicle_trace.py:180` y `normalize_request`, y el contrato de ingreso pinta el techo de `limit` y `max_samples` pero de `sample_hz` solo el suelo); puerto `65531` y `65536`; `NUL` en `valid_text`; el mensaje de regex de `NAME_RE`; **el CLI de `PASS_WITH_WARNINGS`**; un árbol Unicode de profundidad 5 que no falle por otra puerta; y un literal para `DRAFT_NOTE`. Además, borrar `test_client_mode.py:237` — es `assertEqual(x, x)` | credencial |
| Subir al README, en inglés, las cuatro mediciones que importan (0,0313 m · `ticks_in_flight`=4741 · rumbo 92,1° vs 90° · el probe que **descartó** conducir server-side) y añadir un apartado "lo que este proyecto NO puede hacer y por qué" | N-6, N-4 |
| `git rm` de `task9_build_a_smoke.py` + su test, `run-fase*.ps1`, `run-poc.ps1`, `_audit_*.py`. Mover `spike0/mcp-grab.ps1` a `tools/capture/grab.ps1` y actualizar `GRAB_SCRIPT` en `mcp_capture.py:86` | N-8, N-9 |
| Renombrar el tag del bridge: `[MCP-POC]` → `[DayZ-MCP]` en `MCPBridge.c:3397`. Verificado que ningún `.py` grepea el tag viejo | S-5 |
| Sustituir los ~70 códigos de tracker privado por una línea de explicación, o enlazarlos a un CHANGELOG público. Traducir los comentarios sueltos en castellano | S-6 |
| Arreglar o quitar `_audit_paths.py`: hoy reporta como ausentes flags que existen | N-10 |

El superviviente que más importa es `PASS_OVERALL` (`playbooks/runner.py:60`): quitar
`PASS_WITH_WARNINGS` del frozenset deja **los 24 tests de `test_playbook_runner` en verde**.
El set solo gobierna el exit code del CLI en `:812` y `:822`, que ningún test ejercita — el
camino de directorio de `:817` compara contra el literal `"PASS"`. O sea: `playbooks/README.md`
promete públicamente exit 0 en `PASS_WITH_WARNINGS` y no hay nada que defienda esa promesa.
Es el mismo defecto que A-11, ahora medido desde el otro lado.

### Estado al cierre del 21-ago

| Fila | Estado |
|---|---|
| Publicar el árbol local | **pendiente** — es el `git push`, y es tuyo |
| Gate de conteo de tools | ya existía; hoy además **deja de imponer dónde vive la salvedad** (ver N-5) |
| Pintar los siete bordes de la mutación | ✅ `tools/tests/test_boundary_values_are_pinned.py` (21 tests) + el CLI en `test_playbook_runner.py`. **15 mutaciones, 15 cazadas, 0 supervivientes** |
| README: las 4 mediciones + «lo que NO puede hacer» | ✅ dos secciones nuevas, cada cifra con `fichero:línea` re-verificada |
| `git rm` + mover `spike0/mcp-grab.ps1` | move ✅ · los `git rm` **retirados** (ver el plan de purga: 11 de 12 eran falsos positivos) |
| Sacar `exec_enforce` del titular (N-5) | ✅ fuera de la primera frase; se queda en el cuerpo con la salvedad y en la sección de límites |
| ~70 códigos de tracker privado + castellano suelto (S-6) | ✅ **123 de 126** aplicados en 35 ficheros. Los 3 de `MCPBridge.c` apartados a propósito |
| Arreglar `_audit_paths.py` (N-10) | **fila muerta**: el fichero no existe en el árbol |
| Renombrar `[MCP-POC]` → `[DayZ-MCP]` (S-5) | pendiente, 7 ficheros a la vez (ver plan de purga, bloque C) |

**Los 3 edits de S-6 apartados** (`scratchpad\_lanes\s6_apartados.json`) tocan comentarios de
`MCPBridge.c`, que está bajo el centinela de hash. Cualquier cambio ahí obliga a re-congelar,
así que van agrupados con S-5, que también toca el bridge. Una edición, un re-congelado.

### ⚠ Hallazgo nuevo que sí bloquea, y no es de documentación

**El launcher nativo construido se quedó viejo respecto a sus fuentes, por culpa del arreglo
S-2.** `pinned_keyfile.py` y `host_config.py` están en `PACKAGED_MODULES` (viajan dentro de
`app.pyz`), y al editarlos el bundle dejó de coincidir: `verify_bundle` lo canta con
`app_module_drift`. Peor: S-2 movió la struct a un módulo nuevo, `win32_fileinfo.py`, que **no
estaba en `PACKAGED_MODULES`** — un `ModuleNotFoundError` esperando dentro del bundle en cuanto
alguien lo reconstruyera. Arreglado en el fuente, más un test estático nuevo
(`PackagedModuleClosureTest`) que exige que la lista sea cerrada bajo sus propios imports y que
corre también en un clon sin bundle.

**Lo que falta es tuyo, porque es una operación de seguridad.** Reconstruir el bundle funciona
y es reproducible — dos builds independientes dieron los mismos hashes (`app_pyz 340A8F48…`,
`pe A8F59199…`) — pero cambia el PE, y el registro de launchers acreditados pinea el viejo
(`438619D3…`). Con el bundle reconstruido y sin re-registrar, `dayz_test_run` muere con
`invalid_native_launcher_bundle`. Así que **restauré el bundle anterior**: el launcher sigue
acreditado y usable, y los dos rojos que quedan dicen la verdad — el bundle está viejo.

Secuencia para cerrarlo, cuando quieras:

```powershell
cd tools
.\.venv-mcp\Scripts\python.exe build_native_launcher.py --offline --verify-reproducible
.\.venv-mcp\Scripts\python.exe -m dayz_mcp.launcher_registry_update install-dayz-test-v1 --expected-sha256 <el pe_sha256 que imprima>
```

Respaldo del bundle actual en `scratchpad\_backups_fase0\bundle_pre_rebuild\`.

**Gate de fase 0**: un lector hostil recorre el repo durante 20 minutos y no encuentra
**ninguna** contradicción interna, ni un resto del directorio de trabajo, ni una ruta
personal. Se verifica pasando el propio barrido mecánico de este council otra vez.

---

## Fase 1 — Que arranque en una máquina que no es la tuya

**Coste: días. Es la fase que decide si el producto existe fuera de tu casa.**

| Acción | Cierra |
|---|---|
| **`MIGRATION_DIR` deja de ser `P:\DayZ_MCP_dev\...`.** `RuntimePaths.from_env()` ya fija `%LOCALAPPDATA%\DayZ_MCP` y `ensure_runs_v1_backup` **ya recibe** ese objeto `paths`: son tres líneas. Añadir un test que falle si vuelve a aparecer una letra de unidad literal en el paquete | P-1 |
| **Un solo instalador.** Registro por cliente y opt-in (`-RegisterClaude`), o imprimir el JSON para que lo pegue quien use Cursor. Codex no puede ser requisito. Pin de Python único y cierto. `Test-Path` del `python.exe` tras crear el venv | P-3 |
| AddonBuilder y DayZDiag desde la policy sellada o resueltos por registro de Steam / `DAYZ_TOOLS_PATH`; el default de `C:\Program Files (x86)` pasa a fallback y el fallo nombra la ruta que buscó | P-2 |
| Quitar del lock el artefacto `project_relative` que no viaja, y que el test **falle** en vez de hacer `continue`. Una sola cadena de suministro para `psutil` | P-4 |
| **Receta de empaquetado**: `tools/pack-addon.ps1` que deje `@DayZ_MCP\Addons\DayZ_MCP.pbo`, más diez líneas sobre firma y `verifySignatures`, y un párrafo de convivencia (`modded class`, `super`, load order) | B-2, B-3 |
| **`QUICKSTART.md` de ≤40 líneas con UN solo camino**, el corto: venv → registrar un cliente → `dayz_mcp.json` en profiles → cargar el PBO → `bridge_status` verde. El launcher nativo pasa a "opcional, solo para `dayz_test_run`". Reordenar el README para que el camino corto sea lo primero y la tabla del loop marque qué fila pertenece al camino largo | N-7, N-2 |

**Gate de fase 1 — el único que no se puede simular**: **una persona que no seas tú**,
en su máquina, clona el repo y llega a `bridge_status` verde siguiendo solo el
`QUICKSTART.md`, sin preguntarte nada. Si tiene que preguntar, el quickstart no está
terminado. Anota cuántos minutos tardó: ese número va al README.

---

## Fase 2 — Que las tools dejen de mentir

**Coste: días. Es el hallazgo estructural del council y el más caro de ignorar.**

El patrón es siempre el mismo: **la tool confirma que aceptó la orden, no que la orden
surtió efecto**. Delante de este público destruye la confianza en toda la superficie a la
vez: si tres tools mienten, ninguna medida vale, y todas tus mediciones in-game pasan a
ser sospechosas.

| Acción | Cierra |
|---|---|
| **Preflight en `dayz_test_run`**: si el argv resuelto no contiene el mod puente, fallar con `bridge_mod_missing: add extra_mods=['@DayZ_MCP']` — o inyectarlo por defecto con opt-out. Red inferior: si a los N segundos del `succeeded` el peer no ha sondeado, adjuntar `bridge_not_polling` al resultado | B-1 |
| **Bajar `radius` por defecto a 50** en `vehicle_prepare_fixture` (o subir el cap del bridge) y que la validación Python rechace `>50` nombrando el rango. Igual para `telemetry_read` | A-2 |
| **`vehicle_enter`**: "seated:1 = orden aceptada, no estado final; para `engine_set`/`vehicle_control` usa `vehicle_get_in_client`". En `engine_set`: "requires client-side ownership" | A-5 |
| **`object_anim`**: devolver `phase_requested` en escritura y renombrar la lectura inmediata a `phase_before` | A-9 |
| **`capture_screenshot`**: `frame_hash` en la respuesta y una línea en la descripción sobre el frame congelado sin foco. El foco automático puede venir después | A-10 |
| **`world_spawn` deja de auto-certificarse.** `IsSpawnReady` busca el objeto en la posición del propio objeto: siempre se encuentra. Verificar contra invariante independiente — distancia al punto **pedido** y estabilidad en varias muestras. Lo que se aleja es `unstable_spawn` con `object_id` para poder limpiarlo, no `success` | C-1 |
| **`engine_set` compara `ok` con `mode`.** Hoy hace `result.ok = true` sin relacionarlo nunca con lo pedido. Si solo se puede garantizar despacho, devolver `command_sent` y `state_confirmed` por separado | C-2 |
| **`vehicle_control` valida el mismo rango en ambos lados** y devuelve `effective_ttl_s`. Un TTL >30 se rechaza, no cae al default de 3 en silencio | C-3 |
| **`capture_screenshot` identifica qué ventana capturó** (con servidor y cliente hay dos DayZDiag) | C-4 |
| **`world_time_set` da veredictos separados** para fecha y multiplicador: la fecha correcta no valida por arrastre lo que nunca se comprobó | C-5 |
| **`reap_dead_run` retira un run propio muerto** aunque haya un DayZDiag ajeno en la caja | C-6 |

**Gate de fase 2**: recorrer los handlers uno a uno y contestar, por escrito, **qué
evidencia tiene cada uno de que la acción ocurrió** frente a solo confirmar que el comando
se encoló. Cada `ok` sin evidencia se arregla o se documenta como "aceptado, no
confirmado". Ese documento es publicable y juega a favor.

---

## Fase 3 — La puerta retail

**Coste: días. Decisión ya tomada: fail-closed por defecto, opt-in explícito del admin.**

| Acción | Cierra |
|---|---|
| **Hacer visible la cuarentena.** Hoy `grep -c retail server.py` = 0: el 409 colapsa a `remote_error`. Añadir `retail_quarantine` a `_REMOTE_ERROR_CODES` con hint fijo, por el mismo mecanismo que ya usa `lease_required` | A-1 |
| **Clasificar los call-sites.** Codex ya mapeó los **once** y los clasificó: la tabla completa está en `lanes/lane-codex.md` §B.1 y es el diseño de la puerta. Los de política de usuario abren con opt-in; los de seguridad de sistema no | R-1 |
| **Desbloquear `cleanup_owner` de la señal retail.** Protege al SISTEMA — desarma a un owner que perdió autoridad — y hoy lo frena la cuarentena. Es un defecto del diseño actual, independiente del opt-in | R-5 |
| **Razón tipada en el bloqueo**: hoy "retail presente", "probe roto", `known=false` y payload malformado dan el mismo 409 | R-3 |
| **Cerrar la carrera del stop**: los probes repetidos cierran carreras al iniciar, no al parar; retail apareciendo a mitad de un stop produce cleanup parcial | R-6 |
| **Dejar el lifecycle retail fuera del alcance del opt-in**: la allowlist diag-only de `process_lifecycle.py:976-988` es una decisión aparte y sigue cerrada | R-4 |
| **La puerta**: configuración explícita del admin, con avisos claros y auditoría de que se usó. Fail-closed sigue siendo el default; el que no la abre recibe un error que le dice **por qué** y **cómo** abrirla | Decisión del usuario |
| Camino de diagnóstico: un verbo o campo que diga qué proceso disparó la cuarentena | A-1 |

Las invariantes que el opt-in **no** puede tocar, verbatim de su informe: no concede lease
ni salta `reservation`/`commit`/`claim`; el destino sigue ligado a instancia (presencia retail
no identifica destino); el `identity guard` sigue siendo quien autoriza terminar un proceso,
nunca el booleano retail; y `audit_failed` / `manifest_failed` no se degradan a éxito por
haber abierto la puerta.

**Gate de fase 3**: un admin con su servidor retail corriendo habilita el opt-in
conscientemente y opera; un admin que no lo habilita entiende en un solo mensaje por qué
está bloqueado y qué hacer. Y las invariantes de "no matar el proceso equivocado" siguen
siendo ciertas con el opt-in abierto — eso se demuestra con tests, no con argumentos.

---

## Fase 4 — Smoothed para agentes menos potentes

**Coste: ~una semana. Es el objetivo que pediste explícitamente, y hoy no hay ninguna
evidencia pública de que se cumpla.**

| Acción | Cierra |
|---|---|
| Pasada mecánica sobre los 43 `bad_args` de Python aplicando el patrón que **ya está pagado** en `playbook_tool.py:158-166` (campo + esperado). `_invalid()` acepta el nombre del campo. El lado Enforce puede esperar: el 80% del beneficio está en Python | A-3 |
| **`marker` en `wait_for`**, el mismo que `logs_since` ya devuelve, para anclar la espera a "desde que yo actué". Mientras no exista, la descripción debe declarar el doble filo del lookback, no solo su ventaja | A-4 |
| Pasada de unidades en las descripciones: `rotation` es un **flag `RF_*`, no grados** (hoy es un error de API, no de documentación); `cam_orientation`, `fov`, `show_time`, `time`, `min_duration`, `phase` con su unidad | A-6 |
| `object_id` opcional en `object_anim` / `object_inspect`; `entities_query` devuelve ids; `ambiguous_object` desempata por cercanía y lista candidatos | A-7 |
| **Coser el Knowledge Pack**: enlace y qué aporta en README e `instructions` del server; en las 3-4 descripciones que exigen vocabulario de dominio, decir de dónde sale; `ui_tree` sin menú lista los roots en vez de `no_menu` | A-8 |
| `PASS_WITH_WARNINGS` deja de contar como PASS cuando la degradación viene de un gate `uncalibrated` que era STOP; la clave de lectura del veredicto entra en la descripción de la tool | A-11 |
| **Modo reducido documentado**: registrar solo el núcleo (~13 tools) y el resto opt-in, con el mismo mecanismo de registro condicional que ya usa `exec_enforce` | A-12 |
| `blocked_on {what, held_by, unblock_with, queued_here}` en `session_status`; el TTL de 120 s en las descripciones de `session_acquire_wait`/`heartbeat` | A-13 |
| `version_blocked` con ambos peers en `null` pasa a `game_not_ready:reason=no_run`; el estado ya se distingue en `server.py:267-268` | A-14 |

**Gate de fase 4 — y es también el mejor material de presentación que vas a producir**:
una corrida **grabada** de un modelo pequeño cerrando un ciclo completo sin ayuda
(spawn → medir → corregir). La línea base contra la que se compara ya existe y es tuya:
la sonda qwen3.5 del 17-ago necesitó 548 s y 15 llamadas para lease + teleport + release,
y además teleportó al jugador de un run ajeno. Si la nueva corrida baja ese número y no
toca nada ajeno, tienes la respuesta a la pregunta incómoda de esa lane **en vídeo**.

---

## Fase 5 — Que el código aguante la lectura (en paralelo desde el día 1)

**Coste: los dos primeros, horas. El resto, semanas.**

Los dos primeros no esperan a nadie: son baratos y son exactamente las capturas que este
público busca.

| Acción | Coste | Cierra |
|---|---|---|
| **Borrar la clase muerta** de `daemon_policy.py:284-354` y el wrapper sin callers de `host_config.py:119-121`; re-correr la suite | S | S-1 |
| **Una sola declaración** de `_FILE_STANDARD_INFO` con `c_ubyte`, y un test que abra un directorio real y verifique `Directory=True`. Deduplicar el bloque de prototipos `kernel32` común a los dos módulos | S | S-2 |
| Consolidar los 5 validadores de ruta absoluta en uno, con un test parametrizado que fije la semántica única | M | S-3 |
| Mover `security_runtime_audit.py` a `tools/checks/`, donde ya viven los otros gates de desarrollo | S | S-8 |
| Extraer el registro de tools por dominios a funciones `register_*(app, runtime)` de 100-300 líneas, sin tocar firmas | M | S-4 |
| Clase base `MCPBridgeBase` con el núcleo compartido entre los dos bridges; pasada mecánica de `while`/`i = i + 1` a `for`/`++` | M | S-9 |
| Bloque de invariantes por módulo (qué protege el WAL, qué es un tombstone, el ciclo ticket→grant→release) y partir `SessionCoordinator` en 3-4 colaboradores tras la misma fachada | L | S-7 |
| Tabla declarativa `{comando: schema}` en vez de las 17 ramas de `validate_command_args` | M | S-10 |

**Nota de orden**: S-4 y S-7 son más graves que varios de los de arriba, pero son los
únicos defendibles de palabra ("es la función de registro", "es una máquina de estados
densa") y su arreglo no compra óptica inmediata. Van después de que S-1 y S-2 estén fuera.

---

## El gate de "presentable"

Se presenta cuando las **seis** se cumplen a la vez. Ninguna es opinable:

1. **Fase 0 cerrada.** El barrido mecánico vuelve a pasar sin contradicciones, sin restos
   del directorio de trabajo y sin rutas personales.
2. **Un tercero llegó a `bridge_status` verde** en su máquina, con el quickstart y sin
   preguntarte. Con el número de minutos anotado.
3. **Ninguna tool devuelve éxito sin evidencia**, o lo declara explícitamente. Con el
   documento de postcondiciones publicado.
4. **La puerta retail existe** y su cierre se explica solo, en un mensaje que el agente ve.
5. **Hay una corrida grabada de un modelo pequeño** cerrando un ciclo completo.
6. **S-1 y S-2 están cerrados.** Son las dos capturas que tumban la presentación entera y
   cuestan horas.

Fases 4 (parcial) y 5 (más allá de S-1/S-2) pueden quedar abiertas **si están declaradas**
en un `KNOWN-ISSUES.md`. Declarado no resta; encontrado por otro, sí.

## Qué NO hay que hacer

- **No reescribir nada.** Ninguna lane propuso rediseño y el council lo tenía prohibido
  por brief. El producto funciona; lo que falla es el borde.
- **No traducir los 47 kB del `product-spec`.** Extraer las mediciones al README en inglés
  y marcar los documentos largos como notas de diseño internas.
- **No quitar la cuarentena retail.** La decisión es abrir una puerta, no tirar el muro.
- **No presentar antes de publicar.** El repo público va por detrás del árbol de trabajo.
