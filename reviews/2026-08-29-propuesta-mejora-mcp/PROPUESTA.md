# Propuesta de mejora del MCP — 2026-08-29

**Alcance autorizado: PROPUESTA. Cero acción.** Nada de este documento se ha implementado.
Lo que sí está tocado en el árbol, y por otra vía, va listado al final en «Estado del árbol»
para que nadie lo confunda con esto.

Todo lo de aquí sale de la noche del 28→29 de agosto: tres councils ciegos (Claude, Codex,
Grok, GLM y qwen local), el incidente del PBO v10, y la revisión cruzada de T1–T5. Cada
afirmación lleva su `path:line` verificado en disco. Donde no lo lleva, lo digo.

---

## 0. El hallazgo que ordena todos los demás

**El MCP tiene tres capas y ninguna nombra a las otras en su propio texto:**

| Capa | Dónde vive | Qué decide |
|---|---|---|
| Tool Python | `tools/dayz_mcp/server.py` | firma, schema publicado, kwargs |
| Ingress HTTP | `tools/dayz_mcp/loopback.py` | qué claves se aceptan en `/enqueue` |
| Puente Enforce | `addon/scripts/5_Mission/MCPBridge.c` | qué hace el motor de verdad |

En una sola noche, **tres lanes de tres familias distintas firmaron un veredicto sobre la capa
equivocada**:

1. Esta sesión vio que `SurfaceY` no aparece en el Python y concluyó que `world_spawn` no
   coloca en superficie. Sí lo hace: `MCPBridge.c:2477` → `validation.flags = ECE_PLACE_ON_SURFACE`.
2. GLM vio `box_claimed` sólo en fixtures y concluyó que nadie lo emite. Lo emite
   `session_coordination.py:1900`, alcanzado desde `loopback.py:2699-2706`. Buscó en
   `daemon.py`, donde no está.
3. Otra sesión vio que el Python no snapea y firmó «la frase es falsa».

Ninguno fue descuido: los tres abrieron el fichero correcto **para la capa que suponían**. Eso
ya no es mala suerte, es una propiedad del sistema. **Es el problema más caro del MCP y no se
arregla con un cambio de API.** Las propuestas 1 y 6 atacan esto directamente; el resto se
apoya en ello.

---

## 1. Tratar `instructions=` como un contrato, no como documentación

**Prioridad máxima. Es el texto que lee TODO agente al conectarse y esta noche mintió.**

Estado medido: el bloque pasó de 876 a 301 chars en T1–T5, perdiendo los diez anclajes
operativos (`place_safely`, `surface_query`, `flags=3108`, `ECE_PLACE_ON_SURFACE`, `adminlog`,
`lookback_lines=200`, `componentIndex=-1`, `GetType()`…), y ganando una afirmación sin
condicionar sobre el placement.

La observación que reclasifica esto viene de la lane local (`qwen3.8:65k`) y es la mejor frase
de las tres:

> La instrucción borrada se dedicaba a **ENSEÑAR** a los llamadores a depender de la ventana de
> 200 líneas, así que la integración construida sobre ese contrato **se rompe sola**.

No es «se perdió documentación». **Era el contrato, y había integraciones encima.**

**Propuesta:**

- Declarar `instructions=` como superficie contractual: cambiarlo es un cambio de API, no una
  edición de prosa.
- Su test debe asertar **mecanismos**, no literales. Hoy el test de T1–T5 exige que el texto
  CONTENGA `y=0` y NO CONTENGA `surface_query.y` — es decir, **el gate exige que la guía
  correcta esté borrada**. Un gate calibrado al revés no protege: consagra.
- Coste de la superficie completa, medido: ~4.400 tokens para instructions + 54 descriptions +
  firmas, un 2 % de una ventana de 200K. **La premisa «el MCP pesa porque tiene 58 tools» es
  falsa.** No hay presupuesto que justifique tirar el manual: la reescritura ahorró ~144 tokens.

---

## 2. Cerrar los verbos schemaless

`world_spawn` y `telemetry_read` están en `_SCHEMALESS_COMMANDS` (`loopback.py:105`, `:109`) y
**aceptan cualquier clave extra sin rechistar**. Lo dice el propio comentario del fichero
(`:99`): *"Extra keys are not rejected here"*, frente a `:101`: *"Schemed verbs below already
fail closed on unknown keys"*.

Señalado por dos lanes independientes. Consecuencia hoy: fallo **asimétrico**. Cuatro verbos
revientan limpio en el ingress y estos dos cruzan hasta el puente para fallar allí con otro
código y otro mensaje. Depurar eso cuesta el doble.

**Propuesta:** darles schema cerrado. Vale la pena **aunque no se cambie nada más**, y es
condición previa para cualquier cambio de kwargs.

---

