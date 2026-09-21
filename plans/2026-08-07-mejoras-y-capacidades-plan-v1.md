# Plan — DayZ-MCP: fixes verificados + capacidades nuevas (v1)

> Origen: brainstorm council 2026-08-07 (lane Claude + 2 corridas Grok + calibración).
> Registro y arbitraje por hallazgo: `AI\20_Knowledge\council-scorecard.md` §Corrida 12 y §Corrida 13.
> Encargo del usuario, verbatim: «*como podemos mejorar el MCP? sin sobreingeniarlo, hacerlo mas
> optimizado, menos errores, mas util, lo que se te ocurra*» + «*como herramientas nuevas, o
> "capacidades" no dijisteis nada, podeis hacer una ronda dirigida a eso?*».

## Principio rector

**Sin sobreingeniería** es restricción dura, no aspiración. Consecuencias asumidas en este plan:

1. **Nada que añada un subsistema.** Se rechazaron en el council: hot-reload del daemon (frágil),
   `scene_setup`/`smoke_object` como verbo (tool-dios; el `product-spec` prefiere verbos granulares
   + skill orquestadora), y exponer `doctor.py` entero como tool.
2. **No se toca `session_coordination.py`** salvo un string en un frozenset. Veredicto consensuado
   del council: «activo acotado + superficie sobredimensionada» — no es deuda a purgar ni monumento
   intocable, pero ampliarlo sí sería sobreingeniería.
3. **Un solo rebuild de PBO** para todo lo que toque Enforce (DZ-R5). Las fases Python no lo tocan.

## Estado de bloqueo — D-33 LEVANTADA para este bloque

**Decisión del usuario, 2026-08-07**: se levanta D-33 **para el alcance de este plan**. No es una
descongelación general de la plataforma: es una autorización acotada a las fases de abajo, y al
cerrarlas la congelación vuelve a su estado salvo decisión nueva. Registrar como **D-35** en el
decision-log.

Consecuencia directa: entra la **Fase 5**, que estaba fuera por `[PLATAFORMA]`, y que contiene lo
que el propio HANDOFF señala como **el mayor coste humano del ciclo de test de 3-10 minutos**
(`build:true` roto).

- **No compite con la próxima acción del HANDOFF.** Ese PACKONLY (restore + BUG-066(c)) sigue su
  camino como **PBO-A**. Los verbos nuevos van en **PBO-B**, después y por separado.

---

## Fase 1 — Cuatro fixes en Python, sin tocar el PBO `[EXACT]`

Riesgo bajo, sin rebuild, sin gate in-game. Todo verificado contra fichero el 2026-08-07.

### F1.1 — `query_all_players` es lectura pura y hoy exige lease

**Hoy** (`session_coordination.py:18-27`), el frozenset no lo incluye, y
`command_requires_lease` (`:36-37`) devuelve `True` para todo lo que falte:

```python
READ_ONLY_COMMANDS = frozenset(
    {
        "query_player_state",
        "scene_raycast",
        "telemetry_read",
        "query_get_in_condition",
        "camera_get",
        "vehicle_telemetry",
    }
)
```

**Cambio**: añadir `"query_all_players",` al frozenset. Nada más.

**Criterio de aceptación**: sin lease, `query_all_players` responde 200. Con lease de OTRA sesión
activa, tampoco se bloquea. `object_delete` y `world_spawn` siguen exigiendo lease (control negativo
— sin este control el test es tautológico).

### F1.2 — El timeout de client-mode miente teniendo el dato al lado

**Hoy** (`server.py:798-800`) lanza texto fijo, mientras `_liveness_message` (`:802-814`) —que
devuelve `last poll X.Xs ago; queue_depth=N; version_state=…`— existe y no se usa en ese camino:

```python
        raise ToolError(
            f"timeout waiting for {cmd} id={command_id}; {peer} peer status unavailable"
        )
```

**Cambio**: `raise ToolError(f"timeout waiting for {cmd} id={command_id}; " + await self._liveness_message(peer))`.
`_liveness_message` ya tiene su propio `try/except` (`:803-806`) que degrada al texto viejo, así que
el fallo del diagnóstico no puede romper el error original.

**Criterio de aceptación**: timeout con daemon vivo y bridge sin pollear → el mensaje contiene
`last poll` o `has never polled` y `queue_depth`. Con `/status` caído → degrada al texto viejo.

### F1.3 — `timeout_s` sin cota superior (cierra BUG-027)

**Hoy** (`server.py:853-860`) solo rechaza `<=0` y no finito. Un `timeout_s=1e9` con el `tool_lock`
global deja el servidor inutilizable:

