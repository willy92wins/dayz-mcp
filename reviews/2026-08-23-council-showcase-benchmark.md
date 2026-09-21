# Council — propuesta de showcase + benchmark (5 beats)

**Artefacto**: `plans/2026-08-23-showcase-benchmark.md`, anonimizado antes de repartir
(0 marcas de identidad en 102 líneas; se retiró el autor de dos cabeceras y una ruta
del host).

**Lanes**: seis, ciegas entre sí, cada una con un ángulo distinto y ninguna redundante.
Todas de solo lectura, todas con MCP desarmado — había un run de DayZ vivo
(`e8454507`, `RUNNING_IDLE`) y ningún revisor podía tocarlo. Comprobado antes y
después: la caja siguió con el mismo run y sin dueño.

**⚠ Conflicto de interés declarado**: la propuesta la escribió Grok, y una de las lanes
es Grok. Se anonimizó, pero misma familia sigue siendo misma familia. No se ha
suavizado nada: sus tres afirmaciones decisivas se verificaron una a una contra el
código, y las tres se sostienen. Aun así, cuenta como lane con sesgo conocido.

---

## Veredicto consolidado: **NO-GO todavía** — no por el guion, por el control

| Lane | Ángulo | Veredicto |
|---|---|---|
| Codex | validez del experimento | **NO-GO** |
| Grok ⚠ | tabla pass/fail | GO-CON-CAMBIOS |
| Gemini | métricas de fricción | GO-CON-CAMBIOS |
| Opencode | lectura hostil, sin código | GO-CON-CAMBIOS |
| Claude | nombres y afirmaciones verificables | GO-CON-CAMBIOS |
| Qwen local | consistencia interna | GO-CON-CAMBIOS |

La lane de Qwen no salió por su harness: prime-agent no conecta con su propio worker
(`connect ENOENT /tmp/prime-agent-1000/worker-*.sock`), y además había otro prime-agent
compitiendo por el único slot de Ollama (`fb-20260823-142713-98e2`). Se resolvió yendo
directo al endpoint HTTP, que para un ángulo sin herramientas es lo correcto de por sí.
659,6 s, 7.262 tokens de salida, coste 0.

**Qwen, cruzando el documento consigo mismo** (y sin ver el código), encontró tres
desajustes numéricos entre la receta congelada y lo que el ensayo ejecutó: el sitio
(receta `7512/7502` vs. ensayo `13000/8000`), el `hold_ttl_s` (8 en la receta, 10 en los
resultados) y el `vehicle_prepare_fixture` que el ensayo usó y la receta no lista. Los
dos primeros no los vio nadie más. El tercero lo vieron también Gemini y Claude por otra
vía. Su lectura: el documento presume de prompt congelado con SHAs y luego no siguió su
propia receta, sin una línea que lo justifique.

**El recuento no decide; decide la evidencia.** Cuatro lanes dicen "con cambios" y una
dice "no", pero los cambios que las cuatro piden incluyen rehacer el estado inicial y
volver a anotar el ensayo experto. Eso es la posición de Codex con otro nombre. El
documento **no está mal escrito**: está bien escrito sobre un experimento que todavía
no está controlado.

Y conviene decir lo que **no** está mal, porque es lo primero que un escéptico va a
atacar y aquí no aplica: **ninguna tool inventada**. Los veintiséis verbos citados
existen; el único ausente es `gear_shift`, y el documento ya lo declara inexistente
en dos sitios en vez de disimularlo.

---

## Lo que encontraron varias lanes por separado

### 1. La corrida "fría" no es fría — y el motivo real no es el que parecía

Cuatro de los datos que el benchmark dice ir a medir viajan en el bloque
`instructions` que **todo cliente MCP recibe al conectar**
(`tools/dayz_mcp/server.py:2355-2371`), literal: el flujo de lease
`session_acquire_wait (or lease_acquire)`, `playbook_run(name="place_safely")`,
`pos=[x, surface_query.y, z]; y=0 is ground`, `example type=CivilianSedan` y
`living infected flags=3108`.

