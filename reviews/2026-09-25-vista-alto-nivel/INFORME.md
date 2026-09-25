# DayZ-MCP — vista a alto nivel, valoración y roadmap

**Fecha:** 2026-09-25 · **Commit auditado:** `main` en `57269d2` (árbol de trabajo en `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev`) · **Autor:** Claude (orquestación, métricas y verificación) con lanes Qwen3.8-Flash-Next en el GX10.
**Método, en una línea:** las cifras salen de AST y git sobre `HEAD` (scripts en §8). Cada `path:line` de este informe se abrió antes de citarlo. Lo que dijeron las lanes FN solo entra si pasó la verificación mecánica y mi lectura (§8).


> **Actualización 2026-09-26, después del commit auditado:**
> - **C2 está resuelto en parte en `main`** con la #94 (`9af5cbf`, ficha `fb-20260924-235528-0878`, anterior a esta revisión). El bridge anuncia `ach=` (un hash del contrato de argumentos) y `bridge_status.ready` falla en cerrado si falta el hash, si es erróneo o si el censo no cuadra. El literal `"10"` sigue igual.
> - **C5 ya tenía fichas:** `fb-20260817-120511-2b00` (auto-renovar el lease) y `fb-20260915-014746-d85b` (el lease caduca en las pausas del agente), relacionadas con `d0e0` y `2ee7`.
> - **Fichas nuevas en el buzón:** C1 `fb-20260925-233932-aa11`, C3 `fb-20260925-233937-b753` (relacionada con `e7ef`), C4 `fb-20260925-233943-9ccc`, C6 `fb-20260925-233946-4920`, C7 `fb-20260925-233952-9957` y C8 `fb-20260925-233956-528c`.
> - **Corregido en este mismo commit:** `HANDOFF.md`, en la fila `P:\DayZ_MCP` de la tabla de árboles y las líneas 26 y 60.

---

## 0. Conclusión

1. **El diseño de base es bueno y no hay que reescribir nada.** Los tres actores (MCP, peer de control y peer de captura), el bridge que tira de los comandos con callbacks asíncronos sin bloquear el tick, el daemon dueño del puerto con un lease para varios agentes, el censo de capacidades y el `ready`/`reason` de `bridge_status` están bien razonados y documentados con citas al motor.
2. **El problema es crecer sin podar.** La auditoría del 2026-09-07 (nota global 6,1/10) pidió consolidar. Desde entonces el paquete ha pasado de 64 a 81 módulos y los tests de 185 a 262 ficheros, sin borrar ninguno. `server.py` ha pasado de ~4.867 a 7.410 líneas. Desde la importación inicial (semana del 17 de agosto), por cada línea neta de código se han añadido ~3,2 de tests, y lo borrado no llega al 15 % de lo añadido. La deuda se paga en cada cambio: `server.py` acumula 115 commits, 50 de ellos «fix».
3. **Para agentes débiles, lo que más rinde es quitar reglas, no escribir más descripciones.** Hoy cada tropiezo de un agente se arregla con una frase más en la descripción y un test que la fija. Las palancas buenas son: leer sin lease, un lease que se renueve solo mientras la sesión esté viva, que `dayz_test_run` adopte el lease del llamante, un sobre de error único con remedio y siguiente llamada, parámetros documentados en el propio esquema y tools de tarea (playbooks) dentro del pack pequeño.
4. **Nota global: 5/10.** Baja respecto a la del 09-07 por el crecimiento sin consolidar y por problemas de higiene que esa auditoría no midió: 50.366 ficheros sin trackear (~14 GB en OneDrive), dos árboles del addon que divergen y una versión de puente congelada mientras el protocolo cambia.
5. **Por dónde empezar (1-2 días):** higiene y fuente única del addon, versión del puente que falle con remedio, CI mínima, y las tres correcciones del lease que más tropiezos causan (§3.4, P1-P3).
6. **Tests: limpiar es reorganizar, no borrar.** No hay tests copiados: los helpers repetidos rondan el 1 % de las líneas. En la familia con más ficheros por ficha solo 1 de 348 tests es un duplicado real, comprobado. Lo que rinde es agrupar por dominio, compartir helpers, quitar los imports entre tests y separar tiers. El mapa de la primera familia ya está hecho (§4.9, §5).
7. **Seguridad: no sobra en bloque.** Revisada con el código completo (§4.7), la mitad se justifica incluso en 127.0.0.1: keyfile pinneado, transporte acreditado y guardia de PIDs. El resto puede ir a un extra opcional, a CI o retirarse.

---

## 1. Cómo es hoy

```
Agente (Claude/Codex/8B)
   │ MCP stdio
   ▼
dayz_mcp --client [--supervised: supervisor ─► worker reemplazable, server_reload]
   │ HTTP 127.0.0.1:8765  /enqueue /await /status   (lease FIFO, varias sesiones)
   ▼
daemon (--daemon)  = loopback HTTP + SessionCoordinator + ProcessLifecycle + manifiesto de corridas
   ▲ poll / result (el mod tira de los comandos y empuja resultados; key en ?key=)
   │
mod Enforce @DayZ_MCP:  MCPBridge (MissionServer, control)   MCPClientBridge (MissionGameplay, captura/UI/conducción)
   ▲
dayz_test_run ─► process_lifecycle ─► launcher nativo sellado (CPython embebido) ─► DayZDiag servidor/cliente
```

| Capa | Módulos | Líneas | Comentario |
|---|---:|---:|---|
| Tools MCP (`server.py` y afines) | 20 | 13.449 | `server.py` solo: 7.410. `build_app` es una función de 2.617 líneas con las 63 tools dentro |
| Núcleo: daemon, loopback, coordinación, cliente, supervisor | 18 | 17.262 | `loopback` 4.183, `session_coordination` 3.970, `runtime_state` 2.376 |
| Ciclo de vida de corridas (`dayz_test_*`, `process_lifecycle`) | 11 | 10.324 | `process_lifecycle` 4.773, `dayz_test_tool` 2.617 |
| Launcher nativo, identidad y seguridad del host | 20 | 10.859 | `security_runtime_audit` 1.719, `native_launcher_backend` 1.783, `identity_migration` 1.428 |
| Steam, host y diagnóstico | 10 | 4.999 | `doctor` 1.380, `host_config` 1.239 |
| **Paquete `tools/dayz_mcp`** | **81** | **56.904** | 100 funciones de más de 80 líneas, 17 de más de 200 |
| Mod Enforce (`addon/`) | 9 `.c` | 10.709 | `MCPClientBridge.c` 4.529, `MCPBridge.c` 3.473 |
| Tests (`tools/tests`) | 262 | 123.567 | 4.093 métodos de test |
| Scripts sueltos en `tools/*.py` y `_gate.py` | 27 | ~16.500 | gates históricos: `g0_abba_gate.py` 1.990, `h8_*` 1.583 + 1.275, spikes |

Las tres capas del camino «el agente arranca el juego él solo» (ciclo de vida, launcher/seguridad y Steam/host) suman **~26.200 líneas, el 46 % del paquete**. `QUICKSTART.md` dice que ni el launcher nativo ni `dayz_test_run` hacen falta para el uso básico. Ese camino es justo lo que cierra el bucle de desarrollo autónomo que vende el README, así que no es relleno. Sí es un segundo producto metido dentro del primero.

**Dependencias:** cuatro pineadas (`mcp==1.27.2`, `Pillow`, `psutil` y `pywin32`; `tools/requirements-mcp.txt`). Es un buen punto de partida.

---

## 2. Estructura y escalabilidad

### 2.1 Lo que está bien y hay que conservar

- **El transporte respeta el motor:** solo callbacks async en el tick, la key va en la query string porque `SetHeader` solo acepta Content-Type, y el bind es 127.0.0.1. Son decisiones cerradas y justificadas en `CLAUDE.md` y `README.md`.
- **Censo de capacidades en cada poll** (`addon/scripts/5_Mission/MCPBridge.c:20-26`), cruzado con una tabla escrita a mano a propósito para que pueda discrepar (`tools/dayz_mcp/server.py:827-838`).
- **Readiness con motivo:** `bridge_status.ready` más `reason` (`no_run`, `server_poll_stale`, `version_mismatch`…). Es lo mejor de la experiencia de primer uso (`QUICKSTART.md`, paso 5).
- **Trabajo real para modelos pequeños:** `agent_loop.py:1-6` ya parte de que «~8B callers copy tool names from error text» y limita `next_step` a tools públicas. `_bad_args` nombra el campo, el valor y lo esperado (`server.py:5331-5333`). El pack `local8b` y la disclosure progresiva van en la misma dirección.
- **Las tools simples son finas y limpias:** validar, tomar el lock y llamar al bridge (`server.py:5278-5286`, `server.py:5412-5429`).

### 2.2 Qué hay que corregir (defectos verificados)