## 3. `type` → `classname`: viable, barato, y bloqueado por otra cosa

**La premisa original era falsa.** `type` y `classname` no son dos nombres de lo mismo:

```
MCPMessages.c:353   // type is Object.GetType(); classname is Object.ClassName().
MCPBridge.c:1411    entry.type      = found.GetType();
MCPBridge.c:1412    entry.classname = found.ClassName();
MCPMessages.c:45    string type;        <- ambos, mismo struct MCPArgs
MCPMessages.c:109   string classname;
```

**El nombre destino ya está ocupado y ya es ambiguo**: vale `ClassName()` en `entities_query` y
`GetType()` en `action_use` (`MCPClientBridge.c`, `result.classname = targetObj.GetType()`), y
el filtro de `action_use` acepta **las dos** (`if (found.GetType() == classFilter) … else if
(found.ClassName() == classFilter)`).

Censo real, por AST cruzado con regex: **6 tools con `type`** (`world_spawn:2976`,
`telemetry_read:3115`, `vehicle_prepare_fixture:3160`, `object_anim:3318`, `infected_drive:3348`,
`object_inspect:3429`) y **2 con `classname`** (`inventory_give:3398`, `action_use:4106`).
Circulaban un «9» y un «7». Los dos falsos.

**Paso 0, bloqueante — decidir la semántica antes que la sintaxis.** Si el agente sólo necesita
`GetType()`, lo que sobra es el campo `ClassName()`, y el arreglo barato es renombrar **ése** a
`script_class` en `MCPEntityHit` (`MCPMessages.c:357`) y `MCPTelemetry.class_name` (`:244`):
toca campos de respuesta que casi nadie consume, no kwargs de petición que consume todo el mundo.

**Paso 1, si aun así se quiere el rename — sólo capa 1, con un helper que YA EXISTE.**
`_patch_public_argument_alias` (`server.py:1526-1544`) ya está en producción en `scene_raycast`
(`:4298`). Renombra en el schema publicado, **remapea la lista `required`** (así que no degrada
la obligatoriedad a un error de runtime) y traduce antes de validar:

```python
_patch_public_argument_alias(app, "world_spawn", "type", "classname")
```

Consecuencias, verificadas: el wire sigue emitiendo `"type"`; `MCPArgs`, `MCPBridge.c:553` y el
**PBO quedan intactos**, nadie redespliega; `loopback.py` intacto; `EXPECTED_BRIDGE_VERSION` no
se toca; los 4 gates por HTTP crudo y `drive_ladder.py:186` siguen verdes.

**Prohibiciones:** nada de `sed` — hay `type` que no son classname en los mismos ficheros
(`MCPMessages.c:387` widget UI, `:549`, `MCPBridge.c:1110` tipo de superficie, `:2116`
intersección de raycast). Y si algún día cambia la clave de wire, subir `MCP_BRIDGE_VERSION` y
`EXPECTED_BRIDGE_VERSION` **en el mismo commit**.

---

## 4. Cambios de comportamiento que necesitan aviso de migración

Distinción útil que aportó la lane local: **una firma rota falla al primer intento; un default
cambiado no se detecta nunca solo.** El segundo es más peligroso aunque el primero sea más
visible.

| Cambio | Clase | Medido |
|---|---|---|
| `capture_screenshot` pierde `frames` | **firma rota** | y `delivery` entra como PRIMER parámetro, delante de `scale`: rompe llamadores posicionales en proceso |
| Tope inline de captura | **default** | `PREVIEW_MAX_TOKENS = 7000` (`mcp_capture.py:60`) frente a `default_max_tokens()` que aquí resuelve a **69.000**. Recorte de **9,9×** |
| `resolve_request_budget` | preexistente, agravado | recorta en silencio una petición por encima del tope, por diseño documentado (`:47-52`). Con el tope bajando 9,9×, muchos más llamadores caen ahí |
| `tool_profile="agent"` por defecto | **default** | oculta 7 tools: `session_acquire`, `session_wait`, `session_cancel`, `session_heartbeat`, `lease_acquire`, `pipeline_inbox`, `pipeline_resolve` |
| dedupe de mods | bug | `_effective_extra_mods` (`dayz_test_tool.py:95`) sólo mira `extra_mods`; `public_base`/`public_server` se calculan después (`:150-151`) y no se le pasan. El «injects once» no se cumple |
| `wait_for_box_s` 0 → 60 | default | deliberado |
| `mode` requerido → `"all"` | **inofensivo** | era `REQUERIDO` en HEAD: todo llamador ya lo pasaba. Dos lanes lo listaron mal como rotura |

Sobre `tool_profile`: engancha con que la lista de tools **se congela al arrancar el cliente**,
así que el afectado no ve «me faltan siete tools», ve «el MCP se ha roto».

