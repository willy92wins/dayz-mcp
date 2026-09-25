<!-- Salida CRUDA de una lane Qwen3.8-Flash-Next (GX10), sin editar. NO es el informe: puede contener errores.
     Verificación mecánica del receptor (EVID literal a ±10 líneas en HEAD 57269d2): == R1a_agent_ux.md findings: 12 {'EXACT': 12}
     Veredicto del receptor: ver ../INFORME.md §4 y §8. -->



## Resumen
- Un agente 8B fallará antes de empezar si sigue la receta pública y adquiere un lease antes de `dayz_test_run`, porque el ciclo de ciclo de vida pide soltar el lease.
- El contrato de respuesta está dividido entre `ToolError` con código pelado, dicts con `ok/error`, dicts con `ready/reason` y textos con `next_step=tool`.
- Para un agente débil, la guía correcta no es una frase o una tool suelta: necesita `causa + remedio + reintentable + siguiente tool con argumentos`.
- El lease de 120 s, la renovación limitada a cierto tipo de llamadas y la ausencia de heartbeat automático hacen que un agente pequeño pierda el estado sin entenderlo.
- El pack `local8b` y el catálogo pre-lease dejan fuera o invisibles justamente las tools necesarias para tareas reales de mundo, telemetría y captura.
- La mejora principal no es añadir más tools, sino ofrecer macros simples, read-only visibles, auto-lease oculto y errores accionables.

## A. Contrato de respuesta
Formas que veo en el material:

1. **Error elevado con código pelado**
   - `server.py:4742`: `ToolError("bad_purpose")`
   - `server.py:4783`: `ToolError("bad_wait_timeout")`
   - Problema para un 8B: ve un identificador, pero no tiene a mano remedio, reintentabilidad ni siguiente paso.

2. **Resultados dict con `ok`, `error` y `next_step`**
   - `server.py:805` a `server.py:824`
   - Se comprueba `result.get("ok")`, `result.get("error")` y se puede insertar `payload["next_step"]`.
   - Problema: mezcla resultado operativo y guía de siguiente llamada en el mismo plano.

3. **`next_step` como texto plano**
   - `agent_loop.py:66` a `agent_loop.py:74`
   - `with_next_step` añade un token `next_step=<tool>` a un mensaje string.
   - Problema: un 8B puede copiar el nombre, pero no aprende qué args usar ni por qué.

4. **Dict de readiness con `ready/reason`**
   - `server.py:571` a `server.py:622`
   - Ejemplos:
     - `server.py:594`: `return {"ready": False, "reason": s_block}`
     - `server.py:622`: `return {"ready": False, "reason": "no_run"}`
   - Problema: no siempre trae `ok`, `error`, remedio o args de la siguiente tool.

5. **Razones de no-readiness mapeadas a una sola tool**
   - `server.py:220` a `server.py:237`
   - Ejemplo: `server.py:221`: `"no_run": "dayz_test_run"`
   - Problema: si falta contexto para los argumentos, un agente débil se queda a medias.

6. **Siguiente llamada pública como dict con args vacíos**
   - `server.py:700` a `server.py:707`
   - `server.py:707`: `return {"tool": "bridge_status", "args": {}}`
   - Problema: `args` vacío sirve a un humano, no a un agente pequeño con poca tolerancia a la ambigüedad.

7. **Recetas string con semántica de remedio implícito**
   - `server.py:134` a `server.py:150`
   - Ejemplo visible:
     - `server.py:135`: `"lease_required: call session_acquire_wait(purpose=...)"`
   - Problema: son fáciles de leer para un humano, pero frágiles para parsing o acción determinista de un 8B.

8. **Códigos remotos enumerados, sin tabla visible de remediación**
   - `server.py:251` a `server.py:300`
   - Hay un `frozenset` grande de códigos, pero no veo un mapping uniforme a remedios por código.
   - Problema: el agente necesita un contrato estable, no un diccionario implícito en cabecear strings.

**Sobre único que propondría**
- Debería ser siempre el mismo para éxito y error, aditivo y sin romper compatibilidad antigua:
```json
{
  "ok": true,
  "data": { "...": "..." },
  "error": null,
  "next_step": {
    "tool": "surface_query",
    "args": {"x": 4500, "z": 10200},
    "remedy": "obtener y del terreno antes de spawn"
  }
}
```

- En error:
```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "lease_expired",
    "cause": "sin llamadas de renovación durante 120 s",
    "remedy": "re-adquirir el lease antes de mutar",
    "retryable": true,
    "retry_after_ms": 0
  },
  "next_step": {
    "tool": "session_acquire_wait",
    "args": {"purpose": "reintentar_spawn"}
  }
}
```