| # | Qué | Evidencia | Consecuencia |
|---|---|---|---|
| C1 | **Dos árboles del bridge que divergen con la misma versión.** `DayZ_MCP\scripts` y `DayZ_MCP_dev\addon\scripts` difieren en 5 de 10 ficheros: `MCPBridge.c`, `MCPClientBridge.c`, `MCPMessages.c`, `MCPDialogController.c` y `MissionGameplay.c`. La copia de `DayZ_MCP` aún tiene `vehicle_drive`/`drive_probe_client`, retirados en `main` en `5892014`. | sha256 de ambos árboles frente a `git show HEAD:addon/...` (§8); `HANDOFF.md:26` dice que empaquetar desde cualquiera de los dos «pilla el bridge de `main`»; `PROJECT-MAP.md:13` señala `DayZ_MCP` como «Mod source». | Empaquetar desde el árbol viejo despliega un bridge distinto que se anuncia como la misma versión. |
| C2 | **`MCP_BRIDGE_VERSION = "10"` congelado** desde el 2026-09-02 (`b8708ab`), con más de 20 commits al bridge después: verbos añadidos (`inventory_attach`, `action_use_target`) y retirados. El daemon solo compara ese literal y el censo `caps=` no bloquea nada. | `addon/scripts/5_Mission/MCPMessages.c:1`; `tools/dayz_mcp/core.py:41,67-68`; `tools/dayz_mcp/loopback.py:2954-2955` («the census gates nothing»). | Un PBO desfasado da `ready: true`. El verbo que le falta devuelve un `unknown_command` crudo (`MCPBridge.c:563`) que Python no traduce a «reempaqueta». Para un agente débil es un callejón sin salida. |
| C3 | **El catálogo no avisa al perder el lease.** `tools/list_changed` solo se envía tras `session_acquire_wait`, no al soltar el lease ni cuando caduca. | `server.py:4802-4806`; `_progressive_disclosure_active`, `server.py:666-668`. | Tras un release o una caducidad, el host sigue ofreciendo las tools de mutación, que fallarán con `lease_required`. Con el catálogo refrescado el agente sabría que tiene que volver a pedir el lease. |
| C4 | **Las instrucciones contradicen a `dayz_test_run`.** Las instrucciones dicen «session_acquire_wait -> bridge_status.ready -> mutating verbs», y `dayz_test_run` exige soltar antes el lease («Release any held session lease before calling»), regla que fija un test. | `server.py:4681-4698` frente a `server.py:4924`; `tools/tests/test_precondition_docs.py:1-10`. | El primer paso natural de un agente que sigue las instrucciones falla. |
| C5 | **El lease caduca a los 120 s sin llamadas que lo renueven**, y el heartbeat automático solo existe para el launcher nativo. | `server.py:4493-4504`; `tools/dayz_mcp/lease_supervisor.py:1`. | Un agente que piensa, pregunta al humano o espera más de 2 minutos pierde la corrida (`run_not_owned`). |
| C6 | **Documentación desincronizada.** Hablan de «62 tools» `CLAUDE.md:14`, `HANDOFF.md:60` y `product-spec.md:14`; de «63» `README.md:4,65` y `dayz-mcp-architecture.md:10,125`. El build real registra 63 (§8). `CLAUDE.md` y `AGENTS.md` no están en git. | `grep` en los seis ficheros y `build_app` offline. | Cada agente que entra lee una cifra distinta, y el fichero que carga primero (`CLAUDE.md`) no lo protege ningún test. |
| C7 | **Un `camera_set` que falla deja al jugador sin control.** `ApplyCameraSet` llama a `SuppressGameplay()`, que ejecuta `PlayerControlDisable(INPUT_EXCLUDE_ALL)` y oculta el HUD, *antes* de dos salidas de error (`camera_create_failed` y `bad_args` de la matriz), y ninguna de las dos restaura nada. | `addon/scripts/5_Mission/MCPClientBridge.c:3315` frente a `:3325-3339`; `SuppressGameplay` en `:4151-4170`. Lo encontró W5 y lo he comprobado. | El control queda bloqueado hasta que alguien llama a `restore_gameplay`, y un agente débil no sabrá que tiene que hacerlo. Son fallos raros, pero el arreglo es de una línea: suprimir después de crear la cámara o restaurar en el error. |
| C8 | **No hay CI.** `.github/` solo tiene plantillas de issues. | `git ls-files .github`. | Los 4.093 tests solo protegen cuando alguien los lanza a mano; un PR puede entrar en `main` sin que nadie haya corrido la suite. |

### 2.3 Qué hay que mejorar (estructura)

- **M1 — `server.py` es el cuello de botella del proyecto.** `build_app` (`server.py:4655-7271`) define las 63 tools como closures, `server.py` importa 34 módulos internos, y cuatro funciones parchean esquemas después de registrar las tools (`_patch_public_argument_alias` `:2570`, `_patch_closed_tool_schema` `:2640`, `_patch_mode_enum_from_authority` `:2665`, `_describe_run_parameters` `:2741`). La disclosure progresiva reengancha el handler con internos privados de FastMCP (`server.py:7270`; hay 12 usos de `_tool_manager`/`_mcp_server` en el paquete), que se romperán al subir de `mcp==1.27.2`. Es el fichero con más churn (115 commits, 50 fix).
  **Qué ayudaría:** un módulo por familia (`tools/world.py`, `tools/vehicle.py`, `tools/session.py`…) que registre sus tools a través de un registro común, y un decorador que concentre lock, validación, sobre de respuesta y `next_step`. Declarar el esquema en la propia definición (`Annotated[..., Field(description=...)]`) y dejar de parchearlo después. `build_app` se quedaría en componer.
- **M2 — El transporte Enforce está duplicado.** El 23 % de las líneas significativas de `MCPBridge.c` aparecen tal cual en `MCPClientBridge.c`, y hay 30 funciones con el mismo nombre (TryInit, StartPoll, OnPoll*, PostResult, DrainPending, EncodeQueryValue, Shutdown…). No hay clase base. La auditoría del 09-07 habló de «~70 %»; medido línea a línea sale bastante menos, pero es la capa de transporte entera.
  **Y ya no son copias iguales, sino bifurcaciones que divergen en la semántica** (W5, §4.8, comprobado por mí):
  - el `IsFiniteFloat` del cliente solo rechaza NaN y acepta infinitos, mientras que el del servidor rechaza también ±`float.MAX`;
  - el watchdog de poll existe solo en el cliente;
  - el log de «resultado perdido» existe solo en el servidor.

  Un arreglo en un lado no llega al otro.
  **Qué ayudaría:** `MCPBridgeBase` con el transporte y hooks por lado (`LogTag`, `QueueFullError`, `PollContext`, `DeferFromHere`, `AcquireResultCallback`…). Al unificar se elige la versión estricta de cada función. El diseño función a función está en §4.8.
- **M3 — Máquinas de estado en funciones enormes.** `acquire` (`session_coordination.py:284`, 464 líneas), `_start_run_reserved` (`process_lifecycle.py:2375`, 472), `_supervise_created_launcher` (397), `execute_dayz_test_run` (314) y `stop_run` (272). La autoridad del lease viaja como `tuple[str, str, str]` y se lee por posición 59 veces (`authority[1]` = lease id, p. ej. `process_lifecycle.py:2433`). En el paquete hay 271 `except Exception` y 60 `type: ignore`. El código es defensivo y coherente, pero razonar sobre él exige tener entera en la cabeza una función de 400 líneas, y eso explica por qué hay ficheros de test de hasta ~5.000 líneas.
  **Qué ayudaría:** tipos con nombre (`LeaseAuthority`), un paso por guardia con nombre y una tabla de estados explícita. §4 recoge la propuesta concreta de las lanes FN.
- **M4 — Un segundo producto dentro del primero.** El 46 % del paquete está en el camino de autoarranque. Hay que separar dos cosas:
  - **El ciclo de vida de corridas se justifica** (R3a, §4.2): idempotencia, reservas y reap cubren escenarios reales cuando varias sesiones comparten el juego. Lo que falla ahí es la legibilidad.
  - **Las capas de launcher, identidad y seguridad (10,9k líneas), revisadas con sus cuerpos (§4.7), no son desproporcionadas en bloque:**
    - **Se quedan en el núcleo:** el keyfile pinneado, el transporte acreditado (evita mandar la API key a un proceso que suplanta el puerto en 127.0.0.1, algo posible aun con un solo usuario), el contrato de políticas del daemon y la guardia de procesos (la reutilización de PIDs en Windows es real).
    - **Pasan a un extra opcional:** el sellado y verificación del bundle (cubre despistes al copiar bundles, no a un atacante), las transacciones de instalación con receipts, la autoridad de rutas y el bootstrap.
    - **Solo desarrollo:** `security_runtime_audit.py` (1.719 líneas de visitantes AST) es una guardia de regresión del repo; su sitio es CI o `tools/checks/`, no el paquete que se instala.
    - **Retirable:** `identity_migration.py` (1.428 líneas), en cuanto no quede ninguna instalación v1.

  **Qué ayudaría:** el extra `dayz-mcp[launcher]` con su propia suite. Sin él, `dayz_test_run` lanza DayZDiag directamente con una política mínima o devuelve un error tipado.