```python
def _timeout(timeout_s: float) -> float:
    try:
        value = float(timeout_s)
    except (TypeError, ValueError) as exc:
        raise ToolError("bad_timeout") from exc
    if value <= 0.0 or not math.isfinite(value):
        raise ToolError("bad_timeout")
    return value
```

**Cambio**: añadir `if value > MAX_TIMEOUT_S: raise ToolError("bad_timeout")` con
`MAX_TIMEOUT_S = 300.0` a nivel de módulo. 300 s y no 120 porque `dayz_test_run` en `mode=all`
midió 28,6 s y el margen operativo debe caber.

**Criterio de aceptación**: `timeout_s=1e6` → `bad_timeout`. `timeout_s=299` → aceptado (control
negativo). Ledger BUG-027 pasa a fixed source, gate = la unit test.

### F1.4 — Los errores se tragan su causa

**Hoy** (`server.py:1096-1101` y `:1122-1127`) el mecanismo de códigos tipados **ya existe** una
línea más arriba; lo que pierde la causa es solo el `except Exception` genérico, y su `from None`
además borra el traceback:

```python
            except dayz_test_tool.DayzTestToolError as error:
                raise ToolError(error.code) from None
            except ToolError:
                raise
            except Exception:
                raise ToolError("dayz_test_failed") from None
```

**Cambio**: `except Exception as exc: raise ToolError(f"dayz_test_failed:{type(exc).__name__}") from exc`.
Se emite el **tipo** de excepción, no su mensaje: el mensaje puede llevar rutas del host y el
contrato de errores es fail-closed. El `from exc` restaura el encadenamiento para el log del daemon.

**Criterio de aceptación**: forzar un fallo no tipado (p. ej. `project` inexistente) → el `ToolError`
contiene el nombre de la clase de excepción. Un `DayzTestToolError` sigue devolviendo su `code`
pelado (control negativo: no se degrada el camino tipado).

---

## Fase 2 — Tres mejoras Python, sin tocar el PBO `[DESIGN]`

### F2.1 — `logs_since(marker)` — verbo nuevo, cero Enforce

Lee el RPT y el `script_*.log` del perfil activo y devuelve **solo las líneas nuevas** desde el
marcador anterior, con el marcador siguiente para encadenar. Filtro opcional por substring.

**Por qué**: hoy leer los logs a mano es la mitad del trabajo posterior a cada test, y la trampa de
que `Print()` va al `script_*.log` y no al RPT ya ha costado sesiones. **No toca el bridge, ni el
PBO, ni Enforce**: es un lector de ficheros con estado, del lado Python.

**Contrato propuesto**: `logs_since(marker: str = "", filter: str = "", max_lines: int = 400)` →
`{ok, marker, lines: [...], truncated: bool, sources: [...]}`. Marcador = `(path, offset, inode-ish)`
serializado; si el fichero rotó o encogió, se detecta y se devuelve `rotated: true` releyendo desde
el principio en vez de mentir con líneas perdidas.

**Criterio de aceptación**: dos llamadas seguidas sin actividad → la segunda devuelve `lines: []`.
Un `Print()` desde el mod aparece en la siguiente llamada. Rotar el log a mano → `rotated: true` y
**no** se pierden líneas silenciosamente.

### F2.2 — Podar campos no rellenados en la respuesta

Hoy `players:[]`, `raycast:{}` y `telemetry:{}` viajan en verbos que no los rellenan. Cuesta tokens
en cada llamada y es ambiguo: `[]` no distingue «cero jugadores» de «este verbo no rellena players».

**Cambio**: allowlist estática de campos por comando en el lado Python (`~30 líneas`), aplicada en
el retorno. **No se toca el wire juego↔loopback.**

**Criterio de aceptación**: `world_spawn` OK no trae `players`. `query_all_players` con cero
jugadores **sí** trae `players: []` — eso es éxito semántico verificado in-game el 2026-07-29 y
romperlo sería una regresión. Este control negativo es obligatorio.

### F2.3 — Delatar el `loopback.py` stale del daemon

`SERVER_COMMANDS`/`CLIENT_COMMANDS` se cargan al import; editar el disco no basta y el síntoma
(`not_whitelisted`) parece «el verbo no existe» cuando es «el daemon no lo ha leído» — medido:
1 h 39 min sirviendo el módulo viejo.

**Cambio**: `bridge_status` expone `loopback_mtime` y `daemon_started_at`; si `mtime > started_at`,
añade `warning: "daemon_module_stale"`. **Sin hot-reload** — rechazado por frágil en el council.

**Criterio de aceptación**: tocar `loopback.py` sin reiniciar → aparece el warning. Reiniciar el
daemon → desaparece.

---

## Fase 3 — Capacidades nuevas en el bridge (un solo PBO-B, un solo gate)

Todas con API de Enforce verificada contra el source vanilla el 2026-08-07. Orden de implementación
por coste creciente; **el gate in-game es único y cubre las seis**.