**Campos mínimos para un agente débil**
- `ok`
- `data` o `result`
- `error.code`
- `error.cause`
- `error.remedy`
- `error.retryable`
- `next_step.tool`
- `next_step.args`
- opcionalmente `error.retry_after_ms`

Si un campo puede fallar por diseño, mejor no usarlo.

## B. Recorrido del agente débil
### T1 — Arrancar servidor y cliente con `MiMod` y confirmar readiness
**Secuencia correcta en material**
1. `session_status` para elegir puerto libre, si el proyecto lo exige.
2. `dayz_test_run(...)`
   - `project=<proyecto aprobado>`
   - `mode="all"` para lanzar ambos
   - si no es `DayZ_MCP`, incluir `'@DayZ_MCP'` como exige el gate de verificación
   - incluir `'MiMod'` en mods
   - no pasar `run_id` en `mode="server|all"`
3. `bridge_status`
4. `wait_for(condition="players_at_least", value=1, ...)`

**Dónde rompería un 8B**
- La instrucción pública dice primero `session_acquire_wait -> bridge_status.ready -> mutating verbs` (`server.py:4681-4685`), pero `dayz_test_run` exige soltar el lease si se tiene.
- En `--client` sin lease, el agente no ve muchas tools auxiliares, aunque `session_status` y `dayz_test_run` sí suelen entrar en la lista inicial.
- Puede elegir puerto a mano sin entender `ports_in_use`, `foreign_ports` o `box.available_for`.
- Si usa proyecto distinto de `DayZ_MCP` sin `'@DayZ_MCP'`, fallará por regla externa y no siempre lo entenderá a primera vista.
- Si `mode="server"` y luego `mode="client"`, se pierde en la matriz de `run_id`.

### T2 — Spawnear un `CivilianSedan` y leer telemetría
**Secuencia correcta**
1. `session_acquire_wait(purpose="spawn_vehicle")`
2. `surface_query(x=4500, z=10200)`
3. `world_spawn(type="CivilianSedan", pos=[4500, <y>, 10200], flags=0)`
4. `telemetry_read(mode="object_at", type="CivilianSedan", pos=[4500, <y>, 10200], ...)`

**Dónde rompería un 8B**
- Sin lease, puede no ver `surface_query` o `world_spawn` a tiempo, aunque algunas no requieran lease operativo.
- Tras la adquisición, `tools/list_changed` sí se intenta (`server.py:4802-4806`), pero el agente no siempre refresca catálogo.
- Puede spawnear con `pos=[x,z]` o con `y` inventado, cuando el flujo correcto es usar el `y` del terreno.
- Si no refresca, no sabe que `telemetry_read` tiene modos cerrados `object_at|fixture_jsonl`.
- Tras un spawn ok, el siguiente paso automático puede empujarlo a `session_heartbeat` (`agent_loop.py:60`) en lugar de a la telemetría.
- Si hay más coincidencias exactas de tipo, `telemetry_read` puede acabar en error de ambigüedad.
- Con `local8b`, directamente no dispone del verbo de mundo.

### T3 — Teletransportar al jugador y esperar `[MiMod] ready`
**Secuencia correcta**
1. `session_acquire_wait(purpose="teleport_and_wait")`
2. `player_teleport(pos=[x, 0, z])`
3. `wait_for(condition="log_matches", pattern="[MiMod] ready", ...)`

**Dónde rompería un 8B**
- Puede olvidar que `player_teleport` necesita 3 coordenadas aunque solo importe plano.
- Puede pensar que `y=0` es inválido, cuando la semántica pública de `player_teleport` es que `y==0` ancla a superficie.
- En `wait_for(log_matches)`, la renovación del lease no es la misma que en probes de jugadores o estado de entidades: `log_matches` es el caso más fácil de quedarse fuera de la renovación efectiva.
- Puede tomar `ok=true` como éxito si la espera caducó con `satisfied=false`.
- Puede usar regex o escapados donde se espera substring literal.

### T4 — Capturar una imagen del coche
**Secuencia correcta**
1. Si el cliente debe mirar al coche:
   - `session_acquire_wait(purpose="capture_car")`
   - `camera_set(cam_mode="lookat", cam_pos=..., look_at=<pos del coche>)`
2. `capture_screenshot(frames=2, ...)`
3. `restore_gameplay()` si se dejó cámara scripted.