- **M5 — Higiene del repositorio.** Hay 50.366 ficheros sin trackear: `_s0` 4,8 GB, `_fase3` 4,2 GB, `_fase2` 2,5 GB y `reviews` 2,5 GB, este último con un home entero de Cursor dentro. Ninguno está en `.gitignore` y todo vive en una carpeta de OneDrive. La raíz tiene 31 ficheros de backup y 5 `AUDITORIA_*.md`. En `tools/` siguen gates de fase históricos. Los agentes pagan este ruido en cada Grep o Glob, y git en cada `status`: en esta sesión, un bucle de 81 `wc` tardó más de 120 s.

### 2.4 Escalabilidad: qué pasa si…

| Escenario | Hoy | Qué haría falta |
|---|---|---|
| **Tools 64 a 100** | Cada tool nueva es una closure más en `build_app`, un párrafo de reglas en su descripción y un test que fija ese párrafo. El catálogo ya ocupa ~15,7k tokens. | M1 más descripciones cortas, parámetros documentados en el esquema y reglas largas en recursos MCP. |
| **5-10 agentes a la vez** | El lease es exclusivo, y las lecturas no lo necesitan pero quedan ocultas hasta tenerlo, así que los observadores se ponen en cola detrás de quien muta. La cola FIFO admite 64 (`session_coordination.py:20`). | Lecturas visibles y ejecutables sin lease (§3.4, P1) y un lease que se renueve solo (P2). Con eso la exclusión solo afecta a las mutaciones. |
| **Dos instancias de DayZ en la máquina** | Fuera de alcance por spec (`product-spec.md:172-174`): un puerto, un lease y un «box». | Una clave de instancia en todas las capas: puerto, key, lease y manifiesto. Es una decisión de producto, no un arreglo. |
| **Servidor remoto o Linux** | El mod es portable (es Enforce). El daemon y el ciclo de vida dependen de Windows (pywin32, identidad de proceso, WMI), y MCP va por stdio. | Transporte MCP por HTTP con auth para agentes remotos y un `ProcessBackend` abstracto para lanzar en Linux. Es el caso del pitch «An agent can run a server» del README. |
| **Un segundo desarrollador** | Necesita subst de `P:\`, DayZ Tools, el launcher nativo reconstruido con sus pins de MSVC/SDK y una política de launcher a mano. Sin CI. | CI, un instalador único y el launcher como extra opcional (M4). |

---

## 3. Agentes con poca capacidad

### 3.1 Lo que se ve hoy (medido)

- **Catálogo completo:** 63 tools, ~62.700 caracteres (~15,7k tokens). **Pack `local8b`:** 16 tools, ~21.000 caracteres (`tool_pack.py:12-31`).
- **Antes de tener lease, en modo `--client`:** 17 tools con la descripción cortada a 80 caracteres (`server.py:632-653`, `675-689`).
- **222 de 228 parámetros (97 %) no tienen `description` en su JSON Schema.** En 57 de las 58 tools con parámetros no hay ni uno documentado. Solo 3 de las 63 descripciones traen un ejemplo. Toda la semántica va en la prosa de la tool: `dayz_test_run` tiene 23 parámetros y 17 sin describir; `capture_screenshot`, `wait_for` y `camera_set` no describen ninguno.
- **Las descripciones son reglamentos, no instrucciones de uso:** `bridge_status` pide «validate by shape… never against a whitelist», y `logs_since` explica que `max_lines=1` «replays the whole boot» y que, para marcar «ahora», hay que leer hasta vaciar (`server.py:5303-5307`). No es copiar y pegar: las frases repetidas en 3 o más tools solo suman ~1k de 37,6k caracteres. Cada tool trae sus propias reglas.
- **Por qué pasa:** cada fallo de un agente débil se ha arreglado con una frase y un test que la fija (`tests/test_precondition_docs.py:1-10`: «weak agents skip seated/clear/prepare because the catalog did not name the bridge errors»). Funciona para un modelo fuerte que lee todo. Para uno de 8B, cada frase más es ruido.

### 3.2 Las trampas concretas

1. Las 12 lecturas no necesitan lease (`session_coordination.py:21-41,54-55`), pero no se ven hasta tenerlo (`server.py:632-689`). Y ocultarlas no restringe nada: la disclosure solo filtra `tools/list` (`server.py:7259-7263`) y la llamada sigue funcionando. Lo comprobé con la app offline y la disclosure forzada: `query_all_players` no aparece en la lista, pero `call_tool("query_all_players")` se ejecuta y llega al bridge. Al agente débil se le quita la información sin ganar nada a cambio.
2. Las instrucciones y `dayz_test_run` se contradicen sobre el lease (C4).
3. El lease caduca a los 120 s y en sesiones interactivas nadie lo renueva (C5).
4. `list_changed` solo se envía al adquirir (C3).
5. Con un proyecto que no sea DayZ_MCP, `dayz_test_run` exige `extra_mods=['@DayZ_MCP']` o falla con `bridge_mod_missing` (`server.py:4938-4939`). El caso normal de un tercero necesita un parámetro oscuro.
6. Tras cualquier mutación que sale bien, `next_step` apunta a `session_heartbeat` (`agent_loop.py:59-60`), no al siguiente paso útil. Un agente que copia `next_step` hará heartbeats en vez de avanzar.
7. `local8b` no trae ningún verbo de mundo ni `playbook_run`: un 8B puede arrancar el juego y esperar, pero no montar una escena. Y las instrucciones del servidor le hablan de `playbook_run`, `surface_query` y `action_use`, que ese pack no tiene.
8. Las tools internas del flujo del autor (`pipeline_inbox`, `pipeline_feedback`, `pipeline_resolve`) son 3 de las 17 iniciales y 3 de las 16 de `local8b`. `pipeline_resolve` exige rutas bajo `reviews|gates|reports|research` de este repo, así que para un tercero es ruido.
9. **Cuando el lease caduca, tampoco se puede leer.** La corrida pasa a `RUNNING_IDLE` y la valla de encolado del daemon rechaza *cualquier* comando, también las lecturas, con `run_not_owned`: `_enqueue_run_rejection` ignora si el comando muta (`_ = mutation`, `loopback.py:1386-1414`; se llama en `:1476`). Después, durante 90 s solo el antiguo dueño puede recuperar el lease (`LEASE_GRACE_S = 90.0`, `session_coordination.py:16`; `_grace_blocks_stranger_locked`, `:2194`). Está bien pensado para una reconexión, pero para un agente lento la secuencia es: 120 s sin renovar, se queda sin lecturas y luego hay una ventana de 90 s que no puede deducir. Lo encontró la lane W4b y lo he comprobado yo.
10. Hay duplicados y variantes de bajo nivel en el catálogo completo (`lease_acquire` como alias; `session_acquire`/`session_wait`/`session_cancel` marcados LOW-LEVEL), cada uno una forma más de equivocarse.

### 3.3 Prueba con un agente simulado

**Montaje.** FN sin thinking, como aproximación a un agente débil, recibe una de tres vistas del catálogo, siempre con las `instructions` reales del servidor:
- **completo:** 63 tools;
- **previo al lease:** las 17 tools con la descripción cortada a 80 caracteres, exactamente como `_compact_initial_catalog`;
- **`local8b`:** 16 tools.

Con cada vista escribe el plan de llamadas de 5 tareas: T1, arrancar con el mod y confirmar readiness; T2, spawnear un sedán y leer su telemetría; T3, teletransportar y esperar una línea de log; T4, capturar; T5, listar jugadores sin cambiar nada. Cada llamada se valida mecánicamente contra los esquemas reales (que la tool exista y sea visible, parámetros obligatorios, tipos y enums) y contra un criterio por tarea (tool necesaria, lease antes de mutar). Hay 2 muestras por vista. Es una tendencia, no una tasa.

| Vista | Tareas bien | «Imposible» | Otros fallos |
|---|---:|---:|---|
| Completo (63) | 3/5 en la muestra válida | 0 | La otra muestra no es JSON válido: el modelo razonó dentro de una nota y rompió la estructura. T2 y T3 mutan sin coger antes el lease. |
| Previo al lease (17) | 1/10 | 6/10 | T5 falla las dos veces: `query_all_players` está oculto. Una muestra coge el lease exclusivo solo para contar jugadores con `wait_for`; la otra prueba `session_status`. T2 intenta `playbook_run`, que nombran las instrucciones pero no está visible. |
| `local8b` (16) | 1/10 | 9/10 | Reconoce que no puede hacer nada del mundo. En una muestra también da T1 por imposible. |

**Lo que se aprende, con el razonamiento literal de los planes** (`evidencia/weak_*.json`):
- **Parámetros sin describir:** con la vista previa al lease, T1 se declaró imposible porque `project` no tiene descripción y el modelo no supo si «MiMod» era un proyecto o un mod («falta la definición del proyecto base»).
- **Lecturas escondidas:** para una pregunta de solo lectura, el agente débil acaba cogiendo el lease exclusivo o usando la tool equivocada. Es justo lo que P1 evita.
- **Instrucciones que nombran lo que no se ve:** el modelo detecta que se menciona `playbook_run` y no está, y abandona («se menciona en las instrucciones pero no está en la lista»).
- **Con el catálogo completo, el orden del lease es el error que queda.** Ahí la receta `lease_required` sí da el siguiente paso, así que en un bucle real es recuperable. P2/P3 lo quitarían de raíz.
- **Instrucciones contradictorias sobre la Y:** dicen a la vez «pos=[x, surface_query.y, z]» y «y=0 is ground», y el modelo duda entre las dos antes de usar y=0.

### 3.4 Propuestas (por retorno/coste)

| # | Propuesta | Coste | Cómo saber que funciona |
|---|---|---|---|
| P1 | **Lecturas sin lease y visibles desde el principio.** Añadir los 12 comandos de `READ_ONLY_COMMANDS` al catálogo inicial, o sacar el catálogo inicial de ese conjunto en vez de mantener una lista a mano. No hay riesgo: esas tools ya se pueden llamar hoy sin estar listadas (§3.2, punto 1). Además, la valla de encolado tiene que dejar pasar las lecturas sobre una corrida sin dueño: hoy `_enqueue_run_rejection` ignora `mutation` (§3.2, punto 9). Es una decisión deliberada, con un test sellado que la protege, así que hay que decidirla, no solo cambiarla. | S-M | T5 («lista jugadores») sale bien sin lease en la prueba de §3.3. |
| P2 | **Lease atado a la sesión viva.** El proceso `--client` renueva el lease mientras stdio esté abierto, con un tope de inactividad configurable (p. ej. 30 min), y envía `list_changed` al adquirir, soltar y caducar. El heartbeat deja de ser cosa del agente. | M | Un agente que espera 5 min entre llamadas conserva la corrida; test de integración con reloj falso. |
| P3 | **`dayz_test_run` adopta el lease del llamante** en vez de exigir que lo suelte, e incluye `@DayZ_MCP` por defecto cuando el proyecto no lo trae. Se quita la regla y su test de prosa. | S-M | T1 sale bien siguiendo las instrucciones del servidor tal cual. |
| P4 | **Un solo sobre de respuesta:** `{ok, error_code, cause, remedy, retryable, next_step: {tool, args}}` para todo, también para lo que hoy es un `ToolError("bad_flags")` pelado. `next_step` debe ser el siguiente paso de la tarea, no un heartbeat. | M | Un validador que recorre todas las tools con entradas malas y exige los campos. |
| P5 | **Esquemas autoexplicativos:** `Field(description=...)` con unidad, rango y ejemplo en cada parámetro; descripción de tool de ≤300 caracteres con un ejemplo; las reglas largas a recursos MCP (`dayz-mcp://docs/<tool>`). | M | Tokens del catálogo (objetivo: ≤8k el completo, ≤3k el pack pequeño) y la prueba de §3.3. |
| P6 | **Pack «starter» por tareas:** `launch_and_wait_ready`, `spawn_at` (con `place_safely` y `surface_query` dentro), `wait_for_log`, `capture_object` y las lecturas. Hoy los playbooks son 4 y no están en `local8b`; son la palanca natural. | M | Porcentaje de las 5 tareas fijas que resuelve un 8B real (Qwen en la 3090) solo con el pack. |
| P7 | **Separar la superficie del producto de la del flujo del autor:** `pipeline_*`, `playbook_reload` y los LOW-LEVEL de sesión, a un pack `dev`. | S | Número de tools del pack por defecto. |
| P8 | **Evaluación de agente débil en CI:** 10 tareas fijas, planes validados contra los esquemas reales como en §3.3 y, más adelante, repetición con el juego. Pasa a ser la métrica de regresión de la experiencia de agente. | M | Mantener y subir el porcentaje de planes válidos por pack en cada PR. |

