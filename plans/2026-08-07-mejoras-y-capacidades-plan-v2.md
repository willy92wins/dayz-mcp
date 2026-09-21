# Plan — DayZ-MCP: fixes verificados + capacidades nuevas (v2)

> **v2 = v1 + los 13 hallazgos del R22 aplicados.** El v1
> (`2026-08-07-mejoras-y-capacidades-plan-v1.md`) queda como histórico; **no implementar desde él**.
> Origen: council 2026-08-07 (lane Claude + 2 corridas Grok + calibración + R22 acotado).
> Arbitraje por hallazgo: `AI\20_Knowledge\council-scorecard.md` §Corridas 12-14.
> Encargo del usuario, verbatim: «*como podemos mejorar el MCP? sin sobreingeniarlo, hacerlo mas
> optimizado, menos errores, mas util, lo que se te ocurra*» + «*como herramientas nuevas, o
> "capacidades" no dijisteis nada*» + «*registralo todo y vamos a hacer un plan mas concreto*».

## Qué cambió del v1 (los 3 BLOCKER, verificados contra fichero)

| # | Lo que decía el v1 | Por qué era falso | Consecuencia |
|---|---|---|---|
| **B1** | F1.4 se acepta con «un `project` inexistente» | Ese input sale por `_fail("bad_project")` (`dayz_test_tool.py:71`) → `DayzTestToolError` → `server.py:1096-1097`. **Nunca entra en el `except Exception` que el fix toca** | El gate era **tautológico**: no podía ponerse en rojo por la causa que decía cubrir. Reescrito con mock |
| **B2** | F5.2 «capturar stderr y exit code» era esfuerzo M | El worker solo puede emitir `_TERMINAL_KEYS` exactas (`dayz_test_tool.py:15,265`) y `error_code ∈ WORKER_ERROR_CODES` (`:286`, `dayz_test_worker.py:27,52`). **No cabe stderr ni exit code sin migrar el contrato** | F5.2 sube a **L** y pasa a ser cambio de contrato, no de logging |
| **B3** | F5.2 se trataba como edit Python normal | `dayz_test_worker.py` está en `PACKAGED_MODULES` (`build_native_launcher.py:43,49`) con su SHA en el manifest (`:846`). **Editar el fuente no cambia lo que corre** hasta rebuild + rollout CAS | Precondición dura añadida. ★ La memoria `dayz-mcp-sealed-bundle-hides-source-edits` ya lo decía y no se consultó al escribir el v1 |

Y el MAJOR que más cambia el trabajo: **F3.2 estaba mal descrita**. El cast `CarScript.Cast` y el
error `fixture_not_vehicle` **ya existen** (`MCPBridge.c:922-928`); el v1 los presentaba como algo a
introducir. El diff real es quitar el allowlist de classname de **tres** sitios —guard del bridge
(`MCPBridge.c:866`), loopback (`loopback.py:179`) y los tests que lo clavan— y **exponer el verbo
como tool MCP, que hoy no lo está**.

## Principio rector

**Sin sobreingeniería** es restricción dura. Descartados en council y R22: hot-reload del daemon,
`scene_setup`/`smoke_object` como verbo, exponer `doctor.py` entero, y tocar
`session_coordination.py` más allá de un string.

## Estado de bloqueo — D-33 LEVANTADA (D-35)

Autorización **acotada a este plan**, no descongelación general. Al cerrar el bloque: devolver D-33
a su estado o sustituirla por decisión nueva. Registrar D-35 en el decision-log.

---

## Orden de ejecución (reordenado por el R22)

```
F1  →  F5.1 + F5.2  →  PBO-A  →  F2  →  F3 + PBO-B  →  F4 (spike)
✅       ✅      ⛔       ✅       ✅       ◀ AQUÍ        pendiente
```