| ID | Verbo | API verificada | Esf. |
|---|---|---|---|
| F3.1 | `surface_query(x, z)` → `{y, type, normal}` | `SurfaceY` `scripts\3_game\global\game.c:1162`; `SurfaceGetNormal` `:1173` | S |
| F3.2 | `vehicle_condition(type, pos, radius)` | generalizar el guard de `MCPBridge.c:866` | S |
| F3.3 | `player_teleport(pos)` | `PluginDeveloper.Teleport(PlayerBase, vector)` `plugindeveloper.c:20` | S |
| F3.4 | `object_anim(type, pos, source, phase?)` | `GetAnimationPhase`/`SetAnimationPhase` `entity.c:12-15` `[CITA]` | S |
| F3.5 | `inventory_give(classname, dest)` | `SpawnEntityInInventory` `plugindeveloper.c:566` | M |
| F3.6 | `object_inspect(type, pos, want[])` | `MemoryPointExists`/`GetMemoryPointPos` `object.c:458-460`; `GetBoundingCenter` `:103` | M |

### F3.2 en detalle — el hardcode

`MCPBridge.c:866` exige literalmente que el classname sea el Mercedes:

```c
if (!command.args || command.args.mode != "object_at" || command.args.type != "MERCEDES_AMGLF" || ...)
```

**Cambio**: quitar la comparación contra el literal y validar que la entidad encontrada **es** un
`CarScript` (por cast), no que se llame de una forma concreta. Ese guard tipado es más fuerte que
el de string y además lo generaliza a BRZ, Forza y quads.

**Criterio de aceptación**: `world_spawn CivilianSedan` → `vehicle_condition` → `wheel_count >= 4`
y `fuel_fraction > 0`. Contra un objeto que NO es vehículo → `fixture_not_vehicle`, no un genérico.
Y contra un coche cuyo mod **no** implementa `OnDebugSpawn` → `wheel_count` sigue 0 y el verbo lo
reporta: **ese es el veredicto útil, no un fallo del verbo**.

### F3.6 en detalle — por qué es la estrella

`object_inspect` ataca una clase entera de fallos que ya han costado ciclos completos, y siempre
por la misma vía (foto ambigua → humano mira → rebuild):

- **ViewPilot 1100 del SUB_BRZ**: 7 caras contra 11.977 del `civiliansedan`. Se cazó escribiendo un
  script de censo a mano.
- **Selecciones `bolt`/`trigger` del A6_SR2M mal authored**: la `bolt` caía sobre el cañón.
  Descubierto tarde, tras un ciclo.
- **«¿existe `usti hlavne`? ¿están los axes de las puertas?»** en cada arma y cada coche importado.

**Regla de contrato**: un memory point ausente devuelve `exists:false` con `ok:true`. **No es un
error de la tool: es un FAIL del producto.** Es lo que hace que el verbo sepa ponerse en rojo, que
es justo lo que a estos gates les falta.

### Gate único in-game de PBO-B

Un solo arranque cubre los seis verbos. Escenario:
`surface_query` en coords conocidas → `player_teleport` allí → `world_spawn CivilianSedan` →
`vehicle_condition` (ruedas 0 → ≥4) → `object_inspect` (memory points conocidos + uno inventado →
`exists:false`) → `object_anim` en una puerta → `inventory_give` de un arma vanilla a las manos.

**Criterios negativos obligatorios** (sin ellos el gate no sabe ponerse en rojo): el memory point
inventado debe dar `exists:false`; `vehicle_condition` sobre un objeto no-vehículo debe dar
`fixture_not_vehicle`; `surface_query` en coordenadas fuera del mapa debe fallar, no devolver 0.

---

## Fase 4 — Visual, después del gate de PBO-B `[DESIGN]`

`capture_orbit` (N poses canónicas y N capturas en un round-trip, poses **reproducibles entre
builds**) y después `capture_diff` (baseline vs actual, con `mean_abs_delta` y `changed_fraction`
sobre umbral). Los dos son Python: el window-grab ya existe, esto es orquestar y componer.

Se dejan para el final por una razón concreta: **los umbrales de `capture_diff` hay que calibrarlos
contra suelo real** (iluminación de Chernarus, ruido de frame), y sin `world_time_set`/
`world_weather_set` fijados el diff dará falsos rojos. Hacerlo antes del gate de F3 es adelantar
trabajo que habrá que recalibrar.

**No entra en este plan**: `capture_sequence` (contact sheet). Grok la dejó en conjeturas con un
matiz que comparto: hay que medir el jitter de frame time y si el deadman de la freecam aguanta
durante T. Es un spike de medición antes que una capacidad.

---

---

## Fase 5 — Plataforma, habilitada por el levantamiento de D-33 `[DESIGN]`