**Dónde rompería un 8B**
- `capture_screenshot` puede no estar visible antes de lease, aunque no sea el mutante principal.
- Puede llamar a `capture_screenshot` sin encuadre y obtener una imagen inútil.
- Puede olvidar `restore_gameplay`, dejando cámara scripted y contaminando el siguiente paso.
- La captura no devuelve un único JSON simple, sino imagen + bloque de metadatos; un agente débil puede fallar al parsear.
- Puede considerar correcta una imagen congelada si no entiende `frame_stale` / `distinct_frames`.
- Con `local8b`, no tiene ni cámara ni captura.

### T5 — Listar jugadores sin cambiar nada
**Secuencia correcta**
- `query_all_players(...)`

**Dónde rompería un 8B**
- En modo cliente sin lease, puede no ver la tool de lista de jugadores aunque sea read-only.
- Si no la ve, tenderá a adquirir un lease “por si acaso”, lo cual es exactamente el anti-patrón para 8B.
- Puede llamar a `session_status` esperando una lista de jugadores, porque ve más output y le parece más “explícito”.
- Si el modelo no distingue “estado de coordinación” de “estado de partida”, usará la tool equivocada.

## C. Superficie ideal
Principio: **un 8B no debe gestionar leases, puertos, markers, modos de telemetría ni estados del bridge si no es necesario**.

### 1. Tools macro recomendadas
- `dayz_start_ready`
  - wraps `dayz_test_run` + readiness
  - args mínimos:
    - `project`
    - `mods`
    - `server_or_client_or_both`
    - `auto_wait_ready=true`
  - salida:
    - `run_id`
    - `ready`
    - `next_step`

- `dayz_spawn_and_read`
  - wraps `surface_query` + `world_spawn` + `telemetry_read`
  - args:
    - `type`
    - `x`
    - `z`
    - `read=true`
  - salida:
    - `object_id`
    - `telemetry`
    - `next_step`

- `dayz_capture`
  - wraps optional `camera_set` + `capture_screenshot` + optional `restore_gameplay`
  - args:
    - `look_at`
    - `frames=2`
    - `return_fullres_path=true`

- `dayz_list_players`
  - wrapper directo de read-only, visible siempre
  - cero args o solo `timeout_s`

### 2. Pack mínimo para 8B
Un pack “game” para 8B debería contener al menos:
- `bridge_status`
- `dayz_start_ready`
- `dayz_list_players`
- `surface_query`
- `dayz_spawn_and_read`
- `wait_for`
- `dayz_capture`
- `dayz_test_stop`
- `session_status`
- `pipeline_inbox` / `feedback` solo si se quiere soporte de incidentes

No metería en 8B:
- `session_acquire`
- `session_cancel`
- `session_wait`
- `lease_acquire`
- `session_release` si se puede ocultar
- verbos de mundo de bajo nivel
- UI fino
- `playbook_run`

### 3. Valores por defecto que cambiaría
- `wait_for`:
  - que un timeout no sea `ok=true`
  - o bien devolver solo `satisfied`
  - y que `ok=false` signifique inequívocamente “no se cumplió”
- `world_spawn`:
  - resolver superficie automáticamente si el caller no pasa `y`
- `capture_screenshot`:
  - `frames=2` por defecto
  - devolver ruta si el inline no cabe
- Read-only:
  - visibles siempre, sin lease
- Mutaciones:
  - auto-lease interno
  - auto-liberación diferida si no hay más tareas

### 4. Gestión del lease invisible para el agente
- Introducir un modo `auto_session=true` por defecto en 8B/game:
  - el wrapper adquiere lease si hace falta
  - lo mantiene con heartbeat interno
  - lo suelta o marca a expirar al terminar la tarea
- El agente solo vería errores de coordinación cuando la causa sea externa de verdad
- `session_status` no debería ser la única forma de entender si hay lease
- `tools/list_changed` debería emitirse no solo tras acquire, sino también cuando cambie la visibilidad real del catálogo

### 5. Cómo mostrar reglas a un 8B
- **Descripción corta**:
  - una sola frase
  - verb + objeto
  - sin 5 cláusulas de seguridad
- **Un ejemplo mínimo por tool**
  - `args` concretos
  - sin variables meta
- **Parámetros con `description`**
  - cada uno
  - si es requerido, qué valor aceptar
- **Recursos de documentación**
  - recursos MCP o anexos por tarea:
    - `start_game`
    - `spawn_and_read`
    - `capture`
    - `wait_log`