**Progreso al 2026-08-07 (tarde).** F1 y F2 cerradas y verdes, cada una con su R21 de Grok
aplicado (el de F2 encontró 4 MAJOR reales). F5.1 **resuelto por entorno, sin causa
identificada** — `build:true` volvió a funcionar solo; el mecanismo a vigilar si vuelve quedó
instrumentado. F5.2 **cerrada por descope (D-36)**. PBO-A (`BE22A65190D59E3A`) desplegado y
gateado in-game en sus tres mitades, con restore point verificado por SHA-256.
**En curso: F3 + PBO-B.**

**Por qué F5 sube**: el propio HANDOFF dice que `build:true` roto es el mayor coste humano del
ciclo, y **PBO-A (restore + BUG-066(c)) ya está esperando** con workaround manual de AddonBuilder.
Dejar F5 para el final, como hacía el v1, retrasaba el desbloqueo de PBO-A **y** de PBO-B sin
ninguna dependencia técnica que lo justificara.

---

## Fase 1 — Cuatro fixes Python, sin PBO `[EXACT]`

### F1.1 — `query_all_players` es lectura pura y exige lease

`session_coordination.py:18-27` no lo incluye; `command_requires_lease` (`:36-37`) devuelve `True`
para todo lo ausente. **Cambio**: añadir `"query_all_players",` al frozenset.

**Aceptación** (corregida por H10 — el observable es el contrato MCP, no HTTP): sin lease, la tool
`query_all_players` **no devuelve `lease_required`** y completa. Con lease de otra sesión activa,
tampoco se bloquea.
**Control negativo**: `object_delete` y `world_spawn` siguen exigiendo lease. Sin este control el
test pasa aunque se rompa el gating entero.

### F1.2 — El timeout miente teniendo `_liveness_message` al lado

`server.py:798-800` lanza texto fijo; `_liveness_message` (`:802-814`) existe y no se usa.
**Cambio**: componer el mensaje con ella. Su `try/except` (`:803-806`) ya degrada al texto viejo.

**Aceptación**: timeout con daemon vivo y bridge sin pollear → el mensaje contiene `last poll` o
`has never polled` **y** `queue_depth`.
**Control negativo** (añadido por H1-gates): **inyectar fallo** forzando que `bridge_status_payload`
lance, y comprobar que el mensaje degrada al literal `peer status unavailable`. Sin inyección solo
se prueba el camino feliz.

### F1.3 — `timeout_s` sin cota superior (BUG-027)

`server.py:853-860` solo rechaza `<=0` y no finito. **Cambio**: `MAX_TIMEOUT_S = 300.0` a nivel de
módulo y rechazo por encima. 300 y no 120 porque `dayz_test_run` en `mode=all` midió 28,6 s.

**Aceptación**: `timeout_s=1e6` → `bad_timeout`. **Control negativo**: `timeout_s=299` aceptado.

### F1.4 — El `except Exception` traga la causa `[criterio reescrito por B1]`

`server.py:1096-1101` y `:1122-1127`. El mecanismo tipado ya existe una línea arriba; el `from None`
además borra el traceback. **Cambio**:
`except Exception as exc: raise ToolError(f"dayz_test_failed:{type(exc).__name__}") from exc`.
Se emite **el tipo, no el mensaje** (puede llevar rutas del host; el contrato es fail-closed).

**Aceptación** (la del v1 era tautológica): **mock** — `execute_dayz_test_run` lanza
`RuntimeError("boom")` → el `ToolError` es `dayz_test_failed:RuntimeError`.
**Control negativo**: un `DayzTestToolError` (p. ej. `project` inexistente → `bad_project`) sigue
devolviendo su `code` pelado, sin sufijo. Ese input **no** ejercita el camino arreglado — por eso
sirve de control y no de gate.

---

## Fase 5 — Plataforma (sube en el orden por H6)

### F5.1 — Diagnosticar `build:true` `[hipótesis abiertas, sin pre-conclusión]`

**Precondición dura**: F1.4 primero. Sin la causa propagada esto es a ciegas otra vez.

**Lo que acota el espacio** (medido, del HANDOFF): `preflight:true` pasa en 1,5 s; sin `build`
funciona; AddonBuilder a mano da exit 0 en ~3,4 s.