Va **la última** a propósito: es la de mayor riesgo de regresión y la única que toca el lifecycle.
Pero es la de mayor ahorro de tiempo humano, así que no se pospone indefinidamente.

### F5.1 — Diagnosticar por qué `build:true` falla

Reproducido dos veces, siempre a ~16 s, sin escribir el PBO. **Lo que ya se sabe y acota el
espacio de búsqueda**: `preflight:true` pasa en 1,5 s; sin `build` funciona (`mode=server` 5,1 s,
`mode=all` 28,6 s); y AddonBuilder a mano da `Build Successful` con exit 0 en ~3,4 s. Es decir:
launcher, PE, bundle, request y lifecycle están sanos, y AddonBuilder también. **El fallo está en
la costura entre el worker y AddonBuilder**, no en ninguno de los dos.

**Precondición dura**: F1.4 primero. Sin la causa propagada, este diagnóstico vuelve a ser a ciegas
— es exactamente el error que hizo falta reproducir dos veces para nada.

### F5.2 — No tragar el `result` de AddonBuilder

`dayz_test_worker.py:556-563` `[CITA]`: capturar stderr, exit code y la existencia+hash del PBO
resultante, y emitir `build_failed` con ese detalle. El código `build_failed` **ya existe** en el
worker (`:29-39` `[CITA]`); lo que falta es que llegue con contenido.

**Criterio de aceptación**: un build roto a propósito (prefix inválido) devuelve `build_failed` con
el exit code real y sin PBO escrito. Un build bueno escribe el PBO y el hash reportado **coincide
con el del disco** — verificado por SHA-256, no por mtime.

### F5.3 — Readiness de Mission en cliente (BUG-065) `[DESIGN]`

`mode="all"` no espera a que el cliente compile Mission. Hay una propuesta auditada de 19 viability
tests reutilizable como piloto (referida en el ledger); **no se rediseña desde cero**.

**Nota de alcance**: si F5.1 y F5.2 se cierran y el ciclo baja de forma medible, F5.3 puede
posponerse sin coste. Es la única de las tres que no es claramente rentable a priori.

---

## Lo que este plan NO hace, y por qué

| Descartado | Motivo |
|---|---|
| Hot-reload del `loopback.py` | Más frágil que exponer el mtime. Council |
| `scene_setup` / `smoke_object` como verbo | Tool-dios; el `product-spec` prefiere granular + skill orquestadora |
| Exponer `doctor.py` entero como tool | Lanza `claude`/`codex mcp get` (15 s c/u), relee el keyfile y hace `rglob` sobre todo el árbol (`doctor.py:194-197`, `:204`, `:801`). El subconjunto barato es enriquecer `bridge_status`/`session_status` (F2.3 es el primer paso) |
| Tocar `session_coordination.py` más allá de F1.1 | «Activo acotado + superficie sobredimensionada»: ampliarlo es sobreingeniería, purgarlo no está justificado |
| ~~`build:true`, readiness, AddonBuilder~~ | **Ya NO descartado**: D-33 levantada para este bloque ⇒ Fase 5 |
| `capture_sequence` (contact sheet) | Spike de medición antes que capacidad: hay que medir jitter de frame time y estabilidad del deadman de freecam |

## Orden de ejecución y gates

1. **F1 completa** (4 fixes) + unit tests → sin gate in-game. Es el PR más barato y el de más riesgo/beneficio.
2. **F2** (3 mejoras) + unit tests → sin gate in-game. F2.2 exige el control negativo de `query_all_players`.
3. **PBO-A** sigue su curso propio (restore + BUG-066(c), ya en el HANDOFF). **No se mezcla.**
4. **F3** completa → **PBO-B** → **un solo gate in-game** con los criterios negativos de arriba.
5. **F5.1 + F5.2** (`build:true`). Va **después** de F1.4 por precondición dura, y conviene que sea
   antes de F4: si el ciclo de build se arregla, calibrar los umbrales de `capture_diff` deja de
   costar un rebuild manual por iteración.
6. **F4** (visual) cuando PBO-B esté verde y el build sea fiable.
7. **F5.3** solo si tras F5.1/F5.2 el ciclo sigue doliendo.

**Hard stop**: si el gate de PBO-B falla dos veces, parar y bisecar verbo a verbo en vez de un
tercer rebuild (DZ-R5 §«2 ciclos»). Mismo límite para F5.1: dos intentos de diagnóstico sin causa
identificada ⇒ instrumentar en vez de seguir probando.

**Al cerrar el bloque**: devolver D-33 a su estado o sustituirla por una decisión nueva. Una
congelación levantada «temporalmente» que nadie vuelve a cerrar deja de ser una decisión y pasa a
ser un descuido.