**El principio detrás de las ocho:** si una regla cabe en la API (un valor por defecto, un orden automático, idempotencia, adoptar un estado), va a la API. La prosa queda para lo que el motor impide arreglar.

---

## 4. Lo que aportaron las lanes FN por módulo

Solo entra lo que pasó la verificación: la `EVID` aparece literal en la línea citada y yo leí el hallazgo. Las salidas crudas están en `evidencia/`. Las notas son de cada lane; las mías están en §6.

### 4.1 Capa de tools y experiencia de agente (R1a · 12/12 citas exactas)
- **Ocho formas distintas de responder** a un error o un resultado: `ToolError` con código pelado (`server.py:4742`, `:4783`); dict con `ok`/`error` al que se inyecta `next_step` (`:805-824`); `next_step=` metido en un string (`agent_loop.py:66-74`); `ready`/`reason` sin `ok` (`server.py:571-622`); un mapa de motivo a tool sin argumentos (`:220-237`); `next_step` con `args: {}` siempre vacío (`:707`); recetas en string (`:134-150`), y un conjunto de códigos remotos sin tabla de remedios (`:251-300`).
- **Sobre que propone:** `{ok, data, error:{code, cause, remedy, retryable, retry_after_ms}, next_step:{tool, args}}`, siempre igual y como añadido a lo que hay (no rompe consumidores actuales).
- **Dos trampas que no tenía** (y que he comprobado): `session_status` es la tool más tentadora para «mirar cómo va» y no renueva el lease (`server.py:4499`); y `wait_for` tiene `timeout_s=180` por defecto, mientras que su condición `log_matches` no renueva el lease (lo dice su propia descripción) y el TTL es de 120 s. **Esperar una línea de log con los valores por defecto puede costar la corrida.**
- **Macros:** `dayz_start_ready` (arrancar y esperar readiness), `dayz_spawn_and_read` (superficie, spawn y telemetría), `dayz_capture` (cámara, captura y restauración) y `dayz_list_players`. Un pack para 8B con esas macros y sin las variantes de sesión de bajo nivel.
- **Valores por defecto:** que un `wait_for` que agota el tiempo no devuelva `ok:true`, que `world_spawn` resuelva la Y del terreno si no se le pasa, y que la captura use `frames=2`.
- **Sus notas:** contrato de errores 2, descripciones y esquemas 2, disclosure y packs 3, gestión del lease 2. Son duras, pero están bien argumentadas desde el punto de vista de un 8B.

### 4.2 Ciclo de vida de corridas (R3a · 10/10 citas exactas)
- **Máquina de estados:** `STARTING`, `RUNNING`, `RUNNING_IDLE`, `STOPPING`, `EXITED` y `UNRECONCILED`, persistida en `runs.json` v1 con escritura atómica (`process_lifecycle.py:844,957,977`) y validada en `RunRecord.validate()` (`:796`). Tabla completa en `evidencia/R3a_process_lifecycle.md`.
- **Proporcionalidad, con criterio.** No propone borrar la idempotencia por `operation_id` y sha, las reservas, la auditoría bloqueante, el reap ni la clasificación owned/gone/foreign antes de matar. Cada una cubre un escenario real cuando varias sesiones comparten el juego. El coste está en la legibilidad, no en garantías que sobren.
- **Descomposición:** `_start_run_reserved` en 6 piezas (`admit_start_request`, `prepare_steam_if_client`, `persist_provisional_start`, `supersede_role_if_needed`, `launch_and_identify`, `persist_start_success`) y `stop_run` en 5 (`resolve_stop_target`, `classify_stop_processes`, `audit_stop_plan`, `terminate_stop_processes`, `finalize_stop_run`). `LifecycleAuthority(session_id, lease_id, reservation_id)` sustituye a la tupla, y las transiciones pasan por helpers con nombre (`mark_running`, `mark_idle`…).
- **Portabilidad:** `ProcessLaunchBackend`, `ProcessIdentityBackend`, `NetworkProbeBackend`, `DayZBinaryPolicy` (hoy los `.exe` están fijos en el código, `:53-55`, `:1789-1800`) y `WindowCloseBackend` opcional. Con dobles de esas piezas, la lógica se puede probar en CI sin juego.
- **Corrección mía:** su único P1 (transiciones escritas a mano en `admin_reconcile`, `process_lifecycle.py:4737-4746`) es real, pero es mantenibilidad y lo bajo a P2.
- **Sus notas:** corrección 6, proporcionalidad 4, legibilidad 3, testabilidad 4, portabilidad 2.