★ **Corrección del v1 (H5)**: el v1 concluía «el fallo está en la costura worker↔AddonBuilder».
**No está probado.** El síntoma observado es `ToolError("dayz_test_failed")`, es decir el
`except Exception`; si el worker emitiera un terminal `build_failed` limpio, `dayz_test_run`
devolvería `status=failed` como dict, no un `ToolError`. Luego el fallo está **fuera** del
`_failed("build_failed")` o **rompe el parse del terminal**. Hipótesis a mantener abiertas: broker
nativo · parse del terminal · launcher · costura con AddonBuilder. **No fijar ninguna antes de ver
el tipo de excepción que F1.4 hará visible.**

**Hard stop**: dos intentos de diagnóstico sin causa identificada ⇒ instrumentar, no seguir probando.

### F5.2 — Que el fallo de build llegue con detalle `[CERRADA POR DESCOPE — D-36, 2026-08-07]`

> ⛔ **NO IMPLEMENTAR.** Cerrada por descope, no por entrega. `build:true` dejó de fallar
> (2 builds el 2026-08-07, PBO escrito), así que la capacidad que esto añadía ya no diagnostica
> nada real. El detalle fino (`fine_code`, `event_kind`, `pid`, `image_path`) **sí quedó
> instrumentado** en `NativeLauncherBackendError`, pero vive en traceback y logs locales del
> host: **no cruza el cable MCP** y `server.py` sigue emitiendo sólo `dayz_test_failed:{Tipo}`.
> El contrato terminal (`_TERMINAL_KEYS` / `WORKER_ERROR_CODES`) **no se toca**.
>
> **Condición de reapertura**: `build:true` vuelve a fallar **y** el detalle local no da causa
> en dos intentos. Entonces se retoma la especificación de abajo tal cual — sigue siendo válida
> y por eso no se borra.

**Dos precondiciones que el v1 no vio:**

1. **Contrato terminal cerrado** (B2): el worker emite exactamente `_TERMINAL_KEYS`
   (`cleanup_degraded`, `error_code`, `exit_code`, `ok`, `run_id`) y el tool valida
   `set(value) != _TERMINAL_KEYS` (`dayz_test_tool.py:265`) y `error_code ∈ WORKER_ERROR_CODES`
   (`:286`). Meter stderr o el exit real de AddonBuilder **exige extender el contrato de forma
   explícita** —clave nueva validada en ambos extremos— **o** un canal lateral de artefacto con un
   `error_code` estable que apunte a él. Decidir cuál **antes** de tocar código, y escribir los
   tests del parser en ROJO primero.
2. **Módulo sellado** (B3): `dayz_test_worker.py` ∈ `PACKAGED_MODULES`
   (`build_native_launcher.py:43,49`), con `dayz_test_worker_sha256` en el manifest (`:846`).
   **Editar el fuente no cambia lo que ejecuta `dayz_test_run`.** Secuencia obligatoria:
   editar → rebuild `app.pyz` → rollout CAS → **verificar el SHA del worker empaquetado** contra el
   del fuente. Sin ese verify, el síntoma es «arreglado y sigue igual» y se diagnostica en círculo.

**Aceptación**: build roto a propósito (prefix inválido) → `build_failed` con el detalle por el
canal decidido, y **sin PBO escrito**. Build bueno → PBO escrito y **hash reportado == SHA-256 del
fichero en disco** (no mtime). **Control negativo**: el hash reportado de un build que no escribió
PBO debe ser ausente o error, nunca el del PBO anterior.

### F5.3 — Readiness de Mission (BUG-065) `[DESIGN]`

Reutilizar la propuesta auditada de 19 viability tests; no rediseñar. **Solo si tras F5.1/F5.2 el
ciclo sigue doliendo de forma medible.**

---

## Fase 2 — Tres mejoras Python

### F2.1 — `logs_since(marker)` `[contrato cerrado por H7]`

El v1 lo dejaba como idea. Cerrado ahora:

- **Fuentes**: RPT y `script_*.log` del **perfil del run activo**, resuelto igual que lo resuelve el
  worker (`dayz_test_worker.py:210-270`), no por convención inventada.