Consecuencia directa sobre la lista de métricas: "verbo de lease usado" mide si el
modelo leyó la primera frase; "¿usó `place_safely`?" mide lo mismo; el criterio del
beat 2 nombra el mismo classname que el ejemplo; y el criterio del beat 4 pide el
número exacto que el bloque regala.

**Pero el reverso también es cierto y es peor**: `world_spawn` tiene una descripción
genérica —"Spawn a DayZ object through the existing world_spawn bridge command"
(`server.py:2788-2803`)— y **no hay ningún classname de infectado en el contexto frío**.
El único ejemplo (`ZmbM_CitizenASkinny_Blue`) vive en `tools/README-mcp.md:135`, que la
fría no ve. Un nombre inventado devuelve `spawn_failed`
(`addon/scripts/5_Mission/MCPBridge.c:553-558`) sin alternativa descubrible.

O sea: el beat 4 le regala los flags y le esconde el nombre. Es la peor combinación
posible — parece justo y no lo es.

### 2. El mundo se hereda, pero solo en parte (medido, no inferido)

La receta deja el run vivo para la fría. Se midió qué sobrevive 81 minutos después:

| qué | estado | cómo se midió |
|---|---|---|
| hora y clima del beat 1 | **se hereda** | captura: cielo azul, luz de día, 81 min después de que la experta fijara mediodía despejado |
| el sedán del beat 2 | **no sobrevive** | `entities_query` r=60 y r=80 centradas en el sitio y en el punto final del recorrido: ningún `CivilianSedan` |
| los infectados del beat 4 | **no sobreviven** | mismas consultas: ninguno |

Así que el beat 1 de la fría puede aprobar sin que sus llamadas hagan nada, y los
beats 2 y 4 no. `session_release` no restaura cámara ni borra entidades
(`tools/dayz_mcp/loopback.py:2203-2255`), así que lo que persiste, persiste.

### 3. Ningún beat tiene una condición de fallo limpia

El PASS del beat 4 es "≥3 `world_spawn` con flags 3108": tres **sedanes** con flags
3108 lo cumplirían. El FAIL típico que la propia tabla lista ("classname mal") no está
cubierto por su PASS. Y el ensayo concedió PASS con una captura donde se ven dos, no
tres.

El del beat 1 se apoya en `ok`, que es un eco Set/Get sobre el mismo objeto `World`
(`MCPBridge.c:1562-1583`): no prueba el cielo del cliente. El del beat 5 se apoya en
`sent=1`, que se pone sin ack del cliente (`MCPBridge.c:635-638`).

### 4. Cambian dos variables a la vez

Modelo **y** conocimiento. Con dos modelos distintos, n=1 por celda y sin fijar
versión, cliente ni configuración, una diferencia no distingue "las skills ayudan" de
"un modelo es mejor". Y no hay umbral pre-registrado que pudiera refutar la tesis: sin
él, cualquier resultado se narra como éxito.

---

## Hallazgos de una sola lane, verificados contra código

Todos comprobados por mí antes de darlos por buenos.

- **El beat 3 mide un campo que su verbo no rellena.** `pos_delta` solo se escribe
  dentro de los jobs `drive_probe` (`MCPBridge.c:3047, :3084, :3208`;
  `MCPClientBridge.c:2430, :2460, :2709`). `vehicle_telemetry` escribe `pos_real`,
  `speedo_max`, `gear`, `engine_on_server`, `is_owner`. Como los escalares no se podan,
  llega un `pos_delta: 0` que no distingue "no aplica" de "no se movió". Un juez
  literal falla el recorrido de 54,7 m que el propio ensayo reporta.
  → Archivado como bug de producto: `fb-20260823-141958-dde3`.
- **El "<2 m" del beat 2 no lo puede medir `object_inspect`**, que no devuelve
  `distance` y casa por tipo único en un radio de 25 m
  (`OBJECT_LOOKUP_RADIUS = 25.0`, `MCPBridge.c:20`). Un coche a 20 m pasa.