### 4.3 Bridge Enforce, lado servidor (R5a · 12/12 citas exactas)
- **Robustez, con escenario:**
  - **El resultado se pierde si falla el POST.** `OnResultError` solo lo registra en el log (`MCPBridge.c:3357-3360`, `MCPCallbacks.c:87-94`). Lo he comprobado. Es el E-ERR-01 de la auditoría del 09-07 y sigue igual.
  - **Un spawn que agota el tiempo deja su entrada** en el registro de objetos de runtime (`:603-604` frente a `PostJobTimeout`).
  - **`m_Jobs` no tiene cota visible** y también bloquea la admisión de polls (`:232`).
  - Señaló también que `ReloadKeyAfterFailure` no adopta una URL nueva, pero es deliberado y está documentado («A changed url is deliberately NOT adopted», `:419-420`). No lo cuento como riesgo.
- **Un `world_spawn` retrasa todo lo que viene detrás en su lote** (`:295-310`). Lo he comprobado. Es a propósito y solo cuesta latencia, así que lo dejo en P3.
- **Tick:** `IsSpawnReady` hace una búsqueda espacial por cada job y cada tick (`:3038`); las capacidades se recodifican en cada poll aunque sean constantes (`:251`), y `GetWorldSize()` se pide por comando (`:2695`). Los tres se pueden cachear.
- **Versionado:** una lista de versiones compatibles en el daemon, más un error `bridge_outdated` con `actual`/`esperada` y la acción «reempaqueta», que el daemon devuelve *antes* de encolar nada. Funciona también con los PBO de versión "10" ya desplegados, sin tocarlos. Encaja con C2.
- **Dispatch:** es una cadena de `if` por nombre (`:556`). Con 30-60 verbos, la propuesta es una tabla de metadatos por verbo (nombre, peer, si necesita lease y la función que lo atiende).
- **Sus notas:** arquitectura 6, robustez 7, rendimiento 7, mantenibilidad 6.

### 4.4 Coordinación de sesiones (R2a)

Fue la lane más difícil. Con thinking se quedó muda: gastó los 24.000 tokens en razonar y el contenido salió vacío (`finish=length`). Sin thinking entregó un resultado irregular: 5 de 6 citas exactas y errores de fondo que obligan a filtrar. Me quedo con esto, comprobado por mí:
- **Estados del lease:** `idle`, `queued`/reserva, `granting` (`_grant_inflight`), `active`, `releasing`, `handoff_pending` y `audit_fault`. Son siete conceptos con sus propias transiciones, y sus salidas se comprueban en muchos sitios. Por eso `acquire` mide 464 líneas.
- **La auditoría falla en cerrado y bloquea la cola entera.** Si falla la escritura de la auditoría de un handoff, cualquier `acquire`/`wait` en cola recibe 503 mientras no haya nadie activo (`session_coordination.py:3220-3232`). Es una decisión coherente con la regla fail-closed, pero tiene un coste de disponibilidad real: un disco lleno o un fichero bloqueado deja a todos los agentes fuera. **Qué ayudaría:** el remedio ya existe como comando de admin (`admin_cli.py audit-repair --reason …`, `admin_cli.py:54-55,108-124`). Falta que el 503 que recibe el agente lo nombre, junto con el fichero afectado, y un test que lo compruebe.
- **La I/O se hace con el lock suelto** (p. ej. el cleanup, `:2376-2385`), así que los demás clientes pueden entrar. Hay que revalidar el estado a la vuelta, y eso explica los chequeos repetidos: es correcto, pero caro de leer.

Descarto:
- Que esperar al cleanup «bloquea a los demás por el lock». No es así: se suelta.
- Que el «box» sea una caja del juego. Es la ocupación de la máquina/juego.
- Las cifras inventadas («3,5 millones de líneas»).
- La propuesta de hacer la auditoría fail-open, porque contradice la política fail-closed del proyecto.

Sí comparto su conclusión direccional: **para 2-4 agentes en una máquina, WAL + snapshot + tombstones + expiry grace + box FIFO es mucho aparato.** Pero la decisión de qué recortar necesita el escenario real que justificó cada pieza, y eso está en el historial de fichas, no en el código.

### 4.5 Capa de tools con el fichero entero (W4a · 150k tokens de entrada · 13/15 citas exactas y 2 con la línea desplazada)
La mejor entrega de la sesión. Lo que aporta, ya comprobado:
- **Partición concreta** en ~15 módulos bajo `tools/dayz_mcp/tools/`:
  - de contratos: `contracts/constants.py`, `envelope.py`, `readiness.py`, `disclosure.py`, `recipes.py`, `wire_errors.py`, `capabilities.py`, `fingerprint.py`, `wait_log.py`…
  - uno por dominio con sus tools: `session.py`, `lifecycle.py`, `world_server.py`, `vehicle_client.py`, `camera_client.py`, `client_ui.py`, `capture.py`…
  - un registro con un `ToolContext(runtime, config, log_marker_state, observe_sources)` explícito que sustituye a las closures.
  - Regla de dependencias: un módulo de tools nunca importa otro módulo de tools.
- **Los 4 parcheadores, uno a uno:**
  - `_describe_run_parameters` se puede declarar entero con `Field(description=…)`; es el más barato de quitar.
  - `_patch_closed_tool_schema` se declara en parte (`extra="forbid"`), pero su mensaje propio de argumento desconocido no.
  - `_patch_mode_enum_from_authority` no se puede declarar: el enum se lee de una autoridad en cada llamada, y su docstring lo exige (`server.py:2666-2672`).
  - `_patch_public_argument_alias` expone `from`, que es palabra reservada de Python. Queda pendiente si `Field(alias=…)` funciona con FastMCP 1.27.2 [VERIFICAR-API].
- **El acoplamiento más frágil, comprobado por mí:** tres de los parcheadores sustituyen con `object.__setattr__` el método privado `fn_metadata.call_fn_with_arg_validation` de FastMCP y dependen de su firma interna (`server.py:2588`, `:2662`, `:2689`). Una actualización de `mcp` puede cambiar la validación de argumentos sin que falle nada visible.
- **Sobre único sin romper nada:** un `wrap_tool` común que solo *añade* campos; los `ToolError` actuales pasan a `(code, hint)` partiendo por `; `. Un matiz importante: FastMCP serializa el `ToolError` como texto, así que durante la migración hay que emitir a la vez la forma string y la forma objeto.
- **Plan en 10 pasos, cada uno con su gate:** el catálogo exportado tiene que salir idéntico byte a byte antes y después, usando `canonical_json_bytes`, que ya existe (`effective_schema_core.py:211`). Curiosidad: esa función está definida tres veces en el paquete (`tool_registry_fingerprint.py:301` y `vehicle_trace.py:829`).
- **Sus notas:** estructura 3, extensibilidad 2 (una tool nueva obliga a tocar a mano `build_app`, `PUBLIC_NEXT_TOOLS` y `_INITIAL_CATALOG_NAMES`), consistencia del contrato 4, acoplamiento con FastMCP 3.

### 4.6 Núcleo entero: coordinación, loopback, daemon, cliente y supervisor (W4b · 170k tokens de entrada)
Entrega corta. Usó otra etiqueta de severidad, así que sus hallazgos no pasaron por el verificador y los comprobé a mano. Aporta dos cosas nuevas y comprobadas:
- **La valla de encolado rechaza también las lecturas sobre una corrida sin dueño** (`loopback.py:1386-1414`, llamada en `:1476`). Cambia P1 (§3.2, punto 9; §3.4).
- **La gracia de 90 s tras caducar un lease** (`LEASE_GRACE_S`, `session_coordination.py:16`; `:2194`) reserva la reconexión al antiguo dueño.

Su análisis de P2 es flojo: propone como tope de inactividad el mismo TTL de 120 s, que no resuelve nada. Me quedo con el diseño de §3.4 (P2): renovar mientras el stdio siga vivo, con un tope de inactividad independiente del TTL.

### 4.7 Launcher, identidad y seguridad del host, con sus cuerpos (W4c1 + W4c2 · 78k y 71k tokens de entrada)
El intento de una sola lane con 145k entró en bucle. La partí en dos mitades de 71-78k, y las dos entregaron: W4c2 con 12/12 citas exactas, aunque descriptivas; W4c1 con los hallazgos en otro formato, así que la tomé solo por su tabla de veredictos. Los veredictos, una vez cruzadas las dos:

| Capa | ¿Aplica a 127.0.0.1 y un usuario? | Veredicto |
|---|---|---|
| Keyfile pinneado (`pinned_keyfile.py`) | Parcial: otros procesos y agentes del mismo usuario, symlinks y hardlinks | **Mantener** |
| Transporte acreditado + credencial | **Sí**: otro proceso puede escuchar en el puerto y quedarse con la key | **Mantener** |
| Contrato de política del daemon, política normal | Parcial (coherencia) | **Mantener**; bootstrap **opcional** |
| Guardia de procesos nativos | **Sí**: la reutilización de PIDs es real | **Mantener, en el núcleo** |
| Registro con sha e identidad de 4 campos | Parcial | **Mantener dentro del extra** (el mejor coste/garantía del conjunto) |
| Sellado y verificación del bundle (tres builds reproducibles) | Parcial: cubre despistes al copiar, no a un atacante con escritura | **Opcional y simplificar** |
| Transacciones de instalación CAS + receipts + rollback | Parcial: con un usuario, un install simple con backup y sha basta | **Opcional** |
| Autoridad de rutas (`request_path_authority.py`) | Sí, pero solo si `dayz_test_run` recibe rutas de un agente | **Opcional** (en el extra) |
| `security_runtime_audit.py` | No como runtime; sí como guardia de regresión del repo | **Solo desarrollo** (CI o `tools/checks/`) |
| `identity_migration.py` (v1→v2) | Temporal | **Retirable** cuando no quede v1 |

Descarto dos «defectos» de W4c1 tras comprobarlos:
- **El `getattr` de `native_launcher_backend.py:1671` no contradice el comentario de `:903-907`:** lee un atributo de datos, y la regla del comentario es sobre llamadas resueltas en runtime.
- **El «8191 frente a 8192» no es un fallo de uno:** productor y consumidor de la política usan 8191 (`normal_daemon_policy.py:74,84`; `native_launcher_backend.py:503`), y el 8192 es de otro campo (`:496`).

### 4.8 Bridge Enforce, lado cliente (W5 · 86k tokens de entrada · 14/15 citas exactas y 1 con la línea desplazada)
- **Riesgos, con el mío comprobado:**
  - Un `camera_set` que falla deja el control del jugador bloqueado (C7; comprobado).
  - Un resultado que falla al hacer POST se pierde igual que en el servidor (`MCPClientBridge.c:4320`).
  - Si `m_Ctx` es nulo, `PostResult` sale sin dejar ni una línea de log (`:4283`); el servidor sí registra la caída.
  - Cada `PostResult` crea un callback nuevo, sin pool (`:4297`).
  - `CountUiWidgetsNamed` recorre el árbol entero sin cota si el nombre no existe (`:2328`): es el E-P06 del 09-07 y sigue igual.
- **`MCP_CarScript`** preasigna hasta 8.192 muestras por traza (`MCP_CarScript.c:199`) y hace trabajo en cada `OnInput` de cada coche local aunque no haya control ni traza activa (`:796`). Esto último es el E-TICK-02 del 09-07.
- **`MCPBridgeBase` función a función:**
  - 8 funciones idénticas que suben tal cual: `DrainPending`, `OnPollError`, `OnPollTimeout`, `EncodeQueryValue`, `GetPollVersion`, `VectorToArray`, `StringHasPrefix` y `PostCommandError`.
  - Las demás difieren en un literal (etiqueta de log, token de cola) o en el tipo de callback, y se resuelven con hooks por lado.
  - 2 solo comparten nombre (`GetGame` y `Print` son globales del motor).
  - Hay que elegir la versión estricta donde divergen (`IsFiniteFloat`, comprobado).
  - Tiene una tabla completa en `evidencia/W5_enforce_client.md`.
- **Posible fallo compartido, sin verificar:** según W5, `EncodeQueryValue` convierte a ASCII sin corregir el signo, así que los bytes 0x80-0xFF saldrían como `?` en los dos puentes. Con una key hex no importa; sí importaría con un nombre o una ruta no ASCII en la query.
- **Sus notas:** `MCPClientBridge` 6, `MCP_CarScript` 7, duplicación con el servidor 4 («no es un clon, es una bifurcación que ya diverge»).

### 4.9 Piloto de limpieza: la familia de tests de lease (W4d · 165k tokens de entrada · 14 de 16 pares con las dos citas exactas)
La familia son 14 ficheros, 348 tests y 11.952 líneas: el fichero base, tres `bug046`, tres `task7`, dos `0ab2`, `session_acquire_wait`, `session_handoff`, `reload_lease_recovery`, `session_http` y los de invariantes y fence. Es la familia con más ficheros por ficha de toda la suite.
- **1 duplicado real**, comprobado leyendo los dos tests: `test_0ab2_grace.py::test_n3_stranger_wins_after_grace` y `test_bug046_lease_queue_liveness.py::test_stranger_grants_after_spec_grace_window` hacen lo mismo; solo cambia el helper que construye el coordinador.
- **15 casi-duplicados**, cada uno con un caso límite propio que hay que conservar: el límite exacto TTL+gracia, un fallo de auditoría en *prepared* frente a uno en *commit*, una colisión de identidad a mitad de la gracia, la ruta HTTP frente al coordinador… Se fusionan con `subTest`; no se borran.
- **11 grupos de helpers repetidos**, con firma común propuesta: `FakeClock`/`Clock`, `SequentialIds`/`Sequence`, `AuditSink`/`Audit`, dos `_coord` y `_h4_grace_coordinator`, `_BlockingWal`/`_BlockingWalBoundary`, `SnapshotStore`, `_http`/`http_post`, `LifecycleFixture` importada desde otro test… Comprobé 7 de esas ubicaciones y todas eran exactas.
- **Estructura objetivo:**
  - 10 ficheros por dominio: `test_lease_queue_fifo`, `test_coordination_authority_fence`, `test_coordination_audit_faults`, `test_loopback_authority_quarantine`, `test_session_handoff_reload`, `test_session_http_contract`, `test_process_lifecycle_authority`, `test_takeover_contract`, `test_client_acquire_wait` y `test_test_support`;
  - un `helpers_lease_coordination.py` común;
  - cada clase actual con un destino asignado.
- **Plan en 7 pasos**, cada uno con el mismo número de tests recogidos como gate: helpers, quitar los imports entre tests, dividir por dominio, fusionar los casi-duplicados conservando sus casos y, solo al final, borrar D01.
- **Sus notas:** cobertura de conducta 8, redundancia 4, acoplamiento 2 (los tests tocan internos como `coordinator._condition`, `_active` y `_releasing`), legibilidad 4.

**Qué significa para toda la suite.** Esta familia era la mejor candidata a tener redundancia y apenas la tiene: de 348 tests, 1 es duplicado y 15 casi. Sumado al censo mecánico (§5: no hay tests copiados y los helpers repetidos rondan el 1 %), la conclusión es que **limpiar la suite significa reorganizarla, no borrarla**. Pasaría a ficheros por dominio, helpers comunes, cero imports entre tests y tiers, lo que hace mucho más fácil recorrerla para agentes y personas. El número de tests bajará poco. [INFERENCIA extrapolada desde una familia; la cobertura por fichero (T2) lo confirmaría para el resto].

### 4.10 Tests (L4)
Resumido en §5: agrupa bien las familias y su lista de borrado no sirve.

### 4.11 Descartado
- **L1** (tools, sin thinking): se cortó a mitad y se inventó cifras.
- **L3** (ciclo de vida y launcher, sin thinking): 10 de 25 evidencias no existen en el fichero.
- **L2** (núcleo, sin thinking): solo aprovecho el mapa de componentes. Su hallazgo principal («`acquire` bloquea el lock 50 ms») es falso, porque `Condition.wait` libera el lock mientras espera.

---

## 5. Tests: diagnóstico y plan de limpieza

**Qué hay:** 262 ficheros, 123.567 líneas y 4.093 métodos de test, 2,2 veces el código que prueban. La suite ha pasado de 1.261 tests (2026-07-27) a 2.271 (08-30) y a 3.313 (09-09, 250-320 s por corrida según los logs de `reviews/`). Hay 129 líneas de import entre tests repartidas en 70 ficheros, y ni un helper compartido (`tests/helpers_*` no existe). 18 ficheros mencionan `Popen`, 12 de ellos sin ningún `skipUnless`/`skipIf`, y no hay un tier lento separado (ninguna referencia a una variable tipo `DAYZ_MCP_LIVE`). Unos 29 ficheros comprueban el texto de las descripciones de las tools (grep heurístico). Quedan 26 `.bak` sin trackear en `tools/tests`. No hay CI. Desde el 09-07 se han añadido 75 ficheros y no se ha borrado ninguno.

**Dónde están las líneas (censo por AST, determinista):**
- El 69 % de la suite está dentro de los propios métodos de test: 85.982 líneas. La mediana es de 15 líneas por test, el p90 de 44, y solo 171 de los 4.093 pasan de 60.
- `setUp`/`tearDown` suman el 2 % y los helpers de nivel superior el 9 %.
- **Los tests no están copiados y pegados:** comparando el AST normalizado de funciones de 6 líneas o más, entre métodos de test solo aparece 1 grupo de clones, dentro de un mismo fichero.
- **Los helpers sí se repiten:** hay 67 nombres definidos en dos o más ficheros (`_method_body` en 25, `_policy` en 8, `_content_json` en 7, `FakeClock` en 5, `FakeGuard` en 4) y 36 grupos de clones exactos entre ficheros, unas 870 líneas.