- **Marker**: `(path, offset, size)` serializado, **por sesión**, en el runtime state del cliente.
- **Lease**: es lectura pura de ficheros del host ⇒ **no exige lease**, y va al frozenset de F1.1.
- **Rotación**: si el fichero encogió o cambió de identidad → `rotated: true` y relectura desde el
  principio. Nunca perder líneas en silencio.

**Aceptación offline con fixtures** (el criterio del v1 metía un gate in-game en una fase vendida
como unit-testable): fixture con append real entre dos llamadas → la segunda devuelve solo lo nuevo
y el offset avanza. Fichero truncado → `rotated: true` sin pérdida.
**Control negativo**: dos llamadas **sin** append → `lines: []` **y offset idéntico** (sin el assert
de offset, un reader stub vacío pasa el test).

### F2.2 — Podar campos no rellenados `[matriz obligatoria por H9]`

★ **Riesgo para terceros**: el MCP se publicó a la comunidad de modding; esto es un cambio de
contrato observable. **No se implementa sin (a) matriz explícita comando→campos permitidos, (b) un
test por verbo público, (c) entrada de changelog.**

**Control negativo reforzado**: `query_all_players` con cero jugadores **sigue** devolviendo
`players: []` — éxito semántico verificado in-game el 2026-07-29; romperlo es regresión. Y añadir
los verbos que hoy consumen `raycast`/`telemetry` por presencia de clave (`in result`) o por
truthiness, no solo por valor.

### F2.3 — Delatar el `loopback.py` stale

`bridge_status` expone `loopback_mtime` y `daemon_started_at`; si `mtime > started_at`, warning
`daemon_module_stale`. **Sin hot-reload.**

**Aceptación end-to-end** (H-gates): arrancar daemon → `touch` del fichero → status **con** warning
→ reiniciar daemon → status **sin** warning. Los tres pasos, o el warning se puede fakear a
constante.

---

## Fase 3 — Capacidades nuevas (un PBO-B, un gate)

**Checklist de superficie obligatorio por verbo** (H12), porque F3.2 demuestra que se puede olvidar
una capa entera: `SERVER_COMMANDS`/`CLIENT_COMMANDS` · handler en el bridge · DTO de resultado ·
**`@app.tool` en `server.py`** · peer correcto (server vs client) · lease sí/no.

| ID | Verbo | API | Esf. |
|---|---|---|---|
| F3.1 | `surface_query(x,z)` → `{y, type, normal}` | `SurfaceY` `game.c:1162`, **`SurfaceGetType` `:1166`**, `SurfaceGetNormal` `:1173` | S |
| F3.2 | **generalizar `vehicle_prepare_fixture`** | ver abajo | S |
| F3.3 | `player_teleport(pos)` | `PluginDeveloper.Teleport` `plugindeveloper.c:20` | S |
| F3.4 | `object_anim(type,pos,source,phase?)` | `GetAnimationPhase`/`SetAnimationPhase` `entity.c:12-15` `[CITA]` | S |
| F3.5 | `inventory_give(classname,dest)` | `SpawnEntityInInventory` `plugindeveloper.c:566` | M |
| F3.6 | `object_inspect(type,pos,want[])` | `MemoryPointExists`/`GetMemoryPointPos` `object.c:458-460`, `GetBoundingCenter` `:103` | M |

### F3.1 — corregido por H8

El v1 devolvía `type` sin citar API: es **`SurfaceGetType`** (`game.c:1166`).
Y el negativo «fuera del mapa debe fallar» era **inespecificable**: `SurfaceY` devuelve `float` y no
hay semántica off-map en la API. **Definir el fallo por bounds del mundo o por no-finito**, y elegir
un punto que lo viole de forma estable.

### F3.2 — reformulada por H4