---

## 5. Gates del pipeline que faltan

**5.1 — Compilación tras despliegue.** Un PBO desplegado, reciente y con la versión correcta
**no prueba que compile**. Caso de esta noche: PBO v10 desplegado, byte-scan limpio con control
negativo, y el módulo Mission entero caído por un consumidor huérfano. El gate útil cuesta un
minuto: arrancar el server **sólo con ese mod** y leer la línea del módulo del script log de
ESA arrancada.

**5.2 — Versión fuente vs desplegado**, que pide la ficha `fb-...-a462`. Con un matiz que la
propia noche añadió: **habría cazado el primer bloqueo y no el segundo**, porque el PBO roto
anunciaba v10 correctamente. Es necesario pero no suficiente; va junto al 5.1.

**5.3 — Pre-flight de Steam en el camino de `dayz_test_run`.** `HKCU\Software\Valve\Steam\ActiveProcess`
con `pid=0` mata al cliente en el bootstrap con un RPT de ~500 B y sin una línea de script; el
server no se entera porque no usa Steam. Ya está documentado en `dayz-test-ingame`
(`SKILL.md:400-401`) **con la firma exacta**, pero (a) sólo avisa, no aborta (`:101`), y (b)
vive en `templates/dayz-test.ps1`, que **no es el camino que usa `dayz_test_run`**.

**5.4 — Aviso de cuenta de Steam.** La caja cambió de cuenta entre las 03:51 y las 04:07
(`76561197995575711` → `76561198141021937`; `steamID64 − 76561197960265728 = ActiveUser`). Las
fixtures persistidas viven en la primera. Un ciclo que dependa de estado persistido falla sin
decir por qué y parece un fallo de persistencia del mod.

---

## 6. Contra la opacidad entre capas (el problema del §0)

Propuestas concretas, de menos a más ambiciosas:

- **Que cada capa nombre a la siguiente en su propio texto.** La descripción de un tool dice
  qué clave de wire emite; el schema del ingress dice qué miembro Enforce alimenta. Coste: prosa.
  Beneficio: un lector que abre una capa sabe que hay otras y cuáles.
- **Un mapa de capas** en `dayz-mcp-architecture.md` con la tabla del §0 y la regla explícita:
  *un veredicto sobre comportamiento necesita la capa donde ocurre, no la primera que lo menciona.*
- **Que el brief de cualquier lane futura sobre el MCP nombre las tres raíces.** Esta noche
  las tres lanes que se equivocaron tenían acceso a todo y ninguna sabía que había tres capas.

---

## Lo que NO propongo, y por qué

- **Unificar `inventory_give`/`action_use` hacia `type`**: menor en blast pero peor de nombre.
- **Alias sin fecha de caducidad**: es doble vocabulario permanente. Si se hace, con
  `deprecated` explícito, warning en log y fecha.
- **Renombrar campos de respuesta** (`MCPResult.type`/`.classname`, `MCPEntityHit`) mientras la
  semántica del §3 paso 0 no esté decidida: colapsaría dos campos en uno sin error
  (`MCPBridge.c:1411-1412`), con pérdida de datos silenciosa.
- **Resellar los 3 `SOURCE-HASH-MISMATCH`** del Knowledge Pack para desbloquear la promoción
  SP-349: son de skills que nadie ha revisado.

---

## Orden sugerido

1. **§1 instructions como contrato** + su test por mecanismo. Barato, sin roturas, y es la
   superficie que todo agente lee.
2. **§2 cerrar los schemaless.** Barato, independiente, y precondición del resto.
3. **§5.1 gate de compilación tras despliegue.** Un minuto por despliegue, evita el fallo más
   caro de la noche.
4. **§4 anunciar los cambios de comportamiento** de T1–T5 antes de que aterricen.
5. **§3 paso 0**, decidir `GetType()`/`ClassName()`. Sin esto, el rename no debe tocarse.
6. **§6 y §5.2-5.4** cuando haya calma.

---

## Estado del árbol (NO es parte de la propuesta)

Para que nadie confunda esto con lo ya tocado:

- **`P:\DayZ_MCP` y el PBO desplegado**: reparados esta noche. No fue mejora, fue arreglar un
  despliegue roto que bloqueaba a tres sesiones. Compilación verificada.
- **`Repos\dayz-mcp`**: sobre el diff sucio de T1–T5 hay **dos arreglos aplicados por encargo
  directo del usuario**, sin commitear: `lookback_lines` devuelto a 200 (regresión de BUG-086,
  con control in-game medido: 0,155 s contra timeout de 6,65 s) y el bloque `instructions`
  corregido. Suite: `Ran 1943 tests — OK (skipped=37)`. `HEAD` sigue en `9a58256`.
- Todo reversible con `git checkout`. Nada publicado.