**Conclusión:** la suite es grande porque cubre muchas conductas pequeñas, no porque esté inflada. Unificar helpers ahorra ~1 % de líneas. Para reducir volumen de verdad hay que decidir qué conductas son redundantes, familia a familia y con cobertura. Por eso §5 empieza por la estructura (barato y seguro) y deja la poda para cuando haya datos.

**Por qué ha crecido así:** el patrón es un fichero por ficha (`test_fb_8604_*`, `test_0ab2_*`, `test_bug046_*`, `test_task7_*`), que importa fakes de otros tests y fija la conducta arreglada. Cada uno tiene sentido por separado; juntos forman una suite sin estructura por dominio.

**Qué dijo la lane FN (L4) y qué vale:** clasificó los 262 ficheros sin dejarse ninguno y agrupó bien las familias: control de procesos, transporte, superficie MCP, Steam/launcher, docs/meta, in-game y fichas. **Su lista de borrado no es fiable.** Marcó DELETE «con confianza alta» 25 ficheros (9.663 líneas), casi todos tests de regresión con nombre de ficha y con el número de tests como único motivo. Entre ellos `test_000_path.py`, que es el bootstrap de `sys.path` de toda la suite. Además se inventó cifras («cobertura 92,5 %», «−55 %»). Su utilidad real es la lista de candidatos a fusionar.

**Plan por fases (cada una se cierra con la suite verde y un número):**

| Fase | Qué | Riesgo | Cómo se verifica |
|---|---|---|---|
| T0 | **Reglas que paran el crecimiento:** una regresión va al fichero de su familia, no a `test_<ficha>.py`; prohibidos los imports entre tests; toda prueba con proceso real se marca lenta. Un check en CI lo hace cumplir. | Nulo | El check falla con un fichero nuevo que las incumpla (control negativo). |
| T1 | **Mecánico:** `tests/helpers/` con los fakes compartidos, sustituir los 129 imports entre tests, borrar los 26 `.bak` sin trackear y mover a `tools/checks/` los meta-tests de docs y lint. | Bajo | Mismo número de tests ejecutados, suite verde, 0 imports entre tests. |
| T2 | **Poda con datos:** cobertura por fichero de test (`coverage` con contextos dinámicos). Un fichero cuyas líneas cubiertas ya cubren otros y cuyas aserciones no aportan una conducta nueva pasa a ser candidato de fusión. Mutación dirigida en los de ficha antes de fusionarlos. | Medio | Cobertura por línea del paquete igual o mayor tras cada fusión y los mutantes que mataba siguen muertos. |
| T3 | **Reorganizar por dominio** (es la palanca principal, §4.9). Partir los ficheros-dios (`test_process_lifecycle.py` 5.160 líneas, `test_native_launcher_backend.py`, `test_daemon.py`…) y absorber los ficheros por ficha en su familia. El mapa de la familia de lease ya está hecho (§4.9): se puede ejecutar como primer lote. | Medio | Mismos IDs de test antes y después, sin pérdidas (salvo los duplicados declarados y verificados). |
| T4 | **Tiers:** rápido en cada PR, lento con proceso real bajo un flag y nocturno in-game. | Bajo | Duración del tier rápido (objetivo: menos de 90 s). |

La estimación de la auditoría del 09-07 (−30 % sin perder señal) **me parece optimista**. El texto duplicado literal ronda el 1 %, así que un −30 % exigiría que casi un tercio de las conductas estuviera probado dos veces, y eso nadie lo ha medido. La fase T2 es la que da el número real; la lane de la familia de lease (§4.9) es el piloto, y apunta a que la redundancia real es baja. Puedo lanzarla cuando quieras: corre la suite con cobertura en esta máquina (~5-10 min) y conviene hacerlo cuando no haya otra sesión usando el MCP.

---

## 6. Valoración honesta por secciones

| Sección | Nota | Por qué | Qué la subiría |
|---|---:|---|---|
| Diseño de arquitectura (concepto) | **8** | Tres actores bien separados, transporte que respeta el motor, daemon con lease para varios agentes, censo, readiness con motivo y fail-closed. Todo documentado con citas al motor. | Decidir si multi-instancia y servidor remoto entran en el producto y dejarlo por escrito. |
| Capa de tools (`server.py`) | **4,5** | Las tools simples son limpias. Pero `build_app` mide 2.617 líneas, hay 4 parcheadores de esquema a posteriori, usa internos privados de FastMCP y mezcla estilos de error (215 `raise ToolError`, códigos pelados y recetas). Es el fichero con más fixes. | M1 y P4-P5. |
| Núcleo daemon / coordinación | **5** | Cuidadoso, auditado e idempotente, y encaja los casos límite. Pero `acquire` es una máquina de estados de 464 líneas, con tombstones, WAL, fault-store y un gate de auditoría: aparato de servicio distribuido para 2-3 agentes en una máquina. | Descomposición con tipos con nombre y recortar lo que no aguante la pregunta «¿qué escenario real rompe esto?». |
| Ciclo de vida / launcher / seguridad | **5** | El ciclo de vida es ingeniería seria y sus garantías se sostienen (lanzamientos idempotentes, reservas, reap), pero se lee mal: funciones de 270-470 líneas, tuplas posicionales y Windows por todas partes. En seguridad, revisada con sus cuerpos (§4.7), la mitad se justifica incluso para 127.0.0.1 (keyfile pinneado, transporte acreditado, guardia de PIDs). La otra mitad va a un extra (sellado del bundle, transacciones, autoridad de rutas), a desarrollo (la auditoría en runtime) o se puede retirar (la migración v1). Entre todo, el 46 % del paquete. | M3 y M4: descomponer, tipos con nombre, backends de plataforma, el launcher como extra y la auditoría a CI. |
| Mod Enforce | **6** | Funciona in-game, sortea bien las limitaciones del motor (`*_now`, `SetHeader`, `MakeScreenshot`), anuncia sus capacidades y es defensivo (watchdog, backoff, colas con cota). Pero el transporte es una bifurcación que ya diverge entre los dos puentes, los resultados que fallan al enviarse se pierden, un `camera_set` fallido deja el control bloqueado, la versión está congelada y hay dos árboles de fuente. | M2, C1, C2 y C7. |
| Experiencia de agente (sobre todo débil) | **4** | Buenas ideas (allowlist de `next_step`, `local8b`, disclosure, `_bad_args`, `bridge_status.reason`), pero fragmentadas y a veces contradictorias (§3.2). El 97 % de los parámetros sin describir, reglas en prosa y un lease que exige atención constante. | P1-P8. |
| Tests | **4** | La cantidad y el celo son reales y cazan regresiones. Pero la suite es 2,2 veces el código, no tiene estructura, tiene 70 ficheros acoplados entre sí, no tiene tiers ni CI, y hay tests que fijan frases de descripciones. | §5 T0-T4. |
| Documentación e higiene del repo | **3** | Trazabilidad obsesiva (HANDOFF, gates, reviews). Pero hay 50k ficheros y ~14 GB sin trackear en OneDrive, 31 backups en la raíz, cifras que no cuadran, `CLAUDE.md` fuera de git y un HANDOFF que da por buena la divergencia del addon. | Fase 0 del roadmap. |
| **Global** | **5** | Buen diseño y ejecución que ha crecido sin poda. Nada está roto de raíz, y lo que más cuesta es barato de empezar a pagar. | |

Comparación con la auditoría del 09-07 (global 6,1): esa nota puntuaba el estado de entonces. Esta baja porque su plan no se ejecutó mientras el código crecía, y porque aquí se han medido cosas que esa no miró (higiene, árboles del addon, versión congelada, esquemas sin describir).

---

## 7. Roadmap de producto

Cada fase tiene un «terminado cuando» ejecutable. Un aparato sin consumidor no entra.

**Fase 0 — Higiene y fuente única (1-2 días)**
- Sacar `_s0`, `_fase*`, `_poc`, `_step0`, `_compile` y los directorios de corridas de `reviews/` a un archivo fuera de OneDrive y del repo, y añadir a `.gitignore` lo que se genera. Los 31 backups de la raíz (`HANDOFF.md.bak-*`, `*_bak_*`) también salen del repo: el historial de `HANDOFF.md` ya está en git.
- **Una sola fuente del addon:** `DayZ_MCP` pasa a ser una salida del empaquetado (o se borra), y `PROJECT-MAP.md` y `HANDOFF.md` se corrigen en consecuencia.
- **Versión de puente que falle con remedio:** subir la versión y hacer que el daemon compare el censo `caps=` con lo que espera. Si falta un verbo, `bridge_status.ready=false` con `reason=bridge_stale` y el remedio «reempaqueta el PBO».
- CI mínima (GitHub Actions `windows-latest`) que corra la suite rápida; `CLAUDE.md` y `AGENTS.md` en git con la cifra buena.
- Dos arreglos de una línea en el mod: el `camera_set` que deja el control bloqueado (C7) y el `IsFiniteFloat` laxo del cliente (§2.3, M2).
- *Terminado cuando:* `git status` limpio en menos de 2 s, un PBO viejo da `ready=false` con remedio, y la CI está verde en `main`.