**No es un verbo nuevo.** Es: quitar el allowlist de classname del guard del bridge
(`MCPBridge.c:866`), del loopback (`loopback.py:179`) y de los tests que lo fijan; **exponer
`vehicle_prepare_fixture` como tool MCP** (hoy no tiene `@app.tool`); y **decidir explícitamente si
se renombra** o se mantiene el nombre. El `CarScript.Cast` y `fixture_not_vehicle` ya están
(`:922-928`): no se tocan.

**Aceptación desdoblada** (el criterio único del v1 mezclaba dos cosas): (a) el verbo corre y tipa
sobre cualquier `CarScript`; (b) sobre `CivilianSedan` vanilla alcanza `wheel_count >= 4`.
**Control negativo**: sobre un objeto no-vehículo → `fixture_not_vehicle`. Y sobre un coche cuyo mod
no implementa `OnDebugSpawn`, `wheel_count` sigue 0 **y el verbo lo reporta**: ese es el veredicto
útil, no un fallo.

### F3.6 — la estrella (sin cambios del R22: «bien aterrizada»)

Ataca la clase de fallo del ViewPilot 1100 del BRZ y las selecciones `bolt`/`trigger` del SR-2M.
**Contrato**: memory point ausente → `exists:false` con `ok:true`. **No es error de tool, es FAIL de
producto.**

### Gate único de PBO-B

`surface_query` en coords conocidas → `player_teleport` → `world_spawn CivilianSedan` →
`vehicle_prepare_fixture` (0 → ≥4 ruedas) → `object_inspect` (points reales + uno inventado) →
`object_anim` en una puerta → `inventory_give` de un arma vanilla.

**Negativos obligatorios** (el v1 solo tenía tres; H-gates señaló los que faltaban):
memory point inventado → `exists:false` · objeto no-vehículo → `fixture_not_vehicle` ·
`surface_query` fuera de bounds → error definido · **`player_teleport` sin player** ·
**`object_anim` con source inventada** · **`inventory_give` con destino inválido**.

**Hard stop**: si el gate falla dos veces, bisecar verbo a verbo. No un tercer rebuild.

---

## Fase 4 — Visual: **spike, no fase de entrega** `[degradada por el R22]`

`capture_orbit` + `capture_diff` bajan a **spike con presupuesto de medición**. Motivo, en palabras
del propio revisor que la propuso: sin calibración y sin build fiable da falsos rojos, y venderla
como capacidad entregable incumple la restricción de «sin sobreingeniería».

**Objetivo del spike**: obtener umbrales de `mean_abs_delta` y `changed_fraction` con
**PASS/FAIL numérico** contra suelo real (`world_time_set`/`world_weather_set` fijados), con un
falso-rojo controlado: baseline fijo + cambio forzado que **debe** dar rojo.

---

## Lo que este plan NO hace

| Descartado | Motivo |
|---|---|
| Hot-reload del daemon | Más frágil que exponer el mtime |
| `scene_setup`/`smoke_object` como verbo | Tool-dios. El `product-spec` prefiere verbos granulares + skill orquestadora **en su grupo G** (`product-spec.md:102-108,118`) — citar así, no como principio global (H11) |
| Exponer `doctor.py` entero | Lanza `claude`/`codex mcp get` (15 s c/u), relee el keyfile y hace `rglob` sobre todo el árbol (`doctor.py:194-197`, `:204`, `:801`) |
| Tocar `session_coordination.py` más allá de F1.1 | «Activo acotado + superficie sobredimensionada» |
| `capture_sequence` (contact sheet) | Spike de medición: jitter de frame time y estabilidad del deadman de freecam |

## Registro de arbitraje del R22

13 hallazgos: **3 BLOCKER aplicados** (B1/B2/B3, los tres verificados contra fichero por el
receptor), **6 MAJOR aplicados** (H4 reformula F3.2, H5 abre hipótesis, H6 reordena, H7 cierra el
contrato de `logs_since`, H8 corrige F3.1, H9 exige matriz), **4 MINOR aplicados** (H10 criterio en
contrato MCP, H11 cita acotada al grupo G, H12 checklist de superficie, H13 F4 a spike).
**Cero rechazados.** Falta el follow-up de calibración del revisor: pendiente.