- **Contrato de error igual para todas**
  - sin que `ToolError` sea la vía normal
  - sin que `next_step` sea a veces string, a veces dict, a veces texto embebido

### 6. Sin romper lo actual
- Mantener las 63 tools actuales
- Añadir wrappers/macros
- Añadir los nuevos campos aditivos
- Dejar compatibilidad para clientes potentes
- Hacer `local8b` un modo más restrictivo y guiado, no una poda arbitraria

## D. Propuestas
| Prioridad | Propuesta | Coste | Riesgo | Métrica de mejora |
|---|---|---:|---:|---|
| 1 | Sobre único de respuesta para éxito y error, con `ok/error/next_step.args` | L | Medio-alto por compatibilidad | Proporción de respuestas con remedio accionable y args completos |
| 2 | Auto-lease para 8B/game y read-only siempre visibles | L | Alto por seguridad y concurrencia | Fallos por `lease_required`, `lease_expired` o `run_not_owned` |
| 3 | Macros `dayz_start_ready`, `dayz_spawn_and_read`, `dayz_capture` | M | Medio por semántica oculta | Planes válidos de un 8B sobre 5 tareas fijas |
| 4 | Pack 8B con verbs de mundo, telemetría y captura | M | Bajo | Completitud de tareas típicas sin salir del pack |
| 5 | `wait_for` más simple: timeout = error, o `satisfied` como único gate | S | Bajo | Casos en que un 8B interpreta bien el timeout |
| 6 | `next_step` relativo a la tarea, no a `session_heartbeat` por defecto | S | Medio | Siguiente paso correcto tras mutaciones simples |
| 7 | Ejemplos concretos por tool + descripción corta y param descritos | M | Bajo | Primera llamada correcta y menor retry por args inválidos |
| 8 | Emisión correcta de `tools/list_changed` al cambiar visibilidad de catálogo | M | Medio | Tareas read-only completadas sin adquirir lease |

## E. Valoración
| Dimensión | Nota 0-10 | Motivos |
|---|---:|---|
| Contrato de errores | **2/10** | Hay demasiadas formas distintas de fallar: `ToolError` pelado, dicts `ok/error`, dicts `ready/reason`, strings con `next_step=...`. Para un 8B, eso obliga a adivinar remedio y acción siguiente. |
| Descripciones y esquemas | **2/10** | El material muestra descripciones extremadamente cargadas, ejemplos casi inexistentes y muchos parámetros sin descripción. Un modelo pequeño necesita pocas opciones, ejemplos y args claros. |
| Disclosure y packs | **3/10** | El catálogo pre-lease recorta descripciones a 80 caracteres y esconde verbs útiles. `local8b` corta demasiado: no sirve bien para spawn, telemetría ni captura. |
| Gestión del lease desde el agente | **2/10** | TTL de 120 s, renovación limitada, heartbeat manual, `session_status` no renueva y las reglas están embebidas en descripciones largas. Es un área de alta fragilidad para 8B-30B. |