**Fase 1 — Experiencia de agente débil (≈1 semana)**
- P1, P2, P3 y P4 (lecturas sin lease, lease atado a la sesión, `dayz_test_run` que adopta el lease, sobre único).
- P7 (superficie de producto frente a la de desarrollo) y el arnés de §3.3 convertido en la prueba P8.
- *Terminado cuando:* las 5 tareas fijas de §3.3 dan planes válidos con el pack pequeño, las instrucciones del servidor se pueden seguir literalmente, y un agente que espera 5 min no pierde su corrida.

**Fase 2 — Estructura (2-3 semanas, se puede solapar)**
- M1: tools por módulo, con registro y decorador común y esquemas declarados (sin parcheadores). P5 cae aquí de forma natural.
- M2: `MCPBridgeBase` en Enforce.
- M3: descomponer `acquire`, `_start_run_reserved` y `stop_run`, con `LeaseAuthority` con nombre.
- Tests T0-T3 (§5), empezando por la familia de lease con el mapa de §4.9 como primer lote.
- *Terminado cuando:* ningún fichero del paquete pasa de ~1.500 líneas, ninguna función de ~150, no hay imports entre tests, y la cobertura por línea no baja.

**Fase 3 — Producto (según objetivos)**
- P6, el pack «starter» por tareas, y superficie estable versionada (el «paquete mínimo de verbos» del 09-07, §10.4).
- M4: el launcher y la seguridad del host como extra opcional.
- Si se quiere el caso «un agente opera un servidor»: transporte MCP por HTTP con auth y `ProcessBackend` para Linux. Si se quiere multi-instancia, clave de instancia en todas las capas.
- *Terminado cuando:* un tercero instala el núcleo en una máquina limpia siguiendo `QUICKSTART.md` y un 8B completa las tareas del pack starter.

---

## 8. Método, lanes FN y lo que no se ha verificado

**Métricas propias (deterministas, sobre `HEAD 57269d2`):**
- AST de los 81 módulos: tamaño de cada función, imports internos (fan-in/fan-out) y esqueletos. Con `git log --numstat`: churn por fichero, altas y bajas por semana, y ficheros añadidos o borrados desde el 2026-09-07.
- Catálogo real: `build_app` construido offline en modo embedded con `port=0`, sin lifespan, así que no arranca nada ni abre puertos. De ahí salen nombres, descripciones, esquemas, parámetros sin describir y tamaños.
- Addon: sha256 de cada fichero en `git show HEAD:addon/...`, en el árbol de trabajo y en `DayZ_MCP\`. Duplicación entre puentes: líneas normalizadas de 25 caracteres o más, sin comentarios, que aparecen literalmente en ambos ficheros.
- Higiene: `git status --porcelain --untracked-files=all` y `du -sh` de los directorios grandes.
- Los scripts, junto con los briefs de todas las rondas, están copiados en el vault: `ObsidianVault/AI/10_Projects/DayZ_MCP/research/2026-09-25-vista-alto-nivel-scripts/`, con un README. No van al repo, que es público, porque llevan la dirección del GX10 en la LAN.

**Lanes FN** (GX10, `Qwen/Qwen3.8-Flash-Next`; `/v1/models` comprobado: raíz `hibrid48` y 262.144 de ventana; API directa por `:8001`, porque `prime-agent` no admite dos sesiones a la vez):
- **Ronda 1:** 4 lanes con thinking y 100-150k tokens de entrada cada una. **Las 4 murieron a los ~600 s con `502 {"message":"timed out"}` del proxy, sin ninguna salida.**
- **Ronda 2:** las mismas 4, sin thinking y con streaming. Entregaron (`stop`), pero con baja calidad. L1 se paró a mitad y se inventó cifras. L3 dio 25 hallazgos, 10 de ellos con una evidencia que no existe en el fichero. L4 clasificó los 262 ficheros, pero su lista de borrado no sirve (§5). L2 dio 5 hallazgos con evidencia exacta; uno se apoya en un error de semántica: dice que `Condition.wait` bloquea el lock, y en Python lo libera mientras espera.
- **Ronda 3:** 4 lanes enfocadas, con thinking y 26-69k tokens de entrada reales. **R1a, R3a y R5a entregaron con calidad buena: 34 de 34 citas exactas**, entre 664 y 738 s cada una y unos 15k tokens de razonamiento. R2a se quedó muda (`finish=length`, contenido vacío). Al relanzarla sin thinking entregó en 143 s: 5 de 6 citas exactas y errores de fondo (§4.4).
- **Prueba de agente débil:** 6 peticiones pequeñas sin thinking (§3.3).
- **Ronda 4, con contexto completo** (145-170k tokens de entrada, thinking, `max_tokens` 45k y streaming), tras confirmar el dueño que 200k por lane está presupuestado:
  - **W4a (149k)** cerró el turno tras una frase de razonamiento. Relanzada con el encargo repetido también *al final* del material, entregó la mejor revisión de la sesión (13/15 citas exactas y 2 con la línea desplazada, 400 s).
  - **W4c (145k)** entró en bucle dentro del razonamiento («Hallazgo 4 daemon_policy.py 320…») y cerró sin contenido. La partí en dos de 68 y 75k.
  - **W4b (170k)** entregó, aunque corto y con el formato de hallazgos cambiado.
  - **W4d (165k)** entregó al primer intento: 18 min y calidad alta, con 14 de 16 pares con las dos citas exactas.
  - **W4c**, partida en dos (71k y 78k), entregó; **W5** (cliente Enforce, 86k) entregó con 14/15 citas exactas.
  - **Balance:** por encima de 145k, 3 de 5 entregaron (una de ellas tras repetir el encargo al final). Entre 71 y 86k, con el encargo repetido, 3 de 3.
  - La ficha del GX10 en `delegar` queda actualizada con esta medida.
- **Receptor:** `verify_findings.py` comprueba que cada `EVID` aparece literalmente a ±10 líneas de la línea citada, en `git show HEAD`. Además, leí yo cada hallazgo que uso en este informe.

**Dos lecciones para la ficha del GX10**, útiles para `delegar`:
1. El proxy `:8001` corta las peticiones sin streaming a los ~600 s con `502 {"error":{"message":"timed out"}}` (4 de 4, 2026-09-25). La ficha solo preveía el corte del SDK de openai en `prime-agent`. **Con `stream: true` no corta: hubo peticiones de 664-738 s que llegaron enteras** (el SSE sigue llegando de golpe al final, pero el socket no se queda mudo).
2. FN sin thinking y con más de 100k tokens de contexto degenera: respuestas cortadas, bucles y cifras inventadas. Con thinking, un contexto de ≤70k y una pregunta acotada entrega análisis coherentes con todas las citas exactas, a ~30 tok/s por petición con 4 concurrentes. Conviene dar ≥35-40k de `max_tokens`: el razonamiento se come ~15k, y en R2a se comió los 24k enteros.

**Lo que NO se ha verificado:**
- No se corrió la suite de tests ni nada in-game. Hay un HOLD del dueño sobre el tester, la máquina se comparte con otras sesiones y los tests con `Popen` podrían interferir.
- No se midió cobertura, así que la cifra de poda de tests está pendiente (§5, T2).
- Tras la ronda 4, **solo quedan sin revisar** `runtime_state.py` (2.376 líneas, visto en esqueleto), `MCPDialogController.c` y los módulos de Steam, host y doctor (vistos en esqueleto en la ronda 2). `loopback.py` entró completo en W4b, pero esa revisión fue corta.
- Los veredictos de proporcionalidad del launcher y la seguridad (§4.7) son un juicio cualitativo de las lanes sobre el código completo, más mis comprobaciones puntuales (descarté 2 de sus «defectos»). No se ha medido el coste real de mantener cada capa.
- La conclusión de que limpiar los tests es reorganizarlos, no borrarlos, sale de una familia (348 tests) más el censo mecánico de toda la suite. Para el resto, la confirmaría la cobertura por fichero (T2).
- La prueba de agente débil (§3.3) usa FN sin thinking como aproximación a un modelo pequeño, con n=2 por vista. Sirve para ver tendencias, no para dar una tasa.
- Las cifras de la auditoría del 09-07 que se citan (64 módulos, 185 ficheros de test, ~4.867 líneas de `server.py`) salen de ese documento. Las de hoy son medidas propias.