- **`capture_screenshot` topa en 512 px por defecto** (`scale="small"`,
  `server.py:3280-3286`). El ensayo usó `save_fullres`; la tabla no lo exige, así que
  la fría puede entregar capturas ilegibles y cumplir la letra.
- **Cuatro de los cinco beats se deciden mirando un PNG y nadie nombra al juez.**

---

## Refutado — lo que el council dijo y no se sostiene

Esto vale tanto como lo anterior: cinco afirmaciones cayeron al comprobarlas.

| afirmación | quién | por qué cae |
|---|---|---|
| "`lease_acquire` no existe" | Gemini | está registrada en `server.py:2439` |
| "el prompt frío no da `flags=3108`" | Gemini | sí lo da: `server.py:2363`. El problema real es el classname, no los flags |
| "la fría hereda el coche y los infectados" | Opencode | medido: no sobreviven. Solo se hereda hora y clima |
| "descripción, test y daemon discrepan en `wait_for`" | el documento | los tres coinciden: `server.py:3670`, `:1889`, y `test_weak_agent_consumer_ux.py:108-120`, que se llama `test_wait_for_timeout_ok_stays_true` |
| "los infectados no salieron por amontonamiento" | mía | medido: `count_total` a r=20 es **23**, por debajo del límite de 32. No hubo corte |

La última era mía y la escribí antes de medirla. El o/o del documento —"la IA se alejó
o `entities_query` no indexa `DayZInfected`"— **sigue abierto**, y es una pregunta de
diez minutos in-game: spawnear tres y consultar de inmediato. Si resulta que no los
indexa, ese hallazgo vale más que el vídeo.

---

## Lo mínimo para poder grabar

En orden de coste.

1. **Resolver el o/o del beat 4** (10 min in-game). Decide si hay bug de producto.
2. **Añadir un classname de infectado a la Tarea**, igual que ya se da `CivilianSedan`.
   Sin eso el beat 4 no es superable a ciegas.
3. **Reescribir los cinco PASS como medidas**, no como `ok`: `applied.hour` y
   `overcast_actual` para el 1; Δxz contra `pos_real` para el 2; dos lecturas de
   `pos_real` con `speedo_max>0` para el 3 (nunca `pos_delta`); conteo en PNG fullres
   para el 4; PNG fullres dentro de `show_time` para el 5.
4. **Run fresco para la fría**, o restaurar hora y clima explícitamente. Las entidades
   ya no hacen falta borrarlas: no sobreviven.
5. **Congelar el contexto frío entero**, no solo el bloque "Tarea": `app.instructions`,
   el `tools/list` con schemas, y la versión del cliente. Hoy se hashea la parte que no
   contiene las respuestas.
6. **Pre-registrar el umbral** que refutaría la tesis, y un grado INCONCLUSO para los
   fallos de entorno.
7. **Nombrar al anotador** y re-anotar el ensayo experto con la tabla nueva.

Decidir aparte, porque es una decisión y no un arreglo: **si `app.instructions` cuenta
como parte de la superficie evaluada**. Si cuenta, la tesis pasa a ser "un agente que
lee las instrucciones del servidor puede conducir el juego" —que es buena y
verificable— y se retiran las métricas ya pre-contestadas. Si no cuenta, hace falta una
tercera corrida con ese bloque recortado.

---

## Coste y método

| lane | coste | tiempo | notas |
|---|---|---|---|
| Codex | suscripción | ~19 min | la más estructural; único NO-GO |
| Grok ⚠ | $0,163 | 17 turnos | la más anclada en código, con `path:line` |
| Gemini | suscripción | ~4 min | 1 de 5 hallazgos falso |
| Opencode | **$0,00** | 272 s | sin ver una línea de código, encontró la herencia del mundo |
| Claude | — | — | verificación de nombres y arbitraje |
| Qwen local | **$0,00** | (pendiente) | sonda previa: PONG en 91 s |

Los briefs, los informes crudos y el artefacto anonimizado están en el workspace de la
sesión (`scratchpad/_council2308/`). Nada se escribió en el repo desde una lane.