## Hallazgos
F01 | P1 | tools/dayz_mcp/server.py:4742 | contrato | Muchos fallos de argumento se elevan como ToolError con un código pelado, sin causa ni remedio visibles en el mismo payload. | EVID: raise ToolError("bad_purpose") | FIX: Emitir errores tipados con cause, retryable y next_step.args
F02 | P1 | tools/dayz_mcp/server.py:594 | contrato | La readiness puede devolver un dict con ready/reason y sin ok, error o guía de siguiente paso en el mismo helper. | EVID: return {"ready": False, "reason": s_block} | FIX: Usar el mismo sobre de respuesta para readiness y verbos
F03 | P1 | tools/dayz_mcp/server.py:823 | contrato | En resultados ok se inyecta next_step como clave simple, mezclando datos y guía de la siguiente llamada. | EVID: payload["next_step"] = follow | FIX: Devolver next_step como objeto con tool y args
F04 | P2 | tools/dayz_mcp/agent_loop.py:71 | contrato | Las recetas de error añaden next_step como texto plano, no como objeto navegable por un agente pequeño. | EVID: token = f"next_step={name}" | FIX: Convertirlo a objeto y dejar el string solo como compatibilidad
F05 | P2 | tools/dayz_mcp/server.py:221 | UX | El mapa de reasons solo nombra una tool siguiente, sin argumentos ni explicación accionable para 8B. | EVID: "no_run": "dayz_test_run", | FIX: Ampliar a tool más args, remedy y retryable
F06 | P2 | tools/dayz_mcp/server.py:707 | UX | Cuando se emite next_step con args, estos son siempre un objeto vacío, insuficiente para copiar y ejecutar. | EVID: return {"tool": "bridge_status", "args": {}} | FIX: Completar args desde contexto o marcar qué campos son obligatorios
F07 | P2 | tools/dayz_mcp/server.py:251 | contrato | Se declara un conjunto de códigos remotos, pero no se ve una tabla uniforme de códigos a remedios y reintentabilidad. | EVID: _REMOTE_ERROR_CODES = frozenset({ | FIX: Añadir mapping code a cause, remedy, retryable y next_step
F08 | P1 | tools/dayz_mcp/agent_loop.py:60 | UX | Tras una mutación ok sin destino propio, la sugerencia por defecto empuja a heartbeat en lugar de a la tarea real. | EVID: mapped = "session_heartbeat" | FIX: Sugerir el siguiente paso de la tarea o una lectura de verificación
F09 | P1 | tools/dayz_mcp/server.py:4499 | lease | session_status no renueva el lease, pero es una tool tentadora para comprobar estado y puede provocar expiraciones silenciosas. | EVID: session_status does not renew the lease. With no renewing call | FIX: Renovar automáticamente o separar claramente estado de mantenimiento de lease
F10 | P2 | tools/dayz_mcp/server.py:4802 | disclosure | [INFERENCIA] La renovación visible del catálogo se dispara tras acquiring wait, pero no se ve una señal equivalente al perder, soltar o expirar el lease. | EVID: if ctx is not None and _runtime_holds_lease(client): | FIX: Emitir tools/list_changed también cuando cambie la visibilidad real
F11 | P2 | tools/dayz_mcp/server.py:653 | UX | El catálogo pre-lease corta descripciones a 80 caracteres, justo donde un 8B necesita reglas y ejemplos. | EVID: _INITIAL_DESCRIPTION_LIMIT = 80 | FIX: Para 8B: descripción corta pero completa, con ejemplo mínimo
F12 | P2 | tools/dayz_mcp/tool_pack.py:12 | UX | local8b es un conjunto cerrado que deja fuera los verbos necesarios para tareas reales de spawn, telemetría y captura. | EVID: LOCAL8B_TOOL_NAMES = frozenset( | FIX: Crear un 8b_game con read-only, spawn, telemetry y capture

## LO QUE NO PUDE VERIFICAR
- Cómo convierte exactamente FastMCP un `ToolError("...")` a la respuesta final que ve el cliente MCP.
- Si todos los handlers devuelven payloads consistentes con `ok/error` o si cada tool tiene su variante propia.
- El contrato completo de las 63 tools en runtime, porque solo veo extractos de código y un catálogo resumido.
- Qué devuelve realmente cada tool en error, no solo las excepciones visibles.
- Si `wait_for` siempre normaliza timeouts, errores de bridge y lease expirado con un sobre común.
- Si hay autogestión de lease invisible dentro de algunos handlers de ciclo de vida, más allá de la documentación visible.
- La relación exacta entre `local8b`, el catálogo pre-lease y el catálogo post-lease en un arranque real de cliente.
- Qué tools se ocultan exactamente cuando no se cumple la condición de lease, más allá de las visibles en el material.
- Si `capture_screenshot`, `surface_query`, `telemetry_read` u otras de solo lectura tienen requisitos de lease en código, más allá de las descripciones.
- Si la API de recursos del cliente MCP usado por ese 8B soporta doc resources o si todo debe ir por tools.

## ¿Qué puede estar mal en la premisa de este encargo?
- Puede que no todos los modelos 8B-30B sean igualmente buenos: uno de 30B con buen contexto y salida estructurada podría tolerar parte de esta complejidad, mientras que uno de 8B muy pequeño no.
- Puede que la seguridad compartida de DayZ justifique parte de la fricción; simplificar el lease para el agente no es equivalente a quitar la coordinación subyacente.
- Puede que el diseño asuma un agente de código o un runtime con memoria de sesión, no un 8B puro sin herramientas de recuperación.
- Puede que las 63 tools actuales estén optimizadas para potentes, y que la solución correcta sea un perfil 8B paralelo, no “enderezar” la API para todos.
- Puede que el cuello de botella no sea la cantidad de tools, sino la calidad de los wrappers y de los ejemplos; añadir más tools sin macros empeoraría al 8B.
- Puede que el catálogo pre-lease corto sea intencional para ahorrar tokens, aunque para 8B sea más útil un core pequeño, visible y accionable.

GATE NO CORRIDO: revisión por API sin herramientas